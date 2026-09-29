"""Lo que la API recibe y devuelve.

Las reglas no se repiten aqui: los estados salen del modelo y los catalogos del contrato.
Si el contrato cambia, la API cambia con el, sin que nadie tenga que acordarse.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from motor_cartera.contratos.cartera import CANALES, PRODUCTOS
from motor_cartera.db.modelos import EstadoCorrida
from motor_cartera.ingesta.lectores import REQUERIDAS
from motor_cartera.segmentacion import Dimension

# Los catalogos del contrato, como tipos de la API: un filtro con un producto que el
# contrato no conoce es un 422, sin que la lista se haya escrito dos veces.
Producto = StrEnum("Producto", {p: p for p in PRODUCTOS})
Canal = StrEnum("Canal", {c: c for c in CANALES})


class Paginacion(BaseModel):
    """Paginacion por numero de pagina, igual en todas las listas de la API."""

    pagina: int = Field(default=1, ge=1, description="Pagina a devolver, desde 1.")
    por_pagina: int = Field(
        default=50, ge=1, le=500, description="Elementos por pagina; hasta 500."
    )

    @property
    def desplazamiento(self) -> int:
        return (self.pagina - 1) * self.por_pagina


class Pagina[T](BaseModel):
    total: int = Field(description="Cuantos elementos hay en total, sumando todas las paginas.")
    pagina: int
    por_pagina: int
    elementos: list[T] = Field(description="Vacia si la pagina queda despues de la ultima.")


EJEMPLO_CORRIDA = {
    "run_id": "0b8e6f1c-3d5a-4f7e-9c2b-1a4d6e8f0a2c",
    "estado": "EXITOSA",
    "origen": "cartera_sintetica.xlsx",
    "firma": "9f2c4e6a8b0d1f3e5a7c9e1b3d5f7a9c1e3b5d7f9a1c3e5b7d9f1a3c5e7b9d1f",
    "fecha_corte": "2026-09-30",
    "filas_leidas": 10000,
    "filas_validas": 9800,
    "filas_rechazadas": 200,
    "tolerancia_rechazo": 0.05,
    "iniciada_en": "2026-09-30T15:04:05.123456Z",
    "terminada_en": "2026-09-30T15:04:09.654321Z",
    "duracion_segundos": 4.531,
    "detalle": "Se publicaron 9,800 cuentas; 200 registros (2.0%) se rechazaron, dentro de la "
    "tolerancia de 5.0%. Origen: hoja 'cartera' de 'cartera_sintetica.xlsx'. Descartado: "
    "hoja 'LEEME': ...",
}


class CorridaRespuesta(BaseModel):
    """Una corrida: que archivo, como va o como termino, y cuanto tardo."""

    model_config = ConfigDict(
        from_attributes=True, json_schema_extra={"examples": [EJEMPLO_CORRIDA]}
    )

    run_id: UUID
    estado: EstadoCorrida = Field(
        description="EN_PROCESO mientras trabaja. Al terminar: EXITOSA (publico sus cuentas), "
        "RECHAZADA (demasiados registros no cumplen el contrato; no publico nada) o FALLIDA "
        "(no se pudo juzgar: archivo ilegible o error; no publico nada)."
    )
    origen: str = Field(description="Nombre del archivo recibido.")
    firma: str = Field(description="SHA-256 del archivo: misma firma, mismo archivo.")
    fecha_corte: date | None = Field(description="La mas reciente del archivo, si se leyo.")
    filas_leidas: int
    filas_validas: int = Field(
        description="Cumplen el contrato. Se publican solo si la corrida termina EXITOSA."
    )
    filas_rechazadas: int = Field(description="No cumplen el contrato; ver /rechazos.")
    tolerancia_rechazo: float = Field(description="Fraccion maxima de rechazos que se admitio.")
    iniciada_en: datetime
    terminada_en: datetime | None
    detalle: str | None = Field(description="Que paso, en palabras, y de donde se leyo.")

    @computed_field(description="Segundos de inicio a fin; vacio mientras esta en proceso.")
    @property
    def duracion_segundos(self) -> float | None:
        if self.terminada_en is None:
            return None
        return round((self.terminada_en - self.iniciada_en).total_seconds(), 3)


class MotivoRespuesta(BaseModel):
    campo: str
    regla: str = Field(
        description="La regla del contrato que no se cumplio, como la nombra pandera.",
        examples=["greater_than_or_equal_to(0)"],
    )


class RechazoRespuesta(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    fila: int = Field(description="Fila del archivo; el encabezado es la fila 1.")
    valores: dict[str, str | None] = Field(description="El registro tal como llego, en texto.")
    motivos: list[MotivoRespuesta]

    @field_validator("valores")
    @classmethod
    def _en_el_orden_del_contrato(cls, valores: dict[str, str | None]) -> dict[str, str | None]:
        # JSONB no conserva el orden de las llaves; quien corrige el archivo lo lee en este.
        return {columna: valores[columna] for columna in REQUERIDAS if columna in valores}


class PaginaRechazos(Pagina[RechazoRespuesta]):
    run_id: UUID
    estado: EstadoCorrida


class ParametrosResumen(Paginacion):
    run_id: UUID | None = Field(
        default=None,
        description="Resume esa corrida y no la vigente. Para paginar, fija el run_id de la "
        "primera respuesta: si entre pagina y pagina se publica otra cartera, no se mezclan.",
    )
    por: list[Dimension] = Field(
        default_factory=lambda: [Dimension.CANAL, Dimension.TRAMO_ATRASO],
        min_length=1,
        description="Dimensiones del segmento, en orden. Se repite el parametro: "
        "`?por=canal&por=tramo_atraso`.",
    )
    producto: Producto | None = Field(default=None, description="Solo cuentas de ese producto.")
    canal: Canal | None = Field(default=None, description="Solo cuentas de ese canal.")

    @field_validator("por")
    @classmethod
    def _sin_repetir(cls, por: list[Dimension]) -> list[Dimension]:
        if len(set(por)) != len(por):
            raise ValueError("Una dimension no puede repetirse.")
        return por


class SegmentoRespuesta(BaseModel):
    segmento: dict[str, str] = Field(
        description="El valor de cada dimension.",
        examples=[{"canal": "CAMPO", "tramo_atraso": "91+"}],
    )
    cuentas: int
    saldo_total: Decimal = Field(description="En pesos, como texto para no perder centavos.")
    saldo_promedio: Decimal


class ResumenCartera(Pagina[SegmentoRespuesta]):
    run_id: UUID = Field(description="La corrida que se resume: de ahi sale cada numero.")
    fecha_corte: date
    dimensiones: list[Dimension]
    total_cuentas: int = Field(description="Cuentas que pasan los filtros, en todos los segmentos.")
    saldo_total: Decimal = Field(description="Su saldo sumado, en todos los segmentos.")
