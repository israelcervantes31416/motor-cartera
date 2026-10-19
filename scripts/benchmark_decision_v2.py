"""Benchmark de decision/v2: cuanto tarda decidir un corte grande point-in-time, por etapa, cuanto
crece la base, si decidir dos veces con los mismos datos da exactamente lo mismo, como se compara
con decision/v1 en modo sombra y como responden las consultas, contra PostgreSQL. No corre en el CI:
se pide a mano, sobre una base *_bench que ya tiene el escenario XL con su historia, sus pagos
interpretados, su lifecycle y sus atribuciones (los de benchmark_motor_pagos.py y
benchmark_lifecycle.py):

    python scripts/benchmark_decision_v2.py --destino <dir>
    python scripts/benchmark_decision_v2.py --destino <dir> --reanudar

Lo que hace, cada etapa pesada en su propio proceso para que su memoria pico sea solo suya:

1. sube la base a la cabeza de las migraciones (la 0011) e inventaria lo que hay;
2. decide el corte mas reciente con decision/v1, sobre las cuentas de su corrida, si no lo esta;
3. decide el mismo corte con decision/v2 (OPERATIVO, con known_at del reloj de la base) y mide cada
   fase: universo, historia, lifecycle, pagos, promesas, atribucion, features, politica, conteos y
   commit, con su memoria pico y el WAL que escribe;
4. corre VACUUM ANALYZE y mide cuanto ocupan sus tablas y sus indices, por fila;
5. vuelve a decidir el corte con un known_at un microsegundo despues (los datos elegibles son los
   mismos) y compara, cuenta por cuenta, las features, sus hashes y las decisiones;
6. compara decision/v2 con decision/v1 en modo sombra;
7. mide por la API las consultas de una cuenta, de una pagina y de la Cuenta 360, y guarda el plan
   de cada sentencia con EXPLAIN (ANALYZE, BUFFERS): una consulta de una cuenta no recorre las
   decisiones de toda la ejecucion.

MC_DATABASE_URL tiene que apuntar a una base *_bench. Cada etapa se anota en una bitacora
(benchmark_decision_v2.etapas.json, junto al reporte) en cuanto termina; con --reanudar no se repite
ninguna. Escribe un JSON con todo, los planes en texto y una tabla en Markdown para la
documentacion.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import time
from datetime import timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from benchmark_escala import memoria_pico_mb  # noqa: E402
from benchmark_lifecycle import _exigir_bench, _migrar, _tamanos, _wal, _wal_desde  # noqa: E402
from benchmark_motor_pagos import Bitacora, _sql, _vacuum  # noqa: E402

REPETICIONES = 25
TABLAS_DE_DECISION_V2 = (
    "ejecucion_decision_v2",
    "feature_decision_cuenta",
    "resultado_decision_v2",
)
TABLAS_GRANDES = (
    "resultado_decision_v2",
    "feature_decision_cuenta",
    "snapshot_cuenta",
    "movimiento_economico_canonico",
    "evento_lifecycle",
    "gestion_cobranza",
    "promesa_pago",
    "atribucion_movimiento",
    "decision_cuenta",
    "cuenta",
)


def _hijo(*argumentos: str) -> dict:
    """Una etapa en su propio proceso (este mismo script), con su ultima linea como JSON."""
    import subprocess

    proceso = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), *argumentos],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    if proceso.returncode != 0:
        print(proceso.stdout[-4000:], proceso.stderr[-4000:], sep="\n", file=sys.stderr)
        sys.exit(f"La etapa {argumentos[0]} fallo con codigo {proceso.returncode}.")
    return json.loads(proceso.stdout.strip().splitlines()[-1])


# --- las etapas, cada una en su proceso -----------------------------------------------------------


def decidir_v1(corrida_id: int) -> dict:
    from motor_cartera.decision.ejecuciones import decidir_corrida

    antes = _wal()
    inicio = time.perf_counter()
    ejecucion = decidir_corrida(corrida_id)
    segundos = time.perf_counter() - inicio
    return {
        "decision_run_id": str(ejecucion.decision_run_id),
        "estado": ejecucion.estado,
        "cuentas_decididas": ejecucion.cuentas_decididas,
        "segundos": round(segundos, 3),
        "cuentas_por_segundo": round(ejecucion.cuentas_decididas / segundos) if segundos else None,
        "memoria_pico_mb": memoria_pico_mb(),
        "wal_mib": _wal_desde(antes),
    }


def decidir_v2(corte_id: str, known_at: str | None, continuar: int | None) -> dict:
    """Abre la decision del corte (OPERATIVO; con known_at si se da) y la decide como su trabajo:
    espera la frontera y decide, con el cronometro por fase. Con `continuar`, decide esa ejecucion
    EN_PROCESO, que una corrida interrumpida dejo abierta. Si esas mismas entradas ya se publicaron
    (la corrida se detuvo entre publicar y anotarlo), la reporta como esta, sin tiempos."""
    from datetime import datetime
    from uuid import UUID

    from motor_cartera.config import config
    from motor_cartera.db.modelos import EjecucionDecisionV2, ModoDecisionV2
    from motor_cartera.db.sesion import sesion
    from motor_cartera.decision_v2 import ejecuciones as motor
    from motor_cartera.ingesta.fuente_oficial import Cronometro

    recuperada = False
    if continuar is not None:
        ejecucion_id = continuar
        with sesion() as s:
            previa = s.get_one(EjecucionDecisionV2, ejecucion_id)
            known, recuperada = previa.known_at, previa.estado != "EN_PROCESO"
    else:
        try:
            with sesion() as s:
                ejecucion = motor.abrir(
                    s,
                    UUID(corte_id),
                    as_of=None,
                    known_at=None if known_at is None else datetime.fromisoformat(known_at),
                    modo=ModoDecisionV2.OPERATIVO,
                    version_politica=None,
                    zona=config.zona_horaria_fuente,
                    max_intentos=config.worker_max_intentos,
                )
                s.commit()
                ejecucion_id, known = ejecucion.id, ejecucion.known_at
        except motor.DecisionV2YaGenerada as exc:
            ejecucion_id, recuperada = exc.previa.id, True
        except motor.DecisionV2EnProceso as exc:
            ejecucion_id, known = exc.activa.id, exc.activa.known_at
    antes = _wal()
    cronometro = Cronometro()
    inicio = time.perf_counter()
    if not recuperada:
        with cronometro.fase("frontera"):
            motor.esperar_frontera(known)
        motor.decidir(ejecucion_id, cronometro=cronometro)
    segundos = time.perf_counter() - inicio
    with sesion() as s:
        e = s.get_one(EjecucionDecisionV2, ejecucion_id)
        resultado = {
            "ejecucion_id": e.id,
            "decision_run_id": str(e.decision_run_id),
            "estado": e.estado,
            "resultado": e.resultado,
            "as_of": e.as_of.isoformat(),
            "known_at": e.known_at.isoformat(),
            "firma_inputs": e.firma_inputs,
            "hash_politica": e.hash_politica,
            "cuentas_objetivo": e.cuentas_objetivo,
            "cuentas_decididas": e.cuentas_decididas,
            "disponibilidad": e.disponibilidad,
            "distribucion": e.distribucion,
            "detalle": e.detalle,
        }
    if resultado["estado"] != "EXITOSA":
        print(json.dumps(resultado, default=str), file=sys.stderr)
        sys.exit(f"La decision termino {resultado['estado']}: {resultado['detalle']}")
    if recuperada:
        return {
            **resultado,
            "segundos": None,
            "cuentas_por_segundo": None,
            "fases": {},
            "memoria_pico_mb": None,
            "wal_mib": None,
            "recuperado": True,
        }
    return {
        **resultado,
        "segundos": round(segundos, 3),
        "cuentas_por_segundo": round(resultado["cuentas_decididas"] / segundos),
        "fases": {nombre: round(s_, 3) for nombre, s_ in cronometro.fases.items()},
        "memoria_pico_mb": memoria_pico_mb(),
        "wal_mib": _wal_desde(antes),
    }


def comparar_reconstruccion(primera: int, segunda: int) -> dict:
    """Cuenta por cuenta, si dos decisiones del mismo corte con los mismos datos elegibles dieron
    las mismas features, los mismos hashes y las mismas decisiones."""
    columnas_features = ", ".join(_columnas_de_features())
    (fila,) = _sql(
        "WITH a AS (SELECT * FROM feature_decision_cuenta WHERE ejecucion_decision_v2_id = :p), "
        "b AS (SELECT * FROM feature_decision_cuenta WHERE ejecucion_decision_v2_id = :s), "
        "ra AS (SELECT * FROM resultado_decision_v2 WHERE ejecucion_decision_v2_id = :p), "
        "rb AS (SELECT * FROM resultado_decision_v2 WHERE ejecucion_decision_v2_id = :s) "
        "SELECT (SELECT count(*) FROM a), (SELECT count(*) FROM b), "
        f"(SELECT count(*) FROM a FULL JOIN b USING (cuenta_canonica_id) WHERE "
        f"(a.{columnas_features.replace(', ', ', a.')}) IS DISTINCT FROM "
        f"(b.{columnas_features.replace(', ', ', b.')})), "
        "(SELECT count(*) FROM a FULL JOIN b USING (cuenta_canonica_id) "
        "WHERE a.feature_hash IS DISTINCT FROM b.feature_hash), "
        "(SELECT count(*) FROM ra FULL JOIN rb USING (cuenta_canonica_id) WHERE "
        "(ra.prioridad_base, ra.ajuste_total, ra.prioridad_final, ra.canal_recomendado, "
        "ra.motivos, ra.decision_hash) IS DISTINCT FROM (rb.prioridad_base, rb.ajuste_total, "
        "rb.prioridad_final, rb.canal_recomendado, rb.motivos, rb.decision_hash)), "
        "(SELECT encode(sha256(string_agg(decision_hash, ''::bytea ORDER BY cuenta_canonica_id)), "
        "'hex') FROM ra), "
        "(SELECT encode(sha256(string_agg(decision_hash, ''::bytea ORDER BY cuenta_canonica_id)), "
        "'hex') FROM rb)",
        {"p": primera, "s": segunda},
    )
    return {
        "cuentas_primera": fila[0],
        "cuentas_segunda": fila[1],
        "cuentas_con_features_distintas": fila[2],
        "cuentas_con_feature_hash_distinto": fila[3],
        "cuentas_con_decision_distinta": fila[4],
        "huella_de_la_primera": fila[5],
        "huella_de_la_segunda": fila[6],
    }


def _columnas_de_features() -> list[str]:
    from motor_cartera.decision_v2.features import FEATURES

    return [f.nombre for f in FEATURES]


def sombra(ejecucion_id: int) -> dict:
    from dataclasses import asdict

    from motor_cartera.db.modelos import EjecucionDecisionV2
    from motor_cartera.db.sesion import sesion
    from motor_cartera.decision_v2.sombra import comparar

    inicio = time.perf_counter()
    with sesion() as s:
        comparacion = asdict(comparar(s, s.get_one(EjecucionDecisionV2, ejecucion_id)))
    return {**comparacion, "segundos": round(time.perf_counter() - inicio, 3)}


def consultar(destino: Path, decision_run_id: str, corte_id: str) -> dict:
    """Las consultas por la API, con un cliente HTTP en el mismo proceso: el tiempo incluye la
    serializacion. Cada una se repite y se reporta su mediana, su p95 y su maximo; despues se
    guarda el plan de cada sentencia que emitio."""
    from fastapi.testclient import TestClient
    from sqlalchemy import event

    from motor_cartera.api.app import crear_app
    from motor_cartera.config import Config
    from motor_cartera.db.sesion import crear_motor

    clave = "benchmark-decision-v2-" + "x" * 24
    cliente = TestClient(
        crear_app(Config(app_env="development", api_key=clave)), headers={"X-API-Key": clave}
    )
    # Una cuenta con la saturacion activa (la explicacion mas larga) y la de mas eventos.
    ((con_saturacion,),) = _sql(
        "SELECT c.cuenta_id FROM resultado_decision_v2 r "
        "JOIN ejecucion_decision_v2 e ON e.id = r.ejecucion_decision_v2_id "
        "JOIN cuenta_canonica c ON c.id = r.cuenta_canonica_id "
        "WHERE e.decision_run_id = :d AND r.ajuste_total <> 0 "
        "ORDER BY jsonb_array_length(r.motivos) DESC, r.cuenta_canonica_id LIMIT 1",
        {"d": decision_run_id},
    )
    elegidos = {"cuenta": str(con_saturacion), "decision_run_id": decision_run_id}
    base = f"/decisiones-v2/{decision_run_id}"
    de_una_cuenta = {
        "decision_de_una_cuenta": f"{base}/resultados/{con_saturacion}",
        "historia_de_decisiones_de_una_cuenta": f"/cuentas/{con_saturacion}/decisiones-v2",
        "cuenta_360_con_su_ultima_decision": f"/cuentas/{con_saturacion}",
    }
    paginas = {
        "pagina_por_prioridad": f"{base}/resultados?prioridad_final=ALTA&por_pagina=50",
        "pagina_por_canal": f"{base}/resultados?canal_recomendado=CAMPO&por_pagina=50",
        "primera_pagina": f"{base}/resultados?por_pagina=50",
        "ejecucion": base,
        "decisiones_del_corte": f"/cartera/cortes/{corte_id}/decisiones-v2",
    }

    def medir(ruta: str) -> dict:
        tiempos = []
        for _ in range(REPETICIONES):
            inicio = time.perf_counter()
            respuesta = cliente.get(ruta)
            tiempos.append((time.perf_counter() - inicio) * 1000)
            if respuesta.status_code != 200:
                sys.exit(f"{ruta}: {respuesta.status_code} {respuesta.text[:300]}")
        tiempos.sort()
        return {
            "mediana_ms": round(statistics.median(tiempos), 2),
            "p95_ms": round(tiempos[int(len(tiempos) * 0.95) - 1], 2),
            "maximo_ms": round(tiempos[-1], 2),
        }

    todas = {**de_una_cuenta, **paginas}
    tiempos = {nombre: medir(ruta) for nombre, ruta in todas.items()}
    motor = crear_motor()
    planes: dict[str, list] = {}
    for nombre, ruta in todas.items():
        emitidas: list[tuple[str, object]] = []

        def anotar(conexion, cursor, sentencia, parametros, contexto, varias, emitidas=emitidas):
            emitidas.append((sentencia, parametros))

        event.listen(motor, "before_cursor_execute", anotar)
        try:
            cliente.get(ruta)
        finally:
            event.remove(motor, "before_cursor_execute", anotar)
        planes[nombre] = []
        with motor.connect() as conexion:
            crudo = conexion.connection.driver_connection
            with crudo.cursor() as cursor:
                for sentencia, parametros in emitidas:
                    if not sentencia.lstrip().upper().startswith(("SELECT", "WITH")):
                        continue
                    cursor.execute("EXPLAIN (ANALYZE, BUFFERS) " + sentencia, parametros)
                    plan = "\n".join(fila[0] for fila in cursor.fetchall())
                    planes[nombre].append({"sql": " ".join(sentencia.split()), "plan": plan})
            conexion.rollback()
    destino.mkdir(parents=True, exist_ok=True)
    with (destino / "planes_decision_v2.txt").open("w", encoding="utf-8") as salida:
        for nombre, sentencias in planes.items():
            for i, sentencia in enumerate(sentencias, start=1):
                salida.write(f"### {nombre} ({i} de {len(sentencias)})\n{sentencia['sql']}\n\n")
                salida.write(sentencia["plan"] + "\n\n")
    return {
        "elegidos": elegidos,
        "repeticiones": REPETICIONES,
        "tiempos": tiempos,
        "sentencias": {nombre: len(p) for nombre, p in planes.items()},
        "seq_scan_sobre_tablas_grandes": {
            nombre: [p["sql"][:160] for p in planes[nombre] if _escanea_grande(p["plan"])]
            for nombre in todas
            if any(_escanea_grande(p["plan"]) for p in planes[nombre])
        },
    }


def _escanea_grande(plan: str) -> bool:
    return any(f"Seq Scan on {tabla} " in plan + " " for tabla in TABLAS_GRANDES)


# --- el orquestador -------------------------------------------------------------------------------


def _inventario(corte_id: int) -> dict:
    (fila,) = _sql(
        "SELECT (SELECT count(*) FROM corte_canonico), (SELECT cuentas FROM corte_canonico "
        "WHERE id = :corte), (SELECT count(*) FROM snapshot_cuenta), "
        "(SELECT count(*) FROM cuenta_canonica), (SELECT count(*) FROM pago_observado), "
        "(SELECT count(*) FROM movimiento_economico_canonico), "
        "(SELECT count(*) FROM ejecucion_motor_pagos WHERE estado = 'EXITOSA'), "
        "(SELECT count(*) FROM evento_lifecycle), (SELECT count(*) FROM gestion_cobranza), "
        "(SELECT count(*) FROM promesa_pago), (SELECT count(*) FROM convenio_cobranza), "
        "(SELECT count(*) FROM ejecucion_atribucion WHERE estado = 'EXITOSA'), "
        "(SELECT count(*) FROM atribucion_movimiento)",
        {"corte": corte_id},
    )
    nombres = (
        "cortes",
        "cuentas_del_corte",
        "snapshots",
        "cuentas_canonicas",
        "pagos_observados",
        "movimientos_economicos",
        "interpretaciones_de_pagos",
        "eventos_lifecycle",
        "gestiones",
        "promesas",
        "convenios",
        "atribuciones",
        "atribuciones_por_movimiento",
    )
    return dict(zip(nombres, fila, strict=True))


def _mib(valor: int | None) -> str:
    return "-" if valor is None else f"{valor / 2**20:,.1f} MiB"


def _num(valor, sufijo: str = "") -> str:
    return "-" if valor is None else f"{valor:,}{sufijo}"


def _markdown(reporte: dict) -> str:
    v2, v1 = reporte["decision_v2"], reporte["decision_v1"]
    lineas = [
        "# Benchmark de decision/v2",
        "",
        f"- Maquina: {reporte['maquina']}",
        f"- PostgreSQL: {reporte['postgresql']}",
        f"- Corte: {reporte['corte']['fecha_corte']} "
        f"({_num(reporte['inventario']['cuentas_del_corte'])} cuentas)",
        "",
        "## Lo que habia en la base",
        "",
        "| Conteo | Filas |",
        "|---|---:|",
        *(f"| {k} | {_num(v)} |" for k, v in reporte["inventario"].items()),
        "",
        "## decision/v2 sobre el corte",
        "",
        f"{_num(v2['cuentas_decididas'])} cuentas en {v2['segundos']} s "
        f"({_num(v2['cuentas_por_segundo'])} por segundo), memoria pico del proceso "
        f"{v2['memoria_pico_mb']} MiB, WAL {v2['wal_mib']} MiB. decision/v1 sobre la misma "
        f"corrida: {_num(v1.get('cuentas_decididas'))} cuentas en {v1.get('segundos', '-')} s.",
        "",
        "| Fase | Segundos | Cuentas por segundo |",
        "|---|---:|---:|",
        *(
            f"| {fase} | {segundos} | "
            f"{_num(round(v2['cuentas_decididas'] / segundos) if segundos else None)} |"
            for fase, segundos in v2["fases"].items()
        ),
        "",
        "Disponibilidad a known_at: pagos "
        + json.dumps(v2["disponibilidad"]["pagos"])
        + ", atribucion "
        + json.dumps(v2["disponibilidad"]["atribucion"])
        + f"; horizonte de los pagos {v2['disponibilidad']['horizonte_pagos']}.",
        "",
        "Distribucion: " + json.dumps(v2["distribucion"], ensure_ascii=False),
        "",
        "## Lo que ocupa",
        "",
        "| Tabla | Filas | Datos | Indices | Total | Bytes por fila |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for t in reporte["tamanos_despues"]["tablas"]:
        filas = t["filas_estimadas"] or 0
        por_fila = round(t["total_bytes"] / filas) if filas > 0 else None
        lineas.append(
            f"| {t['tabla']} | {_num(filas)} | {_mib(t['datos_bytes'])} | "
            f"{_mib(t['indices_bytes'])} | {_mib(t['total_bytes'])} | {_num(por_fila)} |"
        )
    lineas += [
        "",
        "| Indice | Tabla | Tamano |",
        "|---|---|---:|",
        *(
            f"| {i['indice']} | {i['tabla']} | {_mib(i['bytes'])} |"
            for i in reporte["tamanos_despues"]["indices"]
        ),
        "",
        f"La base crecio de {_mib(reporte['tamanos_antes']['base_bytes'])} a "
        f"{_mib(reporte['tamanos_despues']['base_bytes'])}.",
        "",
        "## Decidir otra vez con los mismos datos",
        "",
    ]
    r = reporte["reconstruccion"]
    lineas += [
        f"La segunda decision ({reporte['segunda']['segundos']} s) tiene "
        f"{_num(r['cuentas_con_features_distintas'])} cuentas con features distintas, "
        f"{_num(r['cuentas_con_feature_hash_distinto'])} con feature_hash distinto y "
        f"{_num(r['cuentas_con_decision_distinta'])} con decision distinta, de "
        f"{_num(r['cuentas_primera'])}. Huella de las decisiones: `{r['huella_de_la_primera']}` y "
        f"`{r['huella_de_la_segunda']}`.",
        "",
        "## decision/v1 contra decision/v2 en modo sombra",
        "",
    ]
    c = reporte["sombra"]
    lineas += [
        f"- Comparadas: {_num(c['comparadas'])} (solo en v2 {c['solo_v2']}, solo en v1 "
        f"{c['solo_v1']})",
        f"- Prioridad igual {_num(c['prioridad_igual'])}, v2 mayor {_num(c['v2_mayor'])}, v2 "
        f"menor {_num(c['v2_menor'])}",
        f"- Canal igual {_num(c['canal_igual'])}, distinto {_num(c['canal_distinto'])}",
        f"- v1 subio por saldo (SALDO_ALTO, fuera de public-v1): {_num(c['v1_con_saldo_alto'])}; "
        f"base de v2 igual a la final de v1: {_num(c['base_v2_igual_a_v1'])}",
        "- Modificadores: " + ", ".join(f"{k} {_num(v)}" for k, v in c["modificadores"].items()),
        "",
        "| v1 \\ v2 | " + " | ".join(c["matriz"]) + " |",
        "|---|" + "---:|" * len(c["matriz"]),
        *(
            f"| {fila} | " + " | ".join(_num(n) for n in columnas.values()) + " |"
            for fila, columnas in c["matriz"].items()
        ),
        "",
        "Es un diagnostico de diferencias, no un juicio de calidad.",
        "",
        "## Consultas por la API",
        "",
        "| Consulta | Mediana | p95 | Maximo | Sentencias |",
        "|---|---:|---:|---:|---:|",
    ]
    consultas = reporte["consultas"]
    for nombre, t in consultas["tiempos"].items():
        lineas.append(
            f"| {nombre} | {t['mediana_ms']} ms | {t['p95_ms']} ms | {t['maximo_ms']} ms | "
            f"{consultas['sentencias'][nombre]} |"
        )
    lineas += [
        "",
        "Seq Scan sobre una tabla grande: "
        + (
            "ninguno."
            if not consultas["seq_scan_sobre_tablas_grandes"]
            else json.dumps(consultas["seq_scan_sobre_tablas_grandes"], ensure_ascii=False)
        ),
        "",
    ]
    return "\n".join(lineas)


def main(argumentos: argparse.Namespace) -> None:
    _exigir_bench()
    destino = argumentos.destino.resolve()
    bitacora = Bitacora(destino / "benchmark_decision_v2.etapas.json", argumentos.reanudar)
    _migrar()
    filas = _sql(
        "SELECT c.id, c.corte_id, c.fecha_corte, d.corrida_id FROM corte_canonico c "
        "JOIN dataset_conformado d ON d.id = c.dataset_conformado_id "
        "ORDER BY c.fecha_corte DESC LIMIT 1"
    )
    if not filas:
        sys.exit("La base no tiene cortes: carga el XL con benchmark_motor_pagos.py.")
    ((corte, corte_id, fecha_corte, corrida_id),) = filas
    print(f"1. el inventario: corte del {fecha_corte}", flush=True)
    if "inventario" not in bitacora:
        bitacora.anotar("inventario", _inventario(corte))
        _vacuum(TABLAS_DE_DECISION_V2)
        bitacora.anotar("tamanos_antes", _tamanos(TABLAS_DE_DECISION_V2))
    print(f"   {json.dumps(bitacora['inventario'])}", flush=True)

    print("2. decision/v1 sobre la corrida del corte", flush=True)
    if "decision_v1" not in bitacora:
        previa = _sql(
            "SELECT decision_run_id FROM ejecucion_decision WHERE corrida_id = :c "
            "AND estado = 'EXITOSA'",
            {"c": corrida_id},
        )
        if previa:
            bitacora.anotar(
                "decision_v1", {"decision_run_id": str(previa[0][0]), "recuperado": True}
            )
        else:
            bitacora.anotar("decision_v1", _hijo("v1", "--corrida", str(corrida_id)))
    print(f"   {bitacora['decision_v1']}", flush=True)

    print("3. decision/v2 sobre el corte", flush=True)
    if "decision_v2" not in bitacora:
        etapa = ["v2", "--corte", str(corte_id)]
        # Al reanudar, una decision que ya se publico y que la bitacora no tiene (la corrida se
        # detuvo entre las dos cosas) no se repite; una EN_PROCESO la continua el hijo.
        previa = argumentos.reanudar and _sql(
            "SELECT id FROM ejecucion_decision_v2 WHERE corte_canonico_id = :c "
            "AND estado = 'EXITOSA' ORDER BY id LIMIT 1",
            {"c": corte},
        )
        if previa:
            etapa += ["--continuar", str(previa[0][0])]
        bitacora.anotar("decision_v2", _hijo(*etapa))
    v2 = bitacora["decision_v2"]
    print(
        f"   {v2['cuentas_decididas']:,} cuentas en {v2['segundos']} s "
        f"({_num(v2['cuentas_por_segundo'])}/s): {v2['fases']}",
        flush=True,
    )

    print("4. lo que ocupa", flush=True)
    if "tamanos_despues" not in bitacora:
        bitacora.anotar("vacuum_s", _vacuum(TABLAS_DE_DECISION_V2))
        bitacora.anotar("tamanos_despues", _tamanos(TABLAS_DE_DECISION_V2))

    print("5. decidir otra vez con los mismos datos", flush=True)
    if "segunda" not in bitacora:
        from datetime import datetime

        siguiente = datetime.fromisoformat(v2["known_at"]) + timedelta(microseconds=1)
        bitacora.anotar(
            "segunda", _hijo("v2", "--corte", str(corte_id), "--known-at", siguiente.isoformat())
        )
    if "reconstruccion" not in bitacora:
        bitacora.anotar(
            "reconstruccion",
            comparar_reconstruccion(v2["ejecucion_id"], bitacora["segunda"]["ejecucion_id"]),
        )
    print(f"   {bitacora['reconstruccion']}", flush=True)

    print("6. la sombra contra decision/v1", flush=True)
    if "sombra" not in bitacora:
        bitacora.anotar("sombra", _hijo("sombra", "--ejecucion", str(v2["ejecucion_id"])))

    print("7. las consultas", flush=True)
    if "consultas" not in bitacora:
        bitacora.anotar(
            "consultas",
            _hijo(
                "consultar",
                "--destino",
                str(destino),
                "--decision",
                v2["decision_run_id"],
                "--corte",
                str(corte_id),
            ),
        )

    ((version,),) = _sql("SELECT version()")
    reporte = {
        "maquina": f"{platform.system()} {platform.release()}, {os.cpu_count()} CPU, "
        f"Python {platform.python_version()}",
        "postgresql": version.split(",")[0],
        "corte": {"corte_id": str(corte_id), "fecha_corte": str(fecha_corte)},
        "inventario": bitacora["inventario"],
        "decision_v1": bitacora["decision_v1"],
        "decision_v2": v2,
        "tamanos_antes": bitacora["tamanos_antes"],
        "tamanos_despues": bitacora["tamanos_despues"],
        "segunda": bitacora["segunda"],
        "reconstruccion": bitacora["reconstruccion"],
        "sombra": bitacora["sombra"],
        "consultas": bitacora["consultas"],
    }
    destino.mkdir(parents=True, exist_ok=True)
    (destino / "benchmark_decision_v2.json").write_text(
        json.dumps(reporte, indent=1, ensure_ascii=False, default=str), encoding="utf-8"
    )
    (destino / "benchmark_decision_v2.md").write_text(_markdown(reporte), encoding="utf-8")
    print(f"Reporte: {destino / 'benchmark_decision_v2.md'}")


if __name__ == "__main__":
    lector = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = lector.add_subparsers(dest="etapa")
    uno = sub.add_parser("v1")
    uno.add_argument("--corrida", type=int, required=True)
    dos = sub.add_parser("v2")
    dos.add_argument("--corte", required=True)
    dos.add_argument("--known-at")
    dos.add_argument("--continuar", type=int)
    s_ = sub.add_parser("sombra")
    s_.add_argument("--ejecucion", type=int, required=True)
    c = sub.add_parser("consultar")
    c.add_argument("--destino", type=Path, required=True)
    c.add_argument("--decision", required=True)
    c.add_argument("--corte", required=True)
    lector.add_argument("--destino", type=Path, help="Donde van el reporte y los planes.")
    lector.add_argument("--reanudar", action="store_true")
    leidos = lector.parse_args()
    if leidos.etapa == "v1":
        print(json.dumps(decidir_v1(leidos.corrida), default=str))
    elif leidos.etapa == "v2":
        print(json.dumps(decidir_v2(leidos.corte, leidos.known_at, leidos.continuar), default=str))
    elif leidos.etapa == "sombra":
        print(json.dumps(sombra(leidos.ejecucion), default=str))
    elif leidos.etapa == "consultar":
        print(json.dumps(consultar(leidos.destino, leidos.decision, leidos.corte), default=str))
    else:
        if leidos.destino is None:
            lector.error("--destino es obligatorio.")
        main(leidos)
