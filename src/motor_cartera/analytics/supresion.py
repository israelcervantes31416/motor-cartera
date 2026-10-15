"""La privacidad de las celdas pequenas de analytics-public-v1: supresion primaria y complementaria.

Una celda es un valor de un conjunto de dimensiones dentro de un universo (COHORTE, GESTION, CAMPO o
RUTEO), con su poblacion: cuantas cuentas distintas de ese universo caen en ella en el horizonte. La
decision es por celda, no por periodo ni por metrica: una celda suprimida lo esta en todas sus
metricas y en todos sus periodos, y asi una serie no reconstruye lo que el acumulado oculta.

- Primaria: una celda de un conjunto distinto de TOTAL con menos de `minimo` cuentas.
- Complementaria: cada grupo es una ecuacion, su total es la suma de sus celdas, y ninguna puede
  quedar con una sola incognita. Con el total visible, no puede dejar una sola celda suprimida, ni
  dos o mas que juntas tengan menos de `minimo` cuentas: el total menos las visibles las revelaria.
  Si todas sus celdas estan suprimidas y aun asi no cumple (es la unica celda de su total), se
  suprime el total: un padre visible revela a su unica celda. Con el total suprimido, alguna de sus
  celdas tiene que estarlo: si todas se ven, sumarlas revela el total. Los grupos son cada conjunto
  contra el TOTAL de su universo, MUNICIPIO dentro de su ENTIDAD, un cruce dentro de cada uno de
  sus marginales y GESTOR_CAMPO dentro de su BASE. Mientras un grupo no cumpla, se suprime la celda
  visible con menos cuentas (el desempate es su dimension_hash), salvo que haya otra que no sea la
  unica celda de algun grupo: esa la revelaria su total, y no protege a nadie. Hasta que todos
  cumplan.

TOTAL no se suprime nunca: es lo que se publica de todo el universo. Con estas reglas ninguna
ecuacion de un grupo deja deducir una celda suprimida; no es una garantia contra cualquier
combinacion de sumas y restas entre conjuntos distintos.

Este modulo no sabe de la base: lo aplica el motor sobre las celdas, que son pocas.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from motor_cartera.analytics.catalogo import CONJUNTOS, Conjunto

PRIMARIA = "CELDA_MENOR_AL_MINIMO"
COMPLEMENTARIA = "SUPRESION_COMPLEMENTARIA"

Llave = tuple[str, str, str]
"""(universo, conjunto, dimension_hash): una celda."""


@dataclass(frozen=True)
class Celda:
    universo: str
    conjunto: str
    dimensiones: tuple[tuple[str, str], ...]
    """Sus pares (llave, valor), en el orden de las llaves."""
    dimension_hash: str
    poblacion: int

    @property
    def llave(self) -> Llave:
        return (self.universo, self.conjunto, self.dimension_hash)


@dataclass(frozen=True)
class Grupo:
    """Celdas que suman un total publicado: el TOTAL del universo (total None) o una celda."""

    total: Llave | None
    miembros: tuple[Llave, ...]


def _padre(conjunto: Conjunto, padre: Conjunto, dimensiones: dict[str, str]) -> dict[str, str]:
    """Las dimensiones de la celda del conjunto padre que contiene a esta."""
    if conjunto == Conjunto.MUNICIPIO and padre == Conjunto.ENTIDAD:
        return {"entidad": dimensiones["municipio"][:2]}
    return {clave: dimensiones[clave] for clave in CONJUNTOS[padre].claves}


def grupos(celdas: Iterable[Celda]) -> list[Grupo]:
    """Los grupos de la supresion complementaria, en un orden determinista."""
    por_llave: dict[tuple[str, str, tuple], Llave] = {}
    por_conjunto: dict[tuple[str, str], list[Celda]] = defaultdict(list)
    for c in celdas:
        por_llave[(c.universo, c.conjunto, tuple(sorted(c.dimensiones)))] = c.llave
        if c.conjunto != Conjunto.TOTAL:
            por_conjunto[(c.universo, c.conjunto)].append(c)
    resultado: list[Grupo] = []
    for (universo, conjunto), miembros in sorted(por_conjunto.items()):
        ordenados = sorted(miembros, key=lambda c: c.dimension_hash)
        resultado.append(Grupo(None, tuple(c.llave for c in ordenados)))
        for padre in CONJUNTOS[Conjunto(conjunto)].padres:
            hijos: dict[tuple, list[Llave]] = defaultdict(list)
            for c in ordenados:
                dimensiones = _padre(Conjunto(conjunto), padre, dict(c.dimensiones))
                hijos[tuple(sorted(dimensiones.items()))].append(c.llave)
            for clave_padre, llaves in sorted(hijos.items()):
                total = por_llave.get((universo, padre.value, clave_padre))
                if total is not None:
                    resultado.append(Grupo(total, tuple(llaves)))
    return resultado


def suprimir(celdas: Iterable[Celda], minimo: int) -> dict[Llave, str]:
    """Las celdas que no se exponen, con su motivo: primero las primarias, despues las
    complementarias, hasta que ningun grupo deje deducir una."""
    celdas = list(celdas)
    poblacion = {c.llave: c.poblacion for c in celdas}
    suprimidas: dict[Llave, str] = {
        c.llave: PRIMARIA for c in celdas if c.conjunto != Conjunto.TOTAL and c.poblacion < minimo
    }
    todos = grupos(celdas)
    unicas = {g.miembros[0] for g in todos if len(g.miembros) == 1}
    cambio = True
    while cambio:
        cambio = False
        for grupo in todos:
            elegida = _que_suprimir(grupo, suprimidas, poblacion, minimo, unicas)
            if elegida is not None:
                suprimidas[elegida] = COMPLEMENTARIA
                cambio = True
    return suprimidas


def _que_suprimir(
    grupo: Grupo,
    suprimidas: dict[Llave, str],
    poblacion: dict[Llave, int],
    minimo: int,
    unicas: set[Llave],
) -> Llave | None:
    """La celda que falta suprimir para que la ecuacion del grupo no revele ninguna, o None si ya
    no revela nada (o si lo unico que se podria suprimir es el TOTAL, que se publica siempre)."""
    visibles = sorted(
        (m for m in grupo.miembros if m not in suprimidas),
        key=lambda m: (m in unicas, poblacion[m], m[2]),
    )
    if grupo.total is not None and grupo.total in suprimidas:
        return visibles[0] if len(visibles) == len(grupo.miembros) else None
    if _cumple(grupo, suprimidas, poblacion, minimo):
        return None
    return visibles[0] if visibles else grupo.total


def _cumple(
    grupo: Grupo, suprimidas: dict[Llave, str], poblacion: dict[Llave, int], minimo: int
) -> bool:
    ocultas = [m for m in grupo.miembros if m in suprimidas]
    return not ocultas or (len(ocultas) >= 2 and sum(poblacion[m] for m in ocultas) >= minimo)


def violaciones(celdas: Iterable[Celda], suprimidas: dict[Llave, str], minimo: int) -> int:
    """Cuantas reglas rompe una supresion: celdas visibles de un desglose con menos de `minimo`
    cuentas; grupos con el total visible que dejan reconstruir una celda (salvo el TOTAL de un
    universo con todas sus celdas suprimidas: se publica siempre); y grupos con el total suprimido
    y todas sus celdas visibles, que lo revelan al sumarlas. Cero, o el motor no publica."""
    celdas = list(celdas)
    poblacion = {c.llave: c.poblacion for c in celdas}
    malas = sum(
        1
        for c in celdas
        if c.conjunto != Conjunto.TOTAL and c.llave not in suprimidas and c.poblacion < minimo
    )
    for grupo in grupos(celdas):
        visibles = [m for m in grupo.miembros if m not in suprimidas]
        if grupo.total is not None and grupo.total in suprimidas:
            malas += len(visibles) == len(grupo.miembros)
        elif not _cumple(grupo, suprimidas, poblacion, minimo):
            malas += bool(visibles) or grupo.total is not None
    return malas
