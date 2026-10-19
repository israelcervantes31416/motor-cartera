"""La salud de la API: liveness sin la base, readiness con la base y su esquema, y la revision de
Alembic que el codigo espera, que es la cabeza de las migraciones."""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest
from alembic.config import Config as ConfigAlembic
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from motor_cartera.api import salud
from motor_cartera.db import esquema

RAIZ = Path(__file__).resolve().parents[1]


def _sin_base():
    def sin_servidor():
        raise psycopg.OperationalError("connection refused")

    return create_engine("postgresql+psycopg://", creator=sin_servidor)


def test_la_cabeza_esperada_es_la_de_las_migraciones():
    configuracion = ConfigAlembic(str(RAIZ / "alembic.ini"))
    configuracion.set_main_option("script_location", str(RAIZ / "migraciones"))

    assert ScriptDirectory.from_config(configuracion).get_current_head() == esquema.CABEZA_ESPERADA


def test_vivo_no_pide_credencial_ni_toca_la_base(app, monkeypatch):
    monkeypatch.setattr(salud, "crear_motor", _sin_base)
    with TestClient(app) as cliente:
        respuesta = cliente.get("/health/live")

    assert respuesta.status_code == 200
    assert respuesta.json() == {"estado": "vivo"}


@pytest.mark.usefixtures("_esquema")
def test_listo_con_la_base_y_el_esquema_esperado(app):
    with TestClient(app) as cliente:
        respuesta = cliente.get("/health/ready")

    assert respuesta.status_code == 200
    assert respuesta.json() == {
        "estado": "listo",
        "comprobaciones": {"base_de_datos": "ok", "esquema": "ok"},
    }


def test_sin_base_no_esta_lista_pero_sigue_viva(app, monkeypatch):
    monkeypatch.setattr(salud, "crear_motor", _sin_base)
    with TestClient(app) as cliente:
        listo = cliente.get("/health/ready")
        vivo = cliente.get("/health/live")

    assert listo.status_code == 503
    assert listo.json()["comprobaciones"] == {
        "base_de_datos": "sin_conexion",
        "esquema": "desconocido",
    }
    assert vivo.status_code == 200


@pytest.mark.usefixtures("_esquema")
def test_con_otra_revision_del_esquema_no_esta_lista(app, monkeypatch):
    monkeypatch.setattr(salud, "CABEZA_ESPERADA", "9999")
    with TestClient(app) as cliente:
        respuesta = cliente.get("/health/ready")

    assert respuesta.status_code == 503
    assert respuesta.json()["comprobaciones"]["esquema"] == "incompatible"


def test_la_salud_no_dice_hosts_ni_versiones_de_dependencias(app, monkeypatch):
    monkeypatch.setattr(salud, "crear_motor", _sin_base)
    with TestClient(app) as cliente:
        texto = cliente.get("/health/ready").text + cliente.get("/health/live").text

    for prohibido in ("postgres", "psycopg", "5432", "localhost", "connection refused"):
        assert prohibido not in texto.lower(), prohibido
