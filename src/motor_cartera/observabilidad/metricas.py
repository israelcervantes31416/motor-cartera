"""Las metricas de Prometheus de la API y del worker.

Ninguna etiqueta lleva un identificador: ni una cuenta, ni una ejecucion, ni un modelo, ni un
trabajo, ni un actor, ni un request_id, ni una ruta literal. Las etiquetas salen de listas cerradas:
la plantilla de la ruta (`/corridas/{run_id}`, nunca el UUID), el metodo, la clase del estado
(`2xx`...), el tipo de trabajo, como termino y los codigos estables de la autorizacion. Asi la
cardinalidad no depende de los datos.

Se sirven en un puerto interno (`MC_METRICAS_PUERTO`), aparte del de la API: no pasan por el edge ni
se publican al host. El worker sirve en el mismo puerto su salud.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    ProcessCollector,
    generate_latest,
)
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

log = logging.getLogger(__name__)

REGISTRO = CollectorRegistry(auto_describe=True)
ProcessCollector(registry=REGISTRO)

RUTA_NO_ENCONTRADA = "__sin_ruta__"
"""La etiqueta de una peticion que no corresponde a ninguna ruta: nunca su camino literal."""

# --- HTTP ----------------------------------------------------------------------------------------

HTTP_PETICIONES = Counter(
    "mc_http_peticiones_total",
    "Peticiones HTTP atendidas, por metodo, plantilla de la ruta y clase del estado.",
    ["metodo", "ruta", "clase_estado"],
    registry=REGISTRO,
)
HTTP_DURACION = Histogram(
    "mc_http_duracion_segundos",
    "Duracion de las peticiones HTTP, por metodo y plantilla de la ruta.",
    ["metodo", "ruta"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 1.5, 2.5, 5, 10, 30),
    registry=REGISTRO,
)
HTTP_EN_CURSO = Gauge(
    "mc_http_en_curso",
    "Peticiones HTTP en curso, por metodo (la plantilla se conoce al terminar el ruteo).",
    ["metodo"],
    registry=REGISTRO,
)

# --- La autorizacion -----------------------------------------------------------------------------

AUTORIZACIONES = Counter(
    "mc_autorizacion_total",
    "Decisiones de autorizacion, por decision y motivo (codigos estables).",
    ["decision", "motivo"],
    registry=REGISTRO,
)
JWKS_REFRESCOS = Counter(
    "mc_jwks_refrescos_total",
    "Intentos de obtener la JWKS del emisor, por resultado.",
    ["resultado"],
    registry=REGISTRO,
)

# --- La base de datos ----------------------------------------------------------------------------

DB_ERRORES = Counter(
    "mc_db_errores_total",
    "Errores de la base, por clase de error (sin el texto de la consulta).",
    ["tipo"],
    registry=REGISTRO,
)
DB_POOL_AGOTADO = Counter(
    "mc_db_pool_agotado_total",
    "Veces que una peticion espero una conexion del pool hasta agotar su tiempo.",
    registry=REGISTRO,
)
TIPOS_DE_ERROR = frozenset(
    {
        "OperationalError",
        "IntegrityError",
        "DataError",
        "ProgrammingError",
        "InternalError",
        "NotSupportedError",
        "InterfaceError",
        "DatabaseError",
        "QueryCanceled",
        "SerializationFailure",
        "DeadlockDetected",
    }
)

# --- El worker -----------------------------------------------------------------------------------

WORKER_TOMADOS = Counter(
    "mc_worker_trabajos_tomados_total",
    "Trabajos que el worker tomo de la cola, por tipo.",
    ["tipo"],
    registry=REGISTRO,
)
WORKER_CERRADOS = Counter(
    "mc_worker_trabajos_cerrados_total",
    "Trabajos que el worker cerro, por tipo y resultado (COMPLETADO, PENDIENTE para reintentar, "
    "FALLIDO o PERDIDO si perdio el lease).",
    ["tipo", "resultado"],
    registry=REGISTRO,
)
WORKER_ERRORES = Counter(
    "mc_worker_errores_total",
    "Intentos que terminaron en un error del worker (no del motor), por tipo.",
    ["tipo"],
    registry=REGISTRO,
)
WORKER_DURACION = Histogram(
    "mc_worker_duracion_trabajo_segundos",
    "Duracion de la ejecucion de un trabajo, por tipo.",
    ["tipo"],
    buckets=(0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600, 1800, 3600),
    registry=REGISTRO,
)
WORKER_LEASES_PERDIDOS = Counter(
    "mc_worker_leases_perdidos_total",
    "Trabajos cuyo lease vencio mientras el worker los ejecutaba, por tipo.",
    ["tipo"],
    registry=REGISTRO,
)
WORKER_LATIDO = Gauge(
    "mc_worker_latido_timestamp_segundos",
    "Ultima vez (epoch) que el ciclo del worker o el latido de su trabajo dio senal de vida.",
    registry=REGISTRO,
)
WORKER_EN_CURSO = Gauge(
    "mc_worker_trabajo_en_curso",
    "1 si el worker ejecuta un trabajo ahora.",
    registry=REGISTRO,
)

RUTEO_DISPONIBLE = Gauge(
    "mc_ruteo_disponible",
    "1 si el proveedor de ruteo (OSRM) del worker responde, 0 si no. Sin proveedor, no se publica.",
    registry=REGISTRO,
)

SALUD_LISTA = Gauge(
    "mc_salud_lista",
    "1 si la ultima readiness de este proceso estuvo lista (la base contesta con su esquema).",
    registry=REGISTRO,
)

CONSTRUCCION = Gauge(
    "mc_build_info",
    "La version del servicio que corre (siempre 1).",
    ["servicio", "version"],
    registry=REGISTRO,
)


def clase_de_estado(estado: int) -> str:
    return f"{estado // 100}xx" if 100 <= estado < 600 else "otro"


def informacion_de_construccion(servicio: str) -> None:
    from motor_cartera import __version__

    CONSTRUCCION.labels(servicio=servicio, version=__version__).set(1)


def inicializar_series(servicio: str) -> None:
    """Crea en cero las series que las alertas vigilan con increase(): una serie que nace con su
    primer incremento no cuenta ese incremento, y la alerta llegaria un evento tarde. Son listas
    cerradas: no agregan cardinalidad que dependa de los datos."""
    if servicio == "api":
        for resultado in ("ok", "fallido"):
            JWKS_REFRESCOS.labels(resultado=resultado)
        AUTORIZACIONES.labels(decision="ERROR", motivo="IDENTIDAD_NO_DISPONIBLE")
    elif servicio == "worker":
        from motor_cartera.db.modelos import TipoTrabajo

        for tipo in TipoTrabajo:
            WORKER_CERRADOS.labels(tipo=tipo.value, resultado="FALLIDO")
            WORKER_ERRORES.labels(tipo=tipo.value)
            WORKER_LEASES_PERDIDOS.labels(tipo=tipo.value)


def refresco_de_jwks(resultado: str) -> None:
    JWKS_REFRESCOS.labels(resultado=resultado).inc()


def tipo_de_error_de_base(error: BaseException) -> str:
    """La clase del error de la base, de una lista cerrada; otra cualquiera cuenta como `otro`."""
    for candidato in (getattr(error, "orig", None), error):
        nombre = type(candidato).__name__ if candidato is not None else ""
        if nombre in TIPOS_DE_ERROR:
            return nombre
    return "otro"


class PoolDeConexiones(Collector):
    """El estado del pool de SQLAlchemy de este proceso, leido en cada consulta de Prometheus."""

    @staticmethod
    def _familias() -> dict[str, GaugeMetricFamily]:
        return {
            "tamano": GaugeMetricFamily("mc_db_pool_tamano", "Conexiones que el pool mantiene."),
            "en_uso": GaugeMetricFamily("mc_db_pool_en_uso", "Conexiones prestadas ahora."),
            "libres": GaugeMetricFamily("mc_db_pool_libres", "Conexiones abiertas sin prestar."),
            "desborde": GaugeMetricFamily(
                "mc_db_pool_desborde", "Conexiones abiertas por encima del tamano del pool."
            ),
        }

    def describe(self):  # noqa: D102 - interfaz de prometheus_client: registrar no lee nada
        yield from self._familias().values()

    def collect(self):  # noqa: D102 - interfaz de prometheus_client
        from motor_cartera.db.sesion import motor_actual

        familias = self._familias()
        pool = getattr(motor_actual(), "pool", None)
        if pool is not None and hasattr(pool, "checkedout"):
            familias["tamano"].add_metric([], pool.size())
            familias["en_uso"].add_metric([], pool.checkedout())
            familias["libres"].add_metric([], pool.checkedin())
            familias["desborde"].add_metric([], max(pool.overflow(), 0))
        yield from familias.values()


REGISTRO.register(PoolDeConexiones())


class Cola(Collector):
    """La profundidad y la antiguedad de la cola durable, contadas en PostgreSQL con el indice
    parcial de lo reclamable (solo PENDIENTE y EJECUTANDO). Se consulta a lo mas cada
    `cache_segundos`, con un tope de tiempo: medir la cola nunca pesa sobre la base."""

    CONSULTA = (
        "SELECT tipo, estado, count(*) AS n, "
        "extract(epoch FROM now() - min(disponible_desde) "
        "FILTER (WHERE estado = 'PENDIENTE' AND disponible_desde <= now())) AS antiguedad, "
        "count(*) FILTER (WHERE estado = 'EJECUTANDO' AND lease_hasta < now()) AS vencidos "
        "FROM trabajo_orquestacion WHERE estado IN ('PENDIENTE', 'EJECUTANDO') "
        "GROUP BY tipo, estado"
    )

    def __init__(self, cache_segundos: float = 10) -> None:
        self.cache_segundos = cache_segundos
        self._leido_en = 0.0
        self._filas: list[Any] | None = None
        self._ok = False
        self._candado = threading.Lock()

    def _leer(self) -> None:
        from sqlalchemy import text

        from motor_cartera.db.sesion import crear_motor

        try:
            with crear_motor().connect() as conexion, conexion.begin():
                conexion.execute(text("SET LOCAL statement_timeout = 2000"))
                self._filas = list(conexion.execute(text(self.CONSULTA)))
            self._ok = True
        except Exception as error:  # la base caida no debe tumbar la consulta de metricas
            self._ok = False
            log.warning("metricas de la cola: no se pudieron leer (%s)", type(error).__name__)

    @staticmethod
    def _familias() -> tuple[GaugeMetricFamily, ...]:
        return (
            GaugeMetricFamily(
                "mc_cola_trabajos",
                "Trabajos de la cola por estado y tipo.",
                labels=["estado", "tipo"],
            ),
            GaugeMetricFamily(
                "mc_cola_antiguedad_segundos",
                "Edad del trabajo PENDIENTE disponible mas antiguo, por tipo.",
                labels=["tipo"],
            ),
            GaugeMetricFamily(
                "mc_cola_leases_vencidos",
                "Trabajos EJECUTANDO cuyo lease ya vencio (su worker dejo de latir), por tipo.",
                labels=["tipo"],
            ),
            GaugeMetricFamily("mc_cola_lectura_ok", "1 si la ultima lectura de la cola funciono."),
        )

    def describe(self):  # noqa: D102 - interfaz de prometheus_client: registrar no consulta la base
        yield from self._familias()

    def collect(self):  # noqa: D102 - interfaz de prometheus_client
        with self._candado:
            if time.monotonic() - self._leido_en >= self.cache_segundos:
                self._leer()
                self._leido_en = time.monotonic()
            filas = self._filas or []
            ok = self._ok
        trabajos, antiguedad, vencidos, lectura = self._familias()
        for tipo, estado, n, edad, vencidos_n in filas:
            trabajos.add_metric([str(estado), str(tipo)], n)
            if edad is not None:
                antiguedad.add_metric([str(tipo)], float(edad))
            if vencidos_n:
                vencidos.add_metric([str(tipo)], vencidos_n)
        lectura.add_metric([], 1 if ok else 0)
        yield from (trabajos, antiguedad, vencidos, lectura)


_cola_registrada = False


def registrar_cola() -> None:
    """Agrega las metricas de la cola al registro, una vez por proceso (la API las exporta: si todos
    los workers caen, la cola se sigue viendo crecer)."""
    global _cola_registrada
    if not _cola_registrada:
        REGISTRO.register(Cola())
        _cola_registrada = True


# --- El servidor interno -------------------------------------------------------------------------

RUTAS_DE_SALUD: dict[str, Callable[[], tuple[int, dict]]] = {}
"""Rutas de salud adicionales del puerto interno (el worker registra las suyas)."""


class _Manejador(BaseHTTPRequestHandler):
    server_version = "metricas"
    sys_version = ""

    def do_GET(self) -> None:  # noqa: N802 - interfaz de http.server
        ruta = self.path.split("?", 1)[0]
        if ruta == "/metrics":
            self._responder(200, generate_latest(REGISTRO), CONTENT_TYPE_LATEST)
            return
        manejador = RUTAS_DE_SALUD.get(ruta)
        if manejador is None:
            self._responder(404, b'{"codigo":"RUTA_NO_ENCONTRADA"}', "application/json")
            return
        try:
            estado, cuerpo = manejador()
        except Exception:  # pragma: no cover - una salud que falla no tumba el servidor
            log.exception("salud interna: error al evaluar %s", ruta)
            estado, cuerpo = 503, {"estado": "error"}
        self._responder(estado, json.dumps(cuerpo).encode(), "application/json")

    def _responder(self, estado: int, cuerpo: bytes, tipo: str) -> None:
        self.send_response(estado)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, formato: str, *argumentos: Any) -> None:
        # Prometheus consulta cada pocos segundos: no se registra cada consulta.
        return


def servir(puerto: int, direccion: str = "0.0.0.0") -> ThreadingHTTPServer:
    """Sirve /metrics (y las rutas de salud registradas) en un hilo aparte. Escucha en todas las
    interfaces del contenedor para que Prometheus lo alcance por la red de observabilidad; el
    puerto nunca se publica al host."""
    servidor = ThreadingHTTPServer((direccion, puerto), _Manejador)
    servidor.daemon_threads = True
    hilo = threading.Thread(target=servidor.serve_forever, name="metricas", daemon=True)
    hilo.start()
    log.info("metricas en el puerto interno %s", servidor.server_address[1])
    return servidor
