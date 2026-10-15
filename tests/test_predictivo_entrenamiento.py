"""Un entrenamiento de predictive/model-v1 de punta a punta, sobre un escenario generado con
escenario-predictivo/v1 (600 cuentas, 20 cortes semanales) cargado como cualquier cartera.

Lo que se prueba: que publica una version con sus cuatro componentes VALIDADO, sus artefactos JSON
con su hash y su ficha; que las metricas de TEST son las del artefacto publicado (un scoring
BACKTEST con los relojes del ancla de TEST las reproduce exactamente); que el baseline es la
prevalencia de TRAIN (120); que reconstruirlo con el mismo manifiesto da exactamente lo mismo
(128); que es todo o nada en cada punto de falla; que dos workers publican una vez, y que uno que
pierde su lease no publica; que lo publicado no se reescribe; y que una version que no pasa sus
compuertas queda RECHAZADA y ningun scoring la usa.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

import pytest
from predictivo_escenarios import (
    abrir_entrenamiento,
    borrar_entrenamiento,
    componentes_de,
    corte_de,
    entrenamiento_de,
    entrenar,
    generado,
    propuesto,
    puntuar,
    version_de,
)
from sqlalchemy import func, text, update
from sqlalchemy.exc import DBAPIError

from motor_cartera.config import Config
from motor_cartera.db.modelos import EstadoTrabajo, TipoTrabajo, TrabajoOrquestacion
from motor_cartera.db.sesion import sesion
from motor_cartera.orquestacion import cola, worker
from motor_cartera.predictivo import artefacto as artefacto_
from motor_cartera.predictivo import desempeno, scoring
from motor_cartera.predictivo import entrenamiento as motor
from motor_cartera.predictivo import manifiesto as manifiesto_
from motor_cartera.predictivo.entrenamiento import hash_modelo

ESPERA = 120


@pytest.fixture(scope="module")
def escenario(bd_del_modulo, tmp_path_factory):
    return generado(tmp_path_factory.mktemp("predictivo_entrenamiento"))


@pytest.fixture(scope="module")
def publicado(escenario):
    e = entrenar(propuesto(escenario))
    assert (e.estado, e.resultado) == ("EXITOSA", "MODELO_PUBLICADO"), e.detalle
    return e


def _cola_vacia() -> None:
    with sesion() as s:
        s.execute(
            update(TrabajoOrquestacion)
            .where(TrabajoOrquestacion.estado.in_(("PENDIENTE", "EJECUTANDO")))
            .values(
                estado=EstadoTrabajo.COMPLETADO,
                terminado_en=func.now(),
                worker_id=None,
                lease_hasta=None,
            )
        )
        s.commit()


def _cuantos(tabla: str, condicion: str = "true", **parametros) -> int:
    with sesion() as s:
        return s.execute(
            text(f"SELECT count(*) FROM {tabla} WHERE {condicion}"), parametros
        ).scalar_one()


# --- publicar -------------------------------------------------------------------------------------


def test_publica_una_version_con_cuatro_componentes(publicado):
    e = publicado
    assert e.anclas == 20 and len(e.dataset_hash) == 64
    assert (
        e.ejemplos_train + e.ejemplos_validation + e.ejemplos_test + e.ejemplos_purge
        == e.ejemplos_total
    )
    assert e.ejemplos_train > 0 and e.ejemplos_validation > 0 and e.ejemplos_test > 0
    # Las anclas del final no tienen sus labels maduros: censuradas, nunca ceros.
    assert e.ejemplos_censurados > 0
    for target in ("RECUPERACION_POSITIVA_30D", "CONTACTO_14D", "PROMESA_30D"):
        assert e.conteos[target]["PURGE"]["censuradas"] > 0
        assert 0 < e.conteos[target]["TRAIN"]["prevalencia"] < 1
    assert e.tiempos["fases_segundos"]["dataset"] > 0
    assert set(e.tiempos["dataset_fases_segundos"]) == {"features", "labels", "copia", "conversion"}
    assert e.tiempos["matriz"]["columnas"] > e.tiempos["matriz"]["features"] == 40
    assert e.tiempos["matriz"]["filas_train"] == e.ejemplos_train
    assert {f["estado"] for f in e.backtest["folds"]} >= {"PRINCIPAL"}
    v = version_de(e.id)
    assert v.estado == "PUBLICADA" and v.publicada_en is not None
    componentes = componentes_de(v.id)
    assert set(componentes) == {
        "RECUPERACION_POSITIVA_30D",
        "CONTACTO_14D",
        "PROMESA_30D",
        "RECUPERACION_POSITIVA_MONTO_30D",
    }
    for c in componentes.values():
        assert c.estado == "VALIDADO" and all(g["ok"] for g in c.compuertas)
        assert c.hash_artefacto == artefacto_.hash_artefacto(c.artefacto)
        assert c.artefacto["formato"] == "predictive/artifact-v1"
        assert c.bootstrap_test["repeticiones"] == 200 and c.bootstrap_test["semilla"] == 2026
        assert c.explicacion_global[0]["magnitud"] >= c.explicacion_global[-1]["magnitud"]
    assert v.hash_modelo == hash_modelo({t: c.hash_artefacto for t, c in componentes.items()})
    assert v.metadatos["uso_no_previsto"] and v.metadatos["entorno"]["numpy"]
    # La ficha, completa por si sola: las features y los targets con sus hashes, las metricas de
    # TEST de cada componente, la referencia del drift y la privacidad.
    from motor_cartera.predictivo import features as features_
    from motor_cartera.predictivo import targets as targets_

    ficha = v.metadatos
    assert ficha["features"]["hash"] == features_.hash_features()
    assert len(ficha["features"]["nombres"]) == 40
    assert ficha["targets"]["hash"] == targets_.hash_targets()
    for target, c in componentes.items():
        assert ficha["componentes"][target]["metricas_test"]["n"] == c.metricas_test["n"]
    de_la_ficha = ficha["componentes"]["RECUPERACION_POSITIVA_30D"]["metricas_test"]
    del_componente = componentes["RECUPERACION_POSITIVA_30D"].metricas_test
    assert de_la_ficha["roc_auc"] == del_componente["roc_auc"]
    assert "PSI" in ficha["referencia_drift"] and ficha["privacidad_y_etica"]
    assert v.referencia_drift["filas_train"] == e.ejemplos_train
    assert "no mide el efecto" in e.detalle


def test_el_baseline_es_la_prevalencia_de_train(publicado):
    """Golden 120: el baseline de TEST es la prevalencia (o la media) de TRAIN, constante; ni de
    VALIDATION ni de TEST."""
    componentes = componentes_de(version_de(publicado.id).id)
    for target, c in componentes.items():
        clave = "media" if target == "RECUPERACION_POSITIVA_MONTO_30D" else "prevalencia"
        assert c.baseline_test["prediccion"] == pytest.approx(
            publicado.conteos[target]["TRAIN"][clave], abs=1e-8
        )
        assert c.baseline_test["prediccion"] != pytest.approx(
            publicado.conteos[target]["TEST"][clave], abs=1e-12
        )


def test_las_metricas_de_test_son_las_del_artefacto_publicado(escenario, publicado):
    """Un scoring BACKTEST del corte de TEST con su feature_known_at, por otro camino (el SQL del
    scoring, el runtime del JSON, lo cuantizado en la base), reproduce exactamente las metricas de
    TEST del entrenamiento."""
    v = version_de(publicado.id)
    ancla = next(a for a in publicado.manifiesto["anclas"] if a["split"] == "TEST")
    corte = corte_de(escenario, ancla["feature_as_of"])
    s = puntuar(
        v.modelo_version_id,
        corte["corte_id"],
        known_at=datetime.fromisoformat(ancla["feature_known_at"]),
        modo="BACKTEST",
    )
    assert s.estado == "EXITOSA", s.detalle
    from datetime import date

    r = desempeno.evaluar(
        s.scoring_run_id,
        label_known_at=datetime.fromisoformat(ancla["label_known_at"]),
        pagos_completos_hasta=date.fromisoformat(ancla["pagos_completos_hasta"]),
        lifecycle_completo_hasta=date.fromisoformat(ancla["lifecycle_completo_hasta"]),
    )
    for c in r["componentes"]:
        assert c["estado"] == "EVALUADO"
        assert all(d == 0 for d in c["diferencias"].values() if d is not None), c


def test_el_mismo_manifiesto_no_se_entrena_dos_veces(escenario, publicado):
    with pytest.raises(motor.EntrenamientoYaPublicado):
        abrir_entrenamiento(propuesto(escenario))


def test_lo_publicado_no_se_reescribe(publicado):
    v = version_de(publicado.id)
    for sentencia in (
        "UPDATE version_modelo_predictivo SET hash_modelo = repeat('0', 64) WHERE id = :v",
        "UPDATE version_modelo_predictivo SET estado = 'RECHAZADA' WHERE id = :v",
        "DELETE FROM version_modelo_predictivo WHERE id = :v",
        "UPDATE componente_modelo_predictivo SET estado = 'RECHAZADO' "
        "WHERE version_modelo_predictivo_id = :v",
        "DELETE FROM componente_modelo_predictivo WHERE version_modelo_predictivo_id = :v",
        "UPDATE ancla_entrenamiento_predictivo SET split = 'TEST' "
        "WHERE ejecucion_entrenamiento_predictivo_id = :e",
        "UPDATE ejecucion_entrenamiento_predictivo SET dataset_hash = repeat('0', 64) "
        "WHERE id = :e",
    ):
        with sesion() as s, pytest.raises(DBAPIError):
            s.execute(text(sentencia), {"v": v.id, "e": publicado.id})
            s.commit()


def test_reconstruir_con_el_mismo_manifiesto_da_exactamente_lo_mismo(escenario):
    """Golden 128: el mismo manifiesto sobre los mismos hechos da el mismo dataset_hash, los mismos
    artefactos y las mismas metricas, aunque las tablas que se leen cambien de orden fisico."""
    datos = propuesto(escenario, variante=1)
    primera = entrenar(datos)
    v = version_de(primera.id)
    antes = {t: (c.hash_artefacto, c.metricas_test) for t, c in componentes_de(v.id).items()}
    borrar_entrenamiento(primera.id)
    with sesion() as s:
        for tabla, indice in (
            ("snapshot_cuenta", "ix_snapshot_cuenta_historia"),
            ("gestion_cobranza", "ix_gestion_cuenta_ocurrido"),
            ("movimiento_economico_canonico", "ix_movimiento_movimiento_id"),
        ):
            s.execute(text(f"CLUSTER {tabla} USING {indice}"))
            s.execute(text(f"ANALYZE {tabla}"))
        s.commit()
    otro = {**datos, "anclas": list(reversed(datos["anclas"]))}
    segunda = entrenar(otro)
    assert segunda.id != primera.id and segunda.firma_inputs == primera.firma_inputs
    assert segunda.dataset_hash == primera.dataset_hash
    w = version_de(segunda.id)
    assert w.hash_modelo == v.hash_modelo
    despues = {t: (c.hash_artefacto, c.metricas_test) for t, c in componentes_de(w.id).items()}
    assert despues == antes


# --- todo o nada ----------------------------------------------------------------------------------


class Falla(Exception):
    pass


@pytest.mark.parametrize("donde", motor.PUNTOS_DE_FALLA)
def test_una_falla_en_cualquier_punto_no_publica_nada(escenario, monkeypatch, donde):
    datos = propuesto(escenario, variante=10 + motor.PUNTOS_DE_FALLA.index(donde))
    ejecucion_id = abrir_entrenamiento(datos)

    def fallar_en(nombre: str) -> None:
        if nombre == donde:
            raise Falla(nombre)

    monkeypatch.setattr(motor, "_punto_de_falla", fallar_en)
    motor.entrenar(ejecucion_id)
    fallida = entrenamiento_de(ejecucion_id)
    assert (fallida.estado, fallida.resultado) == ("FALLIDA", "ERROR_INTERNO")
    assert fallida.dataset_hash is None and "Falla" in fallida.detalle
    assert version_de(ejecucion_id) is None
    assert (
        _cuantos(
            "ancla_entrenamiento_predictivo",
            "ejecucion_entrenamiento_predictivo_id = :e",
            e=ejecucion_id,
        )
        == 20
    )
    monkeypatch.undo()
    otra = entrenar(datos)
    assert (otra.estado, otra.firma_inputs) == ("EXITOSA", fallida.firma_inputs)


def test_una_version_que_no_pasa_sus_compuertas_queda_rechazada(escenario, monkeypatch):
    """Si un componente no pasa una compuerta en TEST, la version queda RECHAZADA con sus
    metricas, el entrenamiento EXITOSA con MODELO_RECHAZADO, y ningun scoring la puede usar."""
    original = motor._compuerta

    def estricta(nombre, valor, operador, umbral):
        if nombre == "ROC_AUC_MAYOR_QUE_0_5":
            return original(nombre, valor, ">", 1.0)
        return original(nombre, valor, operador, umbral)

    monkeypatch.setattr(motor, "_compuerta", estricta)
    e = entrenar(propuesto(escenario, variante=2))
    assert (e.estado, e.resultado) == ("EXITOSA", "MODELO_RECHAZADO")
    v = version_de(e.id)
    assert v.estado == "RECHAZADA" and v.publicada_en is None
    estados = {t: c.estado for t, c in componentes_de(v.id).items()}
    assert estados["RECUPERACION_POSITIVA_MONTO_30D"] == "VALIDADO"
    assert estados["PROMESA_30D"] == "RECHAZADO"
    assert "RECHAZADA" in e.detalle
    with pytest.raises(scoring.ModeloNoPublicado):
        puntuar(v.modelo_version_id, corte_de(escenario, "2026-07-15")["corte_id"])


def test_una_version_que_el_servicio_no_entrena_no_publica(escenario):
    ejecucion_id = abrir_entrenamiento(propuesto(escenario, variante=3))
    with sesion() as s:
        s.execute(
            text(
                "UPDATE ejecucion_entrenamiento_predictivo SET hash_features = repeat('a', 64) "
                "WHERE id = :e"
            ),
            {"e": ejecucion_id},
        )
        s.commit()
    motor.entrenar(ejecucion_id)
    e = entrenamiento_de(ejecucion_id)
    assert (e.estado, e.resultado) == ("FALLIDA", "VERSION_NO_SOPORTADA")
    assert version_de(ejecucion_id) is None


# --- la cola --------------------------------------------------------------------------------------


def test_un_worker_toma_el_trabajo_y_lo_cierra(escenario, trabajar):
    _cola_vacia()
    ejecucion_id = abrir_entrenamiento(propuesto(escenario, variante=4))
    procesados = trabajar()
    assert [(p.tipo, p.estado) for p in procesados] == [
        (TipoTrabajo.PREDICTIVE_TRAINING, EstadoTrabajo.COMPLETADO)
    ]
    assert entrenamiento_de(ejecucion_id).estado == "EXITOSA"


def test_la_cola_que_agota_sus_intentos_lo_deja_fallido(escenario):
    from motor_cartera.orquestacion import objetivos

    _cola_vacia()
    ejecucion_id = abrir_entrenamiento(propuesto(escenario, variante=5))
    with sesion() as s:
        assert objetivos.fallar(s, TipoTrabajo.PREDICTIVE_TRAINING, ejecucion_id, "agotado")
        s.commit()
    e = entrenamiento_de(ejecucion_id)
    assert (e.estado, e.resultado) == ("FALLIDA", "INTENTOS_AGOTADOS")


def _en_otro_hilo(funcion, *argumentos, **opciones) -> tuple[threading.Thread, dict]:
    salida: dict = {}

    def correr() -> None:
        try:
            salida["resultado"] = funcion(*argumentos, **opciones)
        except BaseException as exc:  # pragma: no cover - la prueba lo reporta
            salida["error"] = exc

    hilo = threading.Thread(target=correr, daemon=True)
    hilo.start()
    return hilo, salida


def _detener_en(monkeypatch, punto: str) -> tuple[threading.Event, threading.Event]:
    llego, soltar = threading.Event(), threading.Event()

    def pausa(nombre: str) -> None:
        if nombre == punto and not llego.is_set():
            llego.set()
            assert soltar.wait(ESPERA)

    monkeypatch.setattr(motor, "_punto_de_falla", pausa)
    return llego, soltar


def test_dos_workers_con_la_misma_ejecucion_publican_una_vez(escenario, monkeypatch):
    """El segundo espera el candado del primero y despues la encuentra terminada."""
    ejecucion_id = abrir_entrenamiento(propuesto(escenario, variante=6))
    llego, soltar = _detener_en(monkeypatch, motor.DESPUES_ENTRENAMIENTO)
    primero, salida_1 = _en_otro_hilo(motor.ejecutar_entrenamiento, ejecucion_id)
    assert llego.wait(ESPERA)
    segundo, salida_2 = _en_otro_hilo(motor.ejecutar_entrenamiento, ejecucion_id)
    time.sleep(0.5)
    assert segundo.is_alive()
    soltar.set()
    for hilo in (primero, segundo):
        hilo.join(ESPERA)
    assert "error" not in salida_1 and "error" not in salida_2
    assert entrenamiento_de(ejecucion_id).estado == "EXITOSA"
    assert (
        _cuantos(
            "version_modelo_predictivo",
            "ejecucion_entrenamiento_predictivo_id = :e",
            e=ejecucion_id,
        )
        == 1
    )


def test_un_worker_que_pierde_el_lease_no_publica(escenario, monkeypatch):
    _cola_vacia()
    ejecucion_id = abrir_entrenamiento(propuesto(escenario, variante=7))
    config = Config(worker_lease_segundos=600, worker_heartbeat_segundos=300)
    calculo, soltar = _detener_en(monkeypatch, motor.DESPUES_EVALUACION)
    reclamo_a = cola.reclamar("worker-a", config.worker_lease_segundos)
    assert reclamo_a.tipo == TipoTrabajo.PREDICTIVE_TRAINING
    hilo_a, salida_a = _en_otro_hilo(worker.procesar_reclamo, reclamo_a, "worker-a", config)
    assert calculo.wait(ESPERA)
    # A ya construyo el dataset, ajusto y evaluo, sin una transaccion abierta, pero su lease vence
    # antes de publicar: B toma el trabajo y espera el candado de A.
    with sesion() as s:
        s.execute(
            update(TrabajoOrquestacion)
            .where(TrabajoOrquestacion.id == reclamo_a.id)
            .values(lease_hasta=func.now() - timedelta(seconds=1))
        )
        s.commit()
    hilo_b, salida_b = _en_otro_hilo(worker.procesar_un_trabajo, "worker-b", config)
    time.sleep(0.5)
    assert hilo_b.is_alive()
    soltar.set()
    for hilo in (hilo_a, hilo_b):
        hilo.join(ESPERA)
    assert "error" not in salida_a and "error" not in salida_b
    assert salida_a["resultado"].estado is None
    assert salida_b["resultado"].estado == EstadoTrabajo.COMPLETADO
    assert entrenamiento_de(ejecucion_id).estado == "EXITOSA"
    assert (
        _cuantos(
            "version_modelo_predictivo",
            "ejecucion_entrenamiento_predictivo_id = :e",
            e=ejecucion_id,
        )
        == 1
    )


def test_en_primer_plano_entrena_con_su_propio_trabajo(escenario):
    _cola_vacia()
    e = worker.entrenar_predictivo_en_primer_plano(
        manifiesto_.leer(propuesto(escenario, variante=8))
    )
    assert e.estado == "EXITOSA"
    with sesion() as s:
        trabajo = s.execute(
            text(
                "SELECT estado, intentos FROM trabajo_orquestacion "
                "WHERE ejecucion_entrenamiento_predictivo_id = :e"
            ),
            {"e": e.id},
        ).one()
    assert tuple(trabajo) == ("COMPLETADO", 1)


def test_la_bitacora_no_lleva_datos_de_una_cuenta(escenario, caplog):
    """Ni un cuenta_id, ni un CLIENTE_UNICO, ni el valor de una feature en la bitacora."""
    import logging

    caplog.set_level(logging.DEBUG)
    e = entrenar(propuesto(escenario, variante=9))
    assert e.estado == "EXITOSA"
    with sesion() as s:
        ids = [str(x) for (x,) in s.execute(text("SELECT cuenta_id FROM cuenta_canonica LIMIT 50"))]
        clientes = [
            x for (x,) in s.execute(text("SELECT cliente_unico FROM cuenta_canonica LIMIT 50"))
        ]
    texto = caplog.text
    assert not any(x in texto for x in ids + clientes)
