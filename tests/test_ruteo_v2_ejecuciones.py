"""ruteo/v2 contra PostgreSQL, con proveedores de ruteo de pruebas: abrir con las entradas
declaradas, publicar todo o nada, los resultados negativos que se publican, el backend equivocado,
el proveedor caido, la privacidad, los puntos de falla, el lease perdido, dos workers y la
reconstruccion identica.

El proveedor de pruebas responde con lo que la prueba le dice (una matriz escrita a mano, o un
modelo geometrico declarado): no es una red vial. Lo que se prueba aqui es el motor; la red real se
prueba con OSRM en test_ruteo_v2_osrm.py.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from ruteo_v2_escenarios import (
    CuentaRuta,
    ProveedorDePruebas,
    abrir_rv2,
    base,
    borrar,
    coordenada,
    ejecucion,
    escenario,
    gestor,
    paradas,
    red_de_pruebas,
    resultado_logico,
    rutas,
    rutear,
    secuencia,
)
from sqlalchemy import func, text, update
from sqlalchemy.exc import DBAPIError

from motor_cartera.config import Config
from motor_cartera.db.modelos import (
    EjecucionCampoV2,
    EjecucionRuteoV2,
    EstadoTrabajo,
    TipoTrabajo,
    TrabajoOrquestacion,
    VersionRedVial,
)
from motor_cartera.db.sesion import sesion
from motor_cartera.historia.identidad import cuenta_id
from motor_cartera.orquestacion import cola, objetivos, worker
from motor_cartera.ruteo_v2 import ejecuciones as motor
from motor_cartera.ruteo_v2 import politica
from motor_cartera.ruteo_v2.ejecuciones import SaludRuteo
from motor_cartera.ruteo_v2.proveedor import ClienteOsrm, ConfiguracionOsrm, FallaProveedorRuteo

pytestmark = pytest.mark.usefixtures("bd")

ESPERA = 60
TABLAS = ("ruta_gestor_v2", "parada_ruta_v2")

# Una base en un cruce y cuatro cuentas alrededor, a menos de 3 km: todo dentro del municipio A.
BASE_XY = (1_000, 1_000)
PUNTOS = {1: (1_000, 1_500), 2: (1_600, 1_000), 3: (2_000, 2_000), 4: (500, 2_500)}
CUENTAS = [CuentaRuta(n, x, y) for n, (x, y) in PUNTOS.items()]
BASES = [base("BASE_A", *BASE_XY)]
UN_GESTOR = [gestor("GESTOR_A")]


def utc(hora: int, minuto: int = 0) -> datetime:
    """Una hora local del jueves 2026-10-01 en la Ciudad de Mexico (UTC-6), en UTC."""
    return datetime(2026, 10, 1, 6, tzinfo=UTC) + timedelta(hours=hora, minutes=minuto)


def matriz_a_mano(filas: dict[str, dict[str, int | None]], por_segundo: int = 8):
    """Un tramo que sale de una matriz escrita a mano, por etiqueta de punto: la base y las cuentas
    por su numero. Distancia: 8 m por segundo."""
    etiqueta = {coordenada(*xy): str(n) for n, xy in PUNTOS.items()}
    etiqueta[coordenada(*BASE_XY)] = "BASE"

    def tramo(a, b):
        d = filas[etiqueta[a]][etiqueta[b]]
        if d is None:
            raise AssertionError("un tramo desconectado se declara con `desconectados`")
        return Decimal(d), Decimal(d * por_segundo)

    return tramo


@pytest.fixture
def plan_simple(tmp_path):
    return escenario(tmp_path, CUENTAS, BASES, UN_GESTOR).campo


@pytest.fixture
def red():
    return red_de_pruebas()


def _cuantos(tabla: str) -> int:
    with sesion() as s:
        return s.execute(text(f"SELECT count(*) FROM {tabla}")).scalar_one()


def _sin_temporales() -> bool:
    with sesion() as s:
        return (
            s.execute(
                text(
                    "SELECT count(*) FROM pg_class WHERE relname LIKE 'rv2\\_%' "
                    "AND relpersistence = 't'"
                )
            ).scalar_one()
            == 0
        )


def _cola_vacia() -> None:
    """Lo que ya se ejecuto a mano (la historia, la decision, la geografia, territorial/v2 y
    campo/v2) se da por terminado en la cola, para que solo quede el trabajo RUTEO_V2."""
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


def _trabajo_de(ejecucion_id: int) -> TrabajoOrquestacion:
    with sesion() as s:
        return s.execute(
            text("SELECT * FROM trabajo_orquestacion WHERE ejecucion_ruteo_v2_id = :e"),
            {"e": ejecucion_id},
        ).one()


# --- el golden del proveedor, publicado -----------------------------------------------------------


GOLDEN = {
    "BASE": {"BASE": 0, "1": 600, "2": 900, "3": 1200},
    "1": {"BASE": 600, "1": 0, "2": 300, "3": 900},
    "2": {"BASE": 900, "1": 300, "2": 0, "3": 600},
    "3": {"BASE": 1200, "1": 900, "2": 600, "3": 0},
}


def test_golden_del_proveedor_base_a_b_c_y_d_desconectada_publicado(plan_simple, red):
    # La matriz escrita a mano del golden puro: A (1) a 10 min, B (2) a 5 de A, C (3) a 10 de B, el
    # regreso de C en 20; D (4) tiene snap pero esta desconectada. Lo publicado, en UTC: sale a las
    # 09:00 locales (15:00 UTC) y regresa a las 10:30 (16:30 UTC).
    proveedor = ProveedorDePruebas(
        red.huella,
        tramo=matriz_a_mano(GOLDEN),
        desconectados={coordenada(*PUNTOS[4])},
        snaps={coordenada(*PUNTOS[4]): Decimal("12.345")},
    )

    e = rutear(plan_simple, red, proveedor)

    assert e.estado == "EXITOSA", e.detalle
    ruta = rutas(e.id)["GESTOR_A"]
    assert ruta["estado_ruta"] == "PARCIAL"
    assert (ruta["programadas"], ruta["no_enrutables"], ruta["paradas_campo"]) == (3, 1, 4)
    assert (ruta["salida_base_at"], ruta["salida_regreso_at"], ruta["regreso_base_at"]) == (
        utc(9),
        utc(10, 10),
        utc(10, 30),
    )
    assert (ruta["jornada_inicio_at"], ruta["jornada_fin_at"]) == (utc(9), utc(19))
    assert (ruta["distancia_total_m"], ruta["duracion_traslado_s"]) == (21_600, 2_700)
    assert (ruta["duracion_servicio_s"], ruta["duracion_espera_s"], ruta["duracion_total_s"]) == (
        2_700,
        0,
        5_400,
    )
    assert (ruta["distancia_regreso_m"], ruta["duracion_regreso_s"]) == (9_600, 1_200)
    assert ruta["base_snap_distancia_m"] == Decimal("0.00")
    publicadas = paradas(e.id)
    esperado = {
        1: (1, utc(9), utc(9, 10), utc(9, 10), utc(9, 25), 4_800, 600),
        2: (2, utc(9, 25), utc(9, 30), utc(9, 30), utc(9, 45), 2_400, 300),
        3: (3, utc(9, 45), utc(9, 55), utc(9, 55), utc(10, 10), 4_800, 600),
    }
    for n, valores in esperado.items():
        p = publicadas[n]
        assert (
            p["orden_visita"],
            p["salida_anterior_at"],
            p["llegada_estimada"],
            p["inicio_servicio"],
            p["fin_servicio"],
            p["distancia_desde_anterior_m"],
            p["duracion_desde_anterior_s"],
        ) == valores, n
        assert (p["estado"], p["motivo"], p["espera_s"]) == ("PROGRAMADA", None, 0)
    d = publicadas[4]
    assert (d["estado"], d["motivo"], d["orden_visita"], d["llegada_estimada"]) == (
        "NO_ENRUTABLE",
        "RED_DESCONECTADA",
        None,
        None,
    )
    assert d["snap_distancia_m"] == Decimal("12.35")
    assert (e.programadas, e.no_enrutables, e.rutas_parciales) == (3, 1, 1)
    assert e.distribucion["motivos"] == {"RED_DESCONECTADA": 1}


# --- abrir ----------------------------------------------------------------------------------------


def test_abrir_fija_sus_entradas_y_encola_su_trabajo(plan_simple, red):
    ejecucion_id = abrir_rv2(plan_simple, red)

    e = ejecucion(ejecucion_id)
    assert (e.estado, e.version_motor, e.version_politica) == (
        "EN_PROCESO",
        "ruteo/v2",
        "ruteo-public-v2",
    )
    assert e.hash_politica == politica.hash_politica()
    assert e.fecha_operacion == plan_simple.fecha_operacion
    assert e.firma_inputs == politica.firma_inputs(
        campo_run_id=plan_simple.campo_run_id,
        firma_campo=plan_simple.firma_inputs,
        red_vial_version_id=red.red_vial_version_id,
        huella_red=red.huella,
        hash_manifest=red.hash_manifest,
        version_motor_ruteo=red.version_motor_ruteo,
        hash_perfil=red.hash_perfil,
    )
    trabajo = _trabajo_de(ejecucion_id)
    assert (trabajo.tipo, trabajo.estado) == ("RUTEO_V2", "PENDIENTE")


def _plan_fallido(plan: EjecucionCampoV2) -> EjecucionCampoV2:
    """Un plan de campo/v2 FALLIDA de las mismas entradas: otro plan, con otra fecha."""
    from motor_cartera.campo_v2 import ejecuciones as campo

    with sesion() as s:
        otro = campo.abrir(
            s,
            s.execute(
                text("SELECT corte_id FROM corte_canonico WHERE id = :c"),
                {"c": plan.corte_canonico_id},
            ).scalar_one(),
            s.execute(
                text("SELECT decision_run_id FROM ejecucion_decision_v2 WHERE id = :d"),
                {"d": plan.ejecucion_decision_v2_id},
            ).scalar_one(),
            s.execute(
                text("SELECT territorial_run_id FROM ejecucion_territorial_v2 WHERE id = :t"),
                {"t": plan.ejecucion_territorial_v2_id},
            ).scalar_one(),
            s.execute(
                text(
                    "SELECT configuracion_campo_version_id FROM version_configuracion_campo "
                    "WHERE id = :c"
                ),
                {"c": plan.version_configuracion_campo_id},
            ).scalar_one(),
            plan.fecha_operacion + timedelta(days=1),
            max_intentos=5,
        )
        s.commit()
        assert objetivos.fallar(s, TipoTrabajo.CAMPO_V2, otro.id, "Fallo a proposito.")
        s.commit()
        return s.get_one(EjecucionCampoV2, otro.id)


def test_lo_que_no_se_puede_abrir_no_registra_nada(plan_simple, red):
    fallido = _plan_fallido(plan_simple)
    with sesion() as s:
        en_curso = VersionRedVial(
            huella="e" * 64,
            proveedor_datos="OpenStreetMap",
            licencia="ODbL-1.0",
            region="Una red que todavia se prepara",
            archivo_fuente="red.osm.pbf",
            sha256_fuente="e" * 64,
            motor_ruteo="OSRM",
            version_motor_ruteo="v26.9.0",
            distribucion_motor="docker:imagen",
            algoritmo_motor="MLD",
            perfil="car",
            hash_perfil="e" * 64,
            identidad={},
            metadatos={},
        )
        s.add(en_curso)
        s.commit()
        s.refresh(en_curso)

    for campo_run_id, red_id, opciones, error, codigo in (
        (uuid4(), red.red_vial_version_id, {}, motor.CampoNoEncontrado, "CAMPO_V2_NO_ENCONTRADO"),
        (fallido.campo_run_id, red.red_vial_version_id, {}, motor.CampoNoExitoso, None),
        (plan_simple.campo_run_id, uuid4(), {}, motor.RedNoEncontrada, "RED_VIAL_NO_ENCONTRADA"),
        (plan_simple.campo_run_id, en_curso.red_vial_version_id, {}, motor.RedNoPublicada, None),
        (
            plan_simple.campo_run_id,
            red.red_vial_version_id,
            {"version_politica": "ruteo-public-v9"},
            motor.PoliticaNoSoportada,
            "POLITICA_NO_SOPORTADA",
        ),
    ):
        with sesion() as s, pytest.raises(error) as capturado:
            motor.abrir(s, campo_run_id, red_id, max_intentos=5, **opciones)
        if codigo is not None:
            assert capturado.value.codigo == codigo
    assert _cuantos("ejecucion_ruteo_v2") == 0
    assert _cuantos("trabajo_orquestacion WHERE tipo = 'RUTEO_V2'") == 0


def test_las_mismas_entradas_son_la_misma_ejecucion(plan_simple, red):
    primera = abrir_rv2(plan_simple, red)
    with pytest.raises(motor.RuteoV2EnProceso):
        abrir_rv2(plan_simple, red)
    motor.rutear(primera, proveedor=ProveedorDePruebas(red.huella))
    assert ejecucion(primera).estado == "EXITOSA"
    with pytest.raises(motor.RuteoV2YaGenerado):
        abrir_rv2(plan_simple, red)
    # Otra red es otra ejecucion, con otra firma, y las dos coexisten.
    otra_red = red_de_pruebas("Otra red de pruebas")
    otra = rutear(plan_simple, otra_red, ProveedorDePruebas(otra_red.huella))
    assert otra.estado == "EXITOSA"
    assert otra.firma_inputs != ejecucion(primera).firma_inputs


# --- los resultados negativos se publican ---------------------------------------------------------


def test_snap_excesivo_sin_snap_y_red_desconectada_se_publican_como_no_enrutables(plan_simple, red):
    proveedor = ProveedorDePruebas(
        red.huella,
        snaps={
            coordenada(*PUNTOS[1]): Decimal("500.01"),
            coordenada(*PUNTOS[2]): None,
            coordenada(*PUNTOS[4]): Decimal("500.004"),
        },
        desconectados={coordenada(*PUNTOS[3])},
    )

    e = rutear(plan_simple, red, proveedor)

    # Una ejecucion EXITOSA con resultados negativos: el motor calculo que no puede programarlas.
    assert e.estado == "EXITOSA"
    publicadas = paradas(e.id)
    assert (publicadas[1]["estado"], publicadas[1]["motivo"]) == ("NO_ENRUTABLE", "SNAP_EXCESIVO")
    assert publicadas[1]["snap_distancia_m"] == Decimal("500.01")
    assert (publicadas[2]["motivo"], publicadas[2]["snap_distancia_m"]) == ("SIN_SNAP", None)
    assert publicadas[3]["motivo"] == "RED_DESCONECTADA"
    # 500.004 m son 500.00 al centimetro: todavia se permite.
    assert (publicadas[4]["estado"], publicadas[4]["snap_distancia_m"]) == (
        "PROGRAMADA",
        Decimal("500.00"),
    )
    assert (e.programadas, e.no_enrutables, e.rutas_parciales) == (1, 3, 1)
    assert e.distribucion["snap"]["excesivos"] == 1
    assert e.distribucion["snap"]["sin_snap"] == 1


def test_una_base_sin_snap_valido_no_tiene_ruta_y_no_envia_ninguna_cuenta(plan_simple, red):
    proveedor = ProveedorDePruebas(red.huella, snaps={coordenada(*BASE_XY): Decimal("500.01")})

    e = rutear(plan_simple, red, proveedor)

    assert e.estado == "EXITOSA"
    ruta = rutas(e.id)["GESTOR_A"]
    assert (ruta["estado_ruta"], ruta["motivo_base"]) == ("BASE_NO_ENRUTABLE", "SNAP_EXCESIVO")
    assert ruta["base_snap_distancia_m"] == Decimal("500.01")
    assert (ruta["salida_base_at"], ruta["hash_matriz"]) == (None, None)
    assert {(p["estado"], p["motivo"]) for p in paradas(e.id).values()} == {
        ("NO_ENRUTABLE_BASE", "BASE_NO_ENRUTABLE")
    }
    assert (e.bases_no_enrutables, e.no_enrutables_base) == (1, 4)
    # Solo se le pregunto por la base: ninguna cuenta de una base sin ruta salio hacia la red.
    assert proveedor.recibidas == [coordenada(*BASE_XY)]


def test_la_prioridad_de_campo_v2_se_respeta(tmp_path, red):
    # La mas cercana a la base es la BAJA, y va al final; nada se vuelve a decidir.
    plan = escenario(
        tmp_path,
        CUENTAS,
        BASES,
        UN_GESTOR,
        prioridades={1: "BAJA", 2: "MUY_ALTA", 3: "ALTA", 4: "MEDIA"},
    ).campo

    e = rutear(plan, red, ProveedorDePruebas(red.huella))

    assert secuencia(e.id, "GESTOR_A") == [2, 3, 4, 1]
    assert {n: p["prioridad"] for n, p in paradas(e.id).items()} == {
        1: "BAJA",
        2: "MUY_ALTA",
        3: "ALTA",
        4: "MEDIA",
    }
    assert e.distribucion["por_prioridad"]["BAJA"] == {
        "paradas": 1,
        "programadas": 1,
        "sin_tiempo": 0,
    }


PARTIDO_CORTO = {
    "codigo": "PARTIDO_CORTO",
    "semana": {
        "JUEVES": [{"inicio": "09:00", "fin": "09:40"}, {"inicio": "10:00", "fin": "14:00"}]
    },
}


def test_una_jornada_partida_espera_la_segunda_ventana_sin_conducir_en_la_pausa(tmp_path, red):
    # De 09:00 a 09:40 y de 10:00 a 14:00. La 1 (MUY_ALTA) esta a 10 min: termina a las 09:25. La 2
    # (ALTA) esta a 20 min de la 1: llegaria a las 09:45, asi que el gestor espera en la 1 y sale a
    # las 10:00. Llega a las 10:20, termina a las 10:35 y regresa a las 10:50.
    plan = escenario(
        tmp_path,
        CUENTAS[:2],
        BASES,
        [gestor("GESTOR_A", calendario="PARTIDO_CORTO")],
        calendarios=[PARTIDO_CORTO],
        prioridades={1: "MUY_ALTA", 2: "ALTA"},
    ).campo
    tramo = matriz_a_mano(
        {
            "BASE": {"BASE": 0, "1": 600, "2": 900},
            "1": {"BASE": 600, "1": 0, "2": 1200},
            "2": {"BASE": 900, "1": 1200, "2": 0},
        }
    )

    e = rutear(plan, red, ProveedorDePruebas(red.huella, tramo=tramo))

    publicadas = paradas(e.id)
    assert (publicadas[1]["llegada_estimada"], publicadas[1]["fin_servicio"]) == (
        utc(9, 10),
        utc(9, 25),
    )
    segunda = publicadas[2]
    assert (segunda["salida_anterior_at"], segunda["llegada_estimada"]) == (utc(10), utc(10, 20))
    assert (segunda["fin_servicio"], segunda["espera_s"]) == (utc(10, 35), 35 * 60)
    ruta = rutas(e.id)["GESTOR_A"]
    assert (ruta["regreso_base_at"], ruta["duracion_espera_s"]) == (utc(10, 50), 35 * 60)
    assert ruta["estado_ruta"] == "COMPLETA"
    # Ningun traslado ni servicio entre las 09:40 y las 10:00.
    for p in publicadas.values():
        for inicio, fin in (
            (p["salida_anterior_at"], p["llegada_estimada"]),
            (p["inicio_servicio"], p["fin_servicio"]),
        ):
            assert fin <= utc(9, 40) or inicio >= utc(10)


def test_un_plan_sin_asignadas_publica_una_ejecucion_vacia_sin_preguntarle_a_la_red(tmp_path, red):
    plan = escenario(tmp_path, CUENTAS, BASES, [gestor("GESTOR_A", activo=False)]).campo
    assert plan.asignadas == 0
    proveedor = ProveedorDePruebas(red.huella)

    e = rutear(plan, red, proveedor)

    assert (e.estado, e.gestores_objetivo, e.paradas_objetivo) == ("EXITOSA", 0, 0)
    assert (e.programadas, e.distancia_total_m) == (0, 0)
    assert rutas(e.id) == {} and paradas(e.id) == {}
    assert proveedor.recibidas == []


# --- el backend, el proveedor caido y la privacidad -----------------------------------------------


def test_un_backend_con_otra_red_falla_cerrado_sin_enviar_ninguna_cuenta(plan_simple, red):
    proveedor = ProveedorDePruebas("f" * 64)

    e = rutear(plan_simple, red, proveedor)

    assert (e.estado, e.resultado) == ("FALLIDA", "RED_VIAL_BACKEND_INCOMPATIBLE")
    assert "otra red vial" in e.detalle
    assert _cuantos("ruta_gestor_v2") == _cuantos("parada_ruta_v2") == 0
    # La red se verifica antes de enviar un punto: ni el de la base salio.
    assert proveedor.recibidas == []
    assert _sin_temporales()


def test_un_proveedor_caido_es_un_fallo_tecnico_y_no_vuelve_nada_no_enrutable(
    plan_simple, red, monkeypatch
):
    caido = ProveedorDePruebas(
        red.huella, falla=FallaProveedorRuteo("nearest", "ConnectionRefusedError", 3)
    )
    ejecucion_id = abrir_rv2(plan_simple, red)

    with pytest.raises(FallaProveedorRuteo):
        motor.rutear(ejecucion_id, proveedor=caido)

    assert ejecucion(ejecucion_id).estado == "EN_PROCESO"
    assert _cuantos("parada_ruta_v2") == 0

    # Por la cola: con un solo intento, la cola la deja FALLIDA, sin publicar ninguna parada.
    _cola_vacia()
    with sesion() as s:
        s.execute(
            update(TrabajoOrquestacion)
            .where(TrabajoOrquestacion.ejecucion_ruteo_v2_id == ejecucion_id)
            .values(estado=EstadoTrabajo.PENDIENTE, max_intentos=1, terminado_en=None, intentos=0)
        )
        s.commit()
    monkeypatch.setattr(motor, "proveedor_configurado", lambda _config: caido)
    procesado = worker.procesar_un_trabajo("worker-a", Config(), SaludRuteo(caido, 60))

    assert (procesado.tipo, procesado.estado) == (TipoTrabajo.RUTEO_V2, EstadoTrabajo.FALLIDO)
    fallida = ejecucion(ejecucion_id)
    assert (fallida.estado, fallida.resultado) == ("FALLIDA", "INTENTOS_AGOTADOS")
    assert _cuantos("parada_ruta_v2") == 0
    assert _trabajo_de(ejecucion_id).ultimo_error == (
        "Error de worker (FallaProveedorRuteo); ver la bitacora."
    )


COORDENADAS_EN_LA_URL = "-98.1234,19.4326;-98.2001,19.0512"


def test_un_error_del_transporte_con_coordenadas_no_las_deja_en_ningun_lado(
    plan_simple, red, monkeypatch, caplog, cliente
):
    # Una excepcion del transporte que repite la URL con coordenadas, como la de algunos clientes
    # HTTP: no llega a la bitacora, ni al detalle, ni al trabajo, ni a la API.
    osrm = ClienteOsrm(ConfiguracionOsrm("http://osrm:5000", reintentos=1, backoff_segundos=0))

    def transporte(*_argumentos):
        raise OSError(
            f"fallo la peticion a http://osrm:5000/table/v1/driving/{COORDENADAS_EN_LA_URL}"
            "?annotations=duration"
        )

    monkeypatch.setattr(osrm, "_enviar", transporte)
    monkeypatch.setattr(motor, "proveedor_configurado", lambda _config: osrm)
    ejecucion_id = abrir_rv2(plan_simple, red)
    _cola_vacia()
    with sesion() as s:
        s.execute(
            update(TrabajoOrquestacion)
            .where(TrabajoOrquestacion.ejecucion_ruteo_v2_id == ejecucion_id)
            .values(estado=EstadoTrabajo.PENDIENTE, max_intentos=1, terminado_en=None)
        )
        s.commit()
    caplog.set_level("DEBUG")
    caplog.clear()

    worker.procesar_un_trabajo("worker-a", Config(), SaludRuteo(ProveedorDePruebas("x"), 60))

    fallida = ejecucion(ejecucion_id)
    assert fallida.estado == "FALLIDA"
    trabajo = _trabajo_de(ejecucion_id)
    respuestas = [
        cliente.get(f"/ruteo-v2/ejecuciones/{fallida.ruteo_run_id}").text,
        cliente.get(f"/trabajos/{trabajo.trabajo_id}").text,
    ]
    assert "FallaProveedorRuteo" in caplog.text
    for texto in (caplog.text, fallida.detalle, trabajo.ultimo_error, *respuestas):
        for fragmento in ("-98.1234", "19.4326", "-98.2001", "/table/v1/driving/-"):
            assert fragmento not in texto, fragmento


# --- todo o nada ----------------------------------------------------------------------------------


class Falla(Exception):
    pass


@pytest.mark.parametrize("donde", motor.PUNTOS_DE_FALLA)
def test_una_falla_en_cualquier_punto_no_publica_nada(plan_simple, red, monkeypatch, donde):
    ejecucion_id = abrir_rv2(plan_simple, red)

    def fallar_en(nombre: str) -> None:
        if nombre == donde:
            raise Falla(nombre)

    monkeypatch.setattr(motor, "_punto_de_falla", fallar_en)
    motor.rutear(ejecucion_id, proveedor=ProveedorDePruebas(red.huella))

    fallida = ejecucion(ejecucion_id)
    assert (fallida.estado, fallida.resultado) == ("FALLIDA", "ERROR_INTERNO")
    assert (fallida.gestores_objetivo, fallida.paradas_objetivo, fallida.programadas) == (0, 0, 0)
    assert _cuantos("ruta_gestor_v2") == _cuantos("parada_ruta_v2") == 0
    assert _sin_temporales()
    monkeypatch.undo()
    otra = rutear(plan_simple, red, ProveedorDePruebas(red.huella))
    assert (otra.estado, otra.firma_inputs) == ("EXITOSA", fallida.firma_inputs)
    assert _cuantos("parada_ruta_v2") == 4


@pytest.mark.parametrize("cambio", [{"version_motor": "ruteo/v9"}, {"hash_politica": "0" * 64}])
def test_un_motor_o_una_politica_que_el_servicio_no_aplica_falla_sin_publicar(
    plan_simple, red, cambio
):
    ejecucion_id = abrir_rv2(plan_simple, red)
    with sesion() as s:
        s.execute(
            update(EjecucionRuteoV2).where(EjecucionRuteoV2.id == ejecucion_id).values(**cambio)
        )
        s.commit()

    motor.rutear(ejecucion_id, proveedor=ProveedorDePruebas(red.huella))

    assert ejecucion(ejecucion_id).resultado == "VERSION_NO_SOPORTADA"
    assert _cuantos("parada_ruta_v2") == 0


def test_la_cola_que_agota_sus_intentos_la_deja_fallida_sin_publicar(plan_simple, red):
    ejecucion_id = abrir_rv2(plan_simple, red)

    with sesion() as s:
        assert objetivos.fallar(s, TipoTrabajo.RUTEO_V2, ejecucion_id, "Se agotaron.")
        s.commit()
        assert not objetivos.fallar(s, TipoTrabajo.RUTEO_V2, ejecucion_id, "Otra vez.")

    fallida = ejecucion(ejecucion_id)
    assert (fallida.estado, fallida.resultado, fallida.detalle) == (
        "FALLIDA",
        "INTENTOS_AGOTADOS",
        "Se agotaron.",
    )


# --- varios workers del motor ---------------------------------------------------------------------


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


def test_dos_workers_del_motor_con_la_misma_firma_publican_una_sola_vez(
    plan_simple, red, monkeypatch
):
    ejecucion_id = abrir_rv2(plan_simple, red)
    llego, soltar = _detener_en(monkeypatch, motor.ANTES_DE_CERRAR)
    proveedor = ProveedorDePruebas(red.huella)

    primero, salida_1 = _en_otro_hilo(motor.rutear, ejecucion_id, proveedor=proveedor)
    assert llego.wait(ESPERA)
    segundo, salida_2 = _en_otro_hilo(motor.rutear, ejecucion_id, proveedor=proveedor)
    time.sleep(0.5)
    assert segundo.is_alive()  # espera la fila que el primero tiene bloqueada

    soltar.set()
    for hilo in (primero, segundo):
        hilo.join(ESPERA)

    assert "error" not in salida_1 and "error" not in salida_2
    assert ejecucion(ejecucion_id).estado == "EXITOSA"
    assert _cuantos("parada_ruta_v2") == 4 and _cuantos("ejecucion_ruteo_v2") == 1


def test_un_worker_que_pierde_el_lease_despues_de_calcular_no_publica(
    plan_simple, red, monkeypatch
):
    _cola_vacia()
    ejecucion_id = abrir_rv2(plan_simple, red)
    proveedor = ProveedorDePruebas(red.huella)
    monkeypatch.setattr(motor, "proveedor_configurado", lambda _config: proveedor)
    salud = SaludRuteo(proveedor, 60)
    # Sin latidos durante la prueba: el lease vence porque la prueba lo vence.
    config = Config(worker_lease_segundos=600, worker_heartbeat_segundos=300)
    calculo, soltar = _detener_en(monkeypatch, motor.DESPUES_DE_PARADAS)
    reclamo_a = cola.reclamar("worker-a", config.worker_lease_segundos)
    assert reclamo_a.tipo == TipoTrabajo.RUTEO_V2
    hilo_a, salida_a = _en_otro_hilo(worker.procesar_reclamo, reclamo_a, "worker-a", config)
    assert calculo.wait(ESPERA)

    # A ya pidio sus matrices, planeo sus rutas y las inserto en su transaccion, pero su lease vence
    # antes de que confirme: B toma el trabajo y espera la ejecucion, que A tiene bloqueada.
    with sesion() as s:
        s.execute(
            update(TrabajoOrquestacion)
            .where(TrabajoOrquestacion.id == reclamo_a.id)
            .values(lease_hasta=func.now() - timedelta(seconds=1))
        )
        s.commit()
    hilo_b, salida_b = _en_otro_hilo(worker.procesar_un_trabajo, "worker-b", config, salud)
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
    assert _cuantos("parada_ruta_v2") == 4 and _cuantos("ejecucion_ruteo_v2") == 1


def test_el_worker_no_toma_un_ruteo_si_su_proveedor_no_responde(plan_simple, red, monkeypatch):
    _cola_vacia()
    ejecucion_id = abrir_rv2(plan_simple, red)
    proveedor = ProveedorDePruebas(red.huella, arriba=False)
    monkeypatch.setattr(motor, "proveedor_configurado", lambda _config: proveedor)

    assert worker.procesar_un_trabajo("worker-a", Config(), SaludRuteo(proveedor, 60)) is None
    trabajo = _trabajo_de(ejecucion_id)
    assert (trabajo.estado, trabajo.intentos) == ("PENDIENTE", 0)
    # Sin proveedor, tampoco.
    assert worker.procesar_un_trabajo("worker-a", Config(), SaludRuteo(None, 60)) is None

    proveedor.arriba = True
    procesado = worker.procesar_un_trabajo("worker-a", Config(), SaludRuteo(proveedor, 60))
    assert procesado.estado == EstadoTrabajo.COMPLETADO
    assert ejecucion(ejecucion_id).estado == "EXITOSA"


def test_la_salud_se_pregunta_a_lo_mas_cada_tanto():
    preguntas = []

    class Contador:
        def salud(self) -> bool:
            preguntas.append(1)
            return True

    reloj = iter([0.0, 5.0, 9.9, 10.0, 25.0])
    salud = SaludRuteo(Contador(), 10, reloj=lambda: next(reloj))
    assert all(salud.disponible() for _ in range(5))
    assert len(preguntas) == 3  # a los 0, a los 10 y a los 25 segundos


def test_en_primer_plano_rutea_con_su_propio_trabajo(plan_simple, red, monkeypatch):
    proveedor = ProveedorDePruebas(red.huella)
    monkeypatch.setattr(motor, "proveedor_configurado", lambda _config: proveedor)

    e = worker.rutear_v2_en_primer_plano(plan_simple.campo_run_id, red.red_vial_version_id)

    assert e.estado == "EXITOSA"
    trabajo = _trabajo_de(e.id)
    assert (trabajo.estado, trabajo.intentos) == ("COMPLETADO", 1)


# --- el desempate, la reconstruccion y lo que no se reescribe ------------------------------------


def _pareja_invertida() -> tuple[int, int]:
    """Dos numeros de cliente n1 < n2 cuyos cuenta_id estan al reves: n1 se registra primero (id
    interno menor) y su cuenta_id es mayor."""
    for n1 in range(10, 200):
        n2 = n1 + 1
        a = cuenta_id("DSP_001", "CARTERA_PRINCIPAL", f"CU{n1:010d}")
        b = cuenta_id("DSP_001", "CARTERA_PRINCIPAL", f"CU{n2:010d}")
        if a > b:
            return n1, n2
    raise AssertionError("no hay una pareja invertida")  # pragma: no cover


def test_un_empate_se_resuelve_por_cuenta_id_y_no_por_el_id_interno(tmp_path, red):
    n1, n2 = _pareja_invertida()
    # Las dos en el mismo punto, a la misma duracion de la base.
    plan = escenario(
        tmp_path, [CuentaRuta(n1, 1_500, 1_000), CuentaRuta(n2, 1_500, 1_000)], BASES, UN_GESTOR
    ).campo

    e = rutear(plan, red, ProveedorDePruebas(red.huella))

    with sesion() as s:
        internos = dict(
            s.execute(
                text(
                    "SELECT cliente_unico, id FROM cuenta_canonica WHERE cliente_unico IN (:a, :b)"
                ),
                {"a": f"CU{n1:010d}", "b": f"CU{n2:010d}"},
            ).all()
        )
    assert internos[f"CU{n1:010d}"] < internos[f"CU{n2:010d}"]
    assert secuencia(e.id, "GESTOR_A") == [n2, n1]


def test_reconstruir_con_las_mismas_entradas_da_exactamente_lo_mismo(plan_simple, red):
    proveedor = ProveedorDePruebas(red.huella, desconectados={coordenada(*PUNTOS[4])})
    primera = rutear(plan_simple, red, proveedor)
    antes = resultado_logico(primera.id)
    conteos = {
        c: getattr(primera, c)
        for c in (
            "gestores_objetivo",
            "paradas_objetivo",
            "programadas",
            "sin_tiempo",
            "no_enrutables",
            "distancia_total_m",
            "duracion_vial_total_s",
            "duracion_espera_total_s",
        )
    }

    # Solo lo derivado de esa ejecucion: sus paradas, sus rutas, su trabajo y la ejecucion. Ni el
    # plan de campo/v2, ni la red.
    borrar(primera.id)

    reconstruida = rutear(plan_simple, red, proveedor)

    assert reconstruida.firma_inputs == primera.firma_inputs
    assert resultado_logico(reconstruida.id) == antes
    assert reconstruida.distribucion == primera.distribucion
    assert {c: getattr(reconstruida, c) for c in conteos} == conteos


@pytest.mark.parametrize("tabla", TABLAS)
def test_lo_que_ruteo_v2_publico_no_se_reescribe(plan_simple, red, tabla):
    e = rutear(plan_simple, red, ProveedorDePruebas(red.huella))
    antes = resultado_logico(e.id)

    for sentencia in (
        f"UPDATE {tabla} SET ejecucion_ruteo_v2_id = ejecucion_ruteo_v2_id",
        f"DELETE FROM {tabla}",
        f"DELETE FROM {tabla} WHERE false",
    ):
        with sesion() as s, pytest.raises(DBAPIError) as error:
            s.execute(text(sentencia))
        assert error.value.orig.diag.constraint_name == "tr_ruteo_v2_inmutable"
        assert "ruteo/v2 no se reescribe" in str(error.value)
    assert resultado_logico(e.id) == antes


def test_una_ejecucion_terminada_y_una_red_publicada_no_cambian_ni_se_borran(plan_simple, red):
    e = rutear(plan_simple, red, ProveedorDePruebas(red.huella))

    for sentencia, trigger in (
        ("UPDATE ejecucion_ruteo_v2 SET detalle = 'otro' WHERE id = :e", "tr_ruteo_v2_terminado"),
        ("DELETE FROM ejecucion_ruteo_v2 WHERE id = :e", "tr_ruteo_v2_terminado"),
        ("UPDATE version_red_vial SET region = 'otra' WHERE id = :r", "tr_red_vial_terminada"),
        ("DELETE FROM version_red_vial WHERE id = :r", "tr_red_vial_terminada"),
    ):
        with sesion() as s, pytest.raises(DBAPIError) as error:
            s.execute(text(sentencia), {"e": e.id, "r": red.id})
        assert error.value.orig.diag.constraint_name == trigger
    assert ejecucion(e.id).estado == "EXITOSA"


def test_las_tablas_de_ruteo_no_tienen_donde_guardar_una_coordenada():
    with sesion() as s:
        columnas = set(
            s.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema = "
                    "current_schema() AND table_name IN ('version_red_vial', "
                    "'ejecucion_ruteo_v2', 'ruta_gestor_v2', 'parada_ruta_v2')"
                )
            ).scalars()
        )
        geometrias = s.execute(
            text(
                "SELECT count(*) FROM information_schema.columns WHERE table_schema = "
                "current_schema() AND udt_name = 'geometry' AND table_name IN ("
                "'version_red_vial', 'ejecucion_ruteo_v2', 'ruta_gestor_v2', 'parada_ruta_v2')"
            )
        ).scalar_one()
    for prohibida in (
        "latitud",
        "longitud",
        "lat",
        "lon",
        "punto",
        "snapped_lat",
        "snapped_lon",
        "ubicacion",
        "geometria",
        "polyline",
        "polilinea",
        "bbox",
        "matriz",
    ):
        assert prohibida not in columnas, prohibida
    assert geometrias == 0


def test_ni_la_bitacora_ni_la_ejecucion_llevan_coordenadas(plan_simple, red, caplog):
    caplog.set_level("DEBUG")
    caplog.clear()

    e = rutear(plan_simple, red, ProveedorDePruebas(red.huella))

    textos = [caplog.text, e.detalle, str(e.distribucion), str(resultado_logico(e.id))]
    for n in (*PUNTOS, "BASE"):
        xy = BASE_XY if n == "BASE" else PUNTOS[n]
        lon, lat = coordenada(*xy)
        for texto in textos:
            assert lon[:8] not in texto and lat[:7] not in texto
