"""decision/v2 sobre el escenario golden (`decision_v2_escenarios.golden`): una cuenta por cada caso
de la mision, dos fronteras de conocimiento sobre el mismo corte (D1 con known_at K1 y D2 con K2), y
lo que cada decision tiene que decir, feature por feature y motivo por motivo.

El escenario se construye una vez por modulo; ninguna prueba escribe."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from decision_v2_escenarios import (
    AS_OF,
    SALIDA,
    codigos,
    features_de,
    foto,
    golden,
    resultados_de,
)
from sqlalchemy import text

from motor_cartera.db.sesion import sesion
from motor_cartera.decision_v2.features import FEATURES, feature_hash
from motor_cartera.decision_v2.politica import (
    PUBLIC_V1,
    VERSION_MOTOR,
    VERSION_POLITICA,
    decidir,
    decision_hash,
    hash_politica,
)

pytestmark = pytest.mark.usefixtures("bd_del_modulo")

REC, VIG, SAT, INC = (
    "RECUPERACION_RECIENTE",
    "PROMESA_VIGENTE",
    "SATURACION_SIN_CONTACTO",
    "PROMESA_INCUMPLIDA_RECIENTE",
)


@pytest.fixture(scope="module")
def g(bd_del_modulo, tmp_path_factory):
    return golden(tmp_path_factory.mktemp("golden"))


@pytest.fixture(scope="module")
def d1(g):
    return {"features": features_de(g.d1.id), "resultados": resultados_de(g.d1.id)}


@pytest.fixture(scope="module")
def d2(g):
    return {"features": features_de(g.d2.id), "resultados": resultados_de(g.d2.id)}


# --- el universo y la ejecucion -------------------------------------------------------------------


def test_las_dos_decisiones_publican_una_decision_por_cuenta_del_corte_y_ninguna_mas(g, d1, d2):
    universo = {n for n in range(1, 32) if n != SALIDA}
    for ejecucion, decision in ((g.d1, d1), (g.d2, d2)):
        assert (ejecucion.estado, ejecucion.resultado) == ("EXITOSA", "DECISION_PUBLICADA")
        assert ejecucion.cuentas_objetivo == ejecucion.cuentas_decididas == len(universo)
        assert ejecucion.cuentas_fallidas == 0
        assert set(decision["resultados"]) == set(decision["features"]) == universo
    # La 30 dejo de estar en el corte: aunque tiene una gestion, no es del universo.
    assert SALIDA not in d1["resultados"]


def test_cada_ejecucion_guarda_sus_entradas_sus_versiones_y_su_firma(g):
    for ejecucion, known_at in ((g.d1, g.k1), (g.d2, g.k2)):
        assert (ejecucion.as_of, ejecucion.known_at) == (AS_OF, known_at)
        assert (ejecucion.version_motor, ejecucion.version_features) == (
            VERSION_MOTOR,
            "features-decision/v1",
        )
        assert ejecucion.version_politica == VERSION_POLITICA
        assert ejecucion.hash_politica == hash_politica(PUBLIC_V1)
        assert ejecucion.zona_horaria == "America/Mexico_City"
    # El mismo corte y el mismo as_of con otro known_at son otra entrada logica.
    assert g.d1.firma_inputs != g.d2.firma_inputs


# --- lo que decidio D1, cuenta por cuenta ---------------------------------------------------------

ESPERADO_D1 = {
    # numero: (base, ajuste, final, canal, modificadores que aplicaron)
    1: ("MEDIA", 0, "MEDIA", "DIGITAL", []),  # sin historia
    2: ("ALTA", 0, "ALTA", "TELEFONICA", []),  # mora base unicamente
    3: ("ALTA", -1, "MEDIA", "DIGITAL", [REC]),  # pago reciente
    4: ("MEDIA", -1, "BAJA", "DIGITAL", [VIG]),  # promesa vigente
    5: ("ALTA", 1, "MUY_ALTA", "CAMPO", [SAT]),  # saturacion sin contacto
    6: ("MEDIA", 1, "ALTA", "TELEFONICA", [INC]),  # promesa incumplida
    7: ("ALTA", 0, "ALTA", "TELEFONICA", [REC, INC]),  # senales que se cancelan
    8: ("MEDIA", 2, "MUY_ALTA", "CAMPO", [SAT, INC]),  # dos positivas
    9: ("ALTA", -2, "BAJA", "DIGITAL", [REC, VIG]),  # dos negativas
    10: ("MEDIA", 0, "MEDIA", "DIGITAL", []),  # reingreso
    11: ("MEDIA", 0, "MEDIA", "DIGITAL", []),  # ausente en el corte anterior
    12: ("ALTA", 0, "ALTA", "TELEFONICA", []),  # pago revertido antes de as_of
    13: ("ALTA", -1, "MEDIA", "DIGITAL", [REC]),  # revertido despues de as_of: ese dia pago
    14: ("MEDIA", -1, "BAJA", "DIGITAL", [REC]),  # asociacion unica
    15: ("MEDIA", -1, "BAJA", "DIGITAL", [REC]),  # atribucion ambigua
    16: ("MEDIA", 0, "MEDIA", "DIGITAL", []),  # pago futuro
    17: ("MEDIA", 0, "MEDIA", "DIGITAL", []),  # corte historico publicado tarde
    18: ("MEDIA", 0, "MEDIA", "DIGITAL", []),  # promesa cancelada
    19: ("MEDIA", 0, "MEDIA", "DIGITAL", []),  # promesa anulada con su gestion
    20: ("MEDIA", 0, "MEDIA", "DIGITAL", []),  # pago de junio
    21: ("MEDIA", 0, "MEDIA", "DIGITAL", []),  # promesa registrada despues de K1
    22: ("MUY_ALTA", 1, "MUY_ALTA", "CAMPO", [SAT]),  # MUY_ALTA no sube mas
    23: ("BAJA", -1, "BAJA", "DIGITAL", [REC]),  # BAJA no baja mas
    24: ("ALTA", 0, "ALTA", "TELEFONICA", []),  # la tercera sin contacto llego tarde
    25: ("MEDIA", 0, "MEDIA", "DIGITAL", []),  # un evento posterior a as_of
    26: ("MEDIA", 0, "MEDIA", "DIGITAL", []),  # sin denominador para la tasa
    27: ("MEDIA", 0, "MEDIA", "DIGITAL", []),  # convenio vigente
    28: ("MEDIA", 0, "MEDIA", "DIGITAL", []),  # convenio cancelado
    29: ("MEDIA", 0, "MEDIA", "DIGITAL", []),  # promesa cumplida
    31: ("MEDIA", 0, "MEDIA", "DIGITAL", []),  # promesa parcial
}


@pytest.mark.parametrize("numero", sorted(ESPERADO_D1))
def test_d1_decide_cada_cuenta_como_dice_su_caso(d1, numero):
    r = d1["resultados"][numero]
    base, ajuste, final, canal, aplicados = ESPERADO_D1[numero]

    assert (r.prioridad_base, r.ajuste_total, r.prioridad_final, r.canal_recomendado) == (
        base,
        ajuste,
        final,
        canal,
    )
    assert codigos(r) == [
        "PRIORIDAD_BASE_MORA",
        *aplicados,
        "PRIORIDAD_FINAL",
        "CANAL_POR_PRIORIDAD",
    ]


def test_el_acotamiento_se_explica_en_el_motivo_final(d1):
    for numero, nivel_sin_acotar, nivel in ((22, 5, 4), (23, 0, 1), (8, 4, 4), (9, 1, 1)):
        final = d1["resultados"][numero].motivos[-2]
        assert final["codigo"] == "PRIORIDAD_FINAL"
        assert (final["nivel_sin_acotar"], final["nivel"]) == (nivel_sin_acotar, nivel)
        assert final["acotada"] is (nivel_sin_acotar != nivel)


def test_cada_motivo_dice_la_feature_su_valor_el_operador_el_umbral_y_el_efecto(d1):
    motivos = d1["resultados"][7].motivos
    assert motivos[0] == {
        "codigo": "PRIORIDAD_BASE_MORA",
        "condiciones": [
            {"feature": "dias_atraso", "valor": "45", "operador": "EN_TRAMO", "umbral": "31-60"}
        ],
        "segmento": "MORA_MEDIA",
        "nivel": 3,
        "prioridad": "ALTA",
    }
    assert motivos[1] == {
        "codigo": REC,
        "condiciones": [
            {
                "feature": "recuperacion_interpretada_7d",
                "valor": "300.00",
                "operador": ">",
                "umbral": "0.00",
            }
        ],
        "efecto": -1,
    }
    assert motivos[2] == {
        "codigo": INC,
        "condiciones": [
            {"feature": "promesas_incumplidas_30d", "valor": "1", "operador": ">=", "umbral": "1"}
        ],
        "efecto": 1,
    }
    assert motivos[3] == {
        "codigo": "PRIORIDAD_FINAL",
        "nivel_base": 3,
        "ajuste_total": 0,
        "nivel_sin_acotar": 3,
        "nivel": 3,
        "prioridad": "ALTA",
        "acotada": False,
    }
    assert motivos[4] == {
        "codigo": "CANAL_POR_PRIORIDAD",
        "prioridad": "ALTA",
        "canal": "TELEFONICA",
    }
    saturacion = d1["resultados"][5].motivos[1]
    assert saturacion["condiciones"] == [
        {"feature": "gestiones_sin_contacto_7d", "valor": "4", "operador": ">=", "umbral": "3"},
        {"feature": "contactos_titular_30d", "valor": "0", "operador": "=", "umbral": "0"},
    ]


# --- las features, por familia --------------------------------------------------------------------


def test_snapshot_e_historia(d1, d2):
    f = d1["features"]
    assert (f[2]["dias_atraso"], f[2]["semanas_atraso"], f[2]["producto"]) == (45, 7, "NOMINA")
    # La 2 paso de 38 a 45 dias y de 9,038 a 10,045 de saldo total desde el corte anterior.
    assert f[2]["delta_dias_atraso_desde_corte_anterior"] == 7
    assert f[2]["delta_saldo_total_desde_corte_anterior"] == Decimal("1007.00")
    assert (f[2]["cortes_observados"], f[2]["continuo_desde_corte_anterior"]) == (4, True)
    # Reingreso: falto en un corte y volvio; el snapshot anterior es el del corte anterior.
    assert (f[10]["cortes_observados"], f[10]["reingresos_observados"]) == (3, 1)
    assert f[10]["continuo_desde_corte_anterior"] is True
    # Falto justo en el corte anterior: hay delta, pero no es de un periodo continuo.
    assert (f[11]["reingresos_observados"], f[11]["continuo_desde_corte_anterior"]) == (1, False)
    assert f[11]["delta_saldo_total_desde_corte_anterior"] == Decimal("0.00")
    # Sin corte anterior: NULL, no cero.
    assert f[1]["cortes_observados"] == 1 and f[1]["reingresos_observados"] == 0
    assert f[1]["delta_saldo_total_desde_corte_anterior"] is None
    assert f[1]["delta_dias_atraso_desde_corte_anterior"] is None
    assert f[1]["continuo_desde_corte_anterior"] is None
    # El corte viejo del 19 de agosto llego despues de K1: D1 no lo ve, D2 si, y para la 17 es
    # un reingreso (estaba el 19, falto el 26 y volvio el 2).
    assert (f[17]["cortes_observados"], f[17]["reingresos_observados"]) == (3, 0)
    tarde = d2["features"][17]
    assert (tarde["cortes_observados"], tarde["reingresos_observados"]) == (4, 1)


def test_gestiones_y_contactabilidad(d1):
    f = d1["features"]
    cinco = f[5]
    assert (cinco["gestiones_7d"], cinco["gestiones_30d"], cinco["gestiones_sin_contacto_7d"]) == (
        4,
        4,
        4,
    )
    assert (cinco["contactos_titular_30d"], cinco["gestiones_contactables_30d"]) == (0, 4)
    assert cinco["tasa_contacto_titular_30d"] == Decimal("0.0000")
    assert (cinco["ultima_gestion_canal"], cinco["ultima_gestion_nivel_contacto"]) == (
        "TELEFONICA",
        "SIN_CONTACTO",
    )
    assert cinco["dias_desde_ultima_gestion"] == 3  # la ultima fue el 13
    assert f[14]["tasa_contacto_titular_30d"] == Decimal("1.0000")
    assert f[15]["tasa_contacto_titular_30d"] == Decimal("0.5000")
    # Solo mensajes de una via: hay gestiones, pero ningun intento en que el contacto aplique.
    sms = f[26]
    assert (sms["gestiones_30d"], sms["gestiones_7d"], sms["gestiones_contactables_30d"]) == (
        2,
        1,
        0,
    )
    assert sms["tasa_contacto_titular_30d"] is None
    assert sms["ultima_gestion_nivel_contacto"] == "NO_APLICA"
    # Sin gestiones: NULL, no cero.
    assert f[1]["dias_desde_ultima_gestion"] is None
    assert f[1]["ultima_gestion_canal"] is None and f[1]["tasa_contacto_titular_30d"] is None
    assert (f[1]["gestiones_7d"], f[1]["gestiones_30d"], f[1]["visitas_30d"]) == (0, 0, 0)


def test_promesas_vigente_cancelada_anulada_incumplida_cumplida_y_parcial(d1):
    f = d1["features"]
    assert (f[4]["promesa_vigente"], f[4]["promesas_30d"], f[4]["promesas_90d"]) == (True, 1, 1)
    # Cancelada: se hizo, pero no esta vigente.
    assert (f[18]["promesa_vigente"], f[18]["promesas_30d"]) == (False, 1)
    # Anulada con su gestion: se registro por error y no existe; tampoco su gestion.
    assert (f[19]["promesa_vigente"], f[19]["promesas_30d"], f[19]["gestiones_30d"]) == (
        False,
        0,
        0,
    )
    for numero in (6, 7, 8):
        assert (f[numero]["promesas_incumplidas_30d"], f[numero]["promesas_incumplidas_90d"]) == (
            1,
            1,
        )
    # Cumplida el 20 de agosto: se acordo el 15, fuera de los 30 dias, dentro de los 90.
    assert (f[29]["promesas_cumplidas_90d"], f[29]["promesas_30d"], f[29]["promesas_90d"]) == (
        1,
        0,
        1,
    )
    assert f[31]["promesas_parciales_90d"] == 1
    # La 9 pago 200 de 1,000 antes de su fecha limite: sigue PENDIENTE, no PARCIAL.
    assert (f[9]["promesa_vigente"], f[9]["promesas_parciales_90d"]) == (True, 0)


def test_convenios(d1):
    f = d1["features"]
    assert (f[27]["convenio_vigente"], f[27]["convenios_90d"]) == (True, 1)
    assert (f[28]["convenio_vigente"], f[28]["convenios_90d"]) == (False, 1)
    assert (f[1]["convenio_vigente"], f[1]["convenios_90d"]) == (False, 0)


def test_pagos_reversos_y_null(d1):
    f = d1["features"]
    assert f[3]["recuperacion_interpretada_7d"] == Decimal("500.00")
    assert (f[3]["movimientos_pago_7d"], f[3]["dias_desde_ultimo_pago"]) == (1, 2)
    # Revertido el 13: no infla ninguna ventana y no hay ultimo pago.
    doce = f[12]
    assert (
        doce["recuperacion_interpretada_7d"],
        doce["recuperacion_interpretada_30d"],
        doce["recuperacion_interpretada_90d"],
    ) == (Decimal("0.00"),) * 3
    assert (doce["movimientos_pago_7d"], doce["dias_desde_ultimo_pago"]) == (0, None)
    # Revertido el 18, despues de as_of: ese dia era un pago.
    assert f[13]["recuperacion_interpretada_7d"] == Decimal("300.00")
    # Recibido el 20, despues de as_of: no existe para esta decision.
    assert f[16]["recuperacion_interpretada_7d"] == Decimal("0.00")
    assert (f[16]["movimientos_pago_30d"], f[16]["dias_desde_ultimo_pago"]) == (0, None)
    # El de junio: fuera de 30 dias, a 98 del corte.
    assert (f[20]["recuperacion_interpretada_30d"], f[20]["dias_desde_ultimo_pago"]) == (
        Decimal("0.00"),
        98,
    )
    assert f[29]["recuperacion_interpretada_30d"] == Decimal("1000.00")
    assert f[31]["recuperacion_interpretada_90d"] == Decimal("400.00")
    # Sin pagos pero con la informacion disponible: cero conocido, no NULL.
    assert (f[1]["recuperacion_interpretada_7d"], f[1]["movimientos_pago_7d"]) == (
        Decimal("0.00"),
        0,
    )
    assert f[1]["dias_desde_ultimo_pago"] is None


def test_atribucion_solo_cuenta_la_asociacion_unica(d1):
    f = d1["features"]
    unica = f[14]
    assert (unica["pagos_asociados_unicos_30d"], unica["recuperacion_asociada_unica_30d"]) == (
        1,
        Decimal("400.00"),
    )
    assert unica["dias_gestion_a_pago_mediana_90d"] == Decimal("2.00")
    ambigua = f[15]
    assert (ambigua["pagos_asociados_unicos_30d"], ambigua["recuperacion_asociada_unica_30d"]) == (
        0,
        Decimal("0.00"),
    )
    assert ambigua["dias_gestion_a_pago_mediana_90d"] is None
    # El pago revertido no tiene asociacion que contar.
    assert f[12]["pagos_asociados_unicos_30d"] == 0


def test_la_disponibilidad_explica_las_ventanas_y_el_horizonte_no_pasa_de_as_of(g):
    disponibilidad = g.d1.disponibilidad
    assert disponibilidad["pagos"] == {"7d": True, "30d": True, "90d": True}
    assert disponibilidad["atribucion"] == {"30d": True, "90d": True}
    # Los pagos del 18 y del 20 ya se conocian, pero son posteriores a as_of: no certifican nada.
    assert disponibilidad["horizonte_pagos"] == "2026-09-16T10:00:00"
    assert disponibilidad["primer_pago_observado"] == "2026-06-10T10:00:00"
    meses = {m["mes"]: m for m in disponibilidad["meses"]}
    assert set(meses) == {"2026-06-01", "2026-07-01", "2026-08-01", "2026-09-01"}
    # Julio no tuvo pagos, dentro del rango de los datos: esta cubierto, con cero.
    assert meses["2026-07-01"] == {
        "mes": "2026-07-01",
        "pagos_observados": 0,
        "pagos_interpretados": None,
        "pagos": True,
        "atribucion": True,
    }
    # Septiembre: los 9 pagos conocidos antes del final de as_of, cada uno interpretado.
    assert (
        meses["2026-09-01"]["pagos_observados"],
        meses["2026-09-01"]["pagos_interpretados"],
    ) == (
        9,
        9,
    )


def test_la_distribucion_cuenta_lo_que_publico(g, d1):
    distribucion = g.d1.distribucion
    resultados = d1["resultados"].values()
    assert distribucion["cuentas"] == 30
    assert distribucion["prioridad_final"] == {
        p: sum(1 for r in resultados if r.prioridad_final == p)
        for p in ("BAJA", "MEDIA", "ALTA", "MUY_ALTA")
    }
    assert distribucion["modificadores"] == {REC: 7, VIG: 2, SAT: 3, INC: 3}
    assert sum(distribucion["canal_recomendado"].values()) == 30


# --- lo que llego tarde ---------------------------------------------------------------------------


def test_d2_ve_exactamente_lo_que_llego_despues_de_k1(d1, d2):
    distintas = {
        n
        for n in d1["resultados"]
        if d1["resultados"][n].decision_hash != d2["resultados"][n].decision_hash
    }
    # La promesa tardia (21), la tercera gestion sin contacto (24) y el corte viejo (17). La
    # gestion del 20, posterior a as_of, no cambia nada (25).
    assert distintas == {17, 21, 24}
    assert d1["features"][25] == d2["features"][25]
    r21, r24 = d2["resultados"][21], d2["resultados"][24]
    assert (r21.prioridad_final, codigos(r21)[1:-2]) == ("BAJA", [VIG])
    assert (r24.prioridad_final, codigos(r24)[1:-2]) == ("MUY_ALTA", [SAT])
    assert d2["features"][24]["gestiones_sin_contacto_7d"] == 3
    assert d1["features"][24]["gestiones_sin_contacto_7d"] == 2
    assert (d1["features"][21]["promesa_vigente"], d2["features"][21]["promesa_vigente"]) == (
        False,
        True,
    )


def test_d1_queda_intacta_despues_de_d2(g):
    assert foto(g.d1.id) == g.foto_d1


# --- la equivalencia con el nucleo puro -----------------------------------------------------------


@pytest.mark.parametrize("cual", ["d1", "d2"])
def test_cada_hash_y_cada_motivo_son_los_del_nucleo_puro(g, cual):
    """Lo que PostgreSQL publico es exactamente lo que `features` y `politica` dicen de esas mismas
    features: el feature_hash, la decision, los motivos y el decision_hash."""
    ejecucion = getattr(g, cual)
    features, resultados = features_de(ejecucion.id), resultados_de(ejecucion.id)
    for numero, valores in features.items():
        guardado = valores.pop("feature_hash")
        assert feature_hash(valores) == guardado, numero
        pura = decidir(valores)
        r = resultados[numero]
        assert (pura.prioridad_base, pura.ajuste_total, pura.prioridad_final) == (
            r.prioridad_base,
            r.ajuste_total,
            r.prioridad_final,
        ), numero
        assert (pura.nivel_base, pura.nivel_final, pura.canal_recomendado) == (
            r.score_nivel_base,
            r.score_nivel_final,
            r.canal_recomendado,
        ), numero
        assert pura.motivos() == r.motivos, numero
        assert decision_hash(guardado, pura) == r.decision_hash, numero


def test_ninguna_feature_null_donde_no_puede_serlo(d1, d2):
    for decision in (d1, d2):
        for valores in decision["features"].values():
            for f in FEATURES:
                if not f.nula:
                    assert valores[f.nombre] is not None, f.nombre


def test_las_tablas_temporales_no_sobreviven_a_la_transaccion(g):
    with sesion() as s:
        temporales = s.execute(
            text(
                "SELECT count(*) FROM pg_class WHERE relname LIKE 'dv2_%' AND relpersistence = 't'"
            )
        ).scalar_one()
    assert temporales == 0


def test_el_as_of_es_el_dia_del_corte(g):
    assert g.d1.as_of == g.d2.as_of == date(2026, 9, 16)
