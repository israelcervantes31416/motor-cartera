"""Lo que varias rutas necesitan: una sesion por peticion, una de solo lectura con una sola foto
de la base, y buscar una corrida."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from sqlmodel import Session, select

from motor_cartera.api.errores import ErrorDeApi
from motor_cartera.db.modelos import Corrida
from motor_cartera.db.sesion import sesion, sesion_de_lectura


def obtener_sesion() -> Iterator[Session]:
    with sesion() as s:
        yield s


Sesion = Annotated[Session, Depends(obtener_sesion)]


def obtener_sesion_de_lectura() -> Iterator[Session]:
    with sesion_de_lectura() as s:
        yield s


SesionDeLectura = Annotated[Session, Depends(obtener_sesion_de_lectura)]
"""Para las rutas que solo leen y componen varias consultas, como la Cuenta 360: todas ven la misma
foto de la base, asi que un corte que se publica mientras responden no aparece a medias."""


def buscar_corrida(s: Session, run_id: UUID) -> Corrida:
    """La corrida con ese run_id, o 404. Se busca por run_id: el id interno no sale de la base."""
    corrida = s.exec(select(Corrida).where(Corrida.run_id == run_id)).first()
    if corrida is None:
        raise ErrorDeApi(
            404,
            "CORRIDA_NO_ENCONTRADA",
            f"No existe una corrida con run_id {run_id}.",
            run_id=run_id,
        )
    return corrida
