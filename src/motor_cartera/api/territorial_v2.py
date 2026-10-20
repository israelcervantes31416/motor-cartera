"""territorial/v2 por HTTP: pedir la asignacion de un corte, leer su ejecucion, sus asignaciones y
las de una cuenta a traves de los cortes.

La API no asigna: el POST fija exactamente sus entradas (el corte, su geografia EXITOSA, una version
de bases y la politica) y deja la ejecucion EN_PROCESO con su trabajo TERRITORIAL_V2 en la cola
durable, y un worker la ejecuta. Corre en paralelo a territorial/v1, que sigue siendo la etapa
TERRITORIAL del flujo de cada corrida.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from sqlmodel import Session

from motor_cartera.api.dependencias import Sesion, SesionDeLectura
from motor_cartera.api.errores import ErrorDeApi, errores
from motor_cartera.api.esquemas import Paginacion
from motor_cartera.api.esquemas_geografia import (
    AsignacionDeCuentaRespuesta,
    AsignacionV2Respuesta,
    BaseAsignadaRespuesta,
    BasesDeLaEjecucion,
    ConteosTerritorialV2,
    CorteRespuesta,
    EjecucionTerritorialV2Respuesta,
    MunicipioRespuesta,
    PaginaAsignacionesV2,
    PaginaEjecucionesTerritorialV2,
    PaginaTerritorialV2DeCuenta,
    ParametrosAsignacionesV2,
    ParametrosEjecuciones,
    TerritorialV2Entrada,
)
from motor_cartera.api.geografia import corte_de
from motor_cartera.api.gestiones import cuenta_de
from motor_cartera.api.operacional import SIN_CLAVE
from motor_cartera.config import Config
from motor_cartera.geografia.reglas import EstadoGeografia as EstadoDeLaCuenta
from motor_cartera.territorial_v2 import consultas
from motor_cartera.territorial_v2 import ejecuciones as motor
from motor_cartera.territorial_v2.consultas import (
    AsignacionVista,
    EjecucionVista,
    TerritorialV2NoEncontrado,
)
from motor_cartera.territorial_v2.politica import POLITICAS, EstadoAsignacion

router = APIRouter(tags=["territorial-v2"])

NO_EXISTE = (
    404,
    "TERRITORIAL_V2_NO_ENCONTRADO",
    "No existe una ejecucion de territorial/v2 con ese territorial_run_id.",
)
CUENTA_NO_EXISTE = (404, "CUENTA_NO_ENCONTRADA", "No existe una cuenta canonica con ese cuenta_id.")
CORTE_NO_EXISTE = (404, "CORTE_NO_ENCONTRADO", "No existe un corte canonico con ese corte_id.")


@router.post(
    "/territorial-v2",
    status_code=201,
    response_model=EjecucionTerritorialV2Respuesta,
    summary="Pide asignar las cuentas de un corte a sus bases operativas con territorial/v2",
    responses={
        201: {
            "description": "La asignacion quedo EN_PROCESO, con su trabajo en la cola durable: un "
            "worker la ejecuta. `Location` apunta a ella."
        },
        **errores(
            *SIN_CLAVE,
            CORTE_NO_EXISTE,
            (404, "GEOGRAFIA_NO_ENCONTRADA", "No existe esa ejecucion de geografia."),
            (404, "BASES_NO_ENCONTRADAS", "No existe esa version de bases operativas."),
            (409, "TERRITORIAL_V2_EN_PROCESO", "Esas mismas entradas ya se estan asignando."),
            (
                409,
                "TERRITORIAL_V2_YA_GENERADO",
                "Esas mismas entradas (la misma firma) ya se asignaron.",
            ),
            (
                422,
                "GEOGRAFIA_NO_EXITOSA",
                "La geografia no esta EXITOSA: no se asigna sobre una geografia parcial.",
            ),
            (422, "GEOGRAFIA_DE_OTRO_CORTE", "La geografia es de otro corte."),
            (422, "POLITICA_NO_SOPORTADA", "Este servicio no aplica esa version de la politica."),
            (422, "ENTRADA_INVALIDA", "Falta un identificador, o no es un UUID."),
        ),
    },
)
def pedir_asignacion(
    entrada: TerritorialV2Entrada, request: Request, response: Response, s: Sesion
) -> EjecucionTerritorialV2Respuesta:
    """Asigna cada cuenta con geografia confiable a la base activa mas cercana entre las que cubren
    su municipio; las demas quedan en revision geografica, fuera de cobertura, sin base elegible o
    con un empate entre bases.

    **201 quiere decir que la asignacion se creo, no que termino.** Las mismas entradas son la misma
    asignacion: si ya se publico, `409 TERRITORIAL_V2_YA_GENERADO`."""
    config: Config = request.app.state.config
    try:
        ejecucion = motor.abrir(
            s,
            entrada.corte_id,
            entrada.geografia_run_id,
            entrada.bases_version_id,
            version_politica=entrada.version_politica,
            max_intentos=config.worker_max_intentos,
        )
    except motor.EntradaNoAsignable as exc:
        raise ErrorDeApi(exc.estado_http, exc.codigo, str(exc)) from exc
    except motor.TerritorialV2EnProceso as exc:
        raise ErrorDeApi(409, "TERRITORIAL_V2_EN_PROCESO", str(exc)) from exc
    except motor.TerritorialV2YaGenerado as exc:
        raise ErrorDeApi(409, "TERRITORIAL_V2_YA_GENERADO", str(exc)) from exc
    s.commit()
    response.headers["Location"] = f"/territorial-v2/{ejecucion.territorial_run_id}"
    return ejecucion_respuesta(consultas.obtener(s, ejecucion.territorial_run_id))


@router.get(
    "/territorial-v2",
    response_model=PaginaEjecucionesTerritorialV2,
    summary="Las ejecuciones de territorial/v2, la mas reciente primero; con corte_id, las de un "
    "corte",
    responses=errores(
        *SIN_CLAVE,
        CORTE_NO_EXISTE,
        (422, "ENTRADA_INVALIDA", "El corte_id no es un UUID, o la paginacion no es valida."),
    ),
)
def ejecuciones(
    parametros: Annotated[ParametrosEjecuciones, Query()], s: SesionDeLectura
) -> PaginaEjecucionesTerritorialV2:
    """Cada ejecucion, en cualquier estado, con sus entradas declaradas, su firma y sus conteos. Con
    `corte_id`, solo las de ese corte, por su indice."""
    corte = None if parametros.corte_id is None else corte_de(s, parametros.corte_id)
    total, filas = consultas.listar(
        s, corte, desplazamiento=parametros.desplazamiento, limite=parametros.por_pagina
    )
    return PaginaEjecucionesTerritorialV2(
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[ejecucion_respuesta(v) for v in filas],
    )


@router.get(
    "/territorial-v2/{territorial_run_id}",
    response_model=EjecucionTerritorialV2Respuesta,
    summary="Una ejecucion de territorial/v2: sus entradas declaradas, su firma y sus conteos",
    responses=errores(
        *SIN_CLAVE, NO_EXISTE, (422, "ENTRADA_INVALIDA", "El territorial_run_id no es un UUID.")
    ),
)
def obtener(territorial_run_id: UUID, s: SesionDeLectura) -> EjecucionTerritorialV2Respuesta:
    """Mientras el estado sea `EN_PROCESO`, un worker no la ha terminado. Trae la politica con su
    hash y su tolerancia de empate, y la distribucion por base, que es un diagnostico y no un
    balanceo."""
    return ejecucion_respuesta(_vista(s, territorial_run_id))


@router.get(
    "/territorial-v2/{territorial_run_id}/resultados",
    response_model=PaginaAsignacionesV2,
    summary="La asignacion de cada cuenta, en el orden de la cuenta, sin coordenadas",
    responses=errores(
        *SIN_CLAVE,
        NO_EXISTE,
        (422, "ENTRADA_INVALIDA", "Un filtro no es valido, o la paginacion no lo es."),
    ),
)
def resultados(
    territorial_run_id: UUID,
    parametros: Annotated[ParametrosAsignacionesV2, Query()],
    s: SesionDeLectura,
) -> PaginaAsignacionesV2:
    """Con `base` (su codigo), solo las cuentas de esa base; con `municipio`, las de ese CVEGEO; con
    `estado`, las de ese estado. Cada filtro entra por su indice."""
    vista = _vista(s, territorial_run_id)
    total, filas = consultas.asignaciones(
        s,
        vista,
        base=parametros.base,
        cvegeo=parametros.municipio,
        estado=None if parametros.estado is None else parametros.estado.value,
        desplazamiento=parametros.desplazamiento,
        limite=parametros.por_pagina,
    )
    return PaginaAsignacionesV2(
        territorial_run_id=territorial_run_id,
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[_asignacion(v) for v in filas],
    )


@router.get(
    "/cuentas/{cuenta_id}/territorial-v2",
    response_model=PaginaTerritorialV2DeCuenta,
    summary="Las asignaciones de territorial/v2 de una cuenta, del corte mas reciente al mas "
    "antiguo",
    responses=errores(
        *SIN_CLAVE,
        CUENTA_NO_EXISTE,
        (422, "ENTRADA_INVALIDA", "El cuenta_id no es un UUID, o la paginacion no es valida."),
    ),
)
def territorial_de_cuenta(
    cuenta_id: UUID, parametros: Annotated[Paginacion, Query()], s: SesionDeLectura
) -> PaginaTerritorialV2DeCuenta:
    """Solo ejecuciones EXITOSA, cada una con sus entradas: una asignacion historica no se
    sobrescribe aunque cambien las bases o la referencia."""
    cuenta = cuenta_de(s, cuenta_id)
    total, filas = consultas.historial(
        s, cuenta, desplazamiento=parametros.desplazamiento, limite=parametros.por_pagina
    )
    return PaginaTerritorialV2DeCuenta(
        cuenta_id=cuenta.cuenta_id,
        cliente_unico=cuenta.cliente_unico,
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[
            AsignacionDeCuentaRespuesta(
                **_asignacion(h.vista).model_dump(),
                territorial_run_id=h.ejecucion.territorial_run_id,
                corte=CorteRespuesta(corte_id=h.corte_id, fecha_corte=h.fecha_corte),
                geografia_run_id=h.geografia_run_id,
                bases_version_id=h.bases_version_id,
                version_politica=h.ejecucion.version_politica,
            )
            for h in filas
        ],
    )


def _vista(s: Session, territorial_run_id: UUID) -> EjecucionVista:
    try:
        return consultas.obtener(s, territorial_run_id)
    except TerritorialV2NoEncontrado as exc:
        raise ErrorDeApi(
            404,
            "TERRITORIAL_V2_NO_ENCONTRADO",
            f"No existe una ejecucion de territorial/v2 con territorial_run_id "
            f"{territorial_run_id}.",
        ) from exc


def _asignacion(v: AsignacionVista) -> AsignacionV2Respuesta:
    a = v.asignacion
    return AsignacionV2Respuesta(
        cuenta_id=v.cuenta_id,
        cliente_unico=v.cliente_unico,
        estado_asignacion=EstadoAsignacion(a.estado_asignacion),
        estado_geografia=EstadoDeLaCuenta(v.estado_geografia),
        municipio=None
        if v.cvegeo is None or v.nombre_oficial is None
        else MunicipioRespuesta(cvegeo=v.cvegeo, nombre_oficial=v.nombre_oficial),
        base=None
        if v.base_id is None or v.codigo_base is None or v.nombre_base is None
        else BaseAsignadaRespuesta(base_id=v.base_id, codigo=v.codigo_base, nombre=v.nombre_base),
        distancia_base_m=a.distancia_base_m,
        motivos=a.motivos,
    )


def ejecucion_respuesta(vista: EjecucionVista) -> EjecucionTerritorialV2Respuesta:
    e = vista.ejecucion
    politica = POLITICAS.get(e.version_politica)
    return EjecucionTerritorialV2Respuesta(
        territorial_run_id=e.territorial_run_id,
        estado=e.estado,
        resultado=e.resultado,
        corte=CorteRespuesta(corte_id=vista.corte_id, fecha_corte=vista.fecha_corte),
        geografia_run_id=vista.geografia.geografia_run_id,
        bases=BasesDeLaEjecucion(
            bases_version_id=vista.bases.bases_version_id,
            nombre=vista.bases.nombre,
            hash_configuracion=vista.bases.hash_configuracion,
        ),
        version_motor=e.version_motor,
        version_politica=e.version_politica,
        hash_politica=e.hash_politica,
        tolerancia_empate_m=None if politica is None else Decimal(politica.tolerancia_empate_m),
        firma_inputs=e.firma_inputs,
        conteos=ConteosTerritorialV2(
            cuentas_objetivo=e.cuentas_objetivo,
            asignadas=e.asignadas,
            revision_geografica=e.revision_geografica,
            sin_base=e.sin_base,
            fuera_cobertura=e.fuera_cobertura,
            ambiguas=e.ambiguas,
        ),
        distribucion=e.distribucion,
        trabajo_id=vista.trabajo_id,
        iniciada_en=e.iniciada_en,
        terminada_en=e.terminada_en,
        detalle=e.detalle,
    )
