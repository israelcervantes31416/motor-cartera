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

from motor_cartera.analytics.politica import Granularidad, Modo
from motor_cartera.config import config
from motor_cartera.db.espacial import Geometria
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


# --- decision/v2 ----------------------------------------------------------------------------------
#
# Desde la 0011. Decide cada cuenta de un corte canonico con lo que se sabia en un instante, con
# reglas versionadas y explicables. Es paralelo a decision/v1: no toca su tabla, su enum ni su flujo
# (ingesta, decision, territorial y ruteo siguen leyendo decision/v1). Tres relojes no se mezclan:
# el corte fija el universo (las cuentas de sus snapshots), as_of el dia de negocio que se decide y
# known_at hasta donde llega el conocimiento que se usa. Una ejecucion publica, todo o nada, las
# features exactas con que decidio cada cuenta (FeatureDecisionCuenta) y lo que decidio, con sus
# motivos (ResultadoDecisionV2): una decision de ayer se explica con lo que se sabia ayer, sin
# volver a calcular nada. Las tablas por cuenta crecen con cada ejecucion: no tienen id propio, su
# llave es la natural (ejecucion, cuenta), y sus columnas de 8 bytes van primero.

CONTEOS_DE_DECISION_V2 = (
    "cuentas_objetivo >= 0 AND cuentas_decididas >= 0 AND cuentas_fallidas >= 0 "
    "AND cuentas_decididas <= cuentas_objetivo AND cuentas_fallidas <= cuentas_objetivo"
)

PUBLICACION_DE_DECISION_V2 = (
    "(estado = 'EXITOSA' AND cuentas_objetivo > 0 AND cuentas_decididas = cuentas_objetivo "
    "AND cuentas_fallidas = 0) OR (estado <> 'EXITOSA' AND cuentas_decididas = 0)"
)
"""Una EXITOSA decidio cada cuenta de su corte y ninguna fallo; las demas no publicaron nada."""

NIVELES_DE_PRIORIDAD = (
    "score_nivel_base BETWEEN 1 AND 4 AND score_nivel_final BETWEEN 1 AND 4 "
    "AND score_nivel_final = least(4, greatest(1, score_nivel_base + ajuste_total)) "
    "AND score_nivel_base = CASE prioridad_base WHEN 'BAJA' THEN 1 WHEN 'MEDIA' THEN 2 "
    "WHEN 'ALTA' THEN 3 WHEN 'MUY_ALTA' THEN 4 END "
    "AND score_nivel_final = CASE prioridad_final WHEN 'BAJA' THEN 1 WHEN 'MEDIA' THEN 2 "
    "WHEN 'ALTA' THEN 3 WHEN 'MUY_ALTA' THEN 4 END"
)
"""El nivel es el orden de la prioridad (BAJA 1 a MUY_ALTA 4), y el final es el base mas el ajuste,
acotado a ese rango. Lo exige la base, no solo la politica: no hay prioridad fuera de la escala ni
un nivel que no sea la suma de sus efectos."""


class EstadoDecisionV2(StrEnum):
    """En que quedo una ejecucion de decision/v2. Solo una EXITOSA publica algo."""

    EN_PROCESO = "EN_PROCESO"
    EXITOSA = "EXITOSA"
    FALLIDA = "FALLIDA"


class ModoDecisionV2(StrEnum):
    """Para que se pidio la decision. No entra en su firma: es un control de quien la pide."""

    OPERATIVO = "OPERATIVO"
    """El corte mas reciente conocido a known_at. known_at puede omitirse: es el reloj de la base al
    abrir, una sola vez."""
    BACKTEST = "BACKTEST"
    """Cualquier corte conocido a known_at, que es obligatorio: un backtest declara su frontera de
    conocimiento."""


class ResultadoEjecucionDecisionV2(StrEnum):
    """Como termino una ejecucion de decision/v2."""

    DECISION_PUBLICADA = "DECISION_PUBLICADA"
    """EXITOSA: publico las features y la decision de cada cuenta del corte."""
    YA_DECIDIDA = "YA_DECIDIDA"
    """FALLIDA: otra ejecucion publico primero la misma firma de entrada."""
    VERSION_NO_SOPORTADA = "VERSION_NO_SOPORTADA"
    CUENTAS_NO_DECIDIBLES = "CUENTAS_NO_DECIDIBLES"
    """FALLIDA: alguna cuenta del corte no cumple lo que la politica necesita; no se publica
    ninguna."""
    DATOS_INCONSISTENTES = "DATOS_INCONSISTENTES"
    ERROR_INTERNO = "ERROR_INTERNO"
    INTENTOS_AGOTADOS = "INTENTOS_AGOTADOS"


class EjecucionDecisionV2(SQLModel, table=True):
    """Una ejecucion de decision/v2 sobre un corte canonico, a un as_of y con conocimiento hasta
    known_at.

    Es una entidad, como las ejecuciones de los demas motores, porque tiene que existir aunque no
    publique nada. Guarda todo lo que fija su resultado: el corte, as_of, known_at, la zona del dia
    logico, las versiones de motor, features y politica, el hash de la politica y la firma de sus
    entradas. La base garantiza a lo mas una EXITOSA por firma, y a lo mas una EN_PROCESO por corte
    y versiones: la misma decision no se publica dos veces, y un corte no se decide dos veces a la
    vez.
    """

    __tablename__ = "ejecucion_decision_v2"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["corte_canonico_id"], ["corte_canonico.id"], name="fk_decision_v2_corte"
        ),
        # Lo que referencia cada FeatureDecisionCuenta: la ejecucion junto con su corte. Asi la base
        # garantiza que las features son de cuentas del corte de su ejecucion.
        sa.UniqueConstraint("id", "corte_canonico_id", name="uq_decision_v2_id_corte"),
        sa.Index(
            "ux_decision_v2_exitosa",
            "firma_inputs",
            unique=True,
            postgresql_where=sa.text("estado = 'EXITOSA'"),
        ),
        sa.Index(
            "ux_decision_v2_en_proceso",
            "corte_canonico_id",
            "version_features",
            "version_politica",
            unique=True,
            postgresql_where=sa.text("estado = 'EN_PROCESO'"),
        ),
        # Las ejecuciones de un corte, en cualquier estado, la mas reciente primero.
        sa.Index("ix_decision_v2_corte", "corte_canonico_id", "id"),
        sa.CheckConstraint(
            "firma_inputs ~ '^[0-9a-f]{64}$' AND hash_politica ~ '^[0-9a-f]{64}$'",
            name=conv("ck_decision_v2_firmas"),
        ),
        sa.CheckConstraint(
            "(estado = 'EN_PROCESO') = (resultado IS NULL)", name=conv("ck_decision_v2_resultado")
        ),
        sa.CheckConstraint(CONTEOS_DE_DECISION_V2, name=conv("ck_decision_v2_conteos")),
        sa.CheckConstraint(PUBLICACION_DE_DECISION_V2, name=conv("ck_decision_v2_publicacion")),
    )

    id: int | None = Field(default=None, primary_key=True)
    decision_run_id: UUID = Field(default_factory=uuid4, unique=True)
    """Identificador publico. Identifica un intento, como los demas *_run_id, y por eso es
    aleatorio; lo que es determinista es su firma y lo que publica."""
    corte_canonico_id: int
    """El corte cuyas cuentas son el universo. Su llave foranea esta en __table_args__."""
    despacho_id: str = Field(max_length=32)
    cartera_id: str = Field(max_length=32)
    as_of: date
    """El dia de negocio que se decide: el del corte. Se observa hasta el final de ese dia en la
    zona de la ejecucion."""
    known_at: datetime = Field(sa_type=sa.DateTime(timezone=True))
    """Hasta cuando se conoce: un evento, un corte, una interpretacion o una atribucion registrados
    despues no existen para esta ejecucion. Si se omite, es el reloj de la base al abrirla, una
    vez."""
    modo: ModoDecisionV2 = Field(
        sa_type=sa.Enum(
            ModoDecisionV2,
            name="modo_decision_v2",
            native_enum=False,
            create_constraint=True,
            length=12,
        )
    )
    zona_horaria: str = Field(max_length=64)
    """La zona del dia logico: as_of termina al iniciar el dia siguiente en ella."""
    version_motor: str = Field(max_length=32)
    """decision/v2. Gobierna tambien el esquema de los motivos."""
    version_features: str = Field(max_length=32)
    """features-decision/v1: que variables y con que definicion."""
    version_politica: str = Field(max_length=40)
    """decision-policy/public-v1: que reglas."""
    hash_politica: str = Field(max_length=64)
    """SHA-256 de la definicion canonica de la politica: sus reglas, umbrales, efectos y mapeos."""
    firma_inputs: str = Field(max_length=64)
    """SHA-256 de las entradas logicas: versiones, hash de la politica, cartera, corte, as_of,
    known_at y zona. Las mismas entradas son la misma decision."""
    estado: EstadoDecisionV2 = Field(
        default=EstadoDecisionV2.EN_PROCESO,
        sa_type=sa.Enum(
            EstadoDecisionV2,
            name="estado_decision_v2",
            native_enum=False,
            create_constraint=True,
            length=12,
        ),
    )
    resultado: str | None = Field(default=None, max_length=40)
    """Como termino: DECISION_PUBLICADA, YA_DECIDIDA, CUENTAS_NO_DECIDIBLES..."""
    cuentas_objetivo: int = Field(default=0, sa_type=sa.BigInteger)
    """Las cuentas del corte: sus snapshots. Se fija al abrir."""
    cuentas_decididas: int = Field(default=0, sa_type=sa.BigInteger)
    """Cuantas decisiones publico: todas las objetivo en una EXITOSA, cero en las demas."""
    cuentas_fallidas: int = Field(default=0, sa_type=sa.BigInteger)
    """Cuantas no se pudieron decidir. Con una sola, no se publica ninguna."""
    disponibilidad: dict | None = Field(default=None, sa_type=JSONB)
    """Que informacion estaba disponible a known_at, por familia y ventana: la cobertura de los
    pagos y de la atribucion de cada mes, y hasta donde llegaban los pagos. Explica cada NULL."""
    distribucion: dict | None = Field(default=None, sa_type=JSONB)
    """Cuantas decisiones de cada prioridad, canal y ajuste, y cuantas activaron cada regla: lo que
    publico, contado en la transaccion que lo publico."""
    iniciada_en: datetime = Field(default_factory=ahora, sa_type=sa.DateTime(timezone=True))
    terminada_en: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    detalle: str | None = None


class FeatureDecisionCuenta(SQLModel, table=True):
    """Las features exactas con que una ejecucion de decision/v2 decidio una cuenta.

    Una fila por cuenta del corte y ejecucion, inmutable: no se recalcula al consultar una decision
    historica. Su llave foranea compuesta hacia el snapshot garantiza que la cuenta es del corte, y
    la de la ejecucion, que el corte es el suyo. `feature_hash` es el SHA-256 de sus valores en
    forma canonica, con la version de las features delante. Un NULL es informacion no disponible a
    known_at, nunca un cero. Ni se actualiza ni se borra: un trigger de la 0011 rechaza cualquier
    UPDATE o DELETE sobre esta tabla.
    """

    __tablename__ = "feature_decision_cuenta"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["ejecucion_decision_v2_id", "corte_canonico_id"],
            ["ejecucion_decision_v2.id", "ejecucion_decision_v2.corte_canonico_id"],
            name="fk_feature_decision_ejecucion",
        ),
        sa.ForeignKeyConstraint(
            ["corte_canonico_id", "cuenta_canonica_id"],
            ["snapshot_cuenta.corte_canonico_id", "snapshot_cuenta.cuenta_canonica_id"],
            name="fk_feature_decision_snapshot",
        ),
        sa.CheckConstraint(
            "octet_length(feature_hash) = 32", name=conv("ck_feature_decision_hash")
        ),
        sa.CheckConstraint(
            "cortes_observados >= 1 AND reingresos_observados >= 0 AND gestiones_7d >= 0 "
            "AND gestiones_7d <= gestiones_30d AND gestiones_sin_contacto_7d <= gestiones_7d "
            "AND contactos_titular_30d + contactos_tercero_30d <= gestiones_contactables_30d "
            "AND gestiones_contactables_30d <= gestiones_30d AND visitas_30d <= gestiones_30d "
            "AND promesas_30d <= promesas_90d AND promesas_incumplidas_30d <= "
            "promesas_incumplidas_90d AND convenios_90d >= 0 "
            "AND (dias_desde_ultima_gestion IS NULL OR dias_desde_ultima_gestion >= 0) "
            "AND (dias_desde_ultimo_pago IS NULL OR dias_desde_ultimo_pago >= 0)",
            name=conv("ck_feature_decision_conteos"),
        ),
        sa.CheckConstraint(
            "(tasa_contacto_titular_30d IS NULL) = (gestiones_contactables_30d = 0) "
            "AND (tasa_contacto_titular_30d IS NULL OR tasa_contacto_titular_30d BETWEEN 0 AND 1)",
            name=conv("ck_feature_decision_tasa"),
        ),
    )

    # Las columnas de 8 bytes primero.
    dias_atraso: int = Field(sa_type=sa.BigInteger)
    semanas_atraso: int | None = Field(default=None, sa_type=sa.BigInteger)
    delta_dias_atraso_desde_corte_anterior: int | None = Field(default=None, sa_type=sa.BigInteger)
    # La llave primaria es la natural: una cuenta, una vez por ejecucion.
    ejecucion_decision_v2_id: int = Field(primary_key=True)
    cuenta_canonica_id: int = Field(primary_key=True)
    corte_canonico_id: int
    """El de su ejecucion: lo exige su llave foranea compuesta."""
    cortes_observados: int
    reingresos_observados: int
    dias_desde_ultima_gestion: int | None = None
    gestiones_7d: int
    gestiones_30d: int
    gestiones_sin_contacto_7d: int
    contactos_titular_30d: int
    contactos_tercero_30d: int
    visitas_30d: int
    gestiones_contactables_30d: int
    promesas_30d: int
    promesas_90d: int
    promesas_cumplidas_90d: int
    promesas_parciales_90d: int
    promesas_incumplidas_30d: int
    promesas_incumplidas_90d: int
    convenios_90d: int
    dias_desde_ultimo_pago: int | None = None
    movimientos_pago_7d: int | None = None
    movimientos_pago_30d: int | None = None
    pagos_asociados_unicos_30d: int | None = None
    saldo_total: Decimal = Field(max_digits=14, decimal_places=2)
    saldo_atrasado: Decimal | None = Field(default=None, max_digits=14, decimal_places=2)
    saldo_requerido: Decimal | None = Field(default=None, max_digits=14, decimal_places=2)
    moratorios: Decimal | None = Field(default=None, max_digits=14, decimal_places=2)
    delta_saldo_total_desde_corte_anterior: Decimal | None = Field(
        default=None, max_digits=14, decimal_places=2
    )
    tasa_contacto_titular_30d: Decimal | None = Field(default=None, max_digits=5, decimal_places=4)
    recuperacion_interpretada_7d: Decimal | None = Field(
        default=None, max_digits=18, decimal_places=2
    )
    recuperacion_interpretada_30d: Decimal | None = Field(
        default=None, max_digits=18, decimal_places=2
    )
    recuperacion_interpretada_90d: Decimal | None = Field(
        default=None, max_digits=18, decimal_places=2
    )
    recuperacion_asociada_unica_30d: Decimal | None = Field(
        default=None, max_digits=18, decimal_places=2
    )
    dias_gestion_a_pago_mediana_90d: Decimal | None = Field(
        default=None, max_digits=7, decimal_places=2
    )
    producto: str = Field(max_length=20)
    estrategia: str | None = None
    estatus_plan: str | None = None
    ultima_gestion_canal: str | None = Field(default=None, max_length=12)
    ultima_gestion_nivel_contacto: str | None = Field(default=None, max_length=20)
    continuo_desde_corte_anterior: bool | None = None
    promesa_vigente: bool
    convenio_vigente: bool
    feature_hash: bytes = Field(sa_type=sa.LargeBinary)


class ResultadoDecisionV2(SQLModel, table=True):
    """Lo que una ejecucion de decision/v2 decidio de una cuenta, y por que.

    Apunta a sus features por la misma llave natural (la llave foranea compuesta es su
    `feature_decision_id`). Guarda la prioridad base, el ajuste, la prioridad final con su nivel
    ordenado, el canal y los motivos: el codigo, las features con su valor, el operador, el umbral
    y el efecto de cada regla que aplico. Ningun nivel es una probabilidad: es el orden de la escala
    de prioridades. `decision_hash` resume la decision con el hash de sus features y el de la
    politica. Como sus features, no se actualiza ni se borra: lo impide el mismo trigger.
    """

    __tablename__ = "resultado_decision_v2"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["ejecucion_decision_v2_id", "cuenta_canonica_id"],
            [
                "feature_decision_cuenta.ejecucion_decision_v2_id",
                "feature_decision_cuenta.cuenta_canonica_id",
            ],
            name="fk_resultado_decision_v2_feature",
        ),
        # Una pagina de una prioridad o de un canal, en el orden de la cuenta, sin recorrer las
        # demas.
        sa.Index(
            "ix_resultado_decision_v2_prioridad",
            "ejecucion_decision_v2_id",
            "prioridad_final",
            "cuenta_canonica_id",
        ),
        sa.Index(
            "ix_resultado_decision_v2_canal",
            "ejecucion_decision_v2_id",
            "canal_recomendado",
            "cuenta_canonica_id",
        ),
        # Las decisiones de una cuenta en todas las ejecuciones: unas pocas por corte.
        sa.Index(
            "ix_resultado_decision_v2_cuenta", "cuenta_canonica_id", "ejecucion_decision_v2_id"
        ),
        sa.CheckConstraint(NIVELES_DE_PRIORIDAD, name=conv("ck_resultado_decision_v2_niveles")),
        sa.CheckConstraint(
            "octet_length(decision_hash) = 32", name=conv("ck_resultado_decision_v2_hash")
        ),
    )

    ejecucion_decision_v2_id: int = Field(primary_key=True)
    cuenta_canonica_id: int = Field(primary_key=True)
    score_nivel_base: int = Field(sa_type=sa.SmallInteger)
    """El orden de la prioridad base: BAJA 1, MEDIA 2, ALTA 3, MUY_ALTA 4. No es una
    probabilidad."""
    ajuste_total: int = Field(sa_type=sa.SmallInteger)
    """La suma de los efectos de las reglas que aplicaron, antes de acotar."""
    score_nivel_final: int = Field(sa_type=sa.SmallInteger)
    prioridad_base: str = Field(max_length=12)
    prioridad_final: str = Field(max_length=12)
    canal_recomendado: str = Field(max_length=20)
    motivos: list[dict] = Field(sa_type=JSONB)
    """Por que, regla por regla, con el esquema de decision/v2: la prioridad base, cada modificador
    que aplico y la prioridad final con su canal."""
    decision_hash: bytes = Field(sa_type=sa.LargeBinary)


# --- la geografia real ----------------------------------------------------------------------------
#
# Desde la 0012, con PostGIS. Dos capas que no se mezclan. La GEOGRAFIA dice que ubicacion se
# observo y en que municipio cae: una referencia publica versionada (VersionGeografia, con sus
# MunicipioGeografico) y, por corte, que concluyo geografia/v1 de la coordenada de cada cuenta
# (EjecucionGeografia, ResultadoGeografiaCuenta). Lo TERRITORIAL dice que base operativa la atiende
# segun una politica: las bases son configuracion operacional versionada (VersionBasesOperativas,
# BaseOperativa, CoberturaBaseMunicipio) y territorial/v2 asigna con una politica declarada
# (EjecucionTerritorialV2, AsignacionTerritorialV2). Una politica nueva reutiliza la misma
# geografia.
#
# Todo se guarda en EPSG:4326 (X = longitud, Y = latitud). La cartografia es referencia publica, no
# una tercera fuente de negocio, y las bases no son datos de CARTERA. Lo publicado no se reescribe:
# triggers de la 0012 rechazan cualquier UPDATE o DELETE sobre los municipios, los resultados, las
# bases y las asignaciones. Las tablas por cuenta no tienen id propio: su llave es la natural.

ESTADOS_GEOGRAFIA = (
    "VALIDA",
    "SIN_COORDENADAS",
    "COORDENADAS_INCOMPLETAS",
    "COORDENADAS_INVALIDAS",
    "FUERA_DE_REFERENCIA",
    "MUNICIPIO_AMBIGUO",
    "DISCREPANCIA_MUNICIPIO",
)
"""El vocabulario de geografia/v1. La base lo exige, y con el la coherencia de cada resultado."""

SIN_PUNTO = "('SIN_COORDENADAS', 'COORDENADAS_INCOMPLETAS', 'COORDENADAS_INVALIDAS')"
CON_MUNICIPIO = "('VALIDA', 'DISCREPANCIA_MUNICIPIO')"

COHERENCIA_DE_LA_GEOGRAFIA = (
    "estado_geografia IN ("
    + ", ".join(f"'{e}'" for e in ESTADOS_GEOGRAFIA)
    + f") AND (punto IS NULL) = (estado_geografia IN {SIN_PUNTO}) "
    f"AND (municipio_geografico_id IS NOT NULL) = (estado_geografia IN {CON_MUNICIPIO}) "
    "AND (estado_geografia <> 'DISCREPANCIA_MUNICIPIO' OR coincide_municipio = false) "
    "AND (estado_geografia <> 'VALIDA' OR coincide_municipio IS DISTINCT FROM false) "
    f"AND (estado_geografia IN {CON_MUNICIPIO} OR coincide_municipio IS NULL)"
)
"""Un punto existe solo si las coordenadas son utilizables; un municipio, solo si el punto cae en
exactamente uno; una VALIDA nunca discrepa de la fuente y una DISCREPANCIA_MUNICIPIO siempre."""

OBSERVADAS_DE_LA_GEOGRAFIA = (
    "(estado_geografia = 'SIN_COORDENADAS') = "
    "(latitud_observada IS NULL AND longitud_observada IS NULL) "
    "AND (estado_geografia = 'COORDENADAS_INCOMPLETAS') = "
    "((latitud_observada IS NULL) <> (longitud_observada IS NULL))"
)
"""Lo observado se guarda tal como vino: sin coordenadas es sin las dos, incompletas es con una."""

CONTEOS_DE_GEOGRAFIA = (
    "validas",
    "sin_coordenadas",
    "incompletas",
    "invalidas",
    "fuera_cobertura",
    "ambiguas",
    "discrepantes",
)
"""Las cuentas de una ejecucion de geografia/v1, por estado: una columna por estado."""

_SUMA_DE_GEOGRAFIA = " + ".join(CONTEOS_DE_GEOGRAFIA)
PUBLICACION_DE_GEOGRAFIA = (
    f"(estado = 'EXITOSA' AND cuentas_objetivo > 0 AND {_SUMA_DE_GEOGRAFIA} = cuentas_objetivo) "
    f"OR (estado <> 'EXITOSA' AND {_SUMA_DE_GEOGRAFIA} = 0)"
)
"""Una EXITOSA clasifico cada cuenta de su corte en exactamente un estado; las demas, ninguna."""

ESTADOS_ASIGNACION = (
    "ASIGNADA",
    "REVISION_GEOGRAFICA",
    "FUERA_DE_COBERTURA",
    "SIN_BASE_ELEGIBLE",
    "AMBIGUA_BASE",
)
"""El vocabulario de territorial/v2."""

COHERENCIA_DE_LA_ASIGNACION = (
    "estado_asignacion IN ("
    + ", ".join(f"'{e}'" for e in ESTADOS_ASIGNACION)
    + ") AND (base_operativa_id IS NOT NULL) = (estado_asignacion = 'ASIGNADA') "
    "AND (distancia_base_m IS NOT NULL) = (estado_asignacion = 'ASIGNADA') "
    "AND (distancia_base_m IS NULL OR distancia_base_m >= 0) "
    "AND (municipio_geografico_id IS NOT NULL) = "
    "(estado_asignacion IN ('ASIGNADA', 'SIN_BASE_ELEGIBLE', 'AMBIGUA_BASE'))"
)
"""Solo una ASIGNADA tiene base y distancia; solo las que tenian geografia confiable, municipio."""

CONTEOS_DE_TERRITORIAL_V2 = (
    "asignadas",
    "revision_geografica",
    "sin_base",
    "fuera_cobertura",
    "ambiguas",
)

_SUMA_DE_TERRITORIAL_V2 = " + ".join(CONTEOS_DE_TERRITORIAL_V2)
PUBLICACION_DE_TERRITORIAL_V2 = (
    f"(estado = 'EXITOSA' AND cuentas_objetivo > 0 "
    f"AND {_SUMA_DE_TERRITORIAL_V2} = cuentas_objetivo) "
    f"OR (estado <> 'EXITOSA' AND {_SUMA_DE_TERRITORIAL_V2} = 0)"
)

PATRON_CVEGEO = "^[0-9]{5}$"
"""La clave geoestadistica municipal concatenada del INEGI: entidad (2) y municipio (3)."""
PATRON_CODIGO_BASE = "^[A-Z0-9_-]{1,32}$"


class EstadoVersionGeografia(StrEnum):
    """En que quedo la importacion de una referencia geografica. Solo una PUBLICADA se usa."""

    EN_PROCESO = (
        "EN_PROCESO"  # registrada; se estan leyendo, validando y escribiendo sus municipios
    )
    PUBLICADA = "PUBLICADA"  # sus municipios estan completos; ya no cambia
    FALLIDA = "FALLIDA"  # no se importo: no tiene ningun municipio


class NivelGeografia(StrEnum):
    """El nivel geoestadistico de una referencia. v0.11 solo importa el municipal."""

    MUNICIPAL = "MUNICIPAL"


class VersionGeografia(SQLModel, table=True):
    """Una version importada de una referencia geografica publica: de que producto y edicion salio,
    de que archivo (por su SHA-256), en que sistema de coordenadas venia y como se importo.

    Es REFERENCIA GEOGRAFICA PUBLICA, no una fuente de negocio: no es CARTERA ni PAGOS, y su archivo
    no va al almacen de artefactos fuente, que es la evidencia del acreedor. El archivo oficial vive
    fuera del repositorio; esta fila lo identifica por su SHA-256 y su manifiesto. Una version
    PUBLICADA no cambia nunca (un trigger de la 0012 lo impide): si el INEGI publica otra edicion,
    se importa otra version, y las ejecuciones viejas siguen apuntando a la suya.
    """

    __tablename__ = "version_geografia"
    __table_args__ = (
        # El mismo archivo y la misma capa se publican una sola vez, y a lo mas una importacion lo
        # procesa a la vez. Una FALLIDA no cuenta: se puede volver a intentar.
        sa.Index(
            "ux_version_geografia_publicada",
            "sha256",
            "capa",
            unique=True,
            postgresql_where=sa.text("estado = 'PUBLICADA'"),
        ),
        sa.Index(
            "ux_version_geografia_en_proceso",
            "sha256",
            "capa",
            unique=True,
            postgresql_where=sa.text("estado = 'EN_PROCESO'"),
        ),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name=conv("ck_version_geografia_sha256")),
        sa.CheckConstraint(
            "srid = 4326 AND srid_origen > 0 AND tamano_bytes > 0",
            name=conv("ck_version_geografia_origen"),
        ),
        sa.CheckConstraint(
            "municipios >= 0 AND reparadas >= 0 AND reparadas <= municipios "
            "AND (estado = 'PUBLICADA') = (municipios > 0)",
            name=conv("ck_version_geografia_conteos"),
        ),
        sa.CheckConstraint(
            "(estado = 'EN_PROCESO') = (terminada_en IS NULL)",
            name=conv("ck_version_geografia_terminada"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    geografia_version_id: UUID = Field(default_factory=uuid4, unique=True)
    """Identificador publico. El `id` es interno."""
    proveedor: str = Field(max_length=64)
    """Quien publica la referencia: INEGI, o SINTETICO en las pruebas."""
    producto: str = Field(max_length=200)
    """El nombre exacto del producto, como lo publica su proveedor."""
    edicion: str = Field(max_length=64)
    nivel: NivelGeografia = Field(
        sa_type=sa.Enum(
            NivelGeografia,
            name="nivel_geografia",
            native_enum=False,
            create_constraint=True,
            length=16,
        )
    )
    capa: str = Field(max_length=255)
    """La capa que se importo: el miembro del zip sin extension (`conjunto_de_datos/00mun`)."""
    archivo: str = Field(max_length=255)
    """El nombre del archivo que se leyo. Es metadata: lo identifica el SHA-256."""
    sha256: str = Field(max_length=64)
    """SHA-256 del archivo tal como se descargo. El archivo no se modifica nunca."""
    tamano_bytes: int = Field(sa_type=sa.BigInteger)
    srid: int = Field(default=4326)
    """El sistema en que se guardaron sus geometrias: siempre EPSG:4326."""
    srid_origen: int
    """El sistema en que venia la capa, reconocido por su .prj: EPSG:6372 en el INEGI."""
    estado: EstadoVersionGeografia = Field(
        default=EstadoVersionGeografia.EN_PROCESO,
        sa_type=sa.Enum(
            EstadoVersionGeografia,
            name="estado_version_geografia",
            native_enum=False,
            create_constraint=True,
            length=12,
        ),
    )
    municipios: int = 0
    """Cuantos municipios publico. Cero mientras no esta PUBLICADA."""
    reparadas: int = 0
    """Cuantas geometrias invalidas se repararon, de forma segura y registrada en `metadatos`."""
    metadatos: dict = Field(sa_type=JSONB)
    """Lo que dice de donde salio y como se importo: la URL oficial, la licencia, las fechas del
    producto, el CRS de origen, la codificacion, las geometrias reparadas y por que."""
    importada_en: datetime = Field(default_factory=ahora, sa_type=sa.DateTime(timezone=True))
    terminada_en: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    detalle: str | None = None


class MunicipioGeografico(SQLModel, table=True):
    """Un area geoestadistica municipal de una version de la referencia, con su geometria.

    Se identifica por su CVEGEO dentro de su version, nunca por su nombre. El nombre oficial es el
    del proveedor y se guarda aparte: no se compara con el que trae CARTERA, ni lo reescribe. La
    geometria es un MultiPolygon en EPSG:4326, valido, con su indice espacial GiST.
    """

    __tablename__ = "municipio_geografico"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["version_geografia_id"], ["version_geografia.id"], name="fk_municipio_version"
        ),
        sa.UniqueConstraint("version_geografia_id", "cvegeo", name="uq_municipio_version_cvegeo"),
        sa.Index("ix_municipio_geografico_geometria", "geometria", postgresql_using="gist"),
        sa.CheckConstraint(
            "cve_ent ~ '^[0-9]{2}$' AND cve_mun ~ '^[0-9]{3}$' AND cvegeo = cve_ent || cve_mun",
            name=conv("ck_municipio_claves"),
        ),
        sa.CheckConstraint(
            "ST_IsValid(geometria) AND NOT ST_IsEmpty(geometria)",
            name=conv("ck_municipio_geometria"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    version_geografia_id: int
    """Su version. La llave foranea, sin cascada, esta en __table_args__."""
    registro_origen: int
    """El numero de registro en la capa de origen, desde 1: su linaje dentro del archivo."""
    cve_ent: str = Field(max_length=2)
    cve_mun: str = Field(max_length=3)
    cvegeo: str = Field(max_length=5)
    nombre_oficial: str = Field(max_length=200)
    """NOMGEO, tal como lo publica el proveedor."""
    reparada: bool = False
    """Si su geometria era invalida y se reparo al importar. El motivo esta en `metadatos` de su
    version."""
    geometria: str = Field(sa_type=Geometria("MULTIPOLYGON", 4326))


class EstadoGeografia(StrEnum):
    """En que quedo una ejecucion de geografia/v1. Solo una EXITOSA publica algo."""

    EN_PROCESO = "EN_PROCESO"
    EXITOSA = "EXITOSA"
    FALLIDA = "FALLIDA"


class ResultadoEjecucionGeografia(StrEnum):
    """Como termino una ejecucion de geografia/v1."""

    GEOGRAFIA_PUBLICADA = "GEOGRAFIA_PUBLICADA"
    """EXITOSA: publico la geografia de cada cuenta del corte."""
    YA_GENERADA = "YA_GENERADA"
    """FALLIDA: otra ejecucion publico primero la misma firma de entrada."""
    VERSION_NO_SOPORTADA = "VERSION_NO_SOPORTADA"
    REFERENCIA_NO_PUBLICADA = "REFERENCIA_NO_PUBLICADA"
    """FALLIDA: la version de la referencia no esta PUBLICADA."""
    ARTEFACTO_CORRUPTO = "ARTEFACTO_CORRUPTO"
    """FALLIDA: los bytes del Parquet conformado ya no son los de su SHA-256."""
    DATOS_INCONSISTENTES = "DATOS_INCONSISTENTES"
    """FALLIDA: el dataset no es lo que su corte dice, o los conteos no cuadran."""
    ERROR_INTERNO = "ERROR_INTERNO"
    INTENTOS_AGOTADOS = "INTENTOS_AGOTADOS"


class EjecucionGeografia(SQLModel, table=True):
    """Una ejecucion de geografia/v1: la geografia de cada cuenta de un corte canonico, con una
    version declarada de la referencia.

    Lee el dataset conformado (cartera/v2) del corte, nunca el archivo original, y guarda que
    concluyo de la coordenada observada de cada cuenta: si es utilizable, en que municipio cae y si
    coincide con el municipio que resolvio la fuente. Sus entradas son el corte, su dataset, la
    version de la referencia y la del motor; su firma las resume, y la base garantiza a lo mas una
    EXITOSA y una EN_PROCESO por firma.
    """

    __tablename__ = "ejecucion_geografia"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["corte_canonico_id"], ["corte_canonico.id"], name="fk_geografia_corte"
        ),
        sa.ForeignKeyConstraint(
            ["dataset_conformado_id"], ["dataset_conformado.id"], name="fk_geografia_dataset"
        ),
        sa.ForeignKeyConstraint(
            ["version_geografia_id"], ["version_geografia.id"], name="fk_geografia_version"
        ),
        # Lo que referencia cada resultado y cada ejecucion territorial: la ejecucion junto con su
        # corte. Asi la base garantiza que un resultado es de una cuenta del corte de su ejecucion.
        sa.UniqueConstraint("id", "corte_canonico_id", name="uq_geografia_id_corte"),
        sa.Index(
            "ux_geografia_exitosa",
            "firma_inputs",
            unique=True,
            postgresql_where=sa.text("estado = 'EXITOSA'"),
        ),
        sa.Index(
            "ux_geografia_en_proceso",
            "firma_inputs",
            unique=True,
            postgresql_where=sa.text("estado = 'EN_PROCESO'"),
        ),
        # Las ejecuciones de un corte, en cualquier estado, la mas reciente primero.
        sa.Index("ix_geografia_corte", "corte_canonico_id", "id"),
        sa.CheckConstraint("firma_inputs ~ '^[0-9a-f]{64}$'", name=conv("ck_geografia_firma")),
        sa.CheckConstraint(
            "(estado = 'EN_PROCESO') = (resultado IS NULL)", name=conv("ck_geografia_resultado")
        ),
        sa.CheckConstraint(
            "cuentas_objetivo >= 0 AND " + " AND ".join(f"{c} >= 0" for c in CONTEOS_DE_GEOGRAFIA),
            name=conv("ck_geografia_conteos"),
        ),
        sa.CheckConstraint(PUBLICACION_DE_GEOGRAFIA, name=conv("ck_geografia_publicacion")),
    )

    id: int | None = Field(default=None, primary_key=True)
    geografia_run_id: UUID = Field(default_factory=uuid4, unique=True)
    """Identificador publico de este intento."""
    corte_canonico_id: int
    dataset_conformado_id: int
    """El dataset conformado de cartera/v2 que produjo el corte: de el se leen las coordenadas."""
    version_geografia_id: int
    version_motor: str = Field(max_length=32)
    """geografia/v1."""
    firma_inputs: str = Field(max_length=64)
    """SHA-256 de sus entradas: el motor, la referencia (su id y su SHA-256), el corte y su dataset
    con sus firmas de contenido. Las mismas entradas son la misma geografia."""
    estado: EstadoGeografia = Field(
        default=EstadoGeografia.EN_PROCESO,
        sa_type=sa.Enum(
            EstadoGeografia,
            name="estado_geografia_ejecucion",
            native_enum=False,
            create_constraint=True,
            length=12,
        ),
    )
    resultado: str | None = Field(default=None, max_length=40)
    cuentas_objetivo: int = Field(default=0, sa_type=sa.BigInteger)
    """Las cuentas del corte: sus snapshots. Se fija al abrir."""
    validas: int = Field(default=0, sa_type=sa.BigInteger)
    sin_coordenadas: int = Field(default=0, sa_type=sa.BigInteger)
    incompletas: int = Field(default=0, sa_type=sa.BigInteger)
    invalidas: int = Field(default=0, sa_type=sa.BigInteger)
    fuera_cobertura: int = Field(default=0, sa_type=sa.BigInteger)
    """FUERA_DE_REFERENCIA: un punto valido que no cae en ningun municipio de la referencia."""
    ambiguas: int = Field(default=0, sa_type=sa.BigInteger)
    """MUNICIPIO_AMBIGUO: un punto que cae en mas de un municipio."""
    discrepantes: int = Field(default=0, sa_type=sa.BigInteger)
    """DISCREPANCIA_MUNICIPIO: el municipio espacial no es el que resolvio la fuente."""
    distribucion: dict | None = Field(default=None, sa_type=JSONB)
    """Lo que publico, contado en la transaccion que lo publico: por estado, por motivo y por
    comparacion con la fuente. Sin coordenadas."""
    iniciada_en: datetime = Field(default_factory=ahora, sa_type=sa.DateTime(timezone=True))
    terminada_en: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    detalle: str | None = None


class ResultadoGeografiaCuenta(SQLModel, table=True):
    """Lo que geografia/v1 concluyo de la coordenada observada de una cuenta en una ejecucion.

    Guarda la coordenada tal como vino en la fuente (latitud y longitud observadas, que pueden ser
    invalidas o faltar), el punto que se construyo con ella si era utilizable, el municipio espacial
    si cae en exactamente uno, el municipio que resolvio la fuente y si coinciden, y los motivos. No
    corrige nada: una coordenada invertida o discrepante se queda como vino, con su motivo. Las
    coordenadas no salen por la API general. Ni se actualiza ni se borra.
    """

    __tablename__ = "resultado_geografia_cuenta"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["ejecucion_geografia_id", "corte_canonico_id"],
            ["ejecucion_geografia.id", "ejecucion_geografia.corte_canonico_id"],
            name="fk_resultado_geografia_ejecucion",
        ),
        sa.ForeignKeyConstraint(
            ["corte_canonico_id", "cuenta_canonica_id"],
            ["snapshot_cuenta.corte_canonico_id", "snapshot_cuenta.cuenta_canonica_id"],
            name="fk_resultado_geografia_snapshot",
        ),
        sa.ForeignKeyConstraint(
            ["municipio_geografico_id"],
            ["municipio_geografico.id"],
            name="fk_resultado_geografia_municipio",
        ),
        # Una pagina de un estado o de un municipio, en el orden de la cuenta, sin recorrer los
        # demas; y la geografia de una cuenta en todas las ejecuciones.
        sa.Index(
            "ix_resultado_geografia_estado",
            "ejecucion_geografia_id",
            "estado_geografia",
            "cuenta_canonica_id",
        ),
        sa.Index(
            "ix_resultado_geografia_municipio",
            "ejecucion_geografia_id",
            "municipio_geografico_id",
            "cuenta_canonica_id",
        ),
        sa.Index("ix_resultado_geografia_cuenta", "cuenta_canonica_id", "ejecucion_geografia_id"),
        sa.CheckConstraint(COHERENCIA_DE_LA_GEOGRAFIA, name=conv("ck_resultado_geografia_estado")),
        sa.CheckConstraint(
            OBSERVADAS_DE_LA_GEOGRAFIA, name=conv("ck_resultado_geografia_observadas")
        ),
        sa.CheckConstraint(
            f"municipio_fuente_resuelto IS NULL OR municipio_fuente_resuelto ~ '{PATRON_CVEGEO}'",
            name=conv("ck_resultado_geografia_fuente"),
        ),
    )

    # Las columnas de 8 bytes primero.
    latitud_observada: float | None = Field(default=None, sa_type=sa.Double)
    longitud_observada: float | None = Field(default=None, sa_type=sa.Double)
    source_row: int = Field(sa_type=sa.BigInteger)
    """La fila del archivo original de la que salio, como `_source_row` en el Parquet."""
    # La llave primaria es la natural: una cuenta, una vez por ejecucion.
    ejecucion_geografia_id: int = Field(primary_key=True)
    cuenta_canonica_id: int = Field(primary_key=True)
    corte_canonico_id: int
    """El de su ejecucion: lo exigen sus dos llaves foraneas compuestas."""
    municipio_geografico_id: int | None = None
    """El municipio espacial, si el punto cae en exactamente uno."""
    estado_geografia: str = Field(max_length=24)
    municipio_fuente_resuelto: str | None = Field(default=None, max_length=5)
    """El CVEGEO que resolvio la fuente con ESTADO_CTE y POBLACION_CTE (el de su snapshot)."""
    coincide_municipio: bool | None = None
    """True si el municipio espacial es el de la fuente (COINCIDE), False si no (DISCREPA), NULL si
    no se pueden comparar (NO_COMPARABLE)."""
    punto: str | None = Field(default=None, sa_type=Geometria("POINT", 4326))
    """ST_MakePoint(longitud, latitud) en EPSG:4326, solo si las coordenadas son utilizables."""
    source_sheet: str | None = Field(default=None, max_length=255)
    motivos: list[dict] = Field(sa_type=JSONB)
    """Por que salio asi, con codigos estables. Sin coordenadas."""


class EstadoVersionBases(StrEnum):
    """Una version de bases nace PUBLICADA, en la transaccion que la crea con sus bases y su
    cobertura: no hay versiones a medias, ni se procesa en segundo plano."""

    PUBLICADA = "PUBLICADA"


class VersionBasesOperativas(SQLModel, table=True):
    """Una version de la configuracion de bases operativas: que bases hay, donde estan, si estan
    activas y que municipios puede atender cada una.

    Es configuracion operacional versionada, no una fuente de negocio ni datos de CARTERA. Se
    identifica por el SHA-256 de su configuracion en forma canonica: el mismo contenido es la misma
    version, que se reutiliza. Una version publicada no cambia (un trigger lo impide): cambiar una
    base es publicar otra version, y las ejecuciones viejas siguen con la suya.
    """

    __tablename__ = "version_bases_operativas"
    __table_args__ = (
        sa.UniqueConstraint("hash_configuracion", name="uq_version_bases_hash"),
        sa.CheckConstraint(
            "hash_configuracion ~ '^[0-9a-f]{64}$'", name=conv("ck_version_bases_hash")
        ),
        sa.CheckConstraint(
            "bases > 0 AND activas >= 0 AND activas <= bases AND coberturas >= 0",
            name=conv("ck_version_bases_conteos"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    bases_version_id: UUID = Field(default_factory=uuid4, unique=True)
    """Identificador publico."""
    nombre: str = Field(max_length=120)
    """Una etiqueta para personas. No entra en el hash: no cambia lo que la version dice."""
    version_configuracion: str = Field(max_length=32)
    """bases-operativas/v1: la forma canonica con que se calculo el hash."""
    hash_configuracion: str = Field(max_length=64)
    estado: EstadoVersionBases = Field(
        default=EstadoVersionBases.PUBLICADA,
        sa_type=sa.Enum(
            EstadoVersionBases,
            name="estado_version_bases",
            native_enum=False,
            create_constraint=True,
            length=12,
        ),
    )
    bases: int
    activas: int
    coberturas: int
    """Cuantos pares (base, municipio) declara."""
    creada_en: datetime = Field(default_factory=ahora, sa_type=sa.DateTime(timezone=True))


class BaseOperativa(SQLModel, table=True):
    """Una base de una version: un punto desde el cual puede organizarse trabajo de campo.

    No representa capacidad, gestores, jornadas ni rutas: eso es de v0.12 en adelante. El mismo
    codigo en otra version es otra fila, con su propio base_id.
    """

    __tablename__ = "base_operativa"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["version_bases_operativas_id"],
            ["version_bases_operativas.id"],
            name="fk_base_version",
        ),
        sa.UniqueConstraint("version_bases_operativas_id", "codigo", name="uq_base_version_codigo"),
        # Lo que referencia cada asignacion: la base junto con su version. Asi la base garantiza
        # que una asignacion solo apunta a una base de la version que su ejecucion declaro.
        sa.UniqueConstraint("id", "version_bases_operativas_id", name="uq_base_id_version"),
        sa.CheckConstraint(f"codigo ~ '{PATRON_CODIGO_BASE}'", name=conv("ck_base_codigo")),
    )

    id: int | None = Field(default=None, primary_key=True)
    base_id: UUID = Field(default_factory=uuid4, unique=True)
    """Identificador publico."""
    version_bases_operativas_id: int
    codigo: str = Field(max_length=32)
    nombre: str = Field(max_length=120)
    activa: bool
    punto: str = Field(sa_type=Geometria("POINT", 4326))
    """Donde esta, en EPSG:4326."""


class CoberturaBaseMunicipio(SQLModel, table=True):
    """Que municipio puede atender una base: declarado, nunca inferido porque la base este cerca.

    El municipio se nombra por su CVEGEO, no por la fila de una version de la referencia: la
    configuracion de bases no depende de una edicion de la cartografia.
    """

    __tablename__ = "cobertura_base_municipio"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["base_operativa_id"], ["base_operativa.id"], name="fk_cobertura_base"
        ),
        # Las bases que cubren un municipio.
        sa.Index("ix_cobertura_cvegeo", "cvegeo", "base_operativa_id"),
        sa.CheckConstraint(f"cvegeo ~ '{PATRON_CVEGEO}'", name=conv("ck_cobertura_cvegeo")),
    )

    base_operativa_id: int = Field(primary_key=True)
    cvegeo: str = Field(primary_key=True, max_length=5)


class EstadoTerritorialV2(StrEnum):
    """En que quedo una ejecucion de territorial/v2. Solo una EXITOSA publica algo."""

    EN_PROCESO = "EN_PROCESO"
    EXITOSA = "EXITOSA"
    FALLIDA = "FALLIDA"


class ResultadoEjecucionTerritorialV2(StrEnum):
    """Como termino una ejecucion de territorial/v2."""

    TERRITORIAL_PUBLICADO = "TERRITORIAL_PUBLICADO"
    """EXITOSA: publico la asignacion de cada cuenta del corte."""
    YA_GENERADO = "YA_GENERADO"
    """FALLIDA: otra ejecucion publico primero la misma firma de entrada."""
    VERSION_NO_SOPORTADA = "VERSION_NO_SOPORTADA"
    GEOGRAFIA_NO_EXITOSA = "GEOGRAFIA_NO_EXITOSA"
    """FALLIDA: la ejecucion de geografia que declara no esta EXITOSA."""
    DATOS_INCONSISTENTES = "DATOS_INCONSISTENTES"
    ERROR_INTERNO = "ERROR_INTERNO"
    INTENTOS_AGOTADOS = "INTENTOS_AGOTADOS"


class EjecucionTerritorialV2(SQLModel, table=True):
    """Una ejecucion de territorial/v2: a que base operativa se asigna cada cuenta de un corte, con
    entradas declaradas.

    Declara exactamente su corte, su ejecucion de geografia (EXITOSA y del mismo corte, lo garantiza
    la base), su version de bases y su politica con su hash. Nunca usa la geografia mas nueva ni las
    bases actuales: lo que se ejecuto se puede volver a ejecutar igual.
    """

    __tablename__ = "ejecucion_territorial_v2"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["ejecucion_geografia_id", "corte_canonico_id"],
            ["ejecucion_geografia.id", "ejecucion_geografia.corte_canonico_id"],
            name="fk_territorial_v2_geografia",
        ),
        sa.ForeignKeyConstraint(
            ["version_bases_operativas_id"],
            ["version_bases_operativas.id"],
            name="fk_territorial_v2_bases",
        ),
        # Lo que referencia cada asignacion: la ejecucion junto con su geografia y sus bases.
        sa.UniqueConstraint(
            "id",
            "ejecucion_geografia_id",
            "version_bases_operativas_id",
            name="uq_territorial_v2_entradas",
        ),
        # Desde la 0013, lo que referencia cada plan de campo/v2: la ejecucion junto con su corte y
        # su version de bases. Asi la base garantiza que el plan, su decision y su configuracion son
        # del mismo corte y de las mismas bases.
        sa.UniqueConstraint(
            "id",
            "corte_canonico_id",
            "version_bases_operativas_id",
            name="uq_territorial_v2_corte_bases",
        ),
        sa.Index(
            "ux_territorial_v2_exitosa",
            "firma_inputs",
            unique=True,
            postgresql_where=sa.text("estado = 'EXITOSA'"),
        ),
        sa.Index(
            "ux_territorial_v2_en_proceso",
            "firma_inputs",
            unique=True,
            postgresql_where=sa.text("estado = 'EN_PROCESO'"),
        ),
        sa.Index("ix_territorial_v2_corte", "corte_canonico_id", "id"),
        sa.CheckConstraint(
            "firma_inputs ~ '^[0-9a-f]{64}$' AND hash_politica ~ '^[0-9a-f]{64}$'",
            name=conv("ck_territorial_v2_firmas"),
        ),
        sa.CheckConstraint(
            "(estado = 'EN_PROCESO') = (resultado IS NULL)",
            name=conv("ck_territorial_v2_resultado"),
        ),
        sa.CheckConstraint(
            "cuentas_objetivo >= 0 AND "
            + " AND ".join(f"{c} >= 0" for c in CONTEOS_DE_TERRITORIAL_V2),
            name=conv("ck_territorial_v2_conteos"),
        ),
        sa.CheckConstraint(
            PUBLICACION_DE_TERRITORIAL_V2, name=conv("ck_territorial_v2_publicacion")
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    territorial_run_id: UUID = Field(default_factory=uuid4, unique=True)
    """Identificador publico. No es el de territorial/v1, que vive en otra tabla."""
    corte_canonico_id: int
    ejecucion_geografia_id: int
    version_bases_operativas_id: int
    version_motor: str = Field(max_length=32)
    """territorial/v2."""
    version_politica: str = Field(max_length=40)
    """territorial-public-v2."""
    hash_politica: str = Field(max_length=64)
    """SHA-256 de la definicion canonica de la politica, con su tolerancia de empate."""
    firma_inputs: str = Field(max_length=64)
    estado: EstadoTerritorialV2 = Field(
        default=EstadoTerritorialV2.EN_PROCESO,
        sa_type=sa.Enum(
            EstadoTerritorialV2,
            name="estado_territorial_v2",
            native_enum=False,
            create_constraint=True,
            length=12,
        ),
    )
    resultado: str | None = Field(default=None, max_length=40)
    cuentas_objetivo: int = Field(default=0, sa_type=sa.BigInteger)
    asignadas: int = Field(default=0, sa_type=sa.BigInteger)
    revision_geografica: int = Field(default=0, sa_type=sa.BigInteger)
    sin_base: int = Field(default=0, sa_type=sa.BigInteger)
    fuera_cobertura: int = Field(default=0, sa_type=sa.BigInteger)
    ambiguas: int = Field(default=0, sa_type=sa.BigInteger)
    """AMBIGUA_BASE: dos o mas bases a la misma distancia, dentro de la tolerancia."""
    distribucion: dict | None = Field(default=None, sa_type=JSONB)
    """Cuantas asignaciones por base y por motivo. Es diagnostico: v0.11 no balancea cargas."""
    iniciada_en: datetime = Field(default_factory=ahora, sa_type=sa.DateTime(timezone=True))
    terminada_en: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    detalle: str | None = None


class AsignacionTerritorialV2(SQLModel, table=True):
    """Lo que territorial/v2 concluyo de una cuenta: a que base se asigna, o por que no.

    Apunta a su resultado geografico por su llave natural (ejecucion de geografia, cuenta), y a su
    base junto con la version de bases de su ejecucion: la base garantiza que la geografia y la base
    son las que la ejecucion declaro. La distancia es geodesica, en metros. Ni se actualiza ni se
    borra: una asignacion historica no se sobrescribe.
    """

    __tablename__ = "asignacion_territorial_v2"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            [
                "ejecucion_territorial_v2_id",
                "ejecucion_geografia_id",
                "version_bases_operativas_id",
            ],
            [
                "ejecucion_territorial_v2.id",
                "ejecucion_territorial_v2.ejecucion_geografia_id",
                "ejecucion_territorial_v2.version_bases_operativas_id",
            ],
            name="fk_asignacion_v2_ejecucion",
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_geografia_id", "cuenta_canonica_id"],
            [
                "resultado_geografia_cuenta.ejecucion_geografia_id",
                "resultado_geografia_cuenta.cuenta_canonica_id",
            ],
            name="fk_asignacion_v2_geografia",
        ),
        sa.ForeignKeyConstraint(
            ["base_operativa_id", "version_bases_operativas_id"],
            ["base_operativa.id", "base_operativa.version_bases_operativas_id"],
            name="fk_asignacion_v2_base",
        ),
        sa.ForeignKeyConstraint(
            ["municipio_geografico_id"],
            ["municipio_geografico.id"],
            name="fk_asignacion_v2_municipio",
        ),
        # Una pagina de una base, de un estado o de un municipio, en el orden de la cuenta; y las
        # asignaciones de una cuenta en todas las ejecuciones.
        sa.Index(
            "ix_asignacion_v2_base",
            "ejecucion_territorial_v2_id",
            "base_operativa_id",
            "cuenta_canonica_id",
        ),
        sa.Index(
            "ix_asignacion_v2_estado",
            "ejecucion_territorial_v2_id",
            "estado_asignacion",
            "cuenta_canonica_id",
        ),
        sa.Index(
            "ix_asignacion_v2_municipio",
            "ejecucion_territorial_v2_id",
            "municipio_geografico_id",
            "cuenta_canonica_id",
        ),
        sa.Index("ix_asignacion_v2_cuenta", "cuenta_canonica_id", "ejecucion_territorial_v2_id"),
        sa.CheckConstraint(COHERENCIA_DE_LA_ASIGNACION, name=conv("ck_asignacion_v2_estado")),
    )

    distancia_base_m: Decimal | None = Field(default=None, max_digits=12, decimal_places=2)
    """Geodesica (PostGIS geography, elipsoide WGS 84) del punto de la cuenta a su base, en metros,
    redondeada al centimetro. Solo en una ASIGNADA."""
    ejecucion_territorial_v2_id: int = Field(primary_key=True)
    cuenta_canonica_id: int = Field(primary_key=True)
    ejecucion_geografia_id: int
    version_bases_operativas_id: int
    municipio_geografico_id: int | None = None
    base_operativa_id: int | None = None
    estado_asignacion: str = Field(max_length=20)
    motivos: list[dict] = Field(sa_type=JSONB)
    """Por que salio asi: la geografia que no era confiable, las bases elegibles, la mas cercana o
    el empate, con codigos estables. Sin coordenadas."""


# --- campo/v2 -------------------------------------------------------------------------------------
#
# Desde la 0013. Dos capas que no se mezclan. La CONFIGURACION DE CAMPO es configuracion operacional
# versionada sobre una version de bases, como las bases mismas: sus gestores (un codigo opaco, nunca
# una persona), sus calendarios con las ventanas de cada dia y sus excepciones, el cupo declarado de
# cada gestor y la zona horaria (VersionConfiguracionCampo, CalendarioCampo, VentanaJornadaCampo,
# ExcepcionCalendarioCampo, GestorCampo). Un PLAN dice, para una fecha de operacion, que candidata
# de campo de una decision/v2 quedo con que gestor de su base territorial, en que zona, subzona,
# tramo y microtramo, o por que no (EjecucionCampoV2, AsignacionCampoCuenta), con las unidades que
# ocupo y la carga de cada gestor (UnidadTerritorialCampo, CargaGestorCampo).
#
# Ninguna tabla guarda una coordenada ni una geometria: la particion es un identificador. Lo
# publicado no se reescribe: triggers de la 0013 rechazan cualquier UPDATE o DELETE sobre la
# configuracion, las asignaciones, las unidades y las cargas, y sobre un plan terminado.

ESTADOS_CAMPO = ("ASIGNADA", "REVISION_TERRITORIAL", "SIN_GESTOR_DISPONIBLE", "SIN_CAPACIDAD")
"""El vocabulario de campo/v2: cada candidata queda en exactamente uno."""

PRIORIDADES = ("BAJA", "MEDIA", "ALTA", "MUY_ALTA")

COHERENCIA_DEL_CAMPO = (
    "estado IN ("
    + ", ".join(f"'{e}'" for e in ESTADOS_CAMPO)
    + ") AND prioridad IN ("
    + ", ".join(f"'{p}'" for p in PRIORIDADES)
    + ") AND (gestor_campo_id IS NOT NULL) = (estado = 'ASIGNADA') "
    "AND (base_operativa_id IS NOT NULL) = (estado <> 'REVISION_TERRITORIAL') "
    "AND (zona_id IS NOT NULL) = (estado <> 'REVISION_TERRITORIAL') "
    "AND (subzona_id IS NOT NULL) = (estado <> 'REVISION_TERRITORIAL') "
    "AND (tramo_id IS NOT NULL) = (estado <> 'REVISION_TERRITORIAL') "
    "AND (microtramo_id IS NOT NULL) = (estado <> 'REVISION_TERRITORIAL') "
    "AND (orden_prioridad IS NOT NULL) = (estado <> 'REVISION_TERRITORIAL') "
    "AND (orden_prioridad IS NULL OR orden_prioridad >= 1) "
    "AND (NOT es_rebalanceada OR estado = 'ASIGNADA')"
)
"""Solo una ASIGNADA tiene gestor, y solo ella puede ser rebalanceada; una candidata en revision
territorial no tiene base, ni particion, ni lugar en la cola de capacidad de una base."""

CONTEOS_DE_CAMPO_V2 = ("asignadas", "revision_territorial", "sin_gestor", "sin_capacidad")
"""Las candidatas de un plan, por estado: una columna por estado."""

_SUMA_DE_CAMPO_V2 = " + ".join(CONTEOS_DE_CAMPO_V2)
PUBLICACION_DE_CAMPO_V2 = (
    f"(estado = 'EXITOSA' AND {_SUMA_DE_CAMPO_V2} = candidatas_campo "
    "AND candidatas_campo <= cuentas_evaluadas_decision AND rebalanceadas <= asignadas "
    "AND capacidad_utilizada = asignadas AND asignadas <= capacidad_total) "
    f"OR (estado <> 'EXITOSA' AND candidatas_campo = 0 AND {_SUMA_DE_CAMPO_V2} = 0 "
    "AND rebalanceadas = 0 AND capacidad_total = 0 AND capacidad_utilizada = 0)"
)
"""Un plan EXITOSA puso cada candidata en exactamente un estado, y nunca uso mas capacidad que la
declarada; los demas no publicaron nada."""

NIVELES_DE_UNIDAD = ("ZONA", "SUBZONA", "TRAMO", "MICROTRAMO")
ESTADOS_JORNADA = (
    "JORNADA_SEMANAL",
    "VENTANAS_ESPECIALES",
    "SIN_JORNADA",
    "CERRADO_POR_EXCEPCION",
    "INACTIVO",
)
JORNADAS_DISPONIBLES = "('JORNADA_SEMANAL', 'VENTANAS_ESPECIALES')"
CAPACIDAD_MAXIMA_GESTOR = 1_000


class EstadoVersionCampo(StrEnum):
    """Una version de la configuracion de campo nace PUBLICADA, en la transaccion que la crea con
    todo lo suyo: no hay versiones a medias."""

    PUBLICADA = "PUBLICADA"


class VersionConfiguracionCampo(SQLModel, table=True):
    """Una version de la configuracion de campo, sobre una version de bases declarada.

    Es configuracion operacional versionada, no una fuente de negocio ni datos de CARTERA o PAGOS.
    Se identifica por el SHA-256 de su configuracion en forma canonica, que incluye el hash de su
    version de bases, sus calendarios, sus ventanas, sus excepciones, sus gestores con sus cupos y
    su zona horaria: el mismo contenido es la misma version, que se reutiliza. Publicada, no cambia
    (un trigger lo impide): cambiar un cupo, un horario o un gestor es publicar otra version, y los
    planes viejos siguen con la suya.
    """

    __tablename__ = "version_configuracion_campo"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["version_bases_operativas_id"],
            ["version_bases_operativas.id"],
            name="fk_configuracion_campo_bases",
        ),
        sa.UniqueConstraint("hash_configuracion", name="uq_version_configuracion_campo_hash"),
        # Lo que referencia cada gestor y cada plan: la configuracion junto con su version de bases.
        sa.UniqueConstraint(
            "id", "version_bases_operativas_id", name="uq_configuracion_campo_id_bases"
        ),
        sa.CheckConstraint(
            "hash_configuracion ~ '^[0-9a-f]{64}$'", name=conv("ck_configuracion_campo_hash")
        ),
        sa.CheckConstraint(
            "calendarios > 0 AND ventanas >= 0 AND excepciones >= 0 AND gestores > 0 "
            "AND gestores_activos >= 0 AND gestores_activos <= gestores "
            "AND capacidad_declarada >= 0",
            name=conv("ck_configuracion_campo_conteos"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    configuracion_campo_version_id: UUID = Field(default_factory=uuid4, unique=True)
    """Identificador publico."""
    version_bases_operativas_id: int
    """La version de bases sobre la que esta construida: la de las bases de sus gestores. Un plan
    solo la usa con una asignacion de territorial/v2 de esa misma version."""
    nombre: str = Field(max_length=120)
    """Una etiqueta para personas. No entra en el hash."""
    version_configuracion: str = Field(max_length=32)
    """campo-configuracion/v1: la forma canonica con que se calculo el hash."""
    hash_configuracion: str = Field(max_length=64)
    zona_horaria: str = Field(max_length=64)
    """La zona IANA en que se leen las horas de sus calendarios y la fecha de operacion de un plan:
    explicita, nunca la del sistema operativo."""
    estado: EstadoVersionCampo = Field(
        default=EstadoVersionCampo.PUBLICADA,
        sa_type=sa.Enum(
            EstadoVersionCampo,
            name="estado_version_campo",
            native_enum=False,
            create_constraint=True,
            length=12,
        ),
    )
    calendarios: int
    ventanas: int
    """Cuantas ventanas declara, de la semana y de las excepciones."""
    excepciones: int
    gestores: int
    gestores_activos: int
    capacidad_declarada: int
    """La suma de los cupos de sus gestores activos: un tope, no la capacidad de una fecha."""
    metadatos: dict = Field(sa_type=JSONB)
    """Lo que dice de si misma, sin entrar en el hash: sus gestores, activos y cupos por base."""
    creada_en: datetime = Field(default_factory=ahora, sa_type=sa.DateTime(timezone=True))
    publicada_en: datetime = Field(default_factory=ahora, sa_type=sa.DateTime(timezone=True))
    """Nace publicada, en la transaccion que la crea: coincide con creada_en."""


class CalendarioCampo(SQLModel, table=True):
    """Un calendario de una configuracion: las ventanas de cada dia de la semana y sus excepciones,
    reutilizable por varios gestores."""

    __tablename__ = "calendario_campo"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["version_configuracion_campo_id"],
            ["version_configuracion_campo.id"],
            name="fk_calendario_campo_configuracion",
        ),
        sa.UniqueConstraint(
            "version_configuracion_campo_id", "codigo", name="uq_calendario_campo_codigo"
        ),
        # Lo que referencia cada gestor: el calendario junto con su configuracion.
        sa.UniqueConstraint(
            "id", "version_configuracion_campo_id", name="uq_calendario_campo_id_configuracion"
        ),
        sa.CheckConstraint(f"codigo ~ '{PATRON_CODIGO_BASE}'", name=conv("ck_calendario_codigo")),
    )

    id: int | None = Field(default=None, primary_key=True)
    calendario_id: UUID = Field(default_factory=uuid4, unique=True)
    version_configuracion_campo_id: int
    codigo: str = Field(max_length=32)


class TipoExcepcionCampo(StrEnum):
    CERRADO = "CERRADO"
    VENTANAS_ESPECIALES = "VENTANAS_ESPECIALES"


class ExcepcionCalendarioCampo(SQLModel, table=True):
    """Lo que un calendario dice de una fecha, en lugar de su semana: CERRADO (un festivo, una
    capacitacion), o VENTANAS_ESPECIALES (una media jornada), con sus ventanas."""

    __tablename__ = "excepcion_calendario_campo"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["calendario_campo_id"], ["calendario_campo.id"], name="fk_excepcion_campo_calendario"
        ),
        sa.UniqueConstraint("calendario_campo_id", "fecha", name="uq_excepcion_campo_fecha"),
        # Lo que referencia cada ventana especial: la excepcion junto con su calendario.
        sa.UniqueConstraint("id", "calendario_campo_id", name="uq_excepcion_campo_id_calendario"),
    )

    id: int | None = Field(default=None, primary_key=True)
    calendario_campo_id: int
    fecha: date
    tipo: TipoExcepcionCampo = Field(
        sa_type=sa.Enum(
            TipoExcepcionCampo,
            name="tipo_excepcion_campo",
            native_enum=False,
            create_constraint=True,
            length=20,
        )
    )
    motivo: str | None = Field(default=None, max_length=120)
    """Para personas: festivo, capacitacion, media jornada. No es un dato de nadie."""


class VentanaJornadaCampo(SQLModel, table=True):
    """Una ventana de disponibilidad: de un dia de la semana de un calendario, o de una excepcion
    VENTANAS_ESPECIALES. En hora local de la zona de su configuracion, como minutos desde la
    medianoche: [minuto_inicio, minuto_fin), dentro de 00:00 a 24:00. Dos ventanas del mismo dia no
    se enciman (un trigger de la 0013 lo garantiza)."""

    __tablename__ = "ventana_jornada_campo"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["calendario_campo_id"], ["calendario_campo.id"], name="fk_ventana_campo_calendario"
        ),
        sa.ForeignKeyConstraint(
            ["excepcion_calendario_campo_id", "calendario_campo_id"],
            ["excepcion_calendario_campo.id", "excepcion_calendario_campo.calendario_campo_id"],
            name="fk_ventana_campo_excepcion",
        ),
        sa.CheckConstraint(
            "(dia_semana IS NULL) <> (excepcion_calendario_campo_id IS NULL) "
            "AND (dia_semana IS NULL OR dia_semana BETWEEN 1 AND 7) "
            "AND minuto_inicio >= 0 AND minuto_inicio < minuto_fin AND minuto_fin <= 1440",
            name=conv("ck_ventana_campo_horario"),
        ),
        # Las ventanas de un calendario: la jornada de cada gestor, las ventanas de cada carga y
        # el trigger que no deja encimarlas las buscan asi.
        sa.Index("ix_ventana_campo_calendario", "calendario_campo_id", "dia_semana"),
    )

    id: int | None = Field(default=None, primary_key=True)
    calendario_campo_id: int
    excepcion_calendario_campo_id: int | None = None
    """La excepcion de la que es, o NULL si es de la semana."""
    dia_semana: int | None = Field(default=None, sa_type=sa.SmallInteger)
    """El dia ISO de la semana (lunes 1, domingo 7), o NULL si es de una excepcion."""
    minuto_inicio: int = Field(sa_type=sa.SmallInteger)
    minuto_fin: int = Field(sa_type=sa.SmallInteger)
    """1440 es las 24:00: el fin del dia."""


class GestorCampo(SQLModel, table=True):
    """Un gestor de campo de una configuracion: un recurso operacional, no una persona.

    Se identifica por su codigo (GESTOR_001); no guarda nombre, correo, telefono ni ningun dato
    personal, y no se relaciona con el `Gestor` de pagos/v1 ni con el `actor_ref` del lifecycle: que
    sean el mismo es una identidad que ninguna fuente afirma. Su base es de la version de bases de
    su configuracion, y su calendario, de su configuracion: lo garantizan sus llaves foraneas.
    """

    __tablename__ = "gestor_campo"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["version_configuracion_campo_id", "version_bases_operativas_id"],
            [
                "version_configuracion_campo.id",
                "version_configuracion_campo.version_bases_operativas_id",
            ],
            name="fk_gestor_campo_configuracion",
        ),
        sa.ForeignKeyConstraint(
            ["base_operativa_id", "version_bases_operativas_id"],
            ["base_operativa.id", "base_operativa.version_bases_operativas_id"],
            name="fk_gestor_campo_base",
        ),
        sa.ForeignKeyConstraint(
            ["calendario_campo_id", "version_configuracion_campo_id"],
            ["calendario_campo.id", "calendario_campo.version_configuracion_campo_id"],
            name="fk_gestor_campo_calendario",
        ),
        sa.UniqueConstraint(
            "version_configuracion_campo_id", "codigo", name="uq_gestor_campo_codigo"
        ),
        # Lo que referencia cada asignacion: el gestor junto con su configuracion y su base. Asi la
        # base garantiza que una cuenta solo queda con un gestor de su misma base.
        sa.UniqueConstraint(
            "id",
            "version_configuracion_campo_id",
            "base_operativa_id",
            name="uq_gestor_campo_id_configuracion_base",
        ),
        # Los gestores de una base en una configuracion, activos o no.
        sa.Index(
            "ix_gestor_campo_base", "version_configuracion_campo_id", "base_operativa_id", "activo"
        ),
        sa.CheckConstraint(f"codigo ~ '{PATRON_CODIGO_BASE}'", name=conv("ck_gestor_codigo")),
        sa.CheckConstraint(
            f"capacidad_cuentas_dia BETWEEN 1 AND {CAPACIDAD_MAXIMA_GESTOR}",
            name=conv("ck_gestor_capacidad"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    gestor_id: UUID = Field(default_factory=uuid4, unique=True)
    """Identificador publico."""
    version_configuracion_campo_id: int
    version_bases_operativas_id: int
    """La de su configuracion: lo exigen sus llaves foraneas compuestas."""
    codigo: str = Field(max_length=32)
    base_operativa_id: int
    calendario_campo_id: int
    capacidad_cuentas_dia: int
    """Su cupo operativo declarado, de 1 a 1,000 cuentas al dia. No sale de horas ni de distancias:
    se declara."""
    activo: bool


class EstadoCampoV2(StrEnum):
    """En que quedo un plan de campo/v2. Solo uno EXITOSA publica algo."""

    EN_PROCESO = "EN_PROCESO"
    EXITOSA = "EXITOSA"
    FALLIDA = "FALLIDA"


class ResultadoEjecucionCampoV2(StrEnum):
    """Como termino un plan de campo/v2."""

    PLAN_PUBLICADO = "PLAN_PUBLICADO"
    """EXITOSA: publico la asignacion de cada candidata de campo, las unidades y las cargas."""
    YA_GENERADO = "YA_GENERADO"
    """FALLIDA: otro plan publico primero la misma firma de entrada."""
    VERSION_NO_SOPORTADA = "VERSION_NO_SOPORTADA"
    ENTRADAS_NO_EXITOSAS = "ENTRADAS_NO_EXITOSAS"
    """FALLIDA: la decision o territorial/v2 que declara ya no esta EXITOSA."""
    DATOS_INCONSISTENTES = "DATOS_INCONSISTENTES"
    """FALLIDA: las candidatas no tienen su asignacion territorial, o los conteos no cuadran."""
    ERROR_INTERNO = "ERROR_INTERNO"
    INTENTOS_AGOTADOS = "INTENTOS_AGOTADOS"


class EjecucionCampoV2(SQLModel, table=True):
    """Un plan de campo/v2: las candidatas de campo de una decision/v2, en una fecha de operacion,
    con entradas declaradas.

    Declara exactamente su corte, su decision/v2 y su territorial/v2 (EXITOSA y del mismo corte), su
    configuracion de campo (sobre la misma version de bases que territorial/v2: lo garantizan sus
    llaves foraneas), la fecha de operacion y la politica con su hash. Nunca usa la decision, la
    asignacion o los gestores mas recientes: lo que se planeo se puede volver a planear igual.
    """

    __tablename__ = "ejecucion_campo_v2"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["ejecucion_decision_v2_id", "corte_canonico_id"],
            ["ejecucion_decision_v2.id", "ejecucion_decision_v2.corte_canonico_id"],
            name="fk_campo_v2_decision",
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_territorial_v2_id", "corte_canonico_id", "version_bases_operativas_id"],
            [
                "ejecucion_territorial_v2.id",
                "ejecucion_territorial_v2.corte_canonico_id",
                "ejecucion_territorial_v2.version_bases_operativas_id",
            ],
            name="fk_campo_v2_territorial",
        ),
        sa.ForeignKeyConstraint(
            ["version_configuracion_campo_id", "version_bases_operativas_id"],
            [
                "version_configuracion_campo.id",
                "version_configuracion_campo.version_bases_operativas_id",
            ],
            name="fk_campo_v2_configuracion",
        ),
        # Lo que referencia cada asignacion: el plan junto con sus entradas.
        sa.UniqueConstraint(
            "id",
            "ejecucion_decision_v2_id",
            "ejecucion_territorial_v2_id",
            "version_configuracion_campo_id",
            "version_bases_operativas_id",
            name="uq_campo_v2_entradas",
        ),
        sa.Index(
            "ux_campo_v2_exitosa",
            "firma_inputs",
            unique=True,
            postgresql_where=sa.text("estado = 'EXITOSA'"),
        ),
        sa.Index(
            "ux_campo_v2_en_proceso",
            "firma_inputs",
            unique=True,
            postgresql_where=sa.text("estado = 'EN_PROCESO'"),
        ),
        # Los planes de un corte, en cualquier estado, el mas reciente primero.
        sa.Index("ix_campo_v2_corte", "corte_canonico_id", "id"),
        sa.CheckConstraint(
            "firma_inputs ~ '^[0-9a-f]{64}$' AND hash_politica ~ '^[0-9a-f]{64}$'",
            name=conv("ck_campo_v2_firmas"),
        ),
        sa.CheckConstraint(
            "(estado = 'EN_PROCESO') = (resultado IS NULL)", name=conv("ck_campo_v2_resultado")
        ),
        sa.CheckConstraint(
            "cuentas_evaluadas_decision >= 0 AND candidatas_campo >= 0 AND rebalanceadas >= 0 "
            "AND capacidad_total >= 0 AND capacidad_utilizada >= 0 AND "
            + " AND ".join(f"{c} >= 0" for c in CONTEOS_DE_CAMPO_V2),
            name=conv("ck_campo_v2_conteos"),
        ),
        sa.CheckConstraint(PUBLICACION_DE_CAMPO_V2, name=conv("ck_campo_v2_publicacion")),
    )

    id: int | None = Field(default=None, primary_key=True)
    campo_run_id: UUID = Field(default_factory=uuid4, unique=True)
    """Identificador publico de este intento."""
    corte_canonico_id: int
    ejecucion_decision_v2_id: int
    ejecucion_territorial_v2_id: int
    version_bases_operativas_id: int
    """La de territorial/v2 y la de la configuracion: la misma, lo exigen sus llaves foraneas."""
    version_configuracion_campo_id: int
    fecha_operacion: date
    """El dia local, en la zona de la configuracion, para el que se planea el trabajo de campo."""
    version_motor: str = Field(max_length=32)
    """campo/v2."""
    version_politica: str = Field(max_length=40)
    """campo-public-v2."""
    hash_politica: str = Field(max_length=64)
    """SHA-256 de la definicion canonica de la politica, con su SRID, sus tamanos y su ranking."""
    firma_inputs: str = Field(max_length=64)
    estado: EstadoCampoV2 = Field(
        default=EstadoCampoV2.EN_PROCESO,
        sa_type=sa.Enum(
            EstadoCampoV2,
            name="estado_campo_v2",
            native_enum=False,
            create_constraint=True,
            length=12,
        ),
    )
    resultado: str | None = Field(default=None, max_length=40)
    cuentas_evaluadas_decision: int = Field(default=0, sa_type=sa.BigInteger)
    """Las cuentas que decidio su decision/v2: el universo del que salen las candidatas. Se fija al
    abrir."""
    candidatas_campo: int = Field(default=0, sa_type=sa.BigInteger)
    """Las de canal CAMPO."""
    asignadas: int = Field(default=0, sa_type=sa.BigInteger)
    revision_territorial: int = Field(default=0, sa_type=sa.BigInteger)
    sin_gestor: int = Field(default=0, sa_type=sa.BigInteger)
    """SIN_GESTOR_DISPONIBLE."""
    sin_capacidad: int = Field(default=0, sa_type=sa.BigInteger)
    rebalanceadas: int = Field(default=0, sa_type=sa.BigInteger)
    """Las asignadas que no quedaron con el propietario preferido de su microtramo, por
    capacidad."""
    capacidad_total: int = Field(default=0, sa_type=sa.BigInteger)
    """La suma de las capacidades efectivas de sus gestores en la fecha: el cupo de los
    disponibles."""
    capacidad_utilizada: int = Field(default=0, sa_type=sa.BigInteger)
    """Las asignadas: una cuenta es una unidad de capacidad."""
    distribucion: dict | None = Field(default=None, sa_type=JSONB)
    """Lo que publico, contado en la transaccion que lo publico: por estado, por base, por motivo,
    las unidades y el balance entre gestores. Sin coordenadas."""
    iniciada_en: datetime = Field(default_factory=ahora, sa_type=sa.DateTime(timezone=True))
    terminada_en: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    detalle: str | None = None


class AsignacionCampoCuenta(SQLModel, table=True):
    """Lo que un plan de campo/v2 concluyo de una candidata: su gestor, su particion, o por que no.

    Apunta a la decision de su cuenta y a su asignacion territorial por sus llaves naturales, y a su
    gestor junto con la configuracion y la base de su plan: la base garantiza que el gestor es de la
    configuracion declarada y de la misma base que la cuenta. No guarda coordenadas: la particion
    son cuatro identificadores. Ni se actualiza ni se borra.
    """

    __tablename__ = "asignacion_campo_cuenta"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            [
                "ejecucion_campo_v2_id",
                "ejecucion_decision_v2_id",
                "ejecucion_territorial_v2_id",
                "version_configuracion_campo_id",
                "version_bases_operativas_id",
            ],
            [
                "ejecucion_campo_v2.id",
                "ejecucion_campo_v2.ejecucion_decision_v2_id",
                "ejecucion_campo_v2.ejecucion_territorial_v2_id",
                "ejecucion_campo_v2.version_configuracion_campo_id",
                "ejecucion_campo_v2.version_bases_operativas_id",
            ],
            name="fk_asignacion_campo_ejecucion",
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_decision_v2_id", "cuenta_canonica_id"],
            [
                "resultado_decision_v2.ejecucion_decision_v2_id",
                "resultado_decision_v2.cuenta_canonica_id",
            ],
            name="fk_asignacion_campo_decision",
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_territorial_v2_id", "cuenta_canonica_id"],
            [
                "asignacion_territorial_v2.ejecucion_territorial_v2_id",
                "asignacion_territorial_v2.cuenta_canonica_id",
            ],
            name="fk_asignacion_campo_territorial",
        ),
        sa.ForeignKeyConstraint(
            ["gestor_campo_id", "version_configuracion_campo_id", "base_operativa_id"],
            [
                "gestor_campo.id",
                "gestor_campo.version_configuracion_campo_id",
                "gestor_campo.base_operativa_id",
            ],
            name="fk_asignacion_campo_gestor",
        ),
        sa.ForeignKeyConstraint(
            ["base_operativa_id", "version_bases_operativas_id"],
            ["base_operativa.id", "base_operativa.version_bases_operativas_id"],
            name="fk_asignacion_campo_base",
        ),
        # Una pagina de un estado, de una base, de un gestor, de una prioridad o de un microtramo,
        # en el orden de la cuenta; y las asignaciones de una cuenta en todos los planes.
        sa.Index(
            "ix_asignacion_campo_estado", "ejecucion_campo_v2_id", "estado", "cuenta_canonica_id"
        ),
        sa.Index(
            "ix_asignacion_campo_base",
            "ejecucion_campo_v2_id",
            "base_operativa_id",
            "cuenta_canonica_id",
        ),
        sa.Index(
            "ix_asignacion_campo_gestor",
            "ejecucion_campo_v2_id",
            "gestor_campo_id",
            "cuenta_canonica_id",
        ),
        sa.Index(
            "ix_asignacion_campo_prioridad",
            "ejecucion_campo_v2_id",
            "prioridad",
            "cuenta_canonica_id",
        ),
        sa.Index(
            "ix_asignacion_campo_microtramo",
            "ejecucion_campo_v2_id",
            "microtramo_id",
            "cuenta_canonica_id",
        ),
        sa.Index("ix_asignacion_campo_cuenta", "cuenta_canonica_id", "ejecucion_campo_v2_id"),
        sa.CheckConstraint(COHERENCIA_DEL_CAMPO, name=conv("ck_asignacion_campo_estado")),
    )

    zona_id: UUID | None = None
    subzona_id: UUID | None = None
    tramo_id: UUID | None = None
    microtramo_id: UUID | None = None
    """Las cuatro unidades de su particion: identificadores deterministas (UUIDv5), sin la
    coordenada. NULL en una REVISION_TERRITORIAL."""
    ejecucion_campo_v2_id: int = Field(primary_key=True)
    cuenta_canonica_id: int = Field(primary_key=True)
    ejecucion_decision_v2_id: int
    ejecucion_territorial_v2_id: int
    version_configuracion_campo_id: int
    version_bases_operativas_id: int
    """Los de su plan: lo exige su llave foranea compuesta."""
    base_operativa_id: int | None = None
    """La de territorial/v2. NULL solo en una REVISION_TERRITORIAL."""
    gestor_campo_id: int | None = None
    """Solo en una ASIGNADA: un gestor de la misma base."""
    orden_prioridad: int | None = None
    """Su lugar, desde 1, en la cola de capacidad de su base: prioridad, bloque y cuenta_id. No es
    un orden de visita: ese es de v0.13."""
    estado: str = Field(max_length=24)
    prioridad: str = Field(max_length=12)
    """La prioridad final de decision/v2, tal como se decidio."""
    es_rebalanceada: bool = False
    """Asignada a otro gestor que el propietario preferido de su microtramo, por capacidad."""
    motivos: list[dict] = Field(sa_type=JSONB)
    """Por que salio asi, con codigos estables. Sin coordenadas."""


class UnidadTerritorialCampo(SQLModel, table=True):
    """Una unidad de la particion que ocupo un plan: una zona, una subzona, un tramo o un
    microtramo con candidatas. No se materializan las celdas vacias, y no se guarda ninguna
    geometria, celda ni coordenada: el identificador, su padre, su base y su municipio."""

    __tablename__ = "unidad_territorial_campo"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["ejecucion_campo_v2_id"], ["ejecucion_campo_v2.id"], name="fk_unidad_campo_ejecucion"
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_campo_v2_id", "padre_id"],
            [
                "unidad_territorial_campo.ejecucion_campo_v2_id",
                "unidad_territorial_campo.unidad_id",
            ],
            name="fk_unidad_campo_padre",
        ),
        sa.ForeignKeyConstraint(
            ["base_operativa_id"], ["base_operativa.id"], name="fk_unidad_campo_base"
        ),
        sa.ForeignKeyConstraint(
            ["propietario_gestor_campo_id"], ["gestor_campo.id"], name="fk_unidad_campo_propietario"
        ),
        # Las unidades hijas de una unidad.
        sa.Index("ix_unidad_campo_padre", "ejecucion_campo_v2_id", "padre_id"),
        sa.CheckConstraint(
            "nivel IN ("
            + ", ".join(f"'{n}'" for n in NIVELES_DE_UNIDAD)
            + ") AND (nivel = 'ZONA') = (padre_id IS NULL) "
            "AND (nivel = 'ZONA') = (cvegeo IS NULL) "
            "AND cve_ent ~ '^[0-9]{2}$' "
            f"AND (cvegeo IS NULL OR (cvegeo ~ '{PATRON_CVEGEO}' AND left(cvegeo, 2) = cve_ent)) "
            "AND (propietario_gestor_campo_id IS NULL OR nivel = 'MICROTRAMO') "
            "AND candidatas > 0 AND asignadas >= 0 AND asignadas <= candidatas",
            name=conv("ck_unidad_campo_nivel"),
        ),
    )

    ejecucion_campo_v2_id: int = Field(primary_key=True)
    unidad_id: UUID = Field(primary_key=True)
    """El mismo identificador que llevan las asignaciones: UUIDv5 de la politica, el nivel, la base
    y su clave."""
    nivel: str = Field(max_length=12)
    padre_id: UUID | None = None
    """La unidad que la contiene: la zona de una subzona, la subzona de un tramo, el tramo de un
    microtramo. NULL en una zona."""
    base_operativa_id: int
    cve_ent: str = Field(max_length=2)
    cvegeo: str | None = Field(default=None, max_length=5)
    """El municipio de una subzona, un tramo o un microtramo. NULL en una zona."""
    candidatas: int
    """Las candidatas de su base que caen en ella, en cualquier estado salvo
    REVISION_TERRITORIAL."""
    asignadas: int
    propietario_gestor_campo_id: int | None = None
    """Solo en un microtramo: el propietario preferido de su ranking, si la base tenia gestores
    disponibles."""


class CargaGestorCampo(SQLModel, table=True):
    """La carga de un gestor de la configuracion en un plan: su jornada en la fecha, su capacidad
    efectiva, cuantas cuentas recibio, de cuantos microtramos y tramos, cuantas por spillover, y las
    ventanas de su dia, para el ruteo de v0.13. Una fila por cada gestor de la configuracion."""

    __tablename__ = "carga_gestor_campo"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["ejecucion_campo_v2_id"], ["ejecucion_campo_v2.id"], name="fk_carga_campo_ejecucion"
        ),
        sa.ForeignKeyConstraint(
            ["gestor_campo_id"], ["gestor_campo.id"], name="fk_carga_campo_gestor"
        ),
        sa.ForeignKeyConstraint(
            ["base_operativa_id"], ["base_operativa.id"], name="fk_carga_campo_base"
        ),
        # Desde la 0014, lo que referencia cada ruta de ruteo/v2: la carga junto con su base.
        sa.UniqueConstraint(
            "ejecucion_campo_v2_id",
            "gestor_campo_id",
            "base_operativa_id",
            name="uq_carga_campo_base",
        ),
        sa.CheckConstraint(
            "estado_jornada IN ("
            + ", ".join(f"'{e}'" for e in ESTADOS_JORNADA)
            + f") AND (capacidad > 0) = (estado_jornada IN {JORNADAS_DISPONIBLES}) "
            "AND capacidad >= 0 AND asignadas >= 0 AND asignadas <= capacidad "
            "AND rebalanceadas >= 0 AND rebalanceadas <= asignadas "
            "AND microtramos >= 0 AND tramos >= 0 AND tramos <= microtramos "
            "AND (asignadas = 0) = (microtramos = 0) "
            "AND (utilizacion IS NULL) = (capacidad = 0)",
            name=conv("ck_carga_campo_conteos"),
        ),
    )

    utilizacion: Decimal | None = Field(default=None, max_digits=5, decimal_places=4)
    """asignadas / capacidad, de 0 a 1. NULL si no tenia capacidad ese dia."""
    ejecucion_campo_v2_id: int = Field(primary_key=True)
    gestor_campo_id: int = Field(primary_key=True)
    base_operativa_id: int
    estado_jornada: str = Field(max_length=24)
    capacidad: int
    """Su capacidad efectiva en la fecha: su cupo si esta disponible, 0 si no."""
    asignadas: int
    rebalanceadas: int
    microtramos: int
    tramos: int
    ventanas: list[dict] = Field(sa_type=JSONB)
    """Las ventanas de su jornada en la fecha: la hora local de inicio y de fin y los instantes que
    les corresponden en la zona de la configuracion. Vacia si no esta disponible."""


# --- ruteo/v2: la red vial, las ejecuciones, las rutas y las paradas ------------------------------

ESTADOS_VERSION_RED_VIAL = ("EN_PROCESO", "PUBLICADA", "FALLIDA")
MOTORES_RUTEO = ("OSRM", "SINTETICO")
ESTADOS_PARADA_RUTEO = ("PROGRAMADA", "SIN_TIEMPO", "NO_ENRUTABLE", "NO_ENRUTABLE_BASE")
MOTIVOS_SIN_TIEMPO = (
    "JORNADA_AGOTADA",
    "PRIORIDAD_SUPERIOR_AGOTO_JORNADA",
    "EXCEDE_JORNADA",
    "SIN_JORNADA",
)
MOTIVOS_NO_ENRUTABLE = ("SIN_SNAP", "SNAP_EXCESIVO", "RED_DESCONECTADA")
ESTADOS_RUTA = ("COMPLETA", "PARCIAL", "SIN_PARADAS_ENRUTABLES", "BASE_NO_ENRUTABLE", "SIN_JORNADA")
CONTEOS_DE_PARADAS = ("programadas", "sin_tiempo", "no_enrutables", "no_enrutables_base")
"""Las paradas de una ruta y de una ejecucion, por estado: una columna por estado."""
CONTEOS_DE_RUTAS = (
    "rutas_completas",
    "rutas_parciales",
    "rutas_sin_paradas",
    "bases_no_enrutables",
    "rutas_sin_jornada",
)
"""Las rutas de una ejecucion, por estado: una columna por estado."""


def _en(valores: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in valores) + ")"


_INSTANTES_DE_LA_PARADA = (
    "salida_anterior_at",
    "llegada_estimada",
    "inicio_servicio",
    "fin_servicio",
    "distancia_desde_anterior_m",
    "duracion_desde_anterior_s",
    "espera_s",
    "orden_visita",
)
COHERENCIA_DE_LA_PARADA = (
    f"estado IN {_en(ESTADOS_PARADA_RUTEO)} AND prioridad IN {_en(PRIORIDADES)} "
    "AND (motivo IS NULL) = (estado = 'PROGRAMADA') "
    f"AND (estado <> 'SIN_TIEMPO' OR motivo IN {_en(MOTIVOS_SIN_TIEMPO)}) "
    f"AND (estado <> 'NO_ENRUTABLE' OR motivo IN {_en(MOTIVOS_NO_ENRUTABLE)}) "
    "AND (estado <> 'NO_ENRUTABLE_BASE' OR motivo = 'BASE_NO_ENRUTABLE') "
    + " ".join(f"AND ({c} IS NOT NULL) = (estado = 'PROGRAMADA')" for c in _INSTANTES_DE_LA_PARADA)
    + " AND (orden_visita IS NULL OR orden_visita >= 1) "
    "AND (llegada_estimada IS NULL OR (salida_anterior_at <= llegada_estimada "
    "AND llegada_estimada <= inicio_servicio AND inicio_servicio <= fin_servicio)) "
    "AND (distancia_desde_anterior_m IS NULL OR distancia_desde_anterior_m >= 0) "
    "AND (duracion_desde_anterior_s IS NULL OR duracion_desde_anterior_s >= 0) "
    "AND (espera_s IS NULL OR espera_s >= 0) "
    "AND (snap_distancia_m IS NULL OR snap_distancia_m >= 0) "
    "AND (motivo IS DISTINCT FROM 'SIN_SNAP' OR snap_distancia_m IS NULL) "
    "AND (motivo IS DISTINCT FROM 'SNAP_EXCESIVO' OR snap_distancia_m IS NOT NULL) "
    "AND (estado <> 'PROGRAMADA' OR snap_distancia_m IS NOT NULL)"
)
"""Solo una PROGRAMADA tiene orden, instantes y tramo desde la anterior, y no tiene motivo; las
demas tienen uno de los de su estado. Una PROGRAMADA tiene snap; una SIN_SNAP, ninguno."""

_SUMA_DE_PARADAS = " + ".join(CONTEOS_DE_PARADAS)
COHERENCIA_DE_LA_RUTA = (
    f"estado_ruta IN {_en(ESTADOS_RUTA)} "
    f"AND paradas_campo >= 1 AND paradas_campo = {_SUMA_DE_PARADAS} "
    + " ".join(f"AND {c} >= 0" for c in CONTEOS_DE_PARADAS)
    + " AND (estado_ruta <> 'COMPLETA' OR programadas = paradas_campo) "
    "AND (estado_ruta <> 'PARCIAL' OR (programadas < paradas_campo AND no_enrutables < "
    "paradas_campo AND no_enrutables_base = 0)) "
    "AND (estado_ruta <> 'SIN_PARADAS_ENRUTABLES' OR no_enrutables = paradas_campo) "
    "AND (estado_ruta <> 'BASE_NO_ENRUTABLE' OR no_enrutables_base = paradas_campo) "
    "AND (estado_ruta <> 'SIN_JORNADA' OR sin_tiempo = paradas_campo) "
    "AND (motivo_base IS NOT NULL) = (estado_ruta = 'BASE_NO_ENRUTABLE') "
    "AND (motivo_base IS NULL OR motivo_base IN ('SIN_SNAP', 'SNAP_EXCESIVO')) "
    "AND (programadas > 0) = (salida_base_at IS NOT NULL) "
    "AND (salida_base_at IS NULL) = (regreso_base_at IS NULL) "
    "AND (salida_base_at IS NULL) = (salida_regreso_at IS NULL) "
    "AND (salida_base_at IS NULL) = (distancia_regreso_m IS NULL) "
    "AND (salida_base_at IS NULL) = (duracion_regreso_s IS NULL) "
    "AND (salida_base_at IS NULL) = (espera_regreso_s IS NULL) "
    "AND (jornada_inicio_at IS NULL) = (estado_ruta = 'SIN_JORNADA') "
    "AND (jornada_inicio_at IS NULL) = (jornada_fin_at IS NULL) "
    "AND distancia_total_m >= 0 AND duracion_traslado_s >= 0 AND duracion_servicio_s >= 0 "
    "AND duracion_espera_s >= 0 AND mejoras_2opt >= 0 "
    "AND duracion_total_s = duracion_traslado_s + duracion_servicio_s + duracion_espera_s "
    "AND (salida_base_at IS NULL OR (jornada_inicio_at <= salida_base_at "
    "AND salida_base_at <= salida_regreso_at AND salida_regreso_at <= regreso_base_at "
    "AND regreso_base_at <= jornada_fin_at "
    "AND regreso_base_at = salida_base_at + make_interval(secs => duracion_total_s))) "
    "AND (salida_base_at IS NOT NULL OR (distancia_total_m = 0 AND duracion_total_s = 0)) "
    "AND (base_snap_distancia_m IS NULL OR base_snap_distancia_m >= 0)"
)
"""Cada parada de la ruta en exactamente un estado, cada estado de la ruta con lo que lo define, y
la jornada que cuadra: salida, regreso y total, con el regreso a la base dentro de la jornada."""

_SUMA_DE_RUTAS = " + ".join(CONTEOS_DE_RUTAS)
PUBLICACION_DE_RUTEO_V2 = (
    f"(estado = 'EXITOSA' AND {_SUMA_DE_RUTAS} = gestores_objetivo "
    f"AND {_SUMA_DE_PARADAS} = paradas_objetivo AND paradas_objetivo >= gestores_objetivo "
    "AND (paradas_objetivo = 0) = (gestores_objetivo = 0)) "
    f"OR (estado <> 'EXITOSA' AND gestores_objetivo = 0 AND {_SUMA_DE_RUTAS} = 0 "
    f"AND paradas_objetivo = 0 AND {_SUMA_DE_PARADAS} = 0 AND distancia_total_m = 0 "
    "AND duracion_vial_total_s = 0 AND duracion_servicio_total_s = 0 "
    "AND duracion_espera_total_s = 0)"
)
"""Una ejecucion EXITOSA puso cada ruta y cada parada en exactamente un estado, y cada parada es
de la ruta de un gestor (sin gestores, sin paradas); las demas no publicaron nada."""


class EstadoVersionRedVial(StrEnum):
    """Una version de la red vial nace EN_PROCESO al empezar su preparacion, y queda PUBLICADA (con
    su manifiesto verificado) o FALLIDA. Publicada, ya no cambia."""

    EN_PROCESO = "EN_PROCESO"
    PUBLICADA = "PUBLICADA"
    FALLIDA = "FALLIDA"


class VersionRedVial(SQLModel, table=True):
    """Una version de la red vial: de que datos publicos salio (OpenStreetMap, ODbL), con que motor
    de ruteo, perfil y parametros se preparo, y su huella.

    Es referencia vial publica, no una fuente de negocio: no es CARTERA ni PAGOS. La huella es el
    SHA-256 de su identidad (la fuente por su SHA-256, el motor y su distribucion exacta, el
    algoritmo, el perfil con su hash y los comandos), y el backend que la sirve la declara en cada
    respuesta: el worker solo rutea contra la red que pidio la ejecucion. Publicada, no cambia (un
    trigger lo impide); otra red es otra version.
    """

    __tablename__ = "version_red_vial"
    __table_args__ = (
        sa.Index(
            "ux_red_vial_publicada",
            "huella",
            unique=True,
            postgresql_where=sa.text("estado = 'PUBLICADA'"),
        ),
        sa.Index(
            "ux_red_vial_en_proceso",
            "huella",
            unique=True,
            postgresql_where=sa.text("estado = 'EN_PROCESO'"),
        ),
        sa.CheckConstraint(
            "huella ~ '^[0-9a-f]{64}$' AND sha256_fuente ~ '^[0-9a-f]{64}$' "
            "AND hash_perfil ~ '^[0-9a-f]{64}$' "
            "AND (hash_manifest IS NULL OR hash_manifest ~ '^[0-9a-f]{64}$')",
            name=conv("ck_red_vial_hashes"),
        ),
        sa.CheckConstraint(
            f"motor_ruteo IN {_en(MOTORES_RUTEO)} "
            "AND (estado = 'PUBLICADA') = (hash_manifest IS NOT NULL) "
            "AND (estado = 'PUBLICADA') = (manifiesto IS NOT NULL) "
            "AND (estado = 'PUBLICADA') = (publicada_en IS NOT NULL) "
            "AND (estado = 'FALLIDA') = (detalle IS NOT NULL)",
            name=conv("ck_red_vial_publicacion"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    red_vial_version_id: UUID = Field(default_factory=uuid4, unique=True)
    """Identificador publico."""
    huella: str = Field(max_length=64)
    """SHA-256 de su identidad: la misma red logica, la misma huella. El backend la declara."""
    proveedor_datos: str = Field(max_length=40)
    """De quien son los datos: OpenStreetMap, o SINTETICO."""
    proveedor_extracto: str | None = Field(default=None, max_length=120)
    """Quien recorto el extracto (OpenStreetMap France, Geofabrik...), si aplica."""
    licencia: str = Field(max_length=40)
    """ODbL-1.0 para OpenStreetMap."""
    fecha_snapshot: date | None = None
    """La fecha de los datos del extracto, como la declara su proveedor."""
    region: str = Field(max_length=120)
    """Lo que cubre, como se declara: una region, nunca un recuadro de coordenadas."""
    archivo_fuente: str = Field(max_length=255)
    url_fuente: str | None = Field(default=None, max_length=500)
    sha256_fuente: str = Field(max_length=64)
    motor_ruteo: str = Field(max_length=16)
    """OSRM, o SINTETICO para un modelo de pruebas que no es una red vial."""
    version_motor_ruteo: str = Field(max_length=32)
    distribucion_motor: str = Field(max_length=300)
    """La imagen de Docker por su digest, o los binarios nativos por su SHA-256."""
    algoritmo_motor: str = Field(max_length=16)
    """MLD."""
    perfil: str = Field(max_length=32)
    hash_perfil: str = Field(max_length=64)
    identidad: dict = Field(sa_type=JSONB)
    """Lo que entra en la huella."""
    hash_manifest: str | None = Field(default=None, max_length=64)
    """SHA-256 del manifiesto con que se publico: la identidad, los comandos, el SHA-256 de cada
    archivo preparado y la cobertura."""
    manifiesto: dict | None = Field(default=None, sa_type=JSONB(none_as_null=True))
    """NULL de SQL (no el JSON null) mientras no se publica: la restriccion lo exige."""
    estado: EstadoVersionRedVial = Field(
        default=EstadoVersionRedVial.EN_PROCESO,
        sa_type=sa.Enum(
            EstadoVersionRedVial,
            name="estado_version_red_vial",
            native_enum=False,
            create_constraint=True,
            length=12,
        ),
    )
    metadatos: dict = Field(sa_type=JSONB)
    """Lo que dice de si misma: su cobertura (nodos, vias, aristas) y el tamano de sus archivos. Sin
    coordenadas ni recuadros."""
    creada_en: datetime = Field(default_factory=ahora, sa_type=sa.DateTime(timezone=True))
    publicada_en: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    detalle: str | None = None


class EstadoRuteoV2(StrEnum):
    """En que quedo una ejecucion de ruteo/v2. Solo una EXITOSA publica algo."""

    EN_PROCESO = "EN_PROCESO"
    EXITOSA = "EXITOSA"
    FALLIDA = "FALLIDA"


class ResultadoEjecucionRuteoV2(StrEnum):
    """Como termino una ejecucion de ruteo/v2."""

    RUTAS_PUBLICADAS = "RUTAS_PUBLICADAS"
    """EXITOSA: publico la ruta de cada gestor y cada parada, con sus resultados negativos."""
    YA_GENERADO = "YA_GENERADO"
    """FALLIDA: otra ejecucion publico primero la misma firma de entrada."""
    VERSION_NO_SOPORTADA = "VERSION_NO_SOPORTADA"
    ENTRADAS_NO_EXITOSAS = "ENTRADAS_NO_EXITOSAS"
    """FALLIDA: el plan de campo/v2 ya no esta EXITOSA, o la red ya no esta PUBLICADA."""
    RED_VIAL_BACKEND_INCOMPATIBLE = "RED_VIAL_BACKEND_INCOMPATIBLE"
    """FALLIDA: el backend de ruteo sirve otra red que la de la version pedida. Falla cerrado."""
    DATOS_INCONSISTENTES = "DATOS_INCONSISTENTES"
    """FALLIDA: una asignacion sin punto, una carga sin su base, o conteos que no cuadran."""
    ERROR_INTERNO = "ERROR_INTERNO"
    INTENTOS_AGOTADOS = "INTENTOS_AGOTADOS"
    """FALLIDA: la cola agoto sus intentos; por ejemplo, porque el proveedor de ruteo no respondio
    en ninguno."""


class EjecucionRuteoV2(SQLModel, table=True):
    """Una ejecucion de ruteo/v2: las rutas de los gestores de un plan de campo/v2, sobre una
    version declarada de la red vial.

    Declara exactamente su plan de campo/v2 (EXITOSA) y su red vial (PUBLICADA), con la politica y
    su hash. Nunca toma "el ultimo plan" ni "la ultima red".
    """

    __tablename__ = "ejecucion_ruteo_v2"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["ejecucion_campo_v2_id"], ["ejecucion_campo_v2.id"], name="fk_ruteo_v2_campo"
        ),
        sa.ForeignKeyConstraint(
            ["version_red_vial_id"], ["version_red_vial.id"], name="fk_ruteo_v2_red"
        ),
        # Lo que referencia cada ruta: la ejecucion junto con su plan de campo.
        sa.UniqueConstraint("id", "ejecucion_campo_v2_id", name="uq_ruteo_v2_campo"),
        sa.Index(
            "ux_ruteo_v2_exitosa",
            "firma_inputs",
            unique=True,
            postgresql_where=sa.text("estado = 'EXITOSA'"),
        ),
        sa.Index(
            "ux_ruteo_v2_en_proceso",
            "firma_inputs",
            unique=True,
            postgresql_where=sa.text("estado = 'EN_PROCESO'"),
        ),
        # Las ejecuciones de un plan de campo, en cualquier estado, la mas reciente primero.
        sa.Index("ix_ruteo_v2_campo", "ejecucion_campo_v2_id", "id"),
        sa.CheckConstraint(
            "firma_inputs ~ '^[0-9a-f]{64}$' AND hash_politica ~ '^[0-9a-f]{64}$'",
            name=conv("ck_ruteo_v2_firmas"),
        ),
        sa.CheckConstraint(
            "(estado = 'EN_PROCESO') = (resultado IS NULL)", name=conv("ck_ruteo_v2_resultado")
        ),
        sa.CheckConstraint(
            "gestores_objetivo >= 0 AND paradas_objetivo >= 0 AND distancia_total_m >= 0 "
            "AND duracion_vial_total_s >= 0 AND duracion_servicio_total_s >= 0 "
            "AND duracion_espera_total_s >= 0 AND "
            + " AND ".join(f"{c} >= 0" for c in (*CONTEOS_DE_RUTAS, *CONTEOS_DE_PARADAS)),
            name=conv("ck_ruteo_v2_conteos"),
        ),
        sa.CheckConstraint(PUBLICACION_DE_RUTEO_V2, name=conv("ck_ruteo_v2_publicacion")),
    )

    id: int | None = Field(default=None, primary_key=True)
    ruteo_run_id: UUID = Field(default_factory=uuid4, unique=True)
    """Identificador publico de este intento."""
    ejecucion_campo_v2_id: int
    version_red_vial_id: int
    fecha_operacion: date
    """La de su plan de campo/v2: el dia que se rutea."""
    version_motor: str = Field(max_length=32)
    """ruteo/v2."""
    version_politica: str = Field(max_length=40)
    """ruteo-public-v2."""
    hash_politica: str = Field(max_length=64)
    firma_inputs: str = Field(max_length=64)
    estado: EstadoRuteoV2 = Field(
        default=EstadoRuteoV2.EN_PROCESO,
        sa_type=sa.Enum(
            EstadoRuteoV2,
            name="estado_ruteo_v2",
            native_enum=False,
            create_constraint=True,
            length=12,
        ),
    )
    resultado: str | None = Field(default=None, max_length=40)
    gestores_objetivo: int = 0
    """Los gestores con al menos una cuenta ASIGNADA en el plan: una ruta cada uno."""
    rutas_completas: int = 0
    rutas_parciales: int = 0
    rutas_sin_paradas: int = 0
    """SIN_PARADAS_ENRUTABLES."""
    bases_no_enrutables: int = 0
    """Las rutas BASE_NO_ENRUTABLE."""
    rutas_sin_jornada: int = 0
    paradas_objetivo: int = Field(default=0, sa_type=sa.BigInteger)
    """Las cuentas ASIGNADA del plan: una parada cada una."""
    programadas: int = Field(default=0, sa_type=sa.BigInteger)
    sin_tiempo: int = Field(default=0, sa_type=sa.BigInteger)
    no_enrutables: int = Field(default=0, sa_type=sa.BigInteger)
    no_enrutables_base: int = Field(default=0, sa_type=sa.BigInteger)
    distancia_total_m: int = Field(default=0, sa_type=sa.BigInteger)
    duracion_vial_total_s: int = Field(default=0, sa_type=sa.BigInteger)
    duracion_servicio_total_s: int = Field(default=0, sa_type=sa.BigInteger)
    duracion_espera_total_s: int = Field(default=0, sa_type=sa.BigInteger)
    distribucion: dict | None = Field(default=None, sa_type=JSONB)
    """Lo que publico, contado en la transaccion que lo publico: por estado, por motivo, por base y
    por prioridad, y las matrices (cuantas, de que tamano, en cuantos bloques). Sin coordenadas."""
    iniciada_en: datetime = Field(default_factory=ahora, sa_type=sa.DateTime(timezone=True))
    terminada_en: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    detalle: str | None = None


class RutaGestorV2(SQLModel, table=True):
    """La ruta de un gestor en una ejecucion: sale de su base, visita sus paradas PROGRAMADA en
    orden y regresa a la base, dentro de su jornada; o por que no hay ruta.

    Apunta a la carga del gestor en el plan de campo/v2 de su ejecucion, con su base: la base
    garantiza que la ruta es de un gestor de ese plan y sale de la base que campo/v2 le dio. No
    guarda geometria, polilinea ni coordenadas: el orden, los instantes, las distancias y las
    duraciones. Ni se actualiza ni se borra.
    """

    __tablename__ = "ruta_gestor_v2"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["ejecucion_ruteo_v2_id", "ejecucion_campo_v2_id"],
            ["ejecucion_ruteo_v2.id", "ejecucion_ruteo_v2.ejecucion_campo_v2_id"],
            name="fk_ruta_v2_ejecucion",
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_campo_v2_id", "gestor_campo_id", "base_operativa_id"],
            [
                "carga_gestor_campo.ejecucion_campo_v2_id",
                "carga_gestor_campo.gestor_campo_id",
                "carga_gestor_campo.base_operativa_id",
            ],
            name="fk_ruta_v2_carga",
        ),
        # Lo que referencia cada parada: la ruta junto con el plan de campo de su ejecucion.
        sa.UniqueConstraint(
            "ejecucion_ruteo_v2_id",
            "gestor_campo_id",
            "ejecucion_campo_v2_id",
            name="uq_ruta_v2_campo",
        ),
        sa.CheckConstraint(COHERENCIA_DE_LA_RUTA, name=conv("ck_ruta_v2_coherencia")),
        sa.CheckConstraint(
            "(hash_matriz IS NULL OR hash_matriz ~ '^[0-9a-f]{64}$') "
            "AND hash_secuencia ~ '^[0-9a-f]{64}$'",
            name=conv("ck_ruta_v2_hashes"),
        ),
    )

    ejecucion_ruteo_v2_id: int = Field(primary_key=True)
    gestor_campo_id: int = Field(primary_key=True)
    ruta_id: UUID = Field(unique=True)
    """Identificador publico: UUIDv5 de la firma de la ejecucion y del gestor."""
    ejecucion_campo_v2_id: int
    base_operativa_id: int
    """La de campo/v2: la ruta sale de ella y regresa a ella."""
    fecha_operacion: date
    estado_ruta: str = Field(max_length=24)
    motivo_base: str | None = Field(default=None, max_length=24)
    """Solo en BASE_NO_ENRUTABLE: SIN_SNAP o SNAP_EXCESIVO."""
    paradas_campo: int
    programadas: int
    sin_tiempo: int
    no_enrutables: int
    no_enrutables_base: int
    base_snap_distancia_m: Decimal | None = Field(default=None, max_digits=12, decimal_places=2)
    """De la base a su segmento de la red, al centimetro."""
    jornada_inicio_at: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    jornada_fin_at: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    salida_base_at: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    salida_regreso_at: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    """Cuando sale de la ultima parada hacia la base."""
    regreso_base_at: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    distancia_regreso_m: int | None = Field(default=None, sa_type=sa.BigInteger)
    duracion_regreso_s: int | None = None
    espera_regreso_s: int | None = None
    distancia_total_m: int = Field(sa_type=sa.BigInteger)
    """Por calle, de la base a la base, con el regreso."""
    duracion_traslado_s: int
    duracion_servicio_s: int
    duracion_espera_s: int
    duracion_total_s: int
    """Del momento en que sale de la base al momento en que regresa: traslado, servicio y espera."""
    mejoras_2opt: int
    hash_matriz: str | None = Field(default=None, max_length=64)
    """SHA-256 de la matriz canonica de la ruta (duraciones y distancias enteras, sin
    coordenadas). La matriz no se guarda."""
    hash_secuencia: str = Field(max_length=64)
    creada_en: datetime = Field(default_factory=ahora, sa_type=sa.DateTime(timezone=True))


class ParadaRutaV2(SQLModel, table=True):
    """Una parada de una ruta: una cuenta ASIGNADA del plan de campo/v2, con su estado, su motivo y,
    si quedo PROGRAMADA, su orden y sus instantes planificados (ETA estaticas, sin trafico).

    Apunta a la asignacion de su cuenta en el plan de campo por su llave natural, y a su ruta junto
    con ese plan. No guarda coordenadas, ni la del punto ni la de su snap: la distancia del snap, al
    centimetro. Ni se actualiza ni se borra.
    """

    __tablename__ = "parada_ruta_v2"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["ejecucion_ruteo_v2_id", "gestor_campo_id", "ejecucion_campo_v2_id"],
            [
                "ruta_gestor_v2.ejecucion_ruteo_v2_id",
                "ruta_gestor_v2.gestor_campo_id",
                "ruta_gestor_v2.ejecucion_campo_v2_id",
            ],
            name="fk_parada_v2_ruta",
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_campo_v2_id", "cuenta_canonica_id"],
            [
                "asignacion_campo_cuenta.ejecucion_campo_v2_id",
                "asignacion_campo_cuenta.cuenta_canonica_id",
            ],
            name="fk_parada_v2_asignacion",
        ),
        # Las paradas de una ruta en su orden de visita: una por lugar.
        sa.UniqueConstraint(
            "ejecucion_ruteo_v2_id", "gestor_campo_id", "orden_visita", name="uq_parada_v2_orden"
        ),
        # Una pagina de un estado de la ejecucion, en el orden de la cuenta; y las paradas de una
        # cuenta en todas las ejecuciones.
        sa.Index("ix_parada_v2_estado", "ejecucion_ruteo_v2_id", "estado", "cuenta_canonica_id"),
        sa.Index("ix_parada_v2_cuenta", "cuenta_canonica_id", "ejecucion_ruteo_v2_id"),
        sa.CheckConstraint(COHERENCIA_DE_LA_PARADA, name=conv("ck_parada_v2_coherencia")),
    )

    snap_distancia_m: Decimal | None = Field(default=None, max_digits=12, decimal_places=2)
    """Del punto de la cuenta a su segmento de la red, al centimetro. NULL sin snap."""
    salida_anterior_at: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    """Cuando sale de la base o de la parada anterior hacia esta."""
    llegada_estimada: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    inicio_servicio: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    fin_servicio: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    distancia_desde_anterior_m: int | None = Field(default=None, sa_type=sa.BigInteger)
    ejecucion_ruteo_v2_id: int = Field(primary_key=True)
    cuenta_canonica_id: int = Field(primary_key=True)
    gestor_campo_id: int
    ejecucion_campo_v2_id: int
    """El de su ejecucion: lo exigen sus dos llaves foraneas compuestas."""
    orden_visita: int | None = None
    duracion_desde_anterior_s: int | None = None
    espera_s: int | None = None
    """Lo que espero, sin el traslado, desde que termino lo anterior hasta empezar su servicio: la
    pausa entre ventanas, si la hubo."""
    prioridad: str = Field(max_length=12)
    """La de campo/v2, la de decision/v2, tal cual."""
    estado: str = Field(max_length=20)
    motivo: str | None = Field(default=None, max_length=40)


# --- analytics/v1 ---------------------------------------------------------------------------------
#
# Desde la 0015. Collection Analytics describe una cohorte (las cuentas de un corte canonico) desde
# fecha_corte hasta as_of, con lo que se conocia en known_at, sobre un manifiesto explicito de
# entradas (EntradaAnalytics): ninguna "ultima" interpretacion, atribucion, evaluacion, decision ni
# plan. Publica todo o nada sus agregados (AgregadoAnalytics: una metrica de un periodo en una
# celda de un conjunto de dimensiones) y sus controles de reconciliacion (ControlAnalytics). No hay
# ninguna fila por cuenta: lo que crece es periodos x celdas x metricas. Lo publicado no se
# reescribe: los triggers de la 0015 rechazan cualquier UPDATE o DELETE sobre las entradas, los
# agregados y los controles, y sobre una ejecucion terminada.

TIPOS_ENTRADA_ANALYTICS = (
    "CORTE",
    "MOTOR_PAGOS",
    "ATRIBUCION",
    "EVALUACION_PROMESAS",
    "DECISION_V2",
    "GEOGRAFIA",
    "TERRITORIAL_V2",
    "CAMPO_V2",
    "RUTEO_V2",
)
"""Lo que puede declarar un manifiesto. Solo CORTE es obligatoria."""

COLUMNA_DE_LA_ENTRADA = {
    "CORTE": "corte_canonico_id",
    "MOTOR_PAGOS": "ejecucion_motor_pagos_id",
    "ATRIBUCION": "ejecucion_atribucion_id",
    "EVALUACION_PROMESAS": "ejecucion_evaluacion_promesas_id",
    "DECISION_V2": "ejecucion_decision_v2_id",
    "GEOGRAFIA": "ejecucion_geografia_id",
    "TERRITORIAL_V2": "ejecucion_territorial_v2_id",
    "CAMPO_V2": "ejecucion_campo_v2_id",
    "RUTEO_V2": "ejecucion_ruteo_v2_id",
}
"""La columna que apunta al recurso de cada tipo de entrada, con su llave foranea."""

OBJETIVO_DE_LA_ENTRADA = " AND ".join(
    f"(tipo_entrada = '{tipo}') = ({columna} IS NOT NULL)"
    for tipo, columna in COLUMNA_DE_LA_ENTRADA.items()
)
"""Exactamente un recurso, el de su tipo."""

VENTANAS_DE_ENTRADA = "('MOTOR_PAGOS', 'ATRIBUCION')"
"""Las entradas que se declaran una por ventana (un mes); las demas, a lo mas una."""

ESTADOS_VALOR_ANALYTICS = ("DISPONIBLE", "NO_DISPONIBLE", "SUPRIMIDO", "NO_APLICA")

COHERENCIA_DEL_AGREGADO = (
    "estado_valor IN ("
    + ", ".join(f"'{e}'" for e in ESTADOS_VALOR_ANALYTICS)
    + ") AND vista IN ('ACUMULADA', 'PERIODO') "
    "AND (estado_valor = 'DISPONIBLE' OR (valor IS NULL AND numerador IS NULL "
    "AND denominador IS NULL)) "
    "AND (estado_valor <> 'SUPRIMIDO' OR poblacion_cuentas IS NULL) "
    "AND (numerador IS NULL) = (denominador IS NULL) "
    "AND (denominador IS NULL OR denominador <> 0 OR valor IS NULL) "
    "AND (denominador IS NULL OR denominador = 0 OR valor IS NOT NULL) "
    "AND (poblacion_cuentas IS NULL OR poblacion_cuentas >= 0) "
    "AND periodo_desde <= periodo_hasta AND (vista = 'PERIODO' OR NOT periodo_parcial) "
    "AND octet_length(dimension_hash) = 32 "
    "AND (dimension_set <> 'TOTAL' OR dimensiones = '{}'::jsonb)"
)
"""Solo un valor DISPONIBLE tiene valor, numerador y denominador; uno SUPRIMIDO tampoco dice su
poblacion. Una razon tiene los dos o ninguno; con denominador cero su valor es NULL, y con uno
distinto de cero, no. La vista ACUMULADA no es parcial, y TOTAL no tiene dimensiones."""

PUBLICACION_DE_ANALYTICS = (
    "(estado = 'EXITOSA' AND cuentas_cohorte > 0 AND agregados_publicados > 0 "
    "AND controles_totales > 0 AND controles_fallidos = 0 AND hash_agregados IS NOT NULL "
    "AND disponibilidad IS NOT NULL) "
    "OR (estado <> 'EXITOSA' AND agregados_publicados = 0 AND hash_agregados IS NULL)"
)
"""Una EXITOSA publico sus agregados y sus controles, y ninguno fallo; las demas no publicaron
ningun agregado (una que fallo un control conserva sus controles)."""


class EstadoAnalytics(StrEnum):
    """En que quedo una ejecucion de analytics/v1. Solo una EXITOSA publica agregados."""

    EN_PROCESO = "EN_PROCESO"
    EXITOSA = "EXITOSA"
    FALLIDA = "FALLIDA"


class ResultadoEjecucionAnalytics(StrEnum):
    """Como termino una ejecucion de analytics/v1."""

    ANALYTICS_PUBLICADA = "ANALYTICS_PUBLICADA"
    """EXITOSA: publico sus agregados y sus controles, todos OK."""
    YA_GENERADA = "YA_GENERADA"
    """FALLIDA: otra ejecucion publico primero la misma firma de entrada."""
    VERSION_NO_SOPORTADA = "VERSION_NO_SOPORTADA"
    ENTRADAS_NO_EXITOSAS = "ENTRADAS_NO_EXITOSAS"
    """FALLIDA: una entrada declarada ya no esta EXITOSA."""
    CONTROL_FALLIDO = "CONTROL_FALLIDO"
    """FALLIDA: una reconciliacion de integridad no cuadro; sus controles se conservan."""
    DATOS_INCONSISTENTES = "DATOS_INCONSISTENTES"
    ERROR_INTERNO = "ERROR_INTERNO"
    INTENTOS_AGOTADOS = "INTENTOS_AGOTADOS"


class EjecucionAnalytics(SQLModel, table=True):
    """Una ejecucion de analytics/v1: la cohorte de un corte canonico, de fecha_corte a as_of, con
    lo que se conocia en known_at, sobre un manifiesto declarado.

    Guarda todo lo que fija su resultado: el corte, as_of, known_at, el modo, la zona, la
    granularidad, las versiones del motor, del catalogo y de la politica con sus hashes, el hash del
    manifiesto y la firma de sus entradas. La base garantiza a lo mas una EXITOSA y una EN_PROCESO
    por firma.
    """

    __tablename__ = "ejecucion_analytics"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["corte_canonico_id", "fecha_corte"],
            ["corte_canonico.id", "corte_canonico.fecha_corte"],
            name="fk_analytics_corte",
        ),
        sa.Index(
            "ux_analytics_exitosa",
            "firma_inputs",
            unique=True,
            postgresql_where=sa.text("estado = 'EXITOSA'"),
        ),
        sa.Index(
            "ux_analytics_en_proceso",
            "firma_inputs",
            unique=True,
            postgresql_where=sa.text("estado = 'EN_PROCESO'"),
        ),
        # Las ejecuciones de una cohorte, por horizonte, la mas reciente primero.
        sa.Index("ix_analytics_corte", "corte_canonico_id", "as_of", "id"),
        sa.CheckConstraint(
            "as_of >= fecha_corte AND horizonte_dias = (as_of - fecha_corte) + 1",
            name=conv("ck_analytics_horizonte"),
        ),
        sa.CheckConstraint(
            "hash_catalogo ~ '^[0-9a-f]{64}$' AND hash_politica ~ '^[0-9a-f]{64}$' "
            "AND hash_manifiesto ~ '^[0-9a-f]{64}$' AND firma_inputs ~ '^[0-9a-f]{64}$' "
            "AND (hash_agregados IS NULL OR hash_agregados ~ '^[0-9a-f]{64}$')",
            name=conv("ck_analytics_firmas"),
        ),
        sa.CheckConstraint(
            "(estado = 'EN_PROCESO') = (resultado IS NULL)", name=conv("ck_analytics_resultado")
        ),
        sa.CheckConstraint(
            "cuentas_cohorte >= 0 AND agregados_publicados >= 0 AND controles_totales >= 0 "
            "AND controles_fallidos >= 0 AND controles_fallidos <= controles_totales",
            name=conv("ck_analytics_conteos"),
        ),
        sa.CheckConstraint(PUBLICACION_DE_ANALYTICS, name=conv("ck_analytics_publicacion")),
    )

    id: int | None = Field(default=None, primary_key=True)
    analytics_run_id: UUID = Field(default_factory=uuid4, unique=True)
    """Identificador publico de este intento. Lo determinista es su firma y lo que publica."""
    corte_canonico_id: int
    """El corte cuyos snapshots son la cohorte. Su llave foranea, con su fecha, esta arriba."""
    despacho_id: str = Field(max_length=32)
    cartera_id: str = Field(max_length=32)
    fecha_corte: date
    as_of: date
    """Hasta que dia de negocio se describe: hasta el final de ese dia en la zona."""
    known_at: datetime = Field(sa_type=sa.DateTime(timezone=True))
    """Hasta que instante podia conocerse lo que se usa. En OPERATIVO sin declararlo, el reloj de la
    base al abrir, una sola vez."""
    modo: Modo = Field(
        sa_type=sa.Enum(
            Modo, name="modo_analytics", native_enum=False, create_constraint=True, length=12
        )
    )
    zona_horaria: str = Field(max_length=64)
    """La zona IANA de los dias del horizonte y de sus periodos: explicita, nunca la del sistema."""
    granularidad: Granularidad = Field(
        sa_type=sa.Enum(
            Granularidad,
            name="granularidad_analytics",
            native_enum=False,
            create_constraint=True,
            length=12,
        )
    )
    horizonte_dias: int
    """as_of - fecha_corte + 1: dias de calendario inclusivos."""
    version_motor: str = Field(max_length=32)
    version_catalogo: str = Field(max_length=40)
    hash_catalogo: str = Field(max_length=64)
    version_politica: str = Field(max_length=40)
    hash_politica: str = Field(max_length=64)
    hash_manifiesto: str = Field(max_length=64)
    """SHA-256 de las entradas declaradas, en orden canonico."""
    firma_inputs: str = Field(max_length=64)
    """SHA-256 de todo lo que fija el resultado: el mismo conjunto logico, la misma firma."""
    estado: EstadoAnalytics = Field(
        default=EstadoAnalytics.EN_PROCESO,
        sa_type=sa.Enum(
            EstadoAnalytics,
            name="estado_analytics",
            native_enum=False,
            create_constraint=True,
            length=12,
        ),
    )
    resultado: str | None = Field(default=None, max_length=40)
    cuentas_cohorte: int = Field(default=0, sa_type=sa.BigInteger)
    """Las cuentas del corte: sus snapshots. Se fija al abrir."""
    agregados_publicados: int = Field(default=0, sa_type=sa.BigInteger)
    controles_totales: int = 0
    controles_fallidos: int = 0
    hash_agregados: str | None = Field(default=None, max_length=64)
    """SHA-256 de todo lo que publico, en orden canonico: reconstruirla da el mismo."""
    disponibilidad: dict | None = Field(default=None, sa_type=JSONB(none_as_null=True))
    """Que familias y conjuntos se pudieron calcular, la cobertura de pagos por dia y por mes, y las
    advertencias: explica cada NO_DISPONIBLE."""
    iniciada_en: datetime = Field(default_factory=ahora, sa_type=sa.DateTime(timezone=True))
    terminada_en: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    detalle: str | None = None


class EntradaAnalytics(SQLModel, table=True):
    """Una entrada declarada del manifiesto de una ejecucion: el corte, o una ejecucion de otro
    dominio, con su identificador publico, su version, su firma, su ventana o su fecha, y el
    instante en que estuvo disponible. Nace con la ejecucion y no cambia."""

    __tablename__ = "entrada_analytics"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["ejecucion_analytics_id"],
            ["ejecucion_analytics.id"],
            name="fk_entrada_analytics_ejecucion",
        ),
        sa.ForeignKeyConstraint(
            ["corte_canonico_id"], ["corte_canonico.id"], name="fk_entrada_analytics_corte"
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_motor_pagos_id"],
            ["ejecucion_motor_pagos.id"],
            name="fk_entrada_analytics_motor_pagos",
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_atribucion_id"],
            ["ejecucion_atribucion.id"],
            name="fk_entrada_analytics_atribucion",
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_evaluacion_promesas_id"],
            ["ejecucion_evaluacion_promesas.id"],
            name="fk_entrada_analytics_evaluacion",
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_decision_v2_id"],
            ["ejecucion_decision_v2.id"],
            name="fk_entrada_analytics_decision_v2",
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_geografia_id"],
            ["ejecucion_geografia.id"],
            name="fk_entrada_analytics_geografia",
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_territorial_v2_id"],
            ["ejecucion_territorial_v2.id"],
            name="fk_entrada_analytics_territorial_v2",
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_campo_v2_id"],
            ["ejecucion_campo_v2.id"],
            name="fk_entrada_analytics_campo_v2",
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_ruteo_v2_id"],
            ["ejecucion_ruteo_v2.id"],
            name="fk_entrada_analytics_ruteo_v2",
        ),
        # Una ventana mensual no aporta dos interpretaciones de pagos, ni dos atribuciones; y de lo
        # demas, a lo mas una entrada por tipo.
        sa.Index(
            "ux_entrada_analytics_ventana",
            "ejecucion_analytics_id",
            "tipo_entrada",
            "periodo_desde",
            unique=True,
            postgresql_where=sa.text(f"tipo_entrada IN {VENTANAS_DE_ENTRADA}"),
        ),
        sa.Index(
            "ux_entrada_analytics_unica",
            "ejecucion_analytics_id",
            "tipo_entrada",
            unique=True,
            postgresql_where=sa.text(f"tipo_entrada NOT IN {VENTANAS_DE_ENTRADA}"),
        ),
        sa.CheckConstraint(
            "tipo_entrada IN (" + ", ".join(f"'{t}'" for t in TIPOS_ENTRADA_ANALYTICS) + ")",
            name=conv("ck_entrada_analytics_tipo"),
        ),
        sa.CheckConstraint(OBJETIVO_DE_LA_ENTRADA, name=conv("ck_entrada_analytics_objetivo")),
        sa.CheckConstraint(
            "firma ~ '^[0-9a-f]{64}$' AND (periodo_desde IS NULL OR periodo_hasta IS NULL "
            f"OR periodo_desde <= periodo_hasta) AND (tipo_entrada NOT IN {VENTANAS_DE_ENTRADA} "
            "OR (periodo_desde IS NOT NULL AND periodo_hasta IS NOT NULL))",
            name=conv("ck_entrada_analytics_firma"),
        ),
    )

    # La llave primaria es la natural: una entrada, una vez por ejecucion.
    ejecucion_analytics_id: int = Field(primary_key=True)
    tipo_entrada: str = Field(primary_key=True, max_length=24)
    referencia_publica: UUID = Field(primary_key=True)
    """El identificador publico del recurso: corte_id o su *_run_id."""
    corte_canonico_id: int | None = None
    ejecucion_motor_pagos_id: int | None = None
    ejecucion_atribucion_id: int | None = None
    ejecucion_evaluacion_promesas_id: int | None = None
    ejecucion_decision_v2_id: int | None = None
    ejecucion_geografia_id: int | None = None
    ejecucion_territorial_v2_id: int | None = None
    ejecucion_campo_v2_id: int | None = None
    ejecucion_ruteo_v2_id: int | None = None
    version: str = Field(max_length=40)
    """La version del recurso: historia/v1, motor-pagos/v1, decision-policy/public-v1..."""
    firma: str = Field(max_length=64)
    """La firma del recurso: firma_contenido, firma_entrada o firma_inputs."""
    periodo_desde: date | None = None
    """La ventana de pagos o de atribucion, la fecha de corte, el as_of de una evaluacion o la
    fecha de operacion de un plan, segun el tipo."""
    periodo_hasta: date | None = None
    disponible_en: datetime = Field(sa_type=sa.DateTime(timezone=True))
    """El instante persistido en que estuvo disponible: terminada_en de una ejecucion, creado_en de
    un corte. Ninguno se inventa."""
    metadatos: dict = Field(sa_type=JSONB)
    """Lo minimo que la describe, sin datos de cuentas."""


class AgregadoAnalytics(SQLModel, table=True):
    """Un valor publicado: una metrica del catalogo, en un periodo de una vista, en una celda de un
    conjunto de dimensiones. Inmutable.

    Es una fila por metrica, y no una tabla ancha, porque el volumen es de agregados (periodos x
    celdas x metricas), no de cuentas. `dimension_hash` es el SHA-256 de `dimensiones` en forma
    canonica. Un valor SUPRIMIDO no guarda su valor, ni su numerador, ni su denominador, ni su
    poblacion: no existen fuera de la transaccion que los calculo.
    """

    __tablename__ = "agregado_analytics"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["ejecucion_analytics_id"],
            ["ejecucion_analytics.id"],
            name="fk_agregado_analytics_ejecucion",
        ),
        # Todo lo de un conjunto de una vista, por periodo, sin recorrer las demas metricas.
        sa.Index(
            "ix_agregado_analytics_conjunto",
            "ejecucion_analytics_id",
            "dimension_set",
            "vista",
            "periodo_desde",
        ),
        sa.CheckConstraint(COHERENCIA_DEL_AGREGADO, name=conv("ck_agregado_analytics_valor")),
    )

    # La llave primaria es la natural, y es tambien el indice de una metrica: su serie en una
    # vista y un conjunto, por periodo.
    ejecucion_analytics_id: int = Field(primary_key=True)
    metrica_codigo: str = Field(primary_key=True, max_length=64)
    vista: str = Field(primary_key=True, max_length=12)
    dimension_set: str = Field(primary_key=True, max_length=24)
    periodo_desde: date = Field(primary_key=True)
    dimension_hash: bytes = Field(primary_key=True, sa_type=sa.LargeBinary)
    periodo_hasta: date
    periodo_parcial: bool
    dimensiones: dict = Field(sa_type=JSONB)
    estado_valor: str = Field(max_length=16)
    poblacion_cuentas: int | None = Field(default=None, sa_type=sa.BigInteger)
    """Las cuentas distintas del universo de la celda en el horizonte. NULL si la celda esta
    suprimida, o si la metrica no es de cuentas."""
    valor: Decimal | None = Field(default=None, sa_type=sa.Numeric())
    numerador: Decimal | None = Field(default=None, sa_type=sa.Numeric())
    denominador: Decimal | None = Field(default=None, sa_type=sa.Numeric())
    motivos: list[dict] = Field(sa_type=JSONB)
    """Por que el estado es el que es, con codigos estables: la entrada que falta, el dia sin
    cobertura, la supresion. Vacio en un valor DISPONIBLE con denominador distinto de cero."""


class ControlAnalytics(SQLModel, table=True):
    """Una reconciliacion de una ejecucion: que se esperaba, que se observo y la diferencia, sin
    datos de cuentas. Un control que falla deja la ejecucion FALLIDA."""

    __tablename__ = "control_analytics"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["ejecucion_analytics_id"],
            ["ejecucion_analytics.id"],
            name="fk_control_analytics_ejecucion",
        ),
        sa.CheckConstraint(
            "estado IN ('OK', 'FALLO') AND (esperado IS NULL) = (observado IS NULL) "
            "AND (diferencia IS NULL) = (esperado IS NULL) "
            "AND (diferencia IS NULL OR diferencia = observado - esperado) "
            "AND (diferencia IS NULL OR (estado = 'OK') = (diferencia = 0))",
            name=conv("ck_control_analytics_estado"),
        ),
    )

    ejecucion_analytics_id: int = Field(primary_key=True)
    codigo_control: str = Field(primary_key=True, max_length=80)
    estado: str = Field(max_length=8)
    esperado: Decimal | None = Field(default=None, sa_type=sa.Numeric())
    observado: Decimal | None = Field(default=None, sa_type=sa.Numeric())
    diferencia: Decimal | None = Field(default=None, sa_type=sa.Numeric())
    detalle: str = Field(max_length=500)
    """Que se comparo, para una persona: codigos, conteos y conjuntos, nunca una cuenta."""


# --- Predictive Intelligence ----------------------------------------------------------------------
#
# Desde la 0016. predictive/model-v1 predice resultados observados bajo la operacion historica,
# con lo que se conocia en cada momento: no es una decision ni un efecto causal, y no cambia
# decision/v2, campo/v2 ni ruteo/v2. Un entrenamiento (EjecucionEntrenamientoPredictivo) lee un
# manifiesto explicito: sus anclas (AnclaEntrenamientoPredictivo), cada una con sus tres relojes,
# sus marcas de cobertura y su split, y las interpretaciones de pagos de sus labels
# (InterpretacionEntrenamientoPredictivo). Publica, todo o nada, una version del modelo
# (VersionModeloPredictivo) con sus cuatro componentes (ComponenteModeloPredictivo), cada uno con
# su artefacto JSON y sus metricas. Un scoring (EjecucionScoringPredictivo) usa una version
# PUBLICADA declarada sobre un corte, y publica, todo o nada, una prediccion por cuenta
# (PrediccionCuenta) y su drift contra TRAIN (DriftPredictivo). Lo publicado no se reescribe: los
# triggers de la 0016 rechazan cualquier UPDATE o DELETE sobre las anclas, las interpretaciones,
# los componentes, las predicciones y el drift, sobre una version terminada y sobre una ejecucion
# terminada. Ninguna tabla guarda un dato personal; las predicciones de una cuenta solo salen con su
# cuenta_id.

PROBABILIDADES_PREDICTIVAS = (
    "p_recuperacion_positiva_30d",
    "p_contacto_14d",
    "p_promesa_30d",
)

RANGOS_DE_LA_PREDICCION = (
    " AND ".join(f"{p} BETWEEN 0 AND 1" for p in PROBABILIDADES_PREDICTIVAS)
    + " AND riesgo_sin_recuperacion_30d = 1 - p_recuperacion_positiva_30d"
    + " AND recuperacion_positiva_esperada_30d >= 0"
    + " AND recuperacion_positiva_esperada_30d <> 'NaN'::numeric"
    + " AND octet_length(feature_hash) = 32"
)
"""Cada probabilidad en [0, 1] (BETWEEN rechaza NaN e infinito), el riesgo exactamente el
complemento de la de recuperacion, y la recuperacion positiva esperada finita y no negativa."""

TARGETS_PREDICTIVOS = (
    "RECUPERACION_POSITIVA_30D",
    "CONTACTO_14D",
    "PROMESA_30D",
    "RECUPERACION_POSITIVA_MONTO_30D",
)

SPLITS_PREDICTIVOS = ("TRAIN", "VALIDATION", "TEST", "PURGE")

PUBLICACION_DEL_ENTRENAMIENTO = (
    "(estado = 'EN_PROCESO' AND resultado IS NULL AND dataset_hash IS NULL) "
    "OR (estado = 'EXITOSA' AND resultado IN ('MODELO_PUBLICADO', 'MODELO_RECHAZADO') "
    "AND dataset_hash IS NOT NULL AND ejemplos_train > 0) "
    "OR (estado = 'FALLIDA' AND resultado IS NOT NULL "
    "AND resultado NOT IN ('MODELO_PUBLICADO', 'MODELO_RECHAZADO'))"
)
"""Una EXITOSA construyo su dataset y dejo un modelo, PUBLICADA o RECHAZADA; una FALLIDA no dejo
ninguno."""

CONTEOS_DEL_ENTRENAMIENTO = (
    "anclas >= 0 AND ejemplos_total >= 0 AND ejemplos_train >= 0 AND ejemplos_validation >= 0 "
    "AND ejemplos_test >= 0 AND ejemplos_purge >= 0 AND ejemplos_censurados >= 0 "
    "AND ejemplos_train + ejemplos_validation + ejemplos_test + ejemplos_purge = ejemplos_total "
    "AND ejemplos_censurados <= ejemplos_total"
)

PUBLICACION_DEL_SCORING = (
    "(estado = 'EXITOSA' AND cuentas_objetivo > 0 "
    "AND predicciones_publicadas = cuentas_objetivo "
    "AND hash_predicciones IS NOT NULL AND hash_drift IS NOT NULL) "
    "OR (estado <> 'EXITOSA' AND predicciones_publicadas = 0 "
    "AND hash_predicciones IS NULL AND hash_drift IS NULL)"
)
"""Una EXITOSA publico una prediccion por cada cuenta de su corte y su drift; las demas, nada."""


class EstadoEntrenamientoPredictivo(StrEnum):
    EN_PROCESO = "EN_PROCESO"
    EXITOSA = "EXITOSA"
    """Construyo su dataset y dejo una version, PUBLICADA o RECHAZADA por sus compuertas."""
    FALLIDA = "FALLIDA"


class ResultadoEntrenamientoPredictivo(StrEnum):
    MODELO_PUBLICADO = "MODELO_PUBLICADO"
    """EXITOSA: los cuatro componentes pasaron sus compuertas y la version quedo PUBLICADA."""
    MODELO_RECHAZADO = "MODELO_RECHAZADO"
    """EXITOSA: algun componente no paso sus compuertas en TEST; la version quedo RECHAZADA, con
    sus metricas, y nada se publico."""
    YA_ENTRENADO = "YA_ENTRENADO"
    """FALLIDA: otra ejecucion publico primero la misma firma."""
    VERSION_NO_SOPORTADA = "VERSION_NO_SOPORTADA"
    SIN_EJEMPLOS = "SIN_EJEMPLOS"
    """FALLIDA: algun bloque del split no tiene ejemplos maduros de algun target."""
    DATOS_INCONSISTENTES = "DATOS_INCONSISTENTES"
    ERROR_INTERNO = "ERROR_INTERNO"
    INTENTOS_AGOTADOS = "INTENTOS_AGOTADOS"


class EstadoModeloPredictivo(StrEnum):
    """El ciclo de una version: CANDIDATA (ajustada), VALIDADA (paso sus compuertas) y PUBLICADA, o
    RECHAZADA. Todo en la transaccion que la publica: fuera de ella solo se ve PUBLICADA o
    RECHAZADA."""

    CANDIDATA = "CANDIDATA"
    VALIDADA = "VALIDADA"
    PUBLICADA = "PUBLICADA"
    RECHAZADA = "RECHAZADA"


class EstadoScoringPredictivo(StrEnum):
    EN_PROCESO = "EN_PROCESO"
    EXITOSA = "EXITOSA"
    FALLIDA = "FALLIDA"


class ModoScoringPredictivo(StrEnum):
    OPERATIVO = "OPERATIVO"
    """known_at puede omitirse: es el reloj de la base al abrir, una sola vez."""
    BACKTEST = "BACKTEST"
    """known_at es obligatorio: un backtest declara su frontera de conocimiento."""


class ResultadoScoringPredictivo(StrEnum):
    PREDICCIONES_PUBLICADAS = "PREDICCIONES_PUBLICADAS"
    YA_PUNTUADO = "YA_PUNTUADO"
    """FALLIDA: otra ejecucion publico primero la misma firma."""
    VERSION_NO_SOPORTADA = "VERSION_NO_SOPORTADA"
    DATOS_INCONSISTENTES = "DATOS_INCONSISTENTES"
    ERROR_INTERNO = "ERROR_INTERNO"
    INTENTOS_AGOTADOS = "INTENTOS_AGOTADOS"


class EjecucionEntrenamientoPredictivo(SQLModel, table=True):
    """Un entrenamiento de predictive/model-v1 sobre un manifiesto explicito: que anclas, con que
    relojes, que split y que interpretaciones de pagos definen sus labels. Guarda las versiones y
    los hashes de las features, de los targets y de la politica, el hash del manifiesto, la firma
    de todo lo que fija el resultado y, al terminar, el hash del dataset y sus conteos. La base
    garantiza a lo mas una EXITOSA y una EN_PROCESO por firma."""

    __tablename__ = "ejecucion_entrenamiento_predictivo"
    __table_args__ = (
        sa.Index(
            "ux_entrenamiento_predictivo_exitosa",
            "firma_inputs",
            unique=True,
            postgresql_where=sa.text("estado = 'EXITOSA'"),
        ),
        sa.Index(
            "ux_entrenamiento_predictivo_en_proceso",
            "firma_inputs",
            unique=True,
            postgresql_where=sa.text("estado = 'EN_PROCESO'"),
        ),
        # La lista de la API, la mas reciente primero, de un estado o de todos.
        sa.Index("ix_entrenamiento_predictivo_estado", "estado", "id"),
        sa.CheckConstraint(
            "hash_features ~ '^[0-9a-f]{64}$' AND hash_targets ~ '^[0-9a-f]{64}$' "
            "AND hash_politica ~ '^[0-9a-f]{64}$' AND hash_manifiesto ~ '^[0-9a-f]{64}$' "
            "AND firma_inputs ~ '^[0-9a-f]{64}$' "
            "AND (dataset_hash IS NULL OR dataset_hash ~ '^[0-9a-f]{64}$')",
            name=conv("ck_entrenamiento_predictivo_firmas"),
        ),
        sa.CheckConstraint(
            CONTEOS_DEL_ENTRENAMIENTO, name=conv("ck_entrenamiento_predictivo_conteos")
        ),
        sa.CheckConstraint(
            PUBLICACION_DEL_ENTRENAMIENTO, name=conv("ck_entrenamiento_predictivo_publicacion")
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    training_run_id: UUID = Field(default_factory=uuid4, unique=True)
    """Identificador publico de este intento. Lo determinista es su firma y lo que publica."""
    despacho_id: str = Field(max_length=32)
    cartera_id: str = Field(max_length=32)
    zona_horaria: str = Field(max_length=64)
    """La zona IANA de los dias de feature_as_of y de las ventanas de los labels."""
    version_modelo: str = Field(max_length=32)
    version_features: str = Field(max_length=40)
    hash_features: str = Field(max_length=64)
    version_targets: str = Field(max_length=40)
    hash_targets: str = Field(max_length=64)
    version_politica: str = Field(max_length=40)
    hash_politica: str = Field(max_length=64)
    manifiesto: dict = Field(sa_type=JSONB)
    """El manifiesto declarado, en su forma canonica: lo que se puede repetir."""
    hash_manifiesto: str = Field(max_length=64)
    firma_inputs: str = Field(max_length=64)
    """SHA-256 de todo lo que fija el resultado: el mismo manifiesto con las mismas versiones."""
    dataset_hash: str | None = Field(default=None, max_length=64)
    """SHA-256 del dataset, fila por fila en orden canonico: sus features, labels y madurez."""
    estado: EstadoEntrenamientoPredictivo = Field(
        default=EstadoEntrenamientoPredictivo.EN_PROCESO,
        sa_type=sa.Enum(
            EstadoEntrenamientoPredictivo,
            name="estado_entrenamiento",
            native_enum=False,
            create_constraint=True,
            length=12,
        ),
    )
    resultado: str | None = Field(default=None, max_length=40)
    anclas: int = 0
    ejemplos_total: int = Field(default=0, sa_type=sa.BigInteger)
    """Filas cuenta-ancla del manifiesto, maduras o no."""
    ejemplos_train: int = Field(default=0, sa_type=sa.BigInteger)
    ejemplos_validation: int = Field(default=0, sa_type=sa.BigInteger)
    ejemplos_test: int = Field(default=0, sa_type=sa.BigInteger)
    ejemplos_purge: int = Field(default=0, sa_type=sa.BigInteger)
    ejemplos_censurados: int = Field(default=0, sa_type=sa.BigInteger)
    """Filas con al menos un label CENSURADA o NO_DISPONIBLE; el detalle, por target, en conteos."""
    conteos: dict | None = Field(default=None, sa_type=JSONB(none_as_null=True))
    """Por target y split: maduras, censuradas, no disponibles y la prevalencia o la media."""
    backtest: dict | None = Field(default=None, sa_type=JSONB(none_as_null=True))
    """El walk-forward: cada fold con su periodo, su eleccion y sus metricas por target."""
    tiempos: dict | None = Field(default=None, sa_type=JSONB(none_as_null=True))
    """Cuanto tardo cada fase y la memoria pico del proceso."""
    iniciada_en: datetime = Field(default_factory=ahora, sa_type=sa.DateTime(timezone=True))
    terminada_en: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    detalle: str | None = None


class AnclaEntrenamientoPredictivo(SQLModel, table=True):
    """Un ancla del manifiesto de un entrenamiento: un corte canonico, el dia de la prediccion (su
    fecha de corte), sus dos fronteras de conocimiento, las marcas de cobertura que declara y su
    split. Nace con el entrenamiento y no cambia."""

    __tablename__ = "ancla_entrenamiento_predictivo"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["ejecucion_entrenamiento_predictivo_id"],
            ["ejecucion_entrenamiento_predictivo.id"],
            name="fk_ancla_predictiva_entrenamiento",
        ),
        sa.ForeignKeyConstraint(
            ["corte_canonico_id", "feature_as_of"],
            ["corte_canonico.id", "corte_canonico.fecha_corte"],
            name="fk_ancla_predictiva_corte",
        ),
        sa.CheckConstraint(
            "split IN (" + ", ".join(f"'{s}'" for s in SPLITS_PREDICTIVOS) + ")",
            name=conv("ck_ancla_predictiva_split"),
        ),
        sa.CheckConstraint(
            "feature_known_at <= label_known_at AND hash_ancla ~ '^[0-9a-f]{64}$'",
            name=conv("ck_ancla_predictiva_relojes"),
        ),
    )

    ejecucion_entrenamiento_predictivo_id: int = Field(primary_key=True)
    corte_canonico_id: int = Field(primary_key=True)
    feature_as_of: date
    """El dia de negocio de la prediccion: la fecha del corte (lo exige su llave foranea)."""
    feature_known_at: datetime = Field(sa_type=sa.DateTime(timezone=True))
    label_known_at: datetime = Field(sa_type=sa.DateTime(timezone=True))
    pagos_completos_hasta: date | None = None
    """Hasta que dia los pagos estaban completos a label_known_at, como lo declara el manifiesto:
    evidencia declarada, nunca la fecha maxima observada. NULL: no declarada."""
    lifecycle_completo_hasta: date | None = None
    split: str = Field(max_length=12)
    hash_ancla: str = Field(max_length=64)
    """SHA-256 de la declaracion canonica del ancla."""


class InterpretacionEntrenamientoPredictivo(SQLModel, table=True):
    """Una interpretacion de pagos declarada para los labels de un entrenamiento: una por mes.
    Nace con el entrenamiento y no cambia."""

    __tablename__ = "interpretacion_entrenamiento_predictivo"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["ejecucion_entrenamiento_predictivo_id"],
            ["ejecucion_entrenamiento_predictivo.id"],
            name="fk_interpretacion_predictiva_entrenamiento",
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_motor_pagos_id"],
            ["ejecucion_motor_pagos.id"],
            name="fk_interpretacion_predictiva_motor_pagos",
        ),
        sa.UniqueConstraint(
            "ejecucion_entrenamiento_predictivo_id",
            "ejecucion_motor_pagos_id",
            name="uq_interpretacion_predictiva_ejecucion",
        ),
    )

    ejecucion_entrenamiento_predictivo_id: int = Field(primary_key=True)
    periodo_desde: date = Field(primary_key=True)
    """El mes de la interpretacion: una por mes, o un movimiento contaria dos veces."""
    ejecucion_motor_pagos_id: int


class VersionModeloPredictivo(SQLModel, table=True):
    """Una version de predictive/model-v1: lo que dejo un entrenamiento EXITOSA, con sus versiones
    y hashes, el hash del dataset, el del modelo (de sus artefactos), sus periodos y la referencia
    de TRAIN para el drift. PUBLICADA, es inmutable y es la unica que un scoring puede usar; un
    cambio es otra version, de otro entrenamiento."""

    __tablename__ = "version_modelo_predictivo"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["ejecucion_entrenamiento_predictivo_id"],
            ["ejecucion_entrenamiento_predictivo.id"],
            name="fk_modelo_predictivo_entrenamiento",
        ),
        sa.UniqueConstraint(
            "ejecucion_entrenamiento_predictivo_id", name="uq_modelo_predictivo_entrenamiento"
        ),
        # El registro de la API, por estado y del mas reciente al mas antiguo.
        sa.Index("ix_modelo_predictivo_estado", "estado", "creada_en"),
        sa.CheckConstraint(
            "hash_features ~ '^[0-9a-f]{64}$' AND hash_targets ~ '^[0-9a-f]{64}$' "
            "AND hash_politica ~ '^[0-9a-f]{64}$' AND dataset_hash ~ '^[0-9a-f]{64}$' "
            "AND hash_modelo ~ '^[0-9a-f]{64}$'",
            name=conv("ck_modelo_predictivo_firmas"),
        ),
        sa.CheckConstraint(
            "(estado = 'PUBLICADA') = (publicada_en IS NOT NULL)",
            name=conv("ck_modelo_predictivo_publicada"),
        ),
        sa.CheckConstraint(
            "periodo_train_desde <= periodo_train_hasta "
            "AND periodo_train_hasta < periodo_validation_desde "
            "AND periodo_validation_desde <= periodo_validation_hasta "
            "AND periodo_validation_hasta < periodo_test_desde "
            "AND periodo_test_desde <= periodo_test_hasta",
            name=conv("ck_modelo_predictivo_periodos"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    modelo_version_id: UUID = Field(default_factory=uuid4, unique=True)
    """Identificador publico de la version."""
    version_modelo: str = Field(max_length=32)
    ejecucion_entrenamiento_predictivo_id: int
    version_features: str = Field(max_length=40)
    hash_features: str = Field(max_length=64)
    version_targets: str = Field(max_length=40)
    hash_targets: str = Field(max_length=64)
    version_politica: str = Field(max_length=40)
    hash_politica: str = Field(max_length=64)
    dataset_hash: str = Field(max_length=64)
    hash_modelo: str = Field(max_length=64)
    """SHA-256 de los hashes de sus artefactos, por target: cambia si cambia un coeficiente."""
    estado: EstadoModeloPredictivo = Field(
        sa_type=sa.Enum(
            EstadoModeloPredictivo,
            name="estado_modelo_predictivo",
            native_enum=False,
            create_constraint=True,
            length=12,
        ),
    )
    creada_en: datetime = Field(default_factory=ahora, sa_type=sa.DateTime(timezone=True))
    publicada_en: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    periodo_train_desde: date
    periodo_train_hasta: date
    periodo_validation_desde: date
    periodo_validation_hasta: date
    periodo_test_desde: date
    periodo_test_hasta: date
    referencia_drift: dict = Field(sa_type=JSONB)
    """Las distribuciones de TRAIN contra las que un scoring mide su drift: bins de cada feature,
    su fraccion de NULL y bins de cada salida."""
    metadatos: dict = Field(sa_type=JSONB)
    """La ficha del modelo: uso previsto y no previsto, prevalencias, compuertas, limitaciones y las
    versiones del entorno con que se entreno."""


class ComponenteModeloPredictivo(SQLModel, table=True):
    """Un componente de una version: el modelo de un target, con su algoritmo, sus
    hiperparametros, su artefacto JSON con su hash, su calibracion, sus metricas de VALIDATION y de
    TEST, el baseline de TEST, sus intervalos bootstrap y sus compuertas. Inmutable."""

    __tablename__ = "componente_modelo_predictivo"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["version_modelo_predictivo_id"],
            ["version_modelo_predictivo.id"],
            name="fk_componente_predictivo_modelo",
        ),
        sa.CheckConstraint(
            "target IN (" + ", ".join(f"'{t}'" for t in TARGETS_PREDICTIVOS) + ") "
            "AND algoritmo IN ('LOGISTICA_L2', 'TWEEDIE_LOG') "
            "AND calibracion IN ('RAW', 'PLATT') AND estado IN ('VALIDADO', 'RECHAZADO') "
            "AND hash_artefacto ~ '^[0-9a-f]{64}$' "
            "AND ((target = 'RECUPERACION_POSITIVA_MONTO_30D') = (algoritmo = 'TWEEDIE_LOG'))",
            name=conv("ck_componente_predictivo_coherencia"),
        ),
    )

    version_modelo_predictivo_id: int = Field(primary_key=True)
    target: str = Field(primary_key=True, max_length=40)
    algoritmo: str = Field(max_length=24)
    hiperparametros: dict = Field(sa_type=JSONB)
    artefacto: dict = Field(sa_type=JSONB)
    hash_artefacto: str = Field(max_length=64)
    calibracion: str = Field(max_length=8)
    metricas_validation: dict = Field(sa_type=JSONB)
    metricas_test: dict = Field(sa_type=JSONB)
    baseline_test: dict = Field(sa_type=JSONB)
    bootstrap_test: dict = Field(sa_type=JSONB)
    explicacion_global: list[dict] = Field(sa_type=JSONB)
    """Cada coeficiente con su feature, su direccion y su magnitud en la escala estandarizada: una
    asociacion del modelo, no una importancia causal."""
    estado: str = Field(max_length=12)
    compuertas: list[dict] = Field(sa_type=JSONB)


class EjecucionScoringPredictivo(SQLModel, table=True):
    """Un scoring: una version PUBLICADA declarada, un corte canonico, su as_of (el dia del corte) y
    known_at. Nunca "el ultimo modelo". La base garantiza a lo mas una EXITOSA y una EN_PROCESO por
    firma."""

    __tablename__ = "ejecucion_scoring_predictivo"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["version_modelo_predictivo_id"],
            ["version_modelo_predictivo.id"],
            name="fk_scoring_predictivo_modelo",
        ),
        sa.ForeignKeyConstraint(
            ["corte_canonico_id", "as_of"],
            ["corte_canonico.id", "corte_canonico.fecha_corte"],
            name="fk_scoring_predictivo_corte",
        ),
        # Lo que referencia cada prediccion: la ejecucion junto con su corte.
        sa.UniqueConstraint("id", "corte_canonico_id", name="uq_scoring_predictivo_id_corte"),
        sa.Index(
            "ux_scoring_predictivo_exitosa",
            "firma_inputs",
            unique=True,
            postgresql_where=sa.text("estado = 'EXITOSA'"),
        ),
        sa.Index(
            "ux_scoring_predictivo_en_proceso",
            "firma_inputs",
            unique=True,
            postgresql_where=sa.text("estado = 'EN_PROCESO'"),
        ),
        sa.Index("ix_scoring_predictivo_corte", "corte_canonico_id", "id"),
        sa.Index("ix_scoring_predictivo_modelo", "version_modelo_predictivo_id", "id"),
        sa.Index("ix_scoring_predictivo_estado", "estado", "id"),
        sa.CheckConstraint(
            "firma_inputs ~ '^[0-9a-f]{64}$' AND hash_features ~ '^[0-9a-f]{64}$' "
            "AND (hash_predicciones IS NULL OR hash_predicciones ~ '^[0-9a-f]{64}$') "
            "AND (hash_drift IS NULL OR hash_drift ~ '^[0-9a-f]{64}$')",
            name=conv("ck_scoring_predictivo_firmas"),
        ),
        sa.CheckConstraint(
            "(estado = 'EN_PROCESO') = (resultado IS NULL) AND cuentas_objetivo >= 0 "
            "AND predicciones_publicadas >= 0",
            name=conv("ck_scoring_predictivo_resultado"),
        ),
        sa.CheckConstraint(PUBLICACION_DEL_SCORING, name=conv("ck_scoring_predictivo_publicacion")),
    )

    id: int | None = Field(default=None, primary_key=True)
    scoring_run_id: UUID = Field(default_factory=uuid4, unique=True)
    version_modelo_predictivo_id: int
    corte_canonico_id: int
    despacho_id: str = Field(max_length=32)
    cartera_id: str = Field(max_length=32)
    as_of: date
    """El dia de la prediccion: la fecha del corte (lo exige su llave foranea)."""
    known_at: datetime = Field(sa_type=sa.DateTime(timezone=True))
    modo: ModoScoringPredictivo = Field(
        sa_type=sa.Enum(
            ModoScoringPredictivo,
            name="modo_scoring_predictivo",
            native_enum=False,
            create_constraint=True,
            length=12,
        )
    )
    zona_horaria: str = Field(max_length=64)
    version_features: str = Field(max_length=40)
    hash_features: str = Field(max_length=64)
    firma_inputs: str = Field(max_length=64)
    estado: EstadoScoringPredictivo = Field(
        default=EstadoScoringPredictivo.EN_PROCESO,
        sa_type=sa.Enum(
            EstadoScoringPredictivo,
            name="estado_scoring_predictivo",
            native_enum=False,
            create_constraint=True,
            length=12,
        ),
    )
    resultado: str | None = Field(default=None, max_length=40)
    cuentas_objetivo: int = Field(default=0, sa_type=sa.BigInteger)
    predicciones_publicadas: int = Field(default=0, sa_type=sa.BigInteger)
    hash_predicciones: str | None = Field(default=None, max_length=64)
    """SHA-256 de las predicciones cuantizadas, en el orden de cuenta_id."""
    hash_drift: str | None = Field(default=None, max_length=64)
    disponibilidad: dict | None = Field(default=None, sa_type=JSONB(none_as_null=True))
    """Que se conocia a known_at: la cobertura de pagos de cada ventana y la evaluacion de promesas
    que se uso."""
    drift_resumen: dict | None = Field(default=None, sa_type=JSONB(none_as_null=True))
    tiempos: dict | None = Field(default=None, sa_type=JSONB(none_as_null=True))
    iniciada_en: datetime = Field(default_factory=ahora, sa_type=sa.DateTime(timezone=True))
    terminada_en: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    detalle: str | None = None


class PrediccionCuenta(SQLModel, table=True):
    """La prediccion de una cuenta del corte en un scoring: sus tres probabilidades, su riesgo sin
    recuperacion (el complemento exacto de la primera) y su recuperacion positiva esperada, con el
    feature_hash de las features con que se calculo. Sin datos personales. Inmutable."""

    __tablename__ = "prediccion_cuenta"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["ejecucion_scoring_predictivo_id", "corte_canonico_id"],
            ["ejecucion_scoring_predictivo.id", "ejecucion_scoring_predictivo.corte_canonico_id"],
            name="fk_prediccion_scoring",
        ),
        sa.ForeignKeyConstraint(
            ["corte_canonico_id", "cuenta_canonica_id"],
            ["snapshot_cuenta.corte_canonico_id", "snapshot_cuenta.cuenta_canonica_id"],
            name="fk_prediccion_snapshot",
        ),
        # Una pagina ordenada por una probabilidad o por el monto, sin ordenar el scoring entero; el
        # riesgo es el orden inverso del de recuperacion.
        sa.Index(
            "ix_prediccion_recuperacion",
            "ejecucion_scoring_predictivo_id",
            "p_recuperacion_positiva_30d",
            "cuenta_canonica_id",
        ),
        sa.Index(
            "ix_prediccion_contacto",
            "ejecucion_scoring_predictivo_id",
            "p_contacto_14d",
            "cuenta_canonica_id",
        ),
        sa.Index(
            "ix_prediccion_promesa",
            "ejecucion_scoring_predictivo_id",
            "p_promesa_30d",
            "cuenta_canonica_id",
        ),
        sa.Index(
            "ix_prediccion_esperada",
            "ejecucion_scoring_predictivo_id",
            "recuperacion_positiva_esperada_30d",
            "cuenta_canonica_id",
        ),
        # Las predicciones de una cuenta en todos sus scorings.
        sa.Index("ix_prediccion_cuenta", "cuenta_canonica_id", "ejecucion_scoring_predictivo_id"),
        sa.CheckConstraint(RANGOS_DE_LA_PREDICCION, name=conv("ck_prediccion_cuenta_rangos")),
        sa.CheckConstraint(
            "estado IN ('COMPLETA', 'CON_IMPUTACION')", name=conv("ck_prediccion_cuenta_estado")
        ),
    )

    ejecucion_scoring_predictivo_id: int = Field(primary_key=True)
    cuenta_canonica_id: int = Field(primary_key=True)
    corte_canonico_id: int
    p_recuperacion_positiva_30d: Decimal = Field(max_digits=9, decimal_places=8)
    p_contacto_14d: Decimal = Field(max_digits=9, decimal_places=8)
    p_promesa_30d: Decimal = Field(max_digits=9, decimal_places=8)
    riesgo_sin_recuperacion_30d: Decimal = Field(max_digits=9, decimal_places=8)
    recuperacion_positiva_esperada_30d: Decimal = Field(max_digits=14, decimal_places=2)
    estado: str = Field(max_length=16)
    """COMPLETA, o CON_IMPUTACION si alguna feature era NULL o una categoria desconocida."""
    motivos: list[dict] = Field(sa_type=JSONB)
    feature_hash: bytes = Field(sa_type=sa.LargeBinary)


class DriftPredictivo(SQLModel, table=True):
    """El drift de un scoring contra TRAIN, por feature y por salida: su PSI, su fraccion de NULL
    en TRAIN y en el scoring, y su severidad. Agregado: ningun bin nombra una cuenta. Inmutable."""

    __tablename__ = "drift_predictivo"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["ejecucion_scoring_predictivo_id"],
            ["ejecucion_scoring_predictivo.id"],
            name="fk_drift_scoring",
        ),
        sa.CheckConstraint(
            "tipo IN ('FEATURE', 'SCORE') AND psi >= 0 "
            "AND severidad IN ('BAJO', 'MODERADO', 'ALTO') "
            "AND (severidad_faltantes IS NULL OR severidad_faltantes IN "
            "('BAJO', 'MODERADO', 'ALTO')) "
            "AND (faltantes_train IS NULL) = (faltantes_scoring IS NULL) "
            "AND (faltantes_train IS NULL) = (severidad_faltantes IS NULL)",
            name=conv("ck_drift_predictivo_valores"),
        ),
    )

    ejecucion_scoring_predictivo_id: int = Field(primary_key=True)
    tipo: str = Field(primary_key=True, max_length=12)
    nombre: str = Field(primary_key=True, max_length=64)
    clase: str = Field(max_length=16)
    """NUMERICA, CATEGORICA, PROBABILIDAD o MONTO."""
    psi: Decimal = Field(max_digits=14, decimal_places=6)
    severidad: str = Field(max_length=10)
    faltantes_train: Decimal | None = Field(default=None, max_digits=9, decimal_places=6)
    faltantes_scoring: Decimal | None = Field(default=None, max_digits=9, decimal_places=6)
    severidad_faltantes: str | None = Field(default=None, max_length=10)
    bins: dict = Field(sa_type=JSONB)
    """Los bordes de TRAIN y las proporciones de TRAIN y del scoring: distribuciones, no cuentas."""


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
    "AND (tipo = 'EVALUACION_PROMESAS') = (ejecucion_evaluacion_promesas_id IS NOT NULL) "
    "AND (tipo = 'DECISION_V2') = (ejecucion_decision_v2_id IS NOT NULL) "
    "AND (tipo = 'GEOGRAFIA') = (ejecucion_geografia_id IS NOT NULL) "
    "AND (tipo = 'TERRITORIAL_V2') = (ejecucion_territorial_v2_id IS NOT NULL) "
    "AND (tipo = 'CAMPO_V2') = (ejecucion_campo_v2_id IS NOT NULL) "
    "AND (tipo = 'RUTEO_V2') = (ejecucion_ruteo_v2_id IS NOT NULL) "
    "AND (tipo = 'ANALYTICS') = (ejecucion_analytics_id IS NOT NULL) "
    "AND (tipo = 'PREDICTIVE_TRAINING') = (ejecucion_entrenamiento_predictivo_id IS NOT NULL) "
    "AND (tipo = 'PREDICTIVE_SCORING') = (ejecucion_scoring_predictivo_id IS NOT NULL)"
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
    DECISION_V2 = "DECISION_V2"
    """Desde la 0011: decidir las cuentas de un corte canonico con decision/v2, a un as_of y con
    conocimiento hasta known_at. Lo piden POST /decisiones-v2 o decidir-v2. No es de ningun flujo:
    corre en paralelo a decision/v1, que sigue siendo la etapa DECISION."""
    GEOGRAFIA = "GEOGRAFIA"
    """Desde la 0012: resolver la geografia de las cuentas de un corte canonico con geografia/v1 y
    una version declarada de la referencia. Lo piden POST /geografia/ejecuciones o geolocalizar. No
    es de ningun flujo: no bloquea decision, territorial/v1 ni ruteo/v1."""
    TERRITORIAL_V2 = "TERRITORIAL_V2"
    """Desde la 0012: asignar las cuentas de un corte a sus bases con territorial/v2, sobre una
    geografia EXITOSA y una version de bases declaradas. Lo piden POST /territorial-v2 o
    asignar-territorial-v2. Corre en paralelo a territorial/v1, que sigue siendo la etapa
    TERRITORIAL."""
    CAMPO_V2 = "CAMPO_V2"
    """Desde la 0013: el plan de campo/v2 de una fecha, sobre una decision/v2, una territorial/v2 y
    una configuracion de campo declaradas. Lo piden POST /campo-v2/planes, planificar-campo-v2 o
    backfill-campo-v2. Corre en paralelo a territorial/v1 y ruteo/v1, que no lo esperan."""
    RUTEO_V2 = "RUTEO_V2"
    """Desde la 0014: las rutas por la red vial de los gestores de un plan de campo/v2 EXITOSA,
    sobre una version declarada de la red vial. Lo piden POST /ruteo-v2/ejecuciones, rutear-v2 o
    backfill-ruteo-v2. Corre en paralelo a ruteo/v1, que sigue siendo la etapa RUTEO; un worker sin
    proveedor de ruteo, o con uno que no responde, no lo toma."""
    ANALYTICS = "ANALYTICS"
    """Desde la 0015: Collection Analytics de una cohorte, de fecha_corte a as_of, con lo que se
    conocia en known_at, sobre un manifiesto declarado. Lo piden POST /analytics/ejecuciones,
    analizar-cobranza o backfill-analytics. Lee lo que publicaron los demas motores y no cambia
    nada de ellos."""
    PREDICTIVE_TRAINING = "PREDICTIVE_TRAINING"
    """Desde la 0016: entrenar predictive/model-v1 con un manifiesto explicito. Lo piden POST
    /predictivo/entrenamientos o entrenar-predictivo. Lee lo publicado y no cambia nada; nunca lo
    dispara un scoring."""
    PREDICTIVE_SCORING = "PREDICTIVE_SCORING"
    """Desde la 0016: las predicciones de las cuentas de un corte con una version PUBLICADA
    declarada, y su drift. Lo piden POST /predictivo/scorings o score-predictivo."""


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
        sa.ForeignKeyConstraint(
            ["ejecucion_decision_v2_id"],
            ["ejecucion_decision_v2.id"],
            name="fk_trabajo_decision_v2",
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_geografia_id"], ["ejecucion_geografia.id"], name="fk_trabajo_geografia"
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_territorial_v2_id"],
            ["ejecucion_territorial_v2.id"],
            name="fk_trabajo_territorial_v2",
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_campo_v2_id"], ["ejecucion_campo_v2.id"], name="fk_trabajo_campo_v2"
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_ruteo_v2_id"], ["ejecucion_ruteo_v2.id"], name="fk_trabajo_ruteo_v2"
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_analytics_id"], ["ejecucion_analytics.id"], name="fk_trabajo_analytics"
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_entrenamiento_predictivo_id"],
            ["ejecucion_entrenamiento_predictivo.id"],
            name="fk_trabajo_entrenamiento_predictivo",
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_scoring_predictivo_id"],
            ["ejecucion_scoring_predictivo.id"],
            name="fk_trabajo_scoring_predictivo",
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
        sa.UniqueConstraint("ejecucion_decision_v2_id", name="uq_trabajo_decision_v2"),
        sa.UniqueConstraint("ejecucion_geografia_id", name="uq_trabajo_geografia"),
        sa.UniqueConstraint("ejecucion_territorial_v2_id", name="uq_trabajo_territorial_v2"),
        sa.UniqueConstraint("ejecucion_campo_v2_id", name="uq_trabajo_campo_v2"),
        sa.UniqueConstraint("ejecucion_ruteo_v2_id", name="uq_trabajo_ruteo_v2"),
        sa.UniqueConstraint("ejecucion_analytics_id", name="uq_trabajo_analytics"),
        sa.UniqueConstraint(
            "ejecucion_entrenamiento_predictivo_id", name="uq_trabajo_entrenamiento_predictivo"
        ),
        sa.UniqueConstraint(
            "ejecucion_scoring_predictivo_id", name="uq_trabajo_scoring_predictivo"
        ),
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
    ejecucion_decision_v2_id: int | None = None
    """El objetivo de un trabajo DECISION_V2, desde la 0011."""
    ejecucion_geografia_id: int | None = None
    """El objetivo de un trabajo GEOGRAFIA, desde la 0012."""
    ejecucion_territorial_v2_id: int | None = None
    """El objetivo de un trabajo TERRITORIAL_V2, desde la 0012."""
    ejecucion_campo_v2_id: int | None = None
    """El objetivo de un trabajo CAMPO_V2, desde la 0013."""
    ejecucion_ruteo_v2_id: int | None = None
    """El objetivo de un trabajo RUTEO_V2, desde la 0014."""
    ejecucion_analytics_id: int | None = None
    """El objetivo de un trabajo ANALYTICS, desde la 0015."""
    ejecucion_entrenamiento_predictivo_id: int | None = None
    """El objetivo de un trabajo PREDICTIVE_TRAINING, desde la 0016."""
    ejecucion_scoring_predictivo_id: int | None = None
    """El objetivo de un trabajo PREDICTIVE_SCORING, desde la 0016."""
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
