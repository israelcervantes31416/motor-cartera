"""Lo que comparten las pruebas de optimization/v1 contra PostgreSQL: un corte con sus cuentas y sus
productos, una decision/v2 con la prioridad y el canal que la prueba elige por cuenta, un modelo
PUBLICADA y un scoring EXITOSA sinteticos con el valor de oportunidad y el p_contacto que la prueba
elige por cuenta, configuraciones, y como abrir y resolver una ejecucion y leer lo que publico.

El modelo y el scoring sinteticos no salen de predictive/model-v1: se insertan con la forma que la
base exige (un entrenamiento EXITOSA y su version PUBLICADA con sus cuatro componentes VALIDADO,
en una sola transaccion; un scoring que se abre EN_PROCESO, recibe una prediccion por cuenta del
corte y se cierra EXITOSA). optimization/v1 solo lee lo publicado, no como se calculo: asi cada
prueba fija exactamente lo que entra al modelo. Todo es sintetico.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from campo_v2_escenarios import CORTE, LV_PARTIDO, decidir, decision_mixta
from historia_escenarios import SEMILLA, Cuenta, estado
from motor_pagos_escenarios import historiar_todo
from ruteo_v2_escenarios import (
    CuentaRuta,
    ProveedorDePruebas,
    coordenada,
    paradas,
    red_de_pruebas,
    rutear,
)
from ruteo_v2_escenarios import base as base_ruteo
from ruteo_v2_escenarios import escenario as escenario_ruteo
from ruteo_v2_escenarios import gestor as gestor_ruteo
from sqlalchemy import text
from sqlmodel import select

from motor_cartera.config import config
from motor_cartera.db.modelos import (
    TARGETS_PREDICTIVOS,
    ComponenteModeloPredictivo,
    CorteCanonico,
    EjecucionDecisionV2,
    EjecucionEntrenamientoPredictivo,
    EjecucionOptimizacion,
    EjecucionScoringPredictivo,
    PrediccionCuenta,
    VersionConfiguracionOptimizacion,
    VersionModeloPredictivo,
    ahora,
)
from motor_cartera.db.sesion import sesion
from motor_cartera.generador.oficial import escribir_cartera, tabla_cartera
from motor_cartera.ingesta.corridas import ingerir_archivo
from motor_cartera.optimizacion import configuracion as configuracion_
from motor_cartera.optimizacion import ejecuciones as motor

ZONA = "America/Mexico_City"
JUEVES = CORTE + timedelta(days=1)
"""La fecha de operacion de los planes sin ruteo: el dia siguiente al corte."""


@dataclass(frozen=True)
class CuentaOpt:
    """Una cuenta del corte: lo que decide decision/v2 de ella (canal y prioridad), su producto,
    sus dias de atraso, y lo que predice el scoring: su recuperacion positiva esperada en pesos y
    su p_contacto_14d, exactos, y si su prediccion es CON_IMPUTACION."""

    numero: int
    canal: str = "DIGITAL"
    prioridad: str = "MEDIA"
    producto: str = "CONSUMO"
    dias: int = 15
    valor: str = "100.00"
    contacto: str = "0.50000000"
    imputada: bool = False


# El golden en la base: los canales, las prioridades, los productos, los valores y los contactos
# de cada cuenta. La 7 (canal OTRO, que ninguna matriz admite) y la 8 (CON_IMPUTACION) valen mas
# que todas y no son elegibles; la 9 es la unica AUTOMOTRIZ.
GOLDEN = (
    CuentaOpt(1, "DIGITAL", "ALTA", "CONSUMO", 15, "90.00", "0.50000000"),
    CuentaOpt(2, "TELEFONICA", "ALTA", "CONSUMO", 45, "80.00", "0.40000000"),
    CuentaOpt(3, "CAMPO", "MUY_ALTA", "NOMINA", 120, "70.00", "0.30000000"),
    CuentaOpt(4, "DIGITAL", "MEDIA", "NOMINA", 15, "60.00", "0.60000000"),
    CuentaOpt(5, "TELEFONICA", "MEDIA", "TARJETA", 45, "50.00", "0.20000000"),
    CuentaOpt(6, "DIGITAL", "BAJA", "TARJETA", 15, "40.00", "0.70000000"),
    CuentaOpt(7, "OTRO", "BAJA", "CONSUMO", 15, "1000.00", "0.90000000"),
    CuentaOpt(8, "DIGITAL", "ALTA", "CONSUMO", 15, "500.00", "0.90000000", imputada=True),
    CuentaOpt(9, "DIGITAL", "BAJA", "AUTOMOTRIZ", 15, "10.00", "0.10000000"),
)


def admisible(decision: str, operativo: str, permitido: bool = True) -> dict:
    return {"canal_decision": decision, "canal_operativo": operativo, "permitido": permitido}


def pool(
    codigo: str,
    canal: str,
    capacidad: int | None,
    unidades: int,
    *,
    alcance: str = "GLOBAL",
    referencia: str | None = None,
    activo: bool = True,
) -> dict:
    return {
        "codigo": codigo,
        "canal": canal,
        "tipo_alcance": alcance,
        "referencia_alcance": referencia,
        "capacidad": capacidad,
        "unidades_recurso_por_asignacion": unidades,
        "activo": activo,
    }


def restriccion(
    codigo: str, tipo: str, dimension: str, valor: str, limite: int, activa: bool = True
) -> dict:
    return {
        "codigo": codigo,
        "tipo": tipo,
        "dimension": dimension,
        "valor_dimension": valor,
        "limite": limite,
        "activa": activa,
    }


def documento_golden(**cambios) -> dict:
    """La configuracion del golden en la base: DIGITAL (3 lugares, 1 unidad) y TELEFONICA (2, 4
    unidades), globales; DIGITAL admite DIGITAL, TELEFONICA admite TELEFONICA y DIGITAL, CAMPO
    admite TELEFONICA, y OTRO nada; al menos una AUTOMOTRIZ.

    El optimo, a mano: sin la AUTOMOTRIZ, las cinco de mayor valor (1 a 5, 350.00) caben; con ella,
    la 9 toma un lugar de DIGITAL y de 1, 2, 3, 4 y 5 entran cuatro: la 3 solo cabe en TELEFONICA,
    y con TELEFONICA = {2, 3} y DIGITAL = {1, 4, 9} el valor es 310.00 (con la 5 en lugar de la 2,
    300.00). 31,000 centavos, contacto 1.9 (190,000,000), 3 + 8 = 11 unidades, un solo plan."""
    return {
        "nombre": "Golden en la base",
        "permitir_predicciones_con_imputacion": False,
        "admisibilidad": [
            admisible("DIGITAL", "DIGITAL"),
            admisible("TELEFONICA", "TELEFONICA"),
            admisible("TELEFONICA", "DIGITAL"),
            admisible("CAMPO", "TELEFONICA"),
        ],
        "pools": [
            pool("DIGITAL_GLOBAL", "DIGITAL", 3, 1),
            pool("TELEFONICA_GLOBAL", "TELEFONICA", 2, 4),
        ],
        "restricciones": [
            restriccion("MIN_AUTOMOTRIZ", "MIN_CUENTAS", "PRODUCTO", "AUTOMOTRIZ", 1),
        ],
        "supuestos": "SUPUESTOS SINTETICOS de las pruebas.",
        **cambios,
    }


# --- el corte, la decision, el modelo y el scoring ------------------------------------------------


def ingerir(destino: Path, cuentas: tuple[CuentaOpt, ...], corte: date = CORTE) -> None:
    """Un corte de cartera/v2 con esas cuentas, sus dias de atraso y sus productos, materializado
    en la historia."""
    tabla = tabla_cartera(
        estado([Cuenta(c.numero, dias=c.dias) for c in cuentas]), semilla=SEMILLA, fecha_corte=corte
    ).to_pandas()
    tabla = tabla.astype(object)
    for i, c in enumerate(cuentas):
        tabla.at[i, "PRODUCTO"] = c.producto
    ruta = destino / f"optimizacion_{corte.isoformat()}.csv"
    escribir_cartera(estado([]), ruta, semilla=SEMILLA, fecha_corte=corte, tabla=tabla)
    corrida = ingerir_archivo(ruta, contrato="cartera/v2", fecha_corte=corte)
    assert corrida.estado == "EXITOSA", corrida.detalle
    historiar_todo()


def decision_de(cuentas: tuple[CuentaOpt, ...], corte: date = CORTE) -> EjecucionDecisionV2:
    """Una decision/v2 EXITOSA del corte con el canal y la prioridad de cada cuenta."""
    return decision_mixta(
        decidir(corte),
        {c.numero: c.prioridad for c in cuentas},
        {c.numero: c.canal for c in cuentas},
    )


def _hash(*partes: object) -> str:
    return hashlib.sha256(":".join(str(p) for p in partes).encode()).hexdigest()


def modelo_publicado(
    etiqueta: str = "optimizacion", *, rechazado: bool = False
) -> VersionModeloPredictivo:
    """Un entrenamiento EXITOSA y su version PUBLICADA con sus cuatro componentes VALIDADO (o,
    `rechazado`, RECHAZADA con uno RECHAZADO), en una sola transaccion. Sinteticos: ningun
    artefacto se evalua, y ninguno hace falta."""
    with sesion() as s:
        entrenamiento = EjecucionEntrenamientoPredictivo(
            despacho_id=config.despacho_id,
            cartera_id=config.cartera_id,
            zona_horaria=ZONA,
            version_modelo="predictive/model-v1",
            version_features="predictive-features/public-v1",
            hash_features=_hash(etiqueta, "features"),
            version_targets="predictive-targets/public-v1",
            hash_targets=_hash(etiqueta, "targets"),
            version_politica="predictive-training/public-v1",
            hash_politica=_hash(etiqueta, "politica"),
            manifiesto={"sintetico": etiqueta},
            hash_manifiesto=_hash(etiqueta, "manifiesto"),
            firma_inputs=_hash(etiqueta, "firma"),
            dataset_hash=_hash(etiqueta, "dataset"),
            estado="EXITOSA",
            resultado="MODELO_RECHAZADO" if rechazado else "MODELO_PUBLICADO",
            anclas=1,
            ejemplos_total=10,
            ejemplos_train=10,
            terminada_en=ahora(),
            detalle="Sintetico: de las pruebas de optimization/v1.",
        )
        s.add(entrenamiento)
        s.flush()
        version = VersionModeloPredictivo(
            version_modelo="predictive/model-v1",
            ejecucion_entrenamiento_predictivo_id=entrenamiento.id,
            version_features=entrenamiento.version_features,
            hash_features=entrenamiento.hash_features,
            version_targets=entrenamiento.version_targets,
            hash_targets=entrenamiento.hash_targets,
            version_politica=entrenamiento.version_politica,
            hash_politica=entrenamiento.hash_politica,
            dataset_hash=entrenamiento.dataset_hash,
            hash_modelo=_hash(etiqueta, "modelo"),
            estado="RECHAZADA" if rechazado else "PUBLICADA",
            publicada_en=None if rechazado else ahora(),
            periodo_train_desde=date(2026, 1, 7),
            periodo_train_hasta=date(2026, 5, 27),
            periodo_validation_desde=date(2026, 6, 3),
            periodo_validation_hasta=date(2026, 6, 24),
            periodo_test_desde=date(2026, 7, 1),
            periodo_test_hasta=date(2026, 7, 29),
            referencia_drift={},
            metadatos={"sintetico": etiqueta},
        )
        s.add(version)
        s.flush()
        for target in TARGETS_PREDICTIVOS:
            s.add(
                ComponenteModeloPredictivo(
                    version_modelo_predictivo_id=version.id,
                    target=target,
                    algoritmo="TWEEDIE_LOG"
                    if target == "RECUPERACION_POSITIVA_MONTO_30D"
                    else "LOGISTICA_L2",
                    hiperparametros={},
                    artefacto={},
                    hash_artefacto=_hash(etiqueta, target),
                    calibracion="RAW",
                    metricas_validation={},
                    metricas_test={},
                    baseline_test={},
                    bootstrap_test={},
                    explicacion_global=[],
                    estado="RECHAZADO"
                    if rechazado and target == TARGETS_PREDICTIVOS[0]
                    else "VALIDADO",
                    compuertas=[],
                )
            )
        s.commit()
        s.refresh(version)
        return version


def scoring_sintetico(
    modelo: VersionModeloPredictivo,
    cuentas: tuple[CuentaOpt, ...],
    corte: date = CORTE,
    *,
    etiqueta: str = "",
    drift: dict | None = None,
    cerrar: bool = True,
    invertido: bool = False,
) -> EjecucionScoringPredictivo:
    """Un scoring EXITOSA del corte con ese modelo: una prediccion por cuenta del corte, con el
    valor y el contacto de su CuentaOpt (por numero de cliente). Se abre EN_PROCESO, recibe sus
    predicciones y se cierra EXITOSA, como la base lo exige; sin `cerrar`, se queda EN_PROCESO.
    `invertido` inserta las predicciones en el orden contrario."""
    por_numero = {c.numero: c for c in cuentas}
    with sesion() as s:
        k = s.exec(select(CorteCanonico).where(CorteCanonico.fecha_corte == corte)).one()
        filas = s.execute(
            text(
                "SELECT c.cliente_unico, sn.cuenta_canonica_id FROM snapshot_cuenta sn "
                "JOIN cuenta_canonica c ON c.id = sn.cuenta_canonica_id "
                "WHERE sn.corte_canonico_id = :k ORDER BY sn.cuenta_canonica_id"
            ),
            {"k": k.id},
        ).all()
        if invertido:
            filas = filas[::-1]
        scoring = EjecucionScoringPredictivo(
            version_modelo_predictivo_id=modelo.id,
            corte_canonico_id=k.id,
            despacho_id=k.despacho_id,
            cartera_id=k.cartera_id,
            as_of=k.fecha_corte,
            known_at=ahora(),
            modo="OPERATIVO",
            zona_horaria=ZONA,
            version_features=modelo.version_features,
            hash_features=modelo.hash_features,
            firma_inputs=_hash(modelo.modelo_version_id, corte, etiqueta),
            estado="EN_PROCESO",
        )
        s.add(scoring)
        s.flush()
        for cliente_unico, cuenta in filas:
            c = por_numero[int(cliente_unico[2:])]
            p = Decimal("0.50000000")
            s.add(
                PrediccionCuenta(
                    ejecucion_scoring_predictivo_id=scoring.id,
                    cuenta_canonica_id=cuenta,
                    corte_canonico_id=k.id,
                    p_recuperacion_positiva_30d=p,
                    p_contacto_14d=Decimal(c.contacto),
                    p_promesa_30d=Decimal("0.10000000"),
                    riesgo_sin_recuperacion_30d=1 - p,
                    recuperacion_positiva_esperada_30d=Decimal(c.valor),
                    estado="CON_IMPUTACION" if c.imputada else "COMPLETA",
                    motivos=[],
                    feature_hash=hashlib.sha256(f"{etiqueta}:{cuenta}".encode()).digest(),
                )
            )
        s.flush()
        if not cerrar:
            s.commit()
            s.refresh(scoring)
            return scoring
        scoring.estado = "EXITOSA"
        scoring.resultado = "PREDICCIONES_PUBLICADAS"
        scoring.cuentas_objetivo = scoring.predicciones_publicadas = len(filas)
        scoring.hash_predicciones = _hash(scoring.firma_inputs, "predicciones")
        scoring.hash_drift = _hash(scoring.firma_inputs, "drift")
        scoring.drift_resumen = drift or {"feature": {}, "score": {}, "faltantes": {}}
        scoring.terminada_en = ahora()
        s.add(scoring)
        s.commit()
        s.refresh(scoring)
        return scoring


def escenario_golden(destino: Path, cuentas: tuple[CuentaOpt, ...] = GOLDEN) -> SimpleNamespace:
    """El corte, su decision/v2, el modelo y el scoring del golden en la base."""
    ingerir(destino, cuentas)
    decision = decision_de(cuentas)
    modelo = modelo_publicado()
    scoring = scoring_sintetico(modelo, cuentas)
    return SimpleNamespace(decision=decision, modelo=modelo, scoring=scoring, cuentas=cuentas)


# --- el escenario con CAMPO -----------------------------------------------------------------------

# Tres bases que cubren el mismo municipio, lejos entre si (en metros desde el origen de la red
# sintetica): cada cuenta va a la mas cercana. Las posiciones de BASE_B y BASE_C, y las de sus
# cuentas, son las de test_api_ruteo_v2.py.
BASE_A, BASE_B, BASE_C = (1_000, 1_000), (8_000, 1_000), (1_000, 8_000)
CORTO = {"codigo": "CORTO", "semana": {"JUEVES": [{"inicio": "09:00", "fin": "09:30"}]}}
"""Media hora el jueves: cabe una visita cerca de la base, no dos."""
PUNTOS_CAMPO = {
    # BASE_A: dos gestores de cupo 2, de 9 a 14 y de 16 a 19: las cuatro quedan PROGRAMADA.
    1: (1_000, 1_500),
    2: (1_600, 1_000),
    3: (2_000, 2_000),
    4: (500, 2_500),
    # BASE_B no tiene snap valido: su gestor no tiene ruta (NO_ENRUTABLE_BASE).
    5: (8_000, 1_500),
    6: (8_500, 1_000),
    # BASE_C, media hora: la 7 cabe (PROGRAMADA), la 8 ya no (SIN_TIEMPO), la 10 no tiene camino.
    7: (1_000, 8_500),
    8: (1_500, 8_000),
    10: (500, 8_200),
    # No son de CAMPO: la 9 es DIGITAL, la 11 y la 12, TELEFONICA (de BASE_A y de BASE_C).
    9: (3_000, 3_000),
    11: (2_500, 1_200),
    12: (1_200, 7_000),
}
CAMPO = (
    CuentaOpt(1, "CAMPO", "MUY_ALTA", valor="100.00", contacto="0.50000000"),
    CuentaOpt(2, "CAMPO", "ALTA", valor="90.00", contacto="0.40000000"),
    CuentaOpt(3, "CAMPO", "MEDIA", valor="80.00", contacto="0.30000000"),
    CuentaOpt(4, "CAMPO", "BAJA", valor="70.00", contacto="0.20000000"),
    CuentaOpt(5, "CAMPO", "MUY_ALTA", valor="300.00", contacto="0.90000000"),
    CuentaOpt(6, "CAMPO", "ALTA", valor="290.00", contacto="0.90000000"),
    CuentaOpt(7, "CAMPO", "MUY_ALTA", valor="60.00", contacto="0.60000000"),
    CuentaOpt(8, "CAMPO", "ALTA", valor="250.00", contacto="0.90000000"),
    CuentaOpt(9, "DIGITAL", "BAJA", valor="50.00", contacto="0.70000000"),
    CuentaOpt(10, "CAMPO", "MUY_ALTA", valor="240.00", contacto="0.90000000"),
    CuentaOpt(11, "TELEFONICA", "ALTA", valor="40.00", contacto="0.80000000"),
    CuentaOpt(12, "TELEFONICA", "ALTA", valor="30.00", contacto="0.80000000"),
)
"""Las que no se pueden programar (5, 6, 8 y 10) valen mas que todas: si CAMPO no exigiera una
parada PROGRAMADA, entrarian primero."""


def escenario_campo(destino: Path) -> SimpleNamespace:
    """El corte, su decision/v2 con canales elegidos, su geografia, sus bases, su territorial/v2,
    la configuracion de campo, el plan de campo/v2 del jueves y su ruteo/v2 con un proveedor de
    pruebas (BASE_B sin snap valido; la 10, desconectada); y un modelo y un scoring sinteticos."""
    e = escenario_ruteo(
        destino,
        [CuentaRuta(n, x, y) for n, (x, y) in PUNTOS_CAMPO.items()],
        [
            base_ruteo("BASE_A", *BASE_A),
            base_ruteo("BASE_B", *BASE_B),
            base_ruteo("BASE_C", *BASE_C),
        ],
        [
            gestor_ruteo("GESTOR_A1", "BASE_A", capacidad=2),
            gestor_ruteo("GESTOR_A2", "BASE_A", capacidad=2),
            gestor_ruteo("GESTOR_B", "BASE_C", capacidad=10, calendario="CORTO"),
            gestor_ruteo("GESTOR_C", "BASE_B", capacidad=10),
        ],
        calendarios=[LV_PARTIDO, CORTO],
        prioridades={c.numero: c.prioridad for c in CAMPO},
        canales={c.numero: c.canal for c in CAMPO},
    )
    red = red_de_pruebas()
    proveedor = ProveedorDePruebas(
        red.huella,
        snaps={coordenada(*BASE_B): Decimal("900")},
        desconectados={coordenada(*PUNTOS_CAMPO[10])},
    )
    ruteo = rutear(e.campo, red, proveedor)
    assert ruteo.estado == "EXITOSA", ruteo.detalle
    modelo = modelo_publicado("campo")
    return SimpleNamespace(
        decision=e.decision,
        campo=e.campo,
        ruteo=ruteo,
        modelo=modelo,
        scoring=scoring_sintetico(modelo, CAMPO, etiqueta="campo"),
        paradas=paradas(ruteo.id),
        cuentas=CAMPO,
    )


# --- configuraciones, ejecuciones y lo publicado --------------------------------------------------


def registrar(documento: dict, *, publicar: bool = True) -> VersionConfiguracionOptimizacion:
    with sesion() as s:
        registrada = configuracion_.registrar(s, configuracion_.leer(documento), publicar=publicar)
        s.commit()
        s.refresh(registrada.version)
        return registrada.version


def abrir(
    scoring: EjecucionScoringPredictivo,
    decision: EjecucionDecisionV2,
    version: VersionConfiguracionOptimizacion,
    *,
    fecha: date = JUEVES,
    ruteo=None,
) -> int:
    with sesion() as s:
        e = motor.abrir(
            s,
            scoring_run_id=scoring.scoring_run_id,
            decision_run_id=decision.decision_run_id,
            configuracion_id=version.configuracion_id,
            fecha_operacion=fecha,
            ruteo_run_id=None if ruteo is None else ruteo.ruteo_run_id,
            max_intentos=config.worker_max_intentos,
        )
        s.commit()
        return e.id


def ejecucion(ejecucion_id: int) -> EjecucionOptimizacion:
    with sesion() as s:
        return s.get_one(EjecucionOptimizacion, ejecucion_id)


def optimizar(scoring, decision, version, **opciones) -> EjecucionOptimizacion:
    """Abre y resuelve la ejecucion, como lo haria su trabajo, y la devuelve terminada."""
    ejecucion_id = abrir(scoring, decision, version, **opciones)
    motor.optimizar(ejecucion_id)
    return ejecucion(ejecucion_id)


def resultados(ejecucion_id: int) -> dict[int, dict]:
    """Lo publicado de cada cuenta, por numero de cliente, con el codigo de su pool, su base y su
    gestor."""
    with sesion() as s:
        filas = (
            s.execute(
                text(
                    "SELECT c.cliente_unico, c.cuenta_id, k.codigo AS pool, b.codigo AS base, "
                    "g.codigo AS gestor, r.* FROM resultado_optimizacion_cuenta r "
                    "JOIN cuenta_canonica c ON c.id = r.cuenta_canonica_id "
                    "LEFT JOIN pool_capacidad_optimizacion k "
                    "ON k.id = r.pool_capacidad_optimizacion_id "
                    "LEFT JOIN base_operativa b ON b.id = r.base_operativa_id "
                    "LEFT JOIN gestor_campo g ON g.id = r.gestor_campo_id "
                    "WHERE r.ejecucion_optimizacion_id = :e"
                ),
                {"e": ejecucion_id},
            )
            .mappings()
            .all()
        )
    return {int(f["cliente_unico"][2:]): dict(f) for f in filas}


def plan(ejecucion_id: int) -> dict[int, str | None]:
    """El pool de cada cuenta del plan, por numero de cliente (None: no asignada)."""
    return {n: r["pool"] for n, r in resultados(ejecucion_id).items()}


def usos(ejecucion_id: int) -> dict[str, dict]:
    with sesion() as s:
        filas = (
            s.execute(
                text(
                    "SELECT k.codigo, u.* FROM uso_pool_optimizacion u "
                    "JOIN pool_capacidad_optimizacion k ON k.id = u.pool_capacidad_optimizacion_id "
                    "WHERE u.ejecucion_optimizacion_id = :e"
                ),
                {"e": ejecucion_id},
            )
            .mappings()
            .all()
        )
    return {f["codigo"]: dict(f) for f in filas}


def restricciones(ejecucion_id: int) -> dict[str, dict]:
    with sesion() as s:
        filas = (
            s.execute(
                text(
                    "SELECT * FROM resultado_restriccion_optimizacion "
                    "WHERE ejecucion_optimizacion_id = :e"
                ),
                {"e": ejecucion_id},
            )
            .mappings()
            .all()
        )
    return {f["codigo"]: dict(f) for f in filas}


def cuantos(tabla: str, condicion: str = "true", **parametros) -> int:
    with sesion() as s:
        return s.execute(
            text(f"SELECT count(*) FROM {tabla} WHERE {condicion}"), parametros
        ).scalar_one()


DISPARADORES = (
    ("resultado_optimizacion_cuenta", "tr_optimizacion_inmutable"),
    ("uso_pool_optimizacion", "tr_optimizacion_inmutable"),
    ("resultado_restriccion_optimizacion", "tr_optimizacion_inmutable"),
    ("ejecucion_optimizacion", "tr_optimizacion_terminada"),
)


def borrar(ejecucion_id: int) -> None:
    """Borra una ejecucion terminada y lo suyo (sus resultados, sus usos, sus restricciones y su
    trabajo), apagando un momento los disparadores que lo protegen: para reconstruirla con las
    mismas entradas. Nada de lo declarado se toca."""
    with sesion() as s:
        for tabla, trigger in DISPARADORES:
            s.execute(text(f"ALTER TABLE {tabla} DISABLE TRIGGER {trigger}"))
        for sentencia in (
            "DELETE FROM resultado_optimizacion_cuenta WHERE ejecucion_optimizacion_id = :e",
            "DELETE FROM uso_pool_optimizacion WHERE ejecucion_optimizacion_id = :e",
            "DELETE FROM resultado_restriccion_optimizacion WHERE ejecucion_optimizacion_id = :e",
            "DELETE FROM trabajo_orquestacion WHERE ejecucion_optimizacion_id = :e",
            "DELETE FROM ejecucion_optimizacion WHERE id = :e",
        ):
            s.execute(text(sentencia), {"e": ejecucion_id})
        for tabla, trigger in DISPARADORES:
            s.execute(text(f"ALTER TABLE {tabla} ENABLE TRIGGER {trigger}"))
        s.commit()
