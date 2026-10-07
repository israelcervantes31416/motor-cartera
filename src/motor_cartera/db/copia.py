"""Insertar muchas filas con COPY, dentro de la transaccion de una sesion.

Un INSERT por fila, aunque vaya en lotes, es lento para 500,000 cuentas. COPY las manda en un solo
flujo, y PostgreSQL convierte cada valor al tipo de su columna. Se usa la conexion de la sesion, asi
que lo copiado queda en su transaccion: se confirma o se revierte junto con todo lo demas, y una
corrida sigue publicando todo o nada.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from psycopg.types.json import Jsonb
from sqlmodel import Session

__all__ = ["Jsonb", "copiar", "copiar_csv"]


def copiar(s: Session, tabla: str, columnas: tuple[str, ...], filas: Iterable[tuple]) -> int:
    """Copia `filas` a `tabla`, en la transaccion de `s`, y dice cuantas copio. Los textos se
    mandan como texto y PostgreSQL los convierte: un importe "1500.50" llega como NUMERIC, una
    fecha "2026-09-30" como DATE. Un valor JSONB va envuelto en Jsonb."""
    conexion = s.connection().connection.driver_connection
    copiadas = 0
    with conexion.cursor() as cursor:
        instruccion = f"COPY {tabla} ({', '.join(columnas)}) FROM STDIN"
        with cursor.copy(instruccion) as copia:
            for fila in filas:
                copia.write_row(fila)
                copiadas += 1
    return copiadas


def copiar_csv(s: Session, tabla: str, columnas: Sequence[str], bloques: Iterable[bytes]) -> None:
    """Copia a `tabla`, en la transaccion de `s`, bloques de CSV ya escritos: cada fila con las
    `columnas` en su orden, un texto entre comillas y un vacio sin comillas como NULL. Es el COPY de
    la historia, cuyo CSV escribe Arrow por lotes, sin pasar fila por fila por Python."""
    conexion = s.connection().connection.driver_connection
    with conexion.cursor() as cursor:
        instruccion = f"COPY {tabla} ({', '.join(columnas)}) FROM STDIN (FORMAT csv)"
        with cursor.copy(instruccion) as copia:
            for bloque in bloques:
                copia.write(bloque)
