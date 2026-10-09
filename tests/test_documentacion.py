"""Los diccionarios de datos dicen exactamente lo que dicen los contratos: si un contrato cambia una
columna, su diccionario no se queda atrás sin que una prueba lo note."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from motor_cartera.contratos.cartera_v2 import COLUMNAS_CARRIER, CONTRATO_V2, TELEFONOS_ANCHOS
from motor_cartera.contratos.pagos import CONTRATO_PAGOS
from motor_cartera.motor_pagos.reglas import Clasificacion, Motivo

DOCS = Path(__file__).resolve().parents[1] / "docs"
FILA = re.compile(r"^\| (\d+) \| `([^`]+)` \| ([^|]+) \| ([^|]+) \|")


def _filas(archivo: str) -> list[tuple[int, str, str, str]]:
    filas = []
    for linea in (DOCS / archivo).read_text(encoding="utf-8").splitlines():
        if coincidencia := FILA.match(linea):
            numero, nombre, tipo, requerida = coincidencia.groups()
            filas.append((int(numero), nombre, tipo.strip(), requerida.strip()))
    return filas


@pytest.mark.parametrize(
    ("archivo", "contrato"),
    [("diccionario_cartera.md", CONTRATO_V2), ("diccionario_pagos.md", CONTRATO_PAGOS)],
    ids=["cartera-v2", "pagos-v1"],
)
def test_cada_diccionario_lista_las_columnas_de_su_contrato_en_su_orden(archivo, contrato):
    filas = _filas(archivo)

    assert [numero for numero, *_ in filas] == list(range(1, len(contrato.columnas) + 1))
    assert tuple(nombre for _, nombre, *_ in filas) == contrato.nombres
    requeridas = {nombre for _, nombre, _, requerida in filas if requerida == "sí"}
    assert requeridas == {c.nombre for c in contrato.columnas if c.requerida}


def test_el_documento_de_carrier_dice_que_columnas_reemplaza():
    texto = (DOCS / "carrier.md").read_text(encoding="utf-8")

    assert (
        f"**{len(COLUMNAS_CARRIER)}\ncolumnas**" in texto
        or f"{len(COLUMNAS_CARRIER)} columnas" in texto
    )
    assert len(COLUMNAS_CARRIER) == 85
    for columna in ("TEL_AVAL", "TELEFONO1", "TIPOTEL1", "TELEFONO"):
        assert f"`{columna}`" in texto
    assert len(TELEFONOS_ANCHOS) == 9


def test_el_documento_del_motor_de_pagos_define_cada_clasificacion_y_cada_motivo():
    """Cada clase y cada motivo que el motor publica tiene su fila en las tablas de
    motor_pagos.md: uno nuevo no llega a la API sin que se documente que quiere decir."""
    texto = (DOCS / "motor_pagos.md").read_text(encoding="utf-8")
    filas = {
        linea.split(" | ")[0].strip("| `")
        for linea in texto.splitlines()
        if linea.startswith("| `")
    }

    assert {c.value for c in Clasificacion} <= filas
    assert {m.value for m in Motivo} <= filas
