"""Cuenta 360 por HTTP: una cuenta canonica, su historia, sus eventos y sus pagos observados.

La logica vive en `historia.cuenta360`; aqui solo se buscan los recursos por sus identificadores
publicos y se arman las respuestas. `GET /cuentas/{cuenta_id}` es un resumen: la historia, los
eventos y los pagos son subrecursos paginados, para que ninguna respuesta crezca sin control.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request
from sqlmodel import Session

from motor_cartera.api.dependencias import Sesion
from motor_cartera.api.errores import ErrorDeApi, errores
from motor_cartera.api.esquemas import (
    AVISO_PAGOS_OBSERVADOS,
    Cuenta360Respuesta,
    CuentaEncontradaRespuesta,
    EventoRespuesta,
    Paginacion,
    PaginaEventos,
    PaginaHistoria,
    PaginaPagosObservados,
    PagoObservadoRespuesta,
    ParametrosHistoria,
    SnapshotEnHistoriaRespuesta,
    SnapshotRespuesta,
)
from motor_cartera.config import Config
from motor_cartera.contratos.cartera_v2 import CLIENTE
from motor_cartera.db.modelos import CuentaCanonica
from motor_cartera.historia import cuenta360
from motor_cartera.historia.cuenta360 import (
    CuentaNoEncontrada,
    PagoVisto,
    SinCuentaObservada,
    SnapshotEnHistoria,
    SnapshotVisto,
)

router = APIRouter(prefix="/cuentas", tags=["cuentas"])

SIN_CLAVE = (
    (401, "API_KEY_AUSENTE", "Falta la cabecera X-API-Key."),
    (401, "API_KEY_INVALIDA", "La API key no es valida."),
)
NO_EXISTE = (404, "CUENTA_NO_ENCONTRADA", "No existe una cuenta canonica con ese cuenta_id.")


@router.get(
    "",
    response_model=CuentaEncontradaRespuesta,
    summary="Busca la cuenta canonica de un CLIENTE_UNICO en la cartera del sistema",
    responses=errores(
        *SIN_CLAVE,
        (
            404,
            "CUENTA_NO_ENCONTRADA",
            "Ningun corte canonico de la cartera trae ese CLIENTE_UNICO, y no hay pagos suyos.",
        ),
        (
            404,
            "SIN_CUENTA_OBSERVADA",
            "Hay pagos observados de ese CLIENTE_UNICO, pero ningun corte de la cartera lo trae.",
        ),
        (422, "ENTRADA_INVALIDA", "Falta cliente_unico, o no tiene la forma de cartera/v2."),
    ),
)
def buscar_cuenta(
    request: Request,
    s: Sesion,
    cliente_unico: Annotated[
        str,
        Query(
            pattern=f"^{CLIENTE}$",
            description="El identificador fuente de la cuenta, como en cartera/v2.",
        ),
    ],
) -> CuentaEncontradaRespuesta:
    """Busca en el despacho y la cartera del sistema (`MC_DESPACHO_ID`, `MC_CARTERA_ID`) y devuelve
    su `cuenta_id` publico, con el que se piden su resumen, su historia, sus eventos y sus pagos.

    Una cuenta canonica nace con el primer corte que trae su CLIENTE_UNICO. **Un pago no la crea**:
    si hay pagos de ese cliente pero ningun corte lo trae, la respuesta es
    `404 SIN_CUENTA_OBSERVADA`, y no un 200 con una cuenta inventada.
    """
    config: Config = request.app.state.config
    try:
        cuenta = cuenta360.buscar(s, config.despacho_id, config.cartera_id, cliente_unico)
    except SinCuentaObservada as exc:
        raise ErrorDeApi(
            404,
            "SIN_CUENTA_OBSERVADA",
            f"Hay {exc.pagos:,} pagos observados de {cliente_unico}, pero ningun corte "
            f"canonico de {config.despacho_id}/{config.cartera_id} lo trae: no tiene cuenta "
            "canonica. Sus pagos se conservan, y se relacionan con su cuenta cuando un corte la "
            "traiga.",
        ) from exc
    except CuentaNoEncontrada as exc:
        raise ErrorDeApi(
            404,
            "CUENTA_NO_ENCONTRADA",
            f"Ningun corte canonico de {config.despacho_id}/{config.cartera_id} trae a "
            f"{cliente_unico}.",
        ) from exc
    return CuentaEncontradaRespuesta(
        cuenta_id=cuenta.cuenta_id,
        cliente_unico=cuenta.cliente_unico,
        despacho_id=cuenta.despacho_id,
        cartera_id=cuenta.cartera_id,
    )


@router.get(
    "/{cuenta_id}",
    response_model=Cuenta360Respuesta,
    summary="Cuenta 360: la cuenta a traves de sus cortes, en resumen",
    responses=errores(
        *SIN_CLAVE,
        NO_EXISTE,
        (422, "ENTRADA_INVALIDA", "El cuenta_id no es un UUID, o la fecha no es AAAA-MM-DD."),
    ),
)
def obtener_cuenta(
    cuenta_id: UUID,
    s: Sesion,
    al: Annotated[
        date | None,
        Query(
            description="La cuenta como se veia en esa fecha: con los cortes de fecha hasta ese "
            "dia, y los pagos recibidos hasta el final de ese dia. Por omision, hoy."
        ),
    ] = None,
) -> Cuenta360Respuesta:
    """Cuando se observo por primera y por ultima vez, si esta en el ultimo corte de su cartera
    (`EN_CARTERA`) o no (`NO_OBSERVADA_EN_ULTIMO_CORTE`), cuantos cortes falto desde su primera
    observacion, cuantas veces salio y reingreso, cuantos pagos observados tiene y su snapshot.

    `snapshot_actual` es el del ultimo corte de la cartera, y es null si la cuenta no esta en el;
    `ultimo_snapshot_observado` es el del ultimo corte en que se observo. **Ninguno de los dos
    estados es crediticio**: que una cuenta no aparezca no dice si se liquido, se cancelo, se
    castigo o se vendio.

    La historia, los eventos y los pagos son subrecursos paginados: `/historia`, `/eventos` y
    `/pagos-observados`.
    """
    cuenta = _cuenta(s, cuenta_id)
    vista = cuenta360.resumen(s, cuenta, al)
    p = vista.presencia
    return Cuenta360Respuesta(
        cuenta_id=cuenta.cuenta_id,
        cliente_unico=cuenta.cliente_unico,
        despacho_id=cuenta.despacho_id,
        cartera_id=cuenta.cartera_id,
        al=al,
        primera_observacion=p.primera_observacion,
        ultima_observacion=p.ultima_observacion,
        ultimo_corte_cartera=None if p.ultimo_corte is None else p.ultimo_corte.fecha_corte,
        estado_presencia=p.estado,
        cortes_observados=p.cortes_observados,
        cortes_ausentes_desde_primera_observacion=p.cortes_ausentes,
        salidas_observadas=p.salidas_observadas,
        reingresos_observados=p.reingresos_observados,
        pagos_observados=vista.pagos_observados,
        snapshot_actual=_snapshot(vista.snapshot_actual),
        ultimo_snapshot_observado=_snapshot(vista.ultimo_snapshot_observado),
    )


@router.get(
    "/{cuenta_id}/historia",
    response_model=PaginaHistoria,
    summary="Los snapshots de la cuenta, corte por corte, con su continuidad y sus deltas",
    responses=errores(
        *SIN_CLAVE,
        NO_EXISTE,
        (422, "ENTRADA_INVALIDA", "El cuenta_id no es un UUID, o la paginacion no es valida."),
    ),
)
def historia(
    cuenta_id: UUID, parametros: Annotated[ParametrosHistoria, Query()], s: Sesion
) -> PaginaHistoria:
    """Un snapshot por cada corte en que se observo la cuenta, del mas reciente al mas antiguo
    (`orden=asc` para el otro sentido), con su evidencia: `corte_id`, `dataset_id`, `source_row` y
    `source_sheet`.

    Cada uno dice si es **continuo** con el anterior de la cuenta (`continuo_desde_anterior`): solo
    lo es si los dos cortes son consecutivos en su cartera. Si la cuenta falto en medio,
    `cortes_ausentes_desde_anterior` dice cuantos, y los deltas son la diferencia entre las dos
    observaciones, no la evolucion de un periodo continuo.
    """
    cuenta = _cuenta(s, cuenta_id)
    total, pagina = cuenta360.historia(
        s,
        cuenta,
        desplazamiento=parametros.desplazamiento,
        limite=parametros.por_pagina,
        descendente=parametros.orden == "desc",
    )
    return PaginaHistoria(
        cuenta_id=cuenta.cuenta_id,
        cliente_unico=cuenta.cliente_unico,
        orden=parametros.orden,
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[_en_historia(elemento) for elemento in pagina],
    )


@router.get(
    "/{cuenta_id}/eventos",
    response_model=PaginaEventos,
    summary="Los eventos de presencia de la cuenta: primera observacion, salidas y reingresos",
    responses=errores(
        *SIN_CLAVE,
        NO_EXISTE,
        (422, "ENTRADA_INVALIDA", "El cuenta_id no es un UUID, o la paginacion no es valida."),
    ),
)
def eventos(
    cuenta_id: UUID, paginacion: Annotated[Paginacion, Query()], s: Sesion
) -> PaginaEventos:
    """En orden de corte. Se calculan al consultar, a partir de sus snapshots y de los cortes de su
    cartera: no se guardan. Un corte atrasado que llega despues cambia la respuesta exactamente como
    si hubiera llegado a tiempo.

    `PRIMERA_OBSERVACION` no es un alta ni una originacion; `SALIDA_OBSERVADA` no es una
    liquidacion, una cancelacion ni un castigo: solo que en ese corte ya no se observo.
    """
    cuenta = _cuenta(s, cuenta_id)
    todos = cuenta360.eventos(s, cuenta)
    inicio = paginacion.desplazamiento
    return PaginaEventos(
        cuenta_id=cuenta.cuenta_id,
        cliente_unico=cuenta.cliente_unico,
        total=len(todos),
        pagina=paginacion.pagina,
        por_pagina=paginacion.por_pagina,
        elementos=[
            EventoRespuesta(
                tipo=e.tipo,
                fecha_corte=e.fecha_corte,
                corte_id=e.corte_id,
                ultima_observacion=e.ultima_observacion,
                cortes_ausente=e.cortes_ausente,
            )
            for e in todos[inicio : inicio + paginacion.por_pagina]
        ],
    )


@router.get(
    "/{cuenta_id}/pagos-observados",
    response_model=PaginaPagosObservados,
    summary="Los movimientos observados de pagos/v1 de la cuenta, sin deduplicar ni conciliar",
    description=(
        f"**{AVISO_PAGOS_OBSERVADOS}**\n\n"
        "Los movimientos con el CLIENTE_UNICO de la cuenta, en su cartera, del mas reciente al mas "
        "antiguo por `fecha_recepcion`, cada uno con su evidencia: la ingesta que lo acepto "
        "(`pagos_run_id`), su dataset conformado y su fila. Dos filas identicas son dos "
        "observaciones, y el mismo movimiento en dos archivos tambien. Incluye los que llegaron "
        "antes del primer corte que trajo a la cuenta. No hay un total de dinero: una suma de "
        "estos movimientos no es una recuperacion."
    ),
    responses=errores(
        *SIN_CLAVE,
        NO_EXISTE,
        (422, "ENTRADA_INVALIDA", "El cuenta_id no es un UUID, o la paginacion no es valida."),
    ),
)
def pagos_observados(
    cuenta_id: UUID, paginacion: Annotated[Paginacion, Query()], s: Sesion
) -> PaginaPagosObservados:
    cuenta = _cuenta(s, cuenta_id)
    total, pagina = cuenta360.pagos_observados(
        s, cuenta, desplazamiento=paginacion.desplazamiento, limite=paginacion.por_pagina
    )
    return PaginaPagosObservados(
        cuenta_id=cuenta.cuenta_id,
        cliente_unico=cuenta.cliente_unico,
        total=total,
        pagina=paginacion.pagina,
        por_pagina=paginacion.por_pagina,
        elementos=[_pago(visto) for visto in pagina],
    )


def _cuenta(s: Session, cuenta_id: UUID) -> CuentaCanonica:
    try:
        return cuenta360.obtener(s, cuenta_id)
    except CuentaNoEncontrada as exc:
        raise ErrorDeApi(
            404, "CUENTA_NO_ENCONTRADA", f"No existe una cuenta canonica con cuenta_id {cuenta_id}."
        ) from exc


def _campos(visto: SnapshotVisto) -> dict:
    s = visto.snapshot
    return {
        "fecha_corte": s.fecha_corte,
        "corte_id": visto.corte_id,
        "saldo_total": s.saldo_total,
        "saldo": s.saldo,
        "moratorios": s.moratorios,
        "saldo_atrasado": s.saldo_atrasado,
        "saldo_requerido": s.saldo_requerido,
        "pago_normal": s.pago_normal,
        "dias_atraso": s.dias_atraso,
        "atraso_maximo": s.atraso_maximo,
        "semanas_atraso": s.semanas_atraso,
        "producto": s.producto,
        "estrategia": s.estrategia,
        "canal": s.canal,
        "fecha_ultimo_pago": s.fecha_ultimo_pago,
        "imp_ultimo_pago": s.imp_ultimo_pago,
        "cve_entidad": s.cve_entidad,
        "cve_municipio": s.cve_municipio,
        "estatus_plan": s.estatus_plan,
        "monto_plan": s.monto_plan,
        "pagos_recibidos": s.pagos_recibidos,
        "estatus_promesa_pago": s.estatus_promesa_pago,
        "monto_promesa_pago": s.monto_promesa_pago,
        "dataset_id": visto.dataset_id,
        "source_row": s.source_row,
        "source_sheet": s.source_sheet,
    }


def _snapshot(visto: SnapshotVisto | None) -> SnapshotRespuesta | None:
    return None if visto is None else SnapshotRespuesta(**_campos(visto))


def _en_historia(elemento: SnapshotEnHistoria) -> SnapshotEnHistoriaRespuesta:
    return SnapshotEnHistoriaRespuesta(
        **_campos(elemento.visto),
        continuo_desde_anterior=elemento.enlace.continuo_desde_anterior,
        cortes_ausentes_desde_anterior=elemento.enlace.cortes_ausentes_desde_anterior,
        delta_saldo_total=elemento.delta_saldo_total,
        delta_dias_atraso=elemento.delta_dias_atraso,
    )


def _pago(visto: PagoVisto) -> PagoObservadoRespuesta:
    p = visto.pago
    return PagoObservadoRespuesta(
        pago_observado_id=p.pago_observado_id,
        fecha_recepcion=p.fecha_recepcion,
        recuperacion_por_gestion=p.recuperacion_por_gestion,
        concepto_calculo=p.concepto_calculo,
        anio=p.anio,
        semana=p.semana,
        territorio=p.territorio,
        zona=p.zona,
        segmento=p.segmento,
        gerencia=p.gerencia,
        tipo_cartera=p.tipo_cartera,
        producto=p.producto,
        campania=p.campania,
        gestor=p.gestor,
        dias_de_atraso=p.dias_de_atraso,
        semanas_de_atraso=p.semanas_de_atraso,
        plan_de_pago=p.plan_de_pago,
        fecha_de_gestion=p.fecha_de_gestion,
        cargos_automaticos=p.cargos_automaticos,
        captacion=p.captacion,
        cobranza_total=p.cobranza_total,
        porcentaje_comision=p.porcentaje_comision,
        monto_comision=p.monto_comision,
        cliente_unico=p.cliente_unico,
        pagos_run_id=visto.pagos_run_id,
        dataset_id=visto.dataset_id,
        source_row=p.source_row,
        source_sheet=p.source_sheet,
    )
