"""Reconocer el formato de un archivo por sus bytes, y no solo por su extension.

La extension la escribe quien sube el archivo; los bytes, el programa que lo genero. Un `.xlsx` es
un zip con la estructura de un libro de Excel; un `.csv` es texto. Un archivo cuyo contenido no es
el de su extension se rechaza con un error explicito, antes de guardarlo: no se adivina que quiso
decir quien lo subio.

Hay dos revisiones. La de los primeros bytes (`verificar_cabeza`) es inmediata y evita copiar un
archivo que ya se sabe que no sirve. La de la estructura completa (`verificar_estructura`: que el
zip abra y sea o no un libro, que el texto no traiga bytes nulos mas adelante) recorre la copia
entera, y el almacen la hace antes de que esa copia se vuelva un objeto.
"""

from __future__ import annotations

import codecs
import zipfile
from enum import StrEnum
from pathlib import Path, PurePosixPath


class Formato(StrEnum):
    """Los formatos que el almacen guarda. Parquet no se recibe: es el del dataset conformado."""

    XLSX = "xlsx"
    CSV = "csv"
    ZIP = "zip"
    PARQUET = "parquet"


MEDIA_TYPES = {
    Formato.XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    Formato.CSV: "text/csv",
    Formato.ZIP: "application/zip",
    Formato.PARQUET: "application/vnd.apache.parquet",
}

EXTENSIONES = {".xlsx": Formato.XLSX, ".csv": Formato.CSV, ".zip": Formato.ZIP}
"""Lo que se puede subir, por la extension del nombre."""

CABEZA = 8192
"""Cuantos bytes del principio se revisan antes de guardar."""

ZIP_LOCAL = b"PK\x03\x04"
ZIP_VACIO = b"PK\x05\x06"

BINARIOS = {
    b"PK\x03\x04": "un zip (xlsx, zip u otro)",
    b"PK\x05\x06": "un zip vacio",
    b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1": "un documento OLE2 (xls)",
    b"%PDF": "un PDF",
    b"\x1f\x8b": "un gzip",
    b"PAR1": "un Parquet",
    b"7z\xbc\xaf\x27\x1c": "un 7z",
    b"Rar!\x1a\x07": "un RAR",
    b"BZh": "un bzip2",
    b"\xfd7zXZ\x00": "un xz",
    b"\x28\xb5\x2f\xfd": "un zstd",
    b"SQLite format 3\x00": "una base SQLite",
    b"PGDMP": "un respaldo de PostgreSQL",
}
"""Firmas de formatos binarios: ninguna puede empezar un CSV."""


class FormatoNoSoportado(ValueError):
    """La extension no es de ningun formato que se acepte."""


class FormatoNoCorresponde(ValueError):
    """Los bytes no son los del formato que dice la extension."""


def formato_por_extension(nombre: str) -> Formato:
    """El formato que declara el nombre del archivo, o FormatoNoSoportado."""
    extension = PurePosixPath(nombre).suffix.lower()
    if extension not in EXTENSIONES:
        aceptados = ", ".join(EXTENSIONES)
        raise FormatoNoSoportado(f"Formato no soportado: {nombre!r}. Se aceptan {aceptados}.")
    return EXTENSIONES[extension]


def verificar_cabeza(formato: Formato, cabeza: bytes, nombre: str) -> None:
    """Revisa que los primeros bytes sean los de `formato`; si no, FormatoNoCorresponde.

    - xlsx: empieza como un zip, porque un libro de Excel es un zip.
    - zip: empieza como un zip, con miembros o vacio (un zip vacio se rechaza al leerlo).
    - csv: es texto. No empieza con la firma de un formato binario, no trae bytes nulos y se puede
      decodificar como UTF-8 o como cp1252.
    """
    if formato == Formato.XLSX and not cabeza.startswith(ZIP_LOCAL):
        raise FormatoNoCorresponde(
            f"{nombre!r} no es un Excel legible: un xlsx es un zip, y sus primeros bytes no son "
            "los de un zip."
        )
    if formato == Formato.ZIP and not cabeza.startswith((ZIP_LOCAL, ZIP_VACIO)):
        raise FormatoNoCorresponde(
            f"{nombre!r} no es un zip legible: sus primeros bytes no son los de un zip."
        )
    if formato == Formato.CSV:
        for firma, que_es in BINARIOS.items():
            if cabeza.startswith(firma):
                raise FormatoNoCorresponde(f"{nombre!r} no es un CSV: por dentro es {que_es}.")
        if b"\x00" in cabeza:
            raise FormatoNoCorresponde(f"{nombre!r} no es un CSV: trae bytes nulos, y no es texto.")
        if not es_texto(cabeza, final=False):
            raise FormatoNoCorresponde(
                f"{nombre!r} no es un CSV: no es texto en UTF-8 ni en cp1252."
            )


PARTES_DE_UN_LIBRO = ("[content_types].xml", "xl/workbook.xml")
"""Lo que hace de un zip un libro de Excel (OOXML). Las partes no distinguen mayusculas."""

BLOQUE = 1 << 20


def verificar_estructura(ruta: Path, declarado: Formato, nombre: str) -> None:
    """Revisa el archivo completo contra el formato que declara su nombre; si no corresponde,
    FormatoNoCorresponde. Es la revision que el almacen hace antes de guardar un objeto.

    - xlsx: un zip que abre y que tiene las partes de un libro de Excel.
    - zip: un zip que abre y que no es un libro de Excel: ese se sube como .xlsx. Asi un mismo
      contenido tiene un solo formato, el que dicen sus bytes.
    - csv: texto de principio a fin: sin bytes nulos, en UTF-8 o en cp1252.
    """
    if declarado in (Formato.XLSX, Formato.ZIP):
        try:
            with zipfile.ZipFile(ruta) as paquete:
                partes = {info.filename.lower() for info in paquete.infolist()}
        except (zipfile.BadZipFile, OSError) as exc:
            raise FormatoNoCorresponde(f"{nombre!r} no es un zip legible: {exc}") from exc
        es_libro = all(parte in partes for parte in PARTES_DE_UN_LIBRO)
        if declarado == Formato.XLSX and not es_libro:
            raise FormatoNoCorresponde(
                f"{nombre!r} no es un Excel legible: es un zip, pero no tiene las partes de un "
                "libro de Excel."
            )
        if declarado == Formato.ZIP and es_libro:
            raise FormatoNoCorresponde(
                f"{nombre!r} es un libro de Excel, no un zip de archivos: subelo como .xlsx."
            )
        return
    if declarado == Formato.CSV:
        verificar_texto(ruta, nombre)
        return
    raise FormatoNoCorresponde(f"{nombre!r}: {declarado} no es un formato que se reciba.")


def verificar_texto(ruta: Path, nombre: str) -> str:
    """Recorre el archivo por bloques y devuelve su codificacion, o FormatoNoCorresponde si en
    algun punto deja de ser texto. Nunca lo tiene entero en memoria."""
    for codificacion in ("utf-8-sig", "cp1252"):
        decodificador = codecs.getincrementaldecoder(codificacion)()
        try:
            with ruta.open("rb") as archivo:
                while bloque := archivo.read(BLOQUE):
                    if b"\x00" in bloque:
                        raise FormatoNoCorresponde(
                            f"{nombre!r} no es un CSV: trae bytes nulos, y no es texto."
                        )
                    decodificador.decode(bloque)
                decodificador.decode(b"", final=True)
        except UnicodeDecodeError:
            continue
        return codificacion
    raise FormatoNoCorresponde(f"{nombre!r} no es un CSV: no es texto en UTF-8 ni en cp1252.")


def es_texto(fragmento: bytes, *, final: bool) -> bool:
    """Si `fragmento` se puede decodificar como UTF-8 o como cp1252. Con final=False se admite que
    el fragmento corte un caracter de UTF-8 a la mitad: es un pedazo del archivo, no el archivo."""
    return codificacion_de(fragmento, final=final) is not None


def codificacion_de(fragmento: bytes, *, final: bool) -> str | None:
    """`utf-8-sig` si el fragmento es UTF-8, `cp1252` si no lo es pero si es cp1252, o None.

    UTF-8 primero porque falla ruidosamente con bytes que no lo son; cp1252 despues, porque es como
    exportan Excel y muchos sistemas en Mexico. Es el mismo orden que usa la lectura de cartera/v1.
    """
    for codificacion in ("utf-8-sig", "cp1252"):
        decodificador = codecs.getincrementaldecoder(codificacion)()
        try:
            decodificador.decode(fragmento, final=final)
        except UnicodeDecodeError:
            continue
        return codificacion
    return None
