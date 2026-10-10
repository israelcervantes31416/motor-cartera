"""decision/v1 contra decision/v2 en modo sombra, y los comandos decidir-v2 y comparar-decisiones:
la misma cartera decidida por las dos, cuanto coinciden y por que no, sin juzgar cual es mejor."""

from __future__ import annotations

from datetime import date

import pytest
from decision_v2_escenarios import C4, decidir_v2, reloj
from historia_escenarios import Cuenta, cuenta, ingerir_corte, ingerir_pagos_de, pago
from lifecycle_escenarios import gestion_de
from motor_pagos_escenarios import historiar_todo, interpretar_todo
from typer.testing import CliRunner

from motor_cartera.cli import app
from motor_cartera.db.sesion import sesion
from motor_cartera.decision.ejecuciones import decidir_corrida
from motor_cartera.decision_v2.sombra import comparar
from motor_cartera.lifecycle.reglas import NivelContacto, ResultadoGestion

pytestmark = pytest.mark.usefixtures("bd")

cli = CliRunner()


def _escenario(tmp_path):
    """Cinco cuentas en el corte del 16, decididas por decision/v1 (su corrida) y por decision/v2
    (su corte):

    - la 1, 15 dias: MEDIA en las dos;
    - la 2, 15 dias con saldo alto: v1 la sube por saldo (ALTA); public-v1 no tiene esa regla;
    - la 3, 45 dias con tres gestiones sin contacto: v2 la sube (MUY_ALTA);
    - la 4, 45 dias con un pago el 15: v2 la baja (MEDIA);
    - la 5, al corriente: BAJA en las dos."""
    corrida = ingerir_corte(
        tmp_path,
        C4,
        [
            Cuenta(1, dias=15),
            Cuenta(2, saldo=80_000, dias=15),
            Cuenta(3, dias=45),
            Cuenta(4, dias=45),
            Cuenta(5, dias=0),
        ],
    )
    historiar_todo()
    ingerir_pagos_de(tmp_path, "pagos.csv", [pago(4, "2026-09-15 10:00:00", "500.00")])
    historiar_todo()
    interpretar_todo()
    for dia in (12, 13, 14):
        gestion_de(
            cuenta(3).cuenta_id,
            f"2026-09-{dia:02d}T10:00:00-06:00",
            nivel_contacto=NivelContacto.SIN_CONTACTO,
            resultado=ResultadoGestion.SIN_RESPUESTA,
        )
    v1 = decidir_corrida(corrida.id)
    assert v1.estado == "EXITOSA"
    return v1


def test_la_sombra_cuenta_coincidencias_y_diferencias_por_su_fuente(tmp_path):
    v1 = _escenario(tmp_path)
    v2 = decidir_v2(known_at=reloj())

    with sesion() as s:
        c = comparar(s, v2)

    assert (c.decision_v2, c.decision_v1) == (v2.decision_run_id, v1.decision_run_id)
    assert (c.cuentas_v2, c.cuentas_v1, c.comparadas, c.solo_v2, c.solo_v1) == (5, 5, 5, 0, 0)
    assert (c.prioridad_igual, c.v2_mayor, c.v2_menor) == (2, 1, 2)
    assert (c.canal_igual, c.canal_distinto) == (2, 3)
    # Una sola diferencia viene del saldo de v1; las demas, de los modificadores de v2.
    assert (c.v1_con_saldo_alto, c.base_v2_igual_a_v1) == (1, 4)
    assert c.matriz["MEDIA"] == {"BAJA": 0, "MEDIA": 1, "ALTA": 0, "MUY_ALTA": 0}
    assert c.matriz["ALTA"] == {"BAJA": 0, "MEDIA": 2, "ALTA": 0, "MUY_ALTA": 1}
    assert c.matriz["BAJA"]["BAJA"] == 1
    assert c.modificadores == {
        "RECUPERACION_RECIENTE": 1,
        "PROMESA_VIGENTE": 0,
        "SATURACION_SIN_CONTACTO": 1,
        "PROMESA_INCUMPLIDA_RECIENTE": 0,
    }


def test_sin_decision_v1_la_sombra_lo_dice(tmp_path):
    ingerir_corte(tmp_path, C4, [Cuenta(1)])
    historiar_todo()
    v2 = decidir_v2(known_at=reloj())

    with sesion() as s:
        c = comparar(s, v2)

    assert (c.decision_v1, c.cuentas_v2, c.cuentas_v1, c.solo_v2, c.comparadas) == (
        None,
        1,
        0,
        1,
        0,
    )
    assert c.matriz == {}


def test_decidir_v2_y_comparar_decisiones_por_el_cli(tmp_path):
    _escenario(tmp_path)

    decidida = cli.invoke(app, ["decidir-v2", "--corte", "2026-09-16"])
    comparada = cli.invoke(app, ["comparar-decisiones", "--corte", "2026-09-16"])

    assert decidida.exit_code == 0, decidida.output
    assert "EXITOSA" in decidida.output
    assert "decision-policy/public-v1" in decidida.output
    assert "firma de entrada" in decidida.output
    assert "pagos disponibles a known_at" in decidida.output
    assert comparada.exit_code == 0, comparada.output
    assert "prioridad: igual 2, v2 mayor 1, v2 menor 2" in comparada.output
    assert "canal: igual 2, distinto 3" in comparada.output
    assert "no un juicio de calidad" in comparada.output


def test_el_cli_dice_por_que_no_decide(tmp_path):
    _escenario(tmp_path)

    sin_corte = cli.invoke(app, ["decidir-v2", "--corte", "2026-09-15"])
    backtest = cli.invoke(app, ["decidir-v2", "--corte", "2026-09-16", "--backtest"])
    sin_v2 = cli.invoke(app, ["comparar-decisiones", "--corte", "2026-09-16"])
    con_known_at = cli.invoke(
        app,
        [
            "decidir-v2",
            "--corte",
            "2026-09-16",
            "--backtest",
            "--known-at",
            reloj().isoformat(),  # al microsegundo: truncado podria quedar antes del corte
        ],
    )

    assert sin_corte.exit_code == 1 and "No hay un corte canonico" in sin_corte.output
    assert backtest.exit_code == 1 and "KNOWN_AT_REQUERIDO" in backtest.output
    assert sin_v2.exit_code == 1 and "no tiene una decision v2 EXITOSA" in sin_v2.output
    assert con_known_at.exit_code == 0, con_known_at.output
    assert "BACKTEST" in con_known_at.output
    assert date(2026, 9, 16).isoformat() in con_known_at.output
