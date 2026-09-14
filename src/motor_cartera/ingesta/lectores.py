"""Lectura de archivos crudos en cualquier formato y forma."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

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


def normalizar(texto: str) -> str:
    """Baja a minusculas, quita acentos y colapsa espacios.

    TODO(israel): implementalo con unicodedata.normalize('NFKD', ...).
    """
    raise NotImplementedError("Pendiente.")


def mapear_columnas(df: pd.DataFrame) -> dict[str, str]:
    """Devuelve {columna_original: nombre_canonico} usando ALIAS y `normalizar`.

    TODO(israel): si una columna requerida no aparece, no adivines: levanta un error
    que diga cual falta y que encabezados si venian. Adivinar aqui es como se cuelan
    los errores silenciosos.
    """
    raise NotImplementedError("Pendiente.")


def leer(ruta: str | Path) -> pd.DataFrame:
    """Lee xlsx, csv o zip y devuelve un DataFrame con los nombres canonicos.

    TODO(israel): para xlsx, detecta la hoja util en vez de asumir la primera.
    Para zip, decide cual archivo interno es el bueno y registra por que.
    """
    raise NotImplementedError("Pendiente.")
