"""campo/v2 por HTTP: pedir el plan de campo de una fecha, leer el plan, sus asignaciones, sus
cargas y sus unidades, y las de una cuenta a traves de los planes.

La API no planea: el POST fija exactamente sus entradas (el corte, la decision/v2, la
territorial/v2, la configuracion de campo, la fecha de operacion y la politica) y deja el plan
EN_PROCESO con su trabajo CAMPO_V2 en la cola durable, y un worker lo ejecuta. Corre en paralelo a
territorial/v1 y ruteo/v1, que siguen siendo las etapas del flujo de cada corrida.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from sqlmodel import Session

from motor_cartera.api.dependencias import Sesion, SesionDeLectura
from motor_cartera.api.errores import ErrorDeApi, errores
from motor_cartera.api.esquemas import Paginacion
from motor_cartera.api.esquemas_campo import (
    AsignacionCampoDeCuentaRespuesta,
    AsignacionCampoRespuesta,
    BaseDelCampo,
    CapacidadCampoV2,
    CargaGestorRespuesta,
    ConfiguracionDelPlan,
    ConteosCampoV2,
    GestorDelCampo,
    PaginaAsignacionesCampo,
    PaginaCampoDeCuenta,
    PaginaCargasCampo,
    PaginaPlanesCampoV2,
    PaginaUnidadesCampo,
    ParametrosAsignacionesCampo,
    ParametrosCargas,
    ParametrosPlanes,
    ParametrosUnidades,
    ParticionDeLaPolitica,
    PlanCampoEntrada,
    PlanCampoV2Respuesta,
    UnidadCampoRespuesta,
)
from motor_cartera.api.esquemas_geografia import CorteRespuesta
from motor_cartera.api.geografia import corte_de
from motor_cartera.api.gestiones import cuenta_de
from motor_cartera.api.operacional import SIN_CLAVE
from motor_cartera.campo_v2 import consultas
from motor_cartera.campo_v2 import ejecuciones as motor
from motor_cartera.campo_v2.consultas import AsignacionVista, Filtros, PlanNoEncontrado, PlanVista
from motor_cartera.campo_v2.politica import CUANTO, POLITICAS
from motor_cartera.config import Config

router = APIRouter(tags=["campo-v2"])

NO_EXISTE = (404, "CAMPO_V2_NO_ENCONTRADO", "No existe un plan de campo/v2 con ese campo_run_id.")
CUENTA_NO_EXISTE = (404, "CUENTA_NO_ENCONTRADA", "No existe una cuenta canonica con ese cuenta_id.")
CORTE_NO_EXISTE = (404, "CORTE_NO_ENCONTRADO", "No existe un corte canonico con ese corte_id.")


@router.post(
    "/campo-v2/planes",
    status_code=201,
    response_model=PlanCampoV2Respuesta,
    summary="Pide el plan de campo/v2 de una fecha, con todas sus entradas declaradas",
    responses={
        201: {
            "description": "El plan quedo EN_PROCESO, con su trabajo en la cola durable: un worker "
            "lo ejecuta. `Location` apunta a el."
        },
        **errores(
            *SIN_CLAVE,
            CORTE_NO_EXISTE,
            (404, "DECISION_NO_ENCONTRADA", "No existe esa decision/v2."),
            (404, "TERRITORIAL_NO_ENCONTRADO", "No existe esa territorial/v2."),
            (404, "CONFIGURACION_NO_ENCONTRADA", "No existe esa configuracion de campo."),
            (409, "CAMPO_V2_EN_PROCESO", "Esas mismas entradas ya se estan planeando."),
            (409, "CAMPO_V2_YA_GENERADO", "Esas mismas entradas (la misma firma) ya se planearon."),
            (422, "DECISION_NO_EXITOSA", "La decision/v2 no esta EXITOSA."),
            (422, "TERRITORIAL_NO_EXITOSA", "La territorial/v2 no esta EXITOSA."),
            (422, "CORTE_INCONSISTENTE", "La decision o territorial/v2 son de otro corte."),
            (
                422,
                "BASES_INCONSISTENTES",
                "La configuracion de campo es de otra version de bases que territorial/v2.",
            ),
            (422, "FECHA_ANTERIOR_AL_CORTE", "La fecha de operacion es anterior al corte."),
            (422, "POLITICA_NO_SOPORTADA", "Este servicio no aplica esa version de la politica."),
            (422, "ENTRADA_INVALIDA", "Falta una entrada, o no tiene su forma."),
        ),
    },
)
def pedir_plan(
    entrada: PlanCampoEntrada, request: Request, response: Response, s: Sesion
) -> PlanCampoV2Respuesta:
    """Las candidatas de campo de la decision (su canal CAMPO) quedan con un gestor disponible de su
    base territorial, en el orden de su prioridad y bajo el cupo declarado de cada gestor, en su
    zona, subzona, tramo y microtramo; o en revision territorial, sin gestor disponible o sin
    capacidad, con sus motivos. No mueve cuentas entre bases ni traza rutas.

    **201 quiere decir que el plan se creo, no que termino.** Las mismas entradas son el mismo
    plan: si ya se publico, `409 CAMPO_V2_YA_GENERADO`."""
    config: Config = request.app.state.config
    try:
        ejecucion = motor.abrir(
            s,
            entrada.corte_id,
            entrada.decision_run_id,
            entrada.territorial_run_id,
            entrada.configuracion_campo_version_id,
            entrada.fecha_operacion,
            version_politica=entrada.version_politica,
            max_intentos=config.worker_max_intentos,
        )
    except motor.EntradaNoPlanificable as exc:
        raise ErrorDeApi(exc.estado_http, exc.codigo, str(exc)) from exc
    except motor.CampoV2EnProceso as exc:
        raise ErrorDeApi(409, "CAMPO_V2_EN_PROCESO", str(exc)) from exc
    except motor.CampoV2YaGenerado as exc:
        raise ErrorDeApi(409, "CAMPO_V2_YA_GENERADO", str(exc)) from exc
    s.commit()
    response.headers["Location"] = f"/campo-v2/planes/{ejecucion.campo_run_id}"
    return plan_respuesta(consultas.obtener(s, ejecucion.campo_run_id))


@router.get(
    "/campo-v2/planes",
    response_model=PaginaPlanesCampoV2,
    summary="Los planes de campo/v2, el mas reciente primero; con corte_id o fecha_operacion, los "
    "de ese corte o esa fecha",
    responses=errores(
        *SIN_CLAVE,
        CORTE_NO_EXISTE,
        (422, "ENTRADA_INVALIDA", "Un filtro no es valido, o la paginacion no lo es."),
    ),
)
def planes(
    parametros: Annotated[ParametrosPlanes, Query()], s: SesionDeLectura
) -> PaginaPlanesCampoV2:
    """Cada plan, en cualquier estado, con sus entradas declaradas, su firma y sus conteos."""
    corte = None if parametros.corte_id is None else corte_de(s, parametros.corte_id)
    total, filas = consultas.listar(
        s,
        corte,
        parametros.fecha_operacion,
        desplazamiento=parametros.desplazamiento,
        limite=parametros.por_pagina,
    )
    return PaginaPlanesCampoV2(
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[plan_respuesta(v) for v in filas],
    )


@router.get(
    "/campo-v2/planes/{campo_run_id}",
    response_model=PlanCampoV2Respuesta,
    summary="Un plan de campo/v2: sus entradas, su firma, sus conteos, su capacidad y su "
    "distribucion por base",
    responses=errores(
        *SIN_CLAVE, NO_EXISTE, (422, "ENTRADA_INVALIDA", "El campo_run_id no es un UUID.")
    ),
)
def obtener(campo_run_id: UUID, s: SesionDeLectura) -> PlanCampoV2Respuesta:
    """Mientras el estado sea `EN_PROCESO`, un worker no lo ha terminado. Trae la politica con su
    hash y su cuadricula, y la distribucion: por base, por estado, por motivo, las unidades y el
    balance entre gestores, que es un diagnostico."""
    return plan_respuesta(_vista(s, campo_run_id))


@router.get(
    "/campo-v2/planes/{campo_run_id}/asignaciones",
    response_model=PaginaAsignacionesCampo,
    summary="La asignacion de cada candidata de campo, en el orden de la cuenta, sin coordenadas",
    responses=errores(
        *SIN_CLAVE,
        NO_EXISTE,
        (422, "ENTRADA_INVALIDA", "Un filtro no es valido, o la paginacion no lo es."),
    ),
)
def asignaciones(
    campo_run_id: UUID,
    parametros: Annotated[ParametrosAsignacionesCampo, Query()],
    s: SesionDeLectura,
) -> PaginaAsignacionesCampo:
    """Filtrables por `estado`, `base` (su codigo), `gestor` (su codigo), `zona`, `subzona`,
    `tramo`, `microtramo` (sus identificadores) y `prioridad`, en la base de datos y por sus
    indices."""
    vista = _vista(s, campo_run_id)
    total, filas = consultas.asignaciones(
        s,
        vista,
        Filtros(
            estado=None if parametros.estado is None else parametros.estado.value,
            base=parametros.base,
            gestor=parametros.gestor,
            zona_id=parametros.zona,
            subzona_id=parametros.subzona,
            tramo_id=parametros.tramo,
            microtramo_id=parametros.microtramo,
            prioridad=parametros.prioridad,
        ),
        desplazamiento=parametros.desplazamiento,
        limite=parametros.por_pagina,
    )
    return PaginaAsignacionesCampo(
        campo_run_id=campo_run_id,
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[_asignacion(v) for v in filas],
    )


@router.get(
    "/campo-v2/planes/{campo_run_id}/cargas",
    response_model=PaginaCargasCampo,
    summary="La carga de cada gestor: su capacidad, sus asignadas, su utilizacion y sus ventanas",
    responses=errores(
        *SIN_CLAVE,
        NO_EXISTE,
        (422, "ENTRADA_INVALIDA", "Un filtro no es valido, o la paginacion no lo es."),
    ),
)
def cargas(
    campo_run_id: UUID, parametros: Annotated[ParametrosCargas, Query()], s: SesionDeLectura
) -> PaginaCargasCampo:
    """Cada gestor de la configuracion, en el orden de su codigo: su jornada en la fecha, su
    capacidad efectiva, cuantas cuentas recibio, de cuantos microtramos y tramos, cuantas por
    spillover, y las ventanas de su dia. Con `base`, los de esa base. Sin coordenadas."""
    vista = _vista(s, campo_run_id)
    total, filas = consultas.cargas(
        s,
        vista,
        base=parametros.base,
        desplazamiento=parametros.desplazamiento,
        limite=parametros.por_pagina,
    )
    return PaginaCargasCampo(
        campo_run_id=campo_run_id,
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[
            CargaGestorRespuesta(
                gestor=GestorDelCampo(gestor_id=c.gestor_id, codigo=c.codigo_gestor),
                base=BaseDelCampo(base_id=c.base_id, codigo=c.codigo_base),
                estado_jornada=c.carga.estado_jornada,
                capacidad=c.carga.capacidad,
                asignadas=c.carga.asignadas,
                utilizacion=c.carga.utilizacion,
                microtramos=c.carga.microtramos,
                tramos=c.carga.tramos,
                rebalanceadas=c.carga.rebalanceadas,
                ventanas=c.carga.ventanas,
            )
            for c in filas
        ],
    )


@router.get(
    "/campo-v2/planes/{campo_run_id}/unidades",
    response_model=PaginaUnidadesCampo,
    summary="Las zonas, subzonas, tramos y microtramos que ocupo el plan, sin geometrias",
    responses=errores(
        *SIN_CLAVE,
        NO_EXISTE,
        (422, "ENTRADA_INVALIDA", "Un filtro no es valido, o la paginacion no lo es."),
    ),
)
def unidades(
    campo_run_id: UUID, parametros: Annotated[ParametrosUnidades, Query()], s: SesionDeLectura
) -> PaginaUnidadesCampo:
    """Solo las unidades con candidatas, en el orden de su identificador: con `nivel`, las de ese
    nivel; con `padre`, las hijas de una unidad; con `base`, las de esa base. Ninguna trae su
    poligono, su celda ni su centro: su identificador, su padre, su base y su municipio."""
    vista = _vista(s, campo_run_id)
    total, filas = consultas.unidades(
        s,
        vista,
        nivel=None if parametros.nivel is None else parametros.nivel.value,
        padre_id=parametros.padre,
        base=parametros.base,
        desplazamiento=parametros.desplazamiento,
        limite=parametros.por_pagina,
    )
    return PaginaUnidadesCampo(
        campo_run_id=campo_run_id,
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[
            UnidadCampoRespuesta(
                unidad_id=u.unidad.unidad_id,
                nivel=u.unidad.nivel,
                padre_id=u.unidad.padre_id,
                base=BaseDelCampo(base_id=u.base_id, codigo=u.codigo_base),
                cve_ent=u.unidad.cve_ent,
                cvegeo=u.unidad.cvegeo,
                candidatas=u.unidad.candidatas,
                asignadas=u.unidad.asignadas,
                propietario_preferido=u.codigo_propietario,
            )
            for u in filas
        ],
    )


@router.get(
    "/cuentas/{cuenta_id}/campo-v2",
    response_model=PaginaCampoDeCuenta,
    summary="Los planes de campo/v2 en que la cuenta fue candidata, de la fecha mas reciente a la "
    "mas antigua",
    responses=errores(
        *SIN_CLAVE,
        CUENTA_NO_EXISTE,
        (422, "ENTRADA_INVALIDA", "El cuenta_id no es un UUID, o la paginacion no es valida."),
    ),
)
def campo_de_cuenta(
    cuenta_id: UUID, parametros: Annotated[Paginacion, Query()], s: SesionDeLectura
) -> PaginaCampoDeCuenta:
    """Solo planes EXITOSA, cada uno con su fecha, su estado, su base, su gestor, su particion, su
    prioridad y sus motivos: un plan historico no se sobrescribe aunque cambie la configuracion."""
    cuenta = cuenta_de(s, cuenta_id)
    total, filas = consultas.historial(
        s, cuenta, desplazamiento=parametros.desplazamiento, limite=parametros.por_pagina
    )
    return PaginaCampoDeCuenta(
        cuenta_id=cuenta.cuenta_id,
        cliente_unico=cuenta.cliente_unico,
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[
            AsignacionCampoDeCuentaRespuesta(
                **_asignacion(h.vista).model_dump(),
                campo_run_id=h.ejecucion.campo_run_id,
                fecha_operacion=h.ejecucion.fecha_operacion,
                corte=CorteRespuesta(corte_id=h.corte_id, fecha_corte=h.fecha_corte),
                configuracion_campo_version_id=h.configuracion_campo_version_id,
                version_politica=h.ejecucion.version_politica,
            )
            for h in filas
        ],
    )


def _vista(s: Session, campo_run_id: UUID) -> PlanVista:
    try:
        return consultas.obtener(s, campo_run_id)
    except PlanNoEncontrado as exc:
        raise ErrorDeApi(
            404,
            "CAMPO_V2_NO_ENCONTRADO",
            f"No existe un plan de campo/v2 con campo_run_id {campo_run_id}.",
        ) from exc


def _asignacion(v: AsignacionVista) -> AsignacionCampoRespuesta:
    a = v.asignacion
    return AsignacionCampoRespuesta(
        cuenta_id=v.cuenta_id,
        cliente_unico=v.cliente_unico,
        estado=a.estado,
        prioridad=a.prioridad,
        orden_prioridad=a.orden_prioridad,
        base=None if v.base_id is None else BaseDelCampo(base_id=v.base_id, codigo=v.codigo_base),
        gestor=None
        if v.gestor_id is None
        else GestorDelCampo(gestor_id=v.gestor_id, codigo=v.codigo_gestor),
        municipio=v.cvegeo,
        zona_id=a.zona_id,
        subzona_id=a.subzona_id,
        tramo_id=a.tramo_id,
        microtramo_id=a.microtramo_id,
        es_rebalanceada=a.es_rebalanceada,
        motivos=a.motivos,
    )


def plan_respuesta(vista: PlanVista) -> PlanCampoV2Respuesta:
    e = vista.ejecucion
    politica = POLITICAS.get(e.version_politica)
    duracion = (
        None
        if e.terminada_en is None
        else round((e.terminada_en - e.iniciada_en).total_seconds(), 3)
    )
    utilizacion = (
        None
        if e.capacidad_total == 0
        else (Decimal(e.capacidad_utilizada) / Decimal(e.capacidad_total)).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
    )
    return PlanCampoV2Respuesta(
        campo_run_id=e.campo_run_id,
        estado=e.estado,
        resultado=e.resultado,
        corte=CorteRespuesta(corte_id=vista.corte_id, fecha_corte=vista.fecha_corte),
        fecha_operacion=e.fecha_operacion,
        decision_run_id=vista.decision_run_id,
        territorial_run_id=vista.territorial_run_id,
        bases_version_id=vista.bases_version_id,
        configuracion=ConfiguracionDelPlan(
            configuracion_campo_version_id=vista.configuracion.configuracion_campo_version_id,
            nombre=vista.configuracion.nombre,
            hash_configuracion=vista.configuracion.hash_configuracion,
            zona_horaria=vista.configuracion.zona_horaria,
        ),
        version_motor=e.version_motor,
        version_politica=e.version_politica,
        hash_politica=e.hash_politica,
        particion=None
        if politica is None
        else ParticionDeLaPolitica(
            srid=politica.srid_metrico,
            tramo_m=politica.tamano_tramo_m,
            microtramo_m=politica.tamano_microtramo_m,
            redondeo_m=str(CUANTO),
        ),
        firma_inputs=e.firma_inputs,
        conteos=ConteosCampoV2(
            cuentas_evaluadas_decision=e.cuentas_evaluadas_decision,
            candidatas_campo=e.candidatas_campo,
            asignadas=e.asignadas,
            revision_territorial=e.revision_territorial,
            sin_gestor=e.sin_gestor,
            sin_capacidad=e.sin_capacidad,
            rebalanceadas=e.rebalanceadas,
        ),
        capacidad=CapacidadCampoV2(
            capacidad_total=e.capacidad_total,
            capacidad_utilizada=e.capacidad_utilizada,
            utilizacion=utilizacion,
        ),
        distribucion=e.distribucion,
        trabajo_id=vista.trabajo_id,
        iniciada_en=e.iniciada_en,
        terminada_en=e.terminada_en,
        duracion_segundos=duracion,
        detalle=e.detalle,
    )
