"""Lo que la API y el CLI leen de predictive/model-v1: los entrenamientos, las versiones del modelo
con sus componentes, los scorings, sus predicciones (filtradas y ordenadas en la base, por pagina),
las predicciones de una cuenta y el drift de un scoring.

Todo se busca por su identificador publico; el id interno no sale de la base. Una prediccion sale
con el cuenta_id de su cuenta, nunca con un dato personal.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy import func, text
from sqlmodel import Session, select

from motor_cartera.db.modelos import (
    ComponenteModeloPredictivo,
    CorteCanonico,
    DriftPredictivo,
    EjecucionEntrenamientoPredictivo,
    EjecucionScoringPredictivo,
    TrabajoOrquestacion,
    VersionModeloPredictivo,
)


class NoEncontrado(Exception):
    """No existe lo que se pidio con ese identificador publico."""


# --- los entrenamientos ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EntrenamientoVista:
    ejecucion: EjecucionEntrenamientoPredictivo
    modelo_version_id: UUID | None
    estado_modelo: str | None
    trabajo_id: UUID | None


def _vista_entrenamiento(s: Session, e: EjecucionEntrenamientoPredictivo) -> EntrenamientoVista:
    version = s.exec(
        select(VersionModeloPredictivo).where(
            VersionModeloPredictivo.ejecucion_entrenamiento_predictivo_id == e.id
        )
    ).one_or_none()
    trabajo = s.exec(
        select(TrabajoOrquestacion.trabajo_id).where(
            TrabajoOrquestacion.ejecucion_entrenamiento_predictivo_id == e.id
        )
    ).one_or_none()
    return EntrenamientoVista(
        e,
        None if version is None else version.modelo_version_id,
        None if version is None else str(version.estado),
        trabajo,
    )


def obtener_entrenamiento(s: Session, training_run_id: UUID) -> EntrenamientoVista:
    e = s.exec(
        select(EjecucionEntrenamientoPredictivo).where(
            EjecucionEntrenamientoPredictivo.training_run_id == training_run_id
        )
    ).one_or_none()
    if e is None:
        raise NoEncontrado(training_run_id)
    return _vista_entrenamiento(s, e)


def listar_entrenamientos(
    s: Session, *, estado: str | None, desplazamiento: int, limite: int
) -> tuple[int, list[EntrenamientoVista]]:
    filtros = [] if estado is None else [EjecucionEntrenamientoPredictivo.estado == estado]
    total = s.exec(
        select(func.count()).select_from(EjecucionEntrenamientoPredictivo).where(*filtros)
    ).one()
    filas = s.exec(
        select(EjecucionEntrenamientoPredictivo)
        .where(*filtros)
        .order_by(EjecucionEntrenamientoPredictivo.id.desc())
        .offset(desplazamiento)
        .limit(limite)
    ).all()
    return total, [_vista_entrenamiento(s, e) for e in filas]


# --- los modelos ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class ModeloVista:
    version: VersionModeloPredictivo
    training_run_id: UUID
    componentes: list[ComponenteModeloPredictivo]


def _vista_modelo(s: Session, v: VersionModeloPredictivo, *, componentes: bool) -> ModeloVista:
    entrenamiento = s.get_one(
        EjecucionEntrenamientoPredictivo, v.ejecucion_entrenamiento_predictivo_id
    )
    return ModeloVista(
        v,
        entrenamiento.training_run_id,
        list(
            s.exec(
                select(ComponenteModeloPredictivo)
                .where(ComponenteModeloPredictivo.version_modelo_predictivo_id == v.id)
                .order_by(ComponenteModeloPredictivo.target)
            ).all()
        )
        if componentes
        else [],
    )


def obtener_modelo(s: Session, modelo_version_id: UUID) -> ModeloVista:
    v = s.exec(
        select(VersionModeloPredictivo).where(
            VersionModeloPredictivo.modelo_version_id == modelo_version_id
        )
    ).one_or_none()
    if v is None:
        raise NoEncontrado(modelo_version_id)
    return _vista_modelo(s, v, componentes=True)


def listar_modelos(
    s: Session, *, estado: str | None, desplazamiento: int, limite: int
) -> tuple[int, list[ModeloVista]]:
    filtros = [] if estado is None else [VersionModeloPredictivo.estado == estado]
    total = s.exec(select(func.count()).select_from(VersionModeloPredictivo).where(*filtros)).one()
    filas = s.exec(
        select(VersionModeloPredictivo)
        .where(*filtros)
        .order_by(VersionModeloPredictivo.creada_en.desc(), VersionModeloPredictivo.id.desc())
        .offset(desplazamiento)
        .limit(limite)
    ).all()
    return total, [_vista_modelo(s, v, componentes=True) for v in filas]


# --- los scorings ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class ScoringVista:
    ejecucion: EjecucionScoringPredictivo
    modelo_version_id: UUID
    corte_id: UUID
    trabajo_id: UUID | None


def _vista_scoring(s: Session, e: EjecucionScoringPredictivo) -> ScoringVista:
    modelo = s.get_one(VersionModeloPredictivo, e.version_modelo_predictivo_id)
    corte = s.get_one(CorteCanonico, e.corte_canonico_id)
    trabajo = s.exec(
        select(TrabajoOrquestacion.trabajo_id).where(
            TrabajoOrquestacion.ejecucion_scoring_predictivo_id == e.id
        )
    ).one_or_none()
    return ScoringVista(e, modelo.modelo_version_id, corte.corte_id, trabajo)


def obtener_scoring(s: Session, scoring_run_id: UUID) -> ScoringVista:
    e = s.exec(
        select(EjecucionScoringPredictivo).where(
            EjecucionScoringPredictivo.scoring_run_id == scoring_run_id
        )
    ).one_or_none()
    if e is None:
        raise NoEncontrado(scoring_run_id)
    return _vista_scoring(s, e)


def listar_scorings(
    s: Session,
    *,
    estado: str | None,
    modelo: VersionModeloPredictivo | None,
    corte: CorteCanonico | None,
    desplazamiento: int,
    limite: int,
) -> tuple[int, list[ScoringVista]]:
    filtros = []
    if estado is not None:
        filtros.append(EjecucionScoringPredictivo.estado == estado)
    if modelo is not None:
        filtros.append(EjecucionScoringPredictivo.version_modelo_predictivo_id == modelo.id)
    if corte is not None:
        filtros.append(EjecucionScoringPredictivo.corte_canonico_id == corte.id)
    total = s.exec(
        select(func.count()).select_from(EjecucionScoringPredictivo).where(*filtros)
    ).one()
    filas = s.exec(
        select(EjecucionScoringPredictivo)
        .where(*filtros)
        .order_by(EjecucionScoringPredictivo.id.desc())
        .offset(desplazamiento)
        .limit(limite)
    ).all()
    return total, [_vista_scoring(s, e) for e in filas]


# --- las predicciones -----------------------------------------------------------------------------


class Orden(StrEnum):
    """Por que se ordena una pagina de predicciones: una lista cerrada, cada una con su indice."""

    CUENTA = "CUENTA"
    P_RECUPERACION_POSITIVA_30D = "P_RECUPERACION_POSITIVA_30D"
    P_CONTACTO_14D = "P_CONTACTO_14D"
    P_PROMESA_30D = "P_PROMESA_30D"
    RIESGO_SIN_RECUPERACION_30D = "RIESGO_SIN_RECUPERACION_30D"
    RECUPERACION_POSITIVA_ESPERADA_30D = "RECUPERACION_POSITIVA_ESPERADA_30D"


class Sentido(StrEnum):
    ASC = "ASC"
    DESC = "DESC"


_COLUMNA_DEL_ORDEN = {
    Orden.CUENTA: ("p.cuenta_canonica_id", False),
    Orden.P_RECUPERACION_POSITIVA_30D: ("p.p_recuperacion_positiva_30d", False),
    Orden.P_CONTACTO_14D: ("p.p_contacto_14d", False),
    Orden.P_PROMESA_30D: ("p.p_promesa_30d", False),
    # El riesgo es el complemento exacto de la recuperacion: su orden es el inverso, con el mismo
    # indice.
    Orden.RIESGO_SIN_RECUPERACION_30D: ("p.p_recuperacion_positiva_30d", True),
    Orden.RECUPERACION_POSITIVA_ESPERADA_30D: ("p.recuperacion_positiva_esperada_30d", False),
}

RANGOS = {
    "p_recuperacion_positiva_30d": ("p.p_recuperacion_positiva_30d", False),
    "p_contacto_14d": ("p.p_contacto_14d", False),
    "p_promesa_30d": ("p.p_promesa_30d", False),
    # Como en el orden: el rango del riesgo es el inverso del de la recuperacion (1 - p, exacto en
    # la base), con su indice.
    "riesgo_sin_recuperacion_30d": ("p.p_recuperacion_positiva_30d", True),
    "recuperacion_positiva_esperada_30d": ("p.recuperacion_positiva_esperada_30d", False),
}
"""Por salida, la columna que filtra su rango y si es la del complemento."""


FILAS_PARA_FILTRAR_PRIMERO = 20_000
"""Con un filtro que deja a lo mas estas predicciones (su conteo exacto ya se hizo), la pagina se
arma primero con ellas, por el indice del filtro, y despues se ordena. Recorrer el orden pedido
hasta juntar la pagina leeria el scoring entero cuando el filtro deja pocas."""


@dataclass(frozen=True)
class FiltrosPredicciones:
    minimos: dict[str, Decimal]
    maximos: dict[str, Decimal]
    producto: str | None = None
    cve_entidad: str | None = None
    estado: str | None = None
    orden: Orden = Orden.CUENTA
    sentido: Sentido = Sentido.ASC


@dataclass(frozen=True)
class PrediccionVista:
    cuenta_id: UUID
    producto: str
    cve_entidad: str
    p_recuperacion_positiva_30d: Decimal
    p_contacto_14d: Decimal
    p_promesa_30d: Decimal
    riesgo_sin_recuperacion_30d: Decimal
    recuperacion_positiva_esperada_30d: Decimal
    estado: str
    motivos: list
    feature_hash: str


def predicciones(
    s: Session,
    ejecucion: EjecucionScoringPredictivo,
    filtros: FiltrosPredicciones,
    *,
    desplazamiento: int,
    limite: int,
) -> tuple[int, list[PrediccionVista]]:
    """Una pagina de las predicciones del scoring, filtradas y ordenadas en la base: los rangos de
    cada salida, producto, entidad y estado, y un orden de la lista cerrada, con cuenta_canonica_id
    para desempatar."""
    condiciones = ["p.ejecucion_scoring_predictivo_id = :ejecucion"]
    parametros: dict = {"ejecucion": ejecucion.id, "corte": ejecucion.corte_canonico_id}
    for nombre, valor in sorted(filtros.minimos.items()):
        columna, inverso = RANGOS[nombre]
        condiciones.append(f"{columna} {'<=' if inverso else '>='} :min_{nombre}")
        parametros[f"min_{nombre}"] = 1 - valor if inverso else valor
    for nombre, valor in sorted(filtros.maximos.items()):
        columna, inverso = RANGOS[nombre]
        condiciones.append(f"{columna} {'>=' if inverso else '<='} :max_{nombre}")
        parametros[f"max_{nombre}"] = 1 - valor if inverso else valor
    if filtros.estado is not None:
        condiciones.append("p.estado = :estado")
        parametros["estado"] = filtros.estado
    unir_snapshot = filtros.producto is not None or filtros.cve_entidad is not None
    if filtros.producto is not None:
        condiciones.append("sn.producto = :producto")
        parametros["producto"] = filtros.producto
    if filtros.cve_entidad is not None:
        condiciones.append("sn.cve_entidad = :cve_entidad")
        parametros["cve_entidad"] = filtros.cve_entidad
    donde = " AND ".join(condiciones)
    snapshot = (
        "JOIN snapshot_cuenta sn ON sn.corte_canonico_id = :corte "
        "AND sn.cuenta_canonica_id = p.cuenta_canonica_id "
    )
    filtrada = len(condiciones) > 1 or unir_snapshot
    # Sin filtros, el total es lo que el scoring publico, todo o nada: no se cuenta otra vez.
    total = (
        s.execute(
            text(
                f"SELECT count(*) FROM prediccion_cuenta p {snapshot if unir_snapshot else ''}"
                f"WHERE {donde}"
            ),
            parametros,
        ).scalar_one()
        if filtrada
        else ejecucion.predicciones_publicadas
    )
    columna, inverso = _COLUMNA_DEL_ORDEN[filtros.orden]
    descendente = (filtros.sentido == Sentido.DESC) != inverso
    direccion = "DESC" if descendente else "ASC"
    orden = f"{columna} {direccion}, p.cuenta_canonica_id {direccion}"
    columnas = (
        "SELECT c.cuenta_id, sn.producto, sn.cve_entidad, p.p_recuperacion_positiva_30d, "
        "p.p_contacto_14d, p.p_promesa_30d, p.riesgo_sin_recuperacion_30d, "
        "p.recuperacion_positiva_esperada_30d, p.estado, p.motivos, "
        "encode(p.feature_hash, 'hex') AS feature_hash "
    )
    if filtrada and total <= FILAS_PARA_FILTRAR_PRIMERO:
        consulta = (
            "WITH elegidas AS MATERIALIZED (SELECT p.* FROM prediccion_cuenta p "
            f"{snapshot if unir_snapshot else ''}WHERE {donde}) "
            + columnas
            + f"FROM (SELECT * FROM elegidas p ORDER BY {orden} "
            "OFFSET :desplazamiento LIMIT :limite) p "
            f"{snapshot}JOIN cuenta_canonica c ON c.id = p.cuenta_canonica_id ORDER BY {orden}"
        )
    else:
        consulta = (
            columnas + f"FROM prediccion_cuenta p {snapshot}"
            "JOIN cuenta_canonica c ON c.id = p.cuenta_canonica_id "
            f"WHERE {donde} ORDER BY {orden} OFFSET :desplazamiento LIMIT :limite"
        )
    filas = s.execute(
        text(consulta), {**parametros, "desplazamiento": desplazamiento, "limite": limite}
    ).all()
    return total, [PrediccionVista(*f) for f in filas]


@dataclass(frozen=True)
class PrediccionDeCuentaVista:
    scoring_run_id: UUID
    modelo_version_id: UUID
    corte_id: UUID
    as_of: date
    known_at: datetime
    p_recuperacion_positiva_30d: Decimal
    p_contacto_14d: Decimal
    p_promesa_30d: Decimal
    riesgo_sin_recuperacion_30d: Decimal
    recuperacion_positiva_esperada_30d: Decimal
    estado: str
    motivos: list
    feature_hash: str


def predicciones_de_cuenta(
    s: Session, cuenta: int, *, desplazamiento: int, limite: int
) -> tuple[int, list[PrediccionDeCuentaVista]]:
    """Las predicciones publicadas de una cuenta, de su scoring mas reciente al mas antiguo."""
    total = s.execute(
        text("SELECT count(*) FROM prediccion_cuenta WHERE cuenta_canonica_id = :cuenta"),
        {"cuenta": cuenta},
    ).scalar_one()
    filas = s.execute(
        text(
            "SELECT e.scoring_run_id, v.modelo_version_id, k.corte_id, e.as_of, e.known_at, "
            "p.p_recuperacion_positiva_30d, p.p_contacto_14d, p.p_promesa_30d, "
            "p.riesgo_sin_recuperacion_30d, p.recuperacion_positiva_esperada_30d, p.estado, "
            "p.motivos, encode(p.feature_hash, 'hex') "
            "FROM prediccion_cuenta p "
            "JOIN ejecucion_scoring_predictivo e ON e.id = p.ejecucion_scoring_predictivo_id "
            "JOIN version_modelo_predictivo v ON v.id = e.version_modelo_predictivo_id "
            "JOIN corte_canonico k ON k.id = e.corte_canonico_id "
            "WHERE p.cuenta_canonica_id = :cuenta "
            "ORDER BY p.ejecucion_scoring_predictivo_id DESC OFFSET :desplazamiento LIMIT :limite"
        ),
        {"cuenta": cuenta, "desplazamiento": desplazamiento, "limite": limite},
    ).all()
    return total, [PrediccionDeCuentaVista(*f) for f in filas]


def drift(s: Session, ejecucion: EjecucionScoringPredictivo) -> list[DriftPredictivo]:
    return list(
        s.exec(
            select(DriftPredictivo)
            .where(DriftPredictivo.ejecucion_scoring_predictivo_id == ejecucion.id)
            .order_by(DriftPredictivo.tipo, DriftPredictivo.psi.desc(), DriftPredictivo.nombre)
        ).all()
    )
