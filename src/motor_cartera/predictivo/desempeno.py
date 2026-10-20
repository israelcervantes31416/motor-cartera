"""El desempeno de un scoring publicado, cuando sus labels ya maduraron: el drift de desempeno.

Se construyen los labels de las cuentas del corte del scoring con la misma regla del
entrenamiento (predictive-targets/public-v1): la ventana (T, T+H], lo registrado hasta
label_known_at, las interpretaciones de pagos vigentes a label_known_at y la cobertura declarada;
un label que no madura no se evalua y nunca es un cero. Se comparan con las predicciones
publicadas (las cuantizadas, las mismas que vio quien las uso) y con las metricas de TEST del
componente.

Solo lee: no guarda nada, no cambia, no recalibra ni reentrena el modelo. Si el desempeno cayo, lo
que sigue es otro entrenamiento, con otra version, decidido por una persona.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from uuid import UUID

import numpy as np
import pyarrow as pa
from sqlalchemy import text
from sqlmodel import col, select

from motor_cartera.db.modelos import (
    ComponenteModeloPredictivo,
    EjecucionMotorPagos,
    EjecucionScoringPredictivo,
    EstadoScoringPredictivo,
    VersionModeloPredictivo,
    id_de,
)
from motor_cartera.db.sesion import sesion
from motor_cartera.motor_pagos.reglas import VERSION_MOTOR_PAGOS, periodos_entre
from motor_cartera.predictivo import construccion, manifiesto, metricas
from motor_cartera.predictivo import targets as targets_
from motor_cartera.predictivo.politica import POLITICAS
from motor_cartera.predictivo.targets import Madurez, Target, TipoTarget

AVISO = (
    "Esta evaluacion describe el desempeno de lo publicado con los labels que ya maduraron. No "
    "cambia, no recalibra ni reentrena el modelo: cambiar el modelo es otro entrenamiento."
)


class EvaluacionInvalida(Exception):
    codigo = "EVALUACION_INVALIDA"


def _metricas_clasificacion(y: np.ndarray, p: np.ndarray, politica) -> dict:
    return metricas.clasificacion(
        y, p, bins=politica.bins_calibracion, fracciones=politica.fracciones_lift
    )


def evaluar(
    scoring_run_id: UUID,
    *,
    label_known_at: datetime | None,
    pagos_completos_hasta: date | None,
    lifecycle_completo_hasta: date | None,
    interpretaciones: list[UUID] | None = None,
) -> dict:
    """El desempeno del scoring con los labels maduros a label_known_at (por omision, el reloj de
    la base). Sin `interpretaciones`, usa la vigente a label_known_at de cada mes que toca la
    ventana de pagos, y dice cuales."""
    with sesion() as s:
        s.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        e = s.exec(
            select(EjecucionScoringPredictivo).where(
                EjecucionScoringPredictivo.scoring_run_id == scoring_run_id
            )
        ).first()
        if e is None or e.estado != EstadoScoringPredictivo.EXITOSA:
            raise EvaluacionInvalida(f"No hay un scoring EXITOSA {scoring_run_id}.")
        reloj = s.execute(text("SELECT now()")).scalar_one()
        conocido = label_known_at or reloj
        if conocido.tzinfo is None or conocido > reloj:
            raise EvaluacionInvalida("label_known_at lleva su zona y no es posterior a la base.")
        version = s.get_one(VersionModeloPredictivo, e.version_modelo_predictivo_id)
        politica = POLITICAS[version.version_politica]
        componentes = {
            Target(c.target): c
            for c in s.exec(
                select(ComponenteModeloPredictivo).where(
                    ComponenteModeloPredictivo.version_modelo_predictivo_id == version.id
                )
            ).all()
        }
        dia = s.execute(
            text("SELECT (CAST(:k AS timestamptz) AT TIME ZONE :z)::date"),
            {"k": conocido, "z": e.zona_horaria},
        ).scalar_one()
        for nombre, marca in (
            ("pagos_completos_hasta", pagos_completos_hasta),
            ("lifecycle_completo_hasta", lifecycle_completo_hasta),
        ):
            if marca is not None and marca > dia:
                raise EvaluacionInvalida(f"{nombre} ({marca}) es posterior a label_known_at.")
        madurez = {
            t.target: targets_.madurez(
                t,
                e.as_of,
                pagos_completos_hasta=pagos_completos_hasta,
                lifecycle_completo_hasta=lifecycle_completo_hasta,
            )
            for t in targets_.TARGETS
        }
        meses = periodos_entre(
            e.as_of + timedelta(days=1), e.as_of + timedelta(days=targets_.HORIZONTE_MAXIMO)
        )
        usadas = _interpretaciones(
            s, e, meses, conocido, interpretaciones, madurez[Target.RECUPERACION_POSITIVA_30D]
        )
        relojes = construccion.Relojes(
            e.corte_canonico_id, e.despacho_id, e.cartera_id, e.as_of, e.known_at, e.zona_horaria
        )
        construccion.preparar_universo(s, relojes)
        construccion.preparar_labels(
            s, relojes, label_known_at=conocido, interpretaciones=[id_de(u) for u in usadas]
        )
        csv = construccion.copiar(
            s,
            "SELECT p.p_recuperacion_positiva_30d, p.p_contacto_14d, p.p_promesa_30d, "
            "p.recuperacion_positiva_esperada_30d, coalesce(lm.neta, 0) AS neta, "
            "CAST(lc.cuenta IS NOT NULL AS integer) AS contacto, "
            "CAST(lp.cuenta IS NOT NULL AS integer) AS promesa "
            "FROM prediccion_cuenta p JOIN pf_universo u ON u.cuenta = p.cuenta_canonica_id "
            "LEFT JOIN pl_mov lm ON lm.cuenta = u.cuenta "
            "LEFT JOIN pl_contacto lc ON lc.cuenta = u.cuenta "
            "LEFT JOIN pl_promesa lp ON lp.cuenta = u.cuenta "
            f"WHERE p.ejecucion_scoring_predictivo_id = {int(id_de(e))} ORDER BY u.cuenta_id",
        )
        s.rollback()
        resumen = {
            "scoring_run_id": str(e.scoring_run_id),
            "modelo_version_id": str(version.modelo_version_id),
            "as_of": e.as_of.isoformat(),
            "known_at": e.known_at.isoformat(),
            "label_known_at": conocido.isoformat(),
            "pagos_completos_hasta": None
            if pagos_completos_hasta is None
            else pagos_completos_hasta.isoformat(),
            "lifecycle_completo_hasta": None
            if lifecycle_completo_hasta is None
            else lifecycle_completo_hasta.isoformat(),
            "interpretaciones_pagos": [str(u.motor_pagos_run_id) for u in usadas],
        }
        test = {t: c.metricas_test for t, c in componentes.items()}
    tabla = construccion.tabla(
        csv,
        {
            "p_recuperacion_positiva_30d": pa.float64(),
            "p_contacto_14d": pa.float64(),
            "p_promesa_30d": pa.float64(),
            "recuperacion_positiva_esperada_30d": pa.float64(),
            "neta": pa.float64(),
            "contacto": pa.float64(),
            "promesa": pa.float64(),
        },
    )
    columna = {n: construccion.flotantes(tabla.column(n)) for n in tabla.column_names}
    labels = {
        Target.RECUPERACION_POSITIVA_30D: (columna["neta"] > 0).astype(np.float64),
        Target.RECUPERACION_POSITIVA_MONTO_30D: np.maximum(columna["neta"], 0.0),
        Target.CONTACTO_14D: columna["contacto"],
        Target.PROMESA_30D: columna["promesa"],
    }
    resultado = []
    for t in targets_.TARGETS:
        fila = {"target": t.target.value, "salida": t.salida, "madurez": madurez[t.target].value}
        if madurez[t.target] != Madurez.MADURA:
            resultado.append({**fila, "estado": "NO_EVALUADO"})
            continue
        y, prediccion = labels[t.target], columna[t.salida]
        if t.tipo == TipoTarget.BINARIO:
            actual = _metricas_clasificacion(y, prediccion, politica)
            comparables = ("roc_auc", "pr_auc", "log_loss", "brier", "ece", "prevalencia")
        else:
            actual = metricas.monto(y, prediccion, bins=politica.bins_calibracion)
            comparables = ("mae", "rmse", "real_medio", "predicho_medio", "razon_sumas")
        en_test = test[t.target]
        resultado.append(
            {
                **fila,
                "estado": "EVALUADO",
                "metricas": {k: v for k, v in actual.items() if not isinstance(v, list | dict)},
                "metricas_test": {k: en_test.get(k) for k in comparables},
                "diferencias": {
                    k: None
                    if actual.get(k) is None or en_test.get(k) is None
                    else actual[k] - en_test[k]
                    for k in comparables
                },
            }
        )
    return {**resumen, "cuentas": tabla.num_rows, "componentes": resultado, "aviso": AVISO}


def _interpretaciones(
    s,
    e: EjecucionScoringPredictivo,
    meses: list[date],
    conocido: datetime,
    declaradas: list[UUID] | None,
    madurez_pagos: Madurez,
) -> list[EjecucionMotorPagos]:
    if madurez_pagos != Madurez.MADURA:
        return []
    if declaradas is None:
        usadas = []
        for mes in meses:
            vigente = s.exec(
                select(EjecucionMotorPagos)
                .where(
                    EjecucionMotorPagos.despacho_id == e.despacho_id,
                    EjecucionMotorPagos.cartera_id == e.cartera_id,
                    EjecucionMotorPagos.version_motor == VERSION_MOTOR_PAGOS,
                    EjecucionMotorPagos.periodo_desde == mes,
                    EjecucionMotorPagos.estado == "EXITOSA",
                    col(EjecucionMotorPagos.terminada_en) <= conocido,
                )
                .order_by(col(EjecucionMotorPagos.id).desc())
                .limit(1)
            ).first()
            if vigente is None:
                raise EvaluacionInvalida(f"El mes {mes:%Y-%m} no tiene interpretacion EXITOSA.")
            usadas.append(vigente)
    else:
        usadas = [
            s.exec(
                select(EjecucionMotorPagos).where(EjecucionMotorPagos.motor_pagos_run_id == u)
            ).one()
            for u in declaradas
        ]
        if sorted(u.periodo_desde for u in usadas) != meses:
            raise EvaluacionInvalida(
                "Las interpretaciones declaradas no son exactamente una por cada mes de la "
                f"ventana: {', '.join(f'{m:%Y-%m}' for m in meses)}."
            )
    for u in usadas:
        try:
            manifiesto.comprobar_vigente(s, u, conocido)
            manifiesto.comprobar_completa(s, u, conocido, e.despacho_id, e.cartera_id)
        except manifiesto.ManifiestoInvalido as exc:
            raise EvaluacionInvalida(str(exc)) from exc
    return usadas
