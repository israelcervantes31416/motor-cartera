"""La geografia, las bases y territorial/v2 por la linea de comandos: la referencia sintetica de la
prueba de humo, importarla, muestrear sus puntos, generar una cartera con coordenadas, resolver su
geografia, publicar bases y asignar, y lo que cada comando rechaza. Ninguna salida lleva una
coordenada de una cuenta."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from motor_cartera.cli import app

pytestmark = pytest.mark.usefixtures("bd")

cli = CliRunner()
CORTE = "2026-09-30"
BASES = {
    "nombre": "Bases sinteticas de la CLI",
    "bases": [
        {
            "codigo": "BASE_NORTE",
            "nombre": "Base norte (sintetica)",
            "lat": 18.5,
            "lon": -99.5,
            "activa": True,
            "municipios": ["21114", "21156"],
        },
        {
            "codigo": "BASE_SUR",
            "nombre": "Base sur (sintetica)",
            "lat": 18.2,
            "lon": -99.2,
            "activa": True,
            "municipios": ["21156"],
        },
    ],
}
COORDENADA = re.compile(r"-?\d{1,3}\.\d{6}")
"""Como escribe el generador una coordenada: seis decimales."""


def _id(salida: str, llave: str) -> str:
    encontrado = re.search(rf"^{llave} ([0-9a-f-]{{36}})$", salida, re.MULTILINE)
    assert encontrado, salida
    return encontrado.group(1)


def _referencia(tmp_path: Path) -> tuple[Path, str]:
    zip_ = tmp_path / "geo" / "municipios.zip"
    escrita = cli.invoke(
        app, ["generar-geografia-sintetica", "--destino", str(zip_), "--entidades", "21"]
    )
    assert escrita.exit_code == 0, escrita.output
    importada = cli.invoke(
        app,
        [
            "importar-geografia",
            str(zip_),
            "--proveedor",
            "SINTETICO",
            "--producto",
            "Referencia sintetica de la CLI",
            "--edicion",
            "cli-v1",
        ],
    )
    assert importada.exit_code == 0, importada.output
    return zip_, _id(importada.output, "geografia_version_id")


def test_el_flujo_completo_por_la_linea_de_comandos(tmp_path, trabajar):
    zip_, version_id = _referencia(tmp_path)

    puntos = tmp_path / "geo" / "puntos.json"
    argumentos = ["muestrear-puntos", "--geografia-version", version_id, "--destino", str(puntos)]
    muestra = cli.invoke(app, [*argumentos, "--por-municipio", "3", "--fuera", "5"])
    assert muestra.exit_code == 0, muestra.output
    datos = json.loads(puntos.read_text(encoding="ascii"))
    assert len(datos["fuera"]) == 5 and datos["geografia_version_id"] == version_id
    assert all(1 <= len(p) <= 3 for p in datos["municipios"].values())
    # La misma version y la misma semilla: los mismos bytes.
    antes = puntos.read_bytes()
    assert cli.invoke(app, [*argumentos, "--por-municipio", "3", "--fuera", "5"]).exit_code == 0
    assert puntos.read_bytes() == antes

    generada = cli.invoke(
        app,
        [
            "generar-oficial",
            "--destino",
            str(tmp_path / "oficial"),
            "--n",
            "120",
            "--formato",
            "csv",
            "--fecha-corte",
            CORTE,
            "--semilla",
            "3",
            "--no-pagos",
            "--puntos",
            str(puntos),
        ],
    )
    assert generada.exit_code == 0, generada.output
    assert "coordenadas: validas" in generada.output
    cargada = cli.invoke(
        app,
        [
            "cargar",
            str(tmp_path / "oficial" / f"cartera_oficial_{CORTE}.csv"),
            "--contrato",
            "cartera/v2",
            "--fecha-corte",
            CORTE,
        ],
    )
    assert cargada.exit_code == 0, cargada.output
    trabajar()

    geografia = cli.invoke(
        app, ["geolocalizar", "--corte", CORTE, "--geografia-version", version_id]
    )
    assert geografia.exit_code == 0, geografia.output
    assert "EXITOSA" in geografia.output and "Ninguna coordenada se corrigio" in geografia.output
    assert not COORDENADA.search(geografia.output)
    geografia_run = _id(geografia.output, "geografia_run_id")

    archivo = tmp_path / "bases.json"
    archivo.write_text(json.dumps(BASES), encoding="utf-8")
    bases = cli.invoke(app, ["cargar-bases", str(archivo)])
    assert bases.exit_code == 0, bases.output
    assert "publicada" in bases.output and "2 bases (2 activas), 3 coberturas" in bases.output
    bases_id = _id(bases.output, "bases_version_id")

    argumentos = [
        "asignar-territorial-v2",
        "--corte",
        CORTE,
        "--geografia-run",
        geografia_run,
        "--bases",
        bases_id,
    ]
    asignada = cli.invoke(app, argumentos)
    assert asignada.exit_code == 0, asignada.output
    assert "EXITOSA" in asignada.output and "territorial-public-v2" in asignada.output
    _id(asignada.output, "territorial_run_id")
    repetida = cli.invoke(app, argumentos)
    assert repetida.exit_code == 1 and "TerritorialV2YaGenerado" in repetida.output

    # La misma referencia no se importa dos veces: dice cual es la que ya existe.
    otra_vez = cli.invoke(
        app,
        [
            "importar-geografia",
            str(zip_),
            "--proveedor",
            "SINTETICO",
            "--producto",
            "Otra",
            "--edicion",
            "otra",
        ],
    )
    assert otra_vez.exit_code == 1
    assert _id(otra_vez.output, "geografia_version_id") == version_id


def test_importar_sin_metadatos_ni_referencia_del_manifiesto_se_niega(tmp_path):
    zip_ = tmp_path / "municipios.zip"
    assert (
        cli.invoke(
            app, ["generar-geografia-sintetica", "--destino", str(zip_), "--entidades", "21"]
        ).exit_code
        == 0
    )

    sin_metadatos = cli.invoke(app, ["importar-geografia", str(zip_), "--proveedor", "SINTETICO"])
    otro_archivo = cli.invoke(
        app, ["importar-geografia", str(zip_), "--referencia", "inegi-mg-2025-municipal"]
    )

    assert sin_metadatos.exit_code == 2
    assert "--proveedor, --producto y --edicion" in sin_metadatos.output
    assert otro_archivo.exit_code == 1
    assert "ImportacionRechazada" in otro_archivo.output
    assert "no es el archivo de inegi-mg-2025-municipal" in otro_archivo.output


def test_geolocalizar_y_asignar_sin_sus_entradas_terminan_con_error(tmp_path):
    _, version_id = _referencia(tmp_path)
    falsa = "00000000-0000-4000-8000-000000000000"

    sin_corte = cli.invoke(
        app, ["geolocalizar", "--corte", CORTE, "--geografia-version", version_id]
    )
    sin_version = cli.invoke(app, ["muestrear-puntos", "--geografia-version", falsa])
    sin_geografia = cli.invoke(
        app,
        ["asignar-territorial-v2", "--corte", CORTE, "--geografia-run", falsa, "--bases", falsa],
    )

    assert (
        sin_corte.exit_code == 1 and "No hay un corte canonico del 2026-09-30" in sin_corte.output
    )
    assert sin_version.exit_code == 1 and f"No existe la version {falsa}" in sin_version.output
    assert sin_geografia.exit_code == 1


def test_cargar_bases_reutiliza_el_mismo_contenido_y_rechaza_uno_invalido(tmp_path):
    json_ = tmp_path / "bases.json"
    json_.write_text(json.dumps(BASES), encoding="utf-8")
    jsonl = tmp_path / "bases.jsonl"
    jsonl.write_text(
        "\n".join(json.dumps(b) for b in reversed(BASES["bases"])) + "\n", encoding="utf-8"
    )
    invalido = tmp_path / "invalido.json"
    invalido.write_text(
        json.dumps({"nombre": "x", "bases": [{**BASES["bases"][0], "lat": 95}]}), encoding="utf-8"
    )

    primera = cli.invoke(app, ["cargar-bases", str(json_)])
    sin_nombre = cli.invoke(app, ["cargar-bases", str(jsonl)])
    misma = cli.invoke(app, ["cargar-bases", str(jsonl), "--nombre", "Las mismas en JSONL"])
    mala = cli.invoke(app, ["cargar-bases", str(invalido)])

    assert primera.exit_code == 0, primera.output
    assert sin_nombre.exit_code == 2 and "--nombre" in sin_nombre.output
    assert misma.exit_code == 0 and "ya existia: se reutiliza" in misma.output
    assert _id(misma.output, "bases_version_id") == _id(primera.output, "bases_version_id")
    assert mala.exit_code == 1 and "lat esta entre -90 y 90" in mala.output


def test_las_proporciones_sin_puntos_o_que_no_suman_uno_se_niegan(tmp_path):
    base = ["generar-oficial", "--destino", str(tmp_path), "--n", "10", "--formato", "csv"]

    sin_puntos = cli.invoke(app, [*base, "--proporciones", "validas=1"])
    puntos = tmp_path / "puntos.json"
    puntos.write_text(json.dumps({"municipios": {}, "fuera": [[-105.0, 17.0]]}), encoding="ascii")
    no_suman = cli.invoke(app, [*base, "--puntos", str(puntos), "--proporciones", "validas=0.5"])

    assert sin_puntos.exit_code == 2 and "--proporciones se usa con --puntos" in sin_puntos.output
    assert no_suman.exit_code == 2 and "suman 1" in no_suman.output
