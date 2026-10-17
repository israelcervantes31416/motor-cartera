"""Optimizacion Matematica: las versiones de la configuracion de optimizacion con su matriz de
admisibilidad, sus pools de capacidad y sus restricciones; las ejecuciones de optimization/v1 con
su resultado por cuenta, el uso de cada pool y cada restriccion con su holgura; y el trabajo
OPTIMIZATION

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-17 08:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

# Como en las demas migraciones, cada nombre se midio contra los 63 caracteres de PostgreSQL y es
# parte del contrato del esquema.
#
# La 0017 solo crea estructuras: no optimiza ni hace backfill. Una ejecucion se pide con sus
# entradas declaradas (un scoring, una decision/v2, una configuracion PUBLICADA y, si usa CAMPO o
# BASE, un ruteo/v2). Nada de lo anterior cambia: ni predictive/model-v1, ni analytics/v1, ni
# ruteo/v2, ni campo/v2, ni territorial/v2, ni la geografia, ni decision/v2, ni el lifecycle, ni
# los pagos, ni la historia. Las llaves foraneas compuestas de una ejecucion usan restricciones
# unicas que esas tablas ya tenian.

TIPOS_0016 = (
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
    "PREDICTIVE_TRAINING",
    "PREDICTIVE_SCORING",
)
TIPOS_0017 = (*TIPOS_0016, "OPTIMIZATION")
OBJETIVO_0016 = (
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
    "AND (tipo = 'ANALYTICS') = (ejecucion_analytics_id IS NOT NULL) "
    "AND (tipo = 'PREDICTIVE_TRAINING') = (ejecucion_entrenamiento_predictivo_id IS NOT NULL) "
    "AND (tipo = 'PREDICTIVE_SCORING') = (ejecucion_scoring_predictivo_id IS NOT NULL)"
)
OBJETIVO_0017 = (
    OBJETIVO_0016 + " AND (tipo = 'OPTIMIZATION') = (ejecucion_optimizacion_id IS NOT NULL)"
)

PATRON_CODIGO = "^[A-Z0-9_-]{1,32}$"
CANALES_OPERATIVOS = ("DIGITAL", "TELEFONICA", "CAMPO")
CANALES_DECISION = ("DIGITAL", "TELEFONICA", "CAMPO", "OTRO")
ALCANCES = ("GLOBAL", "BASE", "GESTOR_CAMPO")
TIPOS_RESTRICCION = (
    "MIN_CUENTAS",
    "MAX_CUENTAS",
    "MIN_PARTICIPACION_BPS",
    "MAX_PARTICIPACION_BPS",
)
DIMENSIONES = ("PRODUCTO", "ATRASO_BUCKET", "PRIORIDAD_DECISION", "BASE")
ESTADOS_CUENTA = ("ASIGNADA", "NO_SELECCIONADA", "NO_ELEGIBLE")
MOTIVOS_NO_ELEGIBLE = (
    "PREDICCION_CON_IMPUTACION",
    "SIN_CANAL_ADMISIBLE",
    "CONFIG_EXCLUYE_CANAL",
    "CAMPO_NO_PROGRAMADA",
    "SIN_POOL_APLICABLE",
)
ESTADOS_SOLVER = (
    "OPTIMAL",
    "INFEASIBLE",
    "TIME_LIMIT",
    "ITERATION_LIMIT",
    "UNBOUNDED",
    "INFEASIBLE_OR_UNBOUNDED",
    "NUMERICAL_ERROR",
    "SOLVER_ERROR",
    "NO_INVOCADO",
)


def _en(valores: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in valores) + ")"


COHERENCIA_DEL_POOL = (
    f"codigo ~ '{PATRON_CODIGO}' "
    f"AND canal IN {_en(CANALES_OPERATIVOS)} "
    f"AND tipo_alcance IN {_en(ALCANCES)} "
    "AND (canal = 'CAMPO') = (tipo_alcance = 'GESTOR_CAMPO') "
    "AND (tipo_alcance = 'GLOBAL') = (referencia_alcance IS NULL) "
    f"AND (referencia_alcance IS NULL OR referencia_alcance ~ '{PATRON_CODIGO}') "
    "AND (capacidad IS NOT NULL OR canal = 'CAMPO') AND (capacidad IS NULL OR capacidad >= 1) "
    "AND unidades_recurso_por_asignacion BETWEEN 1 AND 1000000"
)
VOCABULARIO_DE_LA_RESTRICCION = (
    f"codigo ~ '{PATRON_CODIGO}' "
    f"AND tipo IN {_en(TIPOS_RESTRICCION)} "
    f"AND dimension IN {_en(DIMENSIONES)} "
    "AND limite >= 0 "
    "AND (tipo NOT IN ('MIN_PARTICIPACION_BPS', 'MAX_PARTICIPACION_BPS') OR limite <= 10000) "
    "AND (dimension <> 'PRODUCTO' OR valor_dimension IN "
    "('CONSUMO', 'TARJETA', 'NOMINA', 'AUTOMOTRIZ')) "
    "AND (dimension <> 'ATRASO_BUCKET' OR valor_dimension IN "
    "('0', '1-30', '31-60', '61-90', '91+')) "
    "AND (dimension <> 'PRIORIDAD_DECISION' OR valor_dimension IN "
    "('BAJA', 'MEDIA', 'ALTA', 'MUY_ALTA')) "
    f"AND (dimension <> 'BASE' OR valor_dimension ~ '{PATRON_CODIGO}')"
)
PUBLICACION_DE_OPTIMIZACION = (
    "(estado = 'EXITOSA' AND resultado = 'PLAN_PUBLICADO' AND cuentas_universo > 0 "
    "AND cuentas_publicadas = cuentas_universo AND hash_plan IS NOT NULL "
    "AND solver_status = 'OPTIMAL' AND objetivo_primario_centavos IS NOT NULL "
    "AND objetivo_secundario IS NOT NULL AND unidades_recurso IS NOT NULL "
    "AND desempate_canonico IS NOT NULL) "
    "OR (estado <> 'EXITOSA' AND cuentas_publicadas = 0 AND cuentas_asignadas = 0 "
    "AND hash_plan IS NULL AND objetivo_primario_centavos IS NULL "
    "AND objetivo_secundario IS NULL AND unidades_recurso IS NULL AND desempate_canonico IS NULL)"
)
CONTEOS_DE_OPTIMIZACION = (
    "cuentas_universo >= 0 AND cuentas_elegibles >= 0 AND cuentas_con_imputacion >= 0 "
    "AND cuentas_retenidas >= 0 AND cuentas_asignadas >= 0 AND cuentas_publicadas >= 0 "
    "AND clases >= 0 AND variables >= 0 AND restricciones_solver >= 0 "
    "AND elementos_no_nulos >= 0 AND cuentas_elegibles <= cuentas_universo "
    "AND cuentas_con_imputacion <= cuentas_universo AND cuentas_retenidas <= cuentas_elegibles "
    "AND cuentas_asignadas <= cuentas_retenidas AND cuentas_publicadas <= cuentas_universo"
)
CADENA_DE_CAMPO = (
    "(ejecucion_ruteo_v2_id IS NULL) = (ejecucion_campo_v2_id IS NULL) "
    "AND (ejecucion_ruteo_v2_id IS NULL) = (ejecucion_territorial_v2_id IS NULL) "
    "AND (ejecucion_ruteo_v2_id IS NULL) = (version_configuracion_campo_id IS NULL) "
    "AND (ejecucion_ruteo_v2_id IS NULL) = (version_bases_operativas_id IS NULL)"
)
COHERENCIA_DEL_RESULTADO = (
    f"estado IN {_en(ESTADOS_CUENTA)} "
    "AND (estado = 'ASIGNADA') = (motivo = 'EN_SOLUCION_OPTIMA') "
    "AND (estado = 'NO_SELECCIONADA') = (motivo = 'FUERA_DE_SOLUCION_OPTIMA') "
    f"AND (estado = 'NO_ELEGIBLE') = (motivo IN {_en(MOTIVOS_NO_ELEGIBLE)}) "
    "AND (estado = 'ASIGNADA') = (pool_capacidad_optimizacion_id IS NOT NULL) "
    "AND (estado = 'ASIGNADA') = (canal_asignado IS NOT NULL) "
    "AND (estado = 'ASIGNADA') = (unidades_recurso IS NOT NULL) "
    f"AND (canal_asignado IS NULL OR canal_asignado IN {_en(CANALES_OPERATIVOS)}) "
    f"AND canal_decision IN {_en(CANALES_DECISION)} "
    "AND (canal_asignado IS NOT DISTINCT FROM 'CAMPO') = (gestor_campo_id IS NOT NULL) "
    "AND (gestor_campo_id IS NULL) = (orden_ruteo_original IS NULL) "
    "AND (orden_ruteo_original IS NULL OR orden_ruteo_original >= 1) "
    "AND (unidades_recurso IS NULL OR unidades_recurso >= 1) "
    "AND (estado <> 'NO_ELEGIBLE' OR NOT retenida) AND (estado <> 'ASIGNADA' OR retenida) "
    "AND valor_oportunidad_centavos >= 0 AND p_contacto_14d BETWEEN 0 AND 1"
)

INMUTABLES = (
    "admisibilidad_canal_optimizacion",
    "pool_capacidad_optimizacion",
    "restriccion_optimizacion",
    "resultado_optimizacion_cuenta",
    "uso_pool_optimizacion",
    "resultado_restriccion_optimizacion",
)
"""Lo declarado y lo publicado: un trigger por sentencia rechaza cualquier UPDATE o DELETE, con
restrict_violation. TRUNCATE no pasa por aqui: es una operacion de administracion."""

OPTIMIZACION_INMUTABLE = """
CREATE FUNCTION optimizacion_inmutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'optimization/v1 no se reescribe: % sobre % no se permite.',
        TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation', CONSTRAINT = 'tr_optimizacion_inmutable',
              HINT = 'Lo declarado y lo publicado no cambia: se pide otra configuracion o '
                     || 'otra ejecucion.';
END
$$
"""

OPTIMIZACION_TERMINADA = """
CREATE FUNCTION optimizacion_terminada() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' OR OLD.estado <> 'EN_PROCESO' THEN
        RAISE EXCEPTION 'Una ejecucion terminada no cambia: % sobre % no se permite.',
            TG_OP, TG_TABLE_NAME
            USING ERRCODE = 'restrict_violation', CONSTRAINT = 'tr_optimizacion_terminada',
                  HINT = 'Un plan publicado o fallido es historia: se pide otra ejecucion.';
    END IF;
    RETURN NEW;
END
$$
"""

CONFIGURACION_CICLO = """
CREATE FUNCTION configuracion_optimizacion_ciclo() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Una version de la configuracion de optimizacion no se borra.'
            USING ERRCODE = 'restrict_violation',
                  CONSTRAINT = 'tr_configuracion_optimizacion_ciclo';
    END IF;
    IF NOT (OLD.estado = 'BORRADOR' AND NEW.estado = 'PUBLICADA')
       OR (to_jsonb(NEW) - 'estado' - 'publicada_en')
          IS DISTINCT FROM (to_jsonb(OLD) - 'estado' - 'publicada_en') THEN
        RAISE EXCEPTION USING
            MESSAGE = format('Una version de la configuracion de optimizacion solo pasa de '
                || 'BORRADOR a PUBLICADA, sin cambiar nada mas: %s a %s no se permite.',
                OLD.estado, NEW.estado),
            ERRCODE = 'restrict_violation', CONSTRAINT = 'tr_configuracion_optimizacion_ciclo',
            HINT = 'Una configuracion publicada no cambia: se registra otra version.';
    END IF;
    RETURN NEW;
END
$$
"""
"""Una version solo pasa de BORRADOR a PUBLICADA (con su instante), y nada mas cambia; publicada ya
no cambia, y ninguna se borra."""

# Bajar a la 0016 quita optimization/v1 entero: sus configuraciones, sus ejecuciones, sus
# resultados y sus trabajos. Se reconstruyen registrando otra vez cada configuracion y pidiendo otra
# vez cada ejecucion con sus entradas declaradas, y dan lo mismo. Lo que esta en curso se cierra
# primero. Nada de v0.15 se toca.
CIERRE = (
    "Cerrado al bajar la base a la 0016: la v0.15 no tiene optimization/v1. predictive/model-v1, "
    "analytics/v1, ruteo/v2 y todo lo demas siguen intactos."
)
CERRAR_EJECUCIONES = sa.text(
    "UPDATE ejecucion_optimizacion SET estado = 'FALLIDA', resultado = 'CERRADA_AL_BAJAR', "
    "solver_status = coalesce(solver_status, 'NO_INVOCADO'), cuentas_publicadas = 0, "
    "cuentas_asignadas = 0, hash_plan = NULL, objetivo_primario_centavos = NULL, "
    "objetivo_secundario = NULL, unidades_recurso = NULL, desempate_canonico = NULL, "
    "terminada_en = now(), detalle = :motivo WHERE estado = 'EN_PROCESO'"
).bindparams(motivo=CIERRE)
CERRAR_TRABAJOS = sa.text(
    "UPDATE trabajo_orquestacion SET estado = 'FALLIDO', worker_id = NULL, lease_hasta = NULL, "
    "terminado_en = now(), ultimo_error = :motivo "
    "WHERE tipo = 'OPTIMIZATION' AND estado IN ('PENDIENTE', 'EJECUTANDO')"
).bindparams(motivo=CIERRE)
"""Lo primero que hace bajar la 0017: cerrar las ejecuciones en curso y sus trabajos, antes de
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


def _hash(nombre: str, nullable: bool = False) -> sa.Column:
    return _texto(nombre, 64, nullable=nullable)


def _booleano(nombre: str) -> sa.Column:
    return sa.Column(nombre, sa.Boolean(), nullable=False)


def upgrade() -> None:
    _configuraciones()
    _ejecuciones()
    _resultados()
    _inmutables()
    _cola()


def _configuraciones() -> None:
    op.create_table(
        "version_configuracion_optimizacion",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("configuracion_id", sa.Uuid(), nullable=False),
        _texto("nombre", 120, nullable=False),
        _texto("version_configuracion", 32, nullable=False),
        _hash("hash_contenido"),
        sa.Column(
            "estado",
            _vocabulario("estado_configuracion", 12, "BORRADOR", "PUBLICADA"),
            nullable=False,
        ),
        _booleano("permitir_predicciones_con_imputacion"),
        _grande("max_cuentas_atendidas", nullable=True),
        _grande("max_unidades_recurso", nullable=True),
        _entero("pools"),
        _entero("pools_activos"),
        _entero("restricciones"),
        _entero("restricciones_activas"),
        _json("metadatos"),
        _instante("creada_en", nullable=False),
        _instante("publicada_en"),
        sa.CheckConstraint(
            "hash_contenido ~ '^[0-9a-f]{64}$'", name=op.f("ck_configuracion_optimizacion_hash")
        ),
        sa.CheckConstraint(
            "(estado = 'PUBLICADA') = (publicada_en IS NOT NULL)",
            name=op.f("ck_configuracion_optimizacion_publicada"),
        ),
        sa.CheckConstraint(
            "pools > 0 AND pools_activos >= 1 AND pools_activos <= pools "
            "AND restricciones >= 0 AND restricciones_activas >= 0 "
            "AND restricciones_activas <= restricciones "
            "AND (max_cuentas_atendidas IS NULL OR max_cuentas_atendidas >= 1) "
            "AND (max_unidades_recurso IS NULL OR max_unidades_recurso >= 1)",
            name=op.f("ck_configuracion_optimizacion_conteos"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_version_configuracion_optimizacion")),
        sa.UniqueConstraint(
            "configuracion_id",
            name=op.f("uq_version_configuracion_optimizacion_configuracion_id"),
        ),
        sa.UniqueConstraint("hash_contenido", name="uq_version_configuracion_optimizacion_hash"),
    )
    op.create_index(
        "ix_configuracion_optimizacion_estado",
        "version_configuracion_optimizacion",
        ["estado", "id"],
    )

    op.create_table(
        "admisibilidad_canal_optimizacion",
        _entero("version_configuracion_optimizacion_id"),
        _texto("canal_decision", 12, nullable=False),
        _texto("canal_operativo", 12, nullable=False),
        _booleano("permitido"),
        sa.CheckConstraint(
            f"canal_decision IN {_en(CANALES_DECISION)} "
            f"AND canal_operativo IN {_en(CANALES_OPERATIVOS)}",
            name=op.f("ck_admisibilidad_optimizacion_canales"),
        ),
        sa.ForeignKeyConstraint(
            ["version_configuracion_optimizacion_id"],
            ["version_configuracion_optimizacion.id"],
            name="fk_admisibilidad_optimizacion_version",
        ),
        sa.PrimaryKeyConstraint(
            "version_configuracion_optimizacion_id",
            "canal_decision",
            "canal_operativo",
            name=op.f("pk_admisibilidad_canal_optimizacion"),
        ),
    )

    op.create_table(
        "pool_capacidad_optimizacion",
        sa.Column("id", sa.Integer(), nullable=False),
        _entero("version_configuracion_optimizacion_id"),
        _texto("codigo", 32, nullable=False),
        _texto("canal", 12, nullable=False),
        _texto("tipo_alcance", 16, nullable=False),
        _texto("referencia_alcance", 32),
        _grande("capacidad", nullable=True),
        _entero("unidades_recurso_por_asignacion"),
        _booleano("activo"),
        sa.CheckConstraint(COHERENCIA_DEL_POOL, name=op.f("ck_pool_optimizacion_coherencia")),
        sa.ForeignKeyConstraint(
            ["version_configuracion_optimizacion_id"],
            ["version_configuracion_optimizacion.id"],
            name="fk_pool_optimizacion_version",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pool_capacidad_optimizacion")),
        sa.UniqueConstraint(
            "version_configuracion_optimizacion_id", "codigo", name="uq_pool_optimizacion_codigo"
        ),
        sa.UniqueConstraint(
            "id", "version_configuracion_optimizacion_id", name="uq_pool_optimizacion_id_version"
        ),
    )
    op.create_index(
        "ux_pool_optimizacion_gestor",
        "pool_capacidad_optimizacion",
        ["version_configuracion_optimizacion_id", "referencia_alcance"],
        unique=True,
        postgresql_where=sa.text("canal = 'CAMPO'"),
    )

    op.create_table(
        "restriccion_optimizacion",
        _grande("limite"),
        _entero("version_configuracion_optimizacion_id"),
        _texto("codigo", 32, nullable=False),
        _texto("tipo", 24, nullable=False),
        _texto("dimension", 20, nullable=False),
        _texto("valor_dimension", 32, nullable=False),
        _booleano("activa"),
        sa.CheckConstraint(
            VOCABULARIO_DE_LA_RESTRICCION, name=op.f("ck_restriccion_optimizacion_vocabulario")
        ),
        sa.ForeignKeyConstraint(
            ["version_configuracion_optimizacion_id"],
            ["version_configuracion_optimizacion.id"],
            name="fk_restriccion_optimizacion_version",
        ),
        sa.PrimaryKeyConstraint(
            "version_configuracion_optimizacion_id",
            "codigo",
            name=op.f("pk_restriccion_optimizacion"),
        ),
    )


def _ejecuciones() -> None:
    op.create_table(
        "ejecucion_optimizacion",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("optimizacion_run_id", sa.Uuid(), nullable=False),
        _entero("ejecucion_scoring_predictivo_id"),
        _entero("ejecucion_decision_v2_id"),
        _entero("corte_canonico_id"),
        _entero("ejecucion_ruteo_v2_id", nullable=True),
        _entero("ejecucion_campo_v2_id", nullable=True),
        _entero("ejecucion_territorial_v2_id", nullable=True),
        _entero("version_configuracion_campo_id", nullable=True),
        _entero("version_bases_operativas_id", nullable=True),
        _entero("version_configuracion_optimizacion_id"),
        sa.Column("fecha_operacion", sa.Date(), nullable=False),
        _texto("version_motor", 32, nullable=False),
        _texto("version_politica", 40, nullable=False),
        _hash("hash_politica"),
        _hash("hash_configuracion"),
        _hash("solver_fingerprint"),
        _hash("firma_inputs"),
        sa.Column(
            "estado",
            _vocabulario("estado_optimizacion", 12, "EN_PROCESO", "EXITOSA", "FALLIDA"),
            nullable=False,
        ),
        _texto("resultado", 40),
        _grande("cuentas_universo"),
        _grande("cuentas_elegibles"),
        _grande("cuentas_con_imputacion"),
        _grande("cuentas_retenidas"),
        _grande("cuentas_asignadas"),
        _grande("cuentas_publicadas"),
        _entero("clases"),
        _grande("variables"),
        _grande("restricciones_solver"),
        _grande("elementos_no_nulos"),
        _grande("objetivo_primario_centavos", nullable=True),
        _grande("objetivo_secundario", nullable=True),
        _grande("unidades_recurso", nullable=True),
        _grande("desempate_canonico", nullable=True),
        _texto("solver_status", 24),
        sa.Column("best_bound", sa.Numeric(precision=30, scale=4), nullable=True),
        sa.Column("mip_gap", sa.Numeric(precision=24, scale=12), nullable=True),
        sa.Column("solver_seconds", sa.Numeric(precision=14, scale=3), nullable=False),
        _hash("hash_plan", nullable=True),
        _json("huella_solver"),
        _json("certificado", nullable=True),
        _json("diagnostico", nullable=True),
        _json("resumen", nullable=True),
        _json("tiempos", nullable=True),
        _instante("iniciada_en", nullable=False),
        _instante("terminada_en"),
        _texto("detalle"),
        sa.CheckConstraint(
            "firma_inputs ~ '^[0-9a-f]{64}$' AND hash_politica ~ '^[0-9a-f]{64}$' "
            "AND hash_configuracion ~ '^[0-9a-f]{64}$' AND solver_fingerprint ~ '^[0-9a-f]{64}$' "
            "AND (hash_plan IS NULL OR hash_plan ~ '^[0-9a-f]{64}$')",
            name=op.f("ck_optimizacion_firmas"),
        ),
        sa.CheckConstraint(
            "(estado = 'EN_PROCESO') = (resultado IS NULL) "
            "AND (estado = 'EN_PROCESO') = (solver_status IS NULL) "
            f"AND (solver_status IS NULL OR solver_status IN {_en(ESTADOS_SOLVER)})",
            name=op.f("ck_optimizacion_resultado"),
        ),
        sa.CheckConstraint(CADENA_DE_CAMPO, name=op.f("ck_optimizacion_cadena")),
        sa.CheckConstraint(CONTEOS_DE_OPTIMIZACION, name=op.f("ck_optimizacion_conteos")),
        sa.CheckConstraint(PUBLICACION_DE_OPTIMIZACION, name=op.f("ck_optimizacion_publicacion")),
        sa.ForeignKeyConstraint(
            ["ejecucion_scoring_predictivo_id", "corte_canonico_id"],
            ["ejecucion_scoring_predictivo.id", "ejecucion_scoring_predictivo.corte_canonico_id"],
            name="fk_optimizacion_scoring",
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_decision_v2_id", "corte_canonico_id"],
            ["ejecucion_decision_v2.id", "ejecucion_decision_v2.corte_canonico_id"],
            name="fk_optimizacion_decision",
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_ruteo_v2_id", "ejecucion_campo_v2_id"],
            ["ejecucion_ruteo_v2.id", "ejecucion_ruteo_v2.ejecucion_campo_v2_id"],
            name="fk_optimizacion_ruteo",
        ),
        sa.ForeignKeyConstraint(
            [
                "ejecucion_campo_v2_id",
                "ejecucion_decision_v2_id",
                "ejecucion_territorial_v2_id",
                "version_configuracion_campo_id",
                "version_bases_operativas_id",
            ],
            [
                "ejecucion_campo_v2.id",
                "ejecucion_campo_v2.ejecucion_decision_v2_id",
                "ejecucion_campo_v2.ejecucion_territorial_v2_id",
                "ejecucion_campo_v2.version_configuracion_campo_id",
                "ejecucion_campo_v2.version_bases_operativas_id",
            ],
            name="fk_optimizacion_campo",
        ),
        sa.ForeignKeyConstraint(
            ["version_configuracion_optimizacion_id"],
            ["version_configuracion_optimizacion.id"],
            name="fk_optimizacion_configuracion",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ejecucion_optimizacion")),
        sa.UniqueConstraint(
            "optimizacion_run_id", name=op.f("uq_ejecucion_optimizacion_optimizacion_run_id")
        ),
        sa.UniqueConstraint(
            "id", "version_configuracion_optimizacion_id", name="uq_optimizacion_id_configuracion"
        ),
    )
    for indice, estado in (
        ("ux_optimizacion_exitosa", "EXITOSA"),
        ("ux_optimizacion_en_proceso", "EN_PROCESO"),
    ):
        op.create_index(
            indice,
            "ejecucion_optimizacion",
            ["firma_inputs"],
            unique=True,
            postgresql_where=sa.text(f"estado = '{estado}'"),
        )
    for indice, columnas in (
        ("ix_optimizacion_estado", ["estado", "id"]),
        ("ix_optimizacion_scoring", ["ejecucion_scoring_predictivo_id", "id"]),
        ("ix_optimizacion_configuracion", ["version_configuracion_optimizacion_id", "id"]),
    ):
        op.create_index(indice, "ejecucion_optimizacion", columnas)


def _resultados() -> None:
    op.create_table(
        "resultado_optimizacion_cuenta",
        _grande("valor_oportunidad_centavos"),
        _entero("ejecucion_optimizacion_id"),
        _entero("cuenta_canonica_id"),
        _entero("corte_canonico_id"),
        _entero("version_configuracion_optimizacion_id"),
        _entero("pool_capacidad_optimizacion_id", nullable=True),
        _entero("gestor_campo_id", nullable=True),
        _entero("base_operativa_id", nullable=True),
        _entero("orden_ruteo_original", nullable=True),
        _entero("unidades_recurso", nullable=True),
        sa.Column("p_contacto_14d", sa.Numeric(precision=9, scale=8), nullable=False),
        _texto("estado", 16, nullable=False),
        _texto("motivo", 32, nullable=False),
        _texto("canal_decision", 12, nullable=False),
        _texto("canal_asignado", 12),
        _texto("prioridad", 12, nullable=False),
        _texto("producto", 20, nullable=False),
        _texto("atraso_bucket", 8, nullable=False),
        _booleano("retenida"),
        sa.CheckConstraint(COHERENCIA_DEL_RESULTADO, name=op.f("ck_resultado_optimizacion_estado")),
        sa.ForeignKeyConstraint(
            ["ejecucion_optimizacion_id", "version_configuracion_optimizacion_id"],
            [
                "ejecucion_optimizacion.id",
                "ejecucion_optimizacion.version_configuracion_optimizacion_id",
            ],
            name="fk_resultado_optimizacion_ejecucion",
        ),
        sa.ForeignKeyConstraint(
            ["corte_canonico_id", "cuenta_canonica_id"],
            ["snapshot_cuenta.corte_canonico_id", "snapshot_cuenta.cuenta_canonica_id"],
            name="fk_resultado_optimizacion_snapshot",
        ),
        sa.ForeignKeyConstraint(
            ["pool_capacidad_optimizacion_id", "version_configuracion_optimizacion_id"],
            [
                "pool_capacidad_optimizacion.id",
                "pool_capacidad_optimizacion.version_configuracion_optimizacion_id",
            ],
            name="fk_resultado_optimizacion_pool",
        ),
        sa.ForeignKeyConstraint(
            ["gestor_campo_id"], ["gestor_campo.id"], name="fk_resultado_optimizacion_gestor"
        ),
        sa.ForeignKeyConstraint(
            ["base_operativa_id"], ["base_operativa.id"], name="fk_resultado_optimizacion_base"
        ),
        sa.PrimaryKeyConstraint(
            "ejecucion_optimizacion_id",
            "cuenta_canonica_id",
            name=op.f("pk_resultado_optimizacion_cuenta"),
        ),
    )
    for indice, columna in (
        ("ix_resultado_optimizacion_estado", "estado"),
        ("ix_resultado_optimizacion_producto", "producto"),
        ("ix_resultado_optimizacion_prioridad", "prioridad"),
    ):
        op.create_index(
            indice,
            "resultado_optimizacion_cuenta",
            ["ejecucion_optimizacion_id", columna, "cuenta_canonica_id"],
        )
    for indice, columna in (
        ("ix_resultado_optimizacion_canal", "canal_asignado"),
        ("ix_resultado_optimizacion_pool", "pool_capacidad_optimizacion_id"),
        ("ix_resultado_optimizacion_base", "base_operativa_id"),
    ):
        op.create_index(
            indice,
            "resultado_optimizacion_cuenta",
            ["ejecucion_optimizacion_id", columna, "cuenta_canonica_id"],
            postgresql_where=sa.text(f"{columna} IS NOT NULL"),
        )
    op.create_index(
        "ix_resultado_optimizacion_cuenta",
        "resultado_optimizacion_cuenta",
        ["cuenta_canonica_id", "ejecucion_optimizacion_id"],
    )

    op.create_table(
        "uso_pool_optimizacion",
        _grande("capacidad_declarada", nullable=True),
        _grande("capacidad_efectiva"),
        _grande("elegibles"),
        _grande("retenidas"),
        _grande("utilizada"),
        _grande("holgura"),
        _grande("unidades_consumidas"),
        _entero("ejecucion_optimizacion_id"),
        _entero("pool_capacidad_optimizacion_id"),
        _entero("version_configuracion_optimizacion_id"),
        _entero("gestor_campo_id", nullable=True),
        sa.CheckConstraint(
            "capacidad_efectiva >= 0 AND (capacidad_declarada IS NULL OR "
            "capacidad_efectiva <= capacidad_declarada) AND utilizada >= 0 "
            "AND utilizada <= capacidad_efectiva AND holgura = capacidad_efectiva - utilizada "
            "AND retenidas <= elegibles AND utilizada <= retenidas "
            "AND unidades_consumidas >= utilizada",
            name=op.f("ck_uso_pool_optimizacion_conteos"),
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_optimizacion_id", "version_configuracion_optimizacion_id"],
            [
                "ejecucion_optimizacion.id",
                "ejecucion_optimizacion.version_configuracion_optimizacion_id",
            ],
            name="fk_uso_pool_optimizacion_ejecucion",
        ),
        sa.ForeignKeyConstraint(
            ["pool_capacidad_optimizacion_id", "version_configuracion_optimizacion_id"],
            [
                "pool_capacidad_optimizacion.id",
                "pool_capacidad_optimizacion.version_configuracion_optimizacion_id",
            ],
            name="fk_uso_pool_optimizacion_pool",
        ),
        sa.ForeignKeyConstraint(
            ["gestor_campo_id"], ["gestor_campo.id"], name="fk_uso_pool_optimizacion_gestor"
        ),
        sa.PrimaryKeyConstraint(
            "ejecucion_optimizacion_id",
            "pool_capacidad_optimizacion_id",
            name=op.f("pk_uso_pool_optimizacion"),
        ),
    )

    op.create_table(
        "resultado_restriccion_optimizacion",
        _grande("lhs"),
        _grande("rhs"),
        _grande("holgura"),
        _grande("cuentas_grupo", nullable=True),
        _grande("total", nullable=True),
        _grande("limite_resuelto", nullable=True),
        _entero("ejecucion_optimizacion_id"),
        _texto("codigo", 40, nullable=False),
        _texto("tipo", 24, nullable=False),
        _texto("dimension", 20),
        _texto("valor_dimension", 32),
        _texto("operador", 2, nullable=False),
        _booleano("binding"),
        sa.CheckConstraint(
            "operador IN ('<=', '>=') AND holgura >= 0 AND binding = (holgura = 0) "
            "AND ((operador = '<=' AND holgura = rhs - lhs) "
            "OR (operador = '>=' AND holgura = lhs - rhs))",
            name=op.f("ck_restriccion_resultado_holgura"),
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_optimizacion_id"],
            ["ejecucion_optimizacion.id"],
            name="fk_restriccion_resultado_ejecucion",
        ),
        sa.PrimaryKeyConstraint(
            "ejecucion_optimizacion_id",
            "codigo",
            name=op.f("pk_resultado_restriccion_optimizacion"),
        ),
    )
    op.create_index(
        "ix_restriccion_resultado_binding",
        "resultado_restriccion_optimizacion",
        ["ejecucion_optimizacion_id", "binding", "codigo"],
    )


def _inmutables() -> None:
    op.execute(sa.text(OPTIMIZACION_INMUTABLE))
    for tabla in INMUTABLES:
        op.execute(
            sa.text(
                f"CREATE TRIGGER tr_optimizacion_inmutable BEFORE UPDATE OR DELETE ON {tabla} "
                "FOR EACH STATEMENT EXECUTE FUNCTION optimizacion_inmutable()"
            )
        )
    op.execute(sa.text(OPTIMIZACION_TERMINADA))
    op.execute(
        sa.text(
            "CREATE TRIGGER tr_optimizacion_terminada BEFORE UPDATE OR DELETE "
            "ON ejecucion_optimizacion FOR EACH ROW EXECUTE FUNCTION optimizacion_terminada()"
        )
    )
    op.execute(sa.text(CONFIGURACION_CICLO))
    op.execute(
        sa.text(
            "CREATE TRIGGER tr_configuracion_optimizacion_ciclo BEFORE UPDATE OR DELETE "
            "ON version_configuracion_optimizacion FOR EACH ROW "
            "EXECUTE FUNCTION configuracion_optimizacion_ciclo()"
        )
    )


def _cola() -> None:
    # El trabajo OPTIMIZATION, en la misma cola durable: uno por ejecucion. Cabe en los 24
    # caracteres de tipo; solo se rehacen los CHECK del tipo y del objetivo.
    op.add_column(
        "trabajo_orquestacion",
        sa.Column("ejecucion_optimizacion_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_trabajo_optimizacion",
        "trabajo_orquestacion",
        "ejecucion_optimizacion",
        ["ejecucion_optimizacion_id"],
        ["id"],
    )
    op.create_unique_constraint(
        "uq_trabajo_optimizacion", "trabajo_orquestacion", ["ejecucion_optimizacion_id"]
    )
    _rehacer_check("ck_trabajo_orquestacion_tipo_trabajo", _tipos(TIPOS_0017))
    _rehacer_check("ck_trabajo_objetivo", OBJETIVO_0017)


def downgrade() -> None:
    # Primero se cierra lo que estaba en curso: las ejecuciones (si un worker tiene una bloqueada,
    # termina antes de que el UPDATE la tome) y despues sus trabajos. Despues se van esos trabajos,
    # que la 0016 no sabria guardar, y las estructuras de la 0017.
    op.execute(CERRAR_EJECUCIONES)
    op.execute(CERRAR_TRABAJOS)
    op.execute(sa.text("DELETE FROM trabajo_orquestacion WHERE tipo = 'OPTIMIZATION'"))
    _rehacer_check("ck_trabajo_objetivo", OBJETIVO_0016)
    _rehacer_check("ck_trabajo_orquestacion_tipo_trabajo", _tipos(TIPOS_0016))
    op.drop_constraint("uq_trabajo_optimizacion", "trabajo_orquestacion", type_="unique")
    op.drop_constraint("fk_trabajo_optimizacion", "trabajo_orquestacion", type_="foreignkey")
    op.drop_column("trabajo_orquestacion", "ejecucion_optimizacion_id")

    op.drop_index(
        "ix_restriccion_resultado_binding", table_name="resultado_restriccion_optimizacion"
    )
    op.drop_table("resultado_restriccion_optimizacion")
    op.drop_table("uso_pool_optimizacion")
    for indice, columna in (
        ("ix_resultado_optimizacion_base", "base_operativa_id"),
        ("ix_resultado_optimizacion_pool", "pool_capacidad_optimizacion_id"),
        ("ix_resultado_optimizacion_canal", "canal_asignado"),
    ):
        op.drop_index(
            indice,
            table_name="resultado_optimizacion_cuenta",
            postgresql_where=sa.text(f"{columna} IS NOT NULL"),
        )
    for indice in (
        "ix_resultado_optimizacion_cuenta",
        "ix_resultado_optimizacion_prioridad",
        "ix_resultado_optimizacion_producto",
        "ix_resultado_optimizacion_estado",
    ):
        op.drop_index(indice, table_name="resultado_optimizacion_cuenta")
    op.drop_table("resultado_optimizacion_cuenta")
    for indice in (
        "ix_optimizacion_configuracion",
        "ix_optimizacion_scoring",
        "ix_optimizacion_estado",
    ):
        op.drop_index(indice, table_name="ejecucion_optimizacion")
    for indice, estado in (
        ("ux_optimizacion_en_proceso", "EN_PROCESO"),
        ("ux_optimizacion_exitosa", "EXITOSA"),
    ):
        op.drop_index(
            indice,
            table_name="ejecucion_optimizacion",
            postgresql_where=sa.text(f"estado = '{estado}'"),
        )
    op.drop_table("ejecucion_optimizacion")
    op.drop_table("restriccion_optimizacion")
    op.drop_index(
        "ux_pool_optimizacion_gestor",
        table_name="pool_capacidad_optimizacion",
        postgresql_where=sa.text("canal = 'CAMPO'"),
    )
    op.drop_table("pool_capacidad_optimizacion")
    op.drop_table("admisibilidad_canal_optimizacion")
    op.drop_index(
        "ix_configuracion_optimizacion_estado", table_name="version_configuracion_optimizacion"
    )
    op.drop_table("version_configuracion_optimizacion")
    for funcion in (
        "configuracion_optimizacion_ciclo",
        "optimizacion_terminada",
        "optimizacion_inmutable",
    ):
        op.execute(sa.text(f"DROP FUNCTION {funcion}()"))
