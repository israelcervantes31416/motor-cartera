"""Una ejecucion de ruteo/v2: la ruta por la red vial de cada gestor de un plan de campo/v2, en
PostgreSQL y todo o nada.

El orden:

1. `abrir` registra la ejecucion EN_PROCESO con su trabajo RUTEO_V2, en la transaccion de quien
   llama, despues de fijar exactamente sus entradas: un plan de campo/v2 EXITOSA, una version de la
   red vial PUBLICADA y la politica con su hash. Con ellas sale su `firma_inputs`. Nunca toma el
   ultimo plan ni la ultima red: se declaran.
2. Un worker toma el trabajo. `rutear` toma la ejecucion con su fila bloqueada y, en una
   transaccion:
   - selecciona por su indice las asignaciones ASIGNADA del plan (cada una con su gestor, su
     prioridad y el punto de su geografia publicada), la carga de cada gestor con sus ventanas y la
     base de cada uno: campo/v2 es la autoridad, y nada se vuelve a decidir;
   - pregunta al proveedor de ruteo, atado a la huella de la red, si el backend sirve esa red (la
     sonda de un punto que no es de nadie), y solo entonces le pide el snap de las bases y, de las
     bases enrutables, el de los puntos de sus cuentas, por lotes; cada respuesta se verifica;
   - gestor por gestor, en el orden de su codigo, pide su matriz por bloques (las de los siguientes
     se piden mientras se planea el actual, con un limite) y la planea con ruteo-public-v2; la
     matriz se suelta en cuanto la ruta esta planeada, y no se guarda: su hash si;
   - copia cada ruta y cada parada a tablas de paso, las publica con INSERT ... SELECT, las cuenta
     contra el plan de campo/v2 y cierra EXITOSA.
   Si algo falla, se revierte todo: nunca una ejecucion a medias. Un fallo del proveedor (sin
   conexion, timeouts, 5xx) es un fallo tecnico: la ejecucion sigue EN_PROCESO y la cola la
   reintenta; nunca se vuelve NO_ENRUTABLE ni una distancia en linea recta. Un backend que sirve
   otra red la deja FALLIDA (RED_VIAL_BACKEND_INCOMPATIBLE): falla cerrado.

Ninguna coordenada se guarda, se registra en la bitacora ni sale en un error: viven en memoria
mientras se consultan al proveedor, que es autoalojado.
"""

from __future__ import annotations

import logging
import math
import statistics
import time
from collections import deque
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session, col, select

from motor_cartera.config import config as config_del_entorno
from motor_cartera.db.copia import copiar
from motor_cartera.db.modelos import (
    CONTEOS_DE_PARADAS,
    CONTEOS_DE_RUTAS,
    EjecucionCampoV2,
    EjecucionRuteoV2,
    EstadoCampoV2,
    EstadoRuteoV2,
    EstadoVersionRedVial,
    ResultadoEjecucionRuteoV2,
    TipoTrabajo,
    VersionRedVial,
    ahora,
    id_de,
)
from motor_cartera.db.sesion import filas_afectadas, insertar_en_savepoint, restriccion, sesion
from motor_cartera.geografia.ejecuciones import _traza
from motor_cartera.ingesta.fuente_oficial import Cronometro
from motor_cartera.invariantes import presente
from motor_cartera.orquestacion import cola
from motor_cartera.ruteo_v2 import politica as p
from motor_cartera.ruteo_v2.politica import (
    POLITICAS,
    PUBLIC_V2,
    VERSION_MOTOR,
    EstadoParada,
    EstadoRuta,
    Matriz,
    ParadaEntrada,
    PoliticaRuteo,
    RutaPlaneada,
    firma_inputs,
    hash_politica,
)
from motor_cartera.ruteo_v2.proveedor import (
    BackendIncompatible,
    Coordenada,
    FallaProveedorRuteo,
    MatrizVial,
    ProveedorRuteo,
    SesionRuteo,
    proveedor_configurado,
)

log = logging.getLogger(__name__)

INDICE_EXITOSA = "ux_ruteo_v2_exitosa"
INDICE_EN_PROCESO = "ux_ruteo_v2_en_proceso"

COLUMNA_DEL_ESTADO_RUTA = {
    EstadoRuta.COMPLETA: "rutas_completas",
    EstadoRuta.PARCIAL: "rutas_parciales",
    EstadoRuta.SIN_PARADAS_ENRUTABLES: "rutas_sin_paradas",
    EstadoRuta.BASE_NO_ENRUTABLE: "bases_no_enrutables",
    EstadoRuta.SIN_JORNADA: "rutas_sin_jornada",
}
COLUMNA_DEL_ESTADO_PARADA = {
    EstadoParada.PROGRAMADA: "programadas",
    EstadoParada.SIN_TIEMPO: "sin_tiempo",
    EstadoParada.NO_ENRUTABLE: "no_enrutables",
    EstadoParada.NO_ENRUTABLE_BASE: "no_enrutables_base",
}
assert set(COLUMNA_DEL_ESTADO_RUTA.values()) == set(CONTEOS_DE_RUTAS)
assert set(COLUMNA_DEL_ESTADO_PARADA.values()) == set(CONTEOS_DE_PARADAS)


# --- errores al abrir -----------------------------------------------------------------------------


class EntradaNoRuteable(Exception):
    """Una peticion de ruteo/v2 que no se puede abrir: no registra nada. `codigo` es estable."""

    codigo = "ENTRADA_NO_RUTEABLE"
    estado_http = 422


class CampoNoEncontrado(EntradaNoRuteable):
    codigo = "CAMPO_V2_NO_ENCONTRADO"
    estado_http = 404


class CampoNoExitoso(EntradaNoRuteable):
    codigo = "CAMPO_V2_NO_EXITOSO"


class RedNoEncontrada(EntradaNoRuteable):
    codigo = "RED_VIAL_NO_ENCONTRADA"
    estado_http = 404


class RedNoPublicada(EntradaNoRuteable):
    codigo = "RED_VIAL_NO_PUBLICADA"


class PoliticaNoSoportada(EntradaNoRuteable):
    codigo = "POLITICA_NO_SOPORTADA"


class RuteoV2YaGenerado(Exception):
    """Otra ejecucion ya publico exactamente las mismas entradas (la misma firma)."""

    def __init__(self, previo: EjecucionRuteoV2) -> None:
        super().__init__(
            f"Esas entradas ya se rutearon: la ejecucion {previo.ruteo_run_id} (firma "
            f"{previo.firma_inputs[:12]}...)."
        )
        self.previo = previo


class RuteoV2EnProceso(Exception):
    """Esas mismas entradas ya se estan ruteando: hay una ejecucion EN_PROCESO."""

    def __init__(self, activa: EjecucionRuteoV2) -> None:
        super().__init__(f"Esas entradas ya se estan ruteando: la ejecucion {activa.ruteo_run_id}.")
        self.activa = activa


class SinProveedorDeRuteo(Exception):
    """Este proceso no tiene un proveedor de ruteo (MC_OSRM_URL): no rutea. La ejecucion sigue
    EN_PROCESO, y la toma un worker que si tenga uno."""


class _NoSePublica(Exception):
    def __init__(self, resultado: ResultadoEjecucionRuteoV2, mensaje: str) -> None:
        super().__init__(mensaje)
        self.resultado = resultado


# --- abrir ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Entradas:
    campo: EjecucionCampoV2
    red: VersionRedVial
    politica: PoliticaRuteo
    firma: str


def _una(s: Session, modelo, columna, valor):
    return s.exec(select(modelo).where(columna == valor)).one_or_none()


def entradas(
    s: Session,
    campo_run_id: UUID,
    red_vial_version_id: UUID,
    version_politica: str | None = None,
) -> Entradas:
    """Las entradas de una ejecucion, comprobadas, con su firma. Levanta, sin registrar nada,
    CampoNoEncontrado, CampoNoExitoso, RedNoEncontrada, RedNoPublicada o PoliticaNoSoportada."""
    campo = _una(s, EjecucionCampoV2, EjecucionCampoV2.campo_run_id, campo_run_id)
    if campo is None:
        raise CampoNoEncontrado(f"No existe un plan de campo/v2 con campo_run_id {campo_run_id}.")
    if campo.estado != EstadoCampoV2.EXITOSA:
        raise CampoNoExitoso(
            f"El plan de campo/v2 {campo_run_id} esta {campo.estado}: ruteo/v2 solo rutea un plan "
            "EXITOSA, nunca uno parcial o fallido."
        )
    red = _una(s, VersionRedVial, VersionRedVial.red_vial_version_id, red_vial_version_id)
    if red is None:
        raise RedNoEncontrada(
            f"No existe una version de la red vial con red_vial_version_id {red_vial_version_id}."
        )
    if red.estado != EstadoVersionRedVial.PUBLICADA:
        raise RedNoPublicada(
            f"La red vial {red_vial_version_id} esta {red.estado}: solo se rutea sobre una red "
            "PUBLICADA, con su manifiesto verificado."
        )
    version_politica = version_politica or PUBLIC_V2.version
    politica = POLITICAS.get(version_politica)
    if politica is None:
        raise PoliticaNoSoportada(
            f"Este servicio rutea con {', '.join(sorted(POLITICAS))}, no con {version_politica}."
        )
    firma = firma_inputs(
        campo_run_id=campo.campo_run_id,
        firma_campo=campo.firma_inputs,
        red_vial_version_id=red.red_vial_version_id,
        huella_red=red.huella,
        hash_manifest=red.hash_manifest,
        version_motor_ruteo=red.version_motor_ruteo,
        hash_perfil=red.hash_perfil,
        politica=politica,
    )
    return Entradas(campo, red, politica, firma)


def abrir(
    s: Session,
    campo_run_id: UUID,
    red_vial_version_id: UUID,
    *,
    version_politica: str | None = None,
    max_intentos: int,
    tomado_por: str | None = None,
    lease_segundos: float | None = None,
) -> EjecucionRuteoV2:
    """La ejecucion EN_PROCESO de ruteo/v2, con su trabajo RUTEO_V2, en la transaccion de quien
    llama: envia los INSERT y no confirma. Levanta, sin registrar nada, lo que levanta `entradas`,
    RuteoV2YaGenerado y RuteoV2EnProceso."""
    e = entradas(s, campo_run_id, red_vial_version_id, version_politica)
    previo = _de_firma(s, e.firma, EstadoRuteoV2.EXITOSA)
    if previo is not None:
        raise RuteoV2YaGenerado(previo)
    activa = _de_firma(s, e.firma, EstadoRuteoV2.EN_PROCESO)
    if activa is not None:
        raise RuteoV2EnProceso(activa)
    ejecucion = EjecucionRuteoV2(
        ejecucion_campo_v2_id=id_de(e.campo),
        version_red_vial_id=id_de(e.red),
        fecha_operacion=e.campo.fecha_operacion,
        version_motor=VERSION_MOTOR,
        version_politica=e.politica.version,
        hash_politica=hash_politica(e.politica),
        firma_inputs=e.firma,
    )
    error = insertar_en_savepoint(s, ejecucion)
    if error is not None:
        if restriccion(error) != INDICE_EN_PROCESO:
            raise error
        activa = _de_firma(s, e.firma, EstadoRuteoV2.EN_PROCESO)
        if (
            activa is None
        ):  # pragma: no cover - la que gano ya termino entre el rechazo y la revision
            raise error
        raise RuteoV2EnProceso(activa) from error
    cola.crear(
        s,
        TipoTrabajo.RUTEO_V2,
        id_de(ejecucion),
        max_intentos=max_intentos,
        tomado_por=tomado_por,
        lease_segundos=lease_segundos,
    )
    log.info(
        "ruteo v2 %s abierto: plan de campo %s, red vial %s, fecha %s",
        ejecucion.ruteo_run_id,
        e.campo.campo_run_id,
        e.red.red_vial_version_id,
        e.campo.fecha_operacion,
    )
    return ejecucion


def _de_firma(s: Session, firma: str, estado: EstadoRuteoV2) -> EjecucionRuteoV2 | None:
    consulta = select(EjecucionRuteoV2).where(
        EjecucionRuteoV2.firma_inputs == firma, EjecucionRuteoV2.estado == estado
    )
    if estado == EstadoRuteoV2.EN_PROCESO:
        consulta = consulta.with_for_update()
    return s.exec(consulta).one_or_none()


# --- rutear ---------------------------------------------------------------------------------------

ANTES_DEL_SNAP = "ANTES_DEL_SNAP"
DESPUES_DEL_SNAP = "DESPUES_DEL_SNAP"
DESPUES_DE_MATRICES = "DESPUES_DE_MATRICES"
DESPUES_DE_PLANIFICAR = "DESPUES_DE_PLANIFICAR"
DESPUES_DEL_STAGING = "DESPUES_DEL_STAGING"
DESPUES_DE_PARADAS = "DESPUES_DE_PARADAS"
ANTES_DE_CERRAR = "ANTES_DE_CERRAR"
PUNTOS_DE_FALLA = (
    ANTES_DEL_SNAP,
    DESPUES_DEL_SNAP,
    DESPUES_DE_MATRICES,
    DESPUES_DE_PLANIFICAR,
    DESPUES_DEL_STAGING,
    DESPUES_DE_PARADAS,
    ANTES_DE_CERRAR,
)


def _punto_de_falla(nombre: str) -> None:
    """Un punto donde una prueba puede inyectar una falla. En produccion no hace nada."""


def ejecutar_ruteo_v2(ejecucion_id: int) -> None:
    """Lo que ejecuta un trabajo RUTEO_V2, con el proveedor de la configuracion del proceso. Si la
    ejecucion ya termino no hace nada."""
    with sesion() as s:
        ejecucion = s.get_one(EjecucionRuteoV2, ejecucion_id)
        if ejecucion.estado != EstadoRuteoV2.EN_PROCESO:
            log.info("ruteo v2 %s ya termino %s", ejecucion.ruteo_run_id, ejecucion.estado)
            return
    proveedor = proveedor_configurado(config_del_entorno)
    if proveedor is None:
        raise SinProveedorDeRuteo(
            "Este proceso no tiene proveedor de ruteo (MC_OSRM_URL): la ejecucion sigue EN_PROCESO."
        )
    rutear(ejecucion_id, proveedor=proveedor, concurrencia=config_del_entorno.osrm_concurrencia)


@dataclass
class Medicion:
    """Lo que midio una ejecucion para quien la observa (el benchmark, el CLI): las fases y el
    proveedor. No se guarda: los tiempos no son parte del resultado."""

    cronometro: Cronometro = field(default_factory=Cronometro)
    peticiones: dict[str, int] = field(default_factory=dict)
    latencias_ms: dict[str, list[float]] = field(default_factory=dict)
    reintentos: int = 0
    bytes_enviados: int = 0
    bytes_recibidos: int = 0


def rutear(
    ejecucion_id: int,
    *,
    proveedor: ProveedorRuteo,
    concurrencia: int = 4,
    medicion: Medicion | None = None,
) -> None:
    """Rutea los gestores del plan y deja la ejecucion en un estado terminal: con su fila bloqueada
    hasta el commit, todo en una transaccion y en un savepoint; solo publica o falla el dueno
    vigente de su trabajo. Levanta RuteoV2YaGenerado si otra ejecucion publico primero la misma
    firma, TrabajoAjeno, y los fallos transitorios (de la base o del proveedor de ruteo), con la
    ejecucion EN_PROCESO para que la cola la reintente."""
    medicion = medicion or Medicion()
    with sesion() as s:
        ejecucion = s.exec(
            select(EjecucionRuteoV2).where(EjecucionRuteoV2.id == ejecucion_id).with_for_update()
        ).one()
        if ejecucion.estado != EstadoRuteoV2.EN_PROCESO:
            log.warning("ruteo v2 %s ya termino %s", ejecucion.ruteo_run_id, ejecucion.estado)
            return
        etiqueta, firma = ejecucion.ruteo_run_id, ejecucion.firma_inputs
        try:
            with s.begin_nested():
                _rutear(s, ejecucion, proveedor, concurrencia, medicion)
        except _NoSePublica as exc:
            resultado, motivo = exc.resultado, str(exc)
        except BackendIncompatible as exc:
            resultado, motivo = (
                ResultadoEjecucionRuteoV2.RED_VIAL_BACKEND_INCOMPATIBLE,
                f"{exc} No se envio el punto de ninguna cuenta y no se publico nada.",
            )
        except IntegrityError as exc:
            if restriccion(exc) == INDICE_EXITOSA:
                resultado, motivo = (
                    ResultadoEjecucionRuteoV2.YA_GENERADO,
                    "Otra ejecucion publico la misma firma de entrada mientras esta se procesaba; "
                    "no se publica dos veces.",
                )
            else:
                log.error(
                    "ruteo v2 %s: violacion de integridad inesperada%s", etiqueta, _traza(exc)
                )
                resultado, motivo = (
                    ResultadoEjecucionRuteoV2.ERROR_INTERNO,
                    "Error interno al publicar; ver la bitacora del servicio.",
                )
        except OperationalError:
            log.warning("ruteo v2 %s: error transitorio de la base; sigue EN_PROCESO", etiqueta)
            raise
        except FallaProveedorRuteo as exc:
            # Un fallo tecnico, no un resultado de negocio: no se publica nada, y la cola reintenta.
            log.warning("ruteo v2 %s: %s Sigue EN_PROCESO.", etiqueta, exc)
            raise
        except Exception as exc:
            # Sin el mensaje: el de un error de la base puede citar una fila, y el de uno del
            # transporte, una peticion. El tipo, el SQLSTATE y los marcos bastan.
            log.error("ruteo v2 %s: error inesperado%s", etiqueta, _traza(exc))
            resultado, motivo = (
                ResultadoEjecucionRuteoV2.ERROR_INTERNO,
                f"Error interno ({type(exc).__name__}); ver la bitacora.",
            )
        else:
            cola.confirmar_dueno(s)
            with medicion.cronometro.fase("commit"):
                s.commit()
            return
        _fallar(s, ejecucion_id, etiqueta, resultado, motivo)
        if resultado == ResultadoEjecucionRuteoV2.YA_GENERADO:
            raise RuteoV2YaGenerado(
                presente(_de_firma(s, firma, EstadoRuteoV2.EXITOSA), "el ruteo que genero primero")
            )


@dataclass(frozen=True)
class _Gestor:
    gestor_campo_id: int
    gestor_id: UUID
    codigo: str
    base_operativa_id: int
    ventanas: tuple[p.Ventana, ...]
    asignadas: int


@dataclass(frozen=True)
class _Parada:
    cuenta_canonica_id: int
    cuenta_id: UUID
    prioridad: str
    coordenada: Coordenada


def _rutear(
    s: Session,
    ejecucion: EjecucionRuteoV2,
    proveedor: ProveedorRuteo,
    concurrencia: int,
    medicion: Medicion,
) -> None:
    cronometro = medicion.cronometro
    politica = POLITICAS.get(ejecucion.version_politica)
    if (
        ejecucion.version_motor != VERSION_MOTOR
        or politica is None
        or ejecucion.hash_politica != hash_politica(politica)
    ):
        raise _NoSePublica(
            ResultadoEjecucionRuteoV2.VERSION_NO_SOPORTADA,
            f"La ejecucion pide {ejecucion.version_motor} con {ejecucion.version_politica} (hash "
            f"{ejecucion.hash_politica[:12]}...), y este servicio rutea con {VERSION_MOTOR} y "
            f"{', '.join(sorted(POLITICAS))}; no se publico nada.",
        )
    campo = s.get_one(EjecucionCampoV2, ejecucion.ejecucion_campo_v2_id)
    red = s.get_one(VersionRedVial, ejecucion.version_red_vial_id)
    if campo.estado != EstadoCampoV2.EXITOSA or red.estado != EstadoVersionRedVial.PUBLICADA:
        raise _NoSePublica(
            ResultadoEjecucionRuteoV2.ENTRADAS_NO_EXITOSAS,
            f"El plan de campo/v2 esta {campo.estado} y la red vial {red.estado}; no se publico "
            "nada.",
        )
    parametros = {"ejecucion": ejecucion.id, "campo": campo.id}
    with cronometro.fase("seleccion"):
        gestores, paradas, bases = _seleccionar(s, parametros)
    if sum(len(v) for v in paradas.values()) != campo.asignadas:
        raise _NoSePublica(
            ResultadoEjecucionRuteoV2.DATOS_INCONSISTENTES,
            f"El plan de campo/v2 tiene {campo.asignadas:,} asignadas y se seleccionaron "
            f"{sum(len(v) for v in paradas.values()):,} con su punto; no se publico nada.",
        )
    _punto_de_falla(ANTES_DEL_SNAP)
    sesion_red = proveedor.sesion(red.huella)
    sesion_red.verificar()
    with cronometro.fase("snap"):
        con_jornada = [g for g in gestores if g.ventanas]
        # Las bases primero: su respuesta verifica la red antes de enviar el punto de una cuenta.
        # Despues, solo los puntos de las cuentas de un gestor con base enrutable.
        snaps = _snaps(sesion_red, sorted({bases[g.base_operativa_id] for g in con_jornada}))
        con_base = [
            g
            for g in con_jornada
            if p.motivo_de_snap(snaps[bases[g.base_operativa_id]], politica) is None
        ]
        coordenadas = sorted(
            {pa.coordenada for g in con_base for pa in paradas[g.gestor_campo_id]} - set(snaps)
        )
        snaps.update(_snaps(sesion_red, coordenadas, concurrencia))
    _punto_de_falla(DESPUES_DEL_SNAP)
    _ejecutar(s, SQL_PASO)
    tamanos: list[int] = []
    mejoras = rutas_mejoradas = 0
    rutas_de_paso: list[tuple] = []
    paradas_de_paso: list[tuple] = []

    def planear(g: _Gestor, matriz: Matriz | None, nodos: dict[UUID, int]) -> None:
        nonlocal mejoras, rutas_de_paso, paradas_de_paso, rutas_mejoradas
        propias = paradas[g.gestor_campo_id]
        entrada = [
            ParadaEntrada(
                pa.cuenta_id,
                pa.prioridad,
                snaps.get(pa.coordenada) if g.ventanas else None,
                nodos.get(pa.cuenta_id),
            )
            for pa in propias
        ]
        ruta = p.planear(
            entrada,
            g.ventanas,
            base_snap_cm=snaps.get(bases[g.base_operativa_id]) if g.ventanas else None,
            matriz=matriz,
            politica=politica,
            medidor=cronometro,
        )
        _punto_de_falla(DESPUES_DE_PLANIFICAR)
        mejoras += ruta.mejoras_2opt
        rutas_mejoradas += ruta.mejoras_2opt > 0
        rutas_de_paso.append(_fila_ruta(ejecucion, g, ruta))
        ids = {pa.cuenta_id: pa.cuenta_canonica_id for pa in propias}
        paradas_de_paso.extend(_fila_parada(g, ids, pp) for pp in ruta.paradas)
        if len(rutas_de_paso) >= 500:
            _vaciar(s, rutas_de_paso, paradas_de_paso, cronometro)
            rutas_de_paso, paradas_de_paso = [], []

    for g, matriz, nodos in _con_matrices(
        gestores, paradas, bases, snaps, sesion_red, politica, concurrencia, cronometro
    ):
        if matriz is not None:
            tamanos.append(matriz.puntos)
            _punto_de_falla(DESPUES_DE_MATRICES)
        planear(g, matriz, nodos)
    _vaciar(s, rutas_de_paso, paradas_de_paso, cronometro)
    _punto_de_falla(DESPUES_DEL_STAGING)
    with cronometro.fase("publicacion"):
        rutas = filas_afectadas(s.execute(text(SQL_RUTAS), parametros))
        publicadas = filas_afectadas(s.execute(text(SQL_PARADAS), parametros))
    _punto_de_falla(DESPUES_DE_PARADAS)
    with cronometro.fase("conteos"):
        ajenas = s.execute(text(SQL_PARADAS_AJENAS), parametros).scalar_one()
        conteos, distribucion = _contar(s, ejecucion)
    _medir(medicion, sesion_red)
    if (
        rutas != len(gestores)
        or publicadas != campo.asignadas
        or ajenas
        or conteos["paradas_objetivo"] != campo.asignadas
    ):
        raise _NoSePublica(
            ResultadoEjecucionRuteoV2.DATOS_INCONSISTENTES,
            f"El plan de campo/v2 tiene {campo.asignadas:,} asignadas de {len(gestores):,} "
            f"gestores y se publicaron {publicadas:,} paradas en {rutas:,} rutas ({ajenas:,} con "
            "otro gestor "
            "que el de su asignacion); no se publico ninguna.",
        )
    distribucion["matrices"] = _resumen_matrices(tamanos, sesion_red)
    distribucion["snap"] = {
        "puntos_consultados": len(snaps),
        "sin_snap": sum(1 for v in snaps.values() if v is None),
        "excesivos": sum(1 for v in snaps.values() if v is not None and v > politica.max_snap_cm),
    }
    distribucion["2opt"] = {"mejoras": mejoras, "rutas_mejoradas": rutas_mejoradas}
    _punto_de_falla(ANTES_DE_CERRAR)
    _cerrar(s, ejecucion, politica, conteos, distribucion)


def _ejecutar(s: Session, sentencias: tuple[str, ...]) -> None:
    for sentencia in sentencias:
        s.execute(text(sentencia))


# --- la seleccion ---------------------------------------------------------------------------------

SQL_GESTORES = (
    # Los gestores con al menos una cuenta ASIGNADA: su carga en el plan, con sus ventanas y su
    # base. Por la llave de la carga.
    "SELECT c.gestor_campo_id, g.gestor_id, g.codigo, c.base_operativa_id, c.ventanas, "
    "c.asignadas FROM carga_gestor_campo c JOIN gestor_campo g ON g.id = c.gestor_campo_id "
    "WHERE c.ejecucion_campo_v2_id = :campo AND c.asignadas > 0 ORDER BY g.codigo"
)
SQL_ASIGNADAS = (
    # Las asignaciones ASIGNADA del plan, por (plan, estado, cuenta), con el punto de su geografia
    # publicada (la de la territorial/v2 del plan), a seis decimales: lo que se le pregunta a la
    # red.
    "SELECT a.gestor_campo_id, a.cuenta_canonica_id, c.cuenta_id, a.prioridad, "
    "CAST(round(CAST(ST_X(r.punto) AS numeric), 6) AS text), "
    "CAST(round(CAST(ST_Y(r.punto) AS numeric), 6) AS text) "
    "FROM asignacion_campo_cuenta a JOIN cuenta_canonica c ON c.id = a.cuenta_canonica_id "
    "JOIN asignacion_territorial_v2 t ON t.ejecucion_territorial_v2_id = "
    "a.ejecucion_territorial_v2_id AND t.cuenta_canonica_id = a.cuenta_canonica_id "
    "JOIN resultado_geografia_cuenta r ON r.ejecucion_geografia_id = t.ejecucion_geografia_id "
    "AND r.cuenta_canonica_id = a.cuenta_canonica_id "
    "WHERE a.ejecucion_campo_v2_id = :campo AND a.estado = 'ASIGNADA' AND r.punto IS NOT NULL"
)
SQL_BASES = (
    "SELECT b.id, CAST(round(CAST(ST_X(b.punto) AS numeric), 6) AS text), "
    "CAST(round(CAST(ST_Y(b.punto) AS numeric), 6) AS text) FROM base_operativa b "
    "WHERE b.id IN (SELECT DISTINCT base_operativa_id FROM carga_gestor_campo "
    "WHERE ejecucion_campo_v2_id = :campo AND asignadas > 0)"
)


def _instante(valor: str) -> int:
    momento = datetime.fromisoformat(valor)
    if momento.tzinfo is None:
        raise ValueError("Un instante de una ventana lleva su zona.")
    return int(momento.timestamp())


def _seleccionar(
    s: Session, parametros: dict
) -> tuple[list[_Gestor], dict[int, list[_Parada]], dict[int, Coordenada]]:
    """Los gestores en el orden de su codigo, las paradas de cada uno en el orden de su cuenta_id y
    el punto de cada base. Tres consultas, por conjuntos: ninguna por gestor ni por cuenta."""
    gestores = [
        _Gestor(
            gestor,
            gestor_id,
            codigo,
            base,
            p.normalizar_ventanas(
                (_instante(v["inicio_en"]), _instante(v["fin_en"])) for v in ventanas
            ),
            asignadas,
        )
        for gestor, gestor_id, codigo, base, ventanas, asignadas in s.execute(
            text(SQL_GESTORES), parametros
        ).all()
    ]
    paradas: dict[int, list[_Parada]] = {g.gestor_campo_id: [] for g in gestores}
    for gestor, cuenta, cuenta_id, prioridad, lon, lat in s.execute(
        text(SQL_ASIGNADAS), parametros
    ):
        if gestor not in paradas:
            raise _NoSePublica(
                ResultadoEjecucionRuteoV2.DATOS_INCONSISTENTES,
                "Una asignacion ASIGNADA es de un gestor sin carga en el plan; no se publico nada.",
            )
        paradas[gestor].append(_Parada(cuenta, cuenta_id, prioridad, (lon, lat)))
    for g in gestores:
        paradas[g.gestor_campo_id].sort(key=lambda pa: pa.cuenta_id)
        if len(paradas[g.gestor_campo_id]) != g.asignadas:
            raise _NoSePublica(
                ResultadoEjecucionRuteoV2.DATOS_INCONSISTENTES,
                f"La carga del gestor {g.codigo} dice {g.asignadas:,} asignadas y tiene "
                f"{len(paradas[g.gestor_campo_id]):,} con su punto; no se publico nada.",
            )
    bases = {base: (lon, lat) for base, lon, lat in s.execute(text(SQL_BASES), parametros)}
    return gestores, paradas, bases


# --- el proveedor ---------------------------------------------------------------------------------


def _snaps(
    sesion_red: SesionRuteo, coordenadas: list[Coordenada], concurrencia: int = 1
) -> dict[Coordenada, int | None]:
    """El snap de cada coordenada, en centimetros (None: sin snap). Con concurrencia, por trozos en
    paralelo; el resultado no depende de como se partio."""
    if not coordenadas:
        return {}
    trozo = max(1, math.ceil(len(coordenadas) / max(1, concurrencia)))
    partes = [coordenadas[i : i + trozo] for i in range(0, len(coordenadas), trozo)]
    if len(partes) == 1:
        distancias = sesion_red.snaps(partes[0])
    else:
        with ThreadPoolExecutor(max_workers=len(partes), thread_name_prefix="snap") as pool:
            distancias = [d for parte in pool.map(sesion_red.snaps, partes) for d in parte]
    return {
        c: None if d is None else p.centimetros(d)
        for c, d in zip(coordenadas, distancias, strict=True)
    }


def _entera(matriz: MatrizVial) -> Matriz:
    """La matriz del proveedor con duraciones y distancias enteras, con la regla de la politica."""
    return Matriz(
        [[None if v is None else p.segundos(v) for v in fila] for fila in matriz.duracion],
        [[None if v is None else p.metros(v) for v in fila] for fila in matriz.distancia],
    )


def _con_matrices(
    gestores: list[_Gestor],
    paradas: dict[int, list[_Parada]],
    bases: dict[int, Coordenada],
    snaps: dict[Coordenada, int | None],
    sesion_red: SesionRuteo,
    politica: PoliticaRuteo,
    concurrencia: int,
    cronometro: Cronometro,
) -> Iterator[tuple[_Gestor, Matriz | None, dict[UUID, int]]]:
    """Cada gestor, en orden, con su matriz (o None si no la necesita: sin jornada, sin base
    enrutable o sin paradas con snap) y el punto de cada cuenta en ella. Las matrices de los
    siguientes gestores se piden mientras se planea el actual, hasta `concurrencia` a la vez: una
    matriz vive desde que se pide hasta que su ruta queda planeada."""

    def preparar(g: _Gestor) -> tuple[list[Coordenada] | None, dict[UUID, int]]:
        base = bases[g.base_operativa_id]
        if not g.ventanas or p.motivo_de_snap(snaps.get(base), politica) is not None:
            return None, {}
        propias = paradas[g.gestor_campo_id]
        puntos, nodos = p.puntos_de_matriz(
            {pa.cuenta_id: pa.coordenada for pa in propias},
            {pa.cuenta_id: snaps.get(pa.coordenada) for pa in propias},
            politica,
        )
        if not puntos:
            return None, nodos
        return [base, *puntos], nodos

    with ThreadPoolExecutor(max_workers=concurrencia, thread_name_prefix="matriz") as pool:
        en_vuelo: deque[tuple[_Gestor, Future | None, dict[UUID, int]]] = deque()
        try:
            for g in gestores:
                puntos, nodos = preparar(g)
                futuro = (
                    None
                    if puntos is None
                    else pool.submit(sesion_red.matriz, puntos, bloque=politica.bloque_matriz)
                )
                en_vuelo.append((g, futuro, nodos))
                while len(en_vuelo) > concurrencia:
                    yield _resolver(en_vuelo.popleft(), cronometro)
            while en_vuelo:
                yield _resolver(en_vuelo.popleft(), cronometro)
        finally:
            for _, futuro, _ in en_vuelo:
                if futuro is not None:
                    futuro.cancel()


def _resolver(
    pendiente: tuple[_Gestor, Future | None, dict[UUID, int]], cronometro: Cronometro
) -> tuple[_Gestor, Matriz | None, dict[UUID, int]]:
    g, futuro, nodos = pendiente
    if futuro is None:
        return g, None, nodos
    with cronometro.fase("matrices"):
        cruda = futuro.result()
    return g, _entera(cruda), nodos


def _medir(medicion: Medicion, sesion_red: SesionRuteo) -> None:
    metricas = sesion_red.metricas
    for servicio, cuantas in metricas.peticiones.items():
        medicion.peticiones[servicio] = medicion.peticiones.get(servicio, 0) + cuantas
    for servicio, latencias in metricas.latencias_ms.items():
        medicion.latencias_ms.setdefault(servicio, []).extend(latencias)
    medicion.reintentos += metricas.reintentos
    medicion.bytes_enviados += metricas.bytes_enviados
    medicion.bytes_recibidos += metricas.bytes_recibidos


def _resumen_matrices(tamanos: list[int], sesion_red: SesionRuteo) -> dict[str, Any]:
    """Cuantas matrices, de cuantos puntos (minimo, media, mediana, p95, maximo) y en cuantas
    peticiones: lo que cuesta la red, sin una sola celda."""
    if not tamanos:
        return {"matrices": 0, "bloques": 0, "celdas": 0}
    orden = sorted(tamanos)
    return {
        "matrices": len(orden),
        "bloques": sesion_red.metricas.bloques,
        "celdas": sum(t * t for t in orden),
        "puntos": {
            "minimo": orden[0],
            "media": round(statistics.fmean(orden), 2),
            "mediana": statistics.median(orden),
            "p95": orden[max(1, math.ceil(0.95 * len(orden))) - 1],
            "maximo": orden[-1],
        },
    }


# --- el paso y la publicacion ---------------------------------------------------------------------

SQL_PASO = (
    "CREATE TEMP TABLE rv2_ruta (gestor integer PRIMARY KEY, ruta_id uuid NOT NULL, "
    "base integer NOT NULL, estado_ruta varchar(24) NOT NULL, motivo_base varchar(24), "
    "paradas_campo integer NOT NULL, programadas integer NOT NULL, sin_tiempo integer NOT NULL, "
    "no_enrutables integer NOT NULL, no_enrutables_base integer NOT NULL, "
    "base_snap numeric(12, 2), jornada_inicio timestamptz, jornada_fin timestamptz, "
    "salida_base timestamptz, salida_regreso timestamptz, regreso_base timestamptz, "
    "distancia_regreso bigint, duracion_regreso integer, espera_regreso integer, "
    "distancia_total bigint NOT NULL, traslado integer NOT NULL, servicio integer NOT NULL, "
    "espera integer NOT NULL, total integer NOT NULL, mejoras integer NOT NULL, "
    "hash_matriz varchar(64), hash_secuencia varchar(64) NOT NULL) ON COMMIT DROP",
    "CREATE TEMP TABLE rv2_parada (cuenta integer PRIMARY KEY, gestor integer NOT NULL, "
    "prioridad varchar(12) NOT NULL, estado varchar(20) NOT NULL, motivo varchar(40), "
    "orden integer, snap numeric(12, 2), salida_anterior timestamptz, llegada timestamptz, "
    "inicio timestamptz, fin timestamptz, distancia bigint, duracion integer, espera integer) "
    "ON COMMIT DROP",
)
"""Lo que vuelve de Python, sin coordenadas: las rutas y las paradas, antes de publicarlas."""

COLUMNAS_RUTA = (
    "gestor",
    "ruta_id",
    "base",
    "estado_ruta",
    "motivo_base",
    "paradas_campo",
    "programadas",
    "sin_tiempo",
    "no_enrutables",
    "no_enrutables_base",
    "base_snap",
    "jornada_inicio",
    "jornada_fin",
    "salida_base",
    "salida_regreso",
    "regreso_base",
    "distancia_regreso",
    "duracion_regreso",
    "espera_regreso",
    "distancia_total",
    "traslado",
    "servicio",
    "espera",
    "total",
    "mejoras",
    "hash_matriz",
    "hash_secuencia",
)
COLUMNAS_PARADA = (
    "cuenta",
    "gestor",
    "prioridad",
    "estado",
    "motivo",
    "orden",
    "snap",
    "salida_anterior",
    "llegada",
    "inicio",
    "fin",
    "distancia",
    "duracion",
    "espera",
)

SQL_RUTAS = (
    "INSERT INTO ruta_gestor_v2 (ejecucion_ruteo_v2_id, gestor_campo_id, ruta_id, "
    "ejecucion_campo_v2_id, base_operativa_id, fecha_operacion, estado_ruta, motivo_base, "
    "paradas_campo, programadas, sin_tiempo, no_enrutables, no_enrutables_base, "
    "base_snap_distancia_m, jornada_inicio_at, jornada_fin_at, salida_base_at, salida_regreso_at, "
    "regreso_base_at, distancia_regreso_m, duracion_regreso_s, espera_regreso_s, "
    "distancia_total_m, duracion_traslado_s, duracion_servicio_s, duracion_espera_s, "
    "duracion_total_s, mejoras_2opt, hash_matriz, hash_secuencia, creada_en) "
    "SELECT :ejecucion, r.gestor, r.ruta_id, :campo, r.base, e.fecha_operacion, r.estado_ruta, "
    "r.motivo_base, r.paradas_campo, r.programadas, r.sin_tiempo, r.no_enrutables, "
    "r.no_enrutables_base, r.base_snap, r.jornada_inicio, r.jornada_fin, r.salida_base, "
    "r.salida_regreso, r.regreso_base, r.distancia_regreso, r.duracion_regreso, r.espera_regreso, "
    "r.distancia_total, r.traslado, r.servicio, r.espera, r.total, r.mejoras, r.hash_matriz, "
    "r.hash_secuencia, now() FROM rv2_ruta r JOIN ejecucion_ruteo_v2 e ON e.id = :ejecucion "
    "ORDER BY r.gestor"
)
SQL_PARADAS = (
    "INSERT INTO parada_ruta_v2 (snap_distancia_m, salida_anterior_at, llegada_estimada, "
    "inicio_servicio, fin_servicio, distancia_desde_anterior_m, ejecucion_ruteo_v2_id, "
    "cuenta_canonica_id, gestor_campo_id, ejecucion_campo_v2_id, orden_visita, "
    "duracion_desde_anterior_s, espera_s, prioridad, estado, motivo) "
    "SELECT snap, salida_anterior, llegada, inicio, fin, distancia, :ejecucion, cuenta, gestor, "
    ":campo, orden, duracion, espera, prioridad, estado, motivo FROM rv2_parada ORDER BY cuenta"
)
SQL_PARADAS_AJENAS = (
    # Cada parada publicada es una asignacion ASIGNADA de su plan, con su mismo gestor y su misma
    # prioridad: campo/v2 es la autoridad. Por la llave de la asignacion.
    "SELECT count(*) FROM parada_ruta_v2 x JOIN asignacion_campo_cuenta a "
    "ON a.ejecucion_campo_v2_id = x.ejecucion_campo_v2_id "
    "AND a.cuenta_canonica_id = x.cuenta_canonica_id WHERE x.ejecucion_ruteo_v2_id = :ejecucion "
    "AND (a.estado <> 'ASIGNADA' OR a.gestor_campo_id IS DISTINCT FROM x.gestor_campo_id "
    "OR a.prioridad <> x.prioridad)"
)


def _momento(segundos: int | None) -> datetime | None:
    return None if segundos is None else datetime.fromtimestamp(segundos, UTC)


def _metros(centimetros: int | None) -> Decimal | None:
    return None if centimetros is None else Decimal(centimetros).scaleb(-2)


def _fila_ruta(ejecucion: EjecucionRuteoV2, g: _Gestor, r: RutaPlaneada) -> tuple:
    return (
        g.gestor_campo_id,
        str(p.ruta_id(ejecucion.firma_inputs, g.gestor_id)),
        g.base_operativa_id,
        r.estado.value,
        None if r.motivo_base is None else r.motivo_base.value,
        len(r.paradas),
        r.contar(EstadoParada.PROGRAMADA),
        r.contar(EstadoParada.SIN_TIEMPO),
        r.contar(EstadoParada.NO_ENRUTABLE),
        r.contar(EstadoParada.NO_ENRUTABLE_BASE),
        _metros(r.base_snap_cm),
        _momento(r.jornada_inicio),
        _momento(r.jornada_fin),
        _momento(r.salida_base),
        _momento(r.salida_regreso),
        _momento(r.regreso_base),
        r.distancia_regreso_m,
        r.duracion_regreso_s,
        r.espera_regreso_s,
        r.distancia_total_m,
        r.duracion_traslado_s,
        r.duracion_servicio_s,
        r.duracion_espera_s,
        r.duracion_total_s,
        r.mejoras_2opt,
        r.hash_matriz,
        r.hash_secuencia,
    )


def _fila_parada(g: _Gestor, ids: dict[UUID, int], pp: p.ParadaPlaneada) -> tuple:
    return (
        ids[pp.clave],
        g.gestor_campo_id,
        pp.prioridad,
        pp.estado.value,
        None if pp.motivo is None else pp.motivo.value,
        pp.orden,
        _metros(pp.snap_cm),
        _momento(pp.salida_anterior),
        _momento(pp.llegada),
        _momento(pp.inicio_servicio),
        _momento(pp.fin_servicio),
        pp.distancia_desde_anterior_m,
        pp.duracion_desde_anterior_s,
        pp.espera_s,
    )


def _vaciar(s: Session, rutas: list[tuple], paradas_: list[tuple], cronometro: Cronometro) -> None:
    """Copia a las tablas de paso lo planeado hasta ahora: la memoria no crece con el plan."""
    if not rutas:
        return
    with cronometro.fase("staging"):
        copiar(s, "rv2_ruta", COLUMNAS_RUTA, rutas)
        copiar(s, "rv2_parada", COLUMNAS_PARADA, paradas_)


# --- contar y cerrar ------------------------------------------------------------------------------


def _contar(s: Session, ejecucion: EjecucionRuteoV2) -> tuple[dict[str, int], dict[str, Any]]:
    """Lo que publico la ejecucion, contado en la transaccion que lo publico: por estado de la ruta
    y de la parada, por motivo, por base y por prioridad, con la distancia y las duraciones."""
    parametros = {"ejecucion": ejecucion.id}
    por_ruta = dict(
        s.execute(
            text(
                "SELECT estado_ruta, count(*) FROM ruta_gestor_v2 "
                "WHERE ejecucion_ruteo_v2_id = :ejecucion GROUP BY 1"
            ),
            parametros,
        )
        .tuples()
        .all()
    )
    por_parada = dict(
        s.execute(
            text(
                "SELECT estado, count(*) FROM parada_ruta_v2 "
                "WHERE ejecucion_ruteo_v2_id = :ejecucion GROUP BY 1"
            ),
            parametros,
        )
        .tuples()
        .all()
    )
    por_motivo = dict(
        s.execute(
            text(
                "SELECT motivo, count(*) FROM parada_ruta_v2 WHERE ejecucion_ruteo_v2_id = "
                ":ejecucion AND motivo IS NOT NULL GROUP BY 1 ORDER BY 1"
            ),
            parametros,
        )
        .tuples()
        .all()
    )
    por_prioridad = {
        prioridad: {"paradas": paradas_, "programadas": programadas, "sin_tiempo": sin_tiempo}
        for prioridad, paradas_, programadas, sin_tiempo in s.execute(
            text(
                "SELECT prioridad, count(*), count(*) FILTER (WHERE estado = 'PROGRAMADA'), "
                "count(*) FILTER (WHERE estado = 'SIN_TIEMPO') FROM parada_ruta_v2 "
                "WHERE ejecucion_ruteo_v2_id = :ejecucion GROUP BY 1 ORDER BY 1"
            ),
            parametros,
        ).all()
    }
    totales = s.execute(
        text(
            "SELECT count(*), coalesce(sum(paradas_campo), 0), coalesce(sum(distancia_total_m), "
            "0), coalesce(sum(duracion_traslado_s), 0), coalesce(sum(duracion_servicio_s), 0), "
            "coalesce(sum(duracion_espera_s), 0) FROM ruta_gestor_v2 "
            "WHERE ejecucion_ruteo_v2_id = :ejecucion"
        ),
        parametros,
    ).one()
    por_base = {
        codigo: dict(
            zip(
                (
                    "rutas",
                    "completas",
                    "parciales",
                    "paradas",
                    "programadas",
                    "sin_tiempo",
                    "no_enrutables",
                    "distancia_m",
                    "traslado_s",
                ),
                (int(v) for v in resto),
                strict=True,
            )
        )
        for codigo, *resto in s.execute(
            text(
                "SELECT b.codigo, count(*), count(*) FILTER (WHERE r.estado_ruta = 'COMPLETA'), "
                "count(*) FILTER (WHERE r.estado_ruta = 'PARCIAL'), sum(r.paradas_campo), "
                "sum(r.programadas), sum(r.sin_tiempo), sum(r.no_enrutables + "
                "r.no_enrutables_base), sum(r.distancia_total_m), sum(r.duracion_traslado_s) "
                "FROM ruta_gestor_v2 r JOIN base_operativa b ON b.id = r.base_operativa_id "
                "WHERE r.ejecucion_ruteo_v2_id = :ejecucion GROUP BY b.codigo ORDER BY b.codigo"
            ),
            parametros,
        ).all()
    }
    conteos = {
        columna: por_ruta.get(estado.value, 0)
        for estado, columna in COLUMNA_DEL_ESTADO_RUTA.items()
    }
    conteos.update(
        {
            columna: por_parada.get(estado.value, 0)
            for estado, columna in COLUMNA_DEL_ESTADO_PARADA.items()
        }
    )
    rutas, paradas_campo, distancia, traslado, servicio, espera = (int(v) for v in totales)
    conteos.update(
        gestores_objetivo=rutas,
        paradas_objetivo=paradas_campo,
        distancia_total_m=distancia,
        duracion_vial_total_s=traslado,
        duracion_servicio_total_s=servicio,
        duracion_espera_total_s=espera,
    )
    distribucion = {
        "rutas": {e.value: por_ruta.get(e.value, 0) for e in EstadoRuta},
        "paradas": {e.value: por_parada.get(e.value, 0) for e in EstadoParada},
        "motivos": por_motivo,
        "por_prioridad": por_prioridad,
        "por_base": por_base,
    }
    return conteos, distribucion


def _cerrar(
    s: Session,
    ejecucion: EjecucionRuteoV2,
    politica: PoliticaRuteo,
    conteos: dict[str, int],
    distribucion: dict[str, Any],
) -> None:
    for columna, valor in conteos.items():
        setattr(ejecucion, columna, valor)
    ejecucion.estado = EstadoRuteoV2.EXITOSA
    ejecucion.resultado = ResultadoEjecucionRuteoV2.RUTAS_PUBLICADAS.value
    ejecucion.distribucion = distribucion
    ejecucion.detalle = (
        f"Se rutearon {conteos['paradas_objetivo']:,} cuentas ASIGNADA de "
        f"{conteos['gestores_objetivo']:,} gestores para el "
        f"{ejecucion.fecha_operacion.isoformat()}, "
        f"con {politica.version}: {conteos['programadas']:,} programadas, "
        f"{conteos['sin_tiempo']:,} sin tiempo, {conteos['no_enrutables']:,} no enrutables y "
        f"{conteos['no_enrutables_base']:,} sin base enrutable; "
        f"{conteos['rutas_completas']:,} rutas completas y {conteos['rutas_parciales']:,} "
        f"parciales; {conteos['distancia_total_m']:,} m por calle y "
        f"{conteos['duracion_vial_total_s']:,} s de traslado. Una heuristica determinista, no una "
        f"ruta optima, sin trafico. {VERSION_MOTOR}."
    )
    ejecucion.terminada_en = ahora()
    s.add(ejecucion)
    s.flush()


def _fallar(
    s: Session,
    ejecucion_id: int,
    etiqueta: UUID,
    resultado: ResultadoEjecucionRuteoV2,
    motivo: str,
) -> None:
    try:
        cola.confirmar_dueno(s)
    except cola.TrabajoAjeno:
        s.rollback()
        log.warning("ruteo v2 %s: su trabajo ya no es de este worker: %s", etiqueta, resultado)
        raise
    registrado = filas_afectadas(
        s.execute(
            update(EjecucionRuteoV2)
            .where(
                col(EjecucionRuteoV2.id) == ejecucion_id,
                col(EjecucionRuteoV2.estado) == EstadoRuteoV2.EN_PROCESO,
            )
            .values(
                estado=EstadoRuteoV2.FALLIDA,
                resultado=resultado.value,
                detalle=motivo,
                terminada_en=ahora(),
                gestores_objetivo=0,
                paradas_objetivo=0,
                distancia_total_m=0,
                duracion_vial_total_s=0,
                duracion_servicio_total_s=0,
                duracion_espera_total_s=0,
                **dict.fromkeys((*CONTEOS_DE_RUTAS, *CONTEOS_DE_PARADAS), 0),
            )
            .execution_options(synchronize_session=False)
        )
    )
    s.commit()
    if registrado:
        log.warning("ruteo v2 %s fallido (%s): %s", etiqueta, resultado.value, motivo)


# --- la salud del proveedor, para el worker -------------------------------------------------------


class SaludRuteo:
    """Si el proveedor de ruteo de este proceso responde, preguntado a lo mas cada `cada_segundos`:
    el worker no toma un trabajo RUTEO_V2 mientras no responde, y sigue con los demas."""

    def __init__(
        self,
        proveedor: ProveedorRuteo | None,
        cada_segundos: float,
        reloj: Callable[[], float] | None = None,
    ) -> None:
        self._proveedor = proveedor
        self._cada = cada_segundos
        self._reloj = reloj or time.monotonic
        self._ultima: tuple[float, bool] | None = None

    @property
    def configurado(self) -> bool:
        """Si este proceso tiene un proveedor de ruteo (MC_OSRM_URL). La salud del worker lo
        informa."""
        return self._proveedor is not None

    def disponible(self) -> bool:
        if self._proveedor is None:
            return False
        ahora_ = self._reloj()
        if self._ultima is None or ahora_ - self._ultima[0] >= self._cada:
            self._ultima = (ahora_, self._proveedor.salud())
        return self._ultima[1]
