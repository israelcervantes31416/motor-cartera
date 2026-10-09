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
    CorteCanonico,
    CuentaCanonica,
    DatasetConformado,
    EjecucionHistoria,
    EjecucionMotorPagos,
    EjecucionRuteo,
    EjecucionTerritorial,
    FlujoOrquestacion,
    HojaCompanera,
    IngestaPagos,
    MovimientoEconomicoCanonico,
    PagoObservado,
    ParadaRuta,
    RechazoPago,
    ResultadoPagoObservado,
    ResultadoTerritorial,
    RutaTerritorial,
    SnapshotCuenta,
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
RESTRICCIONES_DE_PAGOS_EN_LA_COLA = {"fk_trabajo_pagos", "uq_trabajo_pagos"}
"""Lo que la 0007 agrega a trabajo_orquestacion para las ingestas de pagos."""

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
    # subida hasta la cabeza. Incluye lo que la 0007 agrega a la cola para las ingestas de pagos, la
    # 0008 para la historia, la 0009 para el motor de pagos y la 0010 para la atribucion y la
    # evaluacion de promesas: su tipo de trabajo, su columna, su llave foranea y su restriccion
    # unica.
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
        "ingesta_pagos_id",
        "ejecucion_historia_id",
        "ejecucion_motor_pagos_id",
        "ejecucion_atribucion_id",
        "ejecucion_evaluacion_promesas_id",
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
        "ingesta_pagos_id",
        "ejecucion_historia_id",
        "ejecucion_motor_pagos_id",
        "ejecucion_atribucion_id",
        "ejecucion_evaluacion_promesas_id",
        "tomado_en",
        "latido_en",
        "lease_hasta",
        "terminado_en",
        "worker_id",
        "ultimo_error",
    ]
    assert columnas["tipo"].type.enums == [
        "INGESTA",
        "DECISION",
        "TERRITORIAL",
        "RUTEO",
        "INGESTA_PAGOS",
        "HISTORIA",
        "MOTOR_PAGOS",
        "ATRIBUCION",
        "EVALUACION_PROMESAS",
    ]
    assert columnas["tipo"].type.length == 24  # EVALUACION_PROMESAS no cabe en 16
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
        ("trabajo_orquestacion", "ingesta_pagos_id"): ("ingesta_pagos.id", None),
        ("trabajo_orquestacion", "ejecucion_historia_id"): ("ejecucion_historia.id", None),
        ("trabajo_orquestacion", "ejecucion_motor_pagos_id"): ("ejecucion_motor_pagos.id", None),
        ("trabajo_orquestacion", "ejecucion_atribucion_id"): ("ejecucion_atribucion.id", None),
        ("trabajo_orquestacion", "ejecucion_evaluacion_promesas_id"): (
            "ejecucion_evaluacion_promesas.id",
            None,
        ),
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
        "uq_trabajo_pagos": ["ingesta_pagos_id"],
        "uq_trabajo_historia": ["ejecucion_historia_id"],
        "uq_trabajo_motor_pagos": ["ejecucion_motor_pagos_id"],
        "uq_trabajo_atribucion": ["ejecucion_atribucion_id"],
        "uq_trabajo_evaluacion": ["ejecucion_evaluacion_promesas_id"],
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
    assert nombres == (
        RESTRICCIONES_DE_ORQUESTACION
        | RESTRICCIONES_DE_PAGOS_EN_LA_COLA
        | RESTRICCIONES_DE_HISTORIA_EN_LA_COLA
        | RESTRICCIONES_DEL_MOTOR_EN_LA_COLA
        | RESTRICCIONES_DE_LA_0010_EN_LA_COLA
        | INDICES_DE_ORQUESTACION
    )
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
    # migraciones, la 0010 incluida, es el que declaran los modelos.
    command.upgrade(_alembic(), "head")
    salida = io.StringIO()
    cfg = ConfigAlembic(str(RAIZ / "alembic.ini"), stdout=salida)
    cfg.set_main_option("script_location", str(RAIZ / "migraciones"))

    command.check(cfg)  # con cualquier diferencia levantaria AutogenerateDiffsDetected

    assert salida.getvalue().strip() == "No new upgrade operations detected."
    assert _consultar(base_en_0001, "SELECT version_num FROM alembic_version") == ["0010"]


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

TABLAS_DE_FUENTES = {
    "artefacto_fuente",
    "dataset_conformado",
    "hoja_companera",
    "ingesta_pagos",
    "rechazo_pago",
}
# Las tablas que existen antes de la 0007, que se comparan completas, columna por columna.
PREVIAS_A_LA_0007 = (*PREVIAS_A_LA_0006, "flujo_orquestacion", "trabajo_orquestacion")
COLUMNAS_NUEVAS_DE_LA_CORRIDA = {
    "artefacto_fuente_id",
    "despacho_id",
    "cartera_id",
    "version_proyeccion",
}

RESTRICCIONES_0007 = RESTRICCIONES.replace(
    "'ejecucion_decision', 'decision_cuenta'",
    "'artefacto_fuente', 'dataset_conformado', 'hoja_companera', 'ingesta_pagos', 'rechazo_pago'",
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
    "pk_dataset_conformado",
    "uq_dataset_conformado_dataset_id",
    "uq_conformado_corrida",
    "uq_conformado_pagos",
    "fk_conformado_corrida",
    "fk_conformado_pagos",
    "fk_conformado_original",
    "fk_conformado_parquet",
    "ck_conformado_conteos",
    "ck_conformado_firma",
    "ck_conformado_origen",
    "pk_hoja_companera",
    "fk_companera_corrida",
    "uq_companera_corrida_nombre",
    "ck_companera_conteos",
    "pk_ingesta_pagos",
    "uq_ingesta_pagos_pagos_run_id",
    "fk_pagos_artefacto",
    "ck_pagos_conteos",
    "ck_ingesta_pagos_estado_ingesta_pagos",
    "pk_rechazo_pago",
    "fk_rechazo_pago_ingesta",
    "uq_rechazo_pago_fila",
}
FK_CORRIDA_ARTEFACTO = "fk_corrida_artefacto_fuente_id_artefacto_fuente"
CHECKS_DE_LA_CORRIDA = {"ck_corrida_v2_fuente", "ck_corrida_proyeccion"}
INSERTAR_CONFORMADO = text(
    "INSERT INTO dataset_conformado (dataset_id, contrato, corrida_id, artefacto_original_id, "
    "artefacto_conformado_id, firma_contenido, filas, columnas, creado_en) VALUES (:dataset_id, "
    "'cartera/v2', :corrida_id, :original, :conformado, :firma, :filas, :columnas, now()) "
    "RETURNING id"
)
INSERTAR_COMPANERA = text(
    "INSERT INTO hoja_companera (corrida_id, nombre, filas, columnas, estructura_reconocida, "
    "advertencias, creado_en) VALUES (:corrida_id, :nombre, :filas, :columnas, true, "
    "CAST('[]' AS JSONB), now()) RETURNING id"
)


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


def test_los_modelos_declaran_el_esquema_de_la_0007():
    # Sin base: lo que declaran los modelos. En el CI, alembic check los compara ademas con la base.
    tablas = (
        ArtefactoFuente.__table__,
        DatasetConformado.__table__,
        HojaCompanera.__table__,
        IngestaPagos.__table__,
        RechazoPago.__table__,
    )
    tabla = tablas[0]
    columnas = {c.name: (type(c.type), c.nullable) for c in tabla.columns}
    assert columnas["tamano_bytes"] == (BigInteger, False)
    assert columnas["sha256"][1] is False and columnas["storage_key"][1] is False
    assert columnas["media_type"][1] is True
    assert tabla.c.formato.type.length == 12
    assert set(tabla.c.formato.type.enums) == {"xlsx", "csv", "zip", "parquet"}
    preparador = postgresql.dialect().identifier_preparer
    nombres = {preparador.format_constraint(c) for t in tablas for c in t.constraints}
    assert nombres == RESTRICCIONES_DE_FUENTES
    # La corrida apunta a su artefacto, y registra el despacho y la cartera del sistema.
    corrida = Corrida.__table__
    assert corrida.c.artefacto_fuente_id.nullable is True
    assert (corrida.c.despacho_id.nullable, corrida.c.cartera_id.nullable) == (False, False)
    llaves = {preparador.format_constraint(c) for c in corrida.foreign_key_constraints}
    assert FK_CORRIDA_ARTEFACTO in llaves
    checks = {preparador.format_constraint(c) for c in corrida.constraints if c.name}
    assert CHECKS_DE_LA_CORRIDA <= checks
    assert corrida.c.version_proyeccion.nullable is True
    assert all(len(nombre) <= 63 for nombre in nombres | llaves | checks)
    # Un dataset conformado es de una corrida o de una ingesta de pagos, nunca de las dos.
    conformado = DatasetConformado.__table__
    assert (conformado.c.corrida_id.nullable, conformado.c.ingesta_pagos_id.nullable) == (
        True,
        True,
    )
    # La ingesta de pagos: conteos que caben en BIGINT, sus cuatro estados y sus indices parciales.
    pagos = IngestaPagos.__table__
    assert all(
        isinstance(pagos.c[columna].type, BigInteger)
        for columna in ("filas_leidas", "filas_validas", "filas_rechazadas")
    )
    assert pagos.c.estado.type.enums == ["EN_PROCESO", "EXITOSA", "RECHAZADA", "FALLIDA"]
    assert (pagos.c.firma_contenido.nullable, pagos.c.terminada_en.nullable) == (True, True)
    parciales = {
        indice.name: str(indice.dialect_options["postgresql"]["where"])
        for indice in pagos.indexes
        if indice.unique
    }
    assert parciales == {
        "ux_ingesta_pagos_firma_publicada": "estado = 'EXITOSA'",
        "ux_ingesta_pagos_firma_en_proceso": "estado = 'EN_PROCESO'",
    }


def test_la_0007_agrega_el_artefacto_fuente_y_no_toca_lo_que_ya_estaba(base_en_0007):
    b = base_en_0007
    motor = b.motor

    # A. Cada fila de antes sigue ahi, identica. La corrida solo gana tres columnas: sin artefacto,
    # porque su archivo no se conservo, y con el despacho y la cartera del sistema. Un trabajo, la
    # de su ingesta de pagos: ninguno es de una.
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
                    "version_proyeccion": None,
                }
            if tabla == "trabajo_orquestacion":
                assert fila.pop("ingesta_pagos_id") is None
            assert fila == antes, tabla
    # El archivo de la corrida que seguia en la cola no se toca: la v0.6 lo va a leer.
    assert _archivos(motor) == b.archivos

    # B. Las tablas nuevas, vacias, con sus restricciones por nombre.
    assert TABLAS_DE_FUENTES <= set(_consultar(motor, TABLAS))
    for tabla in sorted(TABLAS_DE_FUENTES):
        assert _consultar(motor, f"SELECT count(*) FROM {tabla}") == [0]
    assert set(_consultar(motor, RESTRICCIONES_0007)) == RESTRICCIONES_DE_FUENTES
    with motor.connect() as conexion:
        columnas = {
            columna: (tipo, largo, nulo, omision)
            for columna, tipo, largo, nulo, omision in conexion.execute(
                text(
                    "SELECT column_name, data_type, character_maximum_length, is_nullable, "
                    "column_default FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND table_name = 'corrida' "
                    "AND column_name IN ('artefacto_fuente_id', 'despacho_id', 'cartera_id', "
                    "'version_proyeccion')"
                )
            )
        }
    # Sin valor por omision en la base: cada corrida nueva trae el suyo.
    assert columnas == {
        "artefacto_fuente_id": ("integer", None, "YES", None),
        "despacho_id": ("character varying", 32, "NO", None),
        "cartera_id": ("character varying", 32, "NO", None),
        "version_proyeccion": ("character varying", 32, "YES", None),
    }
    checks = _consultar(
        motor,
        "SELECT conname FROM pg_constraint WHERE conrelid = CAST('corrida' AS regclass) "
        "AND contype = 'c'",
    )
    assert CHECKS_DE_LA_CORRIDA <= set(checks)


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


def test_una_corrida_de_cartera_v2_trae_su_archivo_su_corte_y_su_proyeccion(base_en_0007):
    motor = base_en_0007.motor
    (artefacto,) = _insertar(motor, INSERTAR_ARTEFACTO, _artefacto())
    v2 = {"version_contrato": "cartera/v2", "fecha_corte": "2026-09-30"}
    insertar = text(
        "INSERT INTO corrida (run_id, iniciada_en, origen, firma, estado, tolerancia_rechazo, "
        "version_contrato, fecha_corte, version_proyeccion, filas_leidas, filas_validas, "
        "filas_rechazadas, artefacto_fuente_id, despacho_id, cartera_id) VALUES (:run_id, now(), "
        "'c.xlsx', :firma, 'EN_PROCESO', 0.05, :version_contrato, :fecha_corte, "
        ":version_proyeccion, 0, 0, 0, :artefacto_fuente_id, 'DSP_001', 'CARTERA_PRINCIPAL') "
        "RETURNING id"
    )

    def corrida(**cambios) -> dict:
        base = {
            "run_id": uuid4(),
            "firma": uuid4().hex * 2,
            **v2,
            "version_proyeccion": "operacional/v1",
            "artefacto_fuente_id": artefacto,
        }
        return {**base, **cambios}

    # F. cartera/v2 sin su archivo en el almacen, o sin su corte, no existe; ni sin su proyeccion.
    _insertar(motor, insertar, corrida())
    _rechaza(motor, "ck_corrida_v2_fuente", insertar, corrida(artefacto_fuente_id=None))
    _rechaza(motor, "ck_corrida_v2_fuente", insertar, corrida(fecha_corte=None))
    _rechaza(motor, "ck_corrida_proyeccion", insertar, corrida(version_proyeccion=None))
    # cartera/v1 ya es la forma de Cuenta: no tiene proyeccion.
    _rechaza(
        motor,
        "ck_corrida_proyeccion",
        insertar,
        corrida(version_contrato="cartera/v1", fecha_corte=None),
    )
    _insertar(
        motor,
        insertar,
        corrida(version_contrato="cartera/v1", fecha_corte=None, version_proyeccion=None),
    )


def test_el_conformado_y_la_companera_cuelgan_de_su_corrida(base_en_0007):
    motor = base_en_0007.motor
    original, parquet = _insertar(
        motor,
        INSERTAR_ARTEFACTO,
        _artefacto(b"original"),
        _artefacto(b"parquet", formato="parquet"),
    )
    (corrida,) = _insertar(motor, INSERTAR_CORRIDA_0007, _corrida_0007("EXITOSA", original))

    def conformado(**cambios) -> dict:
        base = {
            "dataset_id": uuid4(),
            "corrida_id": corrida,
            "original": original,
            "conformado": parquet,
            "firma": "a" * 64,
            "filas": 10,
            "columnas": 93,
        }
        return {**base, **cambios}

    # G. Un conformado por corrida, con una firma valida y conteos que no son negativos.
    (dataset,) = _insertar(motor, INSERTAR_CONFORMADO, conformado())
    _rechaza(motor, "uq_conformado_corrida", INSERTAR_CONFORMADO, conformado())
    (otra,) = _insertar(motor, INSERTAR_CORRIDA_0007, _corrida_0007("EXITOSA", original))
    _rechaza(
        motor, "ck_conformado_firma", INSERTAR_CONFORMADO, conformado(corrida_id=otra, firma="x")
    )
    _rechaza(
        motor, "ck_conformado_conteos", INSERTAR_CONFORMADO, conformado(corrida_id=otra, filas=-1)
    )
    _rechaza(
        motor,
        "fk_conformado_parquet",
        INSERTAR_CONFORMADO,
        conformado(corrida_id=otra, conformado=999_999),
    )
    # Una companera por nombre en cada corrida.
    companera = {"corrida_id": corrida, "nombre": "CARRIER", "filas": 3, "columnas": 85}
    _insertar(motor, INSERTAR_COMPANERA, companera)
    _rechaza(motor, "uq_companera_corrida_nombre", INSERTAR_COMPANERA, companera)
    _rechaza(
        motor, "ck_companera_conteos", INSERTAR_COMPANERA, {**companera, "nombre": "X", "filas": -1}
    )
    # Y nada se borra en cascada: el artefacto de un conformado no se puede borrar.
    _rechaza(
        motor,
        "fk_conformado_parquet",
        text("DELETE FROM artefacto_fuente WHERE id = :id"),
        {"id": parquet},
    )
    assert _consultar(motor, f"SELECT filas FROM dataset_conformado WHERE id = {dataset}") == [10]


INSERTAR_INGESTA_PAGOS = text(
    "INSERT INTO ingesta_pagos (pagos_run_id, artefacto_fuente_id, origen, firma, "
    "version_contrato, estado, despacho_id, cartera_id, tolerancia_rechazo, iniciada_en, "
    "filas_leidas, filas_validas, filas_rechazadas) VALUES (:pagos_run_id, :artefacto, "
    "'pagos.csv', :firma, 'pagos/v1', :estado, 'DSP_001', 'CARTERA_PRINCIPAL', 0, now(), "
    ":leidas, :validas, :rechazadas) RETURNING id"
)
INSERTAR_RECHAZO_PAGO = text(
    "INSERT INTO rechazo_pago (ingesta_pagos_id, fila, valores, motivos) VALUES (:ingesta, "
    ":fila, CAST('{}' AS JSONB), CAST('[]' AS JSONB)) RETURNING id"
)
INSERTAR_TRABAJO_DE_PAGOS = text(
    "INSERT INTO trabajo_orquestacion (trabajo_id, tipo, estado, corrida_id, ingesta_pagos_id, "
    "intentos, max_intentos, creado_en, disponible_desde) VALUES (:trabajo_id, :tipo, "
    "'PENDIENTE', :corrida_id, :ingesta, 0, 5, now(), now()) RETURNING id"
)
INSERTAR_CONFORMADO_DE_PAGOS = text(
    "INSERT INTO dataset_conformado (dataset_id, contrato, corrida_id, ingesta_pagos_id, "
    "artefacto_original_id, artefacto_conformado_id, firma_contenido, filas, columnas, creado_en) "
    "VALUES (:dataset_id, 'pagos/v1', :corrida_id, :ingesta, :original, :conformado, :firma, 10, "
    "23, now()) RETURNING id"
)
BORRAR_INGESTA = text("DELETE FROM ingesta_pagos WHERE id = :id")


def _ingesta_pagos(artefacto: int, estado: str = "EN_PROCESO", **cambios) -> dict:
    """Una ingesta de pagos de ese artefacto; `cambios` reemplaza cualquier valor."""
    base = {
        "pagos_run_id": uuid4(),
        "artefacto": artefacto,
        "firma": "b" * 64,
        "estado": estado,
        "leidas": 0,
        "validas": 0,
        "rechazadas": 0,
    }
    return {**base, **cambios}


def test_una_ingesta_de_pagos_con_su_trabajo_sus_rechazos_y_su_conformado(base_en_0007):
    motor = base_en_0007.motor
    original, parquet = _insertar(
        motor,
        INSERTAR_ARTEFACTO,
        _artefacto(b"pagos"),
        _artefacto(b"parquet de pagos", formato="parquet"),
    )

    # H. Un archivo se procesa en una sola ingesta a la vez y se acepta una vez; las que no se
    # aceptaron no estorban. Los conteos cuadran y el estado es uno de los cuatro.
    (abierta,) = _insertar(motor, INSERTAR_INGESTA_PAGOS, _ingesta_pagos(original))
    _rechaza(
        motor,
        "ux_ingesta_pagos_firma_en_proceso",
        INSERTAR_INGESTA_PAGOS,
        _ingesta_pagos(original),
    )
    aceptada = {"estado": "EXITOSA", "leidas": 3, "validas": 3}
    (exitosa,) = _insertar(motor, INSERTAR_INGESTA_PAGOS, _ingesta_pagos(original, **aceptada))
    _rechaza(
        motor,
        "ux_ingesta_pagos_firma_publicada",
        INSERTAR_INGESTA_PAGOS,
        _ingesta_pagos(original, **aceptada),
    )
    rechazada, _ = _insertar(
        motor,
        INSERTAR_INGESTA_PAGOS,
        _ingesta_pagos(original, "RECHAZADA", leidas=2, validas=1, rechazadas=1),
        _ingesta_pagos(original, "FALLIDA"),
    )
    for restriccion, ingesta in (
        (
            "ck_pagos_conteos",
            _ingesta_pagos(original, "FALLIDA", leidas=2, validas=2, rechazadas=1),
        ),
        ("ck_ingesta_pagos_estado_ingesta_pagos", _ingesta_pagos(original, "PUBLICADA")),
        ("fk_pagos_artefacto", _ingesta_pagos(999_999, "FALLIDA")),
    ):
        _rechaza(motor, restriccion, INSERTAR_INGESTA_PAGOS, ingesta)
    # Un rechazo por fila.
    _insertar(motor, INSERTAR_RECHAZO_PAGO, {"ingesta": rechazada, "fila": 7})
    _rechaza(
        motor, "uq_rechazo_pago_fila", INSERTAR_RECHAZO_PAGO, {"ingesta": rechazada, "fila": 7}
    )

    # I. Su trabajo, uno por ingesta, apunta a ella y a nada mas. INGESTA_PAGOS cabe en el tipo.
    trabajo = {
        "trabajo_id": uuid4(),
        "tipo": "INGESTA_PAGOS",
        "corrida_id": None,
        "ingesta": abierta,
    }
    _insertar(motor, INSERTAR_TRABAJO_DE_PAGOS, trabajo)
    otro = {**trabajo, "trabajo_id": uuid4()}
    _rechaza(motor, "uq_trabajo_pagos", INSERTAR_TRABAJO_DE_PAGOS, otro)
    _rechaza(motor, "ck_trabajo_objetivo", INSERTAR_TRABAJO_DE_PAGOS, {**otro, "ingesta": None})
    (corrida,) = _insertar(motor, INSERTAR_CORRIDA_0007, _corrida_0007("EN_PROCESO", None))
    _rechaza(
        motor,
        "ck_trabajo_objetivo",
        INSERTAR_TRABAJO_DE_PAGOS,
        {**otro, "tipo": "INGESTA", "corrida_id": corrida, "ingesta": rechazada},
    )
    largo = (
        "SELECT character_maximum_length FROM information_schema.columns WHERE table_schema = "
        "current_schema() AND table_name = 'trabajo_orquestacion' AND column_name = 'tipo'"
    )
    assert _consultar(motor, largo) == [16]

    # J. Un conformado es de una corrida o de una ingesta de pagos: nunca de las dos ni de ninguna.
    conformado = {
        "dataset_id": uuid4(),
        "corrida_id": None,
        "ingesta": exitosa,
        "original": original,
        "conformado": parquet,
        "firma": "c" * 64,
    }
    _insertar(motor, INSERTAR_CONFORMADO_DE_PAGOS, conformado)
    otro = {**conformado, "dataset_id": uuid4()}
    _rechaza(motor, "uq_conformado_pagos", INSERTAR_CONFORMADO_DE_PAGOS, otro)
    for cambios in ({"ingesta": rechazada, "corrida_id": corrida}, {"ingesta": None}):
        _rechaza(motor, "ck_conformado_origen", INSERTAR_CONFORMADO_DE_PAGOS, {**otro, **cambios})

    # Y nada se borra en cascada: ni la ingesta con su trabajo, ni con sus rechazos, ni con su
    # conformado, ni el artefacto de una ingesta.
    for restriccion, ingesta in (
        ("fk_trabajo_pagos", abierta),
        ("fk_rechazo_pago_ingesta", rechazada),
        ("fk_conformado_pagos", exitosa),
    ):
        _rechaza(motor, restriccion, BORRAR_INGESTA, {"id": ingesta})
    _rechaza(
        motor,
        "fk_pagos_artefacto",
        text("DELETE FROM artefacto_fuente WHERE id = :id"),
        {"id": original},
    )


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


def test_la_0007_baja_quitando_las_ingestas_de_pagos_y_sus_trabajos(base_en_0007):
    b = base_en_0007
    motor = b.motor
    (artefacto,) = _insertar(motor, INSERTAR_ARTEFACTO, _artefacto(b"pagos para bajar"))
    (ingesta,) = _insertar(motor, INSERTAR_INGESTA_PAGOS, _ingesta_pagos(artefacto))
    _insertar(motor, INSERTAR_RECHAZO_PAGO, {"ingesta": ingesta, "fila": 2})
    _insertar(
        motor,
        INSERTAR_TRABAJO_DE_PAGOS,
        {"trabajo_id": uuid4(), "tipo": "INGESTA_PAGOS", "corrida_id": None, "ingesta": ingesta},
    )
    trabajos = "SELECT tipo FROM trabajo_orquestacion ORDER BY id"
    assert _consultar(motor, trabajos) == ["INGESTA", "INGESTA_PAGOS"]

    command.downgrade(_alembic(), "0006")

    # La 0006 no conoce las ingestas de pagos: se van sus trabajos, sus rechazos y ellas. El trabajo
    # de la corrida de v0.5 se queda. El almacen conserva sus archivos: bajar no borra evidencia.
    assert _consultar(motor, trabajos) == ["INGESTA"]
    assert not {"ingesta_pagos", "rechazo_pago"} & set(_consultar(motor, TABLAS))
    columnas = (
        "SELECT column_name, character_maximum_length FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = 'trabajo_orquestacion' "
        "AND column_name IN ('tipo', 'ingesta_pagos_id')"
    )
    with motor.connect() as conexion:
        assert [tuple(fila) for fila in conexion.execute(text(columnas))] == [("tipo", 12)]
    assert set(_consultar(motor, RESTRICCIONES_0006)) == RESTRICCIONES_DE_ORQUESTACION
    # Y sus CHECK vuelven a ser los de la 0006: el tipo INGESTA_PAGOS ya no existe.
    definiciones = _consultar(
        motor,
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname IN "
        "('ck_trabajo_objetivo', 'ck_trabajo_orquestacion_tipo_trabajo')",
    )
    assert len(definiciones) == 2
    assert not any("INGESTA_PAGOS" in d or "ingesta_pagos_id" in d for d in definiciones)

    command.upgrade(_alembic(), "0007")

    assert set(_consultar(motor, RESTRICCIONES_0007)) == RESTRICCIONES_DE_FUENTES
    assert _consultar(motor, "SELECT count(*) FROM ingesta_pagos") == [0]


# --- la 0008: el modelo historico y la Cuenta 360 ------------------------------------------------

TABLAS_HISTORICAS = {
    "cuenta_canonica",
    "corte_canonico",
    "ejecucion_historia",
    "snapshot_cuenta",
    "pago_observado",
}
# Las tablas que existen antes de la 0008, que se comparan completas, columna por columna.
PREVIAS_A_LA_0008 = (
    *PREVIAS_A_LA_0007,
    "artefacto_fuente",
    "dataset_conformado",
    "hoja_companera",
    "ingesta_pagos",
    "rechazo_pago",
)
RESTRICCIONES_0008 = RESTRICCIONES.replace(
    "'ejecucion_decision', 'decision_cuenta'",
    "'cuenta_canonica', 'corte_canonico', 'ejecucion_historia', 'snapshot_cuenta', "
    "'pago_observado'",
)
INDICES_0008 = INDICES.replace(
    "'ejecucion_decision', 'decision_cuenta'",
    "'cuenta_canonica', 'corte_canonico', 'ejecucion_historia', 'snapshot_cuenta', "
    "'pago_observado'",
)
RESTRICCIONES_HISTORICAS = {
    "pk_cuenta_canonica",
    "uq_cuenta_canonica_cuenta_id",
    "uq_cuenta_canonica_clave",
    "pk_corte_canonico",
    "uq_corte_canonico_corte_id",
    "uq_corte_canonico_fecha",
    "uq_corte_canonico_dataset",
    "uq_corte_canonico_id_fecha",
    "fk_corte_dataset",
    "ck_corte_firma",
    "ck_corte_cuentas",
    "pk_ejecucion_historia",
    "uq_ejecucion_historia_historia_run_id",
    "fk_historia_dataset",
    "fk_historia_corte",
    "ck_historia_conteos",
    "ck_historia_corte",
    "ck_historia_resultado",
    "ck_ejecucion_historia_tipo_fuente_historia",
    "ck_ejecucion_historia_estado_historia",
    "pk_snapshot_cuenta",
    "fk_snapshot_corte",
    "fk_snapshot_cuenta",
    "pk_pago_observado",
    "fk_pago_observado_dataset",
    "fk_pago_observado_ingesta",
}
RESTRICCIONES_DE_HISTORIA_EN_LA_COLA = {"fk_trabajo_historia", "uq_trabajo_historia"}
# Cada indice de las tablas nuevas: su tabla, si es unico, sus columnas y su predicado parcial.
INDICES_HISTORICOS = {
    ("cuenta_canonica", "pk_cuenta_canonica", True, ("id",), None),
    ("cuenta_canonica", "uq_cuenta_canonica_cuenta_id", True, ("cuenta_id",), None),
    (
        "cuenta_canonica",
        "uq_cuenta_canonica_clave",
        True,
        ("despacho_id", "cartera_id", "cliente_unico"),
        None,
    ),
    ("corte_canonico", "pk_corte_canonico", True, ("id",), None),
    ("corte_canonico", "uq_corte_canonico_corte_id", True, ("corte_id",), None),
    (
        "corte_canonico",
        "uq_corte_canonico_fecha",
        True,
        ("despacho_id", "cartera_id", "fecha_corte"),
        None,
    ),
    ("corte_canonico", "uq_corte_canonico_dataset", True, ("dataset_conformado_id",), None),
    ("corte_canonico", "uq_corte_canonico_id_fecha", True, ("id", "fecha_corte"), None),
    ("ejecucion_historia", "pk_ejecucion_historia", True, ("id",), None),
    (
        "ejecucion_historia",
        "uq_ejecucion_historia_historia_run_id",
        True,
        ("historia_run_id",),
        None,
    ),
    (
        "ejecucion_historia",
        "ix_ejecucion_historia_dataset_conformado_id",
        False,
        ("dataset_conformado_id",),
        None,
    ),
    (
        "ejecucion_historia",
        "ux_ejecucion_historia_exitosa",
        True,
        ("dataset_conformado_id", "version_modelo"),
        "((estado)::text = 'EXITOSA'::text)",
    ),
    (
        "ejecucion_historia",
        "ux_ejecucion_historia_en_proceso",
        True,
        ("dataset_conformado_id", "version_modelo"),
        "((estado)::text = 'EN_PROCESO'::text)",
    ),
    (
        "snapshot_cuenta",
        "pk_snapshot_cuenta",
        True,
        ("corte_canonico_id", "cuenta_canonica_id"),
        None,
    ),
    (
        "snapshot_cuenta",
        "ix_snapshot_cuenta_historia",
        False,
        ("cuenta_canonica_id", "fecha_corte"),
        None,
    ),
    (
        "pago_observado",
        "pk_pago_observado",
        True,
        ("dataset_conformado_id", "source_row"),
        None,
    ),
    (
        "pago_observado",
        "ix_pago_observado_cuenta",
        False,
        ("despacho_id", "cartera_id", "cliente_unico", "fecha_recepcion"),
        None,
    ),
}
"""Ni un indice mas: ninguno por si acaso. El pago_observado_id no tiene indice propio."""

INSERTAR_CUENTA_CANONICA = text(
    "INSERT INTO cuenta_canonica (cuenta_id, despacho_id, cartera_id, cliente_unico, creada_en) "
    "VALUES (:cuenta_id, 'DSP_001', :cartera, :cliente, now()) RETURNING id"
)
INSERTAR_CORTE = text(
    "INSERT INTO corte_canonico (corte_id, despacho_id, cartera_id, fecha_corte, "
    "firma_contenido, version_modelo, cuentas, dataset_conformado_id, creado_en) VALUES "
    "(:corte_id, 'DSP_001', 'CARTERA_PRINCIPAL', :fecha, :firma, 'historia/v1', :cuentas, "
    ":dataset, now()) RETURNING id"
)
INSERTAR_SNAPSHOT = text(
    "INSERT INTO snapshot_cuenta (corte_canonico_id, cuenta_canonica_id, fecha_corte, "
    "dias_atraso, source_row, saldo_total, producto, canal, cve_entidad, cve_municipio) VALUES "
    "(:corte, :cuenta, :fecha, 65, :fila, 62000.00, 'CONSUMO', 'CAMPO', '21', '114') "
    "RETURNING corte_canonico_id"
)
INSERTAR_HISTORIA = text(
    "INSERT INTO ejecucion_historia (historia_run_id, dataset_conformado_id, tipo_fuente, "
    "version_modelo, estado, resultado, corte_canonico_id, registros_leidos, "
    "registros_publicados, iniciada_en) VALUES (:historia_run_id, :dataset, :tipo, "
    "'historia/v1', :estado, :resultado, :corte, :leidos, :publicados, now()) RETURNING id"
)
INSERTAR_PAGO_OBSERVADO = text(
    "INSERT INTO pago_observado (dataset_conformado_id, source_row, fecha_recepcion, "
    "ingesta_pagos_id, pago_observado_id, recuperacion_por_gestion, despacho_id, cartera_id, "
    "cliente_unico) VALUES (:dataset, :fila, '2026-09-03 10:15:00', :ingesta, :id_publico, "
    "-150.00, 'DSP_001', 'CARTERA_PRINCIPAL', 'CU0000000001') RETURNING source_row"
)
INSERTAR_TRABAJO_HISTORIA = text(
    "INSERT INTO trabajo_orquestacion (trabajo_id, tipo, estado, corrida_id, "
    "ejecucion_historia_id, intentos, max_intentos, creado_en, disponible_desde, worker_id, "
    "lease_hasta, tomado_en) VALUES (:trabajo_id, :tipo, :estado, :corrida_id, :historia, "
    ":intentos, 5, now(), now(), :worker_id, :lease_hasta, now()) RETURNING id"
)


def _cierre_0008() -> str:
    return ScriptDirectory.from_config(_alembic()).get_revision("0008").module.CIERRE


def _historia(dataset: int, estado: str = "EN_PROCESO", **cambios) -> dict:
    terminada = estado != "EN_PROCESO"
    return {
        "historia_run_id": uuid4(),
        "dataset": dataset,
        "tipo": "CARTERA",
        "estado": estado,
        "resultado": "ERROR_INTERNO" if terminada else None,
        "corte": None,
        "leidos": 0,
        "publicados": 0,
        **cambios,
    }


def _trabajo_historia(historia: int | None, estado: str = "PENDIENTE", **cambios) -> dict:
    ejecutando = estado == "EJECUTANDO"
    return {
        "trabajo_id": uuid4(),
        "tipo": "HISTORIA",
        "estado": estado,
        "corrida_id": None,
        "historia": historia,
        "intentos": 1 if ejecutando else 0,
        "worker_id": "worker-de-prueba:1:abc" if ejecutando else None,
        "lease_hasta": datetime.now(UTC) + timedelta(minutes=1) if ejecutando else None,
        **cambios,
    }


@dataclass(frozen=True)
class BaseEn0008:
    """Una base de v0.6 con una cartera/v2 y unos pagos publicados, con sus datasets, subida a la
    0008: el modelo historico vacio, listo para el backfill."""

    motor: Engine
    corrida_id: int
    dataset_cartera: int
    ingesta_id: int
    dataset_pagos: int
    previas: dict[str, list[dict]]


@pytest.fixture
def base_en_0008(base_en_0007) -> BaseEn0008:
    motor = base_en_0007.motor
    original, parquet, pagos, parquet_pagos = _insertar(
        motor,
        INSERTAR_ARTEFACTO,
        _artefacto(b"cartera oficial"),
        _artefacto(b"su parquet", formato="parquet"),
        _artefacto(b"pagos oficiales"),
        _artefacto(b"parquet de pagos", formato="parquet"),
    )
    (corrida,) = _insertar(
        motor, INSERTAR_CORRIDA_0007, _corrida_0007("EXITOSA", original, firma="d" * 64)
    )
    (dataset_cartera,) = _insertar(
        motor,
        INSERTAR_CONFORMADO,
        {
            "dataset_id": uuid4(),
            "corrida_id": corrida,
            "original": original,
            "conformado": parquet,
            "firma": "e" * 64,
            "filas": 10,
            "columnas": 93,
        },
    )
    aceptada = {"estado": "EXITOSA", "leidas": 3, "validas": 3}
    (ingesta,) = _insertar(
        motor, INSERTAR_INGESTA_PAGOS, _ingesta_pagos(pagos, firma="f" * 64, **aceptada)
    )
    (dataset_pagos,) = _insertar(
        motor,
        INSERTAR_CONFORMADO_DE_PAGOS,
        {
            "dataset_id": uuid4(),
            "corrida_id": None,
            "ingesta": ingesta,
            "original": pagos,
            "conformado": parquet_pagos,
            "firma": "c" * 64,
        },
    )
    previas = _por_columna(motor, PREVIAS_A_LA_0008)

    command.upgrade(_alembic(), "0008")
    return BaseEn0008(motor, corrida, dataset_cartera, ingesta, dataset_pagos, previas)


def test_la_0008_baja_de_la_0007():
    scripts = ScriptDirectory.from_config(_alembic())

    assert scripts.get_revision("0008").down_revision == "0007"
    assert scripts.get_revision("0007").down_revision == "0006"


def test_los_modelos_declaran_el_esquema_de_la_0008():
    tablas = (
        CuentaCanonica.__table__,
        CorteCanonico.__table__,
        EjecucionHistoria.__table__,
        SnapshotCuenta.__table__,
        PagoObservado.__table__,
    )
    preparador = postgresql.dialect().identifier_preparer
    nombres = {preparador.format_constraint(c) for t in tablas for c in t.constraints}
    assert nombres == RESTRICCIONES_HISTORICAS
    assert all(len(nombre) <= 63 for nombre in nombres)
    # Las tablas que crecen con cada corte no tienen id propio: su llave es la natural.
    assert [c.name for c in SnapshotCuenta.__table__.primary_key] == [
        "corte_canonico_id",
        "cuenta_canonica_id",
    ]
    assert [c.name for c in PagoObservado.__table__.primary_key] == [
        "dataset_conformado_id",
        "source_row",
    ]
    assert "id" not in SnapshotCuenta.__table__.c and "id" not in PagoObservado.__table__.c
    # La cola conoce el trabajo HISTORIA y su objetivo.
    trabajo = TrabajoOrquestacion.__table__
    assert "HISTORIA" in trabajo.c.tipo.type.enums
    assert trabajo.c.ejecucion_historia_id.nullable is True
    llaves = {preparador.format_constraint(c) for c in trabajo.constraints}
    assert RESTRICCIONES_DE_HISTORIA_EN_LA_COLA <= llaves


def test_la_0008_agrega_el_modelo_historico_vacio_y_no_toca_lo_que_ya_estaba(base_en_0008):
    b = base_en_0008
    motor = b.motor

    # A. Cada fila de antes sigue ahi, identica. Un trabajo solo gana su ejecucion historica, que
    # no tiene: la migracion no materializa nada, eso es del backfill.
    despues = _por_columna(motor, PREVIAS_A_LA_0008)
    for tabla, filas in b.previas.items():
        assert len(despues[tabla]) == len(filas), tabla
        for antes, fila in zip(filas, despues[tabla], strict=True):
            fila = dict(fila)
            if tabla == "trabajo_orquestacion":
                assert fila.pop("ejecucion_historia_id") is None
            assert fila == antes, tabla

    # B. Las tablas nuevas, vacias, con sus restricciones y sus indices por nombre.
    assert TABLAS_HISTORICAS <= set(_consultar(motor, TABLAS))
    for tabla in sorted(TABLAS_HISTORICAS):
        assert _consultar(motor, f"SELECT count(*) FROM {tabla}") == [0]
    assert set(_consultar(motor, RESTRICCIONES_0008)) == RESTRICCIONES_HISTORICAS
    with motor.connect() as conexion:
        indices = {
            (tabla, nombre, unico, tuple(columnas), predicado)
            for tabla, nombre, unico, columnas, predicado in conexion.execute(text(INDICES_0008))
        }
    assert indices == INDICES_HISTORICOS
    assert _consultar(motor, "SELECT version_num FROM alembic_version") == ["0008"]


def test_un_corte_por_fecha_y_un_snapshot_por_cuenta_y_corte(base_en_0008):
    b = base_en_0008
    motor = b.motor
    una, otra = _insertar(
        motor,
        INSERTAR_CUENTA_CANONICA,
        {"cuenta_id": uuid4(), "cartera": "CARTERA_PRINCIPAL", "cliente": "CU0000000001"},
        {"cuenta_id": uuid4(), "cartera": "OTRA_CARTERA", "cliente": "CU0000000001"},
    )
    # La misma llave en la misma cartera es la misma cuenta; en otra cartera, otra.
    _rechaza(
        motor,
        "uq_cuenta_canonica_clave",
        INSERTAR_CUENTA_CANONICA,
        {"cuenta_id": uuid4(), "cartera": "CARTERA_PRINCIPAL", "cliente": "CU0000000001"},
    )
    corte = {
        "corte_id": uuid4(),
        "fecha": "2026-09-30",
        "firma": "e" * 64,
        "cuentas": 2,
        "dataset": b.dataset_cartera,
    }
    (corte_id,) = _insertar(motor, INSERTAR_CORTE, corte)
    # Una fotografia por cartera y fecha, de un solo dataset, con una firma y con cuentas.
    for restriccion, cambios in (
        ("uq_corte_canonico_fecha", {"corte_id": uuid4(), "dataset": b.dataset_pagos}),
        ("uq_corte_canonico_dataset", {"corte_id": uuid4(), "fecha": "2026-10-07"}),
        ("ck_corte_firma", {"corte_id": uuid4(), "fecha": "2026-10-07", "firma": "x"}),
        ("ck_corte_cuentas", {"corte_id": uuid4(), "fecha": "2026-10-07", "cuentas": 0}),
        ("fk_corte_dataset", {"corte_id": uuid4(), "fecha": "2026-10-07", "dataset": 999_999}),
    ):
        _rechaza(motor, restriccion, INSERTAR_CORTE, {**corte, **cambios})
    snapshot = {"corte": corte_id, "cuenta": una, "fecha": "2026-09-30", "fila": 2}
    _insertar(motor, INSERTAR_SNAPSHOT, snapshot, {**snapshot, "cuenta": otra, "fila": 3})
    # Una cuenta, a lo mas una vez por corte; la fecha del snapshot es la de su corte.
    _rechaza(motor, "pk_snapshot_cuenta", INSERTAR_SNAPSHOT, {**snapshot, "fila": 9})
    with motor.begin() as conexion:
        conexion.execute(
            text("DELETE FROM snapshot_cuenta WHERE cuenta_canonica_id = :c"), {"c": otra}
        )
    _rechaza(
        motor,
        "fk_snapshot_corte",
        INSERTAR_SNAPSHOT,
        {**snapshot, "cuenta": otra, "fecha": "2026-10-01"},
    )
    _rechaza(motor, "fk_snapshot_cuenta", INSERTAR_SNAPSHOT, {**snapshot, "cuenta": 999_999})
    # Y nada se borra en cascada: ni el corte de un snapshot, ni la cuenta, ni el dataset del corte.
    for restriccion, borrar, parametros in (
        ("fk_snapshot_corte", "DELETE FROM corte_canonico WHERE id = :id", {"id": corte_id}),
        ("fk_snapshot_cuenta", "DELETE FROM cuenta_canonica WHERE id = :id", {"id": una}),
        (
            "fk_corte_dataset",
            "DELETE FROM dataset_conformado WHERE id = :id",
            {"id": b.dataset_cartera},
        ),
    ):
        _rechaza(motor, restriccion, text(borrar), parametros)


def test_una_ejecucion_historica_exitosa_y_un_intento_activo_por_dataset(base_en_0008):
    b = base_en_0008
    motor = b.motor
    (corte,) = _insertar(
        motor,
        INSERTAR_CORTE,
        {
            "corte_id": uuid4(),
            "fecha": "2026-09-30",
            "firma": "e" * 64,
            "cuentas": 10,
            "dataset": b.dataset_cartera,
        },
    )
    publicada = {
        "estado": "EXITOSA",
        "resultado": "CORTE_PUBLICADO",
        "corte": corte,
        "leidos": 10,
        "publicados": 10,
    }
    _insertar(motor, INSERTAR_HISTORIA, _historia(b.dataset_cartera, **publicada))
    _rechaza(
        motor,
        "ux_ejecucion_historia_exitosa",
        INSERTAR_HISTORIA,
        _historia(b.dataset_cartera, **publicada),
    )
    _insertar(motor, INSERTAR_HISTORIA, _historia(b.dataset_cartera))
    _rechaza(
        motor, "ux_ejecucion_historia_en_proceso", INSERTAR_HISTORIA, _historia(b.dataset_cartera)
    )
    # Las FALLIDA son historia, tantas como haya.
    _insertar(
        motor,
        INSERTAR_HISTORIA,
        _historia(b.dataset_cartera, "FALLIDA"),
        _historia(b.dataset_cartera, "FALLIDA", resultado="CORTE_CANONICO_CONFLICTIVO"),
    )
    pagos = b.dataset_pagos
    for restriccion, ejecucion in (
        # Solo una de cartera EXITOSA tiene corte; una de pagos nunca.
        ("ck_historia_corte", _historia(pagos, "EXITOSA", resultado="X")),
        ("ck_historia_corte", _historia(pagos, "FALLIDA", corte=corte)),
        ("ck_historia_corte", _historia(pagos, "EXITOSA", tipo="PAGOS", corte=corte)),
        # Una terminada dice como termino; una EN_PROCESO, todavia no.
        ("ck_historia_resultado", _historia(pagos, "FALLIDA", resultado=None)),
        ("ck_historia_resultado", _historia(pagos, resultado="CORTE_PUBLICADO")),
        ("ck_historia_conteos", _historia(pagos, "FALLIDA", leidos=1, publicados=2)),
        ("ck_ejecucion_historia_tipo_fuente_historia", _historia(pagos, tipo="CUENTAS")),
        ("ck_ejecucion_historia_estado_historia", _historia(pagos, "PUBLICADA")),
        ("fk_historia_dataset", _historia(999_999)),
    ):
        _rechaza(motor, restriccion, INSERTAR_HISTORIA, ejecucion)
    _insertar(
        motor,
        INSERTAR_HISTORIA,
        _historia(pagos, "EXITOSA", tipo="PAGOS", resultado="PAGOS_PUBLICADOS"),
    )


def test_un_pago_observado_por_fila_de_su_dataset(base_en_0008):
    b = base_en_0008
    motor = b.motor
    pago = {"dataset": b.dataset_pagos, "fila": 2, "ingesta": b.ingesta_id, "id_publico": uuid4()}
    _insertar(motor, INSERTAR_PAGO_OBSERVADO, pago, {**pago, "fila": 3, "id_publico": uuid4()})
    # Una fila de la fuente, una observacion; los demas campos pueden repetirse.
    _rechaza(motor, "pk_pago_observado", INSERTAR_PAGO_OBSERVADO, {**pago, "id_publico": uuid4()})
    _rechaza(
        motor, "fk_pago_observado_dataset", INSERTAR_PAGO_OBSERVADO, {**pago, "dataset": 999_999}
    )
    _rechaza(
        motor,
        "fk_pago_observado_ingesta",
        INSERTAR_PAGO_OBSERVADO,
        {**pago, "fila": 4, "ingesta": 999_999},
    )
    with motor.connect() as conexion:
        tipo = conexion.execute(
            text(
                "SELECT data_type FROM information_schema.columns WHERE table_schema = "
                "current_schema() AND table_name = 'pago_observado' AND column_name = "
                "'fecha_recepcion'"
            )
        ).scalar_one()
    # Hora local de la fuente: sin zona horaria.
    assert tipo == "timestamp without time zone"
    _rechaza(
        motor,
        "fk_pago_observado_dataset",
        text("DELETE FROM dataset_conformado WHERE id = :id"),
        {"id": b.dataset_pagos},
    )


def test_el_trabajo_historia_apunta_a_su_ejecucion_y_a_nada_mas(base_en_0008):
    b = base_en_0008
    motor = b.motor
    (ejecucion,) = _insertar(motor, INSERTAR_HISTORIA, _historia(b.dataset_cartera))
    _insertar(motor, INSERTAR_TRABAJO_HISTORIA, _trabajo_historia(ejecucion))
    # Un trabajo por ejecucion, del tipo HISTORIA, con su objetivo y ningun otro.
    _rechaza(motor, "uq_trabajo_historia", INSERTAR_TRABAJO_HISTORIA, _trabajo_historia(ejecucion))
    _rechaza(motor, "ck_trabajo_objetivo", INSERTAR_TRABAJO_HISTORIA, _trabajo_historia(None))
    _rechaza(
        motor,
        "ck_trabajo_objetivo",
        INSERTAR_TRABAJO_HISTORIA,
        _trabajo_historia(ejecucion, tipo="INGESTA", corrida_id=b.corrida_id),
    )
    _rechaza(
        motor,
        "fk_trabajo_historia",
        INSERTAR_TRABAJO_HISTORIA,
        _trabajo_historia(999_999),
    )
    _rechaza(
        motor,
        "fk_trabajo_historia",
        text("DELETE FROM ejecucion_historia WHERE id = :id"),
        {"id": ejecucion},
    )


def test_la_0008_baja_cerrando_la_historia_en_curso_y_vuelve_a_subir(base_en_0008):
    b = base_en_0008
    motor = b.motor
    # Historia en curso: una ejecucion que un worker tiene y otra en la cola, y una ya publicada.
    activa, pendiente = _insertar(
        motor,
        INSERTAR_HISTORIA,
        _historia(b.dataset_cartera),
        _historia(b.dataset_pagos, tipo="PAGOS"),
    )
    _insertar(
        motor,
        INSERTAR_TRABAJO_HISTORIA,
        _trabajo_historia(activa, "EJECUTANDO"),
        _trabajo_historia(pendiente),
    )
    (cuenta,) = _insertar(
        motor,
        INSERTAR_CUENTA_CANONICA,
        {"cuenta_id": uuid4(), "cartera": "CARTERA_PRINCIPAL", "cliente": "CU0000000001"},
    )
    previas = _por_columna(motor, PREVIAS_A_LA_0008)
    trabajos_antes = [f for f in previas["trabajo_orquestacion"] if f["tipo"] != "HISTORIA"]
    assert len(previas["trabajo_orquestacion"]) == len(trabajos_antes) + 2

    command.downgrade(_alembic(), "0007")

    # Las tablas del modelo historico se fueron, con los trabajos HISTORIA. Lo demas, intacto:
    # corridas, datasets, artefactos e ingestas de pagos; los otros trabajos, sin su nueva columna.
    assert not TABLAS_HISTORICAS & set(_consultar(motor, TABLAS))
    despues = _por_columna(motor, PREVIAS_A_LA_0008)
    for tabla, filas in previas.items():
        if tabla == "trabajo_orquestacion":
            esperadas = [
                {k: v for k, v in f.items() if k != "ejecucion_historia_id"} for f in trabajos_antes
            ]
            assert despues[tabla] == esperadas
        else:
            assert despues[tabla] == filas, tabla
    definiciones = _consultar(
        motor,
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname IN "
        "('ck_trabajo_objetivo', 'ck_trabajo_orquestacion_tipo_trabajo')",
    )
    assert len(definiciones) == 2
    assert not any("HISTORIA" in d or "ejecucion_historia_id" in d for d in definiciones)
    assert set(_consultar(motor, RESTRICCIONES_0007)) == RESTRICCIONES_DE_FUENTES
    assert _consultar(motor, "SELECT version_num FROM alembic_version") == ["0007"]
    assert cuenta > 0  # la cuenta canonica se fue con su tabla: se reconstruye con el backfill

    # Y vuelve a subir limpia: el modelo historico vacio, listo para el backfill.
    command.upgrade(_alembic(), "0008")

    for tabla in sorted(TABLAS_HISTORICAS):
        assert _consultar(motor, f"SELECT count(*) FROM {tabla}") == [0]
    assert set(_consultar(motor, RESTRICCIONES_0008)) == RESTRICCIONES_HISTORICAS
    assert _consultar(motor, "SELECT version_num FROM alembic_version") == ["0008"]


def test_al_bajar_la_0008_lo_primero_es_cerrar_la_historia_en_curso(base_en_0008):
    # Lo que la 0008 hace antes de quitar sus tablas, con sus mismas sentencias: la ejecucion en
    # curso queda FALLIDA y su trabajo FALLIDO, sin dueno ni lease, con el motivo. Lo terminado no
    # se toca.
    b = base_en_0008
    motor = b.motor
    modulo = ScriptDirectory.from_config(_alembic()).get_revision("0008").module
    activa, fallida = _insertar(
        motor,
        INSERTAR_HISTORIA,
        _historia(b.dataset_cartera),
        _historia(b.dataset_pagos, "FALLIDA", tipo="PAGOS"),
    )
    _insertar(motor, INSERTAR_TRABAJO_HISTORIA, _trabajo_historia(activa, "EJECUTANDO"))

    with motor.begin() as conexion:
        conexion.execute(modulo.CERRAR_EJECUCIONES)
        conexion.execute(modulo.CERRAR_TRABAJOS)

    with motor.connect() as conexion:
        ejecuciones = conexion.execute(
            text("SELECT id, estado, resultado, detalle FROM ejecucion_historia ORDER BY id")
        ).all()
        trabajo = conexion.execute(
            text(
                "SELECT estado, worker_id, lease_hasta, ultimo_error, terminado_en "
                "FROM trabajo_orquestacion WHERE tipo = 'HISTORIA'"
            )
        ).one()
    assert [tuple(e) for e in ejecuciones] == [
        (activa, "FALLIDA", "CERRADA_AL_BAJAR", _cierre_0008()),
        (fallida, "FALLIDA", "ERROR_INTERNO", None),
    ]
    assert tuple(trabajo)[:4] == ("FALLIDO", None, None, _cierre_0008())
    assert trabajo.terminado_en is not None


# --- la 0009: el motor de pagos -------------------------------------------------------------------

TABLAS_DEL_MOTOR = {
    "ejecucion_motor_pagos",
    "movimiento_economico_canonico",
    "resultado_pago_observado",
}
_DEL_MOTOR = "'ejecucion_motor_pagos', 'movimiento_economico_canonico', 'resultado_pago_observado'"
RESTRICCIONES_0009 = RESTRICCIONES.replace("'ejecucion_decision', 'decision_cuenta'", _DEL_MOTOR)
INDICES_0009 = INDICES.replace(
    "'ejecucion_decision', 'decision_cuenta'", f"{_DEL_MOTOR}, 'pago_observado'"
)
RESTRICCIONES_DEL_MOTOR = {
    "pk_ejecucion_motor_pagos",
    "uq_ejecucion_motor_pagos_motor_pagos_run_id",
    "ck_motor_pagos_periodo",
    "ck_motor_pagos_mes",
    "ck_motor_pagos_resultado",
    "ck_motor_pagos_firma",
    "ck_motor_pagos_conteos",
    "ck_motor_pagos_clases",
    "ck_motor_pagos_movimientos",
    "ck_motor_pagos_publicacion",
    "ck_ejecucion_motor_pagos_estado_motor_pagos",
    "pk_movimiento_economico_canonico",
    "fk_movimiento_ejecucion",
    "fk_movimiento_cuenta",
    "uq_movimiento_ejecucion",
    "ck_movimiento_observaciones",
    "ck_movimiento_monto",
    "ck_movimiento_firma",
    "pk_resultado_pago_observado",
    "fk_resultado_pago_ejecucion",
    "fk_resultado_pago_observado",
    "fk_resultado_pago_movimiento",
    "ck_resultado_pago_firmas",
}
RESTRICCIONES_DEL_MOTOR_EN_LA_COLA = {"fk_trabajo_motor_pagos", "uq_trabajo_motor_pagos"}
VENTANA_0009 = ("despacho_id", "cartera_id", "version_motor", "periodo_desde")
INDICES_DEL_MOTOR = {
    ("ejecucion_motor_pagos", "pk_ejecucion_motor_pagos", True, ("id",), None),
    (
        "ejecucion_motor_pagos",
        "uq_ejecucion_motor_pagos_motor_pagos_run_id",
        True,
        ("motor_pagos_run_id",),
        None,
    ),
    (
        "ejecucion_motor_pagos",
        "ux_ejecucion_motor_pagos_exitosa",
        True,
        (*VENTANA_0009, "firma_entrada"),
        "((estado)::text = 'EXITOSA'::text)",
    ),
    (
        "ejecucion_motor_pagos",
        "ux_ejecucion_motor_pagos_en_proceso",
        True,
        VENTANA_0009,
        "((estado)::text = 'EN_PROCESO'::text)",
    ),
    ("movimiento_economico_canonico", "pk_movimiento_economico_canonico", True, ("id",), None),
    (
        "movimiento_economico_canonico",
        "uq_movimiento_ejecucion",
        True,
        ("ejecucion_motor_pagos_id", "movimiento_id"),
        None,
    ),
    (
        "movimiento_economico_canonico",
        "ix_movimiento_movimiento_id",
        False,
        ("movimiento_id",),
        None,
    ),
    (
        "movimiento_economico_canonico",
        "ix_movimiento_cuenta",
        False,
        ("despacho_id", "cartera_id", "cliente_unico", "fecha_recepcion"),
        None,
    ),
    (
        "resultado_pago_observado",
        "pk_resultado_pago_observado",
        True,
        ("ejecucion_motor_pagos_id", "dataset_conformado_id", "source_row"),
        None,
    ),
    (
        "resultado_pago_observado",
        "ix_resultado_pago_movimiento",
        False,
        ("movimiento_economico_canonico_id",),
        None,
    ),
    # Los pagos observados no cambian: solo ganan los indices de una ventana y de sus negativos.
    (
        "pago_observado",
        "pk_pago_observado",
        True,
        ("dataset_conformado_id", "source_row"),
        None,
    ),
    (
        "pago_observado",
        "ix_pago_observado_cuenta",
        False,
        ("despacho_id", "cartera_id", "cliente_unico", "fecha_recepcion"),
        None,
    ),
    (
        "pago_observado",
        "ix_pago_observado_recepcion",
        False,
        ("despacho_id", "cartera_id", "fecha_recepcion"),
        None,
    ),
    (
        "pago_observado",
        "ix_pago_observado_negativo",
        False,
        ("despacho_id", "cartera_id", "fecha_recepcion"),
        "(recuperacion_por_gestion < (0)::numeric)",
    ),
}
"""Ni un indice mas. Las huellas no tienen indice: un grupo de copias o de la llave historica se
encuentra por los pagos de una cuenta."""

METRICAS_0009 = (
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
INSERTAR_MOTOR = text(
    "INSERT INTO ejecucion_motor_pagos (motor_pagos_run_id, version_motor, despacho_id, "
    "cartera_id, periodo_desde, periodo_hasta, estado, resultado, firma_entrada, iniciada_en, "
    f"{', '.join(METRICAS_0009)}, recuperacion_bruta_interpretada, "
    "recuperacion_neta_interpretada, importe_ambiguo_observado) VALUES (:motor_pagos_run_id, "
    ":version, 'DSP_001', 'CARTERA_PRINCIPAL', :desde, :hasta, :estado, :resultado, :firma, "
    f"now(), {', '.join(':' + m for m in METRICAS_0009)}, 0, 0, 0) RETURNING id"
)
INSERTAR_MOVIMIENTO = text(
    "INSERT INTO movimiento_economico_canonico (observaciones, fecha_recepcion, creado_en, "
    "ejecucion_motor_pagos_id, cuenta_canonica_id, movimiento_id, monto_reportado, "
    "version_motor, despacho_id, cartera_id, cliente_unico, signo_economico, tipo_movimiento, "
    "estado_conciliacion, firma_exacta) VALUES (:observaciones, '2026-09-03 10:15:00', now(), "
    ":ejecucion, :cuenta, :movimiento_id, :monto, 'motor-pagos/v1', 'DSP_001', "
    "'CARTERA_PRINCIPAL', 'CU0000000001', 'SUMA', 'PAGO', 'CONCILIADO_CUENTA', :firma) "
    "RETURNING id"
)
INSERTAR_RESULTADO_PAGO = text(
    "INSERT INTO resultado_pago_observado (ejecucion_motor_pagos_id, dataset_conformado_id, "
    "source_row, movimiento_economico_canonico_id, clasificacion, estado_conciliacion, "
    "firma_exacta, firma_legacy, motivos) VALUES (:ejecucion, :dataset, :fila, :movimiento, "
    "'MOVIMIENTO_PRIMARIO', 'CONCILIADO_CUENTA', :firma, :legacy, "
    'CAST(\'[{"codigo": "OBSERVACION_UNICA"}]\' AS JSONB)) RETURNING source_row'
)
INSERTAR_TRABAJO_MOTOR = text(
    "INSERT INTO trabajo_orquestacion (trabajo_id, tipo, estado, corrida_id, "
    "ejecucion_motor_pagos_id, intentos, max_intentos, creado_en, disponible_desde, worker_id, "
    "lease_hasta, tomado_en) VALUES (:trabajo_id, :tipo, :estado, :corrida_id, :ejecucion, "
    ":intentos, 5, now(), now(), :worker_id, :lease_hasta, now()) RETURNING id"
)
FIRMA_0009 = b"\x01" * 32


def _cierre_0009() -> str:
    return ScriptDirectory.from_config(_alembic()).get_revision("0009").module.CIERRE


def _motor(estado: str = "EN_PROCESO", **cambios) -> dict:
    terminada = estado != "EN_PROCESO"
    return {
        "motor_pagos_run_id": uuid4(),
        "version": "motor-pagos/v1",
        "desde": "2026-09-01",
        "hasta": "2026-10-01",
        "estado": estado,
        "resultado": "ERROR_INTERNO" if terminada else None,
        "firma": "a" * 64 if estado == "EXITOSA" else None,
        **dict.fromkeys(METRICAS_0009, 0),
        **cambios,
    }


def _trabajo_motor(ejecucion: int | None, estado: str = "PENDIENTE", **cambios) -> dict:
    ejecutando = estado == "EJECUTANDO"
    return {
        "trabajo_id": uuid4(),
        "tipo": "MOTOR_PAGOS",
        "estado": estado,
        "corrida_id": None,
        "ejecucion": ejecucion,
        "intentos": 1 if ejecutando else 0,
        "worker_id": "worker-de-prueba:1:abc" if ejecutando else None,
        "lease_hasta": datetime.now(UTC) + timedelta(minutes=1) if ejecutando else None,
        **cambios,
    }


def _historicas(motor: Engine) -> dict[str, list[dict]]:
    """Lo que la 0009 no puede tocar del modelo historico, completo y en orden de su llave."""
    llaves = {
        "cuenta_canonica": "id",
        "corte_canonico": "id",
        "ejecucion_historia": "id",
        "snapshot_cuenta": "corte_canonico_id, cuenta_canonica_id",
        "pago_observado": "dataset_conformado_id, source_row",
    }
    with motor.connect() as conexion:
        return {
            tabla: [
                dict(fila)
                for fila in conexion.execute(text(f"SELECT * FROM {tabla} ORDER BY {orden}"))
                .mappings()
                .all()
            ]
            for tabla, orden in llaves.items()
        }


@dataclass(frozen=True)
class BaseEn0009:
    """Una base de v0.7 con su modelo historico materializado (una cuenta, un corte, un snapshot y
    dos pagos observados), subida a la 0009: ninguna interpretacion, lista para el backfill."""

    motor: Engine
    base: BaseEn0008
    cuenta: int
    previas: dict[str, list[dict]]
    historicas: dict[str, list[dict]]


@pytest.fixture
def base_en_0009(base_en_0008) -> BaseEn0009:
    b = base_en_0008
    motor = b.motor
    (cuenta,) = _insertar(
        motor,
        INSERTAR_CUENTA_CANONICA,
        {"cuenta_id": uuid4(), "cartera": "CARTERA_PRINCIPAL", "cliente": "CU0000000001"},
    )
    (corte,) = _insertar(
        motor,
        INSERTAR_CORTE,
        {
            "corte_id": uuid4(),
            "fecha": "2026-09-02",
            "firma": "e" * 64,
            "cuentas": 1,
            "dataset": b.dataset_cartera,
        },
    )
    _insertar(
        motor,
        INSERTAR_SNAPSHOT,
        {"corte": corte, "cuenta": cuenta, "fecha": "2026-09-02", "fila": 2},
    )
    pago = {"dataset": b.dataset_pagos, "ingesta": b.ingesta_id}
    _insertar(
        motor,
        INSERTAR_PAGO_OBSERVADO,
        {**pago, "fila": 2, "id_publico": uuid4()},
        {**pago, "fila": 3, "id_publico": uuid4()},
    )
    previas = _por_columna(motor, PREVIAS_A_LA_0008)
    historicas = _historicas(motor)

    command.upgrade(_alembic(), "0009")
    return BaseEn0009(motor, b, cuenta, previas, historicas)


def test_la_0009_baja_de_la_0008():
    scripts = ScriptDirectory.from_config(_alembic())

    assert scripts.get_revision("0009").down_revision == "0008"


def test_los_modelos_declaran_el_esquema_de_la_0009():
    tablas = (
        EjecucionMotorPagos.__table__,
        MovimientoEconomicoCanonico.__table__,
        ResultadoPagoObservado.__table__,
    )
    preparador = postgresql.dialect().identifier_preparer
    nombres = {preparador.format_constraint(c) for t in tablas for c in t.constraints}
    assert nombres == RESTRICCIONES_DEL_MOTOR
    assert all(len(nombre) <= 63 for nombre in nombres)
    # El resultado no tiene id propio: su llave es la ejecucion y la de su observacion.
    assert [c.name for c in ResultadoPagoObservado.__table__.primary_key] == [
        "ejecucion_motor_pagos_id",
        "dataset_conformado_id",
        "source_row",
    ]
    # El movimiento, si: los resultados lo citan con 8 bytes, y su movimiento_id no es unico en la
    # tabla, sino en cada ejecucion.
    assert isinstance(MovimientoEconomicoCanonico.__table__.c.id.type, BigInteger)
    trabajo = TrabajoOrquestacion.__table__
    assert "MOTOR_PAGOS" in trabajo.c.tipo.type.enums
    assert trabajo.c.ejecucion_motor_pagos_id.nullable is True
    llaves = {preparador.format_constraint(c) for c in trabajo.constraints}
    assert RESTRICCIONES_DEL_MOTOR_EN_LA_COLA <= llaves


def test_la_0009_agrega_el_motor_vacio_y_no_toca_ningun_pago_observado(base_en_0009):
    b = base_en_0009
    motor = b.motor

    # A. Cada fila de antes sigue ahi, identica, empezando por los pagos observados. Un trabajo
    # solo gana su ejecucion del motor, que no tiene: la migracion no interpreta nada.
    assert _historicas(motor) == b.historicas
    assert len(b.historicas["pago_observado"]) == 2
    despues = _por_columna(motor, PREVIAS_A_LA_0008)
    for tabla, filas in b.previas.items():
        assert len(despues[tabla]) == len(filas), tabla
        for antes, fila in zip(filas, despues[tabla], strict=True):
            fila = dict(fila)
            if tabla == "trabajo_orquestacion":
                assert fila.pop("ejecucion_motor_pagos_id") is None
            assert fila == antes, tabla

    # B. Las tablas nuevas, vacias, con sus restricciones y sus indices por nombre.
    assert TABLAS_DEL_MOTOR <= set(_consultar(motor, TABLAS))
    for tabla in sorted(TABLAS_DEL_MOTOR):
        assert _consultar(motor, f"SELECT count(*) FROM {tabla}") == [0]
    assert set(_consultar(motor, RESTRICCIONES_0009)) == RESTRICCIONES_DEL_MOTOR
    with motor.connect() as conexion:
        indices = {
            (tabla, nombre, unico, tuple(columnas), predicado)
            for tabla, nombre, unico, columnas, predicado in conexion.execute(text(INDICES_0009))
        }
    assert indices == INDICES_DEL_MOTOR
    assert _consultar(motor, "SELECT version_num FROM alembic_version") == ["0009"]


def test_una_exitosa_por_ventana_y_firma_y_un_intento_activo_por_ventana(base_en_0009):
    motor = base_en_0009.motor
    publicada = {"resultado": "INTERPRETACION_PUBLICADA"}
    _insertar(motor, INSERTAR_MOTOR, _motor("EXITOSA", **publicada))
    _rechaza(
        motor, "ux_ejecucion_motor_pagos_exitosa", INSERTAR_MOTOR, _motor("EXITOSA", **publicada)
    )
    # Otra EXITOSA de la misma ventana con otra firma: otra interpretacion, de entradas nuevas.
    _insertar(motor, INSERTAR_MOTOR, _motor("EXITOSA", firma="b" * 64, **publicada))
    # Y otra version, u otra ventana, con la misma firma.
    _insertar(motor, INSERTAR_MOTOR, _motor("EXITOSA", version="motor-pagos/v2", **publicada))
    _insertar(motor, INSERTAR_MOTOR, _motor())
    _rechaza(motor, "ux_ejecucion_motor_pagos_en_proceso", INSERTAR_MOTOR, _motor())
    _insertar(motor, INSERTAR_MOTOR, _motor(desde="2026-10-01", hasta="2026-11-01"))
    # Las FALLIDA son historia, tantas como haya.
    _insertar(motor, INSERTAR_MOTOR, _motor("FALLIDA"), _motor("FALLIDA", firma="c" * 64))
    for restriccion, ejecucion in (
        # La ventana de v1 es un mes calendario.
        ("ck_motor_pagos_mes", _motor(desde="2026-09-02", hasta="2026-10-02")),
        ("ck_motor_pagos_mes", _motor(desde="2026-11-01", hasta="2026-11-30")),
        ("ck_motor_pagos_periodo", _motor(version="motor-pagos/v2", hasta="2026-08-01")),
        # Una terminada dice como termino; una EN_PROCESO, todavia no.
        ("ck_motor_pagos_resultado", _motor("FALLIDA", resultado=None)),
        (
            "ck_motor_pagos_resultado",
            _motor(resultado="ERROR_INTERNO", desde="2026-12-01", hasta="2027-01-01"),
        ),
        # Una EXITOSA dice que leyo; una firma es un SHA-256.
        ("ck_motor_pagos_firma", _motor("EXITOSA", firma=None, **publicada)),
        ("ck_motor_pagos_firma", _motor("FALLIDA", firma="x")),
        # Cada observacion tiene una clase, y cada movimiento lo funda una observacion.
        (
            "ck_motor_pagos_clases",
            _motor("FALLIDA", observaciones_clasificadas=2, primarios=1),
        ),
        (
            "ck_motor_pagos_movimientos",
            _motor("FALLIDA", movimientos_canonicos=1),
        ),
        ("ck_motor_pagos_conteos", _motor("FALLIDA", grupos_legacy=-1)),
        # Una EXITOSA clasifico todo lo que leyo; una FALLIDA no publico nada.
        (
            "ck_motor_pagos_publicacion",
            _motor("EXITOSA", firma="d" * 64, observaciones_leidas=3, **publicada),
        ),
        (
            "ck_motor_pagos_publicacion",
            _motor(
                "FALLIDA",
                observaciones_clasificadas=1,
                primarios=1,
                movimientos_canonicos=1,
                observaciones_leidas=1,
            ),
        ),
        ("ck_ejecucion_motor_pagos_estado_motor_pagos", _motor("PUBLICADA")),
    ):
        _rechaza(motor, restriccion, INSERTAR_MOTOR, ejecucion)


def test_un_resultado_por_observacion_y_un_movimiento_por_ejecucion(base_en_0009):
    b = base_en_0009
    motor = b.motor
    # Una cuenta sin snapshots: la de un cliente cuyo corte todavia no se materializa.
    (sin_snapshots,) = _insertar(
        motor,
        INSERTAR_CUENTA_CANONICA,
        {"cuenta_id": uuid4(), "cartera": "CARTERA_PRINCIPAL", "cliente": "CU0000000002"},
    )
    ejecucion, otra = _insertar(
        motor,
        INSERTAR_MOTOR,
        _motor(
            "EXITOSA",
            resultado="INTERPRETACION_PUBLICADA",
            observaciones_leidas=1,
            observaciones_clasificadas=1,
            primarios=1,
            movimientos_canonicos=1,
        ),
        _motor(desde="2026-10-01", hasta="2026-11-01"),
    )
    movimiento_id = uuid4()
    mov = {
        "observaciones": 1,
        "ejecucion": ejecucion,
        "cuenta": b.cuenta,
        "movimiento_id": movimiento_id,
        "monto": Decimal("-150.00"),
        "firma": FIRMA_0009,
    }
    (movimiento,) = _insertar(motor, INSERTAR_MOVIMIENTO, mov)
    # El mismo movimiento una vez por ejecucion; en otra interpretacion, otra vez.
    _rechaza(motor, "uq_movimiento_ejecucion", INSERTAR_MOVIMIENTO, mov)
    _insertar(motor, INSERTAR_MOVIMIENTO, {**mov, "ejecucion": otra, "cuenta": sin_snapshots})
    for restriccion, cambios in (
        ("ck_movimiento_monto", {"movimiento_id": uuid4(), "monto": Decimal("0.00")}),
        ("ck_movimiento_observaciones", {"movimiento_id": uuid4(), "observaciones": 0}),
        ("ck_movimiento_firma", {"movimiento_id": uuid4(), "firma": b"\x01" * 31}),
        ("fk_movimiento_ejecucion", {"movimiento_id": uuid4(), "ejecucion": 999_999}),
        ("fk_movimiento_cuenta", {"movimiento_id": uuid4(), "cuenta": 999_999}),
    ):
        _rechaza(motor, restriccion, INSERTAR_MOVIMIENTO, {**mov, **cambios})

    dataset = b.base.dataset_pagos
    resultado = {
        "ejecucion": ejecucion,
        "dataset": dataset,
        "fila": 2,
        "movimiento": movimiento,
        "firma": FIRMA_0009,
        "legacy": FIRMA_0009,
    }
    _insertar(motor, INSERTAR_RESULTADO_PAGO, resultado)
    # Una observacion, un resultado por ejecucion; la otra observacion, el suyo.
    _rechaza(motor, "pk_resultado_pago_observado", INSERTAR_RESULTADO_PAGO, resultado)
    _insertar(motor, INSERTAR_RESULTADO_PAGO, {**resultado, "fila": 3, "movimiento": None})
    for restriccion, cambios in (
        ("fk_resultado_pago_observado", {"ejecucion": otra, "fila": 99}),
        ("fk_resultado_pago_ejecucion", {"ejecucion": 999_999}),
        ("fk_resultado_pago_movimiento", {"ejecucion": otra, "movimiento": 999_999}),
        ("ck_resultado_pago_firmas", {"ejecucion": otra, "legacy": b"\x02"}),
    ):
        _rechaza(motor, restriccion, INSERTAR_RESULTADO_PAGO, {**resultado, **cambios})
    # Y nada se borra en cascada: ni la observacion de un resultado, ni su movimiento, ni la
    # ejecucion de un movimiento, ni la cuenta con que se concilio.
    for restriccion, borrar, parametros in (
        (
            "fk_resultado_pago_observado",
            "DELETE FROM pago_observado WHERE dataset_conformado_id = :d AND source_row = 2",
            {"d": dataset},
        ),
        (
            "fk_resultado_pago_movimiento",
            "DELETE FROM movimiento_economico_canonico WHERE id = :id",
            {"id": movimiento},
        ),
        (
            "fk_movimiento_ejecucion",
            "DELETE FROM ejecucion_motor_pagos WHERE id = :id",
            {"id": otra},
        ),
        (
            "fk_movimiento_cuenta",
            "DELETE FROM cuenta_canonica WHERE id = :id",
            {"id": sin_snapshots},
        ),
    ):
        _rechaza(motor, restriccion, text(borrar), parametros)


def test_el_trabajo_motor_pagos_apunta_a_su_ejecucion_y_a_nada_mas(base_en_0009):
    b = base_en_0009
    motor = b.motor
    (ejecucion,) = _insertar(motor, INSERTAR_MOTOR, _motor())
    _insertar(motor, INSERTAR_TRABAJO_MOTOR, _trabajo_motor(ejecucion))
    _rechaza(motor, "uq_trabajo_motor_pagos", INSERTAR_TRABAJO_MOTOR, _trabajo_motor(ejecucion))
    _rechaza(motor, "ck_trabajo_objetivo", INSERTAR_TRABAJO_MOTOR, _trabajo_motor(None))
    _rechaza(
        motor,
        "ck_trabajo_objetivo",
        INSERTAR_TRABAJO_MOTOR,
        _trabajo_motor(ejecucion, tipo="INGESTA", corrida_id=b.base.corrida_id),
    )
    _rechaza(motor, "fk_trabajo_motor_pagos", INSERTAR_TRABAJO_MOTOR, _trabajo_motor(999_999))
    _rechaza(
        motor,
        "fk_trabajo_motor_pagos",
        text("DELETE FROM ejecucion_motor_pagos WHERE id = :id"),
        {"id": ejecucion},
    )


def test_la_0009_baja_cerrando_la_interpretacion_en_curso_y_vuelve_a_subir(base_en_0009):
    b = base_en_0009
    motor = b.motor
    # Interpretacion en curso: una ejecucion que un worker tiene y otra en la cola; y una ya
    # publicada, con un movimiento y un resultado.
    activa, pendiente, publicada = _insertar(
        motor,
        INSERTAR_MOTOR,
        _motor(),
        _motor(desde="2026-10-01", hasta="2026-11-01"),
        _motor(
            "EXITOSA",
            desde="2026-08-01",
            hasta="2026-09-01",
            resultado="INTERPRETACION_PUBLICADA",
            observaciones_leidas=1,
            observaciones_clasificadas=1,
            primarios=1,
            movimientos_canonicos=1,
        ),
    )
    _insertar(
        motor,
        INSERTAR_TRABAJO_MOTOR,
        _trabajo_motor(activa, "EJECUTANDO"),
        _trabajo_motor(pendiente),
    )
    (movimiento,) = _insertar(
        motor,
        INSERTAR_MOVIMIENTO,
        {
            "observaciones": 1,
            "ejecucion": publicada,
            "cuenta": b.cuenta,
            "movimiento_id": uuid4(),
            "monto": Decimal("-150.00"),
            "firma": FIRMA_0009,
        },
    )
    _insertar(
        motor,
        INSERTAR_RESULTADO_PAGO,
        {
            "ejecucion": publicada,
            "dataset": b.base.dataset_pagos,
            "fila": 2,
            "movimiento": movimiento,
            "firma": FIRMA_0009,
            "legacy": FIRMA_0009,
        },
    )
    previas = _por_columna(motor, PREVIAS_A_LA_0008)
    trabajos_antes = [f for f in previas["trabajo_orquestacion"] if f["tipo"] != "MOTOR_PAGOS"]
    assert len(previas["trabajo_orquestacion"]) == len(trabajos_antes) + 2

    command.downgrade(_alembic(), "0008")

    # La interpretacion entera se fue, con sus trabajos. Lo demas, intacto: los pagos observados,
    # las cuentas canonicas, los cortes, los snapshots, los datasets, sus artefactos, las corridas y
    # las ingestas de pagos; los otros trabajos, sin su nueva columna.
    assert not TABLAS_DEL_MOTOR & set(_consultar(motor, TABLAS))
    assert _historicas(motor) == b.historicas
    despues = _por_columna(motor, PREVIAS_A_LA_0008)
    for tabla, filas in previas.items():
        if tabla == "trabajo_orquestacion":
            esperadas = [
                {k: v for k, v in f.items() if k != "ejecucion_motor_pagos_id"}
                for f in trabajos_antes
            ]
            assert despues[tabla] == esperadas
        else:
            assert despues[tabla] == filas, tabla
    definiciones = _consultar(
        motor,
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname IN "
        "('ck_trabajo_objetivo', 'ck_trabajo_orquestacion_tipo_trabajo')",
    )
    assert len(definiciones) == 2
    assert not any("MOTOR_PAGOS" in d or "ejecucion_motor_pagos_id" in d for d in definiciones)
    with motor.connect() as conexion:
        indices = {
            nombre
            for _, nombre, *_ in conexion.execute(
                text(INDICES.replace("'ejecucion_decision', 'decision_cuenta'", "'pago_observado'"))
            )
        }
    assert indices == {"pk_pago_observado", "ix_pago_observado_cuenta"}
    assert _consultar(motor, "SELECT version_num FROM alembic_version") == ["0008"]

    # Y vuelve a subir limpia: el motor vacio, listo para el backfill.
    command.upgrade(_alembic(), "0009")

    for tabla in sorted(TABLAS_DEL_MOTOR):
        assert _consultar(motor, f"SELECT count(*) FROM {tabla}") == [0]
    assert set(_consultar(motor, RESTRICCIONES_0009)) == RESTRICCIONES_DEL_MOTOR
    assert _historicas(motor) == b.historicas
    assert _consultar(motor, "SELECT version_num FROM alembic_version") == ["0009"]


def test_al_bajar_la_0009_lo_primero_es_cerrar_la_interpretacion_en_curso(base_en_0009):
    # Lo que la 0009 hace antes de quitar sus tablas, con sus mismas sentencias: la ejecucion en
    # curso queda FALLIDA y su trabajo FALLIDO, sin dueno ni lease, con el motivo. Lo terminado no
    # se toca.
    motor = base_en_0009.motor
    modulo = ScriptDirectory.from_config(_alembic()).get_revision("0009").module
    activa, fallida = _insertar(
        motor,
        INSERTAR_MOTOR,
        _motor(),
        _motor("FALLIDA", desde="2026-10-01", hasta="2026-11-01"),
    )
    _insertar(motor, INSERTAR_TRABAJO_MOTOR, _trabajo_motor(activa, "EJECUTANDO"))

    with motor.begin() as conexion:
        conexion.execute(modulo.CERRAR_EJECUCIONES)
        conexion.execute(modulo.CERRAR_TRABAJOS)

    with motor.connect() as conexion:
        ejecuciones = conexion.execute(
            text("SELECT id, estado, resultado, detalle FROM ejecucion_motor_pagos ORDER BY id")
        ).all()
        trabajo = conexion.execute(
            text(
                "SELECT estado, worker_id, lease_hasta, ultimo_error, terminado_en "
                "FROM trabajo_orquestacion WHERE tipo = 'MOTOR_PAGOS'"
            )
        ).one()
    assert [tuple(e) for e in ejecuciones] == [
        (activa, "FALLIDA", "CERRADA_AL_BAJAR", _cierre_0009()),
        (fallida, "FALLIDA", "ERROR_INTERNO", None),
    ]
    assert tuple(trabajo)[:4] == ("FALLIDO", None, None, _cierre_0009())
    assert trabajo.terminado_en is not None


# --- la 0010: el lifecycle de cobranza, la atribucion y la evaluacion de promesas -----------------

TABLAS_DEL_LIFECYCLE = (
    "evento_lifecycle",
    "gestion_cobranza",
    "visita_campo",
    "promesa_pago",
    "convenio_cobranza",
    "cuota_convenio",
)
TABLAS_DE_LA_0010 = {
    *TABLAS_DEL_LIFECYCLE,
    "ejecucion_atribucion",
    "atribucion_movimiento",
    "candidato_atribucion",
    "ejecucion_evaluacion_promesas",
    "evaluacion_promesa",
}
_DE_LA_0010 = ", ".join(f"'{tabla}'" for tabla in sorted(TABLAS_DE_LA_0010))
RESTRICCIONES_0010 = RESTRICCIONES.replace("'ejecucion_decision', 'decision_cuenta'", _DE_LA_0010)
INDICES_0010 = INDICES.replace("'ejecucion_decision', 'decision_cuenta'", _DE_LA_0010)
RESTRICCIONES_DE_LA_0010 = {
    "pk_evento_lifecycle",
    "uq_evento_lifecycle_evento_id",
    "uq_evento_idempotencia",
    "fk_evento_cuenta",
    "fk_evento_relacionado",
    "ck_evento_relacionado",
    "ck_evento_motivo",
    "ck_evento_tiempos",
    "ck_evento_huella",
    "ck_evento_llave",
    "ck_evento_actor",
    "ck_evento_lifecycle_tipo_evento_lifecycle",
    "ck_evento_lifecycle_origen_registro",
    "pk_gestion_cobranza",
    "uq_gestion_cobranza_gestion_id",
    "uq_gestion_evento",
    "fk_gestion_evento",
    "fk_gestion_cuenta",
    "ck_gestion_medio",
    "ck_gestion_coherencia",
    "ck_gestion_actor",
    "ck_gestion_cobranza_canal_gestion",
    "ck_gestion_cobranza_medio_gestion",
    "ck_gestion_cobranza_nivel_contacto",
    "ck_gestion_cobranza_resultado_gestion",
    "pk_visita_campo",
    "uq_visita_campo_visita_id",
    "uq_visita_gestion",
    "fk_visita_gestion",
    "ck_visita_intervalo",
    "ck_visita_campo_resultado_visita",
    "pk_promesa_pago",
    "uq_promesa_pago_promesa_id",
    "uq_promesa_evento",
    "uq_promesa_gestion",
    "fk_promesa_evento",
    "fk_promesa_gestion",
    "fk_promesa_cuenta",
    "ck_promesa_monto",
    "pk_convenio_cobranza",
    "uq_convenio_cobranza_convenio_id",
    "uq_convenio_evento",
    "uq_convenio_gestion",
    "fk_convenio_evento",
    "fk_convenio_gestion",
    "fk_convenio_cuenta",
    "ck_convenio_monto",
    "ck_convenio_vigencia",
    "ck_convenio_cuotas",
    "pk_cuota_convenio",
    "fk_cuota_convenio",
    "ck_cuota_monto",
    "ck_cuota_numero",
    "pk_ejecucion_atribucion",
    "uq_ejecucion_atribucion_atribucion_run_id",
    "fk_atribucion_motor_pagos",
    "ck_atribucion_periodo",
    "ck_atribucion_mes",
    "ck_atribucion_ventana_dias",
    "ck_atribucion_resultado",
    "ck_atribucion_firma",
    "ck_atribucion_conteos",
    "ck_atribucion_montos",
    "ck_atribucion_publicacion",
    "ck_ejecucion_atribucion_estado_atribucion",
    "pk_atribucion_movimiento",
    "fk_atribucion_movimiento_ejecucion",
    "fk_atribucion_movimiento_movimiento",
    "fk_atribucion_movimiento_gestion",
    "fk_atribucion_movimiento_cuenta",
    "ck_atribucion_movimiento_clase",
    "pk_candidato_atribucion",
    "fk_candidato_atribucion",
    "fk_candidato_gestion",
    "ck_candidato_antelacion",
    "pk_ejecucion_evaluacion_promesas",
    "uq_ejecucion_evaluacion_promesas_evaluacion_run_id",
    "ck_evaluacion_resultado",
    "ck_evaluacion_firma",
    "ck_evaluacion_conteos",
    "ck_evaluacion_clases",
    "ck_evaluacion_publicacion",
    "ck_ejecucion_evaluacion_promesas_estado_evaluacion_promesas",
    "pk_evaluacion_promesa",
    "fk_evaluacion_promesa_ejecucion",
    "fk_evaluacion_promesa_promesa",
    "ck_evaluacion_promesa_conteos",
}
RESTRICCIONES_DE_LA_0010_EN_LA_COLA = {
    "fk_trabajo_atribucion",
    "uq_trabajo_atribucion",
    "fk_trabajo_evaluacion",
    "uq_trabajo_evaluacion",
}
VENTANA_0010 = ("despacho_id", "cartera_id", "version_atribucion", "periodo_desde")
CORTE_0010 = ("despacho_id", "cartera_id", "version_evaluacion", "as_of")
INDICES_DE_LA_0010 = {
    ("evento_lifecycle", "pk_evento_lifecycle", True, ("id",), None),
    ("evento_lifecycle", "uq_evento_lifecycle_evento_id", True, ("evento_id",), None),
    (
        "evento_lifecycle",
        "uq_evento_idempotencia",
        True,
        ("despacho_id", "cartera_id", "idempotency_key"),
        None,
    ),
    (
        "evento_lifecycle",
        "ux_evento_relacionado",
        True,
        ("evento_relacionado_id", "tipo_evento"),
        "(evento_relacionado_id IS NOT NULL)",
    ),
    (
        "evento_lifecycle",
        "ix_evento_cuenta_ocurrido",
        False,
        ("cuenta_canonica_id", "ocurrido_en"),
        None,
    ),
    ("gestion_cobranza", "pk_gestion_cobranza", True, ("id",), None),
    ("gestion_cobranza", "uq_gestion_cobranza_gestion_id", True, ("gestion_id",), None),
    ("gestion_cobranza", "uq_gestion_evento", True, ("evento_lifecycle_id",), None),
    (
        "gestion_cobranza",
        "ix_gestion_cuenta_ocurrido",
        False,
        ("cuenta_canonica_id", "ocurrido_en"),
        None,
    ),
    ("visita_campo", "pk_visita_campo", True, ("id",), None),
    ("visita_campo", "uq_visita_campo_visita_id", True, ("visita_id",), None),
    ("visita_campo", "uq_visita_gestion", True, ("gestion_cobranza_id",), None),
    ("promesa_pago", "pk_promesa_pago", True, ("id",), None),
    ("promesa_pago", "uq_promesa_pago_promesa_id", True, ("promesa_id",), None),
    ("promesa_pago", "uq_promesa_evento", True, ("evento_lifecycle_id",), None),
    ("promesa_pago", "uq_promesa_gestion", True, ("gestion_cobranza_id",), None),
    (
        "promesa_pago",
        "ix_promesa_cuenta_limite",
        False,
        ("cuenta_canonica_id", "fecha_limite"),
        None,
    ),
    ("convenio_cobranza", "pk_convenio_cobranza", True, ("id",), None),
    ("convenio_cobranza", "uq_convenio_cobranza_convenio_id", True, ("convenio_id",), None),
    ("convenio_cobranza", "uq_convenio_evento", True, ("evento_lifecycle_id",), None),
    ("convenio_cobranza", "uq_convenio_gestion", True, ("gestion_cobranza_id",), None),
    (
        "convenio_cobranza",
        "ix_convenio_cuenta_inicio",
        False,
        ("cuenta_canonica_id", "fecha_inicio"),
        None,
    ),
    ("cuota_convenio", "pk_cuota_convenio", True, ("convenio_cobranza_id", "numero"), None),
    ("ejecucion_atribucion", "pk_ejecucion_atribucion", True, ("id",), None),
    (
        "ejecucion_atribucion",
        "uq_ejecucion_atribucion_atribucion_run_id",
        True,
        ("atribucion_run_id",),
        None,
    ),
    (
        "ejecucion_atribucion",
        "ux_ejecucion_atribucion_exitosa",
        True,
        (*VENTANA_0010, "firma_entrada"),
        "((estado)::text = 'EXITOSA'::text)",
    ),
    (
        "ejecucion_atribucion",
        "ux_ejecucion_atribucion_en_proceso",
        True,
        VENTANA_0010,
        "((estado)::text = 'EN_PROCESO'::text)",
    ),
    (
        "atribucion_movimiento",
        "pk_atribucion_movimiento",
        True,
        ("ejecucion_atribucion_id", "movimiento_economico_canonico_id"),
        None,
    ),
    (
        "atribucion_movimiento",
        "ix_atribucion_movimiento_movimiento_id",
        False,
        ("movimiento_id",),
        None,
    ),
    (
        "candidato_atribucion",
        "pk_candidato_atribucion",
        True,
        ("ejecucion_atribucion_id", "movimiento_economico_canonico_id", "gestion_cobranza_id"),
        None,
    ),
    ("ejecucion_evaluacion_promesas", "pk_ejecucion_evaluacion_promesas", True, ("id",), None),
    (
        "ejecucion_evaluacion_promesas",
        "uq_ejecucion_evaluacion_promesas_evaluacion_run_id",
        True,
        ("evaluacion_run_id",),
        None,
    ),
    (
        "ejecucion_evaluacion_promesas",
        "ux_evaluacion_promesas_exitosa",
        True,
        (*CORTE_0010, "firma_entrada"),
        "((estado)::text = 'EXITOSA'::text)",
    ),
    (
        "ejecucion_evaluacion_promesas",
        "ux_evaluacion_promesas_en_proceso",
        True,
        CORTE_0010,
        "((estado)::text = 'EN_PROCESO'::text)",
    ),
    (
        "evaluacion_promesa",
        "pk_evaluacion_promesa",
        True,
        ("ejecucion_evaluacion_promesas_id", "promesa_pago_id"),
        None,
    ),
    ("evaluacion_promesa", "ix_evaluacion_promesa_promesa", False, ("promesa_pago_id",), None),
}
"""Ni un indice mas: los de cada consulta que existe, medidos en el benchmark."""

TRIGGERS = (
    "SELECT c.relname, t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
    "WHERE t.tgname LIKE 'tr\\_%'"
)
TRIGGERS_DE_LA_0010 = {
    *((tabla, f"tr_{tabla}_solo_agrega") for tabla in TABLAS_DEL_LIFECYCLE),
    ("convenio_cobranza", "tr_convenio_cuotas"),
    ("cuota_convenio", "tr_cuota_convenio"),
}
FUNCIONES_DE_LA_0010 = {"lifecycle_solo_agrega", "convenio_cuotas_coherentes"}
FUNCIONES = "SELECT proname FROM pg_proc WHERE pronamespace = 'public'::regnamespace"

INSERTAR_EVENTO = text(
    "INSERT INTO evento_lifecycle (ocurrido_en, registrado_en, evento_relacionado_id, evento_id, "
    "cuenta_canonica_id, tipo_evento, origen_registro, despacho_id, cartera_id, version_evento, "
    "idempotency_key, actor_ref, motivo, payload_hash) VALUES (:ocurrido, :registrado, "
    ":relacionado, :evento_id, :cuenta, :tipo, :origen, 'DSP_001', :cartera, 'lifecycle/v1', "
    ":llave, :actor, :motivo, :huella) RETURNING id"
)
INSERTAR_GESTION = text(
    "INSERT INTO gestion_cobranza (ocurrido_en, evento_lifecycle_id, gestion_id, "
    "cuenta_canonica_id, canal, medio, nivel_contacto, resultado, actor_ref, observacion) VALUES "
    "('2026-09-02 10:00:00+00', :evento, :gestion_id, :cuenta, :canal, :medio, :nivel, "
    ":resultado, :actor, NULL) RETURNING id"
)
INSERTAR_VISITA = text(
    "INSERT INTO visita_campo (inicio, fin, gestion_cobranza_id, visita_id, resultado) VALUES "
    "(:inicio, :fin, :gestion, :visita_id, :resultado) RETURNING id"
)
INSERTAR_PROMESA = text(
    "INSERT INTO promesa_pago (evento_lifecycle_id, gestion_cobranza_id, fecha_limite, "
    "promesa_id, cuenta_canonica_id, monto_prometido, version_modelo) VALUES (:evento, :gestion, "
    "'2026-09-15', :promesa_id, :cuenta, :monto, 'lifecycle/v1') RETURNING id"
)
INSERTAR_CONVENIO = text(
    "INSERT INTO convenio_cobranza (evento_lifecycle_id, gestion_cobranza_id, fecha_inicio, "
    "fecha_fin, convenio_id, cuenta_canonica_id, cuotas, monto_total_acordado, version_modelo) "
    "VALUES (:evento, :gestion, '2026-09-01', :fin, :convenio_id, :cuenta, :cuotas, :monto, "
    "'lifecycle/v1') RETURNING id"
)
INSERTAR_CUOTA = text(
    "INSERT INTO cuota_convenio (convenio_cobranza_id, numero, fecha_vencimiento, monto) VALUES "
    "((SELECT id FROM convenio_cobranza WHERE convenio_id = :convenio_id), :numero, :fecha, "
    ":monto)"
)
METRICAS_ATRIBUCION_0010 = (
    "movimientos_evaluados",
    "asociados",
    "ambiguos",
    "sin_candidato",
    "candidatos",
    "movimientos_anulados",
    "gestiones_leidas",
    "gestiones_anuladas",
)
INSERTAR_ATRIBUCION = text(
    "INSERT INTO ejecucion_atribucion (atribucion_run_id, version_atribucion, despacho_id, "
    "cartera_id, periodo_desde, periodo_hasta, ventana_dias, zona_horaria, estado, resultado, "
    f"firma_entrada, ejecucion_motor_pagos_id, iniciada_en, {', '.join(METRICAS_ATRIBUCION_0010)}, "
    "monto_asociado, monto_ambiguo, monto_sin_candidato, monto_anulado) VALUES "
    "(:atribucion_run_id, :version, 'DSP_001', 'CARTERA_PRINCIPAL', :desde, :hasta, :ventana, "
    "'America/Mexico_City', :estado, :resultado, :firma, :motor, now(), "
    f"{', '.join(':' + m for m in METRICAS_ATRIBUCION_0010)}, :monto, 0, 0, 0) RETURNING id"
)
INSERTAR_ATRIBUCION_MOVIMIENTO = text(
    "INSERT INTO atribucion_movimiento (ejecucion_atribucion_id, "
    "movimiento_economico_canonico_id, fecha_recepcion, gestion_cobranza_id, movimiento_id, "
    "cuenta_canonica_id, candidatos, monto, anulado_por_reverso, clasificacion, motivos) VALUES "
    "(:ejecucion, :movimiento, '2026-09-03 10:15:00', :gestion, :movimiento_id, :cuenta, "
    ":candidatos, 150.00, false, :clasificacion, CAST('[]' AS JSONB)) "
    "RETURNING movimiento_economico_canonico_id"
)
INSERTAR_CANDIDATO = text(
    "INSERT INTO candidato_atribucion (ejecucion_atribucion_id, movimiento_economico_canonico_id, "
    "gestion_cobranza_id, antelacion_segundos) VALUES (:ejecucion, :movimiento, :gestion, "
    ":antelacion) RETURNING gestion_cobranza_id"
)
METRICAS_EVALUACION_0010 = (
    "promesas_evaluadas",
    "pendientes",
    "cumplidas",
    "parciales",
    "incumplidas",
    "canceladas",
    "no_evaluables",
)
INSERTAR_EVALUACION = text(
    "INSERT INTO ejecucion_evaluacion_promesas (evaluacion_run_id, version_evaluacion, "
    "despacho_id, cartera_id, as_of, zona_horaria, estado, resultado, firma_entrada, iniciada_en, "
    f"{', '.join(METRICAS_EVALUACION_0010)}, monto_prometido, monto_observado) VALUES "
    "(:evaluacion_run_id, 'evaluacion-promesa/v1', 'DSP_001', 'CARTERA_PRINCIPAL', :as_of, "
    "'America/Mexico_City', :estado, :resultado, :firma, now(), "
    f"{', '.join(':' + m for m in METRICAS_EVALUACION_0010)}, 0, 0) RETURNING id"
)
INSERTAR_EVALUACION_PROMESA = text(
    "INSERT INTO evaluacion_promesa (ejecucion_evaluacion_promesas_id, promesa_pago_id, "
    "movimientos_compatibles, monto_observado, estado, motivos) VALUES (:ejecucion, :promesa, "
    ":movimientos, :monto, 'CUMPLIDA', CAST('[]' AS JSONB)) RETURNING promesa_pago_id"
)
INSERTAR_TRABAJO_0010 = text(
    "INSERT INTO trabajo_orquestacion (trabajo_id, tipo, estado, corrida_id, "
    "ejecucion_atribucion_id, ejecucion_evaluacion_promesas_id, intentos, max_intentos, "
    "creado_en, disponible_desde, worker_id, lease_hasta, tomado_en) VALUES (:trabajo_id, :tipo, "
    ":estado, :corrida_id, :atribucion, :evaluacion, :intentos, 5, now(), now(), :worker_id, "
    ":lease_hasta, now()) RETURNING id"
)
HUELLA = b"\x07" * 32


def _modulo_0010():
    return ScriptDirectory.from_config(_alembic()).get_revision("0010").module


def _evento(cuenta: int, tipo: str = "GESTION_REGISTRADA", **cambios) -> dict:
    return {
        "ocurrido": "2026-09-02 10:00:00+00",
        "registrado": "2026-09-02 10:30:00+00",
        "relacionado": None,
        "evento_id": uuid4(),
        "cuenta": cuenta,
        "tipo": tipo,
        "origen": "API",
        "cartera": "CARTERA_PRINCIPAL",
        "llave": f"llave-{uuid4().hex}",
        "actor": "AGT-007",
        "motivo": None,
        "huella": HUELLA,
        **cambios,
    }


def _gestion(evento: int, cuenta: int, **cambios) -> dict:
    return {
        "evento": evento,
        "gestion_id": uuid4(),
        "cuenta": cuenta,
        "canal": "TELEFONICA",
        "medio": "LLAMADA",
        "nivel": "CONTACTO_TITULAR",
        "resultado": "PROMESA",
        "actor": "AGT-007",
        **cambios,
    }


def _atribucion(estado: str = "EN_PROCESO", **cambios) -> dict:
    terminada = estado != "EN_PROCESO"
    return {
        "atribucion_run_id": uuid4(),
        "version": "atribucion/v1",
        "desde": "2026-09-01",
        "hasta": "2026-10-01",
        "ventana": 30,
        "estado": estado,
        "resultado": "ERROR_INTERNO" if terminada else None,
        "firma": "a" * 64 if estado == "EXITOSA" else None,
        "motor": None,
        "monto": 0,
        **dict.fromkeys(METRICAS_ATRIBUCION_0010, 0),
        **cambios,
    }


def _evaluacion(estado: str = "EN_PROCESO", **cambios) -> dict:
    terminada = estado != "EN_PROCESO"
    return {
        "evaluacion_run_id": uuid4(),
        "as_of": "2026-09-16",
        "estado": estado,
        "resultado": "ERROR_INTERNO" if terminada else None,
        "firma": "b" * 64 if estado == "EXITOSA" else None,
        **dict.fromkeys(METRICAS_EVALUACION_0010, 0),
        **cambios,
    }


def _trabajo_0010(
    tipo: str,
    estado: str = "PENDIENTE",
    *,
    atribucion: int | None = None,
    evaluacion: int | None = None,
    **cambios,
) -> dict:
    ejecutando = estado == "EJECUTANDO"
    return {
        "trabajo_id": uuid4(),
        "tipo": tipo,
        "estado": estado,
        "corrida_id": None,
        "atribucion": atribucion,
        "evaluacion": evaluacion,
        "intentos": 1 if ejecutando else 0,
        "worker_id": "worker-de-prueba:1:abc" if ejecutando else None,
        "lease_hasta": datetime.now(UTC) + timedelta(minutes=1) if ejecutando else None,
        **cambios,
    }


def _del_motor(motor: Engine) -> dict[str, list[dict]]:
    """Lo que la 0010 no puede tocar del motor de pagos, completo y en orden de su llave."""
    llaves = {
        "ejecucion_motor_pagos": "id",
        "movimiento_economico_canonico": "id",
        "resultado_pago_observado": "ejecucion_motor_pagos_id, dataset_conformado_id, source_row",
    }
    with motor.connect() as conexion:
        return {
            tabla: [
                dict(fila)
                for fila in conexion.execute(text(f"SELECT * FROM {tabla} ORDER BY {orden}"))
                .mappings()
                .all()
            ]
            for tabla, orden in llaves.items()
        }


def _restriccion_de(error: IntegrityError) -> str | None:
    return getattr(getattr(error.orig, "diag", None), "constraint_name", None)


def _rechaza_al_confirmar(motor: Engine, restriccion: str, *pasos: tuple) -> None:
    """La base rechaza, al confirmar, la transaccion de `pasos` por `restriccion`: lo que se revisa
    al final de la transaccion, como las cuotas de un convenio."""
    with pytest.raises(IntegrityError) as error, motor.begin() as conexion:
        for sentencia, parametros in pasos:
            conexion.execute(sentencia, parametros)
    assert _restriccion_de(error.value) == restriccion, error.value


@dataclass(frozen=True)
class BaseEn0010:
    """Una base de v0.8 con una interpretacion de pagos publicada (una ejecucion EXITOSA, su
    movimiento y su resultado), subida a la 0010: el lifecycle vacio."""

    motor: Engine
    base: BaseEn0009
    cuenta: int
    ejecucion_motor: int
    movimiento: int
    movimiento_id: UUID
    previas: dict[str, list[dict]]
    del_motor: dict[str, list[dict]]


@pytest.fixture
def base_en_0010(base_en_0009) -> BaseEn0010:
    b = base_en_0009
    motor = b.motor
    (ejecucion,) = _insertar(
        motor,
        INSERTAR_MOTOR,
        _motor(
            "EXITOSA",
            resultado="INTERPRETACION_PUBLICADA",
            observaciones_leidas=1,
            observaciones_clasificadas=1,
            primarios=1,
            movimientos_canonicos=1,
        ),
    )
    movimiento_id = uuid4()
    (movimiento,) = _insertar(
        motor,
        INSERTAR_MOVIMIENTO,
        {
            "observaciones": 1,
            "ejecucion": ejecucion,
            "cuenta": b.cuenta,
            "movimiento_id": movimiento_id,
            "monto": Decimal("150.00"),
            "firma": FIRMA_0009,
        },
    )
    _insertar(
        motor,
        INSERTAR_RESULTADO_PAGO,
        {
            "ejecucion": ejecucion,
            "dataset": b.base.dataset_pagos,
            "fila": 2,
            "movimiento": movimiento,
            "firma": FIRMA_0009,
            "legacy": FIRMA_0009,
        },
    )
    previas = _por_columna(motor, PREVIAS_A_LA_0008)
    del_motor = _del_motor(motor)

    command.upgrade(_alembic(), "0010")
    return BaseEn0010(motor, b, b.cuenta, ejecucion, movimiento, movimiento_id, previas, del_motor)


def _gestion_completa(b: BaseEn0010, **cambios) -> tuple[int, int]:
    """Un evento GESTION_REGISTRADA y su gestion; devuelve los ids de los dos."""
    (evento,) = _insertar(b.motor, INSERTAR_EVENTO, _evento(b.cuenta))
    (gestion,) = _insertar(b.motor, INSERTAR_GESTION, _gestion(evento, b.cuenta, **cambios))
    return evento, gestion


def _promesa_de(b: BaseEn0010, evento: int, gestion: int, monto: int = 1000) -> int:
    (creada,) = _insertar(
        b.motor, INSERTAR_EVENTO, _evento(b.cuenta, "PROMESA_CREADA", relacionado=evento)
    )
    (promesa,) = _insertar(
        b.motor,
        INSERTAR_PROMESA,
        {
            "evento": creada,
            "gestion": gestion,
            "promesa_id": uuid4(),
            "cuenta": b.cuenta,
            "monto": monto,
        },
    )
    return promesa


def test_la_cabeza_es_la_0010():
    scripts = ScriptDirectory.from_config(_alembic())

    assert scripts.get_heads() == ["0010"]
    assert scripts.get_revision("0010").down_revision == "0009"


def test_los_modelos_declaran_el_esquema_de_la_0010():
    from motor_cartera.db import modelos

    tablas = [modelos.SQLModel.metadata.tables[t] for t in sorted(TABLAS_DE_LA_0010)]
    preparador = postgresql.dialect().identifier_preparer
    nombres = {preparador.format_constraint(c) for t in tablas for c in t.constraints}
    assert nombres == RESTRICCIONES_DE_LA_0010
    assert all(len(nombre) <= 63 for nombre in nombres)
    # Lo que crece con la operacion se cita con 8 bytes.
    for tabla in TABLAS_DEL_LIFECYCLE[:-1]:
        assert isinstance(modelos.SQLModel.metadata.tables[tabla].c.id.type, BigInteger), tabla
    trabajo = TrabajoOrquestacion.__table__
    assert {"ATRIBUCION", "EVALUACION_PROMESAS"} <= set(trabajo.c.tipo.type.enums)
    assert trabajo.c.tipo.type.length == 24
    assert trabajo.c.ejecucion_atribucion_id.nullable is True
    assert trabajo.c.ejecucion_evaluacion_promesas_id.nullable is True
    llaves = {preparador.format_constraint(c) for c in trabajo.constraints}
    assert RESTRICCIONES_DE_LA_0010_EN_LA_COLA <= llaves


def test_la_0010_agrega_el_lifecycle_vacio_y_no_toca_nada_de_la_v08(base_en_0010):
    b = base_en_0010
    motor = b.motor

    # A. Cada fila de antes sigue ahi, identica: la interpretacion de pagos, el modelo historico y
    # todo lo anterior. Un trabajo solo gana sus dos objetivos nuevos, vacios.
    assert _del_motor(motor) == b.del_motor
    assert _historicas(motor) == b.base.historicas
    despues = _por_columna(motor, PREVIAS_A_LA_0008)
    for tabla, filas in b.previas.items():
        assert len(despues[tabla]) == len(filas), tabla
        for antes, fila in zip(filas, despues[tabla], strict=True):
            fila = dict(fila)
            if tabla == "trabajo_orquestacion":
                assert fila.pop("ejecucion_atribucion_id") is None
                assert fila.pop("ejecucion_evaluacion_promesas_id") is None
            assert fila == antes, tabla

    # B. Las tablas nuevas, vacias, con sus restricciones, sus indices, sus triggers y sus
    # funciones, por nombre.
    assert TABLAS_DE_LA_0010 <= set(_consultar(motor, TABLAS))
    for tabla in sorted(TABLAS_DE_LA_0010):
        assert _consultar(motor, f"SELECT count(*) FROM {tabla}") == [0]
    assert set(_consultar(motor, RESTRICCIONES_0010)) == RESTRICCIONES_DE_LA_0010
    with motor.connect() as conexion:
        indices = {
            (tabla, nombre, unico, tuple(columnas), predicado)
            for tabla, nombre, unico, columnas, predicado in conexion.execute(text(INDICES_0010))
        }
        triggers = {tuple(fila) for fila in conexion.execute(text(TRIGGERS))}
    assert indices == INDICES_DE_LA_0010
    assert triggers == TRIGGERS_DE_LA_0010
    assert FUNCIONES_DE_LA_0010 <= set(_consultar(motor, FUNCIONES))
    assert _consultar(motor, "SELECT version_num FROM alembic_version") == ["0010"]


def test_un_evento_por_llave_y_cada_evento_con_lo_que_le_toca(base_en_0010):
    b = base_en_0010
    motor, cuenta = b.motor, b.cuenta
    registrada = _evento(cuenta, llave="llave-unica-0001")
    (gestion,) = _insertar(motor, INSERTAR_EVENTO, registrada)
    # La misma llave en la misma cartera es el mismo evento: la base no registra otro. En otra
    # cartera, la misma llave es otra peticion.
    _rechaza(
        motor,
        "uq_evento_idempotencia",
        INSERTAR_EVENTO,
        _evento(cuenta, llave=registrada["llave"]),
    )
    _insertar(
        motor, INSERTAR_EVENTO, _evento(cuenta, llave=registrada["llave"], cartera="CARTERA_OTRA")
    )
    _rechaza(
        motor,
        "uq_evento_lifecycle_evento_id",
        INSERTAR_EVENTO,
        _evento(cuenta, evento_id=registrada["evento_id"]),
    )

    anulacion = {"relacionado": gestion, "motivo": "Registrada en la cuenta equivocada."}
    _insertar(motor, INSERTAR_EVENTO, _evento(cuenta, "GESTION_ANULADA", **anulacion))
    # Una sola anulacion por gestion; la promesa de la misma gestion es otro tipo, y entra.
    _rechaza(
        motor,
        "ux_evento_relacionado",
        INSERTAR_EVENTO,
        _evento(cuenta, "GESTION_ANULADA", **anulacion),
    )
    _insertar(motor, INSERTAR_EVENTO, _evento(cuenta, "PROMESA_CREADA", relacionado=gestion))
    cierre = {"relacionado": gestion, "motivo": "x"}
    for restriccion, evento in (
        # Una gestion registrada no se refiere a nada; todo lo demas, a un evento anterior.
        ("ck_evento_relacionado", _evento(cuenta, relacionado=gestion)),
        ("ck_evento_relacionado", _evento(cuenta, "CONVENIO_CREADO")),
        # Solo un cierre lleva motivo, y todo cierre lo lleva.
        ("ck_evento_motivo", _evento(cuenta, motivo="sin razon")),
        ("ck_evento_motivo", _evento(cuenta, "CONVENIO_CANCELADO", relacionado=gestion)),
        # Nada ocurre despues de registrarse, con cinco minutos de tolerancia.
        ("ck_evento_tiempos", _evento(cuenta, ocurrido="2026-09-02 10:36:00+00")),
        ("ck_evento_huella", _evento(cuenta, huella=b"\x07" * 31)),
        ("ck_evento_llave", _evento(cuenta, llave="corta")),
        ("ck_evento_llave", _evento(cuenta, llave="llave con espacios")),
        ("ck_evento_actor", _evento(cuenta, actor="Juan Perez")),
        ("ck_evento_actor", _evento(cuenta, actor="alguien@correo.mx")),
        ("ck_evento_lifecycle_tipo_evento_lifecycle", _evento(cuenta, "PAGO_REGISTRADO")),
        ("ck_evento_lifecycle_origen_registro", _evento(cuenta, origen="CARTERA")),
        (
            "fk_evento_relacionado",
            _evento(cuenta, "GESTION_ANULADA", **{**cierre, "relacionado": 999_999}),
        ),
        ("fk_evento_cuenta", _evento(999_999)),
    ):
        _rechaza(motor, restriccion, INSERTAR_EVENTO, evento)
    # Un evento tardio, ocurrido semanas antes de registrarse, es valido; y dos minutos de reloj
    # adelantado del cliente, tambien.
    _insertar(
        motor,
        INSERTAR_EVENTO,
        _evento(cuenta, ocurrido="2026-08-10 09:00:00+00"),
        _evento(cuenta, ocurrido="2026-09-02 10:32:00+00"),
    )


def test_el_lifecycle_solo_se_agrega(base_en_0010):
    b = base_en_0010
    motor = b.motor
    evento, gestion = _gestion_completa(b, canal="CAMPO", medio=None, resultado="PROMESA")
    _insertar(
        motor,
        INSERTAR_VISITA,
        {
            "inicio": None,
            "fin": None,
            "gestion": gestion,
            "visita_id": uuid4(),
            "resultado": "CONTACTO_TITULAR",
        },
    )
    _promesa_de(b, evento, gestion)
    antes = {t: _consultar(motor, f"SELECT count(*) FROM {t}") for t in TABLAS_DEL_LIFECYCLE}

    for tabla in TABLAS_DEL_LIFECYCLE:
        columna = "numero" if tabla == "cuota_convenio" else "id"
        for sentencia in (
            f"UPDATE {tabla} SET {columna} = {columna}",
            f"DELETE FROM {tabla}",
            # Aunque no toque ninguna fila.
            f"DELETE FROM {tabla} WHERE false",
        ):
            with pytest.raises(IntegrityError) as error, motor.begin() as conexion:
                conexion.execute(text(sentencia))
            assert _restriccion_de(error.value) == "tr_lifecycle_solo_agrega", sentencia
            assert "solo se agrega" in str(error.value)
    assert {t: _consultar(motor, f"SELECT count(*) FROM {t}") for t in TABLAS_DEL_LIFECYCLE} == (
        antes
    )


def test_la_base_y_las_reglas_puras_coinciden_en_cada_gestion(base_en_0010):
    """Cada combinacion de canal, medio, nivel de contacto y resultado: la base la acepta si y solo
    si `incoherencias_de_gestion` no encuentra nada, con una visita OTRO en las de CAMPO (que admite
    cualquier nivel)."""
    from itertools import product

    from motor_cartera.lifecycle.reglas import (
        Canal,
        Medio,
        NivelContacto,
        ResultadoGestion,
        ResultadoVisita,
        incoherencias_de_gestion,
    )

    b = base_en_0010
    motor = b.motor
    combinaciones = list(product(Canal, [None, *Medio], NivelContacto, ResultadoGestion))
    eventos = _insertar(motor, INSERTAR_EVENTO, *(_evento(b.cuenta) for _ in combinaciones))
    aceptadas, esperadas = set(), set()
    with motor.begin() as conexion:
        for evento, (canal, medio, nivel, resultado) in zip(eventos, combinaciones, strict=True):
            visita = ResultadoVisita.OTRO if canal == Canal.CAMPO else None
            if not incoherencias_de_gestion(canal, medio, nivel, resultado, visita):
                esperadas.add((canal, medio, nivel, resultado))
            fila = _gestion(
                evento,
                b.cuenta,
                canal=canal.value,
                medio=None if medio is None else medio.value,
                nivel=nivel.value,
                resultado=resultado.value,
            )
            try:
                with conexion.begin_nested():
                    conexion.execute(INSERTAR_GESTION, fila)
            except IntegrityError as error:
                assert _restriccion_de(error) in ("ck_gestion_medio", "ck_gestion_coherencia")
            else:
                aceptadas.add((canal, medio, nivel, resultado))
    assert aceptadas == esperadas
    assert len(combinaciones) == 560
    assert 0 < len(aceptadas) < 560


def test_visita_promesa_convenio_y_sus_cuotas(base_en_0010):
    b = base_en_0010
    motor, cuenta = b.motor, b.cuenta
    evento, gestion = _gestion_completa(b, canal="CAMPO", medio=None, resultado="RECHAZO")
    visita = {
        "inicio": None,
        "fin": None,
        "gestion": gestion,
        "visita_id": uuid4(),
        "resultado": "CONTACTO_TITULAR",
    }
    _insertar(motor, INSERTAR_VISITA, visita)
    _rechaza(motor, "uq_visita_gestion", INSERTAR_VISITA, {**visita, "visita_id": uuid4()})
    _, otra = _gestion_completa(b, canal="CAMPO", medio=None, resultado="RECHAZO")
    for restriccion, cambios in (
        (
            "ck_visita_intervalo",
            {"inicio": "2026-09-02 11:00:00+00", "fin": "2026-09-02 10:00:00+00"},
        ),
        ("ck_visita_campo_resultado_visita", {"resultado": "GEOCERCA_OK"}),
        ("fk_visita_gestion", {"gestion": 999_999}),
    ):
        _rechaza(
            motor,
            restriccion,
            INSERTAR_VISITA,
            {**visita, "gestion": otra, "visita_id": uuid4(), **cambios},
        )

    # Una promesa por gestion y por evento.
    _promesa_de(b, evento, gestion)
    (de_otra,) = _insertar(
        motor, INSERTAR_EVENTO, _evento(cuenta, "CONVENIO_CREADO", relacionado=evento)
    )
    promesa = {
        "evento": de_otra,
        "gestion": gestion,
        "promesa_id": uuid4(),
        "cuenta": cuenta,
        "monto": 1000,
    }
    _rechaza(motor, "uq_promesa_gestion", INSERTAR_PROMESA, promesa)
    _, sin_promesa = _gestion_completa(b)
    for restriccion, cambios in (
        ("ck_promesa_monto", {"monto": 0}),
        ("fk_promesa_cuenta", {"cuenta": 999_999}),
        ("fk_promesa_gestion", {"gestion": 999_999}),
    ):
        _rechaza(
            motor, restriccion, INSERTAR_PROMESA, {**promesa, "gestion": sin_promesa, **cambios}
        )

    # Un convenio con sus cuotas, que la base comprueba al confirmar.
    def convenio(cuotas: int, **cambios) -> tuple:
        evento_propio, propia = _gestion_completa(b, resultado="CONVENIO")
        (creado,) = _insertar(
            motor, INSERTAR_EVENTO, _evento(cuenta, "CONVENIO_CREADO", relacionado=evento_propio)
        )
        return INSERTAR_CONVENIO, {
            "evento": creado,
            "gestion": propia,
            "fin": "2026-12-31",
            "convenio_id": uuid4(),
            "cuenta": cuenta,
            "cuotas": cuotas,
            "monto": Decimal("3000.00"),
            **cambios,
        }

    def cuota(numero: int, fecha: str, monto: str = "1000.00") -> dict:
        return {"numero": numero, "fecha": fecha, "monto": Decimal(monto)}

    def con_cuotas(de_convenio: tuple, *cuotas: dict) -> list[tuple]:
        _, parametros = de_convenio
        llave = {"convenio_id": parametros["convenio_id"]}
        return [de_convenio, *((INSERTAR_CUOTA, {**c, **llave}) for c in cuotas)]

    trimestral = (cuota(1, "2026-10-01"), cuota(2, "2026-11-01"), cuota(3, "2026-12-01"))
    with motor.begin() as conexion:
        for sentencia, parametros in con_cuotas(convenio(3), *trimestral):
            conexion.execute(sentencia, parametros)
    sin_calendario = convenio(0)
    with motor.begin() as conexion:
        conexion.execute(*sin_calendario)
    for pasos in (
        # Declara tres y trae dos; no suman su monto; dos vencen el mismo dia; se salta un numero;
        # una vence despues de su fin, u otra antes de su inicio.
        con_cuotas(convenio(3), *trimestral[:2]),
        con_cuotas(convenio(3), *trimestral[:2], cuota(3, "2026-12-01", "999.99")),
        con_cuotas(convenio(3), cuota(1, "2026-10-01"), cuota(2, "2026-10-01"), trimestral[2]),
        con_cuotas(convenio(3), *trimestral[:2], cuota(4, "2026-12-01")),
        con_cuotas(convenio(3), *trimestral[:2], cuota(3, "2027-01-15")),
        con_cuotas(convenio(1, fin=None), cuota(1, "2026-08-31", "3000.00")),
    ):
        _rechaza_al_confirmar(motor, "tr_convenio_cuotas", *pasos)
    # A un convenio confirmado no se le agregan cuotas despues: tendria mas de las que declaro.
    agregar = con_cuotas(sin_calendario, cuota(1, "2026-10-01", "3000.00"))[1:]
    _rechaza_al_confirmar(motor, "tr_convenio_cuotas", *agregar)
    for restriccion, cambios in (
        ("ck_convenio_vigencia", {"fin": "2026-08-01"}),
        ("ck_convenio_cuotas", {"cuotas": -1}),
        ("ck_convenio_monto", {"monto": Decimal("0.00")}),
    ):
        sentencia, parametros = convenio(0)
        _rechaza(motor, restriccion, sentencia, {**parametros, **cambios})


def test_la_atribucion_publica_una_clase_por_movimiento_sin_elegir_entre_candidatas(base_en_0010):
    b = base_en_0010
    motor, cuenta = b.motor, b.cuenta
    publicada = {"resultado": "ATRIBUCION_PUBLICADA", "motor": b.ejecucion_motor}
    (exitosa,) = _insertar(motor, INSERTAR_ATRIBUCION, _atribucion("EXITOSA", **publicada))
    _rechaza(
        motor,
        "ux_ejecucion_atribucion_exitosa",
        INSERTAR_ATRIBUCION,
        _atribucion("EXITOSA", **publicada),
    )
    # Con otra firma, otra atribucion de entradas nuevas; otra version u otra ventana, tambien.
    _insertar(
        motor,
        INSERTAR_ATRIBUCION,
        _atribucion("EXITOSA", **{**publicada, "firma": "c" * 64}),
        _atribucion("EXITOSA", version="atribucion/v2", **publicada),
        _atribucion(),
    )
    _rechaza(motor, "ux_ejecucion_atribucion_en_proceso", INSERTAR_ATRIBUCION, _atribucion())
    _insertar(motor, INSERTAR_ATRIBUCION, _atribucion(desde="2026-10-01", hasta="2026-11-01"))
    _insertar(motor, INSERTAR_ATRIBUCION, _atribucion("FALLIDA"), _atribucion("FALLIDA"))
    for restriccion, ejecucion in (
        ("ck_atribucion_mes", _atribucion(desde="2026-09-02", hasta="2026-10-02")),
        ("ck_atribucion_periodo", _atribucion(version="atribucion/v2", hasta="2026-08-01")),
        ("ck_atribucion_ventana_dias", _atribucion(ventana=0)),
        ("ck_atribucion_resultado", _atribucion("FALLIDA", resultado=None)),
        ("ck_atribucion_firma", _atribucion("EXITOSA", **{**publicada, "firma": None})),
        ("ck_atribucion_firma", _atribucion("FALLIDA", firma="x")),
        # Cada evaluado tiene una clase; una unica, una candidata; una ambigua, al menos dos.
        ("ck_atribucion_conteos", _atribucion("FALLIDA", movimientos_evaluados=2, asociados=1)),
        (
            "ck_atribucion_conteos",
            _atribucion(
                "EXITOSA",
                **{**publicada, "firma": "d" * 64},
                movimientos_evaluados=1,
                ambiguos=1,
                candidatos=1,
            ),
        ),
        ("ck_atribucion_montos", _atribucion("FALLIDA", monto=-1)),
        # Una EXITOSA dice que interpretacion leyo; una FALLIDA no publico nada.
        (
            "ck_atribucion_publicacion",
            _atribucion("EXITOSA", firma="e" * 64, resultado="ATRIBUCION_PUBLICADA"),
        ),
        (
            "ck_atribucion_publicacion",
            _atribucion("FALLIDA", movimientos_evaluados=1, sin_candidato=1),
        ),
        ("ck_ejecucion_atribucion_estado_atribucion", _atribucion("PUBLICADA")),
        (
            "fk_atribucion_motor_pagos",
            _atribucion("EXITOSA", **{**publicada, "firma": "f" * 64, "motor": 999_999}),
        ),
    ):
        _rechaza(motor, restriccion, INSERTAR_ATRIBUCION, ejecucion)

    _, gestion = _gestion_completa(b)
    _, otra = _gestion_completa(b)
    fila = {
        "ejecucion": exitosa,
        "movimiento": b.movimiento,
        "gestion": gestion,
        "movimiento_id": b.movimiento_id,
        "cuenta": cuenta,
        "candidatos": 1,
        "clasificacion": "ASOCIACION_UNICA",
    }
    _insertar(motor, INSERTAR_ATRIBUCION_MOVIMIENTO, fila)
    _rechaza(motor, "pk_atribucion_movimiento", INSERTAR_ATRIBUCION_MOVIMIENTO, fila)
    candidato = {
        "ejecucion": exitosa,
        "movimiento": b.movimiento,
        "gestion": gestion,
        "antelacion": 3600,
    }
    _insertar(motor, INSERTAR_CANDIDATO, candidato)
    _rechaza(motor, "pk_candidato_atribucion", INSERTAR_CANDIDATO, candidato)
    for restriccion, cambios in (
        ("ck_candidato_antelacion", {"gestion": otra, "antelacion": -1}),
        ("fk_candidato_gestion", {"gestion": 999_999}),
        # Un candidato es de un movimiento que la ejecucion atribuyo.
        ("fk_candidato_atribucion", {"ejecucion": exitosa + 1, "gestion": otra}),
    ):
        _rechaza(motor, restriccion, INSERTAR_CANDIDATO, {**candidato, **cambios})
    (otra_ejecucion,) = _insertar(
        motor,
        INSERTAR_ATRIBUCION,
        _atribucion(
            "EXITOSA", **{**publicada, "firma": "9" * 64}, desde="2026-11-01", hasta="2026-12-01"
        ),
    )
    en_otra = {**fila, "ejecucion": otra_ejecucion}
    for restriccion, cambios in (
        # Ninguna clase elige entre varias candidatas, ni asocia sin nombrar a su gestion.
        ("ck_atribucion_movimiento_clase", {"gestion": None}),
        ("ck_atribucion_movimiento_clase", {"candidatos": 2}),
        (
            "ck_atribucion_movimiento_clase",
            {"clasificacion": "AMBIGUA", "candidatos": 1, "gestion": None},
        ),
        ("ck_atribucion_movimiento_clase", {"clasificacion": "AMBIGUA", "candidatos": 2}),
        (
            "ck_atribucion_movimiento_clase",
            {"clasificacion": "SIN_GESTION_CANDIDATA", "candidatos": 1, "gestion": None},
        ),
        ("fk_atribucion_movimiento_movimiento", {"movimiento": 999_999}),
        ("fk_atribucion_movimiento_gestion", {"gestion": 999_999}),
        ("fk_atribucion_movimiento_cuenta", {"cuenta": 999_999}),
        ("fk_atribucion_movimiento_ejecucion", {"ejecucion": 999_999}),
    ):
        _rechaza(motor, restriccion, INSERTAR_ATRIBUCION_MOVIMIENTO, {**en_otra, **cambios})
    # El vocabulario de otra version no lo juzgan las invariantes de esta.
    _insertar(
        motor,
        INSERTAR_ATRIBUCION_MOVIMIENTO,
        {**en_otra, "clasificacion": "ASOCIACION_PONDERADA", "candidatos": 2},
    )
    # Y nada se borra en cascada: ni el movimiento atribuido ni la interpretacion que se leyo.
    (suelto,) = _insertar(
        motor,
        INSERTAR_MOVIMIENTO,
        {
            "observaciones": 1,
            "ejecucion": b.ejecucion_motor,
            "cuenta": cuenta,
            "movimiento_id": uuid4(),
            "monto": Decimal("90.00"),
            "firma": FIRMA_0009,
        },
    )
    _insertar(
        motor,
        INSERTAR_ATRIBUCION_MOVIMIENTO,
        {
            **en_otra,
            "movimiento": suelto,
            "clasificacion": "SIN_GESTION_CANDIDATA",
            "gestion": None,
            "candidatos": 0,
        },
    )
    for restriccion, borrar, parametros in (
        (
            "fk_atribucion_movimiento_movimiento",
            "DELETE FROM movimiento_economico_canonico WHERE id = :id",
            {"id": suelto},
        ),
        (
            "fk_atribucion_movimiento_ejecucion",
            "DELETE FROM ejecucion_atribucion WHERE id = :id",
            {"id": otra_ejecucion},
        ),
    ):
        _rechaza(motor, restriccion, text(borrar), parametros)


def test_una_evaluacion_exitosa_por_fecha_de_corte_y_firma(base_en_0010):
    b = base_en_0010
    motor = b.motor
    publicada = {"resultado": "EVALUACION_PUBLICADA"}
    (exitosa,) = _insertar(motor, INSERTAR_EVALUACION, _evaluacion("EXITOSA", **publicada))
    _rechaza(
        motor,
        "ux_evaluacion_promesas_exitosa",
        INSERTAR_EVALUACION,
        _evaluacion("EXITOSA", **publicada),
    )
    _insertar(
        motor,
        INSERTAR_EVALUACION,
        _evaluacion("EXITOSA", firma="c" * 64, **publicada),
        _evaluacion(),
        _evaluacion(as_of="2026-09-17"),
    )
    _rechaza(motor, "ux_evaluacion_promesas_en_proceso", INSERTAR_EVALUACION, _evaluacion())
    for restriccion, ejecucion in (
        ("ck_evaluacion_resultado", _evaluacion("FALLIDA", resultado=None)),
        ("ck_evaluacion_firma", _evaluacion("EXITOSA", firma=None, **publicada)),
        (
            "ck_evaluacion_clases",
            _evaluacion("EXITOSA", firma="d" * 64, promesas_evaluadas=2, cumplidas=1, **publicada),
        ),
        ("ck_evaluacion_conteos", _evaluacion("FALLIDA", pendientes=-1, promesas_evaluadas=-1)),
        ("ck_evaluacion_publicacion", _evaluacion("FALLIDA", promesas_evaluadas=1, cumplidas=1)),
        ("ck_ejecucion_evaluacion_promesas_estado_evaluacion_promesas", _evaluacion("LISTA")),
    ):
        _rechaza(motor, restriccion, INSERTAR_EVALUACION, ejecucion)
    evento, gestion = _gestion_completa(b)
    promesa = _promesa_de(b, evento, gestion)
    evaluada = {
        "ejecucion": exitosa,
        "promesa": promesa,
        "movimientos": 1,
        "monto": Decimal("1000.00"),
    }
    _insertar(motor, INSERTAR_EVALUACION_PROMESA, evaluada)
    _rechaza(motor, "pk_evaluacion_promesa", INSERTAR_EVALUACION_PROMESA, evaluada)
    for restriccion, cambios in (
        ("ck_evaluacion_promesa_conteos", {"movimientos": -1}),
        ("fk_evaluacion_promesa_promesa", {"promesa": 999_999}),
        ("fk_evaluacion_promesa_ejecucion", {"ejecucion": 999_999}),
    ):
        _rechaza(motor, restriccion, INSERTAR_EVALUACION_PROMESA, {**evaluada, **cambios})


def test_los_trabajos_nuevos_apuntan_a_su_ejecucion_y_a_nada_mas(base_en_0010):
    motor = base_en_0010.motor
    (atribucion,) = _insertar(motor, INSERTAR_ATRIBUCION, _atribucion())
    (evaluacion,) = _insertar(motor, INSERTAR_EVALUACION, _evaluacion())
    _insertar(
        motor,
        INSERTAR_TRABAJO_0010,
        _trabajo_0010("ATRIBUCION", atribucion=atribucion),
        # EVALUACION_PROMESAS cabe en los 24 caracteres del tipo.
        _trabajo_0010("EVALUACION_PROMESAS", evaluacion=evaluacion),
    )
    for restriccion, trabajo in (
        ("uq_trabajo_atribucion", _trabajo_0010("ATRIBUCION", atribucion=atribucion)),
        ("uq_trabajo_evaluacion", _trabajo_0010("EVALUACION_PROMESAS", evaluacion=evaluacion)),
        ("ck_trabajo_objetivo", _trabajo_0010("ATRIBUCION")),
        ("ck_trabajo_objetivo", _trabajo_0010("ATRIBUCION", evaluacion=evaluacion)),
        ("ck_trabajo_objetivo", _trabajo_0010("EVALUACION_PROMESAS", atribucion=atribucion)),
        ("fk_trabajo_atribucion", _trabajo_0010("ATRIBUCION", atribucion=999_999)),
        ("fk_trabajo_evaluacion", _trabajo_0010("EVALUACION_PROMESAS", evaluacion=999_999)),
        # Un tipo que no existe, sin objetivo: lo que lo rechaza es el catalogo de tipos.
        ("ck_trabajo_orquestacion_tipo_trabajo", _trabajo_0010("LIFECYCLE")),
    ):
        _rechaza(motor, restriccion, INSERTAR_TRABAJO_0010, trabajo)


def test_la_0010_baja_cerrando_lo_que_estaba_en_curso_y_vuelve_a_subir(base_en_0010):
    b = base_en_0010
    motor, cuenta = b.motor, b.cuenta
    # Un lifecycle con algo de todo: una gestion anulada con su promesa, una atribucion publicada
    # con su resultado, y una atribucion y una evaluacion en curso, con sus trabajos.
    evento, gestion = _gestion_completa(b)
    _insertar(
        motor,
        INSERTAR_EVENTO,
        _evento(cuenta, "GESTION_ANULADA", relacionado=evento, motivo="Duplicada."),
    )
    _promesa_de(b, evento, gestion, monto=500)
    publicada = {"resultado": "ATRIBUCION_PUBLICADA", "motor": b.ejecucion_motor}
    (exitosa,) = _insertar(
        motor,
        INSERTAR_ATRIBUCION,
        _atribucion("EXITOSA", movimientos_evaluados=1, asociados=1, candidatos=1, **publicada),
    )
    _insertar(
        motor,
        INSERTAR_ATRIBUCION_MOVIMIENTO,
        {
            "ejecucion": exitosa,
            "movimiento": b.movimiento,
            "gestion": gestion,
            "movimiento_id": b.movimiento_id,
            "cuenta": cuenta,
            "candidatos": 1,
            "clasificacion": "ASOCIACION_UNICA",
        },
    )
    (activa,) = _insertar(
        motor, INSERTAR_ATRIBUCION, _atribucion(desde="2026-10-01", hasta="2026-11-01")
    )
    (evaluacion,) = _insertar(motor, INSERTAR_EVALUACION, _evaluacion())
    _insertar(
        motor,
        INSERTAR_TRABAJO_0010,
        _trabajo_0010("ATRIBUCION", "EJECUTANDO", atribucion=activa),
        _trabajo_0010("EVALUACION_PROMESAS", evaluacion=evaluacion),
    )
    previas = _por_columna(motor, PREVIAS_A_LA_0008)
    nuevas = ("ejecucion_atribucion_id", "ejecucion_evaluacion_promesas_id")
    trabajos_antes = [
        {k: v for k, v in f.items() if k not in nuevas}
        for f in previas["trabajo_orquestacion"]
        if f["tipo"] not in ("ATRIBUCION", "EVALUACION_PROMESAS")
    ]
    assert len(previas["trabajo_orquestacion"]) == len(trabajos_antes) + 2

    command.downgrade(_alembic(), "0009")

    # El lifecycle entero se fue, con sus trabajos, sus triggers y sus funciones. Lo de v0.8,
    # intacto: la interpretacion de pagos, el modelo historico y todo lo anterior; los otros
    # trabajos, sin sus columnas nuevas y con su tipo de 16 caracteres.
    assert not TABLAS_DE_LA_0010 & set(_consultar(motor, TABLAS))
    assert not FUNCIONES_DE_LA_0010 & set(_consultar(motor, FUNCIONES))
    assert _del_motor(motor) == b.del_motor
    assert _historicas(motor) == b.base.historicas
    despues = _por_columna(motor, PREVIAS_A_LA_0008)
    for tabla, filas in previas.items():
        if tabla == "trabajo_orquestacion":
            assert despues[tabla] == trabajos_antes
        else:
            assert despues[tabla] == filas, tabla
    definiciones = _consultar(
        motor,
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname IN "
        "('ck_trabajo_objetivo', 'ck_trabajo_orquestacion_tipo_trabajo')",
    )
    assert len(definiciones) == 2
    assert not any("ATRIBUCION" in d or "EVALUACION" in d for d in definiciones)
    largo = _consultar(
        motor,
        "SELECT character_maximum_length FROM information_schema.columns "
        "WHERE table_name = 'trabajo_orquestacion' AND column_name = 'tipo'",
    )
    assert largo == [16]
    assert _consultar(motor, "SELECT version_num FROM alembic_version") == ["0009"]

    # Y vuelve a subir limpia: el lifecycle vacio, con sus restricciones y sus triggers.
    command.upgrade(_alembic(), "0010")

    for tabla in sorted(TABLAS_DE_LA_0010):
        assert _consultar(motor, f"SELECT count(*) FROM {tabla}") == [0]
    assert set(_consultar(motor, RESTRICCIONES_0010)) == RESTRICCIONES_DE_LA_0010
    with motor.connect() as conexion:
        assert {tuple(fila) for fila in conexion.execute(text(TRIGGERS))} == TRIGGERS_DE_LA_0010
    assert _del_motor(motor) == b.del_motor
    assert _consultar(motor, "SELECT version_num FROM alembic_version") == ["0010"]


def test_al_bajar_la_0010_lo_primero_es_cerrar_la_atribucion_y_la_evaluacion_en_curso(
    base_en_0010,
):
    # Lo que la 0010 hace antes de quitar sus tablas, con sus mismas sentencias: cada ejecucion en
    # curso queda FALLIDA y su trabajo FALLIDO, sin dueno ni lease, con el motivo. Lo terminado no
    # se toca.
    motor = base_en_0010.motor
    modulo = _modulo_0010()
    activa, fallida = _insertar(
        motor,
        INSERTAR_ATRIBUCION,
        _atribucion(),
        _atribucion("FALLIDA", desde="2026-10-01", hasta="2026-11-01"),
    )
    (evaluacion,) = _insertar(motor, INSERTAR_EVALUACION, _evaluacion())
    _insertar(
        motor,
        INSERTAR_TRABAJO_0010,
        _trabajo_0010("ATRIBUCION", "EJECUTANDO", atribucion=activa),
        _trabajo_0010("EVALUACION_PROMESAS", evaluacion=evaluacion),
    )

    with motor.begin() as conexion:
        conexion.execute(modulo.CERRAR_ATRIBUCIONES)
        conexion.execute(modulo.CERRAR_EVALUACIONES)
        conexion.execute(modulo.CERRAR_TRABAJOS)

    with motor.connect() as conexion:
        atribuciones = conexion.execute(
            text("SELECT id, estado, resultado, detalle FROM ejecucion_atribucion ORDER BY id")
        ).all()
        evaluada = conexion.execute(
            text("SELECT estado, resultado, detalle FROM ejecucion_evaluacion_promesas")
        ).one()
        trabajos = conexion.execute(
            text(
                "SELECT estado, worker_id, lease_hasta, ultimo_error FROM trabajo_orquestacion "
                "WHERE tipo IN ('ATRIBUCION', 'EVALUACION_PROMESAS') ORDER BY id"
            )
        ).all()
    assert [tuple(a) for a in atribuciones] == [
        (activa, "FALLIDA", "CERRADA_AL_BAJAR", modulo.CIERRE),
        (fallida, "FALLIDA", "ERROR_INTERNO", None),
    ]
    assert tuple(evaluada) == ("FALLIDA", "CERRADA_AL_BAJAR", modulo.CIERRE)
    assert [tuple(t) for t in trabajos] == [("FALLIDO", None, None, modulo.CIERRE)] * 2
