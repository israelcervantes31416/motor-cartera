"""optimization/v1 contra PostgreSQL, sin CAMPO: el golden en la base de punta a punta (el universo
por conjuntos, la admisibilidad, la imputacion, el solver, la publicacion), lo que no se abre, lo
infactible y lo no demostrado, los puntos de falla, dos workers, el lease perdido, la reconstruccion
identica, la inmutabilidad y la privacidad.

El escenario se construye una vez por modulo (optimizacion_escenarios.py): nueve cuentas de un
corte, su decision/v2 con canales elegidos, y un modelo y un scoring sinteticos con los valores y
contactos que la prueba fija. Cada prueba registra su propia configuracion: dos planes con la misma
firma no se publican dos veces.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import timedelta
from decimal import Decimal

import numpy as np
import pytest
from optimizacion_escenarios import (
    CORTE,
    GOLDEN,
    JUEVES,
    abrir,
    admisible,
    borrar,
    cuantos,
    documento_golden,
    ejecucion,
    escenario_golden,
    ingerir,
    modelo_publicado,
    optimizar,
    plan,
    pool,
    registrar,
    restriccion,
    restricciones,
    resultados,
    scoring_sintetico,
    usos,
)
from sqlalchemy import func, text, update
from sqlalchemy.exc import DBAPIError

from motor_cartera.config import Config
from motor_cartera.db.modelos import EstadoTrabajo, TipoTrabajo, TrabajoOrquestacion
from motor_cartera.db.sesion import restriccion as restriccion_violada
from motor_cartera.db.sesion import sesion
from motor_cartera.optimizacion import ejecuciones as motor
from motor_cartera.optimizacion import formulacion as f
from motor_cartera.optimizacion import solver as solver_
from motor_cartera.optimizacion.politica import EstadoSolver
from motor_cartera.orquestacion import cola, worker
from motor_cartera.orquestacion.worker import optimizar_en_primer_plano

ESPERA = 60
D, T = "DIGITAL_GLOBAL", "TELEFONICA_GLOBAL"
PLAN_GOLDEN = {1: D, 2: T, 3: T, 4: D, 5: None, 6: None, 7: None, 8: None, 9: D}
PUBLICADAS = ("resultado_optimizacion_cuenta", "uso_pool_optimizacion")
PUBLICADAS += ("resultado_restriccion_optimizacion",)


@pytest.fixture(scope="module")
def g(bd_del_modulo, tmp_path_factory):
    return escenario_golden(tmp_path_factory.mktemp("optimizacion"))


def _desempate(ejecucion_id: int, pools: tuple[str, ...] = (D, T)) -> int:
    """El objetivo del nivel 4, de lo publicado: cada cuenta en su lugar canonico (por cuenta_id),
    cada pool en el suyo (por codigo)."""
    r = resultados(ejecucion_id)
    orden = sorted(str(x["cuenta_id"]) for x in r.values())
    asignadas = [x for x in r.values() if x["pool"] is not None]
    i = np.array([orden.index(str(x["cuenta_id"])) for x in asignadas])
    p = np.array([pools.index(x["pool"]) for x in asignadas])
    return int(f.peso_canonico(i, p, len(orden), len(pools)).sum())


def _certificado_optimo(e) -> None:
    """Cada nivel OPTIMAL, con la cota igual al objetivo y gap cero: la evidencia de optimalidad."""
    niveles = e.certificado["niveles"]
    assert [n["nivel"] for n in niveles] == [1, 2, 3, 4]
    for n in niveles:
        assert n["estado"] == "OPTIMAL", n
        assert n["mejor_cota"] == pytest.approx(n["objetivo"], abs=1e-6), n
        assert n["gap_relativo"] == 0.0, n
    assert e.solver_status == "OPTIMAL" and e.mip_gap == 0


# --- el golden en la base -------------------------------------------------------------------------


def test_el_golden_en_la_base(g):
    """Goldens 109, 117 y 119 de punta a punta: el optimo calculado a mano en
    `documento_golden`, con el universo armado por conjuntos en la base."""
    e = optimizar(g.scoring, g.decision, registrar(documento_golden()))
    assert (e.estado, e.resultado) == ("EXITOSA", "PLAN_PUBLICADO"), e.detalle
    assert plan(e.id) == PLAN_GOLDEN
    assert (e.objetivo_primario_centavos, e.objetivo_secundario, e.unidades_recurso) == (
        31_000,
        190_000_000,
        11,
    )
    assert e.desempate_canonico == _desempate(e.id)
    _certificado_optimo(e)
    # El detalle no le atribuye a HiGHS un nivel que no resolvio (uno sin variables libres).
    invocados = sum(n["invocado"] for n in e.certificado["niveles"])
    assert ("HiGHS demostro el optimo de los cuatro niveles" in e.detalle) == (invocados == 4)
    assert (f"{invocados} por HiGHS" in e.detalle) == (invocados < 4)
    assert (e.cuentas_universo, e.cuentas_elegibles, e.cuentas_asignadas) == (9, 7, 5)
    assert e.cuentas_publicadas == 9 and e.cuentas_con_imputacion == 1
    publicados = resultados(e.id)
    # Golden 117: el valor de oportunidad es la recuperacion positiva esperada, en centavos exactos.
    for c in GOLDEN:
        r = publicados[c.numero]
        assert r["valor_oportunidad_centavos"] == int(Decimal(c.valor) * 100)
        assert r["p_contacto_14d"] == Decimal(c.contacto)
        assert (r["canal_decision"], r["prioridad"], r["producto"]) == (
            c.canal,
            c.prioridad,
            c.producto,
        )
    estados = {n: (r["estado"], r["motivo"]) for n, r in publicados.items()}
    assert estados[5] == estados[6] == ("NO_SELECCIONADA", "FUERA_DE_SOLUCION_OPTIMA")
    assert estados[7] == ("NO_ELEGIBLE", "SIN_CANAL_ADMISIBLE")  # golden 114
    assert estados[8] == ("NO_ELEGIBLE", "PREDICCION_CON_IMPUTACION")  # golden 129
    assert {estados[n] for n in (1, 2, 3, 4, 9)} == {("ASIGNADA", "EN_SOLUCION_OPTIMA")}
    assert publicados[1]["unidades_recurso"] == 1 and publicados[2]["unidades_recurso"] == 4
    assert publicados[5]["unidades_recurso"] is None
    u = usos(e.id)
    assert (u[D]["capacidad_efectiva"], u[D]["utilizada"], u[D]["holgura"]) == (3, 3, 0)
    assert (u[T]["utilizada"], u[T]["unidades_consumidas"], u[T]["elegibles"]) == (2, 8, 3)
    rs = restricciones(e.id)
    minimo = rs["MIN_AUTOMOTRIZ"]
    assert (minimo["lhs"], minimo["rhs"], minimo["holgura"], minimo["binding"]) == (1, 1, 0, True)
    assert rs[f"POOL:{D}"]["binding"] and rs[f"POOL:{T}"]["binding"]
    assert e.resumen["estados"] == {"ASIGNADA": 5, "NO_SELECCIONADA": 2, "NO_ELEGIBLE": 2}


def test_sin_la_cobertura_entran_las_de_mayor_valor_y_desempata_el_orden_canonico(g):
    """Golden 118: sin el minimo, las cinco de mayor valor (35,000); la 2 y la 5 pueden ir a
    DIGITAL o a TELEFONICA con el mismo valor, contacto y unidades: el nivel 4 manda a TELEFONICA
    la de mayor cuenta_id."""
    e = optimizar(g.scoring, g.decision, registrar(documento_golden(restricciones=[])))
    assert e.estado == "EXITOSA", e.detalle
    assert (e.objetivo_primario_centavos, e.objetivo_secundario, e.unidades_recurso) == (
        35_000,
        200_000_000,
        11,
    )
    publicados = resultados(e.id)
    dos, cinco = str(publicados[2]["cuenta_id"]), str(publicados[5]["cuenta_id"])
    primera, segunda = (2, 5) if dos < cinco else (5, 2)
    esperado = {1: D, 3: T, 4: D, primera: D, segunda: T, 6: None, 7: None, 8: None, 9: None}
    assert plan(e.id) == esperado
    assert e.desempate_canonico == _desempate(e.id)
    _certificado_optimo(e)


def test_la_imputacion_se_declara(g):
    """Golden 129: con la imputacion admitida, la 8 (CON_IMPUTACION, 500.00) es elegible y entra."""
    e = optimizar(
        g.scoring,
        g.decision,
        registrar(documento_golden(permitir_predicciones_con_imputacion=True)),
    )
    assert e.estado == "EXITOSA", e.detalle
    assert plan(e.id) == {1: D, 2: T, 3: T, 4: None, 5: None, 6: None, 7: None, 8: D, 9: D}
    assert e.objetivo_primario_centavos == 75_000
    assert e.resumen["con_imputacion"] == {
        "universo": 1,
        "elegibles": 1,
        "asignadas": 1,
        "admitidas": True,
    }


def test_un_canal_admisible_sin_pool_y_uno_sin_mapeo_no_son_elegibles(g):
    """Golden 114: CAMPO admite solo CAMPO y no hay ningun pool de CAMPO: la 3 queda NO_ELEGIBLE por
    la configuracion; OTRO no tiene mapeo: la 7, SIN_CANAL_ADMISIBLE."""
    documento = documento_golden(
        admisibilidad=[
            admisible("DIGITAL", "DIGITAL"),
            admisible("TELEFONICA", "TELEFONICA"),
            admisible("CAMPO", "CAMPO"),
            admisible("OTRO", "DIGITAL", False),
        ],
        restricciones=[],
    )
    e = optimizar(g.scoring, g.decision, registrar(documento))
    assert e.estado == "EXITOSA", e.detalle
    publicados = resultados(e.id)
    assert (publicados[3]["estado"], publicados[3]["motivo"]) == (
        "NO_ELEGIBLE",
        "CONFIG_EXCLUYE_CANAL",
    )
    assert publicados[7]["motivo"] == "SIN_CANAL_ADMISIBLE"
    # Sin TELEFONICA -> DIGITAL, la 2 y la 5 solo caben en TELEFONICA.
    assert plan(e.id) == {1: D, 2: T, 3: None, 4: D, 5: T, 6: D, 7: None, 8: None, 9: None}
    assert any("CAMPO es admisible" in h["mensaje"] for h in e.diagnostico["hallazgos"])


# --- lo que no se abre ----------------------------------------------------------------------------


def test_lo_que_no_se_puede_optimizar_no_se_abre(g, tmp_path_factory):
    """Golden 127 y las demas entradas incompatibles: fallan al abrir, antes de cualquier universo o
    solver, sin registrar nada."""
    publicada = registrar(documento_golden(max_unidades_recurso=500))
    borrador = registrar(documento_golden(max_unidades_recurso=501), publicar=False)
    otra_fecha = CORTE - timedelta(days=7)
    ingerir(tmp_path_factory.mktemp("otro_corte"), GOLDEN, otra_fecha)
    de_otro_corte = scoring_sintetico(g.modelo, GOLDEN, otra_fecha, etiqueta="otro corte")
    en_proceso = scoring_sintetico(g.modelo, GOLDEN, etiqueta="en proceso", cerrar=False)
    de_un_rechazado = scoring_sintetico(
        modelo_publicado("rechazado", rechazado=True), GOLDEN, etiqueta="rechazado"
    )
    con_campo = registrar(
        documento_golden(
            pools=[
                pool(D, "DIGITAL", 3, 1),
                pool("CAMPO_G1", "CAMPO", None, 20, alcance="GESTOR_CAMPO", referencia="G1"),
            ],
            restricciones=[],
        )
    )
    con_base = registrar(
        documento_golden(
            restricciones=[restriccion("MIN_BASE_A", "MIN_CUENTAS", "BASE", "BASE_A", 1)]
        )
    )
    antes = cuantos("ejecucion_optimizacion"), cuantos("trabajo_orquestacion")
    casos = [
        ({"scoring": de_otro_corte}, "CORTES_DISTINTOS"),
        ({"scoring": en_proceso}, "SCORING_NO_EXITOSO"),
        ({"scoring": de_un_rechazado}, "MODELO_NO_PUBLICADO"),
        ({"version": borrador}, "CONFIGURACION_NO_PUBLICADA"),
        ({"version": con_campo}, "RUTEO_REQUERIDO"),
        ({"version": con_base}, "RUTEO_REQUERIDO"),
        ({"fecha": CORTE - timedelta(days=1)}, "FECHA_ANTERIOR_AL_CORTE"),
    ]
    for cambios, codigo in casos:
        entradas = {"scoring": g.scoring, "version": publicada, "fecha": JUEVES, **cambios}
        with pytest.raises(motor.OptimizacionInvalida) as error:
            abrir(entradas["scoring"], g.decision, entradas["version"], fecha=entradas["fecha"])
        assert error.value.codigo == codigo, (codigo, str(error.value))
    assert (cuantos("ejecucion_optimizacion"), cuantos("trabajo_orquestacion")) == antes


def test_la_misma_firma_no_se_abre_dos_veces(g):
    version = registrar(documento_golden(max_unidades_recurso=800))
    ejecucion_id = abrir(g.scoring, g.decision, version)
    with pytest.raises(motor.OptimizacionEnProceso):
        abrir(g.scoring, g.decision, version)
    motor.optimizar(ejecucion_id)
    with pytest.raises(motor.OptimizacionYaPublicada) as error:
        abrir(g.scoring, g.decision, version)
    assert error.value.previa.id == ejecucion_id
    # Otra fecha de operacion es otro plan.
    otra = optimizar(g.scoring, g.decision, version, fecha=JUEVES + timedelta(days=1))
    assert otra.estado == "EXITOSA" and otra.firma_inputs != ejecucion(ejecucion_id).firma_inputs


# --- sin plan -------------------------------------------------------------------------------------


def _nada_publicado(ejecucion_id: int) -> None:
    for tabla in PUBLICADAS:
        assert cuantos(tabla, "ejecucion_optimizacion_id = :e", e=ejecucion_id) == 0, tabla
    e = ejecucion(ejecucion_id)
    assert (e.cuentas_publicadas, e.cuentas_asignadas, e.hash_plan) == (0, 0, None)
    assert e.objetivo_primario_centavos is None and e.terminada_en is not None


def test_un_minimo_imposible_es_infactible_sin_invocar_al_solver(g):
    """Golden 122 en la base: un minimo de 2 AUTOMOTRIZ con una sola elegible."""
    documento = documento_golden(
        restricciones=[restriccion("MIN_AUTOMOTRIZ", "MIN_CUENTAS", "PRODUCTO", "AUTOMOTRIZ", 2)]
    )
    e = optimizar(g.scoring, g.decision, registrar(documento))
    assert (e.estado, e.resultado, e.solver_status) == ("FALLIDA", "INFACTIBLE", "NO_INVOCADO")
    _nada_publicado(e.id)
    hallazgo = next(h for h in e.diagnostico["hallazgos"] if h["severidad"] == f.ERROR)
    assert hallazgo["codigo"] == "MINIMO_SUPERA_ELEGIBLES"
    assert (hallazgo["datos"]["minimo"], hallazgo["datos"]["elegibles"]) == (2, 1)
    assert "diagnostico_relajacion" not in e.diagnostico
    assert e.cuentas_elegibles == 7  # lo que se conto, aunque no haya plan


def test_una_infactibilidad_que_solo_el_solver_ve_trae_su_relajacion(g):
    """Dos NOMINA y dos ALTA son cuatro cuentas distintas, y caben tres: cada minimo por separado
    cabe, el prechequeo no lo demuestra y el solver si. La relajacion minima: una cuenta."""
    documento = documento_golden(
        max_cuentas_atendidas=3,
        restricciones=[
            restriccion("MIN_NOMINA", "MIN_CUENTAS", "PRODUCTO", "NOMINA", 2),
            restriccion("MIN_ALTA", "MIN_CUENTAS", "PRIORIDAD_DECISION", "ALTA", 2),
        ],
    )
    e = optimizar(g.scoring, g.decision, registrar(documento))
    assert (e.estado, e.resultado, e.solver_status) == ("FALLIDA", "INFACTIBLE", "INFEASIBLE")
    _nada_publicado(e.id)
    relajacion = e.diagnostico["diagnostico_relajacion"]
    assert (relajacion["estado"], relajacion["holgura_total"]) == ("RELAJACION_MINIMA", 1)
    assert "IIS" in relajacion["nota"]
    assert e.certificado["niveles"][0]["estado"] == "INFEASIBLE"


def test_sin_demostrar_el_optimo_no_se_publica(g):
    """Golden 123 en la base: un solver que se queda sin tiempo con una solucion factible."""

    def sin_tiempo(c, A, lb, ub, *, politica, superior=None):
        return solver_.ResultadoSolver(
            EstadoSolver.TIME_LIMIT,
            13,
            "Time limit reached.",
            np.ones(len(c)),
            float(c.sum()),
            float(c.sum()) - 1,
            0.5,
            10,
            1800.0,
        )

    ejecucion_id = abrir(
        g.scoring, g.decision, registrar(documento_golden(max_unidades_recurso=600))
    )
    motor.optimizar(ejecucion_id, resolvedor=sin_tiempo)
    e = ejecucion(ejecucion_id)
    assert (e.estado, e.resultado, e.solver_status) == (
        "FALLIDA",
        "OPTIMO_NO_DEMOSTRADO",
        "TIME_LIMIT",
    )
    _nada_publicado(e.id)
    (nivel,) = e.certificado["niveles"]
    assert (nivel["estado"], nivel["gap_relativo"], nivel["nodos"]) == ("TIME_LIMIT", 0.5, 10)
    assert "prueba de optimalidad" in e.detalle


# --- todo o nada ----------------------------------------------------------------------------------


class Falla(Exception):
    pass


@pytest.mark.parametrize("donde", motor.PUNTOS_DE_FALLA)
def test_una_falla_en_cualquier_punto_no_publica_nada(g, monkeypatch, donde):
    # Una configuracion propia por punto (otro presupuesto, que no aprieta): su propia firma.
    version = registrar(
        documento_golden(max_unidades_recurso=1_000 + motor.PUNTOS_DE_FALLA.index(donde))
    )
    ejecucion_id = abrir(g.scoring, g.decision, version)

    def fallar_en(nombre: str) -> None:
        if nombre == donde:
            raise Falla(nombre)

    monkeypatch.setattr(motor, "_punto_de_falla", fallar_en)
    motor.optimizar(ejecucion_id)
    fallida = ejecucion(ejecucion_id)
    assert (fallida.estado, fallida.resultado) == ("FALLIDA", "ERROR_INTERNO")
    _nada_publicado(ejecucion_id)
    monkeypatch.undo()
    otra = optimizar(g.scoring, g.decision, version)
    assert (otra.estado, otra.firma_inputs) == ("EXITOSA", fallida.firma_inputs)
    assert plan(otra.id) == PLAN_GOLDEN


def _en_otro_hilo(funcion, *argumentos, **opciones):
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
    """El primer hilo que llega a `punto` se detiene ahi, con el candado de la ejecucion, hasta que
    la prueba lo suelte. Los demas pasan de largo."""
    llego, soltar = threading.Event(), threading.Event()

    def pausa(nombre: str) -> None:
        if nombre == punto and not llego.is_set():
            llego.set()
            assert soltar.wait(ESPERA)

    monkeypatch.setattr(motor, "_punto_de_falla", pausa)
    return llego, soltar


def _cola_vacia() -> None:
    """Lo que el escenario y las otras pruebas dejaron en la cola se da por terminado: solo queda
    el trabajo que la prueba abra."""
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


def _trabajo_de(ejecucion_id: int):
    with sesion() as s:
        return s.execute(
            text("SELECT * FROM trabajo_orquestacion WHERE ejecucion_optimizacion_id = :e"),
            {"e": ejecucion_id},
        ).one()


def test_dos_workers_con_la_misma_ejecucion_publican_una_vez(g, monkeypatch):
    """El segundo espera el candado del primero y despues la encuentra terminada."""
    ejecucion_id = abrir(
        g.scoring, g.decision, registrar(documento_golden(max_unidades_recurso=700))
    )
    llego, soltar = _detener_en(monkeypatch, motor.ANTES_CERRAR)
    primero, salida_1 = _en_otro_hilo(motor.optimizar, ejecucion_id)
    assert llego.wait(ESPERA)
    segundo, salida_2 = _en_otro_hilo(motor.optimizar, ejecucion_id)
    time.sleep(0.5)
    assert segundo.is_alive()  # espera el candado del primero
    soltar.set()
    for hilo in (primero, segundo):
        hilo.join(ESPERA)
    assert "error" not in salida_1 and "error" not in salida_2
    assert ejecucion(ejecucion_id).estado == "EXITOSA"
    assert (
        cuantos("resultado_optimizacion_cuenta", "ejecucion_optimizacion_id = :e", e=ejecucion_id)
        == 9
    )


def test_un_worker_que_pierde_el_lease_no_publica(g, monkeypatch):
    """A resuelve los cuatro niveles, sin una transaccion abierta, y su lease vence antes de
    publicar: B toma el trabajo y espera el candado de A; A no publica, B si, una vez."""
    _cola_vacia()
    ejecucion_id = abrir(
        g.scoring, g.decision, registrar(documento_golden(max_unidades_recurso=900))
    )
    config = Config(worker_lease_segundos=600, worker_heartbeat_segundos=300)
    calculo, soltar = _detener_en(monkeypatch, motor.DESPUES_SOLVER_NIVEL_4)
    reclamo_a = cola.reclamar("worker-a", config.worker_lease_segundos)
    assert reclamo_a.tipo == TipoTrabajo.OPTIMIZATION
    hilo_a, salida_a = _en_otro_hilo(worker.procesar_reclamo, reclamo_a, "worker-a", config)
    assert calculo.wait(ESPERA)
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
    assert ejecucion(ejecucion_id).estado == "EXITOSA"
    assert (
        cuantos("resultado_optimizacion_cuenta", "ejecucion_optimizacion_id = :e", e=ejecucion_id)
        == 9
    )


def test_un_worker_toma_el_trabajo_de_la_cola_y_lo_cierra(g, trabajar):
    _cola_vacia()
    ejecucion_id = abrir(
        g.scoring, g.decision, registrar(documento_golden(max_unidades_recurso=950))
    )
    assert (_trabajo_de(ejecucion_id).tipo, _trabajo_de(ejecucion_id).estado) == (
        "OPTIMIZATION",
        "PENDIENTE",
    )
    (procesado,) = trabajar()
    assert (procesado.tipo, procesado.estado) == (
        TipoTrabajo.OPTIMIZATION,
        EstadoTrabajo.COMPLETADO,
    )
    assert ejecucion(ejecucion_id).estado == "EXITOSA"
    assert plan(ejecucion_id) == PLAN_GOLDEN


def test_la_optimizacion_va_despues_de_un_scoring_en_la_cola(g):
    """Prioridad 10: con un scoring por publicar, el worker lo toma antes que una optimizacion,
    aunque la optimizacion se haya encolado primero."""
    _cola_vacia()
    ejecucion_id = abrir(
        g.scoring, g.decision, registrar(documento_golden(max_unidades_recurso=970))
    )
    scoring = scoring_sintetico(g.modelo, GOLDEN, etiqueta="en la cola", cerrar=False)
    with sesion() as s:
        cola.crear(s, TipoTrabajo.PREDICTIVE_SCORING, scoring.id, max_intentos=5)
        s.commit()
    primero = cola.reclamar("worker-a", 60)
    segundo = cola.reclamar("worker-b", 60)
    assert (primero.tipo, segundo.tipo) == (
        TipoTrabajo.PREDICTIVE_SCORING,
        TipoTrabajo.OPTIMIZATION,
    )
    assert _trabajo_de(ejecucion_id).id == segundo.id
    _cola_vacia()


def test_en_primer_plano_optimiza_con_su_propio_trabajo(g):
    _cola_vacia()
    version = registrar(documento_golden(max_unidades_recurso=960))
    e = optimizar_en_primer_plano(
        scoring_run_id=g.scoring.scoring_run_id,
        decision_run_id=g.decision.decision_run_id,
        configuracion_id=version.configuracion_id,
        fecha_operacion=JUEVES,
    )
    assert e.estado == "EXITOSA" and plan(e.id) == PLAN_GOLDEN
    assert _trabajo_de(e.id).estado == "COMPLETADO"


# --- reconstruir ----------------------------------------------------------------------------------


def _sin_id(filas: dict) -> dict:
    return {
        k: {c: v for c, v in fila.items() if c != "ejecucion_optimizacion_id"}
        for k, fila in filas.items()
    }


def test_reconstruir_un_plan_da_exactamente_lo_mismo(g):
    """Golden 126: las mismas entradas dan el mismo plan, byte por byte, aunque las tablas que se
    leen cambien de orden fisico."""
    version = registrar(documento_golden(max_unidades_recurso=1_100))
    primera = optimizar(g.scoring, g.decision, version)
    antes = (_sin_id(resultados(primera.id)), _sin_id(usos(primera.id)))
    antes += (_sin_id(restricciones(primera.id)),)
    borrar(primera.id)
    with sesion() as s:
        for tabla, indice in (
            ("prediccion_cuenta", "ix_prediccion_cuenta"),
            ("resultado_decision_v2", "ix_resultado_decision_v2_cuenta"),
            ("snapshot_cuenta", "ix_snapshot_cuenta_historia"),
        ):
            s.execute(text(f"CLUSTER {tabla} USING {indice}"))
            s.execute(text(f"ANALYZE {tabla}"))
        s.commit()
    segunda = optimizar(g.scoring, g.decision, version)
    assert segunda.id != primera.id
    assert (segunda.hash_plan, segunda.firma_inputs) == (primera.hash_plan, primera.firma_inputs)
    assert segunda.certificado["niveles"][3]["objetivo"] == primera.desempate_canonico
    despues = (_sin_id(resultados(segunda.id)), _sin_id(usos(segunda.id)))
    assert despues + (_sin_id(restricciones(segunda.id)),) == antes


def test_las_mismas_predicciones_en_otro_orden_dan_el_mismo_plan(g):
    """Golden 126: otro scoring con las mismas predicciones, insertadas al reves, es otra firma de
    entradas y el mismo plan: su hash no lleva ningun id de ejecucion ni ningun instante."""
    invertido = scoring_sintetico(g.modelo, GOLDEN, etiqueta="invertido", invertido=True)
    version = registrar(documento_golden(max_unidades_recurso=1_200))
    a = optimizar(g.scoring, g.decision, version)
    b = optimizar(invertido, g.decision, version)
    assert a.firma_inputs != b.firma_inputs
    assert a.hash_plan == b.hash_plan and plan(b.id) == PLAN_GOLDEN


# --- inmutable y privado --------------------------------------------------------------------------


def test_lo_publicado_no_se_reescribe(g):
    e = optimizar(g.scoring, g.decision, registrar(documento_golden(max_unidades_recurso=1_300)))
    for sentencia, disparador in (
        (
            "UPDATE resultado_optimizacion_cuenta SET retenida = NOT retenida "
            "WHERE ejecucion_optimizacion_id = :e",
            "tr_optimizacion_inmutable",
        ),
        (
            "DELETE FROM uso_pool_optimizacion WHERE ejecucion_optimizacion_id = :e",
            "tr_optimizacion_inmutable",
        ),
        (
            "UPDATE resultado_restriccion_optimizacion SET holgura = holgura + 1 "
            "WHERE ejecucion_optimizacion_id = :e",
            "tr_optimizacion_inmutable",
        ),
        ("UPDATE ejecucion_optimizacion SET hash_plan = NULL WHERE id = :e", None),
        ("DELETE FROM ejecucion_optimizacion WHERE id = :e", None),
        (
            "UPDATE pool_capacidad_optimizacion SET capacidad = capacidad + 1 "
            "WHERE version_configuracion_optimizacion_id = (SELECT "
            "version_configuracion_optimizacion_id FROM ejecucion_optimizacion WHERE id = :e)",
            "tr_optimizacion_inmutable",
        ),
        (
            "UPDATE version_configuracion_optimizacion SET max_unidades_recurso = 1 WHERE id = "
            "(SELECT version_configuracion_optimizacion_id FROM ejecucion_optimizacion "
            "WHERE id = :e)",
            "tr_configuracion_optimizacion_ciclo",
        ),
    ):
        with sesion() as s, pytest.raises(DBAPIError) as error:
            s.execute(text(sentencia), {"e": e.id})
        assert restriccion_violada(error.value) == (disparador or "tr_optimizacion_terminada")
    assert plan(e.id) == PLAN_GOLDEN


def test_la_bitacora_no_lleva_cuentas_ni_predicciones(g, caplog):
    caplog.set_level(logging.DEBUG, logger="motor_cartera")
    e = optimizar(g.scoring, g.decision, registrar(documento_golden(max_unidades_recurso=1_400)))
    assert e.estado == "EXITOSA"
    texto = caplog.text
    assert texto  # se registro la ejecucion, sus conteos y su estado
    for r in resultados(e.id).values():
        assert str(r["cuenta_id"]) not in texto
        assert r["cliente_unico"] not in texto
    assert "CU0000" not in texto and "90.00" not in texto and "0.5000" not in texto
