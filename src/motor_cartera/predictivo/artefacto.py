"""El artefacto de un componente: JSON canonico, su SHA-256, y la inferencia desde el JSON solo.

Un modelo publicado es JSON, nunca pickle, joblib ni cloudpickle: un pickle ejecuta codigo al
cargarse, cambia con las versiones de las bibliotecas y no se puede leer. El artefacto trae
exactamente lo necesario para predecir: la version de las features con su hash, el orden de las
columnas, la transformacion, la mediana, la media y la desviacion de cada numerica, el vocabulario
de cada categorica, los coeficientes, el intercepto, la liga, la calibracion y la precision. Sus
numeros tienen 12 cifras significativas. Su hash es el SHA-256 de su JSON canonico (llaves en
orden, sin espacios): cambiar un coeficiente cambia el hash.

La inferencia de aqui no usa el codigo de entrenamiento: recorre el artefacto feature por feature,
suma la contribucion de cada una al predictor lineal y aplica la liga y la calibracion. Una prueba
compara su prediccion con la del estimador de entrenamiento (y con scikit-learn); la contribucion de
cada feature es exactamente lo que suma, asi que la explicacion de una prediccion es aritmetica, no
una aproximacion.

Este modulo no sabe de la base.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import numpy as np

from motor_cartera.predictivo import preprocesamiento as pre
from motor_cartera.predictivo.features import (
    DESCONOCIDA,
    FALTANTE,
    SUFIJO_NULO,
    Transformacion,
    hash_features,
)
from motor_cartera.predictivo.features import VERSION_FEATURES as VERSION_DE_FEATURES
from motor_cartera.predictivo.glm import Ajuste, sigmoide
from motor_cartera.predictivo.politica import VERSION_ARTEFACTO

LOGISTICA = "LOGISTICA_L2"
TWEEDIE = "TWEEDIE_LOG"
RAW = "RAW"
PLATT = "PLATT"


def canonico(artefacto: dict) -> bytes:
    """El JSON canonico: llaves en orden, sin espacios, UTF-8, sin NaN ni infinitos."""
    return json.dumps(
        artefacto, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def hash_artefacto(artefacto: dict) -> str:
    return hashlib.sha256(canonico(artefacto)).hexdigest()


def construir(
    *,
    target: str,
    algoritmo: str,
    preprocesamiento: pre.Preprocesamiento,
    ajuste: Ajuste,
    hiperparametros: dict,
    calibracion: tuple[float, float] | None,
    cifras: int,
    decimales_probabilidad: int,
    decimales_monto: int,
) -> dict:
    """El artefacto de un componente, con sus numeros redondeados a `cifras` cifras significativas.
    `calibracion` es (a, b) de Platt, o None si queda la probabilidad cruda."""
    columnas = preprocesamiento.columnas
    if len(columnas) != len(ajuste.coeficientes):
        raise ValueError("Los coeficientes no son los de las columnas del preprocesamiento.")
    return {
        "formato": VERSION_ARTEFACTO,
        "target": target,
        "algoritmo": algoritmo,
        "liga": "logit" if algoritmo == LOGISTICA else "log",
        "version_features": VERSION_DE_FEATURES,
        "hash_features": hash_features(),
        "preprocesamiento": pre.definicion(preprocesamiento),
        "columnas": list(columnas),
        "coeficientes": [pre.redondear(float(c), cifras) for c in ajuste.coeficientes],
        "intercepto": pre.redondear(ajuste.intercepto, cifras),
        "hiperparametros": hiperparametros,
        "calibracion": (
            {"metodo": RAW}
            if calibracion is None
            else {
                "metodo": PLATT,
                "a": pre.redondear(calibracion[0], cifras),
                "b": pre.redondear(calibracion[1], cifras),
            }
        ),
        "precision": {
            "cifras_significativas": cifras,
            "decimales_probabilidad": decimales_probabilidad,
            "decimales_monto": decimales_monto,
        },
    }


# --- la inferencia desde el JSON ------------------------------------------------------------------


def _transformar(transformacion: str, x: np.ndarray) -> np.ndarray:
    if transformacion == Transformacion.LOG1P:
        return np.log1p(np.maximum(x, 0.0))
    if transformacion == Transformacion.SIGNED_LOG1P:
        return np.sign(x) * np.log1p(np.abs(x))
    if transformacion == Transformacion.NINGUNA:
        return np.asarray(x, dtype=np.float64)
    raise ValueError(f"Transformacion desconocida en el artefacto: {transformacion}.")


@dataclass(frozen=True)
class Prediccion:
    eta: np.ndarray
    """El predictor lineal: intercepto mas la suma de las contribuciones."""
    cruda: np.ndarray
    """La probabilidad cruda (sigmoide de eta) o el monto (exp de eta)."""
    publicada: np.ndarray
    """La probabilidad calibrada, si hay calibracion, o la cruda; en un monto, el monto."""


class Runtime:
    """La inferencia de un artefacto, leida solo de su JSON."""

    def __init__(self, artefacto: dict) -> None:
        if artefacto.get("formato") != VERSION_ARTEFACTO:
            formato = artefacto.get("formato")
            raise ValueError(f"Este runtime lee {VERSION_ARTEFACTO}, no {formato}.")
        self.artefacto = artefacto
        self.coeficiente = dict(zip(artefacto["columnas"], artefacto["coeficientes"], strict=True))
        self.numericas = artefacto["preprocesamiento"]["numericas"]
        self.categoricas = artefacto["preprocesamiento"]["categoricas"]
        self.intercepto = float(artefacto["intercepto"])
        self.liga = artefacto["liga"]

    def contribuciones(
        self, numericas: np.ndarray, categoricas: np.ndarray
    ) -> dict[str, np.ndarray]:
        """La contribucion de cada feature al predictor lineal, fila por fila: su coeficiente por su
        valor estandarizado mas el de su indicador de nulo; en una categorica, el coeficiente de su
        categoria. `numericas` (n x p, crudas, NaN es NULL) y `categoricas` (n x q, texto o None),
        en el orden del artefacto."""
        resultado: dict[str, np.ndarray] = {}
        for j, num in enumerate(self.numericas):
            crudo = np.asarray(numericas[:, j], dtype=np.float64)
            nulo = np.isnan(crudo)
            if num["constante"]:
                z = np.zeros(len(crudo))
            else:
                x = _transformar(num["transformacion"], crudo)
                x = np.where(nulo, num["mediana"], x)
                z = (x - num["media"]) / num["desviacion"]
            aporte = self.coeficiente[num["nombre"]] * z
            if num["indicador_nulo"]:
                aporte = aporte + self.coeficiente[num["nombre"] + SUFIJO_NULO] * nulo
            resultado[num["nombre"]] = aporte
        for k, cat in enumerate(self.categoricas):
            vocabulario = cat["vocabulario"]
            conocidas = set(vocabulario[:-2])
            coeficientes = {v: self.coeficiente[f"{cat['nombre']}={v}"] for v in vocabulario}
            resultado[cat["nombre"]] = np.array(
                [
                    coeficientes[FALTANTE if v is None else (v if v in conocidas else DESCONOCIDA)]
                    for v in categoricas[:, k]
                ],
                dtype=np.float64,
            )
        return resultado

    def predecir(self, numericas: np.ndarray, categoricas: np.ndarray) -> Prediccion:
        aportes = self.contribuciones(numericas, categoricas)
        eta = np.full(numericas.shape[0], self.intercepto, dtype=np.float64)
        for nombre in [n["nombre"] for n in self.numericas] + [
            c["nombre"] for c in self.categoricas
        ]:
            eta = eta + aportes[nombre]
        return self.desde_eta(eta)

    def desde_eta(self, eta: np.ndarray) -> Prediccion:
        if self.liga == "log":
            mu = np.exp(eta)
            return Prediccion(eta, mu, mu)
        cruda = sigmoide(eta)
        calibracion = self.artefacto["calibracion"]
        if calibracion["metodo"] == PLATT:
            return Prediccion(eta, cruda, sigmoide(calibracion["a"] + calibracion["b"] * eta))
        return Prediccion(eta, cruda, cruda)


# --- la cuantizacion ------------------------------------------------------------------------------


def cuantizar_probabilidad(p: np.ndarray, decimales: int) -> np.ndarray:
    """La probabilidad en unidades de 10^-decimales, como entero: round(p * 10^d), redondeo al par.
    Lo que se guarda y se hashea; nunca el float binario."""
    escala = 10**decimales
    q = np.rint(np.asarray(p, dtype=np.float64) * escala).astype(np.int64)
    if np.any(q < 0) or np.any(q > escala):
        raise ValueError("Una probabilidad cuantizada quedo fuera de [0, 1].")
    return q


def cuantizar_monto(mu: np.ndarray, decimales: int) -> np.ndarray:
    """El monto en unidades de 10^-decimales (centavos con 2), como entero; nunca negativo."""
    q = np.rint(np.asarray(mu, dtype=np.float64) * 10**decimales).astype(np.int64)
    if np.any(q < 0):
        raise ValueError("Un monto cuantizado quedo negativo.")
    return q


def texto_decimal(entero: int, decimales: int) -> str:
    """Un entero en unidades de 10^-decimales como texto decimal exacto: 12345678 con 8 decimales
    es 0.12345678."""
    signo = "-" if entero < 0 else ""
    entero = abs(int(entero))
    escala = 10**decimales
    return f"{signo}{entero // escala}.{entero % escala:0{decimales}d}"
