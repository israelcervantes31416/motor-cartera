"""Lo que la API y la linea de comandos leen de ruteo/v2: las versiones de la red vial, las
ejecuciones, sus rutas y sus paradas, y las paradas de una cuenta a traves de las ejecuciones.

Cada consulta entra por un indice: una ruta por su ruta_id o por la llave (ejecucion, gestor); las
paradas de una ruta por (ejecucion, gestor, orden); una pagina de un estado de la ejecucion por
(ejecucion, estado, cuenta); las de una cuenta por (cuenta, ejecucion). Ninguna devuelve una
coordenada, una geometria, una polilinea ni una matriz.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import func
from sqlmodel import Session, select
from sqlmodel.sql.expression import Select

from motor_cartera.db.modelos import (
    BaseOperativa,
    CuentaCanonica,
    EjecucionCampoV2,
    EjecucionRuteoV2,
    EstadoRuteoV2,
    GestorCampo,
    ParadaRutaV2,
    RutaGestorV2,
    TrabajoOrquestacion,
    VersionConfiguracionCampo,
    VersionRedVial,
)
from motor_cartera.ruteo_v2.ejecuciones import COLUMNA_DEL_ESTADO_PARADA, COLUMNA_DEL_ESTADO_RUTA
from motor_cartera.ruteo_v2.politica import EstadoParada, EstadoRuta


class RedNoEncontrada(Exception):
    """No hay una version de la red vial con ese red_vial_version_id."""


class EjecucionNoEncontrada(Exception):
    """No hay una ejecucion de ruteo/v2 con ese ruteo_run_id."""


class RutaNoEncontrada(Exception):
    """No hay una ruta con ese ruta_id."""


# --- las redes ------------------------------------------------------------------------------------


def redes(s: Session, *, desplazamiento: int, limite: int) -> tuple[int, list[VersionRedVial]]:
    total = s.exec(select(func.count()).select_from(VersionRedVial)).one()
    filas = s.exec(
        select(VersionRedVial)
        .order_by(VersionRedVial.id.desc())
        .offset(desplazamiento)
        .limit(limite)
    ).all()
    return total, list(filas)


def red(s: Session, red_vial_version_id: UUID) -> VersionRedVial:
    encontrada = s.exec(
        select(VersionRedVial).where(VersionRedVial.red_vial_version_id == red_vial_version_id)
    ).one_or_none()
    if encontrada is None:
        raise RedNoEncontrada(red_vial_version_id)
    return encontrada


# --- las ejecuciones ------------------------------------------------------------------------------


@dataclass(frozen=True)
class EjecucionVista:
    ejecucion: EjecucionRuteoV2
    campo_run_id: UUID
    red: VersionRedVial
    zona_horaria: str
    """La de la configuracion de campo del plan: la de las ventanas de la jornada."""
    trabajo_id: UUID | None


def _ejecuciones() -> Select[Any]:
    return (
        select(
            EjecucionRuteoV2,
            EjecucionCampoV2.campo_run_id,
            VersionRedVial,
            VersionConfiguracionCampo.zona_horaria,
            TrabajoOrquestacion.trabajo_id,
        )
        .join(EjecucionCampoV2, EjecucionCampoV2.id == EjecucionRuteoV2.ejecucion_campo_v2_id)
        .join(VersionRedVial, VersionRedVial.id == EjecucionRuteoV2.version_red_vial_id)
        .join(
            VersionConfiguracionCampo,
            VersionConfiguracionCampo.id == EjecucionCampoV2.version_configuracion_campo_id,
        )
        .outerjoin(
            TrabajoOrquestacion, TrabajoOrquestacion.ejecucion_ruteo_v2_id == EjecucionRuteoV2.id
        )
    )


def obtener(s: Session, ruteo_run_id: UUID) -> EjecucionVista:
    fila = s.exec(_ejecuciones().where(EjecucionRuteoV2.ruteo_run_id == ruteo_run_id)).one_or_none()
    if fila is None:
        raise EjecucionNoEncontrada(ruteo_run_id)
    return EjecucionVista(*fila)


def listar(
    s: Session,
    *,
    campo: EjecucionCampoV2 | None,
    red_vial: VersionRedVial | None,
    estado: str | None,
    desplazamiento: int,
    limite: int,
) -> tuple[int, list[EjecucionVista]]:
    condiciones = []
    if campo is not None:
        condiciones.append(EjecucionRuteoV2.ejecucion_campo_v2_id == campo.id)
    if red_vial is not None:
        condiciones.append(EjecucionRuteoV2.version_red_vial_id == red_vial.id)
    if estado is not None:
        condiciones.append(EjecucionRuteoV2.estado == estado)
    total = s.exec(select(func.count()).select_from(EjecucionRuteoV2).where(*condiciones)).one()
    filas = s.exec(
        _ejecuciones()
        .where(*condiciones)
        .order_by(EjecucionRuteoV2.id.desc())
        .offset(desplazamiento)
        .limit(limite)
    ).all()
    return total, [EjecucionVista(*fila) for fila in filas]


# --- las rutas ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RutaVista:
    ruta: RutaGestorV2
    gestor_id: UUID
    codigo_gestor: str
    base_id: UUID
    codigo_base: str


def _rutas() -> Select[Any]:
    return (
        select(
            RutaGestorV2,
            GestorCampo.gestor_id,
            GestorCampo.codigo,
            BaseOperativa.base_id,
            BaseOperativa.codigo,
        )
        .join(GestorCampo, GestorCampo.id == RutaGestorV2.gestor_campo_id)
        .join(BaseOperativa, BaseOperativa.id == RutaGestorV2.base_operativa_id)
    )


def rutas(
    s: Session,
    vista: EjecucionVista,
    *,
    gestor: str | None,
    base: str | None,
    estado: str | None,
    desplazamiento: int,
    limite: int,
) -> tuple[int, list[RutaVista]]:
    """Las rutas de la ejecucion, en el orden del codigo del gestor, por la llave (ejecucion,
    gestor). Con un solo filtro de estado, el total de una ejecucion publicada sale de lo que conto
    al publicar. Como en las paradas, la pagina se elige primero con las llaves (el gestor y su
    codigo) y solo sus rutas se leen enteras."""
    e = vista.ejecucion
    condiciones: list = [RutaGestorV2.ejecucion_ruteo_v2_id == e.id]
    if gestor is not None:
        condiciones.append(GestorCampo.codigo == gestor)
    if base is not None:
        condiciones.append(BaseOperativa.codigo == base)
    if estado is not None:
        condiciones.append(RutaGestorV2.estado_ruta == estado)
    total = None
    if e.estado == EstadoRuteoV2.EXITOSA and gestor is None and base is None:
        total = (
            e.gestores_objetivo
            if estado is None
            else getattr(e, COLUMNA_DEL_ESTADO_RUTA[EstadoRuta(estado)])
        )
    if total is None:
        total = s.exec(
            select(func.count())
            .select_from(RutaGestorV2)
            .join(GestorCampo, GestorCampo.id == RutaGestorV2.gestor_campo_id)
            .join(BaseOperativa, BaseOperativa.id == RutaGestorV2.base_operativa_id)
            .where(*condiciones)
        ).one()
    pagina = (
        select(RutaGestorV2.gestor_campo_id)
        .join(GestorCampo, GestorCampo.id == RutaGestorV2.gestor_campo_id)
        .join(BaseOperativa, BaseOperativa.id == RutaGestorV2.base_operativa_id)
        .where(*condiciones)
        .order_by(GestorCampo.codigo)
        .offset(desplazamiento)
        .limit(limite)
        .subquery()
    )
    filas = s.exec(
        _rutas()
        .join(pagina, pagina.c.gestor_campo_id == RutaGestorV2.gestor_campo_id)
        .where(RutaGestorV2.ejecucion_ruteo_v2_id == e.id)
        .order_by(GestorCampo.codigo)
    ).all()
    return total, [RutaVista(*fila) for fila in filas]


def ruta(s: Session, ruta_id: UUID) -> tuple[RutaVista, EjecucionVista]:
    fila = s.exec(_rutas().where(RutaGestorV2.ruta_id == ruta_id)).one_or_none()
    if fila is None:
        raise RutaNoEncontrada(ruta_id)
    vista = RutaVista(*fila)
    ejecucion = s.exec(
        _ejecuciones().where(EjecucionRuteoV2.id == vista.ruta.ejecucion_ruteo_v2_id)
    ).one()
    return vista, EjecucionVista(*ejecucion)


# --- las paradas ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class ParadaVista:
    parada: ParadaRutaV2
    cuenta_id: UUID
    cliente_unico: str
    codigo_gestor: str
    ruta_id: UUID


def _paradas() -> Select[Any]:
    return (
        select(
            ParadaRutaV2,
            CuentaCanonica.cuenta_id,
            CuentaCanonica.cliente_unico,
            GestorCampo.codigo,
            RutaGestorV2.ruta_id,
        )
        .join(CuentaCanonica, CuentaCanonica.id == ParadaRutaV2.cuenta_canonica_id)
        .join(GestorCampo, GestorCampo.id == ParadaRutaV2.gestor_campo_id)
        .join(
            RutaGestorV2,
            (RutaGestorV2.ejecucion_ruteo_v2_id == ParadaRutaV2.ejecucion_ruteo_v2_id)
            & (RutaGestorV2.gestor_campo_id == ParadaRutaV2.gestor_campo_id),
        )
    )


def paradas_de_ruta(
    s: Session, ruta_vista: RutaVista, *, desplazamiento: int, limite: int
) -> tuple[int, list[ParadaVista]]:
    """Las paradas de una ruta: las programadas en su orden de visita, y despues las demas por
    cuenta_id. Por (ejecucion, gestor, orden)."""
    r = ruta_vista.ruta
    condiciones = [
        ParadaRutaV2.ejecucion_ruteo_v2_id == r.ejecucion_ruteo_v2_id,
        ParadaRutaV2.gestor_campo_id == r.gestor_campo_id,
    ]
    filas = s.exec(
        _paradas()
        .where(*condiciones)
        .order_by(ParadaRutaV2.orden_visita.asc().nulls_last(), CuentaCanonica.cuenta_id)
        .offset(desplazamiento)
        .limit(limite)
    ).all()
    return r.paradas_campo, [ParadaVista(*fila) for fila in filas]


@dataclass(frozen=True)
class FiltrosParadas:
    estado: str | None = None
    gestor: str | None = None
    base: str | None = None
    prioridad: str | None = None


def paradas(
    s: Session, vista: EjecucionVista, filtros: FiltrosParadas, *, desplazamiento: int, limite: int
) -> tuple[int, list[ParadaVista]]:
    """Una pagina de las paradas de la ejecucion, filtradas en la base: el estado por (ejecucion,
    estado, cuenta); un gestor por (ejecucion, gestor); una base, por las rutas de sus gestores. En
    el orden de la cuenta.

    La pagina se elige primero, solo con las llaves de las paradas y por sus indices, y despues se
    le unen la cuenta, el gestor y la ruta: una pagina profunda no une ni ordena todas las paradas
    de la ejecucion para saltarse las anteriores."""
    e = vista.ejecucion
    condiciones: list = [ParadaRutaV2.ejecucion_ruteo_v2_id == e.id]
    if filtros.estado is not None:
        condiciones.append(ParadaRutaV2.estado == filtros.estado)
    if filtros.prioridad is not None:
        condiciones.append(ParadaRutaV2.prioridad == filtros.prioridad)
    if filtros.gestor is not None:
        gestor = s.exec(
            select(RutaGestorV2.gestor_campo_id)
            .join(GestorCampo, GestorCampo.id == RutaGestorV2.gestor_campo_id)
            .where(RutaGestorV2.ejecucion_ruteo_v2_id == e.id, GestorCampo.codigo == filtros.gestor)
        ).one_or_none()
        if gestor is None:
            return 0, []
        condiciones.append(ParadaRutaV2.gestor_campo_id == gestor)
    if filtros.base is not None:
        gestores = select(RutaGestorV2.gestor_campo_id).where(
            RutaGestorV2.ejecucion_ruteo_v2_id == e.id,
            RutaGestorV2.base_operativa_id.in_(
                select(BaseOperativa.id).where(BaseOperativa.codigo == filtros.base)
            ),
        )
        condiciones.append(ParadaRutaV2.gestor_campo_id.in_(gestores))
    total = None
    if e.estado == EstadoRuteoV2.EXITOSA and not (
        filtros.gestor or filtros.base or filtros.prioridad
    ):
        total = (
            e.paradas_objetivo
            if filtros.estado is None
            else getattr(e, COLUMNA_DEL_ESTADO_PARADA[EstadoParada(filtros.estado)])
        )
    if total is None:
        total = s.exec(select(func.count()).select_from(ParadaRutaV2).where(*condiciones)).one()
    pagina = (
        select(ParadaRutaV2.cuenta_canonica_id)
        .where(*condiciones)
        .order_by(ParadaRutaV2.cuenta_canonica_id)
        .offset(desplazamiento)
        .limit(limite)
        .subquery()
    )
    filas = s.exec(
        _paradas()
        .join(pagina, pagina.c.cuenta_canonica_id == ParadaRutaV2.cuenta_canonica_id)
        .where(ParadaRutaV2.ejecucion_ruteo_v2_id == e.id)
        .order_by(ParadaRutaV2.cuenta_canonica_id)
    ).all()
    return total, [ParadaVista(*fila) for fila in filas]


# --- las de una cuenta ----------------------------------------------------------------------------


@dataclass(frozen=True)
class ParadaEnHistoria:
    vista: ParadaVista
    ejecucion: EjecucionRuteoV2
    codigo_base: str
    estado_ruta: str
    zona_horaria: str


def _de_la_cuenta(cuenta: CuentaCanonica) -> Select[Any]:
    return (
        _paradas()
        .add_columns(
            EjecucionRuteoV2,
            BaseOperativa.codigo,
            RutaGestorV2.estado_ruta,
            VersionConfiguracionCampo.zona_horaria,
        )
        .join(EjecucionRuteoV2, EjecucionRuteoV2.id == ParadaRutaV2.ejecucion_ruteo_v2_id)
        .join(BaseOperativa, BaseOperativa.id == RutaGestorV2.base_operativa_id)
        .join(EjecucionCampoV2, EjecucionCampoV2.id == EjecucionRuteoV2.ejecucion_campo_v2_id)
        .join(
            VersionConfiguracionCampo,
            VersionConfiguracionCampo.id == EjecucionCampoV2.version_configuracion_campo_id,
        )
        .where(
            ParadaRutaV2.cuenta_canonica_id == cuenta.id,
            EjecucionRuteoV2.estado == EstadoRuteoV2.EXITOSA,
        )
    )


def _en_historia(fila) -> ParadaEnHistoria:
    return ParadaEnHistoria(ParadaVista(*fila[:5]), *fila[5:])


def historial(
    s: Session, cuenta: CuentaCanonica, *, desplazamiento: int, limite: int
) -> tuple[int, list[ParadaEnHistoria]]:
    """Las paradas de la cuenta en las ejecuciones EXITOSA, de la fecha de operacion mas reciente a
    la mas antigua; en la misma fecha, la ejecucion mas reciente primero. Por (cuenta,
    ejecucion)."""
    total = s.exec(
        select(func.count())
        .select_from(ParadaRutaV2)
        .join(EjecucionRuteoV2, EjecucionRuteoV2.id == ParadaRutaV2.ejecucion_ruteo_v2_id)
        .where(
            ParadaRutaV2.cuenta_canonica_id == cuenta.id,
            EjecucionRuteoV2.estado == EstadoRuteoV2.EXITOSA,
        )
    ).one()
    filas = s.exec(
        _de_la_cuenta(cuenta)
        .order_by(EjecucionRuteoV2.fecha_operacion.desc(), EjecucionRuteoV2.id.desc())
        .offset(desplazamiento)
        .limit(limite)
    ).all()
    return total, [_en_historia(fila) for fila in filas]


def de_ejecucion(s: Session, cuenta: CuentaCanonica, ejecucion_id: int) -> ParadaEnHistoria | None:
    """La parada de la cuenta en esa ejecucion EXITOSA, si la tiene: la que declara un contexto de
    la app (app/v1), en lugar de la ultima. Por (ejecucion, cuenta)."""
    fila = s.exec(
        _de_la_cuenta(cuenta).where(ParadaRutaV2.ejecucion_ruteo_v2_id == ejecucion_id)
    ).one_or_none()
    return None if fila is None else _en_historia(fila)


def ultima(s: Session, cuenta: CuentaCanonica, al: date | None = None) -> ParadaEnHistoria | None:
    """La parada de la ejecucion EXITOSA mas reciente de la cuenta (con `al`, la de fecha de
    operacion hasta ese dia): la que resume la Cuenta 360."""
    consulta = _de_la_cuenta(cuenta)
    if al is not None:
        consulta = consulta.where(EjecucionRuteoV2.fecha_operacion <= al)
    fila = s.exec(
        consulta.order_by(
            EjecucionRuteoV2.fecha_operacion.desc(), EjecucionRuteoV2.id.desc()
        ).limit(1)
    ).one_or_none()
    return None if fila is None else _en_historia(fila)
