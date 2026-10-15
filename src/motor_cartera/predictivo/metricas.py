"""Las metricas de predictive-training/public-v1, en NumPy y con pesos.

Los pesos son los de una muestra bootstrap (cuantas veces salio la cuenta de cada fila); sin pesos,
cada fila vale uno. Ninguna metrica usa la exactitud (accuracy): con prevalencias bajas, un modelo
que siempre dice que no tiene una exactitud alta y no sirve.

Las definiciones exactas (empates, bins, el orden de las filas) estan en la politica y en
docs/modelos_predictivos.md. Este modulo no sabe de la base.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from motor_cartera.predictivo.glm import intercepto_con_offset, platt

EPSILON_LOG_LOSS = 1e-15


def _pesos(y: np.ndarray, w: np.ndarray | None) -> np.ndarray:
    return np.ones(len(y), dtype=np.float64) if w is None else np.asarray(w, dtype=np.float64)


def roc_auc(y: np.ndarray, p: np.ndarray, w: np.ndarray | None = None) -> float | None:
    """El area bajo la curva ROC: la probabilidad de que un positivo al azar tenga una prediccion
    mayor que un negativo al azar, con los empates a la mitad. None si falta una de las clases."""
    w = _pesos(y, w)
    positivos = float(np.sum(w * y))
    negativos = float(np.sum(w * (1 - y)))
    if positivos == 0 or negativos == 0:
        return None
    orden = np.argsort(p, kind="mergesort")
    p_o, y_o, w_o = p[orden], y[orden], w[orden]
    valores, inicio = np.unique(p_o, return_index=True)
    pos_g = np.add.reduceat(w_o * y_o, inicio)
    neg_g = np.add.reduceat(w_o * (1 - y_o), inicio)
    neg_debajo = np.concatenate([[0.0], np.cumsum(neg_g)[:-1]])
    return float(np.sum(pos_g * (neg_debajo + 0.5 * neg_g)) / (positivos * negativos))


def pr_auc(y: np.ndarray, p: np.ndarray, w: np.ndarray | None = None) -> float | None:
    """La precision promedio (el area bajo precision-recall de scikit-learn): la suma, sobre cada
    umbral de mayor a menor, del aumento del recall por la precision en ese umbral."""
    w = _pesos(y, w)
    positivos = float(np.sum(w * y))
    if positivos == 0:
        return None
    orden = np.argsort(-p, kind="mergesort")
    p_o, y_o, w_o = p[orden], y[orden], w[orden]
    _, inicio = np.unique(-p_o, return_index=True)
    tp = np.cumsum(np.add.reduceat(w_o * y_o, inicio))
    fp = np.cumsum(np.add.reduceat(w_o * (1 - y_o), inicio))
    precision = tp / np.maximum(tp + fp, 1e-300)
    recall = tp / positivos
    return float(np.sum(np.diff(np.concatenate([[0.0], recall])) * precision))


def log_loss(y: np.ndarray, p: np.ndarray, w: np.ndarray | None = None) -> float:
    w = _pesos(y, w)
    q = np.clip(p, EPSILON_LOG_LOSS, 1 - EPSILON_LOG_LOSS)
    return float(-np.sum(w * (y * np.log(q) + (1 - y) * np.log1p(-q))) / np.sum(w))


def brier(y: np.ndarray, p: np.ndarray, w: np.ndarray | None = None) -> float:
    w = _pesos(y, w)
    return float(np.sum(w * (p - y) ** 2) / np.sum(w))


def _orden_descendente(p: np.ndarray) -> np.ndarray:
    """De mayor a menor probabilidad; los empates, en el orden de las filas (el de la cuenta)."""
    return np.argsort(-p, kind="mergesort")


def lift_y_captura(
    y: np.ndarray, p: np.ndarray, fraccion: float, w: np.ndarray | None = None
) -> tuple[float | None, float | None]:
    """(lift, captura) de las primeras ceil(fraccion * n) filas por probabilidad. Con pesos, las
    primeras hasta acumular esa fraccion del peso."""
    w = _pesos(y, w)
    total = float(np.sum(w))
    positivos = float(np.sum(w * y))
    if positivos == 0 or total == 0:
        return None, None
    orden = _orden_descendente(p)
    acumulado = np.cumsum(w[orden])
    if np.all(w == 1):
        k = math.ceil(fraccion * len(y))
    else:
        k = int(np.searchsorted(acumulado, fraccion * total, side="left")) + 1
    arriba = orden[:k]
    peso_arriba = float(np.sum(w[arriba]))
    positivos_arriba = float(np.sum(w[arriba] * y[arriba]))
    captura = positivos_arriba / positivos
    lift = (positivos_arriba / peso_arriba) / (positivos / total)
    return lift, captura


def bins_de_poblacion(p: np.ndarray, bins: int) -> list[np.ndarray]:
    """Los indices de las filas en `bins` grupos de poblacion casi igual, por probabilidad de menor
    a mayor (empates en el orden de las filas)."""
    orden = np.argsort(p, kind="mergesort")
    return [b for b in np.array_split(orden, bins) if len(b)]


def tabla_calibracion(y: np.ndarray, p: np.ndarray, bins: int) -> list[dict]:
    return [
        {
            "bin": i + 1,
            "n": len(b),
            "prediccion_media": float(np.mean(p[b])),
            "frecuencia_observada": float(np.mean(y[b])),
            "prediccion_minima": float(np.min(p[b])),
            "prediccion_maxima": float(np.max(p[b])),
        }
        for i, b in enumerate(bins_de_poblacion(p, bins))
    ]


def ece(y: np.ndarray, p: np.ndarray, bins: int) -> float:
    n = len(y)
    grupos = bins_de_poblacion(p, bins)
    return float(sum(len(b) / n * abs(float(np.mean(p[b] - y[b]))) for b in grupos))


def calibracion_intercepto_pendiente(
    y: np.ndarray, p: np.ndarray
) -> tuple[float | None, float | None]:
    """(intercepto con pendiente 1, pendiente) de logit(y) contra logit(p)."""
    if y.min() == y.max():
        return None, None
    logit = np.log(np.clip(p, EPSILON_LOG_LOSS, 1 - EPSILON_LOG_LOSS)) - np.log1p(
        -np.clip(p, EPSILON_LOG_LOSS, 1 - EPSILON_LOG_LOSS)
    )
    _, pendiente = platt(logit, y)
    return intercepto_con_offset(logit, y), pendiente


def clasificacion(
    y: np.ndarray, p: np.ndarray, *, bins: int, fracciones: tuple[float, ...]
) -> dict:
    """Las metricas de un clasificador sobre unas filas, sin pesos."""
    resultado = {
        "n": int(len(y)),
        "prevalencia": float(np.mean(y)) if len(y) else None,
        "roc_auc": roc_auc(y, p),
        "pr_auc": pr_auc(y, p),
        "log_loss": log_loss(y, p),
        "brier": brier(y, p),
        "ece": ece(y, p, bins),
    }
    intercepto, pendiente = calibracion_intercepto_pendiente(y, p)
    resultado["calibracion_intercepto"] = intercepto
    resultado["calibracion_pendiente"] = pendiente
    for f in fracciones:
        lift, captura = lift_y_captura(y, p, f)
        etiqueta = f"{round(f * 100)}"
        resultado[f"lift_{etiqueta}"] = lift
        resultado[f"captura_{etiqueta}"] = captura
    return resultado


def mae(y: np.ndarray, mu: np.ndarray, w: np.ndarray | None = None) -> float:
    w = _pesos(y, w)
    return float(np.sum(w * np.abs(y - mu)) / np.sum(w))


def monto(y: np.ndarray, mu: np.ndarray, *, bins: int) -> dict:
    """Las metricas del monto esperado sobre unas filas: MAE, RMSE, medias, sumas, la razon de las
    sumas (None si lo observado suma cero) y por decil de prediccion."""
    suma_real, suma_predicha = float(np.sum(y)), float(np.sum(mu))
    deciles = []
    for i, b in enumerate(bins_de_poblacion(mu, bins)):
        real, predicho = float(np.sum(y[b])), float(np.sum(mu[b]))
        deciles.append(
            {
                "decil": i + 1,
                "n": len(b),
                "real_medio": real / len(b),
                "predicho_medio": predicho / len(b),
                "suma_real": real,
                "suma_predicha": predicho,
                "razon": None if real == 0 else predicho / real,
            }
        )
    return {
        "n": int(len(y)),
        "mae": mae(y, mu),
        "rmse": float(np.sqrt(np.mean((y - mu) ** 2))),
        "real_medio": float(np.mean(y)),
        "predicho_medio": float(np.mean(mu)),
        "suma_real": suma_real,
        "suma_predicha": suma_predicha,
        "razon_sumas": None if suma_real == 0 else suma_predicha / suma_real,
        "deciles": deciles,
    }


# --- el bootstrap ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Intervalo:
    estimado: float | None
    inferior: float | None
    superior: float | None


def bootstrap(
    cuentas: np.ndarray,
    estadisticos: dict[str, callable],
    *,
    repeticiones: int,
    semilla: int,
    nivel: float,
) -> dict[str, Intervalo]:
    """Intervalos de percentil por bootstrap de cuentas: cada repeticion saca, con reemplazo, tantas
    cuentas como hay, y cada fila pesa cuantas veces salio su cuenta. `cuentas` es el indice de la
    cuenta de cada fila; cada estadistico recibe los pesos (None en el estimado)."""
    rng = np.random.default_rng(semilla)
    unicas, de_fila = np.unique(cuentas, return_inverse=True)
    valores: dict[str, list[float]] = {k: [] for k in estadisticos}
    for _ in range(repeticiones):
        conteo = np.bincount(rng.integers(0, len(unicas), len(unicas)), minlength=len(unicas))
        pesos = conteo[de_fila].astype(np.float64)
        for nombre, funcion in estadisticos.items():
            valor = funcion(pesos)
            if valor is not None and np.isfinite(valor):
                valores[nombre].append(float(valor))
    cola = (1 - nivel) / 2
    resultado = {}
    for nombre, funcion in estadisticos.items():
        muestra = np.array(valores[nombre])
        resultado[nombre] = Intervalo(
            funcion(None),
            float(np.quantile(muestra, cola)) if len(muestra) else None,
            float(np.quantile(muestra, 1 - cola)) if len(muestra) else None,
        )
    return resultado


# --- el PSI ---------------------------------------------------------------------------------------


def bordes(valores: np.ndarray, bins: int) -> list[float]:
    """Los bordes interiores de `bins` bins de poblacion casi igual de unos valores de TRAIN (sus
    cuantiles), sin repetir: con muchos empates salen menos bins."""
    if len(valores) == 0:
        return []
    cuantiles = np.quantile(valores, np.arange(1, bins) / bins, method="linear")
    return sorted({float(c) for c in cuantiles})


def proporciones(valores: np.ndarray, bordes_: list[float]) -> np.ndarray:
    """La fraccion de los valores en cada bin: (-inf, b1], (b1, b2], ..., (bk, inf)."""
    if len(valores) == 0:
        return np.zeros(len(bordes_) + 1)
    indices = np.searchsorted(np.array(bordes_), valores, side="left")
    return np.bincount(indices, minlength=len(bordes_) + 1) / len(valores)


def psi(referencia: np.ndarray, actual: np.ndarray, epsilon: float) -> float:
    """Population Stability Index entre dos distribuciones sobre los mismos bins."""
    t = np.maximum(np.asarray(referencia, dtype=np.float64), epsilon)
    s = np.maximum(np.asarray(actual, dtype=np.float64), epsilon)
    return float(np.sum((s - t) * np.log(s / t)))
