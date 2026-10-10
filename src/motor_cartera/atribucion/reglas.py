"""atribucion/v1 como nucleo puro: con que gestiones se asocia cada movimiento economico, y por que.

Es atribucion operacional, no causalidad. Dice que gestiones de la misma cuenta, con contacto,
ocurrieron antes de un movimiento dentro de una ventana: que el movimiento se observo despues de
ellas. No dice que una gestion haya producido el pago, ni cuanto aporto, y por eso nunca elige.

La politica de v1, conservadora:

- se evaluan los movimientos PAGO de la interpretacion vigente del motor de pagos. Un REVERSO o un
  POSIBLE_REVERSO corrige una recuperacion: no es el resultado de una gestion;
- una gestion es candidata de un movimiento solo si es de la misma cuenta canonica con que el motor
  de pagos concilio el movimiento, no esta anulada, hubo contacto (CONTACTO_TITULAR o
  CONTACTO_TERCERO) y ocurrio antes del movimiento o en su mismo instante, a lo mas `ventana_dias`
  antes. Un intento sin contacto, o un mensaje de una via, no tiene evidencia de haber llegado a
  nadie, y no es candidato;
- sin candidatas, SIN_GESTION_CANDIDATA; con una, ASOCIACION_UNICA con esa gestion; con dos o mas,
  AMBIGUA, con todas sus candidatas y sin elegir ninguna: ni la ultima, ni la primera, ni la mas
  cercana;
- un movimiento que el motor de pagos no concilio con una cuenta (SIN_CUENTA_OBSERVADA) no tiene
  candidatas: las gestiones son de una cuenta;
- un PAGO que un reverso anulo se clasifica igual, porque las gestiones si lo antecedieron, y queda
  marcado `anulado_por_reverso`: su monto no cuenta como recuperacion asociada.

La ventana es un parametro de cada ejecucion, no una verdad de negocio. La ejecucion lo hace en
PostgreSQL, por conjuntos (`atribucion.ejecuciones`), y una prueba compara las dos.

Este modulo no sabe de la base.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from motor_cartera.lifecycle.reglas import CONTACTOS, NivelContacto

VERSION_ATRIBUCION = "atribucion/v1"
"""Cambia si cambia cualquier regla de este modulo."""


class Clasificacion(StrEnum):
    SIN_GESTION_CANDIDATA = "SIN_GESTION_CANDIDATA"
    ASOCIACION_UNICA = "ASOCIACION_UNICA"
    AMBIGUA = "AMBIGUA"


class Motivo(StrEnum):
    """Por que un movimiento quedo como quedo. Uno, y ANULADO_POR_REVERSO si ademas lo anulo un
    reverso."""

    MOVIMIENTO_SIN_CUENTA = "MOVIMIENTO_SIN_CUENTA"
    """El motor de pagos no lo concilio con una cuenta canonica: no hay gestiones que comparar."""
    SIN_GESTION_EN_LA_VENTANA = "SIN_GESTION_EN_LA_VENTANA"
    """Ninguna gestion candidata en la ventana. Dice cuantas hubo sin contacto y cuantas
    anuladas."""
    UNA_GESTION_CANDIDATA = "UNA_GESTION_CANDIDATA"
    VARIAS_GESTIONES_CANDIDATAS = "VARIAS_GESTIONES_CANDIDATAS"
    """Dos o mas candidatas igual de elegibles: v1 no elige."""
    ANULADO_POR_REVERSO = "ANULADO_POR_REVERSO"


@dataclass(frozen=True)
class MovimientoAtribuible:
    """Un PAGO de la interpretacion vigente, con su recepcion ya como instante (la hora local de la
    fuente, leida en su zona)."""

    movimiento_id: UUID
    cuenta: int | None
    instante: datetime
    monto: Decimal
    anulado_por: UUID | None = None


@dataclass(frozen=True)
class GestionLeida:
    gestion_id: UUID
    cuenta: int
    ocurrido_en: datetime
    nivel_contacto: NivelContacto
    anulada: bool = False


@dataclass(frozen=True)
class Candidata:
    gestion_id: UUID
    antelacion_segundos: int


@dataclass(frozen=True)
class Atribucion:
    movimiento_id: UUID
    clasificacion: Clasificacion
    candidatas: tuple[Candidata, ...]
    """En orden de gestion_id: un orden que no significa nada, a proposito."""
    motivos: tuple[dict, ...]

    @property
    def gestion_id(self) -> UUID | None:
        """La gestion asociada, solo en una ASOCIACION_UNICA."""
        if self.clasificacion != Clasificacion.ASOCIACION_UNICA:
            return None
        return self.candidatas[0].gestion_id


def en_la_ventana(
    gestion: GestionLeida, movimiento: MovimientoAtribuible, ventana_dias: int
) -> bool:
    """Si la gestion ocurrio antes del movimiento, o en su instante, a lo mas `ventana_dias`
    antes."""
    return (
        movimiento.instante - timedelta(days=ventana_dias)
        <= gestion.ocurrido_en
        <= movimiento.instante
    )


def elegible(gestion: GestionLeida) -> bool:
    """Si una gestion en la ventana puede ser candidata: vigente y con contacto."""
    return not gestion.anulada and gestion.nivel_contacto in CONTACTOS


def atribuir(
    movimientos: Iterable[MovimientoAtribuible],
    gestiones: Iterable[GestionLeida],
    ventana_dias: int,
) -> list[Atribucion]:
    """Lo que atribucion/v1 concluye de cada movimiento, en el orden en que llegan."""
    por_cuenta: dict[int, list[GestionLeida]] = defaultdict(list)
    for gestion in gestiones:
        por_cuenta[gestion.cuenta].append(gestion)
    resultado = []
    for movimiento in movimientos:
        resultado.append(_atribuir_uno(movimiento, por_cuenta, ventana_dias))
    return resultado


def _atribuir_uno(
    movimiento: MovimientoAtribuible,
    por_cuenta: dict[int, list[GestionLeida]],
    ventana_dias: int,
) -> Atribucion:
    anulado = (
        ()
        if movimiento.anulado_por is None
        else ({"codigo": Motivo.ANULADO_POR_REVERSO.value, "reverso": str(movimiento.anulado_por)},)
    )
    if movimiento.cuenta is None:
        return Atribucion(
            movimiento.movimiento_id,
            Clasificacion.SIN_GESTION_CANDIDATA,
            (),
            ({"codigo": Motivo.MOVIMIENTO_SIN_CUENTA.value}, *anulado),
        )
    en_ventana = [
        g
        for g in por_cuenta.get(movimiento.cuenta, ())
        if en_la_ventana(g, movimiento, ventana_dias)
    ]
    candidatas = tuple(
        sorted(
            (
                Candidata(g.gestion_id, int((movimiento.instante - g.ocurrido_en).total_seconds()))
                for g in en_ventana
                if elegible(g)
            ),
            key=lambda c: str(c.gestion_id),
        )
    )
    if not candidatas:
        motivo = {
            "codigo": Motivo.SIN_GESTION_EN_LA_VENTANA.value,
            "ventana_dias": ventana_dias,
            "gestiones_sin_contacto": sum(
                1 for g in en_ventana if not g.anulada and g.nivel_contacto not in CONTACTOS
            ),
            "gestiones_anuladas": sum(1 for g in en_ventana if g.anulada),
        }
        clase = Clasificacion.SIN_GESTION_CANDIDATA
    elif len(candidatas) == 1:
        motivo = {"codigo": Motivo.UNA_GESTION_CANDIDATA.value, "ventana_dias": ventana_dias}
        clase = Clasificacion.ASOCIACION_UNICA
    else:
        motivo = {
            "codigo": Motivo.VARIAS_GESTIONES_CANDIDATAS.value,
            "candidatas": len(candidatas),
            "ventana_dias": ventana_dias,
        }
        clase = Clasificacion.AMBIGUA
    return Atribucion(movimiento.movimiento_id, clase, candidatas, (motivo, *anulado))
