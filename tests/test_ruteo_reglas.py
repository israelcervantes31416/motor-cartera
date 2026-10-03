"""ruteo/v1 sin base de datos: las coordenadas sinteticas exactas, la ruta exacta, cada desempate y
lo que no cambia.

Los casos golden se calcularon aparte, con una implementacion independiente que recalcula por
fuerza bruta la longitud completa de cada ruta candidata, y se escribieron aqui como numeros: las
pruebas no calculan lo esperado con las funciones que prueban. Si una regla cambia, cambian estos
resultados: eso es una version nueva de las reglas, no una prueba que corregir.

El ruteo no decide que cuentas van a campo ni que municipio va primero: recibe los clientes de
campo de un municipio y los ordena en una ruta. Por eso aqui no hay cuentas, decisiones ni
PostgreSQL.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import random
import subprocess
import sys
import time
from dataclasses import FrozenInstanceError, asdict, fields
from decimal import Decimal
from itertools import permutations
from pathlib import Path

import pytest

from motor_cartera.contratos import VERSION_CONTRATO
from motor_cartera.decision.reglas import VERSION_REGLAS_DECISION
from motor_cartera.ruteo import reglas
from motor_cartera.ruteo.reglas import (
    COORDENADA_MAXIMA_M,
    DEPOSITO,
    MAX_PASADAS_2OPT,
    VERSION_REGLAS_RUTEO,
    ParadaCalculada,
    PuntoSintetico,
    ResultadoRuta,
    coordenada_sintetica,
    distancia_m,
    rutear_territorio,
)
from motor_cartera.territorial.reglas import VERSION_REGLAS_TERRITORIAL


def _clientes(desde: int, hasta: int) -> list[str]:
    """Los clientes CU00000<desde> a CU00000<hasta>, los dos incluidos, como los numera el
    generador de las pruebas."""
    return [f"CU{numero:08d}" for numero in range(desde, hasta + 1)]


def _parada(cliente: str, secuencia: int, x: int, y: int, desde_anterior: int) -> ParadaCalculada:
    return ParadaCalculada(cliente, secuencia, x, y, desde_anterior)


# --- una implementacion de referencia, independiente y lenta ----------------------------------
#
# Solo para comparar: no usa las funciones del modulo y recalcula cada ruta completa, sin la formula
# de los cuatro bordes.


def _manhattan(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _longitud_completa(orden: list[str], puntos: dict[str, tuple[int, int]]) -> int:
    recorrido = [(0, 0), *(puntos[cliente] for cliente in orden), (0, 0)]
    return sum(_manhattan(recorrido[k], recorrido[k + 1]) for k in range(len(recorrido) - 1))


def _invertida(orden: list[str], i: int, j: int) -> list[str]:
    return orden[:i] + orden[i : j + 1][::-1] + orden[j + 1 :]


def _ahorros(orden: list[str], puntos: dict[str, tuple[int, int]]) -> dict[tuple[int, int], int]:
    """Cuanto acorta la ruta cada inversion i..j, rehaciendo la ruta entera."""
    base = _longitud_completa(orden, puntos)
    return {
        (i, j): base - _longitud_completa(_invertida(orden, i, j), puntos)
        for i in range(len(orden))
        for j in range(i + 1, len(orden))
    }


def _como_tuplas(puntos: dict[str, PuntoSintetico]) -> dict[str, tuple[int, int]]:
    return {cliente: (punto.x_m, punto.y_m) for cliente, punto in puntos.items()}


def _a_mano(puntos: dict[str, tuple[int, int]]) -> dict[str, PuntoSintetico]:
    """Puntos escritos a mano, para probar la geometria sin pasar por el SHA-256."""
    return {cliente: PuntoSintetico(x, y) for cliente, (x, y) in puntos.items()}


# --- golden: las coordenadas sinteticas -------------------------------------------------------

COORDENADAS = [
    # (clave_territorio, cliente_unico, x_m, y_m)
    ("21114", "CU00000001", 2558, -312),
    ("21114", "CU00000002", -592, -3817),
    ("09002", "CU00000001", -4394, 418),  # el mismo cliente, en otro municipio: otro punto
    ("21074", "CU0000004521", 4283, -3537),  # 12 caracteres, como los del generador
    ("15033", "ABCDEFGH", 3057, 1542),  # 8 caracteres, el minimo
    ("30087", "ZZZZZZZZZZZZZZZZZZZZ", 4619, -3307),  # 20, el maximo
    ("00000", "00000000", 4427, 2638),
    ("99999", "CU99999999", -4477, -4764),
]


@pytest.mark.parametrize(
    ("clave", "cliente", "x", "y"),
    [pytest.param(*caso, id=f"{caso[0]}-{caso[1]}") for caso in COORDENADAS],
)
def test_cada_coordenada_sale_exacta(clave, cliente, x, y):
    punto = coordenada_sintetica(clave, cliente)

    assert punto == PuntoSintetico(x, y)
    assert type(punto.x_m) is int and type(punto.y_m) is int


def test_la_coordenada_sale_del_sha256_de_la_version_el_municipio_y_el_cliente():
    # El digest de "ruteo/v1|21114|CU00000001", como lo da sha256sum, y de ahi los dos ejes: los
    # primeros 8 bytes son x, los 8 siguientes, y; cada uno sin signo, big-endian, modulo 10,001,
    # menos 5,000.
    digest = "6dbb87b9e88500397a46dc210f98424d"
    assert hashlib.sha256(b"ruteo/v1|21114|CU00000001").hexdigest().startswith(digest)
    assert (int(digest[:16], 16) % 10001 - 5000, int(digest[16:], 16) % 10001 - 5000) == (
        2558,
        -312,
    )

    assert coordenada_sintetica("21114", "CU00000001") == PuntoSintetico(2558, -312)


def test_el_municipio_entra_en_la_coordenada():
    # Dos clientes que en 21114 caen en el mismo punto, y en 21074, en puntos distintos. Si el
    # municipio no entrara en el calculo, tendrian que coincidir tambien ahi.
    assert coordenada_sintetica("21114", "CU00001758") == PuntoSintetico(-289, -1243)
    assert coordenada_sintetica("21114", "CU00011403") == PuntoSintetico(-289, -1243)
    assert coordenada_sintetica("21074", "CU00001758") == PuntoSintetico(67, 4571)
    assert coordenada_sintetica("21074", "CU00011403") == PuntoSintetico(-3947, -180)


def test_la_version_entra_en_la_coordenada(monkeypatch):
    # Otra version de las reglas mueve el punto: ruteo/v2 no hereda los puntos de ruteo/v1.
    antes = coordenada_sintetica("21114", "CU00000001")
    monkeypatch.setattr(reglas, "VERSION_REGLAS_RUTEO", "ruteo/v2")

    despues = coordenada_sintetica("21114", "CU00000001")

    assert despues != antes
    digest = hashlib.sha256(b"ruteo/v2|21114|CU00000001").digest()
    assert despues == PuntoSintetico(
        int.from_bytes(digest[:8], "big") % 10001 - 5000,
        int.from_bytes(digest[8:16], "big") % 10001 - 5000,
    )


def test_las_coordenadas_cubren_el_plano_y_no_se_salen_de_el():
    # 3,000 clientes de un municipio: todos dentro de [-5000, 5000], y los dos ejes llegan cerca de
    # las dos orillas. Un modulo mas chico, o uno sin restar, se notaria aqui.
    puntos = [coordenada_sintetica("21114", cliente) for cliente in _clientes(1, 3000)]
    xs = [punto.x_m for punto in puntos]
    ys = [punto.y_m for punto in puntos]

    assert all(-5000 <= valor <= 5000 for valor in xs + ys)
    assert min(xs) < -4990 and max(xs) > 4990
    assert min(ys) < -4990 and max(ys) > 4990


# --- golden: la distancia ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b", "metros"),
    [
        pytest.param((0, 0), (3000, 4000), 7000, id="no-es-euclidiana"),  # Euclidiana: 5000
        pytest.param((1016, -2070), (1712, 1261), 4027, id="dos-paradas-del-golden"),
        pytest.param((0, 0), (-3825, 3808), 7633, id="desde-el-deposito"),
        pytest.param((-5000, -5000), (5000, 5000), 20000, id="esquinas-opuestas"),
        pytest.param((1712, 1261), (1712, 1261), 0, id="el-mismo-punto"),
    ],
)
def test_la_distancia_es_manhattan_exacta_y_entera(a, b, metros):
    p, q = PuntoSintetico(*a), PuntoSintetico(*b)

    assert distancia_m(p, q) == distancia_m(q, p) == metros
    assert type(distancia_m(p, q)) is int


def test_la_distancia_solo_se_mide_entre_puntos_sinteticos():
    with pytest.raises(TypeError, match="PuntoSintetico"):
        distancia_m((0, 0), PuntoSintetico(1, 1))
    with pytest.raises(TypeError, match="PuntoSintetico"):
        distancia_m(PuntoSintetico(1, 1), {"x_m": 0, "y_m": 0})


# --- golden: una ruta completa ----------------------------------------------------------------
#
# Cinco clientes de 21114. El vecino mas cercano empieza bien (CU00000044 es el mas cercano al
# deposito), pero se va al norte y deja CU00000041, al sur, para el final. El 2-opt lo corrige en
# dos inversiones.

GOLDEN_CLAVE = "21114"
GOLDEN_CLIENTES = _clientes(41, 45)
GOLDEN_PUNTOS = {
    # cliente: (x_m, y_m), a esta distancia del deposito
    "CU00000041": (1016, -2070),  # 3086
    "CU00000042": (966, 3811),  # 4777
    "CU00000043": (4679, 2833),  # 7512
    "CU00000044": (1712, 1261),  # 2973
    "CU00000045": (-3825, 3808),  # 7633
}
GOLDEN_VECINO_MAS_CERCANO = ["CU00000044", "CU00000042", "CU00000043", "CU00000041", "CU00000045"]
GOLDEN_RUTA = ResultadoRuta(
    clave_territorio="21114",
    paradas=(
        _parada("CU00000041", 1, 1016, -2070, 3086),
        _parada("CU00000044", 2, 1712, 1261, 4027),
        _parada("CU00000043", 3, 4679, 2833, 4539),
        _parada("CU00000042", 4, 966, 3811, 4691),
        _parada("CU00000045", 5, -3825, 3808, 4794),
    ),
    distancia_inicial_m=37878,
    distancia_total_m=28770,  # 3086 + 4027 + 4539 + 4691 + 4794 + 7633
    distancia_regreso_deposito_m=7633,
    mejora_2opt_m=9108,
)


def test_los_puntos_del_golden_son_los_del_sha256():
    assert {c: coordenada_sintetica(GOLDEN_CLAVE, c) for c in GOLDEN_CLIENTES} == _a_mano(
        GOLDEN_PUNTOS
    )


def test_la_ruta_golden_sale_exacta():
    assert rutear_territorio(GOLDEN_CLAVE, GOLDEN_CLIENTES) == GOLDEN_RUTA


def test_el_vecino_mas_cercano_del_golden_sale_exacto():
    # Desde el deposito, CU00000044 (2973) le gana a CU00000041 (3086); desde ahi, CU00000042 (3296)
    # a CU00000041 (4027); despues CU00000043 (4691) y CU00000041 (8566); CU00000045 queda al final.
    # Mide 2973 + 3296 + 4691 + 8566 + 10719 + 7633.
    puntos = _a_mano(GOLDEN_PUNTOS)

    inicial = reglas._vecino_mas_cercano(puntos)

    assert inicial == GOLDEN_VECINO_MAS_CERCANO
    assert reglas._longitud(inicial, puntos) == 37878


def test_el_2opt_del_golden_paso_a_paso():
    puntos = _a_mano(GOLDEN_PUNTOS)
    ruta = list(GOLDEN_VECINO_MAS_CERCANO)

    # Primera pasada: invertir CU00000042..CU00000041 ahorra 5194, mas que cualquier otra.
    assert reglas._mejor_inversion(ruta, puntos) == (1, 3)
    assert max(_ahorros(ruta, GOLDEN_PUNTOS).values()) == 5194
    ruta = _invertida(ruta, 1, 3)
    assert ruta == ["CU00000044", "CU00000041", "CU00000043", "CU00000042", "CU00000045"]
    assert reglas._longitud(ruta, puntos) == 32684

    # Segunda: invertir CU00000044..CU00000041 ahorra 3914.
    assert reglas._mejor_inversion(ruta, puntos) == (0, 1)
    ruta = _invertida(ruta, 0, 1)
    assert reglas._longitud(ruta, puntos) == 28770

    # Tercera: ya ninguna inversion acorta la ruta.
    assert reglas._mejor_inversion(ruta, puntos) is None
    assert max(_ahorros(ruta, GOLDEN_PUNTOS).values()) <= 0
    assert reglas._dos_opt(GOLDEN_VECINO_MAS_CERCANO, puntos) == (ruta, 2)


# --- el 2-opt: cuando mejora, cuando no y cuanto ----------------------------------------------


def test_el_2opt_acorta_la_ruta_del_vecino_mas_cercano():
    ruta = rutear_territorio(GOLDEN_CLAVE, GOLDEN_CLIENTES)

    assert ruta.distancia_total_m < ruta.distancia_inicial_m
    assert ruta.mejora_2opt_m == ruta.distancia_inicial_m - ruta.distancia_total_m > 0


def test_una_ruta_que_ya_es_optima_no_cambia():
    # Cuatro clientes de 09002: el vecino mas cercano ya da la ruta mas corta de las 24 posibles.
    clientes = _clientes(5, 8)

    ruta = rutear_territorio("09002", clientes)

    assert ruta == ResultadoRuta(
        clave_territorio="09002",
        paradas=(
            _parada("CU00000005", 1, -342, -1717, 2059),
            _parada("CU00000007", 2, 4843, -3910, 7378),
            _parada("CU00000008", 3, 4268, 1943, 6428),
            _parada("CU00000006", 4, -2633, 4020, 8978),
        ),
        distancia_inicial_m=31496,
        distancia_total_m=31496,
        distancia_regreso_deposito_m=6653,
        mejora_2opt_m=0,
    )
    puntos = _como_tuplas({cliente: coordenada_sintetica("09002", cliente) for cliente in clientes})
    assert min(_longitud_completa(list(orden), puntos) for orden in permutations(clientes)) == 31496


# Treinta clientes de 21074: sin tope, el 2-opt aplicaria 12 inversiones. Con el de ruteo/v1 se
# detiene en la decima, aunque la undecima todavia acortaria la ruta.
TOPE_CLAVE = "21074"
TOPE_CLIENTES = _clientes(271, 300)
TOPE_DISTANCIAS = {8: 63150, 9: 62894, 10: 62688, 11: 62548, 12: 62442}


def test_el_2opt_aplica_a_lo_mas_diez_inversiones():
    ruta = rutear_territorio(TOPE_CLAVE, TOPE_CLIENTES)

    assert (ruta.distancia_inicial_m, ruta.distancia_total_m, ruta.mejora_2opt_m) == (
        72210,
        TOPE_DISTANCIAS[10],
        9522,
    )
    puntos = {c: coordenada_sintetica(TOPE_CLAVE, c) for c in TOPE_CLIENTES}
    final, aplicadas = reglas._dos_opt(reglas._vecino_mas_cercano(puntos), puntos)
    assert aplicadas == MAX_PASADAS_2OPT == 10
    assert final == [parada.cliente_unico for parada in ruta.paradas]
    # La undecima habria acortado la ruta: el tope es el que la detuvo, no que ya no hubiera mejora.
    assert reglas._mejor_inversion(final, puntos) is not None


@pytest.mark.parametrize("tope", [8, 9, 11, 12])
def test_otro_tope_daria_otra_ruta(monkeypatch, tope):
    # El tope decide: con otro, la misma entrada mide otra cosa. Por eso es parte de la version.
    monkeypatch.setattr(reglas, "MAX_PASADAS_2OPT", tope)

    ruta = rutear_territorio(TOPE_CLAVE, TOPE_CLIENTES)

    assert ruta.distancia_inicial_m == 72210
    assert ruta.distancia_total_m == TOPE_DISTANCIAS[tope] != TOPE_DISTANCIAS[10]


def test_cada_inversion_aplicada_acorta_estrictamente(monkeypatch):
    # Con tope 1, 2, ..., 10: cada inversion mas deja la ruta estrictamente mas corta.
    distancias = []
    for tope in range(0, 11):
        monkeypatch.setattr(reglas, "MAX_PASADAS_2OPT", tope)
        distancias.append(rutear_territorio(TOPE_CLAVE, TOPE_CLIENTES).distancia_total_m)

    assert distancias[0] == 72210
    assert all(antes > despues for antes, despues in zip(distancias, distancias[1:], strict=False))
    assert distancias[8:] == [TOPE_DISTANCIAS[8], TOPE_DISTANCIAS[9], TOPE_DISTANCIAS[10]]


# --- desempates -------------------------------------------------------------------------------


def test_a_igual_distancia_del_deposito_va_primero_el_cliente_menor():
    # CU00000005 (680, 224) y CU00000288 (-432, -472) estan a 904 del deposito.
    clientes = ["CU00000288", "CU00000005"]

    ruta = rutear_territorio("21114", clientes)

    assert ruta == ResultadoRuta(
        clave_territorio="21114",
        paradas=(
            _parada("CU00000005", 1, 680, 224, 904),
            _parada("CU00000288", 2, -432, -472, 1808),
        ),
        distancia_inicial_m=3616,
        distancia_total_m=3616,
        distancia_regreso_deposito_m=904,
        mejora_2opt_m=0,
    )


def test_a_igual_distancia_de_la_parada_actual_va_primero_el_cliente_menor():
    # Desde CU00000002 (-592, -3817), la primera parada, CU00000045 (-3825, 3808) y CU00000131
    # (1933, 4516) estan a 10858. El vecino mas cercano elige CU00000045; con el desempate al
    # reves, la ruta inicial mediria 29366.
    clientes = ["CU00000131", "CU00000045", "CU00000002"]
    puntos = {c: coordenada_sintetica("21114", c) for c in clientes}

    assert reglas._vecino_mas_cercano(puntos) == ["CU00000002", "CU00000045", "CU00000131"]
    assert rutear_territorio("21114", clientes) == ResultadoRuta(
        clave_territorio="21114",
        paradas=(
            _parada("CU00000002", 1, -592, -3817, 4409),
            _parada("CU00000045", 2, -3825, 3808, 10858),
            _parada("CU00000131", 3, 1933, 4516, 6466),
        ),
        distancia_inicial_m=28182,
        distancia_total_m=28182,
        distancia_regreso_deposito_m=6449,
        mejora_2opt_m=0,
    )


def test_a_igual_ahorro_gana_la_inversion_de_i_menor():
    # Tres inversiones ahorran lo mismo, 4000: (0, 1), (1, 3) y (2, 3). Gana la de i menor.
    puntos = {"A": (2000, 2000), "B": (3000, -3000), "C": (-2000, 3000), "D": (2000, 4000)}
    ruta = ["A", "B", "C", "D"]
    ahorros = _ahorros(ruta, puntos)

    assert sorted(inversion for inversion, ahorro in ahorros.items() if ahorro == 4000) == [
        (0, 1),
        (1, 3),
        (2, 3),
    ]
    assert max(ahorros.values()) == 4000
    assert reglas._mejor_inversion(ruta, _a_mano(puntos)) == (0, 1)


def test_a_igual_ahorro_e_igual_i_gana_la_inversion_de_j_menor():
    # (0, 1), (0, 2) y (1, 2) ahorran 4000. Entre las dos de i = 0, gana la de j menor.
    puntos = {"A": (-3000, -4000), "B": (-1000, -1000), "C": (-3000, 1000), "D": (4000, -2000)}
    ruta = ["A", "B", "C", "D"]
    ahorros = _ahorros(ruta, puntos)

    assert sorted(inversion for inversion, ahorro in ahorros.items() if ahorro == 4000) == [
        (0, 1),
        (0, 2),
        (1, 2),
    ]
    assert max(ahorros.values()) == 4000
    assert reglas._mejor_inversion(ruta, _a_mano(puntos)) == (0, 1)


def test_la_mejor_inversion_es_la_de_la_fuerza_bruta_con_su_desempate():
    # 400 rutas al azar sobre una cuadricula chica, donde los empates abundan: la inversion elegida
    # es la de mayor ahorro estrictamente positivo, y a igual ahorro, la de i y despues j menores.
    # El azar es solo de la prueba, con semilla fija; las reglas no lo usan.
    azar = random.Random(31416)
    empatadas = 0
    for _ in range(400):
        nombres = [f"P{k}" for k in range(azar.randint(1, 7))]
        puntos = {n: (azar.randint(-3, 3) * 1000, azar.randint(-3, 3) * 1000) for n in nombres}
        ahorros = _ahorros(nombres, puntos)
        positivos = {k: v for k, v in ahorros.items() if v > 0}
        if positivos:
            mayor = max(positivos.values())
            ganadoras = sorted(k for k, v in positivos.items() if v == mayor)
            empatadas += len(ganadoras) > 1
            esperada = ganadoras[0]
        else:
            esperada = None

        assert reglas._mejor_inversion(nombres, _a_mano(puntos)) == esperada
    assert empatadas > 20  # la prueba de verdad paso por empates


# --- casos borde ------------------------------------------------------------------------------


def test_una_ruta_de_un_solo_cliente_va_y_regresa():
    ruta = rutear_territorio("21114", ["CU00000001"])

    assert ruta == ResultadoRuta(
        clave_territorio="21114",
        paradas=(_parada("CU00000001", 1, 2558, -312, 2870),),
        distancia_inicial_m=5740,
        distancia_total_m=5740,
        distancia_regreso_deposito_m=2870,
        mejora_2opt_m=0,
    )


def test_una_ruta_de_dos_clientes_no_se_puede_mejorar():
    # Con dos paradas solo hay dos recorridos, uno el reverso del otro: miden lo mismo.
    ruta = rutear_territorio("21114", ["CU00000002", "CU00000001"])

    assert [parada.cliente_unico for parada in ruta.paradas] == ["CU00000001", "CU00000002"]
    assert (ruta.distancia_inicial_m, ruta.distancia_total_m, ruta.mejora_2opt_m) == (
        13934,
        13934,
        0,
    )
    assert [p.distancia_desde_anterior_m for p in ruta.paradas] == [2870, 6655]
    assert ruta.distancia_regreso_deposito_m == 4409


def test_un_cliente_en_el_mismo_punto_que_el_deposito():
    # Ningun SHA-256 conocido cae en (0, 0); se prueba con puntos escritos a mano. El cliente del
    # deposito es la primera parada, a cero metros, y la ruta sigue desde ahi.
    puntos = _a_mano({"ENELDEPOSITO": (0, 0), "ALESTE0001": (3000, 1000)})

    ruta = reglas._rutear("21114", puntos)

    assert ruta == ResultadoRuta(
        clave_territorio="21114",
        paradas=(
            _parada("ENELDEPOSITO", 1, 0, 0, 0),
            _parada("ALESTE0001", 2, 3000, 1000, 4000),
        ),
        distancia_inicial_m=8000,
        distancia_total_m=8000,
        distancia_regreso_deposito_m=4000,
        mejora_2opt_m=0,
    )


def test_dos_clientes_con_la_misma_coordenada_sintetica():
    # CU00001758 y CU00011403 caen en el mismo punto de 21114. Los dos son paradas: el menor
    # primero, y el otro a cero metros de el.
    ruta = rutear_territorio("21114", ["CU00011403", "CU00001758"])

    assert ruta == ResultadoRuta(
        clave_territorio="21114",
        paradas=(
            _parada("CU00001758", 1, -289, -1243, 1532),
            _parada("CU00011403", 2, -289, -1243, 0),
        ),
        distancia_inicial_m=3064,
        distancia_total_m=3064,
        distancia_regreso_deposito_m=1532,
        mejora_2opt_m=0,
    )


# --- lo que no cambia -------------------------------------------------------------------------


def test_el_orden_de_llegada_no_cambia_la_ruta():
    # Las 120 formas de ordenar los cinco clientes del golden.
    for orden in permutations(GOLDEN_CLIENTES):
        assert rutear_territorio(GOLDEN_CLAVE, orden) == GOLDEN_RUTA


@pytest.mark.parametrize(
    "como_llegan",
    [
        pytest.param(list, id="list"),
        pytest.param(tuple, id="tuple"),
        pytest.param(lambda clientes: (c for c in clientes), id="generador"),
        pytest.param(lambda clientes: list(reversed(clientes)), id="orden-reverso"),
        pytest.param(lambda clientes: dict.fromkeys(clientes), id="dict"),
        pytest.param(set, id="set"),
        pytest.param(frozenset, id="frozenset"),  # su orden depende del hash
    ],
)
def test_cualquier_iterable_da_la_misma_ruta(como_llegan):
    assert rutear_territorio(GOLDEN_CLAVE, como_llegan(GOLDEN_CLIENTES)) == GOLDEN_RUTA
    assert rutear_territorio(TOPE_CLAVE, como_llegan(TOPE_CLIENTES)) == rutear_territorio(
        TOPE_CLAVE, TOPE_CLIENTES
    )


def test_rutear_no_toca_la_coleccion_que_recibe():
    clientes = list(reversed(GOLDEN_CLIENTES))
    antes = list(clientes)

    rutear_territorio(GOLDEN_CLAVE, clientes)

    assert clientes == antes


def _como_se_guarda(ruta: ResultadoRuta) -> str:
    return json.dumps(asdict(ruta))


def test_la_misma_entrada_da_siempre_la_misma_ruta():
    # Igual cada vez, tambien byte por byte.
    rutas = [rutear_territorio(TOPE_CLAVE, TOPE_CLIENTES) for _ in range(5)]

    assert all(ruta == rutas[0] for ruta in rutas)
    assert len({_como_se_guarda(ruta) for ruta in rutas}) == 1


# La misma ruta en otro proceso, con otra semilla de hash y con los clientes en un frozenset, cuyo
# orden depende de esa semilla: una ruta que dependiera del orden de llegada, o del hash() de
# Python, saldria distinta.
RUTEAR_EN_OTRO_PROCESO = """
import json, sys
from dataclasses import asdict
from motor_cartera.ruteo.reglas import rutear_territorio

clave, clientes = json.load(sys.stdin)
llegada = frozenset(clientes)
print(json.dumps({"llegada": list(llegada), "ruta": asdict(rutear_territorio(clave, llegada))}))
"""


def test_la_misma_ruta_sale_en_otro_proceso_con_otra_semilla_de_hash():
    esperada = json.loads(_como_se_guarda(rutear_territorio(TOPE_CLAVE, TOPE_CLIENTES)))
    llegadas = []

    for semilla in ("0", "1", "31416"):
        proceso = subprocess.run(
            [sys.executable, "-c", RUTEAR_EN_OTRO_PROCESO],
            input=json.dumps([TOPE_CLAVE, TOPE_CLIENTES]),
            env={**os.environ, "PYTHONHASHSEED": semilla},
            capture_output=True,
            text=True,
            check=True,
        )
        salida = json.loads(proceso.stdout)
        assert salida["ruta"] == esperada
        llegadas.append(salida["llegada"])

    # Los clientes de verdad llegaron en otro orden en cada proceso.
    assert len({tuple(llegada) for llegada in llegadas}) > 1


def test_no_usa_el_azar_el_reloj_ni_el_entorno(monkeypatch):
    # Si las reglas tocaran cualquiera de estos, la prueba reventaria.
    def prohibido(*_argumentos, **_opciones):
        raise AssertionError("ruteo/v1 no puede usar el azar, el reloj ni el entorno")

    for modulo, nombre in [
        (random, "random"),
        (random, "randint"),
        (random, "randrange"),
        (random, "choice"),
        (random, "shuffle"),
        (random, "getrandbits"),
        (time, "time"),
        (time, "time_ns"),
        (time, "monotonic"),
        (time, "perf_counter"),
        (os, "getenv"),
        (os, "urandom"),
    ]:
        monkeypatch.setattr(modulo, nombre, prohibido)

    assert rutear_territorio(GOLDEN_CLAVE, GOLDEN_CLIENTES) == GOLDEN_RUTA


# --- invariantes ------------------------------------------------------------------------------

MUNICIPIOS = ["21114", "21074", "09002", "15033", "30087"]
TAMANOS = [1, 2, 3, 5, 8, 13, 21, 34]


@pytest.mark.parametrize("clave", MUNICIPIOS)
@pytest.mark.parametrize("n", TAMANOS)
def test_toda_ruta_cumple_sus_invariantes(clave, n):
    clientes = _clientes(1000 + 37 * n, 1000 + 37 * n + n - 1)
    puntos = {c: coordenada_sintetica(clave, c) for c in clientes}

    ruta = rutear_territorio(clave, clientes)

    paradas = ruta.paradas
    assert type(paradas) is tuple and len(paradas) == n
    assert [parada.secuencia for parada in paradas] == list(range(1, n + 1))
    assert sorted(parada.cliente_unico for parada in paradas) == sorted(clientes)
    # Cada parada en su punto, y a la distancia exacta de la anterior; la primera, del deposito.
    anterior = DEPOSITO
    for parada in paradas:
        punto = puntos[parada.cliente_unico]
        assert (parada.x_m, parada.y_m) == (punto.x_m, punto.y_m)
        assert parada.distancia_desde_anterior_m == distancia_m(anterior, punto)
        anterior = punto
    assert ruta.distancia_regreso_deposito_m == distancia_m(anterior, DEPOSITO)
    # Las distancias cuadran entre si.
    suma = sum(parada.distancia_desde_anterior_m for parada in paradas)
    assert ruta.distancia_total_m == suma + ruta.distancia_regreso_deposito_m
    assert ruta.distancia_total_m <= ruta.distancia_inicial_m
    assert ruta.mejora_2opt_m == ruta.distancia_inicial_m - ruta.distancia_total_m >= 0
    orden = [parada.cliente_unico for parada in paradas]
    assert _longitud_completa(orden, _como_tuplas(puntos)) == ruta.distancia_total_m
    numeros = [ruta.distancia_inicial_m, ruta.distancia_total_m, ruta.distancia_regreso_deposito_m]
    numeros += [ruta.mejora_2opt_m]
    for parada in paradas:
        numeros += [parada.secuencia, parada.x_m, parada.y_m, parada.distancia_desde_anterior_m]
    assert all(type(numero) is int for numero in numeros)
    assert all(type(parada.cliente_unico) is str for parada in paradas)


@pytest.mark.parametrize("clave", MUNICIPIOS)
@pytest.mark.parametrize("n", TAMANOS)
def test_si_el_2opt_se_detiene_antes_del_tope_ninguna_inversion_acorta_la_ruta(clave, n):
    # La fuerza bruta lo confirma: cuando el 2-opt termina por si solo, la ruta es localmente
    # optima. Y la distancia inicial es la del vecino mas cercano de la fuerza bruta.
    clientes = _clientes(1000 + 37 * n, 1000 + 37 * n + n - 1)
    puntos = {c: coordenada_sintetica(clave, c) for c in clientes}
    inicial = reglas._vecino_mas_cercano(puntos)

    final, aplicadas = reglas._dos_opt(inicial, puntos)

    tuplas = _como_tuplas(puntos)
    assert rutear_territorio(clave, clientes).distancia_inicial_m == _longitud_completa(
        inicial, tuplas
    )
    if aplicadas < MAX_PASADAS_2OPT:
        assert all(ahorro <= 0 for ahorro in _ahorros(final, tuplas).values())


# --- validacion: lo que no entra --------------------------------------------------------------


@pytest.mark.parametrize(
    ("clave", "error"),
    [
        pytest.param(21114, TypeError, id="int"),
        pytest.param(True, TypeError, id="bool"),
        pytest.param(b"21114", TypeError, id="bytes"),
        pytest.param(None, TypeError, id="None"),
        pytest.param("2111", ValueError, id="cuatro-digitos"),
        pytest.param("211144", ValueError, id="seis-digitos"),
        pytest.param("2111A", ValueError, id="letra"),
        pytest.param(" 21114", ValueError, id="espacio-antes"),
        pytest.param("21114\n", ValueError, id="salto-de-linea"),
        pytest.param("", ValueError, id="vacia"),
        pytest.param("٢١١١٤", ValueError, id="arabigo-indica"),
        pytest.param("２１１１４", ValueError, id="ancho-completo"),
        pytest.param("2111²", ValueError, id="superindice"),
    ],
)
def test_una_clave_de_territorio_mal_formada_no_entra(clave, error):
    with pytest.raises(error, match="clave_territorio"):
        rutear_territorio(clave, ["CU00000001"])
    with pytest.raises(error, match="clave_territorio"):
        coordenada_sintetica(clave, "CU00000001")


@pytest.mark.parametrize(
    ("cliente", "error"),
    [
        pytest.param(12345678, TypeError, id="int"),
        pytest.param(True, TypeError, id="bool"),
        pytest.param(b"CU00000001", TypeError, id="bytes"),
        pytest.param(None, TypeError, id="None"),
        pytest.param("CU00001", ValueError, id="siete-caracteres"),
        pytest.param("C" * 21, ValueError, id="veintiun-caracteres"),
        pytest.param("cu00000001", ValueError, id="minusculas"),
        pytest.param("CU 0000001", ValueError, id="espacio"),
        pytest.param("CU-0000001", ValueError, id="guion"),
        pytest.param("CU00000001\n", ValueError, id="salto-de-linea"),
        pytest.param("CUÑ0000001", ValueError, id="no-ascii"),
        pytest.param("ＣＵ０００００１", ValueError, id="ancho-completo"),
        pytest.param("", ValueError, id="vacio"),
    ],
)
def test_un_cliente_mal_formado_no_entra(cliente, error):
    with pytest.raises(error, match="cliente_unico"):
        rutear_territorio("21114", ["CU00000001", cliente])
    with pytest.raises(error, match="cliente_unico"):
        coordenada_sintetica("21114", cliente)


@pytest.mark.parametrize("cliente", ["ABCDEFGH", "Z" * 20, "00000000", "CU0000004521"])
def test_un_cliente_de_8_a_20_mayusculas_y_digitos_entra(cliente):
    (parada,) = rutear_territorio("21114", [cliente]).paradas

    assert parada.cliente_unico == cliente


@pytest.mark.parametrize(
    "clientes", [pytest.param("CU00000001", id="str"), pytest.param(b"CU00000001", id="bytes")]
)
def test_un_texto_no_es_una_coleccion_de_clientes(clientes):
    # Iterarlo daria letras, y cada letra pasaria por un cliente.
    with pytest.raises(TypeError, match="coleccion de cliente_unico"):
        rutear_territorio("21114", clientes)


@pytest.mark.parametrize(
    "vacia",
    [pytest.param([], id="list"), pytest.param((), id="tuple"), pytest.param(iter(()), id="iter")],
)
def test_una_ruta_sin_clientes_no_existe(vacia):
    # A diferencia de territorial/v1, una coleccion vacia no da un resultado vacio: un municipio
    # sin cuentas de campo no tiene ruta.
    with pytest.raises(ValueError, match="al menos un cliente"):
        rutear_territorio("21114", vacia)


@pytest.mark.parametrize(
    "clientes",
    [
        pytest.param(["CU00000001", "CU00000001"], id="seguidos"),
        pytest.param(["CU00000001", "CU00000002", "CU00000001"], id="separados"),
        pytest.param([*GOLDEN_CLIENTES, GOLDEN_CLIENTES[0]], id="al-final"),
    ],
)
def test_un_cliente_repetido_no_se_descarta(clientes):
    with pytest.raises(ValueError, match="viene mas de una vez"):
        rutear_territorio("21114", clientes)


@pytest.mark.parametrize(
    ("valor", "error"),
    [
        pytest.param(True, TypeError, id="bool"),
        pytest.param(1.0, TypeError, id="float"),
        pytest.param(Decimal("1"), TypeError, id="Decimal"),
        pytest.param("1", TypeError, id="texto"),
        pytest.param(None, TypeError, id="None"),
        pytest.param(5001, ValueError, id="mas-de-5000"),
        pytest.param(-5001, ValueError, id="menos-de-5000"),
    ],
)
@pytest.mark.parametrize("campo", ["x_m", "y_m"])
def test_un_punto_fuera_del_plano_o_que_no_es_entero_no_existe(campo, valor, error):
    with pytest.raises(error, match=campo):
        PuntoSintetico(**{"x_m": 0, "y_m": 0, campo: valor})


@pytest.mark.parametrize("valor", [-5000, 0, 5000])
def test_las_orillas_del_plano_si_son_puntos(valor):
    assert PuntoSintetico(valor, -valor) == PuntoSintetico(x_m=valor, y_m=-valor)


@pytest.mark.parametrize(
    ("objeto", "campo", "valor"),
    [
        pytest.param(DEPOSITO, "x_m", 1, id="PuntoSintetico"),
        pytest.param(GOLDEN_RUTA.paradas[0], "secuencia", 0, id="ParadaCalculada"),
        pytest.param(GOLDEN_RUTA, "distancia_total_m", 0, id="ResultadoRuta"),
    ],
)
def test_puntos_paradas_y_rutas_no_se_pueden_modificar(objeto, campo, valor):
    with pytest.raises(FrozenInstanceError):
        setattr(objeto, campo, valor)


# --- el contrato del modulo -------------------------------------------------------------------


def test_la_version_es_propia_y_las_constantes_estan_fijas():
    # Los golden de este archivo son los de ruteo/v1.
    assert VERSION_REGLAS_RUTEO == "ruteo/v1"
    assert VERSION_REGLAS_RUTEO not in {
        VERSION_CONTRATO,
        VERSION_REGLAS_DECISION,
        VERSION_REGLAS_TERRITORIAL,
    }
    assert (COORDENADA_MAXIMA_M, MAX_PASADAS_2OPT) == (5000, 10)
    assert type(COORDENADA_MAXIMA_M) is int and type(MAX_PASADAS_2OPT) is int
    assert DEPOSITO == PuntoSintetico(0, 0)


def test_los_modelos_traen_exactamente_sus_campos():
    # La ruta dice clientes, puntos y distancias, nada mas: ni gestores, ni horarios, ni tiempos,
    # ni latitud y longitud.
    assert [campo.name for campo in fields(PuntoSintetico)] == ["x_m", "y_m"]
    assert [campo.name for campo in fields(ParadaCalculada)] == [
        "cliente_unico",
        "secuencia",
        "x_m",
        "y_m",
        "distancia_desde_anterior_m",
    ]
    assert [campo.name for campo in fields(ResultadoRuta)] == [
        "clave_territorio",
        "paradas",
        "distancia_inicial_m",
        "distancia_total_m",
        "distancia_regreso_deposito_m",
        "mejora_2opt_m",
    ]


INFRAESTRUCTURA = {
    "sqlmodel",
    "sqlalchemy",
    "fastapi",
    "starlette",
    "psycopg",
    "pydantic",
    "pandas",
    "pandera",
    "numpy",
}

NUCLEO = {"motor_cartera", "motor_cartera.ruteo", "motor_cartera.ruteo.reglas"}

IMPORTAR_EN_LIMPIO = """
import importlib, json, sys

antes = set(sys.modules)
importlib.import_module(sys.argv[1])
print(json.dumps(sorted(set(sys.modules) - antes)))
"""


@pytest.mark.parametrize("modulo", sorted(NUCLEO - {"motor_cartera"}))
def test_el_nucleo_solo_carga_la_biblioteca_estandar(modulo):
    # En un proceso aparte, aislado: el de pytest ya tiene cargado todo lo demas.
    proceso = subprocess.run(
        [sys.executable, "-I", "-c", IMPORTAR_EN_LIMPIO, modulo],
        capture_output=True,
        text=True,
        check=True,
    )
    cargados = set(json.loads(proceso.stdout))
    raices = {nombre.partition(".")[0] for nombre in cargados}

    assert not raices & INFRAESTRUCTURA
    assert raices - sys.stdlib_module_names == {"motor_cartera"}
    # Ni decision, ni territorial, ni db, ni la API, ni la configuracion: el paquete y sus reglas.
    assert {nombre for nombre in cargados if nombre.startswith("motor_cartera")} == NUCLEO


BIBLIOTECA_PERMITIDA = {
    "__future__",
    "collections.abc",
    "dataclasses",
    "enum",
    "hashlib",
    "typing",
}

# Lo que dependeria del proceso, de la maquina o de afuera: hash() cambia con la semilla de cada
# proceso, id() con la memoria, y los demas leen o ejecutan algo que no es la entrada.
NOMBRES_PROHIBIDOS = {"hash", "id", "open", "input", "eval", "exec", "compile", "__import__"}


def _arbol(ruta: Path) -> ast.Module:
    return ast.parse(ruta.read_text(encoding="utf-8"))


def _importa(ruta: Path) -> set[str]:
    """Todo lo que importa un archivo, en cualquier parte: tambien dentro de una funcion."""
    importados: set[str] = set()
    for nodo in ast.walk(_arbol(ruta)):
        if isinstance(nodo, ast.Import):
            importados |= {alias.name for alias in nodo.names}
        elif isinstance(nodo, ast.ImportFrom):
            importados.add("." * nodo.level + (nodo.module or ""))
    return importados


def test_el_nucleo_no_importa_nada_fuera_de_la_biblioteca_permitida():
    # Ni random, ni time, ni datetime, ni os: lo que no se importa no se puede usar. Importar en
    # limpio solo ve lo que se carga al importar; un import dentro de una funcion solo se ve leyendo
    # el codigo.
    ruta = Path(reglas.__file__)

    assert _importa(ruta) <= BIBLIOTECA_PERMITIDA
    assert _importa(ruta.with_name("__init__.py")) == {"motor_cartera.ruteo.reglas"}


def test_el_nucleo_no_usa_hash_ni_nada_que_dependa_del_proceso():
    nombres = {
        nodo.id for nodo in ast.walk(_arbol(Path(reglas.__file__))) if isinstance(nodo, ast.Name)
    }

    assert not nombres & NOMBRES_PROHIBIDOS
