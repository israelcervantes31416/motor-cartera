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

Cada etapa se anota en una bitacora (benchmark_motor_pagos.etapas.json, junto al reporte) en cuanto
termina. Con --reanudar, una corrida interrumpida sigue sobre la misma base y el mismo --destino:
no repite ninguna etapa que ya termino y conserva su medicion. Lo que la base da por terminado y la
bitacora no tiene se anota con lo que dice la base, sin tiempos, y el reporte lo dice.

    python scripts/benchmark_motor_pagos.py --perfil XL --cortes 12 --destino <dir> --reanudar
"""

from __future__ import annotations

import argparse
import hashlib
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

    from motor_cartera.db.sesion import sesion
    from motor_cartera.ingesta.fuente_oficial import Cronometro
    from motor_cartera.motor_pagos.ejecuciones import interpretar

    # El WAL que escribe la interpretacion: sus filas nuevas y sus indices, y tambien el bloqueo
    # KEY SHARE que cada llave foranea pone en el pago observado que referencia.
    with sesion() as s:
        wal_antes = s.exec(text("SELECT CAST(pg_current_wal_lsn() AS text)")).one()[0]
    cronometro = Cronometro()
    inicio = time.perf_counter()
    interpretar(ejecucion_id, cronometro=cronometro)
    segundos = time.perf_counter() - inicio
    with sesion() as s:
        wal = s.exec(
            text("SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), CAST(:antes AS pg_lsn))"),
            params={"antes": wal_antes},
        ).one()[0]
    publicado = resumen_de_ejecucion(ejecucion_id)
    return {
        **publicado,
        "segundos": round(segundos, 3),
        "observaciones_por_segundo": round(publicado["observaciones"] / segundos)
        if segundos
        else None,
        "fases": {nombre: round(s_, 3) for nombre, s_ in cronometro.fases.items()},
        "memoria_pico_mb": memoria_pico_mb(),
        "wal_mib": round(float(wal) / 2**20, 1),
    }


def resumen_de_ejecucion(ejecucion_id: int) -> dict:
    """Lo que publico una ejecucion del motor, leido de la base: sus conteos y, por que, cuantas
    observaciones de cada clase tienen cada motivo. Fuera de cualquier tiempo medido."""
    from sqlalchemy import text

    from motor_cartera.db.modelos import EjecucionMotorPagos
    from motor_cartera.db.sesion import sesion

    with sesion() as s:
        e = s.get_one(EjecucionMotorPagos, ejecucion_id)
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
            "ejecucion": ejecucion_id,
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
            "motivos": motivos,
        }


def tardio(
    destino: Path, origen: Path, desde_fila: int, cuantas: int, dias: int, reversos: bool
) -> dict:
    """Un archivo de pagos que llega tarde: `cuantas` filas de `origen` a partir de `desde_fila`,
    recibidas `dias` despues (y un segundo mas, para que sean pagos nuevos y no copias). Con
    `reversos`, solo las de importe positivo, con el importe en negativo: el reverso de cada pago,
    que el motor tiene que emparejar con el pago que ya interpreto. Cada llegada usa otras filas:
    un pago con dos copias recibidas en otro momento ya no tendria un solo original posible.

    Lee `origen` por bloques y se detiene en cuanto junta las filas que necesita: el archivo de un
    periodo XL tiene cientos de miles. Las mismas filas, el mismo archivo: con --reanudar, una
    llegada que ya se ingirio se reconoce por su SHA-256."""
    import pandas as pd

    from motor_cartera.contratos.pagos import CONTRATO_PAGOS
    from motor_cartera.generador.oficial import escribir_pagos

    def columna(prefijo: str) -> str:
        (nombre,) = [n for n in CONTRATO_PAGOS.nombres if n.startswith(prefijo)]
        return nombre

    recepcion, importe = columna("Fecha_Recepci"), columna("Recuperaci")
    bloques = pd.read_csv(
        origen,
        dtype=str,
        keep_default_na=False,
        skiprows=range(1, desde_fila + 1),
        chunksize=max(cuantas, 1000),
    )
    partes, juntas = [], 0
    for bloque in bloques:
        if reversos:
            bloque = bloque[~bloque[importe].str.startswith("-")]
        partes.append(bloque)
        juntas += len(bloque)
        if juntas >= cuantas:
            break
    bloques.close()
    tabla = pd.concat(partes).head(cuantas).copy()
    if reversos:
        tabla[importe] = "-" + tabla[importe]
        tabla[columna("Concepto_C")] = "AJUSTE"
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

    def resultados_de_la_ventana(s):
        return consultas.resultados_de(
            s,
            consultas.obtener_ejecucion(s, run_de_la_copia).ejecucion,
            clasificacion=None,
            cliente_unico=None,
            desplazamiento=0,
            limite=50,
        )

    def ambiguas_de_la_ventana(s):
        return consultas.resultados_de(
            s,
            consultas.obtener_ejecucion(s, run_de_la_copia).ejecucion,
            clasificacion=Clasificacion.COINCIDENCIA_AMBIGUA,
            cliente_unico=None,
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
        "primera_pagina_de_resultados_de_una_ventana": resultados_de_la_ventana,
        "coincidencias_ambiguas_de_una_ventana": ambiguas_de_la_ventana,
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


def _tardias_empezadas() -> bool:
    """Si ya llego alguna de las llegadas tardias: despues de eso, el backfill ya termino."""
    return bool(_sql("SELECT 1 FROM ingesta_pagos WHERE origen LIKE 'pagos_tardios_%' LIMIT 1"))


def _ejecuciones() -> list[dict]:
    return [
        {
            "ejecucion": ejecucion,
            "periodo": periodo,
            "estado": estado,
            "resultado": resultado,
            "observaciones": observaciones,
            "contexto": contexto,
            "movimientos": movimientos,
        }
        for ejecucion, periodo, estado, resultado, observaciones, contexto, movimientos in _sql(
            "SELECT id, to_char(periodo_desde, 'YYYY-MM'), estado, resultado, "
            "observaciones_leidas, observaciones_contexto, movimientos_canonicos "
            "FROM ejecucion_motor_pagos ORDER BY id"
        )
    ]


def _suma(valores) -> float | None:
    """La suma, o None si falta alguna medicion: un total con huecos no es un total."""
    valores = list(valores)
    return None if any(v is None for v in valores) else round(sum(valores), 3)


# --- la bitacora: lo que ya se midio, etapa por etapa ---------------------------------------------

RECUPERADA = (
    "De la base: la corrida se interrumpio despues de que esta etapa termino y antes de anotar su "
    "medicion."
)
SIN_MEDICION = {"segundos": None, "fases": {}, "memoria_pico_mb": None}


class Bitacora:
    """Lo que ya se midio, en un JSON junto al reporte que se reescribe entero, de forma atomica,
    en cuanto termina cada etapa. El XL tarda horas: si se interrumpe, --reanudar no repite lo que
    ya termino y conserva su medicion. Una etapa que la base da por terminada y que la bitacora no
    tiene (la corrida se detuvo entre las dos cosas) se anota con lo que dice la base, sin tiempos y
    con `recuperado`."""

    def __init__(self, ruta: Path, reanudar: bool) -> None:
        self.ruta = ruta
        self.etapas: dict = (
            json.loads(ruta.read_text(encoding="utf-8")) if reanudar and ruta.exists() else {}
        )

    def __contains__(self, clave: str) -> bool:
        return clave in self.etapas

    def __getitem__(self, clave: str):
        return self.etapas[clave]

    def anotar(self, clave: str, valor):
        self.etapas[clave] = valor
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        provisional = self.ruta.with_name(self.ruta.name + ".tmp")
        provisional.write_text(
            json.dumps(self.etapas, indent=1, ensure_ascii=False, default=str), encoding="utf-8"
        )
        provisional.replace(self.ruta)
        return valor

    def recuperadas(self) -> list[str]:
        return sorted(
            clave
            for clave, valor in self.etapas.items()
            if isinstance(valor, dict) and valor.get("recuperado")
        )


def _ingeridas() -> dict[str, dict]:
    """Las ingestas EXITOSA que ya tiene la base, por la firma de su archivo."""
    return {
        firma: {
            "estado": estado,
            "filas": filas,
            "segundos": None,
            "segundos_en_la_base": round(float(segundos), 3),
            "memoria_pico_mb": None,
            "recuperado": RECUPERADA,
        }
        for firma, estado, filas, segundos in _sql(
            "SELECT firma, estado, filas_validas, extract(epoch FROM terminada_en - iniciada_en) "
            "FROM corrida WHERE estado = 'EXITOSA' UNION ALL "
            "SELECT firma, estado, filas_validas, extract(epoch FROM terminada_en - iniciada_en) "
            "FROM ingesta_pagos WHERE estado = 'EXITOSA'"
        )
    }


def _ingesta(
    bitacora: Bitacora,
    ingeridas: dict[str, dict],
    firma: str,
    etiqueta: str,
    ruta: Path,
    corte: str | None = None,
    clave: str | None = None,
) -> dict:
    clave = clave or f"ingesta:{firma}"
    if clave in bitacora:
        print(f"Ya estaba ingerido: {etiqueta}", flush=True)
        return bitacora[clave]
    if firma in ingeridas:
        print(f"Ya estaba ingerido: {etiqueta}", flush=True)
        return bitacora.anotar(clave, ingeridas[firma])
    print(f"Ingiriendo {etiqueta}...", flush=True)
    argumentos = ["ingerir", "--ruta", str(ruta)]
    if corte is not None:
        argumentos += ["--corte", corte]
    return bitacora.anotar(clave, _hijo(*argumentos))


def _historia(bitacora: Bitacora, ejecucion_id: int, tipo: str, estado: str) -> dict:
    clave = f"historia:{ejecucion_id}"
    if clave in bitacora:
        print(f"Ya estaba materializada: la historia {ejecucion_id} ({tipo})", flush=True)
        return bitacora[clave]
    if estado == "EN_PROCESO":
        print(f"Materializando la historia {ejecucion_id} ({tipo})...", flush=True)
        return bitacora.anotar(clave, _hijo("historiar", "--ejecucion", str(ejecucion_id)))
    print(f"Ya estaba materializada: la historia {ejecucion_id} ({tipo})", flush=True)
    ((resultado, registros, detalle),) = _sql(
        "SELECT resultado, registros_publicados, detalle FROM ejecucion_historia WHERE id = :id",
        {"id": ejecucion_id},
    )
    return bitacora.anotar(
        clave,
        {
            "estado": estado,
            "resultado": resultado,
            "tipo": tipo,
            "registros": registros,
            **SIN_MEDICION,
            "filas_por_segundo": None,
            "detalle": detalle,
            "recuperado": RECUPERADA,
        },
    )


def _motor(bitacora: Bitacora, ejecucion_id: int, etiqueta: str) -> dict:
    clave = f"motor:{ejecucion_id}"
    if clave in bitacora:
        hecha = bitacora[clave]
        print(f"Ya estaba interpretada: la ventana {hecha['periodo']} ({etiqueta})", flush=True)
        return hecha
    ((estado, periodo),) = _sql(
        "SELECT estado, to_char(periodo_desde, 'YYYY-MM') FROM ejecucion_motor_pagos "
        "WHERE id = :id",
        {"id": ejecucion_id},
    )
    if estado != "EN_PROCESO":
        print(f"Ya estaba interpretada: la ventana {periodo} ({etiqueta})", flush=True)
        return bitacora.anotar(
            clave,
            {
                **resumen_de_ejecucion(ejecucion_id),
                **SIN_MEDICION,
                "observaciones_por_segundo": None,
                "wal_mib": None,
                "recuperado": RECUPERADA,
            },
        )
    print(f"Interpretando la ventana {periodo} ({etiqueta})...", flush=True)
    resultado = bitacora.anotar(clave, _hijo("interpretar", "--ejecucion", str(ejecucion_id)))
    print(
        f"  {resultado['resultado']}: {resultado['observaciones']:,} observaciones en "
        f"{resultado['segundos']:,.1f} s ({resultado['observaciones_por_segundo']:,}/s, "
        f"{resultado['memoria_pico_mb'] or 0:,.0f} MiB, WAL {resultado['wal_mib']:,.0f} MiB)",
        flush=True,
    )
    return resultado


def _tardio(
    bitacora: Bitacora,
    ingeridas: dict[str, dict],
    numero: int,
    caso: str,
    origen: Path,
    carpeta: Path,
    desde_fila: int,
    cuantas: int,
    dias: int,
    reversos: bool,
) -> dict:
    """Una llegada tardia de punta a punta: su archivo, su ingesta, su historia (que abre las
    ventanas que cambian) y la interpretacion de esas ventanas."""
    clave = f"tardio:{numero}"
    if clave in bitacora:
        print(f"Ya estaba medida la llegada tardia: {caso}", flush=True)
        return bitacora[clave]
    ruta = carpeta / f"pagos_tardios_{numero}.csv"
    escrito = _hijo(
        "tardio",
        "--destino",
        str(ruta),
        "--origen",
        str(origen),
        "--desde-fila",
        str(desde_fila),
        "--cuantas",
        str(cuantas),
        "--dias",
        str(dias),
        *(["--reversos"] if reversos else []),
    )
    firma = hashlib.sha256(ruta.read_bytes()).hexdigest()
    print(f"Llega un archivo {caso}: {escrito['filas']:,} pagos...", flush=True)
    if _sql(
        "SELECT 1 FROM ingesta_pagos WHERE origen = :origen AND estado = 'EXITOSA' "
        "AND firma <> :firma",
        {"origen": ruta.name, "firma": firma},
    ):
        sys.exit(f"La base ya tiene otro {ruta.name}, con otro contenido: no es la misma llegada.")
    ingesta = _ingesta(
        bitacora, ingeridas, firma, f"la llegada tardia {numero}", ruta, clave=f"{clave}:ingesta"
    )
    if ingesta["estado"] != "EXITOSA":
        sys.exit(f"La llegada tardia {numero} no se ingirio EXITOSA: el benchmark no es valido.")
    ((historia_id, tipo, estado),) = _sql(
        "SELECT e.id, e.tipo_fuente, e.estado FROM ejecucion_historia e "
        "JOIN dataset_conformado d ON d.id = e.dataset_conformado_id "
        "JOIN ingesta_pagos i ON i.id = d.ingesta_pagos_id "
        "WHERE i.firma = :firma AND i.estado = 'EXITOSA'",
        {"firma": firma},
    )
    historia = _historia(bitacora, historia_id, tipo, estado)
    if historia["estado"] != "EXITOSA":
        sys.exit(f"La historia de la llegada tardia {numero} no termino EXITOSA.")
    # Las ventanas que abrio su historia: las unicas EN_PROCESO, porque todo va en secuencia.
    if f"{clave}:ventanas" not in bitacora:
        bitacora.anotar(f"{clave}:ventanas", {"ejecuciones": [i for i, _ in _pendientes()]})
    interpretadas = [
        _motor(bitacora, ejecucion_id, caso)
        for ejecucion_id in bitacora[f"{clave}:ventanas"]["ejecuciones"]
    ]
    if any(v["estado"] != "EXITOSA" for v in interpretadas):
        sys.exit(f"Alguna ventana de la llegada tardia {numero} no termino EXITOSA.")
    return bitacora.anotar(
        clave,
        {
            "caso": caso,
            "filas": escrito["filas"],
            "firma": firma,
            "ingesta": ingesta,
            "historia": historia,
            "interpretadas": interpretadas,
        },
    )


# --- el reporte -----------------------------------------------------------------------------------


def _mib(valor: int) -> str:
    return f"{valor / 2**20:,.1f} MiB"


def _num(valor, formato: str = ",", sufijo: str = "") -> str:
    """Un numero con su formato, o n/d si no se midio."""
    return "n/d" if valor is None else f"{valor:{formato}}{sufijo}"


def _interpretaciones(reporte: dict) -> list[tuple[str, dict]]:
    """Cada interpretacion de la corrida con su etiqueta: las del backfill y las de cada llegada."""
    return [(v["periodo"], v) for v in reporte["motor"]] + [
        (f"{v['periodo']} (tardia {numero})", v)
        for numero, caso in enumerate(reporte["incremental"], start=1)
        for v in caso["interpretadas"]
    ]


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
            f"{_num(v['segundos'], ',.1f', ' s')} | {_num(v['observaciones_por_segundo'])} | "
            f"{_num(v['memoria_pico_mb'], ',.0f', ' MiB')} |"
        )
    total = reporte["totales"]
    lineas += [
        "",
        f"Total del motor: {_num(total['segundos_motor'], ',.1f', ' s')} para "
        f"{total['observaciones']:,} observaciones ({_num(total['observaciones_por_segundo'])}/s). "
        f"Historia de los pagos: {_num(total['segundos_historia_pagos'], ',.1f', ' s')}, de los "
        f"que la apertura de sus ventanas fue {_num(total['segundos_apertura'], ',.2f', ' s')}.",
        "",
        f"El manifiesto del escenario dice {total['manifiesto']['movimientos']:,} movimientos, "
        f"{total['manifiesto']['repetidos_exactos']:,} repetidos exactos y "
        f"{total['manifiesto']['ajustes']:,} ajustes; el motor leyo {total['observaciones']:,} "
        f"observaciones, con {total['duplicados_exactos']:,} duplicados exactos, "
        f"{total['coincidencias_ambiguas']:,} coincidencias ambiguas, {total['reversos']:,} "
        f"reversos y {total['posibles_reversos']:,} posibles reversos.",
    ]
    medidas = [(etiqueta, v) for etiqueta, v in _interpretaciones(reporte) if v["fases"]]
    if medidas:
        lineas += [
            "",
            "| Fase | " + " | ".join(etiqueta for etiqueta, _ in medidas) + " |",
            "|---|" + "---|" * len(medidas),
        ]
        for fase in sorted({f for _, v in medidas for f in v["fases"]}):
            celdas = " | ".join(_num(v["fases"].get(fase), ",.1f", " s") for _, v in medidas)
            lineas.append(f"| {fase} | {celdas} |")
        celdas = " | ".join(_num(v["wal_mib"], ",.0f", " MiB") for _, v in medidas)
        lineas.append(f"| WAL escrito | {celdas} |")
    final = reporte["tamanos_al_final"]
    lineas += ["", "| Tabla | Filas | Datos | Indices | Total |", "|---|---|---|---|---|"]
    for tabla in final["tablas"]:
        if tabla["tabla"] not in (*TABLAS_DEL_MOTOR, "pago_observado"):
            continue
        lineas.append(
            f"| {tabla['tabla']} | ~{tabla['filas_estimadas']:,} | {_mib(tabla['datos_bytes'])} | "
            f"{_mib(tabla['indices_bytes'])} | {_mib(tabla['total_bytes'])} |"
        )
    del_motor = sum(t["total_bytes"] for t in final["tablas"] if t["tabla"] in TABLAS_DEL_MOTOR)
    lineas += [
        "",
        f"Al final, la base ocupa {_mib(final['base_bytes'])}, y las tablas del motor, "
        f"{_mib(del_motor)}.",
    ]
    if reporte["tamanos_antes"] and reporte["tamanos_despues"]:
        antes = reporte["tamanos_antes"]["base_bytes"]
        despues = reporte["tamanos_despues"]["base_bytes"]
        lineas.append(
            f"Base: {_mib(antes)} antes del motor, {_mib(despues)} despues del backfill "
            f"({_mib(despues - antes)} mas)."
        )
    publicadas = [e for e in reporte["ejecuciones_al_final"] if e["estado"] == "EXITOSA"]
    lineas += [
        "",
        f"{len(publicadas)} interpretaciones publicadas de "
        f"{len({e['periodo'] for e in publicadas})} ventanas; cada una guarda sus resultados y sus "
        "movimientos:",
        "",
        "| Ejecucion | Ventana | Estado | Observaciones | Contexto | Movimientos |",
        "|---|---|---|---|---|---|",
    ]
    for e in reporte["ejecuciones_al_final"]:
        lineas.append(
            f"| {e['ejecucion']} | {e['periodo']} | {e['estado']} | {e['observaciones']:,} | "
            f"{e['contexto']:,} | {e['movimientos']:,} |"
        )
    lineas += [
        "",
        "| Llegada tardia | Filas | Historia | Apertura | Ventanas interpretadas | "
        "Interpretacion |",
        "|---|---|---|---|---|---|",
    ]
    for caso in reporte["incremental"]:
        ventanas = ", ".join(
            f"{v['periodo']} ({v['observaciones']:,} obs., {v['reversos']:,} reversos)"
            for v in caso["interpretadas"]
        )
        historia = caso["historia"]
        lineas.append(
            f"| {caso['caso']} | {caso['filas']:,} | {_num(historia['segundos'], ',.1f', ' s')} | "
            f"{_num(historia['fases'].get('motor_de_pagos'), ',.2f', ' s')} | {ventanas} | "
            f"{_num(_suma(v['segundos'] for v in caso['interpretadas']), ',.1f', ' s')} |"
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
    reanudado = reporte["reanudado"]
    if reanudado and reanudado["etapas_recuperadas"]:
        lineas += [
            "",
            f"Corrida reanudada: {len(reanudado['etapas_recuperadas'])} etapas ya habian terminado "
            "cuando se interrumpio y se tomaron de la base, sin su medicion (n/d en las "
            "tablas).",
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
    salida = (
        Path(argumentos.salida) if argumentos.salida else destino / "benchmark_motor_pagos.json"
    )
    os.environ.setdefault("MC_SOURCE_STORE_ROOT", str(temporal / "almacen"))
    bitacora = Bitacora(salida.with_name("benchmark_motor_pagos.etapas.json"), argumentos.reanudar)
    if argumentos.reanudar:
        # La base de una corrida que se interrumpio: no se vacia, y lo que ya termino no se repite.
        # Una ingesta cortada a la mitad dejo su registro EN_PROCESO, sin nada publicado: se cierra
        # FALLIDA para que su archivo se pueda ingerir otra vez. Una historia o una interpretacion
        # cortada sigue EN_PROCESO, sin nada publicado, y se ejecuta de nuevo.
        from sqlalchemy import make_url, text

        from motor_cartera.db.sesion import crear_motor

        if not (make_url(os.environ.get("MC_DATABASE_URL", "")).database or "").endswith("_bench"):
            sys.exit("MC_DATABASE_URL tiene que apuntar a una base *_bench.")
        with crear_motor().begin() as conexion:
            for tabla in ("corrida", "ingesta_pagos"):
                conexion.execute(
                    text(
                        f"UPDATE {tabla} SET estado = 'FALLIDA', terminada_en = now(), "
                        "detalle = 'Ingesta interrumpida: el benchmark se detuvo antes de "
                        "confirmarla.' WHERE estado = 'EN_PROCESO'"
                    )
                )
    else:
        _preparar_base()
    ingeridas = _ingeridas()

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

    ingestas = [
        _ingesta(
            bitacora,
            ingeridas,
            corte["sha256"],
            f"el corte {corte['fecha_corte']}",
            destino / corte["archivo"],
            corte["fecha_corte"],
        )
        for corte in manifiesto["cortes"]
    ] + [
        _ingesta(
            bitacora,
            ingeridas,
            periodo["sha256"],
            f"los pagos {periodo['archivo']}",
            destino / periodo["archivo"],
        )
        for periodo in manifiesto["periodos"]
    ]
    if any(i["estado"] != "EXITOSA" for i in ingestas):
        sys.exit("Alguna ingesta no termino EXITOSA: el benchmark no es valido.")

    # Las historias de los archivos del escenario, no las de las llegadas tardias.
    firmas = [c["sha256"] for c in manifiesto["cortes"]] + [
        p["sha256"] for p in manifiesto["periodos"]
    ]
    historias = [
        _historia(bitacora, ejecucion_id, tipo, estado)
        for ejecucion_id, tipo, estado in _sql(
            "SELECT e.id, e.tipo_fuente, e.estado FROM ejecucion_historia e "
            "JOIN dataset_conformado d ON d.id = e.dataset_conformado_id "
            "LEFT JOIN corrida c ON c.id = d.corrida_id "
            "LEFT JOIN ingesta_pagos i ON i.id = d.ingesta_pagos_id "
            "WHERE coalesce(c.firma, i.firma) = ANY(:firmas) "
            "ORDER BY e.tipo_fuente, c.fecha_corte, e.id",
            {"firmas": firmas},
        )
    ]
    if any(h["estado"] != "EXITOSA" for h in historias):
        sys.exit("Alguna historia no termino EXITOSA: el benchmark no es valido.")
    de_pagos = [h for h in historias if h["tipo"] == "PAGOS"]

    if "antes_del_motor" not in bitacora:
        if _sql("SELECT 1 FROM ejecucion_motor_pagos WHERE estado <> 'EN_PROCESO' LIMIT 1"):
            bitacora.anotar(
                "antes_del_motor",
                {
                    "vacuum_segundos": None,
                    "tamanos": None,
                    "recuperado": "No se midio: la corrida se reanudo cuando el motor ya habia "
                    "publicado.",
                },
            )
        else:
            print("VACUUM ANALYZE antes del motor...", flush=True)
            vacuum = _vacuum(("pago_observado", "snapshot_cuenta", "cuenta_canonica"))
            bitacora.anotar("antes_del_motor", {"vacuum_segundos": vacuum, "tamanos": _tamanos()})
    antes = bitacora["antes_del_motor"]

    # Las ventanas del backfill son las que abrieron las historias de los pagos del escenario.
    if "backfill" not in bitacora:
        if _tardias_empezadas():
            sys.exit(
                "La corrida se interrumpio en las llegadas tardias y su bitacora no dice que "
                "ventanas eran del backfill: no se puede reanudar."
            )
        bitacora.anotar("backfill", {"ejecuciones": [i for i, _ in _pendientes()]})
    motor = [_motor(bitacora, i, "backfill") for i in bitacora["backfill"]["ejecuciones"]]
    if any(v["estado"] != "EXITOSA" for v in motor):
        sys.exit("Alguna ventana no termino EXITOSA: el benchmark no es valido.")

    if "despues_del_motor" not in bitacora:
        if _tardias_empezadas():
            bitacora.anotar(
                "despues_del_motor",
                {
                    "vacuum_segundos": None,
                    "tamanos": None,
                    "recuperado": "No se midio: la corrida se reanudo cuando ya habian llegado "
                    "pagos tardios.",
                },
            )
        else:
            print("VACUUM ANALYZE despues del motor...", flush=True)
            vacuum = _vacuum(TABLAS_DEL_MOTOR)
            bitacora.anotar("despues_del_motor", {"vacuum_segundos": vacuum, "tamanos": _tamanos()})
    despues = bitacora["despues_del_motor"]

    # Llegadas tardias: pagos nuevos de la ultima ventana, el reverso de pagos ya interpretados y
    # pagos del mes siguiente. Sus archivos van junto al reporte, para que una reanudacion los
    # encuentre.
    ultimo = manifiesto["periodos"][-1]
    carpeta = salida.parent / "tardios"
    # Del primer dia del periodo al dia siguiente al primero del mes que sigue a su fin.
    desde = date.fromisoformat(ultimo["desde"])
    siguiente = (date.fromisoformat(ultimo["hasta"]).replace(day=1) + timedelta(days=32)).replace(
        day=1
    )
    casos = (
        ("pagos tarde, en la ultima ventana", -3, False),
        ("reversos de pagos ya interpretados", 2, True),
        ("pagos del mes siguiente", (siguiente - desde).days + 1, False),
    )
    origen = destino / ultimo["archivo"]
    if origen.suffix == ".zip" and any(f"tardio:{n}" not in bitacora for n in (1, 2, 3)):
        import zipfile

        with zipfile.ZipFile(origen) as paquete:
            (miembro,) = [n for n in paquete.namelist() if n.upper().startswith("PAGOS")]
            paquete.extract(miembro, carpeta)
        origen = carpeta / miembro
    incremental = [
        _tardio(
            bitacora,
            ingeridas,
            numero,
            caso,
            origen,
            carpeta,
            (numero - 1) * argumentos.tardias,
            argumentos.tardias,
            dias,
            reversos,
        )
        for numero, (caso, dias, reversos) in enumerate(casos, start=1)
    ]

    # Al final, como lo dejaria autovacuum: lo que ocupa todo, y cada interpretacion publicada.
    if "al_final" not in bitacora:
        print("VACUUM ANALYZE al final...", flush=True)
        vacuum = _vacuum((*TABLAS_DEL_MOTOR, "pago_observado"))
        bitacora.anotar(
            "al_final",
            {"vacuum_segundos": vacuum, "tamanos": _tamanos(), "ejecuciones": _ejecuciones()},
        )
    al_final = bitacora["al_final"]

    if "consultas" not in bitacora:
        print("Consultas de una cuenta y de un movimiento...", flush=True)
        bitacora.anotar("consultas", _hijo("consultar", "--destino", str(salida.parent)))
    consultas = bitacora["consultas"]

    observaciones = sum(v["observaciones"] for v in motor)
    segundos = _suma(v["segundos"] for v in motor)
    reporte = {
        "perfil": perfil,
        "cuentas_iniciales": PERFILES[perfil],
        "cortes": len(manifiesto["cortes"]),
        "formato": formato,
        "semilla": argumentos.semilla,
        "reanudado": {
            "archivos_ya_ingeridos": len(ingeridas),
            "etapas_recuperadas": bitacora.recuperadas(),
        }
        if argumentos.reanudar
        else None,
        "filas_por_lote": os.environ.get("MC_FILAS_POR_LOTE"),
        "generacion": generado,
        "manifiesto": manifiesto,
        "ingestas": ingestas,
        "historias": historias,
        "motor": motor,
        "incremental": incremental,
        "totales": {
            "observaciones": observaciones,
            "segundos_motor": segundos,
            "observaciones_por_segundo": round(observaciones / segundos) if segundos else None,
            "memoria_pico_motor_mb": max(
                (v["memoria_pico_mb"] for v in motor if v["memoria_pico_mb"] is not None),
                default=None,
            ),
            "segundos_historia_pagos": _suma(h["segundos"] for h in de_pagos),
            "segundos_apertura": _suma(h["fases"].get("motor_de_pagos") for h in de_pagos),
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
        "vacuum_segundos": {
            "antes": antes["vacuum_segundos"],
            "despues": despues["vacuum_segundos"],
            "al_final": al_final["vacuum_segundos"],
        },
        "tamanos_antes": antes["tamanos"],
        "tamanos_despues": despues["tamanos"],
        "tamanos_al_final": al_final["tamanos"],
        "ejecuciones_al_final": al_final["ejecuciones"],
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
        "--reanudar",
        action="store_true",
        help="Sigue una corrida interrumpida sobre la misma base: no la vacia, no repite "
        "ninguna etapa que ya termino y conserva su medicion (ver la bitacora).",
    )
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
