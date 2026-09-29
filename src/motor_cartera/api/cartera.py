"""GET /cartera/resumen: la cartera vigente, segmentada."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

from fastapi import APIRouter, Query

from motor_cartera.api.dependencias import Sesion, buscar_corrida
from motor_cartera.api.errores import ErrorDeApi, errores
from motor_cartera.api.esquemas import ParametrosResumen, ResumenCartera, SegmentoRespuesta
from motor_cartera.db.modelos import EstadoCorrida
from motor_cartera.ingesta.corridas import corrida_vigente
from motor_cartera.segmentacion import resumir

router = APIRouter(prefix="/cartera", tags=["cartera"])

CENTAVO = Decimal("0.01")


@router.get(
    "/resumen",
    response_model=ResumenCartera,
    summary="Resumen segmentado de la cartera: cuentas y saldo por segmento",
    responses=errores(
        (401, "API_KEY_AUSENTE", "Falta la cabecera X-API-Key."),
        (401, "API_KEY_INVALIDA", "La API key no es valida."),
        (404, "SIN_CARTERA_PUBLICADA", "Todavia ninguna corrida ha publicado cartera."),
        (404, "CORRIDA_NO_ENCONTRADA", "No existe una corrida con el run_id pedido."),
        (409, "CORRIDA_NO_PUBLICADA", "La corrida pedida no termino EXITOSA: no hay que resumir."),
        (422, "ENTRADA_INVALIDA", "Dimension, filtro o paginacion invalidos."),
    ),
)
def resumen(parametros: Annotated[ParametrosResumen, Query()], s: Sesion) -> ResumenCartera:
    """Resume la **cartera vigente**: la corrida EXITOSA con la fecha de corte mas reciente.
    Por corte y no por hora de subida: subir hoy la cartera de la semana pasada no la
    vuelve vigente. Con `run_id` se resume esa corrida en particular.

    Cada respuesta dice de que corrida sale (`run_id`): cualquier numero se puede rastrear
    hasta la corrida y el archivo que lo produjeron.

    **404 si no hay nada publicado**, y no un resumen vacio: uno vacio no se distingue de
    una cartera con cero cuentas, y es justo el tipo de salida enganosa que el diseno evita.
    """
    if parametros.run_id is None:
        corrida = corrida_vigente(s)
        if corrida is None:
            raise ErrorDeApi(
                404,
                "SIN_CARTERA_PUBLICADA",
                "Todavia no hay cartera publicada: ninguna corrida ha terminado EXITOSA.",
            )
    else:
        corrida = buscar_corrida(s, parametros.run_id)
        if corrida.estado != EstadoCorrida.EXITOSA:
            raise ErrorDeApi(
                409,
                "CORRIDA_NO_PUBLICADA",
                f"La corrida esta {corrida.estado} y no publico cartera; solo se resume "
                "una corrida EXITOSA.",
                run_id=corrida.run_id,
            )

    resultado = resumir(
        s,
        corrida.id,
        parametros.por,
        producto=parametros.producto,
        canal=parametros.canal,
        desplazamiento=parametros.desplazamiento,
        limite=parametros.por_pagina,
    )
    return ResumenCartera(
        run_id=corrida.run_id,
        fecha_corte=corrida.fecha_corte,
        dimensiones=parametros.por,
        total_cuentas=resultado.cuentas,
        saldo_total=resultado.saldo_total,
        total=resultado.total_segmentos,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[
            SegmentoRespuesta(
                segmento=segmento.claves,
                cuentas=segmento.cuentas,
                saldo_total=segmento.saldo_total,
                saldo_promedio=(segmento.saldo_total / segmento.cuentas).quantize(
                    CENTAVO, rounding=ROUND_HALF_UP
                ),
            )
            for segmento in resultado.segmentos
        ],
    )
