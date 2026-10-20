"""La explicacion local de una prediccion publicada, bajo demanda.

Se recalculan las features de la cuenta con los mismos relojes del scoring (el mismo SQL, con el
universo restringido a la cuenta y la disponibilidad que el scoring ya fijo) y se verifica que su
feature_hash sea el que guardo la prediccion: si no lo es, la explicacion no corresponderia a lo
publicado y no se da. Despues se descompone el predictor lineal de cada componente en la
contribucion de cada feature: su coeficiente por su valor estandarizado, mas el de su indicador de
nulo; en una categorica, el coeficiente de su categoria. El intercepto mas la suma de las
contribuciones es exactamente el predictor lineal (los log-odds de la probabilidad cruda, o el log
del monto esperado): la explicacion es aritmetica, no una aproximacion. La calibracion de Platt, si
la hay, es una funcion creciente de ese predictor: no cambia el signo de ninguna contribucion.

Una contribucion es una asociacion del modelo, no una causa: que una feature empuje la prediccion
hacia arriba no dice que cambiarla cambie el resultado. No se guarda nada y no lleva datos
personales: nombres de features, sus valores y sus contribuciones.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import numpy as np
from sqlalchemy import text
from sqlmodel import select

from motor_cartera.db.modelos import (
    ComponenteModeloPredictivo,
    CuentaCanonica,
    EjecucionScoringPredictivo,
    EstadoScoringPredictivo,
    PrediccionCuenta,
    VersionModeloPredictivo,
)
from motor_cartera.db.sesion import sesion
from motor_cartera.invariantes import presente
from motor_cartera.predictivo import artefacto as artefacto_
from motor_cartera.predictivo import construccion
from motor_cartera.predictivo import features as features_
from motor_cartera.predictivo import targets as targets_
from motor_cartera.predictivo.features import FEATURES
from motor_cartera.predictivo.targets import Target

AVISO = (
    "Una contribucion es una asociacion del modelo con su target, en la escala de su predictor "
    "lineal: no dice que cambiar esa feature cambie el resultado, ni recomienda una accion."
)
TOLERANCIA_DE_LA_SUMA = 1e-9


class ExplicacionNoDisponible(Exception):
    codigo = "EXPLICACION_NO_DISPONIBLE"
    estado_http = 409


def _error(nombre: str, codigo: str, estado_http: int) -> type[ExplicacionNoDisponible]:
    return type(nombre, (ExplicacionNoDisponible,), {"codigo": codigo, "estado_http": estado_http})


ScoringNoEncontrado = _error("ScoringNoEncontrado", "SCORING_NO_ENCONTRADO", 404)
ScoringNoPublicado = _error("ScoringNoPublicado", "SCORING_NO_PUBLICADO", 409)
CuentaNoEncontrada = _error("CuentaNoEncontrada", "CUENTA_NO_ENCONTRADA", 404)
SinPrediccion = _error("SinPrediccion", "SIN_PREDICCION", 404)
NoReproduce = _error("NoReproduce", "LA_EXPLICACION_NO_REPRODUCE", 409)


def explicar(scoring_run_id: UUID, cuenta_id: UUID, *, top: int = 5) -> dict:
    """La explicacion de la prediccion de `cuenta_id` en el scoring: sus features, y por cada
    componente las `top` contribuciones que mas empujan hacia arriba y las `top` que mas empujan
    hacia abajo, con la verificacion de que suman el predictor lineal y reproducen lo publicado."""
    with sesion() as s:
        # Una transaccion que puede crear sus tablas temporales y que al final se revierte: nada
        # de lo que hace queda.
        s.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        e = s.exec(
            select(EjecucionScoringPredictivo).where(
                EjecucionScoringPredictivo.scoring_run_id == scoring_run_id
            )
        ).first()
        if e is None:
            raise ScoringNoEncontrado(f"No existe el scoring {scoring_run_id}.")
        if e.estado != EstadoScoringPredictivo.EXITOSA:
            raise ScoringNoPublicado(
                f"El scoring {scoring_run_id} esta {e.estado}: solo se explica lo publicado."
            )
        cuenta = s.exec(select(CuentaCanonica).where(CuentaCanonica.cuenta_id == cuenta_id)).first()
        if cuenta is None:
            raise CuentaNoEncontrada(f"No existe la cuenta {cuenta_id}.")
        prediccion = s.get(PrediccionCuenta, (e.id, cuenta.id))
        if prediccion is None:
            raise SinPrediccion(
                f"El scoring {scoring_run_id} no tiene una prediccion de esa cuenta."
            )
        version = s.get_one(VersionModeloPredictivo, e.version_modelo_predictivo_id)
        componentes = s.exec(
            select(ComponenteModeloPredictivo).where(
                ComponenteModeloPredictivo.version_modelo_predictivo_id == version.id
            )
        ).all()
        relojes = construccion.Relojes(
            e.corte_canonico_id, e.despacho_id, e.cartera_id, e.as_of, e.known_at, e.zona_horaria
        )
        construccion.preparar_features(
            s,
            relojes,
            cuenta=cuenta.id,
            disponibilidad=construccion.Disponibilidad.desde_definicion(
                s, presente(e.disponibilidad, "la disponibilidad de un scoring publicado")
            ),
        )
        fila = s.execute(text("SELECT * FROM pf_feature")).mappings().one()
        s.rollback()
        publicado = {
            "p_recuperacion_positiva_30d": prediccion.p_recuperacion_positiva_30d,
            "p_contacto_14d": prediccion.p_contacto_14d,
            "p_promesa_30d": prediccion.p_promesa_30d,
            "recuperacion_positiva_esperada_30d": prediccion.recuperacion_positiva_esperada_30d,
        }
        feature_hash = bytes(prediccion.feature_hash)
        resumen = {
            "scoring_run_id": str(e.scoring_run_id),
            "modelo_version_id": str(version.modelo_version_id),
            "cuenta_id": str(cuenta_id),
            "as_of": e.as_of.isoformat(),
            "known_at": e.known_at.isoformat(),
            "estado": prediccion.estado,
            "motivos": prediccion.motivos,
            "riesgo_sin_recuperacion_30d": str(prediccion.riesgo_sin_recuperacion_30d),
        }
        artefactos = {Target(c.target): c.artefacto for c in componentes}
    valores = {f.nombre: fila[f.nombre] for f in FEATURES}
    recalculado = features_.feature_hash(valores)
    if recalculado != feature_hash or bytes(fila["feature_hash"]) != feature_hash:
        raise NoReproduce(
            "Las features de la cuenta, recalculadas con los relojes del scoring, no son las que "
            "se usaron al publicar (su feature_hash no coincide): la explicacion no "
            "corresponderia a lo publicado."
        )
    numericas, categoricas = construccion.arreglos_de_python(valores)
    explicados = [
        _componente(t, artefactos[t.target], numericas, categoricas, publicado, top)
        for t in targets_.TARGETS
    ]
    return {
        **resumen,
        "feature_hash": feature_hash.hex(),
        "features": {f.nombre: _mostrar(f, valores[f.nombre]) for f in FEATURES},
        "componentes": explicados,
        "aviso": AVISO,
    }


def _mostrar(feature: features_.Feature, valor: object) -> str | None:
    """El valor de una feature como se muestra: NULL es null, un texto tal cual y un numero en su
    forma canonica (dos decimales un importe, seis una pendiente)."""
    if valor is None:
        return None
    if feature.tipo == features_.Tipo.TEXTO:
        return str(valor)
    return features_.texto(feature, valor)


def _componente(
    definicion: targets_.DefinicionTarget,
    artefacto: dict,
    numericas: np.ndarray,
    categoricas: np.ndarray,
    publicado: dict,
    top: int,
) -> dict:
    runtime = artefacto_.Runtime(artefacto)
    aportes = {
        nombre: float(valor[0])
        for nombre, valor in runtime.contribuciones(numericas, categoricas).items()
    }
    suma = float(np.sum(list(aportes.values())))
    prediccion = runtime.predecir(numericas, categoricas)
    eta = float(prediccion.eta[0])
    precision = artefacto["precision"]
    if artefacto["algoritmo"] == artefacto_.LOGISTICA:
        decimales = precision["decimales_probabilidad"]
        entero = int(artefacto_.cuantizar_probabilidad(prediccion.publicada, decimales)[0])
        escala = "log-odds de la probabilidad cruda"
    else:
        decimales = precision["decimales_monto"]
        entero = int(artefacto_.cuantizar_monto(prediccion.publicada, decimales)[0])
        escala = "log de la recuperacion positiva esperada"
    reproducida = artefacto_.texto_decimal(entero, decimales)
    ordenadas = sorted(aportes.items(), key=lambda kv: (-kv[1], kv[0]))
    return {
        "target": definicion.target.value,
        "salida": definicion.salida,
        "escala": escala,
        "intercepto": runtime.intercepto,
        "predictor_lineal": eta,
        "suma_de_contribuciones": suma,
        "calibracion": artefacto["calibracion"],
        "prediccion_publicada": str(publicado[definicion.salida]),
        "prediccion_reproducida": reproducida,
        "hacia_arriba": [
            {"feature": nombre, "contribucion": valor}
            for nombre, valor in ordenadas[:top]
            if valor > 0
        ],
        "hacia_abajo": [
            {"feature": nombre, "contribucion": valor}
            for nombre, valor in reversed(ordenadas[-top:])
            if valor < 0
        ],
        "verificacion": {
            "suma_igual_al_predictor": abs(runtime.intercepto + suma - eta)
            <= TOLERANCIA_DE_LA_SUMA,
            "reproduce_lo_publicado": Decimal(reproducida) == publicado[definicion.salida],
        },
    }
