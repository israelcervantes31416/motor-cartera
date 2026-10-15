"""Propiedades de predictive/model-v1, sobre muchos casos aleatorios con semilla fija (sin base).

- las probabilidades publicadas estan en [0, 1] y el riesgo es su complemento exacto;
- el JSON canonico de un artefacto es estable: el orden de las llaves no cambia su hash;
- un split propuesto es temporal y respeta la purga, y uno que no la respeta se rechaza;
- el PSI nunca es negativo, y es cero entre una distribucion y ella misma;
- ningun label censurado entra a TRAIN;
- el SQL de las features no lee ninguna tabla de Decision, Campo, Ruteo ni Analytics.
"""

from __future__ import annotations

import json
import random
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import numpy as np
import pytest

from motor_cartera.predictivo import artefacto as artefacto_
from motor_cartera.predictivo import dataset as dataset_
from motor_cartera.predictivo import entrenamiento, glm, metricas, sql
from motor_cartera.predictivo import targets as targets_
from motor_cartera.predictivo.features import TABLAS_PROHIBIDAS
from motor_cartera.predictivo.politica import PUBLIC_V1
from motor_cartera.predictivo.targets import Madurez, Target

CASOS = 200


def test_las_probabilidades_publicadas_estan_en_rango_y_el_riesgo_es_exacto():
    rng = np.random.default_rng(2026)
    for _ in range(CASOS):
        eta = rng.normal(0, rng.uniform(0.1, 40), 1000)
        a, b = rng.normal(0, 2), rng.uniform(0.05, 3)
        for p in (glm.sigmoide(eta), glm.sigmoide(a + b * eta)):
            q = artefacto_.cuantizar_probabilidad(p, 8)
            assert q.min() >= 0 and q.max() <= 10**8
            assert np.all(q + (10**8 - q) == 10**8)
        q = artefacto_.cuantizar_probabilidad(glm.sigmoide(eta), 8)
        assert q.min() >= 0 and q.max() <= 10**8
        riesgo = 10**8 - q
        assert np.all(q + riesgo == 10**8)


def test_el_json_canonico_no_depende_del_orden_de_las_llaves():
    rng = random.Random(2026)
    for _ in range(CASOS):
        artefacto = {
            f"llave_{i}": rng.choice(
                [rng.random(), rng.randint(-5, 5), "texto", None, [1, 2.5], {"a": 1, "b": [0.1]}]
            )
            for i in range(rng.randint(1, 12))
        }
        llaves = list(artefacto)
        rng.shuffle(llaves)
        reordenado = {k: artefacto[k] for k in llaves}
        assert artefacto_.hash_artefacto(reordenado) == artefacto_.hash_artefacto(artefacto)
        assert json.loads(artefacto_.canonico(artefacto)) == artefacto


def test_el_psi_nunca_es_negativo():
    rng = np.random.default_rng(2026)
    for _ in range(CASOS):
        k = int(rng.integers(2, 15))
        t = rng.dirichlet(np.ones(k))
        s = rng.dirichlet(np.ones(k))
        if rng.random() < 0.3:
            s[rng.integers(0, k)] = 0.0
            s = s / s.sum()
        assert metricas.psi(t, s, PUBLIC_V1.epsilon_psi) >= 0.0
        assert metricas.psi(t, t, PUBLIC_V1.epsilon_psi) == 0.0


@pytest.mark.parametrize("semilla", range(25))
def test_un_split_propuesto_es_temporal_y_respeta_la_purga(semilla):
    """La regla de construir-manifiesto-predictivo, sobre cortes y coberturas al azar: TRAIN antes
    de VALIDATION antes de TEST, con la purga de la politica entre bloques."""
    from motor_cartera.predictivo.manifiesto import (
        AnclasInsuficientes,
        CorteCandidato,
        proponer,
    )

    rng = random.Random(semilla)
    inicio = date(2026, 1, 7)
    cortes = [
        CorteCandidato(uuid4(), inicio + timedelta(days=7 * i), datetime(2026, 1, 1, tzinfo=UTC))
        for i in range(rng.randint(8, 40))
    ]
    cobertura = cortes[-1].fecha + timedelta(days=rng.randint(0, 40))

    class SinBase:
        def execute(self, *_):
            class Resultado:
                def scalar_one_or_none(self):
                    return uuid4()

            return Resultado()

    try:
        propuesta = proponer(
            SinBase(),
            despacho_id="DSP_001",
            cartera_id="CARTERA_PRINCIPAL",
            zona="America/Mexico_City",
            cortes=cortes,
            label_known_at=datetime(2026, 12, 1, tzinfo=UTC),
            pagos_completos_hasta=cobertura,
            lifecycle_completo_hasta=cobertura,
            anclas_validation=rng.randint(1, 4),
            anclas_test=rng.randint(1, 4),
        )
    except AnclasInsuficientes:
        return
    por_split: dict[str, list[date]] = {}
    for a in propuesta["anclas"]:
        por_split.setdefault(a["split"], []).append(date.fromisoformat(a["feature_as_of"]))
    purga = timedelta(days=PUBLIC_V1.horizonte_purga_dias)
    assert max(por_split["TRAIN"]) + purga < min(por_split["VALIDATION"])
    assert max(por_split["VALIDATION"]) + purga < min(por_split["TEST"])
    for fecha in por_split["TEST"]:
        assert fecha + timedelta(days=targets_.HORIZONTE_MAXIMO) <= cobertura


def test_ningun_label_censurado_entra_a_train():
    """Sobre anclas con coberturas al azar, lo que entrena cada target son solo las anclas con su
    label MADURA, y un label que no madura no existe en el bloque."""
    rng = random.Random(2026)
    for _ in range(CASOS):
        bloques = []
        for i in range(rng.randint(1, 8)):
            fecha = date(2026, 3, 4) + timedelta(days=7 * i)
            pagos = fecha + timedelta(days=rng.randint(0, 45))
            lifecycle = fecha + timedelta(days=rng.randint(0, 45))
            ancla = dataset_.AnclaDataset(
                i, uuid4(), fecha, None, None, pagos, lifecycle, "TRAIN", "0" * 64
            )
            madurez = {t.target: ancla.madurez(t) for t in targets_.TARGETS}
            labels = {t: np.zeros(3) for t, m in madurez.items() if m == Madurez.MADURA}
            bloques.append(
                dataset_.BloqueAncla(
                    ancla,
                    np.zeros((3, 1)),
                    np.zeros((3, 1), dtype=np.int32),
                    np.arange(3, dtype=np.int32),
                    labels,
                    madurez,
                    {},
                    0.0,
                )
            )
        for target in Target:
            for b in entrenamiento._maduras(bloques, target):
                assert b.madurez[target] == Madurez.MADURA and target in b.labels


def test_el_sql_de_las_features_no_lee_decisiones_campo_ruteo_ni_analytics():
    """Golden 121 (las fuentes): ninguna tabla de una decision, un plan de campo, una ruta o un
    agregado aparece en el SQL de las features ni en el de los labels."""
    textos = [
        *sql.sql_universo(una_cuenta=False),
        *sql.SQL_HISTORIA,
        *sql.SQL_PAGOS,
        *sql.SQL_DATASETS_DE_PAGOS,
        sql.SQL_ALCANCES,
        sql.SQL_MESES,
        *sql.SQL_LIFECYCLE,
        *sql.SQL_FEATURES,
        *sql.SQL_LABELS,
        sql.sql_dataset(pagos=True, contacto=True, promesa=True),
        sql.SQL_SCORING,
    ]
    for texto in textos:
        for tabla in TABLAS_PROHIBIDAS:
            assert tabla not in texto, tabla
