"""Juzgar una fuente oficial completa, por lotes, en dos pasadas, con sus invariantes globales.

Un archivo de 500,000 filas no cabe entero en memoria como texto, pero sus reglas siguen siendo del
archivo entero: un CLIENTE_UNICO repetido es un duplicado aunque una copia este en el primer lote y
la otra en el ultimo. Por eso se juzga en dos pasadas:

1. Sobre el archivo. Cada lote se juzga registro por registro (`contratos.fuente.juzgar_lote`) y con
   las reglas extra de quien lo usa, como la geografia de cartera/v2. Los rechazados se entregan de
   inmediato, para escribirlos y no acumularlos. Los validos se guardan, como texto y con su fila y
   su hoja, en un Parquet provisional. Mientras, se cuenta cada llave, de todos los registros.
2. Sobre el provisional, que es local y rapido de leer. Las copias de una llave repetida se rechazan
   todas: no hay forma de saber cual es la buena. Los demas son los validos de la fuente: se firman
   y se entregan a quien los publica.

Entre una y otra ya se saben los conteos definitivos, asi que quien llama decide si publica antes de
la segunda pasada, y solo escribe el dataset conformado y la proyeccion si va a publicar. Un lote no
es una publicacion: nada de esto confirma una transaccion.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from motor_cartera.contratos.cartera import Motivo
from motor_cartera.contratos.fuente import ContratoFuente, FirmaDeContenido, juzgar_lote
from motor_cartera.fuentes.conformado import COLUMNA_FILA, COLUMNA_HOJA
from motor_cartera.fuentes.lotes import Lote

DUPLICADO = "unico_en_el_corte"
"""La regla de un registro cuya llave se repite dentro del mismo archivo."""


class Cronometro:
    """Cuanto tiempo se va en cada fase. El benchmark lo lee; la ingesta normal no lo necesita."""

    def __init__(self) -> None:
        self.fases: dict[str, float] = defaultdict(float)

    @contextmanager
    def fase(self, nombre: str) -> Iterator[None]:
        inicio = time.perf_counter()
        try:
            yield
        finally:
            self.fases[nombre] += time.perf_counter() - inicio


@dataclass(frozen=True)
class Rechazado:
    """Un registro que no cumplio: su fila, lo que traia y por que."""

    fila: int
    valores: dict[str, str | None]
    """El registro tal como se leyo: texto sin espacios alrededor, con las columnas del contrato."""
    motivos: list[Motivo]


Rechazar = Callable[[list[Rechazado]], None]
ValidarExtra = Callable[[pd.DataFrame], dict[int, list[Motivo]]]
Publicar = Callable[[pd.DataFrame, pd.Series], None]


class JuicioDeFuente:
    """Las dos pasadas de una fuente oficial. Se usa una vez por archivo."""

    def __init__(
        self,
        contrato: ContratoFuente,
        directorio: Path,
        *,
        rechazar: Rechazar,
        validar_extra: ValidarExtra | None = None,
        filas_por_lote: int = 50_000,
        cronometro: Cronometro | None = None,
    ) -> None:
        self._contrato = contrato
        self._provisional = directorio / "validos.parquet"
        self._rechazar = rechazar
        self._validar_extra = validar_extra
        self._filas_por_lote = filas_por_lote
        self._cronometro = cronometro or Cronometro()
        self._llaves: dict[str, int] = {}
        """Cuantos registros traen cada llave, todos: tambien los rechazados por otra razon."""
        self._llaves_validas: dict[str, int] = {}
        """Cuantos registros validos traen cada llave."""
        self.leidas = 0
        self.rechazadas_por_registro = 0

    # --- primera pasada ---------------------------------------------------------------------------

    def primera_pasada(self, lotes: Iterable[Lote]) -> None:
        escritor: pq.ParquetWriter | None = None
        try:
            iterador = iter(lotes)
            while True:
                with self._cronometro.fase("lectura"):
                    lote = next(iterador, None)
                if lote is None:
                    break
                with self._cronometro.fase("validacion"):
                    validos, rechazados = self._juzgar(lote)
                if rechazados:
                    with self._cronometro.fase("persistencia"):
                        self._rechazar(rechazados)
                with self._cronometro.fase("provisional"):
                    escritor = self._guardar(validos, lote.hoja, escritor)
        finally:
            if escritor is not None:
                escritor.close()

    def _juzgar(self, lote: Lote) -> tuple[pd.DataFrame, list[Rechazado]]:
        datos = lote.datos
        self.leidas += len(datos)
        juicio = juzgar_lote(datos, self._contrato)
        motivos = dict(juicio.rechazos)
        if self._validar_extra is not None and not juicio.canonicos.empty:
            for fila, extra in self._validar_extra(juicio.canonicos).items():
                motivos.setdefault(fila, []).extend(extra)
        rechazadas = sorted(motivos)
        validos = datos.drop(index=rechazadas)
        llave = self._contrato.llave_unica
        if llave is not None:
            _contar(datos[llave], self._llaves)
            _contar(validos[llave], self._llaves_validas)
        self.rechazadas_por_registro += len(rechazadas)
        return validos, _rechazados(datos, motivos, rechazadas)

    def _guardar(
        self, validos: pd.DataFrame, hoja: str | None, escritor: pq.ParquetWriter | None
    ) -> pq.ParquetWriter | None:
        if validos.empty:
            return escritor
        tabla = pa.Table.from_pandas(validos.astype("string"), preserve_index=False)
        tabla = tabla.append_column(COLUMNA_FILA, pa.array(validos.index, type=pa.int64()))
        tabla = tabla.append_column(COLUMNA_HOJA, pa.array([hoja] * len(validos), type=pa.string()))
        if escritor is None:
            escritor = pq.ParquetWriter(self._provisional, tabla.schema, compression="snappy")
        escritor.write_table(tabla)
        return escritor

    # --- entre pasadas ----------------------------------------------------------------------------

    def llaves(self) -> set[str]:
        """Todas las llaves que trae el archivo, de registros validos o no."""
        return set(self._llaves)

    def duplicadas(self) -> set[str]:
        """Las llaves que trae mas de un registro del archivo."""
        return {llave for llave, veces in self._llaves.items() if veces > 1}

    @property
    def rechazadas(self) -> int:
        """Todos los rechazos: los de cada registro y los validos cuya llave se repite."""
        duplicadas = self.duplicadas()
        repetidos = sum(self._llaves_validas.get(llave, 0) for llave in duplicadas)
        return self.rechazadas_por_registro + repetidos

    @property
    def validas(self) -> int:
        return self.leidas - self.rechazadas

    # --- segunda pasada ---------------------------------------------------------------------------

    def segunda_pasada(self, publicar: Publicar | None = None) -> FirmaDeContenido:
        """Rechaza las copias de cada llave repetida, firma los validos y, si hay a quien, se los
        entrega con su hoja, lote por lote, en forma canonica y con su fila como indice."""
        firma = FirmaDeContenido(self._contrato)
        if not self._provisional.exists():
            return firma
        duplicadas = self.duplicadas()
        llave = self._contrato.llave_unica
        archivo = pq.ParquetFile(self._provisional)
        for lote in archivo.iter_batches(batch_size=self._filas_por_lote):
            with self._cronometro.fase("lectura_provisional"):
                datos = lote.to_pandas(types_mapper={pa.string(): pd.StringDtype()}.get)
                datos.index = pd.Index(datos.pop(COLUMNA_FILA).to_numpy())
                hojas = datos.pop(COLUMNA_HOJA)
                hojas.index = datos.index
            if llave is not None and duplicadas:
                repetidas = datos[llave].isin(duplicadas).to_numpy()
                if repetidas.any():
                    filas = list(datos.index[repetidas])
                    motivos = {int(f): [Motivo(llave, DUPLICADO)] for f in filas}
                    with self._cronometro.fase("persistencia"):
                        self._rechazar(_rechazados(datos, motivos, filas))
                    datos, hojas = datos.loc[~repetidas], hojas.loc[~repetidas]
            with self._cronometro.fase("validacion"):
                canonicos = juzgar_lote(datos, self._contrato).canonicos
            with self._cronometro.fase("firma"):
                firma.agregar(canonicos)
            if publicar is not None and not canonicos.empty:
                publicar(canonicos, hojas)
        return firma


def _contar(valores: pd.Series, conteo: dict[str, int]) -> None:
    for valor, veces in valores.dropna().value_counts().items():
        conteo[valor] = conteo.get(valor, 0) + int(veces)


def _rechazados(
    datos: pd.DataFrame, motivos: dict[int, list[Motivo]], filas: list[int]
) -> list[Rechazado]:
    if not filas:
        return []
    crudos = datos.loc[filas]
    crudos = crudos.astype(object).where(crudos.notna(), None)
    return [
        Rechazado(int(fila), registro, motivos[int(fila)])
        for fila, registro in zip(filas, crudos.to_dict("records"), strict=True)
    ]
