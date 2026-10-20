"""El proveedor de ruteo: lo que ruteo/v2 le pide a la red vial, sin que el motor sepa de HTTP.

Un proveedor sirve una red vial preparada, y de ella da dos cosas: el snap de cada punto (la
distancia del punto a su segmento de la red, o que no tiene uno) y la matriz de duraciones y
distancias por calle entre puntos. Antes de responder nada, una sesion del proveedor se ata a la
huella de la version de la red que pidio la ejecucion: si el backend sirve otra red, falla cerrado
(BackendIncompatible), y nunca se usa la red equivocada.

La primera implementacion es OSRM autoalojado (`ClienteOsrm`), por HTTP y siempre con POST: las
coordenadas viajan en el cuerpo JSON, nunca en la URL, asi que ni una excepcion ni una bitacora de
un proxy las pueden repetir. Cada respuesta trae su `data_version`, que la preparacion de la red
fija con la huella de la version (`osrm-extract --data_version`), y se compara con la pedida.

Un fallo tecnico (sin conexion, un timeout, un 5xx, una respuesta que no es lo que debe) es
FallaProveedorRuteo: nunca se convierte en un resultado de negocio, ni en una distancia en linea
recta. Su mensaje dice el servicio, el tipo de error y los intentos, y nada mas: ni la URL, ni el
cuerpo, ni una coordenada.
"""

from __future__ import annotations

import http.client
import json
import logging
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol
from urllib.parse import urlsplit

from motor_cartera.invariantes import presente

log = logging.getLogger(__name__)

PREFIJO_DATA_VERSION = "motor-cartera/red-vial/v1:"
"""Lo que la preparacion pone en `osrm-extract --data_version`, antes de la huella de la red."""

Coordenada = tuple[str, str]
"""Longitud y latitud, como texto decimal con seis decimales: lo que se envia, sin pasar por un
float."""


def data_version(huella: str) -> str:
    """El `data_version` con que se prepara la red de esa huella, y que su backend declara."""
    return PREFIJO_DATA_VERSION + huella


class FallaProveedorRuteo(Exception):
    """El proveedor no respondio, o no respondio lo que debia: un fallo tecnico de la ejecucion.
    El mensaje no lleva coordenadas, URLs ni cuerpos."""

    def __init__(self, servicio: str, tipo: str, intentos: int) -> None:
        super().__init__(
            f"El proveedor de ruteo fallo en {servicio}: {tipo}, tras {intentos} intento(s)."
        )
        self.servicio = servicio
        self.tipo = tipo
        self.intentos = intentos


class BackendIncompatible(Exception):
    """El backend sirve otra red vial que la de la version pedida. Falla cerrado."""

    def __init__(self, esperada: str, declarada: str | None) -> None:
        super().__init__(
            f"El backend de ruteo sirve otra red vial: se esperaba la huella {esperada[:12]}... y "
            f"declara {'ninguna' if declarada is None else declarada[:60]}."
        )
        self.esperada = esperada
        self.declarada = declarada


@dataclass(frozen=True)
class MatrizVial:
    """Duraciones (s) y distancias (m) por calle, como decimales tal como las dio el proveedor:
    origen por destino. None es que no hay camino."""

    duracion: list[list[Decimal | None]]
    distancia: list[list[Decimal | None]]


@dataclass
class Metricas:
    """Lo que paso con el proveedor durante una sesion: cuantas peticiones de cada servicio, sus
    latencias, los reintentos y los bytes. No guarda ninguna coordenada."""

    peticiones: dict[str, int] = field(default_factory=dict)
    latencias_ms: dict[str, list[float]] = field(default_factory=dict)
    reintentos: int = 0
    bytes_enviados: int = 0
    bytes_recibidos: int = 0
    bloques: int = 0
    _cerrojo: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def registrar(self, servicio: str, ms: float, enviados: int, recibidos: int) -> None:
        with self._cerrojo:
            self.peticiones[servicio] = self.peticiones.get(servicio, 0) + 1
            self.latencias_ms.setdefault(servicio, []).append(ms)
            self.bytes_enviados += enviados
            self.bytes_recibidos += recibidos

    def reintento(self) -> None:
        with self._cerrojo:
            self.reintentos += 1

    def bloque(self) -> None:
        with self._cerrojo:
            self.bloques += 1


class SesionRuteo(Protocol):
    """Las preguntas a una red vial ya verificada."""

    metricas: Metricas

    def verificar(self) -> None:
        """Comprueba, sin enviar ningun punto de una cuenta ni de una base, que el backend sirve
        la red de la sesion. Levanta BackendIncompatible si sirve otra."""
        ...

    def snaps(self, coordenadas: Sequence[Coordenada]) -> list[Decimal | None]:
        """La distancia en metros de cada coordenada a su segmento de la red, en el mismo orden;
        None si el proveedor dice que no tiene uno."""
        ...

    def matriz(self, coordenadas: Sequence[Coordenada], *, bloque: int) -> MatrizVial:
        """Duraciones y distancias entre todas las coordenadas, en bloques de a lo mas `bloque` por
        `bloque`."""
        ...


class ProveedorRuteo(Protocol):
    def sesion(self, huella: str) -> SesionRuteo:
        """Una sesion atada a la red de esa huella. Su primera respuesta la verifica."""
        ...

    def salud(self) -> bool:
        """Si el backend responde. No dice que red sirve."""
        ...


# --- OSRM -----------------------------------------------------------------------------------------

CODIGOS_DE_CONFIGURACION = frozenset(
    {
        "InvalidUrl",
        "InvalidService",
        "InvalidVersion",
        "InvalidOptions",
        "InvalidQuery",
        "InvalidValue",
        "TooBig",
        "NotImplemented",
        "DisabledDataset",
    }
)
"""Respuestas de OSRM que dicen que la peticion o el servidor no son los que este cliente espera:
reintentar no cambia nada."""


class _Transitorio(Exception):
    def __init__(self, tipo: str) -> None:
        super().__init__(tipo)
        self.tipo = tipo


class _Definitivo(Exception):
    def __init__(self, tipo: str) -> None:
        super().__init__(tipo)
        self.tipo = tipo


@dataclass(frozen=True)
class ConfiguracionOsrm:
    url: str
    timeout_conexion_segundos: float = 3.0
    timeout_lectura_segundos: float = 30.0
    reintentos: int = 2
    backoff_segundos: float = 0.5
    lote_nearest: int = 100
    perfil_url: str = "driving"

    def __post_init__(self) -> None:
        partes = urlsplit(self.url)
        if partes.scheme not in ("http", "https") or not partes.hostname:
            raise ValueError("MC_OSRM_URL es una URL http(s) con su host.")
        if self.reintentos < 0 or self.lote_nearest < 1:
            raise ValueError("Los reintentos no son negativos y un lote tiene al menos un punto.")


CONEXION_CERRADA = (
    http.client.RemoteDisconnected,
    ConnectionResetError,
    ConnectionAbortedError,
    BrokenPipeError,
)
"""Como se ve una conexion persistente que el servidor ya cerro, al volver a usarla."""


class ClienteOsrm:
    """Un backend OSRM autoalojado, por HTTP. Una conexion persistente por hilo; las peticiones son
    POST con JSON (OSRM v26.9.0 o posterior), idempotentes, y se reintentan con espera solo ante un
    fallo transitorio."""

    def __init__(self, configuracion: ConfiguracionOsrm) -> None:
        self.configuracion = configuracion
        partes = urlsplit(configuracion.url)
        self._https = partes.scheme == "https"
        # ConfiguracionOsrm ya exige una URL http(s) con su host.
        self._host = presente(partes.hostname, "el host de MC_OSRM_URL")
        self._puerto = partes.port or (443 if self._https else 80)
        self._prefijo = partes.path.rstrip("/")
        self._local = threading.local()

    # El transporte: lo unico que sabe de sockets. Una prueba lo reemplaza para simular fallas.
    def _enviar(self, ruta: str, cuerpo: bytes | None, timeout_lectura: float) -> tuple[int, bytes]:
        """Envia la peticion por la conexion persistente de este hilo. Si esa conexion ya la habia
        cerrado OSRM (cierra las que pasan 5 s sin uso), la vuelve a abrir y envia otra vez, una
        sola: la peticion es idempotente y ni siquiera llego. Segun el sistema y el momento, esa
        conexion cerrada se ve como un desconectado, un reset, un aborto (en Windows, WinError
        10053) o un tubo roto."""
        reusada = getattr(self._local, "conexion", None) is not None
        try:
            return self._una_vez(ruta, cuerpo, timeout_lectura)
        except CONEXION_CERRADA:
            if not reusada:
                raise
            return self._una_vez(ruta, cuerpo, timeout_lectura)

    def _una_vez(
        self, ruta: str, cuerpo: bytes | None, timeout_lectura: float
    ) -> tuple[int, bytes]:
        conexion = getattr(self._local, "conexion", None)
        if conexion is None:
            clase = http.client.HTTPSConnection if self._https else http.client.HTTPConnection
            conexion = clase(
                self._host, self._puerto, timeout=self.configuracion.timeout_conexion_segundos
            )
            conexion.connect()
            conexion.sock.settimeout(timeout_lectura)
            self._local.conexion = conexion
        metodo = "GET" if cuerpo is None else "POST"
        encabezados = {"Content-Type": "application/json"} if cuerpo is not None else {}
        try:
            conexion.request(metodo, self._prefijo + ruta, body=cuerpo, headers=encabezados)
            respuesta = conexion.getresponse()
            return respuesta.status, respuesta.read()
        except BaseException:
            self._cerrar_conexion()
            raise

    def _cerrar_conexion(self) -> None:
        conexion = getattr(self._local, "conexion", None)
        self._local.conexion = None
        if conexion is not None:
            try:
                conexion.close()
            except OSError:  # pragma: no cover - cerrar un socket roto no importa
                pass

    def _pedir(
        self, servicio: str, cuerpo: bytes, metricas: Metricas, *, reintentos: int | None = None
    ) -> dict:
        """POST /{servicio}/v1/{perfil} con el cuerpo JSON; el JSON de la respuesta, con sus
        numeros como decimales. Un 400 de OSRM con su codigo vuelve como respuesta: quien llama
        decide. Levanta FallaProveedorRuteo, sin la causa original: su mensaje podria citar la
        peticion."""
        ruta = f"/{servicio}/v1/{self.configuracion.perfil_url}"
        intentos = 1 + (self.configuracion.reintentos if reintentos is None else reintentos)
        ultimo = "desconocido"
        for intento in range(1, intentos + 1):
            if intento > 1:
                metricas.reintento()
                time.sleep(self.configuracion.backoff_segundos * 2 ** (intento - 2))
            inicio = time.perf_counter()
            try:
                estado, contenido = self._enviar(
                    ruta, cuerpo, self.configuracion.timeout_lectura_segundos
                )
                respuesta = _leer_json(estado, contenido)
            except _Definitivo as exc:
                raise FallaProveedorRuteo(servicio, exc.tipo, intento) from None
            except _Transitorio as exc:
                ultimo = exc.tipo
            except (OSError, http.client.HTTPException) as exc:
                ultimo = type(exc).__name__
            else:
                ms = (time.perf_counter() - inicio) * 1000
                metricas.registrar(servicio, ms, len(cuerpo), len(contenido))
                log.debug(
                    "osrm %s: %s, %.1f ms, %s bytes, intento %s",
                    servicio,
                    estado,
                    ms,
                    len(contenido),
                    intento,
                )
                return respuesta
            log.warning(
                "osrm %s: fallo transitorio %s (intento %s de %s)",
                servicio,
                ultimo,
                intento,
                intentos,
            )
        raise FallaProveedorRuteo(servicio, ultimo, intentos) from None

    def _sonda(self) -> dict:
        """Un nearest de (0, 0): un punto que no es de nadie. Sin reintentos y con timeouts
        cortos."""
        estado, contenido = self._enviar(
            f"/nearest/v1/{self.configuracion.perfil_url}/0,0?number=1&generate_hints=false",
            None,
            min(self.configuracion.timeout_lectura_segundos, 3.0),
        )
        return _leer_json(estado, contenido)

    def salud(self) -> bool:
        """Si OSRM responde a la sonda, con Ok o con NoSegment: el servicio esta arriba. No envia
        ninguna coordenada de una cuenta ni de una base."""
        try:
            return isinstance(self._sonda().get("code"), str)
        except (_Definitivo, _Transitorio, OSError, http.client.HTTPException):
            return False

    def data_version(self) -> str | None:
        """La red que declara el backend en la sonda, o None si la sonda no la trae (con
        NoSegment). Levanta FallaProveedorRuteo si el backend no responde."""
        try:
            respuesta = self._sonda()
        except (_Definitivo, _Transitorio) as exc:
            raise FallaProveedorRuteo("nearest", exc.tipo, 1) from None
        except (OSError, http.client.HTTPException) as exc:
            raise FallaProveedorRuteo("nearest", type(exc).__name__, 1) from None
        if respuesta.get("code") != "Ok":
            return None
        declarada = respuesta.get("data_version")
        return declarada if isinstance(declarada, str) else None

    def sesion(self, huella: str) -> SesionOsrm:
        return SesionOsrm(self, huella)


def _leer_json(estado: int, contenido: bytes) -> dict:
    if estado >= 500 or estado == 429:
        raise _Transitorio(f"HTTP {estado}")
    try:
        respuesta = json.loads(contenido, parse_float=Decimal)
    except (ValueError, UnicodeDecodeError):
        raise _Transitorio("RespuestaNoJSON") from None
    if not isinstance(respuesta, dict) or not isinstance(respuesta.get("code"), str):
        raise _Transitorio("RespuestaSinCodigo")
    if estado != 200 and respuesta["code"] in CODIGOS_DE_CONFIGURACION:
        raise _Definitivo(f"OSRM {respuesta['code']}")
    if estado not in (200, 400):
        raise _Transitorio(f"HTTP {estado}")
    return respuesta


def _numero(texto: str) -> str:
    """Una coordenada decimal tal cual, validada: nunca un float ni un exponente."""
    numero = Decimal(texto)
    if not numero.is_finite():
        raise ValueError("Una coordenada es un numero finito.")
    return format(numero, "f")


def _cuerpo(coordenadas: Sequence[Coordenada], **opciones) -> bytes:
    """El JSON de una peticion, con las coordenadas escritas como sus decimales: sin pasar por un
    float, la peticion es la misma en cualquier maquina."""
    puntos = ",".join(f"[{_numero(lon)},{_numero(lat)}]" for lon, lat in coordenadas)
    resto = json.dumps(opciones, sort_keys=True, separators=(",", ":"))[1:-1]
    return ('{"coordinates":[' + puntos + "]" + ("," + resto if resto else "") + "}").encode()


class SesionOsrm:
    """Una sesion de OSRM atada a la red de una huella: cada respuesta Ok tiene que declarar ese
    `data_version`; si declara otro, o ninguno, BackendIncompatible."""

    def __init__(self, cliente: ClienteOsrm, huella: str) -> None:
        self._cliente = cliente
        self._esperado = data_version(huella)
        self._huella = huella
        self.metricas = Metricas()

    def _verificar(self, respuesta: dict) -> None:
        declarada = respuesta.get("data_version")
        if declarada != self._esperado:
            raise BackendIncompatible(self._huella, declarada)

    def verificar(self) -> None:
        """La sonda de (0, 0): si el backend declara otra red, falla cerrado antes de enviar un
        punto. Si la sonda no trae la red (NoSegment), la primera respuesta de un snap la
        verifica: cada respuesta se verifica de todos modos."""
        declarada = self._cliente.data_version()
        if declarada is not None and declarada != self._esperado:
            raise BackendIncompatible(self._huella, declarada)

    def snaps(self, coordenadas: Sequence[Coordenada]) -> list[Decimal | None]:
        """Por lotes del servicio nearest (number=1, sin hints), en orden. Un lote de un solo punto
        lo envia dos veces: asi la respuesta siempre es la de varios puntos, con su data_version, y
        un punto sin segmento no tumba la peticion."""
        resultado: list[Decimal | None] = []
        lote = self._cliente.configuracion.lote_nearest
        for inicio in range(0, len(coordenadas), lote):
            parte = list(coordenadas[inicio : inicio + lote])
            enviadas = parte if len(parte) > 1 else parte * 2
            respuesta = self._cliente._pedir(
                "nearest",
                _cuerpo(enviadas, number=1, generate_hints=False),
                self.metricas,
            )
            if respuesta["code"] != "Ok":
                raise FallaProveedorRuteo("nearest", f"OSRM {respuesta['code']}", 1)
            self._verificar(respuesta)
            puntos = respuesta.get("waypoints")
            if not isinstance(puntos, list) or len(puntos) != len(enviadas):
                raise FallaProveedorRuteo("nearest", "RespuestaIncompleta", 1)
            for punto in puntos[: len(parte)]:
                resultado.append(_distancia_de_snap(punto))
        return resultado

    def matriz(self, coordenadas: Sequence[Coordenada], *, bloque: int) -> MatrizVial:
        """La matriz completa, por bloques de a lo mas `bloque` origenes por `bloque` destinos, en
        orden: cada bloque es una peticion table con sus coordenadas (las de sus origenes y sus
        destinos), sin waypoints. OSRM no acepta una tabla de un solo punto: el bloque de la
        diagonal que queda con uno (101 puntos en bloques de 100) lo envia como origen y como
        destino, igual que cualquier bloque fuera de la diagonal."""
        n = len(coordenadas)
        duracion: list[list[Decimal | None]] = [[None] * n for _ in range(n)]
        distancia: list[list[Decimal | None]] = [[None] * n for _ in range(n)]
        trozos = [list(range(i, min(i + bloque, n))) for i in range(0, n, bloque)]
        for origenes in trozos:
            for destinos in trozos:
                opciones: dict = {
                    "annotations": ["duration", "distance"],
                    "skip_waypoints": True,
                    "generate_hints": False,
                }
                if origenes is destinos and len(origenes) > 1:
                    indices = origenes
                else:
                    indices = origenes + destinos
                    opciones["sources"] = list(range(len(origenes)))
                    opciones["destinations"] = list(range(len(origenes), len(indices)))
                respuesta = self._cliente._pedir(
                    "table", _cuerpo([coordenadas[i] for i in indices], **opciones), self.metricas
                )
                self.metricas.bloque()
                if respuesta["code"] != "Ok":
                    raise FallaProveedorRuteo("table", f"OSRM {respuesta['code']}", 1)
                self._verificar(respuesta)
                duraciones = _tabla(respuesta.get("durations"), len(origenes), len(destinos))
                distancias = _tabla(respuesta.get("distances"), len(origenes), len(destinos))
                for a, i in enumerate(origenes):
                    for b, j in enumerate(destinos):
                        d, m = duraciones[a][b], distancias[a][b]
                        if (d is None) != (m is None):
                            raise FallaProveedorRuteo("table", "CeldaIncoherente", 1)
                        duracion[i][j], distancia[i][j] = d, m
        return MatrizVial(duracion, distancia)


def _distancia_de_snap(punto) -> Decimal | None:
    if isinstance(punto, dict):
        if punto.get("code") == "NoSegment":
            return None
        raise FallaProveedorRuteo("nearest", "RespuestaIncompleta", 1)
    if not isinstance(punto, list) or not punto or not isinstance(punto[0], dict):
        raise FallaProveedorRuteo("nearest", "RespuestaIncompleta", 1)
    distancia = punto[0].get("distance")
    if isinstance(distancia, bool) or not isinstance(distancia, Decimal | int):
        raise FallaProveedorRuteo("nearest", "RespuestaIncompleta", 1)
    return Decimal(distancia)


def _tabla(filas, origenes: int, destinos: int) -> list[list[Decimal | None]]:
    if not isinstance(filas, list) or len(filas) != origenes:
        raise FallaProveedorRuteo("table", "RespuestaIncompleta", 1)
    tabla: list[list[Decimal | None]] = []
    for fila in filas:
        if not isinstance(fila, list) or len(fila) != destinos:
            raise FallaProveedorRuteo("table", "RespuestaIncompleta", 1)
        celdas: list[Decimal | None] = []
        for celda in fila:
            if celda is None:
                celdas.append(None)
            elif isinstance(celda, bool) or not isinstance(celda, Decimal | int):
                raise FallaProveedorRuteo("table", "RespuestaIncompleta", 1)
            else:
                celdas.append(Decimal(celda))
        tabla.append(celdas)
    return tabla


def proveedor_configurado(configuracion) -> ClienteOsrm | None:
    """El proveedor que declara la configuracion (MC_OSRM_URL), o None si no hay uno: sin el, este
    proceso no rutea."""
    if not configuracion.osrm_url:
        return None
    return ClienteOsrm(
        ConfiguracionOsrm(
            url=configuracion.osrm_url,
            timeout_conexion_segundos=configuracion.osrm_timeout_conexion_segundos,
            timeout_lectura_segundos=configuracion.osrm_timeout_lectura_segundos,
            reintentos=configuracion.osrm_reintentos,
            backoff_segundos=configuracion.osrm_backoff_segundos,
            lote_nearest=configuracion.osrm_lote_nearest,
        )
    )
