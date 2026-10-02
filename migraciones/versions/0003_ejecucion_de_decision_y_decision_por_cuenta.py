"""ejecucion de decision y decision por cuenta

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-30 12:30:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Dos tablas nuevas y vacias: el motor de decision no existia en la fase 1, asi que no hay
    # nada que llenar, y las corridas, cuentas y rechazos que ya existan no se tocan.
    op.create_table(
        "ejecucion_decision",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("decision_run_id", sa.Uuid(), nullable=False),
        sa.Column("corrida_id", sa.Integer(), nullable=False),
        sa.Column("version_reglas", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column(
            "estado",
            sa.Enum(
                "EN_PROCESO",
                "EXITOSA",
                "FALLIDA",
                name="estado_decision",
                native_enum=False,
                create_constraint=True,
                length=12,
            ),
            nullable=False,
        ),
        sa.Column("iniciada_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("terminada_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cuentas_evaluadas", sa.Integer(), nullable=False),
        sa.Column("cuentas_decididas", sa.Integer(), nullable=False),
        sa.Column("detalle", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(
            ["corrida_id"], ["corrida.id"], name=op.f("fk_ejecucion_decision_corrida_id_corrida")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ejecucion_decision")),
        sa.UniqueConstraint("decision_run_id", name=op.f("uq_ejecucion_decision_decision_run_id")),
    )
    # Todas las ejecuciones de una corrida, en cualquier estado: el indice parcial de abajo solo
    # cubre las EXITOSA, y PostgreSQL no indexa una llave foranea por su cuenta.
    op.create_index(
        op.f("ix_ejecucion_decision_corrida_id"), "ejecucion_decision", ["corrida_id"], unique=False
    )
    # Una corrida se decide con exito una sola vez por version de las reglas. Las ejecuciones que no
    # terminaron EXITOSA no cuentan: se pueden reintentar.
    op.create_index(
        "ux_ejecucion_decision_exitosa",
        "ejecucion_decision",
        ["corrida_id", "version_reglas"],
        unique=True,
        postgresql_where=sa.text("estado = 'EXITOSA'"),
    )
    op.create_table(
        "decision_cuenta",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ejecucion_decision_id", sa.Integer(), nullable=False),
        sa.Column("cuenta_id", sa.Integer(), nullable=False),
        sa.Column("segmento", sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column("prioridad", sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column("canal_recomendado", sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column("motivos", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["ejecucion_decision_id"],
            ["ejecucion_decision.id"],
            name=op.f("fk_decision_cuenta_ejecucion_decision_id_ejecucion_decision"),
        ),
        sa.ForeignKeyConstraint(
            ["cuenta_id"], ["cuenta.id"], name=op.f("fk_decision_cuenta_cuenta_id_cuenta")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_decision_cuenta")),
        sa.UniqueConstraint(
            "ejecucion_decision_id",
            "cuenta_id",
            name=op.f("uq_decision_cuenta_ejecucion_decision_id_cuenta_id"),
        ),
    )


def downgrade() -> None:
    # Se quita solo lo que esta migracion creo, en orden inverso. Se pierden las decisiones; las
    # corridas, cuentas y rechazos quedan como estaban. No hay tipo que borrar: el estado es VARCHAR
    # con CHECK (native_enum=False), y el CHECK se va con su tabla.
    op.drop_table("decision_cuenta")
    op.drop_index(
        "ux_ejecucion_decision_exitosa",
        table_name="ejecucion_decision",
        postgresql_where=sa.text("estado = 'EXITOSA'"),
    )
    op.drop_index(op.f("ix_ejecucion_decision_corrida_id"), table_name="ejecucion_decision")
    op.drop_table("ejecucion_decision")
