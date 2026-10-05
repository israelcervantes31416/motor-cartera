"""POST /corridas y lo que cuelga de una corrida."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, File, Form, Query, Request, Response, UploadFile
from sqlmodel import func, select

from motor_cartera.api.dependencias import Sesion, buscar_corrida
from motor_cartera.api.errores import ErrorDeApi, errores
from motor_cartera.api.esquemas import (
    EJEMPLO_CORRIDA_EN_PROCESO,
    ArtefactoRespuesta,
    CompaneraRespuesta,
    ConformadoRespuesta,
    CorridaRespuesta,
    FuenteCorridaRespuesta,
    Paginacion,
    PaginaRechazos,
    RechazoRespuesta,
    ordenar_valores,
)
from motor_cartera.api.subidas import ERRORES_DE_SUBIDA, guardar_subida, nombre_de
from motor_cartera.config import Config
from motor_cartera.contratos.cartera_v2 import CONTRATO_V2, VERSION_CONTRATO_V2
from motor_cartera.db.modelos import (
    ArtefactoFuente,
    DatasetConformado,
    EstadoCorrida,
    HojaCompanera,
    Rechazo,
)
from motor_cartera.ingesta.corridas import (
    ArchivoDuplicado,
    CorridaMalDeclarada,
    verificar_declaracion,
)
from motor_cartera.ingesta.lectores import REQUERIDAS
from motor_cartera.orquestacion.flujo import crear_flujo_ingesta

router = APIRouter(prefix="/corridas", tags=["corridas"])

SIN_CLAVE = (
    (401, "API_KEY_AUSENTE", "Falta la cabecera X-API-Key."),
    (401, "API_KEY_INVALIDA", "La API key no es valida."),
)
NO_EXISTE = (404, "CORRIDA_NO_ENCONTRADA", "No existe una corrida con ese run_id.")

ContratoCartera = Literal["cartera/v1", "cartera/v2"]
"""Los contratos que se pueden declarar al subir una cartera. Los controla el servidor."""


@router.post(
    "",
    status_code=201,
    response_model=CorridaRespuesta,
    summary="Inicia una corrida de ingesta sobre un archivo de cartera",
    responses={
        201: {
            "description": "La corrida quedo registrada EN_PROCESO: su archivo, ya guardado en el "
            "almacen de artefactos, su flujo y el trabajo de su ingesta en la cola durable. El "
            "worker la procesa y encadena las demas etapas: consulta `Location` y "
            "`/corridas/{run_id}/flujo`.",
            "content": {"application/json": {"example": EJEMPLO_CORRIDA_EN_PROCESO}},
        },
        **errores(
            *SIN_CLAVE,
            (409, "ARCHIVO_YA_PUBLICADO", "Otra corrida ya publico ese archivo; run_id dice cual."),
            (409, "ARCHIVO_EN_PROCESO", "Otra corrida lo esta procesando; run_id dice cual."),
            *ERRORES_DE_SUBIDA,
            (
                422,
                "FECHA_CORTE_REQUERIDA",
                "cartera/v2 sin fecha_corte: el archivo no la trae, y se declara con la corrida.",
            ),
            (
                422,
                "FECHA_CORTE_NO_APLICA",
                "cartera/v1 con fecha_corte: en cartera/v1 viene dentro del archivo.",
            ),
            (
                422,
                "ENTRADA_INVALIDA",
                "La peticion no trae el campo archivo, o el contrato o la fecha no son validos.",
            ),
        ),
    },
)
def crear_corrida(
    request: Request,
    response: Response,
    s: Sesion,
    archivo: Annotated[UploadFile, File(description="La cartera, en xlsx, csv o zip.")],
    contrato: Annotated[
        ContratoCartera,
        Form(
            description="Con que contrato se juzga: `cartera/v1` (8 columnas; por omision, como "
            "antes de v0.6.0) o `cartera/v2` (las 93 columnas de la hoja CARTERA). Se declara: el "
            "servidor no lo adivina por la forma del archivo."
        ),
    ] = "cartera/v1",
    fecha_corte: Annotated[
        date | None,
        Form(
            description="La fecha de corte, AAAA-MM-DD. Obligatoria con `cartera/v2`, que no la "
            "trae en sus columnas; no se admite con `cartera/v1`, que si."
        ),
    ] = None,
) -> CorridaRespuesta:
    """Registra la corrida y deja su ingesta en la cola durable. Responde de inmediato, con la
    corrida `EN_PROCESO` y su direccion en `Location`: consultala hasta que termine.

    La peticion no juzga el archivo: lo copia por bloques al almacen de artefactos, donde queda
    guardado por su SHA-256 y no se borra nunca, y despues registra el artefacto, la corrida, su
    flujo automatico y el trabajo de la ingesta en una sola transaccion. Un worker hace lo demas.
    Si la API se reinicia despues de responder, no se pierde nada. El flujo lleva la corrida hasta
    el ruteo sin que se pida cada etapa; se sigue en `/corridas/{run_id}/flujo`.

    **201.** La peticion crea la corrida antes de responder: ya tiene `run_id` y se puede
    consultar. Lo que sigue en curso es su procesamiento, y eso es su `estado`. Un 202
    tambien seria valido, para subrayar que el procesamiento es asincrono; se eligio 201
    porque lo que la peticion hace, crear la corrida, ya esta hecho.

    **Un archivo con registros invalidos no es un error de la peticion.** La peticion es
    valida y crea la corrida; el veredicto sobre el contenido llega en el estado de la
    corrida y en `/rechazos`. Los 4xx son para lo que se decide sin juzgar ningun registro: un
    formato que no se recibe, un archivo vacio o demasiado grande, o bytes que no son los de su
    extension (`415 FORMATO_NO_CORRESPONDE`): un xlsx tiene que ser un libro de Excel, y un csv,
    texto.

    **409 si el mismo archivo ya se publico o se esta procesando**: volver a ingerirlo
    duplicaria la cartera o repetiria el trabajo, y un doble clic o un reintento por
    timeout son justo como pasa. La respuesta dice que corrida lo tiene (`run_id`). Un
    archivo que no llego a publicarse (RECHAZADA o FALLIDA) si se puede reintentar.

    **El contrato se declara.** `cartera/v1` es el de siempre y sigue siendo el de omision, para
    que ningun cliente anterior cambie. `cartera/v2` juzga las 93 columnas de la cartera oficial,
    con su `fecha_corte` como metadata del lote, y proyecta cada cuenta a lo que leen los motores.
    Una declaracion invalida responde 422 antes de guardar nada.
    """
    config: Config = request.app.state.config
    try:
        verificar_declaracion(contrato, fecha_corte)
    except CorridaMalDeclarada as exc:
        codigo = "FECHA_CORTE_REQUERIDA" if fecha_corte is None else "FECHA_CORTE_NO_APLICA"
        raise ErrorDeApi(422, codigo, str(exc)) from exc
    nombre = nombre_de(archivo)
    guardado = guardar_subida(archivo, nombre, config)
    try:
        corrida, _ = crear_flujo_ingesta(
            s,
            origen=nombre,
            guardado=guardado,
            contrato=contrato,
            fecha_corte=fecha_corte,
            tolerancia=config.tolerancia_rechazo,
            config=config,
        )
    except ArchivoDuplicado as exc:
        publicado = exc.previa.estado == EstadoCorrida.EXITOSA
        codigo = "ARCHIVO_YA_PUBLICADO" if publicado else "ARCHIVO_EN_PROCESO"
        raise ErrorDeApi(409, codigo, str(exc), run_id=exc.previa.run_id) from exc

    response.headers["Location"] = f"/corridas/{corrida.run_id}"
    return CorridaRespuesta.model_validate(corrida)


@router.get(
    "/{run_id}",
    response_model=CorridaRespuesta,
    summary="Estado de una corrida: conteos, tiempos y resultado",
    responses=errores(*SIN_CLAVE, NO_EXISTE, (422, "ENTRADA_INVALIDA", "El run_id no es un UUID.")),
)
def obtener_corrida(run_id: UUID, s: Sesion) -> CorridaRespuesta:
    """Mientras el estado sea `EN_PROCESO`, la corrida sigue trabajando: vuelve a consultar.

    Se cumple siempre que `filas_leidas = filas_validas + filas_rechazadas`.
    """
    return CorridaRespuesta.model_validate(buscar_corrida(s, run_id))


@router.get(
    "/{run_id}/rechazos",
    response_model=PaginaRechazos,
    summary="Registros rechazados, cada uno con su fila y el motivo",
    responses=errores(
        *SIN_CLAVE,
        NO_EXISTE,
        (409, "CORRIDA_EN_PROCESO", "La corrida no ha terminado; sus rechazos aun no se conocen."),
        (422, "ENTRADA_INVALIDA", "El run_id no es un UUID, o la paginacion esta fuera de rango."),
    ),
)
def listar_rechazos(
    run_id: UUID, paginacion: Annotated[Paginacion, Query()], s: Sesion
) -> PaginaRechazos:
    """Cada registro que no cumplio el contrato: la fila del archivo, lo que traia y que
    regla no cumplio. Es la evidencia de que el diseno es *fail-closed*: nada entra sin
    pasar el contrato, y lo que no entra dice por que.

    Una corrida RECHAZADA tambien tiene rechazos: son justo la razon de que no publicara.
    Una FALLIDA no tiene: no se llego a juzgar ningun registro; su `detalle` dice por que.

    **409 mientras esta en proceso**, y no una lista vacia: vacia diria "no hubo
    rechazos", y eso todavia no se sabe.
    """
    corrida = buscar_corrida(s, run_id)
    if corrida.estado == EstadoCorrida.EN_PROCESO:
        raise ErrorDeApi(
            409,
            "CORRIDA_EN_PROCESO",
            "La corrida sigue en proceso; sus rechazos se conocen cuando termina.",
            run_id=run_id,
        )

    orden = CONTRATO_V2.nombres if corrida.version_contrato == VERSION_CONTRATO_V2 else REQUERIDAS
    de_la_corrida = Rechazo.corrida_id == corrida.id
    total = s.exec(select(func.count()).select_from(Rechazo).where(de_la_corrida)).one()
    rechazos = s.exec(
        select(Rechazo)
        .where(de_la_corrida)
        .order_by(Rechazo.fila)
        .offset(paginacion.desplazamiento)
        .limit(paginacion.por_pagina)
    ).all()
    return PaginaRechazos(
        run_id=corrida.run_id,
        estado=corrida.estado,
        total=total,
        pagina=paginacion.pagina,
        por_pagina=paginacion.por_pagina,
        elementos=[
            RechazoRespuesta(
                fila=r.fila, valores=ordenar_valores(r.valores, orden), motivos=r.motivos
            )
            for r in rechazos
        ],
    )


@router.get(
    "/{run_id}/fuente",
    response_model=FuenteCorridaRespuesta,
    summary="La evidencia de una corrida: su archivo original, su dataset conformado y su CARRIER",
    responses=errores(*SIN_CLAVE, NO_EXISTE, (422, "ENTRADA_INVALIDA", "El run_id no es un UUID.")),
)
def obtener_fuente(run_id: UUID, s: Sesion) -> FuenteCorridaRespuesta:
    """El linaje de una corrida, de punta a punta: el archivo tal como llego (su SHA-256, su
    tamano, su nombre y su formato), el dataset conformado que publico (un Parquet con los
    registros validos, con su contrato y su firma) y las hojas companeras que traia el archivo,
    como CARRIER, con lo que se audito de ellas.

    El archivo original no se borra al terminar la ingesta: es la evidencia con que se puede
    reproducir exactamente la entrada. Lo que nunca se publica es donde vive en el almacen.
    """
    corrida = buscar_corrida(s, run_id)
    artefacto = None
    if corrida.artefacto_fuente_id is not None:
        artefacto = ArtefactoRespuesta.model_validate(
            s.get_one(ArtefactoFuente, corrida.artefacto_fuente_id)
        )
    dataset = s.exec(
        select(DatasetConformado).where(DatasetConformado.corrida_id == corrida.id)
    ).first()
    conformado = None
    if dataset is not None:
        conformado = ConformadoRespuesta(
            dataset_id=dataset.dataset_id,
            contrato=dataset.contrato,
            filas=dataset.filas,
            columnas=dataset.columnas,
            firma_contenido=dataset.firma_contenido,
            artefacto=ArtefactoRespuesta.model_validate(
                s.get_one(ArtefactoFuente, dataset.artefacto_conformado_id)
            ),
            creado_en=dataset.creado_en,
        )
    hojas = s.exec(
        select(HojaCompanera)
        .where(HojaCompanera.corrida_id == corrida.id)
        .order_by(HojaCompanera.nombre)
    ).all()
    return FuenteCorridaRespuesta(
        run_id=corrida.run_id,
        version_contrato=corrida.version_contrato,
        version_proyeccion=corrida.version_proyeccion,
        fecha_corte=corrida.fecha_corte,
        despacho_id=corrida.despacho_id,
        cartera_id=corrida.cartera_id,
        artefacto=artefacto,
        conformado=conformado,
        hojas_companeras=[CompaneraRespuesta.model_validate(h) for h in hojas],
    )
