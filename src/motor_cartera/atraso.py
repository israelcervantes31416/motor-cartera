"""Tramos de dias de atraso: la unica fuente de sus fronteras.

Los usan el resumen de la cartera, el generador sintetico y las reglas de decision. Viven aqui,
sin nada fuera de la biblioteca estandar, para que las reglas de decision puedan usarlos sin
arrastrar la base de datos.
"""

from __future__ import annotations

TRAMOS_ATRASO: tuple[tuple[str, int, int | None], ...] = (
    ("0", 0, 0),
    ("1-30", 1, 30),
    ("31-60", 31, 60),
    ("61-90", 61, 90),
    ("91+", 91, None),
)
"""Cubetas de dias de atraso: (etiqueta, desde, hasta). `None` es "sin tope".

Es la forma habitual de leer una cartera en cobranza: la gestion cambia con el tramo.
"""


def tramo_de_atraso(dias: int) -> str:
    """Etiqueta del tramo al que pertenece `dias` de atraso."""
    for etiqueta, desde, hasta in TRAMOS_ATRASO:
        if dias >= desde and (hasta is None or dias <= hasta):
            return etiqueta
    raise ValueError(f"Dias de atraso fuera de todo tramo: {dias}")
