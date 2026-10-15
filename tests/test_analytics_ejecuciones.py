"""analytics/v1 contra PostgreSQL, con el escenario golden (`analytics_escenarios`): abrir con un
manifiesto explicito y lo que no se puede abrir, la misma firma, publicar todo o nada en cualquier
punto de falla, un control que no cuadra, la cola, dos workers, el lease perdido, la reconstruccion
identica sin importar el orden fisico de las filas, lo publicado inmutable y la bitacora sin datos
de una persona.

Cada prueba que publica pide su propia frontera de conocimiento (`frontera`), un instante despues de
K2 en que no se registro nada: lo mismo que K2, con otra firma.
"""

from __future__ import annotations

import itertools
import logging
import threading
import time
from dataclasses import replace
from datetime import date, datetime, timedelta
from uuid import UUID

import numpy as np
import pytest
from analytics_escenarios import (
    AS_OF_A,
    AS_OF_B,
    CORTE,
    CORTE_ANTERIOR,
    ZONA,
    Cuenta,
    abrir,
    agregados,
    analizar,
    borrar,
    controles,
    decidir,
    ejecucion,
    golden,
    resultado_logico,
)
from geografia_escenarios import corte_de
from historia_escenarios import SEMILLA, estado
from motor_pagos_escenarios import historiar_todo
from sqlalchemy import func, text, update
from sqlalchemy.exc import DBAPIError
from sqlmodel import select

from motor_cartera.analytics import ejecuciones as motor
from motor_cartera.analytics import manifiesto
from motor_cartera.analytics.catalogo import VERSION_CATALOGO, hash_catalogo
from motor_cartera.analytics.politica import PUBLIC_V1, Granularidad, Modo, hash_politica
from motor_cartera.config import Config
from motor_cartera.db.modelos import (
    EjecucionAnalytics,
    EjecucionEvaluacionPromesas,
    EstadoTrabajo,
    TipoTrabajo,
    TrabajoOrquestacion,
)
from motor_cartera.db.sesion import sesion
from motor_cartera.evaluacion import ejecuciones as evaluacion
from motor_cartera.generador.oficial import escribir_cartera
from motor_cartera.ingesta.corridas import ingerir_archivo
from motor_cartera.orquestacion import cola, objetivos, worker

pytestmark = pytest.mark.usefixtures("bd_del_modulo")

ESPERA = 60
NO_EXISTE = UUID("00000000-0000-4000-8000-000000000000")
TABLAS = ("entrada_analytics", "agregado_analytics", "control_analytics")
_SIGUIENTE = itertools.count(1)


@pytest.fixture(scope="module")
def g(bd_del_modulo, tmp_path_factory):
    return golden(tmp_path_factory.mktemp("analytics_ejecuciones"))


def frontera(g) -> datetime:
    """Un known_at nuevo, unos microsegundos despues de K2: nada se registro entre los dos."""
    return g.k2 + timedelta(microseconds=next(_SIGUIENTE))


def _cuantos(tabla: str, ejecucion_id: int | None = None) -> int:
    with sesion() as s:
        if ejecucion_id is None:
            return s.execute(text(f"SELECT count(*) FROM {tabla}")).scalar_one()
        return s.execute(
            text(f"SELECT count(*) FROM {tabla} WHERE ejecucion_analytics_id = :e"),
            {"e": ejecucion_id},
        ).scalar_one()


def _trabajo_de(ejecucion_id: int) -> TrabajoOrquestacion:
    with sesion() as s:
        return s.exec(
            select(TrabajoOrquestacion).where(
                TrabajoOrquestacion.ejecucion_analytics_id == ejecucion_id
            )
        ).one()


def _sin_temporales() -> bool:
    with sesion() as s:
        return (
            s.execute(
                text(
                    "SELECT count(*) FROM pg_class WHERE relname LIKE 'an\\_%' "
                    "AND relpersistence = 't'"
                )
            ).scalar_one()
            == 0
        )


def _cola_vacia() -> None:
    """Lo que ya se ejecuto a mano se da por terminado en la cola: solo queda lo que se abra."""
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


# --- abrir ----------------------------------------------------------------------------------------


def test_abrir_fija_su_manifiesto_sus_entradas_y_encola_su_trabajo(g):
    peticion = g.peticion(AS_OF_A, frontera(g))

    ejecucion_id = abrir(peticion)

    e = ejecucion(ejecucion_id)
    assert (e.estado, e.modo, e.granularidad, e.zona_horaria) == (
        "EN_PROCESO",
        "BACKTEST",
        "DIA",
        ZONA,
    )
    assert (e.version_motor, e.version_catalogo, e.version_politica) == (
        "analytics/v1",
        VERSION_CATALOGO,
        "analytics-public-v1",
    )
    assert (e.hash_catalogo, e.hash_politica) == (hash_catalogo(), hash_politica(PUBLIC_V1))
    assert (e.fecha_corte, e.as_of, e.horizonte_dias, e.cuentas_cohorte) == (
        CORTE,
        AS_OF_A,
        5,
        20,
    )
    assert e.known_at == peticion.known_at
    assert e.firma_inputs == manifiesto.firma_inputs(
        despacho_id=e.despacho_id,
        cartera_id=e.cartera_id,
        corte_id=g.corte.corte_id,
        fecha_corte=CORTE,
        as_of=AS_OF_A,
        known_at=peticion.known_at,
        zona=ZONA,
        granularidad=Granularidad.DIA,
        hash_del_manifiesto=e.hash_manifiesto,
    )
    with sesion() as s:
        entradas = s.execute(
            text(
                "SELECT tipo_entrada, referencia_publica, periodo_desde, periodo_hasta, "
                "disponible_en FROM entrada_analytics WHERE ejecucion_analytics_id = :e "
                "ORDER BY tipo_entrada, periodo_desde"
            ),
            {"e": ejecucion_id},
        ).all()
    assert [f[0] for f in entradas] == [
        "ATRIBUCION",
        "ATRIBUCION",
        "CAMPO_V2",
        "CORTE",
        "DECISION_V2",
        "EVALUACION_PROMESAS",
        "GEOGRAFIA",
        "MOTOR_PAGOS",
        "MOTOR_PAGOS",
        "RUTEO_V2",
        "TERRITORIAL_V2",
    ]
    pagos = [f for f in entradas if f[0] == "MOTOR_PAGOS"]
    assert [(f[1], f[2], f[3]) for f in pagos] == [
        (g.septiembre.motor_pagos_run_id, date(2026, 9, 1), date(2026, 9, 30)),
        (g.octubre.motor_pagos_run_id, date(2026, 10, 1), date(2026, 10, 31)),
    ]
    assert all(f[4] <= peticion.known_at for f in entradas)
    trabajo = _trabajo_de(ejecucion_id)
    assert (trabajo.tipo, trabajo.estado, trabajo.intentos) == ("ANALYTICS", "PENDIENTE", 0)


def test_el_orden_en_que_se_declaran_las_entradas_no_cambia_el_manifiesto(g):
    known_at = frontera(g)
    una = g.peticion(AS_OF_A, known_at)
    otra = g.peticion(
        AS_OF_A,
        known_at,
        motor_pagos=tuple(reversed(una.motor_pagos)),
        atribucion=tuple(reversed(una.atribucion)),
    )
    with sesion() as s:
        r1, r2 = manifiesto.resolver(s, una), manifiesto.resolver(s, otra)
    assert (r1.hash_manifiesto, r1.firma) == (r2.hash_manifiesto, r2.firma)
    assert r1.entradas == r2.entradas


def test_el_modo_no_entra_en_la_firma_y_operativo_toma_el_reloj_de_la_base_una_vez(g):
    known_at = frontera(g)
    with sesion() as s:
        backtest = manifiesto.resolver(s, g.peticion(AS_OF_A, known_at))
        operativo = manifiesto.resolver(s, g.peticion(AS_OF_A, known_at, modo=Modo.OPERATIVO))
        antes = s.execute(text("SELECT now()")).scalar_one()
    assert backtest.firma == operativo.firma

    ejecucion_id = abrir(g.peticion(AS_OF_A, None, modo=Modo.OPERATIVO))

    e = ejecucion(ejecucion_id)
    assert e.modo == "OPERATIVO" and e.known_at >= antes
    assert e.firma_inputs != backtest.firma


@pytest.mark.parametrize(
    ("cambios", "error"),
    [
        ({"corte_id": NO_EXISTE}, manifiesto.CorteNoEncontrado),
        ({"as_of": date(2026, 9, 29)}, manifiesto.AsOfAnteriorAlCorte),
        ({"zona_horaria": "America/Ciudad_Inventada"}, manifiesto.ZonaHorariaInvalida),
        ({"known_at": None}, manifiesto.KnownAtRequerido),
        ({"known_at": datetime(2026, 10, 12, 12)}, manifiesto.KnownAtSinZona),
        ({"version_catalogo": "analytics-metrics/public-v9"}, manifiesto.CatalogoNoSoportado),
        ({"version_politica": "analytics-public-v9"}, manifiesto.PoliticaNoSoportada),
        ({"evaluacion_promesas": NO_EXISTE}, manifiesto.EntradaNoEncontrada),
    ],
)
def test_lo_que_no_se_puede_abrir_no_registra_nada(g, cambios, error):
    cambios = dict(cambios)
    ejecuciones, trabajos = _cuantos("ejecucion_analytics"), _cuantos("trabajo_orquestacion")
    known_at = cambios.pop("known_at", frontera(g))

    with pytest.raises(error) as levantado:
        abrir(g.peticion(cambios.pop("as_of", AS_OF_A), known_at, **cambios))

    assert levantado.value.codigo == error.codigo
    assert (_cuantos("ejecucion_analytics"), _cuantos("trabajo_orquestacion")) == (
        ejecuciones,
        trabajos,
    )


def test_un_known_at_o_un_as_of_futuro_no_se_abren(g):
    with sesion() as s:
        ahora = s.execute(text("SELECT now()")).scalar_one()
    with pytest.raises(manifiesto.KnownAtFuturo):
        abrir(g.peticion(AS_OF_A, ahora + timedelta(hours=1)))
    with pytest.raises(manifiesto.AsOfFuturo):
        abrir(g.peticion(date(2026, 10, 30), frontera(g)))


def test_una_entrada_que_no_existia_a_known_at_no_se_lee(g):
    # En K0, justo despues del corte, no habia decision, ni pagos interpretados, ni nada mas.
    k0 = g.corte.creado_en + timedelta(microseconds=1)
    with pytest.raises(manifiesto.EntradaNoConocida):
        abrir(g.peticion(AS_OF_A, k0))
    with pytest.raises(manifiesto.CorteNoConocido):
        abrir(g.peticion(AS_OF_A, g.corte.creado_en - timedelta(seconds=1)))


@pytest.mark.parametrize(
    "caso",
    [
        "ventana_fuera",
        "ventana_duplicada",
        "repetida",
        "atribucion_de_otra_interpretacion",
        "atribucion_sin_su_interpretacion",
        "evaluacion_de_otro_as_of",
        "territorial_sin_geografia",
        "campo_sin_decision",
        "ruteo_sin_campo",
        "plan_fuera_del_horizonte",
    ],
)
def test_un_manifiesto_incoherente_no_se_abre(g, caso):
    known_at = frontera(g)
    sept, octu = g.septiembre.motor_pagos_run_id, g.octubre.motor_pagos_run_id
    previa = g.septiembre_previa.motor_pagos_run_id
    casos = {
        # El 30 solo toca septiembre: la interpretacion de octubre no es de su horizonte.
        "ventana_fuera": (
            CORTE,
            {"atribucion": (), "campo_v2": None, "ruteo_v2": None, "evaluacion_promesas": None},
            manifiesto.VentanaFueraDelHorizonte,
        ),
        "ventana_duplicada": (
            AS_OF_A,
            {"motor_pagos": (previa, sept, octu), "atribucion": ()},
            manifiesto.VentanaDuplicada,
        ),
        "repetida": (AS_OF_A, {"motor_pagos": (sept, sept, octu)}, manifiesto.EntradaRepetida),
        "atribucion_de_otra_interpretacion": (
            AS_OF_A,
            {"motor_pagos": (previa, octu)},
            manifiesto.AtribucionInconsistente,
        ),
        "atribucion_sin_su_interpretacion": (
            AS_OF_A,
            {"motor_pagos": (octu,)},
            manifiesto.AtribucionInconsistente,
        ),
        "evaluacion_de_otro_as_of": (
            AS_OF_A,
            {"evaluacion_promesas": g.evaluacion_b.evaluacion_run_id},
            manifiesto.EvaluacionInconsistente,
        ),
        "territorial_sin_geografia": (
            AS_OF_A,
            {"geografia": None},
            manifiesto.EncadenamientoIncorrecto,
        ),
        "campo_sin_decision": (AS_OF_A, {"decision_v2": None}, manifiesto.EncadenamientoIncorrecto),
        "ruteo_sin_campo": (AS_OF_A, {"campo_v2": None}, manifiesto.EncadenamientoIncorrecto),
        # El plan es del 1: un horizonte que termina el 30 no lo incluye.
        "plan_fuera_del_horizonte": (
            CORTE,
            {
                "motor_pagos": (sept,),
                "atribucion": (g.atribucion_septiembre.atribucion_run_id,),
                "evaluacion_promesas": None,
            },
            manifiesto.FechaOperacionFueraDelHorizonte,
        ),
    }
    as_of, cambios, error = casos[caso]
    ejecuciones = _cuantos("ejecucion_analytics")

    with pytest.raises(error) as levantado:
        abrir(g.peticion(as_of, known_at, **cambios))

    assert levantado.value.codigo == error.codigo and levantado.value.estado_http == 422
    assert _cuantos("ejecucion_analytics") == ejecuciones


def test_una_entrada_de_otro_corte_o_que_no_termino_no_se_lee(g):
    otra_decision = decidir(CORTE_ANTERIOR, backtest=True)
    with sesion() as s:
        pendiente, _ = evaluacion.abrir(
            s, "DSP_001", "CARTERA_PRINCIPAL", AS_OF_A, zona=ZONA, max_intentos=5, reusar=True
        )
        s.commit()
        pendiente_id = pendiente.evaluacion_run_id
    with sesion() as s:
        known_at = s.execute(text("SELECT now()")).scalar_one()

    with pytest.raises(manifiesto.CorteInconsistente):
        abrir(
            g.peticion(
                AS_OF_A,
                known_at,
                decision_v2=otra_decision.decision_run_id,
                campo_v2=None,
                ruteo_v2=None,
            )
        )
    with pytest.raises(manifiesto.EntradaNoExitosa):
        abrir(g.peticion(AS_OF_A, known_at, evaluacion_promesas=pendiente_id))
    with sesion() as s:
        s.execute(
            update(EjecucionEvaluacionPromesas)
            .where(EjecucionEvaluacionPromesas.evaluacion_run_id == pendiente_id)
            .values(estado="FALLIDA", resultado="ERROR_INTERNO", terminada_en=func.now())
        )
        s.commit()


def test_un_horizonte_de_mas_periodos_que_los_que_admite_la_politica_no_se_abre(g, tmp_path):
    # Un corte de hace mas de un ano, por dia: mas de 366 periodos.
    viejo = date(2025, 9, 1)
    cuentas = replace(estado([Cuenta(98)]), asignacion=np.full(1, np.datetime64("2025-06-01", "D")))
    ruta = escribir_cartera(cuentas, tmp_path / "viejo.csv", semilla=SEMILLA, fecha_corte=viejo)
    corrida = ingerir_archivo(ruta.ruta, contrato="cartera/v2", fecha_corte=viejo)
    assert corrida.estado == "EXITOSA", corrida.detalle
    historiar_todo()

    with pytest.raises(manifiesto.HorizonteDemasiadoLargo):
        abrir(
            manifiesto.Peticion(
                corte_id=corte_de(viejo).corte_id,
                as_of=date(2026, 10, 12),
                modo=Modo.OPERATIVO,
                zona_horaria=ZONA,
                granularidad=Granularidad.DIA,
            )
        )


def test_las_mismas_entradas_son_la_misma_ejecucion(g):
    peticion = g.peticion(AS_OF_A, frontera(g))
    primera = abrir(peticion)

    with pytest.raises(motor.AnalyticsEnProceso) as en_proceso:
        abrir(peticion)
    assert en_proceso.value.activa.id == primera

    motor.ejecutar_analytics(primera)
    with pytest.raises(motor.AnalyticsYaGenerada) as generada:
        abrir(peticion)
    assert generada.value.previa.id == primera
    assert ejecucion(primera).estado == "EXITOSA"


# --- todo o nada ----------------------------------------------------------------------------------


class Falla(Exception):
    pass


@pytest.mark.parametrize("donde", motor.PUNTOS_DE_FALLA)
def test_una_falla_en_cualquier_punto_no_publica_nada(g, monkeypatch, donde):
    peticion = g.peticion(AS_OF_A, frontera(g))
    ejecucion_id = abrir(peticion)

    def fallar_en(nombre: str) -> None:
        if nombre == donde:
            raise Falla(nombre)

    monkeypatch.setattr(motor, "_punto_de_falla", fallar_en)
    motor.analizar(ejecucion_id)

    fallida = ejecucion(ejecucion_id)
    assert (fallida.estado, fallida.resultado) == ("FALLIDA", "ERROR_INTERNO")
    assert (fallida.agregados_publicados, fallida.hash_agregados) == (0, None)
    assert fallida.terminada_en is not None and "Falla" in fallida.detalle
    for tabla in ("agregado_analytics", "control_analytics"):
        assert _cuantos(tabla, ejecucion_id) == 0
    assert _cuantos("entrada_analytics", ejecucion_id) == 11
    assert _sin_temporales()
    monkeypatch.undo()
    otra = analizar(peticion)
    assert (otra.estado, otra.firma_inputs) == ("EXITOSA", fallida.firma_inputs)
    assert _cuantos("agregado_analytics", otra.id) == otra.agregados_publicados > 0


def test_un_control_que_no_cuadra_deja_la_ejecucion_fallida_con_sus_controles(g, caplog):
    # La interpretacion de septiembre de antes del reverso de octubre, con la de octubre: el reverso
    # de la 6 dice anular un pago que esa interpretacion no tiene anulado. Al 8 ese reverso existe.
    peticion = g.peticion(
        AS_OF_B,
        frontera(g),
        motor_pagos=(g.septiembre_previa.motor_pagos_run_id, g.octubre.motor_pagos_run_id),
        atribucion=(g.atribucion_octubre.atribucion_run_id,),
    )
    caplog.set_level(logging.INFO, logger="motor_cartera")

    e = analizar(peticion)

    assert (e.estado, e.resultado) == ("FALLIDA", "CONTROL_FALLIDO")
    assert (e.agregados_publicados, e.hash_agregados) == (0, None)
    assert "pagos.reversos_coherentes (esperado 0, observado 1)" in e.detalle
    assert _cuantos("agregado_analytics", e.id) == 0
    guardados = controles(e.id)
    assert len(guardados) == e.controles_totales > 0
    assert e.controles_fallidos == 1
    fallido = guardados["pagos.reversos_coherentes"]
    assert (fallido["estado"], fallido["esperado"], fallido["observado"]) == ("FALLO", 0, 1)
    assert {f["estado"] for c, f in guardados.items() if c != "pagos.reversos_coherentes"} == {"OK"}
    for texto in (e.detalle, caplog.text, *(f["detalle"] for f in guardados.values())):
        assert "CU0000" not in texto


def test_al_4_las_mismas_interpretaciones_si_cuadran(g):
    # El reverso de octubre llega el 6: al 4 no existe, y no hay nada que no cuadre.
    e = analizar(
        g.peticion(
            AS_OF_A,
            frontera(g),
            motor_pagos=(g.septiembre_previa.motor_pagos_run_id, g.octubre.motor_pagos_run_id),
            atribucion=(g.atribucion_octubre.atribucion_run_id,),
        )
    )
    assert e.estado == "EXITOSA", e.detalle
    assert e.disponibilidad["familias"]["ATRIBUCION"]["estado"] == "PARCIAL"


@pytest.mark.parametrize(
    "cambio",
    [
        {"version_motor": "analytics/v9"},
        {"hash_catalogo": "0" * 64},
        {"version_politica": "analytics-public-v9"},
        {"hash_politica": "0" * 64},
    ],
)
def test_un_motor_un_catalogo_o_una_politica_que_el_servicio_no_aplica_no_publica(g, cambio):
    ejecucion_id = abrir(g.peticion(AS_OF_A, frontera(g)))
    with sesion() as s:
        s.execute(
            update(EjecucionAnalytics).where(EjecucionAnalytics.id == ejecucion_id).values(**cambio)
        )
        s.commit()

    motor.analizar(ejecucion_id)

    e = ejecucion(ejecucion_id)
    assert (e.estado, e.resultado) == ("FALLIDA", "VERSION_NO_SOPORTADA")
    assert _cuantos("agregado_analytics", ejecucion_id) == 0


def test_la_cola_que_agota_sus_intentos_la_deja_fallida_sin_publicar(g):
    ejecucion_id = abrir(g.peticion(AS_OF_A, frontera(g)))

    with sesion() as s:
        assert objetivos.fallar(s, TipoTrabajo.ANALYTICS, ejecucion_id, "Se agotaron.")
        s.commit()
        assert not objetivos.fallar(s, TipoTrabajo.ANALYTICS, ejecucion_id, "Otra vez.")

    e = ejecucion(ejecucion_id)
    assert (e.estado, e.resultado, e.detalle, e.agregados_publicados) == (
        "FALLIDA",
        "INTENTOS_AGOTADOS",
        "Se agotaron.",
        0,
    )


# --- la cola y varios workers del motor -----------------------------------------------------------


def test_un_worker_toma_el_trabajo_analytics_y_lo_cierra(g, trabajar):
    _cola_vacia()
    ejecucion_id = abrir(g.peticion(AS_OF_B, frontera(g)))

    procesados = trabajar()

    assert [(p.tipo, p.estado) for p in procesados] == [("ANALYTICS", "COMPLETADO")]
    assert ejecucion(ejecucion_id).estado == "EXITOSA"
    assert _trabajo_de(ejecucion_id).estado == "COMPLETADO"


def test_en_primer_plano_analiza_con_su_propio_trabajo(g):
    _cola_vacia()

    e = worker.analizar_en_primer_plano(g.peticion(AS_OF_A, frontera(g)))

    assert e.estado == "EXITOSA", e.detalle
    trabajo = _trabajo_de(e.id)
    assert (trabajo.estado, trabajo.intentos, trabajo.worker_id) == ("COMPLETADO", 1, None)


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
    """El primer hilo que llega a `punto` se detiene ahi, con la ejecucion bloqueada, hasta que la
    prueba lo suelte. Los demas pasan de largo."""
    llego, soltar = threading.Event(), threading.Event()

    def pausa(nombre: str) -> None:
        if nombre == punto and not llego.is_set():
            llego.set()
            assert soltar.wait(ESPERA)

    monkeypatch.setattr(motor, "_punto_de_falla", pausa)
    return llego, soltar


def test_dos_workers_del_motor_con_la_misma_firma_publican_una_sola_vez(g, monkeypatch):
    ejecucion_id = abrir(g.peticion(AS_OF_A, frontera(g)))
    llego, soltar = _detener_en(monkeypatch, motor.ANTES_DE_CERRAR)

    primero, salida_1 = _en_otro_hilo(motor.ejecutar_analytics, ejecucion_id)
    assert llego.wait(ESPERA)
    segundo, salida_2 = _en_otro_hilo(motor.ejecutar_analytics, ejecucion_id)
    time.sleep(0.5)
    assert segundo.is_alive()  # espera la fila que el primero tiene bloqueada

    soltar.set()
    for hilo in (primero, segundo):
        hilo.join(ESPERA)

    assert "error" not in salida_1 and "error" not in salida_2
    e = ejecucion(ejecucion_id)
    assert e.estado == "EXITOSA"
    assert _cuantos("agregado_analytics", ejecucion_id) == e.agregados_publicados
    with sesion() as s:
        assert (
            s.execute(
                text("SELECT count(*) FROM ejecucion_analytics WHERE firma_inputs = :f"),
                {"f": e.firma_inputs},
            ).scalar_one()
            == 1
        )


def test_un_worker_que_pierde_el_lease_despues_de_calcular_no_publica(g, monkeypatch):
    _cola_vacia()
    ejecucion_id = abrir(g.peticion(AS_OF_A, frontera(g)))
    # Sin latidos durante la prueba: el lease vence porque la prueba lo vence.
    config = Config(worker_lease_segundos=600, worker_heartbeat_segundos=300)
    calculo, soltar = _detener_en(monkeypatch, motor.DESPUES_DE_AGREGADOS)
    reclamo_a = cola.reclamar("worker-a", config.worker_lease_segundos)
    assert reclamo_a.tipo == TipoTrabajo.ANALYTICS
    hilo_a, salida_a = _en_otro_hilo(worker.procesar_reclamo, reclamo_a, "worker-a", config)
    assert calculo.wait(ESPERA)

    # A ya armo la cohorte, leyo cada dominio y agrego, en su transaccion, pero su lease vence antes
    # de que publique: B toma el trabajo y espera la ejecucion, que A tiene bloqueada.
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
    assert salida_a["resultado"].estado is None  # A no publico ni cerro
    assert salida_b["resultado"].estado == EstadoTrabajo.COMPLETADO
    trabajo = _trabajo_de(ejecucion_id)
    assert (trabajo.estado, trabajo.intentos, trabajo.worker_id) == ("COMPLETADO", 2, None)
    e = ejecucion(ejecucion_id)
    assert e.estado == "EXITOSA"
    assert _cuantos("agregado_analytics", ejecucion_id) == e.agregados_publicados


# --- reconstruir ----------------------------------------------------------------------------------


def test_reconstruir_con_el_mismo_manifiesto_da_exactamente_lo_mismo(g):
    peticion = g.peticion(AS_OF_B, frontera(g), granularidad=Granularidad.SEMANA_ISO)
    primera = analizar(peticion)
    assert primera.estado == "EXITOSA", primera.detalle
    antes = resultado_logico(primera.id)

    borrar(primera.id)
    # Otro orden fisico de lo que se lee: cada tabla reescrita por otro indice (identificadores
    # aleatorios), y las entradas del manifiesto declaradas en otro orden.
    with sesion() as s:
        for tabla, indice in (
            ("movimiento_economico_canonico", "ix_movimiento_movimiento_id"),
            ("gestion_cobranza", "uq_gestion_cobranza_gestion_id"),
            ("evento_lifecycle", "uq_evento_lifecycle_evento_id"),
            ("promesa_pago", "uq_promesa_pago_promesa_id"),
            ("snapshot_cuenta", "ix_snapshot_cuenta_historia"),
        ):
            s.execute(text(f"CLUSTER {tabla} USING {indice}"))
            s.execute(text(f"ANALYZE {tabla}"))
        s.commit()
    otra = g.peticion(
        AS_OF_B,
        peticion.known_at,
        granularidad=Granularidad.SEMANA_ISO,
        motor_pagos=tuple(reversed(peticion.motor_pagos)),
        atribucion=tuple(reversed(peticion.atribucion)),
    )
    reconstruida = analizar(otra)

    assert reconstruida.id != primera.id
    assert resultado_logico(reconstruida.id) == antes


# --- inmutable y sin datos de una persona ---------------------------------------------------------


@pytest.fixture(scope="module")
def publicada(g):
    e = analizar(g.peticion(AS_OF_A, frontera(g)))
    assert e.estado == "EXITOSA", e.detalle
    return e


@pytest.mark.parametrize("tabla", TABLAS)
def test_lo_que_analytics_publico_no_se_reescribe(publicada, tabla):
    antes = resultado_logico(publicada.id)
    for sentencia in (
        f"UPDATE {tabla} SET ejecucion_analytics_id = ejecucion_analytics_id",
        f"DELETE FROM {tabla}",
        f"DELETE FROM {tabla} WHERE false",
    ):
        with sesion() as s, pytest.raises(DBAPIError) as error:
            s.execute(text(sentencia))
        assert error.value.orig.diag.constraint_name == "tr_analytics_inmutable"
        assert "analytics/v1 no se reescribe" in str(error.value)
    assert resultado_logico(publicada.id) == antes


def test_una_ejecucion_terminada_no_cambia_ni_se_borra(publicada):
    for sentencia in (
        "UPDATE ejecucion_analytics SET detalle = 'otro' WHERE id = :e",
        "DELETE FROM ejecucion_analytics WHERE id = :e",
    ):
        with sesion() as s, pytest.raises(DBAPIError) as error:
            s.execute(text(sentencia), {"e": publicada.id})
        assert error.value.orig.diag.constraint_name == "tr_analytics_terminada"
    assert ejecucion(publicada.id).detalle == publicada.detalle


def test_lo_publicado_no_tiene_donde_guardar_una_cuenta_ni_una_coordenada():
    with sesion() as s:
        columnas = set(
            s.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema = "
                    "current_schema() AND table_name IN ('agregado_analytics', "
                    "'control_analytics', 'ejecucion_analytics', 'entrada_analytics')"
                )
            ).scalars()
        )
    for prohibida in (
        "cuenta_canonica_id",
        "cuenta_id",
        "cliente_unico",
        "actor_ref",
        "latitud",
        "longitud",
        "punto",
        "microtramo_id",
        "tramo_id",
        "polilinea",
    ):
        assert prohibida not in columnas


def test_ni_la_bitacora_ni_lo_publicado_llevan_datos_de_una_cuenta(g, caplog):
    caplog.set_level(logging.DEBUG, logger="motor_cartera")

    e = analizar(g.peticion(AS_OF_B, frontera(g)))

    assert e.estado == "EXITOSA", e.detalle
    with sesion() as s:
        ids = [
            str(x) for x in s.execute(text("SELECT cuenta_id FROM cuenta_canonica")).scalars().all()
        ]
        actores = [
            x for x in s.execute(text("SELECT actor_ref FROM gestion_cobranza")).scalars() if x
        ]
    assert actores
    publicados = agregados(e.id)
    textos = [
        caplog.text,
        e.detalle,
        str(e.disponibilidad),
        *(str(f["dimensiones"]) + str(f["motivos"]) for f in publicados.values()),
        *(f["detalle"] for f in controles(e.id).values()),
    ]
    for texto in textos:
        assert "CU0000" not in texto
        for cuenta_id in ids:
            assert cuenta_id not in texto
        for actor in actores:
            assert actor not in texto
        assert "-98." not in texto  # ninguna longitud de los puntos sinteticos
