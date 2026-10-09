"""evaluacion-promesa/v1 sin base: cada estado, cada frontera de tiempo y su orden. Las fechas de
corte son explicitas: ninguna prueba depende del reloj."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from motor_cartera.evaluacion.reglas import (
    Corte,
    EstadoEvaluacion,
    Motivo,
    Movimiento,
    PromesaEvaluable,
    evaluar,
    fin_del_intervalo,
)

MX = timezone(timedelta(hours=-6))


def dia(numero: int, hora: int = 10, minuto: int = 0) -> datetime:
    """Un instante de septiembre de 2026 en la hora local de la fuente."""
    return datetime(2026, 9, 1, hora, minuto, tzinfo=MX) + timedelta(days=numero - 1)


def fin_del_dia(numero: int) -> datetime:
    return dia(numero + 1, 0)


PROMESA = PromesaEvaluable(
    promesa_id=UUID(int=1),
    monto_prometido=Decimal("1000.00"),
    creada_en=dia(5),
    fin_limite=fin_del_dia(15),
)
AL_16 = Corte(fin_as_of=fin_del_dia(16), horizonte=dia(20))


def pago(numero_dia: int, monto: str, *, hora: int = 10, minuto: int = 0, **otros) -> Movimiento:
    return Movimiento(
        movimiento_id=UUID(int=numero_dia * 100 + hora),
        tipo=otros.pop("tipo", "PAGO"),
        instante=dia(numero_dia, hora, minuto),
        monto=Decimal(monto),
        **otros,
    )


def _codigos(evaluacion) -> list[str]:
    return [m["codigo"] for m in evaluacion.motivos]


# --- la prueba de la mision: $1,000 al dia 15, evaluada explicitamente al dia 16 -----------------


@pytest.mark.parametrize(
    ("movimientos", "estado", "observado", "motivo"),
    [
        ([pago(10, "1000.00")], EstadoEvaluacion.CUMPLIDA, "1000.00", Motivo.MONTO_ALCANZADO),
        ([pago(12, "500.00")], EstadoEvaluacion.PARCIAL, "500.00", Motivo.MONTO_PARCIAL),
        ([], EstadoEvaluacion.INCUMPLIDA, "0.00", Motivo.SIN_RECUPERACION),
        (
            [pago(8, "400.00"), pago(14, "700.00")],
            EstadoEvaluacion.CUMPLIDA,
            "1100.00",
            Motivo.MONTO_ALCANZADO,
        ),
    ],
    ids=["mil", "quinientos", "cero", "en-dos-pagos"],
)
def test_al_dia_16_una_promesa_de_mil_al_dia_15(movimientos, estado, observado, motivo):
    evaluacion = evaluar(PROMESA, movimientos, AL_16)

    assert evaluacion.estado == estado
    assert evaluacion.monto_observado == Decimal(observado)
    assert evaluacion.movimientos_compatibles == len(movimientos)
    assert _codigos(evaluacion) == [motivo]


def test_cancelada_antes_de_la_fecha_de_corte_es_cancelada_aunque_se_haya_pagado():
    cancelada = replace(PROMESA, cancelada_en=dia(8))

    evaluacion = evaluar(cancelada, [pago(10, "1000.00")], AL_16)

    assert evaluacion.estado == EstadoEvaluacion.CANCELADA
    assert evaluacion.motivos[0] == {
        "codigo": Motivo.PROMESA_CANCELADA,
        "cancelada_en": dia(8).isoformat(),
    }
    # Lo observado se dice igual: cancelada no quiere decir que no hubo pago.
    assert evaluacion.monto_observado == Decimal("1000.00")


def test_una_promesa_de_una_gestion_anulada_es_cancelada():
    evaluacion = evaluar(replace(PROMESA, anulada=True), [], AL_16)

    assert (evaluacion.estado, _codigos(evaluacion)) == (
        EstadoEvaluacion.CANCELADA,
        [Motivo.GESTION_ANULADA],
    )


def test_una_cancelacion_posterior_a_la_fecha_de_corte_todavia_no_cuenta():
    cancelada_despues = replace(PROMESA, cancelada_en=dia(20))

    assert evaluar(cancelada_despues, [], AL_16).estado == EstadoEvaluacion.INCUMPLIDA


# --- antes de su fecha limite --------------------------------------------------------------------


def test_antes_de_su_fecha_limite_esta_pendiente_salvo_que_ya_se_cumplio():
    al_14 = Corte(fin_as_of=fin_del_dia(14), horizonte=dia(20))

    assert evaluar(PROMESA, [pago(12, "500.00")], al_14).estado == EstadoEvaluacion.PENDIENTE
    assert evaluar(PROMESA, [], al_14).estado == EstadoEvaluacion.PENDIENTE
    assert evaluar(PROMESA, [pago(10, "1000.00")], al_14).estado == EstadoEvaluacion.CUMPLIDA
    # El mismo dia de su fecha limite ya no esta pendiente: se observa hasta el final de ese dia.
    al_15 = Corte(fin_as_of=fin_del_dia(15), horizonte=dia(20))
    assert evaluar(PROMESA, [pago(12, "500.00")], al_15).estado == EstadoEvaluacion.PARCIAL


def test_un_pago_de_despues_de_la_fecha_de_corte_no_se_ve_aunque_este_en_la_base():
    al_11 = Corte(fin_as_of=fin_del_dia(11), horizonte=dia(20))

    evaluacion = evaluar(PROMESA, [pago(12, "1000.00")], al_11)

    assert evaluacion.estado == EstadoEvaluacion.PENDIENTE
    assert evaluacion.monto_observado == Decimal("0.00")


# --- que movimientos son compatibles --------------------------------------------------------------


@pytest.mark.parametrize(
    ("movimiento", "compatible"),
    [
        (pago(5, "1000.00"), True),  # en el instante en que se acordo
        (pago(5, "1000.00", hora=9, minuto=59), False),  # un minuto antes de acordarse
        (pago(3, "1000.00"), False),
        (pago(15, "1000.00", hora=23, minuto=59), True),  # el ultimo minuto de su fecha limite
        (pago(16, "1000.00", hora=0), False),  # el primer minuto despues
        (pago(10, "1000.00", anulado_en=dia(12)), False),  # su reverso llego antes de la fecha
        (pago(10, "1000.00", anulado_en=dia(25)), True),  # su reverso llego despues
        (pago(10, "1000.00", tipo="POSIBLE_REVERSO"), False),
    ],
    ids=[
        "al-acordarse",
        "antes-de-acordarse",
        "dias-antes",
        "ultimo-minuto",
        "despues-del-limite",
        "anulado-antes",
        "anulado-despues",
        "posible-reverso",
    ],
)
def test_solo_un_pago_del_intervalo_no_anulado_hasta_la_fecha_es_compatible(movimiento, compatible):
    evaluacion = evaluar(PROMESA, [movimiento], AL_16)

    assert evaluacion.movimientos_compatibles == int(compatible)
    assert evaluacion.estado == (
        EstadoEvaluacion.CUMPLIDA if compatible else EstadoEvaluacion.INCUMPLIDA
    )


def test_un_posible_reverso_no_se_resta_pero_se_dice():
    movimientos = [
        pago(10, "1000.00"),
        pago(11, "-1000.00", tipo="POSIBLE_REVERSO"),
    ]

    evaluacion = evaluar(PROMESA, movimientos, AL_16)

    assert evaluacion.estado == EstadoEvaluacion.CUMPLIDA
    assert evaluacion.motivos[-1] == {
        "codigo": Motivo.POSIBLES_REVERSOS,
        "cuantos": 1,
        "monto": "-1000.00",
    }


# --- no evaluable ---------------------------------------------------------------------------------


def test_sin_datos_de_pagos_hasta_el_final_de_su_intervalo_no_se_afirma_un_incumplimiento():
    corto = Corte(fin_as_of=fin_del_dia(16), horizonte=dia(15, 20))
    sin_pagos = Corte(fin_as_of=fin_del_dia(16), horizonte=None)

    for corte in (corto, sin_pagos):
        evaluacion = evaluar(PROMESA, [pago(12, "500.00")], corte)
        assert evaluacion.estado == EstadoEvaluacion.NO_EVALUABLE
        assert _codigos(evaluacion) == [Motivo.DATOS_DE_PAGOS_INSUFICIENTES]
    # Pero lo que ya alcanzo el monto se dice: mas datos no lo deshacen.
    assert evaluar(PROMESA, [pago(10, "1000.00")], corto).estado == EstadoEvaluacion.CUMPLIDA


def test_con_pagos_sin_interpretar_en_su_intervalo_no_se_evalua():
    evaluacion = evaluar(PROMESA, [], AL_16, pagos_sin_interpretar=True)

    assert (evaluacion.estado, _codigos(evaluacion)) == (
        EstadoEvaluacion.NO_EVALUABLE,
        [Motivo.PAGOS_SIN_INTERPRETAR],
    )


def test_el_intervalo_termina_en_la_fecha_limite_o_en_la_de_corte_si_es_antes():
    assert fin_del_intervalo(PROMESA, AL_16) == fin_del_dia(15)
    assert fin_del_intervalo(PROMESA, Corte(fin_del_dia(9), dia(20))) == fin_del_dia(9)


def test_la_misma_entrada_da_la_misma_evaluacion():
    movimientos = [pago(12, "500.00"), pago(13, "100.00")]

    assert evaluar(PROMESA, movimientos, AL_16) == evaluar(PROMESA, list(movimientos), AL_16)
    assert evaluar(PROMESA, movimientos, AL_16) == evaluar(PROMESA, movimientos[::-1], AL_16)
