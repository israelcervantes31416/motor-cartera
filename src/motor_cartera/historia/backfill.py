"""El backfill historico: la historia de los datasets conformados que todavia no la tienen.

La migracion 0008 solo crea las tablas: no materializa ningun dataset, porque procesar millones de
filas no es trabajo de Alembic. Los datasets que se publicaron antes de v0.7 (o cuya historia
fallo) se historian con este proceso de la aplicacion, `motor-cartera backfill-historia`:

1. encuentra los datasets conformados de cartera/v2 y de pagos/v1 sin una ejecucion historia/v1
   EXITOSA;
2. los separa: los que no tienen ninguna ejecucion se encolan; los que tienen una EN_PROCESO ya
   estan en la cola; los que solo tienen ejecuciones FALLIDA se reportan, y se vuelven a encolar
   solo si se pide (`--reintentar-fallidas`): un conflicto de corte, por ejemplo, volveria a fallar;
3. encola un trabajo HISTORIA por dataset, cada uno en su propia transaccion: los de cartera en
   orden de fecha de corte y despues los de pagos. Es solo una cortesia: la historia que resulta no
   depende del orden en que se materialicen.

Es idempotente: correrlo dos veces no encola nada dos veces. Lo garantizan las mismas revisiones y
los mismos indices unicos de `abrir_historia`, y un dataset que otra ejecucion encolo entre la
revision y el INSERT se reporta como ya en la cola.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import exists
from sqlmodel import Session, select

from motor_cartera.config import Config
from motor_cartera.db.modelos import (
    Corrida,
    DatasetConformado,
    EjecucionHistoria,
    EstadoHistoria,
    IngestaPagos,
)
from motor_cartera.db.sesion import sesion
from motor_cartera.historia.ejecuciones import (
    TIPO_DEL_CONTRATO,
    VERSION_MODELO_HISTORIA,
    HistoriaEnProceso,
    HistoriaYaMaterializada,
    abrir_historia,
)


@dataclass(frozen=True)
class Pendiente:
    """Un dataset conformado sin historia EXITOSA."""

    dataset_conformado_id: int
    dataset_id: UUID
    contrato: str
    fecha_corte: date | None
    """La de su corrida, si es de cartera."""
    origen_run_id: UUID
    """El run_id de su corrida o el pagos_run_id de su ingesta."""
    ultimo_resultado: str | None
    """El resultado de su ultima ejecucion FALLIDA, si tuvo alguna."""


@dataclass(frozen=True)
class Diagnostico:
    """Cuanta historia falta."""

    datasets: int
    materializados: int
    """Con una ejecucion historia/v1 EXITOSA."""
    en_cola: int
    """Con una ejecucion EN_PROCESO: su trabajo HISTORIA ya esta en la cola."""
    sin_historia: list[Pendiente]
    """Sin ninguna ejecucion: se encolan."""
    solo_fallidas: list[Pendiente]
    """Solo con ejecuciones FALLIDA: se encolan si se pide."""


@dataclass(frozen=True)
class Encolado:
    pendiente: Pendiente
    historia_run_id: UUID | None
    """La ejecucion que se abrio, o None si otra se adelanto."""
    nota: str


def diagnosticar(s: Session) -> Diagnostico:
    """Cada dataset conformado con historia, clasificado por como esta su historia/v1."""
    de_la_version = (EjecucionHistoria.dataset_conformado_id == DatasetConformado.id) & (
        EjecucionHistoria.version_modelo == VERSION_MODELO_HISTORIA
    )

    def con_estado(estado: EstadoHistoria):
        return exists().where(de_la_version, EjecucionHistoria.estado == estado)

    ultimo_resultado = (
        select(EjecucionHistoria.resultado)
        .where(de_la_version, EjecucionHistoria.estado == EstadoHistoria.FALLIDA)
        .order_by(EjecucionHistoria.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    filas = s.exec(
        select(
            DatasetConformado.id,
            DatasetConformado.dataset_id,
            DatasetConformado.contrato,
            Corrida.fecha_corte,
            Corrida.run_id,
            IngestaPagos.pagos_run_id,
            con_estado(EstadoHistoria.EXITOSA),
            con_estado(EstadoHistoria.EN_PROCESO),
            ultimo_resultado,
        )
        .outerjoin(Corrida, Corrida.id == DatasetConformado.corrida_id)
        .outerjoin(IngestaPagos, IngestaPagos.id == DatasetConformado.ingesta_pagos_id)
        .where(DatasetConformado.contrato.in_(list(TIPO_DEL_CONTRATO)))
    ).all()
    materializados = en_cola = 0
    sin_historia: list[Pendiente] = []
    solo_fallidas: list[Pendiente] = []
    for id_, dataset_id, contrato, fecha, run_id, pagos_run_id, exitosa, activa, fallo in filas:
        if exitosa:
            materializados += 1
            continue
        if activa:
            en_cola += 1
            continue
        pendiente = Pendiente(id_, dataset_id, contrato, fecha, run_id or pagos_run_id, fallo)
        (solo_fallidas if fallo is not None else sin_historia).append(pendiente)
    return Diagnostico(
        len(filas), materializados, en_cola, _en_orden(sin_historia), _en_orden(solo_fallidas)
    )


def encolar(pendientes: list[Pendiente], *, config: Config) -> list[Encolado]:
    """Abre la historia de cada pendiente, con su trabajo HISTORIA, en su propia transaccion. Uno
    que otra ejecucion ya encolo o materializo mientras tanto se reporta y no se duplica."""
    encolados = []
    for pendiente in _en_orden(pendientes):
        with sesion() as s:
            dataset = s.get_one(DatasetConformado, pendiente.dataset_conformado_id)
            try:
                ejecucion = abrir_historia(s, dataset, max_intentos=config.worker_max_intentos)
            except (HistoriaEnProceso, HistoriaYaMaterializada) as exc:
                s.rollback()
                encolados.append(Encolado(pendiente, None, str(exc)))
                continue
            s.commit()
            encolados.append(
                Encolado(pendiente, ejecucion.historia_run_id, "historia/v1 en la cola")
            )
    return encolados


def _en_orden(pendientes: list[Pendiente]) -> list[Pendiente]:
    """Los de cartera por fecha de corte y despues los de pagos, cada grupo por su id."""
    return sorted(
        pendientes,
        key=lambda p: (
            p.fecha_corte is None,
            p.fecha_corte or date.min,
            p.dataset_conformado_id,
        ),
    )
