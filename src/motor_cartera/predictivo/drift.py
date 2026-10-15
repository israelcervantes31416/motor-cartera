"""El drift de un scoring contra la referencia de TRAIN de su modelo: PSI por feature y por salida,
y el cambio en la fraccion de NULL de cada numerica.

El drift describe: no cambia, no recalibra ni reentrena el modelo, y no bloquea un scoring. Los
umbrales de severidad (PSI BAJO < 0.10 <= MODERADO <= 0.25 < ALTO; faltantes |diferencia| BAJO <
0.05 <= MODERADO <= 0.20 < ALTO) son heuristicas operacionales de la politica, no leyes
estadisticas. Todo es agregado: ningun bin nombra una cuenta.

Este modulo no sabe de la base.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np

from motor_cartera.analytics.catalogo import canonico
from motor_cartera.predictivo import metricas
from motor_cartera.predictivo import preprocesamiento as pre_
from motor_cartera.predictivo.features import DESCONOCIDA, FALTANTE, NUMERICAS
from motor_cartera.predictivo.politica import (
    PoliticaEntrenamiento,
    severidad_faltantes,
    severidad_psi,
)

DECIMALES_PSI = 6
DECIMALES_FRACCION = 6


@dataclass(frozen=True)
class Fila:
    """Una fila de DriftPredictivo."""

    tipo: str
    nombre: str
    clase: str
    psi: float
    severidad: str
    faltantes_train: float | None
    faltantes_scoring: float | None
    severidad_faltantes: str | None
    bins: dict

    def canonica(self) -> dict:
        return {
            "tipo": self.tipo,
            "nombre": self.nombre,
            "clase": self.clase,
            "psi": f"{self.psi:.{DECIMALES_PSI}f}",
            "severidad": self.severidad,
            "faltantes_train": _texto(self.faltantes_train),
            "faltantes_scoring": _texto(self.faltantes_scoring),
            "severidad_faltantes": self.severidad_faltantes,
            "bins": self.bins,
        }


def _texto(valor: float | None) -> str | None:
    return None if valor is None else f"{valor:.{DECIMALES_FRACCION}f}"


def _psi(referencia: list[float], actual: np.ndarray, politica: PoliticaEntrenamiento) -> float:
    return round(metricas.psi(np.array(referencia), actual, politica.epsilon_psi), DECIMALES_PSI)


def calcular(
    referencia: dict,
    numericas: np.ndarray,
    categoricas: np.ndarray,
    salidas: dict[str, np.ndarray],
    politica: PoliticaEntrenamiento,
) -> list[Fila]:
    """Las filas de drift de un scoring: `numericas` (n x p, crudas, NaN es NULL) y `categoricas`
    (n x q, texto o None) en el orden de las features, y lo publicado de cada salida."""
    filas: list[Fila] = []
    por_nombre = {f["nombre"]: f for f in referencia["features"]}
    for j, feature in enumerate(NUMERICAS):
        ref = por_nombre[feature.nombre]
        valores = pre_.transformar(ref["transformacion"], numericas[:, j])
        nulos = np.isnan(valores)
        actuales = metricas.proporciones(valores[~nulos], ref["bordes"])
        psi = _psi(ref["proporciones"], actuales, politica)
        faltantes = round(float(np.mean(nulos)) if len(nulos) else 0.0, DECIMALES_FRACCION)
        delta = faltantes - ref["faltantes"]
        filas.append(
            Fila(
                tipo="FEATURE",
                nombre=feature.nombre,
                clase="NUMERICA",
                psi=psi,
                severidad=severidad_psi(psi, politica),
                faltantes_train=ref["faltantes"],
                faltantes_scoring=faltantes,
                severidad_faltantes=severidad_faltantes(delta, politica),
                bins={
                    "bordes": ref["bordes"],
                    "proporciones_train": ref["proporciones"],
                    "proporciones_scoring": [round(float(x), 10) for x in actuales],
                },
            )
        )
    for k, nombre in enumerate(
        f["nombre"] for f in referencia["features"] if f["clase"] == "CATEGORICA"
    ):
        ref = por_nombre[nombre]
        vocabulario = ref["vocabulario"]
        posicion = {v: i for i, v in enumerate(vocabulario[:-2])}
        faltante, desconocida = vocabulario.index(FALTANTE), vocabulario.index(DESCONOCIDA)
        indices = np.fromiter(
            (faltante if v is None else posicion.get(v, desconocida) for v in categoricas[:, k]),
            dtype=np.int64,
            count=categoricas.shape[0],
        )
        actuales = (
            np.bincount(indices, minlength=len(vocabulario)) / len(indices)
            if len(indices)
            else np.zeros(len(vocabulario))
        )
        psi = _psi(ref["proporciones"], actuales, politica)
        filas.append(
            Fila(
                tipo="FEATURE",
                nombre=nombre,
                clase="CATEGORICA",
                psi=psi,
                severidad=severidad_psi(psi, politica),
                faltantes_train=None,
                faltantes_scoring=None,
                severidad_faltantes=None,
                bins={
                    "vocabulario": vocabulario,
                    "proporciones_train": ref["proporciones"],
                    "proporciones_scoring": [round(float(x), 10) for x in actuales],
                },
            )
        )
    for salida in referencia["salidas"]:
        valores = salidas[salida["nombre"]]
        actuales = metricas.proporciones(valores, salida["bordes"])
        psi = _psi(salida["proporciones"], actuales, politica)
        filas.append(
            Fila(
                tipo="SCORE",
                nombre=salida["nombre"],
                clase=salida["clase"],
                psi=psi,
                severidad=severidad_psi(psi, politica),
                faltantes_train=None,
                faltantes_scoring=None,
                severidad_faltantes=None,
                bins={
                    "bordes": salida["bordes"],
                    "proporciones_train": salida["proporciones"],
                    "proporciones_scoring": [round(float(x), 10) for x in actuales],
                    "media_train": salida["media"],
                    "media_scoring": round(float(np.mean(valores)), 8) if len(valores) else None,
                },
            )
        )
    return filas


def hash_drift(filas: list[Fila]) -> str:
    """SHA-256 de las filas de drift en su forma canonica, por (tipo, nombre)."""
    ordenadas = sorted(filas, key=lambda f: (f.tipo, f.nombre))
    return hashlib.sha256(canonico([f.canonica() for f in ordenadas])).hexdigest()


def resumen(filas: list[Fila]) -> dict:
    """Cuantas features y salidas quedan en cada severidad, y las de mayor PSI."""
    resultado: dict = {}
    for tipo in ("FEATURE", "SCORE"):
        del_tipo = [f for f in filas if f.tipo == tipo]
        resultado[tipo.lower()] = {
            s: sum(1 for f in del_tipo if f.severidad == s) for s in ("BAJO", "MODERADO", "ALTO")
        }
    resultado["faltantes"] = {
        s: sum(1 for f in filas if f.severidad_faltantes == s) for s in ("BAJO", "MODERADO", "ALTO")
    }
    resultado["mayor_psi"] = [
        {"tipo": f.tipo, "nombre": f.nombre, "psi": f.psi, "severidad": f.severidad}
        for f in sorted(filas, key=lambda f: (-f.psi, f.tipo, f.nombre))[:5]
    ]
    resultado["aviso"] = (
        "El drift describe cuanto se parece lo puntuado a TRAIN. No cambia ni reentrena el modelo; "
        "sus umbrales son heuristicas operacionales."
    )
    return resultado
