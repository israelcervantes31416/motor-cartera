"""Las bases operativas por HTTP: publicar una version de su configuracion y leerlas.

Una version se publica completa, en una transaccion, y no cambia nunca. El mismo contenido (el
mismo hash de su forma canonica) es la misma version: publicarlo otra vez responde 200 con la
existente, y no crea otra.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response
from sqlalchemy import func, text
from sqlmodel import Session, col, select

from motor_cartera.api.dependencias import Sesion, SesionDeLectura
from motor_cartera.api.errores import DetalleError, ErrorDeApi, errores
from motor_cartera.api.esquemas import Paginacion
from motor_cartera.api.esquemas_geografia import (
    BaseRespuesta,
    PaginaVersionesBases,
    VersionBasesDetalleRespuesta,
    VersionBasesEntrada,
    VersionBasesRespuesta,
)
from motor_cartera.api.operacional import SIN_CLAVE
from motor_cartera.bases.configuracion import (
    ConfiguracionInvalida,
    configuracion_de,
    leer,
    publicar,
)
from motor_cartera.db.modelos import VersionBasesOperativas

router = APIRouter(tags=["bases"])

NO_EXISTE = (
    404,
    "BASES_NO_ENCONTRADAS",
    "No existe una version de bases operativas con ese bases_version_id.",
)


@router.post(
    "/bases/versiones",
    status_code=201,
    response_model=VersionBasesRespuesta,
    summary="Publica una version de la configuracion de bases operativas",
    responses={
        200: {
            "model": VersionBasesRespuesta,
            "description": "Esa misma configuracion ya estaba publicada: se devuelve su version, "
            "con `reutilizada: true`, y no se crea otra.",
        },
        201: {"description": "Se publico una version nueva. `Location` apunta a ella."},
        **errores(
            *SIN_CLAVE,
            (
                422,
                "CONFIGURACION_INVALIDA",
                "Un codigo repetido, una coordenada fuera de rango o (0, 0), una cobertura vacia, "
                "repetida o que no es un CVEGEO.",
            ),
            (422, "ENTRADA_INVALIDA", "El documento no tiene la forma de una version de bases."),
        ),
    },
)
def publicar_bases(
    entrada: VersionBasesEntrada, response: Response, s: Sesion
) -> VersionBasesRespuesta:
    """Cada base con su codigo, su nombre, su punto (lat, lon en EPSG:4326), si esta activa y los
    municipios (CVEGEO) que puede atender. La cobertura se declara: nunca se infiere de que una base
    este cerca. Es configuracion operacional, no datos de la cartera."""
    try:
        configuracion = leer(entrada.model_dump())
    except ConfiguracionInvalida as exc:
        error = ErrorDeApi(422, "CONFIGURACION_INVALIDA", str(exc))
        error.respuesta.detalles = [DetalleError(problema=p) for p in exc.problemas]
        raise error from exc
    publicada = publicar(s, configuracion)
    s.commit()
    version = publicada.version
    s.refresh(version)
    if publicada.creada:
        response.headers["Location"] = f"/bases/versiones/{version.bases_version_id}"
    else:
        response.status_code = 200
    return version_respuesta(version, reutilizada=not publicada.creada)


@router.get(
    "/bases/versiones",
    response_model=PaginaVersionesBases,
    summary="Las versiones de bases operativas, la mas reciente primero",
    responses=errores(*SIN_CLAVE, (422, "ENTRADA_INVALIDA", "La paginacion no es valida.")),
)
def versiones(
    parametros: Annotated[Paginacion, Query()], s: SesionDeLectura
) -> PaginaVersionesBases:
    """Cada version publicada, con su hash y sus conteos. Sus bases, con su punto y su cobertura,
    estan en `GET /bases/versiones/{bases_version_id}`."""
    total = s.exec(select(func.count()).select_from(VersionBasesOperativas)).one()
    filas = s.exec(
        select(VersionBasesOperativas)
        .order_by(col(VersionBasesOperativas.id).desc())
        .offset(parametros.desplazamiento)
        .limit(parametros.por_pagina)
    ).all()
    return PaginaVersionesBases(
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[version_respuesta(v) for v in filas],
    )


@router.get(
    "/bases/versiones/{bases_version_id}",
    response_model=VersionBasesDetalleRespuesta,
    summary="Una version de bases: cada base con su punto, si esta activa y su cobertura",
    responses=errores(
        *SIN_CLAVE, NO_EXISTE, (422, "ENTRADA_INVALIDA", "El bases_version_id no es un UUID.")
    ),
)
def version(bases_version_id: UUID, s: SesionDeLectura) -> VersionBasesDetalleRespuesta:
    """`hash_verificado` dice si su hash, vuelto a calcular con lo que hay en sus tablas, es el que
    se guardo al publicarla."""
    v = _version(s, bases_version_id)
    configuracion = configuracion_de(s, v)
    ids = dict(
        s.execute(
            text(
                "SELECT codigo, base_id FROM base_operativa WHERE version_bases_operativas_id = "
                ":version"
            ),
            {"version": v.id},
        )
        .tuples()
        .all()
    )
    return VersionBasesDetalleRespuesta(
        **version_respuesta(v).model_dump(),
        hash_verificado=configuracion.hash == v.hash_configuracion,
        detalle_bases=[
            BaseRespuesta(
                base_id=ids[b.codigo],
                codigo=b.codigo,
                nombre=b.nombre,
                activa=b.activa,
                lat=float(b.latitud),
                lon=float(b.longitud),
                municipios=list(b.municipios),
            )
            for b in configuracion.bases
        ],
    )


def _version(s: Session, bases_version_id: UUID) -> VersionBasesOperativas:
    v = s.exec(
        select(VersionBasesOperativas).where(
            VersionBasesOperativas.bases_version_id == bases_version_id
        )
    ).first()
    if v is None:
        raise ErrorDeApi(
            404,
            "BASES_NO_ENCONTRADAS",
            f"No existe una version de bases operativas con bases_version_id {bases_version_id}.",
        )
    return v


def version_respuesta(v: VersionBasesOperativas, *, reutilizada: bool = False):
    return VersionBasesRespuesta(
        bases_version_id=v.bases_version_id,
        nombre=v.nombre,
        version_configuracion=v.version_configuracion,
        hash_configuracion=v.hash_configuracion,
        estado=v.estado,
        bases=v.bases,
        activas=v.activas,
        coberturas=v.coberturas,
        creada_en=v.creada_en,
        reutilizada=reutilizada,
    )
