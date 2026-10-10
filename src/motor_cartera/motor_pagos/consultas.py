"""Lo que la API lee de la interpretacion de los pagos.

La interpretacion vigente de una ventana es la de su ejecucion EXITOSA mas reciente con la
version que se pide (la del servicio, por omision). Solo puede haber una EN_PROCESO por ventana a la
vez, asi que el id de las EXITOSA de una ventana crece con el tiempo: la vigente es la de mayor id.
Las demas siguen ahi, como historia, y una ejecucion se puede leer por su motor_pagos_run_id.

Cada funcion compone varias consultas. La API las llama con una sesion de lectura
(`db.sesion.sesion_de_lectura`), en la que todas ven la misma foto de la base: una interpretacion
que se publica mientras se responde no aparece en una consulta y falta en la siguiente. Ninguna
consulta de una cuenta o de un movimiento depende del tamano de la cartera: entran por un indice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, and_, func, text
from sqlalchemy.orm import aliased
from sqlmodel import Session, select
from sqlmodel.sql.expression import Select

from motor_cartera.db.modelos import (
    ArtefactoFuente,
    CorteCanonico,
    CuentaCanonica,
    DatasetConformado,
    EjecucionMotorPagos,
    EstadoMotorPagos,
    IngestaPagos,
    MovimientoEconomicoCanonico,
    PagoObservado,
    ResultadoPagoObservado,
    SnapshotCuenta,
    TrabajoOrquestacion,
)
from motor_cartera.historia import cuenta360
from motor_cartera.motor_pagos import contexto
from motor_cartera.motor_pagos.contexto import ContextoTemporal
from motor_cartera.motor_pagos.reglas import Clasificacion


class EjecucionNoEncontrada(Exception):
    """No hay una ejecucion del motor de pagos con ese motor_pagos_run_id."""


class MovimientoNoEncontrado(Exception):
    """Ninguna ejecucion de esa version publico un movimiento con ese movimiento_id."""


def vigentes(s: Session, version: str) -> list[int]:
    """Los id de las ejecuciones vigentes de la version: una por ventana. Son pocas (una por mes de
    cada cartera), y se pasan como arreglo a las consultas que filtran por ellas."""
    return list(
        s.exec(
            select(func.max(EjecucionMotorPagos.id))
            .where(
                EjecucionMotorPagos.version_motor == version,
                EjecucionMotorPagos.estado == EstadoMotorPagos.EXITOSA,
            )
            .group_by(
                EjecucionMotorPagos.despacho_id,
                EjecucionMotorPagos.cartera_id,
                EjecucionMotorPagos.periodo_desde,
            )
        ).all()
    )


# --- las ejecuciones ------------------------------------------------------------------------------


@dataclass(frozen=True)
class EjecucionVista:
    ejecucion: EjecucionMotorPagos
    trabajo_id: UUID | None
    vigente: bool


def _ejecuciones() -> Select[Any]:
    return select(EjecucionMotorPagos, TrabajoOrquestacion.trabajo_id).outerjoin(
        TrabajoOrquestacion,
        TrabajoOrquestacion.ejecucion_motor_pagos_id == EjecucionMotorPagos.id,
    )


def obtener_ejecucion(s: Session, motor_pagos_run_id: UUID) -> EjecucionVista:
    fila = s.exec(
        _ejecuciones().where(EjecucionMotorPagos.motor_pagos_run_id == motor_pagos_run_id)
    ).first()
    if fila is None:
        raise EjecucionNoEncontrada(motor_pagos_run_id)
    ejecucion, trabajo_id = fila
    return EjecucionVista(
        ejecucion, trabajo_id, ejecucion.id in vigentes(s, ejecucion.version_motor)
    )


def listar_ejecuciones(
    s: Session,
    *,
    despacho_id: str,
    cartera_id: str,
    version: str,
    periodo: date | None,
    estado: EstadoMotorPagos | None,
    desplazamiento: int,
    limite: int,
) -> tuple[int, list[EjecucionVista]]:
    """Una pagina de las ejecuciones de la cartera, la ventana mas reciente primero y, dentro de una
    ventana, la ejecucion mas reciente primero."""
    condiciones = [
        EjecucionMotorPagos.despacho_id == despacho_id,
        EjecucionMotorPagos.cartera_id == cartera_id,
        EjecucionMotorPagos.version_motor == version,
    ]
    if periodo is not None:
        condiciones.append(EjecucionMotorPagos.periodo_desde == periodo)
    if estado is not None:
        condiciones.append(EjecucionMotorPagos.estado == estado)
    total = s.exec(select(func.count()).select_from(EjecucionMotorPagos).where(*condiciones)).one()
    filas = s.exec(
        _ejecuciones()
        .where(*condiciones)
        .order_by(EjecucionMotorPagos.periodo_desde.desc(), EjecucionMotorPagos.id.desc())
        .offset(desplazamiento)
        .limit(limite)
    ).all()
    actuales = set(vigentes(s, version))
    return total, [EjecucionVista(e, t, e.id in actuales) for e, t in filas]


# --- los resultados de una ejecucion --------------------------------------------------------------


@dataclass(frozen=True)
class ResultadoVisto:
    resultado: ResultadoPagoObservado
    pago: PagoObservado
    movimiento_id: UUID | None
    pagos_run_id: UUID
    dataset_id: UUID


def _resultados_vistos() -> Select[Any]:
    return (
        select(
            ResultadoPagoObservado,
            PagoObservado,
            MovimientoEconomicoCanonico.movimiento_id,
            IngestaPagos.pagos_run_id,
            DatasetConformado.dataset_id,
        )
        .join(
            PagoObservado,
            and_(
                PagoObservado.dataset_conformado_id == ResultadoPagoObservado.dataset_conformado_id,
                PagoObservado.source_row == ResultadoPagoObservado.source_row,
            ),
        )
        .outerjoin(
            MovimientoEconomicoCanonico,
            MovimientoEconomicoCanonico.id
            == ResultadoPagoObservado.movimiento_economico_canonico_id,
        )
        .join(IngestaPagos, IngestaPagos.id == PagoObservado.ingesta_pagos_id)
        .join(DatasetConformado, DatasetConformado.id == PagoObservado.dataset_conformado_id)
    )


def resultados_de(
    s: Session,
    ejecucion: EjecucionMotorPagos,
    *,
    clasificacion: Clasificacion | None,
    cliente_unico: str | None,
    firma_exacta: bytes | None = None,
    firma_legacy: bytes | None = None,
    desplazamiento: int,
    limite: int,
) -> tuple[int, list[ResultadoVisto]]:
    """Una pagina de lo que la ejecucion concluyo de cada observacion de su ventana, en orden de
    recepcion. Con `cliente_unico`, solo las de ese cliente: se buscan por el indice de los pagos de
    una cuenta, sin recorrer la ventana. Con `firma_exacta` o `firma_legacy`, solo las de esa
    huella: un grupo de copias o el grupo de una llave historica. Con el cliente, el grupo sale del
    indice de sus pagos; sin el, se filtran los resultados de la ventana.

    Cada resultado es de un pago de la ventana de la ejecucion, y la pagina lo dice tambien de los
    pagos: asi se leen en orden de recepcion por el indice de la ventana y la lectura se detiene al
    llenar la pagina, en lugar de ordenar todos los pagos observados de la cartera. El total cuenta
    los resultados sin unirlos a sus pagos, salvo cuando hace falta su cliente."""
    de_los_resultados: list[ColumnElement] = [
        ResultadoPagoObservado.ejecucion_motor_pagos_id == ejecucion.id
    ]
    if clasificacion is not None:
        de_los_resultados.append(ResultadoPagoObservado.clasificacion == clasificacion.value)
    if firma_exacta is not None:
        de_los_resultados.append(ResultadoPagoObservado.firma_exacta == firma_exacta)
    if firma_legacy is not None:
        de_los_resultados.append(ResultadoPagoObservado.firma_legacy == firma_legacy)
    de_la_ventana: list[ColumnElement] = [
        PagoObservado.despacho_id == ejecucion.despacho_id,
        PagoObservado.cartera_id == ejecucion.cartera_id,
        PagoObservado.fecha_recepcion >= _instante(ejecucion.periodo_desde),
        PagoObservado.fecha_recepcion < _instante(ejecucion.periodo_hasta),
    ]
    if cliente_unico is not None:
        de_la_ventana.append(PagoObservado.cliente_unico == cliente_unico)
        total = s.exec(
            select(func.count())
            .select_from(ResultadoPagoObservado)
            .join(
                PagoObservado,
                and_(
                    PagoObservado.dataset_conformado_id
                    == ResultadoPagoObservado.dataset_conformado_id,
                    PagoObservado.source_row == ResultadoPagoObservado.source_row,
                ),
            )
            .where(*de_los_resultados, *de_la_ventana)
        ).one()
    else:
        total = s.exec(
            select(func.count()).select_from(ResultadoPagoObservado).where(*de_los_resultados)
        ).one()
    filas = s.exec(
        _resultados_vistos()
        .where(*de_los_resultados, *de_la_ventana)
        .order_by(
            PagoObservado.fecha_recepcion,
            ResultadoPagoObservado.dataset_conformado_id,
            ResultadoPagoObservado.source_row,
        )
        .offset(desplazamiento)
        .limit(limite)
    ).all()
    return total, [ResultadoVisto(*fila) for fila in filas]


# --- los movimientos ------------------------------------------------------------------------------


@dataclass(frozen=True)
class MovimientoVisto:
    movimiento: MovimientoEconomicoCanonico
    motor_pagos_run_id: UUID
    periodo_desde: date
    cuenta_id: UUID | None
    vigente: bool


def _movimientos_vistos() -> Select[Any]:
    return (
        select(
            MovimientoEconomicoCanonico,
            EjecucionMotorPagos.motor_pagos_run_id,
            EjecucionMotorPagos.periodo_desde,
            CuentaCanonica.cuenta_id,
        )
        .join(
            EjecucionMotorPagos,
            EjecucionMotorPagos.id == MovimientoEconomicoCanonico.ejecucion_motor_pagos_id,
        )
        .outerjoin(
            CuentaCanonica, CuentaCanonica.id == MovimientoEconomicoCanonico.cuenta_canonica_id
        )
    )


def listar_movimientos(
    s: Session,
    *,
    despacho_id: str,
    cartera_id: str,
    version: str,
    cliente_unico: str | None = None,
    desde: date | None = None,
    hasta: date | None = None,
    tipo: str | None = None,
    estado_conciliacion: str | None = None,
    desplazamiento: int,
    limite: int,
) -> tuple[int, list[MovimientoVisto]]:
    """Una pagina de los movimientos vigentes de la cartera, del mas reciente al mas antiguo por
    recepcion. `desde` y `hasta` son dias de recepcion, inclusive. Con un cliente entra por el
    indice de los movimientos de una cuenta."""
    actuales = vigentes(s, version)
    condiciones: list[ColumnElement] = [
        MovimientoEconomicoCanonico.ejecucion_motor_pagos_id.in_(actuales),
        MovimientoEconomicoCanonico.despacho_id == despacho_id,
        MovimientoEconomicoCanonico.cartera_id == cartera_id,
    ]
    if cliente_unico is not None:
        condiciones.append(MovimientoEconomicoCanonico.cliente_unico == cliente_unico)
    if desde is not None:
        condiciones.append(MovimientoEconomicoCanonico.fecha_recepcion >= _instante(desde))
    if hasta is not None:
        condiciones.append(
            MovimientoEconomicoCanonico.fecha_recepcion < _instante(hasta + timedelta(1))
        )
    if tipo is not None:
        condiciones.append(MovimientoEconomicoCanonico.tipo_movimiento == tipo)
    if estado_conciliacion is not None:
        condiciones.append(MovimientoEconomicoCanonico.estado_conciliacion == estado_conciliacion)
    total = s.exec(
        select(func.count()).select_from(MovimientoEconomicoCanonico).where(*condiciones)
    ).one()
    filas = s.exec(
        _movimientos_vistos()
        .where(*condiciones)
        .order_by(
            MovimientoEconomicoCanonico.fecha_recepcion.desc(),
            MovimientoEconomicoCanonico.movimiento_id,
        )
        .offset(desplazamiento)
        .limit(limite)
    ).all()
    return total, [MovimientoVisto(*fila, vigente=True) for fila in filas]


def movimientos_por_id(s: Session, ids: list[int]) -> dict[int, MovimientoVisto]:
    """Los movimientos con esos ids internos, cada uno con su ejecucion, su cuenta y si es de la
    interpretacion vigente de su ventana. Es el detalle de una pagina que ya se eligio en otra
    consulta, como la linea de tiempo de una cuenta o la atribucion de sus movimientos."""
    if not ids:
        return {}
    filas = s.exec(_movimientos_vistos().where(MovimientoEconomicoCanonico.id.in_(ids))).all()
    actuales = {
        version: set(vigentes(s, version)) for version in {f[0].version_motor for f in filas}
    }
    return {
        f[0].id: MovimientoVisto(
            *f, vigente=f[0].ejecucion_motor_pagos_id in actuales[f[0].version_motor]
        )
        for f in filas
    }


@dataclass(frozen=True)
class DetalleDelMovimiento:
    visto: MovimientoVisto
    representante: ResultadoVisto
    original: ArtefactoFuente
    contexto: ContextoTemporal | None
    snapshots: dict[date, tuple[UUID, SnapshotCuenta]]
    """Los snapshots del contexto, por fecha de corte: el anterior y el siguiente."""


def obtener_movimiento(s: Session, movimiento_id: UUID, version: str) -> MovimientoVisto:
    """El movimiento en la interpretacion vigente de su ventana; si ya no esta en ella (la
    ventana se volvio a interpretar y dejo de fundarlo), el de la ejecucion mas reciente que lo
    publico."""
    filas = s.exec(
        _movimientos_vistos()
        .where(
            MovimientoEconomicoCanonico.movimiento_id == movimiento_id,
            MovimientoEconomicoCanonico.version_motor == version,
        )
        .order_by(MovimientoEconomicoCanonico.ejecucion_motor_pagos_id.desc())
    ).all()
    if not filas:
        raise MovimientoNoEncontrado(movimiento_id)
    actuales = set(vigentes(s, version))
    for fila in filas:
        if fila[0].ejecucion_motor_pagos_id in actuales:
            return MovimientoVisto(*fila, vigente=True)
    return MovimientoVisto(*filas[0], vigente=False)


def detalle(s: Session, visto: MovimientoVisto) -> DetalleDelMovimiento:
    """Un movimiento con su evidencia: la observacion que lo funda, con su archivo original, y, si
    se concilio con una cuenta, su contexto temporal entre los snapshots de esa cuenta."""
    m = visto.movimiento
    fila = s.exec(
        _resultados_vistos().where(
            ResultadoPagoObservado.ejecucion_motor_pagos_id == m.ejecucion_motor_pagos_id,
            ResultadoPagoObservado.movimiento_economico_canonico_id == m.id,
            ResultadoPagoObservado.clasificacion != Clasificacion.DUPLICADO_EXACTO.value,
        )
    ).one()
    representante = ResultadoVisto(*fila)
    original = s.exec(
        select(ArtefactoFuente)
        .join(DatasetConformado, DatasetConformado.artefacto_original_id == ArtefactoFuente.id)
        .where(DatasetConformado.id == representante.pago.dataset_conformado_id)
    ).one()
    if m.cuenta_canonica_id is None:
        return DetalleDelMovimiento(visto, representante, original, None, {})
    cuenta = s.get_one(CuentaCanonica, m.cuenta_canonica_id)
    (ubicado,) = contextos_de(s, cuenta, [m])
    return DetalleDelMovimiento(visto, representante, original, *ubicado)


@dataclass(frozen=True)
class ObservacionVista:
    visto: ResultadoVisto
    original: ArtefactoFuente


def observaciones_de(
    s: Session, visto: MovimientoVisto, *, desplazamiento: int, limite: int
) -> tuple[int, list[ObservacionVista]]:
    """Los pagos observados que sustentan el movimiento, en su ejecucion: su representante primero y
    despues sus copias exactas, cada uno con su archivo original y su fila."""
    m = visto.movimiento
    del_movimiento = and_(
        ResultadoPagoObservado.ejecucion_motor_pagos_id == m.ejecucion_motor_pagos_id,
        ResultadoPagoObservado.movimiento_economico_canonico_id == m.id,
    )
    total = s.exec(
        select(func.count()).select_from(ResultadoPagoObservado).where(del_movimiento)
    ).one()
    filas = s.exec(
        _resultados_vistos()
        .add_columns(ArtefactoFuente)
        .join(ArtefactoFuente, ArtefactoFuente.id == DatasetConformado.artefacto_original_id)
        .where(del_movimiento)
        .order_by(
            (ResultadoPagoObservado.clasificacion == Clasificacion.DUPLICADO_EXACTO.value),
            ArtefactoFuente.sha256,
            ResultadoPagoObservado.source_row,
        )
        .offset(desplazamiento)
        .limit(limite)
    ).all()
    return total, [ObservacionVista(ResultadoVisto(*fila[:5]), fila[5]) for fila in filas]


# --- la cuenta ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class MovimientoDeCuenta:
    visto: MovimientoVisto
    contexto: ContextoTemporal
    snapshots: dict[date, tuple[UUID, SnapshotCuenta]]


def movimientos_de_cuenta(
    s: Session,
    cuenta: CuentaCanonica,
    *,
    version: str,
    desde: date | None,
    hasta: date | None,
    desplazamiento: int,
    limite: int,
) -> tuple[int, list[MovimientoDeCuenta]]:
    """Una pagina de los movimientos vigentes con el CLIENTE_UNICO de la cuenta, del mas reciente al
    mas antiguo, cada uno con su contexto temporal entre los snapshots de la cuenta. Incluye los
    que se interpretaron antes de que la cuenta existiera (SIN_CUENTA_OBSERVADA): son de su cliente,
    y su contexto se calcula igual."""
    total, pagina = listar_movimientos(
        s,
        despacho_id=cuenta.despacho_id,
        cartera_id=cuenta.cartera_id,
        version=version,
        cliente_unico=cuenta.cliente_unico,
        desde=desde,
        hasta=hasta,
        desplazamiento=desplazamiento,
        limite=limite,
    )
    ubicados = contextos_de(s, cuenta, [v.movimiento for v in pagina])
    return total, [
        MovimientoDeCuenta(visto, ctx, snapshots)
        for visto, (ctx, snapshots) in zip(pagina, ubicados, strict=True)
    ]


def contextos_de(
    s: Session, cuenta: CuentaCanonica, movimientos: list[MovimientoEconomicoCanonico]
) -> list[tuple[ContextoTemporal, dict[date, tuple[UUID, SnapshotCuenta]]]]:
    """El contexto temporal de cada movimiento entre los snapshots de la cuenta, con los snapshots
    que nombra. Lee los cortes de la cuenta y los de la cartera una vez, y despues solo los
    snapshots que hacen falta, por el indice de la historia de la cuenta."""
    presentes = cuenta360.fechas_observadas(s, cuenta.id)
    cortes = [
        c.fecha_corte
        for c in cuenta360.cortes_de_la_cartera(s, cuenta.despacho_id, cuenta.cartera_id)
    ]
    contextos = [contexto.contexto(cortes, presentes, m.fecha_recepcion) for m in movimientos]
    fechas = {
        f for c in contextos for f in (c.snapshot_anterior, c.snapshot_siguiente) if f is not None
    }
    snapshots = {}
    if fechas:
        for snapshot, corte_id in s.exec(_snapshots(cuenta.id, sorted(fechas))).all():
            snapshots[snapshot.fecha_corte] = (corte_id, snapshot)
    return [
        (
            c,
            {f: snapshots[f] for f in (c.snapshot_anterior, c.snapshot_siguiente) if f is not None},
        )
        for c in contextos
    ]


def _snapshots(cuenta_canonica_id: int, fechas: list[date]) -> Select[Any]:
    return (
        select(SnapshotCuenta, CorteCanonico.corte_id)
        .join(CorteCanonico, CorteCanonico.id == SnapshotCuenta.corte_canonico_id)
        .where(
            SnapshotCuenta.cuenta_canonica_id == cuenta_canonica_id,
            SnapshotCuenta.fecha_corte.in_(fechas),
        )
    )


@dataclass(frozen=True)
class ResumenPagos:
    """Lo que la interpretacion vigente dice de los pagos de una cuenta, en numeros."""

    observaciones: int
    observaciones_interpretadas: int
    movimientos_canonicos: int
    primarios: int
    duplicados_exactos: int
    coincidencias_ambiguas: int
    reversos: int
    posibles_reversos: int
    no_conciliados: int
    pagos_anulados: int
    recuperacion_bruta_interpretada: Decimal
    recuperacion_neta_interpretada: Decimal


def resumen_de_cuenta(
    s: Session, cuenta: CuentaCanonica, *, version: str, al: date | None = None
) -> ResumenPagos:
    """Cuantas observaciones tiene la cuenta, cuantas tienen una interpretacion vigente y como quedo
    cada una, y su recuperacion interpretada. Con `al`, solo los pagos recibidos hasta el final de
    ese dia, con la interpretacion vigente hoy; y un pago cuenta como anulado solo si su reverso
    tambien habia llegado ese dia: si no, ese dia era un pago."""
    limite = None if al is None else _instante(al + timedelta(1))
    parametros = {
        "despacho": cuenta.despacho_id,
        "cartera": cuenta.cartera_id,
        "cliente": cuenta.cliente_unico,
        "version": version,
        "limite": limite,
    }
    hasta = "" if limite is None else "AND p.fecha_recepcion < :limite "
    clases = s.execute(
        text(
            "WITH vigentes AS (SELECT DISTINCT ON (periodo_desde) id, periodo_desde "
            "FROM ejecucion_motor_pagos WHERE despacho_id = :despacho AND cartera_id = :cartera "
            "AND version_motor = :version AND estado = 'EXITOSA' "
            "ORDER BY periodo_desde, id DESC) "
            "SELECT count(*), count(r.clasificacion), "
            + ", ".join(
                f"count(*) FILTER (WHERE r.clasificacion = '{clase}')"
                for clase in (
                    Clasificacion.MOVIMIENTO_PRIMARIO,
                    Clasificacion.DUPLICADO_EXACTO,
                    Clasificacion.COINCIDENCIA_AMBIGUA,
                    Clasificacion.REVERSO,
                    Clasificacion.POSIBLE_REVERSO,
                    Clasificacion.NO_CONCILIADO,
                )
            )
            + " FROM pago_observado p LEFT JOIN vigentes v "
            "ON v.periodo_desde = date_trunc('month', p.fecha_recepcion)::date "
            "LEFT JOIN resultado_pago_observado r ON r.ejecucion_motor_pagos_id = v.id "
            "AND r.dataset_conformado_id = p.dataset_conformado_id "
            "AND r.source_row = p.source_row "
            "WHERE p.despacho_id = :despacho AND p.cartera_id = :cartera "
            f"AND p.cliente_unico = :cliente {hasta}"
        ),
        parametros,
    ).one()
    actuales = vigentes(s, version)
    condiciones: list[ColumnElement] = [
        MovimientoEconomicoCanonico.ejecucion_motor_pagos_id.in_(actuales),
        MovimientoEconomicoCanonico.despacho_id == cuenta.despacho_id,
        MovimientoEconomicoCanonico.cartera_id == cuenta.cartera_id,
        MovimientoEconomicoCanonico.cliente_unico == cuenta.cliente_unico,
    ]
    reverso = aliased(MovimientoEconomicoCanonico)
    if limite is None:
        anulado = MovimientoEconomicoCanonico.anulado_por_movimiento_id.is_not(None)
    else:
        condiciones.append(MovimientoEconomicoCanonico.fecha_recepcion < limite)
        anulado = reverso.id.is_not(None)
    consulta = select(
        func.count(),
        func.count().filter(anulado),
        func.coalesce(
            func.sum(MovimientoEconomicoCanonico.monto_reportado).filter(
                MovimientoEconomicoCanonico.tipo_movimiento == "PAGO", ~anulado
            ),
            0,
        ),
        func.coalesce(
            func.sum(MovimientoEconomicoCanonico.monto_reportado).filter(
                MovimientoEconomicoCanonico.tipo_movimiento == "POSIBLE_REVERSO"
            ),
            0,
        ),
    ).select_from(MovimientoEconomicoCanonico)
    if limite is not None:
        # El reverso que anula un pago esta en la vigente de su propia ventana: una sola fila.
        consulta = consulta.outerjoin(
            reverso,
            and_(
                reverso.movimiento_id == MovimientoEconomicoCanonico.anulado_por_movimiento_id,
                reverso.ejecucion_motor_pagos_id.in_(actuales),
                reverso.fecha_recepcion < limite,
            ),
        )
    movimientos, anulados, bruta, posibles = s.exec(consulta.where(*condiciones)).one()
    observaciones, interpretadas, primarios, duplicados, ambiguas, reversos, posibles_, no_c = (
        clases
    )
    return ResumenPagos(
        observaciones=observaciones,
        observaciones_interpretadas=interpretadas,
        movimientos_canonicos=movimientos,
        primarios=primarios,
        duplicados_exactos=duplicados,
        coincidencias_ambiguas=ambiguas,
        reversos=reversos,
        posibles_reversos=posibles_,
        no_conciliados=no_c,
        pagos_anulados=anulados,
        recuperacion_bruta_interpretada=_pesos(bruta),
        recuperacion_neta_interpretada=_pesos(bruta) + _pesos(posibles),
    )


def _pesos(valor) -> Decimal:
    """Un importe con sus dos decimales, aunque la suma no tuviera ningun sumando."""
    return Decimal(valor).quantize(Decimal("0.01"))


def _instante(dia: date) -> datetime:
    """El inicio de un dia, en la hora local de la fuente, que no tiene zona."""
    return datetime.combine(dia, time())
