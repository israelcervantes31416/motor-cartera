"""Benchmark de ruteo/v2: cuanto tarda rutear un plan de campo/v2 grande, por fase, cuanto crece la
base, si la reconstruccion da exactamente lo mismo y como responden las consultas, contra
PostgreSQL. No corre en el CI: se pide a mano, sobre una base *_bench que ya tiene un plan de
campo/v2 EXITOSA (el de benchmark_campo.py). Son dos benchmarks, que no se mezclan:

A. `planificador`: el motor a escala, con un proveedor SINTETICO determinista que NO es una red
   vial: un modelo geometrico (la distancia en linea recta por 1.3, a 25 km/h), con un snap, un
   punto sin snap de cada 200 y uno sin camino de cada 100, que salen de un hash del punto. Mide la
   seleccion, el snap, las matrices por bloques, la secuenciacion, el 2-opt, la simulacion, el
   staging y la publicacion, su memoria, el WAL, lo que crece la base, la reconstruccion y las
   consultas de la API con su plan. Ninguna cifra de A dice algo de calles.

B. `red`: una red vial real, un extracto regional de OpenStreetMap (ODbL), preparada con OSRM
   v26.9.0 (MLD, perfil car) con sus binarios nativos y servida por osrm-routed en esta maquina: lo
   que tarda y ocupa preparar la red (extract, partition y customize, cada uno con su memoria
   pico), lo que tarda osrm-routed en cargarla y la memoria que usa, y el ruteo del mismo plan
   contra ella: peticiones, latencias (p50, p95 y maximo), snaps, matrices, rutas y paradas por
   estado, y la reconstruccion. Las cuentas son sinteticas: puntos al azar dentro de municipios
   reales. Ninguna coordenada sale de esta maquina.

    python scripts/benchmark_ruteo.py planificador --destino <dir> --plan <campo_run_id>
    python scripts/benchmark_ruteo.py red --destino <dir> --plan <campo_run_id> --fuente <pbf>
        --osrm-bin <dir> --region <texto> --fecha-snapshot AAAA-MM-DD --url-fuente <url>
        --proveedor-extracto <texto>

Cada etapa pesada corre en su propio proceso, para que su memoria pico sea solo suya, y se anota
en una bitacora en cuanto termina; con --reanudar no se repite ninguna. MC_DATABASE_URL tiene que
apuntar a una base *_bench. Ninguna salida lleva una coordenada: solo conteos, tiempos,
identificadores opacos, hashes y planes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from benchmark_escala import memoria_pico_mb  # noqa: E402
from benchmark_lifecycle import _exigir_bench, _migrar, _tamanos, _wal, _wal_desde  # noqa: E402
from benchmark_motor_pagos import Bitacora, _sql, _vacuum  # noqa: E402

TABLAS_NUEVAS = ("version_red_vial", "ejecucion_ruteo_v2", "ruta_gestor_v2", "parada_ruta_v2")
TABLAS_GRANDES = (
    "parada_ruta_v2",
    "ruta_gestor_v2",
    "asignacion_campo_cuenta",
    "carga_gestor_campo",
    "cuenta_canonica",
)
REPETICIONES = 25
CONCURRENCIA = 4
PUERTO_OSRM = 5021
MODELO_SINTETICO = {
    "version": "benchmark-geometrico/v1",
    "nombre": "Modelo geometrico del benchmark de ruteo/v2: NO es una red vial",
    "distancia": "haversine por 1.3",
    "velocidad_kmh": 25,
    "snap_m": "600 por u^6, con u de un hash del punto",
    "sin_snap": "1 de cada 200 puntos",
    "sin_camino": "1 de cada 100 puntos",
}
"""Lo que hace el proveedor SINTETICO del benchmark A, declarado antes de ver ninguna ruta."""
FACTOR = 1.3
VELOCIDAD = Decimal(25) / Decimal("3.6")
RADIO_TIERRA_M = 6_371_008.8
LLAVES_DE_UBICACION = (
    '"latitud',
    '"longitud',
    '"lat"',
    '"lon"',
    '"punto"',
    '"x"',
    '"y"',
    '"bbox"',
    '"geometry"',
    '"geometria"',
    '"polyline"',
    '"coordinates"',
    '"location"',
)


def _hijo(*argumentos: str) -> dict:
    """Una etapa en su propio proceso (este mismo script), con su ultima linea como JSON."""
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


# --- el proveedor SINTETICO del benchmark A: NO es una red vial -----------------------------------


def _azar(coordenada: tuple[str, str], sal: str) -> float:
    """Un numero en [0, 1) que sale del punto y de la sal: el mismo punto, el mismo numero."""
    resumen = hashlib.sha256(f"{sal}|{coordenada[0]}|{coordenada[1]}".encode()).digest()
    return int.from_bytes(resumen[:8], "big") / 2**64


class ProveedorGeometrico:
    """El proveedor SINTETICO del benchmark A, atado a la huella de su version SINTETICO de la red.
    Un modelo geometrico determinista: no conoce ninguna calle, y ningun backend OSRM declara su
    huella."""

    def __init__(self, huella: str) -> None:
        self.huella = huella

    def sesion(self, huella: str) -> _SesionGeometrica:
        return _SesionGeometrica(self, huella)

    def salud(self) -> bool:
        return True


class _SesionGeometrica:
    def __init__(self, proveedor: ProveedorGeometrico, huella: str) -> None:
        from motor_cartera.ruteo_v2.proveedor import Metricas

        self.proveedor = proveedor
        self.pedida = huella
        self.metricas = Metricas()

    def verificar(self) -> None:
        from motor_cartera.ruteo_v2.proveedor import BackendIncompatible

        if self.pedida != self.proveedor.huella:
            raise BackendIncompatible(self.pedida, self.proveedor.huella)

    def snaps(self, coordenadas):
        self.verificar()
        inicio = time.perf_counter()
        resultado: list[Decimal | None] = []
        for c in coordenadas:
            if _azar(c, "sin_snap") < 1 / 200:
                resultado.append(None)
            else:
                resultado.append(Decimal(repr(round(600 * _azar(c, "snap") ** 6, 2))))
        self.metricas.registrar("nearest", (time.perf_counter() - inicio) * 1000, 0, 0)
        return resultado

    def matriz(self, coordenadas, *, bloque: int):
        from motor_cartera.ruteo_v2.proveedor import MatrizVial

        self.verificar()
        inicio = time.perf_counter()
        n = len(coordenadas)
        radianes = [
            (math.radians(float(lat)), math.radians(float(lon))) for lon, lat in coordenadas
        ]
        sin_camino = [_azar(c, "sin_camino") < 1 / 100 for c in coordenadas]
        duracion: list[list[Decimal | None]] = []
        distancia: list[list[Decimal | None]] = []
        for i in range(n):
            fila_d: list[Decimal | None] = []
            fila_m: list[Decimal | None] = []
            for j in range(n):
                if i == j:
                    fila_d.append(Decimal(0))
                    fila_m.append(Decimal(0))
                elif sin_camino[i] or sin_camino[j]:
                    fila_d.append(None)
                    fila_m.append(None)
                else:
                    (la1, lo1), (la2, lo2) = radianes[i], radianes[j]
                    h = (
                        math.sin((la2 - la1) / 2) ** 2
                        + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
                    )
                    metros = Decimal(
                        repr(round(2 * RADIO_TIERRA_M * math.asin(h**0.5) * FACTOR, 1))
                    )
                    fila_m.append(metros)
                    fila_d.append((metros / VELOCIDAD).quantize(Decimal("0.1")))
            duracion.append(fila_d)
            distancia.append(fila_m)
        trozos = math.ceil(n / bloque)
        ms = (time.perf_counter() - inicio) * 1000
        for _ in range(trozos * trozos):
            self.metricas.bloque()
            self.metricas.registrar("table", ms / (trozos * trozos), 0, 0)
        return MatrizVial(duracion, distancia)


# --- las etapas, cada una en su proceso -----------------------------------------------------------


def _percentiles(valores: list[float]) -> dict:
    if not valores:
        return {"n": 0}
    orden = sorted(valores)
    return {
        "n": len(orden),
        "p50": round(statistics.median(orden), 2),
        "p95": round(orden[max(1, math.ceil(0.95 * len(orden))) - 1], 2),
        "maximo": round(orden[-1], 2),
    }


def hash_logico(ejecucion_id: int) -> str:
    """El SHA-256 de lo que publico la ejecucion, sin ids internos ni instantes de registro: cada
    ruta por el codigo de su gestor y cada parada por su cuenta_id. Dos ejecuciones con las mismas
    entradas tienen que dar el mismo."""
    from sqlalchemy import text

    from motor_cartera.db.sesion import crear_motor

    resumen = hashlib.sha256()
    rutas = (
        "SELECT g.codigo, r.ruta_id, b.codigo, r.estado_ruta, r.motivo_base, r.paradas_campo, "
        "r.programadas, r.sin_tiempo, r.no_enrutables, r.no_enrutables_base, "
        "r.base_snap_distancia_m, r.jornada_inicio_at, r.jornada_fin_at, r.salida_base_at, "
        "r.salida_regreso_at, r.regreso_base_at, r.distancia_regreso_m, r.duracion_regreso_s, "
        "r.espera_regreso_s, r.distancia_total_m, r.duracion_traslado_s, r.duracion_servicio_s, "
        "r.duracion_espera_s, r.duracion_total_s, r.mejoras_2opt, r.hash_matriz, r.hash_secuencia "
        "FROM ruta_gestor_v2 r JOIN gestor_campo g ON g.id = r.gestor_campo_id "
        "JOIN base_operativa b ON b.id = r.base_operativa_id "
        "WHERE r.ejecucion_ruteo_v2_id = :e ORDER BY g.codigo"
    )
    paradas = (
        "SELECT c.cuenta_id, g.codigo, x.prioridad, x.estado, x.motivo, x.orden_visita, "
        "x.snap_distancia_m, x.salida_anterior_at, x.llegada_estimada, x.inicio_servicio, "
        "x.fin_servicio, x.distancia_desde_anterior_m, x.duracion_desde_anterior_s, x.espera_s "
        "FROM parada_ruta_v2 x JOIN cuenta_canonica c ON c.id = x.cuenta_canonica_id "
        "JOIN gestor_campo g ON g.id = x.gestor_campo_id "
        "WHERE x.ejecucion_ruteo_v2_id = :e ORDER BY c.cuenta_id"
    )
    with crear_motor().connect() as conexion:
        for consulta in (rutas, paradas):
            filas = conexion.execution_options(stream_results=True, yield_per=10_000).execute(
                text(consulta), {"e": ejecucion_id}
            )
            for fila in filas:
                resumen.update(repr(tuple(fila)).encode())
                resumen.update(b"\n")
    return resumen.hexdigest()


def rutear(plan: str, red: str, modo: str, url: str | None) -> dict:
    """Abre la ejecucion y la resuelve como su trabajo, con la medicion del motor. Si esas mismas
    entradas ya se publicaron (la corrida se detuvo entre publicar y anotarlo), las reporta como
    estan, sin tiempos; si quedo una EN_PROCESO, la continua."""
    from uuid import UUID

    from sqlmodel import select

    from motor_cartera.config import config
    from motor_cartera.db.modelos import EjecucionRuteoV2, VersionRedVial
    from motor_cartera.db.sesion import sesion
    from motor_cartera.ruteo_v2 import ejecuciones as motor
    from motor_cartera.ruteo_v2.ejecuciones import Medicion
    from motor_cartera.ruteo_v2.proveedor import ClienteOsrm, ConfiguracionOsrm

    with sesion() as s:
        version = s.exec(
            select(VersionRedVial).where(VersionRedVial.red_vial_version_id == UUID(red))
        ).one()
        huella = version.huella
    recuperado = False
    try:
        with sesion() as s:
            ejecucion = motor.abrir(
                s, UUID(plan), UUID(red), max_intentos=config.worker_max_intentos
            )
            s.commit()
            ejecucion_id = ejecucion.id
    except motor.RuteoV2YaGenerado as exc:
        ejecucion_id, recuperado = exc.previo.id, True
    except motor.RuteoV2EnProceso as exc:
        ejecucion_id = exc.activa.id
    proveedor = (
        ProveedorGeometrico(huella)
        if modo == "sintetico"
        else ClienteOsrm(ConfiguracionOsrm(url, timeout_lectura_segundos=120))
    )
    medicion = Medicion()
    antes = _wal()
    inicio = time.perf_counter()
    if not recuperado:
        motor.rutear(
            ejecucion_id, proveedor=proveedor, concurrencia=CONCURRENCIA, medicion=medicion
        )
    segundos = time.perf_counter() - inicio
    with sesion() as s:
        e = s.get_one(EjecucionRuteoV2, ejecucion_id)
        resultado = {
            "ejecucion_id": e.id,
            "ruteo_run_id": str(e.ruteo_run_id),
            "estado": str(e.estado),
            "resultado": e.resultado,
            "firma_inputs": e.firma_inputs,
            "hash_politica": e.hash_politica,
            "version_politica": e.version_politica,
            "fecha_operacion": e.fecha_operacion.isoformat(),
            "conteos": {
                c: getattr(e, c)
                for c in (
                    "gestores_objetivo",
                    "rutas_completas",
                    "rutas_parciales",
                    "rutas_sin_paradas",
                    "bases_no_enrutables",
                    "rutas_sin_jornada",
                    "paradas_objetivo",
                    "programadas",
                    "sin_tiempo",
                    "no_enrutables",
                    "no_enrutables_base",
                )
            },
            "totales": {
                c: getattr(e, c)
                for c in (
                    "distancia_total_m",
                    "duracion_vial_total_s",
                    "duracion_servicio_total_s",
                    "duracion_espera_total_s",
                )
            },
            "distribucion": e.distribucion,
            "detalle": e.detalle,
        }
    if resultado["estado"] != "EXITOSA":
        sys.exit(f"ruteo/v2 termino {resultado['estado']}: {resultado['detalle']}")
    medido = (
        {"segundos": None, "fases": {}, "memoria_pico_mb": None, "wal_mib": None}
        if recuperado
        else {
            "segundos": round(segundos, 3),
            "paradas_por_segundo": round(resultado["conteos"]["paradas_objetivo"] / segundos),
            "fases": {nombre: round(s_, 3) for nombre, s_ in medicion.cronometro.fases.items()},
            "peticiones": medicion.peticiones,
            "latencias_ms": {k: _percentiles(v) for k, v in medicion.latencias_ms.items()},
            "reintentos": medicion.reintentos,
            "bytes_enviados": medicion.bytes_enviados,
            "bytes_recibidos": medicion.bytes_recibidos,
            "memoria_pico_mb": memoria_pico_mb(),
            "wal_mib": _wal_desde(antes),
        }
    )
    return {
        **resultado,
        **medido,
        "hash_logico": hash_logico(ejecucion_id),
        "calidad": calidad(ejecucion_id),
        "recuperado": recuperado,
    }


def calidad(ejecucion_id: int) -> dict:
    """Lo que dicen las rutas y las paradas publicadas, en numeros: los motivos, los snaps de las
    paradas, las paradas programadas por ruta y la distancia, el traslado y la espera de las rutas
    que salen de su base. Un diagnostico, no una calificacion."""
    motivos = dict(
        _sql(
            "SELECT coalesce(motivo, 'PROGRAMADA'), count(*) FROM parada_ruta_v2 "
            "WHERE ejecucion_ruteo_v2_id = :e GROUP BY 1 ORDER BY 2 DESC",
            {"e": ejecucion_id},
        )
    )
    ((p50, p95, p99, maximo, con_snap),) = _sql(
        "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY snap_distancia_m), "
        "percentile_cont(0.95) WITHIN GROUP (ORDER BY snap_distancia_m), "
        "percentile_cont(0.99) WITHIN GROUP (ORDER BY snap_distancia_m), max(snap_distancia_m), "
        "count(snap_distancia_m) FROM parada_ruta_v2 WHERE ejecucion_ruteo_v2_id = :e",
        {"e": ejecucion_id},
    )
    rutas = _sql(
        "SELECT programadas, distancia_total_m, duracion_traslado_s, duracion_espera_s, "
        "duracion_total_s, mejoras_2opt FROM ruta_gestor_v2 "
        "WHERE ejecucion_ruteo_v2_id = :e AND salida_base_at IS NOT NULL",
        {"e": ejecucion_id},
    )

    def columna(i: int) -> dict:
        return _percentiles([float(r[i]) for r in rutas])

    return {
        "motivos": motivos,
        "snap_m": {
            "con_snap": con_snap,
            "p50": None if p50 is None else round(float(p50), 2),
            "p95": None if p95 is None else round(float(p95), 2),
            "p99": None if p99 is None else round(float(p99), 2),
            "maximo": None if maximo is None else float(maximo),
        },
        "rutas_que_salen": len(rutas),
        "programadas_por_ruta": columna(0),
        "distancia_por_ruta_m": columna(1),
        "traslado_por_ruta_s": columna(2),
        "espera_por_ruta_s": columna(3),
        "duracion_por_ruta_s": columna(4),
        "mejoras_2opt_por_ruta": columna(5),
        "rutas_con_2opt": sum(1 for r in rutas if r[5] > 0),
    }


def borrar(ruteo_run_id: str) -> dict:
    """Borra una ejecucion terminada y solo lo derivado de ella (sus paradas, sus rutas y su
    trabajo), apagando un momento los disparadores que lo protegen: para reconstruirla con las
    mismas entradas. Ni el plan de campo/v2 ni la red se tocan. Solo en una base *_bench."""
    from sqlalchemy import text

    from motor_cartera.db.sesion import crear_motor

    _exigir_bench()
    disparadores = (
        ("ruta_gestor_v2", "tr_ruteo_v2_inmutable"),
        ("parada_ruta_v2", "tr_ruteo_v2_inmutable"),
        ("ejecucion_ruteo_v2", "tr_ruteo_v2_terminado"),
    )
    inicio = time.perf_counter()
    with crear_motor().begin() as conexion:
        (ejecucion,) = conexion.execute(
            text("SELECT id FROM ejecucion_ruteo_v2 WHERE ruteo_run_id = :r"), {"r": ruteo_run_id}
        ).one()
        for tabla, trigger in disparadores:
            conexion.execute(text(f"ALTER TABLE {tabla} DISABLE TRIGGER {trigger}"))
        for sentencia in (
            "DELETE FROM parada_ruta_v2 WHERE ejecucion_ruteo_v2_id = :e",
            "DELETE FROM ruta_gestor_v2 WHERE ejecucion_ruteo_v2_id = :e",
            "DELETE FROM trabajo_orquestacion WHERE ejecucion_ruteo_v2_id = :e",
            "DELETE FROM ejecucion_ruteo_v2 WHERE id = :e",
        ):
            conexion.execute(text(sentencia), {"e": ejecucion})
        for tabla, trigger in disparadores:
            conexion.execute(text(f"ALTER TABLE {tabla} ENABLE TRIGGER {trigger}"))
    return {"segundos": round(time.perf_counter() - inicio, 3)}


def consultar(destino: Path, ruteo_run_id: str) -> dict:
    """Las consultas de ruteo/v2 por la API, con un cliente HTTP en el mismo proceso: el tiempo
    incluye la serializacion. Cada una se repite y se reporta su mediana, su p95 y su maximo;
    despues se guarda el plan de cada sentencia que emitio."""
    import re

    from fastapi.testclient import TestClient
    from sqlalchemy import event

    from motor_cartera.api.app import crear_app
    from motor_cartera.config import Config
    from motor_cartera.db.sesion import crear_motor

    coordenada = re.compile(r"(?<![\d:.])-?\d{1,3}\.\d{5,}")
    clave = "benchmark-ruteo-" + "x" * 24
    cliente = TestClient(
        crear_app(Config(app_env="development", api_key=clave)), headers={"X-API-Key": clave}
    )
    ((ejecucion,),) = _sql(
        "SELECT id FROM ejecucion_ruteo_v2 WHERE ruteo_run_id = :r", {"r": ruteo_run_id}
    )
    # La ruta PARCIAL con mas paradas, su gestor y su base; y una cuenta programada de ella.
    ((ruta_id, gestor, base),) = _sql(
        "SELECT r.ruta_id, g.codigo, b.codigo FROM ruta_gestor_v2 r JOIN gestor_campo g "
        "ON g.id = r.gestor_campo_id JOIN base_operativa b ON b.id = r.base_operativa_id "
        "WHERE r.ejecucion_ruteo_v2_id = :e ORDER BY r.programadas DESC, r.paradas_campo DESC, "
        "g.codigo LIMIT 1",
        {"e": ejecucion},
    )
    ((cuenta,),) = _sql(
        "SELECT c.cuenta_id FROM parada_ruta_v2 x JOIN cuenta_canonica c "
        "ON c.id = x.cuenta_canonica_id JOIN ruta_gestor_v2 r ON r.ejecucion_ruteo_v2_id = "
        "x.ejecucion_ruteo_v2_id AND r.gestor_campo_id = x.gestor_campo_id "
        "WHERE x.ejecucion_ruteo_v2_id = :e AND r.ruta_id = :r AND x.estado = 'PROGRAMADA' "
        "ORDER BY x.orden_visita LIMIT 1",
        {"e": ejecucion, "r": ruta_id},
    )
    e = f"/ruteo-v2/ejecuciones/{ruteo_run_id}"
    rutas = {
        "ejecucion": e,
        "rutas_pagina_1": f"{e}/rutas?por_pagina=50",
        "rutas_pagina_50": f"{e}/rutas?por_pagina=50&pagina=50",
        "rutas_de_una_base": f"{e}/rutas?base={base}&por_pagina=50",
        "rutas_parciales": f"{e}/rutas?estado=PARCIAL&por_pagina=50",
        "paradas_pagina_1": f"{e}/paradas?por_pagina=50",
        "paradas_pagina_500": f"{e}/paradas?por_pagina=50&pagina=500",
        "paradas_sin_tiempo": f"{e}/paradas?estado=SIN_TIEMPO&por_pagina=50",
        "paradas_no_enrutables": f"{e}/paradas?estado=NO_ENRUTABLE&por_pagina=50",
        "paradas_de_un_gestor": f"{e}/paradas?gestor={gestor}&por_pagina=50",
        "paradas_de_una_base": f"{e}/paradas?base={base}&por_pagina=50",
        "ruta": f"/ruteo-v2/rutas/{ruta_id}",
        "paradas_de_una_ruta": f"/ruteo-v2/rutas/{ruta_id}/paradas?por_pagina=50",
        "ruteo_de_una_cuenta": f"/cuentas/{cuenta}/ruteo-v2",
        "cuenta_360": f"/cuentas/{cuenta}",
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
            if coordenada.search(respuesta.text):
                sys.exit(f"{ruta}: la respuesta trae algo como una coordenada")
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
    with (destino / "planes_ruteo.txt").open("w", encoding="utf-8") as salida:
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


# --- la red vial real (B): prepararla y servirla con los binarios nativos -------------------------


def _pico_de(proceso: subprocess.Popen) -> float | None:
    """La memoria residente pico de un proceso hijo que ya termino, en MiB, leida de su handle
    (Windows); en otro sistema, None."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class Contadores(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    informacion = ctypes.windll.psapi.GetProcessMemoryInfo
    informacion.argtypes = [wintypes.HANDLE, ctypes.POINTER(Contadores), wintypes.DWORD]
    informacion.restype = wintypes.BOOL
    contadores = Contadores()
    contadores.cb = ctypes.sizeof(Contadores)
    if not informacion(
        wintypes.HANDLE(int(proceso._handle)), ctypes.byref(contadores), contadores.cb
    ):
        return None
    return round(contadores.PeakWorkingSetSize / 2**20, 1)


def _ejecutable(binarios: Path, nombre: str) -> Path:
    ruta = binarios / nombre
    return ruta if ruta.exists() else binarios / f"{nombre}.exe"


def preparar(argumentos: argparse.Namespace) -> dict:
    """La version de la red: su directorio por huella, los tres comandos de la preparacion con los
    binarios nativos (cada uno con su tiempo y su memoria pico) y su publicacion con el manifiesto
    verificado. Si esa huella ya esta PUBLICADA, la reutiliza sin prepararla."""
    from datetime import date

    from motor_cartera.db.sesion import sesion
    from motor_cartera.ruteo_v2 import red_vial

    binarios = Path(argumentos.osrm_bin)
    descripcion = red_vial.Descripcion(
        proveedor_datos="OpenStreetMap",
        licencia="ODbL-1.0",
        region=argumentos.region,
        fecha_snapshot=date.fromisoformat(argumentos.fecha_snapshot),
        proveedor_extracto=argumentos.proveedor_extracto,
        url_fuente=argumentos.url_fuente,
    )
    preparacion = red_vial.preparar_directorio(
        Path(argumentos.fuente),
        descripcion,
        red_vial.distribucion_nativa(binarios),
        Path(argumentos.redes),
    )
    with sesion() as s:
        registro = red_vial.abrir_preparacion(s, preparacion)
        s.commit()
        reutilizada = registro.reutilizada
    comandos: list[dict] = []
    if not reutilizada:
        for comando in red_vial.comandos(preparacion.archivo_fuente, preparacion.huella):
            bitacora = preparacion.directorio / f"{comando[0].removeprefix('osrm-')}.log"
            inicio = time.perf_counter()
            with bitacora.open("wb") as salida:
                proceso = subprocess.Popen(
                    [str(_ejecutable(binarios, comando[0])), *comando[1:]],
                    cwd=preparacion.directorio,
                    stdout=salida,
                    stderr=subprocess.STDOUT,
                )
                codigo = proceso.wait()
            comandos.append(
                {
                    "comando": comando[0],
                    "segundos": round(time.perf_counter() - inicio, 3),
                    "memoria_pico_mb": _pico_de(proceso),
                    "codigo": codigo,
                }
            )
            if codigo != 0:
                sys.exit(f"{comando[0]} termino con codigo {codigo}; ver {bitacora}.")
        with sesion() as s:
            red_vial.registrar(s, preparacion.directorio)
            s.commit()
    with sesion() as s:
        version = red_vial.abrir_preparacion(s, preparacion).version
        datos = {
            "red_vial_version_id": str(version.red_vial_version_id),
            "huella": version.huella,
            "estado": str(version.estado),
            "hash_manifest": version.hash_manifest,
            "cobertura": (version.metadatos or {}).get("cobertura", {}),
            "sha256_fuente": version.sha256_fuente,
            "region": version.region,
        }
    archivos = sorted(preparacion.directorio.glob("red.osrm*"))
    return {
        **datos,
        "reutilizada": reutilizada,
        "directorio": str(preparacion.directorio),
        "fuente_bytes": Path(argumentos.fuente).stat().st_size,
        "preparados_bytes": sum(a.stat().st_size for a in archivos),
        "archivos": len(archivos),
        "mayores": [
            {"archivo": a.name, "bytes": a.stat().st_size}
            for a in sorted(archivos, key=lambda a: -a.stat().st_size)[:6]
        ],
        "comandos": comandos,
    }


def _servir(binarios: Path, directorio: Path, salida: Path) -> tuple[subprocess.Popen, float]:
    """osrm-routed sirviendo la red, y lo que tardo en responder la sonda."""
    from motor_cartera.ruteo_v2.proveedor import ClienteOsrm, ConfiguracionOsrm

    bitacora = salida.open("ab")
    inicio = time.perf_counter()
    proceso = subprocess.Popen(
        [
            str(_ejecutable(binarios, "osrm-routed")),
            "--algorithm",
            "mld",
            "--verbosity",
            "WARNING",
            "--port",
            str(PUERTO_OSRM),
            "--max-nearest-locations",
            "100",
            str(directorio / "red.osrm"),
        ],
        stdout=bitacora,
        stderr=subprocess.STDOUT,
    )
    cliente = ClienteOsrm(ConfiguracionOsrm(f"http://127.0.0.1:{PUERTO_OSRM}"))
    while not cliente.salud():
        if proceso.poll() is not None or time.perf_counter() - inicio > 600:
            sys.exit(f"osrm-routed no respondio; ver {salida}.")
        time.sleep(0.2)
    return proceso, round(time.perf_counter() - inicio, 3)


# --- el reporte -----------------------------------------------------------------------------------


def _mib(valor: int | float | None) -> str:
    return "-" if valor is None else f"{valor / 2**20:,.1f} MiB"


def _num(valor, sufijo: str = "") -> str:
    return "-" if valor is None else f"{valor:,}{sufijo}"


def _ejecucion_md(titulo: str, r: dict, nota: str) -> list[str]:
    c, d, q = r["conteos"], r["distribucion"] or {}, r["calidad"]
    matrices = d.get("matrices", {})
    puntos = matrices.get("puntos", {})
    lineas = [
        f"## {titulo}",
        "",
        nota,
        "",
        f"ruteo_run_id `{r['ruteo_run_id']}`, {r['version_politica']} (hash "
        f"`{r['hash_politica'][:16]}...`), firma `{r['firma_inputs'][:16]}...`: "
        f"{_num(c['paradas_objetivo'])} paradas de {_num(c['gestores_objetivo'])} gestores en "
        f"{r['segundos']} s ({_num(r.get('paradas_por_segundo'))} por segundo), memoria pico del "
        f"proceso {r['memoria_pico_mb']} MiB, WAL {r['wal_mib']} MiB.",
        "",
        "| Conteo | Valor |",
        "|---|---:|",
        *(f"| {k} | {_num(v)} |" for k, v in c.items()),
        *(f"| {k} | {_num(v)} |" for k, v in r["totales"].items()),
        "",
        "Motivos: " + ", ".join(f"{k} {_num(v)}" for k, v in q["motivos"].items()) + ".",
        "",
        f"Matrices: {_num(matrices.get('matrices'))} en {_num(matrices.get('bloques'))} bloques, "
        f"{_num(matrices.get('celdas'))} celdas; puntos por matriz: minimo {puntos.get('minimo')}, "
        f"media {puntos.get('media')}, mediana {puntos.get('mediana')}, p95 {puntos.get('p95')}, "
        f"maximo {puntos.get('maximo')}.",
        "",
        f"Puntos distintos que se le preguntaron al proveedor (las cuentas con la misma "
        f"coordenada son un solo punto): {_num(d.get('snap', {}).get('puntos_consultados'))}.",
        "",
        f"Snap de las paradas ({_num(q['snap_m']['con_snap'])} con snap): p50 "
        f"{q['snap_m']['p50']} m, p95 {q['snap_m']['p95']} m, p99 {q['snap_m']['p99']} m, maximo "
        f"{q['snap_m']['maximo']} m.",
        "",
        f"De las {_num(q['rutas_que_salen'])} rutas que salen de su base "
        f"({_num(q['rutas_con_2opt'])} mejoradas por el 2-opt):",
        "",
        "| Medida por ruta | n | p50 | p95 | Maximo |",
        "|---|---:|---:|---:|---:|",
        *(
            f"| {nombre} | {_num(q[clave].get('n'))} | {q[clave].get('p50')} | "
            f"{q[clave].get('p95')} | {q[clave].get('maximo')} |"
            for nombre, clave in (
                ("Paradas programadas", "programadas_por_ruta"),
                ("Distancia (m)", "distancia_por_ruta_m"),
                ("Traslado (s)", "traslado_por_ruta_s"),
                ("Espera (s)", "espera_por_ruta_s"),
                ("Duracion total (s)", "duracion_por_ruta_s"),
                ("Mejoras del 2-opt", "mejoras_2opt_por_ruta"),
            )
        ),
        "",
        "| Fase | Segundos |",
        "|---|---:|",
        *(f"| {fase} | {segundos} |" for fase, segundos in r["fases"].items()),
        "",
        "| Servicio | Peticiones | p50 ms | p95 ms | Maximo ms |",
        "|---|---:|---:|---:|---:|",
        *(
            f"| {servicio} | {_num(r['peticiones'].get(servicio))} | {lat.get('p50')} | "
            f"{lat.get('p95')} | {lat.get('maximo')} |"
            for servicio, lat in r["latencias_ms"].items()
        ),
        "",
        f"Reintentos {r['reintentos']}; bytes enviados {_num(r['bytes_enviados'])}, recibidos "
        f"{_num(r['bytes_recibidos'])}.",
        "",
    ]
    return lineas


def _reconstruccion_md(original: dict, otra: dict) -> list[str]:
    igual = original["hash_logico"] == otra["hash_logico"]
    return [
        f"Reconstruccion: se borro la ejecucion (solo lo derivado de ella) y se volvio a pedir con "
        f"las mismas entradas: {otra['segundos']} s, firma "
        f"{'igual' if otra['firma_inputs'] == original['firma_inputs'] else 'DISTINTA'}, hash "
        f"logico de lo publicado `{otra['hash_logico'][:16]}...`: "
        f"{'identico' if igual else 'DISTINTO'}.",
        "",
    ]


def _markdown(reporte: dict) -> str:
    lineas = [
        "# Benchmark de ruteo/v2",
        "",
        f"- Maquina: {reporte['maquina']}",
        f"- PostgreSQL: {reporte['postgresql']}",
        f"- Plan de campo/v2 `{reporte['plan']['campo_run_id']}` del {reporte['plan']['fecha']}: "
        f"{_num(reporte['plan']['asignadas'])} cuentas ASIGNADA, del benchmark de campo/v2",
        "",
    ]
    if "planificador" in reporte:
        a = reporte["planificador"]
        lineas += _ejecucion_md(
            "A. El planificador, con un proveedor SINTETICO (no es una red vial)",
            a["ruteo"],
            "Modelo geometrico declarado antes de ver ninguna ruta: "
            + "; ".join(f"{k}: {v}" for k, v in MODELO_SINTETICO.items())
            + f". Version de la red `{a['red']['red_vial_version_id']}` (SINTETICO).",
        )
        lineas += _reconstruccion_md(a["ruteo"], a["reconstruccion"])
    if "red" in reporte:
        b = reporte["red"]
        p = b["preparacion"]
        lineas += [
            "## B. La red vial real: OSRM v26.9.0 (MLD) sobre un extracto de OpenStreetMap",
            "",
            f"Red `{p['red_vial_version_id']}`, huella `{p['huella'][:16]}...`: {p['region']}. "
            f"Fuente {_mib(p['fuente_bytes'])} (SHA-256 `{p['sha256_fuente'][:16]}...`); "
            f"cobertura: "
            + ", ".join(f"{k} {_num(v)}" for k, v in p["cobertura"].items())
            + f". Archivos preparados: {p['archivos']}, {_mib(p['preparados_bytes'])}.",
            "",
            "| Comando | Segundos | Memoria pico |",
            "|---|---:|---:|",
            *(
                f"| {c['comando']} | {c['segundos']} | {c['memoria_pico_mb']} MiB |"
                for c in p["comandos"]
            ),
            "",
            f"osrm-routed respondio a los {b['carga_s']} s de arrancar; su memoria pico, al "
            f"terminar el benchmark: {b['osrm_memoria_pico_mb']} MiB.",
            "",
        ]
        lineas += _ejecucion_md(
            "B. El ruteo del plan contra la red real",
            b["ruteo"],
            "Las cuentas son sinteticas (puntos al azar dentro de municipios reales); las bases "
            "son las del benchmark de campo/v2. Una base fuera del extracto no tiene snap valido: "
            "sus paradas son NO_ENRUTABLE_BASE y no se envia ninguna.",
        )
        lineas += _reconstruccion_md(b["ruteo"], b["reconstruccion"])
        antes = b.get("reconstruccion_antes_de_la_correccion")
        if antes is not None:
            lineas += [
                f"La primera corrida tuvo {b['ruteo']['reintentos']} reintentos: en Windows, una "
                "conexion persistente que OSRM cerro por inactividad se vio como "
                "ConnectionAbortedError, que el cliente no reconocia como una conexion cerrada, y "
                "gasto un intento con su espera. Corregido, la reconstruccion de arriba tuvo "
                f"{b['reconstruccion']['reintentos']} (la anterior, "
                f"{antes['reintentos']}, en {antes['segundos']} s, con el mismo hash).",
                "",
            ]
    lineas += [
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
        f"{_mib(reporte['tamanos_despues']['base_bytes'])} con todo lo de este benchmark.",
        "",
    ]
    if "consultas" in reporte:
        consultas = reporte["consultas"]
        lineas += [
            "## Consultas por la API",
            "",
            "| Consulta | Mediana | p95 | Maximo | Sentencias | En PostgreSQL |",
            "|---|---:|---:|---:|---:|---:|",
            *(
                f"| {nombre} | {t['mediana_ms']} ms | {t['p95_ms']} ms | {t['maximo_ms']} ms | "
                f"{consultas['sentencias'][nombre]} | "
                f"{consultas['milisegundos_en_postgresql'][nombre]} ms |"
                for nombre, t in consultas["tiempos"].items()
            ),
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


def _plan(campo_run_id: str) -> dict:
    filas = _sql(
        "SELECT campo_run_id, fecha_operacion, asignadas FROM ejecucion_campo_v2 "
        "WHERE campo_run_id = :c AND estado = 'EXITOSA'",
        {"c": campo_run_id},
    )
    if len(filas) != 1:
        sys.exit(f"No hay un plan de campo/v2 EXITOSA con campo_run_id {campo_run_id}.")
    ((campo, fecha, asignadas),) = filas
    return {"campo_run_id": str(campo), "fecha": fecha.isoformat(), "asignadas": asignadas}


def _red_sintetica() -> dict:
    from motor_cartera.db.sesion import sesion
    from motor_cartera.ruteo_v2 import red_vial

    with sesion() as s:
        registro = red_vial.registrar_sintetica(s, MODELO_SINTETICO["nombre"], MODELO_SINTETICO)
        s.commit()
        s.refresh(registro.version)
        return {
            "red_vial_version_id": str(registro.version.red_vial_version_id),
            "huella": registro.version.huella,
            "reutilizada": registro.reutilizada,
        }


def main(argumentos: argparse.Namespace) -> None:
    _exigir_bench()
    destino = argumentos.destino.resolve()
    nombre = f"benchmark_ruteo_{argumentos.etapa}"
    bitacora = Bitacora(destino / f"{nombre}.etapas.json", argumentos.reanudar)

    print("1. la base: a la cabeza de las migraciones, sin reconstruir nada", flush=True)
    _migrar()
    if "tamanos_antes" not in bitacora:
        bitacora.anotar("tamanos_antes", _tamanos(TABLAS_NUEVAS))
    plan = (
        bitacora["plan"] if "plan" in bitacora else bitacora.anotar("plan", _plan(argumentos.plan))
    )
    print(f"   plan {plan['campo_run_id']}: {plan['asignadas']:,} asignadas", flush=True)

    reporte_parcial: dict = {}
    if argumentos.etapa == "planificador":
        print("2. la version SINTETICO de la red (no es una red vial)", flush=True)
        red = bitacora["red"] if "red" in bitacora else bitacora.anotar("red", _red_sintetica())
        print("3. el ruteo del plan con el proveedor geometrico", flush=True)
        entradas = ["--plan", plan["campo_run_id"], "--red", red["red_vial_version_id"]]
        if "ruteo" not in bitacora:
            bitacora.anotar("ruteo", _hijo("rutear", *entradas, "--modo", "sintetico"))
        ruteo = bitacora["ruteo"]
        print(f"   {ruteo['conteos']} en {ruteo['segundos']} s", flush=True)
        print("4. la reconstruccion: borrar y volver a pedir", flush=True)
        if "reconstruccion" not in bitacora:
            bitacora.anotar("borrado", _hijo("borrar", "--ruteo", ruteo["ruteo_run_id"]))
            bitacora.anotar("reconstruccion", _hijo("rutear", *entradas, "--modo", "sintetico"))
        otra = bitacora["reconstruccion"]
        print(
            f"   hash {'identico' if otra['hash_logico'] == ruteo['hash_logico'] else 'DISTINTO'}"
        )
        print("5. lo que ocupa", flush=True)
        if "tamanos_despues" not in bitacora:
            bitacora.anotar("vacuum_s", _vacuum(TABLAS_NUEVAS))
            bitacora.anotar("tamanos_despues", _tamanos(TABLAS_NUEVAS))
        print("6. las consultas y sus planes", flush=True)
        if "consultas" not in bitacora:
            bitacora.anotar(
                "consultas",
                _hijo("consultar", "--destino", str(destino), "--ruteo", otra["ruteo_run_id"]),
            )
        reporte_parcial = {
            "planificador": {"red": red, "ruteo": ruteo, "reconstruccion": otra},
            "consultas": bitacora["consultas"],
        }
    else:
        print("2. la red: prepararla con los binarios nativos y publicarla", flush=True)
        if "preparacion" not in bitacora:
            bitacora.anotar("preparacion", preparar(argumentos))
        p = bitacora["preparacion"]
        print(f"   red {p['red_vial_version_id']}: {p['cobertura']}", flush=True)
        print("3. servirla con osrm-routed y rutear el plan", flush=True)
        if "reconstruccion" not in bitacora:
            proceso, carga = _servir(
                Path(argumentos.osrm_bin), Path(p["directorio"]), destino / "osrm-routed.log"
            )
            try:
                url = f"http://127.0.0.1:{PUERTO_OSRM}"
                entradas = ["--plan", plan["campo_run_id"], "--red", p["red_vial_version_id"]]
                if "ruteo" not in bitacora:
                    bitacora.anotar("carga_s", carga)
                    bitacora.anotar(
                        "ruteo", _hijo("rutear", *entradas, "--modo", "osrm", "--url", url)
                    )
                ruteo = bitacora["ruteo"]
                print(f"   {ruteo['conteos']} en {ruteo['segundos']} s", flush=True)
                print("4. la reconstruccion: borrar y volver a pedir", flush=True)
                bitacora.anotar("borrado", _hijo("borrar", "--ruteo", ruteo["ruteo_run_id"]))
                bitacora.anotar(
                    "reconstruccion", _hijo("rutear", *entradas, "--modo", "osrm", "--url", url)
                )
            finally:
                proceso.terminate()
                proceso.wait()
                bitacora.anotar("osrm_memoria_pico_mb", _pico_de(proceso))
        ruteo, otra = bitacora["ruteo"], bitacora["reconstruccion"]
        print(
            f"   hash {'identico' if otra['hash_logico'] == ruteo['hash_logico'] else 'DISTINTO'}"
        )
        print("5. lo que ocupa", flush=True)
        if "tamanos_despues" not in bitacora:
            bitacora.anotar("vacuum_s", _vacuum(TABLAS_NUEVAS))
            bitacora.anotar("tamanos_despues", _tamanos(TABLAS_NUEVAS))
        reporte_parcial = {
            "red": {
                "preparacion": p,
                "carga_s": bitacora["carga_s"],
                "osrm_memoria_pico_mb": bitacora["osrm_memoria_pico_mb"],
                "ruteo": ruteo,
                "reconstruccion": otra,
                **(
                    {"reconstruccion_antes_de_la_correccion": bitacora[clave]}
                    if (clave := "reconstruccion_antes_de_la_correccion") in bitacora
                    else {}
                ),
            }
        }

    ((version,),) = _sql("SELECT version()")
    reporte = {
        "maquina": f"{platform.system()} {platform.release()}, {os.cpu_count()} CPU, "
        f"Python {platform.python_version()}",
        "postgresql": version.split(",")[0],
        "plan": plan,
        **reporte_parcial,
        "tamanos_antes": bitacora["tamanos_antes"],
        "tamanos_despues": bitacora["tamanos_despues"],
        "vacuum_s": bitacora["vacuum_s"],
        "recuperadas": bitacora.recuperadas(),
    }
    (destino / f"{nombre}.json").write_text(
        json.dumps(reporte, indent=1, ensure_ascii=False, default=str), encoding="utf-8"
    )
    (destino / f"{nombre}.md").write_text(_markdown(reporte), encoding="utf-8")
    print(f"Reporte: {destino / f'{nombre}.md'}")


if __name__ == "__main__":
    lector = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    etapas = lector.add_subparsers(dest="etapa", required=True)
    for orquestador in ("planificador", "red"):
        o = etapas.add_parser(orquestador)
        o.add_argument("--destino", type=Path, required=True)
        o.add_argument("--plan", required=True, help="El campo_run_id del plan que se rutea.")
        o.add_argument("--reanudar", action="store_true")
        if orquestador == "red":
            o.add_argument("--fuente", required=True, help="El .osm.pbf del extracto.")
            o.add_argument("--osrm-bin", required=True, help="Los binarios nativos de OSRM.")
            o.add_argument(
                "--redes", default="datos/red_vial_bench", help="Un directorio por huella."
            )
            o.add_argument("--region", required=True)
            o.add_argument("--fecha-snapshot", required=True)
            o.add_argument("--url-fuente", required=True)
            o.add_argument("--proveedor-extracto", required=True)
    de_rutear = etapas.add_parser("rutear")
    de_rutear.add_argument("--plan", required=True)
    de_rutear.add_argument("--red", required=True)
    de_rutear.add_argument("--modo", choices=["sintetico", "osrm"], required=True)
    de_rutear.add_argument("--url")
    etapas.add_parser("borrar").add_argument("--ruteo", required=True)
    de_consultar = etapas.add_parser("consultar")
    de_consultar.add_argument("--destino", type=Path, required=True)
    de_consultar.add_argument("--ruteo", required=True)
    leidos = lector.parse_args()
    if leidos.etapa == "rutear":
        print(json.dumps(rutear(leidos.plan, leidos.red, leidos.modo, leidos.url), default=str))
    elif leidos.etapa == "borrar":
        print(json.dumps(borrar(leidos.ruteo)))
    elif leidos.etapa == "consultar":
        print(json.dumps(consultar(leidos.destino, leidos.ruteo), default=str))
    else:
        main(leidos)
