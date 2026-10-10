"""Lo que la API lee de la atribucion: las ejecuciones, lo que concluyeron de cada movimiento y sus
candidatas.

La atribucion vigente de una ventana es su ejecucion EXITOSA mas reciente con la version del
servicio, como en el motor de pagos. Un movimiento se busca por su `movimiento_id`, que es el mismo
en cualquier interpretacion de los pagos, por el indice de las atribuciones de un movimiento; las de
una cuenta, por los movimientos vigentes de su cliente. Las candidatas de una pagina se leen juntas,
en una consulta. Cada resultado dice si la interpretacion de pagos que leyo sigue siendo la vigente:
si no, su ventana se debe volver a atribuir (`motor-cartera backfill-atribucion`).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, text, tuple_
from sqlmodel import Session, select
from sqlmodel.sql.expression import Select

from motor_cartera.atribucion.reglas import VERSION_ATRIBUCION
from motor_cartera.db.modelos import (
    AtribucionMovimiento,
    CandidatoAtribucion,
    CuentaCanonica,
    EjecucionAtribucion,
    EjecucionMotorPagos,
    EstadoAtribucion,
    GestionCobranza,
    MovimientoEconomicoCanonico,
    TrabajoOrquestacion,
)
from motor_cartera.motor_pagos import consultas as pagos
from motor_cartera.motor_pagos.consultas import MovimientoVisto


class AtribucionNoEncontrada(Exception):
    """No hay una ejecucion de la atribucion con ese atribucion_run_id."""


def vigentes(s: Session, version: str = VERSION_ATRIBUCION) -> list[int]:
    """Los id de las atribuciones vigentes de la version: una por ventana, su EXITOSA mas
    reciente. Son pocas (una por mes de cada cartera)."""
    return list(
        s.exec(
            select(func.max(EjecucionAtribucion.id))
            .where(
                EjecucionAtribucion.version_atribucion == version,
                EjecucionAtribucion.estado == EstadoAtribucion.EXITOSA,
            )
            .group_by(
                EjecucionAtribucion.despacho_id,
                EjecucionAtribucion.cartera_id,
                EjecucionAtribucion.periodo_desde,
            )
        ).all()
    )


# --- las ejecuciones ------------------------------------------------------------------------------


@dataclass(frozen=True)
class EjecucionVista:
    ejecucion: EjecucionAtribucion
    trabajo_id: UUID | None
    motor_pagos_run_id: UUID | None
    vigente: bool
    interpretacion_vigente: bool | None
    """Si la interpretacion de pagos que leyo sigue siendo la vigente de su ventana; None si no
    leyo ninguna."""


def _ejecuciones() -> Select[Any]:
    return (
        select(
            EjecucionAtribucion,
            TrabajoOrquestacion.trabajo_id,
            EjecucionMotorPagos.motor_pagos_run_id,
            EjecucionMotorPagos.version_motor,
        )
        .outerjoin(
            TrabajoOrquestacion,
            TrabajoOrquestacion.ejecucion_atribucion_id == EjecucionAtribucion.id,
        )
        .outerjoin(
            EjecucionMotorPagos,
            EjecucionMotorPagos.id == EjecucionAtribucion.ejecucion_motor_pagos_id,
        )
    )


def _interpretaciones_vigentes(s: Session, versiones) -> set[int]:
    return {i for version in set(versiones) - {None} for i in pagos.vigentes(s, version)}


def _vistas(s: Session, filas) -> list[EjecucionVista]:
    actuales = set(vigentes(s))
    motores = _interpretaciones_vigentes(s, (f[3] for f in filas))
    return [
        EjecucionVista(
            e,
            trabajo_id,
            motor_run_id,
            e.id in actuales,
            None if e.ejecucion_motor_pagos_id is None else e.ejecucion_motor_pagos_id in motores,
        )
        for e, trabajo_id, motor_run_id, _ in filas
    ]


def obtener(s: Session, atribucion_run_id: UUID) -> EjecucionVista:
    fila = s.exec(
        _ejecuciones().where(EjecucionAtribucion.atribucion_run_id == atribucion_run_id)
    ).first()
    if fila is None:
        raise AtribucionNoEncontrada(atribucion_run_id)
    return _vistas(s, [fila])[0]


def listar(
    s: Session,
    *,
    despacho_id: str,
    cartera_id: str,
    version: str,
    periodo: date | None,
    estado: EstadoAtribucion | None,
    desplazamiento: int,
    limite: int,
) -> tuple[int, list[EjecucionVista]]:
    """Una pagina de las atribuciones de la cartera, la ventana mas reciente primero y, dentro de
    una ventana, la ejecucion mas reciente primero."""
    condiciones: list = [
        EjecucionAtribucion.despacho_id == despacho_id,
        EjecucionAtribucion.cartera_id == cartera_id,
        EjecucionAtribucion.version_atribucion == version,
    ]
    if periodo is not None:
        condiciones.append(EjecucionAtribucion.periodo_desde == periodo)
    if estado is not None:
        condiciones.append(EjecucionAtribucion.estado == estado)
    total = s.exec(select(func.count()).select_from(EjecucionAtribucion).where(*condiciones)).one()
    filas = s.exec(
        _ejecuciones()
        .where(*condiciones)
        .order_by(EjecucionAtribucion.periodo_desde.desc(), EjecucionAtribucion.id.desc())
        .offset(desplazamiento)
        .limit(limite)
    ).all()
    return total, _vistas(s, filas)


# --- lo que concluyo de cada movimiento -----------------------------------------------------------


@dataclass(frozen=True)
class CandidataVista:
    gestion_id: UUID
    ocurrido_en: datetime
    canal: str
    nivel_contacto: str
    resultado: str
    antelacion_segundos: int


@dataclass(frozen=True)
class ResultadoVisto:
    """Lo que una ejecucion concluyo de un movimiento, con lo que lo explica."""

    resultado: AtribucionMovimiento
    ejecucion: EjecucionAtribucion
    vigente: bool
    """Si su ejecucion es la atribucion vigente de su ventana."""
    motor_pagos_run_id: UUID
    interpretacion_vigente: bool
    """Si la interpretacion de pagos que leyo sigue siendo la vigente de su ventana."""
    cuenta_id: UUID | None
    gestion_id: UUID | None
    candidatas: tuple[CandidataVista, ...]


def _resultados() -> Select[Any]:
    asociada = GestionCobranza.__table__.alias("asociada")
    return (
        select(
            AtribucionMovimiento,
            EjecucionAtribucion,
            EjecucionMotorPagos.motor_pagos_run_id,
            EjecucionMotorPagos.version_motor,
            CuentaCanonica.cuenta_id,
            asociada.c.gestion_id,
        )
        .join(
            EjecucionAtribucion,
            EjecucionAtribucion.id == AtribucionMovimiento.ejecucion_atribucion_id,
        )
        .join(
            EjecucionMotorPagos,
            EjecucionMotorPagos.id == EjecucionAtribucion.ejecucion_motor_pagos_id,
        )
        .outerjoin(CuentaCanonica, CuentaCanonica.id == AtribucionMovimiento.cuenta_canonica_id)
        .outerjoin(asociada, asociada.c.id == AtribucionMovimiento.gestion_cobranza_id)
    )


def _con_candidatas(s: Session, filas) -> list[ResultadoVisto]:
    """Cada resultado con sus candidatas, leidas todas en una consulta, de la mas proxima al
    movimiento a la mas lejana: un orden para leerlas, no una preferencia."""
    pares = [(f[0].ejecucion_atribucion_id, f[0].movimiento_economico_canonico_id) for f in filas]
    por_par: dict[tuple[int, int], list[CandidataVista]] = defaultdict(list)
    if pares:
        for candidato, gestion in s.exec(
            select(CandidatoAtribucion, GestionCobranza)
            .join(GestionCobranza, GestionCobranza.id == CandidatoAtribucion.gestion_cobranza_id)
            .where(
                tuple_(
                    CandidatoAtribucion.ejecucion_atribucion_id,
                    CandidatoAtribucion.movimiento_economico_canonico_id,
                ).in_(pares)
            )
            .order_by(CandidatoAtribucion.antelacion_segundos, GestionCobranza.gestion_id)
        ).all():
            llave = (candidato.ejecucion_atribucion_id, candidato.movimiento_economico_canonico_id)
            por_par[llave].append(
                CandidataVista(
                    gestion.gestion_id,
                    gestion.ocurrido_en,
                    gestion.canal,
                    gestion.nivel_contacto,
                    gestion.resultado,
                    candidato.antelacion_segundos,
                )
            )
    actuales = set(vigentes(s))
    motores = _interpretaciones_vigentes(s, (f[3] for f in filas))
    return [
        ResultadoVisto(
            resultado,
            ejecucion,
            ejecucion.id in actuales,
            motor_run_id,
            ejecucion.ejecucion_motor_pagos_id in motores,
            cuenta_id,
            gestion_id,
            tuple(
                por_par[
                    (resultado.ejecucion_atribucion_id, resultado.movimiento_economico_canonico_id)
                ]
            ),
        )
        for resultado, ejecucion, motor_run_id, _, cuenta_id, gestion_id in filas
    ]


def resultados_de(
    s: Session,
    ejecucion: EjecucionAtribucion,
    *,
    clasificacion: str | None,
    cliente_unico: str | None,
    desplazamiento: int,
    limite: int,
) -> tuple[int, list[ResultadoVisto]]:
    """Una pagina de lo que la ejecucion concluyo de cada movimiento, en orden de recepcion."""
    condiciones: list = [AtribucionMovimiento.ejecucion_atribucion_id == ejecucion.id]
    if clasificacion is not None:
        condiciones.append(AtribucionMovimiento.clasificacion == clasificacion)
    if cliente_unico is not None:
        # Las de un cliente: sus movimientos en la interpretacion que se leyo, por el indice de
        # los movimientos de una cuenta, y cada resultado por su llave.
        condiciones.append(
            AtribucionMovimiento.movimiento_economico_canonico_id.in_(
                select(MovimientoEconomicoCanonico.id).where(
                    MovimientoEconomicoCanonico.despacho_id == ejecucion.despacho_id,
                    MovimientoEconomicoCanonico.cartera_id == ejecucion.cartera_id,
                    MovimientoEconomicoCanonico.cliente_unico == cliente_unico,
                    MovimientoEconomicoCanonico.ejecucion_motor_pagos_id
                    == ejecucion.ejecucion_motor_pagos_id,
                )
            )
        )
    total = s.exec(select(func.count()).select_from(AtribucionMovimiento).where(*condiciones)).one()
    filas = s.exec(
        _resultados()
        .where(*condiciones)
        .order_by(
            AtribucionMovimiento.fecha_recepcion,
            AtribucionMovimiento.movimiento_economico_canonico_id,
        )
        .offset(desplazamiento)
        .limit(limite)
    ).all()
    return total, _con_candidatas(s, filas)


def de_movimiento(
    s: Session, movimiento_id: UUID, *, desplazamiento: int, limite: int
) -> tuple[int, list[ResultadoVisto]]:
    """Cada atribucion de un movimiento, en cualquier interpretacion de los pagos y en cualquier
    ejecucion, de la mas reciente a la mas antigua: la vigente y las que la precedieron, que no
    cambian."""
    condicion = AtribucionMovimiento.movimiento_id == movimiento_id
    total = s.exec(select(func.count()).select_from(AtribucionMovimiento).where(condicion)).one()
    filas = s.exec(
        _resultados()
        .where(condicion)
        .order_by(AtribucionMovimiento.ejecucion_atribucion_id.desc())
        .offset(desplazamiento)
        .limit(limite)
    ).all()
    return total, _con_candidatas(s, filas)


@dataclass(frozen=True)
class MovimientoAtribuido:
    movimiento: MovimientoVisto
    atribucion: ResultadoVisto | None
    """La de la atribucion vigente de su ventana; None si su ventana no tiene una que lo trate."""


def de_cuenta(
    s: Session,
    cuenta: CuentaCanonica,
    *,
    version_motor: str,
    desplazamiento: int,
    limite: int,
) -> tuple[int, list[MovimientoAtribuido]]:
    """Una pagina de los PAGO vigentes de la cuenta, del mas reciente al mas antiguo, cada uno con
    lo que dice de el la atribucion vigente de su ventana. Entra por el indice de los movimientos
    de una cuenta, y cada atribucion por el de las atribuciones de un movimiento."""
    total, pagina = pagos.listar_movimientos(
        s,
        despacho_id=cuenta.despacho_id,
        cartera_id=cuenta.cartera_id,
        version=version_motor,
        cliente_unico=cuenta.cliente_unico,
        tipo="PAGO",
        desplazamiento=desplazamiento,
        limite=limite,
    )
    ids = [v.movimiento.movimiento_id for v in pagina]
    actuales = vigentes(s)
    filas = (
        s.exec(
            _resultados().where(
                AtribucionMovimiento.movimiento_id.in_(ids),
                AtribucionMovimiento.ejecucion_atribucion_id.in_(actuales),
            )
        ).all()
        if ids and actuales
        else []
    )
    por_movimiento = {r.resultado.movimiento_id: r for r in _con_candidatas(s, filas)}
    return total, [
        MovimientoAtribuido(v, por_movimiento.get(v.movimiento.movimiento_id)) for v in pagina
    ]


def ultima_de_cuenta(
    s: Session, cuenta: CuentaCanonica, *, version_motor: str, al: date | None = None
) -> ResultadoVisto | None:
    """La ultima atribucion disponible de la cuenta: lo que la atribucion vigente de su ventana dice
    del PAGO vigente mas reciente de la cuenta que tiene una. Con `al`, de los recibidos hasta el
    final de ese dia. Entra por el indice de los movimientos de una cuenta, en orden de recepcion,
    y se detiene en el primero atribuido."""
    atribuciones = vigentes(s)
    motores = pagos.vigentes(s, version_motor)
    if not atribuciones or not motores:
        return None
    limite = None if al is None else datetime.combine(al + timedelta(1), time())
    fila = s.execute(
        text(
            "SELECT a.ejecucion_atribucion_id, a.movimiento_economico_canonico_id "
            "FROM movimiento_economico_canonico m "
            "JOIN atribucion_movimiento a ON a.movimiento_id = m.movimiento_id "
            "AND a.ejecucion_atribucion_id = ANY(:atribuciones) "
            "WHERE m.despacho_id = :despacho AND m.cartera_id = :cartera "
            "AND m.cliente_unico = :cliente AND m.ejecucion_motor_pagos_id = ANY(:motores) "
            "AND m.tipo_movimiento = 'PAGO' "
            "AND (CAST(:limite AS timestamp) IS NULL OR m.fecha_recepcion < :limite) "
            "ORDER BY m.fecha_recepcion DESC, m.movimiento_id, a.ejecucion_atribucion_id DESC "
            "LIMIT 1"
        ),
        {
            "atribuciones": atribuciones,
            "motores": motores,
            "despacho": cuenta.despacho_id,
            "cartera": cuenta.cartera_id,
            "cliente": cuenta.cliente_unico,
            "limite": limite,
        },
    ).first()
    if fila is None:
        return None
    filas = s.exec(
        _resultados().where(
            AtribucionMovimiento.ejecucion_atribucion_id == fila[0],
            AtribucionMovimiento.movimiento_economico_canonico_id == fila[1],
        )
    ).all()
    return _con_candidatas(s, filas)[0]
