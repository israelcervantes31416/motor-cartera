"""Un scoring de predictive/model-v1: una version PUBLICADA declarada sobre un corte canonico, una
prediccion por cuenta y su drift contra TRAIN, todo o nada.

El orden:

1. `abrir` resuelve lo declarado: la version (PUBLICADA, nunca "la ultima"), el corte, as_of (el
   dia del corte) y known_at (en OPERATIVO, si no se declara, el reloj de la base una vez; en
   BACKTEST, obligatorio). Fail-closed: un modelo que no esta PUBLICADA, un corte que no existia a
   known_at o un known_at futuro no se abren. Registra la ejecucion EN_PROCESO con su trabajo
   PREDICTIVE_SCORING, en la transaccion de quien llama. La zona es la del entrenamiento del modelo.
2. Un worker toma el trabajo, espera a que se asiente la frontera de known_at y puntua.
3. `puntuar`, con el candado de la ejecucion y sin una transaccion abierta mientras infiere:
   - arma las features de cada cuenta del corte con lo conocido a known_at, en una transaccion de
     lectura (el mismo SQL del entrenamiento: `construccion`);
   - predice con el runtime de cada artefacto JSON, cuantiza (8 decimales, centavos) y deriva el
     riesgo como 1 - p de recuperacion, exacto;
   - mide el drift contra la referencia de TRAIN del modelo;
   - publica en una transaccion corta: copia las predicciones a una tabla de paso, las publica con
     un INSERT ... SELECT, publica el drift y cierra EXITOSA con el hash de las predicciones y el
     del drift, si su trabajo sigue siendo suyo.

Una prediccion es una probabilidad de un resultado observado bajo la operacion historica: no es el
efecto de ninguna accion y no cambia decision/v2, campo/v2 ni ruteo/v2. Ni la bitacora ni la
ejecucion llevan el score o las features de una cuenta.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import numpy as np
import pyarrow as pa
import pyarrow.csv as pacsv
from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session, select

from motor_cartera.db.copia import copiar_csv
from motor_cartera.db.modelos import (
    ComponenteModeloPredictivo,
    CorteCanonico,
    DriftPredictivo,
    EjecucionEntrenamientoPredictivo,
    EjecucionScoringPredictivo,
    EstadoModeloPredictivo,
    EstadoScoringPredictivo,
    ModoScoringPredictivo,
    ResultadoScoringPredictivo,
    TipoTrabajo,
    VersionModeloPredictivo,
    ahora,
)
from motor_cartera.db.sesion import insertar_en_savepoint, restriccion, sesion
from motor_cartera.decision_v2.ejecuciones import esperar_frontera
from motor_cartera.geografia.ejecuciones import _traza
from motor_cartera.orquestacion import cola
from motor_cartera.predictivo import artefacto as artefacto_
from motor_cartera.predictivo import construccion, drift, medicion, sql
from motor_cartera.predictivo import features as features_
from motor_cartera.predictivo import targets as targets_
from motor_cartera.predictivo.candado import candado
from motor_cartera.predictivo.features import CATEGORICAS, NUMERICAS, Familia
from motor_cartera.predictivo.politica import POLITICAS, VERSION_MODELO, PoliticaEntrenamiento
from motor_cartera.predictivo.targets import Target

log = logging.getLogger(__name__)

INDICE_EXITOSA = "ux_scoring_predictivo_exitosa"
INDICE_EN_PROCESO = "ux_scoring_predictivo_en_proceso"

AVISO = (
    "Una prediccion es la probabilidad de un resultado observado bajo la operacion historica, con "
    "lo que se conocia a known_at: no es el efecto de ninguna accion ni una recomendacion, y no "
    "cambia decision/v2, campo/v2 ni ruteo/v2."
)

SALIDAS: dict[Target, str] = {t.target: t.salida for t in targets_.TARGETS}

COLUMNAS_PREDICCION = (
    "ejecucion_scoring_predictivo_id",
    "cuenta_canonica_id",
    "corte_canonico_id",
    "p_recuperacion_positiva_30d",
    "p_contacto_14d",
    "p_promesa_30d",
    "riesgo_sin_recuperacion_30d",
    "recuperacion_positiva_esperada_30d",
    "estado",
    "motivos",
    "feature_hash",
)


# --- errores al abrir -----------------------------------------------------------------------------


class ScoringInvalido(Exception):
    """Un scoring que no se puede abrir: no registra nada. `codigo` es estable."""

    codigo = "SCORING_INVALIDO"
    estado_http = 422


def _error(nombre: str, codigo: str, estado_http: int = 422) -> type[ScoringInvalido]:
    return type(nombre, (ScoringInvalido,), {"codigo": codigo, "estado_http": estado_http})


ModeloNoEncontrado = _error("ModeloNoEncontrado", "MODELO_NO_ENCONTRADO", 404)
ModeloNoPublicado = _error("ModeloNoPublicado", "MODELO_NO_PUBLICADO", 409)
ModeloNoSoportado = _error("ModeloNoSoportado", "MODELO_NO_SOPORTADO")
CorteNoEncontrado = _error("CorteNoEncontrado", "CORTE_NO_ENCONTRADO", 404)
KnownAtObligatorio = _error("KnownAtObligatorio", "KNOWN_AT_OBLIGATORIO")
KnownAtSinZona = _error("KnownAtSinZona", "KNOWN_AT_SIN_ZONA")
KnownAtFuturo = _error("KnownAtFuturo", "KNOWN_AT_FUTURO")
CorteNoConocido = _error("CorteNoConocido", "CORTE_NO_CONOCIDO")


class ScoringYaPublicado(Exception):
    """Otro scoring ya publico exactamente lo mismo (la misma firma)."""

    def __init__(self, previa: EjecucionScoringPredictivo) -> None:
        super().__init__(
            f"Ese scoring ya se publico: la ejecucion {previa.scoring_run_id} (firma "
            f"{previa.firma_inputs[:12]}...)."
        )
        self.previa = previa


class ScoringEnProceso(Exception):
    def __init__(self, activa: EjecucionScoringPredictivo) -> None:
        super().__init__(
            f"Ese scoring ya se esta calculando: la ejecucion {activa.scoring_run_id}."
        )
        self.activa = activa


class _NoSePublica(Exception):
    def __init__(self, resultado: ResultadoScoringPredictivo, mensaje: str) -> None:
        super().__init__(mensaje)
        self.resultado = resultado


# --- abrir ----------------------------------------------------------------------------------------


def firma_inputs(
    *,
    modelo_version_id: UUID,
    hash_modelo: str,
    corte_id: UUID,
    as_of,
    known_at: datetime,
    zona: str,
) -> str:
    """El SHA-256 de lo que fija un scoring: la version del modelo con su hash, las features con el
    suyo, el corte, as_of, known_at y la zona. El modo no entra: OPERATIVO solo decide como se fija
    known_at."""
    lineas = [
        VERSION_MODELO,
        str(modelo_version_id),
        hash_modelo,
        features_.VERSION_FEATURES,
        features_.hash_features(),
        str(corte_id),
        as_of.isoformat(),
        known_at.astimezone(UTC).isoformat(timespec="microseconds"),
        zona,
    ]
    return hashlib.sha256("\n".join(lineas).encode("utf-8")).hexdigest()


def abrir(
    s: Session,
    *,
    modelo_version_id: UUID,
    corte_id: UUID,
    known_at: datetime | None,
    modo: ModoScoringPredictivo,
    max_intentos: int,
    tomado_por: str | None = None,
    lease_segundos: float | None = None,
) -> EjecucionScoringPredictivo:
    """La ejecucion EN_PROCESO con su trabajo, en la transaccion de quien llama. Levanta, sin
    registrar nada, un ScoringInvalido, ScoringYaPublicado o ScoringEnProceso."""
    version = s.exec(
        select(VersionModeloPredictivo).where(
            VersionModeloPredictivo.modelo_version_id == modelo_version_id
        )
    ).first()
    if version is None:
        raise ModeloNoEncontrado(f"No existe la version de modelo {modelo_version_id}.")
    if version.estado != EstadoModeloPredictivo.PUBLICADA:
        raise ModeloNoPublicado(
            f"La version {modelo_version_id} esta {version.estado}: solo una PUBLICADA puntua."
        )
    if (
        version.version_modelo != VERSION_MODELO
        or version.version_features != features_.VERSION_FEATURES
        or version.hash_features != features_.hash_features()
        or version.version_politica not in POLITICAS
    ):
        raise ModeloNoSoportado(
            f"La version {modelo_version_id} es de {version.version_modelo} con "
            f"{version.version_features}; este servicio puntua {VERSION_MODELO} con "
            f"{features_.VERSION_FEATURES} y su hash."
        )
    corte = s.exec(select(CorteCanonico).where(CorteCanonico.corte_id == corte_id)).first()
    if corte is None:
        raise CorteNoEncontrado(f"No existe un corte canonico con corte_id {corte_id}.")
    reloj = s.execute(text("SELECT now()")).scalar_one()
    if known_at is None:
        if modo == ModoScoringPredictivo.BACKTEST:
            raise KnownAtObligatorio("Un BACKTEST declara su known_at.")
        known_at = reloj
    elif known_at.tzinfo is None:
        raise KnownAtSinZona("known_at es un instante: lleva su zona horaria.")
    if known_at > reloj:
        raise KnownAtFuturo(
            f"known_at ({known_at.isoformat()}) es posterior al reloj de la base "
            f"({reloj.isoformat()})."
        )
    if corte.creado_en > known_at:
        raise CorteNoConocido(
            f"El corte del {corte.fecha_corte} se materializo el {corte.creado_en.isoformat()}, "
            f"despues de known_at ({known_at.isoformat()}): a esa frontera no existia."
        )
    entrenamiento = s.get_one(
        EjecucionEntrenamientoPredictivo, version.ejecucion_entrenamiento_predictivo_id
    )
    zona = entrenamiento.zona_horaria
    firma = firma_inputs(
        modelo_version_id=modelo_version_id,
        hash_modelo=version.hash_modelo,
        corte_id=corte.corte_id,
        as_of=corte.fecha_corte,
        known_at=known_at,
        zona=zona,
    )
    previa = _de_firma(s, firma, EstadoScoringPredictivo.EXITOSA)
    if previa is not None:
        raise ScoringYaPublicado(previa)
    activa = _de_firma(s, firma, EstadoScoringPredictivo.EN_PROCESO)
    if activa is not None:
        raise ScoringEnProceso(activa)
    ejecucion = EjecucionScoringPredictivo(
        version_modelo_predictivo_id=version.id,
        corte_canonico_id=corte.id,
        despacho_id=corte.despacho_id,
        cartera_id=corte.cartera_id,
        as_of=corte.fecha_corte,
        known_at=known_at,
        modo=modo,
        zona_horaria=zona,
        version_features=features_.VERSION_FEATURES,
        hash_features=features_.hash_features(),
        firma_inputs=firma,
        cuentas_objetivo=corte.cuentas,
    )
    error = insertar_en_savepoint(s, ejecucion)
    if error is not None:
        if restriccion(error) != INDICE_EN_PROCESO:
            raise error
        activa = _de_firma(s, firma, EstadoScoringPredictivo.EN_PROCESO)
        if (
            activa is None
        ):  # pragma: no cover - la que gano ya termino entre el rechazo y la revision
            raise error
        raise ScoringEnProceso(activa) from error
    cola.crear(
        s,
        TipoTrabajo.PREDICTIVE_SCORING,
        ejecucion.id,
        max_intentos=max_intentos,
        tomado_por=tomado_por,
        lease_segundos=lease_segundos,
    )
    log.info(
        "scoring %s abierto: modelo %s, corte %s, known_at %s",
        ejecucion.scoring_run_id,
        modelo_version_id,
        corte.corte_id,
        known_at.isoformat(),
    )
    return ejecucion


def _de_firma(
    s: Session, firma: str, estado: EstadoScoringPredictivo
) -> EjecucionScoringPredictivo | None:
    consulta = select(EjecucionScoringPredictivo).where(
        EjecucionScoringPredictivo.firma_inputs == firma,
        EjecucionScoringPredictivo.estado == estado,
    )
    if estado == EstadoScoringPredictivo.EN_PROCESO:
        consulta = consulta.with_for_update()
    return s.exec(consulta).one_or_none()


# --- puntuar --------------------------------------------------------------------------------------

ANTES_FEATURES = "ANTES_FEATURES"
DESPUES_FEATURES = "DESPUES_FEATURES"
DESPUES_INFERENCE = "DESPUES_INFERENCE"
DESPUES_DRIFT = "DESPUES_DRIFT"
DESPUES_STAGING = "DESPUES_STAGING"
ANTES_PUBLICAR = "ANTES_PUBLICAR"
ANTES_CERRAR = "ANTES_CERRAR"
PUNTOS_DE_FALLA = (
    ANTES_FEATURES,
    DESPUES_FEATURES,
    DESPUES_INFERENCE,
    DESPUES_DRIFT,
    DESPUES_STAGING,
    ANTES_PUBLICAR,
    ANTES_CERRAR,
)


def _punto_de_falla(nombre: str) -> None:
    """Un punto donde una prueba puede inyectar una falla. En produccion no hace nada."""


def ejecutar_scoring(ejecucion_id: int) -> None:
    """Lo que ejecuta un trabajo PREDICTIVE_SCORING."""
    with sesion() as s:
        ejecucion = s.get_one(EjecucionScoringPredictivo, ejecucion_id)
        if ejecucion.estado != EstadoScoringPredictivo.EN_PROCESO:
            log.info("scoring %s ya termino %s", ejecucion.scoring_run_id, ejecucion.estado)
            return
        known_at = ejecucion.known_at
    esperar_frontera(known_at)
    puntuar(ejecucion_id)


@dataclass
class _Corrida:
    ejecucion_id: int
    scoring_run_id: UUID
    relojes: construccion.Relojes
    politica: PoliticaEntrenamiento
    artefactos: dict[Target, dict]
    referencia: dict
    cartera_del_modelo: tuple[str, str]
    cuentas_objetivo: int
    firma: str


@dataclass
class Predicciones:
    """Lo que se publicaria de un corte, en el orden canonico, ya cuantizado."""

    filas: construccion.Filas
    enteros: dict[str, np.ndarray]
    """Por salida: la probabilidad en unidades de 1e-8, o el monto en centavos; y el riesgo."""
    estado: list[str]
    motivos: list[str]
    """El JSON de los motivos de cada fila."""

    def publicadas(self, salida: str, decimales: int) -> np.ndarray:
        return self.enteros[salida] / 10**decimales


def puntuar(ejecucion_id: int) -> None:
    """Puntua la ejecucion y la deja en un estado terminal, con su candado. Levanta
    ScoringYaPublicado si otro publico primero la misma firma, TrabajoAjeno si este worker perdio
    su trabajo, y los errores transitorios de la base, con la ejecucion EN_PROCESO."""
    with candado(f"predictive-scoring:{ejecucion_id}"):
        corrida = _leer(ejecucion_id)
        if corrida is None:
            return
        tiempos = medicion.Tiempos()
        try:
            predicciones, disponibilidad, filas_drift = _calcular(corrida, tiempos)
            _publicar(corrida, predicciones, disponibilidad, filas_drift, tiempos)
        except _NoSePublica as exc:
            _fallar(ejecucion_id, corrida.scoring_run_id, exc.resultado, str(exc))
        except OperationalError:
            log.warning("scoring %s: error transitorio; sigue EN_PROCESO", corrida.scoring_run_id)
            raise
        except (ScoringYaPublicado, cola.TrabajoAjeno):
            raise
        except Exception as exc:
            log.error("scoring %s: error inesperado%s", corrida.scoring_run_id, _traza(exc))
            _fallar(
                ejecucion_id,
                corrida.scoring_run_id,
                ResultadoScoringPredictivo.ERROR_INTERNO,
                f"Error interno ({type(exc).__name__}); ver la bitacora.",
            )


def _leer(ejecucion_id: int) -> _Corrida | None:
    with sesion() as s:
        e = s.get_one(EjecucionScoringPredictivo, ejecucion_id)
        if e.estado != EstadoScoringPredictivo.EN_PROCESO:
            log.warning("scoring %s ya termino %s", e.scoring_run_id, e.estado)
            return None
        version = s.get_one(VersionModeloPredictivo, e.version_modelo_predictivo_id)
        entrenamiento = s.get_one(
            EjecucionEntrenamientoPredictivo, version.ejecucion_entrenamiento_predictivo_id
        )
        componentes = s.exec(
            select(ComponenteModeloPredictivo).where(
                ComponenteModeloPredictivo.version_modelo_predictivo_id == version.id
            )
        ).all()
        politica = POLITICAS.get(version.version_politica)
        soportado = (
            version.estado == EstadoModeloPredictivo.PUBLICADA
            and e.version_features == features_.VERSION_FEATURES
            and e.hash_features == features_.hash_features()
            and politica is not None
            and len(componentes) == len(targets_.TARGETS)
            and all(artefacto_.hash_artefacto(c.artefacto) == c.hash_artefacto for c in componentes)
        )
        corrida = _Corrida(
            ejecucion_id=e.id,
            scoring_run_id=e.scoring_run_id,
            relojes=construccion.Relojes(
                e.corte_canonico_id,
                e.despacho_id,
                e.cartera_id,
                e.as_of,
                e.known_at,
                e.zona_horaria,
            ),
            politica=politica,
            artefactos={Target(c.target): c.artefacto for c in componentes},
            referencia=version.referencia_drift,
            cartera_del_modelo=(entrenamiento.despacho_id, entrenamiento.cartera_id),
            cuentas_objetivo=e.cuentas_objetivo,
            firma=e.firma_inputs,
        )
    if not soportado:
        _fallar(
            ejecucion_id,
            corrida.scoring_run_id,
            ResultadoScoringPredictivo.VERSION_NO_SOPORTADA,
            "El modelo no esta PUBLICADA con sus cuatro artefactos integros, o sus features no son "
            "las de este servicio; no se publico nada.",
        )
        return None
    return corrida


def predecir(
    filas: construccion.Filas,
    artefactos: dict[Target, dict],
    politica: PoliticaEntrenamiento,
    disponibilidad: construccion.Disponibilidad,
) -> Predicciones:
    """Las predicciones cuantizadas de unas filas, con el runtime de cada artefacto JSON, bloque
    por bloque."""
    n, bloque = len(filas), politica.filas_por_bloque
    enteros: dict[str, np.ndarray] = {}
    for target, artefacto in sorted(artefactos.items()):
        runtime = artefacto_.Runtime(artefacto)
        partes = [
            runtime.predecir(
                filas.numericas[i : i + bloque], filas.categoricas[i : i + bloque]
            ).publicada
            for i in range(0, n, bloque)
        ]
        valores = np.concatenate(partes) if partes else np.zeros(0)
        if artefacto["algoritmo"] == artefacto_.LOGISTICA:
            enteros[SALIDAS[target]] = artefacto_.cuantizar_probabilidad(
                valores, politica.decimales_probabilidad
            )
        else:
            enteros[SALIDAS[target]] = artefacto_.cuantizar_monto(valores, politica.decimales_monto)
    enteros[targets_.RIESGO_SIN_RECUPERACION_30D.lower()] = (
        10**politica.decimales_probabilidad - enteros["p_recuperacion_positiva_30d"]
    )
    estado, motivos = _motivos(filas, artefactos, disponibilidad)
    return Predicciones(filas, enteros, estado, motivos)


def motivo_del_nulo(feature: features_.Feature, disponibilidad: construccion.Disponibilidad) -> str:
    """Por que una numerica de una cuenta esta NULL. SIN_EVENTO no es una imputacion: su indicador
    dice que no hubo evento; los demas son un dato que no se conocia a known_at."""
    if feature.familia == Familia.SNAPSHOT:
        return "DATO_NO_REPORTADO"
    if feature.familia == Familia.HISTORIA:
        return "SIN_HISTORIA"
    if feature.familia == Familia.PAGOS:
        ventana = 30 if "30d" in feature.nombre else 90
        return "SIN_COBERTURA_PAGOS" if not disponibilidad.pagos[ventana] else "SIN_EVENTO"
    if feature.nombre.startswith("dias_desde"):
        return "SIN_EVENTO"
    return "SIN_EVALUACION_PROMESAS"


def _motivos(
    filas: construccion.Filas,
    artefactos: dict[Target, dict],
    disponibilidad: construccion.Disponibilidad,
) -> tuple[list[str], list[str]]:
    """El estado y los motivos de cada fila: que features se imputaron porque su dato no se
    conocia, y que categorias faltaban o no estaban en el vocabulario de TRAIN. Se calcula por
    patron de NULL, no por fila."""
    codigos = {f.nombre: motivo_del_nulo(f, disponibilidad) for f in NUMERICAS}
    imputables = [j for j, f in enumerate(NUMERICAS) if codigos[f.nombre] != "SIN_EVENTO"]
    nulos = np.isnan(filas.numericas[:, imputables]) if imputables else np.zeros((len(filas), 0))
    vocabularios = [
        set(c["vocabulario"][:-2])
        for c in next(iter(artefactos.values()))["preprocesamiento"]["categoricas"]
    ]
    desconocidas = np.zeros((len(filas), len(CATEGORICAS)), dtype=np.int8)
    for k, conocidas in enumerate(vocabularios):
        columna = filas.categoricas[:, k]
        desconocidas[:, k] = [0 if v in conocidas else (1 if v is None else 2) for v in columna]
    patron = np.concatenate([nulos.astype(np.int8), desconocidas], axis=1)
    unicos, inverso = np.unique(patron, axis=0, return_inverse=True)
    textos, estados = [], []
    for fila in unicos:
        por_motivo: dict[str, list[str]] = {}
        for posicion, j in enumerate(imputables):
            if fila[posicion]:
                por_motivo.setdefault(codigos[NUMERICAS[j].nombre], []).append(NUMERICAS[j].nombre)
        for k, f in enumerate(CATEGORICAS):
            valor = fila[len(imputables) + k]
            if valor:
                codigo = "CATEGORIA_FALTANTE" if valor == 1 else "CATEGORIA_DESCONOCIDA"
                por_motivo.setdefault(codigo, []).append(f.nombre)
        lista = [{"motivo": m, "features": por_motivo[m]} for m in sorted(por_motivo)]
        textos.append(json.dumps(lista, separators=(",", ":"), ensure_ascii=False))
        estados.append("CON_IMPUTACION" if lista else "COMPLETA")
    inverso = np.asarray(inverso).reshape(-1)
    return [estados[i] for i in inverso], [textos[i] for i in inverso]


def hash_predicciones(p: Predicciones) -> str:
    """El SHA-256 de lo publicado, fila por fila en el orden de cuenta_id: las probabilidades y el
    riesgo con 8 decimales, el monto en pesos con 2, el estado y el feature_hash."""
    digesto = hashlib.sha256()
    d = 8
    columnas = [
        p.enteros["p_recuperacion_positiva_30d"],
        p.enteros["p_contacto_14d"],
        p.enteros["p_promesa_30d"],
        p.enteros["riesgo_sin_recuperacion_30d"],
    ]
    montos = p.enteros["recuperacion_positiva_esperada_30d"]
    for i, cuenta_id in enumerate(p.filas.cuenta_id):
        linea = "|".join(
            [
                str(cuenta_id),
                *(artefacto_.texto_decimal(int(c[i]), d) for c in columnas),
                artefacto_.texto_decimal(int(montos[i]), 2),
                p.estado[i],
                p.filas.feature_hash[i].hex(),
            ]
        )
        digesto.update(linea.encode("utf-8") + b"\n")
    return digesto.hexdigest()


def _calcular(
    corrida: _Corrida, tiempos: medicion.Tiempos
) -> tuple[Predicciones, construccion.Disponibilidad, list[drift.Fila]]:
    _punto_de_falla(ANTES_FEATURES)
    with tiempos.fase("features"):
        with sesion() as s:
            disponibilidad = construccion.preparar_features(s, corrida.relojes)
            csv = construccion.copiar(s, sql.SQL_SCORING)
            s.rollback()
    with tiempos.fase("conversion"):
        filas = construccion.filas(csv)
        del csv
    if len(filas) != corrida.cuentas_objetivo:
        raise _NoSePublica(
            ResultadoScoringPredictivo.DATOS_INCONSISTENTES,
            f"El corte tiene {corrida.cuentas_objetivo:,} cuentas y se armaron las features de "
            f"{len(filas):,}.",
        )
    _punto_de_falla(DESPUES_FEATURES)
    with tiempos.fase("inferencia"):
        predicciones = predecir(filas, corrida.artefactos, corrida.politica, disponibilidad)
    _punto_de_falla(DESPUES_INFERENCE)
    with tiempos.fase("drift"):
        p = corrida.politica
        salidas = {
            SALIDAS[t]: predicciones.publicadas(
                SALIDAS[t],
                p.decimales_probabilidad
                if corrida.artefactos[t]["algoritmo"] == artefacto_.LOGISTICA
                else p.decimales_monto,
            )
            for t in corrida.artefactos
        }
        filas_drift = drift.calcular(
            corrida.referencia, filas.numericas, filas.categoricas, salidas, corrida.politica
        )
    _punto_de_falla(DESPUES_DRIFT)
    return predicciones, disponibilidad, filas_drift


def _csv_predicciones(ejecucion_id: int, corte_id: int, p: Predicciones) -> bytes:
    """Las predicciones como el CSV del COPY: los decimales como texto exacto, el feature_hash en
    la forma hexadecimal de bytea y los motivos como JSON."""

    def decimales(enteros: np.ndarray, d: int) -> pa.Array:
        escala = 10**d
        enteros = enteros.astype(np.int64)
        return pa.array(
            [f"{e // escala}.{e % escala:0{d}d}" for e in enteros.tolist()], type=pa.string()
        )

    n = len(p.filas)
    tabla = pa.table(
        {
            "ejecucion": pa.array(np.full(n, ejecucion_id, dtype=np.int64)),
            "cuenta": pa.array(p.filas.cuenta),
            "corte": pa.array(np.full(n, corte_id, dtype=np.int64)),
            "p_recuperacion": decimales(p.enteros["p_recuperacion_positiva_30d"], 8),
            "p_contacto": decimales(p.enteros["p_contacto_14d"], 8),
            "p_promesa": decimales(p.enteros["p_promesa_30d"], 8),
            "riesgo": decimales(p.enteros["riesgo_sin_recuperacion_30d"], 8),
            "esperada": decimales(p.enteros["recuperacion_positiva_esperada_30d"], 2),
            "estado": pa.array(p.estado, type=pa.string()),
            "motivos": pa.array(p.motivos, type=pa.string()),
            "feature_hash": pa.array(["\\x" + h.hex() for h in p.filas.feature_hash]),
        }
    )
    salida = pa.BufferOutputStream()
    pacsv.write_csv(
        tabla,
        salida,
        write_options=pacsv.WriteOptions(include_header=False, quoting_style="needed"),
    )
    return salida.getvalue().to_pybytes()


def _publicar(
    corrida: _Corrida,
    p: Predicciones,
    disponibilidad: construccion.Disponibilidad,
    filas_drift: list[drift.Fila],
    tiempos: medicion.Tiempos,
) -> None:
    with tiempos.fase("hashes"):
        del_scoring = hash_predicciones(p)
        del_drift = drift.hash_drift(filas_drift)
        csv = _csv_predicciones(corrida.ejecucion_id, corrida.relojes.corte_canonico_id, p)
    with sesion() as s:
        ejecucion = s.exec(
            select(EjecucionScoringPredictivo)
            .where(EjecucionScoringPredictivo.id == corrida.ejecucion_id)
            .with_for_update()
        ).one()
        if ejecucion.estado != EstadoScoringPredictivo.EN_PROCESO:
            log.warning("scoring %s ya termino %s", corrida.scoring_run_id, ejecucion.estado)
            return
        try:
            with s.begin_nested():
                with tiempos.fase("staging"):
                    s.execute(
                        text(
                            "CREATE TEMP TABLE ps_prediccion (LIKE prediccion_cuenta) "
                            "ON COMMIT DROP"
                        )
                    )
                    copiar_csv(s, "ps_prediccion", COLUMNAS_PREDICCION, [csv])
                _punto_de_falla(DESPUES_STAGING)
                _punto_de_falla(ANTES_PUBLICAR)
                with tiempos.fase("publicacion"):
                    columnas = ", ".join(COLUMNAS_PREDICCION)
                    publicadas = s.execute(
                        text(
                            f"INSERT INTO prediccion_cuenta ({columnas}) SELECT {columnas} "
                            "FROM ps_prediccion ORDER BY cuenta_canonica_id"
                        )
                    ).rowcount
                    s.add_all(_modelo_drift(corrida.ejecucion_id, f) for f in filas_drift)
                    s.flush()
                if publicadas != corrida.cuentas_objetivo:
                    raise _NoSePublica(
                        ResultadoScoringPredictivo.DATOS_INCONSISTENTES,
                        f"Se publicarian {publicadas:,} predicciones para "
                        f"{corrida.cuentas_objetivo:,} cuentas del corte.",
                    )
                _punto_de_falla(ANTES_CERRAR)
                _cerrar(
                    s,
                    ejecucion,
                    corrida,
                    publicadas,
                    del_scoring,
                    del_drift,
                    disponibilidad,
                    filas_drift,
                    tiempos,
                )
        except IntegrityError as exc:
            if restriccion(exc) != INDICE_EXITOSA:
                raise
            s.rollback()
            _fallar(
                corrida.ejecucion_id,
                corrida.scoring_run_id,
                ResultadoScoringPredictivo.YA_PUNTUADO,
                "Otro scoring publico la misma firma mientras este se calculaba; no se publica dos "
                "veces.",
            )
            with sesion() as s2:
                previa = _de_firma(s2, corrida.firma, EstadoScoringPredictivo.EXITOSA)
                s2.expunge_all()
            raise ScoringYaPublicado(previa) from exc
        cola.confirmar_dueno(s)
        s.commit()
    log.info(
        "scoring %s: %d predicciones publicadas", corrida.scoring_run_id, corrida.cuentas_objetivo
    )


def _modelo_drift(ejecucion_id: int, f: drift.Fila) -> DriftPredictivo:
    def decimal(valor: float | None, decimales: int) -> Decimal | None:
        return None if valor is None else Decimal(f"{valor:.{decimales}f}")

    return DriftPredictivo(
        ejecucion_scoring_predictivo_id=ejecucion_id,
        tipo=f.tipo,
        nombre=f.nombre,
        clase=f.clase,
        psi=decimal(f.psi, drift.DECIMALES_PSI),
        severidad=f.severidad,
        faltantes_train=decimal(f.faltantes_train, drift.DECIMALES_FRACCION),
        faltantes_scoring=decimal(f.faltantes_scoring, drift.DECIMALES_FRACCION),
        severidad_faltantes=f.severidad_faltantes,
        bins=f.bins,
    )


def _cerrar(
    s: Session,
    ejecucion: EjecucionScoringPredictivo,
    corrida: _Corrida,
    publicadas: int,
    del_scoring: str,
    del_drift: str,
    disponibilidad: construccion.Disponibilidad,
    filas_drift: list[drift.Fila],
    tiempos: medicion.Tiempos,
) -> None:
    distinta = (ejecucion.despacho_id, ejecucion.cartera_id) != corrida.cartera_del_modelo
    ejecucion.estado = EstadoScoringPredictivo.EXITOSA
    ejecucion.resultado = ResultadoScoringPredictivo.PREDICCIONES_PUBLICADAS.value
    ejecucion.predicciones_publicadas = publicadas
    ejecucion.hash_predicciones = del_scoring
    ejecucion.hash_drift = del_drift
    ejecucion.disponibilidad = disponibilidad.definicion()
    ejecucion.drift_resumen = {**drift.resumen(filas_drift), "cartera_distinta": distinta}
    ejecucion.tiempos = tiempos.resumen()
    ejecucion.detalle = (
        f"Se publicaron {publicadas:,} predicciones del corte del {ejecucion.as_of.isoformat()} "
        f"con lo que se conocia a {ejecucion.known_at.isoformat()}."
        + (
            f" El modelo se entreno con {corrida.cartera_del_modelo[0]}/"
            f"{corrida.cartera_del_modelo[1]}: su drift dice cuanto se parece esta cartera."
            if distinta
            else ""
        )
        + f" {AVISO}"
    )
    ejecucion.terminada_en = ahora()
    s.add(ejecucion)
    s.flush()


def _fallar(
    ejecucion_id: int, etiqueta: UUID, resultado: ResultadoScoringPredictivo, motivo: str
) -> None:
    with sesion() as s:
        try:
            cola.confirmar_dueno(s)
        except cola.TrabajoAjeno:
            s.rollback()
            log.warning("scoring %s: su trabajo ya no es de este worker: %s", etiqueta, motivo)
            raise
        s.execute(
            update(EjecucionScoringPredictivo)
            .where(
                EjecucionScoringPredictivo.id == ejecucion_id,
                EjecucionScoringPredictivo.estado == EstadoScoringPredictivo.EN_PROCESO,
            )
            .values(
                estado=EstadoScoringPredictivo.FALLIDA,
                resultado=resultado.value,
                predicciones_publicadas=0,
                hash_predicciones=None,
                hash_drift=None,
                detalle=motivo,
                terminada_en=ahora(),
            )
            .execution_options(synchronize_session=False)
        )
        s.commit()
    log.warning("scoring %s: FALLIDA (%s)", etiqueta, resultado.value)
