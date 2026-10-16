"""Lo que comparten las pruebas de predictive/model-v1: el escenario golden de las fugas, armado a
mano, y el escenario generado con escenario-predictivo/v1 para entrenar y puntuar de verdad. Todo
es sintetico.

El golden: nueve cuentas en cuatro cortes (miercoles 2026-07-01, 2026-08-05, 2026-09-02 y T, el
2026-09-09), en America/Mexico_City. Primero se conoce lo de hasta T (la frontera K); despues
llegan los pagos y el lifecycle de la ventana de los labels, algunos tarde; despues, L.

- 1: un pago el dia T (feature) y otro el T+1 (label, nunca feature);
- 2: un pago en la ventana y su reverso, 29 dias despues, ya fuera de la ventana (T+32): el label
  sigue positivo;
- 3: un pago en la ventana y su reverso dentro de ella: recuperacion neta cero, label 0;
- 4: un pago antes de T que un reverso de la ventana anula: a K es una feature de 250; su reverso
  es de un pago anterior a la ventana y no entra a su neto: label 0;
- 5: un contacto el dia T-1 conocido a K, y otro del mismo dia registrado despues de K (llega
  tarde: a K no existe); un contacto en la ventana: label de contacto 1;
- 6: un contacto en la ventana anulado dentro de ella: label de contacto 0;
- 7: una promesa en la ventana, cancelada despues: el target es la creacion, label 1;
- 8 y 9: los pagos de relleno que dan a cada archivo su alcance (la cobertura de pagos) y a cada
  mes su interpretacion; 9 tambien tiene un contacto en la ventana registrado despues de L.

Una evaluacion de promesas con as_of 2026-09-02 termina antes de K, y otra con as_of T despues.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from decision_v2_escenarios import reloj
from historia_escenarios import Cuenta, cuenta, ingerir_corte, ingerir_pagos_de, pago
from lifecycle_escenarios import anular_gestion, cancelar_promesa, gestion_de, promesa_de
from motor_pagos_escenarios import historiar_todo, publicar
from sqlalchemy import text
from sqlmodel import select

from motor_cartera.db.modelos import (
    CorteCanonico,
    EjecucionEvaluacionPromesas,
    EjecucionMotorPagos,
)
from motor_cartera.db.sesion import sesion
from motor_cartera.evaluacion import ejecuciones as evaluacion
from motor_cartera.predictivo import construccion, sql

ZONA = "America/Mexico_City"
DESPACHO, CARTERA = "DSP_001", "CARTERA_PRINCIPAL"
CORTES = (date(2026, 7, 1), date(2026, 8, 5), date(2026, 9, 2), date(2026, 9, 9))
T = CORTES[-1]
COBERTURA = date(2026, 10, 9)
"""T + 30: hasta donde llegan completos los pagos y el lifecycle del golden."""


def _cuentas(fecha: date) -> list[Cuenta]:
    # La 7 baja su saldo de corte a corte: sus deltas y su tendencia no son cero.
    indice = CORTES.index(fecha)
    return [
        Cuenta(n, saldo=10_000 - (1_000 * indice if n == 7 else 0), dias=15 + 7 * indice)
        for n in range(1, 10)
    ]


def evaluar_promesas(as_of: date) -> EjecucionEvaluacionPromesas:
    with sesion() as s:
        ejecucion, _ = evaluacion.abrir(
            s, DESPACHO, CARTERA, as_of, zona=ZONA, max_intentos=5, reusar=False
        )
        s.commit()
        ejecucion_id = ejecucion.id
    evaluacion.evaluar(ejecucion_id)
    with sesion() as s:
        evaluada = s.get_one(EjecucionEvaluacionPromesas, ejecucion_id)
    assert evaluada.estado == "EXITOSA", evaluada.detalle
    return evaluada


@dataclass(frozen=True)
class Golden:
    K: datetime
    L: datetime
    cortes: dict[date, UUID]
    """corte_id por fecha."""
    ids: dict[date, int]
    """El id interno de cada corte."""


def golden(destino: Path) -> Golden:
    for fecha in CORTES:
        ingerir_corte(destino, fecha, _cuentas(fecha))
    historiar_todo()
    publicar(
        ingerir_pagos_de(
            destino,
            "pagos_a.csv",
            [
                pago(9, "2026-06-11", "100.00"),
                pago(9, "2026-07-15", "100.00"),
                pago(9, "2026-08-15", "100.00"),
                pago(4, "2026-09-05", "250.00"),
                pago(1, "2026-09-09", "500.00"),
                pago(8, "2026-09-09", "50.00"),
            ],
        )
    )
    c = {n: cuenta(n) for n in range(1, 10)}
    gestion_de(c[5].cuenta_id, "2026-09-08T10:00:00-06:00")
    evaluar_promesas(CORTES[2])
    K = reloj()
    # Despues de K: lo que llega tarde y lo de la ventana de los labels.
    gestion_de(c[5].cuenta_id, "2026-09-08T12:00:00-06:00")
    publicar(
        ingerir_pagos_de(
            destino,
            "pagos_b.csv",
            [
                pago(1, "2026-09-10", "300.00"),
                pago(2, "2026-09-12", "400.00"),
                pago(3, "2026-09-12", "200.00"),
                pago(4, "2026-09-15", "-250.00"),
                pago(3, "2026-09-20", "-200.00"),
                pago(8, "2026-10-09", "10.00"),
            ],
        )
    )
    gestion_de(c[5].cuenta_id, "2026-09-15T10:00:00-06:00")
    anulada = gestion_de(c[6].cuenta_id, "2026-09-15T11:00:00-06:00")
    anular_gestion(anulada.recurso_id, "2026-09-20T09:00:00-06:00")
    con_promesa = gestion_de(c[7].cuenta_id, "2026-09-16T10:00:00-06:00")
    promesa = promesa_de(con_promesa.recurso_id, fecha_limite="2026-09-30")
    cancelar_promesa(promesa.recurso_id, "2026-09-18T10:00:00-06:00")
    evaluar_promesas(T)
    publicar(
        ingerir_pagos_de(
            destino,
            "pagos_c.csv",
            [pago(2, "2026-10-11", "-400.00"), pago(8, "2026-10-25", "10.00")],
        )
    )
    L = reloj()
    gestion_de(c[9].cuenta_id, "2026-09-15T10:00:00-06:00")
    with sesion() as s:
        filas = s.exec(select(CorteCanonico)).all()
        cortes = {k.fecha_corte: k.corte_id for k in filas}
        ids = {k.fecha_corte: k.id for k in filas}
    return Golden(K, L, cortes, ids)


def vigentes(conocido: datetime) -> list[EjecucionMotorPagos]:
    """La interpretacion vigente a `conocido` de cada mes."""
    with sesion() as s:
        filas = s.execute(
            text(
                "SELECT DISTINCT ON (periodo_desde) id FROM ejecucion_motor_pagos "
                "WHERE estado = 'EXITOSA' AND terminada_en <= :k ORDER BY periodo_desde, id DESC"
            ),
            {"k": conocido},
        ).all()
        return [s.get_one(EjecucionMotorPagos, i) for (i,) in filas]


def features_y_labels(
    g: Golden,
    *,
    as_of: date = T,
    known_at: datetime,
    label_known_at: datetime | None = None,
    pagos: bool = True,
    contacto: bool = True,
    promesa: bool = True,
) -> SimpleNamespace:
    """Las features (con su feature_hash) y los labels crudos de cada cuenta del corte de as_of,
    por numero de cliente, como las arma el dataset: el mismo SQL."""
    relojes = construccion.Relojes(g.ids[as_of], DESPACHO, CARTERA, as_of, known_at, ZONA)
    with sesion() as s:
        disponibilidad = construccion.preparar_features(s, relojes)
        construccion.preparar_labels(
            s,
            relojes,
            label_known_at=label_known_at or known_at,
            interpretaciones=[e.id for e in vigentes(label_known_at or known_at)],
        )
        filas = (
            s.execute(
                text(
                    "SELECT c.cliente_unico, d.* FROM ("
                    + sql.sql_dataset(pagos=pagos, contacto=contacto, promesa=promesa)
                    + ") d JOIN cuenta_canonica c ON c.cuenta_id = d.cuenta_id"
                )
            )
            .mappings()
            .all()
        )
        crudas = s.execute(text("SELECT * FROM pf_feature")).mappings().all()
        s.rollback()
    por_cuenta = {int(f["cliente_unico"][2:]): dict(f) for f in filas}
    return SimpleNamespace(
        filas=por_cuenta,
        crudas={str(f["cuenta_id"]): dict(f) for f in crudas},
        disponibilidad=disponibilidad,
    )


def manifiesto(
    g: Golden,
    *,
    splits: dict[date, str] | None = None,
    interpretaciones: list[str] | None = None,
    cambios: dict[date, dict] | None = None,
) -> dict:
    """Un manifiesto valido del golden: TRAIN 07-01, VALIDATION 08-05, TEST T, con 09-02 PURGE,
    feature_known_at K, label_known_at L y la cobertura hasta T + 30."""
    splits = splits or {
        CORTES[0]: "TRAIN",
        CORTES[1]: "VALIDATION",
        CORTES[2]: "PURGE",
        T: "TEST",
    }
    anclas = [
        {
            "corte_id": str(g.cortes[fecha]),
            "feature_as_of": fecha.isoformat(),
            "feature_known_at": g.K.isoformat(),
            "label_known_at": g.L.isoformat(),
            "pagos_completos_hasta": COBERTURA.isoformat(),
            "lifecycle_completo_hasta": COBERTURA.isoformat(),
            "split": split,
            **(cambios or {}).get(fecha, {}),
        }
        for fecha, split in splits.items()
    ]
    return json.loads(
        json.dumps(
            {
                "zona_horaria": ZONA,
                "anclas": anclas,
                "interpretaciones_pagos": interpretaciones
                if interpretaciones is not None
                else [str(e.motor_pagos_run_id) for e in vigentes(g.L)],
            }
        )
    )


# --- el escenario generado ------------------------------------------------------------------------

CUENTAS_GENERADAS = 600
CORTES_GENERADOS = 20
PRIMER_CORTE = date(2026, 3, 4)
"""Del 2026-03-04 al 2026-07-15: TRAIN del 03-04 al 04-01, VALIDATION el 05-06, TEST el 06-10."""


def generado(destino: Path) -> dict:
    """Genera y carga el escenario de escenario-predictivo/v1 de las pruebas: su bitacora."""
    from motor_cartera.generador.predictivo import generar_escenario_predictivo
    from motor_cartera.predictivo.escenario import cargar

    generar_escenario_predictivo(
        destino,
        cuentas=CUENTAS_GENERADAS,
        cortes=CORTES_GENERADOS,
        primer_corte=PRIMER_CORTE,
        hoy=date(2026, 10, 15),
    )
    return cargar(destino)


def propuesto(bitacora: dict, *, variante: int = 0) -> dict:
    """El manifiesto que propone construir-manifiesto-predictivo para el escenario generado (TEST y
    VALIDATION de un ancla). Una variante distinta de 0 cambia la cobertura declarada del ultimo
    ancla PURGE: el mismo entrenamiento con otra firma."""
    from motor_cartera.predictivo import manifiesto as manifiesto_

    with sesion() as s:
        fechas = {
            str(c): f
            for c, f in s.execute(text("SELECT corte_id, fecha_corte FROM corte_canonico")).all()
        }
        datos = manifiesto_.proponer(
            s,
            despacho_id=bitacora["despacho_id"],
            cartera_id=bitacora["cartera_id"],
            zona=bitacora["zona_horaria"],
            cortes=[
                manifiesto_.CorteCandidato(
                    UUID(c["corte_id"]),
                    fechas[c["corte_id"]],
                    datetime.fromisoformat(c["known_at"]),
                )
                for c in bitacora["cortes"]
            ],
            label_known_at=datetime.fromisoformat(bitacora["label_known_at"]),
            pagos_completos_hasta=date.fromisoformat(
                bitacora["cobertura"]["pagos_completos_hasta"]
            ),
            lifecycle_completo_hasta=date.fromisoformat(
                bitacora["cobertura"]["lifecycle_completo_hasta"]
            ),
            anclas_validation=1,
            anclas_test=1,
        )
        s.rollback()
    if variante:
        ultima = datos["anclas"][-1]
        assert ultima["split"] == "PURGE"
        ultima["pagos_completos_hasta"] = (
            date.fromisoformat(ultima["pagos_completos_hasta"]) - timedelta(days=variante)
        ).isoformat()
    return datos


def abrir_entrenamiento(datos: dict, **opciones) -> int:
    from motor_cartera.predictivo import entrenamiento
    from motor_cartera.predictivo import manifiesto as manifiesto_

    with sesion() as s:
        e = entrenamiento.abrir(s, manifiesto_.leer(datos), max_intentos=5, **opciones)
        s.commit()
        return e.id


def entrenamiento_de(ejecucion_id: int):
    from motor_cartera.db.modelos import EjecucionEntrenamientoPredictivo

    with sesion() as s:
        return s.get_one(EjecucionEntrenamientoPredictivo, ejecucion_id)


def entrenar(datos: dict):
    """Abre y entrena, en este proceso, sin la cola: la ejecucion terminada."""
    from motor_cartera.predictivo import entrenamiento

    ejecucion_id = abrir_entrenamiento(datos)
    entrenamiento.entrenar(ejecucion_id)
    return entrenamiento_de(ejecucion_id)


def version_de(ejecucion_id: int):
    from motor_cartera.db.modelos import VersionModeloPredictivo

    with sesion() as s:
        return s.exec(
            select(VersionModeloPredictivo).where(
                VersionModeloPredictivo.ejecucion_entrenamiento_predictivo_id == ejecucion_id
            )
        ).one_or_none()


def componentes_de(version_id: int) -> dict:
    from motor_cartera.db.modelos import ComponenteModeloPredictivo

    with sesion() as s:
        return {
            c.target: c
            for c in s.exec(
                select(ComponenteModeloPredictivo).where(
                    ComponenteModeloPredictivo.version_modelo_predictivo_id == version_id
                )
            ).all()
        }


def corte_de(bitacora: dict, fecha: str) -> dict:
    return next(c for c in bitacora["cortes"] if c["fecha_corte"] == fecha)


def abrir_scoring(modelo_version_id, corte_id, *, known_at=None, modo="OPERATIVO") -> int:
    from motor_cartera.db.modelos import ModoScoringPredictivo
    from motor_cartera.predictivo import scoring

    with sesion() as s:
        e = scoring.abrir(
            s,
            modelo_version_id=modelo_version_id,
            corte_id=corte_id,
            known_at=known_at,
            modo=ModoScoringPredictivo(modo),
            max_intentos=5,
        )
        s.commit()
        return e.id


def scoring_de(ejecucion_id: int):
    from motor_cartera.db.modelos import EjecucionScoringPredictivo

    with sesion() as s:
        return s.get_one(EjecucionScoringPredictivo, ejecucion_id)


def puntuar(modelo_version_id, corte_id, *, known_at=None, modo="OPERATIVO"):
    """Abre y puntua, en este proceso, sin la cola: la ejecucion terminada."""
    from motor_cartera.predictivo import scoring

    ejecucion_id = abrir_scoring(modelo_version_id, corte_id, known_at=known_at, modo=modo)
    scoring.puntuar(ejecucion_id)
    return scoring_de(ejecucion_id)


DISPARADORES_DEL_ENTRENAMIENTO = (
    ("ancla_entrenamiento_predictivo", "tr_predictivo_inmutable"),
    ("interpretacion_entrenamiento_predictivo", "tr_predictivo_inmutable"),
    ("componente_modelo_predictivo", "tr_predictivo_inmutable"),
    ("version_modelo_predictivo", "tr_modelo_predictivo_ciclo"),
    ("ejecucion_entrenamiento_predictivo", "tr_entrenamiento_predictivo_terminado"),
)
DISPARADORES_DEL_SCORING = (
    ("prediccion_cuenta", "tr_predictivo_inmutable"),
    ("drift_predictivo", "tr_predictivo_inmutable"),
    ("ejecucion_scoring_predictivo", "tr_scoring_predictivo_terminado"),
)


def _sin_disparadores(disparadores, sentencias, parametros) -> None:
    with sesion() as s:
        for tabla_, trigger in disparadores:
            s.execute(text(f"ALTER TABLE {tabla_} DISABLE TRIGGER {trigger}"))
        for sentencia in sentencias:
            s.execute(text(sentencia), parametros)
        for tabla_, trigger in disparadores:
            s.execute(text(f"ALTER TABLE {tabla_} ENABLE TRIGGER {trigger}"))
        s.commit()


def borrar_scoring(ejecucion_id: int) -> None:
    """Borra un scoring terminado y lo suyo, apagando un momento los disparadores que lo protegen:
    para reconstruirlo con las mismas entradas."""
    _sin_disparadores(
        DISPARADORES_DEL_SCORING,
        (
            "DELETE FROM prediccion_cuenta WHERE ejecucion_scoring_predictivo_id = :e",
            "DELETE FROM drift_predictivo WHERE ejecucion_scoring_predictivo_id = :e",
            "DELETE FROM trabajo_orquestacion WHERE ejecucion_scoring_predictivo_id = :e",
            "DELETE FROM ejecucion_scoring_predictivo WHERE id = :e",
        ),
        {"e": ejecucion_id},
    )


def borrar_entrenamiento(ejecucion_id: int) -> None:
    """Borra un entrenamiento terminado, su version y sus componentes (sin scorings), apagando un
    momento los disparadores que los protegen: para reconstruirlo con el mismo manifiesto."""
    _sin_disparadores(
        DISPARADORES_DEL_ENTRENAMIENTO,
        (
            "DELETE FROM componente_modelo_predictivo WHERE version_modelo_predictivo_id IN "
            "(SELECT id FROM version_modelo_predictivo "
            "WHERE ejecucion_entrenamiento_predictivo_id = :e)",
            "DELETE FROM version_modelo_predictivo "
            "WHERE ejecucion_entrenamiento_predictivo_id = :e",
            "DELETE FROM ancla_entrenamiento_predictivo "
            "WHERE ejecucion_entrenamiento_predictivo_id = :e",
            "DELETE FROM interpretacion_entrenamiento_predictivo "
            "WHERE ejecucion_entrenamiento_predictivo_id = :e",
            "DELETE FROM trabajo_orquestacion WHERE ejecucion_entrenamiento_predictivo_id = :e",
            "DELETE FROM ejecucion_entrenamiento_predictivo WHERE id = :e",
        ),
        {"e": ejecucion_id},
    )
