"""El Decision Engine por HTTP: pedir que se decida una corrida publicada y consultar lo que se
decidio.

La API no decide nada, ni espera a que se decida. El POST deja la ejecucion EN_PROCESO y su trabajo
en la cola durable, en una sola transaccion, y un worker la decide. Las reglas, la transaccion que
publica todas las decisiones o ninguna y la garantia de decidir una corrida con exito una sola vez
por version viven en el servicio de `decision.ejecuciones`. Aqui solo se elige la corrida o la
ejecucion, el codigo HTTP y la forma de la respuesta.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from sqlmodel import Session, func, select

from motor_cartera.api.dependencias import Sesion, buscar_corrida
from motor_cartera.api.errores import ErrorDeApi, errores
from motor_cartera.api.esquemas import (
    EJEMPLO_EJECUCION,
    EJEMPLO_EJECUCION_EN_PROCESO,
    EJEMPLO_EJECUCION_FALLIDA,
    DecisionCuentaRespuesta,
    EjecucionDecisionRespuesta,
    Paginacion,
    PaginaDecisionCuentas,
    PaginaEjecucionesDecision,
)
from motor_cartera.db.modelos import (
    Corrida,
    Cuenta,
    DecisionCuenta,
    EjecucionDecision,
    EstadoDecision,
)
from motor_cartera.decision.ejecuciones import (
    CorridaNoDecidible,
    DecisionEnProceso,
    DecisionYaGenerada,
)
from motor_cartera.decision.reglas import VERSION_REGLAS_DECISION
from motor_cartera.orquestacion.flujo import FlujoDetenido, FlujoEnProceso, encolar_decision

router = APIRouter(tags=["decisiones"])

SIN_CLAVE = (
    (401, "API_KEY_AUSENTE", "Falta la cabecera X-API-Key."),
    (401, "API_KEY_INVALIDA", "La API key no es valida."),
)
CORRIDA_NO_EXISTE = (404, "CORRIDA_NO_ENCONTRADA", "No existe una corrida con ese run_id.")
DECISION_NO_EXISTE = (
    404,
    "DECISION_NO_ENCONTRADA",
    "No existe una ejecucion con ese decision_run_id.",
)
EJEMPLOS_DE_EJECUCION = {
    "content": {
        "application/json": {
            "examples": {
                "EN_PROCESO": {
                    "summary": "El worker todavia no la termina",
                    "value": EJEMPLO_EJECUCION_EN_PROCESO,
                },
                "EXITOSA": {"summary": "Decidio todas las cuentas", "value": EJEMPLO_EJECUCION},
                "FALLIDA": {
                    "summary": "El motor fallo y no publico ninguna decision",
                    "value": EJEMPLO_EJECUCION_FALLIDA,
                },
            }
        }
    }
}


@router.post(
    "/corridas/{run_id}/decisiones",
    status_code=201,
    response_model=EjecucionDecisionRespuesta,
    summary=f"Pide decidir cada cuenta de una corrida publicada con {VERSION_REGLAS_DECISION}",
    responses={
        201: {
            "description": "La ejecucion quedo creada EN_PROCESO, con su trabajo en la cola "
            "durable: un worker la decide. `Location` apunta a ella; consultala hasta que termine "
            "EXITOSA, con una decision por cuenta, o FALLIDA, sin ninguna.",
            "content": {"application/json": {"example": EJEMPLO_EJECUCION_EN_PROCESO}},
        },
        **errores(
            *SIN_CLAVE,
            CORRIDA_NO_EXISTE,
            (
                409,
                "CORRIDA_NO_PUBLICADA",
                "La corrida no termino EXITOSA: no publico una cartera que decidir.",
            ),
            (
                409,
                "DECISION_YA_GENERADA",
                f"La corrida ya tiene una ejecucion EXITOSA con {VERSION_REGLAS_DECISION}; "
                "GET /corridas/{run_id}/decisiones dice cual.",
            ),
            (
                409,
                "DECISION_EN_PROCESO",
                f"La corrida ya se esta decidiendo con {VERSION_REGLAS_DECISION}: tiene una "
                "ejecucion EN_PROCESO.",
            ),
            (
                409,
                "FLUJO_EN_PROCESO",
                "La corrida es de un flujo automatico que todavia la va a decidir.",
            ),
            (
                409,
                "FLUJO_DETENIDO",
                "La corrida es de un flujo que se detuvo en la decision: se reintenta reanudando "
                "el flujo.",
            ),
            (422, "ENTRADA_INVALIDA", "El run_id no es un UUID."),
        ),
    },
)
def crear_ejecucion(
    run_id: UUID, request: Request, response: Response, s: Sesion
) -> EjecucionDecisionRespuesta:
    """Pide decidir cada cuenta de una corrida `EXITOSA` con las reglas de este servicio. La
    peticion no decide: deja la ejecucion `EN_PROCESO` y su trabajo en la cola durable, en una sola
    transaccion, y responde de inmediato. Un worker la decide; consulta `Location` hasta que deje de
    estar `EN_PROCESO`. No recibe cuerpo ni elige version: la respuesta dice con cual se decide
    (`version_reglas`).

    **201 quiere decir que la ejecucion se creo, no que el motor termino**, y menos que tuvo exito.
    Al terminar, su `estado` dice como: `EXITOSA`, con una decision por cada cuenta, o `FALLIDA`,
    sin ninguna y con el motivo en `detalle`. Una `FALLIDA` se reintenta con otro POST, que crea
    otra ejecucion.

    **409 `DECISION_YA_GENERADA` si la corrida ya tiene sus decisiones publicadas** con esta
    version: decidirla otra vez las duplicaria. **409 `DECISION_EN_PROCESO` si ya se esta
    decidiendo**: a lo mas hay una ejecucion activa por corrida y version. Ninguno dice cual
    ejecucion fue; lo dice `GET /corridas/{run_id}/decisiones`.

    **409 `CORRIDA_NO_PUBLICADA` si la corrida no termino `EXITOSA`**: no hay cartera que
    decidir, y no se registra ninguna ejecucion.

    **Una corrida subida por `POST /corridas` ya tiene su flujo automatico**, que la decide sin que
    nadie lo pida: mientras el flujo va a hacerlo, 409 `FLUJO_EN_PROCESO`, y si el flujo se detuvo
    en la decision, 409 `FLUJO_DETENIDO`: se reintenta con `POST /flujos/{flujo_id}/reanudar`.
    """
    corrida = buscar_corrida(s, run_id)
    corrida_id, corrida_run_id = corrida.id, corrida.run_id
    try:
        ejecucion = encolar_decision(s, corrida_id, config=request.app.state.config)
    except FlujoEnProceso as exc:
        raise ErrorDeApi(
            409,
            "FLUJO_EN_PROCESO",
            f"La corrida es del flujo {exc.flujo_id}, que sigue EN_PROCESO en {exc.etapa} y la va "
            f"a decidir por su cuenta. Consulta /corridas/{corrida_run_id}/flujo.",
            run_id=corrida_run_id,
        ) from exc
    except FlujoDetenido as exc:
        raise ErrorDeApi(
            409,
            "FLUJO_DETENIDO",
            f"La corrida es del flujo {exc.flujo_id}, que se detuvo en la decision. Para "
            f"reintentarla: POST /flujos/{exc.flujo_id}/reanudar.",
            run_id=corrida_run_id,
        ) from exc
    except CorridaNoDecidible as exc:
        raise ErrorDeApi(
            409,
            "CORRIDA_NO_PUBLICADA",
            f"La corrida esta {exc.corrida.estado} y no publico una cartera; solo una corrida "
            "EXITOSA puede decidirse.",
            run_id=corrida_run_id,
        ) from exc
    except DecisionYaGenerada as exc:
        # No str(exc): el mensaje del servicio nombra la ejecucion, y esa se descubre en el
        # historial, no en el error.
        raise ErrorDeApi(
            409,
            "DECISION_YA_GENERADA",
            f"La corrida ya tiene una ejecucion EXITOSA con {exc.previa.version_reglas}. "
            f"Consulta /corridas/{corrida_run_id}/decisiones.",
            run_id=corrida_run_id,
        ) from exc
    except DecisionEnProceso as exc:
        raise ErrorDeApi(
            409,
            "DECISION_EN_PROCESO",
            f"La corrida ya se esta decidiendo con {exc.activa.version_reglas}. Consulta "
            f"/corridas/{corrida_run_id}/decisiones.",
            run_id=corrida_run_id,
        ) from exc

    response.headers["Location"] = f"/decisiones/{ejecucion.decision_run_id}"
    return _respuesta_ejecucion(ejecucion, corrida_run_id)


@router.get(
    "/corridas/{run_id}/decisiones",
    response_model=PaginaEjecucionesDecision,
    summary="Las ejecuciones del Decision Engine sobre una corrida, la mas reciente primero",
    responses=errores(
        *SIN_CLAVE,
        CORRIDA_NO_EXISTE,
        (422, "ENTRADA_INVALIDA", "El run_id no es un UUID, o la paginacion esta fuera de rango."),
    ),
)
def listar_ejecuciones(
    run_id: UUID, paginacion: Annotated[Paginacion, Query()], s: Sesion
) -> PaginaEjecucionesDecision:
    """Todas las ejecuciones sobre la corrida, en cualquier estado y con cualquier version de las
    reglas, de la mas reciente a la mas antigua. Es como se encuentra la ejecucion que publico las
    decisiones cuando el POST responde 409.

    Una corrida sin ejecuciones devuelve la lista vacia, no un error: la corrida existe y nadie la
    ha decidido. No hace falta que este EXITOSA.
    """
    corrida = buscar_corrida(s, run_id)
    de_la_corrida = EjecucionDecision.corrida_id == corrida.id
    total = s.exec(select(func.count()).select_from(EjecucionDecision).where(de_la_corrida)).one()
    ejecuciones = s.exec(
        select(EjecucionDecision)
        .where(de_la_corrida)
        # El id solo desempata dos ejecuciones iniciadas en el mismo instante; no sale de la base.
        .order_by(EjecucionDecision.iniciada_en.desc(), EjecucionDecision.id.desc())
        .offset(paginacion.desplazamiento)
        .limit(paginacion.por_pagina)
    ).all()
    return PaginaEjecucionesDecision(
        run_id=corrida.run_id,
        total=total,
        pagina=paginacion.pagina,
        por_pagina=paginacion.por_pagina,
        elementos=[_respuesta_ejecucion(ejecucion, corrida.run_id) for ejecucion in ejecuciones],
    )


@router.get(
    "/decisiones/{decision_run_id}",
    response_model=EjecucionDecisionRespuesta,
    summary="Una ejecucion del Decision Engine: version de las reglas, estado y conteos",
    responses={
        200: {"description": "La ejecucion, en cualquier estado.", **EJEMPLOS_DE_EJECUCION},
        **errores(
            *SIN_CLAVE,
            DECISION_NO_EXISTE,
            (422, "ENTRADA_INVALIDA", "El decision_run_id no es un UUID."),
        ),
    },
)
def obtener_ejecucion(decision_run_id: UUID, s: Sesion) -> EjecucionDecisionRespuesta:
    """Mientras el estado sea `EN_PROCESO`, el worker todavia no la termina: vuelve a consultar.
    En una `FALLIDA`, `cuentas_decididas` es cero aunque `cuentas_evaluadas` diga hasta donde llego
    el motor.
    """
    ejecucion, run_id = _buscar_ejecucion(s, decision_run_id)
    return _respuesta_ejecucion(ejecucion, run_id)


@router.get(
    "/decisiones/{decision_run_id}/cuentas",
    response_model=PaginaDecisionCuentas,
    summary="Lo que una ejecucion decidio de cada cuenta, y por que",
    responses=errores(
        *SIN_CLAVE,
        DECISION_NO_EXISTE,
        (
            409,
            "DECISION_NO_PUBLICADA",
            "La ejecucion no termino EXITOSA: no publico decisiones.",
        ),
        (
            422,
            "ENTRADA_INVALIDA",
            "El decision_run_id no es un UUID, o la paginacion esta fuera de rango.",
        ),
    ),
)
def listar_cuentas_decididas(
    decision_run_id: UUID, paginacion: Annotated[Paginacion, Query()], s: Sesion
) -> PaginaDecisionCuentas:
    """La decision de cada cuenta: segmento, prioridad, canal recomendado y el motivo de cada paso,
    en orden de `cliente_unico`. El canal recomendado es la decision del motor, no el canal de la
    cartera.

    El vocabulario es el de la version de las reglas que decidio (`version_reglas`), y llega como
    texto: otra version puede traer otros valores.

    **409 si la ejecucion no termino EXITOSA**, y no una lista vacia: vacia diria que no habia
    cuentas, y lo cierto es que no publico ninguna decision.
    """
    ejecucion, run_id = _buscar_ejecucion(s, decision_run_id)
    if ejecucion.estado != EstadoDecision.EXITOSA:
        raise ErrorDeApi(
            409,
            "DECISION_NO_PUBLICADA",
            f"La ejecucion esta {ejecucion.estado} y no publico decisiones; solo una ejecucion "
            "EXITOSA tiene decisiones por cuenta.",
            run_id=run_id,
        )

    de_la_ejecucion = DecisionCuenta.ejecucion_decision_id == ejecucion.id
    # Lo que de verdad hay en la tabla, no el contador de la ejecucion.
    total = s.exec(select(func.count()).select_from(DecisionCuenta).where(de_la_ejecucion)).one()
    # Una sola consulta por pagina: la cuenta llega por JOIN, no una por decision.
    decisiones = s.exec(
        select(
            Cuenta.cliente_unico,
            DecisionCuenta.segmento,
            DecisionCuenta.prioridad,
            DecisionCuenta.canal_recomendado,
            DecisionCuenta.motivos,
        )
        .select_from(DecisionCuenta)
        .join(Cuenta, DecisionCuenta.cuenta_id == Cuenta.id)
        .where(de_la_ejecucion)
        .order_by(Cuenta.cliente_unico)
        .offset(paginacion.desplazamiento)
        .limit(paginacion.por_pagina)
    ).all()
    return PaginaDecisionCuentas(
        decision_run_id=ejecucion.decision_run_id,
        run_id=run_id,
        version_reglas=ejecucion.version_reglas,
        estado=ejecucion.estado,
        total=total,
        pagina=paginacion.pagina,
        por_pagina=paginacion.por_pagina,
        elementos=[DecisionCuentaRespuesta(**fila._mapping) for fila in decisiones],
    )


def _buscar_ejecucion(s: Session, decision_run_id: UUID) -> tuple[EjecucionDecision, UUID]:
    """La ejecucion con ese decision_run_id y el run_id de su corrida, o 404. Se busca por el
    identificador publico: el id interno no sale de la base."""
    fila = s.exec(
        select(EjecucionDecision, Corrida.run_id)
        .select_from(EjecucionDecision)
        .join(Corrida, EjecucionDecision.corrida_id == Corrida.id)
        .where(EjecucionDecision.decision_run_id == decision_run_id)
    ).first()
    if fila is None:
        raise ErrorDeApi(
            404,
            "DECISION_NO_ENCONTRADA",
            f"No existe una ejecucion con decision_run_id {decision_run_id}.",
        )
    ejecucion, run_id = fila
    return ejecucion, run_id


def _respuesta_ejecucion(ejecucion: EjecucionDecision, run_id: UUID) -> EjecucionDecisionRespuesta:
    """La ejecucion como la ve un cliente: con el run_id publico de su corrida y sin los ids
    internos."""
    return EjecucionDecisionRespuesta(
        decision_run_id=ejecucion.decision_run_id,
        run_id=run_id,
        version_reglas=ejecucion.version_reglas,
        estado=ejecucion.estado,
        iniciada_en=ejecucion.iniciada_en,
        terminada_en=ejecucion.terminada_en,
        cuentas_evaluadas=ejecucion.cuentas_evaluadas,
        cuentas_decididas=ejecucion.cuentas_decididas,
        detalle=ejecucion.detalle,
    )
