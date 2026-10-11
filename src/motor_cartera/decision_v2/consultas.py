"""Lo que la API lee de decision/v2: las ejecuciones, lo que decidieron y las decisiones de una
cuenta a traves del tiempo.

Cada consulta entra por un indice: una ejecucion por su decision_run_id; una pagina de resultados
por (ejecucion, prioridad o canal, cuenta) o por su llave; la decision de una cuenta en una
ejecucion, por su llave; las de una cuenta en todas, por (cuenta, ejecucion). Ninguna recorre las
cientos de miles de decisiones de una ejecucion para responder una pagina o una cuenta, y ninguna
recalcula una feature: se leen tal como se publicaron.

La API las llama con una sesion de lectura, en la que todas ven la misma foto de la base.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import and_, func
from sqlmodel import Session, select
from sqlmodel.sql.expression import Select

from motor_cartera.db.modelos import (
    CorteCanonico,
    CuentaCanonica,
    EjecucionDecisionV2,
    EstadoDecisionV2,
    FeatureDecisionCuenta,
    ResultadoDecisionV2,
    TrabajoOrquestacion,
)
from motor_cartera.decision_v2.features import FEATURES


class DecisionV2NoEncontrada(Exception):
    """No hay una ejecucion de decision/v2 con ese decision_run_id."""


class CuentaSinDecision(Exception):
    """La ejecucion no decidio esa cuenta: no es de su corte, o la ejecucion no publico."""


@dataclass(frozen=True)
class EjecucionVista:
    ejecucion: EjecucionDecisionV2
    corte_id: UUID
    fecha_corte: date
    trabajo_id: UUID | None
    fin_del_dia: datetime
    """El final de as_of en la zona de la ejecucion, con la base de zonas de PostgreSQL."""


def _ejecuciones() -> Select[Any]:
    return (
        select(
            EjecucionDecisionV2,
            CorteCanonico.corte_id,
            CorteCanonico.fecha_corte,
            TrabajoOrquestacion.trabajo_id,
            func.timezone(
                EjecucionDecisionV2.zona_horaria,
                sa.cast(EjecucionDecisionV2.as_of + 1, sa.DateTime),
            ),
        )
        .join(CorteCanonico, CorteCanonico.id == EjecucionDecisionV2.corte_canonico_id)
        .outerjoin(
            TrabajoOrquestacion,
            TrabajoOrquestacion.ejecucion_decision_v2_id == EjecucionDecisionV2.id,
        )
    )


def obtener(s: Session, decision_run_id: UUID) -> EjecucionVista:
    fila = s.exec(
        _ejecuciones().where(EjecucionDecisionV2.decision_run_id == decision_run_id)
    ).first()
    if fila is None:
        raise DecisionV2NoEncontrada(decision_run_id)
    return EjecucionVista(*fila)


def del_corte(
    s: Session, corte: CorteCanonico, *, desplazamiento: int, limite: int
) -> tuple[int, list[EjecucionVista]]:
    """Las ejecuciones de un corte, en cualquier estado, la mas reciente primero. Entra por el
    indice (corte, id)."""
    total = s.exec(
        select(func.count())
        .select_from(EjecucionDecisionV2)
        .where(EjecucionDecisionV2.corte_canonico_id == corte.id)
    ).one()
    filas = s.exec(
        _ejecuciones()
        .where(EjecucionDecisionV2.corte_canonico_id == corte.id)
        .order_by(EjecucionDecisionV2.id.desc())
        .offset(desplazamiento)
        .limit(limite)
    ).all()
    return total, [EjecucionVista(*fila) for fila in filas]


@dataclass(frozen=True)
class ResultadoVisto:
    resultado: ResultadoDecisionV2
    cuenta_id: UUID
    cliente_unico: str


def resultados(
    s: Session,
    ejecucion: EjecucionDecisionV2,
    *,
    prioridad: str | None,
    canal: str | None,
    desplazamiento: int,
    limite: int,
) -> tuple[int, list[ResultadoVisto]]:
    """Una pagina de lo que decidio la ejecucion, en el orden de la cuenta, con un filtro por
    prioridad final o por canal que entra por su indice. El total sale de la distribucion que la
    ejecucion conto al publicar, si alcanza: contar en cada pagina recorreria toda la ejecucion."""
    condiciones: list = [ResultadoDecisionV2.ejecucion_decision_v2_id == ejecucion.id]
    if prioridad is not None:
        condiciones.append(ResultadoDecisionV2.prioridad_final == prioridad)
    if canal is not None:
        condiciones.append(ResultadoDecisionV2.canal_recomendado == canal)
    distribucion = ejecucion.distribucion or {}
    if prioridad is None and canal is None:
        total = ejecucion.cuentas_decididas
    elif canal is None and distribucion:
        total = distribucion["prioridad_final"].get(prioridad, 0)
    elif prioridad is None and distribucion:
        total = distribucion["canal_recomendado"].get(canal, 0)
    else:
        total = s.exec(
            select(func.count()).select_from(ResultadoDecisionV2).where(*condiciones)
        ).one()
    filas = s.exec(
        select(ResultadoDecisionV2, CuentaCanonica.cuenta_id, CuentaCanonica.cliente_unico)
        .join(CuentaCanonica, CuentaCanonica.id == ResultadoDecisionV2.cuenta_canonica_id)
        .where(*condiciones)
        .order_by(ResultadoDecisionV2.cuenta_canonica_id)
        .offset(desplazamiento)
        .limit(limite)
    ).all()
    return total, [ResultadoVisto(*fila) for fila in filas]


@dataclass(frozen=True)
class DecisionDeCuenta:
    resultado: ResultadoDecisionV2
    features: FeatureDecisionCuenta


def de_cuenta(
    s: Session, ejecucion: EjecucionDecisionV2, cuenta: CuentaCanonica
) -> DecisionDeCuenta:
    """La decision de una cuenta en una ejecucion, con sus features tal como se publicaron: por
    su llave, sin recalcular nada."""
    fila = s.exec(
        select(ResultadoDecisionV2, FeatureDecisionCuenta)
        .join(
            FeatureDecisionCuenta,
            and_(
                FeatureDecisionCuenta.ejecucion_decision_v2_id
                == ResultadoDecisionV2.ejecucion_decision_v2_id,
                FeatureDecisionCuenta.cuenta_canonica_id == ResultadoDecisionV2.cuenta_canonica_id,
            ),
        )
        .where(
            ResultadoDecisionV2.ejecucion_decision_v2_id == ejecucion.id,
            ResultadoDecisionV2.cuenta_canonica_id == cuenta.id,
        )
    ).first()
    if fila is None:
        raise CuentaSinDecision(cuenta.cuenta_id)
    return DecisionDeCuenta(*fila)


def valores(features: FeatureDecisionCuenta) -> dict[str, Any]:
    """Las features de una fila, por nombre, en el orden de features-decision/v1."""
    return {f.nombre: getattr(features, f.nombre) for f in FEATURES}


@dataclass(frozen=True)
class DecisionEnHistoria:
    ejecucion: EjecucionDecisionV2
    resultado: ResultadoDecisionV2
    corte_id: UUID


def _de_la_cuenta(cuenta: CuentaCanonica) -> Select[Any]:
    return (
        select(EjecucionDecisionV2, ResultadoDecisionV2, CorteCanonico.corte_id)
        .join(
            ResultadoDecisionV2,
            ResultadoDecisionV2.ejecucion_decision_v2_id == EjecucionDecisionV2.id,
        )
        .join(CorteCanonico, CorteCanonico.id == EjecucionDecisionV2.corte_canonico_id)
        .where(
            ResultadoDecisionV2.cuenta_canonica_id == cuenta.id,
            EjecucionDecisionV2.estado == EstadoDecisionV2.EXITOSA,
        )
    )


def historial(
    s: Session, cuenta: CuentaCanonica, *, desplazamiento: int, limite: int
) -> tuple[int, list[DecisionEnHistoria]]:
    """Las decisiones de una cuenta en todas las ejecuciones EXITOSA, de la de as_of mas reciente a
    la mas antigua; en el mismo as_of, la de known_at mas reciente primero. Entra por el indice
    (cuenta, ejecucion): una cuenta tiene unas pocas, una por ejecucion de su corte."""
    total = s.exec(
        select(func.count())
        .select_from(ResultadoDecisionV2)
        .join(
            EjecucionDecisionV2,
            EjecucionDecisionV2.id == ResultadoDecisionV2.ejecucion_decision_v2_id,
        )
        .where(
            ResultadoDecisionV2.cuenta_canonica_id == cuenta.id,
            EjecucionDecisionV2.estado == EstadoDecisionV2.EXITOSA,
        )
    ).one()
    filas = s.exec(
        _de_la_cuenta(cuenta)
        .order_by(
            EjecucionDecisionV2.as_of.desc(),
            EjecucionDecisionV2.known_at.desc(),
            EjecucionDecisionV2.id.desc(),
        )
        .offset(desplazamiento)
        .limit(limite)
    ).all()
    return total, [DecisionEnHistoria(*fila) for fila in filas]


def ultima(s: Session, cuenta: CuentaCanonica, al: date | None = None) -> DecisionEnHistoria | None:
    """La decision v2 mas reciente de la cuenta (con `al`, la de as_of hasta ese dia)."""
    consulta = _de_la_cuenta(cuenta)
    if al is not None:
        consulta = consulta.where(EjecucionDecisionV2.as_of <= al)
    fila = s.exec(
        consulta.order_by(
            EjecucionDecisionV2.as_of.desc(),
            EjecucionDecisionV2.known_at.desc(),
            EjecucionDecisionV2.id.desc(),
        ).limit(1)
    ).first()
    return None if fila is None else DecisionEnHistoria(*fila)
