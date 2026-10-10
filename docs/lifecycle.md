# El lifecycle de cobranza (`lifecycle/v1`)

Hasta v0.8, Motor Cartera sabía qué datos llegaron, qué le pasó a cada cuenta a través de sus
cortes y qué movimientos económicos se interpretan de sus pagos. v0.9 agrega lo que faltaba para que
un motor de decisión pueda aprender algo:

> ¿Qué hizo la cobranza con cada cuenta, cuándo, con qué resultado, y qué se observó después?

Lo que la cobranza hizo se registra como **eventos operacionales**: gestiones, contactos, visitas,
promesas, convenios, sus cancelaciones y sus anulaciones. Es una tercera verdad, distinta de las
otras dos, y ninguna se convierte en otra:

```
FUENTE        lo que dicen CARTERA y PAGOS, tal como llegaron (v0.6–v0.7). Lo que un corte dice de
              la promesa o del plan de una cuenta es una observación de la fuente: OBSERVACION_EN_CORTE
OPERACIONAL   lo que la cobranza hizo, como eventos que registra Motor Cartera, con su momento de
              negocio (ocurrido_en) y su momento de registro (registrado_en)
ECONOMICO     los movimientos económicos canónicos que interpreta el motor de pagos (v0.8)
```

**El lifecycle no es una tercera fuente oficial.** Las fuentes oficiales siguen siendo dos, CARTERA y
PAGOS, y vienen del acreedor. Los eventos operacionales los registra Motor Cartera, por su API o por
una importación sintética, y dicen lo que la operación declaró que hizo. Ninguno se fabrica de un
snapshot.

El código vive en `src/motor_cartera/lifecycle/` (`reglas.py`, el vocabulario y sus reglas sin
base; `registro.py`, una escritura con su llave de idempotencia; `importacion.py`, la carga masiva;
`consultas.py`, lo que lee la API) y en `src/motor_cartera/evaluacion/` (la evaluación de las
promesas). La atribución de los pagos a las gestiones está en [atribucion.md](atribucion.md). Las
decisiones de diseño están en [decisiones.md](decisiones.md) (99 a 112).

## Las entidades

**`EventoLifecycle`**. El sobre común de toda acción registrada: su `evento_id` público (aleatorio:
identifica un registro), su cuenta canónica, su tipo, su `ocurrido_en` y su `registrado_en`, su
origen (`API` o `IMPORTACION`), su versión (`lifecycle/v1`), su `idempotency_key`, la huella de la
petición (`payload_hash`), su `actor_ref` y, en un cierre, su motivo y el evento al que se refiere.
Los tipos:

| Tipo | Se refiere a | Qué registra |
|---|---|---|
| `GESTION_REGISTRADA` | nada | una gestión, con su visita si es de campo |
| `PROMESA_CREADA` | su gestión | la promesa que nació de una gestión con resultado `PROMESA` |
| `CONVENIO_CREADO` | su gestión | el convenio que nació de una gestión con resultado `CONVENIO` |
| `PROMESA_CANCELADA` | su promesa | la promesa dejó de valer en el negocio |
| `CONVENIO_CANCELADO` | su convenio | el convenio dejó de valer en el negocio |
| `GESTION_ANULADA` | su gestión | la gestión se registró por error: con ella se anulan su visita, su promesa y su convenio |

**`GestionCobranza`**. Una acción concreta para cobrar o comunicarse con una cuenta: por dónde se
intentó (`canal` y `medio`), con quién se habló (`nivel_contacto`) y qué salió (`resultado`). Repite
el `ocurrido_en` de su evento para el índice de la historia de la cuenta.

**`VisitaCampo`**. El detalle de una gestión de `CAMPO`: qué encontró (`resultado`), y su inicio y
su fin si se saben. Sin GPS, rutas, zonas ni geocercas: eso es de v0.11–v0.13. Una visita no es otro
dominio: es una gestión de campo con su detalle, y se registra con la misma ruta que cualquier
gestión (`POST /cuentas/{id}/gestiones` con `canal=CAMPO` y un bloque `visita`).

**`PromesaPago`**. Cuánto (`monto_prometido`) y hasta cuándo (`fecha_limite`, un día de calendario
en la zona de la fuente) prometió pagar la cuenta. No guarda si se cumplió: eso lo dice una
evaluación versionada, con su propia fecha de corte.

**`ConvenioCobranza`** y **`CuotaConvenio`**. Un acuerdo operacional registrado: su monto total, su
vigencia y, si se declararon, sus cuotas, una por una, con su vencimiento y su monto. No es un
ledger.

## El vocabulario de una gestión

| Campo | Valores |
|---|---|
| `canal` | `DIGITAL`, `TELEFONICA`, `CAMPO`, `OTRO` |
| `medio` (opcional) | `LLAMADA` en `TELEFONICA`; `SMS`, `WHATSAPP` o `EMAIL` en `DIGITAL` |
| `nivel_contacto` | `NO_APLICA` (un mensaje de una vía), `SIN_CONTACTO`, `CONTACTO_TERCERO`, `CONTACTO_TITULAR` |
| `resultado` | `SIN_RESPUESTA`, `CONTACTO`, `RECHAZO`, `PROMESA`, `CONVENIO`, `VISITA_REALIZADA`, `OTRO` |
| resultado de la visita | `NO_LOCALIZADO`, `SIN_CONTACTO`, `CONTACTO_TERCERO`, `CONTACTO_TITULAR`, `DOMICILIO_NO_VALIDO`, `OTRO` |

Las reglas de coherencia son las mismas en Python (`lifecycle.reglas.incoherencias_de_gestion`, que
da un `422 GESTION_INCOHERENTE` que dice qué regla se rompió) y en las restricciones de la base, y una
prueba compara las dos en todas las combinaciones:

- `SIN_RESPUESTA` no tiene contacto; `CONTACTO`, `RECHAZO`, `PROMESA` y `CONVENIO` lo exigen;
- `VISITA_REALIZADA` es solo de `CAMPO`, y una gestión de `CAMPO` no termina en `SIN_RESPUESTA` ni en
  `CONTACTO` (su visita dice a quién encontró);
- `NO_APLICA` es de un mensaje `DIGITAL` o de `OTRO` canal;
- una gestión de `CAMPO` trae su visita, y solo ella; el resultado de la visita corresponde a su
  nivel de contacto (`NO_LOCALIZADO`, `SIN_CONTACTO` y `DOMICILIO_NO_VALIDO` son `SIN_CONTACTO`;
  `OTRO` admite cualquiera).

Hablar con el titular no es un resultado favorable: es un nivel de contacto. Un `actor_ref` es una
referencia opaca (`AGT-0007`), nunca un nombre ni un correo, y no es el `Gestor` de `pagos/v1`: no
existe un `GestorCanonico` (decisión 109). Los textos libres (`observacion`, `motivo`) rechazan lo que
parece un dato personal (un correo, un teléfono, una tarjeta, una CURP o un RFC) con
`422 DATOS_PERSONALES`.

## Dos tiempos: cuándo pasó y cuándo se registró

`ocurrido_en` es cuándo pasó, en el negocio: lo declara quien registra, con su zona horaria (un
instante sin zona es un `422`). `registrado_en` es cuándo Motor Cartera lo recibió: `now()` de la
transacción que lo registró, el reloj de la base, nunca el del cliente. Los dos se guardan y se
devuelven, y no se sustituyen.

**Un evento tardío es válido.** Una gestión de hace dos semanas que se registra hoy aparece en la
historia de la cuenta donde ocurrió, y la auditoría muestra los dos tiempos. Lo que no es válido es
un evento del futuro: `ocurrido_en` no puede ser posterior a `registrado_en` más cinco minutos de
tolerancia para el reloj del cliente (`422 OCURRIDO_EN_FUTURO`, y la restricción
`ck_evento_tiempos` en la base). Un evento que se refiere a otro tampoco puede haber ocurrido antes
que él (`422 OCURRIDO_ANTES_DEL_EVENTO`).

Una prueba registra A, C y D, y después B, por la importación; la historia de la cuenta ordenada por
`ocurrido_en` es A, B, C, D, igual que si hubieran llegado en orden.

## Solo se agrega: anulaciones y cancelaciones

Ninguna tabla del lifecycle admite `UPDATE` ni `DELETE`: un trigger de sentencia lo rechaza con
`restrict_violation` (`lifecycle_solo_agrega`), y una prueba lo intenta en cada tabla. Una
corrección es otro evento:

- **`GESTION_ANULADA`**: la gestión se registró por error. Sigue visible (`GET /gestiones/{id}`, con
  `estado=ANULADA` y su anulación), y deja de contar: no es candidata en una atribución, su promesa y
  su convenio quedan `ANULADA`/`ANULADO`, y el resumen de la cuenta no la cuenta como vigente;
- **`PROMESA_CANCELADA`** y **`CONVENIO_CANCELADO`**: el acuerdo dejó de valer en el negocio (no fue
  un error de registro). La promesa o el convenio quedan `CANCELADA`/`CANCELADO` desde que ocurrió la
  cancelación.

El estado operativo se deriva de los eventos al consultar; no se guarda. Un índice único parcial
(`ux_evento_relacionado`, sobre el evento relacionado y el tipo) garantiza en la base que haya a lo
más una anulación por gestión, una promesa y un convenio por gestión, y una cancelación por promesa o
por convenio, aunque dos peticiones lleguen a la vez.

## Idempotencia

Cada escritura (`POST`) exige la cabecera `Idempotency-Key`, de 8 a 128 caracteres de
`[A-Za-z0-9._:-]`. La petición tiene una **huella**: el SHA-256 de su tipo, del recurso sobre el que
escribe y de sus datos, en forma canónica (llaves ordenadas, instantes en UTC, importes con dos
decimales). Entonces:

- la misma llave con la misma huella es la **misma petición**: responde `200` con el recurso que ya
  se registró y la cabecera `Idempotent-Replayed: true`, sin registrar nada;
- la misma llave con otra huella es un error: `409 IDEMPOTENCY_KEY_REUTILIZADA`, sin registrar
  nada;
- una llave nueva registra el evento y responde `201` con `Location`.

**Lo garantiza PostgreSQL**, no solo el código: un índice único sobre (despacho, cartera, llave). Si
dos peticiones con la misma llave llegan a la vez, la segunda pierde la carrera en el índice, se
revierte su `SAVEPOINT` y encuentra lo que registró la primera; una prueba lo provoca con dos hilos.
`200` para una repetición, y no `201`, es una decisión: el cliente distingue sin ambigüedad lo que
creó de lo que ya existía (decisión 103).

## Observaciones de corte: los snapshots no son eventos

Un corte de la cartera puede traer, para una cuenta, `ESTATUS_PROMESA_PAGO`, `MONTO_PROMESA_PAGO`,
`ESTATUS_PLAN` y `MONTO_PLAN`. Eso es lo que la fuente dijo en esa fecha, no un evento: Motor Cartera
no crea una `PromesaPago` ni un `ConvenioCobranza` de ahí, y no infiere que una gestión ocurrió. La
línea de tiempo lo muestra como **`OBSERVACION_EN_CORTE`**, en el dominio `FUENTE_CORTE`, con su
snapshot, su corte, su dataset, su fila (`source_row`, `source_sheet`) y su archivo original.

## La línea de tiempo de una cuenta

`GET /cuentas/{cuenta_id}/lifecycle` junta, por tiempo de negocio y paginado en la base, los tres
dominios sin convertir uno en otro:

| Dominio | Tipo | Instante |
|---|---|---|
| `OPERACIONAL` | el tipo del evento (`GESTION_REGISTRADA`, `PROMESA_CREADA`...) | su `ocurrido_en` |
| `FUENTE_CORTE` | `OBSERVACION_EN_CORTE` | el inicio del día de su corte, en la zona de la fuente |
| `ECONOMICO` | el tipo del movimiento (`PAGO`, `REVERSO`, `POSIBLE_REVERSO`) | su recepción, en la zona de la fuente |

Cada elemento trae exactamente uno de `operacional`, `fuente_corte` y `economico`, con su propia
evidencia. Filtra por `desde`, `hasta` y `dominio`, y ordena `desc` (por omisión) o `asc`.

## La evaluación de las promesas (`evaluacion-promesa/v1`)

Si una promesa se cumplió no lo decide un `UPDATE`: lo concluye una **evaluación versionada, a una
fecha de corte explícita** (`as_of`), sobre los movimientos económicos interpretados de su cuenta.
`as_of` nunca sale del reloj: los mismos datos, el mismo `as_of` y la misma versión dan el mismo
resultado, y un dato que llega después publica otra evaluación sin tocar la anterior.

Los movimientos **compatibles** con una promesa son los `PAGO` de su cuenta (con la conciliación del
motor de pagos) recibidos desde que se acordó hasta el final de su fecha límite (o de `as_of`, si es
antes), que ningún reverso recibido hasta `as_of` anuló. Compatible no es causado: la evaluación
observa recuperación compatible con la promesa, y no dice que la promesa, ni su gestión, la haya
producido. Es una observación, no causalidad. Tampoco exige que la atribución haya asociado el pago a su gestión (ver
[atribucion.md](atribucion.md)). Un mismo movimiento puede ser compatible con dos promesas: no se
reparte dinero entre ellas.

Los estados, en el orden en que se deciden:

| Estado | Cuándo |
|---|---|
| `CANCELADA` | Su gestión se anuló, o un `PROMESA_CANCELADA` ocurrido hasta el final de `as_of` la dejó sin efecto. |
| `CUMPLIDA` | La recuperación compatible alcanza el monto prometido. |
| `PENDIENTE` | `as_of` es anterior a su fecha límite, y todavía no se cumple. |
| `NO_EVALUABLE` | Vencida, pero los datos no permiten afirmar que no se pagó: hay pagos observados en su intervalo sin interpretar, o los pagos de la cartera no llegan más allá del final de su intervalo. Se prefiere no evaluar a afirmar un incumplimiento sin datos. |
| `PARCIAL` | Vencida, con recuperación compatible menor que lo prometido. |
| `INCUMPLIDA` | Vencida, sin recuperación compatible. |

Y sus motivos:

| Código | Cuándo | Datos |
|---|---|---|
| `GESTION_ANULADA` | Cancelada porque su gestión se anuló. | |
| `PROMESA_CANCELADA` | Cancelada por un `PROMESA_CANCELADA`. | `cancelada_en` |
| `MONTO_ALCANZADO` | Cumplida. | `observado`, `prometido` |
| `ANTES_DE_LA_FECHA_LIMITE` | Pendiente. | `observado`, `prometido` |
| `PAGOS_SIN_INTERPRETAR` | No evaluable: pagos de su intervalo sin interpretación vigente. | |
| `DATOS_DE_PAGOS_INSUFICIENTES` | No evaluable: los pagos de la cartera no llegan al final de su intervalo. | `horizonte` |
| `MONTO_PARCIAL` | Parcial. | `observado`, `prometido` |
| `SIN_RECUPERACION` | Incumplida. | |
| `POSIBLES_REVERSOS` | Además, informativo: hay posibles reversos de la cuenta en su intervalo, y no se restaron. | `cuantos`, `monto` |

El caso de la misión, con una promesa de $1,000 y fecha límite el día 15, evaluada explícitamente al
día 16: un pago compatible de $1,000 la deja `CUMPLIDA`; uno de $500, `PARCIAL`; ninguno,
`INCUMPLIDA`; cancelada antes, `CANCELADA`. Ninguna prueba usa la hora actual.

Una evaluación es **un trabajo `EVALUACION_PROMESAS` por cartera y fecha de corte**, nunca uno por
promesa, que un worker ejecuta en PostgreSQL por conjuntos y todo o nada, con su firma de entrada:
la misma fecha con los mismos datos no se publica dos veces (`YA_EVALUADA`). Los días de calendario
se convierten a instantes en PostgreSQL, con su base de zonas horarias.

## Convenios: sin ledger

Un convenio guarda lo que la operación acordó y nada más. Sus cuotas se reciben **una por una**, con
su vencimiento y su monto: no se generan de un plazo ni de una periodicidad (`semanal`, `quincenal`,
`mensual`) que nadie declaró. Si se declaran, suman exactamente el monto total, vencen en fechas
estrictamente crecientes y dentro de su vigencia; lo revisa la API y, al confirmar, un trigger
diferido de la base (`convenio_cuotas_coherentes`). Sin cuotas, el convenio no tiene calendario.

**No hay ledger.** Ningún movimiento se aplica a una cuota: con varios pagos y varias cuotas hay más
de una interpretación plausible, y no hay reglas que elijan una. `GET /convenios/{id}` trae la
**recuperación observada durante el convenio** (los `PAGO` vigentes de su cuenta en su vigencia),
que no es "cuota pagada", y `evaluacion_de_cuotas` es `NO_EVALUABLE`.

## Cuenta 360

`GET /cuentas/{cuenta_id}` trae `lifecycle_resumen`, sin convertirse en un JSON gigante: cuántas
gestiones vigentes y anuladas, la última gestión, el último contacto con el titular, cuántas
promesas y cuántas vigentes, cuántos convenios y cuántos vigentes, cuántas visitas y la última
atribución disponible. Con `al`, como se veía en esa fecha. Los detalles están en sus subrecursos:
`/gestiones`, `/promesas`, `/convenios`, `/lifecycle`, `/movimientos` y `/atribuciones`. La
respuesta se arma en una transacción `REPEATABLE READ` de solo lectura: una gestión o un movimiento
que se registra mientras se responde no aparece a la mitad.

## La API

| Ruta | Qué |
|---|---|
| `POST /cuentas/{cuenta_id}/gestiones` | Registra una gestión (con su visita si es de `CAMPO`). |
| `GET /cuentas/{cuenta_id}/gestiones` | Sus gestiones, paginadas y filtradas en la base por `desde`, `hasta`, `canal`, `nivel_contacto`, `resultado` y `estado`. |
| `GET /gestiones/{gestion_id}` | Una gestión: su cuenta, su contacto, su resultado, sus dos tiempos, su evento, si fue anulada y lo que nació de ella. |
| `POST /gestiones/{gestion_id}/anulaciones` | La anula. |
| `POST /gestiones/{gestion_id}/promesas` | Su promesa. |
| `GET /promesas/{promesa_id}` | Una promesa: sus datos, su estado operativo, su última evaluación y su linaje. |
| `POST /promesas/{promesa_id}/cancelaciones` | La cancela. |
| `GET /cuentas/{cuenta_id}/promesas` | Las de una cuenta, por estado. |
| `POST /gestiones/{gestion_id}/convenios` | Su convenio, con sus cuotas declaradas. |
| `GET /convenios/{convenio_id}` | Un convenio, sus cuotas y la recuperación observada en su vigencia. |
| `POST /convenios/{convenio_id}/cancelaciones` | Lo cancela. |
| `GET /cuentas/{cuenta_id}/convenios` | Los de una cuenta. |
| `GET /cuentas/{cuenta_id}/lifecycle` | Su línea de tiempo en los tres dominios. |
| `POST /evaluaciones-promesas` | `{as_of}`: evalúa las promesas de la cartera a esa fecha (`422 AS_OF_FUTURO` si es posterior a hoy). |
| `GET /evaluaciones-promesas`, `/{evaluacion_run_id}`, `/{evaluacion_run_id}/promesas` | Las evaluaciones y lo que concluyeron de cada promesa. |

Toda escritura es individual y transaccional: un evento por petición, con su llave. Lo masivo
(evaluar, atribuir, importar) no pasa por estas rutas.

## La importación: `motor-cartera cargar-lifecycle`

Para pruebas, integraciones y el benchmark, `motor-cartera cargar-lifecycle ARCHIVO [--dry-run]`
importa eventos operacionales sintéticos de un JSONL (o `.jsonl.gz`). **No es una fuente oficial**:
es la importación de eventos operacionales, con las mismas reglas que la API, y cada evento queda con
origen `IMPORTACION`.

Cada línea es un objeto con su `tipo` y su `idempotency_key`. Una gestión dice su cuenta por
`cliente_unico`; lo demás se refiere a lo que detalla, cancela o anula por la **llave** con que se
registró (`gestion`, `promesa` o `convenio`), en el mismo archivo o antes, por la API o por otra
importación:

```json
{"tipo":"GESTION_REGISTRADA","idempotency_key":"lc7-20260806-g0000001","cliente_unico":"CU0000004521","ocurrido_en":"2026-08-06T10:15:00-06:00","canal":"TELEFONICA","medio":"LLAMADA","nivel_contacto":"CONTACTO_TITULAR","resultado":"PROMESA","actor_ref":"AGT-0042"}
{"tipo":"PROMESA_CREADA","idempotency_key":"lc7-20260806-g0000001-p","gestion":"lc7-20260806-g0000001","monto_prometido":"1500.00","fecha_limite":"2026-08-14"}
```

Lee por lotes; valida cada línea en Python (el contrato de su tipo, la coherencia, el calendario de
un convenio, los datos personales) y la copia con **COPY** a tablas temporales; nada se inserta fila
por fila. En PostgreSQL, por conjuntos: una llave repetida en el archivo con el mismo contenido
cuenta una vez; una ya registrada con la misma huella no se registra otra vez (**el mismo archivo
dos veces no duplica nada**); con otra huella, es un error. Después registra por fases, en el orden
en que los eventos dependen unos de otros: gestiones con sus visitas, promesas y convenios con sus
cuotas, cancelaciones y anulaciones, cada fase con las mismas revisiones de estado que la API. **Todo
o nada**: con un solo problema no registra nada, termina con código 1 y dice cada línea y por qué.

## El generador sintético

`motor-cartera generar-escenario --lifecycle` agrega a un escenario longitudinal lo que la cobranza
hizo en cada periodo, un `lifecycle_<desde>_<hasta>.jsonl.gz` por periodo, listo para
`cargar-lifecycle`: gestiones digitales, llamadas, contactos fallidos, contactos con el titular y con
terceros, visitas, promesas, convenios con y sin calendario, cancelaciones, anulaciones y eventos
tardíos (grupos de un periodo que llegan en el archivo del siguiente). Algunas gestiones con contacto
ocurren antes de un pago del periodo, a veces dos, para que haya asociaciones únicas y pagos ambiguos
que atribuir. Todo es sintético y determinista por semilla, con su propio generador de números:
agregar el lifecycle **no cambia un byte** de los cortes ni de los pagos (una prueba lo compara). Un
escenario con lifecycle tiene que terminar antes de hoy: un evento operacional no ocurre en el
futuro.

## Backfill

`motor-cartera backfill-lifecycle --as-of AAAA-MM-DD [--dry-run] [--reevaluar]` encola la evaluación
de las promesas de cada cartera a esa fecha, si todavía no la tiene: un trabajo por cartera, que la
cola ejecuta y que se puede reanudar después de una caída. La fecha es obligatoria: nunca sale del
reloj. `backfill-atribucion` hace lo mismo con la atribución ([atribucion.md](atribucion.md)).

## Linaje

```
GestionCobranza ──► EventoLifecycle (evento_id, idempotency_key, registrado_en) ──► CuentaCanonica
PromesaPago / ConvenioCobranza ──► su gestión ──► ...
OBSERVACION_EN_CORTE ──► SnapshotCuenta ──► DatasetConformado (source_row, source_sheet) ──► ArtefactoFuente
```

Y de una atribución hasta el archivo de pagos, en [atribucion.md](atribucion.md).

## Índices y volumen

Los índices son los de las consultas que existen, y nada más:

| Tabla | Índice | Para qué |
|---|---|---|
| `evento_lifecycle` | `(cuenta_canonica_id, ocurrido_en)` | la línea de tiempo de una cuenta, en los dos sentidos |
| `evento_lifecycle` | único `(despacho_id, cartera_id, idempotency_key)` | la idempotencia, y resolver una referencia por llave al importar |
| `evento_lifecycle` | único parcial `(evento_relacionado_id, tipo_evento)` | una anulación, una promesa, un convenio o una cancelación por evento; si algo se anuló o se canceló |
| `gestion_cobranza` | `(cuenta_canonica_id, ocurrido_en)` | las gestiones de una cuenta; las candidatas de un pago en la atribución |
| `promesa_pago` | `(cuenta_canonica_id, fecha_limite)` | las promesas de una cuenta |
| `convenio_cobranza` | `(cuenta_canonica_id, fecha_inicio)` | los convenios de una cuenta |
| `evaluacion_promesa` | `(promesa_pago_id)` | la última evaluación de una promesa |

Cada detalle se une a su evento por un índice único (`uq_gestion_evento`, `uq_promesa_gestion`...).
**No hay particionado** (decisión 112): ninguna consulta de una cuenta recorre una tabla grande, y lo
que cuesta es escribir.

### Benchmark: 2.57 millones de eventos sobre el XL

`scripts/benchmark_lifecycle.py`, sobre la base del benchmark del motor de pagos (los 12 cortes XL:
500,000 cuentas iniciales, 2026-01-07 a 2026-03-25, semilla 31416, con sus pagos interpretados), con
intensidad 0.5. En la máquina de desarrollo, que no es dedicada: Windows 11, AMD Ryzen 5 5500U (6
núcleos, 12 hilos), 15 GB de RAM con 2 a 3 GB libres, PostgreSQL 16.15 con `shared_buffers` de
256 MB, `work_mem` de 4 MB y `max_wal_size` de 4 GB, y Python 3.14.

- **Generar**: el lifecycle de los 11 periodos, 2,573,729 eventos, en 59 s (53 MB comprimidos).
- **Cargar** con `cargar-lifecycle`, un archivo por periodo: **2,573,729 eventos en 832 s, 3,092 por
  segundo** en promedio (de 1,119 a 5,717 por archivo, según los checkpoints y el autovacuum de la
  base), con **114 MiB de memoria pico** por proceso y de 278 a 551 MiB de WAL por archivo. Quedaron
  2,066,688 gestiones (1,121,629 con contacto con el titular y 263,198 con un tercero), 230,085
  visitas, 410,626 promesas, 29,588 convenios con 59,388 cuotas, 35,870 cancelaciones y 30,957
  anulaciones.
- **Volver a cargar** el primer archivo: 17 s, ningún evento nuevo y 239,004 ya registrados.
- **Evaluar** las 410,626 promesas al 2026-03-25: 39 s (10,563 por segundo): 207,220 cumplidas,
  43,805 parciales, 107,688 incumplidas, 38,607 canceladas, 13,306 pendientes y ninguna no evaluable.
- **La base** creció de 9,434 a 12,621 MiB: `evento_lifecycle` 1,119 MiB (la mitad, índices),
  `gestion_cobranza` 524 MiB, `promesa_pago` 93 MiB, `evaluacion_promesa` 82 MiB, `visita_campo` 41
  MiB; la atribución, el resto ([atribucion.md](atribucion.md)).

Las consultas de una cuenta, por la API (con su serialización; la mediana de 25 repeticiones, sobre
la cuenta con más eventos):

| Consulta | Mediana | p95 | Sentencias | La más lenta, dentro de PostgreSQL |
|---|---:|---:|---:|---:|
| `GET /cuentas/{id}/gestiones` | 19.9 ms | 21.0 ms | 3 | 0.50 ms |
| `GET /cuentas/{id}/lifecycle` | 25.9 ms | 27.6 ms | 10 | 0.38 ms |
| `GET /cuentas/{id}/promesas?estado=VIGENTE` | 24.6 ms | 32.4 ms | 4 | 0.36 ms |
| `GET /cuentas/{id}` (Cuenta 360) | 42.4 ms | 45.9 ms | 20 | 0.19 ms |

Con `EXPLAIN (ANALYZE, BUFFERS)`, cada sentencia de una consulta de una cuenta entra por un índice
(`ix_evento_cuenta_ocurrido`, `ix_gestion_cuenta_ocurrido`, `ix_promesa_cuenta_limite`,
`ux_evento_relacionado`, `ix_movimiento_cuenta`, `ix_snapshot_cuenta_historia`...) y ninguna hace un
Seq Scan sobre una tabla grande: los únicos son sobre tablas de unas cuantas filas (las ejecuciones
del motor de pagos y de la atribución, los cortes, los datasets). Lo que tarda una respuesta es el
servicio, no la base.

## Lo que `lifecycle/v1` no hace, a propósito

- No es una fuente oficial, ni convierte snapshots en eventos.
- No tiene un `GestorCanonico`: `actor_ref` es una referencia opaca, sin catálogo de personas.
- No es un ledger: no aplica pagos a cuotas ni calcula saldos de un convenio.
- No hace rutas, GPS, zonas, geocercas ni jornadas: las visitas no se validan contra ninguna
  geografía (v0.11–v0.13).
- No calcula features, scores ni modelos: deja la verdad operacional para derivarlos.

## Lo que habilita para v0.10

Con el lifecycle, la evaluación de promesas y la atribución, el Decision Engine v2 puede derivar sin
inventar nada: días desde la última gestión; intentos de los últimos 7 y 30 días; contactos con el
titular y tasa histórica de contacto; promesas y promesas cumplidas a una fecha; recuperación
observada después de una gestión y el tiempo de gestión a pago (de las asociaciones únicas, sin
contar las ambiguas como si no lo fueran); canal utilizado; visitas; y convenios vigentes. Cada una
se puede calcular "como se veía en una fecha", porque cada evento tiene sus dos tiempos y cada
evaluación su fecha de corte.
