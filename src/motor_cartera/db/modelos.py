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
    "AND (tipo = 'RUTEO') = (ejecucion_ruteo_id IS NOT NULL)"
)
"""Exactamente un objetivo, el de su tipo: el tipo tiene un solo valor, y cada equivalencia obliga a
que su objetivo exista y a que los otros tres esten vacios."""

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
    """Que motor ejecuta un trabajo, y por tanto cual de sus cuatro objetivos tiene."""

    INGESTA = "INGESTA"
    DECISION = "DECISION"
    TERRITORIAL = "TERRITORIAL"
    RUTEO = "RUTEO"


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

    Cada recurso tiene un solo trabajo: las restricciones unicas de sus cuatro objetivos lo
    garantizan, y una nueva entrega reusa la misma fila.
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
        sa.UniqueConstraint("corrida_id", name="uq_trabajo_corrida"),
        sa.UniqueConstraint("ejecucion_decision_id", name="uq_trabajo_decision"),
        sa.UniqueConstraint("ejecucion_territorial_id", name="uq_trabajo_territorial"),
        sa.UniqueConstraint("ejecucion_ruteo_id", name="uq_trabajo_ruteo"),
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
            TipoTrabajo, name="tipo_trabajo", native_enum=False, create_constraint=True, length=12
        )
    )
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
    """El objetivo de una INGESTA. Existe exactamente uno de los cuatro: el de su tipo."""
    ejecucion_decision_id: int | None = None
    ejecucion_territorial_id: int | None = None
    ejecucion_ruteo_id: int | None = None
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
