"""Contrato de datos de la cartera.

Un contrato declara, en un solo lugar, que forma debe tener un conjunto de datos para
que el resto del sistema pueda confiar en el. La validacion es *fail-closed*: si un
archivo no cumple, el proceso se detiene y no escribe nada. Es preferible no producir
salida a producir salida incorrecta, porque una salida incorrecta se usa para operar.
"""

from __future__ import annotations

import pandas as pd
import pandera as pa
from pandera.typing import Series

PRODUCTOS = ("CONSUMO", "TARJETA", "NOMINA", "AUTOMOTRIZ")
CANALES = ("CAMPO", "TELEFONICA", "DIGITAL")


class ErrorDeContrato(RuntimeError):
    """Se levanta cuando un conjunto de datos no cumple su contrato.

    Lleva el reporte de pandera para que la bitacora registre que fallo y en que filas.
    """

    def __init__(self, mensaje: str, fallas: pd.DataFrame | None = None) -> None:
        super().__init__(mensaje)
        self.fallas = fallas


class CarteraCruda(pa.DataFrameModel):
    """Lo minimo que debe traer una cartera para entrar al sistema."""

    cliente_unico: Series[str] = pa.Field(unique=True, str_matches=r"^[A-Z0-9]{8,20}$")
    saldo_total: Series[float] = pa.Field(ge=0, nullable=False)
    dias_atraso: Series[int] = pa.Field(ge=0, le=3650)
    producto: Series[str] = pa.Field(isin=PRODUCTOS)
    canal: Series[str] = pa.Field(isin=CANALES)
    cve_entidad: Series[str] = pa.Field(str_matches=r"^\d{2}$")
    cve_municipio: Series[str] = pa.Field(str_matches=r"^\d{3}$")
    fecha_corte: Series[pd.Timestamp] = pa.Field(nullable=False)

    class Config:
        strict = "filter"  # columnas extra se descartan, no rompen
        coerce = True

    # TODO(israel): agrega aqui las validaciones cruzadas que el sistema necesite.
    # Ejemplos que valen la pena y que ya sabes justificar:
    #   - un saldo mayor a cero exige dias_atraso coherente con la fecha de corte
    #   - la combinacion cve_entidad + cve_municipio debe existir en el catalogo INEGI
    # Se declaran con @pa.dataframe_check.


def validar(df: pd.DataFrame, modelo: type[pa.DataFrameModel] = CarteraCruda) -> pd.DataFrame:
    """Valida `df` contra `modelo`. Devuelve el DataFrame validado o levanta ErrorDeContrato.

    `lazy=True` recoge TODAS las violaciones antes de fallar, en vez de detenerse en la
    primera. Eso importa: un archivo con 400 filas malas se corrige una vez, no 400 veces.
    """
    try:
        return modelo.validate(df, lazy=True)
    except pa.errors.SchemaErrors as exc:
        n = len(exc.failure_cases)
        raise ErrorDeContrato(
            f"La cartera no cumple el contrato {modelo.__name__}: {n} violaciones.",
            fallas=exc.failure_cases,
        ) from exc
