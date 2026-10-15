"""analytics/v1 por la API, contra PostgreSQL, con el escenario golden (`analytics_escenarios`): el
catalogo, pedir una ejecucion con su manifiesto (y lo que no se puede pedir), leerla, sus agregados
con cada filtro y su paginacion, su resumen, sus embudos y sus controles; que no haya analytics por
cuenta, y que ninguna respuesta lleve algo de una cuenta.

El escenario se construye una vez por modulo, como lo haria la operacion: el POST deja la ejecucion
EN_PROCESO con su trabajo en la cola y un worker la publica.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest
from analytics_escenarios import AS_OF_A, AS_OF_B, ZONA
from fastapi.testclient import TestClient
from sqlalchemy import event, func, text, update

from motor_cartera.analytics.catalogo import METRICAS, hash_catalogo
from motor_cartera.api import crear_app
from motor_cartera.config import Config
from motor_cartera.db.modelos import EstadoTrabajo, TrabajoOrquestacion
from motor_cartera.db.sesion import crear_motor, sesion
from motor_cartera.orquestacion.worker import identificador_worker, procesar_un_trabajo

NO_EXISTE = "00000000-0000-4000-8000-000000000000"
CLAVE = "clave-de-prueba"
LLAVES_PROHIBIDAS = (
    '"cuenta_id"',
    '"cuenta_canonica_id"',
    '"cliente_unico"',
    '"nombre_cte"',
    '"direccion"',
    '"telefono"',
    '"actor_ref"',
    '"latitud"',
    '"longitud"',
    '"lat"',
    '"lon"',
    '"tramo_id"',
    '"microtramo_id"',
    '"polilinea"',
    '"polyline"',
    '"geometry"',
)


@pytest.fixture(scope="module")
def cliente(bd_del_modulo):
    app = crear_app(Config(api_key=CLAVE, tolerancia_rechazo=0.05, tamano_maximo_mb=1))
    with TestClient(app, headers={"X-API-Key": CLAVE}) as c:
        yield c


@pytest.fixture(scope="module")
def g(bd_del_modulo, tmp_path_factory):
    from analytics_escenarios import golden

    return golden(tmp_path_factory.mktemp("api_analytics"))


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
    procesados = []
    worker_id = identificador_worker()
    while (p := procesar_un_trabajo(worker_id, Config())) is not None:
        procesados.append(p)
    return procesados


def _cuerpo(g, as_of: date, known_at, /, **cambios) -> dict:
    """El manifiesto del golden, como lo envia un cliente."""
    o = g.operacion
    evaluacion = {AS_OF_A: g.evaluacion_a, AS_OF_B: g.evaluacion_b}.get(as_of)
    cuerpo = {
        "corte_id": str(g.corte.corte_id),
        "as_of": as_of.isoformat(),
        "modo": "BACKTEST",
        "zona_horaria": ZONA,
        "granularidad": "DIA",
        "known_at": None if known_at is None else known_at.isoformat(),
        "motor_pagos_run_ids": [
            str(g.septiembre.motor_pagos_run_id),
            str(g.octubre.motor_pagos_run_id),
        ],
        "atribucion_run_ids": [
            str(g.atribucion_septiembre.atribucion_run_id),
            str(g.atribucion_octubre.atribucion_run_id),
        ],
        "evaluacion_run_id": None if evaluacion is None else str(evaluacion.evaluacion_run_id),
        "decision_run_id": str(o.decision.decision_run_id),
        "geografia_run_id": str(o.geografia.geografia_run_id),
        "territorial_run_id": str(o.territorial.territorial_run_id),
        "campo_run_id": str(o.plan.campo_run_id),
        "ruteo_run_id": str(o.ruteo.ruteo_run_id),
    }
    cuerpo.update(cambios)
    return cuerpo


@pytest.fixture(scope="module")
def publicada(g, cliente):
    """La ejecucion A del golden, pedida por la API y publicada por un worker."""
    _cola_vacia()
    respuesta = cliente.post("/analytics/ejecuciones", json=_cuerpo(g, AS_OF_A, g.k1))
    assert respuesta.status_code == 201, respuesta.text
    creada = respuesta.json()
    procesados = _trabajar()
    assert [(p.tipo, p.estado) for p in procesados] == [("ANALYTICS", "COMPLETADO")]
    return creada


def _sin_datos_de_una_cuenta(cuerpo) -> None:
    texto = json.dumps(cuerpo)
    for llave in LLAVES_PROHIBIDAS:
        assert llave not in texto, llave
    assert "CU0000" not in texto
    assert "AGT-" not in texto
    assert "-98." not in texto


# --- el catalogo ----------------------------------------------------------------------------------


def test_el_catalogo_publica_cada_metrica_con_su_definicion_formal(cliente):
    respuesta = cliente.get("/analytics/catalogo")

    assert respuesta.status_code == 200
    catalogo = respuesta.json()
    assert (catalogo["version"], catalogo["hash"]) == (
        "analytics-metrics/public-v1",
        hash_catalogo(),
    )
    assert [m["codigo"] for m in catalogo["metricas"]] == [m.codigo for m in METRICAS]
    for m in catalogo["metricas"]:
        for campo in (
            "codigo",
            "nombre",
            "descripcion",
            "familia",
            "unidad",
            "formula",
            "dimensiones_permitidas",
            "fuentes_requeridas",
            "version",
            "limitaciones",
        ):
            assert m[campo] not in (None, ""), (m["codigo"], campo)
        assert "numerador" in m and "denominador" in m
        assert (m["numerador"] is None) == (m["tipo"] == "VALOR")
        assert {"conjunto": "TOTAL", "llaves": []} in m["dimensiones_permitidas"]
        assert "CORTE" in m["fuentes_requeridas"]
    (politica,) = catalogo["politicas"]
    assert (politica["version"], politica["min_group_size"]) == ("analytics-public-v1", 10)
    assert "no prueba causalidad" in catalogo["aviso"]


def test_sin_api_key_no_se_lee_nada(cliente):
    respuesta = cliente.get("/analytics/catalogo", headers={"X-API-Key": ""})
    assert respuesta.status_code == 401


# --- pedir y leer una ejecucion -------------------------------------------------------------------


def test_el_post_deja_la_ejecucion_en_proceso_y_un_worker_la_publica(g, cliente, publicada):
    assert publicada["estado"] == "EN_PROCESO" and publicada["resultado"] is None
    assert publicada["corte_id"] == str(g.corte.corte_id)
    assert (publicada["as_of"], publicada["granularidad"], publicada["modo"]) == (
        "2026-10-04",
        "DIA",
        "BACKTEST",
    )
    assert len(publicada["entradas"]) == 11
    assert publicada["trabajo_id"] is not None

    respuesta = cliente.get(f"/analytics/ejecuciones/{publicada['analytics_run_id']}")

    assert respuesta.status_code == 200
    e = respuesta.json()
    assert (e["estado"], e["resultado"]) == ("EXITOSA", "ANALYTICS_PUBLICADA")
    assert e["firma_inputs"] == publicada["firma_inputs"]
    assert e["agregados_publicados"] > 0 and e["controles_fallidos"] == 0
    assert e["disponibilidad"]["cobertura_pagos"]["dias_con_cobertura"] == 5
    assert [x["tipo"] for x in e["entradas"]].count("MOTOR_PAGOS") == 2
    _sin_datos_de_una_cuenta(e)


def test_el_mismo_manifiesto_es_la_misma_ejecucion(g, cliente, publicada):
    respuesta = cliente.post("/analytics/ejecuciones", json=_cuerpo(g, AS_OF_A, g.k1))

    assert respuesta.status_code == 409
    assert respuesta.json()["codigo"] == "ANALYTICS_YA_GENERADA"


@pytest.mark.parametrize(
    ("cambios", "estado", "codigo"),
    [
        ({"known_at": None}, 422, "KNOWN_AT_REQUERIDO"),
        ({"corte_id": NO_EXISTE}, 404, "CORTE_NO_ENCONTRADO"),
        ({"ruteo_run_id": NO_EXISTE}, 404, "ENTRADA_NO_ENCONTRADA"),
        ({"zona_horaria": "Marte/Olympus"}, 422, "ZONA_HORARIA_INVALIDA"),
        ({"as_of": "2026-09-01"}, 422, "AS_OF_ANTERIOR_AL_CORTE"),
        ({"granularidad": "HORA"}, 422, "ENTRADA_INVALIDA"),
        ({"modo": "PREDICTIVO"}, 422, "ENTRADA_INVALIDA"),
        ({"ultimo": True}, 422, "ENTRADA_INVALIDA"),
        ({"version_catalogo": "analytics-metrics/public-v9"}, 422, "CATALOGO_NO_SOPORTADO"),
        ({"geografia_run_id": None}, 422, "ENCADENAMIENTO_INCORRECTO"),
    ],
)
def test_un_manifiesto_que_no_se_puede_analizar_no_registra_nada(
    g, cliente, cambios, estado, codigo
):
    with sesion() as s:
        antes = s.execute(text("SELECT count(*) FROM ejecucion_analytics")).scalar_one()

    respuesta = cliente.post("/analytics/ejecuciones", json=_cuerpo(g, AS_OF_B, g.k2, **cambios))

    assert respuesta.status_code == estado, respuesta.text
    assert respuesta.json()["codigo"] == codigo
    with sesion() as s:
        assert s.execute(text("SELECT count(*) FROM ejecucion_analytics")).scalar_one() == antes


def test_una_evaluacion_de_otro_as_of_no_se_declara(g, cliente):
    respuesta = cliente.post(
        "/analytics/ejecuciones",
        json=_cuerpo(g, AS_OF_A, g.k2, evaluacion_run_id=str(g.evaluacion_b.evaluacion_run_id)),
    )
    assert (respuesta.status_code, respuesta.json()["codigo"]) == (
        422,
        "EVALUACION_INCONSISTENTE",
    )


def test_la_lista_filtra_por_corte_estado_y_as_of(g, cliente, publicada):
    todas = cliente.get("/analytics/ejecuciones").json()
    assert todas["total"] >= 1
    por_corte = cliente.get(
        "/analytics/ejecuciones", params={"corte_id": str(g.corte.corte_id), "as_of": "2026-10-04"}
    ).json()
    assert publicada["analytics_run_id"] in [e["analytics_run_id"] for e in por_corte["elementos"]]
    assert all(e["as_of"] == "2026-10-04" for e in por_corte["elementos"])
    exitosas = cliente.get("/analytics/ejecuciones", params={"estado": "EXITOSA"}).json()
    assert all(e["estado"] == "EXITOSA" for e in exitosas["elementos"])
    assert cliente.get("/analytics/ejecuciones", params={"corte_id": NO_EXISTE}).json()[
        "codigo"
    ] == ("CORTE_NO_ENCONTRADO")
    assert cliente.get("/analytics/ejecuciones", params={"ultima": "true"}).status_code == 422


def test_una_ejecucion_que_no_existe(cliente):
    for ruta in ("", "/agregados", "/resumen", "/embudos", "/controles"):
        respuesta = cliente.get(f"/analytics/ejecuciones/{NO_EXISTE}{ruta}")
        assert (respuesta.status_code, respuesta.json()["codigo"]) == (
            404,
            "ANALYTICS_NO_ENCONTRADA",
        )


# --- los agregados --------------------------------------------------------------------------------


def _agregados(cliente, publicada, **params) -> dict:
    respuesta = cliente.get(
        f"/analytics/ejecuciones/{publicada['analytics_run_id']}/agregados", params=params
    )
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


def test_los_agregados_se_filtran_en_la_base_y_se_paginan(cliente, publicada):
    neta = _agregados(
        cliente,
        publicada,
        metrica="recuperacion.neta",
        vista="PERIODO",
        dimension_set="TOTAL",
        periodo_desde="2026-10-01",
        periodo_hasta="2026-10-03",
    )
    assert neta["total"] == 3
    assert [(a["periodo_desde"], a["valor"]) for a in neta["elementos"]] == [
        ("2026-10-01", "500.00"),
        ("2026-10-02", "1700.00"),
        ("2026-10-03", "400.00"),
    ]
    todos = _agregados(cliente, publicada, por_pagina=500)
    assert todos["total"] == publicada_total(cliente, publicada)
    pagina_2 = _agregados(cliente, publicada, pagina=2, por_pagina=7)
    assert len(pagina_2["elementos"]) == 7
    assert pagina_2["elementos"][0] == todos["elementos"][7]


def publicada_total(cliente, publicada) -> int:
    e = cliente.get(f"/analytics/ejecuciones/{publicada['analytics_run_id']}").json()
    return e["agregados_publicados"]


def test_un_filtro_por_dimension_respeta_la_supresion(cliente, publicada):
    consumo = _agregados(
        cliente, publicada, metrica="cartera.cuentas", dimension_set="PRODUCTO", producto="CONSUMO"
    )
    (fila,) = consumo["elementos"]
    assert (fila["estado_valor"], fila["valor"], fila["poblacion_cuentas"]) == (
        "DISPONIBLE",
        "10",
        10,
    )
    tarjeta = _agregados(
        cliente, publicada, metrica="cartera.cuentas", dimension_set="PRODUCTO", producto="TARJETA"
    )
    (fila,) = tarjeta["elementos"]
    assert fila["estado_valor"] == "SUPRIMIDO"
    assert (fila["valor"], fila["numerador"], fila["denominador"], fila["poblacion_cuentas"]) == (
        None,
        None,
        None,
        None,
    )
    suprimidas = _agregados(cliente, publicada, estado_valor="SUPRIMIDO", por_pagina=500)
    assert suprimidas["total"] > 0
    assert all(a["valor"] is None for a in suprimidas["elementos"])


@pytest.mark.parametrize(
    "params",
    [
        {"columna": "valor"},
        {"orden": "valor desc"},
        {"where": "1=1"},
        {"producto": "CONSUMO' OR '1'='1"},
        {"metrica": "recuperacion.neta; DROP TABLE agregado_analytics"},
        {"vista": "TODAS"},
        {"dimension_set": "CUENTA"},
        {"estado_valor": "ESTIMADO"},
        {"cuenta_id": "x"},
        {"microtramo": "x"},
    ],
)
def test_ningun_filtro_fuera_del_catalogo_se_acepta(cliente, publicada, params):
    respuesta = cliente.get(
        f"/analytics/ejecuciones/{publicada['analytics_run_id']}/agregados", params=params
    )
    assert (respuesta.status_code, respuesta.json()["codigo"]) == (422, "ENTRADA_INVALIDA")


def test_ningun_agregado_lleva_algo_de_una_cuenta(cliente, publicada):
    todos = _agregados(cliente, publicada, por_pagina=500)
    pagina = 1
    while True:
        _sin_datos_de_una_cuenta(todos)
        if pagina * 500 >= todos["total"]:
            break
        pagina += 1
        todos = _agregados(cliente, publicada, por_pagina=500, pagina=pagina)


# --- el resumen, los embudos y los controles ------------------------------------------------------


def test_el_resumen_trae_unos_pocos_indicadores_del_total(cliente, publicada):
    respuesta = cliente.get(f"/analytics/ejecuciones/{publicada['analytics_run_id']}/resumen")

    assert respuesta.status_code == 200
    resumen = respuesta.json()
    indicadores = {i["nombre"]: i for i in resumen["indicadores"]}
    assert {n: i["valor"] for n, i in indicadores.items()} == {
        "cuentas": "20",
        "saldo_total": "200000.00",
        "recuperacion_neta": "2700.00",
        "recuperacion_sobre_saldo_inicial": "0.0135000000",
        "cuentas_gestionadas": "12",
        "contactabilidad": "0.8333333333",
        "promesas_creadas": "8",
        "tasa_cumplimiento_promesas": "0.6000000000",
        "asociacion_unica": "3",
        "campo_candidatas": "6",
        "campo_asignadas": "4",
        "ruteo_paradas_objetivo": "4",
        "ruteo_programadas": "2",
    }
    assert (
        indicadores["contactabilidad"]["numerador"],
        indicadores["contactabilidad"]["denominador"],
    ) == ("10", "12")
    assert resumen["cuentas_cohorte"] == 20
    _sin_datos_de_una_cuenta(resumen)


def test_un_indicador_sin_su_entrada_no_tiene_valor(g, cliente):
    _cola_vacia()
    cuerpo = _cuerpo(
        g,
        date(2026, 10, 10),
        g.k2,
        granularidad="TOTAL",
        campo_run_id=None,
        ruteo_run_id=None,
    )
    creada = cliente.post("/analytics/ejecuciones", json=cuerpo).json()
    _trabajar()

    resumen = cliente.get(f"/analytics/ejecuciones/{creada['analytics_run_id']}/resumen").json()

    indicadores = {i["nombre"]: i for i in resumen["indicadores"]}
    neta = indicadores["recuperacion_neta"]
    assert (neta["estado_valor"], neta["valor"]) == ("NO_DISPONIBLE", None)
    assert neta["motivos"][0]["codigo"] == "PAGOS_SIN_COBERTURA"
    campo = indicadores["campo_candidatas"]
    assert (campo["estado_valor"], campo["valor"]) == ("NO_DISPONIBLE", None)
    assert campo["motivos"] == [{"codigo": "ENTRADA_NO_DECLARADA", "entradas": ["CAMPO_V2"]}]
    assert indicadores["cuentas"]["valor"] == "20"
    embudos = cliente.get(f"/analytics/ejecuciones/{creada['analytics_run_id']}/embudos").json()
    (campo_,) = [e for e in embudos["embudos"] if e["embudo"] == "CAMPO"]
    assert [x["cuentas"] for x in campo_["etapas"]] == [None, None, None]
    assert all(x["pct_cohorte"] is None for x in campo_["etapas"])


def test_los_embudos_son_conjuntos_anidados_sin_pagos(cliente, publicada):
    respuesta = cliente.get(f"/analytics/ejecuciones/{publicada['analytics_run_id']}/embudos")

    assert respuesta.status_code == 200
    embudos = {e["embudo"]: e["etapas"] for e in respuesta.json()["embudos"]}
    contacto = embudos["CONTACTO"]
    assert [(x["etapa"], x["cuentas"]) for x in contacto] == [
        ("COHORTE", 20),
        ("GESTIONADAS", 12),
        ("CONTACTADAS", 10),
        ("CON_PROMESA", 7),
    ]
    assert [x["pct_etapa_anterior"] for x in contacto] == [
        None,
        "0.6000000000",
        "0.8333333333",
        "0.7000000000",
    ]
    assert [x["pct_cohorte"] for x in contacto] == [
        "1.0000000000",
        "0.6000000000",
        "0.5000000000",
        "0.3500000000",
    ]
    campo = embudos["CAMPO"]
    assert [(x["etapa"], x["cuentas"]) for x in campo] == [
        ("CANDIDATAS", 6),
        ("ASIGNADAS", 4),
        ("PARADAS_PROGRAMADAS", 2),
    ]
    assert [x["pct_etapa_anterior"] for x in campo] == [None, "0.6666666667", "0.5000000000"]
    etapas = [x["etapa"] for e in embudos.values() for x in e]
    assert not [x for x in etapas if "PAGO" in x or "RECUPER" in x]


def test_los_controles_de_una_publicada_cuadran_todos(cliente, publicada):
    respuesta = cliente.get(f"/analytics/ejecuciones/{publicada['analytics_run_id']}/controles")

    assert respuesta.status_code == 200
    controles = respuesta.json()
    assert controles["controles_fallidos"] == 0
    assert len(controles["controles"]) == controles["controles_totales"] > 100
    assert {c["estado"] for c in controles["controles"]} == {"OK"}
    assert all(Decimal(c["diferencia"]) == 0 for c in controles["controles"])
    _sin_datos_de_una_cuenta(controles)


def test_una_fallida_por_un_control_lo_dice_y_no_tiene_agregados(g, cliente):
    _cola_vacia()
    cuerpo = _cuerpo(
        g,
        AS_OF_B,
        g.k2,
        motor_pagos_run_ids=[
            str(g.septiembre_previa.motor_pagos_run_id),
            str(g.octubre.motor_pagos_run_id),
        ],
        atribucion_run_ids=[str(g.atribucion_octubre.atribucion_run_id)],
    )
    creada = cliente.post("/analytics/ejecuciones", json=cuerpo).json()
    _trabajar()
    ruta = f"/analytics/ejecuciones/{creada['analytics_run_id']}"

    e = cliente.get(ruta).json()
    assert (e["estado"], e["resultado"], e["agregados_publicados"]) == (
        "FALLIDA",
        "CONTROL_FALLIDO",
        0,
    )
    controles = cliente.get(f"{ruta}/controles").json()
    fallidos = [c for c in controles["controles"] if c["estado"] == "FALLO"]
    assert [(c["codigo"], c["esperado"], c["observado"]) for c in fallidos] == [
        ("pagos.reversos_coherentes", "0", "1")
    ]
    for sub in ("/agregados", "/resumen", "/embudos"):
        respuesta = cliente.get(f"{ruta}{sub}")
        assert (respuesta.status_code, respuesta.json()["codigo"]) == (
            409,
            "ANALYTICS_NO_PUBLICADA",
        )


def test_la_lista_lee_sus_ejecuciones_sin_una_consulta_por_cada_una(cliente):
    """Una pagina de ejecuciones con su corte, su trabajo y sus entradas cuesta lo mismo con una
    que con todas: sin N+1."""
    sentencias: list[str] = []

    def anotar(conexion, cursor, sentencia, parametros, contexto, varias):
        sentencias.append(sentencia)

    def leer(por_pagina: int) -> tuple[int, int]:
        sentencias.clear()
        event.listen(crear_motor(), "before_cursor_execute", anotar)
        try:
            cuerpo = cliente.get("/analytics/ejecuciones", params={"por_pagina": por_pagina})
        finally:
            event.remove(crear_motor(), "before_cursor_execute", anotar)
        return len(cuerpo.json()["elementos"]), len(sentencias)

    una, con_una = leer(1)
    todas, con_todas = leer(500)

    assert (una, todas >= 2) == (1, True)
    assert con_todas == con_una


# --- sin analytics por cuenta ---------------------------------------------------------------------


def test_no_hay_analytics_por_cuenta_y_la_cuenta_360_no_cambia(cliente, publicada):
    from analytics_escenarios import cuenta

    cuenta_id = cuenta(1).cuenta_id
    assert cliente.get(f"/cuentas/{cuenta_id}/analytics").status_code == 404
    respuesta = cliente.get(f"/cuentas/{cuenta_id}")
    assert respuesta.status_code == 200
    assert "analytics" not in respuesta.text.lower()


def test_openapi_documenta_las_operaciones_de_analytics(cliente):
    api = cliente.app.openapi()
    rutas = {r: set(m) for r, m in api["paths"].items() if r.startswith("/analytics")}
    assert rutas == {
        "/analytics/catalogo": {"get"},
        "/analytics/ejecuciones": {"get", "post"},
        "/analytics/ejecuciones/{analytics_run_id}": {"get"},
        "/analytics/ejecuciones/{analytics_run_id}/agregados": {"get"},
        "/analytics/ejecuciones/{analytics_run_id}/resumen": {"get"},
        "/analytics/ejecuciones/{analytics_run_id}/embudos": {"get"},
        "/analytics/ejecuciones/{analytics_run_id}/controles": {"get"},
    }
    post = api["paths"]["/analytics/ejecuciones"]["post"]
    for codigo in ("ANALYTICS_YA_GENERADA", "KNOWN_AT_REQUERIDO", "ENCADENAMIENTO_INCORRECTO"):
        assert codigo in json.dumps(post["responses"])
    assert "analytics" in {t["name"] for t in api["tags"]}
