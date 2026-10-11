# El Decision Engine v2 (`decision/v2`)

`decision/v1` decide cada cuenta con dos datos de su cartera: los días de atraso y el saldo. v0.7
a v0.9 dejaron, para cada cuenta, una historia de cortes, pagos interpretados, lo que la cobranza
hizo con ella y qué pagos siguieron a una gestión. `decision/v2` decide también con eso, y responde
una pregunta precisa:

> Con lo que se sabía en `known_at` de lo que pasó hasta el final de `as_of`, ¿qué prioridad y qué
> canal recomienda la política para cada cuenta del corte, y por qué?

**Esta política demuestra la capacidad del motor; no representa una política real de un
acreedor.** `decision-policy/public-v1` es sintética, pública y de referencia: sus reglas y umbrales
se fijaron antes de ver cualquier resultado y no se ajustan contra ningún benchmark.

**No es una predicción.** `decision/v2` no estima probabilidades, no aprende de los datos y no dice
qué va a pasar: aplica reglas declaradas a variables observadas. Un nivel de prioridad es su lugar
en una escala de cuatro, no una probabilidad de pago. Y una variable que cuenta pagos asociados a
una gestión no dice que la gestión los produjo: **asociación operacional, no causalidad**
([atribucion.md](atribucion.md)).

```
CorteCanonico             el universo: las cuentas del corte, una por snapshot (v0.7)
      │
EjecucionDecisionV2       una decisión del corte: as_of, known_at, modo, versiones, hash de la
      │                   política, firma de entrada, disponibilidad y distribución
FeatureDecisionCuenta     las 43 features de cada cuenta, tal como se calcularon, con su feature_hash
      │
ResultadoDecisionV2       la decisión de cada cuenta: prioridad base y final, ajuste, canal, motivos
                          y decision_hash
```

El código vive en `src/motor_cartera/decision_v2/`: `features.py` (qué features hay y su forma
canónica) y `politica.py` (la política y su evaluador) son el núcleo puro; `ejecuciones.py`, el
cálculo en PostgreSQL; `consultas.py` y `sombra.py`, lo demás. Las decisiones de diseño están en
[decisiones.md](decisiones.md) (113 a 121).

## Las versiones

Una decisión guarda tres versiones y un hash, y cada una cambia por una razón distinta:

| Versión | Qué fija | Cambia si cambia |
|---|---|---|
| `decision/v2` | Cómo se calcula, se publica y se explica una ejecución, incluido el esquema de sus motivos. | El motor. |
| `features-decision/v1` | Las 43 features: su lista, su orden, su tipo, su ventana, su fuente, su filtro point-in-time y lo que significa su NULL. | Cualquier feature. |
| `decision-policy/public-v1` | Las reglas: la base, los modificadores, sus umbrales y efectos, la escala, el acotamiento y el canal. | Cualquier regla. |
| `hash_politica` | El SHA-256 de la definición completa de la política en JSON canónico (`politica.definicion`). Para `public-v1`: `2f9687400863f6af49a3c0fc51fe10c6c3b6f080804fcd4ef643ddad641d29e3`. | La definición, aunque nadie cambie la versión. |

Una ejecución que pide una combinación que este servicio no sabe aplicar no publica nada y termina
`FALLIDA` con `VERSION_NO_SOPORTADA`.

## Dos relojes: `as_of` y `known_at`

- **`as_of`** es el día de negocio que se decide: el del corte. Un día lógico empieza y termina a
  medianoche en la zona de la fuente (`MC_ZONA_HORARIA_FUENTE`, `America/Mexico_City` por omisión).
  No se elige aparte: un corte posterior a `as_of` sería información del futuro, y uno anterior
  mezclaría su foto con días que no observa (`422 AS_OF_DISTINTO_DEL_CORTE`).
- **`known_at`** es la frontera del conocimiento: un instante, con su zona. Solo cuenta lo que el
  sistema ya tenía registrado entonces. No puede ser futuro (`KNOWN_AT_FUTURO`), ni anterior a que
  el corte existiera (`CORTE_NO_CONOCIDO`).

Hay dos modos:

| Modo | `known_at` | Para qué |
|---|---|---|
| `OPERATIVO` | Opcional: si se omite, es el reloj de la base al abrir, una sola vez, y queda guardado para cada reintento. | Decidir el corte más reciente conocido. Sobre un corte que ya no lo es: `422 CORTE_NO_VIGENTE`. |
| `BACKTEST` | Obligatorio (`422 KNOWN_AT_REQUERIDO`): sin él se decidiría un día pasado con todo lo que se sabe hoy. | Decidir un corte pasado como se habría decidido con lo que se sabía en `known_at`. |

`known_at` puede ser posterior al final de `as_of`: la decisión admite entonces lo que se registró
después sobre lo que ya había pasado (una gestión registrada tarde) y lo advierte con
`CONOCIMIENTO_POSTERIOR_AL_DIA`. Con el `known_at` de aquel día, el resultado es exactamente el que
se habría obtenido aquel día.

### La frontera se asienta antes de leer

`registrado_en`, `creado_en` y `terminada_en` son el reloj de la transacción que registra. Una
transacción que empezó antes de `known_at` y todavía no confirma puede publicar algo con una marca
anterior a `known_at` que todavía no se ve. Por eso un worker, antes de leer, espera a que la
frontera esté **asentada**: que el reloj de la base ya haya pasado `known_at` y que ninguna sesión
de la base tenga abierta una transacción que empezó hasta `known_at`. Desde entonces nada nuevo
puede llegar con una marca anterior, y lo que se calcula es función exacta de (`as_of`,
`known_at`). Si no se asienta en 30 segundos, el intento termina sin tocar la ejecución y la cola lo
reintenta: nunca se decide con la frontera abierta. Una prueba lo demuestra con un registro pausado
a la mitad de su transacción.

## Los filtros point-in-time

Cada familia de features lee su fuente con un filtro sobre los dos relojes. *fin* es el final de
`as_of` en la zona de la fuente (la medianoche siguiente).

| Familia | Fuente | Filtro point-in-time |
|---|---|---|
| `SNAPSHOT` | `snapshot_cuenta` del corte objetivo | El corte es del día `as_of` y se materializó (`creado_en`) hasta `known_at`: lo exige `abrir`. |
| `HISTORIA` | `snapshot_cuenta` de los cortes de la cartera | Cortes con `fecha_corte` ≤ `as_of` y `creado_en` ≤ `known_at`. |
| `GESTION` | `gestion_cobranza` y su `evento_lifecycle` | Ocurrida antes de *fin*, registrada hasta `known_at`, y sin un `GESTION_ANULADA` registrado hasta `known_at`. |
| `PROMESA` | `promesa_pago`, evaluada con las reglas de `evaluacion-promesa/v1` | Acordada antes de *fin* y registrada hasta `known_at`; su cancelación cuenta si ocurrió antes de *fin* y se registró hasta `known_at`; su gestión anulada, si la anulación se registró hasta `known_at`. Los pagos que la evalúan son los de la familia `PAGOS`, y su horizonte, el último pago observado conocido, sin pasar de `as_of`. |
| `CONVENIO` | `convenio_cobranza` | Como una promesa: creado antes de *fin* y registrado hasta `known_at`; cancelado si el `CONVENIO_CANCELADO` ocurrió antes de *fin* y se registró hasta `known_at`; sin una anulación de su gestión registrada hasta `known_at`. |
| `PAGOS` | `movimiento_economico_canonico` de `motor-pagos/v1` | De cada mes, la interpretación vigente a `known_at`: su `EXITOSA` más reciente terminada hasta `known_at`. Movimientos recibidos antes del final de `as_of` (hora local de la fuente). Un pago está anulado si el reverso que lo anula se recibió antes del final de `as_of`: si llegó después, ese día era un pago. |
| `ATRIBUCION` | `atribucion_movimiento` y `candidato_atribucion` de `atribucion/v1` | De cada mes, la atribución vigente a `known_at`; solo `ASOCIACION_UNICA`, sobre un pago vigente de la familia `PAGOS` y con una gestión que no se sabía anulada a `known_at`. |

Una **anulación** corrige el registro: cuenta si se registró hasta `known_at`, ocurra cuando ocurra,
igual que en `evaluacion-promesa/v1`. Una **cancelación** es un hecho de negocio, con sus dos
tiempos. Los pagos observados de un archivo existen a `known_at` si su historia se materializó
(`ejecucion_historia` `EXITOSA`) hasta entonces.

Las ventanas son de días de calendario que terminan en `as_of`: `7d` va de `as_of` − 6 a `as_of`,
incluidos, desde la medianoche local; `30d` y `90d`, igual.

## NULL no es cero

Un conteo del lifecycle es **0** cuando no se conoce ningún evento: el lifecycle es el registro de
Motor Cartera, y si no hay una gestión registrada, no hubo una gestión que el sistema conozca.

Los pagos y la atribución son otra cosa: dependen de que los archivos de pagos hayan llegado y se
hayan interpretado. Una feature de `PAGOS` o de `ATRIBUCION` es **NULL** si su ventana no estaba
**disponible** a `known_at`, y **0** si estaba disponible y no hubo nada. La ejecución guarda en
`disponibilidad` el detalle, mes por mes de los últimos 90 días:

- un mes está **cubierto para pagos** si cae entre el primer y el último pago observado conocido a
  `known_at` y recibido hasta `as_of`, y la interpretación vigente de su mes tiene un resultado para
  **cada uno** de sus pagos observados conocidos (un mes sin pagos dentro de ese rango está
  cubierto: no hubo pagos). Se comprueba pago por pago, no comparando conteos: una interpretación
  cuenta su mes entero, y pagos posteriores a `as_of` que sí vio podrían tapar uno anterior que
  llegó después y no vio;
- un mes está **cubierto para atribución** si está cubierto para pagos y, si tuvo pagos, su
  atribución vigente leyó esa misma interpretación;
- una **ventana está disponible** si cada mes que toca está cubierto.

Una distancia o una tasa es NULL, además, cuando no hay de qué calcularla: sin gestiones, sin pagos,
sin denominador. Una regla sobre una feature NULL **no se cumple**: la información que no estaba
disponible no activa ninguna regla, ni a favor ni en contra. La API lo advierte con
`PAGOS_NO_DISPONIBLES` o `ATRIBUCION_NO_DISPONIBLES`.

## El diccionario de features

`features-decision/v1`, en su orden canónico: el de su hash, el de la API y el de esta tabla. El
tipo es el de su forma canónica (`IMPORTE` en pesos con dos decimales, `TASA` de 0 a 1 con cuatro,
`DIAS` con dos). La fuente y el filtro point-in-time de cada una son los de su familia, en la tabla
de [los filtros point-in-time](#los-filtros-point-in-time). "Nunca" quiere decir que la feature
siempre tiene un valor.

| # | Feature | Tipo | Familia | Ventana | Semántica | NULL significa |
|---|---|---|---|---|---|---|
| 1 | `saldo_total` | IMPORTE | SNAPSHOT | el corte | Saldo total de la cuenta en el corte. | nunca |
| 2 | `saldo_atrasado` | IMPORTE | SNAPSHOT | el corte | Saldo atrasado reportado en el corte. | la cartera no lo reportó |
| 3 | `saldo_requerido` | IMPORTE | SNAPSHOT | el corte | Saldo requerido reportado en el corte. | la cartera no lo reportó |
| 4 | `moratorios` | IMPORTE | SNAPSHOT | el corte | Moratorios reportados en el corte. | la cartera no los reportó |
| 5 | `dias_atraso` | ENTERO | SNAPSHOT | el corte | Días de atraso en el corte: la base de la prioridad. | nunca |
| 6 | `semanas_atraso` | ENTERO | SNAPSHOT | el corte | Semanas de atraso reportadas en el corte. | la cartera no las reportó |
| 7 | `producto` | TEXTO | SNAPSHOT | el corte | Producto de la cuenta en el corte. | nunca |
| 8 | `estrategia` | TEXTO | SNAPSHOT | el corte | Estrategia reportada en el corte. | la cartera no la reportó |
| 9 | `estatus_plan` | TEXTO | SNAPSHOT | el corte | Estatus del plan reportado en el corte. | la cartera no lo reportó |
| 10 | `cortes_observados` | ENTERO | HISTORIA | cortes hasta `as_of` | Cuántos cortes conocidos de la cartera, hasta el objetivo incluido, observan la cuenta. | nunca (al menos 1) |
| 11 | `reingresos_observados` | ENTERO | HISTORIA | cortes hasta `as_of` | Cuántas veces volvió a aparecer después de faltar en al menos un corte conocido. | nunca |
| 12 | `delta_saldo_total_desde_corte_anterior` | IMPORTE | HISTORIA | cortes hasta `as_of` | `saldo_total` del corte objetivo menos el de su observación anterior. | es su primera observación |
| 13 | `delta_dias_atraso_desde_corte_anterior` | ENTERO | HISTORIA | cortes hasta `as_of` | `dias_atraso` del corte objetivo menos los de su observación anterior. | es su primera observación |
| 14 | `continuo_desde_corte_anterior` | BOOLEANO | HISTORIA | cortes hasta `as_of` | Si su observación anterior es la del corte conocido inmediatamente anterior (false: reingresó). | es su primera observación |
| 15 | `dias_desde_ultima_gestion` | ENTERO | GESTION | hasta `as_of` | Días de `as_of` a la fecha local de su gestión más reciente, de cualquier canal. | no hay ninguna gestión conocida |
| 16 | `gestiones_7d` | ENTERO | GESTION | 7d | Gestiones de la cuenta. | nunca |
| 17 | `gestiones_30d` | ENTERO | GESTION | 30d | Gestiones de la cuenta. | nunca |
| 18 | `gestiones_sin_contacto_7d` | ENTERO | GESTION | 7d | Gestiones con `nivel_contacto` `SIN_CONTACTO`. | nunca |
| 19 | `contactos_titular_30d` | ENTERO | GESTION | 30d | Gestiones con `CONTACTO_TITULAR`. | nunca |
| 20 | `contactos_tercero_30d` | ENTERO | GESTION | 30d | Gestiones con `CONTACTO_TERCERO`. | nunca |
| 21 | `visitas_30d` | ENTERO | GESTION | 30d | Gestiones con el detalle de una visita de campo. | nunca |
| 22 | `gestiones_contactables_30d` | ENTERO | GESTION | 30d | Gestiones que podían llegar a alguien: todas menos `NO_APLICA` (un mensaje de una vía). | nunca |
| 23 | `tasa_contacto_titular_30d` | TASA | GESTION | 30d | `contactos_titular_30d` / `gestiones_contactables_30d`, redondeada a cuatro decimales. | no hubo gestiones contactables |
| 24 | `ultima_gestion_canal` | TEXTO | GESTION | hasta `as_of` | Canal de su gestión más reciente. | no hay ninguna gestión conocida |
| 25 | `ultima_gestion_nivel_contacto` | TEXTO | GESTION | hasta `as_of` | Nivel de contacto de su gestión más reciente. | no hay ninguna gestión conocida |
| 26 | `promesa_vigente` | BOOLEANO | PROMESA | a `as_of` | Si tiene una promesa que `evaluacion-promesa/v1` deja `PENDIENTE` a `as_of`. | nunca |
| 27 | `promesas_30d` | ENTERO | PROMESA | 30d | Promesas acordadas en la ventana, canceladas o no, sin las de una gestión anulada. | nunca |
| 28 | `promesas_90d` | ENTERO | PROMESA | 90d | Promesas acordadas en la ventana, canceladas o no, sin las de una gestión anulada. | nunca |
| 29 | `promesas_cumplidas_90d` | ENTERO | PROMESA | 90d | Promesas con fecha límite desde `as_of` − 89 (incluidas las que aún no vencen) evaluadas `CUMPLIDA`. | nunca |
| 30 | `promesas_parciales_90d` | ENTERO | PROMESA | 90d | Promesas vencidas en la ventana evaluadas `PARCIAL`. | nunca |
| 31 | `promesas_incumplidas_30d` | ENTERO | PROMESA | 30d | Promesas vencidas en la ventana evaluadas `INCUMPLIDA`. | nunca |
| 32 | `promesas_incumplidas_90d` | ENTERO | PROMESA | 90d | Promesas vencidas en la ventana evaluadas `INCUMPLIDA`. | nunca |
| 33 | `convenio_vigente` | BOOLEANO | CONVENIO | a `as_of` | Si tiene un convenio que empezó hasta `as_of`, no terminó antes, no se canceló y no es de una gestión anulada. | nunca |
| 34 | `convenios_90d` | ENTERO | CONVENIO | 90d | Convenios creados en la ventana, cancelados o no, sin los de una gestión anulada. | nunca |
| 35 | `dias_desde_ultimo_pago` | ENTERO | PAGOS | hasta `as_of` | Días de `as_of` a la recepción de su `PAGO` no anulado más reciente. | la ventana de 90 días no estaba disponible, o no hay pagos conocidos |
| 36 | `movimientos_pago_7d` | ENTERO | PAGOS | 7d | Movimientos `PAGO` no anulados de su `cliente_unico`. | la ventana no estaba disponible |
| 37 | `movimientos_pago_30d` | ENTERO | PAGOS | 30d | Movimientos `PAGO` no anulados de su `cliente_unico`. | la ventana no estaba disponible |
| 38 | `recuperacion_interpretada_7d` | IMPORTE | PAGOS | 7d | Recuperación neta interpretada: los `PAGO` no anulados más los `POSIBLE_REVERSO`, que restan. Un `REVERSO` y el pago que anula suman cero. | la ventana no estaba disponible |
| 39 | `recuperacion_interpretada_30d` | IMPORTE | PAGOS | 30d | Como la de 7 días. | la ventana no estaba disponible |
| 40 | `recuperacion_interpretada_90d` | IMPORTE | PAGOS | 90d | Como la de 7 días. | la ventana no estaba disponible |
| 41 | `pagos_asociados_unicos_30d` | ENTERO | ATRIBUCION | 30d | Pagos vigentes con `ASOCIACION_UNICA`. `AMBIGUA` no cuenta: no se elige entre candidatas. | la ventana no estaba disponible |
| 42 | `recuperacion_asociada_unica_30d` | IMPORTE | ATRIBUCION | 30d | Monto de esos pagos. | la ventana no estaba disponible |
| 43 | `dias_gestion_a_pago_mediana_90d` | DIAS | ATRIBUCION | 90d | Mediana de la antelación, en días, entre la gestión asociada y el pago. | la ventana no estaba disponible, o no hubo asociaciones únicas |

Los movimientos de una cuenta son los de su `cliente_unico` en los pagos, como en la Cuenta 360. Las
features se calculan una vez, al decidir, y se guardan tal cual: consultar una decisión nunca las
recalcula.

## La política `decision-policy/public-v1`

1. **La prioridad base** es la de `decision/v1` por días de atraso, importada de sus reglas, no
   reescrita:

   | Días de atraso | Segmento | Prioridad base | Nivel |
   |---|---|---|---|
   | 0 | `AL_CORRIENTE` | `BAJA` | 1 |
   | 1 a 30 | `MORA_TEMPRANA` | `MEDIA` | 2 |
   | 31 a 90 | `MORA_MEDIA` | `ALTA` | 3 |
   | 91 o más | `MORA_ALTA` | `MUY_ALTA` | 4 |

   El ajuste por saldo de v1 (`SALDO_ALTO`) no es parte de la base: no está en `public-v1`.
2. **Cuatro modificadores**, cada uno evaluado aparte:

   | Modificador | Se cumple si | Efecto |
   |---|---|---|
   | `RECUPERACION_RECIENTE` | `recuperacion_interpretada_7d` > 0 | −1 |
   | `PROMESA_VIGENTE` | `promesa_vigente` = true | −1 |
   | `SATURACION_SIN_CONTACTO` | `gestiones_sin_contacto_7d` ≥ 3 y `contactos_titular_30d` = 0 | +1 |
   | `PROMESA_INCUMPLIDA_RECIENTE` | `promesas_incumplidas_30d` ≥ 1 | +1 |

3. **Los efectos se suman** (`ajuste_total`), sin que uno oculte a otro: una cuenta con una
   recuperación reciente y una promesa incumplida queda con ajuste 0, y su explicación muestra las
   dos. El nivel final es el base más el ajuste, **acotado** de `BAJA` (1) a `MUY_ALTA` (4); el
   motivo `PRIORIDAD_FINAL` dice si se acotó.
4. **El canal** sale de la prioridad final con la correspondencia de `decision/v1`: `BAJA` y `MEDIA`
   por `DIGITAL`, `ALTA` por `TELEFONICA` y `MUY_ALTA` por `CAMPO`. No hay un modelo de canal.

La política es datos (`politica.PUBLIC_V1`): un evaluador puro en Python la aplica a una cuenta, y
el SQL que la aplica a todo un corte se genera de esa misma definición. Una prueba compara, cuenta
por cuenta, lo que publica el SQL con lo que concluye el evaluador: las mismas features, el mismo
`feature_hash`, los mismos motivos y el mismo `decision_hash`.

### Los motivos

Cada decisión guarda su explicación en `motivos`, de un catálogo cerrado: la base, cada modificador
**que aplicó** con sus condiciones (la feature, su valor, el operador y el umbral) y su efecto, la
prioridad final y el canal. Los que no aplicaron no se guardan: se ven en las features y en la
política, que también se guardan, y `GET /decisiones-v2/{id}/resultados/{cuenta_id}` los muestra
todos, aplicaran o no.

| Código | Qué explica | Datos |
|---|---|---|
| `PRIORIDAD_BASE_MORA` | La prioridad base por días de atraso. | la condición (`dias_atraso` en su tramo), `segmento`, `nivel`, `prioridad` |
| `RECUPERACION_RECIENTE` | Hubo recuperación interpretada en los últimos 7 días. | sus condiciones y su `efecto` |
| `PROMESA_VIGENTE` | Tiene una promesa pendiente. | sus condiciones y su `efecto` |
| `SATURACION_SIN_CONTACTO` | Tres o más intentos sin contacto en 7 días y ningún contacto con el titular en 30. | sus condiciones y su `efecto` |
| `PROMESA_INCUMPLIDA_RECIENTE` | Una promesa vencida en los últimos 30 días se evaluó incumplida. | sus condiciones y su `efecto` |
| `PRIORIDAD_FINAL` | La prioridad final. | `nivel_base`, `ajuste_total`, `nivel_sin_acotar`, `nivel`, `prioridad`, `acotada` |
| `CANAL_POR_PRIORIDAD` | El canal recomendado. | `prioridad`, `canal` |

Una cuenta con 45 días de atraso, tres gestiones sin contacto en la semana y ningún contacto con el
titular en el mes queda así:

```json
[
  {"codigo": "PRIORIDAD_BASE_MORA", "segmento": "MORA_MEDIA", "nivel": 3, "prioridad": "ALTA",
   "condiciones": [{"feature": "dias_atraso", "valor": "45", "operador": "EN_TRAMO", "umbral": "31-60"}]},
  {"codigo": "SATURACION_SIN_CONTACTO", "efecto": 1,
   "condiciones": [{"feature": "gestiones_sin_contacto_7d", "valor": "3", "operador": ">=", "umbral": "3"},
                   {"feature": "contactos_titular_30d", "valor": "0", "operador": "=", "umbral": "0"}]},
  {"codigo": "PRIORIDAD_FINAL", "nivel_base": 3, "ajuste_total": 1, "nivel_sin_acotar": 4, "nivel": 4,
   "prioridad": "MUY_ALTA", "acotada": false},
  {"codigo": "CANAL_POR_PRIORIDAD", "prioridad": "MUY_ALTA", "canal": "CAMPO"}
]
```

## Hashes y firma de entrada

- **`feature_hash`**: el SHA-256 de la forma canónica de las features de una cuenta: la versión y
  una línea `nombre=valor` por feature, en su orden. Los enteros en decimal, los números con los
  decimales de su columna, los booleanos `true` o `false`, el texto con su largo delante
  (`s5:CAMPO`) y NULL como `~`. PostgreSQL y Python escriben exactamente lo mismo.
- **`decision_hash`**: el SHA-256 de las versiones, el `feature_hash`, la política y su hash, la
  prioridad base, el efecto de cada modificador en su orden, el ajuste, la prioridad final y el
  canal.
- **`firma_inputs`** de la ejecución: el SHA-256 del motor, las versiones de las features y de la
  política, `hash_politica`, despacho, cartera, corte, `as_of`, `known_at` (en UTC, al
  microsegundo) y la zona. El modo no entra: un `OPERATIVO` y un `BACKTEST` con las mismas entradas
  deciden lo mismo. Las mismas entradas no se publican dos veces: `409 DECISION_V2_YA_GENERADA` con
  la anterior. Un índice único parcial lo garantiza (una `EXITOSA` por firma), y otro permite a lo
  más una `EN_PROCESO` por corte y versiones (`409 DECISION_V2_EN_PROCESO`).

`GET /decisiones-v2/{id}/resultados/{cuenta_id}` trae `verificacion`: si la política, aplicada otra
vez a las features guardadas, da exactamente el `feature_hash`, el `decision_hash` y los motivos
publicados. Lo que una decisión publicó **no se reescribe**: un trigger de PostgreSQL rechaza todo
`UPDATE` y `DELETE` sobre sus features y sus resultados.

## La ejecución: por conjuntos, todo o nada

Una ejecución no decide cuenta por cuenta: ninguna cuenta pasa por Python. Con su fila bloqueada y
en un `SAVEPOINT`, arma en tablas temporales, por fases:

1. **universo**: los snapshots del corte, ni una cuenta más ni una menos;
2. **historia**: los cortes conocidos y, por cuenta, sus observaciones, reingresos y deltas;
3. **lifecycle**: las gestiones y los convenios;
4. **pagos**: los movimientos de la interpretación vigente de cada mes;
5. **promesas**: las promesas, evaluadas con el mismo SQL de `evaluacion-promesa/v1`
   (`sql_conclusiones`), escrito una sola vez, con sus entradas point-in-time; y la disponibilidad
   de los pagos;
6. **atribución**: las asociaciones únicas de la atribución vigente de cada mes;
7. **features**: un `INSERT ... SELECT` las ensambla con su `feature_hash`;
8. **política**: otro aplica la política, con sus motivos y su `decision_hash`;
9. **conteos**: comprueba que cada cuenta del corte tenga exactamente unas features y una decisión,
   y cierra `EXITOSA` con su distribución en la misma transacción.

Si algo falla, se revierte todo y la ejecución queda `FALLIDA`: nunca una decisión a medias.

| Resultado | Cuándo |
|---|---|
| `DECISION_PUBLICADA` | `EXITOSA`: cada cuenta del corte tiene sus features y su decisión. |
| `YA_DECIDIDA` | Otra ejecución publicó la misma firma mientras esta se procesaba. |
| `VERSION_NO_SOPORTADA` | La ejecución pide un motor, unas features o una política que este servicio no aplica. |
| `CUENTAS_NO_DECIDIBLES` | Alguna cuenta del corte tiene días de atraso negativos: la política no la sabe decidir, y no se publica ninguna. |
| `DATOS_INCONSISTENTES` | Lo calculado no cuadra con el corte: sus snapshots no son las cuentas que dice tener, o no hay exactamente unas features y una decisión por cuenta. |
| `ERROR_INTERNO` | Un error inesperado; el detalle está en la bitácora del servicio. |
| `INTENTOS_AGOTADOS` | Su trabajo agotó sus reintentos sin publicar. |

La ejecuta un trabajo `DECISION_V2` de la cola durable, que la cola toma al final, después de todas
las demás etapas. Como en los demás motores, solo publica el dueño vigente de su trabajo: un worker
que pierde su lease a la mitad no publica. Las pruebas inyectan una falla antes de leer, después
de las features, después de las decisiones, antes de los conteos y antes de cerrar: en cada caso
la ejecución queda `FALLIDA` sin nada publicado, y pedir otra vez las mismas entradas publica la
decisión completa, con la misma firma.

**No se abre sola.** La piden `POST /decisiones-v2` o `motor-cartera decidir-v2`. Abrirla con cada
corte, cada pago o cada gestión sería decidir con una frontera que cambia a cada instante; pedirla
es declarar a qué `as_of` y con qué `known_at`.

## Reproducible y sin fuga temporal

Lo prueban un escenario dorado sintético, con una cuenta por cada uno de los 18 casos de la misión
(`tests/decision_v2_escenarios.py`), y sus pruebas:

- **reconstrucción**: decidir otra vez el mismo corte con un `known_at` en el que los datos elegibles
  son los mismos da, cuenta por cuenta, las mismas features, los mismos hashes y las mismas
  decisiones;
- **sin fuga**: un evento, un pago, una interpretación, una atribución o un corte posteriores a
  `known_at`, o un hecho posterior a `as_of`, no cambian ninguna feature. Una gestión registrada
  tarde cambia la decisión de otro `known_at` y deja intacta la del anterior: en el escenario, la
  segunda decisión difiere de la primera exactamente en las tres cuentas con eventos tardíos;
- **política**: cada modificador, solo y combinado, el acotamiento en los dos extremos, y que una
  feature NULL no active ninguna regla;
- **equivalencia**: el SQL y el evaluador puro coinciden en cada cuenta del escenario.

## En paralelo a `decision/v1`: el modo sombra

`decision/v2` no reemplaza a `decision/v1`: el flujo operacional (territorial y ruteo) sigue leyendo
`decision/v1`. `motor-cartera comparar-decisiones --corte AAAA-MM-DD` compara la decisión v2
`EXITOSA` más reciente del corte con la `decision/v1` de la corrida que lo publicó, por
`cliente_unico`:

- cuántas cuentas decidieron las dos, y cuántas solo una;
- prioridad igual, v2 mayor y v2 menor, con la matriz de v1 (filas) contra v2 (columnas);
- canal igual y canal distinto;
- cuántas subió v1 por saldo (`SALDO_ALTO`, que `public-v1` no tiene) y cuántas tienen la base de v2
  igual a la final de v1: en esas, cualquier diferencia viene de los modificadores;
- cuántas activaron cada modificador.

**Es un diagnóstico de diferencias, no un juicio de calidad.** Ninguna de las dos es "la correcta":
son dos políticas distintas, y saber cuál conviene exige medir resultados, que es de v0.14 en
adelante.

## La API

| Ruta | Qué |
|---|---|
| `POST /decisiones-v2` | `{corte_id, as_of?, known_at?, modo?, version_politica?}`: deja la decisión `EN_PROCESO` con su trabajo; `201` con `Location`. Los errores, arriba. |
| `GET /decisiones-v2/{decision_run_id}` | Una decisión: sus entradas, versiones, hash de la política, firma, estado, disponibilidad, distribución y advertencias. |
| `GET /decisiones-v2/{decision_run_id}/resultados` | Lo que decidió de cada cuenta, paginado en el orden de la cuenta; filtra por `prioridad_final` y por `canal_recomendado`, cada uno por su índice. |
| `GET /decisiones-v2/{decision_run_id}/resultados/{cuenta_id}` | La decisión de una cuenta explicada: sus features, cada regla con el valor que encontró y si aplicó, los motivos guardados, los hashes y `verificacion`. |
| `GET /cuentas/{cuenta_id}/decisiones-v2` | Cómo cambió la recomendación de una cuenta a través de los cortes y de las fronteras de conocimiento: solo `EXITOSA`, de la de `as_of` más reciente a la más antigua. |
| `GET /cartera/cortes/{corte_id}/decisiones-v2` | Las decisiones v2 de un corte, en cualquier estado. |

`GET /cuentas/{cuenta_id}` trae `ultima_decision_v2`: la decisión v2 `EXITOSA` de `as_of` más
reciente de la cuenta (con `?al=`, la de `as_of` hasta ese día), con su `decision_run_id`, su
`as_of`, su prioridad y su canal. Cada respuesta de una decisión trae sus advertencias:

| Advertencia | Cuándo |
|---|---|
| `BACKTEST` | Decide un corte pasado con el conocimiento que declara su `known_at`. |
| `CONOCIMIENTO_POSTERIOR_AL_DIA` | `known_at` es posterior al final de `as_of`: admite registros tardíos sobre lo que ya había pasado. |
| `PAGOS_NO_DISPONIBLES` | Alguna ventana de pagos no estaba disponible a `known_at`: sus features son NULL. |
| `ATRIBUCION_NO_DISPONIBLES` | Alguna ventana de atribución no estaba disponible a `known_at`: sus features son NULL. |

Por la terminal:

```bash
motor-cartera decidir-v2 --corte 2026-09-16               # OPERATIVO: known_at es el reloj de la base
motor-cartera decidir-v2 --corte 2026-09-16 --backtest --known-at 2026-09-17T08:00:00-06:00
motor-cartera comparar-decisiones --corte 2026-09-16
```

## Índices y volumen

Las tablas de alto volumen tienen llave primaria natural (ejecución, cuenta), sin un identificador
propio. Los índices son los de sus consultas: la llave primaria, que es la de la decisión de una
cuenta en una ejecución; `ix_resultado_decision_v2_prioridad` y `ix_resultado_decision_v2_canal`
(ejecución, valor, cuenta), una página de una prioridad o de un canal sin recorrer las demás; e
`ix_resultado_decision_v2_cuenta` (cuenta, ejecución), la historia de decisiones de una cuenta.
`feature_decision_cuenta` solo tiene su llave primaria. **No hay particionado** (decisión 121).

### Benchmark: 465,999 cuentas sobre el XL

En la misma base y la misma máquina que los benchmarks del motor de pagos y del lifecycle
([motor_pagos.md](motor_pagos.md), [lifecycle.md](lifecycle.md)): 12 cortes XL con 5,795,700
snapshots, 8,280,525 movimientos económicos, 2,573,729 eventos del lifecycle (410,626 promesas y
29,588 convenios) y 3,242,650 pagos atribuidos. `scripts/benchmark_decision_v2.py` decidió el corte
más reciente, el del 2026-03-25, en `OPERATIVO`:

| Fase | Lo que hace | Segundos |
|---|---|---:|
| universo | los 465,999 snapshots del corte | 3.4 |
| historia | los 5,795,700 snapshots de los 12 cortes conocidos | 20.1 |
| lifecycle | las gestiones y los convenios | 10.8 |
| pagos | los movimientos de la interpretación vigente de cada mes | 33.4 |
| promesas | las promesas, su evaluación y la disponibilidad de los pagos | 27.7 |
| atribución | las asociaciones únicas de la atribución vigente de cada mes | 33.4 |
| features | el ensamble de las 43 features, con su `feature_hash` | 24.9 |
| política | la decisión de cada cuenta, sus motivos y su `decision_hash` | 43.2 |
| conteos y cierre | la comprobación, la distribución y el `COMMIT` | 2.0 |
| **Total** | | **199.1** |

Son **2,341 cuentas por segundo, con 154 MiB de memoria pico** en el proceso: ninguna cuenta pasa
por Python, así que la memoria no crece con ellas. La decisión escribe 879 MiB de WAL. `decision/v1`
sobre la corrida del mismo corte tarda 34.6 s (13,451 cuentas por segundo, 93 MiB): v2 lee además la
historia, el lifecycle, los pagos y la atribución de cada cuenta, y guarda 43 features y una
explicación por cuenta.

A ese `known_at`, las ventanas de 7 y 30 días de los pagos y la de 30 de la atribución estaban
disponibles; las de 90, no: los pagos del escenario empiezan el 8 de enero, y la ventana de 90 días
toca diciembre. `recuperacion_interpretada_90d`, `dias_desde_ultimo_pago` y
`dias_gestion_a_pago_mediana_90d` quedaron en NULL en todas las cuentas, no en cero, y la decisión lo
advierte.

Activaron `RECUPERACION_RECIENTE` 152,527 cuentas, `PROMESA_INCUMPLIDA_RECIENTE` 35,568,
`PROMESA_VIGENTE` 12,529 y `SATURACION_SIN_CONTACTO` 107. El ajuste fue 0 en 295,726, −1 en
144,379, +1 en 22,262 y −2 en 3,632. Las prioridades finales: 161,053 `BAJA`, 219,835 `MEDIA`,
69,230 `ALTA` y 15,881 `MUY_ALTA`. Son las del escenario sintético con una política sintética: no
dicen nada de una cartera real, y la política no se ajustó con ellas.

**Reconstruible.** Otra decisión del mismo corte, con un `known_at` un microsegundo posterior (los
mismos datos elegibles), tardó 185.3 s y dio, cuenta por cuenta, las mismas 43 features, el mismo
`feature_hash` y la misma decisión en las 465,999: la huella de sus `decision_hash`
(`6d0277607f9a…918de931a`) es idéntica.

**Lo que ocupa.** Cada decisión de 465,999 cuentas ocupa 513 MiB: 375 MiB de resultados (843 bytes
por fila, de los que los motivos son unos 590) y 138 MiB de features (311 bytes por fila). Los
índices de los resultados ocupan 58 MiB (su llave primaria y los tres de sus consultas), y la llave
primaria de las features, 10 MiB. La decisión v1 de la misma corrida ocupa 167 MiB.

**En sombra contra `decision/v1`.** De las 465,999 cuentas, 399,661 tienen la misma prioridad en las
dos; v2 la sube en 18,521 y la baja en 47,817. El canal coincide en 405,081 y cambia en 60,918. v1
subió 47,403 por saldo, una regla que `public-v1` no tiene, y la prioridad base de v2 es la final de
v1 en 421,635: en esas, toda diferencia viene de los modificadores.

| v1 \ v2 | `BAJA` | `MEDIA` | `ALTA` | `MUY_ALTA` |
|---|---:|---:|---:|---:|
| **BAJA** | 154,945 | 181 | 0 | 0 |
| **MEDIA** | 5,239 | 186,969 | 14,469 | 0 |
| **ALTA** | 869 | 32,459 | 45,737 | 3,871 |
| **MUY_ALTA** | 0 | 226 | 9,024 | 12,010 |

Es un diagnóstico de diferencias, no un juicio de calidad.

Las consultas, por la API (mediana de 25 repeticiones, con dos decisiones del corte publicadas):

| Consulta | Mediana | p95 | La más lenta, dentro de PostgreSQL |
|---|---:|---:|---:|
| `GET /decisiones-v2/{id}/resultados/{cuenta_id}` | 12.3 ms | 13.8 ms | 0.08 ms |
| `GET /cuentas/{cuenta_id}/decisiones-v2` | 11.0 ms | 12.4 ms | 0.09 ms |
| `GET /cuentas/{cuenta_id}` (Cuenta 360, con su última decisión) | 44.8 ms | 47.8 ms | 0.15 ms |
| `GET /decisiones-v2/{id}/resultados?prioridad_final=ALTA` | 12.8 ms | 14.1 ms | 0.16 ms |
| `GET /decisiones-v2/{id}/resultados?canal_recomendado=CAMPO` | 13.2 ms | 14.4 ms | 0.75 ms |
| `GET /decisiones-v2/{id}/resultados` | 12.7 ms | 13.9 ms | 0.14 ms |
| `GET /decisiones-v2/{id}` | 8.7 ms | 9.3 ms | 0.08 ms |
| `GET /cartera/cortes/{corte_id}/decisiones-v2` | 10.7 ms | 11.6 ms | 0.08 ms |

Con `EXPLAIN (ANALYZE, BUFFERS)`, **ninguna tiene un Seq Scan sobre una tabla grande**. La decisión
de una cuenta entra por `ix_resultado_decision_v2_cuenta` y la llave primaria de sus features, su
historia por el mismo índice, y una página de una prioridad o de un canal por su índice, sin contar
las demás: su total sale de la distribución que la ejecución escribió al publicar. Una consulta de
una cuenta no recorre las 465,999 decisiones de su ejecución.

## Lo que `decision/v2` no hace, a propósito

- No predice: no estima probabilidades de pago, no tiene un modelo entrenado ni aprende de sus
  resultados (v0.15).
- No mide causalidad ni el aporte de una gestión, de un canal o de una promesa.
- No reemplaza a `decision/v1` en el flujo operacional: territorial y ruteo siguen leyéndola.
- No optimiza la asignación de cuentas a canales con capacidad (v0.16).
- No usa geografía real ni la distancia a una cuenta (v0.11).
- No se abre sola, ni decide una cuenta suelta: decide cortes completos.
- No representa la política de ningún acreedor real.
