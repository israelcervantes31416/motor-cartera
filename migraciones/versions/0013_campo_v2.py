"""campo/v2: la configuracion de campo versionada (gestores, calendarios, ventanas y
excepciones), los planes de una fecha con su asignacion por cuenta, sus unidades y sus cargas, y su
trabajo CAMPO_V2

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-11 18:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

# Como en las demas migraciones, cada nombre se midio contra los 63 caracteres de PostgreSQL y es
# parte del contrato del esquema: sale en los IntegrityError y una migracion futura se refiere a el.
#
# La 0013 solo crea estructuras: no carga ninguna configuracion ni planea nada. Una configuracion se
# publica con POST /campo/configuraciones o `motor-cartera cargar-campo`, y un plan se pide por la
# API o el CLI. Nada de lo anterior cambia: ni decision/v2, ni la geografia, ni territorial/v2, ni
# territorial/v1 ni ruteo/v1, ni sus trabajos. A ejecucion_territorial_v2 solo se le agrega una
# restriccion unica sobre columnas que ya tenia, para que un plan pueda exigir en la base que su
# territorial/v2 es de su corte y de sus bases.

TIPOS_0012 = (
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
)
TIPOS_0013 = (*TIPOS_0012, "CAMPO_V2")
OBJETIVO_0012 = (
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
    "AND (tipo = 'TERRITORIAL_V2') = (ejecucion_territorial_v2_id IS NOT NULL)"
)
OBJETIVO_0013 = OBJETIVO_0012 + " AND (tipo = 'CAMPO_V2') = (ejecucion_campo_v2_id IS NOT NULL)"

PATRON_CODIGO = "^[A-Z0-9_-]{1,32}$"
PATRON_CVEGEO = "^[0-9]{5}$"
ESTADOS_CAMPO = ("ASIGNADA", "REVISION_TERRITORIAL", "SIN_GESTOR_DISPONIBLE", "SIN_CAPACIDAD")
PRIORIDADES = ("BAJA", "MEDIA", "ALTA", "MUY_ALTA")
COHERENCIA_DEL_CAMPO = (
    "estado IN ("
    + ", ".join(f"'{e}'" for e in ESTADOS_CAMPO)
    + ") AND prioridad IN ("
    + ", ".join(f"'{p}'" for p in PRIORIDADES)
    + ") AND (gestor_campo_id IS NOT NULL) = (estado = 'ASIGNADA') "
    "AND (base_operativa_id IS NOT NULL) = (estado <> 'REVISION_TERRITORIAL') "
    "AND (zona_id IS NOT NULL) = (estado <> 'REVISION_TERRITORIAL') "
    "AND (subzona_id IS NOT NULL) = (estado <> 'REVISION_TERRITORIAL') "
    "AND (tramo_id IS NOT NULL) = (estado <> 'REVISION_TERRITORIAL') "
    "AND (microtramo_id IS NOT NULL) = (estado <> 'REVISION_TERRITORIAL') "
    "AND (orden_prioridad IS NOT NULL) = (estado <> 'REVISION_TERRITORIAL') "
    "AND (orden_prioridad IS NULL OR orden_prioridad >= 1) "
    "AND (NOT es_rebalanceada OR estado = 'ASIGNADA')"
)
CONTEOS_DE_CAMPO_V2 = ("asignadas", "revision_territorial", "sin_gestor", "sin_capacidad")
_SUMA_DE_CAMPO_V2 = " + ".join(CONTEOS_DE_CAMPO_V2)
PUBLICACION_DE_CAMPO_V2 = (
    f"(estado = 'EXITOSA' AND {_SUMA_DE_CAMPO_V2} = candidatas_campo "
    "AND candidatas_campo <= cuentas_evaluadas_decision AND rebalanceadas <= asignadas "
    "AND capacidad_utilizada = asignadas AND asignadas <= capacidad_total) "
    f"OR (estado <> 'EXITOSA' AND candidatas_campo = 0 AND {_SUMA_DE_CAMPO_V2} = 0 "
    "AND rebalanceadas = 0 AND capacidad_total = 0 AND capacidad_utilizada = 0)"
)
NIVELES_DE_UNIDAD = ("ZONA", "SUBZONA", "TRAMO", "MICROTRAMO")
ESTADOS_JORNADA = (
    "JORNADA_SEMANAL",
    "VENTANAS_ESPECIALES",
    "SIN_JORNADA",
    "CERRADO_POR_EXCEPCION",
    "INACTIVO",
)
JORNADAS_DISPONIBLES = "('JORNADA_SEMANAL', 'VENTANAS_ESPECIALES')"

INMUTABLES = {
    "configuracion_campo_inmutable": (
        (
            "version_configuracion_campo",
            "calendario_campo",
            "excepcion_calendario_campo",
            "ventana_jornada_campo",
            "gestor_campo",
        ),
        "Una configuracion de campo publicada no se reescribe",
        "Cambiar un gestor, un cupo, un calendario o una excepcion es publicar otra version: los "
        "planes viejos siguen con la suya.",
    ),
    "campo_v2_inmutable": (
        ("asignacion_campo_cuenta", "unidad_territorial_campo", "carga_gestor_campo"),
        "campo/v2 no se reescribe",
        "Un plan publicado no cambia: se pide otro plan, con otras entradas.",
    ),
}
"""Lo publicado: un trigger por sentencia rechaza cualquier UPDATE o DELETE sobre estas tablas, con
restrict_violation. TRUNCATE no pasa por aqui: es una operacion de administracion, no de la
aplicacion."""


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


PLAN_TERMINADO = """
CREATE FUNCTION campo_v2_terminado() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' OR OLD.estado <> 'EN_PROCESO' THEN
        RAISE EXCEPTION 'Un plan de campo/v2 terminado no cambia: % sobre % no se permite.',
            TG_OP, TG_TABLE_NAME
            USING ERRCODE = 'restrict_violation', CONSTRAINT = 'tr_campo_v2_terminado',
                  HINT = 'Un plan publicado o fallido es historia: se pide otro plan.';
    END IF;
    RETURN NEW;
END
$$
"""
"""Un plan solo cambia mientras se resuelve (EN_PROCESO): al publicarse o al fallar ya no cambia, y
ninguno se borra."""

VENTANAS_SIN_ENCIMARSE = """
CREATE FUNCTION ventana_campo_sin_encimarse() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM ventana_jornada_campo w
        WHERE w.id <> NEW.id AND w.calendario_campo_id = NEW.calendario_campo_id
          AND w.dia_semana IS NOT DISTINCT FROM NEW.dia_semana
          AND w.excepcion_calendario_campo_id IS NOT DISTINCT FROM NEW.excepcion_calendario_campo_id
          AND w.minuto_inicio < NEW.minuto_fin AND NEW.minuto_inicio < w.minuto_fin
    ) THEN
        RAISE EXCEPTION 'Dos ventanas del mismo dia de un calendario de campo se enciman.'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'tr_ventana_campo_sin_encimarse',
                  HINT = 'Una ventana termina antes de que empiece la siguiente; tocarse no es '
                         'encimarse.';
    END IF;
    RETURN NULL;
END
$$
"""
"""Despues de insertar, cada ventana se compara con las de su mismo dia o su misma excepcion: el
trigger se dispara al terminar la sentencia, y ve todas las que inserto un COPY."""

# Bajar a la 0012 quita campo/v2 entero: la configuracion de campo, los planes y sus trabajos. No se
# reconstruyen solos (se vuelve a cargar la configuracion y a pedir los planes, y dan lo mismo). Lo
# que esta en curso se cierra primero. Nada de v0.11 se toca: ni PostGIS, ni la geografia, ni las
# bases, ni territorial/v2, ni decision/v2, ni el lifecycle, ni los pagos, ni la historia, ni los
# artefactos, ni territorial/v1 o ruteo/v1.
CIERRE = (
    "Cerrado al bajar la base a la 0012: la v0.11 no tiene campo/v2. La geografia, territorial/v2 "
    "y todo lo demas siguen intactos."
)
CERRAR_PLANES = sa.text(
    "UPDATE ejecucion_campo_v2 SET estado = 'FALLIDA', resultado = 'CERRADA_AL_BAJAR', "
    "candidatas_campo = 0, "
    + ", ".join(f"{c} = 0" for c in CONTEOS_DE_CAMPO_V2)
    + ", rebalanceadas = 0, capacidad_total = 0, capacidad_utilizada = 0, terminada_en = now(), "
    "detalle = :motivo WHERE estado = 'EN_PROCESO'"
).bindparams(motivo=CIERRE)
CERRAR_TRABAJOS = sa.text(
    "UPDATE trabajo_orquestacion SET estado = 'FALLIDO', worker_id = NULL, lease_hasta = NULL, "
    "terminado_en = now(), ultimo_error = :motivo "
    "WHERE tipo = 'CAMPO_V2' AND estado IN ('PENDIENTE', 'EJECUTANDO')"
).bindparams(motivo=CIERRE)
"""Lo primero que hace bajar la 0013: cerrar los planes en curso y sus trabajos, antes de quitar sus
tablas."""


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


def _grande(nombre: str) -> sa.Column:
    return sa.Column(nombre, sa.BigInteger(), nullable=False)


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_territorial_v2_corte_bases",
        "ejecucion_territorial_v2",
        ["id", "corte_canonico_id", "version_bases_operativas_id"],
    )
    _configuracion()
    _planes()
    _inmutables()
    _cola()


def _configuracion() -> None:
    op.create_table(
        "version_configuracion_campo",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("configuracion_campo_version_id", sa.Uuid(), nullable=False),
        _entero("version_bases_operativas_id"),
        _texto("nombre", 120, nullable=False),
        _texto("version_configuracion", 32, nullable=False),
        _texto("hash_configuracion", 64, nullable=False),
        _texto("zona_horaria", 64, nullable=False),
        sa.Column("estado", _vocabulario("estado_version_campo", 12, "PUBLICADA"), nullable=False),
        _entero("calendarios"),
        _entero("ventanas"),
        _entero("excepciones"),
        _entero("gestores"),
        _entero("gestores_activos"),
        _entero("capacidad_declarada"),
        sa.Column("metadatos", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("creada_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("publicada_en", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "hash_configuracion ~ '^[0-9a-f]{64}$'", name=op.f("ck_configuracion_campo_hash")
        ),
        sa.CheckConstraint(
            "calendarios > 0 AND ventanas >= 0 AND excepciones >= 0 AND gestores > 0 "
            "AND gestores_activos >= 0 AND gestores_activos <= gestores "
            "AND capacidad_declarada >= 0",
            name=op.f("ck_configuracion_campo_conteos"),
        ),
        sa.ForeignKeyConstraint(
            ["version_bases_operativas_id"],
            ["version_bases_operativas.id"],
            name="fk_configuracion_campo_bases",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_version_configuracion_campo")),
        sa.UniqueConstraint(
            "configuracion_campo_version_id",
            name=op.f("uq_version_configuracion_campo_configuracion_campo_version_id"),
        ),
        sa.UniqueConstraint("hash_configuracion", name="uq_version_configuracion_campo_hash"),
        sa.UniqueConstraint(
            "id", "version_bases_operativas_id", name="uq_configuracion_campo_id_bases"
        ),
    )
    op.create_table(
        "calendario_campo",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("calendario_id", sa.Uuid(), nullable=False),
        _entero("version_configuracion_campo_id"),
        _texto("codigo", 32, nullable=False),
        sa.CheckConstraint(f"codigo ~ '{PATRON_CODIGO}'", name=op.f("ck_calendario_codigo")),
        sa.ForeignKeyConstraint(
            ["version_configuracion_campo_id"],
            ["version_configuracion_campo.id"],
            name="fk_calendario_campo_configuracion",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_calendario_campo")),
        sa.UniqueConstraint("calendario_id", name=op.f("uq_calendario_campo_calendario_id")),
        sa.UniqueConstraint(
            "version_configuracion_campo_id", "codigo", name="uq_calendario_campo_codigo"
        ),
        sa.UniqueConstraint(
            "id", "version_configuracion_campo_id", name="uq_calendario_campo_id_configuracion"
        ),
    )
    op.create_table(
        "excepcion_calendario_campo",
        sa.Column("id", sa.Integer(), nullable=False),
        _entero("calendario_campo_id"),
        sa.Column("fecha", sa.Date(), nullable=False),
        sa.Column(
            "tipo",
            _vocabulario("tipo_excepcion_campo", 20, "CERRADO", "VENTANAS_ESPECIALES"),
            nullable=False,
        ),
        _texto("motivo", 120),
        sa.ForeignKeyConstraint(
            ["calendario_campo_id"], ["calendario_campo.id"], name="fk_excepcion_campo_calendario"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_excepcion_calendario_campo")),
        sa.UniqueConstraint("calendario_campo_id", "fecha", name="uq_excepcion_campo_fecha"),
        sa.UniqueConstraint("id", "calendario_campo_id", name="uq_excepcion_campo_id_calendario"),
    )
    op.create_table(
        "ventana_jornada_campo",
        sa.Column("id", sa.Integer(), nullable=False),
        _entero("calendario_campo_id"),
        _entero("excepcion_calendario_campo_id", nullable=True),
        sa.Column("dia_semana", sa.SmallInteger(), nullable=True),
        sa.Column("minuto_inicio", sa.SmallInteger(), nullable=False),
        sa.Column("minuto_fin", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint(
            "(dia_semana IS NULL) <> (excepcion_calendario_campo_id IS NULL) "
            "AND (dia_semana IS NULL OR dia_semana BETWEEN 1 AND 7) "
            "AND minuto_inicio >= 0 AND minuto_inicio < minuto_fin AND minuto_fin <= 1440",
            name=op.f("ck_ventana_campo_horario"),
        ),
        sa.ForeignKeyConstraint(
            ["calendario_campo_id"], ["calendario_campo.id"], name="fk_ventana_campo_calendario"
        ),
        sa.ForeignKeyConstraint(
            ["excepcion_calendario_campo_id", "calendario_campo_id"],
            ["excepcion_calendario_campo.id", "excepcion_calendario_campo.calendario_campo_id"],
            name="fk_ventana_campo_excepcion",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ventana_jornada_campo")),
    )
    # Las ventanas de un calendario, de un dia o de sus excepciones: la jornada de cada gestor,
    # las ventanas de cada carga y el trigger que no deja encimarlas las buscan asi. Cada version
    # de la configuracion agrega las suyas, y la tabla crece con cada version que se publica.
    op.create_index(
        "ix_ventana_campo_calendario",
        "ventana_jornada_campo",
        ["calendario_campo_id", "dia_semana"],
    )
    op.create_table(
        "gestor_campo",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("gestor_id", sa.Uuid(), nullable=False),
        _entero("version_configuracion_campo_id"),
        _entero("version_bases_operativas_id"),
        _texto("codigo", 32, nullable=False),
        _entero("base_operativa_id"),
        _entero("calendario_campo_id"),
        _entero("capacidad_cuentas_dia"),
        sa.Column("activo", sa.Boolean(), nullable=False),
        sa.CheckConstraint(f"codigo ~ '{PATRON_CODIGO}'", name=op.f("ck_gestor_codigo")),
        sa.CheckConstraint(
            "capacidad_cuentas_dia BETWEEN 1 AND 1000", name=op.f("ck_gestor_capacidad")
        ),
        sa.ForeignKeyConstraint(
            ["version_configuracion_campo_id", "version_bases_operativas_id"],
            [
                "version_configuracion_campo.id",
                "version_configuracion_campo.version_bases_operativas_id",
            ],
            name="fk_gestor_campo_configuracion",
        ),
        sa.ForeignKeyConstraint(
            ["base_operativa_id", "version_bases_operativas_id"],
            ["base_operativa.id", "base_operativa.version_bases_operativas_id"],
            name="fk_gestor_campo_base",
        ),
        sa.ForeignKeyConstraint(
            ["calendario_campo_id", "version_configuracion_campo_id"],
            ["calendario_campo.id", "calendario_campo.version_configuracion_campo_id"],
            name="fk_gestor_campo_calendario",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_gestor_campo")),
        sa.UniqueConstraint("gestor_id", name=op.f("uq_gestor_campo_gestor_id")),
        sa.UniqueConstraint(
            "version_configuracion_campo_id", "codigo", name="uq_gestor_campo_codigo"
        ),
        sa.UniqueConstraint(
            "id",
            "version_configuracion_campo_id",
            "base_operativa_id",
            name="uq_gestor_campo_id_configuracion_base",
        ),
    )
    op.create_index(
        "ix_gestor_campo_base",
        "gestor_campo",
        ["version_configuracion_campo_id", "base_operativa_id", "activo"],
    )


def _planes() -> None:
    op.create_table(
        "ejecucion_campo_v2",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("campo_run_id", sa.Uuid(), nullable=False),
        _entero("corte_canonico_id"),
        _entero("ejecucion_decision_v2_id"),
        _entero("ejecucion_territorial_v2_id"),
        _entero("version_bases_operativas_id"),
        _entero("version_configuracion_campo_id"),
        sa.Column("fecha_operacion", sa.Date(), nullable=False),
        _texto("version_motor", 32, nullable=False),
        _texto("version_politica", 40, nullable=False),
        _texto("hash_politica", 64, nullable=False),
        _texto("firma_inputs", 64, nullable=False),
        sa.Column(
            "estado",
            _vocabulario("estado_campo_v2", 12, "EN_PROCESO", "EXITOSA", "FALLIDA"),
            nullable=False,
        ),
        _texto("resultado", 40),
        _grande("cuentas_evaluadas_decision"),
        _grande("candidatas_campo"),
        *(_grande(c) for c in CONTEOS_DE_CAMPO_V2),
        _grande("rebalanceadas"),
        _grande("capacidad_total"),
        _grande("capacidad_utilizada"),
        sa.Column("distribucion", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("iniciada_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("terminada_en", sa.DateTime(timezone=True), nullable=True),
        _texto("detalle"),
        sa.CheckConstraint(
            "firma_inputs ~ '^[0-9a-f]{64}$' AND hash_politica ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_campo_v2_firmas"),
        ),
        sa.CheckConstraint(
            "(estado = 'EN_PROCESO') = (resultado IS NULL)", name=op.f("ck_campo_v2_resultado")
        ),
        sa.CheckConstraint(
            "cuentas_evaluadas_decision >= 0 AND candidatas_campo >= 0 AND rebalanceadas >= 0 "
            "AND capacidad_total >= 0 AND capacidad_utilizada >= 0 AND "
            + " AND ".join(f"{c} >= 0" for c in CONTEOS_DE_CAMPO_V2),
            name=op.f("ck_campo_v2_conteos"),
        ),
        sa.CheckConstraint(PUBLICACION_DE_CAMPO_V2, name=op.f("ck_campo_v2_publicacion")),
        sa.ForeignKeyConstraint(
            ["ejecucion_decision_v2_id", "corte_canonico_id"],
            ["ejecucion_decision_v2.id", "ejecucion_decision_v2.corte_canonico_id"],
            name="fk_campo_v2_decision",
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_territorial_v2_id", "corte_canonico_id", "version_bases_operativas_id"],
            [
                "ejecucion_territorial_v2.id",
                "ejecucion_territorial_v2.corte_canonico_id",
                "ejecucion_territorial_v2.version_bases_operativas_id",
            ],
            name="fk_campo_v2_territorial",
        ),
        sa.ForeignKeyConstraint(
            ["version_configuracion_campo_id", "version_bases_operativas_id"],
            [
                "version_configuracion_campo.id",
                "version_configuracion_campo.version_bases_operativas_id",
            ],
            name="fk_campo_v2_configuracion",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ejecucion_campo_v2")),
        sa.UniqueConstraint("campo_run_id", name=op.f("uq_ejecucion_campo_v2_campo_run_id")),
        sa.UniqueConstraint(
            "id",
            "ejecucion_decision_v2_id",
            "ejecucion_territorial_v2_id",
            "version_configuracion_campo_id",
            "version_bases_operativas_id",
            name="uq_campo_v2_entradas",
        ),
    )
    for indice, estado in (
        ("ux_campo_v2_exitosa", "EXITOSA"),
        ("ux_campo_v2_en_proceso", "EN_PROCESO"),
    ):
        op.create_index(
            indice,
            "ejecucion_campo_v2",
            ["firma_inputs"],
            unique=True,
            postgresql_where=sa.text(f"estado = '{estado}'"),
        )
    op.create_index("ix_campo_v2_corte", "ejecucion_campo_v2", ["corte_canonico_id", "id"])

    # La asignacion de cada candidata, sin id propio: su llave es la natural.
    op.create_table(
        "asignacion_campo_cuenta",
        sa.Column("zona_id", sa.Uuid(), nullable=True),
        sa.Column("subzona_id", sa.Uuid(), nullable=True),
        sa.Column("tramo_id", sa.Uuid(), nullable=True),
        sa.Column("microtramo_id", sa.Uuid(), nullable=True),
        _entero("ejecucion_campo_v2_id"),
        _entero("cuenta_canonica_id"),
        _entero("ejecucion_decision_v2_id"),
        _entero("ejecucion_territorial_v2_id"),
        _entero("version_configuracion_campo_id"),
        _entero("version_bases_operativas_id"),
        _entero("base_operativa_id", nullable=True),
        _entero("gestor_campo_id", nullable=True),
        _entero("orden_prioridad", nullable=True),
        _texto("estado", 24, nullable=False),
        _texto("prioridad", 12, nullable=False),
        sa.Column("es_rebalanceada", sa.Boolean(), nullable=False),
        sa.Column("motivos", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(COHERENCIA_DEL_CAMPO, name=op.f("ck_asignacion_campo_estado")),
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
            name="fk_asignacion_campo_ejecucion",
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_decision_v2_id", "cuenta_canonica_id"],
            [
                "resultado_decision_v2.ejecucion_decision_v2_id",
                "resultado_decision_v2.cuenta_canonica_id",
            ],
            name="fk_asignacion_campo_decision",
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_territorial_v2_id", "cuenta_canonica_id"],
            [
                "asignacion_territorial_v2.ejecucion_territorial_v2_id",
                "asignacion_territorial_v2.cuenta_canonica_id",
            ],
            name="fk_asignacion_campo_territorial",
        ),
        sa.ForeignKeyConstraint(
            ["gestor_campo_id", "version_configuracion_campo_id", "base_operativa_id"],
            [
                "gestor_campo.id",
                "gestor_campo.version_configuracion_campo_id",
                "gestor_campo.base_operativa_id",
            ],
            name="fk_asignacion_campo_gestor",
        ),
        sa.ForeignKeyConstraint(
            ["base_operativa_id", "version_bases_operativas_id"],
            ["base_operativa.id", "base_operativa.version_bases_operativas_id"],
            name="fk_asignacion_campo_base",
        ),
        sa.PrimaryKeyConstraint(
            "ejecucion_campo_v2_id",
            "cuenta_canonica_id",
            name=op.f("pk_asignacion_campo_cuenta"),
        ),
    )
    for indice, columnas in (
        ("ix_asignacion_campo_estado", ["ejecucion_campo_v2_id", "estado", "cuenta_canonica_id"]),
        (
            "ix_asignacion_campo_base",
            ["ejecucion_campo_v2_id", "base_operativa_id", "cuenta_canonica_id"],
        ),
        (
            "ix_asignacion_campo_gestor",
            ["ejecucion_campo_v2_id", "gestor_campo_id", "cuenta_canonica_id"],
        ),
        (
            "ix_asignacion_campo_prioridad",
            ["ejecucion_campo_v2_id", "prioridad", "cuenta_canonica_id"],
        ),
        (
            "ix_asignacion_campo_microtramo",
            ["ejecucion_campo_v2_id", "microtramo_id", "cuenta_canonica_id"],
        ),
        ("ix_asignacion_campo_cuenta", ["cuenta_canonica_id", "ejecucion_campo_v2_id"]),
    ):
        op.create_index(indice, "asignacion_campo_cuenta", columnas)

    op.create_table(
        "unidad_territorial_campo",
        _entero("ejecucion_campo_v2_id"),
        sa.Column("unidad_id", sa.Uuid(), nullable=False),
        _texto("nivel", 12, nullable=False),
        sa.Column("padre_id", sa.Uuid(), nullable=True),
        _entero("base_operativa_id"),
        _texto("cve_ent", 2, nullable=False),
        _texto("cvegeo", 5),
        _entero("candidatas"),
        _entero("asignadas"),
        _entero("propietario_gestor_campo_id", nullable=True),
        sa.CheckConstraint(
            "nivel IN ("
            + ", ".join(f"'{n}'" for n in NIVELES_DE_UNIDAD)
            + ") AND (nivel = 'ZONA') = (padre_id IS NULL) "
            "AND (nivel = 'ZONA') = (cvegeo IS NULL) "
            "AND cve_ent ~ '^[0-9]{2}$' "
            f"AND (cvegeo IS NULL OR (cvegeo ~ '{PATRON_CVEGEO}' AND left(cvegeo, 2) = cve_ent)) "
            "AND (propietario_gestor_campo_id IS NULL OR nivel = 'MICROTRAMO') "
            "AND candidatas > 0 AND asignadas >= 0 AND asignadas <= candidatas",
            name=op.f("ck_unidad_campo_nivel"),
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_campo_v2_id"], ["ejecucion_campo_v2.id"], name="fk_unidad_campo_ejecucion"
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_campo_v2_id", "padre_id"],
            [
                "unidad_territorial_campo.ejecucion_campo_v2_id",
                "unidad_territorial_campo.unidad_id",
            ],
            name="fk_unidad_campo_padre",
        ),
        sa.ForeignKeyConstraint(
            ["base_operativa_id"], ["base_operativa.id"], name="fk_unidad_campo_base"
        ),
        sa.ForeignKeyConstraint(
            ["propietario_gestor_campo_id"], ["gestor_campo.id"], name="fk_unidad_campo_propietario"
        ),
        sa.PrimaryKeyConstraint(
            "ejecucion_campo_v2_id", "unidad_id", name=op.f("pk_unidad_territorial_campo")
        ),
    )
    op.create_index(
        "ix_unidad_campo_padre", "unidad_territorial_campo", ["ejecucion_campo_v2_id", "padre_id"]
    )

    op.create_table(
        "carga_gestor_campo",
        sa.Column("utilizacion", sa.Numeric(precision=5, scale=4), nullable=True),
        _entero("ejecucion_campo_v2_id"),
        _entero("gestor_campo_id"),
        _entero("base_operativa_id"),
        _texto("estado_jornada", 24, nullable=False),
        _entero("capacidad"),
        _entero("asignadas"),
        _entero("rebalanceadas"),
        _entero("microtramos"),
        _entero("tramos"),
        sa.Column("ventanas", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "estado_jornada IN ("
            + ", ".join(f"'{e}'" for e in ESTADOS_JORNADA)
            + f") AND (capacidad > 0) = (estado_jornada IN {JORNADAS_DISPONIBLES}) "
            "AND capacidad >= 0 AND asignadas >= 0 AND asignadas <= capacidad "
            "AND rebalanceadas >= 0 AND rebalanceadas <= asignadas "
            "AND microtramos >= 0 AND tramos >= 0 AND tramos <= microtramos "
            "AND (asignadas = 0) = (microtramos = 0) "
            "AND (utilizacion IS NULL) = (capacidad = 0)",
            name=op.f("ck_carga_campo_conteos"),
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_campo_v2_id"], ["ejecucion_campo_v2.id"], name="fk_carga_campo_ejecucion"
        ),
        sa.ForeignKeyConstraint(
            ["gestor_campo_id"], ["gestor_campo.id"], name="fk_carga_campo_gestor"
        ),
        sa.ForeignKeyConstraint(
            ["base_operativa_id"], ["base_operativa.id"], name="fk_carga_campo_base"
        ),
        sa.PrimaryKeyConstraint(
            "ejecucion_campo_v2_id", "gestor_campo_id", name=op.f("pk_carga_gestor_campo")
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
    op.execute(sa.text(PLAN_TERMINADO))
    op.execute(
        sa.text(
            "CREATE TRIGGER tr_campo_v2_terminado BEFORE UPDATE OR DELETE ON ejecucion_campo_v2 "
            "FOR EACH ROW EXECUTE FUNCTION campo_v2_terminado()"
        )
    )
    op.execute(sa.text(VENTANAS_SIN_ENCIMARSE))
    op.execute(
        sa.text(
            "CREATE TRIGGER tr_ventana_campo_sin_encimarse AFTER INSERT ON ventana_jornada_campo "
            "FOR EACH ROW EXECUTE FUNCTION ventana_campo_sin_encimarse()"
        )
    )


def _cola() -> None:
    # El trabajo CAMPO_V2, en la misma cola durable: uno por plan. Cabe en los 24 caracteres de
    # tipo; solo se rehacen los CHECK del tipo y del objetivo.
    op.add_column(
        "trabajo_orquestacion", sa.Column("ejecucion_campo_v2_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "fk_trabajo_campo_v2",
        "trabajo_orquestacion",
        "ejecucion_campo_v2",
        ["ejecucion_campo_v2_id"],
        ["id"],
    )
    op.create_unique_constraint(
        "uq_trabajo_campo_v2", "trabajo_orquestacion", ["ejecucion_campo_v2_id"]
    )
    _rehacer_check("ck_trabajo_orquestacion_tipo_trabajo", _tipos(TIPOS_0013))
    _rehacer_check("ck_trabajo_objetivo", OBJETIVO_0013)


def downgrade() -> None:
    # Primero se cierra lo que estaba en curso: los planes (si un worker tiene uno bloqueado,
    # termina antes de que el UPDATE lo tome) y despues sus trabajos. Despues se van esos trabajos,
    # que la 0012 no sabria guardar, y las estructuras de la 0013.
    op.execute(CERRAR_PLANES)
    op.execute(CERRAR_TRABAJOS)
    op.execute(sa.text("DELETE FROM trabajo_orquestacion WHERE tipo = 'CAMPO_V2'"))
    _rehacer_check("ck_trabajo_objetivo", OBJETIVO_0012)
    _rehacer_check("ck_trabajo_orquestacion_tipo_trabajo", _tipos(TIPOS_0012))
    op.drop_constraint("uq_trabajo_campo_v2", "trabajo_orquestacion", type_="unique")
    op.drop_constraint("fk_trabajo_campo_v2", "trabajo_orquestacion", type_="foreignkey")
    op.drop_column("trabajo_orquestacion", "ejecucion_campo_v2_id")

    op.drop_table("carga_gestor_campo")
    op.drop_index("ix_unidad_campo_padre", table_name="unidad_territorial_campo")
    op.drop_table("unidad_territorial_campo")
    for indice in (
        "ix_asignacion_campo_cuenta",
        "ix_asignacion_campo_microtramo",
        "ix_asignacion_campo_prioridad",
        "ix_asignacion_campo_gestor",
        "ix_asignacion_campo_base",
        "ix_asignacion_campo_estado",
    ):
        op.drop_index(indice, table_name="asignacion_campo_cuenta")
    op.drop_table("asignacion_campo_cuenta")
    op.drop_index("ix_campo_v2_corte", table_name="ejecucion_campo_v2")
    for indice, estado in (
        ("ux_campo_v2_en_proceso", "EN_PROCESO"),
        ("ux_campo_v2_exitosa", "EXITOSA"),
    ):
        op.drop_index(
            indice,
            table_name="ejecucion_campo_v2",
            postgresql_where=sa.text(f"estado = '{estado}'"),
        )
    op.drop_table("ejecucion_campo_v2")

    op.drop_index("ix_gestor_campo_base", table_name="gestor_campo")
    op.drop_table("gestor_campo")
    op.drop_index("ix_ventana_campo_calendario", table_name="ventana_jornada_campo")
    op.drop_table("ventana_jornada_campo")
    op.drop_table("excepcion_calendario_campo")
    op.drop_table("calendario_campo")
    op.drop_table("version_configuracion_campo")

    for funcion in (*INMUTABLES, "campo_v2_terminado", "ventana_campo_sin_encimarse"):
        op.execute(sa.text(f"DROP FUNCTION {funcion}()"))
    op.drop_constraint("uq_territorial_v2_corte_bases", "ejecucion_territorial_v2", type_="unique")
