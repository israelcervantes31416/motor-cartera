"""Modelo de datos persistente."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

# Nombres deterministas para indices y restricciones. Sin esto PostgreSQL los inventa, y
# una migracion futura que necesite borrar o cambiar una restriccion no sabe como se llama.
SQLModel.metadata.naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def ahora() -> datetime:
    return datetime.now(UTC)


class EstadoCorrida(StrEnum):
    """En que quedo una corrida. Solo una EXITOSA publica cuentas."""

    EN_PROCESO = "EN_PROCESO"  # registrada; se esta leyendo, validando o escribiendo
    EXITOSA = "EXITOSA"  # publico sus cuentas; sus rechazos, si hubo, caben en la tolerancia
    RECHAZADA = "RECHAZADA"  # se juzgo y no paso: demasiados rechazos o mas de un corte
    FALLIDA = "FALLIDA"  # no se pudo juzgar: archivo ilegible o error; no publico nada


class Corrida(SQLModel, table=True):
    """Una ejecucion del pipeline. Todo lo que se escribe cuelga de una corrida.

    Sin esto no hay trazabilidad: si manana alguien pregunta de donde salio un numero,
    la respuesta tiene que ser una fila de esta tabla.

    Es una entidad y no una columna de `cuenta` porque tiene que existir aunque no se
    escriba nada: una corrida rechazada o fallida no deja cuentas, y es justo la que mas
    importa poder auditar.
    """

    __tablename__ = "corrida"
    __table_args__ = (
        # Una cartera (mismo archivo, misma firma) se publica una sola vez. Lo garantiza la
        # base y no solo el codigo: dos peticiones simultaneas con el mismo archivo no
        # pueden publicar las dos. Las corridas que no publicaron no cuentan.
        sa.Index(
            "ux_corrida_firma_publicada",
            "firma",
            unique=True,
            postgresql_where=sa.text("estado = 'EXITOSA'"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    run_id: UUID = Field(default_factory=uuid4, unique=True)
    """Identificador publico. El `id` es interno: consecutivo, adivinable y revela volumen."""
    iniciada_en: datetime = Field(
        default_factory=ahora, sa_type=sa.DateTime(timezone=True), index=True
    )
    terminada_en: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    origen: str = Field(description="Archivo o proceso que la disparo")
    firma: str = Field(max_length=64, description="SHA-256 del archivo tal como llego")
    estado: EstadoCorrida = Field(
        default=EstadoCorrida.EN_PROCESO,
        sa_type=sa.Enum(
            EstadoCorrida,
            name="estado_corrida",
            native_enum=False,
            create_constraint=True,
            length=12,
        ),
    )
    tolerancia_rechazo: float = Field(
        description="Fraccion maxima de rechazos con la que se juzgo; queda con la corrida "
        "para saber con que regla se decidio aunque la configuracion cambie despues"
    )
    fecha_corte: date | None = Field(
        default=None, description="El corte de la cartera, si trae uno solo"
    )
    filas_leidas: int = 0
    filas_validas: int = 0
    filas_rechazadas: int = 0
    detalle: str | None = None


class Cuenta(SQLModel, table=True):
    """Una cuenta de la cartera, tal como quedo despues de validarse."""

    __tablename__ = "cuenta"
    # El mismo cliente aparece en la cartera de cada dia: es unico dentro de una corrida,
    # no en toda la historia. Hacerlo global obligaria a sobrescribir la foto de ayer para
    # guardar la de hoy, y se perderia justo lo que la trazabilidad quiere conservar.
    __table_args__ = (sa.UniqueConstraint("corrida_id", "cliente_unico"),)

    id: int | None = Field(default=None, primary_key=True)
    corrida_id: int = Field(foreign_key="corrida.id")  # la restriccion unica ya la indexa

    cliente_unico: str = Field(index=True, max_length=20)
    saldo_total: Decimal = Field(max_digits=14, decimal_places=2)
    dias_atraso: int
    producto: str = Field(max_length=20)
    canal: str = Field(max_length=20)
    cve_entidad: str = Field(max_length=2, index=True)
    cve_municipio: str = Field(max_length=3)
    # Un dia del calendario, no un instante: con datetime, tarde o temprano alguien le pone
    # zona horaria y el corte del 31 aparece como el 30.
    fecha_corte: date = Field(index=True)


class Rechazo(SQLModel, table=True):
    """Un registro que no cumplio el contrato: lo que traia y por que no paso.

    Se guarda aunque la corrida no publique nada, porque es la evidencia de que el diseno
    es fail-closed y es lo que el operador necesita para corregir el archivo.
    """

    __tablename__ = "rechazo"
    __table_args__ = (sa.UniqueConstraint("corrida_id", "fila"),)

    id: int | None = Field(default=None, primary_key=True)
    corrida_id: int = Field(foreign_key="corrida.id")
    fila: int = Field(description="Numero de fila en el archivo; el encabezado es la fila 1")
    valores: dict[str, str | None] = Field(sa_type=JSONB)
    """El registro tal como se leyo, en texto."""
    motivos: list[dict[str, str]] = Field(sa_type=JSONB)
    """Las reglas que no cumplio: [{"campo": ..., "regla": ...}]."""
