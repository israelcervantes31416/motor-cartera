"""Benchmark de campo/v2: cuanto tarda planear el trabajo de campo de un corte grande, por fase,
cuanto crece la base, como queda el balance entre los gestores, cuanto se mueve el reparto al
agregar un gestor y como responden las consultas de un plan, contra PostgreSQL con PostGIS. No corre
en el CI: se pide a mano, sobre una base *_bench que ya tiene el corte de benchmark_geografia.py con
su geografia y su territorial/v2 EXITOSA:

    python scripts/benchmark_campo.py --destino <dir>
    python scripts/benchmark_campo.py --destino <dir> --reanudar

Lo que hace, cada etapa pesada en su propio proceso para que su memoria pico sea solo suya:

1. sube la base a la cabeza de las migraciones (la 0013) e inventaria lo que hay; no reconstruye
   nada de lo que ya estaba: ni la historia, ni los pagos, ni el lifecycle, ni la geografia, ni
   territorial/v2;
2. toma la unica territorial/v2 EXITOSA de la base (o la que se declare con --territorial) y su
   corte, y decide ese corte con decision/v2 una sola vez, si no tiene ya una EXITOSA;
3. publica la configuracion de campo que se DECLARA AQUI, antes de ver ningun plan: a cada base
   activa, en el orden de su codigo, un regimen que se repite (SOBRANTE, con 1.25 veces su demanda;
   LIMITE, con 1.00; SATURADA, con 0.50), salvo la ultima, que no tiene gestores. La demanda de una
   base son sus candidatas de campo ASIGNADA en territorial/v2, y sus gestores son
   ceil(regimen * demanda / (cupo medio * disponibilidad)), generados con el generador sintetico
   (cupos de 15, 20 y 25, un 5 % inactivos, semilla fija);
4. planea la fecha de operacion declarada (el dia siguiente al corte) con campo/v2, con el
   cronometro por fase, su memoria pico y su WAL;
5. publica la configuracion B (la A con un gestor mas en la base SATURADA con mas demanda) y planea
   la misma fecha: cuantos microtramos cambian de propietario preferido y cuantas cuentas de gestor,
   contra un reparto por turnos (el hash del microtramo modulo el numero de gestores);
6. corre VACUUM ANALYZE y mide cuanto ocupan las tablas de campo/v2 y sus indices, y cuanto crecio
   la base;
7. guarda con EXPLAIN (ANALYZE, BUFFERS) el plan de cada sentencia de las consultas de una cuenta,
   de un gestor, de una base, de SIN_CAPACIDAD, de un microtramo, de las cargas y de la Cuenta 360,
   tal como las emite la API, y dice si alguna recorre una tabla grande.

MC_DATABASE_URL tiene que apuntar a una base *_bench. Cada etapa se anota en una bitacora
(benchmark_campo.etapas.json) en cuanto termina; con --reanudar no se repite ninguna. Ninguna salida
lleva una coordenada: solo conteos, tiempos, identificadores opacos y planes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import statistics
import sys
import time
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from benchmark_escala import memoria_pico_mb  # noqa: E402
from benchmark_lifecycle import _exigir_bench, _migrar, _tamanos, _wal, _wal_desde  # noqa: E402
from benchmark_motor_pagos import Bitacora, _sql, _vacuum  # noqa: E402

# --- la configuracion, declarada antes de ver ningun plan -----------------------------------------

REGIMENES = (
    ("SOBRANTE", Decimal("1.25")),
    ("LIMITE", Decimal("1.00")),
    ("SATURADA", Decimal("0.50")),
)
"""El regimen de cada base activa, en el orden de su codigo y repitiendose: su capacidad declarada
esperada es esa fraccion de su demanda."""
SIN_GESTORES = "SIN_GESTORES"
"""El regimen de la ultima base activa: sin ningun gestor."""
CUPOS = (15, 20, 25)
INACTIVOS = 0.05
SEMILLA = 12_031
FESTIVOS = (date(2026, 5, 1),)
MEDIAS_JORNADAS = (date(2026, 12, 24),)
"""Excepciones de la configuracion en otras fechas: estan, pero no tocan el dia que se planea."""
GESTOR_EXTRA = {
    "codigo": "GESTOR_EXTRA_B",
    "calendario": "LV_PARTIDO",
    "capacidad_cuentas_dia": 20,
    "activo": True,
}
"""El gestor que la configuracion B agrega a la base SATURADA con mas demanda."""

REPETICIONES = 25
TABLAS_NUEVAS = (
    "version_configuracion_campo",
    "calendario_campo",
    "excepcion_calendario_campo",
    "ventana_jornada_campo",
    "gestor_campo",
    "ejecucion_campo_v2",
    "asignacion_campo_cuenta",
    "unidad_territorial_campo",
    "carga_gestor_campo",
)
TABLAS_GRANDES = (
    "asignacion_campo_cuenta",
    "unidad_territorial_campo",
    "resultado_decision_v2",
    "asignacion_territorial_v2",
    "resultado_geografia_cuenta",
    "snapshot_cuenta",
    "cuenta_canonica",
)
LLAVES_DE_UBICACION = (
    '"latitud',
    '"longitud',
    '"lat"',
    '"lon"',
    '"punto"',
    '"x"',
    '"y"',
    '"grid_x"',
    '"grid_y"',
    '"bbox"',
    '"centroide"',
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


def decidir(corte_id: str) -> dict:
    from benchmark_decision_v2 import decidir_v2

    return decidir_v2(corte_id, None, None)


def planear(
    corte_id: str, decision_run_id: str, territorial_run_id: str, configuracion: str, fecha: str
) -> dict:
    """Abre el plan y lo resuelve como su trabajo, con el cronometro por fase. Si esas mismas
    entradas ya se publicaron (la corrida se detuvo entre publicar y anotarlo), lo reporta como
    esta, sin tiempos; si quedo uno EN_PROCESO, lo continua."""
    from uuid import UUID

    from motor_cartera.campo_v2 import ejecuciones as motor
    from motor_cartera.config import config
    from motor_cartera.db.modelos import EjecucionCampoV2
    from motor_cartera.db.sesion import sesion
    from motor_cartera.ingesta.fuente_oficial import Cronometro

    recuperado = False
    try:
        with sesion() as s:
            ejecucion = motor.abrir(
                s,
                UUID(corte_id),
                UUID(decision_run_id),
                UUID(territorial_run_id),
                UUID(configuracion),
                date.fromisoformat(fecha),
                max_intentos=config.worker_max_intentos,
            )
            s.commit()
            ejecucion_id = ejecucion.id
    except motor.CampoV2YaGenerado as exc:
        ejecucion_id, recuperado = exc.previo.id, True
    except motor.CampoV2EnProceso as exc:
        ejecucion_id = exc.activo.id
    antes = _wal()
    cronometro = Cronometro()
    inicio = time.perf_counter()
    if not recuperado:
        motor.planificar(ejecucion_id, cronometro=cronometro)
    segundos = time.perf_counter() - inicio
    with sesion() as s:
        e = s.get_one(EjecucionCampoV2, ejecucion_id)
        resultado = {
            "ejecucion_id": e.id,
            "campo_run_id": str(e.campo_run_id),
            "estado": str(e.estado),
            "resultado": e.resultado,
            "firma_inputs": e.firma_inputs,
            "hash_politica": e.hash_politica,
            "fecha_operacion": e.fecha_operacion.isoformat(),
            "conteos": {
                c: getattr(e, c)
                for c in (
                    "cuentas_evaluadas_decision",
                    "candidatas_campo",
                    "asignadas",
                    "revision_territorial",
                    "sin_gestor",
                    "sin_capacidad",
                    "rebalanceadas",
                    "capacidad_total",
                    "capacidad_utilizada",
                )
            },
            "distribucion": e.distribucion,
            "detalle": e.detalle,
        }
    if resultado["estado"] != "EXITOSA":
        sys.exit(f"campo/v2 termino {resultado['estado']}: {resultado['detalle']}")
    medicion = (
        {"segundos": None, "fases": {}, "memoria_pico_mb": None, "wal_mib": None}
        if recuperado
        else {
            "segundos": round(segundos, 3),
            "candidatas_por_segundo": round(resultado["conteos"]["candidatas_campo"] / segundos),
            "fases": {nombre: round(s_, 3) for nombre, s_ in cronometro.fases.items()},
            "memoria_pico_mb": memoria_pico_mb(),
            "wal_mib": _wal_desde(antes),
        }
    )
    return {**resultado, **medicion, "recuperado": recuperado}


def consultar(destino: Path, campo_run_id: str) -> dict:
    """Las consultas por la API, con un cliente HTTP en el mismo proceso: el tiempo incluye la
    serializacion. Cada una se repite y se reporta su mediana, su p95 y su maximo; despues se
    guarda el plan de cada sentencia que emitio."""
    from fastapi.testclient import TestClient
    from sqlalchemy import event

    from motor_cartera.api.app import crear_app
    from motor_cartera.config import Config
    from motor_cartera.db.sesion import crear_motor

    clave = "benchmark-campo-" + "x" * 24
    cliente = TestClient(
        crear_app(Config(app_env="development", api_key=clave)), headers={"X-API-Key": clave}
    )
    ((ejecucion,),) = _sql(
        "SELECT id FROM ejecucion_campo_v2 WHERE campo_run_id = :c", {"c": campo_run_id}
    )
    # Una cuenta asignada; el gestor, la base y el microtramo con mas cuentas, y la zona y el tramo
    # de esa cuenta. Cada uno por su orden, sin depender del de la base de datos.
    ((cuenta, zona, tramo),) = _sql(
        "SELECT c.cuenta_id, a.zona_id, a.tramo_id FROM asignacion_campo_cuenta a "
        "JOIN cuenta_canonica c ON c.id = a.cuenta_canonica_id WHERE a.ejecucion_campo_v2_id = :e "
        "AND a.estado = 'ASIGNADA' ORDER BY a.cuenta_canonica_id LIMIT 1",
        {"e": ejecucion},
    )
    ((gestor,),) = _sql(
        "SELECT g.codigo FROM carga_gestor_campo c JOIN gestor_campo g ON g.id = c.gestor_campo_id "
        "WHERE c.ejecucion_campo_v2_id = :e ORDER BY c.asignadas DESC, g.codigo LIMIT 1",
        {"e": ejecucion},
    )
    ((base,),) = _sql(
        "SELECT b.codigo FROM unidad_territorial_campo u JOIN base_operativa b "
        "ON b.id = u.base_operativa_id WHERE u.ejecucion_campo_v2_id = :e AND u.nivel = 'ZONA' "
        "GROUP BY b.codigo ORDER BY sum(u.candidatas) DESC, b.codigo LIMIT 1",
        {"e": ejecucion},
    )
    ((microtramo,),) = _sql(
        "SELECT unidad_id FROM unidad_territorial_campo WHERE ejecucion_campo_v2_id = :e "
        "AND nivel = 'MICROTRAMO' ORDER BY candidatas DESC, unidad_id LIMIT 1",
        {"e": ejecucion},
    )
    plan = f"/campo-v2/planes/{campo_run_id}"
    rutas = {
        "asignacion_de_una_cuenta": f"/cuentas/{cuenta}/campo-v2",
        "cuenta_360": f"/cuentas/{cuenta}",
        "pagina_de_un_gestor": f"{plan}/asignaciones?gestor={gestor}&por_pagina=50",
        "pagina_de_una_base": f"{plan}/asignaciones?base={base}&por_pagina=50",
        "pagina_10_de_una_base": f"{plan}/asignaciones?base={base}&por_pagina=50&pagina=10",
        "pagina_sin_capacidad": f"{plan}/asignaciones?estado=SIN_CAPACIDAD&por_pagina=50",
        "pagina_de_un_microtramo": f"{plan}/asignaciones?microtramo={microtramo}&por_pagina=50",
        "pagina_de_un_tramo": f"{plan}/asignaciones?tramo={tramo}&por_pagina=50",
        "pagina_de_una_zona": f"{plan}/asignaciones?zona={zona}&por_pagina=50",
        "cargas_del_plan": f"{plan}/cargas?por_pagina=50",
        "cargas_de_una_base": f"{plan}/cargas?base={base}&por_pagina=50",
        "microtramos_de_una_base": f"{plan}/unidades?nivel=MICROTRAMO&base={base}&por_pagina=50",
        "plan": plan,
    }

    def medir(ruta: str) -> dict:
        tiempos = []
        for _ in range(REPETICIONES):
            inicio = time.perf_counter()
            respuesta = cliente.get(ruta)
            tiempos.append((time.perf_counter() - inicio) * 1000)
            if respuesta.status_code != 200:
                sys.exit(f"{ruta}: {respuesta.status_code} {respuesta.text[:300]}")
            for llave in LLAVES_DE_UBICACION:
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
    with (destino / "planes_campo.txt").open("w", encoding="utf-8") as salida:
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
    }


def _milisegundos(plan: str) -> float:
    for linea in plan.splitlines():
        if linea.startswith("Execution Time"):
            return float(linea.split(":")[1].split()[0])
    return 0.0


def _escanea_grande(plan: str) -> bool:
    return any(f"Seq Scan on {tabla} " in plan + " " for tabla in TABLAS_GRANDES)


# --- la configuracion declarada -------------------------------------------------------------------


def _disponibilidad(fecha: date) -> Decimal:
    """La fraccion esperada de gestores disponibles en la fecha con el generador: los activos cuyo
    calendario trabaja ese dia de la semana, por el peso de cada calendario."""
    from motor_cartera.campo_v2.configuracion import DIAS
    from motor_cartera.generador.campo import CALENDARIOS

    dia = DIAS[fecha.isoweekday() - 1]
    trabaja = sum(Decimal(str(peso)) for _, semana, peso in CALENDARIOS if dia in semana)
    return (1 - Decimal(str(INACTIVOS))) * trabaja


def _demanda(decision_run_id: str, territorial_run_id: str) -> dict[str, int]:
    """Las candidatas de campo ASIGNADA de cada base activa: lo que la configuracion tiene que
    atender. Se lee antes de planear, de la decision y de territorial/v2."""
    ((territorial, bases),) = _sql(
        "SELECT id, version_bases_operativas_id FROM ejecucion_territorial_v2 "
        "WHERE territorial_run_id = :t",
        {"t": territorial_run_id},
    )
    ((decision,),) = _sql(
        "SELECT id FROM ejecucion_decision_v2 WHERE decision_run_id = :d", {"d": decision_run_id}
    )
    filas = _sql(
        "SELECT b.codigo, count(x.cuenta) FROM base_operativa b LEFT JOIN (SELECT "
        "a.base_operativa_id AS base, a.cuenta_canonica_id AS cuenta "
        "FROM asignacion_territorial_v2 a JOIN resultado_decision_v2 r "
        "ON r.ejecucion_decision_v2_id = :decision AND r.cuenta_canonica_id = a.cuenta_canonica_id "
        "WHERE a.ejecucion_territorial_v2_id = :territorial AND a.estado_asignacion = 'ASIGNADA' "
        "AND r.canal_recomendado = 'CAMPO') x ON x.base = b.id "
        "WHERE b.version_bases_operativas_id = :bases AND b.activa "
        "GROUP BY b.codigo ORDER BY b.codigo",
        {"territorial": territorial, "decision": decision, "bases": bases},
    )
    return {codigo: cuantas for codigo, cuantas in filas}


def declarar(
    bases_version_id: str, demanda: dict[str, int], fecha: date
) -> tuple[dict, dict[str, dict]]:
    """La configuracion A: el regimen de cada base activa y sus gestores, con la regla declarada en
    el encabezado de este script. Devuelve el documento y el plan de cada base."""
    from motor_cartera.generador.campo import configuracion_sintetica

    disponibilidad = _disponibilidad(fecha)
    cupo_medio = Decimal(sum(CUPOS)) / len(CUPOS)
    codigos = sorted(demanda)
    por_base: dict[str, dict] = {}
    for i, codigo in enumerate(codigos):
        if i == len(codigos) - 1:
            regimen, factor, gestores = SIN_GESTORES, Decimal(0), 0
        else:
            regimen, factor = REGIMENES[i % len(REGIMENES)]
            gestores = math.ceil(factor * demanda[codigo] / (cupo_medio * disponibilidad))
        por_base[codigo] = {
            "regimen": regimen,
            "factor": str(factor),
            "demanda": demanda[codigo],
            "gestores": gestores,
        }
    documento = configuracion_sintetica(
        codigos,
        bases_version_id=bases_version_id,
        gestores_por_base={c: b["gestores"] for c, b in por_base.items()},
        semilla=SEMILLA,
        capacidades=CUPOS,
        inactivos=INACTIVOS,
        festivos=FESTIVOS,
        medias_jornadas=MEDIAS_JORNADAS,
        nombre="Campo sintetico del benchmark (A)",
    )
    return documento, por_base


def publicar(documento: dict) -> dict:
    from motor_cartera.campo_v2.configuracion import leer, publicar
    from motor_cartera.db.sesion import sesion

    with sesion() as s:
        publicada = publicar(s, leer(documento))
        s.commit()
        s.refresh(publicada.version)
        v = publicada.version
        return {
            "configuracion_campo_version_id": str(v.configuracion_campo_version_id),
            "hash_configuracion": v.hash_configuracion,
            "gestores": v.gestores,
            "gestores_activos": v.gestores_activos,
            "capacidad_declarada": v.capacidad_declarada,
            "calendarios": v.calendarios,
            "ventanas": v.ventanas,
            "excepciones": v.excepciones,
            "zona_horaria": v.zona_horaria,
            "reutilizada": not publicada.creada,
        }


# --- la estabilidad al agregar un gestor ----------------------------------------------------------


def estabilidad(plan_a: dict, plan_b: dict, base: str) -> dict:
    """Cuantos microtramos cambian de propietario preferido entre A y B (B = A con un gestor mas en
    `base`), y cuantas cuentas cambian de gestor; y lo que cambiaria con un reparto por turnos, el
    hash del microtramo modulo el numero de gestores disponibles de la base."""

    def propietarios(ejecucion: int) -> dict[str, tuple[str, str]]:
        return {
            str(unidad): (codigo_base, gestor)
            for unidad, codigo_base, gestor in _sql(
                "SELECT u.unidad_id, b.codigo, g.codigo FROM unidad_territorial_campo u "
                "JOIN base_operativa b ON b.id = u.base_operativa_id "
                "JOIN gestor_campo g ON g.id = u.propietario_gestor_campo_id "
                "WHERE u.ejecucion_campo_v2_id = :e AND u.nivel = 'MICROTRAMO'",
                {"e": ejecucion},
            )
        }

    a, b = propietarios(plan_a["ejecucion_id"]), propietarios(plan_b["ejecucion_id"])
    comunes = sorted(a.keys() & b.keys())
    cambian = [u for u in comunes if a[u][1] != b[u][1]]
    de_la_base = [u for u in comunes if a[u][0] == base]
    cambian_en_la_base = [u for u in cambian if a[u][0] == base]
    al_nuevo = sum(1 for u in cambian if b[u][1] == GESTOR_EXTRA["codigo"])

    disponibles_a = sorted(
        codigo
        for (codigo,) in _sql(
            "SELECT g.codigo FROM carga_gestor_campo c JOIN gestor_campo g "
            "ON g.id = c.gestor_campo_id JOIN base_operativa b ON b.id = c.base_operativa_id "
            "WHERE c.ejecucion_campo_v2_id = :e AND b.codigo = :b AND c.capacidad > 0",
            {"e": plan_a["ejecucion_id"], "b": base},
        )
    )
    disponibles_b = sorted([*disponibles_a, GESTOR_EXTRA["codigo"]])

    def por_turnos(unidad: str, gestores: list[str]) -> str:
        return gestores[int(hashlib.sha256(unidad.encode()).hexdigest(), 16) % len(gestores)]

    cambian_por_turnos = sum(
        1 for u in de_la_base if por_turnos(u, disponibles_a) != por_turnos(u, disponibles_b)
    )
    ((cuentas, cambian_de_gestor, ganan_gestor, pierden_gestor),) = _sql(
        "SELECT count(*) FILTER (WHERE a.estado = 'ASIGNADA' AND b.estado = 'ASIGNADA'), "
        "count(*) FILTER (WHERE a.estado = 'ASIGNADA' AND b.estado = 'ASIGNADA' "
        "AND ga.codigo <> gb.codigo), "
        "count(*) FILTER (WHERE a.estado <> 'ASIGNADA' AND b.estado = 'ASIGNADA'), "
        "count(*) FILTER (WHERE a.estado = 'ASIGNADA' AND b.estado <> 'ASIGNADA') "
        "FROM asignacion_campo_cuenta a JOIN asignacion_campo_cuenta b "
        "ON b.cuenta_canonica_id = a.cuenta_canonica_id AND b.ejecucion_campo_v2_id = :b "
        "LEFT JOIN gestor_campo ga ON ga.id = a.gestor_campo_id "
        "LEFT JOIN gestor_campo gb ON gb.id = b.gestor_campo_id "
        "WHERE a.ejecucion_campo_v2_id = :a",
        {"a": plan_a["ejecucion_id"], "b": plan_b["ejecucion_id"]},
    )

    def porcentaje(parte: int, total: int) -> float | None:
        return round(100 * parte / total, 2) if total else None

    return {
        "base": base,
        "gestores_disponibles_a": len(disponibles_a),
        "gestores_disponibles_b": len(disponibles_b),
        "microtramos": len(comunes),
        "microtramos_que_cambian": len(cambian),
        "porcentaje_que_cambia": porcentaje(len(cambian), len(comunes)),
        "microtramos_de_la_base": len(de_la_base),
        "cambian_en_la_base": len(cambian_en_la_base),
        "porcentaje_en_la_base": porcentaje(len(cambian_en_la_base), len(de_la_base)),
        "esperado_en_la_base": porcentaje(1, len(disponibles_b)),
        "cambian_al_gestor_nuevo": al_nuevo,
        "cambian_en_otras_bases": len(cambian) - len(cambian_en_la_base),
        "por_turnos_cambian_en_la_base": cambian_por_turnos,
        "porcentaje_por_turnos_en_la_base": porcentaje(cambian_por_turnos, len(de_la_base)),
        "cuentas_asignadas_en_los_dos": cuentas,
        "cuentas_que_cambian_de_gestor": cambian_de_gestor,
        "porcentaje_de_cuentas_que_cambian": porcentaje(cambian_de_gestor, cuentas),
        "cuentas_que_ganan_gestor": ganan_gestor,
        "cuentas_que_pierden_gestor": pierden_gestor,
    }


# --- el reporte -----------------------------------------------------------------------------------


def _mib(valor: int | None) -> str:
    return "-" if valor is None else f"{valor / 2**20:,.1f} MiB"


def _num(valor, sufijo: str = "") -> str:
    return "-" if valor is None else f"{valor:,}{sufijo}"


def _fases(plan: dict) -> list[str]:
    candidatas = plan["conteos"]["candidatas_campo"]
    return [
        "| Fase | Segundos | Candidatas por segundo |",
        "|---|---:|---:|",
        *(
            f"| {fase} | {segundos} | {_num(round(candidatas / segundos) if segundos else None)} |"
            for fase, segundos in plan["fases"].items()
        ),
    ]


def _plan_md(titulo: str, p: dict) -> list[str]:
    c, d = p["conteos"], p["distribucion"]
    balance = d["balance"]
    return [
        f"## {titulo}",
        "",
        f"campo_run_id `{p['campo_run_id']}`, fecha de operacion {p['fecha_operacion']}: "
        f"{_num(c['candidatas_campo'])} candidatas de campo de "
        f"{_num(c['cuentas_evaluadas_decision'])} cuentas en {p['segundos']} s "
        f"({_num(p.get('candidatas_por_segundo'))} por segundo), "
        f"memoria pico del proceso {p['memoria_pico_mb']} MiB, WAL {p['wal_mib']} MiB.",
        "",
        "| Conteo | Cuentas |",
        "|---|---:|",
        *(f"| {k} | {_num(v)} |" for k, v in c.items()),
        "",
        "Unidades ocupadas: "
        + ", ".join(f"{k} {_num(v)}" for k, v in d["unidades"].items())
        + ". Jornada de los gestores: "
        + ", ".join(f"{k} {_num(v)}" for k, v in d["jornada"].items())
        + f"; {d['gestores_disponibles']:,} disponibles de {d['gestores']:,}. Motivos: "
        + ", ".join(f"{k} {_num(v)}" for k, v in d["motivos"].items())
        + ".",
        "",
        "| Base | Candidatas | Asignadas | Sin gestor | Sin capacidad | Rebalanceadas | Gestores "
        "disponibles | Capacidad | Utilizacion |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        *(
            f"| {codigo} | {_num(b['candidatas'])} | {_num(b['asignadas'])} | "
            f"{_num(b['sin_gestor'])} | {_num(b['sin_capacidad'])} | {_num(b['rebalanceadas'])} | "
            f"{b['gestores_disponibles']}/{b['gestores']} | {_num(b['capacidad'])} | "
            f"{b['utilizacion'] or '-'} |"
            for codigo, b in d["por_base"].items()
        ),
        "",
        "Balance entre los gestores disponibles (un diagnostico, no un ajuste):",
        "",
        "| Medida | Minimo | Mediana | p95 | Maximo |",
        "|---|---:|---:|---:|---:|",
        *(
            f"| {nombre} | {v['minimo']} | {v['mediana']} | {v['p95']} | {v['maximo']} |"
            for nombre, v in balance.items()
            if isinstance(v, dict)
        ),
        "",
        "Coeficiente de variacion de las cuentas por gestor: "
        f"{balance['coeficiente_variacion_cuentas']}.",
        "",
        *_fases(p),
        "",
    ]


def _markdown(reporte: dict) -> str:
    cfg, e = reporte["configuracion_a"], reporte["estabilidad"]
    lineas = [
        "# Benchmark de campo/v2",
        "",
        f"- Maquina: {reporte['maquina']}",
        f"- PostgreSQL: {reporte['postgresql']}",
        f"- Corte: {reporte['corte']['fecha_corte']} ({_num(reporte['corte']['cuentas'])} cuentas "
        "sinteticas), con su geografia y su territorial/v2 del benchmark de geografia",
        f"- territorial/v2 `{reporte['territorial']['territorial_run_id']}`: "
        f"{_num(reporte['territorial']['asignadas'])} cuentas ASIGNADA",
        f"- decision/v2 `{reporte['decision']['decision_run_id']}`: "
        + (
            "ya estaba"
            if reporte["decision"].get("recuperado") or reporte["decision"].get("existente")
            else f"{_num(reporte['decision']['cuentas_decididas'])} cuentas en "
            f"{reporte['decision']['segundos']} s"
        ),
        "",
        "## La configuracion declarada",
        "",
        f"Regla: a cada base activa, en el orden de su codigo, SOBRANTE (1.25 veces su demanda), "
        f"LIMITE (1.00) y SATURADA (0.50), repitiendose; la ultima, sin gestores. Gestores = "
        f"ceil(regimen x demanda / (cupo medio {reporte['cupo_medio']} x disponibilidad "
        f"{reporte['disponibilidad']})); cupos {list(CUPOS)}, {INACTIVOS:.0%} inactivos, semilla "
        f"{SEMILLA}.",
        "",
        f"Configuracion A `{cfg['configuracion_campo_version_id']}`, hash "
        f"`{cfg['hash_configuracion']}`: {_num(cfg['gestores'])} gestores "
        f"({_num(cfg['gestores_activos'])} activos), {cfg['calendarios']} calendarios, "
        f"{cfg['ventanas']} ventanas, {cfg['excepciones']} excepciones, capacidad declarada "
        f"{_num(cfg['capacidad_declarada'])}, {cfg['zona_horaria']}.",
        "",
        "| Base | Regimen | Demanda | Gestores |",
        "|---|---|---:|---:|",
        *(
            f"| {codigo} | {b['regimen']} | {_num(b['demanda'])} | {_num(b['gestores'])} |"
            for codigo, b in reporte["regimenes"].items()
        ),
        "",
        *_plan_md("El plan A", reporte["plan_a"]),
        "## Estabilidad al agregar un gestor",
        "",
        f"La configuracion B (`{reporte['configuracion_b']['configuracion_campo_version_id']}`) es "
        f"la A con {GESTOR_EXTRA['codigo']} en {e['base']} "
        f"({e['gestores_disponibles_a']} gestores disponibles en A, {e['gestores_disponibles_b']} "
        f"en B). El plan B tardo {reporte['plan_b']['segundos']} s.",
        "",
        f"- Microtramos que cambian de propietario preferido: {_num(e['microtramos_que_cambian'])} "
        f"de {_num(e['microtramos'])} ({e['porcentaje_que_cambia']} %); en {e['base']}, "
        f"{_num(e['cambian_en_la_base'])} de {_num(e['microtramos_de_la_base'])} "
        f"({e['porcentaje_en_la_base']} %; lo esperado con Rendezvous es 1/n, "
        f"{e['esperado_en_la_base']} %), todos al gestor nuevo: "
        f"{_num(e['cambian_al_gestor_nuevo'])}; en las otras bases, "
        f"{_num(e['cambian_en_otras_bases'])}.",
        f"- Con un reparto por turnos (hash modulo n), cambiarian "
        f"{_num(e['por_turnos_cambian_en_la_base'])} de {_num(e['microtramos_de_la_base'])} "
        f"({e['porcentaje_por_turnos_en_la_base']} %).",
        f"- Cuentas asignadas en los dos planes que cambian de gestor: "
        f"{_num(e['cuentas_que_cambian_de_gestor'])} de {_num(e['cuentas_asignadas_en_los_dos'])} "
        f"({e['porcentaje_de_cuentas_que_cambian']} %); ganan gestor "
        f"{_num(e['cuentas_que_ganan_gestor'])} (el cupo nuevo), lo pierden "
        f"{_num(e['cuentas_que_pierden_gestor'])}.",
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
        f"{_mib(reporte['tamanos_despues']['base_bytes'])} con todo lo de este benchmark (la "
        "decision/v2 del corte, si no estaba, las dos configuraciones y los dos planes).",
        "",
        "## Consultas por la API",
        "",
        "| Consulta | Mediana | p95 | Maximo | Sentencias | En PostgreSQL |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    consultas = reporte["consultas"]
    for nombre, tiempo in consultas["tiempos"].items():
        lineas.append(
            f"| {nombre} | {tiempo['mediana_ms']} ms | {tiempo['p95_ms']} ms | "
            f"{tiempo['maximo_ms']} ms | {consultas['sentencias'][nombre]} | "
            f"{consultas['milisegundos_en_postgresql'][nombre]} ms |"
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


# --- el orquestador -------------------------------------------------------------------------------


def _territorial(declarada: str | None) -> dict:
    filas = _sql(
        "SELECT t.territorial_run_id, c.corte_id, c.fecha_corte, c.cuentas, t.asignadas, "
        "v.bases_version_id FROM ejecucion_territorial_v2 t JOIN corte_canonico c "
        "ON c.id = t.corte_canonico_id JOIN version_bases_operativas v "
        "ON v.id = t.version_bases_operativas_id WHERE t.estado = 'EXITOSA' "
        + ("AND t.territorial_run_id = :t " if declarada else "")
        + "ORDER BY t.id",
        {"t": declarada} if declarada else {},
    )
    if len(filas) != 1:
        sys.exit(
            f"Hay {len(filas)} territorial/v2 EXITOSA que sirven: declara una con --territorial "
            "(o corre antes benchmark_geografia.py)."
        )
    ((territorial_run_id, corte_id, fecha_corte, cuentas, asignadas, bases_version_id),) = filas
    return {
        "territorial_run_id": str(territorial_run_id),
        "corte_id": str(corte_id),
        "fecha_corte": fecha_corte.isoformat(),
        "cuentas": cuentas,
        "asignadas": asignadas,
        "bases_version_id": str(bases_version_id),
    }


def main(argumentos: argparse.Namespace) -> None:
    _exigir_bench()
    destino = argumentos.destino.resolve()
    bitacora = Bitacora(destino / "benchmark_campo.etapas.json", argumentos.reanudar)

    print("1. la base: a la cabeza de las migraciones, sin reconstruir nada", flush=True)
    _migrar()
    if "tamanos_antes" not in bitacora:
        bitacora.anotar("tamanos_antes", _tamanos(TABLAS_NUEVAS))

    print("2. las entradas: territorial/v2 y decision/v2 del corte", flush=True)
    if "territorial" not in bitacora:
        bitacora.anotar("territorial", _territorial(argumentos.territorial))
    t = bitacora["territorial"]
    print(f"   corte {t['fecha_corte']}: {t['cuentas']:,} cuentas, {t['asignadas']:,} asignadas")
    if "decision" not in bitacora:
        existentes = _sql(
            "SELECT d.decision_run_id, d.cuentas_decididas FROM ejecucion_decision_v2 d "
            "JOIN corte_canonico c ON c.id = d.corte_canonico_id WHERE c.corte_id = :c "
            "AND d.estado = 'EXITOSA' ORDER BY d.id",
            {"c": t["corte_id"]},
        )
        if len(existentes) > 1:
            sys.exit(f"El corte tiene {len(existentes)} decision/v2 EXITOSA: ambiguo.")
        bitacora.anotar(
            "decision",
            {
                "decision_run_id": str(existentes[0][0]),
                "cuentas_decididas": existentes[0][1],
                "existente": True,
            }
            if existentes
            else _hijo("decidir", "--corte", t["corte_id"]),
        )
    d = bitacora["decision"]
    print(f"   decision/v2 {d['decision_run_id']}", flush=True)

    fecha = date.fromisoformat(t["fecha_corte"]) + timedelta(days=1)
    print(f"3. la configuracion declarada, para el {fecha.isoformat()}", flush=True)
    if "configuracion_a" not in bitacora:
        demanda = _demanda(d["decision_run_id"], t["territorial_run_id"])
        documento, por_base = declarar(t["bases_version_id"], demanda, fecha)
        bitacora.anotar("regimenes", por_base)
        (destino / "configuracion_a.json").write_text(
            json.dumps(documento, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        bitacora.anotar("configuracion_a", publicar(documento))
    a = bitacora["configuracion_a"]
    print(f"   {a['gestores']:,} gestores, capacidad declarada {a['capacidad_declarada']:,}")

    print("4. el plan A", flush=True)
    entradas = [
        "--corte",
        t["corte_id"],
        "--decision",
        d["decision_run_id"],
        "--territorial",
        t["territorial_run_id"],
        "--fecha",
        fecha.isoformat(),
    ]
    if "plan_a" not in bitacora:
        bitacora.anotar(
            "plan_a",
            _hijo("planear", *entradas, "--configuracion", a["configuracion_campo_version_id"]),
        )
    plan_a = bitacora["plan_a"]
    print(f"   {plan_a['conteos']} en {plan_a['segundos']} s", flush=True)

    print("5. la configuracion B (un gestor mas) y su plan", flush=True)
    saturadas = {
        codigo: b["demanda"]
        for codigo, b in bitacora["regimenes"].items()
        if b["regimen"] == "SATURADA"
    }
    base_extra = min(saturadas, key=lambda codigo: (-saturadas[codigo], codigo))
    if "configuracion_b" not in bitacora:
        documento = json.loads((destino / "configuracion_a.json").read_text(encoding="utf-8"))
        documento["nombre"] = "Campo sintetico del benchmark (B)"
        documento["gestores"].append({**GESTOR_EXTRA, "base": base_extra})
        bitacora.anotar("configuracion_b", publicar(documento))
    b = bitacora["configuracion_b"]
    if "plan_b" not in bitacora:
        bitacora.anotar(
            "plan_b",
            _hijo("planear", *entradas, "--configuracion", b["configuracion_campo_version_id"]),
        )
    if "estabilidad" not in bitacora:
        bitacora.anotar("estabilidad", estabilidad(plan_a, bitacora["plan_b"], base_extra))
    print(f"   {bitacora['estabilidad']}", flush=True)

    print("6. lo que ocupa", flush=True)
    if "tamanos_despues" not in bitacora:
        bitacora.anotar("vacuum_s", _vacuum(TABLAS_NUEVAS))
        bitacora.anotar("tamanos_despues", _tamanos(TABLAS_NUEVAS))

    print("7. las consultas y sus planes", flush=True)
    if "consultas" not in bitacora:
        bitacora.anotar(
            "consultas",
            _hijo("consultar", "--destino", str(destino), "--plan", plan_a["campo_run_id"]),
        )

    ((version,),) = _sql("SELECT version()")
    reporte = {
        "maquina": f"{platform.system()} {platform.release()}, {os.cpu_count()} CPU, "
        f"Python {platform.python_version()}",
        "postgresql": version.split(",")[0],
        "corte": {
            "corte_id": t["corte_id"],
            "fecha_corte": t["fecha_corte"],
            "cuentas": t["cuentas"],
        },
        "territorial": t,
        "decision": d,
        "fecha_operacion": fecha.isoformat(),
        "disponibilidad": str(_disponibilidad(fecha)),
        "cupo_medio": str(Decimal(sum(CUPOS)) / len(CUPOS)),
        "regimenes": bitacora["regimenes"],
        "configuracion_a": a,
        "configuracion_b": b,
        "plan_a": plan_a,
        "plan_b": bitacora["plan_b"],
        "estabilidad": bitacora["estabilidad"],
        "tamanos_antes": bitacora["tamanos_antes"],
        "tamanos_despues": bitacora["tamanos_despues"],
        "vacuum_s": bitacora["vacuum_s"],
        "consultas": bitacora["consultas"],
        "recuperadas": bitacora.recuperadas(),
    }
    (destino / "benchmark_campo.json").write_text(
        json.dumps(reporte, indent=1, ensure_ascii=False, default=str), encoding="utf-8"
    )
    (destino / "benchmark_campo.md").write_text(_markdown(reporte), encoding="utf-8")
    print(f"Reporte: {destino / 'benchmark_campo.md'}")


if __name__ == "__main__":
    lector = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    etapas = lector.add_subparsers(dest="etapa")
    etapas.add_parser("decidir").add_argument("--corte", required=True)
    de_planear = etapas.add_parser("planear")
    for opcion in ("--corte", "--decision", "--territorial", "--configuracion", "--fecha"):
        de_planear.add_argument(opcion, required=True)
    de_consultar = etapas.add_parser("consultar")
    de_consultar.add_argument("--destino", type=Path, required=True)
    de_consultar.add_argument("--plan", required=True)
    lector.add_argument(
        "--destino", type=Path, help="Donde van la bitacora, el reporte y los planes."
    )
    lector.add_argument(
        "--territorial", help="La territorial/v2 EXITOSA que se planea, si hay mas de una."
    )
    lector.add_argument("--reanudar", action="store_true")
    leidos = lector.parse_args()
    if leidos.etapa == "decidir":
        print(json.dumps(decidir(leidos.corte), default=str))
    elif leidos.etapa == "planear":
        print(
            json.dumps(
                planear(
                    leidos.corte,
                    leidos.decision,
                    leidos.territorial,
                    leidos.configuracion,
                    leidos.fecha,
                ),
                default=str,
            )
        )
    elif leidos.etapa == "consultar":
        print(json.dumps(consultar(leidos.destino, leidos.plan), default=str))
    else:
        if leidos.destino is None:
            lector.error("--destino es obligatorio.")
        main(leidos)
