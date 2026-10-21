"""Lo que promete la compatibilidad de Python y de la version (docs/versioning.md), sin PostgreSQL,
Docker ni OSRM: corre en la suite completa (3.12) y en cada pierna de la matriz ligera
(.github/workflows/compatibilidad.yml), en 3.12, 3.13 y 3.14."""

from __future__ import annotations

import importlib
import json
import pkgutil
import re
import sys
import tomllib
from pathlib import Path

import motor_cartera

RAIZ = Path(__file__).resolve().parents[1]
MATRIZ = RAIZ / ".github" / "workflows" / "compatibilidad.yml"


def _pyproject() -> dict:
    return tomllib.loads((RAIZ / "pyproject.toml").read_text(encoding="utf-8"))


def _menores_declarados() -> list[str]:
    """Las versiones menores de Python que admite requires-python (`>=3.A,<3.B`)."""
    requiere = _pyproject()["project"]["requires-python"].replace(" ", "")
    coincidencia = re.fullmatch(r">=3\.(\d+),<3\.(\d+)", requiere)
    assert coincidencia, f"requires-python sin su techo: {requiere}"
    desde, hasta = (int(n) for n in coincidencia.groups())
    return [f"3.{menor}" for menor in range(desde, hasta)]


def test_cada_modulo_del_paquete_se_importa():
    """Importar no necesita una base ni una red: cada modulo del paquete carga en este Python."""
    nombres = sorted(
        m.name for m in pkgutil.walk_packages(motor_cartera.__path__, "motor_cartera.")
    )
    fallas = []
    for nombre in nombres:
        # Se reportan todas, no solo la primera.
        try:
            importlib.import_module(nombre)
        except Exception as error:
            fallas.append(f"{nombre}: {error!r}")

    assert len(nombres) > 200
    assert not fallas, "\n".join(fallas)


def test_la_matriz_prueba_cada_python_que_se_declara():
    """requires-python y la matriz dicen lo mismo: ampliar uno sin el otro falla aqui."""
    texto = MATRIZ.read_text(encoding="utf-8")
    matriz = re.search(r"^\s+python:\s*\[([^\]]*)\]", texto, re.MULTILINE)
    assert matriz, "compatibilidad.yml sin su matriz de Python"

    probadas = [v.strip().strip("\"'") for v in matriz.group(1).split(",")]

    assert probadas == _menores_declarados()
    assert f"{sys.version_info.major}.{sys.version_info.minor}" in probadas


def test_cada_superficie_declara_la_misma_version():
    """El paquete, su lock, la app web y el contrato generado de la app: una sola version, en
    SemVer. Una release no sale con una a medias."""
    version = _pyproject()["project"]["version"]
    lock = tomllib.loads((RAIZ / "uv.lock").read_text(encoding="utf-8"))
    paquete = json.loads((RAIZ / "web" / "package.json").read_text(encoding="utf-8"))
    bloqueo = json.loads((RAIZ / "web" / "package-lock.json").read_text(encoding="utf-8"))
    contrato = json.loads(
        (RAIZ / "web" / "src" / "api" / "openapi.json").read_text(encoding="utf-8")
    )
    en_el_lock = [p["version"] for p in lock["package"] if p["name"] == "motor-cartera"]

    assert re.fullmatch(r"\d+\.\d+\.\d+", version), version
    assert motor_cartera.__version__ == version
    assert en_el_lock == [version]
    assert paquete["version"] == version
    assert bloqueo["version"] == version
    assert bloqueo["packages"][""]["version"] == version
    assert contrato["info"]["version"] == version
