"""modelo historico y Cuenta 360: cuentas canonicas, cortes, snapshots, pagos observados e historia

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-07 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

# Como en las demas migraciones, cada nombre se midio contra los 63 caracteres de PostgreSQL y es
# parte del contrato del esquema: sale en los IntegrityError y una migracion futura se refiere a el.
#
# La 0008 solo crea estructuras: no materializa ningun dataset. La historia de los datasets
# conformados que ya existen la construye un proceso de la aplicacion,
# `motor-cartera backfill-historia`, que encola un trabajo HISTORIA por dataset.

# La cola gana el tipo HISTORIA y su objetivo, la ejecucion historica. HISTORIA cabe en los 16
# caracteres de tipo; los CHECK del tipo y del objetivo se rehacen con el nuevo.
TIPOS_0007 = ("INGESTA", "DECISION", "TERRITORIAL", "RUTEO", "INGESTA_PAGOS")
TIPOS_0008 = (*TIPOS_0007, "HISTORIA")
OBJETIVO_0007 = (
    "(tipo = 'INGESTA') = (corrida_id IS NOT NULL) "
    "AND (tipo = 'DECISION') = (ejecucion_decision_id IS NOT NULL) "
    "AND (tipo = 'TERRITORIAL') = (ejecucion_territorial_id IS NOT NULL) "
    "AND (tipo = 'RUTEO') = (ejecucion_ruteo_id IS NOT NULL) "
    "AND (tipo = 'INGESTA_PAGOS') = (ingesta_pagos_id IS NOT NULL)"
)
OBJETIVO_0008 = OBJETIVO_0007 + " AND (tipo = 'HISTORIA') = (ejecucion_historia_id IS NOT NULL)"

# Bajar a la 0007 quita el modelo historico entero: es una proyeccion de los datasets conformados, y
# se reconstruye con el backfill al volver a subir. Lo que esta en curso se cierra primero, con este
# motivo, y despues se quita junto con sus tablas. Los datasets conformados, sus artefactos, las
# corridas, las ingestas de pagos y lo que publicaron los motores v1 no se tocan.
CIERRE = (
    "Cerrada al bajar la base a la 0007: la v0.6 no tiene modelo historico. Los datasets "
    "conformados y sus artefactos siguen intactos; al volver a subir, backfill-historia la "
    "reconstruye."
)
RESULTADO_DEL_CIERRE = "CERRADA_AL_BAJAR"
CERRAR_EJECUCIONES = sa.text(
    "UPDATE ejecucion_historia SET estado = 'FALLIDA', resultado = :resultado, "
    "registros_publicados = 0, terminada_en = now(), detalle = :motivo "
    "WHERE estado = 'EN_PROCESO'"
).bindparams(resultado=RESULTADO_DEL_CIERRE, motivo=CIERRE)
CERRAR_TRABAJOS = sa.text(
    "UPDATE trabajo_orquestacion SET estado = 'FALLIDO', worker_id = NULL, lease_hasta = NULL, "
    "terminado_en = now(), ultimo_error = :motivo "
    "WHERE tipo = 'HISTORIA' AND estado IN ('PENDIENTE', 'EJECUTANDO')"
).bindparams(motivo=CIERRE)
"""Lo primero que hace bajar la 0008: cerrar la historia en curso, antes de quitar sus tablas."""


def _tipos(tipos: tuple[str, ...]) -> str:
    return "tipo IN (" + ", ".join(f"'{t}'" for t in tipos) + ")"


def _rehacer_check(nombre: str, condicion: str) -> None:
    op.drop_constraint(op.f(nombre), "trabajo_orquestacion", type_="check")
    op.create_check_constraint(op.f(nombre), "trabajo_orquestacion", condicion)


def _estado(nombre: str, *valores: str) -> sa.Enum:
    return sa.Enum(*valores, name=nombre, native_enum=False, create_constraint=True, length=12)


def _importe(nombre: str, nullable: bool = True) -> sa.Column:
    return sa.Column(nombre, sa.Numeric(precision=14, scale=2), nullable=nullable)


def _texto(nombre: str, largo: int | None = None, nullable: bool = True) -> sa.Column:
    return sa.Column(nombre, sqlmodel.sql.sqltypes.AutoString(length=largo), nullable=nullable)


def upgrade() -> None:
    # La identidad longitudinal de cada cuenta: despacho, cartera y CLIENTE_UNICO.
    op.create_table(
        "cuenta_canonica",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("cuenta_id", sa.Uuid(), nullable=False),
        _texto("despacho_id", 32, nullable=False),
        _texto("cartera_id", 32, nullable=False),
        _texto("cliente_unico", 20, nullable=False),
        sa.Column("creada_en", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cuenta_canonica")),
        sa.UniqueConstraint("cuenta_id", name=op.f("uq_cuenta_canonica_cuenta_id")),
        sa.UniqueConstraint(
            "despacho_id", "cartera_id", "cliente_unico", name="uq_cuenta_canonica_clave"
        ),
    )

    # Una fotografia canonica por despacho, cartera y fecha, atada al dataset que la produjo.
    op.create_table(
        "corte_canonico",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("corte_id", sa.Uuid(), nullable=False),
        _texto("despacho_id", 32, nullable=False),
        _texto("cartera_id", 32, nullable=False),
        sa.Column("fecha_corte", sa.Date(), nullable=False),
        _texto("firma_contenido", 64, nullable=False),
        _texto("version_modelo", 32, nullable=False),
        sa.Column("cuentas", sa.BigInteger(), nullable=False),
        sa.Column("dataset_conformado_id", sa.Integer(), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("firma_contenido ~ '^[0-9a-f]{64}$'", name=op.f("ck_corte_firma")),
        sa.CheckConstraint("cuentas > 0", name=op.f("ck_corte_cuentas")),
        sa.ForeignKeyConstraint(
            ["dataset_conformado_id"], ["dataset_conformado.id"], name="fk_corte_dataset"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_corte_canonico")),
        sa.UniqueConstraint("corte_id", name=op.f("uq_corte_canonico_corte_id")),
        sa.UniqueConstraint(
            "despacho_id", "cartera_id", "fecha_corte", name="uq_corte_canonico_fecha"
        ),
        sa.UniqueConstraint("dataset_conformado_id", name="uq_corte_canonico_dataset"),
        sa.UniqueConstraint("id", "fecha_corte", name="uq_corte_canonico_id_fecha"),
    )

    # Cada materializacion de un dataset conformado, versionada y auditable.
    op.create_table(
        "ejecucion_historia",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("historia_run_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_conformado_id", sa.Integer(), nullable=False),
        sa.Column(
            "tipo_fuente", _estado("tipo_fuente_historia", "CARTERA", "PAGOS"), nullable=False
        ),
        _texto("version_modelo", 32, nullable=False),
        sa.Column(
            "estado", _estado("estado_historia", "EN_PROCESO", "EXITOSA", "FALLIDA"), nullable=False
        ),
        _texto("resultado", 40),
        sa.Column("corte_canonico_id", sa.Integer(), nullable=True),
        sa.Column("registros_leidos", sa.BigInteger(), nullable=False),
        sa.Column("registros_publicados", sa.BigInteger(), nullable=False),
        sa.Column("iniciada_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("terminada_en", sa.DateTime(timezone=True), nullable=True),
        _texto("detalle"),
        sa.CheckConstraint(
            "registros_leidos >= 0 AND registros_publicados >= 0 "
            "AND registros_publicados <= registros_leidos",
            name=op.f("ck_historia_conteos"),
        ),
        sa.CheckConstraint(
            "(corte_canonico_id IS NOT NULL) = (tipo_fuente = 'CARTERA' AND estado = 'EXITOSA')",
            name=op.f("ck_historia_corte"),
        ),
        sa.CheckConstraint(
            "(estado = 'EN_PROCESO') = (resultado IS NULL)", name=op.f("ck_historia_resultado")
        ),
        sa.ForeignKeyConstraint(
            ["dataset_conformado_id"], ["dataset_conformado.id"], name="fk_historia_dataset"
        ),
        sa.ForeignKeyConstraint(
            ["corte_canonico_id"], ["corte_canonico.id"], name="fk_historia_corte"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ejecucion_historia")),
        sa.UniqueConstraint("historia_run_id", name=op.f("uq_ejecucion_historia_historia_run_id")),
    )
    op.create_index(
        op.f("ix_ejecucion_historia_dataset_conformado_id"),
        "ejecucion_historia",
        ["dataset_conformado_id"],
        unique=False,
    )
    # Una EXITOSA y a lo mas un intento activo por dataset y version del modelo.
    for indice, estado in (
        ("ux_ejecucion_historia_exitosa", "EXITOSA"),
        ("ux_ejecucion_historia_en_proceso", "EN_PROCESO"),
    ):
        op.create_index(
            indice,
            "ejecucion_historia",
            ["dataset_conformado_id", "version_modelo"],
            unique=True,
            postgresql_where=sa.text(f"estado = '{estado}'"),
        )

    # Una cuenta canonica en un corte canonico. Sin id propio: la llave es la natural. Las columnas
    # de 8 bytes van primero, para que PostgreSQL no rellene bytes en cada fila.
    op.create_table(
        "snapshot_cuenta",
        sa.Column("corte_canonico_id", sa.Integer(), nullable=False),
        sa.Column("cuenta_canonica_id", sa.Integer(), nullable=False),
        sa.Column("dias_atraso", sa.BigInteger(), nullable=False),
        sa.Column("atraso_maximo", sa.BigInteger(), nullable=True),
        sa.Column("semanas_atraso", sa.BigInteger(), nullable=True),
        sa.Column("pagos_recibidos", sa.BigInteger(), nullable=True),
        sa.Column("fecha_corte", sa.Date(), nullable=False),
        sa.Column("fecha_ultimo_pago", sa.Date(), nullable=True),
        sa.Column("source_row", sa.Integer(), nullable=False),
        _importe("saldo_total", nullable=False),
        _importe("saldo"),
        _importe("moratorios"),
        _importe("saldo_atrasado"),
        _importe("saldo_requerido"),
        _importe("pago_normal"),
        _importe("imp_ultimo_pago"),
        _importe("monto_plan"),
        _importe("monto_promesa_pago"),
        _texto("producto", 20, nullable=False),
        _texto("canal", 20, nullable=False),
        _texto("cve_entidad", 2, nullable=False),
        _texto("cve_municipio", 3, nullable=False),
        _texto("estrategia"),
        _texto("estatus_plan"),
        _texto("estatus_promesa_pago"),
        _texto("source_sheet", 255),
        sa.ForeignKeyConstraint(
            ["corte_canonico_id", "fecha_corte"],
            ["corte_canonico.id", "corte_canonico.fecha_corte"],
            name="fk_snapshot_corte",
        ),
        sa.ForeignKeyConstraint(
            ["cuenta_canonica_id"], ["cuenta_canonica.id"], name="fk_snapshot_cuenta"
        ),
        sa.PrimaryKeyConstraint(
            "corte_canonico_id", "cuenta_canonica_id", name=op.f("pk_snapshot_cuenta")
        ),
    )
    op.create_index(
        "ix_snapshot_cuenta_historia",
        "snapshot_cuenta",
        ["cuenta_canonica_id", "fecha_corte"],
        unique=False,
    )

    # Una fila aceptada de pagos/v1, con sus 23 campos tipados. Sin id propio y sin llave hacia
    # cuenta_canonica: se relaciona con ella por despacho, cartera y CLIENTE_UNICO al consultar.
    op.create_table(
        "pago_observado",
        sa.Column("dataset_conformado_id", sa.Integer(), nullable=False),
        sa.Column("source_row", sa.Integer(), nullable=False),
        sa.Column("anio", sa.BigInteger(), nullable=True),
        sa.Column("semana", sa.BigInteger(), nullable=True),
        sa.Column("dias_de_atraso", sa.BigInteger(), nullable=True),
        sa.Column("semanas_de_atraso", sa.BigInteger(), nullable=True),
        sa.Column("fecha_recepcion", sa.DateTime(timezone=False), nullable=False),
        sa.Column("fecha_de_gestion", sa.DateTime(timezone=False), nullable=True),
        sa.Column("porcentaje_comision", sa.Float(), nullable=True),
        sa.Column("ingesta_pagos_id", sa.Integer(), nullable=False),
        sa.Column("pago_observado_id", sa.Uuid(), nullable=False),
        _importe("recuperacion_por_gestion", nullable=False),
        _importe("cargos_automaticos"),
        _importe("captacion"),
        _importe("cobranza_total"),
        _importe("monto_comision"),
        _texto("despacho_id", 32, nullable=False),
        _texto("cartera_id", 32, nullable=False),
        _texto("cliente_unico", 20, nullable=False),
        _texto("territorio"),
        _texto("zona"),
        _texto("segmento"),
        _texto("gerencia"),
        _texto("tipo_cartera"),
        _texto("producto"),
        _texto("campania"),
        _texto("gestor"),
        _texto("plan_de_pago"),
        _texto("concepto_calculo"),
        _texto("source_sheet", 255),
        sa.ForeignKeyConstraint(
            ["dataset_conformado_id"], ["dataset_conformado.id"], name="fk_pago_observado_dataset"
        ),
        sa.ForeignKeyConstraint(
            ["ingesta_pagos_id"], ["ingesta_pagos.id"], name="fk_pago_observado_ingesta"
        ),
        sa.PrimaryKeyConstraint(
            "dataset_conformado_id", "source_row", name=op.f("pk_pago_observado")
        ),
    )
    op.create_index(
        "ix_pago_observado_cuenta",
        "pago_observado",
        ["despacho_id", "cartera_id", "cliente_unico", "fecha_recepcion"],
        unique=False,
    )

    # El trabajo HISTORIA, en la misma cola durable: uno por ejecucion historica.
    op.add_column(
        "trabajo_orquestacion", sa.Column("ejecucion_historia_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "fk_trabajo_historia",
        "trabajo_orquestacion",
        "ejecucion_historia",
        ["ejecucion_historia_id"],
        ["id"],
    )
    op.create_unique_constraint(
        "uq_trabajo_historia", "trabajo_orquestacion", ["ejecucion_historia_id"]
    )
    _rehacer_check("ck_trabajo_orquestacion_tipo_trabajo", _tipos(TIPOS_0008))
    _rehacer_check("ck_trabajo_objetivo", OBJETIVO_0008)


def downgrade() -> None:
    # Primero se cierra lo que estaba en curso, en este orden: la ejecucion (si un worker la tiene
    # bloqueada, termina antes de que el UPDATE la tome) y despues su trabajo. Asi nada queda a
    # medias cuando se quita la estructura que lo sostiene. Despues se van los trabajos HISTORIA,
    # que la 0007 no sabria guardar, y el modelo historico entero.
    op.execute(CERRAR_EJECUCIONES)
    op.execute(CERRAR_TRABAJOS)
    op.execute(sa.text("DELETE FROM trabajo_orquestacion WHERE tipo = 'HISTORIA'"))
    _rehacer_check("ck_trabajo_objetivo", OBJETIVO_0007)
    _rehacer_check("ck_trabajo_orquestacion_tipo_trabajo", _tipos(TIPOS_0007))
    op.drop_constraint("uq_trabajo_historia", "trabajo_orquestacion", type_="unique")
    op.drop_constraint("fk_trabajo_historia", "trabajo_orquestacion", type_="foreignkey")
    op.drop_column("trabajo_orquestacion", "ejecucion_historia_id")

    op.drop_index("ix_pago_observado_cuenta", table_name="pago_observado")
    op.drop_table("pago_observado")
    op.drop_index("ix_snapshot_cuenta_historia", table_name="snapshot_cuenta")
    op.drop_table("snapshot_cuenta")
    for indice, estado in (
        ("ux_ejecucion_historia_en_proceso", "EN_PROCESO"),
        ("ux_ejecucion_historia_exitosa", "EXITOSA"),
    ):
        op.drop_index(
            indice,
            table_name="ejecucion_historia",
            postgresql_where=sa.text(f"estado = '{estado}'"),
        )
    op.drop_index(
        op.f("ix_ejecucion_historia_dataset_conformado_id"), table_name="ejecucion_historia"
    )
    op.drop_table("ejecucion_historia")
    op.drop_table("corte_canonico")
    op.drop_table("cuenta_canonica")
