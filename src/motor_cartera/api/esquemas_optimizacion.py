"""Los esquemas de optimization/v1 en la API: la politica, las configuraciones, las ejecuciones, sus
resultados por cuenta, el uso de cada pool, cada restriccion, el diagnostico, la explicacion de una
cuenta y los planes de una cuenta.

Un resultado sale con el cuenta_id de su cuenta y con codigos (de pool, de base, de gestor); nunca
con un CLIENTE_UNICO, un nombre, una direccion, un telefono, una coordenada ni un actor_ref. Un
plan optimo es el del modelo de asignacion declarado: no dice que el canal asignado cause la
recuperacion predicha, y no ejecuta nada.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

from motor_cartera.api.esquemas import Pagina, Paginacion
from motor_cartera.db.modelos import EstadoOptimizacion, EstadoVersionOptimizacion
from motor_cartera.optimizacion.politica import AVISO

DecimalFijo = Annotated[
    Decimal, PlainSerializer(lambda d: format(d, "f"), return_type=str, when_used="json")
]
"""Un decimal exacto en JSON, siempre en punto fijo: un cero de 12 decimales es
"0.000000000000", no "0E-12"."""

LIMITACIONES = [
    "Es el optimo del modelo de asignacion declarado, no una estrategia causalmente optima.",
    "El valor es la recuperacion positiva esperada bajo la politica historica, no el beneficio "
    "incremental de contactar.",
    "Un canal asignado es una asignacion de capacidad compatible con las restricciones, no la "
    "evidencia de que ese canal cause mayor recuperacion.",
    "Las capacidades de un benchmark son sinteticas, y las unidades de recurso no son costos "
    "reales.",
    "Un optimo matematico de supuestos incorrectos sigue siendo una mala decision de negocio.",
    "Es un plan de soporte a decision, en sombra: no registra gestiones, no envia mensajes, no "
    "llama, no crea visitas ni cambia decision/v2, campo/v2 o ruteo/v2.",
]


# --- la politica ----------------------------------------------------------------------------------


class PoliticaOptimizacionRespuesta(BaseModel):
    version: str
    version_motor: str
    hash: str = Field(description="SHA-256 de la definicion canonica: lo firma cada ejecucion.")
    algoritmo: str
    definicion: dict[str, Any] = Field(
        description="La formula del valor, los objetivos lexicograficos, la precision, los "
        "canales, la reduccion exacta, la regla de optimalidad, el tratamiento de CAMPO y el "
        "solver."
    )
    huella_solver: dict[str, Any] = Field(
        description="Con que resuelve este servicio: el algoritmo, SciPy, HiGHS, NumPy, Python, "
        "el sistema, la arquitectura y los parametros."
    )
    solver_fingerprint: str
    limitaciones: list[str] = Field(default=LIMITACIONES)
    aviso: str = Field(default=AVISO)


# --- las configuraciones --------------------------------------------------------------------------


class AdmisibilidadEntrada(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canal_decision: str = Field(description="El canal que recomienda decision/v2.")
    canal_operativo: str = Field(description="DIGITAL, TELEFONICA o CAMPO.")
    permitido: bool


class PoolEntrada(BaseModel):
    model_config = ConfigDict(extra="forbid")

    codigo: str
    canal: str = Field(description="DIGITAL, TELEFONICA o CAMPO.")
    tipo_alcance: str = Field(
        description="GLOBAL o BASE (DIGITAL, TELEFONICA); GESTOR_CAMPO (CAMPO, uno por gestor)."
    )
    referencia_alcance: str | None = Field(
        default=None, description="El codigo de la base o del gestor; null en GLOBAL."
    )
    capacidad: int | None = Field(
        default=None,
        description="Cuantas cuentas puede atender. Solo CAMPO puede omitirla: entonces son las "
        "paradas PROGRAMADA de su gestor.",
    )
    unidades_recurso_por_asignacion: int = Field(
        description="Unidades abstractas de esfuerzo por cuenta asignada: no dinero."
    )
    activo: bool


class RestriccionEntrada(BaseModel):
    model_config = ConfigDict(extra="forbid")

    codigo: str
    tipo: str = Field(
        description="MIN_CUENTAS, MAX_CUENTAS, MIN_PARTICIPACION_BPS o MAX_PARTICIPACION_BPS."
    )
    dimension: str = Field(description="PRODUCTO, ATRASO_BUCKET, PRIORIDAD_DECISION o BASE.")
    valor_dimension: str
    limite: int = Field(description="Cuentas, o puntos base (0 a 10,000) en una participacion.")
    activa: bool


class ConfiguracionOptimizacionEntrada(BaseModel):
    """Un esquema cerrado: sin SQL, sin Python, sin expresiones. Cada problema se reporta junto
    con los demas."""

    model_config = ConfigDict(extra="forbid")

    nombre: str
    permitir_predicciones_con_imputacion: bool = Field(
        description="Declarado, sin valor por omision: si una prediccion CON_IMPUTACION puede ser "
        "elegible."
    )
    max_cuentas_atendidas: int | None = None
    max_unidades_recurso: int | None = Field(
        default=None, description="Unidades abstractas de esfuerzo, no dinero."
    )
    admisibilidad: list[AdmisibilidadEntrada] = Field(
        description="La matriz explicita: que canal operativo es admisible para cada canal de "
        "decision/v2. Admisible no es efectivo."
    )
    pools: list[PoolEntrada]
    restricciones: list[RestriccionEntrada] = Field(default_factory=list)
    supuestos: str | None = Field(
        default=None, description="Para personas; no entra en el hash. P. ej. SUPUESTOS SINTETICOS."
    )


class ConfiguracionOptimizacionRespuesta(BaseModel):
    configuracion_id: UUID
    nombre: str
    version_configuracion: str
    hash_contenido: str
    estado: EstadoVersionOptimizacion
    permitir_predicciones_con_imputacion: bool
    max_cuentas_atendidas: int | None
    max_unidades_recurso: int | None
    pools: int
    pools_activos: int
    restricciones: int
    restricciones_activas: int
    metadatos: dict[str, Any]
    creada_en: datetime
    publicada_en: datetime | None


class ConfiguracionOptimizacionDetalle(ConfiguracionOptimizacionRespuesta):
    admisibilidad: list[AdmisibilidadEntrada]
    detalle_pools: list[PoolEntrada]
    detalle_restricciones: list[RestriccionEntrada]
    hash_verificado: bool = Field(
        description="Si su hash, vuelto a calcular con lo que hay en sus tablas, es el guardado."
    )
    reutilizada: bool = False
    supuestos: str | None = None


class ParametrosConfiguraciones(Paginacion):
    model_config = ConfigDict(extra="forbid")

    estado: EstadoVersionOptimizacion | None = None


PaginaConfiguracionesOptimizacion = Pagina[ConfiguracionOptimizacionRespuesta]


# --- las ejecuciones ------------------------------------------------------------------------------


class EjecucionOptimizacionEntrada(BaseModel):
    """Las entradas exactas de un plan: nunca "el ultimo scoring", "la ultima decision", "la ultima
    ruta" ni "la ultima configuracion"."""

    model_config = ConfigDict(extra="forbid")

    scoring_run_id: UUID = Field(description="Un scoring EXITOSA de un modelo PUBLICADA.")
    decision_run_id: UUID = Field(description="Una decision/v2 EXITOSA del mismo corte.")
    configuracion_id: UUID = Field(description="Una configuracion PUBLICADA.")
    fecha_operacion: date = Field(description="La del ruteo, si se declara uno.")
    ruteo_run_id: UUID | None = Field(
        default=None,
        description="Un ruteo/v2 EXITOSA cuyo plan de campo es de la misma decision/v2: "
        "obligatorio si la configuracion usa CAMPO o BASE.",
    )


class EjecucionOptimizacionRespuesta(BaseModel):
    optimizacion_run_id: UUID
    estado: EstadoOptimizacion
    resultado: str | None
    scoring_run_id: UUID
    modelo_version_id: UUID
    decision_run_id: UUID
    ruteo_run_id: UUID | None
    campo_run_id: UUID | None
    configuracion_id: UUID
    corte_id: UUID
    fecha_corte: date
    fecha_operacion: date
    version_motor: str
    version_politica: str
    hash_politica: str
    hash_configuracion: str
    solver_fingerprint: str
    huella_solver: dict[str, Any]
    firma_inputs: str
    cuentas_universo: int
    cuentas_elegibles: int
    cuentas_no_elegibles: int
    cuentas_con_imputacion: int
    cuentas_retenidas: int
    cuentas_asignadas: int
    cuentas_publicadas: int
    clases: int = Field(description="Las clases de dominancia de las elegibles.")
    variables: int
    restricciones_solver: int
    elementos_no_nulos: int
    objetivo_primario_centavos: int | None = Field(
        description="El valor de oportunidad predicho del plan: no dinero que se vaya a recuperar."
    )
    objetivo_secundario: int | None = Field(
        description="La suma de p_contacto_14d de las asignadas, en unidades de 1e-8: solo un "
        "desempate."
    )
    unidades_recurso: int | None
    desempate_canonico: int | None
    solver_status: str | None
    best_bound: DecimalFijo | None
    mip_gap: DecimalFijo | None
    solver_seconds: DecimalFijo
    hash_plan: str | None
    certificado: dict[str, Any] | None = Field(
        description="El modelo, la reduccion y cada nivel con su estado, su objetivo, su cota, su "
        "gap, su metodo y sus variables libres: la evidencia de optimalidad."
    )
    resumen: dict[str, Any] | None
    tiempos: dict[str, Any] | None
    trabajo_id: UUID | None
    iniciada_en: datetime
    terminada_en: datetime | None
    duracion_segundos: float | None
    detalle: str | None
    limitaciones: list[str] = Field(default=LIMITACIONES)
    aviso: str = Field(default=AVISO)


class ParametrosEjecuciones(Paginacion):
    model_config = ConfigDict(extra="forbid")

    estado: EstadoOptimizacion | None = None
    configuracion_id: UUID | None = None
    scoring_run_id: UUID | None = None


PaginaEjecucionesOptimizacion = Pagina[EjecucionOptimizacionRespuesta]


class ResultadoCuentaRespuesta(BaseModel):
    cuenta_id: UUID
    estado: str = Field(description="ASIGNADA, NO_SELECCIONADA o NO_ELEGIBLE.")
    motivo: str = Field(description="Un hecho, nunca la restriccion que 'causo' algo.")
    canal_decision: str
    canal_asignado: str | None
    pool: str | None
    prioridad: str
    producto: str
    atraso_bucket: str
    base: str | None
    gestor: str | None = Field(description="Solo en CAMPO: el gestor de su parada PROGRAMADA.")
    orden_ruteo_original: int | None = Field(
        description="Solo en CAMPO: su orden en la ruta de ruteo/v2, que el plan no cambia."
    )
    valor_oportunidad_centavos: int
    p_contacto_14d: DecimalFijo = Field(description="Solo desempata entre planes del mismo valor.")
    unidades_recurso: int | None
    retenida: bool


class ParametrosResultados(Paginacion):
    """Una lista blanca de filtros, en la base."""

    model_config = ConfigDict(extra="forbid")

    estado: str | None = Field(default=None, pattern=r"^(ASIGNADA|NO_SELECCIONADA|NO_ELEGIBLE)$")
    canal: str | None = Field(default=None, pattern=r"^(DIGITAL|TELEFONICA|CAMPO)$")
    pool: str | None = Field(default=None, pattern=r"^[A-Z0-9_-]{1,32}$")
    producto: str | None = Field(default=None, pattern=r"^[A-Z]{1,20}$")
    prioridad: str | None = Field(default=None, pattern=r"^(BAJA|MEDIA|ALTA|MUY_ALTA)$")
    base: str | None = Field(default=None, pattern=r"^[A-Z0-9_-]{1,32}$")


class PaginaResultadosOptimizacion(Pagina[ResultadoCuentaRespuesta]):
    optimizacion_run_id: UUID
    aviso: str = Field(default=AVISO)


class UsoPoolRespuesta(BaseModel):
    pool: str
    canal: str
    tipo_alcance: str
    referencia_alcance: str | None
    capacidad_declarada: int | None
    capacidad: int = Field(description="La efectiva en esta ejecucion.")
    elegibles: int
    retenidas: int
    utilizada: int
    holgura: int
    utilizacion: DecimalFijo | None
    unidades_por_asignacion: int
    unidades_consumidas: int


class ParametrosCapacidad(Paginacion):
    model_config = ConfigDict(extra="forbid")

    canal: str | None = Field(default=None, pattern=r"^(DIGITAL|TELEFONICA|CAMPO)$")


class PaginaCapacidadOptimizacion(Pagina[UsoPoolRespuesta]):
    optimizacion_run_id: UUID
    por_canal: dict[str, Any] | None = Field(description="La capacidad y el uso por canal.")


class RestriccionResultadoRespuesta(BaseModel):
    codigo: str
    tipo: str
    dimension: str | None
    valor_dimension: str | None
    operador: str
    lhs: int
    rhs: int
    holgura: int
    binding: bool = Field(description="Holgura cero, en enteros.")
    cuentas_grupo: int | None
    total: int | None
    limite_resuelto: int | None = Field(
        description="En una participacion: ceil (minimo) o floor (maximo) de bps por total."
    )


class ParametrosRestricciones(Paginacion):
    model_config = ConfigDict(extra="forbid")

    binding: bool | None = None
    tipo: str | None = Field(default=None, pattern=r"^[A-Z_]{1,24}$")


class PaginaRestriccionesOptimizacion(Pagina[RestriccionResultadoRespuesta]):
    optimizacion_run_id: UUID


class DiagnosticoRespuesta(BaseModel):
    optimizacion_run_id: UUID
    estado: EstadoOptimizacion
    resultado: str | None
    solver_status: str | None
    detalle: str | None
    diagnostico: dict[str, Any] | None = Field(
        description="El prechequeo (errores y advertencias) y, si el solver dijo que era "
        "infactible, el diagnostico de relajacion: una relajacion minima, no un IIS."
    )
    certificado: dict[str, Any] | None
    plan_publicado: bool = Field(description="Solo una EXITOSA publico un plan.")


class ExplicacionCuentaRespuesta(BaseModel):
    optimizacion_run_id: UUID
    cuenta_id: UUID
    resultado: ResultadoCuentaRespuesta
    canales_admisibles: list[str]
    pools_elegibles: list[str]
    hechos: dict[str, Any] = Field(description="Los hechos de su elegibilidad, de sus entradas.")
    grupos: list[dict[str, Any]] = Field(description="Sus pertenencias a cada cobertura activa.")
    explicacion: str
    desempate: str = Field(
        default="p_contacto_14d solo desempata entre planes del mismo valor de oportunidad: no es "
        "la probabilidad de exito del canal asignado."
    )
    aviso: str = Field(default=AVISO)


class PlanDeCuentaRespuesta(BaseModel):
    optimizacion_run_id: UUID
    configuracion_id: UUID
    fecha_operacion: date
    corte_id: UUID
    estado: str
    motivo: str
    canal_decision: str
    canal_asignado: str | None
    pool: str | None
    valor_oportunidad_centavos: int
    p_contacto_14d: DecimalFijo


class PaginaPlanesDeCuenta(Pagina[PlanDeCuentaRespuesta]):
    cuenta_id: UUID
    aviso: str = Field(default=AVISO)
