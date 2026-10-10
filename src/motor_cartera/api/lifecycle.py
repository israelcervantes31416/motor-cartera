"""La linea de tiempo de una cuenta por HTTP: lo que paso con ella en las tres verdades.

`GET /cuentas/{cuenta_id}/lifecycle` junta, por tiempo de negocio, lo que la cobranza hizo
(OPERACIONAL), lo que los cortes de la cartera dicen de su promesa o de su plan (FUENTE_CORTE) y los
movimientos economicos que se interpretan de sus pagos (ECONOMICO), sin convertir uno en otro: cada
elemento dice de cual es, y trae su propia evidencia.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request

from motor_cartera.api.dependencias import SesionDeLectura
from motor_cartera.api.errores import errores
from motor_cartera.api.esquemas import ArtefactoRespuesta
from motor_cartera.api.esquemas_lifecycle import (
    ElementoLifecycleRespuesta,
    ObservacionEnCorteRespuesta,
    OperacionalRespuesta,
    PaginaLifecycle,
    ParametrosLifecycle,
)
from motor_cartera.api.gestiones import cuenta_de
from motor_cartera.api.motor_pagos import movimiento_respuesta
from motor_cartera.api.operacional import SIN_CLAVE, evento_respuesta
from motor_cartera.config import Config
from motor_cartera.lifecycle import consultas
from motor_cartera.lifecycle.consultas import (
    ElementoDeLinea,
    EventoVisto,
    ObservacionEnCorte,
)
from motor_cartera.motor_pagos.reglas import VERSION_MOTOR_PAGOS

router = APIRouter(tags=["lifecycle"])


@router.get(
    "/cuentas/{cuenta_id}/lifecycle",
    response_model=PaginaLifecycle,
    summary="La historia de la cuenta: gestiones, observaciones de corte y movimientos",
    responses=errores(
        *SIN_CLAVE,
        (404, "CUENTA_NO_ENCONTRADA", "No existe una cuenta canonica con ese cuenta_id."),
        (422, "ENTRADA_INVALIDA", "Un filtro no es valido, o la paginacion no lo es."),
    ),
)
def linea_de_tiempo(
    cuenta_id: UUID,
    request: Request,
    parametros: Annotated[ParametrosLifecycle, Query()],
    s: SesionDeLectura,
) -> PaginaLifecycle:
    """En orden de tiempo de negocio (`orden=desc` por omision), paginada en la base:

    - `OPERACIONAL`: cada evento del lifecycle (una gestion, su anulacion, una promesa, un
      convenio, una cancelacion) por su `ocurrido_en`. Un evento registrado tarde aparece donde
      ocurrio, y trae los dos tiempos;
    - `FUENTE_CORTE`: `OBSERVACION_EN_CORTE`, lo que un corte dice del estatus o del monto de la
      promesa o del plan de la cuenta, al inicio del dia de su corte, con su snapshot, su dataset,
      su fila y su archivo original. Es lo que dijo la fuente, no un evento: no crea promesas;
    - `ECONOMICO`: cada movimiento economico de la interpretacion vigente de los pagos, por su
      recepcion.

    Las horas de la fuente se leen en la zona horaria de la fuente. Con `desde` y `hasta` (dias,
    inclusive) y `dominio` se filtra en la base."""
    config: Config = request.app.state.config
    cuenta = cuenta_de(s, cuenta_id)
    total, pagina = consultas.linea_de_tiempo(
        s,
        cuenta,
        zona=config.zona_horaria_fuente,
        version_motor=VERSION_MOTOR_PAGOS,
        dominios=frozenset(consultas.Dominio)
        if parametros.dominio is None
        else frozenset({parametros.dominio}),
        desde=parametros.desde,
        hasta=parametros.hasta,
        descendente=parametros.orden == "desc",
        desplazamiento=parametros.desplazamiento,
        limite=parametros.por_pagina,
    )
    return PaginaLifecycle(
        cuenta_id=cuenta.cuenta_id,
        cliente_unico=cuenta.cliente_unico,
        orden=parametros.orden,
        version_motor=VERSION_MOTOR_PAGOS,
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[_elemento(e) for e in pagina],
    )


def _elemento(elemento: ElementoDeLinea) -> ElementoLifecycleRespuesta:
    return ElementoLifecycleRespuesta(
        dominio=elemento.dominio,
        tipo=elemento.tipo,
        instante=elemento.instante,
        operacional=None if elemento.evento is None else _operacional(elemento.evento),
        fuente_corte=None if elemento.observacion is None else _observacion(elemento.observacion),
        economico=None
        if elemento.movimiento is None
        else movimiento_respuesta(elemento.movimiento),
    )


def _operacional(visto: EventoVisto) -> OperacionalRespuesta:
    g, p, c = visto.gestion, visto.promesa, visto.convenio
    return OperacionalRespuesta(
        evento=evento_respuesta(visto.evento, visto.relacionado_id),
        gestion_id=None if g is None else g.gestion_id,
        promesa_id=None if p is None else p.promesa_id,
        convenio_id=None if c is None else c.convenio_id,
        canal=None if g is None else g.canal,
        nivel_contacto=None if g is None else g.nivel_contacto,
        resultado=None if g is None else g.resultado,
        monto_prometido=None if p is None else p.monto_prometido,
        fecha_limite=None if p is None else p.fecha_limite,
        monto_total_acordado=None if c is None else c.monto_total_acordado,
    )


def _observacion(observacion: ObservacionEnCorte) -> ObservacionEnCorteRespuesta:
    snapshot, original = observacion.snapshot, observacion.original
    return ObservacionEnCorteRespuesta(
        fecha_corte=snapshot.fecha_corte,
        corte_id=observacion.corte_id,
        dataset_id=observacion.dataset_id,
        source_row=snapshot.source_row,
        source_sheet=snapshot.source_sheet,
        artefacto_original=ArtefactoRespuesta.model_validate(original, from_attributes=True),
        estatus_promesa_pago=snapshot.estatus_promesa_pago,
        monto_promesa_pago=snapshot.monto_promesa_pago,
        estatus_plan=snapshot.estatus_plan,
        monto_plan=snapshot.monto_plan,
    )
