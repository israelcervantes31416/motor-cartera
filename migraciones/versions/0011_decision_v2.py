"""decision/v2: ejecucion, features por cuenta, resultado por cuenta y su trabajo DECISION_V2

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-10 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

# Como en las demas migraciones, cada nombre se midio contra los 63 caracteres de PostgreSQL y es
# parte del contrato del esquema: sale en los IntegrityError y una migracion futura se refiere a el.
#
# La 0011 solo crea estructuras: no decide nada ni hace backfill. Las decisiones v2 las piden
# POST /decisiones-v2 y `motor-cartera decidir-v2`. Nada de decision/v1 cambia: ni su tabla, ni su
# enum, ni su trabajo DECISION, ni el flujo que lo encadena.

TIPOS_0010 = (
    "INGESTA",
    "DECISION",
    "TERRITORIAL",
    "RUTEO",
    "INGESTA_PAGOS",
    "HISTORIA",
    "MOTOR_PAGOS",
    "ATRIBUCION",
    "EVALUACION_PROMESAS",
)
TIPOS_0011 = (*TIPOS_0010, "DECISION_V2")
OBJETIVO_0010 = (
    "(tipo = 'INGESTA') = (corrida_id IS NOT NULL) "
    "AND (tipo = 'DECISION') = (ejecucion_decision_id IS NOT NULL) "
    "AND (tipo = 'TERRITORIAL') = (ejecucion_territorial_id IS NOT NULL) "
    "AND (tipo = 'RUTEO') = (ejecucion_ruteo_id IS NOT NULL) "
    "AND (tipo = 'INGESTA_PAGOS') = (ingesta_pagos_id IS NOT NULL) "
    "AND (tipo = 'HISTORIA') = (ejecucion_historia_id IS NOT NULL) "
    "AND (tipo = 'MOTOR_PAGOS') = (ejecucion_motor_pagos_id IS NOT NULL) "
    "AND (tipo = 'ATRIBUCION') = (ejecucion_atribucion_id IS NOT NULL) "
    "AND (tipo = 'EVALUACION_PROMESAS') = (ejecucion_evaluacion_promesas_id IS NOT NULL)"
)
OBJETIVO_0011 = OBJETIVO_0010 + (
    " AND (tipo = 'DECISION_V2') = (ejecucion_decision_v2_id IS NOT NULL)"
)

CONTEOS = (
    "cuentas_objetivo >= 0 AND cuentas_decididas >= 0 AND cuentas_fallidas >= 0 "
    "AND cuentas_decididas <= cuentas_objetivo AND cuentas_fallidas <= cuentas_objetivo"
)
PUBLICACION = (
    "(estado = 'EXITOSA' AND cuentas_objetivo > 0 AND cuentas_decididas = cuentas_objetivo "
    "AND cuentas_fallidas = 0) OR (estado <> 'EXITOSA' AND cuentas_decididas = 0)"
)
NIVELES = (
    "score_nivel_base BETWEEN 1 AND 4 AND score_nivel_final BETWEEN 1 AND 4 "
    "AND score_nivel_final = least(4, greatest(1, score_nivel_base + ajuste_total)) "
    "AND score_nivel_base = CASE prioridad_base WHEN 'BAJA' THEN 1 WHEN 'MEDIA' THEN 2 "
    "WHEN 'ALTA' THEN 3 WHEN 'MUY_ALTA' THEN 4 END "
    "AND score_nivel_final = CASE prioridad_final WHEN 'BAJA' THEN 1 WHEN 'MEDIA' THEN 2 "
    "WHEN 'ALTA' THEN 3 WHEN 'MUY_ALTA' THEN 4 END"
)
CONTEOS_FEATURES = (
    "cortes_observados >= 1 AND reingresos_observados >= 0 AND gestiones_7d >= 0 "
    "AND gestiones_7d <= gestiones_30d AND gestiones_sin_contacto_7d <= gestiones_7d "
    "AND contactos_titular_30d + contactos_tercero_30d <= gestiones_contactables_30d "
    "AND gestiones_contactables_30d <= gestiones_30d AND visitas_30d <= gestiones_30d "
    "AND promesas_30d <= promesas_90d AND promesas_incumplidas_30d <= "
    "promesas_incumplidas_90d AND convenios_90d >= 0 "
    "AND (dias_desde_ultima_gestion IS NULL OR dias_desde_ultima_gestion >= 0) "
    "AND (dias_desde_ultimo_pago IS NULL OR dias_desde_ultimo_pago >= 0)"
)
TASA = (
    "(tasa_contacto_titular_30d IS NULL) = (gestiones_contactables_30d = 0) "
    "AND (tasa_contacto_titular_30d IS NULL OR tasa_contacto_titular_30d BETWEEN 0 AND 1)"
)

ENTEROS_NO_NULOS = (
    "cortes_observados",
    "reingresos_observados",
)
GESTIONES = (
    "gestiones_7d",
    "gestiones_30d",
    "gestiones_sin_contacto_7d",
    "contactos_titular_30d",
    "contactos_tercero_30d",
    "visitas_30d",
    "gestiones_contactables_30d",
    "promesas_30d",
    "promesas_90d",
    "promesas_cumplidas_90d",
    "promesas_parciales_90d",
    "promesas_incumplidas_30d",
    "promesas_incumplidas_90d",
    "convenios_90d",
)
ENTEROS_NULOS = (
    "dias_desde_ultimo_pago",
    "movimientos_pago_7d",
    "movimientos_pago_30d",
    "pagos_asociados_unicos_30d",
)
RECUPERACIONES = (
    "recuperacion_interpretada_7d",
    "recuperacion_interpretada_30d",
    "recuperacion_interpretada_90d",
    "recuperacion_asociada_unica_30d",
)

TABLAS_INMUTABLES = ("feature_decision_cuenta", "resultado_decision_v2")
"""Lo que una decision publico: un trigger rechaza cualquier UPDATE o DELETE sobre ellas."""

INMUTABLE = """
CREATE FUNCTION decision_v2_inmutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'decision/v2 no se reescribe: % sobre % no se permite.', TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation', CONSTRAINT = 'tr_decision_v2_inmutable',
              HINT = 'Una decision publicada no cambia: se pide otra, con otro known_at.';
END
$$
"""
"""Un UPDATE o un DELETE sobre las features o los resultados de una decision, aunque no toque
ninguna fila, falla con restrict_violation: lo que se decidio ayer no cambia en silencio. TRUNCATE
no pasa por aqui: es una operacion de administracion, no de la aplicacion."""

# Bajar a la 0010 quita decision/v2 entera: sus ejecuciones, sus features y sus resultados, que no
# se reconstruyen solos (se vuelven a pedir con su corte, su as_of y su known_at, y dan lo mismo).
# Lo que esta en curso se cierra primero. Nada de v0.9 se toca: ni el lifecycle, ni la atribucion,
# ni las promesas, ni los pagos, ni la historia, ni la cartera, ni los artefactos, ni decision/v1,
# territorial/v1 o ruteo/v1.
CIERRE = (
    "Cerrada al bajar la base a la 0010: la v0.9 no tiene decision/v2. decision/v1 y todo lo demas "
    "siguen intactos."
)
CERRAR_DECISIONES = sa.text(
    "UPDATE ejecucion_decision_v2 SET estado = 'FALLIDA', resultado = 'CERRADA_AL_BAJAR', "
    "cuentas_decididas = 0, terminada_en = now(), detalle = :motivo WHERE estado = 'EN_PROCESO'"
).bindparams(motivo=CIERRE)
CERRAR_TRABAJOS = sa.text(
    "UPDATE trabajo_orquestacion SET estado = 'FALLIDO', worker_id = NULL, lease_hasta = NULL, "
    "terminado_en = now(), ultimo_error = :motivo "
    "WHERE tipo = 'DECISION_V2' AND estado IN ('PENDIENTE', 'EJECUTANDO')"
).bindparams(motivo=CIERRE)
"""Lo primero que hace bajar la 0011: cerrar las decisiones v2 en curso y sus trabajos, antes de
quitar sus tablas."""


def _tipos(tipos: tuple[str, ...]) -> str:
    return "tipo IN (" + ", ".join(f"'{t}'" for t in tipos) + ")"


def _rehacer_check(nombre: str, condicion: str) -> None:
    op.drop_constraint(op.f(nombre), "trabajo_orquestacion", type_="check")
    op.create_check_constraint(op.f(nombre), "trabajo_orquestacion", condicion)


def _texto(nombre: str, largo: int | None = None, nullable: bool = True) -> sa.Column:
    return sa.Column(nombre, sqlmodel.sql.sqltypes.AutoString(length=largo), nullable=nullable)


def _importe(nombre: str, digitos: int, nullable: bool = True) -> sa.Column:
    return sa.Column(nombre, sa.Numeric(precision=digitos, scale=2), nullable=nullable)


def _vocabulario(nombre: str, largo: int, *valores: str) -> sa.Enum:
    return sa.Enum(*valores, name=nombre, native_enum=False, create_constraint=True, length=largo)


def upgrade() -> None:
    _ejecucion()
    _features()
    _resultados()
    _inmutables()
    _cola()


def _inmutables() -> None:
    op.execute(sa.text(INMUTABLE))
    for tabla in TABLAS_INMUTABLES:
        op.execute(
            sa.text(
                f"CREATE TRIGGER tr_decision_v2_inmutable BEFORE UPDATE OR DELETE ON {tabla} "
                "FOR EACH STATEMENT EXECUTE FUNCTION decision_v2_inmutable()"
            )
        )


def _ejecucion() -> None:
    op.create_table(
        "ejecucion_decision_v2",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("decision_run_id", sa.Uuid(), nullable=False),
        sa.Column("corte_canonico_id", sa.Integer(), nullable=False),
        _texto("despacho_id", 32, nullable=False),
        _texto("cartera_id", 32, nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("known_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "modo", _vocabulario("modo_decision_v2", 12, "OPERATIVO", "BACKTEST"), nullable=False
        ),
        _texto("zona_horaria", 64, nullable=False),
        _texto("version_motor", 32, nullable=False),
        _texto("version_features", 32, nullable=False),
        _texto("version_politica", 40, nullable=False),
        _texto("hash_politica", 64, nullable=False),
        _texto("firma_inputs", 64, nullable=False),
        sa.Column(
            "estado",
            _vocabulario("estado_decision_v2", 12, "EN_PROCESO", "EXITOSA", "FALLIDA"),
            nullable=False,
        ),
        _texto("resultado", 40),
        sa.Column("cuentas_objetivo", sa.BigInteger(), nullable=False),
        sa.Column("cuentas_decididas", sa.BigInteger(), nullable=False),
        sa.Column("cuentas_fallidas", sa.BigInteger(), nullable=False),
        sa.Column("disponibilidad", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("distribucion", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("iniciada_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("terminada_en", sa.DateTime(timezone=True), nullable=True),
        _texto("detalle"),
        sa.CheckConstraint(
            "firma_inputs ~ '^[0-9a-f]{64}$' AND hash_politica ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_decision_v2_firmas"),
        ),
        sa.CheckConstraint(
            "(estado = 'EN_PROCESO') = (resultado IS NULL)", name=op.f("ck_decision_v2_resultado")
        ),
        sa.CheckConstraint(CONTEOS, name=op.f("ck_decision_v2_conteos")),
        sa.CheckConstraint(PUBLICACION, name=op.f("ck_decision_v2_publicacion")),
        sa.ForeignKeyConstraint(
            ["corte_canonico_id"], ["corte_canonico.id"], name="fk_decision_v2_corte"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ejecucion_decision_v2")),
        sa.UniqueConstraint(
            "decision_run_id", name=op.f("uq_ejecucion_decision_v2_decision_run_id")
        ),
        sa.UniqueConstraint("id", "corte_canonico_id", name="uq_decision_v2_id_corte"),
    )
    op.create_index(
        "ux_decision_v2_exitosa",
        "ejecucion_decision_v2",
        ["firma_inputs"],
        unique=True,
        postgresql_where=sa.text("estado = 'EXITOSA'"),
    )
    op.create_index(
        "ux_decision_v2_en_proceso",
        "ejecucion_decision_v2",
        ["corte_canonico_id", "version_features", "version_politica"],
        unique=True,
        postgresql_where=sa.text("estado = 'EN_PROCESO'"),
    )
    op.create_index("ix_decision_v2_corte", "ejecucion_decision_v2", ["corte_canonico_id", "id"])


def _features() -> None:
    # Las features exactas de cada cuenta, sin id propio: su llave es la natural. Las columnas de 8
    # bytes primero.
    op.create_table(
        "feature_decision_cuenta",
        sa.Column("dias_atraso", sa.BigInteger(), nullable=False),
        sa.Column("semanas_atraso", sa.BigInteger(), nullable=True),
        sa.Column("delta_dias_atraso_desde_corte_anterior", sa.BigInteger(), nullable=True),
        sa.Column("ejecucion_decision_v2_id", sa.Integer(), nullable=False),
        sa.Column("cuenta_canonica_id", sa.Integer(), nullable=False),
        sa.Column("corte_canonico_id", sa.Integer(), nullable=False),
        *(sa.Column(nombre, sa.Integer(), nullable=False) for nombre in ENTEROS_NO_NULOS),
        sa.Column("dias_desde_ultima_gestion", sa.Integer(), nullable=True),
        *(sa.Column(nombre, sa.Integer(), nullable=False) for nombre in GESTIONES),
        *(sa.Column(nombre, sa.Integer(), nullable=True) for nombre in ENTEROS_NULOS),
        _importe("saldo_total", 14, nullable=False),
        _importe("saldo_atrasado", 14),
        _importe("saldo_requerido", 14),
        _importe("moratorios", 14),
        _importe("delta_saldo_total_desde_corte_anterior", 14),
        sa.Column("tasa_contacto_titular_30d", sa.Numeric(precision=5, scale=4), nullable=True),
        *(_importe(nombre, 18) for nombre in RECUPERACIONES),
        sa.Column(
            "dias_gestion_a_pago_mediana_90d", sa.Numeric(precision=7, scale=2), nullable=True
        ),
        _texto("producto", 20, nullable=False),
        _texto("estrategia"),
        _texto("estatus_plan"),
        _texto("ultima_gestion_canal", 12),
        _texto("ultima_gestion_nivel_contacto", 20),
        sa.Column("continuo_desde_corte_anterior", sa.Boolean(), nullable=True),
        sa.Column("promesa_vigente", sa.Boolean(), nullable=False),
        sa.Column("convenio_vigente", sa.Boolean(), nullable=False),
        sa.Column("feature_hash", sa.LargeBinary(), nullable=False),
        sa.CheckConstraint(
            "octet_length(feature_hash) = 32", name=op.f("ck_feature_decision_hash")
        ),
        sa.CheckConstraint(CONTEOS_FEATURES, name=op.f("ck_feature_decision_conteos")),
        sa.CheckConstraint(TASA, name=op.f("ck_feature_decision_tasa")),
        sa.ForeignKeyConstraint(
            ["ejecucion_decision_v2_id", "corte_canonico_id"],
            ["ejecucion_decision_v2.id", "ejecucion_decision_v2.corte_canonico_id"],
            name="fk_feature_decision_ejecucion",
        ),
        sa.ForeignKeyConstraint(
            ["corte_canonico_id", "cuenta_canonica_id"],
            ["snapshot_cuenta.corte_canonico_id", "snapshot_cuenta.cuenta_canonica_id"],
            name="fk_feature_decision_snapshot",
        ),
        sa.PrimaryKeyConstraint(
            "ejecucion_decision_v2_id",
            "cuenta_canonica_id",
            name=op.f("pk_feature_decision_cuenta"),
        ),
    )


def _resultados() -> None:
    op.create_table(
        "resultado_decision_v2",
        sa.Column("ejecucion_decision_v2_id", sa.Integer(), nullable=False),
        sa.Column("cuenta_canonica_id", sa.Integer(), nullable=False),
        sa.Column("score_nivel_base", sa.SmallInteger(), nullable=False),
        sa.Column("ajuste_total", sa.SmallInteger(), nullable=False),
        sa.Column("score_nivel_final", sa.SmallInteger(), nullable=False),
        _texto("prioridad_base", 12, nullable=False),
        _texto("prioridad_final", 12, nullable=False),
        _texto("canal_recomendado", 20, nullable=False),
        sa.Column("motivos", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("decision_hash", sa.LargeBinary(), nullable=False),
        sa.CheckConstraint(NIVELES, name=op.f("ck_resultado_decision_v2_niveles")),
        sa.CheckConstraint(
            "octet_length(decision_hash) = 32", name=op.f("ck_resultado_decision_v2_hash")
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_decision_v2_id", "cuenta_canonica_id"],
            [
                "feature_decision_cuenta.ejecucion_decision_v2_id",
                "feature_decision_cuenta.cuenta_canonica_id",
            ],
            name="fk_resultado_decision_v2_feature",
        ),
        sa.PrimaryKeyConstraint(
            "ejecucion_decision_v2_id",
            "cuenta_canonica_id",
            name=op.f("pk_resultado_decision_v2"),
        ),
    )
    op.create_index(
        "ix_resultado_decision_v2_prioridad",
        "resultado_decision_v2",
        ["ejecucion_decision_v2_id", "prioridad_final", "cuenta_canonica_id"],
    )
    op.create_index(
        "ix_resultado_decision_v2_canal",
        "resultado_decision_v2",
        ["ejecucion_decision_v2_id", "canal_recomendado", "cuenta_canonica_id"],
    )
    op.create_index(
        "ix_resultado_decision_v2_cuenta",
        "resultado_decision_v2",
        ["cuenta_canonica_id", "ejecucion_decision_v2_id"],
    )


def _cola() -> None:
    # El trabajo DECISION_V2, en la misma cola durable: uno por ejecucion. DECISION_V2 cabe en los
    # 24 caracteres de tipo desde la 0010; solo se rehacen los CHECK del tipo y del objetivo.
    op.add_column(
        "trabajo_orquestacion", sa.Column("ejecucion_decision_v2_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "fk_trabajo_decision_v2",
        "trabajo_orquestacion",
        "ejecucion_decision_v2",
        ["ejecucion_decision_v2_id"],
        ["id"],
    )
    op.create_unique_constraint(
        "uq_trabajo_decision_v2", "trabajo_orquestacion", ["ejecucion_decision_v2_id"]
    )
    _rehacer_check("ck_trabajo_orquestacion_tipo_trabajo", _tipos(TIPOS_0011))
    _rehacer_check("ck_trabajo_objetivo", OBJETIVO_0011)


def downgrade() -> None:
    # Primero se cierra lo que estaba en curso, en este orden: las ejecuciones (si un worker tiene
    # una bloqueada, termina antes de que el UPDATE la tome) y despues sus trabajos. Despues se van
    # esos trabajos, que la 0010 no sabria guardar, y las estructuras de la 0011.
    op.execute(CERRAR_DECISIONES)
    op.execute(CERRAR_TRABAJOS)
    op.execute(sa.text("DELETE FROM trabajo_orquestacion WHERE tipo = 'DECISION_V2'"))
    _rehacer_check("ck_trabajo_objetivo", OBJETIVO_0010)
    _rehacer_check("ck_trabajo_orquestacion_tipo_trabajo", _tipos(TIPOS_0010))
    op.drop_constraint("uq_trabajo_decision_v2", "trabajo_orquestacion", type_="unique")
    op.drop_constraint("fk_trabajo_decision_v2", "trabajo_orquestacion", type_="foreignkey")
    op.drop_column("trabajo_orquestacion", "ejecucion_decision_v2_id")

    for indice in (
        "ix_resultado_decision_v2_cuenta",
        "ix_resultado_decision_v2_canal",
        "ix_resultado_decision_v2_prioridad",
    ):
        op.drop_index(indice, table_name="resultado_decision_v2")
    op.drop_table("resultado_decision_v2")
    op.drop_table("feature_decision_cuenta")
    op.drop_index("ix_decision_v2_corte", table_name="ejecucion_decision_v2")
    for indice, estado in (
        ("ux_decision_v2_en_proceso", "EN_PROCESO"),
        ("ux_decision_v2_exitosa", "EXITOSA"),
    ):
        op.drop_index(
            indice,
            table_name="ejecucion_decision_v2",
            postgresql_where=sa.text(f"estado = '{estado}'"),
        )
    op.drop_table("ejecucion_decision_v2")
    op.execute(sa.text("DROP FUNCTION decision_v2_inmutable()"))
