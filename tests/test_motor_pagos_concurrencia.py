"""El motor de pagos con varios workers a la vez, contra PostgreSQL real.

Cada caso fuerza la concurrencia en lugar de esperarla: los hilos se detienen en el punto exacto
hasta que el otro llega, y solo entonces siguen. Cada hilo usa su propia conexion, como un worker en
otro proceso.
"""

from __future__ import annotations

import threading
import time
from datetime import date, timedelta

import pytest
from historia_escenarios import historia_de, ingerir_pagos_de, pago
from motor_pagos_escenarios import cuantos, ejecuciones, historiar_todo, resultados, vigente
from sqlalchemy import func, update

from motor_cartera.config import Config
from motor_cartera.db.modelos import (
    EjecucionMotorPagos,
    EstadoTrabajo,
    MovimientoEconomicoCanonico,
    ResultadoPagoObservado,
    TipoTrabajo,
    TrabajoOrquestacion,
)
from motor_cartera.db.sesion import sesion
from motor_cartera.historia.ejecuciones import materializar
from motor_cartera.motor_pagos import ejecuciones as motor
from motor_cartera.motor_pagos.ejecuciones import interpretar
from motor_cartera.orquestacion import cola, worker

pytestmark = pytest.mark.usefixtures("bd")

SEPTIEMBRE = date(2026, 9, 1)
ESPERA = 60


def _pagos(tmp_path, nombre: str, desde: int = 1, cuantos_: int = 40):
    filas = [
        pago(i, f"2026-09-{1 + i % 28:02d} 10:00:00", f"{100 + i}.00")
        for i in range(desde, desde + cuantos_)
    ]
    return ingerir_pagos_de(tmp_path, nombre, filas)


def _en_otro_hilo(funcion, *argumentos) -> tuple[threading.Thread, dict]:
    salida: dict = {}

    def correr() -> None:
        try:
            salida["resultado"] = funcion(*argumentos)
        except BaseException as exc:
            salida["error"] = exc

    hilo = threading.Thread(target=correr, daemon=True)
    hilo.start()
    return hilo, salida


def _sin_errores(*salidas: dict) -> None:
    for salida in salidas:
        assert "error" not in salida, salida.get("error")


def _detener_en(monkeypatch, funcion: str) -> tuple[threading.Event, threading.Event]:
    """El primer hilo que llega a `funcion` del motor la ejecuta y se detiene despues, hasta que la
    prueba lo suelte. Los demas pasan de largo."""
    llego, soltar = threading.Event(), threading.Event()
    original = getattr(motor, funcion)

    def envoltura(*args, **kwargs):
        hecho = original(*args, **kwargs)
        if not llego.is_set():
            llego.set()
            assert soltar.wait(ESPERA)
        return hecho

    monkeypatch.setattr(motor, funcion, envoltura)
    return llego, soltar


# --- la misma ejecucion ---------------------------------------------------------------------------


def test_dos_workers_con_la_misma_ventana_publican_una_sola_vez(tmp_path, monkeypatch):
    _pagos(tmp_path, "pagos.csv")
    historiar_todo()
    (abierta,) = ejecuciones()
    llego, soltar = _detener_en(monkeypatch, "_crear_movimientos")

    primero, salida_1 = _en_otro_hilo(interpretar, abierta.id)
    assert llego.wait(ESPERA)
    # El segundo llega mientras el primero tiene la ejecucion bloqueada: espera.
    segundo, salida_2 = _en_otro_hilo(interpretar, abierta.id)
    time.sleep(0.5)
    assert segundo.is_alive()

    soltar.set()
    for hilo in (primero, segundo):
        hilo.join(ESPERA)

    _sin_errores(salida_1, salida_2)
    (terminada,) = ejecuciones()
    assert (terminada.estado, terminada.resultado) == ("EXITOSA", "INTERPRETACION_PUBLICADA")
    assert cuantos(ResultadoPagoObservado) == 40
    assert cuantos(MovimientoEconomicoCanonico) == terminada.movimientos_canonicos == 40


# --- un worker que pierde el lease despues de calcular --------------------------------------------


def test_un_worker_que_pierde_el_lease_a_media_interpretacion_no_publica(tmp_path, monkeypatch):
    _pagos(tmp_path, "pagos.csv")
    historiar_todo()
    with sesion() as s:
        # La historia se materializo a mano: su trabajo se da por terminado, para que la cola solo
        # tenga el del motor.
        s.execute(
            update(TrabajoOrquestacion)
            .where(TrabajoOrquestacion.tipo == TipoTrabajo.HISTORIA)
            .values(estado=EstadoTrabajo.COMPLETADO, terminado_en=func.now())
        )
        s.commit()
    # Sin latidos durante la prueba: el lease vence porque la prueba lo vence.
    config = Config(worker_lease_segundos=600, worker_heartbeat_segundos=300)
    calculo, soltar = _detener_en(monkeypatch, "_insertar_resultados")
    reclamo_a = cola.reclamar("worker-a", config.worker_lease_segundos)
    assert reclamo_a.tipo == TipoTrabajo.MOTOR_PAGOS
    hilo_a, salida_a = _en_otro_hilo(worker.procesar_reclamo, reclamo_a, "worker-a", config)
    assert calculo.wait(ESPERA)

    # A ya calculo todo, pero su lease vence antes de que confirme: B toma el trabajo.
    with sesion() as s:
        s.execute(
            update(TrabajoOrquestacion)
            .where(TrabajoOrquestacion.id == reclamo_a.id)
            .values(lease_hasta=func.now() - timedelta(seconds=1))
        )
        s.commit()
    hilo_b, salida_b = _en_otro_hilo(worker.procesar_un_trabajo, "worker-b", config)
    time.sleep(0.5)
    assert hilo_b.is_alive()  # espera la ejecucion, que A todavia tiene bloqueada

    soltar.set()
    for hilo in (hilo_a, hilo_b):
        hilo.join(ESPERA)

    _sin_errores(salida_a, salida_b)
    # A no publico: al confirmar, su trabajo ya era de B, y se revirtio entero. B, el dueno
    # vigente, la interpreto y cerro su trabajo.
    assert salida_a["resultado"].estado is None
    assert salida_b["resultado"].estado == EstadoTrabajo.COMPLETADO
    with sesion() as s:
        trabajo = s.get_one(TrabajoOrquestacion, reclamo_a.id)
    assert (trabajo.estado, trabajo.intentos, trabajo.worker_id) == ("COMPLETADO", 2, None)
    (terminada,) = ejecuciones()
    assert (terminada.estado, terminada.resultado) == ("EXITOSA", "INTERPRETACION_PUBLICADA")
    assert cuantos(ResultadoPagoObservado) == 40
    assert cuantos(EjecucionMotorPagos) == 1


# --- pagos que se publican mientras se interpreta su ventana -------------------------------------


def test_pagos_publicados_mientras_se_interpreta_su_ventana_se_interpretan_despues(
    tmp_path, monkeypatch
):
    _pagos(tmp_path, "uno.csv")
    historiar_todo()
    (primera,) = ejecuciones()
    segundo_archivo = _pagos(tmp_path, "dos.csv", desde=200, cuantos_=5)
    llego, soltar = _detener_en(monkeypatch, "_clasificar")

    interpretando, salida_motor = _en_otro_hilo(interpretar, primera.id)
    assert llego.wait(ESPERA)
    # La historia del segundo archivo publica sus pagos mientras el motor interpreta la ventana: la
    # apertura de la ventana espera a que el motor termine.
    historia, salida_historia = _en_otro_hilo(materializar, historia_de(ingesta=segundo_archivo).id)
    time.sleep(0.5)
    assert historia.is_alive()

    soltar.set()
    for hilo in (interpretando, historia):
        hilo.join(ESPERA)

    _sin_errores(salida_motor, salida_historia)
    terminada, nueva = ejecuciones()
    # La primera interpreto lo que leyo, y la historia abrio otra para lo que publico despues.
    assert (terminada.estado, terminada.observaciones_leidas) == ("EXITOSA", 40)
    assert nueva.estado == "EN_PROCESO"
    interpretar(nueva.id)
    assert vigente(SEPTIEMBRE).observaciones_leidas == 45
    assert len(resultados(vigente(SEPTIEMBRE))) == 45


def test_dos_archivos_de_la_misma_cartera_a_la_vez_comparten_una_interpretacion(
    tmp_path, monkeypatch
):
    uno = _pagos(tmp_path, "uno.csv")
    dos = _pagos(tmp_path, "dos.csv", desde=100, cuantos_=30)
    juntos = threading.Barrier(2, timeout=ESPERA)
    abrir = motor.abrir_por_dataset

    def a_la_vez(*args, **kwargs):
        juntos.wait()
        return abrir(*args, **kwargs)

    from motor_cartera.historia import ejecuciones as historia

    monkeypatch.setattr(historia, "abrir_por_dataset", a_la_vez)
    hilos = [_en_otro_hilo(materializar, historia_de(ingesta=ingesta).id) for ingesta in (uno, dos)]
    for hilo, _ in hilos:
        hilo.join(ESPERA)

    _sin_errores(*(salida for _, salida in hilos))
    # Una sola interpretacion pendiente para la ventana: la segunda historia la reuso.
    (abierta,) = ejecuciones()
    assert abierta.estado == "EN_PROCESO"
    assert cuantos(TrabajoOrquestacion, TrabajoOrquestacion.tipo == TipoTrabajo.MOTOR_PAGOS) == 1
    interpretar(abierta.id)
    assert vigente(SEPTIEMBRE).observaciones_leidas == 70
