# El modelo histórico (`historia/v1`)

v0.6.0 hizo que el sistema guardara correctamente **qué llegó**: cada archivo original, su dataset
conformado y su linaje. v0.7.0 agrega una dimensión nueva, **el tiempo**: qué le pasó a cada cuenta
a través de los cortes, con sus pagos observados y su evidencia completa, sin volver a interpretar
un solo archivo.

No pretende saber todavía **por qué** pasó (eso necesita las gestiones, de v0.9) ni **qué pago es
económicamente válido** (eso es del motor de pagos, de v0.8). Construye la base temporal sobre la
que esas respuestas se van a apoyar.

La API de la Cuenta 360 está en [cuenta_360.md](cuenta_360.md); las decisiones de diseño, de la 71
a la 84 de [decisiones.md](decisiones.md).

## Tres capas que no se confunden

```
SOURCE-CONFORMED     el Parquet de cada fuente oficial: las 93 columnas de CARTERA y las 23 de
        |            PAGOS, tipadas, con la fila y la hoja de origen de cada registro
        |
HISTÓRICO            la identidad de cada cuenta (CuentaCanonica), una fotografía por corte
        |            (CorteCanonico), las variables históricas de cada cuenta en cada corte
        |            (SnapshotCuenta) y cada movimiento de pagos tal como llegó (PagoObservado)
        |
OPERACIONAL          Cuenta: la foto de una corrida que leen decision/v1, territorial/v1 y ruteo/v1
```

El modelo histórico **no sustituye** al Parquet conformado: el Parquet sigue siendo la
representación completa y tipada de la fuente. PostgreSQL no guarda otra copia de las 93 columnas
por cada corte: con 500,000 cuentas y 52 cortes al año serían 26 millones de filas anchas al año.
Guarda lo que hace falta para responder rápido qué le pasó a una cuenta, y cada fila dice de qué
fila de qué Parquet salió.

Tampoco toca la capa operacional: los motores v1 no leen el modelo histórico ni lo esperan.

## `Cuenta` y `CuentaCanonica`

Son dos conceptos distintos, y los dos se quedan:

| | `Cuenta` | `CuentaCanonica` |
|---|---|---|
| Qué es | La foto operacional de una cuenta **en una corrida** | La identidad longitudinal de una cuenta **a través de sus cortes** |
| Cuántas hay | Una por cuenta y por corrida | Una por despacho, cartera y `CLIENTE_UNICO` |
| Quién la lee | decision/v1, territorial/v1, ruteo/v1 | La Cuenta 360 |
| Qué guarda | Las 8 columnas que leen los motores | Solo su identidad; sus valores en cada corte son sus snapshots |
| De dónde sale | La proyección `operacional/v1` | La materialización `historia/v1` |

## La identidad: despacho + cartera + `CLIENTE_UNICO`

Ninguna fuente dice que dos cuentas sean de la misma persona, ni trae un identificador de crédito.
Por eso v0.7.0 no inventa ninguno: no hay `persona_id`, `credito_id` ni `cliente_persona`. La
identidad canónica es

```
despacho_id + cartera_id + CLIENTE_UNICO
```

con `UNIQUE (despacho_id, cartera_id, cliente_unico)`. `CLIENTE_UNICO` sigue siendo lo que es: **el
identificador fuente de la cuenta**, no una persona. El mismo `CLIENTE_UNICO` en otra cartera es
otra cuenta canónica, aunque hoy el sistema opere una sola.

Cada cuenta, cada corte y cada pago observado tiene un identificador público (`cuenta_id`,
`corte_id`, `pago_observado_id`) que **no se sortea**: es un UUID versión 5 de su llave natural,
en un espacio de nombres propio del proyecto. Reconstruir la historia en cualquier orden y en
cualquier máquina da los mismos identificadores, así que una referencia guardada fuera del sistema
sigue valiendo después de un backfill. El id interno nunca sale por la API.

## `CorteCanonico`: una fotografía por cartera y fecha

Un corte canónico es la fotografía de la cartera en una fecha. Hay **uno solo** por
`(despacho_id, cartera_id, fecha_corte)`, y lo garantiza la base. Guarda su `firma_contenido` (la
de la cartera, no la del archivo), cuántas cuentas tiene, con qué versión del modelo se materializó
y **de qué dataset conformado salió**: esa es su evidencia.

### Dos fuentes para el mismo corte

v0.6.0 deja publicar la misma cartera en dos formatos: un `2026-10-01.xlsx` y un `2026-10-01.zip`
tienen SHA-256 distintos y la misma firma de contenido. Al materializarse:

- **Misma fecha y misma firma de contenido: es una fuente equivalente.** No se crea otro corte, ni
  otra cuenta, ni otro snapshot. La ejecución termina `EXITOSA` con resultado
  `FUENTE_EQUIVALENTE`, apuntando al corte que ya existía, sin leer el Parquet. Queda como su
  procedencia: `GET /cartera/cortes/{corte_id}` lista todas las fuentes de un corte, cada una con
  su archivo original.
- **Misma fecha y otra firma: es un conflicto.** Nadie elige un archivo, no hay "último gana" y la
  historia no se sobrescribe. La ejecución termina `FALLIDA` con resultado
  `CORTE_CANONICO_CONFLICTIVO`, y su detalle dice qué corte ya existe, de qué dataset salió y las
  dos firmas. El corte publicado queda intacto. Las correcciones explícitas (reemplazar un corte
  con otro, con su auditoría) no son de v0.7.

Lo mismo pasa si las dos fuentes se materializan a la vez: el `INSERT ... ON CONFLICT` del corte
hace que la segunda espere a que la primera confirme, y entonces se juzga contra ella.

## `SnapshotCuenta`: una cuenta en un corte

Una fila quiere decir: **esta cuenta canónica se observó en este corte canónico**, con sus valores
de ese día. La llave primaria es `(corte_canonico_id, cuenta_canonica_id)`: una cuenta, a lo más
una vez por corte. No tiene un id propio, porque nadie la referencia por uno y su llave natural es
igual de corta.

Guarda solo las variables con significado claro y uso transversal, cada una con el tipo que tiene
en el Parquet conformado:

| Columna | Tipo | Columna de cartera/v2 |
|---|---|---|
| `saldo`, `moratorios`, `saldo_total` | NUMERIC(14, 2) | `SALDO`, `MORATORIOS`, `SALDO_TOTAL` |
| `saldo_atrasado`, `saldo_requerido`, `pago_normal` | NUMERIC(14, 2) | `SALDO ATRASADO`, `SALDO REQUERIDO`, `PAGO_NORMAL` |
| `dias_atraso`, `atraso_maximo`, `semanas_atraso` | BIGINT | `DIAS_ATRASO`, `ATRASO_MAXIMO`, `SEMANAS_ATRASO` |
| `producto`, `canal` | VARCHAR(20) | `PRODUCTO`, `CANAL` |
| `estrategia` | VARCHAR | `ESTRATEGIA` |
| `fecha_ultimo_pago`, `imp_ultimo_pago` | DATE, NUMERIC(14, 2) | `FECHA_ULTIMO_PAGO`, `IMP_ULTIMO_PAGO` |
| `cve_entidad`, `cve_municipio` | VARCHAR(2), VARCHAR(3) | `ESTADO_CTE` y `POBLACION_CTE`, resueltos con el catálogo del INEGI |
| `estatus_plan`, `monto_plan`, `pagos_recibidos` | VARCHAR, NUMERIC(14, 2), BIGINT | `ESTATUS_PLAN`, `MONTO_PLAN`, `PAGOS_RECIBIDOS` |
| `estatus_promesa_pago`, `monto_promesa_pago` | VARCHAR, NUMERIC(14, 2) | `ESTATUS_PROMESA_PAGO`, `MONTO_PROMESA_PAGO` |
| `source_row`, `source_sheet` | INTEGER, VARCHAR(255) | `_source_row`, `_source_sheet` del Parquet |
| `fecha_corte` | DATE | la de su corte |

**No copia PII que la historia no necesita**: el nombre, el domicilio, los teléfonos, el aval y las
referencias siguen en el dataset conformado, y `source_row` lleva a la fila exacta. Las claves
geográficas se resuelven con el mismo catálogo y las mismas reglas que la proyección operacional:
son las mismas que tiene la `Cuenta` de esa corrida, sin leerla.

El snapshot repite la `fecha_corte` de su corte para que la historia de una cuenta se lea por el
índice `(cuenta_canonica_id, fecha_corte)` sin pasar por el corte. Que sea la misma no depende del
código: la llave foránea es compuesta, `(corte_canonico_id, fecha_corte)` hacia
`corte_canonico (id, fecha_corte)`, y la base rechaza un snapshot con otra fecha.

**Un snapshot no se actualiza nunca.** Un corte nuevo es otra fila; el anterior queda como estaba.

## `PagoObservado`: una fila de pagos/v1, tal como llegó

Cada fila aceptada de pagos/v1 es exactamente un pago observado, y **ninguno se deduplica**: dos
filas idénticas son dos observaciones, y el mismo movimiento en dos archivos también. Son
observaciones, no verdad económica: no hay `PagoConciliado`, `PagoAplicado`, reversos ni
atribución; eso lo va a construir el motor de pagos de v0.8 sobre estas filas.

Conserva los 23 campos, tipados como en el Parquet (`Año` → `anio` BIGINT, `Fecha_Recepción` →
`fecha_recepcion` TIMESTAMP sin zona, porque es la hora local de la fuente, `Recuperación_por_Gestión`
→ `recuperacion_por_gestion` NUMERIC(14, 2), `Porcentaje_Comision` → `porcentaje_comision` DOUBLE
PRECISION...), y además su dataset conformado, su ingesta, su fila, su hoja, su despacho, su cartera
y su `pago_observado_id`. Su llave primaria es `(dataset_conformado_id, source_row)`: una fila, una
observación. La llave histórica que deduplicaba (cliente, fecha de recepción y monto) no se usa como
restricción.

**No tiene llave foránea hacia `CuentaCanonica`.** Se relaciona con ella al consultar, por
despacho, cartera y `CLIENTE_UNICO`, con su índice. Así:

- **Un pago no crea una cuenta.** Un pago de un `CLIENTE_UNICO` que ningún corte trae se conserva
  igual, y queda clasificado como `SIN_CUENTA_OBSERVADA`: `GET /cuentas?cliente_unico=...`
  responde `404 SIN_CUENTA_OBSERVADA`, y `GET /pagos/{pagos_run_id}/historia` cuenta cuántos pagos
  de la ingesta tienen hoy una cuenta y cuántos no.
- **Un pago anterior a la primera cartera que trae a su cliente** se relaciona solo, en cuanto ese
  corte se materializa: no hay que volver a enlazar nada ni tocar el pago.
- **Un backfill de cortes no mueve ningún pago.**

## La materialización: `historia/v1`

Cada materialización es una **EjecucionHistoria**, con su `historia_run_id`, su dataset, su
`tipo_fuente` (`CARTERA` o `PAGOS`), su versión del modelo, su estado, su resultado, cuántos
registros leyó y cuántos publicó, y su detalle. Hay a lo más una `EXITOSA` y a lo más una
`EN_PROCESO` por dataset y versión del modelo, y lo garantizan dos índices únicos parciales.

| Resultado | Estado | Qué pasó |
|---|---|---|
| `CORTE_PUBLICADO` | EXITOSA | Publicó un corte nuevo con todos sus snapshots y sus cuentas nuevas |
| `FUENTE_EQUIVALENTE` | EXITOSA | El corte ya existía con la misma firma: no duplicó nada |
| `PAGOS_PUBLICADOS` | EXITOSA | Publicó un pago observado por cada fila del dataset |
| `CORTE_CANONICO_CONFLICTIVO` | FALLIDA | Ya hay un corte de esa fecha con otra firma; no se tocó |
| `DATOS_INCONSISTENTES` | FALLIDA | El Parquet no es lo que su dataset dice, o los conteos no cuadran |
| `ARTEFACTO_CORRUPTO` | FALLIDA | Los bytes del Parquet ya no son los de su SHA-256 |
| `VERSION_NO_SOPORTADA` | FALLIDA | La ejecución pide una versión del modelo que este servicio no sabe hacer |
| `YA_MATERIALIZADA` | FALLIDA | Otra ejecución del mismo dataset publicó primero |
| `ERROR_INTERNO` | FALLIDA | Un error inesperado; la traza está en la bitácora del worker |
| `INTENTOS_AGOTADOS` | FALLIDA | Su trabajo agotó los intentos de la cola sin terminar |

### Se abre con su dataset, y es paralela al flujo

Cuando una fuente oficial termina `EXITOSA`, en **la misma transacción** que publica su
`DatasetConformado` se registran su `EjecucionHistoria` `EN_PROCESO` y su
`TrabajoOrquestacion` de tipo `HISTORIA`. No hay un instante en que el dataset exista sin su
historia pendiente, ni tareas en memoria: si la API o el worker mueren después, otro worker la
termina.

```
                  ┌→ HISTORIA
INGESTA EXITOSA ──┤
                  └→ DECISION → TERRITORIAL → RUTEO

INGESTA_PAGOS EXITOSA → HISTORIA
```

`HISTORIA` es un tipo nuevo de la misma cola durable, con el mismo lease, latido, reintentos,
entrega al menos una vez y cierres condicionados a su dueño. No es una etapa del flujo: no tiene
`flujo_id`, ninguna etapa la espera y una historia que falla no detiene nada. Con varios trabajos
listos, la cola toma primero los operacionales (ingestas, decisiones, organizaciones territoriales
y ruteos) y al final los `HISTORIA`: con un solo worker, la decisión de la cartera de hoy no espera
detrás de un backfill de cientos de cortes.

### Solo lee el dataset conformado

La entrada es el Parquet del `DatasetConformado`, comprobado contra su SHA-256 y contra su
registro (su contrato, sus filas y sus columnas). **Nunca el xlsx, el csv ni el zip original**, que
queda como evidencia reproducible. Las pruebas lo verifican borrando el original del almacén antes
de materializar.

### Todo o nada, por lotes y con COPY

Un corte de cartera/v2, en una sola transacción, con la ejecución bloqueada:

1. si ya hay un corte de esa fecha, se juzga la equivalencia o el conflicto y se termina;
2. el Parquet se lee por lotes de `MC_FILAS_POR_LOTE` filas, solo con las columnas que hacen falta;
   cada lote se convierte a CSV con el escritor de Arrow (en C++) y se copia con `COPY` a una tabla
   temporal (`ON COMMIT DROP`), junto con el `cuenta_id` determinista y las claves del INEGI;
3. se publica el corte, con `INSERT ... ON CONFLICT DO NOTHING` sobre su fecha;
4. las cuentas canónicas nuevas se insertan en una sola operación, `INSERT ... SELECT` con
   `NOT EXISTS`, en orden de `CLIENTE_UNICO` y con `ON CONFLICT DO NOTHING`;
5. los snapshots se insertan en otra, con sus cuentas resueltas por `JOIN`, en orden de cuenta;
6. se cuenta: lo leído tiene que ser lo que el dataset dice, y lo publicado, lo leído;
7. la ejecución se cierra `EXITOSA` y se confirma todo junto.

Un dataset de pagos se copia por lotes directo a `pago_observado` con `COPY`, y se cuenta. En
memoria nunca hay más de un lote, y ningún registro pasa por un objeto ORM.

Si algo falla en cualquier punto (antes del `COPY`, a media copia, después de crear las cuentas,
después de insertar los snapshots o antes de cerrar), se revierte todo: no queda un corte, una
cuenta, un snapshot ni un pago a medias, ni la tabla temporal. La ejecución queda `FALLIDA`, con su
resultado y cuántos registros alcanzó a leer, y su trabajo `COMPLETADO`: es auditable, y el backfill
la puede reintentar. Si lo que falta es el Parquet en el almacén (un volumen sin montar), no es un
fallo de la historia: la ejecución sigue `EN_PROCESO` y el trabajo se reintenta con su espera.

### Concurrencia

- **El mismo dataset en dos workers.** La ejecución se toma con su fila bloqueada: el segundo
  espera, la encuentra terminada y no hace nada. Una segunda ejecución que se saltara las
  revisiones choca al cerrar con el índice de las `EXITOSA` y queda `FALLIDA` (`YA_MATERIALIZADA`).
- **Dos cortes distintos con cuentas nuevas en común.** Los dos insertan sus cuentas nuevas en
  orden de `CLIENTE_UNICO`: el segundo espera, en la primera cuenta común, a que el primero
  confirme, y nunca se bloquean entre sí. Ninguna cuenta se duplica.
- **Dos fuentes del mismo corte a la vez**: una publica y la otra es equivalente, o conflictiva.
- **Un worker que pierde el lease después de calcular** todavía tiene la ejecución bloqueada:
  publica una sola vez, el worker que tomó su trabajo la encuentra terminada, y el trabajo lo
  cierra su dueño vigente. Es la semántica de todos los motores desde v0.5.0: el lease decide
  quién debe intentar un trabajo; el bloqueo de la ejecución, quién puede cambiarla.

## El tiempo: orden de llegada no es orden de la historia

La historia se ordena por **fecha de corte**, no por el orden en que llegaron los archivos. No se
guarda ningún delta, evento ni estado que dependa del orden de llegada: los eventos, la
continuidad y los deltas se calculan al consultar, a partir de los snapshots y de los cortes de la
cartera. Por eso procesar los cortes 1, 3 y 4 y después el 2 deja exactamente la misma historia
que procesarlos en orden, y reconstruir toda la capa histórica en un orden al azar da las mismas
cuentas, los mismos cortes, los mismos snapshots y los mismos pagos, con los mismos
identificadores públicos. Las dos cosas son pruebas del proyecto.

## Presencia, continuidad y deltas

El vocabulario dice lo que se **observó**, no lo que pasó:

- **`PRIMERA_OBSERVACION`**: el primer corte canónico en que aparece la cuenta. No es su fecha de
  originación ni un alta: la cartera pudo tenerla desde antes del primer corte que se observó.
- **`SALIDA_OBSERVADA`**: la cuenta aparece en un corte y no en el siguiente corte de su cartera.
  No quiere decir liquidación, cancelación, castigo ni venta: solo que dejó de observarse.
- **`REINGRESO_OBSERVADO`**: después de faltar al menos un corte, vuelve a aparecer.

Dos snapshots de una cuenta son **continuos** solo si sus cortes son consecutivos en la cartera:

```
Cortes de la cartera:   01   08   15   22
La cuenta aparece en:   01   08        22

01 → 08   continuo
15        salida observada
22        reingreso observado; 08 → 22 no es un cambio semanal continuo
```

En la historia, cada snapshot trae `delta_saldo_total` y `delta_dias_atraso` contra el snapshot
anterior de la cuenta, y `continuo_desde_anterior` es `true` solo si los cortes son consecutivos.
En un reingreso la diferencia se muestra, con `continuo_desde_anterior = false` y los cortes que
faltó, pero no se etiqueta como la evolución de un periodo.

El **estado de presencia** de una cuenta es `EN_CARTERA` si tiene snapshot en el último corte de su
cartera, y `NO_OBSERVADA_EN_ULTIMO_CORTE` si no. No hay `LIQUIDADA`, `CERRADA`, `CASTIGADA` ni
`CANCELADA`: la ausencia, por sí sola, no demuestra ninguna de esas causas. Con `?al=AAAA-MM-DD`, la
Cuenta 360 se ve como se veía en esa fecha, con los cortes de fecha hasta ese día.

## Backfill

La migración `0008` solo crea las tablas: procesar millones de filas no es trabajo de Alembic. Los
datasets conformados que ya existían (los de v0.6) se historian con un proceso de la aplicación:

```bash
motor-cartera backfill-historia --dry-run    # cuánto falta y qué encolaría; no encola nada
motor-cartera backfill-historia              # encola un trabajo HISTORIA por dataset sin historia
motor-cartera backfill-historia --reintentar-fallidas
```

Encuentra los datasets de cartera/v2 y de pagos/v1 sin una ejecución `historia/v1` `EXITOSA`, y
los separa: los que no tienen ninguna ejecución se encolan; los que tienen una `EN_PROCESO` ya están
en la cola; los que solo tienen ejecuciones `FALLIDA` se reportan con su último resultado, y se
encolan solo con `--reintentar-fallidas` (un conflicto de corte volvería a fallar). Los de cartera
se encolan en orden de fecha de corte, y después los de pagos, cada uno en su propia transacción;
el orden es una cortesía, porque la historia que resulta no depende de él. Es idempotente: correrlo
dos veces no encola nada dos veces. Los trabajos los ejecuta el worker.

## Linaje

Cada valor de un snapshot se explica de punta a punta:

```
CuentaCanonica
 → SnapshotCuenta        (con su source_row y su source_sheet)
 → CorteCanonico
 → EjecucionHistoria     (la que lo publicó, y las de sus fuentes equivalentes)
 → DatasetConformado
 → ArtefactoFuente       el Parquet, por su SHA-256
 → ArtefactoFuente       el archivo original, por su SHA-256
```

Y cada pago observado:

```
CuentaCanonica → (despacho, cartera, CLIENTE_UNICO) → PagoObservado (con su fila y su hoja)
 → EjecucionHistoria → DatasetConformado → IngestaPagos → ArtefactoFuente
```

Por la API: `GET /cuentas/{cuenta_id}/historia` da el `corte_id`, el `dataset_id`, el `source_row`
y el `source_sheet` de cada snapshot; `GET /cartera/cortes/{corte_id}`, el Parquet y el original del
corte con sus SHA-256 y sus fuentes equivalentes; y `GET /corridas/{run_id}/fuente`, la corrida que
publicó el dataset.

## Índices y volumen

El volumen de diseño es el de la cartera objetivo: 500,000 cuentas y un corte por semana, unas 26
millones de snapshots al año, y del orden de 15 millones de pagos observados al año. Cada índice
cuesta almacenamiento y escritura, así que no hay ninguno "por si acaso":

| Tabla | Índice | Para qué |
|---|---|---|
| `cuenta_canonica` | `UNIQUE (despacho_id, cartera_id, cliente_unico)` | La identidad, buscar una cuenta y resolver las de un corte |
| `cuenta_canonica` | `UNIQUE (cuenta_id)` | `GET /cuentas/{cuenta_id}` |
| `corte_canonico` | `UNIQUE (despacho_id, cartera_id, fecha_corte)` | Un corte por fecha; los cortes de la cartera y el último |
| `corte_canonico` | `UNIQUE (id, fecha_corte)` | El destino de la llave compuesta de cada snapshot |
| `corte_canonico` | `UNIQUE (corte_id)`, `UNIQUE (dataset_conformado_id)` | El identificador público; un corte por dataset |
| `snapshot_cuenta` | `PRIMARY KEY (corte_canonico_id, cuenta_canonica_id)` | Una cuenta una vez por corte; la cuenta en un corte |
| `snapshot_cuenta` | `(cuenta_canonica_id, fecha_corte)` | La historia de una cuenta, en los dos sentidos; su estado en una fecha |
| `pago_observado` | `PRIMARY KEY (dataset_conformado_id, source_row)` | Una fila, una observación |
| `pago_observado` | `(despacho_id, cartera_id, cliente_unico, fecha_recepcion)` | Los pagos de una cuenta, del más reciente al más antiguo |
| `ejecucion_historia` | dos únicos parciales por dataset y versión; uno simple por dataset | Una `EXITOSA` y un intento activo; las ejecuciones de un dataset |

Lo que se decidió **no** indexar:

- `(cuenta_canonica_id, fecha_corte DESC)` se declaró sin `DESC`: con la cuenta fija, PostgreSQL
  recorre el btree hacia atrás (`Index Scan Backward`) y el plan es el mismo.
- `pago_observado_id` no tiene índice propio: es un UUID determinista de `(dataset, fila)`, que ya es
  la llave primaria, y ninguna consulta de v0.7 lo busca.
- `corte_canonico (fecha_corte)` sola tampoco: la tabla tiene una fila por semana, y el índice
  único de la cartera ya la cubre.

**Sin particionado en v0.7.** El particionado de PostgreSQL no se introduce porque "suena
escalable": con los índices de arriba, ninguna consulta de una cuenta recorre una tabla grande, y
el benchmark lo mide (abajo). Se reconsidera con evidencia: si el tamaño de `snapshot_cuenta` o de
`pago_observado` hace caro su mantenimiento, o si la analítica de v0.14 necesita podar por fecha.

## Benchmark

`scripts/benchmark_historia.py` (manual, fuera del CI normal; también como workflow a mano) genera
un escenario longitudinal, ingiere cada corte y cada archivo de pagos, materializa la historia de
cada dataset en su propio proceso, corre `VACUUM ANALYZE` y mide las consultas de una cuenta, con el
plan de cada sentencia que emite el servicio (`EXPLAIN (ANALYZE, BUFFERS)`). Falla si alguna
consulta de una cuenta recorre entera `snapshot_cuenta`, `pago_observado` o `cuenta_canonica`.

### 12 cortes XL con sus pagos

Medido el 2026-10-07 con:

```bash
python scripts/benchmark_historia.py --perfil XL --cortes 12 \
  --destino <directorio> --salida benchmark_historia.json
```

- **Escenario.** Perfil XL: 500,000 cuentas iniciales, 12 cortes semanales en zip (del 2026-01-07
  al 2026-03-25) y los 11 archivos de pagos entre ellos, semilla 31416, sin hoja CARRIER. Generarlo
  tomó 167.2 s, e ingerir sus 23 fuentes (lo de v0.6, para comparar), 1,673.3 s: de 110 a 131 s
  por corte y de 20 a 28 s por archivo de pagos.
- **Máquina.** La de desarrollo, que no era dedicada: Windows 11, 12 hilos (AMD), Python 3.14.6 y
  PostgreSQL 16.15 local, con `shared_buffers` de 256 MB, `max_wal_size` de 4 GB y
  `checkpoint_timeout` de 5 minutos.
- **Cada dataset se materializa en su propio proceso**, con la misma materialización que ejecuta
  el worker.

| Dataset | Registros | Tiempo | Filas/s | Memoria pico | Staging · cuentas · snapshots (s) |
|---|---|---|---|---|---|
| Cartera 2026-01-07 | 500,000 | 27.8 s | 17,964 | 384 MiB | 7.3 · 8.0 · 11.8 |
| Cartera 2026-01-14 | 496,838 | 26.1 s | 19,068 | 369 MiB | 7.3 · 0.8 · 17.0 |
| Cartera 2026-01-21 | 493,599 | 78.7 s | 6,274 | 392 MiB | 7.3 · 0.7 · 69.9 |
| Cartera 2026-01-28 | 490,567 | 21.8 s | 22,462 | 379 MiB | 7.2 · 1.3 · 12.6 |
| Cartera 2026-02-04 | 487,579 | 58.7 s | 8,308 | 374 MiB | 7.2 · 0.9 · 49.7 |
| Cartera 2026-02-11 | 484,424 | 29.2 s | 16,565 | 392 MiB | 9.0 · 2.5 · 16.6 |
| Cartera 2026-02-18 | 481,485 | 27.5 s | 17,505 | 371 MiB | 7.0 · 0.9 · 18.8 |
| Cartera 2026-02-25 | 478,479 | 37.0 s | 12,946 | 381 MiB | 7.0 · 0.9 · 28.4 |
| Cartera 2026-03-04 | 475,481 | 20.5 s | 23,161 | 388 MiB | 6.9 · 1.0 · 12.0 |
| Cartera 2026-03-11 | 472,183 | 55.2 s | 8,559 | 388 MiB | 6.9 · 0.9 · 46.6 |
| Cartera 2026-03-18 | 469,066 | 20.4 s | 23,023 | 370 MiB | 7.0 · 0.9 · 11.9 |
| Cartera 2026-03-25 | 465,999 | 51.0 s | 9,131 | 383 MiB | 7.0 · 1.3 · 42.0 |

| Pagos | Registros | Tiempo | Filas/s | Memoria pico |
|---|---|---|---|---|
| Del 2026-01-08 al 01-14 | 281,037 | 14.9 s | 18,923 | 370 MiB |
| Del 2026-01-15 al 01-21 | 279,752 | 10.8 s | 25,866 | 355 MiB |
| Del 2026-01-22 al 01-28 | 277,426 | 10.5 s | 26,436 | 357 MiB |
| Del 2026-01-29 al 02-04 | 275,514 | 22.8 s | 12,078 | 359 MiB |
| Del 2026-02-05 al 02-11 | 274,722 | 35.8 s | 7,677 | 384 MiB |
| Del 2026-02-12 al 02-18 | 273,117 | 37.6 s | 7,270 | 397 MiB |
| Del 2026-02-19 al 02-25 | 270,388 | 12.8 s | 21,188 | 352 MiB |
| Del 2026-02-26 al 03-04 | 269,813 | 21.0 s | 12,821 | 346 MiB |
| Del 2026-03-05 al 03-11 | 266,566 | 83.6 s | 3,190 | 352 MiB |
| Del 2026-03-12 al 03-18 | 264,514 | 13.0 s | 20,403 | 348 MiB |
| Del 2026-03-19 al 03-25 | 262,997 | 98.5 s | 2,670 | 399 MiB |

**En total**, 815.1 s para 8,791,546 registros (10,786 filas por segundo): 453.9 s los cortes, con
una mediana de 28.5 s (de 20.4 a 78.7 s), y 361.2 s los pagos, con una mediana de 21.0 s (de 10.5 a
98.5 s). La memoria pico de cada proceso quedó entre 346 y 399 MiB: en memoria hay un lote a la
vez. Comprobar el SHA-256 del Parquet tomó a lo más 0.4 s, y confirmar, otro tanto. Las
23 ejecuciones terminaron `EXITOSA`.

**Lo publicado cuadra con el manifiesto del escenario:**

| Tabla | Filas | Datos | Índices | Total |
|---|---|---|---|---|
| `snapshot_cuenta` | 5,795,700 | 1,015.4 MiB | 291.6 MiB | 1,307.3 MiB |
| `pago_observado` | 2,995,846 | 836.1 MiB | 316.2 MiB | 1,152.6 MiB |
| `cuenta_canonica` | 606,858 | 58.5 MiB | 92.6 MiB | 151.2 MiB |
| `corte_canonico` | 12 | | | 0.1 MiB |
| `cuenta` (la capa operacional de v0.6, para comparar) | 5,795,700 | 527.0 MiB | 557.7 MiB | 1,084.9 MiB |

- 606,858 cuentas canónicas: las 500,000 iniciales más las 106,858 altas del manifiesto.
- 5,795,700 snapshots: la suma de las cuentas de los 12 cortes, uno por cuenta y corte.
- 2,995,846 pagos observados: la suma de los movimientos de los 11 archivos, con sus 14,904
  repetidos exactos, que se conservan como observaciones distintas.
- La base completa, con la capa operacional y las corridas, ocupa 3,706.2 MiB. Un snapshot cuesta
  unos 236 bytes con sus índices, y un pago observado, unos 403.

El índice de la historia de una cuenta (`ix_snapshot_cuenta_historia`, 167 MiB) pesa más que la
llave primaria del snapshot (124 MiB): cada corte agrega una entrada por cuenta repartida en todo el
índice, mientras que la llave primaria crece por su extremo. Es el costo de que la historia de una
cuenta se lea en unas pocas páginas. El de los pagos de una cuenta (`ix_pago_observado_cuenta`)
ocupa 252 MiB.

**El costo no crece con la historia.** El staging (de 6.9 a 9.0 s por corte) y las cuentas nuevas
(de 0.7 a 2.5 s; 8.0 s en el primer corte, que crea 500,000) son estables en los 12 cortes. En los
cortes más rápidos, los snapshots tardan lo mismo con la tabla vacía (11.8 s, el primer corte) que
con 4.9 millones de filas ya publicadas (11.9 s, el del 2026-03-18); y los pagos, lo mismo en el
primer archivo (14.6 s) que en el décimo, con 2.5 millones ya publicados (12.6 s).

**La variación es del entorno, y se dice.** Lo que varía son las escrituras masivas: los snapshots,
de 11.8 a 69.9 s, y los pagos, de 10.3 a 96.9 s, en datasets casi del mismo tamaño. Dos de los
más lentos coinciden con sincronizaciones de checkpoint que PostgreSQL registró (`log_checkpoints`):
una de 60.4 s mientras se escribían los snapshots del 2026-01-21, el corte más lento, y otra de
41.9 s durante los pagos del 2026-03-05 al 11. Los demás, incluido el archivo de pagos más lento
(98.5 s), no coinciden con ningún evento de la base: la máquina no era dedicada. En el servidor de
producción se vuelve a medir, con su configuración (v0.18).

Después de materializar todo, `VACUUM ANALYZE` de las cuatro tablas históricas tomó 127.7 s.

### Las consultas de una cuenta

Con todo lo anterior publicado, cada consulta de la Cuenta 360 se midió 25 veces sobre tres cuentas:
una que está en los 12 cortes (`CU0000000607`), una que salió (`CU0000026427`) y la que más pagos
tiene (`CU4828964117`, con 23). Es la mediana, y entre paréntesis el percentil 95, medidas desde el
servicio (`historia.cuenta360`, con su sesión): incluyen Python, SQLAlchemy y la ida y vuelta de cada
sentencia a la base.

| Consulta | En los 12 cortes | Salió | Más pagos |
|---|---|---|---|
| Buscar por `CLIENTE_UNICO` | 3.03 ms (3.91) | 2.56 ms (4.26) | 2.77 ms (5.78) |
| Cuenta 360 | 7.50 ms (9.70) | 5.39 ms (6.44) | 5.36 ms (6.69) |
| Cuenta 360 en una fecha (`?al=`) | 5.30 ms (5.85) | 5.45 ms (7.20) | 5.18 ms (6.23) |
| Historia, primera página | 4.61 ms (9.58) | 4.43 ms (5.72) | 5.45 ms (6.89) |
| Eventos | 2.42 ms (3.36) | 2.07 ms (3.15) | 2.41 ms (3.38) |
| Pagos observados, primera página | 3.68 ms (5.21) | 3.50 ms (4.46) | 4.24 ms (5.04) |
| Cortes de la cartera y el último | 3.96 ms (4.64) | 4.25 ms (5.18) | 4.52 ms (5.02) |

Cada sentencia que emite el servicio se corrió con `EXPLAIN (ANALYZE, BUFFERS)`; los planes completos
quedan en `planes.txt`, junto al reporte. Ninguna recorre una tabla grande, y dentro de PostgreSQL
cada una se ejecuta en 0.014 a 0.137 ms, leyendo de 1 a 30 páginas, todas en caché:

| Sentencia | Acceso a la tabla grande | Páginas | Ejecución |
|---|---|---|---|
| La cuenta por su llave natural | `Index Scan using uq_cuenta_canonica_clave` | 4 | 0.037 ms |
| Los cortes de la cuenta | `Index Only Scan using ix_snapshot_cuenta_historia` (`Heap Fetches: 0`) | 7 | 0.017 ms |
| La historia, primera página | `Index Scan Backward using ix_snapshot_cuenta_historia` | 18 | 0.092 ms |
| El estado en una fecha | `Index Only Scan using ix_snapshot_cuenta_historia` (`fecha_corte <= ...`) | 4 | 0.014 ms |
| El snapshot en un corte | `Index Scan using ix_snapshot_cuenta_historia` (cuenta y fecha) | 7 | 0.046 ms |
| Cuántos pagos observados | `Index Only Scan using ix_pago_observado_cuenta` | 8 | 0.042 ms |
| Los pagos observados, primera página | `Index Scan Backward using ix_pago_observado_cuenta` | 30 | 0.137 ms |
| El último corte | `Index Scan Backward using uq_corte_canonico_fecha` | 4 | 0.040 ms |

```
Limit  (cost=50.93..50.95 rows=11 width=204) (actual time=0.064..0.066 rows=12 loops=1)
  Buffers: shared hit=18
  ->  Sort  (cost=50.93..50.95 rows=11 width=204) (actual time=0.064..0.065 rows=12 loops=1)
        Sort Key: snapshot_cuenta.fecha_corte DESC
        Sort Method: quicksort  Memory: 28kB
        Buffers: shared hit=18
        ->  Hash Join  (cost=3.15..50.73 rows=11 width=204) (actual time=0.039..0.055 rows=12 loops=1)
              Hash Cond: (snapshot_cuenta.corte_canonico_id = corte_canonico.id)
              Buffers: shared hit=18
              ->  Index Scan Backward using ix_snapshot_cuenta_historia on snapshot_cuenta  (cost=0.43..47.86 rows=11 width=172) (actual time=0.007..0.018 rows=12 loops=1)
                    Index Cond: (cuenta_canonica_id = 483265)
                    Buffers: shared hit=16
              ->  Hash  (cost=2.57..2.57 rows=12 width=36) (actual time=0.029..0.029 rows=12 loops=1)
                    ...   (los 12 cortes y sus 23 datasets: Seq Scan, 1 página cada uno)
Planning Time: 0.241 ms
Execution Time: 0.092 ms
```

Los `Seq Scan` que aparecen en los planes son de tablas con una fila por corte o por ingesta
(`corte_canonico`, `dataset_conformado`, `corrida`, `ingesta_pagos`), donde recorrerlas es más
barato que usar un índice. La distancia entre los 0.1 ms de PostgreSQL y los 2 a 8 ms del servicio
son las varias sentencias de cada consulta (la Cuenta 360 emite cinco) y su ida y vuelta desde
Python.

**Sin particionado, con números.** Con 5.8 millones de snapshots y 3 millones de pagos, la
sentencia más cara de una cuenta lee 30 páginas. Un año de la cartera objetivo son unos 26 millones
de snapshots (unos 5.7 GiB con sus índices) y unos 14 millones de pagos observados (unos 5.3 GiB):
la profundidad de un btree crece de forma logarítmica, y una consulta de una cuenta sigue leyendo
solo las páginas de esa cuenta. El particionado se reconsidera cuando el mantenimiento de esas
tablas (VACUUM, respaldos) cueste, o cuando la analítica de v0.14 necesite podar por fecha.

## Lo que `historia/v1` no hace

A propósito, y con su versión:

- **Analytics** (roll rates, vintages, cure rates, cohortes, recurrencia, matrices de transición,
  pronósticos): v0.14. Los snapshots permiten calcularlos después sin rediseñar nada.
- **El motor de pagos** (deduplicación, conciliación, reversos, aplicación contable, atribución a
  gestiones, recuperación neta, la llave económica definitiva): v0.8.
- **El lifecycle de cobranza** (gestiones, contactos, promesas, convenios y visitas): v0.9.
- **Decision Engine v2** (variables históricas, scores, probabilidades, ML): v0.10. `decision/v1`
  sigue leyendo `Cuenta` y no sabe que existe la historia.
- **Persona y crédito**, **correcciones o reemplazos explícitos de un corte** y **multitenancy**:
  ninguna fuente da todavía la evidencia para modelarlos.
