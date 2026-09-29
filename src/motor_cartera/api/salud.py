"""GET /salud: el proceso vive y la base contesta."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from motor_cartera import __version__
from motor_cartera.db.sesion import crear_motor

router = APIRouter(tags=["salud"])


class Salud(BaseModel):
    estado: Literal["ok", "degradado"]
    base_de_datos: Literal["ok", "sin_conexion"]
    version: str


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
