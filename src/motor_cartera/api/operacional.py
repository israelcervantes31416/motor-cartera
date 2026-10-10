"""Lo comun de las escrituras operacionales por HTTP: la llave de idempotencia, como responde una
repeticion, como se traducen los errores del registro y como se arma cada respuesta del lifecycle.

Contrato de una escritura operacional (POST de una gestion, una anulacion, una promesa, un
convenio o una cancelacion):

- exige la cabecera `Idempotency-Key`: sin ella, 422. La llave es del cliente y vale para su
  cartera: identifica una sola peticion;
- la primera vez registra el evento y responde `201 Created`, con el recurso y su `Location`;
- la misma llave con la misma peticion (la misma huella) no registra nada: responde `200 OK`, con
  el recurso como esta hoy, su `Location` y la cabecera `Idempotent-Replayed: true`. 200 y no 201
  porque esa peticion no creo nada: lo creo la primera;
- la misma llave con otra peticion responde `409 IDEMPOTENCY_KEY_REUTILIZADA` y no registra nada.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Header, Response

from motor_cartera.api.errores import DetalleError, ErrorDeApi
from motor_cartera.api.esquemas_lifecycle import (
    ConvenioRespuesta,
    CuotaRespuesta,
    EvaluacionDePromesaRespuesta,
    EventoLifecycleRespuesta,
    GestionRespuesta,
    PromesaRespuesta,
    RecuperacionObservadaRespuesta,
    VisitaRespuesta,
)
from motor_cartera.db.modelos import PATRON_LLAVE, EventoLifecycle
from motor_cartera.lifecycle import registro
from motor_cartera.lifecycle.consultas import (
    ConvenioVista,
    EvaluacionVista,
    GestionVista,
    PromesaVista,
)

LlaveDeIdempotencia = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        pattern=PATRON_LLAVE,
        description="La llave del cliente para esta peticion: de 8 a 128 letras, digitos, '.', "
        "'_', ':' o '-' (un UUID sirve). Repetir la peticion con la misma llave no registra nada "
        "otra vez; usarla para otra peticion es un 409.",
    ),
]

REPETIDA = "Idempotent-Replayed"

SIN_CLAVE = (
    (401, "API_KEY_AUSENTE", "Falta la cabecera X-API-Key."),
    (401, "API_KEY_INVALIDA", "La API key no es valida."),
)
LLAVE_REUTILIZADA = (
    409,
    "IDEMPOTENCY_KEY_REUTILIZADA",
    "La Idempotency-Key ya registro otra peticion, con otros datos o sobre otro recurso.",
)
ENTRADA_INVALIDA = (
    422,
    "ENTRADA_INVALIDA",
    "Falta la Idempotency-Key, o el cuerpo no cumple el contrato (un campo que no existe, como "
    "registrado_en, tambien).",
)
DATOS_PERSONALES = (
    422,
    "DATOS_PERSONALES",
    "Un texto libre parece traer un telefono, un correo, una tarjeta, una CURP o un RFC.",
)
EN_EL_FUTURO = (422, "OCURRIDO_EN_FUTURO", "ocurrido_en es posterior al momento de registrarlo.")
ANTES_DEL_EVENTO = (
    422,
    "OCURRIDO_ANTES_DEL_EVENTO",
    "Ocurre antes de lo que modifica: una promesa antes de su gestion, una cancelacion antes de su "
    "promesa.",
)


def responder(response: Response, hecho: registro.Registro, ubicacion: str) -> None:
    """201 con Location si la peticion registro algo; 200, Location e Idempotent-Replayed si la
    misma peticion ya lo habia registrado."""
    response.headers["Location"] = ubicacion
    if not hecho.nuevo:
        response.status_code = 200
        response.headers[REPETIDA] = "true"


def traducir(exc: Exception) -> ErrorDeApi:
    """El error del registro como error de la API, con su codigo estable."""
    if isinstance(exc, registro.LlaveReutilizada):
        return ErrorDeApi(409, "IDEMPOTENCY_KEY_REUTILIZADA", str(exc))
    if isinstance(exc, registro.RecursoNoEncontrado):
        codigos = {
            "una cuenta canonica con cuenta_id": "CUENTA_NO_ENCONTRADA",
            "una gestion con gestion_id": "GESTION_NO_ENCONTRADA",
            "una promesa con promesa_id": "PROMESA_NO_ENCONTRADA",
            "un convenio con convenio_id": "CONVENIO_NO_ENCONTRADO",
        }
        return ErrorDeApi(404, codigos[exc.recurso], str(exc))
    if isinstance(exc, registro.PeticionInvalida):
        error = ErrorDeApi(422, exc.codigo, " ".join(exc.problemas))
        error.respuesta.detalles = [DetalleError(problema=p) for p in exc.problemas]
        return error
    if isinstance(exc, registro.Conflicto):
        return ErrorDeApi(409, exc.codigo, str(exc))
    raise exc


ERRORES_DEL_REGISTRO = (
    registro.LlaveReutilizada,
    registro.RecursoNoEncontrado,
    registro.PeticionInvalida,
    registro.Conflicto,
)


# --- las respuestas -------------------------------------------------------------------------------


def evento_respuesta(evento: EventoLifecycle, relacionado_id=None) -> EventoLifecycleRespuesta:
    return EventoLifecycleRespuesta(
        evento_id=evento.evento_id,
        tipo_evento=evento.tipo_evento,
        ocurrido_en=evento.ocurrido_en,
        registrado_en=evento.registrado_en,
        origen_registro=evento.origen_registro,
        actor_ref=evento.actor_ref,
        motivo=evento.motivo,
        evento_relacionado_id=relacionado_id,
        version_evento=evento.version_evento,
        idempotency_key=evento.idempotency_key,
    )


def gestion_respuesta(vista: GestionVista) -> GestionRespuesta:
    g, visita = vista.gestion, vista.visita
    return GestionRespuesta(
        gestion_id=g.gestion_id,
        cuenta_id=vista.cuenta_id,
        cliente_unico=vista.cliente_unico,
        estado=vista.estado,
        ocurrido_en=g.ocurrido_en,
        registrado_en=vista.evento.registrado_en,
        canal=g.canal,
        medio=g.medio,
        nivel_contacto=g.nivel_contacto,
        resultado=g.resultado,
        actor_ref=g.actor_ref,
        observacion=g.observacion,
        visita=None
        if visita is None
        else VisitaRespuesta(
            visita_id=visita.visita_id,
            resultado=visita.resultado,
            inicio=visita.inicio,
            fin=visita.fin,
            observacion=visita.observacion,
        ),
        promesa_id=vista.promesa_id,
        convenio_id=vista.convenio_id,
        evento=evento_respuesta(vista.evento),
        anulacion=None
        if vista.anulacion is None
        else evento_respuesta(vista.anulacion, vista.evento.evento_id),
    )


def evaluacion_respuesta(vista: EvaluacionVista) -> EvaluacionDePromesaRespuesta:
    e, ejecucion = vista.evaluacion, vista.ejecucion
    return EvaluacionDePromesaRespuesta(
        evaluacion_run_id=ejecucion.evaluacion_run_id,
        version_evaluacion=ejecucion.version_evaluacion,
        as_of=ejecucion.as_of,
        estado=e.estado,
        monto_observado=e.monto_observado,
        movimientos_compatibles=e.movimientos_compatibles,
        primer_movimiento_en=e.primer_movimiento_en,
        ultimo_movimiento_en=e.ultimo_movimiento_en,
        motivos=e.motivos,
    )


def promesa_respuesta(vista: PromesaVista) -> PromesaRespuesta:
    p, gestion_evento_id = vista.promesa, vista.gestion_evento_id
    return PromesaRespuesta(
        promesa_id=p.promesa_id,
        cuenta_id=vista.cuenta_id,
        cliente_unico=vista.cliente_unico,
        gestion_id=vista.gestion_id,
        monto_prometido=p.monto_prometido,
        fecha_limite=p.fecha_limite,
        creada_en=vista.evento.ocurrido_en,
        registrada_en=vista.evento.registrado_en,
        version_modelo=p.version_modelo,
        estado_operativo=vista.estado,
        ultima_evaluacion=None
        if vista.ultima_evaluacion is None
        else evaluacion_respuesta(vista.ultima_evaluacion),
        evento=evento_respuesta(vista.evento, gestion_evento_id),
        cancelacion=None
        if vista.cancelacion is None
        else evento_respuesta(vista.cancelacion, vista.evento.evento_id),
        anulacion=None
        if vista.anulacion is None
        else evento_respuesta(vista.anulacion, gestion_evento_id),
    )


def convenio_respuesta(vista: ConvenioVista, version_motor: str) -> ConvenioRespuesta:
    c, recuperacion = vista.convenio, vista.recuperacion
    gestion_evento_id = vista.gestion_evento_id
    return ConvenioRespuesta(
        convenio_id=c.convenio_id,
        cuenta_id=vista.cuenta_id,
        cliente_unico=vista.cliente_unico,
        gestion_id=vista.gestion_id,
        monto_total_acordado=c.monto_total_acordado,
        fecha_inicio=c.fecha_inicio,
        fecha_fin=c.fecha_fin,
        creado_en=vista.evento.ocurrido_en,
        registrado_en=vista.evento.registrado_en,
        version_modelo=c.version_modelo,
        estado_operativo=vista.estado,
        cuotas=[
            CuotaRespuesta(numero=q.numero, fecha_vencimiento=q.fecha_vencimiento, monto=q.monto)
            for q in vista.cuotas
        ],
        recuperacion_observada_durante_convenio=None
        if recuperacion is None
        else RecuperacionObservadaRespuesta(
            monto=recuperacion.monto,
            movimientos=recuperacion.movimientos,
            desde=recuperacion.desde,
            hasta=recuperacion.hasta,
            horizonte_pagos=recuperacion.horizonte_pagos,
            version_motor=version_motor,
        ),
        evento=evento_respuesta(vista.evento, gestion_evento_id),
        cancelacion=None
        if vista.cancelacion is None
        else evento_respuesta(vista.cancelacion, vista.evento.evento_id),
        anulacion=None
        if vista.anulacion is None
        else evento_respuesta(vista.anulacion, gestion_evento_id),
    )
