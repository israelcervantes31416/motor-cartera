from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

from motor_cartera.config import config as app_config
from motor_cartera.db import modelos  # noqa: F401  (registra las tablas)

cfg = context.config
cfg.set_main_option("sqlalchemy.url", app_config.database_url)

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=app_config.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        cfg.get_section(cfg.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
