"""campo-public-v2 como nucleo puro, sin base: la cuadricula y su frontera, los identificadores de
la particion, el ranking Rendezvous y su estabilidad, el reparto por prioridad, por bloques y con
spillover, la firma y la politica como datos con su hash."""

from __future__ import annotations

import random
from collections import Counter
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4, uuid5

import pytest

from motor_cartera.campo_v2 import politica
from motor_cartera.campo_v2.politica import (
    ESPACIO_CAMPO,
    PUBLIC_V2,
    Bloque,
    Candidata,
    EstadoCampo,
    EstadoJornada,
    MotivoCampo,
    PoliticaCampo,
    asignar,
    celda,
    hash_politica,
    metros,
    orden_de_bloques,
    particion,
    ranking,
    repartir,
)

M = UUID("00000000-0000-5000-8000-000000000001")


def _microtramos(n: int, semilla: int = 31416) -> list[UUID]:
    azar = random.Random(semilla)
    return [UUID(int=azar.getrandbits(128), version=5) for _ in range(n)]


# --- la cuadricula --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("x", "tramo", "microtramo"),
    [
        ("0", 0, 0),
        ("999.999", 0, 0),
        ("1000", 0, 1),
        ("4999.99", 0, 4),
        ("4999.9994", 0, 4),
        # Al milimetro, con los empates lejos del cero: 4,999.9995 es 5,000.000.
        ("4999.9995", 1, 5),
        ("5000", 1, 5),
        ("5000.00", 1, 5),
        ("5000.01", 1, 5),
        ("9999.999", 1, 9),
        ("-0.0004", 0, 0),
        ("-0.0005", -1, -1),
        ("-0.001", -1, -1),
        ("-5000", -1, -5),
        ("-5000.001", -2, -6),
    ],
)
def test_la_celda_k_es_el_intervalo_que_empieza_en_k_por_el_tamano(x, tramo, microtramo):
    """floor(x / tamano) sobre la coordenada al milimetro: [k * tamano, (k + 1) * tamano). Un
    punto exactamente en la frontera es de la celda que empieza ahi."""
    assert celda(x, PUBLIC_V2.tamano_tramo_m) == tramo
    assert celda(x, PUBLIC_V2.tamano_microtramo_m) == microtramo


def test_de_un_lado_y_otro_de_la_frontera_de_un_tramo_son_dos_tramos():
    assert celda("4999.99", 5_000) == 0
    assert celda("5000.01", 5_000) == 1
    assert celda("5000.00", 5_000) == 1
    # Lo mismo a la escala real de EPSG:6372, sobre la frontera x = 2,900,000 (el tramo 580).
    assert celda("2899999.99", 5_000) == 579
    assert celda("2900000.00", 5_000) == 580
    assert celda("2900000.01", 5_000) == 580


def test_un_punto_que_vuelve_de_una_transformacion_un_nanometro_antes_de_la_frontera_es_de_ella():
    """Un punto construido exactamente en x = 2,900,000 vuelve de ida y vuelta a EPSG:4326 como
    2,899,999.9999999995: sin el milimetro, caeria en el tramo anterior por un error de redondeo."""
    vuelta = 2_899_999.9999999995
    assert metros(vuelta) == Decimal("2900000.000")
    assert celda(vuelta, 5_000) == 580
    assert celda(vuelta, 1_000) == 2_900


def test_la_coordenada_se_toma_como_postgresql_toma_un_double_precision():
    """15 digitos significativos y despues el milimetro: 1,234,567.0004999998 es 1,234,567.0005 y
    redondea a .001, como CAST(x AS numeric) y round(..., 3) en PostgreSQL."""
    assert metros(1_234_567.0004999998) == Decimal("1234567.001")
    assert metros(1_234_567.00049999) == Decimal("1234567.000")
    assert metros(0.1) == Decimal("0.100")
    with pytest.raises(ValueError):
        metros(float("nan"))
    with pytest.raises(ValueError):
        metros(float("inf"))


@pytest.mark.parametrize("ix", range(-12, 13))
def test_el_tramo_de_un_microtramo_es_exactamente_el_que_lo_contiene(ix):
    """5,000 es multiplo de 1,000: la celda de 5 km de cualquier punto del microtramo ix es
    floor(ix / 5), sin un solo punto en dos tramos."""
    for desplazamiento in ("0", "0.001", "500", "999.999"):
        x = Decimal(ix * 1_000) + Decimal(desplazamiento)
        assert celda(x, 1_000) == ix
        assert celda(x, 5_000) == ix // 5


# --- los identificadores --------------------------------------------------------------------------


def test_los_identificadores_son_uuid5_de_la_politica_el_nivel_la_base_y_la_clave():
    p = particion("BASE_A", "21114", 2_897, 787)

    assert p == particion("BASE_A", "21114", 2_897, 787)
    for unidad in (p.zona_id, p.subzona_id, p.tramo_id, p.microtramo_id):
        assert unidad.version == 5
    assert p.zona_id == uuid5(ESPACIO_CAMPO, "campo-public-v2:ZONA:BASE_A:21")
    assert p.subzona_id == uuid5(ESPACIO_CAMPO, "campo-public-v2:SUBZONA:BASE_A:21114")
    assert p.tramo_id == uuid5(ESPACIO_CAMPO, "campo-public-v2:TRAMO:BASE_A:21114:579:157")
    assert p.microtramo_id == uuid5(
        ESPACIO_CAMPO, "campo-public-v2:MICROTRAMO:BASE_A:21114:2897:787"
    )


def test_la_jerarquia_de_la_particion():
    base = particion("BASE_A", "21114", 2_897, 787)
    mismo_microtramo = particion("BASE_A", "21114", 2_897, 787)
    otro_microtramo = particion("BASE_A", "21114", 2_898, 787)
    otro_tramo = particion("BASE_A", "21114", 2_902, 787)
    otro_municipio = particion("BASE_A", "21015", 2_915, 790)
    otra_entidad = particion("BASE_A", "31050", 3_780, 1_050)
    otra_base = particion("BASE_B", "21114", 2_897, 787)

    assert mismo_microtramo == base
    assert (otro_microtramo.tramo_id, otro_microtramo.microtramo_id) == (
        base.tramo_id,
        otro_microtramo.microtramo_id,
    )
    assert otro_microtramo.microtramo_id != base.microtramo_id
    assert otro_tramo.subzona_id == base.subzona_id and otro_tramo.tramo_id != base.tramo_id
    assert otro_municipio.zona_id == base.zona_id
    assert otro_municipio.subzona_id != base.subzona_id
    assert otra_entidad.zona_id != base.zona_id
    assert {
        getattr(otra_base, n) for n in ("zona_id", "subzona_id", "tramo_id", "microtramo_id")
    }.isdisjoint({base.zona_id, base.subzona_id, base.tramo_id, base.microtramo_id})


def test_otra_politica_da_otros_identificadores():
    otra = PoliticaCampo("campo-public-v3", 6372, 4_000, 1_000)

    assert particion("BASE_A", "21114", 2_897, 787, otra) != particion(
        "BASE_A", "21114", 2_897, 787
    )
    # Con tramos de 4 km, el microtramo 2,897 es del tramo 724, no del 579.
    assert particion("BASE_A", "21114", 2_897, 787, otra).tramo_id == uuid5(
        ESPACIO_CAMPO, "campo-public-v3:TRAMO:BASE_A:21114:724:196"
    )


# --- la politica como datos -----------------------------------------------------------------------


def test_la_politica_declara_su_srid_y_sus_tamanos_y_cambiarlos_cambia_su_hash():
    definicion = PUBLIC_V2.definicion()

    assert definicion["particion"]["srid"] == 6372
    assert (definicion["particion"]["tramo_m"], definicion["particion"]["microtramo_m"]) == (
        5_000,
        1_000,
    )
    assert definicion["particion"]["espacio"] == str(ESPACIO_CAMPO)
    assert hash_politica() == hash_politica(PUBLIC_V2)
    for otra in (
        PoliticaCampo("campo-public-v2", 6372, 4_000, 1_000),
        PoliticaCampo("campo-public-v2", 6372, 5_000, 500),
        PoliticaCampo("campo-public-v2", 6370, 5_000, 1_000),
    ):
        assert hash_politica(otra) != hash_politica()


@pytest.mark.parametrize(("tramo", "microtramo"), [(5_000, 3_000), (5_000, 0), (1_000, 2_000)])
def test_un_tramo_que_no_es_multiplo_del_microtramo_no_es_una_politica(tramo, microtramo):
    with pytest.raises(ValueError):
        PoliticaCampo("campo-mala", 6372, tramo, microtramo)


def test_la_firma_cambia_con_cada_entrada():
    base = {
        "corte_id": uuid4(),
        "decision_run_id": uuid4(),
        "firma_decision": "a" * 64,
        "territorial_run_id": uuid4(),
        "firma_territorial": "b" * 64,
        "configuracion_campo_version_id": uuid4(),
        "hash_configuracion": "c" * 64,
        "fecha_operacion": date(2026, 10, 1),
    }
    firma = politica.firma_inputs(**base)

    assert politica.firma_inputs(**base) == firma
    for campo, otro in (
        ("corte_id", uuid4()),
        ("decision_run_id", uuid4()),
        ("firma_decision", "d" * 64),
        ("territorial_run_id", uuid4()),
        ("firma_territorial", "e" * 64),
        ("configuracion_campo_version_id", uuid4()),
        ("hash_configuracion", "f" * 64),
        ("fecha_operacion", date(2026, 10, 2)),
    ):
        assert politica.firma_inputs(**{**base, campo: otro}) != firma, campo
    assert (
        politica.firma_inputs(**base, politica=PoliticaCampo("campo-public-v3", 6372, 4_000, 1_000))
        != firma
    )


# --- el ranking -----------------------------------------------------------------------------------


def test_el_ranking_es_el_sha256_del_microtramo_con_cada_codigo_el_mayor_primero():
    import hashlib

    gestores = ["GESTOR_C", "GESTOR_A", "GESTOR_B"]
    esperado = sorted(
        gestores,
        key=lambda g: hashlib.sha256(f"{M}|{g}".encode()).digest(),
        reverse=True,
    )

    assert ranking(M, gestores) == esperado
    for permutacion in (gestores[::-1], sorted(gestores), ["GESTOR_B", "GESTOR_C", "GESTOR_A"]):
        assert ranking(M, permutacion) == esperado


def test_agregar_un_gestor_solo_mueve_los_microtramos_en_que_el_nuevo_queda_primero():
    microtramos = _microtramos(1_000)
    antes = {m: ranking(m, ["GESTOR_A", "GESTOR_B"])[0] for m in microtramos}
    despues = {m: ranking(m, ["GESTOR_A", "GESTOR_B", "GESTOR_C"])[0] for m in microtramos}

    movidos = [m for m in microtramos if despues[m] != antes[m]]
    assert all(despues[m] == "GESTOR_C" for m in movidos)
    assert {m for m in microtramos if despues[m] == "GESTOR_C"} == set(movidos)
    # Alrededor de un tercio, el que le toca al nuevo de tres.
    assert 0.28 < len(movidos) / len(microtramos) < 0.39


def test_rendezvous_mueve_menos_que_un_reparto_por_turnos():
    """El reparto por turnos (el i-esimo microtramo al gestor i mod k) mueve dos de cada tres
    microtramos al pasar de 2 a 3 gestores; Rendezvous, uno de cada tres."""
    microtramos = sorted(_microtramos(1_000))
    gestores = ["GESTOR_A", "GESTOR_B", "GESTOR_C"]
    por_turnos = sum(
        1 for i, _ in enumerate(microtramos) if gestores[i % 2] != gestores[i % 3]
    ) / len(microtramos)
    rendezvous = sum(
        1 for m in microtramos if ranking(m, gestores[:2])[0] != ranking(m, gestores)[0]
    ) / len(microtramos)

    assert por_turnos > 0.6
    assert rendezvous < 0.4
    assert rendezvous < por_turnos


# --- el reparto -----------------------------------------------------------------------------------


def _bloques(*especificacion: tuple[str, int]) -> list[Bloque]:
    microtramos = _microtramos(len(especificacion))
    return [Bloque(p, m, n) for (p, n), m in zip(especificacion, microtramos, strict=True)]


def _por_estado(reparto) -> Counter:
    estados: Counter = Counter()
    for p in reparto.partes:
        estados[p.estado] += p.hasta - p.desde
    return estados


def _asignadas_por_gestor(reparto) -> Counter:
    asignadas = Counter()
    for p in reparto.partes:
        if p.gestor is not None:
            asignadas[p.gestor] += p.hasta - p.desde
    return asignadas


@pytest.mark.parametrize(
    "bloques",
    [
        [("MUY_ALTA", 1)] * 7,
        [("MUY_ALTA", 7)],
        [("MUY_ALTA", 3), ("MUY_ALTA", 4)],
        [("MUY_ALTA", 2), ("MUY_ALTA", 2), ("MUY_ALTA", 2), ("MUY_ALTA", 1)],
    ],
    ids=["siete-sueltas", "un-bloque", "tres-y-cuatro", "dos-dos-dos-uno"],
)
def test_tres_gestores_de_2_2_y_1_dan_exactamente_5_asignadas_y_2_sin_capacidad(bloques):
    reparto = repartir(
        _bloques(*bloques), {"GESTOR_A": 2, "GESTOR_B": 2, "GESTOR_C": 1}, base="BASE_A"
    )

    estados = _por_estado(reparto)
    assert estados[EstadoCampo.ASIGNADA] == 5
    assert estados[EstadoCampo.SIN_CAPACIDAD] == 2
    assert _asignadas_por_gestor(reparto) == {"GESTOR_A": 2, "GESTOR_B": 2, "GESTOR_C": 1}


def test_con_capacidad_3_queda_fuera_la_baja_y_nunca_la_muy_alta():
    bloques = _bloques(("BAJA", 1), ("MEDIA", 1), ("ALTA", 1), ("MUY_ALTA", 1))
    reparto = repartir(bloques, {"GESTOR_A": 2, "GESTOR_B": 1}, base="BASE_A")

    por_prioridad = {p.bloque.prioridad: p.estado for p in reparto.partes}
    assert por_prioridad == {
        "MUY_ALTA": EstadoCampo.ASIGNADA,
        "ALTA": EstadoCampo.ASIGNADA,
        "MEDIA": EstadoCampo.ASIGNADA,
        "BAJA": EstadoCampo.SIN_CAPACIDAD,
    }


@pytest.mark.parametrize("semilla", range(40))
def test_la_prioridad_va_antes_que_la_capacidad_en_cualquier_reparto(semilla):
    """Al azar (con semilla): una cuenta queda sin capacidad solo cuando toda la capacidad de la
    base ya se uso, y entonces ninguna de menor prioridad se asigna; ningun gestor recibe mas que su
    cupo; y cada cuenta de cada bloque queda en exactamente una parte."""
    azar = random.Random(semilla)
    prioridades = ["BAJA", "MEDIA", "ALTA", "MUY_ALTA"]
    bloques = [
        Bloque(azar.choice(prioridades), m, azar.randint(1, 9))
        for m in _microtramos(azar.randint(1, 30), semilla)
    ]
    capacidades = {f"GESTOR_{i}": azar.randint(1, 12) for i in range(azar.randint(1, 6))}

    reparto = repartir(bloques, capacidades, base="BASE_A")

    asignadas = _asignadas_por_gestor(reparto)
    assert all(asignadas[g] <= c for g, c in capacidades.items())
    demanda = sum(b.cuentas for b in bloques)
    assert sum(asignadas.values()) == min(demanda, sum(capacidades.values()))
    for b in bloques:
        partes = sorted((p for p in reparto.partes if p.bloque == b), key=lambda p: p.desde)
        assert [(p.desde, p.hasta) for p in partes] == list(
            zip([0] + [p.hasta for p in partes[:-1]], [p.hasta for p in partes], strict=True)
        )
        assert partes[-1].hasta == b.cuentas
    sin_capacidad = [p for p in reparto.partes if p.estado == EstadoCampo.SIN_CAPACIDAD]
    if sin_capacidad:
        assert sum(asignadas.values()) == sum(capacidades.values())
        mas_alta_fuera = max(p.bloque.nivel for p in sin_capacidad)
        assert all(
            p.bloque.nivel >= mas_alta_fuera
            for p in reparto.partes
            if p.estado == EstadoCampo.ASIGNADA
        )


def test_cada_microtramo_que_cabe_entero_queda_con_un_solo_gestor():
    """Dos microtramos de 3 cuentas y dos gestores con cupo 3: cada uno entero con uno, nunca
    A1, A2 y A3 repartidas."""
    bloques = _bloques(("MUY_ALTA", 3), ("MUY_ALTA", 3))
    reparto = repartir(bloques, {"GESTOR_A": 3, "GESTOR_B": 3}, base="BASE_A")

    assert len(reparto.partes) == 2
    assert all((p.desde, p.hasta) == (0, 3) for p in reparto.partes)
    assert {p.gestor for p in reparto.partes} == {"GESTOR_A", "GESTOR_B"}
    assert all(not p.motivo["dividido"] for p in reparto.partes)


def _gestores_para(microtramo: UUID) -> tuple[str, str, str]:
    """Tres codigos en el orden del ranking de ese microtramo: el preferido, el segundo y el
    tercero."""
    return tuple(ranking(microtramo, ["GESTOR_A", "GESTOR_B", "GESTOR_C"]))


def test_si_al_propietario_le_caben_3_de_5_y_a_ningun_otro_las_5_se_divide_3_y_2():
    preferido, segundo, tercero = _gestores_para(M)
    reparto = repartir(
        [Bloque("MUY_ALTA", M, 5)], {preferido: 3, segundo: 2, tercero: 1}, base="BASE_A"
    )

    partes = [(p.desde, p.hasta, p.gestor, p.rebalanceada) for p in reparto.partes]
    assert partes == [(0, 3, preferido, False), (3, 5, segundo, True)]
    assert reparto.partes[0].motivo == {
        "codigo": MotivoCampo.PROPIETARIO_PREFERIDO.value,
        "gestor": preferido,
        "cuentas_bloque": 5,
        "dividido": True,
    }
    assert reparto.partes[1].motivo == {
        "codigo": MotivoCampo.SPILLOVER_POR_CAPACIDAD.value,
        "gestor": segundo,
        "propietario_preferido": preferido,
        "lugar": 2,
        "cuentas_bloque": 5,
        "dividido": True,
    }


def test_si_al_propietario_no_le_cabe_y_al_siguiente_si_el_bloque_va_entero_al_siguiente():
    preferido, segundo, tercero = _gestores_para(M)
    reparto = repartir(
        [Bloque("MUY_ALTA", M, 5)], {preferido: 3, segundo: 8, tercero: 8}, base="BASE_A"
    )

    assert [(p.desde, p.hasta, p.gestor, p.rebalanceada) for p in reparto.partes] == [
        (0, 5, segundo, True)
    ]
    assert reparto.partes[0].motivo["lugar"] == 2
    assert reparto.partes[0].motivo["dividido"] is False


def test_el_propietario_preferido_no_depende_de_su_cupo_restante():
    """El propietario es el primero del ranking entre todos los gestores disponibles, aunque ya no
    tenga cupo: es lo que se le dice a v0.13 y lo que hace estable la particion."""
    preferido, segundo, _ = _gestores_para(M)
    reparto = repartir([Bloque("MUY_ALTA", M, 2)], {preferido: 1, segundo: 5}, base="BASE_A")

    assert reparto.propietario == {M: preferido}
    assert [p.gestor for p in reparto.partes] == [segundo]


def test_sin_gestores_disponibles_todo_queda_sin_gestor_con_sus_jornadas():
    bloques = _bloques(("MUY_ALTA", 2), ("ALTA", 1))
    jornadas = {EstadoJornada.INACTIVO: 1, EstadoJornada.SIN_JORNADA: 2}
    reparto = repartir(bloques, {}, base="BASE_B", jornadas=jornadas)

    assert {p.estado for p in reparto.partes} == {EstadoCampo.SIN_GESTOR_DISPONIBLE}
    assert reparto.partes[0].motivo == {
        "codigo": MotivoCampo.SIN_GESTOR_DISPONIBLE.value,
        "base": "BASE_B",
        "gestores": 3,
        "inactivos": 1,
        "sin_jornada": 2,
        "cerrados": 0,
    }
    # El orden de la cola existe aunque no haya capacidad: la prioridad mas alta primero.
    assert [reparto.inicio[b] for b in orden_de_bloques(bloques)] == [0, 2]


def test_un_gestor_disponible_tiene_capacidad():
    with pytest.raises(ValueError):
        repartir(_bloques(("MUY_ALTA", 1)), {"GESTOR_A": 0}, base="BASE_A")


def test_el_orden_de_los_bloques_es_prioridad_luego_tamano_luego_microtramo():
    a, b, c, d = sorted(_microtramos(4))
    bloques = [
        Bloque("ALTA", a, 9),
        Bloque("MUY_ALTA", d, 1),
        Bloque("MUY_ALTA", c, 5),
        Bloque("MUY_ALTA", b, 5),
    ]

    assert orden_de_bloques(bloques) == [
        Bloque("MUY_ALTA", b, 5),
        Bloque("MUY_ALTA", c, 5),
        Bloque("MUY_ALTA", d, 1),
        Bloque("ALTA", a, 9),
    ]
    assert orden_de_bloques(reversed(bloques)) == orden_de_bloques(bloques)


# --- cuenta por cuenta ----------------------------------------------------------------------------


def test_cuenta_por_cuenta_una_division_sigue_el_orden_de_la_cuenta_id():
    cuentas = sorted(uuid4() for _ in range(5))
    preferido = ranking(particion("BASE_A", "21114", 1, 1).microtramo_id, ["GESTOR_A", "GESTOR_B"])
    candidatas = [
        Candidata(c, "MUY_ALTA", "ASIGNADA", (), "BASE_A", "21114", 1, 1) for c in cuentas[::-1]
    ]
    candidatas.append(Candidata(uuid4(), "MUY_ALTA", "SIN_BASE_ELEGIBLE", ("SIN_BASE_QUE_CUBRA",)))

    resultados = asignar(candidatas, {"BASE_A": {preferido[0]: 3, preferido[1]: 2}})

    assert [resultados[c].gestor for c in cuentas] == [preferido[0]] * 3 + [preferido[1]] * 2
    assert [resultados[c].orden_prioridad for c in cuentas] == [1, 2, 3, 4, 5]
    revision = resultados[candidatas[-1].cuenta_id]
    assert (revision.estado, revision.base, revision.particion, revision.orden_prioridad) == (
        EstadoCampo.REVISION_TERRITORIAL,
        None,
        None,
        None,
    )
    assert revision.motivos == [
        {
            "codigo": MotivoCampo.TERRITORIAL_NO_ASIGNADA.value,
            "estado_territorial": "SIN_BASE_ELEGIBLE",
            "motivos_territoriales": ["SIN_BASE_QUE_CUBRA"],
        }
    ]


def test_las_cuentas_no_cambian_de_base_aunque_otra_base_tenga_cupo():
    """BASE_A sin gestores y BASE_B con cupo de sobra: las de BASE_A quedan sin gestor; ninguna se
    lleva a BASE_B."""
    candidatas = [
        Candidata(uuid4(), "MUY_ALTA", "ASIGNADA", (), "BASE_A", "21114", i, 1) for i in range(3)
    ] + [Candidata(uuid4(), "MUY_ALTA", "ASIGNADA", (), "BASE_B", "21015", 1, 1)]

    resultados = asignar(candidatas, {"BASE_B": {"GESTOR_B": 50}})

    for c in candidatas[:3]:
        assert (resultados[c.cuenta_id].estado, resultados[c.cuenta_id].base) == (
            EstadoCampo.SIN_GESTOR_DISPONIBLE,
            "BASE_A",
        )
    assert resultados[candidatas[3].cuenta_id].gestor == "GESTOR_B"


def test_el_nucleo_es_puro():
    """Sin base, sin reloj y sin azar: el modulo no importa nada de la base de datos."""
    import motor_cartera.campo_v2.politica as modulo

    texto = open(modulo.__file__, encoding="utf-8").read()
    for prohibido in ("sqlalchemy", "sqlmodel", "psycopg", "random", "datetime.now", "time."):
        assert prohibido not in texto, prohibido
