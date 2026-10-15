"""Los esquemas de analytics/v1 en la API: el catalogo, el manifiesto de una ejecucion, la
ejecucion, sus agregados, su resumen, sus embudos y sus controles.

Ninguna respuesta lleva una cuenta, un CLIENTE_UNICO, un cuenta_id, un nombre, una direccion, un
telefono, un actor_ref, una coordenada, un tramo, un microtramo ni una polilinea: solo agregados
por celda de un conjunto fijo de dimensiones, con su estado. Un valor SUPRIMIDO, NO_DISPONIBLE o
NO_APLICA no tiene valor, y un NULL no es un cero.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from motor_cartera.analytics.catalogo import Conjunto, Vista
from motor_cartera.analytics.politica import Granularidad, Modo
from motor_cartera.api.esquemas import Pagina, Paginacion
from motor_cartera.db.modelos import EstadoAnalytics

AVISO_ANALYTICS = (
    "analytics/v1 describe lo observado y lo planificado, agregado: no prueba causalidad, no "
    "predice y no recomienda. Una asociacion de la atribucion es operacional, no causal; una "
    "parada programada no es una visita realizada; un gestor de campo no es el actor_ref de una "
    "gestion. Las celdas con menos cuentas que el minimo de la politica se suprimen."
)

PATRON_METRICA = r"^[a-z_]+\.[a-z_]+$"
PATRON_VALOR_DIMENSION = r"^[A-Za-z0-9_+\-]{1,40}$"


# --- el catalogo ----------------------------------------------------------------------------------


class DimensionPermitidaAnalytics(BaseModel):
    conjunto: Conjunto
    llaves: list[str] = Field(description="Las llaves de dimension de sus celdas; TOTAL, ninguna.")


class MetricaAnalyticsRespuesta(BaseModel):
    """La definicion formal de una metrica: lo que calcula, de que, sobre que poblacion, en que
    ventana de tiempo, cuando no esta disponible y por que dimensiones se desglosa."""

    codigo: str
    nombre: str
    descripcion: str
    familia: str
    unidad: str
    tipo: str = Field(description="VALOR o RAZON. Una razon publica su numerador y denominador.")
    universo: str = Field(description="COHORTE, CAMPO o RUTEO: de que cuentas es su poblacion.")
    formula: str = Field(description="La formula, legible.")
    numerador: str | None
    denominador: str | None
    poblacion: str
    regla_temporal: str
    fuentes_requeridas: list[str] = Field(
        description="Las entradas del manifiesto que necesita; sin ellas es NO_DISPONIBLE."
    )
    nulo: str = Field(description="Cuando es NO_DISPONIBLE, o cuando su valor es NULL.")
    agregacion: str
    aditiva: bool
    dimensiones_permitidas: list[DimensionPermitidaAnalytics]
    vistas: list[Vista]
    monetaria: bool = Field(description="En la moneda de los montos de la fuente: no se inventa.")
    puede_ser_negativa: bool
    cobertura_de_pagos: bool
    limitaciones: list[str]
    version: str = Field(description="La version del catalogo que la define.")


class PoliticaAnalyticsRespuesta(BaseModel):
    version: str
    hash: str
    min_group_size: int = Field(description="Una celda con menos cuentas se suprime.")
    conjuntos_en_periodo: list[Conjunto] = Field(
        description="Los conjuntos que se publican tambien por periodo."
    )
    max_periodos: int
    escala_razon: int = Field(description="Los decimales de una razon publicada.")
    reglas: list[str]


class ConjuntoAnalyticsRespuesta(BaseModel):
    conjunto: Conjunto
    llaves: list[str]
    universos: list[str]


class CatalogoAnalyticsRespuesta(BaseModel):
    version: str
    hash: str = Field(description="SHA-256 de su definicion canonica: lo firma cada ejecucion.")
    version_motor: str
    politicas: list[PoliticaAnalyticsRespuesta]
    conjuntos: list[ConjuntoAnalyticsRespuesta]
    metricas: list[MetricaAnalyticsRespuesta]
    aviso: str = Field(default=AVISO_ANALYTICS)


# --- las ejecuciones ------------------------------------------------------------------------------


class EjecucionAnalyticsEntrada(BaseModel):
    """El manifiesto de una ejecucion, explicito: el corte de la cohorte, los dos relojes, la zona
    y la granularidad, y cada entrada por su identificador publico. Ninguna se elige sola: lo que
    no se declara no se lee, y sus metricas son NO_DISPONIBLE."""

    model_config = ConfigDict(extra="forbid")

    corte_id: UUID = Field(description="El corte canonico de la cohorte.")
    as_of: date = Field(description="Hasta que dia de negocio se describe, en `zona_horaria`.")
    modo: Modo = Field(
        description="OPERATIVO (known_at opcional: sin el, el reloj de la base, una vez) o "
        "BACKTEST (known_at obligatorio)."
    )
    zona_horaria: str = Field(description="Una zona IANA: el final de as_of y los periodos.")
    granularidad: Granularidad = Field(
        description="TOTAL (sin periodos), DIA, SEMANA_ISO (de lunes a domingo) o MES."
    )
    known_at: datetime | None = Field(
        default=None, description="Hasta que instante se podia conocer la informacion, con zona."
    )
    motor_pagos_run_ids: list[UUID] = Field(
        default_factory=list,
        description="Una interpretacion de motor-pagos/v1 por cada mes del horizonte que se quiera "
        "describir.",
    )
    atribucion_run_ids: list[UUID] = Field(
        default_factory=list,
        description="La atribucion/v1 de cada mes, la que leyo la interpretacion declarada.",
    )
    evaluacion_run_id: UUID | None = Field(
        default=None, description="Una evaluacion de promesas con el mismo as_of y la misma zona."
    )
    decision_run_id: UUID | None = Field(default=None, description="Una decision/v2 del corte.")
    geografia_run_id: UUID | None = Field(default=None, description="Una geografia del corte.")
    territorial_run_id: UUID | None = Field(
        default=None, description="Una territorial/v2 del corte, de la geografia declarada."
    )
    campo_run_id: UUID | None = Field(
        default=None,
        description="Un plan de campo/v2 de la decision y la territorial declaradas, con su fecha "
        "en el horizonte.",
    )
    ruteo_run_id: UUID | None = Field(
        default=None, description="Una ejecucion de ruteo/v2 del plan declarado."
    )
    version_catalogo: str | None = Field(
        default=None, description="Por omision, analytics-metrics/public-v1, la unica vigente."
    )
    version_politica: str | None = Field(
        default=None, description="Por omision, analytics-public-v1, la unica vigente."
    )


class EntradaDeAnalyticsRespuesta(BaseModel):
    tipo: str
    referencia_publica: UUID
    version: str
    firma: str
    periodo_desde: date | None
    periodo_hasta: date | None
    disponible_en: datetime = Field(
        description="Cuando termino (o se creo, el corte): a known_at ya existia."
    )
    metadatos: dict[str, Any]


class EjecucionAnalyticsRespuesta(BaseModel):
    analytics_run_id: UUID
    estado: EstadoAnalytics
    resultado: str | None = Field(
        description="ANALYTICS_PUBLICADA, CONTROL_FALLIDO, INTENTOS_AGOTADOS... null mientras "
        "esta EN_PROCESO."
    )
    modo: Modo
    corte_id: UUID
    fecha_corte: date
    as_of: date
    known_at: datetime
    zona_horaria: str
    granularidad: Granularidad
    horizonte_dias: int
    version_motor: str
    version_catalogo: str
    hash_catalogo: str
    version_politica: str
    hash_politica: str
    hash_manifiesto: str = Field(description="SHA-256 de las entradas, en su orden canonico.")
    firma_inputs: str = Field(
        description="SHA-256 de todo lo que fija el resultado. Las mismas entradas, la misma "
        "firma: no se publican dos veces."
    )
    cuentas_cohorte: int
    agregados_publicados: int
    controles_totales: int
    controles_fallidos: int
    hash_agregados: str | None = Field(
        description="SHA-256 de lo publicado, en orden canonico: lo que una reconstruccion repite."
    )
    disponibilidad: dict[str, Any] | None = Field(
        description="Que familias se publicaron, cuales no y por que, la cobertura de pagos por "
        "dia y cuantas celdas se suprimieron."
    )
    entradas: list[EntradaDeAnalyticsRespuesta]
    trabajo_id: UUID | None
    iniciada_en: datetime
    terminada_en: datetime | None
    duracion_segundos: float | None
    detalle: str | None
    aviso: str = Field(default=AVISO_ANALYTICS)


class PaginaEjecucionesAnalytics(Pagina[EjecucionAnalyticsRespuesta]):
    pass


class ParametrosEjecucionesAnalytics(Paginacion):
    model_config = ConfigDict(extra="forbid")

    corte_id: UUID | None = None
    estado: EstadoAnalytics | None = None
    as_of: date | None = None


# --- los agregados --------------------------------------------------------------------------------


class AgregadoAnalyticsRespuesta(BaseModel):
    """Una metrica en una celda y un periodo. Solo un DISPONIBLE tiene valor; una razon trae su
    numerador y su denominador, y con denominador cero su valor es null."""

    metrica: str
    vista: Vista
    dimension_set: Conjunto
    periodo_desde: date
    periodo_hasta: date
    periodo_parcial: bool
    dimensiones: dict[str, str]
    dimension_hash: str
    estado_valor: str = Field(description="DISPONIBLE, NO_DISPONIBLE, SUPRIMIDO o NO_APLICA.")
    poblacion_cuentas: int | None = Field(
        description="Las cuentas distintas de la celda en el horizonte. Null si esta suprimida."
    )
    valor: Decimal | None
    numerador: Decimal | None
    denominador: Decimal | None
    motivos: list[dict[str, Any]]


class PaginaAgregadosAnalytics(Pagina[AgregadoAnalyticsRespuesta]):
    analytics_run_id: UUID


class ParametrosAgregadosAnalytics(Paginacion):
    """Columnas y llaves de dimension del catalogo, con valores: nada mas. Un parametro que no es
    uno de estos se rechaza."""

    model_config = ConfigDict(extra="forbid")

    metrica: str | None = Field(default=None, pattern=PATRON_METRICA)
    vista: Vista | None = None
    dimension_set: Conjunto | None = None
    periodo_desde: date | None = Field(
        default=None, description="Los periodos que empiezan ese dia o despues."
    )
    periodo_hasta: date | None = Field(
        default=None, description="Los periodos que terminan ese dia o antes."
    )
    estado_valor: str | None = Field(
        default=None, pattern="^(DISPONIBLE|NO_DISPONIBLE|SUPRIMIDO|NO_APLICA)$"
    )
    producto: str | None = Field(default=None, pattern=PATRON_VALOR_DIMENSION)
    atraso_bucket: str | None = Field(default=None, pattern=PATRON_VALOR_DIMENSION)
    entidad: str | None = Field(default=None, pattern=PATRON_VALOR_DIMENSION)
    municipio: str | None = Field(default=None, pattern=PATRON_VALOR_DIMENSION)
    prioridad_decision: str | None = Field(default=None, pattern=PATRON_VALOR_DIMENSION)
    canal_decision: str | None = Field(default=None, pattern=PATRON_VALOR_DIMENSION)
    base: str | None = Field(default=None, pattern=PATRON_VALOR_DIMENSION)
    canal_gestion: str | None = Field(default=None, pattern=PATRON_VALOR_DIMENSION)
    medio_gestion: str | None = Field(default=None, pattern=PATRON_VALOR_DIMENSION)
    estado_campo: str | None = Field(default=None, pattern=PATRON_VALOR_DIMENSION)
    gestor_campo: str | None = Field(default=None, pattern=PATRON_VALOR_DIMENSION)
    estado_ruteo: str | None = Field(default=None, pattern=PATRON_VALOR_DIMENSION)


# --- el resumen, los embudos y los controles ------------------------------------------------------


class IndicadorAnalyticsRespuesta(BaseModel):
    nombre: str
    metrica: str
    estado_valor: str
    valor: Decimal | None
    numerador: Decimal | None
    denominador: Decimal | None
    motivos: list[dict[str, Any]]


class ResumenAnalyticsRespuesta(BaseModel):
    """Unos pocos indicadores del TOTAL acumulado, cada uno con su estado: lo que no esta
    disponible lo dice, sin valor."""

    analytics_run_id: UUID
    fecha_corte: date
    as_of: date
    known_at: datetime
    cuentas_cohorte: int
    indicadores: list[IndicadorAnalyticsRespuesta]
    aviso: str = Field(default=AVISO_ANALYTICS)


class EtapaEmbudoAnalytics(BaseModel):
    etapa: str
    metrica: str
    estado_valor: str
    cuentas: int | None
    pct_etapa_anterior: Decimal | None = Field(
        description="Las cuentas de la etapa entre las de la anterior. Null en la primera, sin "
        "dato o con la anterior en cero."
    )
    pct_cohorte: Decimal | None = Field(description="Las cuentas de la etapa entre la cohorte.")


class EmbudoAnalyticsRespuesta(BaseModel):
    embudo: str
    etapas: list[EtapaEmbudoAnalytics]


class EmbudosAnalyticsRespuesta(BaseModel):
    """Conjuntos de cuentas anidados del horizonte entero, no sumas de eventos. Un pago no es una
    etapa: el embudo no dice que una gestion haya producido un pago."""

    analytics_run_id: UUID
    embudos: list[EmbudoAnalyticsRespuesta]
    aviso: str = Field(default=AVISO_ANALYTICS)


class ControlAnalyticsRespuesta(BaseModel):
    codigo: str
    estado: str = Field(description="OK o FALLO: exacto, sin tolerancia.")
    esperado: Decimal | None
    observado: Decimal | None
    diferencia: Decimal | None
    detalle: str


class ControlesAnalyticsRespuesta(BaseModel):
    analytics_run_id: UUID
    estado: EstadoAnalytics
    resultado: str | None
    controles_totales: int
    controles_fallidos: int
    controles: list[ControlAnalyticsRespuesta]
