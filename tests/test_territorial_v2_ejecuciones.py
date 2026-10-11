"""territorial/v2 en PostgreSQL: abrir con sus entradas declaradas, la idempotencia, lo que no se
puede abrir, la publicacion todo o nada con fallas inyectadas, varios workers, el lease perdido, dos
cortes a la vez, las bases versionadas y la reconstruccion."""

from __future__ import annotations

import threading
import time
from datetime import date, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from geografia_escenarios import (
    AMOZOC,
    MERIDA,
    MUNICIPIOS,
    PUEBLA,
    CuentaGeo,
    abrir_geo,
    corte_de,
    ejecucion,
    geolocalizar,
    importar_referencia,
    ingerir_corte_geo,
)
from sqlalchemy import func, text, update
from sqlalchemy.exc import DBAPIError
from sqlmodel import select
from territorial_v2_escenarios import (
    abrir_tv2,
    asignaciones,
    asignar_tv2,
    base,
    ejecucion_tv2,
    publicadas,
    publicar_bases,
)

from motor_cartera.config import Config
from motor_cartera.db.modelos import (
    AsignacionTerritorialV2,
    CorteCanonico,
    EjecucionTerritorialV2,
    EstadoTrabajo,
    TipoTrabajo,
    TrabajoOrquestacion,
)
from motor_cartera.db.sesion import sesion
from motor_cartera.geografia.sintetica import rectangulo
from motor_cartera.orquestacion import cola, objetivos, worker
from motor_cartera.orquestacion.worker import asignar_territorial_v2_en_primer_plano
from motor_cartera.territorial_v2 import ejecuciones as motor
from motor_cartera.territorial_v2.ejecuciones import (
    BasesNoEncontradas,
    CorteNoEncontrado,
    GeografiaDeOtroCorte,
    GeografiaNoEncontrada,
    GeografiaNoExitosa,
    PoliticaNoSoportada,
    TerritorialV2EnProceso,
    TerritorialV2YaGenerado,
)
from motor_cartera.territorial_v2.politica import PUBLIC_V2, firma_inputs, hash_politica

pytestmark = pytest.mark.usefixtures("bd")

ESPERA = 60
CORTE = date(2026, 9, 30)
OTRO_CORTE = date(2026, 10, 7)
CUENTAS = [
    CuentaGeo(1, PUEBLA, "19.05", "-98.24"),
    CuentaGeo(2, AMOZOC, "19.1", "-97.96"),
    CuentaGeo(3, PUEBLA),
    CuentaGeo(4, MERIDA, "21.0", "-89.6"),
]
BASES = (
    base("BASE_A", 19.05, -98.25, [PUEBLA]),
    base("BASE_B", 19.05, -98.05, [PUEBLA, AMOZOC]),
)


def pequeno(tmp_path, cuentas=CUENTAS, bases=BASES) -> SimpleNamespace:
    """Un corte con su geografia EXITOSA y una version de bases."""
    referencia = importar_referencia(tmp_path)
    ingerir_corte_geo(tmp_path, CORTE, cuentas)
    return SimpleNamespace(
        referencia=referencia,
        geografia=geolocalizar(CORTE, referencia),
        bases=publicar_bases(*bases),
    )


def _cuantos(modelo, *condiciones) -> int:
    with sesion() as s:
        return s.exec(select(func.count()).select_from(modelo).where(*condiciones)).one()


def _trabajo_de(ejecucion_id: int) -> TrabajoOrquestacion:
    with sesion() as s:
        return s.exec(
            select(TrabajoOrquestacion).where(
                TrabajoOrquestacion.ejecucion_territorial_v2_id == ejecucion_id
            )
        ).one()


def _sin_temporales() -> bool:
    with sesion() as s:
        return (
            s.execute(
                text(
                    "SELECT count(*) FROM pg_class WHERE relname LIKE 'tv2\\_%' "
                    "AND relpersistence = 't'"
                )
            ).scalar_one()
            == 0
        )


def _cola_vacia() -> None:
    """La historia y la geografia ya se ejecutaron a mano: sus trabajos se dan por terminados,
    para que la cola solo tenga el de territorial/v2."""
    with sesion() as s:
        s.execute(
            update(TrabajoOrquestacion).values(
                estado=EstadoTrabajo.COMPLETADO, terminado_en=func.now()
            )
        )
        s.commit()


# --- abrir ----------------------------------------------------------------------------------------


def test_abrir_fija_sus_entradas_declaradas_y_encola_su_trabajo(tmp_path):
    p = pequeno(tmp_path)
    corte = corte_de(CORTE)

    ejecucion_id = abrir_tv2(CORTE, p.geografia, p.bases)

    e = ejecucion_tv2(ejecucion_id)
    assert (e.estado, e.resultado, e.version_motor, e.version_politica, e.cuentas_objetivo) == (
        "EN_PROCESO",
        None,
        "territorial/v2",
        "territorial-public-v2",
        4,
    )
    assert (e.corte_canonico_id, e.ejecucion_geografia_id, e.version_bases_operativas_id) == (
        corte.id,
        p.geografia.id,
        p.bases.id,
    )
    assert e.hash_politica == hash_politica(PUBLIC_V2)
    assert e.firma_inputs == firma_inputs(
        corte_id=corte.corte_id,
        geografia_run_id=p.geografia.geografia_run_id,
        firma_geografia=p.geografia.firma_inputs,
        bases_version_id=p.bases.bases_version_id,
        hash_configuracion=p.bases.hash_configuracion,
    )
    trabajo = _trabajo_de(ejecucion_id)
    assert (trabajo.tipo, trabajo.estado, trabajo.intentos) == ("TERRITORIAL_V2", "PENDIENTE", 0)


@pytest.mark.parametrize(
    "que", ["corte", "geografia", "en_proceso", "otro_corte", "bases", "politica"]
)
def test_lo_que_no_se_puede_abrir_no_registra_nada(tmp_path, que):
    p = pequeno(tmp_path)
    corte_id = corte_de(CORTE).corte_id
    geografia_id, bases_id = p.geografia.geografia_run_id, p.bases.bases_version_id
    opciones = {}
    if que == "corte":
        corte_id, error = uuid4(), CorteNoEncontrado
    elif que == "geografia":
        geografia_id, error = uuid4(), GeografiaNoEncontrada
    elif que == "en_proceso":
        # Una geografia que todavia no termina, con otra referencia.
        otra = importar_referencia(tmp_path, MUNICIPIOS[:2], edicion="sin-c")
        geografia_id = ejecucion(abrir_geo(CORTE, otra)).geografia_run_id
        error = GeografiaNoExitosa
    elif que == "otro_corte":
        ingerir_corte_geo(tmp_path, OTRO_CORTE, [CuentaGeo(1, PUEBLA)], nombre="otra")
        corte_id, error = corte_de(OTRO_CORTE).corte_id, GeografiaDeOtroCorte
    elif que == "bases":
        bases_id, error = uuid4(), BasesNoEncontradas
    else:
        opciones["version_politica"], error = "territorial-public-v9", PoliticaNoSoportada

    with sesion() as s, pytest.raises(error) as fallo:
        motor.abrir(s, corte_id, geografia_id, bases_id, max_intentos=5, **opciones)

    assert fallo.value.codigo == error.codigo
    assert _cuantos(EjecucionTerritorialV2) == 0
    assert (
        _cuantos(TrabajoOrquestacion, TrabajoOrquestacion.tipo == TipoTrabajo.TERRITORIAL_V2) == 0
    )


def test_las_mismas_entradas_no_se_abren_dos_veces(tmp_path):
    p = pequeno(tmp_path)
    activa = abrir_tv2(CORTE, p.geografia, p.bases)
    with pytest.raises(TerritorialV2EnProceso):
        abrir_tv2(CORTE, p.geografia, p.bases)

    motor.ejecutar_territorial_v2(activa)

    with pytest.raises(TerritorialV2YaGenerado) as error:
        abrir_tv2(CORTE, p.geografia, p.bases)
    assert error.value.previa.id == activa
    assert _cuantos(EjecucionTerritorialV2) == 1


def test_una_geografia_mas_nueva_no_cambia_una_asignacion_ya_abierta(tmp_path):
    """territorial/v2 nunca toma la geografia mas nueva: usa la que se declaro al abrir."""
    p = pequeno(tmp_path)
    ejecucion_id = abrir_tv2(CORTE, p.geografia, p.bases)
    movidos = (
        rectangulo(PUEBLA, "A movido", -98.40, 18.95, -98.25, 19.15),
        rectangulo(AMOZOC, "B movido", -98.25, 18.95, -97.90, 19.15),
    )
    mas_nueva = geolocalizar(CORTE, importar_referencia(tmp_path, movidos, edicion="nueva"))

    motor.ejecutar_territorial_v2(ejecucion_id)

    e = ejecucion_tv2(ejecucion_id)
    assert e.ejecucion_geografia_id == p.geografia.id != mas_nueva.id
    assert asignaciones(ejecucion_id)[1]["base"] == "BASE_A"


# --- lo que publica, todo o nada ------------------------------------------------------------------


def test_una_asignacion_publica_cada_cuenta_del_corte_con_sus_entradas(tmp_path):
    p = pequeno(tmp_path)

    e = asignar_tv2(CORTE, p.geografia, p.bases)

    assert (e.estado, e.resultado) == ("EXITOSA", "TERRITORIAL_PUBLICADO")
    assert (e.cuentas_objetivo, e.asignadas, e.revision_geografica, e.sin_base) == (4, 2, 1, 1)
    assert (e.fuera_cobertura, e.ambiguas) == (0, 0)
    assert {n: (a["estado"], a["base"]) for n, a in asignaciones(e.id).items()} == {
        1: ("ASIGNADA", "BASE_A"),
        2: ("ASIGNADA", "BASE_B"),
        3: ("REVISION_GEOGRAFICA", None),
        4: ("SIN_BASE_ELEGIBLE", None),
    }
    assert "100.00 m" in e.detalle
    # Cada asignacion cuelga del resultado geografico de su cuenta y de la version de bases.
    with sesion() as s:
        unidas = s.execute(
            text(
                "SELECT count(*) FROM asignacion_territorial_v2 a JOIN resultado_geografia_cuenta "
                "r ON r.ejecucion_geografia_id = a.ejecucion_geografia_id "
                "AND r.cuenta_canonica_id = a.cuenta_canonica_id "
                "WHERE a.version_bases_operativas_id = :bases"
            ),
            {"bases": p.bases.id},
        ).scalar_one()
    assert unidas == 4
    assert _sin_temporales()


class Falla(Exception):
    pass


@pytest.mark.parametrize("donde", motor.PUNTOS_DE_FALLA)
def test_una_falla_en_cualquier_punto_no_publica_nada(tmp_path, monkeypatch, donde):
    p = pequeno(tmp_path)
    ejecucion_id = abrir_tv2(CORTE, p.geografia, p.bases)

    def fallar_en(nombre: str) -> None:
        if nombre == donde:
            raise Falla(nombre)

    monkeypatch.setattr(motor, "_punto_de_falla", fallar_en)
    motor.ejecutar_territorial_v2(ejecucion_id)

    fallida = ejecucion_tv2(ejecucion_id)
    assert (fallida.estado, fallida.resultado) == ("FALLIDA", "ERROR_INTERNO")
    assert (fallida.asignadas, fallida.revision_geografica, fallida.sin_base) == (0, 0, 0)
    assert _cuantos(AsignacionTerritorialV2) == 0
    assert _sin_temporales()
    monkeypatch.undo()
    otra = asignar_tv2(CORTE, p.geografia, p.bases)
    assert (otra.estado, otra.firma_inputs) == ("EXITOSA", fallida.firma_inputs)
    assert _cuantos(AsignacionTerritorialV2) == 4


@pytest.mark.parametrize(
    "cambio", [{"version_motor": "territorial/v9"}, {"hash_politica": "0" * 64}]
)
def test_un_motor_o_una_politica_que_el_servicio_no_aplica_falla_sin_publicar(tmp_path, cambio):
    p = pequeno(tmp_path)
    ejecucion_id = abrir_tv2(CORTE, p.geografia, p.bases)
    with sesion() as s:
        s.execute(
            update(EjecucionTerritorialV2)
            .where(EjecucionTerritorialV2.id == ejecucion_id)
            .values(**cambio)
        )
        s.commit()

    motor.ejecutar_territorial_v2(ejecucion_id)

    assert ejecucion_tv2(ejecucion_id).resultado == "VERSION_NO_SOPORTADA"
    assert _cuantos(AsignacionTerritorialV2) == 0


def test_si_el_corte_no_tiene_las_cuentas_que_dice_no_se_publica(tmp_path):
    p = pequeno(tmp_path)
    with sesion() as s:
        s.execute(update(CorteCanonico).values(cuentas=5))
        s.commit()

    fallida = asignar_tv2(CORTE, p.geografia, p.bases)

    assert (fallida.estado, fallida.resultado) == ("FALLIDA", "DATOS_INCONSISTENTES")
    assert _cuantos(AsignacionTerritorialV2) == 0


def test_la_cola_que_agota_sus_intentos_la_deja_fallida_sin_publicar(tmp_path):
    p = pequeno(tmp_path)
    ejecucion_id = abrir_tv2(CORTE, p.geografia, p.bases)

    with sesion() as s:
        assert objetivos.fallar(s, TipoTrabajo.TERRITORIAL_V2, ejecucion_id, "Se agotaron.")
        s.commit()
        assert not objetivos.fallar(s, TipoTrabajo.TERRITORIAL_V2, ejecucion_id, "Otra vez.")

    fallida = ejecucion_tv2(ejecucion_id)
    assert (fallida.estado, fallida.resultado, fallida.detalle) == (
        "FALLIDA",
        "INTENTOS_AGOTADOS",
        "Se agotaron.",
    )


@pytest.mark.parametrize(
    "sentencia",
    [
        "UPDATE asignacion_territorial_v2 SET estado_asignacion = 'ASIGNADA'",
        "DELETE FROM asignacion_territorial_v2",
    ],
)
def test_lo_que_territorial_v2_publico_no_se_reescribe(tmp_path, sentencia):
    p = pequeno(tmp_path)
    asignar_tv2(CORTE, p.geografia, p.bases)
    with sesion() as s, pytest.raises(DBAPIError) as error:
        s.execute(text(sentencia))
    assert "territorial/v2 no se reescribe" in str(error.value)
    assert _cuantos(AsignacionTerritorialV2) == 4


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


def test_dos_workers_con_la_misma_asignacion_publican_una_sola_vez(tmp_path, monkeypatch):
    p = pequeno(tmp_path)
    ejecucion_id = abrir_tv2(CORTE, p.geografia, p.bases)
    llego, soltar = _detener_en(monkeypatch, motor.ANTES_DE_CERRAR)

    primero, salida_1 = _en_otro_hilo(motor.asignar, ejecucion_id)
    assert llego.wait(ESPERA)
    segundo, salida_2 = _en_otro_hilo(motor.asignar, ejecucion_id)
    time.sleep(0.5)
    assert segundo.is_alive()  # espera la fila que el primero tiene bloqueada

    soltar.set()
    for hilo in (primero, segundo):
        hilo.join(ESPERA)

    assert "error" not in salida_1 and "error" not in salida_2
    assert ejecucion_tv2(ejecucion_id).estado == "EXITOSA"
    assert _cuantos(AsignacionTerritorialV2) == 4


def test_un_worker_que_pierde_el_lease_a_media_asignacion_no_publica(tmp_path, monkeypatch):
    p = pequeno(tmp_path)
    _cola_vacia()
    ejecucion_id = abrir_tv2(CORTE, p.geografia, p.bases)
    # Sin latidos durante la prueba: el lease vence porque la prueba lo vence.
    config = Config(worker_lease_segundos=600, worker_heartbeat_segundos=300)
    calculo, soltar = _detener_en(monkeypatch, motor.DESPUES_DE_LAS_ASIGNACIONES)
    reclamo_a = cola.reclamar("worker-a", config.worker_lease_segundos)
    assert reclamo_a.tipo == TipoTrabajo.TERRITORIAL_V2
    hilo_a, salida_a = _en_otro_hilo(worker.procesar_reclamo, reclamo_a, "worker-a", config)
    assert calculo.wait(ESPERA)

    # A ya inserto sus asignaciones en su transaccion, pero su lease vence antes de que confirme: B
    # toma el trabajo y espera la ejecucion, que A todavia tiene bloqueada.
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
    assert ejecucion_tv2(ejecucion_id).estado == "EXITOSA"
    assert _cuantos(AsignacionTerritorialV2) == 4 and _cuantos(EjecucionTerritorialV2) == 1


def test_dos_cortes_a_la_vez_no_se_estorban(tmp_path):
    p = pequeno(tmp_path)
    ingerir_corte_geo(
        tmp_path,
        OTRO_CORTE,
        [CuentaGeo(1, AMOZOC, "19.1", "-97.96"), CuentaGeo(9, PUEBLA, "19.05", "-98.24")],
        nombre="otra",
    )
    geografia_otra = geolocalizar(OTRO_CORTE, p.referencia)
    primera = abrir_tv2(CORTE, p.geografia, p.bases)
    segunda = abrir_tv2(OTRO_CORTE, geografia_otra, p.bases)

    hilos = [_en_otro_hilo(motor.ejecutar_territorial_v2, e) for e in (primera, segunda)]
    for hilo, salida in hilos:
        hilo.join(ESPERA)
        assert "error" not in salida

    assert (ejecucion_tv2(primera).estado, ejecucion_tv2(primera).asignadas) == ("EXITOSA", 2)
    assert (ejecucion_tv2(segunda).estado, ejecucion_tv2(segunda).asignadas) == ("EXITOSA", 2)
    assert _cuantos(AsignacionTerritorialV2) == 6
    # La cuenta 1, en A en un corte y en B en el otro.
    assert asignaciones(primera)[1]["base"] == "BASE_A"
    assert asignaciones(segunda)[1]["base"] == "BASE_B"


def test_el_worker_toma_el_trabajo_territorial_v2_y_lo_completa(tmp_path, trabajar):
    p = pequeno(tmp_path)
    ejecucion_id = abrir_tv2(CORTE, p.geografia, p.bases)

    procesados = trabajar()

    assert TipoTrabajo.TERRITORIAL_V2 in {x.tipo for x in procesados}
    assert _trabajo_de(ejecucion_id).estado == "COMPLETADO"
    assert ejecucion_tv2(ejecucion_id).estado == "EXITOSA"


def test_en_primer_plano_resuelve_con_su_propio_trabajo(tmp_path):
    p = pequeno(tmp_path)

    e = asignar_territorial_v2_en_primer_plano(
        corte_de(CORTE).corte_id, p.geografia.geografia_run_id, p.bases.bases_version_id
    )

    assert e.estado == "EXITOSA"
    trabajo = _trabajo_de(e.id)
    assert (trabajo.estado, trabajo.intentos) == ("COMPLETADO", 1)


# --- bases versionadas y reconstruccion ----------------------------------------------------------


def test_una_base_nueva_y_mas_cercana_en_otra_version_no_reescribe_la_asignacion_vieja(tmp_path):
    """Version 1: BASE_A cubre A. Version 2: aparece BASE_B, tambien cubre A y esta mas cerca. Con
    la version 1 la cuenta sigue en BASE_A; con la 2, en BASE_B; la de la 1 no cambia."""
    v1 = (base("BASE_A", 19.05, -98.28, [PUEBLA]),)
    p = pequeno(tmp_path, [CuentaGeo(1, PUEBLA, "19.05", "-98.2")], v1)
    con_v1 = asignar_tv2(CORTE, p.geografia, p.bases)
    antes = publicadas(con_v1.id)
    assert antes[1][:2] == ("ASIGNADA", "BASE_A")

    v2 = publicar_bases(*v1, base("BASE_B", 19.05, -98.21, [PUEBLA]), nombre="Version 2")
    con_v2 = asignar_tv2(CORTE, p.geografia, v2)

    assert publicadas(con_v2.id)[1][:2] == ("ASIGNADA", "BASE_B")
    assert publicadas(con_v1.id) == antes
    assert ejecucion_tv2(con_v1.id).estado == "EXITOSA"
    assert (con_v1.version_bases_operativas_id, con_v2.version_bases_operativas_id) == (
        p.bases.id,
        v2.id,
    )


def _foto(ejecucion_id: int) -> list[tuple]:
    with sesion() as s:
        return [
            tuple(f)
            for f in s.execute(
                text(
                    "SELECT c.cliente_unico, a.estado_asignacion, b.codigo, a.distancia_base_m, "
                    "m.cvegeo, a.motivos::text FROM asignacion_territorial_v2 a "
                    "JOIN cuenta_canonica c ON c.id = a.cuenta_canonica_id "
                    "LEFT JOIN base_operativa b ON b.id = a.base_operativa_id "
                    "LEFT JOIN municipio_geografico m ON m.id = a.municipio_geografico_id "
                    "WHERE a.ejecucion_territorial_v2_id = :e ORDER BY c.cliente_unico"
                ),
                {"e": ejecucion_id},
            ).all()
        ]


def test_reconstruir_la_asignacion_con_las_mismas_entradas_da_exactamente_lo_mismo(tmp_path):
    p = pequeno(tmp_path)
    primera = asignar_tv2(CORTE, p.geografia, p.bases)
    antes, firma, distribucion = _foto(primera.id), primera.firma_inputs, primera.distribucion

    # Solo lo derivado de esa ejecucion: sus asignaciones, su trabajo y la ejecucion. Ni la
    # geografia, ni las bases, ni el corte.
    tabla = "asignacion_territorial_v2"
    with sesion() as s:
        s.execute(text(f"ALTER TABLE {tabla} DISABLE TRIGGER tr_territorial_v2_inmutable"))
        for sentencia in (
            f"DELETE FROM {tabla} WHERE ejecucion_territorial_v2_id = :e",
            "DELETE FROM trabajo_orquestacion WHERE ejecucion_territorial_v2_id = :e",
            "DELETE FROM ejecucion_territorial_v2 WHERE id = :e",
        ):
            s.execute(text(sentencia), {"e": primera.id})
        s.execute(text(f"ALTER TABLE {tabla} ENABLE TRIGGER tr_territorial_v2_inmutable"))
        s.commit()

    reconstruida = asignar_tv2(CORTE, p.geografia, p.bases)

    assert reconstruida.firma_inputs == firma
    assert _foto(reconstruida.id) == antes
    assert reconstruida.distribucion == distribucion


def test_ni_la_bitacora_ni_el_detalle_llevan_coordenadas(tmp_path, caplog):
    p = pequeno(tmp_path)
    caplog.set_level("DEBUG")
    caplog.clear()

    e = asignar_tv2(CORTE, p.geografia, p.bases)

    assert caplog.records
    for texto in (caplog.text, e.detalle, str(e.distribucion)):
        for coordenada in ("19.05", "98.24", "97.96", "98.25"):
            assert coordenada not in texto
