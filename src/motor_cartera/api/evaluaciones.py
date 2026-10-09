"""La evaluacion de las promesas por HTTP: pedirla a una fecha de corte y leer lo que concluyo.

La API no evalua: el POST deja la ejecucion EN_PROCESO y su trabajo EVALUACION_PROMESAS en la cola
durable, en una transaccion, y un worker la ejecuta para todas las promesas de la cartera. La fecha
de corte es obligatoria: nunca sale del reloj, y la misma fecha con los mismos datos da lo mismo.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import text

from motor_cartera.api.dependencias import Sesion, SesionDeLectura
from motor_cartera.api.errores import ErrorDeApi, errores
from motor_cartera.api.esquemas_lifecycle import (
    ConteosEvaluacionRespuesta,
    EjecucionEvaluacionRespuesta,
    EvaluacionEnEjecucionRespuesta,
    EvaluacionPromesasEntrada,
    PaginaEvaluaciones,
    PaginaEvaluacionesDePromesas,
    ParametrosEvaluaciones,
    ParametrosEvaluacionesDePromesas,
)
from motor_cartera.api.operacional import SIN_CLAVE
from motor_cartera.config import Config
from motor_cartera.evaluacion import consultas
from motor_cartera.evaluacion.consultas import EjecucionVista, EvaluacionNoEncontrada
from motor_cartera.evaluacion.ejecuciones import EvaluacionEnProceso, abrir

router = APIRouter(tags=["lifecycle"])

NO_EXISTE = (
    404,
    "EVALUACION_NO_ENCONTRADA",
    "No existe una evaluacion de promesas con ese evaluacion_run_id.",
)


@router.post(
    "/evaluaciones-promesas",
    status_code=201,
    response_model=EjecucionEvaluacionRespuesta,
    summary="Pide evaluar las promesas de la cartera a una fecha de corte",
    responses={
        201: {
            "description": "La evaluacion quedo EN_PROCESO, con su trabajo en la cola durable: un "
            "worker la ejecuta. `Location` apunta a ella."
        },
        **errores(
            *SIN_CLAVE,
            (
                409,
                "EVALUACION_EN_PROCESO",
                "La cartera ya se esta evaluando a esa fecha de corte: tiene una EN_PROCESO.",
            ),
            (422, "AS_OF_FUTURO", "La fecha de corte es posterior a hoy, en la zona de la fuente."),
            (422, "ENTRADA_INVALIDA", "Falta as_of, o no es AAAA-MM-DD."),
        ),
    },
)
def pedir_evaluacion(
    entrada: EvaluacionPromesasEntrada, request: Request, response: Response, s: Sesion
) -> EjecucionEvaluacionRespuesta:
    """Evalua, en un solo trabajo, cada promesa de la cartera del sistema acordada hasta el final de
    `as_of`, con `evaluacion-promesa/v1`: `PENDIENTE`, `CUMPLIDA`, `PARCIAL`, `INCUMPLIDA`,
    `CANCELADA` o `NO_EVALUABLE`. Observa los movimientos economicos interpretados de la cuenta de
    cada promesa; no afirma que la promesa haya producido el pago.

    **201 quiere decir que la evaluacion se creo, no que termino.** Si la misma fecha de corte ya se
    evaluo con exactamente las mismas entradas, la nueva termina `FALLIDA` con `YA_EVALUADA` y la
    anterior sigue siendo la que vale."""
    config: Config = request.app.state.config
    hoy = s.execute(
        text("SELECT (now() AT TIME ZONE :zona)::date"), {"zona": config.zona_horaria_fuente}
    ).scalar_one()
    if entrada.as_of > hoy:
        raise ErrorDeApi(
            422,
            "AS_OF_FUTURO",
            f"La fecha de corte {entrada.as_of.isoformat()} es posterior a hoy "
            f"({hoy.isoformat()} en {config.zona_horaria_fuente}): no se evalua lo que no ha "
            "pasado.",
        )
    try:
        ejecucion, _ = abrir(
            s,
            config.despacho_id,
            config.cartera_id,
            entrada.as_of,
            zona=config.zona_horaria_fuente,
            max_intentos=config.worker_max_intentos,
            reusar=False,
        )
    except EvaluacionEnProceso as exc:
        raise ErrorDeApi(409, "EVALUACION_EN_PROCESO", str(exc)) from exc
    s.commit()
    evaluacion_run_id = ejecucion.evaluacion_run_id
    response.headers["Location"] = f"/evaluaciones-promesas/{evaluacion_run_id}"
    return ejecucion_respuesta(consultas.obtener(s, evaluacion_run_id))


@router.get(
    "/evaluaciones-promesas",
    response_model=PaginaEvaluaciones,
    summary="Las evaluaciones de promesas de la cartera, por fecha de corte",
    responses=errores(
        *SIN_CLAVE, (422, "ENTRADA_INVALIDA", "Un filtro no es valido, o la paginacion no lo es.")
    ),
)
def listar_evaluaciones(
    request: Request, parametros: Annotated[ParametrosEvaluaciones, Query()], s: SesionDeLectura
) -> PaginaEvaluaciones:
    """La de fecha de corte mas reciente primero; dentro de una fecha, la mas reciente primero."""
    config: Config = request.app.state.config
    total, pagina = consultas.listar(
        s,
        despacho_id=config.despacho_id,
        cartera_id=config.cartera_id,
        as_of=parametros.as_of,
        estado=parametros.estado,
        desplazamiento=parametros.desplazamiento,
        limite=parametros.por_pagina,
    )
    return PaginaEvaluaciones(
        despacho_id=config.despacho_id,
        cartera_id=config.cartera_id,
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[ejecucion_respuesta(vista) for vista in pagina],
    )


@router.get(
    "/evaluaciones-promesas/{evaluacion_run_id}",
    response_model=EjecucionEvaluacionRespuesta,
    summary="Una evaluacion de promesas: su fecha de corte, su estado y cuantas de cada clase",
    responses=errores(
        *SIN_CLAVE, NO_EXISTE, (422, "ENTRADA_INVALIDA", "El evaluacion_run_id no es un UUID.")
    ),
)
def obtener_evaluacion(evaluacion_run_id: UUID, s: SesionDeLectura) -> EjecucionEvaluacionRespuesta:
    return ejecucion_respuesta(_vista(s, evaluacion_run_id))


@router.get(
    "/evaluaciones-promesas/{evaluacion_run_id}/promesas",
    response_model=PaginaEvaluacionesDePromesas,
    summary="Lo que la evaluacion concluyo de cada promesa, y por que",
    responses=errores(
        *SIN_CLAVE,
        NO_EXISTE,
        (422, "ENTRADA_INVALIDA", "El estado no es valido, o la paginacion no lo es."),
    ),
)
def promesas_evaluadas(
    evaluacion_run_id: UUID,
    parametros: Annotated[ParametrosEvaluacionesDePromesas, Query()],
    s: SesionDeLectura,
) -> PaginaEvaluacionesDePromesas:
    """Cada promesa con su estado, el monto observado, sus movimientos compatibles y sus motivos.
    Vacia mientras la evaluacion sigue EN_PROCESO, o si fallo."""
    vista = _vista(s, evaluacion_run_id)
    total, pagina = consultas.evaluaciones_de(
        s,
        vista.ejecucion,
        estado=parametros.estado,
        desplazamiento=parametros.desplazamiento,
        limite=parametros.por_pagina,
    )
    return PaginaEvaluacionesDePromesas(
        evaluacion_run_id=evaluacion_run_id,
        as_of=vista.ejecucion.as_of,
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[
            EvaluacionEnEjecucionRespuesta(
                promesa_id=e.promesa_id,
                cuenta_id=e.cuenta_id,
                cliente_unico=e.cliente_unico,
                monto_prometido=e.monto_prometido,
                fecha_limite=e.fecha_limite,
                estado=e.evaluacion.estado,
                monto_observado=e.evaluacion.monto_observado,
                movimientos_compatibles=e.evaluacion.movimientos_compatibles,
                primer_movimiento_en=e.evaluacion.primer_movimiento_en,
                ultimo_movimiento_en=e.evaluacion.ultimo_movimiento_en,
                motivos=e.evaluacion.motivos,
            )
            for e in pagina
        ],
    )


def _vista(s, evaluacion_run_id: UUID) -> EjecucionVista:
    try:
        return consultas.obtener(s, evaluacion_run_id)
    except EvaluacionNoEncontrada as exc:
        raise ErrorDeApi(
            404,
            "EVALUACION_NO_ENCONTRADA",
            f"No existe una evaluacion de promesas con evaluacion_run_id {evaluacion_run_id}.",
        ) from exc


def ejecucion_respuesta(vista: EjecucionVista) -> EjecucionEvaluacionRespuesta:
    e = vista.ejecucion
    return EjecucionEvaluacionRespuesta(
        evaluacion_run_id=e.evaluacion_run_id,
        version_evaluacion=e.version_evaluacion,
        estado=e.estado,
        resultado=e.resultado,
        despacho_id=e.despacho_id,
        cartera_id=e.cartera_id,
        as_of=e.as_of,
        zona_horaria=e.zona_horaria,
        firma_entrada=e.firma_entrada,
        horizonte_pagos=e.horizonte_pagos,
        conteos=ConteosEvaluacionRespuesta(
            promesas_evaluadas=e.promesas_evaluadas,
            pendientes=e.pendientes,
            cumplidas=e.cumplidas,
            parciales=e.parciales,
            incumplidas=e.incumplidas,
            canceladas=e.canceladas,
            no_evaluables=e.no_evaluables,
        ),
        monto_prometido=e.monto_prometido,
        monto_observado=e.monto_observado,
        trabajo_id=vista.trabajo_id,
        iniciada_en=e.iniciada_en,
        terminada_en=e.terminada_en,
        detalle=e.detalle,
    )
