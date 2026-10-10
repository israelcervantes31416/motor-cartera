"""El backfill de la atribucion: las ventanas cuyos movimientos no tienen una atribucion al dia.

La atribucion no se abre sola: depende de dos cosas que cambian por separado, los movimientos que
interpreta el motor de pagos y las gestiones que registra la cobranza, y una gestion tardia puede
llegar en cualquier momento. `motor-cartera backfill-atribucion` revisa cada ventana (despacho,
cartera y mes de recepcion) que tiene una interpretacion vigente de los pagos, y la compara con su
atribucion vigente (su EXITOSA mas reciente):

- al dia: leyo la interpretacion vigente de los pagos, y las gestiones de sus cuentas en su rango
  son las mismas que leyo (las mismas cuantas, y las mismas anuladas: el lifecycle solo se agrega,
  asi que contar basta);
- en la cola: tiene una EN_PROCESO;
- sin atribucion: nunca se ha atribuido;
- desactualizada: hay otra interpretacion de pagos, o gestiones nuevas o anuladas en su rango;
- solo con FALLIDA: se reintenta con --reintentar-fallidas.

Encola una ejecucion por ventana pendiente, cada una en su propia transaccion; la ejecuta un worker,
por conjuntos. Es idempotente: una ventana en la cola o al dia no se toca, y una ejecucion con las
mismas entradas que su vigente no publica (YA_ATRIBUIDA). Una ventana desactualizada se vuelve a
atribuir con la ventana hacia atras de su vigente; una nueva, con la configurada.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from sqlalchemy import text
from sqlmodel import Session, select

from motor_cartera.atribucion.ejecuciones import abrir, gestiones_del_rango
from motor_cartera.atribucion.reglas import VERSION_ATRIBUCION
from motor_cartera.config import Config
from motor_cartera.db.modelos import (
    EjecucionAtribucion,
    EjecucionMotorPagos,
    EstadoAtribucion,
    EstadoMotorPagos,
)
from motor_cartera.db.sesion import sesion
from motor_cartera.motor_pagos.ejecuciones import Ventana
from motor_cartera.motor_pagos.reglas import VERSION_MOTOR_PAGOS


@dataclass(frozen=True)
class EstadoDeVentana:
    despacho_id: str
    cartera_id: str
    desde: date
    hasta: date
    pagos: int
    """Los PAGO de su interpretacion vigente de los pagos: los que se atribuyen."""
    vigente: UUID | None = None
    ventana_dias: int | None = None
    """La ventana hacia atras de su atribucion vigente, si tiene una."""
    motivo: str | None = None
    """Por que esta desactualizada, o el resultado de su ultima FALLIDA."""

    @property
    def ventana(self) -> Ventana:
        return Ventana(self.despacho_id, self.cartera_id, self.desde, self.hasta)


@dataclass(frozen=True)
class Diagnostico:
    ventanas: int
    pagos: int
    al_dia: int
    en_cola: int
    sin_atribucion: list[EstadoDeVentana] = field(default_factory=list)
    desactualizadas: list[EstadoDeVentana] = field(default_factory=list)
    solo_fallidas: list[EstadoDeVentana] = field(default_factory=list)

    @property
    def pagos_sin_atribucion_al_dia(self) -> int:
        """Los pagos de las ventanas que no estan al dia ni en la cola."""
        return sum(
            v.pagos for v in (*self.sin_atribucion, *self.desactualizadas, *self.solo_fallidas)
        )


@dataclass(frozen=True)
class Encolada:
    ventana: EstadoDeVentana
    atribucion_run_id: UUID
    ventana_dias: int
    nueva: bool


def diagnosticar(s: Session) -> Diagnostico:
    """Cada ventana con una interpretacion vigente de los pagos, comparada con su atribucion."""
    motores = s.exec(
        select(EjecucionMotorPagos)
        .where(
            EjecucionMotorPagos.version_motor == VERSION_MOTOR_PAGOS,
            EjecucionMotorPagos.estado == EstadoMotorPagos.EXITOSA,
        )
        .order_by(EjecucionMotorPagos.id)
    ).all()
    vigente_del_motor: dict[tuple, EjecucionMotorPagos] = {}
    for motor in motores:
        vigente_del_motor[(motor.despacho_id, motor.cartera_id, motor.periodo_desde)] = motor
    atribuciones: dict[tuple, list[EjecucionAtribucion]] = defaultdict(list)
    for ejecucion in s.exec(
        select(EjecucionAtribucion)
        .where(EjecucionAtribucion.version_atribucion == VERSION_ATRIBUCION)
        .order_by(EjecucionAtribucion.id)
    ).all():
        atribuciones[(ejecucion.despacho_id, ejecucion.cartera_id, ejecucion.periodo_desde)].append(
            ejecucion
        )
    al_dia = en_cola = pagos_totales = 0
    listas: dict[str, list[EstadoDeVentana]] = defaultdict(list)
    for llave, motor in sorted(vigente_del_motor.items()):
        pagos = pagos_de(s, motor.id)
        pagos_totales += pagos
        todas = atribuciones.get(llave, [])
        exitosas = [e for e in todas if e.estado == EstadoAtribucion.EXITOSA]
        base = {
            "despacho_id": motor.despacho_id,
            "cartera_id": motor.cartera_id,
            "desde": motor.periodo_desde,
            "hasta": motor.periodo_hasta,
            "pagos": pagos,
        }
        if any(e.estado == EstadoAtribucion.EN_PROCESO for e in todas):
            en_cola += 1
            continue
        if not exitosas:
            fallidas = [e for e in todas if e.estado == EstadoAtribucion.FALLIDA]
            if fallidas:
                listas["solo_fallidas"].append(
                    EstadoDeVentana(**base, motivo=fallidas[-1].resultado)
                )
            else:
                listas["sin_atribucion"].append(EstadoDeVentana(**base))
            continue
        vigente = exitosas[-1]
        motivo = _desactualizada(s, vigente, motor)
        estado = EstadoDeVentana(
            **base,
            vigente=vigente.atribucion_run_id,
            ventana_dias=vigente.ventana_dias,
            motivo=motivo,
        )
        if motivo is None:
            al_dia += 1
        else:
            listas["desactualizadas"].append(estado)
    return Diagnostico(
        ventanas=len(vigente_del_motor),
        pagos=pagos_totales,
        al_dia=al_dia,
        en_cola=en_cola,
        **listas,
    )


def pagos_de(s: Session, motor_id: int) -> int:
    return s.execute(
        text(
            "SELECT count(*) FROM movimiento_economico_canonico "
            "WHERE ejecucion_motor_pagos_id = :motor AND tipo_movimiento = 'PAGO'"
        ),
        {"motor": motor_id},
    ).scalar_one()


def gestiones_en_el_rango(
    s: Session, motor_id: int, *, ventana_dias: int, zona: str
) -> tuple[int, int]:
    """Cuantas gestiones leeria hoy una atribucion de la interpretacion `motor_id` con esa ventana,
    y cuantas de ellas estan anuladas: exactamente el conjunto que entra en su firma de entrada."""
    return s.execute(
        text(
            "SELECT count(*), count(*) FILTER (WHERE anulada) FROM ("
            + gestiones_del_rango(
                "SELECT m.cuenta_canonica_id AS cuenta, "
                "min(timezone(:zona, m.fecha_recepcion)) AS primero, "
                "max(timezone(:zona, m.fecha_recepcion)) AS ultimo "
                "FROM movimiento_economico_canonico m WHERE m.ejecucion_motor_pagos_id = :motor "
                "AND m.tipo_movimiento = 'PAGO' AND m.cuenta_canonica_id IS NOT NULL GROUP BY 1"
            )
            + ") leidas"
        ),
        {"motor": motor_id, "zona": zona, "ventana": ventana_dias},
    ).one()


def _desactualizada(
    s: Session, vigente: EjecucionAtribucion, motor: EjecucionMotorPagos
) -> str | None:
    """Por que la atribucion vigente ya no corresponde a sus entradas; None si corresponde. Cuenta
    con la ventana hacia atras y la zona de la vigente."""
    if vigente.ejecucion_motor_pagos_id != motor.id:
        return f"otra interpretacion de pagos ({motor.motor_pagos_run_id})"
    gestiones, anuladas = gestiones_en_el_rango(
        s, motor.id, ventana_dias=vigente.ventana_dias, zona=vigente.zona_horaria
    )
    if (gestiones, anuladas) != (vigente.gestiones_leidas, vigente.gestiones_anuladas):
        return (
            f"{gestiones:,} gestiones en su rango ({anuladas:,} anuladas); su atribucion vigente "
            f"leyo {vigente.gestiones_leidas:,} ({vigente.gestiones_anuladas:,} anuladas)"
        )
    return None


def encolar(
    pendientes: list[EstadoDeVentana], *, config: Config, ventana_dias: int | None = None
) -> list[Encolada]:
    """Abre la atribucion de cada ventana, con su trabajo ATRIBUCION, en su propia transaccion y en
    orden de ventana. Una que ya tiene una EN_PROCESO la reusa. La ventana hacia atras es la de su
    atribucion vigente, si tiene una; si no, `ventana_dias` o la configurada."""
    encoladas = []
    for pendiente in sorted(pendientes, key=lambda p: (p.despacho_id, p.cartera_id, p.desde)):
        dias = pendiente.ventana_dias or ventana_dias or config.atribucion_ventana_dias
        with sesion() as s:
            ejecucion, nueva = abrir(
                s,
                pendiente.ventana,
                ventana_dias=dias,
                zona=config.zona_horaria_fuente,
                max_intentos=config.worker_max_intentos,
                reusar=True,
            )
            s.commit()
            encoladas.append(
                Encolada(pendiente, ejecucion.atribucion_run_id, ejecucion.ventana_dias, nueva)
            )
    return encoladas
