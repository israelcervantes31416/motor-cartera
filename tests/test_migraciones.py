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
from datetime import UTC, date, datetime, timedelta
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
    Float,
    Integer,
    LargeBinary,
    Numeric,
    UniqueConstraint,
    create_engine,
    make_url,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError, IntegrityError
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
    # 0008 para la historia, la 0009 para el motor de pagos, la 0010 para la atribucion y la
    # evaluacion de promesas, la 0011 para decision/v2, la 0012 para la geografia y territorial/v2,
    # la 0013 para campo/v2 y la 0014 para ruteo/v2: su tipo de trabajo, su columna, su llave
    # foranea y su restriccion unica.
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
        "ejecucion_decision_v2_id",
        "ejecucion_geografia_id",
        "ejecucion_territorial_v2_id",
        "ejecucion_campo_v2_id",
        "ejecucion_ruteo_v2_id",
        "ejecucion_analytics_id",
        "ejecucion_entrenamiento_predictivo_id",
        "ejecucion_scoring_predictivo_id",
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
        "ejecucion_decision_v2_id",
        "ejecucion_geografia_id",
        "ejecucion_territorial_v2_id",
        "ejecucion_campo_v2_id",
        "ejecucion_ruteo_v2_id",
        "ejecucion_analytics_id",
        "ejecucion_entrenamiento_predictivo_id",
        "ejecucion_scoring_predictivo_id",
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
        "DECISION_V2",
        "GEOGRAFIA",
        "TERRITORIAL_V2",
        "CAMPO_V2",
        "RUTEO_V2",
        "ANALYTICS",
        "PREDICTIVE_TRAINING",
        "PREDICTIVE_SCORING",
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
        ("trabajo_orquestacion", "ejecucion_decision_v2_id"): ("ejecucion_decision_v2.id", None),
        ("trabajo_orquestacion", "ejecucion_geografia_id"): ("ejecucion_geografia.id", None),
        ("trabajo_orquestacion", "ejecucion_territorial_v2_id"): (
            "ejecucion_territorial_v2.id",
            None,
        ),
        ("trabajo_orquestacion", "ejecucion_campo_v2_id"): ("ejecucion_campo_v2.id", None),
        ("trabajo_orquestacion", "ejecucion_ruteo_v2_id"): ("ejecucion_ruteo_v2.id", None),
        ("trabajo_orquestacion", "ejecucion_analytics_id"): ("ejecucion_analytics.id", None),
        ("trabajo_orquestacion", "ejecucion_entrenamiento_predictivo_id"): (
            "ejecucion_entrenamiento_predictivo.id",
            None,
        ),
        ("trabajo_orquestacion", "ejecucion_scoring_predictivo_id"): (
            "ejecucion_scoring_predictivo.id",
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
        "uq_trabajo_decision_v2": ["ejecucion_decision_v2_id"],
        "uq_trabajo_geografia": ["ejecucion_geografia_id"],
        "uq_trabajo_territorial_v2": ["ejecucion_territorial_v2_id"],
        "uq_trabajo_campo_v2": ["ejecucion_campo_v2_id"],
        "uq_trabajo_ruteo_v2": ["ejecucion_ruteo_v2_id"],
        "uq_trabajo_analytics": ["ejecucion_analytics_id"],
        "uq_trabajo_entrenamiento_predictivo": ["ejecucion_entrenamiento_predictivo_id"],
        "uq_trabajo_scoring_predictivo": ["ejecucion_scoring_predictivo_id"],
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
        | RESTRICCIONES_DE_LA_0011_EN_LA_COLA
        | RESTRICCIONES_DE_LA_0012_EN_LA_COLA
        | RESTRICCIONES_DE_LA_0013_EN_LA_COLA
        | RESTRICCIONES_DE_LA_0014_EN_LA_COLA
        | RESTRICCIONES_DE_LA_0015_EN_LA_COLA
        | RESTRICCIONES_DE_LA_0016_EN_LA_COLA
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
    # migraciones, la 0012 con PostGIS y de la 0013 a la 0016 incluidas, es el que declaran los
    # modelos.
    command.upgrade(_alembic(), "head")
    salida = io.StringIO()
    cfg = ConfigAlembic(str(RAIZ / "alembic.ini"), stdout=salida)
    cfg.set_main_option("script_location", str(RAIZ / "migraciones"))

    command.check(cfg)  # con cualquier diferencia levantaria AutogenerateDiffsDetected

    assert salida.getvalue().strip() == "No new upgrade operations detected."
    assert _consultar(base_en_0001, "SELECT version_num FROM alembic_version") == ["0016"]


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


def test_la_0010_baja_de_la_0009():
    scripts = ScriptDirectory.from_config(_alembic())

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


# --- la 0011: decision/v2 -------------------------------------------------------------------------

TABLAS_DE_LA_0011 = {"ejecucion_decision_v2", "feature_decision_cuenta", "resultado_decision_v2"}
_DE_LA_0011 = ", ".join(f"'{tabla}'" for tabla in sorted(TABLAS_DE_LA_0011))
RESTRICCIONES_0011 = RESTRICCIONES.replace("'ejecucion_decision', 'decision_cuenta'", _DE_LA_0011)
INDICES_0011 = INDICES.replace("'ejecucion_decision', 'decision_cuenta'", _DE_LA_0011)
RESTRICCIONES_DE_LA_0011 = {
    "pk_ejecucion_decision_v2",
    "uq_ejecucion_decision_v2_decision_run_id",
    "uq_decision_v2_id_corte",
    "fk_decision_v2_corte",
    "ck_decision_v2_firmas",
    "ck_decision_v2_resultado",
    "ck_decision_v2_conteos",
    "ck_decision_v2_publicacion",
    "ck_ejecucion_decision_v2_modo_decision_v2",
    "ck_ejecucion_decision_v2_estado_decision_v2",
    "pk_feature_decision_cuenta",
    "fk_feature_decision_ejecucion",
    "fk_feature_decision_snapshot",
    "ck_feature_decision_hash",
    "ck_feature_decision_conteos",
    "ck_feature_decision_tasa",
    "pk_resultado_decision_v2",
    "fk_resultado_decision_v2_feature",
    "ck_resultado_decision_v2_niveles",
    "ck_resultado_decision_v2_hash",
}
RESTRICCIONES_DE_LA_0011_EN_LA_COLA = {"fk_trabajo_decision_v2", "uq_trabajo_decision_v2"}
INDICES_DE_LA_0011 = {
    ("ejecucion_decision_v2", "pk_ejecucion_decision_v2", True, ("id",), None),
    (
        "ejecucion_decision_v2",
        "uq_ejecucion_decision_v2_decision_run_id",
        True,
        ("decision_run_id",),
        None,
    ),
    ("ejecucion_decision_v2", "uq_decision_v2_id_corte", True, ("id", "corte_canonico_id"), None),
    (
        "ejecucion_decision_v2",
        "ux_decision_v2_exitosa",
        True,
        ("firma_inputs",),
        "((estado)::text = 'EXITOSA'::text)",
    ),
    (
        "ejecucion_decision_v2",
        "ux_decision_v2_en_proceso",
        True,
        ("corte_canonico_id", "version_features", "version_politica"),
        "((estado)::text = 'EN_PROCESO'::text)",
    ),
    ("ejecucion_decision_v2", "ix_decision_v2_corte", False, ("corte_canonico_id", "id"), None),
    (
        "feature_decision_cuenta",
        "pk_feature_decision_cuenta",
        True,
        ("ejecucion_decision_v2_id", "cuenta_canonica_id"),
        None,
    ),
    (
        "resultado_decision_v2",
        "pk_resultado_decision_v2",
        True,
        ("ejecucion_decision_v2_id", "cuenta_canonica_id"),
        None,
    ),
    (
        "resultado_decision_v2",
        "ix_resultado_decision_v2_prioridad",
        False,
        ("ejecucion_decision_v2_id", "prioridad_final", "cuenta_canonica_id"),
        None,
    ),
    (
        "resultado_decision_v2",
        "ix_resultado_decision_v2_canal",
        False,
        ("ejecucion_decision_v2_id", "canal_recomendado", "cuenta_canonica_id"),
        None,
    ),
    (
        "resultado_decision_v2",
        "ix_resultado_decision_v2_cuenta",
        False,
        ("cuenta_canonica_id", "ejecucion_decision_v2_id"),
        None,
    ),
}
"""Ni un indice por feature: solo los de la llave, la unicidad y las paginas que la API lee."""

INSERTAR_DECISION_V2 = text(
    "INSERT INTO ejecucion_decision_v2 (decision_run_id, corte_canonico_id, despacho_id, "
    "cartera_id, as_of, known_at, modo, zona_horaria, version_motor, version_features, "
    "version_politica, hash_politica, firma_inputs, estado, resultado, cuentas_objetivo, "
    "cuentas_decididas, cuentas_fallidas, iniciada_en) VALUES (:decision_run_id, :corte, "
    "'DSP_001', 'CARTERA_PRINCIPAL', '2026-09-02', :known_at, 'OPERATIVO', 'America/Mexico_City', "
    "'decision/v2', 'features-decision/v1', 'decision-policy/public-v1', :hash_politica, :firma, "
    ":estado, :resultado, :objetivo, :decididas, 0, now()) RETURNING id"
)
COLUMNAS_FEATURE_0011 = {
    "ejecucion_decision_v2_id": None,
    "cuenta_canonica_id": None,
    "corte_canonico_id": None,
    "dias_atraso": 65,
    "saldo_total": Decimal("62000.00"),
    "producto": "CONSUMO",
    "cortes_observados": 1,
    "reingresos_observados": 0,
    "gestiones_7d": 0,
    "gestiones_30d": 0,
    "gestiones_sin_contacto_7d": 0,
    "contactos_titular_30d": 0,
    "contactos_tercero_30d": 0,
    "visitas_30d": 0,
    "gestiones_contactables_30d": 0,
    "tasa_contacto_titular_30d": None,
    "dias_desde_ultima_gestion": None,
    "dias_desde_ultimo_pago": None,
    "promesa_vigente": False,
    "promesas_30d": 0,
    "promesas_90d": 0,
    "promesas_cumplidas_90d": 0,
    "promesas_parciales_90d": 0,
    "promesas_incumplidas_30d": 0,
    "promesas_incumplidas_90d": 0,
    "convenio_vigente": False,
    "convenios_90d": 0,
    "feature_hash": HUELLA,
}
INSERTAR_FEATURE_0011 = text(
    f"INSERT INTO feature_decision_cuenta ({', '.join(COLUMNAS_FEATURE_0011)}) VALUES "
    f"({', '.join(':' + c for c in COLUMNAS_FEATURE_0011)}) RETURNING cuenta_canonica_id"
)
INSERTAR_RESULTADO_0011 = text(
    "INSERT INTO resultado_decision_v2 (ejecucion_decision_v2_id, cuenta_canonica_id, "
    "score_nivel_base, ajuste_total, score_nivel_final, prioridad_base, prioridad_final, "
    "canal_recomendado, motivos, decision_hash) VALUES (:ejecucion, :cuenta, :nivel_base, "
    ":ajuste, :nivel_final, :base, :final, :canal, CAST('[]' AS JSONB), :hash) "
    "RETURNING cuenta_canonica_id"
)
INSERTAR_TRABAJO_0011 = text(
    "INSERT INTO trabajo_orquestacion (trabajo_id, tipo, estado, ejecucion_decision_v2_id, "
    "ejecucion_atribucion_id, intentos, max_intentos, creado_en, disponible_desde, worker_id, "
    "lease_hasta, tomado_en) VALUES (:trabajo_id, :tipo, :estado, :decision_v2, :atribucion, "
    ":intentos, 5, now(), now(), :worker_id, :lease_hasta, now()) RETURNING id"
)


def _modulo_0011():
    return ScriptDirectory.from_config(_alembic()).get_revision("0011").module


def _decision_v2(corte: int, estado: str = "EN_PROCESO", **cambios) -> dict:
    terminada = estado != "EN_PROCESO"
    return {
        "decision_run_id": uuid4(),
        "corte": corte,
        "known_at": datetime(2026, 9, 3, 12, tzinfo=UTC),
        "hash_politica": "a" * 64,
        "firma": uuid4().hex * 2,
        "estado": estado,
        "resultado": "DECISION_PUBLICADA"
        if estado == "EXITOSA"
        else "ERROR_INTERNO"
        if terminada
        else None,
        "objetivo": 1,
        "decididas": 1 if estado == "EXITOSA" else 0,
        **cambios,
    }


def _feature_0011(ejecucion: int, corte: int, cuenta: int, **cambios) -> dict:
    return {
        **COLUMNAS_FEATURE_0011,
        "ejecucion_decision_v2_id": ejecucion,
        "cuenta_canonica_id": cuenta,
        "corte_canonico_id": corte,
        **cambios,
    }


def _resultado_0011(ejecucion: int, cuenta: int, **cambios) -> dict:
    return {
        "ejecucion": ejecucion,
        "cuenta": cuenta,
        "nivel_base": 3,
        "ajuste": 1,
        "nivel_final": 4,
        "base": "ALTA",
        "final": "MUY_ALTA",
        "canal": "CAMPO",
        "hash": HUELLA,
        **cambios,
    }


def _trabajo_0011(tipo: str = "DECISION_V2", estado: str = "PENDIENTE", **cambios) -> dict:
    ejecutando = estado == "EJECUTANDO"
    return {
        "trabajo_id": uuid4(),
        "tipo": tipo,
        "estado": estado,
        "decision_v2": None,
        "atribucion": None,
        "intentos": 1 if ejecutando else 0,
        "worker_id": "worker-de-prueba:1:abc" if ejecutando else None,
        "lease_hasta": datetime.now(UTC) + timedelta(minutes=1) if ejecutando else None,
        **cambios,
    }


def _de_la_v09(motor: Engine) -> dict[str, list[dict]]:
    """Lo que la 0011 no puede tocar, completo y en orden de su llave: el lifecycle, la atribucion,
    la evaluacion de promesas, el motor de pagos y el modelo historico."""
    llaves = {
        "evento_lifecycle": "id",
        "gestion_cobranza": "id",
        "promesa_pago": "id",
        "ejecucion_atribucion": "id",
        "atribucion_movimiento": "ejecucion_atribucion_id, movimiento_economico_canonico_id",
        "ejecucion_evaluacion_promesas": "id",
        "ejecucion_motor_pagos": "id",
        "movimiento_economico_canonico": "id",
        "cuenta_canonica": "id",
        "corte_canonico": "id",
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
class BaseEn0011:
    """Una base de v0.9 con su lifecycle (una gestion, su promesa y una atribucion publicada) y un
    corte de una cuenta, subida a la 0011: decision/v2 vacia."""

    motor: Engine
    base: BaseEn0010
    corte: int
    cuenta: int
    previas: dict[str, list[dict]]
    de_la_v09: dict[str, list[dict]]


@pytest.fixture
def base_en_0011(base_en_0010) -> BaseEn0011:
    b = base_en_0010
    motor = b.motor
    evento, gestion = _gestion_completa(b)
    _promesa_de(b, evento, gestion)
    publicada = {"resultado": "ATRIBUCION_PUBLICADA", "motor": b.ejecucion_motor}
    (atribucion,) = _insertar(
        motor,
        INSERTAR_ATRIBUCION,
        _atribucion("EXITOSA", movimientos_evaluados=1, asociados=1, candidatos=1, **publicada),
    )
    _insertar(
        motor,
        INSERTAR_ATRIBUCION_MOVIMIENTO,
        {
            "ejecucion": atribucion,
            "movimiento": b.movimiento,
            "gestion": gestion,
            "movimiento_id": b.movimiento_id,
            "cuenta": b.cuenta,
            "candidatos": 1,
            "clasificacion": "ASOCIACION_UNICA",
        },
    )
    (corte,) = _consultar(motor, "SELECT id FROM corte_canonico")
    previas = _por_columna(motor, PREVIAS_A_LA_0008)
    de_la_v09 = _de_la_v09(motor)

    command.upgrade(_alembic(), "0011")
    return BaseEn0011(motor, b, corte, b.cuenta, previas, de_la_v09)


def test_la_0011_sigue_a_la_0010():
    scripts = ScriptDirectory.from_config(_alembic())

    assert scripts.get_revision("0011").down_revision == "0010"


def test_los_modelos_declaran_el_esquema_de_la_0011():
    from motor_cartera.db import modelos

    tablas = [modelos.SQLModel.metadata.tables[t] for t in sorted(TABLAS_DE_LA_0011)]
    preparador = postgresql.dialect().identifier_preparer
    nombres = {preparador.format_constraint(c) for t in tablas for c in t.constraints}
    assert nombres == RESTRICCIONES_DE_LA_0011
    assert all(len(nombre) <= 63 for nombre in nombres)
    # Las tablas que crecen con cada ejecucion no tienen id propio: su llave es la natural, y el
    # resultado apunta a sus features por ella.
    for tabla in ("feature_decision_cuenta", "resultado_decision_v2"):
        llave = [c.name for c in modelos.SQLModel.metadata.tables[tabla].primary_key]
        assert llave == ["ejecucion_decision_v2_id", "cuenta_canonica_id"], tabla
        assert "id" not in modelos.SQLModel.metadata.tables[tabla].c
    trabajo = TrabajoOrquestacion.__table__
    assert "DECISION_V2" in trabajo.c.tipo.type.enums
    assert trabajo.c.ejecucion_decision_v2_id.nullable is True
    llaves = {preparador.format_constraint(c) for c in trabajo.constraints}
    assert RESTRICCIONES_DE_LA_0011_EN_LA_COLA <= llaves


def test_la_0011_agrega_decision_v2_vacia_y_no_toca_nada_de_la_v09(base_en_0011):
    b = base_en_0011
    motor = b.motor

    # A. Cada fila de antes sigue ahi, identica; un trabajo solo gana su objetivo nuevo, vacio.
    assert _de_la_v09(motor) == b.de_la_v09
    despues = _por_columna(motor, PREVIAS_A_LA_0008)
    for tabla, filas in b.previas.items():
        assert len(despues[tabla]) == len(filas), tabla
        for antes, fila in zip(filas, despues[tabla], strict=True):
            fila = dict(fila)
            if tabla == "trabajo_orquestacion":
                assert fila.pop("ejecucion_decision_v2_id") is None
            assert fila == antes, tabla

    # B. Las tablas nuevas, vacias, con sus restricciones y sus indices, por nombre.
    assert TABLAS_DE_LA_0011 <= set(_consultar(motor, TABLAS))
    for tabla in sorted(TABLAS_DE_LA_0011):
        assert _consultar(motor, f"SELECT count(*) FROM {tabla}") == [0]
    assert set(_consultar(motor, RESTRICCIONES_0011)) == RESTRICCIONES_DE_LA_0011
    with motor.connect() as conexion:
        indices = {
            (tabla, nombre, unico, tuple(columnas), predicado)
            for tabla, nombre, unico, columnas, predicado in conexion.execute(text(INDICES_0011))
        }
    assert indices == INDICES_DE_LA_0011
    assert _consultar(motor, "SELECT version_num FROM alembic_version") == ["0011"]


def test_una_exitosa_por_firma_y_un_intento_activo_por_corte_y_versiones(base_en_0011):
    motor, corte = base_en_0011.motor, base_en_0011.corte
    firma = "c" * 64
    _insertar(motor, INSERTAR_DECISION_V2, _decision_v2(corte, "EXITOSA", firma=firma))
    activa = _decision_v2(corte)
    _insertar(motor, INSERTAR_DECISION_V2, activa)

    # La misma firma no se publica dos veces; otra firma del mismo corte si (otro known_at).
    _rechaza(
        motor,
        "ux_decision_v2_exitosa",
        INSERTAR_DECISION_V2,
        _decision_v2(corte, "EXITOSA", firma=firma),
    )
    _insertar(motor, INSERTAR_DECISION_V2, _decision_v2(corte, "EXITOSA"))
    # A lo mas un intento activo por corte y versiones, aunque traiga otra firma.
    _rechaza(motor, "ux_decision_v2_en_proceso", INSERTAR_DECISION_V2, _decision_v2(corte))
    # Las FALLIDA son historia: tantas como haya, con la misma firma.
    _insertar(
        motor,
        INSERTAR_DECISION_V2,
        _decision_v2(corte, "FALLIDA", firma=firma),
        _decision_v2(corte, "FALLIDA", firma=firma),
    )


@pytest.mark.parametrize(
    ("restriccion", "cambios"),
    [
        ("ck_decision_v2_firmas", {"firma": "NO-ES-HEX"}),
        ("ck_decision_v2_firmas", {"hash_politica": "a" * 63}),
        ("ck_decision_v2_resultado", {"resultado": "DECISION_PUBLICADA"}),
        ("ck_decision_v2_conteos", {"objetivo": -1}),
        ("ck_decision_v2_publicacion", {"decididas": 1}),
        ("ck_ejecucion_decision_v2_estado_decision_v2", {"estado": "RECHAZADA"}),
        ("fk_decision_v2_corte", {"corte": 999_999}),
    ],
    ids=["firma", "hash", "resultado", "conteos", "publicacion", "estado", "corte"],
)
def test_una_ejecucion_dice_lo_que_publico_con_sus_firmas(base_en_0011, restriccion, cambios):
    b = base_en_0011
    corte = cambios.pop("corte", b.corte)
    _rechaza(b.motor, restriccion, INSERTAR_DECISION_V2, _decision_v2(corte, **cambios))


def test_una_exitosa_decidio_cada_cuenta_y_ninguna_fallo(base_en_0011):
    b = base_en_0011
    for cambios in (
        {"objetivo": 0, "decididas": 0},
        {"objetivo": 2, "decididas": 1},
    ):
        _rechaza(
            b.motor,
            "ck_decision_v2_publicacion",
            INSERTAR_DECISION_V2,
            _decision_v2(b.corte, "EXITOSA", **cambios),
        )


def _otro_corte(b: BaseEn0011, cuenta: int) -> int:
    """Un segundo corte de la cartera, de otro dataset, con un snapshot de `cuenta`."""
    motor = b.motor
    original, parquet = _insertar(
        motor,
        INSERTAR_ARTEFACTO,
        _artefacto(b"otra cartera oficial"),
        _artefacto(b"otro parquet", formato="parquet"),
    )
    (corrida,) = _insertar(
        motor, INSERTAR_CORRIDA_0007, _corrida_0007("EXITOSA", original, firma="9" * 64)
    )
    (dataset,) = _insertar(
        motor,
        INSERTAR_CONFORMADO,
        {
            "dataset_id": uuid4(),
            "corrida_id": corrida,
            "original": original,
            "conformado": parquet,
            "firma": "8" * 64,
            "filas": 1,
            "columnas": 93,
        },
    )
    (corte,) = _insertar(
        motor,
        INSERTAR_CORTE,
        {
            "corte_id": uuid4(),
            "fecha": "2026-09-09",
            "firma": "8" * 64,
            "cuentas": 1,
            "dataset": dataset,
        },
    )
    _insertar(
        motor,
        INSERTAR_SNAPSHOT,
        {"corte": corte, "cuenta": cuenta, "fecha": "2026-09-09", "fila": 2},
    )
    return corte


def test_las_features_son_de_cuentas_del_corte_de_su_ejecucion(base_en_0011):
    b = base_en_0011
    motor = b.motor
    (ejecucion,) = _insertar(motor, INSERTAR_DECISION_V2, _decision_v2(b.corte))
    (otra_cuenta,) = _insertar(
        motor,
        INSERTAR_CUENTA_CANONICA,
        {"cuenta_id": uuid4(), "cartera": "CARTERA_PRINCIPAL", "cliente": "CU0000000002"},
    )
    otro_corte = _otro_corte(b, b.cuenta)

    # Una cuenta sin snapshot en el corte: la base no la deja.
    _rechaza(
        motor,
        "fk_feature_decision_snapshot",
        INSERTAR_FEATURE_0011,
        _feature_0011(ejecucion, b.corte, otra_cuenta),
    )
    # El snapshot de la cuenta en otro corte, que no es el de su ejecucion: tampoco.
    _rechaza(
        motor,
        "fk_feature_decision_ejecucion",
        INSERTAR_FEATURE_0011,
        _feature_0011(ejecucion, otro_corte, b.cuenta),
    )
    _insertar(motor, INSERTAR_FEATURE_0011, _feature_0011(ejecucion, b.corte, b.cuenta))
    # Una vez por ejecucion.
    _rechaza(
        motor,
        "pk_feature_decision_cuenta",
        INSERTAR_FEATURE_0011,
        _feature_0011(ejecucion, b.corte, b.cuenta),
    )


@pytest.mark.parametrize(
    ("restriccion", "cambios"),
    [
        ("ck_feature_decision_hash", {"feature_hash": b"corta"}),
        ("ck_feature_decision_conteos", {"cortes_observados": 0}),
        ("ck_feature_decision_conteos", {"gestiones_7d": 2, "gestiones_30d": 1}),
        ("ck_feature_decision_conteos", {"dias_desde_ultimo_pago": -1}),
        # Sin denominador, la tasa es NULL; con el, no.
        ("ck_feature_decision_tasa", {"tasa_contacto_titular_30d": Decimal("0.5000")}),
        (
            "ck_feature_decision_tasa",
            {"gestiones_30d": 2, "gestiones_contactables_30d": 2},
        ),
    ],
    ids=["hash", "cortes", "ventanas", "dias", "tasa-sin-denominador", "denominador-sin-tasa"],
)
def test_las_features_son_coherentes_entre_si(base_en_0011, restriccion, cambios):
    b = base_en_0011
    (ejecucion,) = _insertar(b.motor, INSERTAR_DECISION_V2, _decision_v2(b.corte))

    _rechaza(
        b.motor,
        restriccion,
        INSERTAR_FEATURE_0011,
        _feature_0011(ejecucion, b.corte, b.cuenta, **cambios),
    )


@pytest.mark.parametrize(
    "cambios",
    [
        {"nivel_final": 3},  # no es el base mas el ajuste
        {"ajuste": 3, "nivel_final": 5, "final": "MUY_ALTA"},  # fuera de la escala
        {"ajuste": 3, "nivel_final": 4},  # acotado esta bien; la prioridad no dice 4
        {"base": "MEDIA"},  # la prioridad no es la de su nivel
        {"hash": b"corto"},
    ],
    ids=["suma", "escala", "acotado-incoherente", "prioridad", "hash"],
)
def test_el_nivel_final_es_el_base_mas_el_ajuste_acotado_a_la_escala(base_en_0011, cambios):
    b = base_en_0011
    (ejecucion,) = _insertar(b.motor, INSERTAR_DECISION_V2, _decision_v2(b.corte))
    _insertar(b.motor, INSERTAR_FEATURE_0011, _feature_0011(ejecucion, b.corte, b.cuenta))
    restriccion = (
        "ck_resultado_decision_v2_hash" if "hash" in cambios else "ck_resultado_decision_v2_niveles"
    )
    if cambios == {"ajuste": 3, "nivel_final": 4}:
        cambios = {**cambios, "final": "ALTA"}

    _rechaza(
        b.motor,
        restriccion,
        INSERTAR_RESULTADO_0011,
        _resultado_0011(ejecucion, b.cuenta, **cambios),
    )
    # MUY_ALTA con un ajuste que la pasaria de la escala queda acotada: la base la admite.
    _insertar(
        b.motor,
        INSERTAR_RESULTADO_0011,
        _resultado_0011(ejecucion, b.cuenta, ajuste=2, nivel_final=4),
    )


def test_un_resultado_apunta_a_sus_features_y_nada_se_borra_en_cascada(base_en_0011):
    b = base_en_0011
    motor = b.motor
    (ejecucion,) = _insertar(motor, INSERTAR_DECISION_V2, _decision_v2(b.corte))

    # Sin sus features, no hay resultado.
    _rechaza(
        motor,
        "fk_resultado_decision_v2_feature",
        INSERTAR_RESULTADO_0011,
        _resultado_0011(ejecucion, b.cuenta),
    )
    _insertar(motor, INSERTAR_FEATURE_0011, _feature_0011(ejecucion, b.corte, b.cuenta))
    _insertar(motor, INSERTAR_RESULTADO_0011, _resultado_0011(ejecucion, b.cuenta))
    for sentencia, restriccion in (
        ("DELETE FROM ejecucion_decision_v2", "fk_feature_decision_ejecucion"),
        ("DELETE FROM snapshot_cuenta", "fk_feature_decision_snapshot"),
    ):
        _rechaza(motor, restriccion, text(sentencia), {})


@pytest.mark.parametrize(
    "sentencia",
    [
        "UPDATE feature_decision_cuenta SET gestiones_7d = gestiones_7d",
        "DELETE FROM feature_decision_cuenta",
        "UPDATE resultado_decision_v2 SET motivos = motivos",
        "DELETE FROM resultado_decision_v2",
        # Aunque no toque ninguna fila: es la sentencia la que no se permite.
        "DELETE FROM resultado_decision_v2 WHERE false",
    ],
)
def test_lo_que_una_decision_publico_no_se_reescribe(base_en_0011, sentencia):
    b = base_en_0011
    motor = b.motor
    (ejecucion,) = _insertar(motor, INSERTAR_DECISION_V2, _decision_v2(b.corte, "EXITOSA"))
    _insertar(motor, INSERTAR_FEATURE_0011, _feature_0011(ejecucion, b.corte, b.cuenta))
    _insertar(motor, INSERTAR_RESULTADO_0011, _resultado_0011(ejecucion, b.cuenta))

    with pytest.raises(DBAPIError) as error, motor.begin() as conexion:
        conexion.execute(text(sentencia))

    assert error.value.orig.sqlstate == "23001"  # restrict_violation
    assert error.value.orig.diag.constraint_name == "tr_decision_v2_inmutable"
    assert _consultar(motor, "SELECT count(*) FROM resultado_decision_v2") == [1]


def test_el_trabajo_decision_v2_apunta_a_su_ejecucion_y_a_nada_mas(base_en_0011):
    b = base_en_0011
    motor = b.motor
    (ejecucion,) = _insertar(motor, INSERTAR_DECISION_V2, _decision_v2(b.corte))
    _insertar(motor, INSERTAR_TRABAJO_0011, _trabajo_0011(decision_v2=ejecucion))
    (atribucion,) = _consultar(motor, "SELECT max(id) FROM ejecucion_atribucion")
    for restriccion, trabajo in (
        ("uq_trabajo_decision_v2", _trabajo_0011(decision_v2=ejecucion)),
        ("ck_trabajo_objetivo", _trabajo_0011()),
        ("ck_trabajo_objetivo", _trabajo_0011(decision_v2=ejecucion, atribucion=atribucion)),
        ("ck_trabajo_objetivo", _trabajo_0011("ATRIBUCION", decision_v2=ejecucion)),
        ("fk_trabajo_decision_v2", _trabajo_0011(decision_v2=999_999)),
        ("ck_trabajo_orquestacion_tipo_trabajo", _trabajo_0011("DECISION_V3")),
    ):
        _rechaza(motor, restriccion, INSERTAR_TRABAJO_0011, trabajo)


def test_la_0011_baja_cerrando_lo_que_estaba_en_curso_y_vuelve_a_subir(base_en_0011):
    b = base_en_0011
    motor = b.motor
    # Una decision publicada con sus features y su resultado, y otra en curso con su trabajo.
    (publicada,) = _insertar(motor, INSERTAR_DECISION_V2, _decision_v2(b.corte, "EXITOSA"))
    _insertar(motor, INSERTAR_FEATURE_0011, _feature_0011(publicada, b.corte, b.cuenta))
    _insertar(motor, INSERTAR_RESULTADO_0011, _resultado_0011(publicada, b.cuenta))
    (activa,) = _insertar(motor, INSERTAR_DECISION_V2, _decision_v2(b.corte))
    _insertar(motor, INSERTAR_TRABAJO_0011, _trabajo_0011(estado="EJECUTANDO", decision_v2=activa))
    previas = _por_columna(motor, PREVIAS_A_LA_0008)
    trabajos_antes = [
        {k: v for k, v in f.items() if k != "ejecucion_decision_v2_id"}
        for f in previas["trabajo_orquestacion"]
        if f["tipo"] != "DECISION_V2"
    ]
    assert len(previas["trabajo_orquestacion"]) == len(trabajos_antes) + 1

    command.downgrade(_alembic(), "0010")

    # decision/v2 entera se fue, con su trabajo. Lo de v0.9, intacto: el lifecycle, la atribucion,
    # las promesas, los pagos, la historia y todo lo anterior; los otros trabajos, sin su columna
    # nueva.
    assert not TABLAS_DE_LA_0011 & set(_consultar(motor, TABLAS))
    assert "decision_v2_inmutable" not in set(_consultar(motor, FUNCIONES))
    assert _de_la_v09(motor) == b.de_la_v09
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
    assert not any("DECISION_V2" in d for d in definiciones)
    assert all("EVALUACION_PROMESAS" in d for d in definiciones)
    assert _consultar(motor, "SELECT version_num FROM alembic_version") == ["0010"]

    # Y vuelve a subir limpia: decision/v2 vacia, con sus restricciones.
    command.upgrade(_alembic(), "0011")

    for tabla in sorted(TABLAS_DE_LA_0011):
        assert _consultar(motor, f"SELECT count(*) FROM {tabla}") == [0]
    assert set(_consultar(motor, RESTRICCIONES_0011)) == RESTRICCIONES_DE_LA_0011
    assert "decision_v2_inmutable" in set(_consultar(motor, FUNCIONES))
    assert _de_la_v09(motor) == b.de_la_v09
    assert _consultar(motor, "SELECT version_num FROM alembic_version") == ["0011"]


def test_al_bajar_la_0011_lo_primero_es_cerrar_la_decision_v2_en_curso(base_en_0011):
    # Lo que la 0011 hace antes de quitar sus tablas, con sus mismas sentencias: la ejecucion en
    # curso queda FALLIDA y su trabajo FALLIDO, sin dueno ni lease, con el motivo. Lo terminado no
    # se toca.
    b = base_en_0011
    motor = b.motor
    modulo = _modulo_0011()
    activa, fallida = _insertar(
        motor,
        INSERTAR_DECISION_V2,
        _decision_v2(b.corte),
        _decision_v2(b.corte, "FALLIDA"),
    )
    _insertar(motor, INSERTAR_TRABAJO_0011, _trabajo_0011(estado="EJECUTANDO", decision_v2=activa))

    with motor.begin() as conexion:
        conexion.execute(modulo.CERRAR_DECISIONES)
        conexion.execute(modulo.CERRAR_TRABAJOS)

    with motor.connect() as conexion:
        decisiones = conexion.execute(
            text("SELECT id, estado, resultado, detalle FROM ejecucion_decision_v2 ORDER BY id")
        ).all()
        trabajo = conexion.execute(
            text(
                "SELECT estado, worker_id, lease_hasta, ultimo_error FROM trabajo_orquestacion "
                "WHERE tipo = 'DECISION_V2'"
            )
        ).one()
    assert [tuple(d) for d in decisiones] == [
        (activa, "FALLIDA", "CERRADA_AL_BAJAR", modulo.CIERRE),
        (fallida, "FALLIDA", "ERROR_INTERNO", None),
    ]
    assert tuple(trabajo) == ("FALLIDO", None, None, modulo.CIERRE)


# --- la 0012: la geografia real, las bases operativas y territorial/v2 ---------------------------

TABLAS_DE_LA_0012 = {
    "version_geografia",
    "municipio_geografico",
    "ejecucion_geografia",
    "resultado_geografia_cuenta",
    "version_bases_operativas",
    "base_operativa",
    "cobertura_base_municipio",
    "ejecucion_territorial_v2",
    "asignacion_territorial_v2",
}
_DE_LA_0012 = ", ".join(f"'{tabla}'" for tabla in sorted(TABLAS_DE_LA_0012))
RESTRICCIONES_0012 = RESTRICCIONES.replace("'ejecucion_decision', 'decision_cuenta'", _DE_LA_0012)
INDICES_0012 = INDICES.replace("'ejecucion_decision', 'decision_cuenta'", _DE_LA_0012)
RESTRICCIONES_DE_LA_0012 = {
    "pk_version_geografia",
    "uq_version_geografia_geografia_version_id",
    "ck_version_geografia_sha256",
    "ck_version_geografia_origen",
    "ck_version_geografia_conteos",
    "ck_version_geografia_terminada",
    "ck_version_geografia_estado_version_geografia",
    "ck_version_geografia_nivel_geografia",
    "pk_municipio_geografico",
    "fk_municipio_version",
    "uq_municipio_version_cvegeo",
    "ck_municipio_claves",
    "ck_municipio_geometria",
    "pk_ejecucion_geografia",
    "uq_ejecucion_geografia_geografia_run_id",
    "uq_geografia_id_corte",
    "fk_geografia_corte",
    "fk_geografia_dataset",
    "fk_geografia_version",
    "ck_geografia_firma",
    "ck_geografia_resultado",
    "ck_geografia_conteos",
    "ck_geografia_publicacion",
    "ck_ejecucion_geografia_estado_geografia_ejecucion",
    "pk_resultado_geografia_cuenta",
    "fk_resultado_geografia_ejecucion",
    "fk_resultado_geografia_snapshot",
    "fk_resultado_geografia_municipio",
    "ck_resultado_geografia_estado",
    "ck_resultado_geografia_observadas",
    "ck_resultado_geografia_fuente",
    "pk_version_bases_operativas",
    "uq_version_bases_operativas_bases_version_id",
    "uq_version_bases_hash",
    "ck_version_bases_hash",
    "ck_version_bases_conteos",
    "ck_version_bases_operativas_estado_version_bases",
    "pk_base_operativa",
    "uq_base_operativa_base_id",
    "uq_base_id_version",
    "uq_base_version_codigo",
    "fk_base_version",
    "ck_base_codigo",
    "pk_cobertura_base_municipio",
    "fk_cobertura_base",
    "ck_cobertura_cvegeo",
    "pk_ejecucion_territorial_v2",
    "uq_ejecucion_territorial_v2_territorial_run_id",
    "uq_territorial_v2_entradas",
    "fk_territorial_v2_geografia",
    "fk_territorial_v2_bases",
    "ck_territorial_v2_firmas",
    "ck_territorial_v2_resultado",
    "ck_territorial_v2_conteos",
    "ck_territorial_v2_publicacion",
    "ck_ejecucion_territorial_v2_estado_territorial_v2",
    "pk_asignacion_territorial_v2",
    "fk_asignacion_v2_ejecucion",
    "fk_asignacion_v2_geografia",
    "fk_asignacion_v2_municipio",
    "fk_asignacion_v2_base",
    "ck_asignacion_v2_estado",
}
RESTRICCIONES_DE_LA_0012_EN_LA_COLA = {
    "fk_trabajo_geografia",
    "uq_trabajo_geografia",
    "fk_trabajo_territorial_v2",
    "uq_trabajo_territorial_v2",
}
_POR_ESTADO = "((estado)::text = '{}'::text)"
INDICES_DE_LA_0012 = {
    ("version_geografia", "pk_version_geografia", True, ("id",), None),
    (
        "version_geografia",
        "uq_version_geografia_geografia_version_id",
        True,
        ("geografia_version_id",),
        None,
    ),
    (
        "version_geografia",
        "ux_version_geografia_publicada",
        True,
        ("sha256", "capa"),
        _POR_ESTADO.format("PUBLICADA"),
    ),
    (
        "version_geografia",
        "ux_version_geografia_en_proceso",
        True,
        ("sha256", "capa"),
        _POR_ESTADO.format("EN_PROCESO"),
    ),
    ("municipio_geografico", "pk_municipio_geografico", True, ("id",), None),
    (
        "municipio_geografico",
        "uq_municipio_version_cvegeo",
        True,
        ("version_geografia_id", "cvegeo"),
        None,
    ),
    ("municipio_geografico", "ix_municipio_geografico_geometria", False, ("geometria",), None),
    ("ejecucion_geografia", "pk_ejecucion_geografia", True, ("id",), None),
    (
        "ejecucion_geografia",
        "uq_ejecucion_geografia_geografia_run_id",
        True,
        ("geografia_run_id",),
        None,
    ),
    ("ejecucion_geografia", "uq_geografia_id_corte", True, ("id", "corte_canonico_id"), None),
    (
        "ejecucion_geografia",
        "ux_geografia_exitosa",
        True,
        ("firma_inputs",),
        _POR_ESTADO.format("EXITOSA"),
    ),
    (
        "ejecucion_geografia",
        "ux_geografia_en_proceso",
        True,
        ("firma_inputs",),
        _POR_ESTADO.format("EN_PROCESO"),
    ),
    ("ejecucion_geografia", "ix_geografia_corte", False, ("corte_canonico_id", "id"), None),
    (
        "resultado_geografia_cuenta",
        "pk_resultado_geografia_cuenta",
        True,
        ("ejecucion_geografia_id", "cuenta_canonica_id"),
        None,
    ),
    (
        "resultado_geografia_cuenta",
        "ix_resultado_geografia_estado",
        False,
        ("ejecucion_geografia_id", "estado_geografia", "cuenta_canonica_id"),
        None,
    ),
    (
        "resultado_geografia_cuenta",
        "ix_resultado_geografia_municipio",
        False,
        ("ejecucion_geografia_id", "municipio_geografico_id", "cuenta_canonica_id"),
        None,
    ),
    (
        "resultado_geografia_cuenta",
        "ix_resultado_geografia_cuenta",
        False,
        ("cuenta_canonica_id", "ejecucion_geografia_id"),
        None,
    ),
    ("version_bases_operativas", "pk_version_bases_operativas", True, ("id",), None),
    (
        "version_bases_operativas",
        "uq_version_bases_operativas_bases_version_id",
        True,
        ("bases_version_id",),
        None,
    ),
    ("version_bases_operativas", "uq_version_bases_hash", True, ("hash_configuracion",), None),
    ("base_operativa", "pk_base_operativa", True, ("id",), None),
    ("base_operativa", "uq_base_operativa_base_id", True, ("base_id",), None),
    ("base_operativa", "uq_base_id_version", True, ("id", "version_bases_operativas_id"), None),
    (
        "base_operativa",
        "uq_base_version_codigo",
        True,
        ("version_bases_operativas_id", "codigo"),
        None,
    ),
    (
        "cobertura_base_municipio",
        "pk_cobertura_base_municipio",
        True,
        ("base_operativa_id", "cvegeo"),
        None,
    ),
    (
        "cobertura_base_municipio",
        "ix_cobertura_cvegeo",
        False,
        ("cvegeo", "base_operativa_id"),
        None,
    ),
    ("ejecucion_territorial_v2", "pk_ejecucion_territorial_v2", True, ("id",), None),
    (
        "ejecucion_territorial_v2",
        "uq_ejecucion_territorial_v2_territorial_run_id",
        True,
        ("territorial_run_id",),
        None,
    ),
    (
        "ejecucion_territorial_v2",
        "uq_territorial_v2_entradas",
        True,
        ("id", "ejecucion_geografia_id", "version_bases_operativas_id"),
        None,
    ),
    (
        "ejecucion_territorial_v2",
        "ux_territorial_v2_exitosa",
        True,
        ("firma_inputs",),
        _POR_ESTADO.format("EXITOSA"),
    ),
    (
        "ejecucion_territorial_v2",
        "ux_territorial_v2_en_proceso",
        True,
        ("firma_inputs",),
        _POR_ESTADO.format("EN_PROCESO"),
    ),
    (
        "ejecucion_territorial_v2",
        "ix_territorial_v2_corte",
        False,
        ("corte_canonico_id", "id"),
        None,
    ),
    (
        "asignacion_territorial_v2",
        "pk_asignacion_territorial_v2",
        True,
        ("ejecucion_territorial_v2_id", "cuenta_canonica_id"),
        None,
    ),
    (
        "asignacion_territorial_v2",
        "ix_asignacion_v2_base",
        False,
        ("ejecucion_territorial_v2_id", "base_operativa_id", "cuenta_canonica_id"),
        None,
    ),
    (
        "asignacion_territorial_v2",
        "ix_asignacion_v2_estado",
        False,
        ("ejecucion_territorial_v2_id", "estado_asignacion", "cuenta_canonica_id"),
        None,
    ),
    (
        "asignacion_territorial_v2",
        "ix_asignacion_v2_municipio",
        False,
        ("ejecucion_territorial_v2_id", "municipio_geografico_id", "cuenta_canonica_id"),
        None,
    ),
    (
        "asignacion_territorial_v2",
        "ix_asignacion_v2_cuenta",
        False,
        ("cuenta_canonica_id", "ejecucion_territorial_v2_id"),
        None,
    ),
}
"""Solo los de las llaves, la unicidad, las paginas que la API lee y el GiST de los municipios."""
TRIGGERS_DE_LA_0012 = {
    ("municipio_geografico", "tr_geografia_inmutable"),
    ("resultado_geografia_cuenta", "tr_geografia_inmutable"),
    ("version_bases_operativas", "tr_bases_operativas_inmutables"),
    ("base_operativa", "tr_bases_operativas_inmutables"),
    ("cobertura_base_municipio", "tr_bases_operativas_inmutables"),
    ("asignacion_territorial_v2", "tr_territorial_v2_inmutable"),
    ("version_geografia", "tr_version_geografia_terminada"),
}
FUNCIONES_DE_LA_0012 = {
    "geografia_inmutable",
    "bases_operativas_inmutables",
    "territorial_v2_inmutable",
    "version_geografia_terminada",
}
POSTGIS = "SELECT extname FROM pg_extension WHERE extname = 'postgis'"

INSERTAR_VERSION_GEOGRAFIA = text(
    "INSERT INTO version_geografia (geografia_version_id, proveedor, producto, edicion, nivel, "
    "capa, archivo, sha256, tamano_bytes, srid, srid_origen, estado, municipios, reparadas, "
    "metadatos, importada_en, terminada_en) VALUES (:geografia_version_id, 'SINTETICO', "
    "'Referencia de la migracion', 'migracion', 'MUNICIPAL', 'municipios', 'municipios.zip', "
    ":sha256, 100, 4326, 4326, :estado, :municipios, 0, CAST('{}' AS JSONB), now(), :terminada) "
    "RETURNING id"
)
INSERTAR_MUNICIPIO = text(
    "INSERT INTO municipio_geografico (version_geografia_id, registro_origen, cve_ent, cve_mun, "
    "cvegeo, nombre_oficial, reparada, geometria) VALUES (:version, 0, :cve_ent, :cve_mun, "
    ":cvegeo, 'Municipio sintetico', false, ST_GeomFromText(:wkt, :srid)) RETURNING id"
)
INSERTAR_GEOGRAFIA = text(
    "INSERT INTO ejecucion_geografia (geografia_run_id, corte_canonico_id, dataset_conformado_id, "
    "version_geografia_id, version_motor, firma_inputs, estado, resultado, cuentas_objetivo, "
    "validas, sin_coordenadas, incompletas, invalidas, fuera_cobertura, ambiguas, discrepantes, "
    "iniciada_en, terminada_en) VALUES (:geografia_run_id, :corte, :dataset, :version, "
    "'geografia/v1', :firma, :estado, :resultado, :objetivo, :validas, 0, 0, 0, 0, 0, 0, now(), "
    ":terminada) RETURNING id"
)
INSERTAR_RESULTADO_GEOGRAFIA = text(
    "INSERT INTO resultado_geografia_cuenta (latitud_observada, longitud_observada, source_row, "
    "ejecucion_geografia_id, cuenta_canonica_id, corte_canonico_id, municipio_geografico_id, "
    "estado_geografia, municipio_fuente_resuelto, coincide_municipio, punto, motivos) VALUES "
    "(:latitud, :longitud, 2, :ejecucion, :cuenta, :corte, :municipio, :estado, :fuente, "
    ":coincide, CASE WHEN CAST(:con_punto AS boolean) THEN ST_SetSRID(ST_MakePoint(-98.2, 19.05), "
    "4326) END, CAST('[]' AS JSONB)) RETURNING cuenta_canonica_id"
)
INSERTAR_VERSION_BASES = text(
    "INSERT INTO version_bases_operativas (bases_version_id, nombre, version_configuracion, "
    "hash_configuracion, estado, bases, activas, coberturas, creada_en) VALUES "
    "(:bases_version_id, 'Bases de la migracion', 'bases-operativas/v1', :hash, :estado, :bases, "
    ":activas, 1, now()) RETURNING id"
)
INSERTAR_BASE = text(
    "INSERT INTO base_operativa (base_id, version_bases_operativas_id, codigo, nombre, activa, "
    "punto) VALUES (:base_id, :version, :codigo, 'Base sintetica', true, "
    "ST_SetSRID(ST_MakePoint(-98.25, 19.05), 4326)) RETURNING id"
)
INSERTAR_COBERTURA = text(
    "INSERT INTO cobertura_base_municipio (base_operativa_id, cvegeo) VALUES (:base, :cvegeo) "
    "RETURNING base_operativa_id"
)
INSERTAR_TERRITORIAL_V2 = text(
    "INSERT INTO ejecucion_territorial_v2 (territorial_run_id, corte_canonico_id, "
    "ejecucion_geografia_id, version_bases_operativas_id, version_motor, version_politica, "
    "hash_politica, firma_inputs, estado, resultado, cuentas_objetivo, asignadas, "
    "revision_geografica, sin_base, fuera_cobertura, ambiguas, iniciada_en, terminada_en) VALUES "
    "(:territorial_run_id, :corte, :geografia, :bases, 'territorial/v2', 'territorial-public-v2', "
    ":hash_politica, :firma, :estado, :resultado, :objetivo, :asignadas, 0, 0, 0, 0, now(), "
    ":terminada) RETURNING id"
)
INSERTAR_ASIGNACION = text(
    "INSERT INTO asignacion_territorial_v2 (distancia_base_m, ejecucion_territorial_v2_id, "
    "cuenta_canonica_id, ejecucion_geografia_id, version_bases_operativas_id, "
    "municipio_geografico_id, base_operativa_id, estado_asignacion, motivos) VALUES (:distancia, "
    ":ejecucion, :cuenta, :geografia, :bases, :municipio, :base, :estado, CAST('[]' AS JSONB)) "
    "RETURNING cuenta_canonica_id"
)
INSERTAR_TRABAJO_0012 = text(
    "INSERT INTO trabajo_orquestacion (trabajo_id, tipo, estado, ejecucion_geografia_id, "
    "ejecucion_territorial_v2_id, intentos, max_intentos, creado_en, disponible_desde, worker_id, "
    "lease_hasta, tomado_en) VALUES (:trabajo_id, :tipo, :estado, :geografia, :territorial, "
    ":intentos, 5, now(), now(), :worker_id, :lease_hasta, now()) RETURNING id"
)
RECTANGULO_A = "MULTIPOLYGON(((-98.3 18.95,-98.1 18.95,-98.1 19.15,-98.3 19.15,-98.3 18.95)))"
CRUZADO = "MULTIPOLYGON(((-98.3 18.95,-98.1 19.15,-98.1 18.95,-98.3 19.15,-98.3 18.95)))"


def _modulo_0012():
    return ScriptDirectory.from_config(_alembic()).get_revision("0012").module


def _version_geografia(estado: str = "PUBLICADA", **cambios) -> dict:
    return {
        "geografia_version_id": uuid4(),
        "sha256": uuid4().hex * 2,
        "estado": estado,
        "municipios": 1 if estado == "PUBLICADA" else 0,
        "terminada": None if estado == "EN_PROCESO" else datetime.now(UTC),
        **cambios,
    }


def _municipio(version: int, **cambios) -> dict:
    return {
        "version": version,
        "cve_ent": "21",
        "cve_mun": "114",
        "cvegeo": "21114",
        "wkt": RECTANGULO_A,
        "srid": 4326,
        **cambios,
    }


def _geografia_0012(b: BaseEn0012, version: int, estado: str = "EN_PROCESO", **cambios) -> dict:
    terminada = estado != "EN_PROCESO"
    return {
        "geografia_run_id": uuid4(),
        "corte": b.corte,
        "dataset": b.dataset,
        "version": version,
        "firma": uuid4().hex * 2,
        "estado": estado,
        "resultado": "GEOGRAFIA_PUBLICADA"
        if estado == "EXITOSA"
        else "ERROR_INTERNO"
        if terminada
        else None,
        "objetivo": 1,
        "validas": 1 if estado == "EXITOSA" else 0,
        "terminada": datetime.now(UTC) if terminada else None,
        **cambios,
    }


def _resultado_geografia(
    b: BaseEn0012, ejecucion: int, municipio: int | None, estado: str = "VALIDA", **cambios
) -> dict:
    return {
        "latitud": 19.05,
        "longitud": -98.2,
        "ejecucion": ejecucion,
        "cuenta": b.cuenta,
        "corte": b.corte,
        "municipio": municipio,
        "estado": estado,
        "fuente": "21114",
        "coincide": True,
        "con_punto": True,
        **cambios,
    }


def _version_bases(**cambios) -> dict:
    return {
        "bases_version_id": uuid4(),
        "hash": uuid4().hex * 2,
        "estado": "PUBLICADA",
        "bases": 1,
        "activas": 1,
        **cambios,
    }


def _base(version: int, codigo: str = "BASE_A") -> dict:
    return {"base_id": uuid4(), "version": version, "codigo": codigo}


def _territorial_0012(
    b: BaseEn0012, geografia: int, bases: int, estado: str = "EN_PROCESO", **cambios
) -> dict:
    terminada = estado != "EN_PROCESO"
    return {
        "territorial_run_id": uuid4(),
        "corte": b.corte,
        "geografia": geografia,
        "bases": bases,
        "hash_politica": "a" * 64,
        "firma": uuid4().hex * 2,
        "estado": estado,
        "resultado": "TERRITORIAL_PUBLICADO"
        if estado == "EXITOSA"
        else "ERROR_INTERNO"
        if terminada
        else None,
        "objetivo": 1,
        "asignadas": 1 if estado == "EXITOSA" else 0,
        "terminada": datetime.now(UTC) if terminada else None,
        **cambios,
    }


def _asignacion(
    b: BaseEn0012, ejecucion: int, geografia: int, bases: int, municipio: int, base: int, **cambios
) -> dict:
    return {
        "distancia": Decimal("1052.61"),
        "ejecucion": ejecucion,
        "cuenta": b.cuenta,
        "geografia": geografia,
        "bases": bases,
        "municipio": municipio,
        "base": base,
        "estado": "ASIGNADA",
        **cambios,
    }


def _trabajo_0012(tipo: str = "GEOGRAFIA", estado: str = "PENDIENTE", **cambios) -> dict:
    ejecutando = estado == "EJECUTANDO"
    return {
        "trabajo_id": uuid4(),
        "tipo": tipo,
        "estado": estado,
        "geografia": None,
        "territorial": None,
        "intentos": 1 if ejecutando else 0,
        "worker_id": "worker-de-prueba:1:abc" if ejecutando else None,
        "lease_hasta": datetime.now(UTC) + timedelta(minutes=1) if ejecutando else None,
        **cambios,
    }


def _de_la_v010(motor: Engine) -> dict[str, list[dict]]:
    """Lo que la 0012 no puede tocar: todo lo de v0.9 y decision/v2."""
    filas = _de_la_v09(motor)
    llaves = {
        "ejecucion_decision_v2": "id",
        "feature_decision_cuenta": "ejecucion_decision_v2_id, cuenta_canonica_id",
        "resultado_decision_v2": "ejecucion_decision_v2_id, cuenta_canonica_id",
    }
    with motor.connect() as conexion:
        for tabla, orden in llaves.items():
            filas[tabla] = [
                dict(fila)
                for fila in conexion.execute(text(f"SELECT * FROM {tabla} ORDER BY {orden}"))
                .mappings()
                .all()
            ]
    return filas


@dataclass(frozen=True)
class BaseEn0012:
    """Una base de v0.10 con una decision/v2 publicada sobre su corte, subida a la 0012: la
    geografia, las bases y territorial/v2 vacias, con PostGIS."""

    motor: Engine
    corte: int
    cuenta: int
    dataset: int
    previas: dict[str, list[dict]]
    de_la_v010: dict[str, list[dict]]


@pytest.fixture
def base_en_0012(base_en_0011) -> BaseEn0012:
    b = base_en_0011
    motor = b.motor
    (decision,) = _insertar(motor, INSERTAR_DECISION_V2, _decision_v2(b.corte, "EXITOSA"))
    _insertar(motor, INSERTAR_FEATURE_0011, _feature_0011(decision, b.corte, b.cuenta))
    _insertar(motor, INSERTAR_RESULTADO_0011, _resultado_0011(decision, b.cuenta))
    (dataset,) = _consultar(
        motor, f"SELECT dataset_conformado_id FROM corte_canonico WHERE id = {b.corte}"
    )
    previas = _por_columna(motor, PREVIAS_A_LA_0008)
    de_la_v010 = _de_la_v010(motor)

    command.upgrade(_alembic(), "0012")
    return BaseEn0012(motor, b.corte, b.cuenta, dataset, previas, de_la_v010)


def _geografia_publicada(b: BaseEn0012) -> tuple[int, int, int]:
    """Una referencia con un municipio y una geografia EXITOSA del corte con su resultado."""
    motor = b.motor
    (version,) = _insertar(motor, INSERTAR_VERSION_GEOGRAFIA, _version_geografia())
    (municipio,) = _insertar(motor, INSERTAR_MUNICIPIO, _municipio(version))
    (geografia,) = _insertar(motor, INSERTAR_GEOGRAFIA, _geografia_0012(b, version, "EXITOSA"))
    _insertar(motor, INSERTAR_RESULTADO_GEOGRAFIA, _resultado_geografia(b, geografia, municipio))
    return version, municipio, geografia


def _bases_publicadas(motor: Engine, codigo: str = "BASE_A") -> tuple[int, int]:
    (bases,) = _insertar(motor, INSERTAR_VERSION_BASES, _version_bases())
    (base,) = _insertar(motor, INSERTAR_BASE, _base(bases, codigo))
    _insertar(motor, INSERTAR_COBERTURA, {"base": base, "cvegeo": "21114"})
    return bases, base


def test_la_0012_sigue_a_la_0011():
    scripts = ScriptDirectory.from_config(_alembic())

    assert scripts.get_revision("0012").down_revision == "0011"


def test_los_modelos_declaran_el_esquema_de_la_0012():
    from motor_cartera.db import modelos

    tablas = [modelos.SQLModel.metadata.tables[t] for t in sorted(TABLAS_DE_LA_0012)]
    preparador = postgresql.dialect().identifier_preparer
    nombres = {preparador.format_constraint(c) for t in tablas for c in t.constraints}
    # La 0013 le agrega a ejecucion_territorial_v2 una restriccion unica sobre columnas que ya
    # tenia: la que referencia cada plan de campo/v2.
    assert nombres == RESTRICCIONES_DE_LA_0012 | {"uq_territorial_v2_corte_bases"}
    assert all(len(nombre) <= 63 for nombre in nombres)
    # Lo que crece con cada ejecucion no tiene id propio: su llave es la natural, y una asignacion
    # apunta al resultado geografico de su cuenta por ella.
    for tabla, ejecucion in (
        ("resultado_geografia_cuenta", "ejecucion_geografia_id"),
        ("asignacion_territorial_v2", "ejecucion_territorial_v2_id"),
    ):
        llave = [c.name for c in modelos.SQLModel.metadata.tables[tabla].primary_key]
        assert llave == [ejecucion, "cuenta_canonica_id"], tabla
        assert "id" not in modelos.SQLModel.metadata.tables[tabla].c
    geometrias = {
        (t.name, c.name): c.type.get_col_spec()
        for t in tablas
        for c in t.columns
        if c.name in ("geometria", "punto")
    }
    assert geometrias == {
        ("municipio_geografico", "geometria"): "geometry(MULTIPOLYGON,4326)",
        ("resultado_geografia_cuenta", "punto"): "geometry(POINT,4326)",
        ("base_operativa", "punto"): "geometry(POINT,4326)",
    }
    trabajo = TrabajoOrquestacion.__table__
    assert {"GEOGRAFIA", "TERRITORIAL_V2"} <= set(trabajo.c.tipo.type.enums)
    assert trabajo.c.ejecucion_geografia_id.nullable is True
    assert trabajo.c.ejecucion_territorial_v2_id.nullable is True
    llaves = {preparador.format_constraint(c) for c in trabajo.constraints}
    assert RESTRICCIONES_DE_LA_0012_EN_LA_COLA <= llaves


def test_la_0012_instala_postgis_y_agrega_la_geografia_vacia_sin_tocar_nada_de_la_v010(
    base_en_0012,
):
    b = base_en_0012
    motor = b.motor

    # A. Cada fila de antes sigue ahi, identica; un trabajo solo gana sus dos objetivos nuevos,
    # vacios.
    assert _de_la_v010(motor) == b.de_la_v010
    despues = _por_columna(motor, PREVIAS_A_LA_0008)
    for tabla, filas in b.previas.items():
        assert len(despues[tabla]) == len(filas), tabla
        for antes, fila in zip(filas, despues[tabla], strict=True):
            fila = dict(fila)
            if tabla == "trabajo_orquestacion":
                assert fila.pop("ejecucion_geografia_id") is None
                assert fila.pop("ejecucion_territorial_v2_id") is None
            assert fila == antes, tabla

    # B. PostGIS, y las tablas nuevas vacias, con sus restricciones, sus indices (el GiST de los
    # municipios incluido) y sus triggers, por nombre.
    assert _consultar(motor, POSTGIS) == ["postgis"]
    assert TABLAS_DE_LA_0012 <= set(_consultar(motor, TABLAS))
    for tabla in sorted(TABLAS_DE_LA_0012):
        assert _consultar(motor, f"SELECT count(*) FROM {tabla}") == [0]
    assert set(_consultar(motor, RESTRICCIONES_0012)) == RESTRICCIONES_DE_LA_0012
    with motor.connect() as conexion:
        indices = {
            (tabla, nombre, unico, tuple(columnas), predicado)
            for tabla, nombre, unico, columnas, predicado in conexion.execute(text(INDICES_0012))
        }
        triggers = {tuple(fila) for fila in conexion.execute(text(TRIGGERS))}
    assert indices == INDICES_DE_LA_0012
    assert _consultar(
        motor,
        "SELECT am.amname FROM pg_class i JOIN pg_am am ON am.oid = i.relam "
        "WHERE i.relname = 'ix_municipio_geografico_geometria'",
    ) == ["gist"]
    assert TRIGGERS_DE_LA_0012 <= triggers
    assert FUNCIONES_DE_LA_0012 <= set(_consultar(motor, FUNCIONES))
    assert _consultar(motor, "SELECT version_num FROM alembic_version") == ["0012"]


def test_una_version_de_la_referencia_solo_cambia_mientras_se_importa(base_en_0012):
    motor = base_en_0012.motor
    (version,) = _insertar(motor, INSERTAR_VERSION_GEOGRAFIA, _version_geografia("EN_PROCESO"))
    publicar = text(
        "UPDATE version_geografia SET estado = 'PUBLICADA', municipios = 1, terminada_en = now() "
        "WHERE id = :id"
    )
    with motor.begin() as conexion:
        conexion.execute(publicar, {"id": version})

    for sentencia in (
        "UPDATE version_geografia SET detalle = 'otro' WHERE id = :id",
        "DELETE FROM version_geografia WHERE id = :id",
    ):
        with pytest.raises(DBAPIError) as error, motor.begin() as conexion:
            conexion.execute(text(sentencia), {"id": version})
        assert error.value.orig.sqlstate == "23001"  # restrict_violation
        assert error.value.orig.diag.constraint_name == "tr_version_geografia_terminada"


def test_un_archivo_y_su_capa_se_publican_una_vez(base_en_0012):
    motor = base_en_0012.motor
    sha256 = "d" * 64
    _insertar(motor, INSERTAR_VERSION_GEOGRAFIA, _version_geografia(sha256=sha256))
    _insertar(motor, INSERTAR_VERSION_GEOGRAFIA, _version_geografia("EN_PROCESO", sha256=sha256))

    for restriccion, estado in (
        ("ux_version_geografia_publicada", "PUBLICADA"),
        ("ux_version_geografia_en_proceso", "EN_PROCESO"),
    ):
        _rechaza(
            motor,
            restriccion,
            INSERTAR_VERSION_GEOGRAFIA,
            _version_geografia(estado, sha256=sha256),
        )
    # Las FALLIDA son historia: tantas como haya.
    _insertar(
        motor,
        INSERTAR_VERSION_GEOGRAFIA,
        _version_geografia("FALLIDA", sha256=sha256),
        _version_geografia("FALLIDA", sha256=sha256),
    )


@pytest.mark.parametrize(
    ("restriccion", "cambios"),
    [
        ("ck_version_geografia_sha256", {"sha256": "NO-ES-HEX"}),
        ("ck_version_geografia_conteos", {"municipios": 0}),
        ("ck_version_geografia_terminada", {"terminada": None}),
        ("ck_version_geografia_estado_version_geografia", {"estado": "BORRADOR"}),
    ],
    ids=["sha256", "conteos", "terminada", "estado"],
)
def test_una_version_de_la_referencia_dice_lo_que_publico(base_en_0012, restriccion, cambios):
    _rechaza(
        base_en_0012.motor,
        restriccion,
        INSERTAR_VERSION_GEOGRAFIA,
        _version_geografia(**cambios),
    )


def test_un_municipio_es_de_su_version_una_vez_con_claves_coherentes_y_geometria_valida(
    base_en_0012,
):
    motor = base_en_0012.motor
    (version,) = _insertar(motor, INSERTAR_VERSION_GEOGRAFIA, _version_geografia())
    _insertar(motor, INSERTAR_MUNICIPIO, _municipio(version))

    for restriccion, cambios in (
        ("uq_municipio_version_cvegeo", {}),
        ("ck_municipio_claves", {"cvegeo": "21115"}),
        ("ck_municipio_geometria", {"cvegeo": "21015", "cve_mun": "015", "wkt": CRUZADO}),
        ("fk_municipio_version", {"version": 999_999}),
    ):
        _rechaza(motor, restriccion, INSERTAR_MUNICIPIO, {**_municipio(version), **cambios})
    # La columna es geometry(MultiPolygon, 4326): ni otro SRID ni otro tipo.
    for cambios in ({"srid": 6372}, {"wkt": "POINT(-98.2 19.05)"}):
        with pytest.raises(DBAPIError), motor.begin() as conexion:
            conexion.execute(
                INSERTAR_MUNICIPIO,
                _municipio(version, cvegeo="21015", cve_mun="015", **cambios),
            )


def test_una_geografia_exitosa_por_firma_y_un_intento_activo_por_firma(base_en_0012):
    b = base_en_0012
    motor = b.motor
    (version,) = _insertar(motor, INSERTAR_VERSION_GEOGRAFIA, _version_geografia())
    firma = "c" * 64
    _insertar(motor, INSERTAR_GEOGRAFIA, _geografia_0012(b, version, "EXITOSA", firma=firma))
    _insertar(motor, INSERTAR_GEOGRAFIA, _geografia_0012(b, version, firma=firma))

    _rechaza(
        motor,
        "ux_geografia_exitosa",
        INSERTAR_GEOGRAFIA,
        _geografia_0012(b, version, "EXITOSA", firma=firma),
    )
    _rechaza(
        motor,
        "ux_geografia_en_proceso",
        INSERTAR_GEOGRAFIA,
        _geografia_0012(b, version, firma=firma),
    )
    _insertar(
        motor,
        INSERTAR_GEOGRAFIA,
        _geografia_0012(b, version, "FALLIDA", firma=firma),
        _geografia_0012(b, version, "FALLIDA", firma=firma),
    )


@pytest.mark.parametrize(
    ("restriccion", "cambios"),
    [
        ("ck_geografia_firma", {"firma": "NO-ES-HEX"}),
        ("ck_geografia_resultado", {"resultado": "GEOGRAFIA_PUBLICADA"}),
        ("ck_geografia_conteos", {"objetivo": -1}),
        ("ck_geografia_publicacion", {"validas": 1}),
        ("ck_ejecucion_geografia_estado_geografia_ejecucion", {"estado": "RECHAZADA"}),
        ("fk_geografia_dataset", {"dataset": 999_999}),
        ("fk_geografia_version", {"version": 999_999}),
    ],
    ids=["firma", "resultado", "conteos", "publicacion", "estado", "dataset", "version"],
)
def test_una_ejecucion_de_geografia_dice_lo_que_publico(base_en_0012, restriccion, cambios):
    b = base_en_0012
    (version,) = _insertar(b.motor, INSERTAR_VERSION_GEOGRAFIA, _version_geografia())
    _rechaza(
        b.motor,
        restriccion,
        INSERTAR_GEOGRAFIA,
        {**_geografia_0012(b, version), **cambios},
    )


@pytest.mark.parametrize(
    ("restriccion", "cambios"),
    [
        ("ck_resultado_geografia_estado", {"con_punto": False}),
        ("ck_resultado_geografia_estado", {"municipio": None}),
        ("ck_resultado_geografia_estado", {"coincide": False}),
        ("ck_resultado_geografia_estado", {"estado": "DISCREPANCIA_MUNICIPIO"}),
        ("ck_resultado_geografia_estado", {"estado": "CERCA"}),
        (
            "ck_resultado_geografia_observadas",
            {"estado": "SIN_COORDENADAS", "municipio": None, "con_punto": False, "coincide": None},
        ),
        (
            "ck_resultado_geografia_observadas",
            {
                "estado": "COORDENADAS_INVALIDAS",
                "longitud": None,
                "municipio": None,
                "con_punto": False,
                "coincide": None,
            },
        ),
        ("ck_resultado_geografia_fuente", {"fuente": "2111"}),
        ("fk_resultado_geografia_municipio", {"municipio": 999_999}),
        ("fk_resultado_geografia_snapshot", {"cuenta": 999_999}),
    ],
    ids=[
        "valida-sin-punto",
        "valida-sin-municipio",
        "valida-que-discrepa",
        "discrepancia-que-coincide",
        "estado",
        "sin-coordenadas-con-ellas",
        "invalida-incompleta",
        "fuente",
        "municipio",
        "snapshot",
    ],
)
def test_un_resultado_geografico_es_coherente_con_su_estado(base_en_0012, restriccion, cambios):
    b = base_en_0012
    motor = b.motor
    (version,) = _insertar(motor, INSERTAR_VERSION_GEOGRAFIA, _version_geografia())
    (municipio,) = _insertar(motor, INSERTAR_MUNICIPIO, _municipio(version))
    (geografia,) = _insertar(motor, INSERTAR_GEOGRAFIA, _geografia_0012(b, version))
    _rechaza(
        motor,
        restriccion,
        INSERTAR_RESULTADO_GEOGRAFIA,
        {**_resultado_geografia(b, geografia, municipio), **cambios},
    )


def test_un_resultado_geografico_es_de_una_cuenta_del_corte_de_su_ejecucion(base_en_0012):
    b = base_en_0012
    motor = b.motor
    (version,) = _insertar(motor, INSERTAR_VERSION_GEOGRAFIA, _version_geografia())
    (municipio,) = _insertar(motor, INSERTAR_MUNICIPIO, _municipio(version))
    (geografia,) = _insertar(motor, INSERTAR_GEOGRAFIA, _geografia_0012(b, version))
    otro = _otro_corte(b, b.cuenta)

    # La cuenta tiene snapshot en el otro corte, pero la ejecucion es de este.
    _rechaza(
        motor,
        "fk_resultado_geografia_ejecucion",
        INSERTAR_RESULTADO_GEOGRAFIA,
        _resultado_geografia(b, geografia, municipio, corte=otro),
    )
    _insertar(motor, INSERTAR_RESULTADO_GEOGRAFIA, _resultado_geografia(b, geografia, municipio))
    _rechaza(
        motor,
        "pk_resultado_geografia_cuenta",
        INSERTAR_RESULTADO_GEOGRAFIA,
        _resultado_geografia(b, geografia, municipio),
    )


def test_una_version_de_bases_sus_bases_y_su_cobertura_tienen_su_forma(base_en_0012):
    motor = base_en_0012.motor
    huella = "e" * 64
    (bases,) = _insertar(motor, INSERTAR_VERSION_BASES, _version_bases(hash=huella))
    (base,) = _insertar(motor, INSERTAR_BASE, _base(bases))
    _insertar(motor, INSERTAR_COBERTURA, {"base": base, "cvegeo": "21114"})

    for restriccion, sentencia, fila in (
        ("uq_version_bases_hash", INSERTAR_VERSION_BASES, _version_bases(hash=huella)),
        ("ck_version_bases_hash", INSERTAR_VERSION_BASES, _version_bases(hash="NO-ES-HEX")),
        ("ck_version_bases_conteos", INSERTAR_VERSION_BASES, _version_bases(activas=2)),
        (
            "ck_version_bases_operativas_estado_version_bases",
            INSERTAR_VERSION_BASES,
            _version_bases(estado="BORRADOR"),
        ),
        ("uq_base_version_codigo", INSERTAR_BASE, _base(bases)),
        ("ck_base_codigo", INSERTAR_BASE, _base(bases, "base a")),
        ("fk_base_version", INSERTAR_BASE, _base(999_999, "BASE_B")),
        ("pk_cobertura_base_municipio", INSERTAR_COBERTURA, {"base": base, "cvegeo": "21114"}),
        ("ck_cobertura_cvegeo", INSERTAR_COBERTURA, {"base": base, "cvegeo": "2111"}),
        ("fk_cobertura_base", INSERTAR_COBERTURA, {"base": 999_999, "cvegeo": "21015"}),
    ):
        _rechaza(motor, restriccion, sentencia, fila)


def test_una_asignacion_exitosa_por_firma_y_un_intento_activo_por_firma(base_en_0012):
    b = base_en_0012
    motor = b.motor
    _, _, geografia = _geografia_publicada(b)
    bases, _ = _bases_publicadas(motor)
    firma = "c" * 64
    exitosa = _territorial_0012(b, geografia, bases, "EXITOSA", firma=firma)
    _insertar(motor, INSERTAR_TERRITORIAL_V2, exitosa)
    _insertar(motor, INSERTAR_TERRITORIAL_V2, _territorial_0012(b, geografia, bases, firma=firma))

    _rechaza(
        motor,
        "ux_territorial_v2_exitosa",
        INSERTAR_TERRITORIAL_V2,
        _territorial_0012(b, geografia, bases, "EXITOSA", firma=firma),
    )
    _rechaza(
        motor,
        "ux_territorial_v2_en_proceso",
        INSERTAR_TERRITORIAL_V2,
        _territorial_0012(b, geografia, bases, firma=firma),
    )
    _insertar(
        motor,
        INSERTAR_TERRITORIAL_V2,
        _territorial_0012(b, geografia, bases, "FALLIDA", firma=firma),
        _territorial_0012(b, geografia, bases, "FALLIDA", firma=firma),
    )


@pytest.mark.parametrize(
    ("restriccion", "cambios"),
    [
        ("ck_territorial_v2_firmas", {"firma": "NO-ES-HEX"}),
        ("ck_territorial_v2_firmas", {"hash_politica": "a" * 63}),
        ("ck_territorial_v2_resultado", {"resultado": "TERRITORIAL_PUBLICADO"}),
        ("ck_territorial_v2_conteos", {"objetivo": -1}),
        ("ck_territorial_v2_publicacion", {"asignadas": 1}),
        ("ck_ejecucion_territorial_v2_estado_territorial_v2", {"estado": "RECHAZADA"}),
        ("fk_territorial_v2_geografia", {"geografia": 999_999}),
        ("fk_territorial_v2_bases", {"bases": 999_999}),
    ],
    ids=["firma", "hash", "resultado", "conteos", "publicacion", "estado", "geografia", "bases"],
)
def test_una_ejecucion_territorial_v2_declara_sus_entradas(base_en_0012, restriccion, cambios):
    b = base_en_0012
    _, _, geografia = _geografia_publicada(b)
    bases, _ = _bases_publicadas(b.motor)
    _rechaza(
        b.motor,
        restriccion,
        INSERTAR_TERRITORIAL_V2,
        {**_territorial_0012(b, geografia, bases), **cambios},
    )


def test_una_asignacion_es_de_su_ejecucion_su_geografia_y_sus_bases(base_en_0012):
    b = base_en_0012
    motor = b.motor
    _, municipio, geografia = _geografia_publicada(b)
    bases, base = _bases_publicadas(motor)
    otras_bases, otra_base = _bases_publicadas(motor, "BASE_B")
    (ejecucion,) = _insertar(motor, INSERTAR_TERRITORIAL_V2, _territorial_0012(b, geografia, bases))

    def asignacion(**cambios) -> dict:
        return {**_asignacion(b, ejecucion, geografia, bases, municipio, base), **cambios}

    for restriccion, fila in (
        ("ck_asignacion_v2_estado", asignacion(base=None)),
        ("ck_asignacion_v2_estado", asignacion(distancia=None)),
        ("ck_asignacion_v2_estado", asignacion(distancia=Decimal("-1.00"))),
        ("ck_asignacion_v2_estado", asignacion(estado="REVISION_GEOGRAFICA", base=None)),
        ("ck_asignacion_v2_estado", asignacion(estado="CERCA")),
        # Una base de otra version, o la ejecucion con otras bases.
        ("fk_asignacion_v2_base", asignacion(base=otra_base)),
        ("fk_asignacion_v2_ejecucion", asignacion(bases=otras_bases, base=otra_base)),
        ("fk_asignacion_v2_geografia", asignacion(cuenta=999_999)),
        ("fk_asignacion_v2_municipio", asignacion(municipio=999_999)),
    ):
        _rechaza(motor, restriccion, INSERTAR_ASIGNACION, fila)
    _insertar(motor, INSERTAR_ASIGNACION, asignacion())
    _rechaza(motor, "pk_asignacion_territorial_v2", INSERTAR_ASIGNACION, asignacion())


@pytest.mark.parametrize(
    ("tabla", "trigger"),
    [
        ("municipio_geografico", "tr_geografia_inmutable"),
        ("resultado_geografia_cuenta", "tr_geografia_inmutable"),
        ("version_bases_operativas", "tr_bases_operativas_inmutables"),
        ("base_operativa", "tr_bases_operativas_inmutables"),
        ("cobertura_base_municipio", "tr_bases_operativas_inmutables"),
        ("asignacion_territorial_v2", "tr_territorial_v2_inmutable"),
    ],
)
def test_lo_que_publicaron_la_geografia_las_bases_y_territorial_v2_no_se_reescribe(
    base_en_0012, tabla, trigger
):
    b = base_en_0012
    motor = b.motor
    _, municipio, geografia = _geografia_publicada(b)
    bases, base = _bases_publicadas(motor)
    (ejecucion,) = _insertar(
        motor, INSERTAR_TERRITORIAL_V2, _territorial_0012(b, geografia, bases, "EXITOSA")
    )
    _insertar(
        motor, INSERTAR_ASIGNACION, _asignacion(b, ejecucion, geografia, bases, municipio, base)
    )
    (columna,) = _consultar(
        motor,
        "SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() "
        f"AND table_name = '{tabla}' AND column_name NOT IN ('id') ORDER BY ordinal_position "
        "LIMIT 1",
    )

    # Aunque no toque ninguna fila: es la sentencia la que no se permite.
    for sentencia in (
        f"UPDATE {tabla} SET {columna} = {columna}",
        f"DELETE FROM {tabla}",
        f"DELETE FROM {tabla} WHERE false",
    ):
        with pytest.raises(DBAPIError) as error, motor.begin() as conexion:
            conexion.execute(text(sentencia))
        assert error.value.orig.sqlstate == "23001", sentencia  # restrict_violation
        assert error.value.orig.diag.constraint_name == trigger
    assert _consultar(motor, f"SELECT count(*) FROM {tabla}") == [1]


def test_los_trabajos_geografia_y_territorial_v2_apuntan_a_su_ejecucion_y_a_nada_mas(
    base_en_0012,
):
    b = base_en_0012
    motor = b.motor
    _, _, geografia = _geografia_publicada(b)
    bases, _ = _bases_publicadas(motor)
    (territorial,) = _insertar(
        motor, INSERTAR_TERRITORIAL_V2, _territorial_0012(b, geografia, bases)
    )
    _insertar(
        motor,
        INSERTAR_TRABAJO_0012,
        _trabajo_0012(geografia=geografia),
        _trabajo_0012("TERRITORIAL_V2", territorial=territorial),
    )

    for restriccion, trabajo in (
        ("uq_trabajo_geografia", _trabajo_0012(geografia=geografia)),
        ("uq_trabajo_territorial_v2", _trabajo_0012("TERRITORIAL_V2", territorial=territorial)),
        ("ck_trabajo_objetivo", _trabajo_0012()),
        ("ck_trabajo_objetivo", _trabajo_0012(geografia=geografia, territorial=territorial)),
        ("ck_trabajo_objetivo", _trabajo_0012("TERRITORIAL_V2", geografia=geografia)),
        ("fk_trabajo_geografia", _trabajo_0012(geografia=999_999)),
        ("fk_trabajo_territorial_v2", _trabajo_0012("TERRITORIAL_V2", territorial=999_999)),
        ("ck_trabajo_orquestacion_tipo_trabajo", _trabajo_0012("GEOGRAFIA_V2")),
    ):
        _rechaza(motor, restriccion, INSERTAR_TRABAJO_0012, trabajo)


def test_la_0012_baja_cerrando_lo_que_estaba_en_curso_conserva_postgis_y_vuelve_a_subir(
    base_en_0012,
):
    b = base_en_0012
    motor = b.motor
    # Una geografia publicada y otra en curso; bases; una asignacion publicada y otra en curso; y
    # los trabajos de las dos en curso.
    version, municipio, publicada = _geografia_publicada(b)
    (en_curso,) = _insertar(motor, INSERTAR_GEOGRAFIA, _geografia_0012(b, version))
    bases, base = _bases_publicadas(motor)
    (asignada,) = _insertar(
        motor, INSERTAR_TERRITORIAL_V2, _territorial_0012(b, publicada, bases, "EXITOSA")
    )
    _insertar(
        motor, INSERTAR_ASIGNACION, _asignacion(b, asignada, publicada, bases, municipio, base)
    )
    (asignando,) = _insertar(motor, INSERTAR_TERRITORIAL_V2, _territorial_0012(b, publicada, bases))
    _insertar(
        motor,
        INSERTAR_TRABAJO_0012,
        _trabajo_0012(estado="EJECUTANDO", geografia=en_curso),
        _trabajo_0012("TERRITORIAL_V2", territorial=asignando),
    )
    nuevas = ("ejecucion_geografia_id", "ejecucion_territorial_v2_id")
    previas = _por_columna(motor, PREVIAS_A_LA_0008)
    trabajos_antes = [
        {k: v for k, v in f.items() if k not in nuevas}
        for f in previas["trabajo_orquestacion"]
        if f["tipo"] not in ("GEOGRAFIA", "TERRITORIAL_V2")
    ]
    assert len(previas["trabajo_orquestacion"]) == len(trabajos_antes) + 2

    command.downgrade(_alembic(), "0011")

    # La geografia, las bases y territorial/v2 enteros se fueron, con sus trabajos. PostGIS se
    # queda: quitar una extension no es de esta migracion. Lo de v0.10, intacto; los otros
    # trabajos, sin sus dos columnas nuevas.
    assert not TABLAS_DE_LA_0012 & set(_consultar(motor, TABLAS))
    assert not FUNCIONES_DE_LA_0012 & set(_consultar(motor, FUNCIONES))
    assert _consultar(motor, POSTGIS) == ["postgis"]
    assert "spatial_ref_sys" in set(_consultar(motor, TABLAS))
    assert _de_la_v010(motor) == b.de_la_v010
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
    assert not any("GEOGRAFIA" in d for d in definiciones)
    assert all("DECISION_V2" in d for d in definiciones)
    assert _consultar(motor, "SELECT version_num FROM alembic_version") == ["0011"]

    # Y vuelve a subir limpia, sobre el PostGIS que se quedo: todo vacio, con sus restricciones.
    command.upgrade(_alembic(), "0012")

    for tabla in sorted(TABLAS_DE_LA_0012):
        assert _consultar(motor, f"SELECT count(*) FROM {tabla}") == [0]
    assert set(_consultar(motor, RESTRICCIONES_0012)) == RESTRICCIONES_DE_LA_0012
    assert FUNCIONES_DE_LA_0012 <= set(_consultar(motor, FUNCIONES))
    assert _de_la_v010(motor) == b.de_la_v010
    assert _consultar(motor, "SELECT version_num FROM alembic_version") == ["0012"]


def test_al_bajar_la_0012_lo_primero_es_cerrar_lo_que_estaba_en_curso(base_en_0012):
    # Lo que la 0012 hace antes de quitar sus tablas, con sus mismas sentencias: las ejecuciones en
    # curso quedan FALLIDA y sus trabajos FALLIDO, sin dueno ni lease, con el motivo. Lo terminado
    # no se toca.
    b = base_en_0012
    motor = b.motor
    modulo = _modulo_0012()
    version, _, publicada = _geografia_publicada(b)
    (en_curso,) = _insertar(motor, INSERTAR_GEOGRAFIA, _geografia_0012(b, version))
    bases, _ = _bases_publicadas(motor)
    (asignando,) = _insertar(motor, INSERTAR_TERRITORIAL_V2, _territorial_0012(b, publicada, bases))
    _insertar(
        motor,
        INSERTAR_TRABAJO_0012,
        _trabajo_0012(estado="EJECUTANDO", geografia=en_curso),
        _trabajo_0012("TERRITORIAL_V2", territorial=asignando),
    )

    with motor.begin() as conexion:
        conexion.execute(modulo.CERRAR_GEOGRAFIAS)
        conexion.execute(modulo.CERRAR_TERRITORIALES)
        conexion.execute(modulo.CERRAR_TRABAJOS)

    with motor.connect() as conexion:
        geografias = conexion.execute(
            text("SELECT id, estado, resultado, detalle FROM ejecucion_geografia ORDER BY id")
        ).all()
        territoriales = conexion.execute(
            text("SELECT id, estado, resultado, detalle FROM ejecucion_territorial_v2")
        ).all()
        trabajos = conexion.execute(
            text(
                "SELECT tipo, estado, worker_id, lease_hasta, ultimo_error FROM "
                "trabajo_orquestacion WHERE tipo IN ('GEOGRAFIA', 'TERRITORIAL_V2') ORDER BY id"
            )
        ).all()
    assert [tuple(g) for g in geografias] == [
        (publicada, "EXITOSA", "GEOGRAFIA_PUBLICADA", None),
        (en_curso, "FALLIDA", "CERRADA_AL_BAJAR", modulo.CIERRE),
    ]
    assert [tuple(t) for t in territoriales] == [
        (asignando, "FALLIDA", "CERRADA_AL_BAJAR", modulo.CIERRE)
    ]
    assert [tuple(t) for t in trabajos] == [
        ("GEOGRAFIA", "FALLIDO", None, None, modulo.CIERRE),
        ("TERRITORIAL_V2", "FALLIDO", None, None, modulo.CIERRE),
    ]


# --- la 0013: campo/v2 ----------------------------------------------------------------------------

TABLAS_DE_LA_0013 = {
    "version_configuracion_campo",
    "calendario_campo",
    "excepcion_calendario_campo",
    "ventana_jornada_campo",
    "gestor_campo",
    "ejecucion_campo_v2",
    "asignacion_campo_cuenta",
    "unidad_territorial_campo",
    "carga_gestor_campo",
}
_DE_LA_0013 = ", ".join(f"'{tabla}'" for tabla in sorted(TABLAS_DE_LA_0013))
RESTRICCIONES_0013 = RESTRICCIONES.replace("'ejecucion_decision', 'decision_cuenta'", _DE_LA_0013)
INDICES_0013 = INDICES.replace("'ejecucion_decision', 'decision_cuenta'", _DE_LA_0013)
RESTRICCIONES_DE_LA_0013 = {
    "pk_version_configuracion_campo",
    "uq_version_configuracion_campo_configuracion_campo_version_id",
    "uq_version_configuracion_campo_hash",
    "uq_configuracion_campo_id_bases",
    "fk_configuracion_campo_bases",
    "ck_configuracion_campo_hash",
    "ck_configuracion_campo_conteos",
    "ck_version_configuracion_campo_estado_version_campo",
    "pk_calendario_campo",
    "uq_calendario_campo_calendario_id",
    "uq_calendario_campo_codigo",
    "uq_calendario_campo_id_configuracion",
    "fk_calendario_campo_configuracion",
    "ck_calendario_codigo",
    "pk_excepcion_calendario_campo",
    "uq_excepcion_campo_fecha",
    "uq_excepcion_campo_id_calendario",
    "fk_excepcion_campo_calendario",
    "ck_excepcion_calendario_campo_tipo_excepcion_campo",
    "pk_ventana_jornada_campo",
    "fk_ventana_campo_calendario",
    "fk_ventana_campo_excepcion",
    "ck_ventana_campo_horario",
    "pk_gestor_campo",
    "uq_gestor_campo_gestor_id",
    "uq_gestor_campo_codigo",
    "uq_gestor_campo_id_configuracion_base",
    "fk_gestor_campo_configuracion",
    "fk_gestor_campo_base",
    "fk_gestor_campo_calendario",
    "ck_gestor_codigo",
    "ck_gestor_capacidad",
    "pk_ejecucion_campo_v2",
    "uq_ejecucion_campo_v2_campo_run_id",
    "uq_campo_v2_entradas",
    "fk_campo_v2_decision",
    "fk_campo_v2_territorial",
    "fk_campo_v2_configuracion",
    "ck_campo_v2_firmas",
    "ck_campo_v2_resultado",
    "ck_campo_v2_conteos",
    "ck_campo_v2_publicacion",
    "ck_ejecucion_campo_v2_estado_campo_v2",
    "pk_asignacion_campo_cuenta",
    "fk_asignacion_campo_ejecucion",
    "fk_asignacion_campo_decision",
    "fk_asignacion_campo_territorial",
    "fk_asignacion_campo_gestor",
    "fk_asignacion_campo_base",
    "ck_asignacion_campo_estado",
    "pk_unidad_territorial_campo",
    "fk_unidad_campo_ejecucion",
    "fk_unidad_campo_padre",
    "fk_unidad_campo_base",
    "fk_unidad_campo_propietario",
    "ck_unidad_campo_nivel",
    "pk_carga_gestor_campo",
    "fk_carga_campo_ejecucion",
    "fk_carga_campo_gestor",
    "fk_carga_campo_base",
    "ck_carga_campo_conteos",
}
RESTRICCIONES_DE_LA_0013_EN_LA_COLA = {"fk_trabajo_campo_v2", "uq_trabajo_campo_v2"}
INDICES_DE_LA_0013 = {
    ("version_configuracion_campo", "pk_version_configuracion_campo", True, ("id",), None),
    (
        "version_configuracion_campo",
        "uq_version_configuracion_campo_configuracion_campo_version_id",
        True,
        ("configuracion_campo_version_id",),
        None,
    ),
    (
        "version_configuracion_campo",
        "uq_version_configuracion_campo_hash",
        True,
        ("hash_configuracion",),
        None,
    ),
    (
        "version_configuracion_campo",
        "uq_configuracion_campo_id_bases",
        True,
        ("id", "version_bases_operativas_id"),
        None,
    ),
    ("calendario_campo", "pk_calendario_campo", True, ("id",), None),
    ("calendario_campo", "uq_calendario_campo_calendario_id", True, ("calendario_id",), None),
    (
        "calendario_campo",
        "uq_calendario_campo_codigo",
        True,
        ("version_configuracion_campo_id", "codigo"),
        None,
    ),
    (
        "calendario_campo",
        "uq_calendario_campo_id_configuracion",
        True,
        ("id", "version_configuracion_campo_id"),
        None,
    ),
    ("excepcion_calendario_campo", "pk_excepcion_calendario_campo", True, ("id",), None),
    (
        "excepcion_calendario_campo",
        "uq_excepcion_campo_fecha",
        True,
        ("calendario_campo_id", "fecha"),
        None,
    ),
    (
        "excepcion_calendario_campo",
        "uq_excepcion_campo_id_calendario",
        True,
        ("id", "calendario_campo_id"),
        None,
    ),
    ("ventana_jornada_campo", "pk_ventana_jornada_campo", True, ("id",), None),
    (
        "ventana_jornada_campo",
        "ix_ventana_campo_calendario",
        False,
        ("calendario_campo_id", "dia_semana"),
        None,
    ),
    ("gestor_campo", "pk_gestor_campo", True, ("id",), None),
    ("gestor_campo", "uq_gestor_campo_gestor_id", True, ("gestor_id",), None),
    (
        "gestor_campo",
        "uq_gestor_campo_codigo",
        True,
        ("version_configuracion_campo_id", "codigo"),
        None,
    ),
    (
        "gestor_campo",
        "uq_gestor_campo_id_configuracion_base",
        True,
        ("id", "version_configuracion_campo_id", "base_operativa_id"),
        None,
    ),
    (
        "gestor_campo",
        "ix_gestor_campo_base",
        False,
        ("version_configuracion_campo_id", "base_operativa_id", "activo"),
        None,
    ),
    ("ejecucion_campo_v2", "pk_ejecucion_campo_v2", True, ("id",), None),
    ("ejecucion_campo_v2", "uq_ejecucion_campo_v2_campo_run_id", True, ("campo_run_id",), None),
    (
        "ejecucion_campo_v2",
        "uq_campo_v2_entradas",
        True,
        (
            "id",
            "ejecucion_decision_v2_id",
            "ejecucion_territorial_v2_id",
            "version_configuracion_campo_id",
            "version_bases_operativas_id",
        ),
        None,
    ),
    (
        "ejecucion_campo_v2",
        "ux_campo_v2_exitosa",
        True,
        ("firma_inputs",),
        _POR_ESTADO.format("EXITOSA"),
    ),
    (
        "ejecucion_campo_v2",
        "ux_campo_v2_en_proceso",
        True,
        ("firma_inputs",),
        _POR_ESTADO.format("EN_PROCESO"),
    ),
    ("ejecucion_campo_v2", "ix_campo_v2_corte", False, ("corte_canonico_id", "id"), None),
    (
        "asignacion_campo_cuenta",
        "pk_asignacion_campo_cuenta",
        True,
        ("ejecucion_campo_v2_id", "cuenta_canonica_id"),
        None,
    ),
    *(
        (
            "asignacion_campo_cuenta",
            f"ix_asignacion_campo_{nombre}",
            False,
            ("ejecucion_campo_v2_id", columna, "cuenta_canonica_id"),
            None,
        )
        for nombre, columna in (
            ("estado", "estado"),
            ("base", "base_operativa_id"),
            ("gestor", "gestor_campo_id"),
            ("prioridad", "prioridad"),
            ("microtramo", "microtramo_id"),
        )
    ),
    (
        "asignacion_campo_cuenta",
        "ix_asignacion_campo_cuenta",
        False,
        ("cuenta_canonica_id", "ejecucion_campo_v2_id"),
        None,
    ),
    (
        "unidad_territorial_campo",
        "pk_unidad_territorial_campo",
        True,
        ("ejecucion_campo_v2_id", "unidad_id"),
        None,
    ),
    (
        "unidad_territorial_campo",
        "ix_unidad_campo_padre",
        False,
        ("ejecucion_campo_v2_id", "padre_id"),
        None,
    ),
    (
        "carga_gestor_campo",
        "pk_carga_gestor_campo",
        True,
        ("ejecucion_campo_v2_id", "gestor_campo_id"),
        None,
    ),
}
"""Las llaves, la unicidad y las paginas que la API lee de un plan: ni un indice mas."""
INMUTABLES_DE_LA_0013 = {
    "version_configuracion_campo": "tr_configuracion_campo_inmutable",
    "calendario_campo": "tr_configuracion_campo_inmutable",
    "excepcion_calendario_campo": "tr_configuracion_campo_inmutable",
    "ventana_jornada_campo": "tr_configuracion_campo_inmutable",
    "gestor_campo": "tr_configuracion_campo_inmutable",
    "asignacion_campo_cuenta": "tr_campo_v2_inmutable",
    "unidad_territorial_campo": "tr_campo_v2_inmutable",
    "carga_gestor_campo": "tr_campo_v2_inmutable",
}
TRIGGERS_DE_LA_0013 = {
    *INMUTABLES_DE_LA_0013.items(),
    ("ejecucion_campo_v2", "tr_campo_v2_terminado"),
    ("ventana_jornada_campo", "tr_ventana_campo_sin_encimarse"),
}
FUNCIONES_DE_LA_0013 = {
    "configuracion_campo_inmutable",
    "campo_v2_inmutable",
    "campo_v2_terminado",
    "ventana_campo_sin_encimarse",
}
SIN_COORDENADAS = {
    "lat",
    "lon",
    "latitud",
    "longitud",
    "punto",
    "geometria",
    "x",
    "y",
    "grid_x",
    "grid_y",
    "bbox",
    "centroide",
}
"""Ninguna tabla de campo/v2 guarda una coordenada: la particion se recalcula, nunca se publica."""

INSERTAR_CONFIGURACION_CAMPO = text(
    "INSERT INTO version_configuracion_campo (configuracion_campo_version_id, "
    "version_bases_operativas_id, nombre, version_configuracion, hash_configuracion, zona_horaria, "
    "estado, calendarios, ventanas, excepciones, gestores, gestores_activos, capacidad_declarada, "
    "metadatos, creada_en, publicada_en) VALUES (:id, :bases, 'Campo de la migracion', "
    "'campo-configuracion/v1', :hash, 'America/Mexico_City', 'PUBLICADA', :calendarios, 1, 0, "
    ":gestores, :activos, 20, CAST('{}' AS JSONB), now(), now()) RETURNING id"
)
INSERTAR_CALENDARIO_CAMPO = text(
    "INSERT INTO calendario_campo (calendario_id, version_configuracion_campo_id, codigo) VALUES "
    "(:id, :configuracion, :codigo) RETURNING id"
)
INSERTAR_EXCEPCION_CAMPO = text(
    "INSERT INTO excepcion_calendario_campo (calendario_campo_id, fecha, tipo, motivo) VALUES "
    "(:calendario, :fecha, :tipo, NULL) RETURNING id"
)
INSERTAR_VENTANA_CAMPO = text(
    "INSERT INTO ventana_jornada_campo (calendario_campo_id, excepcion_calendario_campo_id, "
    "dia_semana, minuto_inicio, minuto_fin) VALUES (:calendario, :excepcion, :dia, :inicio, :fin) "
    "RETURNING id"
)
INSERTAR_GESTOR_CAMPO = text(
    "INSERT INTO gestor_campo (gestor_id, version_configuracion_campo_id, "
    "version_bases_operativas_id, codigo, base_operativa_id, calendario_campo_id, "
    "capacidad_cuentas_dia, activo) VALUES (:id, :configuracion, :bases, :codigo, :base, "
    ":calendario, :capacidad, true) RETURNING id"
)
INSERTAR_PLAN_CAMPO = text(
    "INSERT INTO ejecucion_campo_v2 (campo_run_id, corte_canonico_id, ejecucion_decision_v2_id, "
    "ejecucion_territorial_v2_id, version_bases_operativas_id, version_configuracion_campo_id, "
    "fecha_operacion, version_motor, version_politica, hash_politica, firma_inputs, estado, "
    "resultado, cuentas_evaluadas_decision, candidatas_campo, asignadas, revision_territorial, "
    "sin_gestor, sin_capacidad, rebalanceadas, capacidad_total, capacidad_utilizada, iniciada_en, "
    "terminada_en) VALUES (:run, :corte, :decision, :territorial, :bases, :configuracion, "
    "'2026-10-01', 'campo/v2', 'campo-public-v2', :hash_politica, :firma, :estado, :resultado, 1, "
    ":candidatas, :asignadas, 0, 0, 0, :rebalanceadas, :capacidad, :utilizada, now(), :terminada) "
    "RETURNING id"
)
INSERTAR_ASIGNACION_CAMPO = text(
    "INSERT INTO asignacion_campo_cuenta (zona_id, subzona_id, tramo_id, microtramo_id, "
    "ejecucion_campo_v2_id, cuenta_canonica_id, ejecucion_decision_v2_id, "
    "ejecucion_territorial_v2_id, version_configuracion_campo_id, version_bases_operativas_id, "
    "base_operativa_id, gestor_campo_id, orden_prioridad, estado, prioridad, es_rebalanceada, "
    "motivos) VALUES (:zona, :subzona, :tramo, :microtramo, :plan, :cuenta, :decision, "
    ":territorial, :configuracion, :bases, :base, :gestor, :orden, :estado, :prioridad, "
    ":rebalanceada, CAST('[]' AS JSONB)) RETURNING cuenta_canonica_id"
)
INSERTAR_UNIDAD_CAMPO = text(
    "INSERT INTO unidad_territorial_campo (ejecucion_campo_v2_id, unidad_id, nivel, padre_id, "
    "base_operativa_id, cve_ent, cvegeo, candidatas, asignadas, propietario_gestor_campo_id) "
    "VALUES (:plan, :unidad, :nivel, :padre, :base, :cve_ent, :cvegeo, :candidatas, :asignadas, "
    ":propietario) RETURNING ejecucion_campo_v2_id"
)
INSERTAR_CARGA_CAMPO = text(
    "INSERT INTO carga_gestor_campo (utilizacion, ejecucion_campo_v2_id, gestor_campo_id, "
    "base_operativa_id, estado_jornada, capacidad, asignadas, rebalanceadas, microtramos, tramos, "
    "ventanas) VALUES (:utilizacion, :plan, :gestor, :base, :jornada, :capacidad, :asignadas, 0, "
    ":microtramos, :tramos, CAST('[]' AS JSONB)) RETURNING ejecucion_campo_v2_id"
)
INSERTAR_TRABAJO_0013 = text(
    "INSERT INTO trabajo_orquestacion (trabajo_id, tipo, estado, ejecucion_campo_v2_id, "
    "ejecucion_territorial_v2_id, intentos, max_intentos, creado_en, disponible_desde, worker_id, "
    "lease_hasta, tomado_en) VALUES (:trabajo_id, :tipo, :estado, :campo, :territorial, "
    ":intentos, 5, now(), now(), :worker_id, :lease_hasta, now()) RETURNING id"
)


def _modulo_0013():
    return ScriptDirectory.from_config(_alembic()).get_revision("0013").module


def _de_la_v011(motor: Engine) -> dict[str, list[dict]]:
    """Lo que la 0013 no puede tocar: todo lo de v0.10, la geografia, las bases y territorial/v2."""
    filas = _de_la_v010(motor)
    llaves = {
        "version_geografia": "id",
        "municipio_geografico": "id",
        "ejecucion_geografia": "id",
        "resultado_geografia_cuenta": "ejecucion_geografia_id, cuenta_canonica_id",
        "version_bases_operativas": "id",
        "base_operativa": "id",
        "cobertura_base_municipio": "base_operativa_id, cvegeo",
        "ejecucion_territorial_v2": "id",
        "asignacion_territorial_v2": "ejecucion_territorial_v2_id, cuenta_canonica_id",
    }
    with motor.connect() as conexion:
        for tabla, orden in llaves.items():
            filas[tabla] = [
                dict(fila)
                for fila in conexion.execute(text(f"SELECT * FROM {tabla} ORDER BY {orden}"))
                .mappings()
                .all()
            ]
    return filas


@dataclass(frozen=True)
class BaseEn0013:
    """Una base de v0.11 con una geografia, unas bases y una territorial/v2 publicadas sobre el
    corte de su decision/v2, subida a la 0013: campo/v2 vacio."""

    motor: Engine
    corte: int
    cuenta: int
    decision: int
    geografia: int
    territorial: int
    bases: int
    base: int
    previas: dict[str, list[dict]]
    de_la_v011: dict[str, list[dict]]


@pytest.fixture
def base_en_0013(base_en_0012) -> BaseEn0013:
    b = base_en_0012
    motor = b.motor
    (decision,) = _consultar(motor, "SELECT id FROM ejecucion_decision_v2")
    _, municipio, geografia = _geografia_publicada(b)
    bases, base_a = _bases_publicadas(motor)
    (territorial,) = _insertar(
        motor, INSERTAR_TERRITORIAL_V2, _territorial_0012(b, geografia, bases, "EXITOSA")
    )
    _insertar(
        motor, INSERTAR_ASIGNACION, _asignacion(b, territorial, geografia, bases, municipio, base_a)
    )
    previas = _por_columna(motor, PREVIAS_A_LA_0008)
    de_la_v011 = _de_la_v011(motor)

    command.upgrade(_alembic(), "0013")
    return BaseEn0013(
        motor,
        b.corte,
        b.cuenta,
        decision,
        geografia,
        territorial,
        bases,
        base_a,
        previas,
        de_la_v011,
    )


def _configuracion_campo(b: BaseEn0013, **cambios) -> dict:
    return {
        "id": uuid4(),
        "bases": b.bases,
        "hash": uuid4().hex * 2,
        "calendarios": 1,
        "gestores": 1,
        "activos": 1,
        **cambios,
    }


def _gestor_campo(b: BaseEn0013, configuracion: int, calendario: int, **cambios) -> dict:
    return {
        "id": uuid4(),
        "configuracion": configuracion,
        "bases": b.bases,
        "codigo": "GESTOR_001",
        "base": b.base,
        "calendario": calendario,
        "capacidad": 20,
        **cambios,
    }


def _configuracion_con_gestor(b: BaseEn0013) -> tuple[int, int, int]:
    """Una configuracion de las bases de `b` con un calendario de una ventana y un gestor de su
    base."""
    motor = b.motor
    (configuracion,) = _insertar(motor, INSERTAR_CONFIGURACION_CAMPO, _configuracion_campo(b))
    (calendario,) = _insertar(
        motor,
        INSERTAR_CALENDARIO_CAMPO,
        {"id": uuid4(), "configuracion": configuracion, "codigo": "LV"},
    )
    _insertar(
        motor,
        INSERTAR_VENTANA_CAMPO,
        {"calendario": calendario, "excepcion": None, "dia": 4, "inicio": 540, "fin": 840},
    )
    (gestor,) = _insertar(motor, INSERTAR_GESTOR_CAMPO, _gestor_campo(b, configuracion, calendario))
    return configuracion, calendario, gestor


def _plan_0013(b: BaseEn0013, configuracion: int, estado: str = "EN_PROCESO", **cambios) -> dict:
    terminada = estado != "EN_PROCESO"
    exitosa = estado == "EXITOSA"
    return {
        "run": uuid4(),
        "corte": b.corte,
        "decision": b.decision,
        "territorial": b.territorial,
        "bases": b.bases,
        "configuracion": configuracion,
        "hash_politica": "a" * 64,
        "firma": uuid4().hex * 2,
        "estado": estado,
        "resultado": "PLAN_PUBLICADO" if exitosa else "ERROR_INTERNO" if terminada else None,
        "candidatas": 1 if exitosa else 0,
        "asignadas": 1 if exitosa else 0,
        "rebalanceadas": 0,
        "capacidad": 20 if exitosa else 0,
        "utilizada": 1 if exitosa else 0,
        "terminada": datetime.now(UTC) if terminada else None,
        **cambios,
    }


def _asignacion_campo(b: BaseEn0013, plan: int, configuracion: int, gestor: int, **cambios):
    return {
        "zona": uuid4(),
        "subzona": uuid4(),
        "tramo": uuid4(),
        "microtramo": uuid4(),
        "plan": plan,
        "cuenta": b.cuenta,
        "decision": b.decision,
        "territorial": b.territorial,
        "configuracion": configuracion,
        "bases": b.bases,
        "base": b.base,
        "gestor": gestor,
        "orden": 1,
        "estado": "ASIGNADA",
        "prioridad": "MUY_ALTA",
        "rebalanceada": False,
        **cambios,
    }


def _unidad_campo(b: BaseEn0013, plan: int, **cambios) -> dict:
    return {
        "plan": plan,
        "unidad": uuid4(),
        "nivel": "ZONA",
        "padre": None,
        "base": b.base,
        "cve_ent": "21",
        "cvegeo": None,
        "candidatas": 2,
        "asignadas": 1,
        "propietario": None,
        **cambios,
    }


def _carga_campo(b: BaseEn0013, plan: int, gestor: int, **cambios) -> dict:
    return {
        "utilizacion": Decimal("0.5000"),
        "plan": plan,
        "gestor": gestor,
        "base": b.base,
        "jornada": "JORNADA_SEMANAL",
        "capacidad": 20,
        "asignadas": 10,
        "microtramos": 3,
        "tramos": 1,
        **cambios,
    }


def _trabajo_0013(tipo: str = "CAMPO_V2", estado: str = "PENDIENTE", **cambios) -> dict:
    ejecutando = estado == "EJECUTANDO"
    return {
        "trabajo_id": uuid4(),
        "tipo": tipo,
        "estado": estado,
        "campo": None,
        "territorial": None,
        "intentos": 1 if ejecutando else 0,
        "worker_id": "worker-de-prueba:1:abc" if ejecutando else None,
        "lease_hasta": datetime.now(UTC) + timedelta(minutes=1) if ejecutando else None,
        **cambios,
    }


def _restricciones_de_territorial_v2(motor: Engine) -> set[str]:
    return set(
        _consultar(
            motor,
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = CAST('ejecucion_territorial_v2' AS regclass)",
        )
    )


def test_los_modelos_declaran_el_esquema_de_la_0013():
    from motor_cartera.db import modelos

    tablas = [modelos.SQLModel.metadata.tables[t] for t in sorted(TABLAS_DE_LA_0013)]
    preparador = postgresql.dialect().identifier_preparer
    nombres = {preparador.format_constraint(c) for t in tablas for c in t.constraints}
    # La 0014 le agrega a carga_gestor_campo una restriccion unica sobre columnas que ya tenia: la
    # que referencia cada ruta de ruteo/v2.
    assert nombres == RESTRICCIONES_DE_LA_0013 | {"uq_carga_campo_base"}
    assert all(len(nombre) <= 63 for nombre in nombres)
    # Lo que crece con cada plan no tiene id propio: su llave es la natural.
    for tabla, llave in (
        ("asignacion_campo_cuenta", ["ejecucion_campo_v2_id", "cuenta_canonica_id"]),
        ("unidad_territorial_campo", ["ejecucion_campo_v2_id", "unidad_id"]),
        ("carga_gestor_campo", ["ejecucion_campo_v2_id", "gestor_campo_id"]),
    ):
        assert [c.name for c in modelos.SQLModel.metadata.tables[tabla].primary_key] == llave
        assert "id" not in modelos.SQLModel.metadata.tables[tabla].c
    # Ni una geometria ni una coordenada.
    for tabla in tablas:
        for columna in tabla.columns:
            assert not hasattr(columna.type, "get_col_spec"), (tabla.name, columna.name)
            assert columna.name not in SIN_COORDENADAS, (tabla.name, columna.name)
    trabajo = TrabajoOrquestacion.__table__
    assert "CAMPO_V2" in trabajo.c.tipo.type.enums
    assert trabajo.c.ejecucion_campo_v2_id.nullable is True
    llaves = {preparador.format_constraint(c) for c in trabajo.constraints}
    assert RESTRICCIONES_DE_LA_0013_EN_LA_COLA <= llaves


def test_la_0013_agrega_campo_v2_vacio_sin_tocar_nada_de_la_v011(base_en_0013):
    b = base_en_0013
    motor = b.motor

    # A. Cada fila de antes sigue ahi, identica; un trabajo solo gana su objetivo nuevo, vacio.
    assert _de_la_v011(motor) == b.de_la_v011
    despues = _por_columna(motor, PREVIAS_A_LA_0008)
    for tabla, filas in b.previas.items():
        assert len(despues[tabla]) == len(filas), tabla
        for antes, fila in zip(filas, despues[tabla], strict=True):
            fila = dict(fila)
            if tabla == "trabajo_orquestacion":
                assert fila.pop("ejecucion_campo_v2_id") is None
            assert fila == antes, tabla

    # B. Las tablas nuevas vacias, con sus restricciones, sus indices y sus triggers, por nombre; y
    # la restriccion unica que gana ejecucion_territorial_v2.
    assert TABLAS_DE_LA_0013 <= set(_consultar(motor, TABLAS))
    for tabla in sorted(TABLAS_DE_LA_0013):
        assert _consultar(motor, f"SELECT count(*) FROM {tabla}") == [0]
    assert set(_consultar(motor, RESTRICCIONES_0013)) == RESTRICCIONES_DE_LA_0013
    with motor.connect() as conexion:
        indices = {
            (tabla, nombre, unico, tuple(columnas), predicado)
            for tabla, nombre, unico, columnas, predicado in conexion.execute(text(INDICES_0013))
        }
        triggers = {tuple(fila) for fila in conexion.execute(text(TRIGGERS))}
    assert indices == INDICES_DE_LA_0013
    assert TRIGGERS_DE_LA_0013 <= triggers
    assert FUNCIONES_DE_LA_0013 <= set(_consultar(motor, FUNCIONES))
    assert "uq_territorial_v2_corte_bases" in _restricciones_de_territorial_v2(motor)
    assert _consultar(motor, "SELECT version_num FROM alembic_version") == ["0013"]


def test_una_configuracion_sus_calendarios_y_sus_ventanas_tienen_su_forma(base_en_0013):
    b = base_en_0013
    motor = b.motor
    huella = "e" * 64
    (configuracion,) = _insertar(
        motor, INSERTAR_CONFIGURACION_CAMPO, _configuracion_campo(b, hash=huella)
    )
    (calendario,) = _insertar(
        motor,
        INSERTAR_CALENDARIO_CAMPO,
        {"id": uuid4(), "configuracion": configuracion, "codigo": "LV"},
    )
    (excepcion,) = _insertar(
        motor,
        INSERTAR_EXCEPCION_CAMPO,
        {"calendario": calendario, "fecha": "2026-11-16", "tipo": "VENTANAS_ESPECIALES"},
    )

    def ventana(**cambios) -> dict:
        return {
            "calendario": calendario,
            "excepcion": None,
            "dia": 1,
            "inicio": 540,
            "fin": 840,
            **cambios,
        }

    def calendario_de(**cambios) -> dict:
        return {"id": uuid4(), "configuracion": configuracion, "codigo": "LS", **cambios}

    def excepcion_de(**cambios) -> dict:
        return {"calendario": calendario, "fecha": "2026-11-17", "tipo": "CERRADO", **cambios}

    for restriccion, sentencia, fila in (
        (
            "uq_version_configuracion_campo_hash",
            INSERTAR_CONFIGURACION_CAMPO,
            _configuracion_campo(b, hash=huella),
        ),
        (
            "ck_configuracion_campo_hash",
            INSERTAR_CONFIGURACION_CAMPO,
            _configuracion_campo(b, hash="NO-ES-HEX"),
        ),
        (
            "ck_configuracion_campo_conteos",
            INSERTAR_CONFIGURACION_CAMPO,
            _configuracion_campo(b, activos=2),
        ),
        (
            "ck_configuracion_campo_conteos",
            INSERTAR_CONFIGURACION_CAMPO,
            _configuracion_campo(b, gestores=0, activos=0),
        ),
        (
            "fk_configuracion_campo_bases",
            INSERTAR_CONFIGURACION_CAMPO,
            _configuracion_campo(b, bases=999_999),
        ),
        ("uq_calendario_campo_codigo", INSERTAR_CALENDARIO_CAMPO, calendario_de(codigo="LV")),
        ("ck_calendario_codigo", INSERTAR_CALENDARIO_CAMPO, calendario_de(codigo="lv")),
        (
            "fk_calendario_campo_configuracion",
            INSERTAR_CALENDARIO_CAMPO,
            calendario_de(configuracion=999_999),
        ),
        ("uq_excepcion_campo_fecha", INSERTAR_EXCEPCION_CAMPO, excepcion_de(fecha="2026-11-16")),
        (
            "ck_excepcion_calendario_campo_tipo_excepcion_campo",
            INSERTAR_EXCEPCION_CAMPO,
            excepcion_de(tipo="FESTIVO"),
        ),
        ("ck_ventana_campo_horario", INSERTAR_VENTANA_CAMPO, ventana(inicio=840, fin=540)),
        ("ck_ventana_campo_horario", INSERTAR_VENTANA_CAMPO, ventana(inicio=540, fin=540)),
        ("ck_ventana_campo_horario", INSERTAR_VENTANA_CAMPO, ventana(fin=1441)),
        ("ck_ventana_campo_horario", INSERTAR_VENTANA_CAMPO, ventana(inicio=-1)),
        ("ck_ventana_campo_horario", INSERTAR_VENTANA_CAMPO, ventana(dia=8)),
        ("ck_ventana_campo_horario", INSERTAR_VENTANA_CAMPO, ventana(dia=0)),
        # Una ventana es de un dia de la semana o de una excepcion: nunca de los dos ni de ninguno.
        ("ck_ventana_campo_horario", INSERTAR_VENTANA_CAMPO, ventana(dia=None)),
        ("ck_ventana_campo_horario", INSERTAR_VENTANA_CAMPO, ventana(excepcion=excepcion)),
        (
            "fk_ventana_campo_excepcion",
            INSERTAR_VENTANA_CAMPO,
            ventana(dia=None, excepcion=999_999),
        ),
    ):
        _rechaza(motor, restriccion, sentencia, fila)
    # Una ventana de las 00:00 a las 24:00 cabe; y la de una excepcion no se encima con las de la
    # semana.
    _insertar(
        motor,
        INSERTAR_VENTANA_CAMPO,
        ventana(),
        ventana(dia=None, excepcion=excepcion),
        ventana(dia=7, inicio=0, fin=1440),
    )

    # Dos ventanas del mismo dia, o de la misma excepcion, no se enciman; tocarse no es encimarse.
    for encimada in (
        ventana(inicio=600, fin=900),
        ventana(inicio=0, fin=541),
        ventana(dia=None, excepcion=excepcion, inicio=839, fin=1000),
    ):
        with pytest.raises(IntegrityError) as error, motor.begin() as conexion:
            conexion.execute(INSERTAR_VENTANA_CAMPO, encimada)
        assert error.value.orig.sqlstate == "23514"  # check_violation
        assert _restriccion_de(error.value) == "tr_ventana_campo_sin_encimarse"
    _insertar(
        motor,
        INSERTAR_VENTANA_CAMPO,
        ventana(inicio=840, fin=900),
        ventana(inicio=480, fin=540),
        ventana(dia=2, inicio=600, fin=900),
    )
    assert _consultar(motor, "SELECT count(*) FROM ventana_jornada_campo") == [6]


def test_un_gestor_es_de_una_base_de_sus_bases_y_de_un_calendario_de_su_configuracion(
    base_en_0013,
):
    b = base_en_0013
    motor = b.motor
    configuracion, calendario, _ = _configuracion_con_gestor(b)
    otras_bases, otra_base = _bases_publicadas(motor, "BASE_B")
    (otra_configuracion,) = _insertar(motor, INSERTAR_CONFIGURACION_CAMPO, _configuracion_campo(b))
    (otro_calendario,) = _insertar(
        motor,
        INSERTAR_CALENDARIO_CAMPO,
        {"id": uuid4(), "configuracion": otra_configuracion, "codigo": "LV"},
    )

    def gestor(**cambios) -> dict:
        return {**_gestor_campo(b, configuracion, calendario, codigo="GESTOR_002"), **cambios}

    for restriccion, fila in (
        ("uq_gestor_campo_codigo", gestor(codigo="GESTOR_001")),
        ("ck_gestor_codigo", gestor(codigo="gestor@correo")),
        ("ck_gestor_codigo", gestor(codigo="Gestor Uno")),
        ("ck_gestor_capacidad", gestor(capacidad=0)),
        ("ck_gestor_capacidad", gestor(capacidad=1001)),
        ("ck_gestor_capacidad", gestor(capacidad=-5)),
        # Una base de otra version de bases, o una configuracion que dice otras bases.
        ("fk_gestor_campo_base", gestor(base=otra_base)),
        ("fk_gestor_campo_configuracion", gestor(bases=otras_bases, base=otra_base)),
        # Un calendario de otra configuracion.
        ("fk_gestor_campo_calendario", gestor(calendario=otro_calendario)),
    ):
        _rechaza(motor, restriccion, INSERTAR_GESTOR_CAMPO, fila)
    _insertar(motor, INSERTAR_GESTOR_CAMPO, gestor(), gestor(codigo="GESTOR_003", capacidad=1000))


def test_un_plan_exitoso_por_firma_y_uno_en_proceso_por_firma(base_en_0013):
    b = base_en_0013
    motor = b.motor
    configuracion, _, _ = _configuracion_con_gestor(b)
    firma = "c" * 64
    _insertar(
        motor,
        INSERTAR_PLAN_CAMPO,
        _plan_0013(b, configuracion, "EXITOSA", firma=firma),
        _plan_0013(b, configuracion, firma=firma),
    )

    _rechaza(
        motor,
        "ux_campo_v2_exitosa",
        INSERTAR_PLAN_CAMPO,
        _plan_0013(b, configuracion, "EXITOSA", firma=firma),
    )
    _rechaza(
        motor,
        "ux_campo_v2_en_proceso",
        INSERTAR_PLAN_CAMPO,
        _plan_0013(b, configuracion, firma=firma),
    )
    # Los intentos fallidos de la misma firma se conservan todos.
    _insertar(
        motor,
        INSERTAR_PLAN_CAMPO,
        _plan_0013(b, configuracion, "FALLIDA", firma=firma),
        _plan_0013(b, configuracion, "FALLIDA", firma=firma),
    )


def test_un_plan_declara_entradas_de_un_mismo_corte_y_de_unas_mismas_bases(base_en_0013):
    b = base_en_0013
    motor = b.motor
    configuracion, _, _ = _configuracion_con_gestor(b)
    otro_corte = _otro_corte(b, b.cuenta)
    (de_otro_corte,) = _insertar(motor, INSERTAR_DECISION_V2, _decision_v2(otro_corte, "EXITOSA"))
    otras_bases, _ = _bases_publicadas(motor, "BASE_B")
    (de_otras_bases,) = _insertar(
        motor, INSERTAR_CONFIGURACION_CAMPO, _configuracion_campo(b, bases=otras_bases)
    )

    for restriccion, cambios in (
        # La decision/v2 y territorial/v2 son del corte del plan; la configuracion, de las bases de
        # territorial/v2.
        ("fk_campo_v2_decision", {"decision": de_otro_corte}),
        ("fk_campo_v2_territorial", {"corte": otro_corte, "decision": de_otro_corte}),
        ("fk_campo_v2_configuracion", {"configuracion": de_otras_bases}),
        ("fk_campo_v2_territorial", {"bases": otras_bases, "configuracion": de_otras_bases}),
        ("ck_campo_v2_firmas", {"firma": "NO-ES-HEX"}),
        ("ck_campo_v2_firmas", {"hash_politica": "a" * 63}),
        ("ck_campo_v2_resultado", {"resultado": "PLAN_PUBLICADO"}),
        ("ck_campo_v2_conteos", {"rebalanceadas": -1}),
        ("ck_campo_v2_publicacion", {"asignadas": 1}),
        ("ck_campo_v2_publicacion", {"capacidad": 20}),
        ("ck_ejecucion_campo_v2_estado_campo_v2", {"estado": "PLANEADO", "resultado": "X"}),
    ):
        _rechaza(
            motor, restriccion, INSERTAR_PLAN_CAMPO, {**_plan_0013(b, configuracion), **cambios}
        )
    # Una EXITOSA dice su resultado, cuenta cada candidata una vez, usa su capacidad en sus
    # asignadas y nada mas, y no rebalancea mas de lo que asigno.
    for restriccion, cambios in (
        ("ck_campo_v2_resultado", {"resultado": None}),
        ("ck_campo_v2_publicacion", {"candidatas": 2}),
        ("ck_campo_v2_publicacion", {"candidatas": 2, "asignadas": 2, "utilizada": 2}),
        ("ck_campo_v2_publicacion", {"capacidad": 0}),
        ("ck_campo_v2_publicacion", {"utilizada": 2}),
        ("ck_campo_v2_publicacion", {"rebalanceadas": 2}),
    ):
        _rechaza(
            motor,
            restriccion,
            INSERTAR_PLAN_CAMPO,
            _plan_0013(b, configuracion, "EXITOSA", **cambios),
        )


def test_una_asignacion_es_de_su_plan_de_sus_entradas_y_de_un_gestor_de_su_base(base_en_0013):
    b = base_en_0013
    motor = b.motor
    configuracion, calendario, gestor = _configuracion_con_gestor(b)
    (plan,) = _insertar(motor, INSERTAR_PLAN_CAMPO, _plan_0013(b, configuracion))
    _, otra_base = _bases_publicadas(motor, "BASE_B")
    # Un gestor de otra base de las mismas bases (BASE_C), en la misma configuracion.
    (base_c,) = _insertar(motor, INSERTAR_BASE, _base(b.bases, "BASE_C"))
    (gestor_c,) = _insertar(
        motor,
        INSERTAR_GESTOR_CAMPO,
        _gestor_campo(b, configuracion, calendario, codigo="GESTOR_C", base=base_c),
    )
    # Otros planes del mismo corte: uno con otra configuracion, uno con otra decision/v2 que no
    # decidio la cuenta, uno con otra territorial/v2 que no la asigno y uno con las mismas entradas.
    otra_configuracion, _, _ = _configuracion_con_gestor(b)
    (otra_decision,) = _insertar(motor, INSERTAR_DECISION_V2, _decision_v2(b.corte, "EXITOSA"))
    (otra_territorial,) = _insertar(
        motor, INSERTAR_TERRITORIAL_V2, _territorial_0012(b, b.geografia, b.bases, "EXITOSA")
    )
    con_otra_configuracion, con_otra_decision, con_otra_territorial, otro_plan = _insertar(
        motor,
        INSERTAR_PLAN_CAMPO,
        _plan_0013(b, otra_configuracion),
        _plan_0013(b, configuracion, decision=otra_decision),
        _plan_0013(b, configuracion, territorial=otra_territorial),
        _plan_0013(b, configuracion),
    )

    def asignacion(**cambios) -> dict:
        return {**_asignacion_campo(b, plan, configuracion, gestor), **cambios}

    revision = {
        "estado": "REVISION_TERRITORIAL",
        "base": None,
        "gestor": None,
        "orden": None,
        "zona": None,
        "subzona": None,
        "tramo": None,
        "microtramo": None,
    }
    for restriccion, fila in (
        ("ck_asignacion_campo_estado", asignacion(gestor=None)),
        ("ck_asignacion_campo_estado", asignacion(microtramo=None)),
        ("ck_asignacion_campo_estado", asignacion(orden=None)),
        ("ck_asignacion_campo_estado", asignacion(orden=0)),
        ("ck_asignacion_campo_estado", asignacion(estado="SIN_CAPACIDAD")),
        (
            "ck_asignacion_campo_estado",
            asignacion(estado="SIN_CAPACIDAD", gestor=None, rebalanceada=True),
        ),
        ("ck_asignacion_campo_estado", asignacion(estado="CERCA")),
        ("ck_asignacion_campo_estado", asignacion(prioridad="URGENTE")),
        ("ck_asignacion_campo_estado", asignacion(**{**revision, "zona": uuid4()})),
        ("ck_asignacion_campo_estado", asignacion(**{**revision, "base": b.base})),
        # El gestor es de la base de la cuenta y de la configuracion del plan: nunca de otra.
        ("fk_asignacion_campo_gestor", asignacion(gestor=gestor_c)),
        (
            "fk_asignacion_campo_base",
            asignacion(gestor=None, estado="SIN_CAPACIDAD", base=otra_base),
        ),
        # La configuracion, la decision/v2 y territorial/v2 son las del plan, y la cuenta es una que
        # esa decision/v2 decidio y esa territorial/v2 asigno.
        ("fk_asignacion_campo_ejecucion", asignacion(plan=con_otra_configuracion)),
        (
            "fk_asignacion_campo_decision",
            asignacion(plan=con_otra_decision, decision=otra_decision),
        ),
        (
            "fk_asignacion_campo_territorial",
            asignacion(plan=con_otra_territorial, territorial=otra_territorial),
        ),
    ):
        _rechaza(motor, restriccion, INSERTAR_ASIGNACION_CAMPO, fila)
    _insertar(
        motor,
        INSERTAR_ASIGNACION_CAMPO,
        asignacion(),
        asignacion(
            plan=con_otra_configuracion,
            configuracion=otra_configuracion,
            gestor=None,
            estado="SIN_CAPACIDAD",
        ),
        asignacion(plan=otro_plan, **revision),
    )
    _rechaza(motor, "pk_asignacion_campo_cuenta", INSERTAR_ASIGNACION_CAMPO, asignacion())


def test_una_unidad_cuelga_de_su_padre_y_una_carga_cuadra_con_su_jornada(base_en_0013):
    b = base_en_0013
    motor = b.motor
    configuracion, calendario, gestor = _configuracion_con_gestor(b)
    (inactivo,) = _insertar(
        motor,
        INSERTAR_GESTOR_CAMPO,
        _gestor_campo(b, configuracion, calendario, codigo="GESTOR_002"),
    )
    (plan,) = _insertar(motor, INSERTAR_PLAN_CAMPO, _plan_0013(b, configuracion))
    zona, subzona, tramo = uuid4(), uuid4(), uuid4()

    def unidad(**cambios) -> dict:
        return _unidad_campo(
            b, plan, **{"nivel": "SUBZONA", "padre": zona, "cvegeo": "21114", **cambios}
        )

    _insertar(
        motor,
        INSERTAR_UNIDAD_CAMPO,
        unidad(unidad=zona, nivel="ZONA", padre=None, cvegeo=None),
        unidad(unidad=subzona),
        unidad(unidad=tramo, nivel="TRAMO", padre=subzona),
        unidad(nivel="MICROTRAMO", padre=tramo, propietario=gestor),
    )
    for restriccion, fila in (
        ("ck_unidad_campo_nivel", unidad(padre=None)),
        ("ck_unidad_campo_nivel", unidad(nivel="ZONA")),
        ("ck_unidad_campo_nivel", unidad(cvegeo=None)),
        ("ck_unidad_campo_nivel", unidad(cvegeo="31050")),
        ("ck_unidad_campo_nivel", unidad(cvegeo="2111")),
        ("ck_unidad_campo_nivel", unidad(asignadas=3)),
        ("ck_unidad_campo_nivel", unidad(candidatas=0, asignadas=0)),
        ("ck_unidad_campo_nivel", unidad(propietario=gestor)),
        ("ck_unidad_campo_nivel", unidad(nivel="MANZANA")),
        ("fk_unidad_campo_padre", unidad(padre=uuid4())),
        ("fk_unidad_campo_propietario", unidad(nivel="MICROTRAMO", propietario=999_999)),
        ("pk_unidad_territorial_campo", unidad(unidad=subzona)),
    ):
        _rechaza(motor, restriccion, INSERTAR_UNIDAD_CAMPO, fila)

    def carga(**cambios) -> dict:
        return {**_carga_campo(b, plan, gestor), **cambios}

    sin_jornada = {"capacidad": 0, "asignadas": 0, "microtramos": 0, "tramos": 0}
    for fila in (
        carga(asignadas=21),
        carga(capacidad=0),
        carga(jornada="INACTIVO"),
        carga(jornada="CERRADO_POR_EXCEPCION"),
        # Sin capacidad no hay utilizacion; con capacidad, siempre.
        carga(jornada="INACTIVO", **sin_jornada),
        carga(utilizacion=None),
        carga(tramos=4),
        carga(asignadas=0),
        carga(microtramos=0, tramos=0),
        carga(jornada="DE_VACACIONES"),
    ):
        _rechaza(motor, "ck_carga_campo_conteos", INSERTAR_CARGA_CAMPO, fila)
    _insertar(
        motor,
        INSERTAR_CARGA_CAMPO,
        carga(),
        carga(gestor=inactivo, jornada="INACTIVO", utilizacion=None, **sin_jornada),
    )
    _rechaza(motor, "pk_carga_gestor_campo", INSERTAR_CARGA_CAMPO, carga())


@pytest.mark.parametrize(("tabla", "trigger"), sorted(INMUTABLES_DE_LA_0013.items()))
def test_lo_que_publico_campo_v2_no_se_reescribe(base_en_0013, tabla, trigger):
    b = base_en_0013
    motor = b.motor
    configuracion, calendario, gestor = _configuracion_con_gestor(b)
    _insertar(
        motor,
        INSERTAR_EXCEPCION_CAMPO,
        {"calendario": calendario, "fecha": "2026-11-16", "tipo": "CERRADO"},
    )
    (plan,) = _insertar(motor, INSERTAR_PLAN_CAMPO, _plan_0013(b, configuracion))
    _insertar(motor, INSERTAR_ASIGNACION_CAMPO, _asignacion_campo(b, plan, configuracion, gestor))
    _insertar(motor, INSERTAR_UNIDAD_CAMPO, _unidad_campo(b, plan))
    _insertar(motor, INSERTAR_CARGA_CAMPO, _carga_campo(b, plan, gestor))
    (columna,) = _consultar(
        motor,
        "SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() "
        f"AND table_name = '{tabla}' AND column_name NOT IN ('id') ORDER BY ordinal_position "
        "LIMIT 1",
    )

    # Aunque no toque ninguna fila: es la sentencia la que no se permite.
    for sentencia in (
        f"UPDATE {tabla} SET {columna} = {columna}",
        f"DELETE FROM {tabla}",
        f"DELETE FROM {tabla} WHERE false",
    ):
        with pytest.raises(DBAPIError) as error, motor.begin() as conexion:
            conexion.execute(text(sentencia))
        assert error.value.orig.sqlstate == "23001", sentencia  # restrict_violation
        assert error.value.orig.diag.constraint_name == trigger
    assert _consultar(motor, f"SELECT count(*) FROM {tabla}") == [1]


def test_un_plan_solo_cambia_mientras_esta_en_proceso_y_ninguno_se_borra(base_en_0013):
    b = base_en_0013
    motor = b.motor
    configuracion, _, _ = _configuracion_con_gestor(b)
    (plan,) = _insertar(motor, INSERTAR_PLAN_CAMPO, _plan_0013(b, configuracion))
    borrar = text("DELETE FROM ejecucion_campo_v2 WHERE id = :id")

    with pytest.raises(DBAPIError) as error, motor.begin() as conexion:
        conexion.execute(borrar, {"id": plan})
    assert error.value.orig.diag.constraint_name == "tr_campo_v2_terminado"
    with motor.begin() as conexion:
        conexion.execute(
            text(
                "UPDATE ejecucion_campo_v2 SET estado = 'EXITOSA', resultado = 'PLAN_PUBLICADO', "
                "candidatas_campo = 1, asignadas = 1, capacidad_total = 20, "
                "capacidad_utilizada = 1, terminada_en = now() WHERE id = :id"
            ),
            {"id": plan},
        )

    for sentencia in (
        text("UPDATE ejecucion_campo_v2 SET detalle = 'otro' WHERE id = :id"),
        text(
            "UPDATE ejecucion_campo_v2 SET estado = 'EN_PROCESO', resultado = NULL, "
            "candidatas_campo = 0, asignadas = 0, capacidad_total = 0, capacidad_utilizada = 0 "
            "WHERE id = :id"
        ),
        borrar,
    ):
        with pytest.raises(DBAPIError) as error, motor.begin() as conexion:
            conexion.execute(sentencia, {"id": plan})
        assert error.value.orig.sqlstate == "23001"  # restrict_violation
        assert error.value.orig.diag.constraint_name == "tr_campo_v2_terminado"
    assert _consultar(motor, f"SELECT estado FROM ejecucion_campo_v2 WHERE id = {plan}") == [
        "EXITOSA"
    ]


def test_el_trabajo_campo_v2_apunta_a_su_plan_y_a_nada_mas(base_en_0013):
    b = base_en_0013
    motor = b.motor
    configuracion, _, _ = _configuracion_con_gestor(b)
    (plan,) = _insertar(motor, INSERTAR_PLAN_CAMPO, _plan_0013(b, configuracion))
    _insertar(motor, INSERTAR_TRABAJO_0013, _trabajo_0013(campo=plan))

    for restriccion, trabajo in (
        ("uq_trabajo_campo_v2", _trabajo_0013(campo=plan)),
        ("ck_trabajo_objetivo", _trabajo_0013()),
        ("ck_trabajo_objetivo", _trabajo_0013(campo=plan, territorial=b.territorial)),
        ("ck_trabajo_objetivo", _trabajo_0013("TERRITORIAL_V2", campo=plan)),
        ("fk_trabajo_campo_v2", _trabajo_0013(campo=999_999)),
        ("ck_trabajo_orquestacion_tipo_trabajo", _trabajo_0013("CAMPO_V3")),
    ):
        _rechaza(motor, restriccion, INSERTAR_TRABAJO_0013, trabajo)


def test_la_0013_baja_cerrando_lo_que_estaba_en_curso_y_vuelve_a_subir(base_en_0013):
    b = base_en_0013
    motor = b.motor
    # Una configuracion, un plan publicado con su asignacion y otro en curso con su trabajo.
    configuracion, _, gestor = _configuracion_con_gestor(b)
    (publicado,) = _insertar(motor, INSERTAR_PLAN_CAMPO, _plan_0013(b, configuracion, "EXITOSA"))
    _insertar(
        motor, INSERTAR_ASIGNACION_CAMPO, _asignacion_campo(b, publicado, configuracion, gestor)
    )
    (en_curso,) = _insertar(motor, INSERTAR_PLAN_CAMPO, _plan_0013(b, configuracion))
    _insertar(motor, INSERTAR_TRABAJO_0013, _trabajo_0013(estado="EJECUTANDO", campo=en_curso))
    previas = _por_columna(motor, PREVIAS_A_LA_0008)
    trabajos_antes = [
        {k: v for k, v in f.items() if k != "ejecucion_campo_v2_id"}
        for f in previas["trabajo_orquestacion"]
        if f["tipo"] != "CAMPO_V2"
    ]
    assert len(previas["trabajo_orquestacion"]) == len(trabajos_antes) + 1

    command.downgrade(_alembic(), "0012")

    # campo/v2 entero se fue, con su trabajo y con la restriccion que le agrego a territorial/v2. Lo
    # de v0.11, intacto: PostGIS, la geografia, las bases, territorial/v2, decision/v2 y todo lo
    # anterior; los otros trabajos, sin su columna nueva.
    assert not TABLAS_DE_LA_0013 & set(_consultar(motor, TABLAS))
    assert not FUNCIONES_DE_LA_0013 & set(_consultar(motor, FUNCIONES))
    assert "uq_territorial_v2_corte_bases" not in _restricciones_de_territorial_v2(motor)
    assert _consultar(motor, POSTGIS) == ["postgis"]
    assert _de_la_v011(motor) == b.de_la_v011
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
    assert not any("CAMPO_V2" in d for d in definiciones)
    assert all("TERRITORIAL_V2" in d for d in definiciones)
    assert _consultar(motor, "SELECT version_num FROM alembic_version") == ["0012"]

    # Y vuelve a subir limpia: todo vacio, con sus restricciones.
    command.upgrade(_alembic(), "0013")

    for tabla in sorted(TABLAS_DE_LA_0013):
        assert _consultar(motor, f"SELECT count(*) FROM {tabla}") == [0]
    assert set(_consultar(motor, RESTRICCIONES_0013)) == RESTRICCIONES_DE_LA_0013
    assert FUNCIONES_DE_LA_0013 <= set(_consultar(motor, FUNCIONES))
    assert "uq_territorial_v2_corte_bases" in _restricciones_de_territorial_v2(motor)
    assert _de_la_v011(motor) == b.de_la_v011
    assert _consultar(motor, "SELECT version_num FROM alembic_version") == ["0013"]


def test_al_bajar_la_0013_lo_primero_es_cerrar_lo_que_estaba_en_curso(base_en_0013):
    # Lo que la 0013 hace antes de quitar sus tablas, con sus mismas sentencias: los planes en curso
    # quedan FALLIDA y sus trabajos FALLIDO, sin dueno ni lease, con el motivo. Lo terminado no se
    # toca.
    b = base_en_0013
    motor = b.motor
    modulo = _modulo_0013()
    configuracion, _, _ = _configuracion_con_gestor(b)
    (publicado,) = _insertar(motor, INSERTAR_PLAN_CAMPO, _plan_0013(b, configuracion, "EXITOSA"))
    (en_curso,) = _insertar(motor, INSERTAR_PLAN_CAMPO, _plan_0013(b, configuracion))
    _insertar(motor, INSERTAR_TRABAJO_0013, _trabajo_0013(estado="EJECUTANDO", campo=en_curso))

    with motor.begin() as conexion:
        conexion.execute(modulo.CERRAR_PLANES)
        conexion.execute(modulo.CERRAR_TRABAJOS)

    with motor.connect() as conexion:
        planes = conexion.execute(
            text("SELECT id, estado, resultado, detalle FROM ejecucion_campo_v2 ORDER BY id")
        ).all()
        trabajos = conexion.execute(
            text(
                "SELECT tipo, estado, worker_id, lease_hasta, ultimo_error FROM "
                "trabajo_orquestacion WHERE tipo = 'CAMPO_V2' ORDER BY id"
            )
        ).all()
    assert [tuple(p) for p in planes] == [
        (publicado, "EXITOSA", "PLAN_PUBLICADO", None),
        (en_curso, "FALLIDA", "CERRADA_AL_BAJAR", modulo.CIERRE),
    ]
    assert [tuple(t) for t in trabajos] == [("CAMPO_V2", "FALLIDO", None, None, modulo.CIERRE)]


# --- la 0014: ruteo/v2 ----------------------------------------------------------------------------

TABLAS_DE_LA_0014 = {"version_red_vial", "ejecucion_ruteo_v2", "ruta_gestor_v2", "parada_ruta_v2"}
_DE_LA_0014 = ", ".join(f"'{tabla}'" for tabla in sorted(TABLAS_DE_LA_0014))
RESTRICCIONES_0014 = RESTRICCIONES.replace("'ejecucion_decision', 'decision_cuenta'", _DE_LA_0014)
INDICES_0014 = INDICES.replace("'ejecucion_decision', 'decision_cuenta'", _DE_LA_0014)
RESTRICCIONES_DE_LA_0014 = {
    "pk_version_red_vial",
    "uq_version_red_vial_red_vial_version_id",
    "ck_red_vial_hashes",
    "ck_red_vial_publicacion",
    "ck_version_red_vial_estado_version_red_vial",
    "pk_ejecucion_ruteo_v2",
    "uq_ejecucion_ruteo_v2_ruteo_run_id",
    "uq_ruteo_v2_campo",
    "fk_ruteo_v2_campo",
    "fk_ruteo_v2_red",
    "ck_ruteo_v2_firmas",
    "ck_ruteo_v2_resultado",
    "ck_ruteo_v2_conteos",
    "ck_ruteo_v2_publicacion",
    "ck_ejecucion_ruteo_v2_estado_ruteo_v2",
    "pk_ruta_gestor_v2",
    "uq_ruta_gestor_v2_ruta_id",
    "uq_ruta_v2_campo",
    "fk_ruta_v2_ejecucion",
    "fk_ruta_v2_carga",
    "ck_ruta_v2_coherencia",
    "ck_ruta_v2_hashes",
    "pk_parada_ruta_v2",
    "uq_parada_v2_orden",
    "fk_parada_v2_ruta",
    "fk_parada_v2_asignacion",
    "ck_parada_v2_coherencia",
}
RESTRICCIONES_DE_LA_0014_EN_LA_COLA = {"fk_trabajo_ruteo_v2", "uq_trabajo_ruteo_v2"}
INDICES_DE_LA_0014 = {
    ("version_red_vial", "pk_version_red_vial", True, ("id",), None),
    (
        "version_red_vial",
        "uq_version_red_vial_red_vial_version_id",
        True,
        ("red_vial_version_id",),
        None,
    ),
    (
        "version_red_vial",
        "ux_red_vial_publicada",
        True,
        ("huella",),
        _POR_ESTADO.format("PUBLICADA"),
    ),
    (
        "version_red_vial",
        "ux_red_vial_en_proceso",
        True,
        ("huella",),
        _POR_ESTADO.format("EN_PROCESO"),
    ),
    ("ejecucion_ruteo_v2", "pk_ejecucion_ruteo_v2", True, ("id",), None),
    ("ejecucion_ruteo_v2", "uq_ejecucion_ruteo_v2_ruteo_run_id", True, ("ruteo_run_id",), None),
    ("ejecucion_ruteo_v2", "uq_ruteo_v2_campo", True, ("id", "ejecucion_campo_v2_id"), None),
    (
        "ejecucion_ruteo_v2",
        "ux_ruteo_v2_exitosa",
        True,
        ("firma_inputs",),
        _POR_ESTADO.format("EXITOSA"),
    ),
    (
        "ejecucion_ruteo_v2",
        "ux_ruteo_v2_en_proceso",
        True,
        ("firma_inputs",),
        _POR_ESTADO.format("EN_PROCESO"),
    ),
    ("ejecucion_ruteo_v2", "ix_ruteo_v2_campo", False, ("ejecucion_campo_v2_id", "id"), None),
    (
        "ruta_gestor_v2",
        "pk_ruta_gestor_v2",
        True,
        ("ejecucion_ruteo_v2_id", "gestor_campo_id"),
        None,
    ),
    ("ruta_gestor_v2", "uq_ruta_gestor_v2_ruta_id", True, ("ruta_id",), None),
    (
        "ruta_gestor_v2",
        "uq_ruta_v2_campo",
        True,
        ("ejecucion_ruteo_v2_id", "gestor_campo_id", "ejecucion_campo_v2_id"),
        None,
    ),
    (
        "parada_ruta_v2",
        "pk_parada_ruta_v2",
        True,
        ("ejecucion_ruteo_v2_id", "cuenta_canonica_id"),
        None,
    ),
    (
        "parada_ruta_v2",
        "uq_parada_v2_orden",
        True,
        ("ejecucion_ruteo_v2_id", "gestor_campo_id", "orden_visita"),
        None,
    ),
    (
        "parada_ruta_v2",
        "ix_parada_v2_estado",
        False,
        ("ejecucion_ruteo_v2_id", "estado", "cuenta_canonica_id"),
        None,
    ),
    (
        "parada_ruta_v2",
        "ix_parada_v2_cuenta",
        False,
        ("cuenta_canonica_id", "ejecucion_ruteo_v2_id"),
        None,
    ),
}
"""Las llaves, la unicidad y las paginas que la API lee de una ejecucion: ni un indice mas."""
INMUTABLES_DE_LA_0014 = {
    "ruta_gestor_v2": "tr_ruteo_v2_inmutable",
    "parada_ruta_v2": "tr_ruteo_v2_inmutable",
}
TRIGGERS_DE_LA_0014 = {
    *INMUTABLES_DE_LA_0014.items(),
    ("ejecucion_ruteo_v2", "tr_ruteo_v2_terminado"),
    ("version_red_vial", "tr_red_vial_terminada"),
}
FUNCIONES_DE_LA_0014 = {"ruteo_v2_inmutable", "ruteo_v2_terminado", "red_vial_terminada"}
SIN_GEOMETRIA = SIN_COORDENADAS | {"polilinea", "polyline", "geometry", "coordenadas", "ruta_geo"}
"""Ninguna tabla de ruteo/v2 guarda una coordenada ni una geometria: ni de la cuenta, ni del snap,
ni del recorrido."""

NUEVE = datetime(2026, 10, 1, 15, tzinfo=UTC)
"""Las 09:00 del jueves en la Ciudad de Mexico, en UTC: donde empieza la jornada."""

INSERTAR_PLAN_CAMPO_DE_DOS = text(
    INSERTAR_PLAN_CAMPO.text.replace(":resultado, 1, :candidatas", ":resultado, 2, :candidatas")
)
"""Un plan que evaluo dos cuentas de su decision/v2."""
INSERTAR_RED_VIAL = text(
    "INSERT INTO version_red_vial (red_vial_version_id, huella, proveedor_datos, "
    "proveedor_extracto, licencia, fecha_snapshot, region, archivo_fuente, url_fuente, "
    "sha256_fuente, motor_ruteo, version_motor_ruteo, distribucion_motor, algoritmo_motor, "
    "perfil, hash_perfil, identidad, hash_manifest, manifiesto, estado, metadatos, creada_en, "
    "publicada_en, detalle) VALUES (:id, :huella, 'OpenStreetMap', NULL, 'ODbL-1.0', '2026-10-11', "
    "'Red de la migracion', 'red.osm', NULL, :sha256, :motor, 'v26.9.0', 'docker:imagen', 'MLD', "
    "'car', :perfil, CAST('{}' AS JSONB), :manifest, CAST(:manifiesto AS JSONB), :estado, "
    "CAST('{}' AS JSONB), now(), :publicada, :detalle) RETURNING id"
)
CONTEOS_RUTEO_V2 = (
    "rutas_completas",
    "rutas_parciales",
    "rutas_sin_paradas",
    "bases_no_enrutables",
    "rutas_sin_jornada",
    "programadas",
    "sin_tiempo",
    "no_enrutables",
    "no_enrutables_base",
)
INSERTAR_RUTEO_V2 = text(
    "INSERT INTO ejecucion_ruteo_v2 (ruteo_run_id, ejecucion_campo_v2_id, version_red_vial_id, "
    "fecha_operacion, version_motor, version_politica, hash_politica, firma_inputs, estado, "
    f"resultado, gestores_objetivo, paradas_objetivo, {', '.join(CONTEOS_RUTEO_V2)}, "
    "distancia_total_m, duracion_vial_total_s, duracion_servicio_total_s, "
    "duracion_espera_total_s, iniciada_en, terminada_en) VALUES (:run, :plan, :red, '2026-10-01', "
    "'ruteo/v2', 'ruteo-public-v2', :hash_politica, :firma, :estado, :resultado, :gestores, "
    f":paradas, {', '.join(':' + c for c in CONTEOS_RUTEO_V2)}, :distancia, :vial, :servicio, "
    ":espera, now(), :terminada) RETURNING id"
)
INSERTAR_RUTA_V2 = text(
    "INSERT INTO ruta_gestor_v2 (ejecucion_ruteo_v2_id, gestor_campo_id, ruta_id, "
    "ejecucion_campo_v2_id, base_operativa_id, fecha_operacion, estado_ruta, motivo_base, "
    "paradas_campo, programadas, sin_tiempo, no_enrutables, no_enrutables_base, "
    "base_snap_distancia_m, jornada_inicio_at, jornada_fin_at, salida_base_at, salida_regreso_at, "
    "regreso_base_at, distancia_regreso_m, duracion_regreso_s, espera_regreso_s, "
    "distancia_total_m, duracion_traslado_s, duracion_servicio_s, duracion_espera_s, "
    "duracion_total_s, mejoras_2opt, hash_matriz, hash_secuencia, creada_en) VALUES (:ejecucion, "
    ":gestor, :ruta_id, :plan, :base, '2026-10-01', :estado_ruta, :motivo_base, :paradas_campo, "
    ":programadas, :sin_tiempo, :no_enrutables, :no_enrutables_base, :base_snap, "
    ":jornada_inicio, :jornada_fin, :salida_base, :salida_regreso, :regreso_base, "
    ":distancia_regreso, :duracion_regreso, :espera_regreso, :distancia_total, :traslado, "
    ":servicio, :espera, :total, :mejoras, :hash_matriz, :hash_secuencia, now()) "
    "RETURNING ejecucion_ruteo_v2_id"
)
INSERTAR_PARADA_V2 = text(
    "INSERT INTO parada_ruta_v2 (snap_distancia_m, salida_anterior_at, llegada_estimada, "
    "inicio_servicio, fin_servicio, distancia_desde_anterior_m, ejecucion_ruteo_v2_id, "
    "cuenta_canonica_id, gestor_campo_id, ejecucion_campo_v2_id, orden_visita, "
    "duracion_desde_anterior_s, espera_s, prioridad, estado, motivo) VALUES (:snap, :salida, "
    ":llegada, :inicio, :fin, :distancia, :ejecucion, :cuenta, :gestor, :plan, :orden, "
    ":duracion, :espera, :prioridad, :estado, :motivo) RETURNING ejecucion_ruteo_v2_id"
)
INSERTAR_TRABAJO_0014 = text(
    "INSERT INTO trabajo_orquestacion (trabajo_id, tipo, estado, ejecucion_ruteo_v2_id, "
    "ejecucion_campo_v2_id, intentos, max_intentos, creado_en, disponible_desde, worker_id, "
    "lease_hasta, tomado_en) VALUES (:trabajo_id, :tipo, :estado, :ruteo, :campo, :intentos, 5, "
    "now(), now(), :worker_id, :lease_hasta, now()) RETURNING id"
)


def _modulo_0014():
    return ScriptDirectory.from_config(_alembic()).get_revision("0014").module


def _de_la_v012(motor: Engine) -> dict[str, list[dict]]:
    """Lo que la 0014 no puede tocar: todo lo de v0.11, campo/v2 y su configuracion."""
    filas = _de_la_v011(motor)
    llaves = {
        "version_configuracion_campo": "id",
        "calendario_campo": "id",
        "excepcion_calendario_campo": "id",
        "ventana_jornada_campo": "id",
        "gestor_campo": "id",
        "ejecucion_campo_v2": "id",
        "asignacion_campo_cuenta": "ejecucion_campo_v2_id, cuenta_canonica_id",
        "unidad_territorial_campo": "ejecucion_campo_v2_id, unidad_id",
        "carga_gestor_campo": "ejecucion_campo_v2_id, gestor_campo_id",
    }
    with motor.connect() as conexion:
        for tabla, orden in llaves.items():
            filas[tabla] = [
                dict(fila)
                for fila in conexion.execute(text(f"SELECT * FROM {tabla} ORDER BY {orden}"))
                .mappings()
                .all()
            ]
    return filas


def _otra_cuenta_decidida_y_asignada(b: BaseEn0013) -> int:
    """Una segunda cuenta del corte de `b`, decidida por su decision/v2, con su geografia y
    asignada a su base por su territorial/v2: para que un plan tenga dos cuentas ASIGNADA. Los
    conteos de esas ejecuciones no se tocan (lo publicado no se reescribe, y la base no los cruza
    con sus filas)."""
    motor = b.motor
    (cuenta,) = _insertar(
        motor,
        INSERTAR_CUENTA_CANONICA,
        {"cuenta_id": uuid4(), "cartera": "CARTERA_PRINCIPAL", "cliente": "CU0000000002"},
    )
    (fecha,) = _consultar(motor, f"SELECT fecha_corte FROM corte_canonico WHERE id = {b.corte}")
    _insertar(
        motor, INSERTAR_SNAPSHOT, {"corte": b.corte, "cuenta": cuenta, "fecha": fecha, "fila": 3}
    )
    _insertar(motor, INSERTAR_FEATURE_0011, _feature_0011(b.decision, b.corte, cuenta))
    _insertar(motor, INSERTAR_RESULTADO_0011, _resultado_0011(b.decision, cuenta))
    (municipio,) = _consultar(
        motor,
        "SELECT municipio_geografico_id FROM resultado_geografia_cuenta "
        f"WHERE ejecucion_geografia_id = {b.geografia}",
    )
    _insertar(
        motor,
        INSERTAR_RESULTADO_GEOGRAFIA,
        {**_resultado_geografia(b, b.geografia, municipio), "cuenta": cuenta},
    )
    _insertar(
        motor,
        INSERTAR_ASIGNACION,
        _asignacion(b, b.territorial, b.geografia, b.bases, municipio, b.base, cuenta=cuenta),
    )
    return cuenta


@dataclass(frozen=True)
class BaseEn0014:
    """Una base de v0.12 con un plan de campo/v2 publicado (dos cuentas ASIGNADA a un gestor, la
    carga de ese gestor y la de otro de la misma base), subida a la 0014: ruteo/v2 vacio."""

    motor: Engine
    cuenta: int
    otra_cuenta: int
    configuracion: int
    calendario: int
    plan: int
    gestor: int
    otro_gestor: int
    base: int
    bases: int
    previas: dict[str, list[dict]]
    de_la_v012: dict[str, list[dict]]
    anterior: BaseEn0013


@pytest.fixture
def base_en_0014(base_en_0013) -> BaseEn0014:
    b = base_en_0013
    motor = b.motor
    configuracion, calendario, gestor = _configuracion_con_gestor(b)
    (otro_gestor,) = _insertar(
        motor,
        INSERTAR_GESTOR_CAMPO,
        _gestor_campo(b, configuracion, calendario, codigo="GESTOR_002"),
    )
    otra_cuenta = _otra_cuenta_decidida_y_asignada(b)
    (plan,) = _insertar(
        motor,
        INSERTAR_PLAN_CAMPO_DE_DOS,
        _plan_0013(b, configuracion, "EXITOSA", candidatas=2, asignadas=2, utilizada=2),
    )
    _insertar(
        motor,
        INSERTAR_ASIGNACION_CAMPO,
        _asignacion_campo(b, plan, configuracion, gestor),
        _asignacion_campo(b, plan, configuracion, gestor, cuenta=otra_cuenta, orden=2),
    )
    _insertar(
        motor,
        INSERTAR_CARGA_CAMPO,
        _carga_campo(b, plan, gestor, asignadas=2),
        _carga_campo(b, plan, otro_gestor, asignadas=1, microtramos=1),
    )
    previas = _por_columna(motor, PREVIAS_A_LA_0008)
    de_la_v012 = _de_la_v012(motor)

    command.upgrade(_alembic(), "0014")
    return BaseEn0014(
        motor,
        b.cuenta,
        otra_cuenta,
        configuracion,
        calendario,
        plan,
        gestor,
        otro_gestor,
        b.base,
        b.bases,
        previas,
        de_la_v012,
        b,
    )


def _red_vial(estado: str = "PUBLICADA", **cambios) -> dict:
    publicada = estado == "PUBLICADA"
    return {
        "id": uuid4(),
        "huella": uuid4().hex * 2,
        "sha256": "b" * 64,
        "motor": "OSRM",
        "perfil": "c" * 64,
        "manifest": "d" * 64 if publicada else None,
        "manifiesto": '{"artefactos": []}' if publicada else None,
        "estado": estado,
        "publicada": datetime.now(UTC) if publicada else None,
        "detalle": "La preparacion fallo." if estado == "FALLIDA" else None,
        **cambios,
    }


def _ruteo_0014(b: BaseEn0014, red: int, estado: str = "EN_PROCESO", **cambios) -> dict:
    terminada = estado != "EN_PROCESO"
    exitosa = estado == "EXITOSA"
    return {
        "run": uuid4(),
        "plan": b.plan,
        "red": red,
        "hash_politica": "a" * 64,
        "firma": uuid4().hex * 2,
        "estado": estado,
        "resultado": "RUTAS_PUBLICADAS" if exitosa else "ERROR_INTERNO" if terminada else None,
        "gestores": 1 if exitosa else 0,
        "paradas": 1 if exitosa else 0,
        **{c: 0 for c in CONTEOS_RUTEO_V2},
        "rutas_completas": 1 if exitosa else 0,
        "programadas": 1 if exitosa else 0,
        "distancia": 2_000 if exitosa else 0,
        "vial": 1_200 if exitosa else 0,
        "servicio": 900 if exitosa else 0,
        "espera": 0,
        "terminada": datetime.now(UTC) if terminada else None,
        **cambios,
    }


def _ruta_0014(b: BaseEn0014, ejecucion: int, **cambios) -> dict:
    """Una ruta COMPLETA de una parada: sale a las 09:00, llega en 10 min, atiende 15 y regresa en
    10, a las 09:35."""
    return {
        "ejecucion": ejecucion,
        "gestor": b.gestor,
        "ruta_id": uuid4(),
        "plan": b.plan,
        "base": b.base,
        "estado_ruta": "COMPLETA",
        "motivo_base": None,
        "paradas_campo": 1,
        "programadas": 1,
        "sin_tiempo": 0,
        "no_enrutables": 0,
        "no_enrutables_base": 0,
        "base_snap": Decimal("0.00"),
        "jornada_inicio": NUEVE,
        "jornada_fin": NUEVE + timedelta(hours=5),
        "salida_base": NUEVE,
        "salida_regreso": NUEVE + timedelta(minutes=25),
        "regreso_base": NUEVE + timedelta(minutes=35),
        "distancia_regreso": 1_000,
        "duracion_regreso": 600,
        "espera_regreso": 0,
        "distancia_total": 2_000,
        "traslado": 1_200,
        "servicio": 900,
        "espera": 0,
        "total": 2_100,
        "mejoras": 0,
        "hash_matriz": "e" * 64,
        "hash_secuencia": "f" * 64,
        **cambios,
    }


SIN_SALIDA = {
    "programadas": 0,
    "salida_base": None,
    "salida_regreso": None,
    "regreso_base": None,
    "distancia_regreso": None,
    "duracion_regreso": None,
    "espera_regreso": None,
    "distancia_total": 0,
    "traslado": 0,
    "servicio": 0,
    "total": 0,
    "hash_matriz": None,
}
"""Lo de una ruta que no sale de su base: sin salida, sin regreso, sin distancia."""


def _parada_0014(b: BaseEn0014, ejecucion: int, **cambios) -> dict:
    """La parada PROGRAMADA de la ruta de `_ruta_0014`: la primera, de 09:10 a 09:25."""
    return {
        "snap": Decimal("12.34"),
        "salida": NUEVE,
        "llegada": NUEVE + timedelta(minutes=10),
        "inicio": NUEVE + timedelta(minutes=10),
        "fin": NUEVE + timedelta(minutes=25),
        "distancia": 1_000,
        "ejecucion": ejecucion,
        "cuenta": b.cuenta,
        "gestor": b.gestor,
        "plan": b.plan,
        "orden": 1,
        "duracion": 600,
        "espera": 0,
        "prioridad": "MUY_ALTA",
        "estado": "PROGRAMADA",
        "motivo": None,
        **cambios,
    }


SIN_PROGRAMAR = {
    "salida": None,
    "llegada": None,
    "inicio": None,
    "fin": None,
    "distancia": None,
    "orden": None,
    "duracion": None,
    "espera": None,
}
"""Lo de una parada que no se programo: sin orden, sin horas y sin tramo."""


def _trabajo_0014(tipo: str = "RUTEO_V2", estado: str = "PENDIENTE", **cambios) -> dict:
    ejecutando = estado == "EJECUTANDO"
    return {
        "trabajo_id": uuid4(),
        "tipo": tipo,
        "estado": estado,
        "ruteo": None,
        "campo": None,
        "intentos": 1 if ejecutando else 0,
        "worker_id": "worker-de-prueba:1:abc" if ejecutando else None,
        "lease_hasta": datetime.now(UTC) + timedelta(minutes=1) if ejecutando else None,
        **cambios,
    }


def _publicada_0014(b: BaseEn0014) -> tuple[int, int]:
    """Una red PUBLICADA y una ejecucion EXITOSA sobre el plan de `b`, con su ruta y su parada."""
    (red,) = _insertar(b.motor, INSERTAR_RED_VIAL, _red_vial())
    (ejecucion,) = _insertar(b.motor, INSERTAR_RUTEO_V2, _ruteo_0014(b, red, "EXITOSA"))
    _insertar(b.motor, INSERTAR_RUTA_V2, _ruta_0014(b, ejecucion))
    _insertar(b.motor, INSERTAR_PARADA_V2, _parada_0014(b, ejecucion))
    return red, ejecucion


def _restricciones_de(motor: Engine, tabla: str) -> set[str]:
    return set(
        _consultar(
            motor,
            f"SELECT conname FROM pg_constraint WHERE conrelid = CAST('{tabla}' AS regclass)",
        )
    )


def test_la_0013_sigue_a_la_0012():
    scripts = ScriptDirectory.from_config(_alembic())

    assert scripts.get_revision("0013").down_revision == "0012"


def test_la_0014_sigue_a_la_0013():
    scripts = ScriptDirectory.from_config(_alembic())

    assert scripts.get_revision("0014").down_revision == "0013"


def test_los_modelos_declaran_el_esquema_de_la_0014():
    from motor_cartera.db import modelos

    tablas = [modelos.SQLModel.metadata.tables[t] for t in sorted(TABLAS_DE_LA_0014)]
    preparador = postgresql.dialect().identifier_preparer
    nombres = {preparador.format_constraint(c) for t in tablas for c in t.constraints}
    assert nombres == RESTRICCIONES_DE_LA_0014
    assert all(len(nombre) <= 63 for nombre in nombres)
    # Lo que crece con cada ejecucion no tiene id propio: su llave es la natural.
    for tabla, llave in (
        ("ruta_gestor_v2", ["ejecucion_ruteo_v2_id", "gestor_campo_id"]),
        ("parada_ruta_v2", ["ejecucion_ruteo_v2_id", "cuenta_canonica_id"]),
    ):
        assert [c.name for c in modelos.SQLModel.metadata.tables[tabla].primary_key] == llave
        assert "id" not in modelos.SQLModel.metadata.tables[tabla].c
    # Ni una geometria ni una coordenada: ni de la cuenta, ni de su snap, ni del recorrido.
    for tabla in tablas:
        for columna in tabla.columns:
            assert not hasattr(columna.type, "get_col_spec"), (tabla.name, columna.name)
            assert columna.name not in SIN_GEOMETRIA, (tabla.name, columna.name)
    carga = modelos.SQLModel.metadata.tables["carga_gestor_campo"]
    assert "uq_carga_campo_base" in {preparador.format_constraint(c) for c in carga.constraints}
    trabajo = TrabajoOrquestacion.__table__
    assert "RUTEO_V2" in trabajo.c.tipo.type.enums
    assert trabajo.c.ejecucion_ruteo_v2_id.nullable is True
    llaves = {preparador.format_constraint(c) for c in trabajo.constraints}
    assert RESTRICCIONES_DE_LA_0014_EN_LA_COLA <= llaves


def test_la_0014_agrega_ruteo_v2_vacio_sin_tocar_nada_de_la_v012(base_en_0014):
    b = base_en_0014
    motor = b.motor

    # A. Cada fila de antes sigue ahi, identica; un trabajo solo gana su objetivo nuevo, vacio.
    assert _de_la_v012(motor) == b.de_la_v012
    despues = _por_columna(motor, PREVIAS_A_LA_0008)
    for tabla, filas in b.previas.items():
        assert len(despues[tabla]) == len(filas), tabla
        for antes, fila in zip(filas, despues[tabla], strict=True):
            fila = dict(fila)
            if tabla == "trabajo_orquestacion":
                assert fila.pop("ejecucion_ruteo_v2_id") is None
            assert fila == antes, tabla

    # B. Las tablas nuevas vacias, con sus restricciones, sus indices y sus triggers, por nombre; y
    # la restriccion unica que gana carga_gestor_campo.
    assert TABLAS_DE_LA_0014 <= set(_consultar(motor, TABLAS))
    for tabla in sorted(TABLAS_DE_LA_0014):
        assert _consultar(motor, f"SELECT count(*) FROM {tabla}") == [0]
    assert set(_consultar(motor, RESTRICCIONES_0014)) == RESTRICCIONES_DE_LA_0014
    with motor.connect() as conexion:
        indices = {
            (tabla, nombre, unico, tuple(columnas), predicado)
            for tabla, nombre, unico, columnas, predicado in conexion.execute(text(INDICES_0014))
        }
        triggers = {tuple(fila) for fila in conexion.execute(text(TRIGGERS))}
    assert indices == INDICES_DE_LA_0014
    assert TRIGGERS_DE_LA_0014 <= triggers
    assert FUNCIONES_DE_LA_0014 <= set(_consultar(motor, FUNCIONES))
    assert "uq_carga_campo_base" in _restricciones_de(motor, "carga_gestor_campo")
    assert RESTRICCIONES_DE_LA_0014_EN_LA_COLA <= _restricciones_de(motor, "trabajo_orquestacion")
    assert _consultar(motor, "SELECT version_num FROM alembic_version") == ["0014"]


def test_una_version_de_la_red_vial_dice_lo_que_publico(base_en_0014):
    b = base_en_0014
    motor = b.motor
    huella = "9" * 64
    _insertar(
        motor,
        INSERTAR_RED_VIAL,
        _red_vial(huella=huella),
        _red_vial("EN_PROCESO", huella=huella),
        _red_vial("FALLIDA", huella=huella),
        _red_vial("FALLIDA", huella=huella),
        _red_vial(motor="SINTETICO"),
    )

    for restriccion, fila in (
        # Una publicada y una en preparacion por huella; las fallidas se conservan todas.
        ("ux_red_vial_publicada", _red_vial(huella=huella)),
        ("ux_red_vial_en_proceso", _red_vial("EN_PROCESO", huella=huella)),
        ("ck_red_vial_hashes", _red_vial(huella="NO-ES-HEX")),
        ("ck_red_vial_hashes", _red_vial(sha256="B" * 64)),
        ("ck_red_vial_hashes", _red_vial(perfil="c" * 63)),
        ("ck_red_vial_hashes", _red_vial(manifest="D" * 64)),
        # Publicada es con su manifiesto, su hash y su instante; fallida, con su motivo.
        ("ck_red_vial_publicacion", _red_vial(manifest=None)),
        ("ck_red_vial_publicacion", _red_vial(manifiesto=None)),
        ("ck_red_vial_publicacion", _red_vial(publicada=None)),
        ("ck_red_vial_publicacion", _red_vial("EN_PROCESO", manifest="d" * 64)),
        ("ck_red_vial_publicacion", _red_vial("EN_PROCESO", detalle="algo")),
        ("ck_red_vial_publicacion", _red_vial("FALLIDA", detalle=None)),
        ("ck_red_vial_publicacion", _red_vial(motor="GOOGLE")),
        ("ck_version_red_vial_estado_version_red_vial", _red_vial("BORRADOR")),
    ):
        _rechaza(motor, restriccion, INSERTAR_RED_VIAL, fila)
    (red,) = _consultar(motor, "SELECT red_vial_version_id FROM version_red_vial LIMIT 1")
    _rechaza(motor, "uq_version_red_vial_red_vial_version_id", INSERTAR_RED_VIAL, _red_vial(id=red))


def test_una_red_solo_cambia_mientras_se_prepara_y_ninguna_se_borra(base_en_0014):
    b = base_en_0014
    motor = b.motor
    (red,) = _insertar(motor, INSERTAR_RED_VIAL, _red_vial("EN_PROCESO"))
    borrar = text("DELETE FROM version_red_vial WHERE id = :id")

    with pytest.raises(DBAPIError) as error, motor.begin() as conexion:
        conexion.execute(borrar, {"id": red})
    assert error.value.orig.diag.constraint_name == "tr_red_vial_terminada"
    with motor.begin() as conexion:
        conexion.execute(
            text(
                "UPDATE version_red_vial SET estado = 'PUBLICADA', hash_manifest = :h, "
                "manifiesto = CAST('{}' AS JSONB), publicada_en = now() WHERE id = :id"
            ),
            {"id": red, "h": "d" * 64},
        )

    for sentencia in (
        text("UPDATE version_red_vial SET region = 'Otra' WHERE id = :id"),
        text(
            "UPDATE version_red_vial SET estado = 'EN_PROCESO', hash_manifest = NULL, "
            "manifiesto = NULL, publicada_en = NULL WHERE id = :id"
        ),
        borrar,
    ):
        with pytest.raises(DBAPIError) as error, motor.begin() as conexion:
            conexion.execute(sentencia, {"id": red})
        assert error.value.orig.sqlstate == "23001"  # restrict_violation
        assert error.value.orig.diag.constraint_name == "tr_red_vial_terminada"
    assert _consultar(motor, f"SELECT estado FROM version_red_vial WHERE id = {red}") == [
        "PUBLICADA"
    ]


def test_una_ejecucion_exitosa_por_firma_y_una_en_proceso_por_firma(base_en_0014):
    b = base_en_0014
    motor = b.motor
    (red,) = _insertar(motor, INSERTAR_RED_VIAL, _red_vial())
    firma = "c" * 64
    _insertar(
        motor,
        INSERTAR_RUTEO_V2,
        _ruteo_0014(b, red, "EXITOSA", firma=firma),
        _ruteo_0014(b, red, firma=firma),
    )

    _rechaza(
        motor, "ux_ruteo_v2_exitosa", INSERTAR_RUTEO_V2, _ruteo_0014(b, red, "EXITOSA", firma=firma)
    )
    _rechaza(motor, "ux_ruteo_v2_en_proceso", INSERTAR_RUTEO_V2, _ruteo_0014(b, red, firma=firma))
    # Los intentos fallidos de la misma firma se conservan todos.
    _insertar(
        motor,
        INSERTAR_RUTEO_V2,
        _ruteo_0014(b, red, "FALLIDA", firma=firma),
        _ruteo_0014(b, red, "FALLIDA", firma=firma),
    )


def test_una_ejecucion_declara_su_plan_y_su_red_y_cuadra_lo_que_publico(base_en_0014):
    b = base_en_0014
    motor = b.motor
    (red,) = _insertar(motor, INSERTAR_RED_VIAL, _red_vial())

    for restriccion, cambios in (
        ("fk_ruteo_v2_campo", {"plan": 999_999}),
        ("fk_ruteo_v2_red", {"red": 999_999}),
        ("ck_ruteo_v2_firmas", {"firma": "NO-ES-HEX"}),
        ("ck_ruteo_v2_firmas", {"hash_politica": "a" * 63}),
        ("ck_ruteo_v2_resultado", {"resultado": "RUTAS_PUBLICADAS"}),
        ("ck_ruteo_v2_conteos", {"gestores": -1}),
        ("ck_ruteo_v2_conteos", {"sin_tiempo": -1}),
        # Mientras se resuelve, no ha publicado nada.
        ("ck_ruteo_v2_publicacion", {"programadas": 1}),
        ("ck_ruteo_v2_publicacion", {"distancia": 1}),
        ("ck_ruteo_v2_publicacion", {"rutas_parciales": 1}),
        ("ck_ejecucion_ruteo_v2_estado_ruteo_v2", {"estado": "RUTEADA", "resultado": "X"}),
    ):
        _rechaza(motor, restriccion, INSERTAR_RUTEO_V2, {**_ruteo_0014(b, red), **cambios})
    # Una EXITOSA dice su resultado y cuenta cada gestor en una ruta y cada parada en un estado.
    for restriccion, cambios in (
        ("ck_ruteo_v2_resultado", {"resultado": None}),
        ("ck_ruteo_v2_publicacion", {"gestores": 2}),
        ("ck_ruteo_v2_publicacion", {"rutas_parciales": 1}),
        ("ck_ruteo_v2_publicacion", {"paradas": 2}),
        ("ck_ruteo_v2_publicacion", {"sin_tiempo": 1}),
        ("ck_ruteo_v2_publicacion", {"gestores": 0, "rutas_completas": 0}),
    ):
        _rechaza(motor, restriccion, INSERTAR_RUTEO_V2, _ruteo_0014(b, red, "EXITOSA", **cambios))
    # Una ejecucion sin ninguna cuenta ASIGNADA tambien se publica: vacia.
    vacia = {"gestores": 0, "paradas": 0, "rutas_completas": 0, "programadas": 0}
    _insertar(
        motor,
        INSERTAR_RUTEO_V2,
        _ruteo_0014(b, red, "EXITOSA", **vacia, distancia=0, vial=0, servicio=0),
    )


def test_una_ruta_es_de_su_ejecucion_y_de_la_carga_de_su_gestor_en_el_plan(base_en_0014):
    b = base_en_0014
    motor = b.motor
    (red,) = _insertar(motor, INSERTAR_RED_VIAL, _red_vial())
    (ejecucion,) = _insertar(motor, INSERTAR_RUTEO_V2, _ruteo_0014(b, red))
    # Un gestor de la configuracion sin carga en el plan, otro plan con la carga del gestor, y otra
    # base de las mismas bases.
    (sin_carga,) = _insertar(
        motor,
        INSERTAR_GESTOR_CAMPO,
        _gestor_campo(b, b.configuracion, b.calendario, codigo="GESTOR_003"),
    )
    (otro_plan,) = _insertar(motor, INSERTAR_PLAN_CAMPO, _plan_0013(b.anterior, b.configuracion))
    _insertar(motor, INSERTAR_CARGA_CAMPO, _carga_campo(b.anterior, otro_plan, b.gestor))
    (otra_base,) = _insertar(motor, INSERTAR_BASE, _base(b.bases, "BASE_C"))

    def ruta(**cambios) -> dict:
        return {**_ruta_0014(b, ejecucion), **cambios}

    for restriccion, fila in (
        # La ruta es de la ejecucion y de su plan, y de la carga del gestor, con su base.
        ("fk_ruta_v2_ejecucion", ruta(ejecucion=999_999)),
        ("fk_ruta_v2_ejecucion", ruta(plan=otro_plan)),
        ("fk_ruta_v2_carga", ruta(gestor=sin_carga)),
        ("fk_ruta_v2_carga", ruta(base=otra_base)),
        ("ck_ruta_v2_hashes", ruta(hash_matriz="NO-ES-HEX")),
        ("ck_ruta_v2_hashes", ruta(hash_secuencia="F" * 64)),
        # Sus conteos cuadran con su estado.
        ("ck_ruta_v2_coherencia", ruta(estado_ruta="OPTIMA")),
        ("ck_ruta_v2_coherencia", ruta(paradas_campo=2)),
        ("ck_ruta_v2_coherencia", ruta(paradas_campo=0, **SIN_SALIDA)),
        ("ck_ruta_v2_coherencia", ruta(paradas_campo=2, sin_tiempo=1)),
        ("ck_ruta_v2_coherencia", ruta(estado_ruta="PARCIAL")),
        (
            "ck_ruta_v2_coherencia",
            ruta(estado_ruta="PARCIAL", paradas_campo=2, no_enrutables_base=1),
        ),
        ("ck_ruta_v2_coherencia", ruta(motivo_base="SIN_SNAP")),
        (
            "ck_ruta_v2_coherencia",
            ruta(estado_ruta="BASE_NO_ENRUTABLE", no_enrutables_base=1, **SIN_SALIDA),
        ),
        (
            "ck_ruta_v2_coherencia",
            ruta(
                estado_ruta="BASE_NO_ENRUTABLE",
                motivo_base="RED_DESCONECTADA",
                no_enrutables_base=1,
                **SIN_SALIDA,
            ),
        ),
        # Sale de la base si y solo si tiene una parada programada, y regresa con todo su regreso.
        ("ck_ruta_v2_coherencia", ruta(salida_base=None)),
        ("ck_ruta_v2_coherencia", ruta(distancia_regreso=None)),
        ("ck_ruta_v2_coherencia", ruta(espera_regreso=None)),
        # Sus horas van en orden, dentro de su jornada, y su duracion es la del reloj.
        ("ck_ruta_v2_coherencia", ruta(salida_base=NUEVE - timedelta(minutes=1))),
        ("ck_ruta_v2_coherencia", ruta(jornada_fin=NUEVE + timedelta(minutes=30))),
        ("ck_ruta_v2_coherencia", ruta(salida_regreso=NUEVE + timedelta(minutes=40))),
        ("ck_ruta_v2_coherencia", ruta(regreso_base=NUEVE + timedelta(minutes=36))),
        ("ck_ruta_v2_coherencia", ruta(total=2_101)),
        ("ck_ruta_v2_coherencia", ruta(espera=-1, total=2_099)),
        ("ck_ruta_v2_coherencia", ruta(distancia_total=-1)),
        ("ck_ruta_v2_coherencia", ruta(mejoras=-1)),
        ("ck_ruta_v2_coherencia", ruta(base_snap=Decimal("-0.01"))),
        # Sin jornada no hay ventanas; con jornada, las dos.
        ("ck_ruta_v2_coherencia", ruta(jornada_inicio=None)),
        ("ck_ruta_v2_coherencia", ruta(jornada_fin=None)),
        (
            "ck_ruta_v2_coherencia",
            ruta(estado_ruta="SIN_JORNADA", sin_tiempo=1, **SIN_SALIDA),
        ),
    ):
        _rechaza(motor, restriccion, INSERTAR_RUTA_V2, fila)
    # Una ruta COMPLETA y, del otro gestor, una cuya base no tiene snap: sin salida ni regreso.
    no_enrutable = {
        "gestor": b.otro_gestor,
        "estado_ruta": "BASE_NO_ENRUTABLE",
        "motivo_base": "SNAP_EXCESIVO",
        "no_enrutables_base": 1,
        "base_snap": Decimal("612.50"),
        **SIN_SALIDA,
    }
    ruta_id = uuid4()
    _insertar(motor, INSERTAR_RUTA_V2, ruta(ruta_id=ruta_id), ruta(**no_enrutable))
    _rechaza(motor, "pk_ruta_gestor_v2", INSERTAR_RUTA_V2, ruta())
    (otra,) = _insertar(motor, INSERTAR_RUTEO_V2, _ruteo_0014(b, red))
    _rechaza(
        motor, "uq_ruta_gestor_v2_ruta_id", INSERTAR_RUTA_V2, _ruta_0014(b, otra, ruta_id=ruta_id)
    )
    # Un gestor sin jornada ese dia tambien tiene su ruta: SIN_JORNADA, sin ventanas.
    _insertar(
        motor,
        INSERTAR_RUTA_V2,
        _ruta_0014(
            b,
            otra,
            estado_ruta="SIN_JORNADA",
            sin_tiempo=1,
            jornada_inicio=None,
            jornada_fin=None,
            **SIN_SALIDA,
        ),
    )


def test_una_parada_es_de_su_ruta_y_de_una_cuenta_de_su_plan_y_cuadra_con_su_estado(
    base_en_0014,
):
    b = base_en_0014
    motor = b.motor
    (red,) = _insertar(motor, INSERTAR_RED_VIAL, _red_vial())
    (ejecucion,) = _insertar(motor, INSERTAR_RUTEO_V2, _ruteo_0014(b, red))
    _insertar(
        motor,
        INSERTAR_RUTA_V2,
        _ruta_0014(b, ejecucion, estado_ruta="PARCIAL", paradas_campo=2, sin_tiempo=1),
    )

    def parada(**cambios) -> dict:
        return {**_parada_0014(b, ejecucion), **cambios}

    def sin_programar(estado: str, motivo: str | None, **cambios) -> dict:
        return parada(estado=estado, motivo=motivo, **{**SIN_PROGRAMAR, **cambios})

    for restriccion, fila in (
        # Es de una ruta de la ejecucion (la de su gestor) y de una cuenta del plan.
        ("fk_parada_v2_ruta", parada(gestor=b.otro_gestor)),
        ("fk_parada_v2_ruta", parada(ejecucion=999_999)),
        ("fk_parada_v2_asignacion", parada(cuenta=999_999)),
        # Su estado, su motivo y su prioridad, del vocabulario, y coherentes entre si.
        ("ck_parada_v2_coherencia", parada(estado="VISITADA")),
        ("ck_parada_v2_coherencia", parada(prioridad="URGENTE")),
        ("ck_parada_v2_coherencia", parada(motivo="JORNADA_AGOTADA")),
        ("ck_parada_v2_coherencia", sin_programar("SIN_TIEMPO", None)),
        ("ck_parada_v2_coherencia", sin_programar("SIN_TIEMPO", "SNAP_EXCESIVO")),
        ("ck_parada_v2_coherencia", sin_programar("NO_ENRUTABLE", "JORNADA_AGOTADA")),
        ("ck_parada_v2_coherencia", sin_programar("NO_ENRUTABLE_BASE", "SNAP_EXCESIVO")),
        # Programada es con orden, horas y tramo; si no, sin ninguno.
        ("ck_parada_v2_coherencia", parada(llegada=None)),
        ("ck_parada_v2_coherencia", parada(orden=None)),
        ("ck_parada_v2_coherencia", parada(espera=None)),
        ("ck_parada_v2_coherencia", sin_programar("SIN_TIEMPO", "JORNADA_AGOTADA", orden=1)),
        (
            "ck_parada_v2_coherencia",
            sin_programar("SIN_TIEMPO", "JORNADA_AGOTADA", llegada=NUEVE),
        ),
        # El orden empieza en 1, las horas van en orden y nada es negativo.
        ("ck_parada_v2_coherencia", parada(orden=0)),
        ("ck_parada_v2_coherencia", parada(llegada=NUEVE - timedelta(minutes=1))),
        ("ck_parada_v2_coherencia", parada(inicio=NUEVE + timedelta(minutes=5))),
        ("ck_parada_v2_coherencia", parada(fin=NUEVE + timedelta(minutes=9))),
        ("ck_parada_v2_coherencia", parada(distancia=-1)),
        ("ck_parada_v2_coherencia", parada(duracion=-1)),
        ("ck_parada_v2_coherencia", parada(espera=-1)),
        # Sin snap no hay distancia de snap; con snap excesivo si; programada, siempre.
        ("ck_parada_v2_coherencia", sin_programar("NO_ENRUTABLE", "SIN_SNAP", snap=Decimal(3))),
        ("ck_parada_v2_coherencia", sin_programar("NO_ENRUTABLE", "SNAP_EXCESIVO", snap=None)),
        ("ck_parada_v2_coherencia", parada(snap=None)),
        ("ck_parada_v2_coherencia", parada(snap=Decimal("-0.01"))),
    ):
        _rechaza(motor, restriccion, INSERTAR_PARADA_V2, fila)
    _insertar(
        motor,
        INSERTAR_PARADA_V2,
        parada(),
        sin_programar("SIN_TIEMPO", "PRIORIDAD_SUPERIOR_AGOTO_JORNADA", cuenta=b.otra_cuenta),
    )
    _rechaza(motor, "pk_parada_ruta_v2", INSERTAR_PARADA_V2, parada())
    # Dos paradas de una ruta no comparten su orden de visita.
    (otra,) = _insertar(motor, INSERTAR_RUTEO_V2, _ruteo_0014(b, red))
    _insertar(motor, INSERTAR_RUTA_V2, _ruta_0014(b, otra))
    _insertar(motor, INSERTAR_PARADA_V2, _parada_0014(b, otra))
    _rechaza(
        motor,
        "uq_parada_v2_orden",
        INSERTAR_PARADA_V2,
        _parada_0014(b, otra, cuenta=b.otra_cuenta),
    )
    _insertar(motor, INSERTAR_PARADA_V2, _parada_0014(b, otra, cuenta=b.otra_cuenta, orden=2))
    # Las que no se programaron no tienen orden: no chocan entre si.
    (tercera,) = _insertar(motor, INSERTAR_RUTEO_V2, _ruteo_0014(b, red))
    _insertar(
        motor,
        INSERTAR_RUTA_V2,
        _ruta_0014(
            b,
            tercera,
            paradas_campo=2,
            no_enrutables=2,
            estado_ruta="SIN_PARADAS_ENRUTABLES",
            **SIN_SALIDA,
        ),
    )
    _insertar(
        motor,
        INSERTAR_PARADA_V2,
        *(
            {
                **_parada_0014(b, tercera, cuenta=cuenta),
                **SIN_PROGRAMAR,
                "estado": "NO_ENRUTABLE",
                "motivo": motivo,
                "snap": snap,
            }
            for cuenta, motivo, snap in (
                (b.cuenta, "SIN_SNAP", None),
                (b.otra_cuenta, "RED_DESCONECTADA", Decimal("40.00")),
            )
        ),
    )


@pytest.mark.parametrize(("tabla", "trigger"), sorted(INMUTABLES_DE_LA_0014.items()))
def test_lo_que_publico_ruteo_v2_no_se_reescribe(base_en_0014, tabla, trigger):
    b = base_en_0014
    motor = b.motor
    _publicada_0014(b)
    (columna,) = _consultar(
        motor,
        "SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() "
        f"AND table_name = '{tabla}' ORDER BY ordinal_position LIMIT 1",
    )

    # Aunque no toque ninguna fila: es la sentencia la que no se permite.
    for sentencia in (
        f"UPDATE {tabla} SET {columna} = {columna}",
        f"DELETE FROM {tabla}",
        f"DELETE FROM {tabla} WHERE false",
    ):
        with pytest.raises(DBAPIError) as error, motor.begin() as conexion:
            conexion.execute(text(sentencia))
        assert error.value.orig.sqlstate == "23001", sentencia  # restrict_violation
        assert error.value.orig.diag.constraint_name == trigger
    assert _consultar(motor, f"SELECT count(*) FROM {tabla}") == [1]


def test_una_ejecucion_solo_cambia_mientras_esta_en_proceso_y_ninguna_se_borra(base_en_0014):
    b = base_en_0014
    motor = b.motor
    (red,) = _insertar(motor, INSERTAR_RED_VIAL, _red_vial())
    (ejecucion,) = _insertar(motor, INSERTAR_RUTEO_V2, _ruteo_0014(b, red))
    borrar = text("DELETE FROM ejecucion_ruteo_v2 WHERE id = :id")

    with pytest.raises(DBAPIError) as error, motor.begin() as conexion:
        conexion.execute(borrar, {"id": ejecucion})
    assert error.value.orig.diag.constraint_name == "tr_ruteo_v2_terminado"
    with motor.begin() as conexion:
        conexion.execute(
            text(
                "UPDATE ejecucion_ruteo_v2 SET estado = 'EXITOSA', resultado = 'RUTAS_PUBLICADAS', "
                "gestores_objetivo = 1, rutas_completas = 1, paradas_objetivo = 1, "
                "programadas = 1, terminada_en = now() WHERE id = :id"
            ),
            {"id": ejecucion},
        )

    for sentencia in (
        text("UPDATE ejecucion_ruteo_v2 SET detalle = 'otro' WHERE id = :id"),
        text(
            "UPDATE ejecucion_ruteo_v2 SET estado = 'EN_PROCESO', resultado = NULL, "
            "gestores_objetivo = 0, rutas_completas = 0, paradas_objetivo = 0, programadas = 0 "
            "WHERE id = :id"
        ),
        borrar,
    ):
        with pytest.raises(DBAPIError) as error, motor.begin() as conexion:
            conexion.execute(sentencia, {"id": ejecucion})
        assert error.value.orig.sqlstate == "23001"  # restrict_violation
        assert error.value.orig.diag.constraint_name == "tr_ruteo_v2_terminado"
    assert _consultar(motor, f"SELECT estado FROM ejecucion_ruteo_v2 WHERE id = {ejecucion}") == [
        "EXITOSA"
    ]


def test_el_trabajo_ruteo_v2_apunta_a_su_ejecucion_y_a_nada_mas(base_en_0014):
    b = base_en_0014
    motor = b.motor
    (red,) = _insertar(motor, INSERTAR_RED_VIAL, _red_vial())
    (ejecucion,) = _insertar(motor, INSERTAR_RUTEO_V2, _ruteo_0014(b, red))
    _insertar(motor, INSERTAR_TRABAJO_0014, _trabajo_0014(ruteo=ejecucion))

    for restriccion, trabajo in (
        ("uq_trabajo_ruteo_v2", _trabajo_0014(ruteo=ejecucion)),
        ("ck_trabajo_objetivo", _trabajo_0014()),
        ("ck_trabajo_objetivo", _trabajo_0014(ruteo=ejecucion, campo=b.plan)),
        ("ck_trabajo_objetivo", _trabajo_0014("CAMPO_V2", ruteo=ejecucion)),
        ("fk_trabajo_ruteo_v2", _trabajo_0014(ruteo=999_999)),
        ("ck_trabajo_orquestacion_tipo_trabajo", _trabajo_0014("RUTEO_V3")),
    ):
        _rechaza(motor, restriccion, INSERTAR_TRABAJO_0014, trabajo)


def test_la_0014_baja_cerrando_lo_que_estaba_en_curso_y_vuelve_a_subir(base_en_0014):
    b = base_en_0014
    motor = b.motor
    # Una ejecucion publicada con su ruta y su parada, otra en curso con su trabajo y una red en
    # preparacion.
    red, _ = _publicada_0014(b)
    (en_curso,) = _insertar(motor, INSERTAR_RUTEO_V2, _ruteo_0014(b, red))
    _insertar(motor, INSERTAR_TRABAJO_0014, _trabajo_0014(estado="EJECUTANDO", ruteo=en_curso))
    _insertar(motor, INSERTAR_RED_VIAL, _red_vial("EN_PROCESO"))
    previas = _por_columna(motor, PREVIAS_A_LA_0008)
    trabajos_antes = [
        {k: v for k, v in f.items() if k != "ejecucion_ruteo_v2_id"}
        for f in previas["trabajo_orquestacion"]
        if f["tipo"] != "RUTEO_V2"
    ]
    assert len(previas["trabajo_orquestacion"]) == len(trabajos_antes) + 1

    command.downgrade(_alembic(), "0013")

    # ruteo/v2 entero se fue, con sus redes, su trabajo y la restriccion que le agrego a
    # carga_gestor_campo. Lo de v0.12, intacto: campo/v2 y su configuracion, territorial/v2, la
    # geografia, PostGIS y todo lo anterior; los otros trabajos, sin su columna nueva.
    assert not TABLAS_DE_LA_0014 & set(_consultar(motor, TABLAS))
    assert not FUNCIONES_DE_LA_0014 & set(_consultar(motor, FUNCIONES))
    assert "uq_carga_campo_base" not in _restricciones_de(motor, "carga_gestor_campo")
    assert _consultar(motor, POSTGIS) == ["postgis"]
    assert _de_la_v012(motor) == b.de_la_v012
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
    assert not any("RUTEO_V2" in d for d in definiciones)
    assert all("CAMPO_V2" in d for d in definiciones)
    assert _consultar(motor, "SELECT version_num FROM alembic_version") == ["0013"]

    # Y vuelve a subir limpia: todo vacio, con sus restricciones.
    command.upgrade(_alembic(), "0014")

    for tabla in sorted(TABLAS_DE_LA_0014):
        assert _consultar(motor, f"SELECT count(*) FROM {tabla}") == [0]
    assert set(_consultar(motor, RESTRICCIONES_0014)) == RESTRICCIONES_DE_LA_0014
    assert FUNCIONES_DE_LA_0014 <= set(_consultar(motor, FUNCIONES))
    assert "uq_carga_campo_base" in _restricciones_de(motor, "carga_gestor_campo")
    assert _de_la_v012(motor) == b.de_la_v012
    assert _consultar(motor, "SELECT version_num FROM alembic_version") == ["0014"]


def test_al_bajar_la_0014_lo_primero_es_cerrar_lo_que_estaba_en_curso(base_en_0014):
    # Lo que la 0014 hace antes de quitar sus tablas, con sus mismas sentencias: las ejecuciones en
    # curso quedan FALLIDA, sin nada publicado, y sus trabajos FALLIDO, sin dueno ni lease, con el
    # motivo. Lo terminado no se toca.
    b = base_en_0014
    motor = b.motor
    modulo = _modulo_0014()
    red, publicada = _publicada_0014(b)
    (en_curso,) = _insertar(motor, INSERTAR_RUTEO_V2, _ruteo_0014(b, red))
    _insertar(motor, INSERTAR_TRABAJO_0014, _trabajo_0014(estado="EJECUTANDO", ruteo=en_curso))

    with motor.begin() as conexion:
        conexion.execute(modulo.CERRAR_EJECUCIONES)
        conexion.execute(modulo.CERRAR_TRABAJOS)

    with motor.connect() as conexion:
        ejecuciones = conexion.execute(
            text(
                "SELECT id, estado, resultado, detalle, programadas, terminada_en IS NOT NULL "
                "FROM ejecucion_ruteo_v2 ORDER BY id"
            )
        ).all()
        trabajos = conexion.execute(
            text(
                "SELECT tipo, estado, worker_id, lease_hasta, ultimo_error FROM "
                "trabajo_orquestacion WHERE tipo = 'RUTEO_V2' ORDER BY id"
            )
        ).all()
    assert [tuple(e) for e in ejecuciones] == [
        (publicada, "EXITOSA", "RUTAS_PUBLICADAS", None, 1, True),
        (en_curso, "FALLIDA", "CERRADA_AL_BAJAR", modulo.CIERRE, 0, True),
    ]
    assert [tuple(t) for t in trabajos] == [("RUTEO_V2", "FALLIDO", None, None, modulo.CIERRE)]


# --- la 0015: analytics/v1 ------------------------------------------------------------------------

TABLAS_DE_LA_0015 = {
    "ejecucion_analytics",
    "entrada_analytics",
    "agregado_analytics",
    "control_analytics",
}
_DE_LA_0015 = ", ".join(f"'{tabla}'" for tabla in sorted(TABLAS_DE_LA_0015))
RESTRICCIONES_0015 = RESTRICCIONES.replace("'ejecucion_decision', 'decision_cuenta'", _DE_LA_0015)
INDICES_0015 = INDICES.replace("'ejecucion_decision', 'decision_cuenta'", _DE_LA_0015)
RESTRICCIONES_DE_LA_0015 = {
    "pk_ejecucion_analytics",
    "uq_ejecucion_analytics_analytics_run_id",
    "fk_analytics_corte",
    "ck_analytics_horizonte",
    "ck_analytics_firmas",
    "ck_analytics_resultado",
    "ck_analytics_conteos",
    "ck_analytics_publicacion",
    "ck_ejecucion_analytics_modo_analytics",
    "ck_ejecucion_analytics_granularidad_analytics",
    "ck_ejecucion_analytics_estado_analytics",
    "pk_entrada_analytics",
    "fk_entrada_analytics_ejecucion",
    "fk_entrada_analytics_corte",
    "fk_entrada_analytics_motor_pagos",
    "fk_entrada_analytics_atribucion",
    "fk_entrada_analytics_evaluacion",
    "fk_entrada_analytics_decision_v2",
    "fk_entrada_analytics_geografia",
    "fk_entrada_analytics_territorial_v2",
    "fk_entrada_analytics_campo_v2",
    "fk_entrada_analytics_ruteo_v2",
    "ck_entrada_analytics_tipo",
    "ck_entrada_analytics_objetivo",
    "ck_entrada_analytics_firma",
    "pk_agregado_analytics",
    "fk_agregado_analytics_ejecucion",
    "ck_agregado_analytics_valor",
    "pk_control_analytics",
    "fk_control_analytics_ejecucion",
    "ck_control_analytics_estado",
}
RESTRICCIONES_DE_LA_0015_EN_LA_COLA = {"fk_trabajo_analytics", "uq_trabajo_analytics"}
_VENTANAS = "(ARRAY['MOTOR_PAGOS'::character varying, 'ATRIBUCION'::character varying])::text[]"
INDICES_DE_LA_0015 = {
    ("ejecucion_analytics", "pk_ejecucion_analytics", True, ("id",), None),
    (
        "ejecucion_analytics",
        "uq_ejecucion_analytics_analytics_run_id",
        True,
        ("analytics_run_id",),
        None,
    ),
    (
        "ejecucion_analytics",
        "ux_analytics_exitosa",
        True,
        ("firma_inputs",),
        _POR_ESTADO.format("EXITOSA"),
    ),
    (
        "ejecucion_analytics",
        "ux_analytics_en_proceso",
        True,
        ("firma_inputs",),
        _POR_ESTADO.format("EN_PROCESO"),
    ),
    (
        "ejecucion_analytics",
        "ix_analytics_corte",
        False,
        ("corte_canonico_id", "as_of", "id"),
        None,
    ),
    (
        "entrada_analytics",
        "pk_entrada_analytics",
        True,
        ("ejecucion_analytics_id", "tipo_entrada", "referencia_publica"),
        None,
    ),
    (
        "entrada_analytics",
        "ux_entrada_analytics_ventana",
        True,
        ("ejecucion_analytics_id", "tipo_entrada", "periodo_desde"),
        f"((tipo_entrada)::text = ANY ({_VENTANAS}))",
    ),
    (
        "entrada_analytics",
        "ux_entrada_analytics_unica",
        True,
        ("ejecucion_analytics_id", "tipo_entrada"),
        f"((tipo_entrada)::text <> ALL ({_VENTANAS}))",
    ),
    (
        "agregado_analytics",
        "pk_agregado_analytics",
        True,
        (
            "ejecucion_analytics_id",
            "metrica_codigo",
            "vista",
            "dimension_set",
            "periodo_desde",
            "dimension_hash",
        ),
        None,
    ),
    (
        "agregado_analytics",
        "ix_agregado_analytics_conjunto",
        False,
        ("ejecucion_analytics_id", "dimension_set", "vista", "periodo_desde"),
        None,
    ),
    (
        "control_analytics",
        "pk_control_analytics",
        True,
        ("ejecucion_analytics_id", "codigo_control"),
        None,
    ),
}
"""Las llaves, la unicidad y las paginas que la API lee de una ejecucion: ni un indice mas, y
ninguno GIN: los filtros de la API son por columnas, no por el contenido de las dimensiones."""
INMUTABLES_DE_LA_0015 = {
    "entrada_analytics": "tr_analytics_inmutable",
    "agregado_analytics": "tr_analytics_inmutable",
    "control_analytics": "tr_analytics_inmutable",
}
TRIGGERS_DE_LA_0015 = {
    *INMUTABLES_DE_LA_0015.items(),
    ("ejecucion_analytics", "tr_analytics_terminada"),
}
FUNCIONES_DE_LA_0015 = {"analytics_inmutable", "analytics_terminada"}
SIN_DATOS_DE_UNA_CUENTA = SIN_GEOMETRIA | {
    "cuenta_canonica_id",
    "cuenta_id",
    "cliente_unico",
    "actor_ref",
    "nombre",
    "direccion",
    "telefono",
    "microtramo_id",
    "tramo_id",
}
"""Ninguna tabla de analytics/v1 guarda algo de una cuenta: ni su identidad, ni quien la gestiono,
ni donde esta."""

HASH = "a" * 64
INSERTAR_ANALYTICS = text(
    "INSERT INTO ejecucion_analytics (analytics_run_id, corte_canonico_id, despacho_id, "
    "cartera_id, fecha_corte, as_of, known_at, modo, zona_horaria, granularidad, horizonte_dias, "
    "version_motor, version_catalogo, hash_catalogo, version_politica, hash_politica, "
    "hash_manifiesto, firma_inputs, estado, resultado, cuentas_cohorte, agregados_publicados, "
    "controles_totales, controles_fallidos, hash_agregados, disponibilidad, iniciada_en, "
    "terminada_en, detalle) VALUES (:run, :corte, 'DSP_001', 'CARTERA_PRINCIPAL', :fecha, :as_of, "
    "now(), :modo, 'America/Mexico_City', :granularidad, :horizonte, 'analytics/v1', "
    "'analytics-metrics/public-v1', :hash, 'analytics-public-v1', :hash, :hash, :firma, :estado, "
    ":resultado, :cuentas, :agregados, :totales, :fallidos, :hash_agregados, "
    "CAST(:disponibilidad AS JSONB), now(), :terminada, :detalle) RETURNING id"
)
INSERTAR_ENTRADA_ANALYTICS = text(
    "INSERT INTO entrada_analytics (ejecucion_analytics_id, tipo_entrada, referencia_publica, "
    "corte_canonico_id, ejecucion_motor_pagos_id, ejecucion_atribucion_id, "
    "ejecucion_evaluacion_promesas_id, ejecucion_decision_v2_id, ejecucion_geografia_id, "
    "ejecucion_territorial_v2_id, ejecucion_campo_v2_id, ejecucion_ruteo_v2_id, version, firma, "
    "periodo_desde, periodo_hasta, disponible_en, metadatos) VALUES (:ejecucion, :tipo, :ref, "
    ":corte, :motor, :atribucion, :evaluacion, :decision, :geografia, :territorial, :campo, "
    ":ruteo, 'v1', :firma, :desde, :hasta, now(), CAST('{}' AS JSONB)) "
    "RETURNING ejecucion_analytics_id"
)
INSERTAR_AGREGADO_ANALYTICS = text(
    "INSERT INTO agregado_analytics (ejecucion_analytics_id, metrica_codigo, vista, "
    "dimension_set, periodo_desde, dimension_hash, periodo_hasta, periodo_parcial, dimensiones, "
    "estado_valor, poblacion_cuentas, valor, numerador, denominador, motivos) VALUES "
    "(:ejecucion, :metrica, :vista, :conjunto, :desde, :hash_dims, :hasta, :parcial, "
    "CAST(:dimensiones AS JSONB), :estado, :poblacion, :valor, :numerador, :denominador, "
    "CAST(:motivos AS JSONB)) RETURNING ejecucion_analytics_id"
)
INSERTAR_CONTROL_ANALYTICS = text(
    "INSERT INTO control_analytics (ejecucion_analytics_id, codigo_control, estado, esperado, "
    "observado, diferencia, detalle) VALUES (:ejecucion, :codigo, :estado, :esperado, "
    ":observado, :diferencia, 'una comparacion') RETURNING ejecucion_analytics_id"
)
INSERTAR_TRABAJO_0015 = text(
    "INSERT INTO trabajo_orquestacion (trabajo_id, tipo, estado, ejecucion_analytics_id, "
    "ejecucion_ruteo_v2_id, intentos, max_intentos, creado_en, disponible_desde, worker_id, "
    "lease_hasta, tomado_en) VALUES (:trabajo_id, :tipo, :estado, :analytics, :ruteo, "
    ":intentos, 5, now(), now(), :worker_id, :lease_hasta, now()) RETURNING id"
)


def _modulo_0015():
    return ScriptDirectory.from_config(_alembic()).get_revision("0015").module


def _de_la_v013(motor: Engine) -> dict[str, list[dict]]:
    """Lo que la 0015 no puede tocar: todo lo de v0.12, ruteo/v2 y sus redes."""
    filas = _de_la_v012(motor)
    llaves = {
        "version_red_vial": "id",
        "ejecucion_ruteo_v2": "id",
        "ruta_gestor_v2": "ejecucion_ruteo_v2_id, gestor_campo_id",
        "parada_ruta_v2": "ejecucion_ruteo_v2_id, cuenta_canonica_id",
    }
    with motor.connect() as conexion:
        for tabla, orden in llaves.items():
            filas[tabla] = [
                dict(fila)
                for fila in conexion.execute(text(f"SELECT * FROM {tabla} ORDER BY {orden}"))
                .mappings()
                .all()
            ]
    return filas


@dataclass(frozen=True)
class BaseEn0015:
    """Una base de v0.13 con una ejecucion de ruteo/v2 publicada sobre su plan de campo/v2, subida
    a la 0015: analytics/v1 vacio."""

    motor: Engine
    corte: int
    fecha_corte: date
    decision: int
    geografia: int
    territorial: int
    plan: int
    ruteo: int
    previas: dict[str, list[dict]]
    de_la_v013: dict[str, list[dict]]


@pytest.fixture
def base_en_0015(base_en_0014) -> BaseEn0015:
    b = base_en_0014
    motor = b.motor
    _, ruteo = _publicada_0014(b)
    a = b.anterior
    (fecha_corte,) = _consultar(
        motor, f"SELECT fecha_corte FROM corte_canonico WHERE id = {a.corte}"
    )
    previas = _por_columna(motor, PREVIAS_A_LA_0008)
    de_la_v013 = _de_la_v013(motor)

    command.upgrade(_alembic(), "0015")
    return BaseEn0015(
        motor,
        a.corte,
        fecha_corte,
        a.decision,
        a.geografia,
        a.territorial,
        b.plan,
        ruteo,
        previas,
        de_la_v013,
    )


def _analytics_0015(b: BaseEn0015, estado: str = "EN_PROCESO", **cambios) -> dict:
    terminada = estado != "EN_PROCESO"
    exitosa = estado == "EXITOSA"
    return {
        "run": uuid4(),
        "corte": b.corte,
        "fecha": b.fecha_corte,
        "as_of": b.fecha_corte + timedelta(days=2),
        "modo": "BACKTEST",
        "granularidad": "TOTAL",
        "horizonte": 3,
        "hash": HASH,
        "firma": uuid4().hex * 2,
        "estado": estado,
        "resultado": "ANALYTICS_PUBLICADA" if exitosa else "ERROR_INTERNO" if terminada else None,
        "cuentas": 1,
        "agregados": 1 if exitosa else 0,
        "totales": 1 if exitosa else 0,
        "fallidos": 0,
        "hash_agregados": "e" * 64 if exitosa else None,
        "disponibilidad": "{}" if exitosa else None,
        "terminada": datetime.now(UTC) if terminada else None,
        "detalle": None,
        **cambios,
    }


def _entrada_0015(b: BaseEn0015, ejecucion: int, tipo: str = "CORTE", **cambios) -> dict:
    objetivo = {
        "CORTE": ("corte", b.corte),
        "DECISION_V2": ("decision", b.decision),
        "GEOGRAFIA": ("geografia", b.geografia),
        "TERRITORIAL_V2": ("territorial", b.territorial),
        "CAMPO_V2": ("campo", b.plan),
        "RUTEO_V2": ("ruteo", b.ruteo),
    }.get(tipo)
    valores = dict.fromkeys(
        (
            "corte",
            "motor",
            "atribucion",
            "evaluacion",
            "decision",
            "geografia",
            "territorial",
            "campo",
            "ruteo",
        )
    )
    if objetivo is not None:
        valores[objetivo[0]] = objetivo[1]
    return {
        "ejecucion": ejecucion,
        "tipo": tipo,
        "ref": uuid4(),
        **valores,
        "firma": HASH,
        "desde": None,
        "hasta": None,
        **cambios,
    }


def _agregado_0015(ejecucion: int, **cambios) -> dict:
    return {
        "ejecucion": ejecucion,
        "metrica": "cartera.cuentas",
        "vista": "ACUMULADA",
        "conjunto": "TOTAL",
        "desde": date(2026, 9, 30),
        "hash_dims": hashlib.sha256(b"{}").digest(),
        "hasta": date(2026, 10, 2),
        "parcial": False,
        "dimensiones": "{}",
        "estado": "DISPONIBLE",
        "poblacion": 20,
        "valor": Decimal(20),
        "numerador": None,
        "denominador": None,
        "motivos": "[]",
        **cambios,
    }


def _control_0015(ejecucion: int, **cambios) -> dict:
    return {
        "ejecucion": ejecucion,
        "codigo": "cohorte.cuentas",
        "estado": "OK",
        "esperado": Decimal(20),
        "observado": Decimal(20),
        "diferencia": Decimal(0),
        **cambios,
    }


def _trabajo_0015(tipo: str = "ANALYTICS", estado: str = "PENDIENTE", **cambios) -> dict:
    ejecutando = estado == "EJECUTANDO"
    return {
        "trabajo_id": uuid4(),
        "tipo": tipo,
        "estado": estado,
        "analytics": None,
        "ruteo": None,
        "intentos": 1 if ejecutando else 0,
        "worker_id": "worker-de-prueba:1:abc" if ejecutando else None,
        "lease_hasta": datetime.now(UTC) + timedelta(minutes=1) if ejecutando else None,
        **cambios,
    }


def _publicada_0015(b: BaseEn0015) -> int:
    """Una ejecucion EXITOSA con su entrada, su agregado y su control."""
    (ejecucion,) = _insertar(b.motor, INSERTAR_ANALYTICS, _analytics_0015(b, "EXITOSA"))
    _insertar(b.motor, INSERTAR_ENTRADA_ANALYTICS, _entrada_0015(b, ejecucion))
    _insertar(b.motor, INSERTAR_AGREGADO_ANALYTICS, _agregado_0015(ejecucion))
    _insertar(b.motor, INSERTAR_CONTROL_ANALYTICS, _control_0015(ejecucion))
    return ejecucion


def test_la_0015_sigue_a_la_0014():
    scripts = ScriptDirectory.from_config(_alembic())

    assert scripts.get_revision("0015").down_revision == "0014"


def test_los_modelos_declaran_el_esquema_de_la_0015():
    from motor_cartera.db import modelos

    tablas = [modelos.SQLModel.metadata.tables[t] for t in sorted(TABLAS_DE_LA_0015)]
    preparador = postgresql.dialect().identifier_preparer
    nombres = {preparador.format_constraint(c) for t in tablas for c in t.constraints}
    assert nombres == RESTRICCIONES_DE_LA_0015
    assert all(len(nombre) <= 63 for nombre in nombres)
    # Lo que crece con cada ejecucion no tiene id propio: su llave es la natural.
    for tabla, llave in (
        ("entrada_analytics", ["ejecucion_analytics_id", "tipo_entrada", "referencia_publica"]),
        (
            "agregado_analytics",
            [
                "ejecucion_analytics_id",
                "metrica_codigo",
                "vista",
                "dimension_set",
                "periodo_desde",
                "dimension_hash",
            ],
        ),
        ("control_analytics", ["ejecucion_analytics_id", "codigo_control"]),
    ):
        assert [c.name for c in modelos.SQLModel.metadata.tables[tabla].primary_key] == llave
        assert "id" not in modelos.SQLModel.metadata.tables[tabla].c
    # Nada de una cuenta: ni su identidad, ni quien la gestiono, ni una coordenada.
    for tabla in tablas:
        for columna in tabla.columns:
            assert not hasattr(columna.type, "get_col_spec"), (tabla.name, columna.name)
            assert columna.name not in SIN_DATOS_DE_UNA_CUENTA, (tabla.name, columna.name)
    # Un agregado no es una fila EAV ciega: su metrica, su vista, su conjunto, su periodo y sus
    # dimensiones son columnas, y su valor es NUMERIC, nunca un float.
    agregado = modelos.SQLModel.metadata.tables["agregado_analytics"]
    for columna in ("valor", "numerador", "denominador"):
        assert isinstance(agregado.c[columna].type, Numeric)
        assert not isinstance(agregado.c[columna].type, Float)
    trabajo = TrabajoOrquestacion.__table__
    assert "ANALYTICS" in trabajo.c.tipo.type.enums
    assert trabajo.c.ejecucion_analytics_id.nullable is True
    llaves = {preparador.format_constraint(c) for c in trabajo.constraints}
    assert RESTRICCIONES_DE_LA_0015_EN_LA_COLA <= llaves


def test_la_0015_agrega_analytics_vacio_sin_tocar_nada_de_la_v013(base_en_0015):
    b = base_en_0015
    motor = b.motor

    # A. Cada fila de antes sigue ahi, identica; un trabajo solo gana su objetivo nuevo, vacio.
    assert _de_la_v013(motor) == b.de_la_v013
    despues = _por_columna(motor, PREVIAS_A_LA_0008)
    for tabla, filas in b.previas.items():
        assert len(despues[tabla]) == len(filas), tabla
        for antes, fila in zip(filas, despues[tabla], strict=True):
            fila = dict(fila)
            if tabla == "trabajo_orquestacion":
                assert fila.pop("ejecucion_analytics_id") is None
            assert fila == antes, tabla

    # B. Las tablas nuevas vacias, con sus restricciones, sus indices y sus triggers, por nombre.
    assert TABLAS_DE_LA_0015 <= set(_consultar(motor, TABLAS))
    for tabla in sorted(TABLAS_DE_LA_0015):
        assert _consultar(motor, f"SELECT count(*) FROM {tabla}") == [0]
    assert set(_consultar(motor, RESTRICCIONES_0015)) == RESTRICCIONES_DE_LA_0015
    with motor.connect() as conexion:
        indices = {
            (tabla, nombre, unico, tuple(columnas), predicado)
            for tabla, nombre, unico, columnas, predicado in conexion.execute(text(INDICES_0015))
        }
        triggers = {tuple(fila) for fila in conexion.execute(text(TRIGGERS))}
        metodos = set(
            conexion.execute(
                text(
                    "SELECT DISTINCT am.amname FROM pg_index x JOIN pg_class i "
                    "ON i.oid = x.indexrelid JOIN pg_class t ON t.oid = x.indrelid "
                    f"JOIN pg_am am ON am.oid = i.relam WHERE t.relname IN ({_DE_LA_0015})"
                )
            ).scalars()
        )
    assert indices == INDICES_DE_LA_0015
    assert metodos == {"btree"}
    assert TRIGGERS_DE_LA_0015 <= triggers
    assert FUNCIONES_DE_LA_0015 <= set(_consultar(motor, FUNCIONES))
    assert RESTRICCIONES_DE_LA_0015_EN_LA_COLA <= _restricciones_de(motor, "trabajo_orquestacion")
    assert _consultar(motor, "SELECT version_num FROM alembic_version") == ["0015"]


def test_un_analisis_exitoso_por_firma_y_uno_en_proceso_por_firma(base_en_0015):
    b = base_en_0015
    motor = b.motor
    firma = "c" * 64
    _insertar(
        motor,
        INSERTAR_ANALYTICS,
        _analytics_0015(b, "EXITOSA", firma=firma),
        _analytics_0015(b, firma=firma),
    )

    _rechaza(
        motor,
        "ux_analytics_exitosa",
        INSERTAR_ANALYTICS,
        _analytics_0015(b, "EXITOSA", firma=firma),
    )
    _rechaza(motor, "ux_analytics_en_proceso", INSERTAR_ANALYTICS, _analytics_0015(b, firma=firma))
    # Los intentos fallidos de la misma firma se conservan todos.
    _insertar(
        motor,
        INSERTAR_ANALYTICS,
        _analytics_0015(b, "FALLIDA", firma=firma),
        _analytics_0015(b, "FALLIDA", firma=firma),
    )


def test_una_ejecucion_declara_su_corte_y_su_horizonte_y_cuadra_lo_que_publico(base_en_0015):
    b = base_en_0015
    motor = b.motor

    for restriccion, cambios in (
        ("fk_analytics_corte", {"corte": 999_999}),
        ("fk_analytics_corte", {"fecha": b.fecha_corte - timedelta(days=1), "horizonte": 4}),
        ("ck_analytics_horizonte", {"horizonte": 2}),
        ("ck_analytics_horizonte", {"as_of": b.fecha_corte - timedelta(days=1), "horizonte": 0}),
        ("ck_analytics_firmas", {"firma": "NO-ES-HEX"}),
        ("ck_analytics_firmas", {"hash": "a" * 63}),
        ("ck_analytics_resultado", {"resultado": "ANALYTICS_PUBLICADA"}),
        ("ck_analytics_conteos", {"cuentas": -1}),
        ("ck_analytics_conteos", {"fallidos": 1}),
        # Mientras se resuelve, no ha publicado nada.
        ("ck_analytics_publicacion", {"agregados": 1}),
        ("ck_analytics_publicacion", {"hash_agregados": "e" * 64}),
        ("ck_ejecucion_analytics_modo_analytics", {"modo": "PREDICTIVO"}),
        ("ck_ejecucion_analytics_granularidad_analytics", {"granularidad": "HORA"}),
        ("ck_ejecucion_analytics_estado_analytics", {"estado": "PUBLICADA", "resultado": "X"}),
    ):
        _rechaza(motor, restriccion, INSERTAR_ANALYTICS, {**_analytics_0015(b), **cambios})
    # Una EXITOSA dice su resultado, publico algo, cuadro cada control y firmo lo publicado.
    for restriccion, cambios in (
        ("ck_analytics_resultado", {"resultado": None}),
        ("ck_analytics_publicacion", {"agregados": 0}),
        ("ck_analytics_publicacion", {"hash_agregados": None}),
        ("ck_analytics_publicacion", {"fallidos": 1}),
        ("ck_analytics_publicacion", {"totales": 0}),
        ("ck_analytics_publicacion", {"cuentas": 0}),
        ("ck_analytics_publicacion", {"disponibilidad": None}),
    ):
        _rechaza(motor, restriccion, INSERTAR_ANALYTICS, _analytics_0015(b, "EXITOSA", **cambios))
    # Una FALLIDA guarda sus controles, incluidos los que fallaron, y nada publicado.
    _insertar(motor, INSERTAR_ANALYTICS, _analytics_0015(b, "FALLIDA", totales=12, fallidos=1))


def test_una_entrada_apunta_a_su_tipo_de_ejecucion_y_una_ventana_aporta_una(base_en_0015):
    b = base_en_0015
    motor = b.motor
    (ejecucion,) = _insertar(motor, INSERTAR_ANALYTICS, _analytics_0015(b))
    # Otra ejecucion, sin entradas, para probar la llave foranea del corte sin chocar con su unica.
    (otra,) = _insertar(motor, INSERTAR_ANALYTICS, _analytics_0015(b))
    # Otra firma que la interpretacion EXITOSA que ya dejo la base de la 0010 en la misma ventana.
    (pagos,) = _insertar(
        motor,
        INSERTAR_MOTOR,
        _motor("EXITOSA", resultado="INTERPRETACION_PUBLICADA", firma="e" * 64),
    )
    ventana = {"motor": pagos, "desde": date(2026, 9, 1), "hasta": date(2026, 9, 30)}
    _insertar(
        motor,
        INSERTAR_ENTRADA_ANALYTICS,
        *(_entrada_0015(b, ejecucion, t) for t in ("CORTE", "DECISION_V2", "GEOGRAFIA")),
        *(_entrada_0015(b, ejecucion, t) for t in ("TERRITORIAL_V2", "CAMPO_V2", "RUTEO_V2")),
        _entrada_0015(b, ejecucion, "MOTOR_PAGOS", **ventana),
    )

    for restriccion, entrada in (
        ("ck_entrada_analytics_tipo", _entrada_0015(b, ejecucion, "PRONOSTICO")),
        # Con su ventana, para que la firma cumpla: PostgreSQL revisa los CHECK por nombre.
        (
            "ck_entrada_analytics_objetivo",
            _entrada_0015(
                b, ejecucion, "MOTOR_PAGOS", desde=date(2026, 9, 1), hasta=date(2026, 9, 30)
            ),
        ),
        (
            "ck_entrada_analytics_objetivo",
            _entrada_0015(b, ejecucion, "DECISION_V2", corte=b.corte),
        ),
        ("ck_entrada_analytics_firma", _entrada_0015(b, ejecucion, firma="NO-ES-HEX")),
        (
            "ck_entrada_analytics_firma",
            _entrada_0015(b, ejecucion, "MOTOR_PAGOS", motor=pagos),
        ),
        (
            "ck_entrada_analytics_firma",
            _entrada_0015(b, ejecucion, desde=date(2026, 10, 1), hasta=date(2026, 9, 1)),
        ),
        # Un corte, una decision, ... por ejecucion; una interpretacion por ventana.
        ("ux_entrada_analytics_unica", _entrada_0015(b, ejecucion)),
        ("ux_entrada_analytics_ventana", _entrada_0015(b, ejecucion, "MOTOR_PAGOS", **ventana)),
        ("fk_entrada_analytics_ejecucion", _entrada_0015(b, 999_999)),
        ("fk_entrada_analytics_corte", _entrada_0015(b, otra, corte=999_999)),
    ):
        _rechaza(motor, restriccion, INSERTAR_ENTRADA_ANALYTICS, entrada)
    # Otra ventana si.
    _insertar(
        motor,
        INSERTAR_ENTRADA_ANALYTICS,
        _entrada_0015(
            b,
            ejecucion,
            "MOTOR_PAGOS",
            motor=pagos,
            desde=date(2026, 10, 1),
            hasta=date(2026, 10, 31),
        ),
    )


def test_un_agregado_es_coherente_con_su_estado_de_valor(base_en_0015):
    b = base_en_0015
    motor = b.motor
    (ejecucion,) = _insertar(motor, INSERTAR_ANALYTICS, _analytics_0015(b))
    dims = {"dimensiones": '{"producto": "CONSUMO"}', "conjunto": "PRODUCTO"}
    razon = {"metrica": "x.razon", "valor": Decimal("0.5"), "numerador": 1, "denominador": 2}
    _insertar(
        motor,
        INSERTAR_AGREGADO_ANALYTICS,
        _agregado_0015(ejecucion),
        _agregado_0015(ejecucion, **razon),
        # Denominador cero: sin valor, con su numerador y su denominador.
        _agregado_0015(ejecucion, metrica="x.cero", valor=None, numerador=0, denominador=0),
        _agregado_0015(
            ejecucion,
            metrica="x.suprimida",
            estado="SUPRIMIDO",
            poblacion=None,
            valor=None,
            motivos='[{"codigo": "CELDA_MENOR_AL_MINIMO"}]',
            hash_dims=hashlib.sha256(b"producto").digest(),
            **dims,
        ),
        _agregado_0015(ejecucion, metrica="x.no_disponible", estado="NO_DISPONIBLE", valor=None),
        _agregado_0015(
            ejecucion,
            metrica="x.no_aplica",
            vista="PERIODO",
            estado="NO_APLICA",
            valor=None,
            poblacion=None,
            parcial=True,
        ),
    )

    for cambios in (
        {"estado": "ESTIMADO"},
        {"vista": "PREDICCION"},
        # Fuera de DISPONIBLE no hay ningun valor.
        {"estado": "NO_DISPONIBLE"},
        {"estado": "SUPRIMIDO", "poblacion": None},
        {"estado": "NO_APLICA", "valor": None, "numerador": 1, "denominador": 2},
        # Una celda suprimida no dice cuantas cuentas tiene.
        {"estado": "SUPRIMIDO", "valor": None},
        # Una razon tiene los dos, y un denominador cero no tiene valor.
        {"numerador": 1},
        {"valor": Decimal(0), "numerador": 0, "denominador": 0},
        {"valor": None, "numerador": 1, "denominador": 2},
        {"poblacion": -1},
        {"hasta": date(2026, 9, 29)},
        {"parcial": True},
        {"hash_dims": b"corto"},
        {"dimensiones": '{"producto": "CONSUMO"}'},
    ):
        _rechaza(
            motor,
            "ck_agregado_analytics_valor",
            INSERTAR_AGREGADO_ANALYTICS,
            _agregado_0015(ejecucion, metrica="x.otra", **cambios),
        )
    _rechaza(motor, "pk_agregado_analytics", INSERTAR_AGREGADO_ANALYTICS, _agregado_0015(ejecucion))
    _rechaza(
        motor,
        "fk_agregado_analytics_ejecucion",
        INSERTAR_AGREGADO_ANALYTICS,
        _agregado_0015(999_999),
    )


def test_un_control_cuadra_su_diferencia_con_su_estado(base_en_0015):
    b = base_en_0015
    motor = b.motor
    (ejecucion,) = _insertar(motor, INSERTAR_ANALYTICS, _analytics_0015(b))
    _insertar(
        motor,
        INSERTAR_CONTROL_ANALYTICS,
        _control_0015(ejecucion),
        _control_0015(
            ejecucion,
            codigo="pagos.reversos_coherentes",
            estado="FALLO",
            esperado=Decimal(0),
            observado=Decimal(1),
            diferencia=Decimal(1),
        ),
    )

    for cambios in (
        {"estado": "AVISO"},
        {"estado": "FALLO"},
        {"diferencia": Decimal(1)},
        {"observado": Decimal(21)},
        {"esperado": None},
    ):
        _rechaza(
            motor,
            "ck_control_analytics_estado",
            INSERTAR_CONTROL_ANALYTICS,
            _control_0015(ejecucion, codigo="otro", **cambios),
        )
    _rechaza(motor, "pk_control_analytics", INSERTAR_CONTROL_ANALYTICS, _control_0015(ejecucion))


@pytest.mark.parametrize(("tabla", "trigger"), sorted(INMUTABLES_DE_LA_0015.items()))
def test_lo_que_publico_analytics_no_se_reescribe(base_en_0015, tabla, trigger):
    b = base_en_0015
    motor = b.motor
    _publicada_0015(b)
    (columna,) = _consultar(
        motor,
        "SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() "
        f"AND table_name = '{tabla}' ORDER BY ordinal_position LIMIT 1",
    )

    # Aunque no toque ninguna fila: es la sentencia la que no se permite.
    for sentencia in (
        f"UPDATE {tabla} SET {columna} = {columna}",
        f"DELETE FROM {tabla}",
        f"DELETE FROM {tabla} WHERE false",
    ):
        with pytest.raises(DBAPIError) as error, motor.begin() as conexion:
            conexion.execute(text(sentencia))
        assert error.value.orig.sqlstate == "23001", sentencia  # restrict_violation
        assert error.value.orig.diag.constraint_name == trigger
    assert _consultar(motor, f"SELECT count(*) FROM {tabla}") == [1]


def test_un_analisis_solo_cambia_mientras_esta_en_proceso_y_ninguno_se_borra(base_en_0015):
    b = base_en_0015
    motor = b.motor
    (ejecucion,) = _insertar(motor, INSERTAR_ANALYTICS, _analytics_0015(b))
    borrar = text("DELETE FROM ejecucion_analytics WHERE id = :id")

    with pytest.raises(DBAPIError) as error, motor.begin() as conexion:
        conexion.execute(borrar, {"id": ejecucion})
    assert error.value.orig.diag.constraint_name == "tr_analytics_terminada"
    with motor.begin() as conexion:
        conexion.execute(
            text(
                "UPDATE ejecucion_analytics SET estado = 'EXITOSA', "
                "resultado = 'ANALYTICS_PUBLICADA', agregados_publicados = 1, "
                "controles_totales = 1, hash_agregados = :hash, "
                "disponibilidad = CAST('{}' AS JSONB), terminada_en = now() WHERE id = :id"
            ),
            {"id": ejecucion, "hash": "e" * 64},
        )

    for sentencia in (
        text("UPDATE ejecucion_analytics SET detalle = 'otro' WHERE id = :id"),
        text(
            "UPDATE ejecucion_analytics SET estado = 'EN_PROCESO', resultado = NULL, "
            "agregados_publicados = 0, controles_totales = 0, hash_agregados = NULL WHERE id = :id"
        ),
        borrar,
    ):
        with pytest.raises(DBAPIError) as error, motor.begin() as conexion:
            conexion.execute(sentencia, {"id": ejecucion})
        assert error.value.orig.sqlstate == "23001"  # restrict_violation
        assert error.value.orig.diag.constraint_name == "tr_analytics_terminada"
    assert _consultar(motor, f"SELECT estado FROM ejecucion_analytics WHERE id = {ejecucion}") == [
        "EXITOSA"
    ]


def test_el_trabajo_analytics_apunta_a_su_ejecucion_y_a_nada_mas(base_en_0015):
    b = base_en_0015
    motor = b.motor
    (ejecucion,) = _insertar(motor, INSERTAR_ANALYTICS, _analytics_0015(b))
    _insertar(motor, INSERTAR_TRABAJO_0015, _trabajo_0015(analytics=ejecucion))

    for restriccion, trabajo in (
        ("uq_trabajo_analytics", _trabajo_0015(analytics=ejecucion)),
        ("ck_trabajo_objetivo", _trabajo_0015()),
        ("ck_trabajo_objetivo", _trabajo_0015(analytics=ejecucion, ruteo=b.ruteo)),
        ("ck_trabajo_objetivo", _trabajo_0015("RUTEO_V2", analytics=ejecucion)),
        ("fk_trabajo_analytics", _trabajo_0015(analytics=999_999)),
        ("ck_trabajo_orquestacion_tipo_trabajo", _trabajo_0015("ANALYTICS_V2")),
    ):
        _rechaza(motor, restriccion, INSERTAR_TRABAJO_0015, trabajo)


def test_la_0015_baja_cerrando_lo_que_estaba_en_curso_y_vuelve_a_subir(base_en_0015):
    b = base_en_0015
    motor = b.motor
    # Una ejecucion publicada con su entrada, su agregado y su control; otra en curso con su
    # trabajo.
    _publicada_0015(b)
    (en_curso,) = _insertar(motor, INSERTAR_ANALYTICS, _analytics_0015(b))
    _insertar(motor, INSERTAR_TRABAJO_0015, _trabajo_0015(estado="EJECUTANDO", analytics=en_curso))
    previas = _por_columna(motor, PREVIAS_A_LA_0008)
    trabajos_antes = [
        {k: v for k, v in f.items() if k != "ejecucion_analytics_id"}
        for f in previas["trabajo_orquestacion"]
        if f["tipo"] != "ANALYTICS"
    ]
    assert len(previas["trabajo_orquestacion"]) == len(trabajos_antes) + 1

    command.downgrade(_alembic(), "0014")

    # analytics/v1 entero se fue, con su trabajo. Lo de v0.13, intacto: ruteo/v2 y sus redes,
    # campo/v2, territorial/v2, la geografia, PostGIS y todo lo anterior; los otros trabajos, sin
    # su columna nueva.
    assert not TABLAS_DE_LA_0015 & set(_consultar(motor, TABLAS))
    assert not FUNCIONES_DE_LA_0015 & set(_consultar(motor, FUNCIONES))
    assert _consultar(motor, POSTGIS) == ["postgis"]
    assert _de_la_v013(motor) == b.de_la_v013
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
    assert not any("ANALYTICS" in d for d in definiciones)
    assert all("RUTEO_V2" in d for d in definiciones)
    assert _consultar(motor, "SELECT version_num FROM alembic_version") == ["0014"]

    # Y vuelve a subir limpia: todo vacio, con sus restricciones.
    command.upgrade(_alembic(), "0015")

    for tabla in sorted(TABLAS_DE_LA_0015):
        assert _consultar(motor, f"SELECT count(*) FROM {tabla}") == [0]
    assert set(_consultar(motor, RESTRICCIONES_0015)) == RESTRICCIONES_DE_LA_0015
    assert FUNCIONES_DE_LA_0015 <= set(_consultar(motor, FUNCIONES))
    assert RESTRICCIONES_DE_LA_0015_EN_LA_COLA <= _restricciones_de(motor, "trabajo_orquestacion")
    assert _de_la_v013(motor) == b.de_la_v013
    assert _consultar(motor, "SELECT version_num FROM alembic_version") == ["0015"]


def test_al_bajar_la_0015_lo_primero_es_cerrar_lo_que_estaba_en_curso(base_en_0015):
    # Lo que la 0015 hace antes de quitar sus tablas, con sus mismas sentencias: las ejecuciones en
    # curso quedan FALLIDA, sin nada publicado, y sus trabajos FALLIDO, sin dueno ni lease, con el
    # motivo. Lo terminado no se toca.
    b = base_en_0015
    motor = b.motor
    modulo = _modulo_0015()
    publicada = _publicada_0015(b)
    (en_curso,) = _insertar(motor, INSERTAR_ANALYTICS, _analytics_0015(b))
    _insertar(motor, INSERTAR_TRABAJO_0015, _trabajo_0015(estado="EJECUTANDO", analytics=en_curso))

    with motor.begin() as conexion:
        conexion.execute(modulo.CERRAR_EJECUCIONES)
        conexion.execute(modulo.CERRAR_TRABAJOS)

    with motor.connect() as conexion:
        ejecuciones = conexion.execute(
            text(
                "SELECT id, estado, resultado, detalle, agregados_publicados, "
                "terminada_en IS NOT NULL FROM ejecucion_analytics ORDER BY id"
            )
        ).all()
        trabajos = conexion.execute(
            text(
                "SELECT tipo, estado, worker_id, lease_hasta, ultimo_error FROM "
                "trabajo_orquestacion WHERE tipo = 'ANALYTICS' ORDER BY id"
            )
        ).all()
    assert [tuple(e) for e in ejecuciones] == [
        (publicada, "EXITOSA", "ANALYTICS_PUBLICADA", None, 1, True),
        (en_curso, "FALLIDA", "CERRADA_AL_BAJAR", modulo.CIERRE, 0, True),
    ]
    assert [tuple(t) for t in trabajos] == [("ANALYTICS", "FALLIDO", None, None, modulo.CIERRE)]


# --- la 0016: predictive/model-v1 ----------------------------------------------------------------

TABLAS_DE_LA_0016 = {
    "ejecucion_entrenamiento_predictivo",
    "ancla_entrenamiento_predictivo",
    "interpretacion_entrenamiento_predictivo",
    "version_modelo_predictivo",
    "componente_modelo_predictivo",
    "ejecucion_scoring_predictivo",
    "prediccion_cuenta",
    "drift_predictivo",
}
_DE_LA_0016 = ", ".join(f"'{tabla}'" for tabla in sorted(TABLAS_DE_LA_0016))
RESTRICCIONES_0016 = RESTRICCIONES.replace("'ejecucion_decision', 'decision_cuenta'", _DE_LA_0016)
INDICES_0016 = INDICES.replace("'ejecucion_decision', 'decision_cuenta'", _DE_LA_0016)
RESTRICCIONES_DE_LA_0016 = {
    "pk_ejecucion_entrenamiento_predictivo",
    "uq_ejecucion_entrenamiento_predictivo_training_run_id",
    "ck_entrenamiento_predictivo_firmas",
    "ck_entrenamiento_predictivo_conteos",
    "ck_entrenamiento_predictivo_publicacion",
    "ck_ejecucion_entrenamiento_predictivo_estado_entrenamiento",
    "pk_ancla_entrenamiento_predictivo",
    "fk_ancla_predictiva_entrenamiento",
    "fk_ancla_predictiva_corte",
    "ck_ancla_predictiva_split",
    "ck_ancla_predictiva_relojes",
    "pk_interpretacion_entrenamiento_predictivo",
    "fk_interpretacion_predictiva_entrenamiento",
    "fk_interpretacion_predictiva_motor_pagos",
    "uq_interpretacion_predictiva_ejecucion",
    "pk_version_modelo_predictivo",
    "uq_version_modelo_predictivo_modelo_version_id",
    "fk_modelo_predictivo_entrenamiento",
    "uq_modelo_predictivo_entrenamiento",
    "ck_modelo_predictivo_firmas",
    "ck_modelo_predictivo_publicada",
    "ck_modelo_predictivo_periodos",
    "ck_version_modelo_predictivo_estado_modelo_predictivo",
    "pk_componente_modelo_predictivo",
    "fk_componente_predictivo_modelo",
    "ck_componente_predictivo_coherencia",
    "pk_ejecucion_scoring_predictivo",
    "uq_ejecucion_scoring_predictivo_scoring_run_id",
    "fk_scoring_predictivo_modelo",
    "fk_scoring_predictivo_corte",
    "uq_scoring_predictivo_id_corte",
    "ck_scoring_predictivo_firmas",
    "ck_scoring_predictivo_resultado",
    "ck_scoring_predictivo_publicacion",
    "ck_ejecucion_scoring_predictivo_modo_scoring_predictivo",
    "ck_ejecucion_scoring_predictivo_estado_scoring_predictivo",
    "pk_prediccion_cuenta",
    "fk_prediccion_scoring",
    "fk_prediccion_snapshot",
    "ck_prediccion_cuenta_rangos",
    "ck_prediccion_cuenta_estado",
    "pk_drift_predictivo",
    "fk_drift_scoring",
    "ck_drift_predictivo_valores",
}
RESTRICCIONES_DE_LA_0016_EN_LA_COLA = {
    "fk_trabajo_entrenamiento_predictivo",
    "uq_trabajo_entrenamiento_predictivo",
    "fk_trabajo_scoring_predictivo",
    "uq_trabajo_scoring_predictivo",
}
_EN_PROCESO = "((estado)::text = 'EN_PROCESO'::text)"
_EXITOSA = "((estado)::text = 'EXITOSA'::text)"
INDICES_DE_LA_0016 = {
    (
        "ancla_entrenamiento_predictivo",
        "pk_ancla_entrenamiento_predictivo",
        True,
        ("ejecucion_entrenamiento_predictivo_id", "corte_canonico_id"),
        None,
    ),
    (
        "componente_modelo_predictivo",
        "pk_componente_modelo_predictivo",
        True,
        ("version_modelo_predictivo_id", "target"),
        None,
    ),
    (
        "drift_predictivo",
        "pk_drift_predictivo",
        True,
        ("ejecucion_scoring_predictivo_id", "tipo", "nombre"),
        None,
    ),
    (
        "ejecucion_entrenamiento_predictivo",
        "ix_entrenamiento_predictivo_estado",
        False,
        ("estado", "id"),
        None,
    ),
    (
        "ejecucion_entrenamiento_predictivo",
        "pk_ejecucion_entrenamiento_predictivo",
        True,
        ("id",),
        None,
    ),
    (
        "ejecucion_entrenamiento_predictivo",
        "uq_ejecucion_entrenamiento_predictivo_training_run_id",
        True,
        ("training_run_id",),
        None,
    ),
    (
        "ejecucion_entrenamiento_predictivo",
        "ux_entrenamiento_predictivo_en_proceso",
        True,
        ("firma_inputs",),
        _EN_PROCESO,
    ),
    (
        "ejecucion_entrenamiento_predictivo",
        "ux_entrenamiento_predictivo_exitosa",
        True,
        ("firma_inputs",),
        _EXITOSA,
    ),
    (
        "ejecucion_scoring_predictivo",
        "ix_scoring_predictivo_corte",
        False,
        ("corte_canonico_id", "id"),
        None,
    ),
    ("ejecucion_scoring_predictivo", "ix_scoring_predictivo_estado", False, ("estado", "id"), None),
    (
        "ejecucion_scoring_predictivo",
        "ix_scoring_predictivo_modelo",
        False,
        ("version_modelo_predictivo_id", "id"),
        None,
    ),
    ("ejecucion_scoring_predictivo", "pk_ejecucion_scoring_predictivo", True, ("id",), None),
    (
        "ejecucion_scoring_predictivo",
        "uq_ejecucion_scoring_predictivo_scoring_run_id",
        True,
        ("scoring_run_id",),
        None,
    ),
    (
        "ejecucion_scoring_predictivo",
        "uq_scoring_predictivo_id_corte",
        True,
        ("id", "corte_canonico_id"),
        None,
    ),
    (
        "ejecucion_scoring_predictivo",
        "ux_scoring_predictivo_en_proceso",
        True,
        ("firma_inputs",),
        _EN_PROCESO,
    ),
    (
        "ejecucion_scoring_predictivo",
        "ux_scoring_predictivo_exitosa",
        True,
        ("firma_inputs",),
        _EXITOSA,
    ),
    (
        "interpretacion_entrenamiento_predictivo",
        "pk_interpretacion_entrenamiento_predictivo",
        True,
        ("ejecucion_entrenamiento_predictivo_id", "periodo_desde"),
        None,
    ),
    (
        "interpretacion_entrenamiento_predictivo",
        "uq_interpretacion_predictiva_ejecucion",
        True,
        ("ejecucion_entrenamiento_predictivo_id", "ejecucion_motor_pagos_id"),
        None,
    ),
    *(
        (
            "prediccion_cuenta",
            f"ix_prediccion_{nombre}",
            False,
            ("ejecucion_scoring_predictivo_id", columna, "cuenta_canonica_id"),
            None,
        )
        for nombre, columna in (
            ("recuperacion", "p_recuperacion_positiva_30d"),
            ("contacto", "p_contacto_14d"),
            ("promesa", "p_promesa_30d"),
            ("esperada", "recuperacion_positiva_esperada_30d"),
        )
    ),
    (
        "prediccion_cuenta",
        "ix_prediccion_cuenta",
        False,
        ("cuenta_canonica_id", "ejecucion_scoring_predictivo_id"),
        None,
    ),
    (
        "prediccion_cuenta",
        "pk_prediccion_cuenta",
        True,
        ("ejecucion_scoring_predictivo_id", "cuenta_canonica_id"),
        None,
    ),
    (
        "version_modelo_predictivo",
        "ix_modelo_predictivo_estado",
        False,
        ("estado", "creada_en"),
        None,
    ),
    ("version_modelo_predictivo", "pk_version_modelo_predictivo", True, ("id",), None),
    (
        "version_modelo_predictivo",
        "uq_modelo_predictivo_entrenamiento",
        True,
        ("ejecucion_entrenamiento_predictivo_id",),
        None,
    ),
    (
        "version_modelo_predictivo",
        "uq_version_modelo_predictivo_modelo_version_id",
        True,
        ("modelo_version_id",),
        None,
    ),
}
"""Las llaves, la unicidad (una EXITOSA y una EN_PROCESO por firma) y las paginas que la API lee:
las predicciones de un scoring por cada salida y las de una cuenta. Ni uno mas."""
INMUTABLES_DE_LA_0016 = {
    "ancla_entrenamiento_predictivo": "tr_predictivo_inmutable",
    "interpretacion_entrenamiento_predictivo": "tr_predictivo_inmutable",
    "componente_modelo_predictivo": "tr_predictivo_inmutable",
    "prediccion_cuenta": "tr_predictivo_inmutable",
    "drift_predictivo": "tr_predictivo_inmutable",
}
TRIGGERS_DE_LA_0016 = {
    *INMUTABLES_DE_LA_0016.items(),
    ("ejecucion_entrenamiento_predictivo", "tr_entrenamiento_predictivo_terminado"),
    ("ejecucion_scoring_predictivo", "tr_scoring_predictivo_terminado"),
    ("version_modelo_predictivo", "tr_modelo_predictivo_ciclo"),
    ("version_modelo_predictivo", "tr_modelo_predictivo_completo"),
}
FUNCIONES_DE_LA_0016 = {
    "predictivo_inmutable",
    "entrenamiento_predictivo_terminado",
    "scoring_predictivo_terminado",
    "modelo_predictivo_ciclo",
    "modelo_predictivo_completo",
}
DATOS_PERSONALES = {
    "cuenta_id",
    "cliente_unico",
    "actor_ref",
    "nombre_cliente",
    "direccion",
    "telefono",
    "curp",
    "rfc",
    "sexo",
    "edad",
    "latitud",
    "longitud",
}
"""Ninguna tabla de predictive/model-v1 guarda la identidad publica de una cuenta, a una persona ni
una coordenada; una prediccion apunta a su cuenta solo por su id interno. (El `nombre` de una fila
de drift es el de una feature o una salida.)"""

INSERTAR_ENTRENAMIENTO = text(
    "INSERT INTO ejecucion_entrenamiento_predictivo (training_run_id, despacho_id, cartera_id, "
    "zona_horaria, version_modelo, version_features, hash_features, version_targets, "
    "hash_targets, version_politica, hash_politica, manifiesto, hash_manifiesto, firma_inputs, "
    "dataset_hash, estado, resultado, anclas, ejemplos_total, ejemplos_train, "
    "ejemplos_validation, ejemplos_test, ejemplos_purge, ejemplos_censurados, iniciada_en, "
    "terminada_en) VALUES (:run, 'DSP_001', 'CARTERA_PRINCIPAL', 'America/Mexico_City', "
    "'predictive/model-v1', 'predictive-features/public-v1', :hash, "
    "'predictive-targets/public-v1', :hash, 'predictive-training/public-v1', :hash, "
    "CAST('{}' AS JSONB), :hash, :firma, :dataset, :estado, :resultado, 3, :total, :train, "
    ":validation, :test, :purge, :censurados, now(), :terminada) RETURNING id"
)
INSERTAR_ANCLA = text(
    "INSERT INTO ancla_entrenamiento_predictivo (ejecucion_entrenamiento_predictivo_id, "
    "corte_canonico_id, feature_as_of, feature_known_at, label_known_at, split, hash_ancla) "
    "VALUES (:ejecucion, :corte, :fecha, now(), :label, :split, :hash) "
    "RETURNING ejecucion_entrenamiento_predictivo_id"
)
INSERTAR_VERSION = text(
    "INSERT INTO version_modelo_predictivo (modelo_version_id, version_modelo, "
    "ejecucion_entrenamiento_predictivo_id, version_features, hash_features, version_targets, "
    "hash_targets, version_politica, hash_politica, dataset_hash, hash_modelo, estado, "
    "creada_en, publicada_en, periodo_train_desde, periodo_train_hasta, "
    "periodo_validation_desde, periodo_validation_hasta, periodo_test_desde, periodo_test_hasta, "
    "referencia_drift, metadatos) VALUES (:modelo, 'predictive/model-v1', :ejecucion, "
    "'predictive-features/public-v1', :hash, 'predictive-targets/public-v1', :hash, "
    "'predictive-training/public-v1', :hash, :hash, :hash, :estado, now(), :publicada, "
    "'2026-03-04', '2026-04-01', :validation, '2026-05-06', '2026-06-10', '2026-06-10', "
    "CAST('{}' AS JSONB), CAST('{}' AS JSONB)) RETURNING id"
)
INSERTAR_COMPONENTE = text(
    "INSERT INTO componente_modelo_predictivo (version_modelo_predictivo_id, target, algoritmo, "
    "hiperparametros, artefacto, hash_artefacto, calibracion, metricas_validation, "
    "metricas_test, baseline_test, bootstrap_test, explicacion_global, estado, compuertas) "
    "VALUES (:version, :target, :algoritmo, CAST('{}' AS JSONB), CAST('{}' AS JSONB), :hash, "
    ":calibracion, CAST('{}' AS JSONB), CAST('{}' AS JSONB), CAST('{}' AS JSONB), "
    "CAST('{}' AS JSONB), CAST('[]' AS JSONB), :estado, CAST('[]' AS JSONB)) "
    "RETURNING version_modelo_predictivo_id"
)
INSERTAR_SCORING = text(
    "INSERT INTO ejecucion_scoring_predictivo (scoring_run_id, version_modelo_predictivo_id, "
    "corte_canonico_id, despacho_id, cartera_id, as_of, known_at, modo, zona_horaria, "
    "version_features, hash_features, firma_inputs, estado, resultado, cuentas_objetivo, "
    "predicciones_publicadas, hash_predicciones, hash_drift, iniciada_en, terminada_en) VALUES "
    "(:run, :version, :corte, 'DSP_001', 'CARTERA_PRINCIPAL', :fecha, now(), 'OPERATIVO', "
    "'America/Mexico_City', 'predictive-features/public-v1', :hash, :firma, :estado, "
    ":resultado, :objetivo, :publicadas, :hash_predicciones, :hash_drift, now(), :terminada) "
    "RETURNING id"
)
INSERTAR_PREDICCION = text(
    "INSERT INTO prediccion_cuenta (ejecucion_scoring_predictivo_id, cuenta_canonica_id, "
    "corte_canonico_id, p_recuperacion_positiva_30d, p_contacto_14d, p_promesa_30d, "
    "riesgo_sin_recuperacion_30d, recuperacion_positiva_esperada_30d, estado, motivos, "
    "feature_hash) VALUES (:scoring, :cuenta, :corte, :p, 0.25, 0.1, :riesgo, :esperada, "
    ":estado, CAST('[]' AS JSONB), :feature_hash) RETURNING ejecucion_scoring_predictivo_id"
)
INSERTAR_DRIFT = text(
    "INSERT INTO drift_predictivo (ejecucion_scoring_predictivo_id, tipo, nombre, clase, psi, "
    "severidad, faltantes_train, faltantes_scoring, severidad_faltantes, bins) VALUES "
    "(:scoring, :tipo, :nombre, 'NUMERICA', :psi, :severidad, :train, :scoring_nulos, "
    ":severidad_nulos, CAST('{}' AS JSONB)) RETURNING ejecucion_scoring_predictivo_id"
)
INSERTAR_TRABAJO_0016 = text(
    "INSERT INTO trabajo_orquestacion (trabajo_id, tipo, estado, "
    "ejecucion_entrenamiento_predictivo_id, ejecucion_scoring_predictivo_id, "
    "ejecucion_analytics_id, intentos, max_intentos, creado_en, disponible_desde, worker_id, "
    "lease_hasta, tomado_en) VALUES (:trabajo_id, :tipo, :estado, :entrenamiento, :scoring, "
    ":analytics, :intentos, 5, now(), now(), :worker_id, :lease_hasta, now()) RETURNING id"
)
TARGETS_0016 = (
    ("RECUPERACION_POSITIVA_30D", "LOGISTICA_L2", "PLATT"),
    ("CONTACTO_14D", "LOGISTICA_L2", "RAW"),
    ("PROMESA_30D", "LOGISTICA_L2", "PLATT"),
    ("RECUPERACION_POSITIVA_MONTO_30D", "TWEEDIE_LOG", "RAW"),
)


def _modulo_0016():
    return ScriptDirectory.from_config(_alembic()).get_revision("0016").module


def _de_la_v014(motor: Engine) -> dict[str, list[dict]]:
    """Lo que la 0016 no puede tocar: todo lo de v0.13 y analytics/v1."""
    filas = _de_la_v013(motor)
    llaves = {
        "ejecucion_analytics": "id",
        "entrada_analytics": "ejecucion_analytics_id, tipo_entrada, referencia_publica",
        "agregado_analytics": "ejecucion_analytics_id, metrica_codigo, vista, dimension_set",
        "control_analytics": "ejecucion_analytics_id, codigo_control",
    }
    with motor.connect() as conexion:
        for tabla, orden in llaves.items():
            filas[tabla] = [
                dict(fila)
                for fila in conexion.execute(text(f"SELECT * FROM {tabla} ORDER BY {orden}"))
                .mappings()
                .all()
            ]
    return filas


@dataclass(frozen=True)
class BaseEn0016:
    """Una base de v0.14 con una ejecucion de analytics/v1 publicada, subida a la 0016:
    predictive/model-v1 vacio."""

    motor: Engine
    corte: int
    fecha_corte: date
    cuenta: int
    """Una cuenta con snapshot en el corte."""
    analytics: int
    previas: dict[str, list[dict]]
    de_la_v014: dict[str, list[dict]]


@pytest.fixture
def base_en_0016(base_en_0015) -> BaseEn0016:
    b = base_en_0015
    motor = b.motor
    analytics = _publicada_0015(b)
    (cuenta,) = _consultar(
        motor,
        f"SELECT cuenta_canonica_id FROM snapshot_cuenta WHERE corte_canonico_id = {b.corte} "
        "ORDER BY cuenta_canonica_id LIMIT 1",
    )
    previas = _por_columna(motor, PREVIAS_A_LA_0008)
    de_la_v014 = _de_la_v014(motor)

    command.upgrade(_alembic(), "0016")
    return BaseEn0016(motor, b.corte, b.fecha_corte, cuenta, analytics, previas, de_la_v014)


def _entrenamiento_0016(estado: str = "EN_PROCESO", **cambios) -> dict:
    exitosa = estado == "EXITOSA"
    return {
        "run": uuid4(),
        "hash": HASH,
        "firma": uuid4().hex * 2,
        "dataset": "d" * 64 if exitosa else None,
        "estado": estado,
        "resultado": "MODELO_PUBLICADO"
        if exitosa
        else (None if estado == "EN_PROCESO" else "ERROR_INTERNO"),
        "total": 30 if exitosa else 0,
        "train": 10 if exitosa else 0,
        "validation": 10 if exitosa else 0,
        "test": 10 if exitosa else 0,
        "purge": 0,
        "censurados": 0,
        "terminada": None if estado == "EN_PROCESO" else datetime.now(UTC),
        **cambios,
    }


def _version_0016(ejecucion: int, estado: str = "CANDIDATA", **cambios) -> dict:
    return {
        "modelo": uuid4(),
        "ejecucion": ejecucion,
        "hash": HASH,
        "estado": estado,
        "publicada": datetime.now(UTC) if estado == "PUBLICADA" else None,
        "validation": "2026-05-06",
        **cambios,
    }


def _componentes_0016(version: int, estado: str = "VALIDADO") -> list[dict]:
    return [
        {
            "version": version,
            "target": target,
            "algoritmo": algoritmo,
            "hash": HASH,
            "calibracion": calibracion,
            "estado": estado,
        }
        for target, algoritmo, calibracion in TARGETS_0016
    ]


def _publicado_0016(b: BaseEn0016) -> tuple[int, int]:
    """Un entrenamiento EXITOSA con su version PUBLICADA y sus cuatro componentes VALIDADO, todo en
    una transaccion: (entrenamiento, version)."""
    with b.motor.begin() as conexion:
        ejecucion = conexion.execute(
            INSERTAR_ENTRENAMIENTO, _entrenamiento_0016("EXITOSA")
        ).scalar_one()
        version = conexion.execute(INSERTAR_VERSION, _version_0016(ejecucion)).scalar_one()
        for componente in _componentes_0016(version):
            conexion.execute(INSERTAR_COMPONENTE, componente)
        conexion.execute(
            text("UPDATE version_modelo_predictivo SET estado = 'VALIDADA' WHERE id = :v"),
            {"v": version},
        )
        conexion.execute(
            text(
                "UPDATE version_modelo_predictivo SET estado = 'PUBLICADA', publicada_en = now() "
                "WHERE id = :v"
            ),
            {"v": version},
        )
    return ejecucion, version


def _scoring_0016(b: BaseEn0016, version_id: int, estado: str = "EN_PROCESO", **cambios) -> dict:
    exitosa = estado == "EXITOSA"
    return {
        "run": uuid4(),
        "version": version_id,
        "corte": b.corte,
        "fecha": b.fecha_corte,
        "hash": HASH,
        "firma": uuid4().hex * 2,
        "estado": estado,
        "resultado": "PREDICCIONES_PUBLICADAS"
        if exitosa
        else (None if estado == "EN_PROCESO" else "ERROR_INTERNO"),
        "objetivo": 1,
        "publicadas": 1 if exitosa else 0,
        "hash_predicciones": "e" * 64 if exitosa else None,
        "hash_drift": "f" * 64 if exitosa else None,
        "terminada": None if estado == "EN_PROCESO" else datetime.now(UTC),
        **cambios,
    }


def _prediccion_0016(b: BaseEn0016, scoring_id: int, **cambios) -> dict:
    return {
        "scoring": scoring_id,
        "cuenta": b.cuenta,
        "corte": b.corte,
        "p": Decimal("0.3"),
        "riesgo": Decimal("0.7"),
        "esperada": Decimal("125.50"),
        "estado": "COMPLETA",
        "feature_hash": hashlib.sha256(b"features").digest(),
        **cambios,
    }


def _drift_0016(scoring_id: int, **cambios) -> dict:
    return {
        "scoring": scoring_id,
        "tipo": "FEATURE",
        "nombre": "saldo_total",
        "psi": Decimal("0.05"),
        "severidad": "BAJO",
        "train": Decimal("0.1"),
        "scoring_nulos": Decimal("0.12"),
        "severidad_nulos": "BAJO",
        **cambios,
    }


def _trabajo_0016(tipo: str = "PREDICTIVE_TRAINING", estado: str = "PENDIENTE", **cambios):
    ejecutando = estado == "EJECUTANDO"
    return {
        "trabajo_id": uuid4(),
        "tipo": tipo,
        "estado": estado,
        "entrenamiento": None,
        "scoring": None,
        "analytics": None,
        "intentos": 1 if ejecutando else 0,
        "worker_id": "worker-de-prueba:1:abc" if ejecutando else None,
        "lease_hasta": datetime.now(UTC) + timedelta(minutes=1) if ejecutando else None,
        **cambios,
    }


def test_la_cabeza_es_la_0016():
    scripts = ScriptDirectory.from_config(_alembic())

    assert scripts.get_heads() == ["0016"]
    assert scripts.get_revision("0016").down_revision == "0015"


def test_los_modelos_declaran_el_esquema_de_la_0016():
    from motor_cartera.db import modelos

    tablas = [modelos.SQLModel.metadata.tables[t] for t in sorted(TABLAS_DE_LA_0016)]
    preparador = postgresql.dialect().identifier_preparer
    nombres = {preparador.format_constraint(c) for t in tablas for c in t.constraints}
    assert nombres == RESTRICCIONES_DE_LA_0016
    assert all(len(nombre) <= 63 for nombre in nombres)
    for tabla in tablas:
        for columna in tabla.columns:
            assert columna.name not in DATOS_PERSONALES, (tabla.name, columna.name)
            assert not hasattr(columna.type, "get_col_spec"), (tabla.name, columna.name)
            # Ninguna probabilidad, monto o PSI es un float binario: NUMERIC con su escala.
            assert not isinstance(columna.type, Float), (tabla.name, columna.name)
    prediccion = modelos.SQLModel.metadata.tables["prediccion_cuenta"]
    for columna in ("p_recuperacion_positiva_30d", "riesgo_sin_recuperacion_30d"):
        assert (prediccion.c[columna].type.precision, prediccion.c[columna].type.scale) == (9, 8)
    trabajo = TrabajoOrquestacion.__table__
    assert {"PREDICTIVE_TRAINING", "PREDICTIVE_SCORING"} <= set(trabajo.c.tipo.type.enums)
    llaves = {preparador.format_constraint(c) for c in trabajo.constraints}
    assert RESTRICCIONES_DE_LA_0016_EN_LA_COLA <= llaves


def test_la_0016_agrega_predictivo_vacio_sin_tocar_nada_de_la_v014(base_en_0016):
    b = base_en_0016
    motor = b.motor

    # A. Cada fila de antes sigue ahi, identica; un trabajo solo gana sus dos objetivos nuevos,
    # vacios.
    assert _de_la_v014(motor) == b.de_la_v014
    despues = _por_columna(motor, PREVIAS_A_LA_0008)
    for tabla, filas in b.previas.items():
        assert len(despues[tabla]) == len(filas), tabla
        for antes, fila in zip(filas, despues[tabla], strict=True):
            fila = dict(fila)
            if tabla == "trabajo_orquestacion":
                assert fila.pop("ejecucion_entrenamiento_predictivo_id") is None
                assert fila.pop("ejecucion_scoring_predictivo_id") is None
            assert fila == antes, tabla

    # B. Las tablas nuevas vacias, con sus restricciones, sus indices y sus triggers, por nombre.
    assert TABLAS_DE_LA_0016 <= set(_consultar(motor, TABLAS))
    for tabla in sorted(TABLAS_DE_LA_0016):
        assert _consultar(motor, f"SELECT count(*) FROM {tabla}") == [0]
    assert set(_consultar(motor, RESTRICCIONES_0016)) == RESTRICCIONES_DE_LA_0016
    with motor.connect() as conexion:
        indices = {
            (tabla, nombre, unico, tuple(columnas), predicado)
            for tabla, nombre, unico, columnas, predicado in conexion.execute(text(INDICES_0016))
        }
        triggers = {tuple(fila) for fila in conexion.execute(text(TRIGGERS))}
        metodos = set(
            conexion.execute(
                text(
                    "SELECT DISTINCT am.amname FROM pg_index x JOIN pg_class i "
                    "ON i.oid = x.indexrelid JOIN pg_class t ON t.oid = x.indrelid "
                    f"JOIN pg_am am ON am.oid = i.relam WHERE t.relname IN ({_DE_LA_0016})"
                )
            ).scalars()
        )
    assert indices == INDICES_DE_LA_0016
    assert metodos == {"btree"}
    assert TRIGGERS_DE_LA_0016 <= triggers
    assert FUNCIONES_DE_LA_0016 <= set(_consultar(motor, FUNCIONES))
    assert RESTRICCIONES_DE_LA_0016_EN_LA_COLA <= _restricciones_de(motor, "trabajo_orquestacion")
    assert _consultar(motor, "SELECT version_num FROM alembic_version") == ["0016"]


def test_un_entrenamiento_exitoso_y_uno_en_proceso_por_firma(base_en_0016):
    motor = base_en_0016.motor
    firma = "c" * 64
    _insertar(
        motor,
        INSERTAR_ENTRENAMIENTO,
        _entrenamiento_0016("EXITOSA", firma=firma),
        _entrenamiento_0016(firma=firma),
    )
    _rechaza(
        motor,
        "ux_entrenamiento_predictivo_exitosa",
        INSERTAR_ENTRENAMIENTO,
        _entrenamiento_0016("EXITOSA", firma=firma),
    )
    _rechaza(
        motor,
        "ux_entrenamiento_predictivo_en_proceso",
        INSERTAR_ENTRENAMIENTO,
        _entrenamiento_0016(firma=firma),
    )
    _insertar(
        motor,
        INSERTAR_ENTRENAMIENTO,
        _entrenamiento_0016("FALLIDA", firma=firma),
        _entrenamiento_0016("FALLIDA", firma=firma),
    )


def test_un_entrenamiento_cuadra_sus_conteos_y_lo_que_dejo(base_en_0016):
    motor = base_en_0016.motor
    for restriccion, entrenamiento in (
        ("ck_entrenamiento_predictivo_firmas", _entrenamiento_0016(firma="NO-ES-HEX")),
        ("ck_entrenamiento_predictivo_firmas", _entrenamiento_0016(hash="a" * 63)),
        # Mientras entrena no ha dejado ningun dataset ni resultado.
        ("ck_entrenamiento_predictivo_publicacion", _entrenamiento_0016(dataset="d" * 64)),
        ("ck_entrenamiento_predictivo_publicacion", _entrenamiento_0016(resultado="X")),
        # Una EXITOSA dejo un dataset con TRAIN y un modelo, PUBLICADO o RECHAZADO.
        ("ck_entrenamiento_predictivo_publicacion", _entrenamiento_0016("EXITOSA", dataset=None)),
        (
            "ck_entrenamiento_predictivo_publicacion",
            _entrenamiento_0016("EXITOSA", train=0, total=20),
        ),
        (
            "ck_entrenamiento_predictivo_publicacion",
            _entrenamiento_0016("EXITOSA", resultado="ERROR_INTERNO"),
        ),
        (
            "ck_entrenamiento_predictivo_publicacion",
            _entrenamiento_0016("FALLIDA", resultado="MODELO_PUBLICADO"),
        ),
        ("ck_entrenamiento_predictivo_conteos", _entrenamiento_0016("EXITOSA", total=31)),
        ("ck_entrenamiento_predictivo_conteos", _entrenamiento_0016("EXITOSA", censurados=31)),
        ("ck_entrenamiento_predictivo_conteos", _entrenamiento_0016(purge=-1, total=-1)),
        (
            "ck_ejecucion_entrenamiento_predictivo_estado_entrenamiento",
            _entrenamiento_0016("PUBLICADA", resultado="X"),
        ),
    ):
        _rechaza(motor, restriccion, INSERTAR_ENTRENAMIENTO, entrenamiento)
    _insertar(
        motor, INSERTAR_ENTRENAMIENTO, _entrenamiento_0016("EXITOSA", resultado="MODELO_RECHAZADO")
    )


def test_un_ancla_es_de_su_corte_y_declara_sus_relojes(base_en_0016):
    b = base_en_0016
    motor = b.motor
    (ejecucion,) = _insertar(motor, INSERTAR_ENTRENAMIENTO, _entrenamiento_0016())
    ancla = {
        "ejecucion": ejecucion,
        "corte": b.corte,
        "fecha": b.fecha_corte,
        "label": datetime.now(UTC) + timedelta(minutes=5),
        "split": "TRAIN",
        "hash": HASH,
    }
    _insertar(motor, INSERTAR_ANCLA, ancla)
    # La unicidad se revisa antes que las llaves foraneas: la del corte, con otro entrenamiento.
    (otra,) = _insertar(motor, INSERTAR_ENTRENAMIENTO, _entrenamiento_0016())
    for restriccion, cambios in (
        ("pk_ancla_entrenamiento_predictivo", {}),
        (
            "fk_ancla_predictiva_corte",
            {"ejecucion": otra, "fecha": b.fecha_corte + timedelta(days=1)},
        ),
        ("ck_ancla_predictiva_split", {"split": "HOLDOUT"}),
        ("ck_ancla_predictiva_relojes", {"label": datetime(2020, 1, 1, tzinfo=UTC)}),
        ("fk_ancla_predictiva_entrenamiento", {"ejecucion": 999_999}),
    ):
        _rechaza(motor, restriccion, INSERTAR_ANCLA, {**ancla, **cambios})


def test_una_version_avanza_hasta_publicada_y_se_confirma_completa(base_en_0016):
    """CANDIDATA, VALIDADA y PUBLICADA (o RECHAZADA) en la transaccion que la publica; al confirmar,
    PUBLICADA con sus cuatro componentes VALIDADO; despues, nada cambia ni se borra."""
    b = base_en_0016
    motor = b.motor
    _, version = _publicado_0016(b)
    assert _consultar(
        motor, f"SELECT estado FROM version_modelo_predictivo WHERE id = {version}"
    ) == ["PUBLICADA"]
    # Al confirmar: una PUBLICADA sin sus cuatro componentes VALIDADO no.
    (otra,) = _insertar(motor, INSERTAR_ENTRENAMIENTO, _entrenamiento_0016("EXITOSA"))
    _rechaza_al_confirmar(
        motor, "tr_modelo_predictivo_completo", (INSERTAR_VERSION, _version_0016(otra, "PUBLICADA"))
    )
    # Ni quedarse CANDIDATA al confirmar.
    _rechaza_al_confirmar(
        motor, "tr_modelo_predictivo_completo", (INSERTAR_VERSION, _version_0016(otra))
    )
    # Una publicada no cambia, no vuelve atras ni se borra.
    for sentencia in (
        "UPDATE version_modelo_predictivo SET estado = 'RECHAZADA' WHERE id = :v",
        "UPDATE version_modelo_predictivo SET hash_modelo = repeat('b', 64) WHERE id = :v",
        "DELETE FROM version_modelo_predictivo WHERE id = :v",
    ):
        with pytest.raises(DBAPIError) as error, motor.begin() as conexion:
            conexion.execute(text(sentencia), {"v": version})
        assert error.value.orig.diag.constraint_name == "tr_modelo_predictivo_ciclo"
    # De CANDIDATA no salta a PUBLICADA.
    with pytest.raises(DBAPIError) as error, motor.begin() as conexion:
        nueva = conexion.execute(INSERTAR_VERSION, _version_0016(otra)).scalar_one()
        for c in _componentes_0016(nueva):
            conexion.execute(INSERTAR_COMPONENTE, c)
        conexion.execute(
            text(
                "UPDATE version_modelo_predictivo SET estado = 'PUBLICADA', publicada_en = now() "
                "WHERE id = :v"
            ),
            {"v": nueva},
        )
    assert error.value.orig.diag.constraint_name == "tr_modelo_predictivo_ciclo"


def test_un_componente_es_coherente_con_su_target(base_en_0016):
    b = base_en_0016
    motor = b.motor
    _, version = _publicado_0016(b)
    for cambios in (
        {"target": "SALDO_FUTURO"},
        {"algoritmo": "XGBOOST"},
        {"algoritmo": "TWEEDIE_LOG"},
        {"calibracion": "BETA"},
        {"estado": "PUBLICADO"},
        {"hash": "x" * 64},
    ):
        _rechaza(
            motor,
            "ck_componente_predictivo_coherencia",
            INSERTAR_COMPONENTE,
            {**_componentes_0016(version)[1], **cambios},
        )


def test_un_scoring_cuadra_lo_que_publico_y_cada_prediccion_su_rango(base_en_0016):
    b = base_en_0016
    motor = b.motor
    _, version = _publicado_0016(b)
    (scoring,) = _insertar(motor, INSERTAR_SCORING, _scoring_0016(b, version))
    _insertar(motor, INSERTAR_PREDICCION, _prediccion_0016(b, scoring))
    _insertar(motor, INSERTAR_DRIFT, _drift_0016(scoring))
    for restriccion, cambios in (
        ("ck_scoring_predictivo_publicacion", {"publicadas": 1}),
        ("ck_scoring_predictivo_publicacion", {"hash_predicciones": "e" * 64}),
        ("ck_scoring_predictivo_resultado", {"resultado": "PREDICCIONES_PUBLICADAS"}),
        ("ck_scoring_predictivo_firmas", {"firma": "NO-ES-HEX"}),
        ("fk_scoring_predictivo_corte", {"fecha": b.fecha_corte + timedelta(days=1)}),
        ("fk_scoring_predictivo_modelo", {"version": 999_999}),
        (
            "ck_ejecucion_scoring_predictivo_estado_scoring_predictivo",
            {"estado": "X", "resultado": "X"},
        ),
    ):
        _rechaza(motor, restriccion, INSERTAR_SCORING, _scoring_0016(b, version, **cambios))
    for cambios in (
        {"publicadas": 0},
        {"hash_drift": None},
        {"objetivo": 0, "publicadas": 0},
    ):
        _rechaza(
            motor,
            "ck_scoring_predictivo_publicacion",
            INSERTAR_SCORING,
            _scoring_0016(b, version, "EXITOSA", **cambios),
        )
    (otro,) = _insertar(motor, INSERTAR_SCORING, _scoring_0016(b, version))
    for restriccion, cambios in (
        ("ck_prediccion_cuenta_rangos", {"p": Decimal("1.5"), "riesgo": Decimal("-0.5")}),
        ("ck_prediccion_cuenta_rangos", {"riesgo": Decimal("0.6")}),
        ("ck_prediccion_cuenta_rangos", {"esperada": Decimal("-1")}),
        ("ck_prediccion_cuenta_rangos", {"esperada": Decimal("NaN")}),
        ("ck_prediccion_cuenta_rangos", {"feature_hash": b"corto"}),
        ("ck_prediccion_cuenta_estado", {"estado": "ESTIMADA"}),
        ("fk_prediccion_snapshot", {"cuenta": 999_999}),
        ("fk_prediccion_scoring", {"scoring": 999_999}),
    ):
        _rechaza(motor, restriccion, INSERTAR_PREDICCION, _prediccion_0016(b, otro, **cambios))
    for cambios in (
        {"tipo": "CUENTA"},
        {"psi": Decimal("-0.1")},
        {"severidad": "CRITICO"},
        {"train": None},
    ):
        _rechaza(
            motor,
            "ck_drift_predictivo_valores",
            INSERTAR_DRIFT,
            _drift_0016(otro, nombre="otra", **cambios),
        )


@pytest.mark.parametrize(("tabla", "trigger"), sorted(INMUTABLES_DE_LA_0016.items()))
def test_lo_que_publica_predictivo_no_se_reescribe(base_en_0016, tabla, trigger):
    b = base_en_0016
    motor = b.motor
    ejecucion, version = _publicado_0016(b)
    _insertar(
        motor,
        INSERTAR_ANCLA,
        {
            "ejecucion": ejecucion,
            "corte": b.corte,
            "fecha": b.fecha_corte,
            "label": datetime.now(UTC) + timedelta(minutes=5),
            "split": "TRAIN",
            "hash": HASH,
        },
    )
    (scoring,) = _insertar(motor, INSERTAR_SCORING, _scoring_0016(b, version))
    _insertar(motor, INSERTAR_PREDICCION, _prediccion_0016(b, scoring))
    _insertar(motor, INSERTAR_DRIFT, _drift_0016(scoring))
    (columna,) = _consultar(
        motor,
        "SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() "
        f"AND table_name = '{tabla}' ORDER BY ordinal_position LIMIT 1",
    )
    for sentencia in (
        f"UPDATE {tabla} SET {columna} = {columna}",
        f"DELETE FROM {tabla}",
        f"DELETE FROM {tabla} WHERE false",
    ):
        with pytest.raises(DBAPIError) as error, motor.begin() as conexion:
            conexion.execute(text(sentencia))
        assert error.value.orig.sqlstate == "23001", sentencia  # restrict_violation
        assert error.value.orig.diag.constraint_name == trigger


def test_una_ejecucion_predictiva_solo_cambia_mientras_esta_en_proceso(base_en_0016):
    b = base_en_0016
    motor = b.motor
    entrenamiento, version = _publicado_0016(b)
    (scoring,) = _insertar(motor, INSERTAR_SCORING, _scoring_0016(b, version, "FALLIDA"))
    for tabla, ejecucion, trigger in (
        (
            "ejecucion_entrenamiento_predictivo",
            entrenamiento,
            "tr_entrenamiento_predictivo_terminado",
        ),
        ("ejecucion_scoring_predictivo", scoring, "tr_scoring_predictivo_terminado"),
    ):
        for sentencia in (
            f"UPDATE {tabla} SET detalle = 'otro' WHERE id = :id",
            f"DELETE FROM {tabla} WHERE id = :id",
        ):
            with pytest.raises(DBAPIError) as error, motor.begin() as conexion:
                conexion.execute(text(sentencia), {"id": ejecucion})
            assert error.value.orig.sqlstate == "23001"
            assert error.value.orig.diag.constraint_name == trigger


def test_el_trabajo_predictivo_apunta_a_su_ejecucion_y_a_nada_mas(base_en_0016):
    b = base_en_0016
    motor = b.motor
    (entrenamiento,) = _insertar(motor, INSERTAR_ENTRENAMIENTO, _entrenamiento_0016())
    _insertar(motor, INSERTAR_TRABAJO_0016, _trabajo_0016(entrenamiento=entrenamiento))
    for restriccion, trabajo in (
        ("uq_trabajo_entrenamiento_predictivo", _trabajo_0016(entrenamiento=entrenamiento)),
        ("ck_trabajo_objetivo", _trabajo_0016()),
        ("ck_trabajo_objetivo", _trabajo_0016("PREDICTIVE_SCORING", entrenamiento=entrenamiento)),
        (
            "ck_trabajo_objetivo",
            _trabajo_0016(entrenamiento=entrenamiento, analytics=b.analytics),
        ),
        ("fk_trabajo_entrenamiento_predictivo", _trabajo_0016(entrenamiento=999_999)),
        ("fk_trabajo_scoring_predictivo", _trabajo_0016("PREDICTIVE_SCORING", scoring=999_999)),
        ("ck_trabajo_orquestacion_tipo_trabajo", _trabajo_0016("PREDICTIVE_RETRAIN")),
    ):
        _rechaza(motor, restriccion, INSERTAR_TRABAJO_0016, trabajo)


def test_la_0016_baja_cerrando_lo_que_estaba_en_curso_y_vuelve_a_subir(base_en_0016):
    b = base_en_0016
    motor = b.motor
    _, version = _publicado_0016(b)
    (scoring,) = _insertar(motor, INSERTAR_SCORING, _scoring_0016(b, version))
    _insertar(motor, INSERTAR_PREDICCION, _prediccion_0016(b, scoring))
    (en_curso,) = _insertar(motor, INSERTAR_ENTRENAMIENTO, _entrenamiento_0016())
    _insertar(
        motor,
        INSERTAR_TRABAJO_0016,
        _trabajo_0016(estado="EJECUTANDO", entrenamiento=en_curso),
        _trabajo_0016("PREDICTIVE_SCORING", scoring=scoring),
    )
    previas = _por_columna(motor, PREVIAS_A_LA_0008)
    trabajos_antes = [
        {
            k: v
            for k, v in f.items()
            if k not in ("ejecucion_entrenamiento_predictivo_id", "ejecucion_scoring_predictivo_id")
        }
        for f in previas["trabajo_orquestacion"]
        if not f["tipo"].startswith("PREDICTIVE_")
    ]
    assert len(previas["trabajo_orquestacion"]) == len(trabajos_antes) + 2

    command.downgrade(_alembic(), "0015")

    # predictive/model-v1 entero se fue, con sus trabajos. Lo de v0.14, intacto: analytics/v1,
    # ruteo/v2 y todo lo anterior, PostGIS; los otros trabajos, sin sus columnas nuevas.
    assert not TABLAS_DE_LA_0016 & set(_consultar(motor, TABLAS))
    assert not FUNCIONES_DE_LA_0016 & set(_consultar(motor, FUNCIONES))
    assert _consultar(motor, POSTGIS) == ["postgis"]
    assert _de_la_v014(motor) == b.de_la_v014
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
    assert not any("PREDICTIVE" in d for d in definiciones)
    assert all("ANALYTICS" in d for d in definiciones)
    assert _consultar(motor, "SELECT version_num FROM alembic_version") == ["0015"]

    # Y vuelve a subir limpia.
    command.upgrade(_alembic(), "0016")

    for tabla in sorted(TABLAS_DE_LA_0016):
        assert _consultar(motor, f"SELECT count(*) FROM {tabla}") == [0]
    assert set(_consultar(motor, RESTRICCIONES_0016)) == RESTRICCIONES_DE_LA_0016
    assert FUNCIONES_DE_LA_0016 <= set(_consultar(motor, FUNCIONES))
    assert RESTRICCIONES_DE_LA_0016_EN_LA_COLA <= _restricciones_de(motor, "trabajo_orquestacion")
    assert _de_la_v014(motor) == b.de_la_v014
    assert _consultar(motor, "SELECT version_num FROM alembic_version") == ["0016"]


def test_al_bajar_la_0016_lo_primero_es_cerrar_lo_que_estaba_en_curso(base_en_0016):
    # Lo que la 0016 hace antes de quitar sus tablas, con sus mismas sentencias: los entrenamientos
    # y los scorings en curso quedan FALLIDA, sin nada publicado, y sus trabajos FALLIDO, sin dueno
    # ni lease, con el motivo. Lo terminado no se toca.
    b = base_en_0016
    motor = b.motor
    modulo = _modulo_0016()
    publicado, version = _publicado_0016(b)
    (en_curso,) = _insertar(motor, INSERTAR_ENTRENAMIENTO, _entrenamiento_0016())
    (scoring,) = _insertar(motor, INSERTAR_SCORING, _scoring_0016(b, version))
    _insertar(
        motor,
        INSERTAR_TRABAJO_0016,
        _trabajo_0016(estado="EJECUTANDO", entrenamiento=en_curso),
        _trabajo_0016("PREDICTIVE_SCORING", scoring=scoring),
    )

    with motor.begin() as conexion:
        conexion.execute(modulo.CERRAR_ENTRENAMIENTOS)
        conexion.execute(modulo.CERRAR_SCORINGS)
        conexion.execute(modulo.CERRAR_TRABAJOS)

    with motor.connect() as conexion:
        entrenamientos = conexion.execute(
            text(
                "SELECT id, estado, resultado, detalle FROM ejecucion_entrenamiento_predictivo "
                "ORDER BY id"
            )
        ).all()
        scorings = conexion.execute(
            text(
                "SELECT estado, resultado, predicciones_publicadas, terminada_en IS NOT NULL "
                "FROM ejecucion_scoring_predictivo"
            )
        ).all()
        trabajos = conexion.execute(
            text(
                "SELECT tipo, estado, worker_id, lease_hasta, ultimo_error FROM "
                "trabajo_orquestacion WHERE tipo LIKE 'PREDICTIVE_%' ORDER BY id"
            )
        ).all()
    assert [tuple(e) for e in entrenamientos] == [
        (publicado, "EXITOSA", "MODELO_PUBLICADO", None),
        (en_curso, "FALLIDA", "CERRADA_AL_BAJAR", modulo.CIERRE),
    ]
    assert [tuple(s) for s in scorings] == [("FALLIDA", "CERRADA_AL_BAJAR", 0, True)]
    assert [tuple(t) for t in trabajos] == [
        ("PREDICTIVE_TRAINING", "FALLIDO", None, None, modulo.CIERRE),
        ("PREDICTIVE_SCORING", "FALLIDO", None, None, modulo.CIERRE),
    ]
