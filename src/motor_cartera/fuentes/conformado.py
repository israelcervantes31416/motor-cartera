"""El dataset conformado (source-conformed): los registros validos de una fuente, en Parquet.

Hay dos representaciones de cada fuente, y no se confunden:

- ORIGINAL: los bytes exactos que se recibieron, en el almacen de artefactos. Es la evidencia.
- SOURCE-CONFORMED: los registros que cumplieron el contrato, con las columnas del contrato en su
  orden oficial y cada valor tipado desde su forma canonica (un importe es DECIMAL(14, 2), una fecha
  es DATE, un instante TIMESTAMP). Ya no es el archivo del acreedor: lleva dos columnas tecnicas,
  `_source_row` (la fila del archivo original) y `_source_sheet` (su hoja, o su miembro dentro de un
  zip; vacia en un csv suelto), que lo atan a la evidencia registro por registro.

No lleva ningun score ni variable derivada: es la fuente, validada y normalizada, para que el modelo
canonico de v0.7 no tenga que volver a interpretar un xlsx de cientos de miles de filas. Se escribe
por lotes (un row group por lote), se guarda en el mismo almacen por contenido y se registra en la
base con su contrato, su firma, sus conteos y de que artefacto original salio.

Es reproducible: el mismo archivo con la misma version del contrato produce los mismos bytes.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from motor_cartera.contratos.fuente import ContratoFuente, Tipo

COLUMNA_FILA = "_source_row"
COLUMNA_HOJA = "_source_sheet"

TIPOS = {
    Tipo.TEXTO: pa.string(),
    Tipo.CODIGO: pa.string(),
    Tipo.CATALOGO: pa.string(),
    Tipo.ENTERO: pa.int64(),
    Tipo.IMPORTE: pa.decimal128(14, 2),
    Tipo.DECIMAL: pa.float64(),
    Tipo.FECHA: pa.date32(),
    Tipo.FECHA_HORA: pa.timestamp("us"),
}
"""El tipo de Parquet de cada tipo de columna del contrato."""

COMPRESION = "zstd"


def esquema(contrato: ContratoFuente, metadata: dict[str, str]) -> pa.Schema:
    """Las columnas del contrato en su orden oficial, mas las dos tecnicas, con `metadata` en el
    esquema del archivo: el contrato y de que artefacto original sale."""
    campos = [pa.field(c.nombre, TIPOS[c.tipo]) for c in contrato.columnas]
    campos += [
        pa.field(COLUMNA_FILA, pa.int64(), nullable=False),
        pa.field(COLUMNA_HOJA, pa.string()),
    ]
    return pa.schema(campos, metadata={f"motor_cartera.{k}": v for k, v in metadata.items()})


class EscritorConformado:
    """Escribe el dataset conformado por lotes, sin tenerlo entero en memoria."""

    def __init__(self, ruta: Path, contrato: ContratoFuente, metadata: dict[str, str]) -> None:
        self.ruta = ruta
        self._contrato = contrato
        self._esquema = esquema(contrato, metadata)
        self._escritor = pq.ParquetWriter(ruta, self._esquema, compression=COMPRESION)
        self.filas = 0

    def escribir(self, canonicos: pd.DataFrame, hojas: pd.Series) -> None:
        """Un lote de registros validos en forma canonica, con la fila del archivo como indice y la
        hoja de cada uno."""
        if canonicos.empty:
            return
        columnas = [
            pc.cast(pa.array(canonicos[c.nombre].astype("string"), type=pa.string()), TIPOS[c.tipo])
            for c in self._contrato.columnas
        ]
        columnas.append(pa.array(canonicos.index.to_numpy(dtype="int64"), type=pa.int64()))
        columnas.append(pa.array(hojas.astype("string"), type=pa.string()))
        self._escritor.write_table(pa.Table.from_arrays(columnas, schema=self._esquema))
        self.filas += len(canonicos)

    def cerrar(self) -> None:
        self._escritor.close()


def leer(ruta: Path) -> pa.Table:
    """El dataset conformado completo. Para las pruebas y para quien lo audite."""
    return pq.read_table(ruta)
