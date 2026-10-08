"""La cola durable: los trabajos de la orquestacion, sobre PostgreSQL.

Un trabajo es ejecutar un motor sobre un recurso que ya existe EN_PROCESO. Nace PENDIENTE, en la
misma transaccion que su recurso. Un worker lo toma con FOR UPDATE SKIP LOCKED, lo marca EJECUTANDO
con su worker_id y un lease, y confirma eso antes de ejecutar nada. Mientras trabaja, su latido
renueva el lease. Si el worker muere, el latido se detiene, el lease vence y otro worker lo vuelve a
tomar: la misma fila, con un intento mas.

La entrega es al menos una vez, no exactamente una: un trabajo se puede ejecutar mas de una vez. Lo
que hace segura la repeticion son los motores (sus bloqueos, sus estados terminales y sus
transacciones todo o nada), no la cola. El lease decide quien debe intentar ejecutar un trabajo; el
bloqueo de cada motor sigue decidiendo quien puede cambiar su ejecucion.

Este modulo no sabe de motores ni de flujos: solo de trabajos, de sus duenos y de sus tiempos, todos
con el reloj de PostgreSQL. Asi un worker con el reloj adelantado no le acorta el lease a otro.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import and_, case, func, or_, update
from sqlmodel import Session, select

from motor_cartera.db.modelos import EstadoTrabajo, TipoTrabajo, TrabajoOrquestacion
from motor_cartera.db.sesion import sesion
from motor_cartera.orquestacion.objetivos import OBJETIVOS

log = logging.getLogger(__name__)

LEASE_VENCIDO = (
    "El worker que lo tenia dejo de latir y vencio su lease; otro worker lo volvio a tomar."
)
"""El ultimo_error de un trabajo que se vuelve a tomar porque su worker dejo de latir."""


class TrabajoAjeno(Exception):
    """El trabajo ya no es de este worker: su lease vencio y otro lo tomo. No le toca cerrarlo."""


_EN_CURSO: ContextVar[tuple[int, str] | None] = ContextVar("trabajo_en_curso", default=None)
"""El trabajo que ejecuta este hilo y su worker, mientras el worker ejecuta su motor."""


@dataclass(frozen=True)
class Reclamo:
    """Un trabajo recien tomado por un worker: lo que hace falta para ejecutarlo y cerrarlo, leido
    en la misma transaccion que lo tomo."""

    id: int
    trabajo_id: UUID
    tipo: TipoTrabajo
    objetivo_id: int
    flujo_id: int | None
    intentos: int
    """Las veces que se ha tomado para ejecutarlo, contando esta."""
    max_intentos: int
    ejecutar: bool
    """False si el lease que vencio era el del ultimo intento: ya no se ejecuta, solo se cierra."""


def espera_de_reintento(intentos: int, base_segundos: float) -> float:
    """Cuanto espera un trabajo antes de que se pueda volver a tomar, tras fallar su intento numero
    `intentos`: la base, el doble, el cuadruple... Con la base de 1 segundo y 5 intentos: 1, 2, 4,
    8 y 16. Sin azar, para que se pueda fijar en una prueba."""
    if intentos < 1:
        raise ValueError(f"Un trabajo que fallo se tomo al menos una vez: {intentos} intentos.")
    return base_segundos * 2 ** (intentos - 1)


def crear(
    s: Session,
    tipo: TipoTrabajo,
    objetivo_id: int,
    *,
    max_intentos: int,
    flujo_id: int | None = None,
    tomado_por: str | None = None,
    lease_segundos: float | None = None,
) -> TrabajoOrquestacion:
    """Registra el trabajo de un recurso recien abierto, en la transaccion de quien llama: envia el
    INSERT pero no confirma. Quien llama confirma el recurso y su trabajo juntos, y asi no hay un
    instante en que exista uno sin el otro.

    Nace PENDIENTE y disponible desde ya. Con `tomado_por`, nace ya tomado por ese worker, en su
    primer intento y con su lease: es el trabajo que un proceso abre para ejecutarlo el mismo, en
    primer plano, sin que otro worker se le adelante. Si ese proceso muere, el lease vence y la cola
    lo recupera como a cualquier otro.
    """
    ahora = func.now()
    trabajo = TrabajoOrquestacion(
        tipo=tipo,
        flujo_id=flujo_id,
        max_intentos=max_intentos,
        creado_en=ahora,
        disponible_desde=ahora,
        **{OBJETIVOS[tipo].columna: objetivo_id},
    )
    if tomado_por is not None:
        if lease_segundos is None:
            raise ValueError("Un trabajo que nace tomado necesita su lease.")
        trabajo.estado = EstadoTrabajo.EJECUTANDO
        trabajo.intentos = 1
        trabajo.worker_id = tomado_por
        trabajo.tomado_en = trabajo.latido_en = ahora
        trabajo.lease_hasta = _lease(lease_segundos)
    s.add(trabajo)
    s.flush()
    return trabajo


def reclamo_de(trabajo: TrabajoOrquestacion) -> Reclamo:
    """Lo que el worker que tiene el trabajo necesita para ejecutarlo y cerrarlo."""
    return Reclamo(
        id=trabajo.id,
        trabajo_id=trabajo.trabajo_id,
        tipo=trabajo.tipo,
        objetivo_id=getattr(trabajo, OBJETIVOS[trabajo.tipo].columna),
        flujo_id=trabajo.flujo_id,
        intentos=trabajo.intentos,
        max_intentos=trabajo.max_intentos,
        ejecutar=True,
    )


def reclamar(worker_id: str, lease_segundos: float) -> Reclamo | None:
    """Toma un trabajo para `worker_id`, o None si no hay ninguno que tomar.

    Se puede tomar un trabajo PENDIENTE que ya este disponible, o uno EJECUTANDO cuyo lease vencio:
    su worker dejo de latir. De los que se pueden tomar, primero los del flujo operacional y las
    ingestas, despues los HISTORIA y al final los MOTOR_PAGOS (ver `_prioridad`); entre iguales, el
    de menor id. Uno a la
    vez. FOR UPDATE SKIP LOCKED: si otro worker esta tomando o cerrando una fila en este momento, la
    consulta la salta en lugar de esperarla, asi que dos workers nunca toman la misma y ninguno
    espera al otro.

    Tomarlo cuenta un intento, salvo que el lease vencido fuera el del ultimo: ese trabajo no se
    vuelve a ejecutar, y quien lo toma solo lo cierra. Se confirma antes de ejecutar nada: si el
    worker muere despues, la base ya sabe que lo tenia, y hasta cuando.
    """
    with sesion() as s:
        trabajo = s.exec(
            select(TrabajoOrquestacion)
            .where(_reclamable())
            .order_by(_prioridad(), TrabajoOrquestacion.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).first()
        if trabajo is None:
            return None
        ejecutar = trabajo.intentos < trabajo.max_intentos
        intentos = trabajo.intentos + 1 if ejecutar else trabajo.intentos
        anterior = trabajo.worker_id
        valores = {
            "estado": EstadoTrabajo.EJECUTANDO,
            "worker_id": worker_id,
            "intentos": intentos,
            "tomado_en": func.now(),
            "latido_en": func.now(),
            "lease_hasta": _lease(lease_segundos),
        }
        if trabajo.estado == EstadoTrabajo.EJECUTANDO:
            valores["ultimo_error"] = LEASE_VENCIDO
        reclamo = Reclamo(
            id=trabajo.id,
            trabajo_id=trabajo.trabajo_id,
            tipo=trabajo.tipo,
            objetivo_id=getattr(trabajo, OBJETIVOS[trabajo.tipo].columna),
            flujo_id=trabajo.flujo_id,
            intentos=intentos,
            max_intentos=trabajo.max_intentos,
            ejecutar=ejecutar,
        )
        s.execute(
            update(TrabajoOrquestacion)
            .where(TrabajoOrquestacion.id == trabajo.id)
            .values(**valores)
            .execution_options(synchronize_session=False)
        )
        s.commit()
    if anterior is not None:
        log.warning(
            "trabajo %s: vencio el lease de %s; lo toma %s (intento %s de %s)",
            reclamo.trabajo_id,
            anterior,
            worker_id,
            reclamo.intentos,
            reclamo.max_intentos,
        )
    return reclamo


def renovar(trabajo_id: int, worker_id: str, lease_segundos: float) -> bool:
    """El latido: extiende el lease, solo si el trabajo sigue EJECUTANDO y sigue siendo de
    `worker_id`. False si ya no lo es: su lease vencio y otro worker lo tomo, y este ya no debe
    cerrarlo. Usa su propia sesion, para latir mientras el motor trabaja en la suya."""
    with sesion() as s:
        renovados = s.execute(
            update(TrabajoOrquestacion)
            .where(
                TrabajoOrquestacion.id == trabajo_id,
                TrabajoOrquestacion.estado == EstadoTrabajo.EJECUTANDO,
                TrabajoOrquestacion.worker_id == worker_id,
            )
            .values(latido_en=func.now(), lease_hasta=_lease(lease_segundos))
            .execution_options(synchronize_session=False)
        ).rowcount
        s.commit()
    return renovados == 1


@contextmanager
def en_curso(trabajo_id: int, worker_id: str) -> Iterator[None]:
    """Marca, mientras dura, que este hilo ejecuta el trabajo `trabajo_id` para `worker_id`: un
    motor que publica con `confirmar_dueno` sabe asi de quien es la publicacion. Fuera de un
    worker (una prueba, un proceso en primer plano sin trabajo) no hay marca."""
    marca = _EN_CURSO.set((trabajo_id, worker_id))
    try:
        yield
    finally:
        _EN_CURSO.reset(marca)


def confirmar_dueno(s: Session) -> None:
    """La ultima palabra antes de publicar: si este hilo ejecuta un trabajo, ese trabajo tiene que
    seguir EJECUTANDO y ser de su worker. Lo bloquea, en la transaccion de quien llama, hasta su
    commit: mientras tanto ningun otro worker lo puede tomar (su SKIP LOCKED lo salta), asi que el
    lease no puede cambiar de dueno entre esta revision y la publicacion. Si ya no es suyo, levanta
    TrabajoAjeno y quien llama no publica: lo hara el dueno vigente, que lo esta esperando.

    Sin un trabajo en curso no revisa nada."""
    actual = _EN_CURSO.get()
    if actual is None:
        return
    trabajo_id, worker_id = actual
    if tomar_para_cerrar(s, trabajo_id, worker_id) is None:
        raise TrabajoAjeno(
            f"El trabajo {trabajo_id} ya no es de {worker_id}: perdio su lease y no publica."
        )


def tomar_para_cerrar(s: Session, trabajo_id: int, worker_id: str) -> TrabajoOrquestacion | None:
    """El trabajo con su fila bloqueada, si sigue EJECUTANDO y es de `worker_id`; si no, None.

    Es lo primero que hace la transaccion que cierra un trabajo. Con la fila bloqueada, ningun otro
    worker la puede tomar mientras se cierra (su SKIP LOCKED la salta), asi que quien la tiene
    sigue siendo su dueno hasta el commit, aunque el lease venza a la mitad.
    """
    return s.exec(
        select(TrabajoOrquestacion)
        .where(
            TrabajoOrquestacion.id == trabajo_id,
            TrabajoOrquestacion.estado == EstadoTrabajo.EJECUTANDO,
            TrabajoOrquestacion.worker_id == worker_id,
        )
        .with_for_update()
    ).one_or_none()


def completar(s: Session, trabajo_id: int, worker_id: str) -> None:
    """COMPLETADO: su recurso ya llego a un estado terminal, el que sea. En la transaccion de quien
    llama, que en el mismo commit encadena lo que sigue."""
    _cerrar(
        s,
        trabajo_id,
        worker_id,
        estado=EstadoTrabajo.COMPLETADO,
        terminado_en=func.now(),
        worker_id=None,
        lease_hasta=None,
    )


def devolver(
    s: Session, trabajo_id: int, worker_id: str, *, espera_segundos: float, error: str
) -> None:
    """De vuelta a PENDIENTE, sin dueno ni lease, disponible cuando pase la espera: el intento fallo
    sin que su recurso terminara, y todavia le quedan intentos."""
    _cerrar(
        s,
        trabajo_id,
        worker_id,
        estado=EstadoTrabajo.PENDIENTE,
        disponible_desde=func.now() + timedelta(seconds=espera_segundos),
        worker_id=None,
        lease_hasta=None,
        ultimo_error=error[:500],
    )


def agotar(s: Session, trabajo_id: int, worker_id: str, *, error: str) -> None:
    """FALLIDO: agoto sus intentos sin que su recurso llegara a un estado terminal. Es lo unico que
    deja un trabajo FALLIDO; un motor que termina FALLIDA deja su trabajo COMPLETADO."""
    _cerrar(
        s,
        trabajo_id,
        worker_id,
        estado=EstadoTrabajo.FALLIDO,
        terminado_en=func.now(),
        worker_id=None,
        lease_hasta=None,
        ultimo_error=error[:500],
    )


def _cerrar(s: Session, trabajo_id: int, dueno: str, **valores: object) -> None:
    """El UPDATE que cierra un trabajo, condicionado a que siga EJECUTANDO y siga siendo de `dueno`:
    un dueno anterior, que perdio el lease, nunca lo cierra."""
    cerrados = s.execute(
        update(TrabajoOrquestacion)
        .where(
            TrabajoOrquestacion.id == trabajo_id,
            TrabajoOrquestacion.estado == EstadoTrabajo.EJECUTANDO,
            TrabajoOrquestacion.worker_id == dueno,
        )
        .values(**valores)
        .execution_options(synchronize_session=False)
    ).rowcount
    if cerrados != 1:
        raise TrabajoAjeno(f"El trabajo {trabajo_id} ya no es de {dueno}: no le toca cerrarlo.")


def _reclamable():
    """Lo que un worker puede tomar: un PENDIENTE ya disponible, o un EJECUTANDO con el lease
    vencido."""
    return or_(
        and_(
            TrabajoOrquestacion.estado == EstadoTrabajo.PENDIENTE,
            TrabajoOrquestacion.disponible_desde <= func.now(),
        ),
        and_(
            TrabajoOrquestacion.estado == EstadoTrabajo.EJECUTANDO,
            TrabajoOrquestacion.lease_hasta < func.now(),
        ),
    )


def _prioridad():
    """0 para lo operacional, 1 para la historia y 2 para el motor de pagos. La historia y la
    interpretacion de los pagos son proyecciones paralelas: ninguna etapa del flujo las espera, y
    con un solo worker tampoco espera detras de ellas. Un backfill de cientos de trabajos HISTORIA
    no retrasa la decision de la cartera de hoy. El motor de pagos va al final porque interpreta lo
    que la historia publica: con los HISTORIA pendientes primero, una ventana se interpreta una vez
    con todos sus archivos, y no una vez por archivo. No cambia que se ejecuta ni como: solo cual
    de los que ya se pueden tomar va primero."""
    return case(
        (TrabajoOrquestacion.tipo == TipoTrabajo.HISTORIA, 1),
        (TrabajoOrquestacion.tipo == TipoTrabajo.MOTOR_PAGOS, 2),
        else_=0,
    )


def _lease(segundos: float):
    return func.now() + timedelta(seconds=segundos)
