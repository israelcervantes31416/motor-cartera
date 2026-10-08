"""Benchmark del motor de pagos: cuanto tarda interpretar los pagos observados de muchos cortes
grandes, cuanto crece la base y cuanto tardan las consultas de una cuenta y de un movimiento, contra
PostgreSQL. No corre en el CI: se pide a mano.

    python scripts/benchmark_motor_pagos.py --perfil XL --cortes 12
    python scripts/benchmark_motor_pagos.py --perfil M --cortes 6 --formato csv

Lo que hace, cada etapa en su propio proceso para que su memoria pico sea solo suya:

1. genera el escenario longitudinal, como benchmark_historia (o reusa el que ya este en --destino);
2. ingiere cada corte y cada archivo de pagos y materializa su historia: la de cada archivo de pagos
   abre, en su misma transaccion, la interpretacion de las ventanas que toca;
3. corre VACUUM ANALYZE y mide el tamano de la base antes del motor;
4. interpreta cada ventana y mide su tiempo, sus observaciones por segundo, su memoria pico, sus
   fases y lo que encontro (movimientos, duplicados, ambiguos, reversos, sin cuenta);
5. corre VACUUM ANALYZE y mide otra vez: lo que agrega el motor a la base;
6. llegan tres archivos tarde: uno con pagos de la ultima ventana, otro con el reverso de pagos
   que ya se interpretaron, y otro con pagos del mes siguiente; mide la historia de cada uno y la
   interpretacion de las ventanas que abre;
7. mide las consultas de una cuenta y de un movimiento (sus movimientos, sus observaciones, los
   grupos de una huella, un intervalo y el resumen de pagos), y guarda el plan de cada sentencia con
   EXPLAIN (ANALYZE, BUFFERS), tal como las emite el servicio.

Necesita una PostgreSQL en MC_DATABASE_URL cuya base se llame *_bench. El almacen de artefactos
es MC_SOURCE_STORE_ROOT, o un directorio temporal si no se define. Escribe un JSON con todo, los
planes en texto y una tabla en Markdown para la documentacion.
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
from datetime import date, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from benchmark_escala import _preparar_base, memoria_pico_mb  # noqa: E402
from benchmark_historia import generar, historiar, ingerir  # noqa: E402

REPETICIONES = 25
TABLAS_GRANDES = (
    "pago_observado",
    "resultado_pago_observado",
    "movimiento_economico_canonico",
    "snapshot_cuenta",
    "cuenta_canonica",
)
TABLAS_DEL_MOTOR = (
    "ejecucion_motor_pagos",
    "resultado_pago_observado",
    "movimiento_economico_canonico",
)


# --- las etapas, cada una en su proceso -----------------------------------------------------------


def interpretar_ventana(ejecucion_id: int) -> dict:
    from sqlalchemy import text

    from motor_cartera.db.modelos import EjecucionMotorPagos
    from motor_cartera.db.sesion import sesion
    from motor_cartera.ingesta.fuente_oficial import Cronometro
    from motor_cartera.motor_pagos.ejecuciones import interpretar

    cronometro = Cronometro()
    inicio = time.perf_counter()
    interpretar(ejecucion_id, cronometro=cronometro)
    segundos = time.perf_counter() - inicio
    with sesion() as s:
        e = s.get_one(EjecucionMotorPagos, ejecucion_id)
        # Por que: cuantas observaciones de cada clase con cada motivo. Fuera del tiempo medido.
        motivos = [
            {"clasificacion": clase, "motivo": motivo, "observaciones": n}
            for clase, motivo, n in s.exec(
                text(
                    "SELECT r.clasificacion, m ->> 'codigo', count(*) "
                    "FROM resultado_pago_observado r "
                    "CROSS JOIN LATERAL jsonb_array_elements(r.motivos) m "
                    "WHERE r.ejecucion_motor_pagos_id = :id GROUP BY 1, 2 ORDER BY 1, 2"
                ),
                params={"id": ejecucion_id},
            )
        ]
        return {
            "periodo": f"{e.periodo_desde:%Y-%m}",
            "estado": str(e.estado),
            "resultado": e.resultado,
            "observaciones": e.observaciones_leidas,
            "contexto": e.observaciones_contexto,
            "movimientos": e.movimientos_canonicos,
            "primarios": e.primarios,
            "duplicados_exactos": e.duplicados_exactos,
            "coincidencias_ambiguas": e.coincidencias_ambiguas,
            "reversos": e.reversos,
            "posibles_reversos": e.posibles_reversos,
            "no_conciliados": e.no_conciliados,
            "sin_cuenta_observada": e.sin_cuenta_observada,
            "grupos_legacy": e.grupos_legacy,
            "grupos_ambiguos": e.grupos_ambiguos,
            "pagos_anulados": e.pagos_anulados,
            "bruta": str(e.recuperacion_bruta_interpretada),
            "neta": str(e.recuperacion_neta_interpretada),
            "segundos": round(segundos, 3),
            "observaciones_por_segundo": round(e.observaciones_leidas / segundos)
            if segundos
            else None,
            "fases": {nombre: round(s_, 3) for nombre, s_ in cronometro.fases.items()},
            "memoria_pico_mb": memoria_pico_mb(),
            "motivos": motivos,
        }


def tardio(
    destino: Path, origen: Path, desde_fila: int, cuantas: int, dias: int, reversos: bool
) -> dict:
    """Un archivo de pagos que llega tarde: `cuantas` filas de `origen` a partir de `desde_fila`,
    recibidas `dias` despues (y un segundo mas, para que sean pagos nuevos y no copias). Con
    `reversos`, solo las de importe positivo, con el importe en negativo: el reverso de cada pago,
    que el motor tiene que emparejar con el pago que ya interpreto. Cada llegada usa otras filas:
    un pago con dos copias recibidas en otro momento ya no tendria un solo original posible."""
    import pandas as pd

    from motor_cartera.contratos.pagos import CONTRATO_PAGOS
    from motor_cartera.generador.oficial import escribir_pagos

    def columna(prefijo: str) -> str:
        (nombre,) = [n for n in CONTRATO_PAGOS.nombres if n.startswith(prefijo)]
        return nombre

    recepcion, importe = columna("Fecha_Recepci"), columna("Recuperaci")
    tabla = pd.read_csv(origen, dtype=str, keep_default_na=False).iloc[desde_fila:]
    if reversos:
        tabla = tabla[~tabla[importe].str.startswith("-")].head(cuantas).copy()
        tabla[importe] = "-" + tabla[importe]
        tabla[columna("Concepto_C")] = "AJUSTE"
    else:
        tabla = tabla.head(cuantas).copy()
    instantes = pd.to_datetime(tabla[recepcion]) + timedelta(days=dias, seconds=1)
    tabla[recepcion] = instantes.dt.strftime("%Y-%m-%d %H:%M:%S")
    escrito = escribir_pagos(tabla.replace("", None), destino)
    return {"ruta": str(escrito.ruta), "filas": escrito.filas}


def consultar(destino: Path) -> dict:
    """Las consultas de una cuenta y de un movimiento, con su tiempo y su plan."""
    from sqlalchemy import and_, event, func
    from sqlmodel import select

    from motor_cartera.db.modelos import (
        CuentaCanonica,
        EjecucionMotorPagos,
        MovimientoEconomicoCanonico,
        PagoObservado,
        ResultadoPagoObservado,
    )
    from motor_cartera.db.sesion import crear_motor, sesion_de_lectura
    from motor_cartera.historia import cuenta360
    from motor_cartera.motor_pagos import consultas
    from motor_cartera.motor_pagos.reglas import VERSION_MOTOR_PAGOS, Clasificacion

    version = VERSION_MOTOR_PAGOS
    motor = crear_motor()
    with sesion_de_lectura() as s:
        vigentes = consultas.vigentes(s, version)
        conciliados = (
            MovimientoEconomicoCanonico.ejecucion_motor_pagos_id.in_(vigentes),
            MovimientoEconomicoCanonico.cuenta_canonica_id.is_not(None),
        )
        # La cuenta con mas movimientos, y un movimiento con una copia exacta, de otra cuenta.
        pagador = s.exec(
            select(MovimientoEconomicoCanonico.cuenta_canonica_id)
            .where(*conciliados)
            .group_by(MovimientoEconomicoCanonico.cuenta_canonica_id)
            .order_by(func.count().desc(), MovimientoEconomicoCanonico.cuenta_canonica_id)
            .limit(1)
        ).one()
        doble = s.exec(
            select(MovimientoEconomicoCanonico)
            .where(*conciliados, MovimientoEconomicoCanonico.observaciones == 2)
            .order_by(MovimientoEconomicoCanonico.id)
            .limit(1)
        ).one()
        cuenta_id = s.get_one(CuentaCanonica, pagador).cuenta_id
        cuenta_doble_id = s.get_one(CuentaCanonica, doble.cuenta_canonica_id).cuenta_id
        copia = s.exec(
            select(ResultadoPagoObservado, PagoObservado.cliente_unico)
            .join(
                PagoObservado,
                and_(
                    PagoObservado.dataset_conformado_id
                    == ResultadoPagoObservado.dataset_conformado_id,
                    PagoObservado.source_row == ResultadoPagoObservado.source_row,
                ),
            )
            .where(ResultadoPagoObservado.movimiento_economico_canonico_id == doble.id)
            .limit(1)
        ).one()
        # Un grupo de la llave historica que no se interpreto; si no hay, el de la copia.
        ambigua = (
            s.exec(
                select(ResultadoPagoObservado, PagoObservado.cliente_unico)
                .join(
                    PagoObservado,
                    and_(
                        PagoObservado.dataset_conformado_id
                        == ResultadoPagoObservado.dataset_conformado_id,
                        PagoObservado.source_row == ResultadoPagoObservado.source_row,
                    ),
                )
                .where(
                    ResultadoPagoObservado.ejecucion_motor_pagos_id.in_(vigentes),
                    ResultadoPagoObservado.clasificacion
                    == Clasificacion.COINCIDENCIA_AMBIGUA.value,
                )
                .limit(1)
            ).first()
            or copia
        )
        run_de_la_copia = s.get_one(
            EjecucionMotorPagos, copia[0].ejecucion_motor_pagos_id
        ).motor_pagos_run_id
        run_de_la_ambigua = s.get_one(
            EjecucionMotorPagos, ambigua[0].ejecucion_motor_pagos_id
        ).motor_pagos_run_id
        dia = doble.fecha_recepcion.date()
    elegidos = {
        "cuenta_con_mas_movimientos": str(cuenta_id),
        "movimiento_con_copia": str(doble.movimiento_id),
        "cuenta_del_movimiento_con_copia": str(cuenta_doble_id),
        "grupo_legacy": ambigua[0].firma_legacy.hex(),
        "grupo_legacy_es_ambiguo": ambigua is not copia,
    }

    # Cada una como la arma su ruta: la cuenta por su cuenta_id, la ejecucion por su run_id.
    def movimientos(s):
        return consultas.movimientos_de_cuenta(
            s,
            cuenta360.obtener(s, cuenta_id),
            version=version,
            desde=None,
            hasta=None,
            desplazamiento=0,
            limite=50,
        )

    def detalle(s):
        return consultas.detalle(s, consultas.obtener_movimiento(s, doble.movimiento_id, version))

    def observaciones(s):
        visto = consultas.obtener_movimiento(s, doble.movimiento_id, version)
        return consultas.observaciones_de(s, visto, desplazamiento=0, limite=50)

    def por_firma_exacta(s):
        return consultas.resultados_de(
            s,
            consultas.obtener_ejecucion(s, run_de_la_copia).ejecucion,
            clasificacion=None,
            cliente_unico=copia[1],
            firma_exacta=copia[0].firma_exacta,
            desplazamiento=0,
            limite=50,
        )

    def grupo_legacy(s):
        return consultas.resultados_de(
            s,
            consultas.obtener_ejecucion(s, run_de_la_ambigua).ejecucion,
            clasificacion=None,
            cliente_unico=ambigua[1],
            firma_legacy=ambigua[0].firma_legacy,
            desplazamiento=0,
            limite=50,
        )

    def intervalo(s):
        return consultas.movimientos_de_cuenta(
            s,
            cuenta360.obtener(s, cuenta_doble_id),
            version=version,
            desde=dia - timedelta(days=7),
            hasta=dia,
            desplazamiento=0,
            limite=50,
        )

    def resumen(s):
        return consultas.resumen_de_cuenta(s, cuenta360.obtener(s, cuenta_id), version=version)

    def cuenta_360(s):
        cuenta = cuenta360.obtener(s, cuenta_id)
        return cuenta360.resumen(s, cuenta), consultas.resumen_de_cuenta(s, cuenta, version=version)

    def grupo_legacy_sin_cliente(s):
        return consultas.resultados_de(
            s,
            consultas.obtener_ejecucion(s, run_de_la_ambigua).ejecucion,
            clasificacion=None,
            cliente_unico=None,
            firma_legacy=ambigua[0].firma_legacy,
            desplazamiento=0,
            limite=50,
        )

    def primera_pagina(s):
        return consultas.listar_movimientos(
            s,
            despacho_id="DSP_001",
            cartera_id="CARTERA_PRINCIPAL",
            version=version,
            desplazamiento=0,
            limite=50,
        )

    def del_dia(s):
        return consultas.listar_movimientos(
            s,
            despacho_id="DSP_001",
            cartera_id="CARTERA_PRINCIPAL",
            version=version,
            desde=dia,
            hasta=dia,
            tipo="POSIBLE_REVERSO",
            desplazamiento=0,
            limite=50,
        )

    de_una_cuenta = {
        "movimientos_de_una_cuenta": movimientos,
        "detalle_de_un_movimiento": detalle,
        "observaciones_de_un_movimiento": observaciones,
        "grupo_por_firma_exacta": por_firma_exacta,
        "grupo_legacy": grupo_legacy,
        "movimientos_en_un_intervalo": intervalo,
        "resumen_de_pagos_de_una_cuenta": resumen,
        "cuenta_360_con_resumen_de_pagos": cuenta_360,
    }
    # De una ventana o de toda la cartera, no de una cuenta: se miden para saber cuanto cuestan.
    globales = {
        "grupo_legacy_sin_cliente": grupo_legacy_sin_cliente,
        "primera_pagina_de_movimientos_de_la_cartera": primera_pagina,
        "posibles_reversos_de_un_dia_en_la_cartera": del_dia,
    }

    def medir(funcion) -> dict:
        tiempos = []
        for _ in range(REPETICIONES):
            with sesion_de_lectura() as s:
                inicio = time.perf_counter()
                funcion(s)
                tiempos.append((time.perf_counter() - inicio) * 1000)
        tiempos.sort()
        return {
            "mediana_ms": round(statistics.median(tiempos), 2),
            "p95_ms": round(tiempos[int(len(tiempos) * 0.95) - 1], 2),
            "maximo_ms": round(tiempos[-1], 2),
        }

    todas = {**de_una_cuenta, **globales}
    tiempos = {nombre: medir(funcion) for nombre, funcion in todas.items()}

    planes = {}
    for nombre, funcion in todas.items():
        emitidas: list[tuple[str, object]] = []

        def anotar(conexion, cursor, sentencia, parametros, contexto, varias, emitidas=emitidas):
            emitidas.append((sentencia, parametros))

        event.listen(motor, "before_cursor_execute", anotar)
        try:
            with sesion_de_lectura() as s:
                funcion(s)
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
    with (destino / "planes_motor_pagos.txt").open("w", encoding="utf-8") as salida:
        for nombre, sentencias in planes.items():
            for i, sentencia in enumerate(sentencias, start=1):
                salida.write(f"### {nombre} ({i} de {len(sentencias)})\n{sentencia['sql']}\n\n")
                salida.write(sentencia["plan"] + "\n\n")
    secuenciales = {
        nombre: [p["sql"][:140] for p in planes[nombre] if _escanea_grande(p["plan"])]
        for nombre in de_una_cuenta
    }
    return {
        "elegidos": elegidos,
        "repeticiones": REPETICIONES,
        "tiempos": tiempos,
        "planes": planes,
        "seq_scan_sobre_tablas_grandes": {k: v for k, v in secuenciales.items() if v},
        "seq_scan_en_consultas_globales": {
            nombre: [p["sql"][:140] for p in planes[nombre] if _escanea_grande(p["plan"])]
            for nombre in globales
        },
    }


def _escanea_grande(plan: str) -> bool:
    return any(f"Seq Scan on {tabla}" in plan for tabla in TABLAS_GRANDES)


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


def _vacuum(tablas: tuple[str, ...]) -> float:
    from sqlalchemy import text

    from motor_cartera.db.sesion import crear_motor

    inicio = time.perf_counter()
    with crear_motor().connect().execution_options(isolation_level="AUTOCOMMIT") as conexion:
        for tabla in tablas:
            conexion.execute(text(f"VACUUM (ANALYZE) {tabla}"))
        segundos = round(time.perf_counter() - inicio, 3)
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
        "WHERE t.relname IN ('pago_observado', 'ejecucion_motor_pagos', "
        "'resultado_pago_observado', 'movimiento_economico_canonico') "
        "ORDER BY pg_relation_size(i.oid) DESC"
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


def _pendientes() -> list[tuple[int, str]]:
    return _sql(
        "SELECT id, to_char(periodo_desde, 'YYYY-MM') FROM ejecucion_motor_pagos "
        "WHERE estado = 'EN_PROCESO' ORDER BY id"
    )


def _interpretar_pendientes(etiqueta: str) -> list[dict]:
    hechas = []
    for ejecucion_id, periodo in _pendientes():
        print(f"Interpretando la ventana {periodo} ({etiqueta})...", flush=True)
        resultado = _hijo("interpretar", "--ejecucion", str(ejecucion_id))
        hechas.append(resultado)
        print(
            f"  {resultado['resultado']}: {resultado['observaciones']:,} observaciones en "
            f"{resultado['segundos']:,.1f} s ({resultado['observaciones_por_segundo']:,}/s, "
            f"{resultado['memoria_pico_mb'] or 0:,.0f} MiB)",
            flush=True,
        )
    return hechas


def _mib(valor: int) -> str:
    return f"{valor / 2**20:,.1f} MiB"


def _markdown(reporte: dict) -> str:
    lineas = [
        "| Ventana | Observaciones | Contexto | Movimientos | Duplicados | Ambiguas | Reversos | "
        "Posibles | Tiempo | Obs./s | Memoria pico |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for v in reporte["motor"]:
        lineas.append(
            f"| {v['periodo']} | {v['observaciones']:,} | {v['contexto']:,} | "
            f"{v['movimientos']:,} | {v['duplicados_exactos']:,} | "
            f"{v['coincidencias_ambiguas']:,} | {v['reversos']:,} | {v['posibles_reversos']:,} | "
            f"{v['segundos']:,.1f} s | {v['observaciones_por_segundo'] or 0:,} | "
            f"{v['memoria_pico_mb'] or 0:,.0f} MiB |"
        )
    total = reporte["totales"]
    lineas += [
        "",
        f"Total del motor: {total['segundos_motor']:,.1f} s para {total['observaciones']:,} "
        f"observaciones ({total['observaciones_por_segundo']:,}/s). Historia de los pagos: "
        f"{total['segundos_historia_pagos']:,.1f} s, de los que la apertura de sus ventanas fue "
        f"{total['segundos_apertura']:,.2f} s.",
        "",
        f"El manifiesto del escenario dice {total['manifiesto']['movimientos']:,} movimientos, "
        f"{total['manifiesto']['repetidos_exactos']:,} repetidos exactos y "
        f"{total['manifiesto']['ajustes']:,} ajustes; el motor leyo {total['observaciones']:,} "
        f"observaciones, con {total['duplicados_exactos']:,} duplicados exactos, "
        f"{total['coincidencias_ambiguas']:,} coincidencias ambiguas, {total['reversos']:,} "
        f"reversos y {total['posibles_reversos']:,} posibles reversos.",
        "",
        "| Fase | " + " | ".join(v["periodo"] for v in reporte["motor"]) + " |",
        "|---|" + "---|" * len(reporte["motor"]),
    ]
    fases = sorted({f for v in reporte["motor"] for f in v["fases"]})
    for fase in fases:
        celdas = " | ".join(f"{v['fases'].get(fase, 0):,.1f} s" for v in reporte["motor"])
        lineas.append(f"| {fase} | {celdas} |")
    lineas += ["", "| Tabla | Filas | Datos | Indices | Total |", "|---|---|---|---|---|"]
    for tabla in reporte["tamanos_despues"]["tablas"]:
        if tabla["tabla"] not in (*TABLAS_DEL_MOTOR, "pago_observado"):
            continue
        lineas.append(
            f"| {tabla['tabla']} | ~{tabla['filas_estimadas']:,} | {_mib(tabla['datos_bytes'])} | "
            f"{_mib(tabla['indices_bytes'])} | {_mib(tabla['total_bytes'])} |"
        )
    antes = reporte["tamanos_antes"]["base_bytes"]
    despues = reporte["tamanos_despues"]["base_bytes"]
    lineas += [
        "",
        f"Base: {_mib(antes)} antes del motor, {_mib(despues)} despues ({_mib(despues - antes)} "
        "mas).",
        "",
        "| Llegada tardia | Filas | Historia | Ventanas interpretadas | Interpretacion |",
        "|---|---|---|---|---|",
    ]
    for caso in reporte["incremental"]:
        ventanas = ", ".join(
            f"{v['periodo']} ({v['observaciones']:,} obs., {v['reversos']:,} reversos)"
            for v in caso["interpretadas"]
        )
        lineas.append(
            f"| {caso['caso']} | {caso['filas']:,} | {caso['historia']['segundos']:,.1f} s | "
            f"{ventanas} | {sum(v['segundos'] for v in caso['interpretadas']):,.1f} s |"
        )
    lineas += ["", "| Consulta | Mediana | p95 | Maximo |", "|---|---|---|---|"]
    for nombre, t in reporte["consultas"]["tiempos"].items():
        lineas.append(
            f"| {nombre} | {t['mediana_ms']:,.2f} ms | {t['p95_ms']:,.2f} ms | "
            f"{t['maximo_ms']:,.2f} ms |"
        )
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
    temporal = Path(tempfile.mkdtemp(prefix="motor-cartera-motor-pagos-"))
    destino = Path(argumentos.destino) if argumentos.destino else temporal / "escenario"
    os.environ.setdefault("MC_SOURCE_STORE_ROOT", str(temporal / "almacen"))
    _preparar_base()

    manifiesto_existente = destino / "escenario.json"
    if manifiesto_existente.exists():
        manifiesto = json.loads(manifiesto_existente.read_text(encoding="utf-8"))
        generado = {"segundos": 0, "reusado": True}
        print(f"Reusando el escenario de {destino}", flush=True)
    else:
        print(f"Generando {argumentos.cortes} cortes del perfil {perfil}...", flush=True)
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
        manifiesto = generado.pop("manifiesto")
        print(f"  {generado['segundos']:,.1f} s", flush=True)

    ingestas = []
    for corte in manifiesto["cortes"]:
        print(f"Ingiriendo el corte {corte['fecha_corte']}...", flush=True)
        ingestas.append(
            _hijo(
                "ingerir",
                "--ruta",
                str(destino / corte["archivo"]),
                "--corte",
                corte["fecha_corte"],
            )
        )
    for periodo in manifiesto["periodos"]:
        print(f"Ingiriendo los pagos {periodo['archivo']}...", flush=True)
        ingestas.append(_hijo("ingerir", "--ruta", str(destino / periodo["archivo"])))
    if any(i["estado"] != "EXITOSA" for i in ingestas):
        sys.exit("Alguna ingesta no termino EXITOSA: el benchmark no es valido.")

    historias = []
    for ejecucion_id, tipo in _sql(
        "SELECT e.id, e.tipo_fuente FROM ejecucion_historia e "
        "JOIN dataset_conformado d ON d.id = e.dataset_conformado_id "
        "LEFT JOIN corrida c ON c.id = d.corrida_id ORDER BY e.tipo_fuente, c.fecha_corte, e.id"
    ):
        print(f"Materializando la historia {ejecucion_id} ({tipo})...", flush=True)
        historias.append(_hijo("historiar", "--ejecucion", str(ejecucion_id)))
    if any(h["estado"] != "EXITOSA" for h in historias):
        sys.exit("Alguna historia no termino EXITOSA: el benchmark no es valido.")
    de_pagos = [h for h in historias if h["tipo"] == "PAGOS"]

    print("VACUUM ANALYZE antes del motor...", flush=True)
    vacuum_antes = _vacuum(("pago_observado", "snapshot_cuenta", "cuenta_canonica"))
    tamanos_antes = _tamanos()

    motor = _interpretar_pendientes("backfill")
    if any(v["estado"] != "EXITOSA" for v in motor):
        sys.exit("Alguna ventana no termino EXITOSA: el benchmark no es valido.")

    print("VACUUM ANALYZE despues del motor...", flush=True)
    vacuum_despues = _vacuum(TABLAS_DEL_MOTOR)
    tamanos_despues = _tamanos()

    # Llegadas tardias: pagos nuevos de la ultima ventana, y pagos del mes siguiente.
    ultimo = manifiesto["periodos"][-1]
    origen = destino / ultimo["archivo"]
    if origen.suffix == ".zip":
        import zipfile

        with zipfile.ZipFile(origen) as paquete:
            (miembro,) = [n for n in paquete.namelist() if n.upper().startswith("PAGOS")]
            paquete.extract(miembro, temporal)
        origen = temporal / miembro
    # Del primer dia del periodo al dia siguiente al primero del mes que sigue a su fin.
    desde = date.fromisoformat(ultimo["desde"])
    siguiente = (date.fromisoformat(ultimo["hasta"]).replace(day=1) + timedelta(days=32)).replace(
        day=1
    )
    incremental = []
    for numero, (caso, dias, reversos) in enumerate(
        (
            ("pagos tarde, en la ultima ventana", -3, False),
            ("reversos de pagos ya interpretados", 2, True),
            ("pagos del mes siguiente", (siguiente - desde).days + 1, False),
        ),
        start=1,
    ):
        ruta = temporal / f"pagos_tardios_{numero}.csv"
        escrito = _hijo(
            "tardio",
            "--destino",
            str(ruta),
            "--origen",
            str(origen),
            "--desde-fila",
            str((numero - 1) * argumentos.tardias),
            "--cuantas",
            str(argumentos.tardias),
            "--dias",
            str(dias),
            *(["--reversos"] if reversos else []),
        )
        print(f"Llega un archivo {caso}: {escrito['filas']:,} pagos...", flush=True)
        ingesta = _hijo("ingerir", "--ruta", escrito["ruta"])
        (historia_id,) = _sql(
            "SELECT e.id FROM ejecucion_historia e JOIN dataset_conformado d ON "
            "d.id = e.dataset_conformado_id WHERE e.estado = 'EN_PROCESO'"
        )[0]
        historia = _hijo("historiar", "--ejecucion", str(historia_id))
        interpretadas = _interpretar_pendientes(caso)
        incremental.append(
            {
                "caso": caso,
                "filas": escrito["filas"],
                "ingesta": ingesta,
                "historia": historia,
                "interpretadas": interpretadas,
            }
        )

    print("Consultas de una cuenta y de un movimiento...", flush=True)
    salida = (
        Path(argumentos.salida) if argumentos.salida else destino / "benchmark_motor_pagos.json"
    )
    consultas = _hijo("consultar", "--destino", str(salida.parent))
    observaciones = sum(v["observaciones"] for v in motor)
    segundos = sum(v["segundos"] for v in motor)
    reporte = {
        "perfil": perfil,
        "cuentas_iniciales": PERFILES[perfil],
        "cortes": len(manifiesto["cortes"]),
        "formato": formato,
        "semilla": argumentos.semilla,
        "generacion": generado,
        "manifiesto": manifiesto,
        "ingestas": ingestas,
        "historias": historias,
        "motor": motor,
        "incremental": incremental,
        "totales": {
            "observaciones": observaciones,
            "segundos_motor": round(segundos, 3),
            "observaciones_por_segundo": round(observaciones / segundos) if segundos else None,
            "memoria_pico_motor_mb": max(v["memoria_pico_mb"] or 0 for v in motor),
            "segundos_historia_pagos": round(sum(h["segundos"] for h in de_pagos), 3),
            "segundos_apertura": round(
                sum(h["fases"].get("motor_de_pagos", 0) for h in de_pagos), 3
            ),
            **{
                clase: sum(v[clase] for v in motor)
                for clase in (
                    "movimientos",
                    "duplicados_exactos",
                    "coincidencias_ambiguas",
                    "reversos",
                    "posibles_reversos",
                    "no_conciliados",
                    "sin_cuenta_observada",
                )
            },
            "manifiesto": {
                clave: sum(p[clave] for p in manifiesto["periodos"])
                for clave in ("movimientos", "repetidos_exactos", "ajustes")
            },
        },
        "vacuum_segundos": {"antes": vacuum_antes, "despues": vacuum_despues},
        "tamanos_antes": tamanos_antes,
        "tamanos_despues": tamanos_despues,
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
    (salida.parent / "benchmark_motor_pagos.md").write_text(texto + "\n", encoding="utf-8")
    print(texto)
    print(f"\nReporte: {salida}; planes: {salida.parent / 'planes_motor_pagos.txt'}")
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
    etapas.add_parser("interpretar").add_argument("--ejecucion", required=True, type=int)
    de_tardio = etapas.add_parser("tardio")
    de_tardio.add_argument("--destino", required=True, type=Path)
    de_tardio.add_argument("--origen", required=True, type=Path)
    de_tardio.add_argument("--desde-fila", required=True, type=int)
    de_tardio.add_argument("--cuantas", required=True, type=int)
    de_tardio.add_argument("--dias", required=True, type=int)
    de_tardio.add_argument("--reversos", action="store_true")
    etapas.add_parser("consultar").add_argument("--destino", required=True, type=Path)
    lector.add_argument("--perfil", default="XL", help="XS, S, M, L, XL (por omision) o XXL.")
    lector.add_argument("--cortes", type=int, default=12, help="Cuantos cortes; 12 por omision.")
    lector.add_argument("--formato", default="zip", help="zip (por omision) o csv.")
    lector.add_argument("--destino", help="Donde se escriben (o se reusan) los archivos.")
    lector.add_argument("--salida", help="El reporte JSON; por omision, junto a los archivos.")
    lector.add_argument("--semilla", type=int, default=31416)
    lector.add_argument(
        "--tardias", type=int, default=5000, help="Cuantos pagos trae cada llegada tardia."
    )
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
    elif leidos.etapa == "interpretar":
        print(json.dumps(interpretar_ventana(leidos.ejecucion)))
    elif leidos.etapa == "tardio":
        print(
            json.dumps(
                tardio(
                    leidos.destino,
                    leidos.origen,
                    leidos.desde_fila,
                    leidos.cuantas,
                    leidos.dias,
                    leidos.reversos,
                )
            )
        )
    elif leidos.etapa == "consultar":
        print(json.dumps(consultar(leidos.destino), default=str))
    else:
        main(leidos)
