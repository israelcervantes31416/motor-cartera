"""El golden de campo/v2, de punta a punta en PostgreSQL: los calendarios en un plan, la particion
(el mismo microtramo, dos microtramos de un tramo, dos tramos, dos municipios, dos entidades, dos
bases y la frontera exacta de una celda), la capacidad, la prioridad, el microtramo que no se
divide, el spillover, ninguna cuenta llevada a otra base y la estabilidad al agregar un gestor.
Cada plan se compara tambien con el espejo en Python."""

from __future__ import annotations

import pytest
from campo_v2_escenarios import (
    AMOZOC,
    CAMPO,
    JUEVES,
    LV_PARTIDO,
    MERIDA,
    PUEBLA,
    X0,
    Y0,
    CuentaCampo,
    asignaciones,
    base,
    cargas,
    decision_mixta,
    escenario,
    espejo,
    gestor,
    planear,
    publicadas,
    publicar_campo,
    unidades,
)

from motor_cartera.campo_v2.politica import particion, ranking

pytestmark = pytest.mark.usefixtures("bd")


def _celda(x: float) -> int:
    from motor_cartera.campo_v2.politica import celda

    return celda(x, 1_000)


# --- los calendarios ------------------------------------------------------------------------------


def test_cada_gestor_queda_con_su_jornada_del_dia_y_solo_los_disponibles_reciben_cuentas(tmp_path):
    cuentas = [CuentaCampo(n, CAMPO, PUEBLA, X0 + 1_000 * n, Y0) for n in range(1, 9)]
    e = escenario(tmp_path, cuentas, (base("BASE_A", X0 - 3_000, Y0, [PUEBLA]),))
    calendarios = [
        LV_PARTIDO,
        {"codigo": "SABADOS", "semana": {"SABADO": [{"inicio": "09:00", "fin": "14:00"}]}},
        {
            "codigo": "CAPACITACION",
            "semana": LV_PARTIDO["semana"],
            "excepciones": [
                {"fecha": JUEVES.isoformat(), "tipo": "CERRADO", "motivo": "Capacitacion"}
            ],
        },
        {
            "codigo": "MEDIA_JORNADA",
            "semana": LV_PARTIDO["semana"],
            "excepciones": [
                {
                    "fecha": JUEVES.isoformat(),
                    "tipo": "VENTANAS_ESPECIALES",
                    "motivo": "Media jornada",
                    "ventanas": [{"inicio": "09:00", "fin": "13:00"}],
                }
            ],
        },
    ]
    configuracion = publicar_campo(
        e.bases,
        [
            gestor("GESTOR_DISPONIBLE", capacidad=3),
            gestor("GESTOR_INACTIVO", capacidad=3, activo=False),
            gestor("GESTOR_SIN_JORNADA", capacidad=3, calendario="SABADOS"),
            gestor("GESTOR_CERRADO", capacidad=3, calendario="CAPACITACION"),
            gestor("GESTOR_ESPECIAL", capacidad=3, calendario="MEDIA_JORNADA"),
        ],
        calendarios,
    )

    p = planear(e, configuracion, JUEVES)

    c = cargas(p.id)
    assert {g: (x["jornada"], x["capacidad"]) for g, x in c.items()} == {
        "GESTOR_DISPONIBLE": ("JORNADA_SEMANAL", 3),
        "GESTOR_INACTIVO": ("INACTIVO", 0),
        "GESTOR_SIN_JORNADA": ("SIN_JORNADA", 0),
        "GESTOR_CERRADO": ("CERRADO_POR_EXCEPCION", 0),
        "GESTOR_ESPECIAL": ("VENTANAS_ESPECIALES", 3),
    }
    assert c["GESTOR_DISPONIBLE"]["ventanas"] == [
        {
            "inicio": "09:00",
            "fin": "14:00",
            "inicio_en": "2026-10-01T15:00:00+00:00",
            "fin_en": "2026-10-01T20:00:00+00:00",
        },
        {
            "inicio": "16:00",
            "fin": "19:00",
            "inicio_en": "2026-10-01T22:00:00+00:00",
            "fin_en": "2026-10-02T01:00:00+00:00",
        },
    ]
    assert [(v["inicio"], v["fin"]) for v in c["GESTOR_ESPECIAL"]["ventanas"]] == [
        ("09:00", "13:00")
    ]
    for g in ("GESTOR_INACTIVO", "GESTOR_SIN_JORNADA", "GESTOR_CERRADO"):
        assert (c[g]["ventanas"], c[g]["asignadas"], c[g]["utilizacion"]) == ([], 0, None)
    asignados = {a["gestor"] for a in asignaciones(p.id).values() if a["gestor"]}
    assert asignados == {"GESTOR_DISPONIBLE", "GESTOR_ESPECIAL"}
    assert (p.capacidad_total, p.asignadas, p.sin_capacidad) == (6, 6, 2)
    assert p.distribucion["jornada"] == {
        "CERRADO_POR_EXCEPCION": 1,
        "INACTIVO": 1,
        "JORNADA_SEMANAL": 1,
        "SIN_JORNADA": 1,
        "VENTANAS_ESPECIALES": 1,
    }
    assert publicadas(p.id) == espejo(p.id)


# --- la particion ---------------------------------------------------------------------------------

BISECTRIZ = 2_903_300
"""Entre BASE_A (x = 2,893,000) y BASE_B (x = 2,913,600): las dos cubren PUEBLA, y la celda de
microtramo 2,903 tiene un lado mas cerca de cada una."""
PARTICION = [
    # numero, municipio, x, y, base, celda esperada
    (1, PUEBLA, 2_897_500, 787_500, "BASE_A", (2_897, 787)),
    (2, PUEBLA, 2_897_600, 787_400, "BASE_A", (2_897, 787)),
    (3, PUEBLA, 2_898_500, 787_500, "BASE_A", (2_898, 787)),
    (4, PUEBLA, 2_902_500, 787_500, "BASE_A", (2_902, 787)),
    (5, AMOZOC, 2_915_500, 790_500, "BASE_A", (2_915, 790)),
    (6, MERIDA, 3_780_500, 1_050_500, "BASE_A", (3_780, 1_050)),
    (7, PUEBLA, BISECTRIZ - 200, 787_500, "BASE_A", (2_903, 787)),
    (8, PUEBLA, BISECTRIZ + 200, 787_500, "BASE_B", (2_903, 787)),
    (9, PUEBLA, 2_899_999.99, 787_500, "BASE_A", (2_899, 787)),
    (10, PUEBLA, 2_900_000.00, 787_500, "BASE_A", (2_900, 787)),
    (11, PUEBLA, 2_900_000.01, 787_500, "BASE_A", (2_900, 787)),
]
CVEGEO = {PUEBLA: "21114", AMOZOC: "21015", MERIDA: "31050"}


@pytest.fixture
def partido(tmp_path):
    cuentas = [CuentaCampo(n, CAMPO, m, x, y) for n, m, x, y, *_ in PARTICION]
    bases = (
        base("BASE_A", 2_893_000, 787_500, [PUEBLA, AMOZOC, MERIDA]),
        base("BASE_B", 2_913_600, 787_500, [PUEBLA]),
    )
    e = escenario(tmp_path, cuentas, bases)
    configuracion = publicar_campo(
        e.bases, [gestor("GESTOR_A1"), gestor("GESTOR_A2"), gestor("GESTOR_B1", "BASE_B")]
    )
    return e, planear(e, configuracion, JUEVES)


def test_cada_cuenta_queda_en_las_unidades_de_su_celda_exacta(partido):
    _, p = partido
    publicado = asignaciones(p.id)

    for numero, municipio, x, _, codigo, (ix, iy) in PARTICION:
        assert _celda(x) == ix
        esperada = particion(codigo, CVEGEO[municipio], ix, iy)
        a = publicado[numero]
        assert (a["estado"], a["base"]) == ("ASIGNADA", codigo), numero
        assert (a["zona"], a["subzona"], a["tramo"], a["microtramo"]) == (
            esperada.zona_id,
            esperada.subzona_id,
            esperada.tramo_id,
            esperada.microtramo_id,
        ), numero
    assert publicadas(p.id) == espejo(p.id)


def test_la_jerarquia_de_unidades_del_plan(partido):
    _, p = partido
    a = asignaciones(p.id)
    u = unidades(p.id)

    # 1. Dos puntos en el mismo microtramo.
    assert a[1]["microtramo"] == a[2]["microtramo"]
    # 2. Dos microtramos dentro del mismo tramo.
    assert a[1]["microtramo"] != a[3]["microtramo"] and a[1]["tramo"] == a[3]["tramo"]
    # 3. Dos tramos de la misma subzona.
    assert a[1]["tramo"] != a[4]["tramo"] and a[1]["subzona"] == a[4]["subzona"]
    # 4. Dos municipios de la misma entidad: dos subzonas de la misma zona.
    assert a[1]["subzona"] != a[5]["subzona"] and a[1]["zona"] == a[5]["zona"]
    # 5. Dos entidades: dos zonas.
    assert a[1]["zona"] != a[6]["zona"]
    # 6. Dos bases: la misma celda y el mismo municipio son dos microtramos, dos tramos, dos
    # subzonas y dos zonas.
    for nivel in ("zona", "subzona", "tramo", "microtramo"):
        assert a[7][nivel] != a[8][nivel], nivel
    # Cada unidad apunta a su padre, con su entidad y su municipio, y sin coordenadas.
    for numero in (1, 5, 6, 8):
        microtramo = u[a[numero]["microtramo"]]
        tramo = u[microtramo[1]]
        subzona = u[tramo[1]]
        zona = u[subzona[1]]
        assert (microtramo[0], tramo[0], subzona[0], zona[0]) == (
            "MICROTRAMO",
            "TRAMO",
            "SUBZONA",
            "ZONA",
        )
        assert (tramo[1], subzona[1], zona[1]) == (a[numero]["subzona"], a[numero]["zona"], None)
        assert zona[3] is None and subzona[3] == tramo[3] == microtramo[3]
    assert u[a[1]["microtramo"]][4] == 2  # dos candidatas en el microtramo de la 1 y la 2
    assert p.distribucion["unidades"] == {"ZONA": 3, "SUBZONA": 4, "TRAMO": 5, "MICROTRAMO": 9}


def test_la_frontera_de_una_celda_es_de_la_celda_que_empieza_ahi(partido):
    """x = 2,899,999.99 es del tramo 579; x = 2,900,000.00 y x = 2,900,000.01, del 580. El punto
    exactamente en la frontera vuelve de EPSG:4326 como 2,899,999.9999999995 y es del 580: la
    coordenada se toma al milimetro."""
    _, p = partido
    a = asignaciones(p.id)

    assert a[9]["tramo"] == particion("BASE_A", "21114", 2_899, 787).tramo_id
    assert a[10]["tramo"] == a[11]["tramo"] == particion("BASE_A", "21114", 2_900, 787).tramo_id
    assert a[9]["tramo"] != a[10]["tramo"]
    assert a[9]["microtramo"] != a[10]["microtramo"] == a[11]["microtramo"]


# --- la capacidad, la prioridad, el microtramo y el spillover -------------------------------------


def _sueltas(n: int, *, inicio: int = 1, dias: int = CAMPO) -> list[CuentaCampo]:
    """n cuentas, cada una en su propio microtramo del municipio A."""
    return [
        CuentaCampo(
            inicio + i, dias, PUEBLA, 2_891_500 + 1_000 * (i % 15), 781_500 + 1_000 * (i // 15)
        )
        for i in range(n)
    ]


# Dentro de cada prueba, no al importar: base() pasa su punto a EPSG:4326 con PostGIS, y al
# recolectar las pruebas la base de pruebas todavia puede no existir.
def _base_a() -> tuple[dict, ...]:
    return (base("BASE_A", X0 - 3_000, Y0, [PUEBLA, AMOZOC]),)


def test_tres_gestores_de_2_2_y_1_con_7_candidatas_dan_5_asignadas_y_2_sin_capacidad(tmp_path):
    e = escenario(tmp_path, _sueltas(7), _base_a())
    configuracion = publicar_campo(
        e.bases,
        [
            gestor("GESTOR_001", capacidad=2),
            gestor("GESTOR_002", capacidad=2),
            gestor("GESTOR_003", capacidad=1),
        ],
    )

    p = planear(e, configuracion, JUEVES)

    assert (p.candidatas_campo, p.asignadas, p.sin_capacidad) == (7, 5, 2)
    assert {g: c["asignadas"] for g, c in cargas(p.id).items()} == {
        "GESTOR_001": 2,
        "GESTOR_002": 2,
        "GESTOR_003": 1,
    }
    assert {str(c["utilizacion"]) for c in cargas(p.id).values()} == {"1.0000"}
    assert p.distribucion["por_base"]["BASE_A"]["utilizacion"] == "1.0000"
    assert publicadas(p.id) == espejo(p.id)


def test_con_capacidad_3_la_que_queda_fuera_es_la_baja(tmp_path):
    e = escenario(tmp_path, _sueltas(4), _base_a())
    mixta = decision_mixta(e.decision, {1: "BAJA", 2: "MEDIA", 3: "ALTA", 4: "MUY_ALTA"})
    configuracion = publicar_campo(e.bases, [gestor("GESTOR_001", capacidad=3)])

    p = planear(e, configuracion, JUEVES, decision=mixta)

    a = asignaciones(p.id)
    assert {n: (x["prioridad"], x["estado"]) for n, x in a.items()} == {
        1: ("BAJA", "SIN_CAPACIDAD"),
        2: ("MEDIA", "ASIGNADA"),
        3: ("ALTA", "ASIGNADA"),
        4: ("MUY_ALTA", "ASIGNADA"),
    }
    assert {n: x["orden"] for n, x in a.items()} == {4: 1, 3: 2, 2: 3, 1: 4}
    assert publicadas(p.id) == espejo(p.id)


def test_ninguna_de_mayor_prioridad_queda_fuera_mientras_una_menor_entra(tmp_path):
    prioridades = ["BAJA", "MEDIA", "ALTA", "MUY_ALTA"]
    e = escenario(tmp_path, _sueltas(24), _base_a())
    mixta = decision_mixta(e.decision, {n: prioridades[(n * 7) % 4] for n in range(1, 25)})
    configuracion = publicar_campo(
        e.bases, [gestor("GESTOR_001", capacidad=5), gestor("GESTOR_002", capacidad=6)]
    )

    p = planear(e, configuracion, JUEVES, decision=mixta)

    from motor_cartera.decision.reglas import Prioridad
    from motor_cartera.decision_v2.politica import NIVEL

    a = asignaciones(p.id).values()
    fuera = [NIVEL[Prioridad(x["prioridad"])] for x in a if x["estado"] == "SIN_CAPACIDAD"]
    dentro = [NIVEL[Prioridad(x["prioridad"])] for x in a if x["estado"] == "ASIGNADA"]
    assert (len(dentro), len(fuera)) == (11, 13)
    assert min(dentro) >= max(fuera)
    assert publicadas(p.id) == espejo(p.id)


def test_un_microtramo_que_cabe_entero_no_se_reparte_entre_gestores(tmp_path):
    cuentas = [CuentaCampo(n, CAMPO, PUEBLA, X0 + 10 * n, Y0) for n in (1, 2, 3)] + [
        CuentaCampo(n, CAMPO, PUEBLA, X0 + 2_000 + 10 * n, Y0) for n in (4, 5, 6)
    ]
    e = escenario(tmp_path, cuentas, _base_a())
    configuracion = publicar_campo(
        e.bases, [gestor("GESTOR_001", capacidad=3), gestor("GESTOR_002", capacidad=3)]
    )

    p = planear(e, configuracion, JUEVES)

    a = asignaciones(p.id)
    assert len({a[n]["gestor"] for n in (1, 2, 3)}) == 1
    assert len({a[n]["gestor"] for n in (4, 5, 6)}) == 1
    assert {a[1]["gestor"], a[4]["gestor"]} == {"GESTOR_001", "GESTOR_002"}
    assert all(not m["dividido"] for x in a.values() for m in x["motivos"])
    assert {c["microtramos"] for c in cargas(p.id).values()} == {1}


@pytest.mark.parametrize(("cupo_segundo", "partes"), [(2, (3, 2)), (8, (0, 5))])
def test_el_spillover_va_al_siguiente_del_ranking(tmp_path, cupo_segundo, partes):
    """Cinco cuentas en un microtramo; al propietario preferido le caben 3. Si al siguiente le
    caben 2, el bloque se divide 3 y 2; si le caben las 5, va entero al siguiente. Las que no
    quedan con el propietario son rebalanceadas."""
    cuentas = [CuentaCampo(n, CAMPO, PUEBLA, X0 + 10 * n, Y0) for n in range(1, 6)]
    e = escenario(tmp_path, cuentas, _base_a())
    microtramo = particion("BASE_A", "21114", _celda(X0), _celda(Y0)).microtramo_id
    preferido, segundo = ranking(microtramo, ["GESTOR_001", "GESTOR_002"])
    configuracion = publicar_campo(
        e.bases, [gestor(preferido, capacidad=3), gestor(segundo, capacidad=cupo_segundo)]
    )

    p = planear(e, configuracion, JUEVES)

    a = asignaciones(p.id)
    con_preferido = [n for n, x in a.items() if x["gestor"] == preferido]
    con_segundo = [n for n, x in a.items() if x["gestor"] == segundo]
    assert (len(con_preferido), len(con_segundo)) == partes
    assert all(a[n]["rebalanceada"] for n in con_segundo)
    assert not any(a[n]["rebalanceada"] for n in con_preferido)
    assert p.rebalanceadas == partes[1]
    assert {x["motivos"][0]["codigo"] for n, x in a.items() if n in con_segundo} == {
        "SPILLOVER_POR_CAPACIDAD"
    }
    if partes[0]:
        # La division sigue el orden de la cuenta_id: el propietario se queda con las primeras.
        ordenados = sorted(a, key=lambda n: a[n]["orden"])
        assert ordenados[:3] == sorted(con_preferido, key=lambda n: a[n]["orden"])
    unidad = unidades(p.id)[microtramo]
    assert unidad[6] is not None  # su propietario preferido
    assert publicadas(p.id) == espejo(p.id)


def test_una_base_sin_gestores_no_le_pide_capacidad_a_otra(tmp_path):
    cuentas = [CuentaCampo(n, CAMPO, PUEBLA, X0 + 1_000 * n, Y0) for n in (1, 2)] + [
        CuentaCampo(3, CAMPO, AMOZOC, 2_915_500, 790_500)
    ]
    bases = (
        base("BASE_A", X0 - 3_000, Y0, [PUEBLA]),
        base("BASE_B", 2_913_600, 790_500, [AMOZOC]),
    )
    e = escenario(tmp_path, cuentas, bases)
    configuracion = publicar_campo(
        e.bases,
        [gestor("GESTOR_A1", activo=False), gestor("GESTOR_B1", "BASE_B", capacidad=100)],
    )

    p = planear(e, configuracion, JUEVES)

    a = asignaciones(p.id)
    assert (a[1]["estado"], a[1]["base"]) == ("SIN_GESTOR_DISPONIBLE", "BASE_A")
    assert (a[2]["estado"], a[2]["base"]) == ("SIN_GESTOR_DISPONIBLE", "BASE_A")
    assert a[1]["motivos"] == [
        {
            "codigo": "SIN_GESTOR_DISPONIBLE",
            "base": "BASE_A",
            "gestores": 1,
            "inactivos": 1,
            "sin_jornada": 0,
            "cerrados": 0,
        }
    ]
    assert (a[3]["estado"], a[3]["gestor"]) == ("ASIGNADA", "GESTOR_B1")
    assert cargas(p.id)["GESTOR_B1"]["asignadas"] == 1
    assert publicadas(p.id) == espejo(p.id)


# --- la estabilidad al agregar un gestor ----------------------------------------------------------


def test_agregar_un_gestor_mueve_menos_que_un_reparto_por_turnos(tmp_path):
    """Config 1: GESTOR_A y GESTOR_B. Config 2: los mismos y GESTOR_C, con cupo de sobra. Con
    Rendezvous, solo cambian de gestor los microtramos que el nuevo gana; los que todavia podian
    quedarse con su propietario anterior se quedan todos. Un reparto por turnos movia el doble."""
    e = escenario(tmp_path, _sueltas(45), _base_a())
    uno = publicar_campo(
        e.bases, [gestor("GESTOR_A", capacidad=100), gestor("GESTOR_B", capacidad=100)]
    )
    dos = publicar_campo(
        e.bases,
        [
            gestor("GESTOR_A", capacidad=100),
            gestor("GESTOR_B", capacidad=100),
            gestor("GESTOR_C", capacidad=100),
        ],
    )

    antes = asignaciones(planear(e, uno, JUEVES).id)
    despues = asignaciones(planear(e, dos, JUEVES).id)

    microtramos = sorted({x["microtramo"] for x in antes.values()})
    assert len(microtramos) == 45
    movidos = [n for n in antes if antes[n]["gestor"] != despues[n]["gestor"]]
    al_nuevo = [n for n in antes if despues[n]["gestor"] == "GESTOR_C"]
    assert set(movidos) == set(al_nuevo)
    podian_quedarse = [n for n in antes if despues[n]["gestor"] != "GESTOR_C"]
    assert all(antes[n]["gestor"] == despues[n]["gestor"] for n in podian_quedarse)
    gestores = ["GESTOR_A", "GESTOR_B", "GESTOR_C"]
    por_turnos = sum(1 for i, _ in enumerate(microtramos) if gestores[i % 2] != gestores[i % 3])
    assert len(movidos) < por_turnos
    assert 0 < len(movidos) / 45 < 0.5
