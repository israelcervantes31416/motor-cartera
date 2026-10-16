"""Predictive Intelligence: los entrenamientos de predictive/model-v1 con sus anclas y las
interpretaciones de sus labels, las versiones del modelo con sus componentes, los scorings con sus
predicciones por cuenta y su drift, y los trabajos PREDICTIVE_TRAINING y PREDICTIVE_SCORING

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-15 08:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

# Como en las demas migraciones, cada nombre se midio contra los 63 caracteres de PostgreSQL y es
# parte del contrato del esquema.
#
# La 0016 solo crea estructuras: no entrena, no puntua ni hace backfill. Un entrenamiento se pide
# con su manifiesto declarado, y un scoring con una version PUBLICADA declarada. Nada de lo anterior
# cambia: ni analytics/v1, ni ruteo/v2, ni campo/v2, ni territorial/v2, ni la geografia, ni
# decision/v2, ni el lifecycle, ni los pagos, ni la historia.

TIPOS_0015 = (
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
    "ANALYTICS",
)
TIPOS_0016 = (*TIPOS_0015, "PREDICTIVE_TRAINING", "PREDICTIVE_SCORING")
OBJETIVO_0015 = (
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
    "AND (tipo = 'RUTEO_V2') = (ejecucion_ruteo_v2_id IS NOT NULL) "
    "AND (tipo = 'ANALYTICS') = (ejecucion_analytics_id IS NOT NULL)"
)
OBJETIVO_0016 = (
    OBJETIVO_0015
    + " AND (tipo = 'PREDICTIVE_TRAINING') = (ejecucion_entrenamiento_predictivo_id IS NOT NULL)"
    + " AND (tipo = 'PREDICTIVE_SCORING') = (ejecucion_scoring_predictivo_id IS NOT NULL)"
)

PROBABILIDADES = ("p_recuperacion_positiva_30d", "p_contacto_14d", "p_promesa_30d")
RANGOS_DE_LA_PREDICCION = (
    " AND ".join(f"{p} BETWEEN 0 AND 1" for p in PROBABILIDADES)
    + " AND riesgo_sin_recuperacion_30d = 1 - p_recuperacion_positiva_30d"
    + " AND recuperacion_positiva_esperada_30d >= 0"
    + " AND recuperacion_positiva_esperada_30d <> 'NaN'::numeric"
    + " AND octet_length(feature_hash) = 32"
)
TARGETS = (
    "RECUPERACION_POSITIVA_30D",
    "CONTACTO_14D",
    "PROMESA_30D",
    "RECUPERACION_POSITIVA_MONTO_30D",
)
SPLITS = ("TRAIN", "VALIDATION", "TEST", "PURGE")
PUBLICACION_DEL_ENTRENAMIENTO = (
    "(estado = 'EN_PROCESO' AND resultado IS NULL AND dataset_hash IS NULL) "
    "OR (estado = 'EXITOSA' AND resultado IN ('MODELO_PUBLICADO', 'MODELO_RECHAZADO') "
    "AND dataset_hash IS NOT NULL AND ejemplos_train > 0) "
    "OR (estado = 'FALLIDA' AND resultado IS NOT NULL "
    "AND resultado NOT IN ('MODELO_PUBLICADO', 'MODELO_RECHAZADO'))"
)
CONTEOS_DEL_ENTRENAMIENTO = (
    "anclas >= 0 AND ejemplos_total >= 0 AND ejemplos_train >= 0 AND ejemplos_validation >= 0 "
    "AND ejemplos_test >= 0 AND ejemplos_purge >= 0 AND ejemplos_censurados >= 0 "
    "AND ejemplos_train + ejemplos_validation + ejemplos_test + ejemplos_purge = ejemplos_total "
    "AND ejemplos_censurados <= ejemplos_total"
)
PUBLICACION_DEL_SCORING = (
    "(estado = 'EXITOSA' AND cuentas_objetivo > 0 "
    "AND predicciones_publicadas = cuentas_objetivo "
    "AND hash_predicciones IS NOT NULL AND hash_drift IS NOT NULL) "
    "OR (estado <> 'EXITOSA' AND predicciones_publicadas = 0 "
    "AND hash_predicciones IS NULL AND hash_drift IS NULL)"
)

INMUTABLES = (
    "ancla_entrenamiento_predictivo",
    "interpretacion_entrenamiento_predictivo",
    "componente_modelo_predictivo",
    "prediccion_cuenta",
    "drift_predictivo",
)
"""Lo declarado y lo publicado: un trigger por sentencia rechaza cualquier UPDATE o DELETE, con
restrict_violation. TRUNCATE no pasa por aqui: es una operacion de administracion."""

PREDICTIVO_INMUTABLE = """
CREATE FUNCTION predictivo_inmutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'predictive/model-v1 no se reescribe: % sobre % no se permite.',
        TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation', CONSTRAINT = 'tr_predictivo_inmutable',
              HINT = 'Lo declarado y lo publicado no cambia: se pide otro entrenamiento o scoring.';
END
$$
"""


def _terminada(nombre: str, que: str) -> str:
    return f"""
CREATE FUNCTION {nombre}() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' OR OLD.estado <> 'EN_PROCESO' THEN
        RAISE EXCEPTION '{que} terminado no cambia: % sobre % no se permite.',
            TG_OP, TG_TABLE_NAME
            USING ERRCODE = 'restrict_violation', CONSTRAINT = 'tr_{nombre}',
                  HINT = 'Una ejecucion publicada o fallida es historia: se pide otra.';
    END IF;
    RETURN NEW;
END
$$
"""


MODELO_CICLO = """
CREATE FUNCTION modelo_predictivo_ciclo() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Una version de predictive/model-v1 no se borra.'
            USING ERRCODE = 'restrict_violation', CONSTRAINT = 'tr_modelo_predictivo_ciclo';
    END IF;
    IF NOT ((OLD.estado = 'CANDIDATA' AND NEW.estado IN ('VALIDADA', 'RECHAZADA'))
            OR (OLD.estado = 'VALIDADA' AND NEW.estado IN ('PUBLICADA', 'RECHAZADA')))
       OR (to_jsonb(NEW) - 'estado' - 'publicada_en')
          IS DISTINCT FROM (to_jsonb(OLD) - 'estado' - 'publicada_en') THEN
        RAISE EXCEPTION USING
            MESSAGE = format('Una version de predictive/model-v1 solo avanza de CANDIDATA a '
                || 'VALIDADA y de ahi a PUBLICADA, o a RECHAZADA, sin cambiar nada mas: %s a %s '
                || 'no se permite.', OLD.estado, NEW.estado),
            ERRCODE = 'restrict_violation', CONSTRAINT = 'tr_modelo_predictivo_ciclo',
            HINT = 'Un modelo publicado no cambia: se entrena otra version.';
    END IF;
    RETURN NEW;
END
$$
"""
"""Una version solo avanza CANDIDATA -> VALIDADA -> PUBLICADA, o a RECHAZADA, y nada mas cambia;
publicada o rechazada ya no cambia, y ninguna se borra."""

MODELO_COMPLETO = """
CREATE FUNCTION modelo_predictivo_completo() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    actual text;
    componentes integer;
    validados integer;
BEGIN
    SELECT estado INTO actual FROM version_modelo_predictivo WHERE id = NEW.id;
    SELECT count(*), count(*) FILTER (WHERE estado = 'VALIDADO') INTO componentes, validados
        FROM componente_modelo_predictivo WHERE version_modelo_predictivo_id = NEW.id;
    IF actual IN ('CANDIDATA', 'VALIDADA') OR componentes <> 4
       OR (actual = 'PUBLICADA' AND validados <> 4)
       OR (actual = 'RECHAZADA' AND validados = 4) THEN
        RAISE EXCEPTION USING
            MESSAGE = format('Una version de predictive/model-v1 se confirma PUBLICADA con sus '
                || 'cuatro componentes VALIDADO, o RECHAZADA con sus cuatro componentes y alguno '
                || 'RECHAZADO: esta queda %s con %s componentes, %s validados.', actual,
                componentes, validados),
            ERRCODE = 'check_violation', CONSTRAINT = 'tr_modelo_predictivo_completo';
    END IF;
    RETURN NULL;
END
$$
"""
"""Al confirmar, todo o nada: una version PUBLICADA tiene exactamente sus cuatro componentes, todos
VALIDADO; una RECHAZADA, sus cuatro, alguno RECHAZADO; y ninguna se queda a la mitad."""

# Bajar a la 0015 quita Predictive Intelligence entero: sus entrenamientos, sus versiones, sus
# componentes, sus scorings, sus predicciones, su drift y sus trabajos. Se reconstruyen pidiendo
# otra vez cada entrenamiento con su manifiesto y cada scoring con su version, y dan lo mismo. Lo
# que esta en curso se cierra primero. Nada de v0.14 se toca.
CIERRE = (
    "Cerrado al bajar la base a la 0015: la v0.14 no tiene predictive/model-v1. analytics/v1, "
    "ruteo/v2 y todo lo demas siguen intactos."
)
CERRAR_ENTRENAMIENTOS = sa.text(
    "UPDATE ejecucion_entrenamiento_predictivo SET estado = 'FALLIDA', "
    "resultado = 'CERRADA_AL_BAJAR', terminada_en = now(), detalle = :motivo "
    "WHERE estado = 'EN_PROCESO'"
).bindparams(motivo=CIERRE)
CERRAR_SCORINGS = sa.text(
    "UPDATE ejecucion_scoring_predictivo SET estado = 'FALLIDA', resultado = 'CERRADA_AL_BAJAR', "
    "predicciones_publicadas = 0, hash_predicciones = NULL, hash_drift = NULL, "
    "terminada_en = now(), detalle = :motivo WHERE estado = 'EN_PROCESO'"
).bindparams(motivo=CIERRE)
CERRAR_TRABAJOS = sa.text(
    "UPDATE trabajo_orquestacion SET estado = 'FALLIDO', worker_id = NULL, lease_hasta = NULL, "
    "terminado_en = now(), ultimo_error = :motivo "
    "WHERE tipo IN ('PREDICTIVE_TRAINING', 'PREDICTIVE_SCORING') "
    "AND estado IN ('PENDIENTE', 'EJECUTANDO')"
).bindparams(motivo=CIERRE)
"""Lo primero que hace bajar la 0016: cerrar los entrenamientos y los scorings en curso y sus
trabajos, antes de quitar sus tablas."""


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


def _fecha(nombre: str, nullable: bool = False) -> sa.Column:
    return sa.Column(nombre, sa.Date(), nullable=nullable)


def _json(nombre: str, nullable: bool = False) -> sa.Column:
    return sa.Column(nombre, postgresql.JSONB(astext_type=sa.Text()), nullable=nullable)


def _hash(nombre: str, nullable: bool = False) -> sa.Column:
    return _texto(nombre, 64, nullable=nullable)


def upgrade() -> None:
    _entrenamientos()
    _modelos()
    _scorings()
    _inmutables()
    _cola()


def _entrenamientos() -> None:
    op.create_table(
        "ejecucion_entrenamiento_predictivo",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("training_run_id", sa.Uuid(), nullable=False),
        _texto("despacho_id", 32, nullable=False),
        _texto("cartera_id", 32, nullable=False),
        _texto("zona_horaria", 64, nullable=False),
        _texto("version_modelo", 32, nullable=False),
        _texto("version_features", 40, nullable=False),
        _hash("hash_features"),
        _texto("version_targets", 40, nullable=False),
        _hash("hash_targets"),
        _texto("version_politica", 40, nullable=False),
        _hash("hash_politica"),
        _json("manifiesto"),
        _hash("hash_manifiesto"),
        _hash("firma_inputs"),
        _hash("dataset_hash", nullable=True),
        sa.Column(
            "estado",
            _vocabulario("estado_entrenamiento", 12, "EN_PROCESO", "EXITOSA", "FALLIDA"),
            nullable=False,
        ),
        _texto("resultado", 40),
        _entero("anclas"),
        _grande("ejemplos_total"),
        _grande("ejemplos_train"),
        _grande("ejemplos_validation"),
        _grande("ejemplos_test"),
        _grande("ejemplos_purge"),
        _grande("ejemplos_censurados"),
        _json("conteos", nullable=True),
        _json("backtest", nullable=True),
        _json("tiempos", nullable=True),
        _instante("iniciada_en", nullable=False),
        _instante("terminada_en"),
        _texto("detalle"),
        sa.CheckConstraint(
            "hash_features ~ '^[0-9a-f]{64}$' AND hash_targets ~ '^[0-9a-f]{64}$' "
            "AND hash_politica ~ '^[0-9a-f]{64}$' AND hash_manifiesto ~ '^[0-9a-f]{64}$' "
            "AND firma_inputs ~ '^[0-9a-f]{64}$' "
            "AND (dataset_hash IS NULL OR dataset_hash ~ '^[0-9a-f]{64}$')",
            name=op.f("ck_entrenamiento_predictivo_firmas"),
        ),
        sa.CheckConstraint(
            CONTEOS_DEL_ENTRENAMIENTO, name=op.f("ck_entrenamiento_predictivo_conteos")
        ),
        sa.CheckConstraint(
            PUBLICACION_DEL_ENTRENAMIENTO, name=op.f("ck_entrenamiento_predictivo_publicacion")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ejecucion_entrenamiento_predictivo")),
        sa.UniqueConstraint(
            "training_run_id",
            name=op.f("uq_ejecucion_entrenamiento_predictivo_training_run_id"),
        ),
    )
    for indice, estado in (
        ("ux_entrenamiento_predictivo_exitosa", "EXITOSA"),
        ("ux_entrenamiento_predictivo_en_proceso", "EN_PROCESO"),
    ):
        op.create_index(
            indice,
            "ejecucion_entrenamiento_predictivo",
            ["firma_inputs"],
            unique=True,
            postgresql_where=sa.text(f"estado = '{estado}'"),
        )
    op.create_index(
        "ix_entrenamiento_predictivo_estado", "ejecucion_entrenamiento_predictivo", ["estado", "id"]
    )

    op.create_table(
        "ancla_entrenamiento_predictivo",
        _entero("ejecucion_entrenamiento_predictivo_id"),
        _entero("corte_canonico_id"),
        _fecha("feature_as_of"),
        _instante("feature_known_at", nullable=False),
        _instante("label_known_at", nullable=False),
        _fecha("pagos_completos_hasta", nullable=True),
        _fecha("lifecycle_completo_hasta", nullable=True),
        _texto("split", 12, nullable=False),
        _hash("hash_ancla"),
        sa.CheckConstraint(
            "split IN (" + ", ".join(f"'{s}'" for s in SPLITS) + ")",
            name=op.f("ck_ancla_predictiva_split"),
        ),
        sa.CheckConstraint(
            "feature_known_at <= label_known_at AND hash_ancla ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_ancla_predictiva_relojes"),
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_entrenamiento_predictivo_id"],
            ["ejecucion_entrenamiento_predictivo.id"],
            name="fk_ancla_predictiva_entrenamiento",
        ),
        sa.ForeignKeyConstraint(
            ["corte_canonico_id", "feature_as_of"],
            ["corte_canonico.id", "corte_canonico.fecha_corte"],
            name="fk_ancla_predictiva_corte",
        ),
        sa.PrimaryKeyConstraint(
            "ejecucion_entrenamiento_predictivo_id",
            "corte_canonico_id",
            name=op.f("pk_ancla_entrenamiento_predictivo"),
        ),
    )

    op.create_table(
        "interpretacion_entrenamiento_predictivo",
        _entero("ejecucion_entrenamiento_predictivo_id"),
        _fecha("periodo_desde"),
        _entero("ejecucion_motor_pagos_id"),
        sa.ForeignKeyConstraint(
            ["ejecucion_entrenamiento_predictivo_id"],
            ["ejecucion_entrenamiento_predictivo.id"],
            name="fk_interpretacion_predictiva_entrenamiento",
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_motor_pagos_id"],
            ["ejecucion_motor_pagos.id"],
            name="fk_interpretacion_predictiva_motor_pagos",
        ),
        sa.PrimaryKeyConstraint(
            "ejecucion_entrenamiento_predictivo_id",
            "periodo_desde",
            name=op.f("pk_interpretacion_entrenamiento_predictivo"),
        ),
        sa.UniqueConstraint(
            "ejecucion_entrenamiento_predictivo_id",
            "ejecucion_motor_pagos_id",
            name="uq_interpretacion_predictiva_ejecucion",
        ),
    )


def _modelos() -> None:
    op.create_table(
        "version_modelo_predictivo",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("modelo_version_id", sa.Uuid(), nullable=False),
        _texto("version_modelo", 32, nullable=False),
        _entero("ejecucion_entrenamiento_predictivo_id"),
        _texto("version_features", 40, nullable=False),
        _hash("hash_features"),
        _texto("version_targets", 40, nullable=False),
        _hash("hash_targets"),
        _texto("version_politica", 40, nullable=False),
        _hash("hash_politica"),
        _hash("dataset_hash"),
        _hash("hash_modelo"),
        sa.Column(
            "estado",
            _vocabulario(
                "estado_modelo_predictivo", 12, "CANDIDATA", "VALIDADA", "PUBLICADA", "RECHAZADA"
            ),
            nullable=False,
        ),
        _instante("creada_en", nullable=False),
        _instante("publicada_en"),
        _fecha("periodo_train_desde"),
        _fecha("periodo_train_hasta"),
        _fecha("periodo_validation_desde"),
        _fecha("periodo_validation_hasta"),
        _fecha("periodo_test_desde"),
        _fecha("periodo_test_hasta"),
        _json("referencia_drift"),
        _json("metadatos"),
        sa.CheckConstraint(
            "hash_features ~ '^[0-9a-f]{64}$' AND hash_targets ~ '^[0-9a-f]{64}$' "
            "AND hash_politica ~ '^[0-9a-f]{64}$' AND dataset_hash ~ '^[0-9a-f]{64}$' "
            "AND hash_modelo ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_modelo_predictivo_firmas"),
        ),
        sa.CheckConstraint(
            "(estado = 'PUBLICADA') = (publicada_en IS NOT NULL)",
            name=op.f("ck_modelo_predictivo_publicada"),
        ),
        sa.CheckConstraint(
            "periodo_train_desde <= periodo_train_hasta "
            "AND periodo_train_hasta < periodo_validation_desde "
            "AND periodo_validation_desde <= periodo_validation_hasta "
            "AND periodo_validation_hasta < periodo_test_desde "
            "AND periodo_test_desde <= periodo_test_hasta",
            name=op.f("ck_modelo_predictivo_periodos"),
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_entrenamiento_predictivo_id"],
            ["ejecucion_entrenamiento_predictivo.id"],
            name="fk_modelo_predictivo_entrenamiento",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_version_modelo_predictivo")),
        sa.UniqueConstraint(
            "modelo_version_id", name=op.f("uq_version_modelo_predictivo_modelo_version_id")
        ),
        sa.UniqueConstraint(
            "ejecucion_entrenamiento_predictivo_id", name="uq_modelo_predictivo_entrenamiento"
        ),
    )
    op.create_index(
        "ix_modelo_predictivo_estado", "version_modelo_predictivo", ["estado", "creada_en"]
    )

    op.create_table(
        "componente_modelo_predictivo",
        _entero("version_modelo_predictivo_id"),
        _texto("target", 40, nullable=False),
        _texto("algoritmo", 24, nullable=False),
        _json("hiperparametros"),
        _json("artefacto"),
        _hash("hash_artefacto"),
        _texto("calibracion", 8, nullable=False),
        _json("metricas_validation"),
        _json("metricas_test"),
        _json("baseline_test"),
        _json("bootstrap_test"),
        _json("explicacion_global"),
        _texto("estado", 12, nullable=False),
        _json("compuertas"),
        sa.CheckConstraint(
            "target IN (" + ", ".join(f"'{t}'" for t in TARGETS) + ") "
            "AND algoritmo IN ('LOGISTICA_L2', 'TWEEDIE_LOG') "
            "AND calibracion IN ('RAW', 'PLATT') AND estado IN ('VALIDADO', 'RECHAZADO') "
            "AND hash_artefacto ~ '^[0-9a-f]{64}$' "
            "AND ((target = 'RECUPERACION_POSITIVA_MONTO_30D') = (algoritmo = 'TWEEDIE_LOG'))",
            name=op.f("ck_componente_predictivo_coherencia"),
        ),
        sa.ForeignKeyConstraint(
            ["version_modelo_predictivo_id"],
            ["version_modelo_predictivo.id"],
            name="fk_componente_predictivo_modelo",
        ),
        sa.PrimaryKeyConstraint(
            "version_modelo_predictivo_id", "target", name=op.f("pk_componente_modelo_predictivo")
        ),
    )


def _scorings() -> None:
    op.create_table(
        "ejecucion_scoring_predictivo",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("scoring_run_id", sa.Uuid(), nullable=False),
        _entero("version_modelo_predictivo_id"),
        _entero("corte_canonico_id"),
        _texto("despacho_id", 32, nullable=False),
        _texto("cartera_id", 32, nullable=False),
        _fecha("as_of"),
        _instante("known_at", nullable=False),
        sa.Column(
            "modo",
            _vocabulario("modo_scoring_predictivo", 12, "OPERATIVO", "BACKTEST"),
            nullable=False,
        ),
        _texto("zona_horaria", 64, nullable=False),
        _texto("version_features", 40, nullable=False),
        _hash("hash_features"),
        _hash("firma_inputs"),
        sa.Column(
            "estado",
            _vocabulario("estado_scoring_predictivo", 12, "EN_PROCESO", "EXITOSA", "FALLIDA"),
            nullable=False,
        ),
        _texto("resultado", 40),
        _grande("cuentas_objetivo"),
        _grande("predicciones_publicadas"),
        _hash("hash_predicciones", nullable=True),
        _hash("hash_drift", nullable=True),
        _json("disponibilidad", nullable=True),
        _json("drift_resumen", nullable=True),
        _json("tiempos", nullable=True),
        _instante("iniciada_en", nullable=False),
        _instante("terminada_en"),
        _texto("detalle"),
        sa.CheckConstraint(
            "firma_inputs ~ '^[0-9a-f]{64}$' AND hash_features ~ '^[0-9a-f]{64}$' "
            "AND (hash_predicciones IS NULL OR hash_predicciones ~ '^[0-9a-f]{64}$') "
            "AND (hash_drift IS NULL OR hash_drift ~ '^[0-9a-f]{64}$')",
            name=op.f("ck_scoring_predictivo_firmas"),
        ),
        sa.CheckConstraint(
            "(estado = 'EN_PROCESO') = (resultado IS NULL) AND cuentas_objetivo >= 0 "
            "AND predicciones_publicadas >= 0",
            name=op.f("ck_scoring_predictivo_resultado"),
        ),
        sa.CheckConstraint(PUBLICACION_DEL_SCORING, name=op.f("ck_scoring_predictivo_publicacion")),
        sa.ForeignKeyConstraint(
            ["version_modelo_predictivo_id"],
            ["version_modelo_predictivo.id"],
            name="fk_scoring_predictivo_modelo",
        ),
        sa.ForeignKeyConstraint(
            ["corte_canonico_id", "as_of"],
            ["corte_canonico.id", "corte_canonico.fecha_corte"],
            name="fk_scoring_predictivo_corte",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ejecucion_scoring_predictivo")),
        sa.UniqueConstraint(
            "scoring_run_id", name=op.f("uq_ejecucion_scoring_predictivo_scoring_run_id")
        ),
        sa.UniqueConstraint("id", "corte_canonico_id", name="uq_scoring_predictivo_id_corte"),
    )
    for indice, estado in (
        ("ux_scoring_predictivo_exitosa", "EXITOSA"),
        ("ux_scoring_predictivo_en_proceso", "EN_PROCESO"),
    ):
        op.create_index(
            indice,
            "ejecucion_scoring_predictivo",
            ["firma_inputs"],
            unique=True,
            postgresql_where=sa.text(f"estado = '{estado}'"),
        )
    for indice, columnas in (
        ("ix_scoring_predictivo_corte", ["corte_canonico_id", "id"]),
        ("ix_scoring_predictivo_modelo", ["version_modelo_predictivo_id", "id"]),
        ("ix_scoring_predictivo_estado", ["estado", "id"]),
    ):
        op.create_index(indice, "ejecucion_scoring_predictivo", columnas)

    op.create_table(
        "prediccion_cuenta",
        _entero("ejecucion_scoring_predictivo_id"),
        _entero("cuenta_canonica_id"),
        _entero("corte_canonico_id"),
        *(sa.Column(p, sa.Numeric(precision=9, scale=8), nullable=False) for p in PROBABILIDADES),
        sa.Column("riesgo_sin_recuperacion_30d", sa.Numeric(precision=9, scale=8), nullable=False),
        sa.Column(
            "recuperacion_positiva_esperada_30d", sa.Numeric(precision=14, scale=2), nullable=False
        ),
        _texto("estado", 16, nullable=False),
        _json("motivos"),
        sa.Column("feature_hash", sa.LargeBinary(), nullable=False),
        sa.CheckConstraint(RANGOS_DE_LA_PREDICCION, name=op.f("ck_prediccion_cuenta_rangos")),
        sa.CheckConstraint(
            "estado IN ('COMPLETA', 'CON_IMPUTACION')", name=op.f("ck_prediccion_cuenta_estado")
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_scoring_predictivo_id", "corte_canonico_id"],
            ["ejecucion_scoring_predictivo.id", "ejecucion_scoring_predictivo.corte_canonico_id"],
            name="fk_prediccion_scoring",
        ),
        sa.ForeignKeyConstraint(
            ["corte_canonico_id", "cuenta_canonica_id"],
            ["snapshot_cuenta.corte_canonico_id", "snapshot_cuenta.cuenta_canonica_id"],
            name="fk_prediccion_snapshot",
        ),
        sa.PrimaryKeyConstraint(
            "ejecucion_scoring_predictivo_id",
            "cuenta_canonica_id",
            name=op.f("pk_prediccion_cuenta"),
        ),
    )
    for indice, columnas in (
        ("ix_prediccion_recuperacion", ["p_recuperacion_positiva_30d"]),
        ("ix_prediccion_contacto", ["p_contacto_14d"]),
        ("ix_prediccion_promesa", ["p_promesa_30d"]),
        ("ix_prediccion_esperada", ["recuperacion_positiva_esperada_30d"]),
    ):
        op.create_index(
            indice,
            "prediccion_cuenta",
            ["ejecucion_scoring_predictivo_id", *columnas, "cuenta_canonica_id"],
        )
    op.create_index(
        "ix_prediccion_cuenta",
        "prediccion_cuenta",
        ["cuenta_canonica_id", "ejecucion_scoring_predictivo_id"],
    )

    op.create_table(
        "drift_predictivo",
        _entero("ejecucion_scoring_predictivo_id"),
        _texto("tipo", 12, nullable=False),
        _texto("nombre", 64, nullable=False),
        _texto("clase", 16, nullable=False),
        sa.Column("psi", sa.Numeric(precision=14, scale=6), nullable=False),
        _texto("severidad", 10, nullable=False),
        sa.Column("faltantes_train", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("faltantes_scoring", sa.Numeric(precision=9, scale=6), nullable=True),
        _texto("severidad_faltantes", 10),
        _json("bins"),
        sa.CheckConstraint(
            "tipo IN ('FEATURE', 'SCORE') AND psi >= 0 "
            "AND severidad IN ('BAJO', 'MODERADO', 'ALTO') "
            "AND (severidad_faltantes IS NULL OR severidad_faltantes IN "
            "('BAJO', 'MODERADO', 'ALTO')) "
            "AND (faltantes_train IS NULL) = (faltantes_scoring IS NULL) "
            "AND (faltantes_train IS NULL) = (severidad_faltantes IS NULL)",
            name=op.f("ck_drift_predictivo_valores"),
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_scoring_predictivo_id"],
            ["ejecucion_scoring_predictivo.id"],
            name="fk_drift_scoring",
        ),
        sa.PrimaryKeyConstraint(
            "ejecucion_scoring_predictivo_id", "tipo", "nombre", name=op.f("pk_drift_predictivo")
        ),
    )


def _inmutables() -> None:
    op.execute(sa.text(PREDICTIVO_INMUTABLE))
    for tabla in INMUTABLES:
        op.execute(
            sa.text(
                f"CREATE TRIGGER tr_predictivo_inmutable BEFORE UPDATE OR DELETE ON {tabla} "
                "FOR EACH STATEMENT EXECUTE FUNCTION predictivo_inmutable()"
            )
        )
    for tabla, funcion, que in (
        (
            "ejecucion_entrenamiento_predictivo",
            "entrenamiento_predictivo_terminado",
            "Un entrenamiento",
        ),
        ("ejecucion_scoring_predictivo", "scoring_predictivo_terminado", "Un scoring"),
    ):
        op.execute(sa.text(_terminada(funcion, que)))
        op.execute(
            sa.text(
                f"CREATE TRIGGER tr_{funcion} BEFORE UPDATE OR DELETE ON {tabla} "
                f"FOR EACH ROW EXECUTE FUNCTION {funcion}()"
            )
        )
    op.execute(sa.text(MODELO_CICLO))
    op.execute(
        sa.text(
            "CREATE TRIGGER tr_modelo_predictivo_ciclo BEFORE UPDATE OR DELETE "
            "ON version_modelo_predictivo FOR EACH ROW EXECUTE FUNCTION modelo_predictivo_ciclo()"
        )
    )
    op.execute(sa.text(MODELO_COMPLETO))
    op.execute(
        sa.text(
            "CREATE CONSTRAINT TRIGGER tr_modelo_predictivo_completo AFTER INSERT OR UPDATE "
            "ON version_modelo_predictivo DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
            "EXECUTE FUNCTION modelo_predictivo_completo()"
        )
    )


def _cola() -> None:
    # Los trabajos PREDICTIVE_TRAINING y PREDICTIVE_SCORING, en la misma cola durable: uno por
    # ejecucion. Caben en los 24 caracteres de tipo; solo se rehacen los CHECK del tipo y del
    # objetivo.
    for columna, tabla, llave, unica in (
        (
            "ejecucion_entrenamiento_predictivo_id",
            "ejecucion_entrenamiento_predictivo",
            "fk_trabajo_entrenamiento_predictivo",
            "uq_trabajo_entrenamiento_predictivo",
        ),
        (
            "ejecucion_scoring_predictivo_id",
            "ejecucion_scoring_predictivo",
            "fk_trabajo_scoring_predictivo",
            "uq_trabajo_scoring_predictivo",
        ),
    ):
        op.add_column("trabajo_orquestacion", sa.Column(columna, sa.Integer(), nullable=True))
        op.create_foreign_key(llave, "trabajo_orquestacion", tabla, [columna], ["id"])
        op.create_unique_constraint(unica, "trabajo_orquestacion", [columna])
    _rehacer_check("ck_trabajo_orquestacion_tipo_trabajo", _tipos(TIPOS_0016))
    _rehacer_check("ck_trabajo_objetivo", OBJETIVO_0016)


def downgrade() -> None:
    # Primero se cierra lo que estaba en curso: los entrenamientos y los scorings (si un worker
    # tiene uno bloqueado, termina antes de que el UPDATE lo tome) y despues sus trabajos. Despues
    # se van esos trabajos, que la 0015 no sabria guardar, y las estructuras de la 0016.
    op.execute(CERRAR_ENTRENAMIENTOS)
    op.execute(CERRAR_SCORINGS)
    op.execute(CERRAR_TRABAJOS)
    op.execute(
        sa.text(
            "DELETE FROM trabajo_orquestacion "
            "WHERE tipo IN ('PREDICTIVE_TRAINING', 'PREDICTIVE_SCORING')"
        )
    )
    _rehacer_check("ck_trabajo_objetivo", OBJETIVO_0015)
    _rehacer_check("ck_trabajo_orquestacion_tipo_trabajo", _tipos(TIPOS_0015))
    for columna, llave, unica in (
        (
            "ejecucion_scoring_predictivo_id",
            "fk_trabajo_scoring_predictivo",
            "uq_trabajo_scoring_predictivo",
        ),
        (
            "ejecucion_entrenamiento_predictivo_id",
            "fk_trabajo_entrenamiento_predictivo",
            "uq_trabajo_entrenamiento_predictivo",
        ),
    ):
        op.drop_constraint(unica, "trabajo_orquestacion", type_="unique")
        op.drop_constraint(llave, "trabajo_orquestacion", type_="foreignkey")
        op.drop_column("trabajo_orquestacion", columna)

    op.drop_table("drift_predictivo")
    for indice in (
        "ix_prediccion_cuenta",
        "ix_prediccion_esperada",
        "ix_prediccion_promesa",
        "ix_prediccion_contacto",
        "ix_prediccion_recuperacion",
    ):
        op.drop_index(indice, table_name="prediccion_cuenta")
    op.drop_table("prediccion_cuenta")
    for indice in (
        "ix_scoring_predictivo_estado",
        "ix_scoring_predictivo_modelo",
        "ix_scoring_predictivo_corte",
    ):
        op.drop_index(indice, table_name="ejecucion_scoring_predictivo")
    for indice, estado in (
        ("ux_scoring_predictivo_en_proceso", "EN_PROCESO"),
        ("ux_scoring_predictivo_exitosa", "EXITOSA"),
    ):
        op.drop_index(
            indice,
            table_name="ejecucion_scoring_predictivo",
            postgresql_where=sa.text(f"estado = '{estado}'"),
        )
    op.drop_table("ejecucion_scoring_predictivo")
    op.drop_table("componente_modelo_predictivo")
    op.drop_index("ix_modelo_predictivo_estado", table_name="version_modelo_predictivo")
    op.drop_table("version_modelo_predictivo")
    op.drop_table("interpretacion_entrenamiento_predictivo")
    op.drop_table("ancla_entrenamiento_predictivo")
    op.drop_index(
        "ix_entrenamiento_predictivo_estado", table_name="ejecucion_entrenamiento_predictivo"
    )
    for indice, estado in (
        ("ux_entrenamiento_predictivo_en_proceso", "EN_PROCESO"),
        ("ux_entrenamiento_predictivo_exitosa", "EXITOSA"),
    ):
        op.drop_index(
            indice,
            table_name="ejecucion_entrenamiento_predictivo",
            postgresql_where=sa.text(f"estado = '{estado}'"),
        )
    op.drop_table("ejecucion_entrenamiento_predictivo")
    for funcion in (
        "modelo_predictivo_completo",
        "modelo_predictivo_ciclo",
        "scoring_predictivo_terminado",
        "entrenamiento_predictivo_terminado",
        "predictivo_inmutable",
    ):
        op.execute(sa.text(f"DROP FUNCTION {funcion}()"))
