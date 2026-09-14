from __future__ import annotations

import pandas as pd
import pytest


@pytest.fixture
def cartera_valida() -> pd.DataFrame:
    """Tres filas que cumplen el contrato. Punto de partida de las pruebas."""
    return pd.DataFrame(
        {
            "cliente_unico": ["CU00000001", "CU00000002", "CU00000003"],
            "saldo_total": [1500.50, 23000.00, 780.25],
            "dias_atraso": [0, 45, 190],
            "producto": ["CONSUMO", "TARJETA", "NOMINA"],
            "canal": ["TELEFONICA", "CAMPO", "DIGITAL"],
            "cve_entidad": ["21", "21", "09"],
            "cve_municipio": ["114", "156", "005"],
            "fecha_corte": pd.to_datetime(["2026-01-31"] * 3),
        }
    )
