"""El Decision Engine por HTTP: decidir una corrida publicada y consultar lo que se decidio.

La API no decide nada. Las reglas, la transaccion que publica todas las decisiones o ninguna y la
garantia de decidir una corrida con exito una sola vez por version viven en el servicio de
`decision.ejecuciones`. Aqui solo se elige la corrida o la ejecucion, el codigo HTTP y la forma de
la respuesta.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response
from sqlmodel import Session, func, select

from motor_cartera.api.dependencias import Sesion, buscar_corrida
from motor_cartera.api.errores import ErrorDeApi, errores
from motor_cartera.api.esquemas import (
    EJEMPLO_EJECUCION,
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
    DecisionYaGenerada,
    decidir_corrida,
)
from motor_cartera.decision.reglas import VERSION_REGLAS_DECISION

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


@router.post(
    "/corridas/{run_id}/decisiones",
    status_code=201,
    response_model=EjecucionDecisionRespuesta,
    summary=f"Decide cada cuenta de una corrida publicada con {VERSION_REGLAS_DECISION}",
    responses={
        201: {
            "description": "La ejecucion quedo creada y ya termino. Su `estado` dice como: "
            "EXITOSA, con una decision por cuenta, o FALLIDA, sin ninguna y con el motivo en "
            "`detalle`. En los dos casos `Location` apunta a ella.",
            "content": {
                "application/json": {
                    "examples": {
                        "EXITOSA": {
                            "summary": "Decidio todas las cuentas",
                            "value": EJEMPLO_EJECUCION,
                        },
                        "FALLIDA": {
                            "summary": "El motor fallo y no publico ninguna decision",
                            "value": EJEMPLO_EJECUCION_FALLIDA,
                        },
                    }
                }
            },
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
            (422, "ENTRADA_INVALIDA", "El run_id no es un UUID."),
        ),
    },
)
def crear_ejecucion(run_id: UUID, response: Response, s: Sesion) -> EjecucionDecisionRespuesta:
    """Decide cada cuenta de una corrida `EXITOSA` con las reglas de este servicio, en la misma
    peticion, y responde con la ejecucion ya terminada. No recibe cuerpo ni elige version: la
    respuesta dice con cual se decidio (`version_reglas`).

    **201 quiere decir que la ejecucion se creo, no que el motor tuvo exito.** Su `estado` dice
    como termino: `EXITOSA`, con una decision por cada cuenta, o `FALLIDA`, sin ninguna y con el
    motivo en `detalle`. Una `FALLIDA` se reintenta con otro POST, que crea otra ejecucion.

    **409 `DECISION_YA_GENERADA` si la corrida ya tiene sus decisiones publicadas** con esta
    version: decidirla otra vez las duplicaria. La respuesta no dice cual ejecucion las publico;
    lo dice `GET /corridas/{run_id}/decisiones`. Tambien es 409 si otra peticion las publico
    mientras esta decidia: esta ejecucion queda `FALLIDA` en el historial.

    **409 `CORRIDA_NO_PUBLICADA` si la corrida no termino `EXITOSA`**: no hay cartera que
    decidir, y no se registra ninguna ejecucion.
    """
    corrida = buscar_corrida(s, run_id)
    corrida_id, corrida_run_id = corrida.id, corrida.run_id
    # La sesion de la peticion solo sirvio para encontrar la corrida. Se termina su transaccion
    # antes de decidir, que puede tardar y abre las suyas, para que la conexion vuelva al pool. Con
    # el rollback la corrida en memoria caduca: de aqui en adelante solo se usan los dos valores.
    s.rollback()
    try:
        ejecucion = decidir_corrida(corrida_id)
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
    responses=errores(
        *SIN_CLAVE,
        DECISION_NO_EXISTE,
        (422, "ENTRADA_INVALIDA", "El decision_run_id no es un UUID."),
    ),
)
def obtener_ejecucion(decision_run_id: UUID, s: Sesion) -> EjecucionDecisionRespuesta:
    """Mientras el estado sea `EN_PROCESO`, la ejecucion sigue decidiendo. En una `FALLIDA`,
    `cuentas_decididas` es cero aunque `cuentas_evaluadas` diga hasta donde llego el motor.
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
