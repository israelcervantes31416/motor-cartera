"""Una ejecucion de decision/v2: decidir las cuentas de un corte point-in-time, en PostgreSQL y todo
o nada.

El orden:

1. `abrir` registra la ejecucion EN_PROCESO con su trabajo DECISION_V2, en la transaccion de quien
   llama, despues de fijar sus entradas: el corte (el universo), `as_of` (el dia del corte),
   `known_at` (la frontera del conocimiento; si se omite, el reloj de la base al abrir, una sola
   vez), la zona del dia logico y las versiones con el hash de la politica. Con ellas sale su
   `firma_inputs`. La piden POST /decisiones-v2 y `motor-cartera decidir-v2`.
2. Un worker toma el trabajo. Antes de leer nada, espera a que la frontera este asentada: que no
   quede abierta ninguna transaccion que empezo antes de known_at, porque lo que registre llevaria
   un registrado_en o un creado_en anterior a known_at y todavia no se ve (ver `frontera_asentada`).
3. `decidir` toma la ejecucion con su fila bloqueada y, en una transaccion y por conjuntos, arma en
   tablas temporales el universo (los snapshots del corte), la historia (los cortes conocidos a
   known_at), las gestiones, las promesas (evaluadas con las reglas de evaluacion-promesa/v1), los
   convenios, los pagos (la interpretacion de motor-pagos/v1 vigente a known_at) y la atribucion
   (la de atribucion/v1 vigente a known_at, solo ASOCIACION_UNICA). Ninguna cuenta pasa por Python.
4. Un INSERT ... SELECT publica las features de cada cuenta con su `feature_hash`; otro, la decision
   de cada cuenta con decision-policy/public-v1, generada en SQL desde `politica`, con sus motivos y
   su `decision_hash`. Se cuenta, se comprueba y se cierra EXITOSA en la misma transaccion. Si algo
   falla, se revierte todo y la ejecucion queda FALLIDA: nunca una decision a medias.

Los filtros point-in-time, por dominio:

- un evento del lifecycle (gestion, promesa, convenio, sus cancelaciones) existe si ocurrio antes
  del final de as_of y se registro hasta known_at; una anulacion, si se registro hasta known_at,
  ocurra cuando ocurra: corrige el registro, como en evaluacion-promesa/v1;
- un corte, si su fecha es hasta as_of y se materializo (creado_en) hasta known_at;
- un movimiento, si se recibio antes del final de as_of y es de la interpretacion de su mes vigente
  a known_at (la EXITOSA mas reciente terminada hasta known_at); un pago esta anulado si el reverso
  que lo anula se recibio antes del final de as_of;
- una atribucion, si es la vigente de su mes a known_at;
- un pago observado, si su historia se materializo hasta known_at.

Lo que hace segura la entrega repetida es lo mismo que en los demas motores: el bloqueo de la
ejecucion, sus estados terminales, la transaccion todo o nada, los indices unicos (una EXITOSA por
firma; una EN_PROCESO por corte y versiones) y que solo publica el dueno vigente de su trabajo.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from datetime import time as hora
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session, select

from motor_cartera.atraso import TRAMOS_ATRASO
from motor_cartera.atribucion.reglas import VERSION_ATRIBUCION
from motor_cartera.db.modelos import (
    CorteCanonico,
    EjecucionDecisionV2,
    EstadoDecisionV2,
    ModoDecisionV2,
    ResultadoEjecucionDecisionV2,
    TipoTrabajo,
    ahora,
)
from motor_cartera.db.sesion import insertar_en_savepoint, restriccion, sesion
from motor_cartera.decision.reglas import CANAL_POR_PRIORIDAD, PRIORIDAD_BASE, SEGMENTO_POR_TRAMO
from motor_cartera.decision_v2.features import FEATURES, NULO, VERSION_FEATURES, Feature, Tipo
from motor_cartera.decision_v2.politica import (
    EN_TRAMO,
    NIVEL,
    NIVEL_MAXIMO,
    NIVEL_MINIMO,
    POLITICAS,
    PUBLIC_V1,
    VERSION_MOTOR,
    CodigoMotivo,
    Condicion,
    Politica,
    hash_politica,
)
from motor_cartera.evaluacion.ejecuciones import sql_conclusiones
from motor_cartera.evaluacion.reglas import EstadoEvaluacion
from motor_cartera.ingesta.fuente_oficial import Cronometro
from motor_cartera.motor_pagos.reglas import VERSION_MOTOR_PAGOS, periodo_de, siguiente_periodo
from motor_cartera.orquestacion import cola

log = logging.getLogger(__name__)

INDICE_EXITOSA = "ux_decision_v2_exitosa"
INDICE_EN_PROCESO = "ux_decision_v2_en_proceso"

VENTANAS_DE_PAGOS = (7, 30, 90)
"""Las ventanas, en dias de calendario que terminan en as_of, de las features de pagos."""
VENTANAS_DE_ATRIBUCION = (30, 90)

ESPERA_DE_LA_FRONTERA = 30.0
"""Cuantos segundos espera un worker, en su intento, a que la frontera de conocimiento se asiente.
Si no se asienta, el intento termina sin tocar la ejecucion y la cola lo reintenta con su espera:
nunca se decide con una frontera abierta."""


# --- errores al abrir -----------------------------------------------------------------------------


class EntradaNoDecidible(Exception):
    """Una peticion de decision/v2 que no se puede abrir: no registra nada. `codigo` es estable."""

    codigo = "ENTRADA_NO_DECIDIBLE"
    estado_http = 422


class CorteNoEncontrado(EntradaNoDecidible):
    codigo = "CORTE_NO_ENCONTRADO"
    estado_http = 404


class AsOfDistintoDelCorte(EntradaNoDecidible):
    codigo = "AS_OF_DISTINTO_DEL_CORTE"


class KnownAtRequerido(EntradaNoDecidible):
    codigo = "KNOWN_AT_REQUERIDO"


class KnownAtFuturo(EntradaNoDecidible):
    codigo = "KNOWN_AT_FUTURO"


class KnownAtSinZona(EntradaNoDecidible):
    codigo = "KNOWN_AT_SIN_ZONA"


class CorteNoConocido(EntradaNoDecidible):
    codigo = "CORTE_NO_CONOCIDO"


class CorteNoVigente(EntradaNoDecidible):
    codigo = "CORTE_NO_VIGENTE"


class PoliticaNoSoportada(EntradaNoDecidible):
    codigo = "POLITICA_NO_SOPORTADA"


class DecisionV2YaGenerada(Exception):
    """Otra ejecucion ya publico exactamente las mismas entradas (la misma firma)."""

    def __init__(self, previa: EjecucionDecisionV2) -> None:
        super().__init__(
            f"Estas entradas ya se decidieron con {previa.version_politica}: la ejecucion "
            f"{previa.decision_run_id} (firma {previa.firma_inputs[:12]}...)."
        )
        self.previa = previa


class DecisionV2EnProceso(Exception):
    """El corte ya se esta decidiendo con estas versiones: hay una ejecucion EN_PROCESO."""

    def __init__(self, activa: EjecucionDecisionV2) -> None:
        super().__init__(
            f"El corte del {activa.as_of.isoformat()} ya se esta decidiendo con "
            f"{activa.version_politica}: la ejecucion {activa.decision_run_id}."
        )
        self.activa = activa


class FronteraSinAsentar(Exception):
    """Todavia hay una transaccion abierta que empezo antes de known_at: lo que registre no se ve
    aun. Es transitorio: el intento no toca la ejecucion y la cola lo reintenta."""


class _NoSePublica(Exception):
    def __init__(
        self, resultado: ResultadoEjecucionDecisionV2, mensaje: str, *, fallidas: int = 0
    ) -> None:
        super().__init__(mensaje)
        self.resultado = resultado
        self.fallidas = fallidas


# --- la firma de las entradas ---------------------------------------------------------------------


def firma_inputs(
    *,
    despacho_id: str,
    cartera_id: str,
    corte_id: UUID,
    as_of: date,
    known_at: datetime,
    zona: str,
    version_features: str = VERSION_FEATURES,
    politica: Politica = PUBLIC_V1,
) -> str:
    """El SHA-256 de las entradas logicas de una decision: el motor, las versiones de las features
    y de la politica con su hash, la cartera, el corte, as_of, known_at (en UTC, al microsegundo) y
    la zona del dia logico. Las mismas entradas son la misma decision; el modo no entra, porque no
    cambia nada de lo que se calcula."""
    if known_at.tzinfo is None:
        raise ValueError("known_at es un instante: lleva su zona horaria.")
    lineas = [
        VERSION_MOTOR,
        version_features,
        politica.version,
        hash_politica(politica),
        despacho_id,
        cartera_id,
        str(corte_id),
        as_of.isoformat(),
        known_at.astimezone(UTC).isoformat(timespec="microseconds"),
        zona,
    ]
    return hashlib.sha256("\n".join(lineas).encode("utf-8")).hexdigest()


# --- abrir ----------------------------------------------------------------------------------------


def abrir(
    s: Session,
    corte_id: UUID,
    *,
    as_of: date | None,
    known_at: datetime | None,
    modo: ModoDecisionV2,
    version_politica: str | None,
    zona: str,
    max_intentos: int,
    tomado_por: str | None = None,
    lease_segundos: float | None = None,
) -> EjecucionDecisionV2:
    """La ejecucion EN_PROCESO de decision/v2 sobre un corte, con su trabajo DECISION_V2, en la
    transaccion de quien llama: envia los INSERT y no confirma. Con `tomado_por`, el trabajo nace
    tomado por ese proceso, que la decide en primer plano.

    Fija las entradas una sola vez, aqui: as_of es el dia del corte; known_at, si se omite (solo en
    OPERATIVO), es now() de esta transaccion, y queda guardado para cada reintento. Levanta, sin
    registrar nada: CorteNoEncontrado, PoliticaNoSoportada, AsOfDistintoDelCorte, KnownAtRequerido
    (un BACKTEST la declara), KnownAtSinZona, KnownAtFuturo, CorteNoConocido (el corte se
    materializo despues de known_at), CorteNoVigente (un OPERATIVO sobre un corte que ya no es el
    mas reciente conocido a known_at), DecisionV2YaGenerada y DecisionV2EnProceso."""
    corte = s.exec(select(CorteCanonico).where(CorteCanonico.corte_id == corte_id)).one_or_none()
    if corte is None:
        raise CorteNoEncontrado(f"No existe un corte canonico con corte_id {corte_id}.")
    version_politica = version_politica or PUBLIC_V1.version
    politica = POLITICAS.get(version_politica)
    if politica is None:
        raise PoliticaNoSoportada(
            f"Este servicio decide con {', '.join(sorted(POLITICAS))}, no con {version_politica}."
        )
    if as_of is None:
        as_of = corte.fecha_corte
    elif as_of != corte.fecha_corte:
        raise AsOfDistintoDelCorte(
            f"as_of es el dia del corte: el corte {corte.corte_id} es del "
            f"{corte.fecha_corte.isoformat()}, no del {as_of.isoformat()}. Un corte posterior a "
            "as_of seria informacion del futuro, y uno anterior mezclaria su foto con dias que no "
            "observa."
        )
    reloj = s.execute(text("SELECT now()")).scalar_one()
    if known_at is None:
        if modo == ModoDecisionV2.BACKTEST:
            raise KnownAtRequerido(
                "Un BACKTEST declara su frontera de conocimiento: known_at es obligatorio. Sin el "
                "se decidiria un dia pasado con todo lo que se sabe hoy."
            )
        known_at = reloj
    elif known_at.tzinfo is None:
        raise KnownAtSinZona(
            "known_at es un instante: lleva su zona horaria. Sin ella no se sabe cuando es."
        )
    elif known_at > reloj:
        raise KnownAtFuturo(
            f"known_at ({known_at.isoformat()}) es posterior al reloj de la base "
            f"({reloj.isoformat()}): no se decide con conocimiento que todavia no existe."
        )
    if corte.creado_en > known_at:
        raise CorteNoConocido(
            f"El corte {corte.corte_id} se materializo el {corte.creado_en.isoformat()}, despues "
            f"de known_at ({known_at.isoformat()}): a esa frontera no existia, y no hay universo "
            "que decidir."
        )
    if modo == ModoDecisionV2.OPERATIVO:
        posterior = s.exec(
            select(CorteCanonico)
            .where(
                CorteCanonico.despacho_id == corte.despacho_id,
                CorteCanonico.cartera_id == corte.cartera_id,
                CorteCanonico.fecha_corte > corte.fecha_corte,
                CorteCanonico.creado_en <= known_at,
            )
            .order_by(CorteCanonico.fecha_corte.desc())
        ).first()
        if posterior is not None:
            raise CorteNoVigente(
                f"A known_at ya se conocia el corte del {posterior.fecha_corte.isoformat()}: una "
                f"decision OPERATIVO es la del corte mas reciente. Para decidir el del "
                f"{corte.fecha_corte.isoformat()}, pide un BACKTEST y declara su known_at."
            )
    firma = firma_inputs(
        despacho_id=corte.despacho_id,
        cartera_id=corte.cartera_id,
        corte_id=corte.corte_id,
        as_of=as_of,
        known_at=known_at,
        zona=zona,
        politica=politica,
    )
    previa = _exitosa(s, firma)
    if previa is not None:
        raise DecisionV2YaGenerada(previa)
    activa = _activa(s, corte.id, politica)
    if activa is not None:
        raise DecisionV2EnProceso(activa)
    ejecucion = EjecucionDecisionV2(
        corte_canonico_id=corte.id,
        despacho_id=corte.despacho_id,
        cartera_id=corte.cartera_id,
        as_of=as_of,
        known_at=known_at,
        modo=modo,
        zona_horaria=zona,
        version_motor=VERSION_MOTOR,
        version_features=VERSION_FEATURES,
        version_politica=politica.version,
        hash_politica=hash_politica(politica),
        firma_inputs=firma,
        cuentas_objetivo=corte.cuentas,
    )
    error = insertar_en_savepoint(s, ejecucion)
    if error is not None:
        if restriccion(error) != INDICE_EN_PROCESO:
            raise error
        activa = _activa(s, corte.id, politica)
        if (
            activa is None
        ):  # pragma: no cover - la que gano ya termino entre el rechazo y la revision
            raise error
        raise DecisionV2EnProceso(activa) from error
    cola.crear(
        s,
        TipoTrabajo.DECISION_V2,
        ejecucion.id,
        max_intentos=max_intentos,
        tomado_por=tomado_por,
        lease_segundos=lease_segundos,
    )
    log.info(
        "decision v2 %s abierta: corte %s, as_of %s, known_at %s, %s",
        ejecucion.decision_run_id,
        corte.corte_id,
        as_of.isoformat(),
        known_at.isoformat(),
        modo,
    )
    return ejecucion


def _exitosa(s: Session, firma: str) -> EjecucionDecisionV2 | None:
    return s.exec(
        select(EjecucionDecisionV2).where(
            EjecucionDecisionV2.firma_inputs == firma,
            EjecucionDecisionV2.estado == EstadoDecisionV2.EXITOSA,
        )
    ).first()


def _activa(s: Session, corte_canonico_id: int, politica: Politica) -> EjecucionDecisionV2 | None:
    return s.exec(
        select(EjecucionDecisionV2)
        .where(
            EjecucionDecisionV2.corte_canonico_id == corte_canonico_id,
            EjecucionDecisionV2.version_features == VERSION_FEATURES,
            EjecucionDecisionV2.version_politica == politica.version,
            EjecucionDecisionV2.estado == EstadoDecisionV2.EN_PROCESO,
        )
        .with_for_update()
    ).first()


# --- la frontera de conocimiento ------------------------------------------------------------------


def frontera_asentada(s: Session, known_at: datetime) -> bool:
    """Si todo lo que se registro hasta known_at ya esta confirmado y a la vista.

    registrado_en es now() de la transaccion que registra, y creado_en y terminada_en se fijan
    dentro de la transaccion que publica: un registro con una marca anterior a known_at puede estar
    en una transaccion que todavia no confirma. La frontera esta asentada cuando el reloj de la base
    ya paso known_at y ninguna sesion cliente de esta base tiene abierta una transaccion que empezo
    hasta known_at; desde entonces, nada nuevo puede llegar con una marca anterior. Asi lo que se
    calcula es funcion exacta de (as_of, known_at) y reconstruirlo despues da lo mismo.

    pg_stat_activity solo muestra el inicio de la transaccion de las sesiones del mismo rol (o con
    pg_read_all_stats): las de otro rol no se ven, y no se esperan. Cada consulta abre su propia
    transaccion, porque PostgreSQL congela pg_stat_activity dentro de una."""
    return bool(
        s.execute(
            text(
                "SELECT now() > :known_at AND NOT EXISTS (SELECT 1 FROM pg_stat_activity a "
                "WHERE a.datname = current_database() AND a.pid <> pg_backend_pid() "
                "AND a.backend_type = 'client backend' AND a.xact_start <= :known_at)"
            ),
            {"known_at": known_at},
        ).scalar_one()
    )


def esperar_frontera(known_at: datetime, *, espera: float = ESPERA_DE_LA_FRONTERA) -> None:
    """Espera, consultando en transacciones cortas, a que la frontera se asiente; si no se asienta
    en `espera` segundos, levanta FronteraSinAsentar."""
    limite = time.monotonic() + espera
    while True:
        with sesion() as s:
            if frontera_asentada(s, known_at):
                return
        if time.monotonic() >= limite:
            raise FronteraSinAsentar(
                f"Hay una transaccion abierta desde antes de known_at ({known_at.isoformat()}): lo "
                "que registre todavia no se ve. Se reintenta despues; no se decide con la frontera "
                "abierta."
            )
        time.sleep(0.2)


# --- decidir --------------------------------------------------------------------------------------

ANTES_DEL_STAGING = "ANTES_DEL_STAGING"
DESPUES_DE_FEATURES = "DESPUES_DE_FEATURES"
DESPUES_DE_RESULTADOS = "DESPUES_DE_RESULTADOS"
ANTES_DE_LOS_CONTEOS = "ANTES_DE_LOS_CONTEOS"
ANTES_DE_CERRAR = "ANTES_DE_CERRAR"
PUNTOS_DE_FALLA = (
    ANTES_DEL_STAGING,
    DESPUES_DE_FEATURES,
    DESPUES_DE_RESULTADOS,
    ANTES_DE_LOS_CONTEOS,
    ANTES_DE_CERRAR,
)


def _punto_de_falla(nombre: str) -> None:
    """Un punto donde una prueba puede inyectar una falla. En produccion no hace nada."""


@dataclass
class _Avance:
    """Lo que alcanzo a leer: lo que una FALLIDA dice."""

    objetivo: int | None = None
    disponibilidad: dict | None = None
    fallidas: int = 0


@dataclass(frozen=True)
class Limites:
    """Los instantes del dia logico y de cada ventana: los que terminan en as_of."""

    fin: datetime
    """El final de as_of en la zona: el inicio del dia siguiente."""
    inicio: dict[int, datetime] = field(default_factory=dict)
    """El inicio de la ventana de N dias de calendario que termina en as_of."""
    fin_local: datetime = datetime.min
    """El final de as_of en hora local sin zona, como las fechas de pagos/v1."""
    inicio_local: dict[int, datetime] = field(default_factory=dict)


def limites(s: Session, as_of: date, zona: str) -> Limites:
    """Los limites del dia logico, con la base de zonas de PostgreSQL: el dia as_of empieza y
    termina a medianoche en `zona`."""
    dias = sorted({*VENTANAS_DE_PAGOS, *VENTANAS_DE_ATRIBUCION, 7, 30, 90})
    locales = {n: datetime.combine(as_of + timedelta(days=1 - n), hora()) for n in dias}
    fin_local = datetime.combine(as_of + timedelta(days=1), hora())
    instantes = s.execute(
        text(
            "SELECT "
            + ", ".join(f"timezone(:zona, CAST(:l{i} AS timestamp))" for i in range(len(dias) + 1))
        ),
        {"zona": zona, **{f"l{i}": v for i, v in enumerate([fin_local, *locales.values()])}},
    ).one()
    fin, *inicios = instantes
    return Limites(fin, dict(zip(locales, inicios, strict=True)), fin_local, locales)


def ejecutar_decision_v2(ejecucion_id: int) -> None:
    """Lo que ejecuta un trabajo DECISION_V2. Si la ejecucion ya termino no hace nada; si no, espera
    a que su frontera de conocimiento se asiente y decide."""
    with sesion() as s:
        ejecucion = s.get_one(EjecucionDecisionV2, ejecucion_id)
        if ejecucion.estado != EstadoDecisionV2.EN_PROCESO:
            log.info("decision v2 %s ya termino %s", ejecucion.decision_run_id, ejecucion.estado)
            return
        known_at = ejecucion.known_at
    esperar_frontera(known_at)
    decidir(ejecucion_id)


def decidir(ejecucion_id: int, *, cronometro: Cronometro | None = None) -> None:
    """Decide las cuentas del corte de la ejecucion y la deja en un estado terminal: con su fila
    bloqueada hasta el commit, todo en una transaccion y en un savepoint; si falla, queda FALLIDA
    sin soltar su fila, y solo publica o falla el dueno vigente de su trabajo. Levanta
    DecisionV2YaGenerada si otra ejecucion publico primero la misma firma, TrabajoAjeno, y los
    errores transitorios de la base, con la ejecucion EN_PROCESO para que la cola la reintente.

    No espera la frontera: eso lo hace `ejecutar_decision_v2`, antes de bloquear nada. Quien llame
    a esta funcion directamente tiene que saber que known_at ya paso y que nada quedo abierto antes
    de el."""
    cronometro = cronometro or Cronometro()
    with sesion() as s:
        ejecucion = s.exec(
            select(EjecucionDecisionV2)
            .where(EjecucionDecisionV2.id == ejecucion_id)
            .with_for_update()
        ).one()
        if ejecucion.estado != EstadoDecisionV2.EN_PROCESO:
            log.warning("decision v2 %s ya termino %s", ejecucion.decision_run_id, ejecucion.estado)
            return
        etiqueta, firma = ejecucion.decision_run_id, ejecucion.firma_inputs
        avance = _Avance()
        try:
            with s.begin_nested():
                _decidir(s, ejecucion, avance, cronometro)
        except _NoSePublica as exc:
            resultado, motivo = exc.resultado, str(exc)
            avance.fallidas = exc.fallidas
        except IntegrityError as exc:
            if restriccion(exc) == INDICE_EXITOSA:
                resultado, motivo = (
                    ResultadoEjecucionDecisionV2.YA_DECIDIDA,
                    "Otra ejecucion publico la misma firma de entrada mientras esta se procesaba; "
                    "no se publica dos veces.",
                )
            else:
                log.exception("decision v2 %s: violacion de integridad inesperada", etiqueta)
                resultado, motivo = (
                    ResultadoEjecucionDecisionV2.ERROR_INTERNO,
                    "Error interno al publicar; ver la bitacora del servicio.",
                )
        except OperationalError:
            log.warning("decision v2 %s: error transitorio; sigue EN_PROCESO", etiqueta)
            raise
        except Exception as exc:
            log.exception("decision v2 %s: error inesperado", etiqueta)
            resultado, motivo = (
                ResultadoEjecucionDecisionV2.ERROR_INTERNO,
                f"Error interno ({type(exc).__name__}); ver la bitacora.",
            )
        else:
            cola.confirmar_dueno(s)
            with cronometro.fase("commit"):
                s.commit()
            return
        _fallar(s, ejecucion_id, etiqueta, resultado, motivo, avance)
        if resultado == ResultadoEjecucionDecisionV2.YA_DECIDIDA:
            raise DecisionV2YaGenerada(_exitosa(s, firma))


def _decidir(
    s: Session, ejecucion: EjecucionDecisionV2, avance: _Avance, cronometro: Cronometro
) -> None:
    politica = POLITICAS.get(ejecucion.version_politica)
    if (
        ejecucion.version_motor != VERSION_MOTOR
        or ejecucion.version_features != VERSION_FEATURES
        or politica is None
        or ejecucion.hash_politica != hash_politica(politica)
    ):
        raise _NoSePublica(
            ResultadoEjecucionDecisionV2.VERSION_NO_SOPORTADA,
            f"La ejecucion pide {ejecucion.version_motor}, {ejecucion.version_features} y "
            f"{ejecucion.version_politica} (hash {ejecucion.hash_politica[:12]}...), y este "
            f"servicio decide con {VERSION_MOTOR}, {VERSION_FEATURES} y "
            f"{', '.join(sorted(POLITICAS))}; no se publico nada.",
        )
    _punto_de_falla(ANTES_DEL_STAGING)
    parametros = _parametros(s, ejecucion)
    with cronometro.fase("universo"):
        _ejecutar(s, SQL_UNIVERSO, parametros)
        avance.objetivo = s.execute(text("SELECT count(*) FROM dv2_universo")).scalar_one()
    with cronometro.fase("historia"):
        _ejecutar(s, SQL_HISTORIA, parametros)
    with cronometro.fase("lifecycle"):
        _ejecutar(s, SQL_GESTIONES, parametros)
    with cronometro.fase("pagos"):
        _ejecutar(s, SQL_PAGOS, parametros)
    with cronometro.fase("promesas"):
        _ejecutar(s, SQL_PROMESAS, parametros)
        desde = _desde_los_meses(s, parametros)
        observados = _observados(s, {**parametros, "desde_meses": desde})
        parametros["horizonte"] = observados["horizonte"]
        _ejecutar(s, SQL_EVALUACION_DE_PROMESAS, parametros)
    with cronometro.fase("atribucion"):
        _ejecutar(s, SQL_ATRIBUCION, parametros)
    disponibilidad = avance.disponibilidad = _disponibilidad(s, ejecucion, observados)
    for familia, ventanas in (("pagos", VENTANAS_DE_PAGOS), ("atribucion", VENTANAS_DE_ATRIBUCION)):
        for n in ventanas:
            parametros[f"{familia}_{n}d"] = disponibilidad[familia][f"{n}d"]
    with cronometro.fase("features"):
        s.execute(text(SQL_FEATURES), parametros)
    _punto_de_falla(DESPUES_DE_FEATURES)
    no_decidibles = s.execute(
        text(
            "SELECT count(*) FROM feature_decision_cuenta WHERE ejecucion_decision_v2_id = "
            ":ejecucion AND dias_atraso < 0"
        ),
        parametros,
    ).scalar_one()
    if no_decidibles:
        raise _NoSePublica(
            ResultadoEjecucionDecisionV2.CUENTAS_NO_DECIDIBLES,
            f"{no_decidibles:,} cuentas del corte tienen dias de atraso negativos: "
            f"{ejecucion.version_politica} no las sabe decidir, y no se publica ninguna.",
            fallidas=no_decidibles,
        )
    with cronometro.fase("politica"):
        s.execute(text(sql_resultados(politica)), parametros)
    _punto_de_falla(DESPUES_DE_RESULTADOS)
    _punto_de_falla(ANTES_DE_LOS_CONTEOS)
    with cronometro.fase("conteos"):
        distribucion = _contar(s, ejecucion, avance)
    _punto_de_falla(ANTES_DE_CERRAR)
    _cerrar(s, ejecucion, avance, distribucion)


def _parametros(s: Session, ejecucion: EjecucionDecisionV2) -> dict[str, Any]:
    tiempos = limites(s, ejecucion.as_of, ejecucion.zona_horaria)
    return {
        "ejecucion": ejecucion.id,
        "corte": ejecucion.corte_canonico_id,
        "despacho": ejecucion.despacho_id,
        "cartera": ejecucion.cartera_id,
        "as_of": ejecucion.as_of,
        "known_at": ejecucion.known_at,
        "zona": ejecucion.zona_horaria,
        "version_motor_pagos": VERSION_MOTOR_PAGOS,
        "version_atribucion": VERSION_ATRIBUCION,
        "version_features": ejecucion.version_features,
        "version_politica": ejecucion.version_politica,
        "hash_politica": ejecucion.hash_politica,
        "fin": tiempos.fin,
        "fin_local": tiempos.fin_local,
        **{f"inicio_{n}d": instante for n, instante in tiempos.inicio.items()},
        **{f"inicio_{n}d_local": instante for n, instante in tiempos.inicio_local.items()},
    }


def _ejecutar(s: Session, sentencias: tuple[str, ...], parametros: dict[str, Any]) -> None:
    for sentencia in sentencias:
        s.execute(text(sentencia), parametros)


# --- lo que se lee --------------------------------------------------------------------------------


def _sin_anular(evento: str) -> str:
    """Que la gestion del evento `evento` no tenga una anulacion registrada hasta known_at. Una
    anulacion corrige el registro: cuenta si se registro hasta known_at, ocurra cuando ocurra."""
    return (
        "NOT EXISTS (SELECT 1 FROM evento_lifecycle a WHERE a.evento_relacionado_id = "
        f"{evento} AND a.tipo_evento = 'GESTION_ANULADA' AND a.registrado_en <= :known_at)"
    )


def _cierre(evento: str, tipo: str) -> str:
    """El ocurrido_en del cierre (`tipo`) del evento `evento`, si ocurrio antes del final de as_of y
    se registro hasta known_at: una cancelacion es un hecho de negocio, con sus dos tiempos."""
    return (
        f"(SELECT x.ocurrido_en FROM evento_lifecycle x WHERE x.evento_relacionado_id = {evento} "
        f"AND x.tipo_evento = '{tipo}' AND x.ocurrido_en < :fin AND x.registrado_en <= :known_at)"
    )


SQL_UNIVERSO = (
    # Las cuentas del corte: sus snapshots. Ni una mas, ni una menos.
    "CREATE TEMP TABLE dv2_universo ON COMMIT DROP AS "
    "SELECT s.cuenta_canonica_id AS cuenta, c.cliente_unico, s.dias_atraso, s.semanas_atraso, "
    "s.saldo_total, s.saldo_atrasado, s.saldo_requerido, s.moratorios, s.producto, s.estrategia, "
    "s.estatus_plan FROM snapshot_cuenta s JOIN cuenta_canonica c ON c.id = s.cuenta_canonica_id "
    "WHERE s.corte_canonico_id = :corte",
    "CREATE UNIQUE INDEX ON dv2_universo (cuenta)",
    "ANALYZE dv2_universo",
)

SQL_HISTORIA = (
    # Los cortes de la cartera conocidos a known_at hasta as_of, con su lugar en la secuencia.
    "CREATE TEMP TABLE dv2_corte ON COMMIT DROP AS "
    "SELECT id, row_number() OVER (ORDER BY fecha_corte) AS posicion FROM corte_canonico "
    "WHERE despacho_id = :despacho AND cartera_id = :cartera AND fecha_corte <= :as_of "
    "AND creado_en <= :known_at",
    # Por cuenta: cuantos de esos cortes la observan, cuantas veces reingreso (un salto de mas de un
    # lugar entre dos observaciones seguidas) y como se une el snapshot actual con el anterior.
    "CREATE TEMP TABLE dv2_historia ON COMMIT DROP AS "
    "SELECT cuenta, count(*) AS cortes_observados, "
    "count(*) FILTER (WHERE salto > 1) AS reingresos_observados, "
    "max(saldo_total - saldo_previo) FILTER (WHERE actual) AS delta_saldo, "
    "max(dias_atraso - dias_previo) FILTER (WHERE actual) AS delta_dias, "
    "bool_or(salto = 1) FILTER (WHERE actual) AS continuo FROM ("
    "SELECT s.cuenta_canonica_id AS cuenta, s.corte_canonico_id = :corte AS actual, "
    "s.saldo_total, s.dias_atraso, k.posicion - lag(k.posicion) OVER w AS salto, "
    "lag(s.saldo_total) OVER w AS saldo_previo, lag(s.dias_atraso) OVER w AS dias_previo "
    "FROM dv2_universo u JOIN snapshot_cuenta s ON s.cuenta_canonica_id = u.cuenta "
    "JOIN dv2_corte k ON k.id = s.corte_canonico_id "
    "WINDOW w AS (PARTITION BY s.cuenta_canonica_id ORDER BY k.posicion)) x GROUP BY cuenta",
)

SQL_GESTIONES = (
    # Las gestiones de las cuentas del universo ocurridas antes del final de as_of, registradas
    # hasta known_at y sin una anulacion registrada hasta known_at, por el indice de las gestiones
    # de una cuenta.
    "CREATE TEMP TABLE dv2_gestion ON COMMIT DROP AS "
    "SELECT g.cuenta_canonica_id AS cuenta, g.id, g.ocurrido_en, g.canal, g.nivel_contacto, "
    "EXISTS (SELECT 1 FROM visita_campo v WHERE v.gestion_cobranza_id = g.id) AS con_visita "
    "FROM dv2_universo u JOIN gestion_cobranza g ON g.cuenta_canonica_id = u.cuenta "
    "AND g.ocurrido_en < :fin JOIN evento_lifecycle e ON e.id = g.evento_lifecycle_id "
    f"AND e.registrado_en <= :known_at WHERE {_sin_anular('g.evento_lifecycle_id')}",
    "CREATE TEMP TABLE dv2_f_gestion ON COMMIT DROP AS "
    "SELECT cuenta, max(ocurrido_en) AS ultima, "
    "count(*) FILTER (WHERE ocurrido_en >= :inicio_7d) AS gestiones_7d, "
    "count(*) FILTER (WHERE ocurrido_en >= :inicio_30d) AS gestiones_30d, "
    "count(*) FILTER (WHERE ocurrido_en >= :inicio_7d AND nivel_contacto = 'SIN_CONTACTO') "
    "AS sin_contacto_7d, "
    "count(*) FILTER (WHERE ocurrido_en >= :inicio_30d AND nivel_contacto = 'CONTACTO_TITULAR') "
    "AS titular_30d, "
    "count(*) FILTER (WHERE ocurrido_en >= :inicio_30d AND nivel_contacto = 'CONTACTO_TERCERO') "
    "AS tercero_30d, "
    "count(*) FILTER (WHERE ocurrido_en >= :inicio_30d AND nivel_contacto <> 'NO_APLICA') "
    "AS contactables_30d, "
    "count(*) FILTER (WHERE ocurrido_en >= :inicio_30d AND con_visita) AS visitas_30d, "
    "(array_agg(canal ORDER BY ocurrido_en DESC, id DESC))[1] AS ultima_canal, "
    "(array_agg(nivel_contacto ORDER BY ocurrido_en DESC, id DESC))[1] AS ultima_nivel "
    "FROM dv2_gestion GROUP BY cuenta",
    # Los convenios: vigente si empezo hasta as_of, no termino antes y nadie lo cancelo antes del
    # final de as_of; los de los ultimos 90 dias, cancelados o no. Los de una gestion anulada no
    # existen.
    "CREATE TEMP TABLE dv2_f_convenio ON COMMIT DROP AS "
    "SELECT c.cuenta_canonica_id AS cuenta, "
    "bool_or(c.fecha_inicio <= :as_of AND (c.fecha_fin IS NULL OR c.fecha_fin >= :as_of) AND "
    f"{_cierre('c.evento_lifecycle_id', 'CONVENIO_CANCELADO')} IS NULL) AS convenio_vigente, "
    "count(*) FILTER (WHERE e.ocurrido_en >= :inicio_90d) AS convenios_90d "
    "FROM dv2_universo u JOIN convenio_cobranza c ON c.cuenta_canonica_id = u.cuenta "
    "JOIN evento_lifecycle e ON e.id = c.evento_lifecycle_id "
    "JOIN gestion_cobranza g ON g.id = c.gestion_cobranza_id "
    "WHERE e.ocurrido_en < :fin AND e.registrado_en <= :known_at "
    f"AND {_sin_anular('g.evento_lifecycle_id')} GROUP BY c.cuenta_canonica_id",
)

SQL_PAGOS = (
    # La interpretacion de cada mes vigente a known_at: su EXITOSA mas reciente que ya habia
    # terminado.
    "CREATE TEMP TABLE dv2_interpretacion ON COMMIT DROP AS "
    "SELECT DISTINCT ON (periodo_desde) id, periodo_desde, observaciones_leidas "
    "FROM ejecucion_motor_pagos WHERE despacho_id = :despacho AND cartera_id = :cartera "
    "AND version_motor = :version_motor_pagos AND estado = 'EXITOSA' "
    "AND terminada_en <= :known_at AND periodo_desde <= :as_of "
    "ORDER BY periodo_desde, id DESC",
    # Los PAGO y POSIBLE_REVERSO de los clientes del universo recibidos antes del final de as_of,
    # de esas interpretaciones, con la recepcion del reverso que anula cada pago si llego antes del
    # final de as_of: si llego despues, ese dia era un pago. Duplicados y ambiguos no son
    # movimientos: motor-pagos/v1 ya los dejo fuera.
    "CREATE TEMP TABLE dv2_movimiento ON COMMIT DROP AS "
    "SELECT u.cuenta, m.movimiento_id, m.tipo_movimiento AS tipo, m.fecha_recepcion, "
    "m.monto_reportado AS monto, m.cuenta_canonica_id IS NOT DISTINCT FROM u.cuenta AS "
    "conciliado, rv.fecha_recepcion AS anulado_en "
    "FROM dv2_interpretacion i "
    "JOIN movimiento_economico_canonico m ON m.ejecucion_motor_pagos_id = i.id "
    "JOIN dv2_universo u ON u.cliente_unico = m.cliente_unico "
    "LEFT JOIN movimiento_economico_canonico rv ON m.anulado_por_movimiento_id IS NOT NULL "
    "AND rv.movimiento_id = m.anulado_por_movimiento_id "
    "AND rv.ejecucion_motor_pagos_id IN (SELECT id FROM dv2_interpretacion) "
    "AND rv.fecha_recepcion < :fin_local "
    "WHERE m.despacho_id = :despacho AND m.cartera_id = :cartera "
    "AND m.tipo_movimiento IN ('PAGO', 'POSIBLE_REVERSO') AND m.fecha_recepcion < :fin_local",
    "CREATE INDEX ON dv2_movimiento (cuenta, fecha_recepcion)",
    "CREATE INDEX ON dv2_movimiento (movimiento_id)",
    "ANALYZE dv2_movimiento",
    # Por cuenta: el ultimo pago no anulado, cuantos en 7 y 30 dias, y la recuperacion neta
    # interpretada de motor-pagos/v1 en cada ventana: los PAGO no anulados mas los POSIBLE_REVERSO,
    # que restan. Un REVERSO y el pago que anula suman cero y no entran.
    "CREATE TEMP TABLE dv2_f_pago ON COMMIT DROP AS "
    "SELECT cuenta, "
    "max(fecha_recepcion) FILTER (WHERE tipo = 'PAGO' AND anulado_en IS NULL) AS ultimo_pago, "
    "count(*) FILTER (WHERE tipo = 'PAGO' AND anulado_en IS NULL "
    "AND fecha_recepcion >= :inicio_7d_local) AS pagos_7d, "
    "count(*) FILTER (WHERE tipo = 'PAGO' AND anulado_en IS NULL "
    "AND fecha_recepcion >= :inicio_30d_local) AS pagos_30d, "
    + ", ".join(
        f"sum(monto) FILTER (WHERE anulado_en IS NULL AND fecha_recepcion >= :inicio_{n}d_local) "
        f"AS recuperacion_{n}d"
        for n in VENTANAS_DE_PAGOS
    )
    + " FROM dv2_movimiento GROUP BY cuenta",
)

SQL_PROMESAS = (
    # Las promesas de las cuentas del universo acordadas antes del final de as_of y registradas
    # hasta known_at, que vencen en los ultimos 90 dias o despues, o se acordaron en ellos: las
    # unicas que cuentan en alguna feature. Con lo que evaluacion-promesa/v1 necesita de cada una, a
    # esa frontera.
    "CREATE TEMP TABLE dv2_promesa ON COMMIT DROP AS "
    "SELECT p.id, p.cuenta_canonica_id AS cuenta, p.monto_prometido AS monto, "
    "e.ocurrido_en AS creada_en, p.fecha_limite, "
    "timezone(:zona, (p.fecha_limite + 1)::timestamp) AS fin_limite, "
    "least(timezone(:zona, (p.fecha_limite + 1)::timestamp), CAST(:fin AS timestamptz)) AS fin, "
    f"NOT {_sin_anular('g.evento_lifecycle_id')} AS anulada, "
    f"{_cierre('p.evento_lifecycle_id', 'PROMESA_CANCELADA')} AS cancelada_en "
    "FROM dv2_universo u JOIN promesa_pago p ON p.cuenta_canonica_id = u.cuenta "
    "JOIN evento_lifecycle e ON e.id = p.evento_lifecycle_id "
    "JOIN gestion_cobranza g ON g.id = p.gestion_cobranza_id "
    "WHERE e.ocurrido_en < :fin AND e.registrado_en <= :known_at "
    "AND (p.fecha_limite >= CAST(:as_of AS date) - 89 OR e.ocurrido_en >= :inicio_90d)",
    "ANALYZE dv2_promesa",
    # Los movimientos de cada promesa, como los lee evaluacion-promesa/v1: los conciliados con su
    # cuenta, recibidos desde que se acordo hasta el final de su intervalo.
    "CREATE TEMP TABLE dv2_mov_promesa ON COMMIT DROP AS "
    "SELECT pr.id AS promesa, m.movimiento_id, m.tipo, m.fecha_recepcion, m.monto, "
    "timezone(:zona, m.anulado_en) AS anulado_en "
    "FROM dv2_promesa pr JOIN dv2_movimiento m ON m.cuenta = pr.cuenta AND m.conciliado "
    "AND m.fecha_recepcion >= (pr.creada_en AT TIME ZONE :zona) "
    "AND m.fecha_recepcion < (pr.fin AT TIME ZONE :zona)",
    # Los pagos observados conocidos a known_at: los de los datasets cuya historia se materializo
    # hasta entonces.
    "CREATE TEMP TABLE dv2_dataset_pagos ON COMMIT DROP AS "
    "SELECT DISTINCT dataset_conformado_id AS id FROM ejecucion_historia "
    "WHERE tipo_fuente = 'PAGOS' AND estado = 'EXITOSA' AND terminada_en <= :known_at",
)

SQL_EVALUACION_DE_PROMESAS = (
    # El corte de la evaluacion: el final de as_of y el horizonte de los pagos conocidos, sin pasar
    # de as_of. Un pago observado despues de as_of no certifica nada de este dia.
    "CREATE TEMP TABLE dv2_corte_ev ON COMMIT DROP AS SELECT CAST(:fin AS timestamptz) "
    "AS fin_as_of, timezone(:zona, CAST(:horizonte AS timestamp)) AS horizonte",
    # Las promesas cuyo intervalo toca un mes con pagos observados que su interpretacion vigente
    # no vio.
    "CREATE TEMP TABLE dv2_sin_interpretar ON COMMIT DROP AS "
    "SELECT DISTINCT pr.id AS promesa FROM dv2_promesa pr JOIN dv2_mes_pagos x "
    "ON NOT x.interpretado AND x.mes::timestamp < (pr.fin AT TIME ZONE :zona) "
    "AND (x.mes + interval '1 month') > (pr.creada_en AT TIME ZONE :zona)",
    # El estado de cada promesa, con las reglas de evaluacion-promesa/v1, escritas una sola vez.
    "CREATE TEMP TABLE dv2_estado_promesa ON COMMIT DROP AS SELECT c.promesa AS id, c.estado FROM ("
    + sql_conclusiones("dv2_promesa", "dv2_mov_promesa", "dv2_sin_interpretar", "dv2_corte_ev")
    + ") c",
    # Por cuenta: si hay una PENDIENTE (vigente), cuantas se acordaron en 30 y 90 dias (sin las de
    # una gestion anulada) y, de las que vencieron en la ventana, cuantas se cumplieron, se
    # cumplieron en parte o se incumplieron.
    "CREATE TEMP TABLE dv2_f_promesa ON COMMIT DROP AS "
    "SELECT pr.cuenta, "
    f"bool_or(ep.estado = '{EstadoEvaluacion.PENDIENTE}') AS promesa_vigente, "
    "count(*) FILTER (WHERE NOT pr.anulada AND pr.creada_en >= :inicio_30d) AS promesas_30d, "
    "count(*) FILTER (WHERE NOT pr.anulada AND pr.creada_en >= :inicio_90d) AS promesas_90d, "
    f"count(*) FILTER (WHERE ep.estado = '{EstadoEvaluacion.CUMPLIDA}' "
    "AND pr.fecha_limite >= CAST(:as_of AS date) - 89) AS cumplidas_90d, "
    f"count(*) FILTER (WHERE ep.estado = '{EstadoEvaluacion.PARCIAL}' "
    "AND pr.fecha_limite >= CAST(:as_of AS date) - 89 AND pr.fecha_limite <= :as_of) "
    "AS parciales_90d, "
    f"count(*) FILTER (WHERE ep.estado = '{EstadoEvaluacion.INCUMPLIDA}' "
    "AND pr.fecha_limite >= CAST(:as_of AS date) - 29) AS incumplidas_30d, "
    f"count(*) FILTER (WHERE ep.estado = '{EstadoEvaluacion.INCUMPLIDA}' "
    "AND pr.fecha_limite >= CAST(:as_of AS date) - 89) AS incumplidas_90d "
    "FROM dv2_promesa pr JOIN dv2_estado_promesa ep ON ep.id = pr.id GROUP BY pr.cuenta",
)

SQL_ATRIBUCION = (
    # La atribucion de cada mes vigente a known_at.
    "CREATE TEMP TABLE dv2_atribucion ON COMMIT DROP AS "
    "SELECT DISTINCT ON (periodo_desde) id, periodo_desde, ejecucion_motor_pagos_id "
    "FROM ejecucion_atribucion WHERE despacho_id = :despacho AND cartera_id = :cartera "
    "AND version_atribucion = :version_atribucion AND estado = 'EXITOSA' "
    "AND terminada_en <= :known_at AND periodo_desde <= :as_of "
    "ORDER BY periodo_desde, id DESC",
    # Por cuenta: los pagos con ASOCIACION_UNICA de los ultimos 90 dias, no anulados a as_of, cuya
    # gestion no se sabia anulada a known_at. AMBIGUA no cuenta: no se elige entre candidatas.
    "CREATE TEMP TABLE dv2_f_atribucion ON COMMIT DROP AS "
    "SELECT m.cuenta, "
    "count(*) FILTER (WHERE m.fecha_recepcion >= :inicio_30d_local) AS pagos_30d, "
    "sum(m.monto) FILTER (WHERE m.fecha_recepcion >= :inicio_30d_local) AS monto_30d, "
    "percentile_cont(0.5) WITHIN GROUP (ORDER BY c.antelacion_segundos) AS mediana_segundos "
    "FROM dv2_atribucion av "
    "JOIN atribucion_movimiento am ON am.ejecucion_atribucion_id = av.id "
    "AND am.clasificacion = 'ASOCIACION_UNICA' AND am.fecha_recepcion >= :inicio_90d_local "
    "AND am.fecha_recepcion < :fin_local "
    "JOIN dv2_movimiento m ON m.movimiento_id = am.movimiento_id AND m.tipo = 'PAGO' "
    "AND m.anulado_en IS NULL "
    "JOIN candidato_atribucion c ON c.ejecucion_atribucion_id = am.ejecucion_atribucion_id "
    "AND c.movimiento_economico_canonico_id = am.movimiento_economico_canonico_id "
    "AND c.gestion_cobranza_id = am.gestion_cobranza_id "
    "JOIN gestion_cobranza g ON g.id = am.gestion_cobranza_id "
    f"WHERE {_sin_anular('g.evento_lifecycle_id')} GROUP BY m.cuenta",
)


def _desde_los_meses(s: Session, parametros: dict[str, Any]) -> date:
    """El primer mes que importa para los pagos: el del inicio de la ventana de 90 dias o el de la
    promesa mas antigua que se evalua, si es antes."""
    primera = s.execute(
        text("SELECT min(creada_en AT TIME ZONE :zona) FROM dv2_promesa"), parametros
    ).scalar_one()
    desde = parametros["inicio_90d_local"]
    if primera is not None and primera < desde:
        desde = primera
    return periodo_de(desde)


def _observados(s: Session, parametros: dict[str, Any]) -> dict[str, Any]:
    """Los pagos observados conocidos a known_at y recibidos antes del final de as_of: por mes
    desde `desde_meses` (en dv2_mes_pagos), cuantos hay y cuantos clasifico la interpretacion
    vigente de su mes; el primero y el ultimo (el horizonte).

    Un mes esta interpretado si su interpretacion vigente tiene un resultado para cada uno de esos
    pagos, uno por uno: no basta comparar conteos, porque la interpretacion cuenta el mes entero, y
    pagos posteriores a as_of que si vio podrian tapar uno anterior que llego despues y no vio."""
    s.execute(
        text(
            "CREATE TEMP TABLE dv2_mes_pagos ON COMMIT DROP AS "
            "SELECT date_trunc('month', p.fecha_recepcion)::date AS mes, count(*) AS observados, "
            "count(r.source_row) AS interpretados, count(*) = count(r.source_row) AS interpretado "
            "FROM pago_observado p "
            "LEFT JOIN dv2_interpretacion i "
            "ON i.periodo_desde = date_trunc('month', p.fecha_recepcion)::date "
            "LEFT JOIN resultado_pago_observado r ON r.ejecucion_motor_pagos_id = i.id "
            "AND r.dataset_conformado_id = p.dataset_conformado_id AND r.source_row = p.source_row "
            "WHERE p.despacho_id = :despacho AND p.cartera_id = :cartera "
            "AND p.fecha_recepcion >= :desde_meses AND p.fecha_recepcion < :fin_local "
            "AND p.dataset_conformado_id IN (SELECT id FROM dv2_dataset_pagos) GROUP BY 1"
        ),
        {**parametros, "desde_meses": datetime.combine(parametros["desde_meses"], hora())},
    )
    extremos = s.execute(
        text(
            "SELECT (SELECT p.fecha_recepcion FROM pago_observado p WHERE p.despacho_id = "
            ":despacho AND p.cartera_id = :cartera AND p.fecha_recepcion < :fin_local AND "
            "p.dataset_conformado_id IN (SELECT id FROM dv2_dataset_pagos) "
            "ORDER BY p.fecha_recepcion LIMIT 1), "
            "(SELECT p.fecha_recepcion FROM pago_observado p WHERE p.despacho_id = :despacho "
            "AND p.cartera_id = :cartera AND p.fecha_recepcion < :fin_local AND "
            "p.dataset_conformado_id IN (SELECT id FROM dv2_dataset_pagos) "
            "ORDER BY p.fecha_recepcion DESC LIMIT 1)"
        ),
        parametros,
    ).one()
    return {"primero": extremos[0], "horizonte": extremos[1]}


def _disponibilidad(
    s: Session, ejecucion: EjecucionDecisionV2, observados: dict[str, Any]
) -> dict[str, Any]:
    """Que informacion de pagos y de atribucion estaba disponible a known_at, mes por mes de los
    ultimos 90 dias, y por ventana.

    Un mes esta cubierto para pagos si cae entre el primer y el ultimo pago observado conocido, y su
    interpretacion vigente vio todos sus pagos observados conocidos (un mes sin pagos dentro de ese
    rango esta cubierto: no hubo pagos). Para atribucion, ademas, su atribucion vigente leyo esa
    misma interpretacion. Una ventana esta disponible si cada mes que toca esta cubierto; si no, sus
    features son NULL, nunca cero."""
    as_of = ejecucion.as_of
    meses = []
    mes = periodo_de(as_of - timedelta(days=89))
    while mes <= as_of:
        meses.append(mes)
        mes = siguiente_periodo(mes)
    por_mes = {
        fila.mes: fila
        for fila in s.execute(
            text("SELECT mes, observados, interpretados, interpretado FROM dv2_mes_pagos")
        ).all()
    }
    interpretaciones = dict(
        s.execute(text("SELECT periodo_desde, id FROM dv2_interpretacion")).all()
    )
    atribuciones = {
        fila.periodo_desde: fila
        for fila in s.execute(
            text("SELECT periodo_desde, id, ejecucion_motor_pagos_id FROM dv2_atribucion")
        ).all()
    }
    primero, horizonte = observados["primero"], observados["horizonte"]
    detalle = []
    for mes in meses:
        visto = por_mes.get(mes)
        cuantos = 0 if visto is None else visto.observados
        en_rango = primero is not None and periodo_de(primero) <= mes <= periodo_de(horizonte)
        pagos = en_rango and (cuantos == 0 or bool(visto.interpretado))
        atribucion = atribuciones.get(mes)
        atribuido = pagos and (
            cuantos == 0
            or (
                atribucion is not None
                and atribucion.ejecucion_motor_pagos_id == interpretaciones.get(mes)
            )
        )
        detalle.append(
            {
                "mes": mes.isoformat(),
                "pagos_observados": cuantos,
                "pagos_interpretados": None if visto is None else visto.interpretados,
                "pagos": pagos,
                "atribucion": atribuido,
            }
        )
    cubiertos = {date.fromisoformat(m["mes"]): m for m in detalle}

    def disponible(clave: str, dias: int) -> bool:
        desde = periodo_de(as_of - timedelta(days=dias - 1))
        return all(m[clave] for mes, m in cubiertos.items() if mes >= desde)

    return {
        "pagos": {f"{n}d": disponible("pagos", n) for n in VENTANAS_DE_PAGOS},
        "atribucion": {f"{n}d": disponible("atribucion", n) for n in VENTANAS_DE_ATRIBUCION},
        "primer_pago_observado": None if primero is None else primero.isoformat(),
        "horizonte_pagos": None if horizonte is None else horizonte.isoformat(),
        "meses": detalle,
    }


# --- lo que se publica ----------------------------------------------------------------------------

TIPO_SQL: dict[str, str] = {
    "saldo_total": "numeric(14, 2)",
    "saldo_atrasado": "numeric(14, 2)",
    "saldo_requerido": "numeric(14, 2)",
    "moratorios": "numeric(14, 2)",
    "dias_atraso": "bigint",
    "semanas_atraso": "bigint",
    "producto": "varchar(20)",
    "estrategia": "varchar",
    "estatus_plan": "varchar",
    "delta_saldo_total_desde_corte_anterior": "numeric(14, 2)",
    "delta_dias_atraso_desde_corte_anterior": "bigint",
    "continuo_desde_corte_anterior": "boolean",
    "tasa_contacto_titular_30d": "numeric(5, 4)",
    "ultima_gestion_canal": "varchar(12)",
    "ultima_gestion_nivel_contacto": "varchar(20)",
    "promesa_vigente": "boolean",
    "convenio_vigente": "boolean",
    "recuperacion_interpretada_7d": "numeric(18, 2)",
    "recuperacion_interpretada_30d": "numeric(18, 2)",
    "recuperacion_interpretada_90d": "numeric(18, 2)",
    "recuperacion_asociada_unica_30d": "numeric(18, 2)",
    "dias_gestion_a_pago_mediana_90d": "numeric(7, 2)",
}
"""El tipo de cada feature en su columna; las demas son integer. Cada valor se convierte a el antes
de escribir su forma canonica, para que el hash sea el de lo que se guarda."""

EXPRESION: dict[str, str] = {
    "saldo_total": "u.saldo_total",
    "saldo_atrasado": "u.saldo_atrasado",
    "saldo_requerido": "u.saldo_requerido",
    "moratorios": "u.moratorios",
    "dias_atraso": "u.dias_atraso",
    "semanas_atraso": "u.semanas_atraso",
    "producto": "u.producto",
    "estrategia": "u.estrategia",
    "estatus_plan": "u.estatus_plan",
    "cortes_observados": "h.cortes_observados",
    "reingresos_observados": "h.reingresos_observados",
    "delta_saldo_total_desde_corte_anterior": "h.delta_saldo",
    "delta_dias_atraso_desde_corte_anterior": "h.delta_dias",
    "continuo_desde_corte_anterior": "h.continuo",
    "dias_desde_ultima_gestion": "CAST(:as_of AS date) - (g.ultima AT TIME ZONE :zona)::date",
    "gestiones_7d": "coalesce(g.gestiones_7d, 0)",
    "gestiones_30d": "coalesce(g.gestiones_30d, 0)",
    "gestiones_sin_contacto_7d": "coalesce(g.sin_contacto_7d, 0)",
    "contactos_titular_30d": "coalesce(g.titular_30d, 0)",
    "contactos_tercero_30d": "coalesce(g.tercero_30d, 0)",
    "visitas_30d": "coalesce(g.visitas_30d, 0)",
    "gestiones_contactables_30d": "coalesce(g.contactables_30d, 0)",
    "tasa_contacto_titular_30d": "CASE WHEN coalesce(g.contactables_30d, 0) > 0 "
    "THEN round(g.titular_30d::numeric / g.contactables_30d, 4) END",
    "ultima_gestion_canal": "g.ultima_canal",
    "ultima_gestion_nivel_contacto": "g.ultima_nivel",
    "promesa_vigente": "coalesce(p.promesa_vigente, false)",
    "promesas_30d": "coalesce(p.promesas_30d, 0)",
    "promesas_90d": "coalesce(p.promesas_90d, 0)",
    "promesas_cumplidas_90d": "coalesce(p.cumplidas_90d, 0)",
    "promesas_parciales_90d": "coalesce(p.parciales_90d, 0)",
    "promesas_incumplidas_30d": "coalesce(p.incumplidas_30d, 0)",
    "promesas_incumplidas_90d": "coalesce(p.incumplidas_90d, 0)",
    "convenio_vigente": "coalesce(c.convenio_vigente, false)",
    "convenios_90d": "coalesce(c.convenios_90d, 0)",
    "dias_desde_ultimo_pago": "CASE WHEN :pagos_90d THEN "
    "CAST(:as_of AS date) - pg.ultimo_pago::date END",
    "movimientos_pago_7d": "CASE WHEN :pagos_7d THEN coalesce(pg.pagos_7d, 0) END",
    "movimientos_pago_30d": "CASE WHEN :pagos_30d THEN coalesce(pg.pagos_30d, 0) END",
    "recuperacion_interpretada_7d": "CASE WHEN :pagos_7d THEN coalesce(pg.recuperacion_7d, 0) END",
    "recuperacion_interpretada_30d": "CASE WHEN :pagos_30d "
    "THEN coalesce(pg.recuperacion_30d, 0) END",
    "recuperacion_interpretada_90d": "CASE WHEN :pagos_90d "
    "THEN coalesce(pg.recuperacion_90d, 0) END",
    "pagos_asociados_unicos_30d": "CASE WHEN :atribucion_30d THEN coalesce(a.pagos_30d, 0) END",
    "recuperacion_asociada_unica_30d": "CASE WHEN :atribucion_30d "
    "THEN coalesce(a.monto_30d, 0) END",
    "dias_gestion_a_pago_mediana_90d": "CASE WHEN :atribucion_90d "
    "THEN round((a.mediana_segundos / 86400)::numeric, 2) END",
}
"""De donde sale cada feature en el ensamble. Un conteo del lifecycle sin eventos es 0; una feature
de pagos o de atribucion de una ventana no disponible es NULL, y tambien una distancia sin evento
del que medirla."""


def sql_canonico(feature: Feature, columna: str) -> str:
    """La forma canonica de una feature en SQL: exactamente `features.texto`."""
    if feature.tipo == Tipo.TEXTO:
        return f"coalesce('s' || length({columna}) || ':' || {columna}, '{NULO}')"
    return f"coalesce({columna}::text, '{NULO}')"


def _sql_features() -> str:
    columnas = ", ".join(f.nombre for f in FEATURES)
    valores = ",\n    ".join(
        f"CAST({EXPRESION[f.nombre]} AS {TIPO_SQL.get(f.nombre, 'integer')}) AS {f.nombre}"
        for f in FEATURES
    )
    canonica = " || chr(10) || ".join(
        [f"'{VERSION_FEATURES}'"]
        + [f"'{f.nombre}=' || {sql_canonico(f, 'x.' + f.nombre)}" for f in FEATURES]
    )
    return f"""
INSERT INTO feature_decision_cuenta (ejecucion_decision_v2_id, cuenta_canonica_id,
    corte_canonico_id, {columnas}, feature_hash)
SELECT :ejecucion, x.cuenta, :corte, {", ".join("x." + f.nombre for f in FEATURES)},
    sha256(convert_to({canonica}, 'UTF8'))
FROM (
  SELECT u.cuenta,
    {valores}
  FROM dv2_universo u
  LEFT JOIN dv2_historia h ON h.cuenta = u.cuenta
  LEFT JOIN dv2_f_gestion g ON g.cuenta = u.cuenta
  LEFT JOIN dv2_f_promesa p ON p.cuenta = u.cuenta
  LEFT JOIN dv2_f_convenio c ON c.cuenta = u.cuenta
  LEFT JOIN dv2_f_pago pg ON pg.cuenta = u.cuenta
  LEFT JOIN dv2_f_atribucion a ON a.cuenta = u.cuenta
) x
ORDER BY x.cuenta
"""


SQL_FEATURES = _sql_features()
"""Las features de cada cuenta del universo, con su feature_hash, en el orden de la cuenta."""


def _literal(condicion: Condicion) -> str:
    umbral = condicion.umbral
    if isinstance(umbral, bool):
        return "true" if umbral else "false"
    if isinstance(umbral, Decimal):
        return f"CAST('{umbral}' AS numeric)"
    return str(int(umbral))


def _sql_condicion(condicion: Condicion) -> str:
    """La condicion en SQL; un NULL no la cumple."""
    return (
        f"coalesce(f.{condicion.feature} {condicion.operador.value} {_literal(condicion)}, false)"
    )


def _sql_valor(feature: Feature, columna: str) -> str:
    """El valor de una feature como lo muestra un motivo: su forma canonica, o null."""
    if feature.tipo == Tipo.TEXTO:
        return f"'s' || length({columna}) || ':' || {columna}"
    return f"{columna}::text"


def _sql_caso(campo: str, valores: dict, *, texto_valor: bool = True) -> str:
    casos = " ".join(
        f"WHEN '{clave}' THEN " + (f"'{valor}'" if texto_valor else f"{valor}")
        for clave, valor in valores.items()
    )
    return f"CASE {campo} {casos} END"


def sql_resultados(politica: Politica) -> str:
    """La decision de cada cuenta de la ejecucion con `politica`, en un INSERT ... SELECT generado
    de su definicion, paso por paso como se explica: el tramo de los dias de atraso, su segmento y
    su prioridad base (las de decision/v1), el efecto de cada modificador evaluado aparte, su suma,
    el nivel acotado a la escala, la prioridad final, el canal, los motivos y el decision_hash. Es
    exactamente `politica.decidir` y `politica.decision_hash`; una prueba compara las dos."""
    tramo = (
        "CASE "
        + " ".join(
            f"WHEN f.dias_atraso BETWEEN {desde} AND {hasta} THEN '{etiqueta}'"
            if hasta is not None
            else f"WHEN f.dias_atraso >= {desde} THEN '{etiqueta}'"
            for etiqueta, desde, hasta in TRAMOS_ATRASO
        )
        + " END"
    )
    etiquetas = [etiqueta for etiqueta, _, _ in TRAMOS_ATRASO]
    segmento = {t: SEGMENTO_POR_TRAMO[t].value for t in etiquetas}
    base = {t: PRIORIDAD_BASE[SEGMENTO_POR_TRAMO[t]].value for t in etiquetas}
    nivel_base = {t: NIVEL[PRIORIDAD_BASE[SEGMENTO_POR_TRAMO[t]]] for t in etiquetas}
    prioridad = (
        "CASE b2.nivel_final "
        + " ".join(f"WHEN {nivel} THEN '{p.value}'" for p, nivel in NIVEL.items())
        + " END"
    )
    canal = _sql_caso("b3.final", {p.value: c for p, c in CANAL_POR_PRIORIDAD.items()})
    modificadores = list(enumerate(politica.modificadores))
    salto = ",\n        "
    efectos = salto.join(
        f"CASE WHEN {' AND '.join(_sql_condicion(c) for c in m.condiciones)} "
        f"THEN {m.efecto} ELSE 0 END AS e{i}"
        for i, m in modificadores
    )
    valores = "".join(
        f"{salto}{_sql_valor(_feature(c.feature), 'f.' + c.feature)} AS v{i}_{j}"
        for i, m in modificadores
        for j, c in enumerate(m.condiciones)
    )
    suma = " + ".join(f"b0.e{i}" for i, _ in modificadores) or "0"
    aplicadas = "".join(
        f" || CASE WHEN b0.e{i} <> 0 THEN jsonb_build_array(jsonb_build_object('codigo', "
        f"'{m.codigo.value}', 'condiciones', jsonb_build_array("
        + ", ".join(
            f"jsonb_build_object('feature', '{c.feature}', 'valor', b0.v{i}_{j}, 'operador', "
            f"'{c.operador.value}', 'umbral', '{c.umbral_canonico}')"
            for j, c in enumerate(m.condiciones)
        )
        + f"), 'efecto', b0.e{i})) ELSE '[]'::jsonb END"
        for i, m in modificadores
    )
    motivos = (
        "jsonb_build_array(jsonb_build_object("
        f"'codigo', '{CodigoMotivo.PRIORIDAD_BASE_MORA.value}', 'condiciones', "
        "jsonb_build_array(jsonb_build_object('feature', 'dias_atraso', 'valor', "
        f"b0.dias_atraso::text, 'operador', '{EN_TRAMO}', 'umbral', b0.tramo)), "
        "'segmento', b1.segmento, 'nivel', b1.nivel_base, 'prioridad', b1.base))"
        f"{aplicadas}"
        " || jsonb_build_array(jsonb_build_object("
        f"'codigo', '{CodigoMotivo.PRIORIDAD_FINAL.value}', 'nivel_base', b1.nivel_base, "
        "'ajuste_total', b1.ajuste, 'nivel_sin_acotar', b1.nivel_base + b1.ajuste, "
        "'nivel', b2.nivel_final, 'prioridad', b3.final, "
        "'acotada', b1.nivel_base + b1.ajuste <> b2.nivel_final), jsonb_build_object("
        f"'codigo', '{CodigoMotivo.CANAL_POR_PRIORIDAD.value}', 'prioridad', b3.final, "
        "'canal', b4.canal))"
    )
    canonica = " || chr(10) || ".join(
        [
            f"'{VERSION_MOTOR}'",
            ":version_features",
            "encode(b0.feature_hash, 'hex')",
            ":version_politica",
            ":hash_politica",
            "b1.base",
            *(f"'{m.codigo.value}=' || b0.e{i}" for i, m in modificadores),
            "b1.ajuste::text",
            "b3.final",
            "b4.canal",
        ]
    )
    return f"""
INSERT INTO resultado_decision_v2 (ejecucion_decision_v2_id, cuenta_canonica_id, score_nivel_base,
    ajuste_total, score_nivel_final, prioridad_base, prioridad_final, canal_recomendado, motivos,
    decision_hash)
SELECT :ejecucion, b0.cuenta, b1.nivel_base, b1.ajuste, b2.nivel_final, b1.base, b3.final,
    b4.canal,
    {motivos},
    sha256(convert_to({canonica}, 'UTF8'))
FROM (
    SELECT f.cuenta_canonica_id AS cuenta, f.dias_atraso, f.feature_hash,
        {tramo} AS tramo,
        {efectos}{valores}
    FROM feature_decision_cuenta f
    WHERE f.ejecucion_decision_v2_id = :ejecucion
) b0
CROSS JOIN LATERAL (
    SELECT {_sql_caso("b0.tramo", segmento)} AS segmento,
        {_sql_caso("b0.tramo", base)} AS base,
        {_sql_caso("b0.tramo", nivel_base, texto_valor=False)} AS nivel_base,
        {suma} AS ajuste
) b1
CROSS JOIN LATERAL (
    SELECT least({NIVEL_MAXIMO}, greatest({NIVEL_MINIMO}, b1.nivel_base + b1.ajuste)) AS nivel_final
) b2
CROSS JOIN LATERAL (SELECT {prioridad} AS final) b3
CROSS JOIN LATERAL (SELECT {canal} AS canal) b4
ORDER BY b0.cuenta
"""


def _feature(nombre: str) -> Feature:
    return next(f for f in FEATURES if f.nombre == nombre)


def _contar(s: Session, ejecucion: EjecucionDecisionV2, avance: _Avance) -> dict[str, Any]:
    """Comprueba que haya exactamente unas features y una decision por cada cuenta del corte, y
    cuenta lo que se publico, en la misma transaccion."""
    objetivo = avance.objetivo
    if objetivo != ejecucion.cuentas_objetivo:
        raise _NoSePublica(
            ResultadoEjecucionDecisionV2.DATOS_INCONSISTENTES,
            f"El corte dice {ejecucion.cuentas_objetivo:,} cuentas y tiene {objetivo:,} "
            "snapshots; no se publico nada.",
        )
    features, resultados = s.execute(
        text(
            "SELECT (SELECT count(*) FROM feature_decision_cuenta WHERE "
            "ejecucion_decision_v2_id = :ejecucion), (SELECT count(*) FROM resultado_decision_v2 "
            "WHERE ejecucion_decision_v2_id = :ejecucion)"
        ),
        {"ejecucion": ejecucion.id},
    ).one()
    if not features == resultados == objetivo:
        raise _NoSePublica(
            ResultadoEjecucionDecisionV2.DATOS_INCONSISTENTES,
            f"El corte tiene {objetivo:,} cuentas y se guardaron {features:,} features y "
            f"{resultados:,} decisiones; no se publico ninguna.",
        )
    codigos = [m.codigo.value for m in PUBLIC_V1.modificadores]
    filas = s.execute(
        text(
            "SELECT prioridad_final, canal_recomendado, ajuste_total, "
            + ", ".join(f"motivos @> CAST(:m{i} AS jsonb)" for i in range(len(codigos)))
            + ", count(*) FROM resultado_decision_v2 WHERE ejecucion_decision_v2_id = :ejecucion "
            "GROUP BY 1, 2, 3, " + ", ".join(str(4 + i) for i in range(len(codigos)))
        ),
        {
            "ejecucion": ejecucion.id,
            **{f"m{i}": f'[{{"codigo": "{c}"}}]' for i, c in enumerate(codigos)},
        },
    ).all()
    prioridades: Counter = Counter()
    canales: Counter = Counter()
    ajustes: Counter = Counter()
    modificadores: Counter = Counter()
    for prioridad, canal, ajuste, *aplicados, cuantas in filas:
        prioridades[prioridad] += cuantas
        canales[canal] += cuantas
        ajustes[str(ajuste)] += cuantas
        for codigo, aplicado in zip(codigos, aplicados, strict=True):
            modificadores[codigo] += cuantas if aplicado else 0
    return {
        "cuentas": resultados,
        "prioridad_final": {p.value: prioridades[p.value] for p in NIVEL},
        "canal_recomendado": dict(sorted(canales.items())),
        "ajuste_total": dict(sorted(ajustes.items(), key=lambda par: int(par[0]))),
        "modificadores": {codigo: modificadores[codigo] for codigo in codigos},
    }


def _cerrar(
    s: Session,
    ejecucion: EjecucionDecisionV2,
    avance: _Avance,
    distribucion: dict[str, Any],
) -> None:
    """EXITOSA, con sus conteos, su disponibilidad y su distribucion, en la transaccion que publico.
    Envia el cambio pero no confirma: una carrera con otra EXITOSA de la misma firma se descubre
    aqui, todavia dentro de la transaccion."""
    cuentas = distribucion["cuentas"]
    prioridades = ", ".join(f"{n:,} {p}" for p, n in distribucion["prioridad_final"].items())
    aplicados = ", ".join(f"{c} {n:,}" for c, n in distribucion["modificadores"].items())
    ejecucion.estado = EstadoDecisionV2.EXITOSA
    ejecucion.resultado = ResultadoEjecucionDecisionV2.DECISION_PUBLICADA.value
    ejecucion.cuentas_decididas = cuentas
    ejecucion.cuentas_fallidas = 0
    ejecucion.disponibilidad = avance.disponibilidad
    ejecucion.distribucion = distribucion
    ejecucion.detalle = (
        f"Se decidieron {cuentas:,} cuentas del corte del {ejecucion.as_of.isoformat()} con lo que "
        f"se conocia hasta {ejecucion.known_at.astimezone(UTC).isoformat()} (UTC), con "
        f"{ejecucion.version_politica} sobre {ejecucion.version_features}: {prioridades}. "
        f"Modificadores aplicados: {aplicados}. Reglas deterministas sobre datos sinteticos: no "
        f"es un modelo predictivo. {VERSION_MOTOR}."
    )
    ejecucion.terminada_en = ahora()
    s.add(ejecucion)
    s.flush()


def _fallar(
    s: Session,
    ejecucion_id: int,
    etiqueta: UUID,
    resultado: ResultadoEjecucionDecisionV2,
    motivo: str,
    avance: _Avance,
) -> None:
    """FALLIDA, con su fila todavia bloqueada, solo si quien la ejecuta sigue siendo el dueno de su
    trabajo; si no, TrabajoAjeno sin cambiar nada. No publica nada: lo de este intento ya lo quito
    el rollback del savepoint."""
    try:
        cola.confirmar_dueno(s)
    except cola.TrabajoAjeno:
        s.rollback()
        log.warning("decision v2 %s: su trabajo ya no es de este worker: %s", etiqueta, motivo)
        raise
    registrado = s.execute(
        update(EjecucionDecisionV2)
        .where(
            EjecucionDecisionV2.id == ejecucion_id,
            EjecucionDecisionV2.estado == EstadoDecisionV2.EN_PROCESO,
        )
        .values(
            estado=EstadoDecisionV2.FALLIDA,
            resultado=resultado.value,
            cuentas_decididas=0,
            cuentas_fallidas=avance.fallidas,
            disponibilidad=avance.disponibilidad,
            detalle=motivo,
            terminada_en=ahora(),
        )
        .execution_options(synchronize_session=False)
    ).rowcount
    s.commit()
    if registrado:
        log.warning("decision v2 %s fallida (%s): %s", etiqueta, resultado.value, motivo)
