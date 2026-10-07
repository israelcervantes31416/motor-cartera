"""Del Parquet conformado a PostgreSQL: que columnas se leen, como se convierten y como se copian.

La historia se carga por lotes y sin objetos ORM: cada lote del Parquet (MC_FILAS_POR_LOTE filas) se
lee con pyarrow, solo con las columnas que hacen falta, se le agregan las columnas que se calculan
(el identificador publico, las claves geograficas, el dataset) y se escribe como CSV con el escritor
de Arrow, en C++; ese texto va directo a un COPY. En memoria nunca hay mas de un lote.

Los tipos no se reinterpretan: cada columna del Parquet ya trae el tipo de su contrato (un importe
es DECIMAL(14, 2), un entero es int64, una fecha es date32, un instante es timestamp sin zona), y la
columna de la base tiene el mismo. El CSV de Arrow es el que PostgreSQL lee: un texto va entre
comillas, y un vacio sin comillas es NULL.

No se lee ningun archivo original: solo el Parquet del dataset conformado.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from uuid import UUID

import pandas as pd
import pyarrow as pa
import pyarrow.csv as pcsv
import pyarrow.parquet as pq
from sqlalchemy.dialects import postgresql

from motor_cartera.contratos.cartera_v2 import CONTRATO_V2
from motor_cartera.contratos.fuente import ContratoFuente
from motor_cartera.contratos.pagos import CONTRATO_PAGOS
from motor_cartera.db.modelos import CuentaCanonica, SnapshotCuenta
from motor_cartera.fuentes.conformado import COLUMNA_FILA, COLUMNA_HOJA
from motor_cartera.fuentes.geografia import resolver
from motor_cartera.historia import identidad

STAGING = "historia_staging"
"""La tabla temporal donde se copia un corte antes de resolver sus cuentas. Es de la sesion y se
borra al terminar la transaccion (ON COMMIT DROP), se confirme o se revierta."""

CONTRATOS = {CONTRATO_V2.version: CONTRATO_V2, CONTRATO_PAGOS.version: CONTRATO_PAGOS}
"""Los contratos cuyos datasets conformados tienen historia: las dos fuentes oficiales."""

GEOGRAFIA = ("ESTADO_CTE", "POBLACION_CTE")
"""Lo que la proyeccion operacional resuelve con el catalogo del INEGI. La historia lo resuelve con
el mismo catalogo, desde el Parquet: no lee las cuentas operacionales."""


class DatosInconsistentes(Exception):
    """El dataset conformado no es lo que su registro dice que es: otro contrato, otras filas, o un
    registro que ya no cumple lo que cumplio al publicarse."""


@dataclass(frozen=True)
class Campo:
    """Una columna que se copia: su nombre en la base y, si sale tal cual, la del Parquet."""

    columna: str
    fuente: str | None = None
    """La columna del Parquet conformado. None si se calcula al cargar."""


CAMPOS_SNAPSHOT: tuple[Campo, ...] = (
    Campo("cliente_unico", "CLIENTE_UNICO"),
    Campo("cuenta_id"),
    Campo("source_row", COLUMNA_FILA),
    Campo("source_sheet", COLUMNA_HOJA),
    Campo("dias_atraso", "DIAS_ATRASO"),
    Campo("atraso_maximo", "ATRASO_MAXIMO"),
    Campo("semanas_atraso", "SEMANAS_ATRASO"),
    Campo("pagos_recibidos", "PAGOS_RECIBIDOS"),
    Campo("fecha_ultimo_pago", "FECHA_ULTIMO_PAGO"),
    Campo("saldo_total", "SALDO_TOTAL"),
    Campo("saldo", "SALDO"),
    Campo("moratorios", "MORATORIOS"),
    Campo("saldo_atrasado", "SALDO ATRASADO"),
    Campo("saldo_requerido", "SALDO REQUERIDO"),
    Campo("pago_normal", "PAGO_NORMAL"),
    Campo("imp_ultimo_pago", "IMP_ULTIMO_PAGO"),
    Campo("monto_plan", "MONTO_PLAN"),
    Campo("monto_promesa_pago", "MONTO_PROMESA_PAGO"),
    Campo("producto", "PRODUCTO"),
    Campo("canal", "CANAL"),
    Campo("cve_entidad"),
    Campo("cve_municipio"),
    Campo("estrategia", "ESTRATEGIA"),
    Campo("estatus_plan", "ESTATUS_PLAN"),
    Campo("estatus_promesa_pago", "ESTATUS_PROMESA_PAGO"),
)
"""Lo que se copia de cada cuenta de un corte, en el orden de la tabla temporal. Son las variables
historicas de SnapshotCuenta, mas el CLIENTE_UNICO y el cuenta_id, que sirven para resolver la
cuenta canonica y no se repiten en el snapshot. Ninguna PII."""

CAMPOS_PAGO: tuple[Campo, ...] = (
    Campo("dataset_conformado_id"),
    Campo("source_row", COLUMNA_FILA),
    Campo("anio", "Año"),
    Campo("semana", "Semana"),
    Campo("dias_de_atraso", "Días_de_Atraso"),
    Campo("semanas_de_atraso", "Semanas_de_Atraso"),
    Campo("fecha_recepcion", "Fecha_Recepción"),
    Campo("fecha_de_gestion", "Fecha_de_Gestion"),
    Campo("porcentaje_comision", "Porcentaje_Comision"),
    Campo("ingesta_pagos_id"),
    Campo("pago_observado_id"),
    Campo("recuperacion_por_gestion", "Recuperación_por_Gestión"),
    Campo("cargos_automaticos", "Cargos_Automáticos"),
    Campo("captacion", "Captación"),
    Campo("cobranza_total", "Cobranza_Total"),
    Campo("monto_comision", "Monto_Comision"),
    Campo("despacho_id"),
    Campo("cartera_id"),
    Campo("cliente_unico", "Cliente_Unico"),
    Campo("territorio", "Territorio"),
    Campo("zona", "Zona"),
    Campo("segmento", "Segmento"),
    Campo("gerencia", "Gerencia"),
    Campo("tipo_cartera", "Tipo_Cartera"),
    Campo("producto", "Producto"),
    Campo("campania", "Campaña"),
    Campo("gestor", "Gestor"),
    Campo("plan_de_pago", "Plan_de_Pago"),
    Campo("concepto_calculo", "Concepto_Cálculo"),
    Campo("source_sheet", COLUMNA_HOJA),
)
"""Cada columna de PagoObservado, en su orden: los 23 campos de pagos/v1 con su nombre interno, y lo
que dice de donde salio cada uno."""


def columnas(campos: Sequence[Campo]) -> tuple[str, ...]:
    return tuple(campo.columna for campo in campos)


def fuentes(campos: Sequence[Campo]) -> tuple[str, ...]:
    return tuple(campo.fuente for campo in campos if campo.fuente is not None)


def crear_staging() -> str:
    """El CREATE TEMP TABLE de la tabla temporal de un corte. Cada columna tiene el tipo que tiene
    en su tabla: el de SnapshotCuenta, y el de CuentaCanonica para el CLIENTE_UNICO y el cuenta_id.
    Asi lo que se copia ya llega con el tipo con que se va a guardar."""
    tipos = {**_tipos(SnapshotCuenta), **_tipos(CuentaCanonica)}
    definiciones = []
    for campo in CAMPOS_SNAPSHOT:
        nulo = "" if _admite_nulo(campo.columna) else " NOT NULL"
        definiciones.append(f"{campo.columna} {tipos[campo.columna]}{nulo}")
    return f"CREATE TEMP TABLE {STAGING} ({', '.join(definiciones)}) ON COMMIT DROP"


def _tipos(modelo) -> dict[str, str]:
    dialecto = postgresql.dialect()
    return {c.name: c.type.compile(dialect=dialecto) for c in modelo.__table__.columns}


def _admite_nulo(columna: str) -> bool:
    tabla = SnapshotCuenta.__table__
    if columna in tabla.columns:
        return tabla.columns[columna].nullable
    return CuentaCanonica.__table__.columns[columna].nullable


def abrir_parquet(ruta, contrato: ContratoFuente, filas: int) -> pq.ParquetFile:
    """El Parquet conformado, si es el que la base dice: el de ese contrato, con esas filas y con
    las columnas que se van a leer. Si no, DatosInconsistentes, sin leer un solo registro."""
    archivo = pq.ParquetFile(ruta)
    metadata = archivo.schema_arrow.metadata or {}
    declarado = metadata.get(b"motor_cartera.contrato", b"").decode()
    if declarado != contrato.version:
        raise DatosInconsistentes(
            f"El Parquet conformado es de {declarado or 'un contrato que no declara'}, no de "
            f"{contrato.version}."
        )
    if archivo.metadata.num_rows != filas:
        raise DatosInconsistentes(
            f"El Parquet conformado tiene {archivo.metadata.num_rows:,} filas y su dataset dice "
            f"{filas:,}."
        )
    necesarias = set(contrato.nombres) | {COLUMNA_FILA, COLUMNA_HOJA}
    faltan = sorted(necesarias - set(archivo.schema_arrow.names))
    if faltan:
        raise DatosInconsistentes(f"Al Parquet conformado le faltan columnas: {', '.join(faltan)}.")
    return archivo


@dataclass(frozen=True)
class Bloque:
    """Un lote convertido: cuantas filas trae y su CSV, listo para el COPY."""

    filas: int
    csv: bytes


def bloques_snapshot(
    archivo: pq.ParquetFile, *, despacho_id: str, cartera_id: str, filas_por_lote: int
) -> Iterator[Bloque]:
    """Cada lote de un corte, con las columnas de CAMPOS_SNAPSHOT."""
    leer = [*fuentes(CAMPOS_SNAPSHOT), *GEOGRAFIA]

    def calcular(lote: pa.RecordBatch) -> dict[str, pa.Array]:
        clientes = lote.column("CLIENTE_UNICO").to_pylist()
        resolucion = resolver(
            pd.Series(lote.column("ESTADO_CTE").to_pylist(), dtype="string"),
            pd.Series(lote.column("POBLACION_CTE").to_pylist(), dtype="string"),
        )
        if resolucion.motivo.notna().any():
            raise DatosInconsistentes(
                "Hay registros del corte cuya geografia ya no se resuelve con el catalogo: el "
                "dataset conformado no es el que publico la corrida, o el catalogo cambio."
            )
        return {
            "cuenta_id": pa.array(
                list(identidad.cuenta_ids(despacho_id, cartera_id, clientes)), pa.string()
            ),
            "cve_entidad": _texto(resolucion.cve_entidad),
            "cve_municipio": _texto(resolucion.cve_municipio),
        }

    yield from _bloques(archivo, CAMPOS_SNAPSHOT, leer, calcular, filas_por_lote)


def bloques_pagos(
    archivo: pq.ParquetFile,
    *,
    dataset_conformado_id: int,
    dataset_id: UUID,
    ingesta_pagos_id: int,
    despacho_id: str,
    cartera_id: str,
    filas_por_lote: int,
) -> Iterator[Bloque]:
    """Cada lote de un dataset de pagos, con todas las columnas de PagoObservado."""

    def calcular(lote: pa.RecordBatch) -> dict[str, pa.Array]:
        n = lote.num_rows
        filas = lote.column(COLUMNA_FILA).to_pylist()
        return {
            "dataset_conformado_id": pa.repeat(pa.scalar(dataset_conformado_id, pa.int64()), n),
            "ingesta_pagos_id": pa.repeat(pa.scalar(ingesta_pagos_id, pa.int64()), n),
            "pago_observado_id": pa.array(
                list(identidad.pago_observado_ids(dataset_id, filas)), pa.string()
            ),
            "despacho_id": pa.repeat(pa.scalar(despacho_id, pa.string()), n),
            "cartera_id": pa.repeat(pa.scalar(cartera_id, pa.string()), n),
        }

    yield from _bloques(archivo, CAMPOS_PAGO, list(fuentes(CAMPOS_PAGO)), calcular, filas_por_lote)


def _bloques(
    archivo: pq.ParquetFile,
    campos: Sequence[Campo],
    leer: Sequence[str],
    calcular: Callable[[pa.RecordBatch], dict[str, pa.Array]],
    filas_por_lote: int,
) -> Iterator[Bloque]:
    opciones = pcsv.WriteOptions(include_header=False)
    for lote in archivo.iter_batches(batch_size=filas_por_lote, columns=list(leer)):
        calculadas = calcular(lote)
        arreglos = [
            lote.column(campo.fuente) if campo.fuente is not None else calculadas[campo.columna]
            for campo in campos
        ]
        tabla = pa.Table.from_arrays(arreglos, names=list(columnas(campos)))
        salida = io.BytesIO()
        pcsv.write_csv(tabla, salida, write_options=opciones)
        yield Bloque(lote.num_rows, salida.getvalue())


def _texto(serie: pd.Series) -> pa.Array:
    return pa.array(serie.astype(object).where(serie.notna(), None).tolist(), pa.string())
