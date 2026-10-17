"""La exportacion de un plan publicado de optimization/v1, en CSV, Parquet o JSONL: sus asignaciones
(una fila por cuenta) o su plan agregado (una fila por pool, o por restriccion).

Las asignaciones salen por conjuntos: un COPY de PostgreSQL a CSV y, para Parquet y JSONL, una
lectura por lotes de ese CSV, sin cargar el plan en memoria ni crear un objeto por cuenta. Solo el
cuenta_id publico y codigos (de pool, de base, de gestor): ningun nombre, direccion, telefono,
CLIENTE_UNICO, actor_ref ni coordenada. Los montos son enteros en centavos y p_contacto un decimal
exacto, nunca un float.
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
from pathlib import Path

import pyarrow as pa

from motor_cartera.db.modelos import EjecucionOptimizacion
from motor_cartera.optimizacion import consultas

FORMATOS = ("csv", "parquet", "jsonl")
CONTENIDOS = ("asignaciones", "pools", "restricciones")

TIPOS_ASIGNACIONES = {
    "orden_ruteo_original": pa.int32(),
    "valor_oportunidad_centavos": pa.int64(),
    "p_contacto_14d": pa.decimal128(9, 8),
    "unidades_recurso": pa.int64(),
    "retenida": pa.bool_(),
}
ESQUEMA_ASIGNACIONES = pa.schema(
    [(c, TIPOS_ASIGNACIONES.get(c, pa.string())) for c in consultas.COLUMNAS_EXPORTADAS]
)

ESQUEMA_POOLS = pa.schema(
    [
        ("pool", pa.string()),
        ("canal", pa.string()),
        ("tipo_alcance", pa.string()),
        ("referencia_alcance", pa.string()),
        ("capacidad_declarada", pa.int64()),
        ("capacidad", pa.int64()),
        ("elegibles", pa.int64()),
        ("retenidas", pa.int64()),
        ("utilizada", pa.int64()),
        ("holgura", pa.int64()),
        ("utilizacion", pa.decimal128(12, 6)),
        ("unidades_por_asignacion", pa.int64()),
        ("unidades_consumidas", pa.int64()),
    ]
)

ESQUEMA_RESTRICCIONES = pa.schema(
    [
        ("codigo", pa.string()),
        ("tipo", pa.string()),
        ("dimension", pa.string()),
        ("valor_dimension", pa.string()),
        ("operador", pa.string()),
        ("lhs", pa.int64()),
        ("rhs", pa.int64()),
        ("holgura", pa.int64()),
        ("binding", pa.bool_()),
        ("cuentas_grupo", pa.int64()),
        ("total", pa.int64()),
        ("limite_resuelto", pa.int64()),
    ]
)

TODAS = 1_000_000
"""Mas filas de las que tiene cualquier plan agregado (pools y restricciones)."""


def exportar(s, ejecucion: EjecucionOptimizacion, ruta: Path, formato: str, contenido: str) -> int:
    """Escribe `contenido` del plan de una ejecucion EXITOSA en `ruta`; devuelve cuantas filas."""
    if formato not in FORMATOS or contenido not in CONTENIDOS:
        raise ValueError(f"formato {formato} o contenido {contenido} no soportado")
    ruta.parent.mkdir(parents=True, exist_ok=True)
    if contenido == "asignaciones":
        return _asignaciones(s, ejecucion, ruta, formato)
    if contenido == "pools":
        _, usos = consultas.capacidad(s, ejecucion, canal=None, desplazamiento=0, limite=TODAS)
        registros = [
            {
                "pool": u.pool,
                "canal": u.canal,
                "tipo_alcance": u.tipo_alcance,
                "referencia_alcance": u.referencia_alcance,
                "capacidad_declarada": u.capacidad_declarada,
                "capacidad": u.capacidad_efectiva,
                "elegibles": u.elegibles,
                "retenidas": u.retenidas,
                "utilizada": u.utilizada,
                "holgura": u.holgura,
                "utilizacion": u.utilizacion,
                "unidades_por_asignacion": u.unidades_por_asignacion,
                "unidades_consumidas": u.unidades_consumidas,
            }
            for u in usos
        ]
        return _escribir(ruta, formato, ESQUEMA_POOLS, registros)
    _, filas = consultas.restricciones(
        s, ejecucion, binding=None, tipo=None, desplazamiento=0, limite=TODAS
    )
    registros = [{c: getattr(r, c) for c in ESQUEMA_RESTRICCIONES.names} for r in filas]
    return _escribir(ruta, formato, ESQUEMA_RESTRICCIONES, registros)


def _texto(valor):
    """Un decimal exacto como texto en punto fijo en CSV y JSONL ("0.00000000", no "0E-8"); lo
    demas, como viene."""
    if valor is None or isinstance(valor, bool | int | str):
        return valor
    return format(valor, "f")


def _escribir(ruta: Path, formato: str, esquema: pa.Schema, registros: list[dict]) -> int:
    if formato == "parquet":
        import pyarrow.parquet as pq

        pq.write_table(pa.Table.from_pylist(registros, schema=esquema), ruta)
    elif formato == "csv":
        with ruta.open("w", encoding="utf-8", newline="") as salida:
            escritor = csv.DictWriter(salida, fieldnames=esquema.names)
            escritor.writeheader()
            for r in registros:
                escritor.writerow({k: _texto(v) for k, v in r.items()})
    else:
        with ruta.open("w", encoding="utf-8") as salida:
            for r in registros:
                salida.write(json.dumps({k: _texto(v) for k, v in r.items()}) + "\n")
    return len(registros)


def _asignaciones(s, ejecucion: EjecucionOptimizacion, ruta: Path, formato: str) -> int:
    if formato == "csv":
        with ruta.open("wb") as salida:
            consultas.copiar_resultados(s, ejecucion, salida)
        return ejecucion.cuentas_publicadas
    descriptor, intermedio = tempfile.mkstemp(suffix=".csv", dir=ruta.parent)
    try:
        with os.fdopen(descriptor, "wb") as salida:
            consultas.copiar_resultados(s, ejecucion, salida)
        return _convertir(Path(intermedio), ruta, formato)
    finally:
        os.remove(intermedio)


def _convertir(intermedio: Path, ruta: Path, formato: str) -> int:
    """El CSV del COPY, por lotes, a Parquet o JSONL con los tipos exactos de cada columna."""
    import pyarrow.csv as pacsv

    lector = pacsv.open_csv(
        intermedio,
        convert_options=pacsv.ConvertOptions(
            column_types=ESQUEMA_ASIGNACIONES,
            strings_can_be_null=True,
            quoted_strings_can_be_null=False,
        ),
    )
    filas = 0
    if formato == "parquet":
        import pyarrow.parquet as pq

        with pq.ParquetWriter(ruta, ESQUEMA_ASIGNACIONES) as escritor:
            for lote in lector:
                escritor.write_batch(lote)
                filas += lote.num_rows
        return filas
    with ruta.open("w", encoding="utf-8") as salida:
        for lote in lector:
            for fila in lote.to_pylist():
                salida.write(json.dumps({k: _texto(v) for k, v in fila.items()}) + "\n")
            filas += lote.num_rows
    return filas
