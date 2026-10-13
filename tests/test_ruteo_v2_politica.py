"""El nucleo puro de ruteo/v2 (ruteo-public-v2), sin base ni red: matrices escritas a mano, con los
resultados esperados calculados a mano, no con el algoritmo que se prueba.

Las horas son de la Ciudad de Mexico el jueves 2026-10-01 (UTC-6, sin horario de verano): las 09:00
locales son las 15:00 UTC. Cada instante esperado se escribe como hora local.
"""

from __future__ import annotations

import hashlib
import json
import random
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from motor_cartera.ruteo_v2 import politica
from motor_cartera.ruteo_v2.politica import (
    PUBLIC_V2,
    EstadoParada,
    EstadoRuta,
    Matriz,
    MotivoParada,
    ParadaEntrada,
    centimetros,
    colocar,
    metros,
    motivo_de_snap,
    normalizar_ventanas,
    planear,
    segundos,
)

INICIO_DIA = int(datetime(2026, 10, 1, 6, 0, tzinfo=UTC).timestamp())
"""La medianoche local del 2026-10-01 en la Ciudad de Mexico, en segundos UTC."""


def h(hora: int, minuto: int = 0, segundo: int = 0) -> int:
    """Una hora local de ese dia, en segundos desde la epoca."""
    return INICIO_DIA + hora * 3600 + minuto * 60 + segundo


def c(n: int) -> UUID:
    """Una cuenta_id: el orden de las cuentas es el de `n`."""
    return UUID(int=n)


def matriz(duraciones: list[list[int | None]], por_segundo: int = 8) -> Matriz:
    """Una matriz con distancias proporcionales a las duraciones: 8 m por segundo, salvo otra."""
    return Matriz(
        duraciones,
        [[None if d is None else d * por_segundo for d in fila] for fila in duraciones],
    )


def parada(n: int, nodo: int | None, prioridad: str = "MUY_ALTA", snap: int | None = 0):
    return ParadaEntrada(c(n), prioridad, snap, nodo)


def por_cuenta(ruta) -> dict[int, object]:
    return {p.clave.int: p for p in ruta.paradas}


JORNADA = [(h(9), h(14))]
PARTIDA = [(h(9), h(14)), (h(16), h(19))]


# --- la politica como datos -----------------------------------------------------------------------


def test_la_politica_se_hashea_con_su_definicion_canonica():
    definicion = PUBLIC_V2.definicion()
    texto = json.dumps(definicion, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    assert politica.hash_politica() == hashlib.sha256(texto.encode("utf-8")).hexdigest()
    assert definicion["version"] == "ruteo-public-v2"
    assert definicion["motor"] == "ruteo/v2"
    assert definicion["algoritmo"] == "PRIORITY_NEAREST_NEIGHBOR_2OPT/v1"
    assert definicion["inicio_fin_base"] is True
    assert definicion["snap"]["max_distancia_m"] == 500
    assert definicion["servicio_s"] == 900
    assert definicion["prioridad"] == ["MUY_ALTA", "ALTA", "MEDIA", "BAJA"]
    assert definicion["2opt"]["max_mejoras_por_segmento"] == 100
    assert definicion["matriz"]["bloque"] == 100
    assert "trafico" in definicion["no_usa"]
    assert "distancia en linea recta" in definicion["no_usa"]
    # Ningun numero de la politica es un float: el hash no depende de una representacion binaria.
    assert "." not in json.dumps({k: v for k, v in definicion.items() if isinstance(v, int)})
    # Cambiar un parametro es otra politica, con otro hash.
    otra = politica.PoliticaRuteo("ruteo-public-v2", 499, 900, 100, 100, "car")
    assert politica.hash_politica(otra) != politica.hash_politica()


def test_la_firma_cambia_con_cada_entrada():
    base = {
        "campo_run_id": c(1),
        "firma_campo": "a" * 64,
        "red_vial_version_id": c(2),
        "huella_red": "b" * 64,
        "hash_manifest": "c" * 64,
        "version_motor_ruteo": "v26.9.0",
        "hash_perfil": "d" * 64,
    }
    firma = politica.firma_inputs(**base)

    assert firma == politica.firma_inputs(**base)
    for llave, valor in (
        ("campo_run_id", c(3)),
        ("firma_campo", "e" * 64),
        ("red_vial_version_id", c(4)),
        ("huella_red", "f" * 64),
        ("hash_manifest", "0" * 64),
        ("version_motor_ruteo", "v26.10.0"),
        ("hash_perfil", "1" * 64),
    ):
        assert politica.firma_inputs(**{**base, llave: valor}) != firma, llave
    assert politica.ruta_id(firma, c(9)) == politica.ruta_id(firma, c(9))
    assert politica.ruta_id(firma, c(9)) != politica.ruta_id(firma, c(10))


# --- la canonicalizacion numerica -----------------------------------------------------------------


def test_los_numeros_de_la_red_se_redondean_como_decimales():
    assert segundos(Decimal("123.4")) == 123
    assert segundos(Decimal("123.5")) == 124
    assert segundos(Decimal("0.5")) == 1
    assert segundos(0) == 0
    assert metros(Decimal("2.5")) == 3
    assert metros(Decimal("1000.49")) == 1000
    assert metros("17") == 17
    # Un float binario no se acepta: 0.1 + 0.2 no es 0.3, y la firma no depende de eso.
    with pytest.raises(TypeError):
        segundos(0.5)
    with pytest.raises(ValueError):
        metros(Decimal("-1"))
    with pytest.raises(ValueError):
        metros(Decimal("NaN"))


@pytest.mark.parametrize(
    ("distancia", "centimetros_esperados", "motivo"),
    [
        (Decimal("0"), 0, None),
        (Decimal("50"), 5000, None),
        (Decimal("499"), 49900, None),
        (Decimal("500"), 50000, None),
        (Decimal("500.004"), 50000, None),
        (Decimal("500.005"), 50001, MotivoParada.SNAP_EXCESIVO),
        (Decimal("500.01"), 50001, MotivoParada.SNAP_EXCESIVO),
        (Decimal("2500"), 250000, MotivoParada.SNAP_EXCESIVO),
    ],
)
def test_la_frontera_del_snap(distancia, centimetros_esperados, motivo):
    # Hasta 500.00 m (al centimetro) se permite; 500.01 m ya no.
    assert centimetros(distancia) == centimetros_esperados
    assert motivo_de_snap(centimetros_esperados) == motivo


def test_sin_snap_no_es_enrutable():
    assert motivo_de_snap(None) == MotivoParada.SIN_SNAP


# --- las ventanas ---------------------------------------------------------------------------------


def test_las_ventanas_se_ordenan_y_las_que_se_tocan_se_unen():
    assert normalizar_ventanas([(h(16), h(19)), (h(9), h(14))]) == ((h(9), h(14)), (h(16), h(19)))
    assert normalizar_ventanas([(h(9), h(14)), (h(14), h(19))]) == ((h(9), h(19)),)
    assert normalizar_ventanas([]) == ()
    with pytest.raises(ValueError):
        normalizar_ventanas([(h(9), h(14)), (h(13), h(17))])
    with pytest.raises(ValueError):
        normalizar_ventanas([(h(10), h(9))])


def test_una_actividad_cabe_entera_en_una_ventana_o_espera_a_la_siguiente():
    ventanas = normalizar_ventanas(PARTIDA)

    assert colocar(h(9), 600, ventanas) == h(9)
    assert colocar(h(8), 600, ventanas) == h(9)
    # Termina justo al cerrar la ventana: [13:50, 14:00] cabe en [09:00, 14:00).
    assert colocar(h(13, 50), 600, ventanas) == h(13, 50)
    # Un segundo despues ya no cabe antes de las 14:00: espera a las 16:00, sin trabajar en el
    # hueco.
    assert colocar(h(13, 50, 1), 600, ventanas) == h(16)
    assert colocar(h(15), 600, ventanas) == h(16)
    # Mas larga que cualquier ventana: no cabe.
    assert colocar(h(9), 6 * 3600, ventanas) is None
    assert colocar(h(18, 55), 600, ventanas) is None


# --- el golden del proveedor: base, A, B, C y D ---------------------------------------------------


def test_golden_de_la_matriz_base_a_b_c_y_d_desconectada():
    # 0 es la base; A, B y C (cuentas 1, 2 y 3) estan en la red; D (4) tiene snap, pero esta en una
    # componente sin camino a la base. Distancias: 8 m por segundo.
    duraciones = [
        [0, 600, 900, 1200, None],
        [600, 0, 300, 900, None],
        [900, 300, 0, 600, None],
        [1200, 900, 600, 0, None],
        [None, None, None, None, 0],
    ]
    ruta = planear(
        [parada(1, 1), parada(2, 2), parada(3, 3), parada(4, 4)],
        JORNADA,
        base_snap_cm=0,
        matriz=matriz(duraciones),
    )

    # El vecino mas cercano: A (10 min), de A a B (5), de B a C (10) y el regreso (20). Ningun
    # 2-opt reduce el traslado: base-C-B-A-base empata (2,700 s) y un empate conserva la ruta.
    assert ruta.estado == EstadoRuta.PARCIAL
    a, b, cc, d = (por_cuenta(ruta)[n] for n in (1, 2, 3, 4))
    assert (a.orden, a.salida_anterior, a.llegada, a.inicio_servicio, a.fin_servicio) == (
        1,
        h(9),
        h(9, 10),
        h(9, 10),
        h(9, 25),
    )
    assert (a.distancia_desde_anterior_m, a.duracion_desde_anterior_s, a.espera_s) == (4800, 600, 0)
    assert (b.orden, b.salida_anterior, b.llegada, b.fin_servicio) == (
        2,
        h(9, 25),
        h(9, 30),
        h(9, 45),
    )
    assert (b.distancia_desde_anterior_m, b.duracion_desde_anterior_s) == (2400, 300)
    assert (cc.orden, cc.llegada, cc.inicio_servicio, cc.fin_servicio) == (
        3,
        h(9, 55),
        h(9, 55),
        h(10, 10),
    )
    assert (d.estado, d.motivo, d.orden, d.llegada) == (
        EstadoParada.NO_ENRUTABLE,
        MotivoParada.RED_DESCONECTADA,
        None,
        None,
    )
    assert (ruta.salida_base, ruta.salida_regreso, ruta.regreso_base) == (
        h(9),
        h(10, 10),
        h(10, 30),
    )
    assert (ruta.distancia_regreso_m, ruta.duracion_regreso_s, ruta.espera_regreso_s) == (
        9600,
        1200,
        0,
    )
    assert ruta.distancia_total_m == 21600
    assert ruta.duracion_traslado_s == 2700
    assert ruta.duracion_servicio_s == 2700
    assert ruta.duracion_espera_s == 0
    assert ruta.duracion_total_s == 5400
    assert ruta.mejoras_2opt == 0
    # Las programadas en su orden, y despues las demas por cuenta.
    assert [p.clave.int for p in ruta.paradas] == [1, 2, 3, 4]
    assert len(ruta.hash_matriz) == len(ruta.hash_secuencia) == 64


def test_la_matriz_de_la_ruta_se_hashea_sin_coordenadas_y_con_cada_par():
    duraciones = [[0, 60, 60], [60, 0, 30], [60, 30, 0]]
    ruta = planear([parada(1, 1), parada(2, 2)], JORNADA, base_snap_cm=0, matriz=matriz(duraciones))
    esperado = {
        "puntos": ["BASE", str(c(1)), str(c(2))],
        "duracion_s": duraciones,
        "distancia_m": [[d * 8 for d in fila] for fila in duraciones],
    }
    texto = json.dumps(esperado, sort_keys=True, separators=(",", ":"))

    assert ruta.hash_matriz == hashlib.sha256(texto.encode()).hexdigest()
    # Una duracion distinta es otra matriz.
    otra = planear(
        [parada(1, 1), parada(2, 2)],
        JORNADA,
        base_snap_cm=0,
        matriz=matriz([[0, 60, 61], [60, 0, 30], [60, 30, 0]]),
    )
    assert otra.hash_matriz != ruta.hash_matriz


# --- el golden de la prioridad --------------------------------------------------------------------


PRIORIDAD = [
    # base, M (MUY_ALTA), A (ALTA), X (MEDIA): X es la mas cercana a todo, y aun asi va al final.
    [0, 300, 300, 100],
    [300, 0, 300, 100],
    [300, 300, 0, 100],
    [100, 100, 100, 0],
]


def test_golden_de_prioridad_la_jornada_permite_dos_visitas():
    # De 09:00 a 09:55: M (09:05-09:20) y A (09:25-09:40) regresan a las 09:45. X, despues de A,
    # terminaria su servicio a las 09:56:40: ya no cabe.
    ruta = planear(
        [parada(1, 1, "MUY_ALTA"), parada(2, 2, "ALTA"), parada(3, 3, "MEDIA")],
        [(h(9), h(9, 55))],
        base_snap_cm=0,
        matriz=matriz(PRIORIDAD),
    )
    m, a, x = (por_cuenta(ruta)[n] for n in (1, 2, 3))

    assert (m.orden, m.llegada, m.fin_servicio) == (1, h(9, 5), h(9, 20))
    assert (a.orden, a.llegada, a.fin_servicio) == (2, h(9, 25), h(9, 40))
    assert (x.estado, x.motivo) == (EstadoParada.SIN_TIEMPO, MotivoParada.JORNADA_AGOTADA)
    assert ruta.regreso_base == h(9, 45)
    assert ruta.estado == EstadoRuta.PARCIAL


def test_una_superior_que_no_cabe_bloquea_a_las_inferiores_aunque_quepan():
    # M1 (a 5 min) cabe; M2 (a 10 min) cabe sola, pero no despues de M1. A (ALTA, a 100 s de M1)
    # cabria despues de M1, pero hay una MUY_ALTA enrutable que se quedo fuera por la jornada.
    duraciones = [
        [0, 300, 600, 100],
        [300, 0, 600, 100],
        [600, 600, 0, 600],
        [100, 100, 600, 0],
    ]
    ruta = planear(
        [parada(1, 1, "MUY_ALTA"), parada(2, 2, "MUY_ALTA"), parada(3, 3, "ALTA")],
        [(h(9), h(9, 40))],
        base_snap_cm=0,
        matriz=matriz(duraciones),
    )
    m1, m2, a = (por_cuenta(ruta)[n] for n in (1, 2, 3))

    assert m1.estado == EstadoParada.PROGRAMADA
    assert (m2.estado, m2.motivo) == (EstadoParada.SIN_TIEMPO, MotivoParada.JORNADA_AGOTADA)
    assert (a.estado, a.motivo) == (
        EstadoParada.SIN_TIEMPO,
        MotivoParada.PRIORIDAD_SUPERIOR_AGOTO_JORNADA,
    )
    assert ruta.programadas == 1


def test_una_superior_no_enrutable_no_bloquea_a_las_inferiores():
    ruta = planear(
        [parada(1, None, "MUY_ALTA", snap=50001), parada(2, 2, "ALTA"), parada(3, 3, "MEDIA")],
        [(h(9), h(9, 55))],
        base_snap_cm=0,
        matriz=matriz(PRIORIDAD),
    )
    m, a, x = (por_cuenta(ruta)[n] for n in (1, 2, 3))

    assert (m.estado, m.motivo) == (EstadoParada.NO_ENRUTABLE, MotivoParada.SNAP_EXCESIVO)
    assert (a.orden, x.orden) == (1, 2)
    assert ruta.estado == EstadoRuta.PARCIAL


def test_una_superior_que_no_cabe_ni_sola_no_bloquea_a_las_inferiores():
    # M esta a 3 h de la base: ida, servicio y regreso no caben en una jornada de 09:00 a 14:00.
    duraciones = [[0, 10800, 300], [10800, 0, 10800], [300, 10800, 0]]
    ruta = planear(
        [parada(1, 1, "MUY_ALTA"), parada(2, 2, "BAJA")],
        JORNADA,
        base_snap_cm=0,
        matriz=matriz(duraciones),
    )
    m, b = (por_cuenta(ruta)[n] for n in (1, 2))

    assert (m.estado, m.motivo) == (EstadoParada.SIN_TIEMPO, MotivoParada.EXCEDE_JORNADA)
    assert (b.estado, b.orden) == (EstadoParada.PROGRAMADA, 1)


# --- el golden del snap, de la red y de la base ---------------------------------------------------


@pytest.mark.parametrize(
    ("snap", "estado", "motivo"),
    [
        (0, EstadoParada.PROGRAMADA, None),
        (5000, EstadoParada.PROGRAMADA, None),
        (49900, EstadoParada.PROGRAMADA, None),
        (50000, EstadoParada.PROGRAMADA, None),
        (50001, EstadoParada.NO_ENRUTABLE, MotivoParada.SNAP_EXCESIVO),
        (None, EstadoParada.NO_ENRUTABLE, MotivoParada.SIN_SNAP),
    ],
)
def test_golden_de_snap_0_50_499_500_500_01_y_sin_snap(snap, estado, motivo):
    enrutable = motivo is None
    ruta = planear(
        [parada(1, 1 if enrutable else None, snap=snap)],
        JORNADA,
        base_snap_cm=0,
        matriz=matriz([[0, 60], [60, 0]]) if enrutable else None,
    )
    (unica,) = ruta.paradas

    assert (unica.estado, unica.motivo, unica.snap_cm) == (estado, motivo, snap)
    assert ruta.estado == (EstadoRuta.COMPLETA if enrutable else EstadoRuta.SIN_PARADAS_ENRUTABLES)


def test_golden_de_red_desconectada_no_es_sin_tiempo():
    # Snap de 3 m, pero sin camino de la base a la parada: NO_ENRUTABLE, nunca SIN_TIEMPO, aunque la
    # jornada sobre.
    ruta = planear(
        [parada(1, 1, snap=300)],
        JORNADA,
        base_snap_cm=0,
        matriz=matriz([[0, None], [None, 0]]),
    )
    (unica,) = ruta.paradas

    assert (unica.estado, unica.motivo) == (
        EstadoParada.NO_ENRUTABLE,
        MotivoParada.RED_DESCONECTADA,
    )
    assert ruta.estado == EstadoRuta.SIN_PARADAS_ENRUTABLES
    assert (ruta.salida_base, ruta.regreso_base, ruta.duracion_total_s) == (None, None, 0)
    # Solo de ida tambien es desconectada: la ruta es cerrada.
    solo_ida = planear([parada(1, 1)], JORNADA, base_snap_cm=0, matriz=matriz([[0, 60], [None, 0]]))
    assert solo_ida.paradas[0].motivo == MotivoParada.RED_DESCONECTADA


@pytest.mark.parametrize(
    ("base_snap", "motivo_base"),
    [(50001, MotivoParada.SNAP_EXCESIVO), (None, MotivoParada.SIN_SNAP)],
)
def test_golden_de_base_no_enrutable(base_snap, motivo_base):
    # La base a mas de 500 m de la red: ninguna parada se rutea, ni se usa la primera cuenta como
    # origen.
    ruta = planear(
        [parada(1, 1), parada(2, 2)],
        JORNADA,
        base_snap_cm=base_snap,
        matriz=matriz([[0, 60, 60], [60, 0, 60], [60, 60, 0]]),
    )

    assert ruta.estado == EstadoRuta.BASE_NO_ENRUTABLE
    assert ruta.motivo_base == motivo_base
    assert ruta.base_snap_cm == base_snap
    assert {(p.estado, p.motivo) for p in ruta.paradas} == {
        (EstadoParada.NO_ENRUTABLE_BASE, MotivoParada.BASE_NO_ENRUTABLE)
    }
    assert (ruta.salida_base, ruta.distancia_total_m, ruta.hash_matriz) == (None, 0, None)


def test_sin_jornada_ninguna_parada_tiene_tiempo():
    ruta = planear([parada(1, None, snap=None)], [], base_snap_cm=None, matriz=None)

    assert ruta.estado == EstadoRuta.SIN_JORNADA
    assert (ruta.paradas[0].estado, ruta.paradas[0].motivo) == (
        EstadoParada.SIN_TIEMPO,
        MotivoParada.SIN_JORNADA,
    )
    assert (ruta.jornada_inicio, ruta.jornada_fin) == (None, None)


# --- el golden de la jornada ----------------------------------------------------------------------


def test_golden_de_jornada_simple_con_eta_servicio_y_regreso():
    # De 09:00 a 14:00. La primera parada esta a 11 minutos por calle: llega a las 09:11 y su
    # servicio termina a las 09:26; la segunda, a 7 minutos de la primera; el regreso, 14 minutos.
    duraciones = [[0, 660, 1200], [660, 0, 420], [840, 420, 0]]
    ruta = planear(
        [parada(1, 1), parada(2, 2)], JORNADA, base_snap_cm=1234, matriz=matriz(duraciones)
    )
    primera, segunda = (por_cuenta(ruta)[n] for n in (1, 2))

    assert (primera.salida_anterior, primera.llegada, primera.fin_servicio) == (
        h(9),
        h(9, 11),
        h(9, 26),
    )
    assert (segunda.salida_anterior, segunda.llegada, segunda.inicio_servicio) == (
        h(9, 26),
        h(9, 33),
        h(9, 33),
    )
    assert segunda.fin_servicio == h(9, 48)
    assert (ruta.salida_regreso, ruta.regreso_base) == (h(9, 48), h(10, 2))
    assert ruta.duracion_total_s == 62 * 60
    assert ruta.base_snap_cm == 1234
    assert ruta.estado == EstadoRuta.COMPLETA


def test_golden_de_jornada_partida_sin_conducir_en_la_pausa():
    # 09:00-14:00 y 16:00-19:00. P1 (MUY_ALTA) esta a 3 h: llega a las 12:00 y termina a las 12:15.
    # P2 (ALTA) esta a 2 h de P1: llegaria a las 14:15, asi que su traslado no empieza en la primera
    # ventana. El gestor espera en P1 y sale a las 16:00: llega a las 18:00, termina a las 18:15 y
    # regresa a la base a las 18:45.
    duraciones = [[0, 10800, 1800], [10800, 0, 7200], [1800, 7200, 0]]
    ruta = planear(
        [parada(1, 1, "MUY_ALTA"), parada(2, 2, "ALTA")],
        PARTIDA,
        base_snap_cm=0,
        matriz=matriz(duraciones),
    )
    p1, p2 = (por_cuenta(ruta)[n] for n in (1, 2))

    assert (p1.orden, p1.salida_anterior, p1.llegada, p1.fin_servicio, p1.espera_s) == (
        1,
        h(9),
        h(12),
        h(12, 15),
        0,
    )
    assert (p2.orden, p2.salida_anterior, p2.llegada, p2.fin_servicio) == (
        2,
        h(16),
        h(18),
        h(18, 15),
    )
    assert p2.espera_s == 3 * 3600 + 45 * 60
    assert (ruta.salida_regreso, ruta.regreso_base) == (h(18, 15), h(18, 45))
    assert ruta.duracion_traslado_s == 19800
    assert ruta.duracion_servicio_s == 1800
    assert ruta.duracion_espera_s == 13500
    assert ruta.duracion_total_s == 35100
    assert (ruta.jornada_inicio, ruta.jornada_fin) == (h(9), h(19))
    # Ningun traslado ni servicio ocurre en el hueco de 14:00 a 16:00.
    for p in ruta.paradas:
        for inicio, fin in ((p.salida_anterior, p.llegada), (p.inicio_servicio, p.fin_servicio)):
            assert fin <= h(14) or inicio >= h(16)


def test_el_regreso_a_la_base_se_reserva_siempre():
    # De 09:00 a 10:00. Q esta a 1 minuto; P, a 20 minutos de la base y de Q. Despues de Q, P
    # terminaria su servicio a las 09:51, dentro de la jornada, pero su regreso llegaria a las
    # 10:11: SIN_TIEMPO. Sola, P si cabe (regresaria a las 09:55).
    duraciones = [[0, 60, 1200], [60, 0, 1200], [1200, 1200, 0]]
    ruta = planear(
        [parada(1, 1), parada(2, 2)], [(h(9), h(10))], base_snap_cm=0, matriz=matriz(duraciones)
    )
    q, p = (por_cuenta(ruta)[n] for n in (1, 2))

    assert (q.orden, q.fin_servicio) == (1, h(9, 16))
    assert (p.estado, p.motivo) == (EstadoParada.SIN_TIEMPO, MotivoParada.JORNADA_AGOTADA)
    assert ruta.regreso_base == h(9, 17)
    sola = planear(
        [parada(2, 1)], [(h(9), h(10))], base_snap_cm=0, matriz=matriz([[0, 1200], [1200, 0]])
    )
    assert sola.regreso_base == h(9, 55)


def test_una_parada_que_cabe_sin_regreso_pero_no_con_el_no_se_programa():
    # A 25 minutos: llega a las 09:25 y termina a las 09:40, antes de las 10:00; con el regreso, a
    # las 10:05. No cabe ni sola.
    ruta = planear(
        [parada(1, 1)], [(h(9), h(10))], base_snap_cm=0, matriz=matriz([[0, 1500], [1500, 0]])
    )

    assert (ruta.paradas[0].estado, ruta.paradas[0].motivo) == (
        EstadoParada.SIN_TIEMPO,
        MotivoParada.EXCEDE_JORNADA,
    )
    assert ruta.estado == EstadoRuta.PARCIAL
    assert ruta.programadas == 0


# --- el 2-opt -------------------------------------------------------------------------------------


DOS_OPT = [
    # base, A, B, C, asimetrica. El vecino mas cercano hace A-C-B (traslado 1,700 s); A-B-C son 600.
    [0, 100, 900, 1000],
    [100, 0, 250, 200],
    [1000, 250, 0, 150],
    [100, 200, 400, 0],
]


def test_golden_del_2opt_reduce_el_traslado_y_recalcula_la_jornada():
    ruta = planear(
        [parada(1, 1), parada(2, 2), parada(3, 3)],
        JORNADA,
        base_snap_cm=0,
        matriz=matriz(DOS_OPT),
    )

    assert [p.clave.int for p in ruta.paradas] == [1, 2, 3]
    assert ruta.mejoras_2opt == 1
    assert ruta.duracion_traslado_s == 600
    a, b, cc = ruta.paradas
    assert (a.llegada, a.fin_servicio) == (h(9, 1, 40), h(9, 16, 40))
    assert (b.llegada, b.fin_servicio) == (h(9, 20, 50), h(9, 35, 50))
    assert (cc.llegada, cc.fin_servicio) == (h(9, 38, 20), h(9, 53, 20))
    assert ruta.regreso_base == h(9, 55)


VENTANAS_2OPT = [(h(9), h(10, 20)), (h(10, 40), h(12, 15))]
ROMPE = [
    # base, A, B, C, en minutos.
    [0, 60, 15, 10],
    [5, 0, 20, 10],
    [30, 60, 0, 30],
    [10, 15, 30, 0],
]


def test_el_2opt_no_acepta_un_cambio_que_rompe_la_jornada():
    # De 09:00 a 10:20 y de 10:40 a 12:15. A-B-C (traslado 120 min) cabe: A de 10:00 a 10:15, B
    # espera la segunda ventana (llega a las 11:00), C de 11:45 a 12:00 y el regreso a las 12:10.
    # La mayor mejora, B-A-C (95 min), no cabe: A llega a las 11:40 y C ya no termina antes de las
    # 12:15. La siguiente, C-B-A (105 min), si: es la que se acepta.
    duraciones = [[m * 60 for m in fila] for fila in ROMPE]
    ventanas = normalizar_ventanas(VENTANAS_2OPT)
    secuencia = [parada(1, 1), parada(2, 2), parada(3, 3)]

    assert _cabe([1, 2, 3], duraciones, ventanas, 900)
    assert not _cabe([2, 1, 3], duraciones, ventanas, 900)
    assert _cabe([3, 2, 1], duraciones, ventanas, 900)
    una, mejoras = politica._dos_opt(secuencia, duraciones, ventanas, 900, 1)
    assert ([p.clave.int for p in una], mejoras) == ([3, 2, 1], 1)
    # Sin tope, despues de C-B-A sigue B-C-A (65 min), que tambien cabe; y ahi converge.
    assert _cabe([2, 3, 1], duraciones, ventanas, 900)
    todas, mejoras = politica._dos_opt(secuencia, duraciones, ventanas, 900, 100)
    assert ([p.clave.int for p in todas], mejoras) == ([2, 3, 1], 2)


def test_el_2opt_nunca_cruza_prioridades():
    # C y B son MUY_ALTA y A es ALTA: la construccion hace C-B-A (traslado 1,900 s). Llevar A antes
    # de B (C-A-B) dejaria el traslado en 280 s, pero cruzaria prioridades: no se hace. Invertir el
    # segmento MUY_ALTA (B-C-A) no reduce el traslado.
    duraciones = [
        [0, 600, 900, 100],
        [600, 0, 60, 60],
        [60, 600, 0, 600],
        [100, 60, 600, 0],
    ]
    ruta = planear(
        [parada(1, 1, "ALTA"), parada(2, 2, "MUY_ALTA"), parada(3, 3, "MUY_ALTA")],
        JORNADA,
        base_snap_cm=0,
        matriz=matriz(duraciones),
    )

    assert [p.clave.int for p in ruta.paradas] == [3, 2, 1]
    assert [p.prioridad for p in ruta.paradas] == ["MUY_ALTA", "MUY_ALTA", "ALTA"]
    assert ruta.duracion_traslado_s == 1900
    assert ruta.mejoras_2opt == 0
    assert politica._traslado([3, 1, 2], duraciones) == 280


# --- el desempate y el orden de entrada -----------------------------------------------------------


def test_un_empate_de_duracion_se_resuelve_por_cuenta_id():
    # Dos paradas a 5 minutos de la base y entre si: va primero la de cuenta_id menor, sin importar
    # en que orden llegan ni en que punto de la matriz estan.
    duraciones = [[0, 300, 300], [300, 0, 300], [300, 300, 0]]
    for paradas in (
        [parada(0x20, 1), parada(0x10, 2)],
        [parada(0x10, 2), parada(0x20, 1)],
    ):
        ruta = planear(paradas, JORNADA, base_snap_cm=0, matriz=matriz(duraciones))
        assert [p.clave.int for p in ruta.paradas] == [0x10, 0x20]


def test_la_misma_matriz_en_otro_orden_de_entrada_da_la_misma_ruta():
    generador = random.Random(7)
    n = 9
    duraciones = [
        [0 if i == j else generador.randint(60, 1800) for j in range(n + 1)] for i in range(n + 1)
    ]
    prioridades = ["MUY_ALTA", "ALTA", "MEDIA", "BAJA"]
    paradas = [parada(100 + i, i, prioridades[i % 4]) for i in range(1, n + 1)]
    referencia = planear(paradas, PARTIDA, base_snap_cm=0, matriz=matriz(duraciones))
    for _ in range(5):
        revueltas = paradas[:]
        generador.shuffle(revueltas)
        otra = planear(revueltas, PARTIDA, base_snap_cm=0, matriz=matriz(duraciones))
        assert otra == referencia


# --- una, ninguna, completa y parcial -------------------------------------------------------------


def test_una_sola_parada_va_y_vuelve():
    ruta = planear(
        [parada(1, 1)],
        JORNADA,
        base_snap_cm=0,
        matriz=Matriz([[0, 700], [650, 0]], [[0, 5100], [4900, 0]]),
    )
    (unica,) = ruta.paradas

    assert ruta.distancia_total_m == 5100 + 4900
    assert ruta.duracion_traslado_s == 700 + 650
    assert ruta.duracion_total_s == 700 + 900 + 650 + ruta.duracion_espera_s
    assert ruta.duracion_espera_s == 0
    assert (unica.orden, ruta.estado) == (1, EstadoRuta.COMPLETA)


def test_ruta_completa_y_ruta_parcial():
    duraciones = [[0, 300, 300], [300, 0, 300], [300, 300, 0]]
    completa = planear(
        [parada(1, 1), parada(2, 2)], JORNADA, base_snap_cm=0, matriz=matriz(duraciones)
    )
    parcial = planear(
        [parada(1, 1), parada(2, 2)], [(h(9), h(9, 30))], base_snap_cm=0, matriz=matriz(duraciones)
    )

    assert completa.estado == EstadoRuta.COMPLETA
    assert completa.programadas == 2
    assert parcial.estado == EstadoRuta.PARCIAL
    assert [p.estado for p in parcial.paradas] == [EstadoParada.PROGRAMADA, EstadoParada.SIN_TIEMPO]


def test_una_ruta_sin_paradas_no_existe():
    with pytest.raises(ValueError):
        planear([], JORNADA, base_snap_cm=0, matriz=None)
    with pytest.raises(ValueError):
        planear([parada(1, 1), parada(1, 2)], JORNADA, base_snap_cm=0, matriz=matriz([[0] * 3] * 3))
    with pytest.raises(ValueError):
        planear([parada(1, 1, "URGENTE")], JORNADA, base_snap_cm=0, matriz=matriz([[0, 1], [1, 0]]))
    with pytest.raises(ValueError):
        # Con snap valido y sin punto en la matriz.
        planear([parada(1, None)], JORNADA, base_snap_cm=0, matriz=matriz([[0]]))


def test_los_puntos_de_la_matriz_son_unicos_y_en_orden_canonico():
    coordenadas = {
        c(3): ("-98.200000", "19.000000"),
        c(1): ("-98.100000", "19.000000"),
        c(2): ("-98.100000", "19.000000"),
        c(4): ("-98.300000", "19.000000"),
    }
    snaps = {c(1): 0, c(2): 10, c(3): 50001, c(4): None}

    puntos, nodos = politica.puntos_de_matriz(coordenadas, snaps)

    assert puntos == [("-98.100000", "19.000000")]
    assert nodos == {c(1): 1, c(2): 1}


# --- propiedades, contra un simulador escrito aparte ----------------------------------------------


def _cabe(secuencia, duraciones, ventanas, servicio):
    """Un simulador independiente del de la politica: recorre la secuencia minuto a minuto de
    actividad, con las ventanas como intervalos cerrados por la derecha para el fin."""
    t, actual = ventanas[0][0], 0

    def primera(desde, d):
        for inicio, fin in ventanas:
            arranque = desde if desde > inicio else inicio
            if arranque + d <= fin:
                return arranque
        return None

    for nodo in secuencia:
        ida = duraciones[actual][nodo]
        if ida is None:
            return False
        sale = primera(t, ida)
        if sale is None:
            return False
        empieza = primera(sale + ida, servicio)
        if empieza is None:
            return False
        t, actual = empieza + servicio, nodo
    regreso = duraciones[actual][0]
    return regreso is not None and primera(t, regreso) is not None


@pytest.mark.parametrize("semilla", range(40))
def test_propiedades_de_cualquier_ruta(semilla):
    generador = random.Random(semilla)
    n = generador.randint(1, 12)
    duraciones = [
        [
            0 if i == j else (None if generador.random() < 0.03 else generador.randint(30, 2400))
            for j in range(n + 1)
        ]
        for i in range(n + 1)
    ]
    prioridades = ["MUY_ALTA", "ALTA", "MEDIA", "BAJA"]
    claves = generador.sample(range(1, 10**9), n)
    paradas = [parada(claves[i - 1], i, generador.choice(prioridades)) for i in range(1, n + 1)]
    ventanas = normalizar_ventanas(generador.choice([JORNADA, PARTIDA, [(h(9), h(11))]]))
    ruta = planear(paradas, ventanas, base_snap_cm=0, matriz=matriz(duraciones))
    programadas = [p for p in ruta.paradas if p.estado == EstadoParada.PROGRAMADA]
    nodo = {p.clave: p.nodo for p in paradas}

    # 1. Lo programado cabe, segun un simulador escrito aparte.
    if programadas:
        assert _cabe([nodo[p.clave] for p in programadas], duraciones, ventanas, 900)
    # 2. La prioridad nunca se invierte en el orden de visita.
    niveles = [prioridades.index(p.prioridad) for p in programadas]
    assert niveles == sorted(niveles)
    # 3. Si una enrutable se quedo sin tiempo por la jornada, ninguna inferior esta programada.
    agotadas = [p for p in ruta.paradas if p.motivo == MotivoParada.JORNADA_AGOTADA]
    for agotada in agotadas:
        nivel = prioridades.index(agotada.prioridad)
        assert all(prioridades.index(p.prioridad) <= nivel for p in programadas)
    # 4. Los totales cuadran y cada parada esta exactamente una vez.
    assert ruta.duracion_total_s == (
        ruta.duracion_traslado_s + ruta.duracion_servicio_s + ruta.duracion_espera_s
    )
    assert sorted(p.clave for p in ruta.paradas) == sorted(p.clave for p in paradas)
    assert [p.orden for p in programadas] == list(range(1, len(programadas) + 1))
    for p in ruta.paradas:
        assert (p.motivo is None) == (p.estado == EstadoParada.PROGRAMADA)
        assert p.motivo is None or p.motivo in politica.MOTIVOS_DEL_ESTADO[p.estado]
    # 5. Mismo resultado otra vez.
    assert planear(paradas, ventanas, base_snap_cm=0, matriz=matriz(duraciones)) == ruta
