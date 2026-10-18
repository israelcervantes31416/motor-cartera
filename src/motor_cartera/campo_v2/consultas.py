"""Lo que la API lee de campo/v2: las configuraciones, los planes, sus asignaciones, sus cargas y
sus unidades, y las asignaciones de una cuenta a traves de los planes.

Cada consulta entra por un indice: una pagina de asignaciones por su llave, o por (plan, estado,
cuenta), (plan, base, cuenta), (plan, gestor, cuenta), (plan, prioridad, cuenta) o (plan,
microtramo, cuenta); una zona, una subzona o un tramo, por la base de su unidad; las de una cuenta
por (cuenta, plan). Ninguna devuelve coordenadas, celdas ni geometrias: los identificadores de la
particion, la base, el gestor, el estado y los motivos.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import func
from sqlmodel import Session, select
from sqlmodel.sql.expression import Select

from motor_cartera.campo_v2.politica import CONTEO_DE_ESTADO, EstadoCampo, NivelUnidad
from motor_cartera.db.modelos import (
    AsignacionCampoCuenta,
    BaseOperativa,
    CargaGestorCampo,
    CorteCanonico,
    CuentaCanonica,
    EjecucionCampoV2,
    EjecucionDecisionV2,
    EjecucionTerritorialV2,
    EstadoCampoV2,
    GestorCampo,
    TrabajoOrquestacion,
    UnidadTerritorialCampo,
    VersionBasesOperativas,
    VersionConfiguracionCampo,
)


class PlanNoEncontrado(Exception):
    """No hay un plan de campo/v2 con ese campo_run_id."""


class ConfiguracionNoEncontrada(Exception):
    """No hay una configuracion de campo con ese configuracion_campo_version_id."""


# --- las configuraciones --------------------------------------------------------------------------


def configuracion(s: Session, configuracion_campo_version_id: UUID) -> VersionConfiguracionCampo:
    encontrada = s.exec(
        select(VersionConfiguracionCampo).where(
            VersionConfiguracionCampo.configuracion_campo_version_id
            == configuracion_campo_version_id
        )
    ).one_or_none()
    if encontrada is None:
        raise ConfiguracionNoEncontrada(configuracion_campo_version_id)
    return encontrada


def bases_de(s: Session, version: VersionConfiguracionCampo) -> VersionBasesOperativas:
    return s.get_one(VersionBasesOperativas, version.version_bases_operativas_id)


def configuraciones(
    s: Session, *, desplazamiento: int, limite: int
) -> tuple[int, list[tuple[VersionConfiguracionCampo, VersionBasesOperativas]]]:
    total = s.exec(select(func.count()).select_from(VersionConfiguracionCampo)).one()
    filas = s.exec(
        select(VersionConfiguracionCampo, VersionBasesOperativas)
        .join(
            VersionBasesOperativas,
            VersionBasesOperativas.id == VersionConfiguracionCampo.version_bases_operativas_id,
        )
        .order_by(VersionConfiguracionCampo.id.desc())
        .offset(desplazamiento)
        .limit(limite)
    ).all()
    return total, [tuple(f) for f in filas]


# --- los planes -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanVista:
    ejecucion: EjecucionCampoV2
    corte_id: UUID
    fecha_corte: date
    decision_run_id: UUID
    territorial_run_id: UUID
    configuracion: VersionConfiguracionCampo
    bases_version_id: UUID
    trabajo_id: UUID | None


def _planes() -> Select[Any]:
    return (
        select(
            EjecucionCampoV2,
            CorteCanonico.corte_id,
            CorteCanonico.fecha_corte,
            EjecucionDecisionV2.decision_run_id,
            EjecucionTerritorialV2.territorial_run_id,
            VersionConfiguracionCampo,
            VersionBasesOperativas.bases_version_id,
            TrabajoOrquestacion.trabajo_id,
        )
        .join(CorteCanonico, CorteCanonico.id == EjecucionCampoV2.corte_canonico_id)
        .join(
            EjecucionDecisionV2,
            EjecucionDecisionV2.id == EjecucionCampoV2.ejecucion_decision_v2_id,
        )
        .join(
            EjecucionTerritorialV2,
            EjecucionTerritorialV2.id == EjecucionCampoV2.ejecucion_territorial_v2_id,
        )
        .join(
            VersionConfiguracionCampo,
            VersionConfiguracionCampo.id == EjecucionCampoV2.version_configuracion_campo_id,
        )
        .join(
            VersionBasesOperativas,
            VersionBasesOperativas.id == EjecucionCampoV2.version_bases_operativas_id,
        )
        .outerjoin(
            TrabajoOrquestacion, TrabajoOrquestacion.ejecucion_campo_v2_id == EjecucionCampoV2.id
        )
    )


def obtener(s: Session, campo_run_id: UUID) -> PlanVista:
    fila = s.exec(_planes().where(EjecucionCampoV2.campo_run_id == campo_run_id)).one_or_none()
    if fila is None:
        raise PlanNoEncontrado(campo_run_id)
    return PlanVista(*fila)


def listar(
    s: Session,
    corte: CorteCanonico | None,
    fecha_operacion: date | None,
    *,
    desplazamiento: int,
    limite: int,
) -> tuple[int, list[PlanVista]]:
    condiciones = []
    if corte is not None:
        condiciones.append(EjecucionCampoV2.corte_canonico_id == corte.id)
    if fecha_operacion is not None:
        condiciones.append(EjecucionCampoV2.fecha_operacion == fecha_operacion)
    total = s.exec(select(func.count()).select_from(EjecucionCampoV2).where(*condiciones)).one()
    filas = s.exec(
        _planes()
        .where(*condiciones)
        .order_by(EjecucionCampoV2.id.desc())
        .offset(desplazamiento)
        .limit(limite)
    ).all()
    return total, [PlanVista(*fila) for fila in filas]


# --- las asignaciones -----------------------------------------------------------------------------


@dataclass(frozen=True)
class AsignacionVista:
    asignacion: AsignacionCampoCuenta
    cuenta_id: UUID
    cliente_unico: str
    base_id: UUID | None
    codigo_base: str | None
    gestor_id: UUID | None
    codigo_gestor: str | None
    cvegeo: str | None


def _asignaciones() -> Select[Any]:
    return (
        select(
            AsignacionCampoCuenta,
            CuentaCanonica.cuenta_id,
            CuentaCanonica.cliente_unico,
            BaseOperativa.base_id,
            BaseOperativa.codigo,
            GestorCampo.gestor_id,
            GestorCampo.codigo,
            UnidadTerritorialCampo.cvegeo,
        )
        .join(CuentaCanonica, CuentaCanonica.id == AsignacionCampoCuenta.cuenta_canonica_id)
        .outerjoin(BaseOperativa, BaseOperativa.id == AsignacionCampoCuenta.base_operativa_id)
        .outerjoin(GestorCampo, GestorCampo.id == AsignacionCampoCuenta.gestor_campo_id)
        .outerjoin(
            UnidadTerritorialCampo,
            (
                UnidadTerritorialCampo.ejecucion_campo_v2_id
                == AsignacionCampoCuenta.ejecucion_campo_v2_id
            )
            & (UnidadTerritorialCampo.unidad_id == AsignacionCampoCuenta.subzona_id),
        )
    )


@dataclass(frozen=True)
class Filtros:
    estado: str | None = None
    base: str | None = None
    gestor: str | None = None
    zona_id: UUID | None = None
    subzona_id: UUID | None = None
    tramo_id: UUID | None = None
    microtramo_id: UUID | None = None
    prioridad: str | None = None


_COLUMNA_DEL_NIVEL = {
    NivelUnidad.ZONA: AsignacionCampoCuenta.zona_id,
    NivelUnidad.SUBZONA: AsignacionCampoCuenta.subzona_id,
    NivelUnidad.TRAMO: AsignacionCampoCuenta.tramo_id,
    NivelUnidad.MICROTRAMO: AsignacionCampoCuenta.microtramo_id,
}


def unidad(
    s: Session, ejecucion: EjecucionCampoV2, unidad_id: UUID
) -> UnidadTerritorialCampo | None:
    return s.get(UnidadTerritorialCampo, (ejecucion.id, unidad_id))


def asignaciones(
    s: Session, vista: PlanVista, filtros: Filtros, *, desplazamiento: int, limite: int
) -> tuple[int, list[AsignacionVista]]:
    """Una pagina de las asignaciones del plan, en el orden de la cuenta. Cada filtro entra por su
    indice: el estado, la base, el gestor, la prioridad y el microtramo por los suyos; una zona, una
    subzona o un tramo, por el de la base de su unidad, que es un tramo del indice mas chico. Con un
    solo filtro, el total de un plan publicado sale de lo que conto al publicar."""
    ejecucion = vista.ejecucion
    condiciones: list = [AsignacionCampoCuenta.ejecucion_campo_v2_id == ejecucion.id]
    total: int | None = None
    if filtros.estado is not None:
        condiciones.append(AsignacionCampoCuenta.estado == filtros.estado)
    if filtros.prioridad is not None:
        condiciones.append(AsignacionCampoCuenta.prioridad == filtros.prioridad)
    if filtros.base is not None:
        base = s.exec(
            select(BaseOperativa).where(
                BaseOperativa.version_bases_operativas_id == ejecucion.version_bases_operativas_id,
                BaseOperativa.codigo == filtros.base,
            )
        ).one_or_none()
        if base is None:
            return 0, []
        condiciones.append(AsignacionCampoCuenta.base_operativa_id == base.id)
    if filtros.gestor is not None:
        gestor = s.exec(
            select(GestorCampo).where(
                GestorCampo.version_configuracion_campo_id
                == ejecucion.version_configuracion_campo_id,
                GestorCampo.codigo == filtros.gestor,
            )
        ).one_or_none()
        if gestor is None:
            return 0, []
        condiciones.append(AsignacionCampoCuenta.gestor_campo_id == gestor.id)
    unidades = [
        (nivel, valor)
        for nivel, valor in (
            (NivelUnidad.ZONA, filtros.zona_id),
            (NivelUnidad.SUBZONA, filtros.subzona_id),
            (NivelUnidad.TRAMO, filtros.tramo_id),
            (NivelUnidad.MICROTRAMO, filtros.microtramo_id),
        )
        if valor is not None
    ]
    for nivel, valor in unidades:
        encontrada = unidad(s, ejecucion, valor)
        if encontrada is None or encontrada.nivel != nivel:
            return 0, []
        condiciones.append(_COLUMNA_DEL_NIVEL[nivel] == valor)
        if nivel != NivelUnidad.MICROTRAMO:
            # La base de la unidad acota el recorrido al tramo del indice (plan, base, cuenta).
            condiciones.append(
                AsignacionCampoCuenta.base_operativa_id == encontrada.base_operativa_id
            )
    if ejecucion.estado == EstadoCampoV2.EXITOSA:
        total = _total_publicado(ejecucion, filtros, unidades, s)
    if total is None:
        total = s.exec(
            select(func.count()).select_from(AsignacionCampoCuenta).where(*condiciones)
        ).one()
    filas = s.exec(
        _asignaciones()
        .where(*condiciones)
        .order_by(AsignacionCampoCuenta.cuenta_canonica_id)
        .offset(desplazamiento)
        .limit(limite)
    ).all()
    return total, [AsignacionVista(*fila) for fila in filas]


def _total_publicado(
    ejecucion: EjecucionCampoV2, filtros: Filtros, unidades: list, s: Session
) -> int | None:
    """El total de una pagina con a lo mas un filtro, de lo que el plan conto al publicar: contar
    las decenas de miles de asignaciones de una base en cada pagina no hace falta."""
    activos = [
        f
        for f in (filtros.estado, filtros.base, filtros.gestor, filtros.prioridad)
        if f is not None
    ]
    if len(activos) + len(unidades) > 1:
        return None
    if not activos and not unidades:
        return ejecucion.candidatas_campo
    if filtros.estado is not None:
        return getattr(ejecucion, CONTEO_DE_ESTADO[EstadoCampo(filtros.estado)])
    if filtros.base is not None:
        por_base = (ejecucion.distribucion or {}).get("por_base", {})
        return (por_base.get(filtros.base) or {}).get("candidatas", 0)
    if filtros.gestor is not None:
        fila = s.exec(
            select(CargaGestorCampo.asignadas)
            .join(GestorCampo, GestorCampo.id == CargaGestorCampo.gestor_campo_id)
            .where(
                CargaGestorCampo.ejecucion_campo_v2_id == ejecucion.id,
                GestorCampo.codigo == filtros.gestor,
            )
        ).one_or_none()
        return fila or 0
    if unidades:
        nivel, valor = unidades[0]
        return unidad(s, ejecucion, valor).candidatas
    return None


# --- las cargas y las unidades --------------------------------------------------------------------


@dataclass(frozen=True)
class CargaVista:
    carga: CargaGestorCampo
    gestor_id: UUID
    codigo_gestor: str
    base_id: UUID
    codigo_base: str


def cargas(
    s: Session, vista: PlanVista, *, base: str | None, desplazamiento: int, limite: int
) -> tuple[int, list[CargaVista]]:
    """La carga de cada gestor de la configuracion en el plan, en el orden del codigo del gestor."""
    condiciones = [CargaGestorCampo.ejecucion_campo_v2_id == vista.ejecucion.id]
    if base is not None:
        condiciones.append(BaseOperativa.codigo == base)
    consulta = (
        select(
            CargaGestorCampo,
            GestorCampo.gestor_id,
            GestorCampo.codigo,
            BaseOperativa.base_id,
            BaseOperativa.codigo,
        )
        .join(GestorCampo, GestorCampo.id == CargaGestorCampo.gestor_campo_id)
        .join(BaseOperativa, BaseOperativa.id == CargaGestorCampo.base_operativa_id)
        .where(*condiciones)
    )
    total = s.exec(
        select(func.count())
        .select_from(CargaGestorCampo)
        .join(BaseOperativa, BaseOperativa.id == CargaGestorCampo.base_operativa_id)
        .where(*condiciones)
    ).one()
    filas = s.exec(consulta.order_by(GestorCampo.codigo).offset(desplazamiento).limit(limite)).all()
    return total, [CargaVista(*fila) for fila in filas]


@dataclass(frozen=True)
class UnidadVista:
    unidad: UnidadTerritorialCampo
    base_id: UUID
    codigo_base: str
    codigo_propietario: str | None


def unidades(
    s: Session,
    vista: PlanVista,
    *,
    nivel: str | None,
    padre_id: UUID | None,
    base: str | None,
    desplazamiento: int,
    limite: int,
) -> tuple[int, list[UnidadVista]]:
    """Las unidades que ocupo el plan, en el orden de su identificador: con `padre_id`, las hijas
    de una unidad (por su indice); con `nivel` o `base`, las de ese nivel o esa base."""
    condiciones = [UnidadTerritorialCampo.ejecucion_campo_v2_id == vista.ejecucion.id]
    if nivel is not None:
        condiciones.append(UnidadTerritorialCampo.nivel == nivel)
    if padre_id is not None:
        condiciones.append(UnidadTerritorialCampo.padre_id == padre_id)
    if base is not None:
        condiciones.append(BaseOperativa.codigo == base)
    total = s.exec(
        select(func.count())
        .select_from(UnidadTerritorialCampo)
        .join(BaseOperativa, BaseOperativa.id == UnidadTerritorialCampo.base_operativa_id)
        .where(*condiciones)
    ).one()
    filas = s.exec(
        select(
            UnidadTerritorialCampo, BaseOperativa.base_id, BaseOperativa.codigo, GestorCampo.codigo
        )
        .join(BaseOperativa, BaseOperativa.id == UnidadTerritorialCampo.base_operativa_id)
        .outerjoin(
            GestorCampo, GestorCampo.id == UnidadTerritorialCampo.propietario_gestor_campo_id
        )
        .where(*condiciones)
        .order_by(UnidadTerritorialCampo.unidad_id)
        .offset(desplazamiento)
        .limit(limite)
    ).all()
    return total, [UnidadVista(*fila) for fila in filas]


# --- las de una cuenta ----------------------------------------------------------------------------


@dataclass(frozen=True)
class AsignacionEnHistoria:
    vista: AsignacionVista
    ejecucion: EjecucionCampoV2
    corte_id: UUID
    fecha_corte: date
    configuracion_campo_version_id: UUID


def _de_la_cuenta(cuenta: CuentaCanonica) -> Select[Any]:
    return (
        _asignaciones()
        .add_columns(
            EjecucionCampoV2,
            CorteCanonico.corte_id,
            CorteCanonico.fecha_corte,
            VersionConfiguracionCampo.configuracion_campo_version_id,
        )
        .join(EjecucionCampoV2, EjecucionCampoV2.id == AsignacionCampoCuenta.ejecucion_campo_v2_id)
        .join(CorteCanonico, CorteCanonico.id == EjecucionCampoV2.corte_canonico_id)
        .join(
            VersionConfiguracionCampo,
            VersionConfiguracionCampo.id == EjecucionCampoV2.version_configuracion_campo_id,
        )
        .where(
            AsignacionCampoCuenta.cuenta_canonica_id == cuenta.id,
            EjecucionCampoV2.estado == EstadoCampoV2.EXITOSA,
        )
    )


def _en_historia(fila) -> AsignacionEnHistoria:
    return AsignacionEnHistoria(AsignacionVista(*fila[:8]), *fila[8:])


def historial(
    s: Session, cuenta: CuentaCanonica, *, desplazamiento: int, limite: int
) -> tuple[int, list[AsignacionEnHistoria]]:
    """Los planes EXITOSA en que la cuenta fue candidata de campo, de la fecha de operacion mas
    reciente a la mas antigua; en la misma fecha, el plan mas reciente primero. Ninguno se
    sobrescribe."""
    total = s.exec(
        select(func.count())
        .select_from(AsignacionCampoCuenta)
        .join(EjecucionCampoV2, EjecucionCampoV2.id == AsignacionCampoCuenta.ejecucion_campo_v2_id)
        .where(
            AsignacionCampoCuenta.cuenta_canonica_id == cuenta.id,
            EjecucionCampoV2.estado == EstadoCampoV2.EXITOSA,
        )
    ).one()
    filas = s.exec(
        _de_la_cuenta(cuenta)
        .order_by(EjecucionCampoV2.fecha_operacion.desc(), EjecucionCampoV2.id.desc())
        .offset(desplazamiento)
        .limit(limite)
    ).all()
    return total, [_en_historia(fila) for fila in filas]


def de_plan(s: Session, cuenta: CuentaCanonica, ejecucion_id: int) -> AsignacionEnHistoria | None:
    """La asignacion de la cuenta en ese plan EXITOSA, si fue su candidata: la que declara un
    contexto de la app (app/v1), en lugar de la ultima. Por (plan, cuenta)."""
    fila = s.exec(
        _de_la_cuenta(cuenta).where(AsignacionCampoCuenta.ejecucion_campo_v2_id == ejecucion_id)
    ).one_or_none()
    return None if fila is None else _en_historia(fila)


def ultima(
    s: Session, cuenta: CuentaCanonica, al: date | None = None
) -> AsignacionEnHistoria | None:
    """La asignacion del plan EXITOSA mas reciente de la cuenta (con `al`, el de fecha de operacion
    hasta ese dia): la que resume la Cuenta 360."""
    consulta = _de_la_cuenta(cuenta)
    if al is not None:
        consulta = consulta.where(EjecucionCampoV2.fecha_operacion <= al)
    fila = s.exec(
        consulta.order_by(
            EjecucionCampoV2.fecha_operacion.desc(), EjecucionCampoV2.id.desc()
        ).limit(1)
    ).one_or_none()
    return None if fila is None else _en_historia(fila)
