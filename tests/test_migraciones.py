"""Las migraciones sobre una base que ya tiene corridas, contra PostgreSQL.

Las demas pruebas suben el esquema una vez sobre una base vacia, y el CI tambien migra sobre
una vacia. Aqui se prueba lo que ninguno de los dos ve: que una migracion no rompe ni pierde
las corridas que ya existen. Corre en una base propia, para no mover el esquema de la base de
pruebas a media sesion.

Las pruebas que migran de verdad usan PostgreSQL. Algunas comprobaciones solo leen lo que declaran
los modelos, sin migrar nada, y corren sin servidor.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config as ConfigAlembic
from sqlalchemy import BigInteger, Engine, Numeric, UniqueConstraint, create_engine, make_url, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from motor_cartera.config import config
from motor_cartera.db.modelos import EjecucionTerritorial, ResultadoTerritorial

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
    # subida hasta la 0004.
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
    assert nombres == RESTRICCIONES_TERRITORIALES | INDICES_TERRITORIALES
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
