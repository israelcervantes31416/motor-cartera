"""La aplicacion FastAPI.

Se construye con `crear_app()` y se sirve con
`uvicorn --factory motor_cartera.api.app:crear_app`. Asi importar el modulo no exige
configuracion, y cada prueba construye su propia app con su propia clave.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI

from motor_cartera import __version__
from motor_cartera.api import cartera, corridas, decisiones, salud
from motor_cartera.api.errores import registrar_manejadores
from motor_cartera.api.seguridad import exigir_api_key
from motor_cartera.config import Config
from motor_cartera.config import config as config_del_entorno

DESCRIPCION = """
Ingesta, validacion, resumen y decision por cuenta de una cartera de credito al consumo.
**Todos los datos son sinteticos**: los produce el generador del proyecto.

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
    app.include_router(salud.router)
    return app
