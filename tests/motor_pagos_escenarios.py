"""Lo que comparten las pruebas del motor de pagos: pagos y cortes sinteticos ingeridos como
cualquier fuente oficial, y como leer lo que el motor publico."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from historia_escenarios import historia_de
from sqlalchemy import func
from sqlmodel import select

from motor_cartera.db.modelos import (
    DatasetConformado,
    EjecucionHistoria,
    EjecucionMotorPagos,
    EstadoHistoria,
    EstadoMotorPagos,
    MovimientoEconomicoCanonico,
    PagoObservado,
    ResultadoPagoObservado,
)
from motor_cartera.db.sesion import sesion
from motor_cartera.historia.ejecuciones import materializar
from motor_cartera.motor_pagos.ejecuciones import interpretar

DESPACHO, CARTERA = "DSP_001", "CARTERA_PRINCIPAL"


def historiar_todo() -> None:
    """Materializa, en orden de id, cada historia que siga EN_PROCESO."""
    with sesion() as s:
        pendientes = s.exec(
            select(EjecucionHistoria.id)
            .where(EjecucionHistoria.estado == EstadoHistoria.EN_PROCESO)
            .order_by(EjecucionHistoria.id)
        ).all()
    for ejecucion_id in pendientes:
        materializar(ejecucion_id)


def interpretar_todo() -> list[EjecucionMotorPagos]:
    """Interpreta, en orden de id, cada ventana que siga EN_PROCESO, y las devuelve terminadas."""
    with sesion() as s:
        pendientes = s.exec(
            select(EjecucionMotorPagos.id)
            .where(EjecucionMotorPagos.estado == EstadoMotorPagos.EN_PROCESO)
            .order_by(EjecucionMotorPagos.id)
        ).all()
    for ejecucion_id in pendientes:
        interpretar(ejecucion_id)
    with sesion() as s:
        return list(
            s.exec(
                select(EjecucionMotorPagos)
                .where(EjecucionMotorPagos.id.in_(pendientes))
                .order_by(EjecucionMotorPagos.id)
            ).all()
        )


def publicar(ingesta) -> list[EjecucionMotorPagos]:
    """La historia de una ingesta de pagos y la interpretacion de lo que abrio."""
    materializar(historia_de(ingesta=ingesta).id)
    return interpretar_todo()


def ejecuciones(periodo: date | None = None) -> list[EjecucionMotorPagos]:
    with sesion() as s:
        consulta = select(EjecucionMotorPagos).order_by(EjecucionMotorPagos.id)
        if periodo is not None:
            consulta = consulta.where(EjecucionMotorPagos.periodo_desde == periodo)
        return list(s.exec(consulta).all())


def vigente(periodo: date) -> EjecucionMotorPagos:
    """La EXITOSA mas reciente de la ventana."""
    exitosas = [e for e in ejecuciones(periodo) if e.estado == EstadoMotorPagos.EXITOSA]
    return exitosas[-1]


@dataclass(frozen=True)
class Visto:
    """Un resultado con lo que lo ata a su observacion y a su movimiento."""

    resultado: ResultadoPagoObservado
    pago: PagoObservado
    movimiento: MovimientoEconomicoCanonico | None

    @property
    def clasificacion(self) -> str:
        return self.resultado.clasificacion

    @property
    def codigos(self) -> list[str]:
        return [m["codigo"] for m in self.resultado.motivos]


def resultados(ejecucion: EjecucionMotorPagos) -> list[Visto]:
    """Los resultados de una ejecucion, en orden de recepcion y de fila."""
    with sesion() as s:
        filas = s.exec(
            select(ResultadoPagoObservado, PagoObservado, MovimientoEconomicoCanonico)
            .join(
                PagoObservado,
                (
                    PagoObservado.dataset_conformado_id
                    == ResultadoPagoObservado.dataset_conformado_id
                )
                & (PagoObservado.source_row == ResultadoPagoObservado.source_row),
            )
            .outerjoin(
                MovimientoEconomicoCanonico,
                MovimientoEconomicoCanonico.id
                == ResultadoPagoObservado.movimiento_economico_canonico_id,
            )
            .where(ResultadoPagoObservado.ejecucion_motor_pagos_id == ejecucion.id)
            .order_by(
                PagoObservado.fecha_recepcion,
                PagoObservado.dataset_conformado_id,
                PagoObservado.source_row,
            )
        ).all()
    return [Visto(*fila) for fila in filas]


def movimientos(ejecucion: EjecucionMotorPagos) -> list[MovimientoEconomicoCanonico]:
    with sesion() as s:
        return list(
            s.exec(
                select(MovimientoEconomicoCanonico)
                .where(MovimientoEconomicoCanonico.ejecucion_motor_pagos_id == ejecucion.id)
                .order_by(
                    MovimientoEconomicoCanonico.fecha_recepcion,
                    MovimientoEconomicoCanonico.movimiento_id,
                )
            ).all()
        )


def del_cliente(vistos: list[Visto], cliente_unico: str) -> list[Visto]:
    return [v for v in vistos if v.pago.cliente_unico == cliente_unico]


def cuantos(modelo, *condiciones) -> int:
    with sesion() as s:
        return s.exec(select(func.count()).select_from(modelo).where(*condiciones)).one()


def dataset_de(ingesta) -> int:
    with sesion() as s:
        return s.exec(
            select(DatasetConformado.id).where(DatasetConformado.ingesta_pagos_id == ingesta.id)
        ).one()


def pesos(texto: str) -> Decimal:
    return Decimal(texto)


def foto_motor() -> dict:
    """La interpretacion vigente entera, como se ve desde fuera y sin nada que dependa de cuando,
    en que orden o en que base se calculo: cada observacion se nombra por el SHA-256 de su archivo
    original y su fila, y cada cuenta por su cuenta_id determinista. Lo demas, incluidos los
    movimiento_id y la firma de entrada, tiene que ser identico si se reconstruye."""
    import json

    from motor_cartera.db.modelos import ArtefactoFuente, CuentaCanonica
    from motor_cartera.motor_pagos import consultas
    from motor_cartera.motor_pagos.reglas import VERSION_MOTOR_PAGOS

    with sesion() as s:
        vigentes = consultas.vigentes(s, VERSION_MOTOR_PAGOS)
        archivo = dict(
            s.exec(
                select(DatasetConformado.id, ArtefactoFuente.sha256).join(
                    ArtefactoFuente, ArtefactoFuente.id == DatasetConformado.artefacto_original_id
                )
            ).all()
        )
        por_id = {
            p.pago_observado_id: (archivo[p.dataset_conformado_id], p.source_row)
            for p in s.exec(select(PagoObservado)).all()
        }
        cuentas = {c.id: c.cuenta_id for c in s.exec(select(CuentaCanonica)).all()}
        ejecuciones_ = s.exec(
            select(EjecucionMotorPagos).where(EjecucionMotorPagos.id.in_(vigentes))
        ).all()
        filas = s.exec(
            select(ResultadoPagoObservado, MovimientoEconomicoCanonico.movimiento_id)
            .outerjoin(
                MovimientoEconomicoCanonico,
                MovimientoEconomicoCanonico.id
                == ResultadoPagoObservado.movimiento_economico_canonico_id,
            )
            .where(ResultadoPagoObservado.ejecucion_motor_pagos_id.in_(vigentes))
        ).all()
        publicados = s.exec(
            select(MovimientoEconomicoCanonico).where(
                MovimientoEconomicoCanonico.ejecucion_motor_pagos_id.in_(vigentes)
            )
        ).all()

    def motivos(lista: list[dict]) -> str:
        normalizados = [
            {k: (por_id[UUID(v)] if k == "representante" else v) for k, v in m.items()}
            for m in lista
        ]
        return json.dumps(normalizados, sort_keys=True, default=str)

    ocultas = {"id", "motor_pagos_run_id", "iniciada_en", "terminada_en", "detalle"}
    return {
        "ejecuciones": sorted(
            tuple((k, v) for k, v in sorted(e.model_dump().items()) if k not in ocultas)
            for e in ejecuciones_
        ),
        "resultados": sorted(
            (
                (archivo[r.dataset_conformado_id], r.source_row),
                r.clasificacion,
                r.estado_conciliacion,
                movimiento_id,
                r.movimiento_relacionado_id,
                r.firma_exacta,
                r.firma_legacy,
                motivos(r.motivos),
            )
            for r, movimiento_id in filas
        ),
        "movimientos": sorted(
            (
                m.movimiento_id,
                m.version_motor,
                m.despacho_id,
                m.cartera_id,
                m.cliente_unico,
                m.fecha_recepcion,
                m.monto_reportado,
                m.signo_economico,
                m.tipo_movimiento,
                m.estado_conciliacion,
                cuentas.get(m.cuenta_canonica_id),
                m.movimiento_original_id,
                m.anulado_por_movimiento_id,
                m.observaciones,
                m.firma_exacta,
            )
            for m in publicados
        ),
    }
