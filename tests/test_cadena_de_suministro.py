"""La cadena de suministro fijada (scripts/verificar_cadena_de_suministro.py): el repositorio la
cumple, y cada regla detecta lo que existe para impedir, sobre archivos de ejemplo."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
DIGEST = "@sha256:" + "a" * 64
SHA = "b" * 40


def _herramienta():
    ruta = RAIZ / "scripts" / "verificar_cadena_de_suministro.py"
    especificacion = importlib.util.spec_from_file_location("verificar_cadena", ruta)
    modulo = importlib.util.module_from_spec(especificacion)
    sys.modules[especificacion.name] = modulo
    especificacion.loader.exec_module(modulo)
    return modulo


cadena = _herramienta()


def _escribir(ruta: Path, texto: str) -> Path:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(texto, encoding="utf-8")
    return ruta


def test_el_repositorio_cumple(capsys):
    assert cadena.main([str(RAIZ)]) == 0, capsys.readouterr().out


def test_una_imagen_base_sin_digest_se_detecta_y_una_etapa_propia_no(tmp_path):
    dockerfile = _escribir(
        tmp_path / "Dockerfile",
        f"FROM python:3.12-slim{DIGEST} AS base\nFROM base AS final\nFROM nginx:1.30\n",
    )

    problemas = cadena.problemas_de_dockerfile(dockerfile)

    assert problemas == ["Dockerfile:3: FROM nginx:1.30 sin digest"]


def test_una_imagen_del_compose_sin_digest_se_detecta_y_una_construida_no(tmp_path):
    compose = _escribir(
        tmp_path / "docker-compose.yml",
        "x-app: &app\n"
        "  build:\n"
        "    context: .\n"
        "  image: propia:local\n"
        "\n"
        "services:\n"
        "  api:\n"
        "    <<: *app\n"
        "  web:\n"
        "    build: ./web\n"
        "    image: propia-web:local\n"
        "  base:\n"
        f"    image: postgis/postgis:16{DIGEST}\n"
        "  cache:\n"
        "    image: valkey/valkey:8\n",
    )

    problemas = cadena.problemas_de_compose(compose)

    assert problemas == ["docker-compose.yml:15: cache usa valkey/valkey:8 sin digest"]


def test_un_workflow_sin_fijar_se_detecta_regla_por_regla(tmp_path):
    workflow = _escribir(
        tmp_path / "ci.yml",
        "name: CI\n"
        "on: push\n"
        "jobs:\n"
        "  a:\n"
        "    steps:\n"
        "      - uses: actions/checkout@v7\n"
        f"      - uses: actions/setup-node@{SHA} # v7.1.0\n"
        "      - run: docker run --rm grafana/k6:1.8.1 run x.js\n",
    )

    problemas = cadena.problemas_de_workflow(workflow)

    assert problemas == [
        "ci.yml: sin permissions de nivel superior",
        "ci.yml:6: actions/checkout@v7 no esta fijada por SHA",
        "ci.yml:6: checkout sin persist-credentials: false",
        "ci.yml:8: docker run grafana/k6:1.8.1 sin digest",
    ]


def test_un_workflow_fijado_y_con_permisos_minimos_pasa(tmp_path):
    workflow = _escribir(
        tmp_path / "ci.yml",
        "name: CI\n"
        "on: push\n"
        "permissions:\n"
        "  contents: read\n"
        "jobs:\n"
        "  a:\n"
        "    steps:\n"
        f"      - uses: actions/checkout@{SHA} # v7.0.1\n"
        "        with:\n"
        "          persist-credentials: false\n"
        f"      - run: docker run --rm grafana/k6:1.8.1{DIGEST} run x.js\n",
    )

    assert cadena.problemas_de_workflow(workflow) == []


def test_write_all_se_detecta(tmp_path):
    workflow = _escribir(tmp_path / "ci.yml", "name: CI\npermissions: write-all\njobs: {}\n")

    assert "ci.yml: permissions write-all" in cadena.problemas_de_workflow(workflow)


def test_una_imagen_que_corre_un_script_sin_digest_se_detecta(tmp_path):
    _escribir(
        tmp_path / "scripts" / "carga.py",
        'IMAGEN = "grafana/k6:1.8.1"\ncomando = ["docker", "run", IMAGEN]\n',
    )

    assert cadena.problemas_de_scripts(tmp_path) == ["carga.py: grafana/k6:1.8.1 sin digest"]
