"""Prueba de humo: lo que la API hace por si sola, contra una API que ya corre.

La API no ejecuta ningun motor: registra el trabajo en la cola durable y responde. Esta prueba
sube una cartera y comprueba que la corrida queda EN_PROCESO, con su flujo automatico en la
ingesta y el trabajo de la ingesta en la cola; que el mismo archivo no se registra dos veces; que
la decision no se pide a mano mientras el flujo la va a correr, y que un flujo en proceso no se
reanuda. Al final revisa que el OpenAPI documente la orquestacion. Ejecutar el flujo le toca a un
worker, que este Compose todavia no levanta: aqui el flujo se queda en la cola, y eso es lo que se
comprueba. Verifica los codigos HTTP de cada paso. Solo usa la biblioteca estandar, para correr
igual en el CI, dentro del contenedor o en una laptop:

    python scripts/prueba_de_humo.py datos/cartera_sintetica.xlsx

Lee MC_URL_API (por omision http://localhost:8000) y MC_API_KEY. Termina con codigo 1 en
cuanto algo no sale como debe.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE = os.environ.get("MC_URL_API", "http://localhost:8000").rstrip("/")
CLAVE = os.environ.get("MC_API_KEY", "clave-local-de-desarrollo")
ESPERA_MAXIMA = 120  # segundos
PAQUETE = Path(__file__).resolve().parents[1] / "src" / "motor_cartera" / "__init__.py"


def pedir(metodo, ruta, *, cuerpo=None, tipo=None, con_clave=True, timeout=30):
    encabezados = {"X-API-Key": CLAVE} if con_clave else {}
    if tipo:
        encabezados["Content-Type"] = tipo
    peticion = urllib.request.Request(BASE + ruta, data=cuerpo, headers=encabezados, method=metodo)
    try:
        with urllib.request.urlopen(peticion, timeout=timeout) as respuesta:
            return respuesta.status, dict(respuesta.headers), json.loads(respuesta.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), json.loads(error.read() or b"{}")


def subir(archivo: Path, *, con_clave=True):
    frontera = uuid.uuid4().hex
    cuerpo = b"".join(
        [
            f"--{frontera}\r\n".encode(),
            f'Content-Disposition: form-data; name="archivo"; filename="{archivo.name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n".encode(),
            archivo.read_bytes(),
            f"\r\n--{frontera}--\r\n".encode(),
        ]
    )
    tipo = f"multipart/form-data; boundary={frontera}"
    return pedir("POST", "/corridas", cuerpo=cuerpo, tipo=tipo, con_clave=con_clave)


def esperar(condicion: bool, descripcion: str) -> None:
    print(f"  {'ok ' if condicion else 'MAL'} {descripcion}")
    if not condicion:
        sys.exit(1)


def version_del_repositorio() -> str:
    """La version que el repositorio declara; la API que corre tiene que ser esa."""
    return re.search(r'__version__ = "([^"]+)"', PAQUETE.read_text(encoding="utf-8")).group(1)


def esperar_a_la_api() -> tuple[int, dict]:
    """La API puede estar arrancando todavia (docker compose up -d no espera)."""
    limite = time.monotonic() + ESPERA_MAXIMA
    while True:
        try:
            estado, _, salud = pedir("GET", "/salud", con_clave=False)
            if estado == 200 or time.monotonic() > limite:
                return estado, salud
        except (urllib.error.URLError, ConnectionError):
            if time.monotonic() > limite:
                return 0, {}
        time.sleep(1)


def main(archivo: Path) -> None:
    print(f"Prueba de humo contra {BASE} con {archivo}")

    estado, salud = esperar_a_la_api()
    esperar(estado == 200 and salud.get("base_de_datos") == "ok", "GET /salud 200, base ok")

    estado, _, error = subir(archivo, con_clave=False)
    esperar(estado == 401 and error["codigo"] == "API_KEY_AUSENTE", "POST sin clave: 401")

    estado, encabezados, corrida = subir(archivo)
    esperar(estado == 201 and corrida["estado"] == "EN_PROCESO", "POST /corridas: 201 EN_PROCESO")
    run_id = corrida["run_id"]
    ubicacion = encabezados.get("location") or encabezados.get("Location")
    esperar(ubicacion == f"/corridas/{run_id}", f"Location: {ubicacion}")

    estado, _, error = subir(archivo)
    esperar(
        estado == 409 and error["codigo"] == "ARCHIVO_EN_PROCESO" and error["run_id"] == run_id,
        f"el mismo archivo otra vez: 409 {error.get('codigo')}, con la corrida que lo tiene",
    )

    # La corrida nace con su flujo, en la ingesta, y con el trabajo de la ingesta en la cola.
    estado, _, flujo = pedir("GET", f"/corridas/{run_id}/flujo")
    esperar(
        estado == 200
        and (flujo["estado"], flujo["etapa"]) == ("EN_PROCESO", "INGESTA")
        and flujo["run_id"] == run_id,
        f"GET /corridas/{{run_id}}/flujo: {estado} {flujo.get('estado')} en "
        f"{flujo.get('etapa')}: {flujo.get('detalle')}",
    )
    flujo_id = flujo["flujo_id"]
    estado, _, flujo_por_id = pedir("GET", f"/flujos/{flujo_id}")
    esperar(
        estado == 200 and flujo_por_id == flujo,
        f"GET /flujos/{{flujo_id}}: {estado}, el mismo flujo",
    )

    estado, _, trabajos = pedir("GET", f"/flujos/{flujo_id}/trabajos")
    elementos = trabajos.get("elementos", [])
    esperar(
        estado == 200
        and trabajos["total"] == 1
        and (elementos[0]["tipo"], elementos[0]["estado"]) == ("INGESTA", "PENDIENTE")
        and elementos[0]["objetivo_run_id"] == run_id,
        f"GET /flujos/{{flujo_id}}/trabajos: {estado}, {trabajos.get('total')} trabajo: la "
        "ingesta, PENDIENTE",
    )
    trabajo = elementos[0]
    estado, _, consultado = pedir("GET", f"/trabajos/{trabajo['trabajo_id']}")
    esperar(
        estado == 200 and consultado == trabajo and "worker_id" not in consultado,
        f"GET /trabajos/{{trabajo_id}}: {estado}, el mismo, sin decir que worker lo tendria",
    )

    # El flujo va a decidir la corrida por su cuenta: la decision no se pide a mano, y un flujo
    # que sigue en proceso no se reanuda.
    estado, encabezados, error = pedir("POST", f"/corridas/{run_id}/decisiones")
    esperar(
        estado == 409
        and error["codigo"] == "FLUJO_EN_PROCESO"
        and not (encabezados.get("location") or encabezados.get("Location")),
        f"POST /corridas/{{run_id}}/decisiones: {estado} {error.get('codigo')}, sin Location",
    )
    estado, _, error = pedir("POST", f"/flujos/{flujo_id}/reanudar")
    esperar(
        estado == 409 and error["codigo"] == "FLUJO_EN_PROCESO",
        f"POST /flujos/{{flujo_id}}/reanudar: {estado} {error.get('codigo')}",
    )

    estado, _, openapi = pedir("GET", "/openapi.json", con_clave=False)
    documentadas = openapi.get("paths", {})
    version = openapi.get("info", {}).get("version")
    orquestacion = (
        "/corridas/{run_id}/flujo",
        "/flujos/{flujo_id}",
        "/flujos/{flujo_id}/trabajos",
        "/trabajos/{trabajo_id}",
        "/flujos/{flujo_id}/reanudar",
    )
    esperar(
        estado == 200
        and version == version_del_repositorio()
        and all(ruta in documentadas for ruta in orquestacion),
        f"GET /openapi.json {estado}: version {version}, con las rutas de la orquestacion",
    )
    print("Todo en orden.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(Path(sys.argv[1]))
