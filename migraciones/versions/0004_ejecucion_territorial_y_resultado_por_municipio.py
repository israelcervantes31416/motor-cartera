"""ejecucion territorial y resultado por municipio

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-03 11:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

# Las dos llaves foraneas y la restriccion unica de resultado_territorial llevan nombre propio,
# igual que en los modelos: los de la convencion pasarian de 63 caracteres, el limite de
# PostgreSQL, y la base guardaria unos recortados. Son parte del contrato del esquema: salen en
# los IntegrityError y una migracion futura se refiere a ellos.


def upgrade() -> None:
    # Dos tablas nuevas y vacias: el motor territorial no existia, asi que no hay nada que llenar,
    # y lo que ya existe (corridas, cuentas, rechazos, ejecuciones de decision y decisiones) no se
    # toca.
    op.create_table(
        "ejecucion_territorial",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("territorial_run_id", sa.Uuid(), nullable=False),
        sa.Column("ejecucion_decision_id", sa.Integer(), nullable=False),
        sa.Column("version_reglas", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column(
            "estado",
            sa.Enum(
                "EN_PROCESO",
                "EXITOSA",
                "FALLIDA",
                name="estado_territorial",
                native_enum=False,
                create_constraint=True,
                length=12,
            ),
            nullable=False,
        ),
        sa.Column("iniciada_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("terminada_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("territorios_evaluados", sa.Integer(), nullable=False),
        sa.Column("territorios_publicados", sa.Integer(), nullable=False),
        sa.Column("detalle", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(
            ["ejecucion_decision_id"],
            ["ejecucion_decision.id"],
            name="fk_ejecucion_territorial_decision",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ejecucion_territorial")),
        sa.UniqueConstraint(
            "territorial_run_id", name=op.f("uq_ejecucion_territorial_territorial_run_id")
        ),
    )
    # Todas las ejecuciones territoriales de una ejecucion de decision, en cualquier estado: el
    # indice parcial de abajo solo cubre las EXITOSA, y PostgreSQL no indexa una llave foranea por
    # su cuenta.
    op.create_index(
        op.f("ix_ejecucion_territorial_ejecucion_decision_id"),
        "ejecucion_territorial",
        ["ejecucion_decision_id"],
        unique=False,
    )
    # Una ejecucion de decision se organiza con exito una sola vez por version de las reglas
    # territoriales. Las ejecuciones que no terminaron EXITOSA no cuentan: se pueden reintentar.
    op.create_index(
        "ux_ejecucion_territorial_exitosa",
        "ejecucion_territorial",
        ["ejecucion_decision_id", "version_reglas"],
        unique=True,
        postgresql_where=sa.text("estado = 'EXITOSA'"),
    )
    op.create_table(
        "resultado_territorial",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ejecucion_territorial_id", sa.Integer(), nullable=False),
        sa.Column("cve_entidad", sqlmodel.sql.sqltypes.AutoString(length=2), nullable=False),
        sa.Column("cve_municipio", sqlmodel.sql.sqltypes.AutoString(length=3), nullable=False),
        sa.Column("cuentas_total", sa.BigInteger(), nullable=False),
        sa.Column("saldo_total", sa.Numeric(precision=24, scale=2), nullable=False),
        sa.Column("cuentas_campo", sa.BigInteger(), nullable=False),
        sa.Column("saldo_campo", sa.Numeric(precision=24, scale=2), nullable=False),
        sa.Column("carga", sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column("posicion_campo", sa.BigInteger(), nullable=True),
        sa.Column("motivos", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["ejecucion_territorial_id"],
            ["ejecucion_territorial.id"],
            name="fk_resultado_territorial_ejecucion",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resultado_territorial")),
        sa.UniqueConstraint(
            "ejecucion_territorial_id",
            "cve_entidad",
            "cve_municipio",
            name="uq_resultado_territorial_municipio",
        ),
    )
    # Cada lugar del orden de campo es de un solo municipio por ejecucion; los municipios sin
    # cuentas de campo no tienen lugar (NULL), y esos no cuentan.
    op.create_index(
        "ux_resultado_territorial_posicion_campo",
        "resultado_territorial",
        ["ejecucion_territorial_id", "posicion_campo"],
        unique=True,
        postgresql_where=sa.text("posicion_campo IS NOT NULL"),
    )


def downgrade() -> None:
    # Se quita solo lo que esta migracion creo, en orden inverso: primero los resultados, que
    # apuntan a las ejecuciones. Se pierde lo territorial; lo de antes queda como estaba. No hay
    # tipo que borrar: el estado es VARCHAR con CHECK (native_enum=False), y el CHECK se va con su
    # tabla.
    op.drop_index(
        "ux_resultado_territorial_posicion_campo",
        table_name="resultado_territorial",
        postgresql_where=sa.text("posicion_campo IS NOT NULL"),
    )
    op.drop_table("resultado_territorial")
    op.drop_index(
        "ux_ejecucion_territorial_exitosa",
        table_name="ejecucion_territorial",
        postgresql_where=sa.text("estado = 'EXITOSA'"),
    )
    op.drop_index(
        op.f("ix_ejecucion_territorial_ejecucion_decision_id"), table_name="ejecucion_territorial"
    )
    op.drop_table("ejecucion_territorial")
