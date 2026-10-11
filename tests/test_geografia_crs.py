"""Reconocer el sistema de coordenadas de una capa por su .prj: por sus parametros, sin adivinar."""

from __future__ import annotations

import pytest

from motor_cartera.geografia import crs
from motor_cartera.geografia.sintetica import LCC_MEXICO_PRJ, WGS84_PRJ

OGC_6372 = (
    'PROJCS["Mexico ITRF2008 / LCC",GEOGCS["Mexico ITRF2008",DATUM["Mexico_ITRF2008",'
    'SPHEROID["GRS 1980",6378137,298.257222101,AUTHORITY["EPSG","7019"]],'
    'AUTHORITY["EPSG","1120"]],PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]],'
    'PROJECTION["Lambert_Conformal_Conic_2SP"],PARAMETER["latitude_of_origin",12],'
    'PARAMETER["central_meridian",-102],PARAMETER["standard_parallel_1",17.5],'
    'PARAMETER["standard_parallel_2",29.5],PARAMETER["false_easting",2500000],'
    'PARAMETER["false_northing",0],UNIT["metre",1],AUTHORITY["EPSG","6372"]]'
)


@pytest.mark.parametrize(
    ("prj", "epsg"),
    [(LCC_MEXICO_PRJ, 6372), (OGC_6372, 6372), (WGS84_PRJ, 4326), ("  " + WGS84_PRJ + "\n", 4326)],
    ids=["esri-6372", "ogc-6372", "wgs84", "con-espacios"],
)
def test_reconoce_por_sus_parametros_el_marco_geoestadistico_y_wgs84(prj, epsg):
    assert crs.reconocer(prj).epsg == epsg


@pytest.mark.parametrize(
    ("prj", "dice"),
    [
        (LCC_MEXICO_PRJ.replace("-102.0", "-99.0"), "centralmeridian"),
        (LCC_MEXICO_PRJ.replace("29.5", "30.0"), "standardparallel2"),
        (LCC_MEXICO_PRJ.replace("2500000.0", "500000.0"), "falseeasting"),
        (LCC_MEXICO_PRJ.replace("298.257222101", "298.257223563"), "GRS80"),
        (LCC_MEXICO_PRJ.replace("Lambert_Conformal_Conic", "Transverse_Mercator"), "Lambert"),
        (LCC_MEXICO_PRJ.replace('UNIT["Meter",1.0]', 'UNIT["Foot",0.3048]'), "metro"),
        (WGS84_PRJ.replace("298.257223563", "298.257222101"), "WGS 84"),
        (WGS84_PRJ.replace("0.0174532925199433", "1.0"), "WGS 84"),
        ('GEOCCS["x"]', "GEOCCS"),
    ],
)
def test_un_sistema_que_no_es_exactamente_el_esperado_se_rechaza(prj, dice):
    with pytest.raises(crs.CrsNoReconocido) as error:
        crs.reconocer(prj)
    assert dice in str(error.value)


@pytest.mark.parametrize(
    "prj", ["", "GEOGCS[", 'GEOGCS["x" "y"]', "PROJCS[1,2", "GEOGCS[]]", "$%&", "123"]
)
def test_un_prj_que_no_es_wkt_se_rechaza_sin_adivinar(prj):
    with pytest.raises(crs.CrsNoReconocido):
        crs.reconocer(prj)


def test_el_arbol_del_wkt_conserva_sus_argumentos():
    raiz = crs.analizar(WGS84_PRJ)
    assert raiz.clave == "GEOGCS"
    assert raiz.argumentos[0] == "GCS_WGS_1984"
    esferoide = raiz.hijo("DATUM").hijo("SPHEROID")
    assert esferoide.argumentos == ["WGS_1984", 6378137.0, 298.257223563]
    assert raiz.hijo("NO_EXISTE") is None
