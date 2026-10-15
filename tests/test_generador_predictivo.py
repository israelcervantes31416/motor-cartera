"""El escenario sintetico de predictive/model-v1 (escenario-predictivo/v1), sin base: determinista
con su semilla, con sus reglas y su cobertura declaradas en su manifiesto, y con senal (las tasas
dependen de los rasgos latentes que ningun archivo trae)."""

from __future__ import annotations

import json
from datetime import date

import numpy as np
import pytest

from motor_cartera.generador.predictivo import (
    MANIFIESTO,
    REGLAS,
    SEMILLA,
    VERSION_ESCENARIO,
    generar_escenario_predictivo,
    latentes,
)

HOY = date(2026, 10, 15)


def _generar(destino, semilla: int = SEMILLA):
    return generar_escenario_predictivo(
        destino, cuentas=80, cortes=4, primer_corte=date(2026, 6, 3), semilla=semilla, hoy=HOY
    )


def _archivos(destino) -> dict[str, bytes]:
    return {r.name: r.read_bytes() for r in sorted(destino.iterdir()) if r.is_file()}


def test_el_mismo_escenario_sale_igual_byte_por_byte(tmp_path):
    uno, otro = tmp_path / "uno", tmp_path / "otro"
    _generar(uno)
    _generar(otro)
    assert _archivos(uno) == _archivos(otro)
    distinto = tmp_path / "distinto"
    _generar(distinto, semilla=7)
    assert _archivos(distinto)[MANIFIESTO] != _archivos(uno)[MANIFIESTO]


def test_el_manifiesto_declara_sus_reglas_su_cobertura_y_sus_archivos(tmp_path):
    escenario = _generar(tmp_path)
    m = json.loads((tmp_path / MANIFIESTO).read_text(encoding="utf-8"))
    assert m == escenario.manifiesto
    assert (m["version"], m["semilla"]) == (VERSION_ESCENARIO, SEMILLA)
    assert m["reglas"] == REGLAS.definicion()
    # La cobertura es la que el generador escribio, nunca la fecha maxima observada.
    assert m["cobertura"] == {
        "pagos_completos_desde": "2026-06-04",
        "pagos_completos_hasta": "2026-06-24",
        "lifecycle_completo_desde": "2026-06-04",
        "lifecycle_completo_hasta": "2026-06-24",
    }
    assert [c["fecha_corte"] for c in m["cortes"]] == [
        "2026-06-03",
        "2026-06-10",
        "2026-06-17",
        "2026-06-24",
    ]
    assert len(m["periodos"]) == 3
    import hashlib

    for registro in [*m["cortes"], *m["periodos"]]:
        ruta = tmp_path / registro["archivo"]
        assert hashlib.sha256(ruta.read_bytes()).hexdigest() == registro["sha256"]
    # Los labels no se escriben: los construye el pipeline.
    nombres = {r.name for r in tmp_path.iterdir()}
    assert not any("label" in n or "target" in n for n in nombres)


def test_un_escenario_no_termina_en_el_futuro(tmp_path):
    with pytest.raises(ValueError):
        generar_escenario_predictivo(
            tmp_path, cuentas=10, cortes=3, primer_corte=date(2026, 10, 7), hoy=HOY
        )
    with pytest.raises(ValueError):
        generar_escenario_predictivo(
            tmp_path, cuentas=10, cortes=1, primer_corte=date(2026, 6, 3), hoy=HOY
        )


def test_los_rasgos_latentes_son_fijos_por_cliente_y_semilla():
    clientes = np.arange(1, 5001)
    voluntad, contactabilidad = latentes(clientes, SEMILLA)
    v2, c2 = latentes(clientes[::-1], SEMILLA)
    assert np.array_equal(voluntad, v2[::-1]) and np.array_equal(contactabilidad, c2[::-1])
    # Normales estandar, independientes entre si.
    assert abs(voluntad.mean()) < 0.05 and abs(voluntad.std() - 1) < 0.05
    assert abs(np.corrcoef(voluntad, contactabilidad)[0, 1]) < 0.05
    assert not np.array_equal(latentes(clientes, 7)[0], voluntad)


def test_las_tasas_del_escenario_tienen_senal(tmp_path):
    """Con las reglas publicas, una fraccion de las cuentas paga cada semana y otra se gestiona: las
    tasas que el generador cuenta caen en la escala declarada en backtesting_predictivo.md."""
    escenario = generar_escenario_predictivo(
        tmp_path, cuentas=1500, cortes=3, primer_corte=date(2026, 6, 3), hoy=HOY
    )
    tasas = escenario.manifiesto["tasas_semanales"]
    assert 0.05 < tasas["pagadoras"] < 0.25
    assert 0.3 < tasas["gestionadas"] < 0.8
    assert 0 < tasas["con_promesa"] < tasas["contactadas"] < tasas["gestionadas"]
