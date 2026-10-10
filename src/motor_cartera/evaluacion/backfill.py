"""El backfill del lifecycle: las evaluaciones de promesas que faltan a una fecha de corte.

Lo unico que el lifecycle deriva en lote es la evaluacion de sus promesas, y una evaluacion no
existe sin su fecha de corte: `motor-cartera backfill-lifecycle --as-of AAAA-MM-DD` la exige, porque
as_of nunca sale del reloj. Por cada cartera con promesas acordadas hasta ese dia:

- si ya hay una evaluacion EN_PROCESO a esa fecha, esta en la cola y no se encola otra;
- si no hay ninguna EXITOSA a esa fecha, falta, y se encola una;
- si ya hay una EXITOSA, esta al dia; con `--reevaluar` se encola otra, que solo publica si sus
  entradas cambiaron (una promesa, una cancelacion, una anulacion o un pago nuevo): con las mismas,
  queda FALLIDA con YA_EVALUADA y la anterior sigue siendo la vigente.

Es idempotente y por cartera: un trabajo EVALUACION_PROMESAS evalua todas sus promesas, nunca uno
por promesa. Si el proceso o el worker caen, la ejecucion sigue EN_PROCESO con su trabajo, y la cola
la retoma.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import text
from sqlmodel import Session

from motor_cartera.config import Config
from motor_cartera.db.sesion import sesion
from motor_cartera.evaluacion.ejecuciones import abrir
from motor_cartera.evaluacion.reglas import VERSION_EVALUACION


@dataclass(frozen=True)
class EstadoDeCartera:
    despacho_id: str
    cartera_id: str
    promesas: int
    """Las acordadas hasta el final de as_of."""
    vencidas: int
    """De esas, las de fecha limite hasta as_of."""
    vigente: UUID | None
    """La evaluacion EXITOSA mas reciente a esa fecha, si hay una."""
    en_cola: UUID | None


@dataclass(frozen=True)
class Encolada:
    cartera: EstadoDeCartera
    evaluacion_run_id: UUID
    nueva: bool


def diagnosticar(s: Session, as_of: date, *, zona: str) -> list[EstadoDeCartera]:
    """Cada cartera con promesas acordadas hasta el final de as_of, con su evaluacion a esa
    fecha."""
    filas = s.execute(
        text(
            "WITH promesas AS (SELECT e.despacho_id, e.cartera_id, count(*) AS promesas, "
            "count(*) FILTER (WHERE p.fecha_limite <= :as_of) AS vencidas "
            "FROM promesa_pago p JOIN evento_lifecycle e ON e.id = p.evento_lifecycle_id "
            "WHERE e.ocurrido_en < timezone(:zona, (CAST(:as_of AS date) + 1)::timestamp) "
            "GROUP BY 1, 2) "
            "SELECT p.despacho_id, p.cartera_id, p.promesas, p.vencidas, "
            "(SELECT x.evaluacion_run_id FROM ejecucion_evaluacion_promesas x "
            "WHERE x.despacho_id = p.despacho_id AND x.cartera_id = p.cartera_id "
            "AND x.version_evaluacion = :version AND x.as_of = :as_of AND x.estado = 'EXITOSA' "
            "ORDER BY x.id DESC LIMIT 1), "
            "(SELECT x.evaluacion_run_id FROM ejecucion_evaluacion_promesas x "
            "WHERE x.despacho_id = p.despacho_id AND x.cartera_id = p.cartera_id "
            "AND x.version_evaluacion = :version AND x.as_of = :as_of "
            "AND x.estado = 'EN_PROCESO') "
            "FROM promesas p ORDER BY 1, 2"
        ),
        {"as_of": as_of, "zona": zona, "version": VERSION_EVALUACION},
    ).all()
    return [EstadoDeCartera(*fila) for fila in filas]


def pendientes(carteras: list[EstadoDeCartera], *, reevaluar: bool) -> list[EstadoDeCartera]:
    """Las carteras que hay que encolar: sin evaluacion a esa fecha o, con `reevaluar`, tambien las
    que ya tienen una. Las que estan en la cola no."""
    return [c for c in carteras if c.en_cola is None and (c.vigente is None or reevaluar)]


def encolar(carteras: list[EstadoDeCartera], as_of: date, *, config: Config) -> list[Encolada]:
    """Abre la evaluacion de cada cartera a esa fecha, con su trabajo, en su propia transaccion. Una
    que ya tiene una EN_PROCESO la reusa."""
    encoladas = []
    for cartera in carteras:
        with sesion() as s:
            ejecucion, nueva = abrir(
                s,
                cartera.despacho_id,
                cartera.cartera_id,
                as_of,
                zona=config.zona_horaria_fuente,
                max_intentos=config.worker_max_intentos,
                reusar=True,
            )
            s.commit()
            encoladas.append(Encolada(cartera, ejecucion.evaluacion_run_id, nueva))
    return encoladas
