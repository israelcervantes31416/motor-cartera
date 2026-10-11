"""La geografia por la API, contra PostgreSQL: las versiones de la referencia, pedir la geografia de
un corte, leer su ejecucion y sus resultados por pagina y con filtros, la geografia de una cuenta a
traves de los cortes y la Cuenta 360 con su geografia vigente. Ninguna respuesta lleva una
coordenada de una cuenta."""

from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

import pytest
from geografia_escenarios import (
    AMOZOC,
    PUEBLA,
    CuentaGeo,
    corte_de,
    importar_referencia,
    ingerir_corte_geo,
)
from geografia_escenarios import cliente as cu
from sqlalchemy import func
from sqlmodel import select
from territorial_v2_escenarios import base

from motor_cartera.db.modelos import CuentaCanonica, EjecucionGeografia, TrabajoOrquestacion
from motor_cartera.db.sesion import sesion
from motor_cartera.geografia.referencia import ImportacionFallida
from motor_cartera.geografia.sintetica import rectangulo

pytestmark = pytest.mark.usefixtures("bd")

CORTE, OTRO_CORTE = date(2026, 9, 30), date(2026, 10, 7)
CUENTAS = [
    CuentaGeo(1, PUEBLA, "19.05", "-98.24"),
    CuentaGeo(2, AMOZOC, "19.1", "-97.96"),
    CuentaGeo(3, PUEBLA),
    CuentaGeo(4, AMOZOC, "19.05", "-98.24"),
]
COORDENADAS = {"19.05", "-98.24", "19.1", "-97.96"}
"""Las coordenadas observadas de las cuentas: ninguna sale por la API."""
NO_EXISTE = "00000000-0000-4000-8000-000000000000"


def cuenta_id(numero: int) -> UUID:
    with sesion() as s:
        return s.exec(
            select(CuentaCanonica.cuenta_id).where(CuentaCanonica.cliente_unico == cu(numero))
        ).one()


def _pedir(api, fecha=CORTE, version=None, **cuerpo):
    return api.post(
        "/geografia/ejecuciones",
        json={
            "corte_id": str(corte_de(fecha).corte_id),
            "geografia_version_id": str(version.geografia_version_id),
            **cuerpo,
        },
    )


def _sin_coordenadas(respuesta) -> None:
    """Ni una llave de coordenadas ni un valor que sea una de ellas, en ninguna parte."""
    for llave in ('"latitud', '"longitud', '"lat"', '"lon"', '"punto"'):
        assert llave not in respuesta.text
    for longitud in ("-98.24", "-97.96"):
        assert longitud not in respuesta.text

    def recorrer(valor) -> None:
        if isinstance(valor, dict):
            for v in valor.values():
                recorrer(v)
        elif isinstance(valor, list):
            for v in valor:
                recorrer(v)
        else:
            assert str(valor) not in COORDENADAS, valor

    recorrer(respuesta.json())


@pytest.fixture
def version(tmp_path):
    referencia = importar_referencia(tmp_path)
    ingerir_corte_geo(tmp_path, CORTE, CUENTAS)
    return referencia


@pytest.fixture
def resuelta(cliente, version, trabajar):
    respuesta = _pedir(cliente, version=version)
    assert respuesta.status_code == 201, respuesta.text
    trabajar()
    return respuesta.json()


# --- las versiones de la referencia ---------------------------------------------------------------


def test_las_versiones_de_la_referencia_se_listan_sin_geometrias(cliente, version):
    pagina = cliente.get("/geografia/versiones")
    una = cliente.get(f"/geografia/versiones/{version.geografia_version_id}")

    assert pagina.status_code == una.status_code == 200
    assert pagina.json()["total"] == 1
    cuerpo = una.json()
    assert pagina.json()["elementos"] == [cuerpo]
    assert {k: cuerpo[k] for k in ("proveedor", "producto", "edicion", "estado", "municipios")} == {
        "proveedor": "SINTETICO",
        "producto": "Referencia sintetica de pruebas",
        "edicion": "golden-v1",
        "estado": "PUBLICADA",
        "municipios": 3,
    }
    assert (cuerpo["sha256"], cuerpo["srid"], cuerpo["reparadas"]) == (version.sha256, 4326, 0)
    for geometria in ('"geometria"', "MULTIPOLYGON", "0106000020"):
        assert geometria not in pagina.text and geometria not in una.text


def test_una_version_que_no_existe_es_404_y_un_id_que_no_es_uuid_422(cliente):
    no_existe = cliente.get(f"/geografia/versiones/{NO_EXISTE}")
    invalido = cliente.get("/geografia/versiones/no-es-un-uuid")

    assert (no_existe.status_code, no_existe.json()["codigo"]) == (
        404,
        "GEOGRAFIA_VERSION_NO_ENCONTRADA",
    )
    assert (invalido.status_code, invalido.json()["codigo"]) == (422, "ENTRADA_INVALIDA")


# --- pedirla --------------------------------------------------------------------------------------


def test_pedirla_responde_201_en_proceso_y_un_worker_la_publica(cliente, version, trabajar):
    respuesta = _pedir(cliente, version=version)

    assert respuesta.status_code == 201, respuesta.text
    cuerpo = respuesta.json()
    assert respuesta.headers["location"] == f"/geografia/ejecuciones/{cuerpo['geografia_run_id']}"
    assert (cuerpo["estado"], cuerpo["resultado"], cuerpo["version_motor"]) == (
        "EN_PROCESO",
        None,
        "geografia/v1",
    )
    assert cuerpo["corte"] == {
        "corte_id": str(corte_de(CORTE).corte_id),
        "fecha_corte": "2026-09-30",
    }
    assert cuerpo["referencia"]["geografia_version_id"] == str(version.geografia_version_id)
    assert cuerpo["conteos"]["cuentas_objetivo"] == 4
    assert sum(v for k, v in cuerpo["conteos"].items() if k != "cuentas_objetivo") == 0
    assert len(cuerpo["firma_inputs"]) == 64 and cuerpo["trabajo_id"] is not None

    trabajar()
    terminada = cliente.get(respuesta.headers["location"]).json()
    assert (terminada["estado"], terminada["resultado"]) == ("EXITOSA", "GEOGRAFIA_PUBLICADA")
    assert terminada["conteos"] == {
        "cuentas_objetivo": 4,
        "validas": 2,
        "sin_coordenadas": 1,
        "incompletas": 0,
        "invalidas": 0,
        "fuera_cobertura": 0,
        "ambiguas": 0,
        "discrepantes": 1,
    }
    assert terminada["firma_inputs"] == cuerpo["firma_inputs"]
    _sin_coordenadas(cliente.get(respuesta.headers["location"]))


def test_en_proceso_y_ya_generada_son_409(cliente, version, trabajar):
    primera = _pedir(cliente, version=version)
    en_proceso = _pedir(cliente, version=version)
    trabajar()
    repetida = _pedir(cliente, version=version)

    assert primera.status_code == 201
    assert (en_proceso.status_code, en_proceso.json()["codigo"]) == (409, "GEOGRAFIA_EN_PROCESO")
    assert (repetida.status_code, repetida.json()["codigo"]) == (409, "GEOGRAFIA_YA_GENERADA")
    assert primera.json()["geografia_run_id"] in repetida.json()["mensaje"]


def test_lo_que_no_se_puede_pedir_responde_su_error_sin_registrar_nada(cliente, version, tmp_path):
    corte = str(corte_de(CORTE).corte_id)
    with pytest.raises(ImportacionFallida) as fallo:
        importar_referencia(
            tmp_path, (rectangulo(PUEBLA, "Repetido", -98.3, 18.95, -98.1, 19.15),) * 2, edicion="x"
        )
    fallida = str(fallo.value.geografia_version_id)
    publicada = str(version.geografia_version_id)

    casos = {
        (NO_EXISTE, publicada): (404, "CORTE_NO_ENCONTRADO"),
        (corte, NO_EXISTE): (404, "GEOGRAFIA_VERSION_NO_ENCONTRADA"),
        (corte, fallida): (422, "GEOGRAFIA_VERSION_NO_PUBLICADA"),
        (corte, "no-es-un-uuid"): (422, "ENTRADA_INVALIDA"),
    }
    for (corte_id, version_id), esperado in casos.items():
        respuesta = cliente.post(
            "/geografia/ejecuciones",
            json={"corte_id": corte_id, "geografia_version_id": version_id},
        )
        assert (respuesta.status_code, respuesta.json()["codigo"]) == esperado, respuesta.text
    sin_version = cliente.post("/geografia/ejecuciones", json={"corte_id": corte})
    assert (sin_version.status_code, sin_version.json()["codigo"]) == (422, "ENTRADA_INVALIDA")
    with sesion() as s:
        assert s.exec(select(func.count()).select_from(EjecucionGeografia)).one() == 0
        assert (
            s.exec(
                select(func.count())
                .select_from(TrabajoOrquestacion)
                .where(TrabajoOrquestacion.tipo == "GEOGRAFIA")
            ).one()
            == 0
        )


def test_sin_la_clave_es_401(cliente, version):
    respuesta = cliente.post(
        "/geografia/ejecuciones",
        json={
            "corte_id": str(corte_de(CORTE).corte_id),
            "geografia_version_id": str(version.geografia_version_id),
        },
        headers={"X-API-Key": ""},
    )
    assert respuesta.status_code == 401
    assert cliente.get("/geografia/versiones", headers={"X-API-Key": ""}).status_code == 401


# --- leerla ---------------------------------------------------------------------------------------


def test_una_ejecucion_que_no_existe_es_404(cliente):
    for ruta in (
        f"/geografia/ejecuciones/{NO_EXISTE}",
        f"/geografia/ejecuciones/{NO_EXISTE}/resultados",
    ):
        respuesta = cliente.get(ruta)
        assert (respuesta.status_code, respuesta.json()["codigo"]) == (
            404,
            "GEOGRAFIA_NO_ENCONTRADA",
        )
    assert cliente.get("/geografia/ejecuciones/no-es-un-uuid").status_code == 422


def test_los_resultados_se_paginan_en_el_orden_de_la_cuenta_sin_coordenadas(cliente, resuelta):
    ruta = f"/geografia/ejecuciones/{resuelta['geografia_run_id']}/resultados"

    todas = cliente.get(ruta)
    segunda = cliente.get(ruta, params={"pagina": 2, "por_pagina": 2})

    assert todas.status_code == 200
    elementos = todas.json()["elementos"]
    assert [e["cliente_unico"] for e in elementos] == [cu(n) for n in (1, 2, 3, 4)]
    assert [e["cliente_unico"] for e in segunda.json()["elementos"]] == [cu(3), cu(4)]
    assert segunda.json()["total"] == 4
    uno = elementos[0]
    assert {
        k: uno[k] for k in ("estado_geografia", "municipio_fuente", "comparacion_municipio")
    } == {
        "estado_geografia": "VALIDA",
        "municipio_fuente": PUEBLA,
        "comparacion_municipio": "COINCIDE",
    }
    assert uno["municipio"] == {"cvegeo": PUEBLA, "nombre_oficial": "Municipio A (sintetico)"}
    assert uno["source_row"] >= 1
    assert elementos[2]["municipio"] is None
    _sin_coordenadas(todas)


def test_las_anomalias_y_un_municipio_se_filtran(cliente, resuelta):
    ruta = f"/geografia/ejecuciones/{resuelta['geografia_run_id']}/resultados"

    discrepantes = cliente.get(ruta, params={"estado_geografia": "DISCREPANCIA_MUNICIPIO"}).json()
    de_b = cliente.get(ruta, params={"municipio": AMOZOC}).json()
    malo = cliente.get(ruta, params={"estado_geografia": "CERCA"})
    clave_mala = cliente.get(ruta, params={"municipio": "211"})

    assert [e["cliente_unico"] for e in discrepantes["elementos"]] == [cu(4)]
    cuatro = discrepantes["elementos"][0]
    assert (cuatro["municipio"]["cvegeo"], cuatro["municipio_fuente"]) == (PUEBLA, AMOZOC)
    assert cuatro["comparacion_municipio"] == "DISCREPA"
    assert cuatro["motivos"][-1]["codigo"] == "DISCREPA_DE_FUENTE"
    assert [e["cliente_unico"] for e in de_b["elementos"]] == [cu(2)]
    assert malo.status_code == clave_mala.status_code == 422


def test_las_ejecuciones_se_listan_y_se_filtran_por_corte(cliente, resuelta):
    todas = cliente.get("/geografia/ejecuciones").json()
    del_corte = cliente.get(
        "/geografia/ejecuciones", params={"corte_id": str(corte_de(CORTE).corte_id)}
    ).json()
    sin_corte = cliente.get("/geografia/ejecuciones", params={"corte_id": NO_EXISTE})

    assert todas["total"] == del_corte["total"] == 1
    assert todas["elementos"][0]["geografia_run_id"] == resuelta["geografia_run_id"]
    assert (sin_corte.status_code, sin_corte.json()["codigo"]) == (404, "CORTE_NO_ENCONTRADO")


# --- la de una cuenta, y la Cuenta 360 ------------------------------------------------------------


def test_la_geografia_de_una_cuenta_a_traves_de_los_cortes(cliente, version, tmp_path, trabajar):
    ingerir_corte_geo(tmp_path, OTRO_CORTE, [CuentaGeo(1, PUEBLA, "19.1", "-97.96")], nombre="otra")
    for fecha in (CORTE, OTRO_CORTE):
        assert _pedir(cliente, fecha, version).status_code == 201
    trabajar()

    respuesta = cliente.get(f"/cuentas/{cuenta_id(1)}/geografia")

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert (cuerpo["cliente_unico"], cuerpo["total"]) == (cu(1), 2)
    assert [
        (e["corte"]["fecha_corte"], e["estado_geografia"], e["comparacion_municipio"])
        for e in cuerpo["elementos"]
    ] == [
        ("2026-10-07", "DISCREPANCIA_MUNICIPIO", "DISCREPA"),
        ("2026-09-30", "VALIDA", "COINCIDE"),
    ]
    assert cuerpo["elementos"][0]["referencia"]["sha256"] == version.sha256
    _sin_coordenadas(respuesta)
    no_existe = cliente.get(f"/cuentas/{uuid4()}/geografia")
    assert (no_existe.status_code, no_existe.json()["codigo"]) == (404, "CUENTA_NO_ENCONTRADA")


def test_la_cuenta_360_trae_su_geografia_vigente_y_su_base_sin_coordenadas(
    cliente, resuelta, trabajar
):
    bases = cliente.post(
        "/bases/versiones",
        json={"nombre": "Bases", "bases": [base("BASE_A", 19.05, -98.25, [PUEBLA])]},
    ).json()
    asignacion = cliente.post(
        "/territorial-v2",
        json={
            "corte_id": str(corte_de(CORTE).corte_id),
            "geografia_run_id": resuelta["geografia_run_id"],
            "bases_version_id": bases["bases_version_id"],
        },
    )
    assert asignacion.status_code == 201, asignacion.text
    trabajar()

    uno = cliente.get(f"/cuentas/{cuenta_id(1)}")
    antes = cliente.get(f"/cuentas/{cuenta_id(1)}", params={"al": "2026-09-29"}).json()
    tres = cliente.get(f"/cuentas/{cuenta_id(3)}").json()["geografia_resumen"]

    resumen = uno.json()["geografia_resumen"]
    assert resumen == {
        "geografia_run_id": resuelta["geografia_run_id"],
        "corte_id": str(corte_de(CORTE).corte_id),
        "fecha_corte": "2026-09-30",
        "geografia_version_id": resuelta["referencia"]["geografia_version_id"],
        "referencia": "Referencia sintetica de pruebas (golden-v1)",
        "estado_geografia": "VALIDA",
        "cvegeo": PUEBLA,
        "municipio": "Municipio A (sintetico)",
        "municipio_fuente": PUEBLA,
        "comparacion_municipio": "COINCIDE",
        "territorial": {
            "territorial_run_id": asignacion.json()["territorial_run_id"],
            "bases_version_id": bases["bases_version_id"],
            "version_politica": "territorial-public-v2",
            "estado_asignacion": "ASIGNADA",
            "codigo_base": "BASE_A",
            "nombre_base": "Base BASE_A (sintetica)",
            "distancia_base_m": "1052.61",
        },
    }
    assert antes["geografia_resumen"] is None
    assert (tres["estado_geografia"], tres["cvegeo"]) == ("SIN_COORDENADAS", None)
    assert tres["territorial"]["estado_asignacion"] == "REVISION_GEOGRAFICA"
    _sin_coordenadas(uno)
