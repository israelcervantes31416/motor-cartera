"""Leer una fuente oficial por lotes, sin base de datos: la estructura antes que los registros, la
fila y la hoja de cada registro, y la hoja companera."""

from __future__ import annotations

import io
import zipfile
from datetime import date, datetime
from decimal import Decimal

import openpyxl
import pandas as pd
import pytest

from motor_cartera.contratos.cartera_v2 import COLUMNAS_CARRIER, CONTRATO_V2
from motor_cartera.contratos.fuente import Columna, ContratoFuente, ErrorDeEstructura, Tipo
from motor_cartera.fuentes.formatos import Formato
from motor_cartera.fuentes.lotes import EspecificacionCompanera, _texto_de_celda, abrir_fuente
from motor_cartera.ingesta.lectores import ErrorDeLectura

CONTRATO = ContratoFuente(
    "prueba/v1",
    (
        Columna("ID", Tipo.CODIGO, patron=r"[A-Z0-9]+"),
        Columna("MONTO", Tipo.IMPORTE),
        Columna("TXT"),
    ),
    llave_unica="ID",
)
COMPANERA = EspecificacionCompanera(
    "CARRIER", ("ID", "TELEFONO"), llave="ID", formas={"TELEFONO": r"\d{10}"}
)


def _escribir(tmp_path, nombre: str, contenido: bytes):
    ruta = tmp_path / nombre
    ruta.write_bytes(contenido)
    return ruta


def _leer(ruta, formato, *, por_lote=2, hoja=None, companera=None, contrato=CONTRATO):
    fuente = abrir_fuente(
        ruta,
        formato,
        ruta.name,
        contrato,
        filas_por_lote=por_lote,
        hoja_principal=hoja,
        companera=companera,
    )
    with fuente:
        lotes = list(fuente.lotes())
    return fuente, lotes


def _xlsx(tmp_path, nombre: str, **hojas: list[list[object]]):
    libro = openpyxl.Workbook(write_only=True)
    for titulo, filas in hojas.items():
        hoja = libro.create_sheet(titulo)
        for fila in filas:
            hoja.append(fila)
    ruta = tmp_path / nombre
    libro.save(ruta)
    return ruta


# --- csv -----------------------------------------------------------------------------------------


def test_csv_por_lotes_conserva_la_fila_de_cada_registro_y_ordena_las_columnas(tmp_path):
    # Columnas en otro orden, una linea en blanco que sigue contando y una fila solo con espacios.
    contenido = "TXT,MONTO,ID\na,1.00,A1\nb,2.00,A2\n\n  ,  ,  \nc,3.00,A3\nd,4.00,A4\n"
    ruta = _escribir(tmp_path, "c.csv", contenido.encode())

    fuente, lotes = _leer(ruta, Formato.CSV)

    filas = pd.concat([lote.datos for lote in lotes])
    assert list(filas.columns) == ["ID", "MONTO", "TXT"]
    assert list(filas.index) == [2, 3, 6, 7]
    assert list(filas["ID"]) == ["A1", "A2", "A3", "A4"]
    assert all(lote.hoja is None for lote in lotes)
    assert fuente.origen == "'c.csv'"


def test_csv_no_convierte_textos_en_nulos_ni_quita_ceros(tmp_path):
    ruta = _escribir(tmp_path, "c.csv", b"ID,MONTO,TXT\nA1,1.00,N/A\nA2,2.00,NULL\nA3,,007\n")

    _, lotes = _leer(ruta, Formato.CSV, por_lote=10)

    datos = lotes[0].datos
    assert list(datos["TXT"]) == ["N/A", "NULL", "007"]
    assert pd.isna(datos.loc[4, "MONTO"])


def test_csv_en_cp1252_se_lee_y_lo_dice(tmp_path):
    ruta = _escribir(tmp_path, "c.csv", "ID,MONTO,TXT\nA1,1.00,TEHUACÁN\n".encode("cp1252"))

    fuente, lotes = _leer(ruta, Formato.CSV)

    assert lotes[0].datos.loc[2, "TXT"] == "TEHUACÁN"
    assert "cp1252" in fuente.origen


def test_csv_con_una_fila_de_mas_campos_es_un_error_de_estructura(tmp_path):
    ruta = _escribir(tmp_path, "c.csv", b"ID,MONTO,TXT\nA1,1.00,a\nA2,2.00,b,sobra\n")

    with pytest.raises(ErrorDeEstructura, match="no tiene la forma de una tabla"):
        _leer(ruta, Formato.CSV, por_lote=10)


def test_csv_sin_la_estructura_del_contrato_no_se_lee(tmp_path):
    ruta = _escribir(tmp_path, "c.csv", b"ID,MONTO\nA1,1.00\n")

    with pytest.raises(ErrorDeEstructura, match="faltan 1 columnas del contrato: TXT"):
        _leer(ruta, Formato.CSV)


def test_csv_con_bytes_nulos_no_se_lee(tmp_path):
    ruta = _escribir(tmp_path, "c.csv", b"ID,MONTO,TXT\nA1,1.00,a\x00\n")

    with pytest.raises(ErrorDeLectura, match="bytes nulos"):
        _leer(ruta, Formato.CSV)


def test_csv_solo_con_encabezado_no_trae_lotes(tmp_path):
    ruta = _escribir(tmp_path, "c.csv", b"ID,MONTO,TXT\n")

    _, lotes = _leer(ruta, Formato.CSV)

    assert lotes == []


# --- zip -----------------------------------------------------------------------------------------


def _zip(tmp_path, nombre: str, **miembros: bytes):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as paquete:
        for interno, contenido in miembros.items():
            paquete.writestr(interno, contenido)
    return _escribir(tmp_path, nombre, buf.getvalue())


def test_zip_elige_el_unico_csv_con_la_estructura_y_registra_lo_descartado(tmp_path):
    ruta = _zip(
        tmp_path,
        "p.zip",
        **{
            "LEEME.txt": b"x",
            "otra_tabla.csv": b"A,B\n1,2\n",
            "__MACOSX/._cartera.csv": b"ruido",
            "cartera.csv": b"ID,MONTO,TXT\nA1,1.00,a\nA2,2.00,b\nA3,3.00,c\n",
        },
    )

    fuente, lotes = _leer(ruta, Formato.ZIP)

    assert [len(lote.datos) for lote in lotes] == [2, 1]
    assert {lote.hoja for lote in lotes} == {"cartera.csv"}
    assert fuente.origen.startswith("'cartera.csv', dentro de 'p.zip'. Descartado:")
    assert "'LEEME.txt': dentro de un zip solo se leen csv" in fuente.origen
    assert "'otra_tabla.csv'" in fuente.origen and "__MACOSX" not in fuente.origen


@pytest.mark.parametrize(
    ("miembros", "error"),
    [
        (
            {"a.csv": b"ID,MONTO,TXT\nA1,1,a\n", "b.csv": b"ID,MONTO,TXT\nA2,2,b\n"},
            "Hay 2 archivos",
        ),
        ({"a.csv": b"A,B\n1,2\n"}, "No hay un archivo con la estructura"),
        ({}, "no contiene nada"),
    ],
    ids=["dos", "ninguno", "vacio"],
)
def test_zip_sin_un_unico_candidato_no_elige_a_ciegas(tmp_path, miembros, error):
    ruta = _zip(tmp_path, "p.zip", **miembros)

    with pytest.raises(ErrorDeLectura, match=error):
        _leer(ruta, Formato.ZIP)


def test_zip_audita_su_companera_sin_bloquear_nada(tmp_path):
    ruta = _zip(
        tmp_path,
        "p.zip",
        **{
            "cartera.csv": b"ID,MONTO,TXT\nA1,1.00,a\nA2,2.00,b\n",
            # A9 no esta en la cartera, y un telefono tiene 9 digitos.
            "CARRIER.csv": (
                b"ID,TELEFONO\nA1,0123456789\nA1,0987654321\nA9,0111111111\nA2,012345678\n"
            ),
        },
    )
    fuente = abrir_fuente(
        ruta, Formato.ZIP, "p.zip", CONTRATO, filas_por_lote=10, companera=COMPANERA
    )
    with fuente:
        list(fuente.lotes())
        auditada = fuente.auditar_companera({"A1", "A2"})

    assert (auditada.nombre, auditada.filas, auditada.columnas) == ("CARRIER.csv", 4, 2)
    assert auditada.estructura_reconocida is True
    assert auditada.advertencias == (
        "1 filas con un ID que no esta en la tabla principal.",
        "1 filas con un TELEFONO que no tiene la forma esperada.",
    )


def test_una_companera_con_otra_estructura_se_reconoce_como_distinta(tmp_path):
    ruta = _zip(
        tmp_path,
        "p.zip",
        **{"cartera.csv": b"ID,MONTO,TXT\nA1,1.00,a\n", "CARRIER.csv": b"ID,TEL,EXTRA\nA1,1,2\n"},
    )
    fuente = abrir_fuente(
        ruta, Formato.ZIP, "p.zip", CONTRATO, filas_por_lote=10, companera=COMPANERA
    )
    with fuente:
        auditada = fuente.auditar_companera({"A1"})

    assert auditada.estructura_reconocida is False
    assert "Le faltan 1 columnas: TELEFONO." in auditada.advertencias
    assert "Le sobran 2 columnas: TEL, EXTRA." in auditada.advertencias


def test_una_companera_danada_es_un_error_de_estructura(tmp_path):
    # El CRC de CARRIER ya no corresponde a sus bytes: el archivo esta danado, no solo incoherente.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as paquete:
        paquete.writestr("cartera.csv", b"ID,MONTO,TXT\nA1,1.00,a\n")
        paquete.writestr("CARRIER.csv", b"ID,TELEFONO\nA1,0123456789\n" * 50)
    datos = bytearray(buf.getvalue())
    posicion = datos.index(b"A1,0123456789", datos.index(b"CARRIER.csv") + 20)
    datos[posicion] = ord("B")
    ruta = _escribir(tmp_path, "p.zip", bytes(datos))
    fuente = abrir_fuente(
        ruta, Formato.ZIP, "p.zip", CONTRATO, filas_por_lote=10, companera=COMPANERA
    )
    with fuente, pytest.raises(ErrorDeEstructura, match="esta danado"):
        fuente.auditar_companera({"A1"})


# --- xlsx ----------------------------------------------------------------------------------------


def test_xlsx_lee_la_hoja_pedida_en_streaming_con_su_fila_y_su_hoja(tmp_path):
    ruta = _xlsx(
        tmp_path,
        "c.xlsx",
        LEEME=[["nota"]],
        cartera=[
            ["TXT", "MONTO", "ID"],
            ["a", 1500.5, "A1"],
            [],
            [None, None, None],
            ["c", 12, "A3"],
        ],
    )

    fuente, lotes = _leer(ruta, Formato.XLSX, hoja="CARTERA")

    filas = pd.concat([lote.datos for lote in lotes])
    assert list(filas.index) == [2, 5]
    assert list(filas["MONTO"]) == ["1500.5", "12"]
    assert {lote.hoja for lote in lotes} == {"cartera"}
    assert fuente.origen == "hoja 'cartera' de 'c.xlsx'"


@pytest.mark.parametrize(
    ("hojas", "error"),
    [
        ({"Hoja1": [["ID", "MONTO", "TXT"]]}, "no tiene la hoja CARTERA"),
        (
            {"CARTERA": [["ID", "MONTO", "TXT"]], "cartera ": [["ID", "MONTO", "TXT"]]},
            "tiene varias hojas que se llaman CARTERA",
        ),
    ],
    ids=["sin-hoja", "dos-hojas"],
)
def test_xlsx_busca_explicitamente_su_hoja(tmp_path, hojas, error):
    ruta = _xlsx(tmp_path, "c.xlsx", **hojas)

    with pytest.raises(ErrorDeLectura, match=error):
        _leer(ruta, Formato.XLSX, hoja="CARTERA")


def test_xlsx_sin_hoja_pedida_elige_la_unica_con_la_estructura(tmp_path):
    ruta = _xlsx(
        tmp_path, "p.xlsx", Notas=[["x"]], Datos=[["ID", "MONTO", "TXT"], ["A1", "1.00", "a"]]
    )

    fuente, lotes = _leer(ruta, Formato.XLSX)

    assert fuente.origen.startswith("hoja 'Datos' de 'p.xlsx'. Descartado: hoja 'Notas'")
    assert len(lotes[0].datos) == 1


def test_xlsx_con_valores_fuera_del_encabezado_es_un_error_de_estructura(tmp_path):
    ruta = _xlsx(tmp_path, "c.xlsx", CARTERA=[["ID", "MONTO", "TXT"], ["A1", "1", "a", "sobra"]])

    with pytest.raises(ErrorDeEstructura, match="fuera de las 3 columnas"):
        _leer(ruta, Formato.XLSX, hoja="CARTERA")


def test_xlsx_que_no_abre_no_se_lee(tmp_path):
    ruta = _zip(tmp_path, "c.xlsx", **{"a.txt": b"no es un libro"})

    with pytest.raises(ErrorDeLectura, match="no es un Excel legible"):
        _leer(ruta, Formato.XLSX, hoja="CARTERA")


def test_xlsx_con_la_cartera_oficial_y_su_carrier(tmp_path):
    carrier = list(COLUMNAS_CARRIER)
    fila = {c: None for c in CONTRATO_V2.nombres}
    fila.update(CLIENTE_UNICO="CU0000000001")
    ruta = _xlsx(
        tmp_path,
        "o.xlsx",
        CARTERA=[list(CONTRATO_V2.nombres), list(fila.values())],
        CARRIER=[carrier, ["CU0000000001" if c == "CLIENTE_UNICO" else None for c in carrier]],
    )
    especificacion = EspecificacionCompanera(
        "CARRIER", COLUMNAS_CARRIER, "CLIENTE_UNICO", {"TELEFONO": r"\d{10}"}
    )
    fuente = abrir_fuente(
        ruta,
        Formato.XLSX,
        "o.xlsx",
        CONTRATO_V2,
        filas_por_lote=10,
        hoja_principal="CARTERA",
        companera=especificacion,
    )
    with fuente:
        (lote,) = list(fuente.lotes())
        auditada = fuente.auditar_companera({"CU0000000001"})

    assert list(lote.datos.columns) == list(CONTRATO_V2.nombres)
    assert (auditada.filas, auditada.columnas, auditada.estructura_reconocida) == (1, 85, True)
    assert auditada.advertencias == ("1 filas con un TELEFONO que no tiene la forma esperada.",)


@pytest.mark.parametrize(
    ("valor", "texto"),
    [
        (None, None),
        ("  x ", "  x "),
        (12, "12"),
        (12.0, "12"),
        (1500.5, "1500.5"),
        (0.1 + 0.2, "0.30000000000000004"),  # no se redondea: el contrato decide
        (True, "TRUE"),
        (datetime(2026, 9, 30), "2026-09-30"),
        (datetime(2026, 9, 30, 13, 5, 7), "2026-09-30T13:05:07"),
        (datetime(2026, 9, 30, 13, 5, 7, 500000), "2026-09-30T13:05:07.500000"),
        (date(2026, 9, 30), "2026-09-30"),
        (Decimal("1500.50"), "1500.50"),
    ],
)
def test_una_celda_de_excel_como_texto_sin_perder_nada(valor, texto):
    assert _texto_de_celda(valor) == texto


def test_una_fuente_oficial_no_se_lee_de_un_parquet(tmp_path):
    ruta = _escribir(tmp_path, "d.parquet", b"PAR1")

    with pytest.raises(ErrorDeLectura, match="no se lee de un parquet"):
        _leer(ruta, Formato.PARQUET)
