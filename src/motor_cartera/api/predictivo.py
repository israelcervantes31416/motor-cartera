"""predictive/model-v1 por HTTP: las definiciones de las features y de los targets, pedir y leer
entrenamientos, leer las versiones del modelo, pedir y leer scorings, sus predicciones, su drift y
la explicacion de una prediccion, y las predicciones de una cuenta.

La API no entrena ni puntua: cada POST resuelve y comprueba lo declarado y deja la ejecucion
EN_PROCESO con su trabajo en la cola durable; la ejecuta un worker. Un scoring declara su version
del modelo (PUBLICADA): nunca "la ultima". Nada de esto cambia decision/v2, campo/v2 ni ruteo/v2.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from sqlmodel import Session, select

from motor_cartera.api.dependencias import Sesion, SesionDeLectura
from motor_cartera.api.errores import ErrorDeApi, errores
from motor_cartera.api.esquemas import Paginacion
from motor_cartera.api.esquemas_predictivo import (
    ComponentePredictivoRespuesta,
    DriftPredictivoFila,
    DriftPredictivoRespuesta,
    EntrenamientoPredictivoEntrada,
    EntrenamientoPredictivoRespuesta,
    ExplicacionPredictivaRespuesta,
    FeaturesPredictivasRespuesta,
    ModeloPredictivoRespuesta,
    ModeloPredictivoResumen,
    PaginaEntrenamientosPredictivos,
    PaginaModelosPredictivos,
    PaginaPrediccionesDeCuenta,
    PaginaPrediccionesPredictivas,
    PaginaScoringsPredictivos,
    ParametrosEntrenamientosPredictivos,
    ParametrosModelosPredictivos,
    ParametrosPrediccionesPredictivas,
    ParametrosScoringsPredictivos,
    PrediccionCuentaRespuesta,
    PrediccionDeCuentaRespuesta,
    ScoringPredictivoEntrada,
    ScoringPredictivoRespuesta,
    TargetsPredictivosRespuesta,
)
from motor_cartera.api.operacional import SIN_CLAVE
from motor_cartera.config import Config
from motor_cartera.db.modelos import (
    CorteCanonico,
    CuentaCanonica,
    EstadoModeloPredictivo,
    EstadoScoringPredictivo,
    VersionModeloPredictivo,
    id_de,
)
from motor_cartera.predictivo import consultas, entrenamiento, explicacion, manifiesto, scoring
from motor_cartera.predictivo import features as features_
from motor_cartera.predictivo import targets as targets_
from motor_cartera.predictivo.consultas import FiltrosPredicciones

router = APIRouter(tags=["predictivo"])

UUID_INVALIDO = (422, "ENTRADA_INVALIDA", "No es un UUID.")
INVALIDA = (422, "ENTRADA_INVALIDA", "Un filtro no es valido, o la paginacion no lo es.")
ENTRENAMIENTO_NO_EXISTE = (
    404,
    "ENTRENAMIENTO_NO_ENCONTRADO",
    "No existe un entrenamiento con ese training_run_id.",
)
MODELO_NO_EXISTE = (404, "MODELO_NO_ENCONTRADO", "No existe una version con ese id.")
SCORING_NO_EXISTE = (404, "SCORING_NO_ENCONTRADO", "No existe un scoring con ese scoring_run_id.")
SCORING_NO_PUBLICADO = (
    409,
    "SCORING_NO_PUBLICADO",
    "El scoring no esta EXITOSA: no publico predicciones ni drift.",
)
ERRORES_DEL_MANIFIESTO = (
    (404, "CORTE_NO_ENCONTRADO", "No existe uno de los cortes declarados."),
    (404, "INTERPRETACION_NO_ENCONTRADA", "No existe una de las interpretaciones declaradas."),
    (409, "ENTRENAMIENTO_EN_PROCESO", "Ese manifiesto ya se esta entrenando."),
    (409, "ENTRENAMIENTO_YA_PUBLICADO", "Ese manifiesto (la misma firma) ya se entreno."),
    (422, "MANIFIESTO_INVALIDO", "El manifiesto no tiene la forma esperada."),
    (422, "POLITICA_NO_SOPORTADA", "Este servicio no entrena con esa politica."),
    (422, "ZONA_HORARIA_INVALIDA", "La zona no es una zona IANA que conozca la base."),
    (422, "ANCLAS_INSUFICIENTES", "Menos de tres anclas."),
    (422, "ANCLA_REPETIDA", "Un corte esta declarado dos veces."),
    (422, "CARTERA_INCONSISTENTE", "Las anclas son de mas de una cartera."),
    (422, "AS_OF_DISTINTO_DEL_CORTE", "feature_as_of no es la fecha del corte."),
    (422, "KNOWN_AT_SIN_ZONA", "Un instante no lleva su zona horaria."),
    (422, "KNOWN_AT_FUTURO", "label_known_at es posterior al reloj de la base."),
    (422, "RELOJES_INVERTIDOS", "feature_known_at es posterior a label_known_at."),
    (422, "CORTE_NO_CONOCIDO", "El corte se materializo despues de feature_known_at."),
    (422, "COBERTURA_NO_CONOCIDA", "Una cobertura declarada es posterior a label_known_at."),
    (422, "SPLIT_INVALIDO", "Un split no es TRAIN, VALIDATION, TEST ni PURGE, o falta uno."),
    (422, "SPLIT_DESORDENADO", "TRAIN, VALIDATION y TEST no van en ese orden en el tiempo."),
    (422, "PURGA_INSUFICIENTE", "La ventana de un label llega al bloque siguiente."),
    (422, "SPLIT_SIN_MADURAS", "Un bloque no tiene anclas maduras de un target."),
    (422, "INTERPRETACION_NO_EXITOSA", "Una interpretacion declarada no termino EXITOSA."),
    (422, "INTERPRETACION_INCONSISTENTE", "Una interpretacion es de otra version o cartera."),
    (422, "VENTANA_DUPLICADA", "Un mes tiene dos interpretaciones declaradas."),
    (422, "INTERPRETACION_NO_CONOCIDA", "Una interpretacion termino despues de label_known_at."),
    (422, "INTERPRETACION_NO_VIGENTE", "Una interpretacion no es la vigente a label_known_at."),
    (422, "INTERPRETACION_FALTANTE", "Un mes de una ventana madura no tiene interpretacion."),
    (422, "INTERPRETACION_INCOMPLETA", "Una interpretacion no clasifico todos los pagos."),
    (422, "ENTRADA_INVALIDA", "Falta un campo, o no tiene su forma."),
)
ERRORES_DEL_SCORING = (
    (404, "MODELO_NO_ENCONTRADO", "No existe esa version del modelo."),
    (404, "CORTE_NO_ENCONTRADO", "No existe ese corte canonico."),
    (409, "MODELO_NO_PUBLICADO", "La version no esta PUBLICADA."),
    (409, "SCORING_EN_PROCESO", "Ese mismo scoring ya se esta calculando."),
    (409, "SCORING_YA_PUBLICADO", "Ese mismo scoring (la misma firma) ya se publico."),
    (422, "MODELO_NO_SOPORTADO", "La version es de otras features o de otra version del modelo."),
    (422, "KNOWN_AT_OBLIGATORIO", "Un BACKTEST declara su known_at."),
    (422, "KNOWN_AT_SIN_ZONA", "known_at no lleva su zona horaria."),
    (422, "KNOWN_AT_FUTURO", "known_at es posterior al reloj de la base."),
    (422, "CORTE_NO_CONOCIDO", "El corte se materializo despues de known_at."),
    (422, "ENTRADA_INVALIDA", "Falta un campo, o no tiene su forma."),
)


# --- las definiciones -----------------------------------------------------------------------------


@router.get(
    "/predictivo/features",
    response_model=FeaturesPredictivasRespuesta,
    summary="predictive-features/public-v1: cada feature con su fuente, su ventana y su nulo",
    responses=errores(*SIN_CLAVE),
)
def features() -> FeaturesPredictivasRespuesta:
    """La lista explicita de features (ninguna se elige sola), con su hash: su tipo, su fuente,
    su transformacion, su ventana y que significa su NULL; los nombres y las tablas que ninguna
    puede usar (datos personales, coordenadas, identificadores, decisiones)."""
    return FeaturesPredictivasRespuesta(**features_.definicion(), hash=features_.hash_features())


@router.get(
    "/predictivo/targets",
    response_model=TargetsPredictivosRespuesta,
    summary="predictive-targets/public-v1: que se predice, en que ventana y cuando esta maduro",
    responses=errores(*SIN_CLAVE),
)
def targets() -> TargetsPredictivosRespuesta:
    """Cada target con su regla formal, su ventana (T, T+H], su fuente y su madurez. Un label
    censurado nunca es un cero. El riesgo sin recuperacion no es un modelo: es 1 - p."""
    return TargetsPredictivosRespuesta(**targets_.definicion(), hash=targets_.hash_targets())


# --- los entrenamientos ---------------------------------------------------------------------------


@router.post(
    "/predictivo/entrenamientos",
    status_code=201,
    response_model=EntrenamientoPredictivoRespuesta,
    summary="Pide un entrenamiento con un manifiesto explicito de anclas y splits",
    responses={
        201: {
            "description": "El entrenamiento quedo EN_PROCESO con su trabajo en la cola durable: "
            "un worker lo entrena. `Location` apunta a el."
        },
        **errores(*SIN_CLAVE, *ERRORES_DEL_MANIFIESTO),
    },
)
def pedir_entrenamiento(
    entrada: EntrenamientoPredictivoEntrada, request: Request, response: Response, s: Sesion
) -> EntrenamientoPredictivoRespuesta:
    """Cada ancla, su corte, sus relojes, su cobertura y su split se comprueban antes de registrar
    nada: un split temporal con su purga, labels maduros en cada bloque, e interpretaciones de
    pagos vigentes y completas a label_known_at. `construir-manifiesto-predictivo` propone uno.

    **201 quiere decir que el entrenamiento se creo, no que termino.**"""
    config: Config = request.app.state.config
    try:
        declarado = manifiesto.leer(entrada.model_dump(mode="json"))
        ejecucion = entrenamiento.abrir(s, declarado, max_intentos=config.worker_max_intentos)
    except manifiesto.ManifiestoInvalido as exc:
        raise ErrorDeApi(exc.estado_http, exc.codigo, str(exc)) from exc
    except entrenamiento.EntrenamientoEnProceso as exc:
        raise ErrorDeApi(409, "ENTRENAMIENTO_EN_PROCESO", str(exc)) from exc
    except entrenamiento.EntrenamientoYaPublicado as exc:
        raise ErrorDeApi(409, "ENTRENAMIENTO_YA_PUBLICADO", str(exc)) from exc
    s.commit()
    response.headers["Location"] = f"/predictivo/entrenamientos/{ejecucion.training_run_id}"
    return entrenamiento_respuesta(consultas.obtener_entrenamiento(s, ejecucion.training_run_id))


@router.get(
    "/predictivo/entrenamientos",
    response_model=PaginaEntrenamientosPredictivos,
    summary="Los entrenamientos, el mas reciente primero",
    responses=errores(*SIN_CLAVE, INVALIDA),
)
def entrenamientos(
    parametros: Annotated[ParametrosEntrenamientosPredictivos, Query()], s: SesionDeLectura
) -> PaginaEntrenamientosPredictivos:
    """Con `estado`, solo los que estan en el (EN_PROCESO, EXITOSA o FALLIDA). Cada uno con su
    manifiesto, sus hashes y la version que dejo, si termino EXITOSA."""
    total, filas = consultas.listar_entrenamientos(
        s,
        estado=None if parametros.estado is None else parametros.estado.value,
        desplazamiento=parametros.desplazamiento,
        limite=parametros.por_pagina,
    )
    return PaginaEntrenamientosPredictivos(
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[entrenamiento_respuesta(v) for v in filas],
    )


@router.get(
    "/predictivo/entrenamientos/{training_run_id}",
    response_model=EntrenamientoPredictivoRespuesta,
    summary="Un entrenamiento: su manifiesto, sus hashes, sus conteos, su backtest y su version",
    responses=errores(*SIN_CLAVE, ENTRENAMIENTO_NO_EXISTE, UUID_INVALIDO),
)
def obtener_entrenamiento(
    training_run_id: UUID, s: SesionDeLectura
) -> EntrenamientoPredictivoRespuesta:
    """Mientras el estado sea `EN_PROCESO`, un worker no lo ha terminado."""
    try:
        return entrenamiento_respuesta(consultas.obtener_entrenamiento(s, training_run_id))
    except consultas.NoEncontrado as exc:
        raise ErrorDeApi(
            404, "ENTRENAMIENTO_NO_ENCONTRADO", f"No existe el entrenamiento {training_run_id}."
        ) from exc


# --- los modelos ----------------------------------------------------------------------------------


@router.get(
    "/predictivo/modelos",
    response_model=PaginaModelosPredictivos,
    summary="El registro de versiones del modelo, la mas reciente primero",
    responses=errores(*SIN_CLAVE, INVALIDA),
)
def modelos(
    parametros: Annotated[ParametrosModelosPredictivos, Query()], s: SesionDeLectura
) -> PaginaModelosPredictivos:
    """Con `estado`, solo las PUBLICADA o solo las RECHAZADA. Un scoring usa una PUBLICADA que
    declara por su modelo_version_id: el registro no tiene "la ultima"."""
    total, filas = consultas.listar_modelos(
        s,
        estado=None if parametros.estado is None else parametros.estado.value,
        desplazamiento=parametros.desplazamiento,
        limite=parametros.por_pagina,
    )
    return PaginaModelosPredictivos(
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[modelo_resumen(v) for v in filas],
    )


@router.get(
    "/predictivo/modelos/{modelo_version_id}",
    response_model=ModeloPredictivoRespuesta,
    summary="Una version: su ficha, sus hashes, sus periodos y sus cuatro componentes",
    responses=errores(*SIN_CLAVE, MODELO_NO_EXISTE, UUID_INVALIDO),
)
def obtener_modelo(modelo_version_id: UUID, s: SesionDeLectura) -> ModeloPredictivoRespuesta:
    """Cada componente con su artefacto JSON y su hash, sus metricas de VALIDATION y de TEST, su
    baseline, sus intervalos bootstrap, sus compuertas y su explicacion global."""
    try:
        return modelo_respuesta(consultas.obtener_modelo(s, modelo_version_id))
    except consultas.NoEncontrado as exc:
        raise ErrorDeApi(
            404, "MODELO_NO_ENCONTRADO", f"No existe la version {modelo_version_id}."
        ) from exc


# --- los scorings ---------------------------------------------------------------------------------


@router.post(
    "/predictivo/scorings",
    status_code=201,
    response_model=ScoringPredictivoRespuesta,
    summary="Pide el scoring de un corte con una version PUBLICADA declarada",
    responses={
        201: {
            "description": "El scoring quedo EN_PROCESO con su trabajo en la cola durable: un "
            "worker lo calcula. `Location` apunta a el."
        },
        **errores(*SIN_CLAVE, *ERRORES_DEL_SCORING),
    },
)
def pedir_scoring(
    entrada: ScoringPredictivoEntrada, request: Request, response: Response, s: Sesion
) -> ScoringPredictivoRespuesta:
    """**201 quiere decir que el scoring se creo, no que termino.** El mismo modelo, corte y
    known_at son el mismo scoring: si ya se publico, `409 SCORING_YA_PUBLICADO`."""
    config: Config = request.app.state.config
    try:
        ejecucion = scoring.abrir(
            s,
            modelo_version_id=entrada.modelo_version_id,
            corte_id=entrada.corte_id,
            known_at=entrada.known_at,
            modo=entrada.modo,
            max_intentos=config.worker_max_intentos,
        )
    except scoring.ScoringInvalido as exc:
        raise ErrorDeApi(exc.estado_http, exc.codigo, str(exc)) from exc
    except scoring.ScoringEnProceso as exc:
        raise ErrorDeApi(409, "SCORING_EN_PROCESO", str(exc)) from exc
    except scoring.ScoringYaPublicado as exc:
        raise ErrorDeApi(409, "SCORING_YA_PUBLICADO", str(exc)) from exc
    s.commit()
    response.headers["Location"] = f"/predictivo/scorings/{ejecucion.scoring_run_id}"
    return scoring_respuesta(consultas.obtener_scoring(s, ejecucion.scoring_run_id))


@router.get(
    "/predictivo/scorings",
    response_model=PaginaScoringsPredictivos,
    summary="Los scorings, el mas reciente primero",
    responses=errores(
        *SIN_CLAVE,
        MODELO_NO_EXISTE,
        (404, "CORTE_NO_ENCONTRADO", "No existe ese corte canonico."),
        INVALIDA,
    ),
)
def scorings(
    parametros: Annotated[ParametrosScoringsPredictivos, Query()], s: SesionDeLectura
) -> PaginaScoringsPredictivos:
    """Por `estado`, por la version del modelo (`modelo_version_id`) y por el corte (`corte_id`):
    una version o un corte que no existen son un 404, no una lista vacia."""
    modelo = corte = None
    if parametros.modelo_version_id is not None:
        modelo = s.exec(
            select(VersionModeloPredictivo).where(
                VersionModeloPredictivo.modelo_version_id == parametros.modelo_version_id
            )
        ).one_or_none()
        if modelo is None:
            raise ErrorDeApi(
                404, "MODELO_NO_ENCONTRADO", f"No existe la version {parametros.modelo_version_id}."
            )
    if parametros.corte_id is not None:
        corte = s.exec(
            select(CorteCanonico).where(CorteCanonico.corte_id == parametros.corte_id)
        ).one_or_none()
        if corte is None:
            raise ErrorDeApi(
                404, "CORTE_NO_ENCONTRADO", f"No existe el corte canonico {parametros.corte_id}."
            )
    total, filas = consultas.listar_scorings(
        s,
        estado=None if parametros.estado is None else parametros.estado.value,
        modelo=modelo,
        corte=corte,
        desplazamiento=parametros.desplazamiento,
        limite=parametros.por_pagina,
    )
    return PaginaScoringsPredictivos(
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[scoring_respuesta(v) for v in filas],
    )


@router.get(
    "/predictivo/scorings/{scoring_run_id}",
    response_model=ScoringPredictivoRespuesta,
    summary="Un scoring: su modelo, su corte, sus relojes, sus hashes y su resumen de drift",
    responses=errores(*SIN_CLAVE, SCORING_NO_EXISTE, UUID_INVALIDO),
)
def obtener_scoring(scoring_run_id: UUID, s: SesionDeLectura) -> ScoringPredictivoRespuesta:
    """Mientras el estado sea `EN_PROCESO`, un worker no lo ha terminado. Uno `EXITOSA` trae el hash
    de sus predicciones y el de su drift, la disponibilidad de pagos y de la evaluacion de promesas
    que conocia a known_at, y su resumen de drift."""
    return scoring_respuesta(_scoring(s, scoring_run_id))


@router.get(
    "/predictivo/scorings/{scoring_run_id}/predicciones",
    response_model=PaginaPrediccionesPredictivas,
    summary="Las predicciones de un scoring, filtradas y ordenadas en la base, por pagina",
    responses=errores(*SIN_CLAVE, SCORING_NO_EXISTE, SCORING_NO_PUBLICADO, INVALIDA),
)
def predicciones(
    scoring_run_id: UUID,
    parametros: Annotated[ParametrosPrediccionesPredictivas, Query()],
    s: SesionDeLectura,
) -> PaginaPrediccionesPredictivas:
    """Un rango por salida (`p_contacto_14d_min`, `riesgo_sin_recuperacion_30d_max`...),
    `producto`, `cve_entidad` y `estado`, y un `orden` de la lista cerrada con su `sentido`. Una
    probabilidad no recomienda gestionar una cuenta: es la de un resultado observado."""
    vista = _publicado(s, scoring_run_id)
    minimos, maximos = {}, {}
    for nombre in consultas.RANGOS:
        if (valor := getattr(parametros, f"{nombre}_min")) is not None:
            minimos[nombre] = valor
        if (valor := getattr(parametros, f"{nombre}_max")) is not None:
            maximos[nombre] = valor
    total, filas = consultas.predicciones(
        s,
        vista.ejecucion,
        FiltrosPredicciones(
            minimos=minimos,
            maximos=maximos,
            producto=parametros.producto,
            cve_entidad=parametros.cve_entidad,
            estado=parametros.estado,
            orden=parametros.orden,
            sentido=parametros.sentido,
        ),
        desplazamiento=parametros.desplazamiento,
        limite=parametros.por_pagina,
    )
    return PaginaPrediccionesPredictivas(
        scoring_run_id=scoring_run_id,
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[PrediccionCuentaRespuesta(**vars(p)) for p in filas],
    )


@router.get(
    "/predictivo/scorings/{scoring_run_id}/drift",
    response_model=DriftPredictivoRespuesta,
    summary="El drift de un scoring contra TRAIN: PSI por feature y por salida, y los NULL",
    responses=errores(*SIN_CLAVE, SCORING_NO_EXISTE, SCORING_NO_PUBLICADO, UUID_INVALIDO),
)
def drift(scoring_run_id: UUID, s: SesionDeLectura) -> DriftPredictivoRespuesta:
    """Agregado: bordes y proporciones, nunca una cuenta. El drift no cambia ni reentrena el
    modelo."""
    vista = _publicado(s, scoring_run_id)
    return DriftPredictivoRespuesta(
        scoring_run_id=scoring_run_id,
        modelo_version_id=vista.modelo_version_id,
        resumen=vista.ejecucion.drift_resumen,
        filas=[
            DriftPredictivoFila(
                tipo=f.tipo,
                nombre=f.nombre,
                clase=f.clase,
                psi=f.psi,
                severidad=f.severidad,
                faltantes_train=f.faltantes_train,
                faltantes_scoring=f.faltantes_scoring,
                severidad_faltantes=f.severidad_faltantes,
                bins=f.bins,
            )
            for f in consultas.drift(s, vista.ejecucion)
        ],
    )


@router.get(
    "/predictivo/scorings/{scoring_run_id}/cuentas/{cuenta_id}/explicacion",
    response_model=ExplicacionPredictivaRespuesta,
    summary="Por que el modelo dio esa prediccion a esa cuenta: contribuciones verificadas",
    responses=errores(
        *SIN_CLAVE,
        SCORING_NO_EXISTE,
        SCORING_NO_PUBLICADO,
        (404, "CUENTA_NO_ENCONTRADA", "No existe esa cuenta."),
        (404, "SIN_PREDICCION", "El scoring no tiene una prediccion de esa cuenta."),
        (409, "LA_EXPLICACION_NO_REPRODUCE", "Las features recalculadas no son las publicadas."),
        UUID_INVALIDO,
    ),
)
def explicar(scoring_run_id: UUID, cuenta_id: UUID) -> ExplicacionPredictivaRespuesta:
    """Se calcula al pedirla y no se guarda: las features de la cuenta con los relojes del scoring
    (su feature_hash tiene que ser el publicado), y las contribuciones al predictor lineal de cada
    componente, que suman exactamente el predictor. Una contribucion es una asociacion del
    modelo, no una causa."""
    try:
        return ExplicacionPredictivaRespuesta(**explicacion.explicar(scoring_run_id, cuenta_id))
    except explicacion.ExplicacionNoDisponible as exc:
        raise ErrorDeApi(exc.estado_http, exc.codigo, str(exc)) from exc


@router.get(
    "/cuentas/{cuenta_id}/predictivo",
    response_model=PaginaPrediccionesDeCuenta,
    summary="Las predicciones publicadas de una cuenta, de su scoring mas reciente al mas antiguo",
    responses=errores(*SIN_CLAVE, (404, "CUENTA_NO_ENCONTRADA", "No existe esa cuenta."), INVALIDA),
)
def predicciones_de_cuenta(
    cuenta_id: UUID, parametros: Annotated[Paginacion, Query()], s: SesionDeLectura
) -> PaginaPrediccionesDeCuenta:
    """Cada una con su scoring, su version del modelo, su corte, sus relojes y su feature_hash. Una
    prediccion es la de un resultado observado, no una recomendacion para la cuenta."""
    cuenta = s.exec(select(CuentaCanonica).where(CuentaCanonica.cuenta_id == cuenta_id)).first()
    if cuenta is None:
        raise ErrorDeApi(404, "CUENTA_NO_ENCONTRADA", f"No existe la cuenta {cuenta_id}.")
    total, filas = consultas.predicciones_de_cuenta(
        s, id_de(cuenta), desplazamiento=parametros.desplazamiento, limite=parametros.por_pagina
    )
    return PaginaPrediccionesDeCuenta(
        cuenta_id=cuenta_id,
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[PrediccionDeCuentaRespuesta(**vars(p)) for p in filas],
    )


# --- las respuestas -------------------------------------------------------------------------------


def _scoring(s: Session, scoring_run_id: UUID) -> consultas.ScoringVista:
    try:
        return consultas.obtener_scoring(s, scoring_run_id)
    except consultas.NoEncontrado as exc:
        raise ErrorDeApi(
            404, "SCORING_NO_ENCONTRADO", f"No existe el scoring {scoring_run_id}."
        ) from exc


def _publicado(s: Session, scoring_run_id: UUID) -> consultas.ScoringVista:
    vista = _scoring(s, scoring_run_id)
    if vista.ejecucion.estado != EstadoScoringPredictivo.EXITOSA:
        raise ErrorDeApi(
            409,
            "SCORING_NO_PUBLICADO",
            f"El scoring {scoring_run_id} esta {vista.ejecucion.estado}"
            + (f" ({vista.ejecucion.resultado})" if vista.ejecucion.resultado else "")
            + ": no publico predicciones.",
        )
    return vista


def _duracion(iniciada, terminada) -> float | None:
    return None if terminada is None else (terminada - iniciada).total_seconds()


def entrenamiento_respuesta(v: consultas.EntrenamientoVista) -> EntrenamientoPredictivoRespuesta:
    e = v.ejecucion
    return EntrenamientoPredictivoRespuesta(
        training_run_id=e.training_run_id,
        estado=e.estado,
        resultado=e.resultado,
        despacho_id=e.despacho_id,
        cartera_id=e.cartera_id,
        zona_horaria=e.zona_horaria,
        version_modelo=e.version_modelo,
        version_features=e.version_features,
        hash_features=e.hash_features,
        version_targets=e.version_targets,
        hash_targets=e.hash_targets,
        version_politica=e.version_politica,
        hash_politica=e.hash_politica,
        hash_manifiesto=e.hash_manifiesto,
        firma_inputs=e.firma_inputs,
        dataset_hash=e.dataset_hash,
        anclas=e.anclas,
        ejemplos_total=e.ejemplos_total,
        ejemplos_train=e.ejemplos_train,
        ejemplos_validation=e.ejemplos_validation,
        ejemplos_test=e.ejemplos_test,
        ejemplos_purge=e.ejemplos_purge,
        ejemplos_censurados=e.ejemplos_censurados,
        conteos=e.conteos,
        backtest=e.backtest,
        tiempos=e.tiempos,
        manifiesto=e.manifiesto,
        modelo_version_id=v.modelo_version_id,
        estado_modelo=None if v.estado_modelo is None else EstadoModeloPredictivo(v.estado_modelo),
        trabajo_id=v.trabajo_id,
        iniciada_en=e.iniciada_en,
        terminada_en=e.terminada_en,
        duracion_segundos=_duracion(e.iniciada_en, e.terminada_en),
        detalle=e.detalle,
    )


def modelo_resumen(v: consultas.ModeloVista) -> ModeloPredictivoResumen:
    m = v.version
    return ModeloPredictivoResumen(
        modelo_version_id=m.modelo_version_id,
        version_modelo=m.version_modelo,
        estado=m.estado,
        training_run_id=v.training_run_id,
        version_features=m.version_features,
        hash_features=m.hash_features,
        version_targets=m.version_targets,
        hash_targets=m.hash_targets,
        version_politica=m.version_politica,
        hash_politica=m.hash_politica,
        dataset_hash=m.dataset_hash,
        hash_modelo=m.hash_modelo,
        periodo_train_desde=m.periodo_train_desde,
        periodo_train_hasta=m.periodo_train_hasta,
        periodo_validation_desde=m.periodo_validation_desde,
        periodo_validation_hasta=m.periodo_validation_hasta,
        periodo_test_desde=m.periodo_test_desde,
        periodo_test_hasta=m.periodo_test_hasta,
        creada_en=m.creada_en,
        publicada_en=m.publicada_en,
        componentes={c.target: c.estado for c in v.componentes},
    )


def modelo_respuesta(v: consultas.ModeloVista) -> ModeloPredictivoRespuesta:
    m = v.version
    return ModeloPredictivoRespuesta(
        modelo_version_id=m.modelo_version_id,
        version_modelo=m.version_modelo,
        estado=m.estado,
        training_run_id=v.training_run_id,
        version_features=m.version_features,
        hash_features=m.hash_features,
        version_targets=m.version_targets,
        hash_targets=m.hash_targets,
        version_politica=m.version_politica,
        hash_politica=m.hash_politica,
        dataset_hash=m.dataset_hash,
        hash_modelo=m.hash_modelo,
        periodo_train_desde=m.periodo_train_desde,
        periodo_train_hasta=m.periodo_train_hasta,
        periodo_validation_desde=m.periodo_validation_desde,
        periodo_validation_hasta=m.periodo_validation_hasta,
        periodo_test_desde=m.periodo_test_desde,
        periodo_test_hasta=m.periodo_test_hasta,
        creada_en=m.creada_en,
        publicada_en=m.publicada_en,
        ficha=m.metadatos,
        referencia_drift=m.referencia_drift,
        componentes=[
            ComponentePredictivoRespuesta(
                target=c.target,
                algoritmo=c.algoritmo,
                hiperparametros=c.hiperparametros,
                calibracion=c.calibracion,
                hash_artefacto=c.hash_artefacto,
                estado=c.estado,
                compuertas=c.compuertas,
                metricas_validation=c.metricas_validation,
                metricas_test=c.metricas_test,
                baseline_test=c.baseline_test,
                bootstrap_test=c.bootstrap_test,
                explicacion_global=c.explicacion_global,
                artefacto=c.artefacto,
            )
            for c in v.componentes
        ],
    )


def scoring_respuesta(v: consultas.ScoringVista) -> ScoringPredictivoRespuesta:
    e = v.ejecucion
    return ScoringPredictivoRespuesta(
        scoring_run_id=e.scoring_run_id,
        estado=e.estado,
        resultado=e.resultado,
        modo=e.modo,
        modelo_version_id=v.modelo_version_id,
        corte_id=v.corte_id,
        despacho_id=e.despacho_id,
        cartera_id=e.cartera_id,
        as_of=e.as_of,
        known_at=e.known_at,
        zona_horaria=e.zona_horaria,
        version_features=e.version_features,
        hash_features=e.hash_features,
        firma_inputs=e.firma_inputs,
        cuentas_objetivo=e.cuentas_objetivo,
        predicciones_publicadas=e.predicciones_publicadas,
        hash_predicciones=e.hash_predicciones,
        hash_drift=e.hash_drift,
        disponibilidad=e.disponibilidad,
        drift_resumen=e.drift_resumen,
        tiempos=e.tiempos,
        trabajo_id=v.trabajo_id,
        iniciada_en=e.iniciada_en,
        terminada_en=e.terminada_en,
        duracion_segundos=_duracion(e.iniciada_en, e.terminada_en),
        detalle=e.detalle,
    )
