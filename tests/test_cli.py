from __future__ import annotations

import pytest
from typer.testing import CliRunner

from motor_cartera.cli import app
from motor_cartera.ingesta.lectores import leer

cli = CliRunner()


def _generar(destino, *extra: str):
    argumentos = ["generar", "--n", "50", "--destino", str(destino), "--fecha-corte", "2026-09-30"]
    return cli.invoke(app, [*argumentos, *extra])


def test_generar_escribe_una_cartera_legible(tmp_path):
    destino = tmp_path / "cartera.csv"

    resultado = _generar(destino)

    assert resultado.exit_code == 0, resultado.output
    assert len(leer(destino).datos) == 50


@pytest.mark.usefixtures("bd")
def test_cargar_publica_y_dice_como_quedo(tmp_path):
    destino = tmp_path / "cartera.csv"
    _generar(destino, "--tasa-invalidas", "0")

    resultado = cli.invoke(app, ["cargar", str(destino)])

    assert resultado.exit_code == 0, resultado.output
    assert "EXITOSA" in resultado.output
    assert "leidas 50, validas 50, rechazadas 0" in resultado.output


@pytest.mark.usefixtures("bd")
def test_cargar_termina_con_error_si_no_publica(tmp_path):
    destino = tmp_path / "cartera.csv"
    destino.write_text("esto,no,es\nuna,cartera,valida\n", encoding="utf-8")

    resultado = cli.invoke(app, ["cargar", str(destino)])

    assert resultado.exit_code == 1
    assert "FALLIDA" in resultado.output


@pytest.mark.usefixtures("bd")
def test_cargar_dos_veces_el_mismo_archivo_se_niega(tmp_path):
    destino = tmp_path / "cartera.csv"
    _generar(destino, "--tasa-invalidas", "0")
    cli.invoke(app, ["cargar", str(destino)])

    resultado = cli.invoke(app, ["cargar", str(destino)])

    assert resultado.exit_code == 1
    assert "ya lo publico la corrida" in resultado.output
