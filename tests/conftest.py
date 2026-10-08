"""Fixtures compartidas.

Las pruebas que tocan la base usan una base propia, nunca la de desarrollo, y la vacian
antes de cada prueba. El almacen de artefactos de las pruebas es un directorio temporal, aparte
del de desarrollo, que se borra al terminar.
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
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

# Un solo almacen para toda la sesion, fijado antes de que se construya cualquier Config: varios
# modulos de prueba construyen la suya al importarse, y la API, el flujo y el worker tienen que ver
# el mismo almacen, como en produccion. Por contenido, un objeto de otra prueba no estorba.
ALMACEN_DE_PRUEBAS = Path(tempfile.mkdtemp(prefix="motor-cartera-fuentes-"))
os.environ["MC_SOURCE_STORE_ROOT"] = str(ALMACEN_DE_PRUEBAS)


@pytest.fixture(scope="session", autouse=True)
def _almacen_de_la_sesion() -> Iterator[None]:
    """Borra el almacen de las pruebas al terminar. Los objetos son de solo lectura."""
    yield

    def hacer_borrable(funcion, ruta, _error):
        os.chmod(ruta, stat.S_IWRITE)
        funcion(ruta)

    shutil.rmtree(ALMACEN_DE_PRUEBAS, onexc=hacer_borrable)


@pytest.fixture
def almacen():
    """El almacen de artefactos que ven la API, el flujo y el worker durante las pruebas."""
    from motor_cartera.fuentes.almacen import LocalContentAddressedStore

    return LocalContentAddressedStore(ALMACEN_DE_PRUEBAS)


@pytest.fixture
def objetos(almacen):
    """Los SHA-256 de los objetos que tiene el almacen al llamarla: para comparar antes y despues
    de algo que no debe guardar nada."""

    def objetos() -> set[str]:
        raiz = almacen.raiz / "sha256"
        if not raiz.exists():
            return set()
        return {ruta.name for ruta in raiz.rglob("*") if ruta.is_file()}

    return objetos


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
    """Base migrada y vacia: cada prueba empieza de cero. La ejecucion del motor de pagos no cuelga
    de ninguna de las otras, asi que el CASCADE no la alcanza: se nombra."""
    from motor_cartera.db.sesion import crear_motor

    with crear_motor().begin() as conexion:
        conexion.execute(
            text(
                "TRUNCATE corrida, cuenta, rechazo, artefacto_fuente, cuenta_canonica, "
                "ejecucion_motor_pagos RESTART IDENTITY CASCADE"
            )
        )
    yield


@pytest.fixture
def trabajar():
    """Procesa la cola como lo haria un worker, hasta que no quede ningun trabajo que tomar ya, y
    devuelve lo que proceso. Un trabajo que vuelve a la cola con espera ya no se toma aqui."""
    from motor_cartera.config import Config
    from motor_cartera.orquestacion.worker import identificador_worker, procesar_un_trabajo

    def trabajar(config: Config | None = None, *, limite: int = 50) -> list:
        config = config or Config()
        worker_id = identificador_worker()
        procesados = []
        while (procesado := procesar_un_trabajo(worker_id, config)) is not None:
            procesados.append(procesado)
            assert len(procesados) <= limite, "la cola no se vacia"
        return procesados

    return trabajar


CLAVE = "clave-de-prueba"


@pytest.fixture
def clave_api() -> str:
    return CLAVE


@pytest.fixture
def app():
    from motor_cartera.api import crear_app
    from motor_cartera.config import Config

    return crear_app(Config(api_key=CLAVE, tolerancia_rechazo=0.05, tamano_maximo_mb=1))


@pytest.fixture
def cliente(app):
    """Cliente con la API key puesta. Para probar sin ella, quitala en la peticion."""
    from fastapi.testclient import TestClient

    with TestClient(app, headers={"X-API-Key": CLAVE}) as cliente:
        yield cliente


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
