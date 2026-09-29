"""Autenticacion por API key en una cabecera.

Sencilla a proposito en esta fase: una sola clave, leida del entorno (MC_API_KEY), sin
usuarios ni permisos. Quien tiene la clave puede todo; quien no, no puede nada.
"""

from __future__ import annotations

import secrets

from fastapi import Request, Security
from fastapi.security import APIKeyHeader

from motor_cartera.api.errores import ErrorDeApi

CABECERA = "X-API-Key"

esquema = APIKeyHeader(
    name=CABECERA,
    auto_error=False,  # el 401 lo arma esta API, con su propio esquema de error
    description="La clave definida en MC_API_KEY.",
)

# 401 exige decir como autenticarse (RFC 9110, 11.6.1). No hay un esquema registrado para
# API keys; este es el uso comun.
DESAFIO = {"WWW-Authenticate": f'ApiKey header="{CABECERA}"'}


def exigir_api_key(request: Request, clave: str | None = Security(esquema)) -> None:
    """Deja pasar solo peticiones con la clave correcta.

    401 y no 403 en ambos casos: 403 es "se quien eres y no te dejo"; aqui el problema es
    que no hay identidad valida. Con una sola clave no existe el caso de permisos.
    """
    if not clave:
        raise ErrorDeApi(
            401, "API_KEY_AUSENTE", f"Falta la cabecera {CABECERA}.", encabezados=DESAFIO
        )
    # compare_digest tarda lo mismo acierte o no: la duracion no revela cuantos
    # caracteres coinciden.
    if not secrets.compare_digest(clave.encode(), request.app.state.api_key.encode()):
        raise ErrorDeApi(401, "API_KEY_INVALIDA", "La API key no es valida.", encabezados=DESAFIO)
