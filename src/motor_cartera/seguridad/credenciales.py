"""Las claves de los servicios y los secretos del gateway.

Una clave de servicio es una credencial interna: la usa un proceso (la automatizacion, una prueba de
humo dentro de la red), nunca un navegador, y vale solo para sus permisos. La API guarda su SHA-256,
no la clave, y compara en tiempo constante. Rotar una clave es agregar la nueva, cambiar el
cliente y borrar la vieja.

El secreto del gateway es como se presenta el edge ante la API: sin el, un token de persona no vale
en produccion, aunque sea legitimo, porque no llego por el edge.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass

from motor_cartera.seguridad.permisos import Permiso, permisos_de

NOMBRE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
LARGO_MINIMO = 32


@dataclass(frozen=True)
class ClaveDeServicio:
    nombre: str
    digest: bytes
    permisos: frozenset[Permiso]


def digest_de(clave: str) -> bytes:
    return hashlib.sha256(clave.encode()).digest()


def leer_claves(texto: str) -> tuple[ClaveDeServicio, ...]:
    """Las claves de `nombre sha256hex permisos` (una por linea; `#` comenta). Un error dice la
    linea, nunca su contenido."""
    claves: list[ClaveDeServicio] = []
    vistos: set[bytes] = set()
    for numero, linea in enumerate(texto.splitlines(), start=1):
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue
        partes = linea.split()
        if len(partes) != 3:
            raise ValueError(f"la linea {numero} no es `nombre sha256 permisos`.")
        nombre, digest, permisos = partes
        if not NOMBRE.match(nombre):
            raise ValueError(f"la linea {numero}: el nombre debe ser [a-z0-9-], de 1 a 40.")
        if not DIGEST.match(digest):
            raise ValueError(f"la linea {numero}: el hash debe ser un SHA-256 en hex minuscula.")
        if bytes.fromhex(digest) in vistos:
            raise ValueError(f"la linea {numero} repite una clave.")
        try:
            conjunto = permisos_de(permisos)
        except ValueError as error:
            raise ValueError(f"la linea {numero}: {error}") from None
        if not conjunto:
            raise ValueError(f"la linea {numero} no da ningun permiso.")
        vistos.add(bytes.fromhex(digest))
        claves.append(ClaveDeServicio(nombre, bytes.fromhex(digest), conjunto))
    return tuple(claves)


def buscar_clave(claves: tuple[ClaveDeServicio, ...], presentada: str) -> ClaveDeServicio | None:
    """La clave de servicio que corresponde a `presentada`, o None. Compara con todas, sin salir al
    encontrarla: el tiempo no dice cuantas hay ni cual coincidio."""
    digest = digest_de(presentada)
    encontrada = None
    for clave in claves:
        if hmac.compare_digest(digest, clave.digest):
            encontrada = clave
    return encontrada


def leer_secretos_gateway(texto: str) -> tuple[bytes, ...]:
    """Los secretos del gateway, uno por linea. Cada uno de al menos LARGO_MINIMO caracteres."""
    secretos = tuple(linea.strip().encode() for linea in texto.splitlines() if linea.strip())
    if not secretos:
        raise ValueError("no hay ningun secreto.")
    if any(len(s) < LARGO_MINIMO for s in secretos):
        raise ValueError(f"cada secreto debe tener al menos {LARGO_MINIMO} caracteres.")
    return secretos


def gateway_valido(secretos: tuple[bytes, ...], presentado: str | None) -> bool:
    """Si `presentado` es uno de los secretos del gateway, comparando con todos en tiempo
    constante."""
    if not presentado:
        return False
    valido = False
    for secreto in secretos:
        if hmac.compare_digest(presentado.encode(), secreto):
            valido = True
    return valido
