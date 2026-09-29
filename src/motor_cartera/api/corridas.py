"""POST /corridas y lo que cuelga de una corrida."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, File, Query, Request, Response, UploadFile
from sqlmodel import func, select

from motor_cartera.api.dependencias import Sesion, buscar_corrida
from motor_cartera.api.errores import ErrorDeApi, errores
from motor_cartera.api.esquemas import (
    EJEMPLO_CORRIDA_EN_PROCESO,
    CorridaRespuesta,
    Paginacion,
    PaginaRechazos,
    RechazoRespuesta,
)
from motor_cartera.config import Config
from motor_cartera.db.modelos import EstadoCorrida, Rechazo
from motor_cartera.ingesta.corridas import ArchivoDuplicado, abrir_corrida, procesar_corrida
from motor_cartera.ingesta.lectores import FORMATOS

router = APIRouter(prefix="/corridas", tags=["corridas"])

SIN_CLAVE = (
    (401, "API_KEY_AUSENTE", "Falta la cabecera X-API-Key."),
    (401, "API_KEY_INVALIDA", "La API key no es valida."),
)
NO_EXISTE = (404, "CORRIDA_NO_ENCONTRADA", "No existe una corrida con ese run_id.")


@router.post(
    "",
    status_code=201,
    response_model=CorridaRespuesta,
    summary="Inicia una corrida de ingesta sobre un archivo de cartera",
    responses={
        201: {
            "description": "La corrida quedo registrada y se procesa en segundo plano.",
            "content": {"application/json": {"example": EJEMPLO_CORRIDA_EN_PROCESO}},
        },
        **errores(
            *SIN_CLAVE,
            (409, "ARCHIVO_YA_PUBLICADO", "Otra corrida ya publico ese archivo; run_id dice cual."),
            (409, "ARCHIVO_EN_PROCESO", "Otra corrida lo esta procesando; run_id dice cual."),
            (413, "ARCHIVO_DEMASIADO_GRANDE", "El archivo pasa del tope (MC_TAMANO_MAXIMO_MB)."),
            (415, "FORMATO_NO_SOPORTADO", "El archivo no es xlsx, csv ni zip."),
            (422, "ARCHIVO_VACIO", "El archivo llego vacio."),
            (422, "ENTRADA_INVALIDA", "La peticion no trae el campo archivo."),
        ),
    },
)
def crear_corrida(
    request: Request,
    response: Response,
    fondo: BackgroundTasks,
    s: Sesion,
    archivo: Annotated[UploadFile, File(description="La cartera, en xlsx, csv o zip.")],
) -> CorridaRespuesta:
    """Registra la corrida y la procesa en segundo plano. Responde de inmediato, con la
    corrida `EN_PROCESO` y su direccion en `Location`: consultala hasta que termine.

    **201 y no 202.** La corrida ya existe cuando llega la respuesta: tiene `run_id` y se
    puede consultar. Lo que sigue en curso es su procesamiento, y eso es su `estado`.

    **Un archivo con registros invalidos no es un error de la peticion.** La peticion es
    valida y crea la corrida; el veredicto sobre el contenido llega en el estado de la
    corrida y en `/rechazos`. Los 4xx son solo para lo que se decide sin leer el archivo.

    **409 si el mismo archivo ya se publico o se esta procesando**: volver a ingerirlo
    duplicaria la cartera o repetiria el trabajo, y un doble clic o un reintento por
    timeout son justo como pasa. La respuesta dice que corrida lo tiene (`run_id`). Un
    archivo que no llego a publicarse (RECHAZADA o FALLIDA) si se puede reintentar.
    """
    config: Config = request.app.state.config
    nombre = PurePosixPath((archivo.filename or "").replace("\\", "/")).name
    if PurePosixPath(nombre).suffix.lower() not in FORMATOS:
        raise ErrorDeApi(
            415,
            "FORMATO_NO_SOPORTADO",
            f"Formato no soportado: {nombre!r}. Se aceptan {', '.join(FORMATOS)}.",
        )

    limite = config.tamano_maximo_mb * 1024 * 1024
    contenido = archivo.file.read(limite + 1)
    if len(contenido) > limite:
        raise ErrorDeApi(
            413,
            "ARCHIVO_DEMASIADO_GRANDE",
            f"El archivo pasa del tope de {config.tamano_maximo_mb} MiB.",
        )
    if not contenido:
        raise ErrorDeApi(422, "ARCHIVO_VACIO", f"{nombre!r} llego vacio.")

    try:
        corrida = abrir_corrida(
            s, origen=nombre, contenido=contenido, tolerancia=config.tolerancia_rechazo
        )
    except ArchivoDuplicado as exc:
        publicado = exc.previa.estado == EstadoCorrida.EXITOSA
        codigo = "ARCHIVO_YA_PUBLICADO" if publicado else "ARCHIVO_EN_PROCESO"
        raise ErrorDeApi(409, codigo, str(exc), run_id=exc.previa.run_id) from exc

    fondo.add_task(procesar_corrida, corrida.id, contenido)
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
        elementos=[RechazoRespuesta.model_validate(r) for r in rechazos],
    )
