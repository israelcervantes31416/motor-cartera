"""analytics-public-v1: la politica publica de Collection Analytics, como datos con su hash.

Dice lo que no es una formula pero cambia lo que se publica: los modos y sus relojes, las
granularidades y sus periodos, que conjuntos se desglosan por periodo, la escala de las razones, la
regla de la cobertura de pagos y la de las anulaciones, y la privacidad: ningun desglose expone una
celda de menos de 10 cuentas distintas (supresion primaria), ni deja reconstruir una con el total y
las demas (supresion complementaria). El umbral se fijo antes de medir nada y no se ajusta con el
benchmark.

Este modulo no sabe de la base: los periodos de un horizonte salen de aqui, y una prueba los fija.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum

from motor_cartera.analytics.catalogo import Conjunto, Vista, canonico

VERSION_POLITICA = "analytics-public-v1"


class Modo(StrEnum):
    OPERATIVO = "OPERATIVO"
    """known_at puede omitirse: es now() de la base al abrir la ejecucion, una sola vez."""
    BACKTEST = "BACKTEST"
    """known_at es obligatorio: un backtest declara su frontera de conocimiento."""


class Granularidad(StrEnum):
    TOTAL = "TOTAL"
    """Un solo periodo: todo el horizonte. Solo la vista ACUMULADA."""
    DIA = "DIA"
    SEMANA_ISO = "SEMANA_ISO"
    """De lunes a domingo."""
    MES = "MES"


@dataclass(frozen=True)
class PoliticaAnalytics:
    version: str
    min_group_size: int
    """Una celda de un desglose con menos cuentas distintas no expone su valor."""
    conjuntos_en_periodo: tuple[Conjunto, ...]
    """Los conjuntos que se desglosan tambien por periodo: los de cardinalidad baja. MUNICIPIO,
    GESTOR_CAMPO, los estados y los cruces, solo en la vista ACUMULADA."""
    max_periodos: int
    """Cuantos periodos puede tener un horizonte en su granularidad."""
    escala_razon: int
    """Los decimales de `valor` en una razon: round(numerador / denominador, escala)."""
    reglas: dict[str, str] = field(default_factory=dict)
    """Las reglas que no son un parametro numerico, por nombre: lo que significan, en texto."""

    def definicion(self) -> dict:
        return {
            "version": self.version,
            "min_group_size": self.min_group_size,
            "supresion_complementaria": True,
            "total_sin_supresion": True,
            "conjuntos_en_periodo": [c.value for c in self.conjuntos_en_periodo],
            "max_periodos": self.max_periodos,
            "escala_razon": self.escala_razon,
            "modos": [m.value for m in Modo],
            "granularidades": [g.value for g in Granularidad],
            "vistas": [v.value for v in Vista],
            "reglas": dict(sorted(self.reglas.items())),
        }


PUBLIC_V1 = PoliticaAnalytics(
    version=VERSION_POLITICA,
    min_group_size=10,
    conjuntos_en_periodo=(
        Conjunto.TOTAL,
        Conjunto.PRODUCTO,
        Conjunto.ATRASO_BUCKET,
        Conjunto.PRIORIDAD_DECISION,
        Conjunto.CANAL_DECISION,
        Conjunto.ENTIDAD,
        Conjunto.BASE,
        Conjunto.CANAL_GESTION,
        Conjunto.MEDIO_GESTION,
    ),
    max_periodos=366,
    escala_razon=10,
    reglas={
        "horizonte": (
            "La ventana de resultados es [fecha_corte, as_of], dias de calendario inclusivos en la "
            "zona declarada: desde el inicio de fecha_corte hasta el final de as_of. "
            "horizonte_dias = as_of - fecha_corte + 1."
        ),
        "relojes": (
            "as_of es hasta que dia ocurrio el negocio; known_at, hasta que instante podia "
            "conocerse. No se sustituyen: un hecho existe si ocurrio en el horizonte y se conocia "
            "a known_at."
        ),
        "manifiesto": (
            "Cada entrada se declara: nunca la ultima interpretacion, atribucion, evaluacion, "
            "decision o plan. Una entrada estaba disponible si termino (o se creo, el corte) hasta "
            "known_at."
        ),
        "anulacion": (
            "Una gestion deja de ser efectiva solo si su anulacion ocurrio antes del final de "
            "as_of y se registro hasta known_at: una anulacion futura no borra una gestion de un "
            "analisis anterior."
        ),
        "reverso": (
            "Un PAGO esta anulado solo si el REVERSO que lo anula esta en una interpretacion "
            "declarada y se recibio antes del final de as_of."
        ),
        "cobertura_pagos": (
            "Un dia tiene cobertura de pagos si cae entre el primer y el ultimo movimiento de "
            "algun archivo de pagos cuya historia termino hasta known_at, y la interpretacion "
            "declarada de su mes clasifico cada pago observado conocido de ese mes (o no hay "
            "ninguno). Un periodo sin cobertura completa publica NO_DISPONIBLE en las metricas de "
            "pagos."
        ),
        "evaluacion": (
            "La evaluacion de promesas declarada tiene el mismo as_of y la misma zona que la "
            "ejecucion: sus estados son los de ese dia."
        ),
        "plan": (
            "La fecha de operacion del plan de campo/v2 (y de su ruteo/v2) cae dentro del "
            "horizonte. Lo planificado no es lo ejecutado."
        ),
        "supresion": (
            "Primaria: una celda de un desglose distinto de TOTAL con menos de min_group_size "
            "cuentas distintas de su universo se publica SUPRIMIDO, sin valor, numerador, "
            "denominador ni poblacion. Complementaria: cada grupo de celdas (el conjunto contra "
            "TOTAL, MUNICIPIO dentro de su ENTIDAD, un cruce dentro de cada uno de sus marginales, "
            "GESTOR_CAMPO dentro de su BASE) es una ecuacion, su total es la suma de sus celdas, y "
            "ninguna queda con una sola incognita. Con el total visible, las suprimidas son "
            "ninguna, o dos o mas que juntas tienen al menos min_group_size cuentas; si no, se "
            "suprime tambien la visible con menos cuentas (desempate por dimension_hash), "
            "prefiriendo una que no sea la unica celda de algun grupo, y si ya no queda ninguna "
            "visible, el total. Con el total suprimido, alguna de sus celdas tambien. Hasta que "
            "todos los grupos cumplan. La misma celda se suprime en todos los periodos."
        ),
        "razon": (
            "Una razon publica su numerador y su denominador; con denominador cero, su valor es "
            "NULL y su estado DISPONIBLE, nunca 0 % ni 100 %."
        ),
    },
)

POLITICAS: dict[str, PoliticaAnalytics] = {PUBLIC_V1.version: PUBLIC_V1}


def hash_politica(politica: PoliticaAnalytics = PUBLIC_V1) -> str:
    """SHA-256 de la definicion canonica de la politica."""
    return hashlib.sha256(canonico(politica.definicion())).hexdigest()


# --- los periodos ---------------------------------------------------------------------------------


@dataclass(frozen=True, order=True)
class Periodo:
    desde: date
    hasta: date
    """Inclusive."""
    parcial: bool
    """Si el horizonte lo corta: empieza despues del inicio de su dia, semana o mes, o termina
    antes de su final."""


def horizonte_dias(fecha_corte: date, as_of: date) -> int:
    return (as_of - fecha_corte).days + 1


def _inicio(dia: date, granularidad: Granularidad) -> date:
    if granularidad == Granularidad.DIA:
        return dia
    if granularidad == Granularidad.SEMANA_ISO:
        return dia - timedelta(days=dia.isoweekday() - 1)
    if granularidad == Granularidad.MES:
        return date(dia.year, dia.month, 1)
    raise ValueError(f"{granularidad} no se desglosa en periodos.")


def _siguiente(inicio: date, granularidad: Granularidad) -> date:
    if granularidad == Granularidad.DIA:
        return inicio + timedelta(days=1)
    if granularidad == Granularidad.SEMANA_ISO:
        return inicio + timedelta(days=7)
    return date(inicio.year + inicio.month // 12, inicio.month % 12 + 1, 1)


def periodos(fecha_corte: date, as_of: date, granularidad: Granularidad) -> list[Periodo]:
    """Los periodos de la vista PERIODO, en orden: cada dia, semana ISO o mes que toca el horizonte,
    recortado a el. Con TOTAL no hay ninguno: la unica vista es ACUMULADA."""
    if as_of < fecha_corte:
        raise ValueError(f"as_of ({as_of}) es anterior al corte ({fecha_corte}).")
    if granularidad == Granularidad.TOTAL:
        return []
    resultado = []
    inicio = _inicio(fecha_corte, granularidad)
    while inicio <= as_of:
        fin = _siguiente(inicio, granularidad) - timedelta(days=1)
        desde, hasta = max(inicio, fecha_corte), min(fin, as_of)
        resultado.append(Periodo(desde, hasta, desde != inicio or hasta != fin))
        inicio = _siguiente(inicio, granularidad)
    return resultado


def cuantos_periodos(fecha_corte: date, as_of: date, granularidad: Granularidad) -> int:
    """Cuantos periodos tendria la vista PERIODO, sin construirlos: para rechazar un horizonte
    demasiado largo antes de abrir nada."""
    if granularidad == Granularidad.TOTAL:
        return 0
    if granularidad == Granularidad.DIA:
        return horizonte_dias(fecha_corte, as_of)
    if granularidad == Granularidad.SEMANA_ISO:
        return (_inicio(as_of, granularidad) - _inicio(fecha_corte, granularidad)).days // 7 + 1
    return (as_of.year - fecha_corte.year) * 12 + as_of.month - fecha_corte.month + 1
