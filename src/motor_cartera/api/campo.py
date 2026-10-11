"""La configuracion de campo por HTTP: publicar una version y leerlas.

Una version se publica completa, en una transaccion, y no cambia nunca. El mismo contenido (el
mismo hash de su forma canonica) es la misma version: publicarlo otra vez responde 200 con la
existente, y no crea otra. Es configuracion operacional, no una fuente de negocio.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response
from sqlalchemy import text
from sqlmodel import Session

from motor_cartera.api.dependencias import Sesion, SesionDeLectura
from motor_cartera.api.errores import DetalleError, ErrorDeApi, errores
from motor_cartera.api.esquemas import Paginacion
from motor_cartera.api.esquemas_campo import (
    CalendarioRespuesta,
    ConfiguracionCampoDetalleRespuesta,
    ConfiguracionCampoEntrada,
    ConfiguracionCampoRespuesta,
    ExcepcionRespuesta,
    GestorRespuesta,
    PaginaConfiguracionesCampo,
    VentanaRespuesta,
)
from motor_cartera.api.operacional import SIN_CLAVE
from motor_cartera.campo_v2 import consultas
from motor_cartera.campo_v2.configuracion import (
    DIAS,
    ConfiguracionInvalida,
    Ventana,
    configuracion_de,
    leer,
    publicar,
)
from motor_cartera.db.modelos import VersionBasesOperativas, VersionConfiguracionCampo

router = APIRouter(tags=["campo"])

NO_EXISTE = (
    404,
    "CONFIGURACION_NO_ENCONTRADA",
    "No existe una configuracion de campo con ese configuracion_campo_version_id.",
)


@router.post(
    "/campo/configuraciones",
    status_code=201,
    response_model=ConfiguracionCampoDetalleRespuesta,
    summary="Publica una version de la configuracion de campo, o reutiliza la que ya existe",
    responses={
        200: {
            "model": ConfiguracionCampoDetalleRespuesta,
            "description": "Esa misma configuracion ya estaba publicada: se devuelve su version, "
            "con `reutilizada: true`, y no se crea otra.",
        },
        201: {"description": "Se publico una version nueva. `Location` apunta a ella."},
        **errores(
            *SIN_CLAVE,
            (
                422,
                "CONFIGURACION_INVALIDA",
                "Una base que no existe o esta inactiva, un gestor o un calendario repetido, un "
                "calendario que no existe, ventanas que se enciman, un cupo fuera de 1 a 1,000, "
                "una zona horaria desconocida.",
            ),
            (422, "ENTRADA_INVALIDA", "El documento no tiene la forma de una configuracion."),
        ),
    },
)
def publicar_configuracion(
    entrada: ConfiguracionCampoEntrada, response: Response, s: Sesion
) -> ConfiguracionCampoDetalleRespuesta:
    """Cada gestor con su codigo, su base, su calendario, su cupo diario declarado y si esta
    activo; cada calendario con las ventanas de cada dia de la semana y sus excepciones; la zona
    horaria; y la version de bases sobre la que se construye. Se valida todo antes de escribir
    nada: un solo problema rechaza el documento entero, con todos sus problemas. Un gestor no lleva
    nombre, correo ni telefono: es un codigo."""
    try:
        configuracion = leer(entrada.model_dump(mode="json", exclude_none=True))
        publicada = publicar(s, configuracion)
    except ConfiguracionInvalida as exc:
        error = ErrorDeApi(422, "CONFIGURACION_INVALIDA", str(exc))
        error.respuesta.detalles = [DetalleError(problema=p) for p in exc.problemas]
        raise error from exc
    s.commit()
    version = publicada.version
    s.refresh(version)
    if publicada.creada:
        response.headers["Location"] = (
            f"/campo/configuraciones/{version.configuracion_campo_version_id}"
        )
    else:
        response.status_code = 200
    return detalle(s, version, reutilizada=not publicada.creada)


@router.get(
    "/campo/configuraciones",
    response_model=PaginaConfiguracionesCampo,
    summary="Las versiones de la configuracion de campo, la mas reciente primero",
    responses=errores(*SIN_CLAVE, (422, "ENTRADA_INVALIDA", "La paginacion no es valida.")),
)
def configuraciones(
    parametros: Annotated[Paginacion, Query()], s: SesionDeLectura
) -> PaginaConfiguracionesCampo:
    """Cada version publicada, con su hash, su version de bases y sus conteos. Sus gestores y sus
    calendarios estan en `GET /campo/configuraciones/{configuracion_campo_version_id}`."""
    total, filas = consultas.configuraciones(
        s, desplazamiento=parametros.desplazamiento, limite=parametros.por_pagina
    )
    return PaginaConfiguracionesCampo(
        total=total,
        pagina=parametros.pagina,
        por_pagina=parametros.por_pagina,
        elementos=[resumen(version, bases) for version, bases in filas],
    )


@router.get(
    "/campo/configuraciones/{configuracion_campo_version_id}",
    response_model=ConfiguracionCampoDetalleRespuesta,
    summary="Una version de la configuracion de campo: sus gestores con sus cupos y sus "
    "calendarios",
    responses=errores(
        *SIN_CLAVE,
        NO_EXISTE,
        (422, "ENTRADA_INVALIDA", "El configuracion_campo_version_id no es un UUID."),
    ),
)
def configuracion(
    configuracion_campo_version_id: UUID, s: SesionDeLectura
) -> ConfiguracionCampoDetalleRespuesta:
    """`hash_verificado` dice si su hash, vuelto a calcular con lo que hay en sus tablas, es el que
    se guardo al publicarla."""
    return detalle(s, _version(s, configuracion_campo_version_id))


def _version(s: Session, configuracion_campo_version_id: UUID) -> VersionConfiguracionCampo:
    try:
        return consultas.configuracion(s, configuracion_campo_version_id)
    except consultas.ConfiguracionNoEncontrada as exc:
        raise ErrorDeApi(
            404,
            "CONFIGURACION_NO_ENCONTRADA",
            "No existe una configuracion de campo con configuracion_campo_version_id "
            f"{configuracion_campo_version_id}.",
        ) from exc


def resumen(
    v: VersionConfiguracionCampo, bases: VersionBasesOperativas, *, reutilizada: bool = False
) -> ConfiguracionCampoRespuesta:
    return ConfiguracionCampoRespuesta(
        configuracion_campo_version_id=v.configuracion_campo_version_id,
        nombre=v.nombre,
        bases_version_id=bases.bases_version_id,
        version_configuracion=v.version_configuracion,
        hash_configuracion=v.hash_configuracion,
        zona_horaria=v.zona_horaria,
        estado=v.estado,
        calendarios=v.calendarios,
        ventanas=v.ventanas,
        excepciones=v.excepciones,
        gestores=v.gestores,
        gestores_activos=v.gestores_activos,
        capacidad_declarada=v.capacidad_declarada,
        por_base=v.metadatos.get("gestores_por_base", {}),
        creada_en=v.creada_en,
        publicada_en=v.publicada_en,
        reutilizada=reutilizada,
    )


def _ventanas(ventanas: tuple[Ventana, ...]) -> list[VentanaRespuesta]:
    return [VentanaRespuesta(inicio=v.canonica()[0], fin=v.canonica()[1]) for v in ventanas]


def detalle(
    s: Session, v: VersionConfiguracionCampo, *, reutilizada: bool = False
) -> ConfiguracionCampoDetalleRespuesta:
    bases = consultas.bases_de(s, v)
    leida = configuracion_de(s, v)
    ids = s.execute(
        text(
            "SELECT 'C', codigo, calendario_id FROM calendario_campo WHERE "
            "version_configuracion_campo_id = :v UNION ALL SELECT 'G', codigo, gestor_id "
            "FROM gestor_campo WHERE version_configuracion_campo_id = :v"
        ),
        {"v": v.id},
    ).all()
    calendario_id = {codigo: uuid for tipo, codigo, uuid in ids if tipo == "C"}
    gestor_id = {codigo: uuid for tipo, codigo, uuid in ids if tipo == "G"}
    return ConfiguracionCampoDetalleRespuesta(
        **resumen(v, bases, reutilizada=reutilizada).model_dump(),
        hash_verificado=leida.hash(bases.hash_configuracion) == v.hash_configuracion,
        detalle_calendarios=[
            CalendarioRespuesta(
                calendario_id=calendario_id[c.codigo],
                codigo=c.codigo,
                semana={dia: _ventanas(c.semana[i]) for i, dia in enumerate(DIAS)},
                excepciones=[
                    ExcepcionRespuesta(
                        fecha=e.fecha,
                        tipo=e.tipo.value,
                        motivo=e.motivo,
                        ventanas=_ventanas(e.ventanas),
                    )
                    for e in c.excepciones
                ],
            )
            for c in leida.calendarios
        ],
        detalle_gestores=[
            GestorRespuesta(
                gestor_id=gestor_id[g.codigo],
                codigo=g.codigo,
                base=g.base,
                calendario=g.calendario,
                capacidad_cuentas_dia=g.capacidad_cuentas_dia,
                activo=g.activo,
            )
            for g in leida.gestores
        ],
    )
