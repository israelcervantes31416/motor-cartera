"""Coordenadas sinteticas para LATITUD y LONGITUD de cartera/v2, en proporciones explicitas.

Sin una muestra de puntos el generador deja las dos columnas vacias, como siempre. Con una (ver
`geografia.muestra`), cada cuenta cae en una categoria, con la proporcion que se pide y por un hash
de su cliente y la semilla, asi que una cuenta conserva su coordenada en todos sus cortes:

- validas: un punto de la muestra dentro de su municipio (el que resuelven ESTADO_CTE y
  POBLACION_CTE);
- faltantes: las dos celdas vacias;
- incompletas: la latitud de un punto valido y la longitud vacia;
- invalidas: (0, 0), la unica coordenada invalida que el contrato de cartera/v2 deja pasar (una
  fuera de rango o que no es un numero la rechaza el contrato, y la cuenta no llega al corte);
- fuera: un punto de la muestra fuera de la referencia;
- discrepantes: un punto de otro municipio, que no es el de la fuente.

Las proporciones no se ajustan para que el resultado "se vea real": son parametros de una prueba, y
el manifiesto del escenario dice cuales se usaron. Ninguna coordenada es de una persona.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

import numpy as np
import pyarrow as pa

VERSION_COORDENADAS = "coordenadas-sinteticas/v1"
CATEGORIAS = ("validas", "faltantes", "incompletas", "invalidas", "fuera", "discrepantes")


@dataclass(frozen=True)
class Proporciones:
    """La fraccion de cuentas de cada categoria. Suman 1."""

    validas: float = 0.90
    faltantes: float = 0.04
    incompletas: float = 0.01
    invalidas: float = 0.01
    fuera: float = 0.02
    discrepantes: float = 0.02

    def __post_init__(self) -> None:
        valores = [getattr(self, c) for c in CATEGORIAS]
        if any(v < 0 for v in valores) or abs(sum(valores) - 1) > 1e-9:
            raise ValueError(
                "Las proporciones de las coordenadas son fracciones no negativas que suman 1: "
                + ", ".join(f"{c}={getattr(self, c)}" for c in CATEGORIAS)
                + "."
            )

    @classmethod
    def de_texto(cls, texto: str) -> Proporciones:
        """De `validas=0.9,faltantes=0.04,...`: las que no se nombran valen 0."""
        valores = dict.fromkeys(CATEGORIAS, 0.0)
        for parte in texto.split(","):
            if not parte.strip():
                continue
            nombre, _, valor = parte.partition("=")
            nombre = nombre.strip()
            if nombre not in valores:
                raise ValueError(f"No hay una categoria de coordenadas {nombre!r}: {CATEGORIAS}.")
            valores[nombre] = float(valor)
        return cls(**valores)


@dataclass(frozen=True)
class MuestraDePuntos:
    """Los puntos de una referencia, por CVEGEO, y los de fuera de ella."""

    municipios: dict[str, list[tuple[float, float]]]
    fuera: list[tuple[float, float]]
    sha256: str
    """El SHA-256 del archivo de la muestra: lo que el manifiesto registra."""
    datos: dict

    @classmethod
    def leer(cls, ruta: str | Path) -> MuestraDePuntos:
        contenido = Path(ruta).read_bytes()
        datos = json.loads(contenido)
        return cls(
            municipios={c: [tuple(p) for p in ps] for c, ps in datos["municipios"].items()},
            fuera=[tuple(p) for p in datos["fuera"]],
            sha256=hashlib.sha256(contenido).hexdigest(),
            datos={k: v for k, v in datos.items() if k not in ("municipios", "fuera")},
        )


@dataclass(frozen=True)
class Coordenadas:
    """Lo que el generador necesita para dar coordenadas: la muestra y las proporciones."""

    muestra: MuestraDePuntos
    proporciones: Proporciones = Proporciones()

    def manifiesto(self) -> dict:
        return {
            "version": VERSION_COORDENADAS,
            "muestra_sha256": self.muestra.sha256,
            "geografia_version_id": self.muestra.datos.get("geografia_version_id"),
            "sha256_referencia": self.muestra.datos.get("sha256_referencia"),
            "proporciones": asdict(self.proporciones),
        }


def _texto(valores: np.ndarray, presentes: np.ndarray) -> pa.Array:
    return pa.array(
        [f"{v:.6f}" if p else None for v, p in zip(valores, presentes, strict=True)],
        type=pa.string(),
    )


def asignar(
    cvegeo: np.ndarray, azar, coordenadas: Coordenadas
) -> tuple[pa.Array, pa.Array, np.ndarray]:
    """LATITUD y LONGITUD como texto, y la categoria de cada cuenta. `cvegeo` es el municipio que
    resuelve la fuente; `azar(atributo)` da un numero en [0, 1) por cuenta, fijo por cliente."""
    n = len(cvegeo)
    p = coordenadas.proporciones
    acumulado = np.cumsum([getattr(p, c) for c in CATEGORIAS])
    categoria = np.minimum(
        np.searchsorted(acumulado, azar("geo_categoria"), side="right"), len(CATEGORIAS) - 1
    )
    elegido = azar("geo_punto")
    otro = azar("geo_otro")
    muestra = coordenadas.muestra
    claves = sorted(muestra.municipios)
    lugar = {c: i for i, c in enumerate(claves)}
    latitud = np.zeros(n)
    longitud = np.zeros(n)
    con_latitud = np.zeros(n, dtype=bool)
    con_longitud = np.zeros(n, dtype=bool)
    for i in range(n):
        nombre = CATEGORIAS[categoria[i]]
        if nombre == "faltantes":
            continue
        if nombre == "invalidas":
            con_latitud[i] = con_longitud[i] = True
            continue
        if nombre == "fuera":
            x, y = muestra.fuera[int(elegido[i] * len(muestra.fuera))]
        else:
            fuente = str(cvegeo[i])
            if nombre == "discrepantes" and len(claves) > 1:
                desde = lugar.get(fuente, -1)
                salto = 1 + int(otro[i] * (len(claves) - 1))
                fuente = claves[(desde + salto) % len(claves)]
            puntos = muestra.municipios.get(fuente)
            if not puntos:
                continue  # su municipio no esta en la referencia: sin coordenadas
            x, y = puntos[int(elegido[i] * len(puntos))]
        latitud[i], longitud[i] = y, x
        con_latitud[i] = True
        con_longitud[i] = nombre != "incompletas"
    return (
        _texto(latitud, con_latitud),
        _texto(longitud, con_longitud),
        np.array([CATEGORIAS[c] for c in categoria]),
    )


def contar(categorias: np.ndarray) -> dict[str, int]:
    return {c: int((categorias == c).sum()) for c in CATEGORIAS}


assert tuple(f.name for f in fields(Proporciones)) == CATEGORIAS
