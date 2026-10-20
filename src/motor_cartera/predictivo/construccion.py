"""Construir las features (y los labels) de un corte con sus relojes, en una transaccion.

Lo usan, con el mismo codigo, el dataset de un entrenamiento (un ancla a la vez), un scoring (su
corte) y la explicacion de una cuenta (el universo restringido a ella). Antes de las features se
decide, con lo que se conocia a K:

- la cobertura de pagos de cada dia de la ventana de 90 dias, con la regla de analytics/v1
  (`analytics.cobertura`): un dia tiene cobertura si cae en el alcance de algun archivo de pagos
  conocido a K y la interpretacion vigente a K de su mes clasifico cada pago observado conocido de
  ese mes. Una ventana sin cobertura completa deja sus features NULL, nunca cero;
- la evaluacion de promesas: la EXITOSA de la cartera, en la zona, con as_of hasta T y terminada
  hasta K, la de as_of mas reciente. Sin ninguna, sus features son NULL. Una evaluacion terminada
  despues de K no existe para estas features, aunque sea de un as_of anterior.

Todo vive en tablas temporales ON COMMIT DROP: quien llama confirma (o revierte) cuando termino de
leer. Lo que se lee sale por COPY en CSV a una tabla de Arrow, sin una consulta ni un objeto de
Python por cuenta.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
from sqlalchemy import text
from sqlmodel import Session

from motor_cartera.analytics import cobertura
from motor_cartera.evaluacion.reglas import VERSION_EVALUACION
from motor_cartera.invariantes import presente
from motor_cartera.motor_pagos.reglas import VERSION_MOTOR_PAGOS
from motor_cartera.predictivo import sql
from motor_cartera.predictivo.features import CATEGORICAS, FEATURES, NUMERICAS, Tipo

VENTANAS = (30, 90)


@dataclass(frozen=True)
class Relojes:
    """Lo que fija las features de un corte: el corte, su cartera, el dia de la prediccion, la
    frontera de conocimiento y la zona del dia logico."""

    corte_canonico_id: int
    despacho_id: str
    cartera_id: str
    as_of: date
    known_at: datetime
    zona: str


@dataclass(frozen=True)
class Disponibilidad:
    """Que se conocia a K: la cobertura de pagos de cada ventana y la evaluacion que se uso."""

    pagos: dict[int, bool]
    evaluacion_id: int | None
    evaluacion_run_id: UUID | None
    evaluacion_as_of: date | None
    meses: list[dict] = field(default_factory=list)
    dias_sin_cobertura: int = 0

    @classmethod
    def desde_definicion(cls, s: Session, datos: dict) -> Disponibilidad:
        """La disponibilidad que guardo un scoring, con el id interno de su evaluacion."""
        evaluacion = datos.get("evaluacion_promesas")
        fila = (
            None
            if evaluacion is None
            else s.execute(
                text(
                    "SELECT id, evaluacion_run_id, as_of FROM ejecucion_evaluacion_promesas "
                    "WHERE evaluacion_run_id = :e"
                ),
                {"e": evaluacion["evaluacion_run_id"]},
            ).one()
        )
        return cls(
            pagos={n: bool(datos["pagos"][f"{n}d"]) for n in VENTANAS},
            evaluacion_id=None if fila is None else fila.id,
            evaluacion_run_id=None if fila is None else fila.evaluacion_run_id,
            evaluacion_as_of=None if fila is None else fila.as_of,
            meses=datos.get("meses", []),
            dias_sin_cobertura=datos.get("dias_sin_cobertura_90d", 0),
        )

    def definicion(self) -> dict:
        return {
            "pagos": {f"{n}d": self.pagos[n] for n in VENTANAS},
            "dias_sin_cobertura_90d": self.dias_sin_cobertura,
            "meses": self.meses,
            "evaluacion_promesas": (
                None
                if self.evaluacion_id is None
                else {
                    "evaluacion_run_id": str(self.evaluacion_run_id),
                    "as_of": presente(
                        self.evaluacion_as_of, "el as_of de la evaluacion de promesas"
                    ).isoformat(),
                }
            ),
        }


def limites(s: Session, as_of: date, zona: str) -> dict[str, Any]:
    """Los instantes del dia logico y de cada ventana, con la base de zonas de PostgreSQL: el final
    de T, el inicio de las ventanas de 30 y 90 dias que terminan en T, y el final de las ventanas
    de los labels (T+14 y T+30); y las mismas horas locales sin zona, como las de pagos/v1."""
    locales = {
        "fin": datetime.combine(as_of + timedelta(days=1), time()),
        "inicio_30d": datetime.combine(as_of - timedelta(days=29), time()),
        "inicio_90d": datetime.combine(as_of - timedelta(days=89), time()),
        "fin_14": datetime.combine(as_of + timedelta(days=15), time()),
        "fin_30": datetime.combine(as_of + timedelta(days=31), time()),
    }
    nombres = list(locales)
    fila = s.execute(
        text("SELECT " + ", ".join(f"timezone(:zona, CAST(:{n} AS timestamp))" for n in nombres)),
        {"zona": zona, **locales},
    ).one()
    resultado: dict[str, Any] = dict(zip(nombres, fila, strict=True))
    resultado.update({f"{n}_local": v for n, v in locales.items()})
    return resultado


def parametros(relojes: Relojes, tiempos: dict[str, Any], *, cuenta: int | None) -> dict:
    return {
        "corte": relojes.corte_canonico_id,
        "despacho": relojes.despacho_id,
        "cartera": relojes.cartera_id,
        "as_of": relojes.as_of,
        "known_at": relojes.known_at,
        "zona": relojes.zona,
        "cuenta": cuenta,
        "version_motor_pagos": VERSION_MOTOR_PAGOS,
        **tiempos,
    }


def _ejecutar(s: Session, sentencias: tuple[str, ...], p: dict) -> None:
    for sentencia in sentencias:
        s.execute(text(sentencia), p)


def preparar_features(
    s: Session,
    relojes: Relojes,
    *,
    cuenta: int | None = None,
    disponibilidad: Disponibilidad | None = None,
) -> Disponibilidad:
    """Arma pf_feature en la transaccion de `s`: una fila por cuenta del corte (o solo `cuenta`),
    con cada feature y su feature_hash. Devuelve lo que se conocia a K. Con `disponibilidad` (la
    que ya calculo un scoring con los mismos relojes) no la vuelve a calcular."""
    tiempos = limites(s, relojes.as_of, relojes.zona)
    p = parametros(relojes, tiempos, cuenta=cuenta)
    _ejecutar(s, sql.sql_universo(una_cuenta=cuenta is not None), p)
    _ejecutar(s, sql.SQL_HISTORIA, p)
    _ejecutar(s, sql.sql_pagos(una_cuenta=cuenta is not None), p)
    if disponibilidad is None:
        disponibilidad = _disponibilidad(s, relojes, p)
    p.update(
        {
            "pagos_30d": disponibilidad.pagos[30],
            "pagos_90d": disponibilidad.pagos[90],
            "evaluacion": disponibilidad.evaluacion_id,
            "con_evaluacion": disponibilidad.evaluacion_id is not None,
        }
    )
    _ejecutar(s, sql.SQL_LIFECYCLE, p)
    _ejecutar(s, sql.SQL_FEATURES, p)
    return disponibilidad


def _disponibilidad(s: Session, relojes: Relojes, p: dict) -> Disponibilidad:
    _ejecutar(s, sql.SQL_DATASETS_DE_PAGOS, p)
    desde = relojes.as_of - timedelta(days=89)
    alcances = [
        (a, b)
        for a, b in s.execute(
            text(sql.SQL_ALCANCES),
            {**p, "desde_alcance": datetime.combine(desde - timedelta(days=400), time())},
        ).all()
    ]
    primer_mes = date(desde.year, desde.month, 1)
    meses = [
        cobertura.Mes(mes, observados, clasificados)
        for mes, observados, clasificados in s.execute(
            text(sql.SQL_MESES), {**p, "primer_mes": datetime.combine(primer_mes, time())}
        ).all()
    ]
    interpretados = {
        m for (m,) in s.execute(text("SELECT periodo_desde FROM pf_interpretacion")).all()
    }
    dias = cobertura.por_dia(
        desde,
        relojes.as_of,
        pagos_declarados=True,
        alcances=alcances,
        meses=meses,
        interpretados=interpretados,
        atribuidos=set(),
    )
    pagos = {
        n: all(dias[relojes.as_of - timedelta(days=k)].pagos is None for k in range(n))
        for n in VENTANAS
    }
    evaluacion = s.execute(
        text(
            "SELECT id, evaluacion_run_id, as_of FROM ejecucion_evaluacion_promesas "
            "WHERE despacho_id = :despacho AND cartera_id = :cartera "
            "AND version_evaluacion = :version AND estado = 'EXITOSA' AND zona_horaria = :zona "
            "AND as_of <= :as_of AND terminada_en <= :known_at "
            "ORDER BY as_of DESC, terminada_en DESC, id DESC LIMIT 1"
        ),
        {**p, "version": VERSION_EVALUACION},
    ).one_or_none()
    return Disponibilidad(
        pagos=pagos,
        evaluacion_id=None if evaluacion is None else evaluacion.id,
        evaluacion_run_id=None if evaluacion is None else evaluacion.evaluacion_run_id,
        evaluacion_as_of=None if evaluacion is None else evaluacion.as_of,
        meses=[
            {
                "mes": m.mes.isoformat(),
                "pagos_observados": m.observados,
                "clasificados": m.clasificados,
                "interpretado": m.mes in interpretados,
            }
            for m in meses
        ],
        dias_sin_cobertura=sum(1 for d in dias.values() if d.pagos is not None),
    )


def preparar_universo(s: Session, relojes: Relojes) -> None:
    """Solo pf_universo: las cuentas del corte, para construir sus labels sin sus features."""
    tiempos = limites(s, relojes.as_of, relojes.zona)
    _ejecutar(s, sql.sql_universo(una_cuenta=False), parametros(relojes, tiempos, cuenta=None))


def preparar_labels(
    s: Session,
    relojes: Relojes,
    *,
    label_known_at: datetime,
    interpretaciones: list[int],
) -> None:
    """Arma pl_mov, pl_contacto y pl_promesa sobre pf_universo: los labels crudos en (T, T+H], con
    las interpretaciones declaradas y lo registrado hasta label_known_at."""
    tiempos = limites(s, relojes.as_of, relojes.zona)
    p = parametros(relojes, tiempos, cuenta=None)
    p.update({"label_known_at": label_known_at, "interpretaciones": interpretaciones})
    _ejecutar(s, sql.SQL_LABELS, p)


# --- de la base a Arrow y a NumPy -----------------------------------------------------------------

NULO_COPY = "\\N"
"""Como sale un NULL del COPY: distinto del texto vacio, que sale vacio."""

TIPOS_FEATURES: dict[str, pa.DataType] = {
    f.nombre: pa.string() if f.tipo == Tipo.TEXTO else pa.float64() for f in FEATURES
}
"""Como se lee cada feature del CSV: las numericas como float64 (el texto decimal de la base,
redondeado una vez y correctamente, igual que float() de Python), las categoricas como texto."""


def copiar(s: Session, consulta: str) -> bytes:
    """El resultado de una consulta sin parametros, por COPY ... TO STDOUT en CSV con encabezado y
    NULO_COPY: ningun valor pasa por un objeto de Python por fila. Los bytes son deterministas para
    los mismos valores en el mismo orden: el hash del dataset los lee tal cual."""
    conexion = presente(s.connection().connection.driver_connection, "la conexion de la sesion")
    partes: list[bytes] = []
    with conexion.cursor() as cursor:
        with cursor.copy(
            f"COPY ({consulta}) TO STDOUT WITH (FORMAT csv, HEADER true, NULL '{NULO_COPY}')"
        ) as copia:
            for bloque in copia:
                partes.append(bytes(bloque))
    return b"".join(partes)


def tabla(csv: bytes, tipos: dict[str, pa.DataType]) -> pa.Table:
    """El CSV de `copiar` como una tabla de Arrow con los tipos declarados."""
    return pacsv.read_csv(
        pa.BufferReader(csv),
        convert_options=pacsv.ConvertOptions(
            column_types=tipos,
            null_values=[NULO_COPY],
            strings_can_be_null=True,
            quoted_strings_can_be_null=False,
        ),
    )


def flotantes(columna: pa.ChunkedArray) -> np.ndarray:
    """Una columna numerica como float64; un NULL es NaN."""
    return np.asarray(
        pc.fill_null(pc.cast(columna, pa.float64()), np.nan).to_numpy(), dtype=np.float64
    )


def numericas(t: pa.Table) -> np.ndarray:
    """n x p, float64, en el orden de NUMERICAS; un NULL es NaN."""
    if t.num_rows == 0:
        return np.zeros((0, len(NUMERICAS)), dtype=np.float64)
    return np.column_stack([flotantes(t.column(f.nombre)) for f in NUMERICAS])


def diccionario(columna: pa.ChunkedArray) -> tuple[np.ndarray, list[str]]:
    """Una columna de texto como (indices, valores): -1 es NULL."""
    codificada = columna.combine_chunks().dictionary_encode()
    indices = pc.fill_null(codificada.indices, -1).to_numpy(zero_copy_only=False)
    return np.asarray(indices, dtype=np.int32), codificada.dictionary.to_pylist()


def categoricas(t: pa.Table) -> np.ndarray:
    """n x q, objetos (un texto o None), en el orden de CATEGORICAS. Cada valor distinto es un solo
    objeto de Python, compartido por todas sus filas."""
    resultado = np.empty((t.num_rows, len(CATEGORICAS)), dtype=object)
    for k, f in enumerate(CATEGORICAS):
        indices, valores = diccionario(t.column(f.nombre))
        tabla_de_valores = np.array([*valores, None], dtype=object)
        resultado[:, k] = tabla_de_valores[indices]
    return resultado


# --- las filas de un scoring ----------------------------------------------------------------------


@dataclass
class Filas:
    """Las filas de un corte, en el orden canonico (el de cuenta_id, un UUID): la entrada del
    modelo."""

    cuenta: np.ndarray
    """El id interno de cada cuenta (int64): solo para publicar su prediccion."""
    cuenta_id: list[str]
    """El cuenta_id publico de cada cuenta: el orden canonico."""
    numericas: np.ndarray
    """n x p, float64, NaN es NULL, en el orden de NUMERICAS."""
    categoricas: np.ndarray
    """n x q, objetos (texto o None), en el orden de CATEGORICAS."""
    feature_hash: list[bytes]

    def __len__(self) -> int:
        return len(self.cuenta)


TIPOS_SCORING: dict[str, pa.DataType] = {
    "cuenta": pa.int64(),
    "cuenta_id": pa.string(),
    **TIPOS_FEATURES,
    "feature_hash": pa.string(),
}


def filas(csv: bytes) -> Filas:
    t = tabla(csv, TIPOS_SCORING)
    return Filas(
        cuenta=np.asarray(t.column("cuenta").to_numpy(), dtype=np.int64),
        cuenta_id=t.column("cuenta_id").to_pylist(),
        numericas=numericas(t),
        categoricas=categoricas(t),
        feature_hash=[bytes.fromhex(h) for h in t.column("feature_hash").to_pylist()],
    )


def arreglos_de_python(
    valores: Mapping[str, Decimal | int | str | None],
) -> tuple[np.ndarray, np.ndarray]:
    """Las features de una cuenta leidas como valores de Python (Decimal, int, str o None) como la
    entrada del runtime (1 x p y 1 x q): cada Decimal a float64 con el mismo redondeo correcto que
    el CSV de un scoring."""
    numericas_ = np.array(
        [[np.nan if (v := valores[f.nombre]) is None else float(v) for f in NUMERICAS]],
        dtype=np.float64,
    )
    categoricas_ = np.empty((1, len(CATEGORICAS)), dtype=object)
    for k, f in enumerate(CATEGORICAS):
        categoricas_[0, k] = valores[f.nombre]
    return numericas_, categoricas_
