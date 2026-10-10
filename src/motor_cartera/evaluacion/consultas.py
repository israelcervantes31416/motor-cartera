"""Lo que la API lee de la evaluacion de las promesas: las ejecuciones y lo que concluyeron.

La ultima evaluacion de una promesa, la que se muestra con ella, esta en `lifecycle.consultas`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func
from sqlmodel import Session, select
from sqlmodel.sql.expression import Select

from motor_cartera.db.modelos import (
    CuentaCanonica,
    EjecucionEvaluacionPromesas,
    EstadoEvaluacionPromesas,
    EvaluacionPromesa,
    PromesaPago,
    TrabajoOrquestacion,
)
from motor_cartera.evaluacion.reglas import EstadoEvaluacion


class EvaluacionNoEncontrada(Exception):
    pass


@dataclass(frozen=True)
class EjecucionVista:
    ejecucion: EjecucionEvaluacionPromesas
    trabajo_id: UUID | None


def _ejecuciones() -> Select[Any]:
    return select(EjecucionEvaluacionPromesas, TrabajoOrquestacion.trabajo_id).outerjoin(
        TrabajoOrquestacion,
        TrabajoOrquestacion.ejecucion_evaluacion_promesas_id == EjecucionEvaluacionPromesas.id,
    )


def obtener(s: Session, evaluacion_run_id: UUID) -> EjecucionVista:
    fila = s.exec(
        _ejecuciones().where(EjecucionEvaluacionPromesas.evaluacion_run_id == evaluacion_run_id)
    ).first()
    if fila is None:
        raise EvaluacionNoEncontrada(evaluacion_run_id)
    return EjecucionVista(*fila)


def listar(
    s: Session,
    *,
    despacho_id: str,
    cartera_id: str,
    as_of: date | None,
    estado: EstadoEvaluacionPromesas | None,
    desplazamiento: int,
    limite: int,
) -> tuple[int, list[EjecucionVista]]:
    """Una pagina de las evaluaciones de la cartera, la de fecha de corte mas reciente primero y,
    dentro de una fecha, la ejecucion mas reciente primero."""
    condiciones: list = [
        EjecucionEvaluacionPromesas.despacho_id == despacho_id,
        EjecucionEvaluacionPromesas.cartera_id == cartera_id,
    ]
    if as_of is not None:
        condiciones.append(EjecucionEvaluacionPromesas.as_of == as_of)
    if estado is not None:
        condiciones.append(EjecucionEvaluacionPromesas.estado == estado)
    total = s.exec(
        select(func.count()).select_from(EjecucionEvaluacionPromesas).where(*condiciones)
    ).one()
    filas = s.exec(
        _ejecuciones()
        .where(*condiciones)
        .order_by(EjecucionEvaluacionPromesas.as_of.desc(), EjecucionEvaluacionPromesas.id.desc())
        .offset(desplazamiento)
        .limit(limite)
    ).all()
    return total, [EjecucionVista(*fila) for fila in filas]


@dataclass(frozen=True)
class EvaluacionVista:
    """Una evaluacion con su promesa y su cuenta."""

    evaluacion: EvaluacionPromesa
    promesa_id: UUID
    monto_prometido: Decimal
    fecha_limite: date
    cuenta_id: UUID
    cliente_unico: str


def evaluaciones_de(
    s: Session,
    ejecucion: EjecucionEvaluacionPromesas,
    *,
    estado: str | None,
    desplazamiento: int,
    limite: int,
) -> tuple[int, list[EvaluacionVista]]:
    """Una pagina de lo que la ejecucion concluyo de cada promesa, en el orden de su llave."""
    condiciones: list = [EvaluacionPromesa.ejecucion_evaluacion_promesas_id == ejecucion.id]
    if estado is not None:
        condiciones.append(EvaluacionPromesa.estado == estado)
    # Los conteos de la ejecucion, que se escribieron con sus evaluaciones en la transaccion que
    # las publico: contarlas en cada pagina recorreria las de toda la cartera.
    total = {
        None: ejecucion.promesas_evaluadas,
        EstadoEvaluacion.PENDIENTE: ejecucion.pendientes,
        EstadoEvaluacion.CUMPLIDA: ejecucion.cumplidas,
        EstadoEvaluacion.PARCIAL: ejecucion.parciales,
        EstadoEvaluacion.INCUMPLIDA: ejecucion.incumplidas,
        EstadoEvaluacion.CANCELADA: ejecucion.canceladas,
        EstadoEvaluacion.NO_EVALUABLE: ejecucion.no_evaluables,
    }[estado]
    filas = s.exec(
        select(
            EvaluacionPromesa,
            PromesaPago.promesa_id,
            PromesaPago.monto_prometido,
            PromesaPago.fecha_limite,
            CuentaCanonica.cuenta_id,
            CuentaCanonica.cliente_unico,
        )
        .join(PromesaPago, PromesaPago.id == EvaluacionPromesa.promesa_pago_id)
        .join(CuentaCanonica, CuentaCanonica.id == PromesaPago.cuenta_canonica_id)
        .where(*condiciones)
        .order_by(EvaluacionPromesa.promesa_pago_id)
        .offset(desplazamiento)
        .limit(limite)
    ).all()
    return total, [EvaluacionVista(*fila) for fila in filas]
