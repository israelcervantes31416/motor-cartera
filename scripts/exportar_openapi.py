"""Exporta el contrato OpenAPI completo de la API: el asset `openapi-vX.Y.Z.json` de cada release.

Es el OpenAPI que la API sirve en `/openapi.json` en desarrollo (en produccion no se publica): cada
operacion, sus esquemas, sus errores y su esquema de seguridad. La salida es determinista (llaves
ordenadas, sangria de dos espacios, UTF-8 y LF), asi que el mismo commit da los mismos bytes en
cualquier maquina y en cada Python soportado: la matriz de compatibilidad lo compara entre 3.12,
3.13 y 3.14, y el workflow de release lo publica con su SHA-256 y su atestacion.

    python scripts/exportar_openapi.py --destino openapi.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def generar() -> str:
    from motor_cartera.api import crear_app
    from motor_cartera.config import Config

    api = crear_app(Config(app_env="development", api_key="exportacion-del-contrato")).openapi()
    return json.dumps(api, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    argumentos = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    argumentos.add_argument("--destino", type=Path, required=True)
    leidos = argumentos.parse_args()
    texto = generar()
    leidos.destino.parent.mkdir(parents=True, exist_ok=True)
    leidos.destino.write_bytes(texto.encode("utf-8"))
    api = json.loads(texto)
    print(
        f"{leidos.destino}: OpenAPI {api['openapi']} de motor-cartera {api['info']['version']}, "
        f"{len(api['paths'])} rutas y {len(api['components']['schemas'])} esquemas."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
