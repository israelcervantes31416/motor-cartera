"""Collection Analytics: las ejecuciones de analytics/v1, su manifiesto de entradas, sus agregados
y sus controles, y su trabajo ANALYTICS

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-14 08:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

# Como en las demas migraciones, cada nombre se midio contra los 63 caracteres de PostgreSQL y es
# parte del contrato del esquema.
#
# La 0015 solo crea estructuras: no analiza nada ni hace backfill. Una ejecucion se pide por la API
# o el CLI, con su manifiesto declarado. Nada de lo anterior cambia: ni ruteo/v2, ni campo/v2, ni
# territorial/v2, ni la geografia, ni decision/v2, ni el lifecycle, ni los pagos, ni la historia.

TIPOS_0014 = (
    "INGESTA",
    "DECISION",
    "TERRITORIAL",
    "RUTEO",
    "INGESTA_PAGOS",
    "HISTORIA",
    "MOTOR_PAGOS",
    "ATRIBUCION",
    "EVALUACION_PROMESAS",
    "DECISION_V2",
    "GEOGRAFIA",
    "TERRITORIAL_V2",
    "CAMPO_V2",
    "RUTEO_V2",
)
TIPOS_0015 = (*TIPOS_0014, "ANALYTICS")
OBJETIVO_0014 = (
    "(tipo = 'INGESTA') = (corrida_id IS NOT NULL) "
    "AND (tipo = 'DECISION') = (ejecucion_decision_id IS NOT NULL) "
    "AND (tipo = 'TERRITORIAL') = (ejecucion_territorial_id IS NOT NULL) "
    "AND (tipo = 'RUTEO') = (ejecucion_ruteo_id IS NOT NULL) "
    "AND (tipo = 'INGESTA_PAGOS') = (ingesta_pagos_id IS NOT NULL) "
    "AND (tipo = 'HISTORIA') = (ejecucion_historia_id IS NOT NULL) "
    "AND (tipo = 'MOTOR_PAGOS') = (ejecucion_motor_pagos_id IS NOT NULL) "
    "AND (tipo = 'ATRIBUCION') = (ejecucion_atribucion_id IS NOT NULL) "
    "AND (tipo = 'EVALUACION_PROMESAS') = (ejecucion_evaluacion_promesas_id IS NOT NULL) "
    "AND (tipo = 'DECISION_V2') = (ejecucion_decision_v2_id IS NOT NULL) "
    "AND (tipo = 'GEOGRAFIA') = (ejecucion_geografia_id IS NOT NULL) "
    "AND (tipo = 'TERRITORIAL_V2') = (ejecucion_territorial_v2_id IS NOT NULL) "
    "AND (tipo = 'CAMPO_V2') = (ejecucion_campo_v2_id IS NOT NULL) "
    "AND (tipo = 'RUTEO_V2') = (ejecucion_ruteo_v2_id IS NOT NULL)"
)
OBJETIVO_0015 = OBJETIVO_0014 + " AND (tipo = 'ANALYTICS') = (ejecucion_analytics_id IS NOT NULL)"

TIPOS_ENTRADA = (
    "CORTE",
    "MOTOR_PAGOS",
    "ATRIBUCION",
    "EVALUACION_PROMESAS",
    "DECISION_V2",
    "GEOGRAFIA",
    "TERRITORIAL_V2",
    "CAMPO_V2",
    "RUTEO_V2",
)
COLUMNA_DE_LA_ENTRADA = {
    "CORTE": ("corte_canonico_id", "corte_canonico", "fk_entrada_analytics_corte"),
    "MOTOR_PAGOS": (
        "ejecucion_motor_pagos_id",
        "ejecucion_motor_pagos",
        "fk_entrada_analytics_motor_pagos",
    ),
    "ATRIBUCION": (
        "ejecucion_atribucion_id",
        "ejecucion_atribucion",
        "fk_entrada_analytics_atribucion",
    ),
    "EVALUACION_PROMESAS": (
        "ejecucion_evaluacion_promesas_id",
        "ejecucion_evaluacion_promesas",
        "fk_entrada_analytics_evaluacion",
    ),
    "DECISION_V2": (
        "ejecucion_decision_v2_id",
        "ejecucion_decision_v2",
        "fk_entrada_analytics_decision_v2",
    ),
    "GEOGRAFIA": (
        "ejecucion_geografia_id",
        "ejecucion_geografia",
        "fk_entrada_analytics_geografia",
    ),
    "TERRITORIAL_V2": (
        "ejecucion_territorial_v2_id",
        "ejecucion_territorial_v2",
        "fk_entrada_analytics_territorial_v2",
    ),
    "CAMPO_V2": ("ejecucion_campo_v2_id", "ejecucion_campo_v2", "fk_entrada_analytics_campo_v2"),
    "RUTEO_V2": ("ejecucion_ruteo_v2_id", "ejecucion_ruteo_v2", "fk_entrada_analytics_ruteo_v2"),
}
OBJETIVO_DE_LA_ENTRADA = " AND ".join(
    f"(tipo_entrada = '{tipo}') = ({columna} IS NOT NULL)"
    for tipo, (columna, _, _) in COLUMNA_DE_LA_ENTRADA.items()
)
VENTANAS_DE_ENTRADA = "('MOTOR_PAGOS', 'ATRIBUCION')"

ESTADOS_VALOR = ("DISPONIBLE", "NO_DISPONIBLE", "SUPRIMIDO", "NO_APLICA")
COHERENCIA_DEL_AGREGADO = (
    "estado_valor IN ("
    + ", ".join(f"'{e}'" for e in ESTADOS_VALOR)
    + ") AND vista IN ('ACUMULADA', 'PERIODO') "
    "AND (estado_valor = 'DISPONIBLE' OR (valor IS NULL AND numerador IS NULL "
    "AND denominador IS NULL)) "
    "AND (estado_valor <> 'SUPRIMIDO' OR poblacion_cuentas IS NULL) "
    "AND (numerador IS NULL) = (denominador IS NULL) "
    "AND (denominador IS NULL OR denominador <> 0 OR valor IS NULL) "
    "AND (denominador IS NULL OR denominador = 0 OR valor IS NOT NULL) "
    "AND (poblacion_cuentas IS NULL OR poblacion_cuentas >= 0) "
    "AND periodo_desde <= periodo_hasta AND (vista = 'PERIODO' OR NOT periodo_parcial) "
    "AND octet_length(dimension_hash) = 32 "
    "AND (dimension_set <> 'TOTAL' OR dimensiones = '{}'::jsonb)"
)
PUBLICACION_DE_ANALYTICS = (
    "(estado = 'EXITOSA' AND cuentas_cohorte > 0 AND agregados_publicados > 0 "
    "AND controles_totales > 0 AND controles_fallidos = 0 AND hash_agregados IS NOT NULL "
    "AND disponibilidad IS NOT NULL) "
    "OR (estado <> 'EXITOSA' AND agregados_publicados = 0 AND hash_agregados IS NULL)"
)

INMUTABLES = {
    "analytics_inmutable": (
        ("entrada_analytics", "agregado_analytics", "control_analytics"),
        "analytics/v1 no se reescribe",
        "Un analisis publicado no cambia: se pide otra ejecucion, con otras entradas.",
    ),
}
"""Lo publicado: un trigger por sentencia rechaza cualquier UPDATE o DELETE sobre estas tablas, con
restrict_violation. TRUNCATE no pasa por aqui: es una operacion de administracion."""


def _funcion_inmutable(nombre: str, mensaje: str, pista: str) -> str:
    return f"""
CREATE FUNCTION {nombre}() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '{mensaje}: % sobre % no se permite.', TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation', CONSTRAINT = 'tr_{nombre}',
              HINT = '{pista}';
END
$$
"""


EJECUCION_TERMINADA = """
CREATE FUNCTION analytics_terminada() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' OR OLD.estado <> 'EN_PROCESO' THEN
        RAISE EXCEPTION
            'Una ejecucion de analytics/v1 terminada no cambia: % sobre % no se permite.',
            TG_OP, TG_TABLE_NAME
            USING ERRCODE = 'restrict_violation', CONSTRAINT = 'tr_analytics_terminada',
                  HINT = 'Una ejecucion publicada o fallida es historia: se pide otra.';
    END IF;
    RETURN NEW;
END
$$
"""
"""Una ejecucion solo cambia mientras se resuelve (EN_PROCESO): al publicarse o al fallar ya no
cambia, y ninguna se borra."""

# Bajar a la 0014 quita analytics/v1 entero: sus ejecuciones, sus entradas, sus agregados, sus
# controles y sus trabajos. Se reconstruyen pidiendo otra vez cada ejecucion con su manifiesto, y
# dan lo mismo. Lo que esta en curso se cierra primero. Nada de v0.13 se toca.
CIERRE = (
    "Cerrado al bajar la base a la 0014: la v0.13 no tiene analytics/v1. ruteo/v2, campo/v2 y todo "
    "lo demas siguen intactos."
)
CERRAR_EJECUCIONES = sa.text(
    "UPDATE ejecucion_analytics SET estado = 'FALLIDA', resultado = 'CERRADA_AL_BAJAR', "
    "agregados_publicados = 0, hash_agregados = NULL, terminada_en = now(), detalle = :motivo "
    "WHERE estado = 'EN_PROCESO'"
).bindparams(motivo=CIERRE)
CERRAR_TRABAJOS = sa.text(
    "UPDATE trabajo_orquestacion SET estado = 'FALLIDO', worker_id = NULL, lease_hasta = NULL, "
    "terminado_en = now(), ultimo_error = :motivo "
    "WHERE tipo = 'ANALYTICS' AND estado IN ('PENDIENTE', 'EJECUTANDO')"
).bindparams(motivo=CIERRE)
"""Lo primero que hace bajar la 0015: cerrar las ejecuciones en curso y sus trabajos, antes de
quitar sus tablas."""


def _tipos(tipos: tuple[str, ...]) -> str:
    return "tipo IN (" + ", ".join(f"'{t}'" for t in tipos) + ")"


def _rehacer_check(nombre: str, condicion: str) -> None:
    op.drop_constraint(op.f(nombre), "trabajo_orquestacion", type_="check")
    op.create_check_constraint(op.f(nombre), "trabajo_orquestacion", condicion)


def _texto(nombre: str, largo: int | None = None, nullable: bool = True) -> sa.Column:
    return sa.Column(nombre, sqlmodel.sql.sqltypes.AutoString(length=largo), nullable=nullable)


def _vocabulario(nombre: str, largo: int, *valores: str) -> sa.Enum:
    return sa.Enum(*valores, name=nombre, native_enum=False, create_constraint=True, length=largo)


def _entero(nombre: str, nullable: bool = False) -> sa.Column:
    return sa.Column(nombre, sa.Integer(), nullable=nullable)


def _grande(nombre: str, nullable: bool = False) -> sa.Column:
    return sa.Column(nombre, sa.BigInteger(), nullable=nullable)


def _instante(nombre: str, nullable: bool = True) -> sa.Column:
    return sa.Column(nombre, sa.DateTime(timezone=True), nullable=nullable)


def _json(nombre: str, nullable: bool = False) -> sa.Column:
    return sa.Column(nombre, postgresql.JSONB(astext_type=sa.Text()), nullable=nullable)


def _numero(nombre: str) -> sa.Column:
    return sa.Column(nombre, sa.Numeric(), nullable=True)


def upgrade() -> None:
    _ejecuciones()
    _entradas()
    _agregados()
    _controles()
    _inmutables()
    _cola()


def _ejecuciones() -> None:
    op.create_table(
        "ejecucion_analytics",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("analytics_run_id", sa.Uuid(), nullable=False),
        _entero("corte_canonico_id"),
        _texto("despacho_id", 32, nullable=False),
        _texto("cartera_id", 32, nullable=False),
        sa.Column("fecha_corte", sa.Date(), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        _instante("known_at", nullable=False),
        sa.Column(
            "modo",
            _vocabulario("modo_analytics", 12, "OPERATIVO", "BACKTEST"),
            nullable=False,
        ),
        _texto("zona_horaria", 64, nullable=False),
        sa.Column(
            "granularidad",
            _vocabulario("granularidad_analytics", 12, "TOTAL", "DIA", "SEMANA_ISO", "MES"),
            nullable=False,
        ),
        _entero("horizonte_dias"),
        _texto("version_motor", 32, nullable=False),
        _texto("version_catalogo", 40, nullable=False),
        _texto("hash_catalogo", 64, nullable=False),
        _texto("version_politica", 40, nullable=False),
        _texto("hash_politica", 64, nullable=False),
        _texto("hash_manifiesto", 64, nullable=False),
        _texto("firma_inputs", 64, nullable=False),
        sa.Column(
            "estado",
            _vocabulario("estado_analytics", 12, "EN_PROCESO", "EXITOSA", "FALLIDA"),
            nullable=False,
        ),
        _texto("resultado", 40),
        _grande("cuentas_cohorte"),
        _grande("agregados_publicados"),
        _entero("controles_totales"),
        _entero("controles_fallidos"),
        _texto("hash_agregados", 64),
        _json("disponibilidad", nullable=True),
        _instante("iniciada_en", nullable=False),
        _instante("terminada_en"),
        _texto("detalle"),
        sa.CheckConstraint(
            "as_of >= fecha_corte AND horizonte_dias = (as_of - fecha_corte) + 1",
            name=op.f("ck_analytics_horizonte"),
        ),
        sa.CheckConstraint(
            "hash_catalogo ~ '^[0-9a-f]{64}$' AND hash_politica ~ '^[0-9a-f]{64}$' "
            "AND hash_manifiesto ~ '^[0-9a-f]{64}$' AND firma_inputs ~ '^[0-9a-f]{64}$' "
            "AND (hash_agregados IS NULL OR hash_agregados ~ '^[0-9a-f]{64}$')",
            name=op.f("ck_analytics_firmas"),
        ),
        sa.CheckConstraint(
            "(estado = 'EN_PROCESO') = (resultado IS NULL)", name=op.f("ck_analytics_resultado")
        ),
        sa.CheckConstraint(
            "cuentas_cohorte >= 0 AND agregados_publicados >= 0 AND controles_totales >= 0 "
            "AND controles_fallidos >= 0 AND controles_fallidos <= controles_totales",
            name=op.f("ck_analytics_conteos"),
        ),
        sa.CheckConstraint(PUBLICACION_DE_ANALYTICS, name=op.f("ck_analytics_publicacion")),
        sa.ForeignKeyConstraint(
            ["corte_canonico_id", "fecha_corte"],
            ["corte_canonico.id", "corte_canonico.fecha_corte"],
            name="fk_analytics_corte",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ejecucion_analytics")),
        sa.UniqueConstraint(
            "analytics_run_id", name=op.f("uq_ejecucion_analytics_analytics_run_id")
        ),
    )
    for indice, estado in (
        ("ux_analytics_exitosa", "EXITOSA"),
        ("ux_analytics_en_proceso", "EN_PROCESO"),
    ):
        op.create_index(
            indice,
            "ejecucion_analytics",
            ["firma_inputs"],
            unique=True,
            postgresql_where=sa.text(f"estado = '{estado}'"),
        )
    op.create_index(
        "ix_analytics_corte", "ejecucion_analytics", ["corte_canonico_id", "as_of", "id"]
    )


def _entradas() -> None:
    op.create_table(
        "entrada_analytics",
        _entero("ejecucion_analytics_id"),
        _texto("tipo_entrada", 24, nullable=False),
        sa.Column("referencia_publica", sa.Uuid(), nullable=False),
        *(_entero(columna, nullable=True) for columna, _, _ in COLUMNA_DE_LA_ENTRADA.values()),
        _texto("version", 40, nullable=False),
        _texto("firma", 64, nullable=False),
        sa.Column("periodo_desde", sa.Date(), nullable=True),
        sa.Column("periodo_hasta", sa.Date(), nullable=True),
        _instante("disponible_en", nullable=False),
        _json("metadatos"),
        sa.CheckConstraint(
            "tipo_entrada IN (" + ", ".join(f"'{t}'" for t in TIPOS_ENTRADA) + ")",
            name=op.f("ck_entrada_analytics_tipo"),
        ),
        sa.CheckConstraint(OBJETIVO_DE_LA_ENTRADA, name=op.f("ck_entrada_analytics_objetivo")),
        sa.CheckConstraint(
            "firma ~ '^[0-9a-f]{64}$' AND (periodo_desde IS NULL OR periodo_hasta IS NULL "
            f"OR periodo_desde <= periodo_hasta) AND (tipo_entrada NOT IN {VENTANAS_DE_ENTRADA} "
            "OR (periodo_desde IS NOT NULL AND periodo_hasta IS NOT NULL))",
            name=op.f("ck_entrada_analytics_firma"),
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_analytics_id"],
            ["ejecucion_analytics.id"],
            name="fk_entrada_analytics_ejecucion",
        ),
        *(
            sa.ForeignKeyConstraint([columna], [f"{tabla}.id"], name=nombre)
            for columna, tabla, nombre in COLUMNA_DE_LA_ENTRADA.values()
        ),
        sa.PrimaryKeyConstraint(
            "ejecucion_analytics_id",
            "tipo_entrada",
            "referencia_publica",
            name=op.f("pk_entrada_analytics"),
        ),
    )
    op.create_index(
        "ux_entrada_analytics_ventana",
        "entrada_analytics",
        ["ejecucion_analytics_id", "tipo_entrada", "periodo_desde"],
        unique=True,
        postgresql_where=sa.text(f"tipo_entrada IN {VENTANAS_DE_ENTRADA}"),
    )
    op.create_index(
        "ux_entrada_analytics_unica",
        "entrada_analytics",
        ["ejecucion_analytics_id", "tipo_entrada"],
        unique=True,
        postgresql_where=sa.text(f"tipo_entrada NOT IN {VENTANAS_DE_ENTRADA}"),
    )


def _agregados() -> None:
    op.create_table(
        "agregado_analytics",
        _entero("ejecucion_analytics_id"),
        _texto("metrica_codigo", 64, nullable=False),
        _texto("vista", 12, nullable=False),
        _texto("dimension_set", 24, nullable=False),
        sa.Column("periodo_desde", sa.Date(), nullable=False),
        sa.Column("dimension_hash", sa.LargeBinary(), nullable=False),
        sa.Column("periodo_hasta", sa.Date(), nullable=False),
        sa.Column("periodo_parcial", sa.Boolean(), nullable=False),
        _json("dimensiones"),
        _texto("estado_valor", 16, nullable=False),
        _grande("poblacion_cuentas", nullable=True),
        _numero("valor"),
        _numero("numerador"),
        _numero("denominador"),
        _json("motivos"),
        sa.CheckConstraint(COHERENCIA_DEL_AGREGADO, name=op.f("ck_agregado_analytics_valor")),
        sa.ForeignKeyConstraint(
            ["ejecucion_analytics_id"],
            ["ejecucion_analytics.id"],
            name="fk_agregado_analytics_ejecucion",
        ),
        sa.PrimaryKeyConstraint(
            "ejecucion_analytics_id",
            "metrica_codigo",
            "vista",
            "dimension_set",
            "periodo_desde",
            "dimension_hash",
            name=op.f("pk_agregado_analytics"),
        ),
    )
    op.create_index(
        "ix_agregado_analytics_conjunto",
        "agregado_analytics",
        ["ejecucion_analytics_id", "dimension_set", "vista", "periodo_desde"],
    )


def _controles() -> None:
    op.create_table(
        "control_analytics",
        _entero("ejecucion_analytics_id"),
        _texto("codigo_control", 80, nullable=False),
        _texto("estado", 8, nullable=False),
        _numero("esperado"),
        _numero("observado"),
        _numero("diferencia"),
        _texto("detalle", 500, nullable=False),
        sa.CheckConstraint(
            "estado IN ('OK', 'FALLO') AND (esperado IS NULL) = (observado IS NULL) "
            "AND (diferencia IS NULL) = (esperado IS NULL) "
            "AND (diferencia IS NULL OR diferencia = observado - esperado) "
            "AND (diferencia IS NULL OR (estado = 'OK') = (diferencia = 0))",
            name=op.f("ck_control_analytics_estado"),
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_analytics_id"],
            ["ejecucion_analytics.id"],
            name="fk_control_analytics_ejecucion",
        ),
        sa.PrimaryKeyConstraint(
            "ejecucion_analytics_id", "codigo_control", name=op.f("pk_control_analytics")
        ),
    )


def _inmutables() -> None:
    for funcion, (tablas, mensaje, pista) in INMUTABLES.items():
        op.execute(sa.text(_funcion_inmutable(funcion, mensaje, pista)))
        for tabla in tablas:
            op.execute(
                sa.text(
                    f"CREATE TRIGGER tr_{funcion} BEFORE UPDATE OR DELETE ON {tabla} "
                    f"FOR EACH STATEMENT EXECUTE FUNCTION {funcion}()"
                )
            )
    op.execute(sa.text(EJECUCION_TERMINADA))
    op.execute(
        sa.text(
            "CREATE TRIGGER tr_analytics_terminada BEFORE UPDATE OR DELETE ON ejecucion_analytics "
            "FOR EACH ROW EXECUTE FUNCTION analytics_terminada()"
        )
    )


def _cola() -> None:
    # El trabajo ANALYTICS, en la misma cola durable: uno por ejecucion. Cabe en los 24 caracteres
    # de tipo; solo se rehacen los CHECK del tipo y del objetivo.
    op.add_column(
        "trabajo_orquestacion", sa.Column("ejecucion_analytics_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "fk_trabajo_analytics",
        "trabajo_orquestacion",
        "ejecucion_analytics",
        ["ejecucion_analytics_id"],
        ["id"],
    )
    op.create_unique_constraint(
        "uq_trabajo_analytics", "trabajo_orquestacion", ["ejecucion_analytics_id"]
    )
    _rehacer_check("ck_trabajo_orquestacion_tipo_trabajo", _tipos(TIPOS_0015))
    _rehacer_check("ck_trabajo_objetivo", OBJETIVO_0015)


def downgrade() -> None:
    # Primero se cierra lo que estaba en curso: las ejecuciones (si un worker tiene una bloqueada,
    # termina antes de que el UPDATE la tome) y despues sus trabajos. Despues se van esos trabajos,
    # que la 0014 no sabria guardar, y las estructuras de la 0015.
    op.execute(CERRAR_EJECUCIONES)
    op.execute(CERRAR_TRABAJOS)
    op.execute(sa.text("DELETE FROM trabajo_orquestacion WHERE tipo = 'ANALYTICS'"))
    _rehacer_check("ck_trabajo_objetivo", OBJETIVO_0014)
    _rehacer_check("ck_trabajo_orquestacion_tipo_trabajo", _tipos(TIPOS_0014))
    op.drop_constraint("uq_trabajo_analytics", "trabajo_orquestacion", type_="unique")
    op.drop_constraint("fk_trabajo_analytics", "trabajo_orquestacion", type_="foreignkey")
    op.drop_column("trabajo_orquestacion", "ejecucion_analytics_id")

    op.drop_table("control_analytics")
    op.drop_index("ix_agregado_analytics_conjunto", table_name="agregado_analytics")
    op.drop_table("agregado_analytics")
    for indice, condicion in (
        ("ux_entrada_analytics_unica", f"tipo_entrada NOT IN {VENTANAS_DE_ENTRADA}"),
        ("ux_entrada_analytics_ventana", f"tipo_entrada IN {VENTANAS_DE_ENTRADA}"),
    ):
        op.drop_index(indice, table_name="entrada_analytics", postgresql_where=sa.text(condicion))
    op.drop_table("entrada_analytics")
    op.drop_index("ix_analytics_corte", table_name="ejecucion_analytics")
    for indice, estado in (
        ("ux_analytics_en_proceso", "EN_PROCESO"),
        ("ux_analytics_exitosa", "EXITOSA"),
    ):
        op.drop_index(
            indice,
            table_name="ejecucion_analytics",
            postgresql_where=sa.text(f"estado = '{estado}'"),
        )
    op.drop_table("ejecucion_analytics")
    for funcion in (*INMUTABLES, "analytics_terminada"):
        op.execute(sa.text(f"DROP FUNCTION {funcion}()"))
