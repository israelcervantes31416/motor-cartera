"""El universo y la elegibilidad de una ejecucion de optimization/v1, por conjuntos, en PostgreSQL.

Nada se lee cuenta por cuenta: el universo es un JOIN de las predicciones del scoring declarado con
las decisiones de la decision/v2 declarada y con los snapshots de su corte (y, con un ruteo, con sus
paradas y con la territorial/v2 de su cadena), en una tabla temporal; los pares elegibles, otro JOIN
de ese universo con la matriz de admisibilidad y los pools activos, por igualdad en cada alcance.
Python recibe dos COPY: las cuentas en orden canonico (cuenta_id) y los pares (cuenta, pool).

Todo vive en tablas temporales ON COMMIT DROP de una transaccion de lectura: se arma, se copia y se
revierte. Ninguna tabla guarda una coordenada, y nada de esto escribe en una tabla del sistema.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pyarrow as pa
from sqlalchemy import text
from sqlmodel import Session

from motor_cartera.atraso import TRAMOS_ATRASO
from motor_cartera.db.copia import copiar
from motor_cartera.predictivo import construccion


@dataclass(frozen=True)
class Entradas:
    """Lo declarado por una ejecucion, con sus ids internos."""

    scoring: int
    decision: int
    corte: int
    configuracion: int
    ruteo: int | None
    territorial: int | None
    configuracion_campo: int | None
    bases: int | None
    permitir_imputacion: bool


def _tramo() -> str:
    """El tramo de atraso del snapshot, de la misma tabla de fronteras que el resto del sistema."""
    casos = []
    for etiqueta, desde, hasta in TRAMOS_ATRASO:
        condicion = (
            f"sn.dias_atraso >= {desde}"
            if hasta is None
            else f"sn.dias_atraso BETWEEN {desde} AND {hasta}"
        )
        casos.append(f"WHEN {condicion} THEN '{etiqueta}'")
    return "CASE " + " ".join(casos) + " END"


UNIVERSO = f"""
CREATE TEMP TABLE op_universo ON COMMIT DROP AS
SELECT (row_number() OVER (ORDER BY c.cuenta_id) - 1)::bigint AS i,
       p.cuenta_canonica_id AS cuenta,
       c.cuenta_id::text AS cuenta_id,
       (p.recuperacion_positiva_esperada_30d * 100)::bigint AS valor,
       (p.p_contacto_14d * 100000000)::bigint AS contacto,
       p.estado = 'CON_IMPUTACION' AS imputada,
       d.canal_recomendado AS canal_decision,
       d.prioridad_final AS prioridad,
       sn.producto,
       {_tramo()} AS atraso,
       t.base_operativa_id AS base,
       b.codigo AS base_codigo,
       x.estado AS estado_parada,
       x.gestor_campo_id AS gestor,
       g.codigo AS gestor_codigo,
       x.orden_visita
FROM prediccion_cuenta p
JOIN resultado_decision_v2 d
  ON d.ejecucion_decision_v2_id = :decision AND d.cuenta_canonica_id = p.cuenta_canonica_id
JOIN snapshot_cuenta sn
  ON sn.corte_canonico_id = :corte AND sn.cuenta_canonica_id = p.cuenta_canonica_id
JOIN cuenta_canonica c ON c.id = p.cuenta_canonica_id
LEFT JOIN parada_ruta_v2 x
  ON x.ejecucion_ruteo_v2_id = :ruteo AND x.cuenta_canonica_id = p.cuenta_canonica_id
LEFT JOIN gestor_campo g ON g.id = x.gestor_campo_id
LEFT JOIN asignacion_territorial_v2 t
  ON t.ejecucion_territorial_v2_id = :territorial
 AND t.cuenta_canonica_id = p.cuenta_canonica_id
 AND t.estado_asignacion = 'ASIGNADA'
LEFT JOIN base_operativa b ON b.id = t.base_operativa_id
WHERE p.ejecucion_scoring_predictivo_id = :scoring
"""
"""Una fila por cuenta del corte: su prediccion, su decision y su snapshot (las tres EXITOSA y del
mismo corte, lo garantiza la base); su parada del ruteo y su base territorial, si hay un ruteo. La
recuperacion esperada y el p_contacto pasan a enteros exactos: NUMERIC(14,2) por 100 y
NUMERIC(9,8) por 10^8."""

POOLS = """
CREATE TEMP TABLE op_pool (
    j bigint PRIMARY KEY,
    id integer NOT NULL,
    canal text NOT NULL,
    tipo_alcance text NOT NULL,
    referencia_alcance text
) ON COMMIT DROP
"""
"""Los pools activos de la configuracion, con su lugar canonico (por codigo, ordenado en Python, sin
depender de la colacion de la base)."""

_PARES_DE = """
SELECT u.i, k.j
FROM op_universo u
JOIN admisibilidad_canal_optimizacion a
  ON a.version_configuracion_optimizacion_id = :configuracion
 AND a.canal_decision = u.canal_decision AND a.permitido
JOIN op_pool k ON k.canal = a.canal_operativo AND k.tipo_alcance = '{alcance}' {igualdad}
WHERE (:imputacion OR NOT u.imputada) {condicion}
"""
PARES = (
    "CREATE TEMP TABLE op_par ON COMMIT DROP AS "
    + _PARES_DE.format(alcance="GLOBAL", igualdad="", condicion="")
    + " UNION ALL "
    + _PARES_DE.format(
        alcance="BASE", igualdad="AND k.referencia_alcance = u.base_codigo", condicion=""
    )
    + " UNION ALL "
    + _PARES_DE.format(
        alcance="GESTOR_CAMPO",
        igualdad="AND k.referencia_alcance = u.gestor_codigo",
        condicion="AND u.estado_parada = 'PROGRAMADA'",
    )
)
"""Los pares elegibles (cuenta, pool): el canal del pool es admisible para el canal que decision/v2
le recomendo a la cuenta, la prediccion es COMPLETA o se admiten CON_IMPUTACION, y el alcance la
alcanza, por igualdad (nunca un producto cartesiano de cuentas por pools): GLOBAL, siempre; BASE, si
su base territorial es la del pool; GESTOR_CAMPO, si tiene una parada PROGRAMADA de ese gestor."""

COLUMNAS_UNIVERSO = (
    "i, cuenta, cuenta_id, valor, contacto, imputada::int AS imputada, canal_decision, prioridad, "
    "producto, atraso, base, base_codigo, estado_parada, gestor, gestor_codigo, orden_visita"
)
"""Lo que se copia del universo; un booleano, como 0 o 1 (el CSV de PostgreSQL lo escribe t o f)."""
TIPOS_UNIVERSO = {
    "i": pa.int64(),
    "cuenta": pa.int64(),
    "cuenta_id": pa.string(),
    "valor": pa.int64(),
    "contacto": pa.int64(),
    "imputada": pa.int8(),
    "canal_decision": pa.string(),
    "prioridad": pa.string(),
    "producto": pa.string(),
    "atraso": pa.string(),
    "base": pa.int64(),
    "base_codigo": pa.string(),
    "estado_parada": pa.string(),
    "gestor": pa.int64(),
    "gestor_codigo": pa.string(),
    "orden_visita": pa.int64(),
}


@dataclass(frozen=True)
class Universo:
    """Las cuentas del corte en orden canonico, como arreglos: la cuenta i es la i-esima."""

    cuenta: np.ndarray
    cuenta_id: list[str]
    valor: np.ndarray
    contacto: np.ndarray
    imputada: np.ndarray
    canal_decision: np.ndarray
    prioridad: np.ndarray
    producto: np.ndarray
    atraso: np.ndarray
    base: np.ndarray
    """El id interno de su base territorial, o -1."""
    base_codigo: np.ndarray
    estado_parada: np.ndarray
    gestor: np.ndarray
    """El id interno del gestor de su parada, o -1."""
    gestor_codigo: np.ndarray
    orden_visita: np.ndarray
    """Su orden de visita, o -1."""

    def __len__(self) -> int:
        return len(self.cuenta)


@dataclass(frozen=True)
class PoolActivo:
    j: int
    id: int
    canal: str
    tipo_alcance: str
    referencia_alcance: str | None


def _enteros(t: pa.Table, columna: str) -> np.ndarray:
    """Una columna entera con -1 en lugar de NULL."""
    return np.asarray(t.column(columna).fill_null(-1).to_numpy(), dtype=np.int64)


def _textos(t: pa.Table, columna: str) -> np.ndarray:
    return np.asarray(t.column(columna).to_pylist(), dtype=object)


def preparar(s: Session, e: Entradas, pools: list[PoolActivo]) -> None:
    """Las tablas temporales del universo, de los pools y de los pares, con sus estadisticas, en la
    transaccion de `s`."""
    parametros = {
        "scoring": e.scoring,
        "decision": e.decision,
        "corte": e.corte,
        "ruteo": e.ruteo,
        "territorial": e.territorial,
    }
    s.execute(text(UNIVERSO), parametros)
    s.execute(text(POOLS))
    copiar(
        s,
        "op_pool",
        ("j", "id", "canal", "tipo_alcance", "referencia_alcance"),
        ((p.j, p.id, p.canal, p.tipo_alcance, p.referencia_alcance) for p in pools),
    )
    # Sin ANALYZE, el planificador no sabe cuantas filas tiene una tabla de paso.
    s.execute(text("ANALYZE op_universo"))
    s.execute(text("ANALYZE op_pool"))
    s.execute(
        text(PARES),
        {"configuracion": e.configuracion, "imputacion": e.permitir_imputacion},
    )


def universo(s: Session) -> Universo:
    csv = construccion.copiar(s, f"SELECT {COLUMNAS_UNIVERSO} FROM op_universo ORDER BY i")
    t = construccion.tabla(csv, TIPOS_UNIVERSO)
    del csv
    indice = np.asarray(t.column("i").to_numpy(), dtype=np.int64)
    if not np.array_equal(indice, np.arange(len(indice))):
        raise ValueError("El universo no salio en su orden canonico.")
    return Universo(
        cuenta=np.asarray(t.column("cuenta").to_numpy(), dtype=np.int64),
        cuenta_id=t.column("cuenta_id").to_pylist(),
        valor=np.asarray(t.column("valor").to_numpy(), dtype=np.int64),
        contacto=np.asarray(t.column("contacto").to_numpy(), dtype=np.int64),
        imputada=np.asarray(t.column("imputada").to_numpy(), dtype=bool),
        canal_decision=_textos(t, "canal_decision"),
        prioridad=_textos(t, "prioridad"),
        producto=_textos(t, "producto"),
        atraso=_textos(t, "atraso"),
        base=_enteros(t, "base"),
        base_codigo=_textos(t, "base_codigo"),
        estado_parada=_textos(t, "estado_parada"),
        gestor=_enteros(t, "gestor"),
        gestor_codigo=_textos(t, "gestor_codigo"),
        orden_visita=_enteros(t, "orden_visita"),
    )


def pares(s: Session) -> tuple[np.ndarray, np.ndarray]:
    """Los pares elegibles en orden (cuenta, pool)."""
    csv = construccion.copiar(s, "SELECT i, j FROM op_par ORDER BY i, j")
    t = construccion.tabla(csv, {"i": pa.int64(), "j": pa.int64()})
    return (
        np.asarray(t.column("i").to_numpy(), dtype=np.int64),
        np.asarray(t.column("j").to_numpy(), dtype=np.int64),
    )


def programadas_por_gestor(s: Session, ruteo: int) -> dict[str, int]:
    """Cuantas paradas PROGRAMADA tiene cada gestor (por su codigo) en el ruteo."""
    filas = s.execute(
        text(
            "SELECT g.codigo, count(*) FROM parada_ruta_v2 x JOIN gestor_campo g "
            "ON g.id = x.gestor_campo_id WHERE x.ejecucion_ruteo_v2_id = :ruteo "
            "AND x.estado = 'PROGRAMADA' GROUP BY g.codigo"
        ),
        {"ruteo": ruteo},
    ).all()
    return {codigo: int(n) for codigo, n in filas}


def gestores_de(s: Session, configuracion_campo: int) -> dict[str, int]:
    """Los gestores de una configuracion de campo, por codigo: su id interno."""
    filas = s.execute(
        text("SELECT codigo, id FROM gestor_campo WHERE version_configuracion_campo_id = :c"),
        {"c": configuracion_campo},
    ).all()
    return {codigo: int(i) for codigo, i in filas}


def bases_de(s: Session, bases: int) -> dict[str, int]:
    """Las bases de una version de bases operativas, por codigo: su id interno."""
    filas = s.execute(
        text("SELECT codigo, id FROM base_operativa WHERE version_bases_operativas_id = :v"),
        {"v": bases},
    ).all()
    return {codigo: int(i) for codigo, i in filas}
