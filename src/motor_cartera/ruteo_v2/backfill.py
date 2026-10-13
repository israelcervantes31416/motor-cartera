"""Las ejecuciones de ruteo/v2 que faltan, solo sobre una version de la red vial declarada.

ruteo/v2 nunca toma "la ultima red" ni "el ultimo plan". El backfill tampoco: quien lo pide declara
la version de la red, y una ejecucion pendiente es un plan de campo/v2 EXITOSA (de una fecha de
operacion, si se declara) que todavia no tiene una ejecucion EXITOSA o EN_PROCESO con la misma firma
sobre esa red. Cada plan es una entrada exacta, asi que no hay planes ambiguos. Es idempotente.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlmodel import Session, select

from motor_cartera.config import Config
from motor_cartera.db.modelos import (
    EjecucionCampoV2,
    EjecucionRuteoV2,
    EstadoCampoV2,
    EstadoRuteoV2,
    VersionRedVial,
)
from motor_cartera.ruteo_v2 import ejecuciones


@dataclass
class Revision:
    """Lo que encontro el backfill, plan por plan."""

    pendientes: list[EjecucionCampoV2] = field(default_factory=list)
    ruteados: int = 0
    en_proceso: int = 0


def revisar(s: Session, red: VersionRedVial, fecha_operacion: date | None = None) -> Revision:
    """Los planes de campo/v2 EXITOSA (de esa fecha de operacion, si se declara) y lo que a cada uno
    le falta sobre esa red. No escribe nada."""
    consulta = select(EjecucionCampoV2).where(EjecucionCampoV2.estado == EstadoCampoV2.EXITOSA)
    if fecha_operacion is not None:
        consulta = consulta.where(EjecucionCampoV2.fecha_operacion == fecha_operacion)
    revision = Revision()
    for plan in s.exec(consulta.order_by(EjecucionCampoV2.fecha_operacion, EjecucionCampoV2.id)):
        e = ejecuciones.entradas(s, plan.campo_run_id, red.red_vial_version_id)
        estados = set(
            s.exec(
                select(EjecucionRuteoV2.estado).where(EjecucionRuteoV2.firma_inputs == e.firma)
            ).all()
        )
        if EstadoRuteoV2.EXITOSA in estados:
            revision.ruteados += 1
        elif EstadoRuteoV2.EN_PROCESO in estados:
            revision.en_proceso += 1
        else:
            revision.pendientes.append(plan)
    return revision


def encolar(
    s: Session, pendientes: list[EjecucionCampoV2], red: VersionRedVial, *, config: Config
) -> list[EjecucionRuteoV2]:
    """Abre una ejecucion por plan pendiente, con su trabajo RUTEO_V2, en la transaccion de quien
    llama. Lo que se abrio entre la revision y aqui (otra peticion) se salta."""
    abiertas = []
    for plan in pendientes:
        try:
            abiertas.append(
                ejecuciones.abrir(
                    s,
                    plan.campo_run_id,
                    red.red_vial_version_id,
                    max_intentos=config.worker_max_intentos,
                )
            )
        except (ejecuciones.RuteoV2EnProceso, ejecuciones.RuteoV2YaGenerado):
            continue
    return abiertas
