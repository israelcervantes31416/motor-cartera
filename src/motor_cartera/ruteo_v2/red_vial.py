"""Las versiones de la red vial: que red sirve el proveedor de ruteo, de que datos salio y como se
preparo.

La red vial publica (OpenStreetMap) es una REFERENCIA VIAL, no una fuente de negocio: no es
CARTERA ni PAGOS, no entra al almacen de artefactos y no la lee ningun motor de negocio. Una version
la identifica su **huella**: el SHA-256 de su identidad, que es lo que determina la red que se
rutea:

- el SHA-256 del archivo fuente (un .osm.pbf o un .osm), nunca su nombre;
- el motor (OSRM), su version (v26.9.0) y su distribucion exacta (la imagen de Docker por su digest,
  o los binarios nativos por su SHA-256);
- el algoritmo (MLD), el perfil (car) y el hash de sus archivos (el car.lua de OSRM v26.9.0,
  copiado tal cual en `perfiles/` y verificado contra el hash fijado aqui);
- los comandos exactos de la preparacion (extract, partition y customize), con sus parametros.

La misma version logica tiene la misma huella. La preparacion la graba en la red con
`osrm-extract --data_version`, y el backend la declara en cada respuesta: asi el worker sabe que
red le contesta. Los archivos que produce OSRM no son identicos byte por byte entre dos
preparaciones (`.osrm.partition` y `.osrm.fileIndex` cambian en cada corrida): su SHA-256 se guarda
en el manifiesto como evidencia de esa preparacion, no como identidad.

Las redes grandes no entran al repositorio: viven fuera, en un directorio por huella.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy import update
from sqlmodel import Session, select

from motor_cartera.db.modelos import EstadoVersionRedVial, VersionRedVial, ahora
from motor_cartera.ruteo_v2.politica import canonico
from motor_cartera.ruteo_v2.proveedor import data_version

VERSION_IDENTIDAD = "red-vial-identidad/v1"
VERSION_MANIFIESTO = "red-vial-manifiesto/v1"

MOTOR_OSRM = "OSRM"
MOTOR_SINTETICO = "SINTETICO"
VERSION_OSRM = "v26.9.0"
IMAGEN_OSRM = (
    "ghcr.io/project-osrm/osrm-backend:v26.9.0-debian"
    "@sha256:8a1b1bc938412f15f9b5b32d794c4ec6bf4a85dfbbabfa0a014b70b187edb53b"
)
"""La imagen oficial de OSRM v26.9.0 (Debian), fijada por el digest de su indice multiplataforma. El
compose arranca el backend con esta misma referencia."""
ALGORITMO = "MLD"
PERFIL = "car"
DIRECTORIO_PERFIL = Path(__file__).parent / "perfiles" / "osrm-v26.9.0"
HASH_PERFIL_CAR = "87186be6c3f681c038f13c97847802267b7e6c2f04ef95edf8be13642c938f3e"
"""El hash del car.lua de OSRM v26.9.0 y su lib/, tal cual: si alguien cambia un archivo del
perfil, deja de coincidir y la red no se prepara ni se registra."""

BINARIOS_OSRM = ("osrm-extract", "osrm-partition", "osrm-customize", "osrm-routed")
NOMBRE_RED = "red"
ARTEFACTOS_MLD = (
    "datasource_names",
    "ebg_nodes",
    "edges",
    "fileIndex",
    "geometry",
    "icd",
    "maneuver_overrides",
    "names",
    "nbg_nodes",
    "properties",
    "ramIndex",
    "timestamp",
    "tld",
    "tls",
    "turn_duration_penalties",
    "turn_weight_penalties",
    "cells",
    "cell_metrics",
    "mldgr",
    "partition",
)
"""Lo que `osrm-routed --algorithm mld` necesita (`--list-inputs` de v26.9.0): sin cualquiera de
ellos, la red no esta preparada."""
PARAMETROS = {"small_component_size": 1}
"""`--small-component-size 1`: ningun componente es "pequeno", asi que el snap de un punto es
siempre su segmento mas cercano, el mismo en nearest que en table y sin importar que otros puntos
vayan en la consulta. Con el valor por omision (1000), OSRM mueve un punto cercano a un fragmento
aislado al componente grande mas cercano, y lo hace distinto segun los demas puntos de la
consulta."""


class RedVialInvalida(Exception):
    """Una red que no se puede preparar ni registrar: su fuente, su perfil, su identidad o sus
    artefactos no son lo que dicen. No registra nada."""


# --- el perfil y los archivos ---------------------------------------------------------------------


def sha256_archivo(ruta: Path) -> str:
    h = hashlib.sha256()
    with Path(ruta).open("rb") as archivo:
        for bloque in iter(lambda: archivo.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def hash_perfil(directorio: Path = DIRECTORIO_PERFIL) -> str:
    """SHA-256 canonico de un perfil: cada .lua (car.lua y lib/), con su ruta relativa y su SHA-256,
    en orden. La licencia no cuenta."""
    archivos = sorted(p for p in Path(directorio).rglob("*.lua"))
    if not archivos:
        raise RedVialInvalida(f"No hay un perfil en {directorio}.")
    lista = [[p.relative_to(directorio).as_posix(), sha256_archivo(p)] for p in archivos]
    return hashlib.sha256(json.dumps(lista, separators=(",", ":")).encode()).hexdigest()


def distribucion_nativa(binarios: Path) -> str:
    """La distribucion nativa de OSRM, por el SHA-256 de sus binarios: dos maquinas con los mismos
    binarios preparan la misma red."""
    partes = []
    for nombre in BINARIOS_OSRM:
        candidatos = [binarios / nombre, binarios / f"{nombre}.exe"]
        ruta = next((c for c in candidatos if c.is_file()), None)
        if ruta is None:
            raise RedVialInvalida(f"No esta {nombre} en {binarios}.")
        partes.append(f"{nombre}={sha256_archivo(ruta)}")
    return "nativa:" + hashlib.sha256(";".join(partes).encode()).hexdigest()


def distribucion_docker() -> str:
    return f"docker:{IMAGEN_OSRM}"


# --- la identidad, la huella y los comandos -------------------------------------------------------


@dataclass(frozen=True)
class Descripcion:
    """Lo que se declara de una red: de donde salen sus datos y que cubren. No entra en la huella:
    la misma fuente con otra etiqueta es la misma red."""

    proveedor_datos: str
    licencia: str
    region: str
    fecha_snapshot: date | None = None
    proveedor_extracto: str | None = None
    url_fuente: str | None = None

    def __post_init__(self) -> None:
        for nombre in ("proveedor_datos", "licencia", "region"):
            if not getattr(self, nombre).strip():
                raise RedVialInvalida(f"La red declara su {nombre}.")


def comandos(archivo_fuente: str, huella: str) -> list[list[str]]:
    """Los comandos de la preparacion MLD, con rutas relativas al directorio de la red: los mismos
    con los binarios nativos que dentro de la imagen de Docker."""
    return [
        [
            "osrm-extract",
            "-p",
            "perfil/car.lua",
            "--small-component-size",
            str(PARAMETROS["small_component_size"]),
            "--data_version",
            data_version(huella),
            "-o",
            NOMBRE_RED,
            f"fuente/{archivo_fuente}",
        ],
        ["osrm-partition", f"{NOMBRE_RED}.osrm"],
        ["osrm-customize", f"{NOMBRE_RED}.osrm"],
    ]


def identidad(sha256_fuente: str, distribucion: str) -> dict:
    """Lo que determina la red que se rutea, y nada mas: la huella es su SHA-256."""
    return {
        "version": VERSION_IDENTIDAD,
        "sha256_fuente": sha256_fuente,
        "motor": MOTOR_OSRM,
        "version_motor": VERSION_OSRM,
        "distribucion": distribucion,
        "algoritmo": ALGORITMO,
        "perfil": PERFIL,
        "hash_perfil": HASH_PERFIL_CAR,
        "parametros": dict(PARAMETROS),
        "comandos": comandos("{archivo_fuente}", "{huella}"),
    }


def huella(identidad_red: dict) -> str:
    return hashlib.sha256(canonico(identidad_red)).hexdigest()


# --- preparar -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Preparacion:
    huella: str
    directorio: Path
    identidad: dict
    descripcion: Descripcion
    archivo_fuente: str


def preparar_directorio(
    fuente: Path, descripcion: Descripcion, distribucion: str, raiz: Path
) -> Preparacion:
    """El directorio de la red, por su huella: la fuente y el perfil copiados, y lo que se declara
    de ella. Si ya existia, se reutiliza, con lo que se declaro la primera vez: la version de esa
    huella conserva su primera descripcion. No ejecuta OSRM."""
    fuente = Path(fuente)
    if not fuente.is_file() or fuente.stat().st_size == 0:
        raise RedVialInvalida(f"La fuente {fuente.name!r} no existe o esta vacia.")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,120}\.osm(\.pbf)?", fuente.name):
        raise RedVialInvalida(
            "La fuente es un .osm o un .osm.pbf con un nombre de letras, digitos, puntos o guiones."
        )
    if hash_perfil() != HASH_PERFIL_CAR:
        raise RedVialInvalida("El perfil car.lua no es el de OSRM v26.9.0: alguien lo cambio.")
    sha = sha256_archivo(fuente)
    identidad_red = identidad(sha, distribucion)
    h = huella(identidad_red)
    directorio = Path(raiz) / h
    (directorio / "fuente").mkdir(parents=True, exist_ok=True)
    destino = directorio / "fuente" / fuente.name
    if not destino.exists() or sha256_archivo(destino) != sha:
        shutil.copyfile(fuente, destino)
    if (directorio / "perfil").exists():
        shutil.rmtree(directorio / "perfil")
    shutil.copytree(DIRECTORIO_PERFIL, directorio / "perfil")
    declarada = directorio / "identidad.json"
    if declarada.exists():
        previa = json.loads(declarada.read_bytes())
        d = previa["descripcion"]
        descripcion = Descripcion(
            proveedor_datos=d["proveedor_datos"],
            licencia=d["licencia"],
            region=d["region"],
            fecha_snapshot=None
            if d["fecha_snapshot"] is None
            else date.fromisoformat(d["fecha_snapshot"]),
            proveedor_extracto=d["proveedor_extracto"],
            url_fuente=d["url_fuente"],
        )
    else:
        declaracion = {
            "identidad": identidad_red,
            "huella": h,
            "archivo_fuente": fuente.name,
            "descripcion": _descripcion(descripcion),
        }
        declarada.write_bytes(canonico(declaracion) + b"\n")
    if distribucion.startswith("docker:"):
        _guion_docker(directorio, fuente.name, h)
    return Preparacion(h, directorio, identidad_red, descripcion, fuente.name)


def _descripcion(d: Descripcion) -> dict:
    return {
        "proveedor_datos": d.proveedor_datos,
        "proveedor_extracto": d.proveedor_extracto,
        "licencia": d.licencia,
        "region": d.region,
        "fecha_snapshot": None if d.fecha_snapshot is None else d.fecha_snapshot.isoformat(),
        "url_fuente": d.url_fuente,
    }


def _guion_docker(directorio: Path, archivo_fuente: str, h: str) -> Path:
    """`preparar.sh`: los mismos comandos, con la imagen fijada por su digest. Se corre en el
    anfitrion de Docker, desde cualquier lugar; el directorio de la red se monta en /datos. La
    bitacora de cada comando la escribe el contenedor, no el anfitrion: el directorio puede ser de
    otro usuario (del de la API, si ella lo creo). Al final, el contenedor deja los archivos
    preparados legibles para todos: OSRM crea el .fileIndex solo para su dueno, root en el
    contenedor, y quien registra la red o la sirve puede ser otro usuario."""
    lineas = [
        "#!/bin/sh",
        f"# Prepara la red vial {h} con OSRM {VERSION_OSRM} (MLD), con la imagen fijada.",
        "# Generado por motor-cartera preparar-red-vial; no lo edites: la huella lo describe.",
        "set -eu",
        'cd "$(dirname "$0")"',
        f"IMAGEN='{IMAGEN_OSRM}'",
    ]
    for comando in comandos(archivo_fuente, h):
        # Cada argumento ya es seguro sin comillas: el nombre de la fuente se valido, la huella es
        # hexadecimal y lo demas es fijo. Se revisa de todos modos: el guion no interpreta nada.
        if not all(re.fullmatch(r"[A-Za-z0-9._:/-]+", a) for a in comando):
            raise RedVialInvalida("Un argumento de la preparacion no es seguro en el guion.")
        bitacora = comando[0].removeprefix("osrm-")
        lineas.append(
            f'docker run --rm -v "$PWD:/datos" -w /datos "$IMAGEN" '
            f"sh -c '{' '.join(comando)} > {bitacora}.log 2>&1'"
        )
    lineas += [
        "# Legibles para quien registre o sirva la red: OSRM crea el .fileIndex solo para root.",
        f'docker run --rm -v "$PWD:/datos" -w /datos "$IMAGEN" '
        f"sh -c 'chmod a+r {NOMBRE_RED}.osrm*'",
    ]
    ruta = directorio / "preparar.sh"
    ruta.write_bytes(("\n".join(lineas) + "\n").encode())
    ruta.chmod(0o755)
    return ruta


def ejecutar_nativa(preparacion: Preparacion, binarios: Path) -> None:
    """Corre los comandos con los binarios nativos, en el directorio de la red, con la bitacora de
    cada uno en su archivo. Levanta RedVialInvalida si alguno falla."""
    for comando in comandos(preparacion.archivo_fuente, preparacion.huella):
        ejecutable = Path(binarios) / comando[0]
        if not ejecutable.exists() and Path(f"{ejecutable}.exe").exists():
            ejecutable = Path(f"{ejecutable}.exe")
        bitacora = preparacion.directorio / f"{comando[0].removeprefix('osrm-')}.log"
        with bitacora.open("wb") as salida:
            resultado = subprocess.run(
                [str(ejecutable), *comando[1:]],
                cwd=preparacion.directorio,
                stdout=salida,
                stderr=subprocess.STDOUT,
                check=False,
            )
        if resultado.returncode != 0:
            raise RedVialInvalida(
                f"{comando[0]} termino con codigo {resultado.returncode}; ver {bitacora.name}."
            )


# --- el manifiesto y el registro ------------------------------------------------------------------


def _cobertura(directorio: Path) -> dict:
    """Lo que la bitacora de osrm-extract dice de la red: cuantos nodos, vias, relaciones y
    restricciones leyo, y cuantas aristas escribio. Sin coordenadas ni recuadros."""
    bitacora = directorio / "extract.log"
    if not bitacora.exists():
        return {}
    texto = bitacora.read_text(encoding="utf-8", errors="replace")
    cobertura: dict = {}
    entrada = re.search(
        r"Raw input contains (\d+) nodes, (\d+) ways, and (\d+) relations, (\d+) restrictions",
        texto,
    )
    if entrada:
        cobertura.update(
            zip(
                ("nodos_osm", "vias_osm", "relaciones_osm", "restricciones_osm"),
                map(int, entrada.groups()),
                strict=True,
            )
        )
    aristas = re.search(r"Processed (\d+) edges\b", texto)
    if aristas:
        cobertura["aristas"] = int(aristas.group(1))
    return cobertura


def manifiesto(directorio: Path) -> dict:
    """El manifiesto de una red preparada en `directorio`, verificado: la fuente tiene el SHA-256 de
    su identidad, el perfil copiado es el fijado, la huella es la de la identidad y estan todos los
    archivos que necesita osrm-routed. Con el SHA-256 de cada uno y la cobertura."""
    directorio = Path(directorio)
    declaracion_ruta = directorio / "identidad.json"
    if not declaracion_ruta.exists():
        raise RedVialInvalida(f"{directorio} no es el directorio de una red: falta identidad.json.")
    declaracion = json.loads(declaracion_ruta.read_bytes())
    identidad_red = declaracion["identidad"]
    h = declaracion["huella"]
    if huella(identidad_red) != h or directorio.name != h:
        raise RedVialInvalida(
            "La huella no es la de la identidad declarada, o el directorio no se llama asi."
        )
    if (
        identidad_red.get("version") != VERSION_IDENTIDAD
        or identidad_red.get("motor") != MOTOR_OSRM
    ):
        raise RedVialInvalida("La identidad no es de una red OSRM que este servicio conozca.")
    if (
        identidad_red.get("hash_perfil") != HASH_PERFIL_CAR
        or hash_perfil(directorio / "perfil") != HASH_PERFIL_CAR
    ):
        raise RedVialInvalida("El perfil de la red no es el car.lua fijado de OSRM v26.9.0.")
    fuente = directorio / "fuente" / declaracion["archivo_fuente"]
    if not fuente.is_file() or sha256_archivo(fuente) != identidad_red["sha256_fuente"]:
        raise RedVialInvalida("La fuente de la red no tiene el SHA-256 de su identidad.")
    faltan = [a for a in ARTEFACTOS_MLD if not (directorio / f"{NOMBRE_RED}.osrm.{a}").is_file()]
    if faltan:
        raise RedVialInvalida(f"La red no esta preparada: faltan {', '.join(faltan)}.")
    artefactos = [
        {"archivo": p.name, "bytes": p.stat().st_size, "sha256": sha256_archivo(p)}
        for p in sorted(directorio.glob(f"{NOMBRE_RED}.osrm*"))
        if p.is_file()
    ]
    return {
        "version": VERSION_MANIFIESTO,
        "huella": h,
        "data_version": data_version(h),
        "identidad": identidad_red,
        "archivo_fuente": declaracion["archivo_fuente"],
        "fuente_bytes": fuente.stat().st_size,
        "descripcion": declaracion["descripcion"],
        "comandos": comandos(declaracion["archivo_fuente"], h),
        "artefactos": artefactos,
        "cobertura": _cobertura(directorio),
    }


def hash_manifiesto(datos: dict) -> str:
    return hashlib.sha256(canonico(datos)).hexdigest()


@dataclass(frozen=True)
class Registro:
    version: VersionRedVial
    reutilizada: bool
    misma_preparacion: bool | None = None
    """Al registrar un directorio de una huella ya publicada: si sus archivos son los de la
    preparacion publicada (el mismo hash del manifiesto). Otra preparacion de la misma red da los
    mismos snaps y las mismas duraciones, pero una distancia puede diferir en 0.1 m (y un metro al
    redondear): para reconstruir exacto, se sirve la publicada."""


def abrir_preparacion(s: Session, preparacion: Preparacion) -> Registro:
    """La version EN_PROCESO de una red que se empieza a preparar, en la transaccion de quien
    llama; o la PUBLICADA de esa huella, si ya existe (reutilizada)."""
    publicada = _de_huella(s, preparacion.huella, EstadoVersionRedVial.PUBLICADA)
    if publicada is not None:
        return Registro(publicada, True)
    en_proceso = _de_huella(s, preparacion.huella, EstadoVersionRedVial.EN_PROCESO)
    if en_proceso is not None:
        return Registro(en_proceso, False)
    d = preparacion.descripcion
    i = preparacion.identidad
    version = VersionRedVial(
        huella=preparacion.huella,
        proveedor_datos=d.proveedor_datos,
        proveedor_extracto=d.proveedor_extracto,
        licencia=d.licencia,
        fecha_snapshot=d.fecha_snapshot,
        region=d.region,
        archivo_fuente=preparacion.archivo_fuente,
        url_fuente=d.url_fuente,
        sha256_fuente=i["sha256_fuente"],
        motor_ruteo=i["motor"],
        version_motor_ruteo=i["version_motor"],
        distribucion_motor=i["distribucion"],
        algoritmo_motor=i["algoritmo"],
        perfil=i["perfil"],
        hash_perfil=i["hash_perfil"],
        identidad=i,
        metadatos={},
    )
    s.add(version)
    s.flush()
    return Registro(version, False)


def registrar(s: Session, directorio: Path) -> Registro:
    """Publica la red preparada en `directorio`, despues de verificar su manifiesto, en la
    transaccion de quien llama. Si esa huella ya esta publicada, la devuelve (reutilizada) sin
    cambiarla: su manifiesto conserva la preparacion con que se publico."""
    datos = manifiesto(directorio)
    declaracion = json.loads((Path(directorio) / "identidad.json").read_bytes())
    d = declaracion["descripcion"]
    preparacion = Preparacion(
        datos["huella"],
        Path(directorio),
        datos["identidad"],
        Descripcion(
            proveedor_datos=d["proveedor_datos"],
            licencia=d["licencia"],
            region=d["region"],
            fecha_snapshot=None
            if d["fecha_snapshot"] is None
            else date.fromisoformat(d["fecha_snapshot"]),
            proveedor_extracto=d["proveedor_extracto"],
            url_fuente=d["url_fuente"],
        ),
        datos["archivo_fuente"],
    )
    registro = abrir_preparacion(s, preparacion)
    if registro.reutilizada:
        misma = registro.version.hash_manifest == hash_manifiesto(datos)
        return Registro(registro.version, True, misma)
    (Path(directorio) / "manifiesto.json").write_bytes(canonico(datos) + b"\n")
    version = registro.version
    s.execute(
        update(VersionRedVial)
        .where(
            VersionRedVial.id == version.id,
            VersionRedVial.estado == EstadoVersionRedVial.EN_PROCESO,
        )
        .values(
            estado=EstadoVersionRedVial.PUBLICADA,
            manifiesto=datos,
            hash_manifest=hash_manifiesto(datos),
            metadatos={
                "cobertura": datos["cobertura"],
                "artefactos": len(datos["artefactos"]),
                "artefactos_bytes": sum(a["bytes"] for a in datos["artefactos"]),
                "fuente_bytes": datos["fuente_bytes"],
            },
            publicada_en=ahora(),
        )
        .execution_options(synchronize_session=False)
    )
    s.flush()
    s.refresh(version)
    return Registro(version, False)


def fallar(s: Session, version: VersionRedVial, motivo: str) -> None:
    """La preparacion de una version EN_PROCESO fallo: queda FALLIDA, con el motivo."""
    s.execute(
        update(VersionRedVial)
        .where(
            VersionRedVial.id == version.id,
            VersionRedVial.estado == EstadoVersionRedVial.EN_PROCESO,
        )
        .values(estado=EstadoVersionRedVial.FALLIDA, detalle=motivo[:500])
        .execution_options(synchronize_session=False)
    )


def _de_huella(s: Session, h: str, estado: EstadoVersionRedVial) -> VersionRedVial | None:
    return s.exec(
        select(VersionRedVial).where(VersionRedVial.huella == h, VersionRedVial.estado == estado)
    ).one_or_none()


# --- una red sintetica sin OSRM -------------------------------------------------------------------


def registrar_sintetica(s: Session, nombre: str, modelo: dict) -> Registro:
    """Una version SINTETICA: un modelo de duraciones y distancias que no es una red vial (el de
    un proveedor de pruebas o del benchmark del planificador), publicada con su huella. Ningun
    backend OSRM la declara, asi que con ella el worker nunca rutea por calle: solo un proveedor
    sintetico que se presenta con la misma huella."""
    identidad_red = {
        "version": VERSION_IDENTIDAD,
        "motor": MOTOR_SINTETICO,
        "modelo": modelo,
    }
    h = huella(identidad_red)
    publicada = _de_huella(s, h, EstadoVersionRedVial.PUBLICADA)
    if publicada is not None:
        return Registro(publicada, True)
    datos = {
        "version": VERSION_MANIFIESTO,
        "huella": h,
        "identidad": identidad_red,
        "nombre": nombre,
    }
    sha = hashlib.sha256(canonico(modelo)).hexdigest()
    version = VersionRedVial(
        huella=h,
        proveedor_datos=MOTOR_SINTETICO,
        proveedor_extracto=None,
        licencia="sintetica",
        fecha_snapshot=None,
        region=nombre,
        archivo_fuente="modelo-sintetico",
        url_fuente=None,
        sha256_fuente=sha,
        motor_ruteo=MOTOR_SINTETICO,
        version_motor_ruteo=str(modelo.get("version", "sintetico/v1")),
        distribucion_motor="sintetica",
        algoritmo_motor=MOTOR_SINTETICO,
        perfil="ninguno",
        hash_perfil=sha,
        identidad=identidad_red,
        estado=EstadoVersionRedVial.PUBLICADA,
        manifiesto=datos,
        hash_manifest=hash_manifiesto(datos),
        metadatos={"modelo": modelo},
        publicada_en=ahora(),
    )
    s.add(version)
    s.flush()
    return Registro(version, False)
