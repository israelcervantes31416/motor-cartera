"""La superficie web vista desde la API: su proxy deja pasar solo lecturas, solo de las rutas de su
lista blanca, y ninguna de esas rutas responde un dato personal; el contrato con que se generan los
tipos de la app corresponde a la API; y los dos proxies (nginx en el compose, Vite en desarrollo)
leen la misma lista y ponen la clave del lado del servidor.

No necesitan una base: revisan el OpenAPI que la API declara y los archivos de web/.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
from pydantic import BaseModel

from motor_cartera.api import crear_app
from motor_cartera.api.esquemas_app_v1 import sin_datos_personales
from motor_cartera.config import Config

RAIZ = Path(__file__).resolve().parents[1]
WEB = RAIZ / "web"


def _exportador():
    ruta = RAIZ / "scripts" / "exportar_contrato_web.py"
    especificacion = importlib.util.spec_from_file_location("exportar_contrato_web", ruta)
    modulo = importlib.util.module_from_spec(especificacion)
    especificacion.loader.exec_module(modulo)
    return modulo


exportador = _exportador()
EXPRESIONES = exportador.rutas_permitidas()

# Lo que ninguna respuesta que el proxy deja pasar declara: el identificador fuente de la cuenta,
# quien hizo una gestion, una llave de idempotencia, texto libre, datos de contacto y ubicacion.
PERSONALES = {
    "cliente_unico",
    "actor_ref",
    "idempotency_key",
    "observacion",
    "telefono",
    "correo",
    "curp",
    "rfc",
    "domicilio",
    "latitud",
    "longitud",
    "lat",
    "lon",
    "lng",
    "punto",
    "geometry",
    "geometria",
    "coordenadas",
    "polyline",
    "polilinea",
}


@pytest.fixture(scope="module")
def api() -> dict:
    return crear_app(Config(api_key="pruebas-de-la-superficie-web")).openapi()


def _alcanzables(api: dict, nodo) -> set[str]:
    """Los esquemas que un nodo del OpenAPI usa, en cualquier nivel."""
    esquemas = api["components"]["schemas"]
    usados: set[str] = set()

    def recorrer(n) -> None:
        if isinstance(n, dict):
            referencia = n.get("$ref")
            if isinstance(referencia, str) and referencia.startswith("#/components/schemas/"):
                nombre = referencia.rsplit("/", 1)[1]
                if nombre not in usados:
                    usados.add(nombre)
                    recorrer(esquemas[nombre])
            for valor in n.values():
                recorrer(valor)
        elif isinstance(n, list):
            for valor in n:
                recorrer(valor)

    recorrer(nodo)
    return usados


def _expuestas(api: dict) -> dict[str, dict]:
    return {
        ruta: metodos["get"]
        for ruta, metodos in api["paths"].items()
        if "get" in metodos and exportador.permitida(ruta, EXPRESIONES)
    }


def test_la_lista_blanca_tiene_expresiones_ancladas_bajo_api():
    assert len(EXPRESIONES) >= 10
    for expresion in EXPRESIONES:
        assert expresion.pattern.startswith("^/api/"), expresion.pattern


def test_cada_expresion_de_la_lista_deja_pasar_al_menos_una_lectura(api):
    rutas = [ruta for ruta, metodos in api["paths"].items() if "get" in metodos]
    muertas = [
        e.pattern
        for e in EXPRESIONES
        if not any(e.search(exportador.ruta_de_ejemplo(ruta)) for ruta in rutas)
    ]
    assert muertas == []


def test_ninguna_respuesta_que_el_proxy_deja_pasar_declara_un_dato_personal(api):
    expuestas = _expuestas(api)
    assert "/app/v1/cuentas/{cuenta_id}" in expuestas
    esquemas = api["components"]["schemas"]
    for ruta, operacion in expuestas.items():
        for nombre in _alcanzables(api, operacion["responses"]["200"]):
            campos = set(esquemas[nombre].get("properties", {}))
            assert not campos & PERSONALES, f"{ruta}: {nombre} declara {campos & PERSONALES}"


@pytest.mark.parametrize(
    "ruta",
    [
        "/cuentas",
        "/cuentas/{cuenta_id}",
        "/cuentas/{cuenta_id}/gestiones",
        "/cuentas/{cuenta_id}/campo-v2",
        "/cuentas/{cuenta_id}/ruteo-v2",
        "/cuentas/{cuenta_id}/geografia",
        "/gestiones/{gestion_id}",
        "/corridas/{run_id}",
        "/campo-v2/planes/{campo_run_id}/asignaciones",
        "/ruteo-v2/rutas/{ruta_id}/paradas",
        "/ruteo-v2/ejecuciones/{ruteo_run_id}/paradas",
        "/decisiones-v2/{decision_run_id}/resultados",
        "/geografia/ejecuciones/{geografia_run_id}/resultados",
        "/territorial-v2/{territorial_run_id}/resultados",
        "/atribuciones/{atribucion_run_id}/resultados",
    ],
)
def test_las_rutas_de_dominio_con_datos_personales_no_pasan_por_el_proxy(api, ruta):
    assert ruta in api["paths"], ruta
    assert not exportador.permitida(ruta, EXPRESIONES)


@pytest.mark.parametrize("ruta", ["/docs", "/redoc", "/openapi.json"])
def test_la_documentacion_de_la_api_tampoco_pasa(ruta):
    assert not any(e.search("/api" + ruta) for e in EXPRESIONES)


def test_por_el_proxy_solo_hay_lecturas(api):
    """Una ruta expuesta puede tener otro metodo en la API (una escritura de dominio): el proxy
    no lo deja pasar, y el contrato de la web no lo declara."""
    contrato = exportador.contrato(api, EXPRESIONES)
    assert {m for metodos in contrato["paths"].values() for m in metodos} == {"get"}


def test_el_contrato_de_la_web_corresponde_a_la_api():
    guardado = (WEB / "src" / "api" / "openapi.json").read_bytes().decode("utf-8")
    assert exportador.generar() == guardado


def test_el_contrato_no_depende_de_la_sangria_que_python_deja_en_un_docstring():
    """Python 3.12 conserva la sangria de un docstring y 3.13 la quita al compilar: el modelo sin
    datos personales que se deriva de uno se describe igual con las dos."""

    class ConCliente(BaseModel):
        cuenta_id: str
        cliente_unico: str

    ConCliente.__doc__ = "Una cuenta.\n\n    Con su cliente.\n    "  # como la deja Python 3.12

    derivado = sin_datos_personales(ConCliente)

    assert derivado.model_json_schema()["description"] == (
        "Una cuenta.\n\nCon su cliente.\n\nSin datos personales (app/v1)."
    )


def test_nginx_solo_deja_leer_pone_la_clave_y_lee_la_lista_blanca():
    plantilla = (WEB / "nginx" / "default.conf.template").read_text(encoding="utf-8")
    dockerfile = (WEB / "Dockerfile").read_text(encoding="utf-8")

    assert "if ($request_method != GET)" in plantilla
    assert "if ($api_permitida = 0)" in plantilla
    assert "include /etc/nginx/rutas-api.conf;" in plantilla
    assert 'proxy_set_header X-API-Key "${MC_API_KEY}";' in plantilla
    assert 'proxy_set_header Cookie "";' in plantilla
    assert "Access-Control-Allow-Origin" not in plantilla
    assert "nginx/rutas-api.conf /etc/nginx/rutas-api.conf" in dockerfile


def test_dentro_de_un_if_de_nginx_solo_van_directivas_que_nginx_admite_ahi():
    """nginx no arranca si un `if` lleva una directiva que no admite ahi (default_type, por
    ejemplo), y eso solo se veria al levantar el compose."""
    plantilla = (WEB / "nginx" / "default.conf.template").read_text(encoding="utf-8")
    bloques = re.findall(r"^\s*if \([^\n]*\) \{\n(.*?)^\s*\}$", plantilla, re.MULTILINE | re.DOTALL)

    assert len(bloques) == 2
    for bloque in bloques:
        directivas = set(re.findall(r"^\s*([a-z_]+)\s", bloque, re.MULTILINE))
        assert directivas <= {"add_header", "return", "rewrite", "set", "break"}, directivas


def test_el_proxy_de_vite_lee_la_misma_lista_y_solo_deja_leer():
    proxy = (WEB / "proxy-api.ts").read_text(encoding="utf-8")

    assert '"./nginx/rutas-api.conf"' in proxy
    assert 'req.method !== "GET"' in proxy
    assert '"X-API-Key": clave' in proxy


def test_la_api_no_abre_cors(api):
    """La app es del mismo origen que su proxy: la API no necesita CORS, y no lo abre."""
    app = crear_app(Config(api_key="pruebas-de-la-superficie-web"))
    assert not [m for m in app.user_middleware if m.cls.__name__ == "CORSMiddleware"]
