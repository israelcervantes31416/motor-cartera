"""El backfill del motor de pagos: la interpretacion de los pagos observados que no la tienen.

La migracion 0009 solo crea las tablas: no interpreta ningun pago, porque procesar millones de filas
no es trabajo de Alembic. Los pagos observados que se publicaron antes de v0.8 (o cuya
interpretacion fallo) se interpretan con este proceso de la aplicacion, `motor-cartera
backfill-motor-pagos`, por ventanas, que es como trabaja el motor:

1. agrupa los pagos observados por ventana (despacho, cartera y mes de recepcion) y compara
   cada una con su interpretacion vigente con motor-pagos/v1 (su EXITOSA mas reciente);
2. las separa: al dia (su vigente leyo todos sus pagos; un pago observado no se borra, asi que
   contar basta), en la cola (tiene una EN_PROCESO), sin interpretacion, desactualizadas (llegaron
   pagos que su vigente no ve), solo con ejecuciones FALLIDA, y por conciliar (al dia, pero con
   movimientos SIN_CUENTA_OBSERVADA cuyo cliente ya tiene cuenta canonica);
3. encola una ejecucion por ventana pendiente, cada una en su propia transaccion: las sin
   interpretacion y las desactualizadas siempre; las que solo fallaron, con
   `--reintentar-fallidas`; y las por conciliar, con `--reconciliar`, que es una interpretacion
   nueva y no una correccion de la anterior.

Es idempotente: una ventana con una ejecucion EN_PROCESO no se encola otra vez (la reusa
`abrir_ventana`, con su indice unico), y una que ya esta al dia no se toca. Los movimientos no se
duplican: cada ejecucion publica los suyos, y la base no deja publicar dos veces las mismas
entradas.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from sqlalchemy import text
from sqlmodel import Session, select

from motor_cartera.config import Config
from motor_cartera.db.modelos import EjecucionMotorPagos, EstadoMotorPagos
from motor_cartera.db.sesion import sesion
from motor_cartera.motor_pagos.ejecuciones import Ventana, abrir_ventana
from motor_cartera.motor_pagos.reglas import VERSION_MOTOR_PAGOS, siguiente_periodo


@dataclass(frozen=True)
class EstadoDeVentana:
    """Una ventana con pagos observados, comparada con su interpretacion."""

    despacho_id: str
    cartera_id: str
    desde: date
    observaciones: int
    """Los pagos observados de la ventana, hoy."""
    interpretadas: int = 0
    """Los que leyo su interpretacion vigente; 0 si no tiene."""
    vigente: UUID | None = None
    ultimo_resultado: str | None = None
    """El resultado de su ultima ejecucion FALLIDA, si tuvo alguna."""
    por_conciliar: int = 0
    """Movimientos de su vigente SIN_CUENTA_OBSERVADA cuyo cliente ya tiene cuenta canonica."""

    @property
    def hasta(self) -> date:
        return siguiente_periodo(self.desde)

    @property
    def pendientes(self) -> int:
        """Pagos observados que su interpretacion vigente no ve."""
        return self.observaciones - self.interpretadas

    @property
    def ventana(self) -> Ventana:
        return Ventana(self.despacho_id, self.cartera_id, self.desde, self.hasta)


@dataclass(frozen=True)
class Diagnostico:
    ventanas: int
    observaciones: int
    al_dia: int
    en_cola: int
    sin_interpretacion: list[EstadoDeVentana] = field(default_factory=list)
    desactualizadas: list[EstadoDeVentana] = field(default_factory=list)
    solo_fallidas: list[EstadoDeVentana] = field(default_factory=list)
    por_conciliar: list[EstadoDeVentana] = field(default_factory=list)

    @property
    def observaciones_sin_interpretacion(self) -> int:
        """Pagos observados sin una interpretacion vigente que los vea, en las ventanas que no
        estan en la cola."""
        return sum(
            v.pendientes
            for v in (*self.sin_interpretacion, *self.desactualizadas, *self.solo_fallidas)
        )


@dataclass(frozen=True)
class Encolada:
    ventana: EstadoDeVentana
    motor_pagos_run_id: UUID
    nueva: bool
    """False si la ventana ya tenia una ejecucion EN_PROCESO: no se abrio otra."""


def diagnosticar(s: Session) -> Diagnostico:
    """Cada ventana con pagos observados, clasificada por como esta su interpretacion."""
    conteos = s.execute(
        text(
            "SELECT despacho_id, cartera_id, date_trunc('month', fecha_recepcion)::date, count(*) "
            "FROM pago_observado GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"
        )
    ).all()
    por_ventana: dict[tuple, list[EjecucionMotorPagos]] = defaultdict(list)
    for ejecucion in s.exec(
        select(EjecucionMotorPagos)
        .where(EjecucionMotorPagos.version_motor == VERSION_MOTOR_PAGOS)
        .order_by(EjecucionMotorPagos.id)
    ).all():
        llave = (ejecucion.despacho_id, ejecucion.cartera_id, ejecucion.periodo_desde)
        por_ventana[llave].append(ejecucion)
    vigentes = {
        llave: exitosas[-1]
        for llave, todas in por_ventana.items()
        if (exitosas := [e for e in todas if e.estado == EstadoMotorPagos.EXITOSA])
    }
    sin_conciliar = _por_conciliar(s, [v.id for v in vigentes.values()])
    al_dia = en_cola = 0
    listas: dict[str, list[EstadoDeVentana]] = defaultdict(list)
    for despacho, cartera, desde, observaciones in conteos:
        llave = (despacho, cartera, desde)
        todas = por_ventana.get(llave, [])
        vigente = vigentes.get(llave)
        fallidas = [e for e in todas if e.estado == EstadoMotorPagos.FALLIDA]
        estado = EstadoDeVentana(
            despacho,
            cartera,
            desde,
            observaciones,
            interpretadas=vigente.observaciones_leidas if vigente else 0,
            vigente=vigente.motor_pagos_run_id if vigente else None,
            ultimo_resultado=fallidas[-1].resultado if fallidas else None,
            por_conciliar=sin_conciliar.get(vigente.id, 0) if vigente else 0,
        )
        if any(e.estado == EstadoMotorPagos.EN_PROCESO for e in todas):
            en_cola += 1
        elif vigente is not None and estado.pendientes == 0:
            al_dia += 1
            if estado.por_conciliar:
                listas["por_conciliar"].append(estado)
        elif vigente is not None:
            listas["desactualizadas"].append(estado)
        elif fallidas:
            listas["solo_fallidas"].append(estado)
        else:
            listas["sin_interpretacion"].append(estado)
    return Diagnostico(
        ventanas=len(conteos),
        observaciones=sum(fila[3] for fila in conteos),
        al_dia=al_dia,
        en_cola=en_cola,
        **listas,
    )


def _por_conciliar(s: Session, vigentes: list[int]) -> dict[int, int]:
    """Cuantos movimientos SIN_CUENTA_OBSERVADA de cada ejecucion tienen hoy cuenta canonica."""
    if not vigentes:
        return {}
    filas = s.execute(
        text(
            "SELECT m.ejecucion_motor_pagos_id, count(*) FROM movimiento_economico_canonico m "
            "JOIN cuenta_canonica c ON c.despacho_id = m.despacho_id "
            "AND c.cartera_id = m.cartera_id AND c.cliente_unico = m.cliente_unico "
            "WHERE m.ejecucion_motor_pagos_id = ANY(:vigentes) "
            "AND m.estado_conciliacion = 'SIN_CUENTA_OBSERVADA' GROUP BY 1"
        ),
        {"vigentes": vigentes},
    ).all()
    return {ejecucion: cuantos for ejecucion, cuantos in filas}


def encolar(pendientes: list[EstadoDeVentana], *, config: Config) -> list[Encolada]:
    """Abre la interpretacion de cada ventana, con su trabajo MOTOR_PAGOS, en su propia
    transaccion y en orden de ventana. Una que ya tiene una EN_PROCESO la reusa."""
    encoladas = []
    for pendiente in sorted(pendientes, key=lambda p: (p.despacho_id, p.cartera_id, p.desde)):
        with sesion() as s:
            ejecucion, nueva = abrir_ventana(
                s, pendiente.ventana, max_intentos=config.worker_max_intentos
            )
            s.commit()
            encoladas.append(Encolada(pendiente, ejecucion.motor_pagos_run_id, nueva))
    return encoladas
