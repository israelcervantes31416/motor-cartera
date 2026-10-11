"""geografia/v1 como nucleo puro: su vocabulario, la regla de una coordenada, su espejo de la
clasificacion completa y la firma de una ejecucion. Sin base de datos."""

from __future__ import annotations

import math
from uuid import uuid4

import pytest

from motor_cartera.db.modelos import CONTEOS_DE_GEOGRAFIA, ESTADOS_GEOGRAFIA
from motor_cartera.geografia import reglas
from motor_cartera.geografia.reglas import (
    CONFIABLES,
    CONTEO_DE_ESTADO,
    Comparacion,
    EstadoGeografia,
    Motivo,
    clasificar,
    comparacion,
    coordenadas,
    firma_inputs,
    se_puede_invertir,
)

NAN, INF = math.nan, math.inf


def test_el_vocabulario_es_el_que_la_base_exige_y_cada_estado_tiene_su_conteo():
    assert tuple(e.value for e in EstadoGeografia) == ESTADOS_GEOGRAFIA
    assert set(CONTEO_DE_ESTADO) == set(EstadoGeografia)
    assert set(CONTEO_DE_ESTADO.values()) == set(CONTEOS_DE_GEOGRAFIA)
    # Pequeno y formal: siete estados, y solo uno es confiable.
    assert len(EstadoGeografia) == 7
    assert CONFIABLES == {EstadoGeografia.VALIDA}
    assert reglas.VERSION_MOTOR == "geografia/v1"
    assert reglas.PREDICADO_ESPACIAL == "ST_Covers"


@pytest.mark.parametrize(
    ("latitud", "longitud", "motivos"),
    [
        (None, None, ["SIN_LATITUD", "SIN_LONGITUD"]),
        (19.05, None, ["SIN_LONGITUD"]),
        (None, -98.2, ["SIN_LATITUD"]),
        (95.0, -98.2, ["LATITUD_FUERA_DE_RANGO"]),
        (-90.0001, -98.2, ["LATITUD_FUERA_DE_RANGO"]),
        (19.05, -200.0, ["LONGITUD_FUERA_DE_RANGO"]),
        (19.05, 180.0001, ["LONGITUD_FUERA_DE_RANGO"]),
        (NAN, -98.2, ["LATITUD_NO_FINITA"]),
        (19.05, INF, ["LONGITUD_NO_FINITA"]),
        (-INF, NAN, ["LATITUD_NO_FINITA", "LONGITUD_NO_FINITA"]),
        (95.0, -200.0, ["LATITUD_FUERA_DE_RANGO", "LONGITUD_FUERA_DE_RANGO"]),
        (0.0, 0.0, ["PUNTO_0_0"]),
        (19.05, -98.2, []),
        (90.0, 180.0, []),
        (0.0, -98.2, []),
        (19.05, 0.0, []),
    ],
)
def test_una_coordenada_se_descarta_solo_por_lo_que_dice_su_motivo(latitud, longitud, motivos):
    assert [m["codigo"] for m in coordenadas(latitud, longitud)] == motivos


def test_sin_coordenadas_e_incompletas_no_inventan_la_otra():
    sin = clasificar(None, None, municipios=[], cvegeo_fuente="21114", fuente_en_referencia=True)
    assert (sin.estado, sin.cvegeo, sin.coincide, sin.utilizable) == (
        EstadoGeografia.SIN_COORDENADAS,
        None,
        None,
        False,
    )
    incompleta = clasificar(
        19.05, None, municipios=[], cvegeo_fuente="21114", fuente_en_referencia=True
    )
    assert incompleta.estado == EstadoGeografia.COORDENADAS_INCOMPLETAS
    assert incompleta.motivos == [{"codigo": "SIN_LONGITUD"}]


@pytest.mark.parametrize(
    ("latitud", "longitud"), [(95.0, -98.2), (19.05, -200.0), (NAN, -98.2), (0.0, 0.0)]
)
def test_invalidas_no_tienen_punto_ni_municipio(latitud, longitud):
    c = clasificar(
        latitud, longitud, municipios=[], cvegeo_fuente="21114", fuente_en_referencia=True
    )
    assert (c.estado, c.cvegeo, c.coincide, c.utilizable) == (
        EstadoGeografia.COORDENADAS_INVALIDAS,
        None,
        None,
        False,
    )


def test_fuera_ambiguo_discrepante_y_valido():
    base = {"cvegeo_fuente": "21114", "fuente_en_referencia": True}
    fuera = clasificar(25.0, -105.0, municipios=[], **base)
    assert fuera.estado == EstadoGeografia.FUERA_DE_REFERENCIA
    assert fuera.motivos == [{"codigo": "SIN_MUNICIPIO_EN_REFERENCIA"}]
    assert fuera.utilizable

    ambiguo = clasificar(19.05, -98.1, municipios=["21114", "21015"], **base)
    assert (ambiguo.estado, ambiguo.cvegeo, ambiguo.coincide) == (
        EstadoGeografia.MUNICIPIO_AMBIGUO,
        None,
        None,
    )
    # No se elige ninguno: se nombran todos, en orden.
    assert ambiguo.motivos == [{"codigo": "VARIOS_MUNICIPIOS", "cvegeo": ["21015", "21114"]}]

    valida = clasificar(19.05, -98.2, municipios=["21114"], **base)
    assert (valida.estado, valida.cvegeo, valida.coincide) == (
        EstadoGeografia.VALIDA,
        "21114",
        True,
    )
    assert [m["codigo"] for m in valida.motivos] == ["MUNICIPIO_UNICO", "COINCIDE_CON_FUENTE"]

    discrepa = clasificar(
        19.05, -98.2, municipios=["21114"], cvegeo_fuente="21015", **{"fuente_en_referencia": False}
    )
    assert (discrepa.estado, discrepa.cvegeo, discrepa.coincide) == (
        EstadoGeografia.DISCREPANCIA_MUNICIPIO,
        "21114",
        False,
    )
    assert discrepa.motivos[-1] == {
        "codigo": "DISCREPA_DE_FUENTE",
        "cvegeo_fuente": "21015",
        "cvegeo_espacial": "21114",
        "fuente_en_referencia": False,
    }


def test_sin_municipio_de_la_fuente_no_hay_discrepancia_conocida_y_es_valida():
    c = clasificar(
        19.05, -98.2, municipios=["21114"], cvegeo_fuente=None, fuente_en_referencia=False
    )
    assert (c.estado, c.coincide) == (EstadoGeografia.VALIDA, None)
    assert comparacion(c.coincide) == Comparacion.NO_COMPARABLE
    assert c.motivos[-1] == {"codigo": "FUENTE_SIN_MUNICIPIO"}


def test_el_orden_invertido_es_un_motivo_y_nunca_una_correccion():
    base = {"cvegeo_fuente": "31050", "fuente_en_referencia": True}
    fuera = clasificar(-89.6, 20.95, municipios=[], invertido=["31050"], **base)
    assert fuera.estado == EstadoGeografia.FUERA_DE_REFERENCIA
    assert fuera.cvegeo is None
    assert fuera.motivos[-1] == {"codigo": "POSIBLE_ORDEN_INVERTIDO", "cvegeo": ["31050"]}

    invalida = clasificar(-98.2, 19.05, municipios=[], invertido=["21114"], **base)
    assert invalida.estado == EstadoGeografia.COORDENADAS_INVALIDAS
    assert [m["codigo"] for m in invalida.motivos] == [
        "LATITUD_FUERA_DE_RANGO",
        "POSIBLE_ORDEN_INVERTIDO",
    ]
    # A una que ya cae en un municipio no se le agrega.
    valida = clasificar(19.05, -98.2, municipios=["21114"], invertido=["99999"], **base)
    assert Motivo.POSIBLE_ORDEN_INVERTIDO not in [m["codigo"] for m in valida.motivos]


@pytest.mark.parametrize(
    ("latitud", "longitud", "se_puede"),
    [
        (-89.6, 20.95, True),
        (-98.2, 19.05, True),
        (19.05, -98.2, False),  # la invertida tendria latitud -98.2
        (0.0, 0.0, False),
        (None, 19.05, False),
        (NAN, 19.05, False),
        (-200.0, 19.0, False),
    ],
)
def test_solo_se_prueba_el_punto_invertido_cuando_es_un_punto_valido(latitud, longitud, se_puede):
    assert se_puede_invertir(latitud, longitud) is se_puede


def test_la_firma_cambia_con_cada_entrada_y_no_con_otra_cosa():
    entradas = {
        "geografia_version_id": uuid4(),
        "sha256_referencia": "a" * 64,
        "capa": "conjunto_de_datos/00mun",
        "corte_id": uuid4(),
        "firma_corte": "b" * 64,
        "dataset_id": uuid4(),
        "firma_dataset": "c" * 64,
    }
    firma = firma_inputs(**entradas)
    assert firma == firma_inputs(**entradas)
    assert len(firma) == 64
    for llave, otro in (
        ("geografia_version_id", uuid4()),
        ("sha256_referencia", "d" * 64),
        ("capa", "otra"),
        ("corte_id", uuid4()),
        ("firma_corte", "e" * 64),
        ("dataset_id", uuid4()),
        ("firma_dataset", "f" * 64),
    ):
        assert firma_inputs(**{**entradas, llave: otro}) != firma, llave
    assert firma_inputs(**entradas, version_motor="geografia/v2") != firma
