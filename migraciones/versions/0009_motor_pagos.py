"""motor de pagos: ejecuciones, movimientos economicos canonicos y resultados por pago observado

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-08 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

# Como en las demas migraciones, cada nombre se midio contra los 63 caracteres de PostgreSQL y es
# parte del contrato del esquema: sale en los IntegrityError y una migracion futura se refiere a el.
#
# La 0009 solo crea estructuras: no interpreta ningun pago. La interpretacion de los pagos
# observados que ya existen la encola un proceso de la aplicacion, `motor-cartera
# backfill-motor-pagos`, una ventana por trabajo MOTOR_PAGOS. Los pagos observados no cambian: solo
# ganan dos indices, para leer una ventana sin recorrer toda la historia.

# La cola gana el tipo MOTOR_PAGOS y su objetivo, la ejecucion del motor de pagos. MOTOR_PAGOS cabe
# en los 16 caracteres de tipo; los CHECK del tipo y del objetivo se rehacen con el nuevo.
TIPOS_0008 = ("INGESTA", "DECISION", "TERRITORIAL", "RUTEO", "INGESTA_PAGOS", "HISTORIA")
TIPOS_0009 = (*TIPOS_0008, "MOTOR_PAGOS")
OBJETIVO_0008 = (
    "(tipo = 'INGESTA') = (corrida_id IS NOT NULL) "
    "AND (tipo = 'DECISION') = (ejecucion_decision_id IS NOT NULL) "
    "AND (tipo = 'TERRITORIAL') = (ejecucion_territorial_id IS NOT NULL) "
    "AND (tipo = 'RUTEO') = (ejecucion_ruteo_id IS NOT NULL) "
    "AND (tipo = 'INGESTA_PAGOS') = (ingesta_pagos_id IS NOT NULL) "
    "AND (tipo = 'HISTORIA') = (ejecucion_historia_id IS NOT NULL)"
)
OBJETIVO_0009 = (
    OBJETIVO_0008 + " AND (tipo = 'MOTOR_PAGOS') = (ejecucion_motor_pagos_id IS NOT NULL)"
)

CONTEOS = (
    "observaciones_leidas >= 0 AND observaciones_contexto >= 0 AND primarios >= 0 "
    "AND duplicados_exactos >= 0 AND coincidencias_ambiguas >= 0 AND reversos >= 0 "
    "AND posibles_reversos >= 0 AND no_conciliados >= 0 AND sin_cuenta_observada >= 0 "
    "AND grupos_exactos >= 0 AND grupos_legacy >= 0 AND grupos_ambiguos >= 0 "
    "AND observaciones_en_grupos_legacy >= 0 AND pagos_anulados >= 0 "
    "AND sin_cuenta_observada <= observaciones_clasificadas AND pagos_anulados <= primarios"
)
CLASES = (
    "observaciones_clasificadas = primarios + duplicados_exactos + coincidencias_ambiguas "
    "+ reversos + posibles_reversos + no_conciliados"
)
METRICAS = (
    "observaciones_leidas",
    "observaciones_contexto",
    "observaciones_clasificadas",
    "movimientos_canonicos",
    "primarios",
    "duplicados_exactos",
    "coincidencias_ambiguas",
    "reversos",
    "posibles_reversos",
    "no_conciliados",
    "sin_cuenta_observada",
    "grupos_exactos",
    "grupos_legacy",
    "grupos_ambiguos",
    "observaciones_en_grupos_legacy",
    "pagos_anulados",
)

# Bajar a la 0008 quita la interpretacion entera: es una proyeccion de los pagos observados, y se
# reconstruye con el backfill al volver a subir, con los mismos movimiento_id. Lo que esta en curso
# se cierra primero, con este motivo. Los pagos observados, las cuentas canonicas, los snapshots,
# los datasets, sus artefactos, las corridas y las ingestas de pagos no se tocan.
CIERRE = (
    "Cerrada al bajar la base a la 0008: la v0.7 no tiene motor de pagos. Los pagos observados "
    "siguen intactos; al volver a subir, backfill-motor-pagos los vuelve a interpretar."
)
RESULTADO_DEL_CIERRE = "CERRADA_AL_BAJAR"
CERRAR_EJECUCIONES = sa.text(
    "UPDATE ejecucion_motor_pagos SET estado = 'FALLIDA', resultado = :resultado, "
    "terminada_en = now(), detalle = :motivo WHERE estado = 'EN_PROCESO'"
).bindparams(resultado=RESULTADO_DEL_CIERRE, motivo=CIERRE)
CERRAR_TRABAJOS = sa.text(
    "UPDATE trabajo_orquestacion SET estado = 'FALLIDO', worker_id = NULL, lease_hasta = NULL, "
    "terminado_en = now(), ultimo_error = :motivo "
    "WHERE tipo = 'MOTOR_PAGOS' AND estado IN ('PENDIENTE', 'EJECUTANDO')"
).bindparams(motivo=CIERRE)
"""Lo primero que hace bajar la 0009: cerrar la interpretacion en curso, antes de quitar sus
tablas."""


def _tipos(tipos: tuple[str, ...]) -> str:
    return "tipo IN (" + ", ".join(f"'{t}'" for t in tipos) + ")"


def _rehacer_check(nombre: str, condicion: str) -> None:
    op.drop_constraint(op.f(nombre), "trabajo_orquestacion", type_="check")
    op.create_check_constraint(op.f(nombre), "trabajo_orquestacion", condicion)


def _texto(nombre: str, largo: int | None = None, nullable: bool = True) -> sa.Column:
    return sa.Column(nombre, sqlmodel.sql.sqltypes.AutoString(length=largo), nullable=nullable)


def _importe(nombre: str, digitos: int = 14) -> sa.Column:
    return sa.Column(nombre, sa.Numeric(precision=digitos, scale=2), nullable=False)


def upgrade() -> None:
    # Cada interpretacion de una ventana: un despacho, una cartera y un mes de recepcion.
    op.create_table(
        "ejecucion_motor_pagos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("motor_pagos_run_id", sa.Uuid(), nullable=False),
        _texto("version_motor", 32, nullable=False),
        _texto("despacho_id", 32, nullable=False),
        _texto("cartera_id", 32, nullable=False),
        sa.Column("periodo_desde", sa.Date(), nullable=False),
        sa.Column("periodo_hasta", sa.Date(), nullable=False),
        sa.Column(
            "estado",
            sa.Enum(
                "EN_PROCESO",
                "EXITOSA",
                "FALLIDA",
                name="estado_motor_pagos",
                native_enum=False,
                create_constraint=True,
                length=12,
            ),
            nullable=False,
        ),
        _texto("resultado", 40),
        _texto("firma_entrada", 64),
        sa.Column("iniciada_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("terminada_en", sa.DateTime(timezone=True), nullable=True),
        *(sa.Column(metrica, sa.BigInteger(), nullable=False) for metrica in METRICAS),
        _importe("recuperacion_bruta_interpretada", 24),
        _importe("recuperacion_neta_interpretada", 24),
        _importe("importe_ambiguo_observado", 24),
        _texto("detalle"),
        sa.CheckConstraint("periodo_desde < periodo_hasta", name=op.f("ck_motor_pagos_periodo")),
        sa.CheckConstraint(
            "version_motor <> 'motor-pagos/v1' OR (extract(day FROM periodo_desde) = 1 "
            "AND periodo_hasta = (periodo_desde + interval '1 month')::date)",
            name=op.f("ck_motor_pagos_mes"),
        ),
        sa.CheckConstraint(
            "(estado = 'EN_PROCESO') = (resultado IS NULL)", name=op.f("ck_motor_pagos_resultado")
        ),
        sa.CheckConstraint(
            "(firma_entrada IS NULL OR firma_entrada ~ '^[0-9a-f]{64}$') "
            "AND (estado <> 'EXITOSA' OR firma_entrada IS NOT NULL)",
            name=op.f("ck_motor_pagos_firma"),
        ),
        sa.CheckConstraint(CONTEOS, name=op.f("ck_motor_pagos_conteos")),
        sa.CheckConstraint(CLASES, name=op.f("ck_motor_pagos_clases")),
        sa.CheckConstraint(
            "movimientos_canonicos = primarios + reversos + posibles_reversos",
            name=op.f("ck_motor_pagos_movimientos"),
        ),
        sa.CheckConstraint(
            "(estado = 'EXITOSA' AND observaciones_clasificadas = observaciones_leidas) "
            "OR (estado <> 'EXITOSA' AND observaciones_clasificadas = 0 "
            "AND movimientos_canonicos = 0)",
            name=op.f("ck_motor_pagos_publicacion"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ejecucion_motor_pagos")),
        sa.UniqueConstraint(
            "motor_pagos_run_id", name=op.f("uq_ejecucion_motor_pagos_motor_pagos_run_id")
        ),
    )
    # Una EXITOSA por ventana, version y firma de entrada; un intento activo por ventana y version.
    op.create_index(
        "ux_ejecucion_motor_pagos_exitosa",
        "ejecucion_motor_pagos",
        ["despacho_id", "cartera_id", "version_motor", "periodo_desde", "firma_entrada"],
        unique=True,
        postgresql_where=sa.text("estado = 'EXITOSA'"),
    )
    op.create_index(
        "ux_ejecucion_motor_pagos_en_proceso",
        "ejecucion_motor_pagos",
        ["despacho_id", "cartera_id", "version_motor", "periodo_desde"],
        unique=True,
        postgresql_where=sa.text("estado = 'EN_PROCESO'"),
    )

    # Un movimiento economico distinto, por ejecucion. Las columnas de 8 bytes primero.
    op.create_table(
        "movimiento_economico_canonico",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("observaciones", sa.BigInteger(), nullable=False),
        sa.Column("fecha_recepcion", sa.DateTime(timezone=False), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ejecucion_motor_pagos_id", sa.Integer(), nullable=False),
        sa.Column("cuenta_canonica_id", sa.Integer(), nullable=True),
        sa.Column("movimiento_id", sa.Uuid(), nullable=False),
        sa.Column("movimiento_original_id", sa.Uuid(), nullable=True),
        sa.Column("anulado_por_movimiento_id", sa.Uuid(), nullable=True),
        _importe("monto_reportado"),
        _texto("version_motor", 32, nullable=False),
        _texto("despacho_id", 32, nullable=False),
        _texto("cartera_id", 32, nullable=False),
        _texto("cliente_unico", 20, nullable=False),
        _texto("signo_economico", 8, nullable=False),
        _texto("tipo_movimiento", 24, nullable=False),
        _texto("estado_conciliacion", 24, nullable=False),
        sa.Column("firma_exacta", sa.LargeBinary(), nullable=False),
        sa.CheckConstraint("observaciones >= 1", name=op.f("ck_movimiento_observaciones")),
        sa.CheckConstraint("monto_reportado <> 0", name=op.f("ck_movimiento_monto")),
        sa.CheckConstraint("octet_length(firma_exacta) = 32", name=op.f("ck_movimiento_firma")),
        sa.ForeignKeyConstraint(
            ["ejecucion_motor_pagos_id"],
            ["ejecucion_motor_pagos.id"],
            name="fk_movimiento_ejecucion",
        ),
        sa.ForeignKeyConstraint(
            ["cuenta_canonica_id"], ["cuenta_canonica.id"], name="fk_movimiento_cuenta"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_movimiento_economico_canonico")),
        sa.UniqueConstraint(
            "ejecucion_motor_pagos_id", "movimiento_id", name="uq_movimiento_ejecucion"
        ),
    )
    op.create_index(
        "ix_movimiento_movimiento_id", "movimiento_economico_canonico", ["movimiento_id"]
    )
    op.create_index(
        "ix_movimiento_cuenta",
        "movimiento_economico_canonico",
        ["despacho_id", "cartera_id", "cliente_unico", "fecha_recepcion"],
    )

    # Lo que una ejecucion concluyo de cada pago observado de su ventana. Sin id propio.
    op.create_table(
        "resultado_pago_observado",
        sa.Column("ejecucion_motor_pagos_id", sa.Integer(), nullable=False),
        sa.Column("dataset_conformado_id", sa.Integer(), nullable=False),
        sa.Column("source_row", sa.Integer(), nullable=False),
        sa.Column("movimiento_economico_canonico_id", sa.BigInteger(), nullable=True),
        sa.Column("movimiento_relacionado_id", sa.Uuid(), nullable=True),
        _texto("clasificacion", 24, nullable=False),
        _texto("estado_conciliacion", 24, nullable=False),
        sa.Column("firma_exacta", sa.LargeBinary(), nullable=False),
        sa.Column("firma_legacy", sa.LargeBinary(), nullable=False),
        sa.Column("motivos", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "octet_length(firma_exacta) = 32 AND octet_length(firma_legacy) = 32",
            name=op.f("ck_resultado_pago_firmas"),
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_motor_pagos_id"],
            ["ejecucion_motor_pagos.id"],
            name="fk_resultado_pago_ejecucion",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_conformado_id", "source_row"],
            ["pago_observado.dataset_conformado_id", "pago_observado.source_row"],
            name="fk_resultado_pago_observado",
        ),
        sa.ForeignKeyConstraint(
            ["movimiento_economico_canonico_id"],
            ["movimiento_economico_canonico.id"],
            name="fk_resultado_pago_movimiento",
        ),
        sa.PrimaryKeyConstraint(
            "ejecucion_motor_pagos_id",
            "dataset_conformado_id",
            "source_row",
            name=op.f("pk_resultado_pago_observado"),
        ),
    )
    op.create_index(
        "ix_resultado_pago_movimiento",
        "resultado_pago_observado",
        ["movimiento_economico_canonico_id"],
    )

    # Los pagos observados no cambian: ganan los indices de una ventana y de sus negativos.
    op.create_index(
        "ix_pago_observado_recepcion",
        "pago_observado",
        ["despacho_id", "cartera_id", "fecha_recepcion"],
    )
    op.create_index(
        "ix_pago_observado_negativo",
        "pago_observado",
        ["despacho_id", "cartera_id", "fecha_recepcion"],
        postgresql_where=sa.text("recuperacion_por_gestion < 0"),
    )

    # El trabajo MOTOR_PAGOS, en la misma cola durable: uno por ejecucion.
    op.add_column(
        "trabajo_orquestacion", sa.Column("ejecucion_motor_pagos_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "fk_trabajo_motor_pagos",
        "trabajo_orquestacion",
        "ejecucion_motor_pagos",
        ["ejecucion_motor_pagos_id"],
        ["id"],
    )
    op.create_unique_constraint(
        "uq_trabajo_motor_pagos", "trabajo_orquestacion", ["ejecucion_motor_pagos_id"]
    )
    _rehacer_check("ck_trabajo_orquestacion_tipo_trabajo", _tipos(TIPOS_0009))
    _rehacer_check("ck_trabajo_objetivo", OBJETIVO_0009)


def downgrade() -> None:
    # Primero se cierra lo que estaba en curso, en este orden: la ejecucion (si un worker la tiene
    # bloqueada, termina antes de que el UPDATE la tome) y despues su trabajo. Despues se van los
    # trabajos MOTOR_PAGOS, que la 0008 no sabria guardar, y la interpretacion entera.
    op.execute(CERRAR_EJECUCIONES)
    op.execute(CERRAR_TRABAJOS)
    op.execute(sa.text("DELETE FROM trabajo_orquestacion WHERE tipo = 'MOTOR_PAGOS'"))
    _rehacer_check("ck_trabajo_objetivo", OBJETIVO_0008)
    _rehacer_check("ck_trabajo_orquestacion_tipo_trabajo", _tipos(TIPOS_0008))
    op.drop_constraint("uq_trabajo_motor_pagos", "trabajo_orquestacion", type_="unique")
    op.drop_constraint("fk_trabajo_motor_pagos", "trabajo_orquestacion", type_="foreignkey")
    op.drop_column("trabajo_orquestacion", "ejecucion_motor_pagos_id")

    op.drop_index(
        "ix_pago_observado_negativo",
        table_name="pago_observado",
        postgresql_where=sa.text("recuperacion_por_gestion < 0"),
    )
    op.drop_index("ix_pago_observado_recepcion", table_name="pago_observado")
    op.drop_index("ix_resultado_pago_movimiento", table_name="resultado_pago_observado")
    op.drop_table("resultado_pago_observado")
    op.drop_index("ix_movimiento_cuenta", table_name="movimiento_economico_canonico")
    op.drop_index("ix_movimiento_movimiento_id", table_name="movimiento_economico_canonico")
    op.drop_table("movimiento_economico_canonico")
    for indice, estado in (
        ("ux_ejecucion_motor_pagos_en_proceso", "EN_PROCESO"),
        ("ux_ejecucion_motor_pagos_exitosa", "EXITOSA"),
    ):
        op.drop_index(
            indice,
            table_name="ejecucion_motor_pagos",
            postgresql_where=sa.text(f"estado = '{estado}'"),
        )
    op.drop_table("ejecucion_motor_pagos")
