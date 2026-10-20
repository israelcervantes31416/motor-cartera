"""app/v1 por HTTP: la fachada de lectura de la app web.

Solo hay GET. Cada ruta compone, para una pantalla, lo que los dominios ya publicaron, bajo un
contexto operativo explicito (el corte y cada ejecucion, por su identificador publico), y solo
existe donde ahorra viajes, uniones en el navegador o una mezcla accidental de ejecuciones que no
encadenan. Lo que la app lee tal cual (analytics/v1, predictive/model-v1 y optimization/v1, sin un
dato personal) lo lee de sus propias rutas.

Ninguna respuesta lleva un CLIENTE_UNICO, un actor_ref, un nombre, una direccion, un telefono, una
coordenada ni una geometria; las que reutilizan una respuesta de un dominio que si los lleva la
derivan sin ellos (`sin_datos_personales`).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlmodel import Session

from motor_cartera.analytics import consultas as analytics
from motor_cartera.api import atribuciones as api_atribuciones
from motor_cartera.api import campo_v2 as api_campo_v2
from motor_cartera.api import cuentas as api_cuentas
from motor_cartera.api import decisiones_v2 as api_decisiones_v2
from motor_cartera.api import gestiones as api_gestiones
from motor_cartera.api import lifecycle as api_lifecycle
from motor_cartera.api import ruteo_v2 as api_ruteo_v2
from motor_cartera.api.autorizacion import principal_de
from motor_cartera.api.dependencias import SesionDeLectura
from motor_cartera.api.errores import DetalleError, ErrorDeApi, errores
from motor_cartera.api.esquemas import Paginacion, ParametrosHistoria
from motor_cartera.api.esquemas_analytics import (
    EmbudoAnalyticsRespuesta,
    EtapaEmbudoAnalytics,
    IndicadorAnalyticsRespuesta,
)
from motor_cartera.api.esquemas_app_v1 import (
    AnalyticsDelResumen,
    AristaLinaje,
    AsignacionCampoDeCuentaApp,
    CampoDeCuentaSeccion,
    CampoDelResumen,
    CarteraOpcion,
    ContextoDeclarado,
    ContextoEntrada,
    ContextoValidadoRespuesta,
    CorteDelContexto,
    CorteOpcion,
    Cuenta360AppRespuesta,
    CuentaListadaRespuesta,
    DecisionDeCuentaSeccion,
    DecisionDelResumen,
    DecisionV2DeCuentaApp,
    DistribucionPrediccionesRespuesta,
    EjecucionDelContexto,
    EjecucionesDeTipo,
    EstadoFuente,
    EstadoSeccion,
    GeografiaDeCuentaSeccion,
    GestionesDeCuentaApp,
    HistogramaPredicciones,
    LinajeRespuesta,
    NodoLinaje,
    OpcionesRespuesta,
    OptimizacionDeCuentaSeccion,
    OptimizacionDelResumen,
    PaginaAtribucionesDeCuentaApp,
    PaginaCuentasApp,
    PaginaGestionesApp,
    PaginaHistoriaApp,
    PaginaLifecycleApp,
    PaginaParadasApp,
    PagosDeCuentaApp,
    ParadaDeCuentaApp,
    ParametrosCuentas,
    ParametrosOpciones,
    PredictivoDeCuentaSeccion,
    PredictivoDelResumen,
    PresenciaRespuesta,
    ProblemaDeContexto,
    ResumenAppRespuesta,
    RuteoDeCuentaSeccion,
    RuteoDelResumen,
    SesionApp,
    SugerenciaDeContexto,
    sin_personales,
)
from motor_cartera.api.esquemas_campo import BaseDelCampo, CargaGestorRespuesta, GestorDelCampo
from motor_cartera.api.esquemas_geografia import CorteRespuesta
from motor_cartera.api.esquemas_lifecycle import ParametrosGestiones, ParametrosLifecycle
from motor_cartera.api.esquemas_optimizacion import ResultadoCuentaRespuesta
from motor_cartera.api.esquemas_predictivo import PrediccionDeCuentaRespuesta
from motor_cartera.api.operacional import SIN_CLAVE
from motor_cartera.app_v1 import cuentas as explorador
from motor_cartera.app_v1 import linaje as linaje_
from motor_cartera.app_v1 import opciones as opciones_
from motor_cartera.app_v1 import resumen as resumen_
from motor_cartera.app_v1.contexto import (
    Contexto,
    ContextoResuelto,
    CorteNoEncontrado,
    Ejecucion,
    Tipo,
    leer,
    leer_corte,
    resolver,
)
from motor_cartera.atribucion import consultas as atribucion
from motor_cartera.campo_v2 import consultas as campo_v2
from motor_cartera.config import Config
from motor_cartera.db.modelos import (
    CuentaCanonica,
    EjecucionAnalytics,
    EjecucionDecisionV2,
    EjecucionOptimizacion,
    EjecucionScoringPredictivo,
    id_de,
)
from motor_cartera.geografia import consultas as geografia
from motor_cartera.geografia.reglas import comparacion
from motor_cartera.historia import cuenta360
from motor_cartera.historia.cuenta360 import CuentaNoEncontrada
from motor_cartera.lifecycle import consultas as lifecycle
from motor_cartera.motor_pagos import consultas as motor_pagos
from motor_cartera.motor_pagos.reglas import VERSION_MOTOR_PAGOS
from motor_cartera.optimizacion import consultas as optimizacion
from motor_cartera.predictivo import consultas as predictivo
from motor_cartera.ruteo_v2 import consultas as ruteo_v2
from motor_cartera.territorial_v2 import consultas as territorial_v2

router = APIRouter(prefix="/app/v1", tags=["app"])

CORTE_NO_EXISTE = (404, "CORTE_NO_ENCONTRADO", "No existe el corte canonico del contexto.")
INCOMPATIBLE = (
    409,
    "CONTEXTO_INCOMPATIBLE",
    "Las ejecuciones del contexto no encadenan: una no existe, es de otro corte o leyo otra cosa. "
    "Los problemas van en `detalles`; GET /app/v1/contextos/validar los explica.",
)
CUENTA_NO_EXISTE = (404, "CUENTA_NO_ENCONTRADA", "No existe una cuenta canonica con ese cuenta_id.")
INVALIDA = (422, "ENTRADA_INVALIDA", "Un identificador, un filtro o la paginacion no son validos.")
DESCRIPCION_CONTEXTO = (
    "El contexto va en la query string: `corte_id` y, si se leen, `decision_run_id`, "
    "`analytics_run_id`, `scoring_run_id`, `optimizacion_run_id`, `campo_run_id` y "
    "`ruteo_run_id`. Nada se elige como 'lo ultimo': lo que el contexto no declara no se lee."
)


# --- el contexto ----------------------------------------------------------------------------------


def _contexto(entrada: ContextoEntrada | ParametrosCuentas) -> Contexto:
    return Contexto(
        corte_id=entrada.corte_id,
        decision_run_id=entrada.decision_run_id,
        analytics_run_id=entrada.analytics_run_id,
        scoring_run_id=entrada.scoring_run_id,
        optimizacion_run_id=entrada.optimizacion_run_id,
        campo_run_id=entrada.campo_run_id,
        ruteo_run_id=entrada.ruteo_run_id,
    )


def _resolver(s: Session, contexto: Contexto) -> ContextoResuelto:
    try:
        return resolver(s, contexto)
    except CorteNoEncontrado as exc:
        raise ErrorDeApi(404, "CORTE_NO_ENCONTRADO", str(exc)) from exc


def _compatible(s: Session, contexto: Contexto) -> ContextoResuelto:
    """El contexto resuelto, o 409 con cada problema: ninguna pantalla combina un contexto que no
    encadena."""
    resuelto = _resolver(s, contexto)
    if not resuelto.compatible:
        error = ErrorDeApi(
            409,
            "CONTEXTO_INCOMPATIBLE",
            "Las ejecuciones del contexto no encadenan: "
            + " ".join(p.mensaje for p in resuelto.problemas),
        )
        error.respuesta.detalles = [
            DetalleError(campo=",".join(p.ejecuciones), problema=f"{p.codigo}: {p.mensaje}")
            for p in resuelto.problemas
        ]
        raise error
    return resuelto


def _corte_respuesta(corte) -> CorteDelContexto:
    return CorteDelContexto(
        corte_id=corte.corte_id,
        despacho_id=corte.despacho_id,
        cartera_id=corte.cartera_id,
        fecha_corte=corte.fecha_corte,
        cuentas=corte.cuentas,
        firma_contenido=corte.firma_contenido,
        version_modelo=corte.version_modelo,
        dataset_id=corte.dataset_id,
        run_id=corte.run_id,
        creado_en=corte.creado_en,
    )


def _ejecucion_respuesta(e: Ejecucion) -> EjecucionDelContexto:
    return EjecucionDelContexto(
        tipo=e.tipo.value,
        run_id=e.run_id,
        estado=e.estado,
        resultado=e.resultado,
        corte_id=e.corte_id,
        fecha_corte=e.fecha_corte,
        as_of=e.as_of,
        known_at=e.known_at,
        zona_horaria=e.zona_horaria,
        fecha_operacion=e.fecha_operacion,
        version_motor=e.version_motor,
        version_politica=e.version_politica,
        hash_politica=e.hash_politica,
        firma_inputs=e.firma_inputs,
        iniciada_en=e.iniciada_en,
        terminada_en=e.terminada_en,
        lee=e.lee,
    )


def validado_respuesta(resuelto: ContextoResuelto) -> ContextoValidadoRespuesta:
    declaradas = {t.value: r for t, r in resuelto.contexto.declaradas().items()}
    derivadas = {}
    for e in resuelto.ejecuciones.values():
        for clave, valor in e.lee.items():
            if clave not in declaradas:
                derivadas.setdefault(clave, valor)
    return ContextoValidadoRespuesta(
        compatible=resuelto.compatible,
        corte=_corte_respuesta(resuelto.corte),
        declaradas=declaradas,
        ejecuciones=[
            _ejecucion_respuesta(resuelto.ejecuciones[t]) for t in Tipo if t in resuelto.ejecuciones
        ],
        derivadas=dict(sorted(derivadas.items())),
        problemas=[
            ProblemaDeContexto(codigo=p.codigo, mensaje=p.mensaje, ejecuciones=list(p.ejecuciones))
            for p in resuelto.problemas
        ],
    )


@router.get(
    "/sesion",
    response_model=SesionApp,
    summary="Quien ve la app: su tipo, su seudonimo y sus permisos",
    description="Lo que la app necesita para saber que mostrar: si hay una persona con sesion (o "
    "la clave de servicio del proxy de desarrollo) y sus permisos, sin su nombre, su correo ni su "
    "`sub`. Exige `mc.read`: una persona sin ese permiso recibe 403 y la app le dice por que.",
    responses=errores(*SIN_CLAVE),
)
def sesion_actual(request: Request) -> SesionApp:
    principal = principal_de(request)
    if principal is None:  # pragma: no cover - la ruta exige mc.read: siempre hay principal
        raise ErrorDeApi(401, "CREDENCIAL_AUSENTE", "La peticion no trae credencial.")
    return SesionApp(
        tipo=principal.tipo,
        actor=principal.actor,
        permisos=sorted(p.value for p in principal.permisos),
    )


@router.get(
    "/contextos/opciones",
    response_model=OpcionesRespuesta,
    summary="Las carteras, sus cortes y, de un corte, sus ejecuciones y los contextos que se "
    "pueden proponer",
    description="Lo que hace falta para abrir un contexto, sin abrir ninguno: las carteras del "
    "despacho, los cortes de una (del mas reciente al mas antiguo, paginados) y, con `corte_id`, "
    "las ejecuciones mas recientes de cada tipo del corte y hasta seis contextos compatibles que "
    "la app puede proponer. Una sugerencia no se abre sola: la persona elige abrirla.",
    responses=errores(*SIN_CLAVE, CORTE_NO_EXISTE, INVALIDA),
)
def opciones(
    request: Request, parametros: Annotated[ParametrosOpciones, Query()], s: SesionDeLectura
) -> OpcionesRespuesta:
    config: Config = request.app.state.config
    corte = None
    if parametros.corte_id is not None:
        try:
            corte = leer_corte(s, parametros.corte_id)
        except CorteNoEncontrado as exc:
            raise ErrorDeApi(404, "CORTE_NO_ENCONTRADO", str(exc)) from exc
    cartera_id = parametros.cartera_id or (corte.cartera_id if corte else config.cartera_id)
    total, cortes, _ = cuenta360.listar_cortes(
        s,
        config.despacho_id,
        cartera_id,
        desplazamiento=parametros.desplazamiento,
        limite=parametros.por_pagina,
        descendente=True,
    )
    ejecuciones: list[EjecucionesDeTipo] = []
    sugerencias: list[SugerenciaDeContexto] = []
    if corte is not None:
        del_corte = opciones_.del_corte(s, corte)
        ejecuciones = [
            EjecucionesDeTipo(
                tipo=tipo.value,
                total=total_tipo,
                elementos=[_ejecucion_respuesta(e) for e in lista],
            )
            for tipo, (total_tipo, lista) in del_corte.items()
        ]
        sugerencias = [
            SugerenciaDeContexto(
                contexto=ContextoDeclarado(**vars(sugerencia.contexto)),
                codigo=sugerencia.codigo,
                motivo=sugerencia.motivo,
            )
            for sugerencia in opciones_.sugerencias(
                corte, {tipo: lista for tipo, (_, lista) in del_corte.items()}
            )
        ]
    return OpcionesRespuesta(
        despacho_id=config.despacho_id,
        cartera_id=cartera_id,
        carteras=[
            CarteraOpcion(cartera_id=c.cartera_id, cortes=c.cortes, ultimo_corte=c.ultimo_corte)
            for c in opciones_.carteras(s, config.despacho_id)
        ],
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        cortes=[
            CorteOpcion(
                corte_id=v.corte.corte_id,
                fecha_corte=v.corte.fecha_corte,
                cuentas=v.corte.cuentas,
                creado_en=v.corte.creado_en,
            )
            for v in cortes
        ],
        corte=None if corte is None else _corte_respuesta(corte),
        ejecuciones=ejecuciones,
        sugerencias=sugerencias,
    )


@router.get(
    "/contextos/validar",
    response_model=ContextoValidadoRespuesta,
    summary="Valida un contexto: que cada ejecucion exista, sea del corte y encadene con las demas",
    description=DESCRIPCION_CONTEXTO
    + " La respuesta es 200 tambien si el contexto no encadena: `compatible` es falso y "
    "`problemas` dice cada razon (`EJECUCION_NO_ENCONTRADA`, `CORTE_DISTINTO`, "
    "`ENCADENAMIENTO_DISTINTO`). Nada se corrige ni se completa: la app muestra los problemas y "
    "no combina el contexto.",
    responses=errores(*SIN_CLAVE, CORTE_NO_EXISTE, INVALIDA),
)
def validar(
    entrada: Annotated[ContextoEntrada, Query()], s: SesionDeLectura
) -> ContextoValidadoRespuesta:
    return validado_respuesta(_resolver(s, _contexto(entrada)))


# --- el resumen -----------------------------------------------------------------------------------


def _fuente(ejecucion: Ejecucion | None, **datos) -> dict:
    if ejecucion is None:
        return {
            "estado_fuente": EstadoFuente.NO_DECLARADA,
            "run_id": None,
            "estado": None,
            "resultado": None,
        }
    estado_fuente = {
        "EXITOSA": EstadoFuente.DISPONIBLE,
        "EN_PROCESO": EstadoFuente.EN_PROCESO,
    }.get(ejecucion.estado, EstadoFuente.FALLIDA)
    return {
        "estado_fuente": estado_fuente,
        "run_id": ejecucion.run_id,
        "estado": ejecucion.estado,
        "resultado": ejecucion.resultado,
        **(datos if estado_fuente == EstadoFuente.DISPONIBLE else {}),
    }


def distribucion_respuesta(
    scoring: Ejecucion, distribucion: resumen_.Distribucion
) -> DistribucionPrediccionesRespuesta:
    return DistribucionPrediccionesRespuesta(
        scoring_run_id=scoring.run_id,
        modelo_version_id=scoring.lee["MODELO"],
        publicadas=distribucion.publicadas,
        con_imputacion=distribucion.con_imputacion,
        medias=distribucion.medias,
        suma_recuperacion_positiva_esperada_30d=distribucion.suma_recuperacion_positiva_esperada_30d,
        bins=resumen_.BINS,
        histogramas=[
            HistogramaPredicciones(salida=salida, cuentas=cuentas)
            for salida, cuentas in distribucion.histogramas.items()
        ],
    )


@router.get(
    "/resumen",
    response_model=ResumenAppRespuesta,
    summary="El resumen de un contexto compatible: cada bloque, de la ejecucion que declara",
    description=DESCRIPCION_CONTEXTO
    + " Cada bloque es lo que su ejecucion ya publico: el corte; los indicadores y los embudos de "
    "analytics/v1; la distribucion que conto decision/v2; la distribucion de las predicciones del "
    "scoring, contada en la base; el certificado y el uso por canal de la optimizacion; los "
    "conteos del plan de campo/v2 y del ruteo/v2. Un bloque sin ejecucion declarada es "
    "`NO_DECLARADA`, nunca cero; uno cuya ejecucion no publico dice su estado y su resultado.",
    responses=errores(*SIN_CLAVE, CORTE_NO_EXISTE, INCOMPATIBLE, INVALIDA),
)
def resumen(
    entrada: Annotated[ContextoEntrada, Query()], s: SesionDeLectura
) -> ResumenAppRespuesta:
    resuelto = _compatible(s, _contexto(entrada))
    a = resuelto.ejecucion(Tipo.ANALYTICS)
    bloque_analytics = _fuente(a)
    if a is not None and bloque_analytics["estado_fuente"] == EstadoFuente.DISPONIBLE:
        ejecucion = s.get_one(EjecucionAnalytics, a.id)
        bloque_analytics.update(
            as_of=ejecucion.as_of,
            known_at=ejecucion.known_at,
            zona_horaria=ejecucion.zona_horaria,
            granularidad=ejecucion.granularidad,
            version_catalogo=ejecucion.version_catalogo,
            hash_catalogo=ejecucion.hash_catalogo,
            cuentas_cohorte=ejecucion.cuentas_cohorte,
            indicadores=[
                IndicadorAnalyticsRespuesta(**vars(i)) for i in analytics.resumen(s, ejecucion)
            ],
            embudos=[
                EmbudoAnalyticsRespuesta(
                    embudo=nombre, etapas=[EtapaEmbudoAnalytics(**vars(x)) for x in etapas]
                )
                for nombre, etapas in analytics.embudos(s, ejecucion).items()
            ],
        )
    d = resuelto.ejecucion(Tipo.DECISION_V2)
    bloque_decision = _fuente(d)
    if d is not None and bloque_decision["estado_fuente"] == EstadoFuente.DISPONIBLE:
        ejecucion = s.get_one(EjecucionDecisionV2, d.id)
        bloque_decision.update(
            as_of=ejecucion.as_of,
            known_at=ejecucion.known_at,
            version_politica=ejecucion.version_politica,
            hash_politica=ejecucion.hash_politica,
            cuentas_decididas=ejecucion.cuentas_decididas,
            distribucion=ejecucion.distribucion,
        )
    p = resuelto.ejecucion(Tipo.SCORING)
    bloque_predictivo = _fuente(p)
    if p is not None:
        bloque_predictivo.update(modelo_version_id=p.lee["MODELO"], version_modelo=p.version_motor)
    if p is not None and bloque_predictivo["estado_fuente"] == EstadoFuente.DISPONIBLE:
        ejecucion = s.get_one(EjecucionScoringPredictivo, p.id)
        bloque_predictivo.update(
            as_of=ejecucion.as_of,
            known_at=ejecucion.known_at,
            distribucion=distribucion_respuesta(p, resumen_.de_scoring(s, p.id)),
            drift_resumen=ejecucion.drift_resumen,
        )
    o = resuelto.ejecucion(Tipo.OPTIMIZACION)
    bloque_optimizacion = _fuente(o)
    if o is not None:
        ejecucion = s.get_one(EjecucionOptimizacion, o.id)
        bloque_optimizacion.update(
            fecha_operacion=ejecucion.fecha_operacion, solver_status=ejecucion.solver_status
        )
        if bloque_optimizacion["estado_fuente"] == EstadoFuente.DISPONIBLE:
            bloque_optimizacion.update(
                objetivo_primario_centavos=ejecucion.objetivo_primario_centavos,
                best_bound=ejecucion.best_bound,
                mip_gap=ejecucion.mip_gap,
                solver_seconds=ejecucion.solver_seconds,
                cuentas_universo=ejecucion.cuentas_universo,
                cuentas_elegibles=ejecucion.cuentas_elegibles,
                cuentas_asignadas=ejecucion.cuentas_asignadas,
                por_canal=(ejecucion.resumen or {}).get("por_canal"),
            )
    c = resuelto.ejecucion(Tipo.CAMPO_V2)
    bloque_campo = _fuente(c)
    if c is not None and bloque_campo["estado_fuente"] == EstadoFuente.DISPONIBLE:
        plan = api_campo_v2.plan_respuesta(campo_v2.obtener(s, c.run_id))
        bloque_campo.update(
            fecha_operacion=plan.fecha_operacion, conteos=plan.conteos, capacidad=plan.capacidad
        )
    r = resuelto.ejecucion(Tipo.RUTEO_V2)
    bloque_ruteo = _fuente(r)
    if r is not None and bloque_ruteo["estado_fuente"] == EstadoFuente.DISPONIBLE:
        ruteado = api_ruteo_v2.ejecucion_respuesta(ruteo_v2.obtener(s, r.run_id))
        bloque_ruteo.update(
            fecha_operacion=ruteado.fecha_operacion,
            conteos=ruteado.conteos,
            totales=ruteado.totales,
        )
    return ResumenAppRespuesta(
        corte=_corte_respuesta(resuelto.corte),
        analytics=AnalyticsDelResumen(**bloque_analytics),
        decision=DecisionDelResumen(**bloque_decision),
        predictivo=PredictivoDelResumen(**bloque_predictivo),
        optimizacion=OptimizacionDelResumen(**bloque_optimizacion),
        campo=CampoDelResumen(**bloque_campo),
        ruteo=RuteoDelResumen(**bloque_ruteo),
    )


@router.get(
    "/predictivo/scorings/{scoring_run_id}/distribucion",
    response_model=DistribucionPrediccionesRespuesta,
    summary="La distribucion de las predicciones publicadas de un scoring, contada en la base",
    description="Cuantas predicciones publico el scoring, cuantas llevan imputacion, la media de "
    "cada probabilidad, la suma de la recuperacion positiva esperada a 30 dias y, por "
    "probabilidad, cuantas caen en cada decimo de [0, 1]. Una pasada por sus predicciones, en la "
    "base: el navegador nunca baja las predicciones para contarlas. Es un resumen descriptivo de "
    "lo publicado: no una prediccion nueva, no una metrica de analytics/v1 y no un efecto causal.",
    responses=errores(
        *SIN_CLAVE,
        (404, "SCORING_NO_ENCONTRADO", "No existe un scoring con ese scoring_run_id."),
        (409, "SCORING_NO_PUBLICADO", "El scoring no esta EXITOSA: no publico predicciones."),
        (422, "ENTRADA_INVALIDA", "No es un UUID."),
    ),
)
def distribucion_de_predicciones(
    scoring_run_id: UUID, s: SesionDeLectura
) -> DistribucionPrediccionesRespuesta:
    encontrados = _leer_una(s, Tipo.SCORING, scoring_run_id)
    if encontrados is None:
        raise ErrorDeApi(404, "SCORING_NO_ENCONTRADO", f"No existe el scoring {scoring_run_id}.")
    if encontrados.estado != "EXITOSA":
        raise ErrorDeApi(
            409,
            "SCORING_NO_PUBLICADO",
            f"El scoring {scoring_run_id} esta {encontrados.estado}: no publico predicciones.",
        )
    return distribucion_respuesta(encontrados, resumen_.de_scoring(s, encontrados.id))


def _leer_una(s: Session, tipo: Tipo, run_id: UUID) -> Ejecucion | None:
    encontradas = leer(s, tipo, run_ids=[run_id])
    return encontradas[0] if encontradas else None


# --- el explorador de cuentas ---------------------------------------------------------------------


@router.get(
    "/cuentas",
    response_model=PaginaCuentasApp,
    summary="Las cuentas del corte del contexto, filtradas, ordenadas y paginadas en la base",
    description=DESCRIPCION_CONTEXTO
    + " Cada cuenta del corte con su producto, su saldo, su atraso y lo que publicaron de ella la "
    "decision/v2, el scoring y la optimizacion del contexto. Filtros de una lista blanca "
    "(`cuenta`, el principio del cuenta_id; `producto`; `atraso_bucket`; `prioridad` y `canal` de "
    "la decision/v2; `estado_prediccion`; `estado_optimizacion`) y un `orden` de una lista "
    "cerrada con su `sentido`. Hasta 100 por pagina: ningun cliente baja la cartera entera. Un "
    "filtro o un orden de una ejecucion que el contexto no declara es un 422, no una lista vacia.",
    responses=errores(
        *SIN_CLAVE,
        CORTE_NO_EXISTE,
        INCOMPATIBLE,
        (422, "FILTRO_SIN_EJECUCION", "El filtro o el orden es de una ejecucion no declarada."),
        INVALIDA,
    ),
)
def cuentas(
    parametros: Annotated[ParametrosCuentas, Query()], s: SesionDeLectura
) -> PaginaCuentasApp:
    resuelto = _compatible(s, _contexto(parametros))

    def interno(tipo: Tipo) -> int | None:
        e = resuelto.ejecucion(tipo)
        return None if e is None or e.estado != "EXITOSA" else e.id

    try:
        total, filas = explorador.listar(
            s,
            corte_id=resuelto.corte.id,
            cuentas_del_corte=resuelto.corte.cuentas,
            decision_id=interno(Tipo.DECISION_V2),
            scoring_id=interno(Tipo.SCORING),
            optimizacion_id=interno(Tipo.OPTIMIZACION),
            filtros=explorador.Filtros(
                cuenta=parametros.cuenta,
                producto=parametros.producto,
                atraso_bucket=parametros.atraso_bucket,
                prioridad=parametros.prioridad,
                canal=parametros.canal,
                estado_prediccion=parametros.estado_prediccion,
                estado_optimizacion=parametros.estado_optimizacion,
            ),
            orden=parametros.orden.value,
            descendente=parametros.sentido == "DESC",
            desplazamiento=parametros.desplazamiento,
            limite=parametros.por_pagina,
        )
    except explorador.FiltroSinEjecucion as exc:
        raise ErrorDeApi(422, "FILTRO_SIN_EJECUCION", str(exc)) from exc
    return PaginaCuentasApp(
        corte_id=resuelto.corte.corte_id,
        orden=parametros.orden,
        sentido=parametros.sentido,
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[CuentaListadaRespuesta(**vars(f)) for f in filas],
    )


# --- la Cuenta 360 de la app ----------------------------------------------------------------------


def _cuenta(s: Session, cuenta_id: UUID) -> CuentaCanonica:
    try:
        return cuenta360.obtener(s, cuenta_id)
    except CuentaNoEncontrada as exc:
        raise ErrorDeApi(
            404, "CUENTA_NO_ENCONTRADA", f"No existe una cuenta canonica con cuenta_id {cuenta_id}."
        ) from exc


def _seccion(ejecucion: Ejecucion | None) -> tuple[EstadoSeccion, UUID | None]:
    if ejecucion is None:
        return EstadoSeccion.NO_DECLARADA, None
    if ejecucion.estado != "EXITOSA":
        return EstadoSeccion.NO_PUBLICADA, ejecucion.run_id
    return EstadoSeccion.DISPONIBLE, ejecucion.run_id


@router.get(
    "/cuentas/{cuenta_id}",
    response_model=Cuenta360AppRespuesta,
    summary="Cuenta 360 en un contexto: su presencia, su snapshot del corte y lo que cada "
    "ejecucion declarada publico de ella",
    description=DESCRIPCION_CONTEXTO
    + " Su presencia y su snapshot a la fecha del corte del contexto; su decision en la "
    "decision/v2 declarada; su geografia y su base en la territorial/v2 que leyo el plan de campo "
    "declarado; su asignacion en ese plan, con la carga de su gestor; su parada en el ruteo/v2 "
    "declarado; su prediccion en el scoring declarado; su resultado en la optimizacion declarada. "
    "Una seccion sin ejecucion declarada es `NO_DECLARADA`; una en la que la cuenta no esta, "
    "`SIN_RESULTADO`. Sin CLIENTE_UNICO ni coordenadas. Las explicaciones (la de decision/v2, la "
    "de la prediccion y la del plan) y las listas (historia, pagos, gestiones) son rutas aparte.",
    responses=errores(*SIN_CLAVE, CORTE_NO_EXISTE, CUENTA_NO_EXISTE, INCOMPATIBLE, INVALIDA),
)
def cuenta(
    cuenta_id: UUID, entrada: Annotated[ContextoEntrada, Query()], s: SesionDeLectura
) -> Cuenta360AppRespuesta:
    resuelto = _compatible(s, _contexto(entrada))
    la_cuenta = _cuenta(s, cuenta_id)
    corte = resuelto.corte
    vista = cuenta360.resumen(s, la_cuenta, corte.fecha_corte)
    p = vista.presencia
    snapshot = vista.snapshot_actual
    en_el_corte = snapshot is not None and snapshot.corte_id == corte.corte_id
    return Cuenta360AppRespuesta(
        cuenta_id=la_cuenta.cuenta_id,
        despacho_id=la_cuenta.despacho_id,
        cartera_id=la_cuenta.cartera_id,
        corte_id=corte.corte_id,
        fecha_corte=corte.fecha_corte,
        en_el_corte=en_el_corte,
        presencia=PresenciaRespuesta(
            primera_observacion=p.primera_observacion,
            ultima_observacion=p.ultima_observacion,
            ultimo_corte_cartera=None if p.ultimo_corte is None else p.ultimo_corte.fecha_corte,
            estado_presencia=p.estado,
            cortes_observados=p.cortes_observados,
            cortes_ausentes_desde_primera_observacion=p.cortes_ausentes,
            salidas_observadas=p.salidas_observadas,
            reingresos_observados=p.reingresos_observados,
            pagos_observados=vista.pagos_observados,
        ),
        snapshot=api_cuentas.snapshot_respuesta(snapshot) if en_el_corte else None,
        decision=_decision_de(s, resuelto, la_cuenta),
        geografia=_geografia_de(s, resuelto, la_cuenta),
        campo=_campo_de(s, resuelto, la_cuenta),
        ruteo=_ruteo_de(s, resuelto, la_cuenta),
        predictivo=_prediccion_de(s, resuelto, la_cuenta),
        optimizacion=_optimizacion_de(s, resuelto, la_cuenta),
    )


def _decision_de(s: Session, resuelto: ContextoResuelto, cuenta: CuentaCanonica):
    e = resuelto.ejecucion(Tipo.DECISION_V2)
    estado, run_id = _seccion(e)
    if e is None or estado != EstadoSeccion.DISPONIBLE:
        return DecisionDeCuentaSeccion(estado=estado, run_id=run_id)
    fila = s.execute(
        text(
            "SELECT prioridad_base, prioridad_final, canal_recomendado, ajuste_total, motivos, "
            "encode(decision_hash, 'hex') FROM resultado_decision_v2 "
            "WHERE ejecucion_decision_v2_id = :e AND cuenta_canonica_id = :c"
        ),
        {"e": e.id, "c": cuenta.id},
    ).one_or_none()
    if fila is None:
        return DecisionDeCuentaSeccion(estado=EstadoSeccion.SIN_RESULTADO, run_id=run_id)
    return DecisionDeCuentaSeccion(
        estado=estado,
        run_id=run_id,
        as_of=e.as_of,
        known_at=e.known_at,
        prioridad_base=fila[0],
        prioridad_final=fila[1],
        canal_recomendado=fila[2],
        ajuste_total=fila[3],
        motivos=[m["codigo"] for m in fila[4]],
        decision_hash=fila[5],
    )


def _interno(s: Session, tabla: str, columna: str, run_id: UUID) -> int | None:
    return s.execute(text(f"SELECT id FROM {tabla} WHERE {columna} = :r"), {"r": run_id}).scalar()


def _geografia_de(s: Session, resuelto: ContextoResuelto, cuenta: CuentaCanonica):
    """Su geografia y su base en la territorial/v2 que leyo el plan de campo del contexto (o, sin
    plan, la que declaro el manifiesto de analytics): nunca la vigente."""
    territorial_run_id = resuelto.derivada("TERRITORIAL_V2")
    if territorial_run_id is None:
        return GeografiaDeCuentaSeccion(estado=EstadoSeccion.NO_DECLARADA, run_id=None)
    territorial_id = _interno(
        s, "ejecucion_territorial_v2", "territorial_run_id", territorial_run_id
    )
    asignada = (
        None if territorial_id is None else territorial_v2.de_ejecucion(s, cuenta, territorial_id)
    )
    if asignada is None:
        return GeografiaDeCuentaSeccion(
            estado=EstadoSeccion.SIN_RESULTADO,
            run_id=territorial_run_id,
            territorial_run_id=territorial_run_id,
        )
    geografia_id = asignada.ejecucion.ejecucion_geografia_id
    ubicada = geografia.de_ejecucion(s, cuenta, geografia_id)
    r = None if ubicada is None else ubicada.resultado
    a = asignada.vista.asignacion
    return GeografiaDeCuentaSeccion(
        estado=EstadoSeccion.DISPONIBLE,
        run_id=territorial_run_id,
        territorial_run_id=territorial_run_id,
        geografia_run_id=asignada.geografia_run_id,
        estado_geografia=asignada.vista.estado_geografia,
        cvegeo=asignada.vista.cvegeo,
        municipio=asignada.vista.nombre_oficial,
        municipio_fuente=None if r is None else r.municipio_fuente_resuelto,
        comparacion_municipio=None if r is None else comparacion(r.coincide_municipio).value,
        estado_asignacion=a.estado_asignacion,
        codigo_base=asignada.vista.codigo_base,
        nombre_base=asignada.vista.nombre_base,
        distancia_base_m=a.distancia_base_m,
    )


def _campo_de(s: Session, resuelto: ContextoResuelto, cuenta: CuentaCanonica):
    e = resuelto.ejecucion(Tipo.CAMPO_V2)
    estado, run_id = _seccion(e)
    if e is None or estado != EstadoSeccion.DISPONIBLE:
        return CampoDeCuentaSeccion(estado=estado, run_id=run_id)
    h = campo_v2.de_plan(s, cuenta, e.id)
    if h is None:
        return CampoDeCuentaSeccion(estado=EstadoSeccion.SIN_RESULTADO, run_id=run_id)
    asignacion = AsignacionCampoDeCuentaApp.model_validate(
        {
            **api_campo_v2.asignacion_respuesta(h.vista).model_dump(),
            "campo_run_id": h.ejecucion.campo_run_id,
            "fecha_operacion": h.ejecucion.fecha_operacion,
            "corte": CorteRespuesta(corte_id=h.corte_id, fecha_corte=h.fecha_corte).model_dump(),
            "configuracion_campo_version_id": h.configuracion_campo_version_id,
            "version_politica": h.ejecucion.version_politica,
        }
    )
    carga = None
    gestor = h.vista.asignacion.gestor_campo_id
    if gestor is not None:
        fila = s.execute(
            text(
                "SELECT k.estado_jornada, k.capacidad, k.asignadas, k.utilizacion, k.microtramos, "
                "k.tramos, k.rebalanceadas, k.ventanas, g.gestor_id, g.codigo, b.base_id, b.codigo "
                "FROM carga_gestor_campo k JOIN gestor_campo g ON g.id = k.gestor_campo_id "
                "JOIN base_operativa b ON b.id = k.base_operativa_id "
                "WHERE k.ejecucion_campo_v2_id = :e AND k.gestor_campo_id = :g"
            ),
            {"e": e.id, "g": gestor},
        ).one_or_none()
        if fila is not None:
            carga = CargaGestorRespuesta(
                gestor=GestorDelCampo(gestor_id=fila[8], codigo=fila[9]),
                base=BaseDelCampo(base_id=fila[10], codigo=fila[11]),
                estado_jornada=fila[0],
                capacidad=fila[1],
                asignadas=fila[2],
                utilizacion=fila[3],
                microtramos=fila[4],
                tramos=fila[5],
                rebalanceadas=fila[6],
                ventanas=fila[7],
            )
    return CampoDeCuentaSeccion(
        estado=estado, run_id=run_id, asignacion=asignacion, carga_gestor=carga
    )


def _ruteo_de(s: Session, resuelto: ContextoResuelto, cuenta: CuentaCanonica):
    e = resuelto.ejecucion(Tipo.RUTEO_V2)
    estado, run_id = _seccion(e)
    if e is None or estado != EstadoSeccion.DISPONIBLE:
        return RuteoDeCuentaSeccion(estado=estado, run_id=run_id)
    h = ruteo_v2.de_ejecucion(s, cuenta, e.id)
    if h is None:
        return RuteoDeCuentaSeccion(estado=EstadoSeccion.SIN_RESULTADO, run_id=run_id)
    parada = ParadaDeCuentaApp.model_validate(
        {
            **api_ruteo_v2.parada_respuesta(h.vista, ZoneInfo(h.zona_horaria)).model_dump(),
            "ruteo_run_id": h.ejecucion.ruteo_run_id,
            "fecha_operacion": h.ejecucion.fecha_operacion,
            "zona_horaria": h.zona_horaria,
            "base": h.codigo_base,
            "estado_ruta": h.estado_ruta,
            "version_politica": h.ejecucion.version_politica,
        }
    )
    return RuteoDeCuentaSeccion(estado=estado, run_id=run_id, parada=parada)


def _prediccion_de(s: Session, resuelto: ContextoResuelto, cuenta: CuentaCanonica):
    e = resuelto.ejecucion(Tipo.SCORING)
    estado, run_id = _seccion(e)
    if e is None or estado != EstadoSeccion.DISPONIBLE:
        return PredictivoDeCuentaSeccion(estado=estado, run_id=run_id)
    vista = predictivo.de_scoring(s, s.get_one(EjecucionScoringPredictivo, e.id), id_de(cuenta))
    if vista is None:
        return PredictivoDeCuentaSeccion(estado=EstadoSeccion.SIN_RESULTADO, run_id=run_id)
    return PredictivoDeCuentaSeccion(
        estado=estado, run_id=run_id, prediccion=PrediccionDeCuentaRespuesta(**vars(vista))
    )


def _optimizacion_de(s: Session, resuelto: ContextoResuelto, cuenta: CuentaCanonica):
    e = resuelto.ejecucion(Tipo.OPTIMIZACION)
    estado, run_id = _seccion(e)
    if e is None or estado != EstadoSeccion.DISPONIBLE:
        return OptimizacionDeCuentaSeccion(
            estado=estado, run_id=run_id, fecha_operacion=None if e is None else e.fecha_operacion
        )
    vista = optimizacion.resultado_de_cuenta(
        s, s.get_one(EjecucionOptimizacion, e.id), id_de(cuenta)
    )
    if vista is None:
        return OptimizacionDeCuentaSeccion(
            estado=EstadoSeccion.SIN_RESULTADO, run_id=run_id, fecha_operacion=e.fecha_operacion
        )
    return OptimizacionDeCuentaSeccion(
        estado=estado,
        run_id=run_id,
        fecha_operacion=e.fecha_operacion,
        resultado=ResultadoCuentaRespuesta(**vars(vista)),
    )


@router.get(
    "/cuentas/{cuenta_id}/decision",
    response_model=DecisionV2DeCuentaApp,
    summary="La decision de una cuenta en una decision/v2, explicada, sin datos personales",
    description="La de GET /decisiones-v2/{decision_run_id}/resultados/{cuenta_id}: sus features "
    "tal como se publicaron, cada regla con el valor que encontro y si aplico, la prioridad base y "
    "la final, el canal, los motivos, los hashes y su verificacion. Sin su CLIENTE_UNICO. Una "
    "decision/v2 es un motor de reglas: no una probabilidad ni una prediccion.",
    responses=errores(
        *SIN_CLAVE,
        (404, "DECISION_V2_NO_ENCONTRADA", "No existe esa decision/v2."),
        CUENTA_NO_EXISTE,
        (404, "CUENTA_SIN_DECISION", "La decision no decidio esa cuenta."),
        INVALIDA,
    ),
)
def decision_de_cuenta(
    cuenta_id: UUID,
    decision_run_id: Annotated[UUID, Query(description="La decision/v2 del contexto.")],
    s: SesionDeLectura,
) -> BaseModel:
    respuesta = api_decisiones_v2.decision_de_cuenta(decision_run_id, cuenta_id, s)
    return sin_personales(DecisionV2DeCuentaApp, respuesta)


@router.get(
    "/cuentas/{cuenta_id}/historia",
    response_model=PaginaHistoriaApp,
    summary="Los snapshots de la cuenta, corte por corte, sin datos personales",
    description="La de GET /cuentas/{cuenta_id}/historia, sin su CLIENTE_UNICO: un snapshot por "
    "corte en que se observo, con su continuidad, sus deltas y su evidencia (corte, dataset, fila "
    "y hoja de la fuente).",
    responses=errores(*SIN_CLAVE, CUENTA_NO_EXISTE, INVALIDA),
)
def historia_de_cuenta(
    cuenta_id: UUID, parametros: Annotated[ParametrosHistoria, Query()], s: SesionDeLectura
) -> BaseModel:
    return sin_personales(PaginaHistoriaApp, api_cuentas.historia(cuenta_id, parametros, s))


@router.get(
    "/cuentas/{cuenta_id}/linea-de-tiempo",
    response_model=PaginaLifecycleApp,
    summary="La linea de tiempo de la cuenta en sus tres dominios, sin datos personales",
    description="La de GET /cuentas/{cuenta_id}/lifecycle, sin su CLIENTE_UNICO, sin el actor_ref "
    "de los eventos y sin sus llaves de idempotencia: los eventos operacionales por su "
    "`ocurrido_en` (con su `registrado_en`: un evento registrado tarde aparece donde ocurrio), lo "
    "que dijo cada corte de su promesa o su plan, y sus movimientos economicos.",
    responses=errores(*SIN_CLAVE, CUENTA_NO_EXISTE, INVALIDA),
)
def linea_de_tiempo(
    cuenta_id: UUID,
    request: Request,
    parametros: Annotated[ParametrosLifecycle, Query()],
    s: SesionDeLectura,
) -> BaseModel:
    pagina = api_lifecycle.linea_de_tiempo(cuenta_id, request, parametros, s)
    return sin_personales(PaginaLifecycleApp, pagina)


@router.get(
    "/cuentas/{cuenta_id}/pagos",
    response_model=PagosDeCuentaApp,
    summary="Los pagos de la cuenta: el resumen de su interpretacion y sus movimientos con su "
    "atribucion",
    description="El resumen de la interpretacion vigente del motor de pagos (observaciones, "
    "duplicados, reversos, recuperacion bruta y neta interpretada) y una pagina de sus "
    "movimientos PAGO, cada uno con su atribucion vigente (ASOCIACION_UNICA, AMBIGUA o "
    "SIN_GESTION_CANDIDATA, o null si su ventana no se ha atribuido). Una asociacion operacional "
    "no implica causalidad, y un pago observado no es una recuperacion canonica.",
    responses=errores(*SIN_CLAVE, CUENTA_NO_EXISTE, INVALIDA),
)
def pagos_de_cuenta(
    cuenta_id: UUID, parametros: Annotated[Paginacion, Query()], s: SesionDeLectura
) -> PagosDeCuentaApp:
    la_cuenta = _cuenta(s, cuenta_id)
    resumen_pagos = motor_pagos.resumen_de_cuenta(s, la_cuenta, version=VERSION_MOTOR_PAGOS)
    pagina = api_atribuciones.atribuciones_de_la_cuenta(cuenta_id, parametros, s)
    return PagosDeCuentaApp(
        cuenta_id=cuenta_id,
        resumen=api_cuentas.resumen_pagos_respuesta(resumen_pagos),
        pagina=sin_personales(PaginaAtribucionesDeCuentaApp, pagina),
    )


@router.get(
    "/cuentas/{cuenta_id}/gestiones",
    response_model=GestionesDeCuentaApp,
    summary="Las gestiones de la cuenta: su resumen y una pagina, sin datos personales",
    description="El resumen del lifecycle de la cuenta (gestiones, contactos, promesas, convenios, "
    "visitas y la ultima atribucion de sus pagos) y una pagina de sus gestiones por su "
    "`ocurrido_en`, con su `registrado_en`, sin el actor_ref ni las llaves de idempotencia.",
    responses=errores(*SIN_CLAVE, CUENTA_NO_EXISTE, INVALIDA),
)
def gestiones_de_cuenta(
    cuenta_id: UUID,
    request: Request,
    parametros: Annotated[ParametrosGestiones, Query()],
    s: SesionDeLectura,
) -> GestionesDeCuentaApp:
    config: Config = request.app.state.config
    la_cuenta = _cuenta(s, cuenta_id)
    operacion = lifecycle.resumen_de_cuenta(s, la_cuenta, zona=config.zona_horaria_fuente)
    atribuida = atribucion.ultima_de_cuenta(s, la_cuenta, version_motor=VERSION_MOTOR_PAGOS)
    pagina = api_gestiones.listar_gestiones(cuenta_id, request, parametros, s)
    return GestionesDeCuentaApp(
        cuenta_id=cuenta_id,
        resumen=api_cuentas.lifecycle_resumen_respuesta(operacion, atribuida),
        pagina=sin_personales(PaginaGestionesApp, pagina),
    )


# --- el ruteo y el linaje -------------------------------------------------------------------------


@router.get(
    "/ruteo/rutas/{ruta_id}/paradas",
    response_model=PaginaParadasApp,
    summary="Las paradas de una ruta en su orden de visita, sin datos personales ni coordenadas",
    description="La de GET /ruteo-v2/rutas/{ruta_id}/paradas, sin el CLIENTE_UNICO de cada "
    "cuenta: las PROGRAMADA por `orden_visita`, con su llegada estimada, su servicio y el tramo "
    "desde la anterior, y despues las demas, con su motivo. Sin coordenadas ni geometria.",
    responses=errores(
        *SIN_CLAVE, (404, "RUTA_NO_ENCONTRADA", "No existe una ruta con ese ruta_id."), INVALIDA
    ),
)
def paradas_de_ruta(
    ruta_id: UUID, parametros: Annotated[Paginacion, Query()], s: SesionDeLectura
) -> BaseModel:
    return sin_personales(PaginaParadasApp, api_ruteo_v2.paradas_de_ruta(ruta_id, parametros, s))


@router.get(
    "/linaje",
    response_model=LinajeRespuesta,
    summary="El linaje de un contexto: cada ejecucion con sus versiones y sus hashes, y lo que "
    "cada una declaro que leyo",
    description=DESCRIPCION_CONTEXTO
    + " Los nodos son el corte, las ejecuciones declaradas y lo que ellas leyeron (la territorial "
    "y la geografia de un plan, la version del modelo de un scoring y su entrenamiento, las "
    "configuraciones, la red vial, las entradas del manifiesto de analytics), con su estado, su "
    "version, sus relojes y sus hashes. Las aristas son solo las relaciones que cada ejecucion "
    "declaro al publicarse: ninguna dependencia se infiere. Tambien de un contexto incompatible, "
    "para ver por que no encadena.",
    responses=errores(*SIN_CLAVE, CORTE_NO_EXISTE, INVALIDA),
)
def linaje(entrada: Annotated[ContextoEntrada, Query()], s: SesionDeLectura) -> LinajeRespuesta:
    resuelto = _resolver(s, _contexto(entrada))
    nodos, aristas = linaje_.construir(s, resuelto)
    return LinajeRespuesta(
        compatible=resuelto.compatible,
        nodos=[NodoLinaje(**vars(n)) for n in nodos],
        aristas=[AristaLinaje(**vars(a)) for a in aristas],
    )
