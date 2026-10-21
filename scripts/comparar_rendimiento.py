"""La senal de rendimiento del perfil M (docs/rendimiento.md): lee los reportes de los benchmarks
de una o varias repeticiones, saca de cada capa sus metricas (segundos, memoria pico y la mediana de
sus consultas), toma la mediana de las repeticiones y la compara con la linea base.

Una metrica es una regresion solo si empeora mas que su tolerancia relativa **y** que la absoluta, a
la vez: unos puntos de ruido no la marcan, y una consulta de milisegundos tampoco por un porcentaje
grande de casi nada. Las tolerancias son de la linea base, por clase de metrica, y salen de lo que
varian las mediciones comparables (docs/rendimiento.md).

Cada repeticion es un directorio con los reportes de benchmark_escala, benchmark_motor_pagos,
benchmark_lifecycle y benchmark_decision_v2 (escala/, motor_pagos/, lifecycle/ y decision_v2/).
Escribe el resultado en JSON y en Markdown; termina con 1 si hay una regresion (o falta un reporte).

    python scripts/comparar_rendimiento.py --resultados r1 r2 r3 \\
        --linea-base docs/rendimiento/linea-base-M.json --salida rendimiento.json \\
        --markdown rendimiento.md
    python scripts/comparar_rendimiento.py --resultados r1 r2 r3 --escribir-linea-base <ruta>

Solo usa la biblioteca estandar.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

FORMATO = "rendimiento-motor-cartera/v1"
REPORTES = {
    "escala": "escala/benchmark_escala.json",
    "motor_pagos": "motor_pagos/benchmark_motor_pagos.json",
    "lifecycle": "lifecycle/benchmark_lifecycle.json",
    "decision": "decision_v2/benchmark_decision_v2.json",
}
UNIDADES = {"tiempo": "s", "memoria": "MiB", "consulta": "ms"}
TOLERANCIAS = {
    "tiempo": {"relativa": 0.60, "absoluta": 2.0},
    "memoria": {"relativa": 0.20, "absoluta": 64.0},
    "consulta": {"relativa": 0.50, "absoluta": 25.0},
}
"""Las de una linea base que no declara las suyas. Calibradas con dos tandas de tres corridas del
perfil M (docs/rendimiento.md): con las tres en la CPU mas lenta de los runners, un tiempo quedo
46 % arriba de la linea base sin ningun cambio de codigo, y la memoria vario menos de 10 %."""


def _fuente(escala: dict, nombre: str) -> dict:
    return next(r for r in escala["resultados"] if r["fuente"] == nombre)


def metricas(reportes: dict[str, dict]) -> dict[str, tuple[str, float]]:
    """Las metricas de una repeticion: nombre -> (clase, valor)."""
    escala, pagos = reportes["escala"], reportes["motor_pagos"]
    lifecycle, decision = reportes["lifecycle"], reportes["decision"]
    cartera, de_pagos = _fuente(escala, "cartera/v2"), _fuente(escala, "pagos/v1")
    valores: dict[str, tuple[str, float]] = {
        "ingesta.cartera_v2": ("tiempo", cartera["ingesta_total_s"]),
        "ingesta.pagos_v1": ("tiempo", de_pagos["ingesta_total_s"]),
        "historia.cortes": (
            "tiempo",
            sum(h["segundos"] for h in pagos["historias"] if h.get("tipo") == "CARTERA"),
        ),
        "historia.pagos": ("tiempo", pagos["totales"]["segundos_historia_pagos"]),
        "motor_pagos.interpretacion": ("tiempo", pagos["totales"]["segundos_motor"]),
        "lifecycle.importacion": ("tiempo", lifecycle["total_importacion"]["segundos"]),
        "lifecycle.atribucion": (
            "tiempo",
            sum(a["segundos"] for a in lifecycle["atribuciones"].values()),
        ),
        "lifecycle.evaluacion": ("tiempo", lifecycle["evaluacion"]["segundos"]),
        "decision_v1.decision": ("tiempo", decision["decision_v1"]["segundos"]),
        "decision_v2.decision": ("tiempo", decision["decision_v2"]["segundos"]),
        "ingesta.cartera_v2.memoria": ("memoria", cartera["memoria_pico_ingesta_mb"]),
        "motor_pagos.memoria": ("memoria", pagos["totales"]["memoria_pico_motor_mb"]),
        "decision_v2.memoria": ("memoria", decision["decision_v2"]["memoria_pico_mb"]),
    }
    for capa, reporte in (("pagos", pagos), ("lifecycle", lifecycle), ("decision", decision)):
        for consulta, tiempo in reporte["consultas"]["tiempos"].items():
            valores[f"consulta.{capa}.{consulta}"] = ("consulta", tiempo["mediana_ms"])
    return {nombre: (clase, float(valor)) for nombre, (clase, valor) in valores.items()}


def entrada(reportes: dict[str, dict]) -> dict:
    """Lo que se midio: el perfil, los cortes y el tamano de cada capa."""
    escala, pagos = reportes["escala"], reportes["motor_pagos"]
    lifecycle, decision = reportes["lifecycle"], reportes["decision"]
    return {
        "perfil": escala["perfil"],
        "cuentas": escala["cuentas"],
        "cortes": pagos["cortes"],
        "filas_cartera": _fuente(escala, "cartera/v2")["filas"],
        "filas_pagos": _fuente(escala, "pagos/v1")["filas"],
        "observaciones_de_pagos": pagos["totales"]["observaciones"],
        "eventos_lifecycle": lifecycle["total_importacion"]["eventos"],
        "cuentas_decididas": decision["decision_v2"]["cuentas_decididas"],
    }


def leer_repeticion(directorio: Path) -> dict[str, dict]:
    """Los cuatro reportes de una repeticion y, si lo tiene, el entorno donde se midio."""
    faltan = [r for r in REPORTES.values() if not (directorio / r).exists()]
    if faltan:
        raise FileNotFoundError(f"{directorio}: faltan {', '.join(faltan)}")
    reportes = {
        capa: json.loads((directorio / ruta).read_text(encoding="utf-8"))
        for capa, ruta in REPORTES.items()
    }
    archivo = directorio / "entorno.json"
    reportes["entorno"] = (
        json.loads(archivo.read_text(encoding="utf-8")) if archivo.exists() else {}
    )
    return reportes


def resumir(repeticiones: list[dict[str, tuple[str, float]]]) -> dict[str, dict]:
    """La mediana, el minimo, el maximo y el coeficiente de variacion de cada metrica: primero los
    tiempos, despues la memoria y al final las consultas."""
    clases = {nombre: clase for r in repeticiones for nombre, (clase, _) in r.items()}
    orden = list(UNIDADES)
    resumen: dict[str, dict] = {}
    for nombre in sorted(clases, key=lambda n: (orden.index(clases[n]), n)):
        valores = [r[nombre][1] for r in repeticiones if nombre in r]
        clase = clases[nombre]
        media = statistics.fmean(valores)
        cv = statistics.pstdev(valores) / media if len(valores) > 1 and media else 0.0
        resumen[nombre] = {
            "clase": clase,
            "unidad": UNIDADES[clase],
            "valor": round(statistics.median(valores), 3),
            "minimo": round(min(valores), 3),
            "maximo": round(max(valores), 3),
            "cv": round(cv, 4),
            "n": len(valores),
        }
    return resumen


def comparar(resumen: dict[str, dict], base: dict | None) -> dict[str, dict]:
    """El estado de cada metrica frente a la linea base: REGRESION o MEJORA solo si el cambio
    supera las dos tolerancias de su clase; DENTRO si no; SIN_BASE si la base no la tiene."""
    tolerancias = (base or {}).get("tolerancias", TOLERANCIAS)
    metricas_base = (base or {}).get("metricas", {})
    comparadas: dict[str, dict] = {}
    for nombre, actual in resumen.items():
        fila = dict(actual)
        anterior = metricas_base.get(nombre)
        if anterior is None or not anterior.get("valor"):
            fila.update({"base": None, "cambio": None, "estado": "SIN_BASE"})
        else:
            tolerancia = tolerancias.get(actual["clase"], TOLERANCIAS[actual["clase"]])
            diferencia = actual["valor"] - anterior["valor"]
            cambio = diferencia / anterior["valor"]
            fuera = (
                abs(cambio) > tolerancia["relativa"] and abs(diferencia) > tolerancia["absoluta"]
            )
            estado = "DENTRO" if not fuera else ("REGRESION" if diferencia > 0 else "MEJORA")
            fila.update({"base": anterior["valor"], "cambio": round(cambio, 4), "estado": estado})
        comparadas[nombre] = fila
    for nombre in sorted(set(metricas_base) - set(resumen)):
        comparadas[nombre] = {**metricas_base[nombre], "estado": "FALTA", "base": None}
    return comparadas


def entorno() -> dict:
    """Donde se midio: el commit, la corrida y la maquina."""
    procesador = platform.processor() or platform.machine()
    memoria = None
    cpuinfo, meminfo = Path("/proc/cpuinfo"), Path("/proc/meminfo")
    if cpuinfo.exists():
        modelos = [
            linea.split(":", 1)[1].strip()
            for linea in cpuinfo.read_text(encoding="utf-8").splitlines()
            if linea.startswith("model name")
        ]
        procesador = modelos[0] if modelos else procesador
    if meminfo.exists():
        total = next(
            (linea for linea in meminfo.read_text().splitlines() if linea.startswith("MemTotal")),
            "",
        )
        memoria = round(int(total.split()[1]) / 1024) if total else None
    return {
        "commit": os.environ.get("GITHUB_SHA"),
        "run": os.environ.get("GITHUB_RUN_ID"),
        "runner": os.environ.get("ImageOS"),
        "imagen": os.environ.get("ImageVersion"),
        "procesador": procesador,
        "cpus": os.cpu_count(),
        "memoria_mib": memoria,
        "python": platform.python_version(),
    }


def markdown(resultado: dict) -> str:
    e, base = resultado["entrada"], resultado["linea_base"]
    maquinas = sorted(
        {f"{x.get('procesador')} ({x.get('cpus')} CPU)" for x in resultado["entornos"] if x}
    )
    commit = next((x.get("commit") for x in resultado["entornos"] if x.get("commit")), None)
    lineas = [
        f"## Rendimiento: perfil {e['perfil']} ({e['cuentas']:,} cuentas, {e['cortes']} cortes)",
        "",
        f"{resultado['repeticiones']} repeticiones; mediana, rango y coeficiente de variacion. "
        f"{'; '.join(maquinas) or 'Maquina sin registrar'}; commit `{(commit or 'local')[:12]}`. "
        + (
            f"Linea base: commit `{(base.get('commit') or '?')[:12]}`, {base.get('fecha')}."
            if base
            else "Sin linea base: solo se mide."
        ),
        "",
        "| Metrica | Mediana | Rango | CV | Base | Cambio | Estado |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for nombre, m in resultado["metricas"].items():
        unidad = m.get("unidad", "")
        cambio = f"{m['cambio']:+.1%}" if m.get("cambio") is not None else "—"
        base_valor = f"{m['base']:,.3f}" if m.get("base") is not None else "—"
        rango = f"{m['minimo']:,.3f}–{m['maximo']:,.3f}" if "minimo" in m else "—"
        lineas.append(
            f"| `{nombre}` | {m.get('valor', 0):,.3f} {unidad} | {rango} | "
            f"{m.get('cv', 0):.1%} | {base_valor} | {cambio} | {m['estado']} |"
        )
    regresiones = resultado["regresiones"]
    lineas += [
        "",
        f"**{len(regresiones)} regresiones**: {', '.join(regresiones)}."
        if regresiones
        else "**Ninguna regresion** fuera de las tolerancias de la linea base.",
    ]
    return "\n".join(lineas) + "\n"


def main() -> int:
    argumentos = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    modo = argumentos.add_mutually_exclusive_group(required=True)
    modo.add_argument("--resultados", type=Path, nargs="+", help="Un directorio por repeticion")
    modo.add_argument(
        "--entorno", type=Path, help="Solo escribe entorno.json (la maquina) en ese directorio"
    )
    argumentos.add_argument("--linea-base", type=Path)
    argumentos.add_argument("--salida", type=Path)
    argumentos.add_argument("--markdown", type=Path)
    argumentos.add_argument("--escribir-linea-base", type=Path)
    argumentos.add_argument("--origen", help="De donde salen las mediciones (las corridas)")
    leidos = argumentos.parse_args()
    if leidos.entorno:
        leidos.entorno.mkdir(parents=True, exist_ok=True)
        (leidos.entorno / "entorno.json").write_bytes(
            (json.dumps(entorno(), indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        )
        return 0
    try:
        reportes = [leer_repeticion(d) for d in leidos.resultados]
    except (OSError, json.JSONDecodeError) as error:
        print(f"MAL {error}", file=sys.stderr)
        return 1
    resumen = resumir([metricas(r) for r in reportes])
    entornos = [r["entorno"] for r in reportes]
    ahora = datetime.now(UTC).strftime("%Y-%m-%d")
    if leidos.escribir_linea_base:
        commits = sorted({x["commit"] for x in entornos if x.get("commit")})
        linea_base = {
            "formato": FORMATO,
            "commit": commits[0] if len(commits) == 1 else None,
            "origen": leidos.origen,
            "fecha": ahora,
            "entrada": entrada(reportes[0]),
            "repeticiones": len(reportes),
            "entornos": entornos,
            "tolerancias": TOLERANCIAS,
            "metricas": resumen,
        }
        leidos.escribir_linea_base.write_bytes(
            (json.dumps(linea_base, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        )
        print(f"{leidos.escribir_linea_base}: {len(resumen)} metricas de {len(reportes)} corridas.")
        return 0
    base = (
        json.loads(leidos.linea_base.read_text(encoding="utf-8"))
        if leidos.linea_base and leidos.linea_base.exists()
        else None
    )
    comparadas = comparar(resumen, base)
    resultado = {
        "formato": FORMATO,
        "fecha": ahora,
        "entornos": entornos,
        "entrada": entrada(reportes[0]),
        "repeticiones": len(reportes),
        "linea_base": None
        if base is None
        else {"commit": base.get("commit"), "fecha": base.get("fecha")},
        "metricas": comparadas,
        "regresiones": [n for n, m in comparadas.items() if m["estado"] in ("REGRESION", "FALTA")],
    }
    texto = markdown(resultado)
    if leidos.salida:
        leidos.salida.write_bytes(
            (json.dumps(resultado, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        )
    if leidos.markdown:
        leidos.markdown.write_bytes(texto.encode("utf-8"))
    print(texto)
    return 1 if resultado["regresiones"] else 0


if __name__ == "__main__":
    sys.exit(main())
