"""GET /cartera/resumen."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from motor_cartera.generador.sintetico import generar_archivo, generar_cartera
from motor_cartera.segmentacion import TRAMOS_ATRASO, tramo_de_atraso

pytestmark = pytest.mark.usefixtures("bd")

CORTE = date(2026, 9, 30)


def _publicar(cliente, tmp_path, nombre="cartera.csv", n=300, tasa=0.0, semilla=1, corte=CORTE):
    ruta = generar_archivo(
        tmp_path / nombre, n=n, tasa_invalidas=tasa, semilla=semilla, fecha_corte=corte
    )
    respuesta = cliente.post("/corridas", files={"archivo": (nombre, ruta.read_bytes())})
    return respuesta.json()["run_id"]


def _resumen(cliente, **params):
    return cliente.get("/cartera/resumen", params={"por_pagina": 500, **params})


def test_sin_cartera_publicada_404_y_no_un_resumen_vacio(cliente):
    respuesta = cliente.get("/cartera/resumen")

    assert respuesta.status_code == 404
    assert respuesta.json()["codigo"] == "SIN_CARTERA_PUBLICADA"


def test_los_numeros_cuadran_contra_la_cartera_generada(cliente, tmp_path):
    run_id = _publicar(cliente, tmp_path)
    # La verdad, calculada por otro camino: pandas sobre la misma cartera, en Decimal.
    cartera = generar_cartera(300, semilla=1, fecha_corte=CORTE)
    cartera["tramo"] = cartera["dias_atraso"].map(tramo_de_atraso)
    cartera["saldo"] = cartera["saldo_total"].map(lambda saldo: Decimal(str(saldo)))
    grupos = cartera.groupby(["canal", "tramo"])["saldo"]
    esperado = {clave: (len(saldos), sum(saldos)) for clave, saldos in grupos}

    resumen = _resumen(cliente).json()

    assert resumen["run_id"] == run_id
    assert resumen["fecha_corte"] == "2026-09-30"
    assert resumen["dimensiones"] == ["canal", "tramo_atraso"]
    assert resumen["total_cuentas"] == 300
    assert Decimal(resumen["saldo_total"]) == sum(cartera["saldo"])
    assert resumen["total"] == len(esperado)
    obtenido = {
        (e["segmento"]["canal"], e["segmento"]["tramo_atraso"]): (
            e["cuentas"],
            Decimal(e["saldo_total"]),
        )
        for e in resumen["elementos"]
    }
    assert obtenido == esperado


def test_los_tramos_salen_en_orden_de_atraso_no_alfabetico(cliente, tmp_path):
    _publicar(cliente, tmp_path, n=2_000)

    resumen = _resumen(cliente, por="tramo_atraso").json()

    assert [e["segmento"]["tramo_atraso"] for e in resumen["elementos"]] == [
        etiqueta for etiqueta, _, _ in TRAMOS_ATRASO
    ]


def test_los_saldos_viajan_como_texto_decimal(cliente, tmp_path):
    _publicar(cliente, tmp_path)

    segmento = _resumen(cliente, por="producto").json()["elementos"][0]

    assert isinstance(segmento["saldo_total"], str)
    assert Decimal(segmento["saldo_promedio"]) == (
        Decimal(segmento["saldo_total"]) / segmento["cuentas"]
    ).quantize(Decimal("0.01"))


def test_filtrar_por_canal_resume_solo_esas_cuentas(cliente, tmp_path):
    _publicar(cliente, tmp_path)
    cartera = generar_cartera(300, semilla=1, fecha_corte=CORTE)

    resumen = _resumen(cliente, canal="CAMPO", por="producto").json()

    assert resumen["total_cuentas"] == (cartera["canal"] == "CAMPO").sum()


def test_los_segmentos_se_paginan(cliente, tmp_path):
    _publicar(cliente, tmp_path, n=2_000)

    paginas = [
        _resumen(cliente, por="cve_entidad", pagina=p, por_pagina=2).json() for p in (1, 2, 3)
    ]

    assert [len(p["elementos"]) for p in paginas] == [2, 2, 1]
    assert {p["total"] for p in paginas} == {5}
    entidades = [e["segmento"]["cve_entidad"] for p in paginas for e in p["elementos"]]
    assert entidades == ["09", "15", "21", "29", "30"]


# --- que cartera se resume --------------------------------------------------------------------


def test_la_vigente_es_la_del_corte_mas_reciente_no_la_ultima_subida(cliente, tmp_path):
    hoy = _publicar(cliente, tmp_path, "hoy.csv", corte=CORTE)
    _publicar(cliente, tmp_path, "semana_pasada.csv", semilla=2, corte=date(2026, 9, 23))

    assert _resumen(cliente).json()["run_id"] == hoy


def test_fijar_el_run_id_evita_mezclar_carteras_al_paginar(cliente, tmp_path):
    primera = _publicar(cliente, tmp_path, "a.csv", corte=date(2026, 9, 29))
    pagina_1 = _resumen(cliente, por_pagina=2).json()
    _publicar(cliente, tmp_path, "b.csv", semilla=2, corte=CORTE)  # se publica otra en medio

    pagina_2 = _resumen(cliente, run_id=pagina_1["run_id"], pagina=2, por_pagina=2).json()

    assert pagina_1["run_id"] == pagina_2["run_id"] == primera
    assert _resumen(cliente).json()["run_id"] != primera


def test_resumir_una_corrida_que_no_publico_409(cliente, tmp_path):
    rechazada = _publicar(cliente, tmp_path, tasa=0.5)

    respuesta = _resumen(cliente, run_id=rechazada)

    assert respuesta.status_code == 409
    assert respuesta.json()["codigo"] == "CORRIDA_NO_PUBLICADA"
    assert respuesta.json()["run_id"] == rechazada


def test_resumir_una_corrida_que_no_existe_404(cliente):
    respuesta = _resumen(cliente, run_id=str(uuid4()))

    assert respuesta.status_code == 404
    assert respuesta.json()["codigo"] == "CORRIDA_NO_ENCONTRADA"


# --- la entrada se valida con los catalogos del contrato ----------------------------------------


def test_un_producto_que_el_contrato_no_conoce_422(cliente):
    respuesta = cliente.get("/cartera/resumen", params={"producto": "HIPOTECARIO"})

    assert respuesta.status_code == 422
    assert respuesta.json()["detalles"] == [
        {
            "campo": "query.producto",
            "problema": "Debe ser uno de: 'CONSUMO', 'TARJETA', 'NOMINA' o 'AUTOMOTRIZ'.",
        }
    ]


@pytest.mark.parametrize(
    ("por", "problema"),
    [
        (["municipio"], "Debe ser uno de"),
        (["canal", "canal"], "Una dimension no puede repetirse."),
    ],
)
def test_dimensiones_invalidas_422(cliente, por, problema):
    respuesta = cliente.get("/cartera/resumen", params={"por": por})

    assert respuesta.status_code == 422
    assert respuesta.json()["detalles"][0]["problema"].startswith(problema)


def test_el_resumen_pide_api_key(cliente):
    respuesta = cliente.get("/cartera/resumen", headers={"X-API-Key": ""})

    assert respuesta.status_code == 401
