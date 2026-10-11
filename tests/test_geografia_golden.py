"""El golden de geografia/v1: tres municipios inventados y una cuenta por cada caso que la geografia
tiene que distinguir, con el resultado escrito a mano. Todo es sintetico (ver
`geografia_escenarios`): ninguna geometria ni ninguna coordenada es real.

Los casos 5 y 6 son coordenadas que cartera/v2 rechaza (una latitud de 95, una longitud de -200):
una cuenta asi nunca llega a un corte. Se inyectan en lo que geografia/v1 copia del dataset para
probar que, si llegaran, la geografia las descarta por su cuenta y no las busca en la referencia.
"""

from __future__ import annotations

from datetime import date

import pytest
from geografia_escenarios import (
    AMOZOC,
    ATLIXCO,
    MERIDA,
    PUEBLA,
    CuentaGeo,
    espejo,
    geolocalizar,
    importar_referencia,
    ingerir_corte_geo,
    inyectar,
    publicado,
    resultados,
)

pytestmark = pytest.mark.usefixtures("bd")

CORTE = date(2026, 9, 30)

CASOS = [
    CuentaGeo(1, PUEBLA, "19.05", "-98.2"),  # 1. dentro de A
    CuentaGeo(2, AMOZOC, "19.05", "-98.0"),  # 2. dentro de B
    CuentaGeo(3, PUEBLA),  # 3. sin coordenadas
    CuentaGeo(4, PUEBLA, "19.05", None),  # 4. solo latitud
    CuentaGeo(5, PUEBLA, "19.05", "-98.2"),  # 5. latitud invalida: se inyecta 95
    CuentaGeo(6, PUEBLA, "19.05", "-98.2"),  # 6. longitud invalida: se inyecta -200
    CuentaGeo(7, PUEBLA, "0", "0"),  # 7. el punto (0, 0)
    CuentaGeo(8, PUEBLA, "25.0", "-100.0"),  # 8. fuera de los tres municipios
    CuentaGeo(9, PUEBLA, "19.05", "-98.1"),  # 9. sobre el limite comun de A y B
    CuentaGeo(10, MERIDA, "-89.6", "21.0"),  # 10. la latitud y la longitud de C, intercambiadas
    CuentaGeo(11, MERIDA, "21.0", "-89.6"),  # 11. municipio espacial = fuente
    CuentaGeo(12, AMOZOC, "19.05", "-98.2"),  # 12. municipio espacial != fuente
    CuentaGeo(
        13, ATLIXCO, "19.05", "-98.2"
    ),  # y la fuente dice un municipio fuera de la referencia
]
INYECTADAS = {5: ("95", "-98.2"), 6: ("19.05", "-200")}


def _m(codigo: str, **detalle) -> dict:
    return {"codigo": codigo, **detalle}


ESPERADO = {
    1: (
        "VALIDA",
        PUEBLA,
        True,
        [_m("MUNICIPIO_UNICO", cvegeo=PUEBLA), _m("COINCIDE_CON_FUENTE", cvegeo=PUEBLA)],
    ),
    2: (
        "VALIDA",
        AMOZOC,
        True,
        [_m("MUNICIPIO_UNICO", cvegeo=AMOZOC), _m("COINCIDE_CON_FUENTE", cvegeo=AMOZOC)],
    ),
    3: ("SIN_COORDENADAS", None, None, [_m("SIN_LATITUD"), _m("SIN_LONGITUD")]),
    4: ("COORDENADAS_INCOMPLETAS", None, None, [_m("SIN_LONGITUD")]),
    5: ("COORDENADAS_INVALIDAS", None, None, [_m("LATITUD_FUERA_DE_RANGO")]),
    6: ("COORDENADAS_INVALIDAS", None, None, [_m("LONGITUD_FUERA_DE_RANGO")]),
    7: ("COORDENADAS_INVALIDAS", None, None, [_m("PUNTO_0_0")]),
    8: ("FUERA_DE_REFERENCIA", None, None, [_m("SIN_MUNICIPIO_EN_REFERENCIA")]),
    9: ("MUNICIPIO_AMBIGUO", None, None, [_m("VARIOS_MUNICIPIOS", cvegeo=[AMOZOC, PUEBLA])]),
    10: (
        "FUERA_DE_REFERENCIA",
        None,
        None,
        [_m("SIN_MUNICIPIO_EN_REFERENCIA"), _m("POSIBLE_ORDEN_INVERTIDO", cvegeo=[MERIDA])],
    ),
    11: (
        "VALIDA",
        MERIDA,
        True,
        [_m("MUNICIPIO_UNICO", cvegeo=MERIDA), _m("COINCIDE_CON_FUENTE", cvegeo=MERIDA)],
    ),
    12: (
        "DISCREPANCIA_MUNICIPIO",
        PUEBLA,
        False,
        [
            _m("MUNICIPIO_UNICO", cvegeo=PUEBLA),
            _m(
                "DISCREPA_DE_FUENTE",
                cvegeo_fuente=AMOZOC,
                cvegeo_espacial=PUEBLA,
                fuente_en_referencia=True,
            ),
        ],
    ),
    13: (
        "DISCREPANCIA_MUNICIPIO",
        PUEBLA,
        False,
        [
            _m("MUNICIPIO_UNICO", cvegeo=PUEBLA),
            _m(
                "DISCREPA_DE_FUENTE",
                cvegeo_fuente=ATLIXCO,
                cvegeo_espacial=PUEBLA,
                fuente_en_referencia=False,
            ),
        ],
    ),
}


@pytest.fixture
def golden(tmp_path, monkeypatch):
    version = importar_referencia(tmp_path)
    ingerir_corte_geo(tmp_path, CORTE, CASOS)
    inyectar(monkeypatch, INYECTADAS)
    return geolocalizar(CORTE, version)


def test_cada_caso_del_golden_da_su_resultado(golden):
    assert (golden.estado, golden.resultado) == ("EXITOSA", "GEOGRAFIA_PUBLICADA")
    assert publicado(golden.id) == ESPERADO


def test_los_conteos_del_golden(golden):
    assert golden.cuentas_objetivo == 13
    assert (
        golden.validas,
        golden.sin_coordenadas,
        golden.incompletas,
        golden.invalidas,
        golden.fuera_cobertura,
        golden.ambiguas,
        golden.discrepantes,
    ) == (3, 1, 1, 3, 2, 1, 2)
    assert golden.distribucion["comparacion_municipio"] == {
        "COINCIDE": 3,
        "DISCREPA": 2,
        "NO_COMPARABLE": 8,
    }
    assert golden.distribucion["motivos"]["POSIBLE_ORDEN_INVERTIDO"] == 1


def test_lo_observado_se_guarda_como_vino_y_el_punto_solo_si_es_utilizable(golden):
    r = resultados(golden.id)
    assert (r[5]["latitud"], r[5]["longitud"]) == (95.0, -98.2)
    assert (r[6]["latitud"], r[6]["longitud"]) == (19.05, -200.0)
    # La invertida no se corrige: se guarda como vino, con su punto tal cual.
    assert (r[10]["latitud"], r[10]["longitud"]) == (-89.6, 21.0)
    assert (r[4]["latitud"], r[4]["longitud"]) == (19.05, None)
    con_punto = {n for n, x in r.items() if x["con_punto"]}
    assert con_punto == {1, 2, 8, 9, 10, 11, 12, 13}


def test_el_sql_por_conjuntos_publica_lo_mismo_que_la_regla_en_python(golden):
    assert publicado(golden.id) == espejo(golden.id)
