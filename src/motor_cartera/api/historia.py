"""El modelo historico por HTTP: los cortes canonicos y las ejecuciones que los materializan.

No hay un POST: la historia de un dataset se abre sola, cuando su ingesta lo publica, y la de los
datasets anteriores la encola `motor-cartera backfill-historia`. Aqui se lee como va cada
materializacion y que cortes tiene la cartera, siempre por identificadores publicos.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request
from sqlalchemy import ColumnElement
from sqlmodel import Session, func, select
from sqlmodel.sql.expression import Select

from motor_cartera.api.dependencias import SesionDeLectura, buscar_corrida
from motor_cartera.api.errores import ErrorDeApi, errores
from motor_cartera.api.esquemas import (
    ArtefactoRespuesta,
    CorteDetalleRespuesta,
    CorteRespuesta,
    EjecucionHistoriaRespuesta,
    FuenteDelCorteRespuesta,
    Paginacion,
    PaginaCortes,
    PaginaHistoriasDeCorrida,
    PaginaHistoriasDePagos,
    RelacionPagosRespuesta,
)
from motor_cartera.config import Config
from motor_cartera.db.modelos import (
    Corrida,
    CorteCanonico,
    DatasetConformado,
    EjecucionHistoria,
    EstadoCorrida,
    EstadoHistoria,
    EstadoIngestaPagos,
    IngestaPagos,
    TrabajoOrquestacion,
)
from motor_cartera.historia import cuenta360
from motor_cartera.historia.cuenta360 import CorteNoEncontrado, CorteVisto

router = APIRouter(tags=["historia"])

SIN_CLAVE = (
    (401, "API_KEY_AUSENTE", "Falta la cabecera X-API-Key."),
    (401, "API_KEY_INVALIDA", "La API key no es valida."),
)
SIN_DATASET = (
    404,
    "SIN_DATASET_CONFORMADO",
    "La ingesta no publico un dataset conformado (cartera/v1, o no termino EXITOSA): no tiene "
    "historia.",
)


class ParametrosCortes(Paginacion):
    orden: Literal["desc", "asc"] = "desc"


@router.get(
    "/cartera/cortes",
    response_model=PaginaCortes,
    summary="Los cortes canonicos de la cartera del sistema, por fecha",
    responses=errores(*SIN_CLAVE, (422, "ENTRADA_INVALIDA", "La paginacion no es valida.")),
)
def listar_cortes(
    request: Request, parametros: Annotated[ParametrosCortes, Query()], s: SesionDeLectura
) -> PaginaCortes:
    """Una fotografia canonica por fecha de corte, la mas reciente primero (`orden=asc` para el otro
    sentido). `ultimo_corte` es el de fecha mas reciente, este o no en la pagina: el corte vigente
    del modelo historico se sabe sin inspeccionar corridas.

    Cada corte dice de que dataset conformado y de que corrida salio. Dos archivos con la misma
    cartera y la misma fecha son un solo corte: el segundo es una fuente equivalente
    (`GET /cartera/cortes/{corte_id}` las lista).
    """
    config: Config = request.app.state.config
    total, cortes, ultimo = cuenta360.listar_cortes(
        s,
        config.despacho_id,
        config.cartera_id,
        desplazamiento=parametros.desplazamiento,
        limite=parametros.por_pagina,
        descendente=parametros.orden == "desc",
    )
    return PaginaCortes(
        despacho_id=config.despacho_id,
        cartera_id=config.cartera_id,
        ultimo_corte=None if ultimo is None else _corte(ultimo),
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[_corte(visto) for visto in cortes],
    )


@router.get(
    "/cartera/cortes/{corte_id}",
    response_model=CorteDetalleRespuesta,
    summary="Un corte canonico con su evidencia y sus fuentes equivalentes",
    responses=errores(
        *SIN_CLAVE,
        (404, "CORTE_NO_ENCONTRADO", "No existe un corte canonico con ese corte_id."),
        (422, "ENTRADA_INVALIDA", "El corte_id no es un UUID."),
    ),
)
def obtener_corte(corte_id: UUID, s: SesionDeLectura) -> CorteDetalleRespuesta:
    """El linaje del corte, de punta a punta: el Parquet del dataset del que salieron sus snapshots,
    el archivo original con su SHA-256, y cada ejecucion historica que lo publico
    (`CORTE_PUBLICADO`) o que llego con la misma cartera en otro archivo (`FUENTE_EQUIVALENTE`), con
    su propio original. Es lo que explica de donde sale cada valor de un snapshot."""
    try:
        detalle = cuenta360.detalle_del_corte(s, corte_id)
    except CorteNoEncontrado as exc:
        raise ErrorDeApi(
            404, "CORTE_NO_ENCONTRADO", f"No existe un corte canonico con corte_id {corte_id}."
        ) from exc
    return CorteDetalleRespuesta(
        **_corte(detalle.visto).model_dump(),
        artefacto_conformado=ArtefactoRespuesta.model_validate(detalle.conformado),
        artefacto_original=ArtefactoRespuesta.model_validate(detalle.original),
        fuentes=[
            FuenteDelCorteRespuesta(
                historia_run_id=f.ejecucion.historia_run_id,
                resultado=f.ejecucion.resultado,
                dataset_id=f.dataset.dataset_id,
                run_id=f.corrida.run_id,
                artefacto_original=ArtefactoRespuesta.model_validate(f.original),
            )
            for f in detalle.fuentes
        ],
    )


@router.get(
    "/historias/{historia_run_id}",
    response_model=EjecucionHistoriaRespuesta,
    summary="Una ejecucion historica: como va o como termino la materializacion de un dataset",
    responses=errores(
        *SIN_CLAVE,
        (404, "HISTORIA_NO_ENCONTRADA", "No existe una ejecucion historica con ese id."),
        (422, "ENTRADA_INVALIDA", "El historia_run_id no es un UUID."),
    ),
)
def obtener_historia(historia_run_id: UUID, s: SesionDeLectura) -> EjecucionHistoriaRespuesta:
    """Mientras el estado sea `EN_PROCESO`, un worker no la ha terminado. `resultado` dice como
    termino: `CORTE_PUBLICADO`, `FUENTE_EQUIVALENTE` (la cartera de esa fecha ya existia con la
    misma firma: no se duplico nada), `PAGOS_PUBLICADOS`, o, si fallo,
    `CORTE_CANONICO_CONFLICTIVO` (ya hay un corte de esa fecha con otra firma, y no se toco) y los
    demas codigos de su detalle."""
    fila = s.exec(_ejecuciones(EjecucionHistoria.historia_run_id == historia_run_id)).first()
    if fila is None:
        raise ErrorDeApi(
            404,
            "HISTORIA_NO_ENCONTRADA",
            f"No existe una ejecucion historica con historia_run_id {historia_run_id}.",
        )
    return _ejecucion(*fila)


@router.get(
    "/corridas/{run_id}/historia",
    response_model=PaginaHistoriasDeCorrida,
    summary="La historia de una corrida: cada ejecucion que materializo su dataset conformado",
    responses=errores(
        *SIN_CLAVE,
        (404, "CORRIDA_NO_ENCONTRADA", "No existe una corrida con ese run_id."),
        (409, "CORRIDA_EN_PROCESO", "La corrida no ha terminado: todavia no tiene dataset."),
        SIN_DATASET,
        (422, "ENTRADA_INVALIDA", "El run_id no es un UUID, o la paginacion no es valida."),
    ),
)
def historia_de_corrida(
    run_id: UUID, paginacion: Annotated[Paginacion, Query()], s: SesionDeLectura
) -> PaginaHistoriasDeCorrida:
    """La materializacion historica del dataset conformado que publico la corrida, en paralelo a su
    flujo operacional: ni la decision, ni la organizacion territorial ni el ruteo la esperan. Una
    corrida de cartera/v2 que publico tiene al menos una; si una fallo y se reintento con el
    backfill, todas quedan aqui, la mas reciente primero."""
    corrida = buscar_corrida(s, run_id)
    if corrida.estado == EstadoCorrida.EN_PROCESO:
        raise ErrorDeApi(
            409,
            "CORRIDA_EN_PROCESO",
            "La corrida sigue en proceso: su dataset y su historia se conocen cuando termina.",
            run_id=run_id,
        )
    dataset = s.exec(
        select(DatasetConformado).where(DatasetConformado.corrida_id == corrida.id)
    ).first()
    if dataset is None:
        raise ErrorDeApi(404, SIN_DATASET[1], SIN_DATASET[2], run_id=run_id)
    total, pagina = _pagina(s, dataset, paginacion)
    return PaginaHistoriasDeCorrida(
        run_id=run_id,
        dataset_id=dataset.dataset_id,
        total=total,
        pagina=paginacion.pagina,
        por_pagina=paginacion.por_pagina,
        elementos=pagina,
    )


@router.get(
    "/pagos/{pagos_run_id}/historia",
    response_model=PaginaHistoriasDePagos,
    summary="La historia de una ingesta de pagos: sus pagos observados y su relacion con cuentas",
    responses=errores(
        *SIN_CLAVE,
        (404, "PAGOS_NO_ENCONTRADOS", "No existe una ingesta de pagos con ese pagos_run_id."),
        (409, "PAGOS_EN_PROCESO", "La ingesta no ha terminado: todavia no tiene dataset."),
        SIN_DATASET,
        (422, "ENTRADA_INVALIDA", "El pagos_run_id no es un UUID, o la paginacion no es valida."),
    ),
)
def historia_de_pagos(
    pagos_run_id: UUID, paginacion: Annotated[Paginacion, Query()], s: SesionDeLectura
) -> PaginaHistoriasDePagos:
    """Cada ejecucion que materializo sus movimientos como pagos observados, uno por fila. Si ya se
    publicaron, `relacion` dice cuantos tienen hoy una cuenta canonica con su CLIENTE_UNICO y
    cuantos no (`SIN_CUENTA_OBSERVADA`). Se calcula al consultar: un corte que trae despues a esos
    clientes los relaciona sin tocar ningun pago."""
    ingesta = s.exec(select(IngestaPagos).where(IngestaPagos.pagos_run_id == pagos_run_id)).first()
    if ingesta is None:
        raise ErrorDeApi(
            404,
            "PAGOS_NO_ENCONTRADOS",
            f"No existe una ingesta de pagos con pagos_run_id {pagos_run_id}.",
            pagos_run_id=pagos_run_id,
        )
    if ingesta.estado == EstadoIngestaPagos.EN_PROCESO:
        raise ErrorDeApi(
            409,
            "PAGOS_EN_PROCESO",
            "La ingesta sigue en proceso: su dataset y su historia se conocen cuando termina.",
            pagos_run_id=pagos_run_id,
        )
    dataset = s.exec(
        select(DatasetConformado).where(DatasetConformado.ingesta_pagos_id == ingesta.id)
    ).first()
    if dataset is None:
        raise ErrorDeApi(404, SIN_DATASET[1], SIN_DATASET[2], pagos_run_id=pagos_run_id)
    total, pagina = _pagina(s, dataset, paginacion)
    publicada = s.exec(
        select(func.count())
        .select_from(EjecucionHistoria)
        .where(
            EjecucionHistoria.dataset_conformado_id == dataset.id,
            EjecucionHistoria.estado == EstadoHistoria.EXITOSA,
        )
    ).one()
    relacion = None
    if publicada:
        r = cuenta360.relacion_de_pagos(s, dataset.id)
        relacion = RelacionPagosRespuesta(
            pagos_con_cuenta_observada=r.con_cuenta_observada,
            pagos_sin_cuenta_observada=r.sin_cuenta_observada,
            clientes_sin_cuenta_observada=r.clientes_sin_cuenta_observada,
        )
    return PaginaHistoriasDePagos(
        pagos_run_id=pagos_run_id,
        dataset_id=dataset.dataset_id,
        relacion=relacion,
        total=total,
        pagina=paginacion.pagina,
        por_pagina=paginacion.por_pagina,
        elementos=pagina,
    )


def _pagina(
    s: Session, dataset: DatasetConformado, paginacion: Paginacion
) -> tuple[int, list[EjecucionHistoriaRespuesta]]:
    del_dataset = EjecucionHistoria.dataset_conformado_id == dataset.id
    total = s.exec(select(func.count()).select_from(EjecucionHistoria).where(del_dataset)).one()
    filas = s.exec(
        _ejecuciones(del_dataset)
        .order_by(EjecucionHistoria.iniciada_en.desc(), EjecucionHistoria.id.desc())
        .offset(paginacion.desplazamiento)
        .limit(paginacion.por_pagina)
    ).all()
    return total, [_ejecucion(*fila) for fila in filas]


def _ejecuciones(condicion: ColumnElement) -> Select[Any]:
    """Las ejecuciones historicas que cumplen `condicion`, con los identificadores publicos de su
    dataset, de su fuente (corrida o ingesta de pagos), de su corte y de su trabajo."""
    return (
        select(
            EjecucionHistoria,
            DatasetConformado.dataset_id,
            Corrida.run_id,
            Corrida.fecha_corte,
            IngestaPagos.pagos_run_id,
            CorteCanonico.corte_id,
            TrabajoOrquestacion.trabajo_id,
        )
        .select_from(EjecucionHistoria)
        .join(DatasetConformado, DatasetConformado.id == EjecucionHistoria.dataset_conformado_id)
        .outerjoin(Corrida, Corrida.id == DatasetConformado.corrida_id)
        .outerjoin(IngestaPagos, IngestaPagos.id == DatasetConformado.ingesta_pagos_id)
        .outerjoin(CorteCanonico, CorteCanonico.id == EjecucionHistoria.corte_canonico_id)
        .outerjoin(
            TrabajoOrquestacion,
            TrabajoOrquestacion.ejecucion_historia_id == EjecucionHistoria.id,
        )
        .where(condicion)
    )


def _ejecucion(
    ejecucion: EjecucionHistoria,
    dataset_id: UUID,
    run_id: UUID | None,
    fecha_corte,
    pagos_run_id: UUID | None,
    corte_id: UUID | None,
    trabajo_id: UUID | None,
) -> EjecucionHistoriaRespuesta:
    return EjecucionHistoriaRespuesta(
        historia_run_id=ejecucion.historia_run_id,
        tipo_fuente=ejecucion.tipo_fuente,
        version_modelo=ejecucion.version_modelo,
        estado=ejecucion.estado,
        resultado=ejecucion.resultado,
        dataset_id=dataset_id,
        run_id=run_id,
        pagos_run_id=pagos_run_id,
        corte_id=corte_id,
        fecha_corte=fecha_corte,
        registros_leidos=ejecucion.registros_leidos,
        registros_publicados=ejecucion.registros_publicados,
        trabajo_id=trabajo_id,
        iniciada_en=ejecucion.iniciada_en,
        terminada_en=ejecucion.terminada_en,
        detalle=ejecucion.detalle,
    )


def _corte(visto: CorteVisto) -> CorteRespuesta:
    c = visto.corte
    return CorteRespuesta(
        corte_id=c.corte_id,
        fecha_corte=c.fecha_corte,
        cuentas=c.cuentas,
        firma_contenido=c.firma_contenido,
        version_modelo=c.version_modelo,
        dataset_id=visto.dataset_id,
        run_id=visto.run_id,
        creado_en=c.creado_en,
    )
