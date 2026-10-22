"""La materializacion historica con varios workers a la vez, contra PostgreSQL real.

Cada caso fuerza la concurrencia en lugar de esperarla: los hilos se detienen en el punto exacto
(despues de copiar el corte, antes de crear sus cuentas o de publicar su corte) hasta que el otro
llega, y solo entonces siguen. Cada hilo usa su propia conexion, como un worker en otro proceso.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta

import pytest
from historia_escenarios import Cuenta, cliente, historia_de, ingerir_corte
from sqlalchemy import func, text, update
from sqlmodel import select

from motor_cartera.config import Config
from motor_cartera.db.modelos import (
    Corrida,
    CorteCanonico,
    CuentaCanonica,
    EjecucionHistoria,
    EstadoTrabajo,
    SnapshotCuenta,
    TipoTrabajo,
    TrabajoOrquestacion,
)
from motor_cartera.db.sesion import crear_motor, sesion
from motor_cartera.historia import ejecuciones, identidad
from motor_cartera.historia.ejecuciones import materializar
from motor_cartera.orquestacion import cola, worker

pytestmark = pytest.mark.usefixtures("bd")

CORTE = date(2026, 9, 2)
ESPERA = 60


def _cuantas(modelo, *condiciones) -> int:
    with sesion() as s:
        return s.exec(select(func.count()).select_from(modelo).where(*condiciones)).one()


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


def _juntos(monkeypatch, funcion: str, hilos: int = 2) -> threading.Barrier:
    """Los `hilos` que lleguen a `funcion` de la materializacion la ejecutan a la vez: cada uno
    espera ahi a los demas."""
    juntos = threading.Barrier(hilos, timeout=ESPERA)
    original = getattr(ejecuciones, funcion)

    def envoltura(*args, **kwargs):
        juntos.wait()
        return original(*args, **kwargs)

    monkeypatch.setattr(ejecuciones, funcion, envoltura)
    return juntos


def _sin_errores(*salidas: dict) -> None:
    for salida in salidas:
        assert "error" not in salida, salida.get("error")


@contextmanager
def _reten(sentencia: str, **parametros) -> Iterator[int]:
    """Una fila que choca con lo que van a publicar los hilos solo en su identificador determinista
    (el corte_id o el cuenta_id, que salen de la misma clave que el otro indice unico), insertada
    en una transaccion abierta en otra conexion. Los hilos llegan a su INSERT y la esperan ahi; al
    salir se revierte y siguen a la vez desde ese punto: es la intercalacion de dos workers que
    llegan al mismo INSERT en el mismo instante, que sin el reten solo se da a veces. Da el pid de
    la conexion."""
    with crear_motor().connect() as conexion:
        transaccion = conexion.begin()
        conexion.execute(text(sentencia), parametros)
        pid = conexion.execute(text("SELECT pg_backend_pid()")).scalar_one()
        try:
            yield pid
        finally:
            transaccion.rollback()


def _esperan_al_reten(pid: int, hilos: int) -> None:
    """Hasta que `hilos` conexiones esten bloqueadas por la transaccion del reten."""
    limite = time.monotonic() + ESPERA
    while True:
        with sesion() as s:
            bloqueadas = s.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity WHERE :pid = ANY(pg_blocking_pids(pid))"
                ),
                {"pid": pid},
            ).scalar_one()
        if bloqueadas >= hilos:
            return
        assert time.monotonic() < limite, f"{bloqueadas} de {hilos} hilos llegaron al reten"
        time.sleep(0.05)


def _materializar_tras_el_reten(sentencia: str, corridas: tuple[Corrida, ...], **parametros):
    """La historia de cada una de `corridas`, cada una en su hilo, detenidas juntas en el reten."""
    with _reten(sentencia, **parametros) as pid:
        hilos = [_en_otro_hilo(materializar, historia_de(corrida=c).id) for c in corridas]
        _esperan_al_reten(pid, len(hilos))
    for hilo, _ in hilos:
        hilo.join(ESPERA)
    _sin_errores(*(salida for _, salida in hilos))


CORTE_RETENIDO = (
    "INSERT INTO corte_canonico (corte_id, despacho_id, cartera_id, fecha_corte, firma_contenido, "
    "version_modelo, cuentas, dataset_conformado_id, creado_en) VALUES (:corte_id, :despacho, "
    ":cartera, :fecha, :firma, 'historia/v1', 1, :dataset, now())"
)
CUENTA_RETENIDA = (
    "INSERT INTO cuenta_canonica (cuenta_id, despacho_id, cartera_id, cliente_unico, creada_en) "
    "VALUES (:cuenta_id, :despacho, :cartera, :cliente, now())"
)


# --- el mismo dataset -----------------------------------------------------------------------------


def test_dos_workers_con_el_mismo_dataset_publican_una_sola_vez(tmp_path, monkeypatch):
    corrida = ingerir_corte(tmp_path, CORTE, [Cuenta(n) for n in range(1, 201)])
    ejecucion = historia_de(corrida=corrida)
    empezo, soltar = threading.Event(), threading.Event()
    crear = ejecuciones._crear_cuentas

    def espera(*args, **kwargs):
        empezo.set()
        assert soltar.wait(ESPERA)
        return crear(*args, **kwargs)

    monkeypatch.setattr(ejecuciones, "_crear_cuentas", espera)
    primero, salida_1 = _en_otro_hilo(materializar, ejecucion.id)
    assert empezo.wait(ESPERA)
    # El segundo llega mientras el primero tiene la ejecucion bloqueada: espera.
    segundo, salida_2 = _en_otro_hilo(materializar, ejecucion.id)
    time.sleep(0.5)
    assert segundo.is_alive()

    soltar.set()
    for hilo in (primero, segundo):
        hilo.join(ESPERA)

    _sin_errores(salida_1, salida_2)
    terminada = historia_de(corrida=corrida)
    assert (terminada.estado, terminada.resultado) == ("EXITOSA", "CORTE_PUBLICADO")
    assert _cuantas(EjecucionHistoria) == 1
    assert _cuantas(SnapshotCuenta) == _cuantas(CuentaCanonica) == 200
    assert _cuantas(CorteCanonico) == 1


# --- cortes distintos con cuentas compartidas ----------------------------------------------------


def test_dos_cortes_a_la_vez_con_cuentas_nuevas_compartidas_no_duplican_ninguna(
    tmp_path, monkeypatch
):
    # 300 cuentas en cada corte, 200 compartidas; ninguna existia antes. Los dos hilos llegan
    # juntos a crear sus cuentas nuevas: los dos quieren crear las 200 compartidas.
    uno = ingerir_corte(tmp_path, CORTE, [Cuenta(n) for n in range(1, 301)])
    dos = ingerir_corte(
        tmp_path, CORTE + timedelta(days=7), [Cuenta(n, saldo=9_000) for n in range(101, 401)]
    )
    _juntos(monkeypatch, "_crear_cuentas")

    hilos = [_en_otro_hilo(materializar, historia_de(corrida=c).id) for c in (uno, dos)]
    for hilo, _ in hilos:
        hilo.join(ESPERA)

    _sin_errores(*(salida for _, salida in hilos))
    for corrida in (uno, dos):
        assert historia_de(corrida=corrida).resultado == "CORTE_PUBLICADO"
    assert _cuantas(CuentaCanonica) == 400
    assert _cuantas(SnapshotCuenta) == 600
    with sesion() as s:
        por_cuenta = s.exec(
            select(func.count(), func.count(func.distinct(SnapshotCuenta.cuenta_canonica_id)))
        ).one()
        cortes = {c.fecha_corte: c.cuentas for c in s.exec(select(CorteCanonico)).all()}
    assert tuple(por_cuenta) == (600, 400)
    assert cortes == {CORTE: 300, CORTE + timedelta(days=7): 300}


# --- dos fuentes del mismo corte ------------------------------------------------------------------


def test_dos_fuentes_equivalentes_a_la_vez_son_un_solo_corte(tmp_path, monkeypatch):
    cuentas = [Cuenta(n) for n in range(1, 151)]
    xlsx = ingerir_corte(tmp_path, CORTE, cuentas, formato="xlsx")
    zip_ = ingerir_corte(tmp_path, CORTE, cuentas, formato="zip")
    _juntos(monkeypatch, "_insertar_corte")

    hilos = [_en_otro_hilo(materializar, historia_de(corrida=c).id) for c in (xlsx, zip_)]
    for hilo, _ in hilos:
        hilo.join(ESPERA)

    _sin_errores(*(salida for _, salida in hilos))
    resultados = sorted(historia_de(corrida=c).resultado for c in (xlsx, zip_))
    assert resultados == ["CORTE_PUBLICADO", "FUENTE_EQUIVALENTE"]
    assert _cuantas(CorteCanonico) == 1
    assert _cuantas(SnapshotCuenta) == _cuantas(CuentaCanonica) == 150


def test_dos_fuentes_conflictivas_a_la_vez_publican_una_y_la_otra_falla(tmp_path, monkeypatch):
    una = ingerir_corte(tmp_path, CORTE, [Cuenta(n) for n in range(1, 151)])
    otra = ingerir_corte(
        tmp_path, CORTE, [Cuenta(n, saldo=77_000) for n in range(1, 151)], nombre="otra"
    )
    _juntos(monkeypatch, "_insertar_corte")

    hilos = [_en_otro_hilo(materializar, historia_de(corrida=c).id) for c in (una, otra)]
    for hilo, _ in hilos:
        hilo.join(ESPERA)

    _sin_errores(*(salida for _, salida in hilos))
    terminadas = sorted(
        ((e.estado, e.resultado) for e in (historia_de(corrida=c) for c in (una, otra))),
    )
    assert terminadas == [
        ("EXITOSA", "CORTE_PUBLICADO"),
        ("FALLIDA", "CORTE_CANONICO_CONFLICTIVO"),
    ]
    assert _cuantas(CorteCanonico) == 1
    assert _cuantas(SnapshotCuenta) == 150


# --- dos publicaciones que llegan juntas al mismo INSERT -----------------------------------------
#
# El corte_id sale de la misma clave que uq_corte_canonico_fecha, y el cuenta_id de la misma que
# uq_cuenta_canonica_clave: dos publicaciones de la misma identidad chocan en los dos indices
# unicos. Si el ON CONFLICT arbitrara solo la clave, la que llega a la vez que la otra pasaria su
# revision previa, esperaria a la otra en el indice del identificador y recibiria un
# unique_violation de ese indice: ERROR_INTERNO en lugar de su resultado. El reten las detiene
# juntas en ese INSERT.


@pytest.mark.parametrize(
    ("formatos", "saldo_de_la_otra", "resultados"),
    [
        (
            ("csv", "csv"),
            77_000,
            [("EXITOSA", "CORTE_PUBLICADO"), ("FALLIDA", "CORTE_CANONICO_CONFLICTIVO")],
        ),
        (
            ("xlsx", "zip"),
            10_000,
            [("EXITOSA", "CORTE_PUBLICADO"), ("EXITOSA", "FUENTE_EQUIVALENTE")],
        ),
    ],
    ids=["conflictiva", "equivalente"],
)
def test_dos_fuentes_que_llegan_juntas_al_insert_del_corte_publican_una_y_juzgan_la_otra(
    tmp_path, formatos, saldo_de_la_otra, resultados
):
    una = ingerir_corte(tmp_path, CORTE, [Cuenta(n) for n in range(1, 151)], formato=formatos[0])
    otra = ingerir_corte(
        tmp_path,
        CORTE,
        [Cuenta(n, saldo=saldo_de_la_otra) for n in range(1, 151)],
        formato=formatos[1],
        nombre="otra",
    )
    # El reten necesita un dataset propio: el de un corte de otra fecha, que no se materializa.
    ajeno = ingerir_corte(tmp_path, CORTE + timedelta(days=1), [Cuenta(1)], nombre="ajeno")

    _materializar_tras_el_reten(
        CORTE_RETENIDO,
        (una, otra),
        corte_id=identidad.corte_id(una.despacho_id, una.cartera_id, CORTE),
        despacho=una.despacho_id,
        cartera=una.cartera_id,
        fecha=CORTE + timedelta(days=1),
        firma="0" * 64,
        dataset=historia_de(corrida=ajeno).dataset_conformado_id,
    )

    terminadas = sorted(
        (e.estado, e.resultado) for e in (historia_de(corrida=c) for c in (una, otra))
    )
    assert terminadas == resultados
    assert _cuantas(CorteCanonico) == 1
    assert _cuantas(SnapshotCuenta) == _cuantas(CuentaCanonica) == 150


def test_dos_cortes_que_crean_juntos_la_misma_cuenta_nueva_la_crean_una_vez(tmp_path):
    uno = ingerir_corte(tmp_path, CORTE, [Cuenta(n) for n in range(1, 301)])
    dos = ingerir_corte(
        tmp_path, CORTE + timedelta(days=7), [Cuenta(n, saldo=9_000) for n in range(101, 401)]
    )

    # La 101 es la primera cuenta nueva que comparten, en el orden en que se crean: el reten tiene
    # su cuenta_id con un CLIENTE_UNICO que no esta en ningun corte.
    _materializar_tras_el_reten(
        CUENTA_RETENIDA,
        (uno, dos),
        cuenta_id=identidad.cuenta_id(uno.despacho_id, uno.cartera_id, cliente(101)),
        despacho=uno.despacho_id,
        cartera=uno.cartera_id,
        cliente=cliente(999_999),
    )

    for corrida in (uno, dos):
        assert historia_de(corrida=corrida).resultado == "CORTE_PUBLICADO"
    assert _cuantas(CuentaCanonica) == 400
    assert _cuantas(SnapshotCuenta) == 600


# --- un worker que pierde el lease despues de calcular -------------------------------------------


def test_un_worker_que_pierde_el_lease_a_media_historia_no_publica_dos_veces(tmp_path, monkeypatch):
    corrida = ingerir_corte(tmp_path, CORTE, [Cuenta(n) for n in range(1, 121)])
    # Sin latidos durante la prueba: el lease vence porque la prueba lo vence.
    config = Config(worker_lease_segundos=600, worker_heartbeat_segundos=300)
    calculo, soltar = threading.Event(), threading.Event()
    insertar = ejecuciones._insertar_snapshots

    def calcula_y_espera(*args, **kwargs):
        publicados = insertar(*args, **kwargs)
        if not soltar.is_set():
            calculo.set()
            assert soltar.wait(ESPERA)
        return publicados

    monkeypatch.setattr(ejecuciones, "_insertar_snapshots", calcula_y_espera)
    reclamo_a = cola.reclamar("worker-a", config.worker_lease_segundos)
    assert reclamo_a.tipo == TipoTrabajo.HISTORIA
    hilo_a, salida_a = _en_otro_hilo(worker.procesar_reclamo, reclamo_a, "worker-a", config)
    assert calculo.wait(ESPERA)

    # El worker A ya calculo todo, pero su lease vence antes de que confirme: B toma el trabajo.
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
    # A publico una vez, con la ejecucion bloqueada; B la encontro terminada y solo cerro el
    # trabajo, que ya era suyo. A no lo cierra: ya no le tocaba.
    assert salida_a["resultado"].estado is None
    assert salida_b["resultado"].estado == EstadoTrabajo.COMPLETADO
    with sesion() as s:
        trabajo = s.get_one(TrabajoOrquestacion, reclamo_a.id)
    assert (trabajo.estado, trabajo.intentos, trabajo.worker_id) == ("COMPLETADO", 2, None)
    assert historia_de(corrida=corrida).resultado == "CORTE_PUBLICADO"
    assert _cuantas(SnapshotCuenta) == 120
    assert _cuantas(EjecucionHistoria) == 1
