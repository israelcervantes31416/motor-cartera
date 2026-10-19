"""Benchmark de la superficie web (v0.17): como responden, sobre 500,000 cuentas, las rutas que la
app lee, contra PostgreSQL. No corre en el CI: se pide a mano, sobre la base *_bench que ya tiene el
XL de los benchmarks anteriores (su corte de 500,000 cuentas con decision/v2, analytics/v1, el
scoring de predictive/model-v1, el plan de optimization/v1, campo/v2 y ruteo/v2):

    MC_DATABASE_URL=postgresql+psycopg://motor:motor@localhost:5434/cartera_bench \\
        python scripts/benchmark_app.py --destino <dir>

No construye ni publica nada. El contexto XL se DECLARA AQUI, antes de medir: de los cortes de la
base, el de mas cuentas que tiene una sugerencia DESDE_OPTIMIZACION con las seis ejecuciones (la que
el selector de la app propone), y de sus cuentas, la primera por cuenta_id ASIGNADA en CAMPO por esa
optimizacion (con una parada en su ruteo/v2), para la Cuenta 360. Cada ruta se pide dos veces para
calentar y despues REPETICIONES veces, con un cliente HTTP en el mismo proceso (el tiempo incluye la
serializacion); se reporta su mediana, su p95 y su maximo, los bytes de su respuesta, cuantas
sentencias emitio, cuanto tardaron en PostgreSQL (el EXPLAIN (ANALYZE, BUFFERS) de cada una) y si
alguna recorrio entera una tabla grande. Ninguna salida lleva un dato de una cuenta: solo tiempos,
conteos, identificadores opacos y planes.
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import statistics
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from benchmark_escala import memoria_pico_mb  # noqa: E402
from benchmark_lifecycle import _exigir_bench, _migrar  # noqa: E402

REPETICIONES = 20
CALENTAMIENTO = 2
EJECUCIONES = (
    "decision_run_id",
    "analytics_run_id",
    "scoring_run_id",
    "optimizacion_run_id",
    "campo_run_id",
    "ruteo_run_id",
)
TABLAS_GRANDES = (
    "snapshot_cuenta",
    "cuenta_canonica",
    "resultado_decision_v2",
    "prediccion_cuenta",
    "resultado_optimizacion_cuenta",
    "asignacion_campo_cuenta",
    "parada_ruta_v2",
    "agregado_analytics",
    "evento_lifecycle",
    "movimiento_economico_canonico",
)
PERSONALES = re.compile(
    r'"(cliente_unico|actor_ref|idempotency_key|observacion|telefono|curp|rfc|domicilio|latitud|'
    r'longitud|lat|lon|lng|geometry|geometria)"\s*:|CU\d{10}',
    re.IGNORECASE,
)


def _cliente():
    from fastapi.testclient import TestClient

    from motor_cartera.api.app import crear_app
    from motor_cartera.config import Config

    clave = "benchmark-app-" + "x" * 24
    return TestClient(
        crear_app(Config(app_env="development", api_key=clave)), headers={"X-API-Key": clave}
    )


def _leer(cliente, ruta: str) -> dict:
    respuesta = cliente.get(ruta)
    if respuesta.status_code != 200:
        sys.exit(f"{ruta}: {respuesta.status_code} {respuesta.text[:300]}")
    return respuesta.json()


def declarar_contexto(cliente) -> dict:
    """El contexto XL y la cuenta de la Cuenta 360, como se declaran arriba."""
    inicio = _leer(cliente, "/app/v1/contextos/opciones?por_pagina=1")
    candidatos = []
    for cartera in inicio["carteras"]:
        cortes = _leer(
            cliente, f"/app/v1/contextos/opciones?cartera_id={cartera['cartera_id']}&por_pagina=100"
        )
        for corte in cortes["cortes"]:
            opciones = _leer(cliente, f"/app/v1/contextos/opciones?corte_id={corte['corte_id']}")
            completa = next(
                (
                    s["contexto"]
                    for s in opciones["sugerencias"]
                    if s["codigo"] == "DESDE_OPTIMIZACION"
                    and all(s["contexto"].get(e) for e in EJECUCIONES)
                ),
                None,
            )
            if completa:
                candidatos.append((corte["cuentas"], corte["fecha_corte"], completa))
    if not candidatos:
        sys.exit("La base no tiene un corte con un contexto completo: no hay XL que medir.")
    cuentas, fecha, contexto = max(candidatos, key=lambda c: (c[0], c[1]))
    consulta = urlencode({k: v for k, v in contexto.items() if v})
    asignadas = _leer(
        cliente,
        f"/app/v1/cuentas?{consulta}&canal=CAMPO&estado_optimizacion=ASIGNADA&por_pagina=1",
    )
    if not asignadas["elementos"]:
        sys.exit("El plan XL no asigno ninguna cuenta a CAMPO.")
    cuenta = asignadas["elementos"][0]["cuenta_id"]
    detalle = _leer(cliente, f"/app/v1/cuentas/{cuenta}?{consulta}")
    parada = detalle["ruteo"]["parada"]
    return {
        "corte_id": contexto["corte_id"],
        "fecha_corte": fecha,
        "cuentas": cuentas,
        "contexto": contexto,
        "consulta": consulta,
        "cuenta": cuenta,
        "ruta": parada["ruta_id"] if parada else None,
        "modelo_version_id": _leer(cliente, f"/predictivo/scorings/{contexto['scoring_run_id']}")[
            "modelo_version_id"
        ],
    }


def rutas_de_la_app(x: dict) -> dict[str, str]:
    """Lo que piden las pantallas de la app, con el contexto XL."""
    c = x["contexto"]
    q = x["consulta"]
    a = f"/analytics/ejecuciones/{c['analytics_run_id']}"
    s = f"/predictivo/scorings/{c['scoring_run_id']}"
    o = f"/optimizacion/ejecuciones/{c['optimizacion_run_id']}"
    cuenta = f"/app/v1/cuentas/{x['cuenta']}"
    rutas = {
        # El selector y el contexto
        "opciones": "/app/v1/contextos/opciones?por_pagina=10",
        "opciones_del_corte": f"/app/v1/contextos/opciones?corte_id={x['corte_id']}",
        "validar": f"/app/v1/contextos/validar?{q}",
        # El resumen
        "resumen": f"/app/v1/resumen?{q}",
        "catalogo": "/analytics/catalogo",
        "distribucion": f"/app/v1/predictivo/scorings/{c['scoring_run_id']}/distribucion",
        # El explorador de cuentas
        "cuentas_pagina_1": f"/app/v1/cuentas?{q}&por_pagina=50",
        "cuentas_pagina_100": f"/app/v1/cuentas?{q}&por_pagina=50&pagina=100",
        "cuentas_pagina_10000": f"/app/v1/cuentas?{q}&por_pagina=50&pagina=10000",
        "cuentas_por_saldo": f"/app/v1/cuentas?{q}&por_pagina=50&orden=SALDO_TOTAL&sentido=DESC",
        "cuentas_por_p_recuperacion": f"/app/v1/cuentas?{q}&por_pagina=50"
        "&orden=P_RECUPERACION_POSITIVA_30D&sentido=DESC",
        "cuentas_prioridad_y_canal": f"/app/v1/cuentas?{q}&por_pagina=50&prioridad=ALTA"
        "&canal=CAMPO",
        "cuentas_asignadas": f"/app/v1/cuentas?{q}&por_pagina=50&estado_optimizacion=ASIGNADA",
        "cuentas_por_prefijo": f"/app/v1/cuentas?{q}&por_pagina=50&cuenta={x['cuenta'][:6]}",
        # La Cuenta 360
        "cuenta_360": f"{cuenta}?{q}",
        "cuenta_decision": f"{cuenta}/decision?decision_run_id={c['decision_run_id']}",
        "cuenta_historia": f"{cuenta}/historia?por_pagina=25",
        "cuenta_linea_de_tiempo": f"{cuenta}/linea-de-tiempo?por_pagina=25",
        "cuenta_pagos": f"{cuenta}/pagos?por_pagina=25",
        "cuenta_gestiones": f"{cuenta}/gestiones?por_pagina=25",
        "cuenta_explicacion_predictiva": f"{s}/cuentas/{x['cuenta']}/explicacion",
        "cuenta_explicacion_optimizacion": f"{o}/cuentas/{x['cuenta']}",
        # Analytics
        "analytics_ejecucion": a,
        "analytics_serie": f"{a}/agregados?metrica=recuperacion.neta&vista=PERIODO"
        "&dimension_set=TOTAL&por_pagina=500",
        "analytics_por_producto": f"{a}/agregados?metrica=recuperacion.neta&vista=ACUMULADA"
        "&dimension_set=PRODUCTO&por_pagina=100",
        "analytics_por_municipio": f"{a}/agregados?metrica=cartera.cuentas&vista=ACUMULADA"
        "&dimension_set=MUNICIPIO&por_pagina=100",
        "analytics_embudos": f"{a}/embudos",
        "analytics_controles": f"{a}/controles",
        # Predictivo
        "predictivo_modelo": f"/predictivo/modelos/{x['modelo_version_id']}",
        "predictivo_scoring": s,
        "predictivo_drift": f"{s}/drift",
        "predictivo_predicciones": f"{s}/predicciones?por_pagina=25",
        # Optimizacion
        "optimizacion_ejecucion": o,
        "optimizacion_capacidad": f"{o}/capacidad?por_pagina=50",
        "optimizacion_restricciones": f"{o}/restricciones?por_pagina=50",
        "optimizacion_asignaciones": f"{o}/resultados?por_pagina=50",
        "optimizacion_asignaciones_campo": f"{o}/resultados?por_pagina=50&canal=CAMPO"
        "&estado=ASIGNADA",
        "optimizacion_diagnostico": f"{o}/diagnostico",
        # Campo y ruteo
        "campo_plan": f"/campo-v2/planes/{c['campo_run_id']}",
        "campo_cargas": f"/campo-v2/planes/{c['campo_run_id']}/cargas?por_pagina=50",
        "ruteo_ejecucion": f"/ruteo-v2/ejecuciones/{c['ruteo_run_id']}",
        "ruteo_rutas": f"/ruteo-v2/ejecuciones/{c['ruteo_run_id']}/rutas?por_pagina=50",
        # La auditoria
        "linaje": f"/app/v1/linaje?{q}",
    }
    if x["ruta"]:
        rutas["ruteo_ruta"] = f"/ruteo-v2/rutas/{x['ruta']}"
        rutas["ruteo_paradas"] = f"/app/v1/ruteo/rutas/{x['ruta']}/paradas?por_pagina=50"
    return rutas


def medir(cliente, rutas: dict[str, str]) -> dict:
    resultados = {}
    for nombre, ruta in rutas.items():
        for _ in range(CALENTAMIENTO):
            _leer(cliente, ruta)
        tiempos = []
        for _ in range(REPETICIONES):
            inicio = time.perf_counter()
            respuesta = cliente.get(ruta)
            tiempos.append((time.perf_counter() - inicio) * 1000)
            if respuesta.status_code != 200:
                sys.exit(f"{ruta}: {respuesta.status_code} {respuesta.text[:300]}")
        if PERSONALES.search(respuesta.text):
            sys.exit(f"{nombre}: la respuesta trae un dato personal")
        tiempos.sort()
        resultados[nombre] = {
            "mediana_ms": round(statistics.median(tiempos), 2),
            "p95_ms": round(tiempos[int(len(tiempos) * 0.95) - 1], 2),
            "maximo_ms": round(tiempos[-1], 2),
            "bytes": len(respuesta.content),
        }
        print(f"  {nombre}: {resultados[nombre]['mediana_ms']} ms", flush=True)
    return resultados


def planes(cliente, rutas: dict[str, str], destino: Path) -> dict:
    """Las sentencias que emite cada ruta, con su EXPLAIN (ANALYZE, BUFFERS)."""
    from sqlalchemy import event

    from motor_cartera.db.sesion import crear_motor

    motor = crear_motor()
    por_ruta: dict[str, list[dict]] = {}
    for nombre, ruta in rutas.items():
        emitidas: list[tuple[str, object]] = []

        def anotar(conexion, cursor, sentencia, parametros, contexto, varias, emitidas=emitidas):
            emitidas.append((sentencia, parametros))

        event.listen(motor, "before_cursor_execute", anotar)
        try:
            _leer(cliente, ruta)
        finally:
            event.remove(motor, "before_cursor_execute", anotar)
        por_ruta[nombre] = []
        with motor.connect() as conexion:
            crudo = conexion.connection.driver_connection
            with crudo.cursor() as cursor:
                for sentencia, parametros in emitidas:
                    if not sentencia.lstrip().upper().startswith(("SELECT", "WITH")):
                        continue
                    # Una sentencia sobre una tabla temporal de su peticion (la explicacion de una
                    # prediccion arma las features de la cuenta en una) no se puede repetir en otra
                    # conexion: se anota sin plan, y su tiempo queda en el de la ruta.
                    cursor.execute("SAVEPOINT plan")
                    try:
                        cursor.execute("EXPLAIN (ANALYZE, BUFFERS) " + sentencia, parametros)
                        texto = "\n".join(fila[0] for fila in cursor.fetchall())
                    except Exception as error:  # noqa: BLE001 - se anota y se sigue
                        cursor.execute("ROLLBACK TO SAVEPOINT plan")
                        texto = f"(sin plan: {type(error).__name__})"
                    por_ruta[nombre].append({"sql": " ".join(sentencia.split()), "plan": texto})
            conexion.rollback()
    destino.mkdir(parents=True, exist_ok=True)
    with (destino / "planes_app.txt").open("w", encoding="utf-8") as salida:
        for nombre, sentencias in por_ruta.items():
            for i, s in enumerate(sentencias, start=1):
                salida.write(
                    f"### {nombre} ({i} de {len(sentencias)})\n{s['sql']}\n\n{s['plan']}\n\n"
                )
    return {
        "sentencias": {nombre: len(p) for nombre, p in por_ruta.items()},
        "milisegundos_en_postgresql": {
            nombre: round(sum(_milisegundos(p["plan"]) for p in por_ruta[nombre]), 3)
            for nombre in por_ruta
        },
        "seq_scan_sobre_tablas_grandes": {
            nombre: [p["sql"][:200] for p in por_ruta[nombre] if _escanea_grande(p["plan"])]
            for nombre in por_ruta
            if any(_escanea_grande(p["plan"]) for p in por_ruta[nombre])
        },
    }


def _milisegundos(plan: str) -> float:
    for linea in plan.splitlines():
        if linea.startswith("Execution Time"):
            return float(linea.split(":")[1].split()[0])
    return 0.0


def _escanea_grande(plan: str) -> bool:
    return any(f"Seq Scan on {tabla} " in plan + " " for tabla in TABLAS_GRANDES)


def _markdown(reporte: dict) -> str:
    x = reporte["contexto"]
    lineas = [
        "# Benchmark de la superficie web (v0.17)",
        "",
        f"Contexto XL: corte {x['fecha_corte']} ({x['cuentas']:,} cuentas), "
        f"{REPETICIONES} repeticiones despues de {CALENTAMIENTO} de calentamiento. "
        f"{reporte['maquina']}.",
        "",
        "| Ruta | Mediana ms | p95 ms | Maximo ms | Bytes | Sentencias | ms en PostgreSQL |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for nombre, t in reporte["tiempos"].items():
        lineas.append(
            f"| {nombre} | {t['mediana_ms']} | {t['p95_ms']} | {t['maximo_ms']} | "
            f"{t['bytes']:,} | {reporte['planes']['sentencias'][nombre]} | "
            f"{reporte['planes']['milisegundos_en_postgresql'][nombre]} |"
        )
    escaneos = reporte["planes"]["seq_scan_sobre_tablas_grandes"]
    lineas += ["", "Seq Scan sobre una tabla grande:", ""]
    lineas += [f"- {n}: {len(s)} sentencias" for n, s in escaneos.items()] or ["- ninguno"]
    return "\n".join(lineas) + "\n"


def main(argumentos: argparse.Namespace) -> None:
    _exigir_bench()
    _migrar()
    cliente = _cliente()
    print("Declarando el contexto XL...", flush=True)
    xl = declarar_contexto(cliente)
    print(f"Corte {xl['fecha_corte']}: {xl['cuentas']:,} cuentas", flush=True)
    rutas = rutas_de_la_app(xl)
    inicio = time.perf_counter()
    tiempos = medir(cliente, rutas)
    reporte = {
        "maquina": f"{platform.system()} {platform.release()}, {platform.machine()}, "
        f"Python {platform.python_version()}",
        "contexto": {k: v for k, v in xl.items() if k != "consulta"},
        "repeticiones": REPETICIONES,
        "tiempos": tiempos,
        "planes": planes(cliente, rutas, argumentos.destino),
        "segundos": round(time.perf_counter() - inicio, 1),
        "memoria_pico_mb": memoria_pico_mb(),
    }
    destino = argumentos.destino
    destino.mkdir(parents=True, exist_ok=True)
    (destino / "benchmark_app.json").write_text(
        json.dumps(reporte, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (destino / "benchmark_app.md").write_text(_markdown(reporte), encoding="utf-8")
    print(f"Listo: {destino / 'benchmark_app.md'}")


if __name__ == "__main__":
    argumentos = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    argumentos.add_argument("--destino", type=Path, required=True)
    main(argumentos.parse_args())
