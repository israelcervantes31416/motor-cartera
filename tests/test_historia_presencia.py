"""La presencia de una cuenta en los cortes de su cartera, y sus identificadores publicos. Sin base:
el nucleo es puro, y los casos son los de la definicion de v0.7.0."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from motor_cartera.historia import identidad
from motor_cartera.historia.presencia import (
    Corte,
    Enlace,
    Evento,
    Presencia,
    TipoEvento,
    enlazar,
    eventos,
    posiciones,
    resumir,
)

PRIMERO = date(2026, 9, 2)
CORTES = [Corte(PRIMERO + timedelta(days=7 * i), uuid.uuid4()) for i in range(4)]
"""Cuatro cortes semanales de una cartera: 1, 2, 3 y 4."""


def _en(*numeros: int) -> set[date]:
    """Las fechas de los cortes con esos numeros, desde 1."""
    return {CORTES[n - 1].fecha_corte for n in numeros}


def _evento(tipo: TipoEvento, numero: int, ultima: int | None = None, ausente: int = 0) -> Evento:
    corte = CORTES[numero - 1]
    previa = None if ultima is None else CORTES[ultima - 1].fecha_corte
    return Evento(tipo, corte.fecha_corte, corte.corte_id, previa, ausente)


# --- los cuatro casos de la definicion -----------------------------------------------------------


def test_a_aparece_en_los_cuatro_cortes_solo_tiene_su_primera_observacion():
    assert eventos(CORTES, _en(1, 2, 3, 4)) == [_evento(TipoEvento.PRIMERA_OBSERVACION, 1)]
    vista = resumir(CORTES, _en(1, 2, 3, 4))
    assert (vista.estado, vista.cortes_observados, vista.cortes_ausentes) == (
        Presencia.EN_CARTERA,
        4,
        0,
    )


def test_b_aparece_en_1_y_2_y_sale_en_3():
    assert eventos(CORTES, _en(1, 2)) == [
        _evento(TipoEvento.PRIMERA_OBSERVACION, 1),
        _evento(TipoEvento.SALIDA_OBSERVADA, 3, ultima=2),
    ]
    vista = resumir(CORTES, _en(1, 2))
    assert vista.estado == Presencia.NO_OBSERVADA_EN_ULTIMO_CORTE
    assert (vista.ultima_observacion, vista.ultimo_corte) == (CORTES[1].fecha_corte, CORTES[3])
    # Falto en 3 y en 4, aunque haya salido una sola vez.
    assert (vista.cortes_ausentes, vista.salidas_observadas, vista.reingresos_observados) == (
        2,
        1,
        0,
    )


def test_c_aparece_en_1_2_y_4_sale_en_3_y_reingresa_en_4():
    assert eventos(CORTES, _en(1, 2, 4)) == [
        _evento(TipoEvento.PRIMERA_OBSERVACION, 1),
        _evento(TipoEvento.SALIDA_OBSERVADA, 3, ultima=2),
        _evento(TipoEvento.REINGRESO_OBSERVADO, 4, ultima=2, ausente=1),
    ]
    vista = resumir(CORTES, _en(1, 2, 4))
    assert vista.estado == Presencia.EN_CARTERA
    assert (vista.cortes_observados, vista.cortes_ausentes) == (3, 1)
    assert (vista.salidas_observadas, vista.reingresos_observados) == (1, 1)


def test_d_aparece_por_primera_vez_en_4_y_no_es_un_alta():
    (unico,) = eventos(CORTES, _en(4))
    assert unico == _evento(TipoEvento.PRIMERA_OBSERVACION, 4)
    # El vocabulario no dice que la cuenta nacio: dice que se observo por primera vez.
    assert {t.value for t in TipoEvento} == {
        "PRIMERA_OBSERVACION",
        "SALIDA_OBSERVADA",
        "REINGRESO_OBSERVADO",
    }
    vista = resumir(CORTES, _en(4))
    # Los cortes antes de su primera observacion no son ausencias.
    assert (vista.primera_observacion, vista.cortes_ausentes) == (CORTES[3].fecha_corte, 0)


def test_una_salida_larga_y_varios_reingresos():
    cortes = [Corte(PRIMERO + timedelta(days=7 * i), uuid.uuid4()) for i in range(8)]
    presente = {cortes[i].fecha_corte for i in (1, 4, 5, 7)}

    sucesos = eventos(cortes, presente)

    assert [(e.tipo, cortes.index(_corte(cortes, e)), e.cortes_ausente) for e in sucesos] == [
        (TipoEvento.PRIMERA_OBSERVACION, 1, 0),
        (TipoEvento.SALIDA_OBSERVADA, 2, 0),
        (TipoEvento.REINGRESO_OBSERVADO, 4, 2),
        (TipoEvento.SALIDA_OBSERVADA, 6, 0),
        (TipoEvento.REINGRESO_OBSERVADO, 7, 1),
    ]
    vista = resumir(cortes, presente)
    assert (vista.cortes_observados, vista.cortes_ausentes) == (4, 3)
    assert (vista.salidas_observadas, vista.reingresos_observados) == (2, 2)


def _corte(cortes: list[Corte], evento: Evento) -> Corte:
    (corte,) = [c for c in cortes if c.fecha_corte == evento.fecha_corte]
    return corte


# --- continuidad ----------------------------------------------------------------------------------


def test_dos_snapshots_son_continuos_solo_si_sus_cortes_son_consecutivos():
    lugares = posiciones(CORTES)
    fechas = [c.fecha_corte for c in CORTES]

    # La cuenta aparece en 1, 2 y 4: 1 -> 2 es continuo; 2 -> 4 no, falto el 3.
    assert enlazar(lugares, fechas[0], None) == Enlace(None, None)
    assert enlazar(lugares, fechas[1], fechas[0]) == Enlace(True, 0)
    assert enlazar(lugares, fechas[3], fechas[1]) == Enlace(False, 1)
    assert enlazar(lugares, fechas[3], fechas[0]) == Enlace(False, 2)


def test_un_snapshot_anterior_que_no_es_anterior_es_un_error():
    lugares = posiciones(CORTES)

    with pytest.raises(ValueError, match="no es anterior"):
        enlazar(lugares, CORTES[1].fecha_corte, CORTES[2].fecha_corte)


def test_los_cortes_van_en_orden_y_un_snapshot_es_de_un_corte_de_la_cartera():
    with pytest.raises(ValueError, match="orden de fecha"):
        posiciones([CORTES[1], CORTES[0]])
    with pytest.raises(ValueError, match="orden de fecha"):
        posiciones([CORTES[0], CORTES[0]])
    with pytest.raises(ValueError, match="no son cortes de la cartera"):
        eventos(CORTES, {date(2020, 1, 1)})


# --- como se veia en una fecha --------------------------------------------------------------------


def test_la_presencia_hasta_una_fecha_solo_ve_los_cortes_de_entonces():
    # C, que salio en 3 y reingreso en 4, vista con los cortes hasta el 3: estaba fuera.
    hasta_el_3 = CORTES[:3]
    vista = resumir(hasta_el_3, _en(1, 2))

    assert vista.estado == Presencia.NO_OBSERVADA_EN_ULTIMO_CORTE
    assert vista.ultimo_corte == CORTES[2]
    assert (vista.salidas_observadas, vista.reingresos_observados) == (1, 0)


def test_sin_cortes_no_hay_presencia():
    vista = resumir([], set())

    assert vista.ultimo_corte is None
    assert vista.estado == Presencia.NO_OBSERVADA_EN_ULTIMO_CORTE
    assert (vista.primera_observacion, vista.cortes_observados, vista.cortes_ausentes) == (
        None,
        0,
        0,
    )
    assert eventos([], set()) == []


# --- identificadores publicos deterministas -------------------------------------------------------


def test_los_identificadores_publicos_son_uuid5_de_su_llave_natural():
    dataset = uuid.UUID("5f1c2e3d-4b5a-4c6d-8e7f-9a0b1c2d3e4f")
    esperado = {
        identidad.cuenta_id("DSP_001", "CARTERA_PRINCIPAL", "CU0000000001"): (
            "cuenta:DSP_001:CARTERA_PRINCIPAL:CU0000000001"
        ),
        identidad.corte_id("DSP_001", "CARTERA_PRINCIPAL", date(2026, 9, 30)): (
            "corte:DSP_001:CARTERA_PRINCIPAL:2026-09-30"
        ),
        identidad.pago_observado_id(dataset, 17): f"pago:{dataset}:17",
    }
    for calculado, nombre in esperado.items():
        assert calculado == uuid.uuid5(identidad.ESPACIO, nombre)
        assert calculado.version == 5
    # La forma rapida, la que se usa por cada fila, da exactamente lo mismo.
    clientes = [f"CU{n:010d}" for n in range(1, 200)]
    rapidos = list(identidad.cuenta_ids("DSP_001", "CARTERA_PRINCIPAL", clientes))
    assert rapidos == [
        str(identidad.cuenta_id("DSP_001", "CARTERA_PRINCIPAL", c)) for c in clientes
    ]
    assert list(identidad.pago_observado_ids(dataset, [2, 3])) == [
        str(identidad.pago_observado_id(dataset, 2)),
        str(identidad.pago_observado_id(dataset, 3)),
    ]


def test_la_misma_llave_en_otra_cartera_es_otra_cuenta():
    una = identidad.cuenta_id("DSP_001", "CARTERA_PRINCIPAL", "CU0000000001")
    otra = identidad.cuenta_id("DSP_001", "OTRA_CARTERA", "CU0000000001")

    assert una != otra
    assert una == identidad.cuenta_id("DSP_001", "CARTERA_PRINCIPAL", "CU0000000001")
