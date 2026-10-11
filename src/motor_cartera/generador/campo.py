"""Configuraciones de campo sinteticas: gestores, calendarios y excepciones inventados,
deterministas por semilla.

Nada sale de personas reales: un gestor es un codigo (GESTOR_0001, GESTOR_0002...), su cupo se
elige de una lista declarada, su calendario de tres calendarios publicos del generador, y una
fraccion declarada queda inactiva. Las excepciones son fechas que se declaran (un festivo cierra
todos los calendarios; una media jornada los deja de 9 a 13). La misma semilla con los mismos
parametros da el mismo documento, byte por byte.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from datetime import date

DIAS_HABILES = ("LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES")

CALENDARIOS = (
    (
        "LV_PARTIDO",
        {dia: [("09:00", "14:00"), ("16:00", "19:00")] for dia in DIAS_HABILES},
        0.6,
    ),
    (
        "LS_CORRIDO",
        {dia: [("08:00", "15:00")] for dia in (*DIAS_HABILES, "SABADO")},
        0.3,
    ),
    (
        "JD_EXTENDIDO",
        {dia: [("10:00", "18:00")] for dia in ("JUEVES", "VIERNES", "SABADO", "DOMINGO")},
        0.1,
    ),
)
"""Los calendarios del generador, con su peso: el turno partido de lunes a viernes, el corrido de
lunes a sabado, y uno de jueves a domingo."""

CAPACIDADES = (15, 20, 25)
INACTIVOS = 0.05
MEDIA_JORNADA = (("09:00", "13:00"),)


def configuracion_sintetica(
    bases: Sequence[str],
    *,
    bases_version_id: str,
    gestores_por_base: int | Mapping[str, int],
    semilla: int,
    capacidades: Sequence[int] = CAPACIDADES,
    inactivos: float = INACTIVOS,
    festivos: Sequence[date] = (),
    medias_jornadas: Sequence[date] = (),
    zona_horaria: str = "America/Mexico_City",
    nombre: str | None = None,
) -> dict:
    """El documento de una configuracion de campo para esas bases (sus codigos), listo para
    `cargar-campo`. `gestores_por_base` es un numero para todas, o uno por codigo (una base que no
    esta tiene 0). Los gestores se numeran en el orden de las bases y se reparten con la semilla:
    su calendario por los pesos de CALENDARIOS, su cupo de `capacidades` y si esta activo con
    probabilidad 1 - `inactivos`."""
    if not 0 <= inactivos < 1:
        raise ValueError(f"inactivos es una fraccion en [0, 1): {inactivos}.")
    if not capacidades or any(c < 1 for c in capacidades):
        raise ValueError(f"Cada cupo es un entero positivo: {list(capacidades)}.")
    azar = random.Random(semilla)
    codigos = [c for c, _, _ in CALENDARIOS]
    pesos = [p for _, _, p in CALENDARIOS]
    gestores = []
    numero = 0
    for codigo_base in sorted(bases):
        cuantos = (
            gestores_por_base
            if isinstance(gestores_por_base, int)
            else gestores_por_base.get(codigo_base, 0)
        )
        for _ in range(cuantos):
            numero += 1
            gestores.append(
                {
                    "codigo": f"GESTOR_{numero:04d}",
                    "base": codigo_base,
                    "calendario": azar.choices(codigos, pesos)[0],
                    "capacidad_cuentas_dia": azar.choice(list(capacidades)),
                    "activo": azar.random() >= inactivos,
                }
            )
    excepciones = [
        {"fecha": f.isoformat(), "tipo": "CERRADO", "motivo": "Festivo"} for f in sorted(festivos)
    ] + [
        {
            "fecha": f.isoformat(),
            "tipo": "VENTANAS_ESPECIALES",
            "motivo": "Media jornada",
            "ventanas": [{"inicio": i, "fin": f_} for i, f_ in MEDIA_JORNADA],
        }
        for f in sorted(medias_jornadas)
    ]
    return {
        "nombre": nombre or f"Campo sintetico (semilla {semilla})",
        "bases_version_id": bases_version_id,
        "zona_horaria": zona_horaria,
        "calendarios": [
            {
                "codigo": codigo,
                "semana": {
                    dia: [{"inicio": i, "fin": f} for i, f in ventanas]
                    for dia, ventanas in semana.items()
                },
                "excepciones": excepciones,
            }
            for codigo, semana, _ in CALENDARIOS
        ],
        "gestores": gestores,
    }
