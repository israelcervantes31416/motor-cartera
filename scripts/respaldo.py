"""El respaldo y la restauracion oficiales de motor-cartera: la base y el almacen de artefactos.

    respaldo.py respaldar [--destino /respaldos] [--almacen /var/lib/motor-cartera/fuentes]
    respaldo.py verificar /respaldos/<respaldo>
    respaldo.py restaurar /respaldos/<respaldo> [--almacen /var/lib/motor-cartera/fuentes]
                          [--revision-esperada 0017] [--informe restauracion.json]

El estado de motor-cartera vive en dos lugares, y un respaldo que solo tenga uno no restaura nada
que sirva: PostgreSQL (las filas) y el almacen de artefactos (MC_SOURCE_STORE_ROOT: cada archivo
recibido, byte por byte, guardado por su SHA-256, que las filas de artefacto_fuente registran). Una
base sin su almacen tiene corridas cuyo archivo ya no existe; un almacen sin su base es un monton de
archivos sin nombre.

Corre donde hay pg_dump y pg_restore del mismo mayor que el servidor y psycopg: la imagen
motor-cartera-respaldo (infra/respaldo/Dockerfile, sobre la de PostGIS 16) en el compose de
produccion, o el runner del CI. Solo usa la biblioteca estandar y psycopg.

**Respaldar.** Una transaccion REPEATABLE READ de solo lectura exporta su foto de la base
(pg_export_snapshot); pg_dump vuelca esa misma foto (--snapshot) y, en esa misma transaccion, se
cuentan las filas de cada tabla y se leen los artefactos registrados: los conteos y la lista del
manifiesto son exactamente los del volcado. Formato custom, sin dueno ni permisos (--no-owner
--no-acl), solo el esquema de la aplicacion (public): las extensiones (PostGIS) las pone la
plataforma de destino. Nunca se copian los archivos de la base mientras corre.

Despues se copian del almacen al respaldo los artefactos de la lista, firmando cada uno mientras se
copia. La aplicacion escribe primero el objeto durable y despues la fila que lo registra: todo lo
que la foto registra ya esta en el almacen, y lo que llego despues de la foto no lo registra, asi
que no hace falta. Un artefacto que falte, o cuyos bytes ya no sean los de su SHA-256, niega el
respaldo entero y no deja nada: un respaldo incompleto no se da por bueno.

El manifiesto (manifiesto.json) dice cuando, de que revision del esquema, de que version de
PostgreSQL y de la aplicacion, el SHA-256 y el tamano del volcado, los conteos y el resumen del
almacen (cuantos objetos, cuantos bytes y el SHA-256 de su lista, almacen.lista): ningun dato de una
cuenta. Cada objeto se identifica por su propio SHA-256.

**Restaurar.** Primero se verifica el respaldo entero: el volcado contra su SHA-256 y su tamano, la
lista contra el suyo y cada objeto contra su nombre. Uno alterado no toca nada. Despues, que el
destino sea otra base y este vacia (nunca se restaura sobre la base de origen ni sobre una con
tablas), que su PostgreSQL no sea de un mayor anterior, que tenga las extensiones del origen y que
la revision del esquema sea la esperada (si no, se niega, salvo --permitir-otra-revision, que lo
advierte). Los objetos se copian al almacen de destino antes que la base: si la base falla, quedan
objetos que ninguna fila registra, que no corrompen nada (el almacen es por contenido). pg_restore
corre en una sola transaccion: o queda todo, o nada. Al final se valida: la revision, los conteos de
cada tabla contra el manifiesto, que ninguna restriccion quede sin validar y que cada artefacto que
la base registra este en el almacen con su tamano. El informe sale por la salida estandar (y a
--informe): el respaldo no se modifica, y se puede montar de solo lectura.

El SHA-256 del volcado verifica el archivo, no la base restaurada: dos bases con las mismas filas no
tienen por que tener los mismos bytes. Lo que se compara despues de restaurar son las filas.

Termina con 0 si todo salio bien, con 1 si se nego (o una validacion fallo) y con 2 ante un error
inesperado.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

FORMATO = "respaldo-motor-cartera/v1"
ARCHIVO_DUMP = "motor-cartera.dump"
ARCHIVO_MANIFIESTO = "manifiesto.json"
ARCHIVO_LISTA = "almacen.lista"
DIRECTORIO_ALMACEN = "almacen"
ESQUEMA = "public"
TABLAS_DE_EXTENSIONES = ("spatial_ref_sys",)
VERSION_EN_IMAGEN = Path("/usr/local/share/motor-cartera/VERSION")
PATRON_SHA256 = re.compile(r"[0-9a-f]{64}")
BLOQUE = 1 << 20
CAMPOS = (
    "formato",
    "creado_en",
    "app_version",
    "revision_esquema",
    "postgres_version",
    "postgres_version_num",
    "base_origen",
    "extensiones",
    "dump",
    "conteos",
    "almacen",
)


class Negado(Exception):
    """El respaldo o la restauracion se nego, con su codigo estable."""

    def __init__(self, codigo: str, mensaje: str) -> None:
        super().__init__(mensaje)
        self.codigo = codigo


# --- utilidades sin base ------------------------------------------------------------------------


def dsn_libpq(dsn: str) -> str:
    """Una URL de SQLAlchemy (postgresql+psycopg://) como la entiende libpq (postgresql://)."""
    return re.sub(r"^postgresql\+[a-z0-9]+://", "postgresql://", dsn.strip())


def leer_dsn(variable: str, argumento: str | None) -> str:
    if argumento:
        return dsn_libpq(Path(argumento).read_text(encoding="utf-8"))
    ruta = os.environ.get(f"{variable}_FILE")
    if ruta:
        return dsn_libpq(Path(ruta).read_text(encoding="utf-8"))
    valor = os.environ.get(variable)
    if valor:
        return dsn_libpq(valor)
    raise Negado("SIN_DSN", f"Falta {variable} o {variable}_FILE (o --dsn-file).")


def sha256_de(ruta: Path) -> str:
    return firmar(ruta)[0]


def firmar(ruta: Path) -> tuple[str, int]:
    """El SHA-256 y el tamano de un archivo, leido por bloques."""
    digest = hashlib.sha256()
    tamano = 0
    with ruta.open("rb") as archivo:
        for bloque in iter(lambda: archivo.read(BLOQUE), b""):
            digest.update(bloque)
            tamano += len(bloque)
    return digest.hexdigest(), tamano


def clave_de(sha256: str) -> str:
    """Donde vive un objeto, en el almacen y en el respaldo: `sha256/ab/abcdef...`, la misma
    storage_key de la aplicacion (motor_cartera.fuentes.almacen)."""
    if not PATRON_SHA256.fullmatch(sha256):
        raise Negado("SHA256_INVALIDO", f"No es un SHA-256 en hexadecimal: {sha256!r}.")
    return f"sha256/{sha256[:2]}/{sha256}"


def version_app() -> str:
    if VERSION_EN_IMAGEN.exists():
        return VERSION_EN_IMAGEN.read_text(encoding="utf-8").strip()
    inicial = Path(__file__).resolve().parents[1] / "src" / "motor_cartera" / "__init__.py"
    if inicial.exists():
        coincidencia = re.search(r'__version__ = "([^"]+)"', inicial.read_text(encoding="utf-8"))
        if coincidencia:
            return coincidencia.group(1)
    return os.environ.get("MC_APP_VERSION", "desconocida")


def mayor(version_num: int) -> int:
    return version_num // 10000


def mayor_de_binario(binario: str) -> int:
    salida = subprocess.run([binario, "--version"], capture_output=True, text=True, check=True)
    coincidencia = re.search(r"(\d+)(?:\.\d+)?", salida.stdout)
    if not coincidencia:
        raise Negado("BINARIO_DESCONOCIDO", f"No se reconoce la version de {binario}.")
    return int(coincidencia.group(1))


def lista_de_objetos(objetos: dict[str, int]) -> str:
    """La lista del almacen: una linea `<sha256> <bytes>` por objeto, en orden."""
    return "".join(f"{sha256} {tamano}\n" for sha256, tamano in sorted(objetos.items()))


def entradas_a_restaurar(toc: str) -> str:
    """La tabla de contenido de pg_restore (--list) sin lo que ya pone la plataforma de destino.

    pg_dump con --schema=public declara el esquema, y en el destino ya existe (lo crea initdb): su
    CREATE SCHEMA abortaria la restauracion entera. Y puede traer los datos de una tabla de
    configuracion de una extension (spatial_ref_sys, de PostGIS), que en el destino ya tiene la
    extension y que no es del rol que restaura. Se quitan esas entradas; todo lo de la aplicacion
    se restaura igual."""
    conservadas = []
    for linea in toc.splitlines():
        entrada = ";" in linea and not linea.startswith(";")
        partes = linea.split(";", 1)[1].split() if entrada else []
        # "<dump_id>; <catalogo> <oid> SCHEMA - public <dueno>" y "... COMMENT - SCHEMA public ..."
        if len(partes) >= 5 and partes[2:5] == ["SCHEMA", "-", ESQUEMA]:
            continue
        if len(partes) >= 6 and partes[2:6] == ["COMMENT", "-", "SCHEMA", ESQUEMA]:
            continue
        # "<dump_id>; 0 <oid> TABLE DATA public spatial_ref_sys <dueno>"
        if (
            len(partes) >= 6
            and partes[2:5] == ["TABLE", "DATA", ESQUEMA]
            and partes[5] in TABLAS_DE_EXTENSIONES
        ):
            continue
        conservadas.append(linea)
    return "\n".join(conservadas) + "\n"


def _quitar(ruta: Path) -> None:
    """Borra un directorio con objetos de solo lectura (Windows no borra un archivo asi)."""

    def de_nuevo(funcion, camino, _error) -> None:
        os.chmod(camino, stat.S_IWRITE | stat.S_IREAD)
        funcion(camino)

    shutil.rmtree(ruta, onexc=de_nuevo)


def _sincronizar_directorio(directorio: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(directorio, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def copiar_verificando(
    origen: Path, destino: Path, sha256: str, tamano: int, *, temporales: Path, codigo: str
) -> None:
    """Copia un objeto por bloques a un temporal, firmandolo mientras copia; solo si sus bytes son
    los de su SHA-256 y su tamano lo mueve a su lugar (os.replace, atomico en el mismo sistema de
    archivos) y lo deja de solo lectura. Si no, levanta Negado con `codigo` y no deja nada."""
    temporales.mkdir(parents=True, exist_ok=True)
    descriptor, nombre = tempfile.mkstemp(prefix="objeto-", suffix=".parcial", dir=temporales)
    temporal = Path(nombre)
    try:
        firma = hashlib.sha256()
        leidos = 0
        with origen.open("rb") as entrada, os.fdopen(descriptor, "wb") as salida:
            for bloque in iter(lambda: entrada.read(BLOQUE), b""):
                firma.update(bloque)
                leidos += len(bloque)
                salida.write(bloque)
            salida.flush()
            os.fsync(salida.fileno())
        if firma.hexdigest() != sha256 or leidos != tamano:
            raise Negado(
                codigo,
                f"El objeto {sha256} no tiene los bytes de su SHA-256 ({leidos} bytes leidos; "
                f"se esperaban {tamano}).",
            )
        destino.parent.mkdir(parents=True, exist_ok=True)
        if destino.exists():
            os.chmod(destino, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temporal, destino)
        os.chmod(destino, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        _sincronizar_directorio(destino.parent)
    finally:
        temporal.unlink(missing_ok=True)


# --- verificar un respaldo ----------------------------------------------------------------------


def leer_manifiesto(directorio: Path) -> dict:
    ruta = directorio / ARCHIVO_MANIFIESTO
    if not ruta.exists():
        raise Negado("SIN_MANIFIESTO", f"{directorio} no tiene {ARCHIVO_MANIFIESTO}.")
    try:
        manifiesto = json.loads(ruta.read_text(encoding="utf-8"))
    except ValueError:
        raise Negado("MANIFIESTO_ILEGIBLE", "El manifiesto no es JSON.") from None
    faltan = [c for c in CAMPOS if c not in manifiesto]
    if faltan:
        raise Negado("MANIFIESTO_INCOMPLETO", f"Al manifiesto le falta: {', '.join(faltan)}.")
    if manifiesto["formato"] != FORMATO:
        raise Negado(
            "FORMATO_DESCONOCIDO",
            f"El manifiesto es {manifiesto['formato']!r}; esta herramienta lee {FORMATO!r}.",
        )
    return manifiesto


def _leer_lista(directorio: Path, almacen: dict) -> dict[str, int]:
    ruta = directorio / almacen["lista"]
    if not ruta.exists():
        raise Negado("RESPALDO_ALTERADO", f"Falta {almacen['lista']}.")
    if sha256_de(ruta) != almacen["sha256_lista"]:
        raise Negado("RESPALDO_ALTERADO", f"El SHA-256 de {ruta.name} no es el del manifiesto.")
    objetos: dict[str, int] = {}
    for numero, linea in enumerate(ruta.read_text(encoding="ascii").splitlines(), 1):
        partes = linea.split(" ")
        if len(partes) != 2 or not PATRON_SHA256.fullmatch(partes[0]) or not partes[1].isdigit():
            raise Negado("RESPALDO_ALTERADO", f"La linea {numero} de {ruta.name} no tiene forma.")
        objetos[partes[0]] = int(partes[1])
    if len(objetos) != almacen["objetos"] or sum(objetos.values()) != almacen["bytes"]:
        raise Negado("RESPALDO_ALTERADO", "La lista del almacen no suma lo que dice el manifiesto.")
    return objetos


def verificar(directorio: Path) -> tuple[dict, dict[str, int]]:
    """El manifiesto y los objetos del almacen, si el respaldo es exactamente el que describe: el
    volcado con su SHA-256 y su tamano, la lista con el suyo y cada objeto con los bytes de su
    nombre, sin que falte ni sobre ninguno."""
    manifiesto = leer_manifiesto(directorio)
    volcado = directorio / manifiesto["dump"]["archivo"]
    if not volcado.exists():
        raise Negado("SIN_VOLCADO", f"Falta {volcado.name}.")
    tamano = volcado.stat().st_size
    if tamano != manifiesto["dump"]["bytes"]:
        raise Negado(
            "RESPALDO_ALTERADO",
            f"{volcado.name} mide {tamano} bytes; el manifiesto dice "
            f"{manifiesto['dump']['bytes']}.",
        )
    if sha256_de(volcado) != manifiesto["dump"]["sha256"]:
        raise Negado("RESPALDO_ALTERADO", f"El SHA-256 de {volcado.name} no es el del manifiesto.")
    almacen = manifiesto["almacen"]
    if not almacen.get("incluido"):
        return manifiesto, {}
    objetos = _leer_lista(directorio, almacen)
    raiz = directorio / almacen["directorio"]
    presentes = (
        {p.relative_to(raiz).as_posix() for p in raiz.rglob("*") if p.is_file()}
        if raiz.is_dir()
        else set()
    )
    esperados = {clave_de(sha256) for sha256 in objetos}
    if presentes != esperados:
        raise Negado(
            "RESPALDO_ALTERADO",
            f"El almacen del respaldo tiene {len(presentes - esperados)} objetos de mas y le "
            f"faltan {len(esperados - presentes)}.",
        )
    for sha256, tamano_objeto in objetos.items():
        if firmar(raiz / clave_de(sha256)) != (sha256, tamano_objeto):
            raise Negado("RESPALDO_ALTERADO", f"El objeto {sha256} ya no tiene sus bytes.")
    return manifiesto, objetos


@dataclass(frozen=True)
class Destino:
    """Lo que se sabe de la base de destino antes de restaurar."""

    nombre: str
    identificador_sistema: str | None
    version_num: int
    tablas: int
    extensiones: dict[str, str]


def revisar_compatibilidad(
    manifiesto: dict,
    destino: Destino,
    *,
    revision_esperada: str | None,
    permitir_otra_revision: bool,
    mayor_pg_restore: int,
) -> list[str]:
    """Levanta Negado si no se debe restaurar ahi; devuelve las advertencias."""
    origen = manifiesto["base_origen"]
    if (
        origen.get("identificador_sistema")
        and origen["identificador_sistema"] == destino.identificador_sistema
        and origen.get("nombre") == destino.nombre
    ):
        raise Negado("DESTINO_ES_EL_ORIGEN", "Nunca se restaura sobre la base de origen.")
    if destino.tablas:
        raise Negado(
            "BASE_NO_VACIA",
            f"La base de destino ya tiene {destino.tablas} tablas en {ESQUEMA}: se restaura solo "
            "en una base nueva y vacia.",
        )
    if mayor(destino.version_num) < mayor(manifiesto["postgres_version_num"]):
        raise Negado(
            "POSTGRES_ANTERIOR",
            f"El respaldo es de PostgreSQL {manifiesto['postgres_version']} y el destino de un "
            "mayor anterior.",
        )
    if mayor_pg_restore < mayor(manifiesto["postgres_version_num"]):
        raise Negado("PG_RESTORE_ANTERIOR", "pg_restore es de un mayor anterior al del respaldo.")
    faltan = sorted(set(manifiesto["extensiones"]) - set(destino.extensiones))
    if faltan:
        raise Negado(
            "EXTENSIONES_FALTANTES",
            f"El destino no tiene {', '.join(faltan)}: la plataforma debe proveerlas.",
        )
    advertencias = []
    revision = manifiesto["revision_esquema"]
    if revision_esperada is not None and revision != revision_esperada:
        if not permitir_otra_revision:
            raise Negado(
                "REVISION_INCOMPATIBLE",
                f"El respaldo es de la revision {revision} y se espera la {revision_esperada}. "
                "Restauralo con la version de la aplicacion que le corresponde, o usa "
                "--permitir-otra-revision sabiendo que despues hay que migrar.",
            )
        advertencias.append(
            f"La revision del respaldo ({revision}) no es la esperada ({revision_esperada})."
        )
    return advertencias


# --- con la base --------------------------------------------------------------------------------


def _conectar(dsn: str):
    import psycopg

    return psycopg.connect(dsn, connect_timeout=10)


def _entorno_libpq(dsn: str) -> dict[str, str]:
    """El entorno con que corren pg_dump y pg_restore: la conexion va en variables PG*, no en la
    linea de comandos, donde cualquiera que liste los procesos veria la contrasena."""
    from psycopg.conninfo import conninfo_to_dict

    variables = {"host": "PGHOST", "port": "PGPORT", "user": "PGUSER", "password": "PGPASSWORD"}
    variables["dbname"] = "PGDATABASE"
    entorno = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    for llave, valor in conninfo_to_dict(dsn).items():
        if llave in variables and valor is not None:
            entorno[variables[llave]] = str(valor)
    return entorno


def _tablas(cursor) -> list[str]:
    cursor.execute(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = %s AND c.relkind IN ('r', 'p') AND NOT EXISTS "
        "(SELECT 1 FROM pg_depend d WHERE d.objid = c.oid AND d.deptype = 'e') ORDER BY c.relname",
        (ESQUEMA,),
    )
    return [fila[0] for fila in cursor.fetchall() if fila[0] not in TABLAS_DE_EXTENSIONES]


def _conteos(cursor, tablas: list[str]) -> dict[str, int]:
    from psycopg import sql

    conteos = {}
    for tabla in tablas:
        cursor.execute(
            sql.SQL("SELECT count(*) FROM {}.{}").format(
                sql.Identifier(ESQUEMA), sql.Identifier(tabla)
            )
        )
        conteos[tabla] = cursor.fetchone()[0]
    return conteos


def _artefactos(cursor) -> dict[str, int]:
    """Los artefactos que la base registra (su SHA-256 y su tamano); ninguno si aun no hay tabla."""
    cursor.execute("SELECT to_regclass(%s) IS NOT NULL", (f"{ESQUEMA}.artefacto_fuente",))
    if not cursor.fetchone()[0]:
        return {}
    cursor.execute(f"SELECT sha256, tamano_bytes FROM {ESQUEMA}.artefacto_fuente")
    return {sha256: int(tamano) for sha256, tamano in cursor.fetchall()}


def _identificador(cursor) -> str | None:
    """El identificador del cluster (pg_control_system), si el rol puede leerlo; si no, None, y la
    revision de que el destino no sea el origen se apoya en que este vacio."""
    try:
        cursor.execute("SELECT system_identifier::text FROM pg_control_system()")
        return cursor.fetchone()[0]
    except Exception:
        if not cursor.connection.autocommit:
            cursor.connection.rollback()
        return None


def _revision(cursor) -> str | None:
    cursor.execute("SELECT to_regclass(%s) IS NOT NULL", (f"{ESQUEMA}.alembic_version",))
    if not cursor.fetchone()[0]:
        return None
    cursor.execute(f"SELECT version_num FROM {ESQUEMA}.alembic_version LIMIT 1")
    fila = cursor.fetchone()
    return fila[0] if fila else None


def respaldar(dsn: str, destino_raiz: Path, almacen: Path | None) -> Path:
    """Respalda la base y, si se le da, el almacen, en un directorio nuevo bajo `destino_raiz`. Si
    algo falla, borra lo que alcanzo a escribir: no deja un respaldo a medias."""
    if shutil.which("pg_dump") is None:
        raise Negado("SIN_PG_DUMP", "pg_dump no esta en el PATH.")
    if almacen is not None and not almacen.is_dir():
        raise Negado("ALMACEN_NO_MONTADO", f"El almacen {almacen} no existe o no es un directorio.")
    inicio = time.monotonic()
    creado = datetime.datetime.now(datetime.UTC)
    directorio = destino_raiz / f"motor-cartera-{creado:%Y%m%dT%H%M%SZ}"
    directorio.mkdir(parents=True, exist_ok=False)
    try:
        return _respaldar_en(dsn, directorio, almacen, creado, inicio)
    except BaseException:
        _quitar(directorio)
        raise


def _respaldar_en(
    dsn: str, directorio: Path, almacen: Path | None, creado: datetime.datetime, inicio: float
) -> Path:
    volcado = directorio / ARCHIVO_DUMP
    with _conectar(dsn) as conexion:
        conexion.autocommit = True
        with conexion.cursor() as cursor:
            identificador = _identificador(cursor)
    with _conectar(dsn) as conexion:
        conexion.autocommit = False
        with conexion.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            cursor.execute("SELECT pg_export_snapshot()")
            foto = cursor.fetchone()[0]
            cursor.execute("SHOW server_version")
            version = cursor.fetchone()[0]
            cursor.execute("SHOW server_version_num")
            version_num = int(cursor.fetchone()[0])
            cursor.execute("SELECT current_database()")
            base = cursor.fetchone()[0]
            cursor.execute(
                "SELECT extname, extversion FROM pg_extension WHERE extname <> 'plpgsql'"
            )
            extensiones = dict(cursor.fetchall())
            revision = _revision(cursor)
            tablas = _tablas(cursor)
            if mayor_de_binario("pg_dump") < mayor(version_num):
                raise Negado("PG_DUMP_ANTERIOR", "pg_dump es de un mayor anterior al del servidor.")
            opciones = ["--format=custom", "--no-owner", "--no-acl", f"--schema={ESQUEMA}"]
            # Los datos de las tablas de las extensiones son de la plataforma, no de la aplicacion.
            opciones += [f"--exclude-table-data={ESQUEMA}.{t}" for t in TABLAS_DE_EXTENSIONES]
            subprocess.run(
                ["pg_dump", *opciones, f"--snapshot={foto}", f"--file={volcado}"],
                check=True,
                env=_entorno_libpq(dsn),
            )
            conteos = _conteos(cursor, tablas)
            artefactos = _artefactos(cursor)
        conexion.rollback()
    volcado_listo = time.monotonic()
    if artefactos and almacen is None:
        raise Negado(
            "SIN_ALMACEN",
            f"La base registra {len(artefactos)} artefactos y no se dio el almacen (--almacen o "
            "MC_SOURCE_STORE_ROOT): el respaldo quedaria sin sus archivos. Usa --sin-almacen solo "
            "si el almacen se respalda por otro medio.",
        )
    if almacen is not None:
        temporales = directorio / ".tmp"
        for sha256, tamano in sorted(artefactos.items()):
            origen = almacen / clave_de(sha256)
            if not origen.is_file():
                raise Negado(
                    "ALMACEN_INCOMPLETO",
                    f"La base registra el artefacto {sha256} y el almacen no lo tiene.",
                )
            copiar_verificando(
                origen,
                directorio / DIRECTORIO_ALMACEN / clave_de(sha256),
                sha256,
                tamano,
                temporales=temporales,
                codigo="ALMACEN_DANADO",
            )
        if temporales.exists():
            temporales.rmdir()
        lista = directorio / ARCHIVO_LISTA
        lista.write_text(lista_de_objetos(artefactos), encoding="ascii")
        seccion_almacen = {
            "incluido": True,
            "directorio": DIRECTORIO_ALMACEN,
            "lista": ARCHIVO_LISTA,
            "sha256_lista": sha256_de(lista),
            "objetos": len(artefactos),
            "bytes": sum(artefactos.values()),
        }
    else:
        seccion_almacen = {"incluido": False, "registrados": len(artefactos)}
    manifiesto = {
        "formato": FORMATO,
        "creado_en": creado.isoformat(timespec="seconds"),
        "herramienta": "scripts/respaldo.py",
        "app_version": version_app(),
        "revision_esquema": revision,
        "postgres_version": version,
        "postgres_version_num": version_num,
        "pg_dump": subprocess.run(
            ["pg_dump", "--version"], capture_output=True, text=True, check=True
        ).stdout.strip(),
        "base_origen": {"nombre": base, "identificador_sistema": identificador},
        "extensiones": extensiones,
        "dump": {
            "archivo": ARCHIVO_DUMP,
            "formato": "custom",
            "opciones": opciones,
            "sha256": sha256_de(volcado),
            "bytes": volcado.stat().st_size,
        },
        "conteos": conteos,
        "almacen": seccion_almacen,
        "duracion_segundos": {
            "base": round(volcado_listo - inicio, 3),
            "almacen": round(time.monotonic() - volcado_listo, 3),
            "total": round(time.monotonic() - inicio, 3),
        },
    }
    (directorio / ARCHIVO_MANIFIESTO).write_text(
        json.dumps(manifiesto, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return directorio


def _destino(cursor) -> Destino:
    cursor.execute("SELECT current_database()")
    nombre = cursor.fetchone()[0]
    cursor.execute("SHOW server_version_num")
    version_num = int(cursor.fetchone()[0])
    cursor.execute("SELECT extname, extversion FROM pg_extension WHERE extname <> 'plpgsql'")
    extensiones = dict(cursor.fetchall())
    return Destino(nombre, _identificador(cursor), version_num, len(_tablas(cursor)), extensiones)


def restaurar_objetos(directorio: Path, almacen: Path, objetos: dict[str, int]) -> dict[str, int]:
    """Copia los objetos del respaldo al almacen de destino. Uno que ya este con sus bytes no se
    toca; uno danado se repone con la copia verificada del respaldo."""
    cuenta = {"copiados": 0, "ya_presentes": 0, "repuestos": 0}
    raiz = directorio / DIRECTORIO_ALMACEN
    for sha256, tamano in sorted(objetos.items()):
        final = almacen / clave_de(sha256)
        if final.is_file():
            if firmar(final) == (sha256, tamano):
                cuenta["ya_presentes"] += 1
                continue
            cuenta["repuestos"] += 1
        else:
            cuenta["copiados"] += 1
        copiar_verificando(
            raiz / clave_de(sha256),
            final,
            sha256,
            tamano,
            temporales=almacen / "tmp",
            codigo="RESPALDO_ALTERADO",
        )
    return cuenta


def restaurar(
    dsn: str,
    directorio: Path,
    *,
    almacen: Path | None,
    sin_almacen: bool,
    revision_esperada: str | None,
    permitir_otra_revision: bool,
) -> dict:
    inicio = time.monotonic()
    manifiesto, objetos = verificar(directorio)
    verificado = time.monotonic()
    if objetos and almacen is None and not sin_almacen:
        raise Negado(
            "SIN_ALMACEN_DESTINO",
            f"El respaldo trae {len(objetos)} artefactos y no se dio el almacen de destino "
            "(--almacen o MC_SOURCE_STORE_ROOT). Usa --sin-almacen solo si el almacen se restaura "
            "por otro medio.",
        )
    if shutil.which("pg_restore") is None:
        raise Negado("SIN_PG_RESTORE", "pg_restore no esta en el PATH.")
    with _conectar(dsn) as conexion, conexion.cursor() as cursor:
        destino = _destino(cursor)
    advertencias = revisar_compatibilidad(
        manifiesto,
        destino,
        revision_esperada=revision_esperada,
        permitir_otra_revision=permitir_otra_revision,
        mayor_pg_restore=mayor_de_binario("pg_restore"),
    )
    if not manifiesto["almacen"].get("incluido"):
        advertencias.append("El respaldo no incluye el almacen de artefactos.")
    cuenta = {"copiados": 0, "ya_presentes": 0, "repuestos": 0}
    if almacen is not None:
        cuenta = restaurar_objetos(directorio, almacen, objetos)
    elif objetos:
        advertencias.append("No se restauro el almacen de artefactos (--sin-almacen).")
    objetos_listos = time.monotonic()
    entorno = _entorno_libpq(dsn)
    archivo_dump = str(directorio / manifiesto["dump"]["archivo"])
    toc = subprocess.run(
        ["pg_restore", "--list", archivo_dump], capture_output=True, text=True, check=True
    ).stdout
    with tempfile.TemporaryDirectory(prefix="restauracion-") as temporal:
        lista_toc = Path(temporal) / "entradas.toc"
        lista_toc.write_text(entradas_a_restaurar(toc), encoding="utf-8")
        subprocess.run(
            [
                "pg_restore",
                "--exit-on-error",
                "--single-transaction",
                "--no-owner",
                "--no-acl",
                f"--use-list={lista_toc}",
                f"--dbname={entorno.get('PGDATABASE', '')}",
                archivo_dump,
            ],
            check=True,
            env=entorno,
        )
    restaurado = time.monotonic()
    with _conectar(dsn) as conexion:
        conexion.autocommit = True
        with conexion.cursor() as cursor:
            revision = _revision(cursor)
            conteos = _conteos(cursor, _tablas(cursor))
            cursor.execute(
                "SELECT count(*) FROM pg_constraint c "
                "JOIN pg_namespace n ON n.oid = c.connamespace "
                "WHERE n.nspname = %s AND NOT c.convalidated",
                (ESQUEMA,),
            )
            sin_validar = cursor.fetchone()[0]
            registrados = _artefactos(cursor)
            cursor.execute("ANALYZE")
    diferencias = {
        tabla: {"esperado": manifiesto["conteos"].get(tabla), "restaurado": conteos.get(tabla)}
        for tabla in sorted(set(manifiesto["conteos"]) | set(conteos))
        if manifiesto["conteos"].get(tabla) != conteos.get(tabla)
    }
    faltantes = (
        sum(
            1
            for sha256, tamano in registrados.items()
            if not (almacen / clave_de(sha256)).is_file()
            or (almacen / clave_de(sha256)).stat().st_size != tamano
        )
        if almacen is not None
        else None
    )
    informe = {
        "respaldo": directorio.name,
        "revision_esquema": revision,
        "tablas": len(conteos),
        "filas": sum(conteos.values()),
        "conteos_iguales": not diferencias,
        "diferencias": diferencias,
        "restricciones_sin_validar": sin_validar,
        "almacen": {
            "objetos_en_respaldo": len(objetos),
            **cuenta,
            "artefactos_registrados": len(registrados),
            "artefactos_faltantes": faltantes,
        },
        "advertencias": advertencias,
        "segundos": {
            "verificar": round(verificado - inicio, 3),
            "almacen": round(objetos_listos - verificado, 3),
            "pg_restore": round(restaurado - objetos_listos, 3),
            "validar": round(time.monotonic() - restaurado, 3),
            "total": round(time.monotonic() - inicio, 3),
        },
    }
    base_valida = (
        not diferencias and sin_validar == 0 and revision == manifiesto["revision_esquema"]
    )
    if not base_valida or faltantes:
        informe["resultado"] = "INVALIDA"
    elif faltantes is None and registrados:
        informe["resultado"] = "VALIDADA_SIN_ALMACEN"
    else:
        informe["resultado"] = "VALIDADA"
    return informe


def _almacen_de(argumento: Path | None, sin_almacen: bool) -> Path | None:
    if sin_almacen:
        return None
    if argumento is not None:
        return argumento
    raiz = os.environ.get("MC_SOURCE_STORE_ROOT")
    return Path(raiz) if raiz else None


def main(argv: list[str] | None = None) -> int:
    argumentos = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = argumentos.add_subparsers(dest="orden", required=True)
    a = sub.add_parser("respaldar", help="Respalda la base y el almacen en un directorio nuevo.")
    a.add_argument("--destino", type=Path, default=Path("/respaldos"))
    a.add_argument("--dsn-file")
    a.add_argument("--almacen", type=Path, help="La raiz del almacen (MC_SOURCE_STORE_ROOT).")
    a.add_argument("--sin-almacen", action="store_true")
    v = sub.add_parser("verificar", help="Verifica un respaldo contra su manifiesto.")
    v.add_argument("respaldo", type=Path)
    r = sub.add_parser("restaurar", help="Restaura un respaldo verificado en una base vacia.")
    r.add_argument("respaldo", type=Path)
    r.add_argument("--dsn-file")
    r.add_argument("--almacen", type=Path, help="El almacen de destino (MC_SOURCE_STORE_ROOT).")
    r.add_argument("--sin-almacen", action="store_true")
    r.add_argument("--revision-esperada")
    r.add_argument("--permitir-otra-revision", action="store_true")
    r.add_argument("--informe", type=Path, help="Ademas de imprimirlo, escribe aqui el informe.")
    leidos = argumentos.parse_args(argv)
    try:
        if leidos.orden == "respaldar":
            directorio = respaldar(
                leer_dsn("MC_RESPALDO_DSN", leidos.dsn_file),
                leidos.destino,
                _almacen_de(leidos.almacen, leidos.sin_almacen),
            )
            manifiesto = json.loads((directorio / ARCHIVO_MANIFIESTO).read_text(encoding="utf-8"))
            print(
                json.dumps(
                    {
                        "resultado": "RESPALDADO",
                        "respaldo": str(directorio),
                        "revision_esquema": manifiesto["revision_esquema"],
                        "bytes": manifiesto["dump"]["bytes"],
                        "sha256": manifiesto["dump"]["sha256"],
                        "tablas": len(manifiesto["conteos"]),
                        "filas": sum(manifiesto["conteos"].values()),
                        "almacen": manifiesto["almacen"],
                        "duracion_segundos": manifiesto["duracion_segundos"],
                    },
                    indent=2,
                )
            )
        elif leidos.orden == "verificar":
            manifiesto, objetos = verificar(leidos.respaldo)
            print(
                json.dumps(
                    {
                        "resultado": "INTEGRO",
                        "respaldo": str(leidos.respaldo),
                        "sha256": manifiesto["dump"]["sha256"],
                        "objetos": len(objetos),
                    }
                )
            )
        else:
            informe = restaurar(
                leer_dsn("MC_RESTAURACION_DSN", leidos.dsn_file),
                leidos.respaldo,
                almacen=_almacen_de(leidos.almacen, leidos.sin_almacen),
                sin_almacen=leidos.sin_almacen,
                revision_esperada=leidos.revision_esperada,
                permitir_otra_revision=leidos.permitir_otra_revision,
            )
            texto = json.dumps(informe, indent=2, ensure_ascii=False) + "\n"
            if leidos.informe is not None:
                leidos.informe.write_text(texto, encoding="utf-8")
            print(texto, end="")
            if informe["resultado"] == "INVALIDA":
                return 1
    except Negado as error:
        print(json.dumps({"resultado": "NEGADO", "codigo": error.codigo, "mensaje": str(error)}))
        return 1
    except subprocess.CalledProcessError as error:
        print(
            json.dumps({"resultado": "ERROR", "codigo": "PROCESO_FALLO", "comando": error.cmd[0]})
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
