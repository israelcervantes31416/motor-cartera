"""Los GLM de predictive/model-v1, en NumPy: regresion logistica con L2, GLM Tweedie con liga log y
la calibracion de Platt, por el metodo de Newton (IRLS) con busqueda de paso.

Los objetivos son los de scikit-learn, para que se puedan comparar (una prueba lo hace):

- logistica: C * sum_i log_loss(y_i, sigmoide(x_i w + b)) + ||w||^2 / 2;
- Tweedie (1 < p < 2), mu = exp(x w + b): sum_i devianza(y_i, mu_i) / (2 n) + alpha ||w||^2 / 2;

sin penalizar el intercepto. Con C = None la logistica no se penaliza (Platt y la pendiente de
calibracion).

Por que propios y no un estimador de scikit-learn: los datos de entrenamiento no caben como una
matriz densa en la memoria de una maquina chica (millones de filas por cerca de 80 columnas), y
Newton solo necesita, por iteracion, X' W X y X' g, que se acumulan bloque por bloque, en un orden
fijo: el resultado es determinista y la memoria es la de un bloque. Lo que se publica no depende de
este modulo: el artefacto JSON es independiente, y `artefacto.py` predice solo con el.

Una columna fija (constante en TRAIN) no se ajusta: su coeficiente es exactamente cero.

Este modulo no sabe de la base.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

import numpy as np

Bloques = Callable[[], Iterator[tuple[np.ndarray, np.ndarray]]]
"""Una funcion que, cada vez que se llama, recorre los datos en bloques de filas (X, y), siempre en
el mismo orden. X trae todas las columnas de la matriz de diseno, sin el intercepto."""

LIMITE_ETA = 700.0
"""exp(700) cabe en un float64; mas alla, el paso se rechaza."""


@dataclass(frozen=True)
class Ajuste:
    coeficientes: np.ndarray
    intercepto: float
    iteraciones: int
    convergio: bool
    objetivo: float


class NoConverge(ArithmeticError):
    """Newton no encontro un paso que bajara el objetivo."""


def sigmoide(x: np.ndarray) -> np.ndarray:
    """1 / (1 + exp(-x)), estable en los dos extremos."""
    x = np.asarray(x, dtype=np.float64)
    resultado = np.empty_like(x)
    positivos = x >= 0
    resultado[positivos] = 1.0 / (1.0 + np.exp(-x[positivos]))
    e = np.exp(x[~positivos])
    resultado[~positivos] = e / (1.0 + e)
    return resultado


def log1pexp(x: np.ndarray) -> np.ndarray:
    """log(1 + exp(x)), estable."""
    return np.logaddexp(0.0, x)


def bloques_de(x: np.ndarray, y: np.ndarray, filas: int = 65_536) -> Bloques:
    """Los bloques de una matriz densa: para datos que ya caben en memoria."""

    def recorrer() -> Iterator[tuple[np.ndarray, np.ndarray]]:
        for inicio in range(0, len(y), filas):
            yield x[inicio : inicio + filas], y[inicio : inicio + filas]

    return recorrer


# --- el Newton comun ------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Familia:
    """Lo que distingue un GLM de otro en Newton: la perdida, su gradiente y su curvatura respecto
    al predictor lineal de cada fila, y su escala."""

    perdida: Callable[[np.ndarray, np.ndarray], np.ndarray]
    gradiente: Callable[[np.ndarray, np.ndarray], np.ndarray]
    curvatura: Callable[[np.ndarray, np.ndarray], np.ndarray]


def _logistica() -> _Familia:
    return _Familia(
        perdida=lambda eta, y: log1pexp(eta) - y * eta,
        gradiente=lambda eta, y: sigmoide(eta) - y,
        curvatura=lambda eta, y: (p := sigmoide(eta)) * (1.0 - p),
    )


def _tweedie(potencia: float) -> _Familia:
    p = potencia

    def perdida(eta: np.ndarray, y: np.ndarray) -> np.ndarray:
        # La mitad de la devianza, sin el termino que no depende de mu.
        return -y * np.exp((1 - p) * eta) / (1 - p) + np.exp((2 - p) * eta) / (2 - p)

    def gradiente(eta: np.ndarray, y: np.ndarray) -> np.ndarray:
        return np.exp((2 - p) * eta) - y * np.exp((1 - p) * eta)

    def curvatura(eta: np.ndarray, y: np.ndarray) -> np.ndarray:
        return (2 - p) * np.exp((2 - p) * eta) - (1 - p) * y * np.exp((1 - p) * eta)

    return _Familia(perdida, gradiente, curvatura)


def _newton(
    familia: _Familia,
    bloques: Bloques,
    columnas: int,
    *,
    escala: float,
    penalizacion: float,
    libres: np.ndarray,
    intercepto_inicial: float,
    tolerancia: float,
    iteraciones_maximas: int,
) -> Ajuste:
    """Minimiza escala * sum_i perdida(eta_i, y_i) + penalizacion * ||w||^2 / 2 sobre las columnas
    `libres` y el intercepto, con eta = x w + b. Paso de Newton con busqueda de Armijo; termina
    cuando la reduccion que predice el paso (el decremento de Newton) es menor que la tolerancia
    relativa al objetivo."""
    indices = np.flatnonzero(libres)
    k = len(indices)
    theta = np.zeros(k + 1, dtype=np.float64)
    theta[k] = intercepto_inicial
    penal = np.full(k + 1, penalizacion, dtype=np.float64)
    penal[k] = 0.0

    def eta_de(x: np.ndarray, t: np.ndarray) -> np.ndarray:
        return x[:, indices] @ t[:k] + t[k]

    def objetivo(t: np.ndarray) -> float:
        total = 0.0
        for x, y in bloques():
            eta = eta_de(x, t)
            if not np.all(np.abs(eta) < LIMITE_ETA):
                return np.inf
            total += float(np.sum(familia.perdida(eta, y)))
        return escala * total + 0.5 * float(np.sum(penal * t * t))

    valor = objetivo(theta)
    if not np.isfinite(valor):
        raise NoConverge("El punto de partida no tiene un objetivo finito.")
    for iteracion in range(1, iteraciones_maximas + 1):
        hessiana = np.zeros((k + 1, k + 1), dtype=np.float64)
        gradiente = np.zeros(k + 1, dtype=np.float64)
        for x, y in bloques():
            xa = np.empty((len(y), k + 1), dtype=np.float64)
            xa[:, :k] = x[:, indices]
            xa[:, k] = 1.0
            eta = xa @ theta
            g = familia.gradiente(eta, y)
            h = familia.curvatura(eta, y)
            gradiente += xa.T @ g
            hessiana += (xa * h[:, None]).T @ xa
        gradiente = escala * gradiente + penal * theta
        hessiana = escala * hessiana + np.diag(penal)
        try:
            paso = -np.linalg.solve(hessiana, gradiente)
        except np.linalg.LinAlgError:
            paso = -np.linalg.lstsq(hessiana, gradiente, rcond=None)[0]
        decremento = -float(gradiente @ paso)
        if decremento / 2 <= tolerancia * max(1.0, abs(valor)):
            return _ajuste(theta, indices, columnas, iteracion - 1, True, valor)
        t = 1.0
        for _ in range(40):
            candidato = theta + t * paso
            nuevo = objetivo(candidato)
            if np.isfinite(nuevo) and nuevo <= valor - 1e-4 * t * decremento:
                break
            t /= 2
        else:
            # Ningun paso baja el objetivo: ya esta en el minimo, hasta la precision del float64.
            return _ajuste(theta, indices, columnas, iteracion - 1, True, valor)
        theta, valor = candidato, nuevo
    return _ajuste(theta, indices, columnas, iteraciones_maximas, False, valor)


def _ajuste(
    theta: np.ndarray, indices: np.ndarray, columnas: int, iteraciones: int, convergio: bool, valor
) -> Ajuste:
    coeficientes = np.zeros(columnas, dtype=np.float64)
    coeficientes[indices] = theta[:-1]
    return Ajuste(coeficientes, float(theta[-1]), iteraciones, convergio, float(valor))


# --- los modelos ----------------------------------------------------------------------------------


def ajustar_logistica(
    bloques: Bloques,
    columnas: int,
    *,
    c: float | None,
    libres: np.ndarray | None = None,
    tolerancia: float = 1e-12,
    iteraciones_maximas: int = 100,
) -> Ajuste:
    """La regresion logistica con L2 (C de scikit-learn), o sin penalizar con c=None."""
    positivos, total = 0.0, 0
    for _, y in bloques():
        positivos += float(np.sum(y))
        total += len(y)
    if total == 0:
        raise ValueError("No hay filas que ajustar.")
    prevalencia = min(max(positivos / total, 1e-12), 1 - 1e-12)
    libres = np.ones(columnas, dtype=bool) if libres is None else libres
    return _newton(
        _logistica(),
        bloques,
        columnas,
        escala=1.0 if c is None else c,
        penalizacion=0.0 if c is None else 1.0,
        libres=libres,
        intercepto_inicial=float(np.log(prevalencia / (1 - prevalencia))),
        tolerancia=tolerancia,
        iteraciones_maximas=iteraciones_maximas,
    )


def ajustar_tweedie(
    bloques: Bloques,
    columnas: int,
    *,
    alpha: float,
    potencia: float,
    libres: np.ndarray | None = None,
    tolerancia: float = 1e-12,
    iteraciones_maximas: int = 100,
) -> Ajuste:
    """El GLM Tweedie con liga log (alpha de scikit-learn), con 1 < potencia < 2."""
    if not 1 < potencia < 2:
        raise ValueError(f"La potencia de un Tweedie compuesto esta en (1, 2): {potencia}.")
    suma, total = 0.0, 0
    for _, y in bloques():
        if np.any(y < 0):
            raise ValueError("Un Tweedie no admite un target negativo.")
        suma += float(np.sum(y))
        total += len(y)
    if total == 0:
        raise ValueError("No hay filas que ajustar.")
    media = suma / total
    libres = np.ones(columnas, dtype=bool) if libres is None else libres
    return _newton(
        _tweedie(potencia),
        bloques,
        columnas,
        escala=1.0 / total,
        penalizacion=alpha,
        libres=libres,
        intercepto_inicial=float(np.log(media)) if media > 0 else -30.0,
        tolerancia=tolerancia,
        iteraciones_maximas=iteraciones_maximas,
    )


def predictor_lineal(x: np.ndarray, ajuste: Ajuste) -> np.ndarray:
    return x @ ajuste.coeficientes + ajuste.intercepto


def platt(eta: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """La calibracion de Platt: logit(p) = a + b * eta, por maxima verosimilitud y sin
    regularizar, donde eta es el logit de la probabilidad cruda. Devuelve (a, b)."""
    ajuste = ajustar_logistica(
        bloques_de(eta.reshape(-1, 1), y.astype(np.float64)), 1, c=None, tolerancia=1e-12
    )
    return ajuste.intercepto, float(ajuste.coeficientes[0])


def intercepto_con_offset(offset: np.ndarray, y: np.ndarray) -> float:
    """El a de logit(y) ~ a + offset, por maxima verosimilitud: la calibracion en lo grande."""
    a = 0.0
    for _ in range(100):
        p = sigmoide(offset + a)
        gradiente = float(np.sum(p - y))
        curvatura = float(np.sum(p * (1 - p)))
        if curvatura <= 0:
            break
        paso = gradiente / curvatura
        a -= paso
        if abs(paso) < 1e-12:
            break
    return a


def devianza_tweedie(y: np.ndarray, mu: np.ndarray, potencia: float) -> np.ndarray:
    """La devianza unitaria de un Tweedie con 1 < p < 2, por fila."""
    p = potencia
    mu = np.maximum(mu, 1e-300)
    return 2 * (
        np.power(y, 2 - p) / ((1 - p) * (2 - p))
        - y * np.power(mu, 1 - p) / (1 - p)
        + np.power(mu, 2 - p) / (2 - p)
    )
