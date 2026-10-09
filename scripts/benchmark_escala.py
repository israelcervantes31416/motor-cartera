"""Benchmark de escala: cuanto tarda y cuanta memoria usa cada fase de una fuente oficial grande, de
punta a punta y contra PostgreSQL. No corre en el CI: se pide a mano, con su perfil.

    python scripts/benchmark_escala.py --perfil XL --formato zip
    python scripts/benchmark_escala.py --perfil XXL --formato csv --sin-pagos

Las fases, cada una medida por separado:

1. generacion: el estado de las cuentas y el archivo de cartera/v2 (y, aparte, el de pagos/v1).
2. almacen: guardar el archivo original en el almacen por contenido (SHA-256, copia y fsync).
3. validacion: leer por lotes, juzgar cada registro contra el contrato, revisar las llaves globales
   y firmar el contenido, en las dos pasadas.
4. conformado: escribir el dataset conformado en Parquet y guardarlo en el almacen.
5. proyeccion: llevar cada registro valido a la forma de Cuenta (solo cartera/v2).
6. persistencia: COPY de las cuentas y los rechazos a PostgreSQL y el commit.

La generacion y cada ingesta corren en su propio proceso, asi que la memoria pico de cada una es
solo suya. Escribe un JSON con todo y una tabla en Markdown para la documentacion.

Necesita una PostgreSQL en MC_DATABASE_URL cuya base se llame *_bench: se le aplican las
migraciones, se vacia y se le escriben cientos de miles de filas, asi que nunca es la de desarrollo
ni la de pruebas. El almacen de artefactos es MC_SOURCE_STORE_ROOT, o un directorio temporal si no
se define.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
CORTE = date(2026, 9, 30)
DESDE = CORTE - timedelta(days=6)

# Las fases que registra la ingesta, agrupadas en las del reporte. Ninguna se anida en otra.
GRUPOS = {
    "almacen": ("almacen",),
    "validacion": (
        "registro",
        "verificacion",
        "lectura",
        "validacion",
        "provisional",
        "lectura_provisional",
        "firma",
        "companera",
    ),
    "conformado": ("conformado", "almacen_conformado"),
    "proyeccion": ("proyeccion",),
    "persistencia": ("persistencia", "commit"),
}


def memoria_pico_mb() -> float | None:
    """La memoria residente pico de este proceso, en MiB, si el sistema operativo la dice."""
    if sys.platform == "win32":
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

        actual = ctypes.windll.kernel32.GetCurrentProcess
        actual.restype = wintypes.HANDLE
        informacion = ctypes.windll.psapi.GetProcessMemoryInfo
        informacion.argtypes = [wintypes.HANDLE, ctypes.POINTER(Contadores), wintypes.DWORD]
        informacion.restype = wintypes.BOOL
        contadores = Contadores()
        contadores.cb = ctypes.sizeof(Contadores)
        if not informacion(actual(), ctypes.byref(contadores), contadores.cb):
            return None
        return round(contadores.PeakWorkingSetSize / 2**20, 1)
    try:
        import resource
    except ImportError:
        return None
    pico = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # En Linux ru_maxrss viene en KiB; en macOS, en bytes.
    return round(pico / (2**20 if sys.platform == "darwin" else 2**10), 1)


# --- las etapas, cada una en su proceso -----------------------------------------------------------


def generar(perfil: str, formato: str, destino: Path, semilla: int, pagos: bool) -> dict:
    from motor_cartera.generador.oficial import (
        PERFILES,
        escribir_cartera,
        escribir_pagos,
        estado_inicial,
        tabla_pagos,
    )

    destino.mkdir(parents=True, exist_ok=True)
    inicio = time.perf_counter()
    estado = estado_inicial(PERFILES[perfil], semilla=semilla, fecha_corte=CORTE)
    ruta = destino / f"cartera_oficial_{CORTE.isoformat()}.{formato}"
    escrito = escribir_cartera(estado, ruta, semilla=semilla, fecha_corte=CORTE)
    resultado = {
        "cartera": {
            "ruta": str(escrito.ruta),
            "filas": escrito.filas,
            "filas_carrier": escrito.filas_carrier,
            "bytes": escrito.ruta.stat().st_size,
            "segundos": round(time.perf_counter() - inicio, 3),
        }
    }
    if pagos:
        inicio = time.perf_counter()
        movimientos = tabla_pagos(estado, semilla=semilla, desde=DESDE, hasta=CORTE)
        nombre = f"pagos_oficial_{DESDE.isoformat()}_{CORTE.isoformat()}.{formato}"
        escritos = escribir_pagos(movimientos, destino / nombre)
        resultado["pagos"] = {
            "ruta": str(escritos.ruta),
            "filas": escritos.filas,
            "bytes": escritos.ruta.stat().st_size,
            "segundos": round(time.perf_counter() - inicio, 3),
        }
    resultado["memoria_pico_mb"] = memoria_pico_mb()
    return resultado


def ingerir_cartera(ruta: Path) -> dict:
    from sqlmodel import select

    from motor_cartera.config import config
    from motor_cartera.db.modelos import Corrida
    from motor_cartera.db.sesion import sesion
    from motor_cartera.fuentes.artefactos import almacen_de, guardar_artefacto
    from motor_cartera.ingesta.cartera_v2 import juzgar_y_publicar
    from motor_cartera.ingesta.corridas import abrir_corrida
    from motor_cartera.ingesta.fuente_oficial import Cronometro

    cronometro = Cronometro()
    with cronometro.fase("almacen"), ruta.open("rb") as archivo:
        guardado = guardar_artefacto(almacen_de(config), archivo, ruta.name)
    with sesion() as s:
        with cronometro.fase("registro"):
            abierta = abrir_corrida(
                s, origen=ruta.name, guardado=guardado, contrato="cartera/v2", fecha_corte=CORTE
            )
            corrida = s.exec(
                select(Corrida).where(Corrida.id == abierta.id).with_for_update()
            ).one()
        juzgar_y_publicar(s, corrida, config=config, cronometro=cronometro)
        with cronometro.fase("commit"):
            s.commit()
        return _resultado(corrida, cronometro)


def ingerir_pagos(ruta: Path) -> dict:
    from sqlmodel import select

    from motor_cartera.config import config
    from motor_cartera.db.modelos import IngestaPagos
    from motor_cartera.db.sesion import sesion
    from motor_cartera.fuentes.artefactos import almacen_de, guardar_artefacto
    from motor_cartera.ingesta.fuente_oficial import Cronometro
    from motor_cartera.ingesta.pagos import abrir_ingesta_pagos, juzgar_y_aceptar

    cronometro = Cronometro()
    with cronometro.fase("almacen"), ruta.open("rb") as archivo:
        guardado = guardar_artefacto(almacen_de(config), archivo, ruta.name)
    with sesion() as s:
        with cronometro.fase("registro"):
            abierta = abrir_ingesta_pagos(s, origen=ruta.name, guardado=guardado)
            ingesta = s.exec(
                select(IngestaPagos).where(IngestaPagos.id == abierta.id).with_for_update()
            ).one()
        juzgar_y_aceptar(s, ingesta, config=config, cronometro=cronometro)
        with cronometro.fase("commit"):
            s.commit()
        return _resultado(ingesta, cronometro)


def _resultado(recurso, cronometro) -> dict:
    fases = {nombre: round(segundos, 3) for nombre, segundos in cronometro.fases.items()}
    return {
        "estado": str(recurso.estado),
        "filas_leidas": recurso.filas_leidas,
        "filas_validas": recurso.filas_validas,
        "filas_rechazadas": recurso.filas_rechazadas,
        "fases": fases,
        "memoria_pico_mb": memoria_pico_mb(),
    }


# --- el orquestador -------------------------------------------------------------------------------


def _hijo(*argumentos: str) -> dict:
    """Corre una etapa en su propio proceso y devuelve lo que reporto."""
    proceso = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), *argumentos],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    if proceso.returncode != 0:
        print(proceso.stdout, proceso.stderr, sep="\n", file=sys.stderr)
        sys.exit(f"La etapa {argumentos[0]} fallo con codigo {proceso.returncode}.")
    return json.loads(proceso.stdout.strip().splitlines()[-1])


def _preparar_base() -> None:
    """Las migraciones, y la base vacia: el mismo archivo no se puede publicar dos veces."""
    from alembic import command
    from alembic.config import Config as ConfigAlembic
    from sqlalchemy import create_engine, make_url, text

    url = make_url(os.environ.get("MC_DATABASE_URL", ""))
    if not (url.database or "").endswith("_bench"):
        oculta = url.render_as_string(hide_password=True)
        sys.exit(f"MC_DATABASE_URL tiene que apuntar a una base *_bench, no a {oculta!r}.")
    servidor = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with servidor.connect() as conexion:
        existe = conexion.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :nombre"), {"nombre": url.database}
        )
        if not existe:
            conexion.execute(text(f'CREATE DATABASE "{url.database}"'))
    servidor.dispose()
    cfg = ConfigAlembic(str(RAIZ / "alembic.ini"))
    cfg.set_main_option("script_location", str(RAIZ / "migraciones"))
    command.upgrade(cfg, "head")
    motor = create_engine(url)
    with motor.begin() as conexion:
        conexion.execute(
            text(
                "TRUNCATE corrida, cuenta, rechazo, artefacto_fuente, cuenta_canonica, "
                "ejecucion_motor_pagos RESTART IDENTITY CASCADE"
            )
        )
    motor.dispose()


def _agrupar(fases: dict[str, float]) -> dict[str, float]:
    grupos = {grupo: round(sum(fases.get(f, 0.0) for f in de), 3) for grupo, de in GRUPOS.items()}
    conocidas = {f for de in GRUPOS.values() for f in de}
    grupos["otras"] = round(sum(s for f, s in fases.items() if f not in conocidas), 3)
    return grupos


def _fila(nombre: str, generado: dict, ingesta: dict) -> dict:
    grupos = _agrupar(ingesta["fases"])
    total = round(sum(grupos.values()), 3)
    return {
        "fuente": nombre,
        "filas": ingesta["filas_leidas"],
        "validas": ingesta["filas_validas"],
        "estado": ingesta["estado"],
        "bytes_archivo": generado["bytes"],
        "generacion_s": generado["segundos"],
        **{f"{grupo}_s": segundos for grupo, segundos in grupos.items()},
        "ingesta_total_s": total,
        "filas_por_segundo": round(ingesta["filas_leidas"] / total) if total else None,
        "memoria_pico_ingesta_mb": ingesta["memoria_pico_mb"],
        "fases": ingesta["fases"],
    }


def _markdown(reporte: dict) -> str:
    encabezado = (
        "| Fuente | Filas | Archivo | Generacion | Almacen | Validacion | Conformado | "
        "Proyeccion | Persistencia | Ingesta total | Filas/s | Memoria pico |"
    )
    lineas = [encabezado, "|" + "---|" * 12]
    for fila in reporte["resultados"]:
        lineas.append(
            f"| {fila['fuente']} | {fila['filas']:,} | {fila['bytes_archivo'] / 2**20:,.1f} MiB | "
            f"{fila['generacion_s']:,.1f} s | {fila['almacen_s']:,.1f} s | "
            f"{fila['validacion_s']:,.1f} s | {fila['conformado_s']:,.1f} s | "
            f"{fila['proyeccion_s']:,.1f} s | {fila['persistencia_s']:,.1f} s | "
            f"{fila['ingesta_total_s']:,.1f} s | {fila['filas_por_segundo'] or 0:,} | "
            f"{fila['memoria_pico_ingesta_mb'] or 0:,.0f} MiB |"
        )
    maquina = reporte["maquina"]
    lineas.append("")
    lineas.append(
        f"Perfil {reporte['perfil']} en {reporte['formato']}, semilla {reporte['semilla']}; "
        f"memoria pico de la generacion: {reporte['generacion']['memoria_pico_mb'] or 0:,.0f} MiB. "
        f"{maquina['sistema']}, Python {maquina['python']}, {maquina['cpus']} CPU."
    )
    return "\n".join(lineas)


def main(argumentos: argparse.Namespace) -> None:
    from motor_cartera.generador.oficial import PERFILES

    perfil = argumentos.perfil.upper()
    if perfil not in PERFILES:
        sys.exit(f"Perfil desconocido: {perfil}. Usa {', '.join(PERFILES)}.")
    formato = argumentos.formato.lower().lstrip(".")
    temporal = Path(tempfile.mkdtemp(prefix="motor-cartera-benchmark-"))
    destino = Path(argumentos.destino) if argumentos.destino else temporal / "fuentes"
    os.environ.setdefault("MC_SOURCE_STORE_ROOT", str(temporal / "almacen"))
    _preparar_base()

    print(f"Generando el perfil {perfil} ({PERFILES[perfil]:,} cuentas) en {formato}...")
    generado = _hijo(
        "generar",
        "--perfil",
        perfil,
        "--formato",
        formato,
        "--destino",
        str(destino),
        "--semilla",
        str(argumentos.semilla),
        *(["--sin-pagos"] if argumentos.sin_pagos else []),
    )
    print("Ingiriendo cartera/v2...")
    resultados = [
        _fila(
            "cartera/v2",
            generado["cartera"],
            _hijo("cartera", "--ruta", generado["cartera"]["ruta"]),
        )
    ]
    if not argumentos.sin_pagos:
        print("Ingiriendo pagos/v1...")
        resultados.append(
            _fila(
                "pagos/v1", generado["pagos"], _hijo("pagos", "--ruta", generado["pagos"]["ruta"])
            )
        )
    reporte = {
        "perfil": perfil,
        "cuentas": PERFILES[perfil],
        "formato": formato,
        "semilla": argumentos.semilla,
        "fecha_corte": CORTE.isoformat(),
        "generacion": generado,
        "resultados": resultados,
        "maquina": {
            "sistema": f"{platform.system()} {platform.release()}",
            "procesador": platform.processor() or platform.machine(),
            "cpus": os.cpu_count(),
            "python": platform.python_version(),
        },
    }
    salida = Path(argumentos.salida) if argumentos.salida else destino / f"benchmark_{perfil}.json"
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps(reporte, indent=2, ensure_ascii=False), encoding="utf-8")
    print(_markdown(reporte))
    print(f"\nReporte: {salida}")
    if any(fila["estado"] not in ("EXITOSA",) for fila in resultados):
        sys.exit("Alguna ingesta no termino EXITOSA: el benchmark no es valido.")


if __name__ == "__main__":
    lector = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    etapas = lector.add_subparsers(dest="etapa")
    de_generar = etapas.add_parser("generar")
    de_generar.add_argument("--perfil", required=True)
    de_generar.add_argument("--formato", required=True)
    de_generar.add_argument("--destino", required=True, type=Path)
    de_generar.add_argument("--semilla", required=True, type=int)
    de_generar.add_argument("--sin-pagos", action="store_true")
    for nombre in ("cartera", "pagos"):
        etapas.add_parser(nombre).add_argument("--ruta", required=True, type=Path)
    lector.add_argument("--perfil", default="XL", help="XS, S, M, L, XL (por omision) o XXL.")
    lector.add_argument("--formato", default="zip", help="zip (por omision), csv o xlsx.")
    lector.add_argument("--destino", help="Donde se escriben los archivos generados.")
    lector.add_argument("--salida", help="El reporte JSON; por omision, junto a los archivos.")
    lector.add_argument("--semilla", type=int, default=31416)
    lector.add_argument("--sin-pagos", action="store_true", help="Solo cartera/v2.")
    leidos = lector.parse_args()
    if leidos.etapa == "generar":
        print(
            json.dumps(
                generar(
                    leidos.perfil,
                    leidos.formato,
                    leidos.destino,
                    leidos.semilla,
                    not leidos.sin_pagos,
                )
            )
        )
    elif leidos.etapa == "cartera":
        print(json.dumps(ingerir_cartera(leidos.ruta)))
    elif leidos.etapa == "pagos":
        print(json.dumps(ingerir_pagos(leidos.ruta)))
    else:
        main(leidos)
