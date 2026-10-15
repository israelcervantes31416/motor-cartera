"""Lo que la cola necesita saber de cada recurso que ejecuta un trabajo.

Un trabajo apunta a una corrida, a una ejecucion de alguno de los motores, a una ingesta de pagos, a
una ejecucion historica, a una del motor de pagos, a una atribucion, a una evaluacion de promesas, a
una decision v2, a una geografia, a una ejecucion de territorial/v2, a un plan de campo/v2, a
una ejecucion de ruteo/v2 o a una de analytics/v1.
Para cerrarlo, el worker necesita saber en que tabla vive ese recurso, cuales de sus estados son
terminales, como se llama su identificador publico y como se deja FALLIDO cuando la cola agota sus
intentos sin que el motor lo terminara. Los motores no saben de la cola y la cola no sabe de sus
reglas: este modulo es lo unico que conoce a los dos lados.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import update
from sqlmodel import Session, SQLModel, select

from motor_cartera.db.modelos import (
    Corrida,
    EjecucionAnalytics,
    EjecucionAtribucion,
    EjecucionCampoV2,
    EjecucionDecision,
    EjecucionDecisionV2,
    EjecucionEvaluacionPromesas,
    EjecucionGeografia,
    EjecucionHistoria,
    EjecucionMotorPagos,
    EjecucionRuteo,
    EjecucionRuteoV2,
    EjecucionTerritorial,
    EjecucionTerritorialV2,
    EstadoAnalytics,
    EstadoAtribucion,
    EstadoCampoV2,
    EstadoCorrida,
    EstadoDecision,
    EstadoDecisionV2,
    EstadoEvaluacionPromesas,
    EstadoGeografia,
    EstadoHistoria,
    EstadoIngestaPagos,
    EstadoMotorPagos,
    EstadoRuteo,
    EstadoRuteoV2,
    EstadoTerritorial,
    EstadoTerritorialV2,
    IngestaPagos,
    ResultadoAtribucion,
    ResultadoEjecucionAnalytics,
    ResultadoEjecucionCampoV2,
    ResultadoEjecucionDecisionV2,
    ResultadoEjecucionGeografia,
    ResultadoEjecucionRuteoV2,
    ResultadoEjecucionTerritorialV2,
    ResultadoEvaluacionPromesas,
    ResultadoHistoria,
    ResultadoMotorPagos,
    TipoTrabajo,
    ahora,
)


@dataclass(frozen=True)
class Objetivo:
    """Un tipo de recurso, como lo ve la cola."""

    modelo: type[SQLModel]
    estados: type[StrEnum]
    """Su catalogo de estados. Todos tienen EN_PROCESO, el unico que no es terminal, y FALLIDA."""
    columna: str
    """La columna de TrabajoOrquestacion que apunta a el."""
    publico: str
    """El atributo con su identificador publico: run_id, decision_run_id..."""
    sin_publicar: dict[str, Any]
    """Lo que una FALLIDA dice que publico: nada."""

    @property
    def terminales(self) -> frozenset[str]:
        """Los estados de los que ya no sale: todos menos EN_PROCESO."""
        return frozenset(e for e in self.estados if e != self.estados.EN_PROCESO)


OBJETIVOS: dict[TipoTrabajo, Objetivo] = {
    TipoTrabajo.INGESTA: Objetivo(Corrida, EstadoCorrida, "corrida_id", "run_id", {}),
    TipoTrabajo.DECISION: Objetivo(
        EjecucionDecision,
        EstadoDecision,
        "ejecucion_decision_id",
        "decision_run_id",
        {"cuentas_decididas": 0},
    ),
    TipoTrabajo.TERRITORIAL: Objetivo(
        EjecucionTerritorial,
        EstadoTerritorial,
        "ejecucion_territorial_id",
        "territorial_run_id",
        {"territorios_publicados": 0},
    ),
    TipoTrabajo.RUTEO: Objetivo(
        EjecucionRuteo,
        EstadoRuteo,
        "ejecucion_ruteo_id",
        "ruteo_run_id",
        {"rutas_publicadas": 0, "paradas_publicadas": 0},
    ),
    TipoTrabajo.INGESTA_PAGOS: Objetivo(
        IngestaPagos, EstadoIngestaPagos, "ingesta_pagos_id", "pagos_run_id", {}
    ),
    TipoTrabajo.HISTORIA: Objetivo(
        EjecucionHistoria,
        EstadoHistoria,
        "ejecucion_historia_id",
        "historia_run_id",
        {"registros_publicados": 0, "resultado": ResultadoHistoria.INTENTOS_AGOTADOS.value},
    ),
    TipoTrabajo.MOTOR_PAGOS: Objetivo(
        EjecucionMotorPagos,
        EstadoMotorPagos,
        "ejecucion_motor_pagos_id",
        "motor_pagos_run_id",
        {"resultado": ResultadoMotorPagos.INTENTOS_AGOTADOS.value},
    ),
    TipoTrabajo.ATRIBUCION: Objetivo(
        EjecucionAtribucion,
        EstadoAtribucion,
        "ejecucion_atribucion_id",
        "atribucion_run_id",
        {"resultado": ResultadoAtribucion.INTENTOS_AGOTADOS.value},
    ),
    TipoTrabajo.EVALUACION_PROMESAS: Objetivo(
        EjecucionEvaluacionPromesas,
        EstadoEvaluacionPromesas,
        "ejecucion_evaluacion_promesas_id",
        "evaluacion_run_id",
        {"resultado": ResultadoEvaluacionPromesas.INTENTOS_AGOTADOS.value},
    ),
    TipoTrabajo.DECISION_V2: Objetivo(
        EjecucionDecisionV2,
        EstadoDecisionV2,
        "ejecucion_decision_v2_id",
        "decision_run_id",
        {
            "cuentas_decididas": 0,
            "resultado": ResultadoEjecucionDecisionV2.INTENTOS_AGOTADOS.value,
        },
    ),
    TipoTrabajo.GEOGRAFIA: Objetivo(
        EjecucionGeografia,
        EstadoGeografia,
        "ejecucion_geografia_id",
        "geografia_run_id",
        {
            "validas": 0,
            "sin_coordenadas": 0,
            "incompletas": 0,
            "invalidas": 0,
            "fuera_cobertura": 0,
            "ambiguas": 0,
            "discrepantes": 0,
            "resultado": ResultadoEjecucionGeografia.INTENTOS_AGOTADOS.value,
        },
    ),
    TipoTrabajo.TERRITORIAL_V2: Objetivo(
        EjecucionTerritorialV2,
        EstadoTerritorialV2,
        "ejecucion_territorial_v2_id",
        "territorial_run_id",
        {
            "asignadas": 0,
            "revision_geografica": 0,
            "sin_base": 0,
            "fuera_cobertura": 0,
            "ambiguas": 0,
            "resultado": ResultadoEjecucionTerritorialV2.INTENTOS_AGOTADOS.value,
        },
    ),
    TipoTrabajo.CAMPO_V2: Objetivo(
        EjecucionCampoV2,
        EstadoCampoV2,
        "ejecucion_campo_v2_id",
        "campo_run_id",
        {
            "candidatas_campo": 0,
            "asignadas": 0,
            "revision_territorial": 0,
            "sin_gestor": 0,
            "sin_capacidad": 0,
            "rebalanceadas": 0,
            "capacidad_total": 0,
            "capacidad_utilizada": 0,
            "resultado": ResultadoEjecucionCampoV2.INTENTOS_AGOTADOS.value,
        },
    ),
    TipoTrabajo.RUTEO_V2: Objetivo(
        EjecucionRuteoV2,
        EstadoRuteoV2,
        "ejecucion_ruteo_v2_id",
        "ruteo_run_id",
        {
            "gestores_objetivo": 0,
            "rutas_completas": 0,
            "rutas_parciales": 0,
            "rutas_sin_paradas": 0,
            "bases_no_enrutables": 0,
            "rutas_sin_jornada": 0,
            "paradas_objetivo": 0,
            "programadas": 0,
            "sin_tiempo": 0,
            "no_enrutables": 0,
            "no_enrutables_base": 0,
            "distancia_total_m": 0,
            "duracion_vial_total_s": 0,
            "duracion_servicio_total_s": 0,
            "duracion_espera_total_s": 0,
            "resultado": ResultadoEjecucionRuteoV2.INTENTOS_AGOTADOS.value,
        },
    ),
    TipoTrabajo.ANALYTICS: Objetivo(
        EjecucionAnalytics,
        EstadoAnalytics,
        "ejecucion_analytics_id",
        "analytics_run_id",
        {
            "agregados_publicados": 0,
            "resultado": ResultadoEjecucionAnalytics.INTENTOS_AGOTADOS.value,
        },
    ),
}


def estado(s: Session, tipo: TipoTrabajo, objetivo_id: int) -> StrEnum:
    """El estado del recurso, como esta en la base en este momento."""
    modelo = OBJETIVOS[tipo].modelo
    return s.exec(select(modelo.estado).where(modelo.id == objetivo_id)).one()


def es_terminal(tipo: TipoTrabajo, estado_actual: str) -> bool:
    return estado_actual in OBJETIVOS[tipo].terminales


def fallar(s: Session, tipo: TipoTrabajo, objetivo_id: int, motivo: str) -> bool:
    """Deja el recurso FALLIDA, en la transaccion de quien llama, solo si sigue EN_PROCESO: con el
    motivo, su fin y nada publicado. Devuelve si lo cambio.

    Es un UPDATE condicionado al estado que tiene la base. Un estado terminal no cambia: si el
    recurso ya habia terminado, de la forma que sea, se queda como esta, y no se borra nada de lo
    que haya publicado.
    """
    objetivo = OBJETIVOS[tipo]
    modelo, estados = objetivo.modelo, objetivo.estados
    cambiados = s.execute(
        update(modelo)
        .where(modelo.id == objetivo_id, modelo.estado == estados.EN_PROCESO)
        .values(
            estado=estados.FALLIDA, detalle=motivo, terminada_en=ahora(), **objetivo.sin_publicar
        )
        .execution_options(synchronize_session=False)
    ).rowcount
    return cambiados == 1
