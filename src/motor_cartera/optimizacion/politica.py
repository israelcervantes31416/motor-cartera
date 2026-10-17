"""optimization-public-v1: que se optimiza, con que precision, con que solver y cuando se publica un
plan, como datos con su hash. Todo se fijo antes de medir nada y no se ajusta despues.

La configuracion de un escenario (sus pools, sus capacidades, su admisibilidad y sus restricciones)
no es la politica: es de cada escenario, con su propio hash. La politica dice como se lee esa
configuracion y que significa el resultado:

- El valor de oportunidad de una cuenta es su `recuperacion_positiva_esperada_30d` de
  predictive/model-v1, en centavos exactos. Ya es la media incondicional de max(recuperacion neta,
  0): no se multiplica por ninguna probabilidad.
- Cuatro objetivos lexicograficos, resueltos por etapas: el valor, despues la probabilidad de
  contacto (solo como desempate), despues el menor esfuerzo declarado y al final un orden canonico
  de las cuentas y de los pools. Cada etapa fija exactamente el optimo de la anterior; ninguna
  constante gigante los mezcla.
- Un plan se publica solo si el solver demuestra la optimalidad de cada etapa.

Una prediccion es la de un resultado observado bajo la operacion historica: el plan optimo es el de
un modelo de asignacion de recursos a oportunidades predichas, no la demostracion de que el canal
asignado cause el resultado predicho. Este modulo no sabe de la base.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum

from motor_cartera.analytics.catalogo import canonico
from motor_cartera.atraso import TRAMOS_ATRASO
from motor_cartera.decision.reglas import Prioridad
from motor_cartera.lifecycle.reglas import Canal

VERSION_MOTOR = "optimization/v1"
"""La version del motor: como se lee el universo, se resuelve y se publica un plan."""
VERSION_POLITICA = "optimization-public-v1"
ALGORITMO = "SCIPY_HIGHS_MILP/v1"
"""scipy.optimize.milp, que resuelve con HiGHS (el que trae SciPy): ningun otro backend."""

AVISO = (
    "Este es el optimo del modelo de asignacion declarado: asigna capacidad escasa a oportunidades "
    "predichas bajo la politica historica, con los supuestos de su configuracion. No demuestra que "
    "el canal asignado cause el resultado predicho, no estima un efecto incremental y no ejecuta "
    "ninguna accion: es un plan de soporte a decision, en sombra. Un optimo matematico de "
    "supuestos incorrectos sigue siendo una mala decision de negocio."
)

CANALES_OPERATIVOS: tuple[Canal, ...] = (Canal.DIGITAL, Canal.TELEFONICA, Canal.CAMPO)
"""Los canales a los que se asigna capacidad. OTRO no se optimiza, ni un medio (SMS, llamada...):
no hay efectos por canal ni por medio que lo justifiquen."""
CANALES_DE_DECISION: tuple[Canal, ...] = tuple(Canal)
"""Lo que puede decir decision/v2 de una cuenta: cualquier canal del catalogo del lifecycle."""


class TipoAlcance(StrEnum):
    """Que cuentas puede atender un pool."""

    GLOBAL = "GLOBAL"
    """Cualquiera de su canal."""
    BASE = "BASE"
    """Las de una base operativa: la de territorial/v2 de la cadena del ruteo declarado."""
    GESTOR_CAMPO = "GESTOR_CAMPO"
    """Las paradas PROGRAMADA de un gestor de campo en el ruteo/v2 declarado: un pool por gestor."""


ALCANCE_DEL_CANAL: dict[Canal, frozenset[TipoAlcance]] = {
    Canal.DIGITAL: frozenset({TipoAlcance.GLOBAL, TipoAlcance.BASE}),
    Canal.TELEFONICA: frozenset({TipoAlcance.GLOBAL, TipoAlcance.BASE}),
    Canal.CAMPO: frozenset({TipoAlcance.GESTOR_CAMPO}),
}
"""CAMPO solo se asigna por gestor, y un pool de gestor solo es de CAMPO."""


class TipoRestriccion(StrEnum):
    MIN_CUENTAS = "MIN_CUENTAS"
    MAX_CUENTAS = "MAX_CUENTAS"
    MIN_PARTICIPACION_BPS = "MIN_PARTICIPACION_BPS"
    MAX_PARTICIPACION_BPS = "MAX_PARTICIPACION_BPS"


MINIMOS = frozenset({TipoRestriccion.MIN_CUENTAS, TipoRestriccion.MIN_PARTICIPACION_BPS})
PARTICIPACIONES = frozenset(
    {TipoRestriccion.MIN_PARTICIPACION_BPS, TipoRestriccion.MAX_PARTICIPACION_BPS}
)
BPS = 10_000
"""Un punto base es una diezmilesima: 10,000 bps son el 100 %."""


class Dimension(StrEnum):
    """Las dimensiones operacionales publicas de una restriccion de cobertura. Ninguna es un dato
    personal ni un identificador: ni una cuenta, ni un CLIENTE_UNICO, ni un actor_ref, ni un gestor
    (un gestor es un pool de CAMPO, no una dimension)."""

    PRODUCTO = "PRODUCTO"
    """El producto del snapshot del corte."""
    ATRASO_BUCKET = "ATRASO_BUCKET"
    """El tramo de dias de atraso del snapshot del corte."""
    PRIORIDAD_DECISION = "PRIORIDAD_DECISION"
    """La prioridad final de decision/v2."""
    BASE = "BASE"
    """La base operativa de territorial/v2, por la cadena del ruteo declarado."""


PRODUCTOS = ("CONSUMO", "TARJETA", "NOMINA", "AUTOMOTRIZ")
"""El catalogo de productos de los contratos de cartera. No se importa de ahi porque arrastraria
pandas y pandera; una prueba exige que coincidan."""
TRAMOS: tuple[str, ...] = tuple(etiqueta for etiqueta, _, _ in TRAMOS_ATRASO)
PRIORIDADES: tuple[str, ...] = tuple(p.value for p in Prioridad)
VOCABULARIOS: dict[Dimension, tuple[str, ...] | None] = {
    Dimension.PRODUCTO: PRODUCTOS,
    Dimension.ATRASO_BUCKET: TRAMOS,
    Dimension.PRIORIDAD_DECISION: PRIORIDADES,
    Dimension.BASE: None,
}
"""Los valores que acepta cada dimension. Las bases son las de la version de bases de la cadena
del ruteo: se comprueban al ejecutar."""


class EstadoCuenta(StrEnum):
    ASIGNADA = "ASIGNADA"
    NO_SELECCIONADA = "NO_SELECCIONADA"
    NO_ELEGIBLE = "NO_ELEGIBLE"


class Motivo(StrEnum):
    """Por que una cuenta quedo como quedo, en un catalogo cerrado de hechos. Ninguno dice que una
    restriccion individual fue la causa de que una cuenta elegible no quedara: el solver no lo
    demuestra."""

    EN_SOLUCION_OPTIMA = "EN_SOLUCION_OPTIMA"
    """ASIGNADA: el plan optimo publicado la asigna a un pool."""
    FUERA_DE_SOLUCION_OPTIMA = "FUERA_DE_SOLUCION_OPTIMA"
    """NO_SELECCIONADA: era elegible y el plan optimo publicado no la asigna."""
    PREDICCION_CON_IMPUTACION = "PREDICCION_CON_IMPUTACION"
    """NO_ELEGIBLE: su prediccion es CON_IMPUTACION y la configuracion no las admite."""
    SIN_CANAL_ADMISIBLE = "SIN_CANAL_ADMISIBLE"
    """NO_ELEGIBLE: la matriz de admisibilidad no permite ningun canal operativo para el canal que
    le recomendo decision/v2."""
    CONFIG_EXCLUYE_CANAL = "CONFIG_EXCLUYE_CANAL"
    """NO_ELEGIBLE: sus canales admisibles no tienen ningun pool activo en la configuracion."""
    CAMPO_NO_PROGRAMADA = "CAMPO_NO_PROGRAMADA"
    """NO_ELEGIBLE: solo CAMPO tenia pools para ella, y no tiene una parada PROGRAMADA en el ruteo
    declarado (SIN_TIEMPO, NO_ENRUTABLE, NO_ENRUTABLE_BASE o ninguna parada)."""
    SIN_POOL_APLICABLE = "SIN_POOL_APLICABLE"
    """NO_ELEGIBLE: sus canales admisibles tienen pools, pero ninguno la alcanza: otra base, sin
    base territorial, o un gestor sin pool."""


MOTIVOS_NO_ELEGIBLE = (
    Motivo.PREDICCION_CON_IMPUTACION,
    Motivo.SIN_CANAL_ADMISIBLE,
    Motivo.CONFIG_EXCLUYE_CANAL,
    Motivo.CAMPO_NO_PROGRAMADA,
    Motivo.SIN_POOL_APLICABLE,
)

EXPLICACION_DEL_MOTIVO: dict[Motivo, str] = {
    Motivo.EN_SOLUCION_OPTIMA: "El plan optimo publicado la asigna a un pool.",
    Motivo.FUERA_DE_SOLUCION_OPTIMA: "Era elegible y el plan optimo publicado no la asigna; no se "
    "atribuye a una restriccion individual.",
    Motivo.PREDICCION_CON_IMPUTACION: "Su prediccion es CON_IMPUTACION y la configuracion no "
    "admite predicciones con imputacion.",
    Motivo.SIN_CANAL_ADMISIBLE: "La matriz de admisibilidad de la configuracion no permite ningun "
    "canal operativo para el canal que le recomendo decision/v2.",
    Motivo.CONFIG_EXCLUYE_CANAL: "Sus canales admisibles no tienen ningun pool activo en la "
    "configuracion.",
    Motivo.CAMPO_NO_PROGRAMADA: "CAMPO es admisible para ella y tiene pools, pero no tiene una "
    "parada PROGRAMADA en el ruteo/v2 declarado, y ningun otro pool admisible la alcanza: el plan "
    "no crea visitas ni convierte SIN_TIEMPO, NO_ENRUTABLE o NO_ENRUTABLE_BASE en CAMPO.",
    Motivo.SIN_POOL_APLICABLE: "Sus canales admisibles tienen pools, pero ninguno la alcanza: "
    "otra base, sin base territorial, o un gestor sin pool.",
}
"""Lo que quiere decir cada motivo, para una persona."""


class EstadoSolver(StrEnum):
    """Como termino el solver, sin convertir un estado en otro: un TIME_LIMIT nunca es OPTIMAL."""

    OPTIMAL = "OPTIMAL"
    INFEASIBLE = "INFEASIBLE"
    TIME_LIMIT = "TIME_LIMIT"
    ITERATION_LIMIT = "ITERATION_LIMIT"
    UNBOUNDED = "UNBOUNDED"
    INFEASIBLE_OR_UNBOUNDED = "INFEASIBLE_OR_UNBOUNDED"
    NUMERICAL_ERROR = "NUMERICAL_ERROR"
    """La solucion que devolvio, en aritmetica entera, no cumple una restriccion o no reproduce el
    objetivo que fijo: no se publica."""
    SOLVER_ERROR = "SOLVER_ERROR"
    NO_INVOCADO = "NO_INVOCADO"
    """El prechequeo encontro un error antes de construir el modelo."""


@dataclass(frozen=True)
class PoliticaOptimizacion:
    version: str
    escala_valor: int
    """Centavos por peso: el valor de oportunidad es la recuperacion esperada por 100, exacta."""
    decimales_contacto: int
    """La precision con que v0.15 publica una probabilidad: 8 decimales. El desempate de contacto
    es p_contacto_14d por 10**8, entero."""
    limite_exacto: int
    """El entero mas grande que un double representa exactamente (2**53 - 1): ningun coeficiente,
    cota ni suma posible de una fila puede pasarlo."""
    segundos_por_nivel: float
    """El limite de tiempo de cada etapa lexicografica. Si se agota sin demostrar el optimo, no se
    publica nada."""
    mip_rel_gap: float
    """0: el solver solo termina OPTIMAL cuando su mejor cota alcanza al objetivo."""
    presolve: bool
    reglas: dict[str, str] = field(default_factory=dict)

    def definicion(self) -> dict:
        return {
            "version": self.version,
            "version_motor": VERSION_MOTOR,
            "algoritmo": ALGORITMO,
            "valor": {
                "formula": "valor_oportunidad_centavos = recuperacion_positiva_esperada_30d * "
                f"{self.escala_valor}, exacto (NUMERIC con 2 decimales)",
                "fuente": "predictive/model-v1, el scoring declarado",
                "no_se_multiplica_por": [
                    "p_recuperacion_positiva_30d",
                    "p_contacto_14d",
                    "p_promesa_30d",
                ],
            },
            "objetivos": [
                {"nivel": 1, "sentido": "MAXIMIZAR", "expresion": "sum_i valor_i * z_i"},
                {
                    "nivel": 2,
                    "sentido": "MAXIMIZAR",
                    "expresion": "sum_i p_contacto_e8_i * z_i",
                    "fijado": "nivel 1 = su optimo, exacto",
                },
                {
                    "nivel": 3,
                    "sentido": "MINIMIZAR",
                    "expresion": "sum_ip unidades_p * x_ip",
                    "fijado": "niveles 1 y 2 = sus optimos, exactos",
                },
                {
                    "nivel": 4,
                    "sentido": "MINIMIZAR",
                    "expresion": "sum_ip (rango_i * (pools + 1) + (cuentas - rango_i) * orden_p) "
                    "* x_ip",
                    "fijado": "niveles 1, 2 y 3 = sus optimos, exactos",
                    "rango": "rango_i, la posicion de la cuenta por cuenta_id ascendente en el "
                    "universo; orden_p, la del pool por codigo ascendente. Entre cuentas "
                    "empatadas en un mismo pool va la de menor cuenta_id; entre permutaciones de "
                    "los mismos pools, la de menor cuenta_id toma el pool de menor orden. No es "
                    "una feature y no cambia el valor economico.",
                },
            ],
            "precision": {
                "dinero": "centavos enteros",
                "decimales_contacto": self.decimales_contacto,
                "unidades_recurso": "enteros positivos, abstractos, no dinero",
                "limite_exacto": self.limite_exacto,
            },
            "canales": [c.value for c in CANALES_OPERATIVOS],
            "alcances": {
                c.value: sorted(a.value for a in ALCANCE_DEL_CANAL[c]) for c in CANALES_OPERATIVOS
            },
            "dimensiones": {d.value: list(v) if v else None for d, v in VOCABULARIOS.items()},
            "restricciones": [t.value for t in TipoRestriccion],
            "solver": {
                "algoritmo": ALGORITMO,
                "metodo": "Cada nivel: su relajacion lineal con HiGHS (punto interior con "
                "crossover, scipy.optimize.linprog); si su vertice optimo es entero, es el "
                "optimo del MILP (la relajacion lo acota y la solucion entera la alcanza: cota = "
                "objetivo, gap 0); si no, branch-and-bound de HiGHS (scipy.optimize.milp).",
                "tolerancia_entera_lp": 1e-9,
                "segundos_por_nivel": self.segundos_por_nivel,
                "mip_rel_gap": self.mip_rel_gap,
                "presolve": self.presolve,
            },
            "reglas": dict(sorted(self.reglas.items())),
        }


PUBLIC_V1 = PoliticaOptimizacion(
    version=VERSION_POLITICA,
    escala_valor=100,
    decimales_contacto=8,
    limite_exacto=2**53 - 1,
    segundos_por_nivel=1_800.0,
    mip_rel_gap=0.0,
    presolve=False,
    reglas={
        "variables": "x[i,p] en {0,1} por cada par elegible (cuenta i, pool p); z[i] = sum_p "
        "x[i,p]. No se crea ninguna variable de un par imposible.",
        "una_asignacion": "sum_p x[i,p] <= 1 para cada cuenta.",
        "capacidad_pool": "sum_i x[i,p] <= capacidad efectiva de p. CAMPO: min(capacidad "
        "declarada, paradas PROGRAMADA de su gestor); sin capacidad declarada, las PROGRAMADA.",
        "total_cuentas": "sum_i z[i] <= max_cuentas_atendidas, si se declara.",
        "presupuesto": "sum_ip unidades_p * x[i,p] <= max_unidades_recurso, si se declara. Las "
        "unidades son esfuerzo abstracto, no dinero.",
        "cobertura": "MIN_CUENTAS: sum_{i en g} z[i] >= k. MAX_CUENTAS: <= k. "
        "MIN_PARTICIPACION_BPS: 10000 * sum_{i en g} z[i] >= b * sum_i z[i]. "
        "MAX_PARTICIPACION_BPS: <= . Todas lineales, con coeficientes enteros.",
        "redondeo": "Resuelta a conteos con N cuentas atendidas, una participacion minima exige "
        "ceil(b * N / 10000) cuentas del grupo y una maxima permite floor(b * N / 10000), en "
        "aritmetica entera; es exactamente la restriccion lineal, porque el conteo es entero.",
        "elegibilidad": "Un par (i, p) es elegible si el pool esta activo, su canal es admisible "
        "para el canal que decision/v2 le recomendo a i segun la matriz de la configuracion, la "
        "prediccion de i es COMPLETA o la configuracion admite CON_IMPUTACION, y el alcance lo "
        "alcanza: GLOBAL siempre; BASE si la base territorial de i es la del pool; GESTOR_CAMPO si "
        "i tiene una parada PROGRAMADA de ese gestor en el ruteo declarado.",
        "campo": "CAMPO solo elige un subconjunto de las paradas PROGRAMADA que ruteo/v2 ya "
        "demostro factibles, con su gestor, su base y su orden relativo: no agrega cuentas a una "
        "ruta, no convierte SIN_TIEMPO en CAMPO, no cambia gestor, base ni orden, y no publica "
        "geometria ni ETA nuevas. Quitar paradas de una ruta factible no la vuelve infactible.",
        "reduccion": "Exacta por dominancia: dos cuentas elegibles con la misma firma de "
        "restricciones (los mismos pools elegibles y las mismas pertenencias a cada grupo de "
        "cobertura activo) tienen las mismas columnas salvo el objetivo. Dentro de cada clase, en "
        "orden canonico (valor desc, p_contacto desc, cuenta_id asc), se retienen las primeras "
        "u[s], con u[s] el minimo de cotas seguras: su tamano, la suma de las capacidades de sus "
        "pools, max_cuentas_atendidas, cada MAX_CUENTAS de un grupo que la contiene, el "
        "presupuesto entre las unidades minimas de sus pools y cada MAX_PARTICIPACION_BPS sobre el "
        "maximo de cuentas atendibles. Nunca un top N por score.",
        "optimalidad": "Cada etapa se publica solo si HiGHS demuestra su optimo: su relajacion "
        "lineal termina Optimal con un vertice entero (la cota es el objetivo), o su "
        "branch-and-bound termina Optimal con mip_rel_gap = 0; y si la solucion, redondeada a "
        "enteros, cumple cada restriccion en aritmetica entera y reproduce exactamente los "
        "objetivos fijados. Cualquier otro estado no publica: TIME_LIMIT es "
        "OPTIMO_NO_DEMOSTRADO, INFEASIBLE es INFACTIBLE con su diagnostico.",
        "fijacion_por_dualidad": "Entre etapas, con los multiplicadores de la relajacion de una "
        "etapa (cualquiera del signo correcto) se calculan sus costos reducidos r y su cota "
        "lagrangiana L, con su cota de error de punto flotante. Por dualidad debil, toda solucion "
        "que alcanza el optimo entero z de la etapa deja en 0 cada variable con r > z - L y en 1 "
        "cada una con -r > z - L: esas quedan fijas en las etapas siguientes, que solo deciden "
        "entre los optimos de la anterior. La solucion se verifica en enteros contra todas las "
        "filas en cada etapa.",
        "infactibilidad": "Una configuracion infactible no es un error interno: la ejecucion "
        "termina FALLIDA con resultado INFACTIBLE, su prechequeo o un diagnostico de relajacion "
        "(un modelo auxiliar que minimiza la suma de holguras enteras de las restricciones "
        "declaradas). Ese diagnostico es una relajacion minima posible, no un IIS.",
        "determinismo": "El modelo se construye en orden canonico (cuentas por cuenta_id, pools "
        "por codigo), sin depender del orden fisico de las tablas: las mismas entradas, la misma "
        "configuracion, la misma politica y la misma huella del solver dan el mismo plan y su "
        "hash. Los valores optimos de las cuatro etapas son unicos; entre plataformas distintas, "
        "solo una solucion empatada en las cuatro podria cambiar.",
        "no_causal": "El valor es la recuperacion positiva esperada bajo la politica historica, "
        "no un beneficio incremental de contactar; p_contacto es una prediccion separada, no la "
        "probabilidad de exito del canal asignado; p_promesa y el riesgo (1 - p de recuperacion) "
        "no entran al objetivo.",
    },
)

POLITICAS: dict[str, PoliticaOptimizacion] = {PUBLIC_V1.version: PUBLIC_V1}


def hash_politica(politica: PoliticaOptimizacion = PUBLIC_V1) -> str:
    """SHA-256 de la definicion canonica de la politica."""
    return hashlib.sha256(canonico(politica.definicion())).hexdigest()
