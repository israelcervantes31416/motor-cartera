"""Benchmark de predictive/model-v1: cuanto tarda entrenar el modelo sobre el escenario M, por fase
y con su memoria; su backtest temporal y sus metricas por target; cuanto tarda puntuar un corte XL
por fase, con su WAL, el crecimiento de la base y los bytes por prediccion; si el drift distingue un
scoring parecido a TRAIN de uno desplazado; y como responden las consultas de la API, con el plan de
cada sentencia. No corre en el CI: se pide a mano, sobre una base *_bench que ya tiene cargado el
escenario predictivo (`cargar-escenario-predictivo`, en su propia cartera), su manifiesto propuesto
(`construir-manifiesto-predictivo`) y el corte XL de los benchmarks anteriores:

    python scripts/benchmark_predictivo.py --manifiesto <json> --destino <dir>
    python scripts/benchmark_predictivo.py --manifiesto <json> --destino <dir> --reanudar

No ajusta nada despues de ver TEST: entrena una sola vez, con el manifiesto declarado, y reporta lo
que se publico. Las etapas:

1. sube la base a la cabeza de las migraciones (la 0016), anota su configuracion e inventaria los
   cortes;
2. el entrenamiento, en primer plano: el tiempo de cada fase (del dataset, las features, los labels,
   el COPY y la conversion; el preprocesamiento, el ajuste de cada componente, la calibracion, la
   evaluacion y el walk-forward), las filas cuenta-ancla (TRAIN, VALIDATION, TEST, PURGE y las
   censuradas), las prevalencias por split, la matriz de diseno, la memoria pico y el WAL; el
   backtest por target (cada fold, su agregado y el holdout de TEST, aparte) y las metricas de TEST
   de cada componente contra su baseline, sin calibrar y calibradas, con su tabla de calibracion;
3. el scoring XL: el corte XL (por omision, el del 2026-04-01 de CARTERA_PRINCIPAL, de 500,000
   cuentas, que viene de otro generador) con la version publicada, por fase, con las cuentas por
   segundo, la memoria pico, el WAL y el crecimiento de la base;
4. el drift: A es el ultimo corte de VALIDATION del manifiesto, puntuado en BACKTEST con su
   feature_known_at (el generador de TRAIN, unas semanas despues, fuera de TRAIN), y B es el XL, de
   otro generador y con otra historia: su distribucion es otra a proposito. El modelo no cambia; el
   reporte dice si el drift los distingue;
5. VACUUM ANALYZE y los tamanos de las tablas predictivas y de sus indices, y los bytes por
   prediccion;
6. la API sobre el scoring XL: la mediana, el p95 y el maximo de la version, el registro, el
   scoring, las paginas de predicciones (por cuenta, por recuperacion esperada, por p de
   recuperacion y por riesgo, y un filtro de riesgo alto), el drift, las predicciones de una cuenta
   y la explicacion de una prediccion; y el EXPLAIN (ANALYZE, BUFFERS) de cada sentencia que
   emitieron, con las que recorren una tabla grande.

Cada etapa corre en su propio proceso, para que su memoria pico sea solo suya, y se anota en una
bitacora (benchmark_predictivo.etapas.json) en cuanto termina: con --reanudar no se repite ninguna.
MC_DATABASE_URL tiene que apuntar a una base *_bench. Ninguna salida lleva un dato personal: solo
conteos, tiempos, metricas agregadas, identificadores opacos y planes.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import time
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from benchmark_escala import memoria_pico_mb  # noqa: E402
from benchmark_lifecycle import _exigir_bench, _migrar, _wal, _wal_desde  # noqa: E402
from benchmark_motor_pagos import Bitacora, _sql, _vacuum  # noqa: E402

REPETICIONES = 20
REPETICIONES_EXPLICACION = 10
TABLAS_PREDICTIVAS = (
    "ejecucion_entrenamiento_predictivo",
    "ancla_entrenamiento_predictivo",
    "interpretacion_entrenamiento_predictivo",
    "version_modelo_predictivo",
    "componente_modelo_predictivo",
    "ejecucion_scoring_predictivo",
    "prediccion_cuenta",
    "drift_predictivo",
)
TABLAS_GRANDES = (
    "prediccion_cuenta",
    "snapshot_cuenta",
    "cuenta_canonica",
    "movimiento_economico_canonico",
    "pago_observado",
    "gestion_cobranza",
    "evento_lifecycle",
    "promesa_pago",
)
PROHIBIDAS = ('"cliente_unico"', '"actor_ref"', '"latitud"', '"longitud"', '"nombre_cliente"')
CONFIGURACION = (
    "work_mem",
    "hash_mem_multiplier",
    "temp_buffers",
    "shared_buffers",
    "maintenance_work_mem",
    "effective_cache_size",
    "max_parallel_workers_per_gather",
    "jit",
)


def _base_bytes() -> int:
    return _sql("SELECT pg_database_size(current_database())")[0][0]


# --- el entrenamiento, en su proceso --------------------------------------------------------------


def entrenar(manifiesto: str) -> dict:
    """El manifiesto declarado, entrenado en primer plano con su propio trabajo, como lo haria
    `entrenar-predictivo`. Si ya se entreno (la corrida se detuvo entre publicar y anotarlo), lo
    reporta como esta, sin tiempos del proceso."""
    from motor_cartera.api.esquemas_predictivo import EntrenamientoPredictivoEntrada
    from motor_cartera.db.modelos import EjecucionEntrenamientoPredictivo
    from motor_cartera.db.sesion import sesion
    from motor_cartera.orquestacion.worker import entrenar_predictivo_en_primer_plano
    from motor_cartera.predictivo import consultas, entrenamiento
    from motor_cartera.predictivo import manifiesto as manifiesto_

    datos = json.loads(Path(manifiesto).read_text(encoding="utf-8"))
    entrada = EntrenamientoPredictivoEntrada.model_validate(datos.get("manifiesto", datos))
    declarado = manifiesto_.leer(entrada.model_dump(mode="json"))
    antes, base = _wal(), _base_bytes()
    inicio = time.perf_counter()
    recuperado = False
    try:
        ejecucion_id = entrenar_predictivo_en_primer_plano(declarado).id
    except entrenamiento.EntrenamientoYaPublicado as exc:
        ejecucion_id, recuperado = exc.previa.id, True
    segundos = time.perf_counter() - inicio
    with sesion() as s:
        e = s.get_one(EjecucionEntrenamientoPredictivo, ejecucion_id)
        vista = consultas.obtener_entrenamiento(s, e.training_run_id)
        if vista.modelo_version_id is None:
            sys.exit(f"El entrenamiento termino {e.estado} ({e.resultado}): {e.detalle}")
        modelo = consultas.obtener_modelo(s, vista.modelo_version_id)
        componentes = []
        for c in modelo.componentes:
            prueba = dict(c.metricas_test)
            tabla = prueba.pop("tabla_calibracion", None) or prueba.pop("deciles", None)
            componentes.append(
                {
                    "target": c.target,
                    "algoritmo": c.algoritmo,
                    "hiperparametros": c.hiperparametros,
                    "calibracion": c.calibracion,
                    "estado": c.estado,
                    "compuertas": c.compuertas,
                    "metricas_validation": {
                        k: v
                        for k, v in c.metricas_validation.get("metricas", {}).items()
                        if not isinstance(v, list | dict)
                    },
                    "metricas_test": {
                        k: v for k, v in prueba.items() if not isinstance(v, list | dict)
                    },
                    "sin_calibrar": prueba.get("sin_calibrar"),
                    "baseline_test": {
                        k: v for k, v in c.baseline_test.items() if not isinstance(v, list | dict)
                    },
                    "tabla": tabla,
                    "intervalos": c.bootstrap_test.get("intervalos"),
                    "columnas": len(c.artefacto["columnas"]),
                }
            )
        resultado = {
            "training_run_id": str(e.training_run_id),
            "estado": str(e.estado),
            "resultado": e.resultado,
            "modelo_version_id": str(vista.modelo_version_id),
            "estado_modelo": str(vista.estado_modelo),
            "hash_manifiesto": e.hash_manifiesto,
            "firma_inputs": e.firma_inputs,
            "dataset_hash": e.dataset_hash,
            "hash_modelo": modelo.version.hash_modelo,
            "anclas": e.anclas,
            "anclas_por_split": {
                split: sum(
                    1 for a in datos.get("manifiesto", datos)["anclas"] if a["split"] == split
                )
                for split in ("TRAIN", "VALIDATION", "TEST", "PURGE")
            },
            "periodos": {
                "train": [
                    str(modelo.version.periodo_train_desde),
                    str(modelo.version.periodo_train_hasta),
                ],
                "validation": [
                    str(modelo.version.periodo_validation_desde),
                    str(modelo.version.periodo_validation_hasta),
                ],
                "test": [
                    str(modelo.version.periodo_test_desde),
                    str(modelo.version.periodo_test_hasta),
                ],
            },
            "filas": {
                "total": e.ejemplos_total,
                "train": e.ejemplos_train,
                "validation": e.ejemplos_validation,
                "test": e.ejemplos_test,
                "purge": e.ejemplos_purge,
                "censuradas": e.ejemplos_censurados,
            },
            "conteos": e.conteos,
            "tiempos": e.tiempos,
            "backtest": (e.backtest or {}).get("por_target"),
            "componentes": componentes,
            "detalle": e.detalle,
        }
    medicion = (
        {"segundos": None, "memoria_pico_mb": None, "wal_mib": None, "crecimiento_bytes": None}
        if recuperado
        else {
            "segundos": round(segundos, 3),
            "memoria_pico_mb": memoria_pico_mb(),
            "wal_mib": _wal_desde(antes),
            "crecimiento_bytes": _base_bytes() - base,
        }
    )
    return {**resultado, **medicion, "recuperado": recuperado}


# --- un scoring, en su proceso --------------------------------------------------------------------


def puntuar(modelo: str, corte_id: str, known_at: str | None) -> dict:
    """Un scoring del corte con la version declarada, en primer plano y con su trabajo, como
    `score-predictivo`: OPERATIVO con el reloj de la base, o BACKTEST con el known_at declarado.
    Despues, su drift por fila."""
    from datetime import datetime
    from uuid import UUID

    from motor_cartera.db.modelos import EjecucionScoringPredictivo, ModoScoringPredictivo
    from motor_cartera.db.sesion import sesion
    from motor_cartera.orquestacion.worker import puntuar_predictivo_en_primer_plano
    from motor_cartera.predictivo import consultas

    antes, base = _wal(), _base_bytes()
    inicio = time.perf_counter()
    e = puntuar_predictivo_en_primer_plano(
        modelo_version_id=UUID(modelo),
        corte_id=UUID(corte_id),
        known_at=None if known_at is None else datetime.fromisoformat(known_at),
        modo=ModoScoringPredictivo.OPERATIVO
        if known_at is None
        else ModoScoringPredictivo.BACKTEST,
    )
    segundos = time.perf_counter() - inicio
    wal, crecimiento = _wal_desde(antes), _base_bytes() - base
    with sesion() as s:
        e = s.get_one(EjecucionScoringPredictivo, e.id)
        if e.estado != "EXITOSA":
            sys.exit(f"El scoring termino {e.estado} ({e.resultado}): {e.detalle}")
        filas = consultas.drift(s, e)
        estados = dict(
            s.execute(
                _texto(
                    "SELECT estado, count(*) FROM prediccion_cuenta "
                    "WHERE ejecucion_scoring_predictivo_id = :e GROUP BY 1"
                ),
                {"e": e.id},
            ).all()
        )
        primera = s.execute(
            _texto(
                "SELECT c.cuenta_id FROM prediccion_cuenta p JOIN cuenta_canonica c "
                "ON c.id = p.cuenta_canonica_id WHERE p.ejecucion_scoring_predictivo_id = :e "
                "ORDER BY c.cuenta_id LIMIT 1"
            ),
            {"e": e.id},
        ).scalar_one()
        fecha = s.execute(
            _texto("SELECT fecha_corte, cartera_id FROM corte_canonico WHERE corte_id = :c"),
            {"c": corte_id},
        ).one()
    return {
        "scoring_run_id": str(e.scoring_run_id),
        "corte_id": corte_id,
        "fecha_corte": str(fecha[0]),
        "cartera_id": fecha[1],
        "modo": str(e.modo),
        "known_at": e.known_at.isoformat(),
        "cuentas": e.cuentas_objetivo,
        "predicciones": e.predicciones_publicadas,
        "por_estado": estados,
        "hash_predicciones": e.hash_predicciones,
        "hash_drift": e.hash_drift,
        "disponibilidad": e.disponibilidad,
        "drift_resumen": e.drift_resumen,
        "drift": [
            {
                "tipo": f.tipo,
                "nombre": f.nombre,
                "psi": float(f.psi),
                "severidad": f.severidad,
                "faltantes_train": None if f.faltantes_train is None else float(f.faltantes_train),
                "faltantes_scoring": (
                    None if f.faltantes_scoring is None else float(f.faltantes_scoring)
                ),
                "severidad_faltantes": f.severidad_faltantes,
            }
            for f in filas
        ],
        "tiempos": e.tiempos,
        "segundos": round(segundos, 3),
        "cuentas_por_segundo": round(e.cuentas_objetivo / segundos),
        "memoria_pico_mb": memoria_pico_mb(),
        "wal_mib": wal,
        "crecimiento_bytes": crecimiento,
        "una_cuenta": str(primera),
    }


def _texto(consulta: str):
    from sqlalchemy import text

    return text(consulta)


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
        sys.exit(f"La etapa {argumentos[1]} fallo con codigo {proceso.returncode}.")
    return json.loads(proceso.stdout.strip().splitlines()[-1])


# --- la API ---------------------------------------------------------------------------------------


def consultar(destino: Path, modelo: str, scoring: str, cuenta: str) -> dict:
    """Las consultas por la API, con un cliente HTTP en el mismo proceso: el tiempo incluye la
    serializacion. Cada una se repite y se reporta su mediana, su p95 y su maximo; despues se
    guarda el plan de cada sentencia que emitio, en el mismo orden y en una transaccion que se
    deshace (las tablas temporales de la explicacion se crean con su EXPLAIN ANALYZE)."""
    from fastapi.testclient import TestClient
    from sqlalchemy import event

    from motor_cartera.api.app import crear_app
    from motor_cartera.config import Config
    from motor_cartera.db.sesion import crear_motor

    clave = "benchmark-predictivo-" + "x" * 24
    cliente = TestClient(crear_app(Config(api_key=clave)), headers={"X-API-Key": clave})
    run = f"/predictivo/scorings/{scoring}"
    predicciones = f"{run}/predicciones?por_pagina=100"
    rutas = {
        "version": f"/predictivo/modelos/{modelo}",
        "registro": "/predictivo/modelos",
        "scoring": run,
        "pagina_por_cuenta": predicciones,
        "pagina_100_por_cuenta": f"{predicciones}&pagina=100",
        "mayor_recuperacion_esperada": f"{predicciones}&orden=RECUPERACION_POSITIVA_ESPERADA_30D"
        "&sentido=DESC",
        "mayor_p_recuperacion": f"{predicciones}&orden=P_RECUPERACION_POSITIVA_30D&sentido=DESC",
        "mayor_riesgo": f"{predicciones}&orden=RIESGO_SIN_RECUPERACION_30D&sentido=DESC",
        "riesgo_alto": f"{predicciones}&riesgo_sin_recuperacion_30d_min=0.95",
        "drift": f"{run}/drift",
        "predicciones_de_una_cuenta": f"/cuentas/{cuenta}/predictivo",
        "explicacion": f"{run}/cuentas/{cuenta}/explicacion",
    }

    def medir(nombre: str, ruta: str) -> dict:
        tiempos = []
        repeticiones = REPETICIONES_EXPLICACION if nombre == "explicacion" else REPETICIONES
        for _ in range(repeticiones):
            inicio = time.perf_counter()
            respuesta = cliente.get(ruta)
            tiempos.append((time.perf_counter() - inicio) * 1000)
            if respuesta.status_code != 200:
                sys.exit(f"{ruta}: {respuesta.status_code} {respuesta.text[:300]}")
            for llave in PROHIBIDAS:
                if llave in respuesta.text:
                    sys.exit(f"{ruta}: la respuesta trae {llave}")
        tiempos.sort()
        return {
            "repeticiones": repeticiones,
            "mediana_ms": round(statistics.median(tiempos), 2),
            "p95_ms": round(tiempos[max(int(len(tiempos) * 0.95) - 1, 0)], 2),
            "maximo_ms": round(tiempos[-1], 2),
        }

    tiempos = {nombre: medir(nombre, ruta) for nombre, ruta in rutas.items()}
    motor = crear_motor()
    planes: dict[str, list] = {}
    for nombre, ruta in rutas.items():
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
                    inicio = sentencia.lstrip().upper()
                    if inicio.startswith(("SELECT", "WITH", "CREATE TEMP TABLE")):
                        cursor.execute("EXPLAIN (ANALYZE, BUFFERS) " + sentencia, parametros)
                        texto = "\n".join(fila[0] for fila in cursor.fetchall())
                        planes[nombre].append({"sql": " ".join(sentencia.split()), "plan": texto})
                    elif inicio.startswith(("CREATE UNIQUE INDEX", "CREATE INDEX", "ANALYZE")):
                        cursor.execute(sentencia, parametros)
            conexion.rollback()
    destino.mkdir(parents=True, exist_ok=True)
    with (destino / "planes_predictivo.txt").open("w", encoding="utf-8") as salida:
        for nombre, sentencias in planes.items():
            for i, sentencia in enumerate(sentencias, start=1):
                salida.write(f"### {nombre} ({i} de {len(sentencias)})\n{sentencia['sql']}\n\n")
                salida.write(sentencia["plan"] + "\n\n")
    return {
        "tiempos": tiempos,
        "sentencias": {nombre: len(p) for nombre, p in planes.items()},
        "milisegundos_en_postgresql": {
            nombre: round(sum(_milisegundos(p["plan"]) for p in planes[nombre]), 3)
            for nombre in rutas
        },
        "seq_scan_sobre_tablas_grandes": {
            nombre: [p["sql"][:160] for p in planes[nombre] if _escanea_grande(p["plan"])]
            for nombre in rutas
            if any(_escanea_grande(p["plan"]) for p in planes[nombre])
        },
        "indices_usados": {
            nombre: sorted(
                {
                    linea.split(" using ")[1].split()[0]
                    for p in planes[nombre]
                    for linea in p["plan"].splitlines()
                    if " using " in linea
                }
            )
            for nombre in rutas
        },
    }


def _milisegundos(plan: str) -> float:
    for linea in plan.splitlines():
        if linea.startswith("Execution Time"):
            return float(linea.split(":")[1].split()[0])
    return 0.0


def _escanea_grande(plan: str) -> bool:
    return any(f"Seq Scan on {tabla} " in plan + " " for tabla in TABLAS_GRANDES)


# --- el inventario y los tamanos ------------------------------------------------------------------


def _inventario() -> dict:
    return {
        "cortes": [
            [str(c), f, n]
            for c, f, n in _sql(
                "SELECT cartera_id, count(*), max(cuentas) FROM corte_canonico GROUP BY 1 "
                "ORDER BY 1"
            )
        ],
        "modelos": [
            [e, n]
            for e, n in _sql("SELECT estado, count(*) FROM version_modelo_predictivo GROUP BY 1")
        ],
    }


def _tamanos() -> dict:
    (base,) = _sql("SELECT pg_database_size(current_database())")
    tablas = _sql(
        "SELECT c.relname, c.reltuples::bigint, pg_relation_size(c.oid), "
        "pg_indexes_size(c.oid), pg_total_relation_size(c.oid) FROM pg_class c "
        "WHERE c.relkind = 'r' AND c.relname = ANY(:tablas) ORDER BY 5 DESC",
        {"tablas": list(TABLAS_PREDICTIVAS)},
    )
    indices = _sql(
        "SELECT i.relname, t.relname, pg_relation_size(i.oid) FROM pg_index x "
        "JOIN pg_class i ON i.oid = x.indexrelid JOIN pg_class t ON t.oid = x.indrelid "
        "WHERE t.relname = ANY(:tablas) ORDER BY 3 DESC",
        {"tablas": list(TABLAS_PREDICTIVAS)},
    )
    ((predicciones,),) = _sql("SELECT count(*) FROM prediccion_cuenta")
    total = next((t[4] for t in tablas if t[0] == "prediccion_cuenta"), 0)
    return {
        "base_bytes": base[0],
        "predicciones": predicciones,
        "bytes_por_prediccion": round(total / predicciones) if predicciones else None,
        "tablas": [
            {"tabla": t, "filas": f, "datos": d, "indices": i, "total": tot}
            for t, f, d, i, tot in tablas
        ],
        "indices": [{"indice": i, "tabla": t, "bytes": b} for i, t, b in indices],
    }


# --- el reporte -----------------------------------------------------------------------------------


def _mib(valor: int | None) -> str:
    return "?" if valor is None else f"{valor / 2**20:,.1f} MiB"


def _num(valor, decimales: int = 1) -> str:
    if valor is None:
        return "?"
    if isinstance(valor, float):
        return f"{valor:,.{decimales}f}"
    try:
        return f"{int(valor):,}"
    except (TypeError, ValueError):
        return str(valor)


def _f(valor, decimales: int = 4) -> str:
    return "" if valor is None else f"{valor:.{decimales}f}"


def _entrenamiento_md(e: dict) -> list[str]:
    t = e["tiempos"] or {}
    fases = t.get("fases_segundos", {})
    del_dataset = t.get("dataset_fases_segundos", {})
    matriz = t.get("matriz", {})
    filas = e["filas"]
    lineas = [
        "## El entrenamiento",
        "",
        f"{e['anclas']} anclas ("
        + ", ".join(f"{k} {v}" for k, v in e["anclas_por_split"].items())
        + f"): TRAIN del {e['periodos']['train'][0]} al {e['periodos']['train'][1]}, VALIDATION "
        f"del {e['periodos']['validation'][0]} al {e['periodos']['validation'][1]}, TEST del "
        f"{e['periodos']['test'][0]} al {e['periodos']['test'][1]}. {e['estado']} "
        f"({e['resultado']}) en **{_num(e['segundos'])} s**, memoria pico del proceso "
        f"{_num(e['memoria_pico_mb'])} MiB, WAL {_num(e['wal_mib'])} MiB.",
        "",
        f"{_num(filas['total'])} filas cuenta-ancla: {_num(filas['train'])} TRAIN, "
        f"{_num(filas['validation'])} VALIDATION, {_num(filas['test'])} TEST y "
        f"{_num(filas['purge'])} PURGE; {_num(filas['censuradas'])} con algun label censurado, "
        "que no entra a ningun ajuste.",
        "",
        f"Matriz de diseno: {matriz.get('features')} features, {matriz.get('columnas')} columnas "
        f"(con los indicadores de nulo y las categorias) y {_num(matriz.get('filas_train'))} filas "
        f"de TRAIN ({_mib((matriz.get('filas_train') or 0) * (matriz.get('columnas') or 0) * 8)} "
        "en float64), que se recorren en bloques de a lo mas "
        f"{_num(matriz.get('filas_por_bloque'))} filas ({_mib(matriz.get('bytes_por_bloque'))}) "
        "sin materializarla entera.",
        "",
        f"Dataset `{(e['dataset_hash'] or '')[:16]}...`, modelo `{e['hash_modelo'][:16]}...`.",
        "",
        "| Fase | Segundos |",
        "|---|---:|",
        *(f"| {fase} | {segundos} |" for fase, segundos in fases.items()),
        *(f"| dataset: {fase} | {segundos} |" for fase, segundos in del_dataset.items()),
        "",
        "### Prevalencias por split",
        "",
        "| Target | Split | Maduras | Censuradas | Prevalencia o media |",
        "|---|---|---:|---:|---:|",
    ]
    for target, por_split in (e["conteos"] or {}).items():
        for split, c in por_split.items():
            tasa = c.get("prevalencia", c.get("media"))
            lineas.append(
                f"| {target} | {split} | {_num(c['maduras'])} | {_num(c['censuradas'])} | "
                f"{_f(tasa)} |"
            )
    lineas += ["", "### Backtest temporal por target", ""]
    for target, b in (e["backtest"] or {}).items():
        metricas_ = [k for k in b.get("agregado_folds", {})]
        lineas += [
            f"**{target}** ({json.dumps(b['hiperparametro'])}):",
            "",
            "| Fold | Estado | " + " | ".join(metricas_) + " | Baseline |",
            "|---|---|" + "---:|" * (len(metricas_) + 1),
        ]
        for fold in b["folds"]:
            base = fold.get("baseline") or {}
            base_txt = ", ".join(
                f"{k} {_f(v)}" for k, v in base.items() if k in ("log_loss", "brier", "mae")
            )
            lineas.append(
                f"| {fold['fold']} | {fold['estado']} | "
                + " | ".join(_f(fold.get(k)) for k in metricas_)
                + f" | {base_txt} |"
            )
        agregado = b.get("agregado_folds", {})
        lineas.append(
            "| agregado | media (min a max) | "
            + " | ".join(
                f"{_f(agregado[k]['media'])} ({_f(agregado[k]['minimo'])} a "
                f"{_f(agregado[k]['maximo'])})"
                for k in metricas_
            )
            + " | |"
        )
        holdout = b.get("holdout_test", {})
        lineas += [
            "",
            "Holdout de TEST, aparte: "
            + ", ".join(
                f"{k} {_f(v)}" for k, v in holdout.items() if k != "baseline" and v is not None
            )
            + "; baseline "
            + ", ".join(
                f"{k} {_f(v)}" for k, v in (holdout.get("baseline") or {}).items() if v is not None
            )
            + ".",
            "",
        ]
    lineas += ["### Las metricas de TEST", ""]
    for c in e["componentes"]:
        t_, b_ = c["metricas_test"], c["baseline_test"]
        lineas += [
            f"**{c['target']}**: {c['algoritmo']} {json.dumps(c['hiperparametros'])}, "
            f"{c['calibracion']}, {c['columnas']} columnas, {c['estado']}.",
            "",
        ]
        if c["algoritmo"] == "LOGISTICA_L2":
            nombres = (
                "n",
                "prevalencia",
                "roc_auc",
                "pr_auc",
                "log_loss",
                "brier",
                "ece",
                "calibracion_intercepto",
                "calibracion_pendiente",
                "lift_10",
                "captura_10",
                "lift_20",
                "captura_20",
            )
            crudo = c.get("sin_calibrar") or {}
            lineas += [
                "| Metrica | Baseline | Sin calibrar | Publicado |",
                "|---|---:|---:|---:|",
                *(
                    f"| {n} | {_f(b_.get(n)) if n != 'n' else _num(b_.get(n))} | "
                    f"{_f(crudo.get(n)) if n != 'n' else ''} | "
                    f"{_f(t_.get(n)) if n != 'n' else _num(t_.get(n))} |"
                    for n in nombres
                ),
                "",
                "| Bin | n | Prediccion media | Frecuencia observada |",
                "|---:|---:|---:|---:|",
                *(
                    f"| {r['bin']} | {_num(r['n'])} | {_f(r['prediccion_media'])} | "
                    f"{_f(r['frecuencia_observada'])} |"
                    for r in c["tabla"] or []
                ),
                "",
            ]
        else:
            lineas += [
                "| Metrica | Baseline | Modelo |",
                "|---|---:|---:|",
                *(
                    f"| {n} | {_f(b_.get(n), 2)} | {_f(t_.get(n), 2)} |"
                    for n in ("mae", "rmse", "real_medio", "predicho_medio", "razon_sumas")
                ),
                "",
                "| Decil | n | Real medio | Predicho medio |",
                "|---:|---:|---:|---:|",
                *(
                    f"| {r['decil']} | {_num(r['n'])} | {_f(r['real_medio'], 2)} | "
                    f"{_f(r['predicho_medio'], 2)} |"
                    for r in c["tabla"] or []
                ),
                "",
            ]
    return lineas


def _scoring_md(titulo: str, s: dict) -> list[str]:
    fases = (s["tiempos"] or {}).get("fases_segundos", {})
    return [
        f"## {titulo}",
        "",
        f"El corte del {s['fecha_corte']} de {s['cartera_id']}: {_num(s['cuentas'])} cuentas, "
        f"{_num(s['predicciones'])} predicciones ("
        + ", ".join(f"{k} {_num(v)}" for k, v in sorted(s["por_estado"].items()))
        + f") en **{_num(s['segundos'])} s**, {_num(s['cuentas_por_segundo'])} cuentas por "
        f"segundo; memoria pico del proceso {_num(s['memoria_pico_mb'])} MiB, WAL "
        f"{_num(s['wal_mib'])} MiB, la base crecio {_mib(s['crecimiento_bytes'])}.",
        "",
        "| Fase | Segundos |",
        "|---|---:|",
        *(f"| {fase} | {segundos} |" for fase, segundos in fases.items()),
        "",
    ]


def _drift_md(a: dict, b: dict) -> list[str]:
    def resumen(s: dict) -> dict:
        features = [f for f in s["drift"] if f["tipo"] == "FEATURE"]
        psis = [f["psi"] for f in features]
        return {
            "alto": sum(f["severidad"] == "ALTO" for f in features),
            "moderado": sum(f["severidad"] == "MODERADO" for f in features),
            "nulos_alto": sum(f["severidad_faltantes"] == "ALTO" for f in features),
            "alguno_alto": sum(
                "ALTO" in (f["severidad"], f["severidad_faltantes"]) for f in features
            ),
            "mediana": statistics.median(psis),
            "media": statistics.fmean(psis),
            "maximo": max(psis),
            "salidas": {f["nombre"]: f["psi"] for f in s["drift"] if f["tipo"] == "SCORE"},
        }

    ra, rb = resumen(a), resumen(b)
    distingue = rb["alguno_alto"] > ra["alguno_alto"] and rb["media"] > ra["media"]
    lineas = [
        "## El drift: A parecido a TRAIN, B desplazado",
        "",
        f"A: el corte del {a['fecha_corte']} de {a['cartera_id']} ({_num(a['cuentas'])} cuentas, "
        f"{a['modo']} a {a['known_at']}). B: el del {b['fecha_corte']} de {b['cartera_id']} "
        f"({_num(b['cuentas'])} cuentas, {b['modo']}), de otro generador. El mismo modelo, sin "
        "cambiar nada.",
        "",
        "| | PSI ALTO | PSI MODERADO | NULL ALTO | Alguno ALTO | PSI medio | PSI mediano | "
        "PSI maximo |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        *(
            f"| {nombre} | {r['alto']} | {r['moderado']} | {r['nulos_alto']} | "
            f"{r['alguno_alto']} | {_f(r['media'])} | {_f(r['mediana'])} | {_f(r['maximo'])} |"
            for nombre, r in (("A", ra), ("B", rb))
        ),
        "",
        "| Salida | PSI en A | PSI en B |",
        "|---|---:|---:|",
        *(
            f"| {n} | {_f(ra['salidas'][n])} | {_f(rb['salidas'].get(n))} |"
            for n in sorted(ra["salidas"])
        ),
        "",
        "El drift "
        + ("**distingue** " if distingue else "**no distingue** ")
        + "B de A (mas features en ALTO, por su PSI o por su fraccion de NULL, y un PSI medio "
        "mayor en B).",
        "",
        "| Feature | PSI en A | PSI en B |",
        "|---|---:|---:|",
    ]
    en_b = {f["nombre"]: f["psi"] for f in b["drift"] if f["tipo"] == "FEATURE"}
    for f in sorted(
        (f for f in a["drift"] if f["tipo"] == "FEATURE"), key=lambda f: -en_b.get(f["nombre"], 0)
    )[:15]:
        lineas.append(f"| {f['nombre']} | {_f(f['psi'])} | {_f(en_b.get(f['nombre']))} |")
    lineas.append("")
    return lineas


def _markdown(reporte: dict) -> str:
    lineas = [
        "# Benchmark de predictive/model-v1",
        "",
        f"{reporte['fecha']}, {reporte['maquina']}. PostgreSQL {reporte['postgresql']}, con "
        + ", ".join(f"{k} {v}" for k, v in reporte["configuracion"].items())
        + ".",
        "",
        "```json",
        json.dumps(reporte["inventario"], indent=1, ensure_ascii=False),
        "```",
        "",
    ]
    if "entrenamiento" in reporte:
        lineas += _entrenamiento_md(reporte["entrenamiento"])
    if "scoring_xl" in reporte:
        lineas += _scoring_md("El scoring XL", reporte["scoring_xl"])
    if "scoring_a" in reporte and "scoring_xl" in reporte:
        lineas += _scoring_md("El scoring A", reporte["scoring_a"])
        lineas += _drift_md(reporte["scoring_a"], reporte["scoring_xl"])
    tamanos = reporte.get("tamanos")
    if tamanos:
        lineas += [
            "## Tamanos",
            "",
            f"{_num(tamanos['despues']['predicciones'])} predicciones, "
            f"{_num(tamanos['despues']['bytes_por_prediccion'])} bytes cada una con sus indices. "
            f"La base crecio {_mib(tamanos['crecimiento_bytes'])} con todo el benchmark.",
            "",
            "| Tabla | Filas | Datos | Indices | Total |",
            "|---|---:|---:|---:|---:|",
            *(
                f"| {t['tabla']} | {_num(t['filas'])} | {_mib(t['datos'])} | "
                f"{_mib(t['indices'])} | {_mib(t['total'])} |"
                for t in tamanos["despues"]["tablas"]
            ),
            "",
            "| Indice | Tabla | Tamano |",
            "|---|---|---:|",
            *(
                f"| {i['indice']} | {i['tabla']} | {_mib(i['bytes'])} |"
                for i in tamanos["despues"]["indices"]
            ),
            "",
        ]
    api = reporte.get("api")
    if api:
        lineas += [
            "## La API sobre el scoring XL",
            "",
            "Cada consulta repetida, con la serializacion.",
            "",
            "| Consulta | Repeticiones | Mediana ms | p95 ms | Maximo ms | Sentencias | "
            "ms en PostgreSQL | Indices |",
            "|---|---:|---:|---:|---:|---:|---:|---|",
            *(
                f"| {nombre} | {t['repeticiones']} | {t['mediana_ms']} | {t['p95_ms']} | "
                f"{t['maximo_ms']} | {api['sentencias'][nombre]} | "
                f"{api['milisegundos_en_postgresql'][nombre]} | "
                f"{', '.join(api['indices_usados'][nombre])} |"
                for nombre, t in api["tiempos"].items()
            ),
            "",
            "Seq Scan sobre una tabla grande: "
            + (
                json.dumps(api["seq_scan_sobre_tablas_grandes"], ensure_ascii=False)
                if api["seq_scan_sobre_tablas_grandes"]
                else "ninguno"
            )
            + ".",
            "",
        ]
    return "\n".join(lineas)


def _reporte(bitacora: Bitacora) -> dict:
    reporte = {
        "fecha": bitacora["inicio"]["fecha"],
        "maquina": bitacora["inicio"]["maquina"],
        "postgresql": bitacora["inicio"]["postgresql"],
        "configuracion": bitacora["inicio"]["configuracion"],
        "inventario": bitacora["inicio"]["inventario"],
    }
    for clave in ("entrenamiento", "scoring_xl", "scoring_a", "tamanos", "api"):
        if clave in bitacora:
            reporte[clave] = bitacora[clave]
    return reporte


def _corte(cartera: str, fecha: str) -> str:
    from motor_cartera.config import config

    filas = _sql(
        "SELECT CAST(corte_id AS text) FROM corte_canonico WHERE despacho_id = :d "
        "AND cartera_id = :c AND fecha_corte = :f",
        {"d": config.despacho_id, "c": cartera, "f": date.fromisoformat(fecha)},
    )
    if not filas:
        sys.exit(f"No existe el corte del {fecha} de {cartera}.")
    return filas[0][0]


def main(argumentos: argparse.Namespace) -> None:
    _exigir_bench()
    destino = Path(argumentos.destino)
    destino.mkdir(parents=True, exist_ok=True)
    bitacora = Bitacora(destino / "benchmark_predictivo.etapas.json", argumentos.reanudar)
    if "inicio" not in bitacora:
        _migrar()
        ((version,),) = _sql("SHOW server_version")
        bitacora.anotar(
            "inicio",
            {
                "fecha": date.today().isoformat(),
                "maquina": f"{platform.system()} {platform.release()}, {platform.processor()}",
                "postgresql": version,
                "configuracion": {p: _sql(f"SHOW {p}")[0][0] for p in CONFIGURACION},
                "inventario": _inventario(),
                "base_bytes": _base_bytes(),
            },
        )
        print("inventario anotado", flush=True)
    if "entrenamiento" not in bitacora:
        resultado = _hijo("--etapa", "entrenar", "--manifiesto", argumentos.manifiesto)
        bitacora.anotar("entrenamiento", resultado)
        print(f"entrenamiento: {resultado['segundos']} s", flush=True)
    modelo = bitacora["entrenamiento"]["modelo_version_id"]
    if "scoring_xl" not in bitacora:
        corte = _corte(argumentos.xl_cartera, argumentos.xl_fecha)
        resultado = _hijo("--etapa", "puntuar", "--modelo", modelo, "--corte-id", corte)
        bitacora.anotar("scoring_xl", resultado)
        print(f"scoring XL: {resultado['segundos']} s", flush=True)
    if "scoring_a" not in bitacora:
        declarado = json.loads(Path(argumentos.manifiesto).read_text(encoding="utf-8"))
        anclas = declarado.get("manifiesto", declarado)["anclas"]
        ultima = max(
            (x for x in anclas if x["split"] == "VALIDATION"), key=lambda x: x["feature_as_of"]
        )
        resultado = _hijo(
            "--etapa",
            "puntuar",
            "--modelo",
            modelo,
            "--corte-id",
            ultima["corte_id"],
            "--known-at",
            ultima["feature_known_at"],
        )
        bitacora.anotar("scoring_a", resultado)
        print(f"scoring A: {resultado['segundos']} s", flush=True)
    if "tamanos" not in bitacora:
        segundos = _vacuum(TABLAS_PREDICTIVAS)
        despues = _tamanos()
        bitacora.anotar(
            "tamanos",
            {
                "vacuum_segundos": segundos,
                "despues": despues,
                "crecimiento_bytes": despues["base_bytes"] - bitacora["inicio"]["base_bytes"],
            },
        )
        print("tamanos anotados", flush=True)
    if "api" not in bitacora:
        xl = bitacora["scoring_xl"]
        resultado = _hijo(
            "--etapa",
            "consultar",
            "--destino",
            str(destino),
            "--modelo",
            modelo,
            "--scoring",
            xl["scoring_run_id"],
            "--cuenta",
            xl["una_cuenta"],
        )
        bitacora.anotar("api", resultado)
        print("API medida", flush=True)
    reporte = _reporte(bitacora)
    (destino / "benchmark_predictivo.json").write_text(
        json.dumps(reporte, indent=1, ensure_ascii=False, default=str), encoding="utf-8"
    )
    (destino / "benchmark_predictivo.md").write_text(_markdown(reporte), encoding="utf-8")
    print(f"Escrito: {destino / 'benchmark_predictivo.md'}")


if __name__ == "__main__":
    argumentos_ = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    argumentos_.add_argument("--manifiesto", help="El JSON de construir-manifiesto-predictivo.")
    argumentos_.add_argument("--destino", default="datos/benchmark_predictivo")
    argumentos_.add_argument("--xl-cartera", default="CARTERA_PRINCIPAL")
    argumentos_.add_argument("--xl-fecha", default="2026-04-01")
    argumentos_.add_argument("--reanudar", action="store_true")
    argumentos_.add_argument(
        "--etapa", choices=("entrenar", "puntuar", "consultar"), help=argparse.SUPPRESS
    )
    argumentos_.add_argument("--modelo", help=argparse.SUPPRESS)
    argumentos_.add_argument("--corte-id", help=argparse.SUPPRESS)
    argumentos_.add_argument("--known-at", help=argparse.SUPPRESS)
    argumentos_.add_argument("--scoring", help=argparse.SUPPRESS)
    argumentos_.add_argument("--cuenta", help=argparse.SUPPRESS)
    leidos = argumentos_.parse_args()
    if leidos.etapa == "entrenar":
        print(json.dumps(entrenar(leidos.manifiesto), default=str))
    elif leidos.etapa == "puntuar":
        print(json.dumps(puntuar(leidos.modelo, leidos.corte_id, leidos.known_at), default=str))
    elif leidos.etapa == "consultar":
        print(
            json.dumps(
                consultar(Path(leidos.destino), leidos.modelo, leidos.scoring, leidos.cuenta),
                default=str,
            )
        )
    else:
        if not leidos.manifiesto:
            argumentos_.error("Declara --manifiesto.")
        main(leidos)
