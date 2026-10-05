"""Insertar muchas filas con COPY, dentro de la transaccion de una sesion.

Un INSERT por fila, aunque vaya en lotes, es lento para 500,000 cuentas. COPY las manda en un solo
flujo, y PostgreSQL convierte cada valor al tipo de su columna. Se usa la conexion de la sesion, asi
que lo copiado queda en su transaccion: se confirma o se revierte junto con todo lo demas, y una
corrida sigue publicando todo o nada.
"""

from __future__ import annotations

from collections.abc import Iterable

from psycopg.types.json import Jsonb
from sqlmodel import Session

__all__ = ["Jsonb", "copiar"]


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
