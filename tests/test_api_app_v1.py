"""app/v1 contra PostgreSQL: el contexto operativo, el resumen, el explorador de cuentas, la
Cuenta 360 de la app, el linaje y la distribucion de un scoring.

El escenario es el golden de analytics/v1 (analytics_escenarios.golden: 20 cuentas con su
decision/v2, su geografia, su territorial/v2, su plan de campo/v2, su ruteo/v2, sus pagos, su
lifecycle y sus atribuciones), analizado a AS_OF_A, con un modelo y tres scorings sinteticos del
corte (el del contexto, otro y uno EN_PROCESO) y una optimizacion con CAMPO sobre ese ruteo. Se
construye una vez por modulo; ninguna prueba escribe.

Lo que se prueba: que nada se elige como "lo ultimo" (lo que no se declara es NO_DECLARADA, nunca
cero), que un contexto que no encadena no se combina, que todo se pagina en la base con un numero
de consultas que no crece con la pagina, que ninguna respuesta lleva un dato personal y que solo
hay GET.
"""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from analytics_escenarios import AS_OF_A, CORTE, CORTE_ANTERIOR, CUENTAS, analizar, decidir, golden
from historia_escenarios import cliente, cuenta
from optimizacion_escenarios import (
    JUEVES,
    CuentaOpt,
    admisible,
    modelo_publicado,
    optimizar,
    pool,
    registrar,
    scoring_sintetico,
)
from sqlalchemy import event

from motor_cartera.db.sesion import crear_motor

NO_EXISTE = "00000000-0000-4000-8000-000000000000"
PERSONALES = re.compile(
    r'"(cliente_unico|actor_ref|idempotency_key|latitud|longitud|lat|lon|lng|geometria|geometry|'
    r'polilinea|polyline|telefono|correo|curp|rfc|domicilio|direccion)"'
)


@pytest.fixture(scope="module")
def e(bd_del_modulo, tmp_path_factory):
    g = golden(tmp_path_factory.mktemp("app_v1"))
    analisis = analizar(g.peticion(AS_OF_A, g.k1))
    assert analisis.estado == "EXITOSA", analisis.detalle
    o = g.operacion
    cuentas = tuple(
        CuentaOpt(
            c.numero, valor=f"{100 + c.numero}.00", contacto="0.50000000", imputada=c.numero == 9
        )
        for c in CUENTAS
    )
    modelo = modelo_publicado("app")
    scoring = scoring_sintetico(modelo, cuentas, CORTE, etiqueta="app")
    otro_scoring = scoring_sintetico(modelo, cuentas, CORTE, etiqueta="otro")
    en_proceso = scoring_sintetico(modelo, cuentas, CORTE, etiqueta="en proceso", cerrar=False)
    configuracion = registrar(
        {
            "nombre": "Plan de la app",
            "permitir_predicciones_con_imputacion": False,
            "admisibilidad": [
                admisible("DIGITAL", "DIGITAL"),
                admisible("TELEFONICA", "TELEFONICA"),
                admisible("CAMPO", "CAMPO"),
            ],
            "pools": [
                pool("DIGITAL_GLOBAL", "DIGITAL", 3, 1),
                pool("TELEFONICA_GLOBAL", "TELEFONICA", 2, 4),
                pool("CAMPO_A", "CAMPO", None, 10, alcance="GESTOR_CAMPO", referencia="GESTOR_A"),
                pool("CAMPO_B", "CAMPO", None, 10, alcance="GESTOR_CAMPO", referencia="GESTOR_B"),
            ],
            "supuestos": "SUPUESTOS SINTETICOS de las pruebas de la app.",
        }
    )
    plan = optimizar(scoring, o.decision, configuracion, fecha=JUEVES, ruteo=o.ruteo)
    assert plan.estado == "EXITOSA", plan.detalle
    contexto = {
        "corte_id": str(g.corte.corte_id),
        "decision_run_id": str(o.decision.decision_run_id),
        "analytics_run_id": str(analisis.analytics_run_id),
        "scoring_run_id": str(scoring.scoring_run_id),
        "optimizacion_run_id": str(plan.optimizacion_run_id),
        "campo_run_id": str(o.plan.campo_run_id),
        "ruteo_run_id": str(o.ruteo.ruteo_run_id),
    }
    return SimpleNamespace(
        g=g,
        o=o,
        analisis=analisis,
        modelo=modelo,
        scoring=scoring,
        otro_scoring=otro_scoring,
        en_proceso=en_proceso,
        optimizacion=plan,
        contexto=contexto,
        decision_anterior=decidir(CORTE_ANTERIOR, backtest=True),
    )


def _cuenta_id(numero: int) -> str:
    return str(cuenta(numero).cuenta_id)


def _sin_personales(respuesta) -> None:
    """Ninguna llave personal en ningun nivel, y ningun CLIENTE_UNICO como valor."""
    texto = respuesta.text
    assert not PERSONALES.search(texto), PERSONALES.search(texto).group(0)
    assert not re.search(r"CU\d{10}", texto), re.search(r"CU\d{10}", texto).group(0)


@contextmanager
def consultas():
    """Cuantas sentencias manda la API a la base mientras dura el bloque."""
    contadas: list[str] = []

    def contar(_conexion, _cursor, sentencia, *_):
        contadas.append(sentencia)

    motor = crear_motor()
    event.listen(motor, "before_cursor_execute", contar)
    try:
        yield contadas
    finally:
        event.remove(motor, "before_cursor_execute", contar)


# --- el contexto ----------------------------------------------------------------------------------


def test_un_contexto_completo_es_compatible_y_dice_lo_que_cada_ejecucion_leyo(cliente_app, e):
    r = cliente_app.get("/app/v1/contextos/validar", params=e.contexto)

    assert r.status_code == 200
    v = r.json()
    assert v["compatible"] is True and v["problemas"] == []
    assert v["version"] == "app/v1"
    assert v["corte"]["corte_id"] == e.contexto["corte_id"]
    assert v["corte"]["fecha_corte"] == CORTE.isoformat() and v["corte"]["cuentas"] == 20
    assert v["declaradas"] == {
        "DECISION_V2": e.contexto["decision_run_id"],
        "ANALYTICS": e.contexto["analytics_run_id"],
        "SCORING": e.contexto["scoring_run_id"],
        "OPTIMIZACION": e.contexto["optimizacion_run_id"],
        "CAMPO_V2": e.contexto["campo_run_id"],
        "RUTEO_V2": e.contexto["ruteo_run_id"],
    }
    assert [x["tipo"] for x in v["ejecuciones"]] == [
        "DECISION_V2",
        "ANALYTICS",
        "SCORING",
        "OPTIMIZACION",
        "CAMPO_V2",
        "RUTEO_V2",
    ]
    optimizacion = next(x for x in v["ejecuciones"] if x["tipo"] == "OPTIMIZACION")
    assert optimizacion["lee"]["SCORING"] == e.contexto["scoring_run_id"]
    assert optimizacion["lee"]["RUTEO_V2"] == e.contexto["ruteo_run_id"]
    assert optimizacion["fecha_operacion"] == JUEVES.isoformat()
    analytics = next(x for x in v["ejecuciones"] if x["tipo"] == "ANALYTICS")
    assert analytics["as_of"] == AS_OF_A.isoformat() and analytics["known_at"] is not None
    assert set(v["derivadas"]) == {
        "CONFIGURACION_CAMPO",
        "CONFIGURACION_OPTIMIZACION",
        "EVALUACION_PROMESAS",
        "GEOGRAFIA",
        "MODELO",
        "RED_VIAL",
        "TERRITORIAL_V2",
    }
    assert v["derivadas"]["TERRITORIAL_V2"] == str(e.o.territorial.territorial_run_id)
    assert v["derivadas"]["MODELO"] == str(e.modelo.modelo_version_id)
    _sin_personales(r)


def test_solo_el_corte_es_un_contexto_valido_y_no_declara_nada_mas(cliente_app, e):
    v = cliente_app.get("/app/v1/contextos/validar", params={"corte_id": e.contexto["corte_id"]})

    assert v.status_code == 200
    assert v.json()["compatible"] is True
    assert v.json()["declaradas"] == {} and v.json()["ejecuciones"] == []


@pytest.mark.parametrize(
    "cambio, codigo, tipos",
    [
        ({"scoring_run_id": NO_EXISTE}, "EJECUCION_NO_ENCONTRADA", ["SCORING"]),
        ({"optimizacion_run_id": NO_EXISTE}, "EJECUCION_NO_ENCONTRADA", ["OPTIMIZACION"]),
    ],
)
def test_una_ejecucion_que_no_existe_hace_incompatible_el_contexto(
    cliente_app, e, cambio, codigo, tipos
):
    v = cliente_app.get("/app/v1/contextos/validar", params={**e.contexto, **cambio}).json()

    assert v["compatible"] is False
    assert {"codigo": codigo, "ejecuciones": tipos} in [
        {"codigo": p["codigo"], "ejecuciones": p["ejecuciones"]} for p in v["problemas"]
    ]


def test_una_decision_de_otro_corte_no_se_combina(cliente_app, e):
    contexto = {**e.contexto, "decision_run_id": str(e.decision_anterior.decision_run_id)}
    v = cliente_app.get("/app/v1/contextos/validar", params=contexto).json()

    codigos = [p["codigo"] for p in v["problemas"]]
    assert v["compatible"] is False
    assert "CORTE_DISTINTO" in codigos
    # Y la optimizacion, el plan y analytics leyeron otra decision: tres encadenamientos distintos.
    distintos = [p for p in v["problemas"] if p["codigo"] == "ENCADENAMIENTO_DISTINTO"]
    assert {p["ejecuciones"][0] for p in distintos} == {"ANALYTICS", "CAMPO_V2", "OPTIMIZACION"}
    assert all("DECISION_V2" in p["ejecuciones"] for p in distintos)


def test_una_optimizacion_con_otro_scoring_no_se_combina(cliente_app, e):
    contexto = {**e.contexto, "scoring_run_id": str(e.otro_scoring.scoring_run_id)}
    v = cliente_app.get("/app/v1/contextos/validar", params=contexto).json()

    assert v["compatible"] is False
    assert [(p["codigo"], p["ejecuciones"]) for p in v["problemas"]] == [
        ("ENCADENAMIENTO_DISTINTO", ["OPTIMIZACION", "SCORING"])
    ]
    assert str(e.scoring.scoring_run_id) in v["problemas"][0]["mensaje"]


def test_sin_declarar_el_plan_lo_que_leyeron_las_demas_sigue_encadenado(cliente_app, e):
    """La optimizacion, el ruteo y analytics leyeron el mismo plan: sin declararlo, el contexto
    sigue siendo compatible, y el plan no se busca."""
    sin_plan = {k: v for k, v in e.contexto.items() if k != "campo_run_id"}
    assert cliente_app.get("/app/v1/contextos/validar", params=sin_plan).json()["compatible"]


def test_un_corte_que_no_existe_es_un_404(cliente_app):
    r = cliente_app.get("/app/v1/contextos/validar", params={"corte_id": NO_EXISTE})

    assert (r.status_code, r.json()["codigo"]) == (404, "CORTE_NO_ENCONTRADO")


def test_el_contexto_no_acepta_otros_parametros(cliente_app, e):
    r = cliente_app.get("/app/v1/contextos/validar", params={**e.contexto, "ultimo": "true"})

    assert (r.status_code, r.json()["codigo"]) == (422, "ENTRADA_INVALIDA")


def test_validar_un_contexto_cuesta_lo_mismo_con_una_o_seis_ejecuciones(cliente_app, e):
    with consultas() as solo_corte:
        cliente_app.get("/app/v1/contextos/validar", params={"corte_id": e.contexto["corte_id"]})
    with consultas() as completo:
        cliente_app.get("/app/v1/contextos/validar", params=e.contexto)

    # El corte, y una consulta por ejecucion declarada (mas las entradas de analytics).
    assert len(solo_corte) <= 2
    assert len(completo) <= len(solo_corte) + 7


# --- las opciones ---------------------------------------------------------------------------------


def test_las_opciones_listan_carteras_y_cortes_sin_abrir_ninguno(cliente_app, e):
    r = cliente_app.get("/app/v1/contextos/opciones")

    assert r.status_code == 200
    o = r.json()
    assert o["cartera_id"] == "CARTERA_PRINCIPAL"
    assert o["carteras"] == [
        {"cartera_id": "CARTERA_PRINCIPAL", "cortes": 2, "ultimo_corte": CORTE.isoformat()}
    ]
    assert o["total"] == 2
    assert [c["fecha_corte"] for c in o["cortes"]] == [
        CORTE.isoformat(),
        CORTE_ANTERIOR.isoformat(),
    ]
    assert o["corte"] is None and o["ejecuciones"] == [] and o["sugerencias"] == []


def test_las_opciones_de_un_corte_proponen_el_contexto_completo(cliente_app, e):
    o = cliente_app.get(
        "/app/v1/contextos/opciones", params={"corte_id": e.contexto["corte_id"]}
    ).json()

    totales = {x["tipo"]: x["total"] for x in o["ejecuciones"]}
    assert totales == {
        "DECISION_V2": 1,
        "ANALYTICS": 1,
        "SCORING": 3,
        "OPTIMIZACION": 1,
        "CAMPO_V2": 1,
        "RUTEO_V2": 1,
    }
    sugeridos = [{k: v for k, v in s["contexto"].items()} for s in o["sugerencias"]]
    assert e.contexto in sugeridos
    assert {s["codigo"] for s in o["sugerencias"]} <= {"DESDE_OPTIMIZACION", "DESDE_ANALYTICS"}
    assert "DESDE_OPTIMIZACION" in {s["codigo"] for s in o["sugerencias"]}
    for sugerido in sugeridos:
        valido = cliente_app.get(
            "/app/v1/contextos/validar", params={k: v for k, v in sugerido.items() if v}
        ).json()
        assert valido["compatible"], valido["problemas"]


def test_las_opciones_de_un_corte_que_no_existe_son_un_404(cliente_app):
    r = cliente_app.get("/app/v1/contextos/opciones", params={"corte_id": NO_EXISTE})

    assert (r.status_code, r.json()["codigo"]) == (404, "CORTE_NO_ENCONTRADO")


# --- el resumen -----------------------------------------------------------------------------------


def test_el_resumen_de_un_contexto_completo_trae_cada_bloque_de_su_ejecucion(cliente_app, e):
    r = cliente_app.get("/app/v1/resumen", params=e.contexto)

    assert r.status_code == 200
    s = r.json()
    for bloque in ("analytics", "decision", "predictivo", "optimizacion", "campo", "ruteo"):
        assert s[bloque]["estado_fuente"] == "DISPONIBLE", bloque
    assert s["analytics"]["run_id"] == e.contexto["analytics_run_id"]
    indicadores = {i["nombre"]: i for i in s["analytics"]["indicadores"]}
    assert indicadores["cuentas"]["valor"] == "20"
    # Lo que analytics/v1 publico NO_DISPONIBLE no tiene valor, y no se vuelve cero.
    for indicador in indicadores.values():
        if indicador["estado_valor"] != "DISPONIBLE":
            assert indicador["valor"] is None, indicador
    assert {x["embudo"] for x in s["analytics"]["embudos"]} == {"CONTACTO", "CAMPO"}
    assert s["decision"]["cuentas_decididas"] == 20
    assert s["decision"]["distribucion"] is not None
    distribucion = s["predictivo"]["distribucion"]
    assert distribucion["publicadas"] == 20 and distribucion["con_imputacion"] == 1
    assert distribucion["medias"]["p_recuperacion_positiva_30d"] == "0.50000000"
    assert distribucion["suma_recuperacion_positiva_esperada_30d"] == "2210.00"
    assert {h["salida"]: sum(h["cuentas"]) for h in distribucion["histogramas"]} == {
        "p_recuperacion_positiva_30d": 20,
        "p_contacto_14d": 20,
        "p_promesa_30d": 20,
    }
    assert s["predictivo"]["modelo_version_id"] == str(e.modelo.modelo_version_id)
    assert s["optimizacion"]["solver_status"] == "OPTIMAL"
    assert s["optimizacion"]["cuentas_asignadas"] == e.optimizacion.cuentas_asignadas
    assert s["campo"]["conteos"]["candidatas_campo"] == e.o.plan.candidatas_campo
    assert s["ruteo"]["conteos"]["programadas"] == e.o.ruteo.programadas
    assert any("no efecto causal" in a for a in s["avisos"])
    assert any("no estrategia causalmente optima" in a for a in s["avisos"])
    _sin_personales(r)


def test_un_bloque_sin_ejecucion_declarada_es_no_declarada_y_sin_ceros(cliente_app, e):
    s = cliente_app.get("/app/v1/resumen", params={"corte_id": e.contexto["corte_id"]}).json()

    assert s["corte"]["cuentas"] == 20
    for bloque in ("analytics", "decision", "predictivo", "optimizacion", "campo", "ruteo"):
        assert s[bloque]["estado_fuente"] == "NO_DECLARADA", bloque
        assert s[bloque]["run_id"] is None
    assert s["analytics"]["indicadores"] == [] and s["predictivo"]["distribucion"] is None
    assert s["optimizacion"]["cuentas_asignadas"] is None
    assert s["campo"]["conteos"] is None and s["ruteo"]["totales"] is None


def test_el_resumen_de_un_contexto_incompatible_es_un_409_con_cada_problema(cliente_app, e):
    contexto = {**e.contexto, "scoring_run_id": str(e.otro_scoring.scoring_run_id)}
    r = cliente_app.get("/app/v1/resumen", params=contexto)

    assert (r.status_code, r.json()["codigo"]) == (409, "CONTEXTO_INCOMPATIBLE")
    assert [d["campo"] for d in r.json()["detalles"]] == ["OPTIMIZACION,SCORING"]


def test_la_distribucion_de_un_scoring_se_cuenta_en_la_base(cliente_app, e):
    r = cliente_app.get(f"/app/v1/predictivo/scorings/{e.contexto['scoring_run_id']}/distribucion")

    assert r.status_code == 200
    d = r.json()
    assert d["bins"] == 10 and d["publicadas"] == 20
    recuperacion = next(h for h in d["histogramas"] if h["salida"] == "p_recuperacion_positiva_30d")
    # Todas valen 0.5: el sexto decimo, [0.5, 0.6).
    assert recuperacion["cuentas"] == [0, 0, 0, 0, 0, 20, 0, 0, 0, 0]
    assert d["aviso"] == "Prediccion, no efecto causal ni recomendacion de accion."


def test_la_distribucion_de_un_scoring_que_no_publico_no_se_inventa(cliente_app, e):
    r = cliente_app.get(f"/app/v1/predictivo/scorings/{e.en_proceso.scoring_run_id}/distribucion")
    assert (r.status_code, r.json()["codigo"]) == (409, "SCORING_NO_PUBLICADO")
    r = cliente_app.get(f"/app/v1/predictivo/scorings/{NO_EXISTE}/distribucion")
    assert (r.status_code, r.json()["codigo"]) == (404, "SCORING_NO_ENCONTRADO")


# --- el explorador de cuentas ---------------------------------------------------------------------


def test_las_cuentas_del_corte_se_paginan_en_la_base(cliente_app, e):
    primera = cliente_app.get("/app/v1/cuentas", params={**e.contexto, "por_pagina": 7}).json()
    segunda = cliente_app.get(
        "/app/v1/cuentas", params={**e.contexto, "por_pagina": 7, "pagina": 2}
    ).json()
    ultima = cliente_app.get(
        "/app/v1/cuentas", params={**e.contexto, "por_pagina": 7, "pagina": 3}
    ).json()

    assert primera["total"] == segunda["total"] == 20
    assert len(primera["elementos"]) == len(segunda["elementos"]) == 7
    assert len(ultima["elementos"]) == 6
    vistas = [c["cuenta_id"] for p in (primera, segunda, ultima) for c in p["elementos"]]
    assert len(set(vistas)) == 20
    una = primera["elementos"][0]
    assert set(una) == {
        "cuenta_id",
        "producto",
        "saldo_total",
        "dias_atraso",
        "atraso_bucket",
        "prioridad",
        "canal_recomendado",
        "p_recuperacion_positiva_30d",
        "recuperacion_positiva_esperada_30d",
        "estado_prediccion",
        "estado_optimizacion",
        "canal_asignado",
        "valor_oportunidad_centavos",
    }
    assert una["prioridad"] is not None and una["estado_optimizacion"] is not None


def test_sin_ejecuciones_declaradas_sus_columnas_son_null_no_cero(cliente_app, e):
    p = cliente_app.get("/app/v1/cuentas", params={"corte_id": e.contexto["corte_id"]}).json()

    for c in p["elementos"]:
        assert c["prioridad"] is None and c["p_recuperacion_positiva_30d"] is None
        assert c["valor_oportunidad_centavos"] is None and c["estado_optimizacion"] is None


def test_un_filtro_de_una_ejecucion_no_declarada_es_un_422(cliente_app, e):
    r = cliente_app.get(
        "/app/v1/cuentas", params={"corte_id": e.contexto["corte_id"], "prioridad": "ALTA"}
    )
    assert (r.status_code, r.json()["codigo"]) == (422, "FILTRO_SIN_EJECUCION")
    r = cliente_app.get(
        "/app/v1/cuentas",
        params={"corte_id": e.contexto["corte_id"], "orden": "P_RECUPERACION_POSITIVA_30D"},
    )
    assert (r.status_code, r.json()["codigo"]) == (422, "FILTRO_SIN_EJECUCION")


def test_los_filtros_y_el_total_salen_de_la_base(cliente_app, e):
    def total(**filtros) -> int:
        return cliente_app.get("/app/v1/cuentas", params={**e.contexto, **filtros}).json()["total"]

    assert total(producto="CONSUMO") == 10
    assert total(producto="TARJETA") == 9
    assert total(atraso_bucket="0") == 2
    assert total(atraso_bucket="31-60") == 2
    assert total(atraso_bucket="91+") == 6
    assert total(estado_prediccion="CON_IMPUTACION") == 1
    asignadas = total(estado_optimizacion="ASIGNADA")
    assert asignadas == e.optimizacion.cuentas_asignadas
    assert total(producto="CONSUMO", atraso_bucket="0") == 2
    con_canal = sum(total(canal=c) for c in ("DIGITAL", "TELEFONICA", "CAMPO"))
    assert con_canal == 20
    pagina = cliente_app.get(
        "/app/v1/cuentas", params={**e.contexto, "producto": "TARJETA", "por_pagina": 100}
    ).json()
    assert {c["producto"] for c in pagina["elementos"]} == {"TARJETA"}


def test_buscar_por_el_principio_del_cuenta_id(cliente_app, e):
    buscada = _cuenta_id(3)
    for prefijo in (buscada[:8], buscada[:13], buscada.replace("-", "")[:12], buscada):
        p = cliente_app.get("/app/v1/cuentas", params={**e.contexto, "cuenta": prefijo}).json()
        assert buscada in [c["cuenta_id"] for c in p["elementos"]], prefijo
        assert all(c["cuenta_id"].startswith(buscada[:8]) for c in p["elementos"])
    r = cliente_app.get("/app/v1/cuentas", params={**e.contexto, "cuenta": "zz"})
    assert (r.status_code, r.json()["codigo"]) == (422, "ENTRADA_INVALIDA")


@pytest.mark.parametrize(
    "orden, llave",
    [
        ("SALDO_TOTAL", "saldo_total"),
        ("DIAS_ATRASO", "dias_atraso"),
        ("P_RECUPERACION_POSITIVA_30D", "p_recuperacion_positiva_30d"),
        ("RECUPERACION_POSITIVA_ESPERADA_30D", "recuperacion_positiva_esperada_30d"),
    ],
)
def test_ordenar_en_la_base(cliente_app, e, orden, llave):
    for sentido, reves in (("ASC", False), ("DESC", True)):
        p = cliente_app.get(
            "/app/v1/cuentas",
            params={**e.contexto, "orden": orden, "sentido": sentido, "por_pagina": 100},
        ).json()
        valores = [float(c[llave]) for c in p["elementos"]]
        assert valores == sorted(valores, reverse=reves), (orden, sentido)
        assert len(valores) == 20


def test_la_pagina_no_pasa_de_100(cliente_app, e):
    r = cliente_app.get("/app/v1/cuentas", params={**e.contexto, "por_pagina": 101})

    assert (r.status_code, r.json()["codigo"]) == (422, "ENTRADA_INVALIDA")


def test_las_consultas_del_explorador_no_crecen_con_la_pagina(cliente_app, e):
    with consultas() as cinco:
        cliente_app.get("/app/v1/cuentas", params={**e.contexto, "por_pagina": 5})
    with consultas() as veinte:
        cliente_app.get("/app/v1/cuentas", params={**e.contexto, "por_pagina": 20})
    with consultas() as filtrada:
        cliente_app.get("/app/v1/cuentas", params={**e.contexto, "producto": "CONSUMO"})

    assert len(cinco) == len(veinte)
    # El contexto (corte + seis ejecuciones + entradas de analytics), las llaves de la pagina, su
    # detalle y, filtrada, el conteo.
    assert len(veinte) <= 10
    assert len(filtrada) == len(veinte) + 1


ORDENES_DEL_EXPLORADOR = {
    "CUENTA": None,
    "SALDO_TOTAL": "saldo_total",
    "DIAS_ATRASO": "dias_atraso",
    "P_RECUPERACION_POSITIVA_30D": "p_recuperacion_positiva_30d",
    "RECUPERACION_POSITIVA_ESPERADA_30D": "recuperacion_positiva_esperada_30d",
}


def _todas_las_cuentas(cliente_app, contexto) -> list[dict]:
    """Las cuentas del corte en el orden de su llave: la referencia contra la que se compara."""
    cuentas, pagina = [], 1
    while True:
        r = cliente_app.get(
            "/app/v1/cuentas", params={**contexto, "por_pagina": 100, "pagina": pagina}
        ).json()
        cuentas += r["elementos"]
        if len(cuentas) >= r["total"]:
            return cuentas
        pagina += 1


def _esperadas(cuentas: list[dict], filtros: dict, orden: str, descendente: bool) -> list[str]:
    """Filtrar y ordenar la lista entera, como lo dice el contrato del explorador."""
    columnas = {"prioridad": "prioridad", "canal": "canal_recomendado"}

    def pasa(c: dict) -> bool:
        if "cuenta" in filtros and not c["cuenta_id"].startswith(filtros["cuenta"]):
            return False
        return all(
            c[columnas.get(llave, llave)] == valor
            for llave, valor in filtros.items()
            if llave != "cuenta"
        )

    campo = ORDENES_DEL_EXPLORADOR[orden]

    def llave(par):
        i, c = par
        if campo is None:
            return (i,)
        # Sin prediccion, al final en los dos sentidos (NULLS LAST), por su llave.
        if c[campo] is None:
            return (0 if descendente else 1, 0.0, i)
        return (1 if descendente else 0, float(c[campo]), i)

    filas = sorted(
        ((i, c) for i, c in enumerate(cuentas) if pasa(c)), key=llave, reverse=descendente
    )
    return [c["cuenta_id"] for _, c in filas]


@pytest.mark.parametrize("cubre", [True, False], ids=["por-el-scoring", "por-el-corte"])
def test_el_explorador_pagina_igual_que_una_referencia_en_cada_orden_y_filtro(
    cliente_app, e, monkeypatch, cubre
):
    """Las llaves de la pagina salen de la tabla mas chica que basta: el resultado tiene que ser el
    mismo que filtrar y ordenar la lista entera, en cada orden, sentido y filtro, y lo mismo si el
    scoring no cubre el corte (las cuentas sin prediccion van al final)."""
    from motor_cartera.app_v1 import cuentas as explorador

    if not cubre:
        monkeypatch.setattr(explorador, "_cubre_el_corte", lambda *_: False)
    todas = _todas_las_cuentas(cliente_app, e.contexto)
    primera = todas[0]
    combinaciones = [
        {},
        {"producto": primera["producto"]},
        {"atraso_bucket": primera["atraso_bucket"]},
        {"prioridad": primera["prioridad"]},
        {"canal": primera["canal_recomendado"], "prioridad": primera["prioridad"]},
        {"estado_prediccion": primera["estado_prediccion"]},
        {"estado_optimizacion": "NO_SELECCIONADA"},
        {"cuenta": primera["cuenta_id"][:4]},
        {"producto": primera["producto"], "estado_optimizacion": primera["estado_optimizacion"]},
    ]
    for filtros in combinaciones:
        for orden in ORDENES_DEL_EXPLORADOR:
            for sentido in ("ASC", "DESC"):
                esperadas = _esperadas(todas, filtros, orden, sentido == "DESC")
                obtenidas, total = [], None
                for pagina in range(1, 2 + len(esperadas) // 3 + 1):
                    r = cliente_app.get(
                        "/app/v1/cuentas",
                        params={
                            **e.contexto,
                            **filtros,
                            "orden": orden,
                            "sentido": sentido,
                            "por_pagina": 3,
                            "pagina": pagina,
                        },
                    )
                    assert r.status_code == 200, r.text
                    total = r.json()["total"]
                    obtenidas += [c["cuenta_id"] for c in r.json()["elementos"]]
                assert (total, obtenidas) == (len(esperadas), esperadas), (filtros, orden, sentido)


# --- la Cuenta 360 de la app ----------------------------------------------------------------------


def test_la_cuenta_360_en_un_contexto_completo(cliente_app, e):
    cuenta_id = _cuenta_id(15)
    r = cliente_app.get(f"/app/v1/cuentas/{cuenta_id}", params=e.contexto)

    assert r.status_code == 200
    c = r.json()
    assert c["cuenta_id"] == cuenta_id and c["corte_id"] == e.contexto["corte_id"]
    assert c["en_el_corte"] is True
    assert c["snapshot"]["corte_id"] == e.contexto["corte_id"]
    assert c["snapshot"]["dias_atraso"] == 120
    assert c["presencia"]["estado_presencia"] == "EN_CARTERA"
    for seccion in ("decision", "geografia", "campo", "predictivo", "optimizacion"):
        assert c[seccion]["estado"] == "DISPONIBLE", (seccion, c[seccion])
    assert c["decision"]["canal_recomendado"] == "CAMPO"
    assert c["geografia"]["territorial_run_id"] == str(e.o.territorial.territorial_run_id)
    assert c["geografia"]["codigo_base"] == "BASE_A"
    asignacion = c["campo"]["asignacion"]
    assert asignacion["campo_run_id"] == e.contexto["campo_run_id"]
    assert asignacion["gestor"]["codigo"] == "GESTOR_A"
    assert c["campo"]["carga_gestor"]["gestor"]["codigo"] == "GESTOR_A"
    assert c["ruteo"]["estado"] == "DISPONIBLE"
    assert c["ruteo"]["parada"]["ruteo_run_id"] == e.contexto["ruteo_run_id"]
    assert c["predictivo"]["prediccion"]["scoring_run_id"] == e.contexto["scoring_run_id"]
    assert c["predictivo"]["aviso"] == "Prediccion, no efecto causal ni recomendacion de accion."
    assert c["optimizacion"]["resultado"]["cuenta_id"] == cuenta_id
    assert "causalmente" in c["optimizacion"]["aviso"]
    _sin_personales(r)


def test_una_cuenta_que_no_fue_candidata_de_campo_no_tiene_resultado_de_campo(cliente_app, e):
    c = cliente_app.get(f"/app/v1/cuentas/{_cuenta_id(1)}", params=e.contexto).json()

    assert c["campo"]["estado"] == "SIN_RESULTADO" and c["campo"]["asignacion"] is None
    assert c["ruteo"]["estado"] == "SIN_RESULTADO" and c["ruteo"]["parada"] is None
    assert c["decision"]["estado"] == "DISPONIBLE"


def test_lo_que_el_contexto_no_declara_no_se_busca_en_la_cuenta(cliente_app, e):
    c = cliente_app.get(
        f"/app/v1/cuentas/{_cuenta_id(15)}", params={"corte_id": e.contexto["corte_id"]}
    ).json()

    for seccion in ("decision", "geografia", "campo", "ruteo", "predictivo", "optimizacion"):
        assert c[seccion]["estado"] == "NO_DECLARADA", seccion
        assert c[seccion]["run_id"] is None
    assert c["snapshot"] is not None


def test_una_cuenta_que_no_existe_es_un_404(cliente_app, e):
    r = cliente_app.get(f"/app/v1/cuentas/{uuid4()}", params=e.contexto)

    assert (r.status_code, r.json()["codigo"]) == (404, "CUENTA_NO_ENCONTRADA")


def test_la_cuenta_de_un_contexto_incompatible_no_se_compone(cliente_app, e):
    contexto = {**e.contexto, "scoring_run_id": str(e.otro_scoring.scoring_run_id)}
    r = cliente_app.get(f"/app/v1/cuentas/{_cuenta_id(1)}", params=contexto)

    assert (r.status_code, r.json()["codigo"]) == (409, "CONTEXTO_INCOMPATIBLE")


def test_la_decision_de_la_cuenta_explicada_sin_su_cliente_unico(cliente_app, e):
    r = cliente_app.get(
        f"/app/v1/cuentas/{_cuenta_id(15)}/decision",
        params={"decision_run_id": e.contexto["decision_run_id"]},
    )

    assert r.status_code == 200
    d = r.json()
    assert "cliente_unico" not in d
    assert d["verificacion"] == {"feature_hash": True, "decision_hash": True, "motivos": True}
    assert d["reglas"] and d["features"]
    _sin_personales(r)


def test_la_historia_y_la_linea_de_tiempo_sin_datos_personales(cliente_app, e):
    historia = cliente_app.get(f"/app/v1/cuentas/{_cuenta_id(8)}/historia")
    linea = cliente_app.get(f"/app/v1/cuentas/{_cuenta_id(14)}/linea-de-tiempo")

    assert historia.status_code == linea.status_code == 200
    assert historia.json()["total"] == 1
    _sin_personales(historia)
    _sin_personales(linea)
    eventos = [x["operacional"] for x in linea.json()["elementos"] if x["dominio"] == "OPERACIONAL"]
    # Las dos gestiones de la 14 se registraron despues de K1: la linea las trae con sus dos
    # relojes, y la app las senala como tardias contra el known_at del contexto.
    assert len(eventos) == 2
    for evento in eventos:
        assert datetime.fromisoformat(evento["evento"]["registrado_en"]) > e.g.k1
        assert "actor_ref" not in evento["evento"]


def test_los_pagos_de_la_cuenta_con_su_atribucion_y_sin_causalidad(cliente_app, e):
    unica = cliente_app.get(f"/app/v1/cuentas/{_cuenta_id(1)}/pagos")
    ambigua = cliente_app.get(f"/app/v1/cuentas/{_cuenta_id(2)}/pagos")

    assert unica.status_code == ambigua.status_code == 200
    _sin_personales(unica)
    clasificaciones = [
        p["atribucion"]["clasificacion"]
        for p in unica.json()["pagina"]["elementos"]
        if p["atribucion"]
    ]
    assert clasificaciones == ["ASOCIACION_UNICA"]
    assert [
        p["atribucion"]["clasificacion"]
        for p in ambigua.json()["pagina"]["elementos"]
        if p["atribucion"]
    ] == ["AMBIGUA"]
    assert unica.json()["aviso_atribucion"] == "Asociacion operacional; no implica causalidad."
    assert unica.json()["resumen"]["movimientos_canonicos"] >= 1


def test_las_gestiones_de_la_cuenta_sin_actor_ref_ni_texto_libre(cliente_app, e):
    r = cliente_app.get(f"/app/v1/cuentas/{_cuenta_id(1)}/gestiones")

    assert r.status_code == 200
    g = r.json()
    assert g["resumen"]["gestiones"] == g["pagina"]["total"] >= 1
    assert g["pagina"]["elementos"][0]["evento"]["registrado_en"]
    _sin_personales(r)
    # La observacion es texto libre de quien registro la gestion: la app no la lleva.
    assert '"observacion"' not in r.text


def test_las_paradas_de_una_ruta_sin_cliente_unico_ni_coordenadas(cliente_app, e):
    rutas = cliente_app.get(f"/ruteo-v2/ejecuciones/{e.contexto['ruteo_run_id']}/rutas").json()
    for ruta in rutas["elementos"]:
        r = cliente_app.get(f"/app/v1/ruteo/rutas/{ruta['ruta_id']}/paradas")
        assert r.status_code == 200
        _sin_personales(r)
        assert r.json()["total"] == ruta["conteos"]["paradas_campo"]
    r = cliente_app.get(f"/app/v1/ruteo/rutas/{NO_EXISTE}/paradas")
    assert (r.status_code, r.json()["codigo"]) == (404, "RUTA_NO_ENCONTRADA")


def test_la_cuenta_360_no_crece_en_consultas_con_lo_que_declara_el_contexto(cliente_app, e):
    with consultas() as solo_corte:
        cliente_app.get(
            f"/app/v1/cuentas/{_cuenta_id(15)}", params={"corte_id": e.contexto["corte_id"]}
        )
    with consultas() as completo:
        cliente_app.get(f"/app/v1/cuentas/{_cuenta_id(15)}", params=e.contexto)

    # Cada seccion es una lectura por la llave (ejecucion, cuenta): nunca una por fila.
    assert len(completo) - len(solo_corte) <= 22
    assert len(completo) <= 35


# --- el linaje ------------------------------------------------------------------------------------


def test_el_linaje_dibuja_solo_lo_que_cada_ejecucion_declaro(cliente_app, e):
    r = cliente_app.get("/app/v1/linaje", params=e.contexto)

    assert r.status_code == 200
    linaje = r.json()
    assert linaje["compatible"] is True
    tipos = {n["tipo"] for n in linaje["nodos"]}
    assert tipos >= {
        "CORTE",
        "DECISION_V2",
        "ANALYTICS",
        "SCORING",
        "OPTIMIZACION",
        "CAMPO_V2",
        "RUTEO_V2",
        "TERRITORIAL_V2",
        "GEOGRAFIA",
        "MODELO",
        "ENTRENAMIENTO",
        "CONFIGURACION_CAMPO",
        "CONFIGURACION_OPTIMIZACION",
        "RED_VIAL",
        "MOTOR_PAGOS",
        "ATRIBUCION",
    }
    claves = {n["clave"] for n in linaje["nodos"]}
    for arista in linaje["aristas"]:
        assert arista["desde"] in claves and arista["hacia"] in claves
    pares = {(a["desde"].split(":")[0], a["hacia"].split(":")[0]) for a in linaje["aristas"]}
    assert ("OPTIMIZACION", "SCORING") in pares and ("RUTEO_V2", "CAMPO_V2") in pares
    assert ("SCORING", "MODELO") in pares and ("MODELO", "ENTRENAMIENTO") in pares
    assert ("CAMPO_V2", "TERRITORIAL_V2") in pares and ("TERRITORIAL_V2", "GEOGRAFIA") in pares
    # Nada que no se haya declarado: el scoring no leyo el plan de campo.
    assert ("SCORING", "CAMPO_V2") not in pares
    modelo = next(n for n in linaje["nodos"] if n["tipo"] == "MODELO")
    assert modelo["hashes"]["hash_modelo"] == e.modelo.hash_modelo
    assert modelo["declarado"] is False


def test_el_linaje_de_un_contexto_incompatible_tambien_se_ve(cliente_app, e):
    contexto = {**e.contexto, "scoring_run_id": str(e.otro_scoring.scoring_run_id)}
    linaje = cliente_app.get("/app/v1/linaje", params=contexto).json()

    assert linaje["compatible"] is False


# --- el contrato ----------------------------------------------------------------------------------


def test_app_v1_solo_lee_y_documenta_cada_ruta(cliente_app):
    api = cliente_app.app.openapi()
    rutas = {r: m for r, m in api["paths"].items() if r.startswith("/app/v1")}

    assert set(rutas) == {
        "/app/v1/contextos/opciones",
        "/app/v1/contextos/validar",
        "/app/v1/resumen",
        "/app/v1/predictivo/scorings/{scoring_run_id}/distribucion",
        "/app/v1/cuentas",
        "/app/v1/cuentas/{cuenta_id}",
        "/app/v1/cuentas/{cuenta_id}/decision",
        "/app/v1/cuentas/{cuenta_id}/historia",
        "/app/v1/cuentas/{cuenta_id}/linea-de-tiempo",
        "/app/v1/cuentas/{cuenta_id}/pagos",
        "/app/v1/cuentas/{cuenta_id}/gestiones",
        "/app/v1/ruteo/rutas/{ruta_id}/paradas",
        "/app/v1/linaje",
        "/app/v1/sesion",
    }
    for ruta, metodos in rutas.items():
        assert set(metodos) == {"get"}, ruta
        operacion = metodos["get"]
        assert operacion["summary"] and len(operacion["description"]) > 80, ruta
        assert operacion["tags"] == ["app"], ruta
        # Cada una documenta la credencial que falta y el permiso que no alcanza; y su 422, salvo
        # la sesion, que no tiene ningun parametro que pueda no cumplir el contrato.
        assert {"200", "401", "403"} <= set(operacion["responses"]), ruta
        assert "422" in operacion["responses"] or ruta == "/app/v1/sesion", ruta
        assert "$ref" in json.dumps(operacion["responses"]["200"]), ruta
        for prohibida in ("ejecutar", "enviar", "llamar", "visitar", "aplicar", "optimizar"):
            assert prohibida not in ruta, (ruta, prohibida)
    assert "app" in {t["name"] for t in api["tags"]}


def test_ningun_esquema_de_la_app_lleva_un_dato_personal(cliente_app):
    esquemas = cliente_app.app.openapi()["components"]["schemas"]
    rutas = cliente_app.app.openapi()["paths"]
    usados: set[str] = set()

    def recorrer(nodo) -> None:
        if isinstance(nodo, dict):
            ref = nodo.get("$ref")
            if ref and ref.startswith("#/components/schemas/"):
                nombre = ref.rsplit("/", 1)[1]
                if nombre not in usados:
                    usados.add(nombre)
                    recorrer(esquemas[nombre])
            for valor in nodo.values():
                recorrer(valor)
        elif isinstance(nodo, list):
            for valor in nodo:
                recorrer(valor)

    for ruta, metodos in rutas.items():
        if ruta.startswith("/app/v1"):
            recorrer(metodos["get"]["responses"]["200"])
    assert "Cuenta360AppRespuesta" in usados and "PaginaGestionesApp" in usados
    for nombre in usados:
        campos = set(esquemas[nombre].get("properties", {}))
        assert not campos & {"cliente_unico", "actor_ref", "idempotency_key", "observacion"}, nombre


def test_el_cliente_unico_no_sale_de_la_app_aunque_el_dominio_lo_tenga(cliente_app, e):
    """El mismo recurso por su ruta de dominio si lo trae; por la de la app, no."""
    dominio = cliente_app.get(f"/cuentas/{_cuenta_id(1)}/gestiones")
    app = cliente_app.get(f"/app/v1/cuentas/{_cuenta_id(1)}/gestiones")

    assert cliente(1) in dominio.text
    assert cliente(1) not in app.text


@pytest.fixture
def cliente_app(cliente):
    return cliente


def test_sin_clave_la_app_tampoco_responde(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as sin_clave:
        r = sin_clave.get("/app/v1/contextos/opciones")
    assert (r.status_code, r.json()["codigo"]) == (401, "API_KEY_AUSENTE")


def test_un_identificador_que_no_es_uuid_es_un_422(cliente_app):
    r = cliente_app.get("/app/v1/contextos/validar", params={"corte_id": "no-es-un-uuid"})

    assert (r.status_code, r.json()["codigo"]) == (422, "ENTRADA_INVALIDA")
    assert UUID(NO_EXISTE)
