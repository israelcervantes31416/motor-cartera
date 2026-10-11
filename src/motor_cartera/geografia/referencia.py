"""Importar una referencia geografica publica: la capa de municipios de un Shapefile, validada,
normalizada a EPSG:4326 y publicada todo o nada.

La cartografia es REFERENCIA GEOGRAFICA PUBLICA, no una fuente de negocio: no pasa por la API ni por
el almacen de artefactos, y no se descarga en runtime. El archivo oficial se descarga una vez, de su
URL, a una ruta local, y se importa con `motor-cartera importar-geografia`. Nunca se modifica: se
lee. Lo que identifica a la version es el SHA-256 del archivo; con `--referencia`, ademas, el
manifiesto del repositorio (referencias.json) dice que producto, edicion y capa es, y el importador
se niega si el archivo no es exactamente ese.

Como se importa:

1. Se calcula el SHA-256 del archivo y, con un manifiesto, se compara con el suyo.
2. Se lee la capa (de un zip o de un .shp con sus companeros): su .prj tiene que declarar un sistema
   que se reconozca por sus parametros (EPSG:6372 o EPSG:4326, ver `crs`), su .cpg dice la
   codificacion del .dbf, y la capa tiene que ser de poligonos con CVEGEO, CVE_ENT, CVE_MUN y
   NOMGEO.
3. La version se registra EN_PROCESO, y cada poligono se copia con COPY, como WKB, a una tabla
   temporal. PostgreSQL comprueba las claves (CVEGEO es CVE_ENT y CVE_MUN), que no se repitan, que
   ningun registro venga sin geometria, transforma cada geometria a EPSG:4326 con PostGIS y
   comprueba su validez (ST_IsValid).
4. Una geometria invalida se repara solo si la reparacion es segura y explicita: ST_MakeValid, solo
   sus poligonos, valida y con el mismo area (a menos de 0.01 %). Cada reparacion queda registrada
   en la version: cual, por que y cuanto cambio su area. Si alguna no se puede reparar asi, la
   importacion se rechaza: no se repara en silencio.
5. Los municipios se insertan y la version queda PUBLICADA en la misma transaccion: todo o nada. Si
   algo falla, no queda ningun municipio y la version queda FALLIDA, con su motivo.

Mientras importa, el proceso tiene un candado consultivo de PostgreSQL por el archivo y la capa:
otra importacion del mismo archivo espera su turno con GeografiaEnImportacion, y una version
EN_PROCESO sin candado es de una importacion interrumpida, que la siguiente cierra FALLIDA antes de
empezar.
"""

from __future__ import annotations

import hashlib
import json
import logging
import struct
import tempfile
import unicodedata
import zipfile
from array import array
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any
from uuid import UUID

import shapefile
from sqlalchemy import create_engine, text, update
from sqlalchemy.pool import NullPool
from sqlmodel import Session, select

from motor_cartera.db.copia import copiar
from motor_cartera.db.modelos import (
    EstadoVersionGeografia,
    NivelGeografia,
    VersionGeografia,
    ahora,
)
from motor_cartera.db.sesion import crear_motor
from motor_cartera.geografia import crs as sistemas

log = logging.getLogger(__name__)

MANIFIESTO = Path(__file__).with_name("referencias.json")
VERSION_IMPORTADOR = "importador-geografia/v1"
"""Cambia si cambia como se lee, se valida, se transforma o se repara una capa."""

CAMPOS = ("CVEGEO", "CVE_ENT", "CVE_MUN", "NOMGEO")
"""Los campos de la capa de municipios del Marco Geoestadistico, por nombre."""
OBLIGATORIOS = ("shp", "shx", "dbf", "prj")
TOLERANCIA_DE_AREA = 1e-4
"""Una reparacion es segura si el area cambia menos de 0.01 %."""

CODIFICACIONES = {
    "88591": "latin-1",
    "ISO88591": "latin-1",
    "LATIN1": "latin-1",
    "UTF8": "utf-8",
    "65001": "utf-8",
    "1252": "cp1252",
    "CP1252": "cp1252",
    "WINDOWS1252": "cp1252",
}
"""Lo que puede declarar un .cpg, sin guiones ni espacios, y como se lee el .dbf con eso."""

STAGING = "geo_importacion"


# --- errores --------------------------------------------------------------------------------------


class ImportacionRechazada(ValueError):
    """La capa no se puede importar, y no se registro nada: el archivo no es la referencia que dice
    el manifiesto, falta un archivo de la capa, su sistema de coordenadas no se reconoce o su
    estructura no es la esperada."""


class GeografiaYaImportada(Exception):
    """Ese archivo y esa capa ya estan publicados como una version."""

    def __init__(self, previa: VersionGeografia) -> None:
        super().__init__(
            f"Ese archivo y esa capa ya son la version {previa.geografia_version_id} "
            f"({previa.producto}, {previa.edicion}), publicada: no se importa dos veces."
        )
        self.previa = previa


class GeografiaEnImportacion(Exception):
    """Otro proceso esta importando ese mismo archivo y esa misma capa."""


class ImportacionFallida(Exception):
    """La importacion se registro y fallo: la version quedo FALLIDA, sin ningun municipio."""

    def __init__(self, version_id: UUID, motivo: str) -> None:
        super().__init__(motivo)
        self.geografia_version_id = version_id


# --- el manifiesto --------------------------------------------------------------------------------


@dataclass(frozen=True)
class ReferenciaConocida:
    """Una referencia del manifiesto: lo que el archivo tiene que ser y lo que se registra de el."""

    clave: str
    datos: dict[str, Any]

    @property
    def sha256(self) -> str:
        return self.datos["sha256"]

    @property
    def capa(self) -> str:
        return self.datos["capa"]


@cache
def referencias() -> dict[str, ReferenciaConocida]:
    """Las referencias del manifiesto del paquete, por su clave."""
    documento = json.loads(MANIFIESTO.read_text(encoding="ascii"))
    return {
        clave: ReferenciaConocida(clave, datos)
        for clave, datos in sorted(documento["referencias"].items())
    }


@dataclass(frozen=True)
class Metadatos:
    """Lo que dice de donde sale una capa que no esta en el manifiesto, como una sintetica."""

    proveedor: str
    producto: str
    edicion: str
    nivel: NivelGeografia = NivelGeografia.MUNICIPAL
    adicionales: dict[str, Any] = field(default_factory=dict)
    """URL, licencia, fechas y lo que haga falta, tal cual."""


def sha256_de(ruta: Path) -> tuple[str, int]:
    """El SHA-256 de un archivo y su tamano, leyendo por bloques."""
    digesto = hashlib.sha256()
    tamano = 0
    with ruta.open("rb") as archivo:
        while bloque := archivo.read(1 << 20):
            digesto.update(bloque)
            tamano += len(bloque)
    return digesto.hexdigest(), tamano


# --- la capa --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Capa:
    """Una capa lista para leerse: sus archivos en un directorio temporal, su sistema de
    coordenadas reconocido y la codificacion de su .dbf."""

    nombre: str
    directorio: Path
    crs: sistemas.Crs
    codificacion: str
    cpg: str | None
    """Lo que declaraba su .cpg, tal cual; None si no traia uno."""

    def ruta(self, extension: str) -> Path:
        return self.directorio / _nombre_temporal(extension)

    def lector(self) -> shapefile.Reader:
        return shapefile.Reader(
            str(self.ruta("shp")), encoding=self.codificacion, encodingErrors="strict"
        )


def _nombre_temporal(extension: str) -> str:
    """El nombre con que se copia cada archivo de la capa. El .cpg va con otro nombre: su
    codificacion la interpreta el importador (88591 es ISO-8859-1), y pyshp no la vuelve a leer."""
    return "codificacion.cpg" if extension == "cpg" else f"capa.{extension}"


@contextmanager
def abrir_capa(ruta: Path, capa: str | None) -> Iterator[Capa]:
    """Los archivos de una capa en un directorio temporal, que se borra al salir. De un zip, la
    capa es el miembro sin extension (`conjunto_de_datos/00mun`); sin ella, el unico .shp del zip.
    De un .shp, sus companeros con el mismo nombre. El archivo de origen solo se lee."""
    with tempfile.TemporaryDirectory(prefix="motor-cartera-geografia-") as temporal:
        directorio = Path(temporal)
        if zipfile.is_zipfile(ruta):
            nombre = _extraer_del_zip(ruta, capa, directorio)
        elif ruta.suffix.lower() == ".shp":
            nombre = _copiar_companeros(ruta, capa, directorio)
        else:
            raise ImportacionRechazada(
                f"{ruta.name} no es un zip ni un .shp: la referencia se importa como Shapefile."
            )
        prj = (directorio / _nombre_temporal("prj")).read_text(encoding="latin-1")
        try:
            reconocido = sistemas.reconocer(prj)
        except sistemas.CrsNoReconocido as exc:
            raise ImportacionRechazada(f"La capa {nombre}: {exc}") from exc
        cpg_ruta = directorio / _nombre_temporal("cpg")
        cpg = (
            cpg_ruta.read_text(encoding="ascii", errors="replace").strip()
            if cpg_ruta.exists()
            else None
        )
        yield Capa(nombre, directorio, reconocido, _codificacion(cpg, nombre), cpg)


def _codificacion(cpg: str | None, nombre: str) -> str:
    if cpg is None:
        return "utf-8"
    clave = cpg.upper().replace("-", "").replace("_", "").replace(" ", "")
    if clave not in CODIFICACIONES:
        raise ImportacionRechazada(
            f"La capa {nombre} declara la codificacion {cpg!r} en su .cpg, y no se reconoce."
        )
    return CODIFICACIONES[clave]


def _extraer_del_zip(ruta: Path, capa: str | None, directorio: Path) -> str:
    with zipfile.ZipFile(ruta) as paquete:
        por_nombre: dict[tuple[str, str], zipfile.ZipInfo] = {}
        for miembro in paquete.infolist():
            if miembro.is_dir() or "." not in miembro.filename:
                continue
            base, extension = miembro.filename.rsplit(".", 1)
            por_nombre[(base.lower(), extension.lower())] = miembro
        capas = sorted({base for base, extension in por_nombre if extension == "shp"})
        if capa is None:
            if len(capas) != 1:
                raise ImportacionRechazada(
                    f"{ruta.name} trae {len(capas)} capas ({', '.join(capas) or 'ninguna'}): di "
                    "cual con --capa."
                )
            capa = capas[0]
        base = capa.lower()
        faltan = [e for e in OBLIGATORIOS if (base, e) not in por_nombre]
        if faltan:
            raise ImportacionRechazada(
                f"{ruta.name} no trae la capa {capa} completa: faltan sus archivos "
                f"{', '.join('.' + e for e in faltan)}."
            )
        for extension in (*OBLIGATORIOS, "cpg"):
            miembro = por_nombre.get((base, extension))
            if miembro is None:
                continue
            # Se copia con un nombre propio, no con el del miembro: un nombre de un zip no decide
            # donde se escribe.
            with (
                paquete.open(miembro) as origen,
                (directorio / _nombre_temporal(extension)).open("wb") as destino,
            ):
                while bloque := origen.read(1 << 20):
                    destino.write(bloque)
    return capa


def _copiar_companeros(ruta: Path, capa: str | None, directorio: Path) -> str:
    hermanos = {p.suffix.lower().lstrip("."): p for p in ruta.parent.glob(f"{ruta.stem}.*")}
    faltan = [e for e in OBLIGATORIOS if e not in hermanos]
    if faltan:
        raise ImportacionRechazada(
            f"La capa {ruta.stem} no esta completa: faltan sus archivos "
            f"{', '.join('.' + e for e in faltan)}."
        )
    for extension in (*OBLIGATORIOS, "cpg"):
        if extension in hermanos:
            destino = directorio / _nombre_temporal(extension)
            destino.write_bytes(hermanos[extension].read_bytes())
    return capa or ruta.stem


def validar_estructura(lector: shapefile.Reader, nombre: str) -> dict[str, str]:
    """Que la capa sea de poligonos y traiga los campos de los municipios. Devuelve el nombre de
    cada campo en la capa, que se busca sin distinguir mayusculas."""
    if lector.shapeType != shapefile.POLYGON:
        raise ImportacionRechazada(
            f"La capa {nombre} es de {lector.shapeTypeName}, y una capa de municipios es de "
            "poligonos (POLYGON)."
        )
    presentes = {campo.name.upper(): campo.name for campo in lector.fields[1:]}
    faltan = [c for c in CAMPOS if c not in presentes]
    if faltan:
        raise ImportacionRechazada(
            f"A la capa {nombre} le faltan los campos {', '.join(faltan)}: una capa de municipios "
            f"trae {', '.join(CAMPOS)}."
        )
    if len(lector) == 0:
        raise ImportacionRechazada(f"La capa {nombre} no trae ningun registro.")
    return {c: presentes[c] for c in CAMPOS}


# --- WKB ------------------------------------------------------------------------------------------


def wkb_multipoligono(geometria: dict) -> bytes:
    """El WKB (little endian) de un Polygon o un MultiPolygon de GeoJSON, como MultiPolygon. Los
    anillos van tal como los organizo pyshp: exterior primero y sus huecos despues."""
    if geometria["type"] == "Polygon":
        poligonos = [geometria["coordinates"]]
    elif geometria["type"] == "MultiPolygon":
        poligonos = geometria["coordinates"]
    else:
        raise ImportacionRechazada(f"Una geometria de tipo {geometria['type']} no es un poligono.")
    salida = bytearray(struct.pack("<BII", 1, 6, len(poligonos)))
    for anillos in poligonos:
        salida += struct.pack("<BII", 1, 3, len(anillos))
        for anillo in anillos:
            salida += struct.pack("<I", len(anillo))
            plano = array("d")
            for x, y, *_ in anillo:
                plano.append(x)
                plano.append(y)
            salida += plano.tobytes()
    return bytes(salida)


# --- importar -------------------------------------------------------------------------------------

DESPUES_DEL_COPY = "DESPUES_DEL_COPY"
ANTES_DE_PUBLICAR = "ANTES_DE_PUBLICAR"
DESPUES_DE_LOS_MUNICIPIOS = "DESPUES_DE_LOS_MUNICIPIOS"
PUNTOS_DE_FALLA = (DESPUES_DEL_COPY, ANTES_DE_PUBLICAR, DESPUES_DE_LOS_MUNICIPIOS)


def _punto_de_falla(nombre: str) -> None:
    """Un punto donde una prueba puede inyectar una falla. En produccion no hace nada."""


def importar(
    ruta: str | Path,
    *,
    capa: str | None = None,
    referencia: str | None = None,
    metadatos: Metadatos | None = None,
) -> VersionGeografia:
    """Importa la capa y devuelve su version PUBLICADA.

    Con `referencia`, una clave del manifiesto: el archivo tiene que tener su SHA-256, y la capa,
    el producto y la edicion son los suyos. Sin ella, `metadatos` dice de donde sale. Levanta, sin
    registrar nada: ImportacionRechazada, GeografiaYaImportada y GeografiaEnImportacion; con la
    version registrada FALLIDA: ImportacionFallida."""
    ruta = Path(ruta)
    if (referencia is None) == (metadatos is None):
        raise ValueError(
            "Una importacion es de una referencia del manifiesto o trae sus metadatos."
        )
    sha256, tamano = sha256_de(ruta)
    conocida = None
    if referencia is not None:
        conocida = referencias().get(referencia)
        if conocida is None:
            raise ImportacionRechazada(
                f"El manifiesto no tiene la referencia {referencia!r}: conoce "
                f"{', '.join(referencias())}."
            )
        if sha256 != conocida.sha256:
            raise ImportacionRechazada(
                f"{ruta.name} no es el archivo de {referencia}: su SHA-256 es {sha256} y el del "
                f"manifiesto es {conocida.sha256}. Descargalo otra vez de {conocida.datos['url']}."
            )
        if capa is not None and capa != conocida.capa:
            raise ImportacionRechazada(f"La capa de {referencia} es {conocida.capa}, no {capa}.")
        capa = conocida.capa
    with abrir_capa(ruta, capa) as abierta:
        lector = abierta.lector()
        try:
            campos = validar_estructura(lector, abierta.nombre)
            if conocida is not None and len(lector) != conocida.datos["municipios"]:
                raise ImportacionRechazada(
                    f"La capa {abierta.nombre} trae {len(lector):,} registros, y {conocida.clave} "
                    f"tiene {conocida.datos['municipios']:,} municipios."
                )
            return _importar(ruta, sha256, tamano, abierta, lector, campos, conocida, metadatos)
        finally:
            lector.close()


def _candado(sha256: str, capa: str) -> int:
    """La llave del candado consultivo de una importacion: el archivo y la capa."""
    huella = hashlib.sha256(f"geografia:{sha256}:{capa}".encode()).digest()
    return int.from_bytes(huella[:8], "big", signed=True)


def _importar(
    ruta: Path,
    sha256: str,
    tamano: int,
    capa: Capa,
    lector: shapefile.Reader,
    campos: dict[str, str],
    conocida: ReferenciaConocida | None,
    metadatos: Metadatos | None,
) -> VersionGeografia:
    llave = _candado(sha256, capa.nombre)
    # Una sola conexion para toda la importacion: el candado es de la conexion, y una sesion del
    # pool podria cambiar de conexion entre una transaccion y la siguiente.
    with (
        crear_motor().connect() as conexion,
        Session(bind=conexion, expire_on_commit=False) as s,
    ):
        if not s.execute(
            text("SELECT pg_try_advisory_lock(:llave)"), {"llave": llave}
        ).scalar_one():
            s.rollback()
            raise GeografiaEnImportacion(
                f"Otro proceso esta importando {ruta.name} ({capa.nombre}) en este momento."
            )
        try:
            previa = _de_estado(s, sha256, capa.nombre, EstadoVersionGeografia.PUBLICADA)
            if previa is not None:
                s.expunge(previa)
                raise GeografiaYaImportada(previa)
            _cerrar_interrumpida(s, sha256, capa.nombre)
            version = _registrar(s, ruta, sha256, tamano, capa, conocida, metadatos)
            s.commit()
            version_id, publico = version.id, version.geografia_version_id
            try:
                _publicar(s, version_id, capa, lector, campos, conocida)
                s.commit()
            except Exception as exc:
                s.rollback()
                motivo = (
                    str(exc)
                    if isinstance(exc, ImportacionRechazada)
                    else f"Error al importar ({type(exc).__name__}); ver la bitacora."
                )
                if not isinstance(exc, ImportacionRechazada):
                    log.exception("importacion de %s fallida", ruta.name)
                _fallar(s, version_id, motivo)
                raise ImportacionFallida(publico, motivo) from exc
            publicada = s.get_one(VersionGeografia, version_id)
            # Fuera de la sesion: el rollback que suelta el candado no la caduca.
            s.expunge(publicada)
            return publicada
        finally:
            s.rollback()
            s.execute(text("SELECT pg_advisory_unlock(:llave)"), {"llave": llave})
            s.commit()


def _de_estado(
    s: Session, sha256: str, capa: str, estado: EstadoVersionGeografia
) -> VersionGeografia | None:
    return s.exec(
        select(VersionGeografia).where(
            VersionGeografia.sha256 == sha256,
            VersionGeografia.capa == capa,
            VersionGeografia.estado == estado,
        )
    ).first()


def _cerrar_interrumpida(s: Session, sha256: str, capa: str) -> None:
    """Una version EN_PROCESO de este archivo y esta capa, ahora que este proceso tiene el candado,
    es de una importacion que se interrumpio: queda FALLIDA."""
    s.execute(
        update(VersionGeografia)
        .where(
            VersionGeografia.sha256 == sha256,
            VersionGeografia.capa == capa,
            VersionGeografia.estado == EstadoVersionGeografia.EN_PROCESO,
        )
        .values(
            estado=EstadoVersionGeografia.FALLIDA,
            terminada_en=ahora(),
            detalle="La importacion se interrumpio antes de terminar; no publico ningun municipio.",
        )
        .execution_options(synchronize_session=False)
    )


def versiones_espaciales(s: Session) -> dict[str, str]:
    """Las versiones de PostGIS, GEOS y PROJ con que se importa o se resuelve una geografia: una
    transformacion o un punto en poligono se reproduce con las mismas. Solo los numeros: la cadena
    completa de PROJ trae rutas de la maquina.

    Se consultan en una conexion propia, sin pool, que se cierra al terminar. En PostGIS 3.5 con
    PROJ 8.2, postgis_proj_version() deja inservibles las transformaciones que esa conexion ya
    tenia en su cache: el siguiente ST_Transform falla con "Unknown error (code 4096)". Asi ninguna
    conexion que importa o que resuelve la llama."""
    propia = create_engine(
        s.get_bind().engine.url, poolclass=NullPool, connect_args={"connect_timeout": 5}
    )
    try:
        with propia.connect() as conexion:
            postgis, geos, proj = conexion.execute(
                text("SELECT postgis_lib_version(), postgis_geos_version(), postgis_proj_version()")
            ).one()
    finally:
        propia.dispose()
    return {"postgis": postgis, "geos": geos.split("-")[0], "proj": proj.split()[0]}


def _registrar(
    s: Session,
    ruta: Path,
    sha256: str,
    tamano: int,
    capa: Capa,
    conocida: ReferenciaConocida | None,
    metadatos: Metadatos | None,
) -> VersionGeografia:
    if conocida is not None:
        datos = conocida.datos
        proveedor, producto, edicion = datos["proveedor"], datos["producto"], datos["edicion"]
        nivel = NivelGeografia(datos["nivel"])
        adicionales = {"referencia": conocida.clave, **datos}
    else:
        proveedor, producto, edicion = metadatos.proveedor, metadatos.producto, metadatos.edicion
        nivel = metadatos.nivel
        adicionales = dict(metadatos.adicionales)
    versiones = versiones_espaciales(s)
    version = VersionGeografia(
        proveedor=proveedor,
        producto=producto,
        edicion=edicion,
        nivel=nivel,
        capa=capa.nombre,
        archivo=ruta.name,
        sha256=sha256,
        tamano_bytes=tamano,
        srid_origen=capa.crs.epsg,
        metadatos={
            **adicionales,
            "importador": VERSION_IMPORTADOR,
            "crs_origen": capa.crs.descripcion,
            "cpg": capa.cpg,
            "codificacion": capa.codificacion,
            "versiones": versiones,
        },
    )
    s.add(version)
    s.flush()
    return version


def _publicar(
    s: Session,
    version_id: int,
    capa: Capa,
    lector: shapefile.Reader,
    campos: dict[str, str],
    conocida: ReferenciaConocida | None,
) -> None:
    version = s.exec(
        select(VersionGeografia).where(VersionGeografia.id == version_id).with_for_update()
    ).one()
    s.execute(
        text(
            f"CREATE TEMP TABLE {STAGING} (registro integer NOT NULL, cvegeo text, cve_ent text, "
            "cve_mun text, nombre text, wkb bytea) ON COMMIT DROP"
        )
    )
    registros = copiar(
        s,
        STAGING,
        ("registro", "cvegeo", "cve_ent", "cve_mun", "nombre", "wkb"),
        _filas(lector, campos, capa.nombre),
    )
    _punto_de_falla(DESPUES_DEL_COPY)
    if registros != len(lector):
        raise ImportacionRechazada(
            f"La capa {capa.nombre} dice {len(lector):,} registros y se leyeron {registros:,}."
        )
    _validar_claves(s, capa.nombre)
    s.execute(
        text(
            "CREATE TEMP TABLE geo_normalizada ON COMMIT DROP AS SELECT registro, cvegeo, cve_ent, "
            "cve_mun, nombre, ST_Transform(ST_GeomFromWKB(wkb, :srid), 4326) AS geometria, "
            "false AS reparada FROM geo_importacion"
        ),
        {"srid": capa.crs.epsg},
    )
    reparaciones = _reparar(s, capa.nombre)
    envolvente = _comprobar_envolvente(s, capa.nombre, conocida)
    _punto_de_falla(ANTES_DE_PUBLICAR)
    municipios = s.execute(
        text(
            "INSERT INTO municipio_geografico (version_geografia_id, registro_origen, cve_ent, "
            "cve_mun, cvegeo, nombre_oficial, reparada, geometria) SELECT :version, registro, "
            "cve_ent, cve_mun, cvegeo, nombre, reparada, ST_Multi(geometria) FROM geo_normalizada "
            "ORDER BY cvegeo"
        ),
        {"version": version_id},
    ).rowcount
    _punto_de_falla(DESPUES_DE_LOS_MUNICIPIOS)
    version.estado = EstadoVersionGeografia.PUBLICADA
    version.municipios = municipios
    version.reparadas = len(reparaciones)
    version.metadatos = {
        **version.metadatos,
        "registros": registros,
        "envolvente": envolvente,
        "reparaciones": reparaciones,
    }
    version.terminada_en = ahora()
    transformacion = (
        "ya en EPSG:4326"
        if capa.crs.epsg == 4326
        else f"transformados de EPSG:{capa.crs.epsg} a EPSG:4326"
    )
    version.detalle = (
        f"Se publicaron {municipios:,} municipios de {capa.nombre}, {transformacion}; "
        f"{len(reparaciones)} geometrias reparadas. {VERSION_IMPORTADOR}."
    )
    s.add(version)
    s.flush()
    log.info(
        "referencia %s publicada: %s municipios, %s reparadas",
        version.geografia_version_id,
        municipios,
        len(reparaciones),
    )


def _filas(lector: shapefile.Reader, campos: dict[str, str], nombre: str) -> Iterator[tuple]:
    for numero, par in enumerate(lector.iterShapeRecords(), start=1):
        forma, registro = par.shape, par.record
        if forma.shapeType == shapefile.NULL or not forma.points:
            raise ImportacionRechazada(
                f"El registro {numero} de la capa {nombre} no trae geometria."
            )
        valores = registro.as_dict()
        yield (
            numero,
            _texto(valores[campos["CVEGEO"]]),
            _texto(valores[campos["CVE_ENT"]]),
            _texto(valores[campos["CVE_MUN"]]),
            _texto(valores[campos["NOMGEO"]]),
            wkb_multipoligono(forma.__geo_interface__),
        )


def _texto(valor: object) -> str | None:
    if valor is None:
        return None
    return unicodedata.normalize("NFC", str(valor)).strip()


def _validar_claves(s: Session, nombre: str) -> None:
    malas = s.execute(
        text(
            f"SELECT registro, cvegeo, cve_ent, cve_mun FROM {STAGING} WHERE NOT (coalesce("
            "cve_ent ~ '^[0-9]{2}$' AND cve_mun ~ '^[0-9]{3}$' AND cvegeo = cve_ent || cve_mun, "
            "false)) OR coalesce(nombre, '') = '' ORDER BY registro LIMIT 5"
        )
    ).all()
    if malas:
        ejemplos = "; ".join(
            f"registro {r} (CVEGEO {c!r}, CVE_ENT {e!r}, CVE_MUN {m!r})" for r, c, e, m in malas
        )
        raise ImportacionRechazada(
            f"La capa {nombre} trae claves que no son de un municipio, o un municipio sin nombre: "
            f"{ejemplos}. CVEGEO es CVE_ENT (2 digitos) seguido de CVE_MUN (3)."
        )
    repetidas = s.execute(
        text(
            f"SELECT cvegeo, count(*) FROM {STAGING} GROUP BY cvegeo HAVING count(*) > 1 "
            "ORDER BY cvegeo LIMIT 5"
        )
    ).all()
    if repetidas:
        raise ImportacionRechazada(
            f"La capa {nombre} repite CVEGEO: "
            + ", ".join(f"{c} ({n} veces)" for c, n in repetidas)
            + ". Un municipio aparece una sola vez."
        )


def _reparar(s: Session, nombre: str) -> list[dict[str, Any]]:
    """Repara las geometrias invalidas, si la reparacion es segura, y devuelve cada una con su
    motivo; si alguna no lo es, ImportacionRechazada."""
    invalidas = s.execute(
        text(
            "SELECT registro, cvegeo, ST_IsValidReason(geometria), ST_Area(geometria::geography) "
            "FROM geo_normalizada WHERE NOT ST_IsValid(geometria) ORDER BY cvegeo"
        )
    ).all()
    if not invalidas:
        return []
    reparaciones = []
    for registro, cvegeo, motivo, area in invalidas:
        reparada = s.execute(
            text(
                "SELECT ST_IsValid(g), ST_IsEmpty(g), ST_Area(g::geography) FROM (SELECT "
                "ST_Multi(ST_CollectionExtract(ST_MakeValid(geometria), 3)) AS g FROM "
                "geo_normalizada WHERE registro = :registro) x"
            ),
            {"registro": registro},
        ).one()
        valida, vacia, area_reparada = reparada
        cambio = abs(area_reparada - area) / area if area else float("inf")
        if not valida or vacia or cambio > TOLERANCIA_DE_AREA:
            raise ImportacionRechazada(
                f"La geometria del municipio {cvegeo} (registro {registro}) es invalida "
                f"({motivo}) y repararla no es seguro: su area cambiaria "
                f"{'por completo' if cambio == float('inf') else f'{cambio:.4%}'}. Se rechaza la "
                "importacion en lugar de repararla en silencio."
            )
        reparaciones.append(
            {
                "cvegeo": cvegeo,
                "registro": registro,
                "motivo": motivo,
                "metodo": "ST_MakeValid, solo poligonos",
                "area_m2_antes": round(area, 2),
                "area_m2_despues": round(area_reparada, 2),
            }
        )
    s.execute(
        text(
            "UPDATE geo_normalizada SET geometria = ST_Multi(ST_CollectionExtract("
            "ST_MakeValid(geometria), 3)), reparada = true WHERE NOT ST_IsValid(geometria)"
        )
    )
    return reparaciones


def _comprobar_envolvente(
    s: Session, nombre: str, conocida: ReferenciaConocida | None
) -> list[float]:
    """La envolvente de la capa en EPSG:4326, que tiene que caber en grados validos y, con un
    manifiesto, en la envolvente que este declara: una capa transformada con el sistema equivocado
    queda en otra parte del mundo."""
    xmin, ymin, xmax, ymax = s.execute(
        text(
            "SELECT ST_XMin(e), ST_YMin(e), ST_XMax(e), ST_YMax(e) FROM (SELECT "
            "ST_Extent(geometria) AS e FROM geo_normalizada) x"
        )
    ).one()
    limites = [-180.0, -90.0, 180.0, 90.0]
    if conocida is not None and conocida.datos.get("envolvente"):
        limites = conocida.datos["envolvente"]
    if xmin < limites[0] or ymin < limites[1] or xmax > limites[2] or ymax > limites[3]:
        raise ImportacionRechazada(
            f"La capa {nombre} queda, en EPSG:4326, entre ({xmin:.4f}, {ymin:.4f}) y "
            f"({xmax:.4f}, {ymax:.4f}), fuera de lo que se espera ({limites}): su sistema de "
            "coordenadas no es el que declara."
        )
    return [round(v, 6) for v in (xmin, ymin, xmax, ymax)]


def _fallar(s: Session, version_id: int, motivo: str) -> None:
    s.execute(
        update(VersionGeografia)
        .where(
            VersionGeografia.id == version_id,
            VersionGeografia.estado == EstadoVersionGeografia.EN_PROCESO,
        )
        .values(estado=EstadoVersionGeografia.FALLIDA, terminada_en=ahora(), detalle=motivo[:2000])
        .execution_options(synchronize_session=False)
    )
    s.commit()
