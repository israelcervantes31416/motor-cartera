"""Segmentacion de la cartera: en que grupo cae cada cuenta, y cuanto suma cada grupo."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import ColumnElement, case, func
from sqlmodel import Session, select

# Los tramos viven en atraso.py, la unica fuente de sus fronteras. tramo_de_atraso no se usa en
# este modulo: se importa para que quien lo importaba desde aqui siga funcionando.
from motor_cartera.atraso import TRAMOS_ATRASO, tramo_de_atraso  # noqa: F401
from motor_cartera.db.modelos import Cuenta


class Dimension(StrEnum):
    """Por que se puede segmentar la cartera."""

    PRODUCTO = "producto"
    CANAL = "canal"
    TRAMO_ATRASO = "tramo_atraso"
    CVE_ENTIDAD = "cve_entidad"


@dataclass(frozen=True)
class Segmento:
    claves: dict[str, str]
    cuentas: int
    saldo_total: Decimal


@dataclass(frozen=True)
class Resumen:
    segmentos: list[Segmento]
    """Solo los de la pagina pedida."""
    total_segmentos: int
    cuentas: int
    saldo_total: Decimal


def resumir(
    s: Session,
    corrida_id: int,
    dimensiones: list[Dimension],
    *,
    producto: str | None = None,
    canal: str | None = None,
    desplazamiento: int = 0,
    limite: int = 50,
) -> Resumen:
    """Cuentas y saldo por segmento de las cuentas de una corrida.

    Se agrega en la base y no en pandas: la cartera puede tener cientos de miles de
    cuentas, y lo que tiene que viajar es solo el resultado. Los segmentos salen en un
    orden fijo (los tramos de menor a mayor atraso), para que paginar sea estable.
    """
    expresiones = [_expresion(d) for d in dimensiones]
    filtros = [Cuenta.corrida_id == corrida_id]
    if producto is not None:
        filtros.append(Cuenta.producto == producto)
    if canal is not None:
        filtros.append(Cuenta.canal == canal)

    grupos = (
        select(*expresiones, func.count().label("cuentas"), func.sum(Cuenta.saldo_total))
        .where(*filtros)
        .group_by(*expresiones)
        .order_by(*expresiones)
    )
    total_segmentos = s.exec(select(func.count()).select_from(grupos.subquery())).one()
    pagina = s.exec(grupos.offset(desplazamiento).limit(limite)).all()
    cuentas, saldo = s.exec(
        select(func.count(), func.coalesce(func.sum(Cuenta.saldo_total), 0)).where(*filtros)
    ).one()

    segmentos = [
        Segmento(
            claves={d.value: _etiqueta(d, fila[i]) for i, d in enumerate(dimensiones)},
            cuentas=fila[len(dimensiones)],
            saldo_total=fila[len(dimensiones) + 1],
        )
        for fila in pagina
    ]
    return Resumen(
        segmentos=segmentos,
        total_segmentos=total_segmentos,
        cuentas=cuentas,
        saldo_total=Decimal(saldo),
    )


def _expresion(dimension: Dimension) -> ColumnElement:
    if dimension is Dimension.TRAMO_ATRASO:
        # Se agrupa por la posicion del tramo, no por su etiqueta: asi el orden es el del
        # atraso ("0", "1-30", ... "91+") y no el alfabetico.
        ultimo = len(TRAMOS_ATRASO) - 1
        cortes = [
            (Cuenta.dias_atraso <= hasta, i)
            for i, (_, _, hasta) in enumerate(TRAMOS_ATRASO)
            if hasta is not None
        ]
        return case(*cortes, else_=ultimo).label(dimension.value)
    return getattr(Cuenta, dimension.value).label(dimension.value)


def _etiqueta(dimension: Dimension, valor: object) -> str:
    if dimension is Dimension.TRAMO_ATRASO:
        return TRAMOS_ATRASO[int(valor)][0]
    return str(valor)
