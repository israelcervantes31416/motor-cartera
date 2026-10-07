"""Benchmark del modelo historico: cuanto tarda materializar la historia de muchos cortes grandes y
cuanto tardan las consultas de una cuenta, contra PostgreSQL. No corre en el CI: se pide a mano.

    python scripts/benchmark_historia.py --perfil XL --cortes 12
    python scripts/benchmark_historia.py --perfil M --cortes 12 --formato csv

Lo que hace, cada etapa en su propio proceso para que su memoria pico sea solo suya:

1. genera un escenario longitudinal (generar-escenario) de `--cortes` cortes del perfil, con los
   pagos de cada periodo, sin la hoja CARRIER, que no cambia nada de la historia;
2. ingiere cada corte (cartera/v2) y cada archivo de pagos (pagos/v1), como cualquier fuente
   oficial: su dataset conformado y su historia en la cola;
3. materializa la historia de cada dataset, los cortes en orden de fecha y despues los pagos, y mide
   cada una por separado: tiempo, filas por segundo, memoria pico y sus fases;
4. corre VACUUM ANALYZE, como lo haria autovacuum, y mide el tamano de cada tabla e indice;
5. mide las consultas de una cuenta (buscarla por CLIENTE_UNICO, su Cuenta 360, la primera pagina de
   su historia, de sus eventos y de sus pagos, y el ultimo corte), y guarda el plan de cada una con
   EXPLAIN (ANALYZE, BUFFERS), tal como las emite el servicio.

Necesita una PostgreSQL en MC_DATABASE_URL cuya base se llame *_bench: se le aplican las
migraciones, se vacia y se le escriben millones de filas. El almacen de artefactos es
MC_SOURCE_STORE_ROOT, o un directorio temporal si no se define. Escribe un JSON con todo, los planes
en texto y una tabla en Markdown para la documentacion.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from benchmark_escala import _preparar_base, memoria_pico_mb  # noqa: E402

PRIMER_CORTE = date(2026, 1, 7)
REPETICIONES = 25


# --- las etapas, cada una en su proceso -----------------------------------------------------------


def generar(perfil: str, cortes: int, formato: str, destino: Path, semilla: int) -> dict:
    from motor_cartera.generador.oficial import PERFILES, generar_escenario

    inicio = time.perf_counter()
    escenario = generar_escenario(
        destino,
        cuentas=PERFILES[perfil],
        cortes=cortes,
        primer_corte=PRIMER_CORTE,
        semilla=semilla,
        formato=formato,
        con_carrier=False,
    )
    return {
        "segundos": round(time.perf_counter() - inicio, 3),
        "manifiesto": escenario.manifiesto,
        "memoria_pico_mb": memoria_pico_mb(),
    }


def ingerir(ruta: Path, fecha_corte: str | None) -> dict:
    from sqlmodel import select

    from motor_cartera.config import config
    from motor_cartera.db.modelos import Corrida, IngestaPagos
    from motor_cartera.db.sesion import sesion
    from motor_cartera.fuentes.artefactos import almacen_de, guardar_artefacto
    from motor_cartera.ingesta.cartera_v2 import juzgar_y_publicar
    from motor_cartera.ingesta.corridas import abrir_corrida
    from motor_cartera.ingesta.pagos import abrir_ingesta_pagos, juzgar_y_aceptar

    inicio = time.perf_counter()
    with ruta.open("rb") as archivo:
        guardado = guardar_artefacto(almacen_de(config), archivo, ruta.name)
    with sesion() as s:
        if fecha_corte is not None:
            abierta = abrir_corrida(
                s,
                origen=ruta.name,
                guardado=guardado,
                contrato="cartera/v2",
                fecha_corte=date.fromisoformat(fecha_corte),
            )
            recurso = s.exec(
                select(Corrida).where(Corrida.id == abierta.id).with_for_update()
            ).one()
            juzgar_y_publicar(s, recurso, config=config)
        else:
            abierta = abrir_ingesta_pagos(s, origen=ruta.name, guardado=guardado)
            recurso = s.exec(
                select(IngestaPagos).where(IngestaPagos.id == abierta.id).with_for_update()
            ).one()
            juzgar_y_aceptar(s, recurso, config=config)
        s.commit()
        return {
            "estado": str(recurso.estado),
            "filas": recurso.filas_validas,
            "segundos": round(time.perf_counter() - inicio, 3),
            "memoria_pico_mb": memoria_pico_mb(),
        }


def historiar(ejecucion_id: int) -> dict:
    from motor_cartera.db.modelos import EjecucionHistoria
    from motor_cartera.db.sesion import sesion
    from motor_cartera.historia.ejecuciones import materializar
    from motor_cartera.ingesta.fuente_oficial import Cronometro

    cronometro = Cronometro()
    inicio = time.perf_counter()
    materializar(ejecucion_id, cronometro=cronometro)
    segundos = time.perf_counter() - inicio
    with sesion() as s:
        ejecucion = s.get_one(EjecucionHistoria, ejecucion_id)
        return {
            "estado": str(ejecucion.estado),
            "resultado": ejecucion.resultado,
            "tipo": str(ejecucion.tipo_fuente),
            "registros": ejecucion.registros_publicados,
            "segundos": round(segundos, 3),
            "filas_por_segundo": round(ejecucion.registros_publicados / segundos)
            if segundos
            else None,
            "fases": {nombre: round(s_, 3) for nombre, s_ in cronometro.fases.items()},
            "memoria_pico_mb": memoria_pico_mb(),
            "detalle": ejecucion.detalle,
        }


def consultar(destino: Path) -> dict:
    """Las consultas de una cuenta, con su tiempo y su plan."""
    from sqlalchemy import event, func
    from sqlmodel import select

    from motor_cartera.db.modelos import (
        CorteCanonico,
        CuentaCanonica,
        PagoObservado,
        SnapshotCuenta,
    )
    from motor_cartera.db.sesion import crear_motor, sesion
    from motor_cartera.historia import cuenta360

    motor = crear_motor()
    with sesion() as s:
        ultimo = s.exec(select(func.max(CorteCanonico.fecha_corte))).one()
        primero = s.exec(select(func.min(CorteCanonico.fecha_corte))).one()
        cortes = s.exec(select(func.count()).select_from(CorteCanonico)).one()
        # Una cuenta en todos los cortes, una que salio y la que mas pagos observados tiene.
        en_todos = s.exec(
            select(CuentaCanonica.cliente_unico)
            .join(SnapshotCuenta, SnapshotCuenta.cuenta_canonica_id == CuentaCanonica.id)
            .group_by(CuentaCanonica.cliente_unico)
            .having(func.count() == cortes)
            .limit(1)
        ).one()
        salio = s.exec(
            select(CuentaCanonica.cliente_unico)
            .join(SnapshotCuenta, SnapshotCuenta.cuenta_canonica_id == CuentaCanonica.id)
            .group_by(CuentaCanonica.cliente_unico)
            .having(func.max(SnapshotCuenta.fecha_corte) < ultimo)
            .limit(1)
        ).one()
        pagador = s.exec(
            select(PagoObservado.cliente_unico)
            .group_by(PagoObservado.cliente_unico)
            .order_by(func.count().desc())
            .limit(1)
        ).one()
    elegidas = {"en_todos_los_cortes": en_todos, "salio": salio, "mas_pagos": pagador}

    def medir(funcion) -> dict:
        tiempos = []
        for _ in range(REPETICIONES):
            with sesion() as s:
                inicio = time.perf_counter()
                funcion(s)
                tiempos.append((time.perf_counter() - inicio) * 1000)
        tiempos.sort()
        return {
            "mediana_ms": round(statistics.median(tiempos), 2),
            "p95_ms": round(tiempos[int(len(tiempos) * 0.95) - 1], 2),
            "maximo_ms": round(tiempos[-1], 2),
        }

    def cuenta(s, cliente):
        return cuenta360.buscar(s, "DSP_001", "CARTERA_PRINCIPAL", cliente)

    consultas = {
        "buscar_por_cliente_unico": lambda s, c: cuenta(s, c),
        "cuenta_360": lambda s, c: cuenta360.resumen(s, cuenta(s, c)),
        "cuenta_360_en_una_fecha": lambda s, c: cuenta360.resumen(s, cuenta(s, c), al=primero),
        "historia_primera_pagina": lambda s, c: cuenta360.historia(
            s, cuenta(s, c), desplazamiento=0, limite=50
        ),
        "eventos": lambda s, c: cuenta360.eventos(s, cuenta(s, c)),
        "pagos_primera_pagina": lambda s, c: cuenta360.pagos_observados(
            s, cuenta(s, c), desplazamiento=0, limite=50
        ),
        "ultimo_corte": lambda s, c: cuenta360.listar_cortes(
            s, "DSP_001", "CARTERA_PRINCIPAL", desplazamiento=0, limite=50
        ),
    }
    tiempos = {
        nombre: {
            rol: medir(lambda s, f=funcion, c=cliente: f(s, c)) for rol, cliente in elegidas.items()
        }
        for nombre, funcion in consultas.items()
    }

    # Los planes: cada sentencia que el servicio emite para la cuenta con mas pagos, tal cual, con
    # EXPLAIN (ANALYZE, BUFFERS).
    planes = {}
    for nombre, funcion in consultas.items():
        emitidas: list[tuple[str, object]] = []

        def anotar(conexion, cursor, sentencia, parametros, contexto, varias, emitidas=emitidas):
            emitidas.append((sentencia, parametros))

        event.listen(motor, "before_cursor_execute", anotar)
        try:
            with sesion() as s:
                funcion(s, pagador)
        finally:
            event.remove(motor, "before_cursor_execute", anotar)
        planes[nombre] = []
        with motor.connect() as conexion:
            crudo = conexion.connection.driver_connection
            with crudo.cursor() as cursor:
                for sentencia, parametros in emitidas:
                    cursor.execute("EXPLAIN (ANALYZE, BUFFERS) " + sentencia, parametros)
                    plan = "\n".join(fila[0] for fila in cursor.fetchall())
                    planes[nombre].append({"sql": " ".join(sentencia.split()), "plan": plan})
            conexion.rollback()
    destino.mkdir(parents=True, exist_ok=True)
    with (destino / "planes.txt").open("w", encoding="utf-8") as salida:
        for nombre, sentencias in planes.items():
            for i, sentencia in enumerate(sentencias, start=1):
                salida.write(f"### {nombre} ({i} de {len(sentencias)})\n{sentencia['sql']}\n\n")
                salida.write(sentencia["plan"] + "\n\n")
    secuenciales = {
        nombre: [p["sql"][:120] for p in sentencias if _escanea_grande(p["plan"])]
        for nombre, sentencias in planes.items()
    }
    return {
        "elegidas": elegidas,
        "repeticiones": REPETICIONES,
        "tiempos": tiempos,
        "planes": planes,
        "seq_scan_sobre_tablas_grandes": {k: v for k, v in secuenciales.items() if v},
    }


def _escanea_grande(plan: str) -> bool:
    """Si el plan recorre entera alguna de las tablas que crecen con los cortes."""
    return any(
        f"Seq Scan on {tabla}" in plan
        for tabla in ("snapshot_cuenta", "pago_observado", "cuenta_canonica")
    )


# --- el orquestador -------------------------------------------------------------------------------


def _hijo(*argumentos: str) -> dict:
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


def _sql(consulta: str, parametros: dict | None = None) -> list:
    from sqlalchemy import text

    from motor_cartera.db.sesion import crear_motor

    with crear_motor().connect() as conexion:
        return [tuple(f) for f in conexion.execute(text(consulta), parametros or {})]


def _vacuum() -> float:
    from sqlalchemy import text

    from motor_cartera.db.sesion import crear_motor

    inicio = time.perf_counter()
    with crear_motor().connect().execution_options(isolation_level="AUTOCOMMIT") as conexion:
        for tabla in ("cuenta_canonica", "corte_canonico", "snapshot_cuenta", "pago_observado"):
            conexion.execute(text(f"VACUUM (ANALYZE) {tabla}"))
        segundos = round(time.perf_counter() - inicio, 3)
        # Y las estadisticas de las demas, para que el reporte de tamanos traiga sus filas.
        conexion.execute(text("ANALYZE"))
    return segundos


def _tamanos() -> dict:
    filas = _sql(
        "SELECT c.relname, c.reltuples::bigint, pg_relation_size(c.oid), "
        "pg_indexes_size(c.oid), pg_total_relation_size(c.oid) FROM pg_class c "
        "WHERE c.relkind = 'r' AND c.relnamespace = 'public'::regnamespace "
        "ORDER BY pg_total_relation_size(c.oid) DESC"
    )
    indices = _sql(
        "SELECT i.relname, t.relname, pg_relation_size(i.oid) FROM pg_index x "
        "JOIN pg_class i ON i.oid = x.indexrelid JOIN pg_class t ON t.oid = x.indrelid "
        "WHERE t.relname IN ('cuenta_canonica', 'corte_canonico', 'snapshot_cuenta', "
        "'pago_observado', 'ejecucion_historia') ORDER BY pg_relation_size(i.oid) DESC"
    )
    (base,) = _sql("SELECT pg_database_size(current_database())")
    return {
        "base_bytes": base[0],
        "tablas": [
            {
                "tabla": nombre,
                "filas_estimadas": tuplas,
                "datos_bytes": datos,
                "indices_bytes": indices_,
                "total_bytes": total,
            }
            for nombre, tuplas, datos, indices_, total in filas
        ],
        "indices": [{"indice": i, "tabla": t, "bytes": b} for i, t, b in indices],
    }


def _conteos() -> dict:
    return {
        tabla: _sql(f"SELECT count(*) FROM {tabla}")[0][0]
        for tabla in (
            "cuenta_canonica",
            "corte_canonico",
            "snapshot_cuenta",
            "pago_observado",
            "ejecucion_historia",
            "cuenta",
        )
    }


def _mib(valor: int) -> str:
    return f"{valor / 2**20:,.1f} MiB"


def _markdown(reporte: dict) -> str:
    lineas = [
        "| Dataset | Registros | Resultado | Tiempo | Filas/s | Memoria pico | Fases (s) |",
        "|---|---|---|---|---|---|---|",
    ]
    for fila in reporte["historia"]:
        fases = ", ".join(f"{fase} {s:,.1f}" for fase, s in (fila["fases"] or {}).items())
        lineas.append(
            f"| {fila['dataset']} | {fila['registros']:,} | {fila['resultado']} | "
            f"{fila['segundos']:,.1f} s | {fila['filas_por_segundo'] or 0:,} | "
            f"{fila['memoria_pico_mb'] or 0:,.0f} MiB | {fases} |"
        )
    total = reporte["totales"]
    lineas += [
        "",
        f"Total de la historia: {total['segundos_historia']:,.1f} s para "
        f"{total['registros_historia']:,} registros ({total['filas_por_segundo']:,} filas/s); "
        f"ingesta de las fuentes: {total['segundos_ingesta']:,.1f} s; VACUUM ANALYZE: "
        f"{reporte['vacuum_segundos']:,.1f} s.",
        "",
        "| Tabla | Filas | Datos | Indices | Total |",
        "|---|---|---|---|---|",
    ]
    for tabla in reporte["tamanos"]["tablas"]:
        if tabla["total_bytes"] < 2**20:
            continue
        # El conteo exacto si se hizo; si no, la estimacion de PostgreSQL, marcada.
        exactas = reporte["conteos"].get(tabla["tabla"])
        filas = f"{exactas:,}" if exactas is not None else f"~{tabla['filas_estimadas']:,}"
        lineas.append(
            f"| {tabla['tabla']} | {filas} | {_mib(tabla['datos_bytes'])} | "
            f"{_mib(tabla['indices_bytes'])} | {_mib(tabla['total_bytes'])} |"
        )
    lineas += [
        "",
        f"Base completa: {_mib(reporte['tamanos']['base_bytes'])}.",
        "",
        "| Consulta | En todos los cortes | Salio | Mas pagos |",
        "|---|---|---|---|",
    ]
    for nombre, por_cuenta in reporte["consultas"]["tiempos"].items():
        celdas = " | ".join(
            f"{t['mediana_ms']:,.2f} ms (p95 {t['p95_ms']:,.2f})" for t in por_cuenta.values()
        )
        lineas.append(f"| {nombre} | {celdas} |")
    maquina = reporte["maquina"]
    lineas += [
        "",
        f"Perfil {reporte['perfil']} ({reporte['cuentas_iniciales']:,} cuentas iniciales), "
        f"{reporte['cortes']} cortes en {reporte['formato']}, semilla {reporte['semilla']}. "
        f"{maquina['sistema']}, Python {maquina['python']}, {maquina['cpus']} CPU. Mediana de "
        f"{reporte['consultas']['repeticiones']} repeticiones por consulta.",
    ]
    return "\n".join(lineas)


def main(argumentos: argparse.Namespace) -> None:
    from motor_cartera.generador.oficial import PERFILES

    perfil = argumentos.perfil.upper()
    if perfil not in PERFILES:
        sys.exit(f"Perfil desconocido: {perfil}. Usa {', '.join(PERFILES)}.")
    formato = argumentos.formato.lower().lstrip(".")
    temporal = Path(tempfile.mkdtemp(prefix="motor-cartera-historia-"))
    destino = Path(argumentos.destino) if argumentos.destino else temporal / "escenario"
    os.environ.setdefault("MC_SOURCE_STORE_ROOT", str(temporal / "almacen"))
    _preparar_base()

    print(f"Generando {argumentos.cortes} cortes del perfil {perfil} en {formato}...", flush=True)
    generado = _hijo(
        "generar",
        "--perfil",
        perfil,
        "--cortes",
        str(argumentos.cortes),
        "--formato",
        formato,
        "--destino",
        str(destino),
        "--semilla",
        str(argumentos.semilla),
    )
    manifiesto = generado["manifiesto"]
    print(f"  {generado['segundos']:,.1f} s", flush=True)

    ingestas = []
    for corte in manifiesto["cortes"]:
        print(f"Ingiriendo el corte {corte['fecha_corte']}...", flush=True)
        resultado = _hijo(
            "ingerir", "--ruta", str(destino / corte["archivo"]), "--corte", corte["fecha_corte"]
        )
        ingestas.append({"fuente": corte["archivo"], **resultado})
    for periodo in manifiesto["periodos"]:
        print(f"Ingiriendo los pagos {periodo['archivo']}...", flush=True)
        resultado = _hijo("ingerir", "--ruta", str(destino / periodo["archivo"]))
        ingestas.append({"fuente": periodo["archivo"], **resultado})
    if any(i["estado"] != "EXITOSA" for i in ingestas):
        sys.exit("Alguna ingesta no termino EXITOSA: el benchmark no es valido.")

    ejecuciones = _sql(
        "SELECT e.id, e.tipo_fuente, c.fecha_corte, d.dataset_id FROM ejecucion_historia e "
        "JOIN dataset_conformado d ON d.id = e.dataset_conformado_id "
        "LEFT JOIN corrida c ON c.id = d.corrida_id "
        "ORDER BY e.tipo_fuente, c.fecha_corte, e.id"
    )
    historia = []
    for ejecucion_id, tipo, fecha, dataset_id in ejecuciones:
        nombre = f"cartera {fecha.isoformat()}" if tipo == "CARTERA" else f"pagos {dataset_id}"
        print(f"Materializando la historia de {nombre}...", flush=True)
        resultado = _hijo("historiar", "--ejecucion", str(ejecucion_id))
        historia.append({"dataset": nombre, **resultado})
        print(
            f"  {resultado['resultado']}: {resultado['registros']:,} en "
            f"{resultado['segundos']:,.1f} s ({resultado['filas_por_segundo']:,} filas/s, "
            f"{resultado['memoria_pico_mb'] or 0:,.0f} MiB)",
            flush=True,
        )
    if any(h["estado"] != "EXITOSA" for h in historia):
        sys.exit("Alguna historia no termino EXITOSA: el benchmark no es valido.")

    print("VACUUM ANALYZE...", flush=True)
    vacuum = _vacuum()
    print("Consultas de una cuenta...", flush=True)
    salida = Path(argumentos.salida) if argumentos.salida else destino / "benchmark_historia.json"
    consultas = _hijo("consultar", "--destino", str(salida.parent))
    registros = sum(h["registros"] for h in historia)
    segundos = sum(h["segundos"] for h in historia)
    reporte = {
        "perfil": perfil,
        "cuentas_iniciales": PERFILES[perfil],
        "cortes": len(manifiesto["cortes"]),
        "formato": formato,
        "semilla": argumentos.semilla,
        "generacion": {k: v for k, v in generado.items() if k != "manifiesto"},
        "manifiesto": manifiesto,
        "ingestas": ingestas,
        "historia": historia,
        "totales": {
            "registros_historia": registros,
            "segundos_historia": round(segundos, 3),
            "filas_por_segundo": round(registros / segundos) if segundos else None,
            "segundos_ingesta": round(sum(i["segundos"] for i in ingestas), 3),
            "memoria_pico_historia_mb": max(h["memoria_pico_mb"] or 0 for h in historia),
        },
        "conteos": _conteos(),
        "vacuum_segundos": vacuum,
        "tamanos": _tamanos(),
        "consultas": consultas,
        "maquina": {
            "sistema": f"{platform.system()} {platform.release()}",
            "procesador": platform.processor() or platform.machine(),
            "cpus": os.cpu_count(),
            "python": platform.python_version(),
        },
    }
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps(reporte, indent=2, ensure_ascii=False, default=str), "utf-8")
    texto = _markdown(reporte)
    (salida.parent / "benchmark_historia.md").write_text(texto + "\n", encoding="utf-8")
    print(texto)
    print(f"\nReporte: {salida}; planes: {salida.parent / 'planes.txt'}")
    if consultas["seq_scan_sobre_tablas_grandes"]:
        sys.exit(
            "Alguna consulta de una cuenta recorre entera una tabla grande: "
            f"{consultas['seq_scan_sobre_tablas_grandes']}"
        )


if __name__ == "__main__":
    lector = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    etapas = lector.add_subparsers(dest="etapa")
    de_generar = etapas.add_parser("generar")
    de_generar.add_argument("--perfil", required=True)
    de_generar.add_argument("--cortes", required=True, type=int)
    de_generar.add_argument("--formato", required=True)
    de_generar.add_argument("--destino", required=True, type=Path)
    de_generar.add_argument("--semilla", required=True, type=int)
    de_ingerir = etapas.add_parser("ingerir")
    de_ingerir.add_argument("--ruta", required=True, type=Path)
    de_ingerir.add_argument("--corte")
    etapas.add_parser("historiar").add_argument("--ejecucion", required=True, type=int)
    etapas.add_parser("consultar").add_argument("--destino", required=True, type=Path)
    lector.add_argument("--perfil", default="XL", help="XS, S, M, L, XL (por omision) o XXL.")
    lector.add_argument("--cortes", type=int, default=12, help="Cuantos cortes; 12 por omision.")
    lector.add_argument("--formato", default="zip", help="zip (por omision) o csv.")
    lector.add_argument("--destino", help="Donde se escriben los archivos generados.")
    lector.add_argument("--salida", help="El reporte JSON; por omision, junto a los archivos.")
    lector.add_argument("--semilla", type=int, default=31416)
    leidos = lector.parse_args()
    if leidos.etapa == "generar":
        generado = generar(
            leidos.perfil, leidos.cortes, leidos.formato, leidos.destino, leidos.semilla
        )
        print(json.dumps(generado))
    elif leidos.etapa == "ingerir":
        print(json.dumps(ingerir(leidos.ruta, leidos.corte)))
    elif leidos.etapa == "historiar":
        print(json.dumps(historiar(leidos.ejecucion)))
    elif leidos.etapa == "consultar":
        print(json.dumps(consultar(leidos.destino), default=str))
    else:
        main(leidos)
