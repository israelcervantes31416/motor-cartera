"""fuentes oficiales y evidencia inmutable: artefactos, cartera/v2, pagos/v1 y conformados

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-05 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

# Como en las demas migraciones, cada nombre se midio contra los 63 caracteres de PostgreSQL y es
# parte del contrato del esquema: sale en los IntegrityError y una migracion futura se refiere a el.

CLAVE_DEL_CONTENIDO = "storage_key = 'sha256/' || substr(sha256, 1, 2) || '/' || sha256"

# Un despacho y una cartera. Las corridas de antes de la 0007 no registraban ninguno, pero el
# sistema siempre fue de un solo despacho y una sola cartera: se les asignan los identificadores por
# omision de la configuracion. No es un supuesto sobre los datos, es la definicion del alcance. Las
# columnas quedan sin valor por omision en la base: cada corrida nueva trae el suyo.
METADATA_DEL_SISTEMA = {"despacho_id": "DSP_001", "cartera_id": "CARTERA_PRINCIPAL"}

# Bajar a la 0006 deja sin almacen de artefactos a la v0.5, que lee el archivo de archivo_corrida.
# Una corrida que sigue EN_PROCESO con su archivo solo en el almacen no se podria procesar: se
# cierra FALLIDA con el motivo, con su trabajo FALLIDO y su flujo DETENIDO, como la 0006 cerro lo
# que nadie iba a terminar. Los objetos del almacen no se borran: Alembic no gobierna el sistema de
# archivos.
CIERRE = (
    "Cerrada al bajar la base a la 0006: su archivo vive en el almacen de artefactos, que la v0.5 "
    "no conoce. El archivo sigue en el almacen; para reintentar, vuelve a subirlo."
)
# cartera/v2: la fecha de corte es metadata del lote y el archivo siempre esta en el almacen; se
# proyecta a Cuenta con una version de la proyeccion, que cartera/v1 no tiene. Las corridas de antes
# son de cartera/v1 o 'sin-registro': cumplen las dos sin que se toque ninguna.
V2_CON_FUENTE = (
    "version_contrato <> 'cartera/v2' "
    "OR (artefacto_fuente_id IS NOT NULL AND fecha_corte IS NOT NULL)"
)
PROYECCION = "(version_contrato = 'cartera/v2') = (version_proyeccion IS NOT NULL)"

# pagos/v1 tiene su propia ingesta y su propio trabajo en la cola. INGESTA_PAGOS no cabe en los
# 12 caracteres de tipo, que pasa a 16; los CHECK del tipo y del objetivo se rehacen con el nuevo.
TIPOS_0006 = ("INGESTA", "DECISION", "TERRITORIAL", "RUTEO")
TIPOS_0007 = (*TIPOS_0006, "INGESTA_PAGOS")
OBJETIVO_0006 = (
    "(tipo = 'INGESTA') = (corrida_id IS NOT NULL) "
    "AND (tipo = 'DECISION') = (ejecucion_decision_id IS NOT NULL) "
    "AND (tipo = 'TERRITORIAL') = (ejecucion_territorial_id IS NOT NULL) "
    "AND (tipo = 'RUTEO') = (ejecucion_ruteo_id IS NOT NULL)"
)
OBJETIVO_0007 = OBJETIVO_0006 + " AND (tipo = 'INGESTA_PAGOS') = (ingesta_pagos_id IS NOT NULL)"
CONTEOS_DE_PAGOS = (
    "filas_leidas >= 0 AND filas_validas >= 0 AND filas_rechazadas >= 0 "
    "AND filas_leidas = filas_validas + filas_rechazadas"
)


def _tipos(tipos: tuple[str, ...]) -> str:
    return "tipo IN (" + ", ".join(f"'{t}'" for t in tipos) + ")"


def _rehacer_check(nombre: str, condicion: str) -> None:
    op.drop_constraint(op.f(nombre), "trabajo_orquestacion", type_="check")
    op.create_check_constraint(op.f(nombre), "trabajo_orquestacion", condicion)


SOLO_EN_EL_ALMACEN = (
    "SELECT c.id FROM corrida c WHERE c.estado = 'EN_PROCESO' "
    "AND c.artefacto_fuente_id IS NOT NULL "
    "AND NOT EXISTS (SELECT 1 FROM archivo_corrida a WHERE a.corrida_id = c.id)"
)


def upgrade() -> None:
    op.create_table(
        "artefacto_fuente",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("artifact_id", sa.Uuid(), nullable=False),
        sa.Column("sha256", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("tamano_bytes", sa.BigInteger(), nullable=False),
        sa.Column("nombre_original", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column(
            "formato",
            sa.Enum(
                "xlsx",
                "csv",
                "zip",
                "parquet",
                name="formato_artefacto",
                native_enum=False,
                create_constraint=True,
                length=12,
            ),
            nullable=False,
        ),
        sa.Column("media_type", sqlmodel.sql.sqltypes.AutoString(length=100), nullable=True),
        sa.Column("storage_key", sqlmodel.sql.sqltypes.AutoString(length=80), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_artefacto_sha256")),
        sa.CheckConstraint("tamano_bytes > 0", name=op.f("ck_artefacto_tamano_positivo")),
        sa.CheckConstraint(CLAVE_DEL_CONTENIDO, name=op.f("ck_artefacto_storage_key")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_artefacto_fuente")),
        sa.UniqueConstraint("artifact_id", name=op.f("uq_artefacto_fuente_artifact_id")),
        sa.UniqueConstraint("sha256", name=op.f("uq_artefacto_fuente_sha256")),
        sa.UniqueConstraint("storage_key", name=op.f("uq_artefacto_fuente_storage_key")),
    )

    # Cada corrida nueva apunta a su artefacto. Las de antes no tienen: su archivo se borraba al
    # terminar la ingesta, o sigue en archivo_corrida si estaba en la cola al migrar.
    op.add_column("corrida", sa.Column("artefacto_fuente_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f("fk_corrida_artefacto_fuente_id_artefacto_fuente"),
        "corrida",
        "artefacto_fuente",
        ["artefacto_fuente_id"],
        ["id"],
    )
    op.create_index(
        op.f("ix_corrida_artefacto_fuente_id"), "corrida", ["artefacto_fuente_id"], unique=False
    )
    for columna, valor in METADATA_DEL_SISTEMA.items():
        op.add_column(
            "corrida",
            sa.Column(
                columna,
                sqlmodel.sql.sqltypes.AutoString(length=32),
                nullable=False,
                server_default=valor,
            ),
        )
        op.alter_column("corrida", columna, server_default=None)
    op.add_column(
        "corrida",
        sa.Column("version_proyeccion", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=True),
    )
    op.create_check_constraint(op.f("ck_corrida_v2_fuente"), "corrida", V2_CON_FUENTE)
    op.create_check_constraint(op.f("ck_corrida_proyeccion"), "corrida", PROYECCION)

    # La ingesta de pagos/v1: su propia entidad, con su artefacto y sus rechazos. No es una corrida.
    op.create_table(
        "ingesta_pagos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pagos_run_id", sa.Uuid(), nullable=False),
        sa.Column("artefacto_fuente_id", sa.Integer(), nullable=False),
        sa.Column("origen", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("firma", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("version_contrato", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column(
            "estado",
            sa.Enum(
                "EN_PROCESO",
                "EXITOSA",
                "RECHAZADA",
                "FALLIDA",
                name="estado_ingesta_pagos",
                native_enum=False,
                create_constraint=True,
                length=12,
            ),
            nullable=False,
        ),
        sa.Column("despacho_id", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("cartera_id", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("tolerancia_rechazo", sa.Float(), nullable=False),
        sa.Column("iniciada_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("terminada_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("filas_leidas", sa.BigInteger(), nullable=False),
        sa.Column("filas_validas", sa.BigInteger(), nullable=False),
        sa.Column("filas_rechazadas", sa.BigInteger(), nullable=False),
        sa.Column("firma_contenido", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
        sa.Column("detalle", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.CheckConstraint(CONTEOS_DE_PAGOS, name=op.f("ck_pagos_conteos")),
        sa.ForeignKeyConstraint(
            ["artefacto_fuente_id"], ["artefacto_fuente.id"], name="fk_pagos_artefacto"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingesta_pagos")),
        sa.UniqueConstraint("pagos_run_id", name=op.f("uq_ingesta_pagos_pagos_run_id")),
    )
    op.create_index(
        op.f("ix_ingesta_pagos_artefacto_fuente_id"),
        "ingesta_pagos",
        ["artefacto_fuente_id"],
        unique=False,
    )
    # El mismo archivo se acepta una vez, y se procesa a lo mas en una ingesta a la vez.
    for indice, estado in (
        ("ux_ingesta_pagos_firma_publicada", "EXITOSA"),
        ("ux_ingesta_pagos_firma_en_proceso", "EN_PROCESO"),
    ):
        op.create_index(
            indice,
            "ingesta_pagos",
            ["firma"],
            unique=True,
            postgresql_where=sa.text(f"estado = '{estado}'"),
        )
    op.create_table(
        "rechazo_pago",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ingesta_pagos_id", sa.Integer(), nullable=False),
        sa.Column("fila", sa.Integer(), nullable=False),
        sa.Column("valores", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("motivos", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["ingesta_pagos_id"], ["ingesta_pagos.id"], name="fk_rechazo_pago_ingesta"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rechazo_pago")),
        sa.UniqueConstraint("ingesta_pagos_id", "fila", name="uq_rechazo_pago_fila"),
    )

    # El dataset conformado que publico cada ingesta, de cartera o de pagos: su Parquet y de que
    # artefacto original salio.
    op.create_table(
        "dataset_conformado",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("contrato", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("corrida_id", sa.Integer(), nullable=True),
        sa.Column("ingesta_pagos_id", sa.Integer(), nullable=True),
        sa.Column("artefacto_original_id", sa.Integer(), nullable=False),
        sa.Column("artefacto_conformado_id", sa.Integer(), nullable=False),
        sa.Column("firma_contenido", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("filas", sa.BigInteger(), nullable=False),
        sa.Column("columnas", sa.Integer(), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("filas >= 0 AND columnas > 0", name=op.f("ck_conformado_conteos")),
        sa.CheckConstraint("firma_contenido ~ '^[0-9a-f]{64}$'", name=op.f("ck_conformado_firma")),
        sa.CheckConstraint(
            "(corrida_id IS NULL) <> (ingesta_pagos_id IS NULL)", name=op.f("ck_conformado_origen")
        ),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dataset_conformado")),
        sa.UniqueConstraint("dataset_id", name=op.f("uq_dataset_conformado_dataset_id")),
        sa.UniqueConstraint("corrida_id", name="uq_conformado_corrida"),
        sa.UniqueConstraint("ingesta_pagos_id", name="uq_conformado_pagos"),
    )
    for columna in ("artefacto_original_id", "artefacto_conformado_id"):
        op.create_index(
            op.f(f"ix_dataset_conformado_{columna}"), "dataset_conformado", [columna], unique=False
        )

    # Las hojas companeras de cada corrida (CARRIER): se reconocen y se auditan, no se publican.
    op.create_table(
        "hoja_companera",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("corrida_id", sa.Integer(), nullable=False),
        sa.Column("nombre", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column("filas", sa.BigInteger(), nullable=False),
        sa.Column("columnas", sa.Integer(), nullable=False),
        sa.Column("estructura_reconocida", sa.Boolean(), nullable=False),
        sa.Column("advertencias", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("filas >= 0 AND columnas >= 0", name=op.f("ck_companera_conteos")),
        sa.ForeignKeyConstraint(["corrida_id"], ["corrida.id"], name="fk_companera_corrida"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hoja_companera")),
        sa.UniqueConstraint("corrida_id", "nombre", name="uq_companera_corrida_nombre"),
    )

    # El trabajo de una ingesta de pagos, en la misma cola durable.
    op.add_column(
        "trabajo_orquestacion", sa.Column("ingesta_pagos_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "fk_trabajo_pagos", "trabajo_orquestacion", "ingesta_pagos", ["ingesta_pagos_id"], ["id"]
    )
    op.create_unique_constraint("uq_trabajo_pagos", "trabajo_orquestacion", ["ingesta_pagos_id"])
    op.alter_column(
        "trabajo_orquestacion",
        "tipo",
        type_=sa.String(length=16),
        existing_type=sa.String(length=12),
        existing_nullable=False,
    )
    _rehacer_check("ck_trabajo_orquestacion_tipo_trabajo", _tipos(TIPOS_0007))
    _rehacer_check("ck_trabajo_objetivo", OBJETIVO_0007)


def downgrade() -> None:
    # Primero lo que la v0.5 no podria terminar: los trabajos, los flujos y al final las corridas,
    # porque los dos primeros se eligen por la corrida que todavia esta EN_PROCESO.
    op.execute(
        sa.text(
            "UPDATE trabajo_orquestacion SET estado = 'FALLIDO', worker_id = NULL, "
            "lease_hasta = NULL, terminado_en = now(), ultimo_error = :motivo "
            f"WHERE corrida_id IN ({SOLO_EN_EL_ALMACEN}) AND estado IN ('PENDIENTE', 'EJECUTANDO')"
        ).bindparams(motivo=CIERRE)
    )
    op.execute(
        sa.text(
            "UPDATE flujo_orquestacion SET estado = 'DETENIDO', terminado_en = now(), "
            "actualizado_en = now(), detalle = :motivo "
            f"WHERE corrida_id IN ({SOLO_EN_EL_ALMACEN}) AND estado = 'EN_PROCESO'"
        ).bindparams(motivo=CIERRE)
    )
    op.execute(
        sa.text(
            "UPDATE corrida SET estado = 'FALLIDA', terminada_en = now(), detalle = :motivo "
            f"WHERE id IN ({SOLO_EN_EL_ALMACEN})"
        ).bindparams(motivo=CIERRE)
    )
    # La 0006 no conoce las ingestas de pagos: se van sus trabajos, sus rechazos y ellas. Sus
    # archivos se quedan en el almacen.
    op.execute(sa.text("DELETE FROM trabajo_orquestacion WHERE tipo = 'INGESTA_PAGOS'"))
    _rehacer_check("ck_trabajo_objetivo", OBJETIVO_0006)
    _rehacer_check("ck_trabajo_orquestacion_tipo_trabajo", _tipos(TIPOS_0006))
    op.alter_column(
        "trabajo_orquestacion",
        "tipo",
        type_=sa.String(length=12),
        existing_type=sa.String(length=16),
        existing_nullable=False,
    )
    op.drop_constraint("uq_trabajo_pagos", "trabajo_orquestacion", type_="unique")
    op.drop_constraint("fk_trabajo_pagos", "trabajo_orquestacion", type_="foreignkey")
    op.drop_column("trabajo_orquestacion", "ingesta_pagos_id")

    op.drop_table("hoja_companera")
    for columna in ("artefacto_conformado_id", "artefacto_original_id"):
        op.drop_index(op.f(f"ix_dataset_conformado_{columna}"), table_name="dataset_conformado")
    op.drop_table("dataset_conformado")
    op.drop_table("rechazo_pago")
    for indice, estado in (
        ("ux_ingesta_pagos_firma_en_proceso", "EN_PROCESO"),
        ("ux_ingesta_pagos_firma_publicada", "EXITOSA"),
    ):
        op.drop_index(
            indice, table_name="ingesta_pagos", postgresql_where=sa.text(f"estado = '{estado}'")
        )
    op.drop_index(op.f("ix_ingesta_pagos_artefacto_fuente_id"), table_name="ingesta_pagos")
    op.drop_table("ingesta_pagos")
    # Las corridas de cartera/v2 se quedan, con sus cuentas: son historia. Solo pierden la version
    # de su proyeccion y el vinculo con su artefacto, que la 0006 no sabe guardar.
    op.drop_constraint(op.f("ck_corrida_proyeccion"), "corrida", type_="check")
    op.drop_constraint(op.f("ck_corrida_v2_fuente"), "corrida", type_="check")
    op.drop_column("corrida", "version_proyeccion")
    for columna in reversed(METADATA_DEL_SISTEMA):
        op.drop_column("corrida", columna)
    op.drop_index(op.f("ix_corrida_artefacto_fuente_id"), table_name="corrida")
    op.drop_constraint(
        op.f("fk_corrida_artefacto_fuente_id_artefacto_fuente"), "corrida", type_="foreignkey"
    )
    op.drop_column("corrida", "artefacto_fuente_id")
    op.drop_table("artefacto_fuente")
