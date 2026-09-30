# motor-cartera

Motor de ingesta, validación y segmentación de cartera de crédito al consumo, construido
sobre **datos sintéticos**, con una API REST para operarlo.

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

Fase 1 terminada: una rebanada vertical que funciona de punta a punta.

- [x] Contrato de datos *fail-closed*, registro por registro, con el motivo de cada rechazo
- [x] Generador de cartera sintética en xlsx, csv y zip, con filas inválidas a propósito
- [x] Lectores de Excel, CSV y ZIP con detección flexible de columnas
- [x] Persistencia con trazabilidad por corrida, incluidas la versión del contrato con que se
  juzgó y la firma de su contenido; esquema versionado con Alembic
- [x] API REST: corridas, rechazos, resumen segmentado y salud, con OpenAPI
- [x] `docker compose up` levanta todo; CI con PostgreSQL y prueba del compose en limpio
- [ ] Motor de segmentación: hoy hay resumen por dimensiones, no reglas que asignen cuentas a canales
- [ ] Motor territorial: agrupamiento y ruteo
- [ ] Orquestación (fase 3) y despliegue en nube (fase 4)

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

Los cinco pasos, con la verificación de cada código HTTP, están en un solo script que corre
igual en tu máquina que en el CI. Necesita un archivo que no se haya subido antes, porque el
mismo archivo no se publica dos veces:

```bash
docker compose exec api motor-cartera generar --destino datos/humo.xlsx --semilla 7
python scripts/prueba_de_humo.py datos/humo.xlsx
```

Solo usa la biblioteca estándar de Python; también corre dentro del contenedor con
`docker compose exec api python scripts/prueba_de_humo.py datos/humo.xlsx`.

## La API

| Método y ruta | Qué hace | Respuestas |
|---|---|---|
| `POST /corridas` | Recibe el archivo, registra la corrida y la procesa en segundo plano | 201, 401, 409, 413, 415, 422 |
| `GET /corridas/{run_id}` | Estado: conteos, tiempos y resultado | 200, 401, 404, 422 |
| `GET /corridas/{run_id}/rechazos` | Registros rechazados con su fila y motivo, paginados | 200, 401, 404, 409, 422 |
| `GET /cartera/resumen` | Cuentas y saldo por segmento de la cartera vigente, paginado | 200, 401, 404, 409, 422 |
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

Por qué cada código es el que es (201 y no 202, 422 y no 400, cuándo 409, por qué un
archivo con registros inválidos no es un error HTTP), y el resto de las decisiones, están
en [docs/decisiones.md](docs/decisiones.md).

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

El CI tiene tres trabajos: la revisión de los archivos trackeados; lint, formato,
migraciones (suben, coinciden con los modelos y bajan) y pruebas contra una PostgreSQL de
servicio; y el `docker compose up` completo en un runner limpio, con la prueba de humo.

## Arquitectura

```
src/motor_cartera/
├── config.py          Configuración desde el entorno (prefijo MC_)
├── contratos/         Qué forma deben tener los datos; el juicio registro por registro
├── ingesta/
│   ├── lectores.py    Excel, CSV y ZIP a nombres canónicos; elige la hoja o el archivo útil
│   └── corridas.py    La corrida: lee, juzga, decide y publica. La usan la API y el CLI
├── segmentacion.py    Tramos de atraso y el resumen por segmento, agregado en la base
├── db/                Modelo persistente: Corrida, Cuenta y Rechazo
├── generador/         Cartera sintética, único origen de datos del proyecto
├── api/               FastAPI: rutas, esquemas, errores y autenticación
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

## Limitaciones conocidas

- **El procesamiento corre dentro del proceso de la API** (`BackgroundTasks`). Si la API se
  reinicia a media corrida, esa corrida queda `EN_PROCESO` para siempre. Después de 15
  minutos deja de bloquear que se reintente el mismo archivo, pero nadie la cierra. Un
  worker con cola y reintentos es trabajo de la orquestación, fase 3.
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
  claves, no que existan. Y un saldo con más de dos decimales se redondea al guardarse en
  lugar de rechazarse.
- **Rendimiento medido solo hasta 10,000 filas**: menos de 3 s por corrida en una laptop,
  casi todo leyendo el Excel. A la escala de cientos de miles de cuentas no está medido.
- **Una sola API key**, sin usuarios, permisos ni rotación.
- **El `docker compose up` se prueba en el CI**, en Linux. La máquina donde se construyó
  esta fase no tiene Docker, así que en Windows y macOS no está probado.

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
