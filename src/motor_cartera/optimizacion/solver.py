"""El solver de optimization/v1: el HiGHS que trae SciPy, por su API publica (scipy.optimize), su
huella y como se lee su estado.

Cada etapa es un MILP de variables enteras acotadas. Se resuelve en dos pasos, los dos con HiGHS:

1. Su relajacion lineal, con `scipy.optimize.linprog(method="highs-ipm")`: punto interior con
   crossover (un vertice), sin presolve. Si HiGHS la resuelve Optimal y su vertice es entero, ese
   vertice es el optimo del MILP: la relajacion acota al MILP, y una solucion entera la alcanza. La
   cota es su objetivo y el gap es cero. Si la relajacion es infactible, el MILP tambien lo es.
2. Si el vertice tiene alguna variable fraccionaria, el MILP con branch-and-bound:
   `scipy.optimize.milp`, con mip_rel_gap = 0, en el tiempo que le queda a la etapa.

El presolve se apaga en los dos: medido sobre este modelo, con las filas densas de una
participacion, el presolve de HiGHS tardaba mas que resolver (decision 236).

La huella dice con que se resolvio: el algoritmo, SciPy, HiGHS (si se puede leer de forma
confiable), NumPy, Python, el sistema, la arquitectura y los parametros. Las mismas entradas con la
misma huella dan el mismo plan; con otra huella, una ejecucion es otra firma.

El estado se toma tal como lo da HiGHS, sin convertir uno en otro: un TIME_LIMIT nunca es OPTIMAL.
SciPy pone el codigo de HiGHS en su mensaje ("HiGHS Status 7: Optimal"); si no esta, se usa el de
SciPy. Lo que SciPy no expone (las iteraciones del simplex dentro de milp) no se inventa.
"""

from __future__ import annotations

import hashlib
import math
import platform
import re
import time
import warnings
from dataclasses import dataclass

import numpy as np
import scipy
import scipy.sparse as sp
from scipy.optimize import Bounds, LinearConstraint, linprog, milp

from motor_cartera.analytics.catalogo import canonico
from motor_cartera.optimizacion.politica import (
    ALGORITMO,
    PUBLIC_V1,
    EstadoSolver,
    PoliticaOptimizacion,
)

ESTADOS_HIGHS: dict[int, EstadoSolver] = {
    7: EstadoSolver.OPTIMAL,
    8: EstadoSolver.INFEASIBLE,
    9: EstadoSolver.INFEASIBLE_OR_UNBOUNDED,
    10: EstadoSolver.UNBOUNDED,
    13: EstadoSolver.TIME_LIMIT,
    14: EstadoSolver.ITERATION_LIMIT,
}
"""El HighsModelStatus de HiGHS (kOptimal = 7, kInfeasible = 8, kUnboundedOrInfeasible = 9,
kUnbounded = 10, kTimeLimit = 13, kIterationLimit = 14). Cualquier otro es SOLVER_ERROR."""
ESTADOS_SCIPY: dict[int, EstadoSolver] = {
    0: EstadoSolver.OPTIMAL,
    2: EstadoSolver.INFEASIBLE,
    3: EstadoSolver.UNBOUNDED,
}
"""Si el mensaje no trae el codigo de HiGHS: el de SciPy. Su 1 junta el limite de tiempo y el de
iteraciones, y se distingue por su mensaje."""
CODIGO_HIGHS = re.compile(r"HiGHS Status (\d+)")
TOLERANCIA_ENTERA_LP = 1e-9
"""Un vertice de la relajacion es entero si cada variable esta a menos de esto de un entero: mas
estricto que la tolerancia de integralidad de un MILP (1e-6). Despues se verifica en enteros."""

RELAJACION_LP_ENTERA = "RELAJACION_LP_ENTERA"
BRANCH_AND_BOUND = "BRANCH_AND_BOUND"


def version_highs() -> str | None:
    """La version del HiGHS que trae SciPy, de su modulo compilado; None si no se puede leer."""
    try:
        from scipy.optimize._highspy import _core
    except ImportError:  # pragma: no cover - otra distribucion de SciPy
        return None
    partes = [getattr(_core, f"HIGHS_VERSION_{p}", None) for p in ("MAJOR", "MINOR", "PATCH")]
    if any(not isinstance(p, int) for p in partes):  # pragma: no cover - otra distribucion
        return None
    return ".".join(str(p) for p in partes)


def parametros(politica: PoliticaOptimizacion = PUBLIC_V1) -> dict:
    """Los parametros con que se llama al solver: los de la politica, y nada mas."""
    return {
        "metodo": "relajacion lineal (highs-ipm con crossover); si su vertice no es entero, "
        "branch-and-bound (milp)",
        "time_limit_por_nivel": politica.segundos_por_nivel,
        "mip_rel_gap": politica.mip_rel_gap,
        "presolve": politica.presolve,
        "tolerancia_entera_lp": TOLERANCIA_ENTERA_LP,
    }


def huella(politica: PoliticaOptimizacion = PUBLIC_V1) -> dict:
    """Con que se resuelve en este proceso. Ningun dato de la maquina que identifique a nadie."""
    return {
        "algoritmo": ALGORITMO,
        "scipy": scipy.__version__,
        "highs": version_highs(),
        "numpy": np.__version__,
        "python": platform.python_version(),
        "sistema": platform.system(),
        "arquitectura": platform.machine(),
        "parametros": parametros(politica),
    }


def hash_huella(datos: dict) -> str:
    """El solver_fingerprint: SHA-256 de la huella en forma canonica."""
    return hashlib.sha256(canonico(datos)).hexdigest()


@dataclass(frozen=True)
class Duales:
    """Lo que la relajacion lineal dice, por dualidad debil, de toda solucion entera.

    Con multiplicadores y del signo correcto (cualquiera, no solo los optimos), r = c - A'y y
    L = y'b + sum_j min(0, r_j u_j), toda solucion factible cumple
    c'x >= L + sum_{r_j > 0} r_j x_j + sum_{r_j < 0} |r_j| (u_j - x_j). Si z es el optimo entero,
    toda solucion que lo alcanza deja en 0 cada variable con r_j > z - L, y en u_j cada una con
    -r_j > z - L. r y L se calculan aqui, con su cota de error de punto flotante."""

    reducidos: np.ndarray
    lagrangiana: float
    error: float
    """Una cota del error de punto flotante de r y de L."""

    def fijables(self, optimo: int) -> tuple[np.ndarray, np.ndarray]:
        """Las variables que toda solucion de valor `optimo` (el entero, en el sentido de
        minimizar) deja en 0, y las que deja en su cota superior."""
        brecha = optimo - self.lagrangiana
        if brecha < -self.error:
            # Una cota por encima del optimo entero: los numeros no son confiables; no se fija nada.
            vacio = np.zeros(len(self.reducidos), dtype=bool)
            return vacio, vacio.copy()
        umbral = max(brecha, 0.0) + 2 * self.error
        return self.reducidos > umbral, self.reducidos < -umbral


EPSILON = float(np.finfo(np.float64).eps)


def duales(relajada, c, A_ub, b_ub, A_eq, b_eq, superior) -> Duales | None:
    """Los costos reducidos y la cota lagrangiana de una relajacion resuelta, calculados con sus
    multiplicadores; None si SciPy no los devolvio."""
    n = len(c)
    y_ub = np.zeros(0)
    y_eq = np.zeros(0)
    if A_ub is not None:
        if relajada.get("ineqlin") is None:
            return None
        # Un minimo con signo equivocado no es un multiplicador valido: se proyecta.
        y_ub = np.minimum(np.asarray(relajada.ineqlin.marginals, dtype=np.float64), 0.0)
        if len(y_ub) != A_ub.shape[0]:
            return None
    if A_eq is not None:
        if relajada.get("eqlin") is None:
            return None
        y_eq = np.asarray(relajada.eqlin.marginals, dtype=np.float64)
        if len(y_eq) != A_eq.shape[0]:
            return None
    atenuado = np.zeros(n)
    r = c.copy()
    terminos = []
    for matriz, y, b in ((A_ub, y_ub, b_ub), (A_eq, y_eq, b_eq)):
        if matriz is not None:
            r -= matriz.T @ y
            atenuado += abs(matriz).T @ np.abs(y)
            terminos.append(y * b)
    terminos.append(np.minimum(0.0, r * superior))
    productos = np.concatenate(terminos)
    lagrangiana = math.fsum(productos.tolist())
    escala = math.fsum(np.abs(productos).tolist()) + float(
        ((np.abs(c) + atenuado) * superior).sum()
    )
    return Duales(r, lagrangiana, 64 * EPSILON * escala + 1e-9)


@dataclass(frozen=True)
class ResultadoSolver:
    """Lo que devolvio el solver en una etapa, tal como lo dio."""

    estado: EstadoSolver
    codigo_highs: int | None
    mensaje: str
    x: np.ndarray | None
    objetivo: float | None
    cota: float | None
    """La mejor cota de HiGHS, en el sentido del problema que resolvio (minimizar): la de la
    relajacion, si su vertice fue entero; la del branch-and-bound, si no."""
    gap: float | None
    nodos: int | None
    segundos: float
    metodo: str = BRANCH_AND_BOUND
    iteraciones: int | None = None
    """Las iteraciones de la relajacion lineal (las que SciPy expone); None si no las dio."""
    duales: Duales | None = None
    """Los de la relajacion, para fijar variables en las etapas siguientes; None si no los hay."""


def estado_de(status: int, mensaje: str) -> tuple[EstadoSolver, int | None]:
    """El estado del solver a partir del status de SciPy y su mensaje."""
    encontrado = CODIGO_HIGHS.search(mensaje or "")
    if encontrado:
        codigo = int(encontrado.group(1))
        return ESTADOS_HIGHS.get(codigo, EstadoSolver.SOLVER_ERROR), codigo
    if status == 1:
        limite = "time limit" in (mensaje or "").lower()
        return (EstadoSolver.TIME_LIMIT if limite else EstadoSolver.ITERATION_LIMIT), None
    return ESTADOS_SCIPY.get(status, EstadoSolver.SOLVER_ERROR), None


def _forma_linprog(A: sp.csr_array, lb: np.ndarray, ub: np.ndarray):
    """Las filas lb <= A x <= ub como linprog las pide: A_ub x <= b_ub (un minimo, negado) y
    A_eq x = b_eq."""
    igual = np.isfinite(lb) & np.isfinite(ub) & (lb == ub)
    arriba = np.isfinite(ub) & ~igual
    abajo = np.isfinite(lb) & ~igual
    A_ub = None
    b_ub = None
    if arriba.any() or abajo.any():
        A_ub = sp.vstack([A[arriba], -A[abajo]], format="csr")
        b_ub = np.concatenate([ub[arriba], -lb[abajo]])
    A_eq = A[igual] if igual.any() else None
    b_eq = ub[igual] if igual.any() else None
    return A_ub, b_ub, A_eq, b_eq


TERMINALES_DE_LA_RELAJACION = frozenset(
    {
        EstadoSolver.OPTIMAL,
        EstadoSolver.INFEASIBLE,
        EstadoSolver.TIME_LIMIT,
        EstadoSolver.ITERATION_LIMIT,
        EstadoSolver.UNBOUNDED,
    }
)
"""Lo que la relajacion dice por si sola. Cualquier otro estado (una dificultad numerica) no dice
nada del MILP: se resuelve con branch-and-bound."""


def resolver(
    c: np.ndarray,
    A: sp.csr_array,
    lb: np.ndarray,
    ub: np.ndarray,
    *,
    politica: PoliticaOptimizacion = PUBLIC_V1,
    superior: np.ndarray | None = None,
) -> ResultadoSolver:
    """Minimiza c'x con x entera, 0 <= x <= superior (1 por omision: binaria) y lb <= A x <= ub,
    con HiGHS: su relajacion lineal y, si su vertice no es entero (o la relajacion no concluye por
    una dificultad numerica), su branch-and-bound."""
    n = len(c)
    c = np.asarray(c, dtype=np.float64)
    superior = np.ones(n) if superior is None else np.asarray(superior, dtype=np.float64)
    inicio = time.perf_counter()
    A_ub, b_ub, A_eq, b_eq = _forma_linprog(A, lb, ub) if A.shape[0] else (None,) * 4
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        relajada = linprog(
            c,
            A_ub=A_ub,
            b_ub=b_ub,
            A_eq=A_eq,
            b_eq=b_eq,
            bounds=np.column_stack([np.zeros(n), superior]),
            method="highs-ipm",
            options={
                "presolve": politica.presolve,
                "time_limit": politica.segundos_por_nivel,
            },
        )
    segundos = time.perf_counter() - inicio
    estado, codigo = estado_de(int(relajada.status), str(relajada.message))
    iteraciones = None if relajada.get("nit") is None else int(relajada.nit)
    if estado in TERMINALES_DE_LA_RELAJACION and estado != EstadoSolver.OPTIMAL:
        # La relajacion acota al MILP: sin solucion lineal no hay una entera; y un limite de la
        # relajacion es un limite de la etapa.
        return ResultadoSolver(
            estado,
            codigo,
            str(relajada.message),
            None,
            None,
            None,
            None,
            0,
            segundos,
            RELAJACION_LP_ENTERA,
            iteraciones,
        )
    dual = None
    if estado == EstadoSolver.OPTIMAL:
        x = np.asarray(relajada.x, dtype=np.float64)
        dual = duales(relajada, c, A_ub, b_ub, A_eq, b_eq, superior)
        if np.all(np.abs(x - np.rint(x)) <= TOLERANCIA_ENTERA_LP):
            objetivo = float(relajada.fun)
            return ResultadoSolver(
                EstadoSolver.OPTIMAL,
                codigo,
                str(relajada.message),
                x,
                objetivo,
                objetivo,
                0.0,
                0,
                segundos,
                RELAJACION_LP_ENTERA,
                iteraciones,
                dual,
            )
    restante = max(1.0, politica.segundos_por_nivel - segundos)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        res = milp(
            c,
            integrality=np.ones(n, dtype=np.uint8),
            bounds=Bounds(np.zeros(n), superior),
            constraints=[LinearConstraint(A, lb, ub)] if A.shape[0] else None,
            options={
                "time_limit": restante,
                "mip_rel_gap": politica.mip_rel_gap,
                "presolve": politica.presolve,
                "disp": False,
            },
        )
    segundos = time.perf_counter() - inicio
    estado, codigo = estado_de(int(res.status), str(res.message))
    return ResultadoSolver(
        estado=estado,
        codigo_highs=codigo,
        mensaje=str(res.message),
        x=None if res.x is None else np.asarray(res.x, dtype=np.float64),
        objetivo=None if res.fun is None else float(res.fun),
        cota=None if res.get("mip_dual_bound") is None else float(res.mip_dual_bound),
        gap=None if res.get("mip_gap") is None else float(res.mip_gap),
        nodos=None if res.get("mip_node_count") is None else int(res.mip_node_count),
        segundos=segundos,
        metodo=BRANCH_AND_BOUND,
        iteraciones=iteraciones,
        duales=dual,
    )
