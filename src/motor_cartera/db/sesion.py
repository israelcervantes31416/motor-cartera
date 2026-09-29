"""Motor y sesiones de base de datos."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlmodel import Session, create_engine

from motor_cartera.config import config

_motor = None


def crear_motor(url: str | None = None):
    global _motor
    if _motor is None or url is not None:
        # connect_timeout: si la base no contesta, /salud debe responder 503 en segundos,
        # no quedarse colgada hasta que el sistema operativo se rinda.
        _motor = create_engine(
            url or config.database_url,
            echo=False,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 5},
        )
    return _motor


@contextmanager
def sesion() -> Iterator[Session]:
    with Session(crear_motor()) as s:
        yield s
