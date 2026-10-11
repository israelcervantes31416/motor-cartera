"""Lo que la API lee de la geografia: las versiones de la referencia, las ejecuciones, lo que
publicaron y la geografia de una cuenta a traves de los cortes.

Cada consulta entra por un indice: una version por su identificador publico; una pagina de
resultados por su llave, por (ejecucion, estado, cuenta) o por (ejecucion, municipio, cuenta); la
geografia de una cuenta por (cuenta, ejecucion). Ninguna recorre los cientos de miles de resultados
de una ejecucion para responder una pagina o una cuenta, y ninguna devuelve coordenadas: la API
general da el estado, el municipio y los motivos.
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
    CorteCanonico,
    CuentaCanonica,
    EjecucionGeografia,
    EstadoGeografia,
    MunicipioGeografico,
    ResultadoGeografiaCuenta,
    TrabajoOrquestacion,
    VersionGeografia,
)
from motor_cartera.geografia.reglas import CONTEO_DE_ESTADO
from motor_cartera.geografia.reglas import EstadoGeografia as Estado


class VersionNoEncontrada(Exception):
    """No hay una version de la referencia con ese identificador."""


class GeografiaNoEncontrada(Exception):
    """No hay una ejecucion de geografia con ese geografia_run_id."""


# --- las versiones de la referencia ---------------------------------------------------------------


def versiones(
    s: Session, *, desplazamiento: int, limite: int
) -> tuple[int, list[VersionGeografia]]:
    """Las versiones importadas, en cualquier estado, la mas reciente primero."""
    total = s.exec(select(func.count()).select_from(VersionGeografia)).one()
    filas = s.exec(
        select(VersionGeografia)
        .order_by(VersionGeografia.id.desc())
        .offset(desplazamiento)
        .limit(limite)
    ).all()
    return total, list(filas)


def version(s: Session, geografia_version_id: UUID) -> VersionGeografia:
    fila = s.exec(
        select(VersionGeografia).where(
            VersionGeografia.geografia_version_id == geografia_version_id
        )
    ).first()
    if fila is None:
        raise VersionNoEncontrada(geografia_version_id)
    return fila


# --- las ejecuciones ------------------------------------------------------------------------------


@dataclass(frozen=True)
class EjecucionVista:
    ejecucion: EjecucionGeografia
    corte_id: UUID
    fecha_corte: date
    version: VersionGeografia
    trabajo_id: UUID | None


def _ejecuciones() -> Select[Any]:
    return (
        select(
            EjecucionGeografia,
            CorteCanonico.corte_id,
            CorteCanonico.fecha_corte,
            VersionGeografia,
            TrabajoOrquestacion.trabajo_id,
        )
        .join(CorteCanonico, CorteCanonico.id == EjecucionGeografia.corte_canonico_id)
        .join(VersionGeografia, VersionGeografia.id == EjecucionGeografia.version_geografia_id)
        .outerjoin(
            TrabajoOrquestacion,
            TrabajoOrquestacion.ejecucion_geografia_id == EjecucionGeografia.id,
        )
    )


def obtener(s: Session, geografia_run_id: UUID) -> EjecucionVista:
    fila = s.exec(
        _ejecuciones().where(EjecucionGeografia.geografia_run_id == geografia_run_id)
    ).first()
    if fila is None:
        raise GeografiaNoEncontrada(geografia_run_id)
    return EjecucionVista(*fila)


def listar(
    s: Session, corte: CorteCanonico | None, *, desplazamiento: int, limite: int
) -> tuple[int, list[EjecucionVista]]:
    """Las ejecuciones, en cualquier estado, la mas reciente primero; con un corte, solo las suyas,
    por el indice (corte, id)."""
    condiciones = [] if corte is None else [EjecucionGeografia.corte_canonico_id == corte.id]
    total = s.exec(select(func.count()).select_from(EjecucionGeografia).where(*condiciones)).one()
    filas = s.exec(
        _ejecuciones()
        .where(*condiciones)
        .order_by(EjecucionGeografia.id.desc())
        .offset(desplazamiento)
        .limit(limite)
    ).all()
    return total, [EjecucionVista(*fila) for fila in filas]


# --- los resultados -------------------------------------------------------------------------------


@dataclass(frozen=True)
class ResultadoVisto:
    resultado: ResultadoGeografiaCuenta
    cuenta_id: UUID
    cliente_unico: str
    cvegeo: str | None
    nombre_oficial: str | None


def municipio_de_version(
    s: Session, version_geografia_id: int, cvegeo: str
) -> MunicipioGeografico | None:
    """Un municipio de una version, por su CVEGEO: por su restriccion unica."""
    return s.exec(
        select(MunicipioGeografico).where(
            MunicipioGeografico.version_geografia_id == version_geografia_id,
            MunicipioGeografico.cvegeo == cvegeo,
        )
    ).first()


def resultados(
    s: Session,
    ejecucion: EjecucionGeografia,
    *,
    estado: str | None,
    cvegeo: str | None,
    desplazamiento: int,
    limite: int,
) -> tuple[int, list[ResultadoVisto]]:
    """Una pagina de lo que publico la ejecucion, en el orden de la cuenta, con un filtro por estado
    o por municipio espacial que entra por su indice. Sin filtros, o solo por estado, el total sale
    de lo que la ejecucion conto al publicar."""
    condiciones: list = [ResultadoGeografiaCuenta.ejecucion_geografia_id == ejecucion.id]
    if estado is not None:
        condiciones.append(ResultadoGeografiaCuenta.estado_geografia == estado)
    if cvegeo is not None:
        municipio = municipio_de_version(s, ejecucion.version_geografia_id, cvegeo)
        if municipio is None:
            return 0, []
        condiciones.append(ResultadoGeografiaCuenta.municipio_geografico_id == municipio.id)
    if cvegeo is None and ejecucion.estado == EstadoGeografia.EXITOSA:
        if estado is None:
            total = ejecucion.cuentas_objetivo
        else:
            total = getattr(ejecucion, CONTEO_DE_ESTADO[Estado(estado)])
    else:
        total = s.exec(
            select(func.count()).select_from(ResultadoGeografiaCuenta).where(*condiciones)
        ).one()
    filas = s.exec(
        select(
            ResultadoGeografiaCuenta,
            CuentaCanonica.cuenta_id,
            CuentaCanonica.cliente_unico,
            MunicipioGeografico.cvegeo,
            MunicipioGeografico.nombre_oficial,
        )
        .join(CuentaCanonica, CuentaCanonica.id == ResultadoGeografiaCuenta.cuenta_canonica_id)
        .outerjoin(
            MunicipioGeografico,
            MunicipioGeografico.id == ResultadoGeografiaCuenta.municipio_geografico_id,
        )
        .where(*condiciones)
        .order_by(ResultadoGeografiaCuenta.cuenta_canonica_id)
        .offset(desplazamiento)
        .limit(limite)
    ).all()
    return total, [ResultadoVisto(*fila) for fila in filas]


# --- la geografia de una cuenta -------------------------------------------------------------------


@dataclass(frozen=True)
class GeografiaEnHistoria:
    ejecucion: EjecucionGeografia
    resultado: ResultadoGeografiaCuenta
    corte_id: UUID
    fecha_corte: date
    version: VersionGeografia
    cvegeo: str | None
    nombre_oficial: str | None


def _de_la_cuenta(cuenta: CuentaCanonica) -> Select[Any]:
    return (
        select(
            EjecucionGeografia,
            ResultadoGeografiaCuenta,
            CorteCanonico.corte_id,
            CorteCanonico.fecha_corte,
            VersionGeografia,
            MunicipioGeografico.cvegeo,
            MunicipioGeografico.nombre_oficial,
        )
        .join(
            ResultadoGeografiaCuenta,
            ResultadoGeografiaCuenta.ejecucion_geografia_id == EjecucionGeografia.id,
        )
        .join(CorteCanonico, CorteCanonico.id == EjecucionGeografia.corte_canonico_id)
        .join(VersionGeografia, VersionGeografia.id == EjecucionGeografia.version_geografia_id)
        .outerjoin(
            MunicipioGeografico,
            MunicipioGeografico.id == ResultadoGeografiaCuenta.municipio_geografico_id,
        )
        .where(
            ResultadoGeografiaCuenta.cuenta_canonica_id == cuenta.id,
            EjecucionGeografia.estado == EstadoGeografia.EXITOSA,
        )
    )


def _orden() -> tuple:
    return (CorteCanonico.fecha_corte.desc(), EjecucionGeografia.id.desc())


def historial(
    s: Session, cuenta: CuentaCanonica, *, desplazamiento: int, limite: int
) -> tuple[int, list[GeografiaEnHistoria]]:
    """La geografia de una cuenta en cada ejecucion EXITOSA, del corte mas reciente al mas antiguo;
    en el mismo corte, la ejecucion mas reciente primero. Entra por (cuenta, ejecucion): una cuenta
    tiene unas pocas. Ninguna se sobrescribe."""
    total = s.exec(
        select(func.count())
        .select_from(ResultadoGeografiaCuenta)
        .join(
            EjecucionGeografia,
            EjecucionGeografia.id == ResultadoGeografiaCuenta.ejecucion_geografia_id,
        )
        .where(
            ResultadoGeografiaCuenta.cuenta_canonica_id == cuenta.id,
            EjecucionGeografia.estado == EstadoGeografia.EXITOSA,
        )
    ).one()
    filas = s.exec(
        _de_la_cuenta(cuenta).order_by(*_orden()).offset(desplazamiento).limit(limite)
    ).all()
    return total, [GeografiaEnHistoria(*fila) for fila in filas]


def vigente(
    s: Session, cuenta: CuentaCanonica, al: date | None = None
) -> GeografiaEnHistoria | None:
    """La geografia vigente de la cuenta para la Cuenta 360: la de la ejecucion EXITOSA mas
    reciente de su corte mas reciente que tenga una (con `al`, de los cortes hasta ese dia)."""
    consulta = _de_la_cuenta(cuenta)
    if al is not None:
        consulta = consulta.where(CorteCanonico.fecha_corte <= al)
    fila = s.exec(consulta.order_by(*_orden()).limit(1)).first()
    return None if fila is None else GeografiaEnHistoria(*fila)
