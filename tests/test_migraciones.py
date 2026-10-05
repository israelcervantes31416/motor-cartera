"""Las migraciones sobre una base que ya tiene corridas, contra PostgreSQL.

Las demas pruebas suben el esquema una vez sobre una base vacia, y el CI tambien migra sobre
una vacia. Aqui se prueba lo que ninguno de los dos ve: que una migracion no rompe ni pierde
las corridas que ya existen. Corre en una base propia, para no mover el esquema de la base de
pruebas a media sesion.

Las pruebas que migran de verdad usan PostgreSQL. Algunas comprobaciones solo leen lo que declaran
los modelos, sin migrar nada, y corren sin servidor.
"""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config as ConfigAlembic
from alembic.script import ScriptDirectory
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Engine,
    Integer,
    LargeBinary,
    Numeric,
    UniqueConstraint,
    create_engine,
    make_url,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from motor_cartera.config import config
from motor_cartera.db.modelos import (
    ArchivoCorrida,
    ArtefactoFuente,
    Corrida,
    EjecucionRuteo,
    EjecucionTerritorial,
    FlujoOrquestacion,
    ParadaRuta,
    ResultadoTerritorial,
    RutaTerritorial,
    TrabajoOrquestacion,
)

RAIZ = Path(__file__).resolve().parents[1]

# Una corrida como la dejaba la 0001: sin version del contrato ni firma del contenido.
INSERTAR_CORRIDA = text(
    "INSERT INTO corrida (run_id, iniciada_en, origen, firma, estado, tolerancia_rechazo, "
    "filas_leidas, filas_validas, filas_rechazadas) "
    "VALUES (:run_id, now(), :origen, :firma, :estado, 0.05, 0, 0, 0)"
)


def _alembic() -> ConfigAlembic:
    cfg = ConfigAlembic(str(RAIZ / "alembic.ini"))
    cfg.set_main_option("script_location", str(RAIZ / "migraciones"))
    return cfg


@pytest.fixture
def base_en_0001(monkeypatch) -> Iterator[Engine]:
    """Una base recien creada, aparte de la de pruebas, con el esquema de la 0001."""
    pruebas = make_url(config.database_url)  # conftest ya exige que se llame *_test
    url = pruebas.set(database=pruebas.database.removesuffix("_test") + "_migraciones_test")
    servidor = create_engine(
        url.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    with servidor.connect() as conexion:
        conexion.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
        conexion.execute(text(f'CREATE DATABASE "{url.database}"'))
    # migraciones/env.py toma la URL de la configuracion de la app: se apunta a esta base.
    monkeypatch.setattr(config, "database_url", url.render_as_string(hide_password=False))
    motor = create_engine(url, poolclass=NullPool)
    try:
        command.upgrade(_alembic(), "0001")
        yield motor
    finally:
        motor.dispose()
        with servidor.connect() as conexion:
            conexion.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
        servidor.dispose()


def _version_contrato(motor: Engine) -> tuple[str, str | None] | None:
    """Si la columna existe: si admite nulos y su valor por omision."""
    with motor.connect() as conexion:
        return conexion.execute(
            text(
                "SELECT is_nullable, column_default FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'corrida' "
                "AND column_name = 'version_contrato'"
            )
        ).one_or_none()


def test_la_0002_conserva_las_corridas_que_ya_existian_y_baja_sin_perderlas(base_en_0001):
    with base_en_0001.begin() as conexion:
        conexion.execute(
            INSERTAR_CORRIDA,
            [
                {
                    "run_id": uuid4(),
                    "origen": "historica.csv",
                    "firma": "a" * 64,
                    "estado": "EXITOSA",
                },
                {
                    "run_id": uuid4(),
                    "origen": "fallida.xlsx",
                    "firma": "b" * 64,
                    "estado": "FALLIDA",
                },
            ],
        )

    command.upgrade(_alembic(), "0002")

    with base_en_0001.connect() as conexion:
        filas = conexion.execute(
            text("SELECT origen, version_contrato, firma_contenido FROM corrida ORDER BY id")
        ).all()
    # Las de antes siguen ahi, marcadas: no se les supone una version.
    assert [tuple(fila) for fila in filas] == [
        ("historica.csv", "sin-registro", None),
        ("fallida.xlsx", "sin-registro", None),
    ]
    # Y la columna quedo NOT NULL y sin valor por omision: una corrida nueva trae el suyo, o
    # no entra.
    assert tuple(_version_contrato(base_en_0001)) == ("NO", None)
    with pytest.raises(IntegrityError, match="version_contrato"), base_en_0001.begin() as c:
        c.execute(
            INSERTAR_CORRIDA,
            {"run_id": uuid4(), "origen": "nueva.csv", "firma": "c" * 64, "estado": "EN_PROCESO"},
        )

    command.downgrade(_alembic(), "0001")

    # Bajar quita las columnas, no las corridas.
    assert _version_contrato(base_en_0001) is None
    with base_en_0001.connect() as conexion:
        origenes = conexion.execute(text("SELECT origen FROM corrida ORDER BY id")).scalars()
        assert list(origenes) == ["historica.csv", "fallida.xlsx"]


# --- la 0003: las ejecuciones del motor de decision y sus decisiones ---------------------------

# Una corrida como la deja la 0002: con la version del contrato y la firma de su contenido.
INSERTAR_CORRIDA_0002 = text(
    "INSERT INTO corrida (run_id, iniciada_en, origen, firma, firma_contenido, estado, "
    "tolerancia_rechazo, version_contrato, fecha_corte, filas_leidas, filas_validas, "
    "filas_rechazadas) VALUES (:run_id, now(), 'publicada.csv', :firma, :firma_contenido, "
    "'EXITOSA', 0.05, 'cartera/v1', '2026-09-30', 2, 1, 1) RETURNING id"
)
INSERTAR_CUENTA = text(
    "INSERT INTO cuenta (corrida_id, cliente_unico, saldo_total, dias_atraso, producto, canal, "
    "cve_entidad, cve_municipio, fecha_corte) VALUES (:corrida_id, 'CU00000001', 62000.00, 65, "
    "'CONSUMO', 'DIGITAL', '21', '114', '2026-09-30') RETURNING id"
)
INSERTAR_RECHAZO = text(
    "INSERT INTO rechazo (corrida_id, fila, valores, motivos) "
    "VALUES (:corrida_id, 3, CAST(:valores AS JSONB), CAST(:motivos AS JSONB))"
)
INSERTAR_EJECUCION = text(
    "INSERT INTO ejecucion_decision (decision_run_id, corrida_id, version_reglas, estado, "
    "iniciada_en, cuentas_evaluadas, cuentas_decididas) "
    "VALUES (:decision_run_id, :corrida_id, :version_reglas, :estado, now(), 0, 0) RETURNING id"
)
INSERTAR_DECISION = text(
    "INSERT INTO decision_cuenta (ejecucion_decision_id, cuenta_id, segmento, prioridad, "
    "canal_recomendado, motivos) VALUES (:ejecucion_id, :cuenta_id, 'MORA_MEDIA', 'MUY_ALTA', "
    "'CAMPO', CAST(:motivos AS JSONB)) RETURNING id"
)
TERMINAR_EXITOSA = text(
    "UPDATE ejecucion_decision SET estado = 'EXITOSA', terminada_en = now() WHERE id = :id"
)

# Los motivos que decision/v1 da a una cuenta con 65 dias de atraso y 62,000.00 de saldo.
MOTIVOS = [
    {"codigo": "MORA_31_90", "campo": "dias_atraso", "valor": "65"},
    {"codigo": "SALDO_ALTO", "campo": "saldo_total", "valor": "62000.00"},
    {"codigo": "PRIORIDAD_MUY_ALTA", "campo": "prioridad", "valor": "MUY_ALTA"},
    {"codigo": "CANAL_CAMPO", "campo": "canal_recomendado", "valor": "CAMPO"},
]

TABLAS = "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()"
TABLAS_DE_DECISION = {"ejecucion_decision", "decision_cuenta"}

# Llaves, unicas, foraneas y CHECK de las tablas nuevas (sin los NOT NULL, que desde PostgreSQL 18
# tambien son filas de pg_constraint).
RESTRICCIONES = (
    "SELECT c.conname FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
    "WHERE t.relname IN ('ejecucion_decision', 'decision_cuenta') "
    "AND c.contype IN ('p', 'u', 'f', 'c')"
)
# Salen de la convencion de nombres de los modelos, y una migracion futura se refiere a ellos.
RESTRICCIONES_DE_DECISION = {
    "pk_ejecucion_decision",
    "uq_ejecucion_decision_decision_run_id",
    "fk_ejecucion_decision_corrida_id_corrida",
    "ck_ejecucion_decision_estado_decision",
    "pk_decision_cuenta",
    "fk_decision_cuenta_ejecucion_decision_id_ejecucion_decision",
    "fk_decision_cuenta_cuenta_id_cuenta",
    "uq_decision_cuenta_ejecucion_decision_id_cuenta_id",
}

# Cada indice de las tablas nuevas, incluidos los que crean las llaves y las restricciones unicas:
# su tabla, si es unico, sus columnas en orden y su predicado parcial (NULL si no es parcial).
INDICES = (
    "SELECT t.relname, i.relname, x.indisunique, "
    "ARRAY(SELECT CAST(a.attname AS text) "
    "FROM unnest(CAST(x.indkey AS int2[])) WITH ORDINALITY AS k(numero, orden) "
    "JOIN pg_attribute a ON a.attrelid = x.indrelid AND a.attnum = k.numero "
    "ORDER BY k.orden), "
    "pg_get_expr(x.indpred, x.indrelid) "
    "FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid "
    "JOIN pg_class t ON t.oid = x.indrelid "
    "WHERE t.relname IN ('ejecucion_decision', 'decision_cuenta')"
)

# Lo que guarda la fase 1, completo, para compararlo antes y despues de cada migracion.
FASE_1 = {
    "corrida": "SELECT run_id, origen, firma, estado, version_contrato, firma_contenido, "
    "fecha_corte, filas_leidas, filas_validas, filas_rechazadas FROM corrida ORDER BY id",
    "cuenta": "SELECT corrida_id, cliente_unico, saldo_total, dias_atraso, producto, canal, "
    "cve_entidad, cve_municipio, fecha_corte FROM cuenta ORDER BY id",
    "rechazo": "SELECT corrida_id, fila, valores, motivos FROM rechazo ORDER BY id",
}


@dataclass(frozen=True)
class BaseEn0003:
    """Una base con datos de la fase 1, subida hasta la 0003."""

    motor: Engine
    historica_id: int
    """Una corrida de la 0001, sin cuentas ni rechazos: la 0002 la marco 'sin-registro'."""
    corrida_id: int
    """Una corrida de la 0002, con una cuenta y un rechazo."""
    cuenta_id: int
    fase_1: dict[str, list[tuple]]
    """Las filas de la fase 1 justo antes de subir a la 0003."""


@pytest.fixture
def base_en_0003(base_en_0001) -> BaseEn0003:
    """Corridas de la 0001 y de la 0002, con sus cuentas y rechazos, y la base ya en la 0003."""
    with base_en_0001.begin() as conexion:
        conexion.execute(
            INSERTAR_CORRIDA,
            {"run_id": uuid4(), "origen": "historica.csv", "firma": "a" * 64, "estado": "EXITOSA"},
        )
        historica_id = conexion.execute(
            text("SELECT id FROM corrida WHERE origen = 'historica.csv'")
        ).scalar_one()

    command.upgrade(_alembic(), "0002")

    with base_en_0001.begin() as conexion:
        corrida_id = conexion.execute(
            INSERTAR_CORRIDA_0002,
            {"run_id": uuid4(), "firma": "b" * 64, "firma_contenido": "c" * 64},
        ).scalar_one()
        cuenta_id = conexion.execute(INSERTAR_CUENTA, {"corrida_id": corrida_id}).scalar_one()
        conexion.execute(
            INSERTAR_RECHAZO,
            {
                "corrida_id": corrida_id,
                "valores": json.dumps({"cliente_unico": "CU00000002", "saldo_total": "-5"}),
                "motivos": json.dumps(
                    [{"campo": "saldo_total", "regla": "greater_than_or_equal_to(0)"}]
                ),
            },
        )
    fase_1 = _fase_1(base_en_0001)

    command.upgrade(_alembic(), "0003")
    return BaseEn0003(base_en_0001, historica_id, corrida_id, cuenta_id, fase_1)


def _fase_1(motor: Engine) -> dict[str, list[tuple]]:
    with motor.connect() as conexion:
        return {
            tabla: [tuple(fila) for fila in conexion.execute(text(consulta))]
            for tabla, consulta in FASE_1.items()
        }


def _consultar(motor: Engine, consulta: str) -> list:
    """La primera columna de cada fila de `consulta`."""
    with motor.connect() as conexion:
        return list(conexion.execute(text(consulta)).scalars())


def _ejecucion(
    corrida_id: int,
    estado: str = "EN_PROCESO",
    version: str = "decision/v1",
    decision_run_id: UUID | None = None,
) -> dict:
    return {
        "decision_run_id": decision_run_id or uuid4(),
        "corrida_id": corrida_id,
        "version_reglas": version,
        "estado": estado,
    }


def _decision(ejecucion_id: int, cuenta_id: int) -> dict:
    return {"ejecucion_id": ejecucion_id, "cuenta_id": cuenta_id, "motivos": json.dumps(MOTIVOS)}


def _insertar(motor: Engine, sentencia, *filas: dict) -> list[int]:
    """Inserta las filas en una sola transaccion y devuelve sus ids."""
    with motor.begin() as conexion:
        return [conexion.execute(sentencia, fila).scalar_one() for fila in filas]


def _rechaza(motor: Engine, restriccion: str, sentencia, parametros: dict) -> None:
    """La base rechaza `sentencia` por `restriccion`, en una transaccion propia que se revierte."""
    with pytest.raises(IntegrityError, match=restriccion), motor.begin() as conexion:
        conexion.execute(sentencia, parametros)


def test_la_0003_agrega_las_tablas_de_decision_sin_tocar_la_fase_1(base_en_0003):
    motor, corrida = base_en_0003.motor, base_en_0003.corrida_id

    # A. Lo de la fase 1 sigue igual, fila por fila: la corrida de la 0001 con su 'sin-registro', y
    # la de la 0002 con su version del contrato y la firma de su contenido.
    assert _fase_1(motor) == base_en_0003.fase_1
    assert [(fila[4], fila[5]) for fila in base_en_0003.fase_1["corrida"]] == [
        ("sin-registro", None),
        ("cartera/v1", "c" * 64),
    ]
    assert [len(base_en_0003.fase_1[tabla]) for tabla in ("cuenta", "rechazo")] == [1, 1]

    # B. Las tablas nuevas existen, vacias, con sus restricciones y su indice, por nombre.
    assert TABLAS_DE_DECISION <= set(_consultar(motor, TABLAS))
    assert _consultar(motor, "SELECT count(*) FROM ejecucion_decision") == [0]
    assert _consultar(motor, "SELECT count(*) FROM decision_cuenta") == [0]
    assert set(_consultar(motor, RESTRICCIONES)) == RESTRICCIONES_DE_DECISION
    (indice,) = _consultar(
        motor, "SELECT indexdef FROM pg_indexes WHERE indexname = 'ux_ejecucion_decision_exitosa'"
    )
    assert indice.startswith("CREATE UNIQUE INDEX") and "(corrida_id, version_reglas)" in indice
    assert "WHERE" in indice and "'EXITOSA'" in indice
    # El estado es VARCHAR con CHECK y no un tipo de PostgreSQL: no hay tipo que migrar ni borrar.
    assert _consultar(motor, "SELECT count(*) FROM pg_type WHERE typname = 'estado_decision'") == [
        0
    ]

    # RECHAZADA no existe en la capa de decision: es el veredicto de la corrida, no del motor.
    _rechaza(
        motor,
        "ck_ejecucion_decision_estado_decision",
        INSERTAR_EJECUCION,
        _ejecucion(corrida, "RECHAZADA"),
    )
    # La version de las reglas no tiene valor por omision: cada ejecucion trae la suya.
    _rechaza(
        motor,
        "version_reglas",
        text(
            "INSERT INTO ejecucion_decision (decision_run_id, corrida_id, estado, iniciada_en, "
            "cuentas_evaluadas, cuentas_decididas) "
            "VALUES (:decision_run_id, :corrida_id, 'EN_PROCESO', now(), 0, 0)"
        ),
        {"decision_run_id": uuid4(), "corrida_id": corrida},
    )


def test_la_0003_indexa_las_ejecuciones_de_cada_corrida(base_en_0003):
    with base_en_0003.motor.connect() as conexion:
        filas = conexion.execute(text(INDICES)).all()
    indices = {
        indice: (tabla, unico, columnas, parcial)
        for tabla, indice, unico, columnas, parcial in filas
    }

    # El de la idempotencia sigue aparte: unico, y solo sobre las ejecuciones EXITOSA.
    exitosas = indices["ux_ejecucion_decision_exitosa"][3]
    assert "estado" in exitosas and "'EXITOSA'" in exitosas
    # Todas las ejecuciones de una corrida, en cualquier estado, tienen su propio indice: normal y
    # sin predicado. Y no hay mas indices que esos y los de las llaves y restricciones unicas: el
    # unico de decision_cuenta empieza por ejecucion_decision_id, asi que ya sirve para encontrar
    # las decisiones de una ejecucion.
    assert indices == {
        "pk_ejecucion_decision": ("ejecucion_decision", True, ["id"], None),
        "uq_ejecucion_decision_decision_run_id": (
            "ejecucion_decision",
            True,
            ["decision_run_id"],
            None,
        ),
        "ix_ejecucion_decision_corrida_id": ("ejecucion_decision", False, ["corrida_id"], None),
        "ux_ejecucion_decision_exitosa": (
            "ejecucion_decision",
            True,
            ["corrida_id", "version_reglas"],
            exitosas,
        ),
        "pk_decision_cuenta": ("decision_cuenta", True, ["id"], None),
        "uq_decision_cuenta_ejecucion_decision_id_cuenta_id": (
            "decision_cuenta",
            True,
            ["ejecucion_decision_id", "cuenta_id"],
            None,
        ),
    }


def test_una_corrida_se_decide_con_exito_una_sola_vez_por_version(base_en_0003):
    motor = base_en_0003.motor
    corrida, historica = base_en_0003.corrida_id, base_en_0003.historica_id

    # C. El identificador publico de una ejecucion no se repite.
    repetido = uuid4()
    _insertar(motor, INSERTAR_EJECUCION, _ejecucion(corrida, "FALLIDA", decision_run_id=repetido))
    _rechaza(
        motor,
        "uq_ejecucion_decision_decision_run_id",
        INSERTAR_EJECUCION,
        _ejecucion(corrida, "FALLIDA", decision_run_id=repetido),
    )

    # D. Lo que no termino EXITOSA no cuenta: una corrida se reintenta cuantas veces haga falta...
    _, en_proceso = _insertar(
        motor, INSERTAR_EJECUCION, _ejecucion(corrida, "FALLIDA"), _ejecucion(corrida)
    )
    _insertar(motor, INSERTAR_EJECUCION, _ejecucion(corrida, "EXITOSA"))
    # ...pero una segunda EXITOSA de la misma version no entra: ni insertada, ni al terminar una que
    # estaba en proceso, que es como se daria la carrera entre dos ejecuciones simultaneas.
    _rechaza(
        motor, "ux_ejecucion_decision_exitosa", INSERTAR_EJECUCION, _ejecucion(corrida, "EXITOSA")
    )
    _rechaza(motor, "ux_ejecucion_decision_exitosa", TERMINAR_EXITOSA, {"id": en_proceso})

    # E. Otra version de las reglas si decide la misma corrida, y la misma version, otra corrida.
    _insertar(
        motor,
        INSERTAR_EJECUCION,
        _ejecucion(corrida, "EXITOSA", "decision/v2"),
        _ejecucion(historica, "EXITOSA"),
    )

    with motor.connect() as conexion:
        ejecuciones = conexion.execute(
            text(
                "SELECT corrida_id, version_reglas, estado, count(*) FROM ejecucion_decision "
                "GROUP BY corrida_id, version_reglas, estado"
            )
        ).all()
    assert sorted(tuple(fila) for fila in ejecuciones) == sorted(
        [
            (corrida, "decision/v1", "FALLIDA", 2),
            (corrida, "decision/v1", "EN_PROCESO", 1),
            (corrida, "decision/v1", "EXITOSA", 1),
            (corrida, "decision/v2", "EXITOSA", 1),
            (historica, "decision/v1", "EXITOSA", 1),
        ]
    )


def test_una_ejecucion_decide_cada_cuenta_una_sola_vez(base_en_0003):
    motor, corrida, cuenta = base_en_0003.motor, base_en_0003.corrida_id, base_en_0003.cuenta_id
    v1, v2 = _insertar(
        motor,
        INSERTAR_EJECUCION,
        _ejecucion(corrida, "EXITOSA"),
        _ejecucion(corrida, "EXITOSA", "decision/v2"),
    )

    # F. Cada version puede decidir la misma cuenta, pero una ejecucion la decide una sola vez.
    decision, _ = _insertar(motor, INSERTAR_DECISION, _decision(v1, cuenta), _decision(v2, cuenta))
    _rechaza(
        motor,
        "uq_decision_cuenta_ejecucion_decision_id_cuenta_id",
        INSERTAR_DECISION,
        _decision(v1, cuenta),
    )

    # Los motivos quedan estructurados y en su orden: la lista, no un texto.
    with motor.connect() as conexion:
        guardados = conexion.execute(
            text("SELECT motivos FROM decision_cuenta WHERE id = :id"), {"id": decision}
        ).scalar_one()
    assert guardados == MOTIVOS


def test_una_decision_no_apunta_a_lo_que_no_existe_ni_se_borra_en_cascada(base_en_0003):
    motor, cuenta = base_en_0003.motor, base_en_0003.cuenta_id
    (ejecucion,) = _insertar(
        motor, INSERTAR_EJECUCION, _ejecucion(base_en_0003.corrida_id, "EXITOSA")
    )
    _insertar(motor, INSERTAR_DECISION, _decision(ejecucion, cuenta))
    _insertar(motor, INSERTAR_EJECUCION, _ejecucion(base_en_0003.historica_id, "FALLIDA"))

    # G. Nada apunta a lo que no existe: ni una decision a una ejecucion o a una cuenta, ni una
    # ejecucion a una corrida.
    no_existe = 999_999
    _rechaza(
        motor,
        "fk_decision_cuenta_ejecucion_decision_id_ejecucion_decision",
        INSERTAR_DECISION,
        _decision(no_existe, cuenta),
    )
    _rechaza(
        motor,
        "fk_decision_cuenta_cuenta_id_cuenta",
        INSERTAR_DECISION,
        _decision(ejecucion, no_existe),
    )
    _rechaza(
        motor,
        "fk_ejecucion_decision_corrida_id_corrida",
        INSERTAR_EJECUCION,
        _ejecucion(no_existe),
    )

    # Y sin cascada: borrar algo de lo que cuelgan decisiones falla, en lugar de llevarse la
    # evidencia.
    _rechaza(
        motor,
        "fk_decision_cuenta_cuenta_id_cuenta",
        text("DELETE FROM cuenta WHERE id = :id"),
        {"id": cuenta},
    )
    _rechaza(
        motor,
        "fk_decision_cuenta_ejecucion_decision_id_ejecucion_decision",
        text("DELETE FROM ejecucion_decision WHERE id = :id"),
        {"id": ejecucion},
    )
    # La corrida historica no tiene cuentas ni rechazos: lo unico que impide borrarla es su
    # ejecucion.
    _rechaza(
        motor,
        "fk_ejecucion_decision_corrida_id_corrida",
        text("DELETE FROM corrida WHERE id = :id"),
        {"id": base_en_0003.historica_id},
    )


def test_la_0003_baja_sin_perder_la_fase_1_y_vuelve_a_subir(base_en_0003):
    motor = base_en_0003.motor
    (ejecucion,) = _insertar(
        motor, INSERTAR_EJECUCION, _ejecucion(base_en_0003.corrida_id, "EXITOSA")
    )
    _insertar(motor, INSERTAR_DECISION, _decision(ejecucion, base_en_0003.cuenta_id))

    command.downgrade(_alembic(), "0002")

    # H. Bajar quita las decisiones y nada mas.
    tablas = set(_consultar(motor, TABLAS))
    assert not TABLAS_DE_DECISION & tablas
    assert {"corrida", "cuenta", "rechazo"} <= tablas
    assert _fase_1(motor) == base_en_0003.fase_1
    assert _consultar(motor, "SELECT version_num FROM alembic_version") == ["0002"]

    # Y la 0003 vuelve a subir limpia sobre la misma base.
    command.upgrade(_alembic(), "0003")

    assert TABLAS_DE_DECISION <= set(_consultar(motor, TABLAS))
    assert set(_consultar(motor, RESTRICCIONES)) == RESTRICCIONES_DE_DECISION
    assert _fase_1(motor) == base_en_0003.fase_1
    assert _consultar(motor, "SELECT version_num FROM alembic_version") == ["0003"]


# --- la 0004: las ejecuciones territoriales y sus resultados por municipio ----------------------

INSERTAR_TERRITORIAL = text(
    "INSERT INTO ejecucion_territorial (territorial_run_id, ejecucion_decision_id, "
    "version_reglas, estado, iniciada_en, territorios_evaluados, territorios_publicados) "
    "VALUES (:territorial_run_id, :ejecucion_decision_id, :version_reglas, :estado, now(), 0, 0) "
    "RETURNING id"
)
INSERTAR_RESULTADO = text(
    "INSERT INTO resultado_territorial (ejecucion_territorial_id, cve_entidad, cve_municipio, "
    "cuentas_total, saldo_total, cuentas_campo, saldo_campo, carga, posicion_campo, motivos) "
    "VALUES (:ejecucion_territorial_id, :cve_entidad, :cve_municipio, :cuentas_total, "
    ":saldo_total, :cuentas_campo, :saldo_campo, :carga, :posicion_campo, "
    "CAST(:motivos AS JSONB)) RETURNING id"
)
TERMINAR_TERRITORIAL_EXITOSA = text(
    "UPDATE ejecucion_territorial SET estado = 'EXITOSA', terminada_en = now() WHERE id = :id"
)

# Un municipio con cuentas de campo y uno sin ellas, como los publicaria territorial/v1.
CON_CAMPO = {
    "cuentas_total": 12,
    "saldo_total": Decimal("84000.50"),
    "cuentas_campo": 8,
    "saldo_campo": Decimal("61000.25"),
    "carga": "MEDIA",
    "motivos": [{"codigo": "CARGA_CAMPO_5_19", "campo": "cuentas_campo", "valor": "8"}],
}
SIN_CARGA = {
    "cuentas_total": 3,
    "saldo_total": Decimal("4500.00"),
    "cuentas_campo": 0,
    "saldo_campo": Decimal("0.00"),
    "carga": "SIN_CARGA",
    "motivos": [{"codigo": "SIN_CARGA_CAMPO", "campo": "cuentas_campo", "valor": "0"}],
}

TABLAS_TERRITORIALES = {"ejecucion_territorial", "resultado_territorial"}
# Las tablas que existen antes de la 0004. Se comparan completas, columna por columna: si la 0004
# tocara una fila o una columna de cualquiera de ellas, se notaria.
PREVIAS_A_LA_0004 = ("corrida", "cuenta", "rechazo", "ejecucion_decision", "decision_cuenta")

# Las mismas consultas de la 0003, sobre las tablas de la 0004.
RESTRICCIONES_0004 = RESTRICCIONES.replace(
    "'ejecucion_decision', 'decision_cuenta'", "'ejecucion_territorial', 'resultado_territorial'"
)
INDICES_0004 = INDICES.replace(
    "'ejecucion_decision', 'decision_cuenta'", "'ejecucion_territorial', 'resultado_territorial'"
)

# Con nombre propio en los modelos y en la 0004: los de la convencion pasarian de 63 caracteres, el
# limite de PostgreSQL, y la base guardaria unos recortados. Son parte del contrato del esquema:
# salen en los IntegrityError y una migracion futura se refiere a ellos.
FK_EJECUCION_TERRITORIAL = "fk_ejecucion_territorial_decision"
FK_RESULTADO_TERRITORIAL = "fk_resultado_territorial_ejecucion"
UQ_RESULTADO_TERRITORIAL = "uq_resultado_territorial_municipio"

RESTRICCIONES_TERRITORIALES = {
    "pk_ejecucion_territorial",
    "uq_ejecucion_territorial_territorial_run_id",
    FK_EJECUCION_TERRITORIAL,
    "ck_ejecucion_territorial_estado_territorial",
    "pk_resultado_territorial",
    FK_RESULTADO_TERRITORIAL,
    UQ_RESULTADO_TERRITORIAL,
}
INDICES_TERRITORIALES = {
    "ix_ejecucion_territorial_ejecucion_decision_id",
    "ux_ejecucion_territorial_exitosa",
    "ux_resultado_territorial_posicion_campo",
}
"""Los indices que no salen de una llave ni de una restriccion unica."""


def test_los_modelos_territoriales_declaran_el_esquema_de_la_0004():
    # Sin base: lo que declaran los modelos. En el CI, alembic check los compara ademas con la base
    # subida hasta la cabeza: el esquema de la 0004, mas el indice de las EN_PROCESO de la 0006.
    ejecucion, resultado = EjecucionTerritorial.__table__, ResultadoTerritorial.__table__
    assert (ejecucion.name, resultado.name) == ("ejecucion_territorial", "resultado_territorial")

    columnas = {columna.name: columna for columna in ejecucion.columns}
    assert list(columnas) == [
        "id",
        "territorial_run_id",
        "ejecucion_decision_id",
        "version_reglas",
        "estado",
        "iniciada_en",
        "terminada_en",
        "territorios_evaluados",
        "territorios_publicados",
        "detalle",
    ]
    assert [nombre for nombre, columna in columnas.items() if columna.nullable] == [
        "terminada_en",
        "detalle",
    ]
    assert columnas["iniciada_en"].type.timezone and columnas["terminada_en"].type.timezone
    assert columnas["version_reglas"].type.length == 32
    estado = columnas["estado"].type
    assert (estado.native_enum, estado.length, estado.enums) == (
        False,
        12,
        ["EN_PROCESO", "EXITOSA", "FALLIDA"],
    )

    columnas = {columna.name: columna for columna in resultado.columns}
    assert list(columnas) == [
        "id",
        "ejecucion_territorial_id",
        "cve_entidad",
        "cve_municipio",
        "cuentas_total",
        "saldo_total",
        "cuentas_campo",
        "saldo_campo",
        "carga",
        "posicion_campo",
        "motivos",
    ]
    assert [nombre for nombre, columna in columnas.items() if columna.nullable] == [
        "posicion_campo"
    ]
    # Conteos y lugar BIGINT, saldos NUMERIC(24, 2), motivos JSONB y el vocabulario como texto.
    for nombre in ("cuentas_total", "cuentas_campo", "posicion_campo"):
        assert isinstance(columnas[nombre].type, BigInteger)
    for nombre in ("saldo_total", "saldo_campo"):
        tipo = columnas[nombre].type
        assert type(tipo) is Numeric and (tipo.precision, tipo.scale) == (24, 2)
    assert isinstance(columnas["motivos"].type, postgresql.JSONB)
    longitudes = {
        nombre: columnas[nombre].type.length for nombre in ("cve_entidad", "cve_municipio")
    }
    assert longitudes == {"cve_entidad": 2, "cve_municipio": 3}
    assert columnas["carga"].type.length == 20

    # Ningun valor por omision en la base, ni cascadas.
    assert all(c.server_default is None for c in (*ejecucion.columns, *resultado.columns))
    llaves = [
        (llave.parent.name, llave.target_fullname, llave.ondelete)
        for llave in (*ejecucion.foreign_keys, *resultado.foreign_keys)
    ]
    assert llaves == [
        ("ejecucion_decision_id", "ejecucion_decision.id", None),
        ("ejecucion_territorial_id", "ejecucion_territorial.id", None),
    ]

    # Un municipio por ejecucion, y los indices con sus columnas y sus predicados.
    (unica,) = [c for c in resultado.constraints if isinstance(c, UniqueConstraint)]
    assert [c.name for c in unica.columns] == [
        "ejecucion_territorial_id",
        "cve_entidad",
        "cve_municipio",
    ]
    indices = {
        indice.name: (
            [c.name for c in indice.columns],
            indice.unique,
            str(indice.dialect_options["postgresql"]["where"]),
        )
        for indice in (*ejecucion.indexes, *resultado.indexes)
    }
    assert indices == {
        "ix_ejecucion_territorial_ejecucion_decision_id": (
            ["ejecucion_decision_id"],
            False,
            "None",
        ),
        "ux_ejecucion_territorial_exitosa": (
            ["ejecucion_decision_id", "version_reglas"],
            True,
            "estado = 'EXITOSA'",
        ),
        "ux_ejecucion_territorial_en_proceso": (
            ["ejecucion_decision_id", "version_reglas"],
            True,
            "estado = 'EN_PROCESO'",
        ),
        "ux_resultado_territorial_posicion_campo": (
            ["ejecucion_territorial_id", "posicion_campo"],
            True,
            "posicion_campo IS NOT NULL",
        ),
    }

    # Los nombres con que quedan en PostgreSQL son los que las pruebas de abajo esperan en la base,
    # tal cual: ninguno pasa de 63 caracteres, asi que nada se recorta.
    preparador = postgresql.dialect().identifier_preparer
    nombres = {
        preparador.format_constraint(c) for t in (ejecucion, resultado) for c in t.constraints
    } | {preparador.format_index(i) for t in (ejecucion, resultado) for i in t.indexes}
    assert nombres == (
        RESTRICCIONES_TERRITORIALES
        | INDICES_TERRITORIALES
        | {"ux_ejecucion_territorial_en_proceso"}
    )
    assert all(len(nombre) <= 63 for nombre in nombres)


@dataclass(frozen=True)
class BaseEn0004:
    """Una base con todo lo anterior a la 0004, decisiones incluidas, subida hasta la 0004."""

    motor: Engine
    corrida_id: int
    decision_v1_id: int
    """La ejecucion EXITOSA de decision/v1 de la corrida, con su decision por cuenta."""
    decision_v2_id: int
    """Otra EXITOSA de la misma corrida, de decision/v2: lo territorial tiene que poder decir de
    cual de las dos salio."""
    previas: dict[str, list[tuple]]
    """Las tablas anteriores a la 0004, completas, justo antes de subir."""


@pytest.fixture
def base_en_0004(base_en_0003) -> BaseEn0004:
    """Corridas, cuentas, rechazos, dos ejecuciones de decision con sus decisiones, y la base ya en
    la 0004."""
    motor, corrida, cuenta = base_en_0003.motor, base_en_0003.corrida_id, base_en_0003.cuenta_id
    v1, v2 = _insertar(
        motor,
        INSERTAR_EJECUCION,
        _ejecucion(corrida, "EXITOSA"),
        _ejecucion(corrida, "EXITOSA", "decision/v2"),
    )
    _insertar(motor, INSERTAR_DECISION, _decision(v1, cuenta), _decision(v2, cuenta))
    previas = _previas_a_la_0004(motor)

    command.upgrade(_alembic(), "0004")
    return BaseEn0004(motor, corrida, v1, v2, previas)


def _previas_a_la_0004(motor: Engine) -> dict[str, list[tuple]]:
    with motor.connect() as conexion:
        return {
            tabla: [
                tuple(fila) for fila in conexion.execute(text(f"SELECT * FROM {tabla} ORDER BY id"))
            ]
            for tabla in PREVIAS_A_LA_0004
        }


def _territorial(
    ejecucion_decision_id: int,
    estado: str = "EN_PROCESO",
    version: str = "territorial/v1",
    territorial_run_id: UUID | None = None,
) -> dict:
    return {
        "territorial_run_id": territorial_run_id or uuid4(),
        "ejecucion_decision_id": ejecucion_decision_id,
        "version_reglas": version,
        "estado": estado,
    }


def _resultado(
    ejecucion_territorial_id: int, cve_municipio: str, posicion_campo: int | None, **cambios: object
) -> dict:
    """Un municipio de la entidad 21: con lugar, uno con cuentas de campo; sin lugar, uno
    SIN_CARGA. `cambios` reemplaza cualquier valor."""
    valores = {**(SIN_CARGA if posicion_campo is None else CON_CAMPO), **cambios}
    return {
        "ejecucion_territorial_id": ejecucion_territorial_id,
        "cve_entidad": "21",
        "cve_municipio": cve_municipio,
        "posicion_campo": posicion_campo,
        **valores,
        "motivos": json.dumps(valores["motivos"]),
    }


def test_la_0004_agrega_las_tablas_territoriales_sin_tocar_lo_anterior(base_en_0004):
    motor, v1 = base_en_0004.motor, base_en_0004.decision_v1_id

    # A. Lo de antes sigue igual, fila por fila y columna por columna: la fase 1, las dos
    # ejecuciones de decision y sus decisiones.
    assert _previas_a_la_0004(motor) == base_en_0004.previas
    assert [len(base_en_0004.previas[tabla]) for tabla in PREVIAS_A_LA_0004] == [2, 1, 1, 2, 2]

    # B. Las tablas nuevas existen, vacias, con sus restricciones por nombre.
    assert TABLAS_TERRITORIALES <= set(_consultar(motor, TABLAS))
    assert _consultar(motor, "SELECT count(*) FROM ejecucion_territorial") == [0]
    assert _consultar(motor, "SELECT count(*) FROM resultado_territorial") == [0]
    assert set(_consultar(motor, RESTRICCIONES_0004)) == RESTRICCIONES_TERRITORIALES
    # El estado es VARCHAR con CHECK y no un tipo de PostgreSQL: no hay tipo que migrar ni borrar.
    consulta_tipo = "SELECT count(*) FROM pg_type WHERE typname = 'estado_territorial'"
    assert _consultar(motor, consulta_tipo) == [0]

    # Los tres estados entran. RECHAZADA no existe en lo territorial: no se vuelve a juzgar la
    # cartera ni sus decisiones.
    _insertar(
        motor,
        INSERTAR_TERRITORIAL,
        _territorial(v1),
        _territorial(v1, "EXITOSA"),
        _territorial(v1, "FALLIDA"),
    )
    _rechaza(
        motor,
        "ck_ejecucion_territorial_estado_territorial",
        INSERTAR_TERRITORIAL,
        _territorial(v1, "RECHAZADA"),
    )
    # La version de las reglas no tiene valor por omision: cada ejecucion trae la suya.
    _rechaza(
        motor,
        "version_reglas",
        text(
            "INSERT INTO ejecucion_territorial (territorial_run_id, ejecucion_decision_id, estado, "
            "iniciada_en, territorios_evaluados, territorios_publicados) "
            "VALUES (:territorial_run_id, :ejecucion_decision_id, 'EN_PROCESO', now(), 0, 0)"
        ),
        {"territorial_run_id": uuid4(), "ejecucion_decision_id": v1},
    )


def test_la_0004_indexa_las_ejecuciones_y_los_resultados_territoriales(base_en_0004):
    with base_en_0004.motor.connect() as conexion:
        filas = conexion.execute(text(INDICES_0004)).all()
    indices = {
        indice: (tabla, unico, columnas, parcial)
        for tabla, indice, unico, columnas, parcial in filas
    }

    # Los dos unicos parciales: una EXITOSA por ejecucion de decision y version, y un municipio por
    # lugar de campo, solo donde hay lugar.
    exitosas = indices["ux_ejecucion_territorial_exitosa"][3]
    assert "estado" in exitosas and "'EXITOSA'" in exitosas
    con_lugar = indices["ux_resultado_territorial_posicion_campo"][3]
    assert "posicion_campo IS NOT NULL" in con_lugar
    # Y no hay mas indices que esos, el de todas las ejecuciones territoriales de una ejecucion de
    # decision y los de las llaves y restricciones unicas. La unica de resultado_territorial empieza
    # por ejecucion_territorial_id, asi que ya sirve para encontrar los resultados de una ejecucion.
    assert indices == {
        "pk_ejecucion_territorial": ("ejecucion_territorial", True, ["id"], None),
        "uq_ejecucion_territorial_territorial_run_id": (
            "ejecucion_territorial",
            True,
            ["territorial_run_id"],
            None,
        ),
        "ix_ejecucion_territorial_ejecucion_decision_id": (
            "ejecucion_territorial",
            False,
            ["ejecucion_decision_id"],
            None,
        ),
        "ux_ejecucion_territorial_exitosa": (
            "ejecucion_territorial",
            True,
            ["ejecucion_decision_id", "version_reglas"],
            exitosas,
        ),
        "pk_resultado_territorial": ("resultado_territorial", True, ["id"], None),
        UQ_RESULTADO_TERRITORIAL: (
            "resultado_territorial",
            True,
            ["ejecucion_territorial_id", "cve_entidad", "cve_municipio"],
            None,
        ),
        "ux_resultado_territorial_posicion_campo": (
            "resultado_territorial",
            True,
            ["ejecucion_territorial_id", "posicion_campo"],
            con_lugar,
        ),
    }


def test_una_ejecucion_de_decision_se_organiza_con_exito_una_sola_vez_por_version(base_en_0004):
    motor, v1, v2 = base_en_0004.motor, base_en_0004.decision_v1_id, base_en_0004.decision_v2_id

    # C. El identificador publico de una ejecucion territorial no se repite.
    repetido = uuid4()
    _insertar(motor, INSERTAR_TERRITORIAL, _territorial(v1, "FALLIDA", territorial_run_id=repetido))
    _rechaza(
        motor,
        "uq_ejecucion_territorial_territorial_run_id",
        INSERTAR_TERRITORIAL,
        _territorial(v1, "FALLIDA", territorial_run_id=repetido),
    )

    # D. Lo que no termino EXITOSA no cuenta: dos FALLIDA, una EN_PROCESO y una EXITOSA conviven...
    _, en_proceso = _insertar(
        motor, INSERTAR_TERRITORIAL, _territorial(v1, "FALLIDA"), _territorial(v1)
    )
    _insertar(motor, INSERTAR_TERRITORIAL, _territorial(v1, "EXITOSA"))
    # ...pero una segunda EXITOSA de la misma version no entra: ni insertada, ni al terminar la que
    # estaba en proceso, que es como se daria la carrera entre dos ejecuciones simultaneas.
    _rechaza(
        motor,
        "ux_ejecucion_territorial_exitosa",
        INSERTAR_TERRITORIAL,
        _territorial(v1, "EXITOSA"),
    )
    _rechaza(
        motor, "ux_ejecucion_territorial_exitosa", TERMINAR_TERRITORIAL_EXITOSA, {"id": en_proceso}
    )

    # E. Otra version de las reglas territoriales si organiza las mismas decisiones, y la misma
    # version, las de otra ejecucion de decision.
    _insertar(
        motor,
        INSERTAR_TERRITORIAL,
        _territorial(v1, "EXITOSA", "territorial/v2"),
        _territorial(v2, "EXITOSA"),
    )

    with motor.connect() as conexion:
        ejecuciones = conexion.execute(
            text(
                "SELECT ejecucion_decision_id, version_reglas, estado, count(*) "
                "FROM ejecucion_territorial GROUP BY ejecucion_decision_id, version_reglas, estado"
            )
        ).all()
    assert sorted(tuple(fila) for fila in ejecuciones) == sorted(
        [
            (v1, "territorial/v1", "FALLIDA", 2),
            (v1, "territorial/v1", "EN_PROCESO", 1),
            (v1, "territorial/v1", "EXITOSA", 1),
            (v1, "territorial/v2", "EXITOSA", 1),
            (v2, "territorial/v1", "EXITOSA", 1),
        ]
    )


def test_una_ejecucion_territorial_publica_cada_municipio_y_cada_lugar_una_vez(base_en_0004):
    motor, v1 = base_en_0004.motor, base_en_0004.decision_v1_id
    una, otra = _insertar(
        motor,
        INSERTAR_TERRITORIAL,
        _territorial(v1, "EXITOSA"),
        _territorial(v1, "EXITOSA", "territorial/v2"),
    )
    # Dos municipios con lugar y tres sin lugar: los NULL no chocan entre si.
    _insertar(
        motor,
        INSERTAR_RESULTADO,
        _resultado(una, "001", 1),
        _resultado(una, "002", 2),
        _resultado(una, "003", None),
        _resultado(una, "004", None),
        _resultado(una, "005", None),
    )

    # F. Cada municipio, una vez por ejecucion, aunque llegue con otro lugar...
    _rechaza(motor, UQ_RESULTADO_TERRITORIAL, INSERTAR_RESULTADO, _resultado(una, "001", 3))
    # ...y cada lugar, de un solo municipio.
    for lugar, municipio in ((1, "006"), (2, "007")):
        _rechaza(
            motor,
            "ux_resultado_territorial_posicion_campo",
            INSERTAR_RESULTADO,
            _resultado(una, municipio, lugar),
        )
    # Otra ejecucion si publica el mismo municipio en el mismo lugar.
    _insertar(motor, INSERTAR_RESULTADO, _resultado(otra, "001", 1))

    with motor.connect() as conexion:
        publicados = conexion.execute(
            text(
                "SELECT ejecucion_territorial_id, cve_entidad, cve_municipio, posicion_campo "
                "FROM resultado_territorial ORDER BY id"
            )
        ).all()
    assert [tuple(fila) for fila in publicados] == [
        (una, "21", "001", 1),
        (una, "21", "002", 2),
        (una, "21", "003", None),
        (una, "21", "004", None),
        (una, "21", "005", None),
        (otra, "21", "001", 1),
    ]


def test_la_base_guarda_el_vocabulario_de_otra_version_de_las_reglas(base_en_0004):
    motor = base_en_0004.motor
    (ejecucion,) = _insertar(
        motor,
        INSERTAR_TERRITORIAL,
        _territorial(base_en_0004.decision_v1_id, "EXITOSA", "territorial/v2"),
    )
    futuros = [
        {"codigo": "REGLA_TERRITORIAL_FUTURA", "campo": "algo", "valor": "x"},
        {"codigo": "OTRA_REGLA_FUTURA", "campo": "otro", "valor": "y"},
    ]
    (resultado,) = _insertar(
        motor,
        INSERTAR_RESULTADO,
        _resultado(ejecucion, "001", 1, carga="CRITICA", motivos=futuros),
    )

    # G. Ni la carga ni los codigos de motivo estan atados al catalogo de territorial/v1: una
    # version nueva guarda los suyos sin migrar la tabla.
    with motor.connect() as conexion:
        version, carga, motivos = conexion.execute(
            text(
                "SELECT e.version_reglas, r.carga, r.motivos FROM resultado_territorial r "
                "JOIN ejecucion_territorial e ON e.id = r.ejecucion_territorial_id "
                "WHERE r.id = :id"
            ),
            {"id": resultado},
        ).one()
    assert (version, carga) == ("territorial/v2", "CRITICA")
    # Los motivos vuelven estructurados y en su orden: la lista de objetos, no un texto.
    assert motivos == futuros
    assert type(motivos) is list and all(type(motivo) is dict for motivo in motivos)


def test_la_base_guarda_los_saldos_y_conteos_de_un_municipio_completo(base_en_0004):
    motor = base_en_0004.motor
    (ejecucion,) = _insertar(
        motor, INSERTAR_TERRITORIAL, _territorial(base_en_0004.decision_v1_id, "EXITOSA")
    )
    # H. Valores que no caben en el NUMERIC(14, 2) de una cuenta ni en INTEGER. No son el tamano
    # real del sistema: prueban los tipos. Un municipio suma el saldo de muchas cuentas, y los
    # conteos van a salir de un COUNT.
    grandes = {
        "saldo_total": Decimal("12345678901234567890.12"),
        "saldo_campo": Decimal("9876543210987654321.09"),
        "cuentas_total": 3_000_000_000,
        "cuentas_campo": 2_500_000_000,
    }
    mas_alla_de_integer = 2**31
    motivos = [{"codigo": "CARGA_CAMPO_20_MAS", "campo": "cuentas_campo", "valor": "2500000000"}]
    (resultado,) = _insertar(
        motor,
        INSERTAR_RESULTADO,
        _resultado(ejecucion, "001", mas_alla_de_integer, carga="ALTA", motivos=motivos, **grandes),
    )

    with motor.connect() as conexion:
        fila = conexion.execute(
            text(
                "SELECT saldo_total, saldo_campo, cuentas_total, cuentas_campo, posicion_campo "
                "FROM resultado_territorial WHERE id = :id"
            ),
            {"id": resultado},
        ).one()
    assert tuple(fila) == (*grandes.values(), mas_alla_de_integer)
    # Los saldos vuelven como Decimal y exactos: ni float ni redondeo.
    assert [type(saldo) for saldo in fila[:2]] == [Decimal, Decimal]
    assert [str(saldo) for saldo in fila[:2]] == [
        "12345678901234567890.12",
        "9876543210987654321.09",
    ]


def test_nada_territorial_apunta_a_lo_que_no_existe_ni_se_borra_en_cascada(base_en_0004):
    motor = base_en_0004.motor
    # Una ejecucion de decision sin decisiones, para que lo unico que cuelgue de ella sea la
    # territorial. La base no juzga de que ejecucion de decision sale una territorial: eso le toca
    # al servicio.
    (decision,) = _insertar(
        motor, INSERTAR_EJECUCION, _ejecucion(base_en_0004.corrida_id, "FALLIDA")
    )
    (ejecucion,) = _insertar(motor, INSERTAR_TERRITORIAL, _territorial(decision, "EXITOSA"))
    _insertar(motor, INSERTAR_RESULTADO, _resultado(ejecucion, "001", 1))

    # I. Nada apunta a lo que no existe: ni una ejecucion territorial a una ejecucion de decision,
    # ni un resultado a una ejecucion territorial.
    no_existe = 999_999
    _rechaza(motor, FK_EJECUCION_TERRITORIAL, INSERTAR_TERRITORIAL, _territorial(no_existe))
    _rechaza(motor, FK_RESULTADO_TERRITORIAL, INSERTAR_RESULTADO, _resultado(no_existe, "001", 1))

    # Y sin cascada: borrar algo de lo que cuelga evidencia territorial falla, en lugar de
    # llevarsela.
    _rechaza(
        motor,
        FK_EJECUCION_TERRITORIAL,
        text("DELETE FROM ejecucion_decision WHERE id = :id"),
        {"id": decision},
    )
    _rechaza(
        motor,
        FK_RESULTADO_TERRITORIAL,
        text("DELETE FROM ejecucion_territorial WHERE id = :id"),
        {"id": ejecucion},
    )


def test_la_0004_baja_sin_perder_lo_anterior_y_vuelve_a_subir(base_en_0004):
    motor = base_en_0004.motor
    (ejecucion,) = _insertar(
        motor, INSERTAR_TERRITORIAL, _territorial(base_en_0004.decision_v1_id, "EXITOSA")
    )
    _insertar(
        motor,
        INSERTAR_RESULTADO,
        _resultado(ejecucion, "001", 1),
        _resultado(ejecucion, "002", None),
    )

    command.downgrade(_alembic(), "0003")

    # J. Bajar quita lo territorial, con sus datos, y nada mas.
    tablas = set(_consultar(motor, TABLAS))
    assert not TABLAS_TERRITORIALES & tablas
    assert set(PREVIAS_A_LA_0004) <= tablas
    assert _previas_a_la_0004(motor) == base_en_0004.previas
    assert _consultar(motor, "SELECT version_num FROM alembic_version") == ["0003"]
    consulta_tipo = "SELECT count(*) FROM pg_type WHERE typname = 'estado_territorial'"
    assert _consultar(motor, consulta_tipo) == [0]

    # Y la 0004 vuelve a subir limpia sobre la misma base, sin lo que se fue al bajar.
    command.upgrade(_alembic(), "0004")

    assert TABLAS_TERRITORIALES <= set(_consultar(motor, TABLAS))
    assert set(_consultar(motor, RESTRICCIONES_0004)) == RESTRICCIONES_TERRITORIALES
    assert _consultar(motor, "SELECT count(*) FROM ejecucion_territorial") == [0]
    assert _consultar(motor, "SELECT count(*) FROM resultado_territorial") == [0]
    assert _previas_a_la_0004(motor) == base_en_0004.previas
    assert _consultar(motor, "SELECT version_num FROM alembic_version") == ["0004"]


# --- la 0005: las ejecuciones de ruteo, sus rutas y sus paradas ---------------------------------

INSERTAR_CUENTA_0005 = text(
    "INSERT INTO cuenta (corrida_id, cliente_unico, saldo_total, dias_atraso, producto, canal, "
    "cve_entidad, cve_municipio, fecha_corte) VALUES (:corrida_id, :cliente_unico, 1000.00, 120, "
    "'CONSUMO', 'DIGITAL', '21', :cve_municipio, '2026-09-30') RETURNING id"
)
INSERTAR_RUTEO = text(
    "INSERT INTO ejecucion_ruteo (ruteo_run_id, ejecucion_territorial_id, version_reglas, estado, "
    "iniciada_en, rutas_evaluadas, rutas_publicadas, paradas_evaluadas, paradas_publicadas) "
    "VALUES (:ruteo_run_id, :ejecucion_territorial_id, :version_reglas, :estado, now(), 0, 0, 0, "
    "0) RETURNING id"
)
INSERTAR_RUTA = text(
    "INSERT INTO ruta_territorial (ejecucion_ruteo_id, resultado_territorial_id, paradas, "
    "distancia_inicial_m, distancia_total_m, distancia_regreso_deposito_m, mejora_2opt_m) "
    "VALUES (:ejecucion_ruteo_id, :resultado_territorial_id, :paradas, :distancia_inicial_m, "
    ":distancia_total_m, :distancia_regreso_deposito_m, :mejora_2opt_m) RETURNING id"
)
INSERTAR_PARADA = text(
    "INSERT INTO parada_ruta (ejecucion_ruteo_id, ruta_territorial_id, decision_cuenta_id, "
    "secuencia, x_m, y_m, distancia_desde_anterior_m) VALUES (:ejecucion_ruteo_id, "
    ":ruta_territorial_id, :decision_cuenta_id, :secuencia, :x_m, :y_m, "
    ":distancia_desde_anterior_m) RETURNING id"
)
TERMINAR_RUTEO_EXITOSA = text(
    "UPDATE ejecucion_ruteo SET estado = 'EXITOSA', terminada_en = now() WHERE id = :id"
)

# La ruta golden de ruteo/v1 en 21114: cinco paradas, y la primera, a 3086 m del deposito.
RUTA = {
    "paradas": 5,
    "distancia_inicial_m": 37878,
    "distancia_total_m": 28770,
    "distancia_regreso_deposito_m": 7633,
    "mejora_2opt_m": 9108,
}
PARADA = {"x_m": 1016, "y_m": -2070, "distancia_desde_anterior_m": 3086}

TABLAS_DE_RUTEO = {"ejecucion_ruteo", "ruta_territorial", "parada_ruta"}
# Las tablas que existen antes de la 0005. Se comparan completas, columna por columna.
PREVIAS_A_LA_0005 = (*PREVIAS_A_LA_0004, "ejecucion_territorial", "resultado_territorial")

# Las mismas consultas de la 0003, sobre las tablas de la 0005.
RESTRICCIONES_0005 = RESTRICCIONES.replace(
    "'ejecucion_decision', 'decision_cuenta'",
    "'ejecucion_ruteo', 'ruta_territorial', 'parada_ruta'",
)
INDICES_0005 = INDICES.replace(
    "'ejecucion_decision', 'decision_cuenta'",
    "'ejecucion_ruteo', 'ruta_territorial', 'parada_ruta'",
)

# Con nombre propio en los modelos y en la 0005: la convencion los dejaria largos, o justo en el
# limite de 63 caracteres. Son parte del contrato del esquema.
FK_EJECUCION_RUTEO = "fk_ejecucion_ruteo_territorial"
FK_RUTA_RESULTADO = "fk_ruta_territorial_resultado"
UQ_RUTA = "uq_ruta_ruteo_territorio"
UQ_PARADA_DECISION = "uq_parada_ruteo_decision"
UQ_PARADA_SECUENCIA = "uq_parada_ruta_secuencia"
# Los de la convencion, que caben.
FK_RUTA_EJECUCION = "fk_ruta_territorial_ejecucion_ruteo_id_ejecucion_ruteo"
FK_PARADA_EJECUCION = "fk_parada_ruta_ejecucion_ruteo_id_ejecucion_ruteo"
FK_PARADA_RUTA = "fk_parada_ruta_ruta_territorial_id_ruta_territorial"
FK_PARADA_DECISION = "fk_parada_ruta_decision_cuenta_id_decision_cuenta"

RESTRICCIONES_DE_RUTEO = {
    "pk_ejecucion_ruteo",
    "uq_ejecucion_ruteo_ruteo_run_id",
    FK_EJECUCION_RUTEO,
    "ck_ejecucion_ruteo_estado_ruteo",
    "pk_ruta_territorial",
    FK_RUTA_EJECUCION,
    FK_RUTA_RESULTADO,
    UQ_RUTA,
    "pk_parada_ruta",
    FK_PARADA_EJECUCION,
    FK_PARADA_RUTA,
    FK_PARADA_DECISION,
    UQ_PARADA_DECISION,
    UQ_PARADA_SECUENCIA,
}
INDICES_DE_RUTEO = {"ix_ejecucion_ruteo_ejecucion_territorial_id", "ux_ejecucion_ruteo_exitosa"}
"""Los indices que no salen de una llave ni de una restriccion unica."""


def test_los_modelos_de_ruteo_declaran_el_esquema_de_la_0005():
    # Sin base: lo que declaran los modelos. En el CI, alembic check los compara ademas con la base
    # subida hasta la cabeza: el esquema de la 0005, mas el indice de las EN_PROCESO de la 0006.
    ejecucion, ruta, parada = tablas = (
        EjecucionRuteo.__table__,
        RutaTerritorial.__table__,
        ParadaRuta.__table__,
    )
    assert [tabla.name for tabla in tablas] == [
        "ejecucion_ruteo",
        "ruta_territorial",
        "parada_ruta",
    ]

    columnas = {columna.name: columna for columna in ejecucion.columns}
    assert list(columnas) == [
        "id",
        "ruteo_run_id",
        "ejecucion_territorial_id",
        "version_reglas",
        "estado",
        "iniciada_en",
        "terminada_en",
        "rutas_evaluadas",
        "rutas_publicadas",
        "paradas_evaluadas",
        "paradas_publicadas",
        "detalle",
    ]
    assert [nombre for nombre, columna in columnas.items() if columna.nullable] == [
        "terminada_en",
        "detalle",
    ]
    assert columnas["iniciada_en"].type.timezone and columnas["terminada_en"].type.timezone
    assert columnas["version_reglas"].type.length == 32
    estado = columnas["estado"].type
    assert (estado.native_enum, estado.length, estado.enums) == (
        False,
        12,
        ["EN_PROCESO", "EXITOSA", "FALLIDA"],
    )
    # Los contadores, BIGINT, como los COUNT con que se comparan.
    for nombre in list(columnas)[7:11]:
        assert isinstance(columnas[nombre].type, BigInteger), nombre

    columnas = {columna.name: columna for columna in ruta.columns}
    assert list(columnas) == [
        "id",
        "ejecucion_ruteo_id",
        "resultado_territorial_id",
        "paradas",
        "distancia_inicial_m",
        "distancia_total_m",
        "distancia_regreso_deposito_m",
        "mejora_2opt_m",
    ]
    for nombre in list(columnas)[3:]:
        assert isinstance(columnas[nombre].type, BigInteger), nombre

    columnas = {columna.name: columna for columna in parada.columns}
    assert list(columnas) == [
        "id",
        "ejecucion_ruteo_id",
        "ruta_territorial_id",
        "decision_cuenta_id",
        "secuencia",
        "x_m",
        "y_m",
        "distancia_desde_anterior_m",
    ]
    for nombre in list(columnas)[4:]:
        assert isinstance(columnas[nombre].type, BigInteger), nombre
    # Las llaves son INTEGER, como los id a los que apuntan.
    for nombre in list(columnas)[1:4]:
        assert type(columnas[nombre].type) is Integer, nombre
    assert not any(columna.nullable for tabla in (ruta, parada) for columna in tabla.columns)

    # Ni cliente, ni clave o lugar del municipio, ni algoritmo, ni metrica, ni geografia: se leen
    # por JOIN, o son el contrato de la version, o no existen.
    todas = {columna.name for tabla in tablas for columna in tabla.columns}
    assert not todas & {
        "cliente_unico",
        "clave_territorio",
        "cve_entidad",
        "cve_municipio",
        "posicion_campo",
        "algoritmo",
        "metrica",
        "latitud",
        "longitud",
    }

    # Ningun valor por omision en la base, ni cascadas.
    assert all(columna.server_default is None for tabla in tablas for columna in tabla.columns)
    llaves = {
        (tabla.name, llave.parent.name): (llave.target_fullname, llave.ondelete)
        for tabla in tablas
        for llave in tabla.foreign_keys
    }
    assert llaves == {
        ("ejecucion_ruteo", "ejecucion_territorial_id"): ("ejecucion_territorial.id", None),
        ("ruta_territorial", "ejecucion_ruteo_id"): ("ejecucion_ruteo.id", None),
        ("ruta_territorial", "resultado_territorial_id"): ("resultado_territorial.id", None),
        ("parada_ruta", "ejecucion_ruteo_id"): ("ejecucion_ruteo.id", None),
        ("parada_ruta", "ruta_territorial_id"): ("ruta_territorial.id", None),
        ("parada_ruta", "decision_cuenta_id"): ("decision_cuenta.id", None),
    }

    # Una ruta por municipio y ejecucion; una parada por lugar de la ruta, y una por decision en
    # toda la ejecucion. Y los dos indices, con sus columnas y su predicado.
    unicas = {
        restriccion.name: [columna.name for columna in restriccion.columns]
        for tabla in (ruta, parada)
        for restriccion in tabla.constraints
        if isinstance(restriccion, UniqueConstraint)
    }
    assert unicas == {
        UQ_RUTA: ["ejecucion_ruteo_id", "resultado_territorial_id"],
        UQ_PARADA_DECISION: ["ejecucion_ruteo_id", "decision_cuenta_id"],
        UQ_PARADA_SECUENCIA: ["ruta_territorial_id", "secuencia"],
    }
    indices = {
        indice.name: (
            [c.name for c in indice.columns],
            indice.unique,
            str(indice.dialect_options["postgresql"]["where"]),
        )
        for tabla in tablas
        for indice in tabla.indexes
    }
    assert indices == {
        "ix_ejecucion_ruteo_ejecucion_territorial_id": (
            ["ejecucion_territorial_id"],
            False,
            "None",
        ),
        "ux_ejecucion_ruteo_exitosa": (
            ["ejecucion_territorial_id", "version_reglas"],
            True,
            "estado = 'EXITOSA'",
        ),
        "ux_ejecucion_ruteo_en_proceso": (
            ["ejecucion_territorial_id", "version_reglas"],
            True,
            "estado = 'EN_PROCESO'",
        ),
    }

    # Los nombres con que quedan en PostgreSQL son los que las pruebas de abajo esperan en la base,
    # tal cual: ninguno pasa de 63 caracteres, asi que nada se recorta.
    preparador = postgresql.dialect().identifier_preparer
    nombres = {preparador.format_constraint(c) for t in tablas for c in t.constraints} | {
        preparador.format_index(i) for t in tablas for i in t.indexes
    }
    assert nombres == RESTRICCIONES_DE_RUTEO | INDICES_DE_RUTEO | {"ux_ejecucion_ruteo_en_proceso"}
    assert all(len(nombre) <= 63 for nombre in nombres)


@dataclass(frozen=True)
class BaseEn0005:
    """Una base con todo lo anterior a la 0005, territorial incluido, subida hasta la 0005."""

    motor: Engine
    decisiones: tuple[int, ...]
    """Las cuatro decisiones por cuenta, por id: las dos de la 0004 y dos mas de decision/v1."""
    territorial_id: int
    """Una ejecucion territorial EXITOSA con tres municipios: dos con lugar y uno SIN_CARGA."""
    otra_territorial_id: int
    """Otra EXITOSA, de las decisiones de decision/v2 y sin municipios."""
    resultados: dict[str, int]
    """Los municipios de la primera, por clave."""
    previas: dict[str, list[tuple]]
    """Las tablas anteriores a la 0005, completas, justo antes de subir."""


@pytest.fixture
def base_en_0005(base_en_0004) -> BaseEn0005:
    """Todo lo de la 0004 con dos cuentas mas y sus decisiones, dos ejecuciones territoriales y tres
    municipios publicados, y la base ya en la 0005."""
    motor, corrida = base_en_0004.motor, base_en_0004.corrida_id
    cuentas = _insertar(
        motor,
        INSERTAR_CUENTA_0005,
        {"corrida_id": corrida, "cliente_unico": "CU00000002", "cve_municipio": "114"},
        {"corrida_id": corrida, "cliente_unico": "CU00000003", "cve_municipio": "156"},
    )
    _insertar(
        motor, INSERTAR_DECISION, *(_decision(base_en_0004.decision_v1_id, c) for c in cuentas)
    )
    territorial, otra = _insertar(
        motor,
        INSERTAR_TERRITORIAL,
        _territorial(base_en_0004.decision_v1_id, "EXITOSA"),
        _territorial(base_en_0004.decision_v2_id, "EXITOSA"),
    )
    resultados = _insertar(
        motor,
        INSERTAR_RESULTADO,
        _resultado(territorial, "114", 1),
        _resultado(territorial, "156", 2),
        _resultado(territorial, "001", None),
    )
    decisiones = tuple(_consultar(motor, "SELECT id FROM decision_cuenta ORDER BY id"))
    previas = _filas(motor, PREVIAS_A_LA_0005)

    command.upgrade(_alembic(), "0005")
    por_clave = dict(zip(("21114", "21156", "21001"), resultados, strict=True))
    return BaseEn0005(motor, decisiones, territorial, otra, por_clave, previas)


def _filas(motor: Engine, tablas: tuple[str, ...]) -> dict[str, list[tuple]]:
    """Cada tabla completa, en el orden de su id."""
    with motor.connect() as conexion:
        return {
            tabla: [
                tuple(fila) for fila in conexion.execute(text(f"SELECT * FROM {tabla} ORDER BY id"))
            ]
            for tabla in tablas
        }


def _ruteo(
    ejecucion_territorial_id: int,
    estado: str = "EN_PROCESO",
    version: str = "ruteo/v1",
    ruteo_run_id: UUID | None = None,
) -> dict:
    return {
        "ruteo_run_id": ruteo_run_id or uuid4(),
        "ejecucion_territorial_id": ejecucion_territorial_id,
        "version_reglas": version,
        "estado": estado,
    }


def _ruta(ejecucion_ruteo_id: int, resultado_territorial_id: int, **cambios: object) -> dict:
    return {
        "ejecucion_ruteo_id": ejecucion_ruteo_id,
        "resultado_territorial_id": resultado_territorial_id,
        **RUTA,
        **cambios,
    }


def _parada(
    ejecucion_ruteo_id: int,
    ruta_territorial_id: int,
    decision_cuenta_id: int,
    secuencia: int,
    **cambios: object,
) -> dict:
    return {
        "ejecucion_ruteo_id": ejecucion_ruteo_id,
        "ruta_territorial_id": ruta_territorial_id,
        "decision_cuenta_id": decision_cuenta_id,
        "secuencia": secuencia,
        **PARADA,
        **cambios,
    }


def test_la_0005_agrega_las_tablas_de_ruteo_sin_tocar_lo_anterior(base_en_0005):
    motor, territorial = base_en_0005.motor, base_en_0005.territorial_id

    # A. Lo de antes sigue igual, fila por fila y columna por columna: la fase 1, las decisiones y
    # lo territorial.
    assert _filas(motor, PREVIAS_A_LA_0005) == base_en_0005.previas
    cuantas = [len(base_en_0005.previas[tabla]) for tabla in PREVIAS_A_LA_0005]
    assert cuantas == [2, 3, 1, 2, 4, 2, 3]

    # B. Las tablas nuevas existen, vacias, con sus restricciones por nombre.
    assert TABLAS_DE_RUTEO <= set(_consultar(motor, TABLAS))
    for tabla in sorted(TABLAS_DE_RUTEO):
        assert _consultar(motor, f"SELECT count(*) FROM {tabla}") == [0]
    assert set(_consultar(motor, RESTRICCIONES_0005)) == RESTRICCIONES_DE_RUTEO
    # El estado es VARCHAR con CHECK y no un tipo de PostgreSQL: no hay tipo que migrar ni borrar.
    assert _consultar(motor, "SELECT count(*) FROM pg_type WHERE typname = 'estado_ruteo'") == [0]

    # Los tres estados entran. RECHAZADA no existe en el ruteo: no se vuelve a juzgar nada.
    _insertar(
        motor,
        INSERTAR_RUTEO,
        _ruteo(territorial),
        _ruteo(territorial, "EXITOSA"),
        _ruteo(territorial, "FALLIDA"),
    )
    _rechaza(
        motor, "ck_ejecucion_ruteo_estado_ruteo", INSERTAR_RUTEO, _ruteo(territorial, "RECHAZADA")
    )
    # La version de las reglas no tiene valor por omision: cada ejecucion trae la suya.
    _rechaza(
        motor,
        "version_reglas",
        text(
            "INSERT INTO ejecucion_ruteo (ruteo_run_id, ejecucion_territorial_id, estado, "
            "iniciada_en, rutas_evaluadas, rutas_publicadas, paradas_evaluadas, "
            "paradas_publicadas) VALUES (:ruteo_run_id, :ejecucion_territorial_id, 'EN_PROCESO', "
            "now(), 0, 0, 0, 0)"
        ),
        {"ruteo_run_id": uuid4(), "ejecucion_territorial_id": territorial},
    )


def test_la_0005_indexa_las_ejecuciones_las_rutas_y_las_paradas(base_en_0005):
    with base_en_0005.motor.connect() as conexion:
        filas = conexion.execute(text(INDICES_0005)).all()
    indices = {
        indice: (tabla, unico, columnas, parcial)
        for tabla, indice, unico, columnas, parcial in filas
    }

    exitosas = indices["ux_ejecucion_ruteo_exitosa"][3]
    assert "estado" in exitosas and "'EXITOSA'" in exitosas
    # Y no hay mas indices que el de todas las ejecuciones de ruteo de una territorial, el de la
    # idempotencia y los de las llaves y restricciones unicas. Las unicas de rutas y paradas
    # empiezan por la columna con que se consultan, asi que no hace falta ningun indice suelto.
    assert indices == {
        "pk_ejecucion_ruteo": ("ejecucion_ruteo", True, ["id"], None),
        "uq_ejecucion_ruteo_ruteo_run_id": ("ejecucion_ruteo", True, ["ruteo_run_id"], None),
        "ix_ejecucion_ruteo_ejecucion_territorial_id": (
            "ejecucion_ruteo",
            False,
            ["ejecucion_territorial_id"],
            None,
        ),
        "ux_ejecucion_ruteo_exitosa": (
            "ejecucion_ruteo",
            True,
            ["ejecucion_territorial_id", "version_reglas"],
            exitosas,
        ),
        "pk_ruta_territorial": ("ruta_territorial", True, ["id"], None),
        UQ_RUTA: (
            "ruta_territorial",
            True,
            ["ejecucion_ruteo_id", "resultado_territorial_id"],
            None,
        ),
        "pk_parada_ruta": ("parada_ruta", True, ["id"], None),
        UQ_PARADA_DECISION: (
            "parada_ruta",
            True,
            ["ejecucion_ruteo_id", "decision_cuenta_id"],
            None,
        ),
        UQ_PARADA_SECUENCIA: ("parada_ruta", True, ["ruta_territorial_id", "secuencia"], None),
    }


def test_una_ejecucion_territorial_se_rutea_con_exito_una_sola_vez_por_version(base_en_0005):
    motor = base_en_0005.motor
    territorial, otra = base_en_0005.territorial_id, base_en_0005.otra_territorial_id

    # C. El identificador publico de una ejecucion de ruteo no se repite.
    repetido = uuid4()
    _insertar(motor, INSERTAR_RUTEO, _ruteo(territorial, "FALLIDA", ruteo_run_id=repetido))
    _rechaza(
        motor,
        "uq_ejecucion_ruteo_ruteo_run_id",
        INSERTAR_RUTEO,
        _ruteo(territorial, "FALLIDA", ruteo_run_id=repetido),
    )

    # D. Lo que no termino EXITOSA no cuenta: dos FALLIDA, una EN_PROCESO y una EXITOSA conviven...
    _, en_proceso = _insertar(
        motor, INSERTAR_RUTEO, _ruteo(territorial, "FALLIDA"), _ruteo(territorial)
    )
    _insertar(motor, INSERTAR_RUTEO, _ruteo(territorial, "EXITOSA"))
    # ...pero una segunda EXITOSA de la misma version no entra: ni insertada, ni al terminar la que
    # estaba en proceso, que es como se daria la carrera entre dos ejecuciones simultaneas.
    _rechaza(motor, "ux_ejecucion_ruteo_exitosa", INSERTAR_RUTEO, _ruteo(territorial, "EXITOSA"))
    _rechaza(motor, "ux_ejecucion_ruteo_exitosa", TERMINAR_RUTEO_EXITOSA, {"id": en_proceso})

    # E. ruteo/v2 si rutea la misma ejecucion territorial, y ruteo/v1, otra.
    _insertar(
        motor,
        INSERTAR_RUTEO,
        _ruteo(territorial, "EXITOSA", "ruteo/v2"),
        _ruteo(otra, "EXITOSA"),
    )

    with motor.connect() as conexion:
        ejecuciones = conexion.execute(
            text(
                "SELECT ejecucion_territorial_id, version_reglas, estado, count(*) "
                "FROM ejecucion_ruteo GROUP BY ejecucion_territorial_id, version_reglas, estado"
            )
        ).all()
    assert sorted(tuple(fila) for fila in ejecuciones) == sorted(
        [
            (territorial, "ruteo/v1", "FALLIDA", 2),
            (territorial, "ruteo/v1", "EN_PROCESO", 1),
            (territorial, "ruteo/v1", "EXITOSA", 1),
            (territorial, "ruteo/v2", "EXITOSA", 1),
            (otra, "ruteo/v1", "EXITOSA", 1),
        ]
    )


def test_una_ejecucion_publica_una_sola_ruta_por_municipio(base_en_0005):
    motor, resultados = base_en_0005.motor, base_en_0005.resultados
    una, otra = _insertar(
        motor,
        INSERTAR_RUTEO,
        _ruteo(base_en_0005.territorial_id, "EXITOSA"),
        _ruteo(base_en_0005.territorial_id, "EXITOSA", "ruteo/v2"),
    )
    _insertar(
        motor, INSERTAR_RUTA, _ruta(una, resultados["21114"]), _ruta(una, resultados["21156"])
    )

    # F. Cada municipio, una ruta por ejecucion, aunque llegue con otras distancias...
    _rechaza(motor, UQ_RUTA, INSERTAR_RUTA, _ruta(una, resultados["21114"], paradas=7))
    # ...y otra ejecucion si publica su propia ruta del mismo municipio.
    _insertar(motor, INSERTAR_RUTA, _ruta(otra, resultados["21114"]))

    with motor.connect() as conexion:
        publicadas = conexion.execute(
            text(
                "SELECT r.ejecucion_ruteo_id, t.cve_entidad || t.cve_municipio, t.posicion_campo, "
                "r.paradas, r.distancia_total_m FROM ruta_territorial r "
                "JOIN resultado_territorial t ON t.id = r.resultado_territorial_id ORDER BY r.id"
            )
        ).all()
    # La clave y el lugar del municipio no se guardan en la ruta: salen del resultado territorial.
    assert [tuple(fila) for fila in publicadas] == [
        (una, "21114", 1, 5, 28770),
        (una, "21156", 2, 5, 28770),
        (otra, "21114", 1, 5, 28770),
    ]


def test_cada_parada_tiene_su_lugar_y_cada_decision_una_sola_parada_por_ejecucion(base_en_0005):
    motor, resultados = base_en_0005.motor, base_en_0005.resultados
    a, b, c, _ = base_en_0005.decisiones
    una, otra = _insertar(
        motor,
        INSERTAR_RUTEO,
        _ruteo(base_en_0005.territorial_id, "EXITOSA"),
        _ruteo(base_en_0005.territorial_id, "EXITOSA", "ruteo/v2"),
    )
    norte, sur, de_la_otra = _insertar(
        motor,
        INSERTAR_RUTA,
        _ruta(una, resultados["21114"]),
        _ruta(una, resultados["21156"]),
        _ruta(otra, resultados["21114"]),
    )
    _insertar(motor, INSERTAR_PARADA, _parada(una, norte, a, 1), _parada(una, norte, b, 2))

    # G. Cada lugar de la secuencia es de una sola parada por ruta...
    _rechaza(motor, UQ_PARADA_SECUENCIA, INSERTAR_PARADA, _parada(una, norte, c, 2))
    # ...y una decision es a lo mas una parada en toda la ejecucion, aunque sea en otra de sus
    # rutas. Para eso ParadaRuta repite ejecucion_ruteo_id.
    _rechaza(motor, UQ_PARADA_DECISION, INSERTAR_PARADA, _parada(una, sur, a, 1))
    # Otra ruta tiene su propia secuencia, desde 1.
    _insertar(motor, INSERTAR_PARADA, _parada(una, sur, c, 1))
    # Y otra ejecucion si puede volver a rutear las mismas decisiones.
    _insertar(
        motor, INSERTAR_PARADA, _parada(otra, de_la_otra, a, 1), _parada(otra, de_la_otra, b, 2)
    )

    with motor.connect() as conexion:
        paradas = conexion.execute(
            text(
                "SELECT ejecucion_ruteo_id, ruta_territorial_id, decision_cuenta_id, secuencia "
                "FROM parada_ruta ORDER BY id"
            )
        ).all()
    assert [tuple(fila) for fila in paradas] == [
        (una, norte, a, 1),
        (una, norte, b, 2),
        (una, sur, c, 1),
        (otra, de_la_otra, a, 1),
        (otra, de_la_otra, b, 2),
    ]


def test_la_base_guarda_coordenadas_negativas_y_numeros_que_no_caben_en_integer(base_en_0005):
    motor = base_en_0005.motor
    (ejecucion,) = _insertar(motor, INSERTAR_RUTEO, _ruteo(base_en_0005.territorial_id, "EXITOSA"))
    # H. Valores que no caben en INTEGER. No son el tamano real del sistema: prueban los tipos.
    grande = 3_000_000_000
    with motor.begin() as conexion:
        conexion.execute(
            text(
                "UPDATE ejecucion_ruteo SET rutas_evaluadas = :n, rutas_publicadas = :n, "
                "paradas_evaluadas = :n, paradas_publicadas = :n WHERE id = :id"
            ),
            {"n": grande, "id": ejecucion},
        )
    distancias = {
        "paradas": grande,
        "distancia_inicial_m": 9_000_000_001,
        "distancia_total_m": 9_000_000_000,
        "distancia_regreso_deposito_m": 2**33,
        "mejora_2opt_m": 1,
    }
    (ruta,) = _insertar(
        motor, INSERTAR_RUTA, _ruta(ejecucion, base_en_0005.resultados["21114"], **distancias)
    )
    # Coordenadas a los dos lados del deposito, en las orillas del plano de ruteo/v1 y fuera de el:
    # la base no ata las coordenadas al plano de una version, y no lleva CHECK.
    coordenadas = [(-5000, 5000), (4999, -1), (-(2**40), 2**40), (0, 0)]
    _insertar(
        motor,
        INSERTAR_PARADA,
        *(
            _parada(
                ejecucion, ruta, decision, 2**31 + n, x_m=x, y_m=y, distancia_desde_anterior_m=0
            )
            for n, (decision, (x, y)) in enumerate(
                zip(base_en_0005.decisiones, coordenadas, strict=True)
            )
        ),
    )

    with motor.connect() as conexion:
        contadores = conexion.execute(
            text(
                "SELECT rutas_evaluadas, rutas_publicadas, paradas_evaluadas, paradas_publicadas "
                "FROM ejecucion_ruteo WHERE id = :id"
            ),
            {"id": ejecucion},
        ).one()
        guardada = conexion.execute(
            text(
                "SELECT paradas, distancia_inicial_m, distancia_total_m, "
                "distancia_regreso_deposito_m, mejora_2opt_m FROM ruta_territorial WHERE id = :id"
            ),
            {"id": ruta},
        ).one()
        paradas = conexion.execute(
            text("SELECT secuencia, x_m, y_m FROM parada_ruta ORDER BY secuencia")
        ).all()
    assert tuple(contadores) == (grande,) * 4
    assert tuple(guardada) == tuple(distancias.values())
    assert [tuple(fila) for fila in paradas] == [
        (2**31 + n, x, y) for n, (x, y) in enumerate(coordenadas)
    ]


def test_nada_del_ruteo_apunta_a_lo_que_no_existe_ni_se_borra_en_cascada(base_en_0005):
    motor, resultados = base_en_0005.motor, base_en_0005.resultados
    a, b, *_ = base_en_0005.decisiones
    # Una ejecucion con una ruta y su parada; otra, de la territorial sin municipios, con una ruta
    # sin paradas; y una tercera sin rutas pero con una parada. La base no exige que una parada y su
    # ruta sean de la misma ejecucion, ni que la ruta sea de un municipio de su territorial: eso lo
    # cuida el servicio. Aqui sirve para que cada borrado choque con una sola llave.
    una, sin_paradas, sin_rutas = _insertar(
        motor,
        INSERTAR_RUTEO,
        _ruteo(base_en_0005.territorial_id, "EXITOSA"),
        _ruteo(base_en_0005.otra_territorial_id, "EXITOSA"),
        _ruteo(base_en_0005.territorial_id, "FALLIDA"),
    )
    ruta, sola = _insertar(
        motor,
        INSERTAR_RUTA,
        _ruta(una, resultados["21114"]),
        _ruta(sin_paradas, resultados["21156"]),
    )
    _insertar(motor, INSERTAR_PARADA, _parada(una, ruta, a, 1), _parada(sin_rutas, ruta, b, 2))

    # I. Nada apunta a lo que no existe.
    no_existe = 999_999
    _rechaza(motor, FK_EJECUCION_RUTEO, INSERTAR_RUTEO, _ruteo(no_existe))
    _rechaza(motor, FK_RUTA_EJECUCION, INSERTAR_RUTA, _ruta(no_existe, resultados["21001"]))
    _rechaza(motor, FK_RUTA_RESULTADO, INSERTAR_RUTA, _ruta(una, no_existe))
    _rechaza(motor, FK_PARADA_EJECUCION, INSERTAR_PARADA, _parada(no_existe, ruta, b, 3))
    _rechaza(motor, FK_PARADA_RUTA, INSERTAR_PARADA, _parada(una, no_existe, b, 3))
    _rechaza(motor, FK_PARADA_DECISION, INSERTAR_PARADA, _parada(una, ruta, no_existe, 3))

    # Y sin cascada: borrar algo de lo que cuelga una ruta o una parada falla, en lugar de llevarse
    # la evidencia.
    borrados = [
        (FK_EJECUCION_RUTEO, "ejecucion_territorial", base_en_0005.otra_territorial_id),
        (FK_RUTA_RESULTADO, "resultado_territorial", resultados["21156"]),
        (FK_RUTA_EJECUCION, "ejecucion_ruteo", sin_paradas),
        (FK_PARADA_EJECUCION, "ejecucion_ruteo", sin_rutas),
        (FK_PARADA_RUTA, "ruta_territorial", ruta),
        (FK_PARADA_DECISION, "decision_cuenta", a),
    ]
    for restriccion, tabla, fila in borrados:
        _rechaza(motor, restriccion, text(f"DELETE FROM {tabla} WHERE id = :id"), {"id": fila})
    # Una ruta sin paradas si se borra: nada cuelga de ella.
    with motor.begin() as conexion:
        conexion.execute(text("DELETE FROM ruta_territorial WHERE id = :id"), {"id": sola})


def test_la_0005_baja_sin_perder_lo_anterior_y_vuelve_a_subir(base_en_0005):
    motor, resultados = base_en_0005.motor, base_en_0005.resultados
    a, b, *_ = base_en_0005.decisiones
    (ejecucion,) = _insertar(motor, INSERTAR_RUTEO, _ruteo(base_en_0005.territorial_id, "EXITOSA"))
    (ruta,) = _insertar(motor, INSERTAR_RUTA, _ruta(ejecucion, resultados["21114"]))
    _insertar(
        motor, INSERTAR_PARADA, _parada(ejecucion, ruta, a, 1), _parada(ejecucion, ruta, b, 2)
    )

    command.downgrade(_alembic(), "0004")

    # J. Bajar quita el ruteo, con sus datos, y nada mas.
    tablas = set(_consultar(motor, TABLAS))
    assert not TABLAS_DE_RUTEO & tablas
    assert set(PREVIAS_A_LA_0005) <= tablas
    assert _filas(motor, PREVIAS_A_LA_0005) == base_en_0005.previas
    assert _consultar(motor, "SELECT version_num FROM alembic_version") == ["0004"]
    assert _consultar(motor, "SELECT count(*) FROM pg_type WHERE typname = 'estado_ruteo'") == [0]

    # Y la 0005 vuelve a subir limpia sobre la misma base, sin lo que se fue al bajar.
    command.upgrade(_alembic(), "0005")

    assert TABLAS_DE_RUTEO <= set(_consultar(motor, TABLAS))
    assert set(_consultar(motor, RESTRICCIONES_0005)) == RESTRICCIONES_DE_RUTEO
    for tabla in sorted(TABLAS_DE_RUTEO):
        assert _consultar(motor, f"SELECT count(*) FROM {tabla}") == [0]
    assert _filas(motor, PREVIAS_A_LA_0005) == base_en_0005.previas
    assert _consultar(motor, "SELECT version_num FROM alembic_version") == ["0005"]


# --- la 0006: la orquestacion durable -------------------------------------------------------------

INSERTAR_CORRIDA_0006 = text(
    "INSERT INTO corrida (run_id, iniciada_en, origen, firma, estado, tolerancia_rechazo, "
    "version_contrato, filas_leidas, filas_validas, filas_rechazadas) VALUES (:run_id, now(), "
    ":origen, :firma, :estado, 0.05, 'cartera/v1', 0, 0, 0) RETURNING id"
)
INSERTAR_ARCHIVO = text(
    "INSERT INTO archivo_corrida (corrida_id, contenido, tamano_bytes, creado_en) "
    "VALUES (:corrida_id, :contenido, :tamano_bytes, now()) RETURNING corrida_id"
)
INSERTAR_FLUJO = text(
    "INSERT INTO flujo_orquestacion (flujo_id, corrida_id, ejecucion_decision_id, "
    "ejecucion_territorial_id, ejecucion_ruteo_id, estado, etapa, creado_en, actualizado_en, "
    "terminado_en) VALUES (:flujo_id, :corrida_id, :decision, :territorial, :ruteo, :estado, "
    ":etapa, now(), now(), :terminado_en) RETURNING id"
)
INSERTAR_TRABAJO = text(
    "INSERT INTO trabajo_orquestacion (trabajo_id, flujo_id, tipo, estado, corrida_id, "
    "ejecucion_decision_id, ejecucion_territorial_id, ejecucion_ruteo_id, intentos, max_intentos, "
    "creado_en, disponible_desde, worker_id, lease_hasta, terminado_en) VALUES (:trabajo_id, "
    ":flujo_id, :tipo, :estado, :corrida_id, :decision, :territorial, :ruteo, :intentos, "
    ":max_intentos, now(), now(), :worker_id, :lease_hasta, :terminado_en) RETURNING id"
)

TABLAS_DE_ORQUESTACION = {"archivo_corrida", "flujo_orquestacion", "trabajo_orquestacion"}
# Las tablas que existen antes de la 0006, que se comparan completas, columna por columna; y de
# ellas, las que tienen recursos EN_PROCESO, que la 0006 cierra.
PREVIAS_A_LA_0006 = (*PREVIAS_A_LA_0005, "ejecucion_ruteo", "ruta_territorial", "parada_ruta")
RECURSOS = ("corrida", "ejecucion_decision", "ejecucion_territorial", "ejecucion_ruteo")

RESTRICCIONES_0006 = RESTRICCIONES.replace(
    "'ejecucion_decision', 'decision_cuenta'",
    "'archivo_corrida', 'flujo_orquestacion', 'trabajo_orquestacion'",
)
INDICES_0006 = INDICES.replace(
    "'ejecucion_decision', 'decision_cuenta'",
    "'archivo_corrida', 'flujo_orquestacion', 'trabajo_orquestacion'",
)
INDICES_DE_RECURSOS = INDICES.replace(
    "'ejecucion_decision', 'decision_cuenta'",
    "'corrida', 'ejecucion_decision', 'ejecucion_territorial', 'ejecucion_ruteo'",
)

# Cortas y con nombre propio, salvo las de los estados y las de los identificadores publicos, que
# salen de la convencion. Son parte del contrato del esquema: salen en los IntegrityError.
RESTRICCIONES_DE_ORQUESTACION = {
    "pk_archivo_corrida",
    "fk_archivo_corrida_corrida_id_corrida",
    "ck_archivo_tamano_positivo",
    "ck_archivo_tamano_exacto",
    "pk_flujo_orquestacion",
    "uq_flujo_orquestacion_flujo_id",
    "fk_flujo_corrida",
    "fk_flujo_decision",
    "fk_flujo_territorial",
    "fk_flujo_ruteo",
    "uq_flujo_corrida",
    "uq_flujo_decision",
    "uq_flujo_territorial",
    "uq_flujo_ruteo",
    "ck_flujo_cadena",
    "ck_flujo_completado",
    "ck_flujo_terminado",
    "ck_flujo_orquestacion_estado_flujo",
    "ck_flujo_orquestacion_etapa_flujo",
    "pk_trabajo_orquestacion",
    "uq_trabajo_orquestacion_trabajo_id",
    "fk_trabajo_flujo",
    "fk_trabajo_corrida",
    "fk_trabajo_decision",
    "fk_trabajo_territorial",
    "fk_trabajo_ruteo",
    "uq_trabajo_corrida",
    "uq_trabajo_decision",
    "uq_trabajo_territorial",
    "uq_trabajo_ruteo",
    "ck_trabajo_objetivo",
    "ck_trabajo_lease",
    "ck_trabajo_intentos",
    "ck_trabajo_orquestacion_tipo_trabajo",
    "ck_trabajo_orquestacion_estado_trabajo",
}
INDICES_DE_ORQUESTACION = {"ix_trabajo_orquestacion_flujo_id", "ix_trabajo_reclamable"}
"""Los indices que no salen de una llave ni de una restriccion unica."""

# Un intento activo por fuente y version, y una publicacion: cada indice con su tabla, sus
# columnas y el estado de su predicado. Los de las EXITOSA son de antes y se quedan.
UNICOS_POR_ESTADO = {
    "ux_corrida_firma_en_proceso": ("corrida", ["firma"], "EN_PROCESO"),
    "ux_corrida_firma_publicada": ("corrida", ["firma"], "EXITOSA"),
    "ux_ejecucion_decision_en_proceso": (
        "ejecucion_decision",
        ["corrida_id", "version_reglas"],
        "EN_PROCESO",
    ),
    "ux_ejecucion_decision_exitosa": (
        "ejecucion_decision",
        ["corrida_id", "version_reglas"],
        "EXITOSA",
    ),
    "ux_ejecucion_territorial_en_proceso": (
        "ejecucion_territorial",
        ["ejecucion_decision_id", "version_reglas"],
        "EN_PROCESO",
    ),
    "ux_ejecucion_territorial_exitosa": (
        "ejecucion_territorial",
        ["ejecucion_decision_id", "version_reglas"],
        "EXITOSA",
    ),
    "ux_ejecucion_ruteo_en_proceso": (
        "ejecucion_ruteo",
        ["ejecucion_territorial_id", "version_reglas"],
        "EN_PROCESO",
    ),
    "ux_ejecucion_ruteo_exitosa": (
        "ejecucion_ruteo",
        ["ejecucion_territorial_id", "version_reglas"],
        "EXITOSA",
    ),
}


def _cierres() -> dict[str, str]:
    """El motivo con que la 0006 cierra cada tabla, tal como lo escribe la migracion."""
    return ScriptDirectory.from_config(_alembic()).get_revision("0006").module.CIERRES


def _por_columna(motor: Engine, tablas: tuple[str, ...]) -> dict[str, list[dict]]:
    """Cada tabla completa, fila por fila y por nombre de columna, en el orden de su id."""
    with motor.connect() as conexion:
        return {
            tabla: [
                dict(fila)
                for fila in conexion.execute(text(f"SELECT * FROM {tabla} ORDER BY id")).mappings()
            ]
            for tabla in tablas
        }


def _corridas(motor: Engine, *estados: str) -> list[int]:
    """Corridas nuevas, una por estado, cada una con su propia firma."""
    return _insertar(
        motor,
        INSERTAR_CORRIDA_0006,
        *(
            {"run_id": uuid4(), "origen": "nueva.csv", "firma": uuid4().hex * 2, "estado": estado}
            for estado in estados
        ),
    )


def _flujo(corrida_id: int, etapa: str = "INGESTA", estado: str = "EN_PROCESO", **cambios) -> dict:
    """Un flujo de la corrida en su etapa; `cambios` fija los punteros (decision, territorial,
    ruteo) y reemplaza cualquier otro valor. Termina cuando ya no esta EN_PROCESO."""
    return {
        "flujo_id": uuid4(),
        "corrida_id": corrida_id,
        "decision": None,
        "territorial": None,
        "ruteo": None,
        "estado": estado,
        "etapa": etapa,
        "terminado_en": None if estado == "EN_PROCESO" else datetime.now(UTC),
        **cambios,
    }


OBJETIVOS = {
    "INGESTA": "corrida_id",
    "DECISION": "decision",
    "TERRITORIAL": "territorial",
    "RUTEO": "ruteo",
}


def _trabajo(tipo: str, objetivo: int | None, estado: str = "PENDIENTE", **cambios) -> dict:
    """Un trabajo de ese tipo sobre su objetivo, coherente con su estado; `cambios` reemplaza
    cualquier valor. Un tipo que no existe no tiene objetivo."""
    valores = {
        "trabajo_id": uuid4(),
        "flujo_id": None,
        "tipo": tipo,
        "estado": estado,
        "corrida_id": None,
        "decision": None,
        "territorial": None,
        "ruteo": None,
        "intentos": 0,
        "max_intentos": 5,
        "worker_id": None,
        "lease_hasta": None,
        "terminado_en": None,
    }
    if tipo in OBJETIVOS:
        valores[OBJETIVOS[tipo]] = objetivo
    if estado == "EJECUTANDO":
        lease = datetime.now(UTC) + timedelta(minutes=1)
        valores.update(intentos=1, worker_id="worker-de-prueba:1:abc", lease_hasta=lease)
    elif estado in ("COMPLETADO", "FALLIDO"):
        valores.update(intentos=1, terminado_en=datetime.now(UTC))
    return {**valores, **cambios}


@dataclass(frozen=True)
class BaseEn0006:
    """Una base con todo lo de la 0005, con recursos EN_PROCESO como los dejaba la v0.4.0,
    subida hasta la 0006."""

    motor: Engine
    corrida_id: int
    """La corrida de la 0002, EXITOSA y con cuentas."""
    decision_id: int
    """Su ejecucion EXITOSA de decision/v1."""
    territorial_id: int
    """La ejecucion territorial EXITOSA de esas decisiones."""
    ruteo_id: int
    """Una ejecucion de ruteo EXITOSA de esa territorial."""
    abiertas: dict[str, list[int]]
    """Por tabla, los recursos que estaban EN_PROCESO al subir: dos por fuente, duplicados, como
    podian quedar antes de la 0006."""
    previas: dict[str, list[dict]]
    """Las tablas anteriores a la 0006, completas, justo antes de subir."""


@pytest.fixture
def base_en_0006(base_en_0005) -> BaseEn0006:
    """Todo lo de la 0005, con dos intentos EN_PROCESO de cada recurso sobre la misma fuente y otros
    ya terminados, y la base ya en la 0006."""
    motor = base_en_0005.motor
    historica, corrida = _consultar(motor, "SELECT id FROM corrida ORDER BY id")
    decision_v1, decision_v2 = _consultar(motor, "SELECT id FROM ejecucion_decision ORDER BY id")
    territorial, otra = base_en_0005.territorial_id, base_en_0005.otra_territorial_id
    abiertas = {
        "corrida": _insertar(
            motor,
            INSERTAR_CORRIDA_0006,
            *(
                {"run_id": uuid4(), "origen": nombre, "firma": "e" * 64, "estado": "EN_PROCESO"}
                for nombre in ("abierta.csv", "otra_vez_abierta.csv")
            ),
        ),
        "ejecucion_decision": _insertar(
            motor, INSERTAR_EJECUCION, _ejecucion(historica), _ejecucion(historica)
        ),
        "ejecucion_territorial": _insertar(
            motor, INSERTAR_TERRITORIAL, _territorial(decision_v2), _territorial(decision_v2)
        ),
        "ejecucion_ruteo": _insertar(
            motor, INSERTAR_RUTEO, _ruteo(territorial), _ruteo(territorial)
        ),
    }
    # Y recursos que ya habian terminado, que la 0006 no toca.
    _insertar(
        motor,
        INSERTAR_CORRIDA_0006,
        {"run_id": uuid4(), "origen": "rechazada.csv", "firma": "f" * 64, "estado": "RECHAZADA"},
        {"run_id": uuid4(), "origen": "fallida.csv", "firma": "0" * 64, "estado": "FALLIDA"},
    )
    _insertar(motor, INSERTAR_EJECUCION, _ejecucion(historica, "FALLIDA"))
    _insertar(motor, INSERTAR_TERRITORIAL, _territorial(decision_v2, "FALLIDA"))
    ruteo, _ = _insertar(
        motor, INSERTAR_RUTEO, _ruteo(territorial, "EXITOSA"), _ruteo(otra, "FALLIDA")
    )
    previas = _por_columna(motor, PREVIAS_A_LA_0006)

    command.upgrade(_alembic(), "0006")
    return BaseEn0006(motor, corrida, decision_v1, territorial, ruteo, abiertas, previas)


def test_los_modelos_de_orquestacion_declaran_el_esquema_de_la_0006():
    # Sin base: lo que declaran los modelos. En el CI, alembic check los compara ademas con la base
    # subida hasta la 0006.
    archivo, flujo, trabajo = tablas = (
        ArchivoCorrida.__table__,
        FlujoOrquestacion.__table__,
        TrabajoOrquestacion.__table__,
    )
    assert [tabla.name for tabla in tablas] == [
        "archivo_corrida",
        "flujo_orquestacion",
        "trabajo_orquestacion",
    ]

    columnas = {columna.name: columna for columna in archivo.columns}
    assert list(columnas) == ["corrida_id", "contenido", "tamano_bytes", "creado_en"]
    assert not any(columna.nullable for columna in archivo.columns)
    # La llave es la corrida misma: un archivo por corrida, y sin secuencia propia.
    assert [columna.name for columna in archivo.primary_key] == ["corrida_id"]
    assert archivo.c.corrida_id.autoincrement == "auto" and archivo.autoincrement_column is None
    assert isinstance(columnas["contenido"].type, LargeBinary)
    assert isinstance(columnas["tamano_bytes"].type, BigInteger)

    columnas = {columna.name: columna for columna in flujo.columns}
    assert list(columnas) == [
        "id",
        "flujo_id",
        "corrida_id",
        "ejecucion_decision_id",
        "ejecucion_territorial_id",
        "ejecucion_ruteo_id",
        "estado",
        "etapa",
        "creado_en",
        "actualizado_en",
        "terminado_en",
        "detalle",
    ]
    assert [nombre for nombre, columna in columnas.items() if columna.nullable] == [
        "ejecucion_decision_id",
        "ejecucion_territorial_id",
        "ejecucion_ruteo_id",
        "terminado_en",
        "detalle",
    ]
    estados = {
        nombre: (columnas[nombre].type.native_enum, columnas[nombre].type.length)
        for nombre in ("estado", "etapa")
    }
    assert estados == {"estado": (False, 12), "etapa": (False, 12)}
    assert columnas["estado"].type.enums == ["EN_PROCESO", "COMPLETADO", "DETENIDO"]
    assert columnas["etapa"].type.enums == [
        "INGESTA",
        "DECISION",
        "TERRITORIAL",
        "RUTEO",
        "COMPLETADA",
    ]

    columnas = {columna.name: columna for columna in trabajo.columns}
    assert list(columnas) == [
        "id",
        "trabajo_id",
        "flujo_id",
        "tipo",
        "estado",
        "corrida_id",
        "ejecucion_decision_id",
        "ejecucion_territorial_id",
        "ejecucion_ruteo_id",
        "intentos",
        "max_intentos",
        "creado_en",
        "disponible_desde",
        "tomado_en",
        "latido_en",
        "lease_hasta",
        "terminado_en",
        "worker_id",
        "ultimo_error",
    ]
    assert [nombre for nombre, columna in columnas.items() if columna.nullable] == [
        "flujo_id",
        "corrida_id",
        "ejecucion_decision_id",
        "ejecucion_territorial_id",
        "ejecucion_ruteo_id",
        "tomado_en",
        "latido_en",
        "lease_hasta",
        "terminado_en",
        "worker_id",
        "ultimo_error",
    ]
    assert columnas["tipo"].type.enums == ["INGESTA", "DECISION", "TERRITORIAL", "RUTEO"]
    assert columnas["estado"].type.enums == ["PENDIENTE", "EJECUTANDO", "COMPLETADO", "FALLIDO"]
    assert {columnas[n].type.native_enum for n in ("tipo", "estado")} == {False}
    assert (columnas["worker_id"].type.length, columnas["ultimo_error"].type.length) == (200, 500)
    # Todos los instantes, con zona horaria.
    instantes = [
        columna
        for tabla in tablas
        for columna in tabla.columns
        if columna.name.endswith("_en") or columna.name in ("disponible_desde", "lease_hasta")
    ]
    assert len(instantes) == 10 and all(columna.type.timezone for columna in instantes)

    # Ningun valor por omision en la base, ni cascadas.
    assert all(columna.server_default is None for tabla in tablas for columna in tabla.columns)
    llaves = {
        (tabla.name, llave.parent.name): (llave.target_fullname, llave.ondelete)
        for tabla in tablas
        for llave in tabla.foreign_keys
    }
    assert llaves == {
        ("archivo_corrida", "corrida_id"): ("corrida.id", None),
        ("flujo_orquestacion", "corrida_id"): ("corrida.id", None),
        ("flujo_orquestacion", "ejecucion_decision_id"): ("ejecucion_decision.id", None),
        ("flujo_orquestacion", "ejecucion_territorial_id"): ("ejecucion_territorial.id", None),
        ("flujo_orquestacion", "ejecucion_ruteo_id"): ("ejecucion_ruteo.id", None),
        ("trabajo_orquestacion", "flujo_id"): ("flujo_orquestacion.id", None),
        ("trabajo_orquestacion", "corrida_id"): ("corrida.id", None),
        ("trabajo_orquestacion", "ejecucion_decision_id"): ("ejecucion_decision.id", None),
        ("trabajo_orquestacion", "ejecucion_territorial_id"): ("ejecucion_territorial.id", None),
        ("trabajo_orquestacion", "ejecucion_ruteo_id"): ("ejecucion_ruteo.id", None),
    }

    # Una corrida, a lo mas un flujo y un trabajo; una ejecucion, a lo mas un flujo y un trabajo. Y
    # los identificadores publicos no se repiten.
    unicas = {
        restriccion.name: [columna.name for columna in restriccion.columns]
        for tabla in (flujo, trabajo)
        for restriccion in tabla.constraints
        if isinstance(restriccion, UniqueConstraint)
    }
    assert unicas == {
        "uq_flujo_orquestacion_flujo_id": ["flujo_id"],
        "uq_trabajo_orquestacion_trabajo_id": ["trabajo_id"],
        "uq_flujo_corrida": ["corrida_id"],
        "uq_flujo_decision": ["ejecucion_decision_id"],
        "uq_flujo_territorial": ["ejecucion_territorial_id"],
        "uq_flujo_ruteo": ["ejecucion_ruteo_id"],
        "uq_trabajo_corrida": ["corrida_id"],
        "uq_trabajo_decision": ["ejecucion_decision_id"],
        "uq_trabajo_territorial": ["ejecucion_territorial_id"],
        "uq_trabajo_ruteo": ["ejecucion_ruteo_id"],
    }
    # Los CHECK estructurales, por nombre, y los de los cuatro catalogos.
    revisiones = {
        restriccion.name
        for tabla in tablas
        for restriccion in tabla.constraints
        if isinstance(restriccion, CheckConstraint)
    }
    assert revisiones == {
        "ck_archivo_tamano_positivo",
        "ck_archivo_tamano_exacto",
        "ck_flujo_cadena",
        "ck_flujo_completado",
        "ck_flujo_terminado",
        "ck_flujo_orquestacion_estado_flujo",
        "ck_flujo_orquestacion_etapa_flujo",
        "ck_trabajo_objetivo",
        "ck_trabajo_lease",
        "ck_trabajo_intentos",
        "ck_trabajo_orquestacion_tipo_trabajo",
        "ck_trabajo_orquestacion_estado_trabajo",
    }
    indices = {
        indice.name: (
            [c.name for c in indice.columns],
            indice.unique,
            str(indice.dialect_options["postgresql"]["where"]),
        )
        for tabla in tablas
        for indice in tabla.indexes
    }
    assert indices == {
        "ix_trabajo_orquestacion_flujo_id": (["flujo_id"], False, "None"),
        "ix_trabajo_reclamable": (
            ["estado", "disponible_desde", "lease_hasta", "id"],
            False,
            "estado IN ('PENDIENTE', 'EJECUTANDO')",
        ),
    }

    # Los nombres con que quedan en PostgreSQL son los que las pruebas de abajo esperan en la base,
    # tal cual: ninguno pasa de 63 caracteres, asi que nada se recorta.
    preparador = postgresql.dialect().identifier_preparer
    nombres = {preparador.format_constraint(c) for t in tablas for c in t.constraints} | {
        preparador.format_index(i) for t in tablas for i in t.indexes
    }
    assert nombres == RESTRICCIONES_DE_ORQUESTACION | INDICES_DE_ORQUESTACION
    assert all(len(nombre) <= 63 for nombre in nombres)


def test_la_0006_sube_sobre_la_0005():
    # Sin base: la 0006 sube sobre la 0005. Despues de ella viene la 0007.
    scripts = ScriptDirectory.from_config(_alembic())

    assert scripts.get_revision("0006").down_revision == "0005"


def test_la_0006_cierra_fallida_lo_que_estaba_en_proceso_y_no_toca_nada_mas(base_en_0006):
    motor = base_en_0006.motor
    cierres = _cierres()

    # A. Cada fila de antes sigue ahi. Las que estaban EN_PROCESO quedan FALLIDA, con su fin y el
    # motivo, y nada mas les cambia; las demas, EXITOSA, RECHAZADA o FALLIDA, quedan identicas. No
    # se borra ningun resultado.
    despues = _por_columna(motor, PREVIAS_A_LA_0006)
    cerradas = {tabla: [] for tabla in RECURSOS}
    for tabla, filas in base_en_0006.previas.items():
        assert len(despues[tabla]) == len(filas), tabla
        for antes, fila in zip(filas, despues[tabla], strict=True):
            if tabla in RECURSOS and antes["estado"] == "EN_PROCESO":
                fila, antes = dict(fila), dict(antes)
                assert (fila.pop("estado"), fila.pop("detalle")) == ("FALLIDA", cierres[tabla])
                assert fila.pop("terminada_en") is not None and antes.pop("terminada_en") is None
                del antes["estado"], antes["detalle"]
                cerradas[tabla].append(fila["id"])
            assert fila == antes, tabla
    assert cerradas == base_en_0006.abiertas
    # Ya no queda ningun EN_PROCESO, y los motivos dicen que se puede reintentar.
    for tabla in RECURSOS:
        consulta = f"SELECT count(*) FROM {tabla} WHERE estado = 'EN_PROCESO'"
        assert _consultar(motor, consulta) == [0]
        assert cierres[tabla].startswith("Cerrada al migrar a la orquestacion durable de v0.5.0")
        assert "reintentar" in cierres[tabla] or "volver a subir" in cierres[tabla]


def test_la_0006_agrega_las_tablas_de_orquestacion_vacias_y_con_sus_tipos(base_en_0006):
    motor = base_en_0006.motor

    # B. Las tablas nuevas existen, vacias, con sus restricciones y sus indices, por nombre.
    assert TABLAS_DE_ORQUESTACION <= set(_consultar(motor, TABLAS))
    for tabla in sorted(TABLAS_DE_ORQUESTACION):
        assert _consultar(motor, f"SELECT count(*) FROM {tabla}") == [0]
    assert set(_consultar(motor, RESTRICCIONES_0006)) == RESTRICCIONES_DE_ORQUESTACION
    with motor.connect() as conexion:
        indices = {fila[1]: fila for fila in conexion.execute(text(INDICES_0006)).all()}
        columnas = {
            (tabla, columna): (tipo, largo, nulo)
            for tabla, columna, tipo, largo, nulo in conexion.execute(
                text(
                    "SELECT table_name, column_name, data_type, character_maximum_length, "
                    "is_nullable FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND table_name IN "
                    "('archivo_corrida', 'flujo_orquestacion', 'trabajo_orquestacion')"
                )
            )
        }
    assert INDICES_DE_ORQUESTACION <= set(indices)
    _, _, unico, en_orden, predicado = indices["ix_trabajo_reclamable"]
    assert (unico, en_orden) == (False, ["estado", "disponible_desde", "lease_hasta", "id"])
    assert "'PENDIENTE'" in predicado and "'EJECUTANDO'" in predicado

    # El archivo es BYTEA y su tamano BIGINT: un archivo de mas de 2 GiB no le cabria a INTEGER.
    assert columnas[("archivo_corrida", "contenido")] == ("bytea", None, "NO")
    assert columnas[("archivo_corrida", "tamano_bytes")] == ("bigint", None, "NO")
    # Los estados son VARCHAR con CHECK y no tipos de PostgreSQL: no hay tipo que migrar ni borrar.
    for tabla, columna in (
        ("flujo_orquestacion", "estado"),
        ("flujo_orquestacion", "etapa"),
        ("trabajo_orquestacion", "tipo"),
        ("trabajo_orquestacion", "estado"),
    ):
        assert columnas[(tabla, columna)] == ("character varying", 12, "NO")
    for tipo in ("estado_flujo", "etapa_flujo", "tipo_trabajo", "estado_trabajo"):
        assert _consultar(motor, f"SELECT count(*) FROM pg_type WHERE typname = '{tipo}'") == [0]
    # PostgreSQL revisa los CHECK en orden alfabetico: estos valores solo violan el del catalogo.
    _rechaza(
        motor,
        "ck_trabajo_orquestacion_tipo_trabajo",
        INSERTAR_TRABAJO,
        _trabajo("ENTREGA", None),
    )
    _rechaza(
        motor,
        "ck_flujo_orquestacion_estado_flujo",
        INSERTAR_FLUJO,
        _flujo(base_en_0006.corrida_id, estado="PAUSADO"),
    )
    # Los instantes de la cola, con zona horaria; los intentos, enteros.
    for columna in ("creado_en", "disponible_desde", "tomado_en", "latido_en", "lease_hasta"):
        assert columnas[("trabajo_orquestacion", columna)][0] == "timestamp with time zone"
    assert columnas[("trabajo_orquestacion", "intentos")] == ("integer", None, "NO")


def test_la_0006_admite_un_solo_intento_activo_y_conserva_las_publicaciones_unicas(base_en_0006):
    b = base_en_0006
    motor = b.motor
    with motor.connect() as conexion:
        indices = {fila[1]: fila for fila in conexion.execute(text(INDICES_DE_RECURSOS)).all()}

    # C. Cada recurso tiene su indice de las EN_PROCESO, y conserva el de las EXITOSA.
    for nombre, (tabla, columnas, estado) in UNICOS_POR_ESTADO.items():
        en_tabla, _, unico, en_orden, predicado = indices[nombre]
        assert (en_tabla, unico, en_orden) == (tabla, True, columnas), nombre
        assert f"'{estado}'" in predicado, nombre

    # Una corrida activa por archivo; la FALLIDA no cuenta, y la EXITOSA sigue siendo una.
    firma = uuid4().hex * 2

    def corrida(estado: str, archivo: str = firma) -> dict:
        return {"run_id": uuid4(), "origen": "c.csv", "firma": archivo, "estado": estado}

    _insertar(motor, INSERTAR_CORRIDA_0006, corrida("EN_PROCESO"), corrida("FALLIDA"))
    _rechaza(motor, "ux_corrida_firma_en_proceso", INSERTAR_CORRIDA_0006, corrida("EN_PROCESO"))
    _rechaza(
        motor, "ux_corrida_firma_publicada", INSERTAR_CORRIDA_0006, corrida("EXITOSA", "b" * 64)
    )

    # Una ejecucion activa por fuente y version: conviven con las FALLIDA que haya, y otra version
    # si abre la suya. Y la publicacion sigue siendo una por fuente y version: cada fuente ya tiene
    # su EXITOSA.
    for sentencia, valores, fuente, en_proceso, exitosa in (
        (
            INSERTAR_EJECUCION,
            _ejecucion,
            b.corrida_id,
            "ux_ejecucion_decision_en_proceso",
            "ux_ejecucion_decision_exitosa",
        ),
        (
            INSERTAR_TERRITORIAL,
            _territorial,
            b.decision_id,
            "ux_ejecucion_territorial_en_proceso",
            "ux_ejecucion_territorial_exitosa",
        ),
        (
            INSERTAR_RUTEO,
            _ruteo,
            b.territorial_id,
            "ux_ejecucion_ruteo_en_proceso",
            "ux_ejecucion_ruteo_exitosa",
        ),
    ):
        _insertar(
            motor,
            sentencia,
            valores(fuente),
            valores(fuente, "FALLIDA"),
            valores(fuente, "FALLIDA"),
        )
        _rechaza(motor, en_proceso, sentencia, valores(fuente))
        version = valores(fuente)["version_reglas"].replace("/v1", "/v9")
        _insertar(motor, sentencia, valores(fuente, version=version))
        _rechaza(motor, exitosa, sentencia, valores(fuente, "EXITOSA"))


def test_el_flujo_apunta_a_las_ejecuciones_de_su_etapa_y_a_ninguna_mas(base_en_0006):
    b = base_en_0006
    motor = b.motor
    decision, territorial, ruteo = b.decision_id, b.territorial_id, b.ruteo_id
    corrida, otra, tercera = _corridas(motor, "EXITOSA", "EXITOSA", "EXITOSA")

    # D. La cadena: cada etapa apunta a la ejecucion de las anteriores y a la suya, y a ninguna
    # despues. Un flujo sin etapa que la respalde no entra.
    (flujo,) = _insertar(motor, INSERTAR_FLUJO, _flujo(corrida))
    invalidos = [
        _flujo(otra, decision=decision),
        _flujo(otra, "DECISION"),
        _flujo(otra, "DECISION", decision=decision, territorial=territorial),
        _flujo(otra, "TERRITORIAL", decision=decision),
        _flujo(otra, "TERRITORIAL", territorial=territorial),
        _flujo(otra, "RUTEO", decision=decision, territorial=territorial),
        _flujo(otra, "RUTEO", decision=decision, ruteo=ruteo),
        _flujo(otra, "COMPLETADA", "COMPLETADO", territorial=territorial, ruteo=ruteo),
    ]
    for valores in invalidos:
        _rechaza(motor, "ck_flujo_cadena", INSERTAR_FLUJO, valores)

    # El flujo avanza etapa por etapa hasta COMPLETADO, siempre por un camino valido.
    pasos = [
        ("etapa = 'DECISION', ejecucion_decision_id = :decision", {"decision": decision}),
        ("etapa = 'TERRITORIAL', ejecucion_territorial_id = :t", {"t": territorial}),
        ("etapa = 'RUTEO', ejecucion_ruteo_id = :ruteo", {"ruteo": ruteo}),
        ("etapa = 'COMPLETADA', estado = 'COMPLETADO', terminado_en = now()", {}),
    ]
    with motor.begin() as conexion:
        for cambio, parametros in pasos:
            conexion.execute(
                text(f"UPDATE flujo_orquestacion SET {cambio} WHERE id = :id"),
                {"id": flujo, **parametros},
            )

    # COMPLETADO es haber llegado a COMPLETADA, y al reves; y solo un flujo que ya no avanza tiene
    # fin. Un DETENIDO conserva la etapa en que se detuvo.
    todas = {"decision": decision, "territorial": territorial, "ruteo": ruteo}
    _rechaza(
        motor,
        "ck_flujo_completado",
        INSERTAR_FLUJO,
        _flujo(otra, "COMPLETADA", "DETENIDO", **todas),
    )
    _rechaza(motor, "ck_flujo_completado", INSERTAR_FLUJO, _flujo(otra, estado="COMPLETADO"))
    _rechaza(
        motor,
        "ck_flujo_terminado",
        INSERTAR_FLUJO,
        _flujo(otra, estado="DETENIDO", terminado_en=None),
    )
    _rechaza(
        motor,
        "ck_flujo_terminado",
        INSERTAR_FLUJO,
        _flujo(otra, terminado_en=datetime.now(UTC)),
    )
    _insertar(motor, INSERTAR_FLUJO, _flujo(otra, estado="DETENIDO"))

    # Una corrida tiene a lo mas un flujo, y una ejecucion es de a lo mas un flujo. Los punteros
    # vacios de las etapas a las que no se ha llegado no chocan entre si.
    _rechaza(motor, "uq_flujo_corrida", INSERTAR_FLUJO, _flujo(corrida))
    _rechaza(
        motor, "uq_flujo_decision", INSERTAR_FLUJO, _flujo(tercera, "DECISION", decision=decision)
    )
    _insertar(motor, INSERTAR_FLUJO, _flujo(tercera))
    with motor.connect() as conexion:
        flujos = conexion.execute(
            text(
                "SELECT estado, etapa, ejecucion_decision_id, ejecucion_territorial_id, "
                "ejecucion_ruteo_id, terminado_en IS NOT NULL FROM flujo_orquestacion ORDER BY id"
            )
        ).all()
    assert [tuple(fila) for fila in flujos] == [
        ("COMPLETADO", "COMPLETADA", decision, territorial, ruteo, True),
        ("DETENIDO", "INGESTA", None, None, None, True),
        ("EN_PROCESO", "INGESTA", None, None, None, False),
    ]


def test_un_trabajo_tiene_un_solo_objetivo_el_de_su_tipo_y_uno_por_recurso(base_en_0006):
    b = base_en_0006
    motor = b.motor
    (corrida,) = _corridas(motor, "EN_PROCESO")

    # E. Uno de cada tipo, cada uno con su objetivo.
    _insertar(
        motor,
        INSERTAR_TRABAJO,
        _trabajo("INGESTA", corrida),
        _trabajo("DECISION", b.decision_id),
        _trabajo("TERRITORIAL", b.territorial_id),
        _trabajo("RUTEO", b.ruteo_id),
    )
    # Sin objetivo, con dos, o con el de otro tipo, no entra.
    (otra,) = _corridas(motor, "EN_PROCESO")
    for valores in (
        _trabajo("INGESTA", None),
        _trabajo("INGESTA", otra, decision=b.decision_id),
        _trabajo("DECISION", None, corrida_id=otra),
        _trabajo("RUTEO", None, territorial=b.territorial_id),
        _trabajo("TERRITORIAL", b.territorial_id, ruteo=b.ruteo_id),
    ):
        _rechaza(motor, "ck_trabajo_objetivo", INSERTAR_TRABAJO, valores)

    # Un recurso, un trabajo: una nueva entrega reusa la fila, no crea otra.
    for restriccion, valores in (
        ("uq_trabajo_corrida", _trabajo("INGESTA", corrida)),
        ("uq_trabajo_decision", _trabajo("DECISION", b.decision_id)),
        ("uq_trabajo_territorial", _trabajo("TERRITORIAL", b.territorial_id)),
        ("uq_trabajo_ruteo", _trabajo("RUTEO", b.ruteo_id)),
    ):
        _rechaza(motor, restriccion, INSERTAR_TRABAJO, valores)
    assert _consultar(motor, "SELECT count(*) FROM trabajo_orquestacion") == [4]


def test_el_estado_de_un_trabajo_fija_su_dueno_su_lease_y_su_fin(base_en_0006):
    motor = base_en_0006.motor
    corridas = iter(_corridas(motor, *["EN_PROCESO"] * 8))

    # F. PENDIENTE sin dueno ni lease ni fin; EJECUTANDO con dueno y lease, sin fin; COMPLETADO y
    # FALLIDO con fin, sin dueno ni lease. Tomado y latido se conservan para auditoria.
    validos = [
        _trabajo("INGESTA", next(corridas)),
        _trabajo("INGESTA", next(corridas), "EJECUTANDO"),
        _trabajo("INGESTA", next(corridas), "COMPLETADO"),
        _trabajo("INGESTA", next(corridas), "FALLIDO", intentos=5),
    ]
    _insertar(motor, INSERTAR_TRABAJO, *validos)
    otra = next(corridas)
    ahora = datetime.now(UTC)
    for valores in (
        _trabajo("INGESTA", otra, worker_id="worker-de-prueba:1:abc"),
        _trabajo("INGESTA", otra, lease_hasta=ahora),
        _trabajo("INGESTA", otra, terminado_en=ahora),
        _trabajo("INGESTA", otra, "EJECUTANDO", worker_id=None),
        _trabajo("INGESTA", otra, "EJECUTANDO", lease_hasta=None),
        _trabajo("INGESTA", otra, "EJECUTANDO", terminado_en=ahora),
        _trabajo("INGESTA", otra, "COMPLETADO", terminado_en=None),
        _trabajo("INGESTA", otra, "COMPLETADO", worker_id="worker-de-prueba:1:abc"),
        _trabajo("INGESTA", otra, "FALLIDO", lease_hasta=ahora),
    ):
        _rechaza(motor, "ck_trabajo_lease", INSERTAR_TRABAJO, valores)

    # Los intentos no son negativos, ni pasan del maximo, que es al menos uno.
    for valores in (
        _trabajo("INGESTA", otra, intentos=-1),
        _trabajo("INGESTA", otra, max_intentos=0),
        _trabajo("INGESTA", otra, intentos=6, max_intentos=5),
    ):
        _rechaza(motor, "ck_trabajo_intentos", INSERTAR_TRABAJO, valores)
    _insertar(motor, INSERTAR_TRABAJO, _trabajo("INGESTA", otra, intentos=1, max_intentos=1))

    with motor.connect() as conexion:
        estados = conexion.execute(
            text(
                "SELECT estado, worker_id IS NOT NULL, lease_hasta IS NOT NULL, "
                "terminado_en IS NOT NULL FROM trabajo_orquestacion ORDER BY id"
            )
        ).all()
    assert [tuple(fila) for fila in estados] == [
        ("PENDIENTE", False, False, False),
        ("EJECUTANDO", True, True, False),
        ("COMPLETADO", False, False, True),
        ("FALLIDO", False, False, True),
        ("PENDIENTE", False, False, False),
    ]


def test_el_archivo_de_una_corrida_se_guarda_byte_por_byte(base_en_0006):
    motor = base_en_0006.motor
    corrida, otra = _corridas(motor, "EN_PROCESO", "EN_PROCESO")
    # Bytes de un xlsx, de un zip o de cualquier codificacion: ceros, bytes altos y UTF-8.
    contenido = b"PK\x03\x04\x00\xff\xfe" + b"cartera de credito" + bytes(range(256))

    _insertar(
        motor,
        INSERTAR_ARCHIVO,
        {"corrida_id": corrida, "contenido": contenido, "tamano_bytes": len(contenido)},
    )

    with motor.connect() as conexion:
        guardado, tamano = conexion.execute(
            text("SELECT contenido, tamano_bytes FROM archivo_corrida WHERE corrida_id = :id"),
            {"id": corrida},
        ).one()
    assert (bytes(guardado), tamano) == (contenido, len(contenido))
    # G. Un archivo por corrida; con al menos un byte, y con el tamano de lo que de verdad guarda.
    _rechaza(
        motor,
        "pk_archivo_corrida",
        INSERTAR_ARCHIVO,
        {"corrida_id": corrida, "contenido": b"x", "tamano_bytes": 1},
    )
    _rechaza(
        motor,
        "ck_archivo_tamano_positivo",
        INSERTAR_ARCHIVO,
        {"corrida_id": otra, "contenido": b"", "tamano_bytes": 0},
    )
    _rechaza(
        motor,
        "ck_archivo_tamano_exacto",
        INSERTAR_ARCHIVO,
        {"corrida_id": otra, "contenido": b"abc", "tamano_bytes": 5},
    )
    _rechaza(
        motor,
        "fk_archivo_corrida_corrida_id_corrida",
        INSERTAR_ARCHIVO,
        {"corrida_id": 999_999, "contenido": b"abc", "tamano_bytes": 3},
    )


def test_nada_de_la_orquestacion_apunta_a_lo_que_no_existe_ni_se_borra_en_cascada(base_en_0006):
    b = base_en_0006
    motor = b.motor
    no_existe = 999_999

    # H. Ninguna llave borra en cascada: todas son NO ACTION.
    with motor.connect() as conexion:
        llaves = dict(
            conexion.execute(
                text(
                    "SELECT c.conname, c.confdeltype FROM pg_constraint c "
                    "JOIN pg_class t ON t.oid = c.conrelid WHERE c.contype = 'f' AND t.relname IN "
                    "('archivo_corrida', 'flujo_orquestacion', 'trabajo_orquestacion')"
                )
            ).all()
        )
    assert set(llaves) == {nombre for nombre in RESTRICCIONES_DE_ORQUESTACION if "fk_" in nombre}
    assert set(llaves.values()) == {"a"}

    # Nada apunta a lo que no existe.
    _rechaza(motor, "fk_flujo_corrida", INSERTAR_FLUJO, _flujo(no_existe))
    _rechaza(motor, "fk_trabajo_corrida", INSERTAR_TRABAJO, _trabajo("INGESTA", no_existe))
    _rechaza(motor, "fk_trabajo_ruteo", INSERTAR_TRABAJO, _trabajo("RUTEO", no_existe))
    (corrida,) = _corridas(motor, "EN_PROCESO")
    _rechaza(
        motor,
        "fk_trabajo_flujo",
        INSERTAR_TRABAJO,
        _trabajo("INGESTA", corrida, flujo_id=no_existe),
    )

    # Y borrar algo de lo que cuelga la orquestacion falla, en lugar de llevarse su rastro. Cada
    # fila que se intenta borrar cuelga de una sola llave, para que el borrado choque con esa: la
    # base no exige que el trabajo de un flujo sea de su misma corrida, eso lo cuida el servicio.
    con_archivo, con_flujo, con_trabajo, otra = _corridas(
        motor, "EN_PROCESO", "EXITOSA", "EN_PROCESO", "EXITOSA"
    )
    _insertar(
        motor,
        INSERTAR_ARCHIVO,
        {"corrida_id": con_archivo, "contenido": b"abc", "tamano_bytes": 3},
    )
    (flujo,) = _insertar(
        motor,
        INSERTAR_FLUJO,
        _flujo(
            con_flujo,
            "RUTEO",
            decision=b.decision_id,
            territorial=b.territorial_id,
            ruteo=b.ruteo_id,
        ),
    )
    _insertar(motor, INSERTAR_TRABAJO, _trabajo("INGESTA", con_trabajo, flujo_id=flujo))
    (sola,) = _insertar(motor, INSERTAR_EJECUCION, _ejecucion(otra, "FALLIDA"))
    _insertar(motor, INSERTAR_FLUJO, _flujo(otra, "DECISION", "DETENIDO", decision=sola))
    for restriccion, tabla, fila in (
        ("fk_archivo_corrida_corrida_id_corrida", "corrida", con_archivo),
        ("fk_flujo_corrida", "corrida", con_flujo),
        ("fk_trabajo_corrida", "corrida", con_trabajo),
        ("fk_trabajo_flujo", "flujo_orquestacion", flujo),
        ("fk_flujo_decision", "ejecucion_decision", sola),
        ("fk_flujo_ruteo", "ejecucion_ruteo", b.ruteo_id),
    ):
        _rechaza(motor, restriccion, text(f"DELETE FROM {tabla} WHERE id = :id"), {"id": fila})


def test_la_0006_baja_sin_reabrir_lo_que_cerro_y_vuelve_a_subir(base_en_0006):
    b = base_en_0006
    motor = b.motor
    # Con orquestacion ya registrada: un archivo, un flujo y su trabajo.
    (corrida,) = _corridas(motor, "EN_PROCESO")
    _insertar(
        motor, INSERTAR_ARCHIVO, {"corrida_id": corrida, "contenido": b"abc", "tamano_bytes": 3}
    )
    (flujo,) = _insertar(motor, INSERTAR_FLUJO, _flujo(corrida))
    _insertar(motor, INSERTAR_TRABAJO, _trabajo("INGESTA", corrida, flujo_id=flujo))
    recursos = _por_columna(motor, RECURSOS)

    command.downgrade(_alembic(), "0005")

    # I. Bajar quita la orquestacion, con sus datos, y los indices de las EN_PROCESO; los de las
    # EXITOSA se quedan.
    tablas = set(_consultar(motor, TABLAS))
    assert not TABLAS_DE_ORQUESTACION & tablas
    assert set(PREVIAS_A_LA_0006) <= tablas
    with motor.connect() as conexion:
        indices = {fila[1] for fila in conexion.execute(text(INDICES_DE_RECURSOS)).all()}
    en_proceso = {nombre for nombre in UNICOS_POR_ESTADO if nombre.endswith("_en_proceso")}
    assert not en_proceso & indices
    assert set(UNICOS_POR_ESTADO) - en_proceso <= indices
    for tipo in ("estado_flujo", "etapa_flujo", "tipo_trabajo", "estado_trabajo"):
        assert _consultar(motor, f"SELECT count(*) FROM pg_type WHERE typname = '{tipo}'") == [0]
    # Lo que la 0006 cerro FALLIDA se queda FALLIDA: el cierre no se revierte, porque el dueno de
    # aquellos EN_PROCESO nunca existio. Lo que se registro despues, tampoco se toca.
    assert _por_columna(motor, RECURSOS) == recursos
    assert _consultar(motor, "SELECT version_num FROM alembic_version") == ["0005"]

    # Y la 0006 vuelve a subir limpia sobre la misma base, sin lo que se fue al bajar. La corrida
    # que seguia EN_PROCESO, ya sin su trabajo, se cierra como las de la v0.4.0.
    command.upgrade(_alembic(), "0006")

    assert TABLAS_DE_ORQUESTACION <= set(_consultar(motor, TABLAS))
    assert set(_consultar(motor, RESTRICCIONES_0006)) == RESTRICCIONES_DE_ORQUESTACION
    for tabla in sorted(TABLAS_DE_ORQUESTACION):
        assert _consultar(motor, f"SELECT count(*) FROM {tabla}") == [0]
    despues = _por_columna(motor, RECURSOS)
    (huerfana,) = [fila for fila in despues["corrida"] if fila["id"] == corrida]
    assert (huerfana["estado"], huerfana["detalle"]) == ("FALLIDA", _cierres()["corrida"])
    for filas in (despues, recursos):
        filas["corrida"] = [fila for fila in filas["corrida"] if fila["id"] != corrida]
    assert despues == recursos
    assert _consultar(motor, "SELECT version_num FROM alembic_version") == ["0006"]


def test_desde_cero_hasta_la_cabeza_alembic_check_no_ve_diferencias(base_en_0001):
    # Lo mismo que el paso de migraciones del CI, dentro de las pruebas: el esquema que dejan las
    # migraciones, la 0007 incluida, es el que declaran los modelos.
    command.upgrade(_alembic(), "head")
    salida = io.StringIO()
    cfg = ConfigAlembic(str(RAIZ / "alembic.ini"), stdout=salida)
    cfg.set_main_option("script_location", str(RAIZ / "migraciones"))

    command.check(cfg)  # con cualquier diferencia levantaria AutogenerateDiffsDetected

    assert salida.getvalue().strip() == "No new upgrade operations detected."
    assert _consultar(base_en_0001, "SELECT version_num FROM alembic_version") == ["0007"]


# --- la 0007: fuentes oficiales y evidencia inmutable --------------------------------------------

INSERTAR_ARTEFACTO = text(
    "INSERT INTO artefacto_fuente (artifact_id, sha256, tamano_bytes, nombre_original, formato, "
    "media_type, storage_key, creado_en) VALUES (:artifact_id, :sha256, :tamano_bytes, "
    ":nombre_original, :formato, :media_type, :storage_key, now()) RETURNING id"
)
INSERTAR_CORRIDA_0007 = text(
    "INSERT INTO corrida (run_id, iniciada_en, origen, firma, estado, tolerancia_rechazo, "
    "version_contrato, filas_leidas, filas_validas, filas_rechazadas, artefacto_fuente_id, "
    "despacho_id, cartera_id) VALUES (:run_id, now(), :origen, :firma, :estado, 0.05, "
    "'cartera/v1', 0, 0, 0, :artefacto_fuente_id, :despacho_id, :cartera_id) RETURNING id"
)

TABLAS_DE_FUENTES = {"artefacto_fuente"}
# Las tablas que existen antes de la 0007, que se comparan completas, columna por columna.
PREVIAS_A_LA_0007 = (*PREVIAS_A_LA_0006, "flujo_orquestacion", "trabajo_orquestacion")
COLUMNAS_NUEVAS_DE_LA_CORRIDA = {"artefacto_fuente_id", "despacho_id", "cartera_id"}

RESTRICCIONES_0007 = RESTRICCIONES.replace(
    "'ejecucion_decision', 'decision_cuenta'", "'artefacto_fuente'"
)
RESTRICCIONES_DE_FUENTES = {
    "pk_artefacto_fuente",
    "uq_artefacto_fuente_artifact_id",
    "uq_artefacto_fuente_sha256",
    "uq_artefacto_fuente_storage_key",
    "ck_artefacto_sha256",
    "ck_artefacto_tamano_positivo",
    "ck_artefacto_storage_key",
    "ck_artefacto_fuente_formato_artefacto",
}
FK_CORRIDA_ARTEFACTO = "fk_corrida_artefacto_fuente_id_artefacto_fuente"


def _cierre_0007() -> str:
    """El motivo con que bajar la 0007 cierra lo que la v0.5 no podria terminar."""
    return ScriptDirectory.from_config(_alembic()).get_revision("0007").module.CIERRE


def _artefacto(contenido: bytes = b"cliente,saldo\n", **cambios) -> dict:
    """Un artefacto valido de ese contenido; `cambios` reemplaza cualquier valor."""
    sha256 = hashlib.sha256(contenido).hexdigest()
    return {
        "artifact_id": uuid4(),
        "sha256": sha256,
        "tamano_bytes": len(contenido),
        "nombre_original": "cartera.csv",
        "formato": "csv",
        "media_type": "text/csv",
        "storage_key": f"sha256/{sha256[:2]}/{sha256}",
        **cambios,
    }


def _corrida_0007(estado: str, artefacto_id: int | None, **cambios) -> dict:
    return {
        "run_id": uuid4(),
        "origen": "cartera.csv",
        "firma": uuid4().hex * 2,
        "estado": estado,
        "artefacto_fuente_id": artefacto_id,
        "despacho_id": "DSP_001",
        "cartera_id": "CARTERA_PRINCIPAL",
        **cambios,
    }


def _archivos(motor: Engine) -> list[dict]:
    """archivo_corrida completa: su llave es corrida_id, no id."""
    with motor.connect() as conexion:
        consulta = "SELECT * FROM archivo_corrida ORDER BY corrida_id"
        return [dict(fila) for fila in conexion.execute(text(consulta)).mappings()]


@dataclass(frozen=True)
class BaseEn0007:
    """Una base con todo lo de la 0006 y una corrida de v0.5 en la cola, subida hasta la 0007."""

    motor: Engine
    heredada_id: int
    """Una corrida EN_PROCESO con su archivo en BYTEA, su flujo y su trabajo PENDIENTE: la v0.6 la
    tiene que poder terminar con ese archivo."""
    previas: dict[str, list[dict]]
    """Las tablas anteriores a la 0007, completas, justo antes de subir."""
    archivos: list[dict]


@pytest.fixture
def base_en_0007(base_en_0006) -> BaseEn0007:
    motor = base_en_0006.motor
    (heredada,) = _corridas(motor, "EN_PROCESO")
    _insertar(
        motor,
        INSERTAR_ARCHIVO,
        {"corrida_id": heredada, "contenido": b"cliente,saldo\n", "tamano_bytes": 14},
    )
    (flujo,) = _insertar(motor, INSERTAR_FLUJO, _flujo(heredada))
    _insertar(motor, INSERTAR_TRABAJO, _trabajo("INGESTA", heredada, flujo_id=flujo))
    previas = _por_columna(motor, PREVIAS_A_LA_0007)
    archivos = _archivos(motor)

    command.upgrade(_alembic(), "0007")
    return BaseEn0007(motor, heredada, previas, archivos)


def test_la_cabeza_es_la_0007():
    # Sin base: la 0007 sube sobre la 0006, y no hay nada despues de ella.
    scripts = ScriptDirectory.from_config(_alembic())

    assert scripts.get_heads() == ["0007"]
    assert scripts.get_revision("0007").down_revision == "0006"


def test_los_modelos_declaran_el_esquema_de_la_0007():
    # Sin base: lo que declaran los modelos. En el CI, alembic check los compara ademas con la base.
    tabla = ArtefactoFuente.__table__
    columnas = {c.name: (type(c.type), c.nullable) for c in tabla.columns}
    assert columnas["tamano_bytes"] == (BigInteger, False)
    assert columnas["sha256"][1] is False and columnas["storage_key"][1] is False
    assert columnas["media_type"][1] is True
    assert tabla.c.formato.type.length == 12
    assert set(tabla.c.formato.type.enums) == {"xlsx", "csv", "zip", "parquet"}
    preparador = postgresql.dialect().identifier_preparer
    nombres = {preparador.format_constraint(c) for c in tabla.constraints}
    assert nombres == RESTRICCIONES_DE_FUENTES
    # La corrida apunta a su artefacto, y registra el despacho y la cartera del sistema.
    corrida = Corrida.__table__
    assert corrida.c.artefacto_fuente_id.nullable is True
    assert (corrida.c.despacho_id.nullable, corrida.c.cartera_id.nullable) == (False, False)
    llaves = {preparador.format_constraint(c) for c in corrida.foreign_key_constraints}
    assert FK_CORRIDA_ARTEFACTO in llaves
    assert all(len(nombre) <= 63 for nombre in nombres | llaves)


def test_la_0007_agrega_el_artefacto_fuente_y_no_toca_lo_que_ya_estaba(base_en_0007):
    b = base_en_0007
    motor = b.motor

    # A. Cada fila de antes sigue ahi, identica. La corrida solo gana tres columnas: sin artefacto,
    # porque su archivo no se conservo, y con el despacho y la cartera del sistema.
    despues = _por_columna(motor, PREVIAS_A_LA_0007)
    for tabla, filas in b.previas.items():
        assert len(despues[tabla]) == len(filas), tabla
        for antes, fila in zip(filas, despues[tabla], strict=True):
            fila = dict(fila)
            if tabla == "corrida":
                nuevas = {columna: fila.pop(columna) for columna in COLUMNAS_NUEVAS_DE_LA_CORRIDA}
                assert nuevas == {
                    "artefacto_fuente_id": None,
                    "despacho_id": "DSP_001",
                    "cartera_id": "CARTERA_PRINCIPAL",
                }
            assert fila == antes, tabla
    # El archivo de la corrida que seguia en la cola no se toca: la v0.6 lo va a leer.
    assert _archivos(motor) == b.archivos

    # B. La tabla nueva, vacia, con sus restricciones por nombre.
    assert TABLAS_DE_FUENTES <= set(_consultar(motor, TABLAS))
    assert _consultar(motor, "SELECT count(*) FROM artefacto_fuente") == [0]
    assert set(_consultar(motor, RESTRICCIONES_0007)) == RESTRICCIONES_DE_FUENTES
    with motor.connect() as conexion:
        columnas = {
            columna: (tipo, largo, nulo, omision)
            for columna, tipo, largo, nulo, omision in conexion.execute(
                text(
                    "SELECT column_name, data_type, character_maximum_length, is_nullable, "
                    "column_default FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND table_name = 'corrida' "
                    "AND column_name IN ('artefacto_fuente_id', 'despacho_id', 'cartera_id')"
                )
            )
        }
    # Sin valor por omision en la base: cada corrida nueva trae el suyo.
    assert columnas == {
        "artefacto_fuente_id": ("integer", None, "YES", None),
        "despacho_id": ("character varying", 32, "NO", None),
        "cartera_id": ("character varying", 32, "NO", None),
    }


def test_un_artefacto_se_identifica_por_su_contenido(base_en_0007):
    motor = base_en_0007.motor
    (primero,) = _insertar(motor, INSERTAR_ARTEFACTO, _artefacto())

    # C. Un contenido, una fila: el SHA-256, la storage_key y el artifact_id son unicos.
    _rechaza(motor, "uq_artefacto_fuente_sha256", INSERTAR_ARTEFACTO, _artefacto())
    otro = _artefacto(b"otro contenido")
    _rechaza(
        motor,
        "uq_artefacto_fuente_artifact_id",
        INSERTAR_ARTEFACTO,
        {**otro, "artifact_id": _consultar(motor, "SELECT artifact_id FROM artefacto_fuente")[0]},
    )
    # El SHA-256 es hexadecimal en minusculas; el tamano, positivo; la storage_key sale del SHA-256
    # y nunca del nombre; el formato es uno de los que el almacen guarda.
    for restriccion, cambios in (
        ("ck_artefacto_sha256", {"sha256": otro["sha256"].upper()}),
        ("ck_artefacto_tamano_positivo", {"tamano_bytes": 0}),
        ("ck_artefacto_storage_key", {"storage_key": "cartera.csv"}),
        ("ck_artefacto_fuente_formato_artefacto", {"formato": "pdf"}),
    ):
        _rechaza(motor, restriccion, INSERTAR_ARTEFACTO, {**otro, **cambios})
    _insertar(motor, INSERTAR_ARTEFACTO, _artefacto(b"y un parquet", formato="parquet"))
    assert _consultar(motor, "SELECT id FROM artefacto_fuente ORDER BY id")[0] == primero


def test_una_corrida_apunta_a_su_artefacto_sin_cascada(base_en_0007):
    motor = base_en_0007.motor
    (artefacto,) = _insertar(motor, INSERTAR_ARTEFACTO, _artefacto())

    # D. La corrida apunta a un artefacto que existe; dos corridas, al mismo; y el artefacto no se
    # puede borrar mientras una corrida apunte a el.
    _insertar(
        motor,
        INSERTAR_CORRIDA_0007,
        _corrida_0007("RECHAZADA", artefacto),
        _corrida_0007("EN_PROCESO", artefacto),
    )
    _rechaza(motor, FK_CORRIDA_ARTEFACTO, INSERTAR_CORRIDA_0007, _corrida_0007("FALLIDA", 999_999))
    _rechaza(
        motor,
        FK_CORRIDA_ARTEFACTO,
        text("DELETE FROM artefacto_fuente WHERE id = :id"),
        {"id": artefacto},
    )
    # Y el despacho y la cartera no son opcionales.
    with pytest.raises(IntegrityError, match="despacho_id"), motor.begin() as conexion:
        conexion.execute(INSERTAR_CORRIDA_0007, _corrida_0007("FALLIDA", None, despacho_id=None))


def test_la_0007_baja_cerrando_lo_que_la_v05_no_podria_terminar_y_vuelve_a_subir(base_en_0007):
    b = base_en_0007
    motor = b.motor
    # Una corrida de v0.6 en la cola: su archivo solo esta en el almacen.
    (artefacto,) = _insertar(motor, INSERTAR_ARTEFACTO, _artefacto())
    (nueva,) = _insertar(motor, INSERTAR_CORRIDA_0007, _corrida_0007("EN_PROCESO", artefacto))
    (flujo,) = _insertar(motor, INSERTAR_FLUJO, _flujo(nueva))
    _insertar(motor, INSERTAR_TRABAJO, _trabajo("INGESTA", nueva, "EJECUTANDO", flujo_id=flujo))
    heredada_antes = _por_columna(motor, ("corrida",))["corrida"]

    command.downgrade(_alembic(), "0006")

    # E. La corrida de v0.6 queda FALLIDA con el motivo, su trabajo FALLIDO y su flujo DETENIDO.
    motivo = _cierre_0007()
    with motor.connect() as conexion:
        corrida = conexion.execute(
            text("SELECT estado, detalle, terminada_en FROM corrida WHERE id = :id"), {"id": nueva}
        ).one()
        trabajo = conexion.execute(
            text(
                "SELECT estado, worker_id, lease_hasta, terminado_en, ultimo_error "
                "FROM trabajo_orquestacion WHERE corrida_id = :id"
            ),
            {"id": nueva},
        ).one()
        detenido = conexion.execute(
            text("SELECT estado, etapa, detalle FROM flujo_orquestacion WHERE corrida_id = :id"),
            {"id": nueva},
        ).one()
        heredada = conexion.execute(
            text(
                "SELECT c.estado, t.estado FROM corrida c JOIN trabajo_orquestacion t "
                "ON t.corrida_id = c.id WHERE c.id = :id"
            ),
            {"id": b.heredada_id},
        ).one()
    assert (corrida.estado, corrida.detalle) == ("FALLIDA", motivo)
    assert corrida.terminada_en is not None
    assert (trabajo.estado, trabajo.worker_id, trabajo.lease_hasta, trabajo.ultimo_error) == (
        "FALLIDO",
        None,
        None,
        motivo,
    )
    assert trabajo.terminado_en is not None
    assert tuple(detenido) == ("DETENIDO", "INGESTA", motivo)
    # La corrida de v0.5 sigue en la cola con su archivo: la v0.5 la puede terminar.
    assert tuple(heredada) == ("EN_PROCESO", "PENDIENTE")
    assert _archivos(motor) == b.archivos
    # Lo demas de la corrida, igual, sin las columnas de la 0007.
    for fila in heredada_antes:
        if fila["id"] == b.heredada_id:
            esperada = {k: v for k, v in fila.items() if k not in COLUMNAS_NUEVAS_DE_LA_CORRIDA}
    (despues,) = [
        f for f in _por_columna(motor, ("corrida",))["corrida"] if f["id"] == b.heredada_id
    ]
    assert despues == esperada
    assert not TABLAS_DE_FUENTES & set(_consultar(motor, TABLAS))
    assert _consultar(motor, "SELECT version_num FROM alembic_version") == ["0006"]

    # Y vuelve a subir limpia, sin los artefactos que se fueron al bajar: el almacen conserva los
    # objetos, pero la base ya no los registra.
    command.upgrade(_alembic(), "0007")

    assert _consultar(motor, "SELECT count(*) FROM artefacto_fuente") == [0]
    assert set(_consultar(motor, RESTRICCIONES_0007)) == RESTRICCIONES_DE_FUENTES
    assert _consultar(motor, "SELECT DISTINCT despacho_id FROM corrida") == ["DSP_001"]
    assert _consultar(
        motor, "SELECT count(*) FROM corrida WHERE artefacto_fuente_id IS NOT NULL"
    ) == [0]
    assert _consultar(motor, "SELECT version_num FROM alembic_version") == ["0007"]
