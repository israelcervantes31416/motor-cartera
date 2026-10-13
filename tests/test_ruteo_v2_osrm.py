"""ruteo/v2 contra OSRM de verdad: la red vial sintetica (`ruteo_v2.sintetica`) preparada por OSRM
v26.9.0 con el perfil car fijado y servida por osrm-routed. Nada de esto es un mock: el snap, la
matriz, la ruta, las distancias, las duraciones y la conectividad las calcula OSRM.

Corre si el entorno declara el backend y la red: `MC_OSRM_URL_PRUEBAS` (el osrm-routed que la sirve)
y `MC_RED_VIAL_PRUEBAS` (su directorio preparado, el de su huella). El CI los prepara con la imagen
de Docker fijada; una maquina sin Docker, con los binarios nativos (ver docs/red_vial.md). Sin
ellos, estas pruebas se saltan: la red sintetica se genera, pero no se descarga nada.

Lo esperado sale de la geometria de la red sintetica: tramos de 1,000 m, una calle de un solo
sentido hacia el norte y un tramo aislado. Las distancias de OSRM se comparan con una tolerancia de
1 %: la red se dibuja en grados con una aproximacion local.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path

import pytest
from ruteo_v2_escenarios import (
    CuentaRuta,
    base,
    borrar,
    coordenada,
    escenario,
    gestor,
    paradas,
    red_de_pruebas,
    resultado_logico,
    rutas,
    rutear,
)

from motor_cartera.db.sesion import sesion
from motor_cartera.ruteo_v2 import red_vial
from motor_cartera.ruteo_v2.proveedor import (
    BackendIncompatible,
    ClienteOsrm,
    ConfiguracionOsrm,
    _cuerpo,
    data_version,
)
from motor_cartera.ruteo_v2.sintetica import BASE_DE_HUMO, PUNTOS_DE_HUMO

URL = os.environ.get("MC_OSRM_URL_PRUEBAS")
DIRECTORIO = os.environ.get("MC_RED_VIAL_PRUEBAS")

pytestmark = pytest.mark.skipif(
    not (URL and DIRECTORIO),
    reason="sin MC_OSRM_URL_PRUEBAS y MC_RED_VIAL_PRUEBAS no hay un OSRM con la red sintetica",
)


@pytest.fixture(scope="module")
def huella() -> str:
    return json.loads((Path(DIRECTORIO) / "identidad.json").read_bytes())["huella"]


@pytest.fixture(scope="module")
def osrm() -> ClienteOsrm:
    return ClienteOsrm(ConfiguracionOsrm(URL, reintentos=1))


def cerca(valor: Decimal | None, esperado: float) -> bool:
    return valor is not None and abs(float(valor) - esperado) <= esperado * 0.01 + 0.5


def test_el_backend_declara_la_red_que_se_preparo(osrm, huella):
    assert osrm.salud()
    assert osrm.data_version() == data_version(huella)
    osrm.sesion(huella).verificar()
    with pytest.raises(BackendIncompatible):
        osrm.sesion("0" * 64).verificar()


def test_el_snap_es_la_distancia_a_la_calle_mas_cercana(osrm, huella):
    snaps = osrm.sesion(huella).snaps(
        [
            coordenada(0, 0),  # un cruce
            coordenada(500, 3_100),  # 100 m al norte de la ultima calle
            coordenada(1_500, 3_800),  # 800 m al norte: mas de 500
            coordenada(6_500, 1_550),  # 50 m del tramo aislado
        ]
    )

    assert snaps[0] is not None and snaps[0] < Decimal("0.5")
    assert cerca(snaps[1], 100)
    assert cerca(snaps[2], 800)
    assert cerca(snaps[3], 50)
    # Un solo punto es la misma consulta, con la misma respuesta.
    assert osrm.sesion(huella).snaps([coordenada(500, 3_100)]) == [snaps[1]]


def test_la_matriz_sigue_las_calles_y_su_sentido(osrm, huella):
    a, b, c, d, aislado = (
        coordenada(0, 0),
        coordenada(1_000, 0),
        coordenada(3_000, 0),
        coordenada(3_000, 3_000),
        coordenada(6_500, 1_500),
    )

    m = osrm.sesion(huella).matriz([a, b, c, d, aislado], bloque=100)

    assert m.duracion[0][0] == 0 and m.distancia[0][0] == 0
    assert cerca(m.distancia[0][1], 1_000) and m.duracion[0][1] > 0
    # La ultima columna es de un solo sentido, hacia el norte: de C a D son 3,000 m; de D a C hay
    # que rodear por la columna anterior, 5,000 m.
    assert cerca(m.distancia[2][3], 3_000)
    assert cerca(m.distancia[3][2], 5_000)
    assert m.duracion[3][2] > m.duracion[2][3]
    # El tramo aislado no tiene camino con la cuadricula, en ningun sentido.
    for i in range(4):
        assert m.duracion[i][4] is None and m.duracion[4][i] is None
        assert m.distancia[i][4] is None and m.distancia[4][i] is None
    # Por bloques de 2 x 2 (nueve peticiones con origenes y destinos), la misma matriz.
    assert osrm.sesion(huella).matriz([a, b, c, d, aislado], bloque=2) == m


def ruta_de_osrm(osrm: ClienteOsrm, huella: str, puntos, **opciones) -> dict:
    red = osrm.sesion(huella)
    respuesta = osrm._pedir("route", _cuerpo(puntos, overview="false", **opciones), red.metricas)
    assert respuesta["code"] == "Ok"
    assert respuesta["data_version"] == data_version(huella)
    (ruta,) = respuesta["routes"]
    assert "geometry" not in ruta
    return ruta


def test_la_ruta_de_osrm_mide_lo_mismo_que_la_matriz(osrm, huella):
    """Un tramo de la matriz es la ruta de OSRM entre sus dos puntos, al decimetro y a la decima
    de segundo. Y una ruta con paradas es la suma de sus tramos si en cada parada se puede salir en
    cualquier sentido (continue_straight=false): asi las trata ruteo/v2, porque en cada una el
    gestor se estaciona. El perfil car, por omision, sigue de frente en cada parada intermedia, y
    eso solo puede costar mas."""
    puntos = [coordenada(0, 0), coordenada(1_000, 1_000), coordenada(2_000, 0)]
    m = osrm.sesion(huella).matriz(puntos, bloque=100)

    for i, j in ((0, 1), (1, 2), (2, 0), (0, 2)):
        ruta = ruta_de_osrm(osrm, huella, [puntos[i], puntos[j]])
        assert (ruta["distance"], ruta["duration"]) == (m.distancia[i][j], m.duracion[i][j])
    con_paradas = ruta_de_osrm(osrm, huella, puntos, continue_straight=False)
    assert cerca(con_paradas["distance"], 4_000)
    assert con_paradas["distance"] == m.distancia[0][1] + m.distancia[1][2]
    assert con_paradas["duration"] == m.duracion[0][1] + m.duracion[1][2]
    de_frente = ruta_de_osrm(osrm, huella, puntos)
    assert de_frente["duration"] >= con_paradas["duration"]
    # Por la calle de un solo sentido: de norte a sur, la ruta rodea, y la matriz tambien.
    norte, sur = coordenada(3_000, 3_000), coordenada(3_000, 0)
    ruta = ruta_de_osrm(osrm, huella, [norte, sur])
    m = osrm.sesion(huella).matriz([norte, sur], bloque=100)
    assert (ruta["distance"], ruta["duration"]) == (m.distancia[0][1], m.duracion[0][1])
    assert cerca(ruta["distance"], 5_000)


@pytest.mark.usefixtures("bd")
def test_ruteo_v2_de_punta_a_punta_contra_osrm(tmp_path, osrm, huella):
    cuentas = [CuentaRuta(100 + i, x, y) for i, (x, y) in enumerate(PUNTOS_DE_HUMO)]
    plan = escenario(tmp_path, cuentas, [base("BASE_A", *BASE_DE_HUMO)], [gestor("GESTOR_A")]).campo
    with sesion() as s:
        registro = red_vial.registrar(s, Path(DIRECTORIO))
        s.commit()
        s.refresh(registro.version)
        red = registro.version
    assert red.huella == huella

    e = rutear(plan, red, osrm)

    assert e.estado == "EXITOSA", e.detalle
    assert e.paradas_objetivo == len(PUNTOS_DE_HUMO)
    publicadas = paradas(e.id)
    excesiva, aislada = 100 + len(PUNTOS_DE_HUMO) - 2, 100 + len(PUNTOS_DE_HUMO) - 1
    assert (publicadas[excesiva]["estado"], publicadas[excesiva]["motivo"]) == (
        "NO_ENRUTABLE",
        "SNAP_EXCESIVO",
    )
    assert publicadas[excesiva]["snap_distancia_m"] > 500
    assert (publicadas[aislada]["estado"], publicadas[aislada]["motivo"]) == (
        "NO_ENRUTABLE",
        "RED_DESCONECTADA",
    )
    programadas = [p for p in publicadas.values() if p["estado"] == "PROGRAMADA"]
    assert len(programadas) == len(PUNTOS_DE_HUMO) - 2
    assert all(p["snap_distancia_m"] <= 200 for p in programadas)
    ruta = rutas(e.id)["GESTOR_A"]
    assert ruta["estado_ruta"] == "PARCIAL"
    assert ruta["base_snap_distancia_m"] < 1
    assert ruta["distancia_total_m"] == (
        sum(p["distancia_desde_anterior_m"] for p in programadas) + ruta["distancia_regreso_m"]
    )
    assert ruta["duracion_total_s"] == (
        ruta["duracion_traslado_s"] + ruta["duracion_servicio_s"] + ruta["duracion_espera_s"]
    )
    assert e.distribucion["matrices"]["matrices"] == 1
    assert e.distribucion["snap"]["excesivos"] == 1

    # Con las mismas entradas, OSRM da exactamente lo mismo.
    antes = resultado_logico(e.id)
    borrar(e.id)
    reconstruida = rutear(plan, red, osrm)
    assert reconstruida.firma_inputs == e.firma_inputs
    assert resultado_logico(reconstruida.id) == antes

    # Otra red (una SINTETICO, la de un proveedor de pruebas) contra este backend: falla cerrado.
    otra = red_de_pruebas("Una red que este OSRM no sirve")
    fallida = rutear(plan, otra, osrm)
    assert (fallida.estado, fallida.resultado) == ("FALLIDA", "RED_VIAL_BACKEND_INCOMPATIBLE")
