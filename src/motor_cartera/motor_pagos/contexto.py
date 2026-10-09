"""El contexto temporal de un movimiento conciliado: donde cae entre los snapshots de su cuenta.

Se calcula al consultar, como la presencia de v0.7, a partir de dos cosas que la base ya tiene: los
cortes canonicos de la cartera y los cortes en que la cuenta tiene snapshot. No se guarda: un corte
que llega despues cambia el contexto de un pago exactamente como si hubiera llegado a tiempo, sin
reinterpretar nada.

Lo que dice es informacion, no un juicio: un pago antes de la primera observacion de su cuenta, o
mientras la cuenta no aparecia en la cartera, no es un error. Con el snapshot anterior y el
siguiente se puede mirar saldo antes, pago y saldo despues, sin afirmar que toda la diferencia de
saldo la causo el pago: ninguna fuente trae intereses, cargos, condonaciones ni ajustes.

La fecha de un corte no tiene hora. Un pago se compara con el corte de su mismo dia como "anterior
o igual": v1 no supone a que hora se toma el corte.

Este modulo es puro: no sabe de la base, y las pruebas lo ejercitan sin ella.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class ContextoTemporal:
    snapshot_anterior: date | None
    """El ultimo corte en que se observo la cuenta, del dia del pago o de antes."""
    snapshot_siguiente: date | None
    """El primer corte, despues del dia del pago, en que se observo la cuenta."""
    antes_de_primera_observacion: bool
    """El pago es de antes del primer corte en que aparece la cuenta."""
    despues_de_ultima_observacion: bool
    """El pago es de despues del ultimo corte en que aparece la cuenta, siga o no en la cartera."""
    durante_ausencia_observada: bool
    """La cuenta ya se habia observado, y el corte mas reciente de su cartera al dia del pago no la
    traia: el pago llego mientras faltaba, o despues de su salida observada."""


def contexto(
    cortes: Sequence[date], presentes: Sequence[date], recepcion: datetime
) -> ContextoTemporal:
    """El contexto de un pago recibido en `recepcion`. `cortes` son los de la cartera y `presentes`
    los de la cuenta, los dos en orden de fecha; `presentes` es parte de `cortes`."""
    dia = recepcion.date()
    hasta_el_dia = bisect_right(presentes, dia)
    anterior = presentes[hasta_el_dia - 1] if hasta_el_dia else None
    siguiente = presentes[hasta_el_dia] if hasta_el_dia < len(presentes) else None
    cortes_hasta_el_dia = bisect_right(cortes, dia)
    ultimo_corte = cortes[cortes_hasta_el_dia - 1] if cortes_hasta_el_dia else None
    ausente = (
        anterior is not None and ultimo_corte is not None and not _contiene(presentes, ultimo_corte)
    )
    return ContextoTemporal(
        snapshot_anterior=anterior,
        snapshot_siguiente=siguiente,
        antes_de_primera_observacion=bool(presentes) and dia < presentes[0],
        despues_de_ultima_observacion=bool(presentes) and dia > presentes[-1],
        durante_ausencia_observada=ausente,
    )


def _contiene(ordenadas: Sequence[date], fecha: date) -> bool:
    lugar = bisect_left(ordenadas, fecha)
    return lugar < len(ordenadas) and ordenadas[lugar] == fecha
