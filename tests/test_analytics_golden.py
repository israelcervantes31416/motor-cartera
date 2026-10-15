"""analytics/v1 contra PostgreSQL con el escenario golden (ver `analytics_escenarios`): lo esperado
esta escrito a mano, cuenta por cuenta, y se compara con lo publicado.

Las ejecuciones del modulo, todas BACKTEST:

- A: as_of 2026-10-04, con lo que se conocia en K1, por dia;
- C: el mismo as_of, con lo que se conocia en K2 (llegaron dos gestiones y una anulacion);
- B: as_of 2026-10-08, en K2, por dia;
- D: as_of 2026-10-10, en K2, sin periodos y sin evaluacion de promesas: los pagos no cubren el 9
  ni el 10.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from analytics_escenarios import (
    AS_OF_A,
    AS_OF_B,
    CORTE,
    ZONA,
    CuentaGolden,
    agregados,
    analizar,
    celda,
    controles,
    golden,
    ingerir,
    valor,
)
from geografia_escenarios import PUEBLA, corte_de
from sqlalchemy import text

from motor_cartera.analytics.catalogo import METRICAS, Vista
from motor_cartera.analytics.manifiesto import Peticion
from motor_cartera.analytics.politica import Granularidad, Modo
from motor_cartera.db.sesion import sesion

pytestmark = pytest.mark.usefixtures("bd_del_modulo")

AS_OF_D = date(2026, 10, 10)
DIAS_A = [CORTE + timedelta(days=i) for i in range(5)]
DIAS_B = [CORTE + timedelta(days=i) for i in range(9)]


@pytest.fixture(scope="module")
def g(bd_del_modulo, tmp_path_factory):
    return golden(tmp_path_factory.mktemp("analytics_golden"))


@pytest.fixture(scope="module")
def a(g):
    e = analizar(g.peticion(AS_OF_A, g.k1))
    assert e.estado == "EXITOSA", e.detalle
    return e


@pytest.fixture(scope="module")
def c(g):
    e = analizar(g.peticion(AS_OF_A, g.k2))
    assert e.estado == "EXITOSA", e.detalle
    return e


@pytest.fixture(scope="module")
def b(g):
    e = analizar(g.peticion(AS_OF_B, g.k2))
    assert e.estado == "EXITOSA", e.detalle
    return e


@pytest.fixture(scope="module")
def pa(a):
    return agregados(a.id)


@pytest.fixture(scope="module")
def pb(b):
    return agregados(b.id)


@pytest.fixture(scope="module")
def pc(c):
    return agregados(c.id)


def _r(numerador, denominador) -> tuple:
    return (Decimal(numerador), Decimal(denominador))


ESPERADO_A: dict[str, object] = {
    # La cartera del corte: 20 cuentas de 10,000.00; 960 dias de atraso; la 20 sin saldo atrasado
    # y la 7 con 0, las demas con 500.00.
    "cartera.cuentas": "20",
    "cartera.saldo_total": "200000.00",
    "cartera.saldo_total_promedio": _r("200000.00", 20),
    "cartera.dias_atraso_promedio": _r(960, 20),
    "cartera.saldo_atrasado_suma": "9000.00",
    "cartera.saldo_atrasado_cuentas_con_dato": "19",
    "cartera.saldo_atrasado_cobertura": _r(19, 20),
    # Del 30 al 4: 250 (6) + 300 + 200 (8) + 1000 (1) + 500 (2, una vez) + 200 (9) + 300 (4) +
    # 100 (5) = 2850 vigentes; el de la 3 lo anulo su reverso del 3; -150 de un posible reverso.
    "recuperacion.neta": "2700.00",
    "recuperacion.pagos_vigentes": "2850.00",
    "recuperacion.posibles_reversos": "-150.00",
    "recuperacion.movimientos_economicos": "11",
    "recuperacion.pagos_anulados": "1",
    "recuperacion.cuentas_con_neto_positivo": "6",
    "recuperacion.sobre_saldo_inicial": _r("2700.00", "200000.00"),
    "calidad_pagos.movimientos_sin_cuenta": "1",
    "calidad_pagos.monto_sin_cuenta": "75.00",
    "calidad_pagos.movimientos_fuera_de_cohorte": "1",
    "calidad_pagos.monto_fuera_de_cohorte": "60.00",
    # 3 de la 8 el 1; el 2: 1, 2 (dos), 9, 10, 11, 12, 13, 15, 16, 17 y 18. La 14 llega tarde.
    "gestion.gestiones": "15",
    "gestion.cuentas_gestionadas": "12",
    "gestion.cuentas_contactadas": "10",
    "gestion.contactadas_sobre_gestionadas": _r(10, 12),
    "gestion.visitas_campo_registradas": "1",
    # 1000 (1) + 300 + 200 (8) + 500 (9) + 400 (10) + 600 (11) + 700 (12) + 800 (13).
    "promesa.creadas": "8",
    "promesa.monto_prometido": "4500.00",
    "promesa.cuentas_con_promesa": "7",
    "promesa.evaluadas": "8",
    "promesa.cumplidas": "3",
    "promesa.parciales": "1",
    "promesa.incumplidas": "1",
    "promesa.pendientes": "2",
    "promesa.canceladas": "1",
    "promesa.no_evaluables": "0",
    "promesa.sin_evaluacion": "0",
    "promesa.tasa_cumplimiento_total": _r(3, 5),
    "promesa.tasa_cumplimiento_total_o_parcial": _r(4, 5),
    # Unica: 300 (8), 1000 (1), 200 (9). Ambigua: 200 (8), 500 (2). Sin gestion: 250 (6), 800 (3,
    # anulado: no suma), 300 (4) y 100 (5).
    "atribucion.movimientos_evaluados": "9",
    "atribucion.asociacion_unica": "3",
    "atribucion.ambigua": "2",
    "atribucion.sin_gestion": "4",
    "atribucion.monto_asociacion_unica": "1500.00",
    "atribucion.monto_ambiguo": "700.00",
    "atribucion.monto_sin_gestion": "650.00",
    "decision.cuentas": "20",
    "decision.pct_universo": _r(20, 20),
    "geografia.cuentas": "20",
    "geografia.validas": "19",
    "geografia.discrepancias": "0",
    "geografia.sin_coordenadas": "1",
    "geografia.incompletas": "0",
    "geografia.invalidas": "0",
    "geografia.fuera_referencia": "0",
    "geografia.ambiguas": "0",
    "geografia.tasa_valida": _r(19, 20),
    "territorial.cuentas": "20",
    "territorial.asignadas": "19",
    "territorial.revision": "1",
    "territorial.sin_base": "0",
    "territorial.ambiguas": "0",
    "territorial.fuera_cobertura": "0",
    "territorial.tasa_asignacion": _r(19, 20),
    # 15-20 a CAMPO: la 15 a GESTOR_A; tres de 16-19 a GESTOR_B (cupo 3) y una SIN_CAPACIDAD; la
    # 20, sin coordenadas, a REVISION_TERRITORIAL. Cupos 5 + 3.
    "campo.candidatas": "6",
    "campo.asignadas": "4",
    "campo.revision_territorial": "1",
    "campo.sin_gestor": "0",
    "campo.sin_capacidad": "1",
    "campo.rebalanceadas": "0",
    "campo.capacidad_total": "8",
    "campo.capacidad_utilizada": "4",
    "campo.utilizacion": _r(4, 8),
    "campo.tasa_asignacion": _r(4, 6),
    # GESTOR_A: la 15, a 1,000 + 1,250 m de su base (Manhattan), ida y vuelta: 4,500 m. GESTOR_B:
    # en media hora cabe la 16, a 500 m (1,000 m ida y vuelta); las otras dos, SIN_TIEMPO. 5,500 m
    # a 25 km/h son 792 s; dos visitas de 15 minutos, 1,800 s.
    "ruteo.rutas": "2",
    "ruteo.rutas_completas": "1",
    "ruteo.rutas_parciales": "1",
    "ruteo.rutas_sin_paradas": "0",
    "ruteo.bases_no_enrutables": "0",
    "ruteo.rutas_sin_jornada": "0",
    "ruteo.paradas_objetivo": "4",
    "ruteo.programadas": "2",
    "ruteo.sin_tiempo": "2",
    "ruteo.no_enrutables": "0",
    "ruteo.no_enrutables_base": "0",
    "ruteo.tasa_programacion": _r(2, 4),
    "ruteo.distancia_total_m": "5500",
    "ruteo.duracion_vial_total_s": "792",
    "ruteo.duracion_servicio_total_s": "1800",
    "ruteo.duracion_espera_total_s": "0",
}

ESPERADO_B = {
    **ESPERADO_A,
    # El 6 llegan los reversos del pago de la 4 (del 3) y del de la 6 (del 30, otra ventana): los
    # dos quedan anulados. El 8, 120 de la 7.
    "recuperacion.neta": "2270.00",
    "recuperacion.pagos_vigentes": "2420.00",
    "recuperacion.movimientos_economicos": "14",
    "recuperacion.pagos_anulados": "3",
    "recuperacion.cuentas_con_neto_positivo": "5",
    "recuperacion.sobre_saldo_inicial": _r("2270.00", "200000.00"),
    # Se anulan la 15 (el 6) y la 16 (el 3, conocido en K2); llegan las dos de la 14; el 7, la 19.
    "gestion.gestiones": "16",
    "gestion.cuentas_gestionadas": "12",
    "gestion.cuentas_contactadas": "10",
    "gestion.contactadas_sobre_gestionadas": _r(10, 12),
    # La 13 vence el 8 y los pagos no llegan mas alla de ese dia: NO_EVALUABLE.
    "promesa.pendientes": "1",
    "promesa.no_evaluables": "1",
    "atribucion.movimientos_evaluados": "10",
    "atribucion.sin_gestion": "5",
    "atribucion.monto_sin_gestion": "220.00",
}

ESPERADO_C = {
    **ESPERADO_A,
    # Lo mismo que A, con lo que llego despues de K1: las dos gestiones de la 14 y la anulacion de
    # la 16. La 15 sigue efectiva: su anulacion ocurre despues de as_of.
    "gestion.gestiones": "16",
    "gestion.cuentas_contactadas": "11",
    "gestion.contactadas_sobre_gestionadas": _r(11, 12),
}


def _comparar(publicados: dict, esperado: dict) -> None:
    assert set(esperado) == {m.codigo for m in METRICAS}
    for codigo, esperado_ in esperado.items():
        fila = celda(publicados, codigo)
        assert fila["estado_valor"] == "DISPONIBLE", (codigo, fila)
        if isinstance(esperado_, tuple):
            numerador, denominador = esperado_
            assert (fila["numerador"], fila["denominador"]) == esperado_, codigo
            assert fila["valor"] == round(numerador / denominador, 10), codigo
        else:
            assert fila["valor"] == Decimal(esperado_), (codigo, fila["valor"], esperado_)
            assert fila["numerador"] is None and fila["denominador"] is None, codigo


def test_a_publica_lo_escrito_a_mano(a, pa):
    _comparar(pa, ESPERADO_A)
    assert a.agregados_publicados == len(pa)
    assert len(a.hash_agregados) == 64
    assert a.controles_fallidos == 0 and a.controles_totales == len(controles(a.id))


def test_b_publica_lo_escrito_a_mano(pb):
    _comparar(pb, ESPERADO_B)


def test_c_publica_lo_escrito_a_mano(pc):
    _comparar(pc, ESPERADO_C)


def test_cada_control_cuadra(a, b, c):
    for e in (a, b, c):
        calculados = controles(e.id)
        assert len(calculados) == e.controles_totales > 100
        assert {f["estado"] for f in calculados.values()} == {"OK"}
        assert all(f["diferencia"] == 0 for f in calculados.values())


# --- pagos ----------------------------------------------------------------------------------------


def _serie(publicados: dict, metrica: str, dias: list[date], conjunto="TOTAL", **dims) -> list:
    return [
        valor(publicados, metrica, conjunto, vista="PERIODO", periodo=dia, **dims) for dia in dias
    ]


def test_la_recuperacion_por_dia_suma_la_acumulada_y_puede_ser_negativa(pa):
    serie = _serie(pa, "recuperacion.neta", DIAS_A)
    assert serie == [Decimal(x) for x in ("250.00", "500.00", "1700.00", "400.00", "-150.00")]
    assert sum(serie) == valor(pa, "recuperacion.neta")
    # El 4 solo llego el posible reverso de -150 de la 5: el periodo es negativo, sin recortar.
    assert _serie(pa, "recuperacion.movimientos_economicos", DIAS_A) == [1, 2, 4, 3, 1]


def test_dos_observaciones_identicas_son_un_solo_movimiento(pa):
    # El 2: la 1 (1000), la 2 (500, observado dos veces), la 9 (200) y la 3 (800, anulado el 3).
    assert valor(pa, "recuperacion.movimientos_economicos", vista="PERIODO", periodo=DIAS_A[2]) == 4
    assert valor(pa, "recuperacion.neta", vista="PERIODO", periodo=DIAS_A[2]) == Decimal("1700.00")


def test_un_reverso_posterior_a_as_of_no_existe_para_ese_as_of(pa, pb):
    # El pago de la 4 (el 3) y el de la 6 (el 30) cuentan al 4: sus reversos llegan el 6. Al 8, el
    # par suma cero y los dos pagos dejan de contar en su propio dia: la neta de cada dia es la que
    # motor-pagos/v1 dice al final de as_of.
    assert _serie(pa, "recuperacion.neta", DIAS_A[:4]) == [
        Decimal("250.00"),
        Decimal("500.00"),
        Decimal("1700.00"),
        Decimal("400.00"),
    ]
    assert _serie(pb, "recuperacion.neta", DIAS_B) == [
        Decimal(x)
        for x in ("0.00", "500.00", "1700.00", "100.00", "-150.00", "0", "0.00", "0", "120.00")
    ]
    assert _serie(pa, "recuperacion.pagos_anulados", DIAS_A) == [0, 0, 1, 0, 0]
    assert _serie(pb, "recuperacion.pagos_anulados", DIAS_B[:4]) == [1, 0, 1, 1]


def test_el_reverso_de_otra_ventana_cuenta_hasta_que_existe(g, pa, pb):
    # El pago de la 6 es de septiembre y su reverso de octubre: la interpretacion vigente de
    # septiembre ya lo marca anulado, pero al 4 ese reverso no existia.
    with sesion() as s:
        anulado = s.execute(
            text(
                "SELECT anulado_por_movimiento_id IS NOT NULL FROM movimiento_economico_canonico "
                "WHERE ejecucion_motor_pagos_id = :e"
            ),
            {"e": g.septiembre.id},
        ).scalar_one()
    assert anulado
    assert valor(pa, "recuperacion.neta", vista="PERIODO", periodo=CORTE) == Decimal("250.00")
    assert valor(pb, "recuperacion.neta", vista="PERIODO", periodo=CORTE) == Decimal("0.00")


def test_los_movimientos_sin_cuenta_o_de_otra_cohorte_son_calidad_y_no_recuperacion(pa):
    assert (
        valor(pa, "calidad_pagos.movimientos_sin_cuenta", vista="PERIODO", periodo=DIAS_A[2]) == 1
    )
    assert valor(
        pa, "calidad_pagos.monto_fuera_de_cohorte", vista="PERIODO", periodo=DIAS_A[3]
    ) == Decimal("60.00")
    total = celda(pa, "calidad_pagos.movimientos_sin_cuenta")
    assert total["poblacion_cuentas"] is None
    for conjunto in ("PRODUCTO", "MUNICIPIO", "BASE"):
        assert not [k for k in pa if k[0].startswith("calidad_pagos.") and k[2] == conjunto]


# --- lifecycle point-in-time ----------------------------------------------------------------------


def test_una_gestion_registrada_despues_de_known_at_no_existe_y_despues_si(pa, pc):
    # Las dos gestiones de la 14 (del 3) se registraron despues de K1.
    assert valor(pa, "gestion.gestiones", vista="PERIODO", periodo=DIAS_A[3]) == 0
    assert valor(pc, "gestion.gestiones", vista="PERIODO", periodo=DIAS_A[3]) == 2
    assert valor(pc, "gestion.cuentas_contactadas") == valor(pa, "gestion.cuentas_contactadas") + 1


def test_una_anulacion_cuenta_solo_si_ocurrio_hasta_as_of_y_se_conocia(pa, pb, pc):
    # El 2: 12 gestiones en A. La 15 se anula el 6: sigue efectiva al 4 (A y C) y no al 8 (B). La 16
    # se anula el 3, pero se registra despues de K1: efectiva en A, anulada en C y en B.
    dia = DIAS_A[2]
    assert valor(pa, "gestion.gestiones", vista="PERIODO", periodo=dia) == 12
    assert valor(pc, "gestion.gestiones", vista="PERIODO", periodo=dia) == 11
    assert valor(pb, "gestion.gestiones", vista="PERIODO", periodo=dia) == 10
    assert valor(pb, "gestion.gestiones", vista="PERIODO", periodo=DIAS_B[7]) == 1


# --- atribucion y promesas ------------------------------------------------------------------------


def test_la_atribucion_no_elige_la_candidata_de_una_ambigua(pa):
    # El 1: el primer pago de la 8 tiene una candidata, el segundo dos; analytics no reparte nada.
    dia = DIAS_A[1]
    assert valor(pa, "atribucion.asociacion_unica", vista="PERIODO", periodo=dia) == 1
    assert valor(pa, "atribucion.ambigua", vista="PERIODO", periodo=dia) == 1
    assert valor(pa, "atribucion.monto_ambiguo", vista="PERIODO", periodo=dia) == Decimal("200.00")


def test_la_tasa_de_cumplimiento_no_cuenta_pendientes_ni_canceladas_ni_no_evaluables(pa, pb):
    for publicados in (pa, pb):
        fila = celda(publicados, "promesa.tasa_cumplimiento_total")
        assert (fila["numerador"], fila["denominador"]) == (3, 5)
    assert valor(pa, "promesa.pendientes") == 2 and valor(pb, "promesa.pendientes") == 1
    assert valor(pa, "promesa.no_evaluables") == 0 and valor(pb, "promesa.no_evaluables") == 1


def test_una_promesa_tiene_el_estado_de_la_evaluacion_declarada(pa):
    # Por dia de creacion: las dos de la 8 el 1 (CUMPLIDA las dos); el 2, CUMPLIDA (1), PARCIAL (9),
    # INCUMPLIDA (10), PENDIENTE (11 y 13) y CANCELADA (12).
    uno, dos = DIAS_A[1], DIAS_A[2]
    assert valor(pa, "promesa.cumplidas", vista="PERIODO", periodo=uno) == 2
    assert [
        valor(pa, f"promesa.{m}", vista="PERIODO", periodo=dos)
        for m in ("cumplidas", "parciales", "incumplidas", "pendientes", "canceladas")
    ] == [1, 1, 1, 2, 1]
    fila = celda(pa, "promesa.tasa_cumplimiento_total", vista="PERIODO", periodo=dos)
    assert (fila["valor"], fila["numerador"], fila["denominador"]) == (
        Decimal("0.3333333333"),
        1,
        3,
    )


# --- nulos, denominadores y supresion -------------------------------------------------------------


def test_un_denominador_cero_publica_null_con_su_denominador_y_no_cero(pa):
    # El 30 no hay gestiones ni promesas: no hay tasa, no es 0 %.
    for metrica in ("gestion.contactadas_sobre_gestionadas", "promesa.tasa_cumplimiento_total"):
        fila = celda(pa, metrica, vista="PERIODO", periodo=CORTE)
        assert fila["estado_valor"] == "DISPONIBLE"
        assert (fila["valor"], fila["numerador"], fila["denominador"]) == (None, 0, 0)
        assert fila["motivos"] == [{"codigo": "DENOMINADOR_CERO"}]


def test_un_saldo_atrasado_vacio_no_es_cero(pa):
    # La 20 no lo trae y la 7 trae 0: la 7 tiene dato y la 20 no.
    assert valor(pa, "cartera.saldo_atrasado_cuentas_con_dato") == 19
    assert valor(pa, "cartera.saldo_atrasado_suma") == Decimal("9000.00")


def test_una_celda_de_9_cuentas_se_suprime_y_una_de_10_se_publica(pa):
    consumo = celda(pa, "cartera.cuentas", "PRODUCTO", producto="CONSUMO")
    assert (consumo["estado_valor"], consumo["valor"], consumo["poblacion_cuentas"]) == (
        "DISPONIBLE",
        10,
        10,
    )
    assert valor(pa, "recuperacion.neta", "PRODUCTO", producto="CONSUMO") == Decimal("2700.00")
    for producto in ("TARJETA", "NOMINA"):
        fila = celda(pa, "cartera.cuentas", "PRODUCTO", producto=producto)
        assert fila["estado_valor"] == "SUPRIMIDO"
        assert fila["motivos"] == [{"codigo": "CELDA_MENOR_AL_MINIMO", "min_group_size": 10}]


def test_un_municipio_que_dejaria_reconstruir_otro_tambien_se_suprime(pa):
    # AMOZOC (5) por pequeno; PUEBLA (15) porque ENTIDAD 21 (20) menos PUEBLA daria AMOZOC.
    amozoc = celda(pa, "cartera.cuentas", "MUNICIPIO", municipio="21015")
    puebla = celda(pa, "cartera.cuentas", "MUNICIPIO", municipio="21114")
    assert amozoc["motivos"][0]["codigo"] == "CELDA_MENOR_AL_MINIMO"
    assert puebla["motivos"][0]["codigo"] == "SUPRESION_COMPLEMENTARIA"
    assert valor(pa, "cartera.cuentas", "ENTIDAD", entidad="21") == 20


def test_una_celda_suprimida_no_publica_ningun_valor_en_ninguna_metrica_ni_periodo(pa, pb):
    for publicados in (pa, pb):
        suprimidas = [f for f in publicados.values() if f["estado_valor"] == "SUPRIMIDO"]
        assert suprimidas
        for f in suprimidas:
            assert (f["valor"], f["numerador"], f["denominador"], f["poblacion_cuentas"]) == (
                None,
                None,
                None,
                None,
            )
        # La decision es por celda: si una celda esta suprimida, lo esta en todos sus periodos.
        por_celda: dict[tuple, set] = {}
        for (metrica, _vista, conjunto, _periodo, dims), f in publicados.items():
            if f["estado_valor"] in ("SUPRIMIDO", "DISPONIBLE") and conjunto != "TOTAL":
                por_celda.setdefault((metrica, conjunto, dims), set()).add(f["estado_valor"])
        assert all(len(estados) == 1 for estados in por_celda.values())


def test_total_nunca_se_suprime(pa):
    assert not [f for k, f in pa.items() if k[2] == "TOTAL" and f["estado_valor"] == "SUPRIMIDO"]


def test_una_metrica_de_estado_no_tiene_periodo(pa):
    sin_periodo = {m.codigo for m in METRICAS if Vista.PERIODO not in m.vistas}
    for codigo in sin_periodo:
        for dia in DIAS_A:
            fila = celda(pa, codigo, vista="PERIODO", periodo=dia)
            assert fila["estado_valor"] == "NO_APLICA"
            assert fila["valor"] is None and fila["motivos"] == [{"codigo": "METRICA_SIN_PERIODO"}]
    assert not [k for k in pa if k[1] == "PERIODO" and k[0] in sin_periodo and k[2] != "TOTAL"]


def test_el_periodo_parcial_y_sus_limites(pa):
    fila = celda(pa, "recuperacion.neta", vista="PERIODO", periodo=DIAS_A[4])
    assert (fila["periodo_desde"], fila["periodo_hasta"], fila["periodo_parcial"]) == (
        DIAS_A[4],
        DIAS_A[4],
        False,
    )
    acumulada = celda(pa, "recuperacion.neta")
    assert (acumulada["periodo_desde"], acumulada["periodo_hasta"]) == (CORTE, AS_OF_A)


# --- la multiplicacion de los joins ---------------------------------------------------------------


def test_tres_gestiones_dos_pagos_y_dos_promesas_no_se_multiplican(pa):
    # El 1 solo hay actividad de la 8: si un join multiplicara, serian 3 x 2 x 2 = 12 de cada una.
    dia = DIAS_A[1]
    assert valor(pa, "gestion.gestiones", vista="PERIODO", periodo=dia) == 3
    assert valor(pa, "gestion.cuentas_gestionadas", vista="PERIODO", periodo=dia) == 1
    assert valor(pa, "recuperacion.movimientos_economicos", vista="PERIODO", periodo=dia) == 2
    assert valor(pa, "recuperacion.neta", vista="PERIODO", periodo=dia) == Decimal("500.00")
    assert valor(pa, "promesa.creadas", vista="PERIODO", periodo=dia) == 2
    assert valor(pa, "promesa.monto_prometido", vista="PERIODO", periodo=dia) == Decimal("500.00")
    assert valor(pa, "atribucion.movimientos_evaluados", vista="PERIODO", periodo=dia) == 2


def test_los_controles_de_lectura_cuentan_cada_hecho_una_vez(a):
    calculados = controles(a.id)
    for codigo, esperado in (
        ("lectura.movimientos_sin_multiplicar", 11),
        ("lectura.gestiones_sin_multiplicar", 15),
        ("lectura.promesas_sin_multiplicar", 8),
        ("lectura.atribuciones_sin_multiplicar", 9),
    ):
        fila = calculados[codigo]
        assert (fila["estado"], fila["esperado"], fila["observado"]) == ("OK", esperado, esperado)


# --- campo y ruteo --------------------------------------------------------------------------------


def test_lo_planificado_es_lo_que_publicaron_campo_y_ruteo(g, pa):
    plan, ruteo = g.operacion.plan, g.operacion.ruteo
    assert (plan.candidatas_campo, plan.asignadas, plan.sin_capacidad) == (6, 4, 1)
    assert (ruteo.programadas, ruteo.sin_tiempo, ruteo.distancia_total_m) == (2, 2, 5500)
    # Una parada programada no es una visita: la unica visita registrada es la de la 17, del 2.
    assert valor(pa, "ruteo.programadas") == 2
    assert valor(pa, "gestion.visitas_campo_registradas") == 1


def test_los_embudos_son_conjuntos_anidados(pa, pb, pc):
    for publicados, esperado in (
        (pa, (20, 12, 10, 7)),
        (pc, (20, 12, 11, 7)),
        (pb, (20, 12, 10, 7)),
    ):
        assert (
            valor(publicados, "cartera.cuentas"),
            valor(publicados, "gestion.cuentas_gestionadas"),
            valor(publicados, "gestion.cuentas_contactadas"),
            valor(publicados, "promesa.cuentas_con_promesa"),
        ) == esperado
    assert (
        valor(pa, "campo.candidatas"),
        valor(pa, "campo.asignadas"),
        valor(pa, "ruteo.programadas"),
    ) == (6, 4, 2)


# --- disponibilidad -------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def d(g):
    e = analizar(g.peticion(AS_OF_D, g.k2, granularidad=Granularidad.TOTAL))
    assert e.estado == "EXITOSA", e.detalle
    return e


def test_un_dia_sin_pagos_conocidos_no_es_cero_sino_no_disponible(d):
    publicados = agregados(d.id)
    fila = celda(publicados, "recuperacion.neta", periodo=CORTE)
    assert (fila["estado_valor"], fila["valor"]) == ("NO_DISPONIBLE", None)
    assert fila["motivos"] == [
        {"codigo": "PAGOS_SIN_COBERTURA", "dias": 2, "desde": "2026-10-09", "hasta": "2026-10-10"}
    ]
    assert celda(publicados, "atribucion.ambigua")["estado_valor"] == "NO_DISPONIBLE"
    assert celda(publicados, "calidad_pagos.monto_sin_cuenta")["estado_valor"] == "NO_DISPONIBLE"
    # Lo que no depende de los pagos se publica.
    assert valor(publicados, "gestion.gestiones") == 16
    assert d.disponibilidad["familias"]["RECUPERACION"] == {
        "estado": "PARCIAL",
        "dias_sin_cobertura": 2,
    }
    assert d.disponibilidad["cobertura_pagos"]["dias_con_cobertura"] == 9


def test_una_entrada_no_declarada_no_es_cero_sino_no_disponible(d):
    publicados = agregados(d.id)
    fila = celda(publicados, "promesa.cumplidas")
    assert (fila["estado_valor"], fila["valor"]) == ("NO_DISPONIBLE", None)
    assert fila["motivos"] == [
        {"codigo": "ENTRADA_NO_DECLARADA", "entradas": ["EVALUACION_PROMESAS"]}
    ]
    # Las promesas creadas son del lifecycle: se publican.
    assert valor(publicados, "promesa.creadas") == 8
    assert d.disponibilidad["familias"]["PROMESA"] == {
        "estado": "NO_DISPONIBLE",
        "faltan": ["EVALUACION_PROMESAS"],
    }
    assert not [k for k in publicados if k[1] == "PERIODO"]


def test_null_contra_cero_en_una_cohorte_de_dos(tmp_path):
    # Dos cuentas en un corte aparte, sin nada mas declarado: la 201 sin saldo atrasado y la 202 con
    # 0. Con dato, una de dos; si el vacio fuera cero, serian dos de dos.
    corte = date(2026, 9, 16)
    ingerir(
        tmp_path,
        [
            CuentaGolden(201, 15, "CONSUMO", PUEBLA, (1_000, 1_000), saldo_atrasado=None),
            CuentaGolden(202, 15, "CONSUMO", PUEBLA, (1_100, 1_000), saldo_atrasado="0.00"),
        ],
        corte,
    )

    e = analizar(
        Peticion(
            corte_id=corte_de(corte).corte_id,
            as_of=corte,
            modo=Modo.OPERATIVO,
            zona_horaria=ZONA,
            granularidad=Granularidad.TOTAL,
        )
    )

    assert e.estado == "EXITOSA", e.detalle
    publicados = agregados(e.id)
    fila = celda(publicados, "cartera.saldo_atrasado_cobertura", periodo=corte)
    assert (fila["valor"], fila["numerador"], fila["denominador"]) == (Decimal("0.5"), 1, 2)
    assert valor(publicados, "cartera.saldo_atrasado_suma", periodo=corte) == Decimal("0.00")
    # Sin otras entradas, cada familia que las necesita es NO_DISPONIBLE, nunca cero.
    for metrica in (
        "recuperacion.neta",
        "decision.cuentas",
        "geografia.validas",
        "campo.asignadas",
    ):
        assert celda(publicados, metrica, periodo=corte)["estado_valor"] == "NO_DISPONIBLE"
    assert valor(publicados, "gestion.gestiones", periodo=corte) == 0
    assert e.disponibilidad["entradas"] == ["CORTE"]
