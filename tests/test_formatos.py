"""Reconocer el formato por los bytes y no solo por la extension, sin base de datos."""

from __future__ import annotations

import io
import zipfile

import pandas as pd
import pytest

from motor_cartera.fuentes.formatos import (
    CABEZA,
    Formato,
    FormatoNoCorresponde,
    FormatoNoSoportado,
    codificacion_de,
    formato_por_extension,
    verificar_cabeza,
    verificar_estructura,
    verificar_texto,
)

CSV = b"cliente,saldo\nCU0000000001,10.50\n"


def _xlsx() -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as escritor:
        pd.DataFrame({"a": [1]}).to_excel(escritor, sheet_name="CARTERA", index=False)
    return buf.getvalue()


def _zip(**miembros: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as paquete:
        for nombre, contenido in miembros.items():
            paquete.writestr(nombre, contenido)
    return buf.getvalue()


@pytest.mark.parametrize(
    ("nombre", "formato"),
    [
        ("cartera.xlsx", Formato.XLSX),
        ("CARTERA.XLSX", Formato.XLSX),
        ("pagos.csv", Formato.CSV),
        ("paquete.Zip", Formato.ZIP),
    ],
)
def test_la_extension_declara_un_formato(nombre, formato):
    assert formato_por_extension(nombre) == formato


@pytest.mark.parametrize("nombre", ["cartera.pdf", "cartera.xls", "cartera", "cartera.parquet"])
def test_una_extension_que_no_se_recibe(nombre):
    with pytest.raises(FormatoNoSoportado, match="Formato no soportado"):
        formato_por_extension(nombre)


# --- los primeros bytes ----------------------------------------------------------------------


def test_un_xlsx_tiene_que_empezar_como_un_zip():
    verificar_cabeza(Formato.XLSX, _xlsx()[:CABEZA], "c.xlsx")
    with pytest.raises(FormatoNoCorresponde, match="no es un Excel legible"):
        verificar_cabeza(Formato.XLSX, b"no soy un excel", "c.xlsx")


def test_un_zip_tiene_que_empezar_como_un_zip():
    verificar_cabeza(Formato.ZIP, _zip(**{"a.csv": CSV})[:CABEZA], "p.zip")
    verificar_cabeza(Formato.ZIP, _zip()[:CABEZA], "vacio.zip")  # se rechaza al leerlo
    with pytest.raises(FormatoNoCorresponde, match="no es un zip legible"):
        verificar_cabeza(Formato.ZIP, b"%PDF-1.7", "p.zip")


@pytest.mark.parametrize(
    ("cabeza", "motivo"),
    [
        (b"PK\x03\x04restodeunzip", "por dentro es un zip"),
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1xls", "OLE2"),
        (b"%PDF-1.7", "un PDF"),
        (b"\x1f\x8b\x08gzip", "un gzip"),
        (b"PAR1parquet", "un Parquet"),
        (b"PGDMP respaldo", "un respaldo de PostgreSQL"),
        (b"cliente,saldo\x00\x00\n", "bytes nulos"),
        (b"\x81\x8d\x81\x8d", "no es texto"),
    ],
)
def test_un_csv_no_acepta_bytes_arbitrarios_por_llamarse_csv(cabeza, motivo):
    with pytest.raises(FormatoNoCorresponde, match=motivo):
        verificar_cabeza(Formato.CSV, cabeza, "c.csv")


def test_un_csv_en_utf8_o_en_cp1252_si_es_texto():
    verificar_cabeza(Formato.CSV, "cliente,poblacion\nCU1,TEHUACÁN\n".encode(), "c.csv")
    verificar_cabeza(Formato.CSV, "cliente,poblacion\nCU1,TEHUACÁN\n".encode("cp1252"), "c.csv")
    # La cabeza puede cortar un caracter de UTF-8 a la mitad: es un pedazo del archivo.
    verificar_cabeza(Formato.CSV, "aá".encode()[:-1], "c.csv")


# --- la estructura completa ------------------------------------------------------------------


def _escribir(tmp_path, nombre: str, contenido: bytes):
    ruta = tmp_path / nombre
    ruta.write_bytes(contenido)
    return ruta


def test_un_libro_de_excel_se_reconoce_por_sus_partes(tmp_path):
    verificar_estructura(_escribir(tmp_path, "c.xlsx", _xlsx()), Formato.XLSX, "c.xlsx")


def test_un_zip_que_no_es_un_libro_no_es_un_xlsx(tmp_path):
    ruta = _escribir(tmp_path, "c.xlsx", _zip(**{"cartera.csv": CSV}))

    with pytest.raises(FormatoNoCorresponde, match="no tiene las partes de un libro"):
        verificar_estructura(ruta, Formato.XLSX, "c.xlsx")


def test_un_libro_de_excel_no_se_sube_como_zip(tmp_path):
    # Un mismo contenido tiene un solo formato: el que dicen sus bytes.
    ruta = _escribir(tmp_path, "c.zip", _xlsx())

    with pytest.raises(FormatoNoCorresponde, match="subelo como .xlsx"):
        verificar_estructura(ruta, Formato.ZIP, "c.zip")


def test_un_zip_de_archivos_es_un_zip(tmp_path):
    ruta = _escribir(tmp_path, "p.zip", _zip(**{"cartera.csv": CSV, "LEEME.txt": b"x"}))

    verificar_estructura(ruta, Formato.ZIP, "p.zip")


def test_un_zip_que_no_abre(tmp_path):
    ruta = _escribir(tmp_path, "p.zip", b"PK\x03\x04" + b"basura" * 100)

    with pytest.raises(FormatoNoCorresponde, match="no es un zip legible"):
        verificar_estructura(ruta, Formato.ZIP, "p.zip")


def test_un_csv_con_bytes_nulos_mas_alla_de_la_cabeza(tmp_path):
    contenido = CSV * 2000 + b"CU2,\x00\n"  # despues de los primeros bytes que se revisan
    assert len(contenido) > CABEZA
    ruta = _escribir(tmp_path, "c.csv", contenido)

    with pytest.raises(FormatoNoCorresponde, match="bytes nulos"):
        verificar_estructura(ruta, Formato.CSV, "c.csv")


def test_un_csv_que_deja_de_ser_texto_a_la_mitad(tmp_path):
    ruta = _escribir(tmp_path, "c.csv", CSV * 2000 + b"\x81\x8d")

    with pytest.raises(FormatoNoCorresponde, match="no es texto"):
        verificar_estructura(ruta, Formato.CSV, "c.csv")


def test_la_codificacion_de_un_csv(tmp_path):
    utf8 = _escribir(tmp_path, "u.csv", "TEHUACÁN\n".encode())
    cp1252 = _escribir(tmp_path, "w.csv", "TEHUACÁN\n".encode("cp1252"))

    assert verificar_texto(utf8, "u.csv") == "utf-8-sig"
    assert verificar_texto(cp1252, "w.csv") == "cp1252"
    assert codificacion_de(b"\x81\x8d", final=True) is None


def test_parquet_no_es_un_formato_que_se_reciba(tmp_path):
    ruta = _escribir(tmp_path, "d.parquet", b"PAR1")

    with pytest.raises(FormatoNoCorresponde, match="no es un formato que se reciba"):
        verificar_estructura(ruta, Formato.PARQUET, "d.parquet")
