"""El modelo matematico de optimization/v1, sin base: el golden manual con su optimo calculado a
mano y por fuerza bruta, cada regla de la formulacion, la reduccion exacta, el solver y sus
estados, y propiedades sobre problemas aleatorios pequenos contra una enumeracion independiente.

La enumeracion de estas pruebas no usa nada de `formulacion`: recorre cada asignacion posible
(cada cuenta en uno de sus pools o en ninguno), revisa cada restriccion con enteros y se queda con
la mejor en el orden lexicografico (valor, contacto, -unidades, -desempate).
"""

from __future__ import annotations

import itertools
import random

import numpy as np
import pytest

from motor_cartera.optimizacion import formulacion as f
from motor_cartera.optimizacion import solver as solver_
from motor_cartera.optimizacion.politica import (
    PUBLIC_V1,
    Dimension,
    EstadoSolver,
    TipoRestriccion,
)

T = TipoRestriccion
D = Dimension
CAMPO = f.Pool("CAMPO_G1", "CAMPO", "GESTOR_CAMPO", "G1", None, 2, 20)
DIGITAL = f.Pool("DIGITAL", "DIGITAL", "GLOBAL", None, 3, 3, 1)
TELEFONICA = f.Pool("TELEFONICA", "TELEFONICA", "GLOBAL", None, 2, 2, 4)


def cuenta(cuenta_id: str, valor: int, contacto: int, producto: str = "CONSUMO", **dims):
    return f.CuentaEntrada(
        cuenta_id, valor, contacto, {D.PRODUCTO: producto, **{D(k): v for k, v in dims.items()}}
    )


# --- la enumeracion independiente -----------------------------------------------------------------


def _cumple(c: f.Cobertura, grupo: int, total: int) -> bool:
    if c.tipo == T.MIN_CUENTAS:
        return grupo >= c.limite
    if c.tipo == T.MAX_CUENTAS:
        return grupo <= c.limite
    if c.tipo == T.MIN_PARTICIPACION_BPS:
        return 10_000 * grupo >= c.limite * total
    return 10_000 * grupo <= c.limite * total


def fuerza_bruta(p: f.Problema) -> tuple[tuple[int, int, int, int], tuple[int, ...]] | None:
    """El mejor plan en orden lexicografico, o None si ninguno es factible. Cada cuenta: uno de sus
    pools elegibles o ninguno (-1)."""
    n, pools = p.cuentas, len(p.pools)
    opciones = [[-1] for _ in range(n)]
    for i, j in zip(p.par_cuenta.tolist(), p.par_pool.tolist(), strict=True):
        opciones[i].append(j)
    mejor = None
    for asignacion in itertools.product(*opciones):
        elegidas = [i for i in range(n) if asignacion[i] >= 0]
        uso = [0] * pools
        for i in elegidas:
            uso[asignacion[i]] += 1
        if any(uso[j] > p.pools[j].capacidad for j in range(pools)):
            continue
        total = len(elegidas)
        if p.max_cuentas is not None and total > p.max_cuentas:
            continue
        unidades = sum(p.pools[asignacion[i]].unidades for i in elegidas)
        if p.max_unidades is not None and unidades > p.max_unidades:
            continue
        if not all(
            _cumple(c, sum(1 for i in elegidas if p.miembros[i, k]), total)
            for k, c in enumerate(p.coberturas)
        ):
            continue
        valor = sum(int(p.valor[i]) for i in elegidas)
        contacto = sum(int(p.contacto[i]) for i in elegidas)
        desempate = sum(i * (pools + 1) + (n - i) * asignacion[i] for i in elegidas)
        llave = (valor, contacto, -unidades, -desempate)
        if mejor is None or llave > mejor[0]:
            mejor = (llave, asignacion)
    if mejor is None:
        return None
    (valor, contacto, unidades, desempate), asignacion = mejor
    return (valor, contacto, -unidades, -desempate), tuple(asignacion)


def objetivos(r: f.Resolucion) -> tuple[int, int, int, int]:
    o = r.objetivos
    return (o.valor, o.contacto, o.unidades, o.desempate)


# --- el golden manual -----------------------------------------------------------------------------


def golden(**cambios) -> tuple[f.Problema, tuple[str, ...]]:
    """Diez cuentas, tres pools, total, presupuesto y dos coberturas (docs/modelo_optimizacion.md).

    a01 a a09 son elegibles; a10, no. a03 es la unica que puede ir a CAMPO, ademas de a07 y a08,
    que solo pueden ir a CAMPO. Con a lo mas 6 cuentas, las seis de mayor valor (a01 a a06) suman
    39,000 y cumplen todo: CONSUMO es exactamente el 50 % (a01, a02, a03: 3 de 6) y hay dos de
    NOMINA (a04, a05). DIGITAL (3) ya tiene a01 y a04, TELEFONICA (2) a a05: de a02, a03 y a06,
    solo a03 cabe en CAMPO, y a02 y a06 se reparten el ultimo lugar de DIGITAL y el de TELEFONICA.
    El nivel 2 no cambia nada (el conjunto ya es el unico de 39,000): contacto 270,000,000. El
    nivel 3 tampoco: 3 DIGITAL, 2 TELEFONICA y 1 CAMPO son 3 + 8 + 20 = 31 unidades de cualquier
    forma. El nivel 4 decide el reparto: a02 (antes que a06 por cuenta_id) toma DIGITAL (antes que
    TELEFONICA por codigo): desempate 108."""
    cuentas = [
        cuenta("a01", 9000, 50_000_000, "CONSUMO"),
        cuenta("a02", 8000, 40_000_000, "CONSUMO"),
        cuenta("a03", 7000, 30_000_000, "CONSUMO"),
        cuenta("a04", 6000, 60_000_000, "NOMINA"),
        cuenta("a05", 5000, 20_000_000, "NOMINA"),
        cuenta("a06", 4000, 70_000_000, "TARJETA"),
        cuenta("a07", 3000, 80_000_000, "TARJETA"),
        cuenta("a08", 2000, 10_000_000, "NOMINA"),
        cuenta("a09", 1000, 90_000_000, "AUTOMOTRIZ"),
        cuenta("a10", 500, 95_000_000, "CONSUMO"),
    ]
    pares = [
        ("a01", "DIGITAL"),
        ("a02", "DIGITAL"),
        ("a02", "TELEFONICA"),
        ("a03", "DIGITAL"),
        ("a03", "TELEFONICA"),
        ("a03", "CAMPO_G1"),
        ("a04", "DIGITAL"),
        ("a05", "TELEFONICA"),
        ("a06", "DIGITAL"),
        ("a06", "TELEFONICA"),
        ("a07", "CAMPO_G1"),
        ("a08", "CAMPO_G1"),
        ("a09", "DIGITAL"),
    ]
    coberturas = [
        f.Cobertura("MIN_NOMINA", T.MIN_CUENTAS, D.PRODUCTO, "NOMINA", 2),
        f.Cobertura("MAX_CONSUMO", T.MAX_PARTICIPACION_BPS, D.PRODUCTO, "CONSUMO", 5000),
    ]
    datos = {
        "cuentas": cuentas,
        "pares": pares,
        "pools": [CAMPO, DIGITAL, TELEFONICA],
        "coberturas": coberturas,
        "max_cuentas": 6,
        "max_unidades": 50,
        **cambios,
    }
    return f.construir(
        datos["cuentas"],
        datos["pares"],
        datos["pools"],
        datos["coberturas"],
        max_cuentas=datos["max_cuentas"],
        max_unidades=datos["max_unidades"],
    )


def plan(r: f.Resolucion, p: f.Problema, ids: tuple[str, ...]) -> dict[str, str | None]:
    return {
        cuenta_id: (p.pools[int(j)].codigo if j != f.SIN_POOL else None)
        for cuenta_id, j in zip(ids, r.asignacion.tolist(), strict=True)
    }


def test_el_golden_manual():
    """Golden 109: el optimo calculado a mano en el docstring de `golden`."""
    p, ids = golden()
    r = f.resolver(p)
    assert (r.estado, r.resultado) == (EstadoSolver.OPTIMAL, f.PLAN_OPTIMO)
    assert objetivos(r) == (39_000, 270_000_000, 31, 108)
    assert plan(r, p, ids) == {
        "a01": "DIGITAL",
        "a02": "DIGITAL",
        "a03": "CAMPO_G1",
        "a04": "DIGITAL",
        "a05": "TELEFONICA",
        "a06": "TELEFONICA",
        "a07": None,
        "a08": None,
        "a09": None,
        "a10": None,
    }
    for nivel in r.niveles:
        assert nivel.estado == EstadoSolver.OPTIMAL
        assert nivel.cota == pytest.approx(nivel.objetivo, abs=1e-6)
        assert nivel.gap == 0.0
    e = f.evaluar(p, r.asignacion, r.reduccion.retenida)
    assert e.factible
    activas = {x.codigo for x in e.restricciones if x.binding}
    # CONSUMO exactamente al 50 %, NOMINA en su minimo, el total, DIGITAL y TELEFONICA llenos.
    assert {"MAX_CONSUMO", "MIN_NOMINA", "TOTAL_CUENTAS", "POOL:DIGITAL", "POOL:TELEFONICA"} <= (
        activas
    )
    assert "PRESUPUESTO_UNIDADES" not in activas and "POOL:CAMPO_G1" not in activas
    estado, motivo = f.estados(p, r.asignacion)
    assert list(estado[6:]) == ["NO_SELECCIONADA"] * 3 + ["NO_ELEGIBLE"]
    assert list(motivo[:6]) == ["EN_SOLUCION_OPTIMA"] * 6


def test_el_golden_coincide_con_la_fuerza_bruta():
    """Golden 110: la enumeracion independiente da el mismo optimo y el mismo plan."""
    p, _ = golden()
    r = f.resolver(p)
    llave, asignacion = fuerza_bruta(p)
    assert llave == objetivos(r)
    assert asignacion == tuple(r.asignacion.tolist())


def test_una_cuenta_va_a_lo_mas_a_un_pool():
    """Golden 111: una cuenta elegible para dos pools, con capacidad en los dos y valor de sobra,
    queda en uno solo."""
    p, _ = f.construir(
        [cuenta("u1", 100, 1)],
        [("u1", "DIGITAL"), ("u1", "TELEFONICA")],
        [DIGITAL, TELEFONICA],
    )
    r = f.resolver(p)
    assert r.asignacion.tolist() == [p.pools.index(DIGITAL)]
    assert objetivos(r) == (100, 1, 1, f.peso_canonico(np.array([0]), np.array([0]), 1, 2)[0])


def test_ningun_pool_pasa_su_capacidad_ni_el_presupuesto():
    """Golden 112 y 113: con el presupuesto apretado (25 unidades: CAMPO ya no cabe con otras
    dos), el plan cambia y sigue cumpliendo cada capacidad y el presupuesto."""
    p, _ = golden(max_unidades=25)
    r = f.resolver(p)
    e = f.evaluar(p, r.asignacion, r.reduccion.retenida)
    assert e.factible
    assert all(u.utilizada <= u.capacidad for u in e.uso)
    assert e.objetivos.unidades <= 25
    assert fuerza_bruta(p)[0] == objetivos(r)
    assert objetivos(r)[0] < 39_000


def test_el_valor_no_se_descuenta_otra_vez():
    """Golden 117 (el nucleo): el objetivo es la suma exacta de los valores que entran, en
    centavos, sin multiplicarlos por ninguna probabilidad."""
    p, _ = golden()
    r = f.resolver(p)
    elegidas = r.asignacion != f.SIN_POOL
    assert r.objetivos.valor == int(p.valor[elegidas].sum()) == 39_000


def test_el_orden_lexicografico():
    """Golden 118: con el mismo valor gana el mayor p_contacto; con el mismo valor y el mismo
    contacto, el menor esfuerzo; y con todo igual, la cuenta de menor cuenta_id."""
    unica = f.Pool("UNICO", "DIGITAL", "GLOBAL", None, 1, 1, 1)
    p, ids = f.construir(
        [cuenta("b1", 100, 10), cuenta("b2", 100, 20)], [("b1", "UNICO"), ("b2", "UNICO")], [unica]
    )
    r = f.resolver(p)
    assert plan(r, p, ids) == {"b1": None, "b2": "UNICO"}
    barato = f.Pool("BARATO", "DIGITAL", "GLOBAL", None, 1, 1, 1)
    caro = f.Pool("CARO", "TELEFONICA", "GLOBAL", None, 1, 1, 4)
    p, ids = f.construir(
        [cuenta("c1", 100, 10)], [("c1", "BARATO"), ("c1", "CARO")], [caro, barato]
    )
    r = f.resolver(p)
    assert plan(r, p, ids) == {"c1": "BARATO"} and r.objetivos.unidades == 1
    p, ids = f.construir(
        [cuenta("d2", 100, 10), cuenta("d1", 100, 10)], [("d1", "UNICO"), ("d2", "UNICO")], [unica]
    )
    r = f.resolver(p)
    assert plan(r, p, ids) == {"d1": "UNICO", "d2": None}


def test_un_minimo_le_gana_a_un_plan_mejor_que_lo_viola():
    """Golden 119: sin el minimo de NOMINA, las mejores cuatro cuentas serian otras; con el, el
    plan cede valor para cumplirlo."""
    cuentas = [
        cuenta("e1", 900, 1, "CONSUMO"),
        cuenta("e2", 800, 1, "CONSUMO"),
        cuenta("e3", 700, 1, "CONSUMO"),
        cuenta("e4", 100, 1, "NOMINA"),
        cuenta("e5", 90, 1, "NOMINA"),
    ]
    pares = [(c.cuenta_id, "DIGITAL") for c in cuentas]
    sin, _ = f.construir(cuentas, pares, [DIGITAL])
    con, ids = f.construir(
        cuentas,
        pares,
        [DIGITAL],
        [f.Cobertura("MIN_NOMINA", T.MIN_CUENTAS, D.PRODUCTO, "NOMINA", 2)],
    )
    libre, exigido = f.resolver(sin), f.resolver(con)
    assert libre.objetivos.valor == 2400 > exigido.objetivos.valor == 1090
    assert plan(exigido, con, ids) == {
        "e1": "DIGITAL",
        "e2": None,
        "e3": None,
        "e4": "DIGITAL",
        "e5": "DIGITAL",
    }


def test_un_maximo_no_se_pasa():
    """Golden 120."""
    cuentas = [cuenta(f"m{i}", 1000 - i, 1, "CONSUMO") for i in range(5)]
    pares = [(c.cuenta_id, "DIGITAL") for c in cuentas]
    tope = f.Cobertura("MAX_CONSUMO", T.MAX_CUENTAS, D.PRODUCTO, "CONSUMO", 2)
    p, _ = f.construir(cuentas, pares, [DIGITAL], [tope])
    r = f.resolver(p)
    assert int((r.asignacion != f.SIN_POOL).sum()) == 2
    assert f.evaluar(p, r.asignacion).factible


@pytest.mark.parametrize(
    ("tipo", "bps", "esperadas"),
    [
        # 3 cuentas atendidas: un minimo de 3,333 bps exige ceil(0.9999) = 1 del grupo; de 3,334,
        # ceil(1.0002) = 2. Un maximo de 6,666 permite floor(1.9998) = 1; de 6,667, floor(2.0001)
        # = 2.
        (T.MIN_PARTICIPACION_BPS, 3333, 1),
        (T.MIN_PARTICIPACION_BPS, 3334, 2),
        (T.MAX_PARTICIPACION_BPS, 6666, 1),
        (T.MAX_PARTICIPACION_BPS, 6667, 2),
    ],
)
def test_una_participacion_se_redondea_en_su_frontera(tipo, bps, esperadas):
    """Golden 121: con exactamente 3 cuentas atendidas (la capacidad), el grupo NOMINA entra lo
    justo: ceil en un minimo, floor en un maximo, en enteros. La restriccion lineal (10,000 por las
    del grupo contra los puntos base por el total) tiene su holgura en esas unidades: el grupo
    puede estar en su limite de cuentas sin que la fila lineal quede activa."""
    cuentas = [cuenta(f"c{i}", 1000, 1, "CONSUMO") for i in range(3)] + [
        cuenta(f"n{i}", 900 if tipo == T.MIN_PARTICIPACION_BPS else 2000, 1, "NOMINA")
        for i in range(3)
    ]
    pool = f.Pool("DIGITAL", "DIGITAL", "GLOBAL", None, 3, 3, 1)
    cobertura = f.Cobertura("NOMINA", tipo, D.PRODUCTO, "NOMINA", bps)
    p, _ = f.construir(cuentas, [(c.cuenta_id, "DIGITAL") for c in cuentas], [pool], [cobertura])
    r = f.resolver(p)
    e = f.evaluar(p, r.asignacion)
    fila = next(x for x in e.restricciones if x.codigo == "NOMINA")
    assert (fila.total, fila.cuentas_grupo, fila.limite_resuelto) == (3, esperadas, esperadas)
    lineal = 10_000 * esperadas - bps * 3
    assert fila.holgura == (lineal if tipo == T.MIN_PARTICIPACION_BPS else -lineal) >= 0
    assert fila.binding == (fila.holgura == 0)
    assert fuerza_bruta(p)[0] == objetivos(r)


def test_un_minimo_imposible_es_infactible_sin_plan():
    """Golden 122: un minimo de 5 con 3 elegibles es INFACTIBLE antes del solver, con su
    diagnostico, y no hay plan."""
    cuentas = [cuenta(f"i{i}", 100, 1, "NOMINA") for i in range(3)]
    cobertura = f.Cobertura("MIN_NOMINA", T.MIN_CUENTAS, D.PRODUCTO, "NOMINA", 5)
    p, _ = f.construir(cuentas, [(c.cuenta_id, "DIGITAL") for c in cuentas], [DIGITAL], [cobertura])
    r = f.resolver(p)
    assert (r.estado, r.resultado, r.asignacion) == (EstadoSolver.NO_INVOCADO, f.INFACTIBLE, None)
    hallazgo = next(h for h in r.hallazgos if h.severidad == f.ERROR)
    assert hallazgo.codigo == "MINIMO_SUPERA_ELEGIBLES"
    assert hallazgo.datos["minimo"] == 5 and hallazgo.datos["elegibles"] == 3


def test_una_infactibilidad_que_solo_el_solver_ve_trae_su_relajacion():
    """Dos minimos de dimensiones distintas que no caben juntos en el total: el prechequeo no lo
    demuestra (suma por dimension), el solver si, y la relajacion dice cuanto falta."""
    cuentas = [
        cuenta("n1", 100, 1, "NOMINA", PRIORIDAD_DECISION="BAJA"),
        cuenta("n2", 100, 1, "NOMINA", PRIORIDAD_DECISION="BAJA"),
        cuenta("a1", 100, 1, "CONSUMO", PRIORIDAD_DECISION="ALTA"),
        cuenta("a2", 100, 1, "CONSUMO", PRIORIDAD_DECISION="ALTA"),
    ]
    coberturas = [
        f.Cobertura("MIN_ALTA", T.MIN_CUENTAS, D.PRIORIDAD_DECISION, "ALTA", 2),
        f.Cobertura("MIN_NOMINA", T.MIN_CUENTAS, D.PRODUCTO, "NOMINA", 2),
    ]
    p, _ = f.construir(
        cuentas, [(c.cuenta_id, "DIGITAL") for c in cuentas], [DIGITAL], coberturas, max_cuentas=3
    )
    r = f.resolver(p)
    assert (r.estado, r.resultado, r.asignacion) == (EstadoSolver.INFEASIBLE, f.INFACTIBLE, None)
    diagnostico = f.diagnosticar_relajacion(p)
    assert diagnostico["estado"] == "RELAJACION_MINIMA"
    assert diagnostico["holgura_total"] == 1
    assert "IIS" in diagnostico["nota"]


def test_sin_demostrar_el_optimo_no_hay_plan():
    """Golden 123: un solver que se queda sin tiempo con una solucion factible no publica nada."""

    def sin_tiempo(c, A, lb, ub, *, politica, superior=None):
        return solver_.ResultadoSolver(
            EstadoSolver.TIME_LIMIT,
            13,
            "Time limit reached. (HiGHS Status 13: Time limit reached)",
            np.ones(len(c)),
            float(c.sum()),
            float(c.sum()) - 1,
            0.5,
            10,
            1800.0,
        )

    p, _ = golden()
    r = f.resolver(p, resolvedor=sin_tiempo)
    assert (r.estado, r.resultado, r.asignacion, r.objetivos) == (
        EstadoSolver.TIME_LIMIT,
        f.OPTIMO_NO_DEMOSTRADO,
        None,
        None,
    )
    assert [n.estado for n in r.niveles] == [EstadoSolver.TIME_LIMIT]


def test_una_solucion_que_no_cumple_en_enteros_no_es_optima():
    """Un solver que dice OPTIMAL con una solucion que viola una capacidad es un error numerico:
    se verifica en enteros, no se le cree."""

    def mentiroso(c, A, lb, ub, *, politica, superior=None):
        x = np.ones(len(c))
        return solver_.ResultadoSolver(
            EstadoSolver.OPTIMAL, 7, "Optimal", x, float(c @ x), float(c @ x), 0.0, 0, 0.0
        )

    p, _ = golden()
    r = f.resolver(p, resolvedor=mentiroso)
    assert (r.estado, r.resultado, r.asignacion) == (
        EstadoSolver.NUMERICAL_ERROR,
        f.ERROR_NUMERICO,
        None,
    )


def test_un_vertice_fraccionario_pasa_al_branch_and_bound():
    """Un presupuesto que corta una cuenta por la mitad en la relajacion: el nivel 1 lo resuelve
    el branch-and-bound de HiGHS, y da el optimo de la enumeracion."""
    a = f.Pool("A", "DIGITAL", "GLOBAL", None, 5, 5, 2)
    b = f.Pool("B", "TELEFONICA", "GLOBAL", None, 5, 5, 3)
    p, _ = f.construir(
        [cuenta("k1", 10, 1), cuenta("k2", 10, 2), cuenta("k3", 9, 3)],
        [("k1", "A"), ("k2", "B"), ("k3", "B")],
        [a, b],
        max_unidades=4,
    )
    r = f.resolver(p)
    assert r.niveles[0].metodo == solver_.BRANCH_AND_BOUND
    assert r.niveles[0].estado == EstadoSolver.OPTIMAL
    assert fuerza_bruta(p)[0] == objetivos(r)


def test_la_relajacion_entera_certifica_el_optimo():
    p, _ = golden()
    r = f.resolver(p)
    assert r.niveles[0].metodo == solver_.RELAJACION_LP_ENTERA
    assert r.niveles[0].nodos == 0


# --- la reduccion exacta --------------------------------------------------------------------------


def test_solo_se_reduce_dentro_de_una_misma_firma():
    """Golden 124: con un solo lugar en DIGITAL, de dos cuentas de la misma firma se retiene la de
    mayor valor; una de otra firma (otro producto en una cobertura activa) no se compara con ellas
    y se retiene aunque valga menos."""
    pool = f.Pool("DIGITAL", "DIGITAL", "GLOBAL", None, 1, 1, 1)
    cuentas = [
        cuenta("s1", 500, 1, "CONSUMO"),
        cuenta("s2", 400, 1, "CONSUMO"),
        cuenta("s3", 100, 1, "NOMINA"),
    ]
    tope = f.Cobertura("MAX_NOMINA", T.MAX_CUENTAS, D.PRODUCTO, "NOMINA", 5)
    p, ids = f.construir(cuentas, [(c.cuenta_id, "DIGITAL") for c in cuentas], [pool], [tope])
    red = f.reducir(p)
    assert red.clases == 2
    assert dict(zip(ids, red.retenida.tolist(), strict=True)) == {
        "s1": True,
        "s2": False,
        "s3": True,
    }
    assert red.resumen()["eliminadas"] == 1


def test_la_firma_distingue_pools_y_pertenencias():
    p, ids = golden()
    clase = dict(zip(ids, f.firmas(p).tolist(), strict=True))
    assert clase["a10"] == -1
    # a02 y a06: los mismos pools, distinto producto (CONSUMO esta en una cobertura).
    assert clase["a02"] != clase["a06"]
    # a01 y a09: el mismo pool (DIGITAL); CONSUMO contra AUTOMOTRIZ: distinta firma.
    assert clase["a01"] != clase["a09"]


def test_la_cota_de_una_clase_es_segura():
    """u[s] nunca es menor que lo que usa el plan optimo de la clase, con o sin reduccion."""
    rng = random.Random(11)
    for _ in range(20):
        p = problema_aleatorio(rng, cuentas=12)
        completo = f.resolver(p, reducir_=False)
        if not completo.optima:
            continue
        red = f.reducir(p)
        usadas = np.bincount(red.clase[completo.asignacion != f.SIN_POOL], minlength=red.clases)
        assert np.all(usadas <= red.cota)


def test_capacidades_enormes_no_envuelven_ninguna_cota():
    """Mas de mil pools con la mayor capacidad que admite una configuracion (2**53 - 1): su suma
    pasa de 2**63. Las cotas del prechequeo y de la reduccion se suman en enteros, con cada
    capacidad topada en el numero de cuentas (ningun plan usa mas), y no envuelven a un negativo
    que dejaria fuera del modelo a cuentas que el optimo usa."""
    enorme = 2**53 - 1
    pools = [
        f.Pool(f"DIGITAL_{j:04d}", "DIGITAL", "GLOBAL", None, enorme, enorme, 1)
        for j in range(1_100)
    ]
    cuentas = [cuenta(f"e{i}", 100 * (i + 1), 1) for i in range(3)]
    pares = [(c.cuenta_id, pool.codigo) for c in cuentas for pool in pools]
    tope = f.Cobertura("MAX_CONSUMO", T.MAX_PARTICIPACION_BPS, D.PRODUCTO, "CONSUMO", 10_000)
    p, _ = f.construir(cuentas, pares, pools, [tope])
    assert f.maximo_atendible(p) == 3
    red = f.reducir(p)
    assert red.cota.tolist() == [3] and red.retenida.all()
    r = f.resolver(p)
    assert r.optima and r.objetivos.valor == 600 and np.all(r.asignacion != f.SIN_POOL)


# --- propiedades ----------------------------------------------------------------------------------

PRODUCTOS = ("CONSUMO", "NOMINA", "TARJETA")
PRIORIDADES = ("BAJA", "ALTA")


def problema_aleatorio(rng: random.Random, *, cuentas: int, empates: bool = False) -> f.Problema:
    """Un problema pequeno: dos o tres pools, capacidades, unidades, admisibilidad al azar, y de
    cero a tres coberturas; con `empates`, valores y contactos repetidos."""
    pools = [
        f.Pool(
            f"P{j}",
            ("DIGITAL", "TELEFONICA", "CAMPO")[j],
            "GLOBAL",
            None,
            None,
            rng.randint(1, 4),
            rng.choice((1, 2, 4, 20)),
        )
        for j in range(rng.randint(2, 3))
    ]
    lista = []
    pares = []
    for i in range(cuentas):
        valor = rng.choice((100, 200, 300)) if empates else rng.randint(0, 1000)
        contacto = rng.choice((10, 20)) if empates else rng.randint(0, 10**8)
        lista.append(
            cuenta(
                f"x{i:03d}",
                valor,
                contacto,
                rng.choice(PRODUCTOS),
                PRIORIDAD_DECISION=rng.choice(PRIORIDADES),
            )
        )
        for pool in pools:
            if rng.random() < 0.6:
                pares.append((f"x{i:03d}", pool.codigo))
    coberturas = []
    for k in range(rng.randint(0, 3)):
        tipo = rng.choice(list(T))
        dimension = rng.choice((D.PRODUCTO, D.PRIORIDAD_DECISION))
        valor = rng.choice(PRODUCTOS if dimension == D.PRODUCTO else PRIORIDADES)
        limite = (
            rng.randint(0, 10_000)
            if tipo in (T.MIN_PARTICIPACION_BPS, T.MAX_PARTICIPACION_BPS)
            else rng.randint(0, 3)
        )
        coberturas.append(f.Cobertura(f"C{k}", tipo, dimension, valor, limite))
    problema, _ = f.construir(
        lista,
        pares,
        pools,
        coberturas,
        max_cuentas=rng.choice((None, rng.randint(1, cuentas))),
        max_unidades=rng.choice((None, rng.randint(1, 40))),
    )
    return problema


@pytest.mark.parametrize("semilla", range(40))
def test_el_milp_coincide_con_la_fuerza_bruta(semilla):
    """Golden 63 y propiedades: en problemas de hasta 8 cuentas, el plan del MILP es factible, no
    asigna nada inelegible ni dos veces, y su optimo lexicografico es el de la enumeracion; si la
    enumeracion no encuentra ninguno factible, el MILP es INFACTIBLE."""
    rng = random.Random(semilla)
    p = problema_aleatorio(rng, cuentas=rng.randint(3, 8), empates=semilla % 2 == 0)
    r = f.resolver(p)
    esperado = fuerza_bruta(p)
    if esperado is None:
        assert r.resultado == f.INFACTIBLE and r.asignacion is None
        return
    assert r.optima, (r.estado, r.resultado)
    assert objetivos(r) == esperado[0]
    e = f.evaluar(p, r.asignacion, r.reduccion.retenida)
    assert e.factible, e.violaciones
    assert e.objetivos == r.objetivos


@pytest.mark.parametrize("semilla", range(25))
def test_el_modelo_reducido_da_el_mismo_optimo_que_el_completo(semilla):
    """Golden 125 y 62: con muchas cuentas de la misma firma (valores y contactos repetidos),
    el modelo reducido y el completo dan el mismo valor, el mismo contacto, el mismo esfuerzo y el
    mismo desempate."""
    rng = random.Random(1000 + semilla)
    p = problema_aleatorio(rng, cuentas=rng.randint(20, 60), empates=semilla % 2 == 0)
    completo = f.resolver(p, reducir_=False)
    reducido = f.resolver(p)
    assert completo.resultado == reducido.resultado
    if completo.optima:
        assert objetivos(completo) == objetivos(reducido)
        assert reducido.reduccion.retenida.sum() <= completo.reduccion.retenida.sum()


@pytest.mark.parametrize("semilla", range(15))
def test_mas_capacidad_no_baja_el_valor(semilla):
    """Golden 133: subir la capacidad de un pool, sin nada mas, no baja el optimo primario (sin
    coberturas, que podrian volverse otras con mas cuentas atendidas)."""
    rng = random.Random(2000 + semilla)
    p = problema_aleatorio(rng, cuentas=rng.randint(5, 30))
    p = f.Problema(
        p.valor,
        p.contacto,
        p.par_cuenta,
        p.par_pool,
        p.pools,
        (),
        np.zeros((p.cuentas, 0), dtype=bool),
        p.max_cuentas,
        p.max_unidades,
    )
    antes = f.resolver(p)
    j = rng.randrange(len(p.pools))
    pools = list(p.pools)
    pools[j] = f.Pool(**{**pools[j].__dict__, "capacidad": pools[j].capacidad + rng.randint(1, 3)})
    despues = f.resolver(
        f.Problema(
            p.valor,
            p.contacto,
            p.par_cuenta,
            p.par_pool,
            tuple(pools),
            (),
            p.miembros,
            p.max_cuentas,
            p.max_unidades,
        )
    )
    assert despues.objetivos.valor >= antes.objetivos.valor


def hash_de(r: f.Resolucion, p: f.Problema, ids: tuple[str, ...]) -> str:
    estado, motivo = f.estados(p, r.asignacion)
    e = f.evaluar(p, r.asignacion, r.reduccion.retenida)
    return f.hash_plan(
        f.lineas_del_plan(ids, p, r.asignacion, estado, motivo, r.reduccion.retenida, e)
    )


def test_el_plan_no_depende_del_orden_de_las_entradas():
    """Golden 126: las mismas entradas en otro orden (cuentas, pares, pools y coberturas) dan el
    mismo plan y el mismo hash."""
    rng = random.Random(7)
    cuentas = [
        cuenta(f"o{i:02d}", rng.choice((100, 200)), rng.choice((5, 6)), rng.choice(PRODUCTOS))
        for i in range(25)
    ]
    pares = [
        (c.cuenta_id, p.codigo)
        for c in cuentas
        for p in (DIGITAL, TELEFONICA, CAMPO)
        if rng.random() < 0.6
    ]
    coberturas = [
        f.Cobertura("MIN_NOMINA", T.MIN_CUENTAS, D.PRODUCTO, "NOMINA", 1),
        f.Cobertura("MAX_CONSUMO", T.MAX_PARTICIPACION_BPS, D.PRODUCTO, "CONSUMO", 6000),
    ]
    p1, ids1 = f.construir(cuentas, pares, [DIGITAL, TELEFONICA, CAMPO], coberturas, max_cuentas=6)
    otro = random.Random(99)
    p2, ids2 = f.construir(
        otro.sample(cuentas, len(cuentas)),
        otro.sample(pares, len(pares)),
        [CAMPO, TELEFONICA, DIGITAL],
        list(reversed(coberturas)),
        max_cuentas=6,
    )
    r1, r2 = f.resolver(p1), f.resolver(p2)
    assert ids1 == ids2
    assert r1.asignacion.tolist() == r2.asignacion.tolist()
    assert hash_de(r1, p1, ids1) == hash_de(r2, p2, ids2)


def test_sin_variables_el_plan_vacio_es_optimo():
    p, _ = f.construir([cuenta("z1", 100, 1)], [], [DIGITAL])
    r = f.resolver(p)
    assert r.optima and objetivos(r) == (0, 0, 0, 0)
    assert not any(n.invocado for n in r.niveles)


# --- los rangos y el solver -----------------------------------------------------------------------


def test_lo_que_no_cabe_exacto_no_se_construye():
    """46: un valor cuya suma no cabe exacta en un double: FueraDeRango antes de construir nada,
    sin envolver."""
    p, _ = f.construir(
        [cuenta("g1", 2**52, 1), cuenta("g2", 2**52, 1)],
        [("g1", "DIGITAL"), ("g2", "DIGITAL")],
        [DIGITAL],
    )
    with pytest.raises(f.FueraDeRango):
        f.resolver(p)
    p, _ = f.construir([cuenta("g3", 100, 10**8 + 1)], [("g3", "DIGITAL")], [DIGITAL])
    with pytest.raises(f.FueraDeRango):
        f.resolver(p)


@pytest.mark.parametrize(
    ("status", "mensaje", "esperado"),
    [
        (
            0,
            "Optimization terminated successfully. (HiGHS Status 7: Optimal)",
            EstadoSolver.OPTIMAL,
        ),
        (2, "The problem is infeasible. (HiGHS Status 8: Infeasible)", EstadoSolver.INFEASIBLE),
        (1, "Time limit reached. (HiGHS Status 13: Time limit reached)", EstadoSolver.TIME_LIMIT),
        (1, "Iteration limit reached. (HiGHS Status 14: x)", EstadoSolver.ITERATION_LIMIT),
        (3, "The problem is unbounded. (HiGHS Status 10: x)", EstadoSolver.UNBOUNDED),
        (2, "(HiGHS Status 2: Model error)", EstadoSolver.SOLVER_ERROR),
        (4, "(HiGHS Status 9: Unbounded or infeasible)", EstadoSolver.INFEASIBLE_OR_UNBOUNDED),
        (1, "Time limit reached. ", EstadoSolver.TIME_LIMIT),
        (4, "sin codigo", EstadoSolver.SOLVER_ERROR),
    ],
)
def test_el_estado_del_solver_se_lee_sin_convertirlo(status, mensaje, esperado):
    """51: cada estado de HiGHS es el suyo; un limite de tiempo nunca es OPTIMAL."""
    assert solver_.estado_de(status, mensaje)[0] == esperado


def test_la_huella_del_solver():
    """49: la huella dice el algoritmo, SciPy, HiGHS, Python, el sistema, la arquitectura y los
    parametros de la politica; su hash cambia si cambia cualquiera."""
    huella = solver_.huella()
    assert huella["algoritmo"] == "SCIPY_HIGHS_MILP/v1"
    assert huella["highs"] and huella["scipy"] and huella["python"]
    assert huella["parametros"]["presolve"] is PUBLIC_V1.presolve
    assert huella["parametros"]["mip_rel_gap"] == 0.0
    otra = {**huella, "sistema": "Otro"}
    assert solver_.hash_huella(huella) != solver_.hash_huella(otra)
    assert solver_.hash_huella(huella) == solver_.hash_huella(dict(reversed(huella.items())))


def test_la_dualidad_solo_fija_lo_que_demuestra():
    """La cota lagrangiana de una relajacion es valida y, sin brecha, fija las variables con costo
    reducido distinto de cero; con una brecha mayor, no fija nada."""
    import scipy.sparse as sp

    # max 3a + 2b + 4c con a + b + c <= 2 y a + c <= 1: el optimo es b + c = 6.
    A = sp.csr_array(np.array([[1.0, 1.0, 1.0], [1.0, 0.0, 1.0]]))
    res = solver_.resolver(
        -np.array([3.0, 2.0, 4.0]), A, np.array([-np.inf, -np.inf]), np.array([2.0, 1.0])
    )
    assert res.estado == EstadoSolver.OPTIMAL and res.x.tolist() == [0.0, 1.0, 1.0]
    assert res.duales.lagrangiana == pytest.approx(-6.0)
    cero, uno = res.duales.fijables(-6)
    assert uno.tolist() == [False, False, True] and not cero.any()
    cero, uno = res.duales.fijables(-3)
    assert not cero.any() and not uno.any()
