from __future__ import annotations

from typer.testing import CliRunner

from motor_cartera.cli import app
from motor_cartera.ingesta.lectores import leer

cli = CliRunner()


def test_generar_escribe_una_cartera_legible(tmp_path):
    destino = tmp_path / "cartera.csv"

    resultado = cli.invoke(
        app, ["generar", "--n", "50", "--destino", str(destino), "--fecha-corte", "2026-09-30"]
    )

    assert resultado.exit_code == 0, resultado.output
    assert len(leer(destino).datos) == 50
