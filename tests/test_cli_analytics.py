"""analytics/v1 por la linea de comandos, con el escenario golden (`analytics_escenarios`): proponer
un manifiesto sin analizar nada (y reportar una ambiguedad en lugar de elegir), analizar con el
manifiesto, resumir, exportar sin valores suprimidos, y el backfill, que exige su horizonte y pide
solo lo que falta. Ninguna salida lleva algo de una cuenta."""

from __future__ import annotations

import csv
import json
import re

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from analytics_escenarios import AS_OF_A, AS_OF_B, CORTE, ZONA, decidir, golden
from sqlalchemy import func, text, update
from typer.testing import CliRunner

from motor_cartera.analytics import manifiesto
from motor_cartera.cli import app
from motor_cartera.db.modelos import EstadoTrabajo, TrabajoOrquestacion
from motor_cartera.db.sesion import sesion

pytestmark = pytest.mark.usefixtures("bd_del_modulo")

cli = CliRunner()
NO_EXISTE = "00000000-0000-4000-8000-000000000000"


@pytest.fixture(scope="module")
def g(bd_del_modulo, tmp_path_factory):
    return golden(tmp_path_factory.mktemp("cli_analytics"))


def _id(salida: str, llave: str) -> str:
    encontrado = re.search(rf"^{llave} ([0-9a-f-]{{36}})$", salida, re.MULTILINE)
    assert encontrado, salida
    return encontrado.group(1)


def _ejecuciones() -> int:
    with sesion() as s:
        return s.execute(text("SELECT count(*) FROM ejecucion_analytics")).scalar_one()


def _cola_vacia() -> None:
    with sesion() as s:
        s.execute(
            update(TrabajoOrquestacion)
            .where(TrabajoOrquestacion.estado.in_(("PENDIENTE", "EJECUTANDO")))
            .values(
                estado=EstadoTrabajo.COMPLETADO,
                terminado_en=func.now(),
                worker_id=None,
                lease_hasta=None,
            )
        )
        s.commit()


def _sin_datos_de_una_cuenta(texto: str) -> None:
    assert "CU0000" not in texto
    assert "AGT-" not in texto
    assert "-98." not in texto


def _construir(known_at, as_of, destino, *extra) -> object:
    return cli.invoke(
        app,
        [
            "construir-manifiesto-analytics",
            "--corte",
            CORTE.isoformat(),
            "--as-of",
            as_of.isoformat(),
            "--modo",
            "BACKTEST",
            "--zona",
            ZONA,
            "--granularidad",
            "DIA",
            "--known-at",
            known_at.isoformat(),
            "--destino",
            str(destino),
            *extra,
        ],
    )


# --- proponer el manifiesto -----------------------------------------------------------------------


def test_construir_el_manifiesto_propone_lo_que_existia_a_known_at_y_no_analiza(g, tmp_path):
    antes = _ejecuciones()
    destino = tmp_path / "manifiesto.json"

    resultado = _construir(g.k1, AS_OF_A, destino)

    assert resultado.exit_code == 0, resultado.output
    salida = resultado.output
    # Septiembre tiene dos interpretaciones: la vigente a K1 es la segunda, y la otra se lista.
    assert f"MOTOR_PAGOS 2026-09: {g.septiembre.motor_pagos_run_id} (VIGENTE_A_KNOWN_AT" in salida
    assert f"candidata {g.septiembre_previa.motor_pagos_run_id}" in salida
    assert f"DECISION_V2: {g.operacion.decision.decision_run_id} (UNICA_CANDIDATA" in salida
    assert f"RUTEO_V2: {g.operacion.ruteo.ruteo_run_id} (UNICA_CANDIDATA" in salida
    assert "No se inicio ningun analisis" in salida
    assert _ejecuciones() == antes
    escrito = json.loads(destino.read_text(encoding="utf-8"))
    esperado = g.peticion(AS_OF_A, g.k1)
    with sesion() as s:
        resuelto = manifiesto.resolver(s, esperado)
    assert (escrito["hash_manifiesto"], escrito["firma_inputs"]) == (
        resuelto.hash_manifiesto,
        resuelto.firma,
    )
    cuerpo = escrito["manifiesto"]
    assert cuerpo["motor_pagos_run_ids"] == [str(x) for x in esperado.motor_pagos]
    assert cuerpo["atribucion_run_ids"] == [str(x) for x in esperado.atribucion]
    assert cuerpo["evaluacion_run_id"] == str(g.evaluacion_a.evaluacion_run_id)
    assert cuerpo["known_at"] == g.k1.isoformat()
    _sin_datos_de_una_cuenta(salida)


def test_una_familia_excluida_no_se_declara(g, tmp_path):
    destino = tmp_path / "sin_ruteo.json"

    resultado = _construir(g.k1, AS_OF_A, destino, "--sin", "RUTEO_V2", "--sin", "CAMPO_V2")

    assert resultado.exit_code == 0, resultado.output
    cuerpo = json.loads(destino.read_text(encoding="utf-8"))["manifiesto"]
    assert (cuerpo["campo_run_id"], cuerpo["ruteo_run_id"]) == (None, None)
    assert "RUTEO_V2:" not in resultado.output


# --- analizar, resumir y exportar -----------------------------------------------------------------


@pytest.fixture(scope="module")
def analizada(g, tmp_path_factory):
    destino = tmp_path_factory.mktemp("manifiesto_b") / "b.json"
    construido = _construir(g.k2, AS_OF_B, destino)
    assert construido.exit_code == 0, construido.output
    _cola_vacia()
    resultado = cli.invoke(app, ["analizar-cobranza", "--manifiesto", str(destino)])
    assert resultado.exit_code == 0, resultado.output
    return destino, resultado.output


def test_analizar_con_el_manifiesto_publica_y_no_repite(analizada):
    destino, salida = analizada
    assert "EXITOSA (ANALYTICS_PUBLICADA)" in salida
    _sin_datos_de_una_cuenta(salida)

    otra = cli.invoke(app, ["analizar-cobranza", "--manifiesto", str(destino)])

    assert otra.exit_code == 1
    assert "ANALYTICS_YA_GENERADA" in otra.output


def test_el_resumen_trae_entradas_indicadores_y_embudos_sin_cuentas(analizada):
    _, salida = analizada
    ejecucion = _id(salida, "analytics_run_id")

    resultado = cli.invoke(app, ["resumen-analytics", "--ejecucion", ejecucion])

    assert resultado.exit_code == 0, resultado.output
    texto = resultado.output
    assert "EXITOSA (ANALYTICS_PUBLICADA)" in texto
    assert "recuperacion_neta: 2270.00" in texto
    assert "embudo CONTACTO: COHORTE 20 > GESTIONADAS 12 > CONTACTADAS 10 > CON_PROMESA 7" in texto
    assert "embudo CAMPO: CANDIDATAS 6 > ASIGNADAS 4 > PARADAS_PROGRAMADAS 2" in texto
    assert "0 sin cuadrar" in texto
    assert "no prueba causalidad" in texto
    _sin_datos_de_una_cuenta(texto)


@pytest.mark.parametrize("formato", ["csv", "jsonl", "parquet"])
def test_exportar_no_exporta_ningun_valor_suprimido(analizada, tmp_path, formato):
    _, salida = analizada
    ejecucion = _id(salida, "analytics_run_id")
    destino = tmp_path / f"agregados.{formato}"

    resultado = cli.invoke(
        app,
        [
            "exportar-analytics",
            "--ejecucion",
            ejecucion,
            "--formato",
            formato,
            "--destino",
            str(destino),
        ],
    )

    assert resultado.exit_code == 0, resultado.output
    with sesion() as s:
        publicados = s.execute(
            text(
                "SELECT agregados_publicados FROM ejecucion_analytics WHERE analytics_run_id = :e"
            ),
            {"e": ejecucion},
        ).scalar_one()
    if formato == "csv":
        with destino.open(encoding="utf-8", newline="") as entrada:
            filas = list(csv.DictReader(entrada))
        vacio = ""
    elif formato == "jsonl":
        filas = [json.loads(linea) for linea in destino.read_text(encoding="utf-8").splitlines()]
        vacio = None
    else:
        tabla = pq.read_table(destino)
        assert tabla.schema.field("valor").type == pa.decimal128(38, 10)
        filas = tabla.to_pylist()
        vacio = None
    assert len(filas) == publicados
    suprimidas = [f for f in filas if f["estado_valor"] == "SUPRIMIDO"]
    assert suprimidas
    for f in suprimidas:
        assert (f["valor"], f["numerador"], f["denominador"], f["poblacion_cuentas"]) == (
            vacio,
            vacio,
            vacio,
            vacio,
        )
    (neta,) = [
        f
        for f in filas
        if f["metrica"] == "recuperacion.neta"
        and f["vista"] == "ACUMULADA"
        and f["dimension_set"] == "TOTAL"
    ]
    assert str(neta["valor"]).startswith("2270.00")
    if formato != "parquet":
        _sin_datos_de_una_cuenta(destino.read_text(encoding="utf-8"))


# --- el backfill ----------------------------------------------------------------------------------


def _backfill(g, *extra):
    return cli.invoke(
        app,
        [
            "backfill-analytics",
            "--modo",
            "BACKTEST",
            "--granularidad",
            "TOTAL",
            "--zona",
            ZONA,
            "--known-at",
            g.k2.isoformat(),
            *extra,
        ],
    )


def test_el_backfill_no_inventa_el_horizonte(g):
    resultado = _backfill(g, "--dry-run")
    assert resultado.exit_code == 2
    assert "declara --as-of o --horizonte" in resultado.output
    ambos = _backfill(g, "--as-of", "2026-10-04", "--horizonte", "5", "--dry-run")
    assert ambos.exit_code == 2


def test_el_backfill_pide_solo_lo_que_falta(g, trabajar):
    _cola_vacia()
    antes = _ejecuciones()

    simulado = _backfill(g, "--horizonte", "5", "--dry-run")

    assert simulado.exit_code == 0, simulado.output
    assert "Ejecuciones pendientes: 2" in simulado.output
    assert "corte 2026-09-23 al 2026-09-27" in simulado.output
    assert "corte 2026-09-30 al 2026-10-04" in simulado.output
    assert "Con --dry-run no se encolo nada." in simulado.output
    assert _ejecuciones() == antes

    hecho = _backfill(g, "--horizonte", "5")

    assert hecho.exit_code == 0, hecho.output
    assert "Se encolaron 2 trabajos ANALYTICS" in hecho.output
    procesados = trabajar()
    assert [(p.tipo, p.estado) for p in procesados] == [("ANALYTICS", "COMPLETADO")] * 2
    with sesion() as s:
        estados = s.execute(
            text("SELECT estado FROM ejecucion_analytics ORDER BY id DESC LIMIT 2")
        ).scalars()
        assert list(estados) == ["EXITOSA", "EXITOSA"]
    otra_vez = _backfill(g, "--horizonte", "5", "--dry-run")
    assert "ya analizados: 2" in otra_vez.output
    assert "Ejecuciones pendientes: 0" in otra_vez.output


# --- lo que se rechaza ----------------------------------------------------------------------------


def test_lo_que_el_cli_de_analytics_rechaza(g, tmp_path):
    destino = tmp_path / "m.json"
    sin_frontera = cli.invoke(
        app,
        [
            "construir-manifiesto-analytics",
            "--corte",
            CORTE.isoformat(),
            "--as-of",
            AS_OF_A.isoformat(),
            "--modo",
            "BACKTEST",
            "--zona",
            ZONA,
            "--granularidad",
            "DIA",
            "--destino",
            str(destino),
        ],
    )
    assert sin_frontera.exit_code == 2
    assert "--known-at" in sin_frontera.output
    sin_zona = _construir(g.k1.replace(tzinfo=None), AS_OF_A, destino)
    assert sin_zona.exit_code == 2
    familia = _construir(g.k1, AS_OF_A, destino, "--sin", "PRONOSTICO")
    assert familia.exit_code == 2
    assert not destino.exists()
    malo = tmp_path / "malo.json"
    malo.write_text('{"corte_id": "x", "ultimo": true}', encoding="utf-8")
    assert cli.invoke(app, ["analizar-cobranza", "--manifiesto", str(malo)]).exit_code == 2
    for comando in (
        ["resumen-analytics", "--ejecucion", NO_EXISTE],
        ["exportar-analytics", "--ejecucion", NO_EXISTE, "--destino", str(tmp_path / "x.csv")],
    ):
        assert cli.invoke(app, comando).exit_code == 1
    formato = cli.invoke(app, ["exportar-analytics", "--ejecucion", NO_EXISTE, "--formato", "xls"])
    assert formato.exit_code == 2


def test_una_familia_ambigua_no_se_elige_y_se_puede_declarar(g, tmp_path):
    # Una segunda decision/v2 del mismo corte: a esta frontera hay dos candidatas igual de validas.
    otra = decidir(CORTE, backtest=True)
    with sesion() as s:
        frontera = s.execute(text("SELECT now()")).scalar_one()
    destino = tmp_path / "ambiguo.json"

    resultado = _construir(frontera, AS_OF_A, destino)

    assert resultado.exit_code == 1
    assert "DECISION_V2: AMBIGUA" in resultado.output
    assert f"candidata {otra.decision_run_id}" in resultado.output
    assert f"candidata {g.operacion.decision.decision_run_id}" in resultado.output
    assert not destino.exists()

    declarada = _construir(
        frontera,
        AS_OF_A,
        destino,
        "--decision-run",
        str(g.operacion.decision.decision_run_id),
    )

    assert declarada.exit_code == 0, declarada.output
    assert f"DECISION_V2: {g.operacion.decision.decision_run_id} (DECLARADA" in declarada.output
    cuerpo = json.loads(destino.read_text(encoding="utf-8"))["manifiesto"]
    assert cuerpo["decision_run_id"] == str(g.operacion.decision.decision_run_id)
    assert cuerpo["campo_run_id"] == str(g.operacion.plan.campo_run_id)
