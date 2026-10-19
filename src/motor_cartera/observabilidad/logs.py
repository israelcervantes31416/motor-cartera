"""Los logs de los procesos de Python.

En produccion cada linea es un objeto JSON con los mismos campos: `timestamp` (UTC), `level`,
`service`, `environment`, `logger`, `event`, `message`, `request_id`, `trace_id`, `span_id`,
`duration_ms` y `status` (nulos cuando no aplican), mas los campos estructurados del evento.
Fuera de produccion, texto para una persona.

Lo que nunca debe llegar a una linea, llegue por donde llegue (un mensaje, una excepcion, una URL),
se redacta aqui: un JWT, un `Bearer`, el valor de una cabecera o un parametro de credenciales, la
contrasena de una URL de base de datos y cada secreto de la configuracion. Los datos de una cuenta
no se redactan aqui porque no se escriben: los logs llevan la plantilla de la ruta, nunca la ruta ni
la query, y SQLAlchemy oculta los parametros (`hide_parameters`). Las pruebas lo verifican.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import traceback
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from motor_cartera.config import Config

request_id_actual: ContextVar[str | None] = ContextVar("request_id", default=None)
"""El request_id de la peticion que atiende este contexto, para cada linea que se escriba en
ella."""

CAMPOS = (
    "timestamp",
    "level",
    "service",
    "environment",
    "logger",
    "event",
    "message",
    "request_id",
    "trace_id",
    "span_id",
    "duration_ms",
    "status",
)
"""Los campos de toda linea JSON, en este orden."""

_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]*")
_BEARER = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]+")
_CREDENCIAL = re.compile(
    r"(?i)\b(authorization|cookie|set-cookie|x-api-key|x-mc-gateway|api[_-]?key|password|passwd"
    r"|contrasena|secret|secreto|token|client_secret|cookie_secret)"
    r"(\"?'?\s*[:=]\s*\"?'?)([^\s\"',;&]+)"
)
_URL_CON_CONTRASENA = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^:/@\s]+):([^@\s]+)@")


RUIDOSOS = ("sqlalchemy", "httpx", "httpx2", "urllib3")
"""Bibliotecas que en INFO o DEBUG escriben el SQL o la URL entera de cada peticion (con su query):
solo sus advertencias llegan a la bitacora."""


class Redactor:
    """Redacta tokens, credenciales y los secretos registrados de un texto."""

    def __init__(self) -> None:
        self._secretos: list[str] = []

    def registrar(self, *secretos: str | None) -> None:
        """Agrega valores secretos exactos (de 8 o mas caracteres) que no deben aparecer nunca."""
        for secreto in secretos:
            if secreto and len(secreto) >= 8 and secreto not in self._secretos:
                self._secretos.append(secreto)
        self._secretos.sort(key=len, reverse=True)

    def __call__(self, texto: str) -> str:
        for secreto in self._secretos:
            if secreto in texto:
                texto = texto.replace(secreto, "[SECRETO REDACTADO]")
        texto = _JWT.sub("[JWT REDACTADO]", texto)
        texto = _BEARER.sub(r"\1 [REDACTADO]", texto)
        texto = _URL_CON_CONTRASENA.sub(r"\1:[REDACTADO]@", texto)
        return _CREDENCIAL.sub(r"\1\2[REDACTADO]", texto)


redactar = Redactor()


def _contexto_de_traza() -> tuple[str | None, str | None]:
    try:
        from opentelemetry import trace
    except ImportError:  # pragma: no cover - es una dependencia del proyecto
        return None, None
    contexto = trace.get_current_span().get_span_context()
    if not contexto.is_valid:
        return None, None
    return f"{contexto.trace_id:032x}", f"{contexto.span_id:016x}"


class FormatoJson(logging.Formatter):
    """Una linea JSON por registro, con CAMPOS siempre presentes."""

    def __init__(self, servicio: str, entorno: str) -> None:
        super().__init__()
        self.servicio = servicio
        self.entorno = entorno

    def format(self, record: logging.LogRecord) -> str:
        trace_id, span_id = _contexto_de_traza()
        datos: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "service": self.servicio,
            "environment": self.entorno,
            "logger": record.name,
            "event": getattr(record, "event", None) or "log",
            "message": redactar(record.getMessage()),
            "request_id": getattr(record, "request_id", None) or request_id_actual.get(),
            "trace_id": getattr(record, "trace_id", None) or trace_id,
            "span_id": getattr(record, "span_id", None) or span_id,
            "duration_ms": getattr(record, "duration_ms", None),
            "status": getattr(record, "status", None),
        }
        campos = getattr(record, "campos", None)
        if isinstance(campos, dict):
            for llave, valor in campos.items():
                if llave not in datos:
                    datos[llave] = redactar(valor) if isinstance(valor, str) else valor
        if record.exc_info and record.exc_info[0] is not None:
            tipo, _, _ = record.exc_info
            datos["exception"] = {
                "type": tipo.__name__,
                "stack": redactar("".join(traceback.format_exception(*record.exc_info))),
            }
        return json.dumps(datos, ensure_ascii=False, default=str)


class FormatoTexto(logging.Formatter):
    """El texto de siempre, con el request_id si lo hay, redactado igual que el JSON."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)s [%(name)s] %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        texto = super().format(record)
        request_id = getattr(record, "request_id", None) or request_id_actual.get()
        if request_id:
            texto = f"{texto} request_id={request_id}"
        return redactar(texto)


def formateador(config: Config, servicio: str) -> logging.Formatter:
    if config.formato_de_logs == "json":
        return FormatoJson(servicio, config.app_env)
    return FormatoTexto()


def secretos_de(config: Config) -> list[str]:
    """Los valores secretos de la configuracion, para que el redactor los conozca."""
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import ArgumentError

    valores: list[str] = []
    for campo in (config.api_key, config.gateway_secrets, config.auditoria_clave):
        if campo is not None:
            valores += [v.strip() for v in campo.get_secret_value().splitlines() if v.strip()]
    try:
        contrasena = make_url(config.database_url).password
    except ArgumentError:
        contrasena = None
    if contrasena:
        valores.append(str(contrasena))
    return valores


def configurar(config: Config, servicio: str) -> None:
    """Los logs del proceso: un manejador en stderr con el formato del entorno, el nivel declarado,
    y
    los loggers de uvicorn pasando por el mismo formato. El log de accesos de uvicorn se apaga: cada
    peticion ya deja su evento `http.peticion`, con la plantilla de la ruta y sin la query."""
    redactar.registrar(*secretos_de(config))
    raiz = logging.getLogger()
    for manejador in list(raiz.handlers):
        if getattr(manejador, "_motor_cartera", False):
            raiz.removeHandler(manejador)
    manejador = logging.StreamHandler(sys.stderr)
    manejador._motor_cartera = True  # type: ignore[attr-defined]
    manejador.setFormatter(formateador(config, servicio))
    raiz.addHandler(manejador)
    raiz.setLevel(config.log_nivel)
    for nombre in ("uvicorn", "uvicorn.error"):
        registro = logging.getLogger(nombre)
        registro.handlers.clear()
        registro.propagate = True
    acceso = logging.getLogger("uvicorn.access")
    acceso.handlers.clear()
    acceso.propagate = False
    acceso.disabled = True
    for nombre in RUIDOSOS:
        logging.getLogger(nombre).setLevel(logging.WARNING)
