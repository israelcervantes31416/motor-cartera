"""El golden de territorial/v2: cuatro municipios inventados, bases inventadas dentro de ellos, y
una cuenta por cada caso que la politica territorial-public-v2 tiene que distinguir, con el
resultado escrito a mano. Las distancias se comprueban ademas contra la formula de Vincenty, un
calculo geodesico independiente del de PostGIS. Todo es sintetico (ver `territorial_v2_escenarios`).

Bases de la version 1:

- BASE_A en (19.05, -98.25), dentro de A, cubre A;
- BASE_B en (19.05, -98.05), dentro de B, cubre A y B;
- BASE_Y en (19.10, -97.95), dentro de B, cubre B, INACTIVA (y mas cerca de la cuenta 1 que BASE_B);
- BASE_Z en (21.00, -89.50), dentro de C, cubre C, INACTIVA.

D no lo cubre ninguna. La version 2 es la 1 mas BASE_C en (19.06, -98.29), dentro de A, cubre A.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from geografia_escenarios import (
    AMOZOC,
    ATLIXCO,
    MERIDA,
    PUEBLA,
    CuentaGeo,
    geolocalizar,
    ingerir_corte_geo,
)
from territorial_v2_escenarios import (
    asignaciones,
    asignar_tv2,
    base,
    espejo,
    publicadas,
    publicar_bases,
    referencia_tv2,
    vincenty,
)

pytestmark = pytest.mark.usefixtures("bd_del_modulo")

CORTE, OTRO_CORTE = date(2026, 9, 30), date(2026, 10, 7)

BASES_V1 = (
    base("BASE_A", 19.05, -98.25, [PUEBLA]),
    base("BASE_B", 19.05, -98.05, [PUEBLA, AMOZOC]),
    base("BASE_Y", 19.10, -97.95, [AMOZOC], activa=False),
    base("BASE_Z", 21.00, -89.50, [MERIDA], activa=False),
)
BASE_C = base("BASE_C", 19.06, -98.29, [PUEBLA])
UBICACION = {
    "BASE_A": (19.05, -98.25),
    "BASE_B": (19.05, -98.05),
    "BASE_C": (19.06, -98.29),
}

CASOS = [
    CuentaGeo(1, AMOZOC, "19.1", "-97.96"),  # 1. B lo cubre una sola base activa
    CuentaGeo(2, PUEBLA, "19.05", "-98.24"),  # 2. dos bases, una claramente mas cercana
    CuentaGeo(3, ATLIXCO, "18.85", "-98.6"),  # 3. D: ninguna base lo cubre
    CuentaGeo(4, PUEBLA, "19.05", "-98.15"),  # 4. a la misma distancia de BASE_A y BASE_B
    CuentaGeo(5, PUEBLA, "0", "0"),  # 5. geografia invalida
    CuentaGeo(6, AMOZOC, "19.05", "-98.2"),  # 6. discrepa de la fuente
    CuentaGeo(7, PUEBLA, "19.05", "-98.1"),  # 7. en el limite de A y B: ambigua
    CuentaGeo(8, MERIDA, "21.0", "-89.6"),  # 8. C solo lo cubre una base inactiva
    CuentaGeo(9, PUEBLA, "19.05", "-98.24"),  # 9. en A en este corte; en B en el otro
    CuentaGeo(10, PUEBLA, "19.05", "-98.29"),  # 10. la version 2 le pone una base mas cerca
    CuentaGeo(11, PUEBLA),  # sin coordenadas
    CuentaGeo(12, PUEBLA, "25.0", "-100.0"),  # fuera de la referencia
]
UBICACION_CUENTA = {
    1: (19.10, -97.96),
    2: (19.05, -98.24),
    4: (19.05, -98.15),
    9: (19.05, -98.24),
    10: (19.05, -98.29),
}


def _m(codigo: str, **detalle) -> dict:
    return {"codigo": codigo, **detalle}


def _revision(estado_geografia: str) -> tuple:
    return (
        "REVISION_GEOGRAFICA",
        None,
        None,
        [_m("GEOGRAFIA_NO_CONFIABLE", estado_geografia=estado_geografia)],
    )


def _mas_cercana(base_: str, metros: str, elegibles: int, siguiente: str, despues: str) -> tuple:
    return (
        "ASIGNADA",
        base_,
        Decimal(metros),
        [
            _m(
                "BASE_MAS_CERCANA",
                base=base_,
                distancia_m=metros,
                elegibles=elegibles,
                siguiente=siguiente,
                distancia_siguiente_m=despues,
            )
        ],
    )


ESPERADO_V1 = {
    1: (
        "ASIGNADA",
        "BASE_B",
        Decimal("10970.49"),
        [_m("BASE_UNICA_ELEGIBLE", base="BASE_B", distancia_m="10970.49")],
    ),
    2: _mas_cercana("BASE_A", "1052.61", 2, "BASE_B", "19999.50"),
    3: ("SIN_BASE_ELEGIBLE", None, None, [_m("SIN_BASE_QUE_CUBRA", cvegeo=ATLIXCO)]),
    4: (
        "AMBIGUA_BASE",
        None,
        None,
        [
            _m(
                "EMPATE_ENTRE_BASES",
                bases=["BASE_A", "BASE_B"],
                distancia_m="10526.05",
                tolerancia_m="100.00",
            )
        ],
    ),
    5: _revision("COORDENADAS_INVALIDAS"),
    6: _revision("DISCREPANCIA_MUNICIPIO"),
    7: _revision("MUNICIPIO_AMBIGUO"),
    8: (
        "SIN_BASE_ELEGIBLE",
        None,
        None,
        [_m("SIN_BASE_QUE_CUBRA", cvegeo=MERIDA), _m("BASES_INACTIVAS", bases=["BASE_Z"])],
    ),
    9: _mas_cercana("BASE_A", "1052.61", 2, "BASE_B", "19999.50"),
    10: _mas_cercana("BASE_A", "4210.42", 2, "BASE_B", "25262.52"),
    11: _revision("SIN_COORDENADAS"),
    12: ("FUERA_DE_COBERTURA", None, None, [_m("PUNTO_FUERA_DE_REFERENCIA")]),
}


@pytest.fixture(scope="module")
def g(bd_del_modulo, tmp_path_factory):
    destino = tmp_path_factory.mktemp("territorial")
    referencia = referencia_tv2(destino)
    ingerir_corte_geo(destino, CORTE, CASOS)
    ingerir_corte_geo(destino, OTRO_CORTE, [CuentaGeo(9, AMOZOC, "19.1", "-97.96")], nombre="otra")
    geografia = geolocalizar(CORTE, referencia)
    geografia_otra = geolocalizar(OTRO_CORTE, referencia)
    v1 = publicar_bases(*BASES_V1, nombre="Bases v1")
    primera = asignar_tv2(CORTE, geografia, v1)
    antes = publicadas(primera.id)
    v2 = publicar_bases(*BASES_V1, BASE_C, nombre="Bases v2")
    return SimpleNamespace(
        geografia=geografia,
        v1=v1,
        v2=v2,
        primera=primera,
        antes=antes,
        con_v2=asignar_tv2(CORTE, geografia, v2),
        otro_corte=asignar_tv2(OTRO_CORTE, geografia_otra, v1),
    )


def test_cada_caso_del_golden_da_su_asignacion(g):
    assert (g.primera.estado, g.primera.resultado) == ("EXITOSA", "TERRITORIAL_PUBLICADO")
    assert g.antes == ESPERADO_V1


def test_los_conteos_del_golden(g):
    e = g.primera
    assert (e.cuentas_objetivo, e.asignadas, e.revision_geografica, e.sin_base) == (12, 4, 4, 2)
    assert (e.fuera_cobertura, e.ambiguas) == (1, 1)
    assert e.distribucion["por_base"] == {
        "BASE_A": {"cuentas": 3, "distancia_mediana_m": "1052.61"},
        "BASE_B": {"cuentas": 1, "distancia_mediana_m": "10970.49"},
    }
    assert "Sin capacidad ni balanceo" in e.detalle


def test_la_asignada_lleva_el_municipio_y_la_revision_no(g):
    a = asignaciones(g.primera.id)
    assert {n: x["cvegeo"] for n, x in a.items() if x["cvegeo"]} == {
        1: AMOZOC,
        2: PUEBLA,
        3: ATLIXCO,
        4: PUEBLA,
        8: MERIDA,
        9: PUEBLA,
        10: PUEBLA,
    }


def test_las_distancias_son_geodesicas(g):
    """Cada distancia publicada es la de Vincenty sobre WGS 84 al centimetro. Sobre grados no lo
    seria: de la cuenta 2 a BASE_A hay 0.01 grados, y 1052.61 metros."""
    for numero, (_, base_, distancia, _) in g.antes.items():
        if base_ is None:
            continue
        geodesica = vincenty(*UBICACION_CUENTA[numero], *UBICACION[base_])
        assert abs(distancia - Decimal(str(geodesica))) <= Decimal("0.01"), numero
    # El empate es exacto: la cuenta 4 esta a la misma longitud de las dos bases.
    assert vincenty(19.05, -98.15, 19.05, -98.25) == pytest.approx(
        vincenty(19.05, -98.15, 19.05, -98.05), abs=1e-6
    )


def test_la_misma_cuenta_en_dos_cortes_se_asigna_con_la_geografia_de_cada_uno(g):
    assert g.antes[9][:2] == ("ASIGNADA", "BASE_A")
    assert publicadas(g.otro_corte.id) == {
        9: (
            "ASIGNADA",
            "BASE_B",
            Decimal("10970.49"),
            [_m("BASE_UNICA_ELEGIBLE", base="BASE_B", distancia_m="10970.49")],
        )
    }


def test_una_nueva_version_de_bases_cambia_la_asignacion_sin_tocar_la_historica(g):
    con_v2 = publicadas(g.con_v2.id)
    assert con_v2[10] == _mas_cercana("BASE_C", "1106.93", 3, "BASE_A", "4210.42")
    # Las demas cuentas de A siguen con la misma base, con una elegible mas en su explicacion.
    assert con_v2[2] == _mas_cercana("BASE_A", "1052.61", 3, "BASE_C", "5378.02")
    assert {n: x[:2] for n, x in con_v2.items() if n not in (10,)} == {
        n: x[:2] for n, x in ESPERADO_V1.items() if n not in (10,)
    }
    # La ejecucion con la version 1 no cambio.
    assert publicadas(g.primera.id) == g.antes
    assert g.con_v2.firma_inputs != g.primera.firma_inputs
    assert g.con_v2.ejecucion_geografia_id == g.primera.ejecucion_geografia_id


def test_el_sql_por_conjuntos_publica_lo_mismo_que_la_politica_en_python(g):
    for ejecucion in (g.primera, g.con_v2, g.otro_corte):
        assert publicadas(ejecucion.id) == espejo(ejecucion.id)
