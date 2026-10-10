"""decision/v1 contra decision/v2 sobre el mismo corte, en modo sombra: cuanto coinciden y cuanto
no, sin decir cual es mejor.

decision/v2 corre en paralelo: no reemplaza a decision/v1 ni la alimenta. Esta comparacion es un
diagnostico del release, no un criterio para ajustar reglas: decision-policy/public-v1 se fijo antes
de verla, y una distribucion distinta se documenta, no se corrige.

Las dos se comparan por CLIENTE_UNICO: decision/v1 decide las cuentas operacionales de la corrida
que produjo el corte (su proyeccion de cartera/v2) y decision/v2, las cuentas canonicas de sus
snapshots. Una diferencia puede venir de dos fuentes, y se cuentan aparte: el ajuste por saldo de
v1 (SALDO_ALTO), que no es parte de public-v1, y los modificadores de v2, que v1 no tiene.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import text
from sqlmodel import Session, select

from motor_cartera.db.modelos import (
    CorteCanonico,
    DatasetConformado,
    EjecucionDecision,
    EjecucionDecisionV2,
    EjecucionHistoria,
    EstadoDecision,
)
from motor_cartera.decision.reglas import VERSION_REGLAS_DECISION, CodigoMotivo
from motor_cartera.decision_v2.politica import NIVEL

NIVEL_SQL = "CASE {} " + " ".join(f"WHEN '{p}' THEN {n}" for p, n in NIVEL.items()) + " END"
NIVEL_NOMBRES = [p.value for p in NIVEL]


@dataclass(frozen=True)
class Comparacion:
    decision_v2: UUID
    decision_v1: UUID | None
    """La ejecucion EXITOSA de decision/v1 de una corrida que publico el corte, si hay una."""
    cuentas_v2: int
    cuentas_v1: int
    comparadas: int
    """Las que decidieron las dos."""
    solo_v2: int
    solo_v1: int
    prioridad_igual: int
    v2_mayor: int
    v2_menor: int
    canal_igual: int
    canal_distinto: int
    v1_con_saldo_alto: int
    """Las que v1 subio por saldo: public-v1 no tiene esa regla."""
    base_v2_igual_a_v1: int
    """Las que tienen la prioridad base de v2 igual a la final de v1: cualquier diferencia en su
    prioridad final viene de los modificadores de v2."""
    matriz: dict[str, dict[str, int]] = field(default_factory=dict)
    """Cuantas cuentas pasaron de cada prioridad de v1 (fila) a cada prioridad de v2 (columna)."""
    modificadores: dict[str, int] = field(default_factory=dict)
    """Cuantas activaron cada modificador de v2, de la distribucion que conto al publicar."""


def decision_v1_del_corte(s: Session, corte: CorteCanonico) -> EjecucionDecision | None:
    """La decision/v1 EXITOSA mas reciente de una corrida que publico el corte o lo reconocio como
    fuente equivalente."""
    corridas = (
        select(DatasetConformado.corrida_id)
        .join(EjecucionHistoria, EjecucionHistoria.dataset_conformado_id == DatasetConformado.id)
        .where(EjecucionHistoria.corte_canonico_id == corte.id)
    )
    return s.exec(
        select(EjecucionDecision)
        .where(
            EjecucionDecision.corrida_id.in_(corridas),
            EjecucionDecision.version_reglas == VERSION_REGLAS_DECISION,
            EjecucionDecision.estado == EstadoDecision.EXITOSA,
        )
        .order_by(EjecucionDecision.id.desc())
    ).first()


def comparar(s: Session, ejecucion: EjecucionDecisionV2) -> Comparacion:
    """La comparacion de una ejecucion de decision/v2 con la decision/v1 de su corte, en una sola
    pasada por conjuntos."""
    corte = s.get_one(CorteCanonico, ejecucion.corte_canonico_id)
    v1 = decision_v1_del_corte(s, corte)
    parametros = {
        "v2": ejecucion.id,
        "v1": None if v1 is None else v1.id,
        "saldo_alto": f'[{{"codigo": "{CodigoMotivo.SALDO_ALTO}"}}]',
    }
    pares = (
        "WITH v2 AS (SELECT c.cliente_unico, r.prioridad_base, r.prioridad_final, "
        "r.canal_recomendado FROM resultado_decision_v2 r JOIN cuenta_canonica c "
        "ON c.id = r.cuenta_canonica_id WHERE r.ejecucion_decision_v2_id = :v2), "
        "v1 AS (SELECT cu.cliente_unico, d.prioridad, d.canal_recomendado, "
        "d.motivos @> CAST(:saldo_alto AS jsonb) AS saldo_alto FROM decision_cuenta d "
        "JOIN cuenta cu ON cu.id = d.cuenta_id WHERE d.ejecucion_decision_id = :v1) "
    )
    nivel_v1 = NIVEL_SQL.format("v1.prioridad")
    nivel_v2 = NIVEL_SQL.format("v2.prioridad_final")
    fila = s.execute(
        text(
            pares + "SELECT count(v2.cliente_unico), count(v1.cliente_unico), "
            "count(v1.cliente_unico) FILTER (WHERE v2.cliente_unico IS NOT NULL), "
            "count(*) FILTER (WHERE v1.cliente_unico IS NULL), "
            "count(*) FILTER (WHERE v2.cliente_unico IS NULL), "
            f"count(*) FILTER (WHERE {nivel_v2} = {nivel_v1}), "
            f"count(*) FILTER (WHERE {nivel_v2} > {nivel_v1}), "
            f"count(*) FILTER (WHERE {nivel_v2} < {nivel_v1}), "
            "count(*) FILTER (WHERE v2.canal_recomendado = v1.canal_recomendado), "
            "count(*) FILTER (WHERE v2.canal_recomendado <> v1.canal_recomendado), "
            "count(*) FILTER (WHERE v1.saldo_alto), "
            "count(*) FILTER (WHERE v2.prioridad_base = v1.prioridad) "
            "FROM v2 FULL JOIN v1 ON v1.cliente_unico = v2.cliente_unico"
        ),
        parametros,
    ).one()
    matriz: dict[str, dict[str, int]] = {p.value: dict.fromkeys(NIVEL_NOMBRES, 0) for p in NIVEL}
    for prioridad_v1, prioridad_v2, cuantas in s.execute(
        text(
            pares + "SELECT v1.prioridad, v2.prioridad_final, count(*) FROM v2 "
            "JOIN v1 ON v1.cliente_unico = v2.cliente_unico GROUP BY 1, 2"
        ),
        parametros,
    ).all():
        matriz[prioridad_v1][prioridad_v2] = cuantas
    return Comparacion(
        decision_v2=ejecucion.decision_run_id,
        decision_v1=None if v1 is None else v1.decision_run_id,
        cuentas_v2=fila[0],
        cuentas_v1=fila[1],
        comparadas=fila[2],
        solo_v2=fila[3],
        solo_v1=fila[4],
        prioridad_igual=fila[5],
        v2_mayor=fila[6],
        v2_menor=fila[7],
        canal_igual=fila[8],
        canal_distinto=fila[9],
        v1_con_saldo_alto=fila[10],
        base_v2_igual_a_v1=fila[11],
        matriz=matriz if v1 is not None else {},
        modificadores=dict((ejecucion.distribucion or {}).get("modificadores", {})),
    )
