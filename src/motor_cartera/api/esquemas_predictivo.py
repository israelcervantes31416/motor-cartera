"""Los esquemas de predictive/model-v1 en la API: las definiciones de las features y de los
targets, el manifiesto de un entrenamiento, los entrenamientos, las versiones del modelo con sus
componentes, los scorings, sus predicciones, su drift y la explicacion de una prediccion.

Una prediccion sale con el cuenta_id de su cuenta, su producto y su entidad; nunca con un
CLIENTE_UNICO, un nombre, una direccion, un telefono, una coordenada ni otro dato personal. Una
probabilidad es la de un resultado observado bajo la operacion historica: no el efecto de una
accion.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from motor_cartera.api.esquemas import Pagina, Paginacion
from motor_cartera.db.modelos import (
    EstadoEntrenamientoPredictivo,
    EstadoModeloPredictivo,
    EstadoScoringPredictivo,
    ModoScoringPredictivo,
)
from motor_cartera.predictivo.consultas import Orden, Sentido

AVISO_PREDICTIVO = (
    "predictive/model-v1 estima la probabilidad de resultados observados bajo la operacion "
    "historica, con lo que se conocia a known_at. No es un efecto causal, no recomienda una accion "
    "y no cambia decision/v2, campo/v2 ni ruteo/v2: corre en paralelo, en sombra."
)


# --- las definiciones -----------------------------------------------------------------------------


class FeaturePredictivaRespuesta(BaseModel):
    nombre: str
    familia: str
    clase: str = Field(description="NUMERICA o CATEGORICA.")
    tipo: str = Field(description="IMPORTE, ENTERO, DECIMAL o TEXTO.")
    transformacion: str = Field(description="NINGUNA, LOG1P o SIGNED_LOG1P, declarada.")
    ventana: str
    fuente: str
    nula: bool
    indicador_nulo: bool = Field(description="Si su NULL lleva un indicador <nombre>__nulo.")
    nulo: str = Field(description="Cuando es NULL.")
    descripcion: str


class FeaturesPredictivasRespuesta(BaseModel):
    version: str
    hash: str = Field(description="SHA-256 de la definicion canonica: lo firma cada entrenamiento.")
    relojes: str
    faltantes: dict[str, str]
    escalado: str
    categoricas: str
    forma_canonica: str
    prohibidas: list[str] = Field(description="Los patrones de nombre que ninguna feature cumple.")
    tablas_prohibidas: list[str]
    features: list[FeaturePredictivaRespuesta]


class TargetPredictivoRespuesta(BaseModel):
    target: str
    tipo: str = Field(description="BINARIO o MONTO.")
    horizonte_dias: int
    fuente: str = Field(description="PAGOS o LIFECYCLE: de que cobertura depende su madurez.")
    unidad: str
    salida: str = Field(description="El nombre del output publicado.")
    regla: str
    madurez: str


class TargetsPredictivosRespuesta(BaseModel):
    version: str
    hash: str
    momento_de_prediccion: str
    ventana: str
    relojes: str
    madurez: list[str]
    targets: list[TargetPredictivoRespuesta]
    derivados: list[dict[str, str]]
    no_causal: str


# --- los entrenamientos ---------------------------------------------------------------------------


class AnclaPredictivaEntrada(BaseModel):
    model_config = ConfigDict(extra="forbid")

    corte_id: UUID
    feature_as_of: date = Field(description="El dia de la prediccion: la fecha del corte.")
    feature_known_at: datetime = Field(
        description="Hasta que instante se conocia lo que usan las features, con zona."
    )
    label_known_at: datetime = Field(
        description="Hasta que instante se observa para construir el label, con zona."
    )
    pagos_completos_hasta: date | None = Field(
        default=None,
        description="Hasta que dia los pagos estaban completos a label_known_at: declarado, "
        "nunca la fecha maxima observada. Sin el, los labels de pagos son NO_DISPONIBLE.",
    )
    lifecycle_completo_hasta: date | None = Field(default=None)
    split: str = Field(description="TRAIN, VALIDATION, TEST o PURGE.")


class EntrenamientoPredictivoEntrada(BaseModel):
    """El manifiesto de un entrenamiento, explicito: cada ancla con sus relojes, su cobertura
    declarada y su split, y una interpretacion de pagos por cada mes que tocan las ventanas de los
    labels de pagos. Nada se elige al ejecutar."""

    model_config = ConfigDict(extra="forbid")

    zona_horaria: str = Field(description="Una zona IANA: el dia de cada ancla y sus ventanas.")
    anclas: list[AnclaPredictivaEntrada] = Field(min_length=3)
    interpretaciones_pagos: list[UUID] = Field(
        default_factory=list, description="Los motor_pagos_run_id de los labels, uno por mes."
    )
    version_politica: str | None = Field(
        default=None, description="Por omision, predictive-training/public-v1, la unica vigente."
    )


class EntrenamientoPredictivoRespuesta(BaseModel):
    training_run_id: UUID
    estado: EstadoEntrenamientoPredictivo
    resultado: str | None = Field(
        description="MODELO_PUBLICADO, MODELO_RECHAZADO, SIN_EJEMPLOS... null mientras esta "
        "EN_PROCESO."
    )
    despacho_id: str
    cartera_id: str
    zona_horaria: str
    version_modelo: str
    version_features: str
    hash_features: str
    version_targets: str
    hash_targets: str
    version_politica: str
    hash_politica: str
    hash_manifiesto: str = Field(description="SHA-256 de las anclas e interpretaciones canonicas.")
    firma_inputs: str = Field(
        description="SHA-256 de todo lo que fija el resultado: el mismo manifiesto con las mismas "
        "versiones no se entrena dos veces."
    )
    dataset_hash: str | None = Field(description="SHA-256 del dataset en orden canonico.")
    anclas: int
    ejemplos_total: int
    ejemplos_train: int
    ejemplos_validation: int
    ejemplos_test: int
    ejemplos_purge: int
    ejemplos_censurados: int
    conteos: dict[str, Any] | None = Field(
        description="Por target y split: maduras, censuradas, no disponibles y su prevalencia."
    )
    backtest: dict[str, Any] | None = Field(description="El walk-forward y el holdout.")
    tiempos: dict[str, Any] | None
    manifiesto: dict[str, Any]
    modelo_version_id: UUID | None = Field(description="La version que dejo, si termino EXITOSA.")
    estado_modelo: EstadoModeloPredictivo | None
    trabajo_id: UUID | None
    iniciada_en: datetime
    terminada_en: datetime | None
    duracion_segundos: float | None
    detalle: str | None
    aviso: str = Field(default=AVISO_PREDICTIVO)


class ParametrosEntrenamientosPredictivos(Paginacion):
    model_config = ConfigDict(extra="forbid")

    estado: EstadoEntrenamientoPredictivo | None = None


PaginaEntrenamientosPredictivos = Pagina[EntrenamientoPredictivoRespuesta]


# --- los modelos ----------------------------------------------------------------------------------


class ComponentePredictivoRespuesta(BaseModel):
    target: str
    algoritmo: str = Field(description="LOGISTICA_L2 o TWEEDIE_LOG.")
    hiperparametros: dict[str, Any]
    calibracion: str = Field(description="RAW o PLATT.")
    hash_artefacto: str
    estado: str = Field(description="VALIDADO o RECHAZADO por sus compuertas en TEST.")
    compuertas: list[dict[str, Any]]
    metricas_validation: dict[str, Any]
    metricas_test: dict[str, Any]
    baseline_test: dict[str, Any]
    bootstrap_test: dict[str, Any]
    explicacion_global: list[dict[str, Any]] = Field(
        description="Cada coeficiente en la escala estandarizada: una asociacion, no una causa."
    )
    artefacto: dict[str, Any] = Field(description="El JSON canonico con que se predice.")


class ModeloPredictivoResumen(BaseModel):
    """Una version en el registro, sin sus artefactos: su estado, sus hashes, sus periodos y el
    estado de cada componente."""

    modelo_version_id: UUID
    version_modelo: str
    estado: EstadoModeloPredictivo
    training_run_id: UUID
    version_features: str
    hash_features: str
    version_targets: str
    hash_targets: str
    version_politica: str
    hash_politica: str
    dataset_hash: str
    hash_modelo: str
    periodo_train_desde: date
    periodo_train_hasta: date
    periodo_validation_desde: date
    periodo_validation_hasta: date
    periodo_test_desde: date
    periodo_test_hasta: date
    creada_en: datetime
    publicada_en: datetime | None
    componentes: dict[str, str] = Field(description="El estado de cada componente, por target.")


class ModeloPredictivoRespuesta(BaseModel):
    modelo_version_id: UUID
    version_modelo: str
    estado: EstadoModeloPredictivo
    training_run_id: UUID
    version_features: str
    hash_features: str
    version_targets: str
    hash_targets: str
    version_politica: str
    hash_politica: str
    dataset_hash: str
    hash_modelo: str = Field(description="SHA-256 de los hashes de sus cuatro artefactos.")
    periodo_train_desde: date
    periodo_train_hasta: date
    periodo_validation_desde: date
    periodo_validation_hasta: date
    periodo_test_desde: date
    periodo_test_hasta: date
    creada_en: datetime
    publicada_en: datetime | None
    ficha: dict[str, Any] = Field(description="La ficha del modelo (model card).")
    referencia_drift: dict[str, Any]
    componentes: list[ComponentePredictivoRespuesta]
    aviso: str = Field(default=AVISO_PREDICTIVO)


class ParametrosModelosPredictivos(Paginacion):
    model_config = ConfigDict(extra="forbid")

    estado: EstadoModeloPredictivo | None = None


PaginaModelosPredictivos = Pagina[ModeloPredictivoResumen]


# --- los scorings ---------------------------------------------------------------------------------


class ScoringPredictivoEntrada(BaseModel):
    """Un scoring declara su version del modelo (PUBLICADA; nunca "la ultima"), su corte y, en
    BACKTEST, su known_at."""

    model_config = ConfigDict(extra="forbid")

    modelo_version_id: UUID
    corte_id: UUID
    modo: ModoScoringPredictivo = Field(
        description="OPERATIVO (known_at opcional: sin el, el reloj de la base, una vez) o "
        "BACKTEST (known_at obligatorio)."
    )
    known_at: datetime | None = Field(default=None, description="Con zona horaria.")


class ScoringPredictivoRespuesta(BaseModel):
    scoring_run_id: UUID
    estado: EstadoScoringPredictivo
    resultado: str | None
    modo: ModoScoringPredictivo
    modelo_version_id: UUID
    corte_id: UUID
    despacho_id: str
    cartera_id: str
    as_of: date
    known_at: datetime
    zona_horaria: str
    version_features: str
    hash_features: str
    firma_inputs: str
    cuentas_objetivo: int
    predicciones_publicadas: int
    hash_predicciones: str | None = Field(
        description="SHA-256 de las predicciones cuantizadas en el orden de cuenta_id."
    )
    hash_drift: str | None
    disponibilidad: dict[str, Any] | None = Field(
        description="La cobertura de pagos y la evaluacion de promesas conocidas a known_at."
    )
    drift_resumen: dict[str, Any] | None
    tiempos: dict[str, Any] | None
    trabajo_id: UUID | None
    iniciada_en: datetime
    terminada_en: datetime | None
    duracion_segundos: float | None
    detalle: str | None
    aviso: str = Field(default=AVISO_PREDICTIVO)


class ParametrosScoringsPredictivos(Paginacion):
    model_config = ConfigDict(extra="forbid")

    estado: EstadoScoringPredictivo | None = None
    modelo_version_id: UUID | None = None
    corte_id: UUID | None = None


PaginaScoringsPredictivos = Pagina[ScoringPredictivoRespuesta]


class PrediccionCuentaRespuesta(BaseModel):
    cuenta_id: UUID
    producto: str
    cve_entidad: str
    p_recuperacion_positiva_30d: Decimal
    p_contacto_14d: Decimal
    p_promesa_30d: Decimal
    riesgo_sin_recuperacion_30d: Decimal = Field(
        description="1 - p_recuperacion_positiva_30d, exacto; sin modelo propio."
    )
    recuperacion_positiva_esperada_30d: Decimal = Field(
        description="La recuperacion POSITIVA esperada en 30 dias: no la neta."
    )
    estado: str = Field(description="COMPLETA o CON_IMPUTACION.")
    motivos: list[dict[str, Any]]
    feature_hash: str


class ParametrosPrediccionesPredictivas(Paginacion):
    """Filtros en la base: un rango por salida, producto, entidad y estado; un orden de una lista
    cerrada. Ningun otro parametro se acepta."""

    model_config = ConfigDict(extra="forbid")

    p_recuperacion_positiva_30d_min: Decimal | None = Field(default=None, ge=0, le=1)
    p_recuperacion_positiva_30d_max: Decimal | None = Field(default=None, ge=0, le=1)
    p_contacto_14d_min: Decimal | None = Field(default=None, ge=0, le=1)
    p_contacto_14d_max: Decimal | None = Field(default=None, ge=0, le=1)
    p_promesa_30d_min: Decimal | None = Field(default=None, ge=0, le=1)
    p_promesa_30d_max: Decimal | None = Field(default=None, ge=0, le=1)
    riesgo_sin_recuperacion_30d_min: Decimal | None = Field(default=None, ge=0, le=1)
    riesgo_sin_recuperacion_30d_max: Decimal | None = Field(default=None, ge=0, le=1)
    recuperacion_positiva_esperada_30d_min: Decimal | None = Field(default=None, ge=0)
    recuperacion_positiva_esperada_30d_max: Decimal | None = Field(default=None, ge=0)
    producto: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_\-]{1,40}$")
    cve_entidad: str | None = Field(default=None, pattern=r"^[0-9]{1,3}$")
    estado: str | None = Field(default=None, pattern=r"^(COMPLETA|CON_IMPUTACION)$")
    orden: Orden = Field(default=Orden.CUENTA)
    sentido: Sentido = Field(default=Sentido.ASC)


class PaginaPrediccionesPredictivas(Pagina[PrediccionCuentaRespuesta]):
    scoring_run_id: UUID
    aviso: str = Field(default=AVISO_PREDICTIVO)


class PrediccionDeCuentaRespuesta(BaseModel):
    scoring_run_id: UUID
    modelo_version_id: UUID
    corte_id: UUID
    as_of: date
    known_at: datetime
    p_recuperacion_positiva_30d: Decimal
    p_contacto_14d: Decimal
    p_promesa_30d: Decimal
    riesgo_sin_recuperacion_30d: Decimal
    recuperacion_positiva_esperada_30d: Decimal
    estado: str
    motivos: list[dict[str, Any]]
    feature_hash: str


class PaginaPrediccionesDeCuenta(Pagina[PrediccionDeCuentaRespuesta]):
    cuenta_id: UUID
    aviso: str = Field(default=AVISO_PREDICTIVO)


# --- el drift y la explicacion --------------------------------------------------------------------


class DriftPredictivoFila(BaseModel):
    tipo: str = Field(description="FEATURE o SCORE.")
    nombre: str
    clase: str
    psi: Decimal
    severidad: str = Field(description="BAJO, MODERADO o ALTO: una heuristica operacional.")
    faltantes_train: Decimal | None
    faltantes_scoring: Decimal | None
    severidad_faltantes: str | None
    bins: dict[str, Any] = Field(description="Bordes y proporciones: distribuciones, no cuentas.")


class DriftPredictivoRespuesta(BaseModel):
    scoring_run_id: UUID
    modelo_version_id: UUID
    resumen: dict[str, Any] | None
    filas: list[DriftPredictivoFila]
    aviso: str = Field(
        default="El drift describe cuanto se parece lo puntuado a TRAIN. No cambia, no recalibra "
        "ni reentrena el modelo; sus umbrales son heuristicas operacionales, no leyes estadisticas."
    )


class ContribucionPredictiva(BaseModel):
    feature: str
    contribucion: float


class ComponenteExplicadoRespuesta(BaseModel):
    target: str
    salida: str
    escala: str = Field(description="La escala del predictor lineal: log-odds o log del monto.")
    intercepto: float
    predictor_lineal: float
    suma_de_contribuciones: float
    calibracion: dict[str, Any]
    prediccion_publicada: str
    prediccion_reproducida: str
    hacia_arriba: list[ContribucionPredictiva]
    hacia_abajo: list[ContribucionPredictiva]
    verificacion: dict[str, bool]


class ExplicacionPredictivaRespuesta(BaseModel):
    scoring_run_id: UUID
    modelo_version_id: UUID
    cuenta_id: UUID
    as_of: date
    known_at: datetime
    estado: str
    motivos: list[dict[str, Any]]
    riesgo_sin_recuperacion_30d: str
    feature_hash: str
    features: dict[str, str | None]
    componentes: list[ComponenteExplicadoRespuesta]
    aviso: str
