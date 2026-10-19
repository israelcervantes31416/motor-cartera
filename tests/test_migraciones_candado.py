"""Dos procesos que migran a la vez sobre una base nueva: el candado consultivo de las migraciones
los serializa, el segundo espera al primero y encuentra el esquema ya en la cabeza. Sin el candado,
los dos crearian las mismas tablas y uno fallaria."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, make_url, text

from motor_cartera.db.esquema import CABEZA_ESPERADA

RAIZ = Path(__file__).resolve().parents[1]
URL_PRUEBAS = os.environ.get(
    "MC_DATABASE_URL_PRUEBAS", "postgresql+psycopg://motor:motor@localhost:5434/cartera_test"
)
BASE = "cartera_candado_test"


def _servidor():
    return create_engine(
        make_url(URL_PRUEBAS).set(database="postgres"), isolation_level="AUTOCOMMIT"
    )


def test_dos_migraciones_simultaneas_terminan_las_dos_con_el_esquema_en_la_cabeza():
    servidor = _servidor()
    try:
        with servidor.connect() as conexion:
            conexion.execute(text(f'DROP DATABASE IF EXISTS "{BASE}" WITH (FORCE)'))
            conexion.execute(text(f'CREATE DATABASE "{BASE}"'))
        url = make_url(URL_PRUEBAS).set(database=BASE).render_as_string(hide_password=False)
        entorno = {**os.environ, "MC_DATABASE_URL": url, "MC_APP_ENV": "test"}
        comando = [sys.executable, "-m", "alembic", "upgrade", "head"]
        procesos = [
            subprocess.Popen(
                comando, cwd=RAIZ, env=entorno, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
            )
            for _ in range(2)
        ]
        salidas = [p.communicate(timeout=600)[0].decode(errors="replace") for p in procesos]

        assert [p.returncode for p in procesos] == [0, 0], salidas
        motor = create_engine(url)
        try:
            with motor.connect() as conexion:
                revision = conexion.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar()
        finally:
            motor.dispose()
        assert revision == CABEZA_ESPERADA
    finally:
        with servidor.connect() as conexion:
            conexion.execute(text(f'DROP DATABASE IF EXISTS "{BASE}" WITH (FORCE)'))
        servidor.dispose()
