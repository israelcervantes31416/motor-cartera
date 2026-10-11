"""territorial-public-v2 como nucleo puro: cada regla, la tolerancia de empate, su hash y la firma
de una ejecucion. Sin base de datos."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from motor_cartera.db.modelos import CONTEOS_DE_TERRITORIAL_V2, ESTADOS_ASIGNACION
from motor_cartera.territorial_v2.politica import (
    CONTEO_DE_ESTADO,
    PUBLIC_V2,
    Candidata,
    EstadoAsignacion,
    PoliticaTerritorial,
    asignar,
    firma_inputs,
    hash_politica,
)


def _c(codigo: str, metros: str) -> Candidata:
    return Candidata(codigo, Decimal(metros))


def test_el_vocabulario_es_el_que_la_base_exige():
    assert {e.value for e in EstadoAsignacion} == set(ESTADOS_ASIGNACION)
    assert set(CONTEO_DE_ESTADO.values()) == set(CONTEOS_DE_TERRITORIAL_V2)


def test_la_politica_publica_declara_su_tolerancia_y_lo_que_no_usa():
    definicion = PUBLIC_V2.definicion()
    assert PUBLIC_V2.version == "territorial-public-v2"
    assert definicion["tolerancia_empate_m"] == "100.00"
    assert definicion["geografia_confiable"] == ["VALIDA"]
    assert {"carga", "capacidad", "saldo", "prioridad", "decision/v2"} <= set(definicion["no_usa"])


def test_el_hash_de_la_politica_es_estable_y_cambia_con_su_tolerancia():
    assert hash_politica() == hash_politica(PUBLIC_V2)
    assert len(hash_politica()) == 64
    otra = PoliticaTerritorial(PUBLIC_V2.version, Decimal("50.00"))
    assert hash_politica(otra) != hash_politica()


@pytest.mark.parametrize(
    "estado",
    [
        "SIN_COORDENADAS",
        "COORDENADAS_INCOMPLETAS",
        "COORDENADAS_INVALIDAS",
        "MUNICIPIO_AMBIGUO",
        "DISCREPANCIA_MUNICIPIO",
    ],
)
def test_una_geografia_dudosa_va_a_revision_aunque_haya_bases(estado):
    a = asignar(estado, "21114", [_c("BASE_A", "10.00")])
    assert (a.estado, a.base, a.distancia_m) == (EstadoAsignacion.REVISION_GEOGRAFICA, None, None)
    assert a.motivos == [{"codigo": "GEOGRAFIA_NO_CONFIABLE", "estado_geografia": estado}]


def test_un_punto_fuera_de_la_referencia_queda_fuera_de_cobertura():
    a = asignar("FUERA_DE_REFERENCIA", None, [])
    assert a.estado == EstadoAsignacion.FUERA_DE_COBERTURA
    assert a.motivos == [{"codigo": "PUNTO_FUERA_DE_REFERENCIA"}]


def test_sin_base_activa_que_cubra_el_municipio_no_se_asigna():
    sin = asignar("VALIDA", "21114", [])
    assert (sin.estado, sin.base) == (EstadoAsignacion.SIN_BASE_ELEGIBLE, None)
    assert sin.motivos == [{"codigo": "SIN_BASE_QUE_CUBRA", "cvegeo": "21114"}]
    con_inactivas = asignar("VALIDA", "21114", [], inactivas=["BASE_Z", "BASE_C"])
    assert con_inactivas.motivos[-1] == {"codigo": "BASES_INACTIVAS", "bases": ["BASE_C", "BASE_Z"]}


def test_una_sola_elegible_se_asigna_con_su_distancia():
    a = asignar("VALIDA", "21114", [_c("BASE_A", "5263.03")])
    assert (a.estado, a.base, a.distancia_m) == (
        EstadoAsignacion.ASIGNADA,
        "BASE_A",
        Decimal("5263.03"),
    )
    assert a.motivos == [
        {"codigo": "BASE_UNICA_ELEGIBLE", "base": "BASE_A", "distancia_m": "5263.03"}
    ]


def test_de_varias_se_asigna_la_inequivocamente_mas_cercana():
    a = asignar("VALIDA", "21114", [_c("BASE_Z", "9000.00"), _c("BASE_A", "1000.00")])
    assert (a.estado, a.base) == (EstadoAsignacion.ASIGNADA, "BASE_A")
    assert a.motivos == [
        {
            "codigo": "BASE_MAS_CERCANA",
            "base": "BASE_A",
            "distancia_m": "1000.00",
            "elegibles": 2,
            "siguiente": "BASE_Z",
            "distancia_siguiente_m": "9000.00",
        }
    ]


@pytest.mark.parametrize(
    ("segunda", "estado"),
    [
        ("1000.00", EstadoAsignacion.AMBIGUA_BASE),
        ("1099.99", EstadoAsignacion.AMBIGUA_BASE),
        ("1100.00", EstadoAsignacion.AMBIGUA_BASE),
        ("1100.01", EstadoAsignacion.ASIGNADA),
    ],
)
def test_dos_bases_a_no_mas_de_la_tolerancia_son_un_empate(segunda, estado):
    a = asignar("VALIDA", "21114", [_c("BASE_B", segunda), _c("BASE_A", "1000.00")])
    assert a.estado == estado
    if estado == EstadoAsignacion.AMBIGUA_BASE:
        assert a.base is None
        assert a.motivos == [
            {
                "codigo": "EMPATE_ENTRE_BASES",
                "bases": ["BASE_A", "BASE_B"],
                "distancia_m": "1000.00",
                "tolerancia_m": "100.00",
            }
        ]


def test_un_empate_no_se_resuelve_por_el_codigo_ni_por_el_orden():
    """Las mismas bases en cualquier orden, y con los codigos al reves, dan un empate igual."""
    uno = asignar("VALIDA", "21114", [_c("AAA", "500.00"), _c("ZZZ", "550.00")])
    otro = asignar("VALIDA", "21114", [_c("ZZZ", "500.00"), _c("AAA", "550.00")])
    assert uno.estado == otro.estado == EstadoAsignacion.AMBIGUA_BASE
    assert uno.base is None and otro.base is None


def test_la_firma_cambia_con_cada_entrada():
    entradas = {
        "corte_id": uuid4(),
        "geografia_run_id": uuid4(),
        "firma_geografia": "a" * 64,
        "bases_version_id": uuid4(),
        "hash_configuracion": "b" * 64,
    }
    firma = firma_inputs(**entradas)
    assert firma == firma_inputs(**entradas)
    for llave, otro in (
        ("corte_id", uuid4()),
        ("geografia_run_id", uuid4()),
        ("firma_geografia", "c" * 64),
        ("bases_version_id", uuid4()),
        ("hash_configuracion", "d" * 64),
    ):
        assert firma_inputs(**{**entradas, llave: otro}) != firma, llave
    otra = PoliticaTerritorial(PUBLIC_V2.version, Decimal("1.00"))
    assert firma_inputs(**entradas, politica=otra) != firma
