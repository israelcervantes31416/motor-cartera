"""Las gestiones de cobranza por HTTP: registrarlas, leerlas y anular las que se registraron por
error.

Una gestion es un evento operacional que registra Motor Cartera, no una fuente oficial: no sale de
ningun archivo del acreedor ni de ningun snapshot. La escritura es sincrona y transaccional (un
evento, una transaccion, en `lifecycle.registro`) y exige su `Idempotency-Key`; ver
`api.operacional` para el contrato de 201, 200 y 409. Una gestion no se corrige en su lugar: se
anula con otro evento, y la anulada sigue visible con su anulacion.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from sqlmodel import Session

from motor_cartera.api.dependencias import SesionDeLectura
from motor_cartera.api.errores import ErrorDeApi, errores
from motor_cartera.api.esquemas_lifecycle import (
    CierreEntrada,
    GestionEntrada,
    GestionRespuesta,
    PaginaGestiones,
    ParametrosGestiones,
)
from motor_cartera.api.operacional import (
    ANTES_DEL_EVENTO,
    DATOS_PERSONALES,
    EN_EL_FUTURO,
    ENTRADA_INVALIDA,
    ERRORES_DEL_REGISTRO,
    LLAVE_REUTILIZADA,
    SIN_CLAVE,
    LlaveDeIdempotencia,
    gestion_respuesta,
    responder,
    traducir,
)
from motor_cartera.config import Config
from motor_cartera.db.modelos import CuentaCanonica
from motor_cartera.db.sesion import sesion_de_lectura
from motor_cartera.historia import cuenta360
from motor_cartera.historia.cuenta360 import CuentaNoEncontrada
from motor_cartera.lifecycle import consultas, registro
from motor_cartera.lifecycle.consultas import GestionNoEncontrada

router = APIRouter(tags=["lifecycle"])

CUENTA_NO_EXISTE = (404, "CUENTA_NO_ENCONTRADA", "No existe una cuenta canonica con ese cuenta_id.")
GESTION_NO_EXISTE = (404, "GESTION_NO_ENCONTRADA", "No existe una gestion con ese gestion_id.")
REGISTRADA = {
    "description": "Se registro la gestion: `Location` apunta a ella.",
}
REPETIDA = {
    "description": "La misma peticion, con la misma Idempotency-Key, ya la habia registrado: no se "
    "registro nada otra vez. Trae `Idempotent-Replayed: true` y la gestion como esta hoy.",
    "model": GestionRespuesta,
}


@router.post(
    "/cuentas/{cuenta_id}/gestiones",
    status_code=201,
    response_model=GestionRespuesta,
    summary="Registra una gestion de cobranza de la cuenta",
    responses={
        201: REGISTRADA,
        200: REPETIDA,
        **errores(
            *SIN_CLAVE,
            CUENTA_NO_EXISTE,
            LLAVE_REUTILIZADA,
            ENTRADA_INVALIDA,
            (
                422,
                "GESTION_INCOHERENTE",
                "Canal, medio, contacto y resultado no tienen sentido juntos: un SIN_RESPUESTA "
                "con contacto, una PROMESA sin contacto, una visita fuera de CAMPO...",
            ),
            (422, "VISITA_INCOHERENTE", "La visita no contiene el momento de su gestion."),
            DATOS_PERSONALES,
            EN_EL_FUTURO,
        ),
    },
)
def registrar_gestion(
    cuenta_id: UUID,
    entrada: GestionEntrada,
    llave: LlaveDeIdempotencia,
    response: Response,
) -> GestionRespuesta:
    """Una accion concreta para cobrar o comunicarse con la cuenta: por donde (`canal` y `medio`),
    con quien se hablo (`nivel_contacto`) y que salio (`resultado`). Una gestion de `CAMPO` es una
    visita y trae su bloque `visita`, con lo que encontro.

    `ocurrido_en` es cuando paso, con su zona horaria; cuando se registro lo pone la base
    (`registrado_en`), y una gestion de hace dos semanas que se registra hoy es valida. Nada se
    sobrescribe: una gestion mal registrada se anula con `POST /gestiones/{gestion_id}/anulaciones`.

    Una promesa o un convenio nacen de la gestion despues, con `POST
    /gestiones/{gestion_id}/promesas` o `/convenios`, si su resultado es `PROMESA` o `CONVENIO`."""
    datos = registro.DatosGestion(
        ocurrido_en=entrada.ocurrido_en,
        canal=entrada.canal,
        medio=entrada.medio,
        nivel_contacto=entrada.nivel_contacto,
        resultado=entrada.resultado,
        actor_ref=entrada.actor_ref,
        observacion=entrada.observacion,
        visita=None
        if entrada.visita is None
        else registro.DatosVisita(
            resultado=entrada.visita.resultado,
            inicio=entrada.visita.inicio,
            fin=entrada.visita.fin,
            observacion=entrada.visita.observacion,
        ),
    )
    try:
        hecho = registro.registrar_gestion(cuenta_id, llave, datos)
    except ERRORES_DEL_REGISTRO as exc:
        raise traducir(exc) from exc
    responder(response, hecho, f"/gestiones/{hecho.recurso_id}")
    return _leer(hecho.recurso_id)


@router.get(
    "/cuentas/{cuenta_id}/gestiones",
    response_model=PaginaGestiones,
    summary="Las gestiones de la cuenta, de la mas reciente a la mas antigua",
    responses=errores(
        *SIN_CLAVE,
        CUENTA_NO_EXISTE,
        (422, "ENTRADA_INVALIDA", "Un filtro no es valido, o la paginacion no lo es."),
    ),
)
def listar_gestiones(
    cuenta_id: UUID,
    request: Request,
    parametros: Annotated[ParametrosGestiones, Query()],
    s: SesionDeLectura,
) -> PaginaGestiones:
    """Por su momento de negocio (`ocurrido_en`), no por cuando se registraron, filtradas en la base
    por dias (`desde` y `hasta`, inclusive, en la zona horaria de la fuente), canal, nivel de
    contacto, resultado y estado. Incluye las anuladas, con su anulacion, salvo `estado=VIGENTE`."""
    config: Config = request.app.state.config
    cuenta = cuenta_de(s, cuenta_id)
    total, pagina = consultas.gestiones_de_cuenta(
        s,
        cuenta,
        zona=config.zona_horaria_fuente,
        desde=parametros.desde,
        hasta=parametros.hasta,
        canal=parametros.canal,
        nivel_contacto=parametros.nivel_contacto,
        resultado=parametros.resultado,
        estado=parametros.estado,
        desplazamiento=parametros.desplazamiento,
        limite=parametros.por_pagina,
    )
    return PaginaGestiones(
        cuenta_id=cuenta.cuenta_id,
        cliente_unico=cuenta.cliente_unico,
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[gestion_respuesta(vista) for vista in pagina],
    )


@router.get(
    "/gestiones/{gestion_id}",
    response_model=GestionRespuesta,
    summary="Una gestion, con su evento, su estado y lo que nacio de ella",
    responses=errores(
        *SIN_CLAVE, GESTION_NO_EXISTE, (422, "ENTRADA_INVALIDA", "El gestion_id no es un UUID.")
    ),
)
def obtener_gestion(gestion_id: UUID, s: SesionDeLectura) -> GestionRespuesta:
    """Su canal, contacto y resultado; cuando paso y cuando se registro; su evento con su
    Idempotency-Key; si fue anulada, y por que; y su visita, su promesa y su convenio, si los
    tiene. El linaje es Gestion → EventoLifecycle → CuentaCanonica."""
    try:
        return gestion_respuesta(consultas.obtener_gestion(s, gestion_id))
    except GestionNoEncontrada as exc:
        raise ErrorDeApi(
            404, "GESTION_NO_ENCONTRADA", f"No existe una gestion con gestion_id {gestion_id}."
        ) from exc


@router.post(
    "/gestiones/{gestion_id}/anulaciones",
    status_code=201,
    response_model=GestionRespuesta,
    summary="Anula una gestion registrada por error, con su visita, su promesa y su convenio",
    responses={
        201: {"description": "Se registro la anulacion: la gestion queda ANULADA."},
        200: {
            "description": "La misma anulacion ya se habia registrado.",
            "model": GestionRespuesta,
        },
        **errores(
            *SIN_CLAVE,
            GESTION_NO_EXISTE,
            (409, "GESTION_YA_ANULADA", "La gestion ya tiene su anulacion, de otra peticion."),
            LLAVE_REUTILIZADA,
            ENTRADA_INVALIDA,
            DATOS_PERSONALES,
            EN_EL_FUTURO,
            ANTES_DEL_EVENTO,
        ),
    },
)
def anular_gestion(
    gestion_id: UUID,
    entrada: CierreEntrada,
    llave: LlaveDeIdempotencia,
    response: Response,
) -> GestionRespuesta:
    """No borra nada: registra un evento `GESTION_ANULADA` que se refiere al de la gestion, con su
    motivo, cuando se decidio y quien. La gestion sigue visible, `ANULADA`, y deja de contar en el
    estado vigente de la cuenta y en la atribucion; su promesa y su convenio quedan anulados con
    ella. Una gestion se anula una sola vez."""
    datos = registro.DatosCierre(entrada.ocurrido_en, entrada.motivo, entrada.actor_ref)
    try:
        hecho = registro.anular_gestion(gestion_id, llave, datos)
    except ERRORES_DEL_REGISTRO as exc:
        raise traducir(exc) from exc
    responder(response, hecho, f"/gestiones/{gestion_id}")
    return _leer(gestion_id)


def cuenta_de(s: Session, cuenta_id: UUID) -> CuentaCanonica:
    try:
        return cuenta360.obtener(s, cuenta_id)
    except CuentaNoEncontrada as exc:
        raise ErrorDeApi(
            404, "CUENTA_NO_ENCONTRADA", f"No existe una cuenta canonica con cuenta_id {cuenta_id}."
        ) from exc


def _leer(gestion_id: UUID) -> GestionRespuesta:
    with sesion_de_lectura() as s:
        return gestion_respuesta(consultas.obtener_gestion(s, gestion_id))
