"""Captura las respuestas de la API con que se prueba el frontend (web/src/test/capturas.ts).

Corre en proceso, con el TestClient de FastAPI y sin levantar un servidor, contra la base sintetica
de la prueba de humo (la que deja `scripts/prueba_de_humo.py`):

    MC_DATABASE_URL=postgresql+psycopg://motor:motor@localhost:5434/cartera_humo_v16 \\
        PYTHONPATH=src python scripts/capturar_fixtures_web.py

Solo pide rutas GET que el proxy de la superficie web deja pasar (web/nginx/rutas-api.conf), y
falla si una respuesta trae un campo personal. Recorta las listas para que el archivo quede chico,
y escribe cada respuesta como una constante tipada por el contrato de la web: si el contrato cambia,
`npm run typecheck` dice que capturas ya no le corresponden. Los datos son del generador sintetico
del proyecto, nunca de una cartera real.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[1]
DESTINO = RAIZ / "web" / "src" / "test" / "capturas.ts"
CLAVE = "captura-de-fixtures"
LARGO = 12
"""Cuantos elementos se quedan de cada lista (los histogramas tienen diez)."""
FILAS = 5
"""Cuantas filas se quedan de cada pagina de una tabla."""
NO_EXISTE = "00000000-0000-4000-8000-00000000dead"

PERSONALES = re.compile(
    r'"(cliente_unico|actor_ref|idempotency_key|curp|rfc|telefono|domicilio|'
    r'latitud|longitud|lat|lon|geometry|geometria|geom)"\s*:'
    # "direccion" solo es el signo de un coeficiente ("+", "-" o "0"): otro valor es un domicilio.
    r'|"direccion"\s*:\s*(?!"[+0-]")',
    re.IGNORECASE,
)
CLIENTE_UNICO = re.compile(r"CU\d{10}")


def _contrato():
    ruta = RAIZ / "scripts" / "exportar_contrato_web.py"
    especificacion = importlib.util.spec_from_file_location("exportar_contrato_web", ruta)
    assert especificacion is not None and especificacion.loader is not None
    modulo = importlib.util.module_from_spec(especificacion)
    especificacion.loader.exec_module(modulo)
    return modulo


def recortar(valor: Any, largo: int = LARGO) -> Any:
    if isinstance(valor, list):
        return [recortar(v, largo) for v in valor[:largo]]
    if isinstance(valor, dict):
        return {k: recortar(v, largo) for k, v in valor.items()}
    return valor


class Captura:
    def __init__(self) -> None:
        from fastapi.testclient import TestClient

        from motor_cartera.api import crear_app
        from motor_cartera.config import Config

        self.cliente = TestClient(
            crear_app(Config(app_env="development", api_key=CLAVE)), headers={"X-API-Key": CLAVE}
        )
        self.expresiones = _contrato().rutas_permitidas()
        self.constantes: list[tuple[str, str, Any]] = []

    def get(self, plantilla: str, params: dict | None = None, **ruta: str) -> Any:
        camino = plantilla.format(**ruta)
        if not any(e.search("/api" + camino) for e in self.expresiones):
            raise SystemExit(f"{camino} no pasa por el proxy de la web: no se captura.")
        respuesta = self.cliente.get(camino, params={k: v for k, v in (params or {}).items() if v})
        if respuesta.status_code != 200:
            raise SystemExit(f"{camino}: HTTP {respuesta.status_code} {respuesta.text[:300]}")
        texto = respuesta.text
        if PERSONALES.search(texto) or CLIENTE_UNICO.search(texto):
            raise SystemExit(f"{camino} trae un campo personal: no se captura.")
        return respuesta.json()

    def guardar(self, nombre: str, plantilla: str, datos: Any) -> Any:
        self.constantes.append((nombre, plantilla, datos))
        return datos

    def escribir(self) -> None:
        lineas = [
            "// Generado por scripts/capturar_fixtures_web.py: respuestas de la API local sobre la",
            "// base sintetica de la prueba de humo, recortadas. No son datos reales y no se edita",
            "// a mano: cada constante esta tipada por el contrato de la web.",
            'import type { Respuesta } from "../api/cliente";',
            "",
        ]
        for nombre, ruta, datos in self.constantes:
            cuerpo = json.dumps(datos, ensure_ascii=False, indent=2, sort_keys=True)
            lineas.append(f'export const {nombre}: Respuesta<"{ruta}"> = {cuerpo};')
            lineas.append("")
        DESTINO.parent.mkdir(parents=True, exist_ok=True)
        DESTINO.write_bytes("\n".join(lineas).encode("utf-8"))


ASOCIACION_UNICA = """
    SELECT cc.cuenta_id FROM atribucion_movimiento a
    JOIN cuenta_canonica cc ON cc.id = a.cuenta_canonica_id
    WHERE a.clasificacion = 'ASOCIACION_UNICA' ORDER BY cc.cuenta_id LIMIT 1
"""
AMBIGUA = ASOCIACION_UNICA.replace("'ASOCIACION_UNICA'", "'AMBIGUA'")
CON_EVENTOS = """
    SELECT cc.cuenta_id FROM evento_lifecycle e
    JOIN cuenta_canonica cc ON cc.id = e.cuenta_canonica_id
    GROUP BY cc.cuenta_id ORDER BY count(*) DESC, cc.cuenta_id LIMIT 1
"""


def _cuenta_con(consulta: str) -> str:
    """El cuenta_id publico de la cuenta que `consulta` elige: solo para saber cual pedir."""
    from sqlalchemy import create_engine, text

    from motor_cartera.config import Config

    motor = create_engine(Config().database_url)
    try:
        with motor.connect() as conexion:
            cuenta = conexion.execute(text(consulta)).scalar_one_or_none()
    finally:
        motor.dispose()
    if cuenta is None:
        raise SystemExit(f"La base no tiene una cuenta para: {' '.join(consulta.split())}")
    return str(cuenta)


def main() -> int:
    c = Captura()

    opciones_sin_corte = c.get("/app/v1/contextos/opciones", {"por_pagina": 5})
    c.guardar("opcionesSinCorte", "/app/v1/contextos/opciones", recortar(opciones_sin_corte))
    corte_id = opciones_sin_corte["cortes"][-1]["corte_id"]
    opciones = c.get("/app/v1/contextos/opciones", {"corte_id": corte_id, "por_pagina": 5})
    sugerencia = next(s for s in opciones["sugerencias"] if s["codigo"] == "DESDE_OPTIMIZACION")
    contexto = {k: v for k, v in sugerencia["contexto"].items() if v}
    c.guardar("opciones", "/app/v1/contextos/opciones", recortar(opciones))
    c.guardar("validado", "/app/v1/contextos/validar", c.get("/app/v1/contextos/validar", contexto))
    incompatible = {**contexto, "scoring_run_id": NO_EXISTE}
    c.guardar(
        "validadoIncompatible",
        "/app/v1/contextos/validar",
        c.get("/app/v1/contextos/validar", incompatible),
    )
    c.guardar("resumen", "/app/v1/resumen", c.get("/app/v1/resumen", contexto))
    cuentas = c.get("/app/v1/cuentas", {**contexto, "por_pagina": FILAS})
    c.guardar("cuentas", "/app/v1/cuentas", cuentas)

    # La cuenta de la Cuenta 360: una de campo con parada PROGRAMADA y ASIGNADA en la optimizacion,
    # para que cada pestana tenga que mostrar; si no hay, la primera de campo.
    de_campo = c.get("/app/v1/cuentas", {**contexto, "canal": "CAMPO", "por_pagina": 100})
    cuenta = None
    for fila in de_campo["elementos"]:
        candidata = c.get("/app/v1/cuentas/{cuenta}", contexto, cuenta=fila["cuenta_id"])
        ruteo = candidata["ruteo"]
        optimizacion = candidata["optimizacion"]
        if (
            ruteo["estado"] == "DISPONIBLE"
            and ruteo["parada"] is not None
            and ruteo["parada"]["estado"] == "PROGRAMADA"
            and optimizacion["estado"] == "DISPONIBLE"
            and optimizacion["resultado"] is not None
            and optimizacion["resultado"]["estado"] == "ASIGNADA"
        ):
            cuenta = candidata
            break
    if cuenta is None:
        cuenta = c.get(
            "/app/v1/cuentas/{cuenta}", contexto, cuenta=de_campo["elementos"][0]["cuenta_id"]
        )
    cuenta_id = cuenta["cuenta_id"]
    c.guardar("cuenta", "/app/v1/cuentas/{cuenta_id}", cuenta)
    paginado = {"por_pagina": FILAS}
    for nombre, sufijo, params in (
        ("decisionDeCuenta", "decision", {"decision_run_id": contexto["decision_run_id"]}),
        ("historiaDeCuenta", "historia", paginado),
        ("lineaDeTiempo", "linea-de-tiempo", paginado),
        ("pagosDeCuenta", "pagos", paginado),
        ("gestionesDeCuenta", "gestiones", paginado),
    ):
        plantilla = "/app/v1/cuentas/{cuenta_id}/" + sufijo
        datos = c.get(plantilla.replace("{cuenta_id}", "{cuenta}"), params, cuenta=cuenta_id)
        c.guardar(nombre, plantilla, recortar(datos))
    # Las pestanas de pagos y de la linea de tiempo, con cuentas que si tienen que mostrar: la de
    # la atribucion unica, la de la ambigua y la de mas eventos. La base solo elige la cuenta; lo
    # capturado sale de la API.
    for nombre, sufijo, cuenta_de_ejemplo in (
        ("pagosConAsociacionUnica", "pagos", _cuenta_con(ASOCIACION_UNICA)),
        ("pagosAmbiguos", "pagos", _cuenta_con(AMBIGUA)),
        ("lineaDeTiempoConEventos", "linea-de-tiempo", _cuenta_con(CON_EVENTOS)),
        ("gestionesConEventos", "gestiones", _cuenta_con(CON_EVENTOS)),
    ):
        datos = c.get(
            "/app/v1/cuentas/{cuenta}/" + sufijo, {"por_pagina": FILAS}, cuenta=cuenta_de_ejemplo
        )
        c.guardar(nombre, "/app/v1/cuentas/{cuenta_id}/" + sufijo, recortar(datos))
    c.guardar("linaje", "/app/v1/linaje", c.get("/app/v1/linaje", contexto))

    scoring_id = contexto["scoring_run_id"]
    c.guardar(
        "distribucion",
        "/app/v1/predictivo/scorings/{scoring_run_id}/distribucion",
        c.get("/app/v1/predictivo/scorings/{s}/distribucion", s=scoring_id),
    )

    # analytics/v1: el catalogo solo con las metricas que las pruebas usan.
    catalogo = c.get("/analytics/catalogo")
    usadas = {"recuperacion.neta", "cartera.cuentas", "gestion.contactadas_sobre_gestionadas"}
    catalogo["metricas"] = [m for m in catalogo["metricas"] if m["codigo"] in usadas]
    c.guardar("catalogo", "/analytics/catalogo", catalogo)
    analytics_id = contexto["analytics_run_id"]
    c.guardar(
        "ejecucionAnalytics",
        "/analytics/ejecuciones/{analytics_run_id}",
        c.get("/analytics/ejecuciones/{a}", a=analytics_id),
    )
    agregados = "/analytics/ejecuciones/{analytics_run_id}/agregados"
    c.guardar(
        "serie",
        agregados,
        c.get(
            "/analytics/ejecuciones/{a}/agregados",
            {
                "metrica": "recuperacion.neta",
                "vista": "PERIODO",
                "dimension_set": "TOTAL",
                "por_pagina": 500,
            },
            a=analytics_id,
        ),
    )
    c.guardar(
        "categorias",
        agregados,
        c.get(
            "/analytics/ejecuciones/{a}/agregados",
            {
                "metrica": "recuperacion.neta",
                "vista": "ACUMULADA",
                "dimension_set": "PRODUCTO",
                "por_pagina": 100,
            },
            a=analytics_id,
        ),
    )
    c.guardar(
        "embudos",
        "/analytics/ejecuciones/{analytics_run_id}/embudos",
        c.get("/analytics/ejecuciones/{a}/embudos", a=analytics_id),
    )
    c.guardar(
        "controles",
        "/analytics/ejecuciones/{analytics_run_id}/controles",
        recortar(c.get("/analytics/ejecuciones/{a}/controles", a=analytics_id)),
    )

    # predictive/model-v1
    scoring = c.get("/predictivo/scorings/{s}", s=scoring_id)
    c.guardar("scoring", "/predictivo/scorings/{scoring_run_id}", scoring)
    c.guardar(
        "modelo",
        "/predictivo/modelos/{modelo_version_id}",
        recortar(c.get("/predictivo/modelos/{m}", m=scoring["modelo_version_id"]), 4),
    )
    c.guardar(
        "drift",
        "/predictivo/scorings/{scoring_run_id}/drift",
        recortar(c.get("/predictivo/scorings/{s}/drift", s=scoring_id)),
    )
    c.guardar(
        "predicciones",
        "/predictivo/scorings/{scoring_run_id}/predicciones",
        c.get("/predictivo/scorings/{s}/predicciones", {"por_pagina": FILAS}, s=scoring_id),
    )
    c.guardar(
        "explicacionPredictiva",
        "/predictivo/scorings/{scoring_run_id}/cuentas/{cuenta_id}/explicacion",
        recortar(
            c.get("/predictivo/scorings/{s}/cuentas/{k}/explicacion", s=scoring_id, k=cuenta_id)
        ),
    )

    # optimization/v1
    optimizacion_id = contexto["optimizacion_run_id"]
    base = "/optimizacion/ejecuciones/{optimizacion_run_id}"
    c.guardar("optimizacion", base, c.get("/optimizacion/ejecuciones/{o}", o=optimizacion_id))
    for nombre, sufijo, params in (
        ("capacidad", "/capacidad", {"por_pagina": 50}),
        ("restricciones", "/restricciones", {"por_pagina": 50}),
        ("resultados", "/resultados", {"por_pagina": FILAS}),
        ("diagnostico", "/diagnostico", None),
    ):
        datos = c.get("/optimizacion/ejecuciones/{o}" + sufijo, params, o=optimizacion_id)
        c.guardar(nombre, base + sufijo, recortar(datos))
    c.guardar(
        "explicacionOptimizacion",
        base + "/cuentas/{cuenta_id}",
        c.get("/optimizacion/ejecuciones/{o}/cuentas/{k}", o=optimizacion_id, k=cuenta_id),
    )

    # campo/v2 y ruteo/v2
    campo_id = contexto["campo_run_id"]
    plan = c.get("/campo-v2/planes/{p}", p=campo_id)
    c.guardar("planCampo", "/campo-v2/planes/{campo_run_id}", plan)
    c.guardar(
        "cargas",
        "/campo-v2/planes/{campo_run_id}/cargas",
        c.get("/campo-v2/planes/{p}/cargas", {"por_pagina": 50}, p=campo_id),
    )
    ruteo_id = contexto["ruteo_run_id"]
    ruteo = c.get("/ruteo-v2/ejecuciones/{r}", r=ruteo_id)
    c.guardar("ruteo", "/ruteo-v2/ejecuciones/{ruteo_run_id}", ruteo)
    rutas = c.get("/ruteo-v2/ejecuciones/{r}/rutas", {"por_pagina": 50}, r=ruteo_id)
    c.guardar("rutas", "/ruteo-v2/ejecuciones/{ruteo_run_id}/rutas", rutas)
    ruta_id = cuenta["ruteo"]["parada"]["ruta_id"] if cuenta["ruteo"]["parada"] else None
    ruta_id = ruta_id or rutas["elementos"][0]["ruta_id"]
    c.guardar("ruta", "/ruteo-v2/rutas/{ruta_id}", c.get("/ruteo-v2/rutas/{r}", r=ruta_id))
    c.guardar(
        "paradas",
        "/app/v1/ruteo/rutas/{ruta_id}/paradas",
        c.get("/app/v1/ruteo/rutas/{r}/paradas", {"por_pagina": 50}, r=ruta_id),
    )

    c.escribir()
    print(f"{DESTINO}: {len(c.constantes)} capturas, {DESTINO.stat().st_size} bytes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
