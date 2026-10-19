"""Los permisos, los roles y el principal de una peticion.

La API autoriza por permisos, nunca por el nombre de un rol: un rol (VIEWER, AUDITOR, OPERATOR,
ADMIN) es solo un nombre para un conjunto de permisos, que se resuelve al leer la configuracion.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


class Permiso(StrEnum):
    LEER = "mc.read"
    """Leer lo publicado: las pantallas de la app y las rutas GET de los motores."""
    AUDITAR = "mc.audit"
    """Leer la evidencia y el linaje: registros rechazados, archivos fuente, observaciones crudas y
    los trabajos de la cola."""
    EJECUTAR = "mc.execute"
    """Pedir trabajo: ingerir, ejecutar un motor, registrar un evento del lifecycle."""
    CONFIGURAR = "mc.configure"
    """Publicar configuracion operacional: bases, configuraciones de campo y de optimizacion."""


ROLES: dict[str, frozenset[Permiso]] = {
    "VIEWER": frozenset({Permiso.LEER}),
    "AUDITOR": frozenset({Permiso.LEER, Permiso.AUDITAR}),
    "OPERATOR": frozenset({Permiso.LEER, Permiso.EJECUTAR}),
    "ADMIN": frozenset(Permiso),
}
"""El mapeo conceptual de roles a permisos (docs/security.md)."""

GRUPO = re.compile(r"^[A-Za-z0-9_.:@/-]{1,128}$")


def permisos_de(texto: str) -> frozenset[Permiso]:
    """Los permisos de una lista separada por comas o espacios: cada elemento es un permiso o un
    rol.
    Un elemento desconocido es un error: un permiso mal escrito no debe pasar en silencio."""
    permisos: set[Permiso] = set()
    for elemento in re.split(r"[,\s]+", texto.strip()):
        if not elemento:
            continue
        if elemento in ROLES:
            permisos |= ROLES[elemento]
            continue
        try:
            permisos.add(Permiso(elemento))
        except ValueError:
            raise ValueError(
                f"{elemento!r} no es un permiso ({', '.join(p.value for p in Permiso)}) ni un rol "
                f"({', '.join(ROLES)})."
            ) from None
    return frozenset(permisos)


def mapa_de_grupos(texto: str | None) -> dict[str, frozenset[Permiso]]:
    """El mapa de grupos del IdP a permisos, de `grupo=PERMISO|ROL,...;otro=...`. Vacio sin
    texto."""
    mapa: dict[str, frozenset[Permiso]] = {}
    if not texto:
        return mapa
    for entrada in texto.split(";"):
        entrada = entrada.strip()
        if not entrada:
            continue
        grupo, separador, permisos = entrada.partition("=")
        grupo = grupo.strip()
        if not separador or not GRUPO.match(grupo):
            raise ValueError(f"cada entrada es grupo=permisos; {grupo or entrada!r} no lo es.")
        if grupo in mapa:
            raise ValueError(f"el grupo {grupo!r} aparece dos veces.")
        mapa[grupo] = permisos_de(permisos)
        if not mapa[grupo]:
            raise ValueError(f"el grupo {grupo!r} no da ningun permiso.")
    return mapa


def permisos_de_reclamos(
    reclamos: dict, mapa: dict[str, frozenset[Permiso]], claim_grupos: str
) -> frozenset[Permiso]:
    """Los permisos de un token: sus scopes (`scope`, separado por espacios, RFC 9068; o `scp`,
    texto o lista) que son permisos de esta API, y los de sus grupos segun el mapa. Lo que no es un
    permiso conocido se ignora: un scope de otra API no da nada aqui."""
    conocidos = {p.value: p for p in Permiso}
    nombres: list[str] = []
    for claim in ("scope", "scp"):
        valor = reclamos.get(claim)
        if isinstance(valor, str):
            nombres += valor.split()
        elif isinstance(valor, list):
            nombres += [v for v in valor if isinstance(v, str)]
    permisos = {conocidos[n] for n in nombres if n in conocidos}
    grupos = reclamos.get(claim_grupos)
    if isinstance(grupos, str):
        grupos = [grupos]
    if isinstance(grupos, list):
        for grupo in grupos:
            if isinstance(grupo, str):
                permisos |= mapa.get(grupo, frozenset())
    return frozenset(permisos)


@dataclass(frozen=True)
class Principal:
    """Quien hace una peticion, ya autenticado."""

    tipo: Literal["USUARIO", "SERVICIO"]
    actor: str
    """Un seudonimo estable: `usr:<16 hex>` (un HMAC del emisor y el sub, nunca el sub) o
    `svc:<nombre>` (el nombre declarado de la clave de servicio)."""
    permisos: frozenset[Permiso]

    def puede(self, permiso: Permiso) -> bool:
        return permiso in self.permisos


def seudonimo(clave: bytes, emisor: str, sujeto: str) -> str:
    """El actor de una persona para la auditoria: un HMAC-SHA256 del emisor y su `sub` con una clave
    propia, recortado a 64 bits. Correlaciona las peticiones de una persona sin guardar su `sub`, y
    sin la clave no se puede revertir ni recalcular."""
    digest = hmac.new(clave, f"{emisor}\n{sujeto}".encode(), hashlib.sha256).hexdigest()
    return f"usr:{digest[:16]}"
