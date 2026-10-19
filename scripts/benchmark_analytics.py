"""Benchmark de analytics/v1: cuanto tarda analizar una cohorte grande, por fase, cuanto crece la
base, de que depende lo publicado y como responden las consultas de la API, contra PostgreSQL. No
corre en el CI: se pide a mano, sobre una base *_bench que ya tiene el XL de los benchmarks
anteriores (sus cortes, sus pagos interpretados y atribuidos, su lifecycle, su evaluacion de
promesas, decision/v2, la geografia, territorial/v2, campo/v2 y ruteo/v2):

    python scripts/benchmark_analytics.py --destino <dir>
    python scripts/benchmark_analytics.py --destino <dir> --reanudar

No reconstruye nada: ni la historia, ni los pagos, ni el lifecycle, ni la geografia, ni campo/v2,
ni ruteo/v2. Cada analisis toma su manifiesto de `analytics.seleccion.proponer` sobre lo que existe
(sus reglas, documentadas en docs/analytics.md), y donde una familia tiene dos o mas candidatas el
benchmark DECLARA AQUI cual usa, antes de ver ningun resultado. Lo que no existe se declara
ausente y sus metricas salen NO_DISPONIBLE: nada se inventa. Las etapas:

1. sube la base a la cabeza de las migraciones (la 0015), anota su configuracion e inventaria lo
   que hay;
2. XL-A, la cadena de operacion: el corte que tiene geografia, territorial/v2, plan de campo y
   ruteo (el del 2026-04-01, 500,000 cuentas), 30 dias, por DIA, con todas las familias que existen
   para el; el lifecycle del XL termina antes de su horizonte, y no hay una evaluacion de promesas
   a su as_of: se declara;
3. XL-A sin operacion: el mismo corte y el mismo horizonte, solo con sus pagos y su atribucion. La
   diferencia en la fase de la cohorte es lo que cuestan la decision, la geografia y la territorial,
   que se unen a la cohorte por llave primaria en la misma sentencia;
4. XL-B, el lifecycle: el primer corte semanal (el del 2026-01-07, 500,000 cuentas) hasta el dia de
   la unica evaluacion de promesas (2026-03-25), por SEMANA_ISO, con los pagos, la atribucion, todo
   el lifecycle del XL y esa evaluacion;
5. la serie historica: los 12 cortes semanales, cada uno con 30 dias, por MES, con sus pagos y su
   atribucion, uno tras otro: tiempo por analisis, filas y crecimiento de la base;
6. VACUUM ANALYZE y tamanos: cuanto ocupan las tablas de analytics y sus indices, y de que depende
   lo publicado (periodos x celdas x metricas, no cuentas x dias);
7. la API sobre XL-A: la mediana, el p95 y el maximo del catalogo, la ejecucion, el resumen, una
   metrica, la serie de la recuperacion, el producto, el tramo de atraso, la prioridad, la base, el
   municipio, los embudos y los controles; y el EXPLAIN (ANALYZE, BUFFERS) de cada sentencia que
   emitieron, con las que recorren una tabla grande;
8. la sensibilidad a work_mem: XL-B otra vez, con otro known_at (y por eso otra firma), con
   work_mem de 256MB solo en las conexiones de ese proceso. El motor no fija work_mem: si las fases
   que agregan millones de hechos derraman a disco con el valor por omision, aqui se ve cuanto.

Cada analisis corre en su propio proceso, para que su memoria pico sea solo suya, con el cronometro
por fase y su WAL. MC_DATABASE_URL tiene que apuntar a una base *_bench. Cada etapa se anota en una
bitacora (benchmark_analytics.etapas.json) en cuanto termina; con --reanudar no se repite ninguna.
Ninguna salida lleva un dato de una cuenta: solo conteos, tiempos, identificadores opacos y planes.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from benchmark_escala import memoria_pico_mb  # noqa: E402
from benchmark_lifecycle import _exigir_bench, _migrar, _wal, _wal_desde  # noqa: E402
from benchmark_motor_pagos import Bitacora, _sql, _vacuum  # noqa: E402

ZONA = "America/Mexico_City"
REPETICIONES = 30
CORTE_OPERACION = date(2026, 4, 1)
AS_OF_OPERACION = date(2026, 4, 30)
CORTE_LIFECYCLE = date(2026, 1, 7)
AS_OF_LIFECYCLE = date(2026, 3, 25)
HORIZONTE_HISTORICO = 30
TABLAS_ANALYTICS = (
    "ejecucion_analytics",
    "entrada_analytics",
    "agregado_analytics",
    "control_analytics",
)
TABLAS_GRANDES = (
    "snapshot_cuenta",
    "movimiento_economico_canonico",
    "gestion_cobranza",
    "evento_lifecycle",
    "promesa_pago",
    "agregado_analytics",
    "cuenta_canonica",
)
PROHIBIDAS = ('"cuenta_id"', '"cliente_unico"', '"actor_ref"', '"latitud"', '"longitud"')
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


# --- lo que el benchmark declara donde hay mas de una candidata -----------------------------------


def _declaradas(corte_id: str) -> dict:
    """Las entradas que se declaran a mano para que la propuesta no sea ambigua, decididas antes de
    analizar nada: el plan de campo/v2 de la configuracion A del benchmark de campo/v2 (el de menor
    id del corte; el de la B tiene las mismas entradas y otra configuracion) y, de los ruteos de ese
    plan, el que corrio contra una red vial real y no contra el modelo geometrico sintetico (el
    benchmark B de ruteo/v2, sobre el extracto de OpenStreetMap). Las demas familias se proponen con
    sus reglas: si alguna resulta ambigua, el benchmark se detiene en vez de elegir."""
    from motor_cartera.analytics.catalogo import Fuente

    declaradas: dict = {}
    planes = _sql(
        "SELECT p.campo_run_id, p.id FROM ejecucion_campo_v2 p JOIN corte_canonico c "
        "ON c.id = p.corte_canonico_id WHERE c.corte_id = :c AND p.estado = 'EXITOSA' "
        "ORDER BY p.id",
        {"c": corte_id},
    )
    if planes:
        declaradas[Fuente.CAMPO_V2] = (planes[0][0],)
        ruteos = _sql(
            "SELECT r.ruteo_run_id FROM ejecucion_ruteo_v2 r JOIN version_red_vial v "
            "ON v.id = r.version_red_vial_id WHERE r.ejecucion_campo_v2_id = :p "
            "AND r.estado = 'EXITOSA' AND v.proveedor_datos <> 'SINTETICO' ORDER BY r.id DESC",
            {"p": planes[0][1]},
        )
        if ruteos:
            declaradas[Fuente.RUTEO_V2] = (ruteos[0][0],)
    return declaradas


# --- un analisis, en su proceso -------------------------------------------------------------------


def analizar(
    corte: str, as_of: str, granularidad: str, familias: str, work_mem: str | None = None
) -> dict:
    """Propone el manifiesto, lo abre y lo analiza con el cronometro por fase. Si esas entradas ya
    se analizaron (la corrida se detuvo entre publicar y anotarlo), lo reporta como esta, sin
    tiempos. Con `work_mem`, cada conexion de este proceso lo fija al abrirse."""
    from sqlalchemy import event, text
    from sqlmodel import select

    from motor_cartera.analytics import ejecuciones as motor
    from motor_cartera.analytics import seleccion
    from motor_cartera.analytics.catalogo import Fuente
    from motor_cartera.analytics.politica import Granularidad, Modo
    from motor_cartera.config import config
    from motor_cartera.db.modelos import CorteCanonico, EjecucionAnalytics
    from motor_cartera.db.sesion import sesion
    from motor_cartera.decision_v2.ejecuciones import esperar_frontera
    from motor_cartera.ingesta.fuente_oficial import Cronometro

    if work_mem is not None:
        from motor_cartera.db.sesion import crear_motor

        def fijar(conexion, registro) -> None:
            with conexion.cursor() as cursor:
                cursor.execute(f"SET work_mem = '{work_mem}'")

        event.listen(crear_motor(), "connect", fijar)
    elegidas = frozenset(Fuente(f) for f in familias.split(",") if f)
    with sesion() as s:
        corte_ = s.exec(
            select(CorteCanonico).where(
                CorteCanonico.despacho_id == config.despacho_id,
                CorteCanonico.cartera_id == config.cartera_id,
                CorteCanonico.fecha_corte == date.fromisoformat(corte),
            )
        ).one()
        known_at = s.execute(text("SELECT now()")).scalar_one()
        inicio_propuesta = time.perf_counter()
        propuesta = seleccion.proponer(
            s,
            corte=corte_,
            as_of=date.fromisoformat(as_of),
            modo=Modo.OPERATIVO,
            zona=ZONA,
            granularidad=Granularidad(granularidad),
            known_at=known_at,
            familias=elegidas,
            declaradas={
                t: v for t, v in _declaradas(str(corte_.corte_id)).items() if t in elegidas
            },
        )
        segundos_propuesta = time.perf_counter() - inicio_propuesta
        if propuesta.ambiguas:
            sys.exit(f"Familias ambiguas: {[e.tipo.value for e in propuesta.ambiguas]}")
        elecciones = [
            {
                "tipo": e.tipo.value,
                "ventana": None if e.ventana is None else e.ventana.isoformat(),
                "regla": e.regla,
                "elegida": None if e.elegida is None else str(e.elegida.referencia),
                "candidatas": len(e.candidatas),
            }
            for e in propuesta.elecciones
        ]
        recuperado = False
        try:
            ejecucion = motor.abrir(s, propuesta.peticion, max_intentos=config.worker_max_intentos)
            s.commit()
            ejecucion_id = ejecucion.id
        except motor.AnalyticsYaGenerada as exc:
            ejecucion_id, recuperado = exc.previa.id, True
        except motor.AnalyticsEnProceso as exc:
            ejecucion_id = exc.activa.id
    # Lo que hace un trabajo ANALYTICS: esperar a que la frontera de known_at se asiente y analizar.
    # La espera no cuenta en el tiempo del analisis.
    esperar_frontera(known_at)
    antes = _wal()
    cronometro = Cronometro()
    inicio = time.perf_counter()
    if not recuperado:
        motor.analizar(ejecucion_id, cronometro=cronometro)
    segundos = time.perf_counter() - inicio
    with sesion() as s:
        e = s.get_one(EjecucionAnalytics, ejecucion_id)
        controles = dict(
            s.execute(
                text(
                    "SELECT codigo_control, esperado FROM control_analytics "
                    "WHERE ejecucion_analytics_id = :e"
                ),
                {"e": ejecucion_id},
            ).all()
        )
        celdas, conjuntos, periodos = s.execute(
            text(
                "SELECT count(DISTINCT (dimension_set, dimension_hash)), "
                "count(DISTINCT dimension_set), count(DISTINCT periodo_desde) FILTER "
                "(WHERE vista = 'PERIODO') FROM agregado_analytics "
                "WHERE ejecucion_analytics_id = :e"
            ),
            {"e": ejecucion_id},
        ).one()
        por_estado = dict(
            s.execute(
                text(
                    "SELECT estado_valor, count(*) FROM agregado_analytics "
                    "WHERE ejecucion_analytics_id = :e GROUP BY 1"
                ),
                {"e": ejecucion_id},
            ).all()
        )
        resultado = {
            "ejecucion_id": e.id,
            "analytics_run_id": str(e.analytics_run_id),
            "estado": str(e.estado),
            "resultado": e.resultado,
            "corte": corte,
            "as_of": as_of,
            "granularidad": granularidad,
            "horizonte_dias": e.horizonte_dias,
            "known_at": e.known_at.isoformat(),
            "firma_inputs": e.firma_inputs,
            "hash_manifiesto": e.hash_manifiesto,
            "hash_agregados": e.hash_agregados,
            "cuentas_cohorte": e.cuentas_cohorte,
            "agregados_publicados": e.agregados_publicados,
            "por_estado": por_estado,
            "celdas": celdas,
            "conjuntos": conjuntos,
            "periodos": periodos,
            "controles_totales": e.controles_totales,
            "controles_fallidos": e.controles_fallidos,
            "disponibilidad": e.disponibilidad,
            "elecciones": elecciones,
            "leido": {
                "movimientos_del_horizonte": controles.get("pagos.universo_de_movimientos"),
                "movimientos_de_la_cohorte": controles.get("lectura.movimientos_sin_multiplicar"),
                "gestiones": controles.get("lectura.gestiones_sin_multiplicar"),
                "promesas": controles.get("lectura.promesas_sin_multiplicar"),
                "atribuciones": controles.get("lectura.atribuciones_sin_multiplicar"),
            },
            "detalle": e.detalle,
        }
    if resultado["estado"] != "EXITOSA":
        sys.exit(f"analytics/v1 termino {resultado['estado']}: {resultado['detalle']}")
    medicion = (
        {"segundos": None, "fases": {}, "memoria_pico_mb": None, "wal_mib": None}
        if recuperado
        else {
            "segundos": round(segundos, 3),
            "segundos_propuesta": round(segundos_propuesta, 3),
            "cuentas_por_segundo": round(resultado["cuentas_cohorte"] / segundos),
            "fases": {nombre: round(s_, 3) for nombre, s_ in cronometro.fases.items()},
            "memoria_pico_mb": memoria_pico_mb(),
            "wal_mib": _wal_desde(antes),
        }
    )
    if work_mem is not None:
        with sesion() as s:
            medicion["work_mem"] = s.execute(text("SHOW work_mem")).scalar_one()
    return {**resultado, **medicion, "recuperado": recuperado}


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


# --- la API ---------------------------------------------------------------------------------------


def consultar(destino: Path, analytics_run_id: str) -> dict:
    """Las consultas por la API, con un cliente HTTP en el mismo proceso: el tiempo incluye la
    serializacion. Cada una se repite y se reporta su mediana, su p95 y su maximo; despues se
    guarda el plan de cada sentencia que emitio."""
    from fastapi.testclient import TestClient
    from sqlalchemy import event

    from motor_cartera.api.app import crear_app
    from motor_cartera.config import Config
    from motor_cartera.db.sesion import crear_motor

    clave = "benchmark-analytics-" + "x" * 24
    cliente = TestClient(
        crear_app(Config(app_env="development", api_key=clave)), headers={"X-API-Key": clave}
    )
    run = f"/analytics/ejecuciones/{analytics_run_id}"
    agregados = f"{run}/agregados"
    rutas = {
        "catalogo": "/analytics/catalogo",
        "ejecucion": run,
        "resumen_total": f"{run}/resumen",
        "una_metrica_total": f"{agregados}?metrica=recuperacion.neta&dimension_set=TOTAL"
        "&vista=ACUMULADA",
        "serie_recuperacion_neta": f"{agregados}?metrica=recuperacion.neta&dimension_set=TOTAL"
        "&vista=PERIODO&por_pagina=100",
        "producto": f"{agregados}?metrica=recuperacion.neta&dimension_set=PRODUCTO&vista=ACUMULADA",
        "atraso_bucket": f"{agregados}?metrica=cartera.cuentas&dimension_set=ATRASO_BUCKET"
        "&vista=ACUMULADA",
        "prioridad": f"{agregados}?metrica=gestion.cuentas_gestionadas"
        "&dimension_set=PRIORIDAD_DECISION&vista=ACUMULADA",
        "base": f"{agregados}?metrica=campo.asignadas&dimension_set=BASE",
        "municipio": f"{agregados}?metrica=cartera.cuentas&dimension_set=MUNICIPIO&por_pagina=100",
        "municipio_pagina_10": f"{agregados}?metrica=cartera.cuentas&dimension_set=MUNICIPIO"
        "&por_pagina=100&pagina=10",
        "un_producto_por_dimension": f"{agregados}?dimension_set=PRODUCTO&producto=CONSUMO"
        "&vista=ACUMULADA&por_pagina=100",
        "suprimidas": f"{agregados}?estado_valor=SUPRIMIDO&por_pagina=100",
        "embudos": f"{run}/embudos",
        "controles": f"{run}/controles",
    }

    def medir(ruta: str) -> dict:
        tiempos = []
        for _ in range(REPETICIONES):
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
            "mediana_ms": round(statistics.median(tiempos), 2),
            "p95_ms": round(tiempos[int(len(tiempos) * 0.95) - 1], 2),
            "maximo_ms": round(tiempos[-1], 2),
        }

    tiempos = {nombre: medir(ruta) for nombre, ruta in rutas.items()}
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
                    if not sentencia.lstrip().upper().startswith(("SELECT", "WITH")):
                        continue
                    cursor.execute("EXPLAIN (ANALYZE, BUFFERS) " + sentencia, parametros)
                    texto = "\n".join(fila[0] for fila in cursor.fetchall())
                    planes[nombre].append({"sql": " ".join(sentencia.split()), "plan": texto})
            conexion.rollback()
    destino.mkdir(parents=True, exist_ok=True)
    with (destino / "planes_analytics.txt").open("w", encoding="utf-8") as salida:
        for nombre, sentencias in planes.items():
            for i, sentencia in enumerate(sentencias, start=1):
                salida.write(f"### {nombre} ({i} de {len(sentencias)})\n{sentencia['sql']}\n\n")
                salida.write(sentencia["plan"] + "\n\n")
    return {
        "repeticiones": REPETICIONES,
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
    def filas(consulta: str) -> list:
        return [[str(v) if v is not None else None for v in f] for f in _sql(consulta)]

    return {
        "cortes": filas("SELECT fecha_corte, cuentas FROM corte_canonico ORDER BY fecha_corte"),
        "ventanas_de_pagos": filas(
            "SELECT periodo_desde, estado, count(*), max(id) FROM ejecucion_motor_pagos "
            "GROUP BY 1, 2 ORDER BY 1, 2"
        ),
        "movimientos": filas(
            "SELECT count(*), min(fecha_recepcion), max(fecha_recepcion) "
            "FROM movimiento_economico_canonico"
        ),
        "atribuciones": filas(
            "SELECT periodo_desde, estado, count(*) FROM ejecucion_atribucion GROUP BY 1, 2 "
            "ORDER BY 1"
        ),
        "evaluaciones": filas(
            "SELECT as_of, zona_horaria, estado FROM ejecucion_evaluacion_promesas ORDER BY 1"
        ),
        "eventos_lifecycle": filas(
            "SELECT tipo_evento, count(*), min(ocurrido_en)::date, max(ocurrido_en)::date "
            "FROM evento_lifecycle GROUP BY 1 ORDER BY 1"
        ),
        "decisiones_v2": filas(
            "SELECT c.fecha_corte, count(*) FROM ejecucion_decision_v2 d JOIN corte_canonico c "
            "ON c.id = d.corte_canonico_id WHERE d.estado = 'EXITOSA' GROUP BY 1 ORDER BY 1"
        ),
        "geografias": filas(
            "SELECT c.fecha_corte, count(*) FROM ejecucion_geografia g JOIN corte_canonico c "
            "ON c.id = g.corte_canonico_id WHERE g.estado = 'EXITOSA' GROUP BY 1 ORDER BY 1"
        ),
        "territoriales_v2": filas(
            "SELECT c.fecha_corte, count(*) FROM ejecucion_territorial_v2 t JOIN corte_canonico c "
            "ON c.id = t.corte_canonico_id WHERE t.estado = 'EXITOSA' GROUP BY 1 ORDER BY 1"
        ),
        "planes_campo_v2": filas(
            "SELECT c.fecha_corte, p.fecha_operacion, count(*) FROM ejecucion_campo_v2 p "
            "JOIN corte_canonico c ON c.id = p.corte_canonico_id WHERE p.estado = 'EXITOSA' "
            "GROUP BY 1, 2 ORDER BY 1"
        ),
        "ruteos_v2": filas(
            "SELECT ejecucion_campo_v2_id, count(*) FROM ejecucion_ruteo_v2 "
            "WHERE estado = 'EXITOSA' GROUP BY 1 ORDER BY 1"
        ),
    }


def _tamanos_analytics() -> dict:
    (base,) = _sql("SELECT pg_database_size(current_database())")
    tablas = _sql(
        "SELECT c.relname, c.reltuples::bigint, pg_relation_size(c.oid), "
        "pg_indexes_size(c.oid), pg_total_relation_size(c.oid) FROM pg_class c "
        "WHERE c.relkind = 'r' AND c.relname = ANY(:tablas) ORDER BY 5 DESC",
        {"tablas": list(TABLAS_ANALYTICS)},
    )
    indices = _sql(
        "SELECT i.relname, t.relname, pg_relation_size(i.oid) FROM pg_index x "
        "JOIN pg_class i ON i.oid = x.indexrelid JOIN pg_class t ON t.oid = x.indrelid "
        "WHERE t.relname = ANY(:tablas) ORDER BY 3 DESC",
        {"tablas": list(TABLAS_ANALYTICS)},
    )
    ((agregados,),) = _sql("SELECT count(*) FROM agregado_analytics")
    total_agregado = next((t[4] for t in tablas if t[0] == "agregado_analytics"), 0)
    return {
        "base_bytes": base[0],
        "agregados": agregados,
        "bytes_por_agregado": round(total_agregado / agregados) if agregados else None,
        "tablas": [
            {"tabla": t, "filas": f, "datos": d, "indices": i, "total": tot}
            for t, f, d, i, tot in tablas
        ],
        "indices": [{"indice": i, "tabla": t, "bytes": b} for i, t, b in indices],
    }


# --- el reporte -----------------------------------------------------------------------------------


def _mib(valor: int | None) -> str:
    return "?" if valor is None else f"{valor / 2**20:,.1f} MiB"


def _num(valor, sufijo: str = "") -> str:
    if valor is None:
        return "?"
    if isinstance(valor, float):
        return f"{valor:,.1f}{sufijo}"
    try:
        return f"{int(valor):,}{sufijo}"
    except (TypeError, ValueError):
        return f"{valor}{sufijo}"


def _analisis_md(titulo: str, a: dict) -> list[str]:
    lineas = [
        f"### {titulo}",
        "",
        f"Corte {a['corte']} al {a['as_of']} ({a['horizonte_dias']} dias, {a['granularidad']}), "
        f"known_at {a['known_at']}: {_num(a['cuentas_cohorte'])} cuentas. "
        f"{a['estado']} ({a['resultado']}) en **{_num(a['segundos'])} s** "
        f"({_num(a.get('cuentas_por_segundo'))} cuentas por segundo), memoria pico del proceso "
        f"{_num(a['memoria_pico_mb'])} MiB, WAL {_num(a['wal_mib'])} MiB. Proponer el manifiesto "
        f"tomo {_num(a.get('segundos_propuesta'))} s.",
        "",
        f"Leido: {_num(a['leido']['movimientos_del_horizonte'])} movimientos del horizonte "
        f"({_num(a['leido']['movimientos_de_la_cohorte'])} de la cohorte), "
        f"{_num(a['leido']['gestiones'])} gestiones efectivas, {_num(a['leido']['promesas'])} "
        f"promesas, {_num(a['leido']['atribuciones'])} pagos atribuidos.",
        "",
        f"Publicado: {_num(a['agregados_publicados'])} agregados en {_num(a['celdas'])} celdas de "
        f"{a['conjuntos']} conjuntos y {a['periodos']} periodos ("
        + ", ".join(f"{k} {_num(v)}" for k, v in sorted(a["por_estado"].items()))
        + f"); {a['controles_totales']} controles, {a['controles_fallidos']} fallidos; "
        f"{_num((a['disponibilidad'] or {}).get('celdas_suprimidas'))} celdas suprimidas. "
        f"Hash de lo publicado `{(a['hash_agregados'] or '')[:16]}...`.",
        "",
        "Lo publicado depende de las celdas y los periodos, no de las cuentas: "
        f"{a['agregados_publicados'] / a['cuentas_cohorte']:.3f} agregados por cuenta, contra "
        f"{_num(a['cuentas_cohorte'] * a['horizonte_dias'])} cuentas x dias del horizonte.",
        "",
        "| Entrada | Ventana | Regla | Candidatas |",
        "|---|---|---|---:|",
        *(
            f"| {e['tipo']} | {e['ventana'] or ''} | {e['regla']} | {e['candidatas']} |"
            for e in a["elecciones"]
        ),
        "",
    ]
    familias = (a["disponibilidad"] or {}).get("familias", {})
    if familias:
        lineas += [
            "Disponibilidad: "
            + "; ".join(
                f"{f} {d['estado']}"
                + (f" (faltan {', '.join(d['faltan'])})" if d.get("faltan") else "")
                for f, d in familias.items()
            )
            + ".",
            "",
        ]
    if a["fases"]:
        lineas += ["| Fase | Segundos |", "|---|---:|"]
        lineas += [f"| {fase} | {segundos} |" for fase, segundos in a["fases"].items()]
        lineas.append("")
    return lineas


def _markdown(reporte: dict) -> str:
    lineas = [
        "# Benchmark de analytics/v1",
        "",
        f"{reporte['fecha']}, {reporte['maquina']}. PostgreSQL {reporte['postgresql']}, con "
        + ", ".join(f"{k} {v}" for k, v in reporte["configuracion"].items())
        + ".",
        "",
        "## Lo que habia (sin reconstruir nada)",
        "",
        "```json",
        json.dumps(reporte["inventario"], indent=1, ensure_ascii=False),
        "```",
        "",
    ]
    for clave, titulo in (
        ("xl_operacion", "XL-A: la cadena de operacion"),
        ("xl_sin_operacion", "XL-A sin operacion: solo pagos y atribucion"),
        ("xl_lifecycle", "XL-B: el lifecycle"),
    ):
        if clave in reporte:
            lineas += _analisis_md(titulo, reporte[clave])
    if "xl_operacion" in reporte and "xl_sin_operacion" in reporte:
        con, sin = reporte["xl_operacion"]["fases"], reporte["xl_sin_operacion"]["fases"]
        lineas += [
            f"La cohorte con decision, geografia y territorial tomo {con.get('cohorte')} s, y sin "
            f"ellas {sin.get('cohorte')} s: unirlas por llave primaria cuesta la diferencia. Campo "
            f"y ruteo (la fase operacion) tomaron {con.get('operacion')} s.",
            "",
        ]
    historico = reporte.get("historico")
    if historico:
        tiempos = [h["segundos"] for h in historico if h["segundos"] is not None]
        lineas += [
            "### La serie historica",
            "",
            f"{len(historico)} cortes semanales, {HORIZONTE_HISTORICO} dias cada uno, por MES: "
            f"{_num(sum(tiempos))} s en total, mediana {_num(statistics.median(tiempos))} s por "
            f"analisis; {_num(sum(h['agregados_publicados'] for h in historico))} agregados.",
            "",
            "| Corte | Cuentas | Segundos | Agregados | Celdas | WAL MiB | Memoria MiB |",
            "|---|---:|---:|---:|---:|---:|---:|",
            *(
                f"| {h['corte']} | {_num(h['cuentas_cohorte'])} | {_num(h['segundos'])} | "
                f"{_num(h['agregados_publicados'])} | {_num(h['celdas'])} | {_num(h['wal_mib'])} | "
                f"{_num(h['memoria_pico_mb'])} |"
                for h in historico
            ),
            "",
        ]
    tamanos = reporte.get("tamanos")
    if tamanos:
        lineas += [
            "## Tamanos",
            "",
            f"La base crecio {_mib(tamanos['crecimiento_bytes'])} con todo el benchmark. "
            f"{_num(tamanos['despues']['agregados'])} agregados, "
            f"{_num(tamanos['despues']['bytes_por_agregado'])} bytes cada uno con sus indices.",
            "",
            "| Tabla | Filas | Datos | Indices | Total |",
            "|---|---:|---:|---:|---:|",
            *(
                f"| {t['tabla']} | {_num(t['filas'])} | {_mib(t['datos'])} | "
                f"{_mib(t['indices'])} | {_mib(t['total'])} |"
                for t in tamanos["despues"]["tablas"]
            ),
            "",
        ]
    api = reporte.get("api")
    if api:
        lineas += [
            "## La API sobre XL-A",
            "",
            f"{api['repeticiones']} repeticiones por consulta, con la serializacion.",
            "",
            "| Consulta | Mediana ms | p95 ms | Maximo ms | Sentencias | ms en PostgreSQL | "
            "Indices |",
            "|---|---:|---:|---:|---:|---:|---|",
            *(
                f"| {nombre} | {t['mediana_ms']} | {t['p95_ms']} | {t['maximo_ms']} | "
                f"{api['sentencias'][nombre]} | {api['milisegundos_en_postgresql'][nombre]} | "
                f"{', '.join(api['indices_usados'][nombre])} |"
                for nombre, t in api["tiempos"].items()
            ),
            "",
            "Seq Scan sobre una tabla grande: "
            + (json.dumps(api["seq_scan_sobre_tablas_grandes"]) or "ninguno")
            + ".",
            "",
        ]
    sensibilidad = reporte.get("work_mem")
    if sensibilidad and "xl_lifecycle" in reporte:
        base, alto = reporte["xl_lifecycle"], sensibilidad
        fases = sorted(set(base["fases"]) | set(alto["fases"]), key=list(base["fases"]).index)
        lineas += [
            f"## Sensibilidad a work_mem (XL-B, {alto['work_mem']})",
            "",
            f"XL-B con work_mem {reporte['configuracion']['work_mem']}: {base['segundos']} s; con "
            f"{alto['work_mem']}: {alto['segundos']} s. Lo publicado es lo mismo: hash "
            f"`{(base['hash_agregados'] or '')[:16]}...` y "
            f"`{(alto['hash_agregados'] or '')[:16]}...`.",
            "",
            f"| Fase | {reporte['configuracion']['work_mem']} | {alto['work_mem']} |",
            "|---|---:|---:|",
            *(f"| {f} | {base['fases'].get(f)} | {alto['fases'].get(f)} |" for f in fases),
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
    for clave in ("xl_operacion", "xl_sin_operacion", "xl_lifecycle", "api", "work_mem"):
        if clave in bitacora:
            reporte[clave] = bitacora[clave]
    historico = [bitacora[k] for k in sorted(bitacora.etapas) if k.startswith("historico_")]
    if historico:
        reporte["historico"] = historico
    if "tamanos" in bitacora:
        reporte["tamanos"] = bitacora["tamanos"]
    return reporte


def main(argumentos: argparse.Namespace) -> None:
    _exigir_bench()
    destino = Path(argumentos.destino)
    destino.mkdir(parents=True, exist_ok=True)
    bitacora = Bitacora(destino / "benchmark_analytics.etapas.json", argumentos.reanudar)
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
                "base_bytes": _sql("SELECT pg_database_size(current_database())")[0][0],
            },
        )
        print("inventario anotado", flush=True)
    todas = "MOTOR_PAGOS,ATRIBUCION,EVALUACION_PROMESAS,DECISION_V2,GEOGRAFIA,TERRITORIAL_V2,"
    todas += "CAMPO_V2,RUTEO_V2"
    if "xl_operacion" not in bitacora:
        resultado = _hijo(
            "--etapa",
            "analizar",
            "--corte",
            CORTE_OPERACION.isoformat(),
            "--as-of",
            AS_OF_OPERACION.isoformat(),
            "--granularidad",
            "DIA",
            "--familias",
            todas,
        )
        bitacora.anotar("xl_operacion", resultado)
        print(f"XL-A: {resultado['segundos']} s", flush=True)
    if "xl_sin_operacion" not in bitacora:
        resultado = _hijo(
            "--etapa",
            "analizar",
            "--corte",
            CORTE_OPERACION.isoformat(),
            "--as-of",
            AS_OF_OPERACION.isoformat(),
            "--granularidad",
            "DIA",
            "--familias",
            "MOTOR_PAGOS,ATRIBUCION",
        )
        bitacora.anotar("xl_sin_operacion", resultado)
        print(f"XL-A sin operacion: {resultado['segundos']} s", flush=True)
    if "xl_lifecycle" not in bitacora:
        resultado = _hijo(
            "--etapa",
            "analizar",
            "--corte",
            CORTE_LIFECYCLE.isoformat(),
            "--as-of",
            AS_OF_LIFECYCLE.isoformat(),
            "--granularidad",
            "SEMANA_ISO",
            "--familias",
            todas,
        )
        bitacora.anotar("xl_lifecycle", resultado)
        print(f"XL-B: {resultado['segundos']} s", flush=True)
    from motor_cartera.config import config

    semanales = [
        date.fromisoformat(f)
        for (f,) in _sql(
            "SELECT CAST(fecha_corte AS text) FROM corte_canonico WHERE despacho_id = :d "
            "AND cartera_id = :c AND fecha_corte < :o ORDER BY fecha_corte",
            {"d": config.despacho_id, "c": config.cartera_id, "o": CORTE_OPERACION},
        )
    ]
    for corte in semanales:
        clave = f"historico_{corte.isoformat()}"
        if clave in bitacora:
            continue
        as_of = corte + timedelta(days=HORIZONTE_HISTORICO - 1)
        resultado = _hijo(
            "--etapa",
            "analizar",
            "--corte",
            corte.isoformat(),
            "--as-of",
            as_of.isoformat(),
            "--granularidad",
            "MES",
            "--familias",
            "MOTOR_PAGOS,ATRIBUCION",
        )
        bitacora.anotar(clave, resultado)
        print(f"historico {corte}: {resultado['segundos']} s", flush=True)
    if "tamanos" not in bitacora:
        segundos = _vacuum(TABLAS_ANALYTICS)
        despues = _tamanos_analytics()
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
        resultado = _hijo(
            "--etapa",
            "consultar",
            "--destino",
            str(destino),
            "--ejecucion",
            bitacora["xl_operacion"]["analytics_run_id"],
        )
        bitacora.anotar("api", resultado)
        print("API medida", flush=True)
    if "work_mem" not in bitacora:
        resultado = _hijo(
            "--etapa",
            "analizar",
            "--corte",
            CORTE_LIFECYCLE.isoformat(),
            "--as-of",
            AS_OF_LIFECYCLE.isoformat(),
            "--granularidad",
            "SEMANA_ISO",
            "--familias",
            todas,
            "--work-mem",
            "256MB",
        )
        bitacora.anotar("work_mem", resultado)
        print(f"XL-B con work_mem 256MB: {resultado['segundos']} s", flush=True)
    reporte = _reporte(bitacora)
    (destino / "benchmark_analytics.json").write_text(
        json.dumps(reporte, indent=1, ensure_ascii=False, default=str), encoding="utf-8"
    )
    (destino / "benchmark_analytics.md").write_text(_markdown(reporte), encoding="utf-8")
    print(f"Escrito: {destino / 'benchmark_analytics.md'}")


if __name__ == "__main__":
    argumentos_ = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    argumentos_.add_argument("--destino", default="datos/benchmark_analytics")
    argumentos_.add_argument("--reanudar", action="store_true")
    argumentos_.add_argument("--etapa", choices=("analizar", "consultar"), help=argparse.SUPPRESS)
    argumentos_.add_argument("--corte", help=argparse.SUPPRESS)
    argumentos_.add_argument("--as-of", help=argparse.SUPPRESS)
    argumentos_.add_argument("--granularidad", help=argparse.SUPPRESS)
    argumentos_.add_argument("--familias", default="", help=argparse.SUPPRESS)
    argumentos_.add_argument("--ejecucion", help=argparse.SUPPRESS)
    argumentos_.add_argument("--work-mem", help=argparse.SUPPRESS)
    leidos = argumentos_.parse_args()
    if leidos.etapa == "analizar":
        print(
            json.dumps(
                analizar(
                    leidos.corte,
                    leidos.as_of,
                    leidos.granularidad,
                    leidos.familias,
                    leidos.work_mem,
                ),
                default=str,
            )
        )
    elif leidos.etapa == "consultar":
        print(json.dumps(consultar(Path(leidos.destino), leidos.ejecucion), default=str))
    else:
        main(leidos)
