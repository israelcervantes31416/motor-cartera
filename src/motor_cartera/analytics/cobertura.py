"""La cobertura de pagos de un horizonte: que dias se pueden describir con lo que se conocia a
known_at, y por que no los demas.

pagos/v1 no declara el periodo que cubre un archivo: la evidencia es lo que trae. Un dia tiene
cobertura si cae entre el primer y el ultimo movimiento de algun archivo de pagos cuya historia
termino hasta known_at, y si la interpretacion declarada de su mes clasifico, uno por uno, cada pago
observado conocido de ese mes (o el mes no tiene ninguno). Asi un hueco entre archivos, o una
interpretacion que no vio un archivo que llego despues, no se vuelve un cero: el periodo publica
NO_DISPONIBLE, con su motivo. Es conservador: un archivo cuyo primer o ultimo dia no tuvo pagos
cubre menos dias de los que cubria.

La atribucion de un dia necesita, ademas, la atribucion declarada de su mes, si su mes tiene una
interpretacion declarada.

Este modulo no sabe de la base.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from motor_cartera.analytics.politica import Periodo

ENTRADA_NO_DECLARADA = "ENTRADA_NO_DECLARADA"
PAGOS_SIN_COBERTURA = "PAGOS_SIN_COBERTURA"
INTERPRETACION_INCOMPLETA = "INTERPRETACION_INCOMPLETA"
ATRIBUCION_NO_DECLARADA = "ATRIBUCION_NO_DECLARADA"


@dataclass(frozen=True)
class Mes:
    mes: date
    observados: int
    """Los pagos observados del mes conocidos a known_at."""
    clasificados: int
    """Cuantos de ellos clasifico la interpretacion declarada del mes."""


@dataclass(frozen=True)
class Dia:
    pagos: str | None
    """None si tiene cobertura; si no, el codigo de por que."""
    atribucion: str | None


def _mes(dia: date) -> date:
    return date(dia.year, dia.month, 1)


def por_dia(
    fecha_corte: date,
    as_of: date,
    *,
    pagos_declarados: bool,
    alcances: Iterable[tuple[datetime, datetime]],
    meses: Iterable[Mes],
    interpretados: set[date],
    atribuidos: set[date],
) -> dict[date, Dia]:
    """La cobertura de cada dia del horizonte, para los pagos y para la atribucion."""
    dias_alcanzados = [(a.date(), b.date()) for a, b in alcances]
    por_mes = {m.mes: m for m in meses}
    resultado = {}
    dia = fecha_corte
    while dia <= as_of:
        mes = _mes(dia)
        visto = por_mes.get(mes)
        if not pagos_declarados:
            pagos = ENTRADA_NO_DECLARADA
        elif not any(desde <= dia <= hasta for desde, hasta in dias_alcanzados):
            pagos = PAGOS_SIN_COBERTURA
        elif (
            visto is not None
            and visto.observados > 0
            and (mes not in interpretados or visto.clasificados < visto.observados)
        ):
            pagos = INTERPRETACION_INCOMPLETA
        else:
            pagos = None
        if pagos is not None:
            atribucion = pagos
        elif mes in interpretados and mes not in atribuidos:
            atribucion = ATRIBUCION_NO_DECLARADA
        else:
            atribucion = None
        resultado[dia] = Dia(pagos, atribucion)
        dia += timedelta(days=1)
    return resultado


def motivos(dias: dict[date, Dia], periodo: Periodo, familia: str) -> list[dict]:
    """Por que un periodo no tiene cobertura: cada codigo con cuantos dias y el primero y el ultimo
    de ellos. Vacia si la tiene completa."""
    por_codigo: dict[str, list[date]] = defaultdict(list)
    dia = periodo.desde
    while dia <= periodo.hasta:
        codigo = getattr(dias[dia], familia)
        if codigo is not None:
            por_codigo[codigo].append(dia)
        dia += timedelta(days=1)
    return [
        {
            "codigo": codigo,
            "dias": len(lista),
            "desde": lista[0].isoformat(),
            "hasta": lista[-1].isoformat(),
        }
        for codigo, lista in sorted(por_codigo.items())
    ]
