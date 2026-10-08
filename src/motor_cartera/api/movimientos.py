"""Los movimientos economicos canonicos por HTTP: lo que el motor de pagos interpreta, con su por
que y su evidencia.

`GET /movimientos` lista los de la interpretacion vigente de cada ventana; `/movimientos/{id}` trae
uno con la observacion que lo funda, su archivo original y, si se concilio con una cuenta, su
contexto entre los snapshots de esa cuenta; `/movimientos/{id}/observaciones`, cada pago observado
que lo sustenta. Asi se responde por que un movimiento vale una sola vez aunque la fuente lo haya
reportado dos.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request

from motor_cartera.api.cuentas import pago_respuesta
from motor_cartera.api.dependencias import SesionDeLectura
from motor_cartera.api.errores import ErrorDeApi, errores
from motor_cartera.api.esquemas import (
    ArtefactoRespuesta,
    MovimientoDetalleRespuesta,
    ObservacionDeMovimientoRespuesta,
    PaginaMovimientos,
    PaginaObservacionesDeMovimiento,
    ParametrosMovimientos,
    ParametrosObservacionesDeMovimiento,
)
from motor_cartera.api.motor_pagos import (
    contexto_respuesta,
    motivos_respuesta,
    movimiento_respuesta,
)
from motor_cartera.config import Config
from motor_cartera.historia import cuenta360
from motor_cartera.historia.cuenta360 import CuentaNoEncontrada, PagoVisto
from motor_cartera.motor_pagos import consultas
from motor_cartera.motor_pagos.consultas import MovimientoNoEncontrado, MovimientoVisto
from motor_cartera.motor_pagos.reglas import VERSION_MOTOR_PAGOS

router = APIRouter(prefix="/movimientos", tags=["movimientos"])

SIN_CLAVE = (
    (401, "API_KEY_AUSENTE", "Falta la cabecera X-API-Key."),
    (401, "API_KEY_INVALIDA", "La API key no es valida."),
)
NO_EXISTE = (
    404,
    "MOVIMIENTO_NO_ENCONTRADO",
    "Ninguna ejecucion de esa version del motor publico un movimiento con ese movimiento_id.",
)
VERSION = Query(
    default=VERSION_MOTOR_PAGOS, max_length=32, description="Por omision, la del servicio."
)


@router.get(
    "",
    response_model=PaginaMovimientos,
    summary="Los movimientos economicos vigentes de la cartera del sistema",
    responses=errores(
        *SIN_CLAVE,
        (404, "CUENTA_NO_ENCONTRADA", "No existe una cuenta canonica con ese cuenta_id."),
        (422, "ENTRADA_INVALIDA", "Un filtro no es valido, o la paginacion no lo es."),
    ),
)
def listar(
    request: Request, parametros: Annotated[ParametrosMovimientos, Query()], s: SesionDeLectura
) -> PaginaMovimientos:
    """Los de la interpretacion vigente de cada ventana, del mas reciente al mas antiguo por
    recepcion. Se filtran por cliente (`cliente_unico` o `cuenta_id`), por dias de recepcion
    (`desde` y `hasta`, inclusive), por `tipo` y por `estado_conciliacion`. Con un cliente, la
    consulta entra por el indice de los movimientos de una cuenta."""
    config: Config = request.app.state.config
    cliente = parametros.cliente_unico
    if parametros.cuenta_id is not None:
        cuenta = _cuenta(s, parametros.cuenta_id)
        if cliente is not None and cliente != cuenta.cliente_unico:
            raise ErrorDeApi(
                422,
                "ENTRADA_INVALIDA",
                f"La cuenta {parametros.cuenta_id} es de {cuenta.cliente_unico}, no de {cliente}.",
            )
        cliente = cuenta.cliente_unico
    total, pagina = consultas.listar_movimientos(
        s,
        despacho_id=config.despacho_id,
        cartera_id=config.cartera_id,
        version=parametros.version,
        cliente_unico=cliente,
        desde=parametros.desde,
        hasta=parametros.hasta,
        tipo=None if parametros.tipo is None else parametros.tipo.value,
        estado_conciliacion=(
            None if parametros.estado_conciliacion is None else parametros.estado_conciliacion.value
        ),
        desplazamiento=parametros.desplazamiento,
        limite=parametros.por_pagina,
    )
    return PaginaMovimientos(
        version_motor=parametros.version,
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[movimiento_respuesta(visto) for visto in pagina],
    )


@router.get(
    "/{movimiento_id}",
    response_model=MovimientoDetalleRespuesta,
    summary="Un movimiento: que es, por que, de que observaciones y archivos sale y donde cae",
    responses=errores(
        *SIN_CLAVE, NO_EXISTE, (422, "ENTRADA_INVALIDA", "El movimiento_id no es un UUID.")
    ),
)
def obtener(
    movimiento_id: UUID, s: SesionDeLectura, version: str = VERSION
) -> MovimientoDetalleRespuesta:
    """El de la interpretacion vigente de su ventana. Si una interpretacion posterior dejo de
    fundarlo (por ejemplo, porque llego otra observacion que lo volvio ambiguo), se devuelve el de
    la ejecucion mas reciente que lo publico, con `vigente=false`.

    `representante` es la observacion que lo funda, con su dataset, su fila y su archivo original;
    `observaciones` dice cuantas lo sustentan, y `/observaciones` las lista todas. Si se concilio
    con una cuenta, `contexto_temporal` dice entre que snapshots de la cuenta cae: el saldo de antes
    y el de despues se pueden mirar, pero su diferencia no se atribuye al pago."""
    visto = _movimiento(s, movimiento_id, version)
    detalle = consultas.detalle(s, visto)
    r = detalle.representante
    return MovimientoDetalleRespuesta(
        **movimiento_respuesta(visto).model_dump(),
        clasificacion=r.resultado.clasificacion,
        motivos=motivos_respuesta(r.resultado.motivos),
        representante=_observacion(r, detalle.original),
        contexto_temporal=(
            None
            if detalle.contexto is None
            else contexto_respuesta(detalle.contexto, detalle.snapshots)
        ),
    )


@router.get(
    "/{movimiento_id}/observaciones",
    response_model=PaginaObservacionesDeMovimiento,
    summary="Los pagos observados que sustentan un movimiento, con su archivo y su fila",
    responses=errores(
        *SIN_CLAVE,
        NO_EXISTE,
        (422, "ENTRADA_INVALIDA", "El movimiento_id no es un UUID, o la paginacion no es valida."),
    ),
)
def observaciones(
    movimiento_id: UUID,
    paginacion: Annotated[ParametrosObservacionesDeMovimiento, Query()],
    s: SesionDeLectura,
) -> PaginaObservacionesDeMovimiento:
    """Su representante primero y despues sus duplicados exactos: identicos en sus 23 campos, el
    mismo hecho reportado otra vez. Por eso el movimiento cuenta una vez. Cada uno, con la
    ingesta que lo acepto, su dataset conformado, su fila y el archivo original con su SHA-256."""
    visto = _movimiento(s, movimiento_id, paginacion.version)
    total, pagina = consultas.observaciones_de(
        s, visto, desplazamiento=paginacion.desplazamiento, limite=paginacion.por_pagina
    )
    return PaginaObservacionesDeMovimiento(
        movimiento_id=movimiento_id,
        motor_pagos_run_id=visto.motor_pagos_run_id,
        total=total,
        pagina=paginacion.pagina,
        por_pagina=paginacion.por_pagina,
        elementos=[_observacion(o.visto, o.original) for o in pagina],
    )


def _movimiento(s, movimiento_id: UUID, version: str) -> MovimientoVisto:
    try:
        return consultas.obtener_movimiento(s, movimiento_id, version)
    except MovimientoNoEncontrado as exc:
        raise ErrorDeApi(
            404,
            NO_EXISTE[1],
            f"Ninguna ejecucion de {version} publico un movimiento con movimiento_id "
            f"{movimiento_id}.",
        ) from exc


def _cuenta(s, cuenta_id: UUID):
    try:
        return cuenta360.obtener(s, cuenta_id)
    except CuentaNoEncontrada as exc:
        raise ErrorDeApi(
            404, "CUENTA_NO_ENCONTRADA", f"No existe una cuenta canonica con cuenta_id {cuenta_id}."
        ) from exc


def _observacion(visto, original) -> ObservacionDeMovimientoRespuesta:
    return ObservacionDeMovimientoRespuesta(
        clasificacion=visto.resultado.clasificacion,
        motivos=motivos_respuesta(visto.resultado.motivos),
        pago_observado=pago_respuesta(PagoVisto(visto.pago, visto.pagos_run_id, visto.dataset_id)),
        artefacto_original=ArtefactoRespuesta.model_validate(original),
    )
