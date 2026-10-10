# La atribución operativa (`atribucion/v1`)

v0.8 respondió qué movimientos económicos distintos hay en los pagos observados; v0.9 agrega lo que
la cobranza hizo con cada cuenta (el [lifecycle](lifecycle.md)). La atribución junta las dos cosas
para responder una pregunta acotada:

> ¿Qué gestiones con contacto de la misma cuenta ocurrieron antes de cada pago, dentro de una
> ventana, y cuántas fueron?

**Asociación operacional no es causalidad.** Que una gestión ocurriera antes de un pago no dice que
la gestión lo haya producido, ni cuánto aportó: el titular pudo pagar por cualquier otra razón. Por
eso `atribucion/v1` nunca dice "esta gestión produjo este pago". Dice *asociado*, *candidata* y
*observado después*, y con dos o más candidatas no elige ninguna.

```
MovimientoEconomicoCanonico   un PAGO de la interpretación vigente del motor de pagos (v0.8)
      │
EjecucionAtribucion           atribucion/v1 sobre una ventana: un despacho, una cartera y un mes
      │                       de recepción, con su ventana hacia atrás y su zona horaria
AtribucionMovimiento          qué concluyó de cada PAGO: SIN_GESTION_CANDIDATA, ASOCIACION_UNICA
      │                       o AMBIGUA, con sus motivos
CandidatoAtribucion           cada gestión candidata del pago, en una relación consultable
```

El código vive en `src/motor_cartera/atribucion/`: `reglas.py` es el núcleo puro y legible,
`ejecuciones.py` la atribución en PostgreSQL, `backfill.py` y `consultas.py` lo demás. Las decisiones
de diseño están en [decisiones.md](decisiones.md) (106 a 108 y 112).

## La política conservadora de `atribucion/v1`

Se atribuyen los movimientos `PAGO` de la **interpretación vigente** del motor de pagos de la ventana
(su `EXITOSA` más reciente). Un `REVERSO` o un `POSIBLE_REVERSO` corrige una recuperación: no es el
resultado de una gestión, y no se atribuye. La atribución consume lo que el motor de pagos concluyó;
no vuelve a interpretar pagos observados, así que un pago que la fuente reportó tres veces es un solo
movimiento y se atribuye una vez.

Una gestión es **candidata** de un pago solo si cumple todo esto:

1. es de la misma `CuentaCanonica` con que el motor de pagos concilió el pago;
2. ocurrió antes del pago o en su mismo instante (`ocurrido_en` ≤ la recepción del pago);
3. no está anulada;
4. ocurrió a lo más `ventana_dias` antes del pago;
5. hubo contacto: `CONTACTO_TITULAR` o `CONTACTO_TERCERO`. Un intento sin contacto o un mensaje de
   una vía (`SIN_CONTACTO`, `NO_APLICA`) no tiene evidencia de haber llegado a nadie.

La hora de un pago es la hora local de la fuente, sin zona (`pagos/v1`); la de una gestión es un
instante con zona. Se comparan en PostgreSQL, leyendo la recepción en la zona horaria de la fuente
(`MC_ZONA_HORARIA_FUENTE`, `America/Mexico_City` por omisión).

**La ventana es un parámetro de cada ejecución, no una verdad de negocio.** Por omisión son 30 días
(`MC_ATRIBUCION_VENTANA_DIAS`), una política del demo con datos sintéticos; `POST /atribuciones` y
`backfill-atribucion` aceptan otra. La ventana y la zona se guardan en la ejecución y entran en su
firma de entrada: la misma ventana de pagos con otra ventana hacia atrás es otra atribución.

## Las clasificaciones

Cada `PAGO` de la ventana recibe exactamente una:

| Clasificación | Cuándo | `gestion_id` |
|---|---|---|
| `SIN_GESTION_CANDIDATA` | Ninguna gestión cumple la política: no hubo, ninguna tuvo contacto, estaban anuladas, ocurrieron después o fuera de la ventana, o el pago no tiene cuenta. | vacío |
| `ASOCIACION_UNICA` | Exactamente una gestión candidata. | esa gestión |
| `AMBIGUA` | Dos o más gestiones candidatas, igual de elegibles. | vacío: no se elige ninguna |

Con varias candidatas **no se elige** la última, ni la primera, ni la más cercana al pago, solo para
subir el porcentaje atribuido. Todas quedan en `CandidatoAtribucion`, con su antelación en segundos,
para que se pueda ver por qué el pago quedó ambiguo. Una versión futura puede usar otro modelo; esta
no lo finge.

Un pago que un reverso anuló (`anulado_por_movimiento_id` en el motor de pagos) **se clasifica
igual**, porque las gestiones sí lo antecedieron, y queda marcado `anulado_por_reverso`: su monto no
cuenta como recuperación asociada. La ejecución suma aparte lo asociado, lo ambiguo, lo que no tuvo
candidata y lo anulado.

### Los motivos

Cada resultado explica su clasificación con un motivo, y con un segundo si un reverso lo anuló:

| Código | Cuándo | Datos |
|---|---|---|
| `MOVIMIENTO_SIN_CUENTA` | El motor de pagos no concilió el pago con una cuenta canónica: no hay gestiones que comparar. | |
| `SIN_GESTION_EN_LA_VENTANA` | Ninguna gestión candidata. | `ventana_dias`, `gestiones_sin_contacto`, `gestiones_anuladas` (las de su ventana que no fueron candidatas, y por qué) |
| `UNA_GESTION_CANDIDATA` | Una asociación única. | `ventana_dias` |
| `VARIAS_GESTIONES_CANDIDATAS` | Un pago ambiguo. | `candidatas`, `ventana_dias` |
| `ANULADO_POR_REVERSO` | Además, un reverso anuló el pago. | `reverso` (su `movimiento_id`) |

## Eventos tardíos y anulaciones

Una gestión se puede registrar días después de ocurrir, y una anulación también. **Ninguna cambia una
atribución ya publicada.** El caso de la misión:

1. un pago del día 10 se interpreta y `atribucion/v1` dice `SIN_GESTION_CANDIDATA`;
2. después se registra una gestión que ocurrió el día 8;
3. se ejecuta otra atribución de la ventana: el pago queda `ASOCIACION_UNICA` con esa gestión.

Las dos ejecuciones se conservan. La vigente de la ventana, la que leen la Cuenta 360 y
`/cuentas/{id}/atribuciones`, es la `EXITOSA` más reciente; `/movimientos/{id}/atribuciones` muestra
las dos, de la más reciente a la más antigua, cada una con su `vigente`. Con una anulación es igual:
la gestión anulada sigue visible en su auditoría (`GET /gestiones/{id}`, `ANULADA`, con su
anulación), y la siguiente atribución ya no la considera.

La atribución **no se abre sola**: depende de dos cosas que cambian por separado, los pagos que
interpreta el motor y las gestiones que registra la cobranza, y abrirla en cada gestión serían
millones de trabajos. La piden `POST /atribuciones` o `motor-cartera backfill-atribucion`, que
encuentra las ventanas desactualizadas.

## Firma de entrada, ejecución vigente e idempotencia

Cada ejecución guarda la **firma de entrada**: el SHA-256 de lo que leyó, en forma canónica. Son la
versión, la ventana, la ventana hacia atrás, la zona, la interpretación de pagos (su
`motor_pagos_run_id` y su propia firma de entrada, que resume sus pagos observados) y cada gestión
leída con su nivel de contacto y si está anulada. Las gestiones leídas son las de las cuentas de sus
pagos que pudieron anteceder a alguno: desde la ventana hacia atrás del primer pago de la cuenta
hasta su último pago. La misma ventana con las mismas entradas no se publica dos veces: la segunda
termina `FALLIDA` con `YA_ATRIBUIDA`, y la vigente sigue siendo la anterior. Un índice único parcial
lo garantiza en la base (una `EXITOSA` por ventana, versión y firma), y otro permite a lo más una
`EN_PROCESO` por ventana y versión.

## La ejecución: por conjuntos, todo o nada

Una ejecución no hace una consulta por pago: eso sería N+1 sobre millones de pagos. Con su fila
bloqueada y en un `SAVEPOINT`:

1. lee los `PAGO` de la interpretación vigente de su ventana a una tabla temporal, con su recepción
   como instante en la zona de la fuente (`at_movimiento`);
2. lee las gestiones de esas cuentas que pudieron anteceder a alguno, por el índice de las gestiones
   de una cuenta, con su anulación (`at_gestion`);
3. calcula su firma de entrada y, si otra ejecución ya publicó la misma, no publica;
4. arma las parejas (pago, gestión) de cada ventana con **una sola unión temporal**
   (`at_par`) y las resume por pago (`at_resumen`);
5. publica con `INSERT ... SELECT` una clasificación por pago y una fila por candidata;
6. cuenta, comprueba que cada pago leído tenga exactamente una clasificación y que las candidatas
   publicadas sean las que dicen las clasificaciones, y cierra `EXITOSA` en la misma transacción.

Si algo falla, se revierte todo y queda `FALLIDA` con lo que alcanzó a leer. Una prueba cuenta las
sentencias que manda la atribución con 2 y con 60 pagos: son las mismas. Otra compara, pago por pago
y candidata por candidata, lo que publica el SQL con lo que concluye el núcleo puro sobre un
escenario al azar.

La ejecuta un trabajo `ATRIBUCION` de la cola durable, que la cola toma después de las etapas
operacionales, de la historia y del motor de pagos. Como en los demás motores, solo publica el dueño
vigente de su trabajo: un worker que pierde su lease a la mitad no publica, y el que tomó el trabajo
la atribuye entera.

## Backfill

`motor-cartera backfill-atribucion [--dry-run] [--reintentar-fallidas] [--ventana-dias N]` revisa
cada ventana con una interpretación vigente de los pagos:

- **al día**: su atribución vigente leyó la interpretación vigente de los pagos y las mismas
  gestiones de sus cuentas en su rango, con las mismas anuladas. El lifecycle solo se agrega, así
  que contar basta;
- **en la cola**: tiene una `EN_PROCESO`;
- **sin atribución**: nunca se ha atribuido;
- **desactualizada**: hay otra interpretación de los pagos, o gestiones nuevas o anuladas en su rango;
- **solo con `FALLIDA`**: se reintenta con `--reintentar-fallidas`.

Encola un trabajo por ventana, nunca uno por pago, y es idempotente. Una ventana desactualizada se
vuelve a atribuir con la ventana hacia atrás de su vigente; una nueva, con `--ventana-dias` o la
configurada.

## Atribución y promesas: asociado no es compatible

La evaluación de una promesa ([lifecycle](lifecycle.md)) suma los movimientos **compatibles
temporalmente** con ella: `PAGO` de su cuenta recibidos entre que se acordó y su fecha límite, sin
pedir que la atribución los haya asociado a su gestión. Son dos conceptos, y no se mezclan:

- **asociado operacionalmente** (`atribucion/v1`): hubo exactamente una gestión candidata antes del
  pago;
- **compatible temporalmente** (`evaluacion-promesa/v1`): el pago cayó en el intervalo de la
  promesa.

Un pago puede ser compatible con una promesa y ambiguo en la atribución, o asociado a una gestión que
no tuvo promesa. Ninguno de los dos dice que la gestión o la promesa lo hayan producido.

## La API

| Ruta | Qué |
|---|---|
| `POST /atribuciones` | `{periodo: "AAAA-MM", ventana_dias?}`: deja la ejecución `EN_PROCESO` con su trabajo; `201` con `Location`. `409 ATRIBUCION_EN_PROCESO` si ya hay una, `409 SIN_INTERPRETACION_DE_PAGOS` si la ventana no tiene pagos interpretados. |
| `GET /atribuciones` | Las ejecuciones de la cartera, por ventana, con `vigente`. |
| `GET /atribuciones/{atribucion_run_id}` | Una ejecución: su ventana, su estado, sus conteos, sus montos, la interpretación de pagos que leyó y si sigue siendo la vigente. |
| `GET /atribuciones/{atribucion_run_id}/resultados` | Lo que concluyó de cada pago, con sus candidatas y sus motivos; filtra por `clasificacion` y por `cliente_unico`. |
| `GET /movimientos/{movimiento_id}/atribuciones` | Cada atribución de un pago: la vigente y las que la precedieron. |
| `GET /cuentas/{cuenta_id}/atribuciones` | Los pagos vigentes de una cuenta, cada uno con su atribución vigente. |

`GET /cuentas/{cuenta_id}` trae en `lifecycle_resumen.ultima_atribucion` la última atribución
disponible: la de la atribución vigente sobre el pago más reciente de la cuenta que tiene una. Cada
respuesta trae su `aviso`: asociación operacional, no causalidad.

## Linaje

Desde una atribución hasta el archivo original, por identificadores públicos:

```
AtribucionMovimiento ──► GestionCobranza (gestion_id y cada candidata)
        │                     └─► EventoLifecycle ──► CuentaCanonica
        ▼
MovimientoEconomicoCanonico (movimiento_id)
        └─► ResultadoPagoObservado ──► PagoObservado ──► DatasetConformado ──► ArtefactoFuente
```

`GET /atribuciones/{id}/resultados` da el `movimiento_id` y las gestiones; `GET /movimientos/{id}` y
`/observaciones`, el pago observado con su fila, su dataset y su archivo original;
`GET /gestiones/{id}`, el evento que la registró y su cuenta.

## Índices y volumen

Los índices de la atribución son los de sus consultas: la llave primaria de
`atribucion_movimiento` (`ejecucion_atribucion_id`, `movimiento_economico_canonico_id`), que es
también la de una ventana; `ix_atribucion_movimiento_movimiento_id`, las atribuciones de un pago en
cualquier interpretación; y la de `candidato_atribucion` (ejecución, movimiento, gestión), las
candidatas de un pago. La ejecución lee las gestiones por `ix_gestion_cuenta_ocurrido`. **No hay
particionado** (decisión 112).

### Benchmark: 3.24 millones de pagos sobre el XL

En la misma base y la misma máquina que el benchmark del lifecycle ([lifecycle.md](lifecycle.md)),
con los 2.57 millones de eventos cargados y una ventana de 30 días:

| Ventana | Pagos | Asociación única | Ambiguos | Sin candidata | Candidatas | Tiempo | Pagos por segundo |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2026-01 | 948,282 | 255,418 | 238,196 | 454,668 | 860,199 | 126.9 s | 7,472 |
| 2026-02 | 1,082,930 | 305,964 | 436,099 | 340,867 | 1,528,973 | 251.9 s | 4,299 |
| 2026-03 | 945,596 | 266,533 | 382,620 | 296,443 | 1,338,235 | 201.3 s | 4,698 |
| 2026-04 | 4,960 | 1,450 | 1,582 | 1,928 | 5,542 | 4.5 s | 1,106 |
| 2026-05 | 260,882 | 0 | 0 | 260,882 | 0 | 40.9 s | 6,378 |
| **Total** | **3,242,650** | **829,365** | **1,058,497** | **1,354,788** | **3,732,949** | **625.5 s** | **5,184** |

Cada ventana, con **152 MiB de memoria pico** y por conjuntos: el tiempo crece con los pagos y con
sus candidatas, no con consultas por pago. Mayo no tiene asociaciones porque el escenario no trae
gestiones de mayo: es la ventana de un experimento de llegadas tardías del benchmark de v0.8. Que un
tercio de los pagos quede ambiguo no es un defecto: el generador pone, a propósito, dos gestiones con
contacto antes de algunos pagos, y la política no elige. `atribucion_movimiento` ocupa 957 MiB y
`candidato_atribucion` 359 MiB.

Las consultas, por la API (mediana de 25 repeticiones):

| Consulta | Mediana | p95 | La más lenta, dentro de PostgreSQL |
|---|---:|---:|---:|
| `GET /cuentas/{id}/atribuciones` | 23.6 ms | 25.2 ms | 0.24 ms |
| `GET /movimientos/{id}/atribuciones` | 18.1 ms | 20.8 ms | 0.10 ms |
| `GET /atribuciones/{id}/resultados?cliente_unico=...` | 22.6 ms | 26.6 ms | 0.20 ms |
| `GET /atribuciones` | 12.3 ms | 14.0 ms | 0.09 ms |
| `GET /atribuciones/{id}/resultados?clasificacion=AMBIGUA` | 524.0 ms | 566.2 ms | 528 ms |

Las de un pago y las de una cuenta entran por sus índices. La última es global: la primera página de
los pagos ambiguos de una ventana de un millón ordena sus 436,099 resultados (un Seq Scan paralelo
y un ordenamiento de unos 80 MB en disco). Su total no se cuenta en cada página: sale de los
conteos que la ejecución escribió al publicar, y eso le quitó 0.4 s (antes tardaba 0.9 s). Su p95
depende del disco: en otra corrida, con otra actividad en la máquina, llegó a 2.1 s. Un índice por
(ejecución, clasificación, recepción) la evitaría, pero sería un cuarto índice sobre cada uno de los
millones de resultados que escribe cada atribución; no se agregó, y queda medida, como las
consultas globales del motor de pagos.

## Lo que `atribucion/v1` no hace, a propósito

- No mide causalidad, ni el aporte de una gestión, ni la efectividad de un canal o de un actor.
- No elige entre candidatas: no hay "último toque" ni "primer toque" escondido.
- No usa el `Gestor` de `pagos/v1`, que es lo que dice la fuente, ni un `GestorCanonico`, que no
  existe: el `actor_ref` de una gestión es una referencia opaca.
- No aplica pagos a cuotas de un convenio (no hay ledger) ni dice si una promesa se cumplió: eso lo
  dice la evaluación de promesas, con su propia versión.
- No construye features ni scores: deja la verdad operacional para que v0.10 los derive.
