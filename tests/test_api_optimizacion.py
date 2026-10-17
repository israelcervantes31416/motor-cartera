"""optimization/v1 por la API, contra PostgreSQL, con el golden en la base
(optimizacion_escenarios): la politica, registrar y publicar configuraciones, pedir una ejecucion
(y lo que no se puede pedir), leerla, sus resultados con cada filtro de su lista blanca y su
paginacion, el uso de cada pool, cada restriccion, el diagnostico, la explicacion de una cuenta y
sus planes; y que ninguna respuesta lleve un dato personal ni una coordenada.

El escenario se construye una vez por modulo, como lo haria la operacion: el POST deja la ejecucion
EN_PROCESO con su trabajo en la cola, y un worker resuelve el MILP; la peticion nunca.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from optimizacion_escenarios import (
    GOLDEN,
    documento_golden,
    escenario_golden,
    restriccion,
    resultados,
)
from sqlalchemy import event, func, update

from motor_cartera.api import crear_app
from motor_cartera.config import Config
from motor_cartera.db.modelos import EstadoTrabajo, TrabajoOrquestacion
from motor_cartera.db.sesion import crear_motor, sesion
from motor_cartera.optimizacion import solver as solver_
from motor_cartera.optimizacion.politica import hash_politica
from motor_cartera.orquestacion.worker import identificador_worker, procesar_un_trabajo

NO_EXISTE = "00000000-0000-4000-8000-000000000000"
CLAVE = "clave-de-prueba"
LLAVES_PROHIBIDAS = (
    '"cliente_unico"',
    '"nombre_cte"',
    '"direccion"',
    '"domicilio"',
    '"telefono"',
    '"actor_ref"',
    '"latitud"',
    '"longitud"',
    '"lat"',
    '"lon"',
    '"coordenadas"',
    '"geometry"',
    '"geometria"',
    '"uplift"',
    '"incremental_recovery"',
    '"expected_channel_effect"',
)
CLIENTE_UNICO = re.compile(r"CU\d{8}")


def _sin_datos_personales(cuerpo) -> None:
    texto = json.dumps(cuerpo)
    for llave in LLAVES_PROHIBIDAS:
        assert llave not in texto, llave
    assert not CLIENTE_UNICO.search(texto)


@pytest.fixture(scope="module")
def cliente(bd_del_modulo):
    app = crear_app(Config(api_key=CLAVE, tolerancia_rechazo=0.05, tamano_maximo_mb=1))
    with TestClient(app, headers={"X-API-Key": CLAVE}) as c:
        yield c


def _cola_vacia() -> None:
    with sesion() as s:
        s.execute(
            update(TrabajoOrquestacion)
            .where(TrabajoOrquestacion.estado.in_(("PENDIENTE", "EJECUTANDO")))
            .values(
                estado=EstadoTrabajo.COMPLETADO,
                terminado_en=func.now(),
                worker_id=None,
                lease_hasta=None,
            )
        )
        s.commit()


def _trabajar() -> list:
    worker_id, procesados = identificador_worker(), []
    while (procesado := procesar_un_trabajo(worker_id, Config())) is not None:
        procesados.append(procesado)
    return procesados


@pytest.fixture(scope="module")
def g(bd_del_modulo, tmp_path_factory):
    e = escenario_golden(tmp_path_factory.mktemp("api_optimizacion"))
    _cola_vacia()
    return e


def _pedir(cliente, g, configuracion, **cambios):
    return cliente.post(
        "/optimizacion/ejecuciones",
        json={
            "scoring_run_id": str(g.scoring.scoring_run_id),
            "decision_run_id": str(g.decision.decision_run_id),
            "configuracion_id": str(configuracion),
            "fecha_operacion": "2026-10-01",
            **cambios,
        },
    )


def _configuracion(cliente, documento: dict, *, publicar: bool = True) -> dict:
    r = cliente.post(
        f"/optimizacion/configuraciones?publicar={str(publicar).lower()}", json=documento
    )
    assert r.status_code in (200, 201), r.text
    return r.json()


@pytest.fixture(scope="module")
def publicado(cliente, g):
    configuracion = _configuracion(cliente, documento_golden())
    r = _pedir(cliente, g, configuracion["configuracion_id"])
    assert r.status_code == 201, r.text
    cuerpo = r.json()
    # 201 quiere decir que se creo: el MILP no corrio en la peticion.
    assert (cuerpo["estado"], cuerpo["hash_plan"], cuerpo["cuentas_publicadas"]) == (
        "EN_PROCESO",
        None,
        0,
    )
    assert r.headers["Location"] == f"/optimizacion/ejecuciones/{cuerpo['optimizacion_run_id']}"
    (procesado,) = _trabajar()
    assert procesado.estado == EstadoTrabajo.COMPLETADO
    leido = cliente.get(r.headers["Location"]).json()
    assert leido["estado"] == "EXITOSA", leido["detalle"]
    return {"configuracion": configuracion, "ejecucion": leido}


def _de(cliente, run_id: str, ruta: str, **parametros) -> dict:
    r = cliente.get(f"/optimizacion/ejecuciones/{run_id}/{ruta}", params=parametros)
    assert r.status_code == 200, r.text
    _sin_datos_personales(r.json())
    return r.json()


# --- la politica y las configuraciones ------------------------------------------------------------


def test_la_politica(cliente):
    r = cliente.get("/optimizacion/politica")
    assert r.status_code == 200
    p = r.json()
    assert (p["version"], p["version_motor"], p["hash"]) == (
        "optimization-public-v1",
        "optimization/v1",
        hash_politica(),
    )
    assert p["solver_fingerprint"] == solver_.hash_huella(p["huella_solver"])
    assert p["huella_solver"]["highs"] and p["huella_solver"]["scipy"]
    assert p["definicion"]["valor"]["no_se_multiplica_por"][0] == "p_recuperacion_positiva_30d"
    assert any("causalmente optima" in x for x in p["limitaciones"])
    assert cliente.get("/optimizacion/politica", headers={"X-API-Key": ""}).status_code == 401


def test_registrar_y_leer_configuraciones(cliente):
    documento = documento_golden(nombre="Configuracion de la API", max_unidades_recurso=77)
    r = cliente.post("/optimizacion/configuraciones", json=documento)
    assert r.status_code == 201, r.text
    creada = r.json()
    assert r.headers["Location"] == f"/optimizacion/configuraciones/{creada['configuracion_id']}"
    assert (creada["estado"], creada["reutilizada"], creada["hash_verificado"]) == (
        "BORRADOR",
        False,
        True,
    )
    assert [p["codigo"] for p in creada["detalle_pools"]] == ["DIGITAL_GLOBAL", "TELEFONICA_GLOBAL"]
    assert creada["supuestos"] == "SUPUESTOS SINTETICOS de las pruebas."
    otra = cliente.post("/optimizacion/configuraciones", json=documento)
    assert (otra.status_code, otra.json()["reutilizada"]) == (200, True)
    assert otra.json()["configuracion_id"] == creada["configuracion_id"]
    publicada = cliente.post(
        f"/optimizacion/configuraciones/{creada['configuracion_id']}/publicacion"
    )
    assert (publicada.status_code, publicada.json()["estado"]) == (200, "PUBLICADA")
    assert publicada.json()["hash_contenido"] == creada["hash_contenido"]
    lista = cliente.get("/optimizacion/configuraciones", params={"estado": "PUBLICADA"}).json()
    assert creada["configuracion_id"] in {c["configuracion_id"] for c in lista["elementos"]}
    una = cliente.get(f"/optimizacion/configuraciones/{creada['configuracion_id']}").json()
    assert (una["estado"], una["hash_verificado"], len(una["admisibilidad"])) == (
        "PUBLICADA",
        True,
        4,
    )
    for ruta in (f"/optimizacion/configuraciones/{NO_EXISTE}",):
        assert cliente.get(ruta).json()["codigo"] == "CONFIGURACION_NO_ENCONTRADA"
    r = cliente.post(f"/optimizacion/configuraciones/{NO_EXISTE}/publicacion")
    assert (r.status_code, r.json()["codigo"]) == (404, "CONFIGURACION_NO_ENCONTRADA")


@pytest.mark.parametrize(
    "cambio, codigo",
    [
        ({"pools": []}, "CONFIGURACION_INVALIDA"),
        (
            {"restricciones": [restriccion("MIN_X", "MIN_CUENTAS", "PRODUCTO", "HIPOTECA", 1)]},
            "CONFIGURACION_INVALIDA",
        ),
        ({"formula": "valor * p_recuperacion"}, "ENTRADA_INVALIDA"),
        ({"permitir_predicciones_con_imputacion": None}, "ENTRADA_INVALIDA"),
    ],
)
def test_una_configuracion_invalida_no_se_registra(cliente, cambio, codigo):
    r = cliente.post("/optimizacion/configuraciones", json=documento_golden(**cambio))
    assert (r.status_code, r.json()["codigo"]) == (422, codigo), r.text
    if codigo == "CONFIGURACION_INVALIDA":
        assert r.json()["detalles"]


def test_el_flag_de_imputacion_es_obligatorio(cliente):
    documento = documento_golden()
    documento.pop("permitir_predicciones_con_imputacion")
    r = cliente.post("/optimizacion/configuraciones", json=documento)
    assert (r.status_code, r.json()["codigo"]) == (422, "ENTRADA_INVALIDA")


# --- las ejecuciones ------------------------------------------------------------------------------


def test_una_ejecucion_publicada(cliente, g, publicado):
    e = publicado["ejecucion"]
    _sin_datos_personales(e)
    assert (e["resultado"], e["solver_status"], e["mip_gap"]) == (
        "PLAN_PUBLICADO",
        "OPTIMAL",
        "0.000000000000",
    )
    assert e["best_bound"] == "31000.0000"
    assert (e["objetivo_primario_centavos"], e["objetivo_secundario"], e["unidades_recurso"]) == (
        31_000,
        190_000_000,
        11,
    )
    assert (e["cuentas_universo"], e["cuentas_elegibles"], e["cuentas_no_elegibles"]) == (9, 7, 2)
    assert (e["scoring_run_id"], e["decision_run_id"], e["ruteo_run_id"]) == (
        str(g.scoring.scoring_run_id),
        str(g.decision.decision_run_id),
        None,
    )
    assert e["hash_politica"] == hash_politica() and len(e["hash_plan"]) == 64
    assert [n["estado"] for n in e["certificado"]["niveles"]] == ["OPTIMAL"] * 4
    assert e["trabajo_id"] and e["duracion_segundos"] >= 0
    assert any("causalmente optima" in x for x in e["limitaciones"])
    lista = cliente.get(
        "/optimizacion/ejecuciones",
        params={"estado": "EXITOSA", "scoring_run_id": str(g.scoring.scoring_run_id)},
    ).json()
    assert e["optimizacion_run_id"] in {x["optimizacion_run_id"] for x in lista["elementos"]}
    por_configuracion = cliente.get(
        "/optimizacion/ejecuciones",
        params={"configuracion_id": publicado["configuracion"]["configuracion_id"]},
    ).json()
    assert por_configuracion["total"] == 1
    for parametro in ("configuracion_id", "scoring_run_id"):
        r = cliente.get("/optimizacion/ejecuciones", params={parametro: NO_EXISTE})
        assert r.status_code == 404
    r = cliente.get(f"/optimizacion/ejecuciones/{NO_EXISTE}")
    assert (r.status_code, r.json()["codigo"]) == (404, "OPTIMIZACION_NO_ENCONTRADA")


@contextmanager
def _sentencias() -> Iterator[list[str]]:
    """El SQL que le llega a la base mientras dura el bloque, una sentencia por ejecucion."""
    motor = crear_motor()
    vistas: list[str] = []

    def anotar(conexion, cursor, sentencia, parametros, contexto, varias):
        vistas.append(sentencia)

    event.listen(motor, "before_cursor_execute", anotar)
    try:
        yield vistas
    finally:
        event.remove(motor, "before_cursor_execute", anotar)


def test_la_lista_de_ejecuciones_no_consulta_una_vez_por_ejecucion(cliente, g, publicado):
    """Una pagina de ejecuciones hace las mismas consultas con una que con todas: lo que cada una
    declaro (su scoring, su modelo, su decision, su ruteo, su configuracion, su corte y su trabajo)
    sale de una sola consulta, no de una por ejecucion."""
    otra = _configuracion(cliente, documento_golden(max_cuentas_atendidas=4))
    assert _pedir(cliente, g, otra["configuracion_id"]).status_code == 201
    _trabajar()
    total = cliente.get("/optimizacion/ejecuciones").json()["total"]
    assert total >= 2
    consultas = []
    for por_pagina in (1, total):
        with _sentencias() as vistas:
            r = cliente.get("/optimizacion/ejecuciones", params={"por_pagina": por_pagina})
        assert r.status_code == 200 and len(r.json()["elementos"]) == por_pagina
        consultas.append(len(vistas))
    assert 0 < consultas[0] == consultas[1]


def test_lo_que_no_se_puede_pedir(cliente, g, publicado):
    configuracion_id = publicado["configuracion"]["configuracion_id"]
    r = _pedir(cliente, g, configuracion_id)
    assert (r.status_code, r.json()["codigo"]) == (409, "OPTIMIZACION_YA_PUBLICADA")
    borrador = _configuracion(cliente, documento_golden(max_unidades_recurso=78), publicar=False)
    r = _pedir(cliente, g, borrador["configuracion_id"])
    assert (r.status_code, r.json()["codigo"]) == (409, "CONFIGURACION_NO_PUBLICADA")
    con_base = _configuracion(
        cliente,
        documento_golden(
            restricciones=[restriccion("MIN_BASE_A", "MIN_CUENTAS", "BASE", "BASE_A", 1)]
        ),
    )
    r = _pedir(cliente, g, con_base["configuracion_id"])
    assert (r.status_code, r.json()["codigo"]) == (422, "RUTEO_REQUERIDO")
    for campo in ("scoring_run_id", "decision_run_id", "configuracion_id", "ruteo_run_id"):
        r = _pedir(cliente, g, configuracion_id, **{campo: NO_EXISTE})
        assert r.status_code == 404, (campo, r.text)
    r = _pedir(cliente, g, configuracion_id, fecha_operacion="2026-09-01")
    assert (r.status_code, r.json()["codigo"]) == (422, "FECHA_ANTERIOR_AL_CORTE")
    r = cliente.post("/optimizacion/ejecuciones", json={"scoring_run_id": NO_EXISTE})
    assert (r.status_code, r.json()["codigo"]) == (422, "ENTRADA_INVALIDA")
    # Lo mismo, EN_PROCESO, otra vez: 409, y el MILP no corrio en ninguna peticion.
    otra = _configuracion(cliente, documento_golden(max_unidades_recurso=79))
    assert _pedir(cliente, g, otra["configuracion_id"]).status_code == 201
    r = _pedir(cliente, g, otra["configuracion_id"])
    assert (r.status_code, r.json()["codigo"]) == (409, "OPTIMIZACION_EN_PROCESO")
    _trabajar()


# --- lo publicado ---------------------------------------------------------------------------------


def _numeros(elementos: list[dict]) -> set[int]:
    por_cuenta = {str(r["cuenta_id"]): n for n, r in _resultados_del_golden().items()}
    return {por_cuenta[x["cuenta_id"]] for x in elementos}


_CACHE: dict = {}


def _resultados_del_golden() -> dict:
    if "golden" not in _CACHE:
        _CACHE["golden"] = resultados(_CACHE["ejecucion_id"])
    return _CACHE["golden"]


@pytest.fixture(scope="module")
def run_id(publicado):
    with sesion() as s:
        from motor_cartera.optimizacion import consultas

        vista = consultas.obtener_ejecucion(s, publicado["ejecucion"]["optimizacion_run_id"])
        _CACHE["ejecucion_id"] = vista.ejecucion.id
    return publicado["ejecucion"]["optimizacion_run_id"]


@pytest.mark.parametrize(
    "filtro, esperadas",
    [
        ({}, set(range(1, 10))),
        ({"estado": "ASIGNADA"}, {1, 2, 3, 4, 9}),
        ({"estado": "NO_ELEGIBLE"}, {7, 8}),
        ({"canal": "TELEFONICA"}, {2, 3}),
        ({"pool": "DIGITAL_GLOBAL"}, {1, 4, 9}),
        ({"producto": "NOMINA"}, {3, 4}),
        ({"prioridad": "ALTA"}, {1, 2, 8}),
        ({"estado": "ASIGNADA", "producto": "CONSUMO"}, {1, 2}),
    ],
)
def test_los_resultados_se_filtran_en_la_base(cliente, run_id, filtro, esperadas):
    pagina = _de(cliente, run_id, "resultados", por_pagina=500, **filtro)
    assert pagina["total"] == len(esperadas)
    assert _numeros(pagina["elementos"]) == esperadas
    for x in pagina["elementos"]:
        assert x["valor_oportunidad_centavos"] == int(
            GOLDEN[_numeros([x]).pop() - 1].valor.replace(".", "")
        )


def test_los_resultados_se_paginan_en_el_orden_de_la_cuenta(cliente, run_id):
    paginas = [_de(cliente, run_id, "resultados", pagina=p, por_pagina=4) for p in (1, 2, 3, 4)]
    assert [len(p["elementos"]) for p in paginas] == [4, 4, 1, 0]
    assert all(p["total"] == 9 for p in paginas)
    vistas = [x["cuenta_id"] for p in paginas for x in p["elementos"]]
    assert len(set(vistas)) == 9


@pytest.mark.parametrize(
    "filtro, estado, codigo",
    [
        ({"pool": "NO_EXISTE"}, 404, "FILTRO_DESCONOCIDO"),
        ({"base": "BASE_A"}, 404, "FILTRO_DESCONOCIDO"),
        ({"estado": "PERDIDA"}, 422, "ENTRADA_INVALIDA"),
        ({"canal": "OTRO"}, 422, "ENTRADA_INVALIDA"),
        ({"cliente_unico": "CU00000001"}, 422, "ENTRADA_INVALIDA"),
        ({"orden": "valor"}, 422, "ENTRADA_INVALIDA"),
    ],
)
def test_un_filtro_fuera_de_la_lista_blanca(cliente, run_id, filtro, estado, codigo):
    r = cliente.get(f"/optimizacion/ejecuciones/{run_id}/resultados", params=filtro)
    assert (r.status_code, r.json()["codigo"]) == (estado, codigo)


def test_la_capacidad_y_las_restricciones(cliente, run_id):
    capacidad = _de(cliente, run_id, "capacidad")
    usos = {u["pool"]: u for u in capacidad["elementos"]}
    assert (usos["DIGITAL_GLOBAL"]["capacidad"], usos["DIGITAL_GLOBAL"]["utilizada"]) == (3, 3)
    assert (usos["TELEFONICA_GLOBAL"]["holgura"], usos["TELEFONICA_GLOBAL"]["utilizacion"]) == (
        0,
        "1.000000",
    )
    assert usos["TELEFONICA_GLOBAL"]["unidades_consumidas"] == 8
    assert capacidad["por_canal"]["DIGITAL"]["utilizada"] == 3
    assert _de(cliente, run_id, "capacidad", canal="TELEFONICA")["total"] == 1
    todas = _de(cliente, run_id, "restricciones")
    codigos = {r["codigo"]: r for r in todas["elementos"]}
    assert (codigos["MIN_AUTOMOTRIZ"]["lhs"], codigos["MIN_AUTOMOTRIZ"]["binding"]) == (1, True)
    activas = _de(cliente, run_id, "restricciones", binding="true")
    assert all(r["binding"] and r["holgura"] == 0 for r in activas["elementos"])
    assert _de(cliente, run_id, "restricciones", tipo="MIN_CUENTAS")["total"] == 1


def test_el_diagnostico(cliente, g, run_id):
    d = _de(cliente, run_id, "diagnostico")
    assert (d["estado"], d["plan_publicado"], d["solver_status"]) == ("EXITOSA", True, "OPTIMAL")
    infactible = _configuracion(
        cliente,
        documento_golden(
            restricciones=[
                restriccion("MIN_AUTOMOTRIZ", "MIN_CUENTAS", "PRODUCTO", "AUTOMOTRIZ", 3)
            ]
        ),
    )
    pedida = _pedir(cliente, g, infactible["configuracion_id"]).json()
    r = cliente.get(f"/optimizacion/ejecuciones/{pedida['optimizacion_run_id']}/diagnostico")
    assert (r.status_code, r.json()["codigo"]) == (409, "OPTIMIZACION_EN_PROCESO")
    _trabajar()
    d = _de(cliente, pedida["optimizacion_run_id"], "diagnostico")
    assert (d["estado"], d["resultado"], d["plan_publicado"]) == ("FALLIDA", "INFACTIBLE", False)
    assert d["diagnostico"]["hallazgos"][0]["codigo"] == "MINIMO_SUPERA_ELEGIBLES"
    for ruta in ("resultados", "capacidad", "restricciones", f"cuentas/{NO_EXISTE}"):
        r = cliente.get(f"/optimizacion/ejecuciones/{pedida['optimizacion_run_id']}/{ruta}")
        assert (r.status_code, r.json()["codigo"]) == (409, "OPTIMIZACION_NO_PUBLICADA"), ruta


def test_la_explicacion_de_una_cuenta(cliente, run_id):
    numeros = _resultados_del_golden()
    asignada = _de(cliente, run_id, f"cuentas/{numeros[1]['cuenta_id']}")
    assert (asignada["resultado"]["estado"], asignada["resultado"]["pool"]) == (
        "ASIGNADA",
        "DIGITAL_GLOBAL",
    )
    assert asignada["pools_elegibles"] == ["DIGITAL_GLOBAL"]
    assert asignada["canales_admisibles"] == ["DIGITAL"]
    assert "no la evidencia de que ese canal cause" in asignada["explicacion"]
    assert asignada["hechos"]["prediccion"] == "COMPLETA"
    assert asignada["grupos"] == [
        {
            "restriccion": "MIN_AUTOMOTRIZ",
            "tipo": "MIN_CUENTAS",
            "dimension": "PRODUCTO",
            "valor": "AUTOMOTRIZ",
            "pertenece": False,
        }
    ]
    no_seleccionada = _de(cliente, run_id, f"cuentas/{numeros[5]['cuenta_id']}")
    assert no_seleccionada["resultado"]["motivo"] == "FUERA_DE_SOLUCION_OPTIMA"
    assert "No se atribuye a una restriccion individual" in no_seleccionada["explicacion"]
    assert no_seleccionada["pools_elegibles"] == ["DIGITAL_GLOBAL", "TELEFONICA_GLOBAL"]
    sin_mapeo = _de(cliente, run_id, f"cuentas/{numeros[7]['cuenta_id']}")
    assert sin_mapeo["resultado"]["motivo"] == "SIN_CANAL_ADMISIBLE"
    assert sin_mapeo["canales_admisibles"] == [] and sin_mapeo["pools_elegibles"] == []
    imputada = _de(cliente, run_id, f"cuentas/{numeros[8]['cuenta_id']}")
    assert imputada["hechos"]["prediccion"] == "CON_IMPUTACION"
    assert imputada["hechos"]["configuracion_admite_imputacion"] is False
    r = cliente.get(f"/optimizacion/ejecuciones/{run_id}/cuentas/{NO_EXISTE}")
    assert (r.status_code, r.json()["codigo"]) == (404, "CUENTA_NO_ENCONTRADA")


def test_los_planes_de_una_cuenta(cliente, run_id):
    cuenta_id = _resultados_del_golden()[1]["cuenta_id"]
    r = cliente.get(f"/cuentas/{cuenta_id}/optimizacion")
    assert r.status_code == 200
    _sin_datos_personales(r.json())
    planes = r.json()["elementos"]
    assert run_id in {p["optimizacion_run_id"] for p in planes}
    golden = next(p for p in planes if p["optimizacion_run_id"] == run_id)
    assert (golden["estado"], golden["pool"], golden["valor_oportunidad_centavos"]) == (
        "ASIGNADA",
        "DIGITAL_GLOBAL",
        9_000,
    )
    r = cliente.get(f"/cuentas/{NO_EXISTE}/optimizacion")
    assert (r.status_code, r.json()["codigo"]) == (404, "CUENTA_NO_ENCONTRADA")


def test_ninguna_ruta_ejecuta_una_accion(cliente):
    rutas = set(cliente.app.openapi()["paths"])
    for prohibida in ("ejecutar", "enviar", "llamar", "visitar", "aplicar"):
        assert not any("optimizacion" in r and prohibida in r for r in rutas), prohibida
