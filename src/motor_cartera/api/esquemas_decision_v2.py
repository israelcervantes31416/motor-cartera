"""Los esquemas de decision/v2 en la API: pedir una decision, leer su ejecucion, sus resultados, la
decision de una cuenta con sus features y la historia de decisiones de una cuenta."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, create_model

from motor_cartera.api.esquemas import Pagina, Paginacion
from motor_cartera.db.modelos import EstadoDecisionV2, ModoDecisionV2
from motor_cartera.decision.reglas import Prioridad
from motor_cartera.decision_v2.features import FEATURES, Tipo

AVISO_DECISION_V2 = (
    "decision/v2 es un motor de reglas deterministas, point-in-time y explicables, sobre datos "
    "sinteticos: no es un modelo predictivo, y un nivel es el lugar de una prioridad en su "
    "escala, no una probabilidad. decision-policy/public-v1 demuestra la capacidad del motor; no "
    "representa una politica real de un acreedor. Corre en paralelo a decision/v1, que sigue "
    "alimentando el flujo operacional."
)

Canal = Literal["DIGITAL", "TELEFONICA", "CAMPO"]


class DecisionV2Entrada(BaseModel):
    """Lo que fija una decision v2: el corte (su universo), as_of y la frontera de conocimiento."""

    model_config = ConfigDict(extra="forbid")

    corte_id: UUID = Field(description="El corte canonico cuyas cuentas se deciden.")
    as_of: date | None = Field(
        default=None,
        description="El dia de negocio que se decide: el del corte. Si se manda, tiene que ser el "
        "suyo.",
    )
    known_at: AwareDatetime | None = Field(
        default=None,
        description="Hasta cuando se conoce, con su zona. Lo que se registro despues no existe "
        "para esta decision. En OPERATIVO, si se omite, es el reloj de la base al abrir, una sola "
        "vez; un BACKTEST lo declara siempre.",
    )
    modo: ModoDecisionV2 = Field(
        default=ModoDecisionV2.OPERATIVO,
        description="OPERATIVO: el corte mas reciente conocido a known_at. BACKTEST: cualquier "
        "corte conocido a known_at, con known_at obligatorio.",
    )
    version_politica: str | None = Field(
        default=None,
        description="Por omision, decision-policy/public-v1, la unica que este servicio aplica.",
    )


class CorteDeLaDecision(BaseModel):
    corte_id: UUID
    fecha_corte: date


class VersionesDecisionV2(BaseModel):
    motor: str
    features: str
    politica: str


class ConteosDecisionV2(BaseModel):
    cuentas_objetivo: int = Field(description="Las cuentas del corte: sus snapshots.")
    cuentas_decididas: int = Field(description="Todas en una EXITOSA; cero en las demas.")
    cuentas_fallidas: int = Field(
        description="Las que no se pudieron decidir; con una sola, no se publica ninguna."
    )


class Advertencia(BaseModel):
    codigo: str
    mensaje: str


class EjecucionDecisionV2Respuesta(BaseModel):
    """Una ejecucion de decision/v2: sus entradas, sus versiones y lo que publico."""

    decision_run_id: UUID
    estado: EstadoDecisionV2
    resultado: str | None = Field(
        description="DECISION_PUBLICADA, YA_DECIDIDA, CUENTAS_NO_DECIDIBLES, ERROR_INTERNO... "
        "null mientras esta EN_PROCESO."
    )
    modo: ModoDecisionV2
    corte: CorteDeLaDecision
    despacho_id: str
    cartera_id: str
    as_of: date
    known_at: datetime = Field(description="La frontera de conocimiento con que se decidio.")
    zona_horaria: str = Field(
        description="La del dia logico: as_of termina al iniciar el dia siguiente en ella."
    )
    fin_del_dia: datetime = Field(description="El final de as_of en esa zona, como instante.")
    versiones: VersionesDecisionV2
    hash_politica: str
    firma_inputs: str = Field(
        description="SHA-256 de sus entradas logicas: las mismas entradas son la misma decision."
    )
    conteos: ConteosDecisionV2
    disponibilidad: dict[str, Any] | None = Field(
        description="Que informacion de pagos y de atribucion estaba disponible a known_at, por "
        "mes y por ventana. Explica cada feature NULL."
    )
    distribucion: dict[str, Any] | None = Field(
        description="Cuantas decisiones de cada prioridad, canal y ajuste, y cuantas activaron "
        "cada regla. No es un juicio de calidad."
    )
    advertencias: list[Advertencia]
    trabajo_id: UUID | None
    iniciada_en: datetime
    terminada_en: datetime | None
    detalle: str | None
    aviso: str = Field(default=AVISO_DECISION_V2)


class PaginaEjecucionesDecisionV2(Pagina[EjecucionDecisionV2Respuesta]):
    corte_id: UUID
    fecha_corte: date


class ResultadoDecisionV2Respuesta(BaseModel):
    """La decision de una cuenta, con sus motivos. Las features, en /resultados/{cuenta_id}."""

    cuenta_id: UUID
    cliente_unico: str
    prioridad_base: Prioridad
    score_nivel_base: int = Field(description="El lugar de la prioridad base: BAJA 1 a MUY_ALTA 4.")
    ajuste_total: int = Field(description="La suma de los efectos de las reglas que aplicaron.")
    score_nivel_final: int = Field(description="El nivel base mas el ajuste, acotado de 1 a 4.")
    prioridad_final: Prioridad
    canal_recomendado: str
    motivos: list[dict[str, Any]]
    decision_hash: str


class ParametrosResultadosDecisionV2(Paginacion):
    prioridad_final: Prioridad | None = Field(
        default=None, description="Solo las de esa prioridad."
    )
    canal_recomendado: Canal | None = Field(default=None, description="Solo las de ese canal.")


class PaginaResultadosDecisionV2(Pagina[ResultadoDecisionV2Respuesta]):
    decision_run_id: UUID
    as_of: date
    known_at: datetime
    aviso: str = Field(default=AVISO_DECISION_V2)


_TIPOS: dict[Tipo, type] = {
    Tipo.ENTERO: int,
    Tipo.IMPORTE: Decimal,
    Tipo.TASA: Decimal,
    Tipo.DIAS: Decimal,
    Tipo.TEXTO: str,
    Tipo.BOOLEANO: bool,
}

FeaturesRespuesta = create_model(
    "FeaturesRespuesta",
    __doc__="Las features con que se decidio la cuenta, tal como se publicaron: "
    "features-decision/v1. Un null es informacion no disponible a known_at o sin de que "
    "calcularse, nunca un cero.",
    **{f.nombre: ((_TIPOS[f.tipo] | None) if f.nula else _TIPOS[f.tipo], ...) for f in FEATURES},
)


class CondicionRespuesta(BaseModel):
    feature: str
    valor: str | None = Field(description="El de la feature, en forma canonica; null si era null.")
    operador: str
    umbral: str
    cumple: bool


class ReglaRespuesta(BaseModel):
    """Una regla de la politica evaluada sobre las features publicadas: aplique o no."""

    codigo: str
    activa: bool
    efecto: int = Field(description="El que sumo: el de la regla si aplico, 0 si no.")
    condiciones: list[CondicionRespuesta]


class VerificacionRespuesta(BaseModel):
    """Si las features y la decision publicadas son las que la politica da sobre esas mismas
    features: se comprueba al responder, sin recalcular ninguna feature."""

    feature_hash: bool
    decision_hash: bool
    motivos: bool


class DecisionV2DeCuentaRespuesta(BaseModel):
    """La decision de una cuenta en una ejecucion, explicada: lo que se sabia (sus features), cada
    regla con lo que encontro, la prioridad antes y despues, el canal y los hashes."""

    decision_run_id: UUID
    as_of: date
    known_at: datetime
    versiones: VersionesDecisionV2
    hash_politica: str
    cuenta_id: UUID
    cliente_unico: str
    features: FeaturesRespuesta  # type: ignore[valid-type]
    feature_hash: str
    prioridad_base: Prioridad
    score_nivel_base: int
    reglas: list[ReglaRespuesta]
    ajuste_total: int
    score_nivel_final: int
    prioridad_final: Prioridad
    canal_recomendado: str
    motivos: list[dict[str, Any]]
    decision_hash: str
    verificacion: VerificacionRespuesta
    aviso: str = Field(default=AVISO_DECISION_V2)


class DecisionV2EnHistoriaRespuesta(BaseModel):
    decision_run_id: UUID
    corte_id: UUID
    as_of: date
    known_at: datetime
    modo: ModoDecisionV2
    prioridad_base: Prioridad
    ajuste_total: int
    prioridad_final: Prioridad
    canal_recomendado: str
    motivos: list[str] = Field(description="Los codigos de sus motivos, en orden.")
    decision_hash: str


class PaginaDecisionesV2DeCuenta(Pagina[DecisionV2EnHistoriaRespuesta]):
    cuenta_id: UUID
    cliente_unico: str
    aviso: str = Field(default=AVISO_DECISION_V2)
