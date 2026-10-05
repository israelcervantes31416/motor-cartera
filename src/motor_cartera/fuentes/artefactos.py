"""Guardar un archivo recibido como artefacto fuente y registrarlo en la base, en el orden seguro.

El almacen (un sistema de archivos) y PostgreSQL no comparten una transaccion, y aqui no se finge lo
contrario. El orden es el unico que no puede dejar a la base mintiendo:

  1. el archivo se copia por bloques a un temporal del almacen, calculando su SHA-256 y su tamano;
  2. fsync, y se revisa su estructura contra el formato que declara su nombre;
  3. se mueve con una operacion atomica a su lugar por contenido: desde aqui ya es durable;
  4. en una transaccion de la base: el ArtefactoFuente y lo que cuelga de el (la corrida, su flujo y
     el trabajo de su ingesta, o la ingesta de pagos y su trabajo);
  5. se responde.

Si la base falla despues del paso 3, queda en el almacen un objeto que ninguna fila registra: un
huerfano. Es aceptable: el almacen es por contenido, asi que el huerfano no corrompe nada ni ocupa
dos veces si el mismo archivo vuelve a llegar, y una limpieza posterior lo puede quitar. Lo que no
puede pasar es lo contrario, que la base confirme una corrida cuyo archivo nunca fue durable, y este
orden lo impide: la fila se escribe despues del objeto, nunca antes.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from typing import BinaryIO
from uuid import uuid4

from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session, select

from motor_cartera.config import Config
from motor_cartera.db.modelos import ArtefactoFuente, ahora
from motor_cartera.fuentes.almacen import (
    ArtefactoCorrupto,
    ArtefactoFaltante,
    ArtefactoVacio,
    LocalContentAddressedStore,
    ObjetoGuardado,
    SourceArtifactStore,
)
from motor_cartera.fuentes.formatos import (
    CABEZA,
    MEDIA_TYPES,
    Formato,
    formato_por_extension,
    verificar_cabeza,
    verificar_estructura,
)


@dataclass(frozen=True)
class ArtefactoGuardado:
    """Un archivo ya durable en el almacen, con lo que la base necesita para registrarlo."""

    objeto: ObjetoGuardado
    formato: Formato
    nombre: str
    """El nombre con que llego, sin ruta."""


def almacen_de(config: Config) -> LocalContentAddressedStore:
    """El almacen de artefactos que dice la configuracion (MC_SOURCE_STORE_ROOT)."""
    return LocalContentAddressedStore(config.source_store_root)


def guardar_artefacto(
    almacen: SourceArtifactStore,
    origen: BinaryIO,
    nombre: str,
    *,
    limite_bytes: int | None = None,
) -> ArtefactoGuardado:
    """Pasos 1 a 3: guarda `origen` en el almacen, si su contenido es el formato de su nombre.

    Levanta FormatoNoSoportado si la extension no es de un formato que se reciba,
    FormatoNoCorresponde si los bytes no son de ese formato, ArtefactoVacio y
    ArtefactoDemasiadoGrande. En cualquiera de esos casos no queda ningun objeto: la revision de los
    primeros bytes se hace antes de copiar, y la de la estructura completa, sobre el temporal y
    antes de moverlo a su lugar.
    """
    formato = formato_por_extension(nombre)
    cabeza = origen.read(CABEZA)
    if not cabeza:
        raise ArtefactoVacio()
    verificar_cabeza(formato, cabeza, nombre)
    objeto = almacen.guardar(
        _Encadenado(cabeza, origen),
        limite_bytes=limite_bytes,
        validar=lambda ruta: verificar_estructura(ruta, formato, nombre),
    )
    return ArtefactoGuardado(objeto, formato, nombre)


def guardar_contenido(
    almacen: SourceArtifactStore, contenido: bytes, nombre: str
) -> ArtefactoGuardado:
    """Como guardar_artefacto, sobre bytes que ya estan en memoria."""
    return guardar_artefacto(almacen, io.BytesIO(contenido), nombre)


def registrar_artefacto(s: Session, guardado: ArtefactoGuardado) -> ArtefactoFuente:
    """Paso 4: la fila del artefacto, en la transaccion de quien llama; no confirma.

    Hay una fila por contenido. Si ese SHA-256 ya estaba registrado, devuelve esa fila, con el
    nombre y el formato de la primera vez: el nombre de cada subida lo conserva su corrida. Dos
    registros simultaneos del mismo contenido no chocan: el segundo espera al primero y lo reusa.
    """
    objeto = guardado.objeto
    s.execute(
        insert(ArtefactoFuente)
        .values(
            artifact_id=uuid4(),
            sha256=objeto.sha256,
            tamano_bytes=objeto.tamano_bytes,
            nombre_original=guardado.nombre[:255],
            formato=guardado.formato,
            media_type=MEDIA_TYPES[guardado.formato],
            storage_key=objeto.clave,
            creado_en=ahora(),
        )
        .on_conflict_do_nothing(index_elements=["sha256"])
    )
    return s.exec(select(ArtefactoFuente).where(ArtefactoFuente.sha256 == objeto.sha256)).one()


def leer_verificado(almacen: SourceArtifactStore, artefacto: ArtefactoFuente) -> bytes:
    """Los bytes de un artefacto, comprobados contra su SHA-256 y su tamano. Para los lectores que
    trabajan en memoria, como el de cartera/v1. ArtefactoFaltante o ArtefactoCorrupto si el almacen
    ya no tiene lo que la base dice que tiene."""
    with almacen.abrir(artefacto.sha256) as objeto:
        contenido = objeto.read()
    calculado = hashlib.sha256(contenido).hexdigest()
    if calculado != artefacto.sha256 or len(contenido) != artefacto.tamano_bytes:
        raise ArtefactoCorrupto(artefacto.sha256, calculado)
    return contenido


@dataclass(frozen=True)
class Auditoria:
    """Un artefacto registrado, revisado contra el almacen."""

    artefacto: ArtefactoFuente
    problema: str | None
    """None si el almacen tiene exactamente sus bytes; si no, que pasa."""


def auditar_artefactos(s: Session, almacen: SourceArtifactStore) -> list[Auditoria]:
    """Vuelve a firmar cada artefacto que la base registra: que el objeto exista y que sus bytes
    sean los de su SHA-256 y su tamano. Lee cada objeto por bloques; no modifica nada."""
    resultado = []
    for artefacto in s.exec(select(ArtefactoFuente).order_by(ArtefactoFuente.id)).all():
        try:
            almacen.verificar(artefacto.sha256, artefacto.tamano_bytes)
        except (ArtefactoFaltante, ArtefactoCorrupto) as exc:
            resultado.append(Auditoria(artefacto, str(exc)))
        else:
            resultado.append(Auditoria(artefacto, None))
    return resultado


class _Encadenado(io.RawIOBase):
    """Los primeros bytes, ya leidos para revisarlos, seguidos del resto del archivo: se copia una
    sola vez, sin volver atras en el origen, que puede no admitirlo."""

    def __init__(self, cabeza: bytes, resto: BinaryIO) -> None:
        super().__init__()
        self._cabeza = cabeza
        self._resto = resto

    def readable(self) -> bool:
        return True

    def read(self, n: int | None = -1) -> bytes:
        if not self._cabeza:
            return self._resto.read(n)
        if n is None or n < 0:
            datos, self._cabeza = self._cabeza + self._resto.read(), b""
            return datos
        datos, self._cabeza = self._cabeza[:n], self._cabeza[n:]
        return datos
