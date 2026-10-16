"""predictive-training/public-v1: como se entrena, se elige, se calibra, se evalua y se publica un
modelo, como datos con su hash. Todo se fijo antes de entrenar y no se ajusta despues de ver TEST.

- El split es temporal y lo declara el manifiesto, ancla por ancla: TRAIN, VALIDATION, TEST o PURGE.
  Nunca al azar, nunca "los ultimos N cortes" decididos al ejecutar. TRAIN es anterior a
  VALIDATION y VALIDATION a TEST, con una purga: el final de la ventana del label mas largo de un
  bloque (T + 30) es anterior a la primera ancla del bloque siguiente. Sin ella, los labels de TRAIN
  verian el periodo que se evalua.
- El walk-forward repite el procedimiento en tres folds sobre lo anterior a TEST: el ultimo es el
  split principal (TRAIN contra VALIDATION) y los otros dos retroceden un bloque de VALIDATION cada
  uno, con su propia purga. TEST no participa en ningun fold.
- Las clasificaciones son regresiones logisticas con L2 y el monto un GLM Tweedie con liga log, con
  rejillas pequenas y declaradas, sin rebalanceo de clases (ni pesos, ni SMOTE, ni muestreo): las
  probabilidades quedan en la prevalencia real. Se elige con VALIDATION; un empate razonable se
  resuelve hacia mas regularizacion.
- La calibracion de Platt se ajusta en VALIDATION y se adopta solo si mejora su Brier ahi.
- TEST evalua una vez, al final, con el artefacto publicado, contra el baseline constante de
  TRAIN. Las compuertas deciden si cada componente queda VALIDADO; el modelo se publica solo con los
  cuatro.

Este modulo no sabe de la base.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from motor_cartera.analytics.catalogo import canonico
from motor_cartera.predictivo.targets import HORIZONTE_MAXIMO

VERSION_POLITICA = "predictive-training/public-v1"
VERSION_MODELO = "predictive/model-v1"
VERSION_ARTEFACTO = "predictive/artifact-v1"


class Split:
    TRAIN = "TRAIN"
    VALIDATION = "VALIDATION"
    TEST = "TEST"
    PURGE = "PURGE"

    TODOS = (TRAIN, VALIDATION, TEST, PURGE)


@dataclass(frozen=True)
class PoliticaEntrenamiento:
    version: str
    semilla: int
    horizonte_purga_dias: int
    folds_walk_forward: int
    grid_c: tuple[float, ...]
    """Las C de la logistica, con la parametrizacion de scikit-learn: se minimiza
    C * sum(log loss) + ||w||^2 / 2, sin penalizar el intercepto."""
    tolerancia_empate_log_loss: float
    potencia_tweedie: float
    grid_alpha: tuple[float, ...]
    """Las alpha del Tweedie, con la de scikit-learn: se minimiza la devianza media / 2 +
    alpha * ||w||^2 / 2, sin penalizar el intercepto."""
    tolerancia_empate_mae: float
    """Relativa: dos MAE a menos de esta fraccion del menor son un empate."""
    mejora_minima_platt: float
    """Platt se adopta si el Brier de VALIDATION baja al menos esta fraccion."""
    iteraciones_maximas: int
    tolerancia_newton: float
    filas_por_bloque: int
    cifras_significativas: int
    decimales_probabilidad: int
    decimales_monto: int
    bins_calibracion: int
    fracciones_lift: tuple[float, ...]
    repeticiones_bootstrap: int
    nivel_bootstrap: float
    bins_psi: int
    epsilon_psi: float
    psi_moderado: float
    psi_alto: float
    faltantes_moderado: float
    faltantes_alto: float
    reglas: dict[str, str] = field(default_factory=dict)

    def definicion(self) -> dict:
        return {
            "version": self.version,
            "version_modelo": VERSION_MODELO,
            "version_artefacto": VERSION_ARTEFACTO,
            "semilla": self.semilla,
            "split": {
                "valores": list(Split.TODOS),
                "orden": "TRAIN < VALIDATION < TEST por feature_as_of; PURGE no se usa en el "
                "split principal.",
                "horizonte_purga_dias": self.horizonte_purga_dias,
                "purga": "max(T de un bloque) + horizonte_purga_dias < min(T del bloque "
                "siguiente), entre TRAIN y VALIDATION y entre VALIDATION y TEST.",
                "folds_walk_forward": self.folds_walk_forward,
            },
            "clasificacion": {
                "algoritmo": "LOGISTICA_L2",
                "solver": "Newton (IRLS) con busqueda de paso, por bloques de filas",
                "grid_c": list(self.grid_c),
                "seleccion": "menor log loss en VALIDATION",
                "tolerancia_empate_log_loss": self.tolerancia_empate_log_loss,
                "rebalanceo": "ninguno: ni class_weight, ni SMOTE, ni sobremuestreo, ni "
                "submuestreo",
            },
            "monto": {
                "algoritmo": "TWEEDIE_LOG",
                "potencia": self.potencia_tweedie,
                "grid_alpha": list(self.grid_alpha),
                "seleccion": "menor MAE en VALIDATION",
                "tolerancia_empate_mae_relativa": self.tolerancia_empate_mae,
            },
            "calibracion": {
                "candidatas": ["RAW", "PLATT"],
                "regla": "PLATT (logistica de un logit, sin regularizar, en VALIDATION) si su "
                "Brier de VALIDATION es menor que el de RAW por al menos "
                f"{self.mejora_minima_platt:.3%}; si no, RAW.",
            },
            "optimizacion": {
                "iteraciones_maximas": self.iteraciones_maximas,
                "tolerancia_newton": self.tolerancia_newton,
                "filas_por_bloque": self.filas_por_bloque,
            },
            "artefacto": {
                "cifras_significativas": self.cifras_significativas,
                "decimales_probabilidad": self.decimales_probabilidad,
                "decimales_monto": self.decimales_monto,
            },
            "metricas": {
                "bins_calibracion": self.bins_calibracion,
                "fracciones_lift": list(self.fracciones_lift),
            },
            "bootstrap": {
                "repeticiones": self.repeticiones_bootstrap,
                "nivel": self.nivel_bootstrap,
                "unidad": "cuenta (todas sus filas de TEST juntas)",
                "semilla": self.semilla,
            },
            "drift": {
                "bins": self.bins_psi,
                "epsilon": self.epsilon_psi,
                "psi_moderado": self.psi_moderado,
                "psi_alto": self.psi_alto,
                "faltantes_moderado": self.faltantes_moderado,
                "faltantes_alto": self.faltantes_alto,
            },
            "reglas": dict(sorted(self.reglas.items())),
        }


PUBLIC_V1 = PoliticaEntrenamiento(
    version=VERSION_POLITICA,
    semilla=2026,
    horizonte_purga_dias=HORIZONTE_MAXIMO,
    folds_walk_forward=3,
    grid_c=(0.1, 1.0, 10.0),
    tolerancia_empate_log_loss=1e-4,
    potencia_tweedie=1.5,
    grid_alpha=(0.0001, 0.001, 0.01),
    tolerancia_empate_mae=0.001,
    mejora_minima_platt=0.001,
    iteraciones_maximas=100,
    tolerancia_newton=1e-12,
    filas_por_bloque=65_536,
    cifras_significativas=12,
    decimales_probabilidad=8,
    decimales_monto=2,
    bins_calibracion=10,
    fracciones_lift=(0.10, 0.20),
    repeticiones_bootstrap=200,
    nivel_bootstrap=0.95,
    bins_psi=10,
    epsilon_psi=1e-4,
    psi_moderado=0.10,
    psi_alto=0.25,
    faltantes_moderado=0.05,
    faltantes_alto=0.20,
    reglas={
        "compuertas_clasificacion": "En TEST, con las probabilidades publicadas: log loss <= la "
        "del baseline, Brier <= el del baseline, ROC AUC > 0.5 y toda probabilidad finita en "
        "[0, 1]. Si no, el componente queda RECHAZADO.",
        "compuertas_monto": "En TEST: MAE <= el del baseline, y toda prediccion finita y >= 0.",
        "baseline": "Clasificacion: la prevalencia de TRAIN, constante. Monto: la media del "
        "target en TRAIN, constante. Nunca de VALIDATION ni de TEST.",
        "publicacion": "Todo o nada: el modelo queda PUBLICADA solo si sus cuatro componentes "
        "quedan VALIDADO; si alguno queda RECHAZADO, el modelo queda RECHAZADA con sus metricas, "
        "y nada se publica.",
        "test": "TEST solo evalua: no elige C ni alpha, no ajusta la calibracion, no elige "
        "features ni umbrales, y no cambia el generador.",
        "ece": "Error de calibracion esperado: la suma, sobre bins de poblacion casi igual "
        "ordenados por probabilidad, de |media predicha - frecuencia observada| por su fraccion.",
        "calibracion_intercepto_pendiente": "Pendiente: la de logit(y) ~ a + b * logit(p) por "
        "maxima verosimilitud. Intercepto: el de logit(y) ~ a + logit(p), con la pendiente fija "
        "en 1 (calibracion en lo grande).",
        "lift_y_captura": "Ordenadas por probabilidad, de mayor a menor (empates por el orden de "
        "la cuenta), las primeras ceil(f * n): captura = positivos entre ellas / positivos; lift = "
        "tasa entre ellas / prevalencia.",
        "cuantizacion": "Una probabilidad se guarda con 8 decimales (round(p * 1e8) / 1e8, "
        "redondeo al par) y el riesgo es 1 menos ella, exacto; un monto, en centavos.",
        "psi": "PSI = sum((s - t) * ln(s / t)) sobre los bins de TRAIN (deciles del valor "
        "transformado sin NULL de una numerica, el vocabulario de una categorica, deciles de cada "
        "probabilidad o del monto), con cada proporcion en al menos epsilon. Severidad: BAJO "
        "< 0.10 <= MODERADO <= 0.25 < ALTO. Son heuristicas operacionales, no leyes estadisticas.",
        "faltantes": "La fraccion de NULL de cada numerica en TRAIN y en el scoring; |diferencia| "
        "BAJO < 0.05 <= MODERADO <= 0.20 < ALTO.",
        "reentrenamiento": "Ningun drift cambia, recalibra ni reentrena un modelo publicado: "
        "cambiar el modelo es otro entrenamiento, con otra version.",
    },
)

POLITICAS: dict[str, PoliticaEntrenamiento] = {PUBLIC_V1.version: PUBLIC_V1}


def hash_politica(politica: PoliticaEntrenamiento = PUBLIC_V1) -> str:
    return hashlib.sha256(canonico(politica.definicion())).hexdigest()


def severidad_psi(psi: float, politica: PoliticaEntrenamiento = PUBLIC_V1) -> str:
    if psi < politica.psi_moderado:
        return "BAJO"
    if psi <= politica.psi_alto:
        return "MODERADO"
    return "ALTO"


def severidad_faltantes(delta: float, politica: PoliticaEntrenamiento = PUBLIC_V1) -> str:
    delta = abs(delta)
    if delta < politica.faltantes_moderado:
        return "BAJO"
    if delta <= politica.faltantes_alto:
        return "MODERADO"
    return "ALTO"
