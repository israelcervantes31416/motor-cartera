"""evaluacion-promesa/v1 como nucleo puro: que se observa de una promesa a una fecha de corte.

Si una promesa se cumplio no lo decide nadie con un UPDATE: lo concluye esta evaluacion, a una fecha
de corte explicita (`as_of`), sobre los movimientos economicos interpretados de su cuenta. as_of
nunca sale del reloj: los mismos datos, el mismo as_of y la misma version dan el mismo resultado.

Observar recuperacion despues de una promesa no demuestra que la promesa la haya producido. La
evaluacion dice que se observo recuperacion compatible con la promesa: PAGO de su cuenta (con la
conciliacion del motor de pagos), recibidos desde que se acordo hasta el final de su fecha limite
(o de as_of, si es antes), que ningun reverso recibido hasta as_of anulo. Un mismo movimiento puede
ser compatible con dos promesas: la evaluacion no reparte dinero entre ellas. Un POSIBLE_REVERSO no
se resta, porque no se sabe que pago revierte, y se informa en sus motivos.

Los estados, en este orden:

- CANCELADA: su gestion se anulo (se registro por error), o un PROMESA_CANCELADA ocurrido hasta el
  final de as_of la dejo sin efecto;
- CUMPLIDA: la recuperacion compatible alcanza el monto prometido;
- PENDIENTE: as_of es anterior a su fecha limite, y todavia no se cumple;
- NO_EVALUABLE: vencida, pero los datos no permiten afirmar que no se pago: hay pagos observados en
  su intervalo que su interpretacion vigente no ve, o los pagos de la cartera no llegan mas alla
  del final de su intervalo. Se prefiere no evaluar a afirmar un incumplimiento sin datos;
- PARCIAL: vencida, con recuperacion compatible menor que lo prometido;
- INCUMPLIDA: vencida, sin recuperacion compatible.

Los dias de calendario (la fecha limite, as_of) son de la zona horaria de la fuente; quien llama
los convierte a instantes. Este modulo no sabe de la base.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

VERSION_EVALUACION = "evaluacion-promesa/v1"
"""Cambia si cambia cualquier regla de este modulo."""


class EstadoEvaluacion(StrEnum):
    PENDIENTE = "PENDIENTE"
    CUMPLIDA = "CUMPLIDA"
    PARCIAL = "PARCIAL"
    INCUMPLIDA = "INCUMPLIDA"
    CANCELADA = "CANCELADA"
    NO_EVALUABLE = "NO_EVALUABLE"


class Motivo(StrEnum):
    GESTION_ANULADA = "GESTION_ANULADA"
    PROMESA_CANCELADA = "PROMESA_CANCELADA"
    MONTO_ALCANZADO = "MONTO_ALCANZADO"
    ANTES_DE_LA_FECHA_LIMITE = "ANTES_DE_LA_FECHA_LIMITE"
    PAGOS_SIN_INTERPRETAR = "PAGOS_SIN_INTERPRETAR"
    DATOS_DE_PAGOS_INSUFICIENTES = "DATOS_DE_PAGOS_INSUFICIENTES"
    MONTO_PARCIAL = "MONTO_PARCIAL"
    SIN_RECUPERACION = "SIN_RECUPERACION"
    POSIBLES_REVERSOS = "POSIBLES_REVERSOS"
    """Informativo: hay posibles reversos de la cuenta en su intervalo, y no se restaron."""


@dataclass(frozen=True)
class PromesaEvaluable:
    promesa_id: UUID
    monto_prometido: Decimal
    creada_en: datetime
    """Cuando se acordo: el ocurrido_en de su evento."""
    fin_limite: datetime
    """El final de su fecha limite, como instante: el inicio del dia siguiente en la zona de la
    fuente."""
    anulada: bool = False
    cancelada_en: datetime | None = None


@dataclass(frozen=True)
class Movimiento:
    """Un movimiento de la interpretacion vigente de la cuenta de la promesa."""

    movimiento_id: UUID
    tipo: str
    """PAGO o POSIBLE_REVERSO; un REVERSO anula su pago, y se ve en `anulado_en`."""
    instante: datetime
    monto: Decimal
    anulado_en: datetime | None = None
    """La recepcion del reverso que anulo este PAGO, si hay uno."""


@dataclass(frozen=True)
class Corte:
    """Lo que la evaluacion sabe de su fecha de corte y de los datos."""

    fin_as_of: datetime
    """El final del dia as_of, como instante."""
    horizonte: datetime | None
    """El pago observado mas reciente de la cartera, como instante; None si no hay ninguno."""


@dataclass(frozen=True)
class Evaluacion:
    estado: EstadoEvaluacion
    monto_observado: Decimal
    movimientos_compatibles: int
    primer_movimiento_en: datetime | None
    ultimo_movimiento_en: datetime | None
    motivos: tuple[dict, ...]


def fin_del_intervalo(promesa: PromesaEvaluable, corte: Corte) -> datetime:
    """Hasta cuando se busca recuperacion compatible: el final de su fecha limite, o el de as_of si
    es antes."""
    return min(promesa.fin_limite, corte.fin_as_of)


def evaluar(
    promesa: PromesaEvaluable,
    movimientos: Iterable[Movimiento],
    corte: Corte,
    *,
    pagos_sin_interpretar: bool = False,
) -> Evaluacion:
    """Lo que evaluacion-promesa/v1 concluye de una promesa a su fecha de corte.
    `pagos_sin_interpretar` dice si hay pagos observados en su intervalo que su interpretacion
    vigente no ve."""
    fin = fin_del_intervalo(promesa, corte)
    en_el_intervalo = [m for m in movimientos if promesa.creada_en <= m.instante < fin]
    compatibles = sorted(
        (
            m
            for m in en_el_intervalo
            if m.tipo == "PAGO" and (m.anulado_en is None or m.anulado_en >= corte.fin_as_of)
        ),
        key=lambda m: m.instante,
    )
    posibles = [m for m in en_el_intervalo if m.tipo == "POSIBLE_REVERSO"]
    observado = sum((m.monto for m in compatibles), Decimal("0.00"))

    def concluir(estado: EstadoEvaluacion, *motivos: dict) -> Evaluacion:
        informativos = (
            ()
            if not posibles or estado == EstadoEvaluacion.CANCELADA
            else (
                {
                    "codigo": Motivo.POSIBLES_REVERSOS.value,
                    "cuantos": len(posibles),
                    "monto": f"{sum(m.monto for m in posibles):.2f}",
                },
            )
        )
        return Evaluacion(
            estado=estado,
            monto_observado=observado.quantize(Decimal("0.01")),
            movimientos_compatibles=len(compatibles),
            primer_movimiento_en=compatibles[0].instante if compatibles else None,
            ultimo_movimiento_en=compatibles[-1].instante if compatibles else None,
            motivos=(*motivos, *informativos),
        )

    prometido = promesa.monto_prometido
    montos = {"observado": f"{observado:.2f}", "prometido": f"{prometido:.2f}"}
    if promesa.anulada:
        return concluir(EstadoEvaluacion.CANCELADA, {"codigo": Motivo.GESTION_ANULADA.value})
    if promesa.cancelada_en is not None and promesa.cancelada_en < corte.fin_as_of:
        return concluir(
            EstadoEvaluacion.CANCELADA,
            {
                "codigo": Motivo.PROMESA_CANCELADA.value,
                "cancelada_en": promesa.cancelada_en.isoformat(),
            },
        )
    if observado >= prometido:
        return concluir(
            EstadoEvaluacion.CUMPLIDA, {"codigo": Motivo.MONTO_ALCANZADO.value, **montos}
        )
    if corte.fin_as_of < promesa.fin_limite:
        return concluir(
            EstadoEvaluacion.PENDIENTE,
            {"codigo": Motivo.ANTES_DE_LA_FECHA_LIMITE.value, **montos},
        )
    if pagos_sin_interpretar:
        return concluir(
            EstadoEvaluacion.NO_EVALUABLE, {"codigo": Motivo.PAGOS_SIN_INTERPRETAR.value}
        )
    if corte.horizonte is None or corte.horizonte < fin:
        return concluir(
            EstadoEvaluacion.NO_EVALUABLE,
            {
                "codigo": Motivo.DATOS_DE_PAGOS_INSUFICIENTES.value,
                "horizonte": None if corte.horizonte is None else corte.horizonte.isoformat(),
            },
        )
    if observado > 0:
        return concluir(EstadoEvaluacion.PARCIAL, {"codigo": Motivo.MONTO_PARCIAL.value, **montos})
    return concluir(EstadoEvaluacion.INCUMPLIDA, {"codigo": Motivo.SIN_RECUPERACION.value})
