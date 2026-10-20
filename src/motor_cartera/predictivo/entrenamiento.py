"""Un entrenamiento de predictive/model-v1: el dataset de un manifiesto, cuatro GLM, su
calibracion, su evaluacion en TEST, su backtest y su publicacion, todo o nada.

El orden:

1. `abrir` resuelve el manifiesto (`manifiesto.resolver`), calcula la firma y registra la ejecucion
   EN_PROCESO con sus anclas, sus interpretaciones y su trabajo PREDICTIVE_TRAINING, en la
   transaccion de quien llama. La piden POST /predictivo/entrenamientos y `motor-cartera
   entrenar-predictivo`.
2. Un worker toma el trabajo, espera a que se asiente la frontera del label_known_at mas tardio de
   sus anclas (`decision_v2.ejecuciones.esperar_frontera`) y entrena.
3. `entrenar` toma el candado de la ejecucion (`candado`: un segundo worker espera a que el primero
   termine) y, sin una transaccion abierta mientras ajusta:
   - construye el dataset ancla por ancla, cada una en su propia transaccion corta de lectura
     (`dataset.construir`): las features a feature_known_at y los labels a label_known_at;
   - aprende el preprocesamiento de TRAIN, ajusta cada GLM de la rejilla de la politica con TRAIN
     y elige con VALIDATION, y decide la calibracion de Platt con VALIDATION;
   - construye el artefacto JSON de cada componente y evalua TEST una sola vez, con el runtime del
     artefacto y las probabilidades cuantizadas que se publicarian, contra el baseline de TRAIN,
     con intervalos bootstrap por cuenta y las compuertas de la politica; repite el procedimiento
     en los folds del walk-forward, y mide la referencia de TRAIN para el drift;
   - publica en una sola transaccion corta: la version (CANDIDATA, VALIDADA y PUBLICADA, o
     RECHAZADA) con sus cuatro componentes, y la ejecucion EXITOSA. Antes de confirmar comprueba que
     su trabajo sigue siendo suyo.

Si algo falla antes de publicar, la ejecucion queda FALLIDA y no queda ninguna version; un error
transitorio de la base la deja EN_PROCESO para que la cola la reintente. Nada de esto cambia
decision/v2, campo/v2 ni ruteo/v2, ni toca un modelo ya publicado.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import overload
from uuid import UUID

import numpy as np
from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session, col, select

from motor_cartera.analytics.catalogo import canonico
from motor_cartera.db.modelos import (
    AnclaEntrenamientoPredictivo,
    ComponenteModeloPredictivo,
    CorteCanonico,
    EjecucionEntrenamientoPredictivo,
    EjecucionMotorPagos,
    EstadoEntrenamientoPredictivo,
    EstadoModeloPredictivo,
    InterpretacionEntrenamientoPredictivo,
    ResultadoEntrenamientoPredictivo,
    TipoTrabajo,
    VersionModeloPredictivo,
    ahora,
    id_de,
)
from motor_cartera.db.sesion import insertar_en_savepoint, restriccion, sesion
from motor_cartera.decision_v2.ejecuciones import esperar_frontera
from motor_cartera.geografia.ejecuciones import _traza
from motor_cartera.invariantes import presente
from motor_cartera.orquestacion import cola
from motor_cartera.predictivo import artefacto as artefacto_
from motor_cartera.predictivo import dataset as dataset_
from motor_cartera.predictivo import features as features_
from motor_cartera.predictivo import glm, manifiesto, medicion, metricas
from motor_cartera.predictivo import preprocesamiento as pre_
from motor_cartera.predictivo import targets as targets_
from motor_cartera.predictivo.candado import candado
from motor_cartera.predictivo.features import CATEGORICAS, DESCONOCIDA, FALTANTE, NUMERICAS
from motor_cartera.predictivo.politica import (
    POLITICAS,
    VERSION_MODELO,
    PoliticaEntrenamiento,
    Split,
    hash_politica,
)
from motor_cartera.predictivo.targets import Madurez, Target, TipoTarget

log = logging.getLogger(__name__)

INDICE_EXITOSA = "ux_entrenamiento_predictivo_exitosa"
INDICE_EN_PROCESO = "ux_entrenamiento_predictivo_en_proceso"

AVISO = (
    "predictive/model-v1 estima la probabilidad de resultados observados bajo la operacion "
    "historica, con lo que se conocia en cada momento: no mide el efecto de ninguna accion, no "
    "recomienda una y no cambia decision/v2, campo/v2 ni ruteo/v2."
)


# --- errores al abrir -----------------------------------------------------------------------------


class EntrenamientoYaPublicado(Exception):
    """Otra ejecucion ya entreno exactamente el mismo manifiesto (la misma firma)."""

    def __init__(self, previa: EjecucionEntrenamientoPredictivo) -> None:
        super().__init__(
            f"Ese manifiesto ya se entreno: la ejecucion {previa.training_run_id} (firma "
            f"{previa.firma_inputs[:12]}...)."
        )
        self.previa = previa


class EntrenamientoEnProceso(Exception):
    """Ese mismo manifiesto ya se esta entrenando."""

    def __init__(self, activa: EjecucionEntrenamientoPredictivo) -> None:
        super().__init__(
            f"Ese manifiesto ya se esta entrenando: la ejecucion {activa.training_run_id}."
        )
        self.activa = activa


class _NoSePublica(Exception):
    def __init__(self, resultado: ResultadoEntrenamientoPredictivo, mensaje: str) -> None:
        super().__init__(mensaje)
        self.resultado = resultado


# --- abrir ----------------------------------------------------------------------------------------


def abrir(
    s: Session,
    declarado: manifiesto.ManifiestoDeclarado,
    *,
    max_intentos: int,
    tomado_por: str | None = None,
    lease_segundos: float | None = None,
) -> EjecucionEntrenamientoPredictivo:
    """La ejecucion EN_PROCESO, con sus anclas, sus interpretaciones y su trabajo, en la transaccion
    de quien llama: envia los INSERT y no confirma. Levanta, sin registrar nada, un
    manifiesto.ManifiestoInvalido, EntrenamientoYaPublicado o EntrenamientoEnProceso."""
    r = manifiesto.resolver(s, declarado)
    previa = _de_firma(s, r.firma, EstadoEntrenamientoPredictivo.EXITOSA)
    if previa is not None:
        raise EntrenamientoYaPublicado(previa)
    activa = _de_firma(s, r.firma, EstadoEntrenamientoPredictivo.EN_PROCESO)
    if activa is not None:
        raise EntrenamientoEnProceso(activa)
    ejecucion = EjecucionEntrenamientoPredictivo(
        despacho_id=r.despacho_id,
        cartera_id=r.cartera_id,
        zona_horaria=r.zona_horaria,
        version_modelo=VERSION_MODELO,
        version_features=features_.VERSION_FEATURES,
        hash_features=features_.hash_features(),
        version_targets=targets_.VERSION_TARGETS,
        hash_targets=targets_.hash_targets(),
        version_politica=r.politica.version,
        hash_politica=hash_politica(r.politica),
        manifiesto=r.canonico(),
        hash_manifiesto=r.hash_manifiesto,
        firma_inputs=r.firma,
        anclas=len(r.anclas),
    )
    error = insertar_en_savepoint(s, ejecucion)
    if error is not None:
        if restriccion(error) != INDICE_EN_PROCESO:
            raise error
        activa = _de_firma(s, r.firma, EstadoEntrenamientoPredictivo.EN_PROCESO)
        if (
            activa is None
        ):  # pragma: no cover - la que gano ya termino entre el rechazo y la revision
            raise error
        raise EntrenamientoEnProceso(activa) from error
    for ancla in r.anclas:
        d = ancla.declarada
        s.add(
            AnclaEntrenamientoPredictivo(
                ejecucion_entrenamiento_predictivo_id=id_de(ejecucion),
                corte_canonico_id=id_de(ancla.corte),
                feature_as_of=d.feature_as_of,
                feature_known_at=d.feature_known_at,
                label_known_at=d.label_known_at,
                pagos_completos_hasta=d.pagos_completos_hasta,
                lifecycle_completo_hasta=d.lifecycle_completo_hasta,
                split=d.split,
                hash_ancla=ancla.hash(),
            )
        )
    for interpretacion in r.interpretaciones:
        s.add(
            InterpretacionEntrenamientoPredictivo(
                ejecucion_entrenamiento_predictivo_id=id_de(ejecucion),
                periodo_desde=interpretacion.ejecucion.periodo_desde,
                ejecucion_motor_pagos_id=id_de(interpretacion.ejecucion),
            )
        )
    s.flush()
    cola.crear(
        s,
        TipoTrabajo.PREDICTIVE_TRAINING,
        id_de(ejecucion),
        max_intentos=max_intentos,
        tomado_por=tomado_por,
        lease_segundos=lease_segundos,
    )
    log.info(
        "entrenamiento %s abierto: %d anclas de %s/%s, %d interpretaciones",
        ejecucion.training_run_id,
        len(r.anclas),
        r.despacho_id,
        r.cartera_id,
        len(r.interpretaciones),
    )
    return ejecucion


def _de_firma(
    s: Session, firma: str, estado: EstadoEntrenamientoPredictivo
) -> EjecucionEntrenamientoPredictivo | None:
    consulta = select(EjecucionEntrenamientoPredictivo).where(
        EjecucionEntrenamientoPredictivo.firma_inputs == firma,
        EjecucionEntrenamientoPredictivo.estado == estado,
    )
    if estado == EstadoEntrenamientoPredictivo.EN_PROCESO:
        consulta = consulta.with_for_update()
    return s.exec(consulta).one_or_none()


# --- entrenar -------------------------------------------------------------------------------------

ANTES_DATASET = "ANTES_DATASET"
DESPUES_FEATURES = dataset_.FEATURES_DE_LA_ANCLA
DESPUES_LABELS = dataset_.LABELS_DE_LA_ANCLA
DESPUES_SPLIT = "DESPUES_SPLIT"
DESPUES_ENTRENAMIENTO = "DESPUES_ENTRENAMIENTO"
DESPUES_CALIBRACION = "DESPUES_CALIBRACION"
DESPUES_EVALUACION = "DESPUES_EVALUACION"
ANTES_PUBLICAR_MODELO = "ANTES_PUBLICAR_MODELO"
PUNTOS_DE_FALLA = (
    ANTES_DATASET,
    DESPUES_FEATURES,
    DESPUES_LABELS,
    DESPUES_SPLIT,
    DESPUES_ENTRENAMIENTO,
    DESPUES_CALIBRACION,
    DESPUES_EVALUACION,
    ANTES_PUBLICAR_MODELO,
)


def _punto_de_falla(nombre: str) -> None:
    """Un punto donde una prueba puede inyectar una falla. En produccion no hace nada."""


def ejecutar_entrenamiento(ejecucion_id: int) -> None:
    """Lo que ejecuta un trabajo PREDICTIVE_TRAINING. Si la ejecucion ya termino no hace nada; si
    no, espera a que su frontera de conocimiento se asiente y entrena."""
    with sesion() as s:
        ejecucion = s.get_one(EjecucionEntrenamientoPredictivo, ejecucion_id)
        if ejecucion.estado != EstadoEntrenamientoPredictivo.EN_PROCESO:
            log.info("entrenamiento %s ya termino %s", ejecucion.training_run_id, ejecucion.estado)
            return
        frontera = s.exec(
            select(func.max(AnclaEntrenamientoPredictivo.label_known_at)).where(
                AnclaEntrenamientoPredictivo.ejecucion_entrenamiento_predictivo_id == ejecucion_id
            )
        ).one()
    esperar_frontera(frontera)
    entrenar(ejecucion_id)


@dataclass
class _Corrida:
    """Lo que el entrenamiento fija al empezar: leido de la ejecucion, nunca del codigo."""

    ejecucion_id: int
    training_run_id: UUID
    despacho_id: str
    cartera_id: str
    zona: str
    politica: PoliticaEntrenamiento
    anclas: list[dataset_.AnclaDataset]
    interpretaciones: list[int]
    firma: str
    hash_manifiesto: str


def entrenar(ejecucion_id: int) -> None:
    """Entrena la ejecucion y la deja en un estado terminal, con el candado de la ejecucion: si
    otro worker la tiene, espera a que termine. Levanta EntrenamientoYaPublicado si otra ejecucion
    publico primero la misma firma, TrabajoAjeno si este worker perdio su trabajo, y los errores
    transitorios de la base, con la ejecucion EN_PROCESO para que la cola la reintente.

    No espera la frontera: eso lo hace `ejecutar_entrenamiento`. Quien llame directamente tiene
    que saber que el label_known_at mas tardio ya paso y que nada quedo abierto antes de el."""
    with candado(f"predictive-training:{ejecucion_id}"):
        corrida = _leer(ejecucion_id)
        if corrida is None:
            return
        tiempos = medicion.Tiempos()
        try:
            resultado = _entrenar(corrida, tiempos)
            _publicar(corrida, resultado, tiempos)
        except _NoSePublica as exc:
            _fallar(ejecucion_id, corrida.training_run_id, exc.resultado, str(exc))
        except OperationalError:
            log.warning(
                "entrenamiento %s: error transitorio; sigue EN_PROCESO", corrida.training_run_id
            )
            raise
        except (EntrenamientoYaPublicado, cola.TrabajoAjeno):
            # Ya quedo FALLIDA (otra publico primero), o ya no es de este worker.
            raise
        except dataset_.DatosInconsistentes as exc:
            _fallar(
                ejecucion_id,
                corrida.training_run_id,
                ResultadoEntrenamientoPredictivo.DATOS_INCONSISTENTES,
                str(exc),
            )
        except Exception as exc:
            # Sin el mensaje: el de un error de la base puede citar una fila de una cuenta.
            log.error("entrenamiento %s: error inesperado%s", corrida.training_run_id, _traza(exc))
            _fallar(
                ejecucion_id,
                corrida.training_run_id,
                ResultadoEntrenamientoPredictivo.ERROR_INTERNO,
                f"Error interno ({type(exc).__name__}); ver la bitacora.",
            )


def _leer(ejecucion_id: int) -> _Corrida | None:
    with sesion() as s:
        e = s.get_one(EjecucionEntrenamientoPredictivo, ejecucion_id)
        if e.estado != EstadoEntrenamientoPredictivo.EN_PROCESO:
            log.warning("entrenamiento %s ya termino %s", e.training_run_id, e.estado)
            return None
        politica = POLITICAS.get(e.version_politica)
        soportada = (
            e.version_modelo == VERSION_MODELO
            and e.version_features == features_.VERSION_FEATURES
            and e.hash_features == features_.hash_features()
            and e.version_targets == targets_.VERSION_TARGETS
            and e.hash_targets == targets_.hash_targets()
            and politica is not None
            and e.hash_politica == hash_politica(politica)
        )
        anclas = [
            dataset_.AnclaDataset(
                corte_canonico_id=a.corte_canonico_id,
                corte_id=corte_id,
                feature_as_of=a.feature_as_of,
                feature_known_at=a.feature_known_at,
                label_known_at=a.label_known_at,
                pagos_completos_hasta=a.pagos_completos_hasta,
                lifecycle_completo_hasta=a.lifecycle_completo_hasta,
                split=a.split,
                hash_ancla=a.hash_ancla,
            )
            for a, corte_id in s.exec(
                select(AnclaEntrenamientoPredictivo, CorteCanonico.corte_id)
                .join(
                    CorteCanonico,
                    col(CorteCanonico.id) == AnclaEntrenamientoPredictivo.corte_canonico_id,
                )
                .where(
                    AnclaEntrenamientoPredictivo.ejecucion_entrenamiento_predictivo_id
                    == ejecucion_id
                )
            ).all()
        ]
        interpretaciones = list(
            s.exec(
                select(InterpretacionEntrenamientoPredictivo.ejecucion_motor_pagos_id)
                .where(
                    InterpretacionEntrenamientoPredictivo.ejecucion_entrenamiento_predictivo_id
                    == ejecucion_id
                )
                .order_by(col(InterpretacionEntrenamientoPredictivo.periodo_desde))
            ).all()
        )
        if politica is None or not soportada:
            corrida = None
        else:
            corrida = _Corrida(
                ejecucion_id=id_de(e),
                training_run_id=e.training_run_id,
                despacho_id=e.despacho_id,
                cartera_id=e.cartera_id,
                zona=e.zona_horaria,
                politica=politica,
                anclas=anclas,
                interpretaciones=interpretaciones,
                firma=e.firma_inputs,
                hash_manifiesto=e.hash_manifiesto,
            )
    if corrida is None:
        _fallar(
            ejecucion_id,
            e.training_run_id,
            ResultadoEntrenamientoPredictivo.VERSION_NO_SOPORTADA,
            f"La ejecucion pide {e.version_modelo}, {e.version_features}, {e.version_targets} y "
            f"{e.version_politica} con sus hashes, y este servicio no entrena exactamente eso; no "
            "se publico nada.",
        )
        return None
    return corrida


# --- el diseno por bloques ------------------------------------------------------------------------


class _Diseno:
    """La matriz de diseno de un preprocesamiento sobre las filas de unas anclas, bloque por bloque,
    sin materializarla entera. Las categoricas de cada ancla se traducen una vez a los indices del
    vocabulario de TRAIN."""

    def __init__(
        self, pre: pre_.Preprocesamiento, diccionario: dataset_.Diccionario, filas: int
    ) -> None:
        self.pre = pre
        self.filas = filas
        self.columnas = len(pre.columnas)
        self._mapas = [_mapa(cat, diccionario) for cat in pre.categoricas]
        self._indices: dict[int, np.ndarray] = {}

    def indices(self, bloque: dataset_.BloqueAncla) -> np.ndarray:
        clave = id(bloque)
        if clave not in self._indices:
            indices = np.empty((bloque.filas, len(self._mapas)), dtype=np.int16)
            for k, mapa in enumerate(self._mapas):
                indices[:, k] = mapa[bloque.categoricas[:, k]]
            self._indices[clave] = indices
        return self._indices[clave]

    def recorrer(self, bloques: list[dataset_.BloqueAncla]):
        for b in bloques:
            indices = self.indices(b)
            for inicio in range(0, b.filas, self.filas):
                fin = inicio + self.filas
                yield (
                    b,
                    inicio,
                    fin,
                    self.pre.disenar_indices(b.numericas[inicio:fin], indices[inicio:fin]),
                )

    def bloques(self, bloques: list[dataset_.BloqueAncla], target: Target) -> glm.Bloques:
        def recorrer():
            for b, inicio, fin, x in self.recorrer(bloques):
                yield x, b.labels[target][inicio:fin]

        return recorrer

    def eta(self, bloques: list[dataset_.BloqueAncla], ajuste: glm.Ajuste) -> np.ndarray:
        partes = [glm.predictor_lineal(x, ajuste) for _, _, _, x in self.recorrer(bloques)]
        return np.concatenate(partes) if partes else np.zeros(0)


def _mapa(cat: pre_.Categorica, diccionario: dataset_.Diccionario) -> np.ndarray:
    """Del codigo comun de un texto (y de -1, NULL, en la ultima posicion) al indice del
    vocabulario: un texto que TRAIN no vio es __UNKNOWN__."""
    desconocida = cat.vocabulario.index(DESCONOCIDA)
    mapa = np.full(len(diccionario.textos) + 1, desconocida, dtype=np.int16)
    for i, valor in enumerate(cat.vocabulario[:-2]):
        codigo = diccionario.buscar(valor)
        if codigo is not None:
            mapa[codigo] = i
    mapa[-1] = cat.vocabulario.index(FALTANTE)
    return mapa


def _y(bloques: list[dataset_.BloqueAncla], target: Target) -> np.ndarray:
    partes = [b.labels[target] for b in bloques]
    return np.concatenate(partes) if partes else np.zeros(0)


def _cuentas(bloques: list[dataset_.BloqueAncla]) -> np.ndarray:
    partes = [b.cuenta for b in bloques]
    return np.concatenate(partes) if partes else np.zeros(0, dtype=np.int32)


def _maduras(bloques: list[dataset_.BloqueAncla], target: Target) -> list[dataset_.BloqueAncla]:
    return [b for b in bloques if b.madurez[target] == Madurez.MADURA and b.filas]


def _preprocesamiento(
    bloques: list[dataset_.BloqueAncla], diccionario: dataset_.Diccionario, cifras: int
) -> pre_.Preprocesamiento:
    """El preprocesamiento de las filas de unas anclas (las de TRAIN, o las de entrenamiento de un
    fold), y solo de ellas."""
    filas = sum(b.filas for b in bloques)
    conteos = []
    for k in range(len(CATEGORICAS)):
        cuantos = np.zeros(len(diccionario.textos) + 1, dtype=np.int64)
        for b in bloques:
            cuantos += np.bincount(b.categoricas[:, k] + 1, minlength=len(cuantos))
        conteos.append(
            {
                (None if codigo == 0 else diccionario.textos[codigo - 1]): int(c)
                for codigo, c in enumerate(cuantos)
                if c
            }
        )
    return pre_.ajustar_por_columnas(
        lambda j: np.concatenate([b.numericas[:, j] for b in bloques]),
        conteos,
        filas=filas,
        cifras=cifras,
    )


def _redondeado(ajuste: glm.Ajuste, cifras: int) -> glm.Ajuste:
    """El ajuste con los numeros que guarda el artefacto: lo que se elige y se calibra es lo que se
    publica."""
    return glm.Ajuste(
        np.array([pre_.redondear(float(c), cifras) for c in ajuste.coeficientes]),
        pre_.redondear(ajuste.intercepto, cifras),
        ajuste.iteraciones,
        ajuste.convergio,
        ajuste.objetivo,
    )


# --- el procedimiento -----------------------------------------------------------------------------


@dataclass
class _Ajustado:
    """Un componente ajustado y elegido con VALIDATION, todavia sin calibrar."""

    target: Target
    algoritmo: str
    hiperparametros: dict
    ajuste: glm.Ajuste
    rejilla: list[dict]
    linea_base: dict
    """El baseline constante de TRAIN medido en VALIDATION: con lo que se compara la rejilla."""
    calibracion: tuple[float, float] | None = None
    decision_calibracion: dict | None = None


@dataclass
class _Componente:
    target: Target
    algoritmo: str
    hiperparametros: dict
    artefacto: dict
    hash_artefacto: str
    calibracion: str
    metricas_validation: dict
    metricas_test: dict
    baseline_test: dict
    bootstrap_test: dict
    explicacion_global: list[dict]
    compuertas: list[dict]

    @property
    def validado(self) -> bool:
        return all(c["ok"] for c in self.compuertas)


@dataclass
class _Resultado:
    dataset: dataset_.Dataset
    componentes: list[_Componente]
    periodos: dict
    referencia_drift: dict
    metadatos: dict
    backtest: dict
    matriz: dict


def _entrenar(corrida: _Corrida, tiempos: medicion.Tiempos) -> _Resultado:
    politica = corrida.politica
    _punto_de_falla(ANTES_DATASET)
    fin_validation = max(a.feature_as_of for a in corrida.anclas if a.split == Split.VALIDATION)
    with tiempos.fase("dataset"):
        datos = dataset_.construir(
            sesion,
            despacho_id=corrida.despacho_id,
            cartera_id=corrida.cartera_id,
            zona=corrida.zona,
            anclas=corrida.anclas,
            interpretaciones=corrida.interpretaciones,
            usadas=lambda a: a.split != Split.PURGE or a.feature_as_of <= fin_validation,
            punto=lambda nombre: _punto_de_falla(nombre),
        )
    log.info(
        "entrenamiento %s: dataset de %d anclas, %d filas, %d cuentas en %.1fs",
        corrida.training_run_id,
        len(datos.bloques),
        sum(datos.filas_por_split.values()),
        datos.cuentas,
        datos.segundos,
    )
    train, validation, test = (
        datos.de(Split.TRAIN),
        datos.de(Split.VALIDATION),
        datos.de(Split.TEST),
    )
    for t in targets_.TARGETS:
        for nombre, bloques in (("TRAIN", train), ("VALIDATION", validation), ("TEST", test)):
            if not _maduras(bloques, t.target):
                raise _NoSePublica(
                    ResultadoEntrenamientoPredictivo.SIN_EJEMPLOS,
                    f"{nombre} no tiene ejemplos maduros de {t.target}.",
                )
        y = _y(_maduras(train, t.target), t.target)
        if t.tipo == TipoTarget.BINARIO and (y.min() == y.max()):
            raise _NoSePublica(
                ResultadoEntrenamientoPredictivo.SIN_EJEMPLOS,
                f"TRAIN no tiene las dos clases de {t.target}: no hay nada que ajustar.",
            )
        if t.tipo == TipoTarget.MONTO and not np.any(y > 0):
            raise _NoSePublica(
                ResultadoEntrenamientoPredictivo.SIN_EJEMPLOS,
                f"TRAIN no tiene ninguna recuperacion positiva de {t.target}.",
            )
    _punto_de_falla(DESPUES_SPLIT)

    with tiempos.fase("preprocesamiento"):
        pre = _preprocesamiento(train, datos.diccionario, politica.cifras_significativas)
        diseno = _Diseno(pre, datos.diccionario, politica.filas_por_bloque)
    ajustados: list[_Ajustado] = []
    for t in targets_.TARGETS:
        with tiempos.fase(f"ajuste_{t.target.value.lower()}"):
            ajustados.append(
                _ajustar_y_elegir(
                    t,
                    diseno,
                    _maduras(train, t.target),
                    _maduras(validation, t.target),
                    politica,
                )
            )
    _punto_de_falla(DESPUES_ENTRENAMIENTO)

    with tiempos.fase("calibracion"):
        for a in ajustados:
            if a.algoritmo == artefacto_.LOGISTICA:
                _calibrar(a, diseno, _maduras(validation, a.target), politica)
    _punto_de_falla(DESPUES_CALIBRACION)

    textos = np.array([*datos.diccionario.textos, None], dtype=object)
    componentes: list[_Componente] = []
    with tiempos.fase("evaluacion"):
        for a in ajustados:
            componentes.append(_evaluar(a, pre, train, validation, test, textos, politica))
    with tiempos.fase("walk_forward"):
        backtest = _walk_forward(datos, ajustados, politica, componentes)
    with tiempos.fase("referencia_drift"):
        referencia = _referencia_drift(train, diseno, pre, componentes, textos, politica)
    periodos = {
        f"periodo_{nombre}_{extremo}": funcion(b.ancla.feature_as_of for b in bloques)
        for nombre, bloques in (("train", train), ("validation", validation), ("test", test))
        for extremo, funcion in (("desde", min), ("hasta", max))
    }
    _punto_de_falla(DESPUES_EVALUACION)
    return _Resultado(
        dataset=datos,
        componentes=componentes,
        periodos=periodos,
        referencia_drift=referencia,
        metadatos=_ficha(corrida, datos, componentes, periodos, politica),
        backtest=backtest,
        matriz={
            "features": len(features_.FEATURES),
            "columnas": diseno.columnas,
            "filas_train": sum(b.filas for b in train),
            "filas_por_bloque": politica.filas_por_bloque,
            "bytes_por_bloque": politica.filas_por_bloque * diseno.columnas * 8,
        },
    )


def _ajustar_y_elegir(
    t: targets_.DefinicionTarget,
    diseno: _Diseno,
    train: list[dataset_.BloqueAncla],
    validation: list[dataset_.BloqueAncla],
    politica: PoliticaEntrenamiento,
) -> _Ajustado:
    """Cada valor de la rejilla, ajustado con TRAIN y medido en VALIDATION; el elegido por la regla
    de la politica."""
    cifras = politica.cifras_significativas
    y_val = _y(validation, t.target)
    base = float(np.mean(_y(train, t.target)))
    constante = np.full(len(y_val), base)
    rejilla, ajustes = [], {}
    if t.tipo == TipoTarget.BINARIO:
        for c in politica.grid_c:
            ajuste = _redondeado(
                glm.ajustar_logistica(
                    diseno.bloques(train, t.target),
                    diseno.columnas,
                    c=c,
                    libres=diseno.pre.libres,
                    tolerancia=politica.tolerancia_newton,
                    iteraciones_maximas=politica.iteraciones_maximas,
                ),
                cifras,
            )
            p = glm.sigmoide(diseno.eta(validation, ajuste))
            ajustes[c] = ajuste
            rejilla.append(
                {
                    "c": c,
                    "log_loss": metricas.log_loss(y_val, p),
                    "brier": metricas.brier(y_val, p),
                    "roc_auc": metricas.roc_auc(y_val, p),
                    "pr_auc": metricas.pr_auc(y_val, p),
                    "iteraciones": ajuste.iteraciones,
                    "convergio": ajuste.convergio,
                }
            )
        mejor = min(r["log_loss"] for r in rejilla)
        elegido = min(
            r["c"] for r in rejilla if r["log_loss"] <= mejor + politica.tolerancia_empate_log_loss
        )
        return _Ajustado(
            t.target,
            artefacto_.LOGISTICA,
            {"c": elegido, "penalizacion": "L2", "rebalanceo": None},
            ajustes[elegido],
            rejilla,
            {
                "prevalencia_train": base,
                "prevalencia_validation": float(np.mean(y_val)),
                "log_loss": metricas.log_loss(y_val, constante),
                "brier": metricas.brier(y_val, constante),
            },
        )
    for alpha in politica.grid_alpha:
        ajuste = _redondeado(
            glm.ajustar_tweedie(
                diseno.bloques(train, t.target),
                diseno.columnas,
                alpha=alpha,
                potencia=politica.potencia_tweedie,
                libres=diseno.pre.libres,
                tolerancia=politica.tolerancia_newton,
                iteraciones_maximas=politica.iteraciones_maximas,
            ),
            cifras,
        )
        mu = np.exp(diseno.eta(validation, ajuste))
        ajustes[alpha] = ajuste
        rejilla.append(
            {
                "alpha": alpha,
                "mae": metricas.mae(y_val, mu),
                "rmse": float(np.sqrt(np.mean((y_val - mu) ** 2))),
                "iteraciones": ajuste.iteraciones,
                "convergio": ajuste.convergio,
            }
        )
    mejor = min(r["mae"] for r in rejilla)
    elegido = max(
        r["alpha"] for r in rejilla if r["mae"] <= mejor * (1 + politica.tolerancia_empate_mae)
    )
    return _Ajustado(
        t.target,
        artefacto_.TWEEDIE,
        {"alpha": elegido, "potencia": politica.potencia_tweedie, "liga": "log"},
        ajustes[elegido],
        rejilla,
        {
            "media_train": base,
            "media_validation": float(np.mean(y_val)),
            "mae": metricas.mae(y_val, constante),
        },
    )


def _calibrar(
    a: _Ajustado,
    diseno: _Diseno,
    validation: list[dataset_.BloqueAncla],
    politica: PoliticaEntrenamiento,
) -> None:
    """Platt en VALIDATION, adoptado solo si su Brier en VALIDATION mejora el crudo al menos en la
    fraccion de la politica. Nunca con TEST."""
    cifras = politica.cifras_significativas
    y = _y(validation, a.target)
    eta = diseno.eta(validation, a.ajuste)
    crudo = metricas.brier(y, glm.sigmoide(eta))
    pa, pb = glm.platt(eta, y)
    pa, pb = pre_.redondear(pa, cifras), pre_.redondear(pb, cifras)
    calibrado = metricas.brier(y, glm.sigmoide(pa + pb * eta))
    adoptado = calibrado <= crudo * (1 - politica.mejora_minima_platt)
    a.calibracion = (pa, pb) if adoptado else None
    a.decision_calibracion = {
        "brier_validation_raw": crudo,
        "brier_validation_platt": calibrado,
        "platt": {"a": pa, "b": pb},
        "mejora_minima": politica.mejora_minima_platt,
        "elegida": artefacto_.PLATT if adoptado else artefacto_.RAW,
    }


def _predecir(
    runtime: artefacto_.Runtime,
    bloques: list[dataset_.BloqueAncla],
    textos: np.ndarray,
    filas: int,
    *,
    cruda: bool = False,
) -> np.ndarray:
    """Lo publicado por el runtime del artefacto (la probabilidad calibrada o el monto), o la
    probabilidad cruda, sin cuantizar, fila por fila en el orden de las anclas."""
    partes = []
    for b in bloques:
        categoricas = textos[b.categoricas]
        for inicio in range(0, b.filas, filas):
            fin = inicio + filas
            prediccion = runtime.predecir(b.numericas[inicio:fin], categoricas[inicio:fin])
            partes.append(prediccion.cruda if cruda else prediccion.publicada)
    return np.concatenate(partes) if partes else np.zeros(0)


def publicada(
    prediccion: np.ndarray, algoritmo: str, politica: PoliticaEntrenamiento
) -> np.ndarray:
    """Lo que se publicaria: la probabilidad con sus decimales, o el monto en centavos."""
    if algoritmo == artefacto_.LOGISTICA:
        d = politica.decimales_probabilidad
        return artefacto_.cuantizar_probabilidad(prediccion, d) / 10**d
    d = politica.decimales_monto
    return artefacto_.cuantizar_monto(prediccion, d) / 10**d


def _evaluar(
    a: _Ajustado,
    pre: pre_.Preprocesamiento,
    train: list[dataset_.BloqueAncla],
    validation: list[dataset_.BloqueAncla],
    test: list[dataset_.BloqueAncla],
    textos: np.ndarray,
    politica: PoliticaEntrenamiento,
) -> _Componente:
    """El artefacto del componente y su evaluacion: VALIDATION y TEST con el runtime del
    artefacto, contra el baseline constante de TRAIN, con sus intervalos y sus compuertas."""
    artefacto = artefacto_.construir(
        target=a.target.value,
        algoritmo=a.algoritmo,
        preprocesamiento=pre,
        ajuste=a.ajuste,
        hiperparametros=a.hiperparametros,
        calibracion=a.calibracion,
        cifras=politica.cifras_significativas,
        decimales_probabilidad=politica.decimales_probabilidad,
        decimales_monto=politica.decimales_monto,
    )
    runtime = artefacto_.Runtime(artefacto)
    train_m, val_m, test_m = (
        _maduras(train, a.target),
        _maduras(validation, a.target),
        _maduras(test, a.target),
    )
    y_train, y_val, y_test = _y(train_m, a.target), _y(val_m, a.target), _y(test_m, a.target)
    filas = politica.filas_por_bloque
    p_val = publicada(_predecir(runtime, val_m, textos, filas), a.algoritmo, politica)
    p_test = publicada(_predecir(runtime, test_m, textos, filas), a.algoritmo, politica)
    cuentas_test = _cuentas(test_m)
    clasificador = a.algoritmo == artefacto_.LOGISTICA
    if clasificador:
        base = float(np.mean(y_train))
        constante = np.full(len(y_test), base)
        bins, fracciones = politica.bins_calibracion, politica.fracciones_lift
        validacion = {
            "rejilla": a.rejilla,
            "elegido": a.hiperparametros,
            "calibracion": a.decision_calibracion,
            "metricas": metricas.clasificacion(y_val, p_val, bins=bins, fracciones=fracciones),
        }
        prueba = metricas.clasificacion(y_test, p_test, bins=bins, fracciones=fracciones)
        prueba["tabla_calibracion"] = metricas.tabla_calibracion(y_test, p_test, bins)
        if a.calibracion is not None:
            # Solo se reporta: la calibracion se decidio en VALIDATION y TEST no la cambia.
            p_cruda = publicada(
                _predecir(runtime, test_m, textos, filas, cruda=True), a.algoritmo, politica
            )
            prueba["sin_calibrar"] = _resumen(
                metricas.clasificacion(y_test, p_cruda, bins=bins, fracciones=fracciones)
            )
        baseline = metricas.clasificacion(y_test, constante, bins=bins, fracciones=fracciones)
        baseline["prediccion"] = base
        estadisticos = {
            "roc_auc": lambda w: metricas.roc_auc(y_test, p_test, w),
            "pr_auc": lambda w: metricas.pr_auc(y_test, p_test, w),
            "log_loss": lambda w: metricas.log_loss(y_test, p_test, w),
            "brier": lambda w: metricas.brier(y_test, p_test, w),
            "diferencia_log_loss": lambda w: (
                metricas.log_loss(y_test, p_test, w) - metricas.log_loss(y_test, constante, w)
            ),
            "diferencia_brier": lambda w: (
                metricas.brier(y_test, p_test, w) - metricas.brier(y_test, constante, w)
            ),
        }
        compuertas = [
            _compuerta(
                "LOG_LOSS_NO_PEOR_QUE_BASELINE", prueba["log_loss"], "<=", baseline["log_loss"]
            ),
            _compuerta("BRIER_NO_PEOR_QUE_BASELINE", prueba["brier"], "<=", baseline["brier"]),
            _compuerta("ROC_AUC_MAYOR_QUE_0_5", prueba["roc_auc"], ">", 0.5),
            _compuerta(
                "PROBABILIDADES_VALIDAS",
                bool(np.all(np.isfinite(p_test)) and np.all((p_test >= 0) & (p_test <= 1))),
                "==",
                True,
            ),
        ]
    else:
        base = float(np.mean(y_train))
        constante = np.full(len(y_test), base)
        bins = politica.bins_calibracion
        validacion = {
            "rejilla": a.rejilla,
            "elegido": a.hiperparametros,
            "metricas": metricas.monto(y_val, p_val, bins=bins),
        }
        prueba = metricas.monto(y_test, p_test, bins=bins)
        baseline = metricas.monto(y_test, constante, bins=bins)
        baseline["prediccion"] = base
        estadisticos = {
            "mae": lambda w: metricas.mae(y_test, p_test, w),
            "diferencia_mae": lambda w: (
                metricas.mae(y_test, p_test, w) - metricas.mae(y_test, constante, w)
            ),
        }
        compuertas = [
            _compuerta("MAE_NO_PEOR_QUE_BASELINE", prueba["mae"], "<=", baseline["mae"]),
            _compuerta(
                "MONTOS_VALIDOS",
                bool(np.all(np.isfinite(p_test)) and np.all(p_test >= 0)),
                "==",
                True,
            ),
        ]
    compuertas.append(_compuerta("AJUSTE_CONVERGIO", a.ajuste.convergio, "==", True))
    intervalos = metricas.bootstrap(
        cuentas_test,
        estadisticos,
        repeticiones=politica.repeticiones_bootstrap,
        semilla=politica.semilla,
        nivel=politica.nivel_bootstrap,
    )
    return _Componente(
        target=a.target,
        algoritmo=a.algoritmo,
        hiperparametros=a.hiperparametros,
        artefacto=artefacto,
        hash_artefacto=artefacto_.hash_artefacto(artefacto),
        calibracion=artefacto["calibracion"]["metodo"],
        metricas_validation=validacion,
        metricas_test=prueba,
        baseline_test=baseline,
        bootstrap_test={
            "repeticiones": politica.repeticiones_bootstrap,
            "nivel": politica.nivel_bootstrap,
            "semilla": politica.semilla,
            "unidad": "cuenta",
            "cuentas": int(len(np.unique(cuentas_test))),
            "intervalos": {
                nombre: {"estimado": i.estimado, "inferior": i.inferior, "superior": i.superior}
                for nombre, i in intervalos.items()
            },
        },
        explicacion_global=_explicacion_global(artefacto),
        compuertas=compuertas,
    )


def _compuerta(nombre: str, valor, operador: str, umbral) -> dict:
    if valor is None:
        ok = False
    elif operador == "<=":
        ok = valor <= umbral
    elif operador == ">":
        ok = valor > umbral
    else:
        ok = valor == umbral
    return {"compuerta": nombre, "valor": valor, "operador": operador, "umbral": umbral, "ok": ok}


def _explicacion_global(artefacto: dict) -> list[dict]:
    """Cada columna del modelo con su coeficiente en la escala estandarizada, de mayor a menor
    magnitud: una asociacion del modelo con su target, no una importancia causal."""
    filas = [
        {
            "columna": columna,
            "feature": columna.split("=")[0].removesuffix(features_.SUFIJO_NULO),
            "coeficiente": coeficiente,
            "direccion": "+" if coeficiente > 0 else ("-" if coeficiente < 0 else "0"),
            "magnitud": abs(coeficiente),
        }
        for columna, coeficiente in zip(
            artefacto["columnas"], artefacto["coeficientes"], strict=True
        )
    ]
    return sorted(filas, key=lambda f: (-f["magnitud"], f["columna"]))


# --- el walk-forward ------------------------------------------------------------------------------


REGLA_WALK_FORWARD = (
    "Las anclas anteriores a TEST (hasta la ultima de VALIDATION, en orden) se recorren hacia "
    "atras en bloques del tamano de VALIDATION: el ultimo fold es el split principal; cada uno "
    "de los anteriores valida con el bloque previo y entrena con las anclas cuya ventana de label "
    "mas larga termina antes de ese bloque (su propia purga). Cada fold aprende su propio "
    "preprocesamiento de sus anclas de entrenamiento, ajusta cada valor de la rejilla y lo mide en "
    "su validacion contra el baseline de su entrenamiento, sin calibrar. TEST no participa en "
    "ningun fold."
)


def _walk_forward(
    datos: dataset_.Dataset,
    ajustados: list[_Ajustado],
    politica: PoliticaEntrenamiento,
    componentes: list[_Componente],
) -> dict:
    """El backtest temporal: el procedimiento repetido en folds anteriores al split principal, con
    la regla de REGLA_WALK_FORWARD."""
    validation = datos.de(Split.VALIDATION)
    fin = max(b.ancla.feature_as_of for b in validation)
    previas = [b for b in datos.bloques if b.split != Split.TEST and b.ancla.feature_as_of <= fin]
    v, m, total = len(validation), len(previas), politica.folds_walk_forward
    horizonte = timedelta(days=politica.horizonte_purga_dias)
    folds = []
    for f in range(1, total):
        hasta = m - (total - f) * v
        desde = hasta - v
        if desde < 0:
            folds.append({"fold": f, "estado": "NO_DISPONIBLE", "motivo": "No hay anclas."})
            continue
        val = previas[desde:hasta]
        primera = val[0].ancla.feature_as_of
        ent = [b for b in previas[:desde] if b.ancla.feature_as_of + horizonte < primera]
        folds.append(_fold(f, ent, val, datos.diccionario, ajustados, politica))
    principal = {
        "fold": total,
        "estado": "PRINCIPAL",
        "entrenamiento": _periodo(datos.de(Split.TRAIN)),
        "validacion": _periodo(validation),
        "targets": {
            a.target.value: {
                "rejilla": a.rejilla,
                "baseline": a.linea_base,
                "elegido": a.hiperparametros,
            }
            for a in ajustados
        },
    }
    return {
        "por_target": _por_target([*folds, principal], ajustados, componentes),
        "folds": [*folds, principal],
        "holdout": {
            "periodo": _periodo(datos.de(Split.TEST)),
            "targets": {
                c.target.value: {
                    "metricas": _resumen(c.metricas_test),
                    "baseline": _resumen(c.baseline_test),
                }
                for c in componentes
            },
        },
        "regla": REGLA_WALK_FORWARD,
    }


METRICAS_DEL_BACKTEST = {
    artefacto_.LOGISTICA: ("log_loss", "brier", "roc_auc", "pr_auc"),
    artefacto_.TWEEDIE: ("mae", "rmse"),
}


def _por_target(folds: list[dict], ajustados: list[_Ajustado], componentes: list[_Componente]):
    """Por target: cada fold con el hiperparametro elegido en el split principal contra su
    baseline, el agregado de los folds (media, minimo y maximo: la degradacion se ve) y el holdout
    de TEST, aparte."""
    resultado = {}
    for a in ajustados:
        clave = "c" if a.algoritmo == artefacto_.LOGISTICA else "alpha"
        nombres = METRICAS_DEL_BACKTEST[a.algoritmo]
        filas = []
        for fold in folds:
            del_target = fold.get("targets", {}).get(a.target.value)
            if not del_target or del_target.get("estado", "EVALUADO") != "EVALUADO":
                filas.append({"fold": fold["fold"], "estado": "NO_DISPONIBLE"})
                continue
            fila = next(r for r in del_target["rejilla"] if r[clave] == a.hiperparametros[clave])
            filas.append(
                {
                    "fold": fold["fold"],
                    "estado": "PRINCIPAL" if fold["estado"] == "PRINCIPAL" else "EVALUADO",
                    "validacion": fold["validacion"],
                    **{n: fila.get(n) for n in nombres},
                    "baseline": del_target["baseline"],
                }
            )
        evaluadas = [f for f in filas if f["estado"] != "NO_DISPONIBLE"]
        agregado = {}
        for n in nombres:
            valores = [f[n] for f in evaluadas if f.get(n) is not None]
            if valores:
                agregado[n] = {
                    "media": float(np.mean(valores)),
                    "minimo": float(np.min(valores)),
                    "maximo": float(np.max(valores)),
                }
        componente = next(c for c in componentes if c.target == a.target)
        resultado[a.target.value] = {
            "hiperparametro": {clave: a.hiperparametros[clave]},
            "folds": filas,
            "agregado_folds": agregado,
            "holdout_test": {
                **{n: componente.metricas_test.get(n) for n in nombres},
                "baseline": {n: componente.baseline_test.get(n) for n in nombres},
            },
        }
    return resultado


def _periodo(bloques: list[dataset_.BloqueAncla]) -> dict:
    fechas = [b.ancla.feature_as_of for b in bloques]
    return {
        "desde": min(fechas).isoformat() if fechas else None,
        "hasta": max(fechas).isoformat() if fechas else None,
        "anclas": len(fechas),
        "filas": sum(b.filas for b in bloques),
    }


def _resumen(m: dict) -> dict:
    """Las metricas escalares, sin tablas."""
    return {k: v for k, v in m.items() if not isinstance(v, list | dict)}


def _fold(
    numero: int,
    entrenamiento: list[dataset_.BloqueAncla],
    validacion: list[dataset_.BloqueAncla],
    diccionario: dataset_.Diccionario,
    ajustados: list[_Ajustado],
    politica: PoliticaEntrenamiento,
) -> dict:
    resultado = {
        "fold": numero,
        "entrenamiento": _periodo(entrenamiento),
        "validacion": _periodo(validacion),
    }
    if not entrenamiento:
        return {**resultado, "estado": "NO_DISPONIBLE", "motivo": "Sin anclas de entrenamiento."}
    pre = _preprocesamiento(entrenamiento, diccionario, politica.cifras_significativas)
    diseno = _Diseno(pre, diccionario, politica.filas_por_bloque)
    por_target = {}
    for a in ajustados:
        t = targets_.POR_TARGET[a.target]
        ent, val = _maduras(entrenamiento, a.target), _maduras(validacion, a.target)
        y_ent, y_val = _y(ent, a.target), _y(val, a.target)
        if (
            not len(y_ent)
            or not len(y_val)
            or (t.tipo == TipoTarget.BINARIO and y_ent.min() == y_ent.max())
        ):
            por_target[a.target.value] = {"estado": "NO_DISPONIBLE"}
            continue
        base = float(np.mean(y_ent))
        filas = []
        if t.tipo == TipoTarget.BINARIO:
            constante = np.full(len(y_val), base)
            for c in politica.grid_c:
                ajuste = glm.ajustar_logistica(
                    diseno.bloques(ent, a.target),
                    diseno.columnas,
                    c=c,
                    libres=pre.libres,
                    tolerancia=politica.tolerancia_newton,
                    iteraciones_maximas=politica.iteraciones_maximas,
                )
                p = glm.sigmoide(diseno.eta(val, ajuste))
                filas.append(
                    {
                        "c": c,
                        "log_loss": metricas.log_loss(y_val, p),
                        "brier": metricas.brier(y_val, p),
                        "roc_auc": metricas.roc_auc(y_val, p),
                        "pr_auc": metricas.pr_auc(y_val, p),
                    }
                )
            linea_base = {
                "prevalencia_train": base,
                "prevalencia_validation": float(np.mean(y_val)),
                "log_loss": metricas.log_loss(y_val, constante),
                "brier": metricas.brier(y_val, constante),
            }
            mejor = min(f_["log_loss"] for f_ in filas)
            elegido = min(
                f_["c"]
                for f_ in filas
                if f_["log_loss"] <= mejor + politica.tolerancia_empate_log_loss
            )
            clave = "c"
        else:
            constante = np.full(len(y_val), base)
            for alpha in politica.grid_alpha:
                ajuste = glm.ajustar_tweedie(
                    diseno.bloques(ent, a.target),
                    diseno.columnas,
                    alpha=alpha,
                    potencia=politica.potencia_tweedie,
                    libres=pre.libres,
                    tolerancia=politica.tolerancia_newton,
                    iteraciones_maximas=politica.iteraciones_maximas,
                )
                mu = np.exp(diseno.eta(val, ajuste))
                filas.append(
                    {
                        "alpha": alpha,
                        "mae": metricas.mae(y_val, mu),
                        "rmse": float(np.sqrt(np.mean((y_val - mu) ** 2))),
                    }
                )
            linea_base = {
                "media_train": base,
                "media_validation": float(np.mean(y_val)),
                "mae": metricas.mae(y_val, constante),
            }
            mejor = min(f_["mae"] for f_ in filas)
            elegido = max(
                f_["alpha"]
                for f_ in filas
                if f_["mae"] <= mejor * (1 + politica.tolerancia_empate_mae)
            )
            clave = "alpha"
        por_target[a.target.value] = {
            "estado": "EVALUADO",
            "filas_entrenamiento": int(len(y_ent)),
            "filas_validacion": int(len(y_val)),
            "rejilla": filas,
            "baseline": linea_base,
            "elegido_en_el_fold": {clave: elegido},
            "elegido_en_el_split_principal": {clave: a.hiperparametros[clave]},
        }
    return {**resultado, "estado": "EVALUADO", "targets": por_target}


# --- la referencia del drift ----------------------------------------------------------------------


def _bordes(valores: np.ndarray, politica: PoliticaEntrenamiento) -> list[float]:
    cifras = politica.cifras_significativas
    return sorted({pre_.redondear(b, cifras) for b in metricas.bordes(valores, politica.bins_psi)})


def _proporciones(valores: np.ndarray, bordes: list[float]) -> list[float]:
    return [round(float(x), 10) for x in metricas.proporciones(valores, bordes)]


def _referencia_drift(
    train: list[dataset_.BloqueAncla],
    diseno: _Diseno,
    pre: pre_.Preprocesamiento,
    componentes: list[_Componente],
    textos: np.ndarray,
    politica: PoliticaEntrenamiento,
) -> dict:
    """Las distribuciones de TRAIN contra las que un scoring mide su drift: por numerica, los
    bordes de sus deciles (del valor transformado, sin NULL), sus proporciones y su fraccion de
    NULL; por categorica, la proporcion de cada valor de su vocabulario; por salida, los deciles de
    lo que se publicaria en TRAIN. Agregadas: ninguna nombra una cuenta."""
    filas = sum(b.filas for b in train)
    features = []
    for j, (feature, num) in enumerate(zip(NUMERICAS, pre.numericas, strict=True)):
        valores = pre_.transformar(
            num.transformacion, np.concatenate([b.numericas[:, j] for b in train])
        )
        nulos = np.isnan(valores)
        conocidos = valores[~nulos]
        bordes = _bordes(conocidos, politica)
        features.append(
            {
                "nombre": feature.nombre,
                "clase": "NUMERICA",
                "transformacion": num.transformacion,
                "bordes": bordes,
                "proporciones": _proporciones(conocidos, bordes),
                "faltantes": round(float(np.mean(nulos)), 6),
            }
        )
    for k, cat in enumerate(pre.categoricas):
        cuantos = np.zeros(len(cat.vocabulario), dtype=np.int64)
        for b in train:
            cuantos += np.bincount(diseno.indices(b)[:, k], minlength=len(cat.vocabulario))
        features.append(
            {
                "nombre": cat.nombre,
                "clase": "CATEGORICA",
                "vocabulario": list(cat.vocabulario),
                "proporciones": [round(float(c / filas), 10) for c in cuantos],
            }
        )
    salidas = []
    for c in componentes:
        definicion = targets_.POR_TARGET[c.target]
        valores = publicada(
            _predecir(artefacto_.Runtime(c.artefacto), train, textos, politica.filas_por_bloque),
            c.algoritmo,
            politica,
        )
        bordes = _bordes(valores, politica)
        salidas.append(
            {
                "nombre": definicion.salida,
                "clase": "PROBABILIDAD" if c.algoritmo == artefacto_.LOGISTICA else "MONTO",
                "bordes": bordes,
                "proporciones": _proporciones(valores, bordes),
                "media": round(float(np.mean(valores)), 8),
            }
        )
    return {
        "filas_train": filas,
        "bins": politica.bins_psi,
        "epsilon": politica.epsilon_psi,
        "features": features,
        "salidas": salidas,
    }


# --- la ficha -------------------------------------------------------------------------------------


METRICAS_DE_LA_FICHA = {
    artefacto_.LOGISTICA: ("n", "prevalencia", "roc_auc", "pr_auc", "log_loss", "brier", "ece"),
    artefacto_.TWEEDIE: ("n", "mae", "rmse", "suma_real", "suma_predicha", "razon_sumas"),
}
"""Lo que la ficha resume de las metricas de TEST de cada componente; el resto esta en el
componente."""


def _ficha(
    corrida: _Corrida,
    datos: dataset_.Dataset,
    componentes: list[_Componente],
    periodos: dict,
    politica: PoliticaEntrenamiento,
) -> dict:
    """La ficha del modelo (model card): para que sirve y para que no, con que se entreno, como se
    evaluo y sus limitaciones."""
    return {
        "ficha": VERSION_MODELO,
        "aviso": AVISO,
        "uso_previsto": (
            "Ordenar las cuentas de la cartera con la que se entreno por la probabilidad de "
            "resultados observados en los dias siguientes al corte (recuperacion positiva y "
            "contacto, creacion de una promesa) y estimar su recuperacion positiva esperada a 30 "
            "dias, como informacion descriptiva en paralelo a decision/v2, en sombra."
        ),
        "uso_no_previsto": [
            "Estimar el efecto de una accion: una probabilidad alta no dice que gestionar la "
            "cuenta cambie su resultado.",
            "Decidir sobre una persona o reemplazar decision/v2, campo/v2 o ruteo/v2.",
            "Usarse en otra cartera, otra zona o un periodo con otra operacion sin medir su drift.",
            "Leer recuperacion_positiva_esperada_30d como la recuperacion neta esperada: los netos "
            "negativos se truncaron a cero por definicion.",
        ],
        "cartera": {"despacho_id": corrida.despacho_id, "cartera_id": corrida.cartera_id},
        "zona_horaria": corrida.zona,
        "periodos": {k: v.isoformat() for k, v in periodos.items()},
        "anclas_por_split": {
            split: sum(1 for b in datos.bloques if b.split == split) for split in Split.TODOS
        },
        "filas_por_split": datos.filas_por_split,
        "cuentas_distintas": datos.cuentas,
        "conteos_por_target": datos.conteos,
        "dataset_hash": datos.hash,
        "hash_manifiesto": corrida.hash_manifiesto,
        "features": {
            "version": features_.VERSION_FEATURES,
            "hash": features_.hash_features(),
            "nombres": [f.nombre for f in features_.FEATURES],
        },
        "targets": {
            "version": targets_.VERSION_TARGETS,
            "hash": targets_.hash_targets(),
            "targets": {
                t.target.value: {"horizonte_dias": t.horizonte_dias, "salida": t.salida}
                for t in targets_.TARGETS
            },
            "derivado": "riesgo_sin_recuperacion_30d = 1 - p_recuperacion_positiva_30d, sin "
            "modelo propio.",
        },
        "componentes": {
            c.target.value: {
                "algoritmo": c.algoritmo,
                "hiperparametros": c.hiperparametros,
                "calibracion": c.calibracion,
                "estado": "VALIDADO" if c.validado else "RECHAZADO",
                "metricas_test": {
                    k: c.metricas_test.get(k)
                    for k in METRICAS_DE_LA_FICHA[c.algoritmo]
                    if k in c.metricas_test
                },
                "baseline_test": {
                    k: c.baseline_test.get(k)
                    for k in METRICAS_DE_LA_FICHA[c.algoritmo]
                    if k in c.baseline_test
                },
            }
            for c in componentes
        },
        "compuertas": {
            "clasificacion": politica.reglas["compuertas_clasificacion"],
            "monto": politica.reglas["compuertas_monto"],
            "convergencia": "El ajuste elegido de cada componente convergio.",
        },
        "referencia_drift": (
            f"Las distribuciones de TRAIN en {politica.bins_psi} bins por feature y por salida, y "
            "la fraccion de NULL de cada feature, en referencia_drift: un scoring mide su PSI "
            f"(epsilon {politica.epsilon_psi}) contra ellas, con severidad MODERADO desde "
            f"{politica.psi_moderado} y ALTO desde {politica.psi_alto}. Son heuristicas "
            "operacionales: un PSI alto no prueba un deterioro, y el drift no cambia el modelo."
        ),
        "privacidad_y_etica": [
            "Ninguna feature es un identificador, un dato personal (nombre, direccion, telefono, "
            "correo, CURP, RFC), un atributo sensible (sexo, edad, salud), una coordenada, una "
            "geografia mas fina que la entidad ni una salida de decision, campo, ruteo o "
            "analytics: predictive-features/public-v1 declara lo que ninguna puede usar.",
            "Las predicciones se publican por cuenta_id, sin CLIENTE_UNICO ni datos personales; el "
            "drift y la referencia son agregados, sin una cuenta.",
            "El modelo no decide sobre una persona: corre en sombra y no cambia decision/v2, "
            "campo/v2 ni ruteo/v2.",
        ],
        "limitaciones": [
            "Las asociaciones son las de la operacion historica de esta cartera: si la operacion "
            "cambia (quien se gestiona, como y cuando), cambian.",
            "Una cuenta sin historia conocida (pocos cortes, ventanas sin cobertura) se predice "
            "con imputaciones de TRAIN; su prediccion lo dice en sus motivos.",
            "Las metricas de TEST son de un solo periodo; el walk-forward muestra su variacion.",
            "El generador sintetico fija la relacion entre las features y los resultados: las "
            "metricas sobre datos sinteticos no anticipan las de una cartera real.",
        ],
        "entorno": medicion.entorno(),
    }


# --- publicar -------------------------------------------------------------------------------------


def hash_modelo(componentes: dict[str, str]) -> str:
    """El SHA-256 de los hashes de los artefactos, por target."""
    return hashlib.sha256(canonico(dict(sorted(componentes.items())))).hexdigest()


def _publicar(corrida: _Corrida, r: _Resultado, tiempos: medicion.Tiempos) -> None:
    """La version y sus componentes, y la ejecucion EXITOSA, en una transaccion: todo o nada, y solo
    si este worker sigue siendo el dueno de su trabajo."""
    validados = all(c.validado for c in r.componentes)
    with sesion() as s:
        ejecucion = s.exec(
            select(EjecucionEntrenamientoPredictivo)
            .where(EjecucionEntrenamientoPredictivo.id == corrida.ejecucion_id)
            .with_for_update()
        ).one()
        if ejecucion.estado != EstadoEntrenamientoPredictivo.EN_PROCESO:
            log.warning(
                "entrenamiento %s ya termino %s; no se publica",
                corrida.training_run_id,
                ejecucion.estado,
            )
            return
        try:
            with s.begin_nested():
                _punto_de_falla(ANTES_PUBLICAR_MODELO)
                version = _registrar(s, corrida, r)
                version.estado = (
                    EstadoModeloPredictivo.VALIDADA
                    if validados
                    else EstadoModeloPredictivo.RECHAZADA
                )
                s.add(version)
                s.flush()
                if validados:
                    version.estado = EstadoModeloPredictivo.PUBLICADA
                    version.publicada_en = ahora()
                    s.add(version)
                    s.flush()
                _cerrar(s, ejecucion, r, version, validados, tiempos)
        except IntegrityError as exc:
            if restriccion(exc) != INDICE_EXITOSA:
                raise
            s.rollback()
            _fallar(
                corrida.ejecucion_id,
                corrida.training_run_id,
                ResultadoEntrenamientoPredictivo.YA_ENTRENADO,
                "Otra ejecucion publico el mismo manifiesto mientras esta entrenaba; no se publica "
                "dos veces.",
            )
            with sesion() as s2:
                previa = _de_firma(s2, corrida.firma, EstadoEntrenamientoPredictivo.EXITOSA)
                s2.expunge_all()
            raise EntrenamientoYaPublicado(
                presente(previa, "la ejecucion que publico primero")
            ) from exc
        except Exception:
            s.rollback()
            raise
        cola.confirmar_dueno(s)
        s.commit()
    log.info(
        "entrenamiento %s: version %s",
        corrida.training_run_id,
        "PUBLICADA" if validados else "RECHAZADA",
    )


def _registrar(s: Session, corrida: _Corrida, r: _Resultado) -> VersionModeloPredictivo:
    hashes = {c.target.value: c.hash_artefacto for c in r.componentes}
    version = VersionModeloPredictivo(
        version_modelo=VERSION_MODELO,
        ejecucion_entrenamiento_predictivo_id=corrida.ejecucion_id,
        version_features=features_.VERSION_FEATURES,
        hash_features=features_.hash_features(),
        version_targets=targets_.VERSION_TARGETS,
        hash_targets=targets_.hash_targets(),
        version_politica=corrida.politica.version,
        hash_politica=hash_politica(corrida.politica),
        dataset_hash=r.dataset.hash,
        hash_modelo=hash_modelo(hashes),
        estado=EstadoModeloPredictivo.CANDIDATA,
        referencia_drift=r.referencia_drift,
        metadatos={**r.metadatos, "hash_modelo": hash_modelo(hashes), "hashes_artefactos": hashes},
        **r.periodos,
    )
    s.add(version)
    s.flush()
    for c in r.componentes:
        s.add(
            ComponenteModeloPredictivo(
                version_modelo_predictivo_id=id_de(version),
                target=c.target.value,
                algoritmo=c.algoritmo,
                hiperparametros=c.hiperparametros,
                artefacto=c.artefacto,
                hash_artefacto=c.hash_artefacto,
                calibracion=c.calibracion,
                metricas_validation=_json(c.metricas_validation),
                metricas_test=_json(c.metricas_test),
                baseline_test=_json(c.baseline_test),
                bootstrap_test=_json(c.bootstrap_test),
                explicacion_global=_json(c.explicacion_global),
                estado="VALIDADO" if c.validado else "RECHAZADO",
                compuertas=_json(c.compuertas),
            )
        )
    s.flush()
    return version


@overload
def _json(valor: dict) -> dict: ...
@overload
def _json(valor: list) -> list: ...
@overload
def _json(valor: object) -> object: ...
def _json(valor: object) -> object:
    """Un valor de las metricas como JSON: floats de NumPy a float, sin NaN ni infinitos."""
    if isinstance(valor, dict):
        return {str(k): _json(v) for k, v in valor.items()}
    if isinstance(valor, list | tuple):
        return [_json(v) for v in valor]
    if isinstance(valor, np.generic):
        valor = valor.item()
    if isinstance(valor, float) and not np.isfinite(valor):
        return None
    return valor


def _cerrar(
    s: Session,
    ejecucion: EjecucionEntrenamientoPredictivo,
    r: _Resultado,
    version: VersionModeloPredictivo,
    validados: bool,
    tiempos: medicion.Tiempos,
) -> None:
    filas = r.dataset.filas_por_split
    ejecucion.estado = EstadoEntrenamientoPredictivo.EXITOSA
    ejecucion.resultado = (
        ResultadoEntrenamientoPredictivo.MODELO_PUBLICADO
        if validados
        else ResultadoEntrenamientoPredictivo.MODELO_RECHAZADO
    ).value
    ejecucion.dataset_hash = r.dataset.hash
    ejecucion.ejemplos_train = filas.get(Split.TRAIN, 0)
    ejecucion.ejemplos_validation = filas.get(Split.VALIDATION, 0)
    ejecucion.ejemplos_test = filas.get(Split.TEST, 0)
    ejecucion.ejemplos_purge = filas.get(Split.PURGE, 0)
    ejecucion.ejemplos_total = sum(filas.values())
    ejecucion.ejemplos_censurados = r.dataset.censurados
    ejecucion.conteos = _json(r.dataset.conteos)
    ejecucion.backtest = _json(r.backtest)
    ejecucion.tiempos = {
        **tiempos.resumen(),
        "dataset_fases_segundos": r.dataset.fases,
        "matriz": r.matriz,
        "anclas_segundos": {
            b.ancla.feature_as_of.isoformat(): round(b.segundos, 3) for b in r.dataset.bloques
        },
    }
    rechazados = [c.target.value for c in r.componentes if not c.validado]
    ejecucion.detalle = (
        f"Se entreno {VERSION_MODELO} con {ejecucion.anclas} anclas y "
        f"{ejecucion.ejemplos_total:,} filas cuenta-ancla ({ejecucion.ejemplos_censurados:,} con "
        "algun label censurado): "
        + (
            f"la version {version.modelo_version_id} quedo PUBLICADA, con sus cuatro componentes "
            "VALIDADO en TEST."
            if validados
            else f"la version {version.modelo_version_id} quedo RECHAZADA: "
            f"{', '.join(rechazados)} no paso sus compuertas en TEST. No se publico nada."
        )
        + f" {AVISO}"
    )
    ejecucion.terminada_en = ahora()
    s.add(ejecucion)
    s.flush()


def _fallar(
    ejecucion_id: int,
    etiqueta: UUID,
    resultado: ResultadoEntrenamientoPredictivo,
    motivo: str,
) -> None:
    """FALLIDA, sin version, solo si quien la ejecuta sigue siendo el dueno de su trabajo; si no,
    TrabajoAjeno sin cambiar nada."""
    with sesion() as s:
        try:
            cola.confirmar_dueno(s)
        except cola.TrabajoAjeno:
            s.rollback()
            log.warning(
                "entrenamiento %s: su trabajo ya no es de este worker: %s", etiqueta, motivo
            )
            raise
        s.execute(
            update(EjecucionEntrenamientoPredictivo)
            .where(
                col(EjecucionEntrenamientoPredictivo.id) == ejecucion_id,
                col(EjecucionEntrenamientoPredictivo.estado)
                == EstadoEntrenamientoPredictivo.EN_PROCESO,
            )
            .values(
                estado=EstadoEntrenamientoPredictivo.FALLIDA,
                resultado=resultado.value,
                detalle=motivo,
                terminada_en=ahora(),
            )
            .execution_options(synchronize_session=False)
        )
        s.commit()
    log.warning("entrenamiento %s: FALLIDA (%s)", etiqueta, resultado.value)


# --- consultar ------------------------------------------------------------------------------------


def interpretaciones_de(s: Session, ejecucion_id: int) -> list[EjecucionMotorPagos]:
    """Las interpretaciones de pagos declaradas de un entrenamiento, por mes."""
    return list(
        s.exec(
            select(EjecucionMotorPagos)
            .join(
                InterpretacionEntrenamientoPredictivo,
                col(InterpretacionEntrenamientoPredictivo.ejecucion_motor_pagos_id)
                == EjecucionMotorPagos.id,
            )
            .where(
                InterpretacionEntrenamientoPredictivo.ejecucion_entrenamiento_predictivo_id
                == ejecucion_id
            )
            .order_by(col(InterpretacionEntrenamientoPredictivo.periodo_desde))
        ).all()
    )
