"""Los planes de campo/v2 que faltan, solo cuando todas sus entradas existen y no son ambiguas.

campo/v2 nunca toma "la ultima decision" ni "el ultimo territorial". El backfill tampoco: quien lo
pide declara la configuracion de campo y la fecha de operacion, que no se infiere de nada, y un
corte solo tiene un plan pendiente si tiene exactamente una decision/v2 EXITOSA y exactamente una
territorial/v2 EXITOSA sobre la version de bases de esa configuracion. Con dos o mas, el corte es
ambiguo: se reporta y no se planea, porque elegir una seria inventar la entrada. Lo que ya tiene un
plan EXITOSA o EN_PROCESO con la misma firma no se vuelve a pedir: es idempotente.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from sqlmodel import Session, col, select

from motor_cartera.campo_v2 import ejecuciones
from motor_cartera.config import Config
from motor_cartera.db.modelos import (
    CorteCanonico,
    EjecucionCampoV2,
    EjecucionDecisionV2,
    EjecucionTerritorialV2,
    EstadoCampoV2,
    EstadoDecisionV2,
    EstadoTerritorialV2,
    VersionConfiguracionCampo,
    id_de,
)


@dataclass(frozen=True)
class Pendiente:
    corte_id: UUID
    fecha_corte: date
    decision_run_id: UUID
    territorial_run_id: UUID


@dataclass(frozen=True)
class Ambiguo:
    fecha_corte: date
    decisiones: int
    territoriales: int


@dataclass
class Revision:
    """Lo que encontro el backfill, corte por corte, hasta la fecha de operacion."""

    pendientes: list[Pendiente] = field(default_factory=list)
    planeados: int = 0
    en_proceso: int = 0
    ambiguos: list[Ambiguo] = field(default_factory=list)
    sin_entradas: int = 0


def revisar(
    s: Session,
    configuracion: VersionConfiguracionCampo,
    fecha_operacion: date,
    *,
    despacho_id: str,
    cartera_id: str,
    corte: date | None = None,
) -> Revision:
    """Los cortes de la cartera hasta la fecha de operacion (o solo el de `corte`), cada uno con lo
    que le falta. No escribe nada."""
    condiciones = [
        CorteCanonico.despacho_id == despacho_id,
        CorteCanonico.cartera_id == cartera_id,
        CorteCanonico.fecha_corte <= fecha_operacion,
    ]
    if corte is not None:
        condiciones.append(CorteCanonico.fecha_corte == corte)
    cortes = s.exec(
        select(CorteCanonico).where(*condiciones).order_by(col(CorteCanonico.fecha_corte))
    ).all()
    decisiones: dict[int, list[EjecucionDecisionV2]] = defaultdict(list)
    territoriales: dict[int, list[EjecucionTerritorialV2]] = defaultdict(list)
    ids = [c.id for c in cortes]
    for d in s.exec(
        select(EjecucionDecisionV2).where(
            col(EjecucionDecisionV2.corte_canonico_id).in_(ids),
            EjecucionDecisionV2.estado == EstadoDecisionV2.EXITOSA,
        )
    ).all():
        decisiones[d.corte_canonico_id].append(d)
    for t in s.exec(
        select(EjecucionTerritorialV2).where(
            col(EjecucionTerritorialV2.corte_canonico_id).in_(ids),
            EjecucionTerritorialV2.estado == EstadoTerritorialV2.EXITOSA,
            EjecucionTerritorialV2.version_bases_operativas_id
            == configuracion.version_bases_operativas_id,
        )
    ).all():
        territoriales[t.corte_canonico_id].append(t)
    revision = Revision()
    for c in cortes:
        propias_d, propias_t = decisiones[id_de(c)], territoriales[id_de(c)]
        if not propias_d or not propias_t:
            revision.sin_entradas += 1
            continue
        if len(propias_d) > 1 or len(propias_t) > 1:
            revision.ambiguos.append(Ambiguo(c.fecha_corte, len(propias_d), len(propias_t)))
            continue
        e = ejecuciones.entradas(
            s,
            c.corte_id,
            propias_d[0].decision_run_id,
            propias_t[0].territorial_run_id,
            configuracion.configuracion_campo_version_id,
            fecha_operacion,
        )
        estados = set(
            s.exec(
                select(EjecucionCampoV2.estado).where(EjecucionCampoV2.firma_inputs == e.firma)
            ).all()
        )
        if EstadoCampoV2.EXITOSA in estados:
            revision.planeados += 1
        elif EstadoCampoV2.EN_PROCESO in estados:
            revision.en_proceso += 1
        else:
            revision.pendientes.append(
                Pendiente(
                    c.corte_id,
                    c.fecha_corte,
                    propias_d[0].decision_run_id,
                    propias_t[0].territorial_run_id,
                )
            )
    return revision


def encolar(
    s: Session,
    pendientes: list[Pendiente],
    configuracion: VersionConfiguracionCampo,
    fecha_operacion: date,
    *,
    config: Config,
) -> list[EjecucionCampoV2]:
    """Abre el plan de cada pendiente con su trabajo CAMPO_V2, en la transaccion de quien llama.
    Uno que otro proceso abrio entre la revision y este momento se salta: ya esta en la cola o ya
    se publico."""
    abiertos = []
    for p in pendientes:
        try:
            abiertos.append(
                ejecuciones.abrir(
                    s,
                    p.corte_id,
                    p.decision_run_id,
                    p.territorial_run_id,
                    configuracion.configuracion_campo_version_id,
                    fecha_operacion,
                    max_intentos=config.worker_max_intentos,
                )
            )
        except (ejecuciones.CampoV2EnProceso, ejecuciones.CampoV2YaGenerado):
            continue
    return abiertos
