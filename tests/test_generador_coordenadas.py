"""Las coordenadas sinteticas del generador: cada categoria hace lo que dice, las proporciones son
las que se piden, una cuenta conserva su coordenada en todos sus cortes, y sin una muestra de
puntos LATITUD y LONGITUD siguen vacias. Sin base de datos: la muestra se arma a mano."""

from __future__ import annotations

import json
import zlib
from datetime import date

import numpy as np
import pytest

from motor_cartera.generador.coordenadas import (
    CATEGORIAS,
    VERSION_COORDENADAS,
    Coordenadas,
    MuestraDePuntos,
    Proporciones,
    asignar,
    contar,
)
from motor_cartera.generador.oficial import (
    _geografia,
    estado_inicial,
    generar_escenario,
    tabla_cartera,
)

PUNTOS = {
    "21114": [(-98.2, 19.05), (-98.21, 19.06)],
    "21015": [(-98.0, 19.05)],
    "31050": [(-89.6, 21.0)],
}
FUERA = [(-105.0, 17.0), (-105.1, 17.1)]


def muestra(municipios=PUNTOS, fuera=FUERA) -> MuestraDePuntos:
    return MuestraDePuntos(
        municipios={c: list(p) for c, p in municipios.items()},
        fuera=list(fuera),
        sha256="0" * 64,
        datos={"geografia_version_id": "sintetica", "sha256_referencia": "1" * 64},
    )


def azar_de(n: int, semilla: int = 1):
    def azar(atributo: str) -> np.ndarray:
        return np.random.default_rng([semilla, zlib.crc32(atributo.encode())]).random(n)

    return azar


def _asignar(cvegeo: list[str], proporciones: str, semilla: int = 1):
    latitud, longitud, categorias = asignar(
        np.array(cvegeo),
        azar_de(len(cvegeo), semilla),
        Coordenadas(muestra(), Proporciones.de_texto(proporciones)),
    )
    return latitud.to_pylist(), longitud.to_pylist(), categorias


def _punto(latitud: str, longitud: str) -> tuple[float, float]:
    return (float(longitud), float(latitud))


# --- las proporciones -----------------------------------------------------------------------------


def test_las_proporciones_de_omision_suman_uno_y_se_leen_de_texto():
    assert sum(getattr(Proporciones(), c) for c in CATEGORIAS) == pytest.approx(1)
    leidas = Proporciones.de_texto("validas=0.5, fuera=0.5")
    assert (leidas.validas, leidas.fuera, leidas.faltantes) == (0.5, 0.5, 0.0)


@pytest.mark.parametrize("texto", ["validas=0.5", "validas=1.2,fuera=-0.2", "cerca=1"])
def test_unas_proporciones_que_no_suman_uno_o_no_existen_se_rechazan(texto):
    with pytest.raises(ValueError):
        Proporciones.de_texto(texto)


# --- cada categoria -------------------------------------------------------------------------------


def test_una_valida_es_un_punto_de_la_muestra_de_su_municipio():
    cvegeo = ["21114", "21015", "31050", "21114"] * 5
    latitud, longitud, categorias = _asignar(cvegeo, "validas=1")
    assert set(categorias) == {"validas"}
    for c, la, lo in zip(cvegeo, latitud, longitud, strict=True):
        assert _punto(la, lo) in PUNTOS[c]
        assert len(la.split(".")[1]) == len(lo.split(".")[1]) == 6


def test_una_valida_de_un_municipio_fuera_de_la_muestra_queda_sin_coordenadas():
    latitud, longitud, _ = _asignar(["21019", "21019"], "validas=1")
    assert latitud == longitud == [None, None]


def test_faltantes_incompletas_e_invalidas():
    cvegeo = ["21114"] * 10
    assert _asignar(cvegeo, "faltantes=1")[:2] == ([None] * 10, [None] * 10)
    latitud, longitud, _ = _asignar(cvegeo, "incompletas=1")
    assert all(la is not None for la in latitud) and longitud == [None] * 10
    assert {float(la) for la in latitud} <= {y for _, y in PUNTOS["21114"]}
    # La unica invalida que cartera/v2 deja pasar: (0, 0).
    assert _asignar(cvegeo, "invalidas=1")[:2] == (["0.000000"] * 10, ["0.000000"] * 10)


def test_una_fuera_es_un_punto_de_fuera_y_una_discrepante_de_otro_municipio():
    cvegeo = ["21114", "21015", "31050"] * 10
    latitud, longitud, _ = _asignar(cvegeo, "fuera=1")
    assert {_punto(la, lo) for la, lo in zip(latitud, longitud, strict=True)} <= set(FUERA)
    latitud, longitud, _ = _asignar(cvegeo, "discrepantes=1")
    for c, la, lo in zip(cvegeo, latitud, longitud, strict=True):
        punto = _punto(la, lo)
        assert punto not in PUNTOS[c]
        assert any(punto in p for otro, p in PUNTOS.items() if otro != c)


def test_las_categorias_salen_en_las_proporciones_que_se_piden():
    n = 20_000
    _, _, categorias = _asignar(["21114"] * n, "validas=0.9,faltantes=0.04,invalidas=0.06")
    conteo = contar(categorias)
    assert sum(conteo.values()) == n
    for categoria, p in (("validas", 0.9), ("faltantes", 0.04), ("invalidas", 0.06)):
        sigma = (n * p * (1 - p)) ** 0.5
        assert abs(conteo[categoria] - n * p) < 4 * sigma, (categoria, conteo)
    assert conteo["fuera"] == conteo["discrepantes"] == conteo["incompletas"] == 0


def test_la_misma_semilla_da_las_mismas_coordenadas():
    cvegeo = ["21114", "21015", "31050"] * 50
    assert (
        _asignar(cvegeo, "validas=0.5,fuera=0.5", semilla=3)[:2]
        == _asignar(cvegeo, "validas=0.5,fuera=0.5", semilla=3)[:2]
    )


# --- en el generador ------------------------------------------------------------------------------


def _muestra_del_generador() -> MuestraDePuntos:
    """Un punto por cada municipio que usa el generador, que dice cual es: x = -100 - clave/1e6."""
    geo = _geografia()
    claves = np.char.add(geo.cve_entidad.astype(str), geo.cve_municipio.astype(str))
    return muestra({c: [(-100.0 - int(c) / 1e6, 19.0)] for c in claves}, FUERA)


def test_sin_muestra_de_puntos_latitud_y_longitud_van_vacias():
    estado = estado_inicial(200, semilla=5, fecha_corte=date(2026, 9, 30))
    tabla = tabla_cartera(estado, semilla=5, fecha_corte=date(2026, 9, 30))
    assert tabla.column("LATITUD").null_count == tabla.column("LONGITUD").null_count == 200


def test_cada_cuenta_valida_cae_en_el_municipio_de_su_fuente_y_lo_conserva_en_cada_corte():
    estado = estado_inicial(400, semilla=5, fecha_corte=date(2026, 9, 30))
    coordenadas = Coordenadas(_muestra_del_generador(), Proporciones.de_texto("validas=1"))
    primera = tabla_cartera(
        estado, semilla=5, fecha_corte=date(2026, 9, 30), coordenadas=coordenadas
    )
    segunda = tabla_cartera(
        estado, semilla=5, fecha_corte=date(2026, 10, 7), coordenadas=coordenadas
    )

    geo = _geografia()
    clave_de = {
        (e, p): ce + cm
        for e, p, ce, cm in zip(
            geo.estado, geo.poblacion, geo.cve_entidad, geo.cve_municipio, strict=True
        )
    }
    for estado_cte, poblacion, longitud in zip(
        primera.column("ESTADO_CTE").to_pylist(),
        primera.column("POBLACION_CTE").to_pylist(),
        primera.column("LONGITUD").to_pylist(),
        strict=True,
    ):
        assert round((-100.0 - float(longitud)) * 1e6) == int(clave_de[(estado_cte, poblacion)])
    for columna in ("LATITUD", "LONGITUD"):
        assert primera.column(columna).to_pylist() == segunda.column(columna).to_pylist()


def test_el_manifiesto_del_escenario_dice_de_donde_salieron_las_coordenadas(tmp_path):
    coordenadas = Coordenadas(_muestra_del_generador(), Proporciones())
    generar_escenario(
        tmp_path,
        cuentas=300,
        cortes=2,
        semilla=5,
        primer_corte=date(2026, 9, 2),
        formato="csv",
        coordenadas=coordenadas,
    )

    manifiesto = json.loads((tmp_path / "escenario.json").read_text(encoding="utf-8"))
    assert manifiesto["coordenadas"] == {
        "version": VERSION_COORDENADAS,
        "muestra_sha256": "0" * 64,
        "geografia_version_id": "sintetica",
        "sha256_referencia": "1" * 64,
        "proporciones": {c: getattr(Proporciones(), c) for c in CATEGORIAS},
    }
    for corte in manifiesto["cortes"]:
        assert sum(corte["coordenadas"].values()) == corte["cuentas"]
