"""El Motor de Ruteo por HTTP: pedir que se tracen las rutas de una ejecucion territorial y
consultar lo que se publico.

La API no rutea nada, ni espera a que se rutee. El POST deja la ejecucion EN_PROCESO y su trabajo en
la cola durable, en una sola transaccion, y un worker la ejecuta. Las coordenadas sinteticas, las
distancias, el recorrido, la transaccion que publica todas las rutas o ninguna y la garantia de
rutear una ejecucion territorial con exito una sola vez por version viven en `ruteo.reglas` y
`ruteo.ejecuciones`. Aqui solo se elige la ejecucion, el codigo HTTP y la forma de la respuesta.

Las coordenadas y las distancias que se sirven son sinteticas: metros de un plano operativo local,
propio de cada municipio, con el deposito en (0, 0). No son latitud ni longitud, domicilios, calles,
trafico ni tiempos.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query, Request, Response
from sqlmodel import Session, func, select

from motor_cartera.api.dependencias import Sesion
from motor_cartera.api.errores import ErrorDeApi, errores
from motor_cartera.api.esquemas import (
    EJEMPLO_EJECUCION_RUTEO,
    EJEMPLO_EJECUCION_RUTEO_EN_PROCESO,
    EJEMPLO_EJECUCION_RUTEO_FALLIDA,
    EjecucionRuteoRespuesta,
    Paginacion,
    PaginaEjecucionesRuteo,
    PaginaParadas,
    PaginaRutas,
    ParadaRutaRespuesta,
    RutaTerritorialRespuesta,
)
from motor_cartera.db.modelos import (
    Corrida,
    Cuenta,
    DecisionCuenta,
    EjecucionDecision,
    EjecucionRuteo,
    EjecucionTerritorial,
    EstadoRuteo,
    ParadaRuta,
    ResultadoTerritorial,
    RutaTerritorial,
)
from motor_cartera.orquestacion.flujo import FlujoDetenido, FlujoEnProceso, encolar_ruteo
from motor_cartera.ruteo.ejecuciones import (
    VERSION_DECISION_COMPATIBLE,
    VERSION_TERRITORIAL_COMPATIBLE,
    RuteoEnProceso,
    RuteoYaGenerado,
    TerritorialNoRuteable,
)
from motor_cartera.ruteo.reglas import VERSION_REGLAS_RUTEO

router = APIRouter(tags=["ruteo"])

ClaveTerritorio = Annotated[
    str,
    Path(
        pattern=r"^[0-9]{5}$",
        description="El municipio: cve_entidad + cve_municipio, cinco digitos.",
        examples=["21114"],
    ),
]

SIN_CLAVE = (
    (401, "API_KEY_AUSENTE", "Falta la cabecera X-API-Key."),
    (401, "API_KEY_INVALIDA", "La API key no es valida."),
)
TERRITORIAL_NO_EXISTE = (
    404,
    "TERRITORIAL_NO_ENCONTRADO",
    "No existe una ejecucion territorial con ese territorial_run_id.",
)
RUTEO_NO_EXISTE = (
    404,
    "RUTEO_NO_ENCONTRADO",
    "No existe una ejecucion de ruteo con ese ruteo_run_id.",
)
RUTEO_NO_PUBLICADO = (
    409,
    "RUTEO_NO_PUBLICADO",
    "La ejecucion de ruteo no termino EXITOSA: no publico rutas ni paradas.",
)
EJEMPLOS_DE_EJECUCION = {
    "content": {
        "application/json": {
            "examples": {
                "EN_PROCESO": {
                    "summary": "El worker todavia no la termina",
                    "value": EJEMPLO_EJECUCION_RUTEO_EN_PROCESO,
                },
                "EXITOSA": {"summary": "Trazo todas las rutas", "value": EJEMPLO_EJECUCION_RUTEO},
                "FALLIDA": {
                    "summary": "El motor fallo y no publico ninguna ruta",
                    "value": EJEMPLO_EJECUCION_RUTEO_FALLIDA,
                },
            }
        }
    }
}
SINTETICAS = (
    "Las coordenadas y las distancias son sinteticas: metros de un plano operativo local, propio "
    "de cada municipio, con el deposito en (0, 0). No son latitud ni longitud, domicilios, calles, "
    "trafico ni tiempos de conduccion."
)


@router.post(
    "/territoriales/{territorial_run_id}/ruteos",
    status_code=201,
    response_model=EjecucionRuteoRespuesta,
    summary=f"Pide trazar con {VERSION_REGLAS_RUTEO} la ruta sintetica de cada municipio con "
    "trabajo de campo de una ejecucion territorial",
    responses={
        201: {
            "description": "La ejecucion de ruteo quedo creada EN_PROCESO, con su trabajo en la "
            "cola durable: un worker la ejecuta. `Location` apunta a ella; consultala hasta que "
            "termine EXITOSA, con una ruta por municipio con cuentas de campo, o FALLIDA, sin "
            "ninguna.",
            "content": {"application/json": {"example": EJEMPLO_EJECUCION_RUTEO_EN_PROCESO}},
        },
        **errores(
            *SIN_CLAVE,
            TERRITORIAL_NO_EXISTE,
            (
                409,
                "TERRITORIAL_NO_RUTEABLE",
                "La ejecucion territorial no termino EXITOSA o no es de "
                f"{VERSION_TERRITORIAL_COMPATIBLE}, o sus decisiones no son una ejecucion EXITOSA "
                f"de {VERSION_DECISION_COMPATIBLE}: {VERSION_REGLAS_RUTEO} no la rutea.",
            ),
            (
                409,
                "RUTEO_YA_GENERADO",
                f"La ejecucion territorial ya se ruteo con exito con {VERSION_REGLAS_RUTEO}; "
                "GET /territoriales/{territorial_run_id}/ruteos dice cual ejecucion.",
            ),
            (
                409,
                "RUTEO_EN_PROCESO",
                f"La ejecucion territorial ya se esta ruteando con {VERSION_REGLAS_RUTEO}: tiene "
                "una ejecucion de ruteo EN_PROCESO.",
            ),
            (
                409,
                "FLUJO_EN_PROCESO",
                "La ejecucion territorial es de un flujo automatico que todavia la va a rutear.",
            ),
            (
                409,
                "FLUJO_DETENIDO",
                "La ejecucion territorial es de un flujo que se detuvo en el ruteo: se reintenta "
                "reanudando el flujo.",
            ),
            (422, "ENTRADA_INVALIDA", "El territorial_run_id no es un UUID."),
        ),
    },
)
def crear_ejecucion_ruteo(
    territorial_run_id: UUID, request: Request, response: Response, s: Sesion
) -> EjecucionRuteoRespuesta:
    """Pide trazar, con las reglas de ruteo de este servicio, la ruta de cada municipio con cuentas
    de campo de una ejecucion territorial `EXITOSA` de `territorial/v1` sobre decisiones de
    `decision/v1`. La peticion no rutea: deja la ejecucion `EN_PROCESO` y su trabajo en la cola
    durable, en una sola transaccion, y responde de inmediato. Un worker la ejecuta; consulta
    `Location` hasta que deje de estar `EN_PROCESO`. No recibe cuerpo ni elige version: la
    respuesta dice con cual se rutea (`version_reglas`).

    Dentro de cada municipio decide en que orden visitar las cuentas con `CAMPO` como canal
    recomendado: sale de un deposito, visita cada una una vez y regresa. No vuelve a decidir que
    cuentas van a campo ni que municipio va primero, y no conecta municipios entre si: cada uno
    tiene su propia ruta. Sin gestores, vehiculos, capacidades ni horarios.

    **Las rutas son sinteticas.** Cada cuenta recibe un punto determinista en un plano local de 10
    km por lado, propio de su municipio, y las distancias son Manhattan, en metros sinteticos. No
    son latitud ni longitud, domicilios, calles, trafico ni tiempos de conduccion.

    **201 quiere decir que la ejecucion se creo, no que el motor termino**, y menos que tuvo exito.
    Al terminar, su `estado` dice como: `EXITOSA`, con una ruta por municipio con cuentas de campo,
    o `FALLIDA`, sin ninguna y con el motivo en `detalle`. Una `FALLIDA` se reintenta con otro POST,
    que crea otra ejecucion.

    **409 `RUTEO_YA_GENERADO` si esa ejecucion territorial ya se ruteo con exito** con esta
    version. **409 `RUTEO_EN_PROCESO` si ya se esta ruteando**: a lo mas hay una ejecucion activa
    por fuente y version. Ninguno dice cual ejecucion fue; lo dice
    `GET /territoriales/{territorial_run_id}/ruteos`.

    **409 `TERRITORIAL_NO_RUTEABLE` si la ejecucion territorial o sus decisiones no se pueden
    rutear**, y no se registra ninguna ejecucion de ruteo.

    **La ejecucion territorial de un flujo automatico la rutea el flujo**: mientras va a hacerlo,
    409 `FLUJO_EN_PROCESO`, y si se detuvo en el ruteo, 409 `FLUJO_DETENIDO`: se reintenta con
    `POST /flujos/{flujo_id}/reanudar`.
    """
    ejecucion_territorial_id, decision_run_id, run_id = _buscar_territorial(s, territorial_run_id)
    try:
        ejecucion = encolar_ruteo(s, ejecucion_territorial_id, config=request.app.state.config)
    except FlujoEnProceso as exc:
        raise ErrorDeApi(
            409,
            "FLUJO_EN_PROCESO",
            f"La ejecucion territorial es del flujo {exc.flujo_id}, que sigue EN_PROCESO en "
            f"{exc.etapa} y la va a rutear por su cuenta. Consulta /flujos/{exc.flujo_id}.",
            run_id=run_id,
        ) from exc
    except FlujoDetenido as exc:
        raise ErrorDeApi(
            409,
            "FLUJO_DETENIDO",
            f"La ejecucion territorial es del flujo {exc.flujo_id}, que se detuvo en el ruteo. "
            f"Para reintentarlo: POST /flujos/{exc.flujo_id}/reanudar.",
            run_id=run_id,
        ) from exc
    except TerritorialNoRuteable as exc:
        # El motivo es el texto que el servicio armo al revisar la cadena; no se vuelve a leer la
        # ejecucion territorial que trae la excepcion, que ya no tiene sesion.
        raise ErrorDeApi(409, "TERRITORIAL_NO_RUTEABLE", str(exc), run_id=run_id) from exc
    except RuteoYaGenerado as exc:
        # No str(exc): el mensaje del servicio nombra la ejecucion, y esa se descubre en el
        # historial, no en el error.
        raise ErrorDeApi(
            409,
            "RUTEO_YA_GENERADO",
            "Los municipios de esta ejecucion territorial ya se rutearon con exito con "
            f"{VERSION_REGLAS_RUTEO}. Consulta /territoriales/{territorial_run_id}/ruteos.",
            run_id=run_id,
        ) from exc
    except RuteoEnProceso as exc:
        raise ErrorDeApi(
            409,
            "RUTEO_EN_PROCESO",
            "Los municipios de esta ejecucion territorial ya se estan ruteando con "
            f"{exc.activa.version_reglas}. Consulta /territoriales/{territorial_run_id}/ruteos.",
            run_id=run_id,
        ) from exc

    response.headers["Location"] = f"/ruteos/{ejecucion.ruteo_run_id}"
    return _respuesta_ejecucion(ejecucion, territorial_run_id, decision_run_id, run_id)


@router.get(
    "/territoriales/{territorial_run_id}/ruteos",
    response_model=PaginaEjecucionesRuteo,
    summary="Las ejecuciones del Motor de Ruteo sobre una ejecucion territorial, la mas reciente "
    "primero",
    responses=errores(
        *SIN_CLAVE,
        TERRITORIAL_NO_EXISTE,
        (
            422,
            "ENTRADA_INVALIDA",
            "El territorial_run_id no es un UUID, o la paginacion esta fuera de rango.",
        ),
    ),
)
def listar_ejecuciones_ruteo(
    territorial_run_id: UUID, paginacion: Annotated[Paginacion, Query()], s: Sesion
) -> PaginaEjecucionesRuteo:
    """Todas las ejecuciones de ruteo sobre esa ejecucion territorial, en cualquier estado y con
    cualquier version de las reglas, de la mas reciente a la mas antigua. Es como se encuentra la
    ejecucion que publico las rutas cuando el POST responde 409.

    Una ejecucion territorial sin ejecuciones de ruteo devuelve la lista vacia, no un error: existe
    y nadie la ha ruteado. No hace falta que este EXITOSA.
    """
    ejecucion_territorial_id, decision_run_id, run_id = _buscar_territorial(s, territorial_run_id)
    de_la_territorial = EjecucionRuteo.ejecucion_territorial_id == ejecucion_territorial_id
    total = s.exec(select(func.count()).select_from(EjecucionRuteo).where(de_la_territorial)).one()
    ejecuciones = s.exec(
        select(EjecucionRuteo)
        .where(de_la_territorial)
        # El id solo desempata dos ejecuciones iniciadas en el mismo instante; no sale de la base.
        .order_by(EjecucionRuteo.iniciada_en.desc(), EjecucionRuteo.id.desc())
        .offset(paginacion.desplazamiento)
        .limit(paginacion.por_pagina)
    ).all()
    return PaginaEjecucionesRuteo(
        territorial_run_id=territorial_run_id,
        decision_run_id=decision_run_id,
        run_id=run_id,
        total=total,
        pagina=paginacion.pagina,
        por_pagina=paginacion.por_pagina,
        elementos=[
            _respuesta_ejecucion(ejecucion, territorial_run_id, decision_run_id, run_id)
            for ejecucion in ejecuciones
        ],
    )


@router.get(
    "/ruteos/{ruteo_run_id}",
    response_model=EjecucionRuteoRespuesta,
    summary="Una ejecucion del Motor de Ruteo: version de las reglas, estado y conteos",
    responses={
        200: {"description": "La ejecucion, en cualquier estado.", **EJEMPLOS_DE_EJECUCION},
        **errores(
            *SIN_CLAVE,
            RUTEO_NO_EXISTE,
            (422, "ENTRADA_INVALIDA", "El ruteo_run_id no es un UUID."),
        ),
    },
)
def obtener_ejecucion_ruteo(ruteo_run_id: UUID, s: Sesion) -> EjecucionRuteoRespuesta:
    """Mientras el estado sea `EN_PROCESO`, el worker todavia no la termina. En una `FALLIDA`,
    `rutas_publicadas` y `paradas_publicadas` son cero aunque `rutas_evaluadas` y
    `paradas_evaluadas` digan hasta donde llego el motor. Trae los identificadores publicos de toda
    la cadena: la ejecucion territorial, la de decision y la corrida.
    """
    ejecucion, territorial_run_id, decision_run_id, run_id = _buscar_ruteo(s, ruteo_run_id)
    return _respuesta_ejecucion(ejecucion, territorial_run_id, decision_run_id, run_id)


@router.get(
    "/ruteos/{ruteo_run_id}/rutas",
    response_model=PaginaRutas,
    summary="La ruta sintetica de cada municipio, en el orden de prioridad territorial",
    responses=errores(
        *SIN_CLAVE,
        RUTEO_NO_EXISTE,
        RUTEO_NO_PUBLICADO,
        (
            422,
            "ENTRADA_INVALIDA",
            "El ruteo_run_id no es un UUID, o la paginacion esta fuera de rango.",
        ),
    ),
)
def listar_rutas(
    ruteo_run_id: UUID, paginacion: Annotated[Paginacion, Query()], s: Sesion
) -> PaginaRutas:
    """Cada municipio con cuentas de campo, con su ruta: cuantas paradas tiene y cuanto mide antes y
    despues del 2-opt, con el regreso al deposito. Van en el orden de prioridad que les dio la
    ejecucion territorial (`posicion_territorial`), que no es el orden de visita: ese es la
    `secuencia` de cada parada, dentro de su municipio. Los municipios sin cuentas de campo no
    tienen ruta.

    **409 si la ejecucion no termino EXITOSA**, y no una lista vacia: vacia diria que no habia
    municipios que rutear, y lo cierto es que no publico ninguno. Las distancias son metros
    sinteticos, no las de ninguna calle real.
    """
    ejecucion, territorial_run_id, decision_run_id, run_id = _publicada(s, ruteo_run_id)
    de_la_ejecucion = RutaTerritorial.ejecucion_ruteo_id == ejecucion.id
    # Lo que de verdad hay en la tabla, no el contador de la ejecucion.
    total = s.exec(select(func.count()).select_from(RutaTerritorial).where(de_la_ejecucion)).one()
    # Una sola consulta por pagina, con la clave y el lugar del municipio por JOIN. Una ejecucion
    # EXITOSA ya no cambia, asi que OFFSET no salta ni repite rutas entre una pagina y otra.
    rutas = s.exec(
        select(
            ResultadoTerritorial.cve_entidad,
            ResultadoTerritorial.cve_municipio,
            ResultadoTerritorial.posicion_campo,
            ResultadoTerritorial.cuentas_campo,
            RutaTerritorial.paradas,
            RutaTerritorial.distancia_inicial_m,
            RutaTerritorial.distancia_total_m,
            RutaTerritorial.distancia_regreso_deposito_m,
            RutaTerritorial.mejora_2opt_m,
        )
        .select_from(RutaTerritorial)
        .join(
            ResultadoTerritorial,
            RutaTerritorial.resultado_territorial_id == ResultadoTerritorial.id,
        )
        .where(de_la_ejecucion)
        # El lugar es unico en la ejecucion territorial; la clave solo hace el orden total.
        .order_by(
            ResultadoTerritorial.posicion_campo,
            ResultadoTerritorial.cve_entidad,
            ResultadoTerritorial.cve_municipio,
        )
        .offset(paginacion.desplazamiento)
        .limit(paginacion.por_pagina)
    ).all()
    return PaginaRutas(
        ruteo_run_id=ruteo_run_id,
        territorial_run_id=territorial_run_id,
        decision_run_id=decision_run_id,
        run_id=run_id,
        version_reglas=ejecucion.version_reglas,
        estado=ejecucion.estado,
        total=total,
        pagina=paginacion.pagina,
        por_pagina=paginacion.por_pagina,
        elementos=[
            RutaTerritorialRespuesta(
                clave_territorio=ruta.cve_entidad + ruta.cve_municipio,
                posicion_territorial=ruta.posicion_campo,
                cuentas_campo=ruta.cuentas_campo,
                paradas=ruta.paradas,
                distancia_inicial_m=ruta.distancia_inicial_m,
                distancia_total_m=ruta.distancia_total_m,
                distancia_regreso_deposito_m=ruta.distancia_regreso_deposito_m,
                mejora_2opt_m=ruta.mejora_2opt_m,
            )
            for ruta in rutas
        ],
    )


@router.get(
    "/ruteos/{ruteo_run_id}/rutas/{clave_territorio}/paradas",
    response_model=PaginaParadas,
    summary="Las paradas de la ruta de un municipio, en el orden de visita",
    responses=errores(
        *SIN_CLAVE,
        RUTEO_NO_EXISTE,
        (
            404,
            "RUTA_NO_ENCONTRADA",
            "La ejecucion de ruteo no tiene ruta para ese municipio: no tiene cuentas de campo, o "
            "no es de su ejecucion territorial.",
        ),
        RUTEO_NO_PUBLICADO,
        (
            422,
            "ENTRADA_INVALIDA",
            "El ruteo_run_id no es un UUID, la clave del municipio no son cinco digitos, o la "
            "paginacion esta fuera de rango.",
        ),
    ),
)
def listar_paradas(
    ruteo_run_id: UUID,
    clave_territorio: ClaveTerritorio,
    paginacion: Annotated[Paginacion, Query()],
    s: Sesion,
) -> PaginaParadas:
    """Cada cuenta de campo del municipio, en el orden en que la ruta la visita (`secuencia`, desde
    1), con su punto en el plano sintetico del municipio y la distancia desde la parada anterior; la
    primera, desde el deposito, en (0, 0). El regreso al deposito no es una parada: esta en la ruta.

    El municipio va por su clave, cinco digitos (`21114`). Un municipio sin cuentas de campo no
    tiene ruta: 404 `RUTA_NO_ENCONTRADA`. Las coordenadas son sinteticas: no son latitud ni
    longitud, ni un domicilio.
    """
    ejecucion, _, _, run_id = _publicada(s, ruteo_run_id)
    ruta_id = s.exec(
        select(RutaTerritorial.id)
        .select_from(RutaTerritorial)
        .join(
            ResultadoTerritorial,
            RutaTerritorial.resultado_territorial_id == ResultadoTerritorial.id,
        )
        .where(
            RutaTerritorial.ejecucion_ruteo_id == ejecucion.id,
            ResultadoTerritorial.cve_entidad == clave_territorio[:2],
            ResultadoTerritorial.cve_municipio == clave_territorio[2:],
        )
    ).first()
    if ruta_id is None:
        raise ErrorDeApi(
            404,
            "RUTA_NO_ENCONTRADA",
            f"La ejecucion de ruteo {ruteo_run_id} no tiene ruta para el municipio "
            f"{clave_territorio}: no tiene cuentas de campo, o no es de su ejecucion territorial.",
            run_id=run_id,
        )

    de_la_ruta = ParadaRuta.ruta_territorial_id == ruta_id
    # Lo que de verdad hay en la tabla, no el conteo de la ruta.
    total = s.exec(select(func.count()).select_from(ParadaRuta).where(de_la_ruta)).one()
    # Una sola consulta por pagina, con el cliente por JOIN: ni la parada ni la ruta lo copian.
    paradas = s.exec(
        select(
            ParadaRuta.secuencia,
            Cuenta.cliente_unico,
            ParadaRuta.x_m,
            ParadaRuta.y_m,
            ParadaRuta.distancia_desde_anterior_m,
        )
        .select_from(ParadaRuta)
        .join(DecisionCuenta, ParadaRuta.decision_cuenta_id == DecisionCuenta.id)
        .join(Cuenta, DecisionCuenta.cuenta_id == Cuenta.id)
        .where(de_la_ruta)
        .order_by(ParadaRuta.secuencia)
        .offset(paginacion.desplazamiento)
        .limit(paginacion.por_pagina)
    ).all()
    return PaginaParadas(
        ruteo_run_id=ruteo_run_id,
        run_id=run_id,
        version_reglas=ejecucion.version_reglas,
        estado=ejecucion.estado,
        clave_territorio=clave_territorio,
        total=total,
        pagina=paginacion.pagina,
        por_pagina=paginacion.por_pagina,
        elementos=[ParadaRutaRespuesta(**parada._mapping) for parada in paradas],
    )


def _buscar_territorial(s: Session, territorial_run_id: UUID) -> tuple[int, UUID, UUID]:
    """El id interno de la ejecucion territorial con ese territorial_run_id, el decision_run_id de
    sus decisiones y el run_id de su corrida, o 404. Valores y no la ejecucion: el id interno solo
    sirve para pedir la ejecucion de ruteo; no sale en ninguna respuesta."""
    fila = s.exec(
        select(EjecucionTerritorial.id, EjecucionDecision.decision_run_id, Corrida.run_id)
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
    ejecucion_territorial_id, decision_run_id, run_id = fila
    return ejecucion_territorial_id, decision_run_id, run_id


def _buscar_ruteo(s: Session, ruteo_run_id: UUID) -> tuple[EjecucionRuteo, UUID, UUID, UUID]:
    """La ejecucion de ruteo con ese ruteo_run_id y los identificadores publicos de su cadena (la
    ejecucion territorial, la de decision y la corrida), o 404. Una sola consulta, con JOIN, y por
    el identificador publico: los ids internos no salen de la base."""
    fila = s.exec(
        select(
            EjecucionRuteo,
            EjecucionTerritorial.territorial_run_id,
            EjecucionDecision.decision_run_id,
            Corrida.run_id,
        )
        .select_from(EjecucionRuteo)
        .join(
            EjecucionTerritorial, EjecucionRuteo.ejecucion_territorial_id == EjecucionTerritorial.id
        )
        .join(EjecucionDecision, EjecucionTerritorial.ejecucion_decision_id == EjecucionDecision.id)
        .join(Corrida, EjecucionDecision.corrida_id == Corrida.id)
        .where(EjecucionRuteo.ruteo_run_id == ruteo_run_id)
    ).first()
    if fila is None:
        raise ErrorDeApi(
            404,
            "RUTEO_NO_ENCONTRADO",
            f"No existe una ejecucion de ruteo con ruteo_run_id {ruteo_run_id}.",
        )
    ejecucion, territorial_run_id, decision_run_id, run_id = fila
    return ejecucion, territorial_run_id, decision_run_id, run_id


def _publicada(s: Session, ruteo_run_id: UUID) -> tuple[EjecucionRuteo, UUID, UUID, UUID]:
    """Como _buscar_ruteo, pero solo una EXITOSA: las demas no publicaron rutas, y 409 y no una
    lista vacia, que diria que no habia municipios que rutear."""
    ejecucion, territorial_run_id, decision_run_id, run_id = _buscar_ruteo(s, ruteo_run_id)
    if ejecucion.estado != EstadoRuteo.EXITOSA:
        raise ErrorDeApi(
            409,
            "RUTEO_NO_PUBLICADO",
            f"La ejecucion de ruteo esta {ejecucion.estado} y no publico rutas; solo una "
            "ejecucion EXITOSA tiene rutas y paradas.",
            run_id=run_id,
        )
    return ejecucion, territorial_run_id, decision_run_id, run_id


def _respuesta_ejecucion(
    ejecucion: EjecucionRuteo, territorial_run_id: UUID, decision_run_id: UUID, run_id: UUID
) -> EjecucionRuteoRespuesta:
    """La ejecucion como la ve un cliente: con los identificadores publicos de su cadena y sin los
    ids internos."""
    return EjecucionRuteoRespuesta(
        ruteo_run_id=ejecucion.ruteo_run_id,
        territorial_run_id=territorial_run_id,
        decision_run_id=decision_run_id,
        run_id=run_id,
        version_reglas=ejecucion.version_reglas,
        estado=ejecucion.estado,
        iniciada_en=ejecucion.iniciada_en,
        terminada_en=ejecucion.terminada_en,
        rutas_evaluadas=ejecucion.rutas_evaluadas,
        rutas_publicadas=ejecucion.rutas_publicadas,
        paradas_evaluadas=ejecucion.paradas_evaluadas,
        paradas_publicadas=ejecucion.paradas_publicadas,
        detalle=ejecucion.detalle,
    )
