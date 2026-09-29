"""Lectura de archivos crudos en cualquier formato y forma."""

from __future__ import annotations

import io
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import pandas as pd

from motor_cartera.contratos.cartera import CarteraCruda

# Sinonimos aceptados por columna. El objetivo es que un cambio de encabezado en el
# archivo fuente no rompa el proceso: eso es lo que en la practica tumba los pipelines.
ALIAS: dict[str, tuple[str, ...]] = {
    "cliente_unico": ("cliente unico", "cliente_unico", "clienteunico", "id cliente", "cu"),
    "saldo_total": ("saldo total", "saldo", "saldo_actual", "importe"),
    "dias_atraso": ("dias atraso", "dias_atraso", "atraso", "dias de atraso"),
    "producto": ("producto", "tipo producto", "linea"),
    "canal": ("canal", "canal gestion", "tipo gestion"),
    "cve_entidad": ("cve entidad", "cve_ent", "entidad", "estado"),
    "cve_municipio": ("cve municipio", "cve_mun", "municipio"),
    "fecha_corte": ("fecha corte", "fecha_corte", "corte", "fecha"),
}

REQUERIDAS: tuple[str, ...] = tuple(CarteraCruda.to_schema().columns)
"""Que columnas hacen falta lo dice el contrato; aqui solo se sabe como pueden venir escritas."""

FORMATOS = (".xlsx", ".csv", ".zip")
TABULARES = (".xlsx", ".csv")
"""Lo que puede contener un zip. Un zip dentro de otro no se abre."""

PRIMERA_FILA_DE_DATOS = 2
"""El encabezado es la fila 1 del archivo, asi que el primer registro es la fila 2."""


class ErrorDeLectura(ValueError):
    """El archivo no se puede leer como cartera: formato, estructura o encabezados.

    Es distinto de ErrorDeContrato: aqui todavia no se juzgo ningun registro.
    """


@dataclass(frozen=True)
class Lectura:
    """Lo que salio de leer un archivo."""

    datos: pd.DataFrame
    """Columnas con su nombre canonico y todo como texto: convertir tipos es trabajo del
    contrato. El indice es el numero de fila en el archivo, para ubicar cada rechazo."""
    origen: str
    """Que se leyo y por que: la hoja o el miembro del zip elegido y lo que se descarto."""


def normalizar(texto: str) -> str:
    """Baja a minusculas, quita acentos y colapsa espacios.

    El guion bajo cuenta como espacio: `CVE_ENT` y `cve ent` son el mismo encabezado.
    """
    descompuesto = unicodedata.normalize("NFKD", str(texto))
    sin_acentos = "".join(c for c in descompuesto if not unicodedata.combining(c))
    return " ".join(sin_acentos.lower().replace("_", " ").split())


# El nombre que usa el contrato siempre se acepta, este o no entre los alias.
_CANONICO = {
    normalizar(alias): nombre for nombre, alias_ in ALIAS.items() for alias in (nombre, *alias_)
}


def mapear_columnas(df: pd.DataFrame) -> dict[str, str]:
    """Devuelve {columna_original: nombre_canonico} usando ALIAS y `normalizar`.

    Si una columna requerida no aparece, o si dos columnas dicen ser la misma, no se
    adivina: se levanta ErrorDeLectura con lo que falta y los encabezados que si venian.
    """
    mapeo: dict[str, str] = {}
    originales: dict[str, list[str]] = {}
    for original in df.columns:
        canonico = _CANONICO.get(normalizar(original))
        if canonico is not None:
            mapeo[original] = canonico
            originales.setdefault(canonico, []).append(str(original))

    ambiguas = {canonico: cols for canonico, cols in originales.items() if len(cols) > 1}
    if ambiguas:
        detalle = "; ".join(f"{c}: {', '.join(cols)}" for c, cols in ambiguas.items())
        raise ErrorDeLectura(f"Hay columnas que dicen ser la misma ({detalle}).")

    faltantes = [nombre for nombre in REQUERIDAS if nombre not in originales]
    if faltantes:
        recibidos = ", ".join(str(c) for c in df.columns) or "ninguno"
        raise ErrorDeLectura(
            f"Faltan columnas requeridas: {', '.join(faltantes)}. "
            f"Encabezados recibidos: {recibidos}."
        )
    return mapeo


def leer(ruta: str | Path) -> Lectura:
    """Lee xlsx, csv o zip y devuelve los datos con los nombres canonicos."""
    ruta = Path(ruta)
    return leer_contenido(ruta.read_bytes(), ruta.name)


def leer_contenido(contenido: bytes, nombre: str) -> Lectura:
    """Como `leer`, sobre bytes ya cargados; el formato lo dice la extension de `nombre`.

    Es la entrada que usa la API, donde el archivo llega en la peticion y no en disco.
    """
    formato = PurePosixPath(nombre).suffix.lower()
    if formato not in FORMATOS:
        raise ErrorDeLectura(f"Formato no soportado: {nombre!r}. Se aceptan {', '.join(FORMATOS)}.")
    if not contenido:
        raise ErrorDeLectura(f"{nombre!r} esta vacio.")

    if formato == ".zip":
        crudo, origen = _leer_zip(contenido, nombre)
    else:
        crudo, origen = _leer_tabla(contenido, nombre)
    return _canonizar(crudo, origen)


def _leer_tabla(contenido: bytes, nombre: str) -> tuple[pd.DataFrame, str]:
    """Devuelve la tabla cruda, ya verificada contra los encabezados, y como se obtuvo."""
    if PurePosixPath(nombre).suffix.lower() == ".csv":
        return _leer_csv(contenido, nombre)
    return _leer_xlsx(contenido, nombre)


def _leer_csv(contenido: bytes, nombre: str) -> tuple[pd.DataFrame, str]:
    # UTF-8 primero porque falla ruidosamente con bytes que no lo son; cp1252 despues,
    # porque es como exportan Excel y muchos sistemas en Mexico. Lo usado queda registrado.
    for codificacion in ("utf-8-sig", "cp1252"):
        try:
            texto = contenido.decode(codificacion)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ErrorDeLectura(f"{nombre!r} no es texto en UTF-8 ni en cp1252.")

    try:
        # skip_blank_lines=False: una linea en blanco sigue contando como fila, o los
        # numeros de fila de los rechazos dejarian de coincidir con el archivo.
        df = pd.read_csv(io.StringIO(texto), dtype=str, skip_blank_lines=False)
    except (pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise ErrorDeLectura(f"{nombre!r} no es un CSV legible: {exc}") from exc
    mapear_columnas(df)
    nota = "" if codificacion == "utf-8-sig" else f" (codificacion {codificacion})"
    return df, f"{nombre!r}{nota}"


def _leer_xlsx(contenido: bytes, nombre: str) -> tuple[pd.DataFrame, str]:
    try:
        hojas = pd.read_excel(io.BytesIO(contenido), sheet_name=None, dtype=str, engine="openpyxl")
    except Exception as exc:  # openpyxl tiene muchas formas de decir "esto no es un Excel"
        raise ErrorDeLectura(f"{nombre!r} no es un Excel legible: {exc}") from exc

    utiles: list[tuple[str, pd.DataFrame]] = []
    descartes: list[str] = []
    for hoja, df in hojas.items():
        try:
            mapear_columnas(df)
        except ErrorDeLectura as exc:
            descartes.append(f"hoja {hoja!r}: {exc}")
        else:
            utiles.append((hoja, df))

    hoja, df = _elegir_una(utiles, descartes, que="hojas", donde=nombre)
    return df, _describir(f"hoja {hoja!r} de {nombre!r}", descartes)


def _leer_zip(contenido: bytes, nombre: str) -> tuple[pd.DataFrame, str]:
    try:
        paquete = zipfile.ZipFile(io.BytesIO(contenido))
    except zipfile.BadZipFile as exc:
        raise ErrorDeLectura(f"{nombre!r} no es un zip legible.") from exc

    utiles: list[tuple[str, tuple[pd.DataFrame, str]]] = []
    descartes: list[str] = []
    with paquete:
        for info in paquete.infolist():
            interno = PurePosixPath(info.filename)
            if info.is_dir() or "__MACOSX" in interno.parts:
                continue  # ruido del empaquetado, no son candidatos
            if interno.suffix.lower() not in TABULARES:
                descartes.append(f"{info.filename!r}: formato no soportado dentro de un zip")
                continue
            try:
                tabla = _leer_tabla(paquete.read(info), info.filename)
            except (ErrorDeLectura, RuntimeError, zipfile.BadZipFile) as exc:
                descartes.append(f"{info.filename!r}: {exc}")
            else:
                utiles.append((info.filename, tabla))

    _, (df, origen) = _elegir_una(utiles, descartes, que="archivos", donde=nombre)
    return df, _describir(f"{origen}, dentro de {nombre!r}", descartes)


def _elegir_una[T](
    utiles: list[tuple[str, T]], descartes: list[str], *, que: str, donde: str
) -> tuple[str, T]:
    """Elige el unico candidato con forma de cartera. Cero o varios es un error, no un azar."""
    if len(utiles) == 1:
        return utiles[0]
    if not utiles:
        motivos = "; ".join(descartes) or "no contiene nada"
        raise ErrorDeLectura(f"No hay {que} con forma de cartera en {donde!r}: {motivos}")
    nombres = ", ".join(repr(n) for n, _ in utiles)
    raise ErrorDeLectura(
        f"Hay {len(utiles)} {que} con forma de cartera en {donde!r} ({nombres}); "
        "no se elige una a ciegas."
    )


def _describir(elegido: str, descartes: list[str]) -> str:
    if not descartes:
        return elegido
    return f"{elegido}. Descartado: {'; '.join(descartes)}"


def _canonizar(crudo: pd.DataFrame, origen: str) -> Lectura:
    mapeo = mapear_columnas(crudo)
    datos = crudo[list(mapeo)].rename(columns=mapeo)[list(REQUERIDAS)]
    datos.index = datos.index + PRIMERA_FILA_DE_DATOS

    # Espacios alrededor de un valor nunca significan nada; una celda que solo tiene
    # espacios es una celda vacia. Una fila completamente vacia no es un registro.
    datos = datos.apply(lambda columna: columna.str.strip().where(lambda v: v != ""))
    datos = datos[datos.notna().any(axis=1)]

    if datos.empty:
        raise ErrorDeLectura(f"{origen}: no trae ningun registro.")
    return Lectura(datos=datos, origen=origen)
