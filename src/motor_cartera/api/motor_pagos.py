"""El motor de pagos por HTTP: las ejecuciones que interpretan cada ventana y lo que concluyeron.

No hay un POST: la interpretacion de una ventana se abre sola, cuando la historia publica sus pagos
observados, y la de los pagos anteriores (o una reconciliacion) la encola
`motor-cartera backfill-motor-pagos`. Aqui se lee como va cada interpretacion, cual es la vigente de
cada ventana y que concluyo de cada observacion, siempre por identificadores publicos.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request

from motor_cartera.api.dependencias import SesionDeLectura
from motor_cartera.api.errores import ErrorDeApi, errores
from motor_cartera.api.esquemas import (
    CalidadMotorPagosRespuesta,
    ContextoTemporalRespuesta,
    EjecucionMotorPagosRespuesta,
    MotivoMotorPagosRespuesta,
    MovimientoRespuesta,
    PaginaEjecucionesMotorPagos,
    PaginaResultadosPago,
    ParametrosEjecucionesMotorPagos,
    ParametrosResultados,
    RecuperacionInterpretadaRespuesta,
    ResultadoPagoRespuesta,
    SnapshotDelContextoRespuesta,
)
from motor_cartera.config import Config
from motor_cartera.motor_pagos import consultas
from motor_cartera.motor_pagos.consultas import (
    EjecucionNoEncontrada,
    EjecucionVista,
    MovimientoVisto,
    ResultadoVisto,
)
from motor_cartera.motor_pagos.contexto import ContextoTemporal

router = APIRouter(tags=["motor-pagos"])

SIN_CLAVE = (
    (401, "API_KEY_AUSENTE", "Falta la cabecera X-API-Key."),
    (401, "API_KEY_INVALIDA", "La API key no es valida."),
)
NO_EXISTE = (
    404,
    "MOTOR_PAGOS_NO_ENCONTRADO",
    "No existe una ejecucion del motor de pagos con ese motor_pagos_run_id.",
)


@router.get(
    "/motor-pagos",
    response_model=PaginaEjecucionesMotorPagos,
    summary="Las interpretaciones de los pagos de la cartera del sistema, por ventana",
    responses=errores(
        *SIN_CLAVE,
        (422, "ENTRADA_INVALIDA", "El periodo no es AAAA-MM, o la paginacion no es valida."),
    ),
)
def listar(
    request: Request,
    parametros: Annotated[ParametrosEjecucionesMotorPagos, Query()],
    s: SesionDeLectura,
) -> PaginaEjecucionesMotorPagos:
    """Una ejecucion por cada vez que se interpreto una ventana (un mes de recepcion), la
    ventana mas reciente primero. `vigente` marca la que vale hoy en cada ventana: su EXITOSA mas
    reciente. Las demas son historia: una interpretacion anterior a pagos o cortes que llegaron
    despues, o una que fallo, y no cambian."""
    config: Config = request.app.state.config
    periodo = None
    if parametros.periodo is not None:
        anio, mes = parametros.periodo.split("-")
        periodo = date(int(anio), int(mes), 1)
    total, pagina = consultas.listar_ejecuciones(
        s,
        despacho_id=config.despacho_id,
        cartera_id=config.cartera_id,
        version=parametros.version,
        periodo=periodo,
        estado=parametros.estado,
        desplazamiento=parametros.desplazamiento,
        limite=parametros.por_pagina,
    )
    return PaginaEjecucionesMotorPagos(
        despacho_id=config.despacho_id,
        cartera_id=config.cartera_id,
        version_motor=parametros.version,
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[ejecucion_respuesta(vista) for vista in pagina],
    )


@router.get(
    "/motor-pagos/{motor_pagos_run_id}",
    response_model=EjecucionMotorPagosRespuesta,
    summary="Una interpretacion de una ventana: version, estado, calidad y recuperacion",
    responses=errores(
        *SIN_CLAVE, NO_EXISTE, (422, "ENTRADA_INVALIDA", "El motor_pagos_run_id no es un UUID.")
    ),
)
def obtener(motor_pagos_run_id: UUID, s: SesionDeLectura) -> EjecucionMotorPagosRespuesta:
    """Mientras el estado sea `EN_PROCESO`, un worker no la ha terminado. `calidad` dice cuantas
    observaciones de cada clase encontro, cuantos grupos de copias y de la llave historica, y
    cuantas sin cuenta; `recuperacion`, la bruta y la neta interpretadas, que no son contabilidad
    del acreedor."""
    return ejecucion_respuesta(_ejecucion(s, motor_pagos_run_id))


@router.get(
    "/motor-pagos/{motor_pagos_run_id}/resultados",
    response_model=PaginaResultadosPago,
    summary="Lo que la ejecucion concluyo de cada pago observado de su ventana, y por que",
    responses=errores(
        *SIN_CLAVE,
        NO_EXISTE,
        (
            422,
            "ENTRADA_INVALIDA",
            "El motor_pagos_run_id no es un UUID, la clasificacion no existe, una firma no son 64 "
            "digitos hexadecimales o la paginacion no es valida.",
        ),
    ),
)
def resultados(
    motor_pagos_run_id: UUID,
    parametros: Annotated[ParametrosResultados, Query()],
    s: SesionDeLectura,
) -> PaginaResultadosPago:
    """Un resultado por observacion, en orden de recepcion: su clasificacion, su conciliacion, sus
    dos huellas, su movimiento y sus motivos. Con `clasificacion=COINCIDENCIA_AMBIGUA` se ve que
    observaciones no se interpretaron y que campos las distinguen; con `cliente_unico`, las de un
    cliente; con `firma_exacta` o `firma_legacy`, las de un grupo: las copias de un pago, o las
    observaciones que comparten una llave historica."""
    vista = _ejecucion(s, motor_pagos_run_id)
    total, pagina = consultas.resultados_de(
        s,
        vista.ejecucion,
        clasificacion=parametros.clasificacion,
        cliente_unico=parametros.cliente_unico,
        firma_exacta=_huella(parametros.firma_exacta),
        firma_legacy=_huella(parametros.firma_legacy),
        desplazamiento=parametros.desplazamiento,
        limite=parametros.por_pagina,
    )
    return PaginaResultadosPago(
        motor_pagos_run_id=motor_pagos_run_id,
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[resultado_respuesta(visto) for visto in pagina],
    )


def _huella(hexadecimal: str | None) -> bytes | None:
    return None if hexadecimal is None else bytes.fromhex(hexadecimal)


def _ejecucion(s, motor_pagos_run_id: UUID) -> EjecucionVista:
    try:
        return consultas.obtener_ejecucion(s, motor_pagos_run_id)
    except EjecucionNoEncontrada as exc:
        raise ErrorDeApi(
            404,
            NO_EXISTE[1],
            f"No existe una ejecucion del motor de pagos con motor_pagos_run_id "
            f"{motor_pagos_run_id}.",
        ) from exc


def ejecucion_respuesta(vista: EjecucionVista) -> EjecucionMotorPagosRespuesta:
    e = vista.ejecucion
    return EjecucionMotorPagosRespuesta(
        motor_pagos_run_id=e.motor_pagos_run_id,
        version_motor=e.version_motor,
        estado=e.estado,
        resultado=e.resultado,
        despacho_id=e.despacho_id,
        cartera_id=e.cartera_id,
        periodo=f"{e.periodo_desde:%Y-%m}",
        periodo_desde=e.periodo_desde,
        periodo_hasta=e.periodo_hasta,
        vigente=vista.vigente,
        firma_entrada=e.firma_entrada,
        calidad=CalidadMotorPagosRespuesta(
            observaciones_leidas=e.observaciones_leidas,
            observaciones_contexto=e.observaciones_contexto,
            observaciones_clasificadas=e.observaciones_clasificadas,
            movimientos_canonicos=e.movimientos_canonicos,
            movimientos_primarios=e.primarios,
            duplicados_exactos=e.duplicados_exactos,
            coincidencias_ambiguas=e.coincidencias_ambiguas,
            reversos=e.reversos,
            posibles_reversos=e.posibles_reversos,
            no_conciliados=e.no_conciliados,
            sin_cuenta_observada=e.sin_cuenta_observada,
            grupos_exactos=e.grupos_exactos,
            grupos_legacy=e.grupos_legacy,
            grupos_ambiguos=e.grupos_ambiguos,
            observaciones_en_grupos_legacy=e.observaciones_en_grupos_legacy,
            pagos_anulados=e.pagos_anulados,
        ),
        recuperacion=RecuperacionInterpretadaRespuesta(
            bruta_interpretada=e.recuperacion_bruta_interpretada,
            neta_interpretada=e.recuperacion_neta_interpretada,
            importe_ambiguo_observado=e.importe_ambiguo_observado,
        ),
        trabajo_id=vista.trabajo_id,
        iniciada_en=e.iniciada_en,
        terminada_en=e.terminada_en,
        detalle=e.detalle,
    )


def motivos_respuesta(motivos: list[dict]) -> list[MotivoMotorPagosRespuesta]:
    return [MotivoMotorPagosRespuesta(**motivo) for motivo in motivos]


def resultado_respuesta(visto: ResultadoVisto) -> ResultadoPagoRespuesta:
    r, p = visto.resultado, visto.pago
    return ResultadoPagoRespuesta(
        pago_observado_id=p.pago_observado_id,
        cliente_unico=p.cliente_unico,
        fecha_recepcion=p.fecha_recepcion,
        recuperacion_por_gestion=p.recuperacion_por_gestion,
        clasificacion=r.clasificacion,
        estado_conciliacion=r.estado_conciliacion,
        movimiento_id=visto.movimiento_id,
        movimiento_relacionado_id=r.movimiento_relacionado_id,
        firma_exacta=r.firma_exacta.hex(),
        firma_legacy=r.firma_legacy.hex(),
        motivos=motivos_respuesta(r.motivos),
        pagos_run_id=visto.pagos_run_id,
        dataset_id=visto.dataset_id,
        source_row=p.source_row,
        source_sheet=p.source_sheet,
    )


def movimiento_respuesta(visto: MovimientoVisto) -> MovimientoRespuesta:
    m = visto.movimiento
    return MovimientoRespuesta(
        movimiento_id=m.movimiento_id,
        version_motor=m.version_motor,
        motor_pagos_run_id=visto.motor_pagos_run_id,
        periodo=f"{visto.periodo_desde:%Y-%m}",
        vigente=visto.vigente,
        tipo_movimiento=m.tipo_movimiento,
        signo_economico=m.signo_economico,
        monto_reportado=m.monto_reportado,
        fecha_recepcion=m.fecha_recepcion,
        cliente_unico=m.cliente_unico,
        cuenta_id=visto.cuenta_id,
        estado_conciliacion=m.estado_conciliacion,
        observaciones=m.observaciones,
        movimiento_original_id=m.movimiento_original_id,
        anulado_por_movimiento_id=m.anulado_por_movimiento_id,
        firma_exacta=m.firma_exacta.hex(),
    )


def contexto_respuesta(contexto: ContextoTemporal, snapshots: dict) -> ContextoTemporalRespuesta:
    def resumido(fecha):
        if fecha is None:
            return None
        corte_id, snapshot = snapshots[fecha]
        return SnapshotDelContextoRespuesta(
            fecha_corte=fecha,
            corte_id=corte_id,
            saldo_total=snapshot.saldo_total,
            dias_atraso=snapshot.dias_atraso,
        )

    return ContextoTemporalRespuesta(
        snapshot_anterior=resumido(contexto.snapshot_anterior),
        snapshot_siguiente=resumido(contexto.snapshot_siguiente),
        antes_de_primera_observacion=contexto.antes_de_primera_observacion,
        despues_de_ultima_observacion=contexto.despues_de_ultima_observacion,
        durante_ausencia_observada=contexto.durante_ausencia_observada,
    )
