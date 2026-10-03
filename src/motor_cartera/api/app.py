"""La aplicacion FastAPI.

Se construye con `crear_app()` y se sirve con
`uvicorn --factory motor_cartera.api.app:crear_app`. Asi importar el modulo no exige
configuracion, y cada prueba construye su propia app con su propia clave.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI

from motor_cartera import __version__
from motor_cartera.api import cartera, corridas, decisiones, ruteo, salud, territorial
from motor_cartera.api.errores import registrar_manejadores
from motor_cartera.api.seguridad import exigir_api_key
from motor_cartera.config import Config
from motor_cartera.config import config as config_del_entorno

DESCRIPCION = """
Ingesta, validacion, resumen, decision por cuenta, organizacion territorial y ruteo sintetico
de una cartera de credito al consumo. **Todos los datos son sinteticos**: los produce el generador
del proyecto.

**Flujo.**

1. `POST /corridas` con el archivo (xlsx, csv o zip). Responde `201` con el `run_id` y la
   corrida `EN_PROCESO`; se procesa en segundo plano.
2. `GET /corridas/{run_id}` hasta que el estado sea `EXITOSA`, `RECHAZADA` o `FALLIDA`.
3. `GET /corridas/{run_id}/rechazos`: cada registro que no cumplio el contrato, con su
   fila y el motivo.
4. `GET /cartera/resumen`: cuentas y saldo por segmento de la cartera vigente.

Con la cartera publicada, el Decision Engine decide cada cuenta con reglas versionadas:

5. `POST /corridas/{run_id}/decisiones` decide la corrida `EXITOSA` en la misma peticion.
   Responde `201` con la ejecucion terminada: `EXITOSA`, con una decision por cuenta, o
   `FALLIDA`, sin ninguna.
6. `GET /corridas/{run_id}/decisiones`: todas las ejecuciones de esa corrida, la mas
   reciente primero.
7. `GET /decisiones/{decision_run_id}`: una ejecucion, con su version de las reglas y sus
   conteos.
8. `GET /decisiones/{decision_run_id}/cuentas`: lo que decidio de cada cuenta (segmento,
   prioridad y canal recomendado) y por que.

Con las decisiones publicadas, el Motor Territorial las organiza por municipio con reglas
versionadas. Prioriza municipios; no traza rutas:

9. `POST /decisiones/{decision_run_id}/territoriales` organiza las decisiones de una ejecucion
   `EXITOSA` en la misma peticion. Responde `201` con la ejecucion terminada: `EXITOSA`, con un
   resultado por municipio, o `FALLIDA`, sin ninguno.
10. `GET /decisiones/{decision_run_id}/territoriales`: todas las ejecuciones territoriales de esas
    decisiones, la mas reciente primero.
11. `GET /territoriales/{territorial_run_id}`: una ejecucion, con su version de las reglas y sus
    conteos.
12. `GET /territoriales/{territorial_run_id}/municipios`: cada municipio con su carga de campo, su
    lugar y por que, en orden de prioridad.

Con los municipios organizados, el Motor de Ruteo traza, dentro de cada municipio con trabajo de
campo, en que secuencia visitar sus cuentas `CAMPO`, con reglas versionadas. **Las rutas son
sinteticas**: cada cuenta recibe un punto determinista en un plano local de 10 km por lado, propio
de su municipio, y las distancias son Manhattan, en metros sinteticos. No son latitud ni longitud,
domicilios, calles, trafico ni tiempos:

13. `POST /territoriales/{territorial_run_id}/ruteos` rutea los municipios de una ejecucion
    territorial `EXITOSA` en la misma peticion. Responde `201` con la ejecucion terminada:
    `EXITOSA`, con una ruta por municipio con cuentas de campo, o `FALLIDA`, sin ninguna.
14. `GET /territoriales/{territorial_run_id}/ruteos`: todas las ejecuciones de ruteo de esa
    ejecucion territorial, la mas reciente primero.
15. `GET /ruteos/{ruteo_run_id}`: una ejecucion, con su version de las reglas y sus conteos.
16. `GET /ruteos/{ruteo_run_id}/rutas`: la ruta de cada municipio, en el orden de prioridad
    territorial, con sus distancias.
17. `GET /ruteos/{ruteo_run_id}/rutas/{clave_territorio}/paradas`: las paradas de un municipio, en
    el orden de visita.

**Autenticacion.** Toda ruta, salvo `/salud` y esta documentacion, exige la cabecera
`X-API-Key` con la clave de `MC_API_KEY`. Usa el boton *Authorize*.

**Errores.** Todas las respuestas 4xx y 5xx tienen la misma forma (`ErrorRespuesta`).
Compara `codigo`, que es estable; `mensaje` es para personas y puede cambiar.
"""

ETIQUETAS = [
    {
        "name": "corridas",
        "description": "Una corrida es una ingesta: un archivo leido, juzgado registro por "
        "registro y, si pasa, publicado. Todo lo que se escribe cuelga de una.",
    },
    {
        "name": "cartera",
        "description": "La cartera publicada: la que resulta de la ultima corrida EXITOSA "
        "con el corte mas reciente.",
    },
    {
        "name": "decisiones",
        "description": "Ejecuciones versionadas del Decision Engine y sus decisiones por cuenta.",
    },
    {
        "name": "territorial",
        "description": "Ejecuciones versionadas del Motor Territorial y sus resultados por "
        "municipio: carga de campo y prioridad, no rutas.",
    },
    {
        "name": "ruteo",
        "description": "Ejecuciones versionadas del Motor de Ruteo, su ruta por municipio y sus "
        "paradas en orden de visita. Coordenadas y distancias sinteticas: metros de un plano "
        "local por municipio, no geografia real.",
    },
    {"name": "salud", "description": "Si la API vive y la base contesta."},
]


def crear_app(config: Config | None = None) -> FastAPI:
    """Construye la API. Sin MC_API_KEY no arranca: una API que arranca sin clave, arranca
    abierta, y eso es fail-open."""
    config = config or config_del_entorno
    clave = config.api_key.get_secret_value() if config.api_key else ""
    if not clave:
        raise RuntimeError("MC_API_KEY no esta definida: la API no arranca sin clave.")

    app = FastAPI(
        title="motor-cartera",
        version=__version__,
        description=DESCRIPCION,
        openapi_tags=ETIQUETAS,
    )
    app.state.api_key = clave
    app.state.config = config
    registrar_manejadores(app)
    protegidas = [Depends(exigir_api_key)]
    app.include_router(corridas.router, dependencies=protegidas)
    app.include_router(cartera.router, dependencies=protegidas)
    app.include_router(decisiones.router, dependencies=protegidas)
    app.include_router(territorial.router, dependencies=protegidas)
    app.include_router(ruteo.router, dependencies=protegidas)
    app.include_router(salud.router)
    return app
