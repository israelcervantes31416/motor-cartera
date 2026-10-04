"""El Motor Territorial por HTTP: pedir que se organicen por municipio las decisiones de una
ejecucion y consultar lo que se publico.

La API no organiza nada, ni espera a que se organice. El POST deja la ejecucion EN_PROCESO y su
trabajo en la cola durable, en una sola transaccion, y un worker la ejecuta. La agregacion, las
reglas, la transaccion que publica todos los municipios o ninguno y la garantia de organizar las
mismas decisiones con exito una sola vez por version viven en el servicio de
`territorial.ejecuciones`. Aqui solo se elige la ejecucion, el codigo HTTP y la forma de la
respuesta.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from sqlmodel import Session, func, select

from motor_cartera.api.dependencias import Sesion
from motor_cartera.api.errores import ErrorDeApi, errores
from motor_cartera.api.esquemas import (
    EJEMPLO_EJECUCION_TERRITORIAL,
    EJEMPLO_EJECUCION_TERRITORIAL_EN_PROCESO,
    EJEMPLO_EJECUCION_TERRITORIAL_FALLIDA,
    EjecucionTerritorialRespuesta,
    Paginacion,
    PaginaEjecucionesTerritoriales,
    PaginaMunicipios,
    ResultadoTerritorialRespuesta,
)
from motor_cartera.db.modelos import (
    Corrida,
    EjecucionDecision,
    EjecucionTerritorial,
    EstadoTerritorial,
    ResultadoTerritorial,
)
from motor_cartera.orquestacion.flujo import FlujoDetenido, FlujoEnProceso, encolar_territorial
from motor_cartera.territorial.ejecuciones import (
    VERSION_DECISION_COMPATIBLE,
    DecisionNoTerritorializable,
    TerritorialEnProceso,
    TerritorialYaGenerado,
)
from motor_cartera.territorial.reglas import VERSION_REGLAS_TERRITORIAL

router = APIRouter(tags=["territorial"])

SIN_CLAVE = (
    (401, "API_KEY_AUSENTE", "Falta la cabecera X-API-Key."),
    (401, "API_KEY_INVALIDA", "La API key no es valida."),
)
DECISION_NO_EXISTE = (
    404,
    "DECISION_NO_ENCONTRADA",
    "No existe una ejecucion de decision con ese decision_run_id.",
)
TERRITORIAL_NO_EXISTE = (
    404,
    "TERRITORIAL_NO_ENCONTRADO",
    "No existe una ejecucion territorial con ese territorial_run_id.",
)
EJEMPLOS_DE_EJECUCION = {
    "content": {
        "application/json": {
            "examples": {
                "EN_PROCESO": {
                    "summary": "El worker todavia no la termina",
                    "value": EJEMPLO_EJECUCION_TERRITORIAL_EN_PROCESO,
                },
                "EXITOSA": {
                    "summary": "Organizo todos los municipios",
                    "value": EJEMPLO_EJECUCION_TERRITORIAL,
                },
                "FALLIDA": {
                    "summary": "El motor fallo y no publico ningun municipio",
                    "value": EJEMPLO_EJECUCION_TERRITORIAL_FALLIDA,
                },
            }
        }
    }
}


@router.post(
    "/decisiones/{decision_run_id}/territoriales",
    status_code=201,
    response_model=EjecucionTerritorialRespuesta,
    summary="Pide organizar por municipio las decisiones de una ejecucion con "
    f"{VERSION_REGLAS_TERRITORIAL}",
    responses={
        201: {
            "description": "La ejecucion territorial quedo creada EN_PROCESO, con su trabajo en la "
            "cola durable: un worker la ejecuta. `Location` apunta a ella; consultala hasta que "
            "termine EXITOSA, con un resultado por municipio, o FALLIDA, sin ninguno.",
            "content": {"application/json": {"example": EJEMPLO_EJECUCION_TERRITORIAL_EN_PROCESO}},
        },
        **errores(
            *SIN_CLAVE,
            DECISION_NO_EXISTE,
            (
                409,
                "DECISION_NO_TERRITORIALIZABLE",
                "La ejecucion de decision no termino EXITOSA, o no se decidio con "
                f"{VERSION_DECISION_COMPATIBLE}: {VERSION_REGLAS_TERRITORIAL} no la organiza.",
            ),
            (
                409,
                "TERRITORIAL_YA_GENERADO",
                f"Las decisiones ya se organizaron con exito con {VERSION_REGLAS_TERRITORIAL}; "
                "GET /decisiones/{decision_run_id}/territoriales dice cual ejecucion.",
            ),
            (
                409,
                "TERRITORIAL_EN_PROCESO",
                f"Las decisiones ya se estan organizando con {VERSION_REGLAS_TERRITORIAL}: tienen "
                "una ejecucion territorial EN_PROCESO.",
            ),
            (
                409,
                "FLUJO_EN_PROCESO",
                "Las decisiones son de un flujo automatico que todavia las va a organizar.",
            ),
            (
                409,
                "FLUJO_DETENIDO",
                "Las decisiones son de un flujo que se detuvo en la organizacion territorial: se "
                "reintenta reanudando el flujo.",
            ),
            (422, "ENTRADA_INVALIDA", "El decision_run_id no es un UUID."),
        ),
    },
)
def crear_ejecucion_territorial(
    decision_run_id: UUID, request: Request, response: Response, s: Sesion
) -> EjecucionTerritorialRespuesta:
    """Pide organizar por municipio las decisiones de una ejecucion `EXITOSA` de `decision/v1`, con
    las reglas territoriales de este servicio. La peticion no organiza: deja la ejecucion
    `EN_PROCESO` y su trabajo en la cola durable, en una sola transaccion, y responde de inmediato.
    Un worker la ejecuta; consulta `Location` hasta que deje de estar `EN_PROCESO`. No recibe
    cuerpo ni elige version: la respuesta dice con cual se organiza (`version_reglas`). Prioriza
    municipios; no traza rutas.

    **201 quiere decir que la ejecucion se creo, no que el motor termino**, y menos que tuvo exito.
    Al terminar, su `estado` dice como: `EXITOSA`, con un resultado por cada municipio con
    decisiones, o `FALLIDA`, sin ninguno y con el motivo en `detalle`. Una `FALLIDA` se reintenta
    con otro POST, que crea otra ejecucion.

    **409 `TERRITORIAL_YA_GENERADO` si esas decisiones ya se organizaron con exito** con esta
    version: organizarlas otra vez duplicaria sus municipios. **409 `TERRITORIAL_EN_PROCESO` si ya
    se estan organizando**: a lo mas hay una ejecucion activa por fuente y version. Ninguno dice
    cual ejecucion fue; lo dice `GET /decisiones/{decision_run_id}/territoriales`.

    **409 `DECISION_NO_TERRITORIALIZABLE` si la ejecucion de decision no termino `EXITOSA` o se
    decidio con otra version de las reglas**: no hay decisiones que estas reglas sepan organizar, y
    no se registra ninguna ejecucion territorial.

    **Las decisiones de un flujo automatico las organiza el flujo**: mientras va a hacerlo, 409
    `FLUJO_EN_PROCESO`, y si se detuvo en esta etapa, 409 `FLUJO_DETENIDO`: se reintenta con
    `POST /flujos/{flujo_id}/reanudar`.
    """
    ejecucion_decision_id, run_id = _buscar_decision(s, decision_run_id)
    try:
        ejecucion = encolar_territorial(s, ejecucion_decision_id, config=request.app.state.config)
    except FlujoEnProceso as exc:
        raise ErrorDeApi(
            409,
            "FLUJO_EN_PROCESO",
            f"Las decisiones son del flujo {exc.flujo_id}, que sigue EN_PROCESO en {exc.etapa} y "
            f"las va a organizar por su cuenta. Consulta /flujos/{exc.flujo_id}.",
            run_id=run_id,
        ) from exc
    except FlujoDetenido as exc:
        raise ErrorDeApi(
            409,
            "FLUJO_DETENIDO",
            f"Las decisiones son del flujo {exc.flujo_id}, que se detuvo en la organizacion "
            f"territorial. Para reintentarla: POST /flujos/{exc.flujo_id}/reanudar.",
            run_id=run_id,
        ) from exc
    except DecisionNoTerritorializable as exc:
        # El motivo es el texto que el servicio armo al revisar la fuente; no se vuelve a leer la
        # ejecucion de decision que trae la excepcion, que ya no tiene sesion.
        raise ErrorDeApi(409, "DECISION_NO_TERRITORIALIZABLE", str(exc), run_id=run_id) from exc
    except TerritorialYaGenerado as exc:
        # No str(exc): el mensaje del servicio nombra la ejecucion, y esa se descubre en el
        # historial, no en el error.
        raise ErrorDeApi(
            409,
            "TERRITORIAL_YA_GENERADO",
            "Las decisiones de esta ejecucion ya se organizaron con exito con "
            f"{VERSION_REGLAS_TERRITORIAL}. Consulta /decisiones/{decision_run_id}/territoriales.",
            run_id=run_id,
        ) from exc
    except TerritorialEnProceso as exc:
        raise ErrorDeApi(
            409,
            "TERRITORIAL_EN_PROCESO",
            "Las decisiones de esta ejecucion ya se estan organizando con "
            f"{exc.activa.version_reglas}. Consulta /decisiones/{decision_run_id}/territoriales.",
            run_id=run_id,
        ) from exc

    response.headers["Location"] = f"/territoriales/{ejecucion.territorial_run_id}"
    return _respuesta_ejecucion(ejecucion, decision_run_id, run_id)


@router.get(
    "/decisiones/{decision_run_id}/territoriales",
    response_model=PaginaEjecucionesTerritoriales,
    summary="Las ejecuciones del Motor Territorial sobre una ejecucion de decision, la mas "
    "reciente primero",
    responses=errores(
        *SIN_CLAVE,
        DECISION_NO_EXISTE,
        (
            422,
            "ENTRADA_INVALIDA",
            "El decision_run_id no es un UUID, o la paginacion esta fuera de rango.",
        ),
    ),
)
def listar_ejecuciones_territoriales(
    decision_run_id: UUID, paginacion: Annotated[Paginacion, Query()], s: Sesion
) -> PaginaEjecucionesTerritoriales:
    """Todas las ejecuciones territoriales sobre esas decisiones, en cualquier estado y con
    cualquier version de las reglas, de la mas reciente a la mas antigua. Es como se encuentra la
    ejecucion que publico los municipios cuando el POST responde 409.

    Una ejecucion de decision sin ejecuciones territoriales devuelve la lista vacia, no un error:
    existe y nadie la ha organizado. No hace falta que este EXITOSA.
    """
    ejecucion_decision_id, run_id = _buscar_decision(s, decision_run_id)
    de_la_decision = EjecucionTerritorial.ejecucion_decision_id == ejecucion_decision_id
    total = s.exec(
        select(func.count()).select_from(EjecucionTerritorial).where(de_la_decision)
    ).one()
    ejecuciones = s.exec(
        select(EjecucionTerritorial)
        .where(de_la_decision)
        # El id solo desempata dos ejecuciones iniciadas en el mismo instante; no sale de la base.
        .order_by(EjecucionTerritorial.iniciada_en.desc(), EjecucionTerritorial.id.desc())
        .offset(paginacion.desplazamiento)
        .limit(paginacion.por_pagina)
    ).all()
    return PaginaEjecucionesTerritoriales(
        decision_run_id=decision_run_id,
        run_id=run_id,
        total=total,
        pagina=paginacion.pagina,
        por_pagina=paginacion.por_pagina,
        elementos=[
            _respuesta_ejecucion(ejecucion, decision_run_id, run_id) for ejecucion in ejecuciones
        ],
    )


@router.get(
    "/territoriales/{territorial_run_id}",
    response_model=EjecucionTerritorialRespuesta,
    summary="Una ejecucion del Motor Territorial: version de las reglas, estado y conteos",
    responses={
        200: {"description": "La ejecucion, en cualquier estado.", **EJEMPLOS_DE_EJECUCION},
        **errores(
            *SIN_CLAVE,
            TERRITORIAL_NO_EXISTE,
            (422, "ENTRADA_INVALIDA", "El territorial_run_id no es un UUID."),
        ),
    },
)
def obtener_ejecucion_territorial(
    territorial_run_id: UUID, s: Sesion
) -> EjecucionTerritorialRespuesta:
    """Mientras el estado sea `EN_PROCESO`, el worker todavia no la termina: vuelve a consultar. En
    una `FALLIDA`, `territorios_publicados` es cero aunque `territorios_evaluados` diga hasta donde
    llego el motor. Trae el `decision_run_id` de las decisiones que organizo y el `run_id` de su
    corrida.
    """
    ejecucion, decision_run_id, run_id = _buscar_territorial(s, territorial_run_id)
    return _respuesta_ejecucion(ejecucion, decision_run_id, run_id)


@router.get(
    "/territoriales/{territorial_run_id}/municipios",
    response_model=PaginaMunicipios,
    summary="Lo que una ejecucion territorial publico de cada municipio, en orden de prioridad",
    responses=errores(
        *SIN_CLAVE,
        TERRITORIAL_NO_EXISTE,
        (
            409,
            "TERRITORIAL_NO_PUBLICADO",
            "La ejecucion territorial no termino EXITOSA: no publico municipios.",
        ),
        (
            422,
            "ENTRADA_INVALIDA",
            "El territorial_run_id no es un UUID, o la paginacion esta fuera de rango.",
        ),
    ),
)
def listar_municipios(
    territorial_run_id: UUID, paginacion: Annotated[Paginacion, Query()], s: Sesion
) -> PaginaMunicipios:
    """Cada municipio con sus agregados, su carga de campo, su lugar entre los municipios con
    cuentas de campo y por que. Primero los que tienen lugar, del 1 en adelante; despues los que no
    tienen ninguna cuenta de campo, sin lugar y por clave. Es el orden de las reglas que los
    organizaron, y es una prioridad territorial, no una ruta.

    El vocabulario (`carga` y el `codigo` de cada motivo) es el de la version de las reglas que
    organizo (`version_reglas`), y llega como texto: otra version puede traer otros valores.

    **409 si la ejecucion no termino EXITOSA**, y no una lista vacia: vacia diria que no habia
    municipios, y lo cierto es que no publico ninguno.
    """
    ejecucion, decision_run_id, run_id = _buscar_territorial(s, territorial_run_id)
    if ejecucion.estado != EstadoTerritorial.EXITOSA:
        raise ErrorDeApi(
            409,
            "TERRITORIAL_NO_PUBLICADO",
            f"La ejecucion territorial esta {ejecucion.estado} y no publico municipios; solo una "
            "ejecucion EXITOSA tiene resultados por municipio.",
            run_id=run_id,
        )

    de_la_ejecucion = ResultadoTerritorial.ejecucion_territorial_id == ejecucion.id
    # Lo que de verdad hay en la tabla, no el contador de la ejecucion.
    total = s.exec(
        select(func.count()).select_from(ResultadoTerritorial).where(de_la_ejecucion)
    ).one()
    # Una sola consulta por pagina. Una ejecucion EXITOSA ya no cambia, asi que OFFSET no salta ni
    # repite municipios entre una pagina y otra.
    municipios = s.exec(
        select(
            ResultadoTerritorial.cve_entidad,
            ResultadoTerritorial.cve_municipio,
            ResultadoTerritorial.cuentas_total,
            ResultadoTerritorial.saldo_total,
            ResultadoTerritorial.cuentas_campo,
            ResultadoTerritorial.saldo_campo,
            ResultadoTerritorial.carga,
            ResultadoTerritorial.posicion_campo,
            ResultadoTerritorial.motivos,
        )
        .where(de_la_ejecucion)
        # El orden de territorial/v1: el lugar de campo, y los que no tienen (NULL) al final, por
        # clave. El lugar es unico en la ejecucion, asi que la clave solo ordena a los NULL.
        .order_by(
            ResultadoTerritorial.posicion_campo.asc().nulls_last(),
            ResultadoTerritorial.cve_entidad,
            ResultadoTerritorial.cve_municipio,
        )
        .offset(paginacion.desplazamiento)
        .limit(paginacion.por_pagina)
    ).all()
    return PaginaMunicipios(
        territorial_run_id=ejecucion.territorial_run_id,
        decision_run_id=decision_run_id,
        run_id=run_id,
        version_reglas=ejecucion.version_reglas,
        estado=ejecucion.estado,
        total=total,
        pagina=paginacion.pagina,
        por_pagina=paginacion.por_pagina,
        elementos=[ResultadoTerritorialRespuesta(**fila._mapping) for fila in municipios],
    )


def _buscar_decision(s: Session, decision_run_id: UUID) -> tuple[int, UUID]:
    """El id interno de la ejecucion de decision con ese decision_run_id y el run_id de su corrida,
    o 404. Dos valores y no la ejecucion: el id interno solo sirve para pedir la ejecucion
    territorial; no sale en ninguna respuesta."""
    fila = s.exec(
        select(EjecucionDecision.id, Corrida.run_id)
        .select_from(EjecucionDecision)
        .join(Corrida, EjecucionDecision.corrida_id == Corrida.id)
        .where(EjecucionDecision.decision_run_id == decision_run_id)
    ).first()
    if fila is None:
        raise ErrorDeApi(
            404,
            "DECISION_NO_ENCONTRADA",
            f"No existe una ejecucion de decision con decision_run_id {decision_run_id}.",
        )
    ejecucion_decision_id, run_id = fila
    return ejecucion_decision_id, run_id


def _buscar_territorial(
    s: Session, territorial_run_id: UUID
) -> tuple[EjecucionTerritorial, UUID, UUID]:
    """La ejecucion territorial con ese territorial_run_id, el decision_run_id de las decisiones que
    organizo y el run_id de su corrida, o 404. Una sola consulta, con JOIN, y por el identificador
    publico: los ids internos no salen de la base."""
    fila = s.exec(
        select(EjecucionTerritorial, EjecucionDecision.decision_run_id, Corrida.run_id)
        .select_from(EjecucionTerritorial)
        .join(EjecucionDecision, EjecucionTerritorial.ejecucion_decision_id == EjecucionDecision.id)
        .join(Corrida, EjecucionDecision.corrida_id == Corrida.id)
        .where(EjecucionTerritorial.territorial_run_id == territorial_run_id)
    ).first()
    if fila is None:
        raise ErrorDeApi(
            404,
            "TERRITORIAL_NO_ENCONTRADO",
            f"No existe una ejecucion territorial con territorial_run_id {territorial_run_id}.",
        )
    ejecucion, decision_run_id, run_id = fila
    return ejecucion, decision_run_id, run_id


def _respuesta_ejecucion(
    ejecucion: EjecucionTerritorial, decision_run_id: UUID, run_id: UUID
) -> EjecucionTerritorialRespuesta:
    """La ejecucion como la ve un cliente: con los identificadores publicos de sus decisiones y de
    su corrida, y sin los ids internos."""
    return EjecucionTerritorialRespuesta(
        territorial_run_id=ejecucion.territorial_run_id,
        decision_run_id=decision_run_id,
        run_id=run_id,
        version_reglas=ejecucion.version_reglas,
        estado=ejecucion.estado,
        iniciada_en=ejecucion.iniciada_en,
        terminada_en=ejecucion.terminada_en,
        territorios_evaluados=ejecucion.territorios_evaluados,
        territorios_publicados=ejecucion.territorios_publicados,
        detalle=ejecucion.detalle,
    )
