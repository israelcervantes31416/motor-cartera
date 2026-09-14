"""Modelo de datos persistente."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlmodel import Field, SQLModel


class Corrida(SQLModel, table=True):
    """Una ejecucion del pipeline. Todo lo que se escribe cuelga de una corrida.

    Sin esto no hay trazabilidad: si manana alguien pregunta de donde salio un numero,
    la respuesta tiene que ser una fila de esta tabla.
    """

    __tablename__ = "corrida"

    id: int | None = Field(default=None, primary_key=True)
    iniciada_en: datetime = Field(default_factory=datetime.utcnow, index=True)
    terminada_en: datetime | None = None
    origen: str = Field(description="Archivo o proceso que la disparo")
    filas_leidas: int = 0
    filas_validas: int = 0
    exitosa: bool = False
    detalle: str | None = None


class Cuenta(SQLModel, table=True):
    """Una cuenta de la cartera, tal como quedo despues de validarse."""

    __tablename__ = "cuenta"

    id: int | None = Field(default=None, primary_key=True)
    corrida_id: int = Field(foreign_key="corrida.id", index=True)

    cliente_unico: str = Field(index=True, max_length=20)
    saldo_total: Decimal = Field(max_digits=14, decimal_places=2)
    dias_atraso: int
    producto: str = Field(max_length=20)
    canal: str = Field(max_length=20)
    cve_entidad: str = Field(max_length=2, index=True)
    cve_municipio: str = Field(max_length=3)
    fecha_corte: datetime = Field(index=True)

# TODO(israel): decide si cliente_unico debe ser unico por corrida o global, y agrega
# el indice compuesto correspondiente en una migracion de Alembic. Es una decision de
# modelado, no un detalle: cambia que significa "duplicado" en todo el sistema.
