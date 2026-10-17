"""Una ejecucion de optimization/v1: entradas declaradas, el plan optimo demostrado, todo o nada.

El orden:

1. `abrir` resuelve lo declarado: el scoring (EXITOSA, con su modelo PUBLICADA), la decision/v2
   (EXITOSA y del mismo corte), la configuracion (PUBLICADA) y, si la configuracion usa CAMPO o
   BASE, el ruteo/v2 (EXITOSA, cuyo plan de campo es de esa misma decision/v2, con la misma fecha
   de operacion). Nunca "el ultimo". Fail-closed: lo incompatible no se abre. Registra la ejecucion
   EN_PROCESO con su trabajo OPTIMIZATION, en la transaccion de quien llama.
2. Un worker toma el trabajo y llama a `optimizar`, con el candado de la ejecucion y sin una
   transaccion abierta mientras resuelve:
   - arma el universo y la elegibilidad por conjuntos, en una transaccion de lectura que se
     revierte (`sql`);
   - resuelve el modelo con su reduccion exacta y sus cuatro niveles (`formulacion`);
   - si el solver demostro el optimo de cada nivel, publica en una transaccion corta: los
     resultados por cuenta a una tabla de paso y de ahi con un INSERT ... SELECT, el uso de cada
     pool y cada restriccion, y cierra EXITOSA con el hash del plan, si su trabajo sigue siendo
     suyo. Si no, la cierra FALLIDA con su diagnostico, sin publicar ningun plan.

Un plan optimo es el del modelo de asignacion declarado: no dice que el canal asignado cause el
resultado predicho, y nada de esto ejecuta una accion. Ni la bitacora ni la ejecucion llevan el
valor o el score de una cuenta.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

import numpy as np
import pyarrow as pa
import pyarrow.csv as pacsv
from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session, select

from motor_cartera.db.copia import copiar, copiar_csv
from motor_cartera.db.modelos import (
    CorteCanonico,
    EjecucionCampoV2,
    EjecucionDecisionV2,
    EjecucionOptimizacion,
    EjecucionRuteoV2,
    EjecucionScoringPredictivo,
    EstadoCampoV2,
    EstadoDecisionV2,
    EstadoModeloPredictivo,
    EstadoOptimizacion,
    EstadoRuteoV2,
    EstadoScoringPredictivo,
    EstadoVersionOptimizacion,
    PoolCapacidadOptimizacion,
    ResultadoOptimizacion,
    TipoTrabajo,
    VersionConfiguracionOptimizacion,
    VersionModeloPredictivo,
    ahora,
)
from motor_cartera.db.sesion import insertar_en_savepoint, restriccion, sesion
from motor_cartera.geografia.ejecuciones import _traza
from motor_cartera.optimizacion import configuracion as configuracion_
from motor_cartera.optimizacion import formulacion, sql
from motor_cartera.optimizacion import solver as solver_
from motor_cartera.optimizacion.politica import (
    AVISO,
    POLITICAS,
    PUBLIC_V1,
    VERSION_MOTOR,
    Dimension,
    EstadoCuenta,
    EstadoSolver,
    Motivo,
    PoliticaOptimizacion,
    TipoAlcance,
    TipoRestriccion,
    hash_politica,
)
from motor_cartera.orquestacion import cola
from motor_cartera.predictivo import medicion
from motor_cartera.predictivo.candado import candado

log = logging.getLogger(__name__)

INDICE_EXITOSA = "ux_optimizacion_exitosa"
INDICE_EN_PROCESO = "ux_optimizacion_en_proceso"

COLUMNAS_RESULTADO = (
    "valor_oportunidad_centavos",
    "ejecucion_optimizacion_id",
    "cuenta_canonica_id",
    "corte_canonico_id",
    "version_configuracion_optimizacion_id",
    "pool_capacidad_optimizacion_id",
    "gestor_campo_id",
    "base_operativa_id",
    "orden_ruteo_original",
    "unidades_recurso",
    "p_contacto_14d",
    "estado",
    "motivo",
    "canal_decision",
    "canal_asignado",
    "prioridad",
    "producto",
    "atraso_bucket",
    "retenida",
)


# --- errores al abrir -----------------------------------------------------------------------------


class OptimizacionInvalida(Exception):
    """Una ejecucion que no se puede abrir: no registra nada. `codigo` es estable."""

    codigo = "OPTIMIZACION_INVALIDA"
    estado_http = 422


def _error(nombre: str, codigo: str, estado_http: int = 422) -> type[OptimizacionInvalida]:
    return type(nombre, (OptimizacionInvalida,), {"codigo": codigo, "estado_http": estado_http})


ScoringNoEncontrado = _error("ScoringNoEncontrado", "SCORING_NO_ENCONTRADO", 404)
ScoringNoExitoso = _error("ScoringNoExitoso", "SCORING_NO_EXITOSO", 409)
ModeloNoPublicado = _error("ModeloNoPublicado", "MODELO_NO_PUBLICADO", 409)
DecisionNoEncontrada = _error("DecisionNoEncontrada", "DECISION_NO_ENCONTRADA", 404)
DecisionNoExitosa = _error("DecisionNoExitosa", "DECISION_NO_EXITOSA", 409)
CortesDistintos = _error("CortesDistintos", "CORTES_DISTINTOS")
ConfiguracionNoEncontrada = _error("ConfiguracionNoEncontrada", "CONFIGURACION_NO_ENCONTRADA", 404)
ConfiguracionNoPublicada = _error("ConfiguracionNoPublicada", "CONFIGURACION_NO_PUBLICADA", 409)
ConfiguracionNoSoportada = _error("ConfiguracionNoSoportada", "CONFIGURACION_NO_SOPORTADA")
PoliticaNoSoportada = _error("PoliticaNoSoportada", "POLITICA_NO_SOPORTADA")
RuteoNoEncontrado = _error("RuteoNoEncontrado", "RUTEO_NO_ENCONTRADO", 404)
RuteoNoExitoso = _error("RuteoNoExitoso", "RUTEO_NO_EXITOSO", 409)
RuteoRequerido = _error("RuteoRequerido", "RUTEO_REQUERIDO")
RuteoDeOtraDecision = _error("RuteoDeOtraDecision", "RUTEO_DE_OTRA_DECISION")
FechaOperacionDistinta = _error("FechaOperacionDistinta", "FECHA_OPERACION_DISTINTA")
FechaAnteriorAlCorte = _error("FechaAnteriorAlCorte", "FECHA_ANTERIOR_AL_CORTE")


class OptimizacionYaPublicada(Exception):
    """Otra ejecucion ya publico exactamente lo mismo (la misma firma)."""

    def __init__(self, previa: EjecucionOptimizacion) -> None:
        super().__init__(
            f"Ese plan ya se publico: la ejecucion {previa.optimizacion_run_id} (firma "
            f"{previa.firma_inputs[:12]}...)."
        )
        self.previa = previa


class OptimizacionEnProceso(Exception):
    def __init__(self, activa: EjecucionOptimizacion) -> None:
        super().__init__(
            f"Ese plan ya se esta calculando: la ejecucion {activa.optimizacion_run_id}."
        )
        self.activa = activa


class _NoSePublica(Exception):
    def __init__(
        self, resultado: ResultadoOptimizacion, mensaje: str, *, extra: dict | None = None
    ) -> None:
        super().__init__(mensaje)
        self.resultado = resultado
        self.extra = extra or {}


# --- abrir ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Declarado:
    """Lo declarado por una ejecucion, ya resuelto y comprobado."""

    scoring: EjecucionScoringPredictivo
    modelo: VersionModeloPredictivo
    decision: EjecucionDecisionV2
    corte: CorteCanonico
    configuracion: VersionConfiguracionOptimizacion
    contenido: configuracion_.ConfiguracionOptimizacion
    ruteo: EjecucionRuteoV2 | None
    campo: EjecucionCampoV2 | None
    fecha_operacion: date


def firma_inputs(
    *,
    politica: PoliticaOptimizacion,
    scoring: EjecucionScoringPredictivo,
    modelo: VersionModeloPredictivo,
    decision: EjecucionDecisionV2,
    ruteo: EjecucionRuteoV2 | None,
    hash_configuracion: str,
    fecha_operacion: date,
    solver_fingerprint: str,
) -> str:
    """El SHA-256 de lo que fija un plan: el motor, la politica con su hash, el scoring declarado
    (con su firma y su modelo con su hash), la decision/v2 declarada con su firma, el ruteo/v2
    declarado con su firma (o ninguno), el hash de la configuracion, la fecha de operacion y la
    huella del solver. Identificadores publicos y firmas, nunca "el ultimo"."""
    lineas = [
        VERSION_MOTOR,
        politica.version,
        hash_politica(politica),
        str(scoring.scoring_run_id),
        scoring.firma_inputs,
        str(modelo.modelo_version_id),
        modelo.hash_modelo,
        str(decision.decision_run_id),
        decision.firma_inputs,
        "-" if ruteo is None else str(ruteo.ruteo_run_id),
        "-" if ruteo is None else ruteo.firma_inputs,
        hash_configuracion,
        fecha_operacion.isoformat(),
        solver_fingerprint,
    ]
    return hashlib.sha256("\n".join(lineas).encode("utf-8")).hexdigest()


def resolver_declarado(
    s: Session,
    *,
    scoring_run_id: UUID,
    decision_run_id: UUID,
    configuracion_id: UUID,
    fecha_operacion: date,
    ruteo_run_id: UUID | None,
) -> Declarado:
    """Lo declarado, comprobado: levanta un OptimizacionInvalida sin registrar nada."""
    scoring = s.exec(
        select(EjecucionScoringPredictivo).where(
            EjecucionScoringPredictivo.scoring_run_id == scoring_run_id
        )
    ).one_or_none()
    if scoring is None:
        raise ScoringNoEncontrado(f"No existe el scoring {scoring_run_id}.")
    if scoring.estado != EstadoScoringPredictivo.EXITOSA:
        raise ScoringNoExitoso(
            f"El scoring {scoring_run_id} esta {scoring.estado}: solo uno EXITOSA tiene sus "
            "predicciones publicadas."
        )
    modelo = s.get_one(VersionModeloPredictivo, scoring.version_modelo_predictivo_id)
    if modelo.estado != EstadoModeloPredictivo.PUBLICADA:
        raise ModeloNoPublicado(
            f"El modelo del scoring {scoring_run_id} esta {modelo.estado}: solo uno PUBLICADA."
        )
    decision = s.exec(
        select(EjecucionDecisionV2).where(EjecucionDecisionV2.decision_run_id == decision_run_id)
    ).one_or_none()
    if decision is None:
        raise DecisionNoEncontrada(f"No existe la decision/v2 {decision_run_id}.")
    if decision.estado != EstadoDecisionV2.EXITOSA:
        raise DecisionNoExitosa(
            f"La decision/v2 {decision_run_id} esta {decision.estado}: solo una EXITOSA."
        )
    corte = s.get_one(CorteCanonico, scoring.corte_canonico_id)
    if (
        decision.corte_canonico_id != scoring.corte_canonico_id
        or scoring.as_of != decision.as_of
        or decision.as_of != corte.fecha_corte
    ):
        otro = s.get_one(CorteCanonico, decision.corte_canonico_id)
        raise CortesDistintos(
            f"El scoring es del corte del {corte.fecha_corte.isoformat()} (as_of "
            f"{scoring.as_of.isoformat()}) y la decision/v2 del corte del "
            f"{otro.fecha_corte.isoformat()} (as_of {decision.as_of.isoformat()}): un plan usa "
            "la prediccion y la decision del mismo corte, con as_of igual a su fecha."
        )
    version = s.exec(
        select(VersionConfiguracionOptimizacion).where(
            VersionConfiguracionOptimizacion.configuracion_id == configuracion_id
        )
    ).one_or_none()
    if version is None:
        raise ConfiguracionNoEncontrada(f"No existe la configuracion {configuracion_id}.")
    if version.estado != EstadoVersionOptimizacion.PUBLICADA:
        raise ConfiguracionNoPublicada(
            f"La configuracion {configuracion_id} esta {version.estado}: solo una PUBLICADA se "
            "usa para planear."
        )
    contenido = configuracion_.configuracion_de(s, version)
    if (
        version.version_configuracion != configuracion_.VERSION_CONFIGURACION
        or contenido.hash() != version.hash_contenido
    ):
        raise ConfiguracionNoSoportada(
            f"La configuracion {configuracion_id} no es de "
            f"{configuracion_.VERSION_CONFIGURACION}, o su contenido no es el de su hash."
        )
    ruteo = campo = None
    if ruteo_run_id is not None:
        ruteo = s.exec(
            select(EjecucionRuteoV2).where(EjecucionRuteoV2.ruteo_run_id == ruteo_run_id)
        ).one_or_none()
        if ruteo is None:
            raise RuteoNoEncontrado(f"No existe el ruteo/v2 {ruteo_run_id}.")
        if ruteo.estado != EstadoRuteoV2.EXITOSA:
            raise RuteoNoExitoso(f"El ruteo/v2 {ruteo_run_id} esta {ruteo.estado}.")
        campo = s.get_one(EjecucionCampoV2, ruteo.ejecucion_campo_v2_id)
        if campo.estado != EstadoCampoV2.EXITOSA:
            raise RuteoNoExitoso(f"El plan de campo/v2 del ruteo {ruteo_run_id} no esta EXITOSA.")
        if campo.ejecucion_decision_v2_id != decision.id:
            raise RuteoDeOtraDecision(
                f"El ruteo/v2 {ruteo_run_id} rutea un plan de campo/v2 de otra decision/v2: un "
                "plan no mezcla la decision de un corte con el ruteo de otra."
            )
        if fecha_operacion != ruteo.fecha_operacion:
            raise FechaOperacionDistinta(
                f"El ruteo/v2 {ruteo_run_id} es del {ruteo.fecha_operacion.isoformat()}: la fecha "
                "de operacion del plan es la de su ruteo."
            )
    elif contenido.usa_campo:
        raise RuteoRequerido(
            "La configuracion tiene pools de CAMPO: un plan con CAMPO declara el ruteo/v2 cuyas "
            "paradas PROGRAMADA puede elegir."
        )
    elif contenido.usa_base:
        raise RuteoRequerido(
            "La configuracion usa la BASE (en un pool o en una restriccion): la base territorial "
            "sale de la cadena del ruteo/v2 declarado (su plan de campo y su territorial/v2)."
        )
    if fecha_operacion < corte.fecha_corte:
        raise FechaAnteriorAlCorte(
            f"La fecha de operacion {fecha_operacion.isoformat()} es anterior al corte del "
            f"{corte.fecha_corte.isoformat()}."
        )
    return Declarado(
        scoring, modelo, decision, corte, version, contenido, ruteo, campo, fecha_operacion
    )


def abrir(
    s: Session,
    *,
    scoring_run_id: UUID,
    decision_run_id: UUID,
    configuracion_id: UUID,
    fecha_operacion: date,
    ruteo_run_id: UUID | None = None,
    max_intentos: int,
    tomado_por: str | None = None,
    lease_segundos: float | None = None,
    version_politica: str | None = None,
) -> EjecucionOptimizacion:
    """La ejecucion EN_PROCESO con su trabajo OPTIMIZATION, en la transaccion de quien llama: envia
    los INSERT y no confirma. Levanta, sin registrar nada, un OptimizacionInvalida,
    OptimizacionYaPublicada u OptimizacionEnProceso."""
    politica = POLITICAS.get(version_politica or PUBLIC_V1.version)
    if politica is None:
        raise PoliticaNoSoportada(
            f"Este servicio optimiza con {', '.join(sorted(POLITICAS))}, no con {version_politica}."
        )
    d = resolver_declarado(
        s,
        scoring_run_id=scoring_run_id,
        decision_run_id=decision_run_id,
        configuracion_id=configuracion_id,
        fecha_operacion=fecha_operacion,
        ruteo_run_id=ruteo_run_id,
    )
    huella = solver_.huella(politica)
    huella_hash = solver_.hash_huella(huella)
    firma = firma_inputs(
        politica=politica,
        scoring=d.scoring,
        modelo=d.modelo,
        decision=d.decision,
        ruteo=d.ruteo,
        hash_configuracion=d.configuracion.hash_contenido,
        fecha_operacion=fecha_operacion,
        solver_fingerprint=huella_hash,
    )
    previa = _de_firma(s, firma, EstadoOptimizacion.EXITOSA)
    if previa is not None:
        raise OptimizacionYaPublicada(previa)
    activa = _de_firma(s, firma, EstadoOptimizacion.EN_PROCESO)
    if activa is not None:
        raise OptimizacionEnProceso(activa)
    ejecucion = EjecucionOptimizacion(
        ejecucion_scoring_predictivo_id=d.scoring.id,
        ejecucion_decision_v2_id=d.decision.id,
        corte_canonico_id=d.corte.id,
        ejecucion_ruteo_v2_id=None if d.ruteo is None else d.ruteo.id,
        ejecucion_campo_v2_id=None if d.campo is None else d.campo.id,
        ejecucion_territorial_v2_id=None
        if d.campo is None
        else d.campo.ejecucion_territorial_v2_id,
        version_configuracion_campo_id=None
        if d.campo is None
        else d.campo.version_configuracion_campo_id,
        version_bases_operativas_id=None
        if d.campo is None
        else d.campo.version_bases_operativas_id,
        version_configuracion_optimizacion_id=d.configuracion.id,
        fecha_operacion=fecha_operacion,
        version_motor=VERSION_MOTOR,
        version_politica=politica.version,
        hash_politica=hash_politica(politica),
        hash_configuracion=d.configuracion.hash_contenido,
        solver_fingerprint=huella_hash,
        firma_inputs=firma,
        huella_solver=huella,
        cuentas_universo=d.corte.cuentas,
    )
    error = insertar_en_savepoint(s, ejecucion)
    if error is not None:
        if restriccion(error) != INDICE_EN_PROCESO:
            raise error
        activa = _de_firma(s, firma, EstadoOptimizacion.EN_PROCESO)
        if (
            activa is None
        ):  # pragma: no cover - la que gano ya termino entre el rechazo y la revision
            raise error
        raise OptimizacionEnProceso(activa) from error
    cola.crear(
        s,
        TipoTrabajo.OPTIMIZATION,
        ejecucion.id,
        max_intentos=max_intentos,
        tomado_por=tomado_por,
        lease_segundos=lease_segundos,
    )
    log.info(
        "optimizacion %s abierta: scoring %s, decision %s, ruteo %s, configuracion %s, fecha %s",
        ejecucion.optimizacion_run_id,
        scoring_run_id,
        decision_run_id,
        ruteo_run_id,
        configuracion_id,
        fecha_operacion,
    )
    return ejecucion


def _de_firma(s: Session, firma: str, estado: EstadoOptimizacion) -> EjecucionOptimizacion | None:
    consulta = select(EjecucionOptimizacion).where(
        EjecucionOptimizacion.firma_inputs == firma, EjecucionOptimizacion.estado == estado
    )
    if estado == EstadoOptimizacion.EN_PROCESO:
        consulta = consulta.with_for_update()
    return s.exec(consulta).one_or_none()


# --- optimizar ------------------------------------------------------------------------------------

ANTES_UNIVERSO = "ANTES_UNIVERSO"
DESPUES_ELEGIBILIDAD = "DESPUES_ELEGIBILIDAD"
DESPUES_REDUCCION = "DESPUES_REDUCCION"
DESPUES_MODELO = "DESPUES_MODELO"
DESPUES_SOLVER_NIVEL_1 = "DESPUES_SOLVER_NIVEL_1"
DESPUES_SOLVER_NIVEL_2 = "DESPUES_SOLVER_NIVEL_2"
DESPUES_SOLVER_NIVEL_3 = "DESPUES_SOLVER_NIVEL_3"
DESPUES_SOLVER_NIVEL_4 = "DESPUES_SOLVER_NIVEL_4"
DESPUES_STAGING = "DESPUES_STAGING"
ANTES_PUBLICAR = "ANTES_PUBLICAR"
ANTES_CERRAR = "ANTES_CERRAR"
PUNTOS_DE_FALLA = (
    ANTES_UNIVERSO,
    DESPUES_ELEGIBILIDAD,
    DESPUES_REDUCCION,
    DESPUES_MODELO,
    DESPUES_SOLVER_NIVEL_1,
    DESPUES_SOLVER_NIVEL_2,
    DESPUES_SOLVER_NIVEL_3,
    DESPUES_SOLVER_NIVEL_4,
    DESPUES_STAGING,
    ANTES_PUBLICAR,
    ANTES_CERRAR,
)


def _punto_de_falla(nombre: str) -> None:
    """Un punto donde una prueba puede inyectar una falla. En produccion no hace nada."""


def ejecutar_optimizacion(ejecucion_id: int) -> None:
    """Lo que ejecuta un trabajo OPTIMIZATION. Si la ejecucion ya termino no hace nada."""
    with sesion() as s:
        ejecucion = s.get_one(EjecucionOptimizacion, ejecucion_id)
        if ejecucion.estado != EstadoOptimizacion.EN_PROCESO:
            log.info(
                "optimizacion %s ya termino %s", ejecucion.optimizacion_run_id, ejecucion.estado
            )
            return
    optimizar(ejecucion_id)


@dataclass
class _Corrida:
    ejecucion_id: int
    run_id: UUID
    firma: str
    politica: PoliticaOptimizacion
    entradas: sql.Entradas
    contenido: configuracion_.ConfiguracionOptimizacion
    pools: list[PoolCapacidadOptimizacion]
    """Los activos, en orden canonico (por codigo)."""
    cuentas_universo: int
    drift: dict | None
    fecha_corte: date
    fecha_operacion: date


def optimizar(ejecucion_id: int, *, resolvedor: formulacion.Resolvedor | None = None) -> None:
    """Resuelve la ejecucion y la deja en un estado terminal, con su candado. Levanta
    OptimizacionYaPublicada si otra publico primero la misma firma, TrabajoAjeno si este worker
    perdio su trabajo, y los errores transitorios de la base, con la ejecucion EN_PROCESO."""
    with candado(f"optimization:{ejecucion_id}"):
        corrida = _leer(ejecucion_id)
        if corrida is None:
            return
        tiempos = medicion.Tiempos()
        try:
            universo, problema, hallazgos = _armar(corrida, tiempos)
            with tiempos.fase("solver"):
                resolucion = formulacion.resolver(
                    problema,
                    corrida.politica,
                    resolvedor=resolvedor,
                    marcar=_punto_de_falla,
                )
            resolucion.hallazgos = hallazgos + resolucion.hallazgos
            if not resolucion.optima:
                _sin_plan(corrida, problema, resolucion, tiempos, resolvedor)
                return
            _publicar(corrida, universo, problema, resolucion, tiempos)
        except _NoSePublica as exc:
            _fallar(ejecucion_id, corrida.run_id, exc.resultado, str(exc), **exc.extra)
        except formulacion.FueraDeRango as exc:
            _fallar(
                ejecucion_id,
                corrida.run_id,
                ResultadoOptimizacion.COEFICIENTES_FUERA_DE_RANGO,
                f"{exc}. No se construyo ningun modelo.",
            )
        except OperationalError:
            log.warning("optimizacion %s: error transitorio; sigue EN_PROCESO", corrida.run_id)
            raise
        except (OptimizacionYaPublicada, cola.TrabajoAjeno):
            raise
        except Exception as exc:
            log.error("optimizacion %s: error inesperado%s", corrida.run_id, _traza(exc))
            _fallar(
                ejecucion_id,
                corrida.run_id,
                ResultadoOptimizacion.ERROR_INTERNO,
                f"Error interno ({type(exc).__name__}); ver la bitacora.",
            )


def _leer(ejecucion_id: int) -> _Corrida | None:
    with sesion() as s:
        e = s.get_one(EjecucionOptimizacion, ejecucion_id)
        if e.estado != EstadoOptimizacion.EN_PROCESO:
            log.warning("optimizacion %s ya termino %s", e.optimizacion_run_id, e.estado)
            return None
        politica = POLITICAS.get(e.version_politica)
        scoring = s.get_one(EjecucionScoringPredictivo, e.ejecucion_scoring_predictivo_id)
        decision = s.get_one(EjecucionDecisionV2, e.ejecucion_decision_v2_id)
        version = s.get_one(
            VersionConfiguracionOptimizacion, e.version_configuracion_optimizacion_id
        )
        ruteo = (
            None
            if e.ejecucion_ruteo_v2_id is None
            else s.get_one(EjecucionRuteoV2, e.ejecucion_ruteo_v2_id)
        )
        contenido = configuracion_.configuracion_de(s, version)
        pools = sorted(
            s.exec(
                select(PoolCapacidadOptimizacion).where(
                    PoolCapacidadOptimizacion.version_configuracion_optimizacion_id == version.id,
                    PoolCapacidadOptimizacion.activo,
                )
            ).all(),
            key=lambda p: p.codigo,
        )
        corte = s.get_one(CorteCanonico, e.corte_canonico_id)
        corrida = _Corrida(
            ejecucion_id=e.id,
            run_id=e.optimizacion_run_id,
            firma=e.firma_inputs,
            politica=politica,
            entradas=sql.Entradas(
                scoring=e.ejecucion_scoring_predictivo_id,
                decision=e.ejecucion_decision_v2_id,
                corte=e.corte_canonico_id,
                configuracion=version.id,
                ruteo=e.ejecucion_ruteo_v2_id,
                territorial=e.ejecucion_territorial_v2_id,
                configuracion_campo=e.version_configuracion_campo_id,
                bases=e.version_bases_operativas_id,
                permitir_imputacion=version.permitir_predicciones_con_imputacion,
            ),
            contenido=contenido,
            pools=pools,
            cuentas_universo=e.cuentas_universo,
            drift=scoring.drift_resumen,
            fecha_corte=corte.fecha_corte,
            fecha_operacion=e.fecha_operacion,
        )
        s.expunge_all()
        entradas_exitosas = (
            scoring.estado == EstadoScoringPredictivo.EXITOSA
            and decision.estado == EstadoDecisionV2.EXITOSA
            and version.estado == EstadoVersionOptimizacion.PUBLICADA
            and (ruteo is None or ruteo.estado == EstadoRuteoV2.EXITOSA)
        )
        soportada = (
            politica is not None
            and e.version_motor == VERSION_MOTOR
            and e.hash_politica == hash_politica(politica)
            and contenido.hash() == e.hash_configuracion
        )
        huella_propia = (
            solver_.hash_huella(solver_.huella(politica)) if politica is not None else None
        )
        huella_declarada = e.solver_fingerprint
    if not soportada:
        _fallar(
            ejecucion_id,
            corrida.run_id,
            ResultadoOptimizacion.VERSION_NO_SOPORTADA,
            "La ejecucion es de otro motor, de otra politica o su configuracion ya no es la de su "
            "hash; no se publico nada.",
        )
        return None
    if not entradas_exitosas:
        _fallar(
            ejecucion_id,
            corrida.run_id,
            ResultadoOptimizacion.ENTRADAS_NO_EXITOSAS,
            "Una entrada declarada ya no esta EXITOSA o PUBLICADA; no se publico nada.",
        )
        return None
    if huella_propia != huella_declarada:
        _fallar(
            ejecucion_id,
            corrida.run_id,
            ResultadoOptimizacion.SOLVER_DISTINTO,
            f"Este worker resuelve con la huella {huella_propia}, y la ejecucion se firmo con "
            f"{huella_declarada}: otro solver no publica bajo esa firma. No se publico nada.",
        )
        return None
    return corrida


def _armar(
    corrida: _Corrida, tiempos: medicion.Tiempos
) -> tuple[sql.Universo, formulacion.Problema, list[formulacion.Hallazgo]]:
    """El universo y los pares elegibles, por conjuntos, en una transaccion de lectura que se
    revierte; y el problema, con lo que se comprueba contra la cadena del ruteo."""
    _punto_de_falla(ANTES_UNIVERSO)
    e = corrida.entradas
    hallazgos: list[formulacion.Hallazgo] = []
    with tiempos.fase("universo"):
        with sesion() as s:
            programadas = sql.programadas_por_gestor(s, e.ruteo) if e.ruteo is not None else {}
            gestores = sql.gestores_de(s, e.configuracion_campo) if e.configuracion_campo else {}
            bases = sql.bases_de(s, e.bases) if e.bases is not None else {}
            activos = [
                sql.PoolActivo(j, p.id, p.canal, p.tipo_alcance, p.referencia_alcance)
                for j, p in enumerate(corrida.pools)
            ]
            sql.preparar(s, e, activos)
            universo = sql.universo(s)
            par_cuenta, par_pool = sql.pares(s)
            s.rollback()
    if len(universo) != corrida.cuentas_universo:
        raise _NoSePublica(
            ResultadoOptimizacion.DATOS_INCONSISTENTES,
            f"El corte tiene {corrida.cuentas_universo:,} cuentas y el universo armo "
            f"{len(universo):,}: el scoring y la decision/v2 no cubren las mismas cuentas.",
        )
    if np.any(universo.atraso == None):  # noqa: E711 - un arreglo de objetos
        raise _NoSePublica(
            ResultadoOptimizacion.DATOS_INCONSISTENTES,
            "Una cuenta tiene dias de atraso fuera de todo tramo.",
        )
    _punto_de_falla(DESPUES_ELEGIBILIDAD)
    # Lo que la configuracion nombra y la cadena del ruteo no tiene: un gestor o una base.
    desconocidos = sorted(
        p.referencia_alcance
        for p in corrida.pools
        if p.tipo_alcance == TipoAlcance.GESTOR_CAMPO and p.referencia_alcance not in gestores
    )
    if desconocidos:
        hallazgos.append(
            formulacion.Hallazgo(
                "GESTOR_DESCONOCIDO",
                formulacion.ERROR,
                f"{len(desconocidos)} pools de CAMPO nombran un gestor que la configuracion de "
                f"campo del ruteo no tiene: {', '.join(desconocidos[:10])}"
                + ("..." if len(desconocidos) > 10 else "."),
                {"gestores": desconocidos[:100], "cuantos": len(desconocidos)},
            )
        )
    nombradas = {
        p.referencia_alcance for p in corrida.pools if p.tipo_alcance == TipoAlcance.BASE
    } | {
        r.valor_dimension
        for r in corrida.contenido.restricciones
        if r.activa and r.dimension == Dimension.BASE
    }
    ajenas = sorted(nombradas - set(bases))
    if ajenas:
        hallazgos.append(
            formulacion.Hallazgo(
                "BASE_DESCONOCIDA",
                formulacion.ERROR,
                "La configuracion nombra bases que la version de bases de la cadena del ruteo no "
                f"tiene: {', '.join(ajenas)}.",
                {"bases": ajenas},
            )
        )
    for aviso in configuracion_.advertencias(corrida.contenido):
        hallazgos.append(formulacion.Hallazgo("CONFIGURACION", formulacion.ADVERTENCIA, aviso, {}))
    if any(h.severidad == formulacion.ERROR for h in hallazgos):
        raise _NoSePublica(
            ResultadoOptimizacion.CONFIGURACION_INCOMPATIBLE,
            "La configuracion nombra gestores o bases que la cadena del ruteo declarado no tiene; "
            "no se construyo ningun modelo.",
            extra={"diagnostico": {"hallazgos": [h.definicion() for h in hallazgos]}},
        )
    problema = _problema(corrida, universo, par_cuenta, par_pool, programadas)
    return universo, problema, hallazgos


def _problema(
    corrida: _Corrida,
    universo: sql.Universo,
    par_cuenta: np.ndarray,
    par_pool: np.ndarray,
    programadas: dict[str, int],
) -> formulacion.Problema:
    pools = []
    for p in corrida.pools:
        if p.canal == "CAMPO":
            tope = programadas.get(p.referencia_alcance, 0)
            efectiva = tope if p.capacidad is None else min(p.capacidad, tope)
        else:
            efectiva = p.capacidad
        pools.append(
            formulacion.Pool(
                p.codigo,
                p.canal,
                p.tipo_alcance,
                p.referencia_alcance,
                p.capacidad,
                efectiva,
                p.unidades_recurso_por_asignacion,
            )
        )
    coberturas = tuple(
        formulacion.Cobertura(
            r.codigo, TipoRestriccion(r.tipo), Dimension(r.dimension), r.valor_dimension, r.limite
        )
        for r in corrida.contenido.restricciones
        if r.activa
    )
    dimensiones = {
        Dimension.PRODUCTO: universo.producto,
        Dimension.ATRASO_BUCKET: universo.atraso,
        Dimension.PRIORIDAD_DECISION: universo.prioridad,
        Dimension.BASE: universo.base_codigo,
    }
    return formulacion.Problema(
        valor=universo.valor,
        contacto=universo.contacto,
        par_cuenta=par_cuenta,
        par_pool=par_pool,
        pools=tuple(pools),
        coberturas=coberturas,
        miembros=formulacion.pertenencias(dimensiones, coberturas, len(universo)),
        max_cuentas=corrida.contenido.max_cuentas_atendidas,
        max_unidades=corrida.contenido.max_unidades_recurso,
    )


def motivos_no_elegible(
    universo: sql.Universo,
    contenido: configuracion_.ConfiguracionOptimizacion,
    pools: list[PoolCapacidadOptimizacion],
    elegibles: np.ndarray,
) -> np.ndarray:
    """El motivo factual de cada cuenta sin pares (None en las elegibles), por canal de decision,
    con las mismas reglas que arman los pares: la imputacion; ningun canal admisible; ningun pool
    activo de sus canales admisibles; CAMPO como unico camino sin una parada PROGRAMADA; o un pool
    que no la alcanza."""
    canales_con_pool = {p.canal for p in pools}
    motivo = np.full(len(universo), None, dtype=object)
    for canal in np.unique(universo.canal_decision):
        admisibles = set(contenido.admisibles(canal))
        de_este = universo.canal_decision == canal
        if not admisibles:
            por_canal = Motivo.SIN_CANAL_ADMISIBLE.value
        elif not admisibles & canales_con_pool:
            por_canal = Motivo.CONFIG_EXCLUYE_CANAL.value
        else:
            por_canal = None
        if por_canal is not None:
            motivo[de_este] = por_canal
            continue
        campo = "CAMPO" in admisibles and "CAMPO" in canales_con_pool
        sin_programada = universo.estado_parada != "PROGRAMADA"
        motivo[de_este & campo & sin_programada] = Motivo.CAMPO_NO_PROGRAMADA.value
        motivo[de_este & ~(campo & sin_programada)] = Motivo.SIN_POOL_APLICABLE.value
    if not contenido.permitir_predicciones_con_imputacion:
        motivo[universo.imputada] = Motivo.PREDICCION_CON_IMPUTACION.value
    motivo[elegibles] = None
    return motivo


# --- sin plan -------------------------------------------------------------------------------------

RESULTADO_DE_LA_RESOLUCION = {
    formulacion.INFACTIBLE: ResultadoOptimizacion.INFACTIBLE,
    formulacion.OPTIMO_NO_DEMOSTRADO: ResultadoOptimizacion.OPTIMO_NO_DEMOSTRADO,
    formulacion.ERROR_NUMERICO: ResultadoOptimizacion.ERROR_NUMERICO,
    formulacion.ERROR_DEL_SOLVER: ResultadoOptimizacion.ERROR_DEL_SOLVER,
}


def _sin_plan(
    corrida: _Corrida,
    problema: formulacion.Problema,
    resolucion: formulacion.Resolucion,
    tiempos: medicion.Tiempos,
    resolvedor: formulacion.Resolvedor | None,
) -> None:
    """Una ejecucion sin plan optimo demostrado: FALLIDA, con su prechequeo, su certificado (lo que
    el solver si dijo, sin publicarlo) y, si fue infactible para el solver, la relajacion minima."""
    diagnostico = {"hallazgos": [h.definicion() for h in resolucion.hallazgos]}
    if resolucion.estado == EstadoSolver.INFEASIBLE:
        with tiempos.fase("relajacion"):
            diagnostico["diagnostico_relajacion"] = formulacion.diagnosticar_relajacion(
                problema, corrida.politica, resolvedor=resolvedor
            )
    resultado = RESULTADO_DE_LA_RESOLUCION[resolucion.resultado]
    nivel = resolucion.niveles[-1] if resolucion.niveles else None
    explicacion = {
        ResultadoOptimizacion.INFACTIBLE: "La configuracion no admite ningun plan factible: ver "
        "el diagnostico. No es un error interno.",
        ResultadoOptimizacion.OPTIMO_NO_DEMOSTRADO: "El solver agoto su limite sin demostrar el "
        "optimo: ningun plan se publica sin su prueba de optimalidad.",
        ResultadoOptimizacion.ERROR_NUMERICO: "La solucion del solver, en enteros, no cumplia una "
        "restriccion o no reproducia los objetivos fijados: no se publica.",
        ResultadoOptimizacion.ERROR_DEL_SOLVER: "El solver termino con un estado que no demuestra "
        "nada: no se publica.",
    }[resultado]
    _fallar(
        corrida.ejecucion_id,
        corrida.run_id,
        resultado,
        explicacion + (f" Nivel {nivel.nivel}: {nivel.estado.value}." if nivel is not None else ""),
        solver_status=resolucion.estado.value,
        diagnostico=diagnostico,
        certificado=resolucion.certificado(),
        tiempos=_tiempos(tiempos, resolucion),
        conteos=_conteos_del_modelo(problema, resolucion),
    )


def _conteos_del_modelo(problema: formulacion.Problema, resolucion: formulacion.Resolucion) -> dict:
    """Lo que se conto del modelo, aunque no se publique un plan: el universo, las elegibles, la
    reduccion y el tamano del modelo."""
    conteos = {"cuentas_elegibles": int(problema.elegibles.sum())}
    if resolucion.reduccion is not None:
        conteos["cuentas_retenidas"] = int(resolucion.reduccion.retenida.sum())
        conteos["clases"] = resolucion.reduccion.clases
    if resolucion.modelo is not None:
        conteos["variables"] = resolucion.modelo.variables
        conteos["restricciones_solver"] = resolucion.modelo.restricciones
        conteos["elementos_no_nulos"] = resolucion.modelo.no_nulos
    return conteos


# --- publicar -------------------------------------------------------------------------------------


@dataclass
class Plan:
    """Lo que se publica de un plan optimo, ya calculado y verificado."""

    evaluacion: formulacion.Evaluacion
    estado: np.ndarray
    motivo: np.ndarray
    hash_plan: str
    csv: bytes
    usos: list[tuple]
    restricciones: list[tuple]
    resumen: dict


def _texto_decimal(enteros: np.ndarray, decimales: int) -> list[str]:
    escala = 10**decimales
    return [f"{e // escala}.{e % escala:0{decimales}d}" for e in enteros.tolist()]


def armar_plan(
    corrida: _Corrida,
    universo: sql.Universo,
    problema: formulacion.Problema,
    resolucion: formulacion.Resolucion,
) -> Plan:
    """El plan publicable: cada cuenta con su estado y su motivo factual, el uso de cada pool, cada
    restriccion, el hash canonico y el resumen. Verifica en enteros que el plan cumple todo."""
    asignacion = resolucion.asignacion
    retenida = resolucion.reduccion.retenida
    evaluacion = formulacion.evaluar(problema, asignacion, retenida)
    if not evaluacion.factible:
        raise _NoSePublica(
            ResultadoOptimizacion.DATOS_INCONSISTENTES,
            "El plan del solver no cumple, en enteros: " + "; ".join(evaluacion.violaciones),
        )
    if evaluacion.objetivos != resolucion.objetivos:
        raise _NoSePublica(
            ResultadoOptimizacion.DATOS_INCONSISTENTES,
            "Los objetivos del plan no son los que el solver demostro.",
        )
    elegibles = problema.elegibles
    motivos = motivos_no_elegible(universo, corrida.contenido, corrida.pools, elegibles)
    estado, motivo = formulacion.estados(problema, asignacion, motivos)
    asignada = asignacion != formulacion.SIN_POOL
    pool = np.where(asignada, asignacion, 0)
    canales = np.array([p.canal for p in problema.pools] or ["-"], dtype=object)
    ids_pool = np.array([p.id for p in corrida.pools] or [0], dtype=np.int64)
    unidades = problema.unidades if problema.pools else np.ones(1, dtype=np.int64)
    canal_asignado = np.where(asignada, canales[pool], None)
    en_campo = asignada & (canal_asignado == "CAMPO")
    gestor = np.where(en_campo, universo.gestor, -1)
    orden = np.where(en_campo, universo.orden_visita, -1)
    if np.any(en_campo & (universo.estado_parada != "PROGRAMADA")) or np.any(
        en_campo & (gestor < 0)
    ):
        raise _NoSePublica(
            ResultadoOptimizacion.DATOS_INCONSISTENTES,
            "Una cuenta quedo en CAMPO sin una parada PROGRAMADA de su gestor.",
        )
    gestores_texto = [
        universo.gestor_codigo[i] if en_campo[i] else None for i in range(len(universo))
    ]
    lineas = formulacion.lineas_del_plan(
        universo.cuenta_id,
        problema,
        asignacion,
        estado,
        motivo,
        retenida,
        evaluacion,
        gestores=gestores_texto,
        ordenes=[int(o) if o >= 0 else None for o in orden.tolist()],
    )
    hash_plan = formulacion.hash_plan(lineas)
    n = len(universo)

    def anulable(valores: np.ndarray, condicion: np.ndarray) -> pa.Array:
        return pa.array(np.where(condicion, valores, 0), mask=~condicion, type=pa.int64())

    tabla = pa.table(
        {
            "valor": pa.array(universo.valor, type=pa.int64()),
            "ejecucion": pa.array(np.full(n, corrida.ejecucion_id, dtype=np.int64)),
            "cuenta": pa.array(universo.cuenta, type=pa.int64()),
            "corte": pa.array(np.full(n, corrida.entradas.corte, dtype=np.int64)),
            "configuracion": pa.array(np.full(n, corrida.entradas.configuracion, dtype=np.int64)),
            "pool": anulable(ids_pool[pool], asignada),
            "gestor": anulable(gestor, en_campo),
            "base": anulable(universo.base, universo.base >= 0),
            "orden": anulable(orden, en_campo),
            "unidades": anulable(unidades[pool], asignada),
            "contacto": pa.array(_texto_decimal(universo.contacto, 8), type=pa.string()),
            "estado": pa.array(estado.tolist(), type=pa.string()),
            "motivo": pa.array(motivo.tolist(), type=pa.string()),
            "canal_decision": pa.array(universo.canal_decision.tolist(), type=pa.string()),
            "canal_asignado": pa.array(canal_asignado.tolist(), type=pa.string()),
            "prioridad": pa.array(universo.prioridad.tolist(), type=pa.string()),
            "producto": pa.array(universo.producto.tolist(), type=pa.string()),
            "atraso": pa.array(universo.atraso.tolist(), type=pa.string()),
            "retenida": pa.array(["t" if r else "f" for r in retenida.tolist()], type=pa.string()),
        }
    )
    salida = pa.BufferOutputStream()
    pacsv.write_csv(
        tabla,
        salida,
        write_options=pacsv.WriteOptions(include_header=False, quoting_style="needed"),
    )
    gestores_por_codigo: dict[str, int] = {}
    for i in np.flatnonzero(universo.gestor >= 0):
        gestores_por_codigo.setdefault(universo.gestor_codigo[i], int(universo.gestor[i]))
    usos = []
    for j, (u, p) in enumerate(zip(evaluacion.uso, corrida.pools, strict=True)):
        usos.append(
            (
                u.capacidad_declarada,
                u.capacidad,
                u.elegibles,
                u.retenidas,
                u.utilizada,
                u.holgura,
                u.unidades_consumidas,
                corrida.ejecucion_id,
                p.id,
                corrida.entradas.configuracion,
                gestores_por_codigo.get(p.referencia_alcance) if p.canal == "CAMPO" else None,
            )
        )
        del j
    restricciones = [
        (
            r.lhs,
            r.rhs,
            r.holgura,
            r.cuentas_grupo,
            r.total,
            r.limite_resuelto,
            corrida.ejecucion_id,
            r.codigo,
            r.tipo,
            r.dimension,
            r.valor_dimension,
            r.operador,
            r.binding,
        )
        for r in evaluacion.restricciones
    ]
    resumen = _resumen(corrida, universo, problema, resolucion, evaluacion, estado, motivo)
    return Plan(
        evaluacion,
        estado,
        motivo,
        hash_plan,
        salida.getvalue().to_pybytes(),
        usos,
        restricciones,
        resumen,
    )


def _resumen(
    corrida: _Corrida,
    universo: sql.Universo,
    problema: formulacion.Problema,
    resolucion: formulacion.Resolucion,
    evaluacion: formulacion.Evaluacion,
    estado: np.ndarray,
    motivo: np.ndarray,
) -> dict:
    asignacion = resolucion.asignacion
    asignada = asignacion != formulacion.SIN_POOL
    por_canal: dict[str, dict] = {}
    for j, p in enumerate(problema.pools):
        canal = por_canal.setdefault(
            p.canal,
            {"pools": 0, "capacidad": 0, "utilizada": 0, "unidades": 0, "valor_centavos": 0},
        )
        en_pool = asignacion == j
        canal["pools"] += 1
        canal["capacidad"] += p.capacidad
        canal["utilizada"] += int(en_pool.sum())
        canal["unidades"] += int(en_pool.sum()) * p.unidades
        canal["valor_centavos"] += (
            int(universo.valor[en_pool].sum(dtype=object)) if en_pool.any() else 0
        )
    for canal in por_canal.values():
        canal["utilizacion"] = (
            round(canal["utilizada"] / canal["capacidad"], 6) if canal["capacidad"] else None
        )
    restricciones = evaluacion.restricciones
    holguras = [r.holgura for r in restricciones]
    return {
        "estados": {e.value: int((estado == e.value).sum()) for e in EstadoCuenta},
        "motivos": {m: int((motivo == m).sum()) for m in sorted(set(motivo.tolist()))},
        "por_canal": dict(sorted(por_canal.items())),
        "con_imputacion": {
            "universo": int(universo.imputada.sum()),
            "elegibles": int((universo.imputada & problema.elegibles).sum()),
            "asignadas": int((universo.imputada & asignada).sum()),
            "admitidas": corrida.contenido.permitir_predicciones_con_imputacion,
        },
        "valor_oportunidad_centavos": {
            "universo": int(universo.valor.sum(dtype=object)),
            "elegibles": int(universo.valor[problema.elegibles].sum(dtype=object)),
            "plan": evaluacion.objetivos.valor,
        },
        "restricciones": {
            "total": len(restricciones),
            "binding": sum(r.binding for r in restricciones),
            "no_binding": sum(not r.binding for r in restricciones),
            "holgura_minima": min(holguras) if holguras else None,
            "binding_declaradas": sorted(
                r.codigo for r in restricciones if r.binding and not r.codigo.startswith("POOL:")
            ),
        },
        "reduccion": resolucion.reduccion.resumen(),
        "drift_scoring": {
            "resumen": corrida.drift,
            "nota": "El drift del scoring declarado se reporta y no cambia el objetivo: un PSI "
            "alto no prueba un mal desempeno.",
        },
    }


def _publicar(
    corrida: _Corrida,
    universo: sql.Universo,
    problema: formulacion.Problema,
    resolucion: formulacion.Resolucion,
    tiempos: medicion.Tiempos,
) -> None:
    with tiempos.fase("plan"):
        plan = armar_plan(corrida, universo, problema, resolucion)
    with sesion() as s:
        ejecucion = s.exec(
            select(EjecucionOptimizacion)
            .where(EjecucionOptimizacion.id == corrida.ejecucion_id)
            .with_for_update()
        ).one()
        if ejecucion.estado != EstadoOptimizacion.EN_PROCESO:
            log.warning("optimizacion %s ya termino %s", corrida.run_id, ejecucion.estado)
            return
        try:
            with s.begin_nested():
                with tiempos.fase("staging"):
                    s.execute(
                        text(
                            "CREATE TEMP TABLE po_resultado (LIKE resultado_optimizacion_cuenta) "
                            "ON COMMIT DROP"
                        )
                    )
                    copiar_csv(s, "po_resultado", COLUMNAS_RESULTADO, [plan.csv])
                _punto_de_falla(DESPUES_STAGING)
                if corrida.entradas.ruteo is not None:
                    _verificar_campo(s, corrida.entradas.ruteo)
                _punto_de_falla(ANTES_PUBLICAR)
                with tiempos.fase("publicacion"):
                    columnas = ", ".join(COLUMNAS_RESULTADO)
                    publicadas = s.execute(
                        text(
                            f"INSERT INTO resultado_optimizacion_cuenta ({columnas}) "
                            f"SELECT {columnas} FROM po_resultado ORDER BY cuenta_canonica_id"
                        )
                    ).rowcount
                    copiar(
                        s,
                        "uso_pool_optimizacion",
                        (
                            "capacidad_declarada",
                            "capacidad_efectiva",
                            "elegibles",
                            "retenidas",
                            "utilizada",
                            "holgura",
                            "unidades_consumidas",
                            "ejecucion_optimizacion_id",
                            "pool_capacidad_optimizacion_id",
                            "version_configuracion_optimizacion_id",
                            "gestor_campo_id",
                        ),
                        plan.usos,
                    )
                    copiar(
                        s,
                        "resultado_restriccion_optimizacion",
                        (
                            "lhs",
                            "rhs",
                            "holgura",
                            "cuentas_grupo",
                            "total",
                            "limite_resuelto",
                            "ejecucion_optimizacion_id",
                            "codigo",
                            "tipo",
                            "dimension",
                            "valor_dimension",
                            "operador",
                            "binding",
                        ),
                        plan.restricciones,
                    )
                if publicadas != corrida.cuentas_universo:
                    raise _NoSePublica(
                        ResultadoOptimizacion.DATOS_INCONSISTENTES,
                        f"Se publicarian {publicadas:,} resultados para "
                        f"{corrida.cuentas_universo:,} cuentas del universo.",
                    )
                _punto_de_falla(ANTES_CERRAR)
                _cerrar(s, ejecucion, corrida, problema, resolucion, plan, tiempos)
        except IntegrityError as exc:
            if restriccion(exc) != INDICE_EXITOSA:
                raise
            s.rollback()
            _fallar(
                corrida.ejecucion_id,
                corrida.run_id,
                ResultadoOptimizacion.YA_OPTIMIZADA,
                "Otra ejecucion publico la misma firma mientras esta se resolvia; no se publica "
                "dos veces.",
            )
            with sesion() as s2:
                previa = _de_firma(s2, corrida.firma, EstadoOptimizacion.EXITOSA)
                s2.expunge_all()
            raise OptimizacionYaPublicada(previa) from exc
        cola.confirmar_dueno(s)
        s.commit()
    log.info(
        "optimizacion %s: plan publicado, %d cuentas asignadas de %d",
        corrida.run_id,
        plan.evaluacion.cuentas,
        corrida.cuentas_universo,
    )


def _verificar_campo(s: Session, ruteo: int) -> None:
    """La ultima revision, por conjuntos: cada cuenta en CAMPO tiene una parada PROGRAMADA en el
    ruteo declarado, con el gestor y el orden que se publican, y su pool es el de ese mismo gestor.
    Si no, no se publica nada."""
    malas = s.execute(
        text(
            "SELECT count(*) FROM po_resultado r LEFT JOIN parada_ruta_v2 x "
            "ON x.ejecucion_ruteo_v2_id = :ruteo AND x.cuenta_canonica_id = r.cuenta_canonica_id "
            "LEFT JOIN gestor_campo g ON g.id = x.gestor_campo_id "
            "LEFT JOIN pool_capacidad_optimizacion k ON k.id = r.pool_capacidad_optimizacion_id "
            "WHERE r.canal_asignado = 'CAMPO' AND (x.estado IS DISTINCT FROM 'PROGRAMADA' "
            "OR x.gestor_campo_id IS DISTINCT FROM r.gestor_campo_id "
            "OR x.orden_visita IS DISTINCT FROM r.orden_ruteo_original "
            "OR k.tipo_alcance IS DISTINCT FROM 'GESTOR_CAMPO' "
            "OR k.referencia_alcance IS DISTINCT FROM g.codigo)"
        ),
        {"ruteo": ruteo},
    ).scalar_one()
    if malas:
        raise _NoSePublica(
            ResultadoOptimizacion.DATOS_INCONSISTENTES,
            f"{malas} cuentas en CAMPO sin una parada PROGRAMADA de su gestor en el ruteo, o en el "
            "pool de otro gestor.",
        )


def _tiempos(tiempos: medicion.Tiempos, resolucion: formulacion.Resolucion) -> dict:
    """Las fases de la ejecucion y, dentro de la del solver, las suyas: la reduccion, la
    construccion del modelo y cada nivel."""
    return {
        **tiempos.resumen(),
        "solver_segundos": {k: round(v, 3) for k, v in resolucion.tiempos.items()},
    }


def _decimal(valor: float | None, decimales: int) -> Decimal | None:
    if valor is None or not np.isfinite(valor):
        return None
    return Decimal(f"{valor:.{decimales}f}")


def _cerrar(
    s: Session,
    ejecucion: EjecucionOptimizacion,
    corrida: _Corrida,
    problema: formulacion.Problema,
    resolucion: formulacion.Resolucion,
    plan: Plan,
    tiempos: medicion.Tiempos,
) -> None:
    o = resolucion.objetivos
    primero = resolucion.niveles[0]
    ejecucion.estado = EstadoOptimizacion.EXITOSA
    ejecucion.resultado = ResultadoOptimizacion.PLAN_PUBLICADO.value
    ejecucion.cuentas_elegibles = int(problema.elegibles.sum())
    ejecucion.cuentas_con_imputacion = plan.resumen["con_imputacion"]["universo"]
    ejecucion.cuentas_retenidas = int(resolucion.reduccion.retenida.sum())
    ejecucion.cuentas_asignadas = plan.evaluacion.cuentas
    ejecucion.cuentas_publicadas = corrida.cuentas_universo
    ejecucion.clases = resolucion.reduccion.clases
    ejecucion.variables = resolucion.modelo.variables
    ejecucion.restricciones_solver = resolucion.modelo.restricciones
    ejecucion.elementos_no_nulos = resolucion.modelo.no_nulos
    ejecucion.objetivo_primario_centavos = o.valor
    ejecucion.objetivo_secundario = o.contacto
    ejecucion.unidades_recurso = o.unidades
    ejecucion.desempate_canonico = o.desempate
    ejecucion.solver_status = EstadoSolver.OPTIMAL.value
    ejecucion.best_bound = _decimal(primero.cota, 4)
    ejecucion.mip_gap = _decimal(primero.gap, 12)
    ejecucion.solver_seconds = Decimal(f"{sum(n.segundos for n in resolucion.niveles):.3f}")
    ejecucion.hash_plan = plan.hash_plan
    ejecucion.certificado = resolucion.certificado()
    ejecucion.diagnostico = {"hallazgos": [h.definicion() for h in resolucion.hallazgos]}
    ejecucion.resumen = plan.resumen
    ejecucion.tiempos = _tiempos(tiempos, resolucion)
    # Un nivel sin variables libres no llama al solver: lo dejaron determinado los anteriores (o
    # no hay variables y el plan vacio es el unico).
    por_highs = sum(n.invocado for n in resolucion.niveles)
    demostracion = (
        "HiGHS demostro el optimo de los cuatro niveles (OPTIMAL, cota = objetivo)"
        if por_highs == len(resolucion.niveles)
        else f"El optimo de los cuatro niveles quedo demostrado: {por_highs} por HiGHS (OPTIMAL, "
        f"cota = objetivo) y {len(resolucion.niveles) - por_highs} sin variables libres, ya "
        "determinados"
    )
    ejecucion.detalle = (
        f"Plan optimo de {plan.evaluacion.cuentas:,} cuentas de {corrida.cuentas_universo:,} del "
        f"corte del {corrida.fecha_corte.isoformat()}, para el "
        f"{corrida.fecha_operacion.isoformat()}: valor de oportunidad predicho de "
        f"{o.valor / 100:,.2f} y {o.unidades:,} unidades de recurso. {demostracion}. {AVISO}"
    )
    ejecucion.terminada_en = ahora()
    s.add(ejecucion)
    s.flush()


def _fallar(
    ejecucion_id: int,
    etiqueta: UUID,
    resultado: ResultadoOptimizacion,
    motivo: str,
    *,
    solver_status: str = EstadoSolver.NO_INVOCADO.value,
    diagnostico: dict | None = None,
    certificado: dict | None = None,
    tiempos: dict | None = None,
    conteos: dict | None = None,
) -> None:
    with sesion() as s:
        try:
            cola.confirmar_dueno(s)
        except cola.TrabajoAjeno:
            s.rollback()
            log.warning("optimizacion %s: su trabajo ya no es de este worker: %s", etiqueta, motivo)
            raise
        s.execute(
            update(EjecucionOptimizacion)
            .where(
                EjecucionOptimizacion.id == ejecucion_id,
                EjecucionOptimizacion.estado == EstadoOptimizacion.EN_PROCESO,
            )
            .values(
                estado=EstadoOptimizacion.FALLIDA,
                resultado=resultado.value,
                solver_status=solver_status,
                cuentas_publicadas=0,
                cuentas_asignadas=0,
                hash_plan=None,
                objetivo_primario_centavos=None,
                objetivo_secundario=None,
                unidades_recurso=None,
                desempate_canonico=None,
                diagnostico=diagnostico,
                certificado=certificado,
                tiempos=tiempos,
                detalle=motivo,
                terminada_en=ahora(),
                **(conteos or {}),
            )
            .execution_options(synchronize_session=False)
        )
        s.commit()
    log.warning("optimizacion %s: FALLIDA (%s)", etiqueta, resultado.value)
