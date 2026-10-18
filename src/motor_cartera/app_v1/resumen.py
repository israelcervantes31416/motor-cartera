"""La distribucion de las predicciones publicadas de un scoring: lo unico que app/v1 agrega.

La pantalla del resumen y la del scoring muestran cuantas predicciones publico un scoring, sus
medias, la suma de su recuperacion positiva esperada, cuantas llevan imputacion y cuantas caen en
cada decimo de [0, 1] de cada probabilidad. Ningun motor publica ese agregado, y contarlo en el
navegador exigiria bajar cada prediccion: se cuenta aqui, en la base, en una sola pasada por las
predicciones del scoring (500,000 en el benchmark, en medio segundo).

Es un resumen descriptivo de lo publicado, no una prediccion nueva ni una metrica de analytics/v1:
no cambia ningun valor, no recalibra nada y no dice que una cuenta vaya a pagar.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import text
from sqlmodel import Session

BINS = 10
"""En cuantos intervalos iguales de [0, 1] se cuenta cada probabilidad: el ultimo incluye el 1."""

SALIDAS = ("p_recuperacion_positiva_30d", "p_contacto_14d", "p_promesa_30d")
"""Las probabilidades publicadas. El riesgo sin recuperacion es 1 - p_recuperacion: su
distribucion es la de la recuperacion al reves, y no se cuenta aparte."""

ESCALA = Decimal("0.00000001")
"""Las medias, con los mismos decimales que las probabilidades publicadas."""


@dataclass(frozen=True)
class Distribucion:
    publicadas: int
    con_imputacion: int
    medias: dict[str, Decimal | None]
    suma_recuperacion_positiva_esperada_30d: Decimal | None
    histogramas: dict[str, list[int]]


def _en_el_decimo(salida: str, k: int) -> str:
    """La condicion exacta del decimo k (de 1 a BINS) de [0, 1], en numeric: [k-1, k) / BINS, y el
    ultimo con el 1. Es la de width_bucket, sin pasar por punto flotante."""
    desde = Decimal(k - 1) / BINS
    if k == BINS:
        return f"{salida} >= {desde}"
    return f"{salida} >= {desde} AND {salida} < {Decimal(k) / BINS}"


def de_scoring(s: Session, scoring_id: int) -> Distribucion:
    """Una pasada por las predicciones del scoring, por su llave y en paralelo: los totales y, por
    cada probabilidad, un conteo por decimo con un FILTER de comparaciones exactas. Agrupar con
    width_bucket sobre numeric costaba el doble. Sin una cuenta: conteos, medias y una suma."""
    conteos = ", ".join(
        f"count(*) FILTER (WHERE {_en_el_decimo(salida, k)}) AS h{i}_{k}"
        for i, salida in enumerate(SALIDAS)
        for k in range(1, BINS + 1)
    )
    totales = (
        s.execute(
            text(
                "SELECT count(*) AS n, "
                "count(*) FILTER (WHERE estado = 'CON_IMPUTACION') AS imputadas, "
                + ", ".join(f"avg({salida}) AS m{i}" for i, salida in enumerate(SALIDAS))
                + ", sum(recuperacion_positiva_esperada_30d) AS esperada, "
                + conteos
                + " FROM prediccion_cuenta WHERE ejecucion_scoring_predictivo_id = :e"
            ),
            {"e": scoring_id},
        )
        .mappings()
        .one()
    )
    return Distribucion(
        publicadas=totales["n"],
        con_imputacion=totales["imputadas"],
        medias={
            salida: None if totales[f"m{i}"] is None else totales[f"m{i}"].quantize(ESCALA)
            for i, salida in enumerate(SALIDAS)
        },
        suma_recuperacion_positiva_esperada_30d=totales["esperada"],
        histogramas={
            salida: [totales[f"h{i}_{k}"] for k in range(1, BINS + 1)]
            for i, salida in enumerate(SALIDAS)
        },
    )
