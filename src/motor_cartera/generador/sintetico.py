"""Generador de cartera sintetica.

REGLA DURA DEL PROYECTO: ningun dato real entra a este repositorio. Todo lo que el
sistema procesa sale de aqui. Si algun dia necesitas mas realismo, lo que se ajusta
son las distribuciones, nunca el origen.
"""

from __future__ import annotations

import pandas as pd

from motor_cartera.config import config


def generar_cartera(n: int = 10_000, semilla: int | None = None) -> pd.DataFrame:
    """Genera `n` cuentas sinteticas que cumplen el contrato CarteraCruda.

    TODO(israel): implementalo. Lo que hace bueno a un generador no es que produzca
    filas validas, es que produzca filas *dificiles*:

      - saldos con cola larga, no uniformes (una lognormal se parece mas a la realidad)
      - dias_atraso agrupado en cubetas reales: 0, 1-30, 31-60, 61-90, 91+
      - concentracion geografica desigual, con INEGI como fuente de claves
      - y a proposito: un porcentaje configurable de filas INVALIDAS, para que las
        pruebas del contrato tengan de donde agarrarse

    Ese ultimo punto es el que hace que el contrato sirva de algo.
    """
    _ = semilla or config.semilla
    raise NotImplementedError("Pendiente: ver docstring.")


def generar_archivo(destino: str, n: int = 10_000, formato: str = "xlsx") -> None:
    """Escribe una cartera sintetica en disco, para probar la ingesta de punta a punta.

    TODO(israel): soporta xlsx, csv y zip. La gracia esta en el zip: que contenga varios
    archivos y que la ingesta tenga que decidir cual es el util.
    """
    raise NotImplementedError("Pendiente.")
