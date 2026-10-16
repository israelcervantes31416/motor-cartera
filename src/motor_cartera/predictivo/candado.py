"""El candado de una ejecucion predictiva: un advisory lock de sesion de PostgreSQL, en una conexion
propia, sin una transaccion abierta mientras se tiene.

Un entrenamiento o un scoring no bloquea su fila durante minutos (una transaccion larga detendria la
frontera de conocimiento de los demas motores, que espera a que no quede ninguna abierta desde antes
de su known_at). En su lugar, quien ejecuta toma este candado: un segundo worker con la misma
ejecucion espera, consultando en sentencias cortas, a que el primero termine, y despues la encuentra
terminada. Si el proceso muere, su conexion se cierra y PostgreSQL suelta el candado.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from motor_cartera.db.sesion import crear_motor

log = logging.getLogger(__name__)

ESPERA = 0.2
"""Cada cuantos segundos vuelve a intentar quien espera el candado."""


@contextmanager
def candado(llave: str) -> Iterator[None]:
    """Tiene el candado de `llave` mientras dura; espera a que se libere si otro lo tiene."""
    with crear_motor().connect() as conexion:
        while True:
            obtenido = conexion.execute(
                text("SELECT pg_try_advisory_lock(hashtextextended(:llave, 0))"),
                {"llave": llave},
            ).scalar_one()
            conexion.commit()
            if obtenido:
                break
            time.sleep(ESPERA)
        try:
            yield
        finally:
            try:
                conexion.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:llave, 0))"),
                    {"llave": llave},
                )
                conexion.commit()
            except DBAPIError:  # pragma: no cover - la conexion ya no existe: el candado tampoco
                log.warning(
                    "no se pudo soltar el candado %s; se suelta al cerrar la conexion", llave
                )
                conexion.invalidate()
