"""Lo que la API lee del lifecycle: gestiones, promesas y convenios, con su estado y su evidencia.

El estado operativo de una gestion, una promesa o un convenio no se guarda: sale de sus eventos. Una
gestion esta ANULADA si un evento GESTION_ANULADA se refiere a la suya; una promesa, CANCELADA si un
PROMESA_CANCELADA se refiere a la suya, o ANULADA si se anulo su gestion. Cada cierre se encuentra
por el indice unico de los eventos relacionados, y cada lista de una cuenta entra por el indice
(cuenta, momento) de su tabla: ninguna consulta de una cuenta recorre el lifecycle entero.

Los filtros por dia (`desde`, `hasta`) son dias de calendario en la zona horaria de la fuente, y se
convierten a instantes en PostgreSQL, con su base de zonas. Como el resto de la Cuenta 360, la API
las llama con una sesion de lectura (REPEATABLE READ): todas ven la misma foto de la base.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import ColumnElement, and_, exists, func, text
from sqlalchemy.orm import aliased
from sqlmodel import Session, select
from sqlmodel.sql.expression import Select

from motor_cartera.db.modelos import (
    ArtefactoFuente,
    ConvenioCobranza,
    CorteCanonico,
    CuentaCanonica,
    CuotaConvenio,
    DatasetConformado,
    EjecucionEvaluacionPromesas,
    EstadoEvaluacionPromesas,
    EvaluacionPromesa,
    EventoLifecycle,
    GestionCobranza,
    PromesaPago,
    SnapshotCuenta,
    VisitaCampo,
)
from motor_cartera.lifecycle.reglas import (
    EstadoConvenio,
    EstadoGestion,
    EstadoPromesa,
    NivelContacto,
    TipoEvento,
)
from motor_cartera.motor_pagos.consultas import MovimientoVisto, movimientos_por_id, vigentes


class GestionNoEncontrada(Exception):
    pass


class PromesaNoEncontrada(Exception):
    pass


class ConvenioNoEncontrado(Exception):
    pass


def instante_local(dia: date, zona: str) -> ColumnElement:
    """El inicio de un dia de calendario en la zona de la fuente, como instante: lo calcula
    PostgreSQL una vez por consulta, y el indice de la tabla sigue sirviendo."""
    return func.timezone(zona, sa.cast(datetime.combine(dia, time()), sa.DateTime))


def _en_el_rango(columna, desde: date | None, hasta: date | None, zona: str) -> list:
    """Los instantes de los dias [desde, hasta], inclusive, en la zona de la fuente."""
    condiciones = []
    if desde is not None:
        condiciones.append(columna >= instante_local(desde, zona))
    if hasta is not None:
        condiciones.append(columna < instante_local(hasta + timedelta(1), zona))
    return condiciones


def _cierre(tipo: TipoEvento, sobre) -> Any:
    """Un alias del evento de ese tipo que se refiere a `sobre` (un id de evento): para un LEFT
    JOIN por el indice unico de los eventos relacionados."""
    cierre = aliased(EventoLifecycle)
    return cierre, and_(cierre.evento_relacionado_id == sobre, cierre.tipo_evento == tipo)


def anulada(evento_id) -> ColumnElement:
    """Si la gestion de ese evento esta anulada: un EXISTS por el indice de los relacionados. Con un
    alias propio, para que no se correlacione con el evento que la consulta de fuera ya une."""
    cierre = aliased(EventoLifecycle)
    return exists().where(
        cierre.evento_relacionado_id == evento_id,
        cierre.tipo_evento == TipoEvento.GESTION_ANULADA,
    )


# --- las gestiones --------------------------------------------------------------------------------


@dataclass(frozen=True)
class GestionVista:
    """Una gestion con su evento, su anulacion, su visita y lo que nacio de ella."""

    gestion: GestionCobranza
    evento: EventoLifecycle
    anulacion: EventoLifecycle | None
    visita: VisitaCampo | None
    promesa_id: UUID | None
    convenio_id: UUID | None
    cuenta_id: UUID
    cliente_unico: str

    @property
    def estado(self) -> EstadoGestion:
        return EstadoGestion.VIGENTE if self.anulacion is None else EstadoGestion.ANULADA


def _gestiones_vistas() -> Select[Any]:
    anulacion, cuando = _cierre(TipoEvento.GESTION_ANULADA, GestionCobranza.evento_lifecycle_id)
    return (
        select(
            GestionCobranza,
            EventoLifecycle,
            anulacion,
            VisitaCampo,
            PromesaPago.promesa_id,
            ConvenioCobranza.convenio_id,
            CuentaCanonica.cuenta_id,
            CuentaCanonica.cliente_unico,
        )
        .join(EventoLifecycle, EventoLifecycle.id == GestionCobranza.evento_lifecycle_id)
        .join(CuentaCanonica, CuentaCanonica.id == GestionCobranza.cuenta_canonica_id)
        .outerjoin(anulacion, cuando)
        .outerjoin(VisitaCampo, VisitaCampo.gestion_cobranza_id == GestionCobranza.id)
        .outerjoin(PromesaPago, PromesaPago.gestion_cobranza_id == GestionCobranza.id)
        .outerjoin(ConvenioCobranza, ConvenioCobranza.gestion_cobranza_id == GestionCobranza.id)
    )


def obtener_gestion(s: Session, gestion_id: UUID) -> GestionVista:
    fila = s.exec(_gestiones_vistas().where(GestionCobranza.gestion_id == gestion_id)).first()
    if fila is None:
        raise GestionNoEncontrada(gestion_id)
    return GestionVista(*fila)


def gestiones_de_cuenta(
    s: Session,
    cuenta: CuentaCanonica,
    *,
    zona: str,
    desde: date | None = None,
    hasta: date | None = None,
    canal: str | None = None,
    nivel_contacto: str | None = None,
    resultado: str | None = None,
    estado: EstadoGestion | None = None,
    desplazamiento: int,
    limite: int,
) -> tuple[int, list[GestionVista]]:
    """Una pagina de las gestiones de la cuenta, de la mas reciente a la mas antigua por su
    momento de negocio, filtradas en PostgreSQL. Entra por el indice (cuenta, ocurrido_en)."""
    condiciones: list = [
        GestionCobranza.cuenta_canonica_id == cuenta.id,
        *_en_el_rango(GestionCobranza.ocurrido_en, desde, hasta, zona),
    ]
    if canal is not None:
        condiciones.append(GestionCobranza.canal == canal)
    if nivel_contacto is not None:
        condiciones.append(GestionCobranza.nivel_contacto == nivel_contacto)
    if resultado is not None:
        condiciones.append(GestionCobranza.resultado == resultado)
    if estado is not None:
        es_anulada = anulada(GestionCobranza.evento_lifecycle_id)
        condiciones.append(es_anulada if estado == EstadoGestion.ANULADA else ~es_anulada)
    total = s.exec(select(func.count()).select_from(GestionCobranza).where(*condiciones)).one()
    filas = s.exec(
        _gestiones_vistas()
        .where(*condiciones)
        .order_by(GestionCobranza.ocurrido_en.desc(), GestionCobranza.id.desc())
        .offset(desplazamiento)
        .limit(limite)
    ).all()
    return total, [GestionVista(*fila) for fila in filas]


# --- las promesas ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class EvaluacionVista:
    evaluacion: EvaluacionPromesa
    ejecucion: EjecucionEvaluacionPromesas


@dataclass(frozen=True)
class PromesaVista:
    """Una promesa con su evento, su gestion, su cancelacion o anulacion y su ultima evaluacion."""

    promesa: PromesaPago
    evento: EventoLifecycle
    gestion_id: UUID
    gestion_evento_id: UUID
    """El evento de su gestion: al que se refiere el suyo."""
    cancelacion: EventoLifecycle | None
    anulacion: EventoLifecycle | None
    cuenta_id: UUID
    cliente_unico: str
    ultima_evaluacion: EvaluacionVista | None = None

    @property
    def estado(self) -> EstadoPromesa:
        if self.anulacion is not None:
            return EstadoPromesa.ANULADA
        if self.cancelacion is not None:
            return EstadoPromesa.CANCELADA
        return EstadoPromesa.VIGENTE


def _promesas_vistas() -> Select[Any]:
    cancelacion, cancelada = _cierre(TipoEvento.PROMESA_CANCELADA, PromesaPago.evento_lifecycle_id)
    anulacion, anulada_ = _cierre(TipoEvento.GESTION_ANULADA, GestionCobranza.evento_lifecycle_id)
    de_la_gestion = aliased(EventoLifecycle)
    return (
        select(
            PromesaPago,
            EventoLifecycle,
            GestionCobranza.gestion_id,
            de_la_gestion.evento_id,
            cancelacion,
            anulacion,
            CuentaCanonica.cuenta_id,
            CuentaCanonica.cliente_unico,
        )
        .join(EventoLifecycle, EventoLifecycle.id == PromesaPago.evento_lifecycle_id)
        .join(GestionCobranza, GestionCobranza.id == PromesaPago.gestion_cobranza_id)
        .join(de_la_gestion, de_la_gestion.id == GestionCobranza.evento_lifecycle_id)
        .join(CuentaCanonica, CuentaCanonica.id == PromesaPago.cuenta_canonica_id)
        .outerjoin(cancelacion, cancelada)
        .outerjoin(anulacion, anulada_)
    )


def obtener_promesa(s: Session, promesa_id: UUID) -> PromesaVista:
    fila = s.exec(_promesas_vistas().where(PromesaPago.promesa_id == promesa_id)).first()
    if fila is None:
        raise PromesaNoEncontrada(promesa_id)
    ultimas = ultimas_evaluaciones(s, [fila[0].id])
    return PromesaVista(*fila, ultima_evaluacion=ultimas.get(fila[0].id))


def promesas_de_cuenta(
    s: Session,
    cuenta: CuentaCanonica,
    *,
    estado: EstadoPromesa | None = None,
    desplazamiento: int,
    limite: int,
) -> tuple[int, list[PromesaVista]]:
    """Una pagina de las promesas de la cuenta, de la de fecha limite mas reciente a la mas
    antigua, con su ultima evaluacion. Entra por el indice (cuenta, fecha_limite)."""
    condiciones: list = [PromesaPago.cuenta_canonica_id == cuenta.id]
    if estado is not None:
        de_la_promesa = aliased(GestionCobranza)
        cierre = aliased(EventoLifecycle)
        anulada_ = exists().where(
            de_la_promesa.id == PromesaPago.gestion_cobranza_id,
            anulada(de_la_promesa.evento_lifecycle_id),
        )
        cancelada = exists().where(
            cierre.evento_relacionado_id == PromesaPago.evento_lifecycle_id,
            cierre.tipo_evento == TipoEvento.PROMESA_CANCELADA,
        )
        condiciones.append(
            {
                EstadoPromesa.ANULADA: anulada_,
                EstadoPromesa.CANCELADA: and_(~anulada_, cancelada),
                EstadoPromesa.VIGENTE: and_(~anulada_, ~cancelada),
            }[estado]
        )
    total = s.exec(select(func.count()).select_from(PromesaPago).where(*condiciones)).one()
    filas = s.exec(
        _promesas_vistas()
        .where(*condiciones)
        .order_by(PromesaPago.fecha_limite.desc(), PromesaPago.id.desc())
        .offset(desplazamiento)
        .limit(limite)
    ).all()
    ultimas = ultimas_evaluaciones(s, [fila[0].id for fila in filas])
    return total, [PromesaVista(*f, ultima_evaluacion=ultimas.get(f[0].id)) for f in filas]


def ultimas_evaluaciones(s: Session, promesas: list[int]) -> dict[int, EvaluacionVista]:
    """La ultima evaluacion EXITOSA de cada promesa: la de su fecha de corte mas reciente y, entre
    dos de la misma fecha, la ejecucion mas reciente. Entra por el indice de las evaluaciones de una
    promesa y devuelve una fila por promesa, por muchas fechas de corte que la hayan evaluado."""
    if not promesas:
        return {}
    filas = s.exec(
        select(EvaluacionPromesa, EjecucionEvaluacionPromesas)
        .join(
            EjecucionEvaluacionPromesas,
            EjecucionEvaluacionPromesas.id == EvaluacionPromesa.ejecucion_evaluacion_promesas_id,
        )
        .where(
            EvaluacionPromesa.promesa_pago_id.in_(promesas),
            EjecucionEvaluacionPromesas.estado == EstadoEvaluacionPromesas.EXITOSA,
        )
        .distinct(EvaluacionPromesa.promesa_pago_id)
        .order_by(
            EvaluacionPromesa.promesa_pago_id,
            EjecucionEvaluacionPromesas.as_of.desc(),
            EjecucionEvaluacionPromesas.id.desc(),
        )
    ).all()
    return {
        evaluacion.promesa_pago_id: EvaluacionVista(evaluacion, ejecucion)
        for evaluacion, ejecucion in filas
    }


# --- los convenios --------------------------------------------------------------------------------


@dataclass(frozen=True)
class RecuperacionObservada:
    """Lo que la interpretacion vigente de los pagos observa en la cuenta durante un convenio. No es
    el pago de ninguna cuota: ningun movimiento se aplica a una cuota sin reglas que lo
    justifiquen."""

    monto: Decimal
    movimientos: int
    desde: date
    hasta: date | None
    horizonte_pagos: datetime | None
    """El pago observado mas reciente de la cartera: hasta donde llegan los datos."""


@dataclass(frozen=True)
class ConvenioVista:
    convenio: ConvenioCobranza
    evento: EventoLifecycle
    gestion_id: UUID
    gestion_evento_id: UUID
    cancelacion: EventoLifecycle | None
    anulacion: EventoLifecycle | None
    cuenta_id: UUID
    cliente_unico: str
    cuotas: tuple[CuotaConvenio, ...] = ()
    recuperacion: RecuperacionObservada | None = None

    @property
    def estado(self) -> EstadoConvenio:
        if self.anulacion is not None:
            return EstadoConvenio.ANULADO
        if self.cancelacion is not None:
            return EstadoConvenio.CANCELADO
        return EstadoConvenio.VIGENTE


def _convenios_vistos() -> Select[Any]:
    cancelacion, cancelado = _cierre(
        TipoEvento.CONVENIO_CANCELADO, ConvenioCobranza.evento_lifecycle_id
    )
    anulacion, anulada_ = _cierre(TipoEvento.GESTION_ANULADA, GestionCobranza.evento_lifecycle_id)
    de_la_gestion = aliased(EventoLifecycle)
    return (
        select(
            ConvenioCobranza,
            EventoLifecycle,
            GestionCobranza.gestion_id,
            de_la_gestion.evento_id,
            cancelacion,
            anulacion,
            CuentaCanonica.cuenta_id,
            CuentaCanonica.cliente_unico,
        )
        .join(EventoLifecycle, EventoLifecycle.id == ConvenioCobranza.evento_lifecycle_id)
        .join(GestionCobranza, GestionCobranza.id == ConvenioCobranza.gestion_cobranza_id)
        .join(de_la_gestion, de_la_gestion.id == GestionCobranza.evento_lifecycle_id)
        .join(CuentaCanonica, CuentaCanonica.id == ConvenioCobranza.cuenta_canonica_id)
        .outerjoin(cancelacion, cancelado)
        .outerjoin(anulacion, anulada_)
    )


def obtener_convenio(s: Session, convenio_id: UUID, *, version_motor: str) -> ConvenioVista:
    """Un convenio con sus cuotas y la recuperacion que se observa en su cuenta durante su
    vigencia, en la interpretacion vigente de los pagos."""
    fila = s.exec(_convenios_vistos().where(ConvenioCobranza.convenio_id == convenio_id)).first()
    if fila is None:
        raise ConvenioNoEncontrado(convenio_id)
    convenio = fila[0]
    cuotas = tuple(
        s.exec(
            select(CuotaConvenio)
            .where(CuotaConvenio.convenio_cobranza_id == convenio.id)
            .order_by(CuotaConvenio.numero)
        ).all()
    )
    cuenta = s.get_one(CuentaCanonica, convenio.cuenta_canonica_id)
    recuperacion = recuperacion_observada(
        s,
        cuenta,
        desde=convenio.fecha_inicio,
        hasta=convenio.fecha_fin,
        version_motor=version_motor,
    )
    return ConvenioVista(*fila, cuotas=cuotas, recuperacion=recuperacion)


def convenios_de_cuenta(
    s: Session, cuenta: CuentaCanonica, *, desplazamiento: int, limite: int
) -> tuple[int, list[ConvenioVista]]:
    """Una pagina de los convenios de la cuenta, del de inicio mas reciente al mas antiguo, con sus
    cuotas. La recuperacion observada de cada uno esta en GET /convenios/{convenio_id}."""
    condicion = ConvenioCobranza.cuenta_canonica_id == cuenta.id
    total = s.exec(select(func.count()).select_from(ConvenioCobranza).where(condicion)).one()
    filas = s.exec(
        _convenios_vistos()
        .where(condicion)
        .order_by(ConvenioCobranza.fecha_inicio.desc(), ConvenioCobranza.id.desc())
        .offset(desplazamiento)
        .limit(limite)
    ).all()
    por_convenio: dict[int, list[CuotaConvenio]] = {fila[0].id: [] for fila in filas}
    if por_convenio:
        for cuota in s.exec(
            select(CuotaConvenio)
            .where(CuotaConvenio.convenio_cobranza_id.in_(list(por_convenio)))
            .order_by(CuotaConvenio.convenio_cobranza_id, CuotaConvenio.numero)
        ).all():
            por_convenio[cuota.convenio_cobranza_id].append(cuota)
    return total, [ConvenioVista(*f, cuotas=tuple(por_convenio[f[0].id])) for f in filas]


def recuperacion_observada(
    s: Session,
    cuenta: CuentaCanonica,
    *,
    desde: date,
    hasta: date | None,
    version_motor: str,
) -> RecuperacionObservada:
    """Los PAGO de la interpretacion vigente conciliados con la cuenta, recibidos del dia `desde` al
    dia `hasta` inclusive (sin `hasta`, hasta el ultimo observado), que ningun reverso anulo; y el
    horizonte de los pagos de la cartera. Las horas de pagos/v1 son locales: se comparan con dias,
    sin zona."""
    limite = "" if hasta is None else "AND m.fecha_recepcion < :hasta "
    monto, movimientos = s.execute(
        text(
            "SELECT coalesce(sum(m.monto_reportado), 0), count(*) "
            "FROM movimiento_economico_canonico m "
            "WHERE m.ejecucion_motor_pagos_id = ANY(:vigentes) AND m.despacho_id = :despacho "
            "AND m.cartera_id = :cartera AND m.cliente_unico = :cliente "
            "AND m.cuenta_canonica_id = :cuenta AND m.tipo_movimiento = 'PAGO' "
            "AND m.anulado_por_movimiento_id IS NULL AND m.fecha_recepcion >= :desde "
            f"{limite}"
        ),
        {
            "vigentes": vigentes(s, version_motor),
            "despacho": cuenta.despacho_id,
            "cartera": cuenta.cartera_id,
            "cliente": cuenta.cliente_unico,
            "cuenta": cuenta.id,
            "desde": datetime.combine(desde, time()),
            "hasta": None if hasta is None else datetime.combine(hasta + timedelta(1), time()),
        },
    ).one()
    return RecuperacionObservada(
        monto=Decimal(monto).quantize(Decimal("0.01")),
        movimientos=movimientos,
        desde=desde,
        hasta=hasta,
        horizonte_pagos=horizonte_de_pagos(s, cuenta.despacho_id, cuenta.cartera_id),
    )


def horizonte_de_pagos(s: Session, despacho_id: str, cartera_id: str) -> datetime | None:
    """El pago observado mas reciente de la cartera: hasta donde llegan los datos de pagos. Lo lee
    del final del indice de recepcion, sin recorrer los pagos."""
    return s.execute(
        text(
            "SELECT max(fecha_recepcion) FROM pago_observado "
            "WHERE despacho_id = :despacho AND cartera_id = :cartera"
        ),
        {"despacho": despacho_id, "cartera": cartera_id},
    ).scalar_one()


# --- el resumen de la Cuenta 360 ------------------------------------------------------------------


@dataclass(frozen=True)
class UltimaGestion:
    gestion_id: UUID
    ocurrido_en: datetime
    canal: str
    nivel_contacto: str
    resultado: str


@dataclass(frozen=True)
class ResumenLifecycle:
    gestiones: int
    gestiones_anuladas: int
    ultima_gestion: UltimaGestion | None
    ultimo_contacto_titular: UltimaGestion | None
    promesas: int
    promesas_vigentes: int
    convenios: int
    convenios_vigentes: int
    visitas: int


def resumen_de_cuenta(
    s: Session, cuenta: CuentaCanonica, *, zona: str, al: date | None = None
) -> ResumenLifecycle:
    """El lifecycle de la cuenta en numeros: sus gestiones vigentes y anuladas, la ultima y el
    ultimo contacto con el titular (vigentes), sus promesas y convenios (y cuantos siguen vigentes)
    y sus visitas vigentes. Con `al`, como se veia al final de ese dia en la zona de la fuente: solo
    lo ocurrido hasta entonces, con lo que se sabe hoy (una anulacion registrada despues tambien
    cuenta: lo anulado se registro por error)."""
    fin = None if al is None else instante_local(al + timedelta(1), zona)
    de_la_cuenta: list = [GestionCobranza.cuenta_canonica_id == cuenta.id]
    if fin is not None:
        de_la_cuenta.append(GestionCobranza.ocurrido_en < fin)
    es_anulada = anulada(GestionCobranza.evento_lifecycle_id)
    en_vigor, anuladas, visitas = s.exec(
        select(
            func.count().filter(~es_anulada),
            func.count().filter(es_anulada),
            func.count().filter(~es_anulada, GestionCobranza.canal == "CAMPO"),
        ).where(*de_la_cuenta)
    ).one()

    def ultima(*condiciones) -> UltimaGestion | None:
        fila = s.exec(
            select(
                GestionCobranza.gestion_id,
                GestionCobranza.ocurrido_en,
                GestionCobranza.canal,
                GestionCobranza.nivel_contacto,
                GestionCobranza.resultado,
            )
            .where(*de_la_cuenta, ~es_anulada, *condiciones)
            .order_by(GestionCobranza.ocurrido_en.desc(), GestionCobranza.id.desc())
            .limit(1)
        ).first()
        return None if fila is None else UltimaGestion(*fila)

    promesas, promesas_vigentes = _acuerdos(
        s, PromesaPago, TipoEvento.PROMESA_CANCELADA, cuenta.id, fin
    )
    convenios, convenios_vigentes = _acuerdos(
        s, ConvenioCobranza, TipoEvento.CONVENIO_CANCELADO, cuenta.id, fin
    )
    return ResumenLifecycle(
        gestiones=en_vigor,
        gestiones_anuladas=anuladas,
        ultima_gestion=ultima(),
        ultimo_contacto_titular=ultima(
            GestionCobranza.nivel_contacto == NivelContacto.CONTACTO_TITULAR
        ),
        promesas=promesas,
        promesas_vigentes=promesas_vigentes,
        convenios=convenios,
        convenios_vigentes=convenios_vigentes,
        visitas=visitas,
    )


def _acuerdos(s: Session, modelo, cancelacion: TipoEvento, cuenta_id: int, fin) -> tuple[int, int]:
    """Cuantas promesas (o convenios) de la cuenta se acordaron hasta `fin`, sin las de una gestion
    anulada, y cuantas seguian vigentes: sin una cancelacion ocurrida hasta `fin`."""
    creado = aliased(EventoLifecycle)
    cancelado = aliased(EventoLifecycle)
    gestion = aliased(GestionCobranza)
    condiciones: list = [modelo.cuenta_canonica_id == cuenta_id]
    hasta_fin: list = []
    if fin is not None:
        condiciones.append(creado.ocurrido_en < fin)
        hasta_fin.append(cancelado.ocurrido_en < fin)
    cancelada = exists().where(
        cancelado.evento_relacionado_id == modelo.evento_lifecycle_id,
        cancelado.tipo_evento == cancelacion,
        *hasta_fin,
    )
    total, en_vigor = s.exec(
        select(func.count(), func.count().filter(~cancelada))
        .select_from(modelo)
        .join(creado, creado.id == modelo.evento_lifecycle_id)
        .join(gestion, gestion.id == modelo.gestion_cobranza_id)
        .where(*condiciones, ~anulada(gestion.evento_lifecycle_id))
    ).one()
    return total, en_vigor


# --- la linea de tiempo ---------------------------------------------------------------------------


class Dominio(StrEnum):
    """De cual de las tres verdades es un elemento de la linea de tiempo. No se mezclan."""

    OPERACIONAL = "OPERACIONAL"
    """Un evento que registro Motor Cartera: una gestion, una promesa, un convenio o un cierre."""
    FUENTE_CORTE = "FUENTE_CORTE"
    """Lo que un corte de la cartera dice de la promesa o del plan de la cuenta: una observacion de
    la fuente, no un evento."""
    ECONOMICO = "ECONOMICO"
    """Un movimiento economico canonico de la interpretacion vigente de los pagos."""


OBSERVACION_EN_CORTE = "OBSERVACION_EN_CORTE"

_CON_OBSERVACION = (
    "(s.estatus_promesa_pago IS NOT NULL OR s.monto_promesa_pago IS NOT NULL "
    "OR s.estatus_plan IS NOT NULL OR s.monto_plan IS NOT NULL)"
)
"""Un snapshot entra a la linea de tiempo si su corte dice algo de la promesa o del plan."""


@dataclass(frozen=True)
class EventoVisto:
    """Un evento con los identificadores publicos de lo que registro y de lo que modifica."""

    evento: EventoLifecycle
    relacionado_id: UUID | None
    gestion: GestionCobranza | None
    promesa: PromesaPago | None
    convenio: ConvenioCobranza | None


@dataclass(frozen=True)
class ObservacionEnCorte:
    """Lo que un snapshot dice de la promesa o del plan de la cuenta, con su evidencia."""

    snapshot: SnapshotCuenta
    corte_id: UUID
    dataset_id: UUID
    original: ArtefactoFuente


@dataclass(frozen=True)
class ElementoDeLinea:
    dominio: Dominio
    tipo: str
    instante: datetime
    evento: EventoVisto | None = None
    observacion: ObservacionEnCorte | None = None
    movimiento: MovimientoVisto | None = None


def linea_de_tiempo(
    s: Session,
    cuenta: CuentaCanonica,
    *,
    zona: str,
    version_motor: str,
    dominios: frozenset[Dominio] = frozenset(Dominio),
    desde: date | None = None,
    hasta: date | None = None,
    descendente: bool = True,
    desplazamiento: int,
    limite: int,
) -> tuple[int, list[ElementoDeLinea]]:
    """Una pagina de la historia de la cuenta en las tres verdades, en orden de tiempo de negocio:
    sus eventos operacionales por ocurrido_en (no por registrado_en), sus observaciones de corte al
    inicio del dia de su corte y sus movimientos por su recepcion, los dos en la zona de la fuente.
    En un mismo instante va primero lo operacional, despues la fuente y al final lo economico.

    Se ordena y se pagina en PostgreSQL, con un UNION ALL de tres consultas que entran cada una por
    el indice de la cuenta en su tabla; despues se lee el detalle de los elementos de la pagina, y
    nada mas."""
    parametros = {
        "cuenta": cuenta.id,
        "zona": zona,
        "despacho": cuenta.despacho_id,
        "cartera": cuenta.cartera_id,
        "cliente": cuenta.cliente_unico,
        "vigentes": vigentes(s, version_motor),
        "desde": None if desde is None else datetime.combine(desde, time()),
        "hasta": None if hasta is None else datetime.combine(hasta + timedelta(1), time()),
        "dia_desde": desde,
        "dia_hasta": hasta,
    }
    ramas = {
        Dominio.OPERACIONAL: (
            "SELECT 'OPERACIONAL' AS dominio, e.tipo_evento AS tipo, e.ocurrido_en AS instante, "
            "0 AS orden, e.id AS llave FROM evento_lifecycle e WHERE e.cuenta_canonica_id = :cuenta"
            + ("" if desde is None else " AND e.ocurrido_en >= timezone(:zona, :desde)")
            + ("" if hasta is None else " AND e.ocurrido_en < timezone(:zona, :hasta)")
        ),
        Dominio.FUENTE_CORTE: (
            f"SELECT 'FUENTE_CORTE' AS dominio, '{OBSERVACION_EN_CORTE}' AS tipo, "
            "timezone(:zona, s.fecha_corte::timestamp) AS instante, 1 AS orden, "
            "s.corte_canonico_id AS llave "
            f"FROM snapshot_cuenta s WHERE s.cuenta_canonica_id = :cuenta AND {_CON_OBSERVACION}"
            + ("" if desde is None else " AND s.fecha_corte >= :dia_desde")
            + ("" if hasta is None else " AND s.fecha_corte <= :dia_hasta")
        ),
        Dominio.ECONOMICO: (
            "SELECT 'ECONOMICO' AS dominio, m.tipo_movimiento AS tipo, "
            "timezone(:zona, m.fecha_recepcion) AS instante, 2 AS orden, m.id AS llave "
            "FROM movimiento_economico_canonico m "
            "WHERE m.ejecucion_motor_pagos_id = ANY(:vigentes) "
            "AND m.despacho_id = :despacho AND m.cartera_id = :cartera "
            "AND m.cliente_unico = :cliente"
            + ("" if desde is None else " AND m.fecha_recepcion >= :desde")
            + ("" if hasta is None else " AND m.fecha_recepcion < :hasta")
        ),
    }
    elegidas = [ramas[d] for d in Dominio if d in dominios]
    if not elegidas:
        return 0, []
    total = sum(
        s.execute(text(f"SELECT count(*) FROM ({rama}) r"), parametros).scalar_one()
        for rama in elegidas
    )
    sentido = "DESC" if descendente else "ASC"
    filas = s.execute(
        text(
            f"SELECT dominio, tipo, instante, llave FROM ({' UNION ALL '.join(elegidas)}) linea "
            f"ORDER BY instante {sentido}, orden {sentido}, llave {sentido} "
            "LIMIT :limite OFFSET :desplazamiento"
        ),
        {**parametros, "limite": limite, "desplazamiento": desplazamiento},
    ).all()
    llaves: dict[str, list[int]] = {d: [] for d in Dominio}
    for dominio, _, _, llave in filas:
        llaves[dominio].append(llave)
    eventos = eventos_vistos(s, llaves[Dominio.OPERACIONAL])
    observaciones = _observaciones(s, cuenta.id, llaves[Dominio.FUENTE_CORTE])
    movimientos = _movimientos(s, llaves[Dominio.ECONOMICO])
    elementos = []
    for dominio, tipo, instante, llave in filas:
        dominio = Dominio(dominio)
        elementos.append(
            ElementoDeLinea(
                dominio=dominio,
                tipo=tipo,
                instante=instante,
                evento=eventos.get(llave) if dominio == Dominio.OPERACIONAL else None,
                observacion=observaciones.get(llave) if dominio == Dominio.FUENTE_CORTE else None,
                movimiento=movimientos.get(llave) if dominio == Dominio.ECONOMICO else None,
            )
        )
    return total, elementos


def eventos_vistos(s: Session, ids: list[int]) -> dict[int, EventoVisto]:
    """Los eventos con esos ids, cada uno con el identificador publico del evento al que se refiere
    y el detalle que registro (su gestion, su promesa o su convenio), si registro uno."""
    if not ids:
        return {}
    relacionado = aliased(EventoLifecycle)
    filas = s.exec(
        select(
            EventoLifecycle,
            relacionado.evento_id,
            GestionCobranza,
            PromesaPago,
            ConvenioCobranza,
        )
        .outerjoin(relacionado, relacionado.id == EventoLifecycle.evento_relacionado_id)
        .outerjoin(GestionCobranza, GestionCobranza.evento_lifecycle_id == EventoLifecycle.id)
        .outerjoin(PromesaPago, PromesaPago.evento_lifecycle_id == EventoLifecycle.id)
        .outerjoin(ConvenioCobranza, ConvenioCobranza.evento_lifecycle_id == EventoLifecycle.id)
        .where(EventoLifecycle.id.in_(ids))
    ).all()
    return {fila[0].id: EventoVisto(*fila) for fila in filas}


def _observaciones(s: Session, cuenta_id: int, cortes: list[int]) -> dict[int, ObservacionEnCorte]:
    if not cortes:
        return {}
    filas = s.exec(
        select(
            SnapshotCuenta, CorteCanonico.corte_id, DatasetConformado.dataset_id, ArtefactoFuente
        )
        .join(CorteCanonico, CorteCanonico.id == SnapshotCuenta.corte_canonico_id)
        .join(DatasetConformado, DatasetConformado.id == CorteCanonico.dataset_conformado_id)
        .join(ArtefactoFuente, ArtefactoFuente.id == DatasetConformado.artefacto_original_id)
        .where(
            SnapshotCuenta.cuenta_canonica_id == cuenta_id,
            SnapshotCuenta.corte_canonico_id.in_(cortes),
        )
    ).all()
    return {fila[0].corte_canonico_id: ObservacionEnCorte(*fila) for fila in filas}


def _movimientos(s: Session, ids: list[int]) -> dict[int, MovimientoVisto]:
    """Los movimientos de la pagina, que son de las interpretaciones vigentes."""
    return movimientos_por_id(s, ids)
