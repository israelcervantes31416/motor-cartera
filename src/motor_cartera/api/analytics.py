"""analytics/v1 por HTTP: el catalogo, pedir una ejecucion con su manifiesto explicito, leerla, y
leer sus agregados, su resumen, sus embudos y sus controles.

La API no analiza: el POST resuelve y comprueba el manifiesto, fija known_at (en OPERATIVO sin
declararlo, el reloj de la base, una vez) y deja la ejecucion EN_PROCESO con su trabajo ANALYTICS
en la cola durable; la ejecuta un worker. Lo publicado es agregado: no hay analytics por cuenta.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from sqlmodel import Session, select

from motor_cartera.analytics import consultas, manifiesto
from motor_cartera.analytics import ejecuciones as motor
from motor_cartera.analytics.consultas import EjecucionVista, FiltrosAgregados
from motor_cartera.api.dependencias import Sesion, SesionDeLectura
from motor_cartera.api.errores import ErrorDeApi, errores
from motor_cartera.api.esquemas_analytics import (
    AgregadoAnalyticsRespuesta,
    CatalogoAnalyticsRespuesta,
    ControlAnalyticsRespuesta,
    ControlesAnalyticsRespuesta,
    EjecucionAnalyticsEntrada,
    EjecucionAnalyticsRespuesta,
    EmbudoAnalyticsRespuesta,
    EmbudosAnalyticsRespuesta,
    EntradaDeAnalyticsRespuesta,
    EtapaEmbudoAnalytics,
    IndicadorAnalyticsRespuesta,
    PaginaAgregadosAnalytics,
    PaginaEjecucionesAnalytics,
    ParametrosAgregadosAnalytics,
    ParametrosEjecucionesAnalytics,
    ResumenAnalyticsRespuesta,
)
from motor_cartera.api.operacional import SIN_CLAVE
from motor_cartera.config import Config
from motor_cartera.db.modelos import CorteCanonico, EstadoAnalytics

router = APIRouter(tags=["analytics"])

NO_EXISTE = (404, "ANALYTICS_NO_ENCONTRADA", "No existe una ejecucion de analytics con ese id.")
NO_PUBLICADA = (
    409,
    "ANALYTICS_NO_PUBLICADA",
    "La ejecucion no esta EXITOSA: no publico agregados (EN_PROCESO, o FALLIDA sin publicar).",
)
INVALIDA = (422, "ENTRADA_INVALIDA", "Un filtro no es valido, o la paginacion no lo es.")
ERRORES_DEL_MANIFIESTO = (
    (404, "CORTE_NO_ENCONTRADO", "No existe ese corte canonico."),
    (404, "ENTRADA_NO_ENCONTRADA", "No existe una de las entradas declaradas."),
    (409, "ANALYTICS_EN_PROCESO", "Esas mismas entradas ya se estan analizando."),
    (409, "ANALYTICS_YA_GENERADA", "Esas mismas entradas (la misma firma) ya se analizaron."),
    (422, "AS_OF_ANTERIOR_AL_CORTE", "as_of es anterior a la fecha del corte."),
    (422, "AS_OF_FUTURO", "as_of es posterior al dia de known_at en la zona."),
    (422, "ZONA_HORARIA_INVALIDA", "La zona no es una zona IANA que conozca la base."),
    (422, "KNOWN_AT_REQUERIDO", "Un BACKTEST declara su known_at."),
    (422, "KNOWN_AT_SIN_ZONA", "known_at no lleva su zona horaria."),
    (422, "KNOWN_AT_FUTURO", "known_at es posterior al reloj de la base."),
    (422, "CORTE_NO_CONOCIDO", "El corte se materializo despues de known_at."),
    (422, "HORIZONTE_DEMASIADO_LARGO", "El horizonte tiene mas periodos que los que admite."),
    (422, "CATALOGO_NO_SOPORTADO", "Este servicio no analiza con esa version del catalogo."),
    (422, "POLITICA_NO_SOPORTADA", "Este servicio no aplica esa version de la politica."),
    (422, "ENTRADA_NO_EXITOSA", "Una entrada declarada no termino EXITOSA."),
    (422, "ENTRADA_NO_CONOCIDA", "Una entrada declarada no existia a known_at."),
    (422, "VERSION_DE_ENTRADA_NO_SOPORTADA", "Una entrada es de una version que no se lee."),
    (422, "CARTERA_INCONSISTENTE", "Una entrada es de otra cartera."),
    (422, "CORTE_INCONSISTENTE", "Una entrada es de otro corte."),
    (422, "VENTANA_FUERA_DEL_HORIZONTE", "Una interpretacion de pagos no toca el horizonte."),
    (422, "VENTANA_DUPLICADA", "Una ventana tiene dos interpretaciones o dos atribuciones."),
    (422, "ATRIBUCION_INCONSISTENTE", "Una atribucion no leyo la interpretacion declarada."),
    (422, "EVALUACION_INCONSISTENTE", "La evaluacion de promesas es de otro as_of o zona."),
    (422, "ENCADENAMIENTO_INCORRECTO", "Una entrada no es de las que declara el manifiesto."),
    (
        422,
        "FECHA_OPERACION_FUERA_DEL_HORIZONTE",
        "El plan de campo/v2 es de una fecha fuera del horizonte.",
    ),
    (422, "ENTRADA_REPETIDA", "Una entrada esta declarada dos veces."),
    (422, "ENTRADA_INVALIDA", "Falta un campo, o no tiene su forma."),
)


# --- el catalogo ----------------------------------------------------------------------------------


@router.get(
    "/analytics/catalogo",
    response_model=CatalogoAnalyticsRespuesta,
    summary="El catalogo versionado de metricas, con su hash, su politica y sus conjuntos",
    responses=errores(*SIN_CLAVE),
)
def catalogo() -> CatalogoAnalyticsRespuesta:
    """Cada metrica con su definicion formal: que calcula, de que entradas, sobre que poblacion,
    en que ventana, cuando no esta disponible, por que dimensiones se desglosa y sus limitaciones.
    El catalogo es codigo versionado: cambiar una formula cambia su hash."""
    datos = consultas.catalogo()
    return CatalogoAnalyticsRespuesta(
        version=datos["version"],
        hash=datos["hash"],
        version_motor=datos["version_motor"],
        politicas=datos["politicas"],
        conjuntos=datos["conjuntos"],
        metricas=[
            {
                **{k: v for k, v in m.items() if k not in ("fuentes", "conjuntos", "componentes")},
                "fuentes_requeridas": m["fuentes"],
            }
            for m in datos["metricas"]
        ],
    )


# --- las ejecuciones ------------------------------------------------------------------------------


@router.post(
    "/analytics/ejecuciones",
    status_code=201,
    response_model=EjecucionAnalyticsRespuesta,
    summary="Pide el analisis de una cohorte con un manifiesto de entradas explicito",
    responses={
        201: {
            "description": "La ejecucion quedo EN_PROCESO, con su trabajo en la cola durable: un "
            "worker la analiza. `Location` apunta a ella."
        },
        **errores(*SIN_CLAVE, *ERRORES_DEL_MANIFIESTO),
    },
)
def pedir_ejecucion(
    entrada: EjecucionAnalyticsEntrada, request: Request, response: Response, s: Sesion
) -> EjecucionAnalyticsRespuesta:
    """La cohorte es el corte declarado; cada otra entrada se declara por su identificador
    publico y se comprueba antes de registrar nada: que existe, termino EXITOSA, es de la cartera
    y del corte, existia a known_at, y encadena con las demas. Nada se elige: lo que no se declara
    no se lee, y sus metricas se publican NO_DISPONIBLE.

    **201 quiere decir que la ejecucion se creo, no que termino.** Las mismas entradas son la misma
    ejecucion: si ya se publico, `409 ANALYTICS_YA_GENERADA`."""
    config: Config = request.app.state.config
    peticion = manifiesto.Peticion(
        corte_id=entrada.corte_id,
        as_of=entrada.as_of,
        modo=entrada.modo,
        zona_horaria=entrada.zona_horaria,
        granularidad=entrada.granularidad,
        known_at=entrada.known_at,
        motor_pagos=tuple(entrada.motor_pagos_run_ids),
        atribucion=tuple(entrada.atribucion_run_ids),
        evaluacion_promesas=entrada.evaluacion_run_id,
        decision_v2=entrada.decision_run_id,
        geografia=entrada.geografia_run_id,
        territorial_v2=entrada.territorial_run_id,
        campo_v2=entrada.campo_run_id,
        ruteo_v2=entrada.ruteo_run_id,
        version_catalogo=entrada.version_catalogo,
        version_politica=entrada.version_politica,
    )
    try:
        ejecucion = motor.abrir(s, peticion, max_intentos=config.worker_max_intentos)
    except manifiesto.EntradaNoAnalizable as exc:
        raise ErrorDeApi(exc.estado_http, exc.codigo, str(exc)) from exc
    except motor.AnalyticsEnProceso as exc:
        raise ErrorDeApi(409, "ANALYTICS_EN_PROCESO", str(exc)) from exc
    except motor.AnalyticsYaGenerada as exc:
        raise ErrorDeApi(409, "ANALYTICS_YA_GENERADA", str(exc)) from exc
    s.commit()
    response.headers["Location"] = f"/analytics/ejecuciones/{ejecucion.analytics_run_id}"
    return ejecucion_respuesta(consultas.obtener(s, ejecucion.analytics_run_id))


@router.get(
    "/analytics/ejecuciones",
    response_model=PaginaEjecucionesAnalytics,
    summary="Las ejecuciones de analytics, la mas reciente primero",
    responses=errores(
        *SIN_CLAVE, (404, "CORTE_NO_ENCONTRADO", "No existe ese corte canonico."), INVALIDA
    ),
)
def ejecuciones(
    parametros: Annotated[ParametrosEjecucionesAnalytics, Query()], s: SesionDeLectura
) -> PaginaEjecucionesAnalytics:
    """Con `corte_id`, `estado` o `as_of`, las de ese corte, ese estado o ese dia."""
    corte = None
    if parametros.corte_id is not None:
        corte = s.exec(
            select(CorteCanonico).where(CorteCanonico.corte_id == parametros.corte_id)
        ).one_or_none()
        if corte is None:
            raise ErrorDeApi(
                404,
                "CORTE_NO_ENCONTRADO",
                f"No existe un corte canonico con corte_id {parametros.corte_id}.",
            )
    total, filas = consultas.listar(
        s,
        corte=corte,
        estado=None if parametros.estado is None else parametros.estado.value,
        as_of=parametros.as_of,
        desplazamiento=parametros.desplazamiento,
        limite=parametros.por_pagina,
    )
    return PaginaEjecucionesAnalytics(
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[ejecucion_respuesta(v) for v in filas],
    )


@router.get(
    "/analytics/ejecuciones/{analytics_run_id}",
    response_model=EjecucionAnalyticsRespuesta,
    summary="Una ejecucion: su manifiesto, sus relojes, sus versiones, sus hashes y su "
    "disponibilidad",
    responses=errores(*SIN_CLAVE, NO_EXISTE, (422, "ENTRADA_INVALIDA", "No es un UUID.")),
)
def obtener(analytics_run_id: UUID, s: SesionDeLectura) -> EjecucionAnalyticsRespuesta:
    """Mientras el estado sea `EN_PROCESO`, un worker no la ha terminado."""
    return ejecucion_respuesta(_vista(s, analytics_run_id))


@router.get(
    "/analytics/ejecuciones/{analytics_run_id}/agregados",
    response_model=PaginaAgregadosAnalytics,
    summary="Los agregados publicados, filtrados en la base y en el orden de su llave",
    responses=errores(*SIN_CLAVE, NO_EXISTE, NO_PUBLICADA, INVALIDA),
)
def agregados(
    analytics_run_id: UUID,
    parametros: Annotated[ParametrosAgregadosAnalytics, Query()],
    s: SesionDeLectura,
) -> PaginaAgregadosAnalytics:
    """Filtrables por `metrica`, `vista`, `dimension_set`, `periodo_desde`, `periodo_hasta`,
    `estado_valor` y cada llave de dimension del catalogo (`producto`, `municipio`, `base`...),
    en la base de datos. Ningun otro parametro se acepta. Una celda SUPRIMIDO se devuelve sin
    ningun valor."""
    vista = _publicada(s, analytics_run_id)
    llaves = consultas.LLAVES_DE_DIMENSION
    total, filas = consultas.agregados(
        s,
        vista.ejecucion,
        FiltrosAgregados(
            metrica=parametros.metrica,
            vista=None if parametros.vista is None else parametros.vista.value,
            dimension_set=(
                None if parametros.dimension_set is None else parametros.dimension_set.value
            ),
            periodo_desde=parametros.periodo_desde,
            periodo_hasta=parametros.periodo_hasta,
            estado_valor=parametros.estado_valor,
            dimensiones=tuple(
                (llave, getattr(parametros, llave))
                for llave in llaves
                if getattr(parametros, llave) is not None
            ),
        ),
        desplazamiento=parametros.desplazamiento,
        limite=parametros.por_pagina,
    )
    return PaginaAgregadosAnalytics(
        analytics_run_id=analytics_run_id,
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[
            AgregadoAnalyticsRespuesta(
                metrica=a.metrica_codigo,
                vista=a.vista,
                dimension_set=a.dimension_set,
                periodo_desde=a.periodo_desde,
                periodo_hasta=a.periodo_hasta,
                periodo_parcial=a.periodo_parcial,
                dimensiones=a.dimensiones,
                dimension_hash=a.dimension_hash,
                estado_valor=a.estado_valor,
                poblacion_cuentas=a.poblacion_cuentas,
                valor=a.valor,
                numerador=a.numerador,
                denominador=a.denominador,
                motivos=a.motivos,
            )
            for a in filas
        ],
    )


@router.get(
    "/analytics/ejecuciones/{analytics_run_id}/resumen",
    response_model=ResumenAnalyticsRespuesta,
    summary="Unos pocos indicadores del total: cuentas, saldo, recuperacion, contactabilidad, "
    "promesas, campo y ruteo",
    responses=errores(*SIN_CLAVE, NO_EXISTE, NO_PUBLICADA, (422, "ENTRADA_INVALIDA", "UUID.")),
)
def resumen(analytics_run_id: UUID, s: SesionDeLectura) -> ResumenAnalyticsRespuesta:
    """Del TOTAL acumulado, cada indicador con su estado: si su familia no se declaro o no tiene
    cobertura, NO_DISPONIBLE y sin valor, nunca cero."""
    vista = _publicada(s, analytics_run_id)
    e = vista.ejecucion
    return ResumenAnalyticsRespuesta(
        analytics_run_id=e.analytics_run_id,
        fecha_corte=e.fecha_corte,
        as_of=e.as_of,
        known_at=e.known_at,
        cuentas_cohorte=e.cuentas_cohorte,
        indicadores=[
            IndicadorAnalyticsRespuesta(
                nombre=i.nombre,
                metrica=i.metrica,
                estado_valor=i.estado_valor,
                valor=i.valor,
                numerador=i.numerador,
                denominador=i.denominador,
                motivos=i.motivos,
            )
            for i in consultas.resumen(s, e)
        ],
    )


@router.get(
    "/analytics/ejecuciones/{analytics_run_id}/embudos",
    response_model=EmbudosAnalyticsRespuesta,
    summary="Los embudos CONTACTO y CAMPO: cuentas por etapa, de la anterior y de la cohorte",
    responses=errores(*SIN_CLAVE, NO_EXISTE, NO_PUBLICADA, (422, "ENTRADA_INVALIDA", "UUID.")),
)
def embudos(analytics_run_id: UUID, s: SesionDeLectura) -> EmbudosAnalyticsRespuesta:
    """CONTACTO: COHORTE, GESTIONADAS, CONTACTADAS y CON_PROMESA. CAMPO: CANDIDATAS, ASIGNADAS y
    PARADAS_PROGRAMADAS (planificadas, no visitas). Cuentas distintas del horizonte, anidadas; un
    pago no es una etapa."""
    vista = _publicada(s, analytics_run_id)
    return EmbudosAnalyticsRespuesta(
        analytics_run_id=analytics_run_id,
        embudos=[
            EmbudoAnalyticsRespuesta(
                embudo=nombre,
                etapas=[
                    EtapaEmbudoAnalytics(
                        etapa=x.etapa,
                        metrica=x.metrica,
                        estado_valor=x.estado_valor,
                        cuentas=x.cuentas,
                        pct_etapa_anterior=x.pct_etapa_anterior,
                        pct_cohorte=x.pct_cohorte,
                    )
                    for x in etapas
                ],
            )
            for nombre, etapas in consultas.embudos(s, vista.ejecucion).items()
        ],
    )


@router.get(
    "/analytics/ejecuciones/{analytics_run_id}/controles",
    response_model=ControlesAnalyticsRespuesta,
    summary="Los controles de reconciliacion de la ejecucion, para auditarla",
    responses=errores(*SIN_CLAVE, NO_EXISTE, (422, "ENTRADA_INVALIDA", "No es un UUID.")),
)
def controles(analytics_run_id: UUID, s: SesionDeLectura) -> ControlesAnalyticsRespuesta:
    """Cada control con lo esperado, lo observado y su diferencia, exactos: los de una EXITOSA y
    los de una FALLIDA porque un control no cuadro. Con codigos y conteos, nunca una cuenta."""
    vista = _vista(s, analytics_run_id)
    e = vista.ejecucion
    return ControlesAnalyticsRespuesta(
        analytics_run_id=e.analytics_run_id,
        estado=e.estado,
        resultado=e.resultado,
        controles_totales=e.controles_totales,
        controles_fallidos=e.controles_fallidos,
        controles=[
            ControlAnalyticsRespuesta(
                codigo=c.codigo_control,
                estado=c.estado,
                esperado=c.esperado,
                observado=c.observado,
                diferencia=c.diferencia,
                detalle=c.detalle,
            )
            for c in consultas.controles(s, e)
        ],
    )


# --- las respuestas -------------------------------------------------------------------------------


def _vista(s: Session, analytics_run_id: UUID) -> EjecucionVista:
    try:
        return consultas.obtener(s, analytics_run_id)
    except consultas.EjecucionNoEncontrada as exc:
        raise ErrorDeApi(
            404,
            "ANALYTICS_NO_ENCONTRADA",
            f"No existe una ejecucion de analytics con analytics_run_id {analytics_run_id}.",
        ) from exc


def _publicada(s: Session, analytics_run_id: UUID) -> EjecucionVista:
    vista = _vista(s, analytics_run_id)
    if vista.ejecucion.estado != EstadoAnalytics.EXITOSA:
        raise ErrorDeApi(
            409,
            "ANALYTICS_NO_PUBLICADA",
            f"La ejecucion {analytics_run_id} esta {vista.ejecucion.estado}"
            + (f" ({vista.ejecucion.resultado})" if vista.ejecucion.resultado else "")
            + ": no publico agregados. Sus controles, si los tiene, estan en /controles.",
        )
    return vista


def ejecucion_respuesta(v: EjecucionVista) -> EjecucionAnalyticsRespuesta:
    e = v.ejecucion
    duracion = None if e.terminada_en is None else (e.terminada_en - e.iniciada_en).total_seconds()
    return EjecucionAnalyticsRespuesta(
        analytics_run_id=e.analytics_run_id,
        estado=e.estado,
        resultado=e.resultado,
        modo=e.modo,
        corte_id=v.corte_id,
        fecha_corte=e.fecha_corte,
        as_of=e.as_of,
        known_at=e.known_at,
        zona_horaria=e.zona_horaria,
        granularidad=e.granularidad,
        horizonte_dias=e.horizonte_dias,
        version_motor=e.version_motor,
        version_catalogo=e.version_catalogo,
        hash_catalogo=e.hash_catalogo,
        version_politica=e.version_politica,
        hash_politica=e.hash_politica,
        hash_manifiesto=e.hash_manifiesto,
        firma_inputs=e.firma_inputs,
        cuentas_cohorte=e.cuentas_cohorte,
        agregados_publicados=e.agregados_publicados,
        controles_totales=e.controles_totales,
        controles_fallidos=e.controles_fallidos,
        hash_agregados=e.hash_agregados,
        disponibilidad=e.disponibilidad,
        entradas=[
            EntradaDeAnalyticsRespuesta(
                tipo=x.tipo_entrada,
                referencia_publica=x.referencia_publica,
                version=x.version,
                firma=x.firma,
                periodo_desde=x.periodo_desde,
                periodo_hasta=x.periodo_hasta,
                disponible_en=x.disponible_en,
                metadatos=x.metadatos,
            )
            for x in v.entradas
        ],
        trabajo_id=v.trabajo_id,
        iniciada_en=e.iniciada_en,
        terminada_en=e.terminada_en,
        duracion_segundos=duracion,
        detalle=e.detalle,
    )
