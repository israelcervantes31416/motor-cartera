"""Las trazas de OpenTelemetry de la API y del worker.

Que se traza: cada peticion HTTP (un span de servidor con la plantilla de la ruta, nunca la ruta
literal ni la query), cada sentencia SQL dentro de una traza muestreada (sin el texto ni los
parametros: solo la operacion), cada peticion a OSRM y cada trabajo del worker. No hay un span por
fila: una sentencia es un span, y una traza tiene a lo mas MAXIMO_SPANS_DB de la base.

Muestreo: `ParentBased(TraceIdRatioBased(MC_OTEL_MUESTREO))`. El edge borra el `traceparent` que
manda un cliente, asi que nadie de fuera decide que se muestrea.

Exportacion: OTLP/HTTP en lotes, en un hilo aparte. Si el colector no responde, el lote se
descarta despues de su tiempo de espera y la peticion de negocio ni se entera: la observabilidad
falla abierta.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanLimits, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import SpanKind, Status, StatusCode
from sqlalchemy import event

from motor_cartera.config import Config

log = logging.getLogger(__name__)

MAXIMO_SPANS_DB = 200
"""Cuantas sentencias de la base se trazan, como mucho, en una peticion o un trabajo."""

OPERACIONES = frozenset(
    {"SELECT", "INSERT", "UPDATE", "DELETE", "WITH", "COPY", "CREATE", "DROP", "ANALYZE", "SET"}
)
_PRIMERA_PALABRA = re.compile(r"^\s*([A-Za-z]+)")

_proveedor: TracerProvider | None = None
_spans_db: ContextVar[int] = ContextVar("spans_db", default=0)


def proveedor() -> TracerProvider | None:
    return _proveedor


def configurar(config: Config, servicio: str) -> TracerProvider:
    """El proveedor de trazas del proceso, una vez. Sin MC_OTEL_ENDPOINT no exporta nada, pero los
    logs siguen llevando el trace_id de cada peticion para correlacionarlos."""
    global _proveedor
    if _proveedor is not None:
        return _proveedor
    from motor_cartera import __version__

    recurso = Resource.create(
        {
            "service.name": f"motor-cartera-{servicio}",
            "service.version": __version__,
            "deployment.environment.name": config.app_env,
        }
    )
    _proveedor = TracerProvider(
        resource=recurso,
        sampler=ParentBased(TraceIdRatioBased(config.otel_muestreo)),
        span_limits=SpanLimits(max_span_attribute_length=512, max_attributes=64, max_events=32),
    )
    if config.otel_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        exportador = OTLPSpanExporter(
            endpoint=config.otel_endpoint.rstrip("/") + "/v1/traces", timeout=3
        )
        _proveedor.add_span_processor(
            BatchSpanProcessor(
                exportador,
                max_queue_size=2048,
                schedule_delay_millis=2000,
                max_export_batch_size=512,
                export_timeout_millis=3000,
            )
        )
    trace.set_tracer_provider(_proveedor)
    return _proveedor


def apagar() -> None:
    """Exporta lo pendiente, con su tiempo de espera, y cierra el proveedor. Se llama al apagar
    el proceso."""
    if _proveedor is not None:
        try:
            _proveedor.shutdown()
        except Exception:  # pragma: no cover - apagar no debe fallar por la telemetria
            log.warning("trazas: no se pudieron exportar las ultimas al apagar")


def tracer(nombre: str) -> trace.Tracer:
    return trace.get_tracer(nombre)


def reiniciar_conteo_db() -> None:
    """Cada peticion y cada trabajo empiezan su propio conteo de sentencias trazadas."""
    _spans_db.set(0)


def operacion(sentencia: str) -> str:
    coincidencia = _PRIMERA_PALABRA.match(sentencia)
    palabra = coincidencia.group(1).upper() if coincidencia else ""
    return palabra if palabra in OPERACIONES else "OTRA"


@contextmanager
def span_cliente(nombre: str, atributos: dict[str, Any] | None = None) -> Iterator[Any]:
    """Un span de cliente (una peticion saliente), solo si hay una traza muestreada en curso."""
    if not trace.get_current_span().is_recording():
        yield None
        return
    with tracer("motor_cartera.cliente").start_as_current_span(
        nombre, kind=SpanKind.CLIENT, attributes=atributos or {}
    ) as span:
        yield span


def instrumentar_motor(motor: Any) -> None:
    """Cuenta los errores de la base en las metricas y, dentro de una traza muestreada, abre un span
    por sentencia: con la operacion, nunca con el texto ni los parametros."""
    from motor_cartera.observabilidad.metricas import DB_ERRORES, tipo_de_error_de_base

    tracer_db = tracer("motor_cartera.db")

    @event.listens_for(motor, "before_cursor_execute")
    def _antes(conexion, cursor, sentencia, parametros, contexto, varias):  # noqa: ANN001
        if contexto is None or not trace.get_current_span().is_recording():
            return
        conteo = _spans_db.get()
        if conteo >= MAXIMO_SPANS_DB:
            return
        _spans_db.set(conteo + 1)
        nombre = operacion(sentencia)
        contexto._mc_span = tracer_db.start_span(
            f"db {nombre}",
            kind=SpanKind.CLIENT,
            attributes={"db.system.name": "postgresql", "db.operation.name": nombre},
        )

    @event.listens_for(motor, "after_cursor_execute")
    def _despues(conexion, cursor, sentencia, parametros, contexto, varias):  # noqa: ANN001
        span = getattr(contexto, "_mc_span", None)
        if span is not None:
            span.end()
            contexto._mc_span = None

    @event.listens_for(motor, "handle_error")
    def _error(contexto_de_excepcion):  # noqa: ANN001
        tipo = tipo_de_error_de_base(contexto_de_excepcion.original_exception)
        DB_ERRORES.labels(tipo=tipo).inc()
        contexto = contexto_de_excepcion.execution_context
        span = getattr(contexto, "_mc_span", None) if contexto is not None else None
        if contexto is not None and span is not None:
            span.set_status(Status(StatusCode.ERROR, tipo))
            span.end()
            contexto._mc_span = None
