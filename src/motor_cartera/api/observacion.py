"""Lo que la API registra de cada peticion: un middleware ASGI, el mas externo.

Por cada peticion: su `request_id` (el que trae del gateway si tiene una forma valida; si no, uno
nuevo), devuelto en `X-Request-ID`; su span de servidor de OpenTelemetry; sus metricas por plantilla
de ruta; una linea `http.peticion` (ERROR si es un 5xx) y su evento `security-audit/v1`. Ninguno
lleva la ruta literal, la query, una cabecera ni el cuerpo.

Una excepcion que nadie manejo termina aqui: se registra con su traza (redactada) y responde un 500
generico, con su `request_id` para encontrarla en la bitacora.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid

from opentelemetry import trace
from opentelemetry.propagate import extract
from opentelemetry.trace import SpanKind, Status, StatusCode
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from motor_cartera.observabilidad import auditoria, metricas, trazas
from motor_cartera.observabilidad.logs import request_id_actual

log = logging.getLogger("motor_cartera.http")

REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
"""La forma de un request_id que se acepta del gateway; cualquier otro se reemplaza."""

SONDAS = frozenset({"/health/live", "/health/ready", "/salud"})
"""Las rutas de salud: las consultan los orquestadores cada pocos segundos. Cuentan en las metricas,
pero no dejan una linea INFO ni un evento de auditoria cada vez."""


def plantilla_de(scope: Scope) -> str:
    ruta = scope.get("route")
    return getattr(ruta, "path", None) or metricas.RUTA_NO_ENCONTRADA


def request_id_de(encabezados: Headers) -> str:
    propuesto = encabezados.get("x-request-id", "")
    return propuesto if REQUEST_ID.match(propuesto) else uuid.uuid4().hex


class Observacion:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.tracer = trazas.tracer("motor_cartera.http")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        inicio = time.perf_counter()
        encabezados = Headers(scope=scope)
        request_id = request_id_de(encabezados)
        marca = request_id_actual.set(request_id)
        metodo = scope["method"]
        respuesta = {"estado": 500, "iniciada": False}
        trazas.reiniciar_conteo_db()
        metricas.HTTP_EN_CURSO.labels(metodo=metodo).inc()
        contexto = extract(dict(encabezados))
        with self.tracer.start_as_current_span(
            f"{metodo}", kind=SpanKind.SERVER, context=contexto
        ) as span:

            async def enviar(mensaje: Message) -> None:
                if mensaje["type"] == "http.response.start":
                    respuesta["estado"] = mensaje["status"]
                    respuesta["iniciada"] = True
                    MutableHeaders(scope=mensaje)["X-Request-ID"] = request_id
                await send(mensaje)

            try:
                await self.app(scope, receive, enviar)
            except Exception:
                respuesta["estado"] = 500
                log.exception(
                    "error no controlado en %s %s",
                    metodo,
                    plantilla_de(scope),
                    extra={"event": "http.error"},
                )
                if respuesta["iniciada"]:
                    raise
                await _responder_error_interno(send, request_id)
            finally:
                metricas.HTTP_EN_CURSO.labels(metodo=metodo).dec()
                self._cerrar(scope, span, metodo, respuesta["estado"], inicio, request_id)
                request_id_actual.reset(marca)

    def _cerrar(
        self, scope: Scope, span, metodo: str, estado: int, inicio: float, request_id: str
    ) -> None:
        ruta = plantilla_de(scope)
        duracion = time.perf_counter() - inicio
        metricas.HTTP_PETICIONES.labels(
            metodo=metodo, ruta=ruta, clase_estado=metricas.clase_de_estado(estado)
        ).inc()
        metricas.HTTP_DURACION.labels(metodo=metodo, ruta=ruta).observe(duracion)
        span.update_name(f"{metodo} {ruta}")
        span.set_attribute("http.request.method", metodo)
        span.set_attribute("http.route", ruta)
        span.set_attribute("http.response.status_code", estado)
        if estado >= 500:
            span.set_status(Status(StatusCode.ERROR))
        seguridad = scope.get("state", {}).get("seguridad", {})
        decision = seguridad.get("decision")
        if decision:
            metricas.AUTORIZACIONES.labels(
                decision=decision, motivo=seguridad.get("motivo") or "-"
            ).inc()
        ms = duracion * 1000
        # El trace_id va en el registro mismo, no se deduce al formatearlo: una linea que se
        # formatea despues (otro hilo, un manejador en cola) conserva su traza.
        contexto = span.get_span_context()
        trace_id = f"{contexto.trace_id:032x}" if contexto.is_valid else None
        span_id = f"{contexto.span_id:016x}" if contexto.is_valid else None
        sonda = ruta in SONDAS
        nivel = logging.ERROR if estado >= 500 else (logging.DEBUG if sonda else logging.INFO)
        log.log(
            nivel,
            "%s %s %s",
            metodo,
            ruta,
            estado,
            extra={
                "event": "http.peticion",
                "status": estado,
                "duration_ms": round(ms, 2),
                "trace_id": trace_id,
                "span_id": span_id,
                "campos": {"method": metodo, "route": ruta},
            },
        )
        if sonda or not decision:
            return
        clase = seguridad.get("clase")
        permiso = seguridad.get("permiso")
        auditoria.registrar(
            request_id=request_id,
            trace_id=trace_id,
            actor=seguridad.get("actor"),
            actor_tipo=seguridad.get("actor_tipo"),
            ruta=ruta,
            metodo=metodo,
            clase=str(clase) if clase else None,
            permiso=str(permiso) if permiso else None,
            decision=decision,
            motivo=seguridad.get("motivo"),
            estado=estado,
            latencia_ms=ms,
        )


async def _responder_error_interno(send: Send, request_id: str) -> None:
    cuerpo = json.dumps(
        {
            "codigo": "ERROR_INTERNO",
            "mensaje": "Error interno. No es un problema de la peticion; quedo en la bitacora con "
            f"el request_id {request_id}.",
            "detalles": [],
            "run_id": None,
            "pagos_run_id": None,
        }
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 500,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(cuerpo)).encode()),
                (b"x-request-id", request_id.encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": cuerpo})


def span_actual() -> trace.Span:
    return trace.get_current_span()
