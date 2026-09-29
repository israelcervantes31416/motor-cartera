"""Fixtures compartidas.

Las pruebas que tocan la base usan una base propia, nunca la de desarrollo, y la vacian
antes de cada prueba.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine, make_url, text

RAIZ = Path(__file__).resolve().parents[1]
URL_PRUEBAS = os.environ.get(
    "MC_DATABASE_URL_PRUEBAS", "postgresql+psycopg://motor:motor@localhost:5434/cartera_test"
)

# Fail-closed tambien aqui: las pruebas vacian la base, asi que se niegan a correr sobre
# una que no se llame *_test. Se fija antes de que cualquier modulo lea la configuracion.
if not (make_url(URL_PRUEBAS).database or "").endswith("_test"):
    oculta = make_url(URL_PRUEBAS).render_as_string(hide_password=True)
    raise pytest.UsageError(f"MC_DATABASE_URL_PRUEBAS debe apuntar a una base *_test: {oculta}")
os.environ["MC_DATABASE_URL"] = URL_PRUEBAS


@pytest.fixture
def cartera_valida() -> pd.DataFrame:
    """Tres filas que cumplen el contrato. Punto de partida de las pruebas."""
    return pd.DataFrame(
        {
            "cliente_unico": ["CU00000001", "CU00000002", "CU00000003"],
            "saldo_total": [1500.50, 23000.00, 780.25],
            "dias_atraso": [0, 45, 190],
            "producto": ["CONSUMO", "TARJETA", "NOMINA"],
            "canal": ["TELEFONICA", "CAMPO", "DIGITAL"],
            "cve_entidad": ["21", "21", "09"],
            "cve_municipio": ["114", "156", "005"],
            "fecha_corte": pd.to_datetime(["2026-01-31"] * 3),
        }
    )


@pytest.fixture(scope="session")
def _esquema() -> None:
    """Crea la base de pruebas si hace falta y le aplica las migraciones, una vez.

    Con Alembic y no con create_all: asi cada corrida de pruebas tambien prueba que la
    migracion produce el esquema que el codigo espera.
    """
    from alembic import command
    from alembic.config import Config as ConfigAlembic

    _crear_base_si_falta(URL_PRUEBAS)
    cfg = ConfigAlembic(str(RAIZ / "alembic.ini"))
    cfg.set_main_option("script_location", str(RAIZ / "migraciones"))
    command.upgrade(cfg, "head")


@pytest.fixture
def bd(_esquema) -> Iterator[None]:
    """Base migrada y vacia: cada prueba empieza de cero."""
    from motor_cartera.db.sesion import crear_motor

    with crear_motor().begin() as conexion:
        conexion.execute(text("TRUNCATE corrida, cuenta, rechazo RESTART IDENTITY CASCADE"))
    yield


def _crear_base_si_falta(url: str) -> None:
    destino = make_url(url)
    servidor = create_engine(destino.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with servidor.connect() as conexion:
            existe = conexion.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :nombre"),
                {"nombre": destino.database},
            )
            if not existe:
                conexion.execute(text(f'CREATE DATABASE "{destino.database}"'))
    finally:
        servidor.dispose()
