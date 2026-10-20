"""Motor y sesiones de base de datos."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any, cast

from sqlalchemy import func
from sqlalchemy.engine import CursorResult, Result
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.sql.expression import Select

from motor_cartera.config import config

_motor = None


def crear_motor(url: str | None = None):
    global _motor
    if _motor is None or url is not None:
        # connect_timeout: si la base no contesta, /salud debe responder 503 en segundos,
        # no quedarse colgada hasta que el sistema operativo se rinda.
        # timezone=UTC: los instantes salen igual sin importar como este configurado el
        # servidor de PostgreSQL.
        # hide_parameters: un error de la base no lleva los valores de la sentencia a su mensaje,
        # ni por lo tanto a la bitacora: un parametro puede ser un CLIENTE_UNICO.
        _motor = create_engine(
            url or config.database_url,
            echo=False,
            pool_pre_ping=True,
            hide_parameters=True,
            connect_args={"connect_timeout": 5, "options": "-c timezone=UTC"},
        )
        from motor_cartera.observabilidad.trazas import instrumentar_motor

        instrumentar_motor(_motor)
    return _motor


def motor_actual():
    """El motor de este proceso si ya se creo, o None: las metricas del pool no lo crean."""
    return _motor


@contextmanager
def sesion() -> Iterator[Session]:
    with Session(crear_motor()) as s:
        yield s


@contextmanager
def sesion_de_lectura() -> Iterator[Session]:
    """Una sesion de solo lectura en la que todas las consultas ven la misma foto de la base
    (REPEATABLE READ): una respuesta que compone varias consultas no mezcla dos estados, aunque otra
    transaccion publique un corte entre una consulta y la siguiente. En PostgreSQL, una transaccion
    que solo lee nunca falla por serializacion en REPEATABLE READ. La conexion vuelve al pool con su
    nivel de aislamiento de siempre."""
    with Session(crear_motor()) as s:
        s.connection(
            execution_options={"isolation_level": "REPEATABLE READ", "postgresql_readonly": True}
        )
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


def reloj_de_la_base() -> datetime:
    """`now()` de PostgreSQL (el instante de la transaccion) como valor de una columna: lo
    resuelve el servidor al escribir la fila. Se tipa como lo que la columna guarda."""
    return cast(datetime, func.now())


def seleccionar(*columnas: Any) -> Select[Any]:
    """El `select()` de SQLModel con cinco columnas o mas. Sus sobrecargas tipadas llegan a cuatro;
    con mas de una columna devuelve siempre su `Select`, que es lo que este declara."""
    return cast(Select[Any], select(*columnas))


def con_columnas(consulta: Select[Any], *columnas: Any) -> Select[Any]:
    """`add_columns` sobre un select de SQLModel. Es generativo: devuelve una copia de la misma
    clase, que SQLAlchemy tipa como su propio Select."""
    return cast(Select[Any], consulta.add_columns(*columnas))


def filas_afectadas(resultado: Result[Any]) -> int:
    """Cuantas filas escribio un INSERT, un UPDATE o un DELETE. Para esas sentencias
    `Session.execute` devuelve un `CursorResult`, que trae `rowcount`; SQLAlchemy lo tipa como el
    `Result` general, que no lo declara."""
    return cast(CursorResult[Any], resultado).rowcount


def restriccion(exc: IntegrityError) -> str | None:
    """El nombre de la restriccion que rechazo la escritura, como lo reporta PostgreSQL."""
    diagnostico = getattr(exc.orig, "diag", None)
    return getattr(diagnostico, "constraint_name", None)
