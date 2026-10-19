from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool, text
from sqlmodel import SQLModel

from motor_cartera.config import config as app_config
from motor_cartera.db import modelos  # noqa: F401  (registra las tablas)

cfg = context.config
# ConfigParser interpola `%`: una contrasena con `%` (codificada en la URL) se escapa.
cfg.set_main_option("sqlalchemy.url", app_config.database_url.replace("%", "%%"))

target_metadata = SQLModel.metadata

TABLAS_DE_POSTGIS = frozenset({"spatial_ref_sys"})
"""Lo que crea la extension PostGIS en el esquema del proyecto. Sin conexion (modo offline) no se
puede preguntar a la base que es de una extension: esto es lo que se sabe."""

CANDADO_DE_MIGRACIONES = 7_031_416_018
"""La llave del candado consultivo de PostgreSQL que serializa las migraciones: si dos procesos
migran a la vez (dos despliegues, dos replicas mal configuradas), el segundo espera al primero y,
cuando obtiene el candado, encuentra el esquema ya en la cabeza y no hace nada."""

DE_EXTENSIONES = text(
    "SELECT c.relname FROM pg_depend d JOIN pg_class c ON c.oid = d.objid "
    "WHERE d.classid = 'pg_class'::regclass AND d.refclassid = 'pg_extension'::regclass "
    "AND d.deptype = 'e'"
)
"""Las relaciones que pertenecen a una extension."""


def filtro(de_extensiones: frozenset[str]):
    """Que compara alembic: todo lo del proyecto, y nada de lo que es de una extension. PostGIS crea
    spatial_ref_sys en el esquema del proyecto, y la imagen oficial de PostGIS instala ademas
    postgis_topology y postgis_tiger_geocoder y pone sus esquemas en el search_path de la base, asi
    que sus tablas tambien se ven al reflejar. No son del proyecto: las administra su extension y
    ningun modelo las declara."""

    def incluir(objeto, nombre, tipo, reflejado, comparado_con) -> bool:
        if not reflejado or comparado_con is not None:
            return True
        tabla = nombre if tipo == "table" else getattr(getattr(objeto, "table", None), "name", None)
        return tabla not in de_extensiones

    return incluir


def run_migrations_offline() -> None:
    context.configure(
        url=app_config.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        include_object=filtro(TABLAS_DE_POSTGIS),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        cfg.get_section(cfg.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    # En una conexion aparte, para no abrir la transaccion de las migraciones antes de tiempo.
    with connectable.connect() as conexion:
        de_extensiones = TABLAS_DE_POSTGIS | frozenset(conexion.execute(DE_EXTENSIONES).scalars())
    # El candado vive en su propia conexion, a nivel de sesion: sobrevive a sus commits y se
    # suelta al final, o cuando la conexion se cierra si el proceso muere.
    with connectable.connect() as candado:
        candado.execute(text("SELECT pg_advisory_lock(:llave)"), {"llave": CANDADO_DE_MIGRACIONES})
        candado.commit()
        try:
            with connectable.connect() as connection:
                context.configure(
                    connection=connection,
                    target_metadata=target_metadata,
                    compare_type=True,
                    include_object=filtro(de_extensiones),
                )
                with context.begin_transaction():
                    context.run_migrations()
        finally:
            candado.execute(
                text("SELECT pg_advisory_unlock(:llave)"), {"llave": CANDADO_DE_MIGRACIONES}
            )
            candado.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
