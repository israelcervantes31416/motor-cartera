"""Modelo de datos persistente."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql.elements import conv
from sqlmodel import Field, SQLModel

from motor_cartera.config import config
from motor_cartera.fuentes.formatos import Formato
from motor_cartera.lifecycle.reglas import (
    Canal,
    Medio,
    NivelContacto,
    OrigenRegistro,
    ResultadoGestion,
    ResultadoVisita,
    TipoEvento,
)

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


CLAVE_DEL_CONTENIDO = "storage_key = 'sha256/' || substr(sha256, 1, 2) || '/' || sha256"
"""La identidad fisica de un artefacto sale de su SHA-256, nunca de su nombre."""


class ArtefactoFuente(SQLModel, table=True):
    """Un archivo recibido, tal como llego: la evidencia original de una ingesta.

    Los bytes viven en el almacen de artefactos (MC_SOURCE_STORE_ROOT), no en PostgreSQL; esta fila
    es su registro. Hay una por contenido: la identidad es el SHA-256, y el mismo archivo subido dos
    veces, aunque sea con otro nombre, es el mismo artefacto. Cada corrida o ingesta que lo usa
    conserva su propio nombre de origen y su propia historia.

    El objeto es inmutable y no se borra al terminar una ingesta: es lo que permite volver a
    reproducir exactamente lo que se recibio. Tambien se registra aqui el Parquet de un dataset
    conformado, que se guarda en el mismo almacen.
    """

    __tablename__ = "artefacto_fuente"
    __table_args__ = (
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name=conv("ck_artefacto_sha256")),
        sa.CheckConstraint("tamano_bytes > 0", name=conv("ck_artefacto_tamano_positivo")),
        sa.CheckConstraint(CLAVE_DEL_CONTENIDO, name=conv("ck_artefacto_storage_key")),
    )

    id: int | None = Field(default=None, primary_key=True)
    artifact_id: UUID = Field(default_factory=uuid4, unique=True)
    """Identificador publico. El `id` es interno, como en las demas tablas."""
    sha256: str = Field(max_length=64, unique=True)
    """SHA-256 de los bytes, calculado mientras se copiaban al almacen."""
    tamano_bytes: int = Field(sa_type=sa.BigInteger)
    nombre_original: str = Field(max_length=255)
    """El nombre con que llego la primera vez. Es metadata: no identifica nada."""
    formato: Formato = Field(
        sa_type=sa.Enum(
            Formato,
            name="formato_artefacto",
            native_enum=False,
            create_constraint=True,
            length=12,
            # El valor (xlsx) y no el nombre del miembro (XLSX), que guardaria por omision.
            values_callable=lambda formatos: [formato.value for formato in formatos],
        )
    )
    """El formato reconocido por sus bytes, que coincide con su extension."""
    media_type: str | None = Field(default=None, max_length=100)
    storage_key: str = Field(max_length=80, unique=True)
    """Donde vive dentro del almacen: `sha256/ab/abcdef...`. Nunca sale por la API."""
    creado_en: datetime = Field(default_factory=ahora, sa_type=sa.DateTime(timezone=True))


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
        # cartera/v2 trae la fecha de corte como metadata del lote, y su archivo siempre en el
        # almacen: una corrida de cartera/v2 sin las dos no existe. Y se proyecta a Cuenta con una
        # version de la proyeccion, que una de cartera/v1 no tiene: v1 ya es la forma de Cuenta.
        sa.CheckConstraint(
            "version_contrato <> 'cartera/v2' "
            "OR (artefacto_fuente_id IS NOT NULL AND fecha_corte IS NOT NULL)",
            name=conv("ck_corrida_v2_fuente"),
        ),
        sa.CheckConstraint(
            "(version_contrato = 'cartera/v2') = (version_proyeccion IS NOT NULL)",
            name=conv("ck_corrida_proyeccion"),
        ),
        # Una cartera (mismo archivo, misma firma) se publica una sola vez. Lo garantiza la
        # base y no solo el codigo: dos peticiones simultaneas con el mismo archivo no
        # pueden publicar las dos. Las corridas que no publicaron no cuentan.
        sa.Index(
            "ux_corrida_firma_publicada",
            "firma",
            unique=True,
            postgresql_where=sa.text("estado = 'EXITOSA'"),
        ),
        # Y a lo mas una corrida activa por archivo: dos subidas simultaneas del mismo archivo no
        # pueden quedar EN_PROCESO las dos. Desde la 0006 una EN_PROCESO no deja de contar por su
        # edad: la cierra el worker que tiene su trabajo, o el que lo toma cuando vence su lease.
        sa.Index(
            "ux_corrida_firma_en_proceso",
            "firma",
            unique=True,
            postgresql_where=sa.text("estado = 'EN_PROCESO'"),
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
    artefacto_fuente_id: int | None = Field(
        default=None,
        foreign_key="artefacto_fuente.id",
        index=True,
        description="El archivo recibido, en el almacen de artefactos. Vacio solo en las corridas "
        "anteriores a la 0007, cuyo archivo no se conservo",
    )
    despacho_id: str = Field(
        default_factory=lambda: config.despacho_id,
        max_length=32,
        description="El despacho del sistema cuando se registro: metadata, no un dato del archivo",
    )
    cartera_id: str = Field(
        default_factory=lambda: config.cartera_id,
        max_length=32,
        description="La cartera del sistema cuando se registro: metadata, no un dato del archivo",
    )
    version_proyeccion: str | None = Field(
        default=None,
        max_length=32,
        description="Con que version de la proyeccion operacional se llevo cartera/v2 a Cuenta. "
        "Vacia en cartera/v1, que ya tiene la forma de Cuenta",
    )


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


class DatasetConformado(SQLModel, table=True):
    """El dataset conformado (source-conformed) que publico una ingesta de una fuente oficial.

    Es el linaje de punta a punta: de que artefacto original salio, en que artefacto (un Parquet en
    el mismo almacen) quedo, con que contrato se juzgo, cuantos registros trae y la firma de su
    contenido. Existe solo si la ingesta publico: es la fuente validada, no un intento.
    """

    __tablename__ = "dataset_conformado"
    __table_args__ = (
        sa.ForeignKeyConstraint(["corrida_id"], ["corrida.id"], name="fk_conformado_corrida"),
        sa.ForeignKeyConstraint(
            ["ingesta_pagos_id"], ["ingesta_pagos.id"], name="fk_conformado_pagos"
        ),
        sa.ForeignKeyConstraint(
            ["artefacto_original_id"], ["artefacto_fuente.id"], name="fk_conformado_original"
        ),
        sa.ForeignKeyConstraint(
            ["artefacto_conformado_id"], ["artefacto_fuente.id"], name="fk_conformado_parquet"
        ),
        # Una ingesta publica a lo mas un dataset conformado.
        sa.UniqueConstraint("corrida_id", name="uq_conformado_corrida"),
        sa.UniqueConstraint("ingesta_pagos_id", name="uq_conformado_pagos"),
        # De una corrida de cartera o de una ingesta de pagos, nunca de las dos ni de ninguna.
        sa.CheckConstraint(
            "(corrida_id IS NULL) <> (ingesta_pagos_id IS NULL)", name=conv("ck_conformado_origen")
        ),
        sa.CheckConstraint("filas >= 0 AND columnas > 0", name=conv("ck_conformado_conteos")),
        sa.CheckConstraint("firma_contenido ~ '^[0-9a-f]{64}$'", name=conv("ck_conformado_firma")),
    )

    id: int | None = Field(default=None, primary_key=True)
    dataset_id: UUID = Field(default_factory=uuid4, unique=True)
    """Identificador publico."""
    contrato: str = Field(max_length=32)
    """Con que contrato se juzgo: cartera/v2 o pagos/v1."""
    corrida_id: int | None = None
    """La corrida de cartera que lo publico, si es de una. Su llave esta en __table_args__."""
    ingesta_pagos_id: int | None = None
    """La ingesta de pagos que lo acepto, si es de una."""
    artefacto_original_id: int = Field(index=True)
    """El archivo tal como llego."""
    artefacto_conformado_id: int = Field(index=True)
    """El Parquet con los registros validos, en el mismo almacen."""
    firma_contenido: str = Field(max_length=64)
    """La firma de su contenido canonico: la misma que la de la ingesta que lo publico."""
    filas: int = Field(sa_type=sa.BigInteger)
    columnas: int
    """Las del contrato, sin contar las dos tecnicas (_source_row y _source_sheet)."""
    creado_en: datetime = Field(default_factory=ahora, sa_type=sa.DateTime(timezone=True))


class HojaCompanera(SQLModel, table=True):
    """Una hoja companera que traia el archivo de una corrida, como CARRIER junto a CARTERA.

    Se reconoce y se audita, pero no se juzga ni se publica, y no decide si la corrida publica: sus
    reglas de negocio todavia no estan definidas. Lo que se encontro queda aqui: cuantas filas y
    columnas trae, si su estructura es la esperada y lo incoherente, como advertencias. Sus bytes
    estan en el artefacto original, que es el libro o el zip completo.
    """

    __tablename__ = "hoja_companera"
    __table_args__ = (
        sa.ForeignKeyConstraint(["corrida_id"], ["corrida.id"], name="fk_companera_corrida"),
        sa.UniqueConstraint("corrida_id", "nombre", name="uq_companera_corrida_nombre"),
        sa.CheckConstraint("filas >= 0 AND columnas >= 0", name=conv("ck_companera_conteos")),
    )

    id: int | None = Field(default=None, primary_key=True)
    corrida_id: int
    nombre: str = Field(max_length=255)
    """La hoja del xlsx, o el miembro del zip."""
    filas: int = Field(sa_type=sa.BigInteger)
    columnas: int
    estructura_reconocida: bool
    """Si trae exactamente las columnas esperadas."""
    advertencias: list[str] = Field(sa_type=JSONB)
    creado_en: datetime = Field(default_factory=ahora, sa_type=sa.DateTime(timezone=True))


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
        # Y a lo mas un intento activo por corrida y version. Protege otra cosa que el de arriba:
        # aquel, que no se publique dos veces; este, que no se trabaje dos veces a la vez. Las
        # FALLIDA siguen siendo historia, tantas como haya.
        sa.Index(
            "ux_ejecucion_decision_en_proceso",
            "corrida_id",
            "version_reglas",
            unique=True,
            postgresql_where=sa.text("estado = 'EN_PROCESO'"),
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


class EstadoTerritorial(StrEnum):
    """En que quedo una ejecucion del motor territorial. Solo una EXITOSA publica resultados.

    No hay RECHAZADA: el motor territorial no vuelve a juzgar la cartera ni sus decisiones. Organiza
    por municipio las decisiones de una ejecucion de decision, y termina o falla.
    """

    EN_PROCESO = "EN_PROCESO"  # registrada; se estan calculando sus territorios
    EXITOSA = "EXITOSA"  # publico el resultado de cada municipio de la ejecucion de decision
    FALLIDA = "FALLIDA"  # no termino; no publica ningun resultado


class EjecucionTerritorial(SQLModel, table=True):
    """Una ejecucion del motor territorial sobre una ejecucion de decision.

    Cuelga de la ejecucion de decision y no de la corrida: una corrida se puede decidir con varias
    versiones de las reglas, y lo territorial tiene que decir exactamente de que decisiones salio.
    La corrida, su run_id y la version de decision se obtienen siguiendo esa llave; no se copian.

    Es una entidad, como la ejecucion de decision, porque tiene que existir aunque no deje
    resultados: una ejecucion fallida es justo la que hay que poder auditar. Guarda con que version
    de las reglas territoriales se calculo, para que cada resultado se pueda volver a explicar
    aunque las reglas cambien despues.
    """

    __tablename__ = "ejecucion_territorial"
    __table_args__ = (
        # Con nombre propio: el de la convencion pasaria de 63 caracteres, el limite de PostgreSQL,
        # y la base guardaria uno recortado. Es parte del contrato del esquema: sale en los
        # IntegrityError y una migracion futura se refiere a el.
        sa.ForeignKeyConstraint(
            ["ejecucion_decision_id"],
            ["ejecucion_decision.id"],
            name="fk_ejecucion_territorial_decision",
        ),
        # Una ejecucion de decision se organiza con exito una sola vez por version de las reglas
        # territoriales. Lo garantiza la base y no solo el codigo: dos ejecuciones simultaneas no
        # pueden quedar EXITOSA las dos. Las que no terminaron EXITOSA no cuentan, asi que una
        # FALLIDA se puede reintentar, y otra version puede organizar las mismas decisiones.
        sa.Index(
            "ux_ejecucion_territorial_exitosa",
            "ejecucion_decision_id",
            "version_reglas",
            unique=True,
            postgresql_where=sa.text("estado = 'EXITOSA'"),
        ),
        # Y a lo mas un intento activo por fuente y version, como en la ejecucion de decision.
        sa.Index(
            "ux_ejecucion_territorial_en_proceso",
            "ejecucion_decision_id",
            "version_reglas",
            unique=True,
            postgresql_where=sa.text("estado = 'EN_PROCESO'"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    territorial_run_id: UUID = Field(default_factory=uuid4, unique=True)
    """Identificador publico. No se llama run_id, que es el de la corrida, ni decision_run_id, que
    es el de la ejecucion de decision."""
    ejecucion_decision_id: int = Field(index=True)
    """La ejecucion de decision cuyas decisiones se organizaron; su llave foranea, con su nombre,
    esta en __table_args__. Sin cascada: borrar una ejecucion de decision con ejecuciones
    territoriales falla, en lugar de llevarse la evidencia. Lleva su propio indice porque hay que
    encontrar todas las ejecuciones territoriales de una ejecucion de decision, en cualquier estado,
    y el indice parcial solo cubre las EXITOSA."""
    version_reglas: str = Field(max_length=32)
    """Con que reglas se calculo: territorial/v1, territorial/v2... No tiene valor por omision en
    la base: cada ejecucion trae la suya."""
    estado: EstadoTerritorial = Field(
        default=EstadoTerritorial.EN_PROCESO,
        sa_type=sa.Enum(
            EstadoTerritorial,
            name="estado_territorial",
            native_enum=False,
            create_constraint=True,
            length=12,
        ),
    )
    iniciada_en: datetime = Field(default_factory=ahora, sa_type=sa.DateTime(timezone=True))
    terminada_en: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    territorios_evaluados: int = 0
    """Cuantos municipios alcanzo a evaluar el motor. En una FALLIDA puede ser mayor que cero."""
    territorios_publicados: int = 0
    """Cuantos resultados publico. En una FALLIDA es cero."""
    detalle: str | None = None
    """Que paso, para una persona. No es la explicacion de cada municipio: esa son sus motivos."""


class ResultadoTerritorial(SQLModel, table=True):
    """Lo que una ejecucion territorial publico de un municipio, y por que.

    Guarda los agregados de los que salio, ademas de la carga, el lugar y los motivos, para que cada
    resultado se pueda volver a explicar sin rehacer la agregacion. No guarda la clave del
    territorio, que es cve_entidad + cve_municipio, ni nombres de entidad o municipio, que la
    cartera no trae. Tampoco copia la ejecucion de decision: se llega a ella por la territorial.

    El vocabulario de las reglas (la carga y el codigo de cada motivo) se guarda como texto y sin
    CHECK, igual que el lugar: es el de la version que lo calculo, y una version nueva puede traer
    otro sin migrar esta tabla. Las reglas de territorial/v1, sus cargas, sus umbrales y la
    consistencia entre agregados, las protege el nucleo puro y no la base.
    """

    __tablename__ = "resultado_territorial"
    __table_args__ = (
        # La llave foranea y la restriccion unica llevan nombre propio: los de la convencion
        # pasarian de 63 caracteres, el limite de PostgreSQL, y la base guardaria unos recortados.
        # Son parte del contrato del esquema: salen en los IntegrityError y una migracion futura se
        # refiere a ellos.
        sa.ForeignKeyConstraint(
            ["ejecucion_territorial_id"],
            ["ejecucion_territorial.id"],
            name="fk_resultado_territorial_ejecucion",
        ),
        # Una ejecucion publica cada municipio a lo mas una vez.
        sa.UniqueConstraint(
            "ejecucion_territorial_id",
            "cve_entidad",
            "cve_municipio",
            name="uq_resultado_territorial_municipio",
        ),
        # Cada lugar del orden de campo es de un solo municipio por ejecucion. Los que no tienen
        # cuentas de campo no tienen lugar (NULL), y de esos puede haber muchos. El mismo indice
        # sirve para leer en orden los que si lo tienen.
        sa.Index(
            "ux_resultado_territorial_posicion_campo",
            "ejecucion_territorial_id",
            "posicion_campo",
            unique=True,
            postgresql_where=sa.text("posicion_campo IS NOT NULL"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    # Su llave foranea esta en __table_args__, con su nombre, y la restriccion unica ya la indexa.
    ejecucion_territorial_id: int
    # Texto con sus ceros a la izquierda, como en `cuenta`, y sin llave hacia un catalogo del INEGI,
    # que todavia no existe.
    cve_entidad: str = Field(max_length=2)
    cve_municipio: str = Field(max_length=3)
    cuentas_total: int = Field(sa_type=sa.BigInteger)
    """BIGINT, como el COUNT que lo va a calcular: el esquema no le achica el dominio a INTEGER."""
    saldo_total: Decimal = Field(max_digits=24, decimal_places=2)
    """NUMERIC(24, 2) y no el (14, 2) de `cuenta`: aquel es el saldo de una cuenta, y este suma el
    de todas las cuentas de un municipio."""
    cuentas_campo: int = Field(sa_type=sa.BigInteger)
    saldo_campo: Decimal = Field(max_digits=24, decimal_places=2)
    carga: str = Field(max_length=20)
    posicion_campo: int | None = Field(default=None, sa_type=sa.BigInteger)
    """El lugar del municipio entre los que tienen cuentas de campo, desde 1. NULL si no tiene
    ninguna: nunca 0."""
    motivos: list[dict[str, str]] = Field(sa_type=JSONB)
    """Por que salio asi: [{"codigo": ..., "campo": ..., "valor": ...}]."""


class EstadoRuteo(StrEnum):
    """En que quedo una ejecucion del motor de ruteo. Solo una EXITOSA publica rutas.

    No hay RECHAZADA: el ruteo no vuelve a juzgar la cartera, sus decisiones ni su organizacion
    territorial. Ordena las visitas de una ejecucion territorial publicada, y termina o falla.
    """

    EN_PROCESO = "EN_PROCESO"  # registrada; se estan calculando sus rutas
    EXITOSA = "EXITOSA"  # publico una ruta por cada municipio con trabajo de campo
    FALLIDA = "FALLIDA"  # no termino; no publica ninguna ruta ni ninguna parada


class EjecucionRuteo(SQLModel, table=True):
    """Una ejecucion del motor de ruteo sobre una ejecucion territorial.

    Cuelga de la ejecucion territorial y no de la de decision ni de la corrida: unas decisiones se
    pueden organizar con varias versiones de las reglas territoriales, y una ruta tiene que decir
    exactamente de que organizacion salio. La ejecucion de decision, la corrida y sus
    identificadores se obtienen siguiendo esa llave; no se copian.

    Es una entidad, como las otras ejecuciones, porque tiene que existir aunque no deje rutas: una
    ejecucion fallida es justo la que hay que poder auditar. Guarda con que version de las reglas de
    ruteo se calculo, para que cada ruta se pueda volver a explicar aunque las reglas cambien
    despues.
    """

    __tablename__ = "ejecucion_ruteo"
    __table_args__ = (
        # Con nombre propio: el de la convencion pasaria de 63 caracteres, el limite de PostgreSQL,
        # y la base guardaria uno recortado. Es parte del contrato del esquema: sale en los
        # IntegrityError y una migracion futura se refiere a el.
        sa.ForeignKeyConstraint(
            ["ejecucion_territorial_id"],
            ["ejecucion_territorial.id"],
            name="fk_ejecucion_ruteo_territorial",
        ),
        # Una ejecucion territorial se rutea con exito una sola vez por version de las reglas de
        # ruteo. Lo garantiza la base y no solo el codigo: dos ejecuciones simultaneas no pueden
        # quedar EXITOSA las dos. Las que no terminaron EXITOSA no cuentan, asi que una FALLIDA se
        # puede reintentar, y otra version puede rutear la misma organizacion territorial.
        sa.Index(
            "ux_ejecucion_ruteo_exitosa",
            "ejecucion_territorial_id",
            "version_reglas",
            unique=True,
            postgresql_where=sa.text("estado = 'EXITOSA'"),
        ),
        # Y a lo mas un intento activo por fuente y version, como en las otras ejecuciones.
        sa.Index(
            "ux_ejecucion_ruteo_en_proceso",
            "ejecucion_territorial_id",
            "version_reglas",
            unique=True,
            postgresql_where=sa.text("estado = 'EN_PROCESO'"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    ruteo_run_id: UUID = Field(default_factory=uuid4, unique=True)
    """Identificador publico. No se llama run_id, que es el de la corrida, ni como el de ninguna de
    las otras ejecuciones."""
    ejecucion_territorial_id: int = Field(index=True)
    """La ejecucion territorial cuyos municipios se rutearon; su llave foranea, con su nombre, esta
    en __table_args__. Sin cascada: borrar una ejecucion territorial con ejecuciones de ruteo falla,
    en lugar de llevarse la evidencia. Lleva su propio indice porque hay que encontrar todas las
    ejecuciones de ruteo de una territorial, en cualquier estado, y el indice parcial solo cubre las
    EXITOSA."""
    version_reglas: str = Field(max_length=32)
    """Con que reglas se ruteo: ruteo/v1, ruteo/v2... No tiene valor por omision en la base: cada
    ejecucion trae la suya."""
    estado: EstadoRuteo = Field(
        default=EstadoRuteo.EN_PROCESO,
        sa_type=sa.Enum(
            EstadoRuteo,
            name="estado_ruteo",
            native_enum=False,
            create_constraint=True,
            length=12,
        ),
    )
    iniciada_en: datetime = Field(default_factory=ahora, sa_type=sa.DateTime(timezone=True))
    terminada_en: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    rutas_evaluadas: int = Field(default=0, sa_type=sa.BigInteger)
    """Cuantas rutas calculo el nucleo. Cero hasta que las calcula todas; en una FALLIDA puede ser
    mayor que cero."""
    rutas_publicadas: int = Field(default=0, sa_type=sa.BigInteger)
    """Cuantas rutas publico. En una FALLIDA es cero."""
    paradas_evaluadas: int = Field(default=0, sa_type=sa.BigInteger)
    """Cuantas paradas tienen esas rutas, en total."""
    paradas_publicadas: int = Field(default=0, sa_type=sa.BigInteger)
    """Cuantas paradas publico. En una FALLIDA es cero."""
    detalle: str | None = None
    """Que paso, para una persona. No es la explicacion de cada ruta: esa son sus distancias."""


class RutaTerritorial(SQLModel, table=True):
    """La ruta que una ejecucion de ruteo publico para un municipio con trabajo de campo.

    Apunta al ResultadoTerritorial del municipio y no copia su clave ni su lugar: cve_entidad,
    cve_municipio y posicion_campo se leen de ahi con un JOIN. Guarda cuantas paradas tiene y sus
    distancias en metros sinteticos: la del vecino mas cercano, la final con el regreso al deposito,
    el regreso y la mejora del 2-opt, para explicar cada ruta sin volver a calcularla.

    No guarda el algoritmo ni la metrica, ni lleva CHECK sobre las distancias: lo que significan es
    el contrato de la version de las reglas, y una version nueva puede calcular otras sin migrar
    esta tabla.
    """

    __tablename__ = "ruta_territorial"
    __table_args__ = (
        # La llave hacia resultado_territorial lleva nombre propio: el de la convencion pasaria de
        # 63 caracteres. La de ejecucion_ruteo cabe, y lleva el de la convencion.
        sa.ForeignKeyConstraint(
            ["resultado_territorial_id"],
            ["resultado_territorial.id"],
            name="fk_ruta_territorial_resultado",
        ),
        # Una ejecucion publica a lo mas una ruta por municipio. Con nombre corto: el de la
        # convencion quedaria justo en el limite de 63.
        sa.UniqueConstraint(
            "ejecucion_ruteo_id", "resultado_territorial_id", name="uq_ruta_ruteo_territorio"
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    # La restriccion unica ya indexa ejecucion_ruteo_id. Sin cascada, como todas las llaves.
    ejecucion_ruteo_id: int = Field(foreign_key="ejecucion_ruteo.id")
    resultado_territorial_id: int
    """El municipio, como lo publico la ejecucion territorial; su llave foranea esta en
    __table_args__."""
    paradas: int = Field(sa_type=sa.BigInteger)
    distancia_inicial_m: int = Field(sa_type=sa.BigInteger)
    """La de la ruta del vecino mas cercano, antes del 2-opt, con el regreso al deposito."""
    distancia_total_m: int = Field(sa_type=sa.BigInteger)
    """La de la ruta publicada, con el regreso al deposito."""
    distancia_regreso_deposito_m: int = Field(sa_type=sa.BigInteger)
    mejora_2opt_m: int = Field(sa_type=sa.BigInteger)


class ParadaRuta(SQLModel, table=True):
    """Una cuenta en una ruta: que decision de campo se visita, en que lugar, en que punto
    sintetico y a que distancia de la parada anterior.

    Apunta a la DecisionCuenta y no copia el cliente ni la cuenta: se leen con un JOIN. Repite
    ejecucion_ruteo_id aunque se pueda llegar a ella por la ruta, y no por descuido: asi una
    restriccion de la base garantiza que una decision aparece a lo mas una vez en toda una
    ejecucion de ruteo, aunque la ejecucion tenga muchas rutas.
    """

    __tablename__ = "parada_ruta"
    __table_args__ = (
        # Una decision, a lo mas una parada por ejecucion de ruteo. Otra ejecucion si puede volver
        # a visitarla.
        sa.UniqueConstraint(
            "ejecucion_ruteo_id", "decision_cuenta_id", name="uq_parada_ruteo_decision"
        ),
        # Cada lugar de la secuencia es de una sola parada por ruta. El mismo indice sirve para leer
        # las paradas de una ruta en orden.
        sa.UniqueConstraint("ruta_territorial_id", "secuencia", name="uq_parada_ruta_secuencia"),
    )

    id: int | None = Field(default=None, primary_key=True)
    # Las dos restricciones unicas ya indexan ejecucion_ruteo_id y ruta_territorial_id.
    ejecucion_ruteo_id: int = Field(foreign_key="ejecucion_ruteo.id")
    ruta_territorial_id: int = Field(foreign_key="ruta_territorial.id")
    decision_cuenta_id: int = Field(foreign_key="decision_cuenta.id")
    secuencia: int = Field(sa_type=sa.BigInteger)
    """El orden de visita dentro de la ruta, desde 1. No es posicion_campo, que es el lugar del
    municipio entre los municipios."""
    x_m: int = Field(sa_type=sa.BigInteger)
    """La coordenada sintetica de la parada en el plano de su municipio, en metros. No es una
    longitud geografica, ni la y una latitud."""
    y_m: int = Field(sa_type=sa.BigInteger)
    distancia_desde_anterior_m: int = Field(sa_type=sa.BigInteger)
    """Desde la parada anterior; la primera, desde el deposito."""


class EstadoIngestaPagos(StrEnum):
    """En que quedo una ingesta de pagos. Solo una EXITOSA acepta sus movimientos."""

    EN_PROCESO = "EN_PROCESO"  # registrada; se estan leyendo y juzgando sus movimientos
    EXITOSA = "EXITOSA"  # acepto sus movimientos: su dataset conformado es la fuente validada
    RECHAZADA = "RECHAZADA"  # se juzgo y no paso: mas rechazos que la tolerancia
    FALLIDA = "FALLIDA"  # no se pudo juzgar: archivo ilegible, estructura distinta o error


class IngestaPagos(SQLModel, table=True):
    """Una ingesta de pagos/v1: un archivo de movimientos economicos, juzgado y, si pasa, aceptado.

    No es una corrida: una corrida publica cuentas, que son un corte; una ingesta de pagos acepta
    movimientos de un periodo, y un cliente puede tener muchos. Por eso es su propia entidad, con su
    identificador publico, su artefacto, sus rechazos y su trabajo en la cola. Lo que acepta es su
    dataset conformado, con todos sus movimientos validos, sin deduplicar: la conciliacion es de un
    motor posterior.
    """

    __tablename__ = "ingesta_pagos"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["artefacto_fuente_id"], ["artefacto_fuente.id"], name="fk_pagos_artefacto"
        ),
        # El mismo archivo se acepta una sola vez, y a lo mas una ingesta lo procesa a la vez. Lo
        # garantiza la base, como en las corridas.
        sa.Index(
            "ux_ingesta_pagos_firma_publicada",
            "firma",
            unique=True,
            postgresql_where=sa.text("estado = 'EXITOSA'"),
        ),
        sa.Index(
            "ux_ingesta_pagos_firma_en_proceso",
            "firma",
            unique=True,
            postgresql_where=sa.text("estado = 'EN_PROCESO'"),
        ),
        sa.CheckConstraint(
            "filas_leidas >= 0 AND filas_validas >= 0 AND filas_rechazadas >= 0 "
            "AND filas_leidas = filas_validas + filas_rechazadas",
            name=conv("ck_pagos_conteos"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    pagos_run_id: UUID = Field(default_factory=uuid4, unique=True)
    """Identificador publico. No se llama run_id: ese es el de las corridas de cartera."""
    artefacto_fuente_id: int = Field(index=True)
    """El archivo recibido, en el almacen de artefactos. Su llave esta en __table_args__."""
    origen: str = Field(description="Nombre del archivo recibido")
    firma: str = Field(max_length=64, description="SHA-256 del archivo tal como llego")
    version_contrato: str = Field(max_length=32)
    estado: EstadoIngestaPagos = Field(
        default=EstadoIngestaPagos.EN_PROCESO,
        sa_type=sa.Enum(
            EstadoIngestaPagos,
            name="estado_ingesta_pagos",
            native_enum=False,
            create_constraint=True,
            length=12,
        ),
    )
    despacho_id: str = Field(default_factory=lambda: config.despacho_id, max_length=32)
    cartera_id: str = Field(default_factory=lambda: config.cartera_id, max_length=32)
    tolerancia_rechazo: float
    """La fraccion de movimientos rechazados con que se juzgo. Por omision 0: todo o nada."""
    iniciada_en: datetime = Field(default_factory=ahora, sa_type=sa.DateTime(timezone=True))
    terminada_en: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    filas_leidas: int = Field(default=0, sa_type=sa.BigInteger)
    filas_validas: int = Field(default=0, sa_type=sa.BigInteger)
    filas_rechazadas: int = Field(default=0, sa_type=sa.BigInteger)
    firma_contenido: str | None = Field(default=None, max_length=64)
    """La firma de sus movimientos validos en forma canonica. Cuenta los repetidos."""
    detalle: str | None = None


class RechazoPago(SQLModel, table=True):
    """Un movimiento que no cumplio pagos/v1: su fila, lo que traia y por que. Ninguno se oculta."""

    __tablename__ = "rechazo_pago"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["ingesta_pagos_id"], ["ingesta_pagos.id"], name="fk_rechazo_pago_ingesta"
        ),
        sa.UniqueConstraint("ingesta_pagos_id", "fila", name="uq_rechazo_pago_fila"),
    )

    id: int | None = Field(default=None, primary_key=True)
    ingesta_pagos_id: int
    fila: int = Field(description="Numero de fila en el archivo; el encabezado es la fila 1")
    valores: dict[str, str | None] = Field(sa_type=JSONB)
    motivos: list[dict[str, str]] = Field(sa_type=JSONB)


# --- el modelo historico --------------------------------------------------------------------------
#
# Desde la 0008. Es una proyeccion de los datasets conformados, paralela a la operacional: no la
# reemplaza, y ninguno de los motores v1 la lee. Guarda poco, a proposito: la identidad de cada
# cuenta a traves del tiempo, una fila por corte de la cartera, las variables historicas de cada
# cuenta en cada corte y cada movimiento de pagos/v1 tal como llego. Las 93 columnas de cada corte
# siguen en su Parquet, y cada fila de aqui dice de que fila de el salio.
#
# Las tablas que crecen con cada corte (snapshot_cuenta y pago_observado) no tienen un id propio:
# su llave primaria es su llave natural, que es igual de corta. Sus columnas van ordenadas por
# alineacion, las de 8 bytes primero, para que PostgreSQL no rellene bytes en cada fila.


class TipoFuenteHistoria(StrEnum):
    """De que fuente oficial es el dataset conformado que materializa una ejecucion historica."""

    CARTERA = "CARTERA"  # cartera/v2: un corte, con un snapshot por cuenta
    PAGOS = "PAGOS"  # pagos/v1: movimientos observados, uno por fila de la fuente


class EstadoHistoria(StrEnum):
    """En que quedo una ejecucion historica. Solo una EXITOSA publica algo."""

    EN_PROCESO = "EN_PROCESO"  # registrada; se esta materializando su dataset
    EXITOSA = "EXITOSA"  # publico su corte o sus pagos, o reconocio una fuente equivalente
    FALLIDA = "FALLIDA"  # no publico nada: un conflicto de corte, datos que no cuadran o un error


class ResultadoHistoria(StrEnum):
    """Como termino una ejecucion historica, en el vocabulario de historia/v1. Se guarda como texto
    y sin CHECK, como el vocabulario de las reglas de los motores: otra version puede traer otro."""

    CORTE_PUBLICADO = "CORTE_PUBLICADO"
    """EXITOSA: publico un corte canonico nuevo, con sus snapshots."""
    FUENTE_EQUIVALENTE = "FUENTE_EQUIVALENTE"
    """EXITOSA: el corte ya existia con la misma firma de contenido; no se duplico nada."""
    PAGOS_PUBLICADOS = "PAGOS_PUBLICADOS"
    """EXITOSA: publico un pago observado por cada fila del dataset de pagos."""
    CORTE_CANONICO_CONFLICTIVO = "CORTE_CANONICO_CONFLICTIVO"
    """FALLIDA: ya hay un corte de esa fecha con otra firma de contenido. El corte no cambia."""
    DATOS_INCONSISTENTES = "DATOS_INCONSISTENTES"
    """FALLIDA: el Parquet no es lo que su dataset dice, o los conteos no cuadran."""
    ARTEFACTO_CORRUPTO = "ARTEFACTO_CORRUPTO"
    """FALLIDA: los bytes del Parquet ya no son los de su SHA-256."""
    VERSION_NO_SOPORTADA = "VERSION_NO_SOPORTADA"
    """FALLIDA: la ejecucion pide una version del modelo que este servicio no sabe materializar."""
    YA_MATERIALIZADA = "YA_MATERIALIZADA"
    """FALLIDA: otra ejecucion del mismo dataset y version publico primero."""
    ERROR_INTERNO = "ERROR_INTERNO"
    """FALLIDA: un error inesperado; el detalle esta en la bitacora."""
    INTENTOS_AGOTADOS = "INTENTOS_AGOTADOS"
    """FALLIDA: su trabajo agoto los intentos de la cola sin que terminara."""


class CuentaCanonica(SQLModel, table=True):
    """La identidad longitudinal de una cuenta: un CLIENTE_UNICO de una cartera, a traves de todos
    sus cortes.

    No es `Cuenta`, que es la foto operacional de una sola corrida y la que leen los motores v1. Ni
    es una persona ni un credito: CLIENTE_UNICO es el identificador fuente de la cuenta, y ninguna
    fuente dice todavia que dos cuentas sean de la misma persona. Nace la primera vez que un corte
    canonico trae su CLIENTE_UNICO; un pago, por si solo, no la crea. Sus cortes son sus snapshots.
    """

    __tablename__ = "cuenta_canonica"
    __table_args__ = (
        sa.UniqueConstraint(
            "despacho_id", "cartera_id", "cliente_unico", name="uq_cuenta_canonica_clave"
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    cuenta_id: UUID = Field(unique=True)
    """Identificador publico. Es determinista: sale de despacho, cartera y CLIENTE_UNICO (ver
    `historia.identidad`), asi que reconstruir la historia da el mismo, en cualquier orden."""
    despacho_id: str = Field(max_length=32)
    cartera_id: str = Field(max_length=32)
    cliente_unico: str = Field(max_length=20)
    """El identificador fuente de la cuenta, con la forma de cartera/v2."""
    creada_en: datetime = Field(default_factory=ahora, sa_type=sa.DateTime(timezone=True))
    """Cuando se registro en la base. No es su primera observacion, que es la fecha de su primer
    corte, ni su originacion, que ninguna fuente dice."""


class CorteCanonico(SQLModel, table=True):
    """La fotografia canonica de una cartera en una fecha: una sola por despacho, cartera y fecha de
    corte.

    La produce el primer dataset conformado de cartera/v2 de esa fecha que se materializa, y sus
    snapshots salen de el. Otro dataset de la misma fecha con la misma firma de contenido (la misma
    cartera en otro formato) es una fuente equivalente: no crea otro corte ni duplica nada. Uno con
    otra firma es un conflicto: este corte no cambia, porque la historia no se sobrescribe.
    """

    __tablename__ = "corte_canonico"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["dataset_conformado_id"], ["dataset_conformado.id"], name="fk_corte_dataset"
        ),
        sa.UniqueConstraint(
            "despacho_id", "cartera_id", "fecha_corte", name="uq_corte_canonico_fecha"
        ),
        # Un dataset produce a lo mas un corte.
        sa.UniqueConstraint("dataset_conformado_id", name="uq_corte_canonico_dataset"),
        # Lo que referencia cada snapshot: el corte junto con su fecha. Asi la base garantiza que la
        # fecha que el snapshot repite, para su indice, es la de su corte.
        sa.UniqueConstraint("id", "fecha_corte", name="uq_corte_canonico_id_fecha"),
        sa.CheckConstraint("firma_contenido ~ '^[0-9a-f]{64}$'", name=conv("ck_corte_firma")),
        sa.CheckConstraint("cuentas > 0", name=conv("ck_corte_cuentas")),
    )

    id: int | None = Field(default=None, primary_key=True)
    corte_id: UUID = Field(unique=True)
    """Identificador publico, determinista: sale de despacho, cartera y fecha de corte."""
    despacho_id: str = Field(max_length=32)
    cartera_id: str = Field(max_length=32)
    fecha_corte: date
    firma_contenido: str = Field(max_length=64)
    """La firma del contenido del dataset que lo produjo: la de la cartera, no la del archivo."""
    version_modelo: str = Field(max_length=32)
    """Con que version del modelo historico se materializo: historia/v1."""
    cuentas: int = Field(sa_type=sa.BigInteger)
    """Cuantos snapshots tiene: uno por cuenta del corte."""
    dataset_conformado_id: int
    """El dataset conformado que lo produjo: la evidencia de cada uno de sus snapshots. Su llave
    foranea y su unicidad estan en __table_args__."""
    creado_en: datetime = Field(default_factory=ahora, sa_type=sa.DateTime(timezone=True))


class EjecucionHistoria(SQLModel, table=True):
    """Una materializacion versionada de un dataset conformado en el modelo historico.

    Es una entidad, como las ejecuciones de los motores, porque tiene que existir aunque no publique
    nada: un conflicto de corte o una fuente equivalente se auditan aqui. Nace EN_PROCESO, junto con
    su trabajo HISTORIA, en la misma transaccion que publica el dataset conformado.
    """

    __tablename__ = "ejecucion_historia"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["dataset_conformado_id"], ["dataset_conformado.id"], name="fk_historia_dataset"
        ),
        sa.ForeignKeyConstraint(
            ["corte_canonico_id"], ["corte_canonico.id"], name="fk_historia_corte"
        ),
        # Un dataset se materializa con exito una sola vez por version del modelo, y a lo mas un
        # intento activo a la vez. Lo garantiza la base y no solo el codigo.
        sa.Index(
            "ux_ejecucion_historia_exitosa",
            "dataset_conformado_id",
            "version_modelo",
            unique=True,
            postgresql_where=sa.text("estado = 'EXITOSA'"),
        ),
        sa.Index(
            "ux_ejecucion_historia_en_proceso",
            "dataset_conformado_id",
            "version_modelo",
            unique=True,
            postgresql_where=sa.text("estado = 'EN_PROCESO'"),
        ),
        sa.CheckConstraint(
            "registros_leidos >= 0 AND registros_publicados >= 0 "
            "AND registros_publicados <= registros_leidos",
            name=conv("ck_historia_conteos"),
        ),
        # Solo una ejecucion de cartera que termino EXITOSA tiene corte: el que publico, o el que ya
        # existia con la misma firma. Una de pagos nunca tiene.
        sa.CheckConstraint(
            "(corte_canonico_id IS NOT NULL) = (tipo_fuente = 'CARTERA' AND estado = 'EXITOSA')",
            name=conv("ck_historia_corte"),
        ),
        # Una ejecucion terminada dice como termino, con un codigo estable; una EN_PROCESO, todavia
        # no.
        sa.CheckConstraint(
            "(estado = 'EN_PROCESO') = (resultado IS NULL)", name=conv("ck_historia_resultado")
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    historia_run_id: UUID = Field(default_factory=uuid4, unique=True)
    """Identificador publico."""
    dataset_conformado_id: int = Field(index=True)
    """El dataset que se materializa. Su llave foranea esta en __table_args__. Lleva su propio
    indice para encontrar todas las ejecuciones de un dataset, en cualquier estado."""
    tipo_fuente: TipoFuenteHistoria = Field(
        sa_type=sa.Enum(
            TipoFuenteHistoria,
            name="tipo_fuente_historia",
            native_enum=False,
            create_constraint=True,
            length=12,
        )
    )
    version_modelo: str = Field(max_length=32)
    """Con que version del modelo historico se materializa: historia/v1."""
    estado: EstadoHistoria = Field(
        default=EstadoHistoria.EN_PROCESO,
        sa_type=sa.Enum(
            EstadoHistoria,
            name="estado_historia",
            native_enum=False,
            create_constraint=True,
            length=12,
        ),
    )
    resultado: str | None = Field(default=None, max_length=40)
    """Como termino, con un codigo estable: CORTE_PUBLICADO, FUENTE_EQUIVALENTE, PAGOS_PUBLICADOS,
    CORTE_CANONICO_CONFLICTIVO... Es el vocabulario de la version del modelo, sin CHECK, como el de
    las reglas de los motores."""
    corte_canonico_id: int | None = None
    """El corte que publico o al que es equivalente, en una de cartera EXITOSA."""
    registros_leidos: int = Field(default=0, sa_type=sa.BigInteger)
    """Cuantos registros del dataset leyo. Cero si no hizo falta leerlo."""
    registros_publicados: int = Field(default=0, sa_type=sa.BigInteger)
    """Cuantos snapshots o pagos observados publico. Cero si no publico nada."""
    iniciada_en: datetime = Field(default_factory=ahora, sa_type=sa.DateTime(timezone=True))
    terminada_en: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    detalle: str | None = None
    """Que paso, para una persona."""


class SnapshotCuenta(SQLModel, table=True):
    """Una cuenta canonica observada en un corte canonico, con sus variables historicas.

    Guarda solo las variables con significado claro y uso transversal: saldos, atraso, producto,
    estrategia, canal, ultimo pago, geografia, plan y promesa. Ninguna PII: el nombre, el domicilio,
    los telefonos, el aval y las referencias siguen en el dataset conformado, y `source_row` lleva a
    la fila. Cada valor tiene el tipo que tiene en el Parquet conformado.

    Un snapshot no se actualiza nunca: un corte nuevo es otra fila. La historia de una cuenta se
    lee por (cuenta_canonica_id, fecha_corte), y por eso el snapshot repite la fecha de su corte; la
    llave foranea compuesta garantiza que es la misma.
    """

    __tablename__ = "snapshot_cuenta"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["corte_canonico_id", "fecha_corte"],
            ["corte_canonico.id", "corte_canonico.fecha_corte"],
            name="fk_snapshot_corte",
        ),
        sa.ForeignKeyConstraint(
            ["cuenta_canonica_id"], ["cuenta_canonica.id"], name="fk_snapshot_cuenta"
        ),
        # La historia de una cuenta, en orden de corte. PostgreSQL recorre un btree en los dos
        # sentidos: con la cuenta fija, este indice sirve igual a ORDER BY fecha_corte DESC.
        sa.Index("ix_snapshot_cuenta_historia", "cuenta_canonica_id", "fecha_corte"),
    )

    # La llave primaria es la natural: una cuenta, a lo mas una vez por corte.
    corte_canonico_id: int = Field(primary_key=True)
    cuenta_canonica_id: int = Field(primary_key=True)
    dias_atraso: int = Field(sa_type=sa.BigInteger)
    atraso_maximo: int | None = Field(default=None, sa_type=sa.BigInteger)
    semanas_atraso: int | None = Field(default=None, sa_type=sa.BigInteger)
    pagos_recibidos: int | None = Field(default=None, sa_type=sa.BigInteger)
    fecha_corte: date
    fecha_ultimo_pago: date | None = None
    source_row: int
    """La fila del archivo original de la que salio, como `_source_row` en el Parquet."""
    saldo_total: Decimal = Field(max_digits=14, decimal_places=2)
    saldo: Decimal | None = Field(default=None, max_digits=14, decimal_places=2)
    moratorios: Decimal | None = Field(default=None, max_digits=14, decimal_places=2)
    saldo_atrasado: Decimal | None = Field(default=None, max_digits=14, decimal_places=2)
    saldo_requerido: Decimal | None = Field(default=None, max_digits=14, decimal_places=2)
    pago_normal: Decimal | None = Field(default=None, max_digits=14, decimal_places=2)
    imp_ultimo_pago: Decimal | None = Field(default=None, max_digits=14, decimal_places=2)
    monto_plan: Decimal | None = Field(default=None, max_digits=14, decimal_places=2)
    monto_promesa_pago: Decimal | None = Field(default=None, max_digits=14, decimal_places=2)
    producto: str = Field(max_length=20)
    canal: str = Field(max_length=20)
    cve_entidad: str = Field(max_length=2)
    cve_municipio: str = Field(max_length=3)
    estrategia: str | None = None
    estatus_plan: str | None = None
    estatus_promesa_pago: str | None = None
    source_sheet: str | None = Field(default=None, max_length=255)
    """Su hoja o su miembro del zip, como `_source_sheet`; vacia en un csv suelto."""


class PagoObservado(SQLModel, table=True):
    """Una fila aceptada de pagos/v1, tal como llego: un movimiento observado.

    Una fila de la fuente es un pago observado, y ninguno se deduplica: dos filas identicas son dos
    observaciones, y el mismo movimiento en dos archivos tambien. No es un pago conciliado, aplicado
    ni atribuido; lo que el motor de pagos concluye de el vive en ResultadoPagoObservado, y el pago
    no se toca nunca. Conserva sus 23 campos con el tipo que tienen en el Parquet conformado, y de
    que dataset y de que fila salio.

    No apunta a ninguna CuentaCanonica. Se relaciona con ella por despacho, cartera y CLIENTE_UNICO
    al consultar: asi un pago anterior a la primera cartera que trae a su cliente, o de un cliente
    que nunca aparece (SIN_CUENTA_OBSERVADA), se conserva igual y no hay que volver a enlazar nada.
    """

    __tablename__ = "pago_observado"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["dataset_conformado_id"], ["dataset_conformado.id"], name="fk_pago_observado_dataset"
        ),
        sa.ForeignKeyConstraint(
            ["ingesta_pagos_id"], ["ingesta_pagos.id"], name="fk_pago_observado_ingesta"
        ),
        # Los pagos de una cuenta, en orden de recepcion; el btree se recorre en los dos sentidos.
        sa.Index(
            "ix_pago_observado_cuenta",
            "despacho_id",
            "cartera_id",
            "cliente_unico",
            "fecha_recepcion",
        ),
        # Desde la 0009, los pagos de una ventana del motor de pagos: un mes de recepcion, sin
        # recorrer los pagos de toda la historia.
        sa.Index("ix_pago_observado_recepcion", "despacho_id", "cartera_id", "fecha_recepcion"),
        # Y solo los negativos, que son pocos: de ellos sale el contexto de los reversos.
        sa.Index(
            "ix_pago_observado_negativo",
            "despacho_id",
            "cartera_id",
            "fecha_recepcion",
            postgresql_where=sa.text("recuperacion_por_gestion < 0"),
        ),
    )

    # La llave primaria es la natural: una fila del dataset, una observacion.
    dataset_conformado_id: int = Field(primary_key=True)
    source_row: int = Field(primary_key=True)
    """La fila del archivo original, como `_source_row` en el Parquet."""
    anio: int | None = Field(default=None, sa_type=sa.BigInteger)
    semana: int | None = Field(default=None, sa_type=sa.BigInteger)
    dias_de_atraso: int | None = Field(default=None, sa_type=sa.BigInteger)
    semanas_de_atraso: int | None = Field(default=None, sa_type=sa.BigInteger)
    fecha_recepcion: datetime = Field(sa_type=sa.DateTime(timezone=False))
    """Hora local de la fuente, sin zona horaria, como en pagos/v1."""
    fecha_de_gestion: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=False))
    porcentaje_comision: float | None = None
    ingesta_pagos_id: int
    """La ingesta que lo acepto; su llave foranea esta en __table_args__."""
    pago_observado_id: UUID
    """Identificador publico, determinista: sale del dataset y la fila, que ya son unicos. No lleva
    indice propio: ninguna consulta de v0.7 lo busca."""
    recuperacion_por_gestion: Decimal = Field(max_digits=14, decimal_places=2)
    cargos_automaticos: Decimal | None = Field(default=None, max_digits=14, decimal_places=2)
    captacion: Decimal | None = Field(default=None, max_digits=14, decimal_places=2)
    cobranza_total: Decimal | None = Field(default=None, max_digits=14, decimal_places=2)
    monto_comision: Decimal | None = Field(default=None, max_digits=14, decimal_places=2)
    despacho_id: str = Field(max_length=32)
    cartera_id: str = Field(max_length=32)
    cliente_unico: str = Field(max_length=20)
    territorio: str | None = None
    zona: str | None = None
    segmento: str | None = None
    gerencia: str | None = None
    tipo_cartera: str | None = None
    producto: str | None = None
    campania: str | None = None
    gestor: str | None = None
    plan_de_pago: str | None = None
    concepto_calculo: str | None = None
    source_sheet: str | None = Field(default=None, max_length=255)


# --- el motor de pagos ----------------------------------------------------------------------------
#
# Desde la 0009. Interpreta los pagos observados sin tocarlos: cada ejecucion lee los de una ventana
# (un mes de recepcion de un despacho y una cartera), y publica, todo o nada, que concluyo de
# cada uno (ResultadoPagoObservado) y los movimientos economicos que considera distintos
# (MovimientoEconomicoCanonico). Una ejecucion nueva de la misma ventana no reescribe la anterior:
# la interpretacion vigente es la de la EXITOSA mas reciente, y las demas quedan como historia. El
# vocabulario (clasificaciones, tipos, signos y estados de conciliacion) es el de la version del
# motor y se guarda como texto sin CHECK, como el de las reglas de los demas motores.


class EstadoMotorPagos(StrEnum):
    """En que quedo una ejecucion del motor de pagos. Solo una EXITOSA publica algo."""

    EN_PROCESO = "EN_PROCESO"  # registrada; se esta interpretando su ventana
    EXITOSA = "EXITOSA"  # publico un resultado por observacion y sus movimientos canonicos
    FALLIDA = "FALLIDA"  # no publico nada


class ResultadoMotorPagos(StrEnum):
    """Como termino una ejecucion del motor de pagos, en el vocabulario de motor-pagos/v1."""

    INTERPRETACION_PUBLICADA = "INTERPRETACION_PUBLICADA"
    """EXITOSA: publico la interpretacion de su ventana."""
    YA_INTERPRETADA = "YA_INTERPRETADA"
    """FALLIDA: otra ejecucion ya interpreto exactamente las mismas entradas con esta version."""
    VERSION_NO_SOPORTADA = "VERSION_NO_SOPORTADA"
    """FALLIDA: la ejecucion pide una version del motor que este servicio no sabe ejecutar."""
    DATOS_INCONSISTENTES = "DATOS_INCONSISTENTES"
    """FALLIDA: lo publicado no cuadra con lo leido; no se publico nada."""
    ERROR_INTERNO = "ERROR_INTERNO"
    """FALLIDA: un error inesperado; el detalle esta en la bitacora."""
    INTENTOS_AGOTADOS = "INTENTOS_AGOTADOS"
    """FALLIDA: su trabajo agoto los intentos de la cola sin que terminara."""


CLASIFICADAS_EN_SUS_CLASES = (
    "observaciones_clasificadas = primarios + duplicados_exactos + coincidencias_ambiguas "
    "+ reversos + posibles_reversos + no_conciliados"
)
"""Cada observacion clasificada tiene exactamente una clasificacion."""

CONTEOS_DEL_MOTOR = (
    "observaciones_leidas >= 0 AND observaciones_contexto >= 0 AND primarios >= 0 "
    "AND duplicados_exactos >= 0 AND coincidencias_ambiguas >= 0 AND reversos >= 0 "
    "AND posibles_reversos >= 0 AND no_conciliados >= 0 AND sin_cuenta_observada >= 0 "
    "AND grupos_exactos >= 0 AND grupos_legacy >= 0 AND grupos_ambiguos >= 0 "
    "AND observaciones_en_grupos_legacy >= 0 AND pagos_anulados >= 0 "
    "AND sin_cuenta_observada <= observaciones_clasificadas AND pagos_anulados <= primarios"
)


class EjecucionMotorPagos(SQLModel, table=True):
    """Una interpretacion versionada de los pagos observados de una ventana: un despacho, una
    cartera y un mes de recepcion, [periodo_desde, periodo_hasta).

    Es una entidad, como las ejecuciones de los demas motores, porque tiene que existir aunque no
    publique nada. Su firma de entrada resume exactamente que leyo: las observaciones de la ventana,
    las de su contexto y cuales tenian cuenta canonica. La base garantiza a lo mas una EXITOSA por
    ventana, version y firma de entrada, y a lo mas una EN_PROCESO por ventana y version: la misma
    interpretacion no se publica dos veces, y una ventana no se interpreta dos veces a la vez.
    Cuando llegan pagos nuevos, otra ejecucion de la misma ventana, con otra firma, publica la
    interpretacion nueva; la anterior no se toca.
    """

    __tablename__ = "ejecucion_motor_pagos"
    __table_args__ = (
        sa.Index(
            "ux_ejecucion_motor_pagos_exitosa",
            "despacho_id",
            "cartera_id",
            "version_motor",
            "periodo_desde",
            "firma_entrada",
            unique=True,
            postgresql_where=sa.text("estado = 'EXITOSA'"),
        ),
        sa.Index(
            "ux_ejecucion_motor_pagos_en_proceso",
            "despacho_id",
            "cartera_id",
            "version_motor",
            "periodo_desde",
            unique=True,
            postgresql_where=sa.text("estado = 'EN_PROCESO'"),
        ),
        sa.CheckConstraint("periodo_desde < periodo_hasta", name=conv("ck_motor_pagos_periodo")),
        # La ventana de motor-pagos/v1 es un mes calendario. Otra version puede usar otra.
        sa.CheckConstraint(
            "version_motor <> 'motor-pagos/v1' OR (extract(day FROM periodo_desde) = 1 "
            "AND periodo_hasta = (periodo_desde + interval '1 month')::date)",
            name=conv("ck_motor_pagos_mes"),
        ),
        sa.CheckConstraint(
            "(estado = 'EN_PROCESO') = (resultado IS NULL)", name=conv("ck_motor_pagos_resultado")
        ),
        sa.CheckConstraint(
            "(firma_entrada IS NULL OR firma_entrada ~ '^[0-9a-f]{64}$') "
            "AND (estado <> 'EXITOSA' OR firma_entrada IS NOT NULL)",
            name=conv("ck_motor_pagos_firma"),
        ),
        sa.CheckConstraint(CONTEOS_DEL_MOTOR, name=conv("ck_motor_pagos_conteos")),
        sa.CheckConstraint(CLASIFICADAS_EN_SUS_CLASES, name=conv("ck_motor_pagos_clases")),
        # Cada movimiento lo funda exactamente una observacion: su representante.
        sa.CheckConstraint(
            "movimientos_canonicos = primarios + reversos + posibles_reversos",
            name=conv("ck_motor_pagos_movimientos"),
        ),
        # Una EXITOSA clasifico cada observacion que leyo; las demas no publicaron nada.
        sa.CheckConstraint(
            "(estado = 'EXITOSA' AND observaciones_clasificadas = observaciones_leidas) "
            "OR (estado <> 'EXITOSA' AND observaciones_clasificadas = 0 "
            "AND movimientos_canonicos = 0)",
            name=conv("ck_motor_pagos_publicacion"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    motor_pagos_run_id: UUID = Field(default_factory=uuid4, unique=True)
    """Identificador publico. Identifica un intento, como los demas *_run_id, y por eso es
    aleatorio; los movimientos, que identifican hechos, tienen identificadores deterministas."""
    version_motor: str = Field(max_length=32)
    """Con que version del motor se interpreto: motor-pagos/v1. Sin valor por omision en la base."""
    despacho_id: str = Field(max_length=32)
    cartera_id: str = Field(max_length=32)
    periodo_desde: date
    """El primer dia de la ventana, inclusive: recibidos desde periodo_desde."""
    periodo_hasta: date
    """El dia despues del ultimo, exclusive: recibidos antes de periodo_hasta."""
    estado: EstadoMotorPagos = Field(
        default=EstadoMotorPagos.EN_PROCESO,
        sa_type=sa.Enum(
            EstadoMotorPagos,
            name="estado_motor_pagos",
            native_enum=False,
            create_constraint=True,
            length=12,
        ),
    )
    resultado: str | None = Field(default=None, max_length=40)
    """Como termino: INTERPRETACION_PUBLICADA, YA_INTERPRETADA, ERROR_INTERNO..."""
    firma_entrada: str | None = Field(default=None, max_length=64)
    """SHA-256 de lo que leyo, en forma canonica; vacia hasta que lo lee."""
    iniciada_en: datetime = Field(default_factory=ahora, sa_type=sa.DateTime(timezone=True))
    terminada_en: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    observaciones_leidas: int = Field(default=0, sa_type=sa.BigInteger)
    """Los pagos observados de la ventana: los que reciben un resultado."""
    observaciones_contexto: int = Field(default=0, sa_type=sa.BigInteger)
    """Los de fuera de la ventana que leyo para decidir sus reversos; no reciben resultado aqui."""
    observaciones_clasificadas: int = Field(default=0, sa_type=sa.BigInteger)
    movimientos_canonicos: int = Field(default=0, sa_type=sa.BigInteger)
    primarios: int = Field(default=0, sa_type=sa.BigInteger)
    duplicados_exactos: int = Field(default=0, sa_type=sa.BigInteger)
    coincidencias_ambiguas: int = Field(default=0, sa_type=sa.BigInteger)
    reversos: int = Field(default=0, sa_type=sa.BigInteger)
    posibles_reversos: int = Field(default=0, sa_type=sa.BigInteger)
    no_conciliados: int = Field(default=0, sa_type=sa.BigInteger)
    sin_cuenta_observada: int = Field(default=0, sa_type=sa.BigInteger)
    """Observaciones de un CLIENTE_UNICO sin cuenta canonica cuando se interpretaron."""
    grupos_exactos: int = Field(default=0, sa_type=sa.BigInteger)
    """Grupos de dos o mas observaciones identicas."""
    grupos_legacy: int = Field(default=0, sa_type=sa.BigInteger)
    """Grupos de dos o mas observaciones con la misma llave historica."""
    grupos_ambiguos: int = Field(default=0, sa_type=sa.BigInteger)
    """De esos, los que juntan observaciones que no son identicas."""
    observaciones_en_grupos_legacy: int = Field(default=0, sa_type=sa.BigInteger)
    pagos_anulados: int = Field(default=0, sa_type=sa.BigInteger)
    """Pagos de la ventana que anulo un reverso, de esta ventana o de la siguiente."""
    recuperacion_bruta_interpretada: Decimal = Field(
        default=Decimal("0.00"), max_digits=24, decimal_places=2
    )
    recuperacion_neta_interpretada: Decimal = Field(
        default=Decimal("0.00"), max_digits=24, decimal_places=2
    )
    importe_ambiguo_observado: Decimal = Field(
        default=Decimal("0.00"), max_digits=24, decimal_places=2
    )
    """Lo que reportan las observaciones ambiguas, sumado tal como llego: puede contar dos veces el
    mismo pago, y por eso no entra en ninguna recuperacion."""
    detalle: str | None = None


class MovimientoEconomicoCanonico(SQLModel, table=True):
    """Un movimiento economico distinto, como lo interpreta una ejecucion del motor de pagos.

    No viene del acreedor: lo funda un grupo de observaciones identicas, y su `movimiento_id` sale
    de la version del motor y de la firma exacta del grupo, asi que reconstruir da el mismo y la
    misma ventana interpretada otra vez tambien. Por eso es unico por ejecucion y no en toda la
    tabla: cada interpretacion de una ventana tiene sus propios movimientos, y la vigente es la de
    su ejecucion EXITOSA mas reciente. Sus observaciones son los ResultadoPagoObservado que apuntan
    a el.

    Un reverso apunta a su original, y un pago anulado a su reverso, por su `movimiento_id`: pueden
    estar en otra ventana, y el identificador es el mismo en cualquier interpretacion.
    """

    __tablename__ = "movimiento_economico_canonico"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["ejecucion_motor_pagos_id"],
            ["ejecucion_motor_pagos.id"],
            name="fk_movimiento_ejecucion",
        ),
        sa.ForeignKeyConstraint(
            ["cuenta_canonica_id"], ["cuenta_canonica.id"], name="fk_movimiento_cuenta"
        ),
        # Un movimiento a lo mas una vez por ejecucion; tambien es el indice de sus movimientos.
        sa.UniqueConstraint(
            "ejecucion_motor_pagos_id", "movimiento_id", name="uq_movimiento_ejecucion"
        ),
        # GET /movimientos/{movimiento_id}: el mismo movimiento en cada interpretacion de su
        # ventana.
        sa.Index("ix_movimiento_movimiento_id", "movimiento_id"),
        # Los movimientos de una cuenta, en orden de recepcion, en los dos sentidos.
        sa.Index(
            "ix_movimiento_cuenta", "despacho_id", "cartera_id", "cliente_unico", "fecha_recepcion"
        ),
        sa.CheckConstraint("observaciones >= 1", name=conv("ck_movimiento_observaciones")),
        sa.CheckConstraint("monto_reportado <> 0", name=conv("ck_movimiento_monto")),
        sa.CheckConstraint("octet_length(firma_exacta) = 32", name=conv("ck_movimiento_firma")),
    )

    # Las columnas de 8 bytes primero, para que PostgreSQL no rellene bytes en cada fila.
    id: int | None = Field(default=None, primary_key=True, sa_type=sa.BigInteger)
    observaciones: int = Field(sa_type=sa.BigInteger)
    """Cuantas observaciones identicas lo sustentan: su representante y sus duplicados exactos."""
    fecha_recepcion: datetime = Field(sa_type=sa.DateTime(timezone=False))
    """Hora local de la fuente, sin zona horaria, como en pagos/v1."""
    creado_en: datetime = Field(default_factory=ahora, sa_type=sa.DateTime(timezone=True))
    ejecucion_motor_pagos_id: int
    """La ejecucion que lo publico; su llave foranea esta en __table_args__."""
    cuenta_canonica_id: int | None = None
    """La cuenta con la que se concilio, si habia una cuando se interpreto."""
    movimiento_id: UUID
    """Identificador publico, determinista. No es un identificador del acreedor."""
    movimiento_original_id: UUID | None = None
    """En un REVERSO, el movimiento que revierte."""
    anulado_por_movimiento_id: UUID | None = None
    """En un PAGO anulado, el REVERSO que lo anula."""
    monto_reportado: Decimal = Field(max_digits=14, decimal_places=2)
    """El importe recuperado de sus observaciones, con su signo, tal como llego."""
    version_motor: str = Field(max_length=32)
    despacho_id: str = Field(max_length=32)
    cartera_id: str = Field(max_length=32)
    cliente_unico: str = Field(max_length=20)
    signo_economico: str = Field(max_length=8)
    """SUMA o RESTA: lo que hace con la recuperacion."""
    tipo_movimiento: str = Field(max_length=24)
    """PAGO, REVERSO o POSIBLE_REVERSO."""
    estado_conciliacion: str = Field(max_length=24)
    """CONCILIADO_CUENTA o SIN_CUENTA_OBSERVADA, cuando se interpreto."""
    firma_exacta: bytes = Field(sa_type=sa.LargeBinary)
    """La firma exacta de sus observaciones: de ella sale su movimiento_id."""


class ResultadoPagoObservado(SQLModel, table=True):
    """Lo que una ejecucion del motor de pagos concluyo de un pago observado, y por que.

    Cada observacion de la ventana tiene exactamente uno por ejecucion: su llave primaria es la
    ejecucion y la llave de la observacion. El pago observado no cambia nunca; esto es lo que el
    motor dice de el. `motivos` explica la clasificacion: no sustituye ninguna columna.
    """

    __tablename__ = "resultado_pago_observado"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["ejecucion_motor_pagos_id"],
            ["ejecucion_motor_pagos.id"],
            name="fk_resultado_pago_ejecucion",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_conformado_id", "source_row"],
            ["pago_observado.dataset_conformado_id", "pago_observado.source_row"],
            name="fk_resultado_pago_observado",
        ),
        sa.ForeignKeyConstraint(
            ["movimiento_economico_canonico_id"],
            ["movimiento_economico_canonico.id"],
            name="fk_resultado_pago_movimiento",
        ),
        # Las observaciones de un movimiento.
        sa.Index("ix_resultado_pago_movimiento", "movimiento_economico_canonico_id"),
        sa.CheckConstraint(
            "octet_length(firma_exacta) = 32 AND octet_length(firma_legacy) = 32",
            name=conv("ck_resultado_pago_firmas"),
        ),
    )

    # La llave primaria es la natural: una observacion, una vez por ejecucion.
    ejecucion_motor_pagos_id: int = Field(primary_key=True)
    dataset_conformado_id: int = Field(primary_key=True)
    source_row: int = Field(primary_key=True)
    movimiento_economico_canonico_id: int | None = Field(default=None, sa_type=sa.BigInteger)
    """El movimiento que funda o del que es copia; vacio si no funda ninguno."""
    movimiento_relacionado_id: UUID | None = None
    """En un REVERSO, su original; en un pago anulado, su reverso. Por movimiento_id."""
    clasificacion: str = Field(max_length=24)
    estado_conciliacion: str = Field(max_length=24)
    firma_exacta: bytes = Field(sa_type=sa.LargeBinary)
    firma_legacy: bytes = Field(sa_type=sa.LargeBinary)
    motivos: list[dict] = Field(sa_type=JSONB)
    """Por que: [{"codigo": ..., ...}], con los datos que lo explican."""


# --- el lifecycle de cobranza ---------------------------------------------------------------------
#
# Desde la 0010. Las acciones de cobranza que registra Motor Cartera: no son una fuente oficial del
# acreedor (esas siguen siendo dos, CARTERA y PAGOS) y no salen de los snapshots, porque lo que un
# corte dice de la promesa o del plan de una cuenta es una observacion de esa fuente, no un evento.
# Cada accion llega como un EventoLifecycle, con su momento de negocio (ocurrido_en) y el momento
# en que se registro (registrado_en), y su detalle vive aparte: la gestion, su visita, la promesa, o
# el convenio con sus cuotas. Nada se actualiza ni se borra: lo registrado por error se anula con
# otro evento, y la base rechaza un UPDATE o un DELETE con un trigger de la 0010, que tambien
# comprueba, al confirmar, las cuotas de cada convenio. El vocabulario (canal, medio, contacto y
# resultados) es el de lifecycle/v1 y la base lo exige: son datos de entrada, no conclusiones de un
# motor. Las tablas que crecen con la operacion llevan id BIGINT y sus columnas de 8 bytes primero.

CIERRES_DEL_LIFECYCLE = "('GESTION_ANULADA', 'PROMESA_CANCELADA', 'CONVENIO_CANCELADO')"
"""Los eventos que dejan sin efecto a otro: los unicos que llevan motivo."""

PATRON_LLAVE = "^[A-Za-z0-9._:-]{8,128}$"
"""La forma de una llave de idempotencia: la escoge el cliente, sin espacios ni datos personales."""

PATRON_ACTOR = "^[A-Za-z0-9._:-]{1,64}$"
"""La forma de una referencia opaca a un actor: un identificador, no un nombre ni un correo."""

COHERENCIA_DE_LA_GESTION = (
    "(resultado <> 'SIN_RESPUESTA' OR nivel_contacto IN ('SIN_CONTACTO', 'NO_APLICA')) "
    "AND (resultado NOT IN ('CONTACTO', 'RECHAZO', 'PROMESA', 'CONVENIO') "
    "OR nivel_contacto IN ('CONTACTO_TERCERO', 'CONTACTO_TITULAR')) "
    "AND (resultado <> 'VISITA_REALIZADA' OR canal = 'CAMPO') "
    "AND (canal <> 'CAMPO' OR resultado NOT IN ('SIN_RESPUESTA', 'CONTACTO')) "
    "AND (nivel_contacto <> 'NO_APLICA' OR canal IN ('DIGITAL', 'OTRO'))"
)
"""Las reglas de `lifecycle.reglas.incoherencias_de_gestion` que caben en una fila: un SIN_RESPUESTA
sin contacto, un resultado de compromiso o rechazo con contacto, VISITA_REALIZADA solo en CAMPO,
CAMPO sin SIN_RESPUESTA ni CONTACTO, y NO_APLICA solo en DIGITAL u OTRO."""

MEDIO_DEL_CANAL = (
    "medio IS NULL OR (canal = 'TELEFONICA' AND medio = 'LLAMADA') "
    "OR (canal = 'DIGITAL' AND medio IN ('SMS', 'WHATSAPP', 'EMAIL'))"
)


class EventoLifecycle(SQLModel, table=True):
    """Un evento operacional del lifecycle de cobranza: el sobre comun de toda accion registrada.

    Distingue dos tiempos que no se sustituyen: `ocurrido_en`, cuando paso en el negocio, y
    `registrado_en`, cuando Motor Cartera lo recibio, con el reloj de la base. Un evento tardio,
    como una gestion de hace dos semanas que se registra hoy, es valido: la historia de la cuenta se
    ordena por ocurrido_en y la auditoria muestra los dos.

    Toda escritura trae una llave de idempotencia: la misma llave con la misma peticion (su huella)
    es el mismo evento, y con otra peticion es un error. Lo garantiza un indice unico, no solo el
    codigo. Un evento que no es GESTION_REGISTRADA se refiere a uno anterior, el que anula, cancela
    o detalla, y la base admite a lo mas uno de cada tipo sobre el mismo evento.
    """

    __tablename__ = "evento_lifecycle"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["cuenta_canonica_id"], ["cuenta_canonica.id"], name="fk_evento_cuenta"
        ),
        sa.ForeignKeyConstraint(
            ["evento_relacionado_id"], ["evento_lifecycle.id"], name="fk_evento_relacionado"
        ),
        # Una llave, un evento, por cartera: dos peticiones simultaneas con la misma llave no
        # registran dos eventos.
        sa.UniqueConstraint(
            "despacho_id", "cartera_id", "idempotency_key", name="uq_evento_idempotencia"
        ),
        # A lo mas un evento de cada tipo sobre el mismo evento: una anulacion por gestion, una
        # promesa y un convenio por gestion, una cancelacion por promesa o por convenio. Es tambien
        # el indice con que se sabe si algo se anulo o se cancelo.
        sa.Index(
            "ux_evento_relacionado",
            "evento_relacionado_id",
            "tipo_evento",
            unique=True,
            postgresql_where=sa.text("evento_relacionado_id IS NOT NULL"),
        ),
        # La historia operacional de una cuenta por momento de negocio. El btree se recorre en los
        # dos sentidos: con la cuenta fija, sirve igual a ORDER BY ocurrido_en DESC.
        sa.Index("ix_evento_cuenta_ocurrido", "cuenta_canonica_id", "ocurrido_en"),
        sa.CheckConstraint(
            "(tipo_evento = 'GESTION_REGISTRADA') = (evento_relacionado_id IS NULL)",
            name=conv("ck_evento_relacionado"),
        ),
        sa.CheckConstraint(
            f"(motivo IS NOT NULL) = (tipo_evento IN {CIERRES_DEL_LIFECYCLE})",
            name=conv("ck_evento_motivo"),
        ),
        # Nada ocurre despues de registrarse; cinco minutos de tolerancia para el reloj del cliente.
        sa.CheckConstraint(
            "registrado_en - ocurrido_en >= interval '-5 minutes'", name=conv("ck_evento_tiempos")
        ),
        sa.CheckConstraint("octet_length(payload_hash) = 32", name=conv("ck_evento_huella")),
        sa.CheckConstraint(f"idempotency_key ~ '{PATRON_LLAVE}'", name=conv("ck_evento_llave")),
        sa.CheckConstraint(f"actor_ref ~ '{PATRON_ACTOR}'", name=conv("ck_evento_actor")),
    )

    id: int | None = Field(default=None, primary_key=True, sa_type=sa.BigInteger)
    ocurrido_en: datetime = Field(sa_type=sa.DateTime(timezone=True))
    """Cuando paso, en el negocio. Lo declara quien registra."""
    registrado_en: datetime = Field(sa_type=sa.DateTime(timezone=True))
    """Cuando Motor Cartera lo recibio: now() de la transaccion que lo registro, nunca del
    cliente."""
    evento_relacionado_id: int | None = Field(default=None, sa_type=sa.BigInteger)
    """El evento al que se refiere: el que anula, cancela o detalla. Vacio en GESTION_REGISTRADA."""
    evento_id: UUID = Field(default_factory=uuid4, unique=True)
    """Identificador publico. Identifica un registro operacional, como un *_run_id, y por eso es
    aleatorio: lo que tiene que ser reproducible es la historia que resulta, no el identificador."""
    cuenta_canonica_id: int
    tipo_evento: TipoEvento = Field(
        sa_type=sa.Enum(
            TipoEvento,
            name="tipo_evento_lifecycle",
            native_enum=False,
            create_constraint=True,
            length=24,
        )
    )
    origen_registro: OrigenRegistro = Field(
        sa_type=sa.Enum(
            OrigenRegistro,
            name="origen_registro",
            native_enum=False,
            create_constraint=True,
            length=12,
        )
    )
    """API o IMPORTACION: por donde entro. Ninguno es una fuente oficial del acreedor."""
    despacho_id: str = Field(max_length=32)
    cartera_id: str = Field(max_length=32)
    version_evento: str = Field(max_length=32)
    """Con que version del lifecycle se registro: lifecycle/v1."""
    idempotency_key: str = Field(max_length=128)
    """La llave con que lo pidio el cliente (cabecera Idempotency-Key, o la de su linea al
    importar)."""
    actor_ref: str | None = Field(default=None, max_length=64)
    """Quien lo registro, como referencia opaca. No es un GestorCanonico ni el Gestor de
    pagos/v1."""
    motivo: str | None = Field(default=None, max_length=500)
    """Por que se anula o se cancela. Solo en los eventos de cierre."""
    payload_hash: bytes = Field(sa_type=sa.LargeBinary)
    """SHA-256 de la peticion en forma canonica (`lifecycle.reglas.huella`)."""


class GestionCobranza(SQLModel, table=True):
    """Una gestion de cobranza: una accion concreta para cobrar o comunicarse con una cuenta.

    La registra un evento GESTION_REGISTRADA, y su momento de negocio es el de ese evento (se repite
    aqui para el indice de la historia de la cuenta). Separa por donde se intento (canal y medio),
    con quien se hablo (nivel de contacto) y que salio (resultado). No se actualiza: si se registro
    mal, un GESTION_ANULADA la anula, junto con su visita, su promesa o su convenio, y la gestion
    sigue aqui, auditable.
    """

    __tablename__ = "gestion_cobranza"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["evento_lifecycle_id"], ["evento_lifecycle.id"], name="fk_gestion_evento"
        ),
        sa.ForeignKeyConstraint(
            ["cuenta_canonica_id"], ["cuenta_canonica.id"], name="fk_gestion_cuenta"
        ),
        sa.UniqueConstraint("evento_lifecycle_id", name="uq_gestion_evento"),
        # Las gestiones de una cuenta por momento de negocio, en los dos sentidos; tambien las
        # candidatas de un movimiento en la atribucion.
        sa.Index("ix_gestion_cuenta_ocurrido", "cuenta_canonica_id", "ocurrido_en"),
        sa.CheckConstraint(MEDIO_DEL_CANAL, name=conv("ck_gestion_medio")),
        sa.CheckConstraint(COHERENCIA_DE_LA_GESTION, name=conv("ck_gestion_coherencia")),
        sa.CheckConstraint(f"actor_ref ~ '{PATRON_ACTOR}'", name=conv("ck_gestion_actor")),
    )

    id: int | None = Field(default=None, primary_key=True, sa_type=sa.BigInteger)
    ocurrido_en: datetime = Field(sa_type=sa.DateTime(timezone=True))
    """El ocurrido_en de su evento."""
    evento_lifecycle_id: int = Field(sa_type=sa.BigInteger)
    gestion_id: UUID = Field(default_factory=uuid4, unique=True)
    """Identificador publico."""
    cuenta_canonica_id: int
    canal: Canal = Field(
        sa_type=sa.Enum(
            Canal, name="canal_gestion", native_enum=False, create_constraint=True, length=12
        )
    )
    medio: Medio | None = Field(
        default=None,
        sa_type=sa.Enum(
            Medio, name="medio_gestion", native_enum=False, create_constraint=True, length=12
        ),
    )
    nivel_contacto: NivelContacto = Field(
        sa_type=sa.Enum(
            NivelContacto,
            name="nivel_contacto",
            native_enum=False,
            create_constraint=True,
            length=20,
        )
    )
    resultado: ResultadoGestion = Field(
        sa_type=sa.Enum(
            ResultadoGestion,
            name="resultado_gestion",
            native_enum=False,
            create_constraint=True,
            length=20,
        )
    )
    actor_ref: str | None = Field(default=None, max_length=64)
    """Quien hizo la gestion, como referencia opaca. No se cruza con el Gestor de pagos/v1."""
    observacion: str | None = Field(default=None, max_length=500)
    """Texto libre, sin datos personales: la API y la importacion rechazan lo que lo parece."""


class VisitaCampo(SQLModel, table=True):
    """El detalle de una gestion de CAMPO: que encontro la visita. Sin GPS, rutas ni zonas, que son
    de versiones posteriores; su resultado no se valida contra ninguna geografia."""

    __tablename__ = "visita_campo"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["gestion_cobranza_id"], ["gestion_cobranza.id"], name="fk_visita_gestion"
        ),
        sa.UniqueConstraint("gestion_cobranza_id", name="uq_visita_gestion"),
        sa.CheckConstraint(
            "inicio IS NULL OR fin IS NULL OR inicio <= fin", name=conv("ck_visita_intervalo")
        ),
    )

    id: int | None = Field(default=None, primary_key=True, sa_type=sa.BigInteger)
    inicio: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    fin: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    gestion_cobranza_id: int = Field(sa_type=sa.BigInteger)
    visita_id: UUID = Field(default_factory=uuid4, unique=True)
    resultado: ResultadoVisita = Field(
        sa_type=sa.Enum(
            ResultadoVisita,
            name="resultado_visita",
            native_enum=False,
            create_constraint=True,
            length=20,
        )
    )
    observacion: str | None = Field(default=None, max_length=500)


class PromesaPago(SQLModel, table=True):
    """Una promesa de pago, nacida de una gestion con resultado PROMESA: cuanto y hasta cuando.

    La crea un evento PROMESA_CREADA, cuyo ocurrido_en es el momento en que se acordo. No guarda si
    se cumplio: su estado operativo (vigente, cancelada o anulada) sale de sus eventos, y si se
    cumplio lo dice una evaluacion versionada con su propia fecha de corte, sin tocarla.
    """

    __tablename__ = "promesa_pago"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["evento_lifecycle_id"], ["evento_lifecycle.id"], name="fk_promesa_evento"
        ),
        sa.ForeignKeyConstraint(
            ["gestion_cobranza_id"], ["gestion_cobranza.id"], name="fk_promesa_gestion"
        ),
        sa.ForeignKeyConstraint(
            ["cuenta_canonica_id"], ["cuenta_canonica.id"], name="fk_promesa_cuenta"
        ),
        sa.UniqueConstraint("evento_lifecycle_id", name="uq_promesa_evento"),
        sa.UniqueConstraint("gestion_cobranza_id", name="uq_promesa_gestion"),
        # Las promesas de una cuenta por fecha limite.
        sa.Index("ix_promesa_cuenta_limite", "cuenta_canonica_id", "fecha_limite"),
        sa.CheckConstraint("monto_prometido > 0", name=conv("ck_promesa_monto")),
    )

    id: int | None = Field(default=None, primary_key=True, sa_type=sa.BigInteger)
    evento_lifecycle_id: int = Field(sa_type=sa.BigInteger)
    gestion_cobranza_id: int = Field(sa_type=sa.BigInteger)
    fecha_limite: date
    """El ultimo dia, en la hora local de la fuente, en que un pago la cumple."""
    promesa_id: UUID = Field(default_factory=uuid4, unique=True)
    cuenta_canonica_id: int
    monto_prometido: Decimal = Field(max_digits=14, decimal_places=2)
    version_modelo: str = Field(max_length=32)


class ConvenioCobranza(SQLModel, table=True):
    """Un convenio de cobranza: un acuerdo operacional registrado, nacido de una gestion con
    resultado CONVENIO. Guarda lo que la operacion acordo (monto total, vigencia y, si se
    declararon, sus cuotas) y nada mas: ni tasas ni terminos que nadie registro. No es un ledger:
    ningun movimiento se aplica a una cuota."""

    __tablename__ = "convenio_cobranza"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["evento_lifecycle_id"], ["evento_lifecycle.id"], name="fk_convenio_evento"
        ),
        sa.ForeignKeyConstraint(
            ["gestion_cobranza_id"], ["gestion_cobranza.id"], name="fk_convenio_gestion"
        ),
        sa.ForeignKeyConstraint(
            ["cuenta_canonica_id"], ["cuenta_canonica.id"], name="fk_convenio_cuenta"
        ),
        sa.UniqueConstraint("evento_lifecycle_id", name="uq_convenio_evento"),
        sa.UniqueConstraint("gestion_cobranza_id", name="uq_convenio_gestion"),
        sa.Index("ix_convenio_cuenta_inicio", "cuenta_canonica_id", "fecha_inicio"),
        sa.CheckConstraint("monto_total_acordado > 0", name=conv("ck_convenio_monto")),
        sa.CheckConstraint(
            "fecha_fin IS NULL OR fecha_fin >= fecha_inicio", name=conv("ck_convenio_vigencia")
        ),
        sa.CheckConstraint("cuotas >= 0", name=conv("ck_convenio_cuotas")),
    )

    id: int | None = Field(default=None, primary_key=True, sa_type=sa.BigInteger)
    evento_lifecycle_id: int = Field(sa_type=sa.BigInteger)
    gestion_cobranza_id: int = Field(sa_type=sa.BigInteger)
    fecha_inicio: date
    fecha_fin: date | None = None
    convenio_id: UUID = Field(default_factory=uuid4, unique=True)
    cuenta_canonica_id: int
    cuotas: int
    """Cuantas cuotas declaro quien lo registro; 0 si no declaro un calendario. La base exige, al
    confirmar, que el convenio tenga exactamente esas, que sumen su monto total, numeradas desde 1
    con fechas crecientes dentro de su vigencia."""
    monto_total_acordado: Decimal = Field(max_digits=14, decimal_places=2)
    version_modelo: str = Field(max_length=32)


class CuotaConvenio(SQLModel, table=True):
    """Una obligacion del calendario de un convenio, tal como se declaro: cuando y cuanto. Nunca se
    inventa una periodicidad que no se declaro."""

    __tablename__ = "cuota_convenio"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["convenio_cobranza_id"], ["convenio_cobranza.id"], name="fk_cuota_convenio"
        ),
        sa.CheckConstraint("monto > 0", name=conv("ck_cuota_monto")),
        sa.CheckConstraint("numero >= 1", name=conv("ck_cuota_numero")),
    )

    convenio_cobranza_id: int = Field(primary_key=True, sa_type=sa.BigInteger)
    numero: int = Field(primary_key=True)
    fecha_vencimiento: date
    monto: Decimal = Field(max_digits=14, decimal_places=2)


# --- la atribucion operativa ----------------------------------------------------------------------
#
# Desde la 0010. Asocia, con reglas versionadas, cada movimiento economico canonico con las
# gestiones que lo antecedieron: asociacion operacional, no causalidad. Como el motor de pagos,
# interpreta por ventana (un mes de recepcion de una cartera) y publica todo o nada: una ejecucion,
# lo que concluyo de cada movimiento y sus candidatas, en relaciones y no en texto. Una ejecucion
# nueva no toca la anterior: la vigente de una ventana es su EXITOSA mas reciente. El vocabulario es
# el de la version y se guarda como texto, salvo las invariantes que no dependen de ella.

CONTEOS_DE_LA_ATRIBUCION = (
    "movimientos_evaluados >= 0 AND asociados >= 0 AND ambiguos >= 0 AND sin_candidato >= 0 "
    "AND candidatos >= 0 AND movimientos_anulados >= 0 AND gestiones_leidas >= 0 "
    "AND gestiones_anuladas >= 0 AND gestiones_anuladas <= gestiones_leidas "
    "AND movimientos_anulados <= movimientos_evaluados "
    "AND movimientos_evaluados = asociados + ambiguos + sin_candidato "
    "AND candidatos >= asociados + 2 * ambiguos"
)
"""Cada movimiento evaluado tiene exactamente una clasificacion, y una asociacion unica tiene una
candidata y una ambigua al menos dos."""

MONTOS_DE_LA_ATRIBUCION = (
    "monto_asociado >= 0 AND monto_ambiguo >= 0 AND monto_sin_candidato >= 0 AND monto_anulado >= 0"
)


class EstadoAtribucion(StrEnum):
    """En que quedo una ejecucion de la atribucion. Solo una EXITOSA publica algo."""

    EN_PROCESO = "EN_PROCESO"
    EXITOSA = "EXITOSA"
    FALLIDA = "FALLIDA"


class ResultadoAtribucion(StrEnum):
    """Como termino una ejecucion de la atribucion, en el vocabulario de atribucion/v1."""

    ATRIBUCION_PUBLICADA = "ATRIBUCION_PUBLICADA"
    """EXITOSA: publico lo que concluyo de cada movimiento de su ventana."""
    YA_ATRIBUIDA = "YA_ATRIBUIDA"
    """FALLIDA: otra ejecucion ya publico exactamente las mismas entradas con esta version."""
    SIN_INTERPRETACION_DE_PAGOS = "SIN_INTERPRETACION_DE_PAGOS"
    """FALLIDA: la ventana no tiene una interpretacion vigente del motor de pagos que atribuir."""
    VERSION_NO_SOPORTADA = "VERSION_NO_SOPORTADA"
    DATOS_INCONSISTENTES = "DATOS_INCONSISTENTES"
    ERROR_INTERNO = "ERROR_INTERNO"
    INTENTOS_AGOTADOS = "INTENTOS_AGOTADOS"


class EjecucionAtribucion(SQLModel, table=True):
    """Una atribucion versionada de los movimientos economicos de una ventana: un despacho, una
    cartera y un mes de recepcion, [periodo_desde, periodo_hasta).

    Lee los movimientos de la interpretacion vigente del motor de pagos de su ventana (la guarda en
    `ejecucion_motor_pagos_id`) y las gestiones de sus cuentas. La ventana hacia atras
    (`ventana_dias`) y la zona horaria con que se comparan los instantes de las gestiones con las
    horas locales de pagos/v1 son parametros de la ejecucion, no verdades de negocio: se guardan
    aqui y entran en su firma de entrada. Como en el motor de pagos, a lo mas una EXITOSA por
    ventana, version y firma, y a lo mas una EN_PROCESO por ventana y version.
    """

    __tablename__ = "ejecucion_atribucion"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["ejecucion_motor_pagos_id"],
            ["ejecucion_motor_pagos.id"],
            name="fk_atribucion_motor_pagos",
        ),
        sa.Index(
            "ux_ejecucion_atribucion_exitosa",
            "despacho_id",
            "cartera_id",
            "version_atribucion",
            "periodo_desde",
            "firma_entrada",
            unique=True,
            postgresql_where=sa.text("estado = 'EXITOSA'"),
        ),
        sa.Index(
            "ux_ejecucion_atribucion_en_proceso",
            "despacho_id",
            "cartera_id",
            "version_atribucion",
            "periodo_desde",
            unique=True,
            postgresql_where=sa.text("estado = 'EN_PROCESO'"),
        ),
        sa.CheckConstraint("periodo_desde < periodo_hasta", name=conv("ck_atribucion_periodo")),
        # La ventana de atribucion/v1 es un mes calendario, como la del motor de pagos.
        sa.CheckConstraint(
            "version_atribucion <> 'atribucion/v1' OR (extract(day FROM periodo_desde) = 1 "
            "AND periodo_hasta = (periodo_desde + interval '1 month')::date)",
            name=conv("ck_atribucion_mes"),
        ),
        sa.CheckConstraint(
            "ventana_dias BETWEEN 1 AND 366", name=conv("ck_atribucion_ventana_dias")
        ),
        sa.CheckConstraint(
            "(estado = 'EN_PROCESO') = (resultado IS NULL)", name=conv("ck_atribucion_resultado")
        ),
        sa.CheckConstraint(
            "(firma_entrada IS NULL OR firma_entrada ~ '^[0-9a-f]{64}$') "
            "AND (estado <> 'EXITOSA' OR firma_entrada IS NOT NULL)",
            name=conv("ck_atribucion_firma"),
        ),
        sa.CheckConstraint(CONTEOS_DE_LA_ATRIBUCION, name=conv("ck_atribucion_conteos")),
        sa.CheckConstraint(MONTOS_DE_LA_ATRIBUCION, name=conv("ck_atribucion_montos")),
        # Una EXITOSA dice que interpretacion de pagos leyo; las demas no publicaron nada.
        sa.CheckConstraint(
            "(estado = 'EXITOSA' AND ejecucion_motor_pagos_id IS NOT NULL) "
            "OR (estado <> 'EXITOSA' AND movimientos_evaluados = 0 AND candidatos = 0)",
            name=conv("ck_atribucion_publicacion"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    atribucion_run_id: UUID = Field(default_factory=uuid4, unique=True)
    """Identificador publico. Identifica un intento, y por eso es aleatorio."""
    version_atribucion: str = Field(max_length=32)
    """Con que version se atribuyo: atribucion/v1. Sin valor por omision en la base."""
    despacho_id: str = Field(max_length=32)
    cartera_id: str = Field(max_length=32)
    periodo_desde: date
    periodo_hasta: date
    ventana_dias: int
    """Cuantos dias antes de un movimiento puede haber ocurrido una gestion candidata."""
    zona_horaria: str = Field(max_length=64)
    """La zona de las horas locales de pagos/v1, con que se comparan con los instantes del
    lifecycle."""
    estado: EstadoAtribucion = Field(
        default=EstadoAtribucion.EN_PROCESO,
        sa_type=sa.Enum(
            EstadoAtribucion,
            name="estado_atribucion",
            native_enum=False,
            create_constraint=True,
            length=12,
        ),
    )
    resultado: str | None = Field(default=None, max_length=40)
    firma_entrada: str | None = Field(default=None, max_length=64)
    """SHA-256 de lo que leyo: la interpretacion de pagos y cada gestion de sus cuentas en el rango,
    con su anulacion."""
    ejecucion_motor_pagos_id: int | None = None
    """La interpretacion de pagos cuyos movimientos atribuyo: la vigente de su ventana al leer."""
    iniciada_en: datetime = Field(default_factory=ahora, sa_type=sa.DateTime(timezone=True))
    terminada_en: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    movimientos_evaluados: int = Field(default=0, sa_type=sa.BigInteger)
    """Los PAGO de la interpretacion: cada uno recibe exactamente una clasificacion."""
    asociados: int = Field(default=0, sa_type=sa.BigInteger)
    ambiguos: int = Field(default=0, sa_type=sa.BigInteger)
    sin_candidato: int = Field(default=0, sa_type=sa.BigInteger)
    candidatos: int = Field(default=0, sa_type=sa.BigInteger)
    """Cuantas parejas (movimiento, gestion candidata) publico."""
    movimientos_anulados: int = Field(default=0, sa_type=sa.BigInteger)
    """De los evaluados, los que el motor de pagos dice que anulo un reverso."""
    gestiones_leidas: int = Field(default=0, sa_type=sa.BigInteger)
    """Las gestiones de sus cuentas en el rango que pudieron ser candidatas, anuladas o no."""
    gestiones_anuladas: int = Field(default=0, sa_type=sa.BigInteger)
    monto_asociado: Decimal = Field(default=Decimal("0.00"), max_digits=24, decimal_places=2)
    """Lo que suman los movimientos con asociacion unica que ningun reverso anulo."""
    monto_ambiguo: Decimal = Field(default=Decimal("0.00"), max_digits=24, decimal_places=2)
    monto_sin_candidato: Decimal = Field(default=Decimal("0.00"), max_digits=24, decimal_places=2)
    monto_anulado: Decimal = Field(default=Decimal("0.00"), max_digits=24, decimal_places=2)
    """Lo que suman los evaluados que anulo un reverso: no cuentan como recuperacion."""
    detalle: str | None = None


class AtribucionMovimiento(SQLModel, table=True):
    """Lo que una ejecucion de la atribucion concluyo de un movimiento economico canonico, y por
    que.

    Un movimiento evaluado tiene exactamente uno por ejecucion. Apunta a la fila del movimiento en
    la interpretacion que se leyo y repite su `movimiento_id`, que es el mismo en cualquier
    interpretacion. La asociacion es operacional: dice que hubo una sola gestion candidata antes del
    movimiento, no que la gestion lo haya causado.
    """

    __tablename__ = "atribucion_movimiento"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["ejecucion_atribucion_id"],
            ["ejecucion_atribucion.id"],
            name="fk_atribucion_movimiento_ejecucion",
        ),
        sa.ForeignKeyConstraint(
            ["movimiento_economico_canonico_id"],
            ["movimiento_economico_canonico.id"],
            name="fk_atribucion_movimiento_movimiento",
        ),
        sa.ForeignKeyConstraint(
            ["gestion_cobranza_id"],
            ["gestion_cobranza.id"],
            name="fk_atribucion_movimiento_gestion",
        ),
        sa.ForeignKeyConstraint(
            ["cuenta_canonica_id"], ["cuenta_canonica.id"], name="fk_atribucion_movimiento_cuenta"
        ),
        # Las atribuciones de un movimiento, en cualquier interpretacion.
        sa.Index("ix_atribucion_movimiento_movimiento_id", "movimiento_id"),
        # Una asociacion unica nombra a su gestion y tiene una candidata; una ambigua, al menos dos;
        # sin candidata, ninguna. Ninguna elige entre varias.
        sa.CheckConstraint(
            "clasificacion NOT IN ('SIN_GESTION_CANDIDATA', 'ASOCIACION_UNICA', 'AMBIGUA') OR ("
            "(clasificacion = 'ASOCIACION_UNICA') = (gestion_cobranza_id IS NOT NULL) "
            "AND ((clasificacion = 'SIN_GESTION_CANDIDATA' AND candidatos = 0) "
            "OR (clasificacion = 'ASOCIACION_UNICA' AND candidatos = 1) "
            "OR (clasificacion = 'AMBIGUA' AND candidatos >= 2)))",
            name=conv("ck_atribucion_movimiento_clase"),
        ),
    )

    # La llave primaria es la natural: un movimiento, una vez por ejecucion.
    ejecucion_atribucion_id: int = Field(primary_key=True)
    movimiento_economico_canonico_id: int = Field(primary_key=True, sa_type=sa.BigInteger)
    fecha_recepcion: datetime = Field(sa_type=sa.DateTime(timezone=False))
    """La del movimiento: hora local de la fuente, sin zona."""
    gestion_cobranza_id: int | None = Field(default=None, sa_type=sa.BigInteger)
    """La gestion asociada, solo en una ASOCIACION_UNICA."""
    movimiento_id: UUID
    cuenta_canonica_id: int | None = None
    """La cuenta con que el motor de pagos concilio el movimiento; vacia si no la tenia."""
    candidatos: int
    monto: Decimal = Field(max_digits=14, decimal_places=2)
    anulado_por_reverso: bool
    """Si la interpretacion de pagos dice que lo anulo un reverso: su monto no es recuperacion."""
    clasificacion: str = Field(max_length=24)
    """SIN_GESTION_CANDIDATA, ASOCIACION_UNICA o AMBIGUA."""
    motivos: list[dict] = Field(sa_type=JSONB)


class CandidatoAtribucion(SQLModel, table=True):
    """Una gestion candidata de un movimiento en una ejecucion: por que el movimiento quedo asociado
    o ambiguo, en una relacion que se puede consultar y no en una lista de texto."""

    __tablename__ = "candidato_atribucion"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["ejecucion_atribucion_id", "movimiento_economico_canonico_id"],
            [
                "atribucion_movimiento.ejecucion_atribucion_id",
                "atribucion_movimiento.movimiento_economico_canonico_id",
            ],
            name="fk_candidato_atribucion",
        ),
        sa.ForeignKeyConstraint(
            ["gestion_cobranza_id"], ["gestion_cobranza.id"], name="fk_candidato_gestion"
        ),
        sa.CheckConstraint("antelacion_segundos >= 0", name=conv("ck_candidato_antelacion")),
    )

    ejecucion_atribucion_id: int = Field(primary_key=True)
    movimiento_economico_canonico_id: int = Field(primary_key=True, sa_type=sa.BigInteger)
    gestion_cobranza_id: int = Field(primary_key=True, sa_type=sa.BigInteger)
    antelacion_segundos: int = Field(sa_type=sa.BigInteger)
    """Cuanto antes del movimiento ocurrio la gestion."""


# --- la evaluacion de las promesas ----------------------------------------------------------------
#
# Desde la 0010. Si una promesa se cumplio no lo decide nadie con un UPDATE: lo concluye, para una
# fecha de corte explicita (as_of), una evaluacion versionada que observa los movimientos economicos
# de su cuenta. Que haya recuperacion compatible con una promesa no dice que la promesa la produjo.

CLASES_DE_LA_EVALUACION = (
    "promesas_evaluadas = pendientes + cumplidas + parciales + incumplidas + canceladas "
    "+ no_evaluables"
)
CONTEOS_DE_LA_EVALUACION = (
    "promesas_evaluadas >= 0 AND pendientes >= 0 AND cumplidas >= 0 AND parciales >= 0 "
    "AND incumplidas >= 0 AND canceladas >= 0 AND no_evaluables >= 0 "
    "AND monto_prometido >= 0 AND monto_observado >= 0"
)


class EstadoEvaluacionPromesas(StrEnum):
    EN_PROCESO = "EN_PROCESO"
    EXITOSA = "EXITOSA"
    FALLIDA = "FALLIDA"


class ResultadoEvaluacionPromesas(StrEnum):
    """Como termino una ejecucion de la evaluacion de promesas."""

    EVALUACION_PUBLICADA = "EVALUACION_PUBLICADA"
    YA_EVALUADA = "YA_EVALUADA"
    """FALLIDA: otra ejecucion ya evaluo exactamente las mismas entradas, con el mismo as_of."""
    VERSION_NO_SOPORTADA = "VERSION_NO_SOPORTADA"
    DATOS_INCONSISTENTES = "DATOS_INCONSISTENTES"
    ERROR_INTERNO = "ERROR_INTERNO"
    INTENTOS_AGOTADOS = "INTENTOS_AGOTADOS"


class EjecucionEvaluacionPromesas(SQLModel, table=True):
    """Una evaluacion versionada de las promesas de una cartera a una fecha de corte (`as_of`).

    Evalua, en un solo trabajo y por conjuntos, cada promesa acordada hasta el final de as_of. La
    misma cartera, el mismo as_of, la misma version y los mismos datos dan el mismo resultado: as_of
    nunca sale del reloj. A lo mas una EXITOSA por cartera, version, as_of y firma de entrada, y a
    lo mas una EN_PROCESO por cartera, version y as_of.
    """

    __tablename__ = "ejecucion_evaluacion_promesas"
    __table_args__ = (
        sa.Index(
            "ux_evaluacion_promesas_exitosa",
            "despacho_id",
            "cartera_id",
            "version_evaluacion",
            "as_of",
            "firma_entrada",
            unique=True,
            postgresql_where=sa.text("estado = 'EXITOSA'"),
        ),
        sa.Index(
            "ux_evaluacion_promesas_en_proceso",
            "despacho_id",
            "cartera_id",
            "version_evaluacion",
            "as_of",
            unique=True,
            postgresql_where=sa.text("estado = 'EN_PROCESO'"),
        ),
        sa.CheckConstraint(
            "(estado = 'EN_PROCESO') = (resultado IS NULL)", name=conv("ck_evaluacion_resultado")
        ),
        sa.CheckConstraint(
            "(firma_entrada IS NULL OR firma_entrada ~ '^[0-9a-f]{64}$') "
            "AND (estado <> 'EXITOSA' OR firma_entrada IS NOT NULL)",
            name=conv("ck_evaluacion_firma"),
        ),
        sa.CheckConstraint(CONTEOS_DE_LA_EVALUACION, name=conv("ck_evaluacion_conteos")),
        sa.CheckConstraint(CLASES_DE_LA_EVALUACION, name=conv("ck_evaluacion_clases")),
        sa.CheckConstraint(
            "estado = 'EXITOSA' OR promesas_evaluadas = 0", name=conv("ck_evaluacion_publicacion")
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    evaluacion_run_id: UUID = Field(default_factory=uuid4, unique=True)
    version_evaluacion: str = Field(max_length=32)
    """evaluacion-promesa/v1. Sin valor por omision en la base."""
    despacho_id: str = Field(max_length=32)
    cartera_id: str = Field(max_length=32)
    as_of: date
    """La fecha de corte: se observa hasta el final de ese dia, en la hora local de la fuente."""
    zona_horaria: str = Field(max_length=64)
    estado: EstadoEvaluacionPromesas = Field(
        default=EstadoEvaluacionPromesas.EN_PROCESO,
        sa_type=sa.Enum(
            EstadoEvaluacionPromesas,
            name="estado_evaluacion_promesas",
            native_enum=False,
            create_constraint=True,
            length=12,
        ),
    )
    resultado: str | None = Field(default=None, max_length=40)
    firma_entrada: str | None = Field(default=None, max_length=64)
    horizonte_pagos: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=False))
    """El pago observado mas reciente de la cartera al evaluar: hasta donde llegan los datos."""
    iniciada_en: datetime = Field(default_factory=ahora, sa_type=sa.DateTime(timezone=True))
    terminada_en: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    promesas_evaluadas: int = Field(default=0, sa_type=sa.BigInteger)
    pendientes: int = Field(default=0, sa_type=sa.BigInteger)
    cumplidas: int = Field(default=0, sa_type=sa.BigInteger)
    parciales: int = Field(default=0, sa_type=sa.BigInteger)
    incumplidas: int = Field(default=0, sa_type=sa.BigInteger)
    canceladas: int = Field(default=0, sa_type=sa.BigInteger)
    no_evaluables: int = Field(default=0, sa_type=sa.BigInteger)
    monto_prometido: Decimal = Field(default=Decimal("0.00"), max_digits=24, decimal_places=2)
    monto_observado: Decimal = Field(default=Decimal("0.00"), max_digits=24, decimal_places=2)
    """Lo que suman los movimientos compatibles con cada promesa; un movimiento compatible con dos
    promesas cuenta en las dos."""
    detalle: str | None = None


class EvaluacionPromesa(SQLModel, table=True):
    """Lo que una ejecucion de la evaluacion concluyo de una promesa, y por que."""

    __tablename__ = "evaluacion_promesa"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["ejecucion_evaluacion_promesas_id"],
            ["ejecucion_evaluacion_promesas.id"],
            name="fk_evaluacion_promesa_ejecucion",
        ),
        sa.ForeignKeyConstraint(
            ["promesa_pago_id"], ["promesa_pago.id"], name="fk_evaluacion_promesa_promesa"
        ),
        # Las evaluaciones de una promesa, para dar la ultima.
        sa.Index("ix_evaluacion_promesa_promesa", "promesa_pago_id"),
        sa.CheckConstraint(
            "movimientos_compatibles >= 0 AND monto_observado >= 0",
            name=conv("ck_evaluacion_promesa_conteos"),
        ),
    )

    ejecucion_evaluacion_promesas_id: int = Field(primary_key=True)
    promesa_pago_id: int = Field(primary_key=True, sa_type=sa.BigInteger)
    primer_movimiento_en: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=False))
    ultimo_movimiento_en: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=False))
    movimientos_compatibles: int
    monto_observado: Decimal = Field(max_digits=14, decimal_places=2)
    estado: str = Field(max_length=16)
    """PENDIENTE, CUMPLIDA, PARCIAL, INCUMPLIDA, CANCELADA o NO_EVALUABLE."""
    motivos: list[dict] = Field(sa_type=JSONB)


# --- la orquestacion durable ----------------------------------------------------------------------
#
# Desde la 0006 los motores no corren dentro de la peticion que los pide: la peticion deja el
# recurso EN_PROCESO y un TrabajoOrquestacion PENDIENTE en la misma transaccion, y un worker lo
# toma, lo ejecuta y lo cierra. La cola es esta tabla, en la misma PostgreSQL que los recursos.
# Las restricciones llevan nombres cortos y propios: los de la convencion pasarian de 63 caracteres
# en varias llaves, y son parte del contrato del esquema.

CADENA_DEL_FLUJO = (
    "(etapa = 'INGESTA' AND ejecucion_decision_id IS NULL "
    "AND ejecucion_territorial_id IS NULL AND ejecucion_ruteo_id IS NULL) "
    "OR (etapa = 'DECISION' AND ejecucion_decision_id IS NOT NULL "
    "AND ejecucion_territorial_id IS NULL AND ejecucion_ruteo_id IS NULL) "
    "OR (etapa = 'TERRITORIAL' AND ejecucion_decision_id IS NOT NULL "
    "AND ejecucion_territorial_id IS NOT NULL AND ejecucion_ruteo_id IS NULL) "
    "OR (etapa IN ('RUTEO', 'COMPLETADA') AND ejecucion_decision_id IS NOT NULL "
    "AND ejecucion_territorial_id IS NOT NULL AND ejecucion_ruteo_id IS NOT NULL)"
)
"""Cada etapa apunta a las ejecuciones de las etapas anteriores y a la suya, y a ninguna despues."""

OBJETIVO_DEL_TRABAJO = (
    "(tipo = 'INGESTA') = (corrida_id IS NOT NULL) "
    "AND (tipo = 'DECISION') = (ejecucion_decision_id IS NOT NULL) "
    "AND (tipo = 'TERRITORIAL') = (ejecucion_territorial_id IS NOT NULL) "
    "AND (tipo = 'RUTEO') = (ejecucion_ruteo_id IS NOT NULL) "
    "AND (tipo = 'INGESTA_PAGOS') = (ingesta_pagos_id IS NOT NULL) "
    "AND (tipo = 'HISTORIA') = (ejecucion_historia_id IS NOT NULL) "
    "AND (tipo = 'MOTOR_PAGOS') = (ejecucion_motor_pagos_id IS NOT NULL) "
    "AND (tipo = 'ATRIBUCION') = (ejecucion_atribucion_id IS NOT NULL) "
    "AND (tipo = 'EVALUACION_PROMESAS') = (ejecucion_evaluacion_promesas_id IS NOT NULL)"
)
"""Exactamente un objetivo, el de su tipo: el tipo tiene un solo valor, y cada equivalencia obliga a
que su objetivo exista y a que los demas esten vacios."""

PROPIEDAD_DEL_TRABAJO = (
    "(estado = 'PENDIENTE' AND worker_id IS NULL AND lease_hasta IS NULL "
    "AND terminado_en IS NULL) "
    "OR (estado = 'EJECUTANDO' AND worker_id IS NOT NULL AND lease_hasta IS NOT NULL "
    "AND terminado_en IS NULL) "
    "OR (estado IN ('COMPLETADO', 'FALLIDO') AND worker_id IS NULL AND lease_hasta IS NULL "
    "AND terminado_en IS NOT NULL)"
)
"""Solo un trabajo EJECUTANDO tiene dueno y lease, y solo uno terminado tiene fin."""


class ArchivoCorrida(SQLModel, table=True):
    """El archivo de una corrida de v0.5, en BYTEA. Desde la 0007 es una estructura heredada.

    En v0.5 la API guardaba aqui el archivo y el worker lo borraba al terminar la ingesta. Desde
    v0.6 el archivo original es evidencia y vive en el almacen de artefactos (ArtefactoFuente):
    nada escribe ni borra en esta tabla. Se conserva para que una corrida de v0.5 que seguia en la
    cola al migrar todavia se pueda procesar con su archivo, y para que bajar a la 0006 sea seguro.
    No copia el origen ni la firma: viven en la corrida. Nunca sale por la API.
    """

    __tablename__ = "archivo_corrida"
    __table_args__ = (
        sa.CheckConstraint("tamano_bytes > 0", name=conv("ck_archivo_tamano_positivo")),
        # El tamano es el del contenido guardado, no uno declarado aparte.
        sa.CheckConstraint(
            "octet_length(contenido) = tamano_bytes", name=conv("ck_archivo_tamano_exacto")
        ),
    )

    corrida_id: int = Field(primary_key=True, foreign_key="corrida.id")
    """Una corrida, un archivo. Sin cascada: no se borra una corrida que todavia tiene su
    archivo."""
    contenido: bytes = Field(sa_type=sa.LargeBinary)
    """BYTEA. Hasta el tope de la API (MC_TAMANO_MAXIMO_MB); PostgreSQL lo guarda en TOAST."""
    tamano_bytes: int = Field(sa_type=sa.BigInteger)
    creado_en: datetime = Field(default_factory=ahora, sa_type=sa.DateTime(timezone=True))


class EstadoFlujo(StrEnum):
    """En que va un flujo automatico: la cadena de ingesta, decision, territorial y ruteo de una
    corrida."""

    EN_PROCESO = "EN_PROCESO"  # todavia puede avanzar: su etapa tiene un trabajo por cerrar
    COMPLETADO = "COMPLETADO"  # llego hasta un ruteo EXITOSA
    DETENIDO = "DETENIDO"  # una etapa termino sin poder continuar; el flujo conserva cual


class EtapaFlujo(StrEnum):
    """Hasta donde llego un flujo. Cada etapa ya tiene su ejecucion; COMPLETADA, todas."""

    INGESTA = "INGESTA"
    DECISION = "DECISION"
    TERRITORIAL = "TERRITORIAL"
    RUTEO = "RUTEO"
    COMPLETADA = "COMPLETADA"


class FlujoOrquestacion(SQLModel, table=True):
    """La cadena automatica de una corrida: ingesta, decision, territorial y ruteo, una etapa
    despues de otra, sin que el cliente pida cada una.

    No es una ejecucion: no calcula nada. Apunta a la corrida y a la ejecucion vigente de cada etapa
    a la que ya llego, y dice en que etapa va y como va. Si una etapa falla, el flujo se DETIENE en
    ella; reanudarlo crea otra ejecucion de esa etapa y mueve el puntero, y la fallida queda en el
    historial.
    """

    __tablename__ = "flujo_orquestacion"
    __table_args__ = (
        sa.ForeignKeyConstraint(["corrida_id"], ["corrida.id"], name="fk_flujo_corrida"),
        sa.ForeignKeyConstraint(
            ["ejecucion_decision_id"], ["ejecucion_decision.id"], name="fk_flujo_decision"
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_territorial_id"],
            ["ejecucion_territorial.id"],
            name="fk_flujo_territorial",
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_ruteo_id"], ["ejecucion_ruteo.id"], name="fk_flujo_ruteo"
        ),
        # Una corrida tiene a lo mas un flujo, y una ejecucion es de a lo mas un flujo. Los NULL de
        # las etapas a las que no se ha llegado no chocan entre si.
        sa.UniqueConstraint("corrida_id", name="uq_flujo_corrida"),
        sa.UniqueConstraint("ejecucion_decision_id", name="uq_flujo_decision"),
        sa.UniqueConstraint("ejecucion_territorial_id", name="uq_flujo_territorial"),
        sa.UniqueConstraint("ejecucion_ruteo_id", name="uq_flujo_ruteo"),
        sa.CheckConstraint(CADENA_DEL_FLUJO, name=conv("ck_flujo_cadena")),
        # COMPLETADO es haber llegado a COMPLETADA, y al reves. Un DETENIDO conserva su etapa.
        sa.CheckConstraint(
            "(estado = 'COMPLETADO') = (etapa = 'COMPLETADA')", name=conv("ck_flujo_completado")
        ),
        # Solo un flujo que ya no avanza tiene fin; reanudarlo se lo quita.
        sa.CheckConstraint(
            "(estado = 'EN_PROCESO') = (terminado_en IS NULL)", name=conv("ck_flujo_terminado")
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    flujo_id: UUID = Field(default_factory=uuid4, unique=True)
    """Identificador publico. El `id` es interno, como en las demas tablas."""
    corrida_id: int
    """La corrida de la ingesta. Su llave foranea, sin cascada, y su unicidad estan en
    __table_args__."""
    ejecucion_decision_id: int | None = None
    """La ejecucion de decision vigente del flujo, desde que llega a DECISION."""
    ejecucion_territorial_id: int | None = None
    ejecucion_ruteo_id: int | None = None
    estado: EstadoFlujo = Field(
        default=EstadoFlujo.EN_PROCESO,
        sa_type=sa.Enum(
            EstadoFlujo, name="estado_flujo", native_enum=False, create_constraint=True, length=12
        ),
    )
    etapa: EtapaFlujo = Field(
        default=EtapaFlujo.INGESTA,
        sa_type=sa.Enum(
            EtapaFlujo, name="etapa_flujo", native_enum=False, create_constraint=True, length=12
        ),
    )
    creado_en: datetime = Field(default_factory=ahora, sa_type=sa.DateTime(timezone=True))
    actualizado_en: datetime = Field(default_factory=ahora, sa_type=sa.DateTime(timezone=True))
    terminado_en: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    detalle: str | None = None
    """Que paso, para una persona: en que etapa va, o por que se detuvo."""


class TipoTrabajo(StrEnum):
    """Que motor ejecuta un trabajo, y por tanto cual de sus objetivos tiene."""

    INGESTA = "INGESTA"
    DECISION = "DECISION"
    TERRITORIAL = "TERRITORIAL"
    RUTEO = "RUTEO"
    INGESTA_PAGOS = "INGESTA_PAGOS"
    """Desde la 0007: juzgar un archivo de pagos/v1. No es de ningun flujo: no encadena nada."""
    HISTORIA = "HISTORIA"
    """Desde la 0008: materializar un dataset conformado, de cartera o de pagos, en el modelo
    historico. No es de ningun flujo, y ninguna etapa operacional lo espera."""
    MOTOR_PAGOS = "MOTOR_PAGOS"
    """Desde la 0009: interpretar los pagos observados de una ventana con el motor de pagos. Lo
    abre la historia de un archivo de pagos al publicar sus pagos observados, o el backfill. No
    es de ningun flujo, y ninguna etapa operacional lo espera."""
    ATRIBUCION = "ATRIBUCION"
    """Desde la 0010: asociar los movimientos de una ventana con las gestiones que los antecedieron.
    No se abre solo: lo piden POST /atribuciones o backfill-atribucion. No es de ningun flujo."""
    EVALUACION_PROMESAS = "EVALUACION_PROMESAS"
    """Desde la 0010: evaluar las promesas de una cartera a una fecha de corte. Lo piden POST
    /evaluaciones-promesas o backfill-lifecycle, uno por cartera y fecha, nunca uno por promesa."""


class EstadoTrabajo(StrEnum):
    """En que va un trabajo de la cola. Es el estado de la entrega, no el del motor: un trabajo
    COMPLETADO puede tener su ejecucion FALLIDA."""

    PENDIENTE = "PENDIENTE"  # espera a que un worker lo tome, a partir de disponible_desde
    EJECUTANDO = "EJECUTANDO"  # lo tiene un worker, mientras su lease siga vigente
    COMPLETADO = "COMPLETADO"  # su objetivo llego a un estado terminal, el que sea
    FALLIDO = "FALLIDO"  # agoto sus intentos sin que su objetivo llegara a un estado terminal


class TrabajoOrquestacion(SQLModel, table=True):
    """Un trabajo de la cola durable: ejecutar un motor sobre un recurso que ya existe EN_PROCESO.

    La entrega es al menos una vez: un worker lo toma con FOR UPDATE SKIP LOCKED, lo marca
    EJECUTANDO con un lease que renueva su latido, y si muere, otro lo vuelve a tomar cuando el
    lease vence. Lo que hace segura la repeticion no es la cola sino los motores: sus bloqueos, sus
    estados terminales y sus transacciones todo o nada.

    Cada recurso tiene un solo trabajo: las restricciones unicas de sus objetivos lo garantizan, y
    una nueva entrega reusa la misma fila.
    """

    __tablename__ = "trabajo_orquestacion"
    __table_args__ = (
        sa.ForeignKeyConstraint(["flujo_id"], ["flujo_orquestacion.id"], name="fk_trabajo_flujo"),
        sa.ForeignKeyConstraint(["corrida_id"], ["corrida.id"], name="fk_trabajo_corrida"),
        sa.ForeignKeyConstraint(
            ["ejecucion_decision_id"], ["ejecucion_decision.id"], name="fk_trabajo_decision"
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_territorial_id"],
            ["ejecucion_territorial.id"],
            name="fk_trabajo_territorial",
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_ruteo_id"], ["ejecucion_ruteo.id"], name="fk_trabajo_ruteo"
        ),
        sa.ForeignKeyConstraint(
            ["ingesta_pagos_id"], ["ingesta_pagos.id"], name="fk_trabajo_pagos"
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_historia_id"], ["ejecucion_historia.id"], name="fk_trabajo_historia"
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_motor_pagos_id"],
            ["ejecucion_motor_pagos.id"],
            name="fk_trabajo_motor_pagos",
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_atribucion_id"],
            ["ejecucion_atribucion.id"],
            name="fk_trabajo_atribucion",
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_evaluacion_promesas_id"],
            ["ejecucion_evaluacion_promesas.id"],
            name="fk_trabajo_evaluacion",
        ),
        sa.UniqueConstraint("corrida_id", name="uq_trabajo_corrida"),
        sa.UniqueConstraint("ejecucion_decision_id", name="uq_trabajo_decision"),
        sa.UniqueConstraint("ejecucion_territorial_id", name="uq_trabajo_territorial"),
        sa.UniqueConstraint("ejecucion_ruteo_id", name="uq_trabajo_ruteo"),
        sa.UniqueConstraint("ingesta_pagos_id", name="uq_trabajo_pagos"),
        sa.UniqueConstraint("ejecucion_historia_id", name="uq_trabajo_historia"),
        sa.UniqueConstraint("ejecucion_motor_pagos_id", name="uq_trabajo_motor_pagos"),
        sa.UniqueConstraint("ejecucion_atribucion_id", name="uq_trabajo_atribucion"),
        sa.UniqueConstraint("ejecucion_evaluacion_promesas_id", name="uq_trabajo_evaluacion"),
        sa.CheckConstraint(OBJETIVO_DEL_TRABAJO, name=conv("ck_trabajo_objetivo")),
        sa.CheckConstraint(PROPIEDAD_DEL_TRABAJO, name=conv("ck_trabajo_lease")),
        sa.CheckConstraint(
            "intentos >= 0 AND max_intentos >= 1 AND intentos <= max_intentos",
            name=conv("ck_trabajo_intentos"),
        ),
        # La consulta del worker: los PENDIENTE ya disponibles y los EJECUTANDO con el lease
        # vencido, en orden de id. Solo indexa los que todavia se pueden reclamar: los terminados
        # son casi todos, y nunca se buscan ahi.
        sa.Index(
            "ix_trabajo_reclamable",
            "estado",
            "disponible_desde",
            "lease_hasta",
            "id",
            postgresql_where=sa.text("estado IN ('PENDIENTE', 'EJECUTANDO')"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    trabajo_id: UUID = Field(default_factory=uuid4, unique=True)
    """Identificador publico. El `id` es interno y es el orden de la cola."""
    flujo_id: int | None = Field(default=None, index=True)
    """El flujo al que pertenece (su `id` interno, no su flujo_id publico), o NULL si se pidio a
    mano sobre un recurso que no es de ningun flujo. Su indice sirve para listar los trabajos de un
    flujo."""
    tipo: TipoTrabajo = Field(
        sa_type=sa.Enum(
            TipoTrabajo, name="tipo_trabajo", native_enum=False, create_constraint=True, length=24
        )
    )
    """VARCHAR(16) desde la 0007, porque INGESTA_PAGOS no cabia en los 12 de antes, y VARCHAR(24)
    desde la 0010, por EVALUACION_PROMESAS."""
    estado: EstadoTrabajo = Field(
        default=EstadoTrabajo.PENDIENTE,
        sa_type=sa.Enum(
            EstadoTrabajo,
            name="estado_trabajo",
            native_enum=False,
            create_constraint=True,
            length=12,
        ),
    )
    corrida_id: int | None = None
    """El objetivo de una INGESTA. Existe exactamente uno de sus objetivos: el de su tipo."""
    ejecucion_decision_id: int | None = None
    ejecucion_territorial_id: int | None = None
    ejecucion_ruteo_id: int | None = None
    ingesta_pagos_id: int | None = None
    """El objetivo de un trabajo INGESTA_PAGOS, desde la 0007."""
    ejecucion_historia_id: int | None = None
    """El objetivo de un trabajo HISTORIA, desde la 0008."""
    ejecucion_motor_pagos_id: int | None = None
    """El objetivo de un trabajo MOTOR_PAGOS, desde la 0009."""
    ejecucion_atribucion_id: int | None = None
    """El objetivo de un trabajo ATRIBUCION, desde la 0010."""
    ejecucion_evaluacion_promesas_id: int | None = None
    """El objetivo de un trabajo EVALUACION_PROMESAS, desde la 0010."""
    intentos: int = 0
    """Cuantas veces un worker lo tomo para ejecutarlo."""
    max_intentos: int
    """Cuantas veces se puede tomar, copiado de la configuracion al crearlo: el trabajo conserva la
    politica con la que nacio."""
    creado_en: datetime = Field(default_factory=ahora, sa_type=sa.DateTime(timezone=True))
    disponible_desde: datetime = Field(default_factory=ahora, sa_type=sa.DateTime(timezone=True))
    """Desde cuando se puede tomar: al crearlo, de inmediato; tras un error, cuando pasa la
    espera."""
    tomado_en: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    """La ultima vez que un worker lo tomo. Se conserva al terminar, para auditoria."""
    latido_en: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    """El ultimo latido de su worker. Tambien se conserva al terminar."""
    lease_hasta: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    """Hasta cuando es de su worker. Vencido, otro worker lo puede tomar."""
    terminado_en: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    worker_id: str | None = Field(default=None, max_length=200)
    """El proceso worker que lo tiene, solo mientras esta EJECUTANDO: host, pid y un UUID del
    proceso. No es la identidad de una persona, y no sale por la API."""
    ultimo_error: str | None = Field(default=None, max_length=500)
    """El ultimo error del worker, corto y sin traza: el tipo de error, y que se vea la bitacora."""
