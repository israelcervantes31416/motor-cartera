"""El generador de configuraciones de campo sinteticas, sin base: determinista por semilla, con
codigos inventados y sin un solo dato de una persona, y siempre una configuracion que `leer` acepta.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date

import pytest

from motor_cartera.campo_v2.configuracion import leer
from motor_cartera.generador.campo import CALENDARIOS, configuracion_sintetica

BASES_VERSION = "5f0c3b8e-1d2a-4c6b-9e7f-0a1b2c3d4e5f"
LLAVES_DE_UN_GESTOR = {"codigo", "base", "calendario", "capacidad_cuentas_dia", "activo"}


def _generar(**cambios) -> dict:
    return configuracion_sintetica(
        **{
            "bases": ["BASE_B", "BASE_A"],
            "bases_version_id": BASES_VERSION,
            "gestores_por_base": 3,
            "semilla": 7,
            **cambios,
        }
    )


def test_la_misma_semilla_da_el_mismo_documento_byte_por_byte():
    primero = json.dumps(_generar(), sort_keys=True)

    assert json.dumps(_generar(), sort_keys=True) == primero
    assert json.dumps(_generar(bases=["BASE_A", "BASE_B"]), sort_keys=True) == primero
    assert json.dumps(_generar(semilla=8), sort_keys=True) != primero


def test_los_gestores_son_codigos_numerados_en_el_orden_de_las_bases_y_nada_mas():
    documento = _generar(gestores_por_base={"BASE_A": 2, "BASE_B": 1, "BASE_Z": 0})

    assert [(g["codigo"], g["base"]) for g in documento["gestores"]] == [
        ("GESTOR_0001", "BASE_A"),
        ("GESTOR_0002", "BASE_A"),
        ("GESTOR_0003", "BASE_B"),
    ]
    # Ni nombre, ni correo, ni telefono, ni un identificador de nomina: solo lo que el motor usa.
    assert all(set(g) == LLAVES_DE_UN_GESTOR for g in documento["gestores"])
    assert _generar(gestores_por_base={"BASE_A": 2})["gestores"][-1]["base"] == "BASE_A"


def test_los_cupos_los_calendarios_y_los_inactivos_salen_de_lo_declarado():
    documento = _generar(gestores_por_base=1_000, capacidades=(5, 9), inactivos=0.1)
    gestores = documento["gestores"]

    assert {g["capacidad_cuentas_dia"] for g in gestores} == {5, 9}
    calendarios = Counter(g["calendario"] for g in gestores)
    assert set(calendarios) == {codigo for codigo, _, _ in CALENDARIOS}
    # Los pesos de los calendarios (0.6, 0.3, 0.1) y la fraccion de inactivos, con 2,000 gestores.
    assert 0.55 < calendarios["LV_PARTIDO"] / len(gestores) < 0.65
    assert 0.05 < calendarios["JD_EXTENDIDO"] / len(gestores) < 0.15
    assert 0.07 < sum(not g["activo"] for g in gestores) / len(gestores) < 0.13
    assert all(g["activo"] for g in _generar(gestores_por_base=200, inactivos=0)["gestores"])


def test_los_festivos_cierran_y_las_medias_jornadas_dejan_de_9_a_13_en_cada_calendario():
    documento = _generar(
        festivos=[date(2026, 11, 16), date(2026, 9, 16)], medias_jornadas=[date(2026, 12, 24)]
    )

    for calendario in documento["calendarios"]:
        assert calendario["excepciones"] == [
            {"fecha": "2026-09-16", "tipo": "CERRADO", "motivo": "Festivo"},
            {"fecha": "2026-11-16", "tipo": "CERRADO", "motivo": "Festivo"},
            {
                "fecha": "2026-12-24",
                "tipo": "VENTANAS_ESPECIALES",
                "motivo": "Media jornada",
                "ventanas": [{"inicio": "09:00", "fin": "13:00"}],
            },
        ]


def test_lo_que_genera_es_una_configuracion_valida_con_todos_sus_gestores():
    documento = _generar(
        gestores_por_base=40, festivos=[date(2026, 11, 16)], medias_jornadas=[date(2026, 12, 24)]
    )

    configuracion = leer(documento)

    assert len(configuracion.gestores) == 80
    assert {c.codigo for c in configuracion.calendarios} == {
        "LV_PARTIDO",
        "LS_CORRIDO",
        "JD_EXTENDIDO",
    }
    assert configuracion.zona_horaria == "America/Mexico_City"
    # El mismo contenido, con los gestores en otro orden y otro nombre, tiene el mismo hash.
    otro = {**documento, "nombre": "Otro nombre", "gestores": documento["gestores"][::-1]}
    assert leer(otro).hash("b" * 64) == configuracion.hash("b" * 64)


@pytest.mark.parametrize(
    ("cambios", "mensaje"),
    [
        ({"inactivos": 1.0}, "inactivos es una fraccion"),
        ({"inactivos": -0.1}, "inactivos es una fraccion"),
        ({"capacidades": ()}, "Cada cupo es un entero positivo"),
        ({"capacidades": (0, 5)}, "Cada cupo es un entero positivo"),
    ],
)
def test_lo_que_el_generador_no_acepta(cambios, mensaje):
    with pytest.raises(ValueError, match=mensaje):
        _generar(**cambios)
