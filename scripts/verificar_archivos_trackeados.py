"""Control de los archivos trackeados: lo que el .gitignore no alcanza a ver.

El .gitignore evita que un archivo entre por descuido, pero solo mira los que todavia no
estan trackeados. Este control revisa el indice, que es lo que se sube, y falla si alguno de
sus archivos:

  - Lo excluye el propio .gitignore: entro con `git add -f`, o ya estaba antes de la regla.
    La politica es la del .gitignore; aqui no se repite.
  - Lo excluiria sin distinguir mayusculas. `CARTERA.XLSX` es tan cartera como
    `cartera.xlsx` (el lector del motor no las distingue), pero en Linux, WSL y el CI git si
    las distingue, y ahi `*.xlsx` no lo detiene.
  - Es por dentro una hoja de calculo, un archivo comprimido, un formato de datos, un
    volcado de una base, cartografia o una red vial, con cualquier nombre: un xlsx renombrado,
    un xlsm o un ods (que son un zip), un xls (un documento OLE2), un Parquet, un Arrow, un
    gzip, un 7z, un rar, un bzip2, un xz, un zstd, un tar (cada archivo que prepara OSRM lo
    es), un volcado de pg_dump, una base SQLite (un GeoPackage lo es), un Shapefile, un
    FlatGeobuf o un extracto de OpenStreetMap (.osm.pbf). Un csv, un GeoJSON, un OSM XML o un
    volcado en SQL plano no tienen firma, asi que a esos solo los detiene su nombre.

Y, si el indice trae la app web (web/), que su instalacion sea reproducible (web/package-lock.json
y web/.node-version trackeados), que lo que carga el navegador (web/index.html, web/public y
web/src, sin los archivos generados) no pida nada fuera del mismo origen, y que cada imagen base de
web/Dockerfile este fijada por su digest.

Solo usa git y la biblioteca estandar, para correr igual en el CI que en una laptop, donde
antes de un commit revisa tambien lo que ya esta en stage:

    python scripts/verificar_archivos_trackeados.py

Termina con codigo 1 si algun archivo no debe estar en el repositorio, y con 2 si no pudo
revisar (sin git, fuera de un repositorio): lo que no se pudo revisar no se da por bueno.
"""

from __future__ import annotations

import re
import subprocess
import sys
from urllib.parse import urlsplit

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
    # El codigo de archivo 9994, en big-endian, con que empiezan el .shp y el .shx.
    b"\x00\x00\x27\x0a": "un Shapefile (.shp o .shx)",
    b"fgb\x03fgb": "un FlatGeobuf",
}
# bzip2: BZh, el nivel (1 a 9) y la marca de su primer bloque, o la del final si esta vacio.
for _nivel in "123456789":
    FIRMAS[f"BZh{_nivel}".encode() + b"1AY&SY"] = "un bzip2"
    FIRMAS[f"BZh{_nivel}".encode() + b"\x17rE8P\x90"] = "un bzip2"

# Las que no estan al principio: (desde que byte, firma).
FIRMAS_ADENTRO = {
    # Un .osm.pbf empieza con el largo de su primer encabezado (4 bytes) y ese encabezado, con el
    # tipo de su bloque: el campo 1, de 9 bytes, OSMHeader.
    (4, b"\x0a\x09OSMHeader"): "un extracto de OpenStreetMap (.osm.pbf)",
    # Un tar POSIX dice ustar en el byte 257: asi es cada archivo que prepara OSRM (.osrm.*).
    (257, b"ustar"): "un tar (como los archivos que prepara OSRM)",
}

LARGO_FIRMA = max(
    *(len(firma) for firma in FIRMAS),
    *(desde + len(firma) for desde, firma in FIRMAS_ADENTRO),
)
REGULARES = (b"100644", b"100755")  # los enlaces y los submodulos no tienen contenido propio


WEB_OBLIGATORIOS = {
    b"web/package-lock.json": "el lockfile de npm: sin el, `npm ci` no reproduce la instalacion",
    b"web/.node-version": "la version de Node fijada que usan el CI y el Dockerfile",
}
WEB_DEL_NAVEGADOR = (b"web/index.html", b"web/public/", b"web/src/")
"""Lo que el navegador carga: nada de aqui puede pedir algo fuera del mismo origen."""
WEB_GENERADOS = {
    b"web/src/api/esquema.ts",
    b"web/src/api/openapi.json",
    b"web/src/test/capturas.ts",
}
"""El contrato y las capturas de las pruebas: los escribe un script, y no los carga el navegador."""
WEB_TEXTOS = (b".html", b".ts", b".tsx", b".css", b".js", b".mjs", b".svg", b".json")
URL = re.compile(rb"https?://[^\s\"'`)<>]+", re.IGNORECASE)
LOCALES = {"localhost", "127.0.0.1"}
"""Esta maquina, con su puerto o sin el: el servidor de desarrollo y la URL base de jsdom."""
ESPACIO_DE_NOMBRES_XML = re.compile(rb"""\bxmlns(?::[\w.-]+)?\s*=\s*(["'])[^"']*\1""")
"""Un `xmlns` (el `http://www.w3.org/2000/svg` de un SVG) nombra un vocabulario: no se pide."""
WEB_DOCKERFILE = b"web/Dockerfile"


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


def contenidos(raiz: str, objetos: list[bytes]) -> list[bytes]:
    """El contenido entero de cada objeto, leido del indice: solo los textos chicos de la web."""
    resultado = []
    with subprocess.Popen(
        ["git", "cat-file", "--batch"], cwd=raiz, stdin=subprocess.PIPE, stdout=subprocess.PIPE
    ) as proceso:
        for objeto in objetos:
            proceso.stdin.write(objeto + b"\n")
            proceso.stdin.flush()
            encabezado = proceso.stdout.readline().split()
            if len(encabezado) != 3:
                raise NoSePudoRevisar(f"git no pudo leer el objeto {objeto.decode()}")
            contenido = proceso.stdout.read(int(encabezado[2]))
            proceso.stdout.read(1)  # el salto de linea que lo cierra
            resultado.append(contenido)
        proceso.stdin.close()
    return resultado


def problemas_de_la_web(raiz: str, regulares: list[tuple[bytes, bytes]]) -> dict[bytes, list[str]]:
    """Lo que la app web necesita y lo que no puede tener, si el indice la trae."""
    rutas = {ruta: objeto for objeto, ruta in regulares}
    if not any(ruta.startswith(b"web/") for ruta in rutas):
        return {}
    problemas = {
        ruta: [f"falta en el indice: {que_es}"]
        for ruta, que_es in WEB_OBLIGATORIOS.items()
        if ruta not in rutas
    }
    revisadas = sorted(
        ruta
        for ruta in rutas
        if ruta.startswith(WEB_DEL_NAVEGADOR)
        and ruta.endswith(WEB_TEXTOS)
        and ruta not in WEB_GENERADOS
    )
    if WEB_DOCKERFILE in rutas:
        revisadas.append(WEB_DOCKERFILE)
    for ruta, texto in zip(revisadas, contenidos(raiz, [rutas[r] for r in revisadas]), strict=True):
        if ruta == WEB_DOCKERFILE:
            for linea in texto.splitlines():
                if linea.strip().upper().startswith(b"FROM ") and b"@sha256:" not in linea:
                    problemas.setdefault(ruta, []).append(
                        f"una imagen base sin su digest: {linea.strip().decode(errors='replace')}"
                    )
            continue
        for externa in externas(texto):
            problemas.setdefault(ruta, []).append(f"pide algo fuera del mismo origen: {externa}")
    return problemas


def externas(texto: bytes) -> list[str]:
    """Las URL absolutas de un texto cuyo anfitrion no es esta maquina, sin contar un `xmlns`.
    Cuenta el anfitrion, no el principio: `localhost.example.com` y `localhost@example.com` son de
    fuera."""
    encontradas = []
    for url in URL.findall(ESPACIO_DE_NOMBRES_XML.sub(b"", texto)):
        url = url.decode(errors="replace")
        try:
            anfitrion = urlsplit(url).hostname
        except ValueError:
            anfitrion = None
        if anfitrion not in LOCALES:
            encontradas.append(url)
    return encontradas


def main() -> int:
    try:
        raiz = git("rev-parse", "--show-toplevel").decode().strip()
        excluidos = excluidos_por_el_gitignore(raiz, distinguir_mayusculas=True)
        sin_mayusculas = excluidos_por_el_gitignore(raiz, distinguir_mayusculas=False)
        entradas = indice(raiz)
        regulares = [(objeto, ruta) for modo, objeto, ruta in entradas if modo in REGULARES]
        cabezas = primeros_bytes(raiz, [objeto for objeto, _ in regulares])
        de_la_web = problemas_de_la_web(raiz, regulares)
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
        for (desde, firma), que_es in FIRMAS_ADENTRO.items():
            if cabeza[desde : desde + len(firma)] == firma:
                problemas.setdefault(ruta, []).append(f"por dentro es {que_es}")

    total = len({ruta for _, _, ruta in entradas})
    if not problemas and not de_la_web:
        print(
            f"{total} archivos trackeados: ninguno lo excluye el .gitignore ni es por dentro "
            "una hoja de calculo, un comprimido, un formato de datos, un volcado, cartografia o "
            "una red vial."
        )
        return 0

    if problemas:
        print(f"De {total} archivos trackeados, estos no deben estar en el repositorio:")
        for ruta in sorted(problemas):
            print(f"  {ruta.decode(errors='replace')}: {'; '.join(problemas[ruta])}")
        print(
            "Sacalos del indice con `git rm --cached <archivo>`. Si ya se subieron, tambien siguen "
            "en la historia."
        )
    if de_la_web:
        print("La app web no cumple:")
        for ruta in sorted(de_la_web):
            print(f"  {ruta.decode(errors='replace')}: {'; '.join(de_la_web[ruta])}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
