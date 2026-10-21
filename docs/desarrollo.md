# Desarrollo

Cómo trabajar en el repositorio: correrlo sin Docker, de dónde salen sus datos, cómo se prueba, cómo
se verifican los tipos y qué revisa el CI. El arranque con Docker está en el
[README](../README.md#inicio-rápido); el recorrido de la API, en
[flujo_completo.md](flujo_completo.md).

## Sin Docker

Hace falta un PostgreSQL 16 con PostGIS 3.5 (el del compose sirve: `docker compose up -d
postgres`, puerto 5434; la migración 0012 crea la extensión) y [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env
uv sync --extra dev                                  # .venv con las versiones del uv.lock
source .venv/bin/activate                            # en Windows: .venv\Scripts\activate
alembic upgrade head
motor-cartera generar --destino datos/cartera.xlsx
motor-cartera cargar datos/cartera.xlsx              # la ingesta en primer plano, sin pasar por la API
uvicorn --factory motor_cartera.api.app:crear_app --reload
motor-cartera worker                                 # en otra terminal: ejecuta la cola
```

Las fuentes oficiales, también sin la API:

```bash
motor-cartera generar-oficial --destino datos/oficial --fecha-corte 2026-09-30
motor-cartera cargar datos/oficial/cartera_oficial_2026-09-30.xlsx --contrato cartera/v2 --fecha-corte 2026-09-30
motor-cartera cargar-pagos datos/oficial/pagos_oficial_2026-09-24_2026-09-30.xlsx
motor-cartera generar-escenario --destino datos/escenario --cortes 4 --primer-corte 2026-09-02
motor-cartera verificar-fuentes                      # vuelve a firmar cada artefacto del almacén
motor-cartera backfill-historia --dry-run            # cuánta historia falta, sin encolar nada
motor-cartera backfill-historia                      # encola la historia de lo publicado antes de v0.7.0
motor-cartera backfill-motor-pagos --dry-run         # cuántos pagos observados no tienen interpretación
motor-cartera backfill-motor-pagos                   # encola la interpretación de cada ventana pendiente
```

El almacén de artefactos es el directorio de `MC_SOURCE_STORE_ROOT` (`datos/fuentes` en el
`.env.example`); la API, el worker y el CLI tienen que ver el mismo.

`ruteo/v2` sin Docker: con los binarios nativos de OSRM v26.9.0 (en Windows, los que trae el
paquete `node_osrm` de su release), `preparar-red-vial --osrm-bin` prepara y publica la red en un
paso, `osrm-routed` la sirve, y el worker la usa con `MC_OSRM_URL`. Así se validó v0.13.0,
prueba de humo incluida, en una máquina que entonces no tenía Docker ni WSL:

```bash
motor-cartera generar-red-sintetica --destino datos/red_vial/fuente/red_sintetica.osm
motor-cartera preparar-red-vial datos/red_vial/fuente/red_sintetica.osm --region "Red sintetica" \
  --proveedor-datos SINTETICO --licencia sintetica --osrm-bin <directorio de los binarios>
osrm-routed --algorithm mld --verbosity WARNING --max-nearest-locations 100 --port 5000 \
  datos/red_vial/<huella>/red.osrm
MC_OSRM_URL=http://localhost:5000 motor-cartera worker      # en otra terminal
motor-cartera rutear-v2 --plan <campo_run_id> --red-vial-version <red_vial_version_id>
```

Sin un worker corriendo, la API registra el trabajo pero nadie lo ejecuta: el flujo se queda en la
cola. `motor-cartera worker --una-vez` procesa a lo más un trabajo y sale, para probar o
diagnosticar. `cargar` registra la corrida y su trabajo en la misma cola, lo toma él mismo y hace
la ingesta en primer plano, con el mismo código que el worker; si se interrumpe, un worker la
termina cuando vence el lease. No crea flujo, así que las demás etapas se piden a mano, y termina
con código 1 si la corrida no publica, para que un script lo note.

Las versiones exactas de todas las dependencias están en `uv.lock`, y el CI y la imagen
instalan esas mismas. Si cambias una dependencia en `pyproject.toml`, corre `uv lock` (con el `uv`
0.12.21 del proyecto) y sube los dos archivos juntos: con el lock desfasado, el CI falla. El lock
sirve para cada Python que declara `requires-python`, 3.12, 3.13 y 3.14, y la matriz de
compatibilidad lo instala en cada una ([El CI](#el-ci)); 3.12 es el runtime de las imágenes. En
3.15 no se instala sin compilar (pyarrow 25.0.1 no publica ruedas para ella), así que
`requires-python` la deja fuera hasta que sus dependencias la soporten sin forzar un mayor.

## Los datos

**Ningún dato real entra a este repositorio.** Todo lo que el sistema procesa lo produce el
generador sintético incluido. Las claves geográficas son claves reales del Marco
Geoestadístico del INEGI (público) de cinco entidades, con casi todas las cuentas en
Puebla; el resto son datos inventados con distribuciones parecidas a las reales: saldos
con cola larga, atraso por tramos y concentración geográfica desigual. El generador mete filas inválidas a
propósito para que el contrato tenga de dónde agarrarse.

La cartografía tampoco entra. La referencia municipal oficial (el Marco Geoestadístico Integrado
2025 del INEGI, público) se descarga de su URL y se verifica contra el SHA-256 de un manifiesto del
repositorio; las pruebas y la prueba de humo usan referencias sintéticas que escriben al vuelo. Las
redes viales tampoco entran: un extracto de OpenStreetMap se descarga y se prepara fuera del
repositorio, y las pruebas, la prueba de humo y el CI usan una red sintética diminuta que generan
al vuelo. Y las coordenadas de las cuentas son sintéticas: puntos de la cartografía pública, en
proporciones explícitas, no ubicaciones de nadie.

El `.gitignore` lo cuida por nombre, pero solo frena lo que todavía no está trackeado. Lo
trackeado lo revisa `scripts/verificar_archivos_trackeados.py` en el CI: falla si entró algo
que el `.gitignore` excluye (con `git add -f`, o en mayúsculas donde git las distingue) o, con
cualquier nombre, una hoja de cálculo, un zip, un Parquet o un Arrow, un archivo comprimido (gzip,
bzip2, xz, zstd, 7z, rar), un volcado de `pg_dump`, una base SQLite (un GeoPackage lo es), un
Shapefile, un FlatGeobuf, un extracto de OpenStreetMap (`.osm.pbf`) o un archivo preparado de OSRM
(un tar), que reconoce por sus primeros bytes. Antes de un commit también se corre a mano:

```bash
python scripts/verificar_archivos_trackeados.py
```

## Pruebas

```bash
pytest
```

Las pruebas de la seguridad y de la operación cubren la configuración que falla cerrada
(`tests/test_config_produccion.py`), cada regla de un token y de la JWKS
(`tests/test_seguridad_tokens.py`), la matriz, cada permiso y cada credencial
(`tests/test_api_autorizacion.py`), los logs, las métricas, las trazas y la cola
(`tests/test_observabilidad.py`), ningún dato personal en los logs de una corrida completa
(`tests/test_logs_sin_datos_personales.py`), la salud, los límites de cuerpo y el candado de las
migraciones, el respaldo y su restauración (`tests/test_respaldo.py`, con `pg_dump` y `pg_restore` de
verdad en el CI), la política de la referencia de la nube (`tests/test_iac.py`) y la cadena de
suministro (`tests/test_cadena_de_suministro.py`). Contra el compose de producción corren su prueba
de humo del edge, la interna y la de los contenedores, Playwright con usuarios de Dex, los
simulacros de falla, la restauración en un entorno nuevo y la carga corta
([docs/production.md](production.md#cómo-se-verifica)).

Las pruebas que tocan la base corren contra PostgreSQL real, con el esquema creado por las
migraciones (no por `create_all`), así que también prueban la migración. Usan
`MC_DATABASE_URL_PRUEBAS`, por omisión `cartera_test` en el puerto 5434; la crean si no
existe. Como vacían la base antes de cada prueba, **se niegan a correr sobre una base que no
se llame `*_test`**. Dentro del compose: `docker compose exec api pytest`.

Las reglas de decisión no necesitan base: `pytest tests/test_decision_reglas.py` corre sus
golden tests, que fijan la decisión exacta, motivos incluidos, en cada frontera de días y de
saldo, y exigen que el núcleo sea determinista y no cargue nada fuera de la biblioteca estándar.
Las territoriales tampoco: `pytest tests/test_territorial_reglas.py` fija la carga, el lugar y
el motivo exactos en cada frontera y en cada desempate, y exige que el resultado no dependa del
orden en que llegan los municipios. Ni las de ruteo: `pytest tests/test_ruteo_reglas.py` fija las
coordenadas sintéticas exactas, una ruta completa paso a paso, cada desempate y el tope de diez
mejoras, con valores calculados aparte por una implementación de fuerza bruta, y exige que la ruta
no dependa del orden de llegada ni de la semilla de hash del proceso.

La cola y el worker también se prueban contra PostgreSQL real, sin dormir de más: los leases se
vencen en la base en lugar de esperarlos, y la muerte de un worker se simula sin matar procesos,
antes del motor, a media transacción y después de su `COMMIT`. Hay pruebas de `SKIP LOCKED`, de dos
workers a la vez, del lease vencido y del dueño anterior que ya no cierra, del latido, de la
espera entre intentos y de los intentos agotados, y del flujo completo, detenido y reanudado.

Las fuentes oficiales se prueban igual, contra PostgreSQL y con archivos sintéticos de cada
formato: el almacén (idempotencia, atomicidad, objetos faltantes y dañados, persistencia después de
la ingesta), la detección de formato por contenido, cada desviación de estructura de cartera/v2 y de
pagos/v1, las llaves repetidas entre lotes, la firma de contenido independiente del formato y del
orden, el conformado regenerado byte por byte desde el original, la proyección con el catálogo del
INEGI, CARRIER, los pagos sin deduplicar con su barrera y su trabajo durable (lease vencido,
reintentos, dos workers), la migración 0007 de ida y de vuelta, y las invariantes del escenario
longitudinal sobre los archivos que escribe. El benchmark de escala no corre en el CI.

El modelo histórico también, contra PostgreSQL y con fuentes sintéticas que se ingieren como
cualquier otra: la identidad (el mismo `CLIENTE_UNICO` en diez cortes es una cuenta con diez
snapshots; en otra cartera, otra cuenta), las fuentes equivalentes y los cortes conflictivos, el
snapshot estrecho con su fila de origen, la historia que no vuelve a leer el original (se borra del
almacén antes de materializar), los pagos sin deduplicar y sin cuenta, fallas inyectadas en cinco
puntos de la transacción y la muerte del proceso a la mitad, dos workers sobre el mismo dataset,
dos cortes con cuentas nuevas en común, dos fuentes del mismo corte a la vez y un worker que pierde
el lease después de calcular. Un escenario golden de seis cortes fija cada evento, cada continuidad
y cada delta; el escenario del generador se cruza con su manifiesto; un corte que llega tarde deja
la misma historia que en orden, y reconstruir toda la capa en un orden al azar da exactamente las
mismas filas, con los mismos identificadores públicos. La semántica de presencia (`presencia.py`) se
prueba sin base. El benchmark histórico no corre en el CI.

El motor de pagos también, contra PostgreSQL: las huellas en SQL y en Python con el mismo texto
canónico, el duplicado exacto (dos y tres copias, en uno o en varios archivos), la coincidencia de
la llave histórica que no se fusiona, dos pagos legítimos iguales, los reversos (la pareja aislada,
sin candidatos, con varios, con uno ambiguo o disputado, entre dos ventanas), el pago sin cuenta
que se concilia con otra interpretación cuando llega su corte, fallas inyectadas en cinco puntos y
la muerte del proceso, dos workers sobre la misma ventana, un worker que pierde el lease y no
publica, la firma de entrada que no deja publicar dos veces, el backfill idempotente, la
reconstrucción idéntica y el orden de llegada. La prueba de equivalencia compara, sobre pagos al
azar, lo que publica el SQL con lo que concluye el núcleo puro, observación por observación; el
escenario golden fija cada clasificación, cada movimiento y la recuperación de cada ventana, y el
del generador cuadra su `SALDO`, centavo por centavo, con los movimientos interpretados. Las reglas
puras (`pytest tests/test_motor_pagos_reglas.py`) no necesitan base. El benchmark del motor no
corre en el CI.

El Decision Engine v2 también, contra PostgreSQL. Un escenario golden, con una cuenta por cada caso
de la misión y una frontera de conocimiento después de la cual llegan, tarde, un corte, una
gestión, una promesa y un evento posterior a `as_of`, fija cada feature, cada regla y cada
decisión. Las pruebas sin fuga temporal agregan eventos, pagos, interpretaciones, atribuciones y
cortes posteriores a `known_at` o a `as_of`, y ninguna feature cambia; una segunda decisión con un
`known_at` posterior difiere de la primera exactamente en las cuentas con eventos tardíos, y la
primera queda intacta; reconstruir la capa da los mismos hashes. Hay pruebas de la frontera que
espera a un registro pausado a la mitad, de fallas inyectadas en cinco puntos, de dos workers, del
lease perdido, de la firma que no deja publicar dos veces y del SQL contra el evaluador puro, cuenta
por cuenta. La política y las features puras (`pytest tests/test_decision_v2_politica.py
tests/test_decision_v2_features.py`) no necesitan base. El benchmark de `decision/v2` no corre en el
CI.

La geografía y `territorial/v2` también, contra PostgreSQL con PostGIS y con referencias
sintéticas diminutas que las pruebas escriben al vuelo: el importador (cada sistema de coordenadas
por sus parámetros, la reparación segura y la que no, cada rechazo, todo o nada, dos importaciones
a la vez), un golden geográfico de tres municipios inventados y un caso por estado, uno territorial
con sus bases, el orden longitud-latitud (falla si se invierte), el borde (`ST_Covers`), el
versionado de la referencia y de las bases sin reescribir lo viejo, fallas inyectadas en seis
puntos de cada motor, dos workers, el lease perdido, dos cortes a la vez, la reconstrucción
idéntica, y el SQL contra el núcleo puro, cuenta por cuenta, con las distancias comparadas con la
fórmula de Vincenty. La referencia oficial del INEGI solo se prueba si el archivo está en la máquina
(`MC_REFERENCIA_INEGI`); el CI no la descarga. Las reglas puras (`pytest
tests/test_geografia_reglas.py tests/test_territorial_v2_politica.py`) no necesitan base.

`campo/v2` también, contra PostgreSQL con PostGIS y con escenarios cuyos puntos se eligen en
EPSG:6372: la configuración de campo (cada regla fail-closed, con todos sus problemas juntos; el
hash que no depende del orden ni del nombre; las ventanas encimadas que la base rechaza aunque no
pasen por la validación; dos publicaciones simultáneas), la jornada de un gestor en cada caso
(inactivo, festivo, ventanas especiales, día sin jornada), y un golden de las jornadas, de las
celdas exactas y su jerarquía, de la frontera de una celda (es de la celda que empieza ahí), de
tres gestores de cupos 2, 2 y 1 con siete candidatas (cinco asignadas y dos sin capacidad), de la
prioridad que nunca se invierte, del microtramo que cabe entero, del spillover al siguiente del
ranking y de la estabilidad al agregar un gestor, contra un reparto por turnos. Hay fallas
inyectadas en seis puntos, dos workers sobre el mismo plan, el lease perdido, dos fechas y dos
cortes a la vez, el cambio de gestor, de cupo y de calendario como otras versiones que no tocan el
plan viejo, la reconstrucción idéntica, las mismas cuentas en otro orden, y el SQL contra el
espejo en Python de la política, cuenta por cuenta; ninguna respuesta, bitácora ni exportación
lleva una coordenada. La política, la cuadrícula y el generador (`pytest
tests/test_campo_v2_politica.py tests/test_generador_campo.py`) no necesitan base. El benchmark
de `campo/v2` no corre en el CI.

`ruteo/v2` también, contra PostgreSQL y con proveedores de ruteo de pruebas que no son una red vial
(un snap y un tramo por par de puntos que la prueba decide): goldens de la matriz, de la prioridad,
del snap en cada frontera (0, 50, 499, 500, 500.004, 500.005 y 500.01 m), de la base no enrutable,
de la red desconectada, de la jornada simple y la partida (sin manejar en la pausa), del regreso
reservado y del 2-opt (que reduce el traslado y que rechaza un cambio que no cabe), y propiedades
sobre matrices al azar contra un simulador independiente; el backend con otra red, que falla sin
recibir un punto; el proveedor caído, que deja la ejecución `EN_PROCESO` hasta agotar sus intentos;
fallas inyectadas en siete puntos, dos workers, el lease perdido, el worker que no toma un ruteo sin
proveedor, el empate por `cuenta_id`, la reconstrucción idéntica, la inmutabilidad, y coordenadas
en la URL de un error del transporte que no aparecen en ninguna parte. Con `MC_OSRM_URL_PRUEBAS` y
`MC_RED_VIAL_PRUEBAS`, `tests/test_ruteo_v2_osrm.py` prueba contra OSRM de verdad, sobre la red
sintética: el snap, la matriz con su calle de un solo sentido y su tramo aislado, la matriz por
bloques, la ruta de OSRM contra sus tramos y una ejecución de punta a punta; el CI la prepara con
la imagen fijada y la sirve para eso. La política (`pytest tests/test_ruteo_v2_politica.py`) no
necesita base. Los benchmarks de `ruteo/v2` no corren en el CI.

`analytics/v1` se prueba contra PostgreSQL con un golden escrito a mano (`pytest
tests/test_analytics_golden.py`): veinte cuentas sintéticas construidas como lo haría la operación,
con dos fronteras de conocimiento, y lo publicado comparado métrica por métrica: los pagos (dos
observaciones idénticas, un reverso dentro del horizonte, reversos posteriores a `as_of` en la misma
ventana y en la siguiente, un posible reverso que deja una cuenta en negativo, un pago sin cuenta y
uno de otro corte), la llegada tardía, las tres anulaciones, la atribución sin elegir, los seis
estados de una promesa y el denominador de la tasa, NULL contra cero, el denominador cero, una celda
de 9 suprimida y una de 10 visible con su supresión complementaria, la cuenta con tres gestiones,
dos pagos y dos promesas que no se multiplican, y la cadena de campo y ruteo. Hay pruebas de cada
error del manifiesto, de la misma firma, de fallas inyectadas en ocho puntos, de un control que no
cuadra (con interpretaciones reales que no se corresponden), dos workers, el lease perdido, la
reconstrucción idéntica después de reescribir el orden físico de las tablas, la inmutabilidad y la
bitácora sin datos de una cuenta. El catálogo, la política, los periodos y la supresión (`pytest
tests/test_analytics_politica.py`) no necesitan base. El benchmark de `analytics/v1` no corre en
el CI.

`predictive/model-v1` se prueba contra PostgreSQL con un golden de fugas escrito a mano (`pytest
tests/test_predictivo_golden.py`): un pago del día T+1 que nunca es feature, un reverso posterior a
la ventana que no anula el label y uno dentro de ella que sí, el reverso de un pago anterior a la
ventana, un contacto registrado después de K, una evaluación de promesas terminada después de K, un
label censurado que queda NULL, la purga y cada error del manifiesto, y el `feature_hash` de
PostgreSQL contra el de Python cuenta por cuenta. Sobre un escenario generado con señal (`pytest
tests/test_predictivo_entrenamiento.py tests/test_predictivo_scoring.py`), un entrenamiento de punta a
punta, su baseline, la reconstrucción idéntica del entrenamiento y del scoring después de reescribir
el orden físico de las tablas, fallas inyectadas en cada punto, dos workers, el lease perdido, una
versión rechazada por sus compuertas, los rangos que la base rechaza, el hash de las predicciones,
el PSI de sus bins, la explicación verificada, la API y el CLI; y que un scoring `BACKTEST` del corte
de TEST reproduce exactamente sus métricas. Los GLM contra scikit-learn, el runtime del JSON, la
imputación, las categorías desconocidas, Platt, el hash del artefacto, las métricas y el PSI
(`pytest tests/test_predictivo_modelos.py tests/test_predictivo_definiciones.py
tests/test_predictivo_propiedades.py`) no necesitan base. El benchmark de `predictive/model-v1` no
corre en el CI.

`optimization/v1` se prueba sin base con el golden manual (diez cuentas, tres pools, un total, un
presupuesto y dos coberturas, cuyo óptimo se calcula a mano) y con propiedades contra una
enumeración independiente de cada asignación posible (`pytest tests/test_optimizacion_formulacion.py
tests/test_optimizacion_configuracion.py`): cada cuenta en un pool a lo más, ninguna capacidad, total,
presupuesto ni cobertura violados, el redondeo de las participaciones en sus fronteras, lo
infactible, lo no demostrado, el modelo reducido igual al completo, la monotonía del valor con más
capacidad, el plan independiente del orden de las entradas y los hashes de la configuración y de la
política. Contra PostgreSQL (`pytest tests/test_optimizacion_ejecuciones.py
tests/test_optimizacion_campo.py tests/test_api_optimizacion.py tests/test_cli_optimizacion.py`), el
golden en la base de punta a punta, la admisibilidad, la imputación, solo paradas `PROGRAMADA` y con
su propio gestor, lo que no se abre (cortes distintos, un ruteo de otra decisión), fallas inyectadas
en cada punto, dos workers, el lease perdido, la reconstrucción idéntica, la inmutabilidad, la
privacidad, la API y el CLI. El benchmark de `optimization/v1` no corre en el CI.

La app web se prueba en tres niveles. `app/v1` y la superficie web, con pytest
(`tests/test_api_app_v1.py`, `tests/test_superficie_web.py`): la compatibilidad de un contexto,
cada problema, las sugerencias, el resumen, el explorador (cada orden, sentido y filtro, página por
página, contra la lista entera filtrada y ordenada aparte), la Cuenta 360 y sus pestañas, el linaje,
cuántas consultas emite cada ruta, ningún dato personal en ningún esquema ni respuesta, la lista
blanca del proxy y el contrato de la web. Las pantallas, con Vitest, MSW y respuestas capturadas de
la API (`cd web && npm run test:cobertura`), con sus umbrales de cobertura. Y los flujos, con
Playwright y axe-core (`npm run e2e`), contra el preview o contra la app del compose.

## Verificación de tipos

`src/motor_cartera` se verifica con [Pyright](https://github.com/microsoft/pyright) en modo
`standard`, sin carpetas excluidas: 219 archivos, 0 errores y 0 advertencias. Los 13 módulos que ya
cumplen el modo `strict` lo tienen activado, para que no retrocedan. La configuración está en
`[tool.pyright]` del `pyproject.toml`, y el CI la corre en el trabajo `pruebas`:

```bash
uv sync --extra dev
PYRIGHT_PYTHON_IGNORE_WARNINGS=1 uv run pyright
```

`pyright` es el paquete de PyPI fijado en el `uv.lock`: trae el motor de Pyright y lo corre con el
Node del sistema, y con `PYRIGHT_PYTHON_IGNORE_WARNINGS=1` no consulta PyPI por una versión más
nueva. `pandas-stubs` y `scipy-stubs` (de la misma versión que SciPy) dan los tipos que esas
bibliotecas no traen completos. Nada de esto se instala en las imágenes del servicio.

Lo que lo mantiene en cero sin silenciarlo:

- **Las columnas de SQLModel van con `col()`.** SQLModel tipa cada campo con su tipo de Python, así
  que `Modelo.campo == x` o `Modelo.campo.desc()` no son, para el verificador, expresiones SQL;
  `col(Modelo.campo)` sí, y en ejecución es el mismo objeto.
- **`id_de(fila)`** (`db/modelos.py`) da el `id` de una fila ya persistida, que el modelo declara
  `int | None`.
- **`presente(valor, que)`** (`invariantes.py`) nombra lo que el código ya garantiza y el
  verificador no ve: una columna que admite NULL y que una restricción de la base exige en ese
  estado, o lo que solo existe en un plan óptimo. Si falta, es un error del código
  (`RuntimeError`), no un `None` que sigue de largo.
- **`seleccionar`, `con_columnas`, `filas_afectadas` y `reloj_de_la_base`** (`db/sesion.py`) dan su
  tipo a lo que SQLAlchemy no declara: un `select()` de más de cuatro columnas, `add_columns`, el
  `rowcount` de una escritura y `now()` como valor de una columna.
- **`pyarrow.compute` va sin tipo estático** (`pc: Any`) en los generadores: sus funciones se arman
  al importar el módulo, desde el registro de C++, y un verificador no las ve.

En `src` hay dos `# pyright: ignore`, cada uno con su regla y un comentario: el `__tablename__` de la
base común de las tablas (`db/modelos.py`) y la `Config` del esquema de pandera
(`contratos/cartera.py`). Los siete `# type: ignore` restantes son anteriores y locales, cada uno con
su código: cinco anotaciones con modelos de pydantic que se arman en ejecución, un `asdict` y un
atributo marcador de un manejador de logging.

**Lo que falta para `strict` en todo `src`.** Medido con la misma versión de Pyright: 5,891 errores
en 157 de los 219 archivos. Casi todos son tipos desconocidos que vienen de bibliotecas sin tipos
completos o de anotaciones genéricas sin sus argumentos (`dict`, `list`, `np.ndarray`):
`reportUnknownMemberType` (1,616), `reportUnknownArgumentType` (1,196), `reportUnknownVariableType`
(1,015), `reportArgumentType` (777), `reportUnknownParameterType` (386), `reportMissingTypeArgument`
(330) y `reportDeprecated` (289). Las validaciones defensivas de las reglas puras (un `isinstance`
sobre una entrada que el tipo ya declara) también fallan en `strict` y se quedan: esas entradas
llegan de fuera. Pasar a `strict` es un trabajo módulo por módulo, que se suma a la lista de
`[tool.pyright]` cuando un módulo queda limpio.

## El CI

`.github/workflows/ci.yml` corre en cada pull request y en cada push a `main`, con permisos de solo
lectura, las acciones fijadas por el SHA de su commit y cada checkout sin la credencial en el disco.
Sus cuatro trabajos son los checks obligatorios de `main`:

- **`archivos`**: lo trackeado (`scripts/verificar_archivos_trackeados.py`), la cadena de suministro
  fijada (`scripts/verificar_cadena_de_suministro.py`), gitleaks sobre el historial completo, Trivy
  sobre los lockfiles y la referencia de Google Cloud en OpenTofu (`fmt`, `init`, `validate` y el
  plan de sus dos entornos, sin credenciales ni estado, con la política de
  `scripts/verificar_iac.py`).
- **`pruebas`**: las dependencias exactas del `uv.lock` y de `web/package-lock.json`, pip-audit y
  npm audit, el contrato de la web regenerado (falla si difiere), el lint, el formato, los tipos,
  Vitest con cobertura, el build y los presupuestos del bundle de la app; ruff, Pyright, las
  migraciones (suben, coinciden con los modelos y bajan), una red vial sintética servida por OSRM y
  la suite completa de pytest con cobertura contra PostgreSQL con PostGIS.
- **`compose`**: el `docker compose up --build` de desarrollo en un runner limpio y la prueba de
  humo de punta a punta por HTTP: la cartera sintética por el flujo automático, la cartera oficial
  y sus pagos, un escenario de cuatro cortes, la historia, el motor de pagos, el lifecycle y la
  atribución, `decision/v2` (y su comparación en sombra con `decision/v1`), la geografía, las
  bases, `territorial/v2`, `campo/v2`, `ruteo/v2` sobre la red sintética, `analytics/v1`, un
  modelo predictivo pequeño con su scoring y su drift, y `optimization/v1`; los backfills que no
  deben encontrar nada pendiente, la prueba de humo `--web`, Playwright con axe-core contra la app
  del compose, y el almacén de artefactos que sobrevive a bajar y volver a subir los contenedores.
- **`produccion`**: el respaldo del trabajo anterior restaurado en un entorno nuevo, el compose de
  producción completo (TLS, OpenID Connect con Dex, permisos), las pruebas de humo del edge, la
  interna y la de los contenedores endurecidos, Playwright con los usuarios de Dex, los simulacros
  de falla, una carga corta con k6, su propio respaldo, Trivy sobre las imágenes propias con su
  SBOM CycloneDX y el informe de las de terceros.

`.github/workflows/codeql.yml` corre el análisis estático de CodeQL sobre Python y
JavaScript/TypeScript en cada pull request, en cada push a `main` y cada semana.

`.github/workflows/compatibilidad.yml` es la matriz ligera de Python: en 3.12, 3.13 y 3.14 (las de
`requires-python`; `tests/test_compatibilidad.py` exige que coincidan), el entorno exacto del
`uv.lock`, la compilación, cada módulo del paquete importado, el contrato de la app web idéntico al
del repositorio, la línea de comandos (su ayuda y una cartera oficial sintética) y las pruebas puras
del núcleo de cada motor, sin PostgreSQL, Docker ni OSRM; al final, el OpenAPI completo de las tres
tiene que ser el mismo, byte por byte. Corre en cada pull request, en cada push a `main` y cada
semana, y no repite el CI: lo que necesita infraestructura corre solo en 3.12, en `ci.yml`. Sin una
base, las pruebas puras corren igual en local:

```bash
MC_DATABASE_URL_PRUEBAS=postgresql+psycopg://sin-base@127.0.0.1:9/sin_base_test \
  pytest tests/test_compatibilidad.py tests/test_decision_reglas.py tests/test_motor_pagos_reglas.py
```

`.github/workflows/release.yml` publica una versión, solo a mano y solo después de revisar sus
precondiciones; cómo, en [cadena-de-suministro.md](cadena-de-suministro.md#las-releases).
`.github/workflows/performance.yml` mide cada semana el perfil M contra su línea base: una señal que
no detiene nada ([rendimiento.md](rendimiento.md)). Los benchmarks de escala son un workflow aparte,
que solo corre a mano, y los demás se corren con su script sobre una base `*_bench` que ya tiene el
XL; ninguno es parte del CI.

## Validación local en Windows

El CI prueba los dos compose en Linux. En la máquina de desarrollo (Windows 11, 16 GB de RAM, Docker
Desktop 29.8 con Compose v5.6 sobre WSL2) se verificó, el 2026-10-21, con el código candidato de
v1.0.0 (el del PR #38), en un clon limpio del repositorio y con nombres de proyecto propios, sin
tocar los volúmenes de desarrollo:

- **una instalación limpia del compose de desarrollo**: `docker compose up --build`, las
  migraciones desde cero, los datos sintéticos y sus tres pruebas de humo (de punta a punta por la
  API, 302 comprobaciones; predictivo y optimización, 60; la superficie web, 63); Playwright con
  axe-core contra la app de esa imagen (55 pruebas; una, la paginación del explorador, agotó su
  tiempo una vez y pasó en las tres repeticiones siguientes), y las capturas del README con
  `web/scripts/capturas-readme.mjs`, con el Chrome del sistema;
- **ninguna de las 32 pantallas de la app desborda** a 320 ni a 390 px, medidas en Chrome;
- **el respaldo, la destrucción y la restauración**: el respaldo del compose de desarrollo después de
  sus pruebas de humo (83 tablas, 156,297 filas), ese entorno destruido con sus volúmenes y, en un
  compose de producción nuevo (sin los perfiles `ruteo` ni `observabilidad`): los secretos
  efímeros, las imágenes, el simulacro de restauración (los cinco respaldos alterados, negados con
  su código; la restauración, `VALIDADA` con sus 97 artefactos en 11.9 s), el esquema en
  `0017 (head)` y sin diferencias con los modelos, los 97 artefactos intactos, la prueba de humo del
  edge (TLS 1.3, el inicio de sesión con PKCE, la cookie `__Host-`, la fijación de sesión, el
  callback repetido, las cabeceras de identidad del cliente que no elevan a nadie y el cierre de
  sesión con su revocación: 81 comprobaciones), la interna (26, con OSRM y la observabilidad no
  verificables sin sus perfiles), la de los contenedores endurecidos (77; Windows no controla los
  permisos de un secreto montado) y Playwright por el edge con los usuarios de Dex (62 pruebas).

No se corrieron aquí los simulacros de falla ni la carga: los corre el job `produccion` del CI en
cada run. macOS no está probado.

En una máquina de 16 GB con otros procesos, la VM de WSL crece con la caché de páginas de
PostgreSQL y de las construcciones de imágenes; vaciarla
(`docker run --rm --privileged alpine sh -c "echo 3 > /proc/sys/vm/drop_caches"`) le devuelve la
memoria a Windows. La suite completa de pytest no se pudo correr aquí sin bajar del margen de
memoria que se fijó para la máquina; se corre entera en el CI.
