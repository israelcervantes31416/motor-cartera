"""Las definiciones versionadas de predictive/model-v1 y sus metricas, sin base.

Las goldens: ninguna feature es un identificador, un dato personal, una coordenada ni la salida de
una decision (121); un label sin cobertura es CENSURADA o NO_DISPONIBLE, nunca un cero (116); el
baseline es la prevalencia de TRAIN (120); el PSI de dos distribuciones conocidas (126). Y las
metricas contra scikit-learn.
"""

from __future__ import annotations

import math
from datetime import date
from decimal import Decimal

import numpy as np
import pytest
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score

from motor_cartera.predictivo import features as features_
from motor_cartera.predictivo import metricas
from motor_cartera.predictivo import targets as targets_
from motor_cartera.predictivo.features import FEATURES, Clase, Familia, Tipo, Transformacion
from motor_cartera.predictivo.politica import (
    PUBLIC_V1,
    VERSION_MODELO,
    hash_politica,
    severidad_faltantes,
    severidad_psi,
)
from motor_cartera.predictivo.targets import Madurez, Target

# --- las versiones y sus hashes -------------------------------------------------------------------


def test_las_versiones_y_los_hashes_son_estables():
    """Cada definicion es codigo versionado: su hash es el de su forma canonica y no cambia entre
    llamadas. Cambiar una regla cambia el hash (y obliga a otra version)."""
    assert features_.VERSION_FEATURES == "predictive-features/public-v1"
    assert targets_.VERSION_TARGETS == "predictive-targets/public-v1"
    assert PUBLIC_V1.version == "predictive-training/public-v1"
    assert VERSION_MODELO == "predictive/model-v1"
    for hash_ in (features_.hash_features(), targets_.hash_targets(), hash_politica()):
        assert len(hash_) == 64 and int(hash_, 16) >= 0
    assert features_.hash_features() == features_.hash_features()
    from dataclasses import replace

    assert hash_politica(replace(PUBLIC_V1, grid_c=(0.1, 1.0))) != hash_politica()


def test_los_targets_y_el_riesgo_derivado():
    assert [t.target for t in targets_.TARGETS] == list(Target)
    assert targets_.CLASIFICADORES == (
        Target.RECUPERACION_POSITIVA_30D,
        Target.CONTACTO_14D,
        Target.PROMESA_30D,
    )
    assert targets_.POR_TARGET[Target.RECUPERACION_POSITIVA_MONTO_30D].salida == (
        "recuperacion_positiva_esperada_30d"
    )
    assert targets_.HORIZONTE_MAXIMO == PUBLIC_V1.horizonte_purga_dias == 30
    definicion = targets_.definicion()
    assert definicion["derivados"][0]["salida"] == "riesgo_sin_recuperacion_30d"
    assert "no es el efecto" in definicion["no_causal"]


# --- la madurez -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("target", "cobertura", "esperada"),
    [
        (Target.RECUPERACION_POSITIVA_30D, date(2026, 7, 31), Madurez.MADURA),
        (Target.RECUPERACION_POSITIVA_30D, date(2026, 7, 30), Madurez.CENSURADA),
        (Target.RECUPERACION_POSITIVA_30D, None, Madurez.NO_DISPONIBLE),
        (Target.CONTACTO_14D, date(2026, 7, 15), Madurez.MADURA),
        (Target.CONTACTO_14D, date(2026, 7, 14), Madurez.CENSURADA),
        (Target.PROMESA_30D, date(2026, 7, 31), Madurez.MADURA),
        (Target.PROMESA_30D, date(2026, 7, 1), Madurez.CENSURADA),
    ],
)
def test_la_madurez_depende_de_la_cobertura_declarada(target, cobertura, esperada):
    """Golden 116: T es el 2026-07-01; la ventana (T, T+H] necesita su fuente completa hasta T+H.
    Sin cobertura declarada no se sabe si la ausencia de hechos es un cero: NO_DISPONIBLE."""
    definicion = targets_.POR_TARGET[target]
    pagos = cobertura if definicion.fuente == targets_.Fuente.PAGOS else date(2026, 12, 31)
    lifecycle = cobertura if definicion.fuente == targets_.Fuente.LIFECYCLE else date(2026, 12, 31)
    assert (
        targets_.madurez(
            definicion,
            date(2026, 7, 1),
            pagos_completos_hasta=pagos,
            lifecycle_completo_hasta=lifecycle,
        )
        == esperada
    )


# --- las features ---------------------------------------------------------------------------------


def test_ninguna_feature_es_prohibida():
    """Golden 121: el esquema no tiene un identificador, un dato personal o sensible, una
    coordenada, la estrategia o el canal declarados, ni una salida de decision/v2, campo/v2 o
    ruteo/v2; y la guarda rechaza cualquiera que se agregue."""
    assert features_.problemas_del_esquema() == []
    features_.validar()
    nombres = {f.nombre for f in FEATURES}
    for prohibido in (
        "cliente_unico",
        "cuenta_id",
        "nombre",
        "telefono",
        "direccion",
        "latitud",
        "longitud",
        "cve_municipio",
        "estrategia",
        "canal",
        "sexo",
        "edad",
        "curp",
        "rfc",
        "accion_recomendada",
        "gestor",
        "microtramo",
    ):
        assert prohibido not in nombres
    for f in FEATURES:
        assert f.familia in features_.FAMILIAS_PERMITIDAS
    malo = features_._f(
        "telefono_del_aval",
        Familia.SNAPSHOT,
        Clase.CATEGORICA,
        Tipo.TEXTO,
        Transformacion.NINGUNA,
        "al corte (T)",
        "snapshot_cuenta",
        "Un dato personal: nunca es una feature.",
    )
    assert features_.problemas_del_esquema((*FEATURES, malo))
    with pytest.raises(features_.EsquemaInvalido):
        features_.validar((*FEATURES, malo))


def test_la_forma_canonica_y_el_feature_hash():
    """La forma canonica: la version, y una linea nombre=valor por feature en su orden, con su
    precision y NULL como ~; el feature_hash es su SHA-256. Un -0.00 es 0.00."""
    valores = {f.nombre: None for f in FEATURES if f.nula}
    for f in FEATURES:
        if f.nula:
            continue
        valores[f.nombre] = (
            "CONSUMO"
            if f.tipo == Tipo.TEXTO
            else (3 if f.tipo == Tipo.ENTERO else Decimal("-0.001"))
        )
    texto = features_.forma_canonica(valores)
    lineas = texto.split("\n")
    assert lineas[0] == features_.VERSION_FEATURES and len(lineas) == len(FEATURES) + 1
    assert "saldo_total=0.00" in lineas and "producto=s7:CONSUMO" in lineas
    assert "saldo=~" in lineas
    assert len(features_.feature_hash(valores)) == 32
    with pytest.raises(features_.ValorInvalido):
        features_.forma_canonica({**valores, "dias_atraso": None})
    with pytest.raises(features_.ValorInvalido):
        features_.forma_canonica({**valores, "extra": 1})


# --- las metricas ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def muestra():
    rng = np.random.default_rng(2026)
    p = np.round(rng.beta(2, 5, 5000), 3)  # con empates
    y = (rng.random(5000) < p).astype(float)
    return y, p


def test_las_metricas_son_las_de_scikit_learn(muestra):
    y, p = muestra
    assert metricas.roc_auc(y, p) == pytest.approx(roc_auc_score(y, p), abs=1e-12)
    assert metricas.pr_auc(y, p) == pytest.approx(average_precision_score(y, p), abs=1e-12)
    assert metricas.log_loss(y, p) == pytest.approx(log_loss(y, p), abs=1e-12)
    assert metricas.brier(y, p) == pytest.approx(brier_score_loss(y, p), abs=1e-12)
    pesos = np.random.default_rng(1).integers(0, 3, len(y)).astype(float)
    assert metricas.roc_auc(y, p, pesos) == pytest.approx(
        roc_auc_score(y, p, sample_weight=pesos), abs=1e-12
    )
    assert metricas.roc_auc(np.ones(3), np.array([0.1, 0.2, 0.3])) is None


def test_el_baseline_es_la_prevalencia_de_train(muestra):
    """Golden 120: una probabilidad constante igual a la prevalencia tiene ROC AUC 0.5, lift 1 y
    la log loss de la entropia de la prevalencia; ninguna metrica usa la exactitud."""
    y, _ = muestra
    prevalencia = float(np.mean(y))
    constante = np.full(len(y), prevalencia)
    m = metricas.clasificacion(y, constante, bins=10, fracciones=(0.1, 0.2))
    assert m["roc_auc"] == 0.5
    entropia = -(prevalencia * math.log(prevalencia) + (1 - prevalencia) * math.log1p(-prevalencia))
    assert m["log_loss"] == pytest.approx(entropia, abs=1e-12)
    assert "accuracy" not in m and "exactitud" not in m


def test_lift_captura_ece_y_calibracion(muestra):
    y, p = muestra
    lift, captura = metricas.lift_y_captura(y, p, 0.1)
    arriba = np.argsort(-p, kind="mergesort")[: math.ceil(0.1 * len(y))]
    assert captura == pytest.approx(y[arriba].sum() / y.sum())
    assert lift == pytest.approx(y[arriba].mean() / y.mean())
    tabla = metricas.tabla_calibracion(y, p, 10)
    assert len(tabla) == 10 and sum(f["n"] for f in tabla) == len(y)
    assert metricas.ece(y, p, 10) == pytest.approx(
        sum(f["n"] / len(y) * abs(f["prediccion_media"] - f["frecuencia_observada"]) for f in tabla)
    )
    intercepto, pendiente = metricas.calibracion_intercepto_pendiente(y, p)
    assert abs(intercepto) < 0.1 and abs(pendiente - 1) < 0.15


def test_el_bootstrap_por_cuenta_es_determinista(muestra):
    y, p = muestra
    cuentas = np.arange(len(y)) // 2  # dos filas por cuenta
    estadisticos = {"roc_auc": lambda w: metricas.roc_auc(y, p, w)}
    uno = metricas.bootstrap(cuentas, estadisticos, repeticiones=50, semilla=2026, nivel=0.95)
    otro = metricas.bootstrap(cuentas, estadisticos, repeticiones=50, semilla=2026, nivel=0.95)
    assert uno == otro
    i = uno["roc_auc"]
    assert i.inferior < i.estimado < i.superior


def test_el_psi_de_distribuciones_conocidas():
    """Golden 126: la misma distribucion tiene PSI 0; dos conocidas tienen exactamente
    sum((s - t) * ln(s / t)); una proporcion cero se lleva a epsilon. Las severidades son las de
    la politica."""
    t = np.array([0.25, 0.25, 0.25, 0.25])
    assert metricas.psi(t, t, 1e-4) == 0.0
    s = np.array([0.10, 0.20, 0.30, 0.40])
    esperado = sum((b - a) * math.log(b / a) for a, b in zip(t, s, strict=True))
    assert metricas.psi(t, s, 1e-4) == pytest.approx(esperado, abs=1e-15)
    assert metricas.psi(
        np.array([0.5, 0.5, 0.0]), np.array([0.5, 0.4, 0.1]), 1e-4
    ) == pytest.approx(
        (0.4 - 0.5) * math.log(0.4 / 0.5) + (0.1 - 1e-4) * math.log(0.1 / 1e-4), abs=1e-12
    )
    bordes = metricas.bordes(np.arange(100.0), 10)
    assert len(bordes) == 9
    assert list(metricas.proporciones(np.arange(100.0), bordes)) == [0.1] * 10
    assert severidad_psi(0.0999) == "BAJO" and severidad_psi(0.10) == "MODERADO"
    assert severidad_psi(0.25) == "MODERADO" and severidad_psi(0.2501) == "ALTO"
    assert severidad_faltantes(-0.06) == "MODERADO" and severidad_faltantes(0.21) == "ALTO"


def test_las_reglas_no_hablan_de_causalidad_ni_de_rebalanceo():
    """Ninguna regla, definicion o aviso promete un efecto causal; ninguna rebalancea clases."""
    textos = " ".join(
        [
            str(PUBLIC_V1.definicion()),
            str(targets_.definicion()),
            str(features_.definicion()),
        ]
    ).lower()
    for prohibido in ("uplift", "impacto causal", "incremento causado", "tratamiento optimo"):
        assert prohibido not in textos
    assert PUBLIC_V1.definicion()["clasificacion"]["rebalanceo"].startswith("ninguno")
