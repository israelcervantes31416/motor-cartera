"""Los GLM de predictive/model-v1, su preprocesamiento, su artefacto JSON y su runtime, sin base.

Las goldens: la regresion logistica y el Tweedie propios dan lo mismo que scikit-learn con el
mismo objetivo (124); el runtime que lee solo el JSON predice lo mismo que el estimador de
entrenamiento antes de cuantizar, y exactamente lo mismo despues (124); la imputacion usa la
mediana de TRAIN y su indicador (117); una categoria que TRAIN no vio es __UNKNOWN__ (118); Platt
se ajusta en VALIDATION y solo mejora la calibracion (119); cambiar un coeficiente cambia el hash
del artefacto (123); las contribuciones suman exactamente el predictor lineal (125).
"""

from __future__ import annotations

import copy
import json

import numpy as np
import pytest
from sklearn.linear_model import LogisticRegression, TweedieRegressor

from motor_cartera.predictivo import artefacto as artefacto_
from motor_cartera.predictivo import glm
from motor_cartera.predictivo import preprocesamiento as pre_
from motor_cartera.predictivo.features import (
    CATEGORICAS,
    DESCONOCIDA,
    FALTANTE,
    NUMERICAS,
    Transformacion,
)
from motor_cartera.predictivo.politica import PUBLIC_V1

SEMILLA = 2026


def _datos(n: int = 3000, semilla: int = SEMILLA):
    """Unas features crudas con la forma de predictive-features/public-v1: numericas con NULL,
    sesgadas y con signo, y categoricas; y dos targets con senal declarada."""
    rng = np.random.default_rng(semilla)
    p = len(NUMERICAS)
    numericas = np.empty((n, p))
    for j, f in enumerate(NUMERICAS):
        if f.transformacion == Transformacion.LOG1P:
            columna = rng.lognormal(6, 1.5, n)
        elif f.transformacion == Transformacion.SIGNED_LOG1P:
            columna = rng.normal(0, 2000, n)
        else:
            columna = rng.integers(0, 120, n).astype(float)
        if f.nula:
            columna[rng.random(n) < 0.15] = np.nan
        numericas[:, j] = columna
    valores = {
        "producto": ["CONSUMO", "NOMINA", "TARJETA", "AUTOMOTRIZ"],
        "cve_entidad": ["09", "15", "21", "29", "30"],
        "tramo_atraso": ["0", "1-30", "31-60", "61-90", "91+"],
    }
    categoricas = np.empty((n, len(CATEGORICAS)), dtype=object)
    for k, f in enumerate(CATEGORICAS):
        categoricas[:, k] = rng.choice(valores[f.nombre], n)
    x = np.nan_to_num(numericas[:, :4], nan=0.0)
    eta = -0.8 + 0.4 * np.log1p(x[:, 0]) / 6 - 0.3 * (categoricas[:, 0] == "TARJETA")
    eta = eta + 0.5 * np.isnan(numericas[:, 1])
    binario = (rng.random(n) < glm.sigmoide(eta)).astype(float)
    monto = np.where(rng.random(n) < glm.sigmoide(eta), rng.gamma(2.0, 400.0 * np.exp(eta)), 0.0)
    return numericas, categoricas, binario, monto


@pytest.fixture(scope="module")
def datos():
    return _datos()


@pytest.fixture(scope="module")
def pre(datos):
    numericas, categoricas, _, _ = datos
    return pre_.ajustar(numericas, categoricas, cifras=PUBLIC_V1.cifras_significativas)


# --- el preprocesamiento --------------------------------------------------------------------------


def test_la_imputacion_usa_la_mediana_de_train_y_su_indicador(datos, pre):
    """Golden 117: un NULL se imputa con la mediana de TRAIN del valor transformado, y su
    indicador vale 1; la estandarizacion usa la media y la desviacion de TRAIN."""
    numericas, categoricas, _, _ = datos
    j = next(j for j, f in enumerate(NUMERICAS) if f.indicador_nulo)
    num = pre.numericas[j]
    transformados = pre_.transformar(num.transformacion, numericas[:, j])
    assert num.mediana == pre_.redondear(float(np.nanmedian(transformados)), 12)
    fila = np.full((1, len(NUMERICAS)), np.nan)
    x = pre.disenar(fila, np.array([[None, None, None]], dtype=object))
    assert x[0, j] == pytest.approx((num.mediana - num.media) / num.desviacion, abs=1e-15)
    indicador = pre.columnas.index(f"{num.nombre}__nulo")
    assert x[0, indicador] == 1.0
    assert x[0, pre.columnas.index(f"{CATEGORICAS[0].nombre}={FALTANTE}")] == 1.0


def test_las_estadisticas_salen_solo_de_train(datos, pre):
    """Otras filas (VALIDATION, TEST, un scoring) no cambian la mediana, la media, la desviacion
    ni el vocabulario: el preprocesamiento es el de TRAIN."""
    numericas, categoricas, _, _ = datos
    otro = _datos(n=500, semilla=7)
    x_antes = pre.disenar(otro[0], otro[1])
    mezclado = pre_.ajustar(
        np.vstack([numericas, otro[0] * 10]),
        np.vstack([categoricas, otro[1]]),
        cifras=12,
    )
    assert mezclado != pre
    assert np.array_equal(pre.disenar(otro[0], otro[1]), x_antes)


def test_una_categoria_que_train_no_vio_es_desconocida(pre):
    """Golden 118: ni una columna nueva ni un error: __UNKNOWN__, con coeficiente fijo en cero
    (nunca se vio en TRAIN)."""
    vocabulario = pre.categoricas[0].vocabulario
    assert vocabulario[-2:] == (FALTANTE, DESCONOCIDA)
    indices = pre_.codificar(vocabulario, np.array(["HIPOTECARIO", None, vocabulario[0]], object))
    assert list(indices) == [len(vocabulario) - 1, len(vocabulario) - 2, 0]
    columna = pre.columnas.index(f"{CATEGORICAS[0].nombre}={DESCONOCIDA}")
    assert not pre.libres[columna]


def test_las_transformaciones_sesgadas_y_con_signo():
    x = np.array([-5.0, 0.0, 3.0, np.nan])
    assert np.allclose(pre_.transformar("LOG1P", x)[:3], [0.0, 0.0, np.log1p(3.0)], equal_nan=True)
    assert np.allclose(
        pre_.transformar("SIGNED_LOG1P", x)[:3], [-np.log1p(5.0), 0.0, np.log1p(3.0)]
    )
    assert np.isnan(pre_.transformar("LOG1P", x)[3])
    with pytest.raises(ValueError):
        pre_.transformar("CUADRADO", x)


def test_una_columna_constante_queda_fija_en_cero():
    numericas, categoricas, binario, _ = _datos(n=800)
    numericas[:, 0] = 5.0
    pre = pre_.ajustar(numericas, categoricas, cifras=12)
    assert pre.numericas[0].constante and not pre.libres[0]
    x = pre.disenar(numericas, categoricas)
    ajuste = glm.ajustar_logistica(glm.bloques_de(x, binario), x.shape[1], c=1.0, libres=pre.libres)
    assert ajuste.coeficientes[0] == 0.0 and ajuste.convergio


# --- los GLM contra scikit-learn ------------------------------------------------------------------


@pytest.mark.parametrize("c", PUBLIC_V1.grid_c)
def test_la_logistica_es_la_de_scikit_learn(datos, pre, c):
    """Golden 124 (estimador): el mismo objetivo, C * suma de la perdida logistica + ||w||^2 / 2
    sin penalizar el intercepto, da los mismos coeficientes."""
    numericas, categoricas, binario, _ = datos
    x = pre.disenar(numericas, categoricas)[:, pre.libres]
    propio = glm.ajustar_logistica(glm.bloques_de(x, binario, 512), x.shape[1], c=c)
    sk = LogisticRegression(C=c, tol=1e-12, max_iter=10_000, solver="newton-cholesky")
    sk.fit(x, binario)
    assert propio.convergio
    assert np.max(np.abs(propio.coeficientes - sk.coef_[0])) < 1e-6
    assert abs(propio.intercepto - sk.intercept_[0]) < 1e-6
    assert (
        np.max(np.abs(glm.sigmoide(glm.predictor_lineal(x, propio)) - sk.predict_proba(x)[:, 1]))
        < 1e-7
    )


@pytest.mark.parametrize("alpha", PUBLIC_V1.grid_alpha)
def test_el_tweedie_es_el_de_scikit_learn(datos, pre, alpha):
    numericas, categoricas, _, monto = datos
    x = pre.disenar(numericas, categoricas)[:, pre.libres]
    propio = glm.ajustar_tweedie(
        glm.bloques_de(x, monto, 700), x.shape[1], alpha=alpha, potencia=1.5
    )
    sk = TweedieRegressor(
        power=1.5, alpha=alpha, link="log", tol=1e-12, max_iter=10_000, solver="newton-cholesky"
    )
    sk.fit(x, monto)
    assert propio.convergio
    mu = np.exp(glm.predictor_lineal(x, propio))
    assert np.max(np.abs(mu - sk.predict(x)) / np.maximum(sk.predict(x), 1.0)) < 1e-6


def test_el_ajuste_es_determinista_y_no_depende_del_tamano_del_bloque(datos, pre):
    numericas, categoricas, binario, _ = datos
    x = pre.disenar(numericas, categoricas)
    una = glm.ajustar_logistica(
        glm.bloques_de(x, binario, 1000), x.shape[1], c=1.0, libres=pre.libres
    )
    otra = glm.ajustar_logistica(
        glm.bloques_de(x, binario, 1000), x.shape[1], c=1.0, libres=pre.libres
    )
    assert np.array_equal(una.coeficientes, otra.coeficientes)
    assert una.intercepto == otra.intercepto
    distinta = glm.ajustar_logistica(
        glm.bloques_de(x, binario, 333), x.shape[1], c=1.0, libres=pre.libres
    )
    assert np.max(np.abs(una.coeficientes - distinta.coeficientes)) < 1e-9


def test_platt_es_la_logistica_sin_regularizar_del_logit():
    """Golden 119: Platt ajusta logit(p) = a + b * eta por maxima verosimilitud; con
    probabilidades bien calibradas, a es casi 0 y b casi 1; con unas sobreconfiadas, b < 1 y su
    Brier baja."""
    rng = np.random.default_rng(SEMILLA)
    eta = rng.normal(0, 1.5, 20_000)
    y = (rng.random(len(eta)) < glm.sigmoide(eta)).astype(float)
    a, b = glm.platt(eta, y)
    assert abs(a) < 0.05 and abs(b - 1) < 0.05
    sobreconfiado = 2.5 * eta
    a2, b2 = glm.platt(sobreconfiado, y)
    assert b2 < 0.6
    from motor_cartera.predictivo import metricas

    crudo = metricas.brier(y, glm.sigmoide(sobreconfiado))
    calibrado = metricas.brier(y, glm.sigmoide(a2 + b2 * sobreconfiado))
    assert calibrado < crudo
    sk = LogisticRegression(C=1e12, tol=1e-12, max_iter=10_000, solver="newton-cholesky")
    sk.fit(sobreconfiado.reshape(-1, 1), y)
    assert abs(b2 - sk.coef_[0][0]) < 1e-5 and abs(a2 - sk.intercept_[0]) < 1e-5


# --- el artefacto y su runtime --------------------------------------------------------------------


def _artefacto(datos, pre, *, calibracion=None, monto=False):
    numericas, categoricas, binario, montos = datos
    x = pre.disenar(numericas, categoricas)
    if monto:
        ajuste = glm.ajustar_tweedie(
            glm.bloques_de(x, montos), x.shape[1], alpha=0.001, potencia=1.5, libres=pre.libres
        )
    else:
        ajuste = glm.ajustar_logistica(
            glm.bloques_de(x, binario), x.shape[1], c=1.0, libres=pre.libres
        )
    redondeado = glm.Ajuste(
        np.array([pre_.redondear(float(c), 12) for c in ajuste.coeficientes]),
        pre_.redondear(ajuste.intercepto, 12),
        ajuste.iteraciones,
        ajuste.convergio,
        ajuste.objetivo,
    )
    artefacto = artefacto_.construir(
        target="RECUPERACION_POSITIVA_MONTO_30D" if monto else "RECUPERACION_POSITIVA_30D",
        algoritmo=artefacto_.TWEEDIE if monto else artefacto_.LOGISTICA,
        preprocesamiento=pre,
        ajuste=redondeado,
        hiperparametros={"alpha": 0.001} if monto else {"c": 1.0},
        calibracion=calibracion,
        cifras=12,
        decimales_probabilidad=8,
        decimales_monto=2,
    )
    return artefacto, x, redondeado


def test_el_runtime_del_json_predice_lo_del_estimador(datos, pre):
    """Golden 124 (runtime): solo con el JSON (recargado de su texto), la prediccion es la del
    estimador de entrenamiento hasta el redondeo del float64, y la cuantizada es identica."""
    numericas, categoricas, _, _ = datos
    for monto in (False, True):
        artefacto, x, ajuste = _artefacto(datos, pre, monto=monto)
        runtime = artefacto_.Runtime(json.loads(artefacto_.canonico(artefacto)))
        prediccion = runtime.predecir(numericas, categoricas)
        eta = glm.predictor_lineal(x, ajuste)
        assert np.max(np.abs(prediccion.eta - eta)) < 1e-12
        if monto:
            esperado = artefacto_.cuantizar_monto(np.exp(eta), 2)
            assert np.array_equal(artefacto_.cuantizar_monto(prediccion.publicada, 2), esperado)
        else:
            esperado = artefacto_.cuantizar_probabilidad(glm.sigmoide(eta), 8)
            assert np.array_equal(
                artefacto_.cuantizar_probabilidad(prediccion.publicada, 8), esperado
            )


def test_el_runtime_y_scikit_learn_coinciden(datos, pre):
    """Golden 124 (runtime contra scikit-learn): el runtime del JSON, entrenado con el GLM propio,
    contra scikit-learn ajustado sobre la misma matriz de diseno."""
    numericas, categoricas, binario, _ = datos
    artefacto, x, _ = _artefacto(datos, pre)
    sk = LogisticRegression(C=1.0, tol=1e-12, max_iter=10_000, solver="newton-cholesky")
    sk.fit(x[:, pre.libres], binario)
    runtime = artefacto_.Runtime(artefacto)
    p = runtime.predecir(numericas, categoricas).publicada
    assert np.max(np.abs(p - sk.predict_proba(x[:, pre.libres])[:, 1])) < 1e-7


def test_las_contribuciones_suman_el_predictor_lineal(datos, pre):
    """Golden 125: el intercepto mas la suma de las contribuciones de cada feature es el
    predictor lineal, fila por fila; la calibracion de Platt no cambia el signo de ninguna."""
    numericas, categoricas, _, _ = datos
    artefacto, _, _ = _artefacto(datos, pre, calibracion=(0.1, 0.9))
    runtime = artefacto_.Runtime(artefacto)
    aportes = runtime.contribuciones(numericas[:50], categoricas[:50])
    assert set(aportes) == {f.nombre for f in NUMERICAS} | {f.nombre for f in CATEGORICAS}
    suma = runtime.intercepto + np.sum(np.vstack(list(aportes.values())), axis=0)
    prediccion = runtime.predecir(numericas[:50], categoricas[:50])
    assert np.max(np.abs(suma - prediccion.eta)) < 1e-12
    assert np.allclose(prediccion.publicada, glm.sigmoide(0.1 + 0.9 * prediccion.eta))


def test_cambiar_un_coeficiente_cambia_el_hash(datos, pre):
    """Golden 123: el hash es el SHA-256 del JSON canonico; cualquier numero lo cambia, el orden
    de las llaves no."""
    artefacto, _, _ = _artefacto(datos, pre)
    original = artefacto_.hash_artefacto(artefacto)
    reordenado = json.loads(json.dumps(artefacto, sort_keys=False))
    assert artefacto_.hash_artefacto(dict(reversed(list(reordenado.items())))) == original
    otro = copy.deepcopy(artefacto)
    otro["coeficientes"][3] = pre_.redondear(otro["coeficientes"][3] + 1e-9, 12)
    assert artefacto_.hash_artefacto(otro) != original
    with pytest.raises(ValueError):
        artefacto_.canonico({**artefacto, "intercepto": float("nan")})
    assert "pickle" not in artefacto_.canonico(artefacto).decode()


def test_un_formato_desconocido_no_se_lee(datos, pre):
    artefacto, _, _ = _artefacto(datos, pre)
    with pytest.raises(ValueError):
        artefacto_.Runtime({**artefacto, "formato": "predictive/artifact-v0"})


def test_la_cuantizacion_y_el_riesgo_exacto():
    """La probabilidad se guarda en unidades de 1e-8 (redondeo al par) y el riesgo es su
    complemento exacto; el monto, en centavos y nunca negativo."""
    p = np.array([0.0, 0.123456784, 0.123456786, 1.0, 0.5])
    q = artefacto_.cuantizar_probabilidad(p, 8)
    assert list(q) == [0, 12345678, 12345679, 100_000_000, 50_000_000]
    riesgo = 100_000_000 - q
    assert [artefacto_.texto_decimal(int(x), 8) for x in q[:2]] == ["0.00000000", "0.12345678"]
    assert artefacto_.texto_decimal(int(riesgo[1]), 8) == "0.87654322"
    assert all(int(a) + int(b) == 100_000_000 for a, b in zip(q, riesgo, strict=True))
    assert list(artefacto_.cuantizar_monto(np.array([0.004, 12.345, 1e6]), 2)) == [
        0,
        1234,
        100_000_000,
    ]
    with pytest.raises(ValueError):
        artefacto_.cuantizar_probabilidad(np.array([1.2]), 8)
    with pytest.raises(ValueError):
        artefacto_.cuantizar_monto(np.array([-1.0]), 2)
