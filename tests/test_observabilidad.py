"""La observabilidad tecnica: los logs JSON con sus campos fijos y su redaccion, el request_id, las
metricas por plantilla de ruta (sin cardinalidad que dependa de los datos), las trazas
correlacionadas con los logs, la telemetria que falla abierta y lo que el worker deja de cada
trabajo."""

from __future__ import annotations

import json
import logging
import re
import sys
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client import generate_latest

from motor_cartera.config import Config
from motor_cartera.observabilidad import metricas
from motor_cartera.observabilidad.logs import (
    CAMPOS,
    FormatoJson,
    FormatoTexto,
    Redactor,
    redactar,
    request_id_actual,
)

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

_exportador = InMemorySpanExporter()


@pytest.fixture(scope="module")
def spans() -> InMemorySpanExporter:
    """Un proveedor de trazas en memoria para todo el proceso de pruebas, una vez: OpenTelemetry no
    deja reemplazar el proveedor global."""
    actual = trace.get_tracer_provider()
    if not isinstance(actual, TracerProvider):
        proveedor = TracerProvider()
        proveedor.add_span_processor(SimpleSpanProcessor(_exportador))
        trace.set_tracer_provider(proveedor)
    elif not getattr(actual, "_con_memoria_de_pruebas", False):
        actual.add_span_processor(SimpleSpanProcessor(_exportador))
    trace.get_tracer_provider()._con_memoria_de_pruebas = True  # type: ignore[attr-defined]
    return _exportador


def _registro(mensaje: str = "hola", **extra) -> logging.LogRecord:
    registro = logging.LogRecord(
        "motor_cartera.prueba", logging.INFO, __file__, 1, mensaje, (), None
    )
    for llave, valor in extra.items():
        setattr(registro, llave, valor)
    return registro


# --- los logs -------------------------------------------------------------------------------------


def test_cada_linea_json_lleva_los_campos_fijos():
    linea = json.loads(FormatoJson("api", "production").format(_registro(event="prueba")))

    assert list(linea)[: len(CAMPOS)] == list(CAMPOS)
    assert linea["service"] == "api" and linea["environment"] == "production"
    assert linea["level"] == "INFO" and linea["event"] == "prueba"
    assert linea["timestamp"].endswith("+00:00")


def test_la_linea_lleva_el_request_id_del_contexto():
    marca = request_id_actual.set("a1b2c3d4e5f60718")
    try:
        linea = json.loads(FormatoJson("api", "production").format(_registro()))
    finally:
        request_id_actual.reset(marca)

    assert linea["request_id"] == "a1b2c3d4e5f60718"


@pytest.mark.parametrize(
    ("texto", "secreto"),
    [
        ("token eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.firmafirmafirma recibido", "eyJzdWIi"),
        ("Authorization: Bearer abc.def.ghi", "abc.def.ghi"),
        ("cabeceras {'x-api-key': 'clave-super-secreta'}", "clave-super-secreta"),
        ("password=hunter2hunter2 user=x", "hunter2hunter2"),
        ("postgresql+psycopg://mc_api:contrasena-larga@postgres/x", "contrasena-larga"),
        ("cookie: __Host-mc_sesion=valorDeLaCookie123", "valorDeLaCookie123"),
        ("client_secret=valor-del-secreto-del-cliente", "valor-del-secreto-del-cliente"),
    ],
)
def test_el_redactor_quita_tokens_y_credenciales(texto, secreto):
    assert secreto not in redactar(texto)


def test_el_redactor_quita_los_secretos_registrados_aunque_no_parezcan_uno():
    redactor = Redactor()
    redactor.registrar("un-valor-cualquiera-0123", None, "corto")

    assert redactor("dice un-valor-cualquiera-0123 y corto") == "dice [SECRETO REDACTADO] y corto"


def test_una_excepcion_se_registra_redactada():
    try:
        raise RuntimeError("fallo con Bearer eyJhbGciOiJIUzI1NiJ9.eyJhIjoxfQ.firma123 adentro")
    except RuntimeError:
        registro = logging.LogRecord(
            "x", logging.ERROR, __file__, 1, "error", (), exc_info=sys.exc_info()
        )
    linea = json.loads(FormatoJson("worker", "production").format(registro))

    assert linea["exception"]["type"] == "RuntimeError"
    assert "eyJhIjoxfQ" not in linea["exception"]["stack"]


def test_el_texto_de_desarrollo_tambien_se_redacta():
    linea = FormatoTexto().format(_registro("Authorization: Bearer secreto.de.verdad"))

    assert "secreto.de.verdad" not in linea


# --- el request_id --------------------------------------------------------------------------------


def test_cada_respuesta_trae_un_request_id(cliente):
    respuesta = cliente.get("/health/live")

    assert re.fullmatch(r"[0-9a-f]{32}", respuesta.headers["X-Request-ID"])


def test_un_request_id_valido_del_gateway_se_conserva(cliente):
    respuesta = cliente.get("/health/live", headers={"X-Request-ID": "edge-0123456789abcdef"})

    assert respuesta.headers["X-Request-ID"] == "edge-0123456789abcdef"


@pytest.mark.parametrize(
    "inventado",
    ["corto", "con espacios adentro", "linea\\nnueva-0123456789", "x" * 200, "<script>"],
)
def test_un_request_id_que_no_tiene_forma_se_reemplaza(cliente, inventado):
    respuesta = cliente.get("/health/live", headers={"X-Request-ID": inventado})

    assert respuesta.headers["X-Request-ID"] != inventado
    assert re.fullmatch(r"[0-9a-f]{32}", respuesta.headers["X-Request-ID"])


# --- el log de cada peticion ----------------------------------------------------------------------


def _peticiones(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if getattr(r, "event", None) == "http.peticion"]


@pytest.mark.usefixtures("_esquema")
def test_el_log_de_la_peticion_lleva_la_plantilla_nunca_la_ruta_ni_la_query(cliente, caplog):
    caplog.set_level(logging.INFO, logger="motor_cartera.http")
    corrida = uuid.uuid4()

    cliente.get(f"/corridas/{corrida}", params={"cliente_unico": "CU0000000777"})

    registro = _peticiones(caplog)[-1]
    linea = FormatoJson("api", "test").format(registro)
    assert registro.campos["route"] == "/corridas/{run_id}"
    assert str(corrida) not in linea and "CU0000000777" not in linea
    assert registro.status == 404 and registro.duration_ms >= 0


def test_un_5xx_deja_su_log_error_con_la_traza_redactada(app, clave_api, caplog):
    from fastapi import APIRouter

    from motor_cartera.api.autorizacion import Clase, clasificar

    prueba = APIRouter()

    @prueba.get("/revienta-con-token")
    def revienta() -> None:
        raise RuntimeError("Bearer eyJhbGciOiJIUzI1NiJ9.eyJkYXRvIjoxfQ.firma999")

    app.include_router(prueba)
    clasificar(app, "GET", "/revienta-con-token", Clase.LECTURA)
    caplog.set_level(logging.INFO)
    with TestClient(app, headers={"X-API-Key": clave_api}, raise_server_exceptions=False) as c:
        respuesta = c.get("/revienta-con-token")

    assert respuesta.status_code == 500 and respuesta.headers["X-Request-ID"]
    assert respuesta.json()["codigo"] == "ERROR_INTERNO"
    errores = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert {getattr(r, "event", None) for r in errores} >= {"http.error", "http.peticion"}
    texto = "\n".join(FormatoJson("api", "test").format(r) for r in errores)
    assert "eyJkYXRvIjoxfQ" not in texto and "RuntimeError" in texto


# --- las metricas ---------------------------------------------------------------------------------


ETIQUETAS_PERMITIDAS = {
    "metodo",
    "ruta",
    "clase_estado",
    "decision",
    "motivo",
    "resultado",
    "tipo",
    "estado",
    "tipo_error",
    "servicio",
    "version",
    "le",
}


def _exposicion() -> str:
    return generate_latest(metricas.REGISTRO).decode()


@pytest.mark.usefixtures("_esquema")
def test_las_metricas_http_van_por_plantilla_y_sin_identificadores(cliente):
    identificadores = [uuid.uuid4() for _ in range(5)]
    for identificador in identificadores:
        cliente.get(f"/corridas/{identificador}")
        cliente.get(f"/decisiones-v2/{identificador}/resultados/{uuid.uuid4()}")
    cliente.get("/una/ruta/que/no/existe/123")

    exposicion = _exposicion()
    assert 'ruta="/corridas/{run_id}"' in exposicion
    assert 'ruta="__sin_ruta__"' in exposicion
    assert not UUID.search(exposicion)
    assert "/no/existe" not in exposicion


def test_ninguna_metrica_tiene_una_etiqueta_fuera_de_la_lista(cliente):
    cliente.get("/health/live")
    for linea in _exposicion().splitlines():
        if linea.startswith("#") or "{" not in linea:
            continue
        etiquetas = re.findall(r'([a-zA-Z_][a-zA-Z0-9_]*)="', linea.split("{", 1)[1])
        assert set(etiquetas) <= ETIQUETAS_PERMITIDAS, linea


def test_la_autorizacion_cuenta_sus_decisiones(cliente):
    antes = metricas.AUTORIZACIONES.labels("NO_AUTENTICADO", "API_KEY_INVALIDA")._value.get()

    cliente.get("/app/v1/sesion", headers={"X-API-Key": "una-clave-inventada"})

    despues = metricas.AUTORIZACIONES.labels("NO_AUTENTICADO", "API_KEY_INVALIDA")._value.get()
    assert despues == antes + 1


def test_las_series_que_vigilan_las_alertas_existen_en_cero_desde_el_arranque():
    """increase() no cuenta el incremento con el que nace una serie: las que vigila una alerta se
    crean al arrancar, sin esperar el primer evento."""
    metricas.inicializar_series("api")
    metricas.inicializar_series("worker")

    exposicion = _exposicion()
    for serie in (
        'mc_jwks_refrescos_total{resultado="fallido"}',
        'mc_autorizacion_total{decision="ERROR",motivo="IDENTIDAD_NO_DISPONIBLE"}',
        'mc_worker_trabajos_cerrados_total{resultado="FALLIDO",tipo="RUTEO"}',
        'mc_worker_errores_total{tipo="DECISION_V2"}',
        'mc_worker_leases_perdidos_total{tipo="INGESTA"}',
    ):
        assert serie in exposicion, serie


def _cartera_de_una_cuenta(cliente_unico: str) -> bytes:
    return (
        "cliente_unico,saldo_total,dias_atraso,producto,canal,cve_entidad,cve_municipio,"
        f"fecha_corte\n{cliente_unico},3000.00,120,CONSUMO,CAMPO,21,114,2026-09-30\n"
    ).encode()


@pytest.mark.usefixtures("bd")
def test_la_cola_se_mide_en_la_base_con_su_antiguedad_y_sus_leases_vencidos(cliente):
    from datetime import timedelta

    from sqlalchemy import func, select, update

    from motor_cartera.db.modelos import TrabajoOrquestacion
    from motor_cartera.db.sesion import sesion

    for cliente_unico in ("CU00000051", "CU00000052", "CU00000053"):
        respuesta = cliente.post(
            "/corridas",
            files={"archivo": ("cartera.csv", _cartera_de_una_cuenta(cliente_unico), "text/csv")},
        )
        assert respuesta.status_code == 201
    # Uno lo tomo un worker que dejo de latir; los otros dos esperan desde hace un minuto y medio.
    with sesion() as s:
        ids = list(s.scalars(select(TrabajoOrquestacion.id).order_by(TrabajoOrquestacion.id)))
        s.execute(
            update(TrabajoOrquestacion)
            .where(TrabajoOrquestacion.id == ids[0])
            .values(
                estado="EJECUTANDO",
                worker_id="worker-caido",
                tomado_en=func.now() - timedelta(minutes=5),
                lease_hasta=func.now() - timedelta(seconds=5),
            )
        )
        s.execute(
            update(TrabajoOrquestacion)
            .where(TrabajoOrquestacion.id.in_(ids[1:]))
            .values(disponible_desde=func.now() - timedelta(seconds=90))
        )
        s.commit()

    familias = {f.name: f for f in metricas.Cola(cache_segundos=0).collect()}

    muestras = {
        nombre: {tuple(sorted(m.labels.items())): m.value for m in familia.samples}
        for nombre, familia in familias.items()
    }
    assert muestras["mc_cola_lectura_ok"] == {(): 1}
    assert muestras["mc_cola_trabajos"] == {
        (("estado", "EJECUTANDO"), ("tipo", "INGESTA")): 1,
        (("estado", "PENDIENTE"), ("tipo", "INGESTA")): 2,
    }
    assert 90 <= muestras["mc_cola_antiguedad_segundos"][(("tipo", "INGESTA"),)] < 300
    assert muestras["mc_cola_leases_vencidos"] == {(("tipo", "INGESTA"),): 1}


def test_sin_base_la_cola_no_se_puede_leer_y_la_consulta_de_metricas_no_falla(monkeypatch):
    # El modulo, no la funcion sesion que motor_cartera.db exporta con el mismo nombre.
    modulo_sesion = sys.modules["motor_cartera.db.sesion"]

    def sin_base():
        raise OSError("la base no contesta")

    monkeypatch.setattr(modulo_sesion, "crear_motor", sin_base)

    familias = {f.name: f for f in metricas.Cola(cache_segundos=0).collect()}

    assert [m.value for m in familias["mc_cola_lectura_ok"].samples] == [0]
    assert familias["mc_cola_trabajos"].samples == []


def test_el_servidor_interno_sirve_metricas_y_nada_mas():
    import urllib.error
    import urllib.request

    servidor = metricas.servir(0, "127.0.0.1")
    puerto = servidor.server_address[1]
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{puerto}/metrics", timeout=5) as r:
            assert r.status == 200 and b"mc_http_peticiones_total" in r.read()
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(f"http://127.0.0.1:{puerto}/docs", timeout=5)
        assert error.value.code == 404
    finally:
        servidor.shutdown()


# --- las trazas -----------------------------------------------------------------------------------


@pytest.mark.usefixtures("_esquema")
def test_una_peticion_es_un_span_con_su_plantilla_y_su_trace_id_esta_en_el_log(
    cliente, spans, caplog
):
    # La salud se registra en DEBUG: los orquestadores la consultan cada pocos segundos.
    caplog.set_level(logging.DEBUG, logger="motor_cartera.http")
    spans.clear()

    cliente.get("/health/ready")

    servidor = [s for s in spans.get_finished_spans() if s.kind == trace.SpanKind.SERVER][-1]
    assert servidor.name == "GET /health/ready"
    assert servidor.attributes["http.route"] == "/health/ready"
    assert "url.query" not in servidor.attributes and "http.target" not in servidor.attributes
    base = [s for s in spans.get_finished_spans() if s.name.startswith("db ")]
    assert base and all(s.context.trace_id == servidor.context.trace_id for s in base)
    assert all("db.statement" not in s.attributes for s in base)
    linea = json.loads(FormatoJson("api", "test").format(_peticiones(caplog)[-1]))
    assert linea["trace_id"] == f"{servidor.context.trace_id:032x}"


@pytest.mark.usefixtures("_esquema")
def test_una_peticion_deja_un_solo_span_de_servidor_sin_la_ruta_real_ni_la_query(cliente, spans):
    """FastAPI trae su propia telemetria, con la ruta real y la query en el span: esta apagada, y
    el unico span de servidor es el de Observacion, con la plantilla."""
    spans.clear()
    corrida = uuid.uuid4()

    cliente.get(f"/corridas/{corrida}", params={"cliente_unico": "CU0000000777"})

    terminados = spans.get_finished_spans()
    servidores = [s for s in terminados if s.kind == trace.SpanKind.SERVER]
    assert [s.name for s in servidores] == ["GET /corridas/{run_id}"]
    assert not [s for s in terminados if s.name.startswith("fastapi.")]
    valores = " ".join(str(v) for s in terminados for v in s.attributes.values())
    assert str(corrida) not in valores and "CU0000000777" not in valores
    assert all("url.path" not in s.attributes for s in terminados)


def test_un_5xx_marca_su_span_con_error(app, clave_api, spans):
    from fastapi import APIRouter

    from motor_cartera.api.autorizacion import Clase, clasificar

    prueba = APIRouter()

    @prueba.get("/revienta-para-la-traza")
    def revienta() -> None:
        raise RuntimeError("falla")

    app.include_router(prueba)
    clasificar(app, "GET", "/revienta-para-la-traza", Clase.LECTURA)
    spans.clear()
    with TestClient(app, headers={"X-API-Key": clave_api}, raise_server_exceptions=False) as c:
        c.get("/revienta-para-la-traza")

    servidor = [s for s in spans.get_finished_spans() if s.kind == trace.SpanKind.SERVER][-1]
    assert servidor.status.status_code == trace.StatusCode.ERROR
    assert servidor.attributes["http.response.status_code"] == 500


def test_el_traceparent_de_un_cliente_no_decide_por_si_solo(cliente, spans):
    """El edge lo borra; si llega, el span lo respeta como padre, pero no por eso lleva datos."""
    spans.clear()
    padre = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"

    cliente.get("/health/live", headers={"traceparent": padre})

    servidor = spans.get_finished_spans()[-1]
    assert f"{servidor.context.trace_id:032x}" == "0af7651916cd43dd8448eb211c80319c"


def test_con_el_colector_caido_la_api_sigue_respondiendo(monkeypatch, clave_api):
    """Una API cuyas trazas van a un colector que no existe (el puerto 9, descartado): exportar
    falla en el hilo del procesador por lotes, y ninguna peticion lo espera."""
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    from motor_cartera.api import crear_app
    from motor_cartera.observabilidad import trazas

    proveedor = TracerProvider()
    exportador = OTLPSpanExporter(endpoint="http://127.0.0.1:9/v1/traces", timeout=1)
    procesador = BatchSpanProcessor(exportador, schedule_delay_millis=50, export_timeout_millis=500)
    proveedor.add_span_processor(procesador)
    monkeypatch.setattr(trazas, "tracer", proveedor.get_tracer)
    app = crear_app(Config(api_key=clave_api))
    try:
        with TestClient(app) as cliente:
            inicio = time.perf_counter()
            estados = [cliente.get("/health/live").status_code for _ in range(30)]
            transcurrido = time.perf_counter() - inicio
    finally:
        proveedor.shutdown()

    assert estados == [200] * 30
    assert transcurrido < 10


# --- el worker ------------------------------------------------------------------------------------


@pytest.mark.usefixtures("bd")
def test_un_trabajo_deja_sus_metricas_su_span_y_su_log(cliente, trabajar, spans, caplog):
    import pandas as pd

    cartera = pd.DataFrame(
        {
            "cliente_unico": ["CU00000041"],
            "saldo_total": ["3000.00"],
            "dias_atraso": [120],
            "producto": ["CONSUMO"],
            "canal": ["CAMPO"],
            "cve_entidad": ["21"],
            "cve_municipio": ["114"],
            "fecha_corte": ["2026-09-30"],
        }
    ).to_csv(index=False)
    caplog.set_level(logging.INFO, logger="motor_cartera")
    tomados = metricas.WORKER_TOMADOS.labels("INGESTA")._value.get()
    completados = metricas.WORKER_CERRADOS.labels("INGESTA", "COMPLETADO")._value.get()
    spans.clear()

    respuesta = cliente.post(
        "/corridas", files={"archivo": ("cartera.csv", cartera.encode(), "text/csv")}
    )
    assert respuesta.status_code == 201
    procesados = trabajar()

    assert [p.tipo.value for p in procesados][:1] == ["INGESTA"]
    assert metricas.WORKER_TOMADOS.labels("INGESTA")._value.get() == tomados + 1
    assert metricas.WORKER_CERRADOS.labels("INGESTA", "COMPLETADO")._value.get() == completados + 1
    assert 'mc_worker_duracion_trabajo_segundos_count{tipo="INGESTA"}' in _exposicion()
    trabajo = [s for s in spans.get_finished_spans() if s.name == "trabajo INGESTA"][-1]
    assert trabajo.attributes["mc.trabajo.resultado"] == "COMPLETADO"
    hijos = [
        s
        for s in spans.get_finished_spans()
        if s.parent and s.parent.span_id == trabajo.context.span_id
    ]
    assert hijos and all(s.name.startswith("db ") for s in hijos)
    cerrado = [
        r for r in caplog.records if "COMPLETADO" in r.getMessage() and "INGESTA" in r.getMessage()
    ]
    assert cerrado and "CU00000041" not in "\n".join(r.getMessage() for r in caplog.records)


def test_la_salud_del_worker_distingue_vivo_listo_y_latido_vencido(monkeypatch):
    from motor_cartera.orquestacion.salud import SaludDelWorker

    salud = SaludDelWorker(umbral_segundos=60)
    salud.latir()
    assert salud.vivo()[0] == 200

    salud._latido -= 61
    estado, cuerpo = salud.vivo()
    assert estado == 503 and cuerpo["estado"] == "latido_vencido"


@pytest.mark.usefixtures("_esquema")
def test_la_readiness_del_worker_depende_de_la_cola_no_de_osrm():
    from motor_cartera.orquestacion.salud import SaludDelWorker

    estado, cuerpo = SaludDelWorker().listo()

    assert estado == 200
    assert cuerpo == {
        "estado": "listo",
        "base_de_datos": "ok",
        "cola": "ok",
        "esquema": "ok",
        "ruteo": "sin_configurar",
    }


def test_sin_base_el_worker_no_esta_listo_pero_sigue_vivo(monkeypatch):
    import psycopg
    from sqlalchemy import create_engine

    from motor_cartera.orquestacion import salud as salud_del_worker

    def sin_servidor():
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(
        salud_del_worker,
        "crear_motor",
        lambda: create_engine("postgresql+psycopg://", creator=sin_servidor),
    )
    salud = salud_del_worker.SaludDelWorker()
    salud.latir()

    assert salud.listo()[0] == 503
    assert salud.vivo()[0] == 200


def test_la_configuracion_de_logs_del_proceso_es_idempotente():
    from motor_cartera.observabilidad import logs

    raiz = logging.getLogger()
    antes = list(raiz.handlers)
    try:
        for _ in range(3):
            logs.configurar(Config(_env_file=None, app_env="production"), "api")
        propios = [h for h in raiz.handlers if getattr(h, "_motor_cartera", False)]
        assert len(propios) == 1
        assert isinstance(propios[0].formatter, FormatoJson)
        assert logging.getLogger("uvicorn.access").disabled
    finally:
        for manejador in list(raiz.handlers):
            if manejador not in antes:
                raiz.removeHandler(manejador)
        logging.getLogger("uvicorn.access").disabled = False
