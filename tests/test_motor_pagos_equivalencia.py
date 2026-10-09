"""El motor en SQL dice exactamente lo mismo que el nucleo puro: sobre pagos al azar con copias,
coincidencias de la llave historica, ceros, negativos y pagos sin cuenta, que llegan en varios
archivos y se interpretan uno por uno, cada ventana vigente coincide observacion por observacion y
movimiento por movimiento con `reglas.interpretar` aplicado a todos los pagos de la cartera. Eso
prueba tambien que el contexto que lee el SQL no pierde nada que cambie una pareja de reverso."""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from historia_escenarios import Cuenta, ingerir_corte, ingerir_pagos_de, pago
from motor_pagos_escenarios import historiar_todo, interpretar_todo, resultados, vigente
from sqlmodel import select

from motor_cartera.db.modelos import (
    ArtefactoFuente,
    CuentaCanonica,
    DatasetConformado,
    EjecucionMotorPagos,
    EstadoMotorPagos,
    MovimientoEconomicoCanonico,
    PagoObservado,
)
from motor_cartera.db.sesion import sesion
from motor_cartera.motor_pagos import firmas
from motor_cartera.motor_pagos.reglas import Observacion, interpretar, periodo_de

pytestmark = pytest.mark.usefixtures("bd")

DESPACHO, CARTERA = "DSP_001", "CARTERA_PRINCIPAL"
INICIO = datetime(2026, 8, 10)


def _al_azar(semilla: int, cuantas: int) -> list[dict]:
    """Pagos de 25 clientes en pocos instantes y pocos importes: muchas coincidencias, a
    proposito."""
    rng = random.Random(semilla)
    filas = []
    for _ in range(cuantas):
        instante = INICIO + timedelta(days=rng.randrange(0, 80), hours=rng.choice((9, 12, 15)))
        if rng.random() < 0.05:
            instante += timedelta(microseconds=rng.choice((250_000, 500_000)))
        monto = Decimal(rng.choice(("100.00", "250.00", "400.00", "75.50")))
        sorteo = rng.random()
        if sorteo < 0.25:
            monto = -monto
        elif sorteo < 0.27:
            monto = Decimal("0.00")
        fila = pago(
            rng.randrange(1, 26),
            instante.isoformat(sep=" "),
            f"{monto:.2f}",
            Gestor=rng.choice(("GESTOR 001", "GESTOR 002")),
            Porcentaje_Comision=rng.choice(("0.05", "0.1", "0.00001")),
        )
        filas.append(fila)
        if rng.random() < 0.08:
            filas.append(dict(fila))
        if rng.random() < 0.04:
            filas.append({**fila, "Gestor": "GESTOR 003"})
    return filas


def _observaciones(periodo: date) -> list[Observacion]:
    """Todos los pagos observados de la cartera, como los ve el nucleo puro: propios los de la
    ventana."""
    with sesion() as s:
        cuentas = set(s.exec(select(CuentaCanonica.cliente_unico)).all())
        filas = s.exec(
            select(PagoObservado, ArtefactoFuente.sha256)
            .join(DatasetConformado, DatasetConformado.id == PagoObservado.dataset_conformado_id)
            .join(ArtefactoFuente, ArtefactoFuente.id == DatasetConformado.artefacto_original_id)
        ).all()
    observaciones = []
    for p, sha256 in filas:
        valores = {c.columna: getattr(p, c.columna) for c in firmas.CAMPOS_FIRMADOS}
        observaciones.append(
            Observacion(
                dataset_conformado_id=p.dataset_conformado_id,
                source_row=p.source_row,
                orden=(sha256, p.source_row),
                propia=periodo_de(p.fecha_recepcion) == periodo,
                con_cuenta=p.cliente_unico in cuentas,
                cliente_unico=p.cliente_unico,
                fecha_recepcion=p.fecha_recepcion,
                recuperacion=p.recuperacion_por_gestion,
                firma_exacta=firmas.firma_exacta(p.despacho_id, p.cartera_id, valores),
                firma_legacy=firmas.firma_legacy(
                    p.despacho_id,
                    p.cartera_id,
                    p.cliente_unico,
                    p.fecha_recepcion,
                    p.recuperacion_por_gestion,
                ),
                valores=(p.despacho_id, p.cartera_id, *valores.values()),
            )
        )
    return observaciones


@pytest.mark.parametrize("semilla", [3, 14])
def test_el_sql_y_el_nucleo_puro_interpretan_igual(tmp_path, semilla):
    ingerir_corte(tmp_path, date(2026, 8, 5), [Cuenta(n) for n in range(1, 21)])
    filas = _al_azar(semilla, 260)
    random.Random(semilla).shuffle(filas)
    # Cuatro archivos, cada uno con pagos de todas las ventanas, interpretados uno por uno.
    for numero in range(4):
        ingerir_pagos_de(tmp_path, f"pagos_{semilla}_{numero}.csv", filas[numero::4])
        historiar_todo()
        interpretar_todo()

    with sesion() as s:
        periodos = sorted(
            set(
                s.exec(
                    select(EjecucionMotorPagos.periodo_desde).where(
                        EjecucionMotorPagos.estado == EstadoMotorPagos.EXITOSA
                    )
                ).all()
            )
        )
    assert periodos == [date(2026, 8, 1), date(2026, 9, 1), date(2026, 10, 1)]
    clases_vistas = set()
    for periodo in periodos:
        ejecucion = vigente(periodo)
        esperados, esperados_mov = interpretar(_observaciones(periodo))
        vistos = resultados(ejecucion)

        por_llave = {
            (v.pago.dataset_conformado_id, v.pago.source_row): (
                v.clasificacion,
                v.resultado.estado_conciliacion,
                None if v.movimiento is None else v.movimiento.movimiento_id,
                v.resultado.movimiento_relacionado_id,
                tuple(v.codigos),
                v.resultado.firma_exacta,
                v.resultado.firma_legacy,
            )
            for v in vistos
        }
        assert por_llave == {
            (r.observacion.dataset_conformado_id, r.observacion.source_row): (
                r.clasificacion,
                r.estado_conciliacion,
                r.movimiento_id,
                r.movimiento_relacionado_id,
                tuple(r.motivos),
                r.observacion.firma_exacta,
                r.observacion.firma_legacy,
            )
            for r in esperados
        }, periodo
        with sesion() as s:
            publicados = s.exec(
                select(MovimientoEconomicoCanonico).where(
                    MovimientoEconomicoCanonico.ejecucion_motor_pagos_id == ejecucion.id
                )
            ).all()
        assert {
            m.movimiento_id: (
                m.tipo_movimiento,
                m.signo_economico,
                m.monto_reportado,
                m.observaciones,
                m.movimiento_original_id,
                m.anulado_por_movimiento_id,
                m.estado_conciliacion,
                m.fecha_recepcion,
                m.cliente_unico,
            )
            for m in publicados
        } == {
            m.movimiento_id: (
                m.tipo,
                m.signo,
                m.representante.recuperacion,
                m.observaciones,
                m.original_id,
                m.anulado_por_id,
                m.estado_conciliacion,
                m.representante.fecha_recepcion,
                m.representante.cliente_unico,
            )
            for m in esperados_mov
        }, periodo
        clases_vistas |= {v.clasificacion for v in vistos}
    # El azar alcanzo todas las clasificaciones: la prueba no compara solo casos faciles.
    assert clases_vistas == {
        "MOVIMIENTO_PRIMARIO",
        "DUPLICADO_EXACTO",
        "COINCIDENCIA_AMBIGUA",
        "REVERSO",
        "POSIBLE_REVERSO",
        "NO_CONCILIADO",
    }
