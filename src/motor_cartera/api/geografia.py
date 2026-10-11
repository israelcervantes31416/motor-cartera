"""La geografia por HTTP: las versiones de la referencia, pedir la geografia de un corte, leer su
ejecucion y lo que publico, y la geografia de una cuenta a traves de los cortes.

La API no resuelve nada: el POST fija las entradas y deja la ejecucion EN_PROCESO con su trabajo
GEOGRAFIA en la cola durable, y un worker la ejecuta. La referencia no se importa por aqui: es un
archivo oficial que se importa con `motor-cartera importar-geografia`. Ninguna respuesta lleva la
coordenada de una cuenta.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from sqlmodel import Session, select

from motor_cartera.api.dependencias import Sesion, SesionDeLectura
from motor_cartera.api.errores import ErrorDeApi, errores
from motor_cartera.api.esquemas import Paginacion
from motor_cartera.api.esquemas_geografia import (
    ConteosGeografia,
    CorteRespuesta,
    EjecucionGeografiaRespuesta,
    GeografiaDeCuentaRespuesta,
    GeografiaEntrada,
    MunicipioRespuesta,
    PaginaEjecucionesGeografia,
    PaginaGeografiaDeCuenta,
    PaginaResultadosGeografia,
    PaginaVersionesGeografia,
    ParametrosEjecuciones,
    ParametrosResultadosGeografia,
    ReferenciaRespuesta,
    ResultadoGeografiaRespuesta,
    VersionGeografiaRespuesta,
)
from motor_cartera.api.gestiones import cuenta_de
from motor_cartera.api.operacional import SIN_CLAVE
from motor_cartera.config import Config
from motor_cartera.db.modelos import CorteCanonico, VersionGeografia
from motor_cartera.geografia import consultas
from motor_cartera.geografia import ejecuciones as motor
from motor_cartera.geografia.consultas import (
    EjecucionVista,
    GeografiaNoEncontrada,
    VersionNoEncontrada,
)
from motor_cartera.geografia.reglas import comparacion

router = APIRouter(tags=["geografia"])

NO_EXISTE = (
    404,
    "GEOGRAFIA_NO_ENCONTRADA",
    "No existe una ejecucion de geografia con ese geografia_run_id.",
)
VERSION_NO_EXISTE = (
    404,
    "GEOGRAFIA_VERSION_NO_ENCONTRADA",
    "No existe una version de la referencia con ese geografia_version_id.",
)
CUENTA_NO_EXISTE = (404, "CUENTA_NO_ENCONTRADA", "No existe una cuenta canonica con ese cuenta_id.")
CORTE_NO_EXISTE = (404, "CORTE_NO_ENCONTRADO", "No existe un corte canonico con ese corte_id.")


@router.get(
    "/geografia/versiones",
    response_model=PaginaVersionesGeografia,
    summary="Las versiones importadas de la referencia geografica, la mas reciente primero",
    responses=errores(*SIN_CLAVE, (422, "ENTRADA_INVALIDA", "La paginacion no es valida.")),
)
def versiones(
    parametros: Annotated[Paginacion, Query()], s: SesionDeLectura
) -> PaginaVersionesGeografia:
    """Cada importacion, en cualquier estado: su producto, su edicion, el SHA-256 de su archivo y
    cuantos municipios publico. Ninguna trae sus geometrias."""
    total, filas = consultas.versiones(
        s, desplazamiento=parametros.desplazamiento, limite=parametros.por_pagina
    )
    return PaginaVersionesGeografia(
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[version_respuesta(v) for v in filas],
    )


@router.get(
    "/geografia/versiones/{geografia_version_id}",
    response_model=VersionGeografiaRespuesta,
    summary="Una version de la referencia: de donde salio, como se importo y que publico",
    responses=errores(
        *SIN_CLAVE,
        VERSION_NO_EXISTE,
        (422, "ENTRADA_INVALIDA", "El geografia_version_id no es un UUID."),
    ),
)
def version(geografia_version_id: UUID, s: SesionDeLectura) -> VersionGeografiaRespuesta:
    """De donde salio (proveedor, producto, edicion, capa y el SHA-256 de su archivo), en que
    sistema de coordenadas venia, en que estado quedo, cuantos municipios publico y cuantos reparo,
    con su detalle. Sin geometrias."""
    return version_respuesta(_version(s, geografia_version_id))


@router.post(
    "/geografia/ejecuciones",
    status_code=201,
    response_model=EjecucionGeografiaRespuesta,
    summary="Pide la geografia de las cuentas de un corte con una version de la referencia",
    responses={
        201: {
            "description": "La geografia quedo EN_PROCESO, con su trabajo en la cola durable: un "
            "worker la resuelve. `Location` apunta a ella."
        },
        **errores(
            *SIN_CLAVE,
            CORTE_NO_EXISTE,
            VERSION_NO_EXISTE,
            (409, "GEOGRAFIA_EN_PROCESO", "Esas mismas entradas ya se estan resolviendo."),
            (
                409,
                "GEOGRAFIA_YA_GENERADA",
                "Esas mismas entradas (la misma firma) ya se resolvieron.",
            ),
            (
                422,
                "GEOGRAFIA_VERSION_NO_PUBLICADA",
                "La version de la referencia no esta PUBLICADA.",
            ),
            (422, "ENTRADA_INVALIDA", "Falta corte_id o geografia_version_id, o no son UUID."),
        ),
    },
)
def pedir_geografia(
    entrada: GeografiaEntrada, request: Request, response: Response, s: Sesion
) -> EjecucionGeografiaRespuesta:
    """Ubica, en un solo trabajo, la coordenada observada de cada cuenta del corte en los municipios
    de la version de la referencia que se declara, y la compara con el municipio que resolvio la
    fuente.

    **201 quiere decir que la geografia se creo, no que termino.** Las mismas entradas (corte, su
    dataset, la version de la referencia y la del motor) son la misma geografia: si ya se publico,
    `409 GEOGRAFIA_YA_GENERADA`."""
    config: Config = request.app.state.config
    try:
        ejecucion = motor.abrir(
            s,
            entrada.corte_id,
            entrada.geografia_version_id,
            max_intentos=config.worker_max_intentos,
        )
    except motor.EntradaNoGeolocalizable as exc:
        raise ErrorDeApi(exc.estado_http, exc.codigo, str(exc)) from exc
    except motor.GeografiaEnProceso as exc:
        raise ErrorDeApi(409, "GEOGRAFIA_EN_PROCESO", str(exc)) from exc
    except motor.GeografiaYaGenerada as exc:
        raise ErrorDeApi(409, "GEOGRAFIA_YA_GENERADA", str(exc)) from exc
    s.commit()
    response.headers["Location"] = f"/geografia/ejecuciones/{ejecucion.geografia_run_id}"
    return ejecucion_respuesta(consultas.obtener(s, ejecucion.geografia_run_id))


@router.get(
    "/geografia/ejecuciones",
    response_model=PaginaEjecucionesGeografia,
    summary="Las ejecuciones de geografia, la mas reciente primero; con corte_id, las de un corte",
    responses=errores(
        *SIN_CLAVE,
        CORTE_NO_EXISTE,
        (422, "ENTRADA_INVALIDA", "El corte_id no es un UUID, o la paginacion no es valida."),
    ),
)
def ejecuciones(
    parametros: Annotated[ParametrosEjecuciones, Query()], s: SesionDeLectura
) -> PaginaEjecucionesGeografia:
    """Cada ejecucion, en cualquier estado, con sus entradas, su firma y sus conteos. Con
    `corte_id`, solo las de ese corte, por su indice."""
    corte = None if parametros.corte_id is None else corte_de(s, parametros.corte_id)
    total, filas = consultas.listar(
        s, corte, desplazamiento=parametros.desplazamiento, limite=parametros.por_pagina
    )
    return PaginaEjecucionesGeografia(
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[ejecucion_respuesta(v) for v in filas],
    )


@router.get(
    "/geografia/ejecuciones/{geografia_run_id}",
    response_model=EjecucionGeografiaRespuesta,
    summary="Una ejecucion de geografia: sus entradas, su firma, su estado y sus conteos",
    responses=errores(
        *SIN_CLAVE, NO_EXISTE, (422, "ENTRADA_INVALIDA", "El geografia_run_id no es un UUID.")
    ),
)
def obtener(geografia_run_id: UUID, s: SesionDeLectura) -> EjecucionGeografiaRespuesta:
    """Mientras el estado sea `EN_PROCESO`, un worker no la ha terminado."""
    return ejecucion_respuesta(_vista(s, geografia_run_id))


@router.get(
    "/geografia/ejecuciones/{geografia_run_id}/resultados",
    response_model=PaginaResultadosGeografia,
    summary="La geografia de cada cuenta, en el orden de la cuenta, sin coordenadas",
    responses=errores(
        *SIN_CLAVE,
        NO_EXISTE,
        (422, "ENTRADA_INVALIDA", "Un filtro no es valido, o la paginacion no lo es."),
    ),
)
def resultados(
    geografia_run_id: UUID,
    parametros: Annotated[ParametrosResultadosGeografia, Query()],
    s: SesionDeLectura,
) -> PaginaResultadosGeografia:
    """Con `estado_geografia`, solo las de ese estado (`DISCREPANCIA_MUNICIPIO`,
    `FUERA_DE_REFERENCIA`...: las anomalias); con `municipio`, solo las que caen en ese CVEGEO.
    Cada filtro entra por su indice. Vacia mientras sigue EN_PROCESO, o si fallo."""
    vista = _vista(s, geografia_run_id)
    total, filas = consultas.resultados(
        s,
        vista.ejecucion,
        estado=None if parametros.estado_geografia is None else parametros.estado_geografia.value,
        cvegeo=parametros.municipio,
        desplazamiento=parametros.desplazamiento,
        limite=parametros.por_pagina,
    )
    return PaginaResultadosGeografia(
        geografia_run_id=geografia_run_id,
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[
            ResultadoGeografiaRespuesta(
                cuenta_id=v.cuenta_id,
                cliente_unico=v.cliente_unico,
                source_row=v.resultado.source_row,
                source_sheet=v.resultado.source_sheet,
                estado_geografia=v.resultado.estado_geografia,
                municipio=_municipio(v.cvegeo, v.nombre_oficial),
                municipio_fuente=v.resultado.municipio_fuente_resuelto,
                comparacion_municipio=comparacion(v.resultado.coincide_municipio).value,
                motivos=v.resultado.motivos,
            )
            for v in filas
        ],
    )


@router.get(
    "/cuentas/{cuenta_id}/geografia",
    response_model=PaginaGeografiaDeCuenta,
    summary="La geografia de una cuenta en cada corte, del mas reciente al mas antiguo",
    responses=errores(
        *SIN_CLAVE,
        CUENTA_NO_EXISTE,
        (422, "ENTRADA_INVALIDA", "El cuenta_id no es un UUID, o la paginacion no es valida."),
    ),
)
def geografia_de_cuenta(
    cuenta_id: UUID, parametros: Annotated[Paginacion, Query()], s: SesionDeLectura
) -> PaginaGeografiaDeCuenta:
    """Solo ejecuciones EXITOSA. La misma cuenta puede tener varias por corte (otra version de la
    referencia), y ninguna se sobrescribe. Sin coordenadas."""
    cuenta = cuenta_de(s, cuenta_id)
    total, filas = consultas.historial(
        s, cuenta, desplazamiento=parametros.desplazamiento, limite=parametros.por_pagina
    )
    return PaginaGeografiaDeCuenta(
        cuenta_id=cuenta.cuenta_id,
        cliente_unico=cuenta.cliente_unico,
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[
            GeografiaDeCuentaRespuesta(
                geografia_run_id=g.ejecucion.geografia_run_id,
                corte=CorteRespuesta(corte_id=g.corte_id, fecha_corte=g.fecha_corte),
                referencia=_referencia(g.version),
                estado_geografia=g.resultado.estado_geografia,
                municipio=_municipio(g.cvegeo, g.nombre_oficial),
                municipio_fuente=g.resultado.municipio_fuente_resuelto,
                comparacion_municipio=comparacion(g.resultado.coincide_municipio).value,
                motivos=g.resultado.motivos,
            )
            for g in filas
        ],
    )


def corte_de(s: Session, corte_id: UUID) -> CorteCanonico:
    """El corte canonico con ese corte_id, o 404."""
    corte = s.exec(select(CorteCanonico).where(CorteCanonico.corte_id == corte_id)).first()
    if corte is None:
        raise ErrorDeApi(
            404, "CORTE_NO_ENCONTRADO", f"No existe un corte canonico con corte_id {corte_id}."
        )
    return corte


def _vista(s: Session, geografia_run_id: UUID) -> EjecucionVista:
    try:
        return consultas.obtener(s, geografia_run_id)
    except GeografiaNoEncontrada as exc:
        raise ErrorDeApi(
            404,
            "GEOGRAFIA_NO_ENCONTRADA",
            f"No existe una ejecucion de geografia con geografia_run_id {geografia_run_id}.",
        ) from exc


def _version(s: Session, geografia_version_id: UUID) -> VersionGeografia:
    try:
        return consultas.version(s, geografia_version_id)
    except VersionNoEncontrada as exc:
        raise ErrorDeApi(
            404,
            "GEOGRAFIA_VERSION_NO_ENCONTRADA",
            f"No existe una version de la referencia con geografia_version_id "
            f"{geografia_version_id}.",
        ) from exc


def _municipio(cvegeo: str | None, nombre: str | None) -> MunicipioRespuesta | None:
    if cvegeo is None:
        return None
    return MunicipioRespuesta(cvegeo=cvegeo, nombre_oficial=nombre)


def _referencia(v: VersionGeografia) -> ReferenciaRespuesta:
    return ReferenciaRespuesta(
        geografia_version_id=v.geografia_version_id,
        producto=v.producto,
        edicion=v.edicion,
        sha256=v.sha256,
    )


def version_respuesta(v: VersionGeografia) -> VersionGeografiaRespuesta:
    return VersionGeografiaRespuesta(
        geografia_version_id=v.geografia_version_id,
        proveedor=v.proveedor,
        producto=v.producto,
        edicion=v.edicion,
        nivel=v.nivel,
        capa=v.capa,
        archivo=v.archivo,
        sha256=v.sha256,
        tamano_bytes=v.tamano_bytes,
        srid=v.srid,
        srid_origen=v.srid_origen,
        estado=v.estado,
        municipios=v.municipios,
        reparadas=v.reparadas,
        metadatos=v.metadatos,
        importada_en=v.importada_en,
        terminada_en=v.terminada_en,
        detalle=v.detalle,
    )


def ejecucion_respuesta(vista: EjecucionVista) -> EjecucionGeografiaRespuesta:
    e = vista.ejecucion
    return EjecucionGeografiaRespuesta(
        geografia_run_id=e.geografia_run_id,
        estado=e.estado,
        resultado=e.resultado,
        corte=CorteRespuesta(corte_id=vista.corte_id, fecha_corte=vista.fecha_corte),
        referencia=_referencia(vista.version),
        version_motor=e.version_motor,
        firma_inputs=e.firma_inputs,
        conteos=ConteosGeografia(
            cuentas_objetivo=e.cuentas_objetivo,
            validas=e.validas,
            sin_coordenadas=e.sin_coordenadas,
            incompletas=e.incompletas,
            invalidas=e.invalidas,
            fuera_cobertura=e.fuera_cobertura,
            ambiguas=e.ambiguas,
            discrepantes=e.discrepantes,
        ),
        distribucion=e.distribucion,
        trabajo_id=vista.trabajo_id,
        iniciada_en=e.iniciada_en,
        terminada_en=e.terminada_en,
        detalle=e.detalle,
    )
