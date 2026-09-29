"""La aplicacion FastAPI.

Se construye con `crear_app()` y se sirve con
`uvicorn --factory motor_cartera.api.app:crear_app`. Asi importar el modulo no exige
configuracion, y cada prueba construye su propia app con su propia clave.
"""

from __future__ import annotations

from fastapi import FastAPI

from motor_cartera import __version__
from motor_cartera.api import salud
from motor_cartera.api.errores import registrar_manejadores
from motor_cartera.config import Config
from motor_cartera.config import config as config_del_entorno

DESCRIPCION = """
Ingesta, validacion y resumen de una cartera de credito al consumo. **Todos los datos son
sinteticos**: los produce el generador del proyecto.

**Autenticacion.** Toda ruta, salvo `/salud` y esta documentacion, exige la cabecera
`X-API-Key` con la clave de `MC_API_KEY`. Usa el boton *Authorize*.

**Errores.** Todas las respuestas 4xx y 5xx tienen la misma forma (`ErrorRespuesta`).
Compara `codigo`, que es estable; `mensaje` es para personas y puede cambiar.
"""

ETIQUETAS = [
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
    app.include_router(salud.router)
    return app
