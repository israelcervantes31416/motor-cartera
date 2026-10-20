"""Las opciones para abrir un contexto: las carteras del despacho, sus cortes y, de un corte, las
ejecuciones de cada tipo y los contextos compatibles que se pueden proponer.

Una sugerencia es solo eso: la pantalla la muestra con todos sus identificadores y la persona elige
abrirla. Nada se abre solo, y ninguna sugerencia mezcla ejecuciones que no encadenan: cada una se
valida con las mismas reglas que un contexto declarado a mano.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Final, Literal
from uuid import UUID

from sqlalchemy import text
from sqlmodel import Session

from motor_cartera.app_v1.contexto import Contexto, Corte, Ejecucion, Tipo, leer, problemas

EJECUCIONES_POR_TIPO = 20
"""Cuantas ejecuciones de cada tipo se listan de un corte: las mas recientes. Un corte tiene unas
pocas; el limite es para que ninguna respuesta crezca sin control."""

SUGERENCIAS = 6


@dataclass(frozen=True)
class Cartera:
    cartera_id: str
    cortes: int
    ultimo_corte: date


def carteras(s: Session, despacho_id: str) -> list[Cartera]:
    filas = s.execute(
        text(
            "SELECT cartera_id, count(*), max(fecha_corte) FROM corte_canonico "
            "WHERE despacho_id = :despacho GROUP BY cartera_id ORDER BY cartera_id"
        ),
        {"despacho": despacho_id},
    ).all()
    return [Cartera(*f) for f in filas]


def del_corte(s: Session, corte: Corte) -> dict[Tipo, tuple[int, list[Ejecucion]]]:
    """De cada tipo, cuantas hay en el corte y las mas recientes: seis consultas de lectura y una
    de conteos, mas las entradas de analytics."""
    totales = dict(
        s.execute(
            text(
                "SELECT 'DECISION_V2', count(*) FROM ejecucion_decision_v2 "
                "WHERE corte_canonico_id = :k "
                "UNION ALL SELECT 'ANALYTICS', count(*) FROM ejecucion_analytics "
                "WHERE corte_canonico_id = :k "
                "UNION ALL SELECT 'SCORING', count(*) FROM ejecucion_scoring_predictivo "
                "WHERE corte_canonico_id = :k "
                "UNION ALL SELECT 'OPTIMIZACION', count(*) FROM ejecucion_optimizacion "
                "WHERE corte_canonico_id = :k "
                "UNION ALL SELECT 'CAMPO_V2', count(*) FROM ejecucion_campo_v2 "
                "WHERE corte_canonico_id = :k "
                "UNION ALL SELECT 'RUTEO_V2', count(*) FROM ejecucion_ruteo_v2 r "
                "JOIN ejecucion_campo_v2 c ON c.id = r.ejecucion_campo_v2_id "
                "WHERE c.corte_canonico_id = :k"
            ),
            {"k": corte.id},
        )
        .tuples()
        .all()
    )
    return {
        tipo: (totales[tipo.value], leer(s, tipo, corte_id=corte.id, limite=EJECUCIONES_POR_TIPO))
        for tipo in Tipo
    }


CodigoSugerencia = Literal["DESDE_OPTIMIZACION", "DESDE_ANALYTICS"]
DESDE_OPTIMIZACION: Final = "DESDE_OPTIMIZACION"
DESDE_ANALYTICS: Final = "DESDE_ANALYTICS"


@dataclass(frozen=True)
class Sugerencia:
    contexto: Contexto
    codigo: CodigoSugerencia
    motivo: str


def sugerencias(corte: Corte, ejecuciones: dict[Tipo, list[Ejecucion]]) -> list[Sugerencia]:
    """Contextos compatibles armados con lo que las ejecuciones EXITOSA del corte declararon: cada
    optimizacion con su scoring, su decision/v2, su plan y su ruteo (y, si hay una, la ejecucion de
    analytics mas reciente que leyo lo mismo), y cada analytics con lo que declaro su manifiesto (y
    la optimizacion mas reciente que encadena con el). Solo las que pasan la validacion."""

    def exitosas(tipo: Tipo) -> list[Ejecucion]:
        return [e for e in ejecuciones.get(tipo, []) if e.estado == "EXITOSA"]

    def contexto_de(base: dict[Tipo, UUID]) -> Contexto:
        return Contexto(
            corte_id=corte.corte_id,
            decision_run_id=base.get(Tipo.DECISION_V2),
            analytics_run_id=base.get(Tipo.ANALYTICS),
            scoring_run_id=base.get(Tipo.SCORING),
            optimizacion_run_id=base.get(Tipo.OPTIMIZACION),
            campo_run_id=base.get(Tipo.CAMPO_V2),
            ruteo_run_id=base.get(Tipo.RUTEO_V2),
        )

    def cadena(e: Ejecucion) -> dict[Tipo, UUID]:
        return {e.tipo: e.run_id, **{Tipo(k): v for k, v in e.lee.items() if k in Tipo.__members__}}

    def compatible(base: dict[Tipo, UUID]) -> bool:
        contexto = contexto_de(base)
        presentes = {t: _por_id(ejecuciones, t, r) for t, r in contexto.declaradas().items()}
        encontradas = {t: e for t, e in presentes.items() if e is not None}
        if len(encontradas) != len(presentes):
            return False
        return not problemas(contexto, corte, encontradas)

    candidatas: list[tuple[dict[Tipo, UUID], CodigoSugerencia, str]] = []
    for optimizacion in exitosas(Tipo.OPTIMIZACION):
        base = cadena(optimizacion)
        for analytics in exitosas(Tipo.ANALYTICS):
            unida = {**base, **cadena(analytics)}
            if compatible(unida):
                base = unida
                break
        candidatas.append(
            (
                base,
                DESDE_OPTIMIZACION,
                "La optimizacion y lo que declaro, con analytics si encadena.",
            )
        )
    for analytics in exitosas(Tipo.ANALYTICS):
        base = cadena(analytics)
        for optimizacion in exitosas(Tipo.OPTIMIZACION):
            unida = {**base, **cadena(optimizacion)}
            if compatible(unida):
                base = unida
                break
        candidatas.append(
            (
                base,
                DESDE_ANALYTICS,
                "La ejecucion de analytics y su manifiesto, con optimizacion si encadena.",
            )
        )
    vistas: set[Contexto] = set()
    resultado = []
    for base, codigo, motivo in candidatas:
        contexto = contexto_de(base)
        if contexto in vistas or not compatible(base):
            continue
        vistas.add(contexto)
        resultado.append(Sugerencia(contexto, codigo, motivo))
        if len(resultado) == SUGERENCIAS:
            break
    return resultado


def _por_id(ejecuciones: dict[Tipo, list[Ejecucion]], tipo: Tipo, run_id: UUID) -> Ejecucion | None:
    return next((e for e in ejecuciones.get(tipo, []) if e.run_id == run_id), None)
