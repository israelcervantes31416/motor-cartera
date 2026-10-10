"""Lo que la API del lifecycle de cobranza, de la atribucion y de la evaluacion de promesas recibe y
devuelve.

Las entradas no admiten campos que no conocen: un `registrado_en` lo pone el reloj de la base, y un
`plazo` o una periodicidad no generan cuotas, asi que mandarlos es un 422 y no un dato que se
ignora en silencio. Los vocabularios salen de `lifecycle.reglas`, como los demas salen de sus
contratos. Siempre por identificadores publicos; los importes, como texto.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, computed_field

from motor_cartera.api.esquemas import (
    ArtefactoRespuesta,
    MovimientoRespuesta,
    Pagina,
    Paginacion,
)
from motor_cartera.atribucion.reglas import VERSION_ATRIBUCION
from motor_cartera.db.modelos import PATRON_ACTOR
from motor_cartera.lifecycle.consultas import Dominio
from motor_cartera.lifecycle.reglas import (
    Canal,
    EstadoConvenio,
    EstadoGestion,
    EstadoPromesa,
    Medio,
    NivelContacto,
    OrigenRegistro,
    ResultadoGestion,
    ResultadoVisita,
    TipoEvento,
)

AVISO_LIFECYCLE = (
    "Eventos operacionales que registra Motor Cartera: lo que la cobranza hizo, con su momento de "
    "negocio (ocurrido_en) y su momento de registro (registrado_en). No son una fuente oficial del "
    "acreedor, y lo que un corte dice de una promesa o de un plan es una observacion de la fuente, "
    "no un evento."
)


def _actor():
    return Field(
        default=None,
        pattern=PATRON_ACTOR,
        description="Quien hizo la accion, como referencia opaca (un identificador, no un "
        "nombre ni un correo). No es el Gestor de pagos/v1 ni un gestor canonico.",
    )


def _texto():
    return Field(
        default=None,
        min_length=1,
        max_length=500,
        description="Texto libre, sin datos personales: un telefono, un correo, una tarjeta, una "
        "CURP o un RFC se rechazan con 422 DATOS_PERSONALES.",
    )


def _importe():
    return Field(gt=0, max_digits=14, decimal_places=2)


# --- lo que se recibe -----------------------------------------------------------------------------


class VisitaEntrada(BaseModel):
    """El detalle de una gestion de CAMPO."""

    model_config = ConfigDict(extra="forbid")

    resultado: ResultadoVisita = Field(
        description="Que encontro: NO_LOCALIZADO, SIN_CONTACTO, CONTACTO_TERCERO, "
        "CONTACTO_TITULAR, DOMICILIO_NO_VALIDO u OTRO. Tiene que corresponder al nivel de contacto "
        "de la gestion."
    )
    inicio: AwareDatetime | None = Field(default=None, description="Cuando empezo, si se sabe.")
    fin: AwareDatetime | None = Field(default=None, description="Cuando termino, si se sabe.")
    observacion: str | None = _texto()


EJEMPLO_GESTION = {
    "ocurrido_en": "2026-09-20T10:15:00-06:00",
    "canal": "TELEFONICA",
    "medio": "LLAMADA",
    "nivel_contacto": "CONTACTO_TITULAR",
    "resultado": "PROMESA",
    "actor_ref": "AGT-0007",
    "observacion": "Acepta pagar el viernes.",
}


class GestionEntrada(BaseModel):
    """Una gestion de cobranza. `ocurrido_en` es cuando paso, con su zona horaria; cuando se
    registra lo pone la base."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [EJEMPLO_GESTION]})

    ocurrido_en: AwareDatetime
    canal: Canal
    medio: Medio | None = Field(
        default=None,
        description="Precisa el canal: LLAMADA en TELEFONICA; SMS, WHATSAPP o EMAIL en DIGITAL.",
    )
    nivel_contacto: NivelContacto
    resultado: ResultadoGestion
    actor_ref: str | None = _actor()
    observacion: str | None = _texto()
    visita: VisitaEntrada | None = Field(
        default=None, description="Obligatoria en una gestion de CAMPO, y solo en ella."
    )


class CierreEntrada(BaseModel):
    """Una anulacion (lo registrado por error) o una cancelacion (lo que dejo de valer)."""

    model_config = ConfigDict(extra="forbid")

    ocurrido_en: AwareDatetime = Field(
        description="Cuando se decidio. No puede ser anterior a lo que anula o cancela."
    )
    motivo: str = Field(min_length=1, max_length=500, description="Por que, sin datos personales.")
    actor_ref: str | None = _actor()


class PromesaEntrada(BaseModel):
    """Una promesa de pago de una gestion con resultado PROMESA."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{"monto_prometido": "1000.00", "fecha_limite": "2026-09-25"}]
        },
    )

    monto_prometido: Decimal = _importe()
    fecha_limite: date = Field(
        description="El ultimo dia, en la hora local de la fuente, en que un pago la cumple."
    )
    ocurrido_en: AwareDatetime | None = Field(
        default=None, description="Cuando se acordo. Por omision, el momento de su gestion."
    )
    actor_ref: str | None = _actor()


class CuotaEntrada(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fecha_vencimiento: date
    monto: Decimal = _importe()


class ConvenioEntrada(BaseModel):
    """Un convenio de una gestion con resultado CONVENIO. Las cuotas se declaran una por una: no se
    generan de un plazo ni de una periodicidad."""

    model_config = ConfigDict(extra="forbid")

    monto_total_acordado: Decimal = _importe()
    fecha_inicio: date
    fecha_fin: date | None = None
    cuotas: list[CuotaEntrada] = Field(
        default_factory=list,
        max_length=360,
        description="El calendario, si se acordo uno: suman exactamente el monto total, vencen en "
        "fechas estrictamente crecientes y dentro de la vigencia. Vacio, el convenio no tiene "
        "calendario.",
    )
    ocurrido_en: AwareDatetime | None = Field(
        default=None, description="Cuando se acordo. Por omision, el momento de su gestion."
    )
    actor_ref: str | None = _actor()


# --- lo que se devuelve ---------------------------------------------------------------------------


class EventoLifecycleRespuesta(BaseModel):
    """Un evento operacional: que se registro, cuando paso, cuando se registro y por que llave."""

    evento_id: UUID
    tipo_evento: TipoEvento
    ocurrido_en: datetime = Field(description="Cuando paso, en el negocio.")
    registrado_en: datetime = Field(description="Cuando Motor Cartera lo recibio.")
    origen_registro: OrigenRegistro
    actor_ref: str | None
    motivo: str | None = Field(description="En una anulacion o una cancelacion, por que.")
    evento_relacionado_id: UUID | None = Field(
        description="El evento que anula, cancela o detalla; null en una GESTION_REGISTRADA."
    )
    version_evento: str
    idempotency_key: str


class VisitaRespuesta(BaseModel):
    visita_id: UUID
    resultado: ResultadoVisita
    inicio: datetime | None
    fin: datetime | None
    observacion: str | None


class GestionRespuesta(BaseModel):
    """Una gestion, con su estado, su evento y lo que nacio de ella."""

    gestion_id: UUID
    cuenta_id: UUID
    cliente_unico: str
    estado: EstadoGestion = Field(
        description="VIGENTE, o ANULADA si un GESTION_ANULADA la anulo: se registro por error. Una "
        "anulada sigue aqui, con su anulacion, y deja de contar en el estado vigente de la cuenta."
    )
    ocurrido_en: datetime
    registrado_en: datetime
    canal: Canal
    medio: Medio | None
    nivel_contacto: NivelContacto = Field(
        description="Con quien se hablo. Hablar con el titular no es un resultado favorable."
    )
    resultado: ResultadoGestion
    actor_ref: str | None
    observacion: str | None
    visita: VisitaRespuesta | None = Field(description="Su visita, si es de CAMPO.")
    promesa_id: UUID | None = Field(description="La promesa que nacio de ella: GET /promesas/{id}.")
    convenio_id: UUID | None = Field(description="El convenio que nacio de ella.")
    evento: EventoLifecycleRespuesta
    anulacion: EventoLifecycleRespuesta | None


class ParametrosGestiones(Paginacion):
    desde: date | None = Field(
        default=None, description="Ocurridas desde este dia, inclusive, en la zona de la fuente."
    )
    hasta: date | None = Field(default=None, description="Hasta este dia, inclusive.")
    canal: Canal | None = None
    nivel_contacto: NivelContacto | None = None
    resultado: ResultadoGestion | None = None
    estado: EstadoGestion | None = Field(
        default=None, description="Solo las VIGENTE o solo las ANULADA; por omision, todas."
    )


class PaginaGestiones(Pagina[GestionRespuesta]):
    cuenta_id: UUID
    cliente_unico: str
    aviso: str = Field(default=AVISO_LIFECYCLE)


AVISO_EVALUACION = (
    "Evaluacion versionada a una fecha de corte: observa, en los movimientos economicos "
    "interpretados de la cuenta, la recuperacion compatible con la promesa. No afirma que la "
    "promesa, ni la gestion de la que nacio, haya producido esa recuperacion."
)


class EvaluacionDePromesaRespuesta(BaseModel):
    """Lo que una evaluacion concluyo de una promesa a su fecha de corte."""

    evaluacion_run_id: UUID
    version_evaluacion: str
    as_of: date = Field(description="Su fecha de corte: se observo hasta el final de ese dia.")
    estado: Literal["PENDIENTE", "CUMPLIDA", "PARCIAL", "INCUMPLIDA", "CANCELADA", "NO_EVALUABLE"]
    monto_observado: Decimal = Field(
        description="Lo que suman los movimientos compatibles: PAGO de la cuenta, recibidos desde "
        "que se acordo hasta su fecha limite (o su fecha de corte, si es antes), sin los que un "
        "reverso anulo hasta la fecha de corte."
    )
    movimientos_compatibles: int
    primer_movimiento_en: datetime | None
    ultimo_movimiento_en: datetime | None
    motivos: list[dict] = Field(description="Por que: [{'codigo': ...}], con lo que lo explica.")
    aviso: str = Field(default=AVISO_EVALUACION)


class PromesaRespuesta(BaseModel):
    """Una promesa: lo que se prometio, su estado operativo y su ultima evaluacion."""

    promesa_id: UUID
    cuenta_id: UUID
    cliente_unico: str
    gestion_id: UUID = Field(description="La gestion de la que nacio: su linaje operacional.")
    monto_prometido: Decimal
    fecha_limite: date
    creada_en: datetime = Field(description="Cuando se acordo: el ocurrido_en de su evento.")
    registrada_en: datetime
    version_modelo: str
    estado_operativo: EstadoPromesa = Field(
        description="VIGENTE, CANCELADA (un PROMESA_CANCELADA) o ANULADA (se anulo su gestion). "
        "No dice si se cumplio: eso lo dice su evaluacion."
    )
    ultima_evaluacion: EvaluacionDePromesaRespuesta | None = Field(
        description="La de su fecha de corte mas reciente, de una evaluacion EXITOSA; null si "
        "ninguna la ha evaluado."
    )
    evento: EventoLifecycleRespuesta
    cancelacion: EventoLifecycleRespuesta | None
    anulacion: EventoLifecycleRespuesta | None = Field(
        description="La anulacion de su gestion, si la hubo."
    )


class ParametrosPromesas(Paginacion):
    estado: EstadoPromesa | None = Field(
        default=None, description="Solo las de ese estado operativo; por omision, todas."
    )


class PaginaPromesas(Pagina[PromesaRespuesta]):
    cuenta_id: UUID
    cliente_unico: str
    aviso: str = Field(default=AVISO_LIFECYCLE)


AVISO_CONVENIO = (
    "Un convenio es un acuerdo operacional registrado, no un ledger: ningun movimiento se aplica a "
    "una cuota sin reglas que lo justifiquen, asi que el estado de cada cuota no se evalua. La "
    "recuperacion observada durante el convenio es lo que la interpretacion vigente de los pagos "
    "observa en la cuenta en su vigencia, no el pago de sus cuotas."
)


class CuotaRespuesta(BaseModel):
    numero: int
    fecha_vencimiento: date
    monto: Decimal


class RecuperacionObservadaRespuesta(BaseModel):
    monto: Decimal = Field(
        description="Los PAGO de la cuenta en la vigencia del convenio que ningun reverso anulo."
    )
    movimientos: int
    desde: date
    hasta: date | None = Field(description="El fin del convenio; null si no tiene.")
    horizonte_pagos: datetime | None = Field(
        description="El pago observado mas reciente de la cartera: hasta donde llegan los datos."
    )
    version_motor: str


class ConvenioRespuesta(BaseModel):
    """Un convenio con sus cuotas, su estado operativo y lo que se observa durante su vigencia."""

    convenio_id: UUID
    cuenta_id: UUID
    cliente_unico: str
    gestion_id: UUID
    monto_total_acordado: Decimal
    fecha_inicio: date
    fecha_fin: date | None
    creado_en: datetime = Field(description="Cuando se acordo: el ocurrido_en de su evento.")
    registrado_en: datetime
    version_modelo: str
    estado_operativo: EstadoConvenio
    cuotas: list[CuotaRespuesta] = Field(
        description="Tal como se declararon; vacia sin calendario."
    )
    evaluacion_de_cuotas: Literal["NO_EVALUABLE"] = Field(
        default="NO_EVALUABLE",
        description="lifecycle/v1 no aplica movimientos a cuotas: no inventa una aplicacion "
        "contable.",
    )
    recuperacion_observada_durante_convenio: RecuperacionObservadaRespuesta | None = Field(
        description="Solo en GET /convenios/{convenio_id}."
    )
    evento: EventoLifecycleRespuesta
    cancelacion: EventoLifecycleRespuesta | None
    anulacion: EventoLifecycleRespuesta | None
    aviso: str = Field(default=AVISO_CONVENIO)


class PaginaConvenios(Pagina[ConvenioRespuesta]):
    cuenta_id: UUID
    cliente_unico: str


# --- la linea de tiempo ---------------------------------------------------------------------------


class OperacionalRespuesta(BaseModel):
    """Un evento operacional, con el recurso que registro o modifica."""

    evento: EventoLifecycleRespuesta
    gestion_id: UUID | None = Field(description="La gestion que registro, si registro una.")
    promesa_id: UUID | None
    convenio_id: UUID | None
    canal: Canal | None = None
    nivel_contacto: NivelContacto | None = None
    resultado: ResultadoGestion | None = None
    monto_prometido: Decimal | None = None
    fecha_limite: date | None = None
    monto_total_acordado: Decimal | None = None


AVISO_OBSERVACION = (
    "Lo que el corte de la cartera dice de la promesa o del plan de la cuenta: una observacion de "
    "la fuente en esa fecha, no un evento. No crea ninguna promesa ni ningun convenio."
)


class ObservacionEnCorteRespuesta(BaseModel):
    fecha_corte: date
    corte_id: UUID
    dataset_id: UUID
    source_row: int
    source_sheet: str | None
    artefacto_original: ArtefactoRespuesta
    estatus_promesa_pago: str | None
    monto_promesa_pago: Decimal | None
    estatus_plan: str | None
    monto_plan: Decimal | None
    aviso: str = Field(default=AVISO_OBSERVACION)


class ElementoLifecycleRespuesta(BaseModel):
    """Un momento de la historia de la cuenta, en una de las tres verdades: exactamente uno de
    `operacional`, `fuente_corte` y `economico` trae su detalle."""

    dominio: Dominio
    tipo: str = Field(
        description="El tipo de evento (OPERACIONAL), OBSERVACION_EN_CORTE (FUENTE_CORTE) o el "
        "tipo de movimiento (ECONOMICO: PAGO, REVERSO o POSIBLE_REVERSO)."
    )
    instante: datetime = Field(
        description="Su tiempo de negocio: ocurrido_en de un evento, el inicio del dia de un corte "
        "o la recepcion de un movimiento, estos dos en la zona horaria de la fuente."
    )
    operacional: OperacionalRespuesta | None = None
    fuente_corte: ObservacionEnCorteRespuesta | None = None
    economico: MovimientoRespuesta | None = None


class ParametrosLifecycle(Paginacion):
    desde: date | None = Field(default=None, description="Desde este dia, inclusive.")
    hasta: date | None = Field(default=None, description="Hasta este dia, inclusive.")
    dominio: Dominio | None = Field(default=None, description="Solo una de las tres verdades.")
    orden: Literal["desc", "asc"] = Field(
        default="desc", description="Por tiempo de negocio: desc (por omision) o asc."
    )


class PaginaLifecycle(Pagina[ElementoLifecycleRespuesta]):
    cuenta_id: UUID
    cliente_unico: str
    orden: Literal["desc", "asc"]
    version_motor: str
    aviso: str = Field(default=AVISO_LIFECYCLE)


# --- la evaluacion de las promesas ----------------------------------------------------------------


class EvaluacionPromesasEntrada(BaseModel):
    """La fecha de corte de una evaluacion. Es obligatoria: nunca sale del reloj."""

    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"examples": [{"as_of": "2026-10-16"}]}
    )

    as_of: date = Field(
        description="Se observa hasta el final de ese dia, en la zona horaria de la fuente. No "
        "puede ser posterior a hoy."
    )


class ConteosEvaluacionRespuesta(BaseModel):
    promesas_evaluadas: int
    pendientes: int
    cumplidas: int
    parciales: int
    incumplidas: int
    canceladas: int
    no_evaluables: int


class EjecucionEvaluacionRespuesta(BaseModel):
    """Una evaluacion de las promesas de una cartera a una fecha de corte."""

    evaluacion_run_id: UUID
    version_evaluacion: str
    estado: str = Field(
        description="EN_PROCESO hasta que un worker la termina. EXITOSA: publico una evaluacion "
        "por promesa. FALLIDA: no publico nada."
    )
    resultado: str | None = Field(
        description="EVALUACION_PUBLICADA, YA_EVALUADA (otra ejecucion ya evaluo exactamente las "
        "mismas entradas a esa fecha), ERROR_INTERNO... null mientras esta EN_PROCESO."
    )
    despacho_id: str
    cartera_id: str
    as_of: date
    zona_horaria: str
    firma_entrada: str | None
    horizonte_pagos: datetime | None = Field(
        description="El pago observado mas reciente de la cartera al evaluar: hasta donde llegan "
        "los datos. Una promesa vencida cuyo intervalo pasa del horizonte es NO_EVALUABLE."
    )
    conteos: ConteosEvaluacionRespuesta
    monto_prometido: Decimal
    monto_observado: Decimal = Field(
        description="Lo que suman los movimientos compatibles con cada promesa; un movimiento "
        "compatible con dos promesas cuenta en las dos."
    )
    trabajo_id: UUID | None = Field(description="Su trabajo EVALUACION_PROMESAS.")
    iniciada_en: datetime
    terminada_en: datetime | None
    detalle: str | None
    aviso: str = Field(default=AVISO_EVALUACION)

    @computed_field(description="Segundos de inicio a fin; vacio mientras esta en proceso.")
    @property
    def duracion_segundos(self) -> float | None:
        if self.terminada_en is None:
            return None
        return round((self.terminada_en - self.iniciada_en).total_seconds(), 3)


class ParametrosEvaluaciones(Paginacion):
    as_of: date | None = Field(default=None, description="Solo las de esa fecha de corte.")
    estado: Literal["EN_PROCESO", "EXITOSA", "FALLIDA"] | None = None


class PaginaEvaluaciones(Pagina[EjecucionEvaluacionRespuesta]):
    despacho_id: str
    cartera_id: str


class EvaluacionEnEjecucionRespuesta(BaseModel):
    """Lo que una evaluacion concluyo de una promesa."""

    promesa_id: UUID
    cuenta_id: UUID
    cliente_unico: str
    monto_prometido: Decimal
    fecha_limite: date
    estado: Literal["PENDIENTE", "CUMPLIDA", "PARCIAL", "INCUMPLIDA", "CANCELADA", "NO_EVALUABLE"]
    monto_observado: Decimal
    movimientos_compatibles: int
    primer_movimiento_en: datetime | None
    ultimo_movimiento_en: datetime | None
    motivos: list[dict]


class ParametrosEvaluacionesDePromesas(Paginacion):
    estado: (
        Literal["PENDIENTE", "CUMPLIDA", "PARCIAL", "INCUMPLIDA", "CANCELADA", "NO_EVALUABLE"]
        | None
    ) = None


class PaginaEvaluacionesDePromesas(Pagina[EvaluacionEnEjecucionRespuesta]):
    evaluacion_run_id: UUID
    as_of: date
    aviso: str = Field(default=AVISO_EVALUACION)


# --- la atribucion operativa ----------------------------------------------------------------------

AVISO_ATRIBUCION = (
    "Asociacion operacional, no causalidad: dice que gestiones con contacto de la misma cuenta "
    "ocurrieron antes de un pago, dentro de la ventana de la ejecucion, no que una gestion lo haya "
    "producido. Con dos o mas candidatas el pago queda AMBIGUA y no se elige ninguna."
)

PATRON_PERIODO = r"^\d{4}-(0[1-9]|1[0-2])$"
Clasificacion = Literal["SIN_GESTION_CANDIDATA", "ASOCIACION_UNICA", "AMBIGUA"]


class AtribucionEntrada(BaseModel):
    """Que ventana atribuir, y con que ventana hacia atras."""

    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"examples": [{"periodo": "2026-09", "ventana_dias": 30}]}
    )

    periodo: str = Field(
        pattern=PATRON_PERIODO,
        description="El mes de recepcion de los pagos, AAAA-MM: una ventana del motor de pagos.",
    )
    ventana_dias: int | None = Field(
        default=None,
        ge=1,
        le=366,
        description="Cuantos dias antes de un pago puede haber ocurrido una gestion candidata. Por "
        "omision, MC_ATRIBUCION_VENTANA_DIAS (30, una politica del demo, no una verdad de "
        "negocio). Se guarda en la ejecucion y entra en su firma de entrada.",
    )


class ConteosAtribucionRespuesta(BaseModel):
    movimientos_evaluados: int = Field(
        description="Los PAGO de la interpretacion de pagos que leyo; cada uno con una "
        "clasificacion. Los reversos y posibles reversos no se atribuyen."
    )
    asociados: int = Field(description="ASOCIACION_UNICA: exactamente una gestion candidata.")
    ambiguos: int = Field(description="AMBIGUA: dos o mas candidatas, sin elegir ninguna.")
    sin_candidato: int = Field(description="SIN_GESTION_CANDIDATA.")
    candidatos: int = Field(description="Las parejas (pago, gestion candidata) que publico.")
    movimientos_anulados: int = Field(description="De los evaluados, los que anulo un reverso.")
    gestiones_leidas: int = Field(
        description="Las gestiones de sus cuentas que pudieron anteceder a un pago, con contacto o "
        "sin el, anuladas o no."
    )
    gestiones_anuladas: int


class MontosAtribucionRespuesta(BaseModel):
    """Lo que suman los pagos de cada clase. Los que anulo un reverso van aparte: no son
    recuperacion."""

    asociado: Decimal
    ambiguo: Decimal
    sin_candidato: Decimal
    anulado: Decimal


class EjecucionAtribucionRespuesta(BaseModel):
    """Una atribucion de los pagos de una ventana (un despacho, una cartera y un mes de
    recepcion)."""

    atribucion_run_id: UUID
    version_atribucion: str
    estado: str = Field(
        description="EN_PROCESO hasta que un worker la termina. EXITOSA: publico una "
        "clasificacion por pago. FALLIDA: no publico nada."
    )
    resultado: str | None = Field(
        description="ATRIBUCION_PUBLICADA, YA_ATRIBUIDA (otra ejecucion ya atribuyo exactamente "
        "las mismas entradas), SIN_INTERPRETACION_DE_PAGOS, ERROR_INTERNO... null mientras esta "
        "EN_PROCESO."
    )
    despacho_id: str
    cartera_id: str
    periodo: str = Field(description="El mes de recepcion de sus pagos, AAAA-MM.")
    periodo_desde: date
    periodo_hasta: date
    ventana_dias: int = Field(
        description="Cuantos dias antes de un pago pudo ocurrir una gestion candidata: un "
        "parametro de esta ejecucion, no una verdad de negocio."
    )
    zona_horaria: str = Field(
        description="La de las horas locales de los pagos, con que se comparan con los instantes "
        "de las gestiones."
    )
    vigente: bool = Field(
        description="Si es la que vale hoy para su ventana: su EXITOSA mas reciente. Las demas son "
        "historia y no cambian."
    )
    motor_pagos_run_id: UUID | None = Field(
        description="La interpretacion de pagos cuyos movimientos atribuyo: la vigente de su "
        "ventana al leer. Null si no publico."
    )
    interpretacion_de_pagos_vigente: bool | None = Field(
        description="Si esa interpretacion sigue siendo la vigente. false: hay una mas reciente, y "
        "la ventana se debe volver a atribuir (motor-cartera backfill-atribucion)."
    )
    firma_entrada: str | None
    conteos: ConteosAtribucionRespuesta
    montos: MontosAtribucionRespuesta
    trabajo_id: UUID | None = Field(description="Su trabajo ATRIBUCION.")
    iniciada_en: datetime
    terminada_en: datetime | None
    detalle: str | None
    aviso: str = Field(default=AVISO_ATRIBUCION)

    @computed_field(description="Segundos de inicio a fin; vacio mientras esta en proceso.")
    @property
    def duracion_segundos(self) -> float | None:
        if self.terminada_en is None:
            return None
        return round((self.terminada_en - self.iniciada_en).total_seconds(), 3)


class ParametrosAtribuciones(Paginacion):
    periodo: str | None = Field(
        default=None, pattern=PATRON_PERIODO, description="Solo las de un mes, AAAA-MM."
    )
    estado: Literal["EN_PROCESO", "EXITOSA", "FALLIDA"] | None = None
    version: str = Field(
        default=VERSION_ATRIBUCION, max_length=32, description="Por omision, la del servicio."
    )


class PaginaAtribuciones(Pagina[EjecucionAtribucionRespuesta]):
    despacho_id: str
    cartera_id: str
    version_atribucion: str


class CandidataRespuesta(BaseModel):
    """Una gestion candidata de un pago: de su cuenta, vigente, con contacto y en la ventana."""

    gestion_id: UUID
    ocurrido_en: datetime
    canal: str
    nivel_contacto: str
    resultado: str
    antelacion_segundos: int = Field(description="Cuanto antes del pago ocurrio.")


class ResultadoAtribucionRespuesta(BaseModel):
    """Lo que una ejecucion concluyo de un pago, con sus candidatas y sus motivos."""

    atribucion_run_id: UUID
    vigente: bool = Field(description="Si su ejecucion es la atribucion vigente de su ventana.")
    ventana_dias: int
    motor_pagos_run_id: UUID
    interpretacion_de_pagos_vigente: bool
    movimiento_id: UUID
    cuenta_id: UUID | None = Field(
        description="La cuenta con que el motor de pagos concilio el pago; null si no la tenia, y "
        "entonces no tiene candidatas."
    )
    fecha_recepcion: datetime = Field(description="Hora local de la fuente, sin zona.")
    monto: Decimal
    anulado_por_reverso: bool = Field(
        description="Si un reverso lo anulo: se clasifica igual, porque las gestiones si lo "
        "antecedieron, pero su monto no es recuperacion."
    )
    clasificacion: Clasificacion
    gestion_id: UUID | None = Field(
        description="La gestion asociada, solo en ASOCIACION_UNICA. En AMBIGUA es null: no se "
        "elige entre las candidatas."
    )
    candidatas: list[CandidataRespuesta] = Field(
        description="Todas, de la mas proxima al pago a la mas lejana: un orden para leerlas, no "
        "una preferencia."
    )
    motivos: list[dict] = Field(description="Por que: [{'codigo': ...}], con lo que lo explica.")


class ParametrosResultadosAtribucion(Paginacion):
    clasificacion: Clasificacion | None = None
    cliente_unico: str | None = Field(default=None, pattern=r"^[A-Z0-9]{8,20}$")


class PaginaResultadosAtribucion(Pagina[ResultadoAtribucionRespuesta]):
    atribucion_run_id: UUID
    aviso: str = Field(default=AVISO_ATRIBUCION)


class PaginaAtribucionesDeMovimiento(Pagina[ResultadoAtribucionRespuesta]):
    movimiento: MovimientoRespuesta
    aviso: str = Field(default=AVISO_ATRIBUCION)


class PagoAtribuidoRespuesta(BaseModel):
    movimiento: MovimientoRespuesta
    atribucion: ResultadoAtribucionRespuesta | None = Field(
        description="La de la atribucion vigente de su ventana; null si su ventana no se ha "
        "atribuido."
    )


class PaginaAtribucionesDeCuenta(Pagina[PagoAtribuidoRespuesta]):
    cuenta_id: UUID
    cliente_unico: str
    version_motor: str
    aviso: str = Field(default=AVISO_ATRIBUCION)
