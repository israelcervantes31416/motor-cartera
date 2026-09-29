"""Lo que toda ruta comparte: esquema de error, API key y /salud."""

from __future__ import annotations

from typing import Annotated

import psycopg
import pytest
from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from motor_cartera.api import crear_app, salud
from motor_cartera.api.seguridad import exigir_api_key
from motor_cartera.config import Config

CAMPOS_DE_ERROR = {"codigo", "mensaje", "detalles", "run_id"}


@pytest.fixture
def app(app):
    """La app real mas rutas de prueba, para ejercitar la infraestructura por si sola."""
    prueba = APIRouter()

    @prueba.get("/protegida")
    def protegida() -> dict[str, bool]:
        return {"ok": True}

    @prueba.get("/con-parametro")
    def con_parametro(n: Annotated[int, Query(ge=1)]) -> dict[str, int]:
        return {"n": n}

    @prueba.get("/revienta")
    def revienta() -> None:
        raise RuntimeError("detalle interno que el cliente no debe ver")

    app.include_router(prueba, dependencies=[Depends(exigir_api_key)])
    return app


# --- la API no arranca abierta -------------------------------------------------------------


@pytest.mark.parametrize("clave", [None, ""])
def test_la_api_no_arranca_sin_clave(clave):
    with pytest.raises(RuntimeError, match="MC_API_KEY"):
        crear_app(Config(api_key=clave))


# --- API key -------------------------------------------------------------------------------


def test_sin_api_key_401_con_desafio(cliente):
    respuesta = cliente.get("/protegida", headers={"X-API-Key": ""})

    assert respuesta.status_code == 401
    assert respuesta.json()["codigo"] == "API_KEY_AUSENTE"
    assert respuesta.headers["WWW-Authenticate"] == 'ApiKey header="X-API-Key"'


def test_con_api_key_incorrecta_401(cliente):
    respuesta = cliente.get("/protegida", headers={"X-API-Key": "otra"})

    assert respuesta.status_code == 401
    assert respuesta.json()["codigo"] == "API_KEY_INVALIDA"


def test_con_api_key_correcta_pasa(cliente):
    assert cliente.get("/protegida").json() == {"ok": True}


# --- un solo esquema de error ----------------------------------------------------------------


def test_entrada_invalida_422_con_detalle_por_campo(cliente):
    respuesta = cliente.get("/con-parametro", params={"n": 0})

    assert respuesta.status_code == 422
    cuerpo = respuesta.json()
    assert set(cuerpo) == CAMPOS_DE_ERROR
    assert cuerpo["codigo"] == "ENTRADA_INVALIDA"
    assert cuerpo["detalles"] == [{"campo": "query.n", "problema": "Debe ser mayor o igual a 1."}]


def test_ruta_inexistente_404_con_el_mismo_esquema(cliente):
    respuesta = cliente.get("/no-existe")

    assert respuesta.status_code == 404
    assert set(respuesta.json()) == CAMPOS_DE_ERROR
    assert respuesta.json()["codigo"] == "RUTA_NO_ENCONTRADA"


def test_metodo_no_permitido_405_y_dice_cuales_si(cliente):
    respuesta = cliente.delete("/salud")

    assert respuesta.status_code == 405
    assert respuesta.json()["codigo"] == "METODO_NO_PERMITIDO"
    assert respuesta.headers["Allow"] == "GET"


def test_error_inesperado_500_sin_filtrar_el_detalle(app, clave_api):
    encabezados = {"X-API-Key": clave_api}
    with TestClient(app, headers=encabezados, raise_server_exceptions=False) as cliente:
        respuesta = cliente.get("/revienta")

    assert respuesta.status_code == 500
    assert respuesta.json()["codigo"] == "ERROR_INTERNO"
    assert "detalle interno" not in respuesta.text


# --- /salud --------------------------------------------------------------------------------


@pytest.mark.usefixtures("_esquema")
def test_salud_con_la_base_arriba_y_sin_pedir_clave(app):
    with TestClient(app) as sin_clave:
        respuesta = sin_clave.get("/salud")

    assert respuesta.status_code == 200
    assert respuesta.json()["estado"] == "ok"
    assert respuesta.json()["base_de_datos"] == "ok"


def test_salud_sin_base_503(cliente, monkeypatch):
    # La caida se simula sin red: una conexion real a un puerto cerrado puede tardar
    # minutos en fallar, segun el sistema operativo.
    def sin_servidor():
        raise psycopg.OperationalError("connection refused")

    caida = create_engine("postgresql+psycopg://", creator=sin_servidor)
    monkeypatch.setattr(salud, "crear_motor", lambda: caida)

    respuesta = cliente.get("/salud")

    assert respuesta.status_code == 503
    assert respuesta.json() == {
        "estado": "degradado",
        "base_de_datos": "sin_conexion",
        "version": respuesta.json()["version"],
    }
