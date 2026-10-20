"""optimization/v1 por HTTP: la politica, las configuraciones, pedir y leer ejecuciones, sus
resultados por cuenta, el uso de cada pool, cada restriccion, el diagnostico, la explicacion de una
cuenta y los planes de una cuenta.

La API no optimiza: cada POST de una ejecucion resuelve y comprueba lo declarado y la deja
EN_PROCESO con su trabajo en la cola durable; el MILP lo resuelve un worker, nunca la peticion. Una
ejecucion declara exactamente su scoring, su decision/v2, su configuracion y, si usa CAMPO o BASE,
su ruteo/v2: nunca "el ultimo". Un plan es de soporte a decision, en sombra: nada de esto ejecuta
una accion.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from sqlmodel import Session, select

from motor_cartera.api.dependencias import Sesion, SesionDeLectura
from motor_cartera.api.errores import DetalleError, ErrorDeApi, errores
from motor_cartera.api.esquemas import Paginacion
from motor_cartera.api.esquemas_optimizacion import (
    AdmisibilidadEntrada,
    ConfiguracionOptimizacionDetalle,
    ConfiguracionOptimizacionEntrada,
    ConfiguracionOptimizacionRespuesta,
    DiagnosticoRespuesta,
    EjecucionOptimizacionEntrada,
    EjecucionOptimizacionRespuesta,
    ExplicacionCuentaRespuesta,
    PaginaCapacidadOptimizacion,
    PaginaConfiguracionesOptimizacion,
    PaginaEjecucionesOptimizacion,
    PaginaPlanesDeCuenta,
    PaginaRestriccionesOptimizacion,
    PaginaResultadosOptimizacion,
    ParametrosCapacidad,
    ParametrosConfiguraciones,
    ParametrosEjecuciones,
    ParametrosRestricciones,
    ParametrosResultados,
    PlanDeCuentaRespuesta,
    PoliticaOptimizacionRespuesta,
    PoolEntrada,
    RestriccionEntrada,
    RestriccionResultadoRespuesta,
    ResultadoCuentaRespuesta,
    UsoPoolRespuesta,
)
from motor_cartera.api.operacional import SIN_CLAVE
from motor_cartera.config import Config
from motor_cartera.db.modelos import (
    CuentaCanonica,
    EjecucionScoringPredictivo,
    EstadoOptimizacion,
    VersionConfiguracionOptimizacion,
    id_de,
)
from motor_cartera.optimizacion import configuracion as configuracion_
from motor_cartera.optimizacion import consultas, ejecuciones
from motor_cartera.optimizacion import solver as solver_
from motor_cartera.optimizacion.politica import (
    ALGORITMO,
    PUBLIC_V1,
    VERSION_MOTOR,
    hash_politica,
)

router = APIRouter(tags=["optimizacion"])

UUID_INVALIDO = (422, "ENTRADA_INVALIDA", "No es un UUID.")
INVALIDA = (422, "ENTRADA_INVALIDA", "Un filtro no es valido, o la paginacion no lo es.")
CONFIGURACION_NO_EXISTE = (
    404,
    "CONFIGURACION_NO_ENCONTRADA",
    "No existe una configuracion de optimizacion con ese configuracion_id.",
)
EJECUCION_NO_EXISTE = (
    404,
    "OPTIMIZACION_NO_ENCONTRADA",
    "No existe una ejecucion con ese optimizacion_run_id.",
)
NO_PUBLICADA = (
    409,
    "OPTIMIZACION_NO_PUBLICADA",
    "La ejecucion no esta EXITOSA: no publico un plan (su diagnostico dice por que).",
)
ERRORES_DE_LA_EJECUCION = (
    (404, "SCORING_NO_ENCONTRADO", "No existe ese scoring."),
    (404, "DECISION_NO_ENCONTRADA", "No existe esa decision/v2."),
    (404, "CONFIGURACION_NO_ENCONTRADA", "No existe esa configuracion."),
    (404, "RUTEO_NO_ENCONTRADO", "No existe ese ruteo/v2."),
    (409, "SCORING_NO_EXITOSO", "El scoring no esta EXITOSA."),
    (409, "MODELO_NO_PUBLICADO", "El modelo del scoring no esta PUBLICADA."),
    (409, "DECISION_NO_EXITOSA", "La decision/v2 no esta EXITOSA."),
    (409, "CONFIGURACION_NO_PUBLICADA", "La configuracion es un BORRADOR."),
    (409, "RUTEO_NO_EXITOSO", "El ruteo/v2 o su plan de campo no estan EXITOSA."),
    (409, "OPTIMIZACION_EN_PROCESO", "Ese mismo plan (la misma firma) ya se esta calculando."),
    (409, "OPTIMIZACION_YA_PUBLICADA", "Ese mismo plan (la misma firma) ya se publico."),
    (422, "CORTES_DISTINTOS", "El scoring y la decision/v2 no son del mismo corte."),
    (422, "RUTEO_REQUERIDO", "La configuracion usa CAMPO o BASE y no se declaro un ruteo/v2."),
    (422, "RUTEO_DE_OTRA_DECISION", "El ruteo/v2 es de un plan de campo de otra decision/v2."),
    (422, "FECHA_OPERACION_DISTINTA", "La fecha de operacion no es la del ruteo/v2."),
    (422, "FECHA_ANTERIOR_AL_CORTE", "La fecha de operacion es anterior al corte."),
    (422, "CONFIGURACION_NO_SOPORTADA", "La configuracion no es de esta forma canonica."),
    (422, "POLITICA_NO_SOPORTADA", "Este servicio no optimiza con esa politica."),
    (422, "ENTRADA_INVALIDA", "Falta un campo, o no tiene su forma."),
)


# --- la politica ----------------------------------------------------------------------------------


@router.get(
    "/optimizacion/politica",
    response_model=PoliticaOptimizacionRespuesta,
    summary="optimization-public-v1: el objetivo, su semantica, el solver y sus limitaciones",
    responses=errores(*SIN_CLAVE),
)
def politica() -> PoliticaOptimizacionRespuesta:
    """La formula del valor de oportunidad (la recuperacion positiva esperada de v0.15 en
    centavos, sin multiplicarla por ninguna probabilidad), los cuatro objetivos lexicograficos, la
    precision, los canales, la reduccion exacta por dominancia, la regla de publicacion (solo un
    optimo demostrado) y la huella del solver de este servicio. No es causal."""
    huella = solver_.huella(PUBLIC_V1)
    return PoliticaOptimizacionRespuesta(
        version=PUBLIC_V1.version,
        version_motor=VERSION_MOTOR,
        hash=hash_politica(PUBLIC_V1),
        algoritmo=ALGORITMO,
        definicion=PUBLIC_V1.definicion(),
        huella_solver=huella,
        solver_fingerprint=solver_.hash_huella(huella),
    )


# --- las configuraciones --------------------------------------------------------------------------


@router.post(
    "/optimizacion/configuraciones",
    status_code=201,
    response_model=ConfiguracionOptimizacionDetalle,
    summary="Registra una configuracion (BORRADOR, o PUBLICADA con publicar=true), o reutiliza "
    "la que ya existe",
    responses={
        200: {
            "model": ConfiguracionOptimizacionDetalle,
            "description": "Ese mismo contenido ya estaba registrado: se devuelve su version, con "
            "`reutilizada: true` (y, con publicar=true, publicada si era un BORRADOR).",
        },
        201: {"description": "Se registro una version nueva. `Location` apunta a ella."},
        **errores(
            *SIN_CLAVE,
            (
                422,
                "CONFIGURACION_INVALIDA",
                "Un canal, un alcance o una dimension fuera de su vocabulario, un pool de CAMPO "
                "que no es de un gestor, una capacidad o unas unidades que no son enteros "
                "positivos, un codigo repetido, un flag sin declarar.",
            ),
            (422, "ENTRADA_INVALIDA", "El documento no tiene la forma de una configuracion."),
        ),
    },
)
def registrar_configuracion(
    entrada: ConfiguracionOptimizacionEntrada,
    response: Response,
    s: Sesion,
    publicar: Annotated[bool, Query(description="Publicarla al registrarla.")] = False,
) -> ConfiguracionOptimizacionDetalle:
    """Se valida todo antes de escribir nada: un solo problema rechaza el documento entero, con
    todos sus problemas. Las capacidades de un benchmark son SUPUESTOS SINTETICOS: dilo en
    `supuestos`. Una PUBLICADA ya no cambia; cambiar una capacidad es otra version."""
    try:
        contenido = configuracion_.leer(entrada.model_dump(mode="json", exclude_unset=True))
        registrada = configuracion_.registrar(s, contenido, publicar=publicar)
    except configuracion_.ConfiguracionInvalida as exc:
        error = ErrorDeApi(422, "CONFIGURACION_INVALIDA", str(exc))
        error.respuesta.detalles = [DetalleError(problema=p) for p in exc.problemas]
        raise error from exc
    s.commit()
    version = registrada.version
    s.refresh(version)
    if registrada.creada:
        response.headers["Location"] = f"/optimizacion/configuraciones/{version.configuracion_id}"
    else:
        response.status_code = 200
    return _detalle(s, version, reutilizada=not registrada.creada)


@router.post(
    "/optimizacion/configuraciones/{configuracion_id}/publicacion",
    response_model=ConfiguracionOptimizacionDetalle,
    summary="Publica una configuracion BORRADOR; una PUBLICADA se queda como esta",
    responses=errores(*SIN_CLAVE, CONFIGURACION_NO_EXISTE, UUID_INVALIDO),
)
def publicar_configuracion(configuracion_id: UUID, s: Sesion) -> ConfiguracionOptimizacionDetalle:
    """Publicar no cambia su contenido ni su hash: solo la deja usar para planear, ya sin
    cambios."""
    try:
        registrada = configuracion_.publicar(s, configuracion_id)
    except configuracion_.ConfiguracionNoEncontrada as exc:
        raise ErrorDeApi(*CONFIGURACION_NO_EXISTE[:2], f"No existe {configuracion_id}.") from exc
    s.commit()
    s.refresh(registrada.version)
    return _detalle(s, registrada.version, reutilizada=False)


@router.get(
    "/optimizacion/configuraciones",
    response_model=PaginaConfiguracionesOptimizacion,
    summary="Las configuraciones, la mas reciente primero",
    responses=errores(*SIN_CLAVE, INVALIDA),
)
def configuraciones(
    parametros: Annotated[ParametrosConfiguraciones, Query()], s: SesionDeLectura
) -> PaginaConfiguracionesOptimizacion:
    """Por `estado` (BORRADOR o PUBLICADA), con sus conteos de pools y restricciones, su hash y
    sus supuestos. El detalle de una (su matriz, sus pools y sus restricciones) esta en
    `GET /optimizacion/configuraciones/{configuracion_id}`."""
    total, filas = consultas.listar_configuraciones(
        s,
        estado=None if parametros.estado is None else parametros.estado.value,
        desplazamiento=parametros.desplazamiento,
        limite=parametros.por_pagina,
    )
    return PaginaConfiguracionesOptimizacion(
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[_resumen(v) for v in filas],
    )


@router.get(
    "/optimizacion/configuraciones/{configuracion_id}",
    response_model=ConfiguracionOptimizacionDetalle,
    summary="Una configuracion: su matriz de admisibilidad, sus pools y sus restricciones",
    responses=errores(*SIN_CLAVE, CONFIGURACION_NO_EXISTE, UUID_INVALIDO),
)
def configuracion(configuracion_id: UUID, s: SesionDeLectura) -> ConfiguracionOptimizacionDetalle:
    """`hash_verificado` dice si su hash, vuelto a calcular con lo que hay en sus tablas, es el que
    se guardo al registrarla."""
    try:
        vista = consultas.obtener_configuracion(s, configuracion_id)
    except consultas.NoEncontrado as exc:
        raise ErrorDeApi(*CONFIGURACION_NO_EXISTE[:2], f"No existe {configuracion_id}.") from exc
    return _detalle(s, vista.version, reutilizada=False)


def _resumen(v: VersionConfiguracionOptimizacion) -> ConfiguracionOptimizacionRespuesta:
    return ConfiguracionOptimizacionRespuesta(
        configuracion_id=v.configuracion_id,
        nombre=v.nombre,
        version_configuracion=v.version_configuracion,
        hash_contenido=v.hash_contenido,
        estado=v.estado,
        permitir_predicciones_con_imputacion=v.permitir_predicciones_con_imputacion,
        max_cuentas_atendidas=v.max_cuentas_atendidas,
        max_unidades_recurso=v.max_unidades_recurso,
        pools=v.pools,
        pools_activos=v.pools_activos,
        restricciones=v.restricciones,
        restricciones_activas=v.restricciones_activas,
        metadatos=v.metadatos,
        creada_en=v.creada_en,
        publicada_en=v.publicada_en,
    )


def _detalle(
    s: Session, v: VersionConfiguracionOptimizacion, *, reutilizada: bool
) -> ConfiguracionOptimizacionDetalle:
    contenido = configuracion_.configuracion_de(s, v)
    return ConfiguracionOptimizacionDetalle(
        **_resumen(v).model_dump(),
        admisibilidad=[
            AdmisibilidadEntrada(
                canal_decision=a.canal_decision,
                canal_operativo=a.canal_operativo,
                permitido=a.permitido,
            )
            for a in contenido.admisibilidad
        ],
        detalle_pools=[PoolEntrada(**p.canonica()) for p in contenido.pools],
        detalle_restricciones=[RestriccionEntrada(**r.canonica()) for r in contenido.restricciones],
        hash_verificado=contenido.hash() == v.hash_contenido,
        reutilizada=reutilizada,
        supuestos=contenido.supuestos,
    )


# --- las ejecuciones ------------------------------------------------------------------------------


@router.post(
    "/optimizacion/ejecuciones",
    status_code=201,
    response_model=EjecucionOptimizacionRespuesta,
    summary="Pide el plan de una fecha con entradas declaradas exactas",
    responses={
        201: {
            "description": "La ejecucion quedo EN_PROCESO con su trabajo OPTIMIZATION en la cola "
            "durable: un worker resuelve el MILP. `Location` apunta a ella."
        },
        **errores(*SIN_CLAVE, *ERRORES_DE_LA_EJECUCION),
    },
)
def pedir_ejecucion(
    entrada: EjecucionOptimizacionEntrada, request: Request, response: Response, s: Sesion
) -> EjecucionOptimizacionRespuesta:
    """**201 quiere decir que la ejecucion se creo, no que termino.** Se comprueba todo antes de
    registrar nada: el scoring y la decision/v2 del mismo corte, la configuracion PUBLICADA y, con
    CAMPO o BASE, el ruteo/v2 de esa misma decision y de la misma fecha. Las mismas entradas con la
    misma huella del solver son el mismo plan: si ya se publico, `409 OPTIMIZACION_YA_PUBLICADA`."""
    config: Config = request.app.state.config
    try:
        ejecucion = ejecuciones.abrir(
            s,
            scoring_run_id=entrada.scoring_run_id,
            decision_run_id=entrada.decision_run_id,
            configuracion_id=entrada.configuracion_id,
            fecha_operacion=entrada.fecha_operacion,
            ruteo_run_id=entrada.ruteo_run_id,
            max_intentos=config.worker_max_intentos,
        )
    except ejecuciones.OptimizacionInvalida as exc:
        raise ErrorDeApi(exc.estado_http, exc.codigo, str(exc)) from exc
    except ejecuciones.OptimizacionEnProceso as exc:
        raise ErrorDeApi(409, "OPTIMIZACION_EN_PROCESO", str(exc)) from exc
    except ejecuciones.OptimizacionYaPublicada as exc:
        raise ErrorDeApi(409, "OPTIMIZACION_YA_PUBLICADA", str(exc)) from exc
    s.commit()
    response.headers["Location"] = f"/optimizacion/ejecuciones/{ejecucion.optimizacion_run_id}"
    return ejecucion_respuesta(consultas.obtener_ejecucion(s, ejecucion.optimizacion_run_id))


@router.get(
    "/optimizacion/ejecuciones",
    response_model=PaginaEjecucionesOptimizacion,
    summary="Las ejecuciones, la mas reciente primero",
    responses=errores(
        *SIN_CLAVE,
        CONFIGURACION_NO_EXISTE,
        (404, "SCORING_NO_ENCONTRADO", "No existe ese scoring."),
        INVALIDA,
    ),
)
def ejecuciones_(
    parametros: Annotated[ParametrosEjecuciones, Query()], s: SesionDeLectura
) -> PaginaEjecucionesOptimizacion:
    """Por `estado`, por configuracion y por scoring: una configuracion o un scoring que no
    existen son un 404, no una lista vacia."""
    version = scoring = None
    if parametros.configuracion_id is not None:
        version = s.exec(
            select(VersionConfiguracionOptimizacion).where(
                VersionConfiguracionOptimizacion.configuracion_id == parametros.configuracion_id
            )
        ).one_or_none()
        if version is None:
            raise ErrorDeApi(
                *CONFIGURACION_NO_EXISTE[:2], f"No existe {parametros.configuracion_id}."
            )
    if parametros.scoring_run_id is not None:
        scoring = s.exec(
            select(EjecucionScoringPredictivo).where(
                EjecucionScoringPredictivo.scoring_run_id == parametros.scoring_run_id
            )
        ).one_or_none()
        if scoring is None:
            raise ErrorDeApi(
                404, "SCORING_NO_ENCONTRADO", f"No existe el scoring {parametros.scoring_run_id}."
            )
    total, filas = consultas.listar_ejecuciones(
        s,
        estado=None if parametros.estado is None else parametros.estado.value,
        configuracion=version,
        scoring=scoring,
        desplazamiento=parametros.desplazamiento,
        limite=parametros.por_pagina,
    )
    return PaginaEjecucionesOptimizacion(
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[ejecucion_respuesta(v) for v in filas],
    )


@router.get(
    "/optimizacion/ejecuciones/{optimizacion_run_id}",
    response_model=EjecucionOptimizacionRespuesta,
    summary="Una ejecucion: sus entradas, sus hashes, su certificado y su resumen",
    responses=errores(*SIN_CLAVE, EJECUCION_NO_EXISTE, UUID_INVALIDO),
)
def ejecucion(optimizacion_run_id: UUID, s: SesionDeLectura) -> EjecucionOptimizacionRespuesta:
    """Mientras el estado sea `EN_PROCESO`, un worker no la ha terminado. Una `EXITOSA` trae su
    plan resumido (por estado, por motivo, por canal), sus restricciones activas, el drift del
    scoring declarado y su certificado: cada nivel lexicografico con su estado, su objetivo, su
    cota y su gap."""
    return ejecucion_respuesta(_ejecucion(s, optimizacion_run_id))


@router.get(
    "/optimizacion/ejecuciones/{optimizacion_run_id}/resultados",
    response_model=PaginaResultadosOptimizacion,
    summary="Los resultados por cuenta de un plan, filtrados en la base, por pagina",
    responses=errores(
        *SIN_CLAVE,
        EJECUCION_NO_EXISTE,
        NO_PUBLICADA,
        (404, "FILTRO_DESCONOCIDO", "El pool o la base no existen en la ejecucion."),
        INVALIDA,
    ),
)
def resultados(
    optimizacion_run_id: UUID,
    parametros: Annotated[ParametrosResultados, Query()],
    s: SesionDeLectura,
) -> PaginaResultadosOptimizacion:
    """Filtros de una lista blanca: `estado`, `canal` (el asignado), `pool`, `producto`,
    `prioridad` y `base`. Un motivo es un hecho: una cuenta NO_SELECCIONADA esta
    FUERA_DE_SOLUCION_OPTIMA, sin atribuirlo a una restriccion."""
    vista = _publicada(s, optimizacion_run_id)
    try:
        total, filas = consultas.resultados(
            s,
            vista.ejecucion,
            consultas.FiltrosResultados(
                estado=parametros.estado,
                canal=parametros.canal,
                pool=parametros.pool,
                producto=parametros.producto,
                prioridad=parametros.prioridad,
                base=parametros.base,
            ),
            desplazamiento=parametros.desplazamiento,
            limite=parametros.por_pagina,
        )
    except consultas.FiltroDesconocido as exc:
        raise ErrorDeApi(404, "FILTRO_DESCONOCIDO", str(exc)) from exc
    return PaginaResultadosOptimizacion(
        optimizacion_run_id=optimizacion_run_id,
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[ResultadoCuentaRespuesta(**vars(f)) for f in filas],
    )


@router.get(
    "/optimizacion/ejecuciones/{optimizacion_run_id}/capacidad",
    response_model=PaginaCapacidadOptimizacion,
    summary="El uso de cada pool de un plan: capacidad, uso, holgura y unidades",
    responses=errores(*SIN_CLAVE, EJECUCION_NO_EXISTE, NO_PUBLICADA, INVALIDA),
)
def capacidad(
    optimizacion_run_id: UUID,
    parametros: Annotated[ParametrosCapacidad, Query()],
    s: SesionDeLectura,
) -> PaginaCapacidadOptimizacion:
    """Cada pool activo, por codigo, con su capacidad declarada y la efectiva (en CAMPO, la menor
    de la declarada y las paradas PROGRAMADA de su gestor), cuantas cuentas eran elegibles y
    cuantas entraron al modelo, la utilizada, la holgura y las unidades consumidas; y el agregado
    por canal. Las unidades son esfuerzo abstracto, no dinero."""
    vista = _publicada(s, optimizacion_run_id)
    total, filas = consultas.capacidad(
        s,
        vista.ejecucion,
        canal=parametros.canal,
        desplazamiento=parametros.desplazamiento,
        limite=parametros.por_pagina,
    )
    return PaginaCapacidadOptimizacion(
        optimizacion_run_id=optimizacion_run_id,
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        por_canal=(vista.ejecucion.resumen or {}).get("por_canal"),
        elementos=[
            UsoPoolRespuesta(
                pool=u.pool,
                canal=u.canal,
                tipo_alcance=u.tipo_alcance,
                referencia_alcance=u.referencia_alcance,
                capacidad_declarada=u.capacidad_declarada,
                capacidad=u.capacidad_efectiva,
                elegibles=u.elegibles,
                retenidas=u.retenidas,
                utilizada=u.utilizada,
                holgura=u.holgura,
                utilizacion=u.utilizacion,
                unidades_por_asignacion=u.unidades_por_asignacion,
                unidades_consumidas=u.unidades_consumidas,
            )
            for u in filas
        ],
    )


@router.get(
    "/optimizacion/ejecuciones/{optimizacion_run_id}/restricciones",
    response_model=PaginaRestriccionesOptimizacion,
    summary="Cada restriccion de un plan: lado izquierdo, derecho, holgura y si quedo activa",
    responses=errores(*SIN_CLAVE, EJECUCION_NO_EXISTE, NO_PUBLICADA, INVALIDA),
)
def restricciones(
    optimizacion_run_id: UUID,
    parametros: Annotated[ParametrosRestricciones, Query()],
    s: SesionDeLectura,
) -> PaginaRestriccionesOptimizacion:
    """Activa (`binding`) es holgura cero, en enteros, en la forma declarada de la restriccion:
    una participacion se lee en su forma lineal (10,000 por las del grupo contra los puntos base
    por el total), con su limite resuelto a cuentas."""
    vista = _publicada(s, optimizacion_run_id)
    total, filas = consultas.restricciones(
        s,
        vista.ejecucion,
        binding=parametros.binding,
        tipo=parametros.tipo,
        desplazamiento=parametros.desplazamiento,
        limite=parametros.por_pagina,
    )
    return PaginaRestriccionesOptimizacion(
        optimizacion_run_id=optimizacion_run_id,
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[
            RestriccionResultadoRespuesta(
                codigo=r.codigo,
                tipo=r.tipo,
                dimension=r.dimension,
                valor_dimension=r.valor_dimension,
                operador=r.operador,
                lhs=r.lhs,
                rhs=r.rhs,
                holgura=r.holgura,
                binding=r.binding,
                cuentas_grupo=r.cuentas_grupo,
                total=r.total,
                limite_resuelto=r.limite_resuelto,
            )
            for r in filas
        ],
    )


@router.get(
    "/optimizacion/ejecuciones/{optimizacion_run_id}/diagnostico",
    response_model=DiagnosticoRespuesta,
    summary="Por que una ejecucion termino como termino, sin inventar un plan",
    responses=errores(
        *SIN_CLAVE,
        EJECUCION_NO_EXISTE,
        (409, "OPTIMIZACION_EN_PROCESO", "La ejecucion todavia no termina."),
        UUID_INVALIDO,
    ),
)
def diagnostico(optimizacion_run_id: UUID, s: SesionDeLectura) -> DiagnosticoRespuesta:
    """Una INFACTIBLE trae su prechequeo y, si el solver lo dijo, su diagnostico de relajacion
    (una relajacion minima posible, no un IIS); una OPTIMO_NO_DEMOSTRADO, lo que el solver alcanzo
    sin publicarlo; una EXITOSA, sus advertencias."""
    vista = _ejecucion(s, optimizacion_run_id)
    e = vista.ejecucion
    if e.estado == EstadoOptimizacion.EN_PROCESO:
        raise ErrorDeApi(
            409, "OPTIMIZACION_EN_PROCESO", f"La ejecucion {optimizacion_run_id} no ha terminado."
        )
    return DiagnosticoRespuesta(
        optimizacion_run_id=optimizacion_run_id,
        estado=e.estado,
        resultado=e.resultado,
        solver_status=e.solver_status,
        detalle=e.detalle,
        diagnostico=e.diagnostico,
        certificado=e.certificado,
        plan_publicado=e.estado == EstadoOptimizacion.EXITOSA,
    )


@router.get(
    "/optimizacion/ejecuciones/{optimizacion_run_id}/cuentas/{cuenta_id}",
    response_model=ExplicacionCuentaRespuesta,
    summary="Por que una cuenta quedo como quedo en un plan: lo publicado y sus hechos",
    responses=errores(
        *SIN_CLAVE,
        EJECUCION_NO_EXISTE,
        NO_PUBLICADA,
        (404, "CUENTA_NO_ENCONTRADA", "No existe esa cuenta."),
        (404, "SIN_RESULTADO", "El plan no tiene un resultado de esa cuenta."),
        UUID_INVALIDO,
    ),
)
def explicar(
    optimizacion_run_id: UUID, cuenta_id: UUID, s: SesionDeLectura
) -> ExplicacionCuentaRespuesta:
    """Su estado, su canal y su pool si quedo asignada, su valor de oportunidad, su p_contacto
    (solo un desempate), sus canales admisibles, los pools que la alcanzaban y los hechos de su
    elegibilidad. Nunca "este canal maximiza su probabilidad de pago"."""
    vista = _publicada(s, optimizacion_run_id)
    cuenta = _cuenta(s, cuenta_id)
    explicacion = consultas.explicar(s, vista, id_de(cuenta))
    if explicacion is None:
        raise ErrorDeApi(404, "SIN_RESULTADO", f"El plan no tiene un resultado de {cuenta_id}.")
    return ExplicacionCuentaRespuesta(
        optimizacion_run_id=optimizacion_run_id,
        cuenta_id=cuenta_id,
        resultado=ResultadoCuentaRespuesta(**vars(explicacion.resultado)),
        canales_admisibles=explicacion.canales_admisibles,
        pools_elegibles=explicacion.pools_elegibles,
        hechos=explicacion.hechos,
        grupos=explicacion.grupos,
        explicacion=explicacion.explicacion,
    )


@router.get(
    "/cuentas/{cuenta_id}/optimizacion",
    response_model=PaginaPlanesDeCuenta,
    summary="Lo que cada plan publicado concluyo de una cuenta, del mas reciente al mas antiguo",
    responses=errores(*SIN_CLAVE, (404, "CUENTA_NO_ENCONTRADA", "No existe esa cuenta."), INVALIDA),
)
def planes_de_cuenta(
    cuenta_id: UUID, parametros: Annotated[Paginacion, Query()], s: SesionDeLectura
) -> PaginaPlanesDeCuenta:
    """Su estado, su motivo, su canal y su pool en cada plan EXITOSA que la incluye, con la
    configuracion y la fecha de operacion de ese plan. Un plan es de soporte a decision, en
    sombra: no dice que se haya hecho nada con la cuenta."""
    cuenta = _cuenta(s, cuenta_id)
    total, filas = consultas.planes_de_cuenta(
        s, id_de(cuenta), desplazamiento=parametros.desplazamiento, limite=parametros.por_pagina
    )
    return PaginaPlanesDeCuenta(
        cuenta_id=cuenta_id,
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[PlanDeCuentaRespuesta(**vars(f)) for f in filas],
    )


# --- las respuestas -------------------------------------------------------------------------------


def _cuenta(s: Session, cuenta_id: UUID) -> CuentaCanonica:
    cuenta = s.exec(select(CuentaCanonica).where(CuentaCanonica.cuenta_id == cuenta_id)).first()
    if cuenta is None:
        raise ErrorDeApi(404, "CUENTA_NO_ENCONTRADA", f"No existe la cuenta {cuenta_id}.")
    return cuenta


def _ejecucion(s: Session, optimizacion_run_id: UUID) -> consultas.EjecucionVista:
    try:
        return consultas.obtener_ejecucion(s, optimizacion_run_id)
    except consultas.NoEncontrado as exc:
        raise ErrorDeApi(
            *EJECUCION_NO_EXISTE[:2], f"No existe la ejecucion {optimizacion_run_id}."
        ) from exc


def _publicada(s: Session, optimizacion_run_id: UUID) -> consultas.EjecucionVista:
    vista = _ejecucion(s, optimizacion_run_id)
    if vista.ejecucion.estado != EstadoOptimizacion.EXITOSA:
        raise ErrorDeApi(
            409,
            "OPTIMIZACION_NO_PUBLICADA",
            f"La ejecucion {optimizacion_run_id} esta {vista.ejecucion.estado}"
            + (f" ({vista.ejecucion.resultado})" if vista.ejecucion.resultado else "")
            + ": no publico un plan. Ver su diagnostico.",
        )
    return vista


def ejecucion_respuesta(v: consultas.EjecucionVista) -> EjecucionOptimizacionRespuesta:
    e = v.ejecucion
    return EjecucionOptimizacionRespuesta(
        optimizacion_run_id=e.optimizacion_run_id,
        estado=e.estado,
        resultado=e.resultado,
        scoring_run_id=v.scoring_run_id,
        modelo_version_id=v.modelo_version_id,
        decision_run_id=v.decision_run_id,
        ruteo_run_id=v.ruteo_run_id,
        campo_run_id=v.campo_run_id,
        configuracion_id=v.configuracion_id,
        corte_id=v.corte_id,
        fecha_corte=v.fecha_corte,
        fecha_operacion=e.fecha_operacion,
        version_motor=e.version_motor,
        version_politica=e.version_politica,
        hash_politica=e.hash_politica,
        hash_configuracion=e.hash_configuracion,
        solver_fingerprint=e.solver_fingerprint,
        huella_solver=e.huella_solver,
        firma_inputs=e.firma_inputs,
        cuentas_universo=e.cuentas_universo,
        cuentas_elegibles=e.cuentas_elegibles,
        cuentas_no_elegibles=e.cuentas_universo - e.cuentas_elegibles
        if e.estado == EstadoOptimizacion.EXITOSA
        else 0,
        cuentas_con_imputacion=e.cuentas_con_imputacion,
        cuentas_retenidas=e.cuentas_retenidas,
        cuentas_asignadas=e.cuentas_asignadas,
        cuentas_publicadas=e.cuentas_publicadas,
        clases=e.clases,
        variables=e.variables,
        restricciones_solver=e.restricciones_solver,
        elementos_no_nulos=e.elementos_no_nulos,
        objetivo_primario_centavos=e.objetivo_primario_centavos,
        objetivo_secundario=e.objetivo_secundario,
        unidades_recurso=e.unidades_recurso,
        desempate_canonico=e.desempate_canonico,
        solver_status=e.solver_status,
        best_bound=e.best_bound,
        mip_gap=e.mip_gap,
        solver_seconds=e.solver_seconds,
        hash_plan=e.hash_plan,
        certificado=e.certificado,
        resumen=e.resumen,
        tiempos=e.tiempos,
        trabajo_id=v.trabajo_id,
        iniciada_en=e.iniciada_en,
        terminada_en=e.terminada_en,
        duracion_segundos=None
        if e.terminada_en is None
        else (e.terminada_en - e.iniciada_en).total_seconds(),
        detalle=e.detalle,
    )
