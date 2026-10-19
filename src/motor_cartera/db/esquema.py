"""La revision de Alembic que este codigo espera encontrar en la base.

La readiness de la API y del worker la compara con la de la base: con otra, el proceso esta vivo
pero no listo, y ningun balanceador le manda trafico. Una prueba verifica que es la cabeza de las
migraciones del repositorio, asi que una migracion nueva no se olvida de moverla.
"""

from __future__ import annotations

from sqlalchemy import Connection, text

CABEZA_ESPERADA = "0017"


def revision_actual(conexion: Connection) -> str | None:
    """La revision de la base, o None si no tiene la tabla de Alembic o esta vacia."""
    existe = conexion.execute(text("SELECT to_regclass('alembic_version') IS NOT NULL")).scalar()
    if not existe:
        return None
    return conexion.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).scalar()
