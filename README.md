# motor-cartera

Motor de ingesta, validación, segmentación, decisión, priorización territorial y ruteo sintético
de cartera de crédito al consumo, construido sobre **datos sintéticos**, con una API REST para
operarlo y un worker que ejecuta cada etapa desde una cola durable.

## De qué se trata

Una cartera de crédito llega en archivos de sistemas distintos, con encabezados que cambian
sin aviso, duplicados, saldos imposibles y claves geográficas incompletas. Sobre esos
archivos alguien tiene que decidir a quién contactar, por qué canal y en qué orden. Si el
dato entra mal, la decisión sale mal, y la decisión se ejecuta.

Este proyecto **lee, valida contra un contrato, persiste con trazabilidad y resume** la
cartera. La regla de diseño es *fail-closed*: nada entra sin pasar el contrato, y lo que no
entra dice por qué. Cada registro se juzga por separado y el que no cumple se rechaza con
su motivo. Si los rechazos pasan de una tolerancia, el archivo entero se rechaza y no se
publica nada; tampoco si trae más de una fecha de corte, porque una cartera es la foto de un
día. Es preferible no producir salida a producir salida incorrecta.

Sobre la cartera ya publicada, el **Decision Engine** decide cada cuenta con reglas explícitas y
versionadas: en qué segmento de mora está, qué prioridad tiene, por qué canal conviene
gestionarla y por qué. Publica las decisiones de toda la corrida o ninguna, y cada una se puede
volver a explicar.

Sobre esas decisiones, el **Motor Territorial** organiza el trabajo de campo por municipio:
cuántas cuentas tienen `CAMPO` como canal recomendado en cada uno, cuánta carga operativa
concentra y qué municipios conviene atender primero. Prioriza territorios; no traza rutas.

Dentro de cada municipio con trabajo de campo, el **Motor de Ruteo** decide en qué secuencia
visitar esas cuentas: una ruta que sale de un depósito, pasa una vez por cada cuenta y regresa,
construida por vecino más cercano y mejorada con 2-opt. **Las rutas son sintéticas**: cada cuenta
recibe un punto determinista en un plano local del municipio, porque la cartera no trae
ubicaciones y aquí no se inventan. No son calles, domicilios ni tiempos de conducción.

Las cuatro etapas se encadenan solas. Un solo `POST /corridas` deja la cartera y el trabajo de su
ingesta en una **cola durable sobre PostgreSQL** y responde de inmediato. Un **worker**,
independiente de la API, toma cada trabajo, ejecuta su motor y deja en la cola la etapa siguiente,
de la ingesta al ruteo. Si un worker muere a la mitad, su trabajo no se pierde: cuando vence su
lease, otro lo retoma.

Desde v0.6.0, el sistema recibe las **fuentes oficiales** del acreedor tal como llegan: la cartera
completa (`cartera/v2`, sus 93 columnas, con su hoja compañera CARRIER) y los pagos (`pagos/v1`,
sus 23 columnas, un movimiento por fila). Cada archivo se guarda entero y para siempre en un
**almacén por contenido**, donde su SHA-256 es su identidad, y sus registros válidos quedan en un
**dataset conformado** en Parquet, cada uno atado a su fila de origen. La cartera mínima de
siempre, `cartera/v1`, sigue igual. Todo está en [docs/fuentes.md](docs/fuentes.md).

Desde v0.7.0, el sistema entiende además **el tiempo**. Cada dataset conformado se materializa, en
paralelo al flujo operacional y sin volver a leer el archivo original, en un **modelo histórico**:
una **cuenta canónica** por `CLIENTE_UNICO` de la cartera, un **corte canónico** por fecha, un
**snapshot** de cada cuenta en cada corte y cada movimiento de pagos como un **pago observado**. La
**Cuenta 360** responde qué le pasó a una cuenta a través de sus cortes: cuándo se observó por
primera vez, si salió o reingresó, cómo cambiaron su saldo y su atraso, qué pagos se observaron y de
qué archivo y de qué fila salió cada dato. No pretende saber todavía por qué pasó, ni qué pago es
económicamente válido. Todo está en [docs/historia.md](docs/historia.md) y
[docs/cuenta_360.md](docs/cuenta_360.md).

Desde v0.8.0, los pagos observados se **interpretan** sin tocarlos: el motor de pagos decide qué
hechos económicos distintos representan, cuáles son copias, cuáles revierten a otro y a qué cuenta
pertenecen ([docs/motor_pagos.md](docs/motor_pagos.md)). Y desde v0.9.0, el sistema sabe además
**qué hizo la cobranza** con cada cuenta: gestiones, contactos, visitas, promesas y convenios, como
eventos operacionales con su momento de negocio y su momento de registro, que solo se agregan. Una
promesa se evalúa a una fecha de corte explícita, y cada pago interpretado se asocia con las
gestiones con contacto que lo antecedieron: **asociación operacional, no causalidad**. Todo está en
[docs/lifecycle.md](docs/lifecycle.md) y [docs/atribucion.md](docs/atribucion.md).

## Datos

**Ningún dato real entra a este repositorio.** Todo lo que el sistema procesa lo produce el
generador sintético incluido. Las claves geográficas son claves reales del Marco
Geoestadístico del INEGI (público) de cinco entidades, con casi todas las cuentas en
Puebla; el resto son datos inventados con distribuciones parecidas a las reales: saldos
con cola larga, atraso por tramos y concentración geográfica desigual. El generador mete filas inválidas a
propósito para que el contrato tenga de dónde agarrarse.

El `.gitignore` lo cuida por nombre, pero solo frena lo que todavía no está trackeado. Lo
trackeado lo revisa `scripts/verificar_archivos_trackeados.py` en el CI: falla si entró algo
que el `.gitignore` excluye (con `git add -f`, o en mayúsculas donde git las distingue) o, con
cualquier nombre, una hoja de cálculo, un zip, un Parquet o un Arrow, un archivo comprimido (gzip,
bzip2, xz, zstd, 7z, rar), un volcado de `pg_dump` o una base SQLite, que reconoce por sus primeros
bytes. Antes de un commit también se corre a mano:

```bash
python scripts/verificar_archivos_trackeados.py
```

## Estado

Hay nueve versiones terminadas, y cada una es una rebanada vertical que funciona de punta a
punta:

- **v0.1.0 ✅ Ingesta + certificación** (la fase 1): una cartera se publica solo si pasa el
  contrato, y lo que no pasa queda con su motivo.
- **v0.2.0 ✅ Decision Engine**: sobre la cartera publicada, una decisión explicable por cuenta.
- **v0.3.0 ✅ Motor Territorial**: sobre las decisiones publicadas, la carga de campo de cada
  municipio y el orden en que conviene atenderlos.
- **v0.4.0 ✅ Motor de Ruteo**: dentro de cada municipio con trabajo de campo, la secuencia en que
  conviene visitar sus cuentas, sobre coordenadas sintéticas y deterministas.
- **v0.5.0 ✅ Orquestación Durable**: la API registra el trabajo y un worker independiente lo
  ejecuta desde una cola sobre PostgreSQL, con lease, latido y reintentos acotados. Una subida
  recorre sola la ingesta, la decisión, la organización territorial y el ruteo.
- **v0.6.0 ✅ Fuentes Oficiales, Evidencia Inmutable y Escala**: la cartera oficial (`cartera/v2`,
  93 columnas) y los pagos (`pagos/v1`, 23 columnas) entran con estructura exacta; cada archivo
  original se conserva en un almacén por contenido, con su dataset conformado y su linaje; y la
  ingesta procesa 500,000 cuentas (el escenario empresarial objetivo) con memoria acotada.
- **v0.7.0 ✅ Modelo Histórico y Cuenta 360**: cada cartera oficial y cada archivo de pagos se
  materializan, en paralelo al flujo y desde su dataset conformado, en una historia longitudinal:
  una identidad canónica por cuenta, un corte por fecha, snapshots inmutables y pagos observados
  sin deduplicar. La Cuenta 360 la consulta por la API, y un backfill construye la de lo publicado
  antes.
- **v0.8.0 ✅ Motor de Pagos Canónico y Conciliación**: los pagos observados se interpretan, sin
  tocarlos, en movimientos económicos canónicos: un duplicado exacto cuenta una vez, una
  coincidencia de la llave histórica queda a la vista sin fusionarse, un reverso solo anula a su
  pago cuando la pareja es inequívoca, y cada movimiento se concilia con su cuenta, con su contexto
  entre los snapshots y la recuperación bruta y neta interpretadas. Cada conclusión se explica
  hasta el archivo y la fila que la justifican.
- **v0.9.0 ✅ Lifecycle de Cobranza y Atribución Operativa**: lo que la cobranza hizo con cada
  cuenta (gestiones, contactos, visitas, promesas, convenios, cancelaciones y anulaciones) se
  registra como eventos operacionales que solo se agregan, con su momento de negocio y su momento
  de registro y una llave de idempotencia garantizada por PostgreSQL; las promesas se evalúan a una
  fecha de corte explícita, y cada pago interpretado se asocia con las gestiones con contacto que lo
  antecedieron, sin elegir entre varias y sin afirmar causalidad.

Lo que ya hace:

- [x] Contrato de datos *fail-closed*, registro por registro, con el motivo de cada rechazo
- [x] Generador de cartera sintética en xlsx, csv y zip, con filas inválidas a propósito
- [x] Lectores de Excel, CSV y ZIP con detección flexible de columnas
- [x] Persistencia con trazabilidad por corrida, incluidas la versión del contrato con que se
  juzgó y la firma de su contenido; esquema versionado con Alembic
- [x] API REST: corridas, rechazos, resumen segmentado y salud, con OpenAPI
- [x] Decision Engine (`decision/v1`): asigna a cada cuenta un segmento, una prioridad y un
  canal recomendado, y explica cada decisión con sus motivos
- [x] Ejecuciones de decisión persistidas y auditables: todas las decisiones de una corrida o
  ninguna, una sola vez por versión de las reglas, con su historial por la API
- [x] Motor Territorial (`territorial/v1`): agrupa por municipio las decisiones de una ejecución,
  asigna a cada municipio una carga de campo y un lugar en el orden de atención, y explica cada
  uno con su motivo
- [x] Ejecuciones territoriales persistidas y auditables: agregadas en PostgreSQL, todos los
  municipios o ninguno, una sola vez por versión de las reglas, con su historial por la API
- [x] Motor de Ruteo (`ruteo/v1`): una ruta por municipio con trabajo de campo, sobre un plano
  sintético por municipio, con distancia Manhattan, vecino más cercano y hasta 10 mejoras 2-opt
- [x] Ejecuciones de ruteo persistidas y auditables: todas las rutas y paradas o ninguna, una sola
  vez por versión de las reglas, con su historial, sus rutas y sus paradas por la API
- [x] Cola durable sobre PostgreSQL: cada trabajo nace en la misma transacción que su recurso, se
  reparte con `FOR UPDATE SKIP LOCKED` y, si su worker muere, otro lo retoma cuando vence el lease;
  reintentos con espera e intentos acotados
- [x] Worker independiente de la API (`motor-cartera worker`), con apagado ordenado; se pueden
  correr varios a la vez
- [x] Flujo automático: un `POST /corridas` llega hasta el ruteo; el flujo y sus trabajos se
  consultan por la API, y una etapa que falló se reanuda
- [x] API asíncrona: cada `POST` que pide trabajo responde `201` con el recurso `EN_PROCESO`
- [x] `docker compose up` levanta todo, con el worker en su propio contenedor; CI con PostgreSQL y
  la prueba de humo del flujo automático en un compose limpio
- [x] Contratos de las fuentes oficiales: `cartera/v2` (93 columnas) y `pagos/v1` (23 columnas),
  con estructura exacta, sin descartes silenciosos; `cartera/v1` congelado y compatible
- [x] Almacén de artefactos por contenido (SHA-256): cada archivo original se guarda una vez, de
  forma atómica, y no se borra nunca; en el compose, en un volumen propio
- [x] Dataset conformado en Parquet, reproducible byte por byte, con la fila de origen de cada
  registro, y linaje del original al conformado y a la corrida
- [x] Proyección operacional explícita de `cartera/v2` a `Cuenta`, con el catálogo público del
  INEGI: la cartera oficial llega por el flujo hasta el ruteo
- [x] CARRIER reconocida y auditada como hoja compañera, sin bloquear CARTERA
- [x] Ingesta de pagos con su propia entidad y su trabajo durable, sin deduplicar movimientos
- [x] Detección de formato por contenido, no solo por la extensión
- [x] Generador de CARTERA, CARRIER y PAGOS; perfiles de escala de XS a XXL; escenario
  longitudinal de varios cortes, determinista por semilla
- [x] Benchmark de escala fuera del CI: XL (500,000 cuentas) medido de punta a punta, sin OOM
- [x] Modelo histórico (`historia/v1`): `CuentaCanonica` por despacho, cartera y `CLIENTE_UNICO`,
  sin persona ni crédito inventados; un `CorteCanonico` por fecha, donde una fuente equivalente no
  duplica nada y una conflictiva no sobrescribe la historia; `SnapshotCuenta` estrecho, inmutable y
  con su fila de origen; un `PagoObservado` por fila de pagos/v1, sin deduplicar
- [x] Materialización durable y paralela al flujo (trabajo `HISTORIA`), abierta en la misma
  transacción que el dataset, solo desde el Parquet conformado, todo o nada, por lotes con `COPY`,
  con identificadores públicos deterministas e independiente del orden de llegada de los cortes
- [x] Cuenta 360 por la API: búsqueda por `CLIENTE_UNICO`, resumen (también como se veía en una
  fecha), historia con continuidad y deltas, eventos de presencia y pagos observados, paginados; y
  los cortes canónicos y las ejecuciones históricas, con su evidencia
- [x] `motor-cartera backfill-historia`, idempotente, para lo publicado antes de v0.7.0, y un
  benchmark histórico fuera del CI: 12 cortes XL medidos, con el plan de cada consulta
- [x] Motor de Pagos (`motor-pagos/v1`): una interpretación versionada por ventana (un mes de
  recepción), con un resultado explicado por cada pago observado y un movimiento económico
  canónico por cada hecho distinto, con identificador determinista, sin modificar ni borrar ningún
  `PagoObservado`
- [x] Firma exacta y llave histórica, con comparación campo por campo antes de contar copias:
  duplicados exactos que cuentan una vez, coincidencias ambiguas que no se fusionan, reversos solo
  como pareja aislada y posibles reversos sin forzar
- [x] Conciliación con `CuentaCanonica` en otro eje y versionada, contexto temporal entre
  snapshots, y recuperación bruta y neta interpretadas, que no pretenden ser un ledger
- [x] Interpretación por conjuntos en PostgreSQL, todo o nada, verificada contra un núcleo puro;
  trabajo durable `MOTOR_PAGOS` que abre la historia de los pagos y que solo publica el dueño
  vigente de su trabajo; `motor-cartera backfill-motor-pagos`, idempotente, por ventanas
- [x] API del motor: `/motor-pagos`, `/movimientos` con su evidencia y
  `/cuentas/{cuenta_id}/movimientos`, que la Cuenta 360 distingue de `/pagos-observados`; y un
  benchmark de 12 cortes XL fuera del CI, con el plan de cada consulta
- [x] Lifecycle de cobranza (`lifecycle/v1`): `EventoLifecycle` con `ocurrido_en` y `registrado_en`,
  gestiones con canal, medio, nivel de contacto y resultado coherentes (en Python y en la base),
  visitas de campo, promesas y convenios con sus cuotas declaradas; solo se agrega (un trigger
  rechaza `UPDATE` y `DELETE`), y se corrige con anulaciones y cancelaciones que son eventos
- [x] Escrituras individuales por la API con `Idempotency-Key` obligatoria: `201`, `200` con
  `Idempotent-Replayed` o `409`, garantizado por un índice único; y la línea de tiempo de una cuenta
  en tres dominios (`OPERACIONAL`, `FUENTE_CORTE`, `ECONOMICO`), sin fabricar eventos de un snapshot
- [x] Evaluación de promesas (`evaluacion-promesa/v1`) a una fecha de corte explícita, por cartera,
  en un trabajo durable `EVALUACION_PROMESAS`, por conjuntos y verificada contra un núcleo puro
- [x] Atribución operativa (`atribucion/v1`): por ventana, sobre la interpretación vigente de los
  pagos, con candidatas en una relación, `AMBIGUA` sin elegir y una ventana que es parámetro de cada
  ejecución; trabajo durable `ATRIBUCION` y `motor-cartera backfill-atribucion`
- [x] `motor-cartera cargar-lifecycle` (JSONL por COPY y tablas temporales, todo o nada e
  idempotente), `generar-escenario --lifecycle` determinista por semilla, `backfill-lifecycle`, la
  Cuenta 360 con su `lifecycle_resumen`, y un benchmark del lifecycle sobre el XL fuera del CI

La ruta completa, versión por versión (✅ publicada; ➡️ la que sigue):

```text
✅ v0.1 Ingesta + contratos
✅ v0.2 Decision Engine v1
✅ v0.3 Territorial v1
✅ v0.4 Ruteo sintético v1
✅ v0.5 Orquestación durable
✅ v0.6 Fuentes oficiales + evidencia + escala
✅ v0.7 Modelo histórico + Cuenta 360
✅ v0.8.0 — Motor de Pagos Canónico y Conciliación
✅ v0.9.0 — Lifecycle de Cobranza y Atribución Operativa
➡️ v0.10 — Decision Engine v2

v0.11 Geografía real + Territorial v2
v0.12 Campo v2
v0.13 Ruteo vial v2
v0.14 Collection Analytics
v0.15 Predictive Intelligence
v0.16 Optimización Matemática
v0.17 Aplicación + dashboards
v0.18 Cloud + observabilidad + hardening

v1.0 Collection Intelligence Platform
```

Lo que sigue es **v0.10, el Decision Engine v2**: decidir cada cuenta también con lo que la
cobranza ya hizo con ella (días desde la última gestión, intentos y contactos recientes, promesas y
su cumplimiento, recuperación observada después de una gestión, canal, visitas y convenios), que v0.9
deja como verdad operacional consultable. Sin adelantar la geografía real, la analítica ni los
modelos predictivos.

Lo que está frágil o pendiente, sin maquillar, está en
[Limitaciones conocidas](#limitaciones-conocidas).

## Arranque rápido

```bash
docker compose up --build
```

Eso levanta PostgreSQL, aplica las migraciones y arranca la API en <http://localhost:8000> y el
worker, cada uno en su contenedor, sin pasos manuales. La documentación interactiva está en
<http://localhost:8000/docs>. La clave de desarrollo es `clave-local-de-desarrollo`; se cambia
con la variable `MC_API_KEY`. Para correr varios workers: `docker compose up --scale worker=3`.
Los archivos recibidos se guardan en el volumen `fuentes`, aparte del de PostgreSQL: sobreviven a
un `docker compose down` (sin `-v`, que borra los volúmenes).

## El flujo completo

**1. Generar una cartera sintética.** El archivo queda en `./datos`, que el contenedor
comparte con tu máquina:

```bash
docker compose exec api motor-cartera generar --destino datos/cartera.xlsx
```

Por omisión son 10,000 cuentas con 2 % de filas inválidas a propósito (`--n`,
`--tasa-invalidas`, `--semilla`, `--fecha-corte`). El formato sale de la extensión.

**2. Subirla por la API.**

```bash
curl -i -H "X-API-Key: clave-local-de-desarrollo" -F "archivo=@datos/cartera.xlsx" http://localhost:8000/corridas
```

Responde `201` de inmediato, con la corrida `EN_PROCESO` y su dirección en `Location`. La API no
juzga el archivo: lo copia por bloques al almacén de artefactos y registra, en una sola
transacción, su artefacto, la corrida, su flujo automático y el trabajo de la ingesta; el worker
hace lo demás. Sin el campo `contrato`, el archivo es de `cartera/v1`, como siempre; una cartera
oficial se sube con `contrato=cartera/v2` y su `fecha_corte` (ver
[Fuentes oficiales](#fuentes-oficiales)).

**3. Seguir su flujo** hasta que deje de estar `EN_PROCESO`:

```bash
curl -H "X-API-Key: clave-local-de-desarrollo" http://localhost:8000/corridas/<run_id>/flujo
```

```json
{
  "flujo_id": "…",
  "run_id": "…",
  "estado": "COMPLETADO",
  "etapa": "COMPLETADA",
  "decision_run_id": "…",
  "territorial_run_id": "…",
  "ruteo_run_id": "…",
  "duracion_segundos": 4.68,
  "detalle": "La ingesta, la decision, la organizacion territorial y el ruteo terminaron EXITOSA."
}
```

Mientras avanza, `etapa` dice en cuál va (`INGESTA`, `DECISION`, `TERRITORIAL` o `RUTEO`) y
`detalle`, qué espera; el identificador de cada ejecución aparece en cuanto el flujo llega a su
etapa. `COMPLETADO` quiere decir que el ruteo terminó `EXITOSA`. `DETENIDO` quiere decir que una
etapa no pudo continuar: `etapa` dice cuál, y `detalle`, por qué y cómo reintentar (ver
[Si el flujo se detiene](#si-el-flujo-se-detiene)).

**4. Ver los trabajos que lo ejecutaron**, uno por etapa, en el orden en que entraron a la cola:

```bash
curl -H "X-API-Key: clave-local-de-desarrollo" http://localhost:8000/flujos/<flujo_id>/trabajos
```

```json
{
  "flujo_id": "…", "run_id": "…", "total": 4, "pagina": 1, "por_pagina": 50,
  "elementos": [
    {
      "trabajo_id": "…",
      "tipo": "INGESTA",
      "estado": "COMPLETADO",
      "objetivo_run_id": "…",
      "intentos": 1,
      "max_intentos": 5,
      "lease_hasta": null,
      "ultimo_error": null,
      "…": "…"
    }
  ]
}
```

El estado de un trabajo es el de su entrega, no el de su motor: `COMPLETADO` quiere decir que su
recurso terminó, `EXITOSA` o no. `objetivo_run_id` es el identificador público de ese recurso, y
`GET /trabajos/{trabajo_id}` consulta un trabajo solo.

**5. Consultar la corrida**: estado, conteos y tiempos.

```bash
curl -H "X-API-Key: clave-local-de-desarrollo" http://localhost:8000/corridas/<run_id>
```

```json
{
  "run_id": "…",
  "estado": "EXITOSA",
  "firma_contenido": "…",
  "filas_leidas": 10000,
  "filas_validas": 9800,
  "filas_rechazadas": 200,
  "version_contrato": "cartera/v1",
  "duracion_segundos": 2.82,
  "detalle": "Se publicaron 9,800 cuentas; 200 registros (2.0%) se rechazaron, dentro de la tolerancia de 5.0%. Origen: hoja 'cartera' de 'cartera.xlsx'. …"
}
```

**6. Ver los rechazos, cada uno con su fila y su motivo:**

```bash
curl -H "X-API-Key: clave-local-de-desarrollo" "http://localhost:8000/corridas/<run_id>/rechazos?por_pagina=2"
```

```json
{
  "total": 200, "pagina": 1, "por_pagina": 2,
  "elementos": [
    {
      "fila": 67,
      "valores": {"cliente_unico": "CU…", "saldo_total": "-258422.11", "…": "…"},
      "motivos": [{"campo": "saldo_total", "regla": "greater_than_or_equal_to(0)"}]
    }
  ]
}
```

**7. Consultar el resumen segmentado** de la cartera vigente:

```bash
curl -H "X-API-Key: clave-local-de-desarrollo" "http://localhost:8000/cartera/resumen?por=canal&por=tramo_atraso"
```

Devuelve cuentas, saldo y saldo promedio por segmento, junto con el `run_id` del que sale
cada número.

**8. Consultar la decisión** que pidió el flujo, con el `decision_run_id` del paso 3: versión de
las reglas, estado, tiempos y conteos.

```bash
curl -H "X-API-Key: clave-local-de-desarrollo" http://localhost:8000/decisiones/<decision_run_id>
```

```json
{
  "decision_run_id": "…",
  "run_id": "…",
  "version_reglas": "decision/v1",
  "estado": "EXITOSA",
  "cuentas_evaluadas": 9800,
  "cuentas_decididas": 9800,
  "detalle": "Se decidieron 9,800 cuentas con decision/v1."
}
```

**9. Listar el historial de la corrida:** todas sus ejecuciones, en cualquier estado y versión
de las reglas, de la más reciente a la más antigua.

```bash
curl -H "X-API-Key: clave-local-de-desarrollo" http://localhost:8000/corridas/<run_id>/decisiones
```

**10. Ver la decisión de cada cuenta**, con sus motivos:

```bash
curl -H "X-API-Key: clave-local-de-desarrollo" "http://localhost:8000/decisiones/<decision_run_id>/cuentas?por_pagina=2"
```

```json
{
  "decision_run_id": "…", "run_id": "…", "version_reglas": "decision/v1", "estado": "EXITOSA",
  "total": 9800, "pagina": 1, "por_pagina": 2,
  "elementos": [
    {
      "cliente_unico": "CU…",
      "segmento": "MORA_MEDIA",
      "prioridad": "MUY_ALTA",
      "canal_recomendado": "CAMPO",
      "motivos": [
        {"codigo": "MORA_31_90", "campo": "dias_atraso", "valor": "65"},
        {"codigo": "SALDO_ALTO", "campo": "saldo_total", "valor": "62000.00"},
        {"codigo": "PRIORIDAD_MUY_ALTA", "campo": "prioridad", "valor": "MUY_ALTA"},
        {"codigo": "CANAL_CAMPO", "campo": "canal_recomendado", "valor": "CAMPO"}
      ]
    }
  ]
}
```

**11. Consultar la ejecución territorial** que pidió el flujo, con el `territorial_run_id` del
paso 3, el `decision_run_id` de las decisiones que organizó y el `run_id` de su corrida:

```bash
curl -H "X-API-Key: clave-local-de-desarrollo" http://localhost:8000/territoriales/<territorial_run_id>
```

```json
{
  "territorial_run_id": "…",
  "decision_run_id": "…",
  "run_id": "…",
  "version_reglas": "territorial/v1",
  "estado": "EXITOSA",
  "territorios_evaluados": 541,
  "territorios_publicados": 541,
  "detalle": "Se organizaron 9,800 decisiones en 541 municipios con territorial/v1."
}
```

**12. Listar el historial territorial de esas decisiones:** todas sus ejecuciones, en cualquier
estado y versión de las reglas, de la más reciente a la más antigua.

```bash
curl -H "X-API-Key: clave-local-de-desarrollo" http://localhost:8000/decisiones/<decision_run_id>/territoriales
```

**13. Ver cada municipio**, en el orden en que conviene atenderlos, con su carga y su motivo:

```bash
curl -H "X-API-Key: clave-local-de-desarrollo" "http://localhost:8000/territoriales/<territorial_run_id>/municipios?por_pagina=1"
```

```json
{
  "territorial_run_id": "…", "decision_run_id": "…", "run_id": "…",
  "version_reglas": "territorial/v1", "estado": "EXITOSA",
  "total": 541, "pagina": 1, "por_pagina": 1,
  "elementos": [
    {
      "clave_territorio": "21074",
      "cve_entidad": "21",
      "cve_municipio": "074",
      "cuentas_total": 1414,
      "saldo_total": "48455164.68",
      "cuentas_campo": 425,
      "saldo_campo": "22464492.42",
      "carga": "ALTA",
      "posicion_campo": 1,
      "motivos": [{"codigo": "CARGA_CAMPO_20_MAS", "campo": "cuentas_campo", "valor": "425"}]
    }
  ]
}
```

**14. Consultar la ejecución de ruteo** que pidió el flujo, con el `ruteo_run_id` del paso 3 y
los identificadores públicos de toda su cadena:

```bash
curl -H "X-API-Key: clave-local-de-desarrollo" http://localhost:8000/ruteos/<ruteo_run_id>
```

```json
{
  "ruteo_run_id": "…",
  "territorial_run_id": "…",
  "decision_run_id": "…",
  "run_id": "…",
  "version_reglas": "ruteo/v1",
  "estado": "EXITOSA",
  "rutas_evaluadas": 403,
  "rutas_publicadas": 403,
  "paradas_evaluadas": 2968,
  "paradas_publicadas": 2968,
  "detalle": "Se rutearon 2,968 cuentas de campo en 403 municipios con ruteo/v1."
}
```

**15. Listar el historial de ruteo de esa ejecución territorial**, en cualquier estado y versión,
de la más reciente a la más antigua:

```bash
curl -H "X-API-Key: clave-local-de-desarrollo" http://localhost:8000/territoriales/<territorial_run_id>/ruteos
```

**16. Ver la ruta de cada municipio**, en el orden de prioridad territorial:

```bash
curl -H "X-API-Key: clave-local-de-desarrollo" "http://localhost:8000/ruteos/<ruteo_run_id>/rutas?por_pagina=1"
```

```json
{
  "ruteo_run_id": "…", "territorial_run_id": "…", "decision_run_id": "…", "run_id": "…",
  "version_reglas": "ruteo/v1", "estado": "EXITOSA",
  "total": 403, "pagina": 1, "por_pagina": 1,
  "elementos": [
    {
      "clave_territorio": "21074",
      "posicion_territorial": 1,
      "cuentas_campo": 425,
      "paradas": 425,
      "distancia_inicial_m": 241140,
      "distancia_total_m": 226184,
      "distancia_regreso_deposito_m": 1924,
      "mejora_2opt_m": 14956
    }
  ]
}
```

**17. Ver las paradas de un municipio**, en el orden de visita:

```bash
curl -H "X-API-Key: clave-local-de-desarrollo" "http://localhost:8000/ruteos/<ruteo_run_id>/rutas/21074/paradas?por_pagina=2"
```

```json
{
  "ruteo_run_id": "…", "run_id": "…", "version_reglas": "ruteo/v1", "estado": "EXITOSA",
  "clave_territorio": "21074",
  "total": 425, "pagina": 1, "por_pagina": 2,
  "elementos": [
    {"secuencia": 1, "cliente_unico": "CU8843537813", "x_m": -71, "y_m": 557, "distancia_desde_anterior_m": 628},
    {"secuencia": 2, "cliente_unico": "CU6487942474", "x_m": -268, "y_m": 579, "distancia_desde_anterior_m": 219}
  ]
}
```

Los metros son sintéticos: `x_m` y `y_m` son un punto del plano local de 21074, no una longitud y
una latitud.

### Si el flujo se detiene

Una etapa que no termina `EXITOSA` detiene el flujo en esa etapa, y su `detalle` lo dice:

```json
{"estado": "DETENIDO", "etapa": "DECISION", "detalle": "La decision termino FALLIDA. Para reintentarla, reanuda el flujo.", "…": "…"}
```

Si se detuvo en la decisión, la organización territorial o el ruteo, se reintenta con otra
ejecución:

```bash
curl -X POST -H "X-API-Key: clave-local-de-desarrollo" http://localhost:8000/flujos/<flujo_id>/reanudar
```

Responde `200` con el flujo otra vez `EN_PROCESO`, apuntando a la ejecución nueva, y el worker
sigue desde ahí hasta el ruteo. La que falló queda en el historial de su etapa. Una corrida
`RECHAZADA` o `FALLIDA` no se reanuda: una corrida terminada es evidencia, y se vuelve a subir el
archivo, que es otra corrida con otro flujo.

### Etapas a mano

Una corrida publicada con el CLI, o antes de v0.5.0, no tiene flujo:
`GET /corridas/{run_id}/flujo` responde `404 FLUJO_NO_ENCONTRADO`. Sus etapas se piden una por una:

```bash
curl -i -X POST -H "X-API-Key: clave-local-de-desarrollo" http://localhost:8000/corridas/<run_id>/decisiones
curl -i -X POST -H "X-API-Key: clave-local-de-desarrollo" http://localhost:8000/decisiones/<decision_run_id>/territoriales
curl -i -X POST -H "X-API-Key: clave-local-de-desarrollo" http://localhost:8000/territoriales/<territorial_run_id>/ruteos
```

Cada una responde `201` con la ejecución `EN_PROCESO` y su `Location`; la ejecuta el worker, y se
consulta hasta que termine antes de pedir la siguiente. Sobre una fuente que es de un flujo
responden `409`: `FLUJO_EN_PROCESO` si el flujo la va a correr, o `FLUJO_DETENIDO` si se detuvo
ahí y se reanuda.

### La prueba de humo

Todo el flujo, con la verificación de cada código HTTP, está en un solo script que corre igual en
tu máquina que en el CI. Sube una cartera y no pide nada más: sigue su flujo hasta `COMPLETADO`,
revisa sus cuatro trabajos y lo que publicó cada etapa, y al final pide a mano la decisión, la
organización territorial y el ruteo ya publicados y exige los tres `409`. Necesita la API y el
worker corriendo, y un archivo que no se haya subido antes, porque el mismo archivo no se publica
dos veces:

```bash
docker compose exec api motor-cartera generar --destino datos/humo.xlsx --semilla 7
python scripts/prueba_de_humo.py datos/humo.xlsx
```

Solo usa la biblioteca estándar de Python; también corre dentro del contenedor con
`docker compose exec api python scripts/prueba_de_humo.py datos/humo.xlsx`. Con
`--oficial <archivo> --corte <fecha>` lleva además una cartera oficial por el flujo hasta el ruteo y
verifica su evidencia, y con `--pagos <archivo>`, una ingesta de pagos:

```bash
docker compose exec api motor-cartera generar-oficial --destino datos --formato zip --fecha-corte 2026-09-30
python scripts/prueba_de_humo.py datos/humo.xlsx --oficial datos/cartera_oficial_2026-09-30.zip \
  --corte 2026-09-30 --pagos datos/pagos_oficial_2026-09-24_2026-09-30.zip
```

Con `--escenario <directorio>` prueba el modelo histórico de punta a punta: sube los cortes de un
escenario de `generar-escenario` del más reciente al más antiguo, y sus pagos, espera la historia de
cada uno y revisa la Cuenta 360 de cuentas elegidas en los archivos: una que está en todos los
cortes, una que sale, una que llega después y una que paga varias veces. Y espera la
interpretación del motor de pagos de cada ventana: que lea cada pago del escenario, que cuente
como duplicados exactos los repetidos de su manifiesto, que una fila que llegó dos veces sea un
movimiento con sus dos observaciones y su archivo original, y que la Cuenta 360 distinga los pagos
observados de los movimientos.

```bash
docker compose exec api motor-cartera generar-escenario --destino datos/escenario --perfil XS \
  --cortes 4 --primer-corte 2026-10-07 --semilla 7
python scripts/prueba_de_humo.py datos/humo.xlsx --escenario datos/escenario
```

Con `--oficial` y `--pagos`, recorre además el lifecycle de cobranza sobre sus cuentas y sus pagos:
registra gestiones (una con promesa, una sin contacto, dos antes de un mismo pago y una que anula),
comprueba la idempotencia de las escrituras, evalúa las promesas a la fecha del corte, atribuye la
ventana de los pagos (una asociación única, una ambigua y una sin candidata por la anulación) y
revisa la Cuenta 360, la línea de tiempo y los trabajos `EVALUACION_PROMESAS` y `ATRIBUCION`.

## Fuentes oficiales

Desde v0.6.0 hay tres contratos de entrada, y el de cada archivo se declara, nunca se adivina:

| Contrato | Qué es | Columnas | Dónde entra |
|---|---|---|---|
| `cartera/v1` | La cartera mínima de siempre, congelada | 8, con alias | `POST /corridas` (por omisión) |
| `cartera/v2` | La hoja CARTERA de la cartera oficial | [93 exactas](docs/diccionario_cartera.md) | `POST /corridas` con `contrato=cartera/v2` y `fecha_corte` |
| `pagos/v1` | Los movimientos económicos de un periodo | [23 exactas](docs/diccionario_pagos.md) | `POST /pagos` |

```bash
# Una cartera oficial (con CARRIER) y los pagos de la semana que termina en su corte
docker compose exec api motor-cartera generar-oficial --destino datos --fecha-corte 2026-09-30
curl -i -H "X-API-Key: clave-local-de-desarrollo" -F "archivo=@datos/cartera_oficial_2026-09-30.xlsx" \
  -F contrato=cartera/v2 -F fecha_corte=2026-09-30 http://localhost:8000/corridas
curl -i -H "X-API-Key: clave-local-de-desarrollo" \
  -F "archivo=@datos/pagos_oficial_2026-09-24_2026-09-30.xlsx" http://localhost:8000/pagos
```

- **Estructura exacta.** cartera/v2 y pagos/v1 exigen exactamente sus columnas, en cualquier
  orden. Una que falta, una que sobra o un encabezado repetido deja la corrida `FALLIDA` sin juzgar
  ningún registro: no hay descartes silenciosos.
- **La fecha de corte es metadata.** Las 93 columnas no traen una: se declara al subir y queda en
  la corrida. El mismo `CLIENTE_UNICO` en cortes distintos es válido; repetido dentro de un corte,
  se rechazan todas sus copias.
- **El original es evidencia.** Cada archivo se guarda en un almacén por contenido (su SHA-256 es
  su nombre), de forma atómica, de solo lectura y para siempre: no se borra al terminar la ingesta
  ni al bajar la migración. `motor-cartera verificar-fuentes` vuelve a firmar cada artefacto.
- **El conformado es la fuente tipada.** Los registros válidos quedan en un Parquet con las
  columnas del contrato, tipadas, y la fila y la hoja de origen de cada uno. Se reproduce byte por
  byte desde el original. `GET /corridas/{run_id}/fuente` y `GET /pagos/{pagos_run_id}/fuente` dan
  el linaje, sin decir nunca dónde vive un objeto.
- **La cartera oficial llega hasta el ruteo.** La proyección explícita `operacional/v1` lleva cada
  registro a `Cuenta`, con las claves del INEGI resueltas desde el estado y la población por un
  catálogo público y versionado; lo que no se resuelve o es ambiguo se rechaza con su motivo.
- **CARRIER se audita, no se publica.** La hoja compañera se reconoce y sus advertencias quedan en
  la evidencia, sin bloquear CARTERA ([docs/carrier.md](docs/carrier.md)).
- **Los pagos no se deduplican.** Una fila es un movimiento: dos filas idénticas son dos
  movimientos. Un solo movimiento inválido rechaza el archivo (tolerancia 0 por omisión). Su
  ingesta es un trabajo durable, `INGESTA_PAGOS`, y no encadena nada. Interpretarlos es del
  [motor de pagos](#el-motor-de-pagos), aparte.
- **Un despacho, una cartera.** `DSP_001` y `CARTERA_PRINCIPAL` (configurables con `MC_DESPACHO_ID`
  y `MC_CARTERA_ID`) quedan en cada corrida e ingesta: son metadata del sistema, no columnas.

**Escala.** El generador tiene perfiles de XS (1,000 cuentas, el valor por omisión) a XXL
(1,000,000); **XL, 500,000, es el escenario empresarial objetivo**. `generar-escenario` escribe
varios cortes relacionados, con altas, bajas, saldos y atrasos que cambian y los pagos entre
cortes, determinista por semilla. `scripts/benchmark_escala.py` mide cada fase fuera del CI: en la
máquina de desarrollo, una cartera XL en zip se ingiere en 149 s (3,355 filas por segundo) con 930
MiB de memoria pico. El detalle, las invariantes del escenario y los resultados completos están en
[docs/fuentes.md](docs/fuentes.md).

## El modelo histórico y la Cuenta 360

Cuando una cartera `cartera/v2` o un archivo de pagos se publican, en la misma transacción que su
dataset conformado queda en la cola un trabajo `HISTORIA`. El worker lo ejecuta en paralelo al
flujo, después de las etapas operacionales: ni la decisión, ni la organización territorial ni el
ruteo lo esperan. Lee solo el Parquet conformado, nunca el archivo original, y publica todo o nada:

- una **cuenta canónica** por despacho, cartera y `CLIENTE_UNICO` (no una persona ni un crédito);
- un **corte canónico** por fecha. Otra fuente de la misma fecha con la misma cartera (el xlsx y el
  zip del mismo día) es una **fuente equivalente**: no duplica nada. Una con otro contenido es un
  **conflicto**: su ejecución queda `FALLIDA` con `CORTE_CANONICO_CONFLICTIVO`, y el corte publicado
  no cambia;
- un **snapshot** de cada cuenta en cada corte, con sus variables históricas (saldos, atraso,
  producto, estrategia, canal, último pago, geografía, plan y promesa) y su fila de origen, sin las
  93 columnas ni la PII, que siguen en el Parquet. Un snapshot nunca se actualiza;
- un **pago observado** por cada fila de pagos/v1, con sus 23 campos, sin deduplicar, conciliar ni
  atribuir. Un pago de un cliente que ningún corte trae se conserva, `SIN_CUENTA_OBSERVADA`, y no
  crea una cuenta. Desde v0.8, el [motor de pagos](#el-motor-de-pagos) los interpreta aparte, sin
  tocarlos.

La historia se ordena por fecha de corte, no por orden de llegada: un corte que llega tarde deja
la misma historia que si hubiera llegado a tiempo. Los eventos (`PRIMERA_OBSERVACION`,
`SALIDA_OBSERVADA`, `REINGRESO_OBSERVADO`), la continuidad entre cortes y los deltas se calculan al
consultar.

```bash
K="X-API-Key: clave-local-de-desarrollo"
curl -s -H "$K" "http://localhost:8000/cuentas?cliente_unico=CU0000004521"     # su cuenta_id
curl -s -H "$K" http://localhost:8000/cuentas/<cuenta_id>                      # Cuenta 360
curl -s -H "$K" "http://localhost:8000/cuentas/<cuenta_id>?al=2026-09-16"      # como se veía ese día
curl -s -H "$K" http://localhost:8000/cuentas/<cuenta_id>/historia             # snapshots y deltas
curl -s -H "$K" http://localhost:8000/cuentas/<cuenta_id>/eventos              # presencia
curl -s -H "$K" http://localhost:8000/cuentas/<cuenta_id>/pagos-observados     # pagos tal como llegaron
curl -s -H "$K" http://localhost:8000/cuentas/<cuenta_id>/movimientos          # lo que interpreta el motor
curl -s -H "$K" http://localhost:8000/cartera/cortes                           # cortes y el último
curl -s -H "$K" http://localhost:8000/corridas/<run_id>/historia               # su materialización
```

Lo publicado antes de v0.7.0 no tiene historia: la migración `0008` solo crea las tablas, y
`motor-cartera backfill-historia` encola la de cada dataset que no la tiene (con `--dry-run` dice
cuánto falta sin encolar nada). La materialización, los índices, el volumen y el benchmark están en
[docs/historia.md](docs/historia.md); la API, con ejemplos, en [docs/cuenta_360.md](docs/cuenta_360.md).

**Medido.** En la máquina de desarrollo, la historia de 12 cortes XL (5,795,700 snapshots de
606,858 cuentas canónicas) y de sus 11 semanas de pagos (2,995,846 pagos observados) se materializó
en 815 s: una mediana de 28.5 s por corte de unas 500,000 cuentas y de 21.0 s por archivo de pagos,
con menos de 400 MiB de memoria pico. Con todo eso publicado, cada sentencia de una consulta de
cuenta se resuelve por sus índices en menos de 0.14 ms dentro de PostgreSQL, y la Cuenta 360
completa responde en 5 a 8 ms (mediana) desde el servicio.

## El motor de pagos

Un pago observado es una fila de pagos/v1 tal como llegó, y no cambia nunca. El motor de pagos
(`motor-pagos/v1`) **interpreta** esas filas sin tocarlas: decide qué hechos económicos distintos
representan, cuáles son copias de otro, cuáles no se pueden decidir, cuáles revierten a otro, a qué
cuenta pertenecen y cuál es la recuperación que se interpreta de ellos. Lo que concluye vive en sus
propias entidades, versionadas: una `EjecucionMotorPagos` por interpretación de una **ventana** (un
despacho, una cartera y un mes de recepción), un `ResultadoPagoObservado` por cada observación, con
su clasificación y sus motivos, y un `MovimientoEconomicoCanonico` por cada hecho distinto.

```
INGESTA_PAGOS ──▶ HISTORIA ──▶ MOTOR_PAGOS ──▶ Cuenta 360
  (pagos/v1)    (pagos observados)  (una ventana)   /pagos-observados  ≠  /movimientos
```

- **Duplicado exacto**: las 23 columnas iguales, en la misma cartera, comprobado campo por campo
  después de agrupar por su firma exacta. El grupo funda un solo movimiento; cada copia conserva su
  resultado, `DUPLICADO_EXACTO`.
- **Coincidencia ambigua**: el mismo cliente, el mismo segundo y el mismo importe (la llave del
  sistema anterior) con otro gestor, otra campaña u otro campo. **No se fusiona** ni funda
  movimiento: no se sabe si es uno o dos pagos, y su importe se informa aparte.
- **Reverso**: un negativo que forma una pareja aislada con un pago del mismo cliente e importe,
  hasta 30 días antes. Lo demás es **posible reverso**: resta de la neta, pero no anula nada. pagos/v1
  no dice cuál es el original de un reverso, y el motor no elige.
- **Conciliación**: con la `CuentaCanonica` de su cliente, si existe (`CONCILIADO_CUENTA`), o
  `SIN_CUENTA_OBSERVADA`, que se conserva y no crea una cuenta. Su **contexto temporal** (el
  snapshot anterior y el siguiente) se calcula al consultar.
- **Recuperación bruta interpretada**: los pagos que ningún reverso anuló, contando una vez cada
  hecho. **Neta**: la bruta menos los posibles reversos. **No es contabilidad**: el saldo oficial
  sigue siendo el del snapshot.

La interpretación de una ventana se abre en la misma transacción en que la historia publica sus
pagos observados, y la ejecuta el worker al final de la cola, fuera del flujo operacional: la
decisión, la organización territorial y el ruteo no la esperan. Corre en PostgreSQL por conjuntos,
todo o nada, y solo la publica el dueño vigente de su trabajo. La misma ventana con las mismas
entradas no se publica dos veces; con entradas nuevas, otra ejecución publica la interpretación
nueva y la anterior queda como historia.

```bash
K="X-API-Key: clave-local-de-desarrollo"
curl -s -H "$K" http://localhost:8000/motor-pagos                                   # interpretaciones, y la vigente
curl -s -H "$K" http://localhost:8000/motor-pagos/<motor_pagos_run_id>              # calidad y recuperación
curl -s -H "$K" "http://localhost:8000/motor-pagos/<id>/resultados?clasificacion=COINCIDENCIA_AMBIGUA"
curl -s -H "$K" "http://localhost:8000/movimientos?cliente_unico=CU0000004521"
curl -s -H "$K" http://localhost:8000/movimientos/<movimiento_id>/observaciones      # por qué vale una vez
curl -s -H "$K" http://localhost:8000/cuentas/<cuenta_id>/movimientos                # con su contexto temporal
```

Los pagos observados antes de v0.8.0 se interpretan con `motor-cartera backfill-motor-pagos`, por
ventanas (`--dry-run` dice cuántos pagos observados no tienen interpretación vigente;
`--reintentar-fallidas` y `--reconciliar`, para las que fallaron y para los pagos sin cuenta cuyo
cliente ya la tiene). Las reglas, las huellas, los motivos, la orquestación, los índices y el
benchmark están en [docs/motor_pagos.md](docs/motor_pagos.md); la Cuenta 360, en
[docs/cuenta_360.md](docs/cuenta_360.md); el porqué, en las decisiones 85 a 98.

**Medido.** En la máquina de desarrollo, sobre los 12 cortes XL de la historia, el motor interpretó
los 2,995,846 pagos observados de sus tres meses en 869 s (3,448 por segundo) con 147 MiB de memoria
pico: 2,980,942 movimientos canónicos, los 14,904 duplicados exactos del escenario y sus 9,100
negativos (33 reversos y 9,067 posibles reversos), sin perder ni contar dos veces ninguna
observación. Una llegada tardía reinterpreta entero cada mes que toca, de 5 a 7 minutos por mes XL;
de 5,000 reversos tardíos, 4,941 se emparejaron con su pago. Con todo publicado, cada sentencia de
una consulta de cuenta o de movimiento se resuelve por sus índices en menos de 0.25 ms dentro de
PostgreSQL, y responde en 4 a 9 ms (mediana) desde el servicio.

## El lifecycle de cobranza y la atribución

Lo que la cobranza hizo con cada cuenta se registra como **eventos operacionales**: gestiones,
contactos, visitas, promesas, convenios, sus cancelaciones y sus anulaciones. Es una tercera verdad,
junto a lo que dicen las fuentes y a lo que interpreta el motor de pagos, y **no es una tercera
fuente oficial**: lo registra Motor Cartera, por su API o por una importación sintética, y nunca se
fabrica de un snapshot.

```
FUENTE        CARTERA y PAGOS tal como llegaron; lo que un corte dice de una promesa: OBSERVACION_EN_CORTE
OPERACIONAL   EventoLifecycle: GESTION_REGISTRADA, PROMESA_CREADA, CONVENIO_CREADO, sus cierres...
ECONOMICO     los movimientos canónicos del motor de pagos
```

- **Dos tiempos**: `ocurrido_en` (cuándo pasó, lo declara quien registra, con su zona) y
  `registrado_en` (el reloj de la base). Un evento tardío es válido y queda donde ocurrió.
- **Solo se agrega**: un trigger rechaza todo `UPDATE` y `DELETE`. Una gestión mal registrada se
  anula con `GESTION_ANULADA`; una promesa o un convenio que dejaron de valer, se cancelan. Lo
  original sigue auditable.
- **Idempotencia**: cada escritura exige `Idempotency-Key`. La misma llave con la misma petición
  responde `200` con `Idempotent-Replayed: true`; con otra, `409 IDEMPOTENCY_KEY_REUTILIZADA`. Lo
  garantiza un índice único en PostgreSQL.
- **Promesas**: si una se cumplió lo dice `evaluacion-promesa/v1`, a una fecha de corte explícita
  (`as_of`, nunca del reloj): `CUMPLIDA`, `PARCIAL`, `INCUMPLIDA`, `PENDIENTE`, `CANCELADA` o
  `NO_EVALUABLE`. Observar recuperación compatible no dice que la promesa la produjo.
- **Convenios**: con sus cuotas declaradas una por una, sin ledger: ningún pago se aplica a una
  cuota.
- **Atribución** (`atribucion/v1`): cada `PAGO` interpretado con las gestiones con contacto de su
  cuenta que ocurrieron antes, dentro de una ventana que es parámetro de cada ejecución (30 días por
  omisión): `SIN_GESTION_CANDIDATA`, `ASOCIACION_UNICA` o `AMBIGUA`, con todas sus candidatas y sin
  elegir ninguna. **Asociación operacional, no causalidad.**

```bash
K="X-API-Key: clave-local-de-desarrollo"
curl -s -X POST -H "$K" -H "Idempotency-Key: gestion-0001" -H "Content-Type: application/json" \
  -d '{"ocurrido_en":"2026-09-20T10:15:00-06:00","canal":"TELEFONICA","medio":"LLAMADA","nivel_contacto":"CONTACTO_TITULAR","resultado":"PROMESA"}' \
  http://localhost:8000/cuentas/<cuenta_id>/gestiones
curl -s -X POST -H "$K" -H "Idempotency-Key: promesa-0001" -H "Content-Type: application/json" \
  -d '{"monto_prometido":"1000.00","fecha_limite":"2026-09-25"}' \
  http://localhost:8000/gestiones/<gestion_id>/promesas
curl -s -H "$K" "http://localhost:8000/cuentas/<cuenta_id>/lifecycle?orden=asc"   # las tres verdades
curl -s -X POST -H "$K" -H "Content-Type: application/json" -d '{"as_of":"2026-09-30"}' \
  http://localhost:8000/evaluaciones-promesas
curl -s -X POST -H "$K" -H "Content-Type: application/json" -d '{"periodo":"2026-09"}' \
  http://localhost:8000/atribuciones
curl -s -H "$K" "http://localhost:8000/atribuciones/<atribucion_run_id>/resultados?clasificacion=AMBIGUA"
```

Lo masivo no pasa por las escrituras individuales: la evaluación y la atribución son trabajos
durables (`EVALUACION_PROMESAS`, `ATRIBUCION`) que la cola toma después del motor de pagos, uno por
cartera y fecha o por ventana, nunca uno por promesa o por pago; `backfill-lifecycle --as-of` y
`backfill-atribucion` encuentran lo pendiente. `motor-cartera cargar-lifecycle` importa eventos
sintéticos de un JSONL por conjuntos (COPY y tablas temporales), todo o nada e idempotente, y
`generar-escenario --lifecycle` los genera, deterministas por semilla, sin cambiar un byte de los
cortes ni de los pagos. Todo está en [docs/lifecycle.md](docs/lifecycle.md) y
[docs/atribucion.md](docs/atribucion.md); el porqué, en las decisiones 99 a 112.

**Medido.** En la máquina de desarrollo, sobre los 12 cortes XL del motor de pagos, con intensidad
0.5: `cargar-lifecycle` registró **2,573,729 eventos** en 832 s (3,092 por segundo) con 113 a 122
MiB de memoria pico por archivo, y volver a cargar uno no registró nada; la atribución de **3,242,650
pagos** (829,365 con asociación única, 1,058,497 ambiguos y 1,354,788 sin candidata) tomó 625 s con
152 MiB, y la evaluación de 410,626 promesas, 39 s. La base creció 3.1 GB. Cada sentencia de una
consulta de una cuenta entra por un índice y se resuelve en menos de 0.6 ms dentro de PostgreSQL;
por la API, de 18 a 43 ms (mediana). Los detalles, en [docs/lifecycle.md](docs/lifecycle.md) y
[docs/atribucion.md](docs/atribucion.md).

## El Decision Engine

**Entrada:** una cuenta certificada, es decir, de una corrida `EXITOSA`. De ella, las reglas
solo leen los días de atraso y el saldo; producto, canal y claves geográficas no entran.

**Salida:** por cada cuenta,

- `segmento`: en qué situación de mora está;
- `prioridad`: qué tan pronto hay que gestionarla;
- `canal_recomendado`: por dónde conviene gestionarla. Es una decisión, no el canal con que la
  cuenta llegó en la cartera;
- `motivos`: por qué, paso a paso, cada uno con su `codigo`, el `campo` que la regla leyó o
  produjo y su `valor`.

Las reglas tienen su propia versión, `VERSION_REGLAS_DECISION = decision/v1`, independiente de
la del contrato, `VERSION_CONTRATO = cartera/v1`. Cada corrida guarda con qué contrato entró la
cartera, y cada ejecución, con qué reglas se tomó la decisión. Si cambia una regla, cambia la
versión de las reglas y no la del contrato, y una decisión vieja se sigue explicando con las
reglas que la tomaron.

Las reglas de `decision/v1`, en el orden en que se aplican:

| Días de atraso | Tramo del resumen | Segmento | Prioridad base |
|---|---|---|---|
| 0 | `0` | `AL_CORRIENTE` | `BAJA` |
| 1 a 30 | `1-30` | `MORA_TEMPRANA` | `MEDIA` |
| 31 a 90 | `31-60` y `61-90` | `MORA_MEDIA` | `ALTA` |
| 91 o más | `91+` | `MORA_ALTA` | `MUY_ALTA` |

- **Saldo alto.** Con atraso y un saldo de al menos `UMBRAL_SALDO_ALTO = 50,000.00`, la
  prioridad sube un nivel, con tope en `MUY_ALTA`. Una cuenta al corriente nunca sube por saldo.
- **Canal.** Sale solo de la prioridad final: `BAJA` y `MEDIA` van por `DIGITAL`, `ALTA` por
  `TELEFONICA` y `MUY_ALTA` por `CAMPO`.
- **Motivos.** Uno por paso: el tramo (`SIN_MORA`, `MORA_1_30`, `MORA_31_90` o `MORA_91_MAS`),
  `SALDO_ALTO` si la regla aplicó (aunque la prioridad ya estuviera en el tope), la prioridad
  final y el canal. Salen de un catálogo cerrado de doce códigos.

Así, una cuenta con 65 días de atraso y 62,000.00 de saldo queda en `MORA_MEDIA`, sube de
`ALTA` a `MUY_ALTA` por su saldo y se recomienda por `CAMPO`: es la del ejemplo del paso 10.

**Las reglas y el umbral son sintéticos**, propios de este proyecto público: no vienen de
ninguna operación real, no son reglas propietarias y no son una recomendación de cobranza.
Existen para que el motor tenga algo concreto que decidir, probar y explicar. El umbral vive en
el código y no en la configuración: si se pudiera cambiar por entorno, la misma versión daría
resultados distintos.

El núcleo de las reglas es puro: no lee la base, la configuración ni el reloj, así que la misma
cuenta con la misma versión da siempre la misma decisión. Guardar las decisiones de una corrida
es trabajo de otra capa, que las publica todas o ninguna.

## El Motor Territorial

**Entrada:** una ejecución de decisión `EXITOSA` de `decision/v1`. De cada decisión solo cuenta
el canal recomendado, y de su cuenta, el saldo y el municipio.

**Agrupación:** un territorio es un municipio, `cve_entidad + cve_municipio`: `"21"` y `"074"`
dan `"21074"`. Las claves son las de la cartera, validadas por su forma.

**Métricas**, por municipio, sobre las decisiones de esa ejecución:

- `cuentas_total`: cuántas cuentas tiene;
- `saldo_total`: su saldo sumado;
- `cuentas_campo`: cuántas tienen `CAMPO` como canal recomendado. Cuenta la decisión, no el
  canal con que la cuenta llegó en la cartera;
- `saldo_campo`: el saldo de esas cuentas de campo.

Las calcula PostgreSQL con un solo `GROUP BY`: al núcleo llega una fila por municipio, no una por
cuenta.

**Carga**, solo por las cuentas de campo:

| Cuentas de campo | Carga | Motivo |
|---|---|---|
| 0 | `SIN_CARGA` | `SIN_CARGA_CAMPO` |
| 1 a 4 | `BAJA` | `CARGA_CAMPO_1_4` |
| 5 a 19 | `MEDIA` | `CARGA_CAMPO_5_19` |
| 20 o más | `ALTA` | `CARGA_CAMPO_20_MAS` |

**Orden:** los municipios con cuentas de campo reciben un lugar, `posicion_campo`, desde 1 y sin
huecos, por:

1. `cuentas_campo`, de mayor a menor;
2. `saldo_campo`, de mayor a menor;
3. `clave_territorio`, de menor a mayor.

Los `SIN_CARGA` van al final, por clave y sin lugar (`null`). **El saldo no cambia la carga:
solo desempata.** Un municipio con mucho saldo y pocas cuentas de campo conserva su carga, y
nunca queda delante de otro con más cuentas de campo. No hay un puntaje que mezcle cuentas y
pesos: cada lugar se explica con los valores que el resultado expone.

**Los umbrales (5 y 20) son sintéticos y demostrativos**, propios de este proyecto público: no
son propietarios, no vienen de ninguna operación real y no son una recomendación real de
cobranza. Como el de `decision/v1`, viven en el código y no en la configuración.

Con la cartera que el generador produce por omisión (semilla 31416), las 9,800 decisiones caen
en 541 municipios: 27 con carga `ALTA`, 75 `MEDIA`, 301 `BAJA` y 138 `SIN_CARGA`. El primero es
`21074`, con 425 cuentas de campo de 1,414: es el del ejemplo del paso 13.

**Cuatro versiones, cuatro preguntas.** Ninguna es la versión del paquete:

- `cartera/v1` (`VERSION_CONTRATO`): qué datos entran;
- `decision/v1` (`VERSION_REGLAS_DECISION`): qué decisión recibe cada cuenta;
- `territorial/v1` (`VERSION_REGLAS_TERRITORIAL`): cómo se organiza por territorio el trabajo de
  campo;
- `ruteo/v1` (`VERSION_REGLAS_RUTEO`): en qué secuencia se visitan las cuentas de campo dentro de
  cada municipio.

`territorial/v1` solo organiza decisiones de `decision/v1`. La compatibilidad es un literal:
cuando el Decision Engine decida con `decision/v2`, `territorial/v1` no la acepta sola. Cada
ejecución territorial guarda con qué reglas se calculó y cuelga de la ejecución de decisión que
organizó, así que de un municipio se llega a sus decisiones, de ellas a la corrida y de la
corrida al archivo.

**v0.3 prioriza; v0.4 secuencia.** v0.3.0 responde dónde se concentra la carga operativa de
campo y qué municipios conviene atender primero. v0.4.0 responde en qué secuencia visitar las
cuentas de campo dentro de cada municipio. `posicion_campo` es una prioridad territorial, no una
parada en un recorrido.

## El Motor de Ruteo

**Entrada:** una ejecución territorial `EXITOSA` de `territorial/v1`, sobre decisiones de una
ejecución `EXITOSA` de `decision/v1`. Las dos compatibilidades son literales: una
`territorial/v2` o una `decision/v2` no se vuelven ruteables solas.

**Paradas:** solo las cuentas con `CAMPO` como canal recomendado, `DecisionCuenta.canal_recomendado`,
nunca el canal con que la cuenta llegó en la cartera. Cada una es exactamente una parada.

**Territorio:** una ruta independiente por municipio con trabajo de campo (`cuentas_campo > 0`).
Cada municipio tiene su propio depósito y su propio plano, y el motor no conecta municipios entre
sí. Los `SIN_CARGA` no tienen ruta.

**Coordenadas sintéticas y deterministas.** La cartera no trae latitud, longitud ni direcciones, y
aquí no se inventan ni se consultan servicios de mapas. Cada municipio es un plano operativo local,
una cuadrícula de 10 km por lado, con el depósito en `(0, 0)` y coordenadas enteras de −5000 a
+5000 metros. El punto de cada cuenta sale de SHA-256 sobre `"ruteo/v1|{clave_territorio}|{cliente_unico}"`:
los primeros 8 bytes del digest dan `x_m` y los 8 siguientes `y_m`, `% 10001 - 5000`. Sin azar,
semilla, reloj ni configuración: la misma versión, el mismo municipio y el mismo cliente dan
siempre el mismo punto.

**Métrica:** distancia Manhattan, `|x1 − x2| + |y1 − y2|`, en metros sintéticos enteros: exacta,
sin `float` ni raíces.

**Algoritmo:** la ruta sale del depósito, visita cada cuenta una vez y regresa, y **la distancia
incluye el regreso al depósito**:

1. vecino más cercano: siempre a la cuenta pendiente más cercana; a igual distancia, a la de
   `cliente_unico` menor;
2. 2-opt: en cada pasada, de todas las inversiones de un tramo, la de mayor ahorro estrictamente
   positivo (a igual ahorro, la de `i` y después `j` menores), una por pasada y **hasta 10
   mejoras** (`MAX_PASADAS_2OPT`).

Cada ruta guarda la distancia del vecino más cercano (`distancia_inicial_m`), la final
(`distancia_total_m`), el regreso al depósito y la mejora del 2-opt, que nunca es negativa.

Con la cartera que el generador produce por omisión, los 403 municipios con trabajo de campo dan
403 rutas con 2,968 paradas. El 2-opt acorta 119 de ellas, 10 llegan al tope de diez mejoras, y
140 tienen una sola parada. La primera, 21074, tiene 425 paradas y mide 226,184 m sintéticos,
14,956 menos que la del vecino más cercano: es la del ejemplo del paso 16.

> **Las rutas de v0.4.0 no son rutas geográficas reales.** No representan calles, domicilios,
> tráfico, tiempos de conducción ni latitud y longitud. Son una simulación reproducible para
> demostrar la arquitectura, la optimización, la trazabilidad, las transacciones, la API y las
> pruebas.

**Por qué un plano sintético.** El repositorio no contiene ubicaciones reales. Inventar latitudes y
longitudes sería engañoso: parecerían puntos de un mapa y no lo serían. Agregar datos reales
violaría la regla del proyecto. Por eso cada municipio tiene un plano operativo sintético, que se
declara como tal.

**Prioridad no es secuencia.** Son dos cosas distintas, de dos motores distintos:

- `posicion_campo` (y `posicion_territorial` en la API de ruteo): la prioridad del municipio
  entre los municipios, de `territorial/v1`;
- `secuencia`: el orden de visita de cada cuenta dentro de su municipio, de `ruteo/v1`.

No hay gestores, vehículos, capacidades, horarios ni ventanas de tiempo: una ruta es la secuencia
de visita sobre todas las cuentas de campo de un municipio, no la jornada de una persona.

## La orquestación durable

**La API no ejecuta ningún motor.** Cada `POST` que pide trabajo registra el recurso `EN_PROCESO`
y su trabajo en PostgreSQL, en la misma transacción, y responde `201`. Un worker, en otro proceso,
toma el trabajo, ejecuta el motor y lo cierra:

```
API
 │
 ├── PostgreSQL, en una sola transacción
 │      ├── el recurso EN_PROCESO: la corrida y su archivo, o la ejecución de una etapa
 │      ├── TrabajoOrquestacion: su trabajo, en la cola
 │      └── FlujoOrquestacion: en qué etapa va la corrida
 │
 └── responde 201

worker (uno o varios procesos)
 │
 ├── toma un trabajo con FOR UPDATE SKIP LOCKED
 ├── lease: el trabajo es suyo hasta lease_hasta
 ├── latido: renueva el lease mientras trabaja
 └── ejecuta el motor y, al cerrar el trabajo, encola la etapa siguiente en la misma transacción
```

**El flujo automático.** Un solo `POST /corridas` produce la corrida, su decisión, su
organización territorial y su ruteo, sin que el cliente pida cada etapa:

```
INGESTA ──▶ DECISION ──▶ TERRITORIAL ──▶ RUTEO ──▶ COMPLETADA
```

Cuando el trabajo de una etapa se cierra con su recurso `EXITOSA`, la misma transacción abre la
siguiente; una etapa que no termina `EXITOSA` detiene el flujo. Todo se observa en
`GET /corridas/{run_id}/flujo` y `GET /flujos/{flujo_id}/trabajos`.

**Fuera del flujo, en la misma cola.** La historia de cada dataset (`HISTORIA`, v0.7) y la
interpretación de cada ventana de pagos (`MOTOR_PAGOS`, v0.8) son trabajos durables como
cualquier otro, pero ninguna etapa los espera y una falla suya no detiene nada. Entre los trabajos
que se pueden tomar, el worker toma primero los operacionales, después los `HISTORIA` y al final
los `MOTOR_PAGOS`.

**Entrega al menos una vez.** La entrega de un trabajo es *at-least-once*, no *exactly-once*: si un
worker muere después de que su motor confirmó y antes de cerrar el trabajo, otro worker lo vuelve a
tomar y llama otra vez al motor. Lo que no se repite es la publicación. Los motores ya estaban
protegidos de dos ejecuciones: toman su recurso con `FOR UPDATE`, no vuelven a ejecutar uno que
llegó a un estado terminal, publican todo o nada en una sola transacción, su fallo no degrada un
estado terminal, y los índices únicos admiten a lo más una ejecución `EXITOSA` y una `EN_PROCESO`
por fuente y versión.

**Lease y latido.**

```
PENDIENTE ──(un worker lo toma)──▶ EJECUTANDO ──(su recurso terminó)──▶ COMPLETADO
    ▲                                │   ▲
    │                                │   └── latido: renueva el lease
    └───(error del worker, espera)───┤
                                     └──(sin intentos)──▶ FALLIDO
```

Si el worker muere, deja de latir, su lease vence y otro worker toma el trabajo, con un intento
más. Si murió a media transacción del motor, PostgreSQL revierte lo que no se confirmó y el motor
se ejecuta entero otra vez; si murió después del `COMMIT`, el siguiente encuentra el recurso
terminado, no hace nada y cierra el trabajo. Un worker que perdió su lease ya no cierra el trabajo.
Con `SIGTERM` o `SIGINT`, el worker termina el trabajo en curso y sale.

**Trabajo no es motor.** El estado de un trabajo es el de la entrega; el de su recurso, el del
motor.

```
Trabajo COMPLETADO + EjecucionDecision FALLIDA + Flujo DETENIDO
```

no es una contradicción: el worker ejecutó bien un motor que terminó de forma controlada en
`FALLIDA`. Un error del worker (se cayó la conexión, murió el proceso) se reintenta solo, con
espera; una `FALLIDA` del motor, no, porque fallaría igual.

**Reanudar.** Un flujo detenido en la decisión, la organización territorial o el ruteo, con su
ejecución `FALLIDA`, se reintenta con `POST /flujos/{flujo_id}/reanudar`: otra ejecución de esa
etapa, y el flujo sigue hasta el ruteo. La ingesta no se reanuda, porque una corrida terminada es
evidencia inmutable: se vuelve a subir el archivo.

**El archivo.** Se copia al almacén de artefactos antes de responder, para que la ingesta
sobreviva a que la API muera, y no se borra nunca: es la evidencia de lo que llegó. Las corridas que
v0.5 dejó en la cola guardaban su archivo en PostgreSQL, y el worker las termina con él; las nuevas
no guardan archivos en la base.

**Parámetros del worker.** Son parámetros operativos, no reglas de decisión: cambian cuándo y
cuántas veces se intenta un trabajo, nunca qué calcula un motor.

| Variable | Por omisión | Qué controla |
|---|---|---|
| `MC_WORKER_POLL_SEGUNDOS` | `0.5` | Cuánto espera el worker antes de volver a buscar trabajo, con la cola vacía |
| `MC_WORKER_LEASE_SEGUNDOS` | `60` | Cuánto tiempo es suyo un trabajo sin latir; al vencer, otro worker lo puede tomar |
| `MC_WORKER_HEARTBEAT_SEGUNDOS` | `20` | Cada cuánto late para renovar el lease; tiene que ser menor que el lease |
| `MC_WORKER_MAX_INTENTOS` | `5` | Cuántas veces se puede tomar un trabajo antes de darlo por `FALLIDO`; cada trabajo copia el valor al nacer |
| `MC_WORKER_BACKOFF_SEGUNDOS` | `1` | La espera tras el primer error de un trabajo; se duplica en cada intento: 1, 2, 4, 8… |

El porqué de cada pieza está en [docs/decisiones.md](docs/decisiones.md), secciones 42 a 53.

## La API

| Método y ruta | Qué hace | Respuestas |
|---|---|---|
| `POST /corridas` | Guarda el archivo en el almacén de artefactos y registra la corrida `EN_PROCESO`, con su flujo automático y el trabajo de su ingesta. `contrato` (`cartera/v1` por omisión, o `cartera/v2`) y, con cartera/v2, `fecha_corte` | 201, 401, 409, 413, 415, 422 |
| `GET /corridas/{run_id}` | Estado: conteos, tiempos y resultado | 200, 401, 404, 422 |
| `GET /corridas/{run_id}/rechazos` | Registros rechazados con su fila y motivo, paginados | 200, 401, 404, 409, 422 |
| `GET /corridas/{run_id}/fuente` | La evidencia: el archivo original, el dataset conformado y la auditoría de CARRIER | 200, 401, 404, 422 |
| `POST /pagos` | Guarda el archivo de pagos y registra la ingesta `EN_PROCESO` con su trabajo `INGESTA_PAGOS` | 201, 401, 409, 413, 415, 422 |
| `GET /pagos/{pagos_run_id}` | Una ingesta de pagos: estado, conteos, firmas, tiempos y su trabajo | 200, 401, 404, 422 |
| `GET /pagos/{pagos_run_id}/rechazos` | Los movimientos rechazados con su fila, sus 23 valores y su motivo, paginados | 200, 401, 404, 409, 422 |
| `GET /pagos/{pagos_run_id}/fuente` | La evidencia de una ingesta de pagos: el original y el conformado | 200, 401, 404, 422 |
| `GET /corridas/{run_id}/flujo` | El flujo automático de la corrida: estado, etapa y el identificador de cada ejecución | 200, 401, 404, 422 |
| `GET /flujos/{flujo_id}` | Lo mismo, por el identificador del flujo | 200, 401, 404, 422 |
| `GET /flujos/{flujo_id}/trabajos` | Los trabajos del flujo, en el orden en que entraron a la cola, con sus intentos y su lease, paginados | 200, 401, 404, 422 |
| `GET /trabajos/{trabajo_id}` | Un trabajo de la cola: tipo, estado, intentos, lease y último error | 200, 401, 404, 422 |
| `POST /flujos/{flujo_id}/reanudar` | Reintenta, con otra ejecución, la etapa downstream en que se detuvo el flujo | 200, 401, 404, 409, 422 |
| `GET /cartera/resumen` | Cuentas y saldo por segmento de la cartera vigente, paginado | 200, 401, 404, 409, 422 |
| `POST /corridas/{run_id}/decisiones` | Pide decidir con `decision/v1` cada cuenta de una corrida `EXITOSA`; registra la ejecución `EN_PROCESO` y la decide el worker | 201, 401, 404, 409, 422 |
| `GET /corridas/{run_id}/decisiones` | Historial: todas las ejecuciones de la corrida, la más reciente primero, paginado | 200, 401, 404, 422 |
| `GET /decisiones/{decision_run_id}` | Una ejecución: versión de las reglas, estado, tiempos y conteos | 200, 401, 404, 422 |
| `GET /decisiones/{decision_run_id}/cuentas` | La decisión de cada cuenta con sus motivos, paginada; solo de una ejecución `EXITOSA` | 200, 401, 404, 409, 422 |
| `POST /decisiones/{decision_run_id}/territoriales` | Pide organizar por municipio, con `territorial/v1`, las decisiones de una ejecución `EXITOSA` de `decision/v1`; las organiza el worker | 201, 401, 404, 409, 422 |
| `GET /decisiones/{decision_run_id}/territoriales` | Historial: todas las ejecuciones territoriales de esas decisiones, la más reciente primero, paginado | 200, 401, 404, 422 |
| `GET /territoriales/{territorial_run_id}` | Una ejecución territorial: versión de las reglas, estado, tiempos y conteos | 200, 401, 404, 422 |
| `GET /territoriales/{territorial_run_id}/municipios` | Cada municipio con su carga, su lugar y su motivo, en orden de prioridad y paginado; solo de una ejecución `EXITOSA` | 200, 401, 404, 409, 422 |
| `POST /territoriales/{territorial_run_id}/ruteos` | Pide trazar con `ruteo/v1` la ruta sintética de cada municipio con trabajo de campo de una ejecución territorial `EXITOSA`; la traza el worker | 201, 401, 404, 409, 422 |
| `GET /territoriales/{territorial_run_id}/ruteos` | Historial: todas las ejecuciones de ruteo de esa ejecución territorial, la más reciente primero, paginado | 200, 401, 404, 422 |
| `GET /ruteos/{ruteo_run_id}` | Una ejecución de ruteo: versión de las reglas, estado, tiempos y conteos | 200, 401, 404, 422 |
| `GET /ruteos/{ruteo_run_id}/rutas` | La ruta de cada municipio con sus distancias, en orden de prioridad territorial y paginada; solo de una ejecución `EXITOSA` | 200, 401, 404, 409, 422 |
| `GET /ruteos/{ruteo_run_id}/rutas/{clave_territorio}/paradas` | Las paradas de un municipio en orden de visita, con su punto sintético y su distancia, paginadas; solo de una ejecución `EXITOSA` | 200, 401, 404, 409, 422 |
| `GET /cuentas?cliente_unico=…` | El `cuenta_id` de la cuenta canónica de ese `CLIENTE_UNICO` en la cartera del sistema | 200, 401, 404, 422 |
| `GET /cuentas/{cuenta_id}` | La Cuenta 360: primera y última observación, presencia, ausencias, reingresos, pagos observados, snapshot actual, `resumen_pagos` (lo que interpreta el motor de pagos) y `lifecycle_resumen` (lo que hizo la cobranza y la última atribución); con `?al=AAAA-MM-DD`, como se veía ese día | 200, 401, 404, 422 |
| `GET /cuentas/{cuenta_id}/historia` | Sus snapshots con continuidad, deltas y evidencia (`corte_id`, `dataset_id`, `source_row`, `source_sheet`), paginados; `orden=desc` por omisión | 200, 401, 404, 422 |
| `GET /cuentas/{cuenta_id}/eventos` | Sus eventos de presencia, calculados al consultar, paginados | 200, 401, 404, 422 |
| `GET /cuentas/{cuenta_id}/pagos-observados` | Sus movimientos de pagos/v1 tal como llegaron, sin deduplicar ni conciliar, del más reciente al más antiguo, paginados | 200, 401, 404, 422 |
| `GET /cuentas/{cuenta_id}/movimientos` | Sus movimientos económicos interpretados por el motor de pagos, con su contexto temporal entre sus snapshots; `desde` y `hasta`, paginados | 200, 401, 404, 422 |
| `GET /cartera/cortes` | Los cortes canónicos de la cartera, por fecha, y el último | 200, 401, 422 |
| `GET /cartera/cortes/{corte_id}` | Un corte con su evidencia: el Parquet, el original y sus fuentes equivalentes | 200, 401, 404, 422 |
| `GET /historias/{historia_run_id}` | Una ejecución histórica: estado, resultado, conteos, corte y trabajo | 200, 401, 404, 422 |
| `GET /corridas/{run_id}/historia` | Las ejecuciones que materializaron el dataset de la corrida | 200, 401, 404, 409, 422 |
| `GET /pagos/{pagos_run_id}/historia` | Las de una ingesta de pagos, y cuántos de sus pagos tienen hoy una cuenta observada | 200, 401, 404, 409, 422 |
| `GET /motor-pagos` | Las interpretaciones del motor de pagos, por ventana, con cuál es la vigente; filtros `periodo`, `estado` y `version`, paginadas | 200, 401, 422 |
| `GET /motor-pagos/{motor_pagos_run_id}` | Una interpretación: versión, estado, ventana, firma de entrada, `calidad` (cuántas observaciones de cada clase), `recuperacion` interpretada, tiempos y detalle | 200, 401, 404, 422 |
| `GET /motor-pagos/{motor_pagos_run_id}/resultados` | Lo que concluyó de cada pago observado de su ventana, y por qué; filtros `clasificacion`, `cliente_unico`, `firma_exacta` y `firma_legacy`, paginados | 200, 401, 404, 422 |
| `GET /movimientos` | Los movimientos económicos vigentes, del más reciente al más antiguo; filtros `cliente_unico`, `cuenta_id`, `desde`, `hasta`, `tipo` y `estado_conciliacion`, paginados | 200, 401, 404, 422 |
| `GET /movimientos/{movimiento_id}` | Un movimiento con su clasificación, sus motivos, su representante con su archivo original y su contexto temporal | 200, 401, 404, 422 |
| `GET /movimientos/{movimiento_id}/observaciones` | Los pagos observados que lo sustentan, cada uno con su ingesta, su dataset, su fila y su archivo original, paginados | 200, 401, 404, 422 |
| `POST /cuentas/{cuenta_id}/gestiones` | Registra una gestión (con su visita si es de `CAMPO`); exige `Idempotency-Key` | 200, 201, 401, 404, 409, 422 |
| `GET /cuentas/{cuenta_id}/gestiones` | Sus gestiones, filtradas en la base por `desde`, `hasta`, `canal`, `nivel_contacto`, `resultado` y `estado`, paginadas | 200, 401, 404, 422 |
| `GET /gestiones/{gestion_id}` | Una gestión: su cuenta, su contacto, su resultado, sus dos tiempos, su evento, si fue anulada y lo que nació de ella | 200, 401, 404, 422 |
| `POST /gestiones/{gestion_id}/anulaciones` | La anula, con su visita, su promesa y su convenio; exige `Idempotency-Key` | 200, 201, 401, 404, 409, 422 |
| `POST /gestiones/{gestion_id}/promesas` | La promesa de una gestión con resultado `PROMESA`; exige `Idempotency-Key` | 200, 201, 401, 404, 409, 422 |
| `GET /promesas/{promesa_id}` | Una promesa: lo prometido, su estado operativo, su última evaluación y su linaje | 200, 401, 404, 422 |
| `POST /promesas/{promesa_id}/cancelaciones` | La cancela; exige `Idempotency-Key` | 200, 201, 401, 404, 409, 422 |
| `GET /cuentas/{cuenta_id}/promesas` | Las promesas de la cuenta, por estado operativo, paginadas | 200, 401, 404, 422 |
| `POST /gestiones/{gestion_id}/convenios` | El convenio de una gestión con resultado `CONVENIO`, con sus cuotas declaradas; exige `Idempotency-Key` | 200, 201, 401, 404, 409, 422 |
| `GET /convenios/{convenio_id}` | Un convenio, sus cuotas y la recuperación observada durante su vigencia (no un ledger) | 200, 401, 404, 422 |
| `POST /convenios/{convenio_id}/cancelaciones` | Lo cancela; exige `Idempotency-Key` | 200, 201, 401, 404, 409, 422 |
| `GET /cuentas/{cuenta_id}/convenios` | Los convenios de la cuenta, paginados | 200, 401, 404, 422 |
| `GET /cuentas/{cuenta_id}/lifecycle` | Su línea de tiempo: `OPERACIONAL`, `FUENTE_CORTE` y `ECONOMICO`, por tiempo de negocio, paginada | 200, 401, 404, 422 |
| `POST /evaluaciones-promesas` | Pide evaluar las promesas de la cartera a una fecha de corte (`as_of`); la evalúa el worker | 201, 401, 409, 422 |
| `GET /evaluaciones-promesas` | Las evaluaciones, por fecha de corte, paginadas | 200, 401, 422 |
| `GET /evaluaciones-promesas/{evaluacion_run_id}` | Una evaluación: fecha de corte, estado, conteos por estado y montos | 200, 401, 404, 422 |
| `GET /evaluaciones-promesas/{evaluacion_run_id}/promesas` | Lo que concluyó de cada promesa, con sus motivos, paginado | 200, 401, 404, 422 |
| `POST /atribuciones` | Pide atribuir los pagos de un mes (`periodo`, y `ventana_dias` si no es la configurada); la atribuye el worker | 201, 401, 409, 422 |
| `GET /atribuciones` | Las atribuciones, por mes, con cuál es la vigente, paginadas | 200, 401, 422 |
| `GET /atribuciones/{atribucion_run_id}` | Una atribución: ventana, estado, conteos, montos y la interpretación de pagos que leyó | 200, 401, 404, 422 |
| `GET /atribuciones/{atribucion_run_id}/resultados` | Lo que concluyó de cada pago, con sus candidatas; filtros `clasificacion` y `cliente_unico`, paginado | 200, 401, 404, 422 |
| `GET /movimientos/{movimiento_id}/atribuciones` | Cada atribución de un pago: la vigente y las que la precedieron | 200, 401, 404, 422 |
| `GET /cuentas/{cuenta_id}/atribuciones` | Los pagos de la cuenta, cada uno con su atribución vigente, paginados | 200, 401, 404, 422 |
| `GET /salud` | La API vive y la base contesta. No pide clave | 200, 503 |

Todas las respuestas de error tienen la misma forma, también las que genera el framework:

```json
{
  "codigo": "ARCHIVO_YA_PUBLICADO",
  "mensaje": "Este archivo ya lo publico la corrida 4cce3e0d-….",
  "detalles": [],
  "run_id": "4cce3e0d-…",
  "pagos_run_id": null
}
```

El cliente compara `codigo`, que es estable; `mensaje` es para personas. `run_id` o `pagos_run_id`
dicen con qué corrida o con qué ingesta de pagos tiene que ver el error, si con alguna. En `/docs`,
cada ruta lista sus códigos de error con un ejemplo de cada uno.

Las reglas que un cliente tiene que conocer:

- **`201` quiere decir que el recurso se creó, no que el motor terminó.** Cada `POST` que pide
  trabajo responde `201` con el recurso `EN_PROCESO` y su `Location`, y el worker lo termina
  después. El cliente consulta `Location`, o el flujo, hasta que el `estado` deje de ser
  `EN_PROCESO`: `EXITOSA`, o `FALLIDA` con el motivo en `detalle` y nada publicado. El código HTTP
  describe la petición; el `estado`, cómo terminó el motor (D1).
- **Una etapa se publica con éxito una sola vez por versión de las reglas.** Si ya lo está, el
  `POST` responde `409 DECISION_YA_GENERADA`, `TERRITORIAL_YA_GENERADO` o `RUTEO_YA_GENERADO`, sin
  `Location` y sin nombrar la ejecución, que se descubre en el historial de su fuente (D2).
- **A lo más una en proceso.** Si la misma fuente ya se está procesando con la misma versión, el
  `POST` responde `409 ARCHIVO_EN_PROCESO`, `DECISION_EN_PROCESO`, `TERRITORIAL_EN_PROCESO` o
  `RUTEO_EN_PROCESO`, y no registra otra.
- **Una fuente que no se puede procesar no registra nada:** `409 CORRIDA_NO_PUBLICADA`,
  `DECISION_NO_TERRITORIALIZABLE` o `TERRITORIAL_NO_RUTEABLE`.
- **Las etapas de un flujo las corre el flujo.** Sobre la fuente de un flujo que va a correr esa
  etapa, `409 FLUJO_EN_PROCESO`; si el flujo se detuvo ahí, `409 FLUJO_DETENIDO`, y se reintenta
  con `POST /flujos/{flujo_id}/reanudar`.
- **Reanudar** responde `200` con el flujo otra vez `EN_PROCESO`. Un flujo que sigue en proceso da
  `409 FLUJO_EN_PROCESO`; uno completo, `409 FLUJO_YA_COMPLETADO`; uno detenido en la ingesta, o
  cuya etapa no terminó `FALLIDA`, `409 FLUJO_NO_REANUDABLE`.
- **Lo publicado solo existe para una ejecución `EXITOSA`.** Las decisiones, los municipios, las
  rutas y las paradas de otra dan `409 DECISION_NO_PUBLICADA`, `TERRITORIAL_NO_PUBLICADO` o
  `RUTEO_NO_PUBLICADO`; un municipio sin ruta en esa ejecución, `404 RUTA_NO_ENCONTRADA`.
- **El contrato se declara.** cartera/v2 sin `fecha_corte` da `422 FECHA_CORTE_REQUERIDA`, y
  cartera/v1 con una, `422 FECHA_CORTE_NO_APLICA`, sin guardar nada. Un archivo cuyos bytes no son
  los de su extensión da `415 FORMATO_NO_CORRESPONDE`.
- **Un archivo de pagos se acepta una vez.** Si otra ingesta ya lo aceptó o lo está procesando,
  `POST /pagos` responde `409 PAGOS_YA_ACEPTADOS` o `PAGOS_EN_PROCESO`, con su `pagos_run_id`.
- **Una cuenta canónica nace con un corte, no con un pago.** `GET /cuentas?cliente_unico=…`
  responde `404 CUENTA_NO_ENCONTRADA` si nada trae ese cliente, y `404 SIN_CUENTA_OBSERVADA` si solo
  hay pagos suyos.
- **Los pagos observados no son recuperación.** Son movimientos tal como llegaron: no están
  deduplicados, conciliados, interpretados como reversos ni atribuidos, y la API no los suma.
- **Los movimientos son una interpretación.** `/movimientos`, `/cuentas/{cuenta_id}/movimientos` y
  `resumen_pagos` dicen lo que `motor-pagos/v1` concluye de los pagos observados; no son el libro
  contable del acreedor, y cada respuesta lo advierte en `aviso`. Un movimiento que la
  interpretación vigente de su ventana ya no funda se sigue leyendo, con `vigente: false`.
- **El motor de pagos tampoco se pide: se abre solo** con la historia de cada archivo de pagos.
- **Toda escritura del lifecycle exige `Idempotency-Key`.** La misma llave con la misma petición
  responde `200` con el recurso ya registrado y `Idempotent-Replayed: true`; con otra petición,
  `409 IDEMPOTENCY_KEY_REUTILIZADA`. Nada se actualiza: una corrección es una anulación o una
  cancelación, y lo original sigue visible.
- **`ocurrido_en` lleva su zona horaria** y no puede ser del futuro (`422 OCURRIDO_EN_FUTURO`); un
  evento tardío es válido. `registrado_en` lo pone la base.
- **La evaluación de promesas y la atribución se piden** (`POST /evaluaciones-promesas`,
  `POST /atribuciones`) o las encuentra su backfill: no se abren solas. Una fecha de corte posterior a
  hoy es `422 AS_OF_FUTURO`, y una ventana sin pagos interpretados,
  `409 SIN_INTERPRETACION_DE_PAGOS`.
- **Atribuido no es causado, y compatible no es atribuido.** Cada respuesta de la atribución y de la
  evaluación lo dice en su `aviso`.
- **La historia no se pide: se abre sola** con cada dataset conformado. Mientras la ingesta sigue en
  proceso, `/historia` responde `409 CORRIDA_EN_PROCESO` o `PAGOS_EN_PROCESO`; una corrida de
  cartera/v1, o que no publicó, `404 SIN_DATASET_CONFORMADO`.

Por qué cada código es el que es (201 y no 202, 422 y no 400, cuándo 409, por qué un
archivo con registros inválidos no es un error HTTP, por qué una ejecución que termina `FALLIDA`
también nació con un `201`), y el resto de las decisiones, están en
[docs/decisiones.md](docs/decisiones.md).

## Sin Docker

Hace falta un PostgreSQL 16 (el del compose sirve: `docker compose up -d postgres`, puerto
5434) y [uv](https://docs.astral.sh/uv/).

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

Sin un worker corriendo, la API registra el trabajo pero nadie lo ejecuta: el flujo se queda en la
cola. `motor-cartera worker --una-vez` procesa a lo más un trabajo y sale, para probar o
diagnosticar. `cargar` registra la corrida y su trabajo en la misma cola, lo toma él mismo y hace
la ingesta en primer plano, con el mismo código que el worker; si se interrumpe, un worker la
termina cuando vence el lease. No crea flujo, así que las demás etapas se piden a mano, y termina
con código 1 si la corrida no publica, para que un script lo note.

Las versiones exactas de todas las dependencias están en `uv.lock`, y el CI y la imagen
instalan esas mismas. Si cambias una dependencia en `pyproject.toml`, corre `uv lock` y sube
los dos archivos juntos: con el lock desfasado, el CI falla. El lock sirve para cualquier
Python desde 3.12 (el piso de `requires-python` y la versión del CI), no solo para el que
tengas instalado.

## Pruebas

```bash
pytest
```

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

El CI tiene tres trabajos: la revisión de los archivos trackeados; lint, formato,
migraciones (suben, coinciden con los modelos y bajan) y pruebas contra una PostgreSQL de
servicio, que también cubren la cola durable, el worker, el flujo automático y los tres motores:
la agregación en la base, la transacción todo o nada, la concurrencia entre ejecuciones, la
idempotencia y la API del historial, incluidos resultados de otras versiones de las reglas; y el
`docker compose up` completo en un runner limpio, con PostgreSQL, migraciones, API y worker, y la
prueba de humo del flujo automático vía HTTP, con una cartera oficial hasta el ruteo, una ingesta
de pagos y un escenario de cuatro cortes y sus pagos hasta la Cuenta 360 y el motor de pagos;
comprueba que ni el backfill de la historia ni el del motor encuentran nada pendiente, baja los
contenedores sin borrar los volúmenes, los vuelve a levantar
y verifica que cada artefacto siga en el almacén con sus mismos bytes. Los benchmarks de escala,
del modelo histórico y del motor de pagos son un workflow aparte, que solo corre a mano.

## Arquitectura

```
src/motor_cartera/
├── config.py          Configuración desde el entorno (prefijo MC_)
├── contratos/         Qué forma deben tener los datos: cartera/v1 (cartera.py), las fuentes
│                      oficiales (fuente.py, cartera_v2.py y pagos.py) y su juicio vectorizado
├── fuentes/
│   ├── almacen.py     El almacén de artefactos por contenido (SHA-256, atómico, de solo lectura)
│   ├── artefactos.py  El artefacto en la base: primero el objeto, después su fila; la auditoría
│   ├── formatos.py    Qué es un archivo por su contenido: xlsx, csv o zip
│   ├── lotes.py       La fuente leída por lotes, con su fila y su hoja; la hoja compañera
│   ├── conformado.py  El dataset conformado en Parquet, reproducible
│   ├── geografia.py   El catálogo público del INEGI y cómo se resuelve un nombre, sin adivinar
│   └── proyeccion.py  operacional/v1: de cartera/v2 a Cuenta
├── ingesta/
│   ├── lectores.py    Excel, CSV y ZIP a nombres canónicos de cartera/v1
│   ├── corridas.py    La corrida: lee, juzga, decide y publica. La usan la API y el CLI
│   ├── fuente_oficial.py  Las dos pasadas de una fuente oficial: llaves globales y firma
│   ├── cartera_v2.py  cartera/v2: juicio, conformado, proyección y CARRIER, todo o nada
│   └── pagos.py       pagos/v1: su ingesta, su barrera y su conformado, sin deduplicar
├── atraso.py          Tramos de atraso: la única fuente de sus fronteras
├── segmentacion.py    El resumen por segmento, agregado en la base
├── decision/
│   ├── reglas.py      decision/v1: el núcleo puro y determinista, sin base ni framework
│   └── ejecuciones.py Aplica el núcleo sobre PostgreSQL, en una transacción: todo o nada
├── territorial/
│   ├── reglas.py      territorial/v1: el dominio puro (carga, orden y motivos), sin base
│   └── ejecuciones.py Agrega en PostgreSQL, aplica el núcleo y persiste: todo o nada
├── ruteo/
│   ├── reglas.py      ruteo/v1: coordenadas sintéticas, distancia, vecino más cercano y 2-opt
│   └── ejecuciones.py Lee las cuentas CAMPO, aplica el núcleo y publica rutas y paradas: todo o nada
├── historia/
│   ├── identidad.py   Los identificadores públicos deterministas (UUID versión 5)
│   ├── carga.py       Del Parquet conformado a PostgreSQL: lotes, CSV de Arrow y COPY
│   ├── ejecuciones.py historia/v1: abrir con su dataset, materializar todo o nada, equivalencia
│   │                  y conflicto de cortes
│   ├── presencia.py   Primera observación, salida, reingreso y continuidad: el núcleo puro
│   ├── backfill.py    La historia de los datasets que todavía no la tienen
│   └── cuenta360.py   Lo que se consulta de una cuenta: resumen, historia, eventos y pagos
├── motor_pagos/
│   ├── reglas.py      motor-pagos/v1: el núcleo puro (grupos, clasificaciones, reversos, recuperación)
│   ├── firmas.py      Las huellas (firma exacta y llave histórica) y el movimiento_id, en Python y SQL
│   ├── ejecuciones.py Abrir las ventanas con la historia de sus pagos e interpretarlas en
│   │                  PostgreSQL, por conjuntos y todo o nada
│   ├── contexto.py    El contexto temporal de un movimiento entre los snapshots: el núcleo puro
│   ├── backfill.py    Las ventanas sin interpretación vigente, desactualizadas o por conciliar
│   └── consultas.py   Lo que se consulta: ejecuciones, resultados, movimientos, su evidencia y el
│                      resumen de pagos de una cuenta
├── lifecycle/
│   ├── reglas.py      lifecycle/v1: el vocabulario, la coherencia de una gestión, los datos
│   │                  personales y la huella de una petición, sin base
│   ├── registro.py    Una escritura con su llave de idempotencia, en una transacción
│   ├── importacion.py cargar-lifecycle: JSONL por COPY y tablas temporales, por fases, todo o nada
│   └── consultas.py   Gestiones, promesas, convenios, la línea de tiempo y el resumen de una cuenta
├── evaluacion/
│   ├── reglas.py      evaluacion-promesa/v1: el núcleo puro
│   ├── ejecuciones.py La evaluación de una cartera a una fecha, en PostgreSQL, todo o nada
│   ├── backfill.py    Las evaluaciones que faltan a una fecha (backfill-lifecycle)
│   └── consultas.py   Las evaluaciones y lo que concluyeron de cada promesa
├── atribucion/
│   ├── reglas.py      atribucion/v1: el núcleo puro (candidatas, clasificación y motivos)
│   ├── ejecuciones.py La atribución de una ventana en PostgreSQL, por conjuntos y todo o nada
│   ├── backfill.py    Las ventanas sin atribución al día (backfill-atribucion)
│   └── consultas.py   Las ejecuciones, sus resultados con sus candidatas y la última de una cuenta
├── orquestacion/
│   ├── cola.py        La cola durable: tomar con SKIP LOCKED, lease, latido, devolver y cerrar
│   ├── worker.py      El worker: ejecuta cada trabajo con su latido y lo cierra con lo que sigue
│   ├── flujo.py       El flujo automático: encola, encadena las etapas, detiene y reanuda
│   └── objetivos.py   El recurso de cada tipo de trabajo y sus estados terminales
├── db/                Modelo: la corrida, sus cuentas y rechazos, las ejecuciones con lo que
│                      publican (decisiones por cuenta, resultados por municipio, rutas y paradas),
│                      la orquestación (flujos y trabajos), la evidencia (artefactos, datasets
│                      conformados, hojas compañeras e ingestas de pagos con sus rechazos) y el
│                      modelo histórico (cuentas canónicas, cortes, snapshots, pagos observados
│                      y sus ejecuciones), el motor de pagos (sus ejecuciones, un resultado por
│                      observación y los movimientos canónicos), el lifecycle (eventos, gestiones,
│                      visitas, promesas, convenios y cuotas), la evaluación de promesas y la
│                      atribución (sus ejecuciones, sus resultados y sus candidatas)
├── generador/         Único origen de datos del proyecto: la cartera de cartera/v1 (sintetico.py),
│                      las fuentes oficiales, sus perfiles y el escenario longitudinal (oficial.py),
│                      y el lifecycle sintético de cada periodo del escenario (lifecycle.py)
├── api/               FastAPI: corridas, pagos, orquestación, cartera, decisiones, territorial,
│                      ruteo, cuentas (Cuenta 360), historia, motor de pagos, movimientos,
│                      gestiones, acuerdos (promesas y convenios), lifecycle, evaluaciones y
│                      atribuciones; subidas, esquemas, errores y autenticación
└── cli.py             Comandos: generar, generar-oficial, generar-escenario, cargar, cargar-pagos,
                       cargar-lifecycle, verificar-fuentes, backfill-historia,
                       backfill-motor-pagos, backfill-lifecycle, backfill-atribucion y worker
migraciones/           Versiones de Alembic
scripts/               Prueba de humo, control de archivos trackeados, benchmarks de escala, del
                       modelo histórico, del motor de pagos y del lifecycle, y actualización del
                       catálogo del INEGI
docs/                  decisiones.md (por qué está hecho así), fuentes.md (las fuentes oficiales),
                       historia.md (el modelo histórico), cuenta_360.md (su API), motor_pagos.md
                       (el motor de pagos), lifecycle.md (el lifecycle de cobranza y la evaluación
                       de promesas), atribucion.md (la atribución operativa), los diccionarios de
                       CARTERA y PAGOS, y carrier.md
```

Todo lo que se escribe cuelga de una **Corrida**. Si alguien pregunta de dónde salió un
número, la respuesta es una fila de esa tabla: qué archivo llegó (con su firma SHA-256) y
qué cartera traía (con la firma de su contenido, la misma en cualquier formato), con qué
tolerancia y qué versión del contrato se juzgó, cuántos registros se leyeron, validaron y
rechazaron, y por qué.

Desde v0.6.0, la corrida apunta a su **ArtefactoFuente**: los bytes exactos que llegaron, en el
almacén. Una corrida de cartera/v2 tiene además su **DatasetConformado** (el Parquet de sus
registros válidos, otro artefacto) y su **HojaCompanera** (la auditoría de CARRIER). Los pagos
cuelgan de una **IngestaPagos**, que no es una corrida: apunta a su artefacto, tiene sus rechazos,
su conformado y su trabajo en la cola, y no publica cuentas. Del artefacto al conformado y del
conformado a cada fila de origen, el linaje está en la base y en cada registro.

Las decisiones cuelgan de una **EjecucionDecision**, y la ejecución, de la corrida que decidió.
Si alguien pregunta por qué a una cuenta se le recomienda `CAMPO`, la respuesta son sus motivos
y una fila de esa tabla: con qué versión de las reglas se decidió, cuándo, cómo terminó y
cuántas cuentas evaluó y publicó. Cada capa hace una sola cosa: `reglas.py` decide una cuenta
sin saber de bases ni de HTTP, `ejecuciones.py` aplica ese núcleo a toda la corrida y publica
todo o nada, y `api/decisiones.py` solo traduce el resultado a HTTP.

Los resultados por municipio cuelgan de una **EjecucionTerritorial**, y esta, de la ejecución de
decisión que organizó, no de la corrida: una corrida se puede decidir con varias versiones de las
reglas, y lo territorial tiene que decir exactamente de qué decisiones salió. Las capas se
repiten: `territorial/reglas.py` es el dominio puro, que evalúa y ordena municipios ya
agregados; `territorial/ejecuciones.py` agrega en PostgreSQL, aplica ese núcleo y persiste todos
los municipios o ninguno, en una transacción; y `api/territorial.py` solo traduce a HTTP.

Las rutas cuelgan de una **EjecucionRuteo**, y esta, de la ejecución territorial que ruteó:
`Corrida → EjecucionDecision → EjecucionTerritorial → EjecucionRuteo → RutaTerritorial →
ParadaRuta`. Cada ruta apunta al `ResultadoTerritorial` de su municipio y cada parada a la
`DecisionCuenta` que visita; la clave del municipio y el cliente se leen por JOIN, no se copian.
`ruteo/reglas.py` calcula las coordenadas sintéticas, la distancia, el vecino más cercano y el
2-opt sin saber de bases; `ruteo/ejecuciones.py` obtiene las cuentas `CAMPO` en una sola consulta,
aplica el núcleo municipio por municipio y publica todas las rutas y paradas o ninguna; y
`api/ruteo.py` solo traduce a HTTP.

La orquestación cuelga de esos mismos recursos sin cambiarlos. Un **FlujoOrquestacion** por cada
corrida subida por la API apunta a la ejecución de cada etapa a la que llegó; un
**TrabajoOrquestacion** por recurso dice qué motor ejecutar, quién lo tiene, hasta cuándo y cuántas
veces se intentó; y el **ArchivoCorrida**, que desde v0.6.0 solo conserva el archivo de las corridas
que v0.5 dejó en la cola.
`orquestacion/` no sabe qué calcula cada motor: llama a su servicio y lee el estado de su recurso.
Por eso v0.5.0 no cambió `cartera/v1`, `decision/v1`, `territorial/v1` ni `ruteo/v1`.

El modelo histórico cuelga de los datasets conformados, no de las corridas: cada
**EjecucionHistoria** materializa un dataset, y un **CorteCanonico** dice de qué dataset salió.
`CuentaCanonica → SnapshotCuenta → CorteCanonico → EjecucionHistoria → DatasetConformado →
ArtefactoFuente (Parquet) → ArtefactoFuente (original)`: cada snapshot guarda su `source_row` y su
`source_sheet`, y cada **PagoObservado**, su dataset y su fila. `CuentaCanonica` no es `Cuenta`: esta
sigue siendo la foto operacional de una corrida, la única que leen los motores v1. `historia/`
tampoco sabe de la cola: `orquestacion/` ejecuta su trabajo `HISTORIA` como el de cualquier motor.

El motor de pagos cuelga de los pagos observados, sin cambiarlos: cada **EjecucionMotorPagos**
interpreta una ventana, y lo que concluye se explica hasta la fuente:
`MovimientoEconomicoCanonico → ResultadoPagoObservado → PagoObservado → DatasetConformado →
IngestaPagos → ArtefactoFuente (original)`, y del movimiento a su `CuentaCanonica` y a sus
snapshots. `motor_pagos/reglas.py` es el núcleo puro, `motor_pagos/ejecuciones.py` aplica las
mismas reglas en PostgreSQL por conjuntos, y `api/motor_pagos.py` y `api/movimientos.py` solo
traducen a HTTP. Ningún motor v1 lee los movimientos.

El lifecycle cuelga de las cuentas canónicas, no de una fuente: cada **EventoLifecycle** es de una
`CuentaCanonica`, y su detalle (**GestionCobranza**, **VisitaCampo**, **PromesaPago**,
**ConvenioCobranza**, **CuotaConvenio**) cuelga del evento que lo registró. Una evaluación
(**EjecucionEvaluacionPromesas** → **EvaluacionPromesa**) y una atribución (**EjecucionAtribucion**
→ **AtribucionMovimiento** → **CandidatoAtribucion**) leen el lifecycle y los movimientos del motor
de pagos sin cambiarlos: `AtribucionMovimiento → GestionCobranza → EventoLifecycle → CuentaCanonica`,
y `→ MovimientoEconomicoCanonico → ResultadoPagoObservado → PagoObservado → DatasetConformado →
ArtefactoFuente`. Los núcleos puros (`lifecycle/reglas.py`, `evaluacion/reglas.py`,
`atribucion/reglas.py`) no saben de la base, y una prueba compara cada uno con su SQL.

## Limitaciones conocidas

**De las fuentes oficiales (v0.6.0).** Lo que v0.6.0 deja a propósito para después:

- **El almacén es un directorio local.** Sin réplica, sin respaldos automáticos y sin *object
  storage* en la nube: un disco que se pierde se lleva la evidencia, y respaldarlo es de la
  operación. GCS o S3, detrás de la misma interfaz, son de v0.18.
- **Los huérfanos no se recolectan.** Si la base falla después de guardar un objeto, el objeto se
  queda en el almacén sin una fila que lo registre. Ocupa disco y nada más; no hay política de
  retención ni de limpieza.
- **Leer un xlsx grande es lento.** openpyxl, en modo de solo lectura, lee del orden de 90,000
  celdas por segundo: una cartera XL en xlsx tarda varios minutos solo en leerse. Para XL y XXL,
  csv o zip.
- **CARRIER se audita, no se publica**, y sus advertencias no bloquean nada.
- **La ingesta de pagos no deduplica, no concilia y no atribuye.** pagos/v1 acepta cada
  movimiento tal como llega y no lo cruza con las cuentas de un corte. Desde v0.8, el motor de
  pagos los interpreta aparte; atribuir cada pago a una gestión queda para después del lifecycle
  de v0.9.
- **La geografía es por municipio.** La proyección resuelve el estado y la población a claves
  municipales del INEGI; localidades, colonias, códigos postales y coordenadas son de v0.11. El
  catálogo es una foto (consultada el 2026-10-05) que se actualiza con su script.
- **El escenario longitudinal no es un modelo financiero**: es coherencia básica y determinismo.
- **La escala medida es la de la ingesta, la historia y el motor de pagos.** Un benchmark mide la
  ingesta de cartera/v2 y de pagos/v1 hasta 1,000,000 de cuentas; otro, la historia de 12 cortes XL
  con sus pagos, y otro, el motor de pagos sobre esos mismos 12 cortes. Los motores operacionales
  (decisión, territorial y ruteo) no están medidos a esa escala, y cartera/v1 todavía lee su
  archivo entero en memoria.

**Del modelo histórico (v0.7.0).** Lo que `historia/v1` deja a propósito para después:

- **Un conflicto de corte no se corrige.** Si llega otra cartera de un corte que ya tiene historia,
  con otro contenido, su historia falla con `CORTE_CANONICO_CONFLICTIVO` y el corte publicado no
  cambia. No hay todavía una corrección explícita (reemplazar un corte, con su motivo y su
  auditoría); mientras tanto, el conflicto queda registrado con las dos firmas.
- **Solo la cartera oficial tiene historia.** Las corridas de `cartera/v1` no publican un dataset
  conformado, así que no tienen cortes canónicos ni snapshots.
- **La geografía de un snapshot es la del catálogo de su versión.** Las claves del INEGI se
  resuelven al materializar, con el catálogo del código; si el catálogo cambia, tiene que cambiar
  también la versión del modelo, como la de la proyección.
- **Los pagos observados no son recuperación.** No se deduplican, no se concilian, no se interpretan
  como reversos ni se atribuyen, y la API no los suma. Desde v0.8 eso lo hace el motor de pagos,
  en sus propias entidades.
- **Los eventos se calculan al consultar.** Con unos cientos de cortes por cartera es inmediato; con
  miles, habría que medir otra vez antes de guardarlos.
- **Una sola búsqueda.** Una cuenta se busca por su `CLIENTE_UNICO` exacto en la cartera del
  sistema; no hay listados ni filtros de cuentas, ni consultas de varias carteras.
- **Sin particionado.** Las tablas que crecen con cada corte (`snapshot_cuenta`, `pago_observado`)
  no están particionadas: con sus índices, ninguna consulta de una cuenta las recorre enteras, y el
  benchmark lo mide. El mantenimiento de tablas de decenas de millones de filas (VACUUM, respaldos)
  es de la operación y de v0.18.
- **Medido en una sola máquina, que no era dedicada.** Con el mismo tamaño, la escritura de un corte
  varió de 12 a 70 s, y la de un archivo de pagos, de 10 a 97 s; dos de las más lentas coinciden
  con checkpoints de PostgreSQL, y las demás con ningún evento de la base. La historia tiene que
  medirse otra vez en el servidor de producción, con su configuración (v0.18).

**Del motor de pagos (v0.8.0).** Lo que `motor-pagos/v1` deja a propósito para después:

- **Una coincidencia ambigua no se resuelve.** Dos observaciones con la misma llave histórica y otro
  campo distinto no fundan movimiento ni suman: su importe se informa aparte. Resolverlas necesita
  reglas con evidencia.
- **Un reverso se empareja solo si la pareja es aislada.** Con varios candidatos, un candidato
  ambiguo o un original disputado, queda como posible reverso: resta de la neta y no anula nada. La
  ventana de 30 días es una regla de `v1`, sin evidencia empírica.
- **No lee `Concepto_Cálculo`, `Captación` ni `Cobranza_Total`** para decidir signos ni tipos.
- **No atribuye pagos a gestiones.** `Gestor`, `Fecha_de_Gestion` y `Campaña` quedan como atributos
  observados. Desde v0.9.0, `atribucion/v1` asocia cada pago interpretado con las gestiones que
  registra el lifecycle, sin causalidad y sin leer esos atributos de la fuente.
- **Un pago sin cuenta no se concilia solo cuando llega su corte**: lo hace
  `backfill-motor-pagos --reconciliar`, con una interpretación nueva.
- **Cada interpretación es completa.** Una llegada tardía reinterpreta entero cada mes que toca, y
  su vecina si cambia su contexto, aunque ninguna conclusión cambie: en el XL, de 5 a 7 minutos, de
  1.8 a 2.2 GB de WAL y unos 0.6 GB más por mes. Las interpretaciones anteriores se conservan; su
  retención es de la operación (v0.18).
- **Las consultas de toda la cartera, o de una ventana sin el cliente, recorren lo que filtran**: en
  el XL, de 1.6 a 4.9 s de mediana. Con un cliente o una cuenta, 4 a 9 ms. Un índice por fecha las
  aceleraba, pero hacía de 2.3 a 2.9 veces más cara la escritura de cada interpretación (decisión 98).
- **La recuperación interpretada no es contabilidad.** El saldo oficial sigue siendo el del snapshot.
- **Medido en una sola máquina, que no era dedicada**, con poca memoria disponible: el tiempo de un
  mismo mes varió de 230 a 422 s entre interpretaciones. Se mide otra vez en producción (v0.18).

**Del lifecycle y la atribución (v0.9.0).** Lo que v0.9.0 deja a propósito para después:

- **La atribución no mide causalidad**, ni el aporte de una gestión, de un canal o de un actor, y no
  elige entre candidatas: un pago con dos gestiones con contacto antes queda `AMBIGUA`. La ventana
  de 30 días es una política del demo, sin evidencia empírica.
- **No hay `GestorCanonico`**: quien hizo una gestión es un `actor_ref` opaco, sin catálogo ni
  relación con el `Gestor` de `pagos/v1`.
- **Un convenio no es un ledger**: ningún pago se aplica a una cuota, y su estado no se evalúa.
- **Las visitas no tienen geografía**: sin GPS, rutas, zonas ni geocercas (v0.11–v0.13).
- **Lo que un corte dice de una promesa o de un plan no se vuelve un evento**: se muestra como
  `OBSERVACION_EN_CORTE`, y ninguna regla decide todavía si dos observaciones son la misma promesa.
- **La evaluación y la atribución no se abren solas**: las piden la API o su backfill. Una gestión
  tardía deja desactualizada la atribución de su ventana hasta que se vuelve a pedir.
- **La importación es para datos sintéticos e integraciones de prueba**, no una fuente oficial: no
  tiene contrato de archivo, almacén ni historia, y una llave usada al importar es de la importación.
- **Escribir es lo que cuesta.** Cargar un periodo XL (unos 230,000 eventos) toma de 42 a 207 s y
  de 0.3 a 0.55 GB de WAL; atribuir un mes de un millón de pagos, de 2 a 4 minutos, y cada
  atribución nueva de una ventana escribe otra vez todos sus pagos. La primera página de los pagos
  ambiguos de una ventana de un millón tarda 0.5 s: es una consulta global, sin índice propio
  (decisión 112).
- **Medido en una sola máquina, que no era dedicada**: el mismo tamaño de archivo se cargó a 1,119 o
  a 5,717 eventos por segundo según los checkpoints y el autovacuum. Se mide otra vez en producción
  (v0.18).

**De la orquestación durable.** La cola, el worker y el flujo resuelven que el trabajo sobreviva y
se recupere, no la operación en producción. Esto le toca a **v0.18 — Cloud + observabilidad +
hardening**:

- **PostgreSQL es la cola.** No hay un broker dedicado: los workers preguntan por trabajo cada
  `MC_WORKER_POLL_SEGUNDOS`, y la cola comparte la base con todo lo demás.
- **Un worker procesa un trabajo a la vez.** Escalar es correr más procesos worker
  (`docker compose up --scale worker=3`); no hay autoscaling.
- **Tres niveles de prioridad, y nada más.** Entre los trabajos que ya se pueden tomar van primero
  los operacionales, después los `HISTORIA` y al final los `MOTOR_PAGOS`; dentro de cada nivel, en
  el orden en que se crearon.
  No hay prioridades por despacho, por cartera ni por urgencia.
- **Sin cola de mensajes muertos externa.** Un trabajo que agota sus intentos queda `FALLIDO` en su
  tabla, con su recurso `FALLIDA` y su flujo `DETENIDO`, y nadie avisa: se ve consultando la API.
- **Sin métricas, alertas ni trazas distribuidas productivas.** Hay bitácora de la API y del
  worker, y los flujos y trabajos se consultan por la API.

Y dos que son del diseño, no pendientes:

- **Entrega al menos una vez.** Un motor se puede ejecutar más de una vez sobre el mismo recurso si
  un worker muere en el momento justo; lo que no se repite es la publicación.
- **La migración `0006` cierra lo que encontró en proceso.** Cada corrida o ejecución que estaba
  `EN_PROCESO` al migrar queda `FALLIDA`, con un motivo que lo dice, y su `downgrade` no la reabre.
  Las corridas que publicaron antes de v0.5.0 no tienen flujo: sus etapas se piden a mano.

**Del resto del sistema.**

- **`territorial/v1` solo consume `decision/v1`.** Las decisiones de otra versión dan
  `409 DECISION_NO_TERRITORIALIZABLE`; organizarlas pedirá otra versión de las reglas
  territoriales.
- **Coordenadas sintéticas.** Las rutas de `ruteo/v1` se trazan sobre un plano sintético por
  municipio: no hay geocodificación, calles, tráfico ni tiempos reales, y las distancias son metros
  de ese plano, no de una calle.
- **Una ruta por municipio, sin gestores.** Cada municipio con trabajo de campo tiene una sola
  ruta sobre todas sus cuentas `CAMPO`. No hay gestores, vehículos, capacidades, turnos ni ventanas
  de tiempo, ni rutas que crucen municipios: no es un VRP multi-vehículo.
- **El 2-opt no está medido con municipios grandes.** Con la cartera por omisión, trazar todas
  las rutas toma alrededor de un segundo, pero cada pasada del 2-opt es `O(n²)` en las paradas de
  un municipio, y un municipio con miles de cuentas de campo no está medido.
- **Una cartera se publica una vez por archivo, no por contenido.** La misma cartera en
  xlsx y en csv tiene dos firmas de archivo y se publica dos veces en la capa operacional. Su firma
  de contenido, que es la misma, lo deja a la vista; desde v0.7.0 el modelo histórico la reconoce
  como una fuente equivalente y no duplica su corte, pero la operación todavía no lo impide.
- **El control de archivos revisa lo trackeado, no la historia.** Un archivo que se subió y
  después se borró sigue en la historia, y en un repositorio público ya salió. Un csv o un
  volcado en SQL plano con otro nombre no se reconocen por sus bytes, y el control confía en el
  `.gitignore` del mismo commit: quitar una regla también la quita del control.
- **Límites de tamaño incompletos.** El tope de subida (`MC_TAMANO_MAXIMO_MB`) se aplica al
  copiar el archivo al almacén, por bloques, pero el servidor ya recibió la subida completa, y no
  hay tope a lo que un zip descomprime. El límite real le toca a un proxy delante de la API.
- **cartera/v1 no valida contra el catálogo INEGI**: revisa la forma de las claves, no que
  existan, y el Motor Territorial agrupa por esas mismas claves. Y en cartera/v1 un saldo con más
  de dos decimales se redondea al guardarse en lugar de rechazarse. cartera/v2 resuelve sus claves
  con el catálogo y rechaza un importe con más de dos decimales.
- **Los motores se miden con la cartera de la prueba de humo.** El Decision Engine, el Motor
  Territorial y el Motor de Ruteo se prueban en el CI con sus 9,800 cuentas y no están medidos a
  la escala de 500,000.
- **Una sola API key**, sin usuarios, permisos ni rotación.
- **El `docker compose up` se prueba en el CI**, en Linux. La máquina donde se desarrolla el
  proyecto no tiene Docker, así que en Windows y macOS no está probado.

## Desarrollo asistido por IA

Este proyecto se desarrolla con asistentes y agentes de IA como herramientas de ingeniería:
para explorar alternativas, escribir código y pruebas, y revisar cambios. Las decisiones de
arquitectura, los criterios de aceptación, la revisión y la responsabilidad sobre cada cambio
siguen bajo control humano.

Ningún cambio se da por terminado solo porque lo haya generado un agente. Las pruebas, el CI
y la revisión técnica son parte de la aceptación, con el mismo criterio para cualquier
cambio.

## Licencia

Todos los derechos reservados. El código es público para que se pueda leer y evaluar, pero
no se concede licencia para copiarlo, modificarlo ni redistribuirlo sin permiso del autor.
Ver [LICENSE](LICENSE).
