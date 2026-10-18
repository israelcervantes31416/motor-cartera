"""ruteo/v2 por HTTP: las versiones de la red vial, pedir el ruteo de un plan de campo/v2, leer la
ejecucion, sus rutas y sus paradas, y las paradas de una cuenta a traves de las ejecuciones.

La API no rutea: el POST fija exactamente sus entradas (el plan de campo/v2 y la version de la
red vial) y deja la ejecucion EN_PROCESO con su trabajo RUTEO_V2 en la cola durable; la ejecuta
un worker que tiene un proveedor de ruteo que responde. Corre en paralelo a ruteo/v1, que sigue
siendo la etapa RUTEO del flujo de cada corrida.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query, Request, Response
from sqlmodel import Session, select

from motor_cartera.api.dependencias import Sesion, SesionDeLectura
from motor_cartera.api.errores import ErrorDeApi, errores
from motor_cartera.api.esquemas import Paginacion
from motor_cartera.api.esquemas_ruteo_v2 import (
    ATRIBUCION_OSM,
    ArtefactoRedVialRespuesta,
    BaseDeLaRuta,
    ConteosDeLaRuta,
    ConteosRuteoV2,
    EjecucionRuteoEntrada,
    EjecucionRuteoV2Respuesta,
    GestorDeLaRuta,
    PaginaEjecucionesRuteoV2,
    PaginaParadasV2,
    PaginaRedesViales,
    PaginaRutasV2,
    PaginaRuteoDeCuenta,
    ParadaDeCuentaRespuesta,
    ParadaRespuesta,
    ParametrosEjecucionesRuteo,
    ParametrosParadas,
    ParametrosRutas,
    PoliticaDeRuteo,
    RedDeLaEjecucion,
    RedVialDetalleRespuesta,
    RedVialRespuesta,
    RegresoDeLaRuta,
    RutaRespuesta,
    TotalesRuteoV2,
)
from motor_cartera.api.gestiones import cuenta_de
from motor_cartera.api.operacional import SIN_CLAVE
from motor_cartera.config import Config
from motor_cartera.db.modelos import EjecucionCampoV2, VersionRedVial
from motor_cartera.ruteo_v2 import consultas
from motor_cartera.ruteo_v2 import ejecuciones as motor
from motor_cartera.ruteo_v2.consultas import (
    EjecucionVista,
    FiltrosParadas,
    ParadaVista,
    RutaVista,
)
from motor_cartera.ruteo_v2.politica import ALGORITMO, POLITICAS

router = APIRouter(tags=["ruteo-v2"])

RED_NO_EXISTE = (404, "RED_VIAL_NO_ENCONTRADA", "No existe esa version de la red vial.")
NO_EXISTE = (404, "RUTEO_V2_NO_ENCONTRADO", "No existe una ejecucion de ruteo/v2 con ese id.")
RUTA_NO_EXISTE = (404, "RUTA_NO_ENCONTRADA", "No existe una ruta con ese ruta_id.")
CUENTA_NO_EXISTE = (404, "CUENTA_NO_ENCONTRADA", "No existe una cuenta canonica con ese cuenta_id.")
INVALIDA = (422, "ENTRADA_INVALIDA", "Un filtro no es valido, o la paginacion no lo es.")


# --- las redes ------------------------------------------------------------------------------------


@router.get(
    "/ruteo-v2/redes",
    response_model=PaginaRedesViales,
    summary="Las versiones de la red vial, la mas reciente primero",
    responses=errores(*SIN_CLAVE, INVALIDA),
)
def redes(parametros: Annotated[Paginacion, Query()], s: SesionDeLectura) -> PaginaRedesViales:
    """Cada version, en cualquier estado: de donde salen sus datos, su licencia, su fecha, su
    region, el SHA-256 de su fuente, el motor de ruteo con su version, el perfil con su hash y su
    huella. Sus archivos no se sirven."""
    total, filas = consultas.redes(
        s, desplazamiento=parametros.desplazamiento, limite=parametros.por_pagina
    )
    return PaginaRedesViales(
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[_red(r) for r in filas],
    )


@router.get(
    "/ruteo-v2/redes/{red_vial_version_id}",
    response_model=RedVialDetalleRespuesta,
    summary="Una version de la red vial, con los comandos de su preparacion y sus archivos",
    responses=errores(*SIN_CLAVE, RED_NO_EXISTE, (422, "ENTRADA_INVALIDA", "No es un UUID.")),
)
def obtener_red(red_vial_version_id: UUID, s: SesionDeLectura) -> RedVialDetalleRespuesta:
    """Con los comandos exactos con que se preparo y el SHA-256 de cada archivo preparado: la
    evidencia de su manifiesto. Ningun archivo se descarga de aqui."""
    red = _red_o_404(s, red_vial_version_id)
    manifiesto = red.manifiesto or {}
    return RedVialDetalleRespuesta(
        **_red(red).model_dump(),
        comandos=manifiesto.get("comandos", []),
        artefactos=[ArtefactoRedVialRespuesta(**a) for a in manifiesto.get("artefactos", [])],
    )


def _red(r: VersionRedVial) -> RedVialRespuesta:
    return RedVialRespuesta(
        red_vial_version_id=r.red_vial_version_id,
        estado=r.estado,
        huella=r.huella,
        proveedor_datos=r.proveedor_datos,
        proveedor_extracto=r.proveedor_extracto,
        licencia=r.licencia,
        fecha_snapshot=r.fecha_snapshot,
        region=r.region,
        archivo_fuente=r.archivo_fuente,
        url_fuente=r.url_fuente,
        sha256_fuente=r.sha256_fuente,
        motor_ruteo=r.motor_ruteo,
        version_motor_ruteo=r.version_motor_ruteo,
        distribucion_motor=r.distribucion_motor,
        algoritmo_motor=r.algoritmo_motor,
        perfil=r.perfil,
        hash_perfil=r.hash_perfil,
        hash_manifest=r.hash_manifest,
        cobertura=(r.metadatos or {}).get("cobertura", {}),
        creada_en=r.creada_en,
        publicada_en=r.publicada_en,
        detalle=r.detalle,
        atribucion=ATRIBUCION_OSM if r.proveedor_datos == "OpenStreetMap" else None,
    )


def _red_o_404(s: Session, red_vial_version_id: UUID) -> VersionRedVial:
    try:
        return consultas.red(s, red_vial_version_id)
    except consultas.RedNoEncontrada as exc:
        raise ErrorDeApi(
            404,
            "RED_VIAL_NO_ENCONTRADA",
            f"No existe una version de la red vial con red_vial_version_id {red_vial_version_id}.",
        ) from exc


# --- las ejecuciones ------------------------------------------------------------------------------


@router.post(
    "/ruteo-v2/ejecuciones",
    status_code=201,
    response_model=EjecucionRuteoV2Respuesta,
    summary="Pide el ruteo vial de un plan de campo/v2 sobre una version declarada de la red vial",
    responses={
        201: {
            "description": "La ejecucion quedo EN_PROCESO, con su trabajo en la cola durable: un "
            "worker con un proveedor de ruteo que responde la ejecuta. `Location` apunta a ella."
        },
        **errores(
            *SIN_CLAVE,
            (404, "CAMPO_V2_NO_ENCONTRADO", "No existe ese plan de campo/v2."),
            RED_NO_EXISTE,
            (409, "RUTEO_V2_EN_PROCESO", "Esas mismas entradas ya se estan ruteando."),
            (409, "RUTEO_V2_YA_GENERADO", "Esas mismas entradas (la misma firma) ya se rutearon."),
            (422, "CAMPO_V2_NO_EXITOSO", "El plan de campo/v2 no esta EXITOSA."),
            (422, "RED_VIAL_NO_PUBLICADA", "La version de la red vial no esta PUBLICADA."),
            (422, "POLITICA_NO_SOPORTADA", "Este servicio no aplica esa version de la politica."),
            (422, "ENTRADA_INVALIDA", "Falta una entrada, o no tiene su forma."),
        ),
    },
)
def pedir_ejecucion(
    entrada: EjecucionRuteoEntrada, request: Request, response: Response, s: Sesion
) -> EjecucionRuteoV2Respuesta:
    """Las cuentas ASIGNADA del plan, con su gestor, su base y su prioridad, se recorren por la red
    vial en una ruta cerrada por gestor, dentro de su jornada, con ruteo-public-v2: en que orden, a
    que hora (ETA estatica, sin trafico), que distancia y que duracion, y cuales no caben o no son
    enrutables, y por que. No se usa la ultima red ni el ultimo plan: se declaran.

    **201 quiere decir que la ejecucion se creo, no que termino.** Las mismas entradas son la misma
    ejecucion: si ya se publico, `409 RUTEO_V2_YA_GENERADO`."""
    config: Config = request.app.state.config
    try:
        ejecucion = motor.abrir(
            s,
            entrada.campo_run_id,
            entrada.red_vial_version_id,
            version_politica=entrada.version_politica,
            max_intentos=config.worker_max_intentos,
        )
    except motor.EntradaNoRuteable as exc:
        raise ErrorDeApi(exc.estado_http, exc.codigo, str(exc)) from exc
    except motor.RuteoV2EnProceso as exc:
        raise ErrorDeApi(409, "RUTEO_V2_EN_PROCESO", str(exc)) from exc
    except motor.RuteoV2YaGenerado as exc:
        raise ErrorDeApi(409, "RUTEO_V2_YA_GENERADO", str(exc)) from exc
    s.commit()
    response.headers["Location"] = f"/ruteo-v2/ejecuciones/{ejecucion.ruteo_run_id}"
    return ejecucion_respuesta(consultas.obtener(s, ejecucion.ruteo_run_id))


@router.get(
    "/ruteo-v2/ejecuciones",
    response_model=PaginaEjecucionesRuteoV2,
    summary="Las ejecuciones de ruteo/v2, la mas reciente primero",
    responses=errores(
        *SIN_CLAVE,
        (404, "CAMPO_V2_NO_ENCONTRADO", "No existe ese plan de campo/v2."),
        RED_NO_EXISTE,
        INVALIDA,
    ),
)
def ejecuciones(
    parametros: Annotated[ParametrosEjecucionesRuteo, Query()], s: SesionDeLectura
) -> PaginaEjecucionesRuteoV2:
    """Con `campo_run_id`, `red_vial_version_id` o `estado`, las de ese plan, esa red o ese
    estado."""
    campo = None
    if parametros.campo_run_id is not None:
        campo = s.exec(
            select(EjecucionCampoV2).where(EjecucionCampoV2.campo_run_id == parametros.campo_run_id)
        ).one_or_none()
        if campo is None:
            raise ErrorDeApi(
                404,
                "CAMPO_V2_NO_ENCONTRADO",
                f"No existe un plan de campo/v2 con campo_run_id {parametros.campo_run_id}.",
            )
    red = (
        None
        if parametros.red_vial_version_id is None
        else _red_o_404(s, parametros.red_vial_version_id)
    )
    total, filas = consultas.listar(
        s,
        campo=campo,
        red_vial=red,
        estado=None if parametros.estado is None else parametros.estado.value,
        desplazamiento=parametros.desplazamiento,
        limite=parametros.por_pagina,
    )
    return PaginaEjecucionesRuteoV2(
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[ejecucion_respuesta(v) for v in filas],
    )


@router.get(
    "/ruteo-v2/ejecuciones/{ruteo_run_id}",
    response_model=EjecucionRuteoV2Respuesta,
    summary="Una ejecucion de ruteo/v2: sus entradas, su firma, sus conteos y sus totales",
    responses=errores(*SIN_CLAVE, NO_EXISTE, (422, "ENTRADA_INVALIDA", "No es un UUID.")),
)
def obtener(ruteo_run_id: UUID, s: SesionDeLectura) -> EjecucionRuteoV2Respuesta:
    """Mientras el estado sea `EN_PROCESO`, un worker no la ha terminado. Trae la politica con su
    hash y sus parametros, y la distribucion: por estado, por motivo, por prioridad y por base, y lo
    que costo la red."""
    return ejecucion_respuesta(_vista(s, ruteo_run_id))


@router.get(
    "/ruteo-v2/ejecuciones/{ruteo_run_id}/rutas",
    response_model=PaginaRutasV2,
    summary="La ruta de cada gestor, en el orden de su codigo",
    responses=errores(*SIN_CLAVE, NO_EXISTE, INVALIDA),
)
def rutas(
    ruteo_run_id: UUID, parametros: Annotated[ParametrosRutas, Query()], s: SesionDeLectura
) -> PaginaRutasV2:
    """Filtrables por `gestor`, `base` (sus codigos) y `estado`, en la base de datos."""
    vista = _vista(s, ruteo_run_id)
    total, filas = consultas.rutas(
        s,
        vista,
        gestor=parametros.gestor,
        base=parametros.base,
        estado=None if parametros.estado is None else parametros.estado.value,
        desplazamiento=parametros.desplazamiento,
        limite=parametros.por_pagina,
    )
    return PaginaRutasV2(
        ruteo_run_id=ruteo_run_id,
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[_ruta(r, vista) for r in filas],
    )


@router.get(
    "/ruteo-v2/ejecuciones/{ruteo_run_id}/paradas",
    response_model=PaginaParadasV2,
    summary="Las paradas de la ejecucion, en el orden de la cuenta, sin coordenadas",
    responses=errores(*SIN_CLAVE, NO_EXISTE, INVALIDA),
)
def paradas(
    ruteo_run_id: UUID, parametros: Annotated[ParametrosParadas, Query()], s: SesionDeLectura
) -> PaginaParadasV2:
    """Filtrables por `estado`, `gestor`, `base` y `prioridad`, en la base de datos y por sus
    indices."""
    vista = _vista(s, ruteo_run_id)
    total, filas = consultas.paradas(
        s,
        vista,
        FiltrosParadas(
            estado=None if parametros.estado is None else parametros.estado.value,
            gestor=parametros.gestor,
            base=parametros.base,
            prioridad=parametros.prioridad,
        ),
        desplazamiento=parametros.desplazamiento,
        limite=parametros.por_pagina,
    )
    zona = ZoneInfo(vista.zona_horaria)
    return PaginaParadasV2(
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[parada_respuesta(p, zona) for p in filas],
    )


# --- una ruta -------------------------------------------------------------------------------------


@router.get(
    "/ruteo-v2/rutas/{ruta_id}",
    response_model=RutaRespuesta,
    summary="Una ruta: su gestor, su base, su estado, su salida, su regreso y sus totales",
    responses=errores(*SIN_CLAVE, RUTA_NO_EXISTE, (422, "ENTRADA_INVALIDA", "No es un UUID.")),
)
def obtener_ruta(ruta_id: UUID, s: SesionDeLectura) -> RutaRespuesta:
    """Sin geometria ni polilinea: los instantes, las distancias y las duraciones."""
    ruta_vista, vista = _ruta_o_404(s, ruta_id)
    return _ruta(ruta_vista, vista)


@router.get(
    "/ruteo-v2/rutas/{ruta_id}/paradas",
    response_model=PaginaParadasV2,
    summary="Las paradas de una ruta: las programadas en su orden de visita, y despues las demas",
    responses=errores(*SIN_CLAVE, RUTA_NO_EXISTE, INVALIDA),
)
def paradas_de_ruta(
    ruta_id: UUID, parametros: Annotated[Paginacion, Query()], s: SesionDeLectura
) -> PaginaParadasV2:
    """Las PROGRAMADA por `orden_visita`, con su llegada, su servicio y el tramo desde la
    anterior; y despues las que no se programaron, por cuenta_id, con su motivo."""
    ruta_vista, vista = _ruta_o_404(s, ruta_id)
    total, filas = consultas.paradas_de_ruta(
        s, ruta_vista, desplazamiento=parametros.desplazamiento, limite=parametros.por_pagina
    )
    zona = ZoneInfo(vista.zona_horaria)
    return PaginaParadasV2(
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[parada_respuesta(p, zona) for p in filas],
    )


# --- las de una cuenta ----------------------------------------------------------------------------


@router.get(
    "/cuentas/{cuenta_id}/ruteo-v2",
    response_model=PaginaRuteoDeCuenta,
    summary="Las paradas de una cuenta en las ejecuciones de ruteo/v2, de la fecha mas reciente a "
    "la mas antigua",
    responses=errores(
        *SIN_CLAVE,
        CUENTA_NO_EXISTE,
        (422, "ENTRADA_INVALIDA", "El cuenta_id no es un UUID, o la paginacion no es valida."),
    ),
)
def ruteo_de_cuenta(
    cuenta_id: UUID, parametros: Annotated[Paginacion, Query()], s: SesionDeLectura
) -> PaginaRuteoDeCuenta:
    """Solo ejecuciones EXITOSA, cada una con su fecha, su ruta, su gestor, su estado, su orden y su
    ETA: una ruta historica no cambia."""
    cuenta = cuenta_de(s, cuenta_id)
    total, filas = consultas.historial(
        s, cuenta, desplazamiento=parametros.desplazamiento, limite=parametros.por_pagina
    )
    return PaginaRuteoDeCuenta(
        cuenta_id=cuenta.cuenta_id,
        cliente_unico=cuenta.cliente_unico,
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[
            ParadaDeCuentaRespuesta(
                **parada_respuesta(h.vista, ZoneInfo(h.zona_horaria)).model_dump(),
                ruteo_run_id=h.ejecucion.ruteo_run_id,
                fecha_operacion=h.ejecucion.fecha_operacion,
                zona_horaria=h.zona_horaria,
                base=h.codigo_base,
                estado_ruta=h.estado_ruta,
                version_politica=h.ejecucion.version_politica,
            )
            for h in filas
        ],
    )


# --- las respuestas -------------------------------------------------------------------------------


def _en(momento: datetime | None, zona: ZoneInfo) -> datetime | None:
    """El instante en la zona de la configuracion de campo, con su desfase: el mismo instante."""
    return None if momento is None else momento.astimezone(zona)


def _vista(s: Session, ruteo_run_id: UUID) -> EjecucionVista:
    try:
        return consultas.obtener(s, ruteo_run_id)
    except consultas.EjecucionNoEncontrada as exc:
        raise ErrorDeApi(
            404,
            "RUTEO_V2_NO_ENCONTRADO",
            f"No existe una ejecucion de ruteo/v2 con ruteo_run_id {ruteo_run_id}.",
        ) from exc


def _ruta_o_404(s: Session, ruta_id: UUID) -> tuple[RutaVista, EjecucionVista]:
    try:
        return consultas.ruta(s, ruta_id)
    except consultas.RutaNoEncontrada as exc:
        raise ErrorDeApi(
            404, "RUTA_NO_ENCONTRADA", f"No existe una ruta con ruta_id {ruta_id}."
        ) from exc


def _ruta(v: RutaVista, vista: EjecucionVista) -> RutaRespuesta:
    r = v.ruta
    zona = ZoneInfo(vista.zona_horaria)
    return RutaRespuesta(
        ruta_id=r.ruta_id,
        ruteo_run_id=vista.ejecucion.ruteo_run_id,
        fecha_operacion=r.fecha_operacion,
        zona_horaria=vista.zona_horaria,
        gestor=GestorDeLaRuta(gestor_id=v.gestor_id, codigo=v.codigo_gestor),
        base=BaseDeLaRuta(base_id=v.base_id, codigo=v.codigo_base),
        estado_ruta=r.estado_ruta,
        motivo_base=r.motivo_base,
        conteos=ConteosDeLaRuta(
            paradas_campo=r.paradas_campo,
            programadas=r.programadas,
            sin_tiempo=r.sin_tiempo,
            no_enrutables=r.no_enrutables,
            no_enrutables_base=r.no_enrutables_base,
        ),
        base_snap_distancia_m=r.base_snap_distancia_m,
        jornada_inicio=_en(r.jornada_inicio_at, zona),
        jornada_fin=_en(r.jornada_fin_at, zona),
        salida_base=_en(r.salida_base_at, zona),
        regreso=None
        if r.regreso_base_at is None
        else RegresoDeLaRuta(
            salida=_en(r.salida_regreso_at, zona),
            llegada=_en(r.regreso_base_at, zona),
            distancia_m=r.distancia_regreso_m,
            duracion_s=r.duracion_regreso_s,
            espera_s=r.espera_regreso_s,
        ),
        distancia_total_m=r.distancia_total_m,
        duracion_traslado_s=r.duracion_traslado_s,
        duracion_servicio_s=r.duracion_servicio_s,
        duracion_espera_s=r.duracion_espera_s,
        duracion_total_s=r.duracion_total_s,
        mejoras_2opt=r.mejoras_2opt,
        hash_matriz=r.hash_matriz,
        hash_secuencia=r.hash_secuencia,
    )


def parada_respuesta(v: ParadaVista, zona: ZoneInfo) -> ParadaRespuesta:
    p = v.parada
    return ParadaRespuesta(
        cuenta_id=v.cuenta_id,
        cliente_unico=v.cliente_unico,
        ruta_id=v.ruta_id,
        gestor=v.codigo_gestor,
        prioridad=p.prioridad,
        estado=p.estado,
        motivo=p.motivo,
        orden_visita=p.orden_visita,
        snap_distancia_m=p.snap_distancia_m,
        salida_anterior=_en(p.salida_anterior_at, zona),
        llegada_estimada=_en(p.llegada_estimada, zona),
        inicio_servicio=_en(p.inicio_servicio, zona),
        fin_servicio=_en(p.fin_servicio, zona),
        distancia_desde_anterior_m=p.distancia_desde_anterior_m,
        duracion_desde_anterior_s=p.duracion_desde_anterior_s,
        espera_s=p.espera_s,
    )


def ejecucion_respuesta(vista: EjecucionVista) -> EjecucionRuteoV2Respuesta:
    e = vista.ejecucion
    politica = POLITICAS.get(e.version_politica)
    duracion = (
        None
        if e.terminada_en is None
        else round((e.terminada_en - e.iniciada_en).total_seconds(), 3)
    )
    return EjecucionRuteoV2Respuesta(
        ruteo_run_id=e.ruteo_run_id,
        estado=e.estado,
        resultado=e.resultado,
        campo_run_id=vista.campo_run_id,
        fecha_operacion=e.fecha_operacion,
        zona_horaria=vista.zona_horaria,
        red=RedDeLaEjecucion(
            red_vial_version_id=vista.red.red_vial_version_id,
            huella=vista.red.huella,
            region=vista.red.region,
            motor_ruteo=vista.red.motor_ruteo,
            version_motor_ruteo=vista.red.version_motor_ruteo,
        ),
        version_motor=e.version_motor,
        version_politica=e.version_politica,
        hash_politica=e.hash_politica,
        politica=None
        if politica is None
        else PoliticaDeRuteo(
            algoritmo=ALGORITMO,
            max_snap_distancia_m=politica.max_snap_distancia_m,
            duracion_servicio_s=politica.duracion_servicio_s,
            inicio_fin_base=True,
            bloque_matriz=politica.bloque_matriz,
            max_mejoras_2opt=politica.max_mejoras_2opt,
            perfil=politica.perfil,
        ),
        firma_inputs=e.firma_inputs,
        conteos=ConteosRuteoV2(
            gestores_objetivo=e.gestores_objetivo,
            rutas_completas=e.rutas_completas,
            rutas_parciales=e.rutas_parciales,
            rutas_sin_paradas=e.rutas_sin_paradas,
            bases_no_enrutables=e.bases_no_enrutables,
            rutas_sin_jornada=e.rutas_sin_jornada,
            paradas_objetivo=e.paradas_objetivo,
            programadas=e.programadas,
            sin_tiempo=e.sin_tiempo,
            no_enrutables=e.no_enrutables,
            no_enrutables_base=e.no_enrutables_base,
        ),
        totales=TotalesRuteoV2(
            distancia_total_m=e.distancia_total_m,
            duracion_vial_total_s=e.duracion_vial_total_s,
            duracion_servicio_total_s=e.duracion_servicio_total_s,
            duracion_espera_total_s=e.duracion_espera_total_s,
        ),
        distribucion=e.distribucion,
        trabajo_id=vista.trabajo_id,
        iniciada_en=e.iniciada_en,
        terminada_en=e.terminada_en,
        duracion_segundos=duracion,
        detalle=e.detalle,
    )
