"""La configuracion por entorno: en produccion falla cerrada, sin valores por omision para lo que
es secreto o decide quien entra; la clave universal de desarrollo y cualquier interruptor que
prometa apagar la autenticacion no arrancan; los secretos se leen de archivos; y la documentacion de
la API no existe en produccion."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from motor_cartera import observabilidad
from motor_cartera.api import crear_app
from motor_cartera.config import Config, ConfiguracionInvalida, validar

SECRETO = "s" * 40
PRODUCCION = {
    "app_env": "production",
    "database_url": "postgresql+psycopg://mc_api:una-contrasena-larga-y-propia@postgres/cartera",
    "oidc_issuer": "https://idp.motor-cartera.invalid/dex",
    "oidc_audience": "motor-cartera-api",
    "oidc_jwks_url": "https://idp.motor-cartera.invalid/dex/keys",
    "gateway_secrets": SECRETO,
    "auditoria_clave": "a" * 40,
}


def _problemas(rol: str = "api", **cambios) -> list[str]:
    return Config(_env_file=None, **{**PRODUCCION, **cambios}).problemas(rol)


def test_sin_entorno_declarado_es_produccion(monkeypatch):
    monkeypatch.delenv("MC_APP_ENV", raising=False)

    assert Config(_env_file=None).app_env == "production"


def test_una_configuracion_de_produccion_completa_arranca():
    assert _problemas() == []


def test_produccion_vacia_no_arranca_y_dice_cada_problema(monkeypatch):
    monkeypatch.delenv("MC_DATABASE_URL", raising=False)
    config = Config(_env_file=None, app_env="production")

    with pytest.raises(ConfiguracionInvalida) as error:
        validar(config, "api")

    texto = str(error.value)
    for falta in ("MC_DATABASE_URL", "OpenID Connect", "MC_GATEWAY_SECRETS", "MC_AUDITORIA_CLAVE"):
        assert falta in texto, falta


@pytest.mark.parametrize(
    ("cambios", "problema"),
    [
        ({"api_key": "clave-local-de-desarrollo"}, "MC_API_KEY esta definida"),
        ({"oidc_issuer": "http://idp.motor-cartera.invalid/dex"}, "MC_OIDC_ISSUER debe ser https"),
        ({"oidc_jwks_url": "http://dex:5556/dex/keys"}, "MC_OIDC_JWKS_URL debe ser https"),
        ({"oidc_audience": None}, "MC_OIDC_AUDIENCE falta"),
        ({"gateway_secrets": None}, "MC_GATEWAY_SECRETS"),
        ({"gateway_secrets": "corto"}, "al menos 32"),
        ({"exigir_gateway": False}, "MC_EXIGIR_GATEWAY=false"),
        ({"auditoria_clave": None}, "MC_AUDITORIA_CLAVE"),
        ({"auditoria_clave": "corta"}, "al menos 32"),
        ({"oidc_algoritmos": "RS256,HS256"}, "'HS256'"),
        ({"oidc_algoritmos": "none"}, "'none'"),
        ({"oidc_grupos": "mc-lectores=mc.superuser"}, "MC_OIDC_GRUPOS"),
        ({"api_keys": "una linea que no es una clave"}, "MC_API_KEYS"),
        (
            {"database_url": "postgresql+psycopg://motor:motor@postgres/cartera"},
            "contrasena vacia o de desarrollo",
        ),
    ],
    ids=[
        "clave-universal",
        "issuer-http",
        "jwks-http",
        "sin-audiencia",
        "sin-gateway",
        "gateway-corto",
        "gateway-apagado",
        "sin-auditoria",
        "auditoria-corta",
        "hmac",
        "alg-none",
        "grupo-invalido",
        "claves-invalidas",
        "base-de-desarrollo",
    ],
)
def test_lo_que_no_arranca_en_produccion(cambios, problema):
    assert any(problema in p for p in _problemas(**cambios)), _problemas(**cambios)


@pytest.mark.parametrize(
    "variable",
    ["AUTH_DISABLED", "MC_AUTH_DISABLED", "MC_AUTH_DESHABILITADA", "MC_SIN_AUTENTICACION"],
)
@pytest.mark.parametrize("entorno", ["production", "development", "test"])
def test_no_existe_un_modo_sin_autenticacion(monkeypatch, variable, entorno):
    monkeypatch.setenv(variable, "true")
    config = Config(_env_file=None, **{**PRODUCCION, "app_env": entorno})

    with pytest.raises(ConfiguracionInvalida, match=variable):
        validar(config, "api")


def test_el_worker_en_produccion_solo_necesita_su_base():
    assert _problemas("worker", oidc_issuer=None, oidc_audience=None, oidc_jwks_url=None) == []
    assert _problemas("worker", database_url="postgresql+psycopg://motor:@postgres/x")


def test_fuera_de_produccion_la_clave_de_desarrollo_basta():
    config = Config(_env_file=None, app_env="development", api_key="clave-local-de-desarrollo")

    assert config.problemas("api") == []


def test_fuera_de_produccion_sin_ninguna_credencial_no_arranca():
    config = Config(_env_file=None, app_env="development")

    assert any("ninguna credencial" in p for p in config.problemas("api"))


# --- los secretos en archivos ---------------------------------------------------------------------


def test_un_secreto_se_lee_de_su_archivo(monkeypatch, tmp_path):
    archivo = tmp_path / "gateway"
    archivo.write_bytes(f"{SECRETO}\n".encode())
    monkeypatch.setenv("MC_GATEWAY_SECRETS_FILE", str(archivo))
    monkeypatch.delenv("MC_GATEWAY_SECRETS", raising=False)

    assert Config(_env_file=None).gateway_secrets.get_secret_value() == SECRETO


def test_un_secreto_por_las_dos_vias_no_arranca(monkeypatch, tmp_path):
    archivo = tmp_path / "clave"
    archivo.write_bytes(b"de-un-archivo")
    monkeypatch.setenv("MC_AUDITORIA_CLAVE_FILE", str(archivo))
    monkeypatch.setenv("MC_AUDITORIA_CLAVE", "de-una-variable")

    with pytest.raises(ConfiguracionInvalida, match="usa solo una"):
        Config(_env_file=None)


@pytest.mark.parametrize("contenido", [None, b"", b"  \n"])
def test_un_archivo_secreto_que_falta_o_esta_vacio_no_arranca(monkeypatch, tmp_path, contenido):
    archivo = tmp_path / "base"
    if contenido is not None:
        archivo.write_bytes(contenido)
    monkeypatch.setenv("MC_DATABASE_URL_FILE", str(archivo))
    monkeypatch.delenv("MC_DATABASE_URL", raising=False)

    with pytest.raises(ConfiguracionInvalida, match="MC_DATABASE_URL_FILE"):
        Config(_env_file=None)


def test_el_error_de_configuracion_no_dice_ningun_secreto():
    with pytest.raises(ConfiguracionInvalida) as error:
        validar(
            Config(_env_file=None, **{**PRODUCCION, "api_key": "una-clave-que-no-debe-salir"}),
            "api",
        )

    assert "una-clave-que-no-debe-salir" not in str(error.value)
    assert "una-contrasena-larga-y-propia" not in str(error.value)


# --- la API en produccion -------------------------------------------------------------------------


@pytest.fixture
def api_de_produccion(monkeypatch):
    # Sin tocar los logs, las trazas ni las metricas globales del proceso de las pruebas.
    monkeypatch.setattr(observabilidad, "preparar_proceso", lambda *_: None)
    app = crear_app(Config(_env_file=None, **PRODUCCION))
    with TestClient(app) as cliente:
        yield cliente


@pytest.mark.parametrize("ruta", ["/docs", "/redoc", "/openapi.json"])
def test_en_produccion_no_hay_documentacion_ni_openapi(api_de_produccion, ruta):
    respuesta = api_de_produccion.get(ruta)

    assert respuesta.status_code == 404
    assert respuesta.json()["codigo"] == "RUTA_NO_ENCONTRADA"


def test_en_produccion_la_clave_de_desarrollo_no_entra(api_de_produccion):
    respuesta = api_de_produccion.get(
        "/app/v1/sesion", headers={"X-API-Key": "clave-local-de-desarrollo"}
    )

    assert respuesta.status_code == 401


def test_en_produccion_un_token_exige_el_gateway(api_de_produccion):
    respuesta = api_de_produccion.get("/app/v1/sesion", headers={"Authorization": "Bearer x.y.z"})

    assert respuesta.status_code == 401
    assert respuesta.json()["codigo"] == "GATEWAY_REQUERIDO"


def test_fuera_de_produccion_la_documentacion_existe(app):
    with TestClient(app) as cliente:
        assert cliente.get("/openapi.json").status_code == 200
        assert cliente.get("/docs").status_code == 200


def test_la_api_no_anuncia_su_framework(app):
    with TestClient(app) as cliente:
        respuesta = cliente.get("/health/live")

    assert "server" not in {k.lower() for k in respuesta.headers}
    assert "x-powered-by" not in {k.lower() for k in respuesta.headers}
