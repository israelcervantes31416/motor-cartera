"""Motor y sesiones de base de datos."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine

from motor_cartera.config import config

_motor = None


def crear_motor(url: str | None = None):
    global _motor
    if _motor is None or url is not None:
        # connect_timeout: si la base no contesta, /salud debe responder 503 en segundos,
        # no quedarse colgada hasta que el sistema operativo se rinda.
        # timezone=UTC: los instantes salen igual sin importar como este configurado el
        # servidor de PostgreSQL.
        _motor = create_engine(
            url or config.database_url,
            echo=False,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 5, "options": "-c timezone=UTC"},
        )
    return _motor


@contextmanager
def sesion() -> Iterator[Session]:
    with Session(crear_motor()) as s:
        yield s


def insertar_en_savepoint(s: Session, objeto: SQLModel) -> IntegrityError | None:
    """Inserta `objeto` dentro de un SAVEPOINT de la transaccion de `s`, sin confirmarla.

    Si la base lo rechaza por una restriccion, revierte solo el SAVEPOINT y devuelve el error: la
    transaccion de quien llama sigue usable, con todo lo que ya llevaba. Asi una apertura que pierde
    una carrera contra un indice unico se puede componer con otras escrituras en una sola
    transaccion. Si entra, devuelve None.
    """
    try:
        with s.begin_nested():
            s.add(objeto)
    except IntegrityError as exc:
        return exc
    return None


def restriccion(exc: IntegrityError) -> str | None:
    """El nombre de la restriccion que rechazo la escritura, como lo reporta PostgreSQL."""
    diagnostico = getattr(exc.orig, "diag", None)
    return getattr(diagnostico, "constraint_name", None)
