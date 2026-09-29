"""Generador de cartera sintetica.

REGLA DURA DEL PROYECTO: ningun dato real entra a este repositorio. Todo lo que el
sistema procesa sale de aqui. Si algun dia necesitas mas realismo, lo que se ajusta
son las distribuciones, nunca el origen.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from motor_cartera.config import config
from motor_cartera.contratos.cartera import CANALES, PRODUCTOS
from motor_cartera.segmentacion import TRAMOS_ATRASO

ENTIDADES: dict[str, tuple[range, float]] = {
    "21": (range(1, 218), 0.70),  # Puebla: 217 municipios
    "29": (range(1, 61), 0.08),  # Tlaxcala: 60
    "30": (range(1, 213), 0.08),  # Veracruz: 212
    "15": (range(1, 126), 0.08),  # Estado de Mexico: 125
    "09": (range(2, 18), 0.06),  # Ciudad de Mexico: 16 alcaldias, claves 002 a 017
}
"""Claves del Marco Geoestadistico del INEGI, que es publico: (claves municipales, peso).

Sirven para que las claves sean reales y la cartera se concentre como una de verdad,
casi toda en una entidad. El proyecto todavia no valida contra el catalogo completo.
"""

PESO_PRODUCTO = {"CONSUMO": 0.45, "TARJETA": 0.30, "NOMINA": 0.15, "AUTOMOTRIZ": 0.10}
MEDIANA_SALDO = {"CONSUMO": 15_000, "TARJETA": 12_000, "NOMINA": 20_000, "AUTOMOTRIZ": 90_000}
PESO_TRAMO = {"0": 0.10, "1-30": 0.30, "31-60": 0.20, "61-90": 0.15, "91+": 0.25}
TOPE_ATRASO = 720
"""El tramo 91+ se reparte hasta dos anios de atraso; el contrato admite hasta 3650 dias."""

# El canal depende del tramo: la gestion escala con el atraso, de lo digital al campo.
CANAL_POR_TRAMO = {
    "0": {"DIGITAL": 0.60, "TELEFONICA": 0.35, "CAMPO": 0.05},
    "1-30": {"DIGITAL": 0.45, "TELEFONICA": 0.45, "CAMPO": 0.10},
    "31-60": {"DIGITAL": 0.25, "TELEFONICA": 0.55, "CAMPO": 0.20},
    "61-90": {"DIGITAL": 0.10, "TELEFONICA": 0.50, "CAMPO": 0.40},
    "91+": {"DIGITAL": 0.05, "TELEFONICA": 0.35, "CAMPO": 0.60},
}

# Encabezados como los escribiria una persona en Excel: acentos, mayusculas, espacios.
ENCABEZADOS_XLSX = {
    "cliente_unico": "Cliente Único",
    "saldo_total": "Saldo Total",
    "dias_atraso": "Días de Atraso",
    "producto": "Producto",
    "canal": "Canal",
    "cve_entidad": "Cve Entidad",
    "cve_municipio": "Cve Municipio",
    "fecha_corte": "Fecha Corte",
}

LEEME = "Cartera sintetica generada por motor-cartera. Ningun dato corresponde a una persona."


def generar_cartera(
    n: int = 10_000, semilla: int | None = None, fecha_corte: date | None = None
) -> pd.DataFrame:
    """Genera `n` cuentas sinteticas que cumplen el contrato CarteraCruda.

    Lo que hace bueno a un generador no es que produzca filas validas, es que produzca
    filas *dificiles*:

      - saldos con cola larga (lognormal, con mediana distinta por producto)
      - dias de atraso agrupados en tramos reales: 0, 1-30, 31-60, 61-90, 91+
      - concentracion geografica desigual, con claves INEGI reales
      - un canal que depende del tramo, como en la operacion

    Las filas invalidas a proposito las agrega `contaminar`. La fecha de corte es la
    misma para todas las cuentas, porque una cartera es la foto de un dia; por omision, hoy.
    """
    rng = np.random.default_rng(config.semilla if semilla is None else semilla)

    i_producto = rng.choice(len(PRODUCTOS), size=n, p=_pesos(PESO_PRODUCTO, PRODUCTOS))
    medianas = np.array([MEDIANA_SALDO[p] for p in PRODUCTOS])[i_producto]
    saldo = np.round(medianas * rng.lognormal(mean=0.0, sigma=1.0, size=n), 2)

    etiquetas = [etiqueta for etiqueta, _, _ in TRAMOS_ATRASO]
    i_tramo = rng.choice(len(TRAMOS_ATRASO), size=n, p=_pesos(PESO_TRAMO, etiquetas))
    dias = np.zeros(n, dtype=np.int64)
    canal = np.empty(n, dtype=object)
    for j, (etiqueta, desde, hasta) in enumerate(TRAMOS_ATRASO):
        en_tramo = i_tramo == j
        cuantas = int(en_tramo.sum())
        tope = TOPE_ATRASO if hasta is None else hasta
        dias[en_tramo] = rng.integers(desde, tope + 1, size=cuantas)
        pesos_canal = _pesos(CANAL_POR_TRAMO[etiqueta], CANALES)
        canal[en_tramo] = rng.choice(CANALES, size=cuantas, p=pesos_canal)

    claves = list(ENTIDADES)
    i_entidad = rng.choice(len(claves), size=n, p=[ENTIDADES[c][1] for c in claves])
    municipio = np.empty(n, dtype=object)
    for j, clave in enumerate(claves):
        en_entidad = i_entidad == j
        municipios = np.array(ENTIDADES[clave][0])
        # Pocos municipios concentran casi todo: pesos tipo Zipf sobre un orden al azar.
        rango = rng.permutation(len(municipios)) + 1
        peso = 1.0 / rango**1.1
        elegidos = rng.choice(municipios, size=int(en_entidad.sum()), p=peso / peso.sum())
        municipio[en_entidad] = [f"{m:03d}" for m in elegidos]

    identificadores = rng.choice(10**10, size=n, replace=False)
    return pd.DataFrame(
        {
            "cliente_unico": [f"CU{x:010d}" for x in identificadores],
            "saldo_total": saldo,
            "dias_atraso": dias,
            "producto": np.array(PRODUCTOS)[i_producto],
            "canal": canal.astype(str),
            "cve_entidad": np.array(claves)[i_entidad],
            "cve_municipio": municipio.astype(str),
            "fecha_corte": pd.Timestamp(fecha_corte or date.today()),
        }
    )


# Cada defecto es invalido siempre, no por casualidad: la prueba del contrato depende de eso.
DEFECTOS: tuple[tuple[str, Callable[[object], object]], ...] = (
    ("saldo_total", lambda saldo: -saldo - 1),
    ("saldo_total", lambda _: "N/D"),
    ("dias_atraso", lambda _: 9_999),
    ("producto", lambda _: "HIPOTECARIO"),
    ("canal", lambda _: "CARTA"),
    ("cve_municipio", lambda _: "7"),  # una clave "007" que Excel convirtio en el numero 7
    ("fecha_corte", lambda fecha: fecha.strftime("%d/%m/%Y")),  # como se escribe aqui; no es ISO
    ("cliente_unico", lambda cliente: cliente.lower()),
)


def contaminar(
    cartera: pd.DataFrame, tasa: float, semilla: int | None = None
) -> tuple[pd.DataFrame, list[int]]:
    """Devuelve una copia con una fraccion `tasa` de filas invalidas, y cuales son.

    Ese porcentaje de filas malas a proposito es lo que hace que el contrato sirva de
    algo: las pruebas necesitan de donde agarrarse. Cada fila contaminada recibe un solo
    defecto, en rotacion. El ultimo de la rotacion es un duplicado: copia el cliente de
    otra fila contaminada, para que todo lo que el contrato rechace este en la lista.

    Las filas se devuelven como posiciones, de menor a mayor.
    """
    if not 0 <= tasa <= 1:
        raise ValueError(f"La tasa de filas invalidas debe estar entre 0 y 1: {tasa}")
    rng = np.random.default_rng([config.semilla if semilla is None else semilla, 1])

    filas = sorted(rng.choice(len(cartera), size=round(len(cartera) * tasa), replace=False))
    sucia = cartera.reset_index(drop=True).astype(object)
    for i, fila in enumerate(filas):
        turno = i % (len(DEFECTOS) + 1)
        if turno == len(DEFECTOS):
            sucia.at[fila, "cliente_unico"] = sucia.at[filas[i - 1], "cliente_unico"]
        else:
            campo, defecto = DEFECTOS[turno]
            sucia.at[fila, campo] = defecto(sucia.at[fila, campo])
    return sucia, [int(f) for f in filas]


def generar_archivo(
    destino: str | Path,
    n: int = 10_000,
    *,
    tasa_invalidas: float = 0.0,
    semilla: int | None = None,
    fecha_corte: date | None = None,
) -> Path:
    """Escribe una cartera sintetica en disco, para probar la ingesta de punta a punta.

    El formato sale de la extension de `destino`, y cada uno trae su dificultad:

      - csv: los nombres del contrato, sin mas.
      - xlsx: encabezados escritos a mano y una hoja LEEME antes de la de datos, para que
        la ingesta tenga que encontrar la hoja util.
      - zip: la cartera junto a un LEEME.txt y un catalogo de productos que tambien es
        CSV, para que la ingesta tenga que decidir cual archivo es la cartera.
    """
    ruta = Path(destino)
    formato = ruta.suffix.lower()
    escritores = {".csv": _como_csv, ".xlsx": _como_xlsx, ".zip": _como_zip}
    if formato not in escritores:
        raise ValueError(f"Formato no soportado: {ruta.name!r}. Usa .csv, .xlsx o .zip.")

    cartera = generar_cartera(n, semilla=semilla, fecha_corte=fecha_corte)
    if tasa_invalidas:
        cartera, _ = contaminar(cartera, tasa_invalidas, semilla=semilla)

    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(escritores[formato](cartera))
    return ruta


def _pesos(pesos: dict[str, float], orden: tuple[str, ...] | list[str]) -> list[float]:
    """Probabilidades en el orden de `orden`. Si falta una categoria, KeyError: mejor ruidoso."""
    return [pesos[clave] for clave in orden]


def _como_csv(cartera: pd.DataFrame) -> bytes:
    return cartera.to_csv(index=False).encode("utf-8")


def _como_xlsx(cartera: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as escritor:
        pd.DataFrame({"Nota": [LEEME]}).to_excel(escritor, sheet_name="LEEME", index=False)
        datos = cartera.rename(columns=ENCABEZADOS_XLSX)
        datos.to_excel(escritor, sheet_name="cartera", index=False)
    return buf.getvalue()


def _como_zip(cartera: pd.DataFrame) -> bytes:
    catalogo = "producto,descripcion\n" + "".join(f"{p},Producto {p.lower()}\n" for p in PRODUCTOS)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as paquete:
        paquete.writestr("LEEME.txt", LEEME)
        paquete.writestr("catalogo_productos.csv", catalogo)
        paquete.writestr("cartera.csv", _como_csv(cartera))
    return buf.getvalue()
