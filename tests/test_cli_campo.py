"""La configuracion de campo y campo/v2 por la linea de comandos: generar una configuracion
sintetica, cargarla en JSON o en JSONL (el mismo contenido se reutiliza), planear con todas sus
entradas declaradas (la fecha de operacion nunca se infiere), resumir y exportar un plan, y el
backfill, que solo pide lo que falta y no es ambiguo. Ninguna salida lleva una coordenada."""

from __future__ import annotations

import csv
import json
import re

import pytest
from campo_v2_escenarios import (
    AMOZOC,
    CAMPO,
    DIGITAL,
    MERIDA,
    PUEBLA,
    X0,
    Y0,
    CuentaCampo,
    base,
    decision_mixta,
    escenario,
    gestor,
    publicar_campo,
)
from sqlalchemy import text
from territorial_v2_escenarios import publicar_bases
from typer.testing import CliRunner

from motor_cartera.cli import app
from motor_cartera.db.sesion import sesion

pytestmark = pytest.mark.usefixtures("bd")

cli = CliRunner()
CORTE = "2026-09-30"
JUEVES = "2026-10-01"
CUENTAS = [
    CuentaCampo(1, CAMPO, PUEBLA, X0, Y0),
    CuentaCampo(2, CAMPO, PUEBLA, X0 + 100, Y0 - 100),
    CuentaCampo(3, CAMPO, PUEBLA, X0 + 1_000, Y0),
    CuentaCampo(4, CAMPO, PUEBLA, X0 + 5_000, Y0),
    CuentaCampo(5, CAMPO, AMOZOC, 2_915_500, 790_500),
    CuentaCampo(6, CAMPO, MERIDA, 3_780_500, 1_050_500),
    CuentaCampo(7, DIGITAL, PUEBLA, X0, Y0),
]


# Dentro de cada prueba, no al importar: base() pasa su punto a EPSG:4326 con PostGIS, y al
# recolectar las pruebas la base de pruebas todavia puede no existir.
def _bases() -> tuple[dict, ...]:
    return base("BASE_A", X0 - 3_000, Y0, [PUEBLA]), base("BASE_B", 2_913_600, 790_500, [AMOZOC])


NO_EXISTE = "00000000-0000-4000-8000-000000000000"
COORDENADA = re.compile(r"-?\d{1,3}\.\d{5,}")
"""Una latitud o una longitud con sus decimales."""
PALABRAS_DE_UBICACION = ("latitud", "longitud", "lat=", "lon=", "grid_", "bbox", "centroide")


def _id(salida: str, llave: str) -> str:
    encontrado = re.search(rf"^{llave} ([0-9a-f-]{{36}})$", salida, re.MULTILINE)
    assert encontrado, salida
    return encontrado.group(1)


def _sin_coordenadas(salida: str) -> None:
    assert not COORDENADA.search(salida), salida
    for palabra in PALABRAS_DE_UBICACION:
        assert palabra not in salida.lower(), palabra


def _planes() -> list[tuple[str, str]]:
    with sesion() as s:
        return [
            (estado, fecha.isoformat())
            for estado, fecha in s.execute(
                text("SELECT estado, fecha_operacion FROM ejecucion_campo_v2 ORDER BY id")
            ).all()
        ]


def _configuraciones() -> int:
    with sesion() as s:
        return s.execute(text("SELECT count(*) FROM version_configuracion_campo")).scalar_one()


def test_generar_cargar_planear_resumir_y_exportar_por_la_linea_de_comandos(tmp_path):
    e = escenario(tmp_path, CUENTAS, _bases())
    documento = tmp_path / "campo" / "campo.json"
    generar = [
        "generar-campo-sintetico",
        "--bases",
        str(e.bases.bases_version_id),
        "--gestores-por-base",
        "1",
        "--base",
        "BASE_A=2",
        "--capacidades",
        "1,2",
        "--inactivos",
        "0",
        "--festivos",
        "2026-11-16",
        "--semilla",
        "11",
    ]

    generada = cli.invoke(app, [*generar, "--destino", str(documento)])
    otra_vez = cli.invoke(app, [*generar, "--destino", str(tmp_path / "otra.json")])

    assert generada.exit_code == 0, generada.output
    assert "3 gestores en 2 bases; semilla 11" in generada.output
    assert otra_vez.exit_code == 0
    assert documento.read_bytes() == (tmp_path / "otra.json").read_bytes()
    contenido = json.loads(documento.read_text(encoding="utf-8"))
    assert [(g["codigo"], g["base"]) for g in contenido["gestores"]] == [
        ("GESTOR_0001", "BASE_A"),
        ("GESTOR_0002", "BASE_A"),
        ("GESTOR_0003", "BASE_B"),
    ]

    # Cargarla publica una version; el mismo contenido en JSONL, en otro orden y con otro nombre,
    # es la misma version.
    cargada = cli.invoke(app, ["cargar-campo", str(documento)])
    assert cargada.exit_code == 0, cargada.output
    assert "publicada" in cargada.output and "3 gestores (3 activos)" in cargada.output
    configuracion = _id(cargada.output, "configuracion_campo_version_id")
    jsonl = tmp_path / "campo" / "campo.jsonl"
    registros = [
        {
            "registro": "configuracion",
            "nombre": "La misma, en JSONL",
            "bases_version_id": contenido["bases_version_id"],
            "zona_horaria": contenido["zona_horaria"],
        },
        *({"registro": "gestor", **g} for g in reversed(contenido["gestores"])),
        *({"registro": "calendario", **c} for c in contenido["calendarios"]),
    ]
    jsonl.write_text("\n".join(json.dumps(r) for r in registros) + "\n", encoding="utf-8")
    misma = cli.invoke(app, ["cargar-campo", str(jsonl)])
    assert misma.exit_code == 0, misma.output
    assert "ya existia: se reutiliza" in misma.output
    assert _id(misma.output, "configuracion_campo_version_id") == configuracion
    assert _configuraciones() == 1

    # Planear exige la fecha de operacion; con ella publica, y la misma peticion otra vez no.
    planear = [
        "planificar-campo-v2",
        "--corte",
        CORTE,
        "--decision-run",
        str(e.decision.decision_run_id),
        "--territorial-run",
        str(e.territorial.territorial_run_id),
        "--configuracion",
        configuracion,
    ]
    sin_fecha = cli.invoke(app, planear)
    planeado = cli.invoke(app, [*planear, "--fecha-operacion", JUEVES])
    repetido = cli.invoke(app, [*planear, "--fecha-operacion", JUEVES])

    assert sin_fecha.exit_code == 2
    assert "no infiere la fecha de operacion: declarala con --fecha-operacion" in sin_fecha.output
    assert planeado.exit_code == 0, planeado.output
    assert ": EXITOSA" in planeado.output and "campo-public-v2" in planeado.output
    plan = _id(planeado.output, "campo_run_id")
    assert repetido.exit_code == 1 and "CampoV2YaGenerado" in repetido.output
    assert _planes() == [("EXITOSA", JUEVES)]

    resumen = cli.invoke(app, ["resumen-campo-v2", "--plan", plan])
    assert resumen.exit_code == 0, resumen.output
    assert "6 candidatas de 7 cuentas" in resumen.output
    assert "1 en revision territorial" in resumen.output
    assert "BASE_A: 4 candidatas" in resumen.output and "BASE_B: 1 candidatas" in resumen.output
    assert "Capacidad declarada, no factibilidad vial" in resumen.output

    csv_ = tmp_path / "plan.csv"
    exportado = cli.invoke(app, ["exportar-plan-campo", "--plan", plan, "--destino", str(csv_)])
    assert exportado.exit_code == 0, exportado.output
    with csv_.open(encoding="utf-8", newline="") as archivo:
        filas = list(csv.DictReader(archivo))
    assert list(filas[0]) == [
        "cuenta_id",
        "estado",
        "prioridad",
        "orden_prioridad",
        "base",
        "gestor",
        "zona_id",
        "subzona_id",
        "tramo_id",
        "microtramo_id",
        "es_rebalanceada",
    ]
    assert len(filas) == 6
    assert [f["estado"] for f in filas].count("REVISION_TERRITORIAL") == 1
    asignadas = [f for f in filas if f["estado"] == "ASIGNADA"]
    assert asignadas and all(f["gestor"].startswith("GESTOR_") for f in asignadas)
    for salida in (
        generada.output,
        cargada.output,
        planeado.output,
        resumen.output,
        exportado.output,
        csv_.read_text(encoding="utf-8"),
    ):
        _sin_coordenadas(salida)


def test_el_backfill_pide_solo_lo_que_falta_y_no_inventa_entradas(tmp_path, trabajar):
    e = escenario(tmp_path, CUENTAS, _bases())
    version = publicar_campo(
        e.bases, [gestor("GESTOR_001", capacidad=2), gestor("GESTOR_002", "BASE_B", 1)]
    )
    backfill = ["backfill-campo-v2", "--configuracion", str(version.configuracion_campo_version_id)]

    sin_fecha = cli.invoke(app, backfill)
    revisado = cli.invoke(app, [*backfill, "--fecha-operacion", JUEVES, "--dry-run"])
    tras_revisar = _planes()
    encolado = cli.invoke(app, [*backfill, "--fecha-operacion", JUEVES])
    procesados = trabajar()
    otra_vez = cli.invoke(app, [*backfill, "--fecha-operacion", JUEVES])
    antes_del_corte = cli.invoke(app, [*backfill, "--fecha-operacion", "2026-09-29"])
    otro_corte = cli.invoke(app, [*backfill, "--fecha-operacion", JUEVES, "--corte", "2026-09-29"])

    assert sin_fecha.exit_code == 2 and "no infiere la fecha de operacion" in sin_fecha.output
    assert revisado.exit_code == 0, revisado.output
    assert "Planes pendientes: 1" in revisado.output
    assert f"{CORTE}: decision {e.decision.decision_run_id}" in revisado.output
    assert "Con --dry-run no se encolo nada." in revisado.output
    assert tras_revisar == []
    assert encolado.exit_code == 0, encolado.output
    assert "Se encolaron 1 trabajos CAMPO_V2" in encolado.output
    # El worker toma tambien lo que el escenario dejo en la cola; de campo/v2, solo ese trabajo.
    campo = [(p.estado, p.intentos) for p in procesados if p.tipo == "CAMPO_V2"]
    assert campo == [("COMPLETADO", 1)]
    assert _planes() == [("EXITOSA", JUEVES)]
    assert "ya planeados: 1" in otra_vez.output and "Planes pendientes: 0" in otra_vez.output
    assert "Se encolaron 0 trabajos" in otra_vez.output
    assert "ya planeados: 0" in antes_del_corte.output
    assert "Planes pendientes: 0" in antes_del_corte.output
    assert "Planes pendientes: 0" in otro_corte.output and "ya planeados: 0" in otro_corte.output

    # Una segunda decision/v2 EXITOSA del mismo corte lo vuelve ambiguo: se reporta y no se planea.
    decision_mixta(e.decision, {1: "MUY_ALTA"})
    ambiguo = cli.invoke(app, [*backfill, "--fecha-operacion", "2026-10-02"])

    assert ambiguo.exit_code == 0, ambiguo.output
    assert "cortes ambiguos (no se planean): 1" in ambiguo.output
    assert f"{CORTE}: 2 decisiones v2 y 1 territoriales v2 EXITOSA" in ambiguo.output
    assert "Planes pendientes: 0" in ambiguo.output
    assert len(_planes()) == 1


def test_lo_que_el_cli_de_campo_rechaza(tmp_path):
    bases = publicar_bases(*_bases())
    generar = ["generar-campo-sintetico", "--bases", str(bases.bases_version_id)]
    invalido = tmp_path / "invalido.json"
    invalido.write_text(
        json.dumps(
            {
                "nombre": "Invalida",
                "bases_version_id": str(bases.bases_version_id),
                "zona_horaria": "Marte/Olimpo",
                "calendarios": [
                    {"codigo": "LV", "semana": {"LUNES": [{"inicio": "13:00", "fin": "09:00"}]}}
                ],
                "gestores": [gestor("GESTOR_001", capacidad=0, calendario="LV")],
            }
        ),
        encoding="utf-8",
    )
    roto = tmp_path / "roto.json"
    roto.write_text("{nombre: sin comillas}", encoding="utf-8")

    sin_bases = cli.invoke(app, ["generar-campo-sintetico", "--bases", NO_EXISTE])
    base_ajena = cli.invoke(app, [*generar, "--base", "BASE_Z=2", "--destino", str(tmp_path / "x")])
    sin_cupos = cli.invoke(
        app, [*generar, "--capacidades", "0,5", "--destino", str(tmp_path / "y")]
    )
    mal = cli.invoke(app, ["cargar-campo", str(invalido)])
    no_json = cli.invoke(app, ["cargar-campo", str(roto)])
    sin_corte = cli.invoke(
        app,
        [
            "planificar-campo-v2",
            "--corte",
            CORTE,
            "--decision-run",
            NO_EXISTE,
            "--territorial-run",
            NO_EXISTE,
            "--configuracion",
            NO_EXISTE,
            "--fecha-operacion",
            JUEVES,
        ],
    )
    sin_plan = cli.invoke(app, ["resumen-campo-v2", "--plan", NO_EXISTE])
    sin_exportar = cli.invoke(app, ["exportar-plan-campo", "--plan", NO_EXISTE])
    sin_configuracion = cli.invoke(
        app, ["backfill-campo-v2", "--configuracion", NO_EXISTE, "--fecha-operacion", JUEVES]
    )

    assert sin_bases.exit_code == 1 and f"No existe la version de bases {NO_EXISTE}" in (
        sin_bases.output
    )
    assert base_ajena.exit_code == 2 and "--base BASE_Z=2" in base_ajena.output
    assert sin_cupos.exit_code == 2 and "Cada cupo es un entero positivo" in sin_cupos.output
    # Fail-closed: todos sus problemas a la vez, y nada publicado.
    assert mal.exit_code == 1
    for problema in ("Marte/Olimpo", "13:00", "capacidad_cuentas_dia"):
        assert problema in mal.output, problema
    assert no_json.exit_code == 1 and "no es JSON" in no_json.output
    assert _configuraciones() == 0
    assert sin_corte.exit_code == 1 and "No hay un corte canonico del 2026-09-30" in (
        sin_corte.output
    )
    assert sin_plan.exit_code == 1 and f"No existe el plan {NO_EXISTE}" in sin_plan.output
    assert sin_exportar.exit_code == 1
    assert sin_configuracion.exit_code == 1
    assert f"No existe la configuracion de campo {NO_EXISTE}" in sin_configuracion.output
