"""Exporta el contrato OpenAPI de la superficie web: el de la API, solo con lo que su proxy deja
pasar.

La app web no ve la API entera: su proxy (web/nginx/rutas-api.conf) deja pasar solo GET de unas
rutas. Este contrato es exactamente eso: cada operacion GET cuya ruta, con sus parametros
llenos, cumple una expresion de la lista blanca, y los esquemas que esas operaciones usan. De el se
generan los tipos de TypeScript de la app (`npm run api:tipos`), asi que la app no puede ni
escribir una llamada a una ruta que su proxy no deja pasar.

La salida es determinista (llaves ordenadas, sangria de dos espacios, LF): el CI la vuelve a generar
y compara con la del repositorio, junto con los tipos.

    python scripts/exportar_contrato_web.py                 # escribe web/src/api/openapi.json
    python scripts/exportar_contrato_web.py --verificar     # sale con 1 si el archivo no coincide
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
LISTA_BLANCA = RAIZ / "web" / "nginx" / "rutas-api.conf"
DESTINO = RAIZ / "web" / "src" / "api" / "openapi.json"
PREFIJO = "/api"
EJEMPLO = "00000000-0000-4000-8000-000000000000"
"""Con que se llena cada parametro de ruta para compararla con la lista blanca: todos los de la
API son UUID."""


def rutas_permitidas(texto: str | None = None) -> list[re.Pattern[str]]:
    """Las expresiones de la lista blanca, como las lee nginx: una por linea `"~<expresion>" 1;`
    (entre comillas, que nginx exige si la expresion lleva llaves)."""
    texto = LISTA_BLANCA.read_text(encoding="utf-8") if texto is None else texto
    return [
        re.compile(m.group(1))
        for linea in texto.splitlines()
        if (m := re.fullmatch(r'"?~([^"\s]+)"?\s+1;', linea.strip()))
    ]


def ruta_de_ejemplo(ruta: str) -> str:
    return PREFIJO + re.sub(r"\{[^}]+\}", EJEMPLO, ruta)


def permitida(ruta: str, expresiones: list[re.Pattern[str]]) -> bool:
    return any(e.search(ruta_de_ejemplo(ruta)) for e in expresiones)


def contrato(api: dict, expresiones: list[re.Pattern[str]]) -> dict:
    """El OpenAPI de `api` reducido a las operaciones GET que el proxy deja pasar y a los esquemas
    que usan, sin la autenticacion: la pone el proxy, no la app."""
    rutas = {}
    for ruta, metodos in api["paths"].items():
        if "get" in metodos and permitida(ruta, expresiones):
            operacion = {k: v for k, v in metodos["get"].items() if k != "security"}
            rutas[ruta] = {"get": operacion}
    esquemas = api["components"]["schemas"]
    usados: set[str] = set()

    def recorrer(nodo) -> None:
        if isinstance(nodo, dict):
            referencia = nodo.get("$ref")
            if isinstance(referencia, str) and referencia.startswith("#/components/schemas/"):
                nombre = referencia.rsplit("/", 1)[1]
                if nombre not in usados:
                    usados.add(nombre)
                    recorrer(esquemas[nombre])
            for valor in nodo.values():
                recorrer(valor)
        elif isinstance(nodo, list):
            for valor in nodo:
                recorrer(valor)

    recorrer(rutas)
    return {
        "openapi": api["openapi"],
        "info": {
            "title": "motor-cartera: la superficie web",
            "version": api["info"]["version"],
            "description": "Las operaciones GET de la API que el proxy de la superficie web deja "
            "pasar (web/nginx/rutas-api.conf), con los esquemas que usan. Generado por "
            "scripts/exportar_contrato_web.py: no se edita a mano.",
        },
        "paths": dict(sorted(rutas.items())),
        "components": {"schemas": {n: esquemas[n] for n in sorted(usados)}},
    }


def generar() -> str:
    from motor_cartera.api import crear_app
    from motor_cartera.config import Config

    api = crear_app(Config(app_env="development", api_key="exportacion-del-contrato")).openapi()
    texto = json.dumps(
        contrato(api, rutas_permitidas()), ensure_ascii=False, indent=2, sort_keys=True
    )
    return texto + "\n"


def main() -> int:
    argumentos = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    argumentos.add_argument(
        "--verificar", action="store_true", help="No escribe: sale con 1 si el archivo no coincide."
    )
    argumentos.add_argument("--destino", type=Path, default=DESTINO)
    leidos = argumentos.parse_args()
    texto = generar()
    if leidos.verificar:
        actual = leidos.destino.read_bytes().decode("utf-8") if leidos.destino.exists() else ""
        if actual != texto:
            print(
                f"{leidos.destino} no corresponde a la API: corre "
                "`python scripts/exportar_contrato_web.py` y `npm run api:tipos` en web/.",
                file=sys.stderr,
            )
            return 1
        print(f"{leidos.destino}: el contrato de la superficie web corresponde a la API.")
        return 0
    leidos.destino.parent.mkdir(parents=True, exist_ok=True)
    leidos.destino.write_bytes(texto.encode("utf-8"))
    print(f"{leidos.destino}: {len(json.loads(texto)['paths'])} rutas.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
