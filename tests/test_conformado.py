"""El dataset conformado en Parquet, sin base de datos: tipos del contrato, columnas tecnicas,
metadata y bytes reproducibles."""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from decimal import Decimal

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from motor_cartera.contratos.cartera_v2 import CONTRATO_V2
from motor_cartera.contratos.fuente import Columna, ContratoFuente, Tipo, juzgar_lote
from motor_cartera.fuentes.conformado import COLUMNA_FILA, COLUMNA_HOJA, EscritorConformado, leer
from motor_cartera.generador.oficial import generar_cartera_oficial

CONTRATO = ContratoFuente(
    "prueba/v1",
    (
        Columna("ID", Tipo.CODIGO, patron=r"[A-Z0-9]+"),
        Columna("N", Tipo.ENTERO),
        Columna("I", Tipo.IMPORTE),
        Columna("D", Tipo.DECIMAL),
        Columna("F", Tipo.FECHA),
        Columna("H", Tipo.FECHA_HORA),
        Columna("T", Tipo.TEXTO),
    ),
)


def _canonicos() -> pd.DataFrame:
    datos = pd.DataFrame(
        {
            "ID": ["A1", "A2"],
            "N": ["007", None],
            "I": ["1500.5", "-3"],
            "D": ["19.0414", "-0.5"],
            "F": ["2026-09-30", None],
            "H": ["2026-09-30 13:05:07", "2026-09-30T08:00:00.25"],
            "T": ["texto, con coma", None],
        },
        index=[2, 7],
        dtype="string",
    )
    juicio = juzgar_lote(datos, CONTRATO)
    assert juicio.rechazos == {}
    return juicio.canonicos


def _escribir(ruta, metadata=None):
    escritor = EscritorConformado(ruta, CONTRATO, metadata or {"contrato": "prueba/v1"})
    escritor.escribir(_canonicos(), pd.Series(["hoja", "hoja"], index=[2, 7]))
    escritor.cerrar()
    return escritor


def test_cada_columna_lleva_el_tipo_de_su_contrato_y_las_dos_tecnicas(tmp_path):
    escritor = _escribir(
        tmp_path / "d.parquet", {"contrato": "prueba/v1", "artefacto_original": "a" * 64}
    )

    tabla = leer(escritor.ruta)

    assert tabla.column_names == ["ID", "N", "I", "D", "F", "H", "T", COLUMNA_FILA, COLUMNA_HOJA]
    tipos = {campo.name: campo.type for campo in tabla.schema}
    assert tipos["N"] == pa.int64() and tipos["I"] == pa.decimal128(14, 2)
    assert tipos["D"] == pa.float64() and tipos["F"] == pa.date32()
    assert tipos["H"] == pa.timestamp("us") and tipos["T"] == pa.string()
    filas = tabla.to_pylist()
    assert filas[0] == {
        "ID": "A1",
        "N": 7,
        "I": Decimal("1500.50"),
        "D": 19.0414,
        "F": date(2026, 9, 30),
        "H": datetime(2026, 9, 30, 13, 5, 7),
        "T": "texto, con coma",
        COLUMNA_FILA: 2,
        COLUMNA_HOJA: "hoja",
    }
    assert filas[1]["I"] == Decimal("-3.00") and filas[1]["N"] is None
    assert filas[1][COLUMNA_FILA] == 7
    metadata = tabla.schema.metadata
    assert metadata[b"motor_cartera.contrato"] == b"prueba/v1"
    assert metadata[b"motor_cartera.artefacto_original"] == b"a" * 64
    assert escritor.filas == 2


def test_los_mismos_registros_dan_los_mismos_bytes(tmp_path):
    uno = _escribir(tmp_path / "uno.parquet")
    otro = _escribir(tmp_path / "otro.parquet")

    assert (
        hashlib.sha256(uno.ruta.read_bytes()).digest()
        == hashlib.sha256(otro.ruta.read_bytes()).digest()
    )


def test_un_lote_vacio_no_escribe_nada(tmp_path):
    escritor = EscritorConformado(tmp_path / "d.parquet", CONTRATO, {})
    escritor.escribir(_canonicos().iloc[[]], pd.Series([], dtype="string"))
    escritor.cerrar()

    assert escritor.filas == 0 and pq.read_table(escritor.ruta).num_rows == 0


def test_la_cartera_oficial_se_conforma_con_sus_93_columnas_tipadas(tmp_path):
    cartera = generar_cartera_oficial(300, semilla=4, fecha_corte=date(2026, 9, 30))
    cartera.index = range(2, 302)
    canonicos = juzgar_lote(cartera.astype("string"), CONTRATO_V2).canonicos
    escritor = EscritorConformado(tmp_path / "c.parquet", CONTRATO_V2, {"contrato": "cartera/v2"})

    escritor.escribir(canonicos, pd.Series(["CARTERA"] * 300, index=canonicos.index))
    escritor.cerrar()

    tabla = leer(escritor.ruta)
    assert tabla.num_rows == 300 and tabla.num_columns == 95
    assert tabla.schema.field("SALDO_TOTAL").type == pa.decimal128(14, 2)
    assert tabla.schema.field("FECHA_ASIGNACION").type == pa.date32()
    assert tabla.schema.field("SALDO ATRASADO").type == pa.decimal128(14, 2)
    assert tabla[COLUMNA_FILA].to_pylist() == list(range(2, 302))
