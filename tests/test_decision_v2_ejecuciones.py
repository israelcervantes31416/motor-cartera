"""decision/v2 en PostgreSQL: abrir con sus entradas fijas, la idempotencia, lo que no se puede
abrir, la publicacion todo o nada con fallas inyectadas, varios workers, el lease perdido, dos
cortes a la vez, la frontera de conocimiento y su trabajo en la cola durable."""

from __future__ import annotations

import threading
import time
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from decision_v2_escenarios import (
    C3,
    C4,
    ZONA,
    abrir_v2,
    corte_id,
    decidir_v2,
    ejecucion,
    pequeno,
    reloj,
)
from historia_escenarios import cuenta
from lifecycle_escenarios import gestion_de
from sqlalchemy import func, text, update
from sqlmodel import select

from motor_cartera.config import Config
from motor_cartera.db.modelos import (
    CorteCanonico,
    EjecucionDecisionV2,
    EstadoTrabajo,
    FeatureDecisionCuenta,
    ModoDecisionV2,
    ResultadoDecisionV2,
    SnapshotCuenta,
    TipoTrabajo,
    TrabajoOrquestacion,
)
from motor_cartera.db.sesion import sesion
from motor_cartera.decision_v2 import ejecuciones as motor
from motor_cartera.decision_v2.ejecuciones import (
    AsOfDistintoDelCorte,
    CorteNoConocido,
    CorteNoEncontrado,
    CorteNoVigente,
    DecisionV2EnProceso,
    DecisionV2YaGenerada,
    FronteraSinAsentar,
    KnownAtFuturo,
    KnownAtRequerido,
    KnownAtSinZona,
    PoliticaNoSoportada,
    firma_inputs,
)
from motor_cartera.decision_v2.politica import PUBLIC_V1, hash_politica
from motor_cartera.lifecycle import registro
from motor_cartera.orquestacion import cola, objetivos, worker
from motor_cartera.orquestacion.worker import decidir_v2_en_primer_plano

pytestmark = pytest.mark.usefixtures("bd")

ESPERA = 60


def _cuantos(modelo, *condiciones) -> int:
    with sesion() as s:
        return s.exec(select(func.count()).select_from(modelo).where(*condiciones)).one()


def _trabajo_de(ejecucion_id: int) -> TrabajoOrquestacion:
    with sesion() as s:
        return s.exec(
            select(TrabajoOrquestacion).where(
                TrabajoOrquestacion.ejecucion_decision_v2_id == ejecucion_id
            )
        ).one()


def _sin_temporales() -> bool:
    with sesion() as s:
        return (
            s.execute(
                text(
                    "SELECT count(*) FROM pg_class "
                    "WHERE relname LIKE 'dv2_%' AND relpersistence = 't'"
                )
            ).scalar_one()
            == 0
        )


# --- abrir ----------------------------------------------------------------------------------------


def test_abrir_fija_sus_entradas_una_vez_y_encola_su_trabajo(tmp_path):
    pequeno(tmp_path)
    antes = reloj()

    ejecucion_id = abrir_v2()

    despues = reloj()
    e = ejecucion(ejecucion_id)
    assert e.estado == "EN_PROCESO" and e.resultado is None
    assert (e.as_of, e.modo, e.zona_horaria, e.cuentas_objetivo) == (C4, "OPERATIVO", ZONA, 3)
    # Sin known_at, el reloj de la base al abrir: una vez, y queda guardado.
    assert antes < e.known_at < despues
    assert (e.version_motor, e.version_features, e.version_politica) == (
        "decision/v2",
        "features-decision/v1",
        "decision-policy/public-v1",
    )
    assert e.hash_politica == hash_politica(PUBLIC_V1)
    assert e.firma_inputs == firma_inputs(
        despacho_id="DSP_001",
        cartera_id="CARTERA_PRINCIPAL",
        corte_id=corte_id(C4),
        as_of=C4,
        known_at=e.known_at,
        zona=ZONA,
    )
    trabajo = _trabajo_de(ejecucion_id)
    assert (trabajo.tipo, trabajo.estado, trabajo.intentos) == ("DECISION_V2", "PENDIENTE", 0)


def test_la_firma_cambia_con_cada_entrada_y_no_con_el_modo():
    entradas = {
        "despacho_id": "DSP_001",
        "cartera_id": "CARTERA_PRINCIPAL",
        "corte_id": uuid4(),
        "as_of": date(2026, 9, 16),
        "known_at": datetime(2026, 9, 17, 8, tzinfo=UTC),
        "zona": ZONA,
    }
    base = firma_inputs(**entradas)
    for campo, otro in (
        ("cartera_id", "OTRA"),
        ("corte_id", uuid4()),
        ("as_of", date(2026, 9, 15)),
        ("known_at", datetime(2026, 9, 17, 8, 0, 0, 1, tzinfo=UTC)),
        ("zona", "UTC"),
    ):
        assert firma_inputs(**{**entradas, campo: otro}) != base, campo
    # El mismo instante en otra zona es el mismo known_at.
    otra_zona = entradas["known_at"].astimezone(datetime.now().astimezone().tzinfo)
    assert firma_inputs(**{**entradas, "known_at": otra_zona}) == base


@pytest.mark.parametrize(
    ("error", "opciones"),
    [
        (CorteNoEncontrado, {"corte": "otro"}),
        (AsOfDistintoDelCorte, {"as_of": date(2026, 9, 15)}),
        (KnownAtRequerido, {"modo": ModoDecisionV2.BACKTEST}),
        (KnownAtSinZona, {"known_at": datetime(2026, 9, 17, 8)}),
        (KnownAtFuturo, {"known_at": "manana"}),
        (CorteNoConocido, {"known_at": "antes_del_corte"}),
        (PoliticaNoSoportada, {"version_politica": "decision-policy/privada-v9"}),
    ],
    ids=[
        "corte",
        "as-of",
        "backtest-sin-known-at",
        "sin-zona",
        "futuro",
        "corte-desconocido",
        "politica",
    ],
)
def test_lo_que_no_se_puede_decidir_no_registra_nada(tmp_path, error, opciones):
    pequeno(tmp_path)
    with sesion() as s:
        creado = s.exec(
            select(CorteCanonico.creado_en).where(CorteCanonico.fecha_corte == C4)
        ).one()
    corte = uuid4() if opciones.pop("corte", None) else corte_id(C4)
    if opciones.get("known_at") == "manana":
        opciones["known_at"] = datetime.now(UTC) + timedelta(days=1)
    elif opciones.get("known_at") == "antes_del_corte":
        opciones["known_at"] = creado - timedelta(seconds=1)

    with sesion() as s, pytest.raises(error) as fallo:
        motor.abrir(
            s,
            corte,
            as_of=opciones.get("as_of"),
            known_at=opciones.get("known_at"),
            modo=opciones.get("modo", ModoDecisionV2.OPERATIVO),
            version_politica=opciones.get("version_politica"),
            zona=ZONA,
            max_intentos=5,
        )

    assert fallo.value.codigo == error.codigo
    assert _cuantos(EjecucionDecisionV2) == 0
    assert _cuantos(TrabajoOrquestacion, TrabajoOrquestacion.tipo == TipoTrabajo.DECISION_V2) == 0


def test_un_corte_que_ya_no_es_el_mas_reciente_se_decide_solo_como_backtest(tmp_path):
    pequeno(tmp_path)

    with sesion() as s, pytest.raises(CorteNoVigente, match="BACKTEST"):
        motor.abrir(
            s,
            corte_id(C3),
            as_of=None,
            known_at=None,
            modo=ModoDecisionV2.OPERATIVO,
            version_politica=None,
            zona=ZONA,
            max_intentos=5,
        )
    backtest = decidir_v2(C3, known_at=reloj(), modo=ModoDecisionV2.BACKTEST)

    assert (backtest.estado, backtest.modo, backtest.as_of) == ("EXITOSA", "BACKTEST", C3)
    assert backtest.cuentas_decididas == 3


def test_las_mismas_entradas_no_se_publican_dos_veces(tmp_path):
    pequeno(tmp_path)
    k = reloj()
    primera = decidir_v2(known_at=k)

    with pytest.raises(DecisionV2YaGenerada) as repetida:
        abrir_v2(known_at=k)
    # Otro known_at es otra entrada: se decide.
    segunda = decidir_v2(known_at=reloj())

    assert repetida.value.previa.id == primera.id
    assert primera.estado == segunda.estado == "EXITOSA"
    assert primera.firma_inputs != segunda.firma_inputs
    assert _cuantos(ResultadoDecisionV2) == 6


def test_un_corte_se_decide_a_lo_mas_una_vez_a_la_vez(tmp_path):
    pequeno(tmp_path)
    activa = abrir_v2()

    with pytest.raises(DecisionV2EnProceso) as error:
        abrir_v2(known_at=reloj())

    assert error.value.activa.id == activa
    assert _cuantos(EjecucionDecisionV2) == 1


def test_si_otra_publica_la_misma_firma_primero_esta_queda_fallida(tmp_path):
    pequeno(tmp_path)
    ejecucion_id = abrir_v2()
    abierta = ejecucion(ejecucion_id)
    # Otra ejecucion con la misma firma ya publico (llega por otro camino, como un backfill).
    with sesion() as s:
        ganadora = EjecucionDecisionV2(
            **{
                c: getattr(abierta, c)
                for c in (
                    "corte_canonico_id",
                    "despacho_id",
                    "cartera_id",
                    "as_of",
                    "known_at",
                    "modo",
                    "zona_horaria",
                    "version_motor",
                    "version_features",
                    "version_politica",
                    "hash_politica",
                    "firma_inputs",
                    "cuentas_objetivo",
                )
            },
            estado="EXITOSA",
            resultado="DECISION_PUBLICADA",
            cuentas_decididas=3,
        )
        s.add(ganadora)
        s.commit()
        ganadora_id = ganadora.id

    with pytest.raises(DecisionV2YaGenerada) as perdio:
        motor.decidir(ejecucion_id)

    assert perdio.value.previa.id == ganadora_id
    perdedora = ejecucion(ejecucion_id)
    assert (perdedora.estado, perdedora.resultado, perdedora.cuentas_decididas) == (
        "FALLIDA",
        "YA_DECIDIDA",
        0,
    )
    assert _cuantos(ResultadoDecisionV2) == 0


# --- todo o nada ----------------------------------------------------------------------------------


class Falla(Exception):
    pass


@pytest.mark.parametrize("donde", motor.PUNTOS_DE_FALLA)
def test_una_falla_en_cualquier_punto_no_publica_nada(tmp_path, monkeypatch, donde):
    pequeno(tmp_path)
    k = reloj()
    ejecucion_id = abrir_v2(known_at=k)

    def fallar_en(nombre: str) -> None:
        if nombre == donde:
            raise Falla(nombre)

    monkeypatch.setattr(motor, "_punto_de_falla", fallar_en)
    motor.ejecutar_decision_v2(ejecucion_id)

    fallida = ejecucion(ejecucion_id)
    assert (fallida.estado, fallida.resultado) == ("FALLIDA", "ERROR_INTERNO")
    assert (fallida.cuentas_decididas, fallida.cuentas_objetivo) == (0, 3)
    assert "Falla" in fallida.detalle
    assert _cuantos(FeatureDecisionCuenta) == _cuantos(ResultadoDecisionV2) == 0
    assert _sin_temporales()
    # Una FALLIDA no estorba: las mismas entradas se vuelven a pedir y se publican completas.
    monkeypatch.undo()
    otra = decidir_v2(known_at=k)
    assert (otra.estado, otra.cuentas_decididas) == ("EXITOSA", 3)
    assert otra.firma_inputs == fallida.firma_inputs


def test_una_version_que_el_servicio_no_aplica_falla_sin_publicar(tmp_path):
    pequeno(tmp_path)
    ejecucion_id = abrir_v2()
    with sesion() as s:
        s.execute(
            update(EjecucionDecisionV2)
            .where(EjecucionDecisionV2.id == ejecucion_id)
            .values(hash_politica="f" * 64)
        )
        s.commit()

    motor.ejecutar_decision_v2(ejecucion_id)

    fallida = ejecucion(ejecucion_id)
    assert (fallida.estado, fallida.resultado) == ("FALLIDA", "VERSION_NO_SOPORTADA")
    assert _cuantos(ResultadoDecisionV2) == 0


def test_una_cuenta_que_la_politica_no_sabe_decidir_detiene_todas(tmp_path):
    pequeno(tmp_path)
    with sesion() as s:
        s.execute(
            update(SnapshotCuenta)
            .where(
                SnapshotCuenta.cuenta_canonica_id == cuenta(2).id, SnapshotCuenta.fecha_corte == C4
            )
            .values(dias_atraso=-3)
        )
        s.commit()

    fallida = decidir_v2()

    assert (fallida.estado, fallida.resultado) == ("FALLIDA", "CUENTAS_NO_DECIDIBLES")
    assert (fallida.cuentas_fallidas, fallida.cuentas_decididas) == (1, 0)
    assert _cuantos(FeatureDecisionCuenta) == _cuantos(ResultadoDecisionV2) == 0


def test_si_el_corte_no_tiene_las_cuentas_que_dice_no_se_publica(tmp_path):
    pequeno(tmp_path)
    with sesion() as s:
        s.execute(update(CorteCanonico).where(CorteCanonico.fecha_corte == C4).values(cuentas=4))
        s.commit()

    fallida = decidir_v2()

    assert (fallida.estado, fallida.resultado, fallida.cuentas_objetivo) == (
        "FALLIDA",
        "DATOS_INCONSISTENTES",
        4,
    )
    assert _cuantos(ResultadoDecisionV2) == 0


def test_la_cola_que_agota_sus_intentos_la_deja_fallida_sin_publicar(tmp_path):
    pequeno(tmp_path)
    ejecucion_id = abrir_v2()

    with sesion() as s:
        assert objetivos.fallar(s, TipoTrabajo.DECISION_V2, ejecucion_id, "Se agotaron.")
        s.commit()

    fallida = ejecucion(ejecucion_id)
    assert (fallida.estado, fallida.resultado, fallida.detalle) == (
        "FALLIDA",
        "INTENTOS_AGOTADOS",
        "Se agotaron.",
    )


# --- varios workers -------------------------------------------------------------------------------


def _en_otro_hilo(funcion, *argumentos) -> tuple[threading.Thread, dict]:
    salida: dict = {}

    def correr() -> None:
        try:
            salida["resultado"] = funcion(*argumentos)
        except BaseException as exc:  # pragma: no cover - la prueba lo reporta
            salida["error"] = exc

    hilo = threading.Thread(target=correr, daemon=True)
    hilo.start()
    return hilo, salida


def _detener_en(monkeypatch, punto: str) -> tuple[threading.Event, threading.Event]:
    """El primer hilo que llega a `punto` se detiene ahi, con la ejecucion bloqueada, hasta que la
    prueba lo suelte. Los demas pasan de largo."""
    llego, soltar = threading.Event(), threading.Event()

    def pausa(nombre: str) -> None:
        if nombre == punto and not llego.is_set():
            llego.set()
            assert soltar.wait(ESPERA)

    monkeypatch.setattr(motor, "_punto_de_falla", pausa)
    return llego, soltar


def test_dos_workers_con_la_misma_decision_publican_una_sola_vez(tmp_path, monkeypatch):
    pequeno(tmp_path)
    ejecucion_id = abrir_v2(known_at=reloj())
    llego, soltar = _detener_en(monkeypatch, motor.ANTES_DE_CERRAR)

    primero, salida_1 = _en_otro_hilo(motor.decidir, ejecucion_id)
    assert llego.wait(ESPERA)
    # El segundo llega mientras el primero tiene la ejecucion bloqueada: espera.
    segundo, salida_2 = _en_otro_hilo(motor.decidir, ejecucion_id)
    time.sleep(0.5)
    assert segundo.is_alive()

    soltar.set()
    for hilo in (primero, segundo):
        hilo.join(ESPERA)

    assert "error" not in salida_1 and "error" not in salida_2
    terminada = ejecucion(ejecucion_id)
    assert (terminada.estado, terminada.cuentas_decididas) == ("EXITOSA", 3)
    assert _cuantos(ResultadoDecisionV2) == _cuantos(FeatureDecisionCuenta) == 3


def test_un_worker_que_pierde_el_lease_a_media_decision_no_publica(tmp_path, monkeypatch):
    pequeno(tmp_path)
    with sesion() as s:
        # La historia y el motor de pagos ya se ejecutaron a mano: sus trabajos se dan por
        # terminados, para que la cola solo tenga el de la decision.
        s.execute(
            update(TrabajoOrquestacion).values(
                estado=EstadoTrabajo.COMPLETADO, terminado_en=func.now()
            )
        )
        s.commit()
    ejecucion_id = abrir_v2(known_at=reloj())
    # Sin latidos durante la prueba: el lease vence porque la prueba lo vence.
    config = Config(worker_lease_segundos=600, worker_heartbeat_segundos=300)
    calculo, soltar = _detener_en(monkeypatch, motor.ANTES_DE_CERRAR)
    reclamo_a = cola.reclamar("worker-a", config.worker_lease_segundos)
    assert reclamo_a.tipo == TipoTrabajo.DECISION_V2
    hilo_a, salida_a = _en_otro_hilo(worker.procesar_reclamo, reclamo_a, "worker-a", config)
    assert calculo.wait(ESPERA)

    # A ya inserto sus features y sus decisiones en su transaccion, pero su lease vence antes de
    # que confirme: B toma el trabajo y espera la ejecucion, que A todavia tiene bloqueada.
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
    # A no publico: al confirmar, su trabajo ya era de B, y se revirtio entero.
    assert salida_a["resultado"].estado is None
    assert salida_b["resultado"].estado == EstadoTrabajo.COMPLETADO
    trabajo = _trabajo_de(ejecucion_id)
    assert (trabajo.estado, trabajo.intentos, trabajo.worker_id) == ("COMPLETADO", 2, None)
    terminada = ejecucion(ejecucion_id)
    assert (terminada.estado, terminada.cuentas_decididas) == ("EXITOSA", 3)
    assert _cuantos(ResultadoDecisionV2) == 3 and _cuantos(EjecucionDecisionV2) == 1


def test_dos_cortes_a_la_vez_no_se_estorban(tmp_path):
    pequeno(tmp_path)
    k = reloj()
    del_16 = abrir_v2(C4, known_at=k)
    del_9 = abrir_v2(C3, known_at=k, modo=ModoDecisionV2.BACKTEST)

    hilos = [_en_otro_hilo(motor.ejecutar_decision_v2, e) for e in (del_16, del_9)]
    for hilo, salida in hilos:
        hilo.join(ESPERA)
        assert "error" not in salida

    for ejecucion_id, as_of in ((del_16, C4), (del_9, C3)):
        terminada = ejecucion(ejecucion_id)
        assert (terminada.estado, terminada.as_of, terminada.cuentas_decididas) == (
            "EXITOSA",
            as_of,
            3,
        )
    assert _cuantos(ResultadoDecisionV2) == 6
    # Cada una con su propio dia: al final del 9 no se conocia ningun pago observado (el archivo
    # empieza el 14), asi que la familia de pagos no estaba disponible y es NULL, no cero.
    with sesion() as s:
        recuperaciones = dict(
            s.execute(
                text(
                    "SELECT e.as_of, sum(f.recuperacion_interpretada_7d) FROM "
                    "feature_decision_cuenta f JOIN ejecucion_decision_v2 e "
                    "ON e.id = f.ejecucion_decision_v2_id GROUP BY e.as_of"
                )
            ).all()
        )
    assert {
        fecha: None if monto is None else str(monto) for fecha, monto in recuperaciones.items()
    } == {
        C4: "350.00",
        C3: None,
    }
    assert ejecucion(del_9).disponibilidad["pagos"] == {"7d": False, "30d": False, "90d": False}


# --- la frontera de conocimiento ------------------------------------------------------------------


def test_la_frontera_espera_a_que_confirme_un_registro_que_empezo_antes_de_known_at(
    tmp_path, monkeypatch
):
    """Una gestion que se registra en una transaccion que empezo antes de known_at lleva un
    registrado_en anterior a known_at, pero todavia no se ve. Si la decision no la esperara, la
    omitiria, y reconstruirla despues la incluiria: la decision no seria reproducible."""
    pequeno(tmp_path)
    en_curso, confirmar = threading.Event(), threading.Event()
    registrar = registro._evento

    def evento_que_espera(*args, **kwargs):
        hecho = registrar(*args, **kwargs)
        en_curso.set()
        assert confirmar.wait(ESPERA)
        return hecho

    monkeypatch.setattr(registro, "_evento", evento_que_espera)
    hilo, salida = _en_otro_hilo(gestion_de, cuenta(3).cuenta_id, "2026-09-15T10:00:00-06:00")
    assert en_curso.wait(ESPERA)
    # known_at es posterior al inicio de la transaccion del registro, que no ha confirmado.
    ejecucion_id = abrir_v2()
    with sesion() as s:
        assert not motor.frontera_asentada(s, ejecucion(ejecucion_id).known_at)
    with pytest.raises(FronteraSinAsentar):
        motor.esperar_frontera(ejecucion(ejecucion_id).known_at, espera=0.5)

    decision, salida_decision = _en_otro_hilo(motor.ejecutar_decision_v2, ejecucion_id)
    time.sleep(0.5)
    assert decision.is_alive()  # espera la frontera, sin haber leido nada
    confirmar.set()
    for h in (hilo, decision):
        h.join(ESPERA)

    assert "error" not in salida and "error" not in salida_decision
    terminada = ejecucion(ejecucion_id)
    assert terminada.estado == "EXITOSA"
    with sesion() as s:
        gestiones = s.exec(
            select(FeatureDecisionCuenta.gestiones_7d).where(
                FeatureDecisionCuenta.ejecucion_decision_v2_id == ejecucion_id,
                FeatureDecisionCuenta.cuenta_canonica_id == cuenta(3).id,
            )
        ).one()
    assert gestiones == 1


def test_una_frontera_que_no_se_asienta_devuelve_el_trabajo_a_la_cola_con_su_known_at(
    tmp_path, monkeypatch, trabajar
):
    pequeno(tmp_path)
    with sesion() as s:
        s.execute(
            update(TrabajoOrquestacion).values(
                estado=EstadoTrabajo.COMPLETADO, terminado_en=func.now()
            )
        )
        s.commit()
    ejecucion_id = abrir_v2()
    known_at = ejecucion(ejecucion_id).known_at
    esperar = motor.esperar_frontera
    intentos = []

    def primera_vez_no(known: datetime, **opciones) -> None:
        intentos.append(known)
        if len(intentos) == 1:
            raise FronteraSinAsentar("Hay una transaccion abierta desde antes de known_at.")
        esperar(known, **opciones)

    monkeypatch.setattr(motor, "esperar_frontera", primera_vez_no)
    # Una espera larga entre intentos: el mismo `trabajar` no lo vuelve a tomar, por lenta que vaya
    # la maquina. Se vence en la base, sin dormir.
    config = Config(worker_backoff_segundos=60)

    (primero,) = trabajar(config)
    assert primero.estado == EstadoTrabajo.PENDIENTE  # vuelve a la cola, sin tocar la ejecucion
    assert ejecucion(ejecucion_id).estado == "EN_PROCESO"
    with sesion() as s:
        s.execute(
            update(TrabajoOrquestacion)
            .where(TrabajoOrquestacion.ejecucion_decision_v2_id == ejecucion_id)
            .values(disponible_desde=func.now())
        )
        s.commit()
    (segundo,) = trabajar(config)

    assert segundo.estado == EstadoTrabajo.COMPLETADO
    terminada = ejecucion(ejecucion_id)
    assert terminada.estado == "EXITOSA"
    # Los dos intentos decidieron con el mismo known_at: el que se fijo al abrir.
    assert intentos == [known_at, known_at] and terminada.known_at == known_at
    assert _trabajo_de(ejecucion_id).intentos == 2


# --- su trabajo durable ---------------------------------------------------------------------------


def test_el_worker_decide_despues_de_lo_que_decision_v2_lee(tmp_path, trabajar):
    pequeno(tmp_path)
    ejecucion_id = abrir_v2()
    # Llega otro archivo de pagos despues de pedir la decision: su historia y su interpretacion van
    # antes en la cola, aunque la decision no los vera (llegaron despues de known_at).
    from historia_escenarios import ingerir_pagos_de, pago

    ingerir_pagos_de(tmp_path, "tarde.csv", [pago(3, "2026-09-15 10:00:00", "70.00")])

    procesados = trabajar()

    tipos = [p.tipo for p in procesados]
    assert tipos[-1] == TipoTrabajo.DECISION_V2
    assert {TipoTrabajo.HISTORIA, TipoTrabajo.MOTOR_PAGOS} <= set(tipos[:-1])
    terminada = ejecucion(ejecucion_id)
    assert terminada.estado == "EXITOSA"
    with sesion() as s:
        tres = s.exec(
            select(FeatureDecisionCuenta.recuperacion_interpretada_7d).where(
                FeatureDecisionCuenta.ejecucion_decision_v2_id == ejecucion_id,
                FeatureDecisionCuenta.cuenta_canonica_id == cuenta(3).id,
            )
        ).one()
    assert str(tres) == "0.00"  # el pago tardio no se conocia a known_at
    assert _trabajo_de(ejecucion_id).estado == "COMPLETADO"


def test_en_primer_plano_decide_con_su_propio_trabajo(tmp_path):
    pequeno(tmp_path)

    terminada = decidir_v2_en_primer_plano(corte_id(C4))

    assert (terminada.estado, terminada.cuentas_decididas) == ("EXITOSA", 3)
    trabajo = _trabajo_de(terminada.id)
    assert (trabajo.estado, trabajo.intentos, trabajo.worker_id) == ("COMPLETADO", 1, None)
