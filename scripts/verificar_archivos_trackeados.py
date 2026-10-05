"""Control de los archivos trackeados: lo que el .gitignore no alcanza a ver.

El .gitignore evita que un archivo entre por descuido, pero solo mira los que todavia no
estan trackeados. Este control revisa el indice, que es lo que se sube, y falla si alguno de
sus archivos:

  - Lo excluye el propio .gitignore: entro con `git add -f`, o ya estaba antes de la regla.
    La politica es la del .gitignore; aqui no se repite.
  - Lo excluiria sin distinguir mayusculas. `CARTERA.XLSX` es tan cartera como
    `cartera.xlsx` (el lector del motor no las distingue), pero en Linux, WSL y el CI git si
    las distingue, y ahi `*.xlsx` no lo detiene.
  - Es por dentro una hoja de calculo, un archivo comprimido, un formato de datos o un
    volcado de una base, con cualquier nombre: un xlsx renombrado, un xlsm o un ods (que son un
    zip), un xls (un documento OLE2), un Parquet, un Arrow, un gzip, un 7z, un rar, un bzip2,
    un xz, un zstd, un volcado de pg_dump o una base SQLite. Un csv o un volcado en SQL plano no
    tienen firma, asi que a esos solo los detiene su nombre.

Solo usa git y la biblioteca estandar, para correr igual en el CI que en una laptop, donde
antes de un commit revisa tambien lo que ya esta en stage:

    python scripts/verificar_archivos_trackeados.py

Termina con codigo 1 si algun archivo no debe estar en el repositorio, y con 2 si no pudo
revisar (sin git, fuera de un repositorio): lo que no se pudo revisar no se da por bueno.
"""

from __future__ import annotations

import subprocess
import sys

# Lo que el .gitignore prohibe por nombre, reconocido por sus primeros bytes.
FIRMAS = {
    b"PK\x03\x04": "un zip (xlsx, xlsm, ods o zip)",
    b"PK\x05\x06": "un zip vacio",
    b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1": "un documento OLE2 (xls)",
    b"PAR1": "un Parquet",
    b"ARROW1": "un Arrow o Feather",
    b"\x1f\x8b": "un gzip",
    b"7z\xbc\xaf\x27\x1c": "un 7z",
    b"Rar!\x1a\x07": "un rar",
    b"\xfd7zXZ\x00": "un xz",
    b"\x28\xb5\x2f\xfd": "un zstd",
    b"PGDMP": "un volcado de pg_dump",
    b"SQLite format 3\x00": "una base SQLite",
}
# bzip2: BZh, el nivel (1 a 9) y la marca de su primer bloque, o la del final si esta vacio.
for _nivel in "123456789":
    FIRMAS[f"BZh{_nivel}".encode() + b"1AY&SY"] = "un bzip2"
    FIRMAS[f"BZh{_nivel}".encode() + b"\x17rE8P\x90"] = "un bzip2"

LARGO_FIRMA = max(len(firma) for firma in FIRMAS)
REGULARES = (b"100644", b"100755")  # los enlaces y los submodulos no tienen contenido propio


class NoSePudoRevisar(Exception):
    pass


def git(*argumentos: str, raiz: str | None = None) -> bytes:
    try:
        resultado = subprocess.run(["git", *argumentos], cwd=raiz, capture_output=True)
    except FileNotFoundError:
        raise NoSePudoRevisar("no se encontro git") from None
    if resultado.returncode != 0:
        raise NoSePudoRevisar(resultado.stderr.decode(errors="replace").strip())
    return resultado.stdout


def excluidos_por_el_gitignore(raiz: str, *, distinguir_mayusculas: bool) -> set[bytes]:
    """Los archivos trackeados que el .gitignore excluye.

    Solo cuentan los .gitignore del repositorio, no .git/info/exclude ni el global de cada
    quien: la politica es la del proyecto y tiene que dar lo mismo en cualquier maquina.
    """
    salida = git(
        "-c",
        f"core.ignorecase={'false' if distinguir_mayusculas else 'true'}",
        "ls-files",
        "-z",
        "--cached",
        "--ignored",
        "--exclude-per-directory=.gitignore",
        raiz=raiz,
    )
    return {ruta for ruta in salida.split(b"\0") if ruta}


def indice(raiz: str) -> list[tuple[bytes, bytes, bytes]]:
    """Modo, objeto y ruta de cada entrada del indice."""
    entradas = []
    for registro in git("ls-files", "-z", "--stage", raiz=raiz).split(b"\0"):
        if registro:
            datos, ruta = registro.split(b"\t", 1)
            modo, objeto, _etapa = datos.split(b" ")
            entradas.append((modo, objeto, ruta))
    return entradas


def primeros_bytes(raiz: str, objetos: list[bytes]) -> list[bytes]:
    """El principio de cada objeto, leido del indice y no de la copia de trabajo.

    El resto se lee y se descarta sin guardarlo: justo lo que se busca puede ser enorme.
    """
    cabezas = []
    with subprocess.Popen(
        ["git", "cat-file", "--batch"], cwd=raiz, stdin=subprocess.PIPE, stdout=subprocess.PIPE
    ) as proceso:
        for objeto in objetos:
            proceso.stdin.write(objeto + b"\n")
            proceso.stdin.flush()
            encabezado = proceso.stdout.readline().split()  # <objeto> blob <tamano>
            if len(encabezado) != 3:
                raise NoSePudoRevisar(f"git no pudo leer el objeto {objeto.decode()}")
            tamano = int(encabezado[2])
            cabeza = proceso.stdout.read(min(tamano, LARGO_FIRMA))
            pendiente = tamano - len(cabeza) + 1  # el resto y el salto de linea que lo cierra
            while pendiente > 0:
                bloque = proceso.stdout.read(min(pendiente, 1 << 20))
                if not bloque:
                    raise NoSePudoRevisar(f"git corto la lectura del objeto {objeto.decode()}")
                pendiente -= len(bloque)
            cabezas.append(cabeza)
        proceso.stdin.close()
    return cabezas


def main() -> int:
    try:
        raiz = git("rev-parse", "--show-toplevel").decode().strip()
        excluidos = excluidos_por_el_gitignore(raiz, distinguir_mayusculas=True)
        sin_mayusculas = excluidos_por_el_gitignore(raiz, distinguir_mayusculas=False)
        entradas = indice(raiz)
        regulares = [(objeto, ruta) for modo, objeto, ruta in entradas if modo in REGULARES]
        cabezas = primeros_bytes(raiz, [objeto for objeto, _ in regulares])
    except (NoSePudoRevisar, OSError) as error:
        print(f"No se pudieron revisar los archivos trackeados: {error}", file=sys.stderr)
        return 2

    problemas: dict[bytes, list[str]] = {ruta: ["lo excluye el .gitignore"] for ruta in excluidos}
    for ruta in sin_mayusculas - excluidos:
        problemas[ruta] = ["lo excluye el .gitignore si no se distinguen mayusculas"]
    for (_, ruta), cabeza in zip(regulares, cabezas, strict=True):
        for firma, que_es in FIRMAS.items():
            if cabeza.startswith(firma):
                problemas.setdefault(ruta, []).append(f"por dentro es {que_es}")

    total = len({ruta for _, _, ruta in entradas})
    if not problemas:
        print(
            f"{total} archivos trackeados: ninguno lo excluye el .gitignore ni es por dentro "
            "una hoja de calculo, un comprimido, un formato de datos o un volcado."
        )
        return 0

    print(f"De {total} archivos trackeados, estos no deben estar en el repositorio:")
    for ruta in sorted(problemas):
        print(f"  {ruta.decode(errors='replace')}: {'; '.join(problemas[ruta])}")
    print(
        "Sacalos del indice con `git rm --cached <archivo>`. Si ya se subieron, tambien siguen "
        "en la historia."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
