"""geografia real con PostGIS: la referencia, geografia/v1, las bases operativas versionadas,
territorial/v2 y sus trabajos GEOGRAFIA y TERRITORIAL_V2

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-11 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

# Como en las demas migraciones, cada nombre se midio contra los 63 caracteres de PostgreSQL y es
# parte del contrato del esquema: sale en los IntegrityError y una migracion futura se refiere a el.
#
# La 0012 habilita PostGIS y solo crea estructuras: no importa ninguna referencia ni resuelve
# ninguna geografia. La referencia se importa con `motor-cartera importar-geografia`; la geografia y
# territorial/v2 se piden por la API o el CLI. Nada de territorial/v1 ni de ruteo/v1 cambia: ni sus
# tablas, ni sus trabajos, ni el flujo que los encadena.

POSTGIS = "CREATE EXTENSION IF NOT EXISTS postgis"
"""PostGIS hace el punto en poligono, las transformaciones y las distancias geodesicas. Bajar la
0012 no la quita: puede haber otra estructura que la use, y quitarla no es de esta migracion."""

TIPOS_0011 = (
    "INGESTA",
    "DECISION",
    "TERRITORIAL",
    "RUTEO",
    "INGESTA_PAGOS",
    "HISTORIA",
    "MOTOR_PAGOS",
    "ATRIBUCION",
    "EVALUACION_PROMESAS",
    "DECISION_V2",
)
TIPOS_0012 = (*TIPOS_0011, "GEOGRAFIA", "TERRITORIAL_V2")
OBJETIVO_0011 = (
    "(tipo = 'INGESTA') = (corrida_id IS NOT NULL) "
    "AND (tipo = 'DECISION') = (ejecucion_decision_id IS NOT NULL) "
    "AND (tipo = 'TERRITORIAL') = (ejecucion_territorial_id IS NOT NULL) "
    "AND (tipo = 'RUTEO') = (ejecucion_ruteo_id IS NOT NULL) "
    "AND (tipo = 'INGESTA_PAGOS') = (ingesta_pagos_id IS NOT NULL) "
    "AND (tipo = 'HISTORIA') = (ejecucion_historia_id IS NOT NULL) "
    "AND (tipo = 'MOTOR_PAGOS') = (ejecucion_motor_pagos_id IS NOT NULL) "
    "AND (tipo = 'ATRIBUCION') = (ejecucion_atribucion_id IS NOT NULL) "
    "AND (tipo = 'EVALUACION_PROMESAS') = (ejecucion_evaluacion_promesas_id IS NOT NULL) "
    "AND (tipo = 'DECISION_V2') = (ejecucion_decision_v2_id IS NOT NULL)"
)
OBJETIVO_0012 = OBJETIVO_0011 + (
    " AND (tipo = 'GEOGRAFIA') = (ejecucion_geografia_id IS NOT NULL) "
    "AND (tipo = 'TERRITORIAL_V2') = (ejecucion_territorial_v2_id IS NOT NULL)"
)

ESTADOS_GEOGRAFIA = (
    "VALIDA",
    "SIN_COORDENADAS",
    "COORDENADAS_INCOMPLETAS",
    "COORDENADAS_INVALIDAS",
    "FUERA_DE_REFERENCIA",
    "MUNICIPIO_AMBIGUO",
    "DISCREPANCIA_MUNICIPIO",
)
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
OBSERVADAS_DE_LA_GEOGRAFIA = (
    "(estado_geografia = 'SIN_COORDENADAS') = "
    "(latitud_observada IS NULL AND longitud_observada IS NULL) "
    "AND (estado_geografia = 'COORDENADAS_INCOMPLETAS') = "
    "((latitud_observada IS NULL) <> (longitud_observada IS NULL))"
)
CONTEOS_DE_GEOGRAFIA = (
    "validas",
    "sin_coordenadas",
    "incompletas",
    "invalidas",
    "fuera_cobertura",
    "ambiguas",
    "discrepantes",
)
_SUMA_DE_GEOGRAFIA = " + ".join(CONTEOS_DE_GEOGRAFIA)
PUBLICACION_DE_GEOGRAFIA = (
    f"(estado = 'EXITOSA' AND cuentas_objetivo > 0 AND {_SUMA_DE_GEOGRAFIA} = cuentas_objetivo) "
    f"OR (estado <> 'EXITOSA' AND {_SUMA_DE_GEOGRAFIA} = 0)"
)
ESTADOS_ASIGNACION = (
    "ASIGNADA",
    "REVISION_GEOGRAFICA",
    "FUERA_DE_COBERTURA",
    "SIN_BASE_ELEGIBLE",
    "AMBIGUA_BASE",
)
COHERENCIA_DE_LA_ASIGNACION = (
    "estado_asignacion IN ("
    + ", ".join(f"'{e}'" for e in ESTADOS_ASIGNACION)
    + ") AND (base_operativa_id IS NOT NULL) = (estado_asignacion = 'ASIGNADA') "
    "AND (distancia_base_m IS NOT NULL) = (estado_asignacion = 'ASIGNADA') "
    "AND (distancia_base_m IS NULL OR distancia_base_m >= 0) "
    "AND (municipio_geografico_id IS NOT NULL) = "
    "(estado_asignacion IN ('ASIGNADA', 'SIN_BASE_ELEGIBLE', 'AMBIGUA_BASE'))"
)
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
PATRON_CODIGO_BASE = "^[A-Z0-9_-]{1,32}$"

INMUTABLES = {
    "geografia_inmutable": (
        ("municipio_geografico", "resultado_geografia_cuenta"),
        "geografia/v1 no se reescribe",
        "Una referencia o una geografia publicada no cambia: se importa otra version o se pide "
        "otra ejecucion.",
    ),
    "bases_operativas_inmutables": (
        ("version_bases_operativas", "base_operativa", "cobertura_base_municipio"),
        "Una version de bases operativas no se reescribe",
        "Cambiar una base es publicar otra version: las ejecuciones viejas siguen con la suya.",
    ),
    "territorial_v2_inmutable": (
        ("asignacion_territorial_v2",),
        "territorial/v2 no se reescribe",
        "Una asignacion historica no cambia: se pide otra ejecucion, con otras entradas.",
    ),
}
"""Lo publicado: un trigger por sentencia rechaza cualquier UPDATE o DELETE sobre estas tablas, con
restrict_violation. TRUNCATE no pasa por aqui: es una operacion de administracion, no de la
aplicacion."""


def _funcion_inmutable(nombre: str, mensaje: str, pista: str) -> str:
    return f"""
CREATE FUNCTION {nombre}() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '{mensaje}: % sobre % no se permite.', TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation', CONSTRAINT = 'tr_{nombre}',
              HINT = '{pista}';
END
$$
"""


VERSION_TERMINADA = """
CREATE FUNCTION version_geografia_terminada() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' OR OLD.estado <> 'EN_PROCESO' THEN
        RAISE EXCEPTION 'Una version de la geografia terminada no cambia: % sobre % no se permite.',
            TG_OP, TG_TABLE_NAME
            USING ERRCODE = 'restrict_violation', CONSTRAINT = 'tr_version_geografia_terminada',
                  HINT = 'Si el proveedor publica otra edicion, se importa otra version.';
    END IF;
    RETURN NEW;
END
$$
"""
"""Una version de la referencia solo cambia mientras se importa (EN_PROCESO): al publicarse o al
fallar ya no cambia, y ninguna se borra."""

# Bajar a la 0011 quita la geografia, las bases y territorial/v2 enteros: no se reconstruyen solos
# (se vuelve a importar la referencia, a cargar las bases y a pedir las ejecuciones, y dan lo
# mismo). Lo que esta en curso se cierra primero. Nada de v0.10 se toca: ni decision/v2, ni el
# lifecycle, ni los pagos, ni la historia, ni la cartera, ni los artefactos, ni territorial/v1 o
# ruteo/v1.
CIERRE = (
    "Cerrada al bajar la base a la 0011: la v0.10 no tiene geografia ni territorial/v2. "
    "territorial/v1, ruteo/v1 y todo lo demas siguen intactos."
)
CERRAR_GEOGRAFIAS = sa.text(
    "UPDATE ejecucion_geografia SET estado = 'FALLIDA', resultado = 'CERRADA_AL_BAJAR', "
    + ", ".join(f"{c} = 0" for c in CONTEOS_DE_GEOGRAFIA)
    + ", terminada_en = now(), detalle = :motivo WHERE estado = 'EN_PROCESO'"
).bindparams(motivo=CIERRE)
CERRAR_TERRITORIALES = sa.text(
    "UPDATE ejecucion_territorial_v2 SET estado = 'FALLIDA', resultado = 'CERRADA_AL_BAJAR', "
    + ", ".join(f"{c} = 0" for c in CONTEOS_DE_TERRITORIAL_V2)
    + ", terminada_en = now(), detalle = :motivo WHERE estado = 'EN_PROCESO'"
).bindparams(motivo=CIERRE)
CERRAR_TRABAJOS = sa.text(
    "UPDATE trabajo_orquestacion SET estado = 'FALLIDO', worker_id = NULL, lease_hasta = NULL, "
    "terminado_en = now(), ultimo_error = :motivo "
    "WHERE tipo IN ('GEOGRAFIA', 'TERRITORIAL_V2') AND estado IN ('PENDIENTE', 'EJECUTANDO')"
).bindparams(motivo=CIERRE)
"""Lo primero que hace bajar la 0012: cerrar las ejecuciones en curso y sus trabajos, antes de
quitar sus tablas."""


class _Geometria(sa.types.UserDefinedType):
    """La columna geometry de PostGIS, con su tipo y su SRID. Propia de esta migracion, para que no
    cambie si el codigo cambia despues."""

    cache_ok = True

    def __init__(self, tipo: str) -> None:
        self.tipo = tipo

    def get_col_spec(self, **_: object) -> str:
        return f"geometry({self.tipo},4326)"


def _tipos(tipos: tuple[str, ...]) -> str:
    return "tipo IN (" + ", ".join(f"'{t}'" for t in tipos) + ")"


def _rehacer_check(nombre: str, condicion: str) -> None:
    op.drop_constraint(op.f(nombre), "trabajo_orquestacion", type_="check")
    op.create_check_constraint(op.f(nombre), "trabajo_orquestacion", condicion)


def _texto(nombre: str, largo: int | None = None, nullable: bool = True) -> sa.Column:
    return sa.Column(nombre, sqlmodel.sql.sqltypes.AutoString(length=largo), nullable=nullable)


def _vocabulario(nombre: str, largo: int, *valores: str) -> sa.Enum:
    return sa.Enum(*valores, name=nombre, native_enum=False, create_constraint=True, length=largo)


def _conteos(nombres: tuple[str, ...]) -> list[sa.Column]:
    return [sa.Column(nombre, sa.BigInteger(), nullable=False) for nombre in nombres]


def upgrade() -> None:
    op.execute(sa.text(POSTGIS))
    _referencia()
    _geografia()
    _bases()
    _territorial()
    _inmutables()
    _cola()


def _referencia() -> None:
    op.create_table(
        "version_geografia",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("geografia_version_id", sa.Uuid(), nullable=False),
        _texto("proveedor", 64, nullable=False),
        _texto("producto", 200, nullable=False),
        _texto("edicion", 64, nullable=False),
        sa.Column("nivel", _vocabulario("nivel_geografia", 16, "MUNICIPAL"), nullable=False),
        _texto("capa", 255, nullable=False),
        _texto("archivo", 255, nullable=False),
        _texto("sha256", 64, nullable=False),
        sa.Column("tamano_bytes", sa.BigInteger(), nullable=False),
        sa.Column("srid", sa.Integer(), nullable=False),
        sa.Column("srid_origen", sa.Integer(), nullable=False),
        sa.Column(
            "estado",
            _vocabulario("estado_version_geografia", 12, "EN_PROCESO", "PUBLICADA", "FALLIDA"),
            nullable=False,
        ),
        sa.Column("municipios", sa.Integer(), nullable=False),
        sa.Column("reparadas", sa.Integer(), nullable=False),
        sa.Column("metadatos", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("importada_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("terminada_en", sa.DateTime(timezone=True), nullable=True),
        _texto("detalle"),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_version_geografia_sha256")),
        sa.CheckConstraint(
            "srid = 4326 AND srid_origen > 0 AND tamano_bytes > 0",
            name=op.f("ck_version_geografia_origen"),
        ),
        sa.CheckConstraint(
            "municipios >= 0 AND reparadas >= 0 AND reparadas <= municipios "
            "AND (estado = 'PUBLICADA') = (municipios > 0)",
            name=op.f("ck_version_geografia_conteos"),
        ),
        sa.CheckConstraint(
            "(estado = 'EN_PROCESO') = (terminada_en IS NULL)",
            name=op.f("ck_version_geografia_terminada"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_version_geografia")),
        sa.UniqueConstraint(
            "geografia_version_id", name=op.f("uq_version_geografia_geografia_version_id")
        ),
    )
    for indice, estado in (
        ("ux_version_geografia_publicada", "PUBLICADA"),
        ("ux_version_geografia_en_proceso", "EN_PROCESO"),
    ):
        op.create_index(
            indice,
            "version_geografia",
            ["sha256", "capa"],
            unique=True,
            postgresql_where=sa.text(f"estado = '{estado}'"),
        )
    op.create_table(
        "municipio_geografico",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("version_geografia_id", sa.Integer(), nullable=False),
        sa.Column("registro_origen", sa.Integer(), nullable=False),
        _texto("cve_ent", 2, nullable=False),
        _texto("cve_mun", 3, nullable=False),
        _texto("cvegeo", 5, nullable=False),
        _texto("nombre_oficial", 200, nullable=False),
        sa.Column("reparada", sa.Boolean(), nullable=False),
        sa.Column("geometria", _Geometria("MULTIPOLYGON"), nullable=False),
        sa.CheckConstraint(
            "cve_ent ~ '^[0-9]{2}$' AND cve_mun ~ '^[0-9]{3}$' AND cvegeo = cve_ent || cve_mun",
            name=op.f("ck_municipio_claves"),
        ),
        sa.CheckConstraint(
            "ST_IsValid(geometria) AND NOT ST_IsEmpty(geometria)",
            name=op.f("ck_municipio_geometria"),
        ),
        sa.ForeignKeyConstraint(
            ["version_geografia_id"], ["version_geografia.id"], name="fk_municipio_version"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_municipio_geografico")),
        sa.UniqueConstraint("version_geografia_id", "cvegeo", name="uq_municipio_version_cvegeo"),
    )
    op.create_index(
        "ix_municipio_geografico_geometria",
        "municipio_geografico",
        ["geometria"],
        postgresql_using="gist",
    )


def _geografia() -> None:
    op.create_table(
        "ejecucion_geografia",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("geografia_run_id", sa.Uuid(), nullable=False),
        sa.Column("corte_canonico_id", sa.Integer(), nullable=False),
        sa.Column("dataset_conformado_id", sa.Integer(), nullable=False),
        sa.Column("version_geografia_id", sa.Integer(), nullable=False),
        _texto("version_motor", 32, nullable=False),
        _texto("firma_inputs", 64, nullable=False),
        sa.Column(
            "estado",
            _vocabulario("estado_geografia_ejecucion", 12, "EN_PROCESO", "EXITOSA", "FALLIDA"),
            nullable=False,
        ),
        _texto("resultado", 40),
        sa.Column("cuentas_objetivo", sa.BigInteger(), nullable=False),
        *_conteos(CONTEOS_DE_GEOGRAFIA),
        sa.Column("distribucion", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("iniciada_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("terminada_en", sa.DateTime(timezone=True), nullable=True),
        _texto("detalle"),
        sa.CheckConstraint("firma_inputs ~ '^[0-9a-f]{64}$'", name=op.f("ck_geografia_firma")),
        sa.CheckConstraint(
            "(estado = 'EN_PROCESO') = (resultado IS NULL)", name=op.f("ck_geografia_resultado")
        ),
        sa.CheckConstraint(
            "cuentas_objetivo >= 0 AND " + " AND ".join(f"{c} >= 0" for c in CONTEOS_DE_GEOGRAFIA),
            name=op.f("ck_geografia_conteos"),
        ),
        sa.CheckConstraint(PUBLICACION_DE_GEOGRAFIA, name=op.f("ck_geografia_publicacion")),
        sa.ForeignKeyConstraint(
            ["corte_canonico_id"], ["corte_canonico.id"], name="fk_geografia_corte"
        ),
        sa.ForeignKeyConstraint(
            ["dataset_conformado_id"], ["dataset_conformado.id"], name="fk_geografia_dataset"
        ),
        sa.ForeignKeyConstraint(
            ["version_geografia_id"], ["version_geografia.id"], name="fk_geografia_version"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ejecucion_geografia")),
        sa.UniqueConstraint(
            "geografia_run_id", name=op.f("uq_ejecucion_geografia_geografia_run_id")
        ),
        sa.UniqueConstraint("id", "corte_canonico_id", name="uq_geografia_id_corte"),
    )
    for indice, estado in (
        ("ux_geografia_exitosa", "EXITOSA"),
        ("ux_geografia_en_proceso", "EN_PROCESO"),
    ):
        op.create_index(
            indice,
            "ejecucion_geografia",
            ["firma_inputs"],
            unique=True,
            postgresql_where=sa.text(f"estado = '{estado}'"),
        )
    op.create_index("ix_geografia_corte", "ejecucion_geografia", ["corte_canonico_id", "id"])

    # La geografia de cada cuenta, sin id propio: su llave es la natural. Las columnas de 8 bytes
    # primero.
    op.create_table(
        "resultado_geografia_cuenta",
        sa.Column("latitud_observada", sa.Double(), nullable=True),
        sa.Column("longitud_observada", sa.Double(), nullable=True),
        sa.Column("source_row", sa.BigInteger(), nullable=False),
        sa.Column("ejecucion_geografia_id", sa.Integer(), nullable=False),
        sa.Column("cuenta_canonica_id", sa.Integer(), nullable=False),
        sa.Column("corte_canonico_id", sa.Integer(), nullable=False),
        sa.Column("municipio_geografico_id", sa.Integer(), nullable=True),
        _texto("estado_geografia", 24, nullable=False),
        _texto("municipio_fuente_resuelto", 5),
        sa.Column("coincide_municipio", sa.Boolean(), nullable=True),
        sa.Column("punto", _Geometria("POINT"), nullable=True),
        _texto("source_sheet", 255),
        sa.Column("motivos", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(COHERENCIA_DE_LA_GEOGRAFIA, name=op.f("ck_resultado_geografia_estado")),
        sa.CheckConstraint(
            OBSERVADAS_DE_LA_GEOGRAFIA, name=op.f("ck_resultado_geografia_observadas")
        ),
        sa.CheckConstraint(
            f"municipio_fuente_resuelto IS NULL OR municipio_fuente_resuelto ~ '{PATRON_CVEGEO}'",
            name=op.f("ck_resultado_geografia_fuente"),
        ),
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
        sa.PrimaryKeyConstraint(
            "ejecucion_geografia_id",
            "cuenta_canonica_id",
            name=op.f("pk_resultado_geografia_cuenta"),
        ),
    )
    for indice, columnas in (
        (
            "ix_resultado_geografia_estado",
            ["ejecucion_geografia_id", "estado_geografia", "cuenta_canonica_id"],
        ),
        (
            "ix_resultado_geografia_municipio",
            ["ejecucion_geografia_id", "municipio_geografico_id", "cuenta_canonica_id"],
        ),
        ("ix_resultado_geografia_cuenta", ["cuenta_canonica_id", "ejecucion_geografia_id"]),
    ):
        op.create_index(indice, "resultado_geografia_cuenta", columnas)


def _bases() -> None:
    op.create_table(
        "version_bases_operativas",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("bases_version_id", sa.Uuid(), nullable=False),
        _texto("nombre", 120, nullable=False),
        _texto("version_configuracion", 32, nullable=False),
        _texto("hash_configuracion", 64, nullable=False),
        sa.Column("estado", _vocabulario("estado_version_bases", 12, "PUBLICADA"), nullable=False),
        sa.Column("bases", sa.Integer(), nullable=False),
        sa.Column("activas", sa.Integer(), nullable=False),
        sa.Column("coberturas", sa.Integer(), nullable=False),
        sa.Column("creada_en", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "hash_configuracion ~ '^[0-9a-f]{64}$'", name=op.f("ck_version_bases_hash")
        ),
        sa.CheckConstraint(
            "bases > 0 AND activas >= 0 AND activas <= bases AND coberturas >= 0",
            name=op.f("ck_version_bases_conteos"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_version_bases_operativas")),
        sa.UniqueConstraint(
            "bases_version_id", name=op.f("uq_version_bases_operativas_bases_version_id")
        ),
        sa.UniqueConstraint("hash_configuracion", name="uq_version_bases_hash"),
    )
    op.create_table(
        "base_operativa",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("base_id", sa.Uuid(), nullable=False),
        sa.Column("version_bases_operativas_id", sa.Integer(), nullable=False),
        _texto("codigo", 32, nullable=False),
        _texto("nombre", 120, nullable=False),
        sa.Column("activa", sa.Boolean(), nullable=False),
        sa.Column("punto", _Geometria("POINT"), nullable=False),
        sa.CheckConstraint(f"codigo ~ '{PATRON_CODIGO_BASE}'", name=op.f("ck_base_codigo")),
        sa.ForeignKeyConstraint(
            ["version_bases_operativas_id"],
            ["version_bases_operativas.id"],
            name="fk_base_version",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_base_operativa")),
        sa.UniqueConstraint("base_id", name=op.f("uq_base_operativa_base_id")),
        sa.UniqueConstraint("version_bases_operativas_id", "codigo", name="uq_base_version_codigo"),
        sa.UniqueConstraint("id", "version_bases_operativas_id", name="uq_base_id_version"),
    )
    op.create_table(
        "cobertura_base_municipio",
        sa.Column("base_operativa_id", sa.Integer(), nullable=False),
        _texto("cvegeo", 5, nullable=False),
        sa.CheckConstraint(f"cvegeo ~ '{PATRON_CVEGEO}'", name=op.f("ck_cobertura_cvegeo")),
        sa.ForeignKeyConstraint(
            ["base_operativa_id"], ["base_operativa.id"], name="fk_cobertura_base"
        ),
        sa.PrimaryKeyConstraint(
            "base_operativa_id", "cvegeo", name=op.f("pk_cobertura_base_municipio")
        ),
    )
    op.create_index(
        "ix_cobertura_cvegeo", "cobertura_base_municipio", ["cvegeo", "base_operativa_id"]
    )


def _territorial() -> None:
    op.create_table(
        "ejecucion_territorial_v2",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("territorial_run_id", sa.Uuid(), nullable=False),
        sa.Column("corte_canonico_id", sa.Integer(), nullable=False),
        sa.Column("ejecucion_geografia_id", sa.Integer(), nullable=False),
        sa.Column("version_bases_operativas_id", sa.Integer(), nullable=False),
        _texto("version_motor", 32, nullable=False),
        _texto("version_politica", 40, nullable=False),
        _texto("hash_politica", 64, nullable=False),
        _texto("firma_inputs", 64, nullable=False),
        sa.Column(
            "estado",
            _vocabulario("estado_territorial_v2", 12, "EN_PROCESO", "EXITOSA", "FALLIDA"),
            nullable=False,
        ),
        _texto("resultado", 40),
        sa.Column("cuentas_objetivo", sa.BigInteger(), nullable=False),
        *_conteos(CONTEOS_DE_TERRITORIAL_V2),
        sa.Column("distribucion", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("iniciada_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("terminada_en", sa.DateTime(timezone=True), nullable=True),
        _texto("detalle"),
        sa.CheckConstraint(
            "firma_inputs ~ '^[0-9a-f]{64}$' AND hash_politica ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_territorial_v2_firmas"),
        ),
        sa.CheckConstraint(
            "(estado = 'EN_PROCESO') = (resultado IS NULL)",
            name=op.f("ck_territorial_v2_resultado"),
        ),
        sa.CheckConstraint(
            "cuentas_objetivo >= 0 AND "
            + " AND ".join(f"{c} >= 0" for c in CONTEOS_DE_TERRITORIAL_V2),
            name=op.f("ck_territorial_v2_conteos"),
        ),
        sa.CheckConstraint(
            PUBLICACION_DE_TERRITORIAL_V2, name=op.f("ck_territorial_v2_publicacion")
        ),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ejecucion_territorial_v2")),
        sa.UniqueConstraint(
            "territorial_run_id", name=op.f("uq_ejecucion_territorial_v2_territorial_run_id")
        ),
        sa.UniqueConstraint(
            "id",
            "ejecucion_geografia_id",
            "version_bases_operativas_id",
            name="uq_territorial_v2_entradas",
        ),
    )
    for indice, estado in (
        ("ux_territorial_v2_exitosa", "EXITOSA"),
        ("ux_territorial_v2_en_proceso", "EN_PROCESO"),
    ):
        op.create_index(
            indice,
            "ejecucion_territorial_v2",
            ["firma_inputs"],
            unique=True,
            postgresql_where=sa.text(f"estado = '{estado}'"),
        )
    op.create_index(
        "ix_territorial_v2_corte", "ejecucion_territorial_v2", ["corte_canonico_id", "id"]
    )

    op.create_table(
        "asignacion_territorial_v2",
        sa.Column("distancia_base_m", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("ejecucion_territorial_v2_id", sa.Integer(), nullable=False),
        sa.Column("cuenta_canonica_id", sa.Integer(), nullable=False),
        sa.Column("ejecucion_geografia_id", sa.Integer(), nullable=False),
        sa.Column("version_bases_operativas_id", sa.Integer(), nullable=False),
        sa.Column("municipio_geografico_id", sa.Integer(), nullable=True),
        sa.Column("base_operativa_id", sa.Integer(), nullable=True),
        _texto("estado_asignacion", 20, nullable=False),
        sa.Column("motivos", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(COHERENCIA_DE_LA_ASIGNACION, name=op.f("ck_asignacion_v2_estado")),
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
        sa.PrimaryKeyConstraint(
            "ejecucion_territorial_v2_id",
            "cuenta_canonica_id",
            name=op.f("pk_asignacion_territorial_v2"),
        ),
    )
    for indice, columnas in (
        (
            "ix_asignacion_v2_base",
            ["ejecucion_territorial_v2_id", "base_operativa_id", "cuenta_canonica_id"],
        ),
        (
            "ix_asignacion_v2_estado",
            ["ejecucion_territorial_v2_id", "estado_asignacion", "cuenta_canonica_id"],
        ),
        (
            "ix_asignacion_v2_municipio",
            ["ejecucion_territorial_v2_id", "municipio_geografico_id", "cuenta_canonica_id"],
        ),
        ("ix_asignacion_v2_cuenta", ["cuenta_canonica_id", "ejecucion_territorial_v2_id"]),
    ):
        op.create_index(indice, "asignacion_territorial_v2", columnas)


def _inmutables() -> None:
    for funcion, (tablas, mensaje, pista) in INMUTABLES.items():
        op.execute(sa.text(_funcion_inmutable(funcion, mensaje, pista)))
        for tabla in tablas:
            op.execute(
                sa.text(
                    f"CREATE TRIGGER tr_{funcion} BEFORE UPDATE OR DELETE ON {tabla} "
                    f"FOR EACH STATEMENT EXECUTE FUNCTION {funcion}()"
                )
            )
    op.execute(sa.text(VERSION_TERMINADA))
    op.execute(
        sa.text(
            "CREATE TRIGGER tr_version_geografia_terminada BEFORE UPDATE OR DELETE ON "
            "version_geografia FOR EACH ROW EXECUTE FUNCTION version_geografia_terminada()"
        )
    )


def _cola() -> None:
    # Los trabajos GEOGRAFIA y TERRITORIAL_V2, en la misma cola durable: uno por ejecucion. Los dos
    # caben en los 24 caracteres de tipo; solo se rehacen los CHECK del tipo y del objetivo.
    for columna, tabla, sufijo in (
        ("ejecucion_geografia_id", "ejecucion_geografia", "geografia"),
        ("ejecucion_territorial_v2_id", "ejecucion_territorial_v2", "territorial_v2"),
    ):
        op.add_column("trabajo_orquestacion", sa.Column(columna, sa.Integer(), nullable=True))
        op.create_foreign_key(
            f"fk_trabajo_{sufijo}", "trabajo_orquestacion", tabla, [columna], ["id"]
        )
        op.create_unique_constraint(f"uq_trabajo_{sufijo}", "trabajo_orquestacion", [columna])
    _rehacer_check("ck_trabajo_orquestacion_tipo_trabajo", _tipos(TIPOS_0012))
    _rehacer_check("ck_trabajo_objetivo", OBJETIVO_0012)


def downgrade() -> None:
    # Primero se cierra lo que estaba en curso, en este orden: las ejecuciones (si un worker tiene
    # una bloqueada, termina antes de que el UPDATE la tome) y despues sus trabajos. Despues se van
    # esos trabajos, que la 0011 no sabria guardar, y las estructuras de la 0012. PostGIS se queda.
    op.execute(CERRAR_GEOGRAFIAS)
    op.execute(CERRAR_TERRITORIALES)
    op.execute(CERRAR_TRABAJOS)
    op.execute(
        sa.text("DELETE FROM trabajo_orquestacion WHERE tipo IN ('GEOGRAFIA', 'TERRITORIAL_V2')")
    )
    _rehacer_check("ck_trabajo_objetivo", OBJETIVO_0011)
    _rehacer_check("ck_trabajo_orquestacion_tipo_trabajo", _tipos(TIPOS_0011))
    for columna, sufijo in (
        ("ejecucion_territorial_v2_id", "territorial_v2"),
        ("ejecucion_geografia_id", "geografia"),
    ):
        op.drop_constraint(f"uq_trabajo_{sufijo}", "trabajo_orquestacion", type_="unique")
        op.drop_constraint(f"fk_trabajo_{sufijo}", "trabajo_orquestacion", type_="foreignkey")
        op.drop_column("trabajo_orquestacion", columna)

    for indice in (
        "ix_asignacion_v2_cuenta",
        "ix_asignacion_v2_municipio",
        "ix_asignacion_v2_estado",
        "ix_asignacion_v2_base",
    ):
        op.drop_index(indice, table_name="asignacion_territorial_v2")
    op.drop_table("asignacion_territorial_v2")
    op.drop_index("ix_territorial_v2_corte", table_name="ejecucion_territorial_v2")
    for indice, estado in (
        ("ux_territorial_v2_en_proceso", "EN_PROCESO"),
        ("ux_territorial_v2_exitosa", "EXITOSA"),
    ):
        op.drop_index(
            indice,
            table_name="ejecucion_territorial_v2",
            postgresql_where=sa.text(f"estado = '{estado}'"),
        )
    op.drop_table("ejecucion_territorial_v2")

    op.drop_index("ix_cobertura_cvegeo", table_name="cobertura_base_municipio")
    op.drop_table("cobertura_base_municipio")
    op.drop_table("base_operativa")
    op.drop_table("version_bases_operativas")

    for indice in (
        "ix_resultado_geografia_cuenta",
        "ix_resultado_geografia_municipio",
        "ix_resultado_geografia_estado",
    ):
        op.drop_index(indice, table_name="resultado_geografia_cuenta")
    op.drop_table("resultado_geografia_cuenta")
    op.drop_index("ix_geografia_corte", table_name="ejecucion_geografia")
    for indice, estado in (
        ("ux_geografia_en_proceso", "EN_PROCESO"),
        ("ux_geografia_exitosa", "EXITOSA"),
    ):
        op.drop_index(
            indice,
            table_name="ejecucion_geografia",
            postgresql_where=sa.text(f"estado = '{estado}'"),
        )
    op.drop_table("ejecucion_geografia")

    op.drop_index("ix_municipio_geografico_geometria", table_name="municipio_geografico")
    op.drop_table("municipio_geografico")
    for indice, estado in (
        ("ux_version_geografia_en_proceso", "EN_PROCESO"),
        ("ux_version_geografia_publicada", "PUBLICADA"),
    ):
        op.drop_index(
            indice,
            table_name="version_geografia",
            postgresql_where=sa.text(f"estado = '{estado}'"),
        )
    op.drop_table("version_geografia")

    for funcion in (*INMUTABLES, "version_geografia_terminada"):
        op.execute(sa.text(f"DROP FUNCTION {funcion}()"))
