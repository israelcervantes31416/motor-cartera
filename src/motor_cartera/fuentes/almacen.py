"""El almacen de artefactos fuente: los archivos tal como llegaron, guardados por su contenido.

Un artefacto fuente es la evidencia de una ingesta: los bytes exactos que se recibieron. Se guardan
una vez y no se borran ni se modifican. Su identidad fisica es su SHA-256, no su nombre: dos subidas
del mismo archivo con nombres distintos son un solo objeto, y dos archivos distintos con el mismo
nombre son dos objetos. El nombre original es metadata, y vive en la base.

`SourceArtifactStore` es lo que la ingesta necesita de un almacen; `LocalContentAddressedStore` lo
implementa sobre un directorio local:

    <raiz>/
      sha256/ab/abcdef...   cada objeto, de solo lectura, en el directorio de sus dos primeros
                            caracteres para que ninguno crezca sin limite
      tmp/                  escrituras en curso: nunca son objetos, y nadie las lee

Guardar es streaming: se copia por bloques a un temporal dentro de la misma raiz, calculando el
SHA-256 y el tamano mientras se copia, se hace fsync y solo entonces se mueve con os.replace, que es
atomico dentro de un mismo sistema de archivos. Un objeto existe completo o no existe: una escritura
interrumpida deja, a lo mas, un temporal en tmp/. Si el objeto ya existia, el temporal se descarta:
guardar el mismo contenido dos veces es idempotente y no duplica bytes.

El almacen no sabe nada de la base. El orden seguro (primero el objeto durable, despues la fila que
lo registra) lo sigue quien lo usa; ver `artefactos`.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import stat
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

log = logging.getLogger(__name__)

BLOQUE = 1 << 20
"""Se copia y se firma de a 1 MiB: un archivo de cientos de MiB nunca esta entero en memoria."""

PATRON_SHA256 = re.compile(r"[0-9a-f]{64}")


class ErrorDeAlmacen(Exception):
    """Algo que el almacen no puede hacer con un artefacto."""


class ArtefactoVacio(ErrorDeAlmacen):
    """Un archivo sin bytes no es evidencia de nada: no se guarda."""

    def __init__(self) -> None:
        super().__init__("El archivo llego vacio: no hay nada que guardar.")


class ArtefactoDemasiadoGrande(ErrorDeAlmacen):
    """El archivo paso del tope mientras se copiaba; no se guardo nada."""

    def __init__(self, limite_bytes: int) -> None:
        super().__init__(f"El archivo pasa del tope de {limite_bytes:,} bytes.")
        self.limite_bytes = limite_bytes


class ArtefactoFaltante(ErrorDeAlmacen):
    """La base registra un artefacto que el almacen no tiene."""

    def __init__(self, sha256: str) -> None:
        super().__init__(f"El almacen no tiene el artefacto {sha256}.")
        self.sha256 = sha256


class ArtefactoCorrupto(ErrorDeAlmacen):
    """Los bytes guardados ya no son los que firmo su SHA-256."""

    def __init__(self, sha256: str, calculado: str) -> None:
        super().__init__(
            f"El artefacto {sha256} esta danado: sus bytes ahora tienen el SHA-256 {calculado}."
        )
        self.sha256 = sha256
        self.calculado = calculado


@dataclass(frozen=True)
class ObjetoGuardado:
    """Lo que el almacen guardo, o ya tenia."""

    sha256: str
    tamano_bytes: int
    clave: str
    """La storage_key: donde vive el objeto en el almacen. Sale del SHA-256, nunca del nombre."""
    nuevo: bool
    """False si el almacen ya tenia ese contenido: no se escribio ningun byte mas."""


def clave_de(sha256: str) -> str:
    """La storage_key de un contenido: `sha256/ab/abcdef...`. Es una ruta relativa con `/`, igual
    en cualquier sistema operativo y en cualquier almacen que se agregue despues."""
    return f"sha256/{_validar(sha256)[:2]}/{sha256}"


def _validar(sha256: str) -> str:
    if not isinstance(sha256, str) or not PATRON_SHA256.fullmatch(sha256):
        raise ValueError(f"No es un SHA-256 en hexadecimal: {sha256!r}.")
    return sha256


class SourceArtifactStore(Protocol):
    """Lo que la ingesta necesita de un almacen de artefactos fuente."""

    def guardar(
        self,
        origen: BinaryIO,
        *,
        limite_bytes: int | None = None,
        validar: Callable[[Path], None] | None = None,
    ) -> ObjetoGuardado:
        """Copia `origen` hasta agotarlo y lo guarda por su contenido. Idempotente. `validar` revisa
        la copia completa antes de que sea un objeto: si levanta, no se guarda nada."""
        ...

    def abrir(self, sha256: str) -> BinaryIO:
        """El objeto, para leerlo desde el principio. ArtefactoFaltante si no esta."""
        ...

    def existe(self, sha256: str) -> bool: ...

    def verificar(self, sha256: str, tamano_bytes: int | None = None) -> None:
        """Vuelve a firmar el objeto. ArtefactoFaltante o ArtefactoCorrupto si no es el que era."""
        ...


class LocalContentAddressedStore:
    """El almacen sobre un directorio local. La raiz se crea al primer uso."""

    def __init__(self, raiz: str | os.PathLike[str]) -> None:
        self.raiz = Path(raiz)

    def guardar(
        self,
        origen: BinaryIO,
        *,
        limite_bytes: int | None = None,
        validar: Callable[[Path], None] | None = None,
    ) -> ObjetoGuardado:
        temporales = self.raiz / "tmp"
        temporales.mkdir(parents=True, exist_ok=True)
        descriptor, nombre = tempfile.mkstemp(prefix="objeto-", suffix=".parcial", dir=temporales)
        temporal = Path(nombre)
        try:
            firma = hashlib.sha256()
            tamano = 0
            with os.fdopen(descriptor, "wb") as destino:
                while bloque := origen.read(BLOQUE):
                    tamano += len(bloque)
                    if limite_bytes is not None and tamano > limite_bytes:
                        raise ArtefactoDemasiadoGrande(limite_bytes)
                    firma.update(bloque)
                    destino.write(bloque)
                destino.flush()
                os.fsync(destino.fileno())
            if tamano == 0:
                raise ArtefactoVacio()
            if validar is not None:
                validar(temporal)
            return self._publicar(temporal, firma.hexdigest(), tamano)
        finally:
            # Si se publico, el temporal ya no existe; si no, nunca fue un objeto.
            temporal.unlink(missing_ok=True)

    def abrir(self, sha256: str) -> BinaryIO:
        ruta = self.ruta(sha256)
        try:
            return ruta.open("rb")
        except FileNotFoundError:
            raise ArtefactoFaltante(sha256) from None

    def existe(self, sha256: str) -> bool:
        return self.ruta(sha256).is_file()

    def verificar(self, sha256: str, tamano_bytes: int | None = None) -> None:
        firma = hashlib.sha256()
        leidos = 0
        with self.abrir(sha256) as objeto:
            while bloque := objeto.read(BLOQUE):
                leidos += len(bloque)
                firma.update(bloque)
        calculado = firma.hexdigest()
        if calculado != sha256 or (tamano_bytes is not None and leidos != tamano_bytes):
            raise ArtefactoCorrupto(sha256, calculado)

    def ruta(self, sha256: str) -> Path:
        """Donde vive el objeto en el disco. Es interna: no sale por la API."""
        return self.raiz.joinpath(*clave_de(sha256).split("/"))

    @contextmanager
    def como_archivo(self, sha256: str) -> Iterator[Path]:
        """La ruta del objeto, para las bibliotecas que leen de un archivo y no de un flujo. El
        objeto es de solo lectura: quien la usa no lo puede modificar."""
        ruta = self.ruta(sha256)
        if not ruta.is_file():
            raise ArtefactoFaltante(sha256)
        yield ruta

    def _publicar(self, temporal: Path, sha256: str, tamano: int) -> ObjetoGuardado:
        """Mueve el temporal a su lugar, si el contenido no estaba ya."""
        final = self.ruta(sha256)
        clave = clave_de(sha256)
        if final.is_file():
            if final.stat().st_size == tamano:
                return ObjetoGuardado(sha256, tamano, clave, nuevo=False)
            # El mismo SHA-256 con otro tamano solo puede ser un objeto danado: se repone con la
            # copia nueva, que si es la que dice su firma.
            log.warning("el artefacto %s esta danado en el almacen; se repone", sha256)
            os.chmod(final, stat.S_IRUSR | stat.S_IWUSR)
        final.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(temporal, final)
        except OSError:
            # Otra escritura del mismo contenido gano la carrera: su objeto es igual a este.
            if final.is_file() and final.stat().st_size == tamano:
                return ObjetoGuardado(sha256, tamano, clave, nuevo=False)
            raise
        os.chmod(final, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        _sincronizar_directorio(final.parent)
        return ObjetoGuardado(sha256, tamano, clave, nuevo=True)


def _sincronizar_directorio(directorio: Path) -> None:
    """Hace durable la entrada del objeto en su directorio. Windows no abre directorios para esto,
    y ahi NTFS registra el renombrado en su bitacora."""
    if os.name == "nt":
        return
    descriptor = os.open(directorio, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
