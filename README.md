# motor-cartera

Motor de ingesta, validación, segmentación, decisión y priorización territorial de cartera de
crédito al consumo, construido sobre **datos sintéticos**, con una API REST para operarlo.

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

## Datos

**Ningún dato real entra a este repositorio.** Todo lo que el sistema procesa lo produce el
generador sintético incluido. Las claves geográficas son claves reales del Marco
Geoestadístico del INEGI (público) de cinco entidades, con casi todas las cuentas en
Puebla; el resto son datos inventados con distribuciones parecidas a las reales: saldos
con cola larga, atraso por tramos y concentración geográfica desigual. El generador mete filas inválidas a
propósito para que el contrato tenga de dónde agarrarse.

El `.gitignore` lo cuida por nombre, pero solo frena lo que todavía no está trackeado. Lo
trackeado lo revisa `scripts/verificar_archivos_trackeados.py` en el CI: falla si entró algo
que el `.gitignore` excluye (con `git add -f`, o en mayúsculas donde git las distingue) o una
hoja de cálculo o un zip con cualquier nombre. Antes de un commit también se corre a mano:

```bash
python scripts/verificar_archivos_trackeados.py
```

## Estado

Hay tres versiones terminadas, y cada una es una rebanada vertical que funciona de punta a punta:

- **v0.1.0 ✅ Ingesta + certificación** (la fase 1): una cartera se publica solo si pasa el
  contrato, y lo que no pasa queda con su motivo.
- **v0.2.0 ✅ Decision Engine**: sobre la cartera publicada, una decisión explicable por cuenta.
- **v0.3.0 ✅ Motor Territorial**: sobre las decisiones publicadas, la carga de campo de cada
  municipio y el orden en que conviene atenderlos.

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
- [x] `docker compose up` levanta todo; CI con PostgreSQL y prueba del compose en limpio

Lo que sigue:

- [ ] **v0.4.0 — Motor de Ruteo**: qué ruta física seguir, en qué secuencia visitar las cuentas y
  con qué distancias y tiempos
- [ ] **v0.5.0 — Orquestación durable**: colas, reintentos y trabajo que sobrevive a un reinicio
- [ ] **v0.6.0 — Cloud + observabilidad**: despliegue en nube, métricas, trazas y alertas

Lo que está frágil o pendiente, sin maquillar, está en
[Limitaciones conocidas](#limitaciones-conocidas).

## Arranque rápido

```bash
docker compose up --build
```

Eso levanta PostgreSQL, aplica las migraciones y arranca la API en
<http://localhost:8000>, sin pasos manuales. La documentación interactiva está en
<http://localhost:8000/docs>. La clave de desarrollo es `clave-local-de-desarrollo`; se
cambia con la variable `MC_API_KEY`.

## El flujo completo

**1. Generar una cartera sintética.** El archivo queda en `./datos`, que el contenedor
comparte con tu máquina:

```bash
docker compose exec api motor-cartera generar --destino datos/cartera.xlsx
```

Por omisión son 10,000 cuentas con 2 % de filas inválidas a propósito (`--n`,
`--tasa-invalidas`, `--semilla`, `--fecha-corte`). El formato sale de la extensión.

**2. Lanzar una corrida por la API.**

```bash
curl -i -H "X-API-Key: clave-local-de-desarrollo" -F "archivo=@datos/cartera.xlsx" http://localhost:8000/corridas
```

Responde `201` de inmediato, con la corrida `EN_PROCESO` y su dirección en `Location`; el
archivo se procesa en segundo plano.

**3. Consultar su estado** hasta que sea `EXITOSA`, `RECHAZADA` o `FALLIDA`:

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

**4. Ver los rechazos, cada uno con su fila y su motivo:**

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

**5. Consultar el resumen segmentado** de la cartera vigente:

```bash
curl -H "X-API-Key: clave-local-de-desarrollo" "http://localhost:8000/cartera/resumen?por=canal&por=tramo_atraso"
```

Devuelve cuentas, saldo y saldo promedio por segmento, junto con el `run_id` del que sale
cada número.

**6. Decidir la corrida** con el Decision Engine:

```bash
curl -i -X POST -H "X-API-Key: clave-local-de-desarrollo" http://localhost:8000/corridas/<run_id>/decisiones
```

Es síncrono: cuando responde, ya decidió todas las cuentas. Devuelve `201` con la ejecución
terminada y su dirección en `Location`:

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

**7. Consultar la ejecución** por su `decision_run_id`: versión de las reglas, estado, tiempos
y conteos.

```bash
curl -H "X-API-Key: clave-local-de-desarrollo" http://localhost:8000/decisiones/<decision_run_id>
```

**8. Listar el historial de la corrida:** todas sus ejecuciones, en cualquier estado y versión
de las reglas, de la más reciente a la más antigua.

```bash
curl -H "X-API-Key: clave-local-de-desarrollo" http://localhost:8000/corridas/<run_id>/decisiones
```

**9. Ver la decisión de cada cuenta**, con sus motivos:

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

**10. Organizar las decisiones por municipio** con el Motor Territorial:

```bash
curl -i -X POST -H "X-API-Key: clave-local-de-desarrollo" http://localhost:8000/decisiones/<decision_run_id>/territoriales
```

También es síncrono: cuando responde, ya organizó todos los municipios. Devuelve `201` con la
ejecución terminada y su dirección en `Location`:

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

**11. Consultar la ejecución territorial** por su `territorial_run_id`, con el
`decision_run_id` de las decisiones que organizó y el `run_id` de su corrida:

```bash
curl -H "X-API-Key: clave-local-de-desarrollo" http://localhost:8000/territoriales/<territorial_run_id>
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

Todo el flujo, con la verificación de cada código HTTP, está en un solo script que corre igual
en tu máquina que en el CI; al final pide decidir la misma corrida y organizar las mismas
decisiones otra vez, y exige los dos `409`. Necesita un archivo que no se haya subido antes,
porque el mismo archivo no se publica dos veces:

```bash
docker compose exec api motor-cartera generar --destino datos/humo.xlsx --semilla 7
python scripts/prueba_de_humo.py datos/humo.xlsx
```

Solo usa la biblioteca estándar de Python; también corre dentro del contenedor con
`docker compose exec api python scripts/prueba_de_humo.py datos/humo.xlsx`.

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
`ALTA` a `MUY_ALTA` por su saldo y se recomienda por `CAMPO`: es la del ejemplo del paso 9.

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

**Tres versiones, tres preguntas.** Ninguna es la versión del paquete:

- `cartera/v1` (`VERSION_CONTRATO`): qué datos entran;
- `decision/v1` (`VERSION_REGLAS_DECISION`): qué decisión recibe cada cuenta;
- `territorial/v1` (`VERSION_REGLAS_TERRITORIAL`): cómo se organiza por territorio el trabajo de
  campo.

`territorial/v1` solo organiza decisiones de `decision/v1`. La compatibilidad es un literal:
cuando el Decision Engine decida con `decision/v2`, `territorial/v1` no la acepta sola. Cada
ejecución territorial guarda con qué reglas se calculó y cuelga de la ejecución de decisión que
organizó, así que de un municipio se llega a sus decisiones, de ellas a la corrida y de la
corrida al archivo.

**v0.3 prioriza; v0.4 traza rutas.** v0.3.0 responde dónde se concentra la carga operativa de
campo y qué municipios conviene atender primero. v0.4.0 responderá qué ruta física seguir, en
qué secuencia visitar las cuentas y con qué distancias y tiempos. Por eso v0.3.0 no tiene
coordenadas, distancias, TSP, VRP, gestores ni rutas: `posicion_campo` es una prioridad
territorial, no una parada en un recorrido.

## La API

| Método y ruta | Qué hace | Respuestas |
|---|---|---|
| `POST /corridas` | Recibe el archivo, registra la corrida y la procesa en segundo plano | 201, 401, 409, 413, 415, 422 |
| `GET /corridas/{run_id}` | Estado: conteos, tiempos y resultado | 200, 401, 404, 422 |
| `GET /corridas/{run_id}/rechazos` | Registros rechazados con su fila y motivo, paginados | 200, 401, 404, 409, 422 |
| `GET /cartera/resumen` | Cuentas y saldo por segmento de la cartera vigente, paginado | 200, 401, 404, 409, 422 |
| `POST /corridas/{run_id}/decisiones` | Decide cada cuenta de una corrida `EXITOSA` con `decision/v1`, en la misma petición | 201, 401, 404, 409, 422 |
| `GET /corridas/{run_id}/decisiones` | Historial: todas las ejecuciones de la corrida, la más reciente primero, paginado | 200, 401, 404, 422 |
| `GET /decisiones/{decision_run_id}` | Una ejecución: versión de las reglas, estado, tiempos y conteos | 200, 401, 404, 422 |
| `GET /decisiones/{decision_run_id}/cuentas` | La decisión de cada cuenta con sus motivos, paginada; solo de una ejecución `EXITOSA` | 200, 401, 404, 409, 422 |
| `POST /decisiones/{decision_run_id}/territoriales` | Organiza por municipio, con `territorial/v1` y en la misma petición, las decisiones de una ejecución `EXITOSA` de `decision/v1` | 201, 401, 404, 409, 422 |
| `GET /decisiones/{decision_run_id}/territoriales` | Historial: todas las ejecuciones territoriales de esas decisiones, la más reciente primero, paginado | 200, 401, 404, 422 |
| `GET /territoriales/{territorial_run_id}` | Una ejecución territorial: versión de las reglas, estado, tiempos y conteos | 200, 401, 404, 422 |
| `GET /territoriales/{territorial_run_id}/municipios` | Cada municipio con su carga, su lugar y su motivo, en orden de prioridad y paginado; solo de una ejecución `EXITOSA` | 200, 401, 404, 409, 422 |
| `GET /salud` | La API vive y la base contesta. No pide clave | 200, 503 |

Todas las respuestas de error tienen la misma forma, también las que genera el framework:

```json
{
  "codigo": "ARCHIVO_YA_PUBLICADO",
  "mensaje": "Este archivo ya lo publico la corrida 4cce3e0d-….",
  "detalles": [],
  "run_id": "4cce3e0d-…"
}
```

El cliente compara `codigo`, que es estable; `mensaje` es para personas. En `/docs`, cada
ruta lista sus códigos de error con un ejemplo de cada uno.

Dos reglas del Decision Engine que un cliente tiene que conocer:

- **`201` quiere decir que la ejecución se creó, no que el motor tuvo éxito.** El POST de
  decisiones responde `201` con `Location` cuando la ejecución ya existe y terminó, tanto si
  quedó `EXITOSA` como `FALLIDA`. Una `FALLIDA` no publica ninguna decisión, trae el motivo en
  `detalle` y se reintenta con otro POST. El código HTTP describe la petición; el `estado`,
  cómo terminó el motor.
- **Una corrida se decide con éxito una sola vez por versión de las reglas.** Si ya tiene una
  ejecución `EXITOSA` con `decision/v1`, el POST responde `409 DECISION_YA_GENERADA`, sin
  `Location` y sin nombrar la ejecución. La que publicó se encuentra en
  `GET /corridas/{run_id}/decisiones`, y su detalle en `GET /decisiones/{decision_run_id}`.

El Motor Territorial sigue las mismas dos reglas:

- **D1: `201` con `EXITOSA` o con `FALLIDA`.** `POST /decisiones/{decision_run_id}/territoriales`
  responde `201` con `Location: /territoriales/{territorial_run_id}` siempre que crea la
  ejecución. Una `FALLIDA` no publica ningún municipio, trae el motivo en `detalle` y se
  reintenta con otro POST.
- **D2: `409 TERRITORIAL_YA_GENERADO`, sin `Location`.** Unas decisiones se organizan con éxito
  una sola vez por versión de las reglas. Si ya lo están con `territorial/v1`, el POST responde
  `409` sin `Location` y sin nombrar la ejecución; la que publicó se descubre en el historial,
  `GET /decisiones/{decision_run_id}/territoriales`. Una ejecución de decisión que no terminó
  `EXITOSA` o que no es de `decision/v1` da `409 DECISION_NO_TERRITORIALIZABLE`, y no se
  registra nada.

Por qué cada código es el que es (201 y no 202, 422 y no 400, cuándo 409, por qué un
archivo con registros inválidos no es un error HTTP, por qué una ejecución `FALLIDA` también
es `201`), y el resto de las decisiones, están en [docs/decisiones.md](docs/decisiones.md).

## Sin Docker

Hace falta un PostgreSQL 16 (el del compose sirve: `docker compose up -d postgres`, puerto
5434) y [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env
uv sync --extra dev                                  # .venv con las versiones del uv.lock
source .venv/bin/activate                            # en Windows: .venv\Scripts\activate
alembic upgrade head
motor-cartera generar --destino datos/cartera.xlsx
motor-cartera cargar datos/cartera.xlsx              # la misma corrida, sin pasar por la API
uvicorn --factory motor_cartera.api.app:crear_app --reload
```

`cargar` usa exactamente el mismo proceso que la API y termina con código 1 si la corrida
no publica, para que un script lo note.

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
orden en que llegan los municipios.

El CI tiene tres trabajos: la revisión de los archivos trackeados; lint, formato,
migraciones (suben, coinciden con los modelos y bajan) y pruebas contra una PostgreSQL de
servicio, que también cubren el Decision Engine y el Motor Territorial: la agregación en la
base, la transacción todo o nada, la concurrencia entre ejecuciones, la idempotencia y la API
del historial, incluidos resultados de otras versiones de las reglas; y el `docker compose up`
completo en un runner limpio, con la prueba de humo de la ingesta, del Decision Engine y del
Motor Territorial vía HTTP.

## Arquitectura

```
src/motor_cartera/
├── config.py          Configuración desde el entorno (prefijo MC_)
├── contratos/         Qué forma deben tener los datos; el juicio registro por registro
├── ingesta/
│   ├── lectores.py    Excel, CSV y ZIP a nombres canónicos; elige la hoja o el archivo útil
│   └── corridas.py    La corrida: lee, juzga, decide y publica. La usan la API y el CLI
├── atraso.py          Tramos de atraso: la única fuente de sus fronteras
├── segmentacion.py    El resumen por segmento, agregado en la base
├── decision/
│   ├── reglas.py      decision/v1: el núcleo puro y determinista, sin base ni framework
│   └── ejecuciones.py Aplica el núcleo sobre PostgreSQL, en una transacción: todo o nada
├── territorial/
│   ├── reglas.py      territorial/v1: el dominio puro (carga, orden y motivos), sin base
│   └── ejecuciones.py Agrega en PostgreSQL, aplica el núcleo y persiste: todo o nada
├── db/                Modelo: la corrida, sus cuentas y rechazos, y las ejecuciones con lo que
│                      publican (decisiones por cuenta y resultados por municipio)
├── generador/         Cartera sintética, único origen de datos del proyecto
├── api/               FastAPI: corridas, cartera, decisiones y territorial; esquemas, errores y
│                      autenticación
└── cli.py             Comandos: generar y cargar
migraciones/           Versiones de Alembic
scripts/               Prueba de humo del flujo completo y control de archivos trackeados
docs/decisiones.md     Por qué está hecho así, y qué haría distinto
```

Todo lo que se escribe cuelga de una **Corrida**. Si alguien pregunta de dónde salió un
número, la respuesta es una fila de esa tabla: qué archivo llegó (con su firma SHA-256) y
qué cartera traía (con la firma de su contenido, la misma en cualquier formato), con qué
tolerancia y qué versión del contrato se juzgó, cuántos registros se leyeron, validaron y
rechazaron, y por qué.

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

## Limitaciones conocidas

- **La ingesta corre dentro del proceso de la API** (`BackgroundTasks`). Si la API se
  reinicia a media corrida, esa corrida queda `EN_PROCESO` para siempre. Después de 15
  minutos deja de bloquear que se reintente el mismo archivo, pero nadie la cierra. Un
  worker con cola y reintentos es trabajo de la orquestación durable, v0.5.0.
- **El POST de decisiones es síncrono.** `POST /corridas/{run_id}/decisiones` decide la
  corrida entera antes de responder, así que la conexión HTTP queda abierta hasta que termina.
  La conexión a la base con que encuentra la corrida sí se libera antes de decidir, pero con una
  cartera mucho más grande que las de prueba, el cliente o un proxy podrían cortar la espera.
- **Una ejecución de decisión no sobrevive a su proceso.** Si la API muere con una ejecución
  `EN_PROCESO`, la transacción revierte sus decisiones a medias, pero la ejecución queda
  `EN_PROCESO` y nadie la cierra: todavía no hay reconciliación durable. No bloquea otro
  intento sobre la misma corrida, porque solo bloquea una `EXITOSA`, pero el historial la sigue
  mostrando en proceso. Cerrarla, reintentarla y sacar la decisión de la petición HTTP es
  trabajo de la orquestación durable, v0.5.0.
- **El POST territorial también es síncrono.** `POST /decisiones/{decision_run_id}/territoriales`
  organiza todos los municipios antes de responder. Cuesta menos que decidir, porque la base
  agrega y al núcleo llega una fila por municipio, pero la conexión HTTP queda abierta hasta que
  termina.
- **Una ejecución territorial tampoco sobrevive a su proceso.** Si el proceso muere después de
  registrarla (T0), la transacción que publica revierte, pero la ejecución queda `EN_PROCESO` y
  nadie la cierra. No bloquea otro intento, porque el índice solo cuenta las `EXITOSA`. Su
  reconciliación es trabajo de la orquestación durable, v0.5.0.
- **`territorial/v1` solo consume `decision/v1`.** Las decisiones de otra versión dan
  `409 DECISION_NO_TERRITORIALIZABLE`; organizarlas pedirá otra versión de las reglas
  territoriales.
- **Sin ruteo.** `posicion_campo` dice qué municipio atender primero, no cómo recorrerlo: no hay
  coordenadas, distancias, secuencia de visitas ni gestores. Eso es v0.4.0, el Motor de Ruteo.
- **Una cartera se publica una vez por archivo, no por contenido.** La misma cartera en
  xlsx y en csv tiene dos firmas de archivo y se publica dos veces. Su firma de contenido,
  que es la misma, lo deja a la vista, pero todavía no lo impide.
- **El control de archivos revisa lo trackeado, no la historia.** Un archivo que se subió y
  después se borró sigue en la historia, y en un repositorio público ya salió. Un csv con
  otro nombre no se reconoce por sus bytes, y el control confía en el `.gitignore` del mismo
  commit: quitar una regla también la quita del control.
- **Límites de tamaño incompletos.** El tope de subida (`MC_TAMANO_MAXIMO_MB`) se revisa
  cuando el archivo ya llegó completo, y no hay tope a lo que un zip descomprime. El límite
  real le toca a un proxy delante de la API.
- **El contrato no valida contra el catálogo INEGI completo**: revisa la forma de las
  claves, no que existan. El Motor Territorial agrupa por esas mismas claves, así que un
  municipio que no existe se organizaría como cualquier otro. Y un saldo con más de dos
  decimales se redondea al guardarse en lugar de rechazarse.
- **Rendimiento medido solo hasta 10,000 filas**: menos de 3 s por corrida en una laptop,
  casi todo leyendo el Excel. A la escala de cientos de miles de cuentas no está medido. El
  Decision Engine y el Motor Territorial se prueban en el CI con las 9,800 cuentas de la prueba
  de humo, y tampoco están medidos más allá.
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
