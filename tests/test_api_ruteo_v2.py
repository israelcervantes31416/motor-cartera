"""ruteo/v2 por la API, contra PostgreSQL: las versiones de la red vial, pedir el ruteo de un plan
de campo/v2 (y lo que no se puede rutear), la ejecucion, sus rutas y sus paradas con cada filtro y
su paginacion, una ruta y sus paradas en orden, las de una cuenta y la Cuenta 360; y que ninguna
respuesta lleve una coordenada, una geometria ni una polilinea.

El escenario se construye una vez por modulo, como lo haria la operacion: el POST deja la ejecucion
EN_PROCESO con su trabajo en la cola y un worker, con un proveedor de pruebas, la publica. Las
pruebas solo leen, salvo la ultima, que deja otra ejecucion EN_PROCESO.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from types import SimpleNamespace

import pytest
from campo_v2_escenarios import LV_PARTIDO, cuenta_canonica
from fastapi.testclient import TestClient
from ruteo_v2_escenarios import (
    CuentaRuta,
    ProveedorDePruebas,
    base,
    coordenada,
    escenario,
    gestor,
    red_de_pruebas,
)
from sqlalchemy import func, update

from motor_cartera.api import crear_app
from motor_cartera.config import Config
from motor_cartera.db.modelos import EstadoTrabajo, TipoTrabajo, TrabajoOrquestacion
from motor_cartera.db.sesion import sesion
from motor_cartera.orquestacion import worker
from motor_cartera.ruteo_v2 import ejecuciones as motor
from motor_cartera.ruteo_v2.ejecuciones import SaludRuteo

NO_EXISTE = "00000000-0000-4000-8000-000000000000"
CORTO = {"codigo": "CORTO", "semana": {"JUEVES": [{"inicio": "09:00", "fin": "09:30"}]}}
"""Media hora el jueves: cabe una visita cerca de la base, no dos."""

# Tres bases que cubren el mismo municipio, lejos entre si: cada cuenta va a la mas cercana.
BASE_A, BASE_B, BASE_C = (1_000, 1_000), (8_000, 1_000), (1_000, 8_000)
CUENTAS = [
    # GESTOR_A, en BASE_A, de 9 a 14 y de 16 a 19: las cuatro caben (COMPLETA).
    CuentaRuta(1, 1_000, 1_500),
    CuentaRuta(2, 1_600, 1_000),
    CuentaRuta(3, 2_000, 2_000),
    CuentaRuta(4, 500, 2_500),
    # GESTOR_C, en BASE_B, que no tiene snap valido (BASE_NO_ENRUTABLE).
    CuentaRuta(5, 8_000, 1_500),
    CuentaRuta(6, 8_500, 1_000),
    # GESTOR_B, en BASE_C, media hora: la 7 cabe, la 8 ya no (SIN_TIEMPO) y la 10 no tiene camino.
    CuentaRuta(7, 1_000, 8_500),
    CuentaRuta(8, 1_500, 8_000),
    CuentaRuta(10, 500, 8_200),
    # La 9 no es de campo: decision/v2 la manda a DIGITAL.
    CuentaRuta(9, 3_000, 3_000),
]
PRIORIDADES = {
    1: "MUY_ALTA",
    2: "ALTA",
    3: "MEDIA",
    4: "BAJA",
    5: "MUY_ALTA",
    6: "ALTA",
    7: "MUY_ALTA",
    8: "ALTA",
    10: "MUY_ALTA",
}
LLAVES_PROHIBIDAS = (
    '"lat"',
    '"lon"',
    '"latitud',
    '"longitud',
    '"punto"',
    '"x"',
    '"y"',
    '"coordinates"',
    '"coordenadas"',
    '"location"',
    '"waypoints"',
    '"geometry"',
    '"geometria"',
    '"polyline"',
    '"polilinea"',
    '"bbox"',
)
GRADOS = re.compile(r"-98\.\d{3}|19\.0\d{3}")
"""Una longitud o una latitud de la red sintetica, en cualquier valor."""


def _sin_coordenadas(cuerpo) -> None:
    texto = json.dumps(cuerpo)
    for llave in LLAVES_PROHIBIDAS:
        assert llave not in texto, llave
    assert not GRADOS.search(texto), GRADOS.search(texto)


def _solo_ruteo_en_la_cola() -> None:
    """Lo que el escenario ya ejecuto a mano se da por terminado en la cola: solo queda lo que
    pida la API."""
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


@pytest.fixture(scope="module")
def publicado(bd_del_modulo, tmp_path_factory):
    e = escenario(
        tmp_path_factory.mktemp("api_ruteo_v2"),
        CUENTAS,
        [base("BASE_A", *BASE_A), base("BASE_B", *BASE_B), base("BASE_C", *BASE_C)],
        [
            gestor("GESTOR_A", "BASE_A", capacidad=10),
            gestor("GESTOR_B", "BASE_C", capacidad=10, calendario="CORTO"),
            gestor("GESTOR_C", "BASE_B", capacidad=10),
        ],
        calendarios=[LV_PARTIDO, CORTO],
        prioridades=PRIORIDADES,
    )
    red = red_de_pruebas()
    proveedor = ProveedorDePruebas(
        red.huella,
        snaps={coordenada(*BASE_B): Decimal("900")},
        desconectados={coordenada(500, 8_200)},
    )
    _solo_ruteo_en_la_cola()
    entrada = {
        "campo_run_id": str(e.campo.campo_run_id),
        "red_vial_version_id": str(red.red_vial_version_id),
    }
    app = crear_app(Config(api_key="clave-del-modulo"))
    with (
        TestClient(app, headers={"X-API-Key": "clave-del-modulo"}) as api,
        pytest.MonkeyPatch.context() as parche,
    ):
        abierta = api.post("/ruteo-v2/ejecuciones", json=entrada)
        assert abierta.status_code == 201, abierta.text
        parche.setattr(motor, "proveedor_configurado", lambda _config: proveedor)
        procesado = worker.procesar_un_trabajo("worker-api", Config(), SaludRuteo(proveedor, 60))
        terminada = api.get(abierta.headers["location"]).json()
    assert (procesado.tipo, procesado.estado) == (TipoTrabajo.RUTEO_V2, EstadoTrabajo.COMPLETADO)
    return SimpleNamespace(
        escenario=e,
        red=red,
        entrada=entrada,
        abierta=abierta,
        ejecucion=terminada,
        ruteo_run_id=terminada["ruteo_run_id"],
    )


def _get(cliente, ruta: str) -> dict:
    respuesta = cliente.get(ruta)
    assert respuesta.status_code == 200, respuesta.text
    _sin_coordenadas(respuesta.json())
    return respuesta.json()


def _numeros(pagina: dict) -> list[int]:
    return [int(p["cliente_unico"][2:]) for p in pagina["elementos"]]


# --- la ejecucion ---------------------------------------------------------------------------------


def test_pedir_un_ruteo_responde_201_en_proceso_y_el_worker_lo_publica(cliente, publicado):
    abierta = publicado.abierta.json()
    assert abierta["estado"] == "EN_PROCESO" and abierta["resultado"] is None
    assert publicado.abierta.headers["location"] == (
        f"/ruteo-v2/ejecuciones/{abierta['ruteo_run_id']}"
    )
    assert abierta["conteos"]["programadas"] == 0
    _sin_coordenadas(abierta)

    e = _get(cliente, publicado.abierta.headers["location"])

    assert (e["estado"], e["resultado"]) == ("EXITOSA", "RUTAS_PUBLICADAS")
    assert e["firma_inputs"] == abierta["firma_inputs"]
    assert e["campo_run_id"] == publicado.entrada["campo_run_id"]
    assert (e["fecha_operacion"], e["zona_horaria"]) == ("2026-10-01", "America/Mexico_City")
    assert e["red"] == {
        "red_vial_version_id": publicado.entrada["red_vial_version_id"],
        "huella": publicado.red.huella,
        "region": publicado.red.region,
        "motor_ruteo": "SINTETICO",
        "version_motor_ruteo": publicado.red.version_motor_ruteo,
    }
    assert (e["version_motor"], e["version_politica"]) == ("ruteo/v2", "ruteo-public-v2")
    assert e["politica"] == {
        "algoritmo": "PRIORITY_NEAREST_NEIGHBOR_2OPT/v1",
        "max_snap_distancia_m": 500,
        "duracion_servicio_s": 900,
        "inicio_fin_base": True,
        "bloque_matriz": 100,
        "max_mejoras_2opt": 100,
        "perfil": "car",
    }
    assert e["conteos"] == {
        "gestores_objetivo": 3,
        "rutas_completas": 1,
        "rutas_parciales": 1,
        "rutas_sin_paradas": 0,
        "bases_no_enrutables": 1,
        "rutas_sin_jornada": 0,
        "paradas_objetivo": 9,
        "programadas": 5,
        "sin_tiempo": 1,
        "no_enrutables": 1,
        "no_enrutables_base": 2,
    }
    assert e["totales"]["duracion_servicio_total_s"] == 5 * 900
    assert e["totales"]["distancia_total_m"] > 0
    assert e["duracion_segundos"] is not None
    assert "sin trafico" in e["aviso"] and "no una ruta optima" in e["aviso"]
    trabajo = _get(cliente, f"/trabajos/{e['trabajo_id']}")
    assert (trabajo["tipo"], trabajo["estado"]) == ("RUTEO_V2", "COMPLETADO")


@pytest.mark.parametrize(
    ("cambios", "estado", "codigo"),
    [
        ({"campo_run_id": NO_EXISTE}, 404, "CAMPO_V2_NO_ENCONTRADO"),
        ({"red_vial_version_id": NO_EXISTE}, 404, "RED_VIAL_NO_ENCONTRADA"),
        ({"version_politica": "ruteo-public-v9"}, 422, "POLITICA_NO_SOPORTADA"),
        ({"red_vial_version_id": "la-ultima"}, 422, "ENTRADA_INVALIDA"),
        ({"otra_cosa": 1}, 422, "ENTRADA_INVALIDA"),
        ({"campo_run_id": None}, 422, "ENTRADA_INVALIDA"),
    ],
)
def test_lo_que_no_se_puede_rutear_responde_su_codigo_sin_registrar_nada(
    cliente, publicado, cambios, estado, codigo
):
    respuesta = cliente.post("/ruteo-v2/ejecuciones", json={**publicado.entrada, **cambios})

    assert respuesta.status_code == estado, respuesta.text
    assert respuesta.json()["codigo"] == codigo
    assert _get(cliente, "/ruteo-v2/ejecuciones")["total"] == 1


def test_las_mismas_entradas_otra_vez_responden_409_ya_generado(cliente, publicado):
    otra = cliente.post("/ruteo-v2/ejecuciones", json=publicado.entrada)
    con_su_politica = cliente.post(
        "/ruteo-v2/ejecuciones", json={**publicado.entrada, "version_politica": "ruteo-public-v2"}
    )

    for respuesta in (otra, con_su_politica):
        assert (respuesta.status_code, respuesta.json()["codigo"]) == (409, "RUTEO_V2_YA_GENERADO")
    assert _get(cliente, "/ruteo-v2/ejecuciones")["total"] == 1


def test_las_ejecuciones_se_listan_por_plan_por_red_y_por_estado(cliente, publicado):
    run = publicado.ruteo_run_id
    plan, red = publicado.entrada["campo_run_id"], publicado.entrada["red_vial_version_id"]

    for consulta in (
        "",
        f"?campo_run_id={plan}",
        f"?red_vial_version_id={red}",
        "?estado=EXITOSA",
        f"?campo_run_id={plan}&red_vial_version_id={red}&estado=EXITOSA",
    ):
        pagina = _get(cliente, f"/ruteo-v2/ejecuciones{consulta}")
        assert [x["ruteo_run_id"] for x in pagina["elementos"]] == [run], consulta
    assert _get(cliente, "/ruteo-v2/ejecuciones?estado=FALLIDA")["total"] == 0
    for consulta, codigo in (
        (f"?campo_run_id={NO_EXISTE}", "CAMPO_V2_NO_ENCONTRADO"),
        (f"?red_vial_version_id={NO_EXISTE}", "RED_VIAL_NO_ENCONTRADA"),
    ):
        respuesta = cliente.get(f"/ruteo-v2/ejecuciones{consulta}")
        assert (respuesta.status_code, respuesta.json()["codigo"]) == (404, codigo)
    for consulta in ("?estado=TERMINADA", "?por_pagina=0", "?campo_run_id=ayer"):
        respuesta = cliente.get(f"/ruteo-v2/ejecuciones{consulta}")
        assert (respuesta.status_code, respuesta.json()["codigo"]) == (422, "ENTRADA_INVALIDA")
    for ruta in (f"/ruteo-v2/ejecuciones/{NO_EXISTE}", f"/ruteo-v2/ejecuciones/{NO_EXISTE}/rutas"):
        respuesta = cliente.get(ruta)
        assert (respuesta.status_code, respuesta.json()["codigo"]) == (
            404,
            "RUTEO_V2_NO_ENCONTRADO",
        )


# --- las redes ------------------------------------------------------------------------------------


def test_las_redes_se_listan_y_se_leen_sin_sus_archivos(cliente, publicado):
    pagina = _get(cliente, "/ruteo-v2/redes")
    detalle = _get(cliente, f"/ruteo-v2/redes/{publicado.red.red_vial_version_id}")

    assert pagina["total"] == 1
    (red,) = pagina["elementos"]
    assert red["red_vial_version_id"] == str(publicado.red.red_vial_version_id)
    assert (red["estado"], red["motor_ruteo"], red["huella"]) == (
        "PUBLICADA",
        "SINTETICO",
        publicado.red.huella,
    )
    # Una red sintetica no es de OpenStreetMap: no lleva su atribucion.
    assert red["atribucion"] is None
    assert {k: v for k, v in detalle.items() if k not in ("comandos", "artefactos")} == red
    assert (detalle["comandos"], detalle["artefactos"]) == ([], [])
    respuesta = cliente.get(f"/ruteo-v2/redes/{NO_EXISTE}")
    assert (respuesta.status_code, respuesta.json()["codigo"]) == (404, "RED_VIAL_NO_ENCONTRADA")
    respuesta = cliente.get("/ruteo-v2/redes/la-ultima")
    assert (respuesta.status_code, respuesta.json()["codigo"]) == (422, "ENTRADA_INVALIDA")


# --- las rutas ------------------------------------------------------------------------------------


def test_las_rutas_van_por_codigo_de_gestor_y_se_filtran(cliente, publicado):
    base_url = f"/ruteo-v2/ejecuciones/{publicado.ruteo_run_id}/rutas"

    todas = _get(cliente, base_url)

    assert todas["ruteo_run_id"] == publicado.ruteo_run_id
    assert [
        (r["gestor"]["codigo"], r["base"]["codigo"], r["estado_ruta"]) for r in todas["elementos"]
    ] == [
        ("GESTOR_A", "BASE_A", "COMPLETA"),
        ("GESTOR_B", "BASE_C", "PARCIAL"),
        ("GESTOR_C", "BASE_B", "BASE_NO_ENRUTABLE"),
    ]
    a, b, c = todas["elementos"]
    assert a["conteos"] == {
        "paradas_campo": 4,
        "programadas": 4,
        "sin_tiempo": 0,
        "no_enrutables": 0,
        "no_enrutables_base": 0,
    }
    assert (b["conteos"]["programadas"], b["conteos"]["sin_tiempo"]) == (1, 1)
    assert b["conteos"]["no_enrutables"] == 1
    # Las horas, en la zona de la configuracion de campo: el mismo instante, con su desfase.
    assert a["salida_base"] == "2026-10-01T09:00:00-06:00"
    assert (a["jornada_inicio"], a["jornada_fin"]) == (
        "2026-10-01T09:00:00-06:00",
        "2026-10-01T19:00:00-06:00",
    )
    assert b["jornada_fin"] == "2026-10-01T09:30:00-06:00"
    assert b["regreso"]["llegada"] <= "2026-10-01T09:30:00-06:00"
    assert a["duracion_total_s"] == (
        a["duracion_traslado_s"] + a["duracion_servicio_s"] + a["duracion_espera_s"]
    )
    assert a["hash_matriz"] and a["hash_secuencia"]
    # Una base sin snap valido no tiene ruta: ni salida, ni regreso, ni distancia.
    assert (c["motivo_base"], c["salida_base"], c["regreso"]) == ("SNAP_EXCESIVO", None, None)
    assert (c["distancia_total_m"], c["hash_matriz"]) == (0, None)
    assert c["base_snap_distancia_m"] == "900.00"

    for consulta, gestores in (
        ("?gestor=GESTOR_B", ["GESTOR_B"]),
        ("?base=BASE_A", ["GESTOR_A"]),
        ("?estado=BASE_NO_ENRUTABLE", ["GESTOR_C"]),
        ("?estado=PARCIAL&base=BASE_C", ["GESTOR_B"]),
        ("?estado=SIN_JORNADA", []),
        ("?gestor=GESTOR_Z", []),
        ("?por_pagina=2&pagina=2", ["GESTOR_C"]),
    ):
        pagina = _get(cliente, f"{base_url}{consulta}")
        assert [r["gestor"]["codigo"] for r in pagina["elementos"]] == gestores, consulta
    assert _get(cliente, f"{base_url}?por_pagina=2")["total"] == 3
    for consulta in ("?estado=OPTIMA", "?gestor=gestor%20a", "?pagina=0"):
        respuesta = cliente.get(f"{base_url}{consulta}")
        assert (respuesta.status_code, respuesta.json()["codigo"]) == (422, "ENTRADA_INVALIDA")


def test_una_ruta_y_sus_paradas_las_programadas_en_su_orden_y_despues_las_demas(cliente, publicado):
    rutas = _get(cliente, f"/ruteo-v2/ejecuciones/{publicado.ruteo_run_id}/rutas")["elementos"]
    a, b, c = rutas

    assert _get(cliente, f"/ruteo-v2/rutas/{a['ruta_id']}") == a
    de_a = _get(cliente, f"/ruteo-v2/rutas/{a['ruta_id']}/paradas")
    de_b = _get(cliente, f"/ruteo-v2/rutas/{b['ruta_id']}/paradas")
    de_c = _get(cliente, f"/ruteo-v2/rutas/{c['ruta_id']}/paradas")

    # Por prioridad: MUY_ALTA, ALTA, MEDIA, BAJA.
    assert _numeros(de_a) == [1, 2, 3, 4]
    assert [p["orden_visita"] for p in de_a["elementos"]] == [1, 2, 3, 4]
    primera = de_a["elementos"][0]
    assert primera["salida_anterior"] == "2026-10-01T09:00:00-06:00"
    assert primera["llegada_estimada"] == primera["inicio_servicio"]
    assert all(
        p["ruta_id"] == a["ruta_id"] and p["gestor"] == "GESTOR_A" for p in de_a["elementos"]
    )
    assert (
        sum(p["distancia_desde_anterior_m"] for p in de_a["elementos"])
        + a["regreso"]["distancia_m"]
        == a["distancia_total_m"]
    )
    # La programada primero; despues las demas, por cuenta_id, con su motivo y sin horas.
    assert _numeros(de_b)[0] == 7
    assert {
        int(p["cliente_unico"][2:]): (p["estado"], p["motivo"], p["orden_visita"])
        for p in de_b["elementos"]
    } == {
        7: ("PROGRAMADA", None, 1),
        8: ("SIN_TIEMPO", "JORNADA_AGOTADA", None),
        10: ("NO_ENRUTABLE", "RED_DESCONECTADA", None),
    }
    for p in de_b["elementos"][1:]:
        assert p["llegada_estimada"] is None and p["distancia_desde_anterior_m"] is None
    assert {(p["estado"], p["motivo"]) for p in de_c["elementos"]} == {
        ("NO_ENRUTABLE_BASE", "BASE_NO_ENRUTABLE")
    }
    assert _numeros(
        _get(cliente, f"/ruteo-v2/rutas/{a['ruta_id']}/paradas?por_pagina=3&pagina=2")
    ) == [4]
    respuesta = cliente.get(f"/ruteo-v2/rutas/{NO_EXISTE}")
    assert (respuesta.status_code, respuesta.json()["codigo"]) == (404, "RUTA_NO_ENCONTRADA")
    respuesta = cliente.get(f"/ruteo-v2/rutas/{NO_EXISTE}/paradas")
    assert (respuesta.status_code, respuesta.json()["codigo"]) == (404, "RUTA_NO_ENCONTRADA")


# --- las paradas ----------------------------------------------------------------------------------


def test_las_paradas_van_por_cuenta_y_se_filtran_en_la_base(cliente, publicado):
    base_url = f"/ruteo-v2/ejecuciones/{publicado.ruteo_run_id}/paradas"

    todas = _get(cliente, f"{base_url}?por_pagina=50")

    assert todas["total"] == 9
    # En el orden de la cuenta canonica, como las asignaciones de campo/v2: el de su llave.
    internos = [cuenta_canonica(n).id for n in _numeros(todas)]
    assert internos == sorted(internos)
    assert 9 not in _numeros(todas)
    for consulta, numeros in (
        ("?estado=PROGRAMADA", {1, 2, 3, 4, 7}),
        ("?estado=SIN_TIEMPO", {8}),
        ("?estado=NO_ENRUTABLE", {10}),
        ("?estado=NO_ENRUTABLE_BASE", {5, 6}),
        ("?gestor=GESTOR_B", {7, 8, 10}),
        ("?base=BASE_B", {5, 6}),
        ("?prioridad=MUY_ALTA", {1, 5, 7, 10}),
        ("?prioridad=ALTA&gestor=GESTOR_A", {2}),
        ("?prioridad=BAJA&estado=PROGRAMADA&base=BASE_A&gestor=GESTOR_A", {4}),
        ("?estado=SIN_TIEMPO&base=BASE_A", set()),
    ):
        pagina = _get(cliente, f"{base_url}{consulta}&por_pagina=50")
        assert set(_numeros(pagina)) == numeros, consulta
        assert pagina["total"] == len(numeros), consulta
    paginas = [_get(cliente, f"{base_url}?por_pagina=4&pagina={n}") for n in (1, 2, 3)]
    assert [len(p["elementos"]) for p in paginas] == [4, 4, 1]
    assert sum((_numeros(p) for p in paginas), []) == _numeros(todas)
    for consulta in ("?estado=CANCELADA", "?prioridad=URGENTE", "?base=base%20a", "?por_pagina=0"):
        respuesta = cliente.get(f"{base_url}{consulta}")
        assert (respuesta.status_code, respuesta.json()["codigo"]) == (422, "ENTRADA_INVALIDA")


# --- las de una cuenta y la Cuenta 360 ------------------------------------------------------------


def test_las_paradas_de_una_cuenta_y_su_ruteo_resumen(cliente, publicado):
    cuenta = cuenta_canonica(1)
    sin_tiempo = cuenta_canonica(8)

    historia = _get(cliente, f"/cuentas/{cuenta.cuenta_id}/ruteo-v2")
    resumen = _get(cliente, f"/cuentas/{cuenta.cuenta_id}")
    antes = _get(cliente, f"/cuentas/{cuenta.cuenta_id}?al=2026-09-30")

    assert (historia["cuenta_id"], historia["total"]) == (str(cuenta.cuenta_id), 1)
    (parada,) = historia["elementos"]
    assert parada["ruteo_run_id"] == publicado.ruteo_run_id
    assert (parada["fecha_operacion"], parada["zona_horaria"], parada["base"]) == (
        "2026-10-01",
        "America/Mexico_City",
        "BASE_A",
    )
    assert (parada["estado"], parada["orden_visita"], parada["estado_ruta"]) == (
        "PROGRAMADA",
        1,
        "COMPLETA",
    )
    assert parada["version_politica"] == "ruteo-public-v2"
    r = resumen["ruteo_resumen"]
    assert set(r) == {
        "ruteo_run_id",
        "ruta_id",
        "fecha_operacion",
        "gestor_codigo",
        "estado",
        "motivo",
        "orden_visita",
        "llegada_estimada",
        "distancia_desde_anterior_m",
        "duracion_desde_anterior_s",
    }
    assert (r["ruteo_run_id"], r["ruta_id"], r["gestor_codigo"]) == (
        publicado.ruteo_run_id,
        parada["ruta_id"],
        "GESTOR_A",
    )
    assert (r["estado"], r["orden_visita"], r["llegada_estimada"]) == (
        "PROGRAMADA",
        1,
        parada["llegada_estimada"],
    )
    assert r["llegada_estimada"].endswith("-06:00")
    # Antes de la fecha de operacion, no habia ruta.
    assert antes["ruteo_resumen"] is None
    otra = _get(cliente, f"/cuentas/{sin_tiempo.cuenta_id}")["ruteo_resumen"]
    assert (otra["estado"], otra["motivo"], otra["llegada_estimada"]) == (
        "SIN_TIEMPO",
        "JORNADA_AGOTADA",
        None,
    )
    # La 9 no fue de campo: no tiene ruteo.
    digital = cuenta_canonica(9)
    assert _get(cliente, f"/cuentas/{digital.cuenta_id}")["ruteo_resumen"] is None
    assert _get(cliente, f"/cuentas/{digital.cuenta_id}/ruteo-v2")["total"] == 0
    respuesta = cliente.get(f"/cuentas/{NO_EXISTE}/ruteo-v2")
    assert (respuesta.status_code, respuesta.json()["codigo"]) == (404, "CUENTA_NO_ENCONTRADA")


# --- la documentacion -----------------------------------------------------------------------------


def test_openapi_documenta_las_diez_operaciones_de_ruteo_v2(app):
    api = app.openapi()
    operaciones = {
        (metodo.upper(), ruta): operacion
        for ruta, metodos in api["paths"].items()
        for metodo, operacion in metodos.items()
        if "ruteo-v2" in operacion.get("tags", [])
    }

    assert {clave: set(op["responses"]) for clave, op in operaciones.items()} == {
        ("GET", "/ruteo-v2/redes"): {"200", "401", "403", "422"},
        ("GET", "/ruteo-v2/redes/{red_vial_version_id}"): {"200", "401", "403", "404", "422"},
        ("POST", "/ruteo-v2/ejecuciones"): {"201", "401", "403", "404", "409", "422"},
        ("GET", "/ruteo-v2/ejecuciones"): {"200", "401", "403", "404", "422"},
        ("GET", "/ruteo-v2/ejecuciones/{ruteo_run_id}"): {"200", "401", "403", "404", "422"},
        ("GET", "/ruteo-v2/ejecuciones/{ruteo_run_id}/rutas"): {"200", "401", "403", "404", "422"},
        ("GET", "/ruteo-v2/ejecuciones/{ruteo_run_id}/paradas"): {
            "200",
            "401",
            "403",
            "404",
            "422",
        },
        ("GET", "/ruteo-v2/rutas/{ruta_id}"): {"200", "401", "403", "404", "422"},
        ("GET", "/ruteo-v2/rutas/{ruta_id}/paradas"): {"200", "401", "403", "404", "422"},
        ("GET", "/cuentas/{cuenta_id}/ruteo-v2"): {"200", "401", "403", "404", "422"},
    }
    for operacion in operaciones.values():
        assert operacion["tags"] == ["ruteo-v2"]
        assert operacion["summary"] and operacion["description"]
        for codigo, respuesta in operacion["responses"].items():
            if codigo[0] in "45":
                esquema = respuesta["content"]["application/json"]["schema"]
                assert esquema["$ref"].endswith("/ErrorRespuesta")
    post = operaciones[("POST", "/ruteo-v2/ejecuciones")]
    assert "worker" in post["responses"]["201"]["description"]
    assert "201 quiere decir que la ejecucion se creo, no que termino" in post["description"]
    for codigo in ("RUTEO_V2_EN_PROCESO", "RUTEO_V2_YA_GENERADO"):
        assert f"`{codigo}`" in post["responses"]["409"]["description"]
    for codigo in ("CAMPO_V2_NO_EXITOSO", "RED_VIAL_NO_PUBLICADA", "POLITICA_NO_SOPORTADA"):
        assert f"`{codigo}`" in post["responses"]["422"]["description"]
    # Ningun esquema de ruteo/v2 tiene donde poner una coordenada.
    esquemas = json.dumps(
        {
            nombre: esquema
            for nombre, esquema in api["components"]["schemas"].items()
            if "Ruta" in nombre or "Parada" in nombre or "Red" in nombre or "Ruteo" in nombre
        }
    )
    for llave in LLAVES_PROHIBIDAS:
        assert llave not in esquemas, llave


def test_ruteo_v2_no_le_cambia_el_nombre_a_ningun_esquema(app):
    # Dos clases con un mismo nombre salen del OpenAPI calificadas por su modulo, y la de v1
    # cambiaria de nombre para quien ya genera un cliente con el. Ningun esquema que usa ruteo/v2
    # comparte el suyo, y los de v1 que se llaman como sus vecinos de v2 conservan el que tenian.
    api = app.openapi()
    referencias = re.compile(r'"#/components/schemas/([^"]+)"')
    texto = json.dumps(
        [
            operacion
            for metodos in api["paths"].values()
            for operacion in metodos.values()
            if "ruteo-v2" in operacion.get("tags", [])
        ]
    )
    usados, nuevos = set(), set(referencias.findall(texto))
    while nuevos:
        usados |= nuevos
        texto = json.dumps([api["components"]["schemas"][nombre] for nombre in nuevos])
        nuevos = set(referencias.findall(texto)) - usados

    assert {"ArtefactoRedVialRespuesta", "PaginaRutasV2", "PaginaParadasV2"} <= usados
    assert not [nombre for nombre in usados if "__" in nombre]
    de_v1 = {"ArtefactoRespuesta", "PaginaRutas", "PaginaParadas"}
    assert de_v1 <= set(api["components"]["schemas"])


def test_sin_clave_no_responde_nada_de_ruteo_v2(cliente, publicado):
    for ruta in (
        "/ruteo-v2/redes",
        f"/ruteo-v2/ejecuciones/{publicado.ruteo_run_id}",
        f"/ruteo-v2/ejecuciones/{publicado.ruteo_run_id}/paradas",
    ):
        respuesta = cliente.get(ruta, headers={"X-API-Key": ""})
        assert respuesta.status_code == 401, ruta
    respuesta = cliente.post(
        "/ruteo-v2/ejecuciones", json=publicado.entrada, headers={"X-API-Key": ""}
    )
    assert respuesta.status_code == 401


# --- al final: la unica prueba que escribe --------------------------------------------------------


def test_las_mismas_entradas_mientras_se_rutean_responden_409_en_proceso(cliente, publicado):
    otra_red = red_de_pruebas("Otra red de pruebas")
    entrada = {**publicado.entrada, "red_vial_version_id": str(otra_red.red_vial_version_id)}

    primera = cliente.post("/ruteo-v2/ejecuciones", json=entrada)
    segunda = cliente.post("/ruteo-v2/ejecuciones", json=entrada)

    assert primera.status_code == 201 and primera.json()["estado"] == "EN_PROCESO"
    assert (segunda.status_code, segunda.json()["codigo"]) == (409, "RUTEO_V2_EN_PROCESO")
    en_proceso = _get(cliente, "/ruteo-v2/ejecuciones?estado=EN_PROCESO")
    assert [x["ruteo_run_id"] for x in en_proceso["elementos"]] == [primera.json()["ruteo_run_id"]]
    assert primera.json()["firma_inputs"] != publicado.ejecucion["firma_inputs"]
