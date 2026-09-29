from __future__ import annotations

import pytest

from motor_cartera.segmentacion import tramo_de_atraso


@pytest.mark.parametrize(
    ("dias", "tramo"),
    [(0, "0"), (1, "1-30"), (30, "1-30"), (31, "31-60"), (90, "61-90"), (91, "91+"), (3650, "91+")],
)
def test_tramo_de_atraso_en_sus_bordes(dias, tramo):
    assert tramo_de_atraso(dias) == tramo


def test_atraso_negativo_no_tiene_tramo():
    with pytest.raises(ValueError):
        tramo_de_atraso(-1)
