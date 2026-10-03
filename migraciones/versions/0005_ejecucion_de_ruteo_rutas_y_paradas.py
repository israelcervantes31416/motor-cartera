"""ejecucion de ruteo, rutas y paradas

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-03 16:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

# Como en los modelos, cada nombre se midio contra los 63 caracteres de PostgreSQL. Los que la
# convencion dejaria largos, o justo en el limite, llevan nombre propio y corto: la llave de
# ejecucion_ruteo hacia ejecucion_territorial, la de ruta_territorial hacia resultado_territorial y
# las tres restricciones unicas de rutas y paradas. Los demas salen de la convencion. Todos son
# parte del contrato del esquema: salen en los IntegrityError y una migracion futura se refiere a
# ellos.


def upgrade() -> None:
    # Tres tablas nuevas y vacias: el motor de ruteo no existia, asi que no hay nada que llenar, y
    # lo que ya existe (corridas, cuentas, rechazos, decisiones y lo territorial) no se toca.
    op.create_table(
        "ejecucion_ruteo",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ruteo_run_id", sa.Uuid(), nullable=False),
        sa.Column("ejecucion_territorial_id", sa.Integer(), nullable=False),
        sa.Column("version_reglas", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column(
            "estado",
            sa.Enum(
                "EN_PROCESO",
                "EXITOSA",
                "FALLIDA",
                name="estado_ruteo",
                native_enum=False,
                create_constraint=True,
                length=12,
            ),
            nullable=False,
        ),
        sa.Column("iniciada_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("terminada_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rutas_evaluadas", sa.BigInteger(), nullable=False),
        sa.Column("rutas_publicadas", sa.BigInteger(), nullable=False),
        sa.Column("paradas_evaluadas", sa.BigInteger(), nullable=False),
        sa.Column("paradas_publicadas", sa.BigInteger(), nullable=False),
        sa.Column("detalle", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(
            ["ejecucion_territorial_id"],
            ["ejecucion_territorial.id"],
            name="fk_ejecucion_ruteo_territorial",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ejecucion_ruteo")),
        sa.UniqueConstraint("ruteo_run_id", name=op.f("uq_ejecucion_ruteo_ruteo_run_id")),
    )
    # Todas las ejecuciones de ruteo de una ejecucion territorial, en cualquier estado: el indice
    # parcial de abajo solo cubre las EXITOSA, y PostgreSQL no indexa una llave foranea por su
    # cuenta.
    op.create_index(
        op.f("ix_ejecucion_ruteo_ejecucion_territorial_id"),
        "ejecucion_ruteo",
        ["ejecucion_territorial_id"],
        unique=False,
    )
    # Una ejecucion territorial se rutea con exito una sola vez por version de las reglas de ruteo.
    # Las ejecuciones que no terminaron EXITOSA no cuentan: se pueden reintentar.
    op.create_index(
        "ux_ejecucion_ruteo_exitosa",
        "ejecucion_ruteo",
        ["ejecucion_territorial_id", "version_reglas"],
        unique=True,
        postgresql_where=sa.text("estado = 'EXITOSA'"),
    )
    op.create_table(
        "ruta_territorial",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ejecucion_ruteo_id", sa.Integer(), nullable=False),
        sa.Column("resultado_territorial_id", sa.Integer(), nullable=False),
        sa.Column("paradas", sa.BigInteger(), nullable=False),
        sa.Column("distancia_inicial_m", sa.BigInteger(), nullable=False),
        sa.Column("distancia_total_m", sa.BigInteger(), nullable=False),
        sa.Column("distancia_regreso_deposito_m", sa.BigInteger(), nullable=False),
        sa.Column("mejora_2opt_m", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["resultado_territorial_id"],
            ["resultado_territorial.id"],
            name="fk_ruta_territorial_resultado",
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_ruteo_id"],
            ["ejecucion_ruteo.id"],
            name=op.f("fk_ruta_territorial_ejecucion_ruteo_id_ejecucion_ruteo"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ruta_territorial")),
        # Una ruta por municipio y ejecucion. Empieza por ejecucion_ruteo_id: tambien sirve para
        # encontrar las rutas de una ejecucion.
        sa.UniqueConstraint(
            "ejecucion_ruteo_id", "resultado_territorial_id", name="uq_ruta_ruteo_territorio"
        ),
    )
    op.create_table(
        "parada_ruta",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ejecucion_ruteo_id", sa.Integer(), nullable=False),
        sa.Column("ruta_territorial_id", sa.Integer(), nullable=False),
        sa.Column("decision_cuenta_id", sa.Integer(), nullable=False),
        sa.Column("secuencia", sa.BigInteger(), nullable=False),
        sa.Column("x_m", sa.BigInteger(), nullable=False),
        sa.Column("y_m", sa.BigInteger(), nullable=False),
        sa.Column("distancia_desde_anterior_m", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["ejecucion_ruteo_id"],
            ["ejecucion_ruteo.id"],
            name=op.f("fk_parada_ruta_ejecucion_ruteo_id_ejecucion_ruteo"),
        ),
        sa.ForeignKeyConstraint(
            ["ruta_territorial_id"],
            ["ruta_territorial.id"],
            name=op.f("fk_parada_ruta_ruta_territorial_id_ruta_territorial"),
        ),
        sa.ForeignKeyConstraint(
            ["decision_cuenta_id"],
            ["decision_cuenta.id"],
            name=op.f("fk_parada_ruta_decision_cuenta_id_decision_cuenta"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_parada_ruta")),
        # Una decision, a lo mas una parada por ejecucion de ruteo, aunque la ejecucion tenga muchas
        # rutas; y cada lugar de la secuencia, de una sola parada por ruta. Las dos empiezan por la
        # columna con que se consultan, asi que no hacen falta indices sueltos.
        sa.UniqueConstraint(
            "ejecucion_ruteo_id", "decision_cuenta_id", name="uq_parada_ruteo_decision"
        ),
        sa.UniqueConstraint("ruta_territorial_id", "secuencia", name="uq_parada_ruta_secuencia"),
    )


def downgrade() -> None:
    # Se quita solo lo que esta migracion creo, en orden inverso: primero las paradas, que apuntan a
    # las rutas, y las rutas, que apuntan a las ejecuciones. Se pierde el ruteo; lo de antes queda
    # como estaba. No hay tipo que borrar: el estado es VARCHAR con CHECK (native_enum=False), y el
    # CHECK se va con su tabla.
    op.drop_table("parada_ruta")
    op.drop_table("ruta_territorial")
    op.drop_index(
        "ux_ejecucion_ruteo_exitosa",
        table_name="ejecucion_ruteo",
        postgresql_where=sa.text("estado = 'EXITOSA'"),
    )
    op.drop_index(op.f("ix_ejecucion_ruteo_ejecucion_territorial_id"), table_name="ejecucion_ruteo")
    op.drop_table("ejecucion_ruteo")
