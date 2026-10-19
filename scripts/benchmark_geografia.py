"""Benchmark de geografia/v1 y territorial/v2: cuanto tarda resolver la geografia de un corte grande
contra la referencia municipal oficial del INEGI y asignarlo a bases operativas, por fase, cuanto
crece la base, y como responden las consultas de una cuenta, de un municipio y de una base, contra
PostgreSQL con PostGIS. No corre en el CI: se pide a mano, sobre una base *_bench que ya tiene el
escenario XL (el de benchmark_motor_pagos.py), y con el archivo oficial descargado:

    python scripts/benchmark_geografia.py --destino <dir> --referencia <mg_2025_integrado.zip>
    python scripts/benchmark_geografia.py --destino <dir> --referencia <zip> --reanudar

Lo que hace, cada etapa pesada en su propio proceso para que su memoria pico sea solo suya:

1. sube la base a la cabeza de las migraciones (la 0012, con PostGIS) e inventaria lo que hay; no
   reconstruye nada de lo que ya estaba: ni la historia, ni los pagos, ni el lifecycle, ni las
   decisiones;
2. importa la referencia oficial (el Marco Geoestadistico Integrado 2025, por su manifiesto), o
   reutiliza la version que ya este PUBLICADA;
3. muestrea con PostGIS puntos dentro de cada municipio y fuera de la referencia;
4. genera un corte de cartera/v2 de unas 500,000 cuentas SINTETICAS, una semana despues del ultimo
   corte del escenario, con coordenadas sinteticas en las proporciones de omision, y lo ingiere y
   materializa en el modelo historico (si no esta ya);
5. resuelve su geografia con geografia/v1, con el cronometro por fase, su memoria pico y su WAL;
6. publica una version de bases sinteticas (tres por entidad en cuatro de las cinco entidades del
   generador, y una inactiva en la quinta) y asigna el corte con territorial/v2;
7. corre VACUUM ANALYZE y mide cuanto ocupan las tablas nuevas y sus indices, y cuanto crecio la
   base;
8. guarda con EXPLAIN (ANALYZE, BUFFERS) el plan del punto en poligono (como lo hace el motor), el
   de un punto, y el de cada sentencia de las consultas de una cuenta, de un municipio, de una base
   y de las anomalias, tal como las emite la API, y dice si alguna recorre una tabla grande.

MC_DATABASE_URL tiene que apuntar a una base *_bench; el almacen es MC_SOURCE_STORE_ROOT, o
<destino>/almacen. Cada etapa se anota en una bitacora (benchmark_geografia.etapas.json) en cuanto
termina; con --reanudar no se repite ninguna. Ninguna salida lleva una coordenada de una cuenta:
solo conteos, tiempos y planes (los planes llevan los parametros de las consultas, que son
identificadores, no ubicaciones).
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
from benchmark_historia import historiar, ingerir  # noqa: E402
from benchmark_lifecycle import _exigir_bench, _migrar, _tamanos, _wal, _wal_desde  # noqa: E402
from benchmark_motor_pagos import Bitacora, _sql, _vacuum  # noqa: E402

REPETICIONES = 25
CUENTAS = 500_000
SEMILLA = 31416
SEMILLA_MUESTRA = 7
POR_MUNICIPIO = 20
FUERA = 200
REFERENCIA = "inegi-mg-2025-municipal"
TABLAS_NUEVAS = (
    "version_geografia",
    "municipio_geografico",
    "ejecucion_geografia",
    "resultado_geografia_cuenta",
    "version_bases_operativas",
    "base_operativa",
    "cobertura_base_municipio",
    "ejecucion_territorial_v2",
    "asignacion_territorial_v2",
)
TABLAS_GRANDES = (
    "resultado_geografia_cuenta",
    "asignacion_territorial_v2",
    "snapshot_cuenta",
    "cuenta_canonica",
    "resultado_decision_v2",
    "decision_cuenta",
)
ENTIDAD_SIN_BASE = "09"
"""La entidad cuyas cuentas no cubre ninguna base activa: solo una inactiva."""


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


def importar_referencia(ruta: Path) -> dict:
    from motor_cartera.geografia import referencia

    antes = _wal()
    inicio = time.perf_counter()
    recuperada = False
    try:
        version = referencia.importar(ruta, referencia=REFERENCIA)
    except referencia.GeografiaYaImportada as exc:
        version, recuperada = exc.previa, True
    segundos = time.perf_counter() - inicio
    return {
        "geografia_version_id": str(version.geografia_version_id),
        "producto": version.producto,
        "edicion": version.edicion,
        "capa": version.capa,
        "sha256": version.sha256,
        "srid_origen": version.srid_origen,
        "estado": str(version.estado),
        "municipios": version.municipios,
        "reparadas": version.reparadas,
        "detalle": version.detalle,
        "segundos": None if recuperada else round(segundos, 3),
        "memoria_pico_mb": None if recuperada else memoria_pico_mb(),
        "wal_mib": None if recuperada else _wal_desde(antes),
        "recuperado": recuperada,
    }


def muestrear(geografia_version_id: str, destino: Path) -> dict:
    from uuid import UUID

    from motor_cartera.db.sesion import sesion
    from motor_cartera.geografia import consultas, muestra

    inicio = time.perf_counter()
    with sesion() as s:
        version = consultas.version(s, UUID(geografia_version_id))
        datos = muestra.muestrear(
            s, version, por_municipio=POR_MUNICIPIO, fuera=FUERA, semilla=SEMILLA_MUESTRA
        )
    ruta = muestra.escribir(datos, destino / "puntos.json")
    return {
        "ruta": str(ruta),
        "municipios_con_puntos": len(datos["municipios"]),
        "puntos": sum(len(p) for p in datos["municipios"].values()),
        "fuera": len(datos["fuera"]),
        "semilla": SEMILLA_MUESTRA,
        "versiones": datos["versiones"],
        "segundos": round(time.perf_counter() - inicio, 3),
    }


def generar_corte(destino: Path, puntos: Path, fecha: str, formato: str) -> dict:
    from motor_cartera.generador.coordenadas import Coordenadas, MuestraDePuntos, Proporciones
    from motor_cartera.generador.oficial import escribir_cartera, estado_inicial

    corte = date.fromisoformat(fecha)
    coordenadas = Coordenadas(MuestraDePuntos.leer(puntos), Proporciones())
    inicio = time.perf_counter()
    estado = estado_inicial(CUENTAS, semilla=SEMILLA, fecha_corte=corte)
    ruta = destino / f"cartera_geografia_{fecha}.{formato}"
    escrito = escribir_cartera(
        estado, ruta, semilla=SEMILLA, fecha_corte=corte, coordenadas=coordenadas
    )
    return {
        "ruta": str(ruta),
        "cuentas": escrito.filas,
        "coordenadas": escrito.coordenadas,
        "manifiesto_coordenadas": coordenadas.manifiesto(),
        "segundos": round(time.perf_counter() - inicio, 3),
        "memoria_pico_mb": memoria_pico_mb(),
    }


def geolocalizar(corte_id: str, geografia_version_id: str) -> dict:
    """Abre la geografia del corte y la resuelve como su trabajo, con el cronometro por fase. Si
    esas mismas entradas ya se publicaron (la corrida se detuvo entre publicar y anotarlo), la
    reporta como esta, sin tiempos; si quedo una EN_PROCESO, la continua."""
    from uuid import UUID

    from motor_cartera.config import config
    from motor_cartera.db.modelos import EjecucionGeografia
    from motor_cartera.db.sesion import sesion
    from motor_cartera.geografia import ejecuciones as motor
    from motor_cartera.ingesta.fuente_oficial import Cronometro

    recuperada = False
    try:
        with sesion() as s:
            ejecucion = motor.abrir(
                s,
                UUID(corte_id),
                UUID(geografia_version_id),
                max_intentos=config.worker_max_intentos,
            )
            s.commit()
            ejecucion_id = ejecucion.id
    except motor.GeografiaYaGenerada as exc:
        ejecucion_id, recuperada = exc.previa.id, True
    except motor.GeografiaEnProceso as exc:
        ejecucion_id = exc.activa.id
    antes = _wal()
    cronometro = Cronometro()
    inicio = time.perf_counter()
    if not recuperada:
        motor.geolocalizar(ejecucion_id, cronometro=cronometro)
    segundos = time.perf_counter() - inicio
    with sesion() as s:
        e = s.get_one(EjecucionGeografia, ejecucion_id)
        resultado = {
            "ejecucion_id": e.id,
            "geografia_run_id": str(e.geografia_run_id),
            "estado": str(e.estado),
            "resultado": e.resultado,
            "firma_inputs": e.firma_inputs,
            "cuentas_objetivo": e.cuentas_objetivo,
            "conteos": {
                c: getattr(e, c)
                for c in (
                    "validas",
                    "sin_coordenadas",
                    "incompletas",
                    "invalidas",
                    "fuera_cobertura",
                    "ambiguas",
                    "discrepantes",
                )
            },
            "distribucion": e.distribucion,
            "detalle": e.detalle,
        }
    if resultado["estado"] != "EXITOSA":
        sys.exit(f"La geografia termino {resultado['estado']}: {resultado['detalle']}")
    medicion = (
        {"segundos": None, "fases": {}, "memoria_pico_mb": None, "wal_mib": None}
        if recuperada
        else {
            "segundos": round(segundos, 3),
            "cuentas_por_segundo": round(resultado["cuentas_objetivo"] / segundos),
            "fases": {nombre: round(s_, 3) for nombre, s_ in cronometro.fases.items()},
            "memoria_pico_mb": memoria_pico_mb(),
            "wal_mib": _wal_desde(antes),
        }
    )
    return {**resultado, **medicion, "recuperado": recuperada}


def asignar(corte_id: str, geografia_run_id: str, bases_version_id: str) -> dict:
    from uuid import UUID

    from motor_cartera.config import config
    from motor_cartera.db.modelos import EjecucionTerritorialV2
    from motor_cartera.db.sesion import sesion
    from motor_cartera.ingesta.fuente_oficial import Cronometro
    from motor_cartera.territorial_v2 import ejecuciones as motor

    recuperada = False
    try:
        with sesion() as s:
            ejecucion = motor.abrir(
                s,
                UUID(corte_id),
                UUID(geografia_run_id),
                UUID(bases_version_id),
                max_intentos=config.worker_max_intentos,
            )
            s.commit()
            ejecucion_id = ejecucion.id
    except motor.TerritorialV2YaGenerado as exc:
        ejecucion_id, recuperada = exc.previa.id, True
    except motor.TerritorialV2EnProceso as exc:
        ejecucion_id = exc.activa.id
    antes = _wal()
    cronometro = Cronometro()
    inicio = time.perf_counter()
    if not recuperada:
        motor.asignar(ejecucion_id, cronometro=cronometro)
    segundos = time.perf_counter() - inicio
    with sesion() as s:
        e = s.get_one(EjecucionTerritorialV2, ejecucion_id)
        resultado = {
            "ejecucion_id": e.id,
            "territorial_run_id": str(e.territorial_run_id),
            "estado": str(e.estado),
            "resultado": e.resultado,
            "firma_inputs": e.firma_inputs,
            "hash_politica": e.hash_politica,
            "cuentas_objetivo": e.cuentas_objetivo,
            "conteos": {
                c: getattr(e, c)
                for c in (
                    "asignadas",
                    "revision_geografica",
                    "sin_base",
                    "fuera_cobertura",
                    "ambiguas",
                )
            },
            "distribucion": e.distribucion,
            "detalle": e.detalle,
        }
    if resultado["estado"] != "EXITOSA":
        sys.exit(f"territorial/v2 termino {resultado['estado']}: {resultado['detalle']}")
    medicion = (
        {"segundos": None, "fases": {}, "memoria_pico_mb": None, "wal_mib": None}
        if recuperada
        else {
            "segundos": round(segundos, 3),
            "cuentas_por_segundo": round(resultado["cuentas_objetivo"] / segundos),
            "fases": {nombre: round(s_, 3) for nombre, s_ in cronometro.fases.items()},
            "memoria_pico_mb": memoria_pico_mb(),
            "wal_mib": _wal_desde(antes),
        }
    )
    return {**resultado, **medicion, "recuperado": recuperada}


def planes_espaciales(destino: Path, ejecucion_id: int, version_id: int) -> dict:
    """El plan del punto en poligono como lo hace el motor (los municipios contra el indice GiST de
    los puntos, en una tabla temporal); el de la alternativa, punto por punto contra el indice GiST
    de los municipios (OFFSET 0 impide que el planeador la convierta en la primera); y el de un
    punto."""
    from sqlalchemy import text

    from motor_cartera.db.sesion import crear_motor

    planes = {}
    with crear_motor().connect() as conexion:
        conexion.execute(
            text(
                "CREATE TEMP TABLE geo_punto AS SELECT cuenta_canonica_id AS cuenta, punto "
                "FROM resultado_geografia_cuenta WHERE ejecucion_geografia_id = :e "
                "AND punto IS NOT NULL"
            ),
            {"e": ejecucion_id},
        )
        conexion.execute(text("CREATE INDEX ON geo_punto USING gist (punto)"))
        conexion.execute(text("CREATE UNIQUE INDEX ON geo_punto (cuenta)"))
        conexion.execute(text("ANALYZE geo_punto"))
        planes["punto_en_poligono"] = _plan(
            conexion,
            "SELECT p.cuenta, m.id, m.cvegeo FROM municipio_geografico m JOIN geo_punto p "
            "ON ST_Covers(m.geometria, p.punto) WHERE m.version_geografia_id = :v",
            {"v": version_id},
        )
        planes["punto_por_punto"] = _plan(
            conexion,
            "SELECT p.cuenta, m.id, m.cvegeo FROM geo_punto p CROSS JOIN LATERAL (SELECT m.id, "
            "m.cvegeo FROM municipio_geografico m WHERE m.version_geografia_id = :v "
            "AND ST_Covers(m.geometria, p.punto) OFFSET 0) m",
            {"v": version_id},
        )
        # El punto de una cuenta, tomado por su indice dentro de la consulta: ni su valor sale en el
        # plan, ni el tiempo de elegirlo pesa en el de buscarlo.
        planes["un_punto"] = _plan(
            conexion,
            "SELECT cvegeo FROM municipio_geografico WHERE version_geografia_id = :v "
            "AND ST_Covers(geometria, (SELECT punto FROM geo_punto WHERE cuenta = "
            "(SELECT min(cuenta) FROM geo_punto)))",
            {"v": version_id},
        )
        conexion.rollback()
    destino.mkdir(parents=True, exist_ok=True)
    with (destino / "planes_geografia_espaciales.txt").open("w", encoding="utf-8") as salida:
        for nombre, plan in planes.items():
            salida.write(f"### {nombre}\n{plan['sql']}\n\n{plan['plan']}\n\n")
    return {
        nombre: {
            "milisegundos": plan["milisegundos"],
            "usa_gist_de_los_puntos": "geo_punto_punto_idx" in plan["plan"],
            "usa_gist_de_los_municipios": "ix_municipio_geografico_geometria" in plan["plan"],
            "seq_scan_de_municipios": "Seq Scan on municipio_geografico" in plan["plan"],
        }
        for nombre, plan in planes.items()
    }


def _plan(conexion, sentencia: str, parametros: dict) -> dict:
    from sqlalchemy import text

    filas = conexion.execute(text("EXPLAIN (ANALYZE, BUFFERS) " + sentencia), parametros).all()
    plan = "\n".join(f[0] for f in filas)
    tiempo = next(
        (float(f[0].split(":")[1].split()[0]) for f in filas if f[0].startswith("Execution Time")),
        None,
    )
    return {"sql": " ".join(sentencia.split()), "plan": plan, "milisegundos": tiempo}


def consultar(destino: Path, geografia_run_id: str, territorial_run_id: str) -> dict:
    """Las consultas por la API, con un cliente HTTP en el mismo proceso: el tiempo incluye la
    serializacion. Cada una se repite y se reporta su mediana, su p95 y su maximo; despues se
    guarda el plan de cada sentencia que emitio."""
    from fastapi.testclient import TestClient
    from sqlalchemy import event

    from motor_cartera.api.app import crear_app
    from motor_cartera.config import Config
    from motor_cartera.db.sesion import crear_motor

    clave = "benchmark-geografia-" + "x" * 24
    cliente = TestClient(
        crear_app(Config(app_env="development", api_key=clave)), headers={"X-API-Key": clave}
    )
    # Una cuenta asignada, su municipio y su base; la base con mas cuentas.
    ((cuenta, cvegeo, codigo),) = _sql(
        "SELECT c.cuenta_id, m.cvegeo, b.codigo FROM asignacion_territorial_v2 a "
        "JOIN ejecucion_territorial_v2 e ON e.id = a.ejecucion_territorial_v2_id "
        "JOIN cuenta_canonica c ON c.id = a.cuenta_canonica_id "
        "JOIN municipio_geografico m ON m.id = a.municipio_geografico_id "
        "JOIN base_operativa b ON b.id = a.base_operativa_id "
        "WHERE e.territorial_run_id = :t AND a.estado_asignacion = 'ASIGNADA' "
        "ORDER BY a.cuenta_canonica_id LIMIT 1",
        {"t": territorial_run_id},
    )
    geo = f"/geografia/ejecuciones/{geografia_run_id}"
    tv2 = f"/territorial-v2/{territorial_run_id}"
    rutas = {
        "geografia_de_una_cuenta": f"/cuentas/{cuenta}/geografia",
        "historial_territorial_de_una_cuenta": f"/cuentas/{cuenta}/territorial-v2",
        "cuenta_360": f"/cuentas/{cuenta}",
        "cuentas_de_un_municipio": f"{geo}/resultados?municipio={cvegeo}&por_pagina=50",
        "anomalias_discrepancia": f"{geo}/resultados?estado_geografia=DISCREPANCIA_MUNICIPIO"
        "&por_pagina=50",
        "anomalias_fuera": f"{geo}/resultados?estado_geografia=FUERA_DE_REFERENCIA&por_pagina=50",
        "cuentas_de_una_base": f"{tv2}/resultados?base={codigo}&por_pagina=50",
        "cuentas_de_una_base_pagina_10": f"{tv2}/resultados?base={codigo}&por_pagina=50&pagina=10",
        "sin_base": f"{tv2}/resultados?estado=SIN_BASE_ELEGIBLE&por_pagina=50",
        "ejecucion_geografia": geo,
        "ejecucion_territorial": tv2,
    }

    def medir(ruta: str) -> dict:
        tiempos = []
        for _ in range(REPETICIONES):
            inicio = time.perf_counter()
            respuesta = cliente.get(ruta)
            tiempos.append((time.perf_counter() - inicio) * 1000)
            if respuesta.status_code != 200:
                sys.exit(f"{ruta}: {respuesta.status_code} {respuesta.text[:300]}")
            for llave in ('"latitud', '"longitud', '"punto"'):
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
                    plan = "\n".join(fila[0] for fila in cursor.fetchall())
                    planes[nombre].append({"sql": " ".join(sentencia.split()), "plan": plan})
            conexion.rollback()
    destino.mkdir(parents=True, exist_ok=True)
    with (destino / "planes_geografia.txt").open("w", encoding="utf-8") as salida:
        for nombre, sentencias in planes.items():
            for i, sentencia in enumerate(sentencias, start=1):
                salida.write(f"### {nombre} ({i} de {len(sentencias)})\n{sentencia['sql']}\n\n")
                salida.write(sentencia["plan"] + "\n\n")
    return {
        "repeticiones": REPETICIONES,
        "tiempos": tiempos,
        "sentencias": {nombre: len(p) for nombre, p in planes.items()},
        "seq_scan_sobre_tablas_grandes": {
            nombre: [p["sql"][:160] for p in planes[nombre] if _escanea_grande(p["plan"])]
            for nombre in rutas
            if any(_escanea_grande(p["plan"]) for p in planes[nombre])
        },
    }


def _escanea_grande(plan: str) -> bool:
    return any(f"Seq Scan on {tabla} " in plan + " " for tabla in TABLAS_GRANDES)


# --- el orquestador -------------------------------------------------------------------------------


def _bases_sinteticas(puntos: Path) -> dict:
    """Tres bases por entidad del generador, en el punto de la muestra de sus tres municipios mas
    poblados, cada una cubriendo todos los municipios de su entidad; la entidad ENTIDAD_SIN_BASE
    solo tiene una base inactiva. Puntos sinteticos de la cartografia, no oficinas reales."""
    from motor_cartera.fuentes.geografia import catalogo
    from motor_cartera.generador.oficial import PESO_ENTIDAD

    muestra = json.loads(puntos.read_text(encoding="ascii"))["municipios"]
    bases = []
    for cve in sorted(PESO_ENTIDAD):
        entidad = catalogo().entidad(cve)
        claves = sorted(
            cve + m.cve_municipio for m in entidad.municipios if cve + m.cve_municipio in muestra
        )
        poblados = sorted(entidad.municipios, key=lambda m: (-(m.poblacion or 0), m.cve_municipio))
        sedes = [cve + m.cve_municipio for m in poblados if cve + m.cve_municipio in muestra]
        cuantas, activa = (1, False) if cve == ENTIDAD_SIN_BASE else (3, True)
        for numero, sede in enumerate(sedes[:cuantas], start=1):
            x, y = muestra[sede][0]
            bases.append(
                {
                    "codigo": f"BASE_{cve}_{numero}",
                    "nombre": f"Base sintetica {numero} de la entidad {cve}",
                    "lat": y,
                    "lon": x,
                    "activa": activa,
                    "municipios": claves,
                }
            )
    return {"nombre": "Bases sinteticas del benchmark de geografia", "bases": bases}


def _inventario() -> dict:
    (fila,) = _sql(
        "SELECT (SELECT count(*) FROM corte_canonico), (SELECT count(*) FROM snapshot_cuenta), "
        "(SELECT count(*) FROM cuenta_canonica), (SELECT count(*) FROM ejecucion_decision_v2), "
        "(SELECT count(*) FROM evento_lifecycle), (SELECT max(fecha_corte) FROM corte_canonico)"
    )
    nombres = (
        "cortes",
        "snapshots",
        "cuentas_canonicas",
        "decisiones_v2",
        "eventos_lifecycle",
        "ultimo_corte",
    )
    return dict(zip(nombres, fila, strict=True))


def _mib(valor: int | None) -> str:
    return "-" if valor is None else f"{valor / 2**20:,.1f} MiB"


def _num(valor, sufijo: str = "") -> str:
    return "-" if valor is None else f"{valor:,}{sufijo}"


def _fases(etapa: dict) -> list[str]:
    cuentas = etapa["cuentas_objetivo"]
    return [
        "| Fase | Segundos | Cuentas por segundo |",
        "|---|---:|---:|",
        *(
            f"| {fase} | {segundos} | {_num(round(cuentas / segundos) if segundos else None)} |"
            for fase, segundos in etapa["fases"].items()
        ),
    ]


def _markdown(reporte: dict) -> str:
    r, g, t = reporte["referencia"], reporte["geografia"], reporte["territorial"]
    lineas = [
        "# Benchmark de geografia/v1 y territorial/v2",
        "",
        f"- Maquina: {reporte['maquina']}",
        f"- PostgreSQL: {reporte['postgresql']}; PostGIS {reporte['versiones']['postgis']}, GEOS "
        f"{reporte['versiones']['geos']}, PROJ {reporte['versiones']['proj']}",
        f"- Corte: {reporte['corte']['fecha_corte']} ({_num(g['cuentas_objetivo'])} cuentas "
        "sinteticas, con coordenadas sinteticas en las proporciones de omision)",
        "",
        "## La referencia oficial",
        "",
        f"{r['producto']} ({r['edicion']}), capa {r['capa']}: {_num(r['municipios'])} municipios, "
        f"{r['reparadas']} reparadas, sistema de origen EPSG:{r['srid_origen']}. Importada en "
        f"{r['segundos'] if r['segundos'] is not None else '- (ya estaba)'} s, memoria pico "
        f"{r['memoria_pico_mb'] or '-'} MiB, WAL {r['wal_mib'] or '-'} MiB.",
        "",
        f"Muestra: {_num(reporte['muestra']['puntos'])} puntos en "
        f"{_num(reporte['muestra']['municipios_con_puntos'])} municipios y "
        f"{reporte['muestra']['fuera']} fuera, en {reporte['muestra']['segundos']} s.",
        "",
        "Coordenadas del corte generado: "
        + ", ".join(f"{k} {_num(v)}" for k, v in reporte["generado"]["coordenadas"].items())
        + ".",
        "",
        "## geografia/v1",
        "",
        f"{_num(g['cuentas_objetivo'])} cuentas en {g['segundos']} s "
        f"({_num(g.get('cuentas_por_segundo'))} por segundo), memoria pico del proceso "
        f"{g['memoria_pico_mb']} MiB, WAL {g['wal_mib']} MiB.",
        "",
        "| Estado | Cuentas |",
        "|---|---:|",
        *(f"| {k} | {_num(v)} |" for k, v in g["distribucion"]["estado_geografia"].items()),
        "",
        "Comparacion con la fuente: "
        + ", ".join(f"{k} {_num(v)}" for k, v in g["distribucion"]["comparacion_municipio"].items())
        + ". Motivos: "
        + ", ".join(f"{k} {_num(v)}" for k, v in g["distribucion"]["motivos"].items())
        + ".",
        "",
        *_fases(g),
        "",
        "## territorial/v2",
        "",
        f"{_num(t['cuentas_objetivo'])} cuentas en {t['segundos']} s "
        f"({_num(t.get('cuentas_por_segundo'))} por segundo), memoria pico del proceso "
        f"{t['memoria_pico_mb']} MiB, WAL {t['wal_mib']} MiB; "
        f"{len(reporte['bases']['bases'])} bases ("
        f"{sum(1 for b in reporte['bases']['bases'] if b['activa'])} activas).",
        "",
        "| Estado | Cuentas |",
        "|---|---:|",
        *(f"| {k} | {_num(v)} |" for k, v in t["distribucion"]["estado_asignacion"].items()),
        "",
        "Distribucion por base (un diagnostico, no un balanceo):",
        "",
        "| Base | Cuentas | Distancia mediana |",
        "|---|---:|---:|",
        *(
            f"| {codigo} | {_num(b['cuentas'])} | {b['distancia_mediana_m']} m |"
            for codigo, b in t["distribucion"]["por_base"].items()
        ),
        "",
        *_fases(t),
        "",
        "## Lo que ocupa",
        "",
        "| Tabla | Filas | Datos | Indices | Total | Bytes por fila |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for tabla in reporte["tamanos_despues"]["tablas"]:
        filas = tabla["filas_estimadas"] or 0
        por_fila = round(tabla["total_bytes"] / filas) if filas > 0 else None
        lineas.append(
            f"| {tabla['tabla']} | {_num(filas)} | {_mib(tabla['datos_bytes'])} | "
            f"{_mib(tabla['indices_bytes'])} | {_mib(tabla['total_bytes'])} | {_num(por_fila)} |"
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
        f"{_mib(reporte['tamanos_despues']['base_bytes'])} con todo lo de este benchmark (el "
        "corte nuevo, su historia, la referencia, la geografia y territorial/v2).",
        "",
        "## El indice espacial",
        "",
        "`punto_en_poligono` es como lo hace geografia/v1: recorre los municipios y entra por el "
        "indice GiST de los puntos, asi que cada poligono se prepara una vez. `punto_por_punto` es "
        "la alternativa: cada punto contra el indice GiST de los municipios. `un_punto`, la "
        "consulta de una sola coordenada.",
        "",
        "| Plan | Milisegundos | GiST de los puntos | GiST de los municipios |",
        "|---|---:|---|---|",
        *(
            f"| {nombre} | {p['milisegundos']} | {'si' if p['usa_gist_de_los_puntos'] else 'no'} "
            f"| {'si' if p['usa_gist_de_los_municipios'] else 'no'} |"
            for nombre, p in reporte["espaciales"].items()
        ),
        "",
        "## Consultas por la API",
        "",
        "| Consulta | Mediana | p95 | Maximo | Sentencias |",
        "|---|---:|---:|---:|---:|",
    ]
    consultas = reporte["consultas"]
    for nombre, tiempo in consultas["tiempos"].items():
        lineas.append(
            f"| {nombre} | {tiempo['mediana_ms']} ms | {tiempo['p95_ms']} ms | "
            f"{tiempo['maximo_ms']} ms | {consultas['sentencias'][nombre]} |"
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
    os.environ.setdefault("MC_SOURCE_STORE_ROOT", str(destino / "almacen"))
    bitacora = Bitacora(destino / "benchmark_geografia.etapas.json", argumentos.reanudar)

    print("1. la base: a la cabeza de las migraciones, sin reconstruir nada", flush=True)
    _migrar()
    if "inventario" not in bitacora:
        bitacora.anotar("inventario", _inventario())
        bitacora.anotar("tamanos_antes", _tamanos(TABLAS_NUEVAS))
    print(f"   {json.dumps(bitacora['inventario'], default=str)}", flush=True)

    print("2. la referencia oficial", flush=True)
    if "referencia" not in bitacora:
        ruta = Path(argumentos.referencia).resolve()
        if not ruta.exists():
            sys.exit(
                f"No esta el archivo oficial: {ruta}. Descargalo de su URL (ver geografia.md)."
            )
        bitacora.anotar("referencia", _hijo("importar", "--ruta", str(ruta)))
    referencia = bitacora["referencia"]
    print(
        f"   {referencia['municipios']:,} municipios en {referencia['segundos']} s "
        f"({referencia['reparadas']} reparadas)",
        flush=True,
    )

    print("3. la muestra de puntos", flush=True)
    if "muestra" not in bitacora:
        bitacora.anotar(
            "muestra",
            _hijo(
                "muestrear",
                "--version",
                referencia["geografia_version_id"],
                "--destino",
                str(destino),
            ),
        )
    puntos = Path(bitacora["muestra"]["ruta"])

    ultimo = bitacora["inventario"]["ultimo_corte"]
    fecha = argumentos.corte or (
        (date.fromisoformat(str(ultimo)) + timedelta(days=7)).isoformat()
        if ultimo
        else "2026-04-01"
    )
    print(f"4. el corte del {fecha} con coordenadas sinteticas", flush=True)
    if "generado" not in bitacora:
        bitacora.anotar(
            "generado",
            _hijo(
                "generar",
                "--destino",
                str(destino),
                "--puntos",
                str(puntos),
                "--corte",
                fecha,
                "--formato",
                argumentos.formato,
            ),
        )
    if "ingesta" not in bitacora:
        ya = _sql(
            "SELECT id FROM corte_canonico WHERE fecha_corte = :f", {"f": date.fromisoformat(fecha)}
        )
        bitacora.anotar(
            "ingesta",
            {"recuperado": True}
            if ya
            else _hijo("ingerir", "--ruta", bitacora["generado"]["ruta"], "--corte", fecha),
        )
    if "historia" not in bitacora:
        pendiente = _sql(
            "SELECT e.id FROM ejecucion_historia e JOIN dataset_conformado d "
            "ON d.id = e.dataset_conformado_id JOIN corrida c ON c.id = d.corrida_id "
            "WHERE c.fecha_corte = :f AND e.estado = 'EN_PROCESO'",
            {"f": date.fromisoformat(fecha)},
        )
        bitacora.anotar(
            "historia",
            _hijo("historiar", "--ejecucion", str(pendiente[0][0]))
            if pendiente
            else {"recuperado": True},
        )
    ((corte_id, cuentas),) = _sql(
        "SELECT corte_id, cuentas FROM corte_canonico WHERE fecha_corte = :f",
        {"f": date.fromisoformat(fecha)},
    )
    print(f"   {cuentas:,} cuentas en el corte {corte_id}", flush=True)

    print("5. geografia/v1", flush=True)
    if "geografia" not in bitacora:
        bitacora.anotar(
            "geografia",
            _hijo(
                "geolocalizar",
                "--corte",
                str(corte_id),
                "--version",
                referencia["geografia_version_id"],
            ),
        )
    g = bitacora["geografia"]
    print(f"   {g['cuentas_objetivo']:,} cuentas en {g['segundos']} s: {g['conteos']}", flush=True)

    print("6. las bases y territorial/v2", flush=True)
    if "bases" not in bitacora:
        from motor_cartera.bases.configuracion import leer, publicar
        from motor_cartera.db.sesion import sesion

        documento = _bases_sinteticas(puntos)
        with sesion() as s:
            publicada = publicar(s, leer(documento))
            s.commit()
            s.refresh(publicada.version)
            bitacora.anotar(
                "bases",
                {
                    "bases_version_id": str(publicada.version.bases_version_id),
                    "hash_configuracion": publicada.version.hash_configuracion,
                    "bases": [
                        {
                            "codigo": b["codigo"],
                            "activa": b["activa"],
                            "municipios": len(b["municipios"]),
                        }
                        for b in documento["bases"]
                    ],
                },
            )
    if "territorial" not in bitacora:
        bitacora.anotar(
            "territorial",
            _hijo(
                "asignar",
                "--corte",
                str(corte_id),
                "--geografia",
                g["geografia_run_id"],
                "--bases",
                bitacora["bases"]["bases_version_id"],
            ),
        )
    t = bitacora["territorial"]
    print(f"   {t['cuentas_objetivo']:,} cuentas en {t['segundos']} s: {t['conteos']}", flush=True)

    print("7. lo que ocupa", flush=True)
    if "tamanos_despues" not in bitacora:
        bitacora.anotar("vacuum_s", _vacuum(TABLAS_NUEVAS))
        bitacora.anotar("tamanos_despues", _tamanos(TABLAS_NUEVAS))

    print("8. los planes y las consultas", flush=True)
    if "espaciales" not in bitacora:
        ((version_id,),) = _sql(
            "SELECT id FROM version_geografia WHERE geografia_version_id = :v",
            {"v": referencia["geografia_version_id"]},
        )
        bitacora.anotar(
            "espaciales",
            _hijo(
                "espaciales",
                "--destino",
                str(destino),
                "--ejecucion",
                str(g["ejecucion_id"]),
                "--version",
                str(version_id),
            ),
        )
    if "consultas" not in bitacora:
        bitacora.anotar(
            "consultas",
            _hijo(
                "consultar",
                "--destino",
                str(destino),
                "--geografia",
                g["geografia_run_id"],
                "--territorial",
                t["territorial_run_id"],
            ),
        )

    ((version,),) = _sql("SELECT version()")
    reporte = {
        "maquina": f"{platform.system()} {platform.release()}, {os.cpu_count()} CPU, "
        f"Python {platform.python_version()}",
        "postgresql": version.split(",")[0],
        "versiones": g["distribucion"]["versiones"],
        "inventario": bitacora["inventario"],
        "corte": {"corte_id": str(corte_id), "fecha_corte": fecha, "cuentas": cuentas},
        "referencia": referencia,
        "muestra": bitacora["muestra"],
        "generado": bitacora["generado"],
        "ingesta": bitacora["ingesta"],
        "historia": bitacora["historia"],
        "geografia": g,
        "bases": bitacora["bases"],
        "territorial": t,
        "tamanos_antes": bitacora["tamanos_antes"],
        "tamanos_despues": bitacora["tamanos_despues"],
        "espaciales": bitacora["espaciales"],
        "consultas": bitacora["consultas"],
        "recuperadas": bitacora.recuperadas(),
    }
    (destino / "benchmark_geografia.json").write_text(
        json.dumps(reporte, indent=1, ensure_ascii=False, default=str), encoding="utf-8"
    )
    (destino / "benchmark_geografia.md").write_text(_markdown(reporte), encoding="utf-8")
    print(f"Reporte: {destino / 'benchmark_geografia.md'}")


if __name__ == "__main__":
    lector = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    etapas = lector.add_subparsers(dest="etapa")
    etapas.add_parser("importar").add_argument("--ruta", type=Path, required=True)
    de_muestra = etapas.add_parser("muestrear")
    de_muestra.add_argument("--version", required=True)
    de_muestra.add_argument("--destino", type=Path, required=True)
    de_generar = etapas.add_parser("generar")
    de_generar.add_argument("--destino", type=Path, required=True)
    de_generar.add_argument("--puntos", type=Path, required=True)
    de_generar.add_argument("--corte", required=True)
    de_generar.add_argument("--formato", required=True)
    de_ingerir = etapas.add_parser("ingerir")
    de_ingerir.add_argument("--ruta", type=Path, required=True)
    de_ingerir.add_argument("--corte", required=True)
    etapas.add_parser("historiar").add_argument("--ejecucion", type=int, required=True)
    de_geografia = etapas.add_parser("geolocalizar")
    de_geografia.add_argument("--corte", required=True)
    de_geografia.add_argument("--version", required=True)
    de_asignar = etapas.add_parser("asignar")
    de_asignar.add_argument("--corte", required=True)
    de_asignar.add_argument("--geografia", required=True)
    de_asignar.add_argument("--bases", required=True)
    de_espaciales = etapas.add_parser("espaciales")
    de_espaciales.add_argument("--destino", type=Path, required=True)
    de_espaciales.add_argument("--ejecucion", type=int, required=True)
    de_espaciales.add_argument("--version", type=int, required=True)
    de_consultar = etapas.add_parser("consultar")
    de_consultar.add_argument("--destino", type=Path, required=True)
    de_consultar.add_argument("--geografia", required=True)
    de_consultar.add_argument("--territorial", required=True)
    lector.add_argument(
        "--destino", type=Path, help="Donde van los archivos, el reporte y los planes."
    )
    lector.add_argument(
        "--referencia",
        default=os.environ.get(
            "MC_REFERENCIA_INEGI",
            str(RAIZ / "datos/referencias/inegi_mg_2025/mg_2025_integrado.zip"),
        ),
        help="El zip oficial del Marco Geoestadistico Integrado 2025.",
    )
    lector.add_argument(
        "--corte", help="La fecha del corte nuevo; por omision, una semana despues del ultimo."
    )
    lector.add_argument("--formato", default="zip", help="zip (por omision) o csv.")
    lector.add_argument("--reanudar", action="store_true")
    leidos = lector.parse_args()
    if leidos.etapa == "importar":
        print(json.dumps(importar_referencia(leidos.ruta), default=str))
    elif leidos.etapa == "muestrear":
        print(json.dumps(muestrear(leidos.version, leidos.destino), default=str))
    elif leidos.etapa == "generar":
        print(
            json.dumps(
                generar_corte(leidos.destino, leidos.puntos, leidos.corte, leidos.formato),
                default=str,
            )
        )
    elif leidos.etapa == "ingerir":
        print(json.dumps(ingerir(leidos.ruta, leidos.corte), default=str))
    elif leidos.etapa == "historiar":
        print(json.dumps(historiar(leidos.ejecucion), default=str))
    elif leidos.etapa == "geolocalizar":
        print(json.dumps(geolocalizar(leidos.corte, leidos.version), default=str))
    elif leidos.etapa == "asignar":
        print(json.dumps(asignar(leidos.corte, leidos.geografia, leidos.bases), default=str))
    elif leidos.etapa == "espaciales":
        print(
            json.dumps(
                planes_espaciales(leidos.destino, leidos.ejecucion, leidos.version), default=str
            )
        )
    elif leidos.etapa == "consultar":
        print(
            json.dumps(consultar(leidos.destino, leidos.geografia, leidos.territorial), default=str)
        )
    else:
        if leidos.destino is None:
            lector.error("--destino es obligatorio.")
        main(leidos)
