"""Las promesas de pago y los convenios por HTTP: nacen de una gestion, se leen y se cancelan.

Una promesa o un convenio nacen de una gestion concreta, con el resultado que les corresponde
(`PROMESA` o `CONVENIO`), y no de un snapshot: lo que un corte dice de una promesa es una
observacion de la fuente, y esta en la linea de tiempo de la cuenta como tal. Su estado operativo
(vigente, cancelada o anulada) sale de sus eventos. Si una promesa se cumplio no lo dice nadie con
un UPDATE: lo dice una evaluacion versionada, a una fecha de corte explicita.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response

from motor_cartera.api.dependencias import SesionDeLectura
from motor_cartera.api.errores import ErrorDeApi, errores
from motor_cartera.api.esquemas import Paginacion
from motor_cartera.api.esquemas_lifecycle import (
    CierreEntrada,
    ConvenioEntrada,
    ConvenioRespuesta,
    PaginaConvenios,
    PaginaPromesas,
    ParametrosPromesas,
    PromesaEntrada,
    PromesaRespuesta,
)
from motor_cartera.api.gestiones import cuenta_de
from motor_cartera.api.operacional import (
    ANTES_DEL_EVENTO,
    DATOS_PERSONALES,
    EN_EL_FUTURO,
    ENTRADA_INVALIDA,
    ERRORES_DEL_REGISTRO,
    LLAVE_REUTILIZADA,
    SIN_CLAVE,
    LlaveDeIdempotencia,
    convenio_respuesta,
    promesa_respuesta,
    responder,
    traducir,
)
from motor_cartera.config import Config
from motor_cartera.db.sesion import sesion_de_lectura
from motor_cartera.lifecycle import consultas, registro
from motor_cartera.lifecycle.consultas import ConvenioNoEncontrado, PromesaNoEncontrada
from motor_cartera.motor_pagos.reglas import VERSION_MOTOR_PAGOS

router = APIRouter(tags=["lifecycle"])

GESTION_NO_EXISTE = (404, "GESTION_NO_ENCONTRADA", "No existe una gestion con ese gestion_id.")
PROMESA_NO_EXISTE = (404, "PROMESA_NO_ENCONTRADA", "No existe una promesa con ese promesa_id.")
CONVENIO_NO_EXISTE = (404, "CONVENIO_NO_ENCONTRADO", "No existe un convenio con ese convenio_id.")
CUENTA_NO_EXISTE = (404, "CUENTA_NO_ENCONTRADA", "No existe una cuenta canonica con ese cuenta_id.")
GESTION_ANULADA = (
    409,
    "GESTION_ANULADA",
    "La gestion esta anulada: no recibe promesa ni convenio.",
)


# --- las promesas ---------------------------------------------------------------------------------


@router.post(
    "/gestiones/{gestion_id}/promesas",
    status_code=201,
    response_model=PromesaRespuesta,
    summary="Registra la promesa de pago de una gestion con resultado PROMESA",
    responses={
        201: {"description": "Se registro la promesa: `Location` apunta a ella."},
        200: {
            "description": "La misma peticion ya la habia registrado.",
            "model": PromesaRespuesta,
        },
        **errores(
            *SIN_CLAVE,
            GESTION_NO_EXISTE,
            GESTION_ANULADA,
            (409, "GESTION_SIN_PROMESA", "La gestion no termino en PROMESA."),
            (409, "PROMESA_YA_REGISTRADA", "La gestion ya tiene su promesa, de otra peticion."),
            LLAVE_REUTILIZADA,
            ENTRADA_INVALIDA,
            (
                422,
                "FECHA_LIMITE_ANTERIOR",
                "La fecha limite es anterior al dia en que se acordo, en la zona de la fuente.",
            ),
            EN_EL_FUTURO,
            ANTES_DEL_EVENTO,
        ),
    },
)
def crear_promesa(
    gestion_id: UUID,
    entrada: PromesaEntrada,
    llave: LlaveDeIdempotencia,
    request: Request,
    response: Response,
) -> PromesaRespuesta:
    """Cuanto se prometio (`monto_prometido`) y hasta que dia (`fecha_limite`, en la hora local de
    la fuente). Nace `VIGENTE`; si se cumple lo dira una evaluacion (`POST /evaluaciones-promesas`),
    no un cambio de esta promesa, que no se sobrescribe nunca."""
    config: Config = request.app.state.config
    datos = registro.DatosPromesa(
        monto_prometido=entrada.monto_prometido,
        fecha_limite=entrada.fecha_limite,
        ocurrido_en=entrada.ocurrido_en,
        actor_ref=entrada.actor_ref,
    )
    try:
        hecho = registro.crear_promesa(gestion_id, llave, datos, zona=config.zona_horaria_fuente)
    except ERRORES_DEL_REGISTRO as exc:
        raise traducir(exc) from exc
    responder(response, hecho, f"/promesas/{hecho.recurso_id}")
    return _promesa(hecho.recurso_id)


@router.get(
    "/promesas/{promesa_id}",
    response_model=PromesaRespuesta,
    summary="Una promesa: lo prometido, su estado operativo y su ultima evaluacion",
    responses=errores(
        *SIN_CLAVE, PROMESA_NO_EXISTE, (422, "ENTRADA_INVALIDA", "El promesa_id no es un UUID.")
    ),
)
def obtener_promesa(promesa_id: UUID, s: SesionDeLectura) -> PromesaRespuesta:
    """Sus datos tal como se registraron, su estado operativo (`VIGENTE`, `CANCELADA` o `ANULADA`),
    su linaje (la gestion de la que nacio y sus eventos) y la ultima evaluacion economica
    disponible: la de su fecha de corte mas reciente."""
    try:
        return promesa_respuesta(consultas.obtener_promesa(s, promesa_id))
    except PromesaNoEncontrada as exc:
        raise ErrorDeApi(
            404, "PROMESA_NO_ENCONTRADA", f"No existe una promesa con promesa_id {promesa_id}."
        ) from exc


@router.post(
    "/promesas/{promesa_id}/cancelaciones",
    status_code=201,
    response_model=PromesaRespuesta,
    summary="Cancela una promesa que dejo de valer",
    responses={
        201: {"description": "Se registro la cancelacion: la promesa queda CANCELADA."},
        200: {
            "description": "La misma cancelacion ya se habia registrado.",
            "model": PromesaRespuesta,
        },
        **errores(
            *SIN_CLAVE,
            PROMESA_NO_EXISTE,
            (409, "PROMESA_YA_CANCELADA", "La promesa ya tiene su cancelacion."),
            (409, "PROMESA_ANULADA", "La promesa esta anulada con su gestion."),
            LLAVE_REUTILIZADA,
            ENTRADA_INVALIDA,
            DATOS_PERSONALES,
            EN_EL_FUTURO,
            ANTES_DEL_EVENTO,
        ),
    },
)
def cancelar_promesa(
    promesa_id: UUID,
    entrada: CierreEntrada,
    llave: LlaveDeIdempotencia,
    response: Response,
) -> PromesaRespuesta:
    """Un evento `PROMESA_CANCELADA`, con su motivo y cuando se decidio: la promesa dejo de valer en
    el negocio. Una promesa registrada por error no se cancela: se anula su gestion. Una evaluacion
    a una fecha posterior a la cancelacion la da por `CANCELADA`."""
    datos = registro.DatosCierre(entrada.ocurrido_en, entrada.motivo, entrada.actor_ref)
    try:
        hecho = registro.cancelar_promesa(promesa_id, llave, datos)
    except ERRORES_DEL_REGISTRO as exc:
        raise traducir(exc) from exc
    responder(response, hecho, f"/promesas/{promesa_id}")
    return _promesa(promesa_id)


@router.get(
    "/cuentas/{cuenta_id}/promesas",
    response_model=PaginaPromesas,
    summary="Las promesas de la cuenta, con su estado y su ultima evaluacion",
    responses=errores(
        *SIN_CLAVE,
        CUENTA_NO_EXISTE,
        (422, "ENTRADA_INVALIDA", "El estado no es valido, o la paginacion no lo es."),
    ),
)
def promesas_de_cuenta(
    cuenta_id: UUID, parametros: Annotated[ParametrosPromesas, Query()], s: SesionDeLectura
) -> PaginaPromesas:
    """De la de fecha limite mas reciente a la mas antigua; con `estado`, solo las de ese estado
    operativo."""
    cuenta = cuenta_de(s, cuenta_id)
    total, pagina = consultas.promesas_de_cuenta(
        s,
        cuenta,
        estado=parametros.estado,
        desplazamiento=parametros.desplazamiento,
        limite=parametros.por_pagina,
    )
    return PaginaPromesas(
        cuenta_id=cuenta.cuenta_id,
        cliente_unico=cuenta.cliente_unico,
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[promesa_respuesta(vista) for vista in pagina],
    )


# --- los convenios --------------------------------------------------------------------------------


@router.post(
    "/gestiones/{gestion_id}/convenios",
    status_code=201,
    response_model=ConvenioRespuesta,
    summary="Registra el convenio de una gestion con resultado CONVENIO, con sus cuotas",
    responses={
        201: {"description": "Se registro el convenio: `Location` apunta a el."},
        200: {
            "description": "La misma peticion ya lo habia registrado.",
            "model": ConvenioRespuesta,
        },
        **errores(
            *SIN_CLAVE,
            GESTION_NO_EXISTE,
            GESTION_ANULADA,
            (409, "GESTION_SIN_CONVENIO", "La gestion no termino en CONVENIO."),
            (409, "CONVENIO_YA_REGISTRADO", "La gestion ya tiene su convenio, de otra peticion."),
            LLAVE_REUTILIZADA,
            ENTRADA_INVALIDA,
            (
                422,
                "CUOTAS_INCOHERENTES",
                "Las cuotas no suman el monto total, no vencen en fechas crecientes o vencen fuera "
                "de la vigencia.",
            ),
            (422, "CONVENIO_INCOHERENTE", "La fecha de fin es anterior al inicio."),
            EN_EL_FUTURO,
            ANTES_DEL_EVENTO,
        ),
    },
)
def crear_convenio(
    gestion_id: UUID,
    entrada: ConvenioEntrada,
    llave: LlaveDeIdempotencia,
    response: Response,
) -> ConvenioRespuesta:
    """Lo que la operacion acordo: monto total, vigencia y, si se declaro, su calendario, cuota por
    cuota. No se generan cuotas de un plazo ni de una periodicidad que nadie declaro, y no se
    registran tasas ni terminos que nadie registro. La base comprueba, en la misma transaccion, que
    el convenio tenga exactamente las cuotas declaradas y que cuadren."""
    datos = registro.DatosConvenio(
        monto_total_acordado=entrada.monto_total_acordado,
        fecha_inicio=entrada.fecha_inicio,
        fecha_fin=entrada.fecha_fin,
        cuotas=tuple(registro.DatosCuota(c.fecha_vencimiento, c.monto) for c in entrada.cuotas),
        ocurrido_en=entrada.ocurrido_en,
        actor_ref=entrada.actor_ref,
    )
    try:
        hecho = registro.crear_convenio(gestion_id, llave, datos)
    except ERRORES_DEL_REGISTRO as exc:
        raise traducir(exc) from exc
    responder(response, hecho, f"/convenios/{hecho.recurso_id}")
    return _convenio(hecho.recurso_id)


@router.get(
    "/convenios/{convenio_id}",
    response_model=ConvenioRespuesta,
    summary="Un convenio: lo acordado, sus cuotas y lo que se observa durante su vigencia",
    responses=errores(
        *SIN_CLAVE, CONVENIO_NO_EXISTE, (422, "ENTRADA_INVALIDA", "El convenio_id no es un UUID.")
    ),
)
def obtener_convenio(convenio_id: UUID, s: SesionDeLectura) -> ConvenioRespuesta:
    """Con su estado operativo y `recuperacion_observada_durante_convenio`: los PAGO de la cuenta
    en su vigencia, en la interpretacion vigente de los pagos, hasta donde llegan los datos
    (`horizonte_pagos`). No es el pago de sus cuotas: `evaluacion_de_cuotas` es `NO_EVALUABLE`,
    porque ningun movimiento se aplica a una cuota sin reglas que lo justifiquen."""
    try:
        vista = consultas.obtener_convenio(s, convenio_id, version_motor=VERSION_MOTOR_PAGOS)
    except ConvenioNoEncontrado as exc:
        raise ErrorDeApi(
            404, "CONVENIO_NO_ENCONTRADO", f"No existe un convenio con convenio_id {convenio_id}."
        ) from exc
    return convenio_respuesta(vista, VERSION_MOTOR_PAGOS)


@router.post(
    "/convenios/{convenio_id}/cancelaciones",
    status_code=201,
    response_model=ConvenioRespuesta,
    summary="Cancela un convenio que dejo de valer",
    responses={
        201: {"description": "Se registro la cancelacion: el convenio queda CANCELADO."},
        200: {
            "description": "La misma cancelacion ya se habia registrado.",
            "model": ConvenioRespuesta,
        },
        **errores(
            *SIN_CLAVE,
            CONVENIO_NO_EXISTE,
            (409, "CONVENIO_YA_CANCELADO", "El convenio ya tiene su cancelacion."),
            (409, "CONVENIO_ANULADO", "El convenio esta anulado con su gestion."),
            LLAVE_REUTILIZADA,
            ENTRADA_INVALIDA,
            DATOS_PERSONALES,
            EN_EL_FUTURO,
            ANTES_DEL_EVENTO,
        ),
    },
)
def cancelar_convenio(
    convenio_id: UUID,
    entrada: CierreEntrada,
    llave: LlaveDeIdempotencia,
    response: Response,
) -> ConvenioRespuesta:
    """Un evento `CONVENIO_CANCELADO`, con su motivo. El convenio y sus cuotas siguen visibles."""
    datos = registro.DatosCierre(entrada.ocurrido_en, entrada.motivo, entrada.actor_ref)
    try:
        hecho = registro.cancelar_convenio(convenio_id, llave, datos)
    except ERRORES_DEL_REGISTRO as exc:
        raise traducir(exc) from exc
    responder(response, hecho, f"/convenios/{convenio_id}")
    return _convenio(convenio_id)


@router.get(
    "/cuentas/{cuenta_id}/convenios",
    response_model=PaginaConvenios,
    summary="Los convenios de la cuenta, con sus cuotas",
    responses=errores(
        *SIN_CLAVE,
        CUENTA_NO_EXISTE,
        (422, "ENTRADA_INVALIDA", "El cuenta_id no es un UUID, o la paginacion no es valida."),
    ),
)
def convenios_de_cuenta(
    cuenta_id: UUID, paginacion: Annotated[Paginacion, Query()], s: SesionDeLectura
) -> PaginaConvenios:
    """Del de inicio mas reciente al mas antiguo. La recuperacion observada de cada uno esta en
    `GET /convenios/{convenio_id}`."""
    cuenta = cuenta_de(s, cuenta_id)
    total, pagina = consultas.convenios_de_cuenta(
        s, cuenta, desplazamiento=paginacion.desplazamiento, limite=paginacion.por_pagina
    )
    return PaginaConvenios(
        cuenta_id=cuenta.cuenta_id,
        cliente_unico=cuenta.cliente_unico,
        total=total,
        pagina=paginacion.pagina,
        por_pagina=paginacion.por_pagina,
        elementos=[convenio_respuesta(vista, VERSION_MOTOR_PAGOS) for vista in pagina],
    )


def _promesa(promesa_id: UUID) -> PromesaRespuesta:
    with sesion_de_lectura() as s:
        return promesa_respuesta(consultas.obtener_promesa(s, promesa_id))


def _convenio(convenio_id: UUID) -> ConvenioRespuesta:
    with sesion_de_lectura() as s:
        vista = consultas.obtener_convenio(s, convenio_id, version_motor=VERSION_MOTOR_PAGOS)
    return convenio_respuesta(vista, VERSION_MOTOR_PAGOS)
