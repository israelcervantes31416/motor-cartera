"""Las bases operativas y territorial/v2 por la API, contra PostgreSQL: publicar una version de
bases (y reutilizarla), leerla, pedir la asignacion de un corte con sus entradas declaradas, leer su
ejecucion y sus asignaciones con filtros, y las de una cuenta a traves de los cortes."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from geografia_escenarios import (
    AMOZOC,
    MERIDA,
    MUNICIPIOS,
    PUEBLA,
    CuentaGeo,
    abrir_geo,
    corte_de,
    ejecucion,
    importar_referencia,
    ingerir_corte_geo,
)
from geografia_escenarios import cliente as cu
from sqlalchemy import func
from sqlmodel import select
from territorial_v2_escenarios import base

from motor_cartera.db.modelos import (
    CuentaCanonica,
    EjecucionTerritorialV2,
    TrabajoOrquestacion,
    VersionBasesOperativas,
)
from motor_cartera.db.sesion import sesion

pytestmark = pytest.mark.usefixtures("bd")

CORTE, OTRO_CORTE = date(2026, 9, 30), date(2026, 10, 7)
CUENTAS = [
    CuentaGeo(1, PUEBLA, "19.05", "-98.24"),
    CuentaGeo(2, AMOZOC, "19.1", "-97.96"),
    CuentaGeo(3, PUEBLA),
    CuentaGeo(4, MERIDA, "21.0", "-89.6"),
]
BASES = [
    base("BASE_A", 19.05, -98.25, [PUEBLA]),
    base("BASE_B", 19.05, -98.05, [PUEBLA, AMOZOC]),
    base("BASE_Z", 21.0, -89.5, [MERIDA], activa=False),
]
NO_EXISTE = "00000000-0000-4000-8000-000000000000"


def _bases(api, bases=BASES, nombre="Bases sinteticas"):
    return api.post("/bases/versiones", json={"nombre": nombre, "bases": bases})


def _geografia(api, trabajar, version, fecha=CORTE) -> str:
    respuesta = api.post(
        "/geografia/ejecuciones",
        json={
            "corte_id": str(corte_de(fecha).corte_id),
            "geografia_version_id": str(version.geografia_version_id),
        },
    )
    assert respuesta.status_code == 201, respuesta.text
    trabajar()
    return respuesta.json()["geografia_run_id"]


def _pedir(api, geografia_run_id, bases_version_id, fecha=CORTE, **cuerpo):
    return api.post(
        "/territorial-v2",
        json={
            "corte_id": str(corte_de(fecha).corte_id),
            "geografia_run_id": geografia_run_id,
            "bases_version_id": bases_version_id,
            **cuerpo,
        },
    )


@pytest.fixture
def entradas(cliente, tmp_path, trabajar):
    referencia = importar_referencia(tmp_path)
    ingerir_corte_geo(tmp_path, CORTE, CUENTAS)
    bases = _bases(cliente)
    assert bases.status_code == 201, bases.text
    return {
        "referencia": referencia,
        "geografia": _geografia(cliente, trabajar, referencia),
        "bases": bases.json()["bases_version_id"],
    }


@pytest.fixture
def asignada(cliente, entradas, trabajar):
    respuesta = _pedir(cliente, entradas["geografia"], entradas["bases"])
    assert respuesta.status_code == 201, respuesta.text
    trabajar()
    return respuesta.json()


# --- las bases ------------------------------------------------------------------------------------


def test_publicar_una_version_de_bases_responde_201_y_la_misma_configuracion_200(cliente):
    primera = _bases(cliente)
    otra_etiqueta = _bases(cliente, list(reversed(BASES)), nombre="Otra etiqueta")

    assert primera.status_code == 201, primera.text
    cuerpo = primera.json()
    assert primera.headers["location"] == f"/bases/versiones/{cuerpo['bases_version_id']}"
    assert {k: cuerpo[k] for k in ("nombre", "estado", "bases", "activas", "coberturas")} == {
        "nombre": "Bases sinteticas",
        "estado": "PUBLICADA",
        "bases": 3,
        "activas": 2,
        "coberturas": 4,
    }
    assert (cuerpo["version_configuracion"], len(cuerpo["hash_configuracion"])) == (
        "bases-operativas/v1",
        64,
    )
    assert cuerpo["reutilizada"] is False
    # El mismo contenido, en otro orden y con otro nombre, es la misma version.
    assert otra_etiqueta.status_code == 200
    assert otra_etiqueta.json()["bases_version_id"] == cuerpo["bases_version_id"]
    assert otra_etiqueta.json()["reutilizada"] is True
    with sesion() as s:
        assert s.exec(select(func.count()).select_from(VersionBasesOperativas)).one() == 1


def test_una_configuracion_invalida_es_422_con_todos_sus_problemas(cliente):
    respuesta = _bases(
        cliente,
        [
            base("BASE_A", 95.0, -98.25, [PUEBLA]),
            base("BASE_A", 0, 0, ["2111"]),
        ],
    )

    assert (respuesta.status_code, respuesta.json()["codigo"]) == (422, "CONFIGURACION_INVALIDA")
    problemas = " | ".join(d["problema"] for d in respuesta.json()["detalles"])
    for dice in ("codigos repetidos: BASE_A", "lat esta entre", "(0, 0)", "no son CVEGEO"):
        assert dice in problemas
    with sesion() as s:
        assert s.exec(select(func.count()).select_from(VersionBasesOperativas)).one() == 0


@pytest.mark.parametrize(
    "cuerpo",
    [
        {"nombre": "x"},
        {"nombre": "x", "bases": []},
        {"nombre": "x", "bases": [{**base("BASE_A", 19.0, -98.0, [PUEBLA]), "capacidad": 10}]},
        {"nombre": "x", "bases": [{**base("BASE_A", 19.0, -98.0, [PUEBLA]), "lat": "19.0"}]},
        {"nombre": "x", "bases": [base("base a", 19.0, -98.0, [PUEBLA])]},
    ],
)
def test_un_documento_sin_la_forma_de_una_version_es_entrada_invalida(cliente, cuerpo):
    respuesta = cliente.post("/bases/versiones", json=cuerpo)
    assert (respuesta.status_code, respuesta.json()["codigo"]) == (422, "ENTRADA_INVALIDA")


def test_una_version_de_bases_se_lee_con_cada_base_y_su_hash_verificado(cliente):
    publicada = _bases(cliente).json()

    lista = cliente.get("/bases/versiones").json()
    una = cliente.get(f"/bases/versiones/{publicada['bases_version_id']}").json()
    no_existe = cliente.get(f"/bases/versiones/{NO_EXISTE}")

    assert (
        lista["total"] == 1 and lista["elementos"][0]["bases_version_id"] == una["bases_version_id"]
    )
    assert una["hash_verificado"] is True
    assert [
        (b["codigo"], b["activa"], b["lat"], b["lon"], b["municipios"])
        for b in una["detalle_bases"]
    ] == [
        ("BASE_A", True, 19.05, -98.25, [PUEBLA]),
        ("BASE_B", True, 19.05, -98.05, [AMOZOC, PUEBLA]),
        ("BASE_Z", False, 21.0, -89.5, [MERIDA]),
    ]
    assert (no_existe.status_code, no_existe.json()["codigo"]) == (404, "BASES_NO_ENCONTRADAS")


# --- pedir territorial/v2 -------------------------------------------------------------------------


def test_pedirla_responde_201_en_proceso_y_un_worker_la_publica(cliente, entradas, trabajar):
    respuesta = _pedir(cliente, entradas["geografia"], entradas["bases"])

    assert respuesta.status_code == 201, respuesta.text
    cuerpo = respuesta.json()
    assert respuesta.headers["location"] == f"/territorial-v2/{cuerpo['territorial_run_id']}"
    assert (cuerpo["estado"], cuerpo["version_motor"], cuerpo["version_politica"]) == (
        "EN_PROCESO",
        "territorial/v2",
        "territorial-public-v2",
    )
    assert cuerpo["geografia_run_id"] == entradas["geografia"]
    assert cuerpo["bases"]["bases_version_id"] == entradas["bases"]
    assert cuerpo["tolerancia_empate_m"] == "100.00"
    assert len(cuerpo["hash_politica"]) == len(cuerpo["firma_inputs"]) == 64
    assert "capacidad" in cuerpo["aviso"]

    trabajar()
    terminada = cliente.get(respuesta.headers["location"]).json()
    assert (terminada["estado"], terminada["resultado"]) == ("EXITOSA", "TERRITORIAL_PUBLICADO")
    assert terminada["conteos"] == {
        "cuentas_objetivo": 4,
        "asignadas": 2,
        "revision_geografica": 1,
        "sin_base": 1,
        "fuera_cobertura": 0,
        "ambiguas": 0,
    }
    assert set(terminada["distribucion"]["por_base"]) == {"BASE_A", "BASE_B"}


def test_en_proceso_y_ya_generado_son_409(cliente, entradas, trabajar):
    primera = _pedir(cliente, entradas["geografia"], entradas["bases"])
    en_proceso = _pedir(cliente, entradas["geografia"], entradas["bases"])
    trabajar()
    repetida = _pedir(cliente, entradas["geografia"], entradas["bases"])

    assert primera.status_code == 201
    assert (en_proceso.status_code, en_proceso.json()["codigo"]) == (
        409,
        "TERRITORIAL_V2_EN_PROCESO",
    )
    assert (repetida.status_code, repetida.json()["codigo"]) == (409, "TERRITORIAL_V2_YA_GENERADO")


def test_lo_que_no_se_puede_pedir_responde_su_error_sin_registrar_nada(cliente, entradas, tmp_path):
    geografia, bases = entradas["geografia"], entradas["bases"]
    otra = importar_referencia(tmp_path, MUNICIPIOS[:2], edicion="otra")
    en_proceso = str(ejecucion(abrir_geo(CORTE, otra)).geografia_run_id)
    ingerir_corte_geo(tmp_path, OTRO_CORTE, [CuentaGeo(1, PUEBLA)], nombre="otra")

    casos = [
        ({"corte_id": NO_EXISTE}, (404, "CORTE_NO_ENCONTRADO")),
        ({"geografia_run_id": NO_EXISTE}, (404, "GEOGRAFIA_NO_ENCONTRADA")),
        ({"geografia_run_id": en_proceso}, (422, "GEOGRAFIA_NO_EXITOSA")),
        (
            {"corte_id": str(corte_de(OTRO_CORTE).corte_id)},
            (422, "GEOGRAFIA_DE_OTRO_CORTE"),
        ),
        ({"bases_version_id": NO_EXISTE}, (404, "BASES_NO_ENCONTRADAS")),
        ({"version_politica": "territorial-public-v9"}, (422, "POLITICA_NO_SOPORTADA")),
        ({"bases_version_id": "no-es-un-uuid"}, (422, "ENTRADA_INVALIDA")),
        ({"capacidad": 10}, (422, "ENTRADA_INVALIDA")),
    ]
    for cambio, esperado in casos:
        cuerpo = {
            "corte_id": str(corte_de(CORTE).corte_id),
            "geografia_run_id": geografia,
            "bases_version_id": bases,
            **cambio,
        }
        respuesta = cliente.post("/territorial-v2", json=cuerpo)
        assert (respuesta.status_code, respuesta.json()["codigo"]) == esperado, cambio
    with sesion() as s:
        assert s.exec(select(func.count()).select_from(EjecucionTerritorialV2)).one() == 0
        assert (
            s.exec(
                select(func.count())
                .select_from(TrabajoOrquestacion)
                .where(TrabajoOrquestacion.tipo == "TERRITORIAL_V2")
            ).one()
            == 0
        )


def test_sin_la_clave_es_401(cliente, entradas):
    sin_clave = {"X-API-Key": ""}
    assert (
        cliente.post(
            "/territorial-v2",
            json={
                "corte_id": str(corte_de(CORTE).corte_id),
                "geografia_run_id": entradas["geografia"],
                "bases_version_id": entradas["bases"],
            },
            headers=sin_clave,
        ).status_code
        == 401
    )
    assert cliente.post("/bases/versiones", json={}, headers=sin_clave).status_code == 401
    assert cliente.get("/bases/versiones", headers=sin_clave).status_code == 401


# --- leerla ---------------------------------------------------------------------------------------


def test_una_ejecucion_que_no_existe_es_404(cliente):
    for ruta in (f"/territorial-v2/{NO_EXISTE}", f"/territorial-v2/{NO_EXISTE}/resultados"):
        respuesta = cliente.get(ruta)
        assert (respuesta.status_code, respuesta.json()["codigo"]) == (
            404,
            "TERRITORIAL_V2_NO_ENCONTRADO",
        )
    assert cliente.get("/territorial-v2/no-es-un-uuid").status_code == 422


def test_las_asignaciones_se_paginan_y_se_filtran_por_base_municipio_y_estado(cliente, asignada):
    ruta = f"/territorial-v2/{asignada['territorial_run_id']}/resultados"

    todas = cliente.get(ruta)
    de_a = cliente.get(ruta, params={"base": "BASE_A"}).json()
    de_puebla = cliente.get(ruta, params={"municipio": PUEBLA}).json()
    sin_base = cliente.get(ruta, params={"estado": "SIN_BASE_ELEGIBLE"}).json()
    pagina = cliente.get(ruta, params={"pagina": 2, "por_pagina": 3}).json()

    assert todas.status_code == 200
    elementos = todas.json()["elementos"]
    assert [e["cliente_unico"] for e in elementos] == [cu(n) for n in (1, 2, 3, 4)]
    uno = elementos[0]
    assert uno["estado_asignacion"] == "ASIGNADA"
    assert uno["base"]["codigo"] == "BASE_A" and uno["distancia_base_m"] == "1052.61"
    assert uno["municipio"] == {"cvegeo": PUEBLA, "nombre_oficial": "Municipio A (sintetico)"}
    assert (elementos[2]["estado_geografia"], elementos[2]["base"]) == ("SIN_COORDENADAS", None)
    assert [e["cliente_unico"] for e in de_a["elementos"]] == [cu(1)] and de_a["total"] == 1
    # El total de una base sale de lo que la ejecucion conto al publicar; con otro estado que no
    # sea ASIGNADA, una base no tiene ninguna cuenta.
    for estado, total in (("ASIGNADA", 1), ("SIN_BASE_ELEGIBLE", 0), ("REVISION_GEOGRAFICA", 0)):
        cruzada = cliente.get(ruta, params={"base": "BASE_A", "estado": estado}).json()
        assert (cruzada["total"], len(cruzada["elementos"])) == (total, total), estado
    inactiva = cliente.get(ruta, params={"base": "BASE_Z"}).json()
    assert (inactiva["total"], inactiva["elementos"]) == (0, [])
    assert [e["cliente_unico"] for e in de_puebla["elementos"]] == [cu(1)]
    assert [e["cliente_unico"] for e in sin_base["elementos"]] == [cu(4)]
    assert sin_base["elementos"][0]["motivos"][-1] == {
        "codigo": "BASES_INACTIVAS",
        "bases": ["BASE_Z"],
    }
    assert (pagina["total"], [e["cliente_unico"] for e in pagina["elementos"]]) == (4, [cu(4)])
    for malo in ({"base": "base a"}, {"municipio": "211"}, {"estado": "CERCA"}):
        assert cliente.get(ruta, params=malo).status_code == 422
    for llave in ('"latitud', '"longitud', '"lat"', '"lon"', '"punto"', "-98.24", "-97.96"):
        assert llave not in todas.text


def test_las_ejecuciones_se_listan_y_se_filtran_por_corte(cliente, asignada):
    todas = cliente.get("/territorial-v2").json()
    del_corte = cliente.get(
        "/territorial-v2", params={"corte_id": str(corte_de(CORTE).corte_id)}
    ).json()
    sin_corte = cliente.get("/territorial-v2", params={"corte_id": NO_EXISTE})

    assert todas["total"] == del_corte["total"] == 1
    assert todas["elementos"][0]["territorial_run_id"] == asignada["territorial_run_id"]
    assert (sin_corte.status_code, sin_corte.json()["codigo"]) == (404, "CORTE_NO_ENCONTRADO")


def test_las_asignaciones_de_una_cuenta_a_traves_de_los_cortes(
    cliente, entradas, asignada, tmp_path, trabajar
):
    ingerir_corte_geo(tmp_path, OTRO_CORTE, [CuentaGeo(1, AMOZOC, "19.1", "-97.96")], nombre="otra")
    geografia_otra = _geografia(cliente, trabajar, entradas["referencia"], OTRO_CORTE)
    assert _pedir(cliente, geografia_otra, entradas["bases"], OTRO_CORTE).status_code == 201
    trabajar()
    with sesion() as s:
        uno = s.exec(
            select(CuentaCanonica.cuenta_id).where(CuentaCanonica.cliente_unico == cu(1))
        ).one()

    respuesta = cliente.get(f"/cuentas/{uno}/territorial-v2")

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["total"] == 2
    assert [
        (e["corte"]["fecha_corte"], e["estado_asignacion"], e["base"]["codigo"])
        for e in cuerpo["elementos"]
    ] == [("2026-10-07", "ASIGNADA", "BASE_B"), ("2026-09-30", "ASIGNADA", "BASE_A")]
    viejo = cuerpo["elementos"][1]
    assert (viejo["territorial_run_id"], viejo["geografia_run_id"], viejo["bases_version_id"]) == (
        asignada["territorial_run_id"],
        entradas["geografia"],
        entradas["bases"],
    )
    no_existe = cliente.get(f"/cuentas/{uuid4()}/territorial-v2")
    assert (no_existe.status_code, no_existe.json()["codigo"]) == (404, "CUENTA_NO_ENCONTRADA")
