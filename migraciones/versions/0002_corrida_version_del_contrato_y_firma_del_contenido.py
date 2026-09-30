"""corrida: version del contrato y firma del contenido

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-29 18:43:50.000000
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Las corridas que ya existan no registraron con que version del contrato se juzgaron.
    # Se marcan 'sin-registro' en lugar de suponerles una, y el valor por omision se quita
    # enseguida: de aqui en adelante cada corrida tiene que traer la suya.
    op.add_column(
        "corrida",
        sa.Column(
            "version_contrato",
            sqlmodel.sql.sqltypes.AutoString(length=32),
            nullable=False,
            server_default="sin-registro",
        ),
    )
    op.alter_column("corrida", "version_contrato", server_default=None)
    # Vacia en las corridas que ya existan: calcularla exigiria el archivo, y no se guarda.
    op.add_column(
        "corrida",
        sa.Column("firma_contenido", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("corrida", "firma_contenido")
    op.drop_column("corrida", "version_contrato")
