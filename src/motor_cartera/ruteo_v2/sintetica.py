"""Una red vial SINTETICA, diminuta y determinista, en OSM XML: calles inventadas sobre coordenadas
inventadas, para las pruebas, la prueba de humo y el CI, que no descargan nada de OpenStreetMap.

La red se dibuja en metros desde un origen que se declara, y se lleva a grados con una aproximacion
local (equirectangular): no representa ninguna calle, direccion ni lugar real, y sus nombres son
inventados. La prepara el mismo OSRM que una red real (extract, partition y customize con el perfil
car), asi que el CI prueba el snap, la matriz, la ruta y la conectividad con el motor de verdad:

- una cuadricula de 4 x 4 cruces cada 1,000 m: calles residenciales de doble sentido, una avenida
  (tertiary) en la primera fila y una calle de un solo sentido (hacia el norte) en la ultima
  columna, que hace la matriz asimetrica;
- un tramo aislado de tres nodos, 3,000 m al este de la cuadricula, que no se conecta con ella: un
  punto junto a el tiene snap, pero no camino a la base (RED_DESCONECTADA).

Son 19 nodos y 9 vias. El mismo origen produce los mismos bytes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

VERSION_RED_SINTETICA = "red-sintetica/v1"
METROS_POR_GRADO = 111_320.0
"""Una aproximacion local: los metros de un grado de latitud. La red es inventada; esto solo la
dibuja."""

ORIGEN_PRUEBAS = (Decimal("-98.2000000"), Decimal("19.0400000"))
"""Donde las pruebas dibujan la red cuando no declaran otro origen: un numero, no un lugar."""

PASO_M = 1_000
LADO = 4
AISLADO_X = (6_000, 6_500, 7_000)
AISLADO_Y = 1_500


@dataclass(frozen=True)
class Nodo:
    id: int
    x: int
    y: int


@dataclass(frozen=True)
class Via:
    id: int
    nodos: tuple[int, ...]
    highway: str
    nombre: str
    un_sentido: bool = False


def _septimo(valor: float) -> Decimal:
    return Decimal(repr(valor)).quantize(Decimal("0.0000001"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Origen:
    """Desde donde se dibuja la red: su esquina suroeste, en grados."""

    lon: Decimal
    lat: Decimal

    def grados(self, x: float, y: float) -> tuple[Decimal, Decimal]:
        """La longitud y la latitud de un punto a (x, y) metros del origen, a siete decimales."""
        lat = float(self.lat) + y / METROS_POR_GRADO
        lon = float(self.lon) + x / (METROS_POR_GRADO * math.cos(math.radians(float(self.lat))))
        return _septimo(lon), _septimo(lat)

    def coordenada(self, x: float, y: float) -> tuple[str, str]:
        """El punto como lo recibe un proveedor de ruteo: texto con seis decimales."""
        lon, lat = self.grados(x, y)
        seis = Decimal("0.000001")
        return (
            format(lon.quantize(seis, rounding=ROUND_HALF_UP), "f"),
            format(lat.quantize(seis, rounding=ROUND_HALF_UP), "f"),
        )


def nodos() -> list[Nodo]:
    """Los 16 cruces de la cuadricula (ids 1 a 16, por fila de sur a norte) y los 3 del tramo
    aislado (17 a 19)."""
    cruces = [
        Nodo(1 + fila * LADO + columna, columna * PASO_M, fila * PASO_M)
        for fila in range(LADO)
        for columna in range(LADO)
    ]
    aislados = [Nodo(17 + i, x, AISLADO_Y) for i, x in enumerate(AISLADO_X)]
    return cruces + aislados


def vias() -> list[Via]:
    filas = [
        Via(
            101 + fila,
            tuple(1 + fila * LADO + columna for columna in range(LADO)),
            "tertiary" if fila == 0 else "residential",
            "Avenida Sintetica" if fila == 0 else f"Calle Sintetica {fila}",
        )
        for fila in range(LADO)
    ]
    columnas = [
        Via(
            201 + columna,
            tuple(1 + fila * LADO + columna for fila in range(LADO)),
            "residential",
            f"Andador Sintetico {columna + 1}",
            un_sentido=columna == LADO - 1,
        )
        for columna in range(LADO)
    ]
    aislada = Via(301, (17, 18, 19), "residential", "Calle Sintetica Aislada")
    return [*filas, *columnas, aislada]


def osm_xml(origen: Origen) -> bytes:
    """La red en OSM XML 0.6, con los nodos en orden de id y las vias despues."""
    lineas = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<osm version="0.6" generator="motor-cartera {VERSION_RED_SINTETICA}">',
    ]
    for n in nodos():
        lon, lat = origen.grados(n.x, n.y)
        lineas.append(f'  <node id="{n.id}" version="1" lat="{lat}" lon="{lon}"/>')
    for v in vias():
        lineas.append(f'  <way id="{v.id}" version="1">')
        lineas.extend(f'    <nd ref="{ref}"/>' for ref in v.nodos)
        lineas.append(f'    <tag k="highway" v="{v.highway}"/>')
        lineas.append(f'    <tag k="name" v="{v.nombre}"/>')
        if v.un_sentido:
            lineas.append('    <tag k="oneway" v="yes"/>')
        lineas.append("  </way>")
    lineas.append("</osm>")
    return ("\n".join(lineas) + "\n").encode("utf-8")


def escribir(destino: str | Path, origen: Origen) -> Path:
    """Escribe la red en `destino` (un .osm) y devuelve la ruta."""
    ruta = Path(destino)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(osm_xml(origen))
    return ruta


# --- los puntos de la prueba de humo --------------------------------------------------------------

PUNTOS_DE_HUMO = (
    # (x, y) en metros desde el origen: junto a calles de la cuadricula, a menos de 200 m.
    (120, 40),
    (980, 150),
    (1_060, 990),
    (1_950, 60),
    (2_040, 1_120),
    (2_960, 1_980),
    (1_500, 2_030),
    (40, 2_900),
    (2_520, 2_960),
    (930, 1_480),
    (2_980, 560),
    (1_480, 30),
    (60, 1_400),
    (2_030, 2_500),
    (1_010, 2_480),
    (2_500, 990),
    # Uno a 800 m de la calle mas cercana: SNAP_EXCESIVO.
    (1_500, 3_800),
    # Uno junto al tramo aislado: snap de 50 m, sin camino a la base (RED_DESCONECTADA).
    (6_500, 1_550),
)
"""Los puntos de la muestra que la prueba de humo pone en el municipio de la red."""
BASE_DE_HUMO = (1_000, 1_000)
"""La base de ruteo de la prueba de humo: un cruce de la cuadricula."""
