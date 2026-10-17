"""Lo que la API lee de territorial/v2: las ejecuciones, sus asignaciones y las asignaciones de una
cuenta a traves de los cortes.

Cada consulta entra por un indice: una pagina de asignaciones por su llave, o por (ejecucion, base,
cuenta), (ejecucion, estado, cuenta) o (ejecucion, municipio, cuenta); las de una cuenta por
(cuenta, ejecucion). Ninguna devuelve coordenadas: la base, la distancia, el estado y los motivos.
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
    AsignacionTerritorialV2,
    BaseOperativa,
    CorteCanonico,
    CuentaCanonica,
    EjecucionGeografia,
    EjecucionTerritorialV2,
    EstadoTerritorialV2,
    MunicipioGeografico,
    ResultadoGeografiaCuenta,
    TrabajoOrquestacion,
    VersionBasesOperativas,
)
from motor_cartera.geografia.consultas import municipio_de_version
from motor_cartera.territorial_v2.politica import CONTEO_DE_ESTADO, EstadoAsignacion


class TerritorialV2NoEncontrado(Exception):
    """No hay una ejecucion de territorial/v2 con ese territorial_run_id."""


@dataclass(frozen=True)
class EjecucionVista:
    ejecucion: EjecucionTerritorialV2
    corte_id: UUID
    fecha_corte: date
    geografia: EjecucionGeografia
    bases: VersionBasesOperativas
    trabajo_id: UUID | None


def _ejecuciones() -> Select[Any]:
    return (
        select(
            EjecucionTerritorialV2,
            CorteCanonico.corte_id,
            CorteCanonico.fecha_corte,
            EjecucionGeografia,
            VersionBasesOperativas,
            TrabajoOrquestacion.trabajo_id,
        )
        .join(CorteCanonico, CorteCanonico.id == EjecucionTerritorialV2.corte_canonico_id)
        .join(
            EjecucionGeografia,
            EjecucionGeografia.id == EjecucionTerritorialV2.ejecucion_geografia_id,
        )
        .join(
            VersionBasesOperativas,
            VersionBasesOperativas.id == EjecucionTerritorialV2.version_bases_operativas_id,
        )
        .outerjoin(
            TrabajoOrquestacion,
            TrabajoOrquestacion.ejecucion_territorial_v2_id == EjecucionTerritorialV2.id,
        )
    )


def obtener(s: Session, territorial_run_id: UUID) -> EjecucionVista:
    fila = s.exec(
        _ejecuciones().where(EjecucionTerritorialV2.territorial_run_id == territorial_run_id)
    ).first()
    if fila is None:
        raise TerritorialV2NoEncontrado(territorial_run_id)
    return EjecucionVista(*fila)


def listar(
    s: Session, corte: CorteCanonico | None, *, desplazamiento: int, limite: int
) -> tuple[int, list[EjecucionVista]]:
    condiciones = [] if corte is None else [EjecucionTerritorialV2.corte_canonico_id == corte.id]
    total = s.exec(
        select(func.count()).select_from(EjecucionTerritorialV2).where(*condiciones)
    ).one()
    filas = s.exec(
        _ejecuciones()
        .where(*condiciones)
        .order_by(EjecucionTerritorialV2.id.desc())
        .offset(desplazamiento)
        .limit(limite)
    ).all()
    return total, [EjecucionVista(*fila) for fila in filas]


@dataclass(frozen=True)
class AsignacionVista:
    asignacion: AsignacionTerritorialV2
    cuenta_id: UUID
    cliente_unico: str
    estado_geografia: str
    cvegeo: str | None
    nombre_oficial: str | None
    base_id: UUID | None
    codigo_base: str | None
    nombre_base: str | None


def _asignaciones() -> Select[Any]:
    return (
        select(
            AsignacionTerritorialV2,
            CuentaCanonica.cuenta_id,
            CuentaCanonica.cliente_unico,
            ResultadoGeografiaCuenta.estado_geografia,
            MunicipioGeografico.cvegeo,
            MunicipioGeografico.nombre_oficial,
            BaseOperativa.base_id,
            BaseOperativa.codigo,
            BaseOperativa.nombre,
        )
        .join(CuentaCanonica, CuentaCanonica.id == AsignacionTerritorialV2.cuenta_canonica_id)
        .join(
            ResultadoGeografiaCuenta,
            (
                ResultadoGeografiaCuenta.ejecucion_geografia_id
                == AsignacionTerritorialV2.ejecucion_geografia_id
            )
            & (
                ResultadoGeografiaCuenta.cuenta_canonica_id
                == AsignacionTerritorialV2.cuenta_canonica_id
            ),
        )
        .outerjoin(
            MunicipioGeografico,
            MunicipioGeografico.id == AsignacionTerritorialV2.municipio_geografico_id,
        )
        .outerjoin(BaseOperativa, BaseOperativa.id == AsignacionTerritorialV2.base_operativa_id)
    )


def base_de_version(s: Session, version_bases_id: int, codigo: str) -> BaseOperativa | None:
    return s.exec(
        select(BaseOperativa).where(
            BaseOperativa.version_bases_operativas_id == version_bases_id,
            BaseOperativa.codigo == codigo,
        )
    ).first()


def asignaciones(
    s: Session,
    vista: EjecucionVista,
    *,
    base: str | None,
    cvegeo: str | None,
    estado: str | None,
    desplazamiento: int,
    limite: int,
) -> tuple[int, list[AsignacionVista]]:
    """Una pagina de las asignaciones de la ejecucion, en el orden de la cuenta, con filtros por
    codigo de base, por municipio y por estado, cada uno por su indice."""
    ejecucion = vista.ejecucion
    condiciones: list = [AsignacionTerritorialV2.ejecucion_territorial_v2_id == ejecucion.id]
    if base is not None:
        encontrada = base_de_version(s, ejecucion.version_bases_operativas_id, base)
        if encontrada is None:
            return 0, []
        condiciones.append(AsignacionTerritorialV2.base_operativa_id == encontrada.id)
    if cvegeo is not None:
        municipio = municipio_de_version(s, vista.geografia.version_geografia_id, cvegeo)
        if municipio is None:
            return 0, []
        condiciones.append(AsignacionTerritorialV2.municipio_geografico_id == municipio.id)
    if estado is not None:
        condiciones.append(AsignacionTerritorialV2.estado_asignacion == estado)
    # El total de una ejecucion publicada sale de lo que conto al publicar, que ya no cambia:
    # contar las 200,000 asignaciones de una base en cada pagina costaba 80 ms en el XL.
    if cvegeo is None and ejecucion.estado == EstadoTerritorialV2.EXITOSA:
        if base is None:
            total = (
                ejecucion.cuentas_objetivo
                if estado is None
                else getattr(ejecucion, CONTEO_DE_ESTADO[EstadoAsignacion(estado)])
            )
        elif estado not in (None, EstadoAsignacion.ASIGNADA):
            return 0, []  # solo una ASIGNADA tiene base
        else:
            por_base = (ejecucion.distribucion or {}).get("por_base", {})
            total = (por_base.get(base) or {}).get("cuentas", 0)
    else:
        total = s.exec(
            select(func.count()).select_from(AsignacionTerritorialV2).where(*condiciones)
        ).one()
    filas = s.exec(
        _asignaciones()
        .where(*condiciones)
        .order_by(AsignacionTerritorialV2.cuenta_canonica_id)
        .offset(desplazamiento)
        .limit(limite)
    ).all()
    return total, [AsignacionVista(*fila) for fila in filas]


@dataclass(frozen=True)
class AsignacionEnHistoria:
    ejecucion: EjecucionTerritorialV2
    vista: AsignacionVista
    corte_id: UUID
    fecha_corte: date
    geografia_run_id: UUID
    bases_version_id: UUID


def _de_la_cuenta(cuenta: CuentaCanonica) -> Select[Any]:
    return (
        _asignaciones()
        .add_columns(
            EjecucionTerritorialV2,
            CorteCanonico.corte_id,
            CorteCanonico.fecha_corte,
            EjecucionGeografia.geografia_run_id,
            VersionBasesOperativas.bases_version_id,
        )
        .join(
            EjecucionTerritorialV2,
            EjecucionTerritorialV2.id == AsignacionTerritorialV2.ejecucion_territorial_v2_id,
        )
        .join(CorteCanonico, CorteCanonico.id == EjecucionTerritorialV2.corte_canonico_id)
        .join(
            EjecucionGeografia,
            EjecucionGeografia.id == EjecucionTerritorialV2.ejecucion_geografia_id,
        )
        .join(
            VersionBasesOperativas,
            VersionBasesOperativas.id == EjecucionTerritorialV2.version_bases_operativas_id,
        )
        .where(
            AsignacionTerritorialV2.cuenta_canonica_id == cuenta.id,
            EjecucionTerritorialV2.estado == EstadoTerritorialV2.EXITOSA,
        )
    )


def _en_historia(fila) -> AsignacionEnHistoria:
    vista = AsignacionVista(*fila[:9])
    ejecucion, corte_id, fecha, geografia_run_id, bases_version_id = fila[9:]
    return AsignacionEnHistoria(
        ejecucion, vista, corte_id, fecha, geografia_run_id, bases_version_id
    )


def historial(
    s: Session, cuenta: CuentaCanonica, *, desplazamiento: int, limite: int
) -> tuple[int, list[AsignacionEnHistoria]]:
    """Las asignaciones de una cuenta en cada ejecucion EXITOSA, del corte mas reciente al mas
    antiguo; en el mismo corte, la ejecucion mas reciente primero. Ninguna se sobrescribe."""
    total = s.exec(
        select(func.count())
        .select_from(AsignacionTerritorialV2)
        .join(
            EjecucionTerritorialV2,
            EjecucionTerritorialV2.id == AsignacionTerritorialV2.ejecucion_territorial_v2_id,
        )
        .where(
            AsignacionTerritorialV2.cuenta_canonica_id == cuenta.id,
            EjecucionTerritorialV2.estado == EstadoTerritorialV2.EXITOSA,
        )
    ).one()
    filas = s.exec(
        _de_la_cuenta(cuenta)
        .order_by(CorteCanonico.fecha_corte.desc(), EjecucionTerritorialV2.id.desc())
        .offset(desplazamiento)
        .limit(limite)
    ).all()
    return total, [_en_historia(fila) for fila in filas]


def de_ejecucion(
    s: Session, cuenta: CuentaCanonica, ejecucion_territorial_id: int
) -> AsignacionEnHistoria | None:
    """La asignacion de la cuenta en esa ejecucion EXITOSA, si la tiene: la que declara un contexto
    de la app (app/v1), por el plan de campo/v2 que la leyo."""
    fila = s.exec(
        _de_la_cuenta(cuenta).where(EjecucionTerritorialV2.id == ejecucion_territorial_id)
    ).first()
    return None if fila is None else _en_historia(fila)


def de_geografia(
    s: Session, cuenta: CuentaCanonica, ejecucion_geografia_id: int
) -> AsignacionEnHistoria | None:
    """La asignacion EXITOSA mas reciente de la cuenta sobre esa geografia: la que acompana a la
    geografia vigente en la Cuenta 360."""
    fila = s.exec(
        _de_la_cuenta(cuenta)
        .where(AsignacionTerritorialV2.ejecucion_geografia_id == ejecucion_geografia_id)
        .order_by(EjecucionTerritorialV2.id.desc())
        .limit(1)
    ).first()
    return None if fila is None else _en_historia(fila)
