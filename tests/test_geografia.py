"""El catalogo geografico de la proyeccion, sin base de datos: que es el del INEGI, como resuelve y
cuando se niega a resolver."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from motor_cartera.fuentes.geografia import (
    ALIAS_DE_ENTIDAD,
    CATALOGO,
    ENTIDAD_DESCONOCIDA,
    MUNICIPIO_AMBIGUO,
    MUNICIPIO_DESCONOCIDO,
    catalogo,
    normalizar_nombre,
    resolver,
)


def test_el_catalogo_es_el_publico_del_inegi_con_su_procedencia():
    texto = CATALOGO.read_bytes()
    assert texto.isascii()  # los acentos van escapados
    documento = json.loads(texto)
    assert documento["url"].startswith("https://gaia.inegi.org.mx/wscatgeo/")
    assert "INEGI" in documento["fuente"] and documento["consultado"]
    cat = catalogo()
    assert len(cat.entidades) == 32
    assert sum(len(e.municipios) for e in cat.entidades) > 2_400
    assert [e.cve_entidad for e in cat.entidades] == [f"{i:02d}" for i in range(1, 33)]
    for entidad in cat.entidades:
        claves = [m.cve_municipio for m in entidad.municipios]
        assert len(claves) == len(set(claves))
        assert all(len(c) == 3 and c.isdigit() for c in claves)


@pytest.mark.parametrize(
    ("estado", "poblacion", "claves"),
    [
        ("PUEBLA", "TEHUACAN", ("21", "156")),
        ("Puebla", "Tehuacán", ("21", "156")),
        ("  puebla ", " puebla ", ("21", "114")),
        ("Pue.", "Teziutlán", ("21", "174")),
        ("CIUDAD DE MEXICO", "COYOACAN", ("09", "003")),
        ("CDMX", "IZTAPALAPA", ("09", "007")),
        ("VERACRUZ", "XALAPA", ("30", "087")),
        ("VERACRUZ DE IGNACIO DE LA LLAVE", "Xalapa", ("30", "087")),
        ("ESTADO DE MEXICO", "ECATEPEC DE MORELOS", ("15", "033")),
        ("Edo. Mex.", "Toluca", ("15", "106")),
        ("TLAXCALA", "APIZACO", ("29", "005")),
    ],
)
def test_resuelve_nombres_a_las_claves_del_inegi(estado, poblacion, claves):
    resolucion = resolver(pd.Series([estado]), pd.Series([poblacion]))

    assert (resolucion.cve_entidad[0], resolucion.cve_municipio[0]) == claves
    assert pd.isna(resolucion.motivo[0])


@pytest.mark.parametrize(
    ("estado", "poblacion", "campo", "regla"),
    [
        ("NARNIA", "TEHUACAN", "ESTADO_CTE", ENTIDAD_DESCONOCIDA),
        ("PUEBLA", "ATLANTIDA", "POBLACION_CTE", MUNICIPIO_DESCONOCIDO),
        # Una localidad que no es municipio no se resuelve: la geografia por localidad es posterior.
        ("PUEBLA", "SAN FRANCISCO TOTIMEHUACAN", "POBLACION_CTE", MUNICIPIO_DESCONOCIDO),
        # Un municipio de otra entidad tampoco.
        ("TLAXCALA", "TEHUACAN", "POBLACION_CTE", MUNICIPIO_DESCONOCIDO),
        # Oaxaca tiene dos San Juan Mixtepec: no se elige ninguno.
        ("OAXACA", "SAN JUAN MIXTEPEC", "POBLACION_CTE", MUNICIPIO_AMBIGUO),
    ],
)
def test_lo_que_no_se_puede_resolver_dice_por_que(estado, poblacion, campo, regla):
    resolucion = resolver(pd.Series([estado]), pd.Series([poblacion]))

    assert (resolucion.campo[0], resolucion.motivo[0]) == (campo, regla)
    assert pd.isna(resolucion.cve_municipio[0])


def test_los_ambiguos_son_pocos_y_estan_marcados():
    cat = catalogo()
    ambiguos = [clave for clave, cve in cat.por_municipio.items() if cve is None]
    assert ambiguos and all(entidad == "20" for entidad, _ in ambiguos)


def test_los_alias_de_entidad_son_pocos_y_no_chocan():
    cat = catalogo()
    for cve, alias in ALIAS_DE_ENTIDAD.items():
        for nombre in alias:
            assert cat.por_entidad[normalizar_nombre(nombre)] == cve


def test_normalizar_quita_acentos_puntos_y_espacios_de_mas():
    assert normalizar_nombre("  San  Andrés   Cholula. ") == "SAN ANDRES CHOLULA"
    assert normalizar_nombre("Méx.") == "MEX"


def test_resuelve_cada_nombre_distinto_una_vez_y_en_el_orden_de_las_filas():
    estados = pd.Series(["PUEBLA", "TLAXCALA", "PUEBLA", None] * 1000, index=range(5, 4005))
    poblaciones = pd.Series(["TEHUACAN", "APIZACO", "ZACATLAN", "X"] * 1000, index=range(5, 4005))

    resolucion = resolver(estados, poblaciones)

    assert list(resolucion.cve_municipio.index[:4]) == [5, 6, 7, 8]
    assert list(resolucion.cve_municipio[:3]) == ["156", "005", "208"]
    assert resolucion.motivo.notna().sum() == 0  # sin estado no hay motivo geografico: es requerido
