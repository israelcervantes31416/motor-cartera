"""Reconocer el sistema de coordenadas de una capa por su .prj, sin adivinar.

Un Shapefile declara su sistema de coordenadas en un .prj, en WKT (el de ESRI, en el INEGI). El
importador solo acepta los sistemas que sabe transformar y que puede reconocer por sus parametros,
no por su nombre: el nombre de un WKT cambia de un programa a otro, y sus parametros no. Uno que no
reconoce no se adivina: la importacion se rechaza.

Reconoce dos:

- EPSG:6372, Mexico ITRF2008 / LCC: Conica Conforme de Lambert con dos paralelos (17.5 y 29.5),
  latitud de origen 12, meridiano central -102, falso este 2,500,000 m y falso norte 0, sobre el
  elipsoide GRS80, en metros. Es la proyeccion del Marco Geoestadistico del INEGI.
- EPSG:4326, WGS 84 en grados: la de las capas sinteticas de las pruebas.

La transformacion la hace PostGIS (ST_Transform, con PROJ). Entre ITRF2008 y WGS 84 la diferencia es
de menos de un metro, muy por debajo de la precision de una coordenada observada.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

EPSG_LCC_MEXICO = 6372
EPSG_WGS84 = 4326

GRS80 = (6378137.0, 298.257222101)
WGS84 = (6378137.0, 298.257223563)
GRADO = math.pi / 180


class CrsNoReconocido(ValueError):
    """El .prj no declara un sistema que el importador sepa transformar."""


@dataclass
class Nodo:
    """Un nodo de un WKT: su palabra clave y sus argumentos, que son textos, numeros u otros
    nodos."""

    clave: str
    argumentos: list = field(default_factory=list)

    def hijos(self, clave: str) -> list[Nodo]:
        return [a for a in self.argumentos if isinstance(a, Nodo) and a.clave == clave]

    def hijo(self, clave: str) -> Nodo | None:
        encontrados = self.hijos(clave)
        return encontrados[0] if encontrados else None


_FICHA = re.compile(
    r"""\s*(?:
        "([^"]*)"                           # una cadena
        |([A-Za-z_][A-Za-z0-9_]*)           # una palabra clave
        |([-+]?[0-9.]+(?:[eE][-+]?\d+)?)    # un numero
        |([\[\](),])                        # un signo
    )""",
    re.VERBOSE,
)


def analizar(wkt: str) -> Nodo:
    """El arbol de un WKT. Levanta CrsNoReconocido si no es un WKT bien formado."""
    fichas: list[tuple[str, str]] = []
    posicion = 0
    texto = wkt.strip()
    while posicion < len(texto):
        encontrada = _FICHA.match(texto, posicion)
        if encontrada is None:
            cerca = texto[posicion:][:30]
            raise CrsNoReconocido(f"El .prj no es un WKT legible cerca de: {cerca!r}")
        cadena, palabra, numero, signo = encontrada.groups()
        if cadena is not None:
            fichas.append(("cadena", cadena))
        elif palabra is not None:
            fichas.append(("palabra", palabra))
        elif numero is not None:
            fichas.append(("numero", numero))
        else:
            fichas.append(("signo", signo))
        posicion = encontrada.end()
    nodo, siguiente = _nodo(fichas, 0)
    if siguiente != len(fichas):
        raise CrsNoReconocido("El .prj trae algo despues del WKT.")
    return nodo


def _nodo(fichas: list[tuple[str, str]], i: int) -> tuple[Nodo, int]:
    if i >= len(fichas) or fichas[i][0] != "palabra":
        raise CrsNoReconocido("El .prj no empieza con una palabra clave de WKT.")
    nodo = Nodo(fichas[i][1].upper())
    i += 1
    if i < len(fichas) and fichas[i] in (("signo", "["), ("signo", "(")):
        i += 1
        while True:
            if i >= len(fichas):
                raise CrsNoReconocido("El .prj tiene un corchete sin cerrar.")
            tipo, valor = fichas[i]
            if tipo == "cadena":
                nodo.argumentos.append(valor)
                i += 1
            elif tipo == "numero":
                nodo.argumentos.append(float(valor))
                i += 1
            elif tipo == "palabra":
                hijo, i = _nodo(fichas, i)
                nodo.argumentos.append(hijo)
            else:
                raise CrsNoReconocido(f"El .prj tiene un {valor!r} donde no corresponde.")
            if i < len(fichas) and fichas[i] == ("signo", ","):
                i += 1
                continue
            if i < len(fichas) and fichas[i] in (("signo", "]"), ("signo", ")")):
                return nodo, i + 1
            raise CrsNoReconocido("El .prj tiene un argumento sin separar.")
    return nodo, i


def _normalizar(nombre: str) -> str:
    return re.sub(r"[^a-z0-9]", "", nombre.lower())


def _cerca(a: float, b: float, tolerancia: float = 1e-9) -> bool:
    return math.isclose(a, b, rel_tol=tolerancia, abs_tol=tolerancia)


def _elipsoide(geogcs: Nodo) -> tuple[float, float] | None:
    datum = geogcs.hijo("DATUM")
    esferoide = None if datum is None else (datum.hijo("SPHEROID") or datum.hijo("ELLIPSOID"))
    if esferoide is None:
        return None
    numeros = [a for a in esferoide.argumentos if isinstance(a, float)]
    return (numeros[0], numeros[1]) if len(numeros) >= 2 else None


def _unidad(nodo: Nodo) -> float | None:
    unidad = nodo.hijo("UNIT")
    if unidad is None:
        return None
    numeros = [a for a in unidad.argumentos if isinstance(a, float)]
    return numeros[0] if numeros else None


@dataclass(frozen=True)
class Crs:
    """Un sistema reconocido: su codigo EPSG y como se reconocio."""

    epsg: int
    descripcion: str


def reconocer(wkt: str) -> Crs:
    """El sistema de coordenadas de un .prj, reconocido por sus parametros. Levanta CrsNoReconocido
    con lo que no cuadra."""
    raiz = analizar(wkt)
    if raiz.clave == "GEOGCS":
        return _geografico(raiz)
    if raiz.clave == "PROJCS":
        return _proyectado(raiz)
    raise CrsNoReconocido(f"El .prj declara un {raiz.clave}, y solo se aceptan GEOGCS y PROJCS.")


def _geografico(geogcs: Nodo) -> Crs:
    elipsoide = _elipsoide(geogcs)
    unidad = _unidad(geogcs)
    if (
        elipsoide is not None
        and _cerca(elipsoide[0], WGS84[0])
        and _cerca(elipsoide[1], WGS84[1])
        and unidad is not None
        and _cerca(unidad, GRADO, 1e-12)
    ):
        return Crs(EPSG_WGS84, "WGS 84, en grados (EPSG:4326)")
    raise CrsNoReconocido(
        "El .prj declara un sistema geografico que no es WGS 84 en grados: solo se aceptan "
        "EPSG:4326 y EPSG:6372."
    )


PARAMETROS_6372 = {
    "falseeasting": 2_500_000.0,
    "falsenorthing": 0.0,
    "centralmeridian": -102.0,
    "standardparallel1": 17.5,
    "standardparallel2": 29.5,
    "latitudeoforigin": 12.0,
}


def _proyectado(projcs: Nodo) -> Crs:
    proyeccion = projcs.hijo("PROJECTION")
    nombre = "" if proyeccion is None else _normalizar(str(proyeccion.argumentos[0]))
    geogcs = projcs.hijo("GEOGCS")
    elipsoide = None if geogcs is None else _elipsoide(geogcs)
    parametros = {
        _normalizar(str(p.argumentos[0])): p.argumentos[1]
        for p in projcs.hijos("PARAMETER")
        if len(p.argumentos) >= 2 and isinstance(p.argumentos[1], float)
    }
    problemas = []
    if nombre not in ("lambertconformalconic", "lambertconformalconic2sp"):
        problemas.append(f"la proyeccion es {nombre or 'ninguna'}, no Lambert Conformal Conic")
    if elipsoide is None or not (_cerca(elipsoide[0], GRS80[0]) and _cerca(elipsoide[1], GRS80[1])):
        problemas.append("el elipsoide no es GRS80")
    for clave, esperado in PARAMETROS_6372.items():
        valor = parametros.get(clave)
        if valor is None or not _cerca(valor, esperado):
            problemas.append(f"{clave} es {valor}, no {esperado}")
    unidad = _unidad(projcs)
    if unidad is None or not _cerca(unidad, 1.0):
        problemas.append("la unidad no es el metro")
    if problemas:
        raise CrsNoReconocido(
            "El .prj declara una proyeccion que no es EPSG:6372 (Mexico ITRF2008 / LCC): "
            + "; ".join(problemas)
            + "."
        )
    return Crs(EPSG_LCC_MEXICO, "Mexico ITRF2008 / LCC, en metros (EPSG:6372)")
