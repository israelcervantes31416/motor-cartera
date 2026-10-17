"""optimization/v1 por la linea de comandos, con el golden en la base (optimizacion_escenarios):
registrar una configuracion, optimizar con todas las entradas declaradas, resumir, exportar las
asignaciones y el plan agregado en CSV, Parquet y JSONL, diagnosticar lo que no publico y comparar
dos planes. Ninguna salida lleva un dato personal ni una coordenada, y ningun comando ejecuta una
accion."""

from __future__ import annotations

import csv
import json
import re
from decimal import Decimal

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from optimizacion_escenarios import (
    GOLDEN,
    documento_golden,
    escenario_golden,
    restriccion,
    resultados,
)
from sqlalchemy import func, text, update
from typer.testing import CliRunner

from motor_cartera.cli import app
from motor_cartera.db.modelos import EstadoTrabajo, TrabajoOrquestacion
from motor_cartera.db.sesion import sesion

pytestmark = pytest.mark.usefixtures("bd_del_modulo")

cli = CliRunner()
NO_EXISTE = "00000000-0000-4000-8000-000000000000"
CLIENTE_UNICO = re.compile(r"CU\d{8}")
COLUMNAS_PROHIBIDAS = {"cliente_unico", "nombre", "direccion", "telefono", "actor_ref"}
COLUMNAS_PROHIBIDAS |= {"latitud", "longitud", "lat", "lon", "geometria"}


def _id(salida: str, llave: str) -> str:
    encontrado = re.search(rf"^{llave} ([0-9a-f-]{{36}})$", salida, re.MULTILINE)
    assert encontrado, salida
    return encontrado.group(1)


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


@pytest.fixture(scope="module")
def g(bd_del_modulo, tmp_path_factory):
    e = escenario_golden(tmp_path_factory.mktemp("cli_optimizacion"))
    _cola_vacia()
    return e


def _sin_datos_personales(texto: str) -> None:
    assert not CLIENTE_UNICO.search(texto)
    for columna in ("actor_ref", "latitud", "longitud", "telefono", "direccion"):
        assert columna not in texto, columna


def _configuracion(tmp_path, documento: dict, *, publicar: bool = True) -> str:
    ruta = tmp_path / "configuracion.json"
    ruta.write_text(json.dumps(documento), encoding="utf-8")
    r = cli.invoke(app, ["crear-config-optimizacion", str(ruta), *(["--publicar"] * publicar)])
    assert r.exit_code == 0, r.output
    return _id(r.output, "configuracion_id")


def _optimizar(g, configuracion: str, *extra: str):
    return cli.invoke(
        app,
        [
            "optimizar-cartera",
            "--scoring",
            str(g.scoring.scoring_run_id),
            "--decision",
            str(g.decision.decision_run_id),
            "--configuracion",
            configuracion,
            "--fecha-operacion",
            "2026-10-01",
            *extra,
        ],
    )


@pytest.fixture(scope="module")
def optimizada(g, tmp_path_factory):
    configuracion = _configuracion(tmp_path_factory.mktemp("cfg"), documento_golden())
    r = _optimizar(g, configuracion)
    assert r.exit_code == 0, r.output
    assert "EXITOSA (PLAN_PUBLICADO)" in r.output and "31,000 centavos" in r.output
    _sin_datos_personales(r.output)
    return _id(r.output, "optimizacion_run_id")


def test_crear_una_configuracion(g, tmp_path):
    documento = documento_golden(max_unidades_recurso=91)
    ruta = tmp_path / "c.json"
    ruta.write_text(json.dumps(documento), encoding="utf-8")
    r = cli.invoke(app, ["crear-config-optimizacion", str(ruta)])
    assert r.exit_code == 0, r.output
    assert "registrada (BORRADOR)" in r.output and "supuestos: SUPUESTOS SINTETICOS" in r.output
    otra = cli.invoke(app, ["crear-config-optimizacion", str(ruta), "--publicar"])
    assert (
        otra.exit_code == 0 and "ya existia: se reutiliza y se publico (PUBLICADA)" in otra.output
    )
    assert _id(otra.output, "configuracion_id") == _id(r.output, "configuracion_id")
    ruta.write_text(json.dumps(documento_golden(pools=[])), encoding="utf-8")
    invalida = cli.invoke(app, ["crear-config-optimizacion", str(ruta)])
    assert invalida.exit_code == 1 and "pools: una lista con al menos uno" in invalida.output
    ruta.write_text("{no es json", encoding="utf-8")
    assert cli.invoke(app, ["crear-config-optimizacion", str(ruta)]).exit_code == 1


def test_optimizar_exige_cada_entrada(g, tmp_path, optimizada):
    configuracion = _configuracion(tmp_path, documento_golden())
    otra_vez = _optimizar(g, configuracion)
    assert otra_vez.exit_code == 1 and "Ese plan ya se publico" in otra_vez.output
    sin_fecha = cli.invoke(
        app,
        [
            "optimizar-cartera",
            "--scoring",
            str(g.scoring.scoring_run_id),
            "--decision",
            str(g.decision.decision_run_id),
            "--configuracion",
            configuracion,
        ],
    )
    assert sin_fecha.exit_code == 2 and "no infiere la fecha de operacion" in sin_fecha.output
    borrador = _configuracion(tmp_path, documento_golden(max_unidades_recurso=92), publicar=False)
    r = _optimizar(g, borrador)
    assert r.exit_code == 1 and "CONFIGURACION_NO_PUBLICADA" in r.output
    r = _optimizar(g, configuracion, "--ruteo", NO_EXISTE)
    assert r.exit_code == 1 and "RUTEO_NO_ENCONTRADO" in r.output


def test_el_resumen(optimizada):
    r = cli.invoke(app, ["resumen-optimizacion", "--ejecucion", optimizada])
    assert r.exit_code == 0, r.output
    for esperado in (
        "EXITOSA (PLAN_PUBLICADO)",
        "universo 9, elegibles 7",
        "nivel 1 (MAXIMIZAR): OPTIMAL, objetivo 31,000",
        "nivel 4 (MINIMIZAR): OPTIMAL",
        "estados: ASIGNADA 5, NO_SELECCIONADA 2, NO_ELEGIBLE 2",
        "TELEFONICA: 1 pools, capacidad 2, utilizada 2",
        "declaradas activas ['MIN_AUTOMOTRIZ']",
        "hash del plan",
        "No demuestra que el canal asignado cause el resultado predicho",
    ):
        assert esperado in r.output, esperado
    _sin_datos_personales(r.output)
    r = cli.invoke(app, ["resumen-optimizacion", "--ejecucion", NO_EXISTE])
    assert r.exit_code == 1


@pytest.mark.parametrize("formato", ["csv", "parquet", "jsonl"])
def test_exportar_las_asignaciones(g, optimizada, tmp_path, formato):
    destino = tmp_path / f"plan.{formato}"
    r = cli.invoke(
        app,
        [
            "exportar-optimizacion",
            "--ejecucion",
            optimizada,
            "--formato",
            formato,
            "--destino",
            str(destino),
        ],
    )
    assert r.exit_code == 0, r.output
    assert "9 filas de asignaciones" in r.output
    if formato == "csv":
        with destino.open(encoding="utf-8") as archivo:
            filas = list(csv.DictReader(archivo))
    elif formato == "jsonl":
        filas = [json.loads(linea) for linea in destino.read_text(encoding="utf-8").splitlines()]
    else:
        tabla = pq.read_table(destino)
        assert tabla.schema.field("p_contacto_14d").type == pa.decimal128(9, 8)
        assert tabla.schema.field("valor_oportunidad_centavos").type == pa.int64()
        filas = tabla.to_pylist()
    assert len(filas) == 9 and not COLUMNAS_PROHIBIDAS & set(filas[0])
    _sin_datos_personales(destino.read_bytes().decode("latin-1"))
    publicados = {str(x["cuenta_id"]): n for n, x in resultados(_ejecucion_id(optimizada)).items()}
    por_numero = {publicados[str(f["cuenta_id"])]: f for f in filas}
    uno = por_numero[1]
    assert (uno["estado"], uno["pool"], uno["canal_asignado"]) == (
        "ASIGNADA",
        "DIGITAL_GLOBAL",
        "DIGITAL",
    )
    for c in GOLDEN:
        fila = por_numero[c.numero]
        assert int(fila["valor_oportunidad_centavos"]) == int(Decimal(c.valor) * 100)
        assert Decimal(str(fila["p_contacto_14d"])) == Decimal(c.contacto)
        if formato != "parquet":
            assert str(fila["p_contacto_14d"]) == c.contacto  # exacto, nunca 5E-1
    assert por_numero[7]["motivo"] == "SIN_CANAL_ADMISIBLE"
    assert por_numero[7]["pool"] in (None, "")
    assert str(por_numero[2]["retenida"]).lower() == "true"


def _ejecucion_id(run_id: str) -> int:
    with sesion() as s:
        return s.execute(
            text("SELECT id FROM ejecucion_optimizacion WHERE optimizacion_run_id = :r"),
            {"r": run_id},
        ).scalar_one()


@pytest.mark.parametrize("formato", ["csv", "parquet", "jsonl"])
@pytest.mark.parametrize("contenido", ["pools", "restricciones"])
def test_exportar_el_plan_agregado(optimizada, tmp_path, formato, contenido):
    destino = tmp_path / f"{contenido}.{formato}"
    r = cli.invoke(
        app,
        [
            "exportar-optimizacion",
            "--ejecucion",
            optimizada,
            "--formato",
            formato,
            "--contenido",
            contenido,
            "--destino",
            str(destino),
        ],
    )
    assert r.exit_code == 0, r.output
    if formato == "csv":
        with destino.open(encoding="utf-8") as archivo:
            filas = list(csv.DictReader(archivo))
    elif formato == "jsonl":
        filas = [json.loads(linea) for linea in destino.read_text(encoding="utf-8").splitlines()]
    else:
        filas = pq.read_table(destino).to_pylist()
    if contenido == "pools":
        usos = {f["pool"]: f for f in filas}
        assert set(usos) == {"DIGITAL_GLOBAL", "TELEFONICA_GLOBAL"}
        assert int(usos["TELEFONICA_GLOBAL"]["unidades_consumidas"]) == 8
        assert str(usos["DIGITAL_GLOBAL"]["utilizacion"]) == "1.000000"
    else:
        codigos = {f["codigo"]: f for f in filas}
        assert "MIN_AUTOMOTRIZ" in codigos and "POOL:DIGITAL_GLOBAL" in codigos
        assert str(codigos["MIN_AUTOMOTRIZ"]["binding"]).lower() == "true"


def test_exportar_rechaza_lo_que_no_conoce(optimizada, tmp_path):
    for opciones in (["--formato", "xlsx"], ["--contenido", "cuentas"]):
        r = cli.invoke(app, ["exportar-optimizacion", "--ejecucion", optimizada, *opciones])
        assert r.exit_code == 2, r.output


def test_diagnosticar(g, tmp_path, optimizada):
    r = cli.invoke(app, ["diagnosticar-optimizacion", "--ejecucion", optimizada])
    assert r.exit_code == 0 and "Plan publicado: hash" in r.output
    documento = documento_golden(
        restricciones=[restriccion("MIN_AUTOMOTRIZ", "MIN_CUENTAS", "PRODUCTO", "AUTOMOTRIZ", 4)]
    )
    infactible = _optimizar(g, _configuracion(tmp_path, documento))
    assert infactible.exit_code == 1 and "FALLIDA (INFACTIBLE)" in infactible.output
    run_id = _id(infactible.output, "optimizacion_run_id")
    r = cli.invoke(app, ["diagnosticar-optimizacion", "--ejecucion", run_id])
    assert r.exit_code == 0, r.output
    assert "ERROR MINIMO_SUPERA_ELEGIBLES" in r.output and "Sin plan publicado" in r.output
    r = cli.invoke(app, ["exportar-optimizacion", "--ejecucion", run_id])
    assert r.exit_code == 1 and "no publico un plan" in r.output


def test_comparar_dos_planes(g, tmp_path, optimizada):
    sin_cobertura = _optimizar(g, _configuracion(tmp_path, documento_golden(restricciones=[])))
    assert sin_cobertura.exit_code == 0, sin_cobertura.output
    otra = _id(sin_cobertura.output, "optimizacion_run_id")
    r = cli.invoke(app, ["comparar-optimizaciones", "--a", optimizada, "--b", otra])
    assert r.exit_code == 0, r.output
    assert "entradas distintas: configuracion" in r.output
    assert "valor_oportunidad_centavos: A 31,000, B 35,000 (B - A +4,000)" in r.output
    # Con la cobertura entra la 9 (AUTOMOTRIZ); sin ella, la 5 en su lugar.
    assert "solo en A 1, solo en B 1" in r.output
    r = cli.invoke(app, ["comparar-optimizaciones", "--a", optimizada, "--b", NO_EXISTE])
    assert r.exit_code == 1


def test_ningun_comando_ejecuta_una_accion():
    r = cli.invoke(app, ["--help"], terminal_width=200)
    comandos = re.findall(r"^\W*([a-z][a-z0-9-]+)\s", r.output, re.MULTILINE)
    for prohibido in ("ejecutar-plan", "enviar", "llamar", "visitar", "aplicar"):
        assert not any(prohibido in c for c in comandos), prohibido
