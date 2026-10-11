"""campo/v2 en PostgreSQL: abrir con sus entradas declaradas y coherentes, la idempotencia, lo que
no se puede abrir, la publicacion todo o nada con fallas inyectadas, varios workers del motor, el
lease perdido, dos fechas y dos cortes a la vez, las configuraciones versionadas, la
reconstruccion, el orden de llegada y la privacidad de la bitacora."""

from __future__ import annotations

import threading
import time
from datetime import date, timedelta
from uuid import uuid4

import pytest
from campo_v2_escenarios import (
    AMOZOC,
    CAMPO,
    CORTE,
    DIGITAL,
    JUEVES,
    MERIDA,
    PUEBLA,
    SABADO,
    X0,
    Y0,
    CuentaCampo,
    abrir_cv2,
    asignaciones,
    base,
    cargas,
    coordenadas,
    decidir,
    escenario,
    espejo,
    gestor,
    ingerir,
    plan,
    planear,
    publicadas,
    publicar_campo,
    unidades,
)
from geografia_escenarios import corte_de, geolocalizar
from sqlalchemy import func, text, update
from sqlalchemy.exc import DBAPIError
from sqlmodel import select
from territorial_v2_escenarios import abrir_tv2, asignar_tv2, publicar_bases

from motor_cartera.campo_v2 import ejecuciones as motor
from motor_cartera.campo_v2 import politica
from motor_cartera.campo_v2.ejecuciones import (
    BasesInconsistentes,
    CampoV2EnProceso,
    CampoV2YaGenerado,
    ConfiguracionNoEncontrada,
    CorteInconsistente,
    CorteNoEncontrado,
    DecisionNoEncontrada,
    DecisionNoExitosa,
    FechaAnteriorAlCorte,
    PoliticaNoSoportada,
    TerritorialNoEncontrado,
    TerritorialNoExitosa,
)
from motor_cartera.config import Config
from motor_cartera.db.modelos import (
    AsignacionCampoCuenta,
    CargaGestorCampo,
    EjecucionCampoV2,
    EstadoTrabajo,
    ModoDecisionV2,
    TipoTrabajo,
    TrabajoOrquestacion,
    UnidadTerritorialCampo,
)
from motor_cartera.db.sesion import sesion
from motor_cartera.decision_v2 import ejecuciones as decision_v2
from motor_cartera.orquestacion import cola, objetivos, worker
from motor_cartera.orquestacion.worker import planificar_campo_v2_en_primer_plano

pytestmark = pytest.mark.usefixtures("bd")

ESPERA = 60
OTRO_CORTE = date(2026, 10, 7)
CUENTAS = [
    CuentaCampo(1, CAMPO, PUEBLA, X0, Y0),
    CuentaCampo(2, CAMPO, PUEBLA, X0 + 100, Y0 - 100),
    CuentaCampo(3, CAMPO, PUEBLA, X0 + 1_000, Y0),
    CuentaCampo(4, CAMPO, PUEBLA, X0 + 5_000, Y0),
    CuentaCampo(5, CAMPO, AMOZOC, 2_915_500, 790_500),
    CuentaCampo(6, CAMPO, MERIDA, 3_780_500, 1_050_500),
    CuentaCampo(7, CAMPO, PUEBLA, None, None),
    CuentaCampo(8, DIGITAL, PUEBLA, X0, Y0),
]
TABLAS = ("asignacion_campo_cuenta", "unidad_territorial_campo", "carga_gestor_campo")


# Dentro de cada prueba, no al importar: base() pasa su punto a EPSG:4326 con PostGIS, y al
# recolectar las pruebas la base de pruebas todavia puede no existir.
def _bases() -> tuple[dict, ...]:
    return (base("BASE_A", X0 - 3_000, Y0, [PUEBLA, AMOZOC]),)


def _pequeno(tmp_path, cuentas=CUENTAS, bases=None, gestores=None):
    e = escenario(tmp_path, cuentas, bases or _bases())
    e.configuracion = publicar_campo(
        e.bases, gestores or [gestor("GESTOR_001", capacidad=2), gestor("GESTOR_002", capacidad=2)]
    )
    return e


def _cuantos(modelo, *condiciones) -> int:
    with sesion() as s:
        return s.exec(select(func.count()).select_from(modelo).where(*condiciones)).one()


def _publicado(ejecucion_id: int) -> int:
    return sum(
        _cuantos(m, m.ejecucion_campo_v2_id == ejecucion_id)
        for m in (AsignacionCampoCuenta, UnidadTerritorialCampo, CargaGestorCampo)
    )


def _trabajo_de(ejecucion_id: int) -> TrabajoOrquestacion:
    with sesion() as s:
        return s.exec(
            select(TrabajoOrquestacion).where(
                TrabajoOrquestacion.ejecucion_campo_v2_id == ejecucion_id
            )
        ).one()


def _sin_temporales() -> bool:
    with sesion() as s:
        return (
            s.execute(
                text(
                    "SELECT count(*) FROM pg_class WHERE relname LIKE 'cv2\\_%' "
                    "AND relpersistence = 't'"
                )
            ).scalar_one()
            == 0
        )


def _cola_vacia() -> None:
    """Lo que ya se ejecuto a mano (la historia, la decision, la geografia, territorial/v2) se da
    por terminado en la cola, para que solo quede el trabajo CAMPO_V2."""
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


def _foto(ejecucion_id: int) -> list[tuple]:
    with sesion() as s:
        asignadas = [
            tuple(f)
            for f in s.execute(
                text(
                    "SELECT c.cliente_unico, a.estado, a.prioridad, b.codigo, g.codigo, "
                    "a.zona_id, a.subzona_id, a.tramo_id, a.microtramo_id, a.es_rebalanceada, "
                    "a.orden_prioridad, a.motivos::text FROM asignacion_campo_cuenta a "
                    "JOIN cuenta_canonica c ON c.id = a.cuenta_canonica_id "
                    "LEFT JOIN base_operativa b ON b.id = a.base_operativa_id "
                    "LEFT JOIN gestor_campo g ON g.id = a.gestor_campo_id "
                    "WHERE a.ejecucion_campo_v2_id = :e ORDER BY c.cliente_unico"
                ),
                {"e": ejecucion_id},
            ).all()
        ]
        unidades_ = [
            tuple(f)
            for f in s.execute(
                text(
                    "SELECT u.unidad_id, u.nivel, u.padre_id, b.codigo, u.cve_ent, u.cvegeo, "
                    "u.candidatas, u.asignadas, g.codigo FROM unidad_territorial_campo u "
                    "JOIN base_operativa b ON b.id = u.base_operativa_id "
                    "LEFT JOIN gestor_campo g ON g.id = u.propietario_gestor_campo_id "
                    "WHERE u.ejecucion_campo_v2_id = :e ORDER BY u.unidad_id"
                ),
                {"e": ejecucion_id},
            ).all()
        ]
        cargas_ = [
            tuple(f)
            for f in s.execute(
                text(
                    "SELECT g.codigo, c.estado_jornada, c.capacidad, c.asignadas, c.rebalanceadas, "
                    "c.microtramos, c.tramos, c.utilizacion, c.ventanas::text "
                    "FROM carga_gestor_campo c JOIN gestor_campo g ON g.id = c.gestor_campo_id "
                    "WHERE c.ejecucion_campo_v2_id = :e ORDER BY g.codigo"
                ),
                {"e": ejecucion_id},
            ).all()
        ]
    return [asignadas, unidades_, cargas_]


# --- abrir ----------------------------------------------------------------------------------------


def test_abrir_fija_sus_entradas_declaradas_y_encola_su_trabajo(tmp_path):
    e = _pequeno(tmp_path)

    ejecucion_id = abrir_cv2(e, e.configuracion, JUEVES)

    p = plan(ejecucion_id)
    assert (p.estado, p.resultado, p.version_motor, p.version_politica) == (
        "EN_PROCESO",
        None,
        "campo/v2",
        "campo-public-v2",
    )
    assert (
        p.corte_canonico_id,
        p.ejecucion_decision_v2_id,
        p.ejecucion_territorial_v2_id,
        p.version_bases_operativas_id,
        p.version_configuracion_campo_id,
        p.fecha_operacion,
    ) == (e.corte.id, e.decision.id, e.territorial.id, e.bases.id, e.configuracion.id, JUEVES)
    assert p.cuentas_evaluadas_decision == 8
    assert p.hash_politica == politica.hash_politica()
    assert p.firma_inputs == politica.firma_inputs(
        corte_id=e.corte.corte_id,
        decision_run_id=e.decision.decision_run_id,
        firma_decision=e.decision.firma_inputs,
        territorial_run_id=e.territorial.territorial_run_id,
        firma_territorial=e.territorial.firma_inputs,
        configuracion_campo_version_id=e.configuracion.configuracion_campo_version_id,
        hash_configuracion=e.configuracion.hash_configuracion,
        fecha_operacion=JUEVES,
    )
    trabajo = _trabajo_de(ejecucion_id)
    assert (trabajo.tipo, trabajo.estado, trabajo.intentos) == ("CAMPO_V2", "PENDIENTE", 0)


@pytest.mark.parametrize(
    "que",
    [
        "corte",
        "decision",
        "decision_no_exitosa",
        "territorial",
        "territorial_no_exitosa",
        "decision_de_otro_corte",
        "territorial_de_otro_corte",
        "configuracion",
        "bases_inconsistentes",
        "fecha_anterior",
        "politica",
    ],
)
def test_lo_que_no_se_puede_abrir_no_registra_nada(tmp_path, que):
    e = _pequeno(tmp_path)
    corte_id = e.corte.corte_id
    decision_id = e.decision.decision_run_id
    territorial_id = e.territorial.territorial_run_id
    configuracion_id = e.configuracion.configuracion_campo_version_id
    fecha, opciones = JUEVES, {}
    if que == "corte":
        corte_id, error = uuid4(), CorteNoEncontrado
    elif que == "decision":
        decision_id, error = uuid4(), DecisionNoEncontrada
    elif que == "decision_no_exitosa":
        with sesion() as s:
            abierta = decision_v2.abrir(
                s,
                corte_id,
                as_of=None,
                known_at=None,
                modo=ModoDecisionV2.OPERATIVO,
                version_politica=None,
                zona="America/Mexico_City",
                max_intentos=5,
            )
            s.commit()
            decision_id, error = abierta.decision_run_id, DecisionNoExitosa
    elif que == "territorial":
        territorial_id, error = uuid4(), TerritorialNoEncontrado
    elif que == "territorial_no_exitosa":
        otras = publicar_bases(base("BASE_Z", X0, Y0, [PUEBLA]), nombre="Otras")
        with sesion() as s:
            territorial_id = s.get_one(
                type(e.territorial), abrir_tv2(CORTE, e.geografia, otras)
            ).territorial_run_id
        error = TerritorialNoExitosa
    elif que in ("decision_de_otro_corte", "territorial_de_otro_corte"):
        ingerir(tmp_path, [CuentaCampo(1), CuentaCampo(9)], OTRO_CORTE, nombre="otro")
        if que == "decision_de_otro_corte":
            decision_id = decidir(OTRO_CORTE).decision_run_id
        else:
            otra_geografia = geolocalizar(OTRO_CORTE, e.referencia)
            territorial_id = asignar_tv2(OTRO_CORTE, otra_geografia, e.bases).territorial_run_id
        error = CorteInconsistente
    elif que == "configuracion":
        configuracion_id, error = uuid4(), ConfiguracionNoEncontrada
    elif que == "bases_inconsistentes":
        otras = publicar_bases(*_bases(), base("BASE_B", X0 + 3_000, Y0, [MERIDA]), nombre="Otras")
        configuracion_id = publicar_campo(
            otras, [gestor("GESTOR_001")]
        ).configuracion_campo_version_id
        error = BasesInconsistentes
    elif que == "fecha_anterior":
        fecha, error = CORTE - timedelta(days=1), FechaAnteriorAlCorte
    else:
        opciones["version_politica"], error = "campo-public-v9", PoliticaNoSoportada

    with sesion() as s, pytest.raises(error) as fallo:
        motor.abrir(
            s,
            corte_id,
            decision_id,
            territorial_id,
            configuracion_id,
            fecha,
            max_intentos=5,
            **opciones,
        )

    assert fallo.value.codigo == error.codigo
    assert _cuantos(EjecucionCampoV2) == 0
    assert _cuantos(TrabajoOrquestacion, TrabajoOrquestacion.tipo == TipoTrabajo.CAMPO_V2) == 0


def test_el_mismo_dia_de_operacion_que_el_corte_se_puede_planear(tmp_path):
    e = _pequeno(tmp_path)

    assert planear(e, e.configuracion, CORTE).estado == "EXITOSA"


def test_las_mismas_entradas_no_se_abren_dos_veces(tmp_path):
    e = _pequeno(tmp_path)
    activa = abrir_cv2(e, e.configuracion, JUEVES)
    with pytest.raises(CampoV2EnProceso):
        abrir_cv2(e, e.configuracion, JUEVES)

    motor.ejecutar_campo_v2(activa)

    with pytest.raises(CampoV2YaGenerado) as error:
        abrir_cv2(e, e.configuracion, JUEVES)
    assert error.value.previo.id == activa
    assert _cuantos(EjecucionCampoV2) == 1


def test_otra_fecha_es_otro_plan_y_los_dos_coexisten(tmp_path):
    e = _pequeno(tmp_path)

    jueves = planear(e, e.configuracion, JUEVES)
    sabado = planear(e, e.configuracion, SABADO)

    assert jueves.firma_inputs != sabado.firma_inputs
    assert (jueves.estado, sabado.estado) == ("EXITOSA", "EXITOSA")
    # El sabado el calendario de lunes a viernes no tiene jornada: nadie esta disponible.
    assert jueves.asignadas == 4 and sabado.asignadas == 0
    assert sabado.sin_gestor == 5
    assert {c["jornada"] for c in cargas(sabado.id).values()} == {"SIN_JORNADA"}


# --- lo que publica, todo o nada ------------------------------------------------------------------


def test_un_plan_publica_cada_candidata_con_su_gestor_y_su_particion(tmp_path):
    e = _pequeno(tmp_path)

    p = planear(e, e.configuracion, JUEVES)

    assert (p.estado, p.resultado) == ("EXITOSA", "PLAN_PUBLICADO"), p.detalle
    assert (p.cuentas_evaluadas_decision, p.candidatas_campo) == (8, 7)
    assert (p.asignadas, p.revision_territorial, p.sin_gestor, p.sin_capacidad) == (4, 2, 0, 1)
    assert (p.capacidad_total, p.capacidad_utilizada) == (4, 4)
    publicado = asignaciones(p.id)
    assert set(publicado) == {1, 2, 3, 4, 5, 6, 7}
    # El bloque de 1 y 2 primero (el mas grande); despues los de una cuenta, por su microtramo: el
    # de la 4 es el ultimo, y ya no le queda cupo.
    assert {n: a["estado"] for n, a in publicado.items() if a["estado"] != "ASIGNADA"} == {
        4: "SIN_CAPACIDAD",
        6: "REVISION_TERRITORIAL",
        7: "REVISION_TERRITORIAL",
    }
    assert publicadas(p.id) == espejo(p.id)
    assert {c["asignadas"] for c in cargas(p.id).values()} == {2}
    assert "Capacidad declarada, no factibilidad vial" in p.detalle
    assert p.distribucion["unidades"] == {"ZONA": 1, "SUBZONA": 2, "TRAMO": 3, "MICROTRAMO": 4}
    assert p.distribucion["por_base"]["BASE_A"]["candidatas"] == 5
    assert _sin_temporales()


def test_una_decision_sin_candidatas_de_campo_publica_un_plan_vacio(tmp_path):
    e = _pequeno(tmp_path, [CuentaCampo(1, DIGITAL), CuentaCampo(2, DIGITAL)])

    p = planear(e, e.configuracion, JUEVES)

    assert (p.estado, p.candidatas_campo, p.asignadas) == ("EXITOSA", 0, 0)
    assert p.capacidad_total == 4
    assert asignaciones(p.id) == {} and unidades(p.id) == {}
    assert len(cargas(p.id)) == 2


class Falla(Exception):
    pass


@pytest.mark.parametrize("donde", motor.PUNTOS_DE_FALLA)
def test_una_falla_en_cualquier_punto_no_publica_nada(tmp_path, monkeypatch, donde):
    e = _pequeno(tmp_path)
    ejecucion_id = abrir_cv2(e, e.configuracion, JUEVES)

    def fallar_en(nombre: str) -> None:
        if nombre == donde:
            raise Falla(nombre)

    monkeypatch.setattr(motor, "_punto_de_falla", fallar_en)
    motor.ejecutar_campo_v2(ejecucion_id)

    fallido = plan(ejecucion_id)
    assert (fallido.estado, fallido.resultado) == ("FALLIDA", "ERROR_INTERNO")
    assert (fallido.candidatas_campo, fallido.asignadas, fallido.capacidad_total) == (0, 0, 0)
    assert _publicado(ejecucion_id) == 0
    assert _sin_temporales()
    monkeypatch.undo()
    otro = planear(e, e.configuracion, JUEVES)
    assert (otro.estado, otro.firma_inputs) == ("EXITOSA", fallido.firma_inputs)
    assert _cuantos(AsignacionCampoCuenta) == 7


@pytest.mark.parametrize("cambio", [{"version_motor": "campo/v9"}, {"hash_politica": "0" * 64}])
def test_un_motor_o_una_politica_que_el_servicio_no_aplica_falla_sin_publicar(tmp_path, cambio):
    e = _pequeno(tmp_path)
    ejecucion_id = abrir_cv2(e, e.configuracion, JUEVES)
    with sesion() as s:
        s.execute(
            update(EjecucionCampoV2).where(EjecucionCampoV2.id == ejecucion_id).values(**cambio)
        )
        s.commit()

    motor.ejecutar_campo_v2(ejecucion_id)

    assert plan(ejecucion_id).resultado == "VERSION_NO_SOPORTADA"
    assert _publicado(ejecucion_id) == 0


def test_una_territorial_sin_la_asignacion_de_una_candidata_no_se_planea(tmp_path):
    """Defensa en profundidad: una territorial/v2 EXITOSA que no tuviera la asignacion de alguna
    cuenta (no la publica el motor, se arma a mano) da DATOS_INCONSISTENTES, y nada se publica."""
    e = _pequeno(tmp_path)
    with sesion() as s:
        nueva = s.execute(
            text(
                "INSERT INTO ejecucion_territorial_v2 (territorial_run_id, corte_canonico_id, "
                "ejecucion_geografia_id, version_bases_operativas_id, version_motor, "
                "version_politica, hash_politica, firma_inputs, estado, resultado, "
                "cuentas_objetivo, asignadas, revision_geografica, sin_base, fuera_cobertura, "
                "ambiguas, iniciada_en, terminada_en) SELECT :run, corte_canonico_id, "
                "ejecucion_geografia_id, version_bases_operativas_id, version_motor, "
                "version_politica, hash_politica, :firma, estado, resultado, cuentas_objetivo, "
                "asignadas, revision_geografica, sin_base, fuera_cobertura, ambiguas, now(), "
                "now() FROM ejecucion_territorial_v2 WHERE id = :t RETURNING territorial_run_id"
            ),
            {"run": uuid4(), "firma": "e" * 64, "t": e.territorial.id},
        ).scalar_one()
        s.commit()
    with sesion() as s:
        ejecucion = motor.abrir(
            s,
            e.corte.corte_id,
            e.decision.decision_run_id,
            nueva,
            e.configuracion.configuracion_campo_version_id,
            JUEVES,
            max_intentos=5,
        )
        s.commit()
        ejecucion_id = ejecucion.id

    motor.ejecutar_campo_v2(ejecucion_id)

    fallido = plan(ejecucion_id)
    assert (fallido.estado, fallido.resultado) == ("FALLIDA", "DATOS_INCONSISTENTES")
    assert "7 candidatas no tienen asignacion" in fallido.detalle
    assert _publicado(ejecucion_id) == 0


def test_la_cola_que_agota_sus_intentos_lo_deja_fallido_sin_publicar(tmp_path):
    e = _pequeno(tmp_path)
    ejecucion_id = abrir_cv2(e, e.configuracion, JUEVES)

    with sesion() as s:
        assert objetivos.fallar(s, TipoTrabajo.CAMPO_V2, ejecucion_id, "Se agotaron.")
        s.commit()
        assert not objetivos.fallar(s, TipoTrabajo.CAMPO_V2, ejecucion_id, "Otra vez.")

    fallido = plan(ejecucion_id)
    assert (fallido.estado, fallido.resultado, fallido.detalle) == (
        "FALLIDA",
        "INTENTOS_AGOTADOS",
        "Se agotaron.",
    )


@pytest.mark.parametrize("tabla", TABLAS)
def test_lo_que_campo_v2_publico_no_se_reescribe(tmp_path, tabla):
    e = _pequeno(tmp_path)
    p = planear(e, e.configuracion, JUEVES)
    antes = _foto(p.id)

    for sentencia in (
        f"UPDATE {tabla} SET ejecucion_campo_v2_id = ejecucion_campo_v2_id",
        f"DELETE FROM {tabla}",
        f"DELETE FROM {tabla} WHERE false",
    ):
        with sesion() as s, pytest.raises(DBAPIError) as error:
            s.execute(text(sentencia))
        assert error.value.orig.diag.constraint_name == "tr_campo_v2_inmutable"
        assert "campo/v2 no se reescribe" in str(error.value)
    assert _foto(p.id) == antes


def test_un_plan_terminado_no_cambia_ni_se_borra(tmp_path):
    e = _pequeno(tmp_path)
    p = planear(e, e.configuracion, JUEVES)

    for sentencia in (
        "UPDATE ejecucion_campo_v2 SET detalle = 'otro' WHERE id = :e",
        "UPDATE ejecucion_campo_v2 SET estado = 'FALLIDA' WHERE id = :e",
        "DELETE FROM ejecucion_campo_v2 WHERE id = :e",
    ):
        with sesion() as s, pytest.raises(DBAPIError) as error:
            s.execute(text(sentencia), {"e": p.id})
        assert error.value.orig.diag.constraint_name == "tr_campo_v2_terminado"
    assert plan(p.id).estado == "EXITOSA"


# --- varios workers del motor ---------------------------------------------------------------------


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
    """El primer hilo que llega a `punto` se detiene ahi, con el plan bloqueado, hasta que la prueba
    lo suelte. Los demas pasan de largo."""
    llego, soltar = threading.Event(), threading.Event()

    def pausa(nombre: str) -> None:
        if nombre == punto and not llego.is_set():
            llego.set()
            assert soltar.wait(ESPERA)

    monkeypatch.setattr(motor, "_punto_de_falla", pausa)
    return llego, soltar


def test_dos_workers_del_motor_con_el_mismo_plan_publican_una_sola_vez(tmp_path, monkeypatch):
    e = _pequeno(tmp_path)
    ejecucion_id = abrir_cv2(e, e.configuracion, JUEVES)
    llego, soltar = _detener_en(monkeypatch, motor.ANTES_DE_CERRAR)

    primero, salida_1 = _en_otro_hilo(motor.planificar, ejecucion_id)
    assert llego.wait(ESPERA)
    segundo, salida_2 = _en_otro_hilo(motor.planificar, ejecucion_id)
    time.sleep(0.5)
    assert segundo.is_alive()  # espera la fila que el primero tiene bloqueada

    soltar.set()
    for hilo in (primero, segundo):
        hilo.join(ESPERA)

    assert "error" not in salida_1 and "error" not in salida_2
    assert plan(ejecucion_id).estado == "EXITOSA"
    assert _cuantos(AsignacionCampoCuenta) == 7
    assert _cuantos(EjecucionCampoV2) == 1


def test_un_worker_que_pierde_el_lease_despues_de_calcular_no_publica(tmp_path, monkeypatch):
    e = _pequeno(tmp_path)
    _cola_vacia()
    ejecucion_id = abrir_cv2(e, e.configuracion, JUEVES)
    # Sin latidos durante la prueba: el lease vence porque la prueba lo vence.
    config = Config(worker_lease_segundos=600, worker_heartbeat_segundos=300)
    calculo, soltar = _detener_en(monkeypatch, motor.DESPUES_DE_ASIGNACIONES)
    reclamo_a = cola.reclamar("worker-a", config.worker_lease_segundos)
    assert reclamo_a.tipo == TipoTrabajo.CAMPO_V2
    hilo_a, salida_a = _en_otro_hilo(worker.procesar_reclamo, reclamo_a, "worker-a", config)
    assert calculo.wait(ESPERA)

    # A ya calculo el plan y lo inserto en su transaccion, pero su lease vence antes de que
    # confirme: B toma el trabajo y espera el plan, que A todavia tiene bloqueado.
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
    assert plan(ejecucion_id).estado == "EXITOSA"
    assert _cuantos(AsignacionCampoCuenta) == 7 and _cuantos(EjecucionCampoV2) == 1


def test_dos_fechas_a_la_vez_no_se_estorban(tmp_path):
    e = _pequeno(tmp_path)
    jueves = abrir_cv2(e, e.configuracion, JUEVES)
    viernes = abrir_cv2(e, e.configuracion, JUEVES + timedelta(days=1))

    hilos = [_en_otro_hilo(motor.ejecutar_campo_v2, i) for i in (jueves, viernes)]
    for hilo, salida in hilos:
        hilo.join(ESPERA)
        assert "error" not in salida

    assert (plan(jueves).estado, plan(viernes).estado) == ("EXITOSA", "EXITOSA")
    assert _cuantos(AsignacionCampoCuenta) == 14
    # El mismo corte, la misma configuracion y dos dias habiles: el mismo reparto.
    assert gestores_iguales(jueves, viernes)


def gestores_iguales(a: int, b: int) -> bool:
    return {n: x["gestor"] for n, x in asignaciones(a).items()} == {
        n: x["gestor"] for n, x in asignaciones(b).items()
    }


def test_dos_cortes_a_la_vez_no_se_estorban(tmp_path):
    e = _pequeno(tmp_path)
    ingerir(
        tmp_path,
        [CuentaCampo(1, CAMPO, AMOZOC, 2_915_500, 790_500), CuentaCampo(9, CAMPO, PUEBLA, X0, Y0)],
        OTRO_CORTE,
        nombre="otro",
    )
    otra_decision = decidir(OTRO_CORTE)
    otra_territorial = asignar_tv2(OTRO_CORTE, geolocalizar(OTRO_CORTE, e.referencia), e.bases)
    primero = abrir_cv2(e, e.configuracion, JUEVES + timedelta(days=7))
    with sesion() as s:
        segundo = motor.abrir(
            s,
            corte_de(OTRO_CORTE).corte_id,
            otra_decision.decision_run_id,
            otra_territorial.territorial_run_id,
            e.configuracion.configuracion_campo_version_id,
            JUEVES + timedelta(days=7),
            max_intentos=5,
        ).id
        s.commit()

    hilos = [_en_otro_hilo(motor.ejecutar_campo_v2, i) for i in (primero, segundo)]
    for hilo, salida in hilos:
        hilo.join(ESPERA)
        assert "error" not in salida

    assert (plan(primero).candidatas_campo, plan(segundo).candidatas_campo) == (7, 2)
    assert (plan(primero).estado, plan(segundo).estado) == ("EXITOSA", "EXITOSA")
    assert set(asignaciones(segundo)) == {1, 9}
    # La cuenta 1 en un municipio en un corte y en otro en el otro: otra subzona.
    assert asignaciones(primero)[1]["subzona"] != asignaciones(segundo)[1]["subzona"]


def test_el_worker_toma_el_trabajo_campo_v2_y_lo_completa(tmp_path, trabajar):
    e = _pequeno(tmp_path)
    ejecucion_id = abrir_cv2(e, e.configuracion, JUEVES)

    procesados = trabajar()

    assert TipoTrabajo.CAMPO_V2 in {x.tipo for x in procesados}
    assert _trabajo_de(ejecucion_id).estado == "COMPLETADO"
    assert plan(ejecucion_id).estado == "EXITOSA"


def test_en_primer_plano_planea_con_su_propio_trabajo(tmp_path):
    e = _pequeno(tmp_path)

    p = planificar_campo_v2_en_primer_plano(
        e.corte.corte_id,
        e.decision.decision_run_id,
        e.territorial.territorial_run_id,
        e.configuracion.configuracion_campo_version_id,
        JUEVES,
    )

    assert p.estado == "EXITOSA"
    trabajo = _trabajo_de(p.id)
    assert (trabajo.estado, trabajo.intentos) == ("COMPLETADO", 1)


# --- configuraciones versionadas ------------------------------------------------------------------


def test_un_gestor_nuevo_en_otra_version_no_cambia_el_plan_viejo(tmp_path):
    """Version 1: GESTOR_A y GESTOR_B. Version 2: agrega GESTOR_C. El plan con la 1 no cambia; el
    de la 2 puede cambiar; los dos coexisten."""
    e = _pequeno(tmp_path, gestores=[gestor("GESTOR_A"), gestor("GESTOR_B")])
    con_v1 = planear(e, e.configuracion, JUEVES)
    antes = _foto(con_v1.id)

    v2 = publicar_campo(
        e.bases, [gestor("GESTOR_A"), gestor("GESTOR_B"), gestor("GESTOR_C")], nombre="v2"
    )
    con_v2 = planear(e, v2, JUEVES)

    assert v2.configuracion_campo_version_id != e.configuracion.configuracion_campo_version_id
    assert _foto(con_v1.id) == antes
    assert (plan(con_v1.id).estado, con_v2.estado) == ("EXITOSA", "EXITOSA")
    assert len(cargas(con_v2.id)) == 3 and len(cargas(con_v1.id)) == 2


def test_un_cupo_que_cambia_es_otra_version_y_el_plan_viejo_se_queda_como_estaba(tmp_path):
    e = _pequeno(tmp_path, gestores=[gestor("GESTOR_A", capacidad=20)])
    con_20 = planear(e, e.configuracion, JUEVES)
    antes = _foto(con_20.id)

    con_10_version = publicar_campo(e.bases, [gestor("GESTOR_A", capacidad=1)])
    con_10 = planear(e, con_10_version, JUEVES)

    assert con_10_version.hash_configuracion != e.configuracion.hash_configuracion
    assert _foto(con_20.id) == antes
    assert (con_20.asignadas, con_20.sin_capacidad) == (5, 0)
    assert (con_10.asignadas, con_10.sin_capacidad) == (1, 4)
    with sesion() as s, pytest.raises(DBAPIError):
        s.execute(text("UPDATE gestor_campo SET capacidad_cuentas_dia = 10"))


def test_un_calendario_que_cambia_es_otra_version(tmp_path):
    e = _pequeno(tmp_path, gestores=[gestor("GESTOR_A")])
    jueves_normal = planear(e, e.configuracion, JUEVES)
    cerrado = {
        "codigo": "LV_PARTIDO",
        "semana": {
            dia: [{"inicio": "09:00", "fin": "14:00"}, {"inicio": "16:00", "fin": "19:00"}]
            for dia in ("LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES")
        },
        "excepciones": [{"fecha": JUEVES.isoformat(), "tipo": "CERRADO", "motivo": "Festivo"}],
    }

    con_festivo = planear(e, publicar_campo(e.bases, [gestor("GESTOR_A")], [cerrado]), JUEVES)

    assert jueves_normal.asignadas == 5
    assert (con_festivo.asignadas, con_festivo.sin_gestor) == (0, 5)
    assert cargas(con_festivo.id)["GESTOR_A"]["jornada"] == "CERRADO_POR_EXCEPCION"
    assert plan(jueves_normal.id).asignadas == 5


# --- reconstruccion y orden de llegada ------------------------------------------------------------


def test_reconstruir_el_plan_con_las_mismas_entradas_da_exactamente_lo_mismo(tmp_path):
    e = _pequeno(tmp_path)
    primero = planear(e, e.configuracion, JUEVES)
    antes, firma = _foto(primero.id), primero.firma_inputs
    conteos = (
        primero.candidatas_campo,
        primero.asignadas,
        primero.revision_territorial,
        primero.sin_gestor,
        primero.sin_capacidad,
        primero.rebalanceadas,
        primero.capacidad_total,
    )
    distribucion = primero.distribucion

    # Solo lo derivado de ese plan: sus asignaciones, sus unidades, sus cargas, su trabajo y el
    # plan. Ni la decision, ni territorial/v2, ni la configuracion.
    with sesion() as s:
        for tabla, trigger in (
            *((t, "tr_campo_v2_inmutable") for t in TABLAS),
            ("ejecucion_campo_v2", "tr_campo_v2_terminado"),
        ):
            s.execute(text(f"ALTER TABLE {tabla} DISABLE TRIGGER {trigger}"))
        for sentencia in (
            "DELETE FROM asignacion_campo_cuenta WHERE ejecucion_campo_v2_id = :e",
            "DELETE FROM unidad_territorial_campo WHERE ejecucion_campo_v2_id = :e AND nivel = "
            "'MICROTRAMO'",
            "DELETE FROM unidad_territorial_campo WHERE ejecucion_campo_v2_id = :e AND nivel = "
            "'TRAMO'",
            "DELETE FROM unidad_territorial_campo WHERE ejecucion_campo_v2_id = :e AND nivel = "
            "'SUBZONA'",
            "DELETE FROM unidad_territorial_campo WHERE ejecucion_campo_v2_id = :e",
            "DELETE FROM carga_gestor_campo WHERE ejecucion_campo_v2_id = :e",
            "DELETE FROM trabajo_orquestacion WHERE ejecucion_campo_v2_id = :e",
            "DELETE FROM ejecucion_campo_v2 WHERE id = :e",
        ):
            s.execute(text(sentencia), {"e": primero.id})
        for tabla, trigger in (
            *((t, "tr_campo_v2_inmutable") for t in TABLAS),
            ("ejecucion_campo_v2", "tr_campo_v2_terminado"),
        ):
            s.execute(text(f"ALTER TABLE {tabla} ENABLE TRIGGER {trigger}"))
        s.commit()

    reconstruido = planear(e, e.configuracion, JUEVES)

    assert reconstruido.firma_inputs == firma
    assert _foto(reconstruido.id) == antes
    assert reconstruido.distribucion == distribucion
    assert (
        reconstruido.candidatas_campo,
        reconstruido.asignadas,
        reconstruido.revision_territorial,
        reconstruido.sin_gestor,
        reconstruido.sin_capacidad,
        reconstruido.rebalanceadas,
        reconstruido.capacidad_total,
    ) == conteos


def _logico(ejecucion_id: int) -> list:
    """El plan sin nada interno: por numero de cliente, y las unidades por su identificador."""
    asignadas, unidades_, cargas_ = _foto(ejecucion_id)
    return [asignadas, unidades_, cargas_]


def test_las_mismas_cuentas_en_otro_orden_dan_el_mismo_plan(tmp_path):
    """Otro orden en el archivo, en el COPY y en los identificadores internos: el mismo plan, cuenta
    por cuenta, con las mismas unidades."""
    from conftest import _vaciar

    e = _pequeno(tmp_path / "uno")
    primero = _logico(planear(e, e.configuracion, JUEVES).id)
    with sesion() as s:
        internos = dict(s.execute(text("SELECT cliente_unico, id FROM cuenta_canonica")).all())

    _vaciar()
    with sesion() as s:
        # Cuentas que no son del corte, primero: los ids internos del corte se recorren.
        for numero in (900, 901, 902):
            s.execute(
                text(
                    "INSERT INTO cuenta_canonica (cuenta_id, despacho_id, cartera_id, "
                    "cliente_unico, creada_en) VALUES (:c, 'DSP_001', 'CARTERA_PRINCIPAL', :u, "
                    "now())"
                ),
                {"c": uuid4(), "u": f"CU{numero:010d}"},
            )
        s.commit()
    otro = _pequeno(tmp_path / "dos", CUENTAS[::-1], gestores=None)
    segundo = _logico(planear(otro, otro.configuracion, JUEVES).id)
    with sesion() as s:
        otros_internos = dict(
            s.execute(text("SELECT cliente_unico, id FROM cuenta_canonica")).all()
        )

    assert any(otros_internos[c] != i for c, i in internos.items())
    assert segundo == primero


# --- la cuadricula en SQL -------------------------------------------------------------------------


def test_la_celda_del_sql_es_la_del_nucleo_puro():
    valores = [
        2_899_999.9999999995,
        2_899_999.99,
        2_900_000.0,
        2_900_000.01,
        1_234_567.0004999998,
        1_234_567.00049999,
        4_999.9995,
        -0.0005,
        -0.0004,
        999.9994,
        999.9995,
    ]
    with sesion() as s:
        for valor in valores:
            micro, tramo = s.execute(
                text(
                    "SELECT CAST(floor(round(CAST(CAST(:x AS double precision) AS numeric), 3) / "
                    "1000) AS bigint), CAST(floor(round(CAST(CAST(:x AS double precision) AS "
                    "numeric), 3) / 5000) AS bigint)"
                ),
                {"x": repr(valor)},
            ).one()
            assert (micro, tramo) == (politica.celda(valor, 1_000), politica.celda(valor, 5_000)), (
                valor
            )


# --- privacidad -----------------------------------------------------------------------------------


def test_ni_la_bitacora_ni_el_plan_llevan_coordenadas(tmp_path, caplog):
    e = _pequeno(tmp_path)
    ((latitud, longitud),) = coordenadas([(X0, Y0)])
    caplog.set_level("DEBUG")
    caplog.clear()

    p = planear(e, e.configuracion, JUEVES)

    assert caplog.records
    for texto in (caplog.text, p.detalle, str(p.distribucion), str(_foto(p.id))):
        for fragmento in (latitud[:7], longitud[:8], str(X0), str(Y0), "2897", "787,"):
            assert fragmento not in texto, fragmento


def test_un_error_con_una_coordenada_en_su_mensaje_no_la_lleva_a_la_bitacora(
    tmp_path, monkeypatch, caplog
):
    e = _pequeno(tmp_path)
    ((latitud, _),) = coordenadas([(X0, Y0)])
    ejecucion_id = abrir_cv2(e, e.configuracion, JUEVES)

    def fallar(nombre: str) -> None:
        if nombre == motor.DESPUES_DE_PARTICION:
            raise ValueError(f"no se pudo transformar el punto ({latitud}, -98.2)")

    monkeypatch.setattr(motor, "_punto_de_falla", fallar)
    caplog.set_level("DEBUG")
    motor.ejecutar_campo_v2(ejecucion_id)

    fallido = plan(ejecucion_id)
    assert fallido.resultado == "ERROR_INTERNO"
    assert "ValueError" in caplog.text
    assert latitud not in caplog.text and latitud not in fallido.detalle


def test_las_tablas_de_campo_no_tienen_donde_guardar_una_coordenada():
    with sesion() as s:
        columnas = set(
            s.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema = "
                    "current_schema() AND table_name IN ('asignacion_campo_cuenta', "
                    "'unidad_territorial_campo', 'carga_gestor_campo', 'ejecucion_campo_v2')"
                )
            ).scalars()
        )
        geometrias = s.execute(
            text(
                "SELECT count(*) FROM information_schema.columns WHERE table_schema = "
                "current_schema() AND udt_name = 'geometry' AND table_name IN "
                "('asignacion_campo_cuenta', 'unidad_territorial_campo', 'carga_gestor_campo')"
            )
        ).scalar_one()
    for prohibida in (
        "latitud",
        "longitud",
        "lat",
        "lon",
        "x",
        "y",
        "grid_x",
        "grid_y",
        "bbox",
        "centroide",
        "punto",
        "ix",
        "iy",
    ):
        assert prohibida not in columnas, prohibida
    assert geometrias == 0
