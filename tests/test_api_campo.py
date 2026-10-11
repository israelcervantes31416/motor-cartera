"""La configuracion de campo y campo/v2 por la API, contra PostgreSQL: publicar una configuracion (y
reutilizarla), leerla, pedir un plan con sus entradas declaradas, leer el plan, sus asignaciones con
cada filtro, sus cargas y sus unidades, las de una cuenta y la Cuenta 360; y que ninguna respuesta
lleve una coordenada, una celda ni un recuadro."""

from __future__ import annotations

import copy
import json
from uuid import UUID, uuid4

import pytest
from campo_v2_escenarios import (
    AMOZOC,
    CAMPO,
    DIGITAL,
    JUEVES,
    LV_PARTIDO,
    MERIDA,
    PUEBLA,
    X0,
    Y0,
    CuentaCampo,
    asignaciones,
    base,
    cuenta_canonica,
    escenario,
    gestor,
)
from sqlmodel import select

from motor_cartera.db.modelos import EjecucionCampoV2
from motor_cartera.db.sesion import sesion

pytestmark = pytest.mark.usefixtures("bd")

CUENTAS = [
    CuentaCampo(1, CAMPO, PUEBLA, X0, Y0),
    CuentaCampo(2, CAMPO, PUEBLA, X0 + 100, Y0 - 100),
    CuentaCampo(3, CAMPO, PUEBLA, X0 + 1_000, Y0),
    CuentaCampo(4, CAMPO, PUEBLA, X0 + 5_000, Y0),
    CuentaCampo(5, CAMPO, AMOZOC, 2_915_500, 790_500),
    CuentaCampo(6, CAMPO, MERIDA, 3_780_500, 1_050_500),
    CuentaCampo(7, DIGITAL, PUEBLA, X0, Y0),
]


# Dentro de cada prueba, no al importar: base() pasa su punto a EPSG:4326 con PostGIS, y al
# recolectar las pruebas la base de pruebas todavia puede no existir.
def _bases() -> tuple[dict, ...]:
    return base("BASE_A", X0 - 3_000, Y0, [PUEBLA]), base("BASE_B", 2_913_600, 790_500, [AMOZOC])


NO_EXISTE = "00000000-0000-4000-8000-000000000000"
LLAVES_PROHIBIDAS = (
    '"lat"',
    '"lon"',
    '"latitud',
    '"longitud',
    '"punto"',
    '"x"',
    '"y"',
    '"ix"',
    '"iy"',
    '"grid_x"',
    '"grid_y"',
    '"bbox"',
    '"centroide"',
    '"geometria"',
)


def _sin_coordenadas(cuerpo) -> None:
    texto = json.dumps(cuerpo)
    for llave in LLAVES_PROHIBIDAS:
        assert llave not in texto, llave


def _documento(e, gestores=None, calendarios=None, nombre="Campo de la API") -> dict:
    return {
        "nombre": nombre,
        "bases_version_id": str(e.bases.bases_version_id),
        "zona_horaria": "America/Mexico_City",
        # Una copia: una prueba cambia el documento, y el calendario del modulo es de todas.
        "calendarios": copy.deepcopy(calendarios or [LV_PARTIDO]),
        "gestores": gestores
        or [
            gestor("GESTOR_001", capacidad=2),
            gestor("GESTOR_002", capacidad=1),
            gestor("GESTOR_003", "BASE_B", capacidad=1, activo=False),
        ],
    }


def _pedir(api, e, configuracion_id, fecha=JUEVES, **cambios):
    cuerpo = {
        "corte_id": str(e.corte.corte_id),
        "decision_run_id": str(e.decision.decision_run_id),
        "territorial_run_id": str(e.territorial.territorial_run_id),
        "configuracion_campo_version_id": str(configuracion_id),
        "fecha_operacion": fecha.isoformat(),
        **cambios,
    }
    return api.post("/campo-v2/planes", json=cuerpo)


@pytest.fixture
def e(tmp_path):
    return escenario(tmp_path, CUENTAS, _bases())


@pytest.fixture
def configuracion(cliente, e):
    respuesta = cliente.post("/campo/configuraciones", json=_documento(e))
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()


@pytest.fixture
def planeado(cliente, e, configuracion, trabajar):
    respuesta = _pedir(cliente, e, configuracion["configuracion_campo_version_id"])
    assert respuesta.status_code == 201, respuesta.text
    trabajar()
    plan = cliente.get(respuesta.headers["location"])
    assert plan.status_code == 200 and plan.json()["estado"] == "EXITOSA", plan.text
    return plan.json()


# --- las configuraciones --------------------------------------------------------------------------


def test_publicar_una_configuracion_responde_201_con_sus_gestores_y_la_misma_200(cliente, e):
    primera = cliente.post("/campo/configuraciones", json=_documento(e))
    otra = _documento(e, nombre="Otra etiqueta")
    otra["gestores"] = otra["gestores"][::-1]
    segunda = cliente.post("/campo/configuraciones", json=otra)

    assert primera.status_code == 201, primera.text
    cuerpo = primera.json()
    assert primera.headers["location"] == (
        f"/campo/configuraciones/{cuerpo['configuracion_campo_version_id']}"
    )
    assert {k: cuerpo[k] for k in ("estado", "gestores", "gestores_activos", "calendarios")} == {
        "estado": "PUBLICADA",
        "gestores": 3,
        "gestores_activos": 2,
        "calendarios": 1,
    }
    assert cuerpo["bases_version_id"] == str(e.bases.bases_version_id)
    assert cuerpo["por_base"] == {
        "BASE_A": {"gestores": 2, "activos": 2, "capacidad_declarada": 3},
        "BASE_B": {"gestores": 1, "activos": 0, "capacidad_declarada": 0},
    }
    assert cuerpo["hash_verificado"] is True and not cuerpo["reutilizada"]
    assert [(g["codigo"], g["capacidad_cuentas_dia"]) for g in cuerpo["detalle_gestores"]] == [
        ("GESTOR_001", 2),
        ("GESTOR_002", 1),
        ("GESTOR_003", 1),
    ]
    assert cuerpo["detalle_calendarios"][0]["semana"]["SABADO"] == []
    assert segunda.status_code == 200
    assert segunda.json()["reutilizada"] is True
    assert (
        segunda.json()["configuracion_campo_version_id"] == cuerpo["configuracion_campo_version_id"]
    )


@pytest.mark.parametrize(
    ("cambio", "codigo"),
    [
        (lambda d: d["gestores"].append(dict(d["gestores"][0])), "CONFIGURACION_INVALIDA"),
        (lambda d: d["gestores"][0].update(base="BASE_Z"), "CONFIGURACION_INVALIDA"),
        (lambda d: d["gestores"][0].update(capacidad_cuentas_dia=-2), "CONFIGURACION_INVALIDA"),
        (lambda d: d["gestores"][0].update(calendario="OTRO"), "CONFIGURACION_INVALIDA"),
        (
            lambda d: d["calendarios"][0]["semana"]["LUNES"].append(
                {"inicio": "13:00", "fin": "17:00"}
            ),
            "CONFIGURACION_INVALIDA",
        ),
        (lambda d: d.update(zona_horaria="Marte/Olimpo"), "CONFIGURACION_INVALIDA"),
        (lambda d: d["gestores"][0].update(nombre="Una persona"), "ENTRADA_INVALIDA"),
        (lambda d: d.update(capacidad=3), "ENTRADA_INVALIDA"),
        (lambda d: d["gestores"][0].update(capacidad_cuentas_dia="20"), "ENTRADA_INVALIDA"),
    ],
)
def test_una_configuracion_invalida_no_se_publica(cliente, e, cambio, codigo):
    documento = _documento(e)
    cambio(documento)

    respuesta = cliente.post("/campo/configuraciones", json=documento)

    assert respuesta.status_code == 422, respuesta.text
    assert respuesta.json()["codigo"] == codigo
    if codigo == "CONFIGURACION_INVALIDA":
        assert respuesta.json()["detalles"]
    assert cliente.get("/campo/configuraciones").json()["total"] == 0


def test_las_configuraciones_se_listan_y_se_leen(cliente, e, configuracion):
    otra = cliente.post(
        "/campo/configuraciones", json=_documento(e, gestores=[gestor("GESTOR_009", capacidad=9)])
    ).json()

    lista = cliente.get("/campo/configuraciones?por_pagina=1")
    detalle = cliente.get(
        f"/campo/configuraciones/{configuracion['configuracion_campo_version_id']}"
    )

    assert lista.status_code == 200
    assert (
        lista.json()["total"],
        lista.json()["elementos"][0]["configuracion_campo_version_id"],
    ) == (
        2,
        otra["configuracion_campo_version_id"],
    )
    assert "detalle_gestores" not in lista.json()["elementos"][0]
    assert detalle.status_code == 200 and detalle.json() == {**configuracion, "reutilizada": False}
    assert cliente.get(f"/campo/configuraciones/{NO_EXISTE}").json()["codigo"] == (
        "CONFIGURACION_NO_ENCONTRADA"
    )


# --- los planes -----------------------------------------------------------------------------------


def test_pedir_un_plan_responde_201_en_proceso_y_el_worker_lo_publica(
    cliente, e, configuracion, trabajar
):
    respuesta = _pedir(cliente, e, configuracion["configuracion_campo_version_id"])

    assert respuesta.status_code == 201, respuesta.text
    abierto = respuesta.json()
    assert abierto["estado"] == "EN_PROCESO" and abierto["resultado"] is None
    assert respuesta.headers["location"] == f"/campo-v2/planes/{abierto['campo_run_id']}"
    trabajar()
    plan = cliente.get(respuesta.headers["location"]).json()
    assert (plan["estado"], plan["resultado"]) == ("EXITOSA", "PLAN_PUBLICADO")
    assert plan["conteos"] == {
        "cuentas_evaluadas_decision": 7,
        "candidatas_campo": 6,
        "asignadas": 3,
        "revision_territorial": 1,
        "sin_gestor": 1,
        "sin_capacidad": 1,
        "rebalanceadas": plan["conteos"]["rebalanceadas"],
    }
    assert plan["capacidad"] == {
        "capacidad_total": 3,
        "capacidad_utilizada": 3,
        "utilizacion": "1.0000",
    }
    assert plan["particion"] == {
        "srid": 6372,
        "tramo_m": 5000,
        "microtramo_m": 1000,
        "redondeo_m": "0.001",
    }
    assert plan["configuracion"]["zona_horaria"] == "America/Mexico_City"
    assert (plan["fecha_operacion"], plan["corte"]["corte_id"]) == (
        "2026-10-01",
        str(e.corte.corte_id),
    )
    assert plan["duracion_segundos"] is not None
    assert "factibilidad vial" in plan["aviso"]
    _sin_coordenadas(plan)
    trabajo = cliente.get(f"/trabajos/{plan['trabajo_id']}").json()
    assert (trabajo["tipo"], trabajo["estado"]) == ("CAMPO_V2", "COMPLETADO")


@pytest.mark.parametrize(
    ("cambios", "estado", "codigo"),
    [
        ({"corte_id": NO_EXISTE}, 404, "CORTE_NO_ENCONTRADO"),
        ({"decision_run_id": NO_EXISTE}, 404, "DECISION_NO_ENCONTRADA"),
        ({"territorial_run_id": NO_EXISTE}, 404, "TERRITORIAL_NO_ENCONTRADO"),
        ({"configuracion_campo_version_id": NO_EXISTE}, 404, "CONFIGURACION_NO_ENCONTRADA"),
        ({"fecha_operacion": "2026-09-29"}, 422, "FECHA_ANTERIOR_AL_CORTE"),
        ({"version_politica": "campo-public-v9"}, 422, "POLITICA_NO_SOPORTADA"),
        ({"fecha_operacion": "manana"}, 422, "ENTRADA_INVALIDA"),
        ({"otra_cosa": 1}, 422, "ENTRADA_INVALIDA"),
    ],
)
def test_lo_que_no_se_puede_planear_responde_su_codigo(
    cliente, e, configuracion, cambios, estado, codigo
):
    respuesta = _pedir(cliente, e, configuracion["configuracion_campo_version_id"], **cambios)

    assert respuesta.status_code == estado, respuesta.text
    assert respuesta.json()["codigo"] == codigo
    assert cliente.get("/campo-v2/planes").json()["total"] == 0


def test_la_misma_peticion_otra_vez_responde_409(cliente, e, configuracion, trabajar):
    primera = _pedir(cliente, e, configuracion["configuracion_campo_version_id"])
    en_proceso = _pedir(cliente, e, configuracion["configuracion_campo_version_id"])
    trabajar()
    ya = _pedir(cliente, e, configuracion["configuracion_campo_version_id"])

    assert primera.status_code == 201
    assert (en_proceso.status_code, en_proceso.json()["codigo"]) == (409, "CAMPO_V2_EN_PROCESO")
    assert (ya.status_code, ya.json()["codigo"]) == (409, "CAMPO_V2_YA_GENERADO")


def test_los_planes_se_listan_por_corte_y_por_fecha(cliente, e, configuracion, planeado):
    otro = _pedir(
        cliente, e, configuracion["configuracion_campo_version_id"], fecha_operacion="2026-10-02"
    )
    assert otro.status_code == 201

    todos = cliente.get("/campo-v2/planes").json()
    del_corte = cliente.get(f"/campo-v2/planes?corte_id={e.corte.corte_id}").json()
    del_jueves = cliente.get("/campo-v2/planes?fecha_operacion=2026-10-01").json()

    assert (todos["total"], del_corte["total"], del_jueves["total"]) == (2, 2, 1)
    assert del_jueves["elementos"][0]["campo_run_id"] == planeado["campo_run_id"]
    assert cliente.get(f"/campo-v2/planes?corte_id={NO_EXISTE}").json()["codigo"] == (
        "CORTE_NO_ENCONTRADO"
    )
    assert cliente.get(f"/campo-v2/planes/{NO_EXISTE}").json()["codigo"] == "CAMPO_V2_NO_ENCONTRADO"


# --- las asignaciones, por cada filtro ------------------------------------------------------------


def _pagina(cliente, plan, consulta="") -> dict:
    respuesta = cliente.get(f"/campo-v2/planes/{plan['campo_run_id']}/asignaciones{consulta}")
    assert respuesta.status_code == 200, respuesta.text
    _sin_coordenadas(respuesta.json())
    return respuesta.json()


def test_las_asignaciones_de_un_plan_en_el_orden_de_la_cuenta(cliente, planeado):
    pagina = _pagina(cliente, planeado, "?por_pagina=50")

    assert pagina["total"] == 6
    estados = {a["cliente_unico"]: a["estado"] for a in pagina["elementos"]}
    assert estados["CU0000000006"] == "REVISION_TERRITORIAL"
    assert estados["CU0000000005"] == "SIN_GESTOR_DISPONIBLE"
    asignada = next(a for a in pagina["elementos"] if a["estado"] == "ASIGNADA")
    assert set(asignada) == {
        "cuenta_id",
        "cliente_unico",
        "estado",
        "prioridad",
        "orden_prioridad",
        "base",
        "gestor",
        "municipio",
        "zona_id",
        "subzona_id",
        "tramo_id",
        "microtramo_id",
        "es_rebalanceada",
        "motivos",
    }
    assert asignada["base"]["codigo"] == "BASE_A" and asignada["municipio"] == "21114"
    assert asignada["gestor"]["codigo"] in {"GESTOR_001", "GESTOR_002"}
    revision = next(a for a in pagina["elementos"] if a["estado"] == "REVISION_TERRITORIAL")
    assert (revision["base"], revision["gestor"], revision["zona_id"]) == (None, None, None)


@pytest.mark.parametrize(
    ("consulta", "esperadas"),
    [
        ("estado=ASIGNADA", 3),
        ("estado=SIN_CAPACIDAD", 1),
        ("estado=SIN_GESTOR_DISPONIBLE", 1),
        ("estado=REVISION_TERRITORIAL", 1),
        ("base=BASE_A", 4),
        ("base=BASE_B", 1),
        ("base=BASE_Z", 0),
        ("gestor=GESTOR_001", 2),
        ("gestor=GESTOR_002", 1),
        ("gestor=GESTOR_003", 0),
        ("gestor=GESTOR_999", 0),
        ("prioridad=MUY_ALTA", 6),
        ("prioridad=BAJA", 0),
        ("base=BASE_A&estado=ASIGNADA", 3),
    ],
)
def test_cada_filtro_de_las_asignaciones(cliente, planeado, consulta, esperadas):
    pagina = _pagina(cliente, planeado, f"?{consulta}&por_pagina=50")

    assert pagina["total"] == len(pagina["elementos"]) == esperadas


def test_los_filtros_de_la_particion(cliente, planeado):
    todas = _pagina(cliente, planeado, "?base=BASE_A&por_pagina=50")["elementos"]
    una = next(a for a in todas if a["cliente_unico"] == "CU0000000001")

    for filtro, nivel in (
        ("zona", "zona_id"),
        ("subzona", "subzona_id"),
        ("tramo", "tramo_id"),
        ("microtramo", "microtramo_id"),
    ):
        pagina = _pagina(cliente, planeado, f"?{filtro}={una[nivel]}&por_pagina=50")
        esperadas = [a["cliente_unico"] for a in todas if a[nivel] == una[nivel]]
        assert pagina["total"] == len(esperadas), filtro
        assert [a["cliente_unico"] for a in pagina["elementos"]] == esperadas, filtro
    # Una unidad de otro nivel, o que no existe, no tiene asignaciones.
    assert _pagina(cliente, planeado, f"?zona={una['microtramo_id']}")["total"] == 0
    assert _pagina(cliente, planeado, f"?tramo={uuid4()}")["total"] == 0
    assert (
        cliente.get(
            f"/campo-v2/planes/{planeado['campo_run_id']}/asignaciones?zona=no-es-uuid"
        ).json()["codigo"]
        == "ENTRADA_INVALIDA"
    )


def test_las_cargas_de_cada_gestor(cliente, planeado):
    respuesta = cliente.get(f"/campo-v2/planes/{planeado['campo_run_id']}/cargas")

    assert respuesta.status_code == 200
    cargas_ = {c["gestor"]["codigo"]: c for c in respuesta.json()["elementos"]}
    assert set(cargas_) == {"GESTOR_001", "GESTOR_002", "GESTOR_003"}
    assert (cargas_["GESTOR_001"]["capacidad"], cargas_["GESTOR_001"]["asignadas"]) == (2, 2)
    assert cargas_["GESTOR_001"]["utilizacion"] == "1.0000"
    assert cargas_["GESTOR_003"]["estado_jornada"] == "INACTIVO"
    assert (cargas_["GESTOR_003"]["capacidad"], cargas_["GESTOR_003"]["ventanas"]) == (0, [])
    assert [v["inicio"] for v in cargas_["GESTOR_002"]["ventanas"]] == ["09:00", "16:00"]
    assert cargas_["GESTOR_002"]["ventanas"][0]["inicio_en"] == "2026-10-01T15:00:00Z"
    assert (
        cliente.get(f"/campo-v2/planes/{planeado['campo_run_id']}/cargas?base=BASE_B").json()[
            "total"
        ]
        == 1
    )
    _sin_coordenadas(respuesta.json())


def test_las_unidades_del_plan_por_nivel_padre_y_base(cliente, planeado):
    ruta = f"/campo-v2/planes/{planeado['campo_run_id']}/unidades"
    zonas = cliente.get(f"{ruta}?nivel=ZONA").json()
    microtramos = cliente.get(f"{ruta}?nivel=MICROTRAMO&base=BASE_A").json()

    assert zonas["total"] == 2
    assert {z["base"]["codigo"] for z in zonas["elementos"]} == {"BASE_A", "BASE_B"}
    assert all(z["padre_id"] is None and z["cvegeo"] is None for z in zonas["elementos"])
    assert microtramos["total"] == 3
    assert all(m["propietario_preferido"] for m in microtramos["elementos"])
    zona_a = next(z for z in zonas["elementos"] if z["base"]["codigo"] == "BASE_A")
    hijas = cliente.get(f"{ruta}?padre={zona_a['unidad_id']}").json()
    assert [h["nivel"] for h in hijas["elementos"]] == ["SUBZONA"]
    assert hijas["elementos"][0]["cvegeo"] == "21114"
    assert cliente.get(f"{ruta}?nivel=CUADRA").json()["codigo"] == "ENTRADA_INVALIDA"
    _sin_coordenadas(zonas)
    _sin_coordenadas(microtramos)


# --- una cuenta y la Cuenta 360 -------------------------------------------------------------------


def test_los_planes_de_una_cuenta_y_su_campo_resumen(cliente, e, configuracion, planeado, trabajar):
    otro = _pedir(
        cliente, e, configuracion["configuracion_campo_version_id"], fecha_operacion="2026-10-05"
    )
    assert otro.status_code == 201
    trabajar()
    cuenta = cuenta_canonica(1)

    historia = cliente.get(f"/cuentas/{cuenta.cuenta_id}/campo-v2")
    resumen = cliente.get(f"/cuentas/{cuenta.cuenta_id}")
    al_jueves = cliente.get(f"/cuentas/{cuenta.cuenta_id}?al=2026-10-01")
    publicado = asignaciones_api(cliente, planeado, cuenta.cuenta_id)

    assert historia.status_code == 200
    planes = historia.json()["elementos"]
    assert [p["fecha_operacion"] for p in planes] == ["2026-10-05", "2026-10-01"]
    assert planes[1]["campo_run_id"] == planeado["campo_run_id"]
    assert planes[1]["corte"]["fecha_corte"] == "2026-09-30"
    c = resumen.json()["campo_resumen"]
    assert c["fecha_plan"] == "2026-10-05" and c["estado"] == "ASIGNADA"
    assert set(c) == {
        "fecha_plan",
        "campo_run_id",
        "estado",
        "prioridad",
        "base",
        "gestor_codigo",
        "zona_id",
        "subzona_id",
        "tramo_id",
        "microtramo_id",
    }
    assert al_jueves.json()["campo_resumen"]["campo_run_id"] == planeado["campo_run_id"]
    assert al_jueves.json()["campo_resumen"]["gestor_codigo"] == publicado["gestor"]["codigo"]
    _sin_coordenadas(historia.json())
    _sin_coordenadas(resumen.json())
    # Una cuenta que no fue candidata de campo no tiene campo_resumen.
    digital = cuenta_canonica(7)
    assert cliente.get(f"/cuentas/{digital.cuenta_id}").json()["campo_resumen"] is None
    assert cliente.get(f"/cuentas/{digital.cuenta_id}/campo-v2").json()["total"] == 0


def asignaciones_api(cliente, plan, cuenta_id) -> dict:
    pagina = cliente.get(f"/campo-v2/planes/{plan['campo_run_id']}/asignaciones?por_pagina=50")
    return next(a for a in pagina.json()["elementos"] if a["cuenta_id"] == str(cuenta_id))


def test_lo_publicado_por_la_api_es_lo_que_publico_el_plan(cliente, planeado):
    """La API no recalcula nada: cada asignacion es la que esta en la base."""
    with sesion() as s:
        ejecucion_id = s.exec(
            select(EjecucionCampoV2.id).where(
                EjecucionCampoV2.campo_run_id == UUID(planeado["campo_run_id"])
            )
        ).one()
    en_la_base = asignaciones(ejecucion_id)
    pagina = _pagina(cliente, planeado, "?por_pagina=50")

    for a in pagina["elementos"]:
        numero = int(a["cliente_unico"][2:])
        assert a["estado"] == en_la_base[numero]["estado"]
        assert (a["gestor"] or {}).get("codigo") == en_la_base[numero]["gestor"]
        assert a["motivos"] == en_la_base[numero]["motivos"]
