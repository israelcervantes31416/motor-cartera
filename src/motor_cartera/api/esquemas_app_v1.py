"""Los esquemas de app/v1 en la API: el contexto operativo, sus opciones, el resumen, el
explorador de cuentas, la Cuenta 360 de la app y el linaje.

app/v1 no define de nuevo lo que un dominio ya define. Donde reutiliza una respuesta de un dominio
que lleva un dato personal (el CLIENTE_UNICO de una cuenta, el actor_ref de un evento), la deriva
con `sin_datos_personales`: el mismo modelo, con sus descripciones, sin esos campos en ningun
nivel. Si el dominio agrega un campo, la app lo hereda; si es personal, no sale.

Ninguna respuesta lleva un CLIENTE_UNICO, un nombre, una direccion, un telefono, una coordenada,
una geometria ni un actor_ref.
"""

from __future__ import annotations

import functools
import operator
import types
from copy import copy
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal, Union, get_args, get_origin
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, create_model

from motor_cartera.api.esquemas import (
    LifecycleResumenRespuesta,
    Pagina,
    Paginacion,
    PaginaHistoria,
    ResumenPagosRespuesta,
    SnapshotRespuesta,
)
from motor_cartera.api.esquemas_analytics import (
    EmbudoAnalyticsRespuesta,
    IndicadorAnalyticsRespuesta,
)
from motor_cartera.api.esquemas_campo import (
    AsignacionCampoDeCuentaRespuesta,
    CapacidadCampoV2,
    CargaGestorRespuesta,
    ConteosCampoV2,
)
from motor_cartera.api.esquemas_decision_v2 import DecisionV2DeCuentaRespuesta
from motor_cartera.api.esquemas_lifecycle import (
    PaginaAtribucionesDeCuenta,
    PaginaGestiones,
    PaginaLifecycle,
)
from motor_cartera.api.esquemas_optimizacion import LIMITACIONES, ResultadoCuentaRespuesta
from motor_cartera.api.esquemas_predictivo import PrediccionDeCuentaRespuesta
from motor_cartera.api.esquemas_ruteo_v2 import (
    ConteosRuteoV2,
    PaginaParadasV2,
    ParadaDeCuentaRespuesta,
    TotalesRuteoV2,
)
from motor_cartera.app_v1 import VERSION_APP
from motor_cartera.atraso import TRAMOS_ATRASO

AVISO_APP = (
    "app/v1 es de solo lectura: compone lo que cada motor ya publico, bajo un contexto explicito "
    "(el corte y cada ejecucion, por su identificador publico). No ejecuta, no envia, no llama, no "
    "visita, no optimiza ni reentrena nada, y nada se elige como 'lo ultimo'."
)
AVISO_PREDICCION = "Prediccion, no efecto causal ni recomendacion de accion."
AVISO_OPTIMO = "Optimo respecto al modelo declarado; no estrategia causalmente optima."
AVISO_ATRIBUCION = "Asociacion operacional; no implica causalidad."


# --- sin datos personales -------------------------------------------------------------------------

CAMPOS_PERSONALES = frozenset({"cliente_unico", "actor_ref", "idempotency_key", "observacion"})
"""Lo que ninguna respuesta de la app lleva: el identificador fuente de la cuenta, quien hizo una
gestion, la llave con que un cliente de la API registro un evento y la observacion de una gestion o
de una visita. Las dos ultimas son texto libre que nadie valida: pueden traer un nombre o un
telefono."""


def _tiene_personales(tipo: Any, vistos: frozenset = frozenset()) -> bool:
    if isinstance(tipo, type) and issubclass(tipo, BaseModel):
        if tipo in vistos:
            return False
        return any(
            nombre in CAMPOS_PERSONALES or _tiene_personales(campo.annotation, vistos | {tipo})
            for nombre, campo in tipo.model_fields.items()
        )
    return any(_tiene_personales(argumento, vistos) for argumento in get_args(tipo))


@functools.cache
def sin_datos_personales(modelo: type[BaseModel]) -> type[BaseModel]:
    """El mismo modelo de respuesta, sin sus campos personales en ningun nivel, con el mismo nombre
    mas App. Un modelo que no lleva ninguno se devuelve tal cual."""
    if not _tiene_personales(modelo):
        return modelo
    campos: dict[str, Any] = {}
    for nombre, campo in modelo.model_fields.items():
        if nombre in CAMPOS_PERSONALES:
            continue
        anotacion = _sin_personales(campo.annotation)
        info = copy(campo)
        info.annotation = anotacion
        campos[nombre] = (anotacion, info)
    return create_model(
        f"{modelo.__name__}App",
        __doc__=(modelo.__doc__ or "").strip() + "\n\nSin datos personales (app/v1).",
        __module__=__name__,
        **campos,
    )


def _sin_personales(tipo: Any) -> Any:
    if isinstance(tipo, type) and issubclass(tipo, BaseModel):
        return sin_datos_personales(tipo)
    origen = get_origin(tipo)
    if origen is None:
        return tipo
    argumentos = get_args(tipo)
    nuevos = tuple(_sin_personales(a) for a in argumentos)
    if nuevos == argumentos:
        return tipo
    if origen in (Union, types.UnionType):
        return functools.reduce(operator.or_, nuevos)
    if origen is list:
        return list[nuevos[0]]
    if origen is dict:
        return dict[nuevos[0], nuevos[1]]
    raise TypeError(f"sin_datos_personales no sabe derivar {tipo}")


def sin_personales(modelo: type[BaseModel], objeto: BaseModel) -> BaseModel:
    """`objeto` como el modelo derivado: los campos personales no pasan."""
    return modelo.model_validate(objeto.model_dump())


DecisionV2DeCuentaApp = sin_datos_personales(DecisionV2DeCuentaRespuesta)
PaginaHistoriaApp = sin_datos_personales(PaginaHistoria)
PaginaLifecycleApp = sin_datos_personales(PaginaLifecycle)
PaginaGestionesApp = sin_datos_personales(PaginaGestiones)
PaginaAtribucionesDeCuentaApp = sin_datos_personales(PaginaAtribucionesDeCuenta)
AsignacionCampoDeCuentaApp = sin_datos_personales(AsignacionCampoDeCuentaRespuesta)
ParadaDeCuentaApp = sin_datos_personales(ParadaDeCuentaRespuesta)
PaginaParadasApp = sin_datos_personales(PaginaParadasV2)


# --- el contexto ----------------------------------------------------------------------------------


class ContextoEntrada(BaseModel):
    """El contexto operativo, en la query string: el corte y, si se leen, cada ejecucion por su
    identificador publico. Lo que no se declara no se lee."""

    model_config = ConfigDict(extra="forbid")

    corte_id: UUID = Field(description="El corte canonico del contexto.")
    decision_run_id: UUID | None = Field(default=None, description="Una decision/v2 del corte.")
    analytics_run_id: UUID | None = Field(
        default=None, description="Una ejecucion de analytics/v1 del corte."
    )
    scoring_run_id: UUID | None = Field(default=None, description="Un scoring del corte.")
    optimizacion_run_id: UUID | None = Field(
        default=None, description="Una ejecucion de optimization/v1 del corte."
    )
    campo_run_id: UUID | None = Field(default=None, description="Un plan de campo/v2 del corte.")
    ruteo_run_id: UUID | None = Field(
        default=None, description="Un ruteo/v2 de un plan de campo/v2 del corte."
    )


class CorteDelContexto(BaseModel):
    corte_id: UUID
    despacho_id: str
    cartera_id: str
    fecha_corte: date
    cuentas: int = Field(description="Las cuentas del corte: sus snapshots.")
    firma_contenido: str
    version_modelo: str
    dataset_id: UUID = Field(description="El dataset conformado del que salieron sus snapshots.")
    run_id: UUID | None = Field(description="La corrida que lo publico.")
    creado_en: datetime


class EjecucionDelContexto(BaseModel):
    """Lo que dice de si misma una ejecucion: su estado, su corte, sus relojes, sus versiones, sus
    hashes y lo que ella misma declaro que leyo."""

    tipo: str = Field(
        description="DECISION_V2, ANALYTICS, SCORING, OPTIMIZACION, CAMPO_V2 o RUTEO_V2."
    )
    run_id: UUID
    estado: str
    resultado: str | None
    corte_id: UUID
    fecha_corte: date
    as_of: date | None = Field(
        description="Null en las que no lo declaran (campo, ruteo, optimizacion)."
    )
    known_at: datetime | None
    zona_horaria: str | None = Field(
        description="La zona de sus relojes y de sus fechas de negocio; null en una optimizacion."
    )
    fecha_operacion: date | None = Field(
        description="La fecha que planea campo, ruteo u optimizacion."
    )
    version_motor: str | None = Field(description="En un scoring, la version del modelo.")
    version_politica: str | None
    hash_politica: str | None
    firma_inputs: str | None
    iniciada_en: datetime
    terminada_en: datetime | None
    lee: dict[str, UUID] = Field(
        description="Lo que declaro leer, por tipo: su scoring, su decision/v2, su plan de campo, "
        "su ruteo, su territorial/v2, su geografia, su version del modelo, su configuracion..."
    )


class ProblemaDeContexto(BaseModel):
    codigo: str = Field(
        description="EJECUCION_NO_ENCONTRADA, CORTE_DISTINTO o ENCADENAMIENTO_DISTINTO."
    )
    mensaje: str
    ejecuciones: list[str] = Field(description="Los tipos que lo producen.")


class ContextoValidadoRespuesta(BaseModel):
    """El contexto, resuelto y validado. `compatible` es falso si hay un solo problema: entonces
    ninguna pantalla combina sus ejecuciones, y la app muestra cada problema."""

    version: str = Field(default=VERSION_APP)
    compatible: bool
    corte: CorteDelContexto
    declaradas: dict[str, UUID] = Field(description="Lo que declaro el contexto, por tipo.")
    ejecuciones: list[EjecucionDelContexto] = Field(
        description="Las declaradas que existen, en el orden de su tipo."
    )
    derivadas: dict[str, UUID] = Field(
        description="Lo que las declaradas leyeron y el contexto no declara: la territorial/v2 y "
        "la geografia de un plan, la version del modelo de un scoring, sus configuraciones."
    )
    problemas: list[ProblemaDeContexto]
    aviso: str = Field(default=AVISO_APP)


# --- las opciones ---------------------------------------------------------------------------------


class ParametrosOpciones(Paginacion):
    model_config = ConfigDict(extra="forbid")

    por_pagina: int = Field(default=20, ge=1, le=100, description="Cortes por pagina; hasta 100.")
    cartera_id: str | None = Field(
        default=None,
        pattern=r"^[A-Z0-9_]{1,32}$",
        description="La cartera cuyos cortes se listan; por omision, la del sistema "
        "(MC_CARTERA_ID).",
    )
    corte_id: UUID | None = Field(
        default=None, description="Con un corte, sus ejecuciones y los contextos que se proponen."
    )


class CarteraOpcion(BaseModel):
    cartera_id: str
    cortes: int
    ultimo_corte: date


class CorteOpcion(BaseModel):
    corte_id: UUID
    fecha_corte: date
    cuentas: int
    creado_en: datetime


class EjecucionesDeTipo(BaseModel):
    tipo: str
    total: int = Field(description="Cuantas tiene el corte; se listan las mas recientes.")
    elementos: list[EjecucionDelContexto]


class ContextoDeclarado(BaseModel):
    corte_id: UUID
    decision_run_id: UUID | None
    analytics_run_id: UUID | None
    scoring_run_id: UUID | None
    optimizacion_run_id: UUID | None
    campo_run_id: UUID | None
    ruteo_run_id: UUID | None


class SugerenciaDeContexto(BaseModel):
    contexto: ContextoDeclarado
    codigo: Literal["DESDE_OPTIMIZACION", "DESDE_ANALYTICS"] = Field(
        description="De que ejecucion parte la cadena: una optimizacion y lo que declaro, o una "
        "ejecucion de analytics y su manifiesto."
    )
    motivo: str


class OpcionesRespuesta(BaseModel):
    """Lo que hace falta para abrir un contexto. Nada se abre solo: una sugerencia es un contexto
    compatible que la persona puede abrir."""

    despacho_id: str
    cartera_id: str
    carteras: list[CarteraOpcion]
    total: int = Field(description="Cuantos cortes tiene la cartera.")
    pagina: int
    por_pagina: int
    cortes: list[CorteOpcion] = Field(description="Del mas reciente al mas antiguo.")
    corte: CorteDelContexto | None = Field(description="El corte pedido, si se pidio uno.")
    ejecuciones: list[EjecucionesDeTipo] = Field(description="Del corte pedido, por tipo.")
    sugerencias: list[SugerenciaDeContexto] = Field(description="Contextos compatibles del corte.")
    aviso: str = Field(default=AVISO_APP)


# --- el resumen -----------------------------------------------------------------------------------


class EstadoFuente(StrEnum):
    DISPONIBLE = "DISPONIBLE"
    """La ejecucion se declaro y publico."""
    NO_DECLARADA = "NO_DECLARADA"
    """El contexto no la declara: no se busca otra."""
    EN_PROCESO = "EN_PROCESO"
    FALLIDA = "FALLIDA"
    """Termino sin publicar: su resultado dice por que."""


class Fuente(BaseModel):
    estado_fuente: EstadoFuente
    run_id: UUID | None
    estado: str | None
    resultado: str | None


class AnalyticsDelResumen(Fuente):
    as_of: date | None = None
    known_at: datetime | None = None
    zona_horaria: str | None = None
    granularidad: str | None = None
    version_catalogo: str | None = None
    hash_catalogo: str | None = None
    cuentas_cohorte: int | None = None
    indicadores: list[IndicadorAnalyticsRespuesta] = Field(default_factory=list)
    embudos: list[EmbudoAnalyticsRespuesta] = Field(default_factory=list)


class DecisionDelResumen(Fuente):
    as_of: date | None = None
    known_at: datetime | None = None
    version_politica: str | None = None
    hash_politica: str | None = None
    cuentas_decididas: int | None = None
    distribucion: dict[str, Any] | None = Field(
        default=None, description="La que conto decision/v2 al publicar: no se cuenta otra vez."
    )


class HistogramaPredicciones(BaseModel):
    salida: str
    cuentas: list[int] = Field(description="Por decimo de [0, 1]; el ultimo incluye el 1.")


class DistribucionPrediccionesRespuesta(BaseModel):
    """Un resumen descriptivo de las predicciones publicadas de un scoring, contado en la base: no
    es una prediccion nueva ni una metrica de analytics/v1."""

    scoring_run_id: UUID
    modelo_version_id: UUID
    publicadas: int
    con_imputacion: int
    medias: dict[str, Decimal | None]
    suma_recuperacion_positiva_esperada_30d: Decimal | None = Field(
        description="La suma de la recuperacion POSITIVA esperada a 30 dias de cada prediccion: no "
        "la neta, no dinero que se vaya a recuperar."
    )
    bins: int
    histogramas: list[HistogramaPredicciones]
    aviso: str = Field(default=AVISO_PREDICCION)


class PredictivoDelResumen(Fuente):
    modelo_version_id: UUID | None = None
    version_modelo: str | None = None
    as_of: date | None = None
    known_at: datetime | None = None
    distribucion: DistribucionPrediccionesRespuesta | None = None
    drift_resumen: dict[str, Any] | None = None


class OptimizacionDelResumen(Fuente):
    fecha_operacion: date | None = None
    solver_status: str | None = None
    objetivo_primario_centavos: int | None = Field(
        default=None, description="El valor de oportunidad predicho del plan, no dinero."
    )
    best_bound: Decimal | None = None
    mip_gap: Decimal | None = None
    solver_seconds: Decimal | None = None
    cuentas_universo: int | None = None
    cuentas_elegibles: int | None = None
    cuentas_asignadas: int | None = None
    por_canal: dict[str, Any] | None = Field(
        default=None, description="La capacidad y el uso por canal, como los publico el plan."
    )


class CampoDelResumen(Fuente):
    fecha_operacion: date | None = None
    conteos: ConteosCampoV2 | None = None
    capacidad: CapacidadCampoV2 | None = None


class RuteoDelResumen(Fuente):
    fecha_operacion: date | None = None
    conteos: ConteosRuteoV2 | None = None
    totales: TotalesRuteoV2 | None = None


class ResumenAppRespuesta(BaseModel):
    """Cada bloque, de la ejecucion que el contexto declara y tal como la publico. Un bloque cuya
    ejecucion no se declaro es NO_DECLARADA, sin un solo cero."""

    corte: CorteDelContexto
    analytics: AnalyticsDelResumen
    decision: DecisionDelResumen
    predictivo: PredictivoDelResumen
    optimizacion: OptimizacionDelResumen
    campo: CampoDelResumen
    ruteo: RuteoDelResumen
    avisos: list[str] = Field(
        default_factory=lambda: [AVISO_APP, AVISO_PREDICCION, AVISO_OPTIMO, AVISO_ATRIBUCION]
    )


# --- el explorador de cuentas ---------------------------------------------------------------------


class OrdenCuentas(StrEnum):
    """Una lista cerrada: cada orden entra por un indice de la base."""

    CUENTA = "CUENTA"
    SALDO_TOTAL = "SALDO_TOTAL"
    DIAS_ATRASO = "DIAS_ATRASO"
    P_RECUPERACION_POSITIVA_30D = "P_RECUPERACION_POSITIVA_30D"
    RECUPERACION_POSITIVA_ESPERADA_30D = "RECUPERACION_POSITIVA_ESPERADA_30D"


TramoAtraso = Literal[tuple(etiqueta for etiqueta, _, _ in TRAMOS_ATRASO)]  # type: ignore[valid-type]


class ParametrosCuentas(Paginacion):
    """El contexto, la pagina, una lista blanca de filtros y un orden de una lista cerrada. Ningun
    otro parametro se acepta, y ninguno es un dato personal."""

    model_config = ConfigDict(extra="forbid")

    por_pagina: int = Field(default=50, ge=1, le=100, description="Cuentas por pagina; hasta 100.")
    corte_id: UUID
    decision_run_id: UUID | None = None
    analytics_run_id: UUID | None = None
    scoring_run_id: UUID | None = None
    optimizacion_run_id: UUID | None = None
    campo_run_id: UUID | None = None
    ruteo_run_id: UUID | None = None
    cuenta: str | None = Field(
        default=None,
        pattern=r"^[0-9a-fA-F-]{4,36}$",
        description="El principio del cuenta_id (cuatro o mas digitos hexadecimales).",
    )
    producto: str | None = Field(default=None, pattern=r"^[A-Z0-9_]{1,20}$")
    atraso_bucket: TramoAtraso | None = None
    prioridad: Literal["BAJA", "MEDIA", "ALTA", "MUY_ALTA"] | None = Field(
        default=None, description="La prioridad final de la decision/v2 del contexto."
    )
    canal: Literal["DIGITAL", "TELEFONICA", "CAMPO"] | None = Field(
        default=None, description="El canal recomendado por la decision/v2 del contexto."
    )
    estado_prediccion: Literal["COMPLETA", "CON_IMPUTACION"] | None = None
    estado_optimizacion: Literal["ASIGNADA", "NO_SELECCIONADA", "NO_ELEGIBLE"] | None = None
    orden: OrdenCuentas = OrdenCuentas.CUENTA
    sentido: Literal["ASC", "DESC"] = "ASC"


class CuentaListadaRespuesta(BaseModel):
    """Una cuenta del corte, con lo que las ejecuciones del contexto publicaron de ella. Un valor
    null es una ejecucion que el contexto no declara, o que no tiene resultado de la cuenta."""

    cuenta_id: UUID
    producto: str
    saldo_total: Decimal
    dias_atraso: int
    atraso_bucket: str
    prioridad: str | None
    canal_recomendado: str | None
    p_recuperacion_positiva_30d: Decimal | None
    recuperacion_positiva_esperada_30d: Decimal | None
    estado_prediccion: str | None
    estado_optimizacion: str | None
    canal_asignado: str | None
    valor_oportunidad_centavos: int | None


class PaginaCuentasApp(Pagina[CuentaListadaRespuesta]):
    corte_id: UUID
    orden: OrdenCuentas
    sentido: str
    aviso: str = Field(default=AVISO_APP)


# --- la Cuenta 360 de la app ----------------------------------------------------------------------


class EstadoSeccion(StrEnum):
    DISPONIBLE = "DISPONIBLE"
    NO_DECLARADA = "NO_DECLARADA"
    """El contexto no declara esa ejecucion."""
    NO_PUBLICADA = "NO_PUBLICADA"
    """La ejecucion no esta EXITOSA: no publico resultados."""
    SIN_RESULTADO = "SIN_RESULTADO"
    """La ejecucion publico, y la cuenta no esta en ella (no era candidata, no es del corte...)."""


class Seccion(BaseModel):
    estado: EstadoSeccion
    run_id: UUID | None


class PresenciaRespuesta(BaseModel):
    primera_observacion: date | None
    ultima_observacion: date | None
    ultimo_corte_cartera: date | None
    estado_presencia: str
    cortes_observados: int
    cortes_ausentes_desde_primera_observacion: int
    salidas_observadas: int
    reingresos_observados: int
    pagos_observados: int


class DecisionDeCuentaSeccion(Seccion):
    as_of: date | None = None
    known_at: datetime | None = None
    prioridad_base: str | None = None
    prioridad_final: str | None = None
    canal_recomendado: str | None = None
    ajuste_total: int | None = None
    motivos: list[str] = Field(default_factory=list, description="Los codigos, en orden.")
    decision_hash: str | None = None


class GeografiaDeCuentaSeccion(Seccion):
    """Su geografia y su base en la territorial/v2 que leyo el plan de campo del contexto."""

    territorial_run_id: UUID | None = None
    geografia_run_id: UUID | None = None
    estado_geografia: str | None = None
    cvegeo: str | None = None
    municipio: str | None = None
    municipio_fuente: str | None = None
    comparacion_municipio: str | None = None
    estado_asignacion: str | None = None
    codigo_base: str | None = None
    nombre_base: str | None = None
    distancia_base_m: Decimal | None = None


class CampoDeCuentaSeccion(Seccion):
    asignacion: AsignacionCampoDeCuentaApp | None = None  # type: ignore[valid-type]
    carga_gestor: CargaGestorRespuesta | None = Field(
        default=None, description="La carga del gestor asignado en el plan."
    )


class RuteoDeCuentaSeccion(Seccion):
    parada: ParadaDeCuentaApp | None = None  # type: ignore[valid-type]


class PredictivoDeCuentaSeccion(Seccion):
    prediccion: PrediccionDeCuentaRespuesta | None = None
    aviso: str = Field(default=AVISO_PREDICCION)


class OptimizacionDeCuentaSeccion(Seccion):
    fecha_operacion: date | None = None
    resultado: ResultadoCuentaRespuesta | None = None
    aviso: str = Field(default=AVISO_OPTIMO)
    limitaciones: list[str] = Field(default=LIMITACIONES)


class Cuenta360AppRespuesta(BaseModel):
    """La cuenta en el contexto: su presencia y su snapshot al corte, y lo que cada ejecucion
    declarada publico de ella. Sin CLIENTE_UNICO: la cuenta es su cuenta_id."""

    cuenta_id: UUID
    despacho_id: str
    cartera_id: str
    corte_id: UUID
    fecha_corte: date
    en_el_corte: bool = Field(description="Si el corte del contexto la trae.")
    presencia: PresenciaRespuesta = Field(description="A la fecha del corte del contexto.")
    snapshot: SnapshotRespuesta | None = Field(description="Su snapshot en el corte del contexto.")
    decision: DecisionDeCuentaSeccion
    geografia: GeografiaDeCuentaSeccion
    campo: CampoDeCuentaSeccion
    ruteo: RuteoDeCuentaSeccion
    predictivo: PredictivoDeCuentaSeccion
    optimizacion: OptimizacionDeCuentaSeccion
    avisos: list[str] = Field(
        default_factory=lambda: [AVISO_APP, AVISO_PREDICCION, AVISO_OPTIMO, AVISO_ATRIBUCION]
    )


class PagosDeCuentaApp(BaseModel):
    """Los movimientos economicos canonicos de la cuenta, cada uno con su atribucion vigente, y el
    resumen de la interpretacion vigente. Un pago observado no es una recuperacion canonica."""

    cuenta_id: UUID
    resumen: ResumenPagosRespuesta
    pagina: PaginaAtribucionesDeCuentaApp  # type: ignore[valid-type]
    aviso_atribucion: str = Field(default=AVISO_ATRIBUCION)


class GestionesDeCuentaApp(BaseModel):
    cuenta_id: UUID
    resumen: LifecycleResumenRespuesta
    pagina: PaginaGestionesApp  # type: ignore[valid-type]
    aviso_atribucion: str = Field(default=AVISO_ATRIBUCION)


# --- el linaje ------------------------------------------------------------------------------------


class NodoLinaje(BaseModel):
    clave: str = Field(description="Su tipo y su identificador publico: la llave de sus aristas.")
    tipo: str = Field(description="CORTE, DECISION_V2, GEOGRAFIA, MODELO, ENTRENAMIENTO...")
    run_id: UUID
    declarado: bool = Field(description="Si lo declara el contexto, o lo leyo una declarada.")
    estado: str | None
    resultado: str | None
    version: str | None
    version_politica: str | None
    hashes: dict[str, str] = Field(description="Cada hash que fija lo que es: firma, politica...")
    as_of: date | None
    known_at: datetime | None
    fecha_operacion: date | None
    iniciada_en: datetime | None
    terminada_en: datetime | None


class AristaLinaje(BaseModel):
    desde: str = Field(description="La clave del nodo que lee.")
    hacia: str = Field(description="La clave del nodo que leyo.")
    relacion: str


class LinajeRespuesta(BaseModel):
    """De donde sale cada resultado del contexto: cada ejecucion con sus versiones y sus hashes, y
    solo las relaciones que cada una declaro al publicarse. Ninguna dependencia se infiere."""

    compatible: bool
    nodos: list[NodoLinaje]
    aristas: list[AristaLinaje]
    aviso: str = Field(default=AVISO_APP)
