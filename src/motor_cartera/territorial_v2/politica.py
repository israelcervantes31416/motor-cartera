"""territorial-public-v2: la politica publica de territorial/v2, como nucleo puro.

Dada la geografia de una cuenta (geografia/v1) y una version de bases operativas:

1. Si la geografia no es confiable, no se asigna: un punto fuera de la referencia es
   FUERA_DE_COBERTURA, y cualquier otra geografia dudosa (sin coordenadas, incompletas, invalidas,
   en mas de un municipio o que discrepa de la fuente) es REVISION_GEOGRAFICA. Fail-closed: una
   ubicacion dudosa no se envia a campo.
2. Con geografia confiable, las bases elegibles son las ACTIVAS de la version cuya cobertura incluye
   el CVEGEO del municipio espacial de la cuenta. Ninguna: SIN_BASE_ELEGIBLE. Una base no es
   elegible por estar cerca, sino por cubrir el municipio.
3. Una elegible: se asigna esa.
4. Varias: se calcula la distancia geodesica del punto de la cuenta a cada una (PostGIS geography,
   sobre el elipsoide WGS 84, en metros, al centimetro) y se asigna la inequivocamente mas cercana.
   Si otra queda a no mas de la tolerancia de empate (100 m) de la mas cercana, son indistinguibles:
   AMBIGUA_BASE, y no se elige ninguna, ni por codigo, ni por nombre, ni por el orden de la base.

La tolerancia es parte de la politica y de su hash. No se calibro con resultados: es la
incertidumbre razonable de una coordenada observada y de la ubicacion de una base. La politica no
usa carga, capacidad, gestores, rutas, promesas, saldo, prioridad ni decision/v2: una base puede
recibir 10 o 100,000 cuentas, y eso no dice que pueda atenderlas (v0.12).

El SQL de `territorial_v2.ejecuciones` aplica esta regla por conjuntos; `asignar` es su espejo.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from motor_cartera.geografia.reglas import CONFIABLES, EstadoGeografia

VERSION_MOTOR = "territorial/v2"


class EstadoAsignacion(StrEnum):
    ASIGNADA = "ASIGNADA"
    REVISION_GEOGRAFICA = "REVISION_GEOGRAFICA"
    FUERA_DE_COBERTURA = "FUERA_DE_COBERTURA"
    SIN_BASE_ELEGIBLE = "SIN_BASE_ELEGIBLE"
    AMBIGUA_BASE = "AMBIGUA_BASE"


CONTEO_DE_ESTADO = {
    EstadoAsignacion.ASIGNADA: "asignadas",
    EstadoAsignacion.REVISION_GEOGRAFICA: "revision_geografica",
    EstadoAsignacion.SIN_BASE_ELEGIBLE: "sin_base",
    EstadoAsignacion.FUERA_DE_COBERTURA: "fuera_cobertura",
    EstadoAsignacion.AMBIGUA_BASE: "ambiguas",
}
"""La columna de EjecucionTerritorialV2 que cuenta cada estado."""


class MotivoAsignacion(StrEnum):
    GEOGRAFIA_NO_CONFIABLE = "GEOGRAFIA_NO_CONFIABLE"
    """Con `estado_geografia`: la geografia que no permitio asignar."""
    PUNTO_FUERA_DE_REFERENCIA = "PUNTO_FUERA_DE_REFERENCIA"
    SIN_BASE_QUE_CUBRA = "SIN_BASE_QUE_CUBRA"
    """Con `cvegeo`: ninguna base activa de la version cubre el municipio."""
    BASES_INACTIVAS = "BASES_INACTIVAS"
    """Con `bases`: las que cubren el municipio pero estan inactivas, y no son elegibles."""
    BASE_UNICA_ELEGIBLE = "BASE_UNICA_ELEGIBLE"
    """Con `base` y `distancia_m`."""
    BASE_MAS_CERCANA = "BASE_MAS_CERCANA"
    """Con `base`, `distancia_m`, `elegibles` y la siguiente (`siguiente`, `distancia_siguiente_m`):
    la diferencia es mayor que la tolerancia."""
    EMPATE_ENTRE_BASES = "EMPATE_ENTRE_BASES"
    """Con `bases` (las indistinguibles), `distancia_m` (la menor) y `tolerancia_m`."""


@dataclass(frozen=True)
class PoliticaTerritorial:
    """Una politica de territorial/v2, como datos: lo que entra en su hash."""

    version: str
    tolerancia_empate_m: Decimal
    geografia_confiable: tuple[str, ...] = field(
        default_factory=lambda: tuple(sorted(e.value for e in CONFIABLES))
    )

    def definicion(self) -> dict:
        return {
            "version": self.version,
            "motor": VERSION_MOTOR,
            "geografia_confiable": list(self.geografia_confiable),
            "fuera_de_referencia": EstadoAsignacion.FUERA_DE_COBERTURA.value,
            "geografia_dudosa": EstadoAsignacion.REVISION_GEOGRAFICA.value,
            "elegibles": "bases activas de la version cuya cobertura incluye el CVEGEO del "
            "municipio espacial",
            "distancia": "geodesica: ST_Distance sobre geography (elipsoide WGS 84), en metros, "
            "redondeada a 0.01",
            "criterio": "la elegible inequivocamente mas cercana",
            "tolerancia_empate_m": str(self.tolerancia_empate_m),
            "empate": "AMBIGUA_BASE: otra elegible a no mas de la tolerancia de la mas cercana; no "
            "se elige ninguna",
            "no_usa": [
                "carga",
                "capacidad",
                "gestores",
                "rutas",
                "promesas",
                "saldo",
                "prioridad",
                "decision/v2",
            ],
        }


PUBLIC_V2 = PoliticaTerritorial("territorial-public-v2", Decimal("100.00"))
POLITICAS = {PUBLIC_V2.version: PUBLIC_V2}


def hash_politica(politica: PoliticaTerritorial = PUBLIC_V2) -> str:
    """SHA-256 de la definicion canonica de la politica."""
    texto = json.dumps(politica.definicion(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def firma_inputs(
    *,
    corte_id,
    geografia_run_id,
    firma_geografia: str,
    bases_version_id,
    hash_configuracion: str,
    politica: PoliticaTerritorial = PUBLIC_V2,
) -> str:
    """El SHA-256 de las entradas de una ejecucion: el motor, la politica con su hash, el corte, la
    geografia con su firma y la version de bases con su hash. Las mismas entradas son la misma
    asignacion."""
    lineas = [
        VERSION_MOTOR,
        politica.version,
        hash_politica(politica),
        str(corte_id),
        str(geografia_run_id),
        firma_geografia,
        str(bases_version_id),
        hash_configuracion,
    ]
    return hashlib.sha256("\n".join(lineas).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Candidata:
    """Una base activa que cubre el municipio de la cuenta, con su distancia geodesica."""

    codigo: str
    distancia_m: Decimal


@dataclass(frozen=True)
class Asignacion:
    estado: EstadoAsignacion
    base: str | None
    distancia_m: Decimal | None
    motivos: list[dict]


def _metros(valor: Decimal) -> str:
    return f"{valor.quantize(Decimal('0.01'))}"


def asignar(
    estado_geografia: str,
    cvegeo: str | None,
    candidatas: list[Candidata],
    *,
    inactivas: Sequence[str] = (),
    politica: PoliticaTerritorial = PUBLIC_V2,
) -> Asignacion:
    """La regla de una cuenta: su geografia, el CVEGEO de su municipio espacial, las bases activas
    que lo cubren con su distancia, y las inactivas que tambien lo cubren."""
    if estado_geografia == EstadoGeografia.FUERA_DE_REFERENCIA:
        return Asignacion(
            EstadoAsignacion.FUERA_DE_COBERTURA,
            None,
            None,
            [{"codigo": MotivoAsignacion.PUNTO_FUERA_DE_REFERENCIA.value}],
        )
    if estado_geografia not in politica.geografia_confiable:
        return Asignacion(
            EstadoAsignacion.REVISION_GEOGRAFICA,
            None,
            None,
            [
                {
                    "codigo": MotivoAsignacion.GEOGRAFIA_NO_CONFIABLE.value,
                    "estado_geografia": estado_geografia,
                }
            ],
        )
    if not candidatas:
        motivos = [{"codigo": MotivoAsignacion.SIN_BASE_QUE_CUBRA.value, "cvegeo": cvegeo}]
        if inactivas:
            motivos.append(
                {"codigo": MotivoAsignacion.BASES_INACTIVAS.value, "bases": sorted(inactivas)}
            )
        return Asignacion(EstadoAsignacion.SIN_BASE_ELEGIBLE, None, None, motivos)
    orden = sorted(candidatas, key=lambda c: (c.distancia_m, c.codigo))
    primera = orden[0]
    if len(orden) == 1:
        return Asignacion(
            EstadoAsignacion.ASIGNADA,
            primera.codigo,
            primera.distancia_m,
            [
                {
                    "codigo": MotivoAsignacion.BASE_UNICA_ELEGIBLE.value,
                    "base": primera.codigo,
                    "distancia_m": _metros(primera.distancia_m),
                }
            ],
        )
    empatadas = sorted(
        c.codigo
        for c in orden
        if c.distancia_m - primera.distancia_m <= politica.tolerancia_empate_m
    )
    if len(empatadas) > 1:
        return Asignacion(
            EstadoAsignacion.AMBIGUA_BASE,
            None,
            None,
            [
                {
                    "codigo": MotivoAsignacion.EMPATE_ENTRE_BASES.value,
                    "bases": empatadas,
                    "distancia_m": _metros(primera.distancia_m),
                    "tolerancia_m": _metros(politica.tolerancia_empate_m),
                }
            ],
        )
    segunda = orden[1]
    return Asignacion(
        EstadoAsignacion.ASIGNADA,
        primera.codigo,
        primera.distancia_m,
        [
            {
                "codigo": MotivoAsignacion.BASE_MAS_CERCANA.value,
                "base": primera.codigo,
                "distancia_m": _metros(primera.distancia_m),
                "elegibles": len(orden),
                "siguiente": segunda.codigo,
                "distancia_siguiente_m": _metros(segunda.distancia_m),
            }
        ],
    )
