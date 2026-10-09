"""lifecycle de cobranza: eventos, gestiones, visitas, promesas, convenios, atribucion y evaluacion

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-09 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

# Como en las demas migraciones, cada nombre se midio contra los 63 caracteres de PostgreSQL y es
# parte del contrato del esquema: sale en los IntegrityError y una migracion futura se refiere a el.
#
# La 0010 solo crea estructuras: no registra ningun evento, ni atribuye ni evalua nada, y no toca
# ningun snapshot (lo que un corte dice de la promesa o del plan de una cuenta no se convierte en un
# evento). Los eventos llegan por la API o por `motor-cartera cargar-lifecycle`; la atribucion y la
# evaluacion de promesas las encolan la API, `backfill-atribucion` y `backfill-lifecycle`.

# La cola gana los tipos ATRIBUCION y EVALUACION_PROMESAS, y sus objetivos. EVALUACION_PROMESAS no
# cabe en los 16 caracteres de tipo: la columna pasa a 24, y los CHECK del tipo y del objetivo se
# rehacen con los nuevos.
TIPOS_0009 = (
    "INGESTA",
    "DECISION",
    "TERRITORIAL",
    "RUTEO",
    "INGESTA_PAGOS",
    "HISTORIA",
    "MOTOR_PAGOS",
)
TIPOS_0010 = (*TIPOS_0009, "ATRIBUCION", "EVALUACION_PROMESAS")
OBJETIVO_0009 = (
    "(tipo = 'INGESTA') = (corrida_id IS NOT NULL) "
    "AND (tipo = 'DECISION') = (ejecucion_decision_id IS NOT NULL) "
    "AND (tipo = 'TERRITORIAL') = (ejecucion_territorial_id IS NOT NULL) "
    "AND (tipo = 'RUTEO') = (ejecucion_ruteo_id IS NOT NULL) "
    "AND (tipo = 'INGESTA_PAGOS') = (ingesta_pagos_id IS NOT NULL) "
    "AND (tipo = 'HISTORIA') = (ejecucion_historia_id IS NOT NULL) "
    "AND (tipo = 'MOTOR_PAGOS') = (ejecucion_motor_pagos_id IS NOT NULL)"
)
OBJETIVO_0010 = (
    OBJETIVO_0009 + " AND (tipo = 'ATRIBUCION') = (ejecucion_atribucion_id IS NOT NULL) "
    "AND (tipo = 'EVALUACION_PROMESAS') = (ejecucion_evaluacion_promesas_id IS NOT NULL)"
)

CIERRES = "('GESTION_ANULADA', 'PROMESA_CANCELADA', 'CONVENIO_CANCELADO')"
PATRON_LLAVE = "^[A-Za-z0-9._:-]{8,128}$"
PATRON_ACTOR = "^[A-Za-z0-9._:-]{1,64}$"
COHERENCIA = (
    "(resultado <> 'SIN_RESPUESTA' OR nivel_contacto IN ('SIN_CONTACTO', 'NO_APLICA')) "
    "AND (resultado NOT IN ('CONTACTO', 'RECHAZO', 'PROMESA', 'CONVENIO') "
    "OR nivel_contacto IN ('CONTACTO_TERCERO', 'CONTACTO_TITULAR')) "
    "AND (resultado <> 'VISITA_REALIZADA' OR canal = 'CAMPO') "
    "AND (canal <> 'CAMPO' OR resultado NOT IN ('SIN_RESPUESTA', 'CONTACTO')) "
    "AND (nivel_contacto <> 'NO_APLICA' OR canal IN ('DIGITAL', 'OTRO'))"
)
MEDIO = (
    "medio IS NULL OR (canal = 'TELEFONICA' AND medio = 'LLAMADA') "
    "OR (canal = 'DIGITAL' AND medio IN ('SMS', 'WHATSAPP', 'EMAIL'))"
)
CONTEOS_ATRIBUCION = (
    "movimientos_evaluados >= 0 AND asociados >= 0 AND ambiguos >= 0 AND sin_candidato >= 0 "
    "AND candidatos >= 0 AND movimientos_anulados >= 0 AND gestiones_leidas >= 0 "
    "AND gestiones_anuladas >= 0 AND gestiones_anuladas <= gestiones_leidas "
    "AND movimientos_anulados <= movimientos_evaluados "
    "AND movimientos_evaluados = asociados + ambiguos + sin_candidato "
    "AND candidatos >= asociados + 2 * ambiguos"
)
METRICAS_ATRIBUCION = (
    "movimientos_evaluados",
    "asociados",
    "ambiguos",
    "sin_candidato",
    "candidatos",
    "movimientos_anulados",
    "gestiones_leidas",
    "gestiones_anuladas",
)
MONTOS_ATRIBUCION = ("monto_asociado", "monto_ambiguo", "monto_sin_candidato", "monto_anulado")
CLASES_EVALUACION = (
    "promesas_evaluadas = pendientes + cumplidas + parciales + incumplidas + canceladas "
    "+ no_evaluables"
)
CONTEOS_EVALUACION = (
    "promesas_evaluadas >= 0 AND pendientes >= 0 AND cumplidas >= 0 AND parciales >= 0 "
    "AND incumplidas >= 0 AND canceladas >= 0 AND no_evaluables >= 0 "
    "AND monto_prometido >= 0 AND monto_observado >= 0"
)
METRICAS_EVALUACION = (
    "promesas_evaluadas",
    "pendientes",
    "cumplidas",
    "parciales",
    "incumplidas",
    "canceladas",
    "no_evaluables",
)

TABLAS_DEL_LIFECYCLE = (
    "evento_lifecycle",
    "gestion_cobranza",
    "visita_campo",
    "promesa_pago",
    "convenio_cobranza",
    "cuota_convenio",
)
"""Las que solo se agregan: un trigger rechaza cualquier UPDATE o DELETE sobre ellas."""

SOLO_AGREGA = """
CREATE FUNCTION lifecycle_solo_agrega() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'El lifecycle solo se agrega: % sobre % no se permite.', TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation', CONSTRAINT = 'tr_lifecycle_solo_agrega',
              HINT = 'Lo registrado por error se anula con otro evento.';
END
$$
"""
"""Un UPDATE o un DELETE sobre una tabla del lifecycle, aunque no toque ninguna fila, falla con
restrict_violation. TRUNCATE no pasa por aqui: es una operacion de administracion, no de la
aplicacion."""

CUOTAS_COHERENTES = """
CREATE FUNCTION convenio_cuotas_coherentes() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    clave bigint;
    convenio record;
    cuantas integer;
    suma numeric;
    mayor integer;
    primera date;
    ultima date;
    desordenadas integer;
BEGIN
    IF TG_TABLE_NAME = 'convenio_cobranza' THEN
        clave := NEW.id;
    ELSE
        clave := NEW.convenio_cobranza_id;
    END IF;
    SELECT cuotas, monto_total_acordado, fecha_inicio, fecha_fin INTO convenio
    FROM convenio_cobranza WHERE id = clave;
    SELECT count(*), coalesce(sum(monto), 0), coalesce(max(numero), 0),
           min(fecha_vencimiento), max(fecha_vencimiento)
    INTO cuantas, suma, mayor, primera, ultima
    FROM cuota_convenio WHERE convenio_cobranza_id = clave;
    IF cuantas <> convenio.cuotas THEN
        RAISE EXCEPTION 'El convenio % declara % cuotas y tiene %.', clave, convenio.cuotas,
            cuantas USING ERRCODE = 'check_violation', CONSTRAINT = 'tr_convenio_cuotas';
    END IF;
    IF cuantas > 0 THEN
        IF suma <> convenio.monto_total_acordado THEN
            RAISE EXCEPTION 'Las cuotas del convenio % suman % y su monto total es %.', clave,
                suma, convenio.monto_total_acordado
                USING ERRCODE = 'check_violation', CONSTRAINT = 'tr_convenio_cuotas';
        END IF;
        IF mayor <> cuantas THEN
            RAISE EXCEPTION 'Las cuotas del convenio % no se numeran de 1 a %.', clave, cuantas
                USING ERRCODE = 'check_violation', CONSTRAINT = 'tr_convenio_cuotas';
        END IF;
        SELECT count(*) INTO desordenadas FROM (
            SELECT fecha_vencimiento,
                   lag(fecha_vencimiento) OVER (ORDER BY numero) AS anterior
            FROM cuota_convenio WHERE convenio_cobranza_id = clave) x
        WHERE anterior >= fecha_vencimiento;
        IF desordenadas > 0 THEN
            RAISE EXCEPTION 'Las cuotas del convenio % no vencen en fechas crecientes.', clave
                USING ERRCODE = 'check_violation', CONSTRAINT = 'tr_convenio_cuotas';
        END IF;
        IF primera < convenio.fecha_inicio
           OR (convenio.fecha_fin IS NOT NULL AND ultima > convenio.fecha_fin) THEN
            RAISE EXCEPTION 'Las cuotas del convenio % vencen fuera de su vigencia.', clave
                USING ERRCODE = 'check_violation', CONSTRAINT = 'tr_convenio_cuotas';
        END IF;
    END IF;
    RETURN NULL;
END
$$
"""
"""Lo que se comprueba de un convenio al confirmar su transaccion: tiene exactamente las cuotas que
declaro y, si declaro alguna, suman su monto total, se numeran de 1 a n, vencen en fechas
estrictamente crecientes y dentro de su vigencia. Es diferido porque el convenio y sus cuotas se
insertan en sentencias distintas de la misma transaccion; quien registra puede adelantarlo con SET
CONSTRAINTS ... IMMEDIATE."""

# Bajar a la 0009 quita el lifecycle entero, con la atribucion y la evaluacion de promesas. A
# diferencia de la historia y del motor de pagos, los eventos operacionales no se reconstruyen desde
# ninguna fuente: quien baje la base tiene que conservarlos antes (un respaldo, o el JSONL con que
# se importaron). Lo que esta en curso se cierra primero, con este motivo. Nada de v0.8 se toca: ni
# los pagos observados, ni los movimientos, ni los snapshots, ni las cuentas canonicas, ni los
# datasets, sus artefactos, las corridas, las ingestas o lo que publicaron los demas motores.
CIERRE = (
    "Cerrada al bajar la base a la 0009: la v0.8 no tiene lifecycle de cobranza, atribucion ni "
    "evaluacion de promesas. Los movimientos economicos y los pagos observados siguen intactos."
)
RESULTADO_DEL_CIERRE = "CERRADA_AL_BAJAR"
CERRAR_ATRIBUCIONES = sa.text(
    "UPDATE ejecucion_atribucion SET estado = 'FALLIDA', resultado = :resultado, "
    "terminada_en = now(), detalle = :motivo WHERE estado = 'EN_PROCESO'"
).bindparams(resultado=RESULTADO_DEL_CIERRE, motivo=CIERRE)
CERRAR_EVALUACIONES = sa.text(
    "UPDATE ejecucion_evaluacion_promesas SET estado = 'FALLIDA', resultado = :resultado, "
    "terminada_en = now(), detalle = :motivo WHERE estado = 'EN_PROCESO'"
).bindparams(resultado=RESULTADO_DEL_CIERRE, motivo=CIERRE)
CERRAR_TRABAJOS = sa.text(
    "UPDATE trabajo_orquestacion SET estado = 'FALLIDO', worker_id = NULL, lease_hasta = NULL, "
    "terminado_en = now(), ultimo_error = :motivo "
    "WHERE tipo IN ('ATRIBUCION', 'EVALUACION_PROMESAS') AND estado IN ('PENDIENTE', 'EJECUTANDO')"
).bindparams(motivo=CIERRE)
"""Lo primero que hace bajar la 0010: cerrar la atribucion y la evaluacion en curso, antes de
quitar sus tablas."""


def _tipos(tipos: tuple[str, ...]) -> str:
    return "tipo IN (" + ", ".join(f"'{t}'" for t in tipos) + ")"


def _rehacer_check(nombre: str, condicion: str) -> None:
    op.drop_constraint(op.f(nombre), "trabajo_orquestacion", type_="check")
    op.create_check_constraint(op.f(nombre), "trabajo_orquestacion", condicion)


def _texto(nombre: str, largo: int | None = None, nullable: bool = True) -> sa.Column:
    return sa.Column(nombre, sqlmodel.sql.sqltypes.AutoString(length=largo), nullable=nullable)


def _importe(nombre: str, digitos: int = 14) -> sa.Column:
    return sa.Column(nombre, sa.Numeric(precision=digitos, scale=2), nullable=False)


def _instante(nombre: str, nullable: bool = False) -> sa.Column:
    return sa.Column(nombre, sa.DateTime(timezone=True), nullable=nullable)


def _vocabulario(nombre: str, largo: int, *valores: str) -> sa.Enum:
    return sa.Enum(*valores, name=nombre, native_enum=False, create_constraint=True, length=largo)


def _estado(nombre: str) -> sa.Enum:
    return _vocabulario(nombre, 12, "EN_PROCESO", "EXITOSA", "FALLIDA")


def upgrade() -> None:
    _lifecycle()
    _atribucion()
    _evaluacion()
    _cola()


def _lifecycle() -> None:
    # El sobre de cada evento operacional. Las columnas de 8 bytes primero.
    op.create_table(
        "evento_lifecycle",
        sa.Column("id", sa.BigInteger(), nullable=False),
        _instante("ocurrido_en"),
        _instante("registrado_en"),
        sa.Column("evento_relacionado_id", sa.BigInteger(), nullable=True),
        sa.Column("evento_id", sa.Uuid(), nullable=False),
        sa.Column("cuenta_canonica_id", sa.Integer(), nullable=False),
        sa.Column(
            "tipo_evento",
            _vocabulario(
                "tipo_evento_lifecycle",
                24,
                "GESTION_REGISTRADA",
                "GESTION_ANULADA",
                "PROMESA_CREADA",
                "PROMESA_CANCELADA",
                "CONVENIO_CREADO",
                "CONVENIO_CANCELADO",
            ),
            nullable=False,
        ),
        sa.Column(
            "origen_registro",
            _vocabulario("origen_registro", 12, "API", "IMPORTACION"),
            nullable=False,
        ),
        _texto("despacho_id", 32, nullable=False),
        _texto("cartera_id", 32, nullable=False),
        _texto("version_evento", 32, nullable=False),
        _texto("idempotency_key", 128, nullable=False),
        _texto("actor_ref", 64),
        _texto("motivo", 500),
        sa.Column("payload_hash", sa.LargeBinary(), nullable=False),
        sa.CheckConstraint(
            "(tipo_evento = 'GESTION_REGISTRADA') = (evento_relacionado_id IS NULL)",
            name=op.f("ck_evento_relacionado"),
        ),
        sa.CheckConstraint(
            f"(motivo IS NOT NULL) = (tipo_evento IN {CIERRES})", name=op.f("ck_evento_motivo")
        ),
        sa.CheckConstraint(
            "registrado_en - ocurrido_en >= interval '-5 minutes'", name=op.f("ck_evento_tiempos")
        ),
        sa.CheckConstraint("octet_length(payload_hash) = 32", name=op.f("ck_evento_huella")),
        sa.CheckConstraint(f"idempotency_key ~ '{PATRON_LLAVE}'", name=op.f("ck_evento_llave")),
        sa.CheckConstraint(f"actor_ref ~ '{PATRON_ACTOR}'", name=op.f("ck_evento_actor")),
        sa.ForeignKeyConstraint(
            ["cuenta_canonica_id"], ["cuenta_canonica.id"], name="fk_evento_cuenta"
        ),
        sa.ForeignKeyConstraint(
            ["evento_relacionado_id"], ["evento_lifecycle.id"], name="fk_evento_relacionado"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evento_lifecycle")),
        sa.UniqueConstraint("evento_id", name=op.f("uq_evento_lifecycle_evento_id")),
        sa.UniqueConstraint(
            "despacho_id", "cartera_id", "idempotency_key", name="uq_evento_idempotencia"
        ),
    )
    op.create_index(
        "ux_evento_relacionado",
        "evento_lifecycle",
        ["evento_relacionado_id", "tipo_evento"],
        unique=True,
        postgresql_where=sa.text("evento_relacionado_id IS NOT NULL"),
    )
    op.create_index(
        "ix_evento_cuenta_ocurrido", "evento_lifecycle", ["cuenta_canonica_id", "ocurrido_en"]
    )

    op.create_table(
        "gestion_cobranza",
        sa.Column("id", sa.BigInteger(), nullable=False),
        _instante("ocurrido_en"),
        sa.Column("evento_lifecycle_id", sa.BigInteger(), nullable=False),
        sa.Column("gestion_id", sa.Uuid(), nullable=False),
        sa.Column("cuenta_canonica_id", sa.Integer(), nullable=False),
        sa.Column(
            "canal",
            _vocabulario("canal_gestion", 12, "DIGITAL", "TELEFONICA", "CAMPO", "OTRO"),
            nullable=False,
        ),
        sa.Column(
            "medio",
            _vocabulario("medio_gestion", 12, "LLAMADA", "SMS", "WHATSAPP", "EMAIL"),
            nullable=True,
        ),
        sa.Column(
            "nivel_contacto",
            _vocabulario(
                "nivel_contacto",
                20,
                "NO_APLICA",
                "SIN_CONTACTO",
                "CONTACTO_TERCERO",
                "CONTACTO_TITULAR",
            ),
            nullable=False,
        ),
        sa.Column(
            "resultado",
            _vocabulario(
                "resultado_gestion",
                20,
                "SIN_RESPUESTA",
                "CONTACTO",
                "RECHAZO",
                "PROMESA",
                "CONVENIO",
                "VISITA_REALIZADA",
                "OTRO",
            ),
            nullable=False,
        ),
        _texto("actor_ref", 64),
        _texto("observacion", 500),
        sa.CheckConstraint(MEDIO, name=op.f("ck_gestion_medio")),
        sa.CheckConstraint(COHERENCIA, name=op.f("ck_gestion_coherencia")),
        sa.CheckConstraint(f"actor_ref ~ '{PATRON_ACTOR}'", name=op.f("ck_gestion_actor")),
        sa.ForeignKeyConstraint(
            ["evento_lifecycle_id"], ["evento_lifecycle.id"], name="fk_gestion_evento"
        ),
        sa.ForeignKeyConstraint(
            ["cuenta_canonica_id"], ["cuenta_canonica.id"], name="fk_gestion_cuenta"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_gestion_cobranza")),
        sa.UniqueConstraint("gestion_id", name=op.f("uq_gestion_cobranza_gestion_id")),
        sa.UniqueConstraint("evento_lifecycle_id", name="uq_gestion_evento"),
    )
    op.create_index(
        "ix_gestion_cuenta_ocurrido", "gestion_cobranza", ["cuenta_canonica_id", "ocurrido_en"]
    )

    op.create_table(
        "visita_campo",
        sa.Column("id", sa.BigInteger(), nullable=False),
        _instante("inicio", nullable=True),
        _instante("fin", nullable=True),
        sa.Column("gestion_cobranza_id", sa.BigInteger(), nullable=False),
        sa.Column("visita_id", sa.Uuid(), nullable=False),
        sa.Column(
            "resultado",
            _vocabulario(
                "resultado_visita",
                20,
                "NO_LOCALIZADO",
                "SIN_CONTACTO",
                "CONTACTO_TERCERO",
                "CONTACTO_TITULAR",
                "DOMICILIO_NO_VALIDO",
                "OTRO",
            ),
            nullable=False,
        ),
        _texto("observacion", 500),
        sa.CheckConstraint(
            "inicio IS NULL OR fin IS NULL OR inicio <= fin", name=op.f("ck_visita_intervalo")
        ),
        sa.ForeignKeyConstraint(
            ["gestion_cobranza_id"], ["gestion_cobranza.id"], name="fk_visita_gestion"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_visita_campo")),
        sa.UniqueConstraint("visita_id", name=op.f("uq_visita_campo_visita_id")),
        sa.UniqueConstraint("gestion_cobranza_id", name="uq_visita_gestion"),
    )

    op.create_table(
        "promesa_pago",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("evento_lifecycle_id", sa.BigInteger(), nullable=False),
        sa.Column("gestion_cobranza_id", sa.BigInteger(), nullable=False),
        sa.Column("fecha_limite", sa.Date(), nullable=False),
        sa.Column("promesa_id", sa.Uuid(), nullable=False),
        sa.Column("cuenta_canonica_id", sa.Integer(), nullable=False),
        _importe("monto_prometido"),
        _texto("version_modelo", 32, nullable=False),
        sa.CheckConstraint("monto_prometido > 0", name=op.f("ck_promesa_monto")),
        sa.ForeignKeyConstraint(
            ["evento_lifecycle_id"], ["evento_lifecycle.id"], name="fk_promesa_evento"
        ),
        sa.ForeignKeyConstraint(
            ["gestion_cobranza_id"], ["gestion_cobranza.id"], name="fk_promesa_gestion"
        ),
        sa.ForeignKeyConstraint(
            ["cuenta_canonica_id"], ["cuenta_canonica.id"], name="fk_promesa_cuenta"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_promesa_pago")),
        sa.UniqueConstraint("promesa_id", name=op.f("uq_promesa_pago_promesa_id")),
        sa.UniqueConstraint("evento_lifecycle_id", name="uq_promesa_evento"),
        sa.UniqueConstraint("gestion_cobranza_id", name="uq_promesa_gestion"),
    )
    op.create_index(
        "ix_promesa_cuenta_limite", "promesa_pago", ["cuenta_canonica_id", "fecha_limite"]
    )

    op.create_table(
        "convenio_cobranza",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("evento_lifecycle_id", sa.BigInteger(), nullable=False),
        sa.Column("gestion_cobranza_id", sa.BigInteger(), nullable=False),
        sa.Column("fecha_inicio", sa.Date(), nullable=False),
        sa.Column("fecha_fin", sa.Date(), nullable=True),
        sa.Column("convenio_id", sa.Uuid(), nullable=False),
        sa.Column("cuenta_canonica_id", sa.Integer(), nullable=False),
        sa.Column("cuotas", sa.Integer(), nullable=False),
        _importe("monto_total_acordado"),
        _texto("version_modelo", 32, nullable=False),
        sa.CheckConstraint("monto_total_acordado > 0", name=op.f("ck_convenio_monto")),
        sa.CheckConstraint(
            "fecha_fin IS NULL OR fecha_fin >= fecha_inicio", name=op.f("ck_convenio_vigencia")
        ),
        sa.CheckConstraint("cuotas >= 0", name=op.f("ck_convenio_cuotas")),
        sa.ForeignKeyConstraint(
            ["evento_lifecycle_id"], ["evento_lifecycle.id"], name="fk_convenio_evento"
        ),
        sa.ForeignKeyConstraint(
            ["gestion_cobranza_id"], ["gestion_cobranza.id"], name="fk_convenio_gestion"
        ),
        sa.ForeignKeyConstraint(
            ["cuenta_canonica_id"], ["cuenta_canonica.id"], name="fk_convenio_cuenta"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_convenio_cobranza")),
        sa.UniqueConstraint("convenio_id", name=op.f("uq_convenio_cobranza_convenio_id")),
        sa.UniqueConstraint("evento_lifecycle_id", name="uq_convenio_evento"),
        sa.UniqueConstraint("gestion_cobranza_id", name="uq_convenio_gestion"),
    )
    op.create_index(
        "ix_convenio_cuenta_inicio", "convenio_cobranza", ["cuenta_canonica_id", "fecha_inicio"]
    )

    op.create_table(
        "cuota_convenio",
        sa.Column("convenio_cobranza_id", sa.BigInteger(), nullable=False),
        sa.Column("numero", sa.Integer(), nullable=False),
        sa.Column("fecha_vencimiento", sa.Date(), nullable=False),
        _importe("monto"),
        sa.CheckConstraint("monto > 0", name=op.f("ck_cuota_monto")),
        sa.CheckConstraint("numero >= 1", name=op.f("ck_cuota_numero")),
        sa.ForeignKeyConstraint(
            ["convenio_cobranza_id"], ["convenio_cobranza.id"], name="fk_cuota_convenio"
        ),
        sa.PrimaryKeyConstraint("convenio_cobranza_id", "numero", name=op.f("pk_cuota_convenio")),
    )

    # Solo se agrega: un UPDATE o un DELETE falla, en cualquier tabla del lifecycle.
    op.execute(sa.text(SOLO_AGREGA))
    for tabla in TABLAS_DEL_LIFECYCLE:
        op.execute(
            sa.text(
                f"CREATE TRIGGER tr_{tabla}_solo_agrega BEFORE UPDATE OR DELETE ON {tabla} "
                "FOR EACH STATEMENT EXECUTE FUNCTION lifecycle_solo_agrega()"
            )
        )
    # Y cada convenio, al confirmar, con exactamente sus cuotas declaradas y coherentes.
    op.execute(sa.text(CUOTAS_COHERENTES))
    for tabla, nombre in (
        ("convenio_cobranza", "tr_convenio_cuotas"),
        ("cuota_convenio", "tr_cuota_convenio"),
    ):
        op.execute(
            sa.text(
                f"CREATE CONSTRAINT TRIGGER {nombre} AFTER INSERT ON {tabla} "
                "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
                "EXECUTE FUNCTION convenio_cuotas_coherentes()"
            )
        )


def _atribucion() -> None:
    op.create_table(
        "ejecucion_atribucion",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("atribucion_run_id", sa.Uuid(), nullable=False),
        _texto("version_atribucion", 32, nullable=False),
        _texto("despacho_id", 32, nullable=False),
        _texto("cartera_id", 32, nullable=False),
        sa.Column("periodo_desde", sa.Date(), nullable=False),
        sa.Column("periodo_hasta", sa.Date(), nullable=False),
        sa.Column("ventana_dias", sa.Integer(), nullable=False),
        _texto("zona_horaria", 64, nullable=False),
        sa.Column("estado", _estado("estado_atribucion"), nullable=False),
        _texto("resultado", 40),
        _texto("firma_entrada", 64),
        sa.Column("ejecucion_motor_pagos_id", sa.Integer(), nullable=True),
        _instante("iniciada_en"),
        _instante("terminada_en", nullable=True),
        *(sa.Column(metrica, sa.BigInteger(), nullable=False) for metrica in METRICAS_ATRIBUCION),
        *(_importe(monto, 24) for monto in MONTOS_ATRIBUCION),
        _texto("detalle"),
        sa.CheckConstraint("periodo_desde < periodo_hasta", name=op.f("ck_atribucion_periodo")),
        sa.CheckConstraint(
            "version_atribucion <> 'atribucion/v1' OR (extract(day FROM periodo_desde) = 1 "
            "AND periodo_hasta = (periodo_desde + interval '1 month')::date)",
            name=op.f("ck_atribucion_mes"),
        ),
        sa.CheckConstraint(
            "ventana_dias BETWEEN 1 AND 366", name=op.f("ck_atribucion_ventana_dias")
        ),
        sa.CheckConstraint(
            "(estado = 'EN_PROCESO') = (resultado IS NULL)", name=op.f("ck_atribucion_resultado")
        ),
        sa.CheckConstraint(
            "(firma_entrada IS NULL OR firma_entrada ~ '^[0-9a-f]{64}$') "
            "AND (estado <> 'EXITOSA' OR firma_entrada IS NOT NULL)",
            name=op.f("ck_atribucion_firma"),
        ),
        sa.CheckConstraint(CONTEOS_ATRIBUCION, name=op.f("ck_atribucion_conteos")),
        sa.CheckConstraint(
            "monto_asociado >= 0 AND monto_ambiguo >= 0 AND monto_sin_candidato >= 0 "
            "AND monto_anulado >= 0",
            name=op.f("ck_atribucion_montos"),
        ),
        sa.CheckConstraint(
            "(estado = 'EXITOSA' AND ejecucion_motor_pagos_id IS NOT NULL) "
            "OR (estado <> 'EXITOSA' AND movimientos_evaluados = 0 AND candidatos = 0)",
            name=op.f("ck_atribucion_publicacion"),
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_motor_pagos_id"],
            ["ejecucion_motor_pagos.id"],
            name="fk_atribucion_motor_pagos",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ejecucion_atribucion")),
        sa.UniqueConstraint(
            "atribucion_run_id", name=op.f("uq_ejecucion_atribucion_atribucion_run_id")
        ),
    )
    op.create_index(
        "ux_ejecucion_atribucion_exitosa",
        "ejecucion_atribucion",
        ["despacho_id", "cartera_id", "version_atribucion", "periodo_desde", "firma_entrada"],
        unique=True,
        postgresql_where=sa.text("estado = 'EXITOSA'"),
    )
    op.create_index(
        "ux_ejecucion_atribucion_en_proceso",
        "ejecucion_atribucion",
        ["despacho_id", "cartera_id", "version_atribucion", "periodo_desde"],
        unique=True,
        postgresql_where=sa.text("estado = 'EN_PROCESO'"),
    )

    # Lo que una ejecucion concluyo de cada movimiento, sin id propio. Las de 8 bytes primero.
    op.create_table(
        "atribucion_movimiento",
        sa.Column("ejecucion_atribucion_id", sa.Integer(), nullable=False),
        sa.Column("movimiento_economico_canonico_id", sa.BigInteger(), nullable=False),
        sa.Column("fecha_recepcion", sa.DateTime(timezone=False), nullable=False),
        sa.Column("gestion_cobranza_id", sa.BigInteger(), nullable=True),
        sa.Column("movimiento_id", sa.Uuid(), nullable=False),
        sa.Column("cuenta_canonica_id", sa.Integer(), nullable=True),
        sa.Column("candidatos", sa.Integer(), nullable=False),
        _importe("monto"),
        sa.Column("anulado_por_reverso", sa.Boolean(), nullable=False),
        _texto("clasificacion", 24, nullable=False),
        sa.Column("motivos", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "clasificacion NOT IN ('SIN_GESTION_CANDIDATA', 'ASOCIACION_UNICA', 'AMBIGUA') OR ("
            "(clasificacion = 'ASOCIACION_UNICA') = (gestion_cobranza_id IS NOT NULL) "
            "AND ((clasificacion = 'SIN_GESTION_CANDIDATA' AND candidatos = 0) "
            "OR (clasificacion = 'ASOCIACION_UNICA' AND candidatos = 1) "
            "OR (clasificacion = 'AMBIGUA' AND candidatos >= 2)))",
            name=op.f("ck_atribucion_movimiento_clase"),
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_atribucion_id"],
            ["ejecucion_atribucion.id"],
            name="fk_atribucion_movimiento_ejecucion",
        ),
        sa.ForeignKeyConstraint(
            ["movimiento_economico_canonico_id"],
            ["movimiento_economico_canonico.id"],
            name="fk_atribucion_movimiento_movimiento",
        ),
        sa.ForeignKeyConstraint(
            ["gestion_cobranza_id"],
            ["gestion_cobranza.id"],
            name="fk_atribucion_movimiento_gestion",
        ),
        sa.ForeignKeyConstraint(
            ["cuenta_canonica_id"], ["cuenta_canonica.id"], name="fk_atribucion_movimiento_cuenta"
        ),
        sa.PrimaryKeyConstraint(
            "ejecucion_atribucion_id",
            "movimiento_economico_canonico_id",
            name=op.f("pk_atribucion_movimiento"),
        ),
    )
    op.create_index(
        "ix_atribucion_movimiento_movimiento_id", "atribucion_movimiento", ["movimiento_id"]
    )

    op.create_table(
        "candidato_atribucion",
        sa.Column("ejecucion_atribucion_id", sa.Integer(), nullable=False),
        sa.Column("movimiento_economico_canonico_id", sa.BigInteger(), nullable=False),
        sa.Column("gestion_cobranza_id", sa.BigInteger(), nullable=False),
        sa.Column("antelacion_segundos", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("antelacion_segundos >= 0", name=op.f("ck_candidato_antelacion")),
        sa.ForeignKeyConstraint(
            ["ejecucion_atribucion_id", "movimiento_economico_canonico_id"],
            [
                "atribucion_movimiento.ejecucion_atribucion_id",
                "atribucion_movimiento.movimiento_economico_canonico_id",
            ],
            name="fk_candidato_atribucion",
        ),
        sa.ForeignKeyConstraint(
            ["gestion_cobranza_id"], ["gestion_cobranza.id"], name="fk_candidato_gestion"
        ),
        sa.PrimaryKeyConstraint(
            "ejecucion_atribucion_id",
            "movimiento_economico_canonico_id",
            "gestion_cobranza_id",
            name=op.f("pk_candidato_atribucion"),
        ),
    )


def _evaluacion() -> None:
    op.create_table(
        "ejecucion_evaluacion_promesas",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("evaluacion_run_id", sa.Uuid(), nullable=False),
        _texto("version_evaluacion", 32, nullable=False),
        _texto("despacho_id", 32, nullable=False),
        _texto("cartera_id", 32, nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        _texto("zona_horaria", 64, nullable=False),
        sa.Column("estado", _estado("estado_evaluacion_promesas"), nullable=False),
        _texto("resultado", 40),
        _texto("firma_entrada", 64),
        sa.Column("horizonte_pagos", sa.DateTime(timezone=False), nullable=True),
        _instante("iniciada_en"),
        _instante("terminada_en", nullable=True),
        *(sa.Column(metrica, sa.BigInteger(), nullable=False) for metrica in METRICAS_EVALUACION),
        _importe("monto_prometido", 24),
        _importe("monto_observado", 24),
        _texto("detalle"),
        sa.CheckConstraint(
            "(estado = 'EN_PROCESO') = (resultado IS NULL)", name=op.f("ck_evaluacion_resultado")
        ),
        sa.CheckConstraint(
            "(firma_entrada IS NULL OR firma_entrada ~ '^[0-9a-f]{64}$') "
            "AND (estado <> 'EXITOSA' OR firma_entrada IS NOT NULL)",
            name=op.f("ck_evaluacion_firma"),
        ),
        sa.CheckConstraint(CONTEOS_EVALUACION, name=op.f("ck_evaluacion_conteos")),
        sa.CheckConstraint(CLASES_EVALUACION, name=op.f("ck_evaluacion_clases")),
        sa.CheckConstraint(
            "estado = 'EXITOSA' OR promesas_evaluadas = 0", name=op.f("ck_evaluacion_publicacion")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ejecucion_evaluacion_promesas")),
        sa.UniqueConstraint(
            "evaluacion_run_id", name=op.f("uq_ejecucion_evaluacion_promesas_evaluacion_run_id")
        ),
    )
    op.create_index(
        "ux_evaluacion_promesas_exitosa",
        "ejecucion_evaluacion_promesas",
        ["despacho_id", "cartera_id", "version_evaluacion", "as_of", "firma_entrada"],
        unique=True,
        postgresql_where=sa.text("estado = 'EXITOSA'"),
    )
    op.create_index(
        "ux_evaluacion_promesas_en_proceso",
        "ejecucion_evaluacion_promesas",
        ["despacho_id", "cartera_id", "version_evaluacion", "as_of"],
        unique=True,
        postgresql_where=sa.text("estado = 'EN_PROCESO'"),
    )

    op.create_table(
        "evaluacion_promesa",
        sa.Column("ejecucion_evaluacion_promesas_id", sa.Integer(), nullable=False),
        sa.Column("promesa_pago_id", sa.BigInteger(), nullable=False),
        sa.Column("primer_movimiento_en", sa.DateTime(timezone=False), nullable=True),
        sa.Column("ultimo_movimiento_en", sa.DateTime(timezone=False), nullable=True),
        sa.Column("movimientos_compatibles", sa.Integer(), nullable=False),
        _importe("monto_observado"),
        _texto("estado", 16, nullable=False),
        sa.Column("motivos", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "movimientos_compatibles >= 0 AND monto_observado >= 0",
            name=op.f("ck_evaluacion_promesa_conteos"),
        ),
        sa.ForeignKeyConstraint(
            ["ejecucion_evaluacion_promesas_id"],
            ["ejecucion_evaluacion_promesas.id"],
            name="fk_evaluacion_promesa_ejecucion",
        ),
        sa.ForeignKeyConstraint(
            ["promesa_pago_id"], ["promesa_pago.id"], name="fk_evaluacion_promesa_promesa"
        ),
        sa.PrimaryKeyConstraint(
            "ejecucion_evaluacion_promesas_id",
            "promesa_pago_id",
            name=op.f("pk_evaluacion_promesa"),
        ),
    )
    op.create_index("ix_evaluacion_promesa_promesa", "evaluacion_promesa", ["promesa_pago_id"])


def _cola() -> None:
    # Los trabajos ATRIBUCION y EVALUACION_PROMESAS, en la misma cola durable: uno por ejecucion.
    for columna, tabla, llave, unica in (
        (
            "ejecucion_atribucion_id",
            "ejecucion_atribucion",
            "fk_trabajo_atribucion",
            "uq_trabajo_atribucion",
        ),
        (
            "ejecucion_evaluacion_promesas_id",
            "ejecucion_evaluacion_promesas",
            "fk_trabajo_evaluacion",
            "uq_trabajo_evaluacion",
        ),
    ):
        op.add_column("trabajo_orquestacion", sa.Column(columna, sa.Integer(), nullable=True))
        op.create_foreign_key(llave, "trabajo_orquestacion", tabla, [columna], ["id"])
        op.create_unique_constraint(unica, "trabajo_orquestacion", [columna])
    op.alter_column(
        "trabajo_orquestacion",
        "tipo",
        type_=sa.String(length=24),
        existing_type=sa.String(length=16),
        existing_nullable=False,
    )
    _rehacer_check("ck_trabajo_orquestacion_tipo_trabajo", _tipos(TIPOS_0010))
    _rehacer_check("ck_trabajo_objetivo", OBJETIVO_0010)


def downgrade() -> None:
    # Primero se cierra lo que estaba en curso, en este orden: las ejecuciones (si un worker tiene
    # una bloqueada, termina antes de que el UPDATE la tome) y despues sus trabajos. Despues se van
    # esos trabajos, que la 0009 no sabria guardar, y las estructuras de la 0010.
    op.execute(CERRAR_ATRIBUCIONES)
    op.execute(CERRAR_EVALUACIONES)
    op.execute(CERRAR_TRABAJOS)
    op.execute(
        sa.text(
            "DELETE FROM trabajo_orquestacion WHERE tipo IN ('ATRIBUCION', 'EVALUACION_PROMESAS')"
        )
    )
    _rehacer_check("ck_trabajo_objetivo", OBJETIVO_0009)
    _rehacer_check("ck_trabajo_orquestacion_tipo_trabajo", _tipos(TIPOS_0009))
    op.alter_column(
        "trabajo_orquestacion",
        "tipo",
        type_=sa.String(length=16),
        existing_type=sa.String(length=24),
        existing_nullable=False,
    )
    for columna, llave, unica in (
        ("ejecucion_evaluacion_promesas_id", "fk_trabajo_evaluacion", "uq_trabajo_evaluacion"),
        ("ejecucion_atribucion_id", "fk_trabajo_atribucion", "uq_trabajo_atribucion"),
    ):
        op.drop_constraint(unica, "trabajo_orquestacion", type_="unique")
        op.drop_constraint(llave, "trabajo_orquestacion", type_="foreignkey")
        op.drop_column("trabajo_orquestacion", columna)

    op.drop_index("ix_evaluacion_promesa_promesa", table_name="evaluacion_promesa")
    op.drop_table("evaluacion_promesa")
    for indice, estado in (
        ("ux_evaluacion_promesas_en_proceso", "EN_PROCESO"),
        ("ux_evaluacion_promesas_exitosa", "EXITOSA"),
    ):
        op.drop_index(
            indice,
            table_name="ejecucion_evaluacion_promesas",
            postgresql_where=sa.text(f"estado = '{estado}'"),
        )
    op.drop_table("ejecucion_evaluacion_promesas")

    op.drop_table("candidato_atribucion")
    op.drop_index("ix_atribucion_movimiento_movimiento_id", table_name="atribucion_movimiento")
    op.drop_table("atribucion_movimiento")
    for indice, estado in (
        ("ux_ejecucion_atribucion_en_proceso", "EN_PROCESO"),
        ("ux_ejecucion_atribucion_exitosa", "EXITOSA"),
    ):
        op.drop_index(
            indice,
            table_name="ejecucion_atribucion",
            postgresql_where=sa.text(f"estado = '{estado}'"),
        )
    op.drop_table("ejecucion_atribucion")

    # Las tablas del lifecycle, con sus triggers, y despues sus funciones.
    op.drop_table("cuota_convenio")
    op.drop_index("ix_convenio_cuenta_inicio", table_name="convenio_cobranza")
    op.drop_table("convenio_cobranza")
    op.drop_index("ix_promesa_cuenta_limite", table_name="promesa_pago")
    op.drop_table("promesa_pago")
    op.drop_table("visita_campo")
    op.drop_index("ix_gestion_cuenta_ocurrido", table_name="gestion_cobranza")
    op.drop_table("gestion_cobranza")
    op.drop_index("ix_evento_cuenta_ocurrido", table_name="evento_lifecycle")
    op.drop_index(
        "ux_evento_relacionado",
        table_name="evento_lifecycle",
        postgresql_where=sa.text("evento_relacionado_id IS NOT NULL"),
    )
    op.drop_table("evento_lifecycle")
    op.execute(sa.text("DROP FUNCTION convenio_cuotas_coherentes()"))
    op.execute(sa.text("DROP FUNCTION lifecycle_solo_agrega()"))
