"""La autenticacion y la autorizacion de la API, por HTTP, con un IdP de prueba que firma de verdad.

Cada caso del modelo de amenazas que se resuelve en la API: sin credencial, un token vencido, de
otro emisor, para otra audiencia, con la firma alterada, con una llave que el emisor no publica,
con `alg=none`; un permiso que falta (403) y uno que alcanza; cabeceras de identidad inventadas; un
token sin la credencial del gateway; las claves de servicio con sus permisos; y la matriz, que
clasifica cada ruta y no deja arrancar una sin clasificar.

Las rutas que se piden no necesitan la base: una autorizacion concedida se ve en un 200 de una ruta
estatica, o en un 422 de una ruta que si se alcanzo (su cuerpo o su query no cumplen el contrato),
nunca en un 401 o un 403.
"""

from __future__ import annotations

import hashlib
import logging

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from identidad_escenarios import (
    AUDIENCIA,
    CLAVE_AUDITORIA,
    EMISOR,
    GRUPOS,
    JWKS_URL,
    SECRETO_GATEWAY,
    Emisor,
    sin_firma,
)

from motor_cartera.api import crear_app
from motor_cartera.api.autorizacion import (
    MATRIZ,
    PERMISO_DE,
    Clase,
    operaciones_de,
    operaciones_de_las_rutas,
    verificar_matriz,
)
from motor_cartera.config import Config

CLAVE_LECTURA = "clave-de-servicio-de-lectura-0123456789abcdef"
CLAVE_AUTOMATIZACION = "clave-de-servicio-de-automatizacion-0123456789"
CLAVES = (
    f"lectura {hashlib.sha256(CLAVE_LECTURA.encode()).hexdigest()} mc.read\n"
    f"automatizacion {hashlib.sha256(CLAVE_AUTOMATIZACION.encode()).hexdigest()} "
    "mc.read,mc.execute\n"
)
GATEWAY = {"X-MC-Gateway": SECRETO_GATEWAY}


@pytest.fixture(scope="module")
def emisor() -> Emisor:
    emisor = Emisor()
    emisor.nueva_rsa("llave-1")
    emisor.nueva_rsa("ajena", publicar=False)
    return emisor


def _app(emisor: Emisor, **cambios) -> FastAPI:
    base = {
        "app_env": "test",
        "oidc_issuer": EMISOR,
        "oidc_audience": AUDIENCIA,
        "oidc_jwks_url": JWKS_URL,
        "oidc_grupos": GRUPOS,
        "gateway_secrets": SECRETO_GATEWAY,
        "exigir_gateway": True,
        "auditoria_clave": CLAVE_AUDITORIA,
        "api_keys": CLAVES,
    }
    app = crear_app(Config(**{**base, **cambios}))
    app.state.autenticador.validador.jwks.obtener = emisor.obtener
    return app


@pytest.fixture(scope="module")
def cliente(emisor):
    with TestClient(_app(emisor)) as cliente:
        yield cliente


def _con_token(token: str, gateway: bool = True) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **(GATEWAY if gateway else {})}


# --- sin credencial y con una credencial que no prueba nada --------------------------------------


def test_sin_credencial_401_con_desafio_bearer(cliente):
    respuesta = cliente.get("/app/v1/sesion")

    assert respuesta.status_code == 401
    assert respuesta.json()["codigo"] == "CREDENCIAL_AUSENTE"
    assert 'Bearer realm="motor-cartera"' in respuesta.headers["WWW-Authenticate"]


@pytest.mark.parametrize(
    ("cambios", "codigo"),
    [
        ({"exp": 1_000_000_000}, "TOKEN_EXPIRADO"),
        ({"iss": "https://otro-emisor.invalid/dex"}, "TOKEN_EMISOR_INVALIDO"),
        ({"aud": ["otra-api"]}, "TOKEN_AUDIENCIA_INVALIDA"),
        ({"sub": None}, "TOKEN_SIN_SUJETO"),
    ],
    ids=["vencido", "otro-emisor", "otra-audiencia", "sin-sujeto"],
)
def test_un_token_invalido_401_con_su_codigo(cliente, emisor, cambios, codigo):
    respuesta = cliente.get("/app/v1/sesion", headers=_con_token(emisor.token(**cambios)))

    assert respuesta.status_code == 401
    assert respuesta.json()["codigo"] == codigo
    assert 'error="invalid_token"' in respuesta.headers["WWW-Authenticate"]


def test_una_firma_alterada_401(cliente, emisor):
    cabecera, cuerpo, firma = emisor.token().split(".")
    alterado = f"{cabecera}.{cuerpo}.{firma[:-6]}AAAAAA"

    respuesta = cliente.get("/app/v1/sesion", headers=_con_token(alterado))

    assert respuesta.status_code == 401
    assert respuesta.json()["codigo"] == "TOKEN_FIRMA_INVALIDA"


def test_una_llave_que_el_emisor_no_publica_401(cliente, emisor):
    respuesta = cliente.get("/app/v1/sesion", headers=_con_token(emisor.token("ajena")))

    assert respuesta.status_code == 401
    assert respuesta.json()["codigo"] == "TOKEN_CLAVE_DESCONOCIDA"


def test_alg_none_401(cliente):
    respuesta = cliente.get("/app/v1/sesion", headers=_con_token(sin_firma()))

    assert respuesta.status_code == 401
    assert respuesta.json()["codigo"] == "TOKEN_ALGORITMO_NO_PERMITIDO"


@pytest.mark.parametrize("basura", ["abc123", "Bearer", "x.y.z"])
def test_un_token_inventado_401(cliente, basura):
    respuesta = cliente.get("/app/v1/sesion", headers=_con_token(basura))

    assert respuesta.status_code == 401


def test_otro_esquema_de_authorization_401(cliente):
    respuesta = cliente.get(
        "/app/v1/sesion", headers={"Authorization": "Basic dXN1YXJpbzpjbGF2ZQ=="}
    )

    assert respuesta.status_code == 401
    assert respuesta.json()["codigo"] == "CREDENCIAL_NO_SOPORTADA"


def test_una_clave_y_un_token_a_la_vez_401(cliente, emisor):
    encabezados = {**_con_token(emisor.token()), "X-API-Key": CLAVE_LECTURA}

    respuesta = cliente.get("/app/v1/sesion", headers=encabezados)

    assert respuesta.status_code == 401
    assert respuesta.json()["codigo"] == "CREDENCIALES_AMBIGUAS"


# --- el gateway ---------------------------------------------------------------------------------


def test_un_token_valido_sin_la_credencial_del_gateway_401(cliente, emisor):
    """Saltarse el edge: un token legitimo presentado directo a la API no entra."""
    respuesta = cliente.get("/app/v1/sesion", headers=_con_token(emisor.token(), gateway=False))

    assert respuesta.status_code == 401
    assert respuesta.json()["codigo"] == "GATEWAY_REQUERIDO"


def test_un_secreto_de_gateway_inventado_401(cliente, emisor):
    encabezados = {
        "Authorization": f"Bearer {emisor.token()}",
        "X-MC-Gateway": "no-es-el-secreto-del-gateway-0123456789",
    }

    respuesta = cliente.get("/app/v1/sesion", headers=encabezados)

    assert respuesta.json()["codigo"] == "GATEWAY_REQUERIDO"


def test_durante_una_rotacion_valen_los_dos_secretos_del_gateway(emisor):
    nuevo = "el-secreto-nuevo-del-gateway-0123456789abcd"
    with TestClient(_app(emisor, gateway_secrets=f"{SECRETO_GATEWAY}\n{nuevo}")) as cliente:
        for secreto in (SECRETO_GATEWAY, nuevo):
            encabezados = {"Authorization": f"Bearer {emisor.token()}", "X-MC-Gateway": secreto}
            assert cliente.get("/app/v1/sesion", headers=encabezados).status_code == 200


def test_fuera_de_produccion_el_gateway_se_puede_no_exigir(emisor):
    with TestClient(_app(emisor, exigir_gateway=False, gateway_secrets=None)) as cliente:
        respuesta = cliente.get("/app/v1/sesion", headers=_con_token(emisor.token(), False))

    assert respuesta.status_code == 200


# --- los permisos ---------------------------------------------------------------------------------


def test_un_lector_ve_su_sesion_sin_un_dato_personal(cliente, emisor):
    respuesta = cliente.get("/app/v1/sesion", headers=_con_token(emisor.token()))

    assert respuesta.status_code == 200
    sesion = respuesta.json()
    assert sesion["tipo"] == "USUARIO"
    assert sesion["permisos"] == ["mc.read"]
    assert sesion["actor"].startswith("usr:") and len(sesion["actor"]) == 20
    assert "lector@" not in respuesta.text and "CiQx" not in respuesta.text


def test_el_seudonimo_es_estable_y_no_es_el_sub(cliente, emisor):
    uno = cliente.get("/app/v1/sesion", headers=_con_token(emisor.token())).json()["actor"]
    otro = cliente.get("/app/v1/sesion", headers=_con_token(emisor.token())).json()["actor"]
    distinto = cliente.get("/app/v1/sesion", headers=_con_token(emisor.token(sub="otra-persona")))

    assert uno == otro
    assert distinto.json()["actor"] != uno


def test_sin_el_permiso_de_la_ruta_403_con_el_scope_que_falta(cliente, emisor):
    respuesta = cliente.get("/app/v1/linaje", headers=_con_token(emisor.token()))

    assert respuesta.status_code == 403
    assert respuesta.json()["codigo"] == "PERMISO_INSUFICIENTE"
    assert 'error="insufficient_scope", scope="mc.audit"' in respuesta.headers["WWW-Authenticate"]


def test_un_auditor_alcanza_la_auditoria(cliente, emisor):
    token = emisor.token(groups=["mc-auditores"])

    respuesta = cliente.get("/app/v1/linaje", headers=_con_token(token))

    # Alcanzo la ruta: le falta el contexto, que es otra cosa.
    assert respuesta.status_code == 422


def test_una_persona_sin_grupos_conocidos_no_lee_nada(cliente, emisor):
    token = emisor.token(groups=["otro-grupo"])

    respuesta = cliente.get("/app/v1/sesion", headers=_con_token(token))

    assert respuesta.status_code == 403
    assert respuesta.json()["codigo"] == "PERMISO_INSUFICIENTE"


def test_los_scopes_del_token_dan_permisos(cliente, emisor):
    token = emisor.token(groups=None, scope="openid mc.read mc.audit")

    sesion = cliente.get("/app/v1/sesion", headers=_con_token(token)).json()

    assert sesion["permisos"] == ["mc.audit", "mc.read"]


def test_un_scope_desconocido_no_da_nada(cliente, emisor):
    token = emisor.token(groups=None, scope="admin superuser mc.root", scp=["todo"])

    respuesta = cliente.get("/app/v1/sesion", headers=_con_token(token))

    assert respuesta.status_code == 403


@pytest.mark.parametrize(
    ("grupo", "estado"),
    [
        ("mc-lectores", 403),
        ("mc-auditores", 403),
        ("mc-operadores", 422),
        ("mc-administradores", 422),
    ],
)
def test_ejecutar_exige_mc_execute(cliente, emisor, grupo, estado):
    respuesta = cliente.post(
        "/decisiones-v2", headers=_con_token(emisor.token(groups=[grupo])), json={}
    )

    assert respuesta.status_code == estado


@pytest.mark.parametrize(
    ("grupo", "estado"),
    [("mc-operadores", 403), ("mc-auditores", 403), ("mc-administradores", 422)],
)
def test_configurar_exige_mc_configure(cliente, emisor, grupo, estado):
    respuesta = cliente.post(
        "/bases/versiones", headers=_con_token(emisor.token(groups=[grupo])), json={}
    )

    assert respuesta.status_code == estado


# --- cabeceras de identidad inventadas ------------------------------------------------------------

SUPLANTACION = {
    "X-MC-Subject": "admin",
    "X-MC-Roles": "ADMIN",
    "X-MC-Permisos": "mc.read mc.audit mc.execute mc.configure",
    "X-Forwarded-User": "admin@motor-cartera.test",
    "X-Forwarded-Email": "admin@motor-cartera.test",
    "X-Auth-Request-User": "admin",
    "X-Auth-Request-Groups": "mc-administradores",
    "X-Remote-User": "admin",
}


def test_las_cabeceras_de_identidad_no_autentican(cliente):
    respuesta = cliente.get("/app/v1/sesion", headers=SUPLANTACION)

    assert respuesta.status_code == 401


def test_las_cabeceras_de_identidad_no_elevan_a_un_lector(cliente, emisor):
    encabezados = {**SUPLANTACION, **_con_token(emisor.token())}

    sesion = cliente.get("/app/v1/sesion", headers=encabezados).json()
    escribir = cliente.post("/decisiones-v2", headers=encabezados, json={})

    assert sesion["permisos"] == ["mc.read"]
    assert escribir.status_code == 403


# --- las claves de servicio -----------------------------------------------------------------------


def test_una_clave_de_servicio_vale_solo_para_sus_permisos(cliente):
    lectura = {"X-API-Key": CLAVE_LECTURA}

    sesion = cliente.get("/app/v1/sesion", headers=lectura).json()
    escribir = cliente.post("/decisiones-v2", headers=lectura, json={})

    assert sesion == {"tipo": "SERVICIO", "actor": "svc:lectura", "permisos": ["mc.read"]}
    assert escribir.status_code == 403


def test_una_clave_con_mc_execute_ejecuta(cliente):
    respuesta = cliente.post("/decisiones-v2", headers={"X-API-Key": CLAVE_AUTOMATIZACION}, json={})

    assert respuesta.status_code == 422


def test_una_clave_inventada_401(cliente):
    respuesta = cliente.get("/app/v1/sesion", headers={"X-API-Key": "una-clave-que-nadie-emitio"})

    assert respuesta.status_code == 401
    assert respuesta.json()["codigo"] == "API_KEY_INVALIDA"


def test_la_clave_de_desarrollo_no_existe_si_no_se_declara(cliente):
    respuesta = cliente.get("/app/v1/sesion", headers={"X-API-Key": "clave-local-de-desarrollo"})

    assert respuesta.status_code == 401


# --- el IdP caido ---------------------------------------------------------------------------------


def test_sin_jwks_y_con_el_idp_caido_503_cerrado():
    caido = Emisor()
    caido.nueva_rsa("llave-1")
    caido.caido = True
    with TestClient(_app(caido)) as cliente:
        respuesta = cliente.get("/app/v1/sesion", headers=_con_token(caido.token()))

    assert respuesta.status_code == 503
    assert respuesta.json()["codigo"] == "IDENTIDAD_NO_DISPONIBLE"


# --- la matriz ------------------------------------------------------------------------------------


def test_la_matriz_clasifica_cada_ruta_y_solo_las_que_existen(cliente):
    assert operaciones_de(cliente.app) == set(MATRIZ)


def test_las_rutas_dan_las_mismas_operaciones_que_el_openapi(cliente):
    assert operaciones_de_las_rutas(cliente.app) == operaciones_de(cliente.app)


def test_construir_la_app_no_genera_el_openapi(emisor):
    """Generarlo tarda mas de un segundo, y las pruebas construyen una app por prueba: verificar la
    matriz lee las rutas, y el esquema se arma solo cuando alguien lo pide."""
    assert _app(emisor).openapi_schema is None


def test_solo_la_salud_es_publica():
    publicas = {ruta for (metodo, ruta), clase in MATRIZ.items() if clase is Clase.PUBLICA}

    assert publicas == {"/salud", "/health/live", "/health/ready"}


def test_ninguna_escritura_es_de_lectura_ni_publica():
    for (metodo, ruta), clase in MATRIZ.items():
        if metodo == "GET":
            assert clase in {Clase.PUBLICA, Clase.LECTURA, Clase.AUDITORIA}, ruta
        else:
            assert clase in {Clase.EJECUCION, Clase.CONFIGURACION}, (metodo, ruta)


def test_la_evidencia_cruda_y_los_trabajos_son_de_auditoria():
    for ruta in (
        "/corridas/{run_id}/rechazos",
        "/corridas/{run_id}/fuente",
        "/pagos/{pagos_run_id}/rechazos",
        "/movimientos/{movimiento_id}/observaciones",
        "/flujos/{flujo_id}/trabajos",
        "/trabajos/{trabajo_id}",
        "/app/v1/linaje",
    ):
        assert MATRIZ[("GET", ruta)] is Clase.AUDITORIA, ruta


def test_una_ruta_sin_clasificar_no_deja_arrancar_ni_se_atiende(emisor):
    app = _app(emisor)
    nueva = APIRouter()

    @nueva.get("/ruta-nueva")
    def ruta_nueva() -> dict[str, bool]:
        return {"ok": True}

    app.include_router(nueva)
    with pytest.raises(RuntimeError, match="sin clasificar: GET /ruta-nueva"):
        verificar_matriz(app)
    with TestClient(app) as cliente:
        respuesta = cliente.get("/ruta-nueva", headers={"X-API-Key": CLAVE_AUTOMATIZACION})
    assert respuesta.status_code == 500
    assert respuesta.json()["codigo"] == "RUTA_SIN_CLASIFICAR"


def test_el_openapi_declara_como_se_autentica_cada_operacion(cliente):
    api = cliente.app.openapi()
    esquemas = api["components"]["securitySchemes"]

    assert esquemas["TokenDeAcceso"]["scheme"] == "bearer"
    assert esquemas["ClaveDeServicio"]["name"] == "X-API-Key"
    for (metodo, ruta), clase in MATRIZ.items():
        operacion = api["paths"][ruta][metodo.lower()]
        if clase is Clase.PUBLICA:
            assert operacion["security"] == [], ruta
        else:
            permiso = PERMISO_DE[clase].value
            assert {"TokenDeAcceso": [permiso]} in operacion["security"], ruta
            assert "403" in operacion["responses"], ruta


# --- la auditoria ---------------------------------------------------------------------------------


def _eventos(caplog) -> list[dict]:
    return [
        r.campos
        for r in caplog.records
        if r.name == "motor_cartera.auditoria" and isinstance(getattr(r, "campos", None), dict)
    ]


def test_cada_decision_deja_su_evento_de_auditoria_sin_datos_personales(cliente, emisor, caplog):
    caplog.set_level(logging.INFO, logger="motor_cartera.auditoria")
    token = emisor.token()

    cliente.get("/app/v1/sesion", headers=_con_token(token))
    cliente.get("/app/v1/linaje", headers=_con_token(token))
    cliente.get("/app/v1/sesion")

    permitido, denegado, anonimo = _eventos(caplog)[-3:]
    assert permitido["schema"] == "security-audit/v1"
    assert permitido["decision"] == "PERMITIDO" and permitido["required_scope"] == "mc.read"
    assert permitido["route"] == "/app/v1/sesion" and permitido["method"] == "GET"
    assert permitido["actor"].startswith("usr:") and permitido["actor_type"] == "USUARIO"
    assert denegado["decision"] == "DENEGADO" and denegado["reason"] == "PERMISO_INSUFICIENTE"
    assert denegado["required_scope"] == "mc.audit" and denegado["route_class"] == "AUDIT"
    assert anonimo["decision"] == "NO_AUTENTICADO" and anonimo["actor"] == "anonimo"
    texto = caplog.text + str(_eventos(caplog))
    for prohibido in (token, "lector@motor-cartera.test", "CiQxMTEx", SECRETO_GATEWAY, "Bearer "):
        assert prohibido not in texto, prohibido


def test_la_salud_no_llena_la_auditoria(cliente, caplog):
    caplog.set_level(logging.INFO, logger="motor_cartera.auditoria")

    cliente.get("/health/live")

    assert not [e for e in _eventos(caplog) if e["route"] == "/health/live"]


# --- el documento ---------------------------------------------------------------------------------


def test_la_matriz_documentada_es_la_de_la_api():
    """docs/matriz-autorizacion.md es exactamente lo que escribe su script desde MATRIZ: si una
    ruta cambia de clase o aparece una nueva, el documento se regenera en el mismo cambio."""
    import importlib.util
    from pathlib import Path

    raiz = Path(__file__).resolve().parents[1]
    ruta = raiz / "scripts" / "exportar_matriz_autorizacion.py"
    especificacion = importlib.util.spec_from_file_location("exportar_matriz", ruta)
    modulo = importlib.util.module_from_spec(especificacion)
    especificacion.loader.exec_module(modulo)

    escrito = (raiz / "docs" / "matriz-autorizacion.md").read_text(encoding="utf-8")
    assert escrito == modulo.documento(), "regenera: python scripts/exportar_matriz_autorizacion.py"
