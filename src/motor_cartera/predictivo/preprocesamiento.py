"""De las features crudas a la matriz de diseno de un GLM, con lo aprendido solo de TRAIN.

Cada numerica pasa por su transformacion declarada (NINGUNA, LOG1P o SIGNED_LOG1P), un NULL se
imputa con la mediana de TRAIN del valor transformado (su indicador, si lo tiene, vale 1), y se
estandariza con la media y la desviacion de TRAIN. Una categorica es one-hot con el vocabulario de
TRAIN, en orden canonico, mas `__MISSING__` y `__UNKNOWN__`. Ninguna estadistica sale de VALIDATION
ni de TEST: si una categoria nueva aparece despues, es `__UNKNOWN__`, no una columna nueva, y si
una numerica cambia de distribucion, la mediana sigue siendo la de TRAIN.

Una columna constante en TRAIN (desviacion cero, o un indicador o una categoria siempre igual) queda
en el diseno pero fija: su coeficiente es exactamente cero. Las estadisticas se redondean a las
cifras significativas del artefacto antes de construir el diseno de entrenamiento, para que el
modelo que se ajusta y el que se publica vean exactamente los mismos valores.

Este modulo no sabe de la base.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from motor_cartera.predictivo.features import (
    CATEGORICAS,
    DESCONOCIDA,
    FALTANTE,
    NUMERICAS,
    Transformacion,
)


def transformar(transformacion: Transformacion | str, x: np.ndarray) -> np.ndarray:
    """La transformacion declarada de una numerica; NaN (NULL) sigue siendo NaN."""
    x = np.asarray(x, dtype=np.float64)
    if transformacion == Transformacion.LOG1P:
        return np.log1p(np.maximum(x, 0.0))
    if transformacion == Transformacion.SIGNED_LOG1P:
        return np.sign(x) * np.log1p(np.abs(x))
    if transformacion == Transformacion.NINGUNA:
        return x.copy()
    raise ValueError(f"Transformacion desconocida: {transformacion}.")


def redondear(valor: float, cifras: int) -> float:
    """Un float con `cifras` cifras significativas: lo que guarda el artefacto."""
    if not np.isfinite(valor):
        raise ValueError(f"Un valor del artefacto tiene que ser finito: {valor}.")
    return float(f"{valor:.{cifras}g}") if valor != 0 else 0.0


@dataclass(frozen=True)
class Numerica:
    nombre: str
    transformacion: str
    mediana: float
    """La mediana de TRAIN del valor transformado, sin NULL; 0 si TRAIN no tiene ningun valor."""
    media: float
    desviacion: float
    constante: bool
    """Desviacion cero en TRAIN: su columna vale 0 y su coeficiente es 0."""
    indicador: bool
    indicador_constante: bool


@dataclass(frozen=True)
class Categorica:
    nombre: str
    vocabulario: tuple[str, ...]
    """Los valores de TRAIN en orden canonico, y despues __MISSING__ y __UNKNOWN__."""
    constantes: tuple[bool, ...]
    """Por cada valor del vocabulario, si su columna es constante en TRAIN."""


@dataclass(frozen=True)
class Preprocesamiento:
    numericas: tuple[Numerica, ...]
    categoricas: tuple[Categorica, ...]

    @property
    def columnas(self) -> tuple[str, ...]:
        nombres = [n.nombre for n in self.numericas]
        nombres += [f"{n.nombre}__nulo" for n in self.numericas if n.indicador]
        for c in self.categoricas:
            nombres += [f"{c.nombre}={valor}" for valor in c.vocabulario]
        return tuple(nombres)

    @property
    def libres(self) -> np.ndarray:
        """Las columnas que se ajustan: las que no son constantes en TRAIN."""
        fijas = [n.constante for n in self.numericas]
        fijas += [n.indicador_constante for n in self.numericas if n.indicador]
        for c in self.categoricas:
            fijas += list(c.constantes)
        return ~np.array(fijas, dtype=bool)

    def disenar(self, numericas: np.ndarray, categoricas: np.ndarray) -> np.ndarray:
        """La matriz de diseno de unas filas: numericas crudas (n x p, NaN es NULL, en el orden de
        NUMERICAS) y categoricas (n x q, objetos: un texto o None, en el orden de CATEGORICAS)."""
        return self.disenar_indices(numericas, self.indices(categoricas))

    def indices(self, categoricas: np.ndarray) -> np.ndarray:
        """n x q: el indice de cada valor en el vocabulario de su categorica."""
        resultado = np.empty((categoricas.shape[0], len(self.categoricas)), dtype=np.int64)
        for k, cat in enumerate(self.categoricas):
            resultado[:, k] = codificar(cat.vocabulario, categoricas[:, k])
        return resultado

    def disenar_indices(self, numericas: np.ndarray, indices: np.ndarray) -> np.ndarray:
        """La matriz de diseno con las categoricas ya como indices de su vocabulario."""
        n = numericas.shape[0]
        indicadores = [j for j, num in enumerate(self.numericas) if num.indicador]
        unos = sum(len(c.vocabulario) for c in self.categoricas)
        x = np.zeros((n, len(self.numericas) + len(indicadores) + unos), dtype=np.float64)
        for j, num in enumerate(self.numericas):
            if num.constante:
                continue
            valores = transformar(num.transformacion, numericas[:, j])
            valores = np.where(np.isnan(valores), num.mediana, valores)
            x[:, j] = (valores - num.media) / num.desviacion
        columna = len(self.numericas)
        for j in indicadores:
            x[:, columna] = np.isnan(numericas[:, j])
            columna += 1
        filas = np.arange(n)
        for k, cat in enumerate(self.categoricas):
            x[filas, columna + indices[:, k]] = 1.0
            columna += len(cat.vocabulario)
        return x


def codificar(vocabulario: tuple[str, ...], valores: np.ndarray) -> np.ndarray:
    """El indice de cada valor en el vocabulario: None es __MISSING__ y un valor que no esta, de
    TRAIN, es __UNKNOWN__."""
    posicion = {v: i for i, v in enumerate(vocabulario[:-2])}
    faltante, desconocida = vocabulario.index(FALTANTE), vocabulario.index(DESCONOCIDA)
    return np.fromiter(
        (faltante if v is None else posicion.get(v, desconocida) for v in valores),
        dtype=np.int64,
        count=len(valores),
    )


def ajustar(numericas: np.ndarray, categoricas: np.ndarray, *, cifras: int) -> Preprocesamiento:
    """El preprocesamiento aprendido de las filas de TRAIN, y solo de ellas."""
    if numericas.shape[1] != len(NUMERICAS) or categoricas.shape[1] != len(CATEGORICAS):
        raise ValueError("Las columnas no son las de predictive-features/public-v1.")
    conteos = [dict(Counter(categoricas[:, k].tolist())) for k in range(len(CATEGORICAS))]
    return ajustar_por_columnas(
        lambda j: numericas[:, j], conteos, filas=numericas.shape[0], cifras=cifras
    )


def ajustar_por_columnas(
    columna: Callable[[int], np.ndarray],
    conteos: list[dict[str | None, int]],
    *,
    filas: int,
    cifras: int,
) -> Preprocesamiento:
    """El preprocesamiento de TRAIN sin la matriz entera: `columna(j)` da los valores crudos de la
    numerica j en todas las filas de TRAIN, y `conteos[k]` cuantas filas tienen cada valor de la
    categorica k (None es NULL)."""
    if len(conteos) != len(CATEGORICAS):
        raise ValueError("Las columnas no son las de predictive-features/public-v1.")
    resultado = []
    for j, feature in enumerate(NUMERICAS):
        valores = transformar(feature.transformacion, columna(j))
        if len(valores) != filas:
            raise ValueError(f"La columna {feature.nombre} no tiene {filas} filas.")
        nulos = np.isnan(valores)
        conocidos = valores[~nulos]
        mediana = redondear(float(np.median(conocidos)), cifras) if len(conocidos) else 0.0
        imputados = np.where(nulos, mediana, valores)
        media = redondear(float(np.mean(imputados)), cifras)
        desviacion = float(np.std(imputados))
        # Constante si todos los valores son el mismo: la desviacion de valores identicos puede
        # no ser exactamente cero en float64.
        constante = not np.isfinite(desviacion) or bool(np.max(imputados) == np.min(imputados))
        desviacion = 1.0 if constante else redondear(desviacion, cifras)
        resultado.append(
            Numerica(
                nombre=feature.nombre,
                transformacion=feature.transformacion.value,
                mediana=mediana,
                media=0.0 if constante else media,
                desviacion=desviacion,
                constante=constante,
                indicador=feature.indicador_nulo,
                indicador_constante=bool(nulos.all() or not nulos.any()),
            )
        )
    cats = []
    for feature, cuantos_por_valor in zip(CATEGORICAS, conteos, strict=True):
        vistos = sorted(v for v in cuantos_por_valor if v is not None)
        vocabulario = (*vistos, FALTANTE, DESCONOCIDA)
        cuantos = [cuantos_por_valor.get(v, 0) for v in vistos]
        cuantos += [cuantos_por_valor.get(None, 0), 0]
        constantes = tuple(bool(c == 0 or c == filas) for c in cuantos)
        cats.append(Categorica(feature.nombre, vocabulario, constantes))
    return Preprocesamiento(tuple(resultado), tuple(cats))


def definicion(pre: Preprocesamiento) -> dict:
    """Lo que el artefacto guarda del preprocesamiento: todo lo necesario para repetirlo."""
    return {
        "numericas": [
            {
                "nombre": n.nombre,
                "transformacion": n.transformacion,
                "mediana": n.mediana,
                "media": n.media,
                "desviacion": n.desviacion,
                "constante": n.constante,
                "indicador_nulo": n.indicador,
                "indicador_constante": n.indicador_constante,
            }
            for n in pre.numericas
        ],
        "categoricas": [
            {
                "nombre": c.nombre,
                "vocabulario": list(c.vocabulario),
                "constantes": list(c.constantes),
            }
            for c in pre.categoricas
        ],
    }


def desde_definicion(datos: dict) -> Preprocesamiento:
    return Preprocesamiento(
        tuple(
            Numerica(
                nombre=n["nombre"],
                transformacion=n["transformacion"],
                mediana=float(n["mediana"]),
                media=float(n["media"]),
                desviacion=float(n["desviacion"]),
                constante=bool(n["constante"]),
                indicador=bool(n["indicador_nulo"]),
                indicador_constante=bool(n["indicador_constante"]),
            )
            for n in datos["numericas"]
        ),
        tuple(
            Categorica(c["nombre"], tuple(c["vocabulario"]), tuple(c["constantes"]))
            for c in datos["categoricas"]
        ),
    )
