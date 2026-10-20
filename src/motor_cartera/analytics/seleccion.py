"""Proponer el manifiesto de una ejecucion de analytics/v1: que entradas existian a known_at, cual
se propone de cada familia y por que, y cuales son ambiguas. Nunca inicia un analisis.

El algoritmo, por familia, sobre las ejecuciones EXITOSA de la cartera terminadas hasta known_at
(en BACKTEST, nada de despues):

- MOTOR_PAGOS, por cada mes que toca el horizonte: la interpretacion vigente a known_at, la de
  mayor id de su ventana. Una reinterpretacion de una ventana sustituye a la anterior: es la regla
  de motor-pagos/v1 para su vigente, no una eleccion. Las demas se listan.
- ATRIBUCION, por cada mes con interpretacion propuesta: la atribucion/v1 que leyo exactamente esa
  interpretacion. Si hay dos o mas (otra ventana de dias u otra zona), es ambigua.
- EVALUACION_PROMESAS: la de ese as_of en esa zona. Dos o mas, ambigua.
- DECISION_V2, GEOGRAFIA: la del corte. Dos o mas (otro known_at, otra referencia), ambigua.
- TERRITORIAL_V2: la del corte sobre la geografia propuesta. CAMPO_V2: la del corte sobre la
  decision y la territorial propuestas, con su fecha de operacion en el horizonte (o la declarada).
  RUTEO_V2: la del plan propuesto. Dos o mas en cualquiera, ambigua.

Una entrada ambigua no se elige: se reporta con sus candidatas, y quien pide la declara. Una entrada
declarada por quien pide no se elige: se usa. Sin ambiguedades, el manifiesto se comprueba con
`manifiesto.resolver`, que no registra nada.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from uuid import UUID

from sqlmodel import Session, col, select

from motor_cartera.analytics import manifiesto
from motor_cartera.analytics.catalogo import Fuente
from motor_cartera.analytics.politica import Granularidad, Modo
from motor_cartera.atribucion.reglas import VERSION_ATRIBUCION
from motor_cartera.db.modelos import (
    CorteCanonico,
    EjecucionAtribucion,
    EjecucionCampoV2,
    EjecucionDecisionV2,
    EjecucionEvaluacionPromesas,
    EjecucionGeografia,
    EjecucionMotorPagos,
    EjecucionRuteoV2,
    EjecucionTerritorialV2,
    id_de,
)
from motor_cartera.evaluacion.reglas import VERSION_EVALUACION
from motor_cartera.motor_pagos.reglas import VERSION_MOTOR_PAGOS

FAMILIAS = (
    Fuente.MOTOR_PAGOS,
    Fuente.ATRIBUCION,
    Fuente.EVALUACION_PROMESAS,
    Fuente.DECISION_V2,
    Fuente.GEOGRAFIA,
    Fuente.TERRITORIAL_V2,
    Fuente.CAMPO_V2,
    Fuente.RUTEO_V2,
)
"""Las entradas que se pueden proponer, ademas del corte."""

VIGENTE = "VIGENTE_A_KNOWN_AT"
UNICA = "UNICA_CANDIDATA"
DECLARADA = "DECLARADA"
AMBIGUA = "AMBIGUA"
SIN_CANDIDATA = "SIN_CANDIDATA"


@dataclass(frozen=True)
class Candidata:
    referencia: UUID
    firma: str
    terminada_en: datetime | None
    """None si se declaro y no es candidata a known_at: resolver dira por que."""
    detalle: str


@dataclass(frozen=True)
class Eleccion:
    """Lo que se propone de una familia (y de una ventana, en las mensuales), y por que."""

    tipo: Fuente
    ventana: date | None
    regla: str
    elegida: Candidata | None
    candidatas: tuple[Candidata, ...]


@dataclass
class Propuesta:
    corte: CorteCanonico
    known_at: datetime
    elecciones: list[Eleccion] = field(default_factory=list)
    peticion: manifiesto.Peticion | None = None
    resuelto: manifiesto.Resuelto | None = None

    @property
    def ambiguas(self) -> list[Eleccion]:
        return [e for e in self.elecciones if e.regla == AMBIGUA]


def _candidata(ejecucion, referencia: UUID, firma: str | None, detalle: str) -> Candidata:
    if firma is None:  # pragma: no cover - la base exige la firma de una EXITOSA
        raise AssertionError(f"La ejecucion EXITOSA {referencia} no tiene su firma.")
    return Candidata(referencia, firma, ejecucion.terminada_en, detalle)


def _una(
    tipo: Fuente,
    filas: Sequence[tuple[object, Candidata]],
    declarada: UUID | None,
    ventana: date | None = None,
) -> tuple[Eleccion, object | None]:
    """La eleccion de una familia con una sola entrada: la declarada, la unica, o ninguna."""
    candidatas = tuple(c for _, c in filas)
    if declarada is not None:
        elegida = next((c for c in candidatas if c.referencia == declarada), None)
        fila = next((f for f, c in filas if c.referencia == declarada), None)
        if elegida is None:
            elegida = Candidata(declarada, "", None, "declarada; no es candidata a known_at")
        return Eleccion(tipo, ventana, DECLARADA, elegida, candidatas), fila
    if not filas:
        return Eleccion(tipo, ventana, SIN_CANDIDATA, None, ()), None
    if len(filas) > 1:
        return Eleccion(tipo, ventana, AMBIGUA, None, candidatas), None
    return Eleccion(tipo, ventana, UNICA, candidatas[0], candidatas), filas[0][0]


def proponer(
    s: Session,
    *,
    corte: CorteCanonico,
    as_of: date,
    modo: Modo,
    zona: str,
    granularidad: Granularidad,
    known_at: datetime,
    familias: frozenset[Fuente] = frozenset(FAMILIAS),
    declaradas: dict[Fuente, tuple[UUID, ...]] | None = None,
    fecha_operacion: date | None = None,
) -> Propuesta:
    """La propuesta de manifiesto. Si no tiene ambiguedades, trae su peticion y el manifiesto
    resuelto (que levanta una EntradaNoAnalizable si no se puede analizar). No escribe nada."""
    declaradas = declaradas or {}
    p = Propuesta(corte, known_at)
    interpretaciones: dict[date, EjecucionMotorPagos] = {}
    if Fuente.MOTOR_PAGOS in familias:
        fijas = declaradas.get(Fuente.MOTOR_PAGOS, ())
        filas = s.exec(
            select(EjecucionMotorPagos).where(
                EjecucionMotorPagos.despacho_id == corte.despacho_id,
                EjecucionMotorPagos.cartera_id == corte.cartera_id,
                EjecucionMotorPagos.estado == "EXITOSA",
                EjecucionMotorPagos.version_motor == VERSION_MOTOR_PAGOS,
                col(EjecucionMotorPagos.terminada_en) <= known_at,
            )
        ).all()
        for mes in manifiesto.meses_del_horizonte(corte.fecha_corte, as_of):
            de_la_ventana = sorted((e for e in filas if e.periodo_desde == mes), key=id_de)
            candidatas = tuple(
                _candidata(e, e.motor_pagos_run_id, e.firma_entrada, f"interpretacion {e.id}")
                for e in de_la_ventana
            )
            fija = next((e for e in de_la_ventana if e.motor_pagos_run_id in fijas), None)
            if fija is not None:
                regla, vigente = DECLARADA, fija
            elif de_la_ventana:
                regla, vigente = VIGENTE, de_la_ventana[-1]
            else:
                p.elecciones.append(Eleccion(Fuente.MOTOR_PAGOS, mes, SIN_CANDIDATA, None, ()))
                continue
            interpretaciones[mes] = vigente
            elegida = next(c for c in candidatas if c.referencia == vigente.motor_pagos_run_id)
            p.elecciones.append(Eleccion(Fuente.MOTOR_PAGOS, mes, regla, elegida, candidatas))
    atribuciones: list[UUID] = []
    if Fuente.ATRIBUCION in familias:
        fijas = declaradas.get(Fuente.ATRIBUCION, ())
        for mes, interpretacion in sorted(interpretaciones.items()):
            filas = s.exec(
                select(EjecucionAtribucion)
                .where(
                    EjecucionAtribucion.ejecucion_motor_pagos_id == interpretacion.id,
                    EjecucionAtribucion.estado == "EXITOSA",
                    EjecucionAtribucion.version_atribucion == VERSION_ATRIBUCION,
                    col(EjecucionAtribucion.terminada_en) <= known_at,
                )
                .order_by(col(EjecucionAtribucion.id))
            ).all()
            pares = [
                (
                    e,
                    _candidata(
                        e,
                        e.atribucion_run_id,
                        e.firma_entrada,
                        f"ventana de {e.ventana_dias} dias, {e.zona_horaria}",
                    ),
                )
                for e in filas
            ]
            fija = next((c.referencia for _, c in pares if c.referencia in fijas), None)
            eleccion, _ = _una(Fuente.ATRIBUCION, pares, fija, mes)
            p.elecciones.append(eleccion)
            if eleccion.elegida is not None:
                atribuciones.append(eleccion.elegida.referencia)
    evaluacion = None
    if Fuente.EVALUACION_PROMESAS in familias:
        filas = s.exec(
            select(EjecucionEvaluacionPromesas)
            .where(
                EjecucionEvaluacionPromesas.despacho_id == corte.despacho_id,
                EjecucionEvaluacionPromesas.cartera_id == corte.cartera_id,
                EjecucionEvaluacionPromesas.estado == "EXITOSA",
                EjecucionEvaluacionPromesas.version_evaluacion == VERSION_EVALUACION,
                EjecucionEvaluacionPromesas.as_of == as_of,
                EjecucionEvaluacionPromesas.zona_horaria == zona,
                col(EjecucionEvaluacionPromesas.terminada_en) <= known_at,
            )
            .order_by(col(EjecucionEvaluacionPromesas.id))
        ).all()
        pares = [
            (e, _candidata(e, e.evaluacion_run_id, e.firma_entrada, f"as_of {e.as_of}"))
            for e in filas
        ]
        eleccion, _ = _una(
            Fuente.EVALUACION_PROMESAS,
            pares,
            _fija(declaradas, Fuente.EVALUACION_PROMESAS),
        )
        p.elecciones.append(eleccion)
        evaluacion = None if eleccion.elegida is None else eleccion.elegida.referencia
    elegidas: dict[Fuente, object | None] = {}
    for tipo, modelo, publico, condiciones in (
        (
            Fuente.DECISION_V2,
            EjecucionDecisionV2,
            "decision_run_id",
            lambda: [EjecucionDecisionV2.corte_canonico_id == corte.id],
        ),
        (
            Fuente.GEOGRAFIA,
            EjecucionGeografia,
            "geografia_run_id",
            lambda: [EjecucionGeografia.corte_canonico_id == corte.id],
        ),
        (
            Fuente.TERRITORIAL_V2,
            EjecucionTerritorialV2,
            "territorial_run_id",
            lambda: [
                EjecucionTerritorialV2.corte_canonico_id == corte.id,
                EjecucionTerritorialV2.ejecucion_geografia_id == _id(elegidas, Fuente.GEOGRAFIA),
            ],
        ),
        (
            Fuente.CAMPO_V2,
            EjecucionCampoV2,
            "campo_run_id",
            lambda: [
                EjecucionCampoV2.corte_canonico_id == corte.id,
                EjecucionCampoV2.ejecucion_decision_v2_id == _id(elegidas, Fuente.DECISION_V2),
                EjecucionCampoV2.ejecucion_territorial_v2_id
                == _id(elegidas, Fuente.TERRITORIAL_V2),
                EjecucionCampoV2.fecha_operacion >= corte.fecha_corte,
                EjecucionCampoV2.fecha_operacion <= as_of,
                *(
                    []
                    if fecha_operacion is None
                    else [EjecucionCampoV2.fecha_operacion == fecha_operacion]
                ),
            ],
        ),
        (
            Fuente.RUTEO_V2,
            EjecucionRuteoV2,
            "ruteo_run_id",
            lambda: [EjecucionRuteoV2.ejecucion_campo_v2_id == _id(elegidas, Fuente.CAMPO_V2)],
        ),
    ):
        if tipo not in familias:
            continue
        filas = s.exec(
            select(modelo)
            .where(modelo.estado == "EXITOSA", col(modelo.terminada_en) <= known_at, *condiciones())
            .order_by(col(modelo.id))
        ).all()
        pares = [
            (
                e,
                _candidata(
                    e,
                    getattr(e, publico),
                    e.firma_inputs,
                    _detalle(tipo, e),
                ),
            )
            for e in filas
        ]
        eleccion, fila = _una(tipo, pares, _fija(declaradas, tipo))
        if eleccion.regla == DECLARADA and fila is None and eleccion.elegida is not None:
            fila = s.exec(
                select(modelo).where(getattr(modelo, publico) == eleccion.elegida.referencia)
            ).one_or_none()
        p.elecciones.append(eleccion)
        elegidas[tipo] = fila if eleccion.elegida is not None else None
    if p.ambiguas:
        return p

    def ref(tipo: Fuente) -> UUID | None:
        e = next(
            (x.elegida for x in p.elecciones if x.tipo == tipo and x.elegida is not None), None
        )
        return None if e is None else e.referencia

    p.peticion = manifiesto.Peticion(
        corte_id=corte.corte_id,
        as_of=as_of,
        modo=modo,
        zona_horaria=zona,
        granularidad=granularidad,
        known_at=known_at,
        motor_pagos=tuple(e.motor_pagos_run_id for _, e in sorted(interpretaciones.items())),
        atribucion=tuple(atribuciones),
        evaluacion_promesas=evaluacion,
        decision_v2=ref(Fuente.DECISION_V2),
        geografia=ref(Fuente.GEOGRAFIA),
        territorial_v2=ref(Fuente.TERRITORIAL_V2),
        campo_v2=ref(Fuente.CAMPO_V2),
        ruteo_v2=ref(Fuente.RUTEO_V2),
    )
    p.resuelto = manifiesto.resolver(s, p.peticion)
    return p


def _fija(declaradas: dict[Fuente, tuple[UUID, ...]], tipo: Fuente) -> UUID | None:
    fijas = declaradas.get(tipo, ())
    return fijas[0] if fijas else None


def _id(elegidas: dict, tipo: Fuente) -> int:
    """El id interno de lo propuesto de una familia; -1 si no se propuso nada: no hay ninguna
    ejecucion con ese id, y lo que depende de ella no tiene candidatas."""
    fila = elegidas.get(tipo)
    return -1 if fila is None else fila.id


def _detalle(tipo: Fuente, e) -> str:
    if tipo == Fuente.DECISION_V2:
        return f"{e.modo}, known_at {e.known_at.isoformat()}, {e.version_politica}"
    if tipo == Fuente.CAMPO_V2:
        return f"fecha de operacion {e.fecha_operacion.isoformat()}, {e.version_politica}"
    if tipo == Fuente.RUTEO_V2:
        return f"red vial {e.version_red_vial_id}, {e.version_politica}"
    return f"ejecucion {e.id}"
