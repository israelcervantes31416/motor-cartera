"""Las precondiciones y los assets de una release, revisados antes de publicar nada.

El workflow de release (.github/workflows/release.yml) lo corre sobre el commit que va a etiquetar
(docs/cadena-de-suministro.md). `precondiciones` revisa todo y lo reporta todo; si falta cualquier
condicion termina con 1, y nada se publica:

1. Lo pide el dueno del repositorio, desde main.
2. La version es X.Y.Z, mayor que la ultima etiqueta v*, y cada superficie del proyecto la declara:
   el paquete y su lock, la app web y su contrato generado, el CHANGELOG (con su fecha y el enlace
   a su release) y el README.
3. El commit es exactamente la cabeza de main: nada entro despues.
4. Cada check que exige la proteccion de main, leido de sus reglas y no de una lista fija, termino
   en success sobre ese commit, y los de la matriz de compatibilidad de Python tambien. El check
   agregado de CodeQL (el de Code scanning) solo existe en los pull requests: en un commit de main,
   su equivalente es el analisis de cada lenguaje sobre ese commit y ninguna alerta abierta en main.
   Un check de una integracion desconocida niega la release.
5. El CI de main de ese commit, de donde salen los SBOM, termino en success.
6. Ni la etiqueta ni la release existen todavia.

Solo lee: la API de GitHub, con el token del job, y los archivos del checkout.

`assets` arma lo que se publica, junto al OpenAPI que ya genero scripts/exportar_openapi.py: los
SBOM de las tres imagenes propias, del CI de main de ese commit, con el nombre de la version (cada
uno revisado: CycloneDX, de su imagen y con componentes), el SHA256SUMS de los bytes que se publican
y las notas de la release, de su entrada del CHANGELOG (sus enlaces relativos, a los archivos de la
etiqueta).

    python scripts/verificar_release.py precondiciones --version 1.0.0 --sha <commit> \\
        [--salida $GITHUB_OUTPUT]
    python scripts/verificar_release.py assets --version 1.0.0 --sha <commit> --ci-run <id> \\
        --evidencia <el artefacto produccion-evidencia> --destino release
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
GITHUB_ACTIONS = 15368
"""La integracion de los checks que corren los workflows."""
CODE_SCANNING = 57789
"""La integracion del check agregado de CodeQL, que Code scanning publica en cada pull request."""
VERSION = re.compile(r"\d+\.\d+\.\d+")
SBOMS = {
    "api": ("motor-cartera:produccion", "la imagen de la API y del worker"),
    "web": ("motor-cartera-web:produccion", "la imagen de la app web y su proxy"),
    "respaldo": ("motor-cartera-respaldo:local", "la imagen del respaldo y la restauración"),
}
"""El SBOM de cada imagen propia que genera el job `produccion` del CI: su imagen y que es."""

Api = Callable[[str], tuple[int, object]]
"""Una peticion GET a la API de GitHub: su estado HTTP y su JSON."""


def clave(version: str) -> tuple[int, int, int]:
    mayor, menor, parche = (int(n) for n in version.split("."))
    return mayor, menor, parche


def versiones_declaradas(raiz: Path) -> dict[str, str]:
    """La version que declara cada superficie del proyecto."""
    pyproject = tomllib.loads((raiz / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((raiz / "uv.lock").read_text(encoding="utf-8"))
    inicio = (raiz / "src" / "motor_cartera" / "__init__.py").read_text(encoding="utf-8")
    paquete = json.loads((raiz / "web" / "package.json").read_text(encoding="utf-8"))
    bloqueo = json.loads((raiz / "web" / "package-lock.json").read_text(encoding="utf-8"))
    contrato = json.loads(
        (raiz / "web" / "src" / "api" / "openapi.json").read_text(encoding="utf-8")
    )
    en_el_codigo = re.search(r'^__version__ = "([^"]+)"', inicio, re.MULTILINE)
    en_el_lock = [p["version"] for p in lock["package"] if p["name"] == "motor-cartera"]
    return {
        "pyproject.toml": pyproject["project"]["version"],
        "motor_cartera.__version__": en_el_codigo.group(1) if en_el_codigo else "",
        "uv.lock": en_el_lock[0] if len(en_el_lock) == 1 else "",
        "web/package.json": paquete["version"],
        "web/package-lock.json": bloqueo["version"],
        'web/package-lock.json (packages[""])': bloqueo["packages"][""]["version"],
        "web/src/api/openapi.json": contrato["info"]["version"],
    }


def entrada_del_changelog(texto: str, version: str, repositorio: str) -> tuple[str, str] | None:
    """El titulo y la fecha de `## [vX.Y.Z] — titulo — AAAA-MM-DD`, si la entrada existe y el
    CHANGELOG enlaza su release."""
    entrada = re.search(
        rf"^## \[v{re.escape(version)}\] — (.+) — (\d{{4}}-\d{{2}}-\d{{2}})$", texto, re.MULTILINE
    )
    enlace = f"[v{version}]: https://github.com/{repositorio}/releases/tag/v{version}" in texto
    return (entrada.group(1), entrada.group(2)) if entrada and enlace else None


def notas_del_changelog(texto: str, version: str) -> str:
    """El cuerpo de la entrada de una version en el CHANGELOG, hasta la siguiente."""
    entrada = re.search(
        rf"^## \[v{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|^\[)",
        texto,
        re.MULTILINE | re.DOTALL,
    )
    return entrada.group(1).strip() if entrada else ""


def enlaces_de_la_etiqueta(texto: str, repositorio: str, version: str) -> str:
    """Cada enlace relativo de las notas, al archivo de la etiqueta. En el CHANGELOG, `docs/x.md` es
    un archivo del repositorio; en la pagina de la release, el navegador lo resolveria contra
    `/releases/tag/` y daria 404. Los enlaces absolutos y las anclas quedan como estan."""
    base = f"https://github.com/{repositorio}/blob/v{version}/"
    return re.sub(
        r"\]\((?![a-zA-Z][a-zA-Z0-9+.-]*:|#|//)([^)\s]+)\)",
        lambda enlace: f"]({base}{enlace.group(1).removeprefix('./').lstrip('/')})",
        texto,
    )


def lenguajes_de_codeql(raiz: Path) -> list[str]:
    texto = (raiz / ".github" / "workflows" / "codeql.yml").read_text(encoding="utf-8")
    matriz = re.search(r"^\s+lenguaje:\s*\[([^\]]*)\]", texto, re.MULTILINE)
    return [v.strip() for v in matriz.group(1).split(",")] if matriz else []


def checks_de_compatibilidad(raiz: Path) -> list[str]:
    """Los checks de la matriz de Python (uno por version de requires-python) y el del contrato."""
    pyproject = tomllib.loads((raiz / "pyproject.toml").read_text(encoding="utf-8"))
    requiere = pyproject["project"]["requires-python"].replace(" ", "")
    rango = re.fullmatch(r">=3\.(\d+),<3\.(\d+)", requiere)
    if not rango:
        return []
    desde, hasta = (int(n) for n in rango.groups())
    menores = [f"compatibilidad (3.{m})" for m in range(desde, hasta)]
    return [*menores, "compatibilidad (contrato)"]


def _ultimo_check(api: Api, repositorio: str, sha: str, nombre: str) -> dict | None:
    consulta = urllib.parse.urlencode({"check_name": nombre, "filter": "latest", "per_page": 100})
    estado, datos = api(f"/repos/{repositorio}/commits/{sha}/check-runs?{consulta}")
    if estado != 200 or not isinstance(datos, dict):
        return None
    corridas = [
        c
        for c in datos.get("check_runs", [])
        if c.get("name") == nombre and (c.get("app") or {}).get("id") == GITHUB_ACTIONS
    ]
    return max(corridas, key=lambda c: c.get("id", 0)) if corridas else None


def problemas_de_checks(
    api: Api, repositorio: str, sha: str, extras: list[str], lenguajes: list[str]
) -> tuple[list[str], list[str]]:
    """Los checks de la proteccion de main y los extras: (los que pasaron, los problemas)."""
    bien: list[str] = []
    mal: list[str] = []
    estado, reglas = api(f"/repos/{repositorio}/rules/branches/main")
    if estado != 200 or not isinstance(reglas, list):
        return bien, [f"no se pudieron leer las reglas de main (HTTP {estado})"]
    requeridos: list[tuple[str, int | None]] = []
    for regla in reglas:
        if regla.get("type") == "required_status_checks":
            for check in regla.get("parameters", {}).get("required_status_checks", []):
                requeridos.append((check["context"], check.get("integration_id")))
    if not requeridos:
        mal.append("las reglas de main no exigen ningun check: no hay con que comparar")
    for nombre in extras:
        if nombre not in {n for n, _ in requeridos}:
            requeridos.append((nombre, GITHUB_ACTIONS))
    for nombre, integracion in requeridos:
        if integracion == CODE_SCANNING:
            bien_cs, mal_cs = _code_scanning(api, repositorio, sha, lenguajes)
            bien += bien_cs
            mal += mal_cs
            continue
        if integracion not in (GITHUB_ACTIONS, None):
            mal.append(f"{nombre}: de la integracion {integracion}, que no se sabe verificar")
            continue
        corrida = _ultimo_check(api, repositorio, sha, nombre)
        if corrida is None:
            mal.append(f"{nombre}: no corrio sobre este commit")
        elif corrida.get("status") != "completed" or corrida.get("conclusion") != "success":
            mal.append(f"{nombre}: {corrida.get('status')} / {corrida.get('conclusion')}")
        else:
            bien.append(f"{nombre}: success")
    return bien, mal


def _code_scanning(
    api: Api, repositorio: str, sha: str, lenguajes: list[str]
) -> tuple[list[str], list[str]]:
    bien: list[str] = []
    mal: list[str] = []
    consulta = urllib.parse.urlencode({"ref": "refs/heads/main", "sha": sha, "per_page": 100})
    estado, analisis = api(f"/repos/{repositorio}/code-scanning/analyses?{consulta}")
    categorias = (
        {a.get("category") for a in analisis if not a.get("error")}
        if estado == 200 and isinstance(analisis, list)
        else set()
    )
    for lenguaje in lenguajes:
        if f"/language:{lenguaje}" in categorias:
            bien.append(f"CodeQL: el analisis de {lenguaje} sobre este commit")
        else:
            mal.append(f"CodeQL: no hay un analisis de {lenguaje} sobre este commit")
    consulta = urllib.parse.urlencode({"state": "open", "ref": "refs/heads/main", "per_page": 100})
    estado, alertas = api(f"/repos/{repositorio}/code-scanning/alerts?{consulta}")
    if estado != 200 or not isinstance(alertas, list):
        mal.append(f"CodeQL: no se pudieron leer las alertas de Code scanning (HTTP {estado})")
    elif alertas:
        numeros = ", ".join(f"#{a.get('number')}" for a in alertas)
        mal.append(f"CodeQL: {len(alertas)} alertas abiertas en main ({numeros})")
    else:
        bien.append("CodeQL: ninguna alerta abierta en main")
    return bien, mal


def corrida_del_ci(api: Api, repositorio: str, sha: str) -> tuple[int | None, str]:
    """La ultima corrida del CI de main sobre el commit: su id si termino en success."""
    consulta = urllib.parse.urlencode({"head_sha": sha, "event": "push", "per_page": 20})
    estado, datos = api(f"/repos/{repositorio}/actions/workflows/ci.yml/runs?{consulta}")
    corridas = datos.get("workflow_runs", []) if estado == 200 and isinstance(datos, dict) else []
    if not corridas:
        return None, "el CI de main no corrio sobre este commit"
    ultima = max(corridas, key=lambda c: (c.get("run_number", 0), c.get("run_attempt", 0)))
    if ultima.get("status") != "completed" or ultima.get("conclusion") != "success":
        return (
            None,
            f"el CI de main de este commit: {ultima.get('status')} / {ultima.get('conclusion')}",
        )
    return int(ultima["id"]), f"el CI de main de este commit: success (run {ultima['id']})"


def api_de_github(token: str, base: str = "https://api.github.com") -> Api:
    def pedir(ruta: str) -> tuple[int, object]:
        peticion = urllib.request.Request(
            base + ruta,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(peticion, timeout=30) as respuesta:
                return respuesta.status, json.loads(respuesta.read() or b"null")
        except urllib.error.HTTPError as error:
            return error.code, None

    return pedir


def revisar(
    api: Api,
    raiz: Path,
    repositorio: str,
    version: str,
    sha: str,
    ref: str,
    actor: str,
    dueno: str,
) -> tuple[list[str], list[str], dict[str, str]]:
    """Todas las precondiciones: (las que se cumplen, las que no, lo que el workflow necesita)."""
    bien: list[str] = []
    mal: list[str] = []
    salida: dict[str, str] = {"sha": sha, "version": version}

    def cumple(condicion: bool, si: str, no: str) -> None:
        (bien if condicion else mal).append(si if condicion else no)

    cumple(actor == dueno, f"la pide {actor}, el dueno", f"la pide {actor}, no el dueno ({dueno})")
    cumple(ref == "refs/heads/main", "corre desde main", f"corre desde {ref}, no desde main")
    if not VERSION.fullmatch(version):
        mal.append(f"la version {version!r} no es X.Y.Z")
        return bien, mal, salida
    bien.append(f"la version {version} es X.Y.Z")

    for superficie, declarada in versiones_declaradas(raiz).items():
        cumple(
            declarada == version,
            f"{superficie} declara {version}",
            f"{superficie} declara {declarada!r}, no {version}",
        )
    changelog = (raiz / "CHANGELOG.md").read_text(encoding="utf-8")
    entrada = entrada_del_changelog(changelog, version, repositorio)
    if entrada:
        salida["titulo"], salida["fecha"] = entrada
        bien.append(f"el CHANGELOG tiene v{version} ({entrada[1]}) y el enlace a su release")
    else:
        mal.append(f"el CHANGELOG no tiene la entrada de v{version} con su fecha y su enlace")
    readme = " ".join((raiz / "README.md").read_text(encoding="utf-8").split())
    cumple(
        f"**Versión estable: v{version}**" in readme,
        f"el README dice Versión estable: v{version}",
        f"el README no dice Versión estable: v{version}",
    )

    estado, etiquetas = api(f"/repos/{repositorio}/tags?per_page=100")
    anteriores = sorted(
        (
            clave(e["name"][1:])
            for e in (etiquetas if estado == 200 and isinstance(etiquetas, list) else [])
            if VERSION.fullmatch(e.get("name", "")[1:]) and e["name"].startswith("v")
        ),
        reverse=True,
    )
    if estado != 200:
        mal.append(f"no se pudieron leer las etiquetas (HTTP {estado})")
    elif anteriores and clave(version) <= anteriores[0]:
        mal.append(
            f"v{version} no es mayor que la ultima etiqueta, v{'.'.join(map(str, anteriores[0]))}"
        )
    else:
        ultima = "v" + ".".join(map(str, anteriores[0])) if anteriores else "ninguna"
        bien.append(f"v{version} es mayor que la ultima etiqueta ({ultima})")

    estado, cabeza = api(f"/repos/{repositorio}/git/ref/heads/main")
    en_main = (cabeza or {}).get("object", {}).get("sha") if isinstance(cabeza, dict) else None
    cumple(
        estado == 200 and en_main == sha,
        f"{sha[:12]} es la cabeza de main: sin divergencia",
        f"la cabeza de main es {en_main}, no {sha}",
    )

    lenguajes = lenguajes_de_codeql(raiz)
    extras = checks_de_compatibilidad(raiz)
    if not extras:
        mal.append("no se pudo leer la matriz de compatibilidad de requires-python")
    bien_checks, mal_checks = problemas_de_checks(api, repositorio, sha, extras, lenguajes)
    bien += bien_checks
    mal += mal_checks

    corrida, detalle = corrida_del_ci(api, repositorio, sha)
    (bien if corrida else mal).append(detalle)
    if corrida:
        salida["ci_run"] = str(corrida)

    estado, _ = api(f"/repos/{repositorio}/git/ref/tags/v{version}")
    cumple(estado == 404, f"la etiqueta v{version} no existe", f"la etiqueta v{version} ya existe")
    estado, _ = api(f"/repos/{repositorio}/releases/tags/v{version}")
    cumple(estado == 404, f"la release v{version} no existe", f"la release v{version} ya existe")
    return bien, mal, salida


def sha256(ruta: Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def preparar_assets(version: str, evidencia: Path, destino: Path) -> list[Path]:
    """Los assets en `destino/assets`: el OpenAPI (ya generado ahi), los SBOM del CI con el nombre
    de la version, los mismos bytes, y su SHA256SUMS. Lo que no es lo esperado se niega."""
    carpeta = destino / "assets"
    openapi = carpeta / f"openapi-v{version}.json"
    declarada = json.loads(openapi.read_text(encoding="utf-8")).get("info", {}).get("version")
    if declarada != version:
        raise ValueError(f"{openapi.name} es de la version {declarada!r}, no de {version}")
    archivos = [openapi]
    for nombre, (imagen, _) in SBOMS.items():
        origen = evidencia / f"sbom-{nombre}.cdx.json"
        sbom = json.loads(origen.read_text(encoding="utf-8"))
        componente = sbom.get("metadata", {}).get("component", {})
        if (
            sbom.get("bomFormat") != "CycloneDX"
            or componente.get("name") != imagen
            or not sbom.get("components")
        ):
            raise ValueError(f"{origen.name} no es el SBOM CycloneDX de {imagen}")
        copia = carpeta / f"sbom-{nombre}-v{version}.cdx.json"
        shutil.copyfile(origen, copia)
        archivos.append(copia)
    sumas = carpeta / "SHA256SUMS"
    sumas.write_bytes("".join(f"{sha256(a)} *{a.name}\n" for a in archivos).encode("utf-8"))
    return [*archivos, sumas]


def notas(changelog: str, repositorio: str, version: str, sha: str, ci_run: str, archivos) -> str:
    """Las notas de la release: su entrada del CHANGELOG, cada asset con sus bytes y su SHA-256,
    como verificarlos y el enlace a los cambios desde la version anterior."""
    base = f"https://github.com/{repositorio}"
    que = {f"openapi-v{version}.json": "El contrato OpenAPI completo de la API, de este commit"}
    for nombre, (imagen, descripcion) in SBOMS.items():
        que[f"sbom-{nombre}-v{version}.cdx.json"] = (
            f"El SBOM CycloneDX de {descripcion} (`{imagen}`)"
        )
    que["SHA256SUMS"] = "El SHA-256 de los otros cuatro"
    filas = [
        f"| `{a.name}` | {que.get(a.name, '')} | {a.stat().st_size:,} | `{sha256(a)}` |"
        for a in archivos
    ]
    versiones = re.findall(r"^## \[v(\d+\.\d+\.\d+)\]", changelog, re.MULTILINE)
    posicion = versiones.index(version) if version in versiones else -1
    anterior = versiones[posicion + 1] if 0 <= posicion < len(versiones) - 1 else None
    cambios = (
        f"\n\n**Cambios:** {base}/compare/v{anterior}...v{version}" if anterior is not None else ""
    )
    entrada = enlaces_de_la_etiqueta(notas_del_changelog(changelog, version), repositorio, version)
    return (
        f"{entrada}\n\n"
        "## Assets\n\n"
        "| Archivo | Qué es | Bytes | SHA-256 |\n| --- | --- | ---: | --- |\n"
        + "\n".join(filas)
        + "\n\n"
        f"Los SBOM los generó Trivy en el job `produccion` del [CI de `main`]"
        f"({base}/actions/runs/{ci_run}) sobre este commit; el OpenAPI,"
        " `scripts/exportar_openapi.py` en el workflow de release. Cada archivo tiene su atestación"
        " de procedencia (SLSA, firmada con Sigstore por el workflow de release):\n\n"
        "```bash\n"
        "sha256sum -c SHA256SUMS\n"
        f"gh attestation verify openapi-v{version}.json --repo {repositorio}\n"
        "```\n\n"
        f"Commit [`{sha[:12]}`]({base}/commit/{sha}) ·"
        f" [Versionado y compatibilidad]({base}/blob/v{version}/docs/versioning.md) ·"
        f" [Limitaciones]({base}/blob/v{version}/docs/limitaciones.md)"
        f"{cambios}\n"
    )


def _precondiciones(leidos: argparse.Namespace, repositorio: str) -> int:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if not token:
        print("Falta GITHUB_TOKEN.", file=sys.stderr)
        return 1
    bien, mal, salida = revisar(
        api_de_github(token, os.environ.get("GITHUB_API_URL", "https://api.github.com")),
        RAIZ,
        repositorio,
        leidos.version,
        leidos.sha,
        os.environ.get("GITHUB_REF", ""),
        os.environ.get("GITHUB_ACTOR", ""),
        os.environ.get("GITHUB_REPOSITORY_OWNER", ""),
    )
    for linea in bien:
        print(f"  ok  {linea}")
    for linea in mal:
        print(f"  MAL {linea}")
    if mal:
        print(f"{len(mal)} precondiciones sin cumplir: no se publica nada.")
        return 1
    if leidos.salida:
        with leidos.salida.open("a", encoding="utf-8") as archivo:
            for llave, valor in salida.items():
                archivo.write(f"{llave}={valor}\n")
    print(f"Las {len(bien)} precondiciones de v{leidos.version} se cumplen.")
    return 0


def _assets(leidos: argparse.Namespace, repositorio: str) -> int:
    try:
        archivos = preparar_assets(leidos.version, leidos.evidencia, leidos.destino)
    except (OSError, ValueError, KeyError) as error:
        print(f"  MAL {error}")
        return 1
    changelog = (RAIZ / "CHANGELOG.md").read_text(encoding="utf-8")
    texto = notas(changelog, repositorio, leidos.version, leidos.sha, leidos.ci_run, archivos)
    (leidos.destino / "notas.md").write_bytes(texto.encode("utf-8"))
    for archivo in archivos:
        print(f"  ok  {archivo.name}: {archivo.stat().st_size:,} bytes, {sha256(archivo)}")
    return 0


def main() -> int:
    argumentos = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    pasos = argumentos.add_subparsers(dest="paso", required=True)
    precondiciones = pasos.add_parser("precondiciones", help="Revisa todo antes de publicar.")
    assets = pasos.add_parser("assets", help="Arma los assets, su SHA256SUMS y las notas.")
    for paso in (precondiciones, assets):
        paso.add_argument("--version", required=True, help="X.Y.Z, sin la v")
        paso.add_argument("--sha", required=True, help="El commit que se etiqueta")
    precondiciones.add_argument("--salida", type=Path, help="Donde escribir sha, ci_run, titulo")
    assets.add_argument("--ci-run", required=True, help="La corrida del CI de main del commit")
    assets.add_argument("--evidencia", type=Path, required=True, help="produccion-evidencia")
    assets.add_argument("--destino", type=Path, required=True, help="Con assets/openapi-v*.json")
    leidos = argumentos.parse_args()
    repositorio = os.environ.get("GITHUB_REPOSITORY", "israelcervantes31416/motor-cartera")
    if leidos.paso == "precondiciones":
        return _precondiciones(leidos, repositorio)
    return _assets(leidos, repositorio)


if __name__ == "__main__":
    sys.exit(main())
