"""atribucion/v1 sin base: que gestion es candidata de un pago, cada clasificacion y lo que la
explica. Ninguna prueba depende del reloj: los instantes son explicitos."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from motor_cartera.atribucion.reglas import (
    Atribucion,
    Candidata,
    Clasificacion,
    GestionLeida,
    MovimientoAtribuible,
    atribuir,
    elegible,
    en_la_ventana,
)
from motor_cartera.lifecycle.reglas import NivelContacto

MX = timezone(timedelta(hours=-6))
TITULAR, TERCERO = NivelContacto.CONTACTO_TITULAR, NivelContacto.CONTACTO_TERCERO
SIN_CONTACTO, NO_APLICA = NivelContacto.SIN_CONTACTO, NivelContacto.NO_APLICA


def dia(numero: int, hora: int = 10, minuto: int = 0, segundo: int = 0) -> datetime:
    """Un instante de septiembre de 2026 en la hora local de la fuente."""
    return datetime(2026, 9, 1, hora, minuto, segundo, tzinfo=MX) + timedelta(days=numero - 1)


def pago(numero: int = 1, *, cuenta: int | None = 1, cuando=None, **otros) -> MovimientoAtribuible:
    return MovimientoAtribuible(
        movimiento_id=UUID(int=1000 + numero),
        cuenta=cuenta,
        instante=cuando or dia(10),
        monto=Decimal("1000.00"),
        **otros,
    )


def gestion(numero: int, cuando: datetime, nivel=TITULAR, *, cuenta: int = 1, anulada=False):
    return GestionLeida(UUID(int=numero), cuenta, cuando, nivel, anulada)


def una(movimiento, *gestiones, ventana_dias: int = 30) -> Atribucion:
    (atribucion,) = atribuir([movimiento], gestiones, ventana_dias)
    return atribucion


# --- la ventana -----------------------------------------------------------------------------------


def test_la_ventana_incluye_el_instante_del_pago_y_sus_dias_hacia_atras_exactos():
    movimiento = pago()

    assert en_la_ventana(gestion(1, dia(10)), movimiento, 30)  # en el mismo instante
    assert en_la_ventana(gestion(1, dia(10) - timedelta(days=30)), movimiento, 30)
    assert not en_la_ventana(gestion(1, dia(10) - timedelta(days=30, seconds=1)), movimiento, 30)
    assert not en_la_ventana(gestion(1, dia(10, segundo=1)), movimiento, 30)  # despues del pago
    assert en_la_ventana(gestion(1, dia(9)), movimiento, 1)
    assert not en_la_ventana(gestion(1, dia(8)), movimiento, 1)


def test_la_ventana_compara_instantes_y_no_horas_locales():
    # Las 9:00 en UTC-5 son las 8:00 en UTC-6: antes de un pago de las 8:30 en UTC-6.
    movimiento = pago(cuando=dia(10, 8, 30))
    otra_zona = datetime(2026, 9, 10, 9, 0, tzinfo=timezone(timedelta(hours=-5)))

    assert en_la_ventana(gestion(1, otra_zona), movimiento, 30)
    assert not en_la_ventana(gestion(1, dia(10, 8, 31)), movimiento, 30)


@pytest.mark.parametrize(
    ("nivel", "anulada", "esperado"),
    [
        (TITULAR, False, True),
        (TERCERO, False, True),
        (SIN_CONTACTO, False, False),
        (NO_APLICA, False, False),
        (TITULAR, True, False),
        (TERCERO, True, False),
    ],
)
def test_solo_es_elegible_una_gestion_vigente_con_contacto(nivel, anulada, esperado):
    assert elegible(gestion(1, dia(9), nivel, anulada=anulada)) is esperado


# --- cada clasificacion ---------------------------------------------------------------------------


def test_sin_gestiones_el_pago_queda_sin_candidata_y_dice_por_que():
    atribucion = una(pago())

    assert atribucion.clasificacion == Clasificacion.SIN_GESTION_CANDIDATA
    assert atribucion.candidatas == () and atribucion.gestion_id is None
    assert atribucion.motivos == (
        {
            "codigo": "SIN_GESTION_EN_LA_VENTANA",
            "ventana_dias": 30,
            "gestiones_sin_contacto": 0,
            "gestiones_anuladas": 0,
        },
    )


def test_una_sola_candidata_es_una_asociacion_unica_con_esa_gestion():
    atribucion = una(pago(), gestion(1, dia(8)))

    assert atribucion.clasificacion == Clasificacion.ASOCIACION_UNICA
    assert atribucion.gestion_id == UUID(int=1)
    assert atribucion.candidatas == (Candidata(UUID(int=1), 2 * 86_400),)
    assert atribucion.motivos == ({"codigo": "UNA_GESTION_CANDIDATA", "ventana_dias": 30},)


def test_dos_candidatas_dejan_el_pago_ambiguo_sin_elegir_la_mas_cercana():
    # El caso de la mision: pago el 10, gestiones elegibles el 7 y el 9.
    del_7, del_9 = gestion(9, dia(7)), gestion(7, dia(9), TERCERO)

    atribucion = una(pago(), del_7, del_9)

    assert atribucion.clasificacion == Clasificacion.AMBIGUA
    assert atribucion.gestion_id is None  # ni la ultima, ni la primera, ni la mas cercana
    # En orden de gestion_id, que no significa nada: ni el del tiempo ni el de la cercania.
    assert atribucion.candidatas == (
        Candidata(UUID(int=7), 86_400),
        Candidata(UUID(int=9), 3 * 86_400),
    )
    assert atribucion.motivos == (
        {"codigo": "VARIAS_GESTIONES_CANDIDATAS", "candidatas": 2, "ventana_dias": 30},
    )


def test_el_orden_de_las_gestiones_no_cambia_la_conclusion():
    gestiones = [gestion(n, dia(n)) for n in range(1, 6)]

    adelante = una(pago(), *gestiones)
    al_reves = una(pago(), *reversed(gestiones))

    assert adelante == al_reves
    assert adelante.clasificacion == Clasificacion.AMBIGUA and len(adelante.candidatas) == 5


def test_las_gestiones_sin_contacto_y_las_anuladas_no_son_candidatas_y_se_cuentan():
    atribucion = una(
        pago(),
        gestion(1, dia(8), SIN_CONTACTO),
        gestion(2, dia(8), NO_APLICA),
        gestion(3, dia(9), anulada=True),
        gestion(4, dia(9), SIN_CONTACTO, anulada=True),
        gestion(5, dia(1) - timedelta(days=40), SIN_CONTACTO),  # fuera de la ventana: no cuenta
    )

    assert atribucion.clasificacion == Clasificacion.SIN_GESTION_CANDIDATA
    assert atribucion.motivos[0] == {
        "codigo": "SIN_GESTION_EN_LA_VENTANA",
        "ventana_dias": 30,
        "gestiones_sin_contacto": 2,
        "gestiones_anuladas": 2,
    }


def test_una_anulada_no_cuenta_y_la_otra_queda_como_asociacion_unica():
    atribucion = una(pago(), gestion(1, dia(8), anulada=True), gestion(2, dia(9)))

    assert atribucion.clasificacion == Clasificacion.ASOCIACION_UNICA
    assert atribucion.gestion_id == UUID(int=2)


def test_las_gestiones_de_otra_cuenta_o_posteriores_al_pago_no_son_candidatas():
    atribucion = una(
        pago(),
        gestion(1, dia(8), cuenta=2),
        gestion(2, dia(11)),
        gestion(3, dia(10, segundo=1)),
    )

    assert atribucion.clasificacion == Clasificacion.SIN_GESTION_CANDIDATA


def test_un_pago_sin_cuenta_no_tiene_candidatas():
    atribucion = una(pago(cuenta=None), gestion(1, dia(8)))

    assert atribucion.clasificacion == Clasificacion.SIN_GESTION_CANDIDATA
    assert atribucion.motivos == ({"codigo": "MOVIMIENTO_SIN_CUENTA"},)


def test_un_pago_anulado_por_un_reverso_se_clasifica_igual_y_lo_dice():
    reverso = UUID(int=77)

    atribucion = una(pago(anulado_por=reverso), gestion(1, dia(8)))

    assert atribucion.clasificacion == Clasificacion.ASOCIACION_UNICA
    assert atribucion.motivos == (
        {"codigo": "UNA_GESTION_CANDIDATA", "ventana_dias": 30},
        {"codigo": "ANULADO_POR_REVERSO", "reverso": str(reverso)},
    )


def test_la_ventana_es_un_parametro_y_cambia_la_conclusion():
    gestiones = (gestion(1, dia(1)), gestion(2, dia(9)))

    assert una(pago(), *gestiones, ventana_dias=30).clasificacion == Clasificacion.AMBIGUA
    estrecha = una(pago(), *gestiones, ventana_dias=5)
    assert (estrecha.clasificacion, estrecha.gestion_id) == (
        Clasificacion.ASOCIACION_UNICA,
        UUID(int=2),
    )
    assert estrecha.motivos[0]["ventana_dias"] == 5


def test_cada_pago_se_atribuye_por_separado_y_en_el_orden_en_que_llega():
    pagos = [pago(1, cuando=dia(10)), pago(2, cuando=dia(20)), pago(3, cuenta=2, cuando=dia(10))]

    atribuciones = atribuir(pagos, [gestion(1, dia(9)), gestion(2, dia(15))], 30)

    assert [a.movimiento_id for a in atribuciones] == [p.movimiento_id for p in pagos]
    assert [a.clasificacion for a in atribuciones] == [
        Clasificacion.ASOCIACION_UNICA,  # solo la del 9 lo antecede
        Clasificacion.AMBIGUA,  # la del 9 y la del 15
        Clasificacion.SIN_GESTION_CANDIDATA,  # otra cuenta
    ]


# --- sin base -------------------------------------------------------------------------------------

IMPORTAR_EN_LIMPIO = """
import importlib, json, sys

antes = set(sys.modules)
importlib.import_module(sys.argv[1])
print(json.dumps(sorted(set(sys.modules) - antes)))
"""


def test_las_reglas_solo_cargan_la_biblioteca_estandar():
    # En un proceso aparte: el de pytest ya tiene cargado todo lo demas.
    proceso = subprocess.run(
        [sys.executable, "-I", "-c", IMPORTAR_EN_LIMPIO, "motor_cartera.atribucion.reglas"],
        capture_output=True,
        text=True,
        check=True,
    )
    cargados = set(json.loads(proceso.stdout))
    raices = {nombre.partition(".")[0] for nombre in cargados}

    assert raices - sys.stdlib_module_names == {"motor_cartera"}
    assert {n for n in cargados if n.startswith("motor_cartera")} == {
        "motor_cartera",
        "motor_cartera.atribucion",
        "motor_cartera.atribucion.reglas",
        "motor_cartera.lifecycle",
        "motor_cartera.lifecycle.reglas",
    }
