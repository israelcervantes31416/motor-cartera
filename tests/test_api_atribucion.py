"""La atribucion por HTTP: pedirla, leer cada ejecucion, lo que concluyo de cada pago con sus
candidatas, la historia de un movimiento, los pagos de una cuenta y la ultima atribucion en Cuenta
360. Siempre dice que es asociacion operacional, no causalidad."""

from __future__ import annotations

from uuid import uuid4

import pytest
from atribucion_escenarios import escenario
from historia_escenarios import cuenta
from lifecycle_escenarios import registrar
from sqlalchemy import text

from motor_cartera.db.sesion import sesion

pytestmark = pytest.mark.usefixtures("bd")


def _movimiento(numero: int) -> str:
    with sesion() as s:
        return str(
            s.execute(
                text(
                    "SELECT movimiento_id FROM movimiento_economico_canonico "
                    "WHERE cliente_unico = :cliente AND tipo_movimiento = 'PAGO' "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"cliente": cuenta(numero).cliente_unico},
            ).scalar_one()
        )


def test_la_api_pide_una_atribucion_y_la_muestra_pago_por_pago(tmp_path, cliente, trabajar):
    g = escenario(tmp_path)

    pedida = cliente.post("/atribuciones", json={"periodo": "2026-09"})
    otra = cliente.post("/atribuciones", json={"periodo": "2026-09", "ventana_dias": 10})
    sin_pagos = cliente.post("/atribuciones", json={"periodo": "2026-10"})

    assert pedida.status_code == 201, pedida.text
    cuerpo = pedida.json()
    assert (cuerpo["estado"], cuerpo["periodo"], cuerpo["ventana_dias"]) == (
        "EN_PROCESO",
        "2026-09",
        30,
    )
    assert cuerpo["zona_horaria"] == "America/Mexico_City"
    assert (cuerpo["motor_pagos_run_id"], cuerpo["interpretacion_de_pagos_vigente"]) == (None, None)
    assert pedida.headers["location"] == f"/atribuciones/{cuerpo['atribucion_run_id']}"
    assert (otra.status_code, otra.json()["codigo"]) == (409, "ATRIBUCION_EN_PROCESO")
    assert (sin_pagos.status_code, sin_pagos.json()["codigo"]) == (
        409,
        "SIN_INTERPRETACION_DE_PAGOS",
    )

    trabajar()
    terminada = cliente.get(pedida.headers["location"]).json()

    assert (terminada["estado"], terminada["resultado"], terminada["vigente"]) == (
        "EXITOSA",
        "ATRIBUCION_PUBLICADA",
        True,
    )
    assert terminada["conteos"] == {
        "movimientos_evaluados": 8,
        "asociados": 2,
        "ambiguos": 1,
        "sin_candidato": 5,
        "candidatos": 4,
        "movimientos_anulados": 1,
        "gestiones_leidas": 6,
        "gestiones_anuladas": 1,
    }
    assert terminada["montos"] == {
        "asociado": "1000.00",
        "ambiguo": "500.00",
        "sin_candidato": "720.00",
        "anulado": "400.00",
    }
    assert terminada["motor_pagos_run_id"] and terminada["interpretacion_de_pagos_vigente"]
    assert terminada["duracion_segundos"] is not None and terminada["trabajo_id"]
    assert "no causalidad" in terminada["aviso"]

    resultados = cliente.get(f"{pedida.headers['location']}/resultados").json()
    ambiguos = cliente.get(
        f"{pedida.headers['location']}/resultados", params={"clasificacion": "AMBIGUA"}
    ).json()
    de_la_1 = cliente.get(
        f"{pedida.headers['location']}/resultados",
        params={"cliente_unico": cuenta(1).cliente_unico},
    ).json()

    assert resultados["total"] == 8 and len(resultados["elementos"]) == 8
    (ambiguo,) = ambiguos["elementos"]
    assert (ambiguo["clasificacion"], ambiguo["gestion_id"]) == ("AMBIGUA", None)
    # De la mas proxima al pago a la mas lejana: un orden para leerlas, no una eleccion.
    assert [
        (c["gestion_id"], c["nivel_contacto"], c["antelacion_segundos"])
        for c in ambiguo["candidatas"]
    ] == [
        (str(g["2_titular"]), "CONTACTO_TITULAR", 86_400),
        (str(g["2_tercero"]), "CONTACTO_TERCERO", 3 * 86_400),
    ]
    assert ambiguo["motivos"] == [
        {"codigo": "VARIAS_GESTIONES_CANDIDATAS", "candidatas": 2, "ventana_dias": 30}
    ]
    (asociado,) = de_la_1["elementos"]
    assert (asociado["clasificacion"], asociado["gestion_id"]) == ("ASOCIACION_UNICA", str(g["1"]))
    assert asociado["cuenta_id"] == str(cuenta(1).cuenta_id)
    assert (asociado["monto"], asociado["anulado_por_reverso"]) == ("1000.00", False)
    assert "no causalidad" in resultados["aviso"]

    lista = cliente.get("/atribuciones", params={"periodo": "2026-09"}).json()
    fallidas = cliente.get("/atribuciones", params={"estado": "FALLIDA"}).json()
    assert (lista["total"], lista["version_atribucion"]) == (1, "atribucion/v1")
    assert fallidas["total"] == 0


def test_un_movimiento_y_una_cuenta_muestran_su_atribucion_vigente(tmp_path, cliente, trabajar):
    g = escenario(tmp_path)
    cliente.post("/atribuciones", json={"periodo": "2026-09"})
    trabajar()
    cuenta_2, cuenta_6 = cuenta(2), cuenta(6)

    del_movimiento = cliente.get(f"/movimientos/{_movimiento(1)}/atribuciones").json()
    de_la_cuenta = cliente.get(f"/cuentas/{cuenta_2.cuenta_id}/atribuciones").json()
    con_reverso = cliente.get(f"/cuentas/{cuenta_6.cuenta_id}/atribuciones").json()
    cuenta_360 = cliente.get(f"/cuentas/{cuenta(1).cuenta_id}").json()

    assert del_movimiento["movimiento"]["movimiento_id"] == _movimiento(1)
    (unica,) = del_movimiento["elementos"]
    assert (unica["vigente"], unica["clasificacion"], unica["gestion_id"]) == (
        True,
        "ASOCIACION_UNICA",
        str(g["1"]),
    )
    assert (de_la_cuenta["total"], de_la_cuenta["cliente_unico"]) == (1, cuenta_2.cliente_unico)
    (pago_2,) = de_la_cuenta["elementos"]
    assert pago_2["movimiento"]["tipo_movimiento"] == "PAGO"
    assert pago_2["atribucion"]["clasificacion"] == "AMBIGUA"
    # Solo los PAGO se atribuyen: el reverso de la 6 no esta, y su pago dice que lo anulo.
    (pago_6,) = con_reverso["elementos"]
    assert pago_6["atribucion"]["anulado_por_reverso"] is True
    assert pago_6["movimiento"]["anulado_por_movimiento_id"] is not None
    ultima = cuenta_360["lifecycle_resumen"]["ultima_atribucion"]
    assert (ultima["clasificacion"], ultima["gestion_id"], ultima["candidatas"]) == (
        "ASOCIACION_UNICA",
        str(g["1"]),
        1,
    )
    assert ultima["movimiento_id"] == _movimiento(1) and ultima["ventana_dias"] == 30
    sin_atribuir = cliente.get(f"/cuentas/{cuenta(5).cuenta_id}?al=2026-09-01").json()
    assert sin_atribuir["lifecycle_resumen"]["ultima_atribucion"] is None


def test_una_gestion_tardia_por_la_api_cambia_la_vigente_y_conserva_la_anterior(
    tmp_path, cliente, trabajar
):
    escenario(tmp_path)
    primera = cliente.post("/atribuciones", json={"periodo": "2026-09"}).json()
    trabajar()

    tardia = registrar(
        cliente,
        cuenta(5).cuenta_id,
        ocurrido_en="2026-09-08T12:00:00-06:00",
        resultado="CONTACTO",
    )
    segunda = cliente.post("/atribuciones", json={"periodo": "2026-09"}).json()
    trabajar()
    repetida = cliente.post("/atribuciones", json={"periodo": "2026-09"}).json()
    trabajar()

    assert tardia.status_code == 201, tardia.text
    historia = cliente.get(f"/movimientos/{_movimiento(5)}/atribuciones").json()
    assert [
        (a["atribucion_run_id"], a["vigente"], a["clasificacion"]) for a in historia["elementos"]
    ] == [
        (segunda["atribucion_run_id"], True, "ASOCIACION_UNICA"),
        (primera["atribucion_run_id"], False, "SIN_GESTION_CANDIDATA"),
    ]
    assert historia["elementos"][0]["gestion_id"] == tardia.json()["gestion_id"]
    # La misma ventana con las mismas entradas no se publica otra vez: la vigente sigue siendo la
    # segunda.
    sin_cambios = cliente.get(f"/atribuciones/{repetida['atribucion_run_id']}").json()
    assert (sin_cambios["estado"], sin_cambios["resultado"], sin_cambios["vigente"]) == (
        "FALLIDA",
        "YA_ATRIBUIDA",
        False,
    )
    lista = cliente.get("/atribuciones").json()
    assert [(a["atribucion_run_id"], a["vigente"]) for a in lista["elementos"]] == [
        (repetida["atribucion_run_id"], False),
        (segunda["atribucion_run_id"], True),
        (primera["atribucion_run_id"], False),
    ]


@pytest.mark.parametrize(
    "cuerpo",
    [
        {},
        {"periodo": "2026-13"},
        {"periodo": "2026-9"},
        {"periodo": "2026-09", "ventana_dias": 0},
        {"periodo": "2026-09", "ventana_dias": 367},
        {"periodo": "2026-09", "causalidad": True},
    ],
)
def test_una_peticion_que_no_cumple_el_contrato_es_un_422(cliente, cuerpo):
    respuesta = cliente.post("/atribuciones", json=cuerpo)

    assert (respuesta.status_code, respuesta.json()["codigo"]) == (422, "ENTRADA_INVALIDA")


def test_lo_que_no_existe_es_un_404_y_lo_que_no_es_un_uuid_un_422(cliente):
    otro = uuid4()

    assert cliente.get(f"/atribuciones/{otro}").json()["codigo"] == "ATRIBUCION_NO_ENCONTRADA"
    assert cliente.get(f"/atribuciones/{otro}/resultados").status_code == 404
    assert (
        cliente.get(f"/movimientos/{otro}/atribuciones").json()["codigo"]
        == "MOVIMIENTO_NO_ENCONTRADO"
    )
    assert cliente.get(f"/cuentas/{otro}/atribuciones").json()["codigo"] == "CUENTA_NO_ENCONTRADA"
    assert cliente.get("/atribuciones/no-es-un-uuid").status_code == 422
    assert cliente.get("/atribuciones", params={"periodo": "septiembre"}).status_code == 422


def test_las_rutas_de_la_atribucion_exigen_la_clave(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as sin_clave:
        for metodo, ruta in (
            ("post", "/atribuciones"),
            ("get", "/atribuciones"),
            ("get", f"/atribuciones/{uuid4()}"),
            ("get", f"/atribuciones/{uuid4()}/resultados"),
            ("get", f"/movimientos/{uuid4()}/atribuciones"),
            ("get", f"/cuentas/{uuid4()}/atribuciones"),
        ):
            respuesta = getattr(sin_clave, metodo)(ruta)
            assert (respuesta.status_code, respuesta.json()["codigo"]) == (
                401,
                "API_KEY_AUSENTE",
            ), ruta
