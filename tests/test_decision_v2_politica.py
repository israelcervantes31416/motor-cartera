"""decision-policy/public-v1 como nucleo puro, sin base: la base de decision/v1, cada modificador
solo y en combinacion, el acotamiento, lo que hace un NULL, las invariantes sobre todas las
combinaciones, los motivos, el hash de la politica y el de la decision."""

from __future__ import annotations

import itertools
from dataclasses import replace
from decimal import Decimal

import pytest

from motor_cartera.atraso import tramo_de_atraso
from motor_cartera.decision.reglas import (
    CANAL_POR_PRIORIDAD,
    PRIORIDAD_BASE,
    SEGMENTO_POR_TRAMO,
    EntradaDecision,
    decidir_cuenta,
)
from motor_cartera.decision_v2.features import FEATURES, Tipo, feature_hash
from motor_cartera.decision_v2.politica import (
    NIVEL,
    PRIORIDAD_DEL_NIVEL,
    PUBLIC_V1,
    CodigoMotivo,
    Condicion,
    CuentaNoDecidible,
    Modificador,
    Operador,
    Politica,
    decidir,
    decision_hash,
    definicion,
    hash_politica,
)

REC, VIG, SAT, INC = (
    CodigoMotivo.RECUPERACION_RECIENTE,
    CodigoMotivo.PROMESA_VIGENTE,
    CodigoMotivo.SATURACION_SIN_CONTACTO,
    CodigoMotivo.PROMESA_INCUMPLIDA_RECIENTE,
)

HASH_DE_PUBLIC_V1 = "2f9687400863f6af49a3c0fc51fe10c6c3b6f080804fcd4ef643ddad641d29e3"
"""El de la definicion de decision-policy/public-v1. Si cambia una regla, un umbral, un efecto o una
correspondencia, cambia el hash: y eso exige otra version de la politica, no editar esta."""

NEUTRAS = {
    Tipo.ENTERO: 0,
    Tipo.IMPORTE: Decimal("0.00"),
    Tipo.TASA: None,
    Tipo.DIAS: None,
    Tipo.TEXTO: "CONSUMO",
    Tipo.BOOLEANO: False,
}


def _features(**cambios) -> dict:
    """Features de una cuenta en las que ningun modificador aplica: sin pagos, sin promesas, sin
    gestiones; 45 dias de atraso."""
    valores = {f.nombre: None if f.nula else NEUTRAS[f.tipo] for f in FEATURES}
    valores.update(
        dias_atraso=45,
        saldo_total=Decimal("10000.00"),
        cortes_observados=1,
        recuperacion_interpretada_7d=Decimal("0.00"),
    )
    valores.update(cambios)
    return valores


SENALES = {
    REC: {"recuperacion_interpretada_7d": Decimal("500.00")},
    VIG: {"promesa_vigente": True},
    SAT: {"gestiones_sin_contacto_7d": 3, "contactos_titular_30d": 0},
    INC: {"promesas_incumplidas_30d": 1},
}
EFECTO = {REC: -1, VIG: -1, SAT: 1, INC: 1}


def _aplicadas(decision) -> list[CodigoMotivo]:
    return [r.codigo for r in decision.reglas if r.activa]


# --- la base --------------------------------------------------------------------------------------


@pytest.mark.parametrize("dias", [0, 1, 15, 30, 31, 45, 60, 61, 90, 91, 400, 3650])
def test_la_base_es_exactamente_la_prioridad_base_de_decision_v1(dias):
    decision = decidir(_features(dias_atraso=dias))

    segmento = SEGMENTO_POR_TRAMO[tramo_de_atraso(dias)]
    assert decision.prioridad_base == PRIORIDAD_BASE[segmento]
    assert decision.segmento == segmento
    assert decision.tramo == tramo_de_atraso(dias)
    # La misma que decision/v1 da sin su ajuste por saldo (un saldo bajo no lo activa).
    v1 = decidir_cuenta(EntradaDecision(dias_atraso=dias, saldo_total=Decimal("100.00")))
    assert decision.prioridad_base == v1.prioridad


def test_el_ajuste_por_saldo_de_v1_no_es_parte_de_public_v1():
    saldo_alto = _features(dias_atraso=15, saldo_total=Decimal("900000.00"))

    decision = decidir(saldo_alto)

    assert (decision.prioridad_base, decision.prioridad_final) == ("MEDIA", "MEDIA")
    v1 = decidir_cuenta(EntradaDecision(dias_atraso=15, saldo_total=Decimal("900000.00")))
    assert v1.prioridad == "ALTA"  # v1 si la sube: es la diferencia que la sombra cuenta aparte


def test_base_sola_sin_ninguna_senal():
    decision = decidir(_features())

    assert _aplicadas(decision) == []
    assert (decision.ajuste_total, decision.prioridad_final, decision.canal_recomendado) == (
        0,
        "ALTA",
        "TELEFONICA",
    )
    assert [m["codigo"] for m in decision.motivos()] == [
        "PRIORIDAD_BASE_MORA",
        "PRIORIDAD_FINAL",
        "CANAL_POR_PRIORIDAD",
    ]


@pytest.mark.parametrize(
    ("codigo", "final"),
    [(REC, "MEDIA"), (VIG, "MEDIA"), (SAT, "MUY_ALTA"), (INC, "MUY_ALTA")],
)
def test_cada_modificador_solo(codigo, final):
    decision = decidir(_features(**SENALES[codigo]))

    assert _aplicadas(decision) == [codigo]
    assert decision.ajuste_total == EFECTO[codigo]
    assert decision.prioridad_final == final
    assert decision.canal_recomendado == CANAL_POR_PRIORIDAD[decision.prioridad_final]


@pytest.mark.parametrize(
    ("codigos", "ajuste", "final"),
    [
        ((REC, INC), 0, "ALTA"),  # -1 +1: se cancelan, y la explicacion muestra las dos
        ((REC, VIG), -2, "BAJA"),  # -1 -1
        ((SAT, INC), 2, "MUY_ALTA"),  # +1 +1, desde MEDIA para no acotar
    ],
    ids=["se-cancelan", "dos-negativas", "dos-positivas"],
)
def test_las_senales_se_suman_sin_que_una_oculte_a_otra(codigos, ajuste, final):
    cambios = {k: v for c in codigos for k, v in SENALES[c].items()}
    dias = 15 if ajuste > 0 else 45

    decision = decidir(_features(dias_atraso=dias, **cambios))

    assert sorted(_aplicadas(decision)) == sorted(codigos)
    assert (decision.ajuste_total, decision.prioridad_final) == (ajuste, final)
    explicadas = [m["codigo"] for m in decision.motivos()[1:-2]]
    assert sorted(explicadas) == sorted(codigos)


def test_baja_nunca_baja_mas_y_muy_alta_nunca_sube_mas():
    todas_negativas = {**SENALES[REC], **SENALES[VIG]}
    todas_positivas = {**SENALES[SAT], **SENALES[INC]}

    baja = decidir(_features(dias_atraso=0, **todas_negativas))
    muy_alta = decidir(_features(dias_atraso=200, **todas_positivas))

    assert (baja.prioridad_base, baja.ajuste_total, baja.prioridad_final) == ("BAJA", -2, "BAJA")
    assert (baja.nivel_sin_acotar, baja.nivel_final, baja.acotada) == (-1, 1, True)
    assert (muy_alta.prioridad_base, muy_alta.ajuste_total) == ("MUY_ALTA", 2)
    assert (muy_alta.prioridad_final, muy_alta.nivel_sin_acotar, muy_alta.acotada) == (
        "MUY_ALTA",
        6,
        True,
    )
    final = muy_alta.motivos()[-2]
    assert (final["nivel_sin_acotar"], final["nivel"], final["acotada"]) == (6, 4, True)


@pytest.mark.parametrize(
    ("cambios", "por_que"),
    [
        ({"recuperacion_interpretada_7d": Decimal("0.00")}, "cero no es recuperacion"),
        ({"recuperacion_interpretada_7d": Decimal("-50.00")}, "neta negativa"),
        ({"recuperacion_interpretada_7d": None}, "NULL: no disponible"),
        ({"gestiones_sin_contacto_7d": 2}, "dos no saturan"),
        ({"gestiones_sin_contacto_7d": 5, "contactos_titular_30d": 1}, "hubo titular"),
        ({"promesas_incumplidas_30d": 0}, "ninguna incumplida"),
        ({"promesa_vigente": False}, "sin promesa vigente"),
    ],
)
def test_los_umbrales_son_estrictos_y_un_null_no_activa_nada(cambios, por_que):
    decision = decidir(_features(**cambios))

    assert _aplicadas(decision) == [], por_que
    assert decision.ajuste_total == 0


def test_una_condicion_sobre_null_se_explica_con_valor_null():
    decision = decidir(_features(recuperacion_interpretada_7d=None))

    (condicion,) = next(r for r in decision.reglas if r.codigo == REC).condiciones
    assert (condicion.valor, condicion.cumple) == (None, False)
    assert condicion.motivo() == {
        "feature": "recuperacion_interpretada_7d",
        "valor": None,
        "operador": ">",
        "umbral": "0.00",
    }


# --- invariantes sobre todas las combinaciones ----------------------------------------------------


@pytest.mark.parametrize("dias", [0, 15, 45, 75, 120])
def test_invariantes_de_la_politica_en_cada_combinacion_de_senales(dias):
    for estados in itertools.product([False, True], repeat=4):
        codigos = [c for c, activa in zip((REC, VIG, SAT, INC), estados, strict=True) if activa]
        cambios = {k: v for c in codigos for k, v in SENALES[c].items()}
        decision = decidir(_features(dias_atraso=dias, **cambios))
        motivos = decision.motivos()

        # La prioridad siempre es de la escala, y el nivel es su lugar en ella.
        assert decision.prioridad_final in NIVEL
        assert NIVEL[decision.prioridad_final] == decision.nivel_final
        # El ajuste es la suma de los efectos; el nivel final, la suma acotada.
        assert decision.ajuste_total == sum(r.efecto for r in decision.reglas)
        assert decision.ajuste_total == sum(EFECTO[c] for c in codigos)
        assert decision.nivel_final == min(4, max(1, decision.nivel_base + decision.ajuste_total))
        # El canal es exactamente el de decision/v1 para la prioridad final.
        assert decision.canal_recomendado == CANAL_POR_PRIORIDAD[decision.prioridad_final]
        # Cada modificador activo tiene su motivo, con su efecto, y ningun motivo sin modificador.
        explicados = motivos[1:-2]
        assert [m["codigo"] for m in explicados] == codigos
        assert all(m["efecto"] == EFECTO[m["codigo"]] for m in explicados)
        assert (motivos[0]["codigo"], motivos[-2]["codigo"], motivos[-1]["codigo"]) == (
            "PRIORIDAD_BASE_MORA",
            "PRIORIDAD_FINAL",
            "CANAL_POR_PRIORIDAD",
        )
        assert motivos[-2]["ajuste_total"] == decision.ajuste_total
        assert motivos[-1]["canal"] == decision.canal_recomendado


def test_el_orden_de_las_features_no_cambia_nada():
    valores = _features(**SENALES[REC], **SENALES[SAT])
    invertido = dict(reversed(list(valores.items())))

    assert decidir(valores) == decidir(invertido)
    assert feature_hash(valores) == feature_hash(invertido)


def test_una_cuenta_sin_dias_de_atraso_validos_no_se_decide():
    for dias in (-1, None, True, 4.5):
        with pytest.raises(CuentaNoDecidible):
            decidir(_features(dias_atraso=dias))


# --- la definicion y su hash ----------------------------------------------------------------------


def test_public_v1_tiene_exactamente_sus_cuatro_modificadores():
    assert [m.codigo for m in PUBLIC_V1.modificadores] == [REC, VIG, SAT, INC]
    assert [m.efecto for m in PUBLIC_V1.modificadores] == [-1, -1, 1, 1]
    assert PUBLIC_V1.version == "decision-policy/public-v1"


def test_el_hash_de_la_politica_es_estable_y_cambia_con_cualquier_regla():
    assert hash_politica(PUBLIC_V1) == HASH_DE_PUBLIC_V1
    assert hash_politica() == hash_politica(PUBLIC_V1)
    saturacion = PUBLIC_V1.modificadores[2]
    otro_umbral = replace(
        saturacion,
        condiciones=(
            Condicion("gestiones_sin_contacto_7d", Operador.MAYOR_O_IGUAL, 4),
            saturacion.condiciones[1],
        ),
    )
    otro_efecto = replace(PUBLIC_V1.modificadores[0], efecto=1)
    for cambio in (
        Politica(
            PUBLIC_V1.version,
            (*PUBLIC_V1.modificadores[:2], otro_umbral, PUBLIC_V1.modificadores[3]),
        ),
        Politica(PUBLIC_V1.version, (otro_efecto, *PUBLIC_V1.modificadores[1:])),
        Politica(PUBLIC_V1.version, PUBLIC_V1.modificadores[:3]),
        Politica("decision-policy/public-v2", PUBLIC_V1.modificadores),
    ):
        assert hash_politica(cambio) != HASH_DE_PUBLIC_V1


def test_la_definicion_dice_la_base_de_v1_los_modificadores_y_el_canal():
    d = definicion()

    assert d["base"]["de"] == "decision/v1"
    assert [(t["tramo"], t["prioridad"]) for t in d["base"]["tramos"]] == [
        ("0", "BAJA"),
        ("1-30", "MEDIA"),
        ("31-60", "ALTA"),
        ("61-90", "ALTA"),
        ("91+", "MUY_ALTA"),
    ]
    assert d["modificadores"][2] == {
        "codigo": "SATURACION_SIN_CONTACTO",
        "condiciones": [
            {"feature": "gestiones_sin_contacto_7d", "operador": ">=", "umbral": "3"},
            {"feature": "contactos_titular_30d", "operador": "=", "umbral": "0"},
        ],
        "efecto": 1,
    }
    assert d["canal"] == {p: CANAL_POR_PRIORIDAD[p] for p in NIVEL}
    assert d["niveles"] == {"BAJA": 1, "MEDIA": 2, "ALTA": 3, "MUY_ALTA": 4}
    assert d["acotado"] == [1, 4]
    assert PRIORIDAD_DEL_NIVEL == {1: "BAJA", 2: "MEDIA", 3: "ALTA", 4: "MUY_ALTA"}


def test_un_modificador_tiene_condiciones_y_efecto_de_mas_o_menos_uno():
    condicion = Condicion("promesas_incumplidas_30d", Operador.MAYOR_O_IGUAL, 1)
    for efecto in (0, 2, -2):
        with pytest.raises(ValueError):
            Modificador(INC, (condicion,), efecto)
    with pytest.raises(ValueError):
        Modificador(INC, (), 1)
    # Un umbral que no es del tipo de su feature tampoco existe.
    with pytest.raises(ValueError):
        Condicion("promesa_vigente", Operador.IGUAL, 1)


# --- el hash de la decision -----------------------------------------------------------------------


def test_el_hash_de_la_decision_es_determinista_y_depende_de_lo_que_la_fija():
    valores = _features(**SENALES[REC])
    huella = feature_hash(valores)
    decision = decidir(valores)

    assert decision_hash(huella, decision) == decision_hash(huella, decidir(dict(valores)))
    otra_huella = feature_hash({**valores, "saldo_total": Decimal("10000.01")})
    assert decision_hash(otra_huella, decision) != decision_hash(huella, decision)
    otra = decidir(_features(**SENALES[INC]))
    assert decision_hash(huella, otra) != decision_hash(huella, decision)
    assert decision_hash(huella, decision, version_features="features-decision/v2") != (
        decision_hash(huella, decision)
    )
