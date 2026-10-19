"""El simulacro de restauracion: un respaldo se restaura en un entorno nuevo, y ninguno alterado
toca nada antes de negarse (docs/backups.md, docs/runbooks/backup-restore.md).

    python scripts/simulacro_de_restauracion.py respaldos/<respaldo> [--informe archivo.json]

Corre en la maquina del compose de produccion (usa el CLI de Docker), con PostgreSQL recien
levantado y vacio (`docker compose -f docker-compose.production.yml up -d --wait postgres` sobre un
volumen nuevo) y el respaldo dentro del directorio de respaldos (MC_RESPALDOS_DIR, ./respaldos por
omision). En orden:

1. Verifica el respaldo (el volcado, la lista y cada objeto contra el manifiesto).
2. Arma copias alteradas de cinco formas (un byte del volcado, un objeto alterado, un objeto que
   falta, otra revision del esquema y un PostgreSQL mas nuevo) y comprueba que cada una se niega con
   su codigo, con la base todavia sin tablas y el almacen sin un objeto de mas. Las borra despues.
3. Restaura el respaldo de verdad, con la revision que declara, y exige el informe VALIDADA: los
   conteos de cada tabla, las restricciones validadas y cada artefacto en el almacen.

Escribe una linea por comprobacion y, con --informe, el informe completo en JSON. Termina con 1 si
algo no salio como debia.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
COMPOSE = RAIZ / "docker-compose.production.yml"
PROYECTO = "motor-cartera-produccion"
CONTEO = {"ok": 0, "mal": 0}


def esperar(condicion: bool, descripcion: str) -> bool:
    CONTEO["ok" if condicion else "mal"] += 1
    print(f"  {'ok ' if condicion else 'MAL'} {descripcion}", flush=True)
    return condicion


def compose(*argumentos: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), "-p", PROYECTO, *argumentos],
        capture_output=True,
        text=True,
        env={**os.environ, "MSYS_NO_PATHCONV": "1"},
        check=False,
    )


def herramienta(*argumentos: str) -> dict:
    """La salida JSON de scripts/respaldo.py, corrido en el servicio restauracion."""
    salida = compose(
        "--profile", "restauracion", "run", "--rm", "--no-deps", "restauracion", *argumentos
    )
    texto = salida.stdout.strip()
    try:
        return json.loads(texto[texto.index("{") :])
    except ValueError:
        return {"resultado": "SIN_SALIDA", "stdout": texto[-500:], "stderr": salida.stderr[-500:]}


def tablas_en_la_base() -> int:
    salida = compose(
        "exec",
        "-T",
        "postgres",
        "sh",
        "-c",
        'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT count(*) FROM pg_tables '
        "WHERE schemaname = 'public' AND tablename <> 'spatial_ref_sys'\"",
    )
    return int(salida.stdout.strip() or -1)


def objetos_en_el_almacen() -> int:
    salida = compose(
        "--profile",
        "restauracion",
        "run",
        "--rm",
        "--no-deps",
        "--entrypoint",
        "sh",
        "restauracion",
        "-c",
        "find /var/lib/motor-cartera/fuentes -type f -path '*/sha256/*' | wc -l",
    )
    return int(salida.stdout.strip().splitlines()[-1] or -1)


def _escribible(ruta: Path) -> None:
    """Escribible por su dueno, sin quitarle la lectura a los demas: la restauracion corre como
    otro usuario (10001) y tiene que poder leer la copia alterada para negarla."""
    os.chmod(ruta, stat.S_IMODE(ruta.stat().st_mode) | stat.S_IWUSR | stat.S_IRUSR)


def _voltear_un_byte(ruta: Path) -> None:
    _escribible(ruta)
    datos = bytearray(ruta.read_bytes())
    datos[len(datos) // 2] ^= 0x01
    ruta.write_bytes(bytes(datos))


def _cambiar_manifiesto(directorio: Path, **cambios) -> None:
    ruta = directorio / "manifiesto.json"
    _escribible(ruta)
    manifiesto = json.loads(ruta.read_text(encoding="utf-8"))
    manifiesto.update(cambios)
    ruta.write_bytes((json.dumps(manifiesto, indent=2) + "\n").encode())


def _objetos(directorio: Path) -> list[Path]:
    return sorted(p for p in (directorio / "almacen").rglob("*") if p.is_file())


def _quitar_un_objeto(directorio: Path) -> None:
    ultimo = _objetos(directorio)[-1]
    _escribible(ultimo)
    ultimo.unlink()


def casos(respaldo: Path) -> list[tuple[str, str, Callable[[Path], None]]]:
    manifiesto = json.loads((respaldo / "manifiesto.json").read_text(encoding="utf-8"))
    lista = [
        (
            "un byte del volcado",
            "RESPALDO_ALTERADO",
            lambda d: _voltear_un_byte(d / "motor-cartera.dump"),
        ),
        (
            "otra revision del esquema",
            "REVISION_INCOMPATIBLE",
            lambda d: _cambiar_manifiesto(d, revision_esquema="0000"),
        ),
        (
            "un PostgreSQL mas nuevo",
            "POSTGRES_ANTERIOR",
            lambda d: _cambiar_manifiesto(d, postgres_version="99.0", postgres_version_num=990000),
        ),
    ]
    if manifiesto["almacen"].get("objetos"):
        lista += [
            ("un objeto alterado", "RESPALDO_ALTERADO", lambda d: _voltear_un_byte(_objetos(d)[0])),
            ("un objeto que falta", "RESPALDO_ALTERADO", _quitar_un_objeto),
        ]
    return lista


def main(argv: list[str] | None = None) -> int:
    argumentos = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    argumentos.add_argument("respaldo", type=Path)
    argumentos.add_argument("--informe", type=Path)
    leidos = argumentos.parse_args(argv)
    respaldo = leidos.respaldo.resolve()
    raiz = respaldo.parent
    dentro = f"/respaldos/{respaldo.name}"
    informe: dict = {"respaldo": respaldo.name, "negados": []}

    print(" El respaldo")
    verificado = herramienta("verificar", dentro)
    esperar(verificado.get("resultado") == "INTEGRO", f"se verifica: {verificado.get('resultado')}")
    base_antes, almacen_antes = tablas_en_la_base(), objetos_en_el_almacen()
    esperar(base_antes == 0, f"el destino es una base nueva, sin tablas ({base_antes})")
    revision = json.loads((respaldo / "manifiesto.json").read_text(encoding="utf-8"))[
        "revision_esquema"
    ]

    print(" Respaldos alterados: se niegan sin tocar nada")
    for nombre, codigo, alterar in casos(respaldo):
        copia = raiz / f"simulacro-{codigo.lower()}-{len(informe['negados'])}-{respaldo.name}"
        shutil.rmtree(copia, ignore_errors=True)
        shutil.copytree(respaldo, copia)
        try:
            alterar(copia)
            salida = herramienta(
                "restaurar", f"/respaldos/{copia.name}", "--revision-esperada", revision
            )
        finally:
            for ruta in copia.rglob("*"):
                if ruta.is_file():
                    _escribible(ruta)
            shutil.rmtree(copia, ignore_errors=True)
        intacta = tablas_en_la_base() == base_antes
        sin_objetos = objetos_en_el_almacen() == almacen_antes
        obtenido = salida.get("codigo") or salida.get("resultado")
        if not esperar(
            salida.get("codigo") == codigo and intacta and sin_objetos,
            f"{nombre}: {obtenido}; la base y el almacen intactos",
        ):
            # Lo que dijo la herramienta, para entender la falla (no lleva secretos).
            print(f"      {salida.get('stderr') or salida.get('mensaje') or salida}"[-400:])
        informe["negados"].append(
            {
                "caso": nombre,
                "codigo": salida.get("codigo"),
                "base_intacta": intacta,
                "almacen_intacto": sin_objetos,
            }
        )

    print(" La restauracion de verdad")
    inicio = time.monotonic()
    restaurado = herramienta("restaurar", dentro, "--revision-esperada", revision)
    pared = round(time.monotonic() - inicio, 1)
    esperar(
        restaurado.get("resultado") == "VALIDADA",
        f"{restaurado.get('resultado')}: {restaurado.get('tablas')} tablas, "
        f"{restaurado.get('filas')} filas, {restaurado.get('almacen', {}).get('copiados')} objetos "
        f"en {pared} s",
    )
    esperar(
        restaurado.get("conteos_iguales") is True
        and restaurado.get("restricciones_sin_validar") == 0
        and restaurado.get("almacen", {}).get("artefactos_faltantes") == 0,
        "las filas de cada tabla, las restricciones y cada artefacto, como en el respaldo",
    )
    informe["restauracion"] = restaurado
    informe["segundos_de_pared"] = pared
    informe["conteo"] = CONTEO
    if leidos.informe is not None:
        leidos.informe.parent.mkdir(parents=True, exist_ok=True)
        leidos.informe.write_bytes(
            (json.dumps(informe, indent=2, ensure_ascii=False) + "\n").encode()
        )
    print()
    if CONTEO["mal"]:
        print(f"{CONTEO['mal']} de {CONTEO['ok'] + CONTEO['mal']} comprobaciones fallaron.")
        return 1
    print(f"Todo en orden: {CONTEO['ok']} comprobaciones.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
