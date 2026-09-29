"""Un solo esquema de error para toda la API.

Toda respuesta 4xx o 5xx tiene la misma forma, venga de una regla del dominio, de la
validacion de FastAPI o de una ruta que no existe. El cliente decide por `codigo`, que es
estable, y nunca tiene que distinguir entre formatos de error.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)


class DetalleError(BaseModel):
    campo: str | None = Field(
        default=None, description="Donde esta el problema, p. ej. `query.por_pagina`."
    )
    problema: str


class ErrorRespuesta(BaseModel):
    """La forma de todos los errores de la API."""

    codigo: str = Field(
        description="Codigo estable y legible por maquina: es lo que un cliente debe comparar.",
        examples=["CORRIDA_NO_ENCONTRADA"],
    )
    mensaje: str = Field(description="Explicacion para una persona; su redaccion puede cambiar.")
    detalles: list[DetalleError] = Field(
        default_factory=list, description="Problemas puntuales, uno por campo, si los hay."
    )
    run_id: UUID | None = Field(
        default=None, description="La corrida con la que tiene que ver el error, si hay una."
    )


class ErrorDeApi(Exception):
    """Un error con su codigo HTTP y su codigo de dominio, decididos a proposito."""

    def __init__(
        self,
        estado: int,
        codigo: str,
        mensaje: str,
        *,
        run_id: UUID | None = None,
        encabezados: dict[str, str] | None = None,
    ) -> None:
        super().__init__(mensaje)
        self.estado = estado
        self.respuesta = ErrorRespuesta(codigo=codigo, mensaje=mensaje, run_id=run_id)
        self.encabezados = encabezados


def errores(*casos: tuple[int, str, str]) -> dict[int | str, dict[str, Any]]:
    """Documenta en OpenAPI los errores de una ruta: (estado HTTP, codigo, cuando ocurre).

    Cada ruta declara exactamente que puede responder, con un ejemplo por codigo, para que
    la documentacion se entienda sin leer el codigo.
    """
    respuestas: dict[int | str, dict[str, Any]] = {}
    for estado, codigo, cuando in casos:
        respuesta = respuestas.setdefault(
            estado,
            {"model": ErrorRespuesta, "description": "", "content": {"application/json": {}}},
        )
        # Una linea por codigo: es lo que el cliente compara, asi que es lo que se ve primero.
        respuesta["description"] += f"- `{codigo}`: {cuando}\n"
        ejemplos = respuesta["content"]["application/json"].setdefault("examples", {})
        ejemplos[codigo] = {"summary": codigo, "value": {"codigo": codigo, "mensaje": cuando}}
    return respuestas


# Pydantic explica en ingles; estas son las fallas de entrada que esta API puede producir.
_PROBLEMAS = {
    "missing": "Falta este campo.",
    "greater_than_equal": "Debe ser mayor o igual a {ge}.",
    "less_than_equal": "Debe ser menor o igual a {le}.",
    "int_parsing": "Debe ser un numero entero.",
    "uuid_parsing": "Debe ser un UUID.",
    "enum": "Debe ser uno de: {expected}.",
}


def registrar_manejadores(app: FastAPI) -> None:
    """Hace que todo error, incluidos los de FastAPI y Starlette, salga como ErrorRespuesta."""

    @app.exception_handler(ErrorDeApi)
    async def _de_dominio(_: Request, exc: ErrorDeApi) -> JSONResponse:
        return _responder(exc.estado, exc.respuesta, exc.encabezados)

    @app.exception_handler(RequestValidationError)
    async def _de_validacion(_: Request, exc: RequestValidationError) -> JSONResponse:
        detalles = [
            DetalleError(campo=".".join(str(p) for p in error["loc"]), problema=_problema(error))
            for error in exc.errors()
        ]
        respuesta = ErrorRespuesta(
            codigo="ENTRADA_INVALIDA",
            mensaje="La peticion no cumple el contrato de la API.",
            detalles=detalles,
        )
        return _responder(422, respuesta)

    @app.exception_handler(StarletteHTTPException)
    async def _de_http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # Los que genera el propio framework. Los del dominio son ErrorDeApi.
        ruta = request.url.path
        codigo, mensaje = {
            400: ("PETICION_ILEGIBLE", f"La peticion no se pudo interpretar: {exc.detail}"),
            404: ("RUTA_NO_ENCONTRADA", f"No existe la ruta {ruta}."),
            405: ("METODO_NO_PERMITIDO", f"{ruta} no admite {request.method}."),
        }.get(exc.status_code, (f"HTTP_{exc.status_code}", str(exc.detail)))
        respuesta = ErrorRespuesta(codigo=codigo, mensaje=mensaje)
        return _responder(exc.status_code, respuesta, exc.headers)

    @app.exception_handler(Exception)
    async def _inesperado(request: Request, _: Exception) -> JSONResponse:
        # El detalle va a la bitacora, no al cliente: un traceback le dice a un atacante
        # mas de lo que le sirve a un usuario.
        log.exception("error no controlado en %s %s", request.method, request.url.path)
        respuesta = ErrorRespuesta(
            codigo="ERROR_INTERNO",
            mensaje="Error interno. No es un problema de la peticion; quedo en la bitacora.",
        )
        return _responder(500, respuesta)


def _responder(
    estado: int, respuesta: ErrorRespuesta, encabezados: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=estado, content=respuesta.model_dump(mode="json"), headers=encabezados
    )


def _problema(error: dict[str, Any]) -> str:
    plantilla = _PROBLEMAS.get(error["type"])
    if plantilla is not None:
        contexto = {k: str(v).replace("' or '", "' o '") for k, v in error.get("ctx", {}).items()}
        try:
            return plantilla.format(**contexto)
        except (KeyError, IndexError):
            pass
    return str(error["msg"]).removeprefix("Value error, ")
