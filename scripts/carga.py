"""Las pruebas de carga del compose de produccion: k6 (infra/carga/motor-cartera.js) con el perfil y
los umbrales registrados en docs/carga.md.

    python scripts/carga.py --escenario api|saldo|edge|limite [--tasa N] [--duracion 60s]
                            [--resultados resultados/carga]

Corre k6, con su imagen fijada por su digest, en un contenedor dentro de la red del compose: a la
API por la red frontal (api y saldo) o al edge por la red publica (edge y limite), con la CA del
compose para verificar su certificado. Las credenciales (la clave de servicio de lectura, o la
cookie de una sesion que se abre en Dex para la prueba con el usuario lector) van en archivos de un
directorio temporal montado de solo lectura, nunca en el entorno ni en la linea de comandos, y se
borran al terminar. Mientras corre, toma cada 5 s el CPU y la memoria de los contenedores que
atienden. Deja el resumen de k6 y esas muestras en --resultados.

Termina con el codigo de k6: 0 si se cumplieron los umbrales, 99 si no.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
PROYECTO = "motor-cartera-produccion"
IMAGEN = "grafana/k6:1.8.1@sha256:23f2279054c3e01535455c4d92914a625df12aeb631ea0e3ea7a43f96bcbc843"
EDGE = "https://motor-cartera.localhost:8443"
TASAS = {"api": 25, "saldo": 2, "edge": 10, "limite": 80}
DURACIONES = {"api": "60s", "saldo": "60s", "edge": "60s", "limite": "10s"}
OBSERVADOS = ("api", "edge", "oauth2-proxy", "valkey", "postgres", "worker", "otel-collector")


def _humo():
    ruta = RAIZ / "scripts" / "prueba_de_humo_produccion.py"
    especificacion = importlib.util.spec_from_file_location("prueba_de_humo_produccion", ruta)
    modulo = importlib.util.module_from_spec(especificacion)
    sys.modules[especificacion.name] = modulo
    especificacion.loader.exec_module(modulo)
    return modulo


def _sesion(secretos: Path) -> str:
    """La cookie de una sesion nueva del usuario lector, abierta por el flujo OIDC completo."""
    humo = _humo()
    humo._resolver_localhost()
    usuario = json.loads((secretos / "usuarios.json").read_text(encoding="utf-8"))["lector"]
    contexto = ssl.create_default_context(cafile=str(secretos / "ca_certificado"))
    navegador = humo.iniciar_sesion(EDGE, contexto, usuario["correo"], usuario["contrasena"])
    return navegador.cookie().value


def _ip_del_edge() -> str:
    salida = subprocess.run(
        [
            "docker",
            "inspect",
            "-f",
            '{{(index .NetworkSettings.Networks "' + PROYECTO + '_publica").IPAddress}}',
            f"{PROYECTO}-edge-1",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return salida.stdout.strip()


class Muestreo(threading.Thread):
    """El CPU y la memoria de los contenedores que atienden, cada 5 s, mientras corre k6."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.alto = threading.Event()
        self.muestras: dict[str, dict[str, float]] = {}

    def run(self) -> None:
        nombres = [f"{PROYECTO}-{s}-1" for s in OBSERVADOS]
        while not self.alto.is_set():
            salida = subprocess.run(
                ["docker", "stats", "--no-stream", "--format", "{{json .}}", *nombres],
                capture_output=True,
                text=True,
                check=False,
            ).stdout
            for linea in salida.splitlines():
                try:
                    fila = json.loads(linea)
                except ValueError:
                    continue
                servicio = fila["Name"].removeprefix(f"{PROYECTO}-").removesuffix("-1")
                cpu = float(fila["CPUPerc"].rstrip("%") or 0)
                memoria = _mib(fila["MemUsage"].split("/")[0].strip())
                actual = self.muestras.setdefault(
                    servicio, {"cpu_max_pct": 0.0, "mem_max_mib": 0.0}
                )
                actual["cpu_max_pct"] = max(actual["cpu_max_pct"], cpu)
                actual["mem_max_mib"] = max(actual["mem_max_mib"], memoria)
            self.alto.wait(5)


def _mib(texto: str) -> float:
    unidades = {"B": 1 / 2**20, "KiB": 1 / 1024, "MiB": 1, "GiB": 1024}
    for unidad, factor in sorted(unidades.items(), key=lambda u: -len(u[0])):
        if texto.endswith(unidad):
            return round(float(texto.removesuffix(unidad)) * factor, 1)
    return 0.0


def main(argv: list[str] | None = None) -> int:
    argumentos = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    argumentos.add_argument("--escenario", choices=sorted(TASAS), required=True)
    argumentos.add_argument("--tasa", type=int)
    argumentos.add_argument("--duracion")
    argumentos.add_argument(
        "--etiqueta", help="Sufijo del archivo de resultados (p. ej. sin-otel)."
    )
    argumentos.add_argument("--secretos", type=Path, default=RAIZ / "secretos")
    argumentos.add_argument("--resultados", type=Path, default=RAIZ / "resultados" / "carga")
    leidos = argumentos.parse_args(argv)
    escenario = leidos.escenario
    tasa = leidos.tasa or TASAS[escenario]
    duracion = leidos.duracion or DURACIONES[escenario]
    nombre = escenario + (f"-{leidos.etiqueta}" if leidos.etiqueta else "")
    resultados = leidos.resultados.resolve()
    resultados.mkdir(parents=True, exist_ok=True)
    temporal = Path(tempfile.mkdtemp(prefix="mc-carga-"))
    try:
        os.chmod(temporal, 0o755)
        shutil.copyfile(leidos.secretos / "ca_certificado", temporal / "ca.pem")
        if escenario in ("edge", "limite"):
            (temporal / "sesion").write_text(_sesion(leidos.secretos), encoding="ascii")
            red = f"{PROYECTO}_publica"
            extra = ["--add-host", f"motor-cartera.localhost:{_ip_del_edge()}"]
        else:
            claves = json.loads((leidos.secretos / "claves_humo").read_text(encoding="utf-8"))
            (temporal / "clave").write_text(claves["lectura"], encoding="ascii")
            red = f"{PROYECTO}_frontal"
            extra = []
        for archivo in temporal.iterdir():
            os.chmod(archivo, 0o644)
        usuario = ["--user", f"{os.getuid()}:{os.getgid()}"] if hasattr(os, "getuid") else []
        comando = [
            "docker", "run", "--rm", "--network", red, *extra, *usuario,
            "-v", f"{RAIZ / 'infra' / 'carga'}:/carga:ro",
            "-v", f"{temporal}:/secretos:ro",
            "-v", f"{resultados}:/resultados",
            "-e", f"MC_CARGA_ESCENARIO={escenario}",
            "-e", f"MC_CARGA_TASA={tasa}",
            "-e", f"MC_CARGA_DURACION={duracion}",
            "-e", "SSL_CERT_FILE=/secretos/ca.pem",
            IMAGEN, "run", "--quiet",
            "--summary-export", f"/resultados/{nombre}.json",
            "/carga/motor-cartera.js",
        ]  # fmt: skip
        muestreo = Muestreo()
        muestreo.start()
        inicio = time.monotonic()
        resultado = subprocess.run(
            comando, env={**os.environ, "MSYS_NO_PATHCONV": "1"}, check=False
        )
        muestreo.alto.set()
        muestreo.join(timeout=10)
        (resultados / f"{nombre}-recursos.json").write_bytes(
            (
                json.dumps(
                    {
                        "escenario": escenario,
                        "tasa_por_segundo": tasa,
                        "duracion": duracion,
                        "segundos_de_pared": round(time.monotonic() - inicio, 1),
                        "codigo_k6": resultado.returncode,
                        "recursos": muestreo.muestras,
                    },
                    indent=2,
                )
                + "\n"
            ).encode()
        )
        return resultado.returncode
    finally:
        shutil.rmtree(temporal, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
