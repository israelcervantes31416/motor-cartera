"""La politica de la referencia de Google Cloud (scripts/verificar_iac.py): la referencia la cumple,
y cada regla importante detecta la regresion que existe para impedir. OpenTofu mismo (fmt, init,
validate y el plan sin credenciales) corre en el CI con su imagen fijada."""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
GCP = RAIZ / "infra" / "gcp"


def _herramienta():
    ruta = RAIZ / "scripts" / "verificar_iac.py"
    especificacion = importlib.util.spec_from_file_location("verificar_iac", ruta)
    modulo = importlib.util.module_from_spec(especificacion)
    sys.modules[especificacion.name] = modulo
    especificacion.loader.exec_module(modulo)
    return modulo


verificar_iac = _herramienta()


def _copia(tmp_path: Path) -> Path:
    destino = tmp_path / "gcp"
    destino.mkdir()
    for ruta in GCP.glob("*.tf"):
        shutil.copyfile(ruta, destino / ruta.name)
    shutil.copytree(GCP / "entornos", destino / "entornos")
    return destino


def _cambiar(directorio: Path, archivo: str, viejo: str, nuevo: str) -> None:
    ruta = directorio / archivo
    texto = ruta.read_text(encoding="utf-8")
    assert viejo in texto, f"{archivo} ya no tiene {viejo!r}: actualiza la prueba"
    ruta.write_text(texto.replace(viejo, nuevo, 1), encoding="utf-8")


def test_la_referencia_cumple_cada_regla(capsys):
    assert verificar_iac.main([str(GCP)]) == 0
    assert "MAL" not in capsys.readouterr().out


def test_ningun_tfstate_ni_proveedor_descargado_se_versiona():
    ignorados = (RAIZ / ".gitignore").read_text(encoding="utf-8")
    assert ".terraform/" in ignorados and "*.tfstate" in ignorados
    assert (GCP / ".terraform.lock.hcl").exists()


@pytest.mark.parametrize(
    ("archivo", "viejo", "nuevo", "regla"),
    [
        (
            "identidades.tf",
            'role    = "roles/run.developer"',
            'role    = "roles/editor"',
            "ningun roles/editor",
        ),
        (
            "base_de_datos.tf",
            "ipv4_enabled                                  = false",
            "ipv4_enabled                                  = true",
            "principal: sin IP publica",
        ),
        (
            "base_de_datos.tf",
            "point_in_time_recovery_enabled = true",
            "point_in_time_recovery_enabled = false",
            "principal: respaldos automaticos y PITR",
        ),
        (
            "base_de_datos.tf",
            "deletion_protection = true",
            "deletion_protection = false",
            "principal: protegida contra el borrado (OpenTofu y la API)",
        ),
        (
            "almacenamiento.tf",
            'public_access_prevention    = "enforced"',
            'public_access_prevention    = "inherited"',
            "respaldos: sin acceso publico posible",
        ),
        (
            "almacenamiento.tf",
            "immutable_tags = true",
            "immutable_tags = false",
            "imagenes: etiquetas inmutables",
        ),
        (
            "cloud_run.tf",
            'ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY"',
            'ingress             = "INGRESS_TRAFFIC_ALL"',
            "worker: solo interno",
        ),
        (
            "osrm_y_sesiones.tf",
            "    network_ip = google_compute_address.osrm.address\n",
            "    network_ip = google_compute_address.osrm.address\n    access_config {}\n",
            "osrm: sin IP publica",
        ),
        (
            "variables.tf",
            'can(regex("@sha256:[0-9a-f]{64}$", var.imagen_api))',
            'can(regex(":", var.imagen_api))',
            "var.imagen_api: exige el digest",
        ),
        (
            "monitoreo_y_wif.tf",
            "assertion.repository == '${var.repositorio_github}' && ",
            "",
            "Workload Identity Federation atado al repositorio y al environment",
        ),
        (
            "balanceador.tf",
            'min_tls_version = "TLS_1_2"',
            'min_tls_version = "TLS_1_0"',
            "politica de TLS moderna (TLS 1.2 o mas)",
        ),
    ],
)
def test_cada_regla_detecta_su_regresion(tmp_path, capsys, archivo, viejo, nuevo, regla):
    directorio = _copia(tmp_path)
    _cambiar(directorio, archivo, viejo, nuevo)

    assert verificar_iac.main([str(directorio)]) == 1
    assert f"MAL {regla}" in capsys.readouterr().out


def test_un_secreto_en_el_codigo_se_detecta(tmp_path, capsys):
    directorio = _copia(tmp_path)
    (directorio / "fuga.tf").write_text(
        'resource "google_secret_manager_secret_version" "x" {\n'
        '  secret      = google_secret_manager_secret.secretos["dsn_api"].id\n'
        '  secret_data = "postgresql://mc_api:una-contrasena@10.0.0.3/cartera"\n'
        "}\n",
        encoding="utf-8",
    )

    assert verificar_iac.main([str(directorio)]) == 1
    assert "MAL ninguna version de secreto en el codigo" in capsys.readouterr().out


def test_una_imagen_por_etiqueta_en_un_entorno_se_detecta(tmp_path, capsys):
    directorio = _copia(tmp_path)
    ruta = directorio / "entornos" / "staging.tfvars"
    texto = ruta.read_text(encoding="utf-8")
    ruta.write_text(
        texto.replace(
            "motor-cartera/web@sha256:0000000000000000000000000000000000000000000000000000000000000000",
            "motor-cartera/web:latest",
        ),
        encoding="utf-8",
    )

    assert verificar_iac.main([str(directorio)]) == 1
    assert "MAL staging.tfvars: cada imagen por su digest" in capsys.readouterr().out
