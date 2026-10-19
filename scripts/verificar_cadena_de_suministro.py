"""Lo que la cadena de suministro del repositorio no puede perder, revisado sin red
(docs/cadena-de-suministro.md):

- cada imagen base (FROM) de cada Dockerfile y cada imagen de cada compose, fijada por su digest
  (las que el compose construye llevan `build:` y no se descargan);
- cada imagen que un script o un workflow corre con `docker run`, fijada por su digest;
- cada accion de GitHub fijada por el SHA de su commit (40 hexadecimales), con su version en un
  comentario;
- cada workflow con permisos explicitos y minimos (nunca write-all), y cada checkout sin dejar la
  credencial en el disco (persist-credentials: false).

    python scripts/verificar_cadena_de_suministro.py

Termina con 1 si algo no se cumple.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}\b")
SHA_DE_ACCION = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}\s+#\s*v\d")


def dockerfiles(raiz: Path) -> list[Path]:
    return sorted(
        p
        for p in raiz.rglob("Dockerfile*")
        if not any(parte in p.parts for parte in ("node_modules", ".venv", ".terraform"))
    )


def problemas_de_dockerfile(ruta: Path) -> list[str]:
    problemas = []
    etapas: set[str] = set()
    for numero, linea in enumerate(ruta.read_text(encoding="utf-8").splitlines(), 1):
        coincidencia = re.match(
            r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?", linea, re.I
        )
        if not coincidencia:
            continue
        imagen, alias = coincidencia.group(1), coincidencia.group(2)
        if imagen not in etapas and not DIGEST.search(imagen):
            problemas.append(f"{ruta.name}:{numero}: FROM {imagen} sin digest")
        if alias:
            etapas.add(alias)
    return problemas


def problemas_de_compose(ruta: Path) -> list[str]:
    """Cada servicio que se descarga (sin `build:`, propio o de su ancla `<<: *ancla`) usa una
    imagen con digest. Las anclas (`x-app: &app`) declaran sus claves con dos espacios; los
    servicios, debajo de `services:`, con cuatro."""
    problemas = []
    seccion = None
    actual = None
    construye: dict[str, bool] = {}
    hereda: dict[str, str] = {}
    imagenes: dict[str, tuple[int, str]] = {}
    for numero, linea in enumerate(ruta.read_text(encoding="utf-8").splitlines(), 1):
        superior = re.match(r"^([\w-]+):(?:\s*&([\w-]+))?", linea)
        if superior:
            seccion = superior.group(1)
            actual = superior.group(2) if seccion.startswith("x-") else None
            continue
        if seccion == "services":
            servicio = re.match(r"^  ([\w-]+):\s*$", linea)
            if servicio:
                actual = servicio.group(1)
                continue
            sangria = r"\s{4}"
        elif seccion is not None and seccion.startswith("x-"):
            sangria = r"\s{2}"
        else:
            continue
        if actual is None:
            continue
        if re.match(rf"^{sangria}build:", linea):
            construye[actual] = True
        ancla = re.match(rf"^{sangria}<<:\s*\*([\w-]+)", linea)
        if ancla:
            hereda[actual] = ancla.group(1)
        imagen = re.match(rf"^{sangria}image:\s*(\S+)", linea)
        if imagen:
            imagenes[actual] = (numero, imagen.group(1))
    for servicio, (numero, imagen) in imagenes.items():
        construido = construye.get(servicio) or construye.get(hereda.get(servicio, ""))
        if not construido and not DIGEST.search(imagen):
            problemas.append(f"{ruta.name}:{numero}: {servicio} usa {imagen} sin digest")
    return problemas


def problemas_de_workflow(ruta: Path) -> list[str]:
    problemas = []
    texto = ruta.read_text(encoding="utf-8")
    lineas = texto.splitlines()
    if not re.search(r"^permissions:", texto, re.M):
        problemas.append(f"{ruta.name}: sin permissions de nivel superior")
    if "write-all" in texto:
        problemas.append(f"{ruta.name}: permissions write-all")
    for numero, linea in enumerate(lineas, 1):
        uso = re.match(r"^\s*(?:-\s+)?uses:\s*(.+?)\s*$", linea)
        if uso and not uso.group(1).startswith("./"):
            if not SHA_DE_ACCION.match(uso.group(1)):
                problemas.append(f"{ruta.name}:{numero}: {uso.group(1)} no esta fijada por SHA")
            if "actions/checkout@" in uso.group(1):
                siguientes = "\n".join(lineas[numero : numero + 8])
                if "persist-credentials: false" not in siguientes:
                    problemas.append(
                        f"{ruta.name}:{numero}: checkout sin persist-credentials: false"
                    )
        for imagen in re.findall(r"docker run[^\n]*?\s([\w.-]+(?:/[\w.-]+)+:[\w.-]+\S*)", linea):
            if not DIGEST.search(imagen) and "$" not in imagen:
                problemas.append(f"{ruta.name}:{numero}: docker run {imagen} sin digest")
    return problemas


def problemas_de_scripts(raiz: Path) -> list[str]:
    """Las imagenes que los scripts de Python corren: en una constante, con su digest."""
    problemas = []
    for ruta in sorted((raiz / "scripts").glob("*.py")):
        texto = ruta.read_text(encoding="utf-8")
        if '"docker", "run"' not in texto and "'docker', 'run'" not in texto:
            continue
        for imagen in re.findall(
            r'"((?:[\w.-]+/)+[\w.-]+:[\w.-]+(?:@sha256:[0-9a-f]{64})?)"', texto
        ):
            if not DIGEST.search(imagen):
                problemas.append(f"{ruta.name}: {imagen} sin digest")
    return problemas


def revisar(raiz: Path) -> list[str]:
    problemas: list[str] = []
    for ruta in dockerfiles(raiz):
        problemas += problemas_de_dockerfile(ruta)
    for ruta in sorted(raiz.glob("docker-compose*.yml")):
        problemas += problemas_de_compose(ruta)
    for ruta in sorted((raiz / ".github" / "workflows").glob("*.yml")):
        problemas += problemas_de_workflow(ruta)
    problemas += problemas_de_scripts(raiz)
    return problemas


def main(argv: list[str] | None = None) -> int:
    argumentos = argv if argv is not None else sys.argv[1:]
    raiz = Path(argumentos[0]) if argumentos else RAIZ
    problemas = revisar(raiz)
    for problema in problemas:
        print(f"  MAL {problema}")
    if problemas:
        print(f"{len(problemas)} problemas de la cadena de suministro.")
        return 1
    print(
        "La cadena de suministro esta fijada: imagenes por digest, acciones por SHA, permisos "
        "explicitos y checkouts sin credencial persistida."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
