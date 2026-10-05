"""El worker: toma trabajos de la cola durable, ejecuta su motor y los cierra.

Corre aparte de la API, con `motor-cartera worker`. En cada vuelta toma un trabajo, lo ejecuta con
un latido que renueva su lease desde otro hilo, vuelve a leer como quedo su recurso y lo cierra en
una sola transaccion con lo que sigue:

- si el recurso llego a un estado terminal, el trabajo queda COMPLETADO, aunque el motor haya
  terminado FALLIDA: el trabajo cumplio con ejecutarlo. Si es de un flujo, el flujo avanza o se
  detiene en esa misma transaccion. El archivo de una ingesta no se borra: es evidencia;
- si no, fue un error del worker y no del motor: el trabajo vuelve a la cola, con una espera que se
  duplica en cada intento, o, si ya no le quedan intentos, queda FALLIDO junto con su recurso, y su
  flujo se detiene.

Un error de un trabajo no detiene el worker. Si el worker muere, ninguna de sus transacciones queda
a medias, y el lease del trabajo que tenia vence: otro worker lo toma. Asi la durabilidad no depende
de que el worker se apague bien.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlmodel import Session

from motor_cartera.config import Config
from motor_cartera.config import config as config_del_entorno
from motor_cartera.contratos import VERSION_CONTRATO
from motor_cartera.db.modelos import (
    ArchivoCorrida,
    ArtefactoFuente,
    Corrida,
    EstadoCorrida,
    EstadoTrabajo,
    EtapaFlujo,
    TipoTrabajo,
)
from motor_cartera.db.sesion import sesion
from motor_cartera.decision.ejecuciones import DecisionYaGenerada, ejecutar_decision
from motor_cartera.fuentes.almacen import ArtefactoFaltante
from motor_cartera.fuentes.artefactos import almacen_de, guardar_artefacto
from motor_cartera.ingesta.corridas import procesar_corrida, verificar_declaracion
from motor_cartera.orquestacion import cola, flujo, objetivos
from motor_cartera.orquestacion.cola import Reclamo
from motor_cartera.ruteo.ejecuciones import RuteoYaGenerado, ejecutar_ruteo
from motor_cartera.territorial.ejecuciones import TerritorialYaGenerado, ejecutar_territorial

log = logging.getLogger(__name__)

PERDIO_LA_CARRERA = (DecisionYaGenerada, TerritorialYaGenerado, RuteoYaGenerado)
"""Lo que levanta un motor cuando otra ejecucion de su fuente publico primero. Para entonces la suya
ya quedo FALLIDA: no es un error del worker."""


class ArchivoFaltante(Exception):
    """Una corrida EN_PROCESO sin artefacto y sin su archivo de v0.5: no hay de donde leer."""


def _ingerir(corrida_id: int) -> None:
    """La ingesta de un trabajo: procesa la corrida con el archivo de su artefacto. Si la corrida ya
    termino no hace nada, ni lee el archivo.

    Si el almacen no tiene el objeto, es un error del worker y no de la corrida: el volumen puede
    no estar montado, y el trabajo se reintenta con su espera, como cualquier otro error del
    worker. Una corrida de v0.5 que seguia en la cola al migrar no tiene artefacto: se procesa con
    su archivo en BYTEA, que tampoco se borra.
    """
    with sesion() as s:
        corrida = s.get_one(Corrida, corrida_id)
        if corrida.estado != EstadoCorrida.EN_PROCESO:
            log.info("corrida %s ya termino %s; no se procesa otra vez", corrida_id, corrida.estado)
            return
        if corrida.artefacto_fuente_id is not None:
            sha256 = s.get_one(ArtefactoFuente, corrida.artefacto_fuente_id).sha256
            contenido = None
        else:
            archivo = s.get(ArchivoCorrida, corrida_id)
            if archivo is None:
                raise ArchivoFaltante(
                    f"La corrida {corrida_id} esta EN_PROCESO y no tiene artefacto ni archivo."
                )
            sha256, contenido = None, archivo.contenido
    if sha256 is not None and not almacen_de(config_del_entorno).existe(sha256):
        raise ArtefactoFaltante(sha256)
    procesar_corrida(corrida_id, contenido)


MANEJADORES: dict[TipoTrabajo, Callable[[int], Any]] = {
    TipoTrabajo.INGESTA: _ingerir,
    TipoTrabajo.DECISION: ejecutar_decision,
    TipoTrabajo.TERRITORIAL: ejecutar_territorial,
    TipoTrabajo.RUTEO: ejecutar_ruteo,
}
"""Que ejecuta cada tipo de trabajo, con el id de su recurso. Todos son idempotentes: con el recurso
ya terminado no hacen nada, y por eso se pueden entregar mas de una vez."""


@dataclass(frozen=True)
class TrabajoProcesado:
    """Lo que hizo un worker con un trabajo que tomo."""

    trabajo_id: UUID
    tipo: TipoTrabajo
    intentos: int
    estado: EstadoTrabajo | None
    """Como lo dejo: COMPLETADO, PENDIENTE (vuelve a la cola despues de su espera) o FALLIDO. None
    si perdio el lease mientras lo ejecutaba: ya no le tocaba cerrarlo."""


def identificador_worker() -> str:
    """Quien es este proceso worker: su host, su pid y un UUID propio. Se genera una vez, al
    arrancar, y se usa en cada trabajo que toma. No es la identidad de una persona."""
    return f"{socket.gethostname()[:120]}:{os.getpid()}:{uuid4().hex}"


class _Latido:
    """Renueva el lease del trabajo desde otro hilo, cada `intervalo` segundos, mientras el motor
    trabaja en el hilo del worker. Usa su propia sesion en cada latido.

    Si un latido encuentra que el trabajo ya no es de este worker (su lease vencio y otro lo tomo),
    deja de latir y lo marca en `perdido`: el worker ya no lo cierra. Si un latido falla por otra
    razon, como la base sin conexion, lo deja en la bitacora y vuelve a intentar en el siguiente.
    """

    def __init__(
        self, trabajo_id: int, worker_id: str, lease_segundos: float, intervalo_segundos: float
    ) -> None:
        self._trabajo_id = trabajo_id
        self._worker_id = worker_id
        self._lease = lease_segundos
        self._intervalo = intervalo_segundos
        self._parar = threading.Event()
        self.perdido = threading.Event()
        self._hilo = threading.Thread(target=self._latir, name=f"latido-{trabajo_id}", daemon=True)

    def __enter__(self) -> _Latido:
        self._hilo.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._parar.set()
        # Un latido en curso termina en lo que tarda un UPDATE; el lease es un tope holgado.
        self._hilo.join(timeout=self._lease)

    def _latir(self) -> None:
        while not self._parar.wait(self._intervalo):
            try:
                vigente = cola.renovar(self._trabajo_id, self._worker_id, self._lease)
            except Exception:
                log.exception(
                    "trabajo %s: no se pudo renovar el lease; se intenta en el siguiente latido",
                    self._trabajo_id,
                )
                continue
            if not vigente:
                log.warning(
                    "trabajo %s: ya no es de %s; deja de latir", self._trabajo_id, self._worker_id
                )
                self.perdido.set()
                return


def procesar_un_trabajo(worker_id: str, config: Config | None = None) -> TrabajoProcesado | None:
    """Toma un trabajo, lo ejecuta y lo cierra. None si no habia ninguno que tomar."""
    config = config or config_del_entorno
    reclamo = cola.reclamar(worker_id, config.worker_lease_segundos)
    if reclamo is None:
        return None
    return procesar_reclamo(reclamo, worker_id, config)


def procesar_reclamo(
    reclamo: Reclamo, worker_id: str, config: Config | None = None
) -> TrabajoProcesado:
    """Ejecuta un trabajo que este worker ya tomo, con su latido, y lo cierra.

    Si su lease vencio en el ultimo intento, ya no se ejecuta: solo se cierra. Un error del motor o
    del worker no se propaga: se deja en la bitacora, con su traza, y el cierre decide que sigue.
    """
    config = config or config_del_entorno
    error: Exception | None = None
    if reclamo.ejecutar:
        latido = _Latido(
            reclamo.id, worker_id, config.worker_lease_segundos, config.worker_heartbeat_segundos
        )
        with latido:
            try:
                MANEJADORES[reclamo.tipo](reclamo.objetivo_id)
            except PERDIO_LA_CARRERA as exc:
                log.warning("trabajo %s: %s", reclamo.trabajo_id, exc)
            except Exception as exc:
                log.exception(
                    "trabajo %s: error del worker en el intento %s de %s",
                    reclamo.trabajo_id,
                    reclamo.intentos,
                    reclamo.max_intentos,
                )
                error = exc
        if latido.perdido.is_set():
            return _procesado(reclamo, None)
    return _cerrar(reclamo, worker_id, error, config)


def ejecutar_worker(
    config: Config | None = None,
    *,
    una_vez: bool = False,
    detener: threading.Event | None = None,
) -> TrabajoProcesado | None:
    """El worker: toma, ejecuta y cierra trabajos, uno a la vez, hasta que se le pide detenerse.
    Devuelve el ultimo trabajo que proceso, o None si no proceso ninguno.

    Con la cola vacia, espera `worker_poll_segundos` antes de volver a buscar. Un error inesperado
    del ciclo, como la base sin conexion, no lo detiene: queda en la bitacora, espera y sigue. Con
    SIGTERM o SIGINT deja de tomar trabajos nuevos y termina el que tiene; una segunda senal ya no
    espera. Con `una_vez`, procesa a lo mas un trabajo y sale, y un error no se oculta.
    """
    config = config or config_del_entorno
    detener = detener or threading.Event()
    worker_id = identificador_worker()
    previos = _escuchar_senales(detener)
    ultimo = None
    log.info(
        "worker %s: arranca (lease %ss, latido %ss, %s intentos, espera base %ss)",
        worker_id,
        config.worker_lease_segundos,
        config.worker_heartbeat_segundos,
        config.worker_max_intentos,
        config.worker_backoff_segundos,
    )
    try:
        while not detener.is_set():
            try:
                procesado = procesar_un_trabajo(worker_id, config)
            except Exception:
                if una_vez:
                    raise
                log.exception("worker %s: error inesperado; espera y sigue", worker_id)
                detener.wait(config.worker_poll_segundos)
                continue
            ultimo = procesado or ultimo
            if una_vez:
                break
            if procesado is None:
                detener.wait(config.worker_poll_segundos)
    finally:
        _restaurar_senales(previos)
        log.info("worker %s: se detiene", worker_id)
    return ultimo


def ingerir_en_primer_plano(
    ruta: str | Path,
    *,
    tolerancia: float | None = None,
    config: Config | None = None,
    contrato: str = VERSION_CONTRATO,
    fecha_corte: date | None = None,
) -> Corrida:
    """La ingesta del CLI: el archivo al almacen de artefactos y, en una transaccion, su artefacto,
    la corrida y su trabajo, con el trabajo ya tomado por este proceso; despues, la ingesta en
    primer plano, con su latido. No crea un flujo: no encadena ninguna otra etapa.

    Si el proceso muere a la mitad, el trabajo queda en la cola con su lease: cuando vence,
    cualquier worker termina la ingesta con el mismo artefacto. Propaga ArchivoDuplicado,
    ValueError si el archivo esta vacio, y FormatoNoSoportado o FormatoNoCorresponde si no es lo
    que dice su extension.
    """
    config = config or config_del_entorno
    ruta = Path(ruta)
    verificar_declaracion(contrato, fecha_corte)
    if ruta.stat().st_size == 0:
        raise ValueError(f"{ruta.name!r} esta vacio: no hay nada que ingerir.")
    with ruta.open("rb") as archivo:
        guardado = guardar_artefacto(almacen_de(config), archivo, ruta.name)
    worker_id = identificador_worker()
    with sesion() as s:
        corrida, trabajo = flujo.encolar_ingesta(
            s,
            origen=ruta.name,
            guardado=guardado,
            contrato=contrato,
            fecha_corte=fecha_corte,
            tolerancia=tolerancia,
            config=config,
            tomado_por=worker_id,
        )
        reclamo = cola.reclamo_de(trabajo)
    procesar_reclamo(reclamo, worker_id, config)
    with sesion() as s:
        return s.get_one(Corrida, corrida.id)


def _cerrar(
    reclamo: Reclamo, worker_id: str, error: Exception | None, config: Config
) -> TrabajoProcesado:
    """Cierra el trabajo segun como quedo su recurso, en una sola transaccion con lo que sigue.

    Lo primero es tomar la fila del trabajo, solo si sigue siendo de este worker: un dueno anterior
    que perdio el lease no lo cierra, ni toca su recurso.
    """
    with sesion() as s:
        if cola.tomar_para_cerrar(s, reclamo.id, worker_id) is None:
            log.warning(
                "trabajo %s: ya no es de %s; lo cierra quien lo tomo", reclamo.trabajo_id, worker_id
            )
            return _procesado(reclamo, None)
        estado = objetivos.estado(s, reclamo.tipo, reclamo.objetivo_id)
        if objetivos.es_terminal(reclamo.tipo, estado):
            _completar(s, reclamo, worker_id, config)
            cerrado = EstadoTrabajo.COMPLETADO
        elif reclamo.intentos < reclamo.max_intentos:
            espera = cola.espera_de_reintento(reclamo.intentos, config.worker_backoff_segundos)
            cola.devolver(
                s,
                reclamo.id,
                worker_id,
                espera_segundos=espera,
                error=_ultimo_error(reclamo, error),
            )
            cerrado = EstadoTrabajo.PENDIENTE
        elif objetivos.fallar(s, reclamo.tipo, reclamo.objetivo_id, _sin_terminar(reclamo)):
            cola.agotar(s, reclamo.id, worker_id, error=_ultimo_error(reclamo, error))
            if reclamo.flujo_id is not None:
                flujo.detener(s, reclamo.flujo_id, _flujo_sin_terminar(reclamo))
            cerrado = EstadoTrabajo.FALLIDO
        else:
            # El recurso termino mientras se cerraba el trabajo: lo termino un dueno anterior que
            # todavia lo estaba ejecutando. Se cierra como cualquier otro que ya termino.
            _completar(s, reclamo, worker_id, config)
            cerrado = EstadoTrabajo.COMPLETADO
        s.commit()
    log.info(
        "trabajo %s (%s, intento %s de %s): %s",
        reclamo.trabajo_id,
        reclamo.tipo,
        reclamo.intentos,
        reclamo.max_intentos,
        cerrado,
    )
    return _procesado(reclamo, cerrado)


def _completar(s: Session, reclamo: Reclamo, worker_id: str, config: Config) -> None:
    """El trabajo COMPLETADO y lo que sigue, en la misma transaccion: el flujo avanza o se
    detiene. Un crash no deja un hueco entre etapas. El artefacto de una ingesta se queda: es la
    evidencia de lo que se recibio."""
    cola.completar(s, reclamo.id, worker_id)
    if reclamo.flujo_id is not None:
        flujo.avanzar(
            s,
            reclamo.flujo_id,
            reclamo.tipo,
            reclamo.objetivo_id,
            max_intentos=config.worker_max_intentos,
        )


def _ultimo_error(reclamo: Reclamo, error: Exception | None) -> str:
    """Lo que el trabajo guarda de su ultimo error: corto y sin traza, ni SQL, ni datos. La traza
    esta en la bitacora."""
    if error is not None:
        return f"Error de worker ({type(error).__name__}); ver la bitacora."
    if not reclamo.ejecutar:
        return cola.LEASE_VENCIDO
    return "El motor termino sin dejar su recurso en un estado terminal; ver la bitacora."


def _sin_terminar(reclamo: Reclamo) -> str:
    """El detalle del recurso que se cierra FALLIDA porque su trabajo agoto los intentos."""
    return (
        f"Su trabajo agoto los {reclamo.max_intentos} intentos de la cola sin que terminara; ver "
        "la bitacora del worker."
    )


def _flujo_sin_terminar(reclamo: Reclamo) -> str:
    """El detalle del flujo que se detiene porque el trabajo de su etapa agoto los intentos."""
    etapa = EtapaFlujo(reclamo.tipo.value)
    reintento = (
        "Para reintentar, vuelve a subir el archivo."
        if etapa == EtapaFlujo.INGESTA
        else "Para reintentarla, reanuda el flujo."
    )
    return (
        f"La etapa {etapa} no termino: su trabajo agoto los {reclamo.max_intentos} intentos de la "
        f"cola. {reintento}"
    )


def _procesado(reclamo: Reclamo, estado: EstadoTrabajo | None) -> TrabajoProcesado:
    return TrabajoProcesado(reclamo.trabajo_id, reclamo.tipo, reclamo.intentos, estado)


SENALES = (signal.SIGTERM, signal.SIGINT)


def _escuchar_senales(detener: threading.Event) -> dict[int, Any]:
    """Con SIGTERM o SIGINT, el worker deja de tomar trabajos nuevos y termina el que tiene. La
    primera senal devuelve los manejadores de antes: con una segunda, el proceso ya no espera, y el
    lease recupera el trabajo que tenia. Solo en el hilo principal, el unico donde Python atiende
    senales; en otro hilo, el worker se detiene con `detener`."""
    if threading.current_thread() is not threading.main_thread():
        return {}
    previos: dict[int, Any] = {}

    def al_recibir(_numero: int, _marco: object) -> None:
        # Sin bitacora aqui: la senal puede llegar a media escritura de otra linea. El ciclo deja
        # constancia al detenerse.
        detener.set()
        _restaurar_senales(previos)

    for senal in SENALES:
        previos[senal] = signal.signal(senal, al_recibir)
    return previos


def _restaurar_senales(previos: dict[int, Any]) -> None:
    for senal, manejador in previos.items():
        signal.signal(senal, manejador)
