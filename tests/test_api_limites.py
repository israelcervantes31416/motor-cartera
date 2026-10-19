"""El tope del cuerpo de las peticiones que no suben un archivo: 413 antes de leerlo entero. Las
subidas (multipart) siguen con su propio tope, MC_TAMANO_MAXIMO_MB."""

from __future__ import annotations

from fastapi.testclient import TestClient

from motor_cartera.api import crear_app
from motor_cartera.config import Config


def _cliente(kb: int) -> TestClient:
    app = crear_app(Config(api_key="clave-de-limites", tamano_maximo_cuerpo_kb=kb))
    return TestClient(app, headers={"X-API-Key": "clave-de-limites"})


def test_un_cuerpo_json_mayor_al_tope_413_sin_llegar_a_la_ruta():
    with _cliente(1) as cliente:
        respuesta = cliente.post(
            "/decisiones-v2",
            content=b"{" + b'"x":"' + b"a" * 4096 + b'"}',
            headers={"Content-Type": "application/json"},
        )

    assert respuesta.status_code == 413
    assert respuesta.json()["codigo"] == "CUERPO_DEMASIADO_GRANDE"


def test_un_cuerpo_sin_content_length_tambien_se_corta(monkeypatch):
    def partes():
        for _ in range(8):
            yield b"a" * 1024

    with _cliente(2) as cliente:
        respuesta = cliente.post(
            "/decisiones-v2", content=partes(), headers={"Content-Type": "application/json"}
        )

    assert respuesta.status_code == 413
    assert respuesta.json()["codigo"] == "CUERPO_DEMASIADO_GRANDE"


def test_un_cuerpo_dentro_del_tope_llega_a_la_ruta():
    with _cliente(64) as cliente:
        respuesta = cliente.post("/decisiones-v2", json={"corte_id": "no-es-un-uuid"})

    assert respuesta.status_code == 422


def test_una_subida_multipart_no_la_corta_el_tope_de_json():
    """El tope del cuerpo JSON no aplica a las subidas: tienen el suyo, aplicado mientras el archivo
    se copia al almacen. Aqui no se procesa nada: falta el contrato del archivo."""
    with _cliente(1) as cliente:
        respuesta = cliente.post(
            "/corridas",
            files={"archivo": ("vacio.txt", b"x" * 4096, "application/octet-stream")},
        )

    assert respuesta.status_code != 413
