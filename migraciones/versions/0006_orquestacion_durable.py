"""orquestacion durable: archivo, flujo, trabajo e intentos EN_PROCESO unicos

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-04 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

# Como en los modelos, cada nombre se midio contra los 63 caracteres de PostgreSQL. Las llaves, las
# unicas y los CHECK de las tablas de orquestacion llevan nombres cortos y propios: varias llaves de
# la convencion pasarian del limite. Las de los estados salen de la convencion, como en las demas
# tablas. Todos son parte del contrato del esquema.

# Hasta la 0005 un recurso EN_PROCESO no tenia trabajo persistido ni lease: si el proceso que lo
# atendia moria, nadie lo iba a cerrar. La 0006 no adivina quien era su dueno. Los cierra FALLIDA,
# con el motivo, antes de crear los indices que solo admiten un intento activo por fuente. Sus
# FALLIDA, EXITOSA y RECHAZADA no se tocan, ni se borra ningun resultado.
CIERRES = {
    "corrida": (
        "Cerrada al migrar a la orquestacion durable de v0.5.0: la corrida no tenia trabajo "
        "persistido ni lease, y nadie la iba a terminar. El archivo se puede volver a subir."
    ),
    "ejecucion_decision": (
        "Cerrada al migrar a la orquestacion durable de v0.5.0: la ejecucion no tenia trabajo "
        "persistido ni lease, y nadie la iba a terminar. Se puede reintentar con otra ejecucion."
    ),
    "ejecucion_territorial": (
        "Cerrada al migrar a la orquestacion durable de v0.5.0: la ejecucion territorial no tenia "
        "trabajo persistido ni lease, y nadie la iba a terminar. Se puede reintentar con otra "
        "ejecucion."
    ),
    "ejecucion_ruteo": (
        "Cerrada al migrar a la orquestacion durable de v0.5.0: la ejecucion de ruteo no tenia "
        "trabajo persistido ni lease, y nadie la iba a terminar. Se puede reintentar con otra "
        "ejecucion."
    ),
}
# Lo que una FALLIDA dice que publico: nada. Una EN_PROCESO todavia no publicaba, asi que ya es
# cero; se fija igual, para que el cierre no dependa de eso.
PUBLICADOS = {
    "corrida": "",
    "ejecucion_decision": ", cuentas_decididas = 0",
    "ejecucion_territorial": ", territorios_publicados = 0",
    "ejecucion_ruteo": ", rutas_publicadas = 0, paradas_publicadas = 0",
}

# Un intento activo por fuente y version: la tabla, el nombre del indice y sus columnas.
EN_PROCESO = (
    ("corrida", "ux_corrida_firma_en_proceso", ["firma"]),
    ("ejecucion_decision", "ux_ejecucion_decision_en_proceso", ["corrida_id", "version_reglas"]),
    (
        "ejecucion_territorial",
        "ux_ejecucion_territorial_en_proceso",
        ["ejecucion_decision_id", "version_reglas"],
    ),
    (
        "ejecucion_ruteo",
        "ux_ejecucion_ruteo_en_proceso",
        ["ejecucion_territorial_id", "version_reglas"],
    ),
)

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
OBJETIVO_DEL_TRABAJO = (
    "(tipo = 'INGESTA') = (corrida_id IS NOT NULL) "
    "AND (tipo = 'DECISION') = (ejecucion_decision_id IS NOT NULL) "
    "AND (tipo = 'TERRITORIAL') = (ejecucion_territorial_id IS NOT NULL) "
    "AND (tipo = 'RUTEO') = (ejecucion_ruteo_id IS NOT NULL)"
)
PROPIEDAD_DEL_TRABAJO = (
    "(estado = 'PENDIENTE' AND worker_id IS NULL AND lease_hasta IS NULL "
    "AND terminado_en IS NULL) "
    "OR (estado = 'EJECUTANDO' AND worker_id IS NOT NULL AND lease_hasta IS NOT NULL "
    "AND terminado_en IS NULL) "
    "OR (estado IN ('COMPLETADO', 'FALLIDO') AND worker_id IS NULL AND lease_hasta IS NULL "
    "AND terminado_en IS NOT NULL)"
)


def _estado(nombre: str, *valores: str) -> sa.Enum:
    return sa.Enum(*valores, name=nombre, native_enum=False, create_constraint=True, length=12)


def upgrade() -> None:
    for tabla, motivo in CIERRES.items():
        op.execute(
            sa.text(
                f"UPDATE {tabla} SET estado = 'FALLIDA', terminada_en = now(), detalle = :motivo"
                f"{PUBLICADOS[tabla]} WHERE estado = 'EN_PROCESO'"
            ).bindparams(motivo=motivo)
        )
    # Los indices de las EXITOSA se quedan: protegen que no se publique dos veces. Estos protegen
    # otra cosa, que no se trabaje dos veces a la vez la misma fuente con la misma version.
    for tabla, indice, columnas in EN_PROCESO:
        op.create_index(
            indice,
            tabla,
            columnas,
            unique=True,
            postgresql_where=sa.text("estado = 'EN_PROCESO'"),
        )

    op.create_table(
        "archivo_corrida",
        sa.Column("corrida_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("contenido", sa.LargeBinary(), nullable=False),
        sa.Column("tamano_bytes", sa.BigInteger(), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("tamano_bytes > 0", name=op.f("ck_archivo_tamano_positivo")),
        sa.CheckConstraint(
            "octet_length(contenido) = tamano_bytes", name=op.f("ck_archivo_tamano_exacto")
        ),
        sa.ForeignKeyConstraint(
            ["corrida_id"], ["corrida.id"], name=op.f("fk_archivo_corrida_corrida_id_corrida")
        ),
        sa.PrimaryKeyConstraint("corrida_id", name=op.f("pk_archivo_corrida")),
    )

    op.create_table(
        "flujo_orquestacion",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("flujo_id", sa.Uuid(), nullable=False),
        sa.Column("corrida_id", sa.Integer(), nullable=False),
        sa.Column("ejecucion_decision_id", sa.Integer(), nullable=True),
        sa.Column("ejecucion_territorial_id", sa.Integer(), nullable=True),
        sa.Column("ejecucion_ruteo_id", sa.Integer(), nullable=True),
        sa.Column(
            "estado",
            _estado("estado_flujo", "EN_PROCESO", "COMPLETADO", "DETENIDO"),
            nullable=False,
        ),
        sa.Column(
            "etapa",
            _estado("etapa_flujo", "INGESTA", "DECISION", "TERRITORIAL", "RUTEO", "COMPLETADA"),
            nullable=False,
        ),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actualizado_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("terminado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("detalle", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_flujo_orquestacion")),
        sa.UniqueConstraint("flujo_id", name=op.f("uq_flujo_orquestacion_flujo_id")),
        # Una corrida, a lo mas un flujo; una ejecucion, a lo mas un flujo. Los NULL no chocan.
        sa.UniqueConstraint("corrida_id", name="uq_flujo_corrida"),
        sa.UniqueConstraint("ejecucion_decision_id", name="uq_flujo_decision"),
        sa.UniqueConstraint("ejecucion_territorial_id", name="uq_flujo_territorial"),
        sa.UniqueConstraint("ejecucion_ruteo_id", name="uq_flujo_ruteo"),
        sa.CheckConstraint(CADENA_DEL_FLUJO, name=op.f("ck_flujo_cadena")),
        sa.CheckConstraint(
            "(estado = 'COMPLETADO') = (etapa = 'COMPLETADA')", name=op.f("ck_flujo_completado")
        ),
        sa.CheckConstraint(
            "(estado = 'EN_PROCESO') = (terminado_en IS NULL)", name=op.f("ck_flujo_terminado")
        ),
    )

    op.create_table(
        "trabajo_orquestacion",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trabajo_id", sa.Uuid(), nullable=False),
        sa.Column("flujo_id", sa.Integer(), nullable=True),
        sa.Column(
            "tipo",
            _estado("tipo_trabajo", "INGESTA", "DECISION", "TERRITORIAL", "RUTEO"),
            nullable=False,
        ),
        sa.Column(
            "estado",
            _estado("estado_trabajo", "PENDIENTE", "EJECUTANDO", "COMPLETADO", "FALLIDO"),
            nullable=False,
        ),
        sa.Column("corrida_id", sa.Integer(), nullable=True),
        sa.Column("ejecucion_decision_id", sa.Integer(), nullable=True),
        sa.Column("ejecucion_territorial_id", sa.Integer(), nullable=True),
        sa.Column("ejecucion_ruteo_id", sa.Integer(), nullable=True),
        sa.Column("intentos", sa.Integer(), nullable=False),
        sa.Column("max_intentos", sa.Integer(), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disponible_desde", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tomado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latido_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_hasta", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sqlmodel.sql.sqltypes.AutoString(length=200), nullable=True),
        sa.Column("ultimo_error", sqlmodel.sql.sqltypes.AutoString(length=500), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trabajo_orquestacion")),
        sa.UniqueConstraint("trabajo_id", name=op.f("uq_trabajo_orquestacion_trabajo_id")),
        # Un recurso, un trabajo: una nueva entrega reusa la misma fila.
        sa.UniqueConstraint("corrida_id", name="uq_trabajo_corrida"),
        sa.UniqueConstraint("ejecucion_decision_id", name="uq_trabajo_decision"),
        sa.UniqueConstraint("ejecucion_territorial_id", name="uq_trabajo_territorial"),
        sa.UniqueConstraint("ejecucion_ruteo_id", name="uq_trabajo_ruteo"),
        sa.CheckConstraint(OBJETIVO_DEL_TRABAJO, name=op.f("ck_trabajo_objetivo")),
        sa.CheckConstraint(PROPIEDAD_DEL_TRABAJO, name=op.f("ck_trabajo_lease")),
        sa.CheckConstraint(
            "intentos >= 0 AND max_intentos >= 1 AND intentos <= max_intentos",
            name=op.f("ck_trabajo_intentos"),
        ),
    )
    # Los trabajos de un flujo, en cualquier estado: PostgreSQL no indexa una llave foranea solo.
    op.create_index(
        op.f("ix_trabajo_orquestacion_flujo_id"), "trabajo_orquestacion", ["flujo_id"], unique=False
    )
    # La consulta del worker, sobre los que todavia se pueden reclamar.
    op.create_index(
        "ix_trabajo_reclamable",
        "trabajo_orquestacion",
        ["estado", "disponible_desde", "lease_hasta", "id"],
        unique=False,
        postgresql_where=sa.text("estado IN ('PENDIENTE', 'EJECUTANDO')"),
    )


def downgrade() -> None:
    # Se quita lo que esta migracion creo, en orden inverso: los trabajos apuntan a los flujos, y
    # los dos, a los recursos. Se pierden la cola, los flujos y los archivos que todavia esperaban
    # su ingesta.
    #
    # Lo que no se deshace es el cierre: las que la 0006 cerro FALLIDA se quedan FALLIDA. No se
    # puede volver a un EN_PROCESO cuyo dueno nunca existio, y un EN_PROCESO sin dueno es justo lo
    # que la 0006 vino a quitar.
    op.drop_index(
        "ix_trabajo_reclamable",
        table_name="trabajo_orquestacion",
        postgresql_where=sa.text("estado IN ('PENDIENTE', 'EJECUTANDO')"),
    )
    op.drop_index(op.f("ix_trabajo_orquestacion_flujo_id"), table_name="trabajo_orquestacion")
    op.drop_table("trabajo_orquestacion")
    op.drop_table("flujo_orquestacion")
    op.drop_table("archivo_corrida")
    for tabla, indice, _ in reversed(EN_PROCESO):
        op.drop_index(indice, table_name=tabla, postgresql_where=sa.text("estado = 'EN_PROCESO'"))
