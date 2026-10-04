"""La orquestacion por HTTP: el flujo de una corrida, sus trabajos, y reanudar un flujo detenido.

La API no ejecuta ningun motor ni mueve ningun flujo: eso lo hace el worker, que toma los trabajos
de la cola durable. Aqui se lee como va cada flujo y cada trabajo, siempre por sus identificadores
publicos, y se pide reanudar un flujo detenido: la peticion crea otra ejecucion de su etapa y su
trabajo, y responde sin esperar a que el worker la ejecute.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, Request
from sqlalchemy import ColumnElement, func
from sqlmodel import Session, select
from sqlmodel.sql.expression import Select

from motor_cartera.api.dependencias import Sesion, buscar_corrida
from motor_cartera.api.errores import ErrorDeApi, errores
from motor_cartera.api.esquemas import (
    EJEMPLO_FLUJO,
    EJEMPLO_FLUJO_DETENIDO,
    EJEMPLO_FLUJO_EN_PROCESO,
    FlujoRespuesta,
    Paginacion,
    PaginaTrabajos,
    TrabajoRespuesta,
)
from motor_cartera.db.modelos import (
    Corrida,
    EjecucionDecision,
    EjecucionRuteo,
    EjecucionTerritorial,
    FlujoOrquestacion,
    TrabajoOrquestacion,
)
from motor_cartera.orquestacion.flujo import (
    FlujoEnProceso,
    FlujoNoReanudable,
    FlujoYaCompletado,
    reanudar_flujo,
)

router = APIRouter(tags=["orquestacion"])

SIN_CLAVE = (
    (401, "API_KEY_AUSENTE", "Falta la cabecera X-API-Key."),
    (401, "API_KEY_INVALIDA", "La API key no es valida."),
)
FLUJO_NO_EXISTE = (404, "FLUJO_NO_ENCONTRADO", "No existe un flujo con ese flujo_id.")
EJEMPLOS_DE_FLUJO = {
    "content": {
        "application/json": {
            "examples": {
                "EN_PROCESO": {"summary": "Todavia avanza", "value": EJEMPLO_FLUJO_EN_PROCESO},
                "COMPLETADO": {"summary": "Llego hasta el ruteo", "value": EJEMPLO_FLUJO},
                "DETENIDO": {"summary": "Se detuvo en una etapa", "value": EJEMPLO_FLUJO_DETENIDO},
            }
        }
    }
}


@router.get(
    "/corridas/{run_id}/flujo",
    response_model=FlujoRespuesta,
    summary="El flujo automatico de una corrida: en que etapa va y como va",
    responses={
        200: {"description": "El flujo, en cualquier estado.", **EJEMPLOS_DE_FLUJO},
        **errores(
            *SIN_CLAVE,
            (404, "CORRIDA_NO_ENCONTRADA", "No existe una corrida con ese run_id."),
            (
                404,
                "FLUJO_NO_ENCONTRADO",
                "La corrida no tiene flujo: se publico antes de v0.5.0, o con el CLI.",
            ),
            (422, "ENTRADA_INVALIDA", "El run_id no es un UUID."),
        ),
    },
)
def obtener_flujo_de_corrida(run_id: UUID, s: Sesion) -> FlujoRespuesta:
    """Es como se sigue una corrida subida por `POST /corridas`: el flujo la lleva de la ingesta a
    la decision, a la organizacion territorial y al ruteo, sin que el cliente pida cada etapa.
    Consultalo hasta que deje de estar `EN_PROCESO`.

    `COMPLETADO` quiere decir que el ruteo termino `EXITOSA`, y trae el identificador de cada
    ejecucion. `DETENIDO` quiere decir que una etapa termino sin poder continuar: `etapa` dice cual
    y `detalle` por que. Una etapa downstream detenida se reintenta con
    `POST /flujos/{flujo_id}/reanudar`.

    **404 `FLUJO_NO_ENCONTRADO` si la corrida no tiene flujo**: se publico antes de v0.5.0, o con el
    CLI, que no encadena etapas.
    """
    corrida = buscar_corrida(s, run_id)
    encontrado = _buscar_flujo(s, FlujoOrquestacion.corrida_id == corrida.id)
    if encontrado is None:
        raise ErrorDeApi(
            404,
            "FLUJO_NO_ENCONTRADO",
            f"La corrida {run_id} no tiene un flujo automatico: se publico antes de v0.5.0, o con "
            "el CLI.",
            run_id=run_id,
        )
    return encontrado[1]


@router.get(
    "/flujos/{flujo_id}",
    response_model=FlujoRespuesta,
    summary="Un flujo automatico: en que etapa va, como va y sus ejecuciones",
    responses={
        200: {"description": "El flujo, en cualquier estado.", **EJEMPLOS_DE_FLUJO},
        **errores(
            *SIN_CLAVE, FLUJO_NO_EXISTE, (422, "ENTRADA_INVALIDA", "El flujo_id no es un UUID.")
        ),
    },
)
def obtener_flujo(flujo_id: UUID, s: Sesion) -> FlujoRespuesta:
    """Lo mismo que `GET /corridas/{run_id}/flujo`, por el identificador del flujo. Los
    identificadores de cada ejecucion son null hasta que el flujo llega a su etapa."""
    return _flujo_o_404(s, flujo_id)[1]


@router.get(
    "/flujos/{flujo_id}/trabajos",
    response_model=PaginaTrabajos,
    summary="Los trabajos de un flujo, en el orden en que entraron a la cola",
    responses=errores(
        *SIN_CLAVE,
        FLUJO_NO_EXISTE,
        (
            422,
            "ENTRADA_INVALIDA",
            "El flujo_id no es un UUID, o la paginacion esta fuera de rango.",
        ),
    ),
)
def listar_trabajos(
    flujo_id: UUID, paginacion: Annotated[Paginacion, Query()], s: Sesion
) -> PaginaTrabajos:
    """Un trabajo por cada ejecucion que el flujo pidio, incluidas las de una etapa que se reanudo:
    la fallida conserva el suyo. En el orden en que entraron a la cola.

    El estado de un trabajo es el de su entrega, no el de su motor: `COMPLETADO` quiere decir que su
    recurso termino, `EXITOSA` o no, y `FALLIDO`, que agoto sus intentos sin que terminara.
    `intentos` dice cuantas veces lo tomo un worker: mas de una, si un worker murio o fallo.
    """
    interno, flujo = _flujo_o_404(s, flujo_id)
    del_flujo = TrabajoOrquestacion.flujo_id == interno
    total = s.exec(select(func.count()).select_from(TrabajoOrquestacion).where(del_flujo)).one()
    filas = s.exec(
        _trabajos(del_flujo)
        .order_by(TrabajoOrquestacion.creado_en, TrabajoOrquestacion.id)
        .offset(paginacion.desplazamiento)
        .limit(paginacion.por_pagina)
    ).all()
    return PaginaTrabajos(
        flujo_id=flujo_id,
        run_id=flujo.run_id,
        total=total,
        pagina=paginacion.pagina,
        por_pagina=paginacion.por_pagina,
        elementos=[_respuesta_trabajo(*fila) for fila in filas],
    )


@router.get(
    "/trabajos/{trabajo_id}",
    response_model=TrabajoRespuesta,
    summary="Un trabajo de la cola durable: su estado, sus intentos y su lease",
    responses=errores(
        *SIN_CLAVE,
        (404, "TRABAJO_NO_ENCONTRADO", "No existe un trabajo con ese trabajo_id."),
        (422, "ENTRADA_INVALIDA", "El trabajo_id no es un UUID."),
    ),
)
def obtener_trabajo(trabajo_id: UUID, s: Sesion) -> TrabajoRespuesta:
    """Cualquier trabajo, de un flujo (`flujo_id`) o de una etapa pedida a mano (`flujo_id` null).
    `objetivo_run_id` es el identificador publico de su recurso; `tipo` dice de cual.

    Mientras esta `EJECUTANDO`, `lease_hasta` dice hasta cuando es de su worker: el latido lo
    renueva, y si vence, otro worker lo toma. El worker que lo tiene no se publica.
    """
    fila = s.exec(_trabajos(TrabajoOrquestacion.trabajo_id == trabajo_id)).first()
    if fila is None:
        raise ErrorDeApi(
            404,
            "TRABAJO_NO_ENCONTRADO",
            f"No existe un trabajo con trabajo_id {trabajo_id}.",
        )
    return _respuesta_trabajo(*fila)


@router.post(
    "/flujos/{flujo_id}/reanudar",
    response_model=FlujoRespuesta,
    summary="Reintenta la etapa en que se detuvo un flujo, con otra ejecucion",
    responses={
        200: {
            "description": "El flujo vuelve a estar EN_PROCESO, apuntando a la nueva ejecucion de "
            "su etapa, que ya tiene su trabajo en la cola.",
            "content": {"application/json": {"example": EJEMPLO_FLUJO_EN_PROCESO}},
        },
        **errores(
            *SIN_CLAVE,
            FLUJO_NO_EXISTE,
            (409, "FLUJO_EN_PROCESO", "El flujo sigue EN_PROCESO: no hay nada que reanudar."),
            (409, "FLUJO_YA_COMPLETADO", "El flujo ya llego hasta el ruteo."),
            (
                409,
                "FLUJO_NO_REANUDABLE",
                "El flujo se detuvo en la ingesta (se vuelve a subir el archivo), o su etapa no "
                "tiene una ejecucion FALLIDA que reintentar.",
            ),
            (422, "ENTRADA_INVALIDA", "El flujo_id no es un UUID."),
        ),
    },
)
def reanudar(flujo_id: UUID, request: Request, s: Sesion) -> FlujoRespuesta:
    """Para un flujo `DETENIDO` en la decision, la organizacion territorial o el ruteo: crea otra
    ejecucion de esa etapa y su trabajo, y el flujo vuelve a estar `EN_PROCESO`. Responde sin
    esperar a que el worker la ejecute. La ejecucion que fallo no se reabre ni se borra: queda en el
    historial de su etapa, y su trabajo en `GET /flujos/{flujo_id}/trabajos`.

    **409 `FLUJO_NO_REANUDABLE` si se detuvo en la ingesta**: una corrida terminada es evidencia y
    no se reabre. Vuelve a subir el archivo: eso crea otra corrida, con otro flujo.
    """
    _, flujo = _flujo_o_404(s, flujo_id)
    try:
        reanudar_flujo(s, flujo_id, config=request.app.state.config)
    except FlujoEnProceso as exc:
        raise ErrorDeApi(
            409,
            "FLUJO_EN_PROCESO",
            f"El flujo sigue EN_PROCESO, en la etapa {exc.etapa}: no hay nada que reanudar. "
            f"Consulta /flujos/{flujo_id}.",
            run_id=flujo.run_id,
        ) from exc
    except FlujoYaCompletado as exc:
        raise ErrorDeApi(
            409,
            "FLUJO_YA_COMPLETADO",
            "El flujo ya esta COMPLETADO: llego hasta el ruteo y no hay nada que reanudar.",
            run_id=flujo.run_id,
        ) from exc
    except FlujoNoReanudable as exc:
        raise ErrorDeApi(409, "FLUJO_NO_REANUDABLE", str(exc), run_id=flujo.run_id) from exc
    return _flujo_o_404(s, flujo_id)[1]


def _flujo_o_404(s: Session, flujo_id: UUID) -> tuple[int, FlujoRespuesta]:
    encontrado = _buscar_flujo(s, FlujoOrquestacion.flujo_id == flujo_id)
    if encontrado is None:
        raise ErrorDeApi(404, "FLUJO_NO_ENCONTRADO", f"No existe un flujo con flujo_id {flujo_id}.")
    return encontrado


def _buscar_flujo(s: Session, condicion: ColumnElement) -> tuple[int, FlujoRespuesta] | None:
    """El id interno del flujo que cumple `condicion` y el flujo como lo ve un cliente, con los
    identificadores publicos de su corrida y de sus ejecuciones, en una sola consulta. El id interno
    solo sirve para buscar sus trabajos; no sale en ninguna respuesta."""
    fila = s.exec(
        select(
            FlujoOrquestacion,
            Corrida.run_id,
            EjecucionDecision.decision_run_id,
            EjecucionTerritorial.territorial_run_id,
            EjecucionRuteo.ruteo_run_id,
        )
        .select_from(FlujoOrquestacion)
        .join(Corrida, FlujoOrquestacion.corrida_id == Corrida.id)
        .outerjoin(
            EjecucionDecision, FlujoOrquestacion.ejecucion_decision_id == EjecucionDecision.id
        )
        .outerjoin(
            EjecucionTerritorial,
            FlujoOrquestacion.ejecucion_territorial_id == EjecucionTerritorial.id,
        )
        .outerjoin(EjecucionRuteo, FlujoOrquestacion.ejecucion_ruteo_id == EjecucionRuteo.id)
        .where(condicion)
    ).first()
    if fila is None:
        return None
    flujo, run_id, decision_run_id, territorial_run_id, ruteo_run_id = fila
    return flujo.id, FlujoRespuesta(
        flujo_id=flujo.flujo_id,
        run_id=run_id,
        estado=flujo.estado,
        etapa=flujo.etapa,
        decision_run_id=decision_run_id,
        territorial_run_id=territorial_run_id,
        ruteo_run_id=ruteo_run_id,
        creado_en=flujo.creado_en,
        actualizado_en=flujo.actualizado_en,
        terminado_en=flujo.terminado_en,
        detalle=flujo.detalle,
    )


def _trabajos(condicion: ColumnElement) -> Select[Any]:
    """Los trabajos que cumplen `condicion`, con el flujo_id publico de su flujo, si es de uno, y el
    identificador publico de su recurso: el de la unica de las cuatro tablas a la que apunta."""
    return (
        select(
            TrabajoOrquestacion,
            FlujoOrquestacion.flujo_id,
            func.coalesce(
                Corrida.run_id,
                EjecucionDecision.decision_run_id,
                EjecucionTerritorial.territorial_run_id,
                EjecucionRuteo.ruteo_run_id,
            ),
        )
        .select_from(TrabajoOrquestacion)
        .outerjoin(FlujoOrquestacion, TrabajoOrquestacion.flujo_id == FlujoOrquestacion.id)
        .outerjoin(Corrida, TrabajoOrquestacion.corrida_id == Corrida.id)
        .outerjoin(
            EjecucionDecision, TrabajoOrquestacion.ejecucion_decision_id == EjecucionDecision.id
        )
        .outerjoin(
            EjecucionTerritorial,
            TrabajoOrquestacion.ejecucion_territorial_id == EjecucionTerritorial.id,
        )
        .outerjoin(EjecucionRuteo, TrabajoOrquestacion.ejecucion_ruteo_id == EjecucionRuteo.id)
        .where(condicion)
    )


def _respuesta_trabajo(
    trabajo: TrabajoOrquestacion, flujo_id: UUID | None, objetivo_run_id: UUID
) -> TrabajoRespuesta:
    """El trabajo como lo ve un cliente: sin su worker ni ningun id interno."""
    return TrabajoRespuesta(
        trabajo_id=trabajo.trabajo_id,
        flujo_id=flujo_id,
        tipo=trabajo.tipo,
        estado=trabajo.estado,
        objetivo_run_id=objetivo_run_id,
        intentos=trabajo.intentos,
        max_intentos=trabajo.max_intentos,
        creado_en=trabajo.creado_en,
        disponible_desde=trabajo.disponible_desde,
        tomado_en=trabajo.tomado_en,
        latido_en=trabajo.latido_en,
        lease_hasta=trabajo.lease_hasta,
        terminado_en=trabajo.terminado_en,
        ultimo_error=trabajo.ultimo_error,
    )
