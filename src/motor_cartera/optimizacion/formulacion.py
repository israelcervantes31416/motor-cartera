"""El modelo matematico de optimization/v1, puro: no sabe de la base.

Recibe el universo ya resuelto (por cuenta, en orden canonico de cuenta_id: su valor de oportunidad
en centavos, su p_contacto en unidades de 1e-8 y sus pertenencias a cada grupo de cobertura; los
pares elegibles cuenta-pool; los pools con su capacidad efectiva y sus unidades; las restricciones
declaradas) y devuelve el plan optimo con su certificado, o por que no hay uno.

Formulacion (docs/modelo_optimizacion.md):

    x[i,p] en {0,1} por cada par elegible;  z[i] = sum_p x[i,p]
    sum_p x[i,p] <= 1                       para cada cuenta con dos o mas pares
    sum_i x[i,p] <= capacidad[p]            para cada pool
    sum_i z[i] <= max_cuentas_atendidas     si se declara
    sum_ip unidades[p] x[i,p] <= max_unidades_recurso   si se declara
    MIN_CUENTAS / MAX_CUENTAS / MIN y MAX_PARTICIPACION_BPS sobre z, lineales y enteras

    nivel 1: max sum_i valor[i] z[i]
    nivel 2: max sum_i contacto[i] z[i]             con el nivel 1 fijo en su optimo
    nivel 3: min sum_ip unidades[p] x[i,p]          con los niveles 1 y 2 fijos
    nivel 4: min sum_ip (i (pools + 1) + (n - i) p) x[i,p]   con los niveles 1, 2 y 3 fijos

Cada etapa es un MILP de scipy.optimize.milp (HiGHS) sobre una matriz dispersa. Se publica solo si
cada una termina OPTIMAL y su solucion, en enteros, cumple cada restriccion y reproduce exactamente
los objetivos fijados.

La reduccion exacta: dos cuentas con la misma firma de restricciones (los mismos pools elegibles y
las mismas pertenencias) tienen las mismas columnas salvo el objetivo. Si A va antes que B en el
orden canonico de su clase (valor desc, contacto desc, cuenta asc), cualquier solucion factible que
use B y no A puede usar A en su lugar sin cambiar ninguna restriccion y sin empeorar ningun nivel; y
ninguna solucion factible usa mas de u[s] cuentas de la clase. Basta retener las primeras u[s].
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

import numpy as np
import scipy.sparse as sp

from motor_cartera.optimizacion import solver as solver_
from motor_cartera.optimizacion.politica import (
    BPS,
    MINIMOS,
    PARTICIPACIONES,
    PUBLIC_V1,
    Dimension,
    EstadoCuenta,
    EstadoSolver,
    Motivo,
    PoliticaOptimizacion,
    TipoRestriccion,
)

SIN_POOL = -1
"""La asignacion de una cuenta que no quedo en ningun pool."""
INFINITO = np.inf

Resolvedor = Callable[..., solver_.ResultadoSolver]
"""Lo que resuelve una etapa: solver.resolver, o el de una prueba."""


class FueraDeRango(ValueError):
    """Un coeficiente, una cota o una suma posible no cabe exacta en un double: el modelo no se
    construye, y nada se envuelve en silencio."""


# --- las entradas ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Pool:
    """Un pool de capacidad tal como entra al modelo, con su capacidad efectiva en la ejecucion."""

    codigo: str
    canal: str
    tipo_alcance: str
    referencia: str | None
    capacidad_declarada: int | None
    capacidad: int
    """La efectiva: la declarada, o, en CAMPO, el minimo entre ella y las paradas PROGRAMADA de su
    gestor (las PROGRAMADA, si no declara una)."""
    unidades: int


@dataclass(frozen=True)
class Cobertura:
    """Una restriccion de cobertura activa: un grupo (una dimension y su valor) y su limite, en
    cuentas o en puntos base."""

    codigo: str
    tipo: TipoRestriccion
    dimension: Dimension
    valor: str
    limite: int

    @property
    def operador(self) -> str:
        return ">=" if self.tipo in MINIMOS else "<="


@dataclass(frozen=True)
class Problema:
    """El universo de una ejecucion, en el orden canonico de las cuentas (cuenta_id ascendente):
    la cuenta i es la i-esima. Una cuenta sin pares no es elegible."""

    valor: np.ndarray
    """int64 (n,): la recuperacion positiva esperada a 30 dias, en centavos."""
    contacto: np.ndarray
    """int64 (n,): p_contacto_14d en unidades de 1e-8."""
    par_cuenta: np.ndarray
    """int64 (m,): la cuenta de cada par elegible, en orden (cuenta, pool), sin repetir."""
    par_pool: np.ndarray
    """int64 (m,): el pool de cada par elegible."""
    pools: tuple[Pool, ...]
    """En orden canonico: por codigo."""
    coberturas: tuple[Cobertura, ...]
    """Las activas, en orden canonico: por codigo."""
    miembros: np.ndarray
    """bool (n, k): si la cuenta i es del grupo de la cobertura k."""
    max_cuentas: int | None = None
    max_unidades: int | None = None

    def __post_init__(self) -> None:
        n, m = len(self.valor), len(self.par_cuenta)
        if self.contacto.shape != (n,) or self.par_pool.shape != (m,):
            raise ValueError("El valor, el contacto y los pares no tienen la misma forma.")
        if self.miembros.shape != (n, len(self.coberturas)):
            raise ValueError("Las pertenencias no tienen una columna por cobertura.")
        if m:
            llave = self.par_cuenta * max(1, len(self.pools)) + self.par_pool
            if np.any(np.diff(llave) <= 0):
                raise ValueError("Los pares no estan en orden (cuenta, pool), o se repiten.")
            if self.par_cuenta.min() < 0 or self.par_cuenta.max() >= n:
                raise ValueError("Un par apunta a una cuenta que no existe.")
            if self.par_pool.min() < 0 or self.par_pool.max() >= len(self.pools):
                raise ValueError("Un par apunta a un pool que no existe.")
        codigos = [p.codigo for p in self.pools]
        if codigos != sorted(codigos) or len(set(codigos)) != len(codigos):
            raise ValueError("Los pools no estan en orden canonico por codigo, o se repiten.")
        codigos = [c.codigo for c in self.coberturas]
        if codigos != sorted(codigos) or len(set(codigos)) != len(codigos):
            raise ValueError("Las coberturas no estan en orden canonico por codigo.")

    @property
    def cuentas(self) -> int:
        return len(self.valor)

    @property
    def elegibles(self) -> np.ndarray:
        """bool (n,): las cuentas con al menos un par."""
        return np.bincount(self.par_cuenta, minlength=self.cuentas) > 0

    @property
    def capacidades(self) -> np.ndarray:
        return np.array([p.capacidad for p in self.pools], dtype=np.int64)

    @property
    def capacidades_utiles(self) -> np.ndarray:
        """int64 (pools,): cada capacidad topada en el numero de cuentas. Ningun plan usa mas
        cuentas de un pool que las que hay, asi que ninguna cota cambia; y sus sumas caben en un
        int64 aunque mil pools declaren 2**53 - 1 cada uno."""
        return np.minimum(self.capacidades, self.cuentas)

    @property
    def unidades(self) -> np.ndarray:
        return np.array([p.unidades for p in self.pools], dtype=np.int64)


@dataclass(frozen=True)
class CuentaEntrada:
    """Una cuenta como la declara quien arma un problema sin orden: su identificador estable, su
    valor, su contacto y sus dimensiones."""

    cuenta_id: str
    valor: int
    contacto: int
    dimensiones: dict[Dimension, str | None] = field(default_factory=dict)


def pertenencias(
    dimensiones: dict[Dimension, Sequence], coberturas: Sequence[Cobertura], n: int
) -> np.ndarray:
    """bool (n, k): la cuenta i es del grupo de la cobertura k si su valor en la dimension es el
    declarado. Un valor ausente no es de ningun grupo."""
    miembros = np.zeros((n, len(coberturas)), dtype=bool)
    for k, c in enumerate(coberturas):
        valores = dimensiones.get(c.dimension)
        if valores is None:
            continue
        miembros[:, k] = np.asarray(valores, dtype=object) == c.valor
    return miembros


def construir(
    cuentas: Iterable[CuentaEntrada],
    pares: Iterable[tuple[str, str]],
    pools: Iterable[Pool],
    coberturas: Iterable[Cobertura] = (),
    *,
    max_cuentas: int | None = None,
    max_unidades: int | None = None,
) -> tuple[Problema, tuple[str, ...]]:
    """El problema en orden canonico, a partir de entradas en cualquier orden: las cuentas por
    cuenta_id, los pools y las coberturas por codigo, los pares por (cuenta, pool). Devuelve
    tambien los cuenta_id en ese orden."""
    ordenadas = sorted(cuentas, key=lambda c: c.cuenta_id)
    ids = tuple(c.cuenta_id for c in ordenadas)
    if len(set(ids)) != len(ids):
        raise ValueError("Una cuenta esta dos veces.")
    pools = tuple(sorted(pools, key=lambda p: p.codigo))
    coberturas = tuple(sorted(coberturas, key=lambda c: c.codigo))
    posicion = {cuenta_id: i for i, cuenta_id in enumerate(ids)}
    lugar = {p.codigo: j for j, p in enumerate(pools)}
    llaves = sorted({(posicion[c], lugar[p]) for c, p in pares})
    par_cuenta = np.array([c for c, _ in llaves], dtype=np.int64)
    par_pool = np.array([p for _, p in llaves], dtype=np.int64)
    dimensiones = {
        d: [c.dimensiones.get(d) for c in ordenadas] for d in {c.dimension for c in coberturas}
    }
    problema = Problema(
        valor=np.array([c.valor for c in ordenadas], dtype=np.int64),
        contacto=np.array([c.contacto for c in ordenadas], dtype=np.int64),
        par_cuenta=par_cuenta,
        par_pool=par_pool,
        pools=pools,
        coberturas=coberturas,
        miembros=pertenencias(dimensiones, coberturas, len(ordenadas)),
        max_cuentas=max_cuentas,
        max_unidades=max_unidades,
    )
    return problema, ids


# --- los rangos -----------------------------------------------------------------------------------


def verificar_rangos(p: Problema, politica: PoliticaOptimizacion = PUBLIC_V1) -> None:
    """Fail-closed: cada coeficiente, cota y suma posible de una fila cabe exacta en un double.
    Levanta FueraDeRango antes de construir nada."""
    limite = politica.limite_exacto
    escala = 10**politica.decimales_contacto
    problemas = []
    if len(p.valor) and (p.valor.min() < 0 or int(p.valor.max()) > limite):
        problemas.append("un valor de oportunidad es negativo o no cabe")
    if len(p.contacto) and (p.contacto.min() < 0 or int(p.contacto.max()) > escala):
        problemas.append(f"un p_contacto no esta entre 0 y {escala} (1e-8)")
    for pool in p.pools:
        if pool.capacidad < 0 or pool.capacidad > limite:
            problemas.append(f"la capacidad del pool {pool.codigo} no cabe o es negativa")
        if pool.unidades < 1 or pool.unidades > limite:
            problemas.append(f"las unidades del pool {pool.codigo} no son un entero positivo")
    for nombre, valor in (("max_cuentas", p.max_cuentas), ("max_unidades", p.max_unidades)):
        if valor is not None and not 0 <= valor <= limite:
            problemas.append(f"{nombre} no cabe o es negativo")
    for c in p.coberturas:
        tope = BPS if c.tipo in PARTICIPACIONES else limite
        if not 0 <= c.limite <= tope:
            problemas.append(f"el limite de {c.codigo} no esta entre 0 y {tope}")
    if problemas:
        raise FueraDeRango("; ".join(problemas))
    # Las sumas posibles de cada fila, con enteros de Python: sin envolver nunca.
    elegibles = p.elegibles
    sumas = {
        "el objetivo de valor": int(p.valor[elegibles].sum(dtype=object)),
        "el objetivo de contacto": int(p.contacto[elegibles].sum(dtype=object)),
        "el presupuesto de unidades": int(p.unidades[p.par_pool].sum(dtype=object))
        if len(p.par_pool)
        else 0,
        "el desempate canonico": int(p.cuentas) * (int(p.cuentas) * (len(p.pools) + 1) + 1),
        "una participacion": BPS * int(p.cuentas),
    }
    fuera = [nombre for nombre, suma in sumas.items() if suma > limite]
    if fuera:
        raise FueraDeRango(
            "Una suma posible no cabe exacta en un double (" + ", ".join(fuera) + "): el modelo no "
            "se construye."
        )


# --- el prechequeo --------------------------------------------------------------------------------

ERROR, ADVERTENCIA = "ERROR", "ADVERTENCIA"


@dataclass(frozen=True)
class Hallazgo:
    """Algo que se encontro antes del solver. Un ERROR no deja construir el modelo."""

    codigo: str
    severidad: str
    mensaje: str
    datos: dict = field(default_factory=dict)

    def definicion(self) -> dict:
        return {
            "codigo": self.codigo,
            "severidad": self.severidad,
            "mensaje": self.mensaje,
            "datos": self.datos,
        }


def _grupo(p: Problema, k: int) -> np.ndarray:
    return p.miembros[:, k] & p.elegibles


def _alcanzables(p: Problema, cuentas: np.ndarray) -> np.ndarray:
    """Los pools con al menos un par de esas cuentas (bool por pool)."""
    alcanzables = np.zeros(len(p.pools), dtype=bool)
    alcanzables[p.par_pool[cuentas[p.par_cuenta]]] = True
    return alcanzables


def _tope(*cotas: int | None) -> int | None:
    """El minimo de las cotas declaradas; None si ninguna lo es."""
    declaradas = [c for c in cotas if c is not None]
    return min(declaradas) if declaradas else None


def maximo_atendible(p: Problema) -> int:
    """Una cota segura de cuantas cuentas puede atender cualquier plan factible."""
    elegibles = int(p.elegibles.sum())
    capacidad = int(p.capacidades_utiles[_alcanzables(p, p.elegibles)].sum())
    unidades = int(p.unidades.min()) if p.pools else 1
    presupuesto = None if p.max_unidades is None else p.max_unidades // unidades
    return _tope(elegibles, capacidad, p.max_cuentas, presupuesto)


def prechequear(p: Problema) -> list[Hallazgo]:
    """Lo que se puede demostrar antes del solver. ERROR: el modelo es infactible, y se dice por
    que; ADVERTENCIA: algo de la configuracion no tiene efecto en este universo."""
    hallazgos: list[Hallazgo] = []
    capacidades, unidades = p.capacidades_utiles, p.unidades
    maximo = maximo_atendible(p)
    minimos: dict[Dimension, dict[str, tuple[int, int]]] = {}
    for k, c in enumerate(p.coberturas):
        grupo = _grupo(p, k)
        elegibles = int(grupo.sum())
        datos = {"restriccion": c.codigo, "dimension": c.dimension.value, "valor": c.valor}
        if elegibles == 0:
            hallazgos.append(
                Hallazgo(
                    "GRUPO_SIN_ELEGIBLES",
                    ADVERTENCIA,
                    f"{c.codigo}: ninguna cuenta elegible es de {c.dimension.value} = {c.valor}"
                    + (
                        "; solo el plan vacio cumple esta participacion minima."
                        if c.tipo == TipoRestriccion.MIN_PARTICIPACION_BPS and c.limite > 0
                        else "."
                    ),
                    datos,
                )
            )
        if c.tipo != TipoRestriccion.MIN_CUENTAS or c.limite == 0:
            continue
        alcanzables = _alcanzables(p, grupo)
        unidad_minima = int(unidades[alcanzables].min()) if alcanzables.any() else 1
        cota = _tope(
            elegibles,
            int(capacidades[alcanzables].sum()),
            p.max_cuentas,
            None if p.max_unidades is None else p.max_unidades // unidad_minima,
        )
        datos = {**datos, "minimo": c.limite, "elegibles": elegibles, "atendibles": cota}
        if c.limite > elegibles:
            hallazgos.append(
                Hallazgo(
                    "MINIMO_SUPERA_ELEGIBLES",
                    ERROR,
                    f"{c.codigo} pide al menos {c.limite:,} cuentas de {c.dimension.value} = "
                    f"{c.valor}, y solo {elegibles:,} son elegibles.",
                    datos,
                )
            )
        elif c.limite > cota:
            hallazgos.append(
                Hallazgo(
                    "MINIMO_SUPERA_CAPACIDAD",
                    ERROR,
                    f"{c.codigo} pide al menos {c.limite:,} cuentas de {c.dimension.value} = "
                    f"{c.valor}, y la capacidad, el total y el presupuesto que las alcanzan "
                    f"permiten a lo mas {cota:,}.",
                    datos,
                )
            )
        previo = minimos.setdefault(c.dimension, {}).get(c.valor, (0, unidad_minima))
        minimos[c.dimension][c.valor] = (max(previo[0], c.limite), unidad_minima)
    # Un minimo y un maximo del mismo grupo que no se pueden cumplir juntos.
    for c in p.coberturas:
        if c.tipo != TipoRestriccion.MAX_CUENTAS:
            continue
        minimo = minimos.get(c.dimension, {}).get(c.valor, (0, 1))[0]
        if minimo > c.limite:
            hallazgos.append(
                Hallazgo(
                    "MINIMO_CONTRA_MAXIMO",
                    ERROR,
                    f"{c.dimension.value} = {c.valor} pide al menos {minimo:,} cuentas y "
                    f"{c.codigo} permite a lo mas {c.limite:,}.",
                    {"restriccion": c.codigo, "minimo": minimo, "maximo": c.limite},
                )
            )
    # Los grupos de una misma dimension con valores distintos no se enciman: sus minimos se suman.
    for dimension, por_valor in minimos.items():
        suma = sum(m for m, _ in por_valor.values())
        if suma > maximo:
            hallazgos.append(
                Hallazgo(
                    "SUMA_DE_MINIMOS",
                    ERROR,
                    f"Los minimos de {dimension.value} suman {suma:,} cuentas, y cualquier plan "
                    f"factible atiende a lo mas {maximo:,}.",
                    {"dimension": dimension.value, "suma": suma, "maximo_atendible": maximo},
                )
            )
        if p.max_unidades is not None:
            necesarias = sum(m * u for m, u in por_valor.values())
            if necesarias > p.max_unidades:
                hallazgos.append(
                    Hallazgo(
                        "PRESUPUESTO_INSUFICIENTE",
                        ERROR,
                        f"Los minimos de {dimension.value} necesitan al menos {necesarias:,} "
                        f"unidades de recurso, y el presupuesto es de {p.max_unidades:,}.",
                        {
                            "dimension": dimension.value,
                            "necesarias": necesarias,
                            "presupuesto": p.max_unidades,
                        },
                    )
                )
    for j, pool in enumerate(p.pools):
        if pool.capacidad == 0:
            hallazgos.append(
                Hallazgo(
                    "POOL_SIN_CAPACIDAD",
                    ADVERTENCIA,
                    f"El pool {pool.codigo} tiene capacidad efectiva 0 en esta ejecucion.",
                    {"pool": pool.codigo},
                )
            )
        elif not np.any(p.par_pool == j):
            hallazgos.append(
                Hallazgo(
                    "POOL_SIN_ELEGIBLES",
                    ADVERTENCIA,
                    f"Ninguna cuenta es elegible para el pool {pool.codigo}.",
                    {"pool": pool.codigo},
                )
            )
    return hallazgos


# --- la reduccion exacta --------------------------------------------------------------------------


@dataclass(frozen=True)
class Reduccion:
    """Las clases de dominancia de las cuentas elegibles y lo que se retuvo de cada una."""

    clase: np.ndarray
    """int64 (n,): la clase de cada cuenta elegible; -1 si no es elegible."""
    tamano: np.ndarray
    """int64 (clases,)."""
    cota: np.ndarray
    """int64 (clases,): u[s], cuantas cuentas de la clase puede usar cualquier plan factible."""
    criterio: tuple[str, ...]
    """Por clase, que cota fue la mas fuerte (la primera, en caso de empate)."""
    retenida: np.ndarray
    """bool (n,): las que entran al modelo."""
    exacta: bool = True
    """False si no se redujo: el modelo completo, para comparar."""

    @property
    def clases(self) -> int:
        return len(self.tamano)

    def resumen(self) -> dict:
        elegibles = int(self.tamano.sum())
        retenidas = int(self.retenida.sum())
        criterios: dict[str, int] = {}
        for nombre in self.criterio:
            criterios[nombre] = criterios.get(nombre, 0) + 1
        return {
            "aplicada": self.exacta,
            "clases": self.clases,
            "elegibles": elegibles,
            "retenidas": retenidas,
            "eliminadas": elegibles - retenidas,
            "porcentaje_eliminado": round(100 * (elegibles - retenidas) / elegibles, 4)
            if elegibles
            else 0.0,
            "clases_por_cota": dict(sorted(criterios.items())),
        }


def firmas(p: Problema) -> np.ndarray:
    """int64 (n,): la clase de dominancia de cada cuenta elegible (-1 si no lo es). Dos cuentas son
    de la misma clase solo si tienen exactamente los mismos pools elegibles y las mismas
    pertenencias a cada cobertura activa: las mismas columnas salvo el objetivo. Determinista: las
    clases son las filas unicas de esa firma, en su orden lexicografico."""
    n = p.cuentas
    clase = np.full(n, -1, dtype=np.int64)
    elegibles = np.flatnonzero(p.elegibles)
    if not len(elegibles):
        return clase
    cuantos = np.bincount(p.par_cuenta, minlength=n)
    inicio = np.concatenate(([0], np.cumsum(cuantos)[:-1]))
    posicion = np.arange(len(p.par_cuenta)) - inicio[p.par_cuenta]
    ancho = int(cuantos.max())
    lugar = np.full(n, -1, dtype=np.int64)
    lugar[elegibles] = np.arange(len(elegibles))
    firma = np.full((len(elegibles), ancho + p.miembros.shape[1]), -1, dtype=np.int64)
    firma[lugar[p.par_cuenta], posicion] = p.par_pool
    firma[:, ancho:] = p.miembros[elegibles].astype(np.int64)
    _, inversa = np.unique(firma, axis=0, return_inverse=True)
    clase[elegibles] = np.asarray(inversa, dtype=np.int64).reshape(-1)
    return clase


def reducir(p: Problema, *, exacta: bool = True) -> Reduccion:
    """Las clases, sus cotas u[s] y las cuentas retenidas: las primeras u[s] de cada clase en orden
    canonico (valor desc, contacto desc, cuenta asc). Con exacta=False se retiene todo: el modelo
    completo, para comparar."""
    clase = firmas(p)
    elegibles = clase >= 0
    clases = int(clase.max()) + 1 if elegibles.any() else 0
    tamano = np.bincount(clase[elegibles], minlength=clases).astype(np.int64)
    if not exacta:
        return Reduccion(
            clase, tamano, tamano.copy(), ("SIN_REDUCCION",) * clases, elegibles.copy(), False
        )
    capacidades, unidades = p.capacidades_utiles, p.unidades
    # La primera cuenta de cada clase la representa: todas tienen los mismos pools y pertenencias.
    representante = np.full(clases, -1, dtype=np.int64)
    indices = np.flatnonzero(elegibles)
    representante[clase[indices[::-1]]] = indices[::-1]
    del_par = clase[p.par_cuenta]
    primero = p.par_cuenta == representante[del_par]
    # En enteros: un bincount con pesos suma en float64.
    capacidad_clase = np.zeros(clases, dtype=np.int64)
    np.add.at(capacidad_clase, del_par[primero], capacidades[p.par_pool[primero]])
    unidad_minima = np.full(clases, np.iinfo(np.int64).max, dtype=np.int64)
    np.minimum.at(unidad_minima, del_par[primero], unidades[p.par_pool[primero]])
    maximo = maximo_atendible(p)
    candidatas = [("TAMANO", tamano), ("CAPACIDAD_POOLS", capacidad_clase)]
    if p.max_cuentas is not None:
        candidatas.append(("MAX_CUENTAS_ATENDIDAS", np.full(clases, p.max_cuentas, np.int64)))
    if p.max_unidades is not None:
        candidatas.append(("PRESUPUESTO", p.max_unidades // unidad_minima))
    for k, c in enumerate(p.coberturas):
        if c.tipo == TipoRestriccion.MAX_CUENTAS:
            tope = c.limite
        elif c.tipo == TipoRestriccion.MAX_PARTICIPACION_BPS:
            tope = (c.limite * maximo) // BPS
        else:
            continue
        en_grupo = p.miembros[representante, k]
        cota = np.where(en_grupo, tope, np.iinfo(np.int64).max)
        candidatas.append((c.codigo, cota))
    matriz = np.vstack([cota for _, cota in candidatas])
    cota = matriz.min(axis=0)
    criterio = tuple(candidatas[int(j)][0] for j in matriz.argmin(axis=0))
    # El orden canonico dentro de cada clase, y su lugar en ella.
    orden = np.lexsort((indices, -p.contacto[indices], -p.valor[indices], clase[indices]))
    ordenadas = indices[orden]
    clase_ordenada = clase[ordenadas]
    inicio_clase = np.concatenate(([0], np.cumsum(tamano)[:-1]))
    rango = np.arange(len(ordenadas)) - inicio_clase[clase_ordenada]
    retenida = np.zeros(p.cuentas, dtype=bool)
    retenida[ordenadas[rango < cota[clase_ordenada]]] = True
    return Reduccion(clase, tamano, cota, criterio, retenida, True)


# --- el modelo ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Modelo:
    """La matriz dispersa del MILP sobre las cuentas retenidas, sin los objetivos fijados."""

    columnas: np.ndarray
    """Los pares (indices de p.par_*) que son variables, en orden canonico."""
    cuenta: np.ndarray
    pool: np.ndarray
    A: sp.csr_array
    lb: np.ndarray
    ub: np.ndarray
    filas: dict[str, int]
    """Cuantas filas de cada tipo."""
    objetivos: dict[int, np.ndarray]
    """Los coeficientes enteros de cada nivel, en su sentido natural (maximizar o minimizar)."""

    @property
    def variables(self) -> int:
        return len(self.columnas)

    @property
    def restricciones(self) -> int:
        return self.A.shape[0]

    @property
    def no_nulos(self) -> int:
        return int(self.A.nnz)


SENTIDO = {1: "MAXIMIZAR", 2: "MAXIMIZAR", 3: "MINIMIZAR", 4: "MINIMIZAR"}


def peso_canonico(cuenta: np.ndarray, pool: np.ndarray, cuentas: int, pools: int) -> np.ndarray:
    """El peso del desempate canonico (nivel 4) del par (cuenta i, pool p), con i y p sus lugares
    canonicos: i (pools + 1) + (cuentas - i) p. Entre cuentas empatadas en un mismo pool, va antes
    la de menor cuenta_id (el primer termino crece con i mas que lo que el segundo decrece); entre
    permutaciones de los mismos pools sobre las mismas cuentas, la de menor cuenta_id toma el pool
    de menor orden (la desigualdad de reordenamiento). No es una feature y no cambia el valor."""
    return cuenta * (pools + 1) + (cuentas - cuenta) * pool


def construir_modelo(p: Problema, retenida: np.ndarray) -> Modelo:
    """Las variables (un par retenido cada una), las filas y sus cotas, en orden canonico, como
    una matriz dispersa CSR. Sin variables para pares imposibles."""
    columnas = np.flatnonzero(retenida[p.par_cuenta])
    cuenta, pool = p.par_cuenta[columnas], p.par_pool[columnas]
    nv = len(columnas)
    variables = np.arange(nv, dtype=np.int64)
    filas_i, columnas_i, datos = [], [], []
    lb, ub = [], []
    conteo: dict[str, int] = {}
    fila = 0

    def agregar(nombre: str, indices_fila, indices_col, valores, bajo, alto, cuantas: int):
        nonlocal fila
        filas_i.append(np.asarray(indices_fila, dtype=np.int64) + fila)
        columnas_i.append(np.asarray(indices_col, dtype=np.int64))
        datos.append(np.asarray(valores, dtype=np.int64))
        lb.append(np.broadcast_to(np.asarray(bajo, dtype=np.float64), (cuantas,)))
        ub.append(np.broadcast_to(np.asarray(alto, dtype=np.float64), (cuantas,)))
        conteo[nombre] = conteo.get(nombre, 0) + cuantas
        fila += cuantas

    # Una asignacion por cuenta, solo donde hay dos o mas pares: con uno, su cota ya es 1.
    por_cuenta = np.bincount(cuenta, minlength=p.cuentas)
    multiples = por_cuenta >= 2
    if multiples.any():
        lugar = np.cumsum(multiples) - 1
        dentro = multiples[cuenta]
        agregar(
            "UNA_ASIGNACION",
            lugar[cuenta[dentro]],
            variables[dentro],
            np.ones(int(dentro.sum())),
            -INFINITO,
            1,
            int(multiples.sum()),
        )
    # La capacidad de cada pool con variables.
    usados = np.unique(pool)
    if len(usados):
        lugar = np.full(len(p.pools), -1, dtype=np.int64)
        lugar[usados] = np.arange(len(usados))
        agregar(
            "CAPACIDAD_POOL",
            lugar[pool],
            variables,
            np.ones(nv),
            -INFINITO,
            p.capacidades[usados],
            len(usados),
        )
    if p.max_cuentas is not None and nv:
        agregar("TOTAL_CUENTAS", np.zeros(nv), variables, np.ones(nv), -INFINITO, p.max_cuentas, 1)
    if p.max_unidades is not None and nv:
        agregar(
            "PRESUPUESTO", np.zeros(nv), variables, p.unidades[pool], -INFINITO, p.max_unidades, 1
        )
    for k, c in enumerate(p.coberturas):
        if not nv:
            break
        miembro = p.miembros[cuenta, k]
        if c.tipo in PARTICIPACIONES:
            coeficiente = BPS * miembro.astype(np.int64) - c.limite
            bajo, alto = (0, INFINITO) if c.tipo in MINIMOS else (-INFINITO, 0)
        else:
            coeficiente = miembro.astype(np.int64)
            bajo, alto = (c.limite, INFINITO) if c.tipo in MINIMOS else (-INFINITO, c.limite)
        no_nulo = coeficiente != 0
        agregar(
            "COBERTURA",
            np.zeros(int(no_nulo.sum())),
            variables[no_nulo],
            coeficiente[no_nulo],
            bajo,
            alto,
            1,
        )
    if filas_i:
        A = sp.csr_array(
            (
                np.concatenate(datos).astype(np.float64),
                (np.concatenate(filas_i), np.concatenate(columnas_i)),
            ),
            shape=(fila, nv),
        )
        A.sum_duplicates()
    else:
        A = sp.csr_array((0, nv), dtype=np.float64)
    pools = len(p.pools)
    return Modelo(
        columnas=columnas,
        cuenta=cuenta,
        pool=pool,
        A=A,
        lb=np.concatenate(lb) if lb else np.zeros(0),
        ub=np.concatenate(ub) if ub else np.zeros(0),
        filas=conteo,
        objetivos={
            1: p.valor[cuenta],
            2: p.contacto[cuenta],
            3: p.unidades[pool],
            4: peso_canonico(cuenta, pool, p.cuentas, pools),
        },
    )


# --- resolver -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Nivel:
    """Lo que dio una etapa lexicografica."""

    nivel: int
    estado: EstadoSolver
    codigo_highs: int | None
    objetivo: int | None
    """Exacto, en enteros, de la solucion verificada, en el sentido natural del nivel."""
    cota: float | None
    """La mejor cota de HiGHS en el sentido natural del nivel: superior al maximizar, inferior al
    minimizar."""
    gap: float | None
    nodos: int | None
    segundos: float
    variables: int
    restricciones: int
    no_nulos: int
    invocado: bool = True
    mensaje: str = ""
    metodo: str | None = None
    """RELAJACION_LP_ENTERA si el vertice optimo de la relajacion fue entero; BRANCH_AND_BOUND si
    hizo falta; FIJADO_POR_DUALIDAD si las etapas anteriores ya fijaron cada variable; None si no
    hay variables."""
    iteraciones: int | None = None
    fijadas: int = 0
    """Las variables que su dualidad fijo para las etapas siguientes."""

    def definicion(self) -> dict:
        return {
            "nivel": self.nivel,
            "sentido": SENTIDO[self.nivel],
            "estado": self.estado.value,
            "codigo_highs": self.codigo_highs,
            "objetivo": self.objetivo,
            "mejor_cota": self.cota,
            "gap_relativo": self.gap,
            "nodos": self.nodos,
            "metodo": self.metodo,
            "iteraciones_relajacion": self.iteraciones,
            "fijadas_por_dualidad": self.fijadas,
            "segundos": round(self.segundos, 6),
            "variables": self.variables,
            "restricciones": self.restricciones,
            "no_nulos": self.no_nulos,
            "invocado": self.invocado,
            "mensaje": self.mensaje,
        }


@dataclass(frozen=True)
class Objetivos:
    valor: int
    contacto: int
    unidades: int
    desempate: int


@dataclass
class Resolucion:
    """El resultado de resolver un problema: un plan optimo demostrado, o por que no lo hay."""

    estado: EstadoSolver
    resultado: str
    """PLAN_OPTIMO, INFACTIBLE, OPTIMO_NO_DEMOSTRADO, ERROR_NUMERICO o ERROR_DEL_SOLVER."""
    hallazgos: list[Hallazgo]
    reduccion: Reduccion | None = None
    modelo: Modelo | None = None
    niveles: list[Nivel] = field(default_factory=list)
    asignacion: np.ndarray | None = None
    """int64 (n,): el pool de cada cuenta, o SIN_POOL. Solo en un plan optimo."""
    objetivos: Objetivos | None = None
    tiempos: dict[str, float] = field(default_factory=dict)

    @property
    def optima(self) -> bool:
        return self.estado == EstadoSolver.OPTIMAL and self.asignacion is not None

    def certificado(self) -> dict:
        """Lo que demuestra el plan, o por que no hay uno: el modelo, la reduccion y cada nivel."""
        return {
            "estado": self.estado.value,
            "resultado": self.resultado,
            "modelo": None
            if self.modelo is None
            else {
                "variables": self.modelo.variables,
                "restricciones": self.modelo.restricciones,
                "no_nulos": self.modelo.no_nulos,
                "filas": self.modelo.filas,
                "matriz": "dispersa (scipy.sparse CSR)",
            },
            "reduccion": None if self.reduccion is None else self.reduccion.resumen(),
            "niveles": [n.definicion() for n in self.niveles],
        }


PLAN_OPTIMO = "PLAN_OPTIMO"
INFACTIBLE = "INFACTIBLE"
OPTIMO_NO_DEMOSTRADO = "OPTIMO_NO_DEMOSTRADO"
ERROR_NUMERICO = "ERROR_NUMERICO"
ERROR_DEL_SOLVER = "ERROR_DEL_SOLVER"

RESULTADO_DEL_ESTADO = {
    EstadoSolver.INFEASIBLE: INFACTIBLE,
    EstadoSolver.INFEASIBLE_OR_UNBOUNDED: ERROR_DEL_SOLVER,
    EstadoSolver.TIME_LIMIT: OPTIMO_NO_DEMOSTRADO,
    EstadoSolver.ITERATION_LIMIT: OPTIMO_NO_DEMOSTRADO,
    EstadoSolver.UNBOUNDED: ERROR_DEL_SOLVER,
    EstadoSolver.NUMERICAL_ERROR: ERROR_NUMERICO,
    EstadoSolver.SOLVER_ERROR: ERROR_DEL_SOLVER,
}


def _entera(modelo: Modelo, x: np.ndarray | None) -> np.ndarray | None:
    """La solucion redondeada a enteros, si es binaria (dentro de 1e-6) y cumple cada fila de la
    matriz exactamente, en aritmetica entera; None si no."""
    if x is None or x.shape != (modelo.variables,) or not np.all(np.isfinite(x)):
        return None
    redondeada = np.rint(x)
    if np.any(np.abs(x - redondeada) > 1e-6) or np.any((redondeada != 0) & (redondeada != 1)):
        return None
    entera = redondeada.astype(np.int64)
    if modelo.restricciones:
        matriz = modelo.A.astype(np.int64)
        lhs = matriz @ entera
        if np.any(lhs < modelo.lb) or np.any(lhs > modelo.ub):
            return None
    return entera


def _valores(modelo: Modelo, x: np.ndarray) -> dict[int, int]:
    """El valor exacto de cada nivel en una solucion entera, con enteros de Python."""
    return {
        nivel: int(np.dot(coeficientes.astype(object), x.astype(object)))
        for nivel, coeficientes in modelo.objetivos.items()
    }


FIJADO_POR_DUALIDAD = "FIJADO_POR_DUALIDAD"


def resolver(
    p: Problema,
    politica: PoliticaOptimizacion = PUBLIC_V1,
    *,
    reducir_: bool = True,
    resolvedor: Resolvedor | None = None,
    marcar: Callable[[str], None] = lambda _fase: None,
) -> Resolucion:
    """El plan lexicografico optimo de un problema, con su certificado, o por que no lo hay.
    `marcar` se llama al terminar cada fase: DESPUES_REDUCCION, DESPUES_MODELO y
    DESPUES_SOLVER_NIVEL_k. Levanta FueraDeRango si algo no cabe exacto.

    Cada etapa resuelve solo sus variables libres: las que las etapas anteriores no fijaron. Al
    terminar una etapa, su relajacion da, por dualidad debil, las variables que toda solucion de su
    optimo deja en su cota (`solver.Duales`): esas quedan fijas en las siguientes, que solo
    deciden entre los optimos de la anterior. La solucion completa se verifica en enteros contra
    todas las filas, en cada etapa."""
    resolvedor = resolvedor or solver_.resolver
    tiempos: dict[str, float] = {}
    verificar_rangos(p, politica)
    hallazgos = prechequear(p)
    if any(h.severidad == ERROR for h in hallazgos):
        return Resolucion(EstadoSolver.NO_INVOCADO, INFACTIBLE, hallazgos, tiempos=tiempos)
    inicio = time.perf_counter()
    reduccion = reducir(p, exacta=reducir_)
    tiempos["reduccion"] = time.perf_counter() - inicio
    marcar("DESPUES_REDUCCION")
    inicio = time.perf_counter()
    modelo = construir_modelo(p, reduccion.retenida)
    tiempos["modelo"] = time.perf_counter() - inicio
    marcar("DESPUES_MODELO")
    resolucion = Resolucion(
        EstadoSolver.OPTIMAL, PLAN_OPTIMO, hallazgos, reduccion, modelo, tiempos=tiempos
    )
    nv = modelo.variables
    libre = np.ones(nv, dtype=bool)
    fijo = np.zeros(nv, dtype=np.int64)
    x = np.zeros(nv, dtype=np.int64)
    fijos: list[tuple[int, int]] = []
    for nivel in (1, 2, 3, 4):
        if nv == 0:
            # Sin variables, el plan vacio es el unico: optimo sin llamar al solver.
            resolucion.niveles.append(
                Nivel(nivel, EstadoSolver.OPTIMAL, None, 0, 0.0, 0.0, 0, 0.0, 0, 0, 0, False)
            )
            marcar(f"DESPUES_SOLVER_NIVEL_{nivel}")
            continue
        nivel_resuelto, x = _resolver_nivel(
            modelo, nivel, libre, fijo, fijos, x, resolvedor, politica
        )
        tiempos[f"nivel_{nivel}"] = nivel_resuelto.segundos
        resolucion.niveles.append(nivel_resuelto)
        marcar(f"DESPUES_SOLVER_NIVEL_{nivel}")
        if nivel_resuelto.estado != EstadoSolver.OPTIMAL:
            resolucion.estado = nivel_resuelto.estado
            resolucion.resultado = RESULTADO_DEL_ESTADO.get(nivel_resuelto.estado, ERROR_DEL_SOLVER)
            return resolucion
        fijos.append((nivel, nivel_resuelto.objetivo))
    asignacion = np.full(p.cuentas, SIN_POOL, dtype=np.int64)
    elegidas = x == 1
    asignacion[modelo.cuenta[elegidas]] = modelo.pool[elegidas]
    resolucion.asignacion = asignacion
    exactos = _valores(modelo, x)
    resolucion.objetivos = Objetivos(exactos[1], exactos[2], exactos[3], exactos[4])
    return resolucion


def _resolver_nivel(
    modelo: Modelo,
    nivel: int,
    libre: np.ndarray,
    fijo: np.ndarray,
    fijos: list[tuple[int, int]],
    anterior: np.ndarray,
    resolvedor: Resolvedor,
    politica: PoliticaOptimizacion,
) -> tuple[Nivel, np.ndarray]:
    """Una etapa sobre sus variables libres. Actualiza `libre` y `fijo` con lo que su dualidad
    fija para las siguientes. Devuelve el nivel y la solucion completa (la anterior, si fallo)."""
    coeficientes = modelo.objetivos[nivel]
    signo = -1 if SENTIDO[nivel] == "MAXIMIZAR" else 1
    indices = np.flatnonzero(libre)
    fijas = np.where(libre, 0, fijo)
    constante = int(np.dot(coeficientes.astype(object), fijas.astype(object)))
    if not len(indices):
        # Las etapas anteriores fijaron cada variable: la solucion ya esta determinada, y su valor
        # en este nivel es el unico posible entre los optimos de la anterior.
        exactos = _valores(modelo, anterior)
        nivel_ = Nivel(
            nivel,
            EstadoSolver.OPTIMAL,
            None,
            exactos[nivel],
            float(exactos[nivel]),
            0.0,
            0,
            0.0,
            0,
            0,
            0,
            False,
            "Sin variables libres: las etapas anteriores las fijaron por dualidad.",
            FIJADO_POR_DUALIDAD,
        )
        return nivel_, anterior
    # Las filas, sobre las variables libres: sus cotas se desplazan por lo fijo, y una fila sin
    # variables libres ya la decidieron las fijas (se verifico en la etapa anterior).
    A_libre = modelo.A[:, indices]
    desplazamiento = modelo.A @ fijas.astype(np.float64) if (~libre).any() else 0.0
    lb = modelo.lb - desplazamiento
    ub = modelo.ub - desplazamiento
    con_libres = np.diff(A_libre.indptr) > 0
    bloques = [A_libre[con_libres]]
    cotas_bajas, cotas_altas = [lb[con_libres]], [ub[con_libres]]
    for n, valor in fijos:
        fila = modelo.objetivos[n]
        resto = valor - int(np.dot(fila.astype(object), fijas.astype(object)))
        bloques.append(sp.csr_array(fila[indices].astype(np.float64).reshape(1, -1)))
        cotas_bajas.append(np.array([resto], dtype=np.float64))
        cotas_altas.append(np.array([resto], dtype=np.float64))
    A = sp.vstack(bloques, format="csr")
    lb, ub = np.concatenate(cotas_bajas), np.concatenate(cotas_altas)
    res = resolvedor(signo * coeficientes[indices].astype(np.float64), A, lb, ub, politica=politica)
    cota = None if res.cota is None else signo * res.cota + constante
    estado = res.estado
    objetivo = None
    entera = None
    fijadas = 0
    if res.estado == EstadoSolver.OPTIMAL and res.x is not None and len(res.x) == len(indices):
        completa = fijas.astype(np.float64)
        completa[indices] = res.x
        entera = _entera(modelo, completa)
    if entera is not None:
        exactos = _valores(modelo, entera)
        objetivo = exactos[nivel]
        reproduce = all(exactos[n] == v for n, v in fijos)
        consistente = (
            res.objetivo is not None and abs(signo * res.objetivo - (objetivo - constante)) < 0.5
        )
        if not (reproduce and consistente):
            entera, estado = None, EstadoSolver.NUMERICAL_ERROR
    elif res.estado == EstadoSolver.OPTIMAL:
        estado = EstadoSolver.NUMERICAL_ERROR
    if nivel > 1 and estado == EstadoSolver.INFEASIBLE:
        # Con el optimo anterior fijo, la solucion anterior es factible: una infactibilidad aqui
        # es numerica, no de la configuracion.
        estado = EstadoSolver.NUMERICAL_ERROR
    if entera is not None and nivel < 4 and res.duales is not None:
        cero, uno = res.duales.fijables(signo * (objetivo - constante))
        valores = entera[indices]
        if np.any(valores[cero] != 0) or np.any(valores[uno] != 1):
            # Una solucion optima que contradice su propia dualidad: los numeros no son confiables.
            entera, estado = None, EstadoSolver.NUMERICAL_ERROR
        else:
            libre[indices[cero | uno]] = False
            fijo[indices[uno]] = 1
            fijo[indices[cero]] = 0
            fijadas = int((cero | uno).sum())
    nivel_ = Nivel(
        nivel,
        estado,
        res.codigo_highs,
        objetivo,
        cota,
        res.gap,
        res.nodos,
        res.segundos,
        len(indices),
        A.shape[0],
        int(A.nnz),
        True,
        res.mensaje,
        res.metodo,
        res.iteraciones,
        fijadas,
    )
    return nivel_, (entera if entera is not None else anterior)


# --- evaluar un plan ------------------------------------------------------------------------------


@dataclass(frozen=True)
class UsoPool:
    codigo: str
    canal: str
    capacidad_declarada: int | None
    capacidad: int
    elegibles: int
    retenidas: int
    utilizada: int
    holgura: int
    unidades: int
    unidades_consumidas: int


@dataclass(frozen=True)
class EvaluacionRestriccion:
    codigo: str
    tipo: str
    dimension: str | None
    valor_dimension: str | None
    operador: str
    lhs: int
    rhs: int
    holgura: int
    cuentas_grupo: int | None = None
    total: int | None = None
    limite_resuelto: int | None = None

    @property
    def binding(self) -> bool:
        return self.holgura == 0


@dataclass(frozen=True)
class Evaluacion:
    """Lo que un plan usa y cumple, calculado en enteros a partir de su asignacion."""

    cuentas: int
    objetivos: Objetivos
    uso: tuple[UsoPool, ...]
    restricciones: tuple[EvaluacionRestriccion, ...]
    violaciones: tuple[str, ...]

    @property
    def factible(self) -> bool:
        return not self.violaciones


def resuelto(c: Cobertura, total: int) -> int:
    """El limite de una participacion resuelto a cuentas con `total` cuentas atendidas: ceil para
    un minimo, floor para un maximo, en aritmetica entera."""
    if c.tipo == TipoRestriccion.MIN_PARTICIPACION_BPS:
        return -(-(c.limite * total) // BPS)
    return (c.limite * total) // BPS


def evaluar(p: Problema, asignacion: np.ndarray, retenida: np.ndarray | None = None) -> Evaluacion:
    """El uso de cada pool, cada restriccion con su lado izquierdo, su derecho y su holgura, los
    objetivos y lo que el plan viola (nada, si es factible), en enteros."""
    elegidas = asignacion != SIN_POOL
    total = int(elegidas.sum())
    unidades = p.unidades
    pools = len(p.pools)
    violaciones = []
    if np.any(asignacion[elegidas] >= pools) or np.any(asignacion < SIN_POOL):
        raise ValueError("La asignacion apunta a un pool que no existe.")
    llaves = p.par_cuenta * max(1, pools) + p.par_pool
    pedidas = np.flatnonzero(elegidas) * max(1, pools) + asignacion[elegidas]
    lugar = np.searchsorted(llaves, pedidas)
    validas = (lugar < len(llaves)) & (llaves[np.minimum(lugar, len(llaves) - 1)] == pedidas)
    if len(pedidas) and not validas.all():
        violaciones.append(f"{int((~validas).sum())} cuentas asignadas a un pool no elegible")
    utilizada = np.bincount(asignacion[elegidas], minlength=pools)
    elegibles = np.bincount(p.par_pool, minlength=pools)
    retenidas = (
        np.bincount(p.par_pool[retenida[p.par_cuenta]], minlength=pools)
        if retenida is not None
        else elegibles
    )
    uso = []
    restricciones = []
    for j, pool in enumerate(p.pools):
        usada = int(utilizada[j])
        uso.append(
            UsoPool(
                pool.codigo,
                pool.canal,
                pool.capacidad_declarada,
                pool.capacidad,
                int(elegibles[j]),
                int(retenidas[j]),
                usada,
                pool.capacidad - usada,
                pool.unidades,
                usada * pool.unidades,
            )
        )
        restricciones.append(
            EvaluacionRestriccion(
                f"POOL:{pool.codigo}",
                "CAPACIDAD_POOL",
                None,
                None,
                "<=",
                usada,
                pool.capacidad,
                pool.capacidad - usada,
            )
        )
        if usada > pool.capacidad:
            violaciones.append(f"el pool {pool.codigo} usa {usada} de {pool.capacidad}")
    consumidas = int(unidades[asignacion[elegidas]].sum()) if total else 0
    if p.max_cuentas is not None:
        restricciones.append(
            EvaluacionRestriccion(
                "TOTAL_CUENTAS",
                "MAX_CUENTAS_ATENDIDAS",
                None,
                None,
                "<=",
                total,
                p.max_cuentas,
                p.max_cuentas - total,
            )
        )
        if total > p.max_cuentas:
            violaciones.append(f"{total} cuentas atendidas, mas que {p.max_cuentas}")
    if p.max_unidades is not None:
        restricciones.append(
            EvaluacionRestriccion(
                "PRESUPUESTO_UNIDADES",
                "MAX_UNIDADES_RECURSO",
                None,
                None,
                "<=",
                consumidas,
                p.max_unidades,
                p.max_unidades - consumidas,
            )
        )
        if consumidas > p.max_unidades:
            violaciones.append(f"{consumidas} unidades de recurso, mas que {p.max_unidades}")
    for k, c in enumerate(p.coberturas):
        grupo = int((elegidas & p.miembros[:, k]).sum())
        if c.tipo in PARTICIPACIONES:
            lhs, rhs = BPS * grupo, c.limite * total
            extra = {"cuentas_grupo": grupo, "total": total, "limite_resuelto": resuelto(c, total)}
        else:
            lhs, rhs = grupo, c.limite
            extra = {"cuentas_grupo": grupo, "total": total}
        holgura = lhs - rhs if c.tipo in MINIMOS else rhs - lhs
        restricciones.append(
            EvaluacionRestriccion(
                c.codigo,
                c.tipo.value,
                c.dimension.value,
                c.valor,
                c.operador,
                lhs,
                rhs,
                holgura,
                **extra,
            )
        )
        if holgura < 0:
            violaciones.append(f"{c.codigo}: {lhs} {c.operador} {rhs} no se cumple")
    valor = int(p.valor[elegidas].sum(dtype=object)) if total else 0
    contacto = int(p.contacto[elegidas].sum(dtype=object)) if total else 0
    desempate = (
        int(
            peso_canonico(np.flatnonzero(elegidas), asignacion[elegidas], p.cuentas, pools).sum(
                dtype=object
            )
        )
        if total
        else 0
    )
    return Evaluacion(
        total,
        Objetivos(valor, contacto, consumidas, desempate),
        tuple(uso),
        tuple(sorted(restricciones, key=lambda r: r.codigo)),
        tuple(violaciones),
    )


# --- la relajacion --------------------------------------------------------------------------------


def diagnosticar_relajacion(
    p: Problema,
    politica: PoliticaOptimizacion = PUBLIC_V1,
    *,
    resolvedor: Resolvedor | None = None,
) -> dict:
    """Un modelo auxiliar para una configuracion infactible: las mismas variables (todos los pares
    elegibles, sin reducir: la reduccion supone factibilidad) con una holgura entera no negativa en
    cada restriccion declarada (capacidad de cada pool, total, presupuesto y cada cobertura), y el
    objetivo de minimizar la suma de las holguras. Dice que restricciones tendrian que relajarse y
    cuanto para que haya un plan, en una relajacion minima posible: no es un IIS, y no es unica."""
    resolvedor = resolvedor or solver_.resolver
    modelo = construir_modelo(p, p.elegibles)
    nv = modelo.variables
    # Una columna de holgura por cada fila declarada (no las de una asignacion por cuenta): +1 en
    # un minimo (le suma al lado izquierdo), -1 en un maximo o una capacidad; en una
    # participacion, por 10,000, para que la holgura se lea en cuentas.
    declaradas = []
    fila = modelo.filas.get("UNA_ASIGNACION", 0)
    for j in np.unique(modelo.pool):
        declaradas.append((fila, f"POOL:{p.pools[int(j)].codigo}", "cuentas", -1))
        fila += 1
    if p.max_cuentas is not None and nv:
        declaradas.append((fila, "TOTAL_CUENTAS", "cuentas", -1))
        fila += 1
    if p.max_unidades is not None and nv:
        declaradas.append((fila, "PRESUPUESTO_UNIDADES", "unidades de recurso", -1))
        fila += 1
    for c in p.coberturas:
        if not nv:
            break
        signo = 1 if c.tipo in MINIMOS else -1
        escala = BPS if c.tipo in PARTICIPACIONES else 1
        declaradas.append((fila, c.codigo, "cuentas", signo * escala))
        fila += 1
    if not declaradas:
        return {"estado": "SIN_RESTRICCIONES_RELAJABLES", "relajaciones": []}
    holguras = len(declaradas)
    columnas = sp.csr_array(
        (
            np.array([s for *_, s in declaradas], dtype=np.float64),
            (np.array([f for f, *_ in declaradas]), np.arange(holguras)),
        ),
        shape=(modelo.restricciones, holguras),
    )
    A = sp.hstack([modelo.A, columnas], format="csr")
    c = np.concatenate([np.zeros(nv), np.ones(holguras)])
    # Una holgura entera, con una cota que ninguna necesita pasar: con todas las cuentas
    # elegidas, cada fila declarada se cumple con esa holgura.
    tope = float(p.cuentas + (p.max_unidades or 0) + int(p.unidades.sum()) * p.cuentas + 1)
    superior = np.concatenate([np.ones(nv), np.full(holguras, tope)])
    res = resolvedor(c, A, modelo.lb, modelo.ub, politica=politica, superior=superior)
    if res.estado != EstadoSolver.OPTIMAL or res.x is None:
        return {
            "estado": "NO_CONCLUYENTE",
            "estado_solver": res.estado.value,
            "segundos": round(res.segundos, 6),
            "relajaciones": [],
        }
    valores = np.rint(res.x[nv:]).astype(np.int64)
    return {
        "estado": "RELAJACION_MINIMA",
        "estado_solver": res.estado.value,
        "segundos": round(res.segundos, 6),
        "holgura_total": int(valores.sum()),
        "relajaciones": [
            {
                "restriccion": codigo,
                "relajar_en": int(v),
                "unidad": unidad,
                "sentido": "bajar el minimo" if s > 0 else "subir el maximo o la capacidad",
            }
            for (_, codigo, unidad, s), v in zip(declaradas, valores, strict=True)
            if v > 0
        ],
        "nota": "Una relajacion minima posible (la suma de holguras enteras, cada una con peso 1 "
        "en su unidad), no un IIS ni la unica: otras combinaciones pueden bastar.",
    }


# --- el plan canonico y su hash -------------------------------------------------------------------


def estados(
    p: Problema, asignacion: np.ndarray, motivos_no_elegible: Sequence[str | None] | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """El estado y el motivo de cada cuenta: ASIGNADA en la solucion, NO_SELECCIONADA si era
    elegible y no quedo, NO_ELEGIBLE con su motivo factual (el que dice quien armo el universo; sin
    el, SIN_POOL_APLICABLE)."""
    asignada = asignacion != SIN_POOL
    elegible = p.elegibles
    estado = np.where(
        asignada,
        EstadoCuenta.ASIGNADA.value,
        np.where(elegible, EstadoCuenta.NO_SELECCIONADA.value, EstadoCuenta.NO_ELEGIBLE.value),
    ).astype(object)
    if motivos_no_elegible is None:
        no_elegible = np.full(p.cuentas, Motivo.SIN_POOL_APLICABLE.value, dtype=object)
    else:
        no_elegible = np.array(
            [m or Motivo.SIN_POOL_APLICABLE.value for m in motivos_no_elegible], dtype=object
        )
    motivo = np.where(
        asignada,
        Motivo.EN_SOLUCION_OPTIMA.value,
        np.where(elegible, Motivo.FUERA_DE_SOLUCION_OPTIMA.value, no_elegible),
    ).astype(object)
    return estado, motivo


def lineas_del_plan(
    cuenta_ids: Sequence[str],
    p: Problema,
    asignacion: np.ndarray,
    estado: Sequence[str],
    motivo: Sequence[str],
    retenida: np.ndarray,
    evaluacion: Evaluacion,
    *,
    gestores: Sequence[str | None] | None = None,
    ordenes: Sequence[int | None] | None = None,
) -> Iterable[str]:
    """El plan en forma canonica, linea por linea: los objetivos, cada cuenta en orden de
    cuenta_id, cada pool por codigo y cada restriccion por codigo. Sin instantes, duraciones ni
    identificadores de la ejecucion."""
    o = evaluacion.objetivos
    yield (
        f"OBJETIVOS|valor={o.valor}|contacto={o.contacto}|unidades={o.unidades}|"
        f"desempate={o.desempate}"
    )
    for i, cuenta_id in enumerate(cuenta_ids):
        j = int(asignacion[i])
        pool = p.pools[j] if j != SIN_POOL else None
        yield "|".join(
            [
                "C",
                cuenta_id,
                estado[i],
                motivo[i],
                pool.canal if pool else "-",
                pool.codigo if pool else "-",
                str(int(p.valor[i])),
                str(int(p.contacto[i])),
                str(pool.unidades) if pool else "-",
                (gestores[i] or "-") if gestores is not None and pool else "-",
                str(ordenes[i]) if ordenes is not None and pool and ordenes[i] is not None else "-",
                "1" if retenida[i] else "0",
            ]
        )
    for u in evaluacion.uso:
        yield (f"P|{u.codigo}|{u.capacidad}|{u.utilizada}|{u.holgura}|{u.unidades_consumidas}")
    for r in evaluacion.restricciones:
        yield f"R|{r.codigo}|{r.lhs}|{r.operador}|{r.rhs}|{r.holgura}|{int(r.binding)}"


def hash_plan(lineas: Iterable[str]) -> str:
    digesto = hashlib.sha256()
    for linea in lineas:
        digesto.update(linea.encode("utf-8") + b"\n")
    return digesto.hexdigest()
