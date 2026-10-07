"""Escenarios longitudinales de punta a punta: el golden de seis cortes, el escenario del generador
contra su manifiesto, y la reconstruccion de la historia en otro orden."""

from __future__ import annotations

import random
from datetime import date
from decimal import Decimal

import pytest
from historia_escenarios import (
    GOLDEN_CORTES,
    GOLDEN_PRESENCIA,
    GOLDEN_RELLENO,
    cliente,
    cuenta,
    foto,
    golden,
    historia_de,
)
from sqlalchemy import func
from sqlmodel import select

from motor_cartera.db.modelos import (
    CorteCanonico,
    CuentaCanonica,
    DatasetConformado,
    EjecucionHistoria,
    PagoObservado,
    SnapshotCuenta,
)
from motor_cartera.db.sesion import sesion
from motor_cartera.generador.oficial import generar_escenario
from motor_cartera.historia import cuenta360
from motor_cartera.historia.cuenta360 import SinCuentaObservada
from motor_cartera.historia.ejecuciones import abrir_historia
from motor_cartera.historia.presencia import Presencia, TipoEvento
from motor_cartera.ingesta.corridas import ingerir_archivo
from motor_cartera.ingesta.pagos import ingerir_pagos

pytestmark = pytest.mark.usefixtures("bd")


def _cuantas(modelo, *condiciones) -> int:
    with sesion() as s:
        return s.exec(select(func.count()).select_from(modelo).where(*condiciones)).one()


def _numero(fecha: date) -> int:
    return GOLDEN_CORTES.index(fecha) + 1


# --- el golden de seis cortes ---------------------------------------------------------------------


def test_el_escenario_golden_de_seis_cortes(tmp_path, trabajar):
    escenario = golden(tmp_path)

    procesados = trabajar()

    # Once historias: seis cortes y cinco archivos de pagos, todas EXITOSA y a la primera.
    assert [(p.tipo, p.estado, p.intentos) for p in procesados] == [
        ("HISTORIA", "COMPLETADO", 1)
    ] * 11
    for corrida in escenario.corridas:
        assert historia_de(corrida=corrida).resultado == "CORTE_PUBLICADO"
    for ingesta in escenario.ingestas:
        assert historia_de(ingesta=ingesta).resultado == "PAGOS_PUBLICADOS"

    # Las cuentas que trae algun corte, y solo esas: el 7 solo tiene un pago.
    assert _cuantas(CuentaCanonica) == len(GOLDEN_PRESENCIA) + len(GOLDEN_RELLENO) == 27
    assert _cuantas(CorteCanonico) == 6
    por_corte = [
        len(GOLDEN_RELLENO) + sum(1 for c in GOLDEN_PRESENCIA.values() if n in c)
        for n in range(1, 7)
    ]
    assert por_corte == [26, 25, 26, 24, 26, 26]
    assert _cuantas(SnapshotCuenta) == sum(por_corte) == 153
    with sesion() as s:
        cuentas_por_corte = [
            c.cuentas
            for c in s.exec(select(CorteCanonico).order_by(CorteCanonico.fecha_corte)).all()
        ]
    assert cuentas_por_corte == por_corte
    assert _cuantas(PagoObservado) == 10

    with sesion() as s:
        vistas = {n: cuenta(n) for n in GOLDEN_PRESENCIA}
        eventos = {n: cuenta360.eventos(s, c) for n, c in vistas.items()}
        resumen = {n: cuenta360.resumen(s, c) for n, c in vistas.items()}
        pagos = {
            n: cuenta360.pagos_observados(s, c, desplazamiento=0, limite=50)
            for n, c in vistas.items()
        }
        _, historia_3 = cuenta360.historia(
            s, vistas[3], desplazamiento=0, limite=50, descendente=False
        )

    def tipos(numero: int) -> list[tuple[str, int, int]]:
        return [(e.tipo, _numero(e.fecha_corte), e.cortes_ausente) for e in eventos[numero]]

    primera = TipoEvento.PRIMERA_OBSERVACION
    salida = TipoEvento.SALIDA_OBSERVADA
    reingreso = TipoEvento.REINGRESO_OBSERVADO
    # Siempre presente.
    assert tipos(1) == [(primera, 1, 0)]
    # Sale en el 4 y no vuelve: fuera del ultimo corte, sin snapshot actual.
    assert tipos(2) == [(primera, 1, 0), (salida, 4, 0)]
    assert resumen[2].presencia.estado == Presencia.NO_OBSERVADA_EN_ULTIMO_CORTE
    assert resumen[2].snapshot_actual is None
    assert resumen[2].ultimo_snapshot_observado.snapshot.fecha_corte == GOLDEN_CORTES[2]
    assert (resumen[2].presencia.cortes_observados, resumen[2].presencia.cortes_ausentes) == (3, 3)
    # Sale en el 3 y reingresa en el 5, despues de faltar dos cortes.
    assert tipos(3) == [(primera, 1, 0), (salida, 3, 0), (reingreso, 5, 2)]
    # Aparece por primera vez en el 3: los cortes de antes no son ausencias.
    assert tipos(4) == [(primera, 3, 0)]
    assert resumen[4].presencia.primera_observacion == GOLDEN_CORTES[2]
    assert resumen[4].presencia.cortes_ausentes == 0
    # Sale y reingresa dos veces.
    assert tipos(8) == [
        (primera, 1, 0),
        (salida, 2, 0),
        (reingreso, 3, 1),
        (salida, 4, 0),
        (reingreso, 5, 1),
    ]
    ocho = resumen[8].presencia
    assert (ocho.salidas_observadas, ocho.reingresos_observados) == (2, 2)
    for numero in (1, 3, 4, 5, 6, 8):
        assert resumen[numero].presencia.estado == Presencia.EN_CARTERA, numero

    # La continuidad y los deltas de la cuenta 3: 1 -> 2 continuo; 2 -> 5 no (falto 3 y 4).
    assert [
        (
            _numero(h.visto.snapshot.fecha_corte),
            h.visto.snapshot.saldo_total,
            h.enlace.continuo_desde_anterior,
            h.enlace.cortes_ausentes_desde_anterior,
            h.delta_saldo_total,
            h.delta_dias_atraso,
        )
        for h in historia_3
    ] == [
        (1, Decimal("9030.00"), None, None, None, None),
        (2, Decimal("8837.00"), True, 0, Decimal("-193.00"), 7),
        (5, Decimal("8000.00"), False, 2, Decimal("-837.00"), -37),
        (6, Decimal("7907.00"), True, 0, Decimal("-93.00"), 7),
    ]

    # Los pagos: varios de una cuenta, dos identicos, uno que llego mientras la cuenta faltaba y
    # otro despues de que salio. Todos observados tal como llegaron.
    total_5, pagos_5 = pagos[5]
    assert total_5 == 3
    assert [p.pago.recuperacion_por_gestion for p in pagos_5] == [
        Decimal("700.00"),
        Decimal("600.00"),
        Decimal("500.00"),
    ]
    total_6, pagos_6 = pagos[6]
    assert total_6 == 2
    assert len({(p.pago.fecha_recepcion, p.pago.recuperacion_por_gestion) for p in pagos_6}) == 1
    assert len({p.pago.pago_observado_id for p in pagos_6}) == 2
    assert pagos[3][0] == 1 and pagos[2][0] == 1
    # El pago sin cuenta observada se conserva, y ningun pago creo una cuenta.
    with sesion() as s, pytest.raises(SinCuentaObservada):
        cuenta360.buscar(s, "DSP_001", "CARTERA_PRINCIPAL", cliente(7))
    assert _cuantas(PagoObservado, PagoObservado.cliente_unico == cliente(7)) == 1


# --- el escenario del generador, contra su manifiesto ---------------------------------------------


def test_la_historia_del_escenario_del_generador_cuadra_con_su_manifiesto(tmp_path, trabajar):
    escenario = generar_escenario(
        tmp_path,
        cuentas=300,
        cortes=5,
        primer_corte=date(2026, 9, 2),
        semilla=11,
        formato="csv",
        tasa_altas=0.04,
        tasa_retiros=0.03,
    )
    manifiesto = escenario.manifiesto
    for corte in manifiesto["cortes"]:
        fecha = date.fromisoformat(corte["fecha_corte"])
        ingerir_archivo(tmp_path / corte["archivo"], contrato="cartera/v2", fecha_corte=fecha)
    for periodo in manifiesto["periodos"]:
        ingerir_pagos(tmp_path / periodo["archivo"])

    trabajar()

    assert _cuantas(EjecucionHistoria, EjecucionHistoria.estado != "EXITOSA") == 0
    with sesion() as s:
        cortes = {c.fecha_corte: c for c in s.exec(select(CorteCanonico)).all()}
        cuentas = s.exec(select(CuentaCanonica)).all()
        eventos = [e for c in cuentas for e in cuenta360.eventos(s, c)]
    fechas = [date.fromisoformat(c["fecha_corte"]) for c in manifiesto["cortes"]]
    # Cada corte, con las cuentas que dice el manifiesto.
    assert [cortes[f].cuentas for f in fechas] == [c["cuentas"] for c in manifiesto["cortes"]]
    # Cada alta del manifiesto es una primera observacion en su corte; cada liquidada o retirada,
    # una salida observada en el corte siguiente. El generador nunca reusa un cliente: no hay
    # reingresos.
    for i, corte in enumerate(manifiesto["cortes"]):
        primeras = sum(
            1 for e in eventos if e.tipo == "PRIMERA_OBSERVACION" and e.fecha_corte == fechas[i]
        )
        salidas = sum(
            1 for e in eventos if e.tipo == "SALIDA_OBSERVADA" and e.fecha_corte == fechas[i]
        )
        if i == 0:
            assert (primeras, salidas) == (corte["cuentas"], 0)
        else:
            assert primeras == corte["altas"]
            assert salidas == corte["liquidadas"] + corte["retiradas"]
    assert not [e for e in eventos if e.tipo == "REINGRESO_OBSERVADO"]
    assert len(cuentas) == manifiesto["cortes"][0]["cuentas"] + sum(
        c["altas"] for c in manifiesto["cortes"][1:]
    )
    # Y un pago observado por cada movimiento de cada periodo, repetidos incluidos.
    assert _cuantas(PagoObservado) == sum(p["movimientos"] for p in manifiesto["periodos"])


# --- reconstruir la historia en otro orden --------------------------------------------------------


def test_reconstruir_la_historia_en_orden_aleatorio_da_exactamente_la_misma(tmp_path, trabajar):
    escenario = golden(tmp_path)
    trabajar()
    en_orden = foto()
    assert len(en_orden["snapshots"]) == 153 and len(en_orden["pagos"]) == 10

    # Se borra solo la capa historica y se vuelve a abrir la historia de cada dataset, en un orden
    # al azar (fijo, para que la prueba se repita igual): cortes y pagos mezclados.
    with sesion() as s:
        for tabla in (
            "trabajo_orquestacion WHERE tipo = 'HISTORIA'",
            "snapshot_cuenta",
            "pago_observado",
            "ejecucion_historia",
            "corte_canonico",
            "cuenta_canonica",
        ):
            s.execute(_borrar(tabla))
        s.commit()
        datasets = list(s.exec(select(DatasetConformado).order_by(DatasetConformado.id)).all())
    assert len(datasets) == len(escenario.corridas) + len(escenario.ingestas)
    orden = list(datasets)
    random.Random(31416).shuffle(orden)
    assert orden != datasets
    for dataset in orden:
        with sesion() as s:
            abrir_historia(s, s.get_one(DatasetConformado, dataset.id), max_intentos=5)
            s.commit()

    trabajar()

    # Las mismas cuentas, los mismos cortes, los mismos snapshots, los mismos pagos observados,
    # con los mismos identificadores publicos, y la misma Cuenta 360 de cada cuenta.
    assert foto() == en_orden


def _borrar(tabla: str):
    from sqlalchemy import text

    return text(f"DELETE FROM {tabla}")
