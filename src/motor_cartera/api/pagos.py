"""POST /pagos y lo que cuelga de una ingesta de pagos.

Una ingesta de pagos no es una corrida: acepta movimientos economicos, no publica cuentas. Tiene su
propio recurso, su propio identificador (pagos_run_id) y su trabajo en la cola durable, que ejecuta
el worker como cualquier otro. La API la registra y responde 201; no juzga ningun movimiento.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Query, Request, Response, UploadFile
from sqlmodel import Session, func, select

from motor_cartera.api.dependencias import Sesion
from motor_cartera.api.errores import ErrorDeApi, errores
from motor_cartera.api.esquemas import (
    EJEMPLO_PAGOS_EN_PROCESO,
    ArtefactoRespuesta,
    ConformadoRespuesta,
    FuentePagosRespuesta,
    IngestaPagosRespuesta,
    Paginacion,
    PaginaRechazosPagos,
    RechazoRespuesta,
    ordenar_valores,
)
from motor_cartera.api.subidas import ERRORES_DE_SUBIDA, guardar_subida, nombre_de
from motor_cartera.config import Config
from motor_cartera.contratos.pagos import CONTRATO_PAGOS
from motor_cartera.db.modelos import (
    ArtefactoFuente,
    DatasetConformado,
    EstadoIngestaPagos,
    IngestaPagos,
    RechazoPago,
    TrabajoOrquestacion,
)
from motor_cartera.ingesta.pagos import PagosDuplicados
from motor_cartera.orquestacion.flujo import encolar_ingesta_pagos

router = APIRouter(prefix="/pagos", tags=["pagos"])

SIN_CLAVE = (
    (401, "API_KEY_AUSENTE", "Falta la cabecera X-API-Key."),
    (401, "API_KEY_INVALIDA", "La API key no es valida."),
)
NO_EXISTE = (404, "PAGOS_NO_ENCONTRADOS", "No existe una ingesta de pagos con ese pagos_run_id.")


@router.post(
    "",
    status_code=201,
    response_model=IngestaPagosRespuesta,
    summary="Inicia la ingesta de un archivo de pagos (pagos/v1)",
    responses={
        201: {
            "description": "La ingesta quedo registrada EN_PROCESO: su archivo, ya guardado en el "
            "almacen de artefactos, y su trabajo en la cola durable. El worker la juzga: consulta "
            "`Location`.",
            "content": {"application/json": {"example": EJEMPLO_PAGOS_EN_PROCESO}},
        },
        **errores(
            *SIN_CLAVE,
            (
                409,
                "PAGOS_YA_ACEPTADOS",
                "Otra ingesta ya acepto ese archivo; pagos_run_id dice cual.",
            ),
            (409, "PAGOS_EN_PROCESO", "Otra ingesta lo esta procesando; pagos_run_id dice cual."),
            *ERRORES_DE_SUBIDA,
            (422, "ENTRADA_INVALIDA", "La peticion no trae el campo archivo."),
        ),
    },
)
def crear_ingesta_pagos(
    request: Request,
    response: Response,
    s: Sesion,
    archivo: Annotated[UploadFile, File(description="Los pagos, en xlsx, csv o zip.")],
) -> IngestaPagosRespuesta:
    """Registra la ingesta de pagos y deja su trabajo en la cola durable. Responde de inmediato,
    con la ingesta `EN_PROCESO` y su direccion en `Location`.

    El archivo se copia por bloques al almacen de artefactos y no se borra nunca. El worker lo juzga
    contra **pagos/v1**: sus 23 columnas exactas, y cada fila como un movimiento economico. **No se
    deduplica nada**: dos movimientos identicos son dos movimientos. La barrera es conservadora:
    con la tolerancia por omision (0), un solo movimiento invalido rechaza el archivo entero, y sus
    rechazos quedan en `/rechazos`.

    **409 si el mismo archivo ya se acepto o se esta procesando.** Un archivo que no se acepto
    (RECHAZADA o FALLIDA) se puede volver a subir.
    """
    config: Config = request.app.state.config
    nombre = nombre_de(archivo)
    guardado = guardar_subida(archivo, nombre, config)
    try:
        ingesta, trabajo = encolar_ingesta_pagos(
            s, origen=nombre, guardado=guardado, tolerancia=None, config=config
        )
    except PagosDuplicados as exc:
        aceptado = exc.previa.estado == EstadoIngestaPagos.EXITOSA
        codigo = "PAGOS_YA_ACEPTADOS" if aceptado else "PAGOS_EN_PROCESO"
        raise ErrorDeApi(409, codigo, str(exc), pagos_run_id=exc.previa.pagos_run_id) from exc
    response.headers["Location"] = f"/pagos/{ingesta.pagos_run_id}"
    return _respuesta(ingesta, trabajo.trabajo_id)


@router.get(
    "/{pagos_run_id}",
    response_model=IngestaPagosRespuesta,
    summary="Estado de una ingesta de pagos: conteos, tiempos y resultado",
    responses=errores(
        *SIN_CLAVE, NO_EXISTE, (422, "ENTRADA_INVALIDA", "El pagos_run_id no es un UUID.")
    ),
)
def obtener_ingesta_pagos(pagos_run_id: UUID, s: Sesion) -> IngestaPagosRespuesta:
    """Mientras el estado sea `EN_PROCESO`, el worker no la ha terminado: vuelve a consultar.
    `trabajo_id` es su trabajo en la cola: `GET /trabajos/{trabajo_id}` dice sus intentos y su
    lease. Se cumple siempre que `filas_leidas = filas_validas + filas_rechazadas`."""
    ingesta = _buscar(s, pagos_run_id)
    return _respuesta(ingesta, _trabajo_de(s, ingesta))


@router.get(
    "/{pagos_run_id}/rechazos",
    response_model=PaginaRechazosPagos,
    summary="Movimientos rechazados, cada uno con su fila, lo que traia y el motivo",
    responses=errores(
        *SIN_CLAVE,
        NO_EXISTE,
        (409, "PAGOS_EN_PROCESO", "La ingesta no ha terminado; sus rechazos aun no se conocen."),
        (
            422,
            "ENTRADA_INVALIDA",
            "El pagos_run_id no es un UUID, o la paginacion esta fuera de rango.",
        ),
    ),
)
def listar_rechazos_pagos(
    pagos_run_id: UUID, paginacion: Annotated[Paginacion, Query()], s: Sesion
) -> PaginaRechazosPagos:
    """Cada movimiento que no cumplio pagos/v1: la fila del archivo, sus 23 valores tal como
    llegaron, en el orden del contrato, y que regla no cumplio. Ninguno se oculta."""
    ingesta = _buscar(s, pagos_run_id)
    if ingesta.estado == EstadoIngestaPagos.EN_PROCESO:
        raise ErrorDeApi(
            409,
            "PAGOS_EN_PROCESO",
            "La ingesta sigue en proceso; sus rechazos se conocen cuando termina.",
            pagos_run_id=pagos_run_id,
        )
    de_la_ingesta = RechazoPago.ingesta_pagos_id == ingesta.id
    total = s.exec(select(func.count()).select_from(RechazoPago).where(de_la_ingesta)).one()
    rechazos = s.exec(
        select(RechazoPago)
        .where(de_la_ingesta)
        .order_by(RechazoPago.fila)
        .offset(paginacion.desplazamiento)
        .limit(paginacion.por_pagina)
    ).all()
    return PaginaRechazosPagos(
        pagos_run_id=ingesta.pagos_run_id,
        estado=ingesta.estado,
        total=total,
        pagina=paginacion.pagina,
        por_pagina=paginacion.por_pagina,
        elementos=[
            RechazoRespuesta(
                fila=r.fila,
                valores=ordenar_valores(r.valores, CONTRATO_PAGOS.nombres),
                motivos=r.motivos,
            )
            for r in rechazos
        ],
    )


@router.get(
    "/{pagos_run_id}/fuente",
    response_model=FuentePagosRespuesta,
    summary="La evidencia de una ingesta de pagos: su archivo original y su dataset conformado",
    responses=errores(
        *SIN_CLAVE, NO_EXISTE, (422, "ENTRADA_INVALIDA", "El pagos_run_id no es un UUID.")
    ),
)
def obtener_fuente_pagos(pagos_run_id: UUID, s: Sesion) -> FuentePagosRespuesta:
    """El archivo tal como llego (su SHA-256, tamano, nombre y formato) y, si la ingesta se
    acepto, el dataset conformado: un Parquet con todos los movimientos validos, sin deduplicar,
    cada uno con la fila del archivo de la que salio."""
    ingesta = _buscar(s, pagos_run_id)
    artefacto = ArtefactoRespuesta.model_validate(
        s.get_one(ArtefactoFuente, ingesta.artefacto_fuente_id)
    )
    dataset = s.exec(
        select(DatasetConformado).where(DatasetConformado.ingesta_pagos_id == ingesta.id)
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
    return FuentePagosRespuesta(
        pagos_run_id=ingesta.pagos_run_id,
        version_contrato=ingesta.version_contrato,
        despacho_id=ingesta.despacho_id,
        cartera_id=ingesta.cartera_id,
        artefacto=artefacto,
        conformado=conformado,
    )


def _buscar(s: Session, pagos_run_id: UUID) -> IngestaPagos:
    ingesta = s.exec(select(IngestaPagos).where(IngestaPagos.pagos_run_id == pagos_run_id)).first()
    if ingesta is None:
        raise ErrorDeApi(
            404,
            "PAGOS_NO_ENCONTRADOS",
            f"No existe una ingesta de pagos con pagos_run_id {pagos_run_id}.",
            pagos_run_id=pagos_run_id,
        )
    return ingesta


def _trabajo_de(s: Session, ingesta: IngestaPagos) -> UUID | None:
    return s.exec(
        select(TrabajoOrquestacion.trabajo_id).where(
            TrabajoOrquestacion.ingesta_pagos_id == ingesta.id
        )
    ).first()


def _respuesta(ingesta: IngestaPagos, trabajo_id: UUID | None) -> IngestaPagosRespuesta:
    return IngestaPagosRespuesta.model_validate(ingesta).model_copy(
        update={"trabajo_id": trabajo_id}
    )
