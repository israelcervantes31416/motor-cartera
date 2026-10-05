"""fuentes oficiales y evidencia inmutable: el artefacto fuente de cada corrida

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-05 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel
from alembic import op

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
    for columna in reversed(METADATA_DEL_SISTEMA):
        op.drop_column("corrida", columna)
    op.drop_index(op.f("ix_corrida_artefacto_fuente_id"), table_name="corrida")
    op.drop_constraint(
        op.f("fk_corrida_artefacto_fuente_id_artefacto_fuente"), "corrida", type_="foreignkey"
    )
    op.drop_column("corrida", "artefacto_fuente_id")
    op.drop_table("artefacto_fuente")
