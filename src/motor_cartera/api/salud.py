"""La salud de la API: si el proceso vive, y si esta listo para atender.

- `GET /health/live`: el proceso responde. No toca la base: si PostgreSQL cae, la API sigue viva,
  y un orquestador no la reinicia en bucle por algo que reiniciarla no arregla.
- `GET /health/ready`: la base contesta y su esquema es el que este codigo espera (la revision de
  Alembic). Si no, 503: viva, pero sin trafico hasta que vuelva.
- `GET /salud`: la de siempre (v0.1), que junta las dos cosas y dice la version.

Las tres son publicas: las consultan balanceadores y orquestadores sin credencial. No dicen nada que
sirva para atacar: ni hosts, ni versiones de las dependencias, ni el detalle de un error. El edge no
las expone a internet.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from motor_cartera import __version__
from motor_cartera.db.esquema import CABEZA_ESPERADA, revision_actual
from motor_cartera.db.sesion import crear_motor
from motor_cartera.observabilidad.metricas import SALUD_LISTA

router = APIRouter(tags=["salud"])


class Salud(BaseModel):
    estado: Literal["ok", "degradado"]
    base_de_datos: Literal["ok", "sin_conexion"]
    version: str


class Vivo(BaseModel):
    estado: Literal["vivo"]


class Comprobaciones(BaseModel):
    base_de_datos: Literal["ok", "sin_conexion"]
    esquema: Literal["ok", "incompatible", "desconocido"]


class Listo(BaseModel):
    estado: Literal["listo", "no_listo"]
    comprobaciones: Comprobaciones


@router.get(
    "/salud",
    summary="Comprueba que la API vive y que la base contesta",
    response_model=Salud,
    responses={503: {"model": Salud, "description": "La API vive, pero la base no contesta."}},
)
def salud(response: Response) -> Salud:
    """200 si todo esta bien; 503 si la base no contesta.

    No pide API key: la consultan balanceadores y orquestadores, y no expone datos.
    Responde lo mismo en 200 y en 503 porque es un reporte de estado, no un error: un
    orquestador lee el codigo y una persona lee el cuerpo.
    """
    try:
        with crear_motor().connect() as conexion:
            conexion.execute(text("SELECT 1"))
    except SQLAlchemyError:
        response.status_code = 503
        return Salud(estado="degradado", base_de_datos="sin_conexion", version=__version__)
    return Salud(estado="ok", base_de_datos="ok", version=__version__)


@router.get(
    "/health/live",
    summary="Liveness: el proceso responde (no depende de la base)",
    response_model=Vivo,
)
def vivo() -> Vivo:
    """Siempre 200 mientras el proceso pueda responder. No consulta la base ni ninguna otra
    dependencia."""
    return Vivo(estado="vivo")


@router.get(
    "/health/ready",
    summary="Readiness: la base contesta y su esquema es el esperado",
    response_model=Listo,
    responses={503: {"model": Listo, "description": "Viva, pero no lista para atender."}},
)
def listo(response: Response) -> Listo:
    """200 si la base contesta y su revision de Alembic es la que este codigo espera; 503 si no.
    Cada consulta tiene un tope de 2 s: la readiness no se queda colgada con la base."""
    try:
        with crear_motor().connect() as conexion, conexion.begin():
            conexion.execute(text("SET LOCAL statement_timeout = 2000"))
            revision = revision_actual(conexion)
    except SQLAlchemyError:
        response.status_code = 503
        SALUD_LISTA.set(0)
        return Listo(
            estado="no_listo",
            comprobaciones=Comprobaciones(base_de_datos="sin_conexion", esquema="desconocido"),
        )
    if revision != CABEZA_ESPERADA:
        response.status_code = 503
        SALUD_LISTA.set(0)
        return Listo(
            estado="no_listo",
            comprobaciones=Comprobaciones(base_de_datos="ok", esquema="incompatible"),
        )
    SALUD_LISTA.set(1)
    return Listo(estado="listo", comprobaciones=Comprobaciones(base_de_datos="ok", esquema="ok"))
