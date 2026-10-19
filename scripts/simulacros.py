"""Los simulacros de falla del compose de produccion: que pasa cuando una dependencia cae y como se
recupera, medido.

    python scripts/simulacros.py [--solo base,api,worker,osrm,telemetria] [--informe archivo.json]

Corre en la maquina que levanto el compose (usa el CLI de Docker) contra el proyecto
motor-cartera-produccion ya levantado y sano. Cada simulacro rompe una sola cosa, mide y la repone;
solo detiene, arranca e inspecciona contenedores de este proyecto. Las mediciones se hacen desde
adentro de las redes internas (la API y el worker tienen Python): ningun puerto interno se publica
para esto. Las escrituras usan la clave de servicio de la prueba de humo (secretos/claves_humo),
que viaja por la entrada estandar y no por la linea de comandos.

- base: PostgreSQL se detiene. La API y el worker siguen vivos (liveness 200), dejan de estar listos
  (readiness 503) sin reiniciarse y sin exponer la conexion ni una traza, y al volver la base se
  recuperan solos.
- api: la API recibe SIGTERM y termina con codigo 0 dentro de su gracia; el worker sigue listo sin
  ella, y la API vuelve a estar lista.
- worker: el worker termina con codigo 0 dentro de su gracia; una corrida que llega sin worker
  espera en la cola durable y se completa cuando vuelve.
- osrm: sin OSRM el worker sigue listo (su readiness no depende de el) e informa el ruteo como no
  disponible (mc_ruteo_disponible 0); al volver, disponible. Sin el perfil ruteo levantado se
  revisa eso mismo sin detener nada.
- telemetria: sin el colector de OpenTelemetry la API sigue respondiendo con su latencia
  (exportar falla en segundo plano) y sus metricas se siguen sirviendo; al volver, las trazas
  vuelven a llegar.

Escribe en la salida una linea por comprobacion y, con --informe, las mediciones en JSON. Termina
con 1 si alguna comprobacion fallo.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
COMPOSE = RAIZ / "docker-compose.production.yml"
PROYECTO = "motor-cartera-produccion"
SIMULACROS = ("base", "api", "worker", "osrm", "telemetria")
CONTEO = {"ok": 0, "mal": 0, "no_verificables": 0}
MEDICIONES: dict[str, dict] = {}

# Lo que corre adentro de un contenedor: N peticiones, con su estado y su latencia. Los parametros
# (la URL, las cabeceras con la clave, el cuerpo) llegan por la entrada estandar.
SONDA = r"""
import json, sys, time, urllib.error, urllib.request
p = json.load(sys.stdin)
datos = p["cuerpo"].encode("latin-1") if p["cuerpo"] is not None else None
resultados = []
for _ in range(p["repeticiones"]):
    peticion = urllib.request.Request(
        p["url"], data=datos, method=p["metodo"], headers=p["cabeceras"]
    )
    inicio = time.perf_counter()
    try:
        with urllib.request.urlopen(peticion, timeout=p["timeout"]) as r:
            estado, texto = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        estado, texto = e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        estado, texto = 0, type(e).__name__
    resultados.append([estado, texto[:500_000], (time.perf_counter() - inicio) * 1000])
print(json.dumps(resultados))
"""


def esperar(condicion: bool, descripcion: str) -> bool:
    CONTEO["ok" if condicion else "mal"] += 1
    print(f"  {'ok ' if condicion else 'MAL'} {descripcion}", flush=True)
    return condicion


def no_verificable(descripcion: str) -> None:
    CONTEO["no_verificables"] += 1
    print(f"  --  {descripcion}", flush=True)


def seccion(titulo: str) -> None:
    print(f"\n {titulo}", flush=True)


def compose(*argumentos: str, entrada: str | None = None) -> subprocess.CompletedProcess:
    entorno = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    return subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), "-p", PROYECTO, *argumentos],
        input=entrada,
        capture_output=True,
        text=True,
        env=entorno,
        check=False,
    )


def pedir(
    desde: str,
    url: str,
    *,
    metodo: str = "GET",
    cabeceras: dict[str, str] | None = None,
    cuerpo: str | None = None,
    repeticiones: int = 1,
    timeout: float = 5,
) -> list[tuple[int, str, float]]:
    """Las respuestas de `url` pedida desde adentro del contenedor del servicio `desde`."""
    parametros = {
        "url": url,
        "metodo": metodo,
        "cabeceras": cabeceras or {},
        "cuerpo": cuerpo,
        "repeticiones": repeticiones,
        "timeout": timeout,
    }
    salida = compose("exec", "-T", desde, "python", "-c", SONDA, entrada=json.dumps(parametros))
    if salida.returncode != 0:
        return [(0, salida.stderr[-500:], 0.0)] * repeticiones
    return [tuple(r) for r in json.loads(salida.stdout)]


def estado_de(desde: str, url: str) -> int:
    return pedir(desde, url)[0][0]


def hasta(condicion: Callable[[], bool], limite: float, intervalo: float = 0.5) -> float | None:
    """Los segundos que tardo `condicion` en cumplirse, o None si no se cumplio en `limite`."""
    inicio = time.monotonic()
    while time.monotonic() - inicio < limite:
        if condicion():
            return round(time.monotonic() - inicio, 1)
        time.sleep(intervalo)
    return None


def contenedor(servicio: str) -> dict:
    identificador = compose("ps", "-a", "-q", servicio).stdout.strip()
    if not identificador:
        return {}
    salida = subprocess.run(
        ["docker", "inspect", "-f", "{{json .State}} {{.RestartCount}}", identificador],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    estado, reinicios = salida.rsplit(" ", 1)
    return {**json.loads(estado), "reinicios": int(reinicios)}


def corriendo(servicio: str) -> bool:
    return contenedor(servicio).get("Status") == "running"


def detener(servicio: str, gracia: int) -> tuple[float, int | None]:
    inicio = time.monotonic()
    compose("stop", "-t", str(gracia), servicio)
    return round(time.monotonic() - inicio, 1), contenedor(servicio).get("ExitCode")


def _segundos(valor: float | None) -> str:
    return "no se cumplio" if valor is None else f"{valor} s"


# --- los simulacros -----------------------------------------------------------------------------

API_LISTA = "http://api:8000/health/ready"
API_VIVA = "http://api:8000/health/live"
WORKER_LISTO = "http://worker:9465/health/ready"
WORKER_VIVO = "http://worker:9465/health/live"


def simulacro_base() -> None:
    seccion("PostgreSQL se detiene")
    reinicios = {s: contenedor(s).get("reinicios") for s in ("api", "worker")}
    if not esperar(
        estado_de("worker", API_LISTA) == 200 and estado_de("api", WORKER_LISTO) == 200,
        "antes: la API y el worker estan listos",
    ):
        return
    compose("stop", "postgres")
    api_nota = hasta(lambda: estado_de("worker", API_LISTA) == 503, 30)
    worker_nota = hasta(lambda: estado_de("api", WORKER_LISTO) == 503, 30)
    esperar(api_nota is not None, f"la API deja de estar lista: 503 ({_segundos(api_nota)})")
    esperar(
        worker_nota is not None, f"el worker deja de estar listo: 503 ({_segundos(worker_nota)})"
    )
    esperar(estado_de("worker", API_VIVA) == 200, "la API sigue viva: liveness 200")
    esperar(estado_de("api", WORKER_VIVO) == 200, "el worker sigue vivo: liveness 200")
    _, cuerpo, _ = pedir("worker", API_LISTA)[0]
    esperar(
        not any(t in cuerpo for t in ("postgresql", "Traceback", "password", "mc_api")),
        "la readiness dice que la base no contesta, sin la conexion ni una traza",
    )
    compose("start", "postgres")
    vuelta = hasta(
        lambda: estado_de("worker", API_LISTA) == 200 and estado_de("api", WORKER_LISTO) == 200,
        120,
    )
    esperar(
        vuelta is not None, f"al volver la base, los dos se recuperan solos ({_segundos(vuelta)})"
    )
    despues = {s: contenedor(s).get("reinicios") for s in ("api", "worker")}
    esperar(despues == reinicios, "ni la API ni el worker se reiniciaron")
    MEDICIONES["base"] = {
        "api_deja_de_estar_lista_s": api_nota,
        "worker_deja_de_estar_listo_s": worker_nota,
        "recuperacion_s": vuelta,
    }


def simulacro_api() -> None:
    seccion("La API se reinicia")
    if not esperar(estado_de("worker", API_LISTA) == 200, "antes: la API esta lista"):
        return
    duracion, codigo = detener("api", 25)
    esperar(codigo == 0, f"con SIGTERM la API termina limpia: codigo {codigo} en {duracion} s")
    esperar(
        estado_de("worker", "http://127.0.0.1:9465/health/ready") == 200,
        "sin la API, el worker sigue listo",
    )
    inicio = time.monotonic()
    compose("start", "api")
    vuelta = hasta(lambda: estado_de("worker", API_LISTA) == 200, 90)
    esperar(vuelta is not None, f"la API vuelve a estar lista ({_segundos(vuelta)})")
    MEDICIONES["api"] = {
        "parada_s": duracion,
        "codigo_de_salida": codigo,
        "arranque_hasta_lista_s": round(time.monotonic() - inicio, 1) if vuelta else None,
    }


def _cartera() -> tuple[str, str]:
    """Un CSV de una cuenta, distinto en cada simulacro (el mismo archivo dos veces es 409)."""
    frontera = uuid.uuid4().hex
    saldo = 3000 + uuid.uuid4().int % 100_000 / 100
    csv = (
        "cliente_unico,saldo_total,dias_atraso,producto,canal,cve_entidad,cve_municipio,"
        f"fecha_corte\nCU00009977,{saldo:.2f},120,CONSUMO,CAMPO,21,114,2026-09-30\n"
    )
    cuerpo = (
        f"--{frontera}\r\n"
        'Content-Disposition: form-data; name="archivo"; filename="simulacro.csv"\r\n'
        "Content-Type: text/csv\r\n\r\n"
        f"{csv}\r\n--{frontera}--\r\n"
    )
    return cuerpo, f"multipart/form-data; boundary={frontera}"


def simulacro_worker(clave: str) -> None:
    seccion("El worker se detiene y la cola espera")
    if not esperar(estado_de("api", WORKER_LISTO) == 200, "antes: el worker esta listo"):
        return
    duracion, codigo = detener("worker", 35)
    esperar(codigo == 0, f"con SIGTERM el worker termina limpio: codigo {codigo} en {duracion} s")
    cuerpo, tipo = _cartera()
    estado, respuesta, _ = pedir(
        "api",
        "http://127.0.0.1:8000/corridas",
        metodo="POST",
        cabeceras={"X-API-Key": clave, "Content-Type": tipo},
        cuerpo=cuerpo,
        timeout=30,
    )[0]
    if not esperar(estado == 201, f"sin worker, la API acepta una corrida ({estado})"):
        compose("start", "worker")
        return
    corrida = json.loads(respuesta)["run_id"]

    def estado_de_la_corrida() -> str:
        _, texto, _ = pedir(
            "api", f"http://127.0.0.1:8000/corridas/{corrida}", cabeceras={"X-API-Key": clave}
        )[0]
        try:
            return json.loads(texto)["estado"]
        except (ValueError, KeyError):
            return "?"

    time.sleep(3)
    esperar(estado_de_la_corrida() == "EN_PROCESO", "la corrida espera en la cola: EN_PROCESO")
    compose("start", "worker")
    vuelta = hasta(lambda: estado_de_la_corrida() != "EN_PROCESO", 120, intervalo=1)
    final = estado_de_la_corrida()
    esperar(
        vuelta is not None and final == "EXITOSA",
        f"al volver el worker la completa: {final} ({_segundos(vuelta)})",
    )
    MEDICIONES["worker"] = {
        "parada_s": duracion,
        "codigo_de_salida": codigo,
        "hasta_completar_la_corrida_s": vuelta,
    }


def _ruteo_del_worker() -> tuple[int, str, str]:
    estado, cuerpo, _ = pedir("worker", "http://127.0.0.1:9465/health/ready")[0]
    try:
        ruteo = json.loads(cuerpo).get("ruteo", "?")
    except ValueError:
        ruteo = "?"
    _, metricas, _ = pedir("worker", "http://127.0.0.1:9465/metrics")[0]
    gauge = next(
        (
            linea.split()[-1]
            for linea in metricas.splitlines()
            if linea.startswith("mc_ruteo_disponible ")
        ),
        "",
    )
    return estado, ruteo, gauge


def simulacro_osrm() -> None:
    seccion("OSRM no responde")
    activo = corriendo("osrm")
    if activo:
        compose("stop", "osrm")
    else:
        no_verificable("el perfil ruteo no esta levantado: se revisa el worker sin OSRM")
    caida = hasta(lambda: _ruteo_del_worker()[2].startswith("0"), 60)
    estado, ruteo, _ = _ruteo_del_worker()
    esperar(estado == 200, "sin OSRM, el worker sigue listo (su readiness no depende de el)")
    esperar(
        ruteo == "no_disponible" and caida is not None,
        f"e informa el ruteo no disponible: mc_ruteo_disponible 0 ({_segundos(caida)})",
    )
    medicion = {"worker_lo_nota_s": caida}
    if activo:
        compose("start", "osrm")
        vuelta = hasta(lambda: _ruteo_del_worker()[2].startswith("1"), 120, intervalo=1)
        esperar(
            vuelta is not None,
            f"al volver OSRM, el ruteo vuelve a estar disponible ({_segundos(vuelta)})",
        )
        medicion["recuperacion_s"] = vuelta
    MEDICIONES["osrm"] = medicion


def _aceptados() -> float | None:
    _, cuerpo, _ = pedir(
        "api",
        "http://prometheus:9090/api/v1/query?query=sum(otelcol_receiver_accepted_spans)",
    )[0]
    try:
        return float(json.loads(cuerpo)["data"]["result"][0]["value"][1])
    except (ValueError, KeyError, IndexError):
        return None


def simulacro_telemetria() -> None:
    seccion("El colector de OpenTelemetry se detiene")
    if not corriendo("otel-collector"):
        no_verificable("el perfil observabilidad no esta levantado")
        return
    sana = pedir("api", "http://127.0.0.1:8000/health/live", repeticiones=40)
    compose("stop", "otel-collector")
    caida = pedir("api", "http://127.0.0.1:8000/health/live", repeticiones=40)
    mediana_sana = statistics.median(ms for _, _, ms in sana)
    mediana_caida = statistics.median(ms for _, _, ms in caida)
    esperar(all(e == 200 for e, _, _ in caida), "sin colector, la API responde las 40 peticiones")
    esperar(
        mediana_caida < max(50.0, 3 * mediana_sana),
        f"con su latencia: mediana {mediana_sana:.1f} ms con colector, "
        f"{mediana_caida:.1f} ms sin el",
    )
    esperar(
        estado_de("api", "http://127.0.0.1:9464/metrics") == 200,
        "sus metricas se siguen sirviendo",
    )
    antes = _aceptados()
    compose("start", "otel-collector")
    vivo = hasta(lambda: estado_de("api", "http://otel-collector:13133/") == 200, 60)
    esperar(vivo is not None, f"el colector vuelve ({_segundos(vivo)})")
    pedir("api", "http://127.0.0.1:8000/health/live", repeticiones=20)
    # El colector reinicio sus contadores: llegan trazas otra vez si el contador vuelve a subir.
    llegan = hasta(lambda: (_aceptados() or 0) > 0, 90, intervalo=3)
    esperar(llegan is not None, f"y las trazas vuelven a llegar ({_segundos(llegan)})")
    MEDICIONES["telemetria"] = {
        "mediana_ms_con_colector": round(mediana_sana, 2),
        "mediana_ms_sin_colector": round(mediana_caida, 2),
        "spans_aceptados_antes": antes,
        "trazas_de_vuelta_s": llegan,
    }


def main(argv: list[str] | None = None) -> int:
    argumentos = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    argumentos.add_argument("--solo", default=",".join(SIMULACROS))
    argumentos.add_argument("--claves", type=Path, default=RAIZ / "secretos" / "claves_humo")
    argumentos.add_argument("--informe", type=Path)
    leidos = argumentos.parse_args(argv)
    elegidos = [s.strip() for s in leidos.solo.split(",") if s.strip()]
    desconocidos = sorted(set(elegidos) - set(SIMULACROS))
    if desconocidos:
        argumentos.error(f"simulacros desconocidos: {', '.join(desconocidos)}")

    def worker() -> None:
        simulacro_worker(json.loads(leidos.claves.read_text(encoding="utf-8"))["ops"])

    acciones: dict[str, Callable[[], None]] = {
        "base": simulacro_base,
        "api": simulacro_api,
        "worker": worker,
        "osrm": simulacro_osrm,
        "telemetria": simulacro_telemetria,
    }
    inicio = time.monotonic()
    for nombre in elegidos:
        acciones[nombre]()
    MEDICIONES["duracion_total_s"] = round(time.monotonic() - inicio, 1)
    if leidos.informe is not None:
        leidos.informe.parent.mkdir(parents=True, exist_ok=True)
        leidos.informe.write_bytes(
            (json.dumps({"conteo": CONTEO, "mediciones": MEDICIONES}, indent=2) + "\n").encode()
        )
    print()
    if CONTEO["mal"]:
        print(f"{CONTEO['mal']} de {CONTEO['ok'] + CONTEO['mal']} comprobaciones fallaron.")
        return 1
    print(
        f"Todo en orden: {CONTEO['ok']} comprobaciones, "
        f"{CONTEO['no_verificables']} no verificables desde aqui."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
