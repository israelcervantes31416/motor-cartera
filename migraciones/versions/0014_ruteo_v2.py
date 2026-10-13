"""ruteo/v2: las versiones de la red vial, las ejecuciones de ruteo sobre un plan de campo/v2, la
ruta de cada gestor y cada parada, y su trabajo RUTEO_V2

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-12 20:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

# Como en las demas migraciones, cada nombre se midio contra los 63 caracteres de PostgreSQL y es
# parte del contrato del esquema: sale en los IntegrityError y una migracion futura se refiere a el.
#
# La 0014 solo crea estructuras: no registra ninguna red vial ni rutea nada. Una red se prepara y se
# registra con `motor-cartera preparar-red-vial` y `registrar-red-vial`, y una ejecucion se pide por
# la API o el CLI. Nada de lo anterior cambia: ni campo/v2, ni territorial/v2, ni la geografia, ni
# decision/v2, ni ruteo/v1 y su flujo. A carga_gestor_campo solo se le agrega una restriccion unica
# sobre columnas que ya tenia, para que una ruta pueda exigir en la base que es de la carga de su
# gestor en el plan, con su misma base.

TIPOS_0013 = (
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
)
TIPOS_0014 = (*TIPOS_0013, "RUTEO_V2")
OBJETIVO_0013 = (
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
    "AND (tipo = 'CAMPO_V2') = (ejecucion_campo_v2_id IS NOT NULL)"
)
OBJETIVO_0014 = OBJETIVO_0013 + " AND (tipo = 'RUTEO_V2') = (ejecucion_ruteo_v2_id IS NOT NULL)"

PRIORIDADES = ("BAJA", "MEDIA", "ALTA", "MUY_ALTA")
MOTORES_RUTEO = ("OSRM", "SINTETICO")
ESTADOS_PARADA_RUTEO = ("PROGRAMADA", "SIN_TIEMPO", "NO_ENRUTABLE", "NO_ENRUTABLE_BASE")
MOTIVOS_SIN_TIEMPO = (
    "JORNADA_AGOTADA",
    "PRIORIDAD_SUPERIOR_AGOTO_JORNADA",
    "EXCEDE_JORNADA",
    "SIN_JORNADA",
)
MOTIVOS_NO_ENRUTABLE = ("SIN_SNAP", "SNAP_EXCESIVO", "RED_DESCONECTADA")
ESTADOS_RUTA = ("COMPLETA", "PARCIAL", "SIN_PARADAS_ENRUTABLES", "BASE_NO_ENRUTABLE", "SIN_JORNADA")
CONTEOS_DE_PARADAS = ("programadas", "sin_tiempo", "no_enrutables", "no_enrutables_base")
CONTEOS_DE_RUTAS = (
    "rutas_completas",
    "rutas_parciales",
    "rutas_sin_paradas",
    "bases_no_enrutables",
    "rutas_sin_jornada",
)


def _en(valores: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in valores) + ")"


_INSTANTES_DE_LA_PARADA = (
    "salida_anterior_at",
    "llegada_estimada",
    "inicio_servicio",
    "fin_servicio",
    "distancia_desde_anterior_m",
    "duracion_desde_anterior_s",
    "espera_s",
    "orden_visita",
)
COHERENCIA_DE_LA_PARADA = (
    f"estado IN {_en(ESTADOS_PARADA_RUTEO)} AND prioridad IN {_en(PRIORIDADES)} "
    "AND (motivo IS NULL) = (estado = 'PROGRAMADA') "
    f"AND (estado <> 'SIN_TIEMPO' OR motivo IN {_en(MOTIVOS_SIN_TIEMPO)}) "
    f"AND (estado <> 'NO_ENRUTABLE' OR motivo IN {_en(MOTIVOS_NO_ENRUTABLE)}) "
    "AND (estado <> 'NO_ENRUTABLE_BASE' OR motivo = 'BASE_NO_ENRUTABLE') "
    + " ".join(f"AND ({c} IS NOT NULL) = (estado = 'PROGRAMADA')" for c in _INSTANTES_DE_LA_PARADA)
    + " AND (orden_visita IS NULL OR orden_visita >= 1) "
    "AND (llegada_estimada IS NULL OR (salida_anterior_at <= llegada_estimada "
    "AND llegada_estimada <= inicio_servicio AND inicio_servicio <= fin_servicio)) "
    "AND (distancia_desde_anterior_m IS NULL OR distancia_desde_anterior_m >= 0) "
    "AND (duracion_desde_anterior_s IS NULL OR duracion_desde_anterior_s >= 0) "
    "AND (espera_s IS NULL OR espera_s >= 0) "
    "AND (snap_distancia_m IS NULL OR snap_distancia_m >= 0) "
    "AND (motivo IS DISTINCT FROM 'SIN_SNAP' OR snap_distancia_m IS NULL) "
    "AND (motivo IS DISTINCT FROM 'SNAP_EXCESIVO' OR snap_distancia_m IS NOT NULL) "
    "AND (estado <> 'PROGRAMADA' OR snap_distancia_m IS NOT NULL)"
)
_SUMA_DE_PARADAS = " + ".join(CONTEOS_DE_PARADAS)
COHERENCIA_DE_LA_RUTA = (
    f"estado_ruta IN {_en(ESTADOS_RUTA)} "
    f"AND paradas_campo >= 1 AND paradas_campo = {_SUMA_DE_PARADAS} "
    + " ".join(f"AND {c} >= 0" for c in CONTEOS_DE_PARADAS)
    + " AND (estado_ruta <> 'COMPLETA' OR programadas = paradas_campo) "
    "AND (estado_ruta <> 'PARCIAL' OR (programadas < paradas_campo AND no_enrutables < "
    "paradas_campo AND no_enrutables_base = 0)) "
    "AND (estado_ruta <> 'SIN_PARADAS_ENRUTABLES' OR no_enrutables = paradas_campo) "
    "AND (estado_ruta <> 'BASE_NO_ENRUTABLE' OR no_enrutables_base = paradas_campo) "
    "AND (estado_ruta <> 'SIN_JORNADA' OR sin_tiempo = paradas_campo) "
    "AND (motivo_base IS NOT NULL) = (estado_ruta = 'BASE_NO_ENRUTABLE') "
    "AND (motivo_base IS NULL OR motivo_base IN ('SIN_SNAP', 'SNAP_EXCESIVO')) "
    "AND (programadas > 0) = (salida_base_at IS NOT NULL) "
    "AND (salida_base_at IS NULL) = (regreso_base_at IS NULL) "
    "AND (salida_base_at IS NULL) = (salida_regreso_at IS NULL) "
    "AND (salida_base_at IS NULL) = (distancia_regreso_m IS NULL) "
    "AND (salida_base_at IS NULL) = (duracion_regreso_s IS NULL) "
    "AND (salida_base_at IS NULL) = (espera_regreso_s IS NULL) "
    "AND (jornada_inicio_at IS NULL) = (estado_ruta = 'SIN_JORNADA') "
    "AND (jornada_inicio_at IS NULL) = (jornada_fin_at IS NULL) "
    "AND distancia_total_m >= 0 AND duracion_traslado_s >= 0 AND duracion_servicio_s >= 0 "
    "AND duracion_espera_s >= 0 AND mejoras_2opt >= 0 "
    "AND duracion_total_s = duracion_traslado_s + duracion_servicio_s + duracion_espera_s "
    "AND (salida_base_at IS NULL OR (jornada_inicio_at <= salida_base_at "
    "AND salida_base_at <= salida_regreso_at AND salida_regreso_at <= regreso_base_at "
    "AND regreso_base_at <= jornada_fin_at "
    "AND regreso_base_at = salida_base_at + make_interval(secs => duracion_total_s))) "
    "AND (salida_base_at IS NOT NULL OR (distancia_total_m = 0 AND duracion_total_s = 0)) "
    "AND (base_snap_distancia_m IS NULL OR base_snap_distancia_m >= 0)"
)
_SUMA_DE_RUTAS = " + ".join(CONTEOS_DE_RUTAS)
PUBLICACION_DE_RUTEO_V2 = (
    f"(estado = 'EXITOSA' AND {_SUMA_DE_RUTAS} = gestores_objetivo "
    f"AND {_SUMA_DE_PARADAS} = paradas_objetivo AND paradas_objetivo >= gestores_objetivo "
    "AND (paradas_objetivo = 0) = (gestores_objetivo = 0)) "
    f"OR (estado <> 'EXITOSA' AND gestores_objetivo = 0 AND {_SUMA_DE_RUTAS} = 0 "
    f"AND paradas_objetivo = 0 AND {_SUMA_DE_PARADAS} = 0 AND distancia_total_m = 0 "
    "AND duracion_vial_total_s = 0 AND duracion_servicio_total_s = 0 "
    "AND duracion_espera_total_s = 0)"
)

INMUTABLES = {
    "ruteo_v2_inmutable": (
        ("ruta_gestor_v2", "parada_ruta_v2"),
        "ruteo/v2 no se reescribe",
        "Una ruta publicada no cambia: se pide otra ejecucion, con otras entradas.",
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


EJECUCION_TERMINADA = """
CREATE FUNCTION ruteo_v2_terminado() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' OR OLD.estado <> 'EN_PROCESO' THEN
        RAISE EXCEPTION 'Una ejecucion de ruteo/v2 terminada no cambia: % sobre % no se permite.',
            TG_OP, TG_TABLE_NAME
            USING ERRCODE = 'restrict_violation', CONSTRAINT = 'tr_ruteo_v2_terminado',
                  HINT = 'Una ejecucion publicada o fallida es historia: se pide otra.';
    END IF;
    RETURN NEW;
END
$$
"""
"""Una ejecucion solo cambia mientras se resuelve (EN_PROCESO): al publicarse o al fallar ya no
cambia, y ninguna se borra."""

RED_TERMINADA = """
CREATE FUNCTION red_vial_terminada() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' OR OLD.estado <> 'EN_PROCESO' THEN
        RAISE EXCEPTION 'Una version de la red vial terminada no cambia: % sobre % no se permite.',
            TG_OP, TG_TABLE_NAME
            USING ERRCODE = 'restrict_violation', CONSTRAINT = 'tr_red_vial_terminada',
                  HINT = 'Otra red, u otra preparacion que falle, es otra version.';
    END IF;
    RETURN NEW;
END
$$
"""
"""Una version de la red solo cambia mientras se prepara: PUBLICADA o FALLIDA ya no cambia, y
ninguna se borra."""

# Bajar a la 0013 quita ruteo/v2 entero: las versiones de la red, las ejecuciones, sus rutas, sus
# paradas y sus trabajos. No se reconstruyen solos (se vuelven a registrar las redes y a pedir las
# ejecuciones, y dan lo mismo). Lo que esta en curso se cierra primero. Nada de v0.12 se toca: ni
# campo/v2, ni la configuracion de campo, ni territorial/v2, ni la geografia, ni PostGIS, ni
# decision/v2, ni el lifecycle, ni los pagos, ni la historia, ni los artefactos, ni ruteo/v1.
CIERRE = (
    "Cerrado al bajar la base a la 0013: la v0.12 no tiene ruteo/v2. campo/v2, territorial/v2 y "
    "todo lo demas siguen intactos."
)
CERRAR_EJECUCIONES = sa.text(
    "UPDATE ejecucion_ruteo_v2 SET estado = 'FALLIDA', resultado = 'CERRADA_AL_BAJAR', "
    "gestores_objetivo = 0, paradas_objetivo = 0, "
    + ", ".join(f"{c} = 0" for c in (*CONTEOS_DE_RUTAS, *CONTEOS_DE_PARADAS))
    + ", distancia_total_m = 0, duracion_vial_total_s = 0, duracion_servicio_total_s = 0, "
    "duracion_espera_total_s = 0, terminada_en = now(), detalle = :motivo "
    "WHERE estado = 'EN_PROCESO'"
).bindparams(motivo=CIERRE)
CERRAR_TRABAJOS = sa.text(
    "UPDATE trabajo_orquestacion SET estado = 'FALLIDO', worker_id = NULL, lease_hasta = NULL, "
    "terminado_en = now(), ultimo_error = :motivo "
    "WHERE tipo = 'RUTEO_V2' AND estado IN ('PENDIENTE', 'EJECUTANDO')"
).bindparams(motivo=CIERRE)
"""Lo primero que hace bajar la 0014: cerrar las ejecuciones en curso y sus trabajos, antes de
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


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_carga_campo_base",
        "carga_gestor_campo",
        ["ejecucion_campo_v2_id", "gestor_campo_id", "base_operativa_id"],
    )
    _red_vial()
    _ejecuciones()
    _inmutables()
    _cola()


def _red_vial() -> None:
    op.create_table(
        "version_red_vial",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("red_vial_version_id", sa.Uuid(), nullable=False),
        _texto("huella", 64, nullable=False),
        _texto("proveedor_datos", 40, nullable=False),
        _texto("proveedor_extracto", 120),
        _texto("licencia", 40, nullable=False),
        sa.Column("fecha_snapshot", sa.Date(), nullable=True),
        _texto("region", 120, nullable=False),
        _texto("archivo_fuente", 255, nullable=False),
        _texto("url_fuente", 500),
        _texto("sha256_fuente", 64, nullable=False),
        _texto("motor_ruteo", 16, nullable=False),
        _texto("version_motor_ruteo", 32, nullable=False),
        _texto("distribucion_motor", 300, nullable=False),
        _texto("algoritmo_motor", 16, nullable=False),
        _texto("perfil", 32, nullable=False),
        _texto("hash_perfil", 64, nullable=False),
        _json("identidad"),
        _texto("hash_manifest", 64),
        _json("manifiesto", nullable=True),
        sa.Column(
            "estado",
            _vocabulario("estado_version_red_vial", 12, "EN_PROCESO", "PUBLICADA", "FALLIDA"),
            nullable=False,
        ),
        _json("metadatos"),
        _instante("creada_en", nullable=False),
        _instante("publicada_en"),
        _texto("detalle"),
        sa.CheckConstraint(
            "huella ~ '^[0-9a-f]{64}$' AND sha256_fuente ~ '^[0-9a-f]{64}$' "
            "AND hash_perfil ~ '^[0-9a-f]{64}$' "
            "AND (hash_manifest IS NULL OR hash_manifest ~ '^[0-9a-f]{64}$')",
            name=op.f("ck_red_vial_hashes"),
        ),
        sa.CheckConstraint(
            f"motor_ruteo IN {_en(MOTORES_RUTEO)} "
            "AND (estado = 'PUBLICADA') = (hash_manifest IS NOT NULL) "
            "AND (estado = 'PUBLICADA') = (manifiesto IS NOT NULL) "
            "AND (estado = 'PUBLICADA') = (publicada_en IS NOT NULL) "
            "AND (estado = 'FALLIDA') = (detalle IS NOT NULL)",
            name=op.f("ck_red_vial_publicacion"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_version_red_vial")),
        sa.UniqueConstraint(
            "red_vial_version_id", name=op.f("uq_version_red_vial_red_vial_version_id")
        ),
    )
    for indice, estado in (
        ("ux_red_vial_publicada", "PUBLICADA"),
        ("ux_red_vial_en_proceso", "EN_PROCESO"),
    ):
        op.create_index(
            indice,
            "version_red_vial",
            ["huella"],
            unique=True,
            postgresql_where=sa.text(f"estado = '{estado}'"),
        )


def _ejecuciones() -> None:
    op.create_table(
        "ejecucion_ruteo_v2",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ruteo_run_id", sa.Uuid(), nullable=False),
        _entero("ejecucion_campo_v2_id"),
        _entero("version_red_vial_id"),
        sa.Column("fecha_operacion", sa.Date(), nullable=False),
        _texto("version_motor", 32, nullable=False),
        _texto("version_politica", 40, nullable=False),
        _texto("hash_politica", 64, nullable=False),
        _texto("firma_inputs", 64, nullable=False),
        sa.Column(
            "estado",
            _vocabulario("estado_ruteo_v2", 12, "EN_PROCESO", "EXITOSA", "FALLIDA"),
            nullable=False,
        ),
        _texto("resultado", 40),
        _entero("gestores_objetivo"),
        *(_entero(c) for c in CONTEOS_DE_RUTAS),
        _grande("paradas_objetivo"),
        *(_grande(c) for c in CONTEOS_DE_PARADAS),
        _grande("distancia_total_m"),
        _grande("duracion_vial_total_s"),
        _grande("duracion_servicio_total_s"),
        _grande("duracion_espera_total_s"),
        _json("distribucion", nullable=True),
        _instante("iniciada_en", nullable=False),
        _instante("terminada_en"),
        _texto("detalle"),
        sa.CheckConstraint(
            "firma_inputs ~ '^[0-9a-f]{64}$' AND hash_politica ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_ruteo_v2_firmas"),
        ),
        sa.CheckConstraint(
            "(estado = 'EN_PROCESO') = (resultado IS NULL)", name=op.f("ck_ruteo_v2_resultado")
        ),
        sa.CheckConstraint(
            "gestores_objetivo >= 0 AND paradas_objetivo >= 0 AND distancia_total_m >= 0 "
            "AND duracion_vial_total_s >= 0 AND duracion_servicio_total_s >= 0 "
            "AND duracion_espera_total_s >= 0 AND "
            + " AND ".join(f"{c} >= 0" for c in (*CONTEOS_DE_RUTAS, *CONTEOS_DE_PARADAS)),
            name=op.f("ck_ruteo_v2_conteos"),
        ),
        sa.CheckConstraint(PUBLICACION_DE_RUTEO_V2, name=op.f("ck_ruteo_v2_publicacion")),
        sa.ForeignKeyConstraint(
            ["ejecucion_campo_v2_id"], ["ejecucion_campo_v2.id"], name="fk_ruteo_v2_campo"
        ),
        sa.ForeignKeyConstraint(
            ["version_red_vial_id"], ["version_red_vial.id"], name="fk_ruteo_v2_red"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ejecucion_ruteo_v2")),
        sa.UniqueConstraint("ruteo_run_id", name=op.f("uq_ejecucion_ruteo_v2_ruteo_run_id")),
        sa.UniqueConstraint("id", "ejecucion_campo_v2_id", name="uq_ruteo_v2_campo"),
    )
    for indice, estado in (
        ("ux_ruteo_v2_exitosa", "EXITOSA"),
        ("ux_ruteo_v2_en_proceso", "EN_PROCESO"),
    ):
        op.create_index(
            indice,
            "ejecucion_ruteo_v2",
            ["firma_inputs"],
            unique=True,
            postgresql_where=sa.text(f"estado = '{estado}'"),
        )
    op.create_index("ix_ruteo_v2_campo", "ejecucion_ruteo_v2", ["ejecucion_campo_v2_id", "id"])

    # La ruta de cada gestor y cada parada, sin id propio: su llave es la natural.
    op.create_table(
        "ruta_gestor_v2",
        _entero("ejecucion_ruteo_v2_id"),
        _entero("gestor_campo_id"),
        sa.Column("ruta_id", sa.Uuid(), nullable=False),
        _entero("ejecucion_campo_v2_id"),
        _entero("base_operativa_id"),
        sa.Column("fecha_operacion", sa.Date(), nullable=False),
        _texto("estado_ruta", 24, nullable=False),
        _texto("motivo_base", 24),
        _entero("paradas_campo"),
        *(_entero(c) for c in CONTEOS_DE_PARADAS),
        sa.Column("base_snap_distancia_m", sa.Numeric(precision=12, scale=2), nullable=True),
        _instante("jornada_inicio_at"),
        _instante("jornada_fin_at"),
        _instante("salida_base_at"),
        _instante("salida_regreso_at"),
        _instante("regreso_base_at"),
        _grande("distancia_regreso_m", nullable=True),
        _entero("duracion_regreso_s", nullable=True),
        _entero("espera_regreso_s", nullable=True),
        _grande("distancia_total_m"),
        _entero("duracion_traslado_s"),
        _entero("duracion_servicio_s"),
        _entero("duracion_espera_s"),
        _entero("duracion_total_s"),
        _entero("mejoras_2opt"),
        _texto("hash_matriz", 64),
        _texto("hash_secuencia", 64, nullable=False),
        _instante("creada_en", nullable=False),
        sa.CheckConstraint(COHERENCIA_DE_LA_RUTA, name=op.f("ck_ruta_v2_coherencia")),
        sa.CheckConstraint(
            "(hash_matriz IS NULL OR hash_matriz ~ '^[0-9a-f]{64}$') "
            "AND hash_secuencia ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_ruta_v2_hashes"),
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_ruteo_v2_id", "ejecucion_campo_v2_id"],
            ["ejecucion_ruteo_v2.id", "ejecucion_ruteo_v2.ejecucion_campo_v2_id"],
            name="fk_ruta_v2_ejecucion",
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_campo_v2_id", "gestor_campo_id", "base_operativa_id"],
            [
                "carga_gestor_campo.ejecucion_campo_v2_id",
                "carga_gestor_campo.gestor_campo_id",
                "carga_gestor_campo.base_operativa_id",
            ],
            name="fk_ruta_v2_carga",
        ),
        sa.PrimaryKeyConstraint(
            "ejecucion_ruteo_v2_id", "gestor_campo_id", name=op.f("pk_ruta_gestor_v2")
        ),
        sa.UniqueConstraint("ruta_id", name=op.f("uq_ruta_gestor_v2_ruta_id")),
        sa.UniqueConstraint(
            "ejecucion_ruteo_v2_id",
            "gestor_campo_id",
            "ejecucion_campo_v2_id",
            name="uq_ruta_v2_campo",
        ),
    )
    op.create_table(
        "parada_ruta_v2",
        sa.Column("snap_distancia_m", sa.Numeric(precision=12, scale=2), nullable=True),
        _instante("salida_anterior_at"),
        _instante("llegada_estimada"),
        _instante("inicio_servicio"),
        _instante("fin_servicio"),
        _grande("distancia_desde_anterior_m", nullable=True),
        _entero("ejecucion_ruteo_v2_id"),
        _entero("cuenta_canonica_id"),
        _entero("gestor_campo_id"),
        _entero("ejecucion_campo_v2_id"),
        _entero("orden_visita", nullable=True),
        _entero("duracion_desde_anterior_s", nullable=True),
        _entero("espera_s", nullable=True),
        _texto("prioridad", 12, nullable=False),
        _texto("estado", 20, nullable=False),
        _texto("motivo", 40),
        sa.CheckConstraint(COHERENCIA_DE_LA_PARADA, name=op.f("ck_parada_v2_coherencia")),
        sa.ForeignKeyConstraint(
            ["ejecucion_ruteo_v2_id", "gestor_campo_id", "ejecucion_campo_v2_id"],
            [
                "ruta_gestor_v2.ejecucion_ruteo_v2_id",
                "ruta_gestor_v2.gestor_campo_id",
                "ruta_gestor_v2.ejecucion_campo_v2_id",
            ],
            name="fk_parada_v2_ruta",
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_campo_v2_id", "cuenta_canonica_id"],
            [
                "asignacion_campo_cuenta.ejecucion_campo_v2_id",
                "asignacion_campo_cuenta.cuenta_canonica_id",
            ],
            name="fk_parada_v2_asignacion",
        ),
        sa.PrimaryKeyConstraint(
            "ejecucion_ruteo_v2_id", "cuenta_canonica_id", name=op.f("pk_parada_ruta_v2")
        ),
        sa.UniqueConstraint(
            "ejecucion_ruteo_v2_id", "gestor_campo_id", "orden_visita", name="uq_parada_v2_orden"
        ),
    )
    op.create_index(
        "ix_parada_v2_estado",
        "parada_ruta_v2",
        ["ejecucion_ruteo_v2_id", "estado", "cuenta_canonica_id"],
    )
    op.create_index(
        "ix_parada_v2_cuenta", "parada_ruta_v2", ["cuenta_canonica_id", "ejecucion_ruteo_v2_id"]
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
            "CREATE TRIGGER tr_ruteo_v2_terminado BEFORE UPDATE OR DELETE ON ejecucion_ruteo_v2 "
            "FOR EACH ROW EXECUTE FUNCTION ruteo_v2_terminado()"
        )
    )
    op.execute(sa.text(RED_TERMINADA))
    op.execute(
        sa.text(
            "CREATE TRIGGER tr_red_vial_terminada BEFORE UPDATE OR DELETE ON version_red_vial "
            "FOR EACH ROW EXECUTE FUNCTION red_vial_terminada()"
        )
    )


def _cola() -> None:
    # El trabajo RUTEO_V2, en la misma cola durable: uno por ejecucion. Cabe en los 24 caracteres
    # de tipo; solo se rehacen los CHECK del tipo y del objetivo.
    op.add_column(
        "trabajo_orquestacion", sa.Column("ejecucion_ruteo_v2_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "fk_trabajo_ruteo_v2",
        "trabajo_orquestacion",
        "ejecucion_ruteo_v2",
        ["ejecucion_ruteo_v2_id"],
        ["id"],
    )
    op.create_unique_constraint(
        "uq_trabajo_ruteo_v2", "trabajo_orquestacion", ["ejecucion_ruteo_v2_id"]
    )
    _rehacer_check("ck_trabajo_orquestacion_tipo_trabajo", _tipos(TIPOS_0014))
    _rehacer_check("ck_trabajo_objetivo", OBJETIVO_0014)


def downgrade() -> None:
    # Primero se cierra lo que estaba en curso: las ejecuciones (si un worker tiene una bloqueada,
    # termina antes de que el UPDATE la tome) y despues sus trabajos. Despues se van esos trabajos,
    # que la 0013 no sabria guardar, y las estructuras de la 0014. Las versiones de la red en curso
    # se van con su tabla: ninguna ejecucion publicada las necesita en la 0013.
    op.execute(CERRAR_EJECUCIONES)
    op.execute(CERRAR_TRABAJOS)
    op.execute(sa.text("DELETE FROM trabajo_orquestacion WHERE tipo = 'RUTEO_V2'"))
    _rehacer_check("ck_trabajo_objetivo", OBJETIVO_0013)
    _rehacer_check("ck_trabajo_orquestacion_tipo_trabajo", _tipos(TIPOS_0013))
    op.drop_constraint("uq_trabajo_ruteo_v2", "trabajo_orquestacion", type_="unique")
    op.drop_constraint("fk_trabajo_ruteo_v2", "trabajo_orquestacion", type_="foreignkey")
    op.drop_column("trabajo_orquestacion", "ejecucion_ruteo_v2_id")

    op.drop_index("ix_parada_v2_cuenta", table_name="parada_ruta_v2")
    op.drop_index("ix_parada_v2_estado", table_name="parada_ruta_v2")
    op.drop_table("parada_ruta_v2")
    op.drop_table("ruta_gestor_v2")
    op.drop_index("ix_ruteo_v2_campo", table_name="ejecucion_ruteo_v2")
    for indice, estado in (
        ("ux_ruteo_v2_en_proceso", "EN_PROCESO"),
        ("ux_ruteo_v2_exitosa", "EXITOSA"),
    ):
        op.drop_index(
            indice,
            table_name="ejecucion_ruteo_v2",
            postgresql_where=sa.text(f"estado = '{estado}'"),
        )
    op.drop_table("ejecucion_ruteo_v2")
    for indice, estado in (
        ("ux_red_vial_en_proceso", "EN_PROCESO"),
        ("ux_red_vial_publicada", "PUBLICADA"),
    ):
        op.drop_index(
            indice,
            table_name="version_red_vial",
            postgresql_where=sa.text(f"estado = '{estado}'"),
        )
    op.drop_table("version_red_vial")

    for funcion in (*INMUTABLES, "ruteo_v2_terminado", "red_vial_terminada"):
        op.execute(sa.text(f"DROP FUNCTION {funcion}()"))
    op.drop_constraint("uq_carga_campo_base", "carga_gestor_campo", type_="unique")
