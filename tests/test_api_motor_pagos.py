"""El motor de pagos por HTTP: sus ejecuciones, sus movimientos, su evidencia y la Cuenta 360 que
distingue lo observado de lo interpretado."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from historia_escenarios import Cuenta, cliente, cuenta, ingerir_corte, ingerir_pagos_de, pago
from motor_pagos_escenarios import (
    ejecuciones,
    historiar_todo,
    interpretar_todo,
    movimientos,
    publicar,
    resultados,
    vigente,
)

from motor_cartera.motor_pagos import consultas
from motor_cartera.motor_pagos.ejecuciones import interpretar

pytestmark = pytest.mark.usefixtures("bd")

SEPTIEMBRE = date(2026, 9, 1)
CORTES = [date(2026, 8, 26), date(2026, 9, 2), date(2026, 9, 9), date(2026, 9, 16)]


@pytest.fixture
def api(cliente):
    """El cliente de la API: en este modulo, `cliente` es el CLIENTE_UNICO de un numero."""
    return cliente


def _escenario(tmp_path) -> None:
    """Cuatro cortes semanales; la cuenta 2 falta en el tercero. Pagos de septiembre: uno normal de
    la 1, dos copias de la 6, una pareja de reverso de la 8, uno de la 2 mientras faltaba, uno de la
    4 antes de que aparezca, y uno de la 9, que no tiene cuenta."""
    presencia = {1: (0, 1, 2, 3), 2: (0, 1, 3), 4: (3,), 6: (0, 1, 2, 3), 8: (0, 1, 2, 3)}
    for i, corte in enumerate(CORTES):
        ingerir_corte(
            tmp_path,
            corte,
            [Cuenta(n, saldo=10_000 - 100 * i) for n, cortes in presencia.items() if i in cortes],
        )
    historiar_todo()
    copia = pago(6, "2026-09-08 09:15:00", "300.00")
    ingerir_pagos_de(
        tmp_path,
        "pagos.csv",
        [
            pago(1, "2026-09-03 10:00:00", "500.00"),
            copia,
            dict(copia),
            pago(8, "2026-09-04 10:00:00", "400.00"),
            pago(8, "2026-09-05 10:00:00", "-400.00"),
            pago(2, "2026-09-10 12:00:00", "250.00"),
            pago(4, "2026-09-11 12:00:00", "100.00"),
            pago(9, "2026-09-12 12:00:00", "320.00"),
        ],
    )
    historiar_todo()
    interpretar_todo()


def _movimiento_de(numero: int):
    (elemento,) = [
        m for m in movimientos(vigente(SEPTIEMBRE)) if m.cliente_unico == cliente(numero)
    ]
    return elemento


# --- las ejecuciones ------------------------------------------------------------------------------


def test_la_ejecucion_con_su_calidad_y_su_recuperacion(tmp_path, api):
    _escenario(tmp_path)
    ejecucion = vigente(SEPTIEMBRE)

    respuesta = api.get(f"/motor-pagos/{ejecucion.motor_pagos_run_id}")

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert (cuerpo["estado"], cuerpo["resultado"], cuerpo["vigente"]) == (
        "EXITOSA",
        "INTERPRETACION_PUBLICADA",
        True,
    )
    assert (cuerpo["periodo"], cuerpo["periodo_desde"], cuerpo["periodo_hasta"]) == (
        "2026-09",
        "2026-09-01",
        "2026-10-01",
    )
    assert cuerpo["version_motor"] == "motor-pagos/v1"
    assert cuerpo["calidad"] == {
        "observaciones_leidas": 8,
        "observaciones_contexto": 0,
        "observaciones_clasificadas": 8,
        "movimientos_canonicos": 7,
        "movimientos_primarios": 6,
        "duplicados_exactos": 1,
        "coincidencias_ambiguas": 0,
        "reversos": 1,
        "posibles_reversos": 0,
        "no_conciliados": 0,
        "sin_cuenta_observada": 1,
        "grupos_exactos": 1,
        "grupos_legacy": 1,
        "grupos_ambiguos": 0,
        "observaciones_en_grupos_legacy": 2,
        "pagos_anulados": 1,
    }
    # 500 + 300 + 250 + 100 + 320; los 400 de la 8 los anulo su reverso.
    assert cuerpo["recuperacion"]["bruta_interpretada"] == "1470.00"
    assert cuerpo["recuperacion"]["neta_interpretada"] == "1470.00"
    assert "no un saldo contable" in cuerpo["recuperacion"]["aviso"]
    assert cuerpo["trabajo_id"] is not None and cuerpo["duracion_segundos"] >= 0


def test_el_trabajo_de_una_ejecucion_dice_de_que_ejecucion_es(tmp_path, api):
    """GET /trabajos/{trabajo_id} de un trabajo MOTOR_PAGOS: su tipo y el motor_pagos_run_id de su
    ejecucion, sin flujo. La prueba de humo lo encontro: el identificador del objetivo salia de una
    lista de tablas en la que faltaba la del motor, y la respuesta era un 500."""
    _escenario(tmp_path)
    ejecucion = vigente(SEPTIEMBRE)
    cuerpo = api.get(f"/motor-pagos/{ejecucion.motor_pagos_run_id}").json()

    respuesta = api.get(f"/trabajos/{cuerpo['trabajo_id']}")

    assert respuesta.status_code == 200
    trabajo = respuesta.json()
    assert (trabajo["tipo"], trabajo["flujo_id"], trabajo["objetivo_run_id"]) == (
        "MOTOR_PAGOS",
        None,
        str(ejecucion.motor_pagos_run_id),
    )


def test_la_lista_de_ejecuciones_dice_cual_es_la_vigente(tmp_path, api):
    _escenario(tmp_path)
    anterior = vigente(SEPTIEMBRE)
    publicar(ingerir_pagos_de(tmp_path, "mas.csv", [pago(1, "2026-09-20 10:00:00", "50.00")]))

    respuesta = api.get("/motor-pagos", params={"periodo": "2026-09"})

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["total"] == 2
    assert [(e["vigente"], e["calidad"]["observaciones_leidas"]) for e in cuerpo["elementos"]] == [
        (True, 9),
        (False, 8),
    ]
    assert cuerpo["elementos"][1]["motor_pagos_run_id"] == str(anterior.motor_pagos_run_id)
    assert api.get("/motor-pagos", params={"periodo": "2026-08"}).json()["total"] == 0
    assert api.get("/motor-pagos", params={"estado": "FALLIDA"}).json()["total"] == 0
    malo = api.get("/motor-pagos", params={"periodo": "2026-13"})
    assert (malo.status_code, malo.json()["codigo"]) == (422, "ENTRADA_INVALIDA")


def test_una_ejecucion_que_no_existe_es_404(api):
    respuesta = api.get(f"/motor-pagos/{uuid4()}")

    assert (respuesta.status_code, respuesta.json()["codigo"]) == (
        404,
        "MOTOR_PAGOS_NO_ENCONTRADO",
    )


def test_los_resultados_de_una_ejecucion_explican_cada_observacion(tmp_path, api):
    _escenario(tmp_path)
    run_id = vigente(SEPTIEMBRE).motor_pagos_run_id

    todos = api.get(f"/motor-pagos/{run_id}/resultados").json()
    copias = api.get(
        f"/motor-pagos/{run_id}/resultados", params={"clasificacion": "DUPLICADO_EXACTO"}
    ).json()
    del_8 = api.get(
        f"/motor-pagos/{run_id}/resultados", params={"cliente_unico": cliente(8)}
    ).json()

    assert todos["total"] == 8
    assert [r["recuperacion_por_gestion"] for r in todos["elementos"]][:2] == ["500.00", "400.00"]
    (copia,) = copias["elementos"]
    assert copia["motivos"][0]["codigo"] == "COPIA_EXACTA"
    assert len(copia["firma_exacta"]) == len(copia["firma_legacy"]) == 64
    pago_8, reverso_8 = del_8["elementos"]
    assert (pago_8["clasificacion"], reverso_8["clasificacion"]) == (
        "MOVIMIENTO_PRIMARIO",
        "REVERSO",
    )
    assert reverso_8["movimiento_relacionado_id"] == pago_8["movimiento_id"]
    assert pago_8["motivos"][1] == {
        "codigo": "ANULADO_POR_REVERSO",
        "reverso": reverso_8["movimiento_id"],
        "ventana_dias": 30,
    }


def test_los_resultados_de_un_grupo_por_su_huella(tmp_path, api):
    _escenario(tmp_path)
    # Otra observacion de la 1 con su misma llave historica y otro gestor: un grupo ambiguo.
    publicar(
        ingerir_pagos_de(
            tmp_path, "mas.csv", [pago(1, "2026-09-03 10:00:00", "500.00", Gestor="OTRO")]
        )
    )
    ruta = f"/motor-pagos/{vigente(SEPTIEMBRE).motor_pagos_run_id}/resultados"
    (copia,) = api.get(ruta, params={"clasificacion": "DUPLICADO_EXACTO"}).json()["elementos"]
    ambigua = api.get(ruta, params={"clasificacion": "COINCIDENCIA_AMBIGUA"}).json()["elementos"][0]

    copias = api.get(ruta, params={"firma_exacta": copia["firma_exacta"]}).json()
    del_cliente = api.get(
        ruta, params={"firma_legacy": ambigua["firma_legacy"], "cliente_unico": cliente(1)}
    ).json()
    de_la_ventana = api.get(ruta, params={"firma_legacy": ambigua["firma_legacy"].upper()}).json()

    assert copias["total"] == 2
    assert sorted(r["clasificacion"] for r in copias["elementos"]) == [
        "DUPLICADO_EXACTO",
        "MOVIMIENTO_PRIMARIO",
    ]
    assert len({r["pago_observado_id"] for r in copias["elementos"]}) == 2
    assert del_cliente["total"] == de_la_ventana["total"] == 2
    assert del_cliente["elementos"] == de_la_ventana["elementos"]
    assert {r["clasificacion"] for r in del_cliente["elementos"]} == {"COINCIDENCIA_AMBIGUA"}
    assert len({r["firma_exacta"] for r in del_cliente["elementos"]}) == 2
    assert api.get(ruta, params={"firma_exacta": "abc"}).status_code == 422


# --- los movimientos ------------------------------------------------------------------------------


def test_los_movimientos_vigentes_con_sus_filtros(tmp_path, api):
    _escenario(tmp_path)

    todos = api.get("/movimientos").json()
    reversos = api.get("/movimientos", params={"tipo": "REVERSO"}).json()
    sin_cuenta = api.get("/movimientos", params={"estado_conciliacion": "SIN_CUENTA_OBSERVADA"})
    del_8 = api.get("/movimientos", params={"cliente_unico": cliente(8)}).json()
    por_cuenta = api.get("/movimientos", params={"cuenta_id": str(cuenta(8).cuenta_id)}).json()
    entre = api.get("/movimientos", params={"desde": "2026-09-05", "hasta": "2026-09-08"}).json()

    assert todos["total"] == 7
    assert "No son el libro contable del acreedor" in todos["aviso"]
    recepciones = [m["fecha_recepcion"] for m in todos["elementos"]]
    assert recepciones == sorted(recepciones, reverse=True)
    assert {m["vigente"] for m in todos["elementos"]} == {True}
    assert [m["cliente_unico"] for m in reversos["elementos"]] == [cliente(8)]
    assert [m["cliente_unico"] for m in sin_cuenta.json()["elementos"]] == [cliente(9)]
    assert sin_cuenta.json()["elementos"][0]["cuenta_id"] is None
    assert del_8 == por_cuenta
    assert del_8["total"] == 2
    # Del 5 al 8 inclusive: el reverso del 5 y las copias del 8, que son un solo movimiento.
    assert [m["observaciones"] for m in entre["elementos"]] == [2, 1]


def test_los_filtros_de_movimientos_que_no_cuadran(tmp_path, api):
    _escenario(tmp_path)

    otra = api.get(
        "/movimientos",
        params={"cuenta_id": str(cuenta(8).cuenta_id), "cliente_unico": cliente(1)},
    )
    sin_cuenta = api.get("/movimientos", params={"cuenta_id": str(uuid4())})
    malo = api.get("/movimientos", params={"tipo": "DEVOLUCION"})

    assert (otra.status_code, otra.json()["codigo"]) == (422, "ENTRADA_INVALIDA")
    assert (sin_cuenta.status_code, sin_cuenta.json()["codigo"]) == (404, "CUENTA_NO_ENCONTRADA")
    assert (malo.status_code, malo.json()["codigo"]) == (422, "ENTRADA_INVALIDA")


def test_un_movimiento_con_su_por_que_su_evidencia_y_su_contexto(tmp_path, api, almacen):
    _escenario(tmp_path)
    movimiento = _movimiento_de(6)

    respuesta = api.get(f"/movimientos/{movimiento.movimiento_id}")

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert (cuerpo["tipo_movimiento"], cuerpo["signo_economico"], cuerpo["monto_reportado"]) == (
        "PAGO",
        "SUMA",
        "300.00",
    )
    assert (cuerpo["clasificacion"], cuerpo["observaciones"], cuerpo["vigente"]) == (
        "MOVIMIENTO_PRIMARIO",
        2,
        True,
    )
    assert cuerpo["motivos"] == [{"codigo": "REPRESENTANTE_DE_COPIAS", "copias": 1}]
    assert cuerpo["cuenta_id"] == str(cuenta(6).cuenta_id)
    assert cuerpo["estado_conciliacion"] == "CONCILIADO_CUENTA"
    representante = cuerpo["representante"]
    assert representante["pago_observado"]["source_row"] == 3
    original = representante["artefacto_original"]
    assert original["nombre_original"] == "pagos.csv"
    assert almacen.existe(original["sha256"])
    # Pago del 8 de septiembre: entre el corte del 2 y el del 9, con la cuenta en los dos.
    contexto = cuerpo["contexto_temporal"]
    assert contexto["snapshot_anterior"]["fecha_corte"] == "2026-09-02"
    assert contexto["snapshot_siguiente"]["fecha_corte"] == "2026-09-09"
    assert (
        contexto["snapshot_anterior"]["saldo_total"]
        != contexto["snapshot_siguiente"]["saldo_total"]
    )
    assert not any(
        contexto[k]
        for k in (
            "antes_de_primera_observacion",
            "despues_de_ultima_observacion",
            "durante_ausencia_observada",
        )
    )


def test_las_observaciones_de_un_movimiento_explican_por_que_vale_una_vez(tmp_path, api):
    _escenario(tmp_path)
    movimiento = _movimiento_de(6)

    respuesta = api.get(f"/movimientos/{movimiento.movimiento_id}/observaciones")

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["total"] == 2
    representante, copia = cuerpo["elementos"]
    assert (representante["clasificacion"], copia["clasificacion"]) == (
        "MOVIMIENTO_PRIMARIO",
        "DUPLICADO_EXACTO",
    )
    assert (
        copia["motivos"][0]["representante"]
        == (representante["pago_observado"]["pago_observado_id"])
    )
    for elemento in cuerpo["elementos"]:
        observado = elemento["pago_observado"]
        assert observado["recuperacion_por_gestion"] == "300.00"
        assert observado["pagos_run_id"] and observado["dataset_id"]
        assert elemento["artefacto_original"]["formato"] == "csv"


def test_un_movimiento_que_no_existe_es_404(api):
    respuesta = api.get(f"/movimientos/{uuid4()}")
    observaciones = api.get(f"/movimientos/{uuid4()}/observaciones")

    assert (respuesta.status_code, respuesta.json()["codigo"]) == (404, "MOVIMIENTO_NO_ENCONTRADO")
    assert observaciones.status_code == 404


def test_un_movimiento_que_ya_no_es_vigente_se_ve_como_historia(tmp_path, api):
    _escenario(tmp_path)
    antes = _movimiento_de(1)
    # Otra observacion con la misma llave historica y otro gestor vuelve ambiguo el pago de la 1:
    # la interpretacion nueva ya no lo funda.
    publicar(
        ingerir_pagos_de(
            tmp_path, "mas.csv", [pago(1, "2026-09-03 10:00:00", "500.00", Gestor="OTRO")]
        )
    )

    respuesta = api.get(f"/movimientos/{antes.movimiento_id}")

    assert respuesta.status_code == 200
    assert respuesta.json()["vigente"] is False
    assert respuesta.json()["motor_pagos_run_id"] == str(
        ejecuciones(SEPTIEMBRE)[0].motor_pagos_run_id
    )
    assert api.get("/movimientos", params={"cliente_unico": cliente(1)}).json()["total"] == 0


# --- la Cuenta 360 --------------------------------------------------------------------------------


def test_la_cuenta_360_distingue_pagos_observados_de_movimientos(tmp_path, api):
    _escenario(tmp_path)
    cuenta_id = cuenta(6).cuenta_id

    observados = api.get(f"/cuentas/{cuenta_id}/pagos-observados").json()
    interpretados = api.get(f"/cuentas/{cuenta_id}/movimientos").json()
    resumen = api.get(f"/cuentas/{cuenta_id}").json()["resumen_pagos"]

    # La fuente lo reporto dos veces; el motor lo interpreta una.
    assert observados["total"] == 2 and "No estan deduplicados" in observados["aviso"]
    assert interpretados["total"] == 1
    assert interpretados["elementos"][0]["observaciones"] == 2
    assert "No son el libro contable" in interpretados["aviso"]
    assert resumen == {
        "version_motor": "motor-pagos/v1",
        "observaciones": 2,
        "observaciones_interpretadas": 2,
        "movimientos_canonicos": 1,
        "duplicados_exactos": 1,
        "coincidencias_ambiguas": 0,
        "reversos": 0,
        "posibles_reversos": 0,
        "no_conciliados": 0,
        "pagos_anulados": 0,
        "recuperacion_bruta_interpretada": "300.00",
        "recuperacion_neta_interpretada": "300.00",
        "aviso": resumen["aviso"],
    }


def test_el_contexto_temporal_de_los_movimientos_de_una_cuenta(tmp_path, api):
    _escenario(tmp_path)

    def contexto(numero: int) -> dict:
        cuerpo = api.get(f"/cuentas/{cuenta(numero).cuenta_id}/movimientos").json()
        return cuerpo["elementos"][0]["contexto_temporal"]

    # La 2 falta en el corte del 9: su pago del 10 llego durante su ausencia.
    ausente = contexto(2)
    assert ausente["durante_ausencia_observada"] is True
    assert ausente["snapshot_anterior"]["fecha_corte"] == "2026-09-02"
    assert ausente["snapshot_siguiente"]["fecha_corte"] == "2026-09-16"
    # La 4 aparece por primera vez el 16: su pago del 11 es de antes.
    antes = contexto(4)
    assert antes["antes_de_primera_observacion"] is True
    assert antes["snapshot_anterior"] is None
    # La 8: su reverso y su pago, con la cuenta en todos los cortes.
    cuerpo = api.get(f"/cuentas/{cuenta(8).cuenta_id}/movimientos").json()
    assert [m["tipo_movimiento"] for m in cuerpo["elementos"]] == ["REVERSO", "PAGO"]
    resumen_8 = api.get(f"/cuentas/{cuenta(8).cuenta_id}").json()["resumen_pagos"]
    assert (resumen_8["reversos"], resumen_8["pagos_anulados"]) == (1, 1)
    assert resumen_8["recuperacion_bruta_interpretada"] == "0.00"
    # Filtrar por fechas.
    solo_el_reverso = api.get(
        f"/cuentas/{cuenta(8).cuenta_id}/movimientos", params={"desde": "2026-09-05"}
    ).json()
    assert [m["tipo_movimiento"] for m in solo_el_reverso["elementos"]] == ["REVERSO"]


def test_el_resumen_de_pagos_en_una_fecha_y_de_una_cuenta_sin_pagos(tmp_path, api):
    _escenario(tmp_path)

    al = api.get(f"/cuentas/{cuenta(8).cuenta_id}", params={"al": "2026-09-04"}).json()
    sin_pagos = api.get(f"/cuentas/{cuenta(4).cuenta_id}", params={"al": "2026-09-01"}).json()

    # El 4 solo habia llegado el pago: su reverso es del 5, asi que ese dia no estaba anulado.
    # Las clases son las de la interpretacion de hoy.
    assert al["resumen_pagos"]["observaciones"] == 1
    assert (al["resumen_pagos"]["reversos"], al["resumen_pagos"]["pagos_anulados"]) == (0, 0)
    assert al["resumen_pagos"]["recuperacion_bruta_interpretada"] == "400.00"
    assert al["resumen_pagos"]["recuperacion_neta_interpretada"] == "400.00"
    hoy = api.get(f"/cuentas/{cuenta(8).cuenta_id}").json()["resumen_pagos"]
    assert (hoy["reversos"], hoy["pagos_anulados"]) == (1, 1)
    assert hoy["recuperacion_bruta_interpretada"] == hoy["recuperacion_neta_interpretada"] == "0.00"
    assert sin_pagos["resumen_pagos"]["observaciones"] == 0
    assert sin_pagos["resumen_pagos"]["movimientos_canonicos"] == 0


def test_la_respuesta_no_mezcla_dos_interpretaciones(tmp_path, api, monkeypatch):
    # Una interpretacion nueva se publica a media respuesta: la Cuenta 360 la ve entera o no la ve.
    _escenario(tmp_path)
    ingerir_pagos_de(tmp_path, "mas.csv", [pago(6, "2026-09-20 10:00:00", "700.00")])
    historiar_todo()
    (pendiente,) = [e for e in ejecuciones(SEPTIEMBRE) if e.estado == "EN_PROCESO"]
    vigentes = consultas.vigentes

    def publicar_en_medio(*args, **kwargs):
        if not getattr(publicar_en_medio, "hecho", False):
            publicar_en_medio.hecho = True
            interpretar(pendiente.id)
        return vigentes(*args, **kwargs)

    monkeypatch.setattr(consultas, "vigentes", publicar_en_medio)
    durante = api.get(f"/cuentas/{cuenta(6).cuenta_id}").json()["resumen_pagos"]
    monkeypatch.undo()
    despues = api.get(f"/cuentas/{cuenta(6).cuenta_id}").json()["resumen_pagos"]

    # Durante: las tres observaciones, dos interpretadas, y la interpretacion de antes, entera.
    assert (durante["observaciones"], durante["observaciones_interpretadas"]) == (3, 2)
    assert (durante["movimientos_canonicos"], durante["recuperacion_bruta_interpretada"]) == (
        1,
        "300.00",
    )
    assert (despues["observaciones_interpretadas"], despues["movimientos_canonicos"]) == (3, 2)
    assert despues["recuperacion_bruta_interpretada"] == "1000.00"


def test_las_rutas_del_motor_exigen_la_clave(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as sin_clave:
        for ruta in (
            "/motor-pagos",
            f"/motor-pagos/{uuid4()}",
            f"/motor-pagos/{uuid4()}/resultados",
            "/movimientos",
            f"/movimientos/{uuid4()}",
            f"/movimientos/{uuid4()}/observaciones",
            f"/cuentas/{uuid4()}/movimientos",
        ):
            respuesta = sin_clave.get(ruta)
            assert (respuesta.status_code, respuesta.json()["codigo"]) == (
                401,
                "API_KEY_AUSENTE",
            ), ruta


def test_los_resultados_de_una_ejecucion_en_proceso_estan_vacios(tmp_path, api):
    ingerir_pagos_de(tmp_path, "pagos.csv", [pago(1, "2026-09-03 10:00:00", "500.00")])
    historiar_todo()
    (abierta,) = ejecuciones()

    cuerpo = api.get(f"/motor-pagos/{abierta.motor_pagos_run_id}").json()
    vacios = api.get(f"/motor-pagos/{abierta.motor_pagos_run_id}/resultados").json()

    assert (cuerpo["estado"], cuerpo["resultado"], cuerpo["vigente"]) == ("EN_PROCESO", None, False)
    assert cuerpo["duracion_segundos"] is None and cuerpo["firma_entrada"] is None
    assert vacios["total"] == 0
    assert resultados(abierta) == []
