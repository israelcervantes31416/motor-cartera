"""Las precondiciones de una release (scripts/verificar_release.py), contra una API de GitHub de
mentira: todas se cumplen en un caso en orden, y cada una niega la release por su cuenta."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import urllib.parse
from pathlib import Path

import pytest

from motor_cartera import __version__

RAIZ = Path(__file__).resolve().parents[1]
REPOSITORIO = "israelcervantes31416/motor-cartera"
SHA = "a" * 40
CHECKS = [
    "archivos",
    "pruebas",
    "compose",
    "produccion",
    "CodeQL (python)",
    "CodeQL (javascript-typescript)",
]


def _herramienta():
    ruta = RAIZ / "scripts" / "verificar_release.py"
    especificacion = importlib.util.spec_from_file_location("verificar_release", ruta)
    modulo = importlib.util.module_from_spec(especificacion)
    sys.modules[especificacion.name] = modulo
    especificacion.loader.exec_module(modulo)
    return modulo


release = _herramienta()


def _corrida(nombre: str, conclusion: str = "success", app: int = 15368) -> dict:
    return {
        "id": 1,
        "name": nombre,
        "status": "completed",
        "conclusion": conclusion,
        "app": {"id": app},
    }


class Github:
    """Las respuestas de la API para un commit de main con todo en orden; cada prueba cambia una."""

    def __init__(self) -> None:
        self.reglas = [
            {
                "type": "required_status_checks",
                "parameters": {
                    "required_status_checks": [
                        *({"context": c, "integration_id": 15368} for c in CHECKS),
                        {"context": "CodeQL", "integration_id": 57789},
                    ]
                },
            }
        ]
        self.checks = {c: [_corrida(c)] for c in [*CHECKS, *release.checks_de_compatibilidad(RAIZ)]}
        self.cabeza = SHA
        self.etiquetas = [{"name": "v0.0.1"}, {"name": "no-es-una-version"}]
        self.analisis = [
            {"category": "/language:python", "error": ""},
            {"category": "/language:javascript-typescript", "error": ""},
        ]
        self.alertas: list[dict] = []
        self.ci = [
            {
                "id": 99,
                "run_number": 7,
                "run_attempt": 1,
                "status": "completed",
                "conclusion": "success",
            }
        ]
        self.etiqueta_existe = False
        self.release_existe = False

    def __call__(self, ruta: str) -> tuple[int, object]:
        camino, _, consulta = ruta.partition("?")
        parametros = dict(urllib.parse.parse_qsl(consulta))
        base = f"/repos/{REPOSITORIO}"
        if camino == f"{base}/rules/branches/main":
            return 200, self.reglas
        if camino == f"{base}/commits/{SHA}/check-runs":
            return 200, {"check_runs": self.checks.get(parametros["check_name"], [])}
        if camino == f"{base}/git/ref/heads/main":
            return 200, {"object": {"sha": self.cabeza}}
        if camino == f"{base}/tags":
            return 200, self.etiquetas
        if camino == f"{base}/code-scanning/analyses":
            return 200, self.analisis
        if camino == f"{base}/code-scanning/alerts":
            return 200, self.alertas
        if camino == f"{base}/actions/workflows/ci.yml/runs":
            return 200, {"workflow_runs": self.ci}
        if camino.startswith(f"{base}/git/ref/tags/v"):
            return (200, {}) if self.etiqueta_existe else (404, None)
        if camino.startswith(f"{base}/releases/tags/v"):
            return (200, {}) if self.release_existe else (404, None)
        raise AssertionError(f"ruta inesperada: {ruta}")


def _revisar(github: Github, **cambios):
    argumentos = {
        "version": __version__,
        "sha": SHA,
        "ref": "refs/heads/main",
        "actor": "israelcervantes31416",
        "dueno": "israelcervantes31416",
    } | cambios
    return release.revisar(github, RAIZ, REPOSITORIO, **argumentos)


def test_un_commit_de_main_con_todo_en_orden_cumple_cada_precondicion():
    bien, mal, salida = _revisar(Github())

    assert mal == []
    assert salida["sha"] == SHA
    assert salida["ci_run"] == "99"
    assert salida["fecha"]
    assert "CodeQL: ninguna alerta abierta en main" in bien
    assert "compatibilidad (3.14): success" in bien
    assert sum("declara" in linea for linea in bien) == 7


@pytest.mark.parametrize(
    ("cambiar", "argumentos", "problema"),
    [
        (lambda g: None, {"actor": "otra-persona"}, "no el dueno"),
        (lambda g: None, {"ref": "refs/heads/feat/x"}, "no desde main"),
        (lambda g: None, {"version": "1.0"}, "no es X.Y.Z"),
        (lambda g: None, {"version": "0.0.1"}, "declara"),
        (lambda g: setattr(g, "cabeza", "b" * 40), {}, "la cabeza de main es"),
        (
            lambda g: g.checks.update({"pruebas": [_corrida("pruebas", "failure")]}),
            {},
            "pruebas: completed / failure",
        ),
        (lambda g: g.checks.pop("compose"), {}, "compose: no corrio"),
        (lambda g: g.checks.pop("compatibilidad (3.13)"), {}, "compatibilidad (3.13): no corrio"),
        (
            lambda g: g.reglas[0]["parameters"]["required_status_checks"].append(
                {"context": "otro", "integration_id": 1}
            ),
            {},
            "que no se sabe verificar",
        ),
        (lambda g: setattr(g, "alertas", [{"number": 5}]), {}, "1 alertas abiertas en main (#5)"),
        (lambda g: g.analisis.pop(), {}, "no hay un analisis de javascript-typescript"),
        (lambda g: setattr(g, "etiquetas", [{"name": "v9.0.0"}]), {}, "no es mayor"),
        (
            lambda g: setattr(g, "ci", [{**g.ci[0], "conclusion": "cancelled"}]),
            {},
            "completed / cancelled",
        ),
        (lambda g: setattr(g, "etiqueta_existe", True), {}, "ya existe"),
        (lambda g: setattr(g, "release_existe", True), {}, "release"),
        (lambda g: setattr(g, "reglas", []), {}, "no exigen ningun check"),
    ],
)
def test_cada_precondicion_niega_la_release_por_su_cuenta(cambiar, argumentos, problema):
    github = Github()
    cambiar(github)

    _, mal, _ = _revisar(github, **argumentos)

    assert mal, problema
    assert any(problema in linea for linea in mal), mal


def test_la_entrada_y_las_notas_salen_del_changelog():
    texto = (
        "## [Unreleased]\n\n"
        "## [v2.1.0] — Algo Nuevo — 2027-01-02\n\n- Una cosa.\n- Otra.\n\n"
        "## [v2.0.0] — Lo Anterior — 2026-12-01\n\n- Vieja.\n\n"
        "[Unreleased]: https://github.com/r/p/compare/v2.1.0...HEAD\n"
        "[v2.1.0]: https://github.com/r/p/releases/tag/v2.1.0\n"
    )

    assert release.entrada_del_changelog(texto, "2.1.0", "r/p") == ("Algo Nuevo", "2027-01-02")
    assert release.entrada_del_changelog(texto, "2.0.0", "r/p") is None
    assert release.notas_del_changelog(texto, "2.1.0") == "- Una cosa.\n- Otra."
    assert release.notas_del_changelog(texto, "2.0.0") == "- Vieja."


def _sbom(imagen: str) -> bytes:
    return json.dumps(
        {
            "bomFormat": "CycloneDX",
            "specVersion": "1.7",
            "metadata": {"component": {"type": "container", "name": imagen}},
            "components": [{"name": "openssl"}],
        }
    ).encode()


def _evidencia(tmp_path: Path, version: str = "2.1.0") -> tuple[Path, Path]:
    evidencia = tmp_path / "evidencia"
    evidencia.mkdir()
    for nombre, (imagen, _) in release.SBOMS.items():
        (evidencia / f"sbom-{nombre}.cdx.json").write_bytes(_sbom(imagen))
    destino = tmp_path / "release"
    (destino / "assets").mkdir(parents=True)
    (destino / "assets" / f"openapi-v{version}.json").write_bytes(
        json.dumps({"openapi": "3.1.0", "info": {"version": version}}).encode()
    )
    return evidencia, destino


def test_los_assets_son_los_bytes_del_ci_con_el_nombre_de_la_version_y_su_sha256sums(tmp_path):
    evidencia, destino = _evidencia(tmp_path)

    archivos = release.preparar_assets("2.1.0", evidencia, destino)

    assert [a.name for a in archivos] == [
        "openapi-v2.1.0.json",
        "sbom-api-v2.1.0.cdx.json",
        "sbom-web-v2.1.0.cdx.json",
        "sbom-respaldo-v2.1.0.cdx.json",
        "SHA256SUMS",
    ]
    for nombre in release.SBOMS:
        copia = destino / "assets" / f"sbom-{nombre}-v2.1.0.cdx.json"
        assert copia.read_bytes() == (evidencia / f"sbom-{nombre}.cdx.json").read_bytes()
    lineas = (destino / "assets" / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    assert lineas == [
        f"{hashlib.sha256(a.read_bytes()).hexdigest()} *{a.name}" for a in archivos[:-1]
    ]


@pytest.mark.parametrize(
    ("estropear", "problema"),
    [
        (lambda e, d: (e / "sbom-web.cdx.json").write_bytes(_sbom("otra:imagen")), "sbom-web"),
        (
            lambda e, d: (d / "assets" / "openapi-v2.1.0.json").write_bytes(
                b'{"info": {"version": "2.0.0"}}'
            ),
            "2.0.0",
        ),
        (
            lambda e, d: (e / "sbom-api.cdx.json").write_bytes(
                b'{"bomFormat": "CycloneDX", "metadata": {"component": '
                b'{"name": "motor-cartera:produccion"}}, "components": []}'
            ),
            "sbom-api",
        ),
    ],
)
def test_un_asset_que_no_es_el_esperado_se_niega(tmp_path, estropear, problema):
    evidencia, destino = _evidencia(tmp_path)
    estropear(evidencia, destino)

    with pytest.raises(ValueError, match=problema):
        release.preparar_assets("2.1.0", evidencia, destino)


def test_las_notas_llevan_el_changelog_cada_asset_y_como_verificarlo(tmp_path):
    evidencia, destino = _evidencia(tmp_path)
    archivos = release.preparar_assets("2.1.0", evidencia, destino)
    changelog = (
        "## [Unreleased]\n\n## [v2.1.0] — Algo Nuevo — 2027-01-02\n\n- Una cosa.\n\n"
        "## [v2.0.0] — Lo Anterior — 2026-12-01\n\n- Vieja.\n\n"
        "[v2.1.0]: https://github.com/r/p/releases/tag/v2.1.0\n"
    )

    texto = release.notas(changelog, "r/p", "2.1.0", SHA, "12345", archivos)

    assert texto.startswith("- Una cosa.\n\n## Assets\n")
    for archivo in archivos:
        assert f"`{hashlib.sha256(archivo.read_bytes()).hexdigest()}`" in texto
    assert "https://github.com/r/p/actions/runs/12345" in texto
    assert "gh attestation verify openapi-v2.1.0.json --repo r/p" in texto
    assert texto.rstrip().endswith("**Cambios:** https://github.com/r/p/compare/v2.0.0...v2.1.0")


def test_los_checks_extra_son_la_matriz_de_python_y_su_contrato():
    assert release.checks_de_compatibilidad(RAIZ) == [
        "compatibilidad (3.12)",
        "compatibilidad (3.13)",
        "compatibilidad (3.14)",
        "compatibilidad (contrato)",
    ]
    assert release.lenguajes_de_codeql(RAIZ) == ["python", "javascript-typescript"]
