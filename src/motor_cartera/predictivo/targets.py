"""predictive-targets/public-v1: que se predice, en que ventana, de que fuente y cuando esta maduro.

Un target es un resultado observado despues de la prediccion, bajo la operacion historica: lo que
paso, no lo que habria pasado con otra accion. La prediccion se toma al cierre de `feature_as_of`
(T) en la zona declarada, y la ventana del target es (T, T+H]: los dias T+1 a T+H, enteros. Lo que
ocurrio el dia T, a cualquier hora, es pasado: puede ser una feature y nunca un label. Lo mismo en
el entrenamiento, el backtest, el scoring, la documentacion y las pruebas.

Un label se construye solo con hechos registrados hasta `label_known_at`, que puede ser posterior a
la prediccion; las features, con lo que se conocia hasta `feature_known_at`. Un label solo existe si
su horizonte esta MADURO: la cobertura declarada de su fuente (`pagos_completos_hasta` o
`lifecycle_completo_hasta`) llega al final de su ventana. Si no llega, el ejemplo esta CENSURADO: no
entra al entrenamiento y nunca se vuelve un cero. Si la cobertura no se declaro, NO_DISPONIBLE.

RIESGO_SIN_RECUPERACION_30D no es un target ni un modelo: es 1 - P(RECUPERACION_POSITIVA_30D), con
la precision publicada.

Este modulo no sabe de la base.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from motor_cartera.analytics.catalogo import canonico

VERSION_TARGETS = "predictive-targets/public-v1"


class Target(StrEnum):
    RECUPERACION_POSITIVA_30D = "RECUPERACION_POSITIVA_30D"
    CONTACTO_14D = "CONTACTO_14D"
    PROMESA_30D = "PROMESA_30D"
    RECUPERACION_POSITIVA_MONTO_30D = "RECUPERACION_POSITIVA_MONTO_30D"


class TipoTarget(StrEnum):
    BINARIO = "BINARIO"
    MONTO = "MONTO"


class Fuente(StrEnum):
    """De que cobertura depende la madurez de un target."""

    PAGOS = "PAGOS"
    LIFECYCLE = "LIFECYCLE"


class Madurez(StrEnum):
    MADURA = "MADURA"
    """La cobertura declarada de su fuente llega al final de su ventana: el label existe."""
    CENSURADA = "CENSURADA"
    """La ventana todavia no termina en lo que cubre la fuente: no hay label, y no es un cero."""
    NO_DISPONIBLE = "NO_DISPONIBLE"
    """La cobertura de su fuente no se declaro: no se sabe si la ausencia de hechos es un cero."""


RIESGO_SIN_RECUPERACION_30D = "RIESGO_SIN_RECUPERACION_30D"
"""El output derivado: 1 - p_recuperacion_positiva_30d, con la misma precision. Sin modelo
propio."""


@dataclass(frozen=True)
class DefinicionTarget:
    target: Target
    tipo: TipoTarget
    horizonte_dias: int
    fuente: Fuente
    unidad: str
    salida: str
    """El nombre del output publicado de su componente."""
    regla: str
    """La definicion formal, para una persona."""
    madurez: str


TARGETS: tuple[DefinicionTarget, ...] = (
    DefinicionTarget(
        Target.RECUPERACION_POSITIVA_30D,
        TipoTarget.BINARIO,
        30,
        Fuente.PAGOS,
        "probabilidad",
        "p_recuperacion_positiva_30d",
        "1 si la recuperacion neta de la cuenta en (T, T+30] es mayor que cero; 0 si es cero o "
        "negativa. La recuperacion neta es la de motor-pagos/v1 (y analytics/v1): la suma de los "
        "PAGO no anulados mas la de los POSIBLE_REVERSO, de los MovimientoEconomicoCanonico "
        "conciliados con la cuenta, recibidos en la ventana, de las interpretaciones declaradas en "
        "el manifiesto. Un PAGO esta anulado si el REVERSO que lo anula se recibio antes del final "
        "de la ventana; un reverso posterior no existe para este label. Tener un pago no es tener "
        "recuperacion positiva.",
        "MADURA si pagos_completos_hasta >= T + 30.",
    ),
    DefinicionTarget(
        Target.CONTACTO_14D,
        TipoTarget.BINARIO,
        14,
        Fuente.LIFECYCLE,
        "probabilidad",
        "p_contacto_14d",
        "1 si la cuenta tiene al menos una gestion efectiva con nivel de contacto CONTACTO_TITULAR "
        "o CONTACTO_TERCERO ocurrida en (T, T+14]: la cuenta contactada de analytics/v1. Una "
        "gestion es efectiva si se registro hasta label_known_at y no tiene una anulacion ocurrida "
        "antes del final de la ventana y registrada hasta label_known_at.",
        "MADURA si lifecycle_completo_hasta >= T + 14.",
    ),
    DefinicionTarget(
        Target.PROMESA_30D,
        TipoTarget.BINARIO,
        30,
        Fuente.LIFECYCLE,
        "probabilidad",
        "p_promesa_30d",
        "1 si se crea al menos una PromesaPago de la cuenta en (T, T+30]: su PROMESA_CREADA "
        "ocurrida en la ventana, registrada hasta label_known_at, de una gestion efectiva (la "
        "misma regla de anulacion de CONTACTO_14D). Mide la creacion, no el cumplimiento: una "
        "cancelacion posterior no cambia que se creo.",
        "MADURA si lifecycle_completo_hasta >= T + 30.",
    ),
    DefinicionTarget(
        Target.RECUPERACION_POSITIVA_MONTO_30D,
        TipoTarget.MONTO,
        30,
        Fuente.PAGOS,
        "pesos",
        "recuperacion_positiva_esperada_30d",
        "max(recuperacion neta de la cuenta en (T, T+30], 0), con la recuperacion neta de "
        "RECUPERACION_POSITIVA_30D: nunca negativo. Su prediccion es la recuperacion POSITIVA "
        "esperada, no la recuperacion neta esperada: los netos negativos se truncaron a cero por "
        "definicion.",
        "MADURA si pagos_completos_hasta >= T + 30.",
    ),
)

POR_TARGET: dict[Target, DefinicionTarget] = {t.target: t for t in TARGETS}
CLASIFICADORES = tuple(t.target for t in TARGETS if t.tipo == TipoTarget.BINARIO)
HORIZONTE_MAXIMO = max(t.horizonte_dias for t in TARGETS)
"""El horizonte mas largo: el de la purga entre bloques del split."""


def madurez(
    definicion: DefinicionTarget,
    feature_as_of: date,
    *,
    pagos_completos_hasta: date | None,
    lifecycle_completo_hasta: date | None,
) -> Madurez:
    """La madurez del label de un ancla: si la cobertura declarada de su fuente llega al final de
    su ventana."""
    cobertura = (
        pagos_completos_hasta if definicion.fuente == Fuente.PAGOS else lifecycle_completo_hasta
    )
    if cobertura is None:
        return Madurez.NO_DISPONIBLE
    if cobertura >= feature_as_of + timedelta(days=definicion.horizonte_dias):
        return Madurez.MADURA
    return Madurez.CENSURADA


def definicion() -> dict:
    return {
        "version": VERSION_TARGETS,
        "momento_de_prediccion": "Al cierre de feature_as_of (T), en la zona declarada.",
        "ventana": "(T, T+H]: los dias de calendario T+1 a T+H, enteros, en la zona declarada. "
        "Lo ocurrido el dia T es pasado.",
        "relojes": "feature_as_of: dia de negocio de la prediccion; feature_known_at: hasta que "
        "instante se conocia lo que usan las features (nunca posterior a la prediccion); "
        "label_known_at: hasta que instante se observa para construir el label.",
        "madurez": [m.value for m in Madurez],
        "targets": [
            {
                "target": t.target.value,
                "tipo": t.tipo.value,
                "horizonte_dias": t.horizonte_dias,
                "fuente": t.fuente.value,
                "unidad": t.unidad,
                "salida": t.salida,
                "regla": t.regla,
                "madurez": t.madurez,
            }
            for t in TARGETS
        ],
        "derivados": [
            {
                "salida": "riesgo_sin_recuperacion_30d",
                "regla": "1 - p_recuperacion_positiva_30d, con la precision publicada. Sin modelo "
                "propio.",
            }
        ],
        "no_causal": "Un target es un resultado observado bajo la operacion historica. Su "
        "probabilidad no es el efecto de ninguna accion.",
    }


def hash_targets() -> str:
    """SHA-256 de la definicion canonica de los targets."""
    return hashlib.sha256(canonico(definicion())).hexdigest()
