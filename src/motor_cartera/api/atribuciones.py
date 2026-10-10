"""La atribucion operativa por HTTP: pedirla para una ventana y leer lo que concluyo.

La API no atribuye: el POST deja la ejecucion EN_PROCESO y su trabajo ATRIBUCION en la cola durable,
en una transaccion, y un worker atribuye todos los pagos de la ventana por conjuntos. Lo que se lee
es asociacion operacional, no causalidad: que gestiones con contacto de la misma cuenta ocurrieron
antes de un pago, nunca que una gestion lo haya producido.

- `/atribuciones` y `/atribuciones/{atribucion_run_id}`: las ejecuciones, con sus conteos;
- `/atribuciones/{atribucion_run_id}/resultados`: lo que concluyo de cada pago, con sus candidatas;
- `/movimientos/{movimiento_id}/atribuciones`: cada atribucion de un pago, la vigente y las que la
  precedieron;
- `/cuentas/{cuenta_id}/atribuciones`: los pagos de una cuenta con su atribucion vigente.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response

from motor_cartera.api.dependencias import Sesion, SesionDeLectura
from motor_cartera.api.errores import ErrorDeApi, errores
from motor_cartera.api.esquemas import Paginacion
from motor_cartera.api.esquemas_lifecycle import (
    AtribucionEntrada,
    CandidataRespuesta,
    ConteosAtribucionRespuesta,
    EjecucionAtribucionRespuesta,
    MontosAtribucionRespuesta,
    PaginaAtribuciones,
    PaginaAtribucionesDeCuenta,
    PaginaAtribucionesDeMovimiento,
    PaginaResultadosAtribucion,
    PagoAtribuidoRespuesta,
    ParametrosAtribuciones,
    ParametrosResultadosAtribucion,
    ResultadoAtribucionRespuesta,
)
from motor_cartera.api.gestiones import cuenta_de
from motor_cartera.api.motor_pagos import movimiento_respuesta
from motor_cartera.api.operacional import SIN_CLAVE
from motor_cartera.atribucion import consultas
from motor_cartera.atribucion.consultas import (
    AtribucionNoEncontrada,
    EjecucionVista,
    ResultadoVisto,
)
from motor_cartera.atribucion.ejecuciones import (
    AtribucionEnProceso,
    abrir,
    interpretacion_vigente,
)
from motor_cartera.config import Config
from motor_cartera.motor_pagos import consultas as pagos
from motor_cartera.motor_pagos.consultas import MovimientoNoEncontrado
from motor_cartera.motor_pagos.ejecuciones import Ventana
from motor_cartera.motor_pagos.reglas import VERSION_MOTOR_PAGOS

router = APIRouter(tags=["atribucion"])

NO_EXISTE = (
    404,
    "ATRIBUCION_NO_ENCONTRADA",
    "No existe una atribucion con ese atribucion_run_id.",
)


@router.post(
    "/atribuciones",
    status_code=201,
    response_model=EjecucionAtribucionRespuesta,
    summary="Pide atribuir los pagos de un mes de recepcion de la cartera",
    responses={
        201: {
            "description": "La atribucion quedo EN_PROCESO, con su trabajo en la cola durable: un "
            "worker la ejecuta. `Location` apunta a ella."
        },
        **errores(
            *SIN_CLAVE,
            (
                409,
                "ATRIBUCION_EN_PROCESO",
                "La ventana ya se esta atribuyendo: tiene una EN_PROCESO.",
            ),
            (
                409,
                "SIN_INTERPRETACION_DE_PAGOS",
                "La ventana no tiene una interpretacion vigente del motor de pagos: no hay pagos "
                "que atribuir todavia.",
            ),
            (422, "ENTRADA_INVALIDA", "El periodo no es AAAA-MM, o la ventana no es de 1 a 366."),
        ),
    },
)
def pedir_atribucion(
    entrada: AtribucionEntrada, request: Request, response: Response, s: Sesion
) -> EjecucionAtribucionRespuesta:
    """Atribuye, en un solo trabajo, cada PAGO de la interpretacion vigente del motor de pagos del
    mes, con `atribucion/v1`: `ASOCIACION_UNICA` si exactamente una gestion de su cuenta, vigente y
    con contacto, ocurrio antes del pago dentro de `ventana_dias`; `AMBIGUA` si fueron varias, sin
    elegir ninguna; `SIN_GESTION_CANDIDATA` si ninguna.

    **201 quiere decir que la atribucion se creo, no que termino.** Si la ventana ya se atribuyo
    con exactamente las mismas entradas, la nueva termina `FALLIDA` con `YA_ATRIBUIDA`; si llego
    una gestion despues, la nueva publica otra atribucion y la anterior se conserva."""
    config: Config = request.app.state.config
    anio, mes = entrada.periodo.split("-")
    ventana = Ventana.del_periodo(
        config.despacho_id, config.cartera_id, date(int(anio), int(mes), 1)
    )
    if interpretacion_vigente(s, ventana) is None:
        raise ErrorDeApi(
            409,
            "SIN_INTERPRETACION_DE_PAGOS",
            f"La ventana {entrada.periodo} no tiene una interpretacion vigente de "
            f"{VERSION_MOTOR_PAGOS}: no hay pagos que atribuir todavia.",
        )
    try:
        ejecucion, _ = abrir(
            s,
            ventana,
            ventana_dias=entrada.ventana_dias or config.atribucion_ventana_dias,
            zona=config.zona_horaria_fuente,
            max_intentos=config.worker_max_intentos,
            reusar=False,
        )
    except AtribucionEnProceso as exc:
        raise ErrorDeApi(409, "ATRIBUCION_EN_PROCESO", str(exc)) from exc
    s.commit()
    atribucion_run_id = ejecucion.atribucion_run_id
    response.headers["Location"] = f"/atribuciones/{atribucion_run_id}"
    return ejecucion_respuesta(consultas.obtener(s, atribucion_run_id))


@router.get(
    "/atribuciones",
    response_model=PaginaAtribuciones,
    summary="Las atribuciones de la cartera, por mes de recepcion",
    responses=errores(
        *SIN_CLAVE, (422, "ENTRADA_INVALIDA", "Un filtro no es valido, o la paginacion no lo es.")
    ),
)
def listar_atribuciones(
    request: Request, parametros: Annotated[ParametrosAtribuciones, Query()], s: SesionDeLectura
) -> PaginaAtribuciones:
    """La ventana mas reciente primero; dentro de una ventana, la ejecucion mas reciente primero.
    `vigente` marca la que vale hoy en cada ventana: su EXITOSA mas reciente."""
    config: Config = request.app.state.config
    periodo = None
    if parametros.periodo is not None:
        anio, mes = parametros.periodo.split("-")
        periodo = date(int(anio), int(mes), 1)
    total, pagina = consultas.listar(
        s,
        despacho_id=config.despacho_id,
        cartera_id=config.cartera_id,
        version=parametros.version,
        periodo=periodo,
        estado=parametros.estado,
        desplazamiento=parametros.desplazamiento,
        limite=parametros.por_pagina,
    )
    return PaginaAtribuciones(
        despacho_id=config.despacho_id,
        cartera_id=config.cartera_id,
        version_atribucion=parametros.version,
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[ejecucion_respuesta(vista) for vista in pagina],
    )


@router.get(
    "/atribuciones/{atribucion_run_id}",
    response_model=EjecucionAtribucionRespuesta,
    summary="Una atribucion: su ventana, su estado y cuantos pagos de cada clase",
    responses=errores(
        *SIN_CLAVE, NO_EXISTE, (422, "ENTRADA_INVALIDA", "El atribucion_run_id no es un UUID.")
    ),
)
def obtener_atribucion(atribucion_run_id: UUID, s: SesionDeLectura) -> EjecucionAtribucionRespuesta:
    """Mientras el estado sea `EN_PROCESO`, un worker no la ha terminado. `conteos` dice cuantos
    pagos quedaron en cada clase y cuantas gestiones leyo; `montos`, lo que suman, con los pagos que
    anulo un reverso aparte. `interpretacion_de_pagos_vigente` en false quiere decir que el motor de
    pagos publico despues otra interpretacion de la ventana, y que se debe volver a atribuir."""
    return ejecucion_respuesta(_vista(s, atribucion_run_id))


@router.get(
    "/atribuciones/{atribucion_run_id}/resultados",
    response_model=PaginaResultadosAtribucion,
    summary="Lo que la atribucion concluyo de cada pago, con sus candidatas",
    responses=errores(
        *SIN_CLAVE,
        NO_EXISTE,
        (422, "ENTRADA_INVALIDA", "Un filtro no es valido, o la paginacion no lo es."),
    ),
)
def resultados(
    atribucion_run_id: UUID,
    parametros: Annotated[ParametrosResultadosAtribucion, Query()],
    s: SesionDeLectura,
) -> PaginaResultadosAtribucion:
    """En orden de recepcion. Con `clasificacion=AMBIGUA` se ve por que cada pago quedo ambiguo:
    todas sus candidatas, sin elegir ninguna; con `cliente_unico`, los de un cliente. Vacia
    mientras la atribucion sigue EN_PROCESO, o si fallo."""
    vista = _vista(s, atribucion_run_id)
    total, pagina = consultas.resultados_de(
        s,
        vista.ejecucion,
        clasificacion=parametros.clasificacion,
        cliente_unico=parametros.cliente_unico,
        desplazamiento=parametros.desplazamiento,
        limite=parametros.por_pagina,
    )
    return PaginaResultadosAtribucion(
        atribucion_run_id=atribucion_run_id,
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[resultado_respuesta(visto) for visto in pagina],
    )


@router.get(
    "/movimientos/{movimiento_id}/atribuciones",
    response_model=PaginaAtribucionesDeMovimiento,
    summary="Cada atribucion de un movimiento: la vigente y las que la precedieron",
    responses=errores(
        *SIN_CLAVE,
        (
            404,
            "MOVIMIENTO_NO_ENCONTRADO",
            "Ninguna ejecucion del motor de pagos publico un movimiento con ese movimiento_id.",
        ),
        (422, "ENTRADA_INVALIDA", "El movimiento_id no es un UUID, o la paginacion no es valida."),
    ),
)
def atribuciones_del_movimiento(
    movimiento_id: UUID, parametros: Annotated[Paginacion, Query()], s: SesionDeLectura
) -> PaginaAtribucionesDeMovimiento:
    """De la mas reciente a la mas antigua. Una gestion registrada tarde no cambia una atribucion
    ya publicada: aparece en la siguiente, y las dos se conservan. Un reverso o un posible reverso
    no se atribuye: su pagina esta vacia."""
    try:
        movimiento = pagos.obtener_movimiento(s, movimiento_id, VERSION_MOTOR_PAGOS)
    except MovimientoNoEncontrado as exc:
        raise ErrorDeApi(
            404,
            "MOVIMIENTO_NO_ENCONTRADO",
            f"Ninguna ejecucion de {VERSION_MOTOR_PAGOS} publico un movimiento con movimiento_id "
            f"{movimiento_id}.",
        ) from exc
    total, pagina = consultas.de_movimiento(
        s, movimiento_id, desplazamiento=parametros.desplazamiento, limite=parametros.por_pagina
    )
    return PaginaAtribucionesDeMovimiento(
        movimiento=movimiento_respuesta(movimiento),
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[resultado_respuesta(visto) for visto in pagina],
    )


@router.get(
    "/cuentas/{cuenta_id}/atribuciones",
    response_model=PaginaAtribucionesDeCuenta,
    summary="Los pagos de la cuenta, cada uno con su atribucion vigente",
    responses=errores(
        *SIN_CLAVE,
        (404, "CUENTA_NO_ENCONTRADA", "No existe una cuenta canonica con ese cuenta_id."),
        (422, "ENTRADA_INVALIDA", "El cuenta_id no es un UUID, o la paginacion no es valida."),
    ),
)
def atribuciones_de_la_cuenta(
    cuenta_id: UUID, parametros: Annotated[Paginacion, Query()], s: SesionDeLectura
) -> PaginaAtribucionesDeCuenta:
    """Los PAGO de la interpretacion vigente de los pagos de la cuenta, del mas reciente al mas
    antiguo, cada uno con lo que dice de el la atribucion vigente de su ventana (null si su
    ventana no se ha atribuido). Que un pago tenga una asociacion unica no lo hace compatible con
    una promesa, ni al reves: son dos conceptos."""
    cuenta = cuenta_de(s, cuenta_id)
    total, pagina = consultas.de_cuenta(
        s,
        cuenta,
        version_motor=VERSION_MOTOR_PAGOS,
        desplazamiento=parametros.desplazamiento,
        limite=parametros.por_pagina,
    )
    return PaginaAtribucionesDeCuenta(
        cuenta_id=cuenta.cuenta_id,
        cliente_unico=cuenta.cliente_unico,
        version_motor=VERSION_MOTOR_PAGOS,
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[
            PagoAtribuidoRespuesta(
                movimiento=movimiento_respuesta(a.movimiento),
                atribucion=None if a.atribucion is None else resultado_respuesta(a.atribucion),
            )
            for a in pagina
        ],
    )


def _vista(s, atribucion_run_id: UUID) -> EjecucionVista:
    try:
        return consultas.obtener(s, atribucion_run_id)
    except AtribucionNoEncontrada as exc:
        raise ErrorDeApi(
            404,
            NO_EXISTE[1],
            f"No existe una atribucion con atribucion_run_id {atribucion_run_id}.",
        ) from exc


def ejecucion_respuesta(vista: EjecucionVista) -> EjecucionAtribucionRespuesta:
    e = vista.ejecucion
    return EjecucionAtribucionRespuesta(
        atribucion_run_id=e.atribucion_run_id,
        version_atribucion=e.version_atribucion,
        estado=e.estado,
        resultado=e.resultado,
        despacho_id=e.despacho_id,
        cartera_id=e.cartera_id,
        periodo=f"{e.periodo_desde:%Y-%m}",
        periodo_desde=e.periodo_desde,
        periodo_hasta=e.periodo_hasta,
        ventana_dias=e.ventana_dias,
        zona_horaria=e.zona_horaria,
        vigente=vista.vigente,
        motor_pagos_run_id=vista.motor_pagos_run_id,
        interpretacion_de_pagos_vigente=vista.interpretacion_vigente,
        firma_entrada=e.firma_entrada,
        conteos=ConteosAtribucionRespuesta(
            movimientos_evaluados=e.movimientos_evaluados,
            asociados=e.asociados,
            ambiguos=e.ambiguos,
            sin_candidato=e.sin_candidato,
            candidatos=e.candidatos,
            movimientos_anulados=e.movimientos_anulados,
            gestiones_leidas=e.gestiones_leidas,
            gestiones_anuladas=e.gestiones_anuladas,
        ),
        montos=MontosAtribucionRespuesta(
            asociado=e.monto_asociado,
            ambiguo=e.monto_ambiguo,
            sin_candidato=e.monto_sin_candidato,
            anulado=e.monto_anulado,
        ),
        trabajo_id=vista.trabajo_id,
        iniciada_en=e.iniciada_en,
        terminada_en=e.terminada_en,
        detalle=e.detalle,
    )


def resultado_respuesta(visto: ResultadoVisto) -> ResultadoAtribucionRespuesta:
    r = visto.resultado
    return ResultadoAtribucionRespuesta(
        atribucion_run_id=visto.ejecucion.atribucion_run_id,
        vigente=visto.vigente,
        ventana_dias=visto.ejecucion.ventana_dias,
        motor_pagos_run_id=visto.motor_pagos_run_id,
        interpretacion_de_pagos_vigente=visto.interpretacion_vigente,
        movimiento_id=r.movimiento_id,
        cuenta_id=visto.cuenta_id,
        fecha_recepcion=r.fecha_recepcion,
        monto=r.monto,
        anulado_por_reverso=r.anulado_por_reverso,
        clasificacion=r.clasificacion,
        gestion_id=visto.gestion_id,
        candidatas=[
            CandidataRespuesta(
                gestion_id=c.gestion_id,
                ocurrido_en=c.ocurrido_en,
                canal=c.canal,
                nivel_contacto=c.nivel_contacto,
                resultado=c.resultado,
                antelacion_segundos=c.antelacion_segundos,
            )
            for c in visto.candidatas
        ],
        motivos=r.motivos,
    )
