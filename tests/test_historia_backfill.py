"""El backfill historico: encolar la historia de los datasets que no la tienen, como los de v0.6,
sin duplicar nada y en orden de corte."""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from historia_escenarios import Cuenta, historia_de, ingerir_corte, ingerir_pagos_de, pago
from sqlalchemy import func, text
from sqlmodel import select
from typer.testing import CliRunner

from motor_cartera.cli import app
from motor_cartera.config import Config
from motor_cartera.db.modelos import (
    Corrida,
    CorteCanonico,
    DatasetConformado,
    EjecucionHistoria,
    SnapshotCuenta,
    TipoTrabajo,
    TrabajoOrquestacion,
)
from motor_cartera.db.sesion import sesion
from motor_cartera.historia.backfill import diagnosticar, encolar

pytestmark = pytest.mark.usefixtures("bd")

CORTES = [date(2026, 9, 2) + timedelta(days=7 * i) for i in range(3)]
cli = CliRunner()


def _sin_historia() -> None:
    """La base como la deja v0.6 despues de subir a la 0008: datasets conformados, ninguna
    historia (ni interpretacion de pagos, que la cita)."""
    with sesion() as s:
        for tabla in (
            "trabajo_orquestacion WHERE tipo = 'MOTOR_PAGOS'",
            "resultado_pago_observado",
            "movimiento_economico_canonico",
            "ejecucion_motor_pagos",
            "trabajo_orquestacion WHERE tipo = 'HISTORIA'",
            "snapshot_cuenta",
            "pago_observado",
            "ejecucion_historia",
            "corte_canonico",
            "cuenta_canonica",
        ):
            s.execute(text(f"DELETE FROM {tabla}"))
        s.commit()


def _publicar_como_v06(tmp_path) -> list[Corrida]:
    # Los cortes llegan fuera de orden: el 3, el 1 y el 2. Y unos pagos.
    corridas = [
        ingerir_corte(tmp_path, CORTES[i], [Cuenta(n, dias=i) for n in range(1, 21)])
        for i in (2, 0, 1)
    ]
    ingerir_pagos_de(tmp_path, "pagos.csv", [pago(1, "2026-09-03 10:00:00", "500.00")])
    _sin_historia()
    return corridas


def _trabajos_historia() -> list[TrabajoOrquestacion]:
    with sesion() as s:
        return list(
            s.exec(
                select(TrabajoOrquestacion)
                .where(TrabajoOrquestacion.tipo == TipoTrabajo.HISTORIA)
                .order_by(TrabajoOrquestacion.id)
            ).all()
        )


def _fecha_de(trabajo: TrabajoOrquestacion) -> date | None:
    with sesion() as s:
        return s.exec(
            select(Corrida.fecha_corte)
            .join(DatasetConformado, DatasetConformado.corrida_id == Corrida.id)
            .join(
                EjecucionHistoria, EjecucionHistoria.dataset_conformado_id == DatasetConformado.id
            )
            .where(EjecucionHistoria.id == trabajo.ejecucion_historia_id)
        ).first()


def test_el_backfill_dice_cuanto_falta_y_con_dry_run_no_encola_nada(tmp_path):
    _publicar_como_v06(tmp_path)

    resultado = cli.invoke(app, ["backfill-historia", "--dry-run"])

    assert resultado.exit_code == 0, resultado.output
    assert "Datasets conformados de cartera/v2 y pagos/v1: 4" in resultado.output
    assert "sin historia: 4" in resultado.output
    assert "Faltan 4. Con --dry-run no se encolo nada" in resultado.output
    # En orden de corte, y despues los pagos.
    lineas = [linea.strip() for linea in resultado.output.splitlines() if "cartera/v2 del" in linea]
    assert [re.search(r"\d{4}-\d{2}-\d{2}", linea).group() for linea in lineas] == [
        c.isoformat() for c in CORTES
    ]
    assert _trabajos_historia() == []


def test_el_backfill_encola_en_orden_de_corte_y_es_idempotente(tmp_path, trabajar):
    _publicar_como_v06(tmp_path)

    primero = cli.invoke(app, ["backfill-historia"])
    segundo = cli.invoke(app, ["backfill-historia"])

    assert primero.exit_code == segundo.exit_code == 0
    assert "Se encolaron 4 trabajos HISTORIA" in primero.output
    # La segunda vez no hay nada que encolar: los cuatro ya estan en la cola.
    assert "en la cola (EN_PROCESO): 4" in segundo.output
    assert "Se encolaron 0 trabajos HISTORIA" in segundo.output
    trabajos = _trabajos_historia()
    assert len(trabajos) == 4
    assert [_fecha_de(t) for t in trabajos] == [*CORTES, None]

    trabajar()

    with sesion() as s:
        diagnostico = diagnosticar(s)
        assert s.exec(select(func.count()).select_from(CorteCanonico)).one() == 3
        assert s.exec(select(func.count()).select_from(SnapshotCuenta)).one() == 60
    assert (diagnostico.materializados, diagnostico.en_cola) == (4, 0)
    assert diagnostico.sin_historia == diagnostico.solo_fallidas == []
    tercero = cli.invoke(app, ["backfill-historia"])
    assert "con historia/v1 EXITOSA: 4" in tercero.output
    assert "Se encolaron 0 trabajos HISTORIA" in tercero.output


def test_una_historia_fallida_se_reintenta_solo_si_se_pide(tmp_path, trabajar):
    # Un conflicto: dos carteras distintas del mismo corte. La segunda falla al materializarse.
    ingerir_corte(tmp_path, CORTES[0], [Cuenta(1)])
    conflictiva = ingerir_corte(tmp_path, CORTES[0], [Cuenta(2)], nombre="otra")
    trabajar()
    assert historia_de(corrida=conflictiva).resultado == "CORTE_CANONICO_CONFLICTIVO"

    sin_bandera = cli.invoke(app, ["backfill-historia"])

    assert "solo con ejecuciones FALLIDA: 1; se reintentan con --reintentar-fallidas" in (
        sin_bandera.output
    )
    assert "CORTE_CANONICO_CONFLICTIVO" in sin_bandera.output
    assert "Se encolaron 0 trabajos HISTORIA" in sin_bandera.output

    con_bandera = cli.invoke(app, ["backfill-historia", "--reintentar-fallidas"])

    assert "Se encolaron 1 trabajos HISTORIA" in con_bandera.output
    trabajar()
    # Vuelve a fallar igual, y el corte publicado sigue intacto: el conflicto no se resuelve solo.
    assert historia_de(corrida=conflictiva).resultado == "CORTE_CANONICO_CONFLICTIVO"
    with sesion() as s:
        assert s.exec(select(func.count()).select_from(EjecucionHistoria)).one() == 3
        assert s.exec(select(func.count()).select_from(SnapshotCuenta)).one() == 1


def test_encolar_un_dataset_que_otro_ya_encolo_no_lo_duplica(tmp_path):
    _publicar_como_v06(tmp_path)
    with sesion() as s:
        pendientes = diagnosticar(s).sin_historia

    encolados = encolar(pendientes, config=Config())
    otra_vez = encolar(pendientes, config=Config())

    assert all(e.historia_run_id is not None for e in encolados)
    assert all(e.historia_run_id is None for e in otra_vez)
    assert all("ya se esta materializando" in e.nota for e in otra_vez)
    assert len(_trabajos_historia()) == 4
