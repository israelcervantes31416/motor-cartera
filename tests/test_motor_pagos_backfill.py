"""El backfill del motor de pagos: encolar la interpretacion de los pagos observados que no la
tienen, como los de v0.7, por ventanas, sin duplicar nada."""

from __future__ import annotations

from datetime import date

import pytest
from historia_escenarios import Cuenta, ingerir_corte, ingerir_pagos_de, pago
from motor_pagos_escenarios import (
    cuantos,
    ejecuciones,
    historiar_todo,
    interpretar_todo,
    publicar,
    vigente,
)
from sqlalchemy import text
from typer.testing import CliRunner

from motor_cartera.cli import app
from motor_cartera.db.modelos import (
    EjecucionMotorPagos,
    MovimientoEconomicoCanonico,
    ResultadoPagoObservado,
    TipoTrabajo,
    TrabajoOrquestacion,
)
from motor_cartera.db.sesion import sesion
from motor_cartera.motor_pagos.backfill import diagnosticar

pytestmark = pytest.mark.usefixtures("bd")

cli = CliRunner()
AGOSTO, SEPTIEMBRE = date(2026, 8, 1), date(2026, 9, 1)


def _sin_motor() -> None:
    """La base como la deja v0.7 despues de subir a la 0009: pagos observados, ninguna
    interpretacion."""
    with sesion() as s:
        for tabla in (
            "trabajo_orquestacion WHERE tipo = 'MOTOR_PAGOS'",
            "resultado_pago_observado",
            "movimiento_economico_canonico",
            "ejecucion_motor_pagos",
        ):
            s.execute(text(f"DELETE FROM {tabla}"))
        s.commit()


def _publicar_como_v07(tmp_path) -> None:
    ingerir_pagos_de(
        tmp_path,
        "pagos.csv",
        [
            pago(1, "2026-08-20 10:00:00", "500.00"),
            pago(2, "2026-08-21 10:00:00", "300.00"),
            pago(1, "2026-09-03 10:00:00", "-500.00"),
        ],
    )
    historiar_todo()
    _sin_motor()


def test_el_backfill_dice_cuanto_falta_y_con_dry_run_no_encola_nada(tmp_path):
    _publicar_como_v07(tmp_path)

    resultado = cli.invoke(app, ["backfill-motor-pagos", "--dry-run"])

    assert resultado.exit_code == 0, resultado.output
    assert "Ventanas con pagos observados: 2, con 3 pagos observados" in resultado.output
    assert "sin interpretacion: 2" in resultado.output
    assert "Pagos observados sin interpretacion vigente: 3" in resultado.output
    assert "Faltan 2 ventanas. Con --dry-run no se encolo nada; se encolarian:" in resultado.output
    assert "DSP_001/CARTERA_PRINCIPAL 2026-08: 2 pagos observados, 2 sin interpretar" in (
        resultado.output
    )
    assert cuantos(EjecucionMotorPagos) == 0


def test_el_backfill_encola_por_ventana_y_es_idempotente(tmp_path, trabajar):
    _publicar_como_v07(tmp_path)

    primero = cli.invoke(app, ["backfill-motor-pagos"])
    segundo = cli.invoke(app, ["backfill-motor-pagos"])

    assert primero.exit_code == segundo.exit_code == 0
    assert "Se encolaron 2 trabajos MOTOR_PAGOS" in primero.output
    # La segunda vez no hay nada que encolar: las dos ventanas ya estan en la cola.
    assert "en la cola (EN_PROCESO): 2" in segundo.output
    assert "Se encolaron 0 trabajos MOTOR_PAGOS" in segundo.output
    assert cuantos(TrabajoOrquestacion, TrabajoOrquestacion.tipo == TipoTrabajo.MOTOR_PAGOS) == 2

    trabajar()

    assert cuantos(ResultadoPagoObservado) == 3
    # El reverso de septiembre encontro su pago de agosto.
    assert vigente(SEPTIEMBRE).reversos == vigente(AGOSTO).pagos_anulados == 1
    tercero = cli.invoke(app, ["backfill-motor-pagos"])
    assert "al dia con motor-pagos/v1: 2" in tercero.output
    assert "Pagos observados sin interpretacion vigente: 0" in tercero.output
    assert "Se encolaron 0 trabajos MOTOR_PAGOS" in tercero.output
    assert cuantos(MovimientoEconomicoCanonico) == 3


def test_una_ventana_con_pagos_que_su_vigente_no_ve_esta_desactualizada(tmp_path):
    publicar(ingerir_pagos_de(tmp_path, "uno.csv", [pago(1, "2026-09-03 10:00:00", "500.00")]))
    # Llega otro archivo, pero su interpretacion falla: la vigente sigue siendo la de antes.
    ingerir_pagos_de(tmp_path, "dos.csv", [pago(2, "2026-09-04 10:00:00", "300.00")])
    historiar_todo()
    _, nueva = ejecuciones(SEPTIEMBRE)
    with sesion() as s:
        s.execute(
            text(
                "UPDATE ejecucion_motor_pagos SET estado = 'FALLIDA', resultado = 'ERROR_INTERNO', "
                "terminada_en = now() WHERE id = :id"
            ),
            {"id": nueva.id},
        )
        s.commit()

    with sesion() as s:
        diagnostico = diagnosticar(s)
    (desactualizada,) = diagnostico.desactualizadas
    assert (desactualizada.observaciones, desactualizada.interpretadas) == (2, 1)
    assert diagnostico.observaciones_sin_interpretacion == 1

    resultado = cli.invoke(app, ["backfill-motor-pagos"])

    assert "desactualizadas (con pagos que su interpretacion vigente no ve): 1" in resultado.output
    assert "Se encolaron 1 trabajos MOTOR_PAGOS" in resultado.output
    interpretar_todo()
    assert vigente(SEPTIEMBRE).observaciones_leidas == 2


def test_una_ventana_que_solo_fallo_se_reintenta_solo_si_se_pide(tmp_path):
    _publicar_como_v07(tmp_path)
    cli.invoke(app, ["backfill-motor-pagos"])
    with sesion() as s:
        s.execute(
            text(
                "UPDATE ejecucion_motor_pagos SET estado = 'FALLIDA', resultado = 'ERROR_INTERNO', "
                "terminada_en = now()"
            )
        )
        s.commit()

    sin_bandera = cli.invoke(app, ["backfill-motor-pagos"])

    assert "solo con ejecuciones FALLIDA: 2; se reintentan con --reintentar-fallidas" in (
        sin_bandera.output
    )
    assert "2026-08: 2 pagos observados, 2 sin interpretar: ERROR_INTERNO" in sin_bandera.output
    assert "Se encolaron 0 trabajos MOTOR_PAGOS" in sin_bandera.output

    con_bandera = cli.invoke(app, ["backfill-motor-pagos", "--reintentar-fallidas"])

    assert "Se encolaron 2 trabajos MOTOR_PAGOS" in con_bandera.output
    assert {e.estado for e in interpretar_todo()} == {"EXITOSA"}


def test_reconciliar_reinterpreta_los_pagos_sin_cuenta_que_ya_la_tienen(tmp_path):
    publicar(ingerir_pagos_de(tmp_path, "pagos.csv", [pago(9, "2026-09-03 10:00:00", "320.00")]))
    ingerir_corte(tmp_path, date(2026, 9, 2), [Cuenta(9)])
    historiar_todo()

    sin_bandera = cli.invoke(app, ["backfill-motor-pagos", "--dry-run"])

    assert (
        "por conciliar (movimientos sin cuenta cuyo cliente ya tiene una): 1; se reinterpretan "
        "con --reconciliar" in sin_bandera.output
    )
    assert "Faltan 0 ventanas" in sin_bandera.output

    con_bandera = cli.invoke(app, ["backfill-motor-pagos", "--reconciliar"])

    assert "Se encolaron 1 trabajos MOTOR_PAGOS" in con_bandera.output
    assert "1 movimientos por conciliar" in con_bandera.output
    (conciliada,) = interpretar_todo()
    assert conciliada.sin_cuenta_observada == 0
    assert len(ejecuciones(SEPTIEMBRE)) == 2
