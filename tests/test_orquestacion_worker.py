"""El worker contra PostgreSQL: ejecutar, reintentar, agotar y recuperarse de un worker que muere.

Cada caida se simula sin matar procesos: un worker que toma un trabajo y nunca lo cierra, un motor
que confirma y un worker que no llega a cerrar, o una excepcion que nada atrapa, como la muerte del
proceso a media transaccion. Los leases se vencen en la base, con el reloj de PostgreSQL, en lugar
de esperarlos; solo las pruebas del latido esperan, y menos de lo que tarda un lease en vencer dos
veces. Las pruebas de la ultima seccion no tocan la base.
"""

from __future__ import annotations

import hashlib
import re
import signal
import threading
import time
from datetime import date, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import func, update
from sqlmodel import select

from motor_cartera.config import Config
from motor_cartera.db.modelos import (
    ArchivoCorrida,
    ArtefactoFuente,
    Corrida,
    Cuenta,
    DecisionCuenta,
    EjecucionDecision,
    EjecucionRuteo,
    EjecucionTerritorial,
    EstadoCorrida,
    EstadoFlujo,
    EstadoTrabajo,
    EtapaFlujo,
    FlujoOrquestacion,
    ParadaRuta,
    RutaTerritorial,
    TipoTrabajo,
    TrabajoOrquestacion,
)
from motor_cartera.db.sesion import sesion
from motor_cartera.decision import ejecuciones as decision
from motor_cartera.generador.sintetico import generar_archivo
from motor_cartera.orquestacion import cola, worker
from motor_cartera.orquestacion.flujo import crear_flujo_ingesta, reanudar_flujo
from motor_cartera.orquestacion.worker import (
    ejecutar_worker,
    identificador_worker,
    procesar_reclamo,
    procesar_un_trabajo,
)

en_la_base = pytest.mark.usefixtures("bd")

CORTE = date(2026, 9, 30)
CONFIG = Config()


class Muerte(BaseException):  # noqa: N818
    """La muerte del proceso: ningun `except Exception` la atrapa, como un SIGKILL a media
    transaccion. La sesion que la ve pasar revierte, igual que PostgreSQL al perder la conexion."""


def _crear(tmp_path: Path, config: Config = CONFIG, *, n: int = 200) -> tuple[int, int]:
    """Una corrida con su flujo, como la crea la API. Devuelve los ids de la corrida y del flujo."""
    ruta = generar_archivo(tmp_path / f"c{n}.csv", n=n, semilla=1, fecha_corte=CORTE)
    with sesion() as s:
        corrida, flujo = crear_flujo_ingesta(
            s, origen=ruta.name, contenido=ruta.read_bytes(), tolerancia=0.05, config=config
        )
        return corrida.id, flujo.id


def _flujo(flujo_id: int) -> FlujoOrquestacion:
    with sesion() as s:
        return s.get_one(FlujoOrquestacion, flujo_id)


def _trabajos() -> list[TrabajoOrquestacion]:
    with sesion() as s:
        return list(s.exec(select(TrabajoOrquestacion).order_by(TrabajoOrquestacion.id)).all())


def _trabajo(tipo: TipoTrabajo) -> TrabajoOrquestacion:
    (trabajo,) = [t for t in _trabajos() if t.tipo == tipo]
    return trabajo


def _de(modelo, objetivo_id: int):
    with sesion() as s:
        return s.get_one(modelo, objetivo_id)


def _cuantas(modelo) -> int:
    with sesion() as s:
        return s.exec(select(func.count()).select_from(modelo)).one()


def _ahora():
    with sesion() as s:
        return s.exec(select(func.now())).one()


def _cambiar(trabajo_id: int, **valores) -> None:
    with sesion() as s:
        s.execute(
            update(TrabajoOrquestacion)
            .where(TrabajoOrquestacion.id == trabajo_id)
            .values(**valores)
        )
        s.commit()


def _vencer(trabajo_id: int) -> None:
    """El lease vence ya: su worker dejo de latir."""
    _cambiar(trabajo_id, lease_hasta=func.now() - timedelta(seconds=1))


def _en_otro_hilo(funcion, *argumentos) -> tuple[threading.Thread, dict]:
    """Corre `funcion` en otro hilo, como otro worker, y guarda lo que devuelve o lo que levanta."""
    salida: dict = {}

    def correr() -> None:
        try:
            salida["resultado"] = funcion(*argumentos)
        except BaseException as exc:
            salida["error"] = exc

    hilo = threading.Thread(target=correr, daemon=True)
    hilo.start()
    return hilo, salida


def _hasta(condicion, limite: float = 30.0) -> bool:
    """Si `condicion` se cumple antes de `limite` segundos. Pregunta cada 50 ms."""
    hasta = time.monotonic() + limite
    while time.monotonic() < hasta:
        if condicion():
            return True
        time.sleep(0.05)
    return False


def _ingesta_que_espera(monkeypatch) -> tuple[threading.Event, threading.Event]:
    """La ingesta del worker se detiene hasta que la prueba la suelta: avisa en `empezo` y espera
    `soltar`. Despues hace la ingesta de verdad."""
    empezo, soltar = threading.Event(), threading.Event()
    ingerir = worker.MANEJADORES[TipoTrabajo.INGESTA]

    def ingesta(corrida_id: int) -> None:
        empezo.set()
        assert soltar.wait(30)
        ingerir(corrida_id)

    monkeypatch.setitem(worker.MANEJADORES, TipoTrabajo.INGESTA, ingesta)
    return empezo, soltar


# --- de punta a punta ----------------------------------------------------------------------------


@en_la_base
def test_el_worker_lleva_una_cartera_de_la_ingesta_al_ruteo(tmp_path, trabajar):
    # La prueba no llama a ningun motor: solo crea el flujo y deja trabajar al worker.
    corrida_id, flujo_id = _crear(tmp_path)

    procesados = trabajar()

    assert [(p.tipo, p.estado, p.intentos) for p in procesados] == [
        (TipoTrabajo.INGESTA, EstadoTrabajo.COMPLETADO, 1),
        (TipoTrabajo.DECISION, EstadoTrabajo.COMPLETADO, 1),
        (TipoTrabajo.TERRITORIAL, EstadoTrabajo.COMPLETADO, 1),
        (TipoTrabajo.RUTEO, EstadoTrabajo.COMPLETADO, 1),
    ]
    completado = _flujo(flujo_id)
    assert (completado.estado, completado.etapa) == (EstadoFlujo.COMPLETADO, EtapaFlujo.COMPLETADA)
    # Cada trabajo termino sin dueno ni lease, y es el de un recurso que termino EXITOSA.
    trabajos = _trabajos()
    assert [t.tipo for t in trabajos] == list(TipoTrabajo)
    assert all(t.flujo_id == flujo_id and t.terminado_en is not None for t in trabajos)
    assert not any(t.worker_id or t.lease_hasta or t.ultimo_error for t in trabajos)
    objetivos = [
        _de(Corrida, corrida_id),
        _de(EjecucionDecision, completado.ejecucion_decision_id),
        _de(EjecucionTerritorial, completado.ejecucion_territorial_id),
        _de(EjecucionRuteo, completado.ejecucion_ruteo_id),
    ]
    assert [objetivo.estado for objetivo in objetivos] == ["EXITOSA"] * 4
    # Lo publicado, una vez: una decision por cuenta, y las rutas y paradas que dice el ruteo.
    corrida, decidida, _, ruteada = objetivos
    assert _cuantas(Cuenta) == _cuantas(DecisionCuenta) == decidida.cuentas_decididas
    assert corrida.filas_validas == 200
    assert (_cuantas(RutaTerritorial), _cuantas(ParadaRuta)) == (
        ruteada.rutas_publicadas,
        ruteada.paradas_publicadas,
    )
    assert ruteada.rutas_publicadas > 0
    # El artefacto sigue ahi, y nada se escribio en la tabla heredada de v0.5.
    assert (_cuantas(ArtefactoFuente), _cuantas(ArchivoCorrida)) == (1, 0)


@en_la_base
def test_dos_workers_se_reparten_la_cola_sin_ejecutar_dos_veces_ningun_trabajo(
    tmp_path, monkeypatch
):
    # Dos workers de verdad, cada uno con su ciclo, su hilo y su conexion, sobre dos flujos. Las dos
    # ingestas se esperan una a la otra: cada worker tiene que estar ejecutando la suya al mismo
    # tiempo, y no la misma. Lo que sigue se lo reparten como caiga, y cada trabajo se ejecuta una
    # sola vez, a la primera.
    flujos = [_crear(tmp_path, n=n)[1] for n in (120, 150)]
    juntas = threading.Barrier(2, timeout=30)
    candado = threading.Lock()
    ejecutados: list[tuple[str, TipoTrabajo, int]] = []

    def anotado(tipo: TipoTrabajo, manejar):
        def manejador(objetivo_id: int) -> None:
            with candado:
                ejecutados.append((threading.current_thread().name, tipo, objetivo_id))
            if tipo == TipoTrabajo.INGESTA:
                juntas.wait()
            manejar(objetivo_id)

        return manejador

    for tipo, manejar in list(worker.MANEJADORES.items()):
        monkeypatch.setitem(worker.MANEJADORES, tipo, anotado(tipo, manejar))
    detener = threading.Event()
    config = Config(worker_poll_segundos=0.05)
    hilos = [
        threading.Thread(
            target=ejecutar_worker,
            args=(config,),
            kwargs={"detener": detener},
            name=nombre,
            daemon=True,
        )
        for nombre in ("worker-a", "worker-b")
    ]
    for hilo in hilos:
        hilo.start()
    try:
        completos = _hasta(
            lambda: all(_flujo(f).estado == EstadoFlujo.COMPLETADO for f in flujos), limite=120
        )
    finally:
        detener.set()
        for hilo in hilos:
            hilo.join(timeout=30)

    assert completos
    assert not any(hilo.is_alive() for hilo in hilos)
    # Ocho trabajos, cada uno ejecutado una vez; las dos ingestas, cada una en su worker.
    assert len(ejecutados) == len({(tipo, objetivo) for _, tipo, objetivo in ejecutados}) == 8
    ingestas = {nombre for nombre, tipo, _ in ejecutados if tipo == TipoTrabajo.INGESTA}
    assert ingestas == {"worker-a", "worker-b"}
    trabajos = _trabajos()
    assert len(trabajos) == 8
    assert {(t.estado, t.intentos) for t in trabajos} == {(EstadoTrabajo.COMPLETADO, 1)}
    # Y lo publicado, una vez por flujo.
    assert [_cuantas(m) for m in (EjecucionDecision, EjecucionTerritorial, EjecucionRuteo)] == [
        2,
        2,
        2,
    ]
    assert _cuantas(DecisionCuenta) == _cuantas(Cuenta)


# --- reintentar y agotar -------------------------------------------------------------------------


@en_la_base
def test_un_error_del_worker_devuelve_el_trabajo_con_su_espera_y_sin_traza(
    tmp_path, monkeypatch, caplog
):
    corrida_id, _ = _crear(tmp_path)

    def revienta(_corrida_id: int) -> None:
        raise RuntimeError("SELECT * FROM cuenta WHERE clave = 'secreta' -- password=hunter2")

    monkeypatch.setitem(worker.MANEJADORES, TipoTrabajo.INGESTA, revienta)
    antes = _ahora()

    procesado = procesar_un_trabajo("worker-a", Config(worker_backoff_segundos=3))

    despues = _ahora()
    assert (procesado.estado, procesado.intentos) == (EstadoTrabajo.PENDIENTE, 1)
    trabajo = _trabajo(TipoTrabajo.INGESTA)
    assert (trabajo.estado, trabajo.worker_id, trabajo.lease_hasta, trabajo.terminado_en) == (
        EstadoTrabajo.PENDIENTE,
        None,
        None,
        None,
    )
    # El error, corto y seguro: sin SQL, sin credenciales y sin traza. La traza, en la bitacora.
    assert trabajo.ultimo_error == "Error de worker (RuntimeError); ver la bitacora."
    (registro,) = [r for r in caplog.records if r.name == worker.__name__ and r.exc_info]
    assert "hunter2" in str(registro.exc_info[1])
    # Vuelve a la cola despues de la espera del primer intento; mientras, nadie lo toma.
    espera = timedelta(seconds=3)
    assert antes + espera <= trabajo.disponible_desde <= despues + espera
    assert procesar_un_trabajo("worker-b", CONFIG) is None
    # La corrida sigue EN_PROCESO, con su artefacto: todavia hay quien la termine.
    assert _de(Corrida, corrida_id).estado == EstadoCorrida.EN_PROCESO
    assert _cuantas(ArtefactoFuente) == 1


@en_la_base
def test_una_ingesta_sin_su_objeto_en_el_almacen_es_un_error_del_worker_y_no_toca_la_corrida(
    tmp_path, almacen
):
    # Como un volumen que no se monto: la base registra el artefacto y el almacen no lo tiene. No
    # es la corrida la que esta mal: el trabajo se reintenta, y cuando el objeto vuelve, termina.
    # Un archivo propio (n=151): el almacen de las pruebas es compartido.
    corrida_id, _ = _crear(tmp_path, n=151)
    with sesion() as s:
        sha256 = s.get_one(ArtefactoFuente, _de(Corrida, corrida_id).artefacto_fuente_id).sha256
    ruta = almacen.ruta(sha256)
    contenido = ruta.read_bytes()
    ruta.chmod(0o600)
    ruta.unlink()

    procesado = procesar_un_trabajo("worker-a", CONFIG)

    assert procesado.estado == EstadoTrabajo.PENDIENTE
    assert _trabajo(TipoTrabajo.INGESTA).ultimo_error == (
        "Error de worker (ArtefactoFaltante); ver la bitacora."
    )
    assert _de(Corrida, corrida_id).estado == EstadoCorrida.EN_PROCESO

    almacen.guardar(__import__("io").BytesIO(contenido))
    _cambiar(_trabajo(TipoTrabajo.INGESTA).id, disponible_desde=func.now())
    procesado = procesar_un_trabajo("worker-a", CONFIG)

    assert (procesado.intentos, procesado.estado) == (2, EstadoTrabajo.COMPLETADO)
    assert _de(Corrida, corrida_id).estado == EstadoCorrida.EXITOSA


@en_la_base
def test_un_objeto_danado_deja_la_corrida_fallida_y_dice_por_que(tmp_path, almacen):
    # Los bytes ya no son los de su firma: reintentar no lo arregla. La corrida termina FALLIDA,
    # con el motivo, y no publica nada. Un archivo propio (n=137), porque se dana a proposito.
    corrida_id, _ = _crear(tmp_path, n=137)
    with sesion() as s:
        sha256 = s.get_one(ArtefactoFuente, _de(Corrida, corrida_id).artefacto_fuente_id).sha256
    ruta = almacen.ruta(sha256)
    ruta.chmod(0o600)
    ruta.write_bytes(ruta.read_bytes().replace(b"CONSUMO", b"TARJETA", 1))

    procesado = procesar_un_trabajo("worker-a", CONFIG)

    assert procesado.estado == EstadoTrabajo.COMPLETADO
    corrida = _de(Corrida, corrida_id)
    assert corrida.estado == EstadoCorrida.FALLIDA
    assert f"El artefacto {sha256} esta danado" in corrida.detalle
    assert _cuantas(Cuenta) == 0


@en_la_base
def test_una_corrida_de_v05_que_seguia_en_la_cola_se_procesa_con_su_archivo_heredado(tmp_path):
    # Antes de la 0007 la corrida guardaba su archivo en BYTEA y no tenia artefacto. Si seguia en
    # la cola al migrar, el worker la termina con ese archivo, y ya no lo borra.
    corrida_id, _ = _crear(tmp_path)
    with sesion() as s:
        corrida = s.get_one(Corrida, corrida_id)
        sha256 = s.get_one(ArtefactoFuente, corrida.artefacto_fuente_id).sha256
        contenido = generar_archivo(tmp_path / "c200.csv", n=200, semilla=1, fecha_corte=CORTE)
        datos = contenido.read_bytes()
        assert hashlib.sha256(datos).hexdigest() == sha256
        corrida.artefacto_fuente_id = None  # como la dejaba la v0.5
        s.add(corrida)
        s.add(ArchivoCorrida(corrida_id=corrida_id, contenido=datos, tamano_bytes=len(datos)))
        s.commit()

    procesado = procesar_un_trabajo("worker-a", CONFIG)

    assert procesado.estado == EstadoTrabajo.COMPLETADO
    assert _de(Corrida, corrida_id).estado == EstadoCorrida.EXITOSA
    assert _cuantas(ArchivoCorrida) == 1


@en_la_base
def test_un_motor_que_vuelve_sin_terminar_su_recurso_es_un_error_del_worker(tmp_path, monkeypatch):
    _crear(tmp_path)
    monkeypatch.setitem(worker.MANEJADORES, TipoTrabajo.INGESTA, lambda _corrida_id: None)

    procesado = procesar_un_trabajo("worker-a", CONFIG)

    assert procesado.estado == EstadoTrabajo.PENDIENTE
    assert _trabajo(TipoTrabajo.INGESTA).ultimo_error == (
        "El motor termino sin dejar su recurso en un estado terminal; ver la bitacora."
    )


@en_la_base
def test_cada_intento_espera_el_doble_y_sin_intentos_todo_queda_fallido(tmp_path, monkeypatch):
    # Un trabajo que siempre falla antes de terminar su recurso se toma exactamente max_intentos
    # veces, con esperas de 1, 2 y 4 segundos entre una y otra, y despues queda FALLIDO con su
    # corrida y su flujo. No hay un ciclo sin fin.
    config = Config(worker_max_intentos=4, worker_backoff_segundos=1)
    corrida_id, flujo_id = _crear(tmp_path, config)
    llamadas = []

    def siempre_falla(corrida_id: int) -> None:
        llamadas.append(corrida_id)
        raise ConnectionError("la base no contesta")

    monkeypatch.setitem(worker.MANEJADORES, TipoTrabajo.INGESTA, siempre_falla)

    for intento, espera in ((1, 1), (2, 2), (3, 4)):
        antes = _ahora()
        procesado = procesar_un_trabajo("worker-a", config)
        despues = _ahora()
        assert (procesado.intentos, procesado.estado) == (intento, EstadoTrabajo.PENDIENTE)
        disponible = _trabajo(TipoTrabajo.INGESTA).disponible_desde
        assert (
            antes + timedelta(seconds=espera) <= disponible <= despues + timedelta(seconds=espera)
        )
        _cambiar(_trabajo(TipoTrabajo.INGESTA).id, disponible_desde=func.now())

    ultimo = procesar_un_trabajo("worker-a", config)

    assert (ultimo.intentos, ultimo.estado) == (4, EstadoTrabajo.FALLIDO)
    assert len(llamadas) == 4
    assert procesar_un_trabajo("worker-a", config) is None
    trabajo = _trabajo(TipoTrabajo.INGESTA)
    assert (trabajo.estado, trabajo.intentos, trabajo.worker_id, trabajo.lease_hasta) == (
        EstadoTrabajo.FALLIDO,
        4,
        None,
        None,
    )
    assert trabajo.terminado_en is not None
    assert trabajo.ultimo_error == "Error de worker (ConnectionError); ver la bitacora."
    # Su recurso, FALLIDA con el motivo; su archivo, borrado; su flujo, detenido.
    corrida = _de(Corrida, corrida_id)
    assert (corrida.estado, corrida.detalle) == (
        EstadoCorrida.FALLIDA,
        "Su trabajo agoto los 4 intentos de la cola sin que terminara; ver la bitacora del worker.",
    )
    assert corrida.terminada_en is not None
    assert _cuantas(ArtefactoFuente) == 1  # su artefacto se conserva
    detenido = _flujo(flujo_id)
    assert (detenido.estado, detenido.etapa) == (EstadoFlujo.DETENIDO, EtapaFlujo.INGESTA)
    assert detenido.detalle == (
        "La etapa INGESTA no termino: su trabajo agoto los 4 intentos de la cola. Para reintentar, "
        "vuelve a subir el archivo."
    )


@en_la_base
def test_una_decision_que_agota_sus_intentos_queda_fallida_y_su_flujo_se_reanuda(
    tmp_path, monkeypatch, trabajar
):
    config = Config(worker_max_intentos=1)
    _, flujo_id = _crear(tmp_path, config)
    procesar_un_trabajo("worker-a", config)  # la ingesta
    with monkeypatch.context() as parche:
        parche.setitem(worker.MANEJADORES, TipoTrabajo.DECISION, lambda _ejecucion_id: None)

        agotado = procesar_un_trabajo("worker-a", config)

    assert (agotado.tipo, agotado.estado) == (TipoTrabajo.DECISION, EstadoTrabajo.FALLIDO)
    detenido = _flujo(flujo_id)
    fallida = _de(EjecucionDecision, detenido.ejecucion_decision_id)
    # Nada publicado, y el motivo.
    assert (fallida.estado, fallida.cuentas_decididas) == ("FALLIDA", 0)
    assert fallida.detalle.startswith("Su trabajo agoto los 1 intentos de la cola")
    assert _cuantas(DecisionCuenta) == 0
    assert (detenido.estado, detenido.etapa) == (EstadoFlujo.DETENIDO, EtapaFlujo.DECISION)
    assert detenido.detalle.endswith("Para reintentarla, reanuda el flujo.")

    with sesion() as s:
        reanudar_flujo(s, detenido.flujo_id, config=CONFIG)
    trabajar()

    assert _flujo(flujo_id).estado == EstadoFlujo.COMPLETADO


# --- un worker que muere --------------------------------------------------------------------------


@en_la_base
def test_un_worker_que_muere_antes_de_ejecutar_deja_su_trabajo_a_otro(tmp_path, trabajar):
    # A. El worker toma el trabajo y muere sin ejecutarlo: nadie se lo quita mientras su lease siga
    # vigente, y cuando vence, otro lo toma y lo termina.
    _, flujo_id = _crear(tmp_path)
    tomado = cola.reclamar("worker-muerto", 60)
    assert procesar_un_trabajo("worker-b", CONFIG) is None

    _vencer(tomado.id)
    procesado = procesar_un_trabajo("worker-b", CONFIG)

    assert (procesado.trabajo_id, procesado.intentos, procesado.estado) == (
        tomado.trabajo_id,
        2,
        EstadoTrabajo.COMPLETADO,
    )
    assert _trabajo(TipoTrabajo.INGESTA).ultimo_error == cola.LEASE_VENCIDO
    trabajar()
    assert _flujo(flujo_id).estado == EstadoFlujo.COMPLETADO


@en_la_base
def test_si_el_worker_muere_despues_del_commit_del_motor_otro_cierra_sin_duplicar(tmp_path, caplog):
    # B. El motor confirmo la decision EXITOSA y el worker murio antes de cerrar su trabajo. Cuando
    # vence el lease, otro worker toma el mismo trabajo: el motor ve la decision terminada y no hace
    # nada, el trabajo queda COMPLETADO y el flujo avanza. Ninguna decision se duplica.
    _, flujo_id = _crear(tmp_path)
    procesar_un_trabajo("worker-a", CONFIG)  # la ingesta
    tomado = cola.reclamar("worker-muerto", 60)
    decision.ejecutar_decision(tomado.objetivo_id)  # el motor confirma; el worker no cierra
    publicada = _de(EjecucionDecision, tomado.objetivo_id)
    assert publicada.estado == "EXITOSA"
    assert _trabajo(TipoTrabajo.DECISION).estado == EstadoTrabajo.EJECUTANDO
    assert _flujo(flujo_id).etapa == EtapaFlujo.DECISION  # todavia no encadeno nada

    _vencer(tomado.id)
    procesado = procesar_un_trabajo("worker-b", CONFIG)

    assert (procesado.trabajo_id, procesado.intentos, procesado.estado) == (
        tomado.trabajo_id,
        2,
        EstadoTrabajo.COMPLETADO,
    )
    assert any(
        "ya termino EXITOSA; no se vuelve a ejecutar" in r.getMessage() for r in caplog.records
    )
    assert _de(EjecucionDecision, tomado.objetivo_id).model_dump() == publicada.model_dump()
    assert _cuantas(DecisionCuenta) == publicada.cuentas_decididas == _cuantas(Cuenta)
    avanzado = _flujo(flujo_id)
    assert avanzado.etapa == EtapaFlujo.TERRITORIAL
    assert _de(EjecucionTerritorial, avanzado.ejecucion_territorial_id).estado == "EN_PROCESO"
    assert _trabajo(TipoTrabajo.TERRITORIAL).estado == EstadoTrabajo.PENDIENTE


@en_la_base
def test_si_el_proceso_muere_a_media_transaccion_el_motor_se_vuelve_a_ejecutar_entero(
    tmp_path, monkeypatch
):
    # C. El proceso muere con la transaccion del motor abierta, con decisiones ya insertadas:
    # PostgreSQL la revierte, la ejecucion sigue EN_PROCESO y el trabajo EJECUTANDO. Cuando vence el
    # lease, otro worker la ejecuta desde el principio y publica todo, una sola vez.
    _crear(tmp_path)
    procesar_un_trabajo("worker-a", CONFIG)  # la ingesta
    decidir = decision.decidir_cuenta
    evaluadas = []

    def muere_a_la_mitad(entrada):
        evaluadas.append(entrada)
        if len(evaluadas) == 150:
            raise Muerte()
        return decidir(entrada)

    monkeypatch.setattr(decision, "decidir_cuenta", muere_a_la_mitad)
    with pytest.raises(Muerte):
        procesar_un_trabajo("worker-muerto", Config(worker_lease_segundos=60))
    trabajo = _trabajo(TipoTrabajo.DECISION)
    assert (trabajo.estado, trabajo.worker_id) == (EstadoTrabajo.EJECUTANDO, "worker-muerto")
    assert _de(EjecucionDecision, trabajo.ejecucion_decision_id).estado == "EN_PROCESO"
    assert _cuantas(DecisionCuenta) == 0  # lo de la transaccion abierta se revirtio

    monkeypatch.setattr(decision, "decidir_cuenta", decidir)
    _vencer(trabajo.id)
    procesado = procesar_un_trabajo("worker-b", CONFIG)

    assert (procesado.intentos, procesado.estado) == (2, EstadoTrabajo.COMPLETADO)
    publicada = _de(EjecucionDecision, trabajo.ejecucion_decision_id)
    assert (publicada.estado, publicada.cuentas_decididas) == ("EXITOSA", 200)
    assert _cuantas(DecisionCuenta) == 200


@en_la_base
def test_mientras_late_ningun_otro_worker_le_quita_el_trabajo(tmp_path, monkeypatch):
    # C. Con un lease de 2 segundos y un latido cada 0.1, el worker A tarda mas de dos leases en
    # su ingesta. Sin latido, su lease ya habria vencido; con latido, B no lo puede tomar.
    lease = 2.0
    config = Config(worker_lease_segundos=lease, worker_heartbeat_segundos=0.1)
    _crear(tmp_path)
    empezo, soltar = _ingesta_que_espera(monkeypatch)
    hilo, salida = _en_otro_hilo(procesar_un_trabajo, "worker-a", config)
    try:
        assert empezo.wait(30)
        tomado_en = _trabajo(TipoTrabajo.INGESTA).tomado_en
        # Pasa mas de un lease entero desde que lo tomo, con el reloj de la base.
        assert _hasta(lambda: _ahora() > tomado_en + timedelta(seconds=lease * 1.25))

        trabajo = _trabajo(TipoTrabajo.INGESTA)
        assert trabajo.worker_id == "worker-a"
        assert trabajo.latido_en > tomado_en
        assert trabajo.lease_hasta > _ahora()
        assert cola.reclamar("worker-b", lease) is None
    finally:
        soltar.set()
        hilo.join(timeout=30)

    assert (salida["resultado"].estado, salida["resultado"].intentos) == (
        EstadoTrabajo.COMPLETADO,
        1,
    )


@en_la_base
def test_el_dueno_anterior_no_cierra_el_trabajo_que_otro_tomo(tmp_path, monkeypatch):
    # D. A tarda y deja vencer su lease; B toma el mismo trabajo. A termina su ingesta, pero al
    # cerrar ya no es el dueno: no cierra el trabajo, ni encadena nada. El que lo cierra es B. Lo
    # que A alcanzo a publicar lo protegio el bloqueo de la corrida, no el lease.
    config = Config(worker_lease_segundos=60, worker_heartbeat_segundos=30)  # A no late aqui
    corrida_id, flujo_id = _crear(tmp_path)
    empezo, soltar = _ingesta_que_espera(monkeypatch)
    hilo, salida = _en_otro_hilo(procesar_un_trabajo, "worker-a", config)
    try:
        assert empezo.wait(30)
        trabajo_id = _trabajo(TipoTrabajo.INGESTA).id
        _vencer(trabajo_id)
        de_b = cola.reclamar("worker-b", 60)
        assert (de_b.id, de_b.intentos) == (trabajo_id, 2)
    finally:
        soltar.set()
        hilo.join(timeout=30)

    assert salida["resultado"].estado is None  # A ya no lo cierra
    trabajo = _trabajo(TipoTrabajo.INGESTA)
    assert (trabajo.estado, trabajo.worker_id) == (EstadoTrabajo.EJECUTANDO, "worker-b")
    assert _de(Corrida, corrida_id).estado == EstadoCorrida.EXITOSA
    assert _flujo(flujo_id).etapa == EtapaFlujo.INGESTA

    monkeypatch.setitem(worker.MANEJADORES, TipoTrabajo.INGESTA, worker._ingerir)
    cerrado = procesar_reclamo(de_b, "worker-b", config)

    assert cerrado.estado == EstadoTrabajo.COMPLETADO
    assert _flujo(flujo_id).etapa == EtapaFlujo.DECISION
    assert _cuantas(Cuenta) == 200


@en_la_base
def test_el_latido_se_detiene_cuando_el_trabajo_ya_no_es_suyo(tmp_path, monkeypatch, caplog):
    _crear(tmp_path)
    tomado = cola.reclamar("worker-a", 60)
    renovar = cola.renovar
    llamadas = []

    def falla_una_vez(*argumentos):
        llamadas.append(argumentos)
        if len(llamadas) == 1:
            raise ConnectionError("la base no contesta")
        return renovar(*argumentos)

    monkeypatch.setattr(cola, "renovar", falla_una_vez)
    latido = worker._Latido(tomado.id, "worker-a", 60, 0.05)

    with latido:
        # Un latido que falla no lo detiene: vuelve a latir.
        assert _hasta(lambda: len(llamadas) >= 3)
        assert not latido.perdido.is_set()
        _cambiar(tomado.id, worker_id="worker-b")  # otro worker lo tomo
        assert latido.perdido.wait(10)

    assert any("no se pudo renovar el lease" in r.getMessage() for r in caplog.records)
    vistas = len(llamadas)
    time.sleep(0.2)
    assert len(llamadas) == vistas  # dejo de latir


@en_la_base
def test_si_muere_el_ultimo_intento_el_siguiente_worker_solo_lo_cierra(tmp_path, monkeypatch):
    # Con un solo intento, el worker que lo tenia muere. El siguiente ya no lo ejecuta: lo cierra
    # FALLIDO, con su corrida y su flujo, y el motivo es el lease vencido.
    config = Config(worker_max_intentos=1)
    corrida_id, flujo_id = _crear(tmp_path, config)
    tomado = cola.reclamar("worker-muerto", 60)
    _vencer(tomado.id)
    ejecutadas = []
    monkeypatch.setitem(worker.MANEJADORES, TipoTrabajo.INGESTA, ejecutadas.append)

    procesado = procesar_un_trabajo("worker-b", config)

    assert ejecutadas == []
    assert (procesado.intentos, procesado.estado) == (1, EstadoTrabajo.FALLIDO)
    trabajo = _trabajo(TipoTrabajo.INGESTA)
    assert (trabajo.estado, trabajo.intentos, trabajo.ultimo_error) == (
        EstadoTrabajo.FALLIDO,
        1,
        cola.LEASE_VENCIDO,
    )
    assert _de(Corrida, corrida_id).estado == EstadoCorrida.FALLIDA
    assert _flujo(flujo_id).estado == EstadoFlujo.DETENIDO


@en_la_base
def test_si_el_recurso_termina_mientras_se_cierra_el_trabajo_se_completa(tmp_path, monkeypatch):
    # Al cerrar, el worker ve el recurso EN_PROCESO y sin intentos que le queden; para cuando lo
    # quiere dejar FALLIDO, otro proceso que todavia lo ejecutaba ya lo termino. El cierre
    # condicionado no lo degrada, y el trabajo queda COMPLETADO, con el flujo avanzando.
    config = Config(worker_max_intentos=1)
    corrida_id, flujo_id = _crear(tmp_path, config)
    estado = worker.objetivos.estado
    vistas = []

    def lo_ve_todavia_en_proceso(s, tipo, objetivo_id):
        vistas.append(estado(s, tipo, objetivo_id))
        return "EN_PROCESO" if len(vistas) == 1 else vistas[-1]

    monkeypatch.setattr(worker.objetivos, "estado", lo_ve_todavia_en_proceso)

    procesado = procesar_un_trabajo("worker-a", config)

    assert vistas[0] == "EXITOSA"  # la ingesta ya habia terminado de verdad
    assert procesado.estado == EstadoTrabajo.COMPLETADO
    assert _de(Corrida, corrida_id).estado == EstadoCorrida.EXITOSA
    assert _flujo(flujo_id).etapa == EtapaFlujo.DECISION


@en_la_base
def test_un_worker_que_pierde_el_lease_mientras_ejecuta_deja_de_latir_y_no_cierra(
    tmp_path, monkeypatch, caplog
):
    config = Config(worker_lease_segundos=60, worker_heartbeat_segundos=0.05)
    _crear(tmp_path)
    empezo, soltar = _ingesta_que_espera(monkeypatch)
    hilo, salida = _en_otro_hilo(procesar_un_trabajo, "worker-a", config)
    try:
        assert empezo.wait(30)
        trabajo_id = _trabajo(TipoTrabajo.INGESTA).id
        _cambiar(trabajo_id, worker_id="worker-b")  # otro worker se lo quedo
        assert _hasta(lambda: any("deja de latir" in r.getMessage() for r in caplog.records))
    finally:
        soltar.set()
        hilo.join(timeout=30)

    assert salida["resultado"].estado is None
    assert _trabajo(TipoTrabajo.INGESTA).worker_id == "worker-b"


@en_la_base
def test_una_decision_que_pierde_la_carrera_en_el_worker_cierra_su_trabajo(
    tmp_path, monkeypatch, caplog
):
    # Mientras el worker decide, otra ejecucion de la misma corrida aparece EXITOSA, a mano. El
    # motor deja la suya FALLIDA y avisa que perdio: no es un error del worker, el trabajo queda
    # COMPLETADO y el flujo se detiene.
    _, flujo_id = _crear(tmp_path)
    procesar_un_trabajo("worker-a", CONFIG)  # la ingesta
    corrida_id = _flujo(flujo_id).corrida_id
    cerrar = decision._cerrar

    def otra_publica_primero(s, ejecucion, evaluadas):
        with sesion() as otra:
            otra.add(
                EjecucionDecision(
                    corrida_id=corrida_id,
                    version_reglas=decision.VERSION_REGLAS_DECISION,
                    estado="EXITOSA",
                )
            )
            otra.commit()
        cerrar(s, ejecucion, evaluadas)

    monkeypatch.setattr(decision, "_cerrar", otra_publica_primero)

    procesado = procesar_un_trabajo("worker-a", CONFIG)

    assert (procesado.tipo, procesado.estado) == (TipoTrabajo.DECISION, EstadoTrabajo.COMPLETADO)
    assert any("ya se decidio con decision/v1" in r.getMessage() for r in caplog.records)
    assert not any(r.exc_info for r in caplog.records if r.name == worker.__name__)
    detenido = _flujo(flujo_id)
    assert (detenido.estado, detenido.etapa) == (EstadoFlujo.DETENIDO, EtapaFlujo.DECISION)
    assert _de(EjecucionDecision, detenido.ejecucion_decision_id).estado == "FALLIDA"


# --- el ciclo del worker -------------------------------------------------------------------------


@en_la_base
def test_una_vez_procesa_a_lo_mas_un_trabajo(tmp_path):
    assert ejecutar_worker(CONFIG, una_vez=True) is None
    _crear(tmp_path)

    procesado = ejecutar_worker(CONFIG, una_vez=True)

    assert (procesado.tipo, procesado.estado) == (TipoTrabajo.INGESTA, EstadoTrabajo.COMPLETADO)
    assert [t.estado for t in _trabajos()] == [EstadoTrabajo.COMPLETADO, EstadoTrabajo.PENDIENTE]


@en_la_base
def test_el_worker_continuo_trabaja_hasta_que_se_le_pide_detenerse(tmp_path):
    detener = threading.Event()
    config = Config(worker_poll_segundos=0.05)
    hilo, salida = _en_otro_hilo(lambda: ejecutar_worker(config, detener=detener))
    try:
        # Arranca con la cola vacia, y toma el flujo en cuanto aparece.
        _, flujo_id = _crear(tmp_path)
        assert _hasta(lambda: _flujo(flujo_id).estado == EstadoFlujo.COMPLETADO)
    finally:
        detener.set()
        hilo.join(timeout=30)

    assert not hilo.is_alive()
    assert salida["resultado"].tipo == TipoTrabajo.RUTEO


def test_un_error_inesperado_del_ciclo_no_detiene_el_worker(monkeypatch, caplog):
    detener = threading.Event()
    vueltas = []

    def falla_y_despues_se_detiene(worker_id, config):
        vueltas.append(worker_id)
        if len(vueltas) == 1:
            raise ConnectionError("la base no contesta")
        detener.set()

    monkeypatch.setattr(worker, "procesar_un_trabajo", falla_y_despues_se_detiene)

    assert ejecutar_worker(Config(worker_poll_segundos=0.01), detener=detener) is None
    assert len(vueltas) == 2 and len(set(vueltas)) == 1  # el mismo worker, que siguio
    assert any("error inesperado; espera y sigue" in r.getMessage() for r in caplog.records)


def test_una_vez_no_oculta_un_error_del_ciclo(monkeypatch):
    def revienta(worker_id, config):
        raise ConnectionError("la base no contesta")

    monkeypatch.setattr(worker, "procesar_un_trabajo", revienta)

    with pytest.raises(ConnectionError):
        ejecutar_worker(CONFIG, una_vez=True)


@en_la_base
@pytest.mark.parametrize("senal", [signal.SIGTERM, signal.SIGINT], ids=["SIGTERM", "SIGINT"])
def test_con_sigterm_o_sigint_termina_el_trabajo_en_curso_y_se_detiene(
    tmp_path, monkeypatch, senal
):
    # La senal llega a media ingesta. El worker la termina y la cierra, con su flujo, y no toma el
    # trabajo de la decision. Despues, las senales vuelven a sus manejadores de antes.
    _, flujo_id = _crear(tmp_path)
    ingerir = worker.MANEJADORES[TipoTrabajo.INGESTA]
    antes = signal.getsignal(senal)
    durante = []

    def ingesta_interrumpida(corrida_id: int) -> None:
        signal.raise_signal(senal)
        # Ya atendida: una segunda senal haria lo de siempre.
        durante.append(signal.getsignal(senal))
        ingerir(corrida_id)

    monkeypatch.setitem(worker.MANEJADORES, TipoTrabajo.INGESTA, ingesta_interrumpida)

    ultimo = ejecutar_worker(Config(worker_poll_segundos=0.01))

    assert (ultimo.tipo, ultimo.estado) == (TipoTrabajo.INGESTA, EstadoTrabajo.COMPLETADO)
    assert [t.estado for t in _trabajos()] == [EstadoTrabajo.COMPLETADO, EstadoTrabajo.PENDIENTE]
    assert _flujo(flujo_id).etapa == EtapaFlujo.DECISION
    assert durante == [antes]
    assert signal.getsignal(senal) == antes


def test_fuera_del_hilo_principal_el_worker_no_toca_las_senales():
    detener = threading.Event()
    hilo, salida = _en_otro_hilo(worker._escuchar_senales, detener)
    hilo.join(timeout=10)

    assert salida == {"resultado": {}}


# --- sin base de datos ---------------------------------------------------------------------------


def test_el_identificador_del_worker_es_su_host_su_pid_y_un_uuid_propio():
    uno, otro = identificador_worker(), identificador_worker()

    assert re.fullmatch(r".+:\d+:[0-9a-f]{32}", uno)
    assert uno != otro and uno.rsplit(":", 1)[0] == otro.rsplit(":", 1)[0]
    assert len(uno) <= 200


def test_los_parametros_del_worker_tienen_sus_valores_por_omision(monkeypatch):
    for variable in (
        "MC_WORKER_POLL_SEGUNDOS",
        "MC_WORKER_LEASE_SEGUNDOS",
        "MC_WORKER_HEARTBEAT_SEGUNDOS",
        "MC_WORKER_MAX_INTENTOS",
        "MC_WORKER_BACKOFF_SEGUNDOS",
    ):
        monkeypatch.delenv(variable, raising=False)

    config = Config()

    assert (
        config.worker_poll_segundos,
        config.worker_lease_segundos,
        config.worker_heartbeat_segundos,
        config.worker_max_intentos,
        config.worker_backoff_segundos,
    ) == (0.5, 60, 20, 5, 1)


def test_los_parametros_del_worker_se_leen_del_entorno(monkeypatch):
    monkeypatch.setenv("MC_WORKER_LEASE_SEGUNDOS", "90")
    monkeypatch.setenv("MC_WORKER_HEARTBEAT_SEGUNDOS", "15.5")
    monkeypatch.setenv("MC_WORKER_MAX_INTENTOS", "3")

    config = Config()

    assert (
        config.worker_lease_segundos,
        config.worker_heartbeat_segundos,
        config.worker_max_intentos,
    ) == (90, 15.5, 3)


@pytest.mark.parametrize(
    "parametros",
    [
        {"worker_poll_segundos": 0},
        {"worker_lease_segundos": 0},
        {"worker_heartbeat_segundos": -1},
        {"worker_max_intentos": 0},
        {"worker_backoff_segundos": 0},
    ],
    ids=lambda parametros: next(iter(parametros)),
)
def test_un_parametro_del_worker_fuera_de_rango_no_se_admite(parametros):
    with pytest.raises(ValidationError):
        Config(**parametros)


@pytest.mark.parametrize("latido", [60, 61])
def test_el_latido_tiene_que_caber_en_el_lease(latido):
    with pytest.raises(ValidationError, match="MC_WORKER_HEARTBEAT_SEGUNDOS debe ser menor"):
        Config(worker_lease_segundos=60, worker_heartbeat_segundos=latido)
