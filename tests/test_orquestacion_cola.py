"""La cola durable contra PostgreSQL: tomar, latir, perder el lease y cerrar un trabajo.

Los trabajos se crean sobre corridas EN_PROCESO y no se ejecuta ningun motor: aqui se prueba la
entrega, no lo que se entrega. Los tiempos se mueven en la base, con el reloj de PostgreSQL, en
lugar de esperarlos. Las pruebas de la ultima seccion no tocan la base.
"""

from __future__ import annotations

import threading
import time
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, func, update
from sqlalchemy.dialects import postgresql
from sqlmodel import select

from motor_cartera.db.modelos import EstadoTrabajo, TipoTrabajo, TrabajoOrquestacion
from motor_cartera.db.sesion import crear_motor, sesion
from motor_cartera.ingesta.corridas import abrir_corrida
from motor_cartera.orquestacion import cola

en_la_base = pytest.mark.usefixtures("bd")

LEASE = 60.0


def _corrida() -> int:
    """Una corrida EN_PROCESO de un archivo propio: el objetivo de un trabajo de ingesta."""
    with sesion() as s:
        # Texto unico: un archivo propio para cada corrida, y que el almacen reconoce como csv.
        contenido = f"cartera {uuid4()}\n".encode()
        return abrir_corrida(s, origen="cartera.csv", contenido=contenido).id


def _encolar(max_intentos: int = 5, **campos) -> int:
    """Un trabajo de ingesta PENDIENTE, confirmado; `campos` lo crea ya tomado."""
    corrida_id = _corrida()
    with sesion() as s:
        trabajo = cola.crear(
            s, TipoTrabajo.INGESTA, corrida_id, max_intentos=max_intentos, **campos
        )
        s.commit()
        return trabajo.id


def _fila(trabajo_id: int) -> TrabajoOrquestacion:
    with sesion() as s:
        return s.get_one(TrabajoOrquestacion, trabajo_id)


def _ahora():
    """El instante de PostgreSQL: el reloj de la cola."""
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
    """El lease del trabajo vence ya, como si su worker hubiera dejado de latir."""
    _cambiar(trabajo_id, lease_hasta=func.now() - timedelta(seconds=1))


def _disponible_ya(trabajo_id: int) -> None:
    """La espera de un trabajo devuelto termina ya, sin esperarla."""
    _cambiar(trabajo_id, disponible_desde=func.now())


# --- tomar un trabajo -----------------------------------------------------------------------------


@en_la_base
def test_un_trabajo_pendiente_se_toma_y_queda_ejecutando_con_su_lease():
    trabajo_id = _encolar()
    antes = _ahora()

    reclamo = cola.reclamar("worker-a", LEASE)

    despues = _ahora()
    assert (reclamo.id, reclamo.tipo, reclamo.intentos, reclamo.ejecutar) == (
        trabajo_id,
        TipoTrabajo.INGESTA,
        1,
        True,
    )
    assert reclamo.objetivo_id == _fila(trabajo_id).corrida_id
    fila = _fila(trabajo_id)
    assert (fila.estado, fila.worker_id, fila.intentos) == (
        EstadoTrabajo.EJECUTANDO,
        "worker-a",
        1,
    )
    # Tomado, con su primer latido y el lease contado desde ahi, con el reloj de PostgreSQL.
    assert antes <= fila.tomado_en == fila.latido_en <= despues
    assert fila.lease_hasta == fila.tomado_en + timedelta(seconds=LEASE)
    assert fila.terminado_en is None and fila.ultimo_error is None


@en_la_base
def test_con_la_cola_vacia_no_se_toma_nada():
    assert cola.reclamar("worker-a", LEASE) is None


@en_la_base
def test_un_trabajo_que_todavia_no_esta_disponible_no_se_toma():
    trabajo_id = _encolar()
    _cambiar(trabajo_id, disponible_desde=func.now() + timedelta(minutes=5))

    assert cola.reclamar("worker-a", LEASE) is None
    assert _fila(trabajo_id).estado == EstadoTrabajo.PENDIENTE

    _disponible_ya(trabajo_id)
    assert cola.reclamar("worker-a", LEASE).id == trabajo_id


@en_la_base
def test_se_toma_el_de_menor_id_y_uno_a_la_vez():
    trabajos = [_encolar() for _ in range(3)]

    tomados = [cola.reclamar("worker-a", LEASE).id for _ in trabajos]

    assert tomados == trabajos
    assert cola.reclamar("worker-a", LEASE) is None


@en_la_base
def test_skip_locked_salta_el_trabajo_que_otra_transaccion_tiene_bloqueado():
    # Otra sesion tiene bloqueada la fila del primero, como un worker que lo esta tomando o
    # cerrando. El reclamo no la espera: toma el siguiente, en seguida.
    primero, segundo = _encolar(), _encolar()
    with sesion() as otra:
        otra.exec(
            select(TrabajoOrquestacion).where(TrabajoOrquestacion.id == primero).with_for_update()
        ).one()
        inicio = time.monotonic()

        reclamo = cola.reclamar("worker-b", LEASE)

        assert time.monotonic() - inicio < 5
        assert reclamo.id == segundo
    # Al soltarse, el primero sigue PENDIENTE: nadie lo tomo por error.
    assert _fila(primero).estado == EstadoTrabajo.PENDIENTE
    assert cola.reclamar("worker-c", LEASE).id == primero


def _a_la_vez(*workers: str) -> dict[str, cola.Reclamo | None]:
    """Cada worker reclama desde su hilo, con su conexion, y todos en el mismo instante: una
    barrera los suelta juntos."""
    barrera = threading.Barrier(len(workers), timeout=10)
    tomados: dict[str, cola.Reclamo | None] = {}

    def reclamar(worker_id: str) -> None:
        barrera.wait()
        tomados[worker_id] = cola.reclamar(worker_id, LEASE)

    hilos = [threading.Thread(target=reclamar, args=(worker,)) for worker in workers]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=30)
    assert len(tomados) == len(workers)
    return tomados


@en_la_base
@pytest.mark.parametrize("cuantos", [2, 1])
def test_dos_workers_a_la_vez_nunca_toman_el_mismo_trabajo(cuantos):
    # Con dos trabajos, cada worker toma uno distinto; con uno solo, uno lo toma y el otro no toma
    # nada.
    trabajos = [_encolar() for _ in range(cuantos)]

    tomados = _a_la_vez("worker-a", "worker-b")

    assert sorted(r.id for r in tomados.values() if r is not None) == trabajos
    for worker_id, reclamo in tomados.items():
        if reclamo is not None:
            assert (reclamo.intentos, _fila(reclamo.id).worker_id) == (1, worker_id)


@en_la_base
def test_un_trabajo_con_su_lease_vigente_no_lo_toma_otro_worker():
    trabajo_id = _encolar()
    cola.reclamar("worker-a", LEASE)

    assert cola.reclamar("worker-b", LEASE) is None
    assert _fila(trabajo_id).worker_id == "worker-a"


# --- el lease, el latido y quien es el dueno ------------------------------------------------------


@en_la_base
def test_con_el_lease_vencido_otro_worker_lo_toma_y_cuenta_otro_intento():
    trabajo_id = _encolar()
    cola.reclamar("worker-a", LEASE)
    _vencer(trabajo_id)

    reclamo = cola.reclamar("worker-b", LEASE)

    # La misma fila, no otra: un recurso tiene un solo trabajo.
    assert (reclamo.id, reclamo.intentos, reclamo.ejecutar) == (trabajo_id, 2, True)
    fila = _fila(trabajo_id)
    assert (fila.estado, fila.worker_id, fila.intentos) == (
        EstadoTrabajo.EJECUTANDO,
        "worker-b",
        2,
    )
    assert fila.lease_hasta > _ahora()
    assert fila.ultimo_error == cola.LEASE_VENCIDO
    with sesion() as s:
        assert s.exec(select(func.count()).select_from(TrabajoOrquestacion)).one() == 1


@en_la_base
def test_el_latido_extiende_el_lease_solo_mientras_el_trabajo_es_suyo():
    trabajo_id = _encolar()
    cola.reclamar("worker-a", 30)
    tomado = _fila(trabajo_id)

    assert cola.renovar(trabajo_id, "worker-a", LEASE) is True

    latido = _fila(trabajo_id)
    assert latido.latido_en >= tomado.latido_en
    assert latido.lease_hasta == latido.latido_en + timedelta(seconds=LEASE) > tomado.lease_hasta
    # Otro worker no le extiende el lease a un trabajo que no es suyo.
    assert cola.renovar(trabajo_id, "worker-b", LEASE) is False
    # Y cuando el lease vence y otro lo toma, el latido del anterior ya no lo renueva.
    _vencer(trabajo_id)
    cola.reclamar("worker-b", LEASE)
    del_nuevo = _fila(trabajo_id)

    assert cola.renovar(trabajo_id, "worker-a", LEASE) is False
    assert _fila(trabajo_id).model_dump() == del_nuevo.model_dump()


@en_la_base
def test_el_dueno_anterior_no_puede_cerrar_el_trabajo_que_otro_tomo():
    trabajo_id = _encolar()
    cola.reclamar("worker-a", LEASE)
    _vencer(trabajo_id)
    cola.reclamar("worker-b", LEASE)
    del_nuevo = _fila(trabajo_id).model_dump()

    with sesion() as s:
        assert cola.tomar_para_cerrar(s, trabajo_id, "worker-a") is None
        for cerrar in (
            lambda: cola.completar(s, trabajo_id, "worker-a"),
            lambda: cola.devolver(s, trabajo_id, "worker-a", espera_segundos=1, error="x"),
            lambda: cola.agotar(s, trabajo_id, "worker-a", error="x"),
        ):
            with pytest.raises(cola.TrabajoAjeno, match="ya no es de worker-a"):
                cerrar()
        s.commit()

    assert _fila(trabajo_id).model_dump() == del_nuevo
    # El nuevo dueno si lo cierra.
    with sesion() as s:
        assert cola.tomar_para_cerrar(s, trabajo_id, "worker-b") is not None
        cola.completar(s, trabajo_id, "worker-b")
        s.commit()
    fila = _fila(trabajo_id)
    assert (fila.estado, fila.worker_id, fila.lease_hasta) == (EstadoTrabajo.COMPLETADO, None, None)
    assert fila.terminado_en is not None
    # Tomado y latido se conservan, para saber cuando lo tuvo su ultimo worker.
    assert fila.tomado_en is not None and fila.latido_en is not None


@en_la_base
def test_cuando_vence_el_lease_del_ultimo_intento_ya_no_se_ejecuta_solo_se_cierra():
    trabajo_id = _encolar(max_intentos=1)
    cola.reclamar("worker-a", LEASE)
    _vencer(trabajo_id)

    reclamo = cola.reclamar("worker-b", LEASE)

    # Lo toma para cerrarlo, sin contar un intento que no va a hacer.
    assert (reclamo.id, reclamo.intentos, reclamo.max_intentos, reclamo.ejecutar) == (
        trabajo_id,
        1,
        1,
        False,
    )
    fila = _fila(trabajo_id)
    assert (fila.worker_id, fila.intentos) == ("worker-b", 1)


# --- devolver y agotar ---------------------------------------------------------------------------


@en_la_base
def test_un_trabajo_devuelto_espera_sin_dueno_ni_lease():
    trabajo_id = _encolar()
    cola.reclamar("worker-a", LEASE)
    antes = _ahora()

    with sesion() as s:
        cola.devolver(s, trabajo_id, "worker-a", espera_segundos=4, error="Error de worker (X).")
        s.commit()

    despues = _ahora()
    fila = _fila(trabajo_id)
    assert (fila.estado, fila.worker_id, fila.lease_hasta, fila.terminado_en) == (
        EstadoTrabajo.PENDIENTE,
        None,
        None,
        None,
    )
    assert (fila.intentos, fila.ultimo_error) == (1, "Error de worker (X).")
    espera = timedelta(seconds=4)
    assert antes + espera <= fila.disponible_desde <= despues + espera
    # Mientras espera, nadie lo toma; despues de la espera, si.
    assert cola.reclamar("worker-b", LEASE) is None
    _disponible_ya(trabajo_id)
    assert cola.reclamar("worker-b", LEASE).intentos == 2


@en_la_base
def test_un_trabajo_agotado_queda_fallido_con_su_fin_y_su_ultimo_error():
    trabajo_id = _encolar(max_intentos=1)
    cola.reclamar("worker-a", LEASE)
    error = "Error de worker (OperationalError); ver la bitacora." + " " * 600

    with sesion() as s:
        cola.agotar(s, trabajo_id, "worker-a", error=error)
        s.commit()

    fila = _fila(trabajo_id)
    assert (fila.estado, fila.worker_id, fila.lease_hasta) == (EstadoTrabajo.FALLIDO, None, None)
    assert fila.terminado_en is not None
    # Acotado a lo que cabe en la columna.
    assert fila.ultimo_error == error[:500]
    # Un trabajo terminado no se vuelve a tomar.
    assert cola.reclamar("worker-b", LEASE) is None


# --- crear ---------------------------------------------------------------------------------------


@en_la_base
def test_crear_no_confirma_la_transaccion_de_quien_llama():
    corrida_id = _corrida()
    with sesion() as s:
        trabajo = cola.crear(s, TipoTrabajo.INGESTA, corrida_id, max_intentos=3)
        assert trabajo.id is not None  # ya se envio el INSERT
        s.rollback()

    with sesion() as s:
        assert s.exec(select(func.count()).select_from(TrabajoOrquestacion)).one() == 0


@en_la_base
def test_un_trabajo_nace_pendiente_disponible_ya_y_con_la_politica_de_su_configuracion():
    corrida_id = _corrida()
    antes = _ahora()
    with sesion() as s:
        trabajo = cola.crear(s, TipoTrabajo.INGESTA, corrida_id, max_intentos=7)
        s.commit()
        trabajo_id = trabajo.id

    fila = _fila(trabajo_id)
    assert (fila.estado, fila.intentos, fila.max_intentos, fila.flujo_id) == (
        EstadoTrabajo.PENDIENTE,
        0,
        7,
        None,
    )
    assert fila.corrida_id == corrida_id
    assert antes <= fila.creado_en == fila.disponible_desde
    assert (fila.tomado_en, fila.latido_en, fila.lease_hasta, fila.worker_id) == (
        None,
        None,
        None,
        None,
    )


@en_la_base
def test_un_trabajo_puede_nacer_ya_tomado_por_quien_lo_va_a_ejecutar():
    trabajo_id = _encolar(tomado_por="cli:1:abc", lease_segundos=LEASE)

    fila = _fila(trabajo_id)
    assert (fila.estado, fila.worker_id, fila.intentos) == (
        EstadoTrabajo.EJECUTANDO,
        "cli:1:abc",
        1,
    )
    assert fila.lease_hasta == fila.tomado_en + timedelta(seconds=LEASE)
    # Ningun otro worker se le adelanta.
    assert cola.reclamar("worker-a", LEASE) is None
    with sesion() as s, pytest.raises(ValueError, match="necesita su lease"):
        cola.crear(s, TipoTrabajo.INGESTA, _corrida(), max_intentos=1, tomado_por="x")


# --- sin base de datos ---------------------------------------------------------------------------


def test_la_espera_se_duplica_en_cada_intento_sin_azar():
    assert [cola.espera_de_reintento(n, 1) for n in range(1, 6)] == [1, 2, 4, 8, 16]
    assert [cola.espera_de_reintento(n, 0.5) for n in range(1, 4)] == [0.5, 1, 2]
    # La misma entrada, la misma espera: se puede fijar en una prueba.
    assert cola.espera_de_reintento(3, 1.5) == cola.espera_de_reintento(3, 1.5) == 6
    with pytest.raises(ValueError, match="al menos una vez"):
        cola.espera_de_reintento(0, 1)


def test_lo_que_se_puede_tomar_se_compara_con_el_reloj_de_postgresql():
    sql = " ".join(str(cola._reclamable().compile(dialect=postgresql.psycopg.dialect())).split())

    # Un PENDIENTE ya disponible, o un EJECUTANDO con el lease vencido; ningun instante de Python.
    assert sql == (
        "trabajo_orquestacion.estado = %(estado_1)s AND trabajo_orquestacion.disponible_desde <= "
        "now() OR trabajo_orquestacion.estado = %(estado_2)s AND trabajo_orquestacion.lease_hasta "
        "< now()"
    )


@en_la_base
def test_el_reclamo_salta_las_filas_bloqueadas_y_toma_una_sola_en_orden():
    _encolar()
    motor = crear_motor()
    vistas: list[str] = []

    def anotar(conexion, cursor, sentencia, parametros, contexto, varias):
        vistas.append(" ".join(sentencia.split()))

    event.listen(motor, "before_cursor_execute", anotar)
    try:
        cola.reclamar("worker-a", LEASE)
    finally:
        event.remove(motor, "before_cursor_execute", anotar)

    # Una consulta que toma una fila, lo operacional antes que la historia, la historia antes que
    # el motor de pagos y este antes que la atribucion y la evaluacion de promesas, y despues en
    # orden de id, saltando las bloqueadas; y un UPDATE de esa.
    tomar, marcar = vistas
    assert tomar.startswith("SELECT ") and "FROM trabajo_orquestacion WHERE" in tomar
    assert tomar.endswith(
        "ORDER BY CASE WHEN (trabajo_orquestacion.tipo = %(tipo_1)s) THEN %(param_1)s::INTEGER "
        "WHEN (trabajo_orquestacion.tipo = %(tipo_2)s) THEN %(param_2)s::INTEGER "
        "WHEN (trabajo_orquestacion.tipo = %(tipo_3)s) THEN %(param_3)s::INTEGER "
        "WHEN (trabajo_orquestacion.tipo = %(tipo_4)s) THEN %(param_4)s::INTEGER "
        "ELSE %(param_5)s::INTEGER END, trabajo_orquestacion.id "
        "LIMIT %(param_6)s::INTEGER FOR UPDATE SKIP LOCKED"
    )
    assert marcar.startswith("UPDATE trabajo_orquestacion SET ")
