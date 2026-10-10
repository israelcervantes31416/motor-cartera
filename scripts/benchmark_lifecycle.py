"""Benchmark del lifecycle de cobranza y de la atribucion: cuanto tarda importar millones de eventos
operacionales sinteticos, atribuir los pagos de cada ventana y evaluar las promesas, cuanto crece la
base y como responden las consultas de una cuenta, contra PostgreSQL. No corre en el CI: se pide a
mano, sobre una base que ya tiene un escenario cargado e interpretado por el motor de pagos (el de
scripts/benchmark_motor_pagos.py):

    python scripts/benchmark_lifecycle.py --escenario <dir del escenario> --destino <dir>
    python scripts/benchmark_lifecycle.py --escenario <dir> --destino <dir> --reanudar

Lo que hace, cada etapa en su propio proceso para que su memoria pico sea solo suya:

1. escribe el lifecycle de cada periodo del escenario con sus mismos parametros (los de su
   escenario.json), sin volver a escribir sus cortes ni sus pagos, con la --intensidad que se pida;
2. importa cada archivo como cargar-lifecycle (validacion en Python, COPY a tablas temporales y
   SQL por conjuntos, todo o nada) y mide su tiempo, sus eventos por segundo, su memoria pico y el
   WAL que escribe;
3. corre VACUUM ANALYZE y mide el tamano de la base y de cada tabla del lifecycle;
4. atribuye cada ventana con una interpretacion vigente de los pagos y mide su tiempo, sus pagos
   por segundo, sus fases, su memoria pico y lo que concluyo;
5. evalua todas las promesas a la fecha del ultimo corte;
6. vuelve a importar el primer archivo: no registra nada, y se mide cuanto tarda saberlo;
7. mide por la API las consultas de una cuenta (sus gestiones, su linea de tiempo, sus promesas
   vigentes, sus pagos atribuidos, la Cuenta 360 y las atribuciones de un pago) y las de una
   atribucion (sus pagos ambiguos con sus candidatas, los de un cliente), y guarda el plan de cada
   sentencia con EXPLAIN (ANALYZE, BUFFERS): una consulta de una cuenta no hace Seq Scan sobre una
   tabla grande.

MC_DATABASE_URL tiene que apuntar a una base *_bench. No se vacia: se le agrega el lifecycle. Cada
etapa se anota en una bitacora (benchmark_lifecycle.etapas.json, junto al reporte) en cuanto
termina; con --reanudar no se repite ninguna. Escribe un JSON con todo, los planes en texto y una
tabla en Markdown para la documentacion.
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
from benchmark_motor_pagos import Bitacora, _sql, _vacuum  # noqa: E402

REPETICIONES = 25
TABLAS_DEL_LIFECYCLE = (
    "evento_lifecycle",
    "gestion_cobranza",
    "visita_campo",
    "promesa_pago",
    "convenio_cobranza",
    "cuota_convenio",
)
TABLAS_DE_LA_ATRIBUCION = (
    "ejecucion_atribucion",
    "atribucion_movimiento",
    "candidato_atribucion",
    "ejecucion_evaluacion_promesas",
    "evaluacion_promesa",
)
TABLAS_GRANDES = (
    *TABLAS_DEL_LIFECYCLE,
    "atribucion_movimiento",
    "candidato_atribucion",
    "evaluacion_promesa",
    "movimiento_economico_canonico",
    "pago_observado",
    "resultado_pago_observado",
    "snapshot_cuenta",
    "cuenta_canonica",
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


def _wal() -> str:
    return _sql("SELECT CAST(pg_current_wal_lsn() AS text)")[0][0]


def _wal_desde(antes: str) -> float:
    (diferencia,) = _sql(
        "SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), CAST(:antes AS pg_lsn))", {"antes": antes}
    )[0]
    return round(float(diferencia) / 2**20, 1)


# --- las etapas, cada una en su proceso -----------------------------------------------------------


def escribir(escenario: Path, destino: Path, intensidad: float) -> dict:
    from motor_cartera.generador.lifecycle import escribir_lifecycle_del_escenario

    m = json.loads((escenario / "escenario.json").read_text(encoding="utf-8"))
    inicio = time.perf_counter()
    archivos = escribir_lifecycle_del_escenario(
        destino,
        cuentas=m["cuentas_iniciales"],
        cortes=len(m["cortes"]),
        primer_corte=date.fromisoformat(m["cortes"][0]["fecha_corte"]),
        semilla=m["semilla"],
        dias_entre_cortes=m["dias_entre_cortes"],
        tasa_altas=m["tasa_altas"],
        tasa_retiros=m["tasa_retiros"],
        intensidad=intensidad,
    )
    return {
        "archivos": [a.manifiesto() for a in archivos],
        "eventos": sum(a.eventos for a in archivos),
        "bytes": sum((destino / a.archivo).stat().st_size for a in archivos),
        "segundos": round(time.perf_counter() - inicio, 3),
        "memoria_pico_mb": memoria_pico_mb(),
    }


def importar(archivo: Path) -> dict:
    from motor_cartera.config import config
    from motor_cartera.lifecycle.importacion import importar as importar_archivo

    antes = _wal()
    inicio = time.perf_counter()
    reporte = importar_archivo(
        archivo,
        despacho_id=config.despacho_id,
        cartera_id=config.cartera_id,
        zona=config.zona_horaria_fuente,
    )
    segundos = time.perf_counter() - inicio
    return {
        "lineas": reporte.lineas,
        "nuevos": reporte.nuevos,
        "ya_registradas": reporte.ya_registradas,
        "registrados": reporte.registrados,
        "problemas": reporte.total_de_problemas,
        "primeros_problemas": [p.__dict__ for p in reporte.problemas[:5]],
        "segundos": round(segundos, 3),
        "eventos_por_segundo": round(reporte.lineas / segundos) if segundos else None,
        "memoria_pico_mb": memoria_pico_mb(),
        "wal_mib": _wal_desde(antes),
    }


def atribuir(ejecucion_id: int) -> dict:
    from motor_cartera.atribucion.ejecuciones import AtribucionYaPublicada
    from motor_cartera.atribucion.ejecuciones import atribuir as atribuir_ventana
    from motor_cartera.db.modelos import EjecucionAtribucion
    from motor_cartera.db.sesion import sesion
    from motor_cartera.ingesta.fuente_oficial import Cronometro

    antes = _wal()
    cronometro = Cronometro()
    inicio = time.perf_counter()
    recuperada = None
    try:
        atribuir_ventana(ejecucion_id, cronometro=cronometro)
    except AtribucionYaPublicada as exc:
        # Una corrida interrumpida despues de publicar y antes de anotarlo: se reporta la que ya
        # estaba, sin tiempos.
        recuperada, ejecucion_id = exc.previa, exc.previa.id
    segundos = time.perf_counter() - inicio
    if recuperada is not None:
        cronometro.fases.clear()
    with sesion() as s:
        e = s.get_one(EjecucionAtribucion, ejecucion_id)
        resultado = {
            "periodo": f"{e.periodo_desde:%Y-%m}",
            "estado": e.estado,
            "resultado": e.resultado,
            "ventana_dias": e.ventana_dias,
            "movimientos_evaluados": e.movimientos_evaluados,
            "asociados": e.asociados,
            "ambiguos": e.ambiguos,
            "sin_candidato": e.sin_candidato,
            "candidatos": e.candidatos,
            "movimientos_anulados": e.movimientos_anulados,
            "gestiones_leidas": e.gestiones_leidas,
            "gestiones_anuladas": e.gestiones_anuladas,
        }
    if recuperada is not None:
        return {**resultado, "segundos": None, "pagos_por_segundo": None, "recuperado": True}
    return {
        **resultado,
        "segundos": round(segundos, 3),
        "pagos_por_segundo": round(resultado["movimientos_evaluados"] / segundos)
        if segundos
        else None,
        "fases": {nombre: round(s_, 3) for nombre, s_ in cronometro.fases.items()},
        "memoria_pico_mb": memoria_pico_mb(),
        "wal_mib": _wal_desde(antes),
    }


def evaluar(ejecucion_id: int) -> dict:
    from motor_cartera.db.modelos import EjecucionEvaluacionPromesas
    from motor_cartera.db.sesion import sesion
    from motor_cartera.evaluacion.ejecuciones import evaluar as evaluar_promesas
    from motor_cartera.ingesta.fuente_oficial import Cronometro

    antes = _wal()
    cronometro = Cronometro()
    inicio = time.perf_counter()
    evaluar_promesas(ejecucion_id, cronometro=cronometro)
    segundos = time.perf_counter() - inicio
    with sesion() as s:
        e = s.get_one(EjecucionEvaluacionPromesas, ejecucion_id)
        resultado = {
            "as_of": e.as_of.isoformat(),
            "estado": e.estado,
            "resultado": e.resultado,
            "promesas_evaluadas": e.promesas_evaluadas,
            "cumplidas": e.cumplidas,
            "parciales": e.parciales,
            "incumplidas": e.incumplidas,
            "pendientes": e.pendientes,
            "canceladas": e.canceladas,
            "no_evaluables": e.no_evaluables,
        }
    return {
        **resultado,
        "segundos": round(segundos, 3),
        "promesas_por_segundo": round(resultado["promesas_evaluadas"] / segundos)
        if segundos
        else None,
        "fases": {nombre: round(s_, 3) for nombre, s_ in cronometro.fases.items()},
        "memoria_pico_mb": memoria_pico_mb(),
        "wal_mib": _wal_desde(antes),
    }


def consultar(destino: Path) -> dict:
    """Las consultas por la API, con un cliente HTTP en el mismo proceso: el tiempo incluye la
    serializacion. Cada una se repite y se reporta su mediana, su p95 y su maximo; despues se
    guarda el plan de cada sentencia que emitio."""
    from fastapi.testclient import TestClient
    from sqlalchemy import event

    from motor_cartera.api.app import crear_app
    from motor_cartera.config import Config
    from motor_cartera.db.sesion import crear_motor

    clave = "benchmark-lifecycle-" + "x" * 24
    cliente = TestClient(crear_app(Config(api_key=clave)), headers={"X-API-Key": clave})
    # La cuenta con mas eventos (el peor caso de una cuenta) y una con un pago ambiguo.
    ((mas_eventos,),) = _sql(
        "SELECT c.cuenta_id FROM (SELECT cuenta_canonica_id, count(*) AS n FROM evento_lifecycle "
        "GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 1) e JOIN cuenta_canonica c "
        "ON c.id = e.cuenta_canonica_id"
    )
    ((atribucion,),) = _sql(
        "SELECT atribucion_run_id FROM ejecucion_atribucion WHERE estado = 'EXITOSA' "
        "ORDER BY movimientos_evaluados DESC, id DESC LIMIT 1"
    )
    ((con_ambiguo, movimiento, cliente_ambiguo),) = _sql(
        "SELECT c.cuenta_id, a.movimiento_id, c.cliente_unico FROM atribucion_movimiento a "
        "JOIN ejecucion_atribucion e ON e.id = a.ejecucion_atribucion_id "
        "JOIN cuenta_canonica c ON c.id = a.cuenta_canonica_id "
        "WHERE e.atribucion_run_id = :atribucion AND a.clasificacion = 'AMBIGUA' "
        "ORDER BY a.candidatos DESC, a.movimiento_economico_canonico_id LIMIT 1",
        {"atribucion": atribucion},
    )
    elegidos = {
        "cuenta_con_mas_eventos": str(mas_eventos),
        "cuenta_con_un_pago_ambiguo": str(con_ambiguo),
        "pago_ambiguo": str(movimiento),
        "atribucion": str(atribucion),
    }
    de_una_cuenta = {
        "ultimas_gestiones_de_una_cuenta": f"/cuentas/{mas_eventos}/gestiones?por_pagina=50",
        "lifecycle_de_una_cuenta": f"/cuentas/{mas_eventos}/lifecycle?por_pagina=50",
        "promesas_vigentes_de_una_cuenta": f"/cuentas/{mas_eventos}/promesas?estado=VIGENTE",
        "pagos_atribuidos_de_una_cuenta": f"/cuentas/{con_ambiguo}/atribuciones?por_pagina=50",
        "cuenta_360": f"/cuentas/{mas_eventos}",
        "cuenta_360_con_un_pago_ambiguo": f"/cuentas/{con_ambiguo}",
        "atribuciones_de_un_pago": f"/movimientos/{movimiento}/atribuciones",
        "candidatas_de_los_pagos_de_un_cliente": (
            f"/atribuciones/{atribucion}/resultados?cliente_unico={cliente_ambiguo}"
        ),
    }
    globales = {
        "primera_pagina_de_pagos_ambiguos": (
            f"/atribuciones/{atribucion}/resultados?clasificacion=AMBIGUA&por_pagina=50"
        ),
        "atribuciones_de_la_cartera": "/atribuciones",
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

    todas = {**de_una_cuenta, **globales}
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
    with (destino / "planes_lifecycle.txt").open("w", encoding="utf-8") as salida:
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
            for nombre in de_una_cuenta
            if any(_escanea_grande(p["plan"]) for p in planes[nombre])
        },
        "seq_scan_en_consultas_globales": {
            nombre: [p["sql"][:160] for p in planes[nombre] if _escanea_grande(p["plan"])]
            for nombre in globales
        },
    }


def _escanea_grande(plan: str) -> bool:
    return any(f"Seq Scan on {tabla} " in plan + " " for tabla in TABLAS_GRANDES)


# --- el orquestador -------------------------------------------------------------------------------


def _exigir_bench() -> None:
    from sqlalchemy import make_url

    url = make_url(os.environ.get("MC_DATABASE_URL", ""))
    if not (url.database or "").endswith("_bench"):
        oculta = url.render_as_string(hide_password=True)
        sys.exit(f"MC_DATABASE_URL tiene que apuntar a una base *_bench, no a {oculta!r}.")


def _migrar() -> None:
    from alembic import command
    from alembic.config import Config as ConfigAlembic

    cfg = ConfigAlembic(str(RAIZ / "alembic.ini"))
    cfg.set_main_option("script_location", str(RAIZ / "migraciones"))
    command.upgrade(cfg, "head")


def _tamanos(tablas: tuple[str, ...]) -> dict:
    (base,) = _sql("SELECT pg_database_size(current_database())")
    filas = _sql(
        "SELECT c.relname, c.reltuples::bigint, pg_relation_size(c.oid), "
        "pg_indexes_size(c.oid), pg_total_relation_size(c.oid) FROM pg_class c "
        "WHERE c.relkind = 'r' AND c.relname = ANY(:tablas) ORDER BY 5 DESC",
        {"tablas": list(tablas)},
    )
    indices = _sql(
        "SELECT i.relname, t.relname, pg_relation_size(i.oid) FROM pg_index x "
        "JOIN pg_class i ON i.oid = x.indexrelid JOIN pg_class t ON t.oid = x.indrelid "
        "WHERE t.relname = ANY(:tablas) ORDER BY 3 DESC",
        {"tablas": list(tablas)},
    )
    return {
        "base_bytes": base[0],
        "tablas": [
            {
                "tabla": t,
                "filas_estimadas": n,
                "datos_bytes": d,
                "indices_bytes": i,
                "total_bytes": total,
            }
            for t, n, d, i, total in filas
        ],
        "indices": [{"indice": i, "tabla": t, "bytes": b} for i, t, b in indices],
    }


def _conteos() -> dict:
    (fila,) = _sql(
        "SELECT (SELECT count(*) FROM evento_lifecycle), (SELECT count(*) FROM gestion_cobranza), "
        "(SELECT count(*) FROM visita_campo), (SELECT count(*) FROM promesa_pago), "
        "(SELECT count(*) FROM convenio_cobranza), (SELECT count(*) FROM cuota_convenio), "
        "(SELECT count(*) FROM evento_lifecycle WHERE tipo_evento = 'GESTION_ANULADA'), "
        "(SELECT count(*) FROM evento_lifecycle WHERE tipo_evento IN "
        "('PROMESA_CANCELADA', 'CONVENIO_CANCELADO')), "
        "(SELECT count(*) FROM gestion_cobranza WHERE nivel_contacto = 'CONTACTO_TITULAR'), "
        "(SELECT count(*) FROM gestion_cobranza WHERE nivel_contacto = 'CONTACTO_TERCERO')"
    )
    nombres = (
        "eventos",
        "gestiones",
        "visitas",
        "promesas",
        "convenios",
        "cuotas",
        "anulaciones",
        "cancelaciones",
        "contactos_titular",
        "contactos_tercero",
    )
    return dict(zip(nombres, fila, strict=True))


def _ventanas() -> list[tuple[str, date, date]]:
    """Las ventanas con una interpretacion vigente de los pagos: su EXITOSA mas reciente."""
    return [
        (f"{desde:%Y-%m}", desde, hasta)
        for desde, hasta in _sql(
            "SELECT DISTINCT periodo_desde, periodo_hasta FROM ejecucion_motor_pagos "
            "WHERE estado = 'EXITOSA' ORDER BY 1"
        )
    ]


def _abrir_atribucion(desde: date) -> int:
    from motor_cartera.atribucion.ejecuciones import abrir
    from motor_cartera.config import config
    from motor_cartera.db.sesion import sesion
    from motor_cartera.motor_pagos.ejecuciones import Ventana

    with sesion() as s:
        ejecucion, _ = abrir(
            s,
            Ventana.del_periodo(config.despacho_id, config.cartera_id, desde),
            ventana_dias=config.atribucion_ventana_dias,
            zona=config.zona_horaria_fuente,
            max_intentos=config.worker_max_intentos,
            reusar=True,
        )
        s.commit()
        return ejecucion.id


def _abrir_evaluacion(as_of: date) -> int:
    from motor_cartera.config import config
    from motor_cartera.db.sesion import sesion
    from motor_cartera.evaluacion.ejecuciones import abrir

    with sesion() as s:
        ejecucion, _ = abrir(
            s,
            config.despacho_id,
            config.cartera_id,
            as_of,
            zona=config.zona_horaria_fuente,
            max_intentos=config.worker_max_intentos,
            reusar=True,
        )
        s.commit()
        return ejecucion.id


def _mib(valor: int | None) -> str:
    return "-" if valor is None else f"{valor / 2**20:,.1f} MiB"


def _num(valor, sufijo: str = "") -> str:
    return "-" if valor is None else f"{valor:,}{sufijo}"


def _markdown(reporte: dict) -> str:
    lineas = [
        "# Benchmark del lifecycle y de la atribucion",
        "",
        f"- Maquina: {reporte['maquina']}",
        f"- PostgreSQL: {reporte['postgresql']}",
        f"- Escenario: {reporte['escenario']}",
        f"- Intensidad del lifecycle: {reporte['intensidad']}",
        "",
        "## Carga (cargar-lifecycle)",
        "",
        "| Archivo | Eventos | Tiempo | Eventos/s | Memoria pico | WAL |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for archivo, medido in reporte["importaciones"].items():
        lineas.append(
            f"| {archivo} | {_num(medido.get('nuevos'))} | {medido.get('segundos')} s | "
            f"{_num(medido.get('eventos_por_segundo'))} | {medido.get('memoria_pico_mb')} MiB | "
            f"{medido.get('wal_mib')} MiB |"
        )
    total = reporte["total_importacion"]
    lineas += [
        f"| **Total** | **{_num(total['eventos'])}** | **{total['segundos']} s** | "
        f"**{_num(total['eventos_por_segundo'])}** | | |",
        "",
        f"Reimportar el primer archivo (nada nuevo): {reporte['reimportacion']['segundos']} s, "
        f"{reporte['reimportacion']['nuevos']} eventos nuevos y "
        f"{_num(reporte['reimportacion']['ya_registradas'])} ya registrados.",
        "",
        "## Atribucion (atribucion/v1, ventana de 30 dias)",
        "",
        "| Ventana | Pagos | Asociados | Ambiguos | Sin candidata | Candidatas | Tiempo | "
        "Pagos/s | Memoria pico |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for periodo, medido in reporte["atribuciones"].items():
        lineas.append(
            f"| {periodo} | {_num(medido['movimientos_evaluados'])} | "
            f"{_num(medido['asociados'])} | {_num(medido['ambiguos'])} | "
            f"{_num(medido['sin_candidato'])} | {_num(medido['candidatos'])} | "
            f"{medido['segundos']} s | {_num(medido['pagos_por_segundo'])} | "
            f"{medido['memoria_pico_mb']} MiB |"
        )
    e = reporte["evaluacion"]
    lineas += [
        "",
        f"## Evaluacion de promesas al {e['as_of']}",
        "",
        f"{_num(e['promesas_evaluadas'])} promesas en {e['segundos']} s "
        f"({_num(e['promesas_por_segundo'])} por segundo): {_num(e['cumplidas'])} cumplidas, "
        f"{_num(e['parciales'])} parciales, {_num(e['incumplidas'])} incumplidas, "
        f"{_num(e['canceladas'])} canceladas, {_num(e['pendientes'])} pendientes y "
        f"{_num(e['no_evaluables'])} no evaluables.",
        "",
        "## Lo que quedo en la base",
        "",
        "| Conteo | Filas |",
        "|---|---:|",
        *(f"| {k} | {_num(v)} |" for k, v in reporte["conteos"].items()),
        "",
        "| Tabla | Filas | Datos | Indices | Total |",
        "|---|---:|---:|---:|---:|",
        *(
            f"| {t['tabla']} | {_num(t['filas_estimadas'])} | {_mib(t['datos_bytes'])} | "
            f"{_mib(t['indices_bytes'])} | {_mib(t['total_bytes'])} |"
            for t in reporte["tamanos_al_final"]["tablas"]
        ),
        "",
        f"La base crecio de {_mib(reporte['tamanos_antes']['base_bytes'])} a "
        f"{_mib(reporte['tamanos_al_final']['base_bytes'])}.",
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
        "Seq Scan sobre una tabla grande en una consulta de una cuenta: "
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
    escenario, destino = argumentos.escenario.resolve(), argumentos.destino.resolve()
    manifiesto = json.loads((escenario / "escenario.json").read_text(encoding="utf-8"))
    archivos_dir = destino / "lifecycle"
    bitacora = Bitacora(destino / "benchmark_lifecycle.etapas.json", argumentos.reanudar)
    _migrar()
    (cortes,) = _sql("SELECT count(*) FROM corte_canonico")[0]
    if cortes < len(manifiesto["cortes"]):
        sys.exit(
            f"La base tiene {cortes} cortes canonicos y el escenario "
            f"{len(manifiesto['cortes'])}: carga el escenario con benchmark_motor_pagos.py."
        )
    if "tamanos_antes" not in bitacora:
        _vacuum(TABLAS_DEL_LIFECYCLE + TABLAS_DE_LA_ATRIBUCION)
        bitacora.anotar("tamanos_antes", _tamanos(TABLAS_DEL_LIFECYCLE + TABLAS_DE_LA_ATRIBUCION))

    print("1. el lifecycle del escenario", flush=True)
    if "escribir" not in bitacora:
        bitacora.anotar(
            "escribir",
            _hijo(
                "escribir",
                "--escenario",
                str(escenario),
                "--destino",
                str(archivos_dir),
                "--intensidad",
                str(argumentos.intensidad),
            ),
        )
    escrito = bitacora["escribir"]
    print(f"   {escrito['eventos']:,} eventos en {escrito['segundos']} s", flush=True)

    print("2. la carga", flush=True)
    for archivo in escrito["archivos"]:
        clave = f"importar:{archivo['archivo']}"
        if clave not in bitacora:
            medido = bitacora.anotar(
                clave, _hijo("importar", "--archivo", str(archivos_dir / archivo["archivo"]))
            )
            if medido["problemas"]:
                sys.exit(f"{archivo['archivo']}: {medido['primeros_problemas']}")
        medido = bitacora[clave]
        print(
            f"   {archivo['archivo']}: {medido['nuevos']:,} nuevos en {medido['segundos']} s "
            f"({medido['eventos_por_segundo']:,}/s)",
            flush=True,
        )
    if "tamanos_despues_de_la_carga" not in bitacora:
        bitacora.anotar("vacuum_de_la_carga_s", _vacuum(TABLAS_DEL_LIFECYCLE))
        bitacora.anotar(
            "tamanos_despues_de_la_carga",
            _tamanos(TABLAS_DEL_LIFECYCLE + TABLAS_DE_LA_ATRIBUCION),
        )

    print("3. la atribucion", flush=True)
    for periodo, desde, _ in _ventanas():
        clave = f"atribuir:{periodo}"
        if clave not in bitacora:
            ejecucion_id = _abrir_atribucion(desde)
            bitacora.anotar(clave, _hijo("atribuir", "--ejecucion", str(ejecucion_id)))
        medido = bitacora[clave]
        print(
            f"   {periodo}: {medido['movimientos_evaluados']:,} pagos, {medido['estado']} "
            f"{medido['resultado']} en {medido['segundos']} s",
            flush=True,
        )

    print("4. la evaluacion de las promesas", flush=True)
    if "evaluar" not in bitacora:
        as_of = date.fromisoformat(manifiesto["cortes"][-1]["fecha_corte"])
        bitacora.anotar("evaluar", _hijo("evaluar", "--ejecucion", str(_abrir_evaluacion(as_of))))
    if "tamanos_al_final" not in bitacora:
        bitacora.anotar("vacuum_de_la_atribucion_s", _vacuum(TABLAS_DE_LA_ATRIBUCION))
        bitacora.anotar(
            "tamanos_al_final", _tamanos(TABLAS_DEL_LIFECYCLE + TABLAS_DE_LA_ATRIBUCION)
        )
        bitacora.anotar("conteos", _conteos())

    print("5. reimportar el primer archivo", flush=True)
    if "reimportacion" not in bitacora:
        primero = archivos_dir / escrito["archivos"][0]["archivo"]
        bitacora.anotar("reimportacion", _hijo("importar", "--archivo", str(primero)))

    print("6. las consultas", flush=True)
    if "consultas" not in bitacora:
        bitacora.anotar("consultas", _hijo("consultar", "--destino", str(destino)))

    importaciones = {
        a["archivo"]: bitacora[f"importar:{a['archivo']}"] for a in escrito["archivos"]
    }
    eventos = sum(m["nuevos"] for m in importaciones.values())
    segundos = round(sum(m["segundos"] for m in importaciones.values()), 3)
    ((version,),) = _sql("SELECT version()")
    reporte = {
        "maquina": f"{platform.system()} {platform.release()}, {os.cpu_count()} CPU, "
        f"Python {platform.python_version()}",
        "postgresql": version.split(",")[0],
        "escenario": f"{manifiesto['cuentas_iniciales']:,} cuentas iniciales, "
        f"{len(manifiesto['cortes'])} cortes, semilla {manifiesto['semilla']}",
        "intensidad": argumentos.intensidad,
        "escritura": escrito,
        "importaciones": importaciones,
        "total_importacion": {
            "eventos": eventos,
            "segundos": segundos,
            "eventos_por_segundo": round(eventos / segundos) if segundos else None,
        },
        "reimportacion": bitacora["reimportacion"],
        "atribuciones": {periodo: bitacora[f"atribuir:{periodo}"] for periodo, _, _ in _ventanas()},
        "evaluacion": bitacora["evaluar"],
        "conteos": bitacora["conteos"],
        "tamanos_antes": bitacora["tamanos_antes"],
        "tamanos_despues_de_la_carga": bitacora["tamanos_despues_de_la_carga"],
        "tamanos_al_final": bitacora["tamanos_al_final"],
        "consultas": bitacora["consultas"],
    }
    destino.mkdir(parents=True, exist_ok=True)
    (destino / "benchmark_lifecycle.json").write_text(
        json.dumps(reporte, indent=1, ensure_ascii=False, default=str), encoding="utf-8"
    )
    (destino / "benchmark_lifecycle.md").write_text(_markdown(reporte), encoding="utf-8")
    print(f"Reporte: {destino / 'benchmark_lifecycle.md'}")


if __name__ == "__main__":
    lector = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = lector.add_subparsers(dest="etapa")
    e = sub.add_parser("escribir")
    e.add_argument("--escenario", type=Path, required=True)
    e.add_argument("--destino", type=Path, required=True)
    e.add_argument("--intensidad", type=float, required=True)
    i = sub.add_parser("importar")
    i.add_argument("--archivo", type=Path, required=True)
    a = sub.add_parser("atribuir")
    a.add_argument("--ejecucion", type=int, required=True)
    v = sub.add_parser("evaluar")
    v.add_argument("--ejecucion", type=int, required=True)
    c = sub.add_parser("consultar")
    c.add_argument("--destino", type=Path, required=True)
    lector.add_argument("--escenario", type=Path, help="El directorio del escenario ya cargado.")
    lector.add_argument("--destino", type=Path, help="Donde van los archivos y el reporte.")
    lector.add_argument(
        "--intensidad",
        type=float,
        default=0.5,
        help="Escala las gestiones por periodo (0.5 en el XL: unos 2.5 millones de eventos).",
    )
    lector.add_argument("--reanudar", action="store_true")
    leidos = lector.parse_args()
    if leidos.etapa == "escribir":
        print(json.dumps(escribir(leidos.escenario, leidos.destino, leidos.intensidad)))
    elif leidos.etapa == "importar":
        print(json.dumps(importar(leidos.archivo), default=str))
    elif leidos.etapa == "atribuir":
        print(json.dumps(atribuir(leidos.ejecucion), default=str))
    elif leidos.etapa == "evaluar":
        print(json.dumps(evaluar(leidos.ejecucion), default=str))
    elif leidos.etapa == "consultar":
        print(json.dumps(consultar(leidos.destino), default=str))
    else:
        if leidos.escenario is None or leidos.destino is None:
            lector.error("--escenario y --destino son obligatorios.")
        main(leidos)
