# Respaldos y restauración (v0.18)

El estado de motor-cartera vive en dos lugares, y un respaldo que solo tenga uno no restaura nada
que sirva:

- **PostgreSQL**: las filas (corridas, cuentas, historia, pagos, lifecycle, decisiones, geografía,
  planes, rutas, analytics, modelos, optimizaciones, la cola).
- **El almacén de artefactos** (`MC_SOURCE_STORE_ROOT`): cada archivo recibido, byte por byte, y cada
  dataset conformado, guardados por su SHA-256, que las filas de `artefacto_fuente` registran. Una
  base sin su almacén tiene corridas cuyo archivo ya no existe (y motores que no pueden releer su
  dataset); un almacén sin su base es un montón de archivos sin nombre.

`scripts/respaldo.py` respalda los dos juntos, verifica un respaldo y lo restaura en un entorno
nuevo. Corre en la imagen `motor-cartera-respaldo` (PostGIS 16, con `pg_dump` y `pg_restore` del
mismo mayor que el servidor, y Python con psycopg). El porqué, en las decisiones 294 a 296 de
[decisiones.md](decisiones.md).

## Qué hace

**Respaldar.** Una transacción `REPEATABLE READ` de solo lectura exporta su foto de la base
(`pg_export_snapshot`); `pg_dump` vuelca esa misma foto (formato custom, sin dueño ni permisos, solo
el esquema de la aplicación, sin los datos de las tablas de las extensiones) y, en esa transacción,
se cuentan las filas de cada tabla y se leen los artefactos registrados. Después se copian del
almacén los artefactos de esa lista, firmando cada uno mientras se copia. La aplicación escribe
primero el objeto durable y después la fila que lo registra: todo lo que la foto registra ya está en
el almacén. Un artefacto que falte o cuyos bytes ya no sean los de su SHA-256 niega el respaldo
entero y no deja nada: un respaldo incompleto no se da por bueno.

```text
respaldos/motor-cartera-20261018T231451Z/
├── manifiesto.json      formato respaldo-motor-cartera/v1: cuándo, la revisión del esquema (0017),
│                        la versión de PostgreSQL y de la aplicación, el SHA-256 y el tamaño del
│                        volcado, las filas de cada tabla y el resumen del almacén; ningún dato
├── motor-cartera.dump   el volcado (pg_dump, formato custom)
├── almacen.lista        una línea "<sha256> <bytes>" por artefacto, con su propio SHA-256 en el manifiesto
└── almacen/sha256/ab/abcdef...   cada artefacto, de solo lectura, en su lugar por contenido
```

**Verificar.** El volcado contra su SHA-256 y su tamaño, la lista contra el suyo y cada objeto contra
su nombre (que es su SHA-256), sin que falte ni sobre ninguno.

**Restaurar.** Primero verifica el respaldo entero: uno alterado no toca nada. Después revisa el
destino: que no sea la base de origen, que esté **vacía**, que su PostgreSQL no sea de un mayor
anterior, que tenga las extensiones del origen y que la revisión del esquema sea la esperada. Copia
los objetos al almacén de destino (uno que ya está con sus bytes no se toca; uno dañado se repone) y
después restaura la base con `pg_restore` en **una sola transacción**: o queda todo, o nada. Al
final valida las filas de cada tabla contra el manifiesto, que ninguna restricción quede sin
validar, la revisión y que cada artefacto que la base registra esté en el almacén. Escribe su informe
a la salida estándar: el respaldo no se modifica, y se monta de solo lectura.

| Código | Por qué se niega |
| --- | --- |
| `RESPALDO_ALTERADO` | el volcado, la lista o un objeto no son los del manifiesto, o falta o sobra un objeto |
| `SIN_MANIFIESTO`, `MANIFIESTO_ILEGIBLE`, `MANIFIESTO_INCOMPLETO`, `FORMATO_DESCONOCIDO` | el manifiesto falta, no es JSON, le faltan campos o es de otro formato |
| `DESTINO_ES_EL_ORIGEN` | el destino es la base de donde salió el respaldo |
| `BASE_NO_VACIA` | el destino ya tiene tablas: se restaura solo en una base nueva |
| `POSTGRES_ANTERIOR`, `PG_RESTORE_ANTERIOR` | el servidor o `pg_restore` son de un mayor anterior al del respaldo |
| `EXTENSIONES_FALTANTES` | el destino no tiene una extensión del origen (PostGIS la pone la plataforma) |
| `REVISION_INCOMPATIBLE` | el respaldo es de otra revisión del esquema (`--permitir-otra-revision` lo permite y lo advierte; después hay que migrar) |
| `SIN_ALMACEN`, `SIN_ALMACEN_DESTINO` | la base registra artefactos y no se dio el almacén (`--sin-almacen` lo permite a propósito; el informe dice `VALIDADA_SIN_ALMACEN`) |
| `ALMACEN_INCOMPLETO`, `ALMACEN_DANADO` | al respaldar, un artefacto registrado falta en el almacén o ya no tiene sus bytes |

Termina con 0 si todo salió bien, con 1 si se negó o una validación falló y con 2 ante un error
inesperado.

## Cómo se usa

En el compose de producción hay dos servicios, cada uno con lo mínimo:

| Servicio | Rol de la base | Almacén | Respaldos | Usuario |
| --- | --- | --- | --- | --- |
| `respaldo` | `mc_respaldo` (solo lectura) | solo lectura | escribe | `MC_RESPALDO_USUARIO` (en Linux, el dueño de `./respaldos`) |
| `restauracion` | `mc_migraciones` (dueño del esquema: lo que restaura queda suyo, y los privilegios por omisión le dan DML a la API y al worker) | escribe | solo lectura | 10001, el de la aplicación |

```bash
# Respaldar (deja respaldos/motor-cartera-<fecha>)
MC_RESPALDO_USUARIO="$(id -u):$(id -g)" \
  docker compose -f docker-compose.production.yml --profile respaldo run --rm respaldo

# Verificar
docker compose -f docker-compose.production.yml --profile restauracion run --rm --no-deps \
  restauracion verificar /respaldos/motor-cartera-<fecha>

# Restaurar en un entorno NUEVO: un volumen de PostgreSQL recien creado (y el del almacen)
docker compose -f docker-compose.production.yml up -d --wait postgres
docker compose -f docker-compose.production.yml --profile restauracion run --rm --no-deps \
  restauracion restaurar /respaldos/motor-cartera-<fecha> --revision-esperada 0017
docker compose -f docker-compose.production.yml up -d --wait   # migraciones no tiene nada que hacer
```

El compose de desarrollo tiene su propio servicio `respaldo` (`docker compose --profile respaldo run
--rm respaldo`).

### De desarrollo a producción

Así consigue datos el compose de producción local, y así lo hace el CI: se respalda el compose de
desarrollo después de su prueba de humo y se restaura en un entorno de producción nuevo.

## El simulacro

`scripts/simulacro_de_restauracion.py` ensaya la restauración en un entorno nuevo (PostgreSQL recién
levantado y vacío): verifica el respaldo, arma copias alteradas de cinco formas (un byte del volcado,
un objeto alterado, un objeto que falta, otra revisión del esquema y un PostgreSQL más nuevo) y
comprueba que cada una se niega con su código sin tocar la base ni el almacén; después restaura de
verdad y exige `VALIDADA`. El CI lo corre en cada push sobre el respaldo del job del compose, en una
máquina limpia.

Corrida del 2026-10-18, local:

| | |
| --- | --- |
| Respaldo del compose de desarrollo | 83 tablas, 156,297 filas, volcado de 5.98 MB, 97 artefactos (7.8 MB): 6.1 s (3.1 s la base, 3.1 s el almacén) |
| Los cinco respaldos alterados | negados con su código; la base siguió sin tablas y el almacén sin un objeto |
| La restauración | `VALIDADA`: las 83 tablas con sus filas, ninguna restricción sin validar, los 97 artefactos; 10.9 s de pared (`pg_restore`, unos 5 s) |
| Después | `alembic current`: `0017 (head)`; `alembic check`: sin cambios; `verificar-fuentes`: 97 de 97 intactos; las tres pruebas de humo de producción, Playwright y los simulacros, sobre la base restaurada |

## Lo que encontró hacerlo de verdad

- **El esquema `public`.** `pg_dump --schema=public` declara el esquema, y en el destino ya existe:
  su `CREATE SCHEMA` abortaba la restauración. Se quita esa entrada de la tabla de contenido
  (`pg_restore --list` / `--use-list`).
- **`spatial_ref_sys`.** El volcado traía los datos de esa tabla de PostGIS, que en el destino ya
  tiene la extensión y que no es del rol que restaura (`permission denied`). Ya no se vuelcan
  (`--exclude-table-data`) y, si un respaldo los trae, no se restauran.
- **El almacén.** Un respaldo solo de la base dejaba corridas sin su archivo: el respaldo lleva los
  dos.

## RPO, RTO y retención

| | Compose local | Referencia de Google Cloud |
| --- | --- | --- |
| Cuándo se respalda | a mano o programado por quien opera (diario como mínimo) | cada noche, a las 03:00 de la Ciudad de México (Cloud Scheduler y un job de Cloud Run), además de los respaldos automáticos de Cloud SQL |
| RPO | lo posterior al último respaldo | minutos para la base (PITR, 7 días de bitácora); el almacén, lo posterior al último respaldo nocturno |
| RTO objetivo | una hora | una hora con la infraestructura aplicada |
| Retención | la decide quien opera; `respaldos/` no entra al repositorio | el bucket retiene cada respaldo al menos 30 días, lo pasa a Nearline a los 30 y lo borra a los 180; Cloud SQL guarda 30 respaldos automáticos |

Los objetivos están en [slos.md](slos.md#recuperación-ante-desastres-rpo-y-rto) y el procedimiento
completo, en [runbooks/disaster-recovery.md](runbooks/disaster-recovery.md).

## Lo que no se respalda, a propósito

- **La red vial de OSRM** es un activo derivado: se reconstruye desde su extracto de OpenStreetMap con
  `motor-cartera preparar-red-vial` y su `preparar.sh` ([red_vial.md](red_vial.md)), y su versión
  publicada está en la base con su huella. La base sí guarda qué red se usó.
- **Los secretos**: se regeneran o se rotan ([runbooks/secret-rotation.md](runbooks/secret-rotation.md));
  un respaldo con secretos sería un secreto más.
- **Las sesiones** (Valkey): efímeras; después de restaurar, cada persona inicia sesión otra vez.
- **Las métricas, las trazas y los tableros** (Prometheus, Jaeger, Grafana): datos operacionales del
  momento, no estado del sistema. Los tableros y las alertas viven en el repositorio.
