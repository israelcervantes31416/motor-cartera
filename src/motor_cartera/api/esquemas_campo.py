"""Los esquemas de la configuracion de campo y de campo/v2 en la API.

Ninguna respuesta lleva una coordenada, una celda de la cuadricula, un recuadro ni un centroide: la
particion sale como cuatro identificadores (zona, subzona, tramo y microtramo), y un gestor como su
codigo, sin ningun dato de una persona. La capacidad es un cupo operativo declarado, no la
factibilidad de una ruta.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from motor_cartera.api.esquemas import Pagina, Paginacion
from motor_cartera.api.esquemas_geografia import CorteRespuesta
from motor_cartera.campo_v2.configuracion import DIAS, TipoExcepcion
from motor_cartera.campo_v2.politica import EstadoCampo, EstadoJornada, NivelUnidad
from motor_cartera.db.modelos import PATRON_CODIGO_BASE, EstadoCampoV2

AVISO_CAMPO_V2 = (
    "campo/v2 dice que candidata de campo de una decision/v2 quedo con que gestor de su base "
    "territorial en una fecha, bajo la capacidad declarada (una cuenta por unidad), y en que "
    "particion operativa. Es capacidad declarada, no factibilidad vial: no usa distancias, tiempos "
    "de traslado ni rutas (v0.13), y no mueve cuentas entre bases. Sin coordenadas."
)

HORA = r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$|^24:00$"

DiaSemana = StrEnum("DiaSemana", {d: d for d in DIAS})


# --- la configuracion -----------------------------------------------------------------------------


class VentanaEntrada(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inicio: str = Field(pattern=HORA, examples=["09:00"])
    fin: str = Field(pattern=HORA, examples=["14:00"])


class ExcepcionEntrada(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fecha: date
    tipo: TipoExcepcion
    motivo: str | None = Field(default=None, max_length=120, examples=["Festivo"])
    ventanas: list[VentanaEntrada] | None = Field(
        default=None, description="Solo con VENTANAS_ESPECIALES: las ventanas de ese dia."
    )


class CalendarioEntrada(BaseModel):
    model_config = ConfigDict(extra="forbid")

    codigo: str = Field(pattern=PATRON_CODIGO_BASE, examples=["LV_PARTIDO"])
    semana: dict[DiaSemana, list[VentanaEntrada]] = Field(
        default_factory=dict,
        description="Las ventanas de cada dia que tiene jornada, en hora local. Un dia ausente o "
        "sin ventanas no tiene jornada.",
    )
    excepciones: list[ExcepcionEntrada] = Field(default_factory=list)


class GestorEntrada(BaseModel):
    """Un gestor de campo: un codigo opaco, nunca un nombre, un correo ni un telefono."""

    model_config = ConfigDict(extra="forbid")

    codigo: str = Field(pattern=PATRON_CODIGO_BASE, examples=["GESTOR_001"])
    base: str = Field(pattern=PATRON_CODIGO_BASE, description="El codigo de su base.")
    calendario: str = Field(pattern=PATRON_CODIGO_BASE)
    capacidad_cuentas_dia: StrictInt = Field(
        description="Su cupo operativo declarado, de 1 a 1,000 cuentas al dia. No se deriva de "
        "horas ni de distancias."
    )
    activo: StrictBool


class ConfiguracionCampoEntrada(BaseModel):
    """Una version completa de la configuracion de campo, sobre una version de bases declarada."""

    model_config = ConfigDict(extra="forbid")

    nombre: str = Field(min_length=1, max_length=120)
    bases_version_id: UUID = Field(
        description="La version de bases sobre la que se construye: la de las bases de sus "
        "gestores."
    )
    zona_horaria: str = Field(
        max_length=64,
        examples=["America/Mexico_City"],
        description="La zona IANA de las horas de sus calendarios: explicita.",
    )
    calendarios: list[CalendarioEntrada] = Field(min_length=1)
    gestores: list[GestorEntrada] = Field(min_length=1)


class VentanaRespuesta(BaseModel):
    inicio: str
    fin: str


class ExcepcionRespuesta(BaseModel):
    fecha: date
    tipo: str
    motivo: str | None
    ventanas: list[VentanaRespuesta]


class CalendarioRespuesta(BaseModel):
    calendario_id: UUID
    codigo: str
    semana: dict[str, list[VentanaRespuesta]] = Field(
        description="Los siete dias, de LUNES a DOMINGO, con sus ventanas (vacia: sin jornada)."
    )
    excepciones: list[ExcepcionRespuesta]


class GestorRespuesta(BaseModel):
    gestor_id: UUID
    codigo: str
    base: str
    calendario: str
    capacidad_cuentas_dia: int
    activo: bool


class ConfiguracionCampoRespuesta(BaseModel):
    configuracion_campo_version_id: UUID
    nombre: str
    bases_version_id: UUID
    version_configuracion: str
    hash_configuracion: str = Field(
        description="SHA-256 de la configuracion en forma canonica: el mismo contenido es la misma "
        "version."
    )
    zona_horaria: str
    estado: str
    calendarios: int
    ventanas: int
    excepciones: int
    gestores: int
    gestores_activos: int
    capacidad_declarada: int = Field(
        description="La suma de los cupos de sus gestores activos: un tope, no la capacidad de una "
        "fecha."
    )
    por_base: dict[str, dict[str, int]] = Field(
        description="Sus gestores, activos y cupo declarado, por codigo de base."
    )
    creada_en: datetime
    publicada_en: datetime
    reutilizada: bool = Field(
        default=False,
        description="True si la configuracion ya existia y se devolvio su version, sin crear otra.",
    )


class ConfiguracionCampoDetalleRespuesta(ConfiguracionCampoRespuesta):
    hash_verificado: bool = Field(
        description="Si el hash, vuelto a calcular con lo que hay en sus tablas, es el que se "
        "guardo: que nada cambio desde que se publico."
    )
    detalle_calendarios: list[CalendarioRespuesta]
    detalle_gestores: list[GestorRespuesta]


class PaginaConfiguracionesCampo(Pagina[ConfiguracionCampoRespuesta]):
    pass


# --- los planes -----------------------------------------------------------------------------------


class PlanCampoEntrada(BaseModel):
    """Lo que fija un plan: todas sus entradas, declaradas. Ninguna se toma por omision."""

    model_config = ConfigDict(extra="forbid")

    corte_id: UUID
    decision_run_id: UUID = Field(description="Una decision/v2 EXITOSA de ese corte.")
    territorial_run_id: UUID = Field(description="Una territorial/v2 EXITOSA de ese corte.")
    configuracion_campo_version_id: UUID = Field(
        description="Una configuracion de campo sobre la misma version de bases que territorial/v2."
    )
    fecha_operacion: date = Field(
        description="El dia, local en la zona de la configuracion, que se planea: no antes del "
        "corte."
    )
    version_politica: str | None = Field(
        default=None, description="Por omision, campo-public-v2, la unica que se aplica."
    )


class ConfiguracionDelPlan(BaseModel):
    configuracion_campo_version_id: UUID
    nombre: str
    hash_configuracion: str
    zona_horaria: str


class ParticionDeLaPolitica(BaseModel):
    srid: int = Field(description="El de la cuadricula metrica: EPSG:6372.")
    tramo_m: int
    microtramo_m: int
    redondeo_m: str = Field(description="Cada coordenada metrica se toma al milimetro.")


class ConteosCampoV2(BaseModel):
    cuentas_evaluadas_decision: int = Field(description="Las que decidio su decision/v2.")
    candidatas_campo: int = Field(description="Las de canal CAMPO: el universo del plan.")
    asignadas: int
    revision_territorial: int
    sin_gestor: int = Field(description="SIN_GESTOR_DISPONIBLE.")
    sin_capacidad: int
    rebalanceadas: int = Field(
        description="Asignadas a otro gestor que el propietario preferido de su microtramo."
    )


class CapacidadCampoV2(BaseModel):
    capacidad_total: int = Field(
        description="La suma de las capacidades efectivas de sus gestores en la fecha."
    )
    capacidad_utilizada: int = Field(description="Las asignadas: una cuenta es una unidad.")
    utilizacion: Decimal | None = Field(description="La utilizada entre la total, de 0 a 1.")


class PlanCampoV2Respuesta(BaseModel):
    campo_run_id: UUID
    estado: EstadoCampoV2
    resultado: str | None = Field(
        description="PLAN_PUBLICADO, YA_GENERADO, DATOS_INCONSISTENTES, ERROR_INTERNO... null "
        "mientras esta EN_PROCESO."
    )
    corte: CorteRespuesta
    fecha_operacion: date
    decision_run_id: UUID
    territorial_run_id: UUID
    bases_version_id: UUID
    configuracion: ConfiguracionDelPlan
    version_motor: str
    version_politica: str
    hash_politica: str
    particion: ParticionDeLaPolitica | None = Field(
        description="La cuadricula de su politica, si este servicio la conoce."
    )
    firma_inputs: str = Field(
        description="SHA-256 de sus entradas: las mismas entradas son el mismo plan."
    )
    conteos: ConteosCampoV2
    capacidad: CapacidadCampoV2
    distribucion: dict[str, Any] | None = Field(
        description="Por estado, por motivo, por base (candidatas, asignadas, capacidad y "
        "utilizacion), las unidades por nivel, la jornada de los gestores y el balance entre los "
        "disponibles. Un diagnostico, no una optimizacion."
    )
    trabajo_id: UUID | None
    iniciada_en: datetime
    terminada_en: datetime | None
    duracion_segundos: float | None
    detalle: str | None
    aviso: str = Field(default=AVISO_CAMPO_V2)


class PaginaPlanesCampoV2(Pagina[PlanCampoV2Respuesta]):
    pass


class ParametrosPlanes(Paginacion):
    corte_id: UUID | None = Field(default=None, description="Solo los de este corte.")
    fecha_operacion: date | None = Field(default=None, description="Solo los de esta fecha.")


class BaseDelCampo(BaseModel):
    base_id: UUID
    codigo: str


class GestorDelCampo(BaseModel):
    gestor_id: UUID
    codigo: str


class AsignacionCampoRespuesta(BaseModel):
    """La asignacion de una candidata. Sin coordenadas: su particion son cuatro identificadores."""

    cuenta_id: UUID
    cliente_unico: str
    estado: EstadoCampo
    prioridad: str = Field(description="La prioridad final de decision/v2.")
    orden_prioridad: int | None = Field(
        description="Su lugar en la cola de capacidad de su base. No es un orden de visita."
    )
    base: BaseDelCampo | None
    gestor: GestorDelCampo | None
    municipio: str | None = Field(description="El CVEGEO de su subzona.")
    zona_id: UUID | None
    subzona_id: UUID | None
    tramo_id: UUID | None
    microtramo_id: UUID | None
    es_rebalanceada: bool
    motivos: list[dict[str, Any]]


class ParametrosAsignacionesCampo(Paginacion):
    estado: EstadoCampo | None = None
    base: str | None = Field(default=None, pattern=PATRON_CODIGO_BASE, description="Su codigo.")
    gestor: str | None = Field(
        default=None, pattern=PATRON_CODIGO_BASE, description="El codigo de un gestor."
    )
    zona: UUID | None = None
    subzona: UUID | None = None
    tramo: UUID | None = None
    microtramo: UUID | None = None
    prioridad: str | None = Field(default=None, pattern="^(BAJA|MEDIA|ALTA|MUY_ALTA)$")


class PaginaAsignacionesCampo(Pagina[AsignacionCampoRespuesta]):
    campo_run_id: UUID


class VentanaDelDia(BaseModel):
    inicio: str = Field(description="Hora local, HH:MM.")
    fin: str
    inicio_en: datetime = Field(description="El instante, en la zona de la configuracion.")
    fin_en: datetime


class CargaGestorRespuesta(BaseModel):
    gestor: GestorDelCampo
    base: BaseDelCampo
    estado_jornada: EstadoJornada
    capacidad: int = Field(
        description="Su capacidad efectiva en la fecha: 0 si no esta disponible."
    )
    asignadas: int
    utilizacion: Decimal | None
    microtramos: int
    tramos: int
    rebalanceadas: int = Field(description="Las que recibio sin ser el propietario preferido.")
    ventanas: list[VentanaDelDia] = Field(
        description="Las ventanas de su jornada en la fecha, para el ruteo de v0.13."
    )


class ParametrosCargas(Paginacion):
    base: str | None = Field(default=None, pattern=PATRON_CODIGO_BASE)


class PaginaCargasCampo(Pagina[CargaGestorRespuesta]):
    campo_run_id: UUID


class UnidadCampoRespuesta(BaseModel):
    """Una unidad de la particion que ocupo el plan. Sin geometria, celda ni centroide."""

    unidad_id: UUID
    nivel: NivelUnidad
    padre_id: UUID | None
    base: BaseDelCampo
    cve_ent: str
    cvegeo: str | None
    candidatas: int
    asignadas: int
    propietario_preferido: str | None = Field(
        description="Solo en un microtramo: el codigo del primer gestor de su ranking."
    )


class ParametrosUnidades(Paginacion):
    nivel: NivelUnidad | None = None
    padre: UUID | None = Field(default=None, description="Las hijas de esta unidad.")
    base: str | None = Field(default=None, pattern=PATRON_CODIGO_BASE)


class PaginaUnidadesCampo(Pagina[UnidadCampoRespuesta]):
    campo_run_id: UUID


class AsignacionCampoDeCuentaRespuesta(AsignacionCampoRespuesta):
    campo_run_id: UUID
    fecha_operacion: date
    corte: CorteRespuesta
    configuracion_campo_version_id: UUID
    version_politica: str


class PaginaCampoDeCuenta(Pagina[AsignacionCampoDeCuentaRespuesta]):
    cuenta_id: UUID
    cliente_unico: str
