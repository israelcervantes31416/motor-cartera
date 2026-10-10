"""El backfill de la atribucion: que ventanas no tienen una atribucion al dia, por que, y encolarlas
sin duplicar nada. Una gestion tardia, una anulacion o una interpretacion nueva de los pagos
desactualizan una ventana; una gestion fuera de su rango no."""

from __future__ import annotations

import pytest
from atribucion_escenarios import atribuir_ventana, escenario
from historia_escenarios import cuenta, ingerir_pagos_de, pago
from lifecycle_escenarios import anular_gestion, gestion_de
from motor_pagos_escenarios import historiar_todo, interpretar_todo
from sqlalchemy import func
from sqlmodel import select
from typer.testing import CliRunner

from motor_cartera.atribucion import ejecuciones as motor
from motor_cartera.atribucion.backfill import diagnosticar
from motor_cartera.cli import app
from motor_cartera.db.modelos import EjecucionAtribucion, TipoTrabajo, TrabajoOrquestacion
from motor_cartera.db.sesion import sesion

pytestmark = pytest.mark.usefixtures("bd")

cli = CliRunner()


def _diagnostico():
    with sesion() as s:
        return diagnosticar(s)


def _atribuciones() -> list[EjecucionAtribucion]:
    with sesion() as s:
        return list(s.exec(select(EjecucionAtribucion).order_by(EjecucionAtribucion.id)).all())


def test_el_backfill_dice_que_falta_y_con_dry_run_no_encola_nada(tmp_path):
    escenario(tmp_path)

    resultado = cli.invoke(app, ["backfill-atribucion", "--dry-run"])

    assert resultado.exit_code == 0, resultado.output
    assert "Ventanas con pagos interpretados: 1, con 8 pagos" in resultado.output
    assert "sin atribucion: 1" in resultado.output
    assert "Pagos sin una atribucion al dia: 8" in resultado.output
    assert "Faltan 1 ventanas. Con --dry-run no se encolo nada; se encolarian:" in resultado.output
    assert "DSP_001/CARTERA_PRINCIPAL 2026-09: 8 pagos" in resultado.output
    assert _atribuciones() == []


def test_el_backfill_encola_una_por_ventana_y_es_idempotente(tmp_path, trabajar):
    escenario(tmp_path)

    primero = cli.invoke(app, ["backfill-atribucion"])
    segundo = cli.invoke(app, ["backfill-atribucion"])

    assert primero.exit_code == segundo.exit_code == 0, primero.output
    assert "Se encolaron 1 trabajos ATRIBUCION" in primero.output
    assert "ventana de 30 dias" in primero.output
    assert "en la cola (EN_PROCESO): 1" in segundo.output
    assert "Se encolaron 0 trabajos ATRIBUCION" in segundo.output
    with sesion() as s:
        trabajos = s.exec(
            select(func.count())
            .select_from(TrabajoOrquestacion)
            .where(TrabajoOrquestacion.tipo == TipoTrabajo.ATRIBUCION)
        ).one()
    assert trabajos == 1

    trabajar()

    tercero = cli.invoke(app, ["backfill-atribucion"])
    assert "al dia con atribucion/v1: 1" in tercero.output
    assert "Pagos sin una atribucion al dia: 0" in tercero.output
    assert "Se encolaron 0 trabajos ATRIBUCION" in tercero.output
    assert [(e.estado, e.movimientos_evaluados) for e in _atribuciones()] == [("EXITOSA", 8)]


def test_una_gestion_tardia_desactualiza_su_ventana_y_una_fuera_de_su_rango_no(tmp_path):
    escenario(tmp_path)
    atribuir_ventana(ventana_dias=20)
    gestion_de(cuenta(5).cuenta_id, "2026-07-01T10:00:00-06:00")  # antes de cualquier ventana
    assert _diagnostico().al_dia == 1

    gestion_de(cuenta(5).cuenta_id, "2026-09-08T10:00:00-06:00")  # tardia, antes de su pago
    diagnostico = _diagnostico()

    (desactualizada,) = diagnostico.desactualizadas
    assert desactualizada.motivo == (
        "7 gestiones en su rango (1 anuladas); su atribucion vigente leyo 6 (1 anuladas)"
    )
    assert diagnostico.pagos_sin_atribucion_al_dia == 8
    # La conserva su ventana hacia atras, aunque se pida otra para las nuevas.
    resultado = cli.invoke(app, ["backfill-atribucion", "--ventana-dias", "45"])
    assert "desactualizadas: 1" in resultado.output
    assert "ventana de 20 dias" in resultado.output
    _, nueva = _atribuciones()
    motor.atribuir(nueva.id)
    assert _diagnostico().al_dia == 1


def test_una_anulacion_desactualiza_su_ventana_aunque_no_cambie_cuantas_gestiones_hay(tmp_path):
    g = escenario(tmp_path)
    atribuir_ventana()

    anular_gestion(g["1"], "2026-09-12T09:00:00-06:00")

    (desactualizada,) = _diagnostico().desactualizadas
    assert desactualizada.motivo == (
        "6 gestiones en su rango (2 anuladas); su atribucion vigente leyo 6 (1 anuladas)"
    )


def test_una_interpretacion_nueva_de_los_pagos_desactualiza_su_ventana(tmp_path):
    escenario(tmp_path)
    atribuir_ventana()
    ingerir_pagos_de(tmp_path, "tarde.csv", [pago(5, "2026-09-15 10:00:00", "10.00")])
    historiar_todo()
    interpretar_todo()

    diagnostico = _diagnostico()

    (desactualizada,) = diagnostico.desactualizadas
    assert desactualizada.motivo.startswith("otra interpretacion de pagos (")
    assert desactualizada.pagos == 9
    assert diagnostico.pagos == 9


def test_una_ventana_que_solo_fallo_se_reintenta_solo_si_se_pide(tmp_path, monkeypatch):
    escenario(tmp_path)

    def falla(*_args, **_kwargs):
        raise RuntimeError("inyectada")

    monkeypatch.setattr(motor, "_contar", falla)
    atribuir_ventana()
    monkeypatch.undo()

    sin_bandera = cli.invoke(app, ["backfill-atribucion"])
    con_bandera = cli.invoke(app, ["backfill-atribucion", "--reintentar-fallidas"])

    assert "solo con atribuciones FALLIDA: 1; se reintentan con --reintentar-fallidas" in (
        sin_bandera.output
    )
    assert "ERROR_INTERNO" in sin_bandera.output
    assert "Se encolaron 0 trabajos ATRIBUCION" in sin_bandera.output
    assert "Pagos sin una atribucion al dia: 8" in sin_bandera.output
    assert "Se encolaron 1 trabajos ATRIBUCION" in con_bandera.output
    assert [e.estado for e in _atribuciones()] == ["FALLIDA", "EN_PROCESO"]


def test_una_ventana_sin_atribucion_usa_la_ventana_que_se_pide(tmp_path):
    escenario(tmp_path)

    resultado = cli.invoke(app, ["backfill-atribucion", "--ventana-dias", "12"])
    fuera_de_rango = cli.invoke(app, ["backfill-atribucion", "--ventana-dias", "0"])

    assert "ventana de 12 dias" in resultado.output
    assert [e.ventana_dias for e in _atribuciones()] == [12]
    assert fuera_de_rango.exit_code != 0
