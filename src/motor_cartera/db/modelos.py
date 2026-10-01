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
    firma_contenido: str | None = Field(
        default=None,
        max_length=64,
        description="SHA-256 de la forma canonica de los registros validos: identifica la "
        "cartera y no el archivo, asi que es la misma en xlsx, csv o zip. Vacia hasta que la "
        "corrida se juzga, y si no se pudo juzgar",
    )
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
    version_contrato: str = Field(
        max_length=32,
        description="Con que version del contrato se juzgo. Como la tolerancia, queda con la "
        "corrida aunque el contrato cambie despues",
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


class EstadoDecision(StrEnum):
    """En que quedo una ejecucion del motor de decision. Solo una EXITOSA publica decisiones.

    No hay RECHAZADA: el motor no juzga la cartera, eso ya lo hizo su corrida. Decide sobre una
    cartera publicada, y termina o falla.
    """

    EN_PROCESO = "EN_PROCESO"  # registrada; se estan decidiendo sus cuentas
    EXITOSA = "EXITOSA"  # publico la decision de cada cuenta de la corrida
    FALLIDA = "FALLIDA"  # no termino; no publica ninguna decision


class EjecucionDecision(SQLModel, table=True):
    """Una ejecucion del motor de decision sobre una corrida publicada.

    Es una entidad, como la corrida, porque tiene que existir aunque no deje decisiones: una
    ejecucion fallida es justo la que hay que poder auditar. Guarda con que version de las reglas se
    decidio, para que cada decision se pueda volver a explicar aunque las reglas cambien despues.
    """

    __tablename__ = "ejecucion_decision"
    __table_args__ = (
        # Una corrida se decide con exito una sola vez por version de las reglas. Lo garantiza la
        # base y no solo el codigo: dos ejecuciones simultaneas no pueden quedar EXITOSA las dos.
        # Las que no terminaron EXITOSA no cuentan, asi que una FALLIDA se puede reintentar, y otra
        # version de las reglas puede decidir la misma corrida.
        sa.Index(
            "ux_ejecucion_decision_exitosa",
            "corrida_id",
            "version_reglas",
            unique=True,
            postgresql_where=sa.text("estado = 'EXITOSA'"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    decision_run_id: UUID = Field(default_factory=uuid4, unique=True)
    """Identificador publico. No se llama run_id: ese es el de la corrida de ingesta."""
    corrida_id: int = Field(foreign_key="corrida.id", index=True)
    """La corrida que se decidio. Sin cascada: borrar una corrida con ejecuciones falla, en lugar de
    llevarse la evidencia. Lleva su propio indice porque hay que encontrar todas las ejecuciones de
    una corrida, en cualquier estado, y el indice parcial solo cubre las EXITOSA."""
    version_reglas: str = Field(max_length=32)
    """Con que reglas se decidio: decision/v1, decision/v2... No tiene valor por omision en la base:
    cada ejecucion trae la suya."""
    estado: EstadoDecision = Field(
        default=EstadoDecision.EN_PROCESO,
        sa_type=sa.Enum(
            EstadoDecision,
            name="estado_decision",
            native_enum=False,
            create_constraint=True,
            length=12,
        ),
    )
    iniciada_en: datetime = Field(default_factory=ahora, sa_type=sa.DateTime(timezone=True))
    terminada_en: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    cuentas_evaluadas: int = 0
    """Cuantas cuentas alcanzo a evaluar el motor. En una FALLIDA puede ser mayor que cero."""
    cuentas_decididas: int = 0
    """Cuantas decisiones publico. En una FALLIDA es cero."""
    detalle: str | None = None
    """Que paso, para una persona. No es la explicacion de cada cuenta: esa son sus motivos."""


class DecisionCuenta(SQLModel, table=True):
    """Lo que el motor decidio de una cuenta en una ejecucion, y por que.

    No copia nada de la cuenta (cliente, saldo, atraso, producto, canal): eso vive en `cuenta` y se
    lee con un JOIN. El vocabulario de las reglas se guarda como texto y sin CHECK: es el de la
    version que decidio, y una version nueva puede traer otro sin migrar esta tabla.
    """

    __tablename__ = "decision_cuenta"
    # Una ejecucion decide cada cuenta a lo mas una vez.
    __table_args__ = (sa.UniqueConstraint("ejecucion_decision_id", "cuenta_id"),)

    id: int | None = Field(default=None, primary_key=True)
    # La restriccion unica ya indexa ejecucion_decision_id.
    ejecucion_decision_id: int = Field(foreign_key="ejecucion_decision.id")
    cuenta_id: int = Field(foreign_key="cuenta.id")
    segmento: str = Field(max_length=20)
    prioridad: str = Field(max_length=20)
    canal_recomendado: str = Field(max_length=20)
    """La decision del motor. No es `cuenta.canal`, que es un dato de la cartera."""
    motivos: list[dict[str, str]] = Field(sa_type=JSONB)
    """Por que salio asi, paso por paso: [{"codigo": ..., "campo": ..., "valor": ...}]."""
