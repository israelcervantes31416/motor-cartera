"""La presencia de una cuenta en los cortes de su cartera: eventos, continuidad y estado.

Todo se calcula al consultar, a partir de dos cosas que la base ya tiene: los cortes canonicos de
la cartera, en orden de fecha, y los cortes en que la cuenta tiene snapshot. No se guarda ningun
evento: estan implicitos en los snapshots, y guardarlos haria que la historia dependiera del orden
en que llegaron los cortes. Asi, procesar un corte atrasado (un backfill) cambia la respuesta
exactamente como si hubiera llegado a tiempo.

El vocabulario dice lo que se observo, no lo que paso:

- PRIMERA_OBSERVACION: el primer corte en que aparece la cuenta. No es su originacion, ni un alta:
  la cartera pudo tenerla desde antes de que el sistema viera un corte.
- SALIDA_OBSERVADA: aparecia en un corte y no aparece en el siguiente corte de su cartera. No dice
  si se liquido, se cancelo, se castigo o se vendio: solo que dejo de observarse.
- REINGRESO_OBSERVADO: despues de estar ausente al menos un corte, vuelve a aparecer.

Dos snapshots de una cuenta son continuos solo si sus cortes son consecutivos en su cartera. Si la
cuenta falto en medio, la diferencia entre ellos se puede mostrar, pero no es la evolucion de un
periodo continuo.

Este modulo es puro: no sabe de la base, y las pruebas lo ejercitan sin ella.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from uuid import UUID


class Presencia(StrEnum):
    """Si la cuenta esta en el ultimo corte de su cartera. No es un estado crediticio: que no este
    no demuestra que se haya liquidado, cancelado o castigado."""

    EN_CARTERA = "EN_CARTERA"
    NO_OBSERVADA_EN_ULTIMO_CORTE = "NO_OBSERVADA_EN_ULTIMO_CORTE"


class TipoEvento(StrEnum):
    PRIMERA_OBSERVACION = "PRIMERA_OBSERVACION"
    SALIDA_OBSERVADA = "SALIDA_OBSERVADA"
    REINGRESO_OBSERVADO = "REINGRESO_OBSERVADO"


@dataclass(frozen=True)
class Corte:
    """Un corte canonico de la cartera, como lo ve la linea de tiempo."""

    fecha_corte: date
    corte_id: UUID


@dataclass(frozen=True)
class Evento:
    """Un cambio de presencia, en el corte en que se observo."""

    tipo: TipoEvento
    fecha_corte: date
    corte_id: UUID
    ultima_observacion: date | None
    """En una salida o un reingreso, el ultimo corte en que se habia observado la cuenta."""
    cortes_ausente: int
    """En un reingreso, cuantos cortes de la cartera falto; en los demas, 0."""


@dataclass(frozen=True)
class Enlace:
    """Como se une un snapshot con el anterior de la misma cuenta."""

    continuo_desde_anterior: bool | None
    """True si el corte anterior de la cartera es el del snapshot anterior. None en la primera
    observacion, que no tiene anterior."""
    cortes_ausentes_desde_anterior: int | None
    """Cuantos cortes de la cartera hay entre los dos, sin la cuenta. None en la primera."""


@dataclass(frozen=True)
class ResumenPresencia:
    """La presencia de una cuenta en los cortes de su cartera, hasta el ultimo considerado."""

    primera_observacion: date | None
    ultima_observacion: date | None
    ultimo_corte: Corte | None
    """El ultimo corte canonico de la cartera, este o no la cuenta en el."""
    estado: Presencia
    cortes_observados: int
    cortes_ausentes: int
    """Cortes de la cartera, desde la primera observacion, en que la cuenta no aparece."""
    salidas_observadas: int
    reingresos_observados: int


def posiciones(cortes: Sequence[Corte]) -> dict[date, int]:
    """El lugar de cada corte en la secuencia de su cartera, desde 0. Los cortes llegan en orden de
    fecha y sin repetirse: lo garantiza la base."""
    resultado = {}
    for posicion, corte in enumerate(cortes):
        if resultado and corte.fecha_corte <= cortes[posicion - 1].fecha_corte:
            raise ValueError("Los cortes de una cartera van en orden de fecha, sin repetirse.")
        resultado[corte.fecha_corte] = posicion
    return resultado


def enlazar(lugares: Mapping[date, int], fecha: date, anterior: date | None) -> Enlace:
    """Como se une el snapshot del corte `fecha` con el snapshot `anterior` de la misma cuenta."""
    if anterior is None:
        return Enlace(None, None)
    ausentes = lugares[fecha] - lugares[anterior] - 1
    if ausentes < 0:
        raise ValueError(f"El snapshot anterior ({anterior}) no es anterior a {fecha}.")
    return Enlace(ausentes == 0, ausentes)


def eventos(cortes: Sequence[Corte], presentes: Collection[date]) -> list[Evento]:
    """Los eventos de presencia de una cuenta, en orden de corte. `presentes` son las fechas de los
    cortes en que la cuenta tiene snapshot; todas tienen que ser cortes de la cartera."""
    _comprobar(cortes, presentes)
    resultado: list[Evento] = []
    ultima: date | None = None
    ausente = 0
    for corte in cortes:
        presente = corte.fecha_corte in presentes
        fecha, corte_id = corte.fecha_corte, corte.corte_id
        if ultima is None:
            if presente:
                resultado.append(Evento(TipoEvento.PRIMERA_OBSERVACION, fecha, corte_id, None, 0))
                ultima = fecha
            continue
        if presente:
            if ausente:
                evento = Evento(TipoEvento.REINGRESO_OBSERVADO, fecha, corte_id, ultima, ausente)
                resultado.append(evento)
            ultima, ausente = fecha, 0
        else:
            if not ausente:
                resultado.append(Evento(TipoEvento.SALIDA_OBSERVADA, fecha, corte_id, ultima, 0))
            ausente += 1
    return resultado


def resumir(cortes: Sequence[Corte], presentes: Collection[date]) -> ResumenPresencia:
    """La presencia de la cuenta en `cortes`, que son los de su cartera en orden de fecha: todos, o
    los que hay hasta una fecha, para ver la cuenta como se veia entonces."""
    _comprobar(cortes, presentes)
    vistos = sorted(presentes)
    ultimo = cortes[-1] if cortes else None
    sucesos = eventos(cortes, presentes)
    desde = vistos[0] if vistos else None
    considerados = 0 if desde is None else sum(1 for c in cortes if c.fecha_corte >= desde)
    en_cartera = ultimo is not None and ultimo.fecha_corte in presentes
    return ResumenPresencia(
        primera_observacion=desde,
        ultima_observacion=vistos[-1] if vistos else None,
        ultimo_corte=ultimo,
        estado=Presencia.EN_CARTERA if en_cartera else Presencia.NO_OBSERVADA_EN_ULTIMO_CORTE,
        cortes_observados=len(vistos),
        cortes_ausentes=considerados - len(vistos),
        salidas_observadas=sum(1 for e in sucesos if e.tipo == TipoEvento.SALIDA_OBSERVADA),
        reingresos_observados=sum(1 for e in sucesos if e.tipo == TipoEvento.REINGRESO_OBSERVADO),
    )


def _comprobar(cortes: Sequence[Corte], presentes: Collection[date]) -> None:
    fechas = posiciones(cortes)
    ajenas = [f for f in presentes if f not in fechas]
    if ajenas:
        raise ValueError(f"Hay snapshots en fechas que no son cortes de la cartera: {ajenas[:3]}.")
