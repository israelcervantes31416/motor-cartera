"""analytics-metrics/public-v1: que mide Collection Analytics, como codigo auditable.

Cada metrica tiene una definicion formal: su codigo, su familia, su unidad, su poblacion, su
numerador y su denominador (si es una razon), su regla temporal, sus fuentes, que significa que no
este disponible, como se agrega, en que conjuntos de dimensiones y en que vistas se publica, si es
dinero, si puede ser negativa y sus limitaciones. Ninguna se calcula con SQL que llegue de afuera:
el motor (`analytics.ejecuciones`) calcula por conjuntos los agregados base de cada universo, y cada
metrica dice de cuales sale (`componentes`).

Los universos de las celdas:

- COHORTE: las cuentas del corte declarado, una vez cada una, con sus dimensiones fijas al corte;
- GESTION: las cuentas de la cohorte con gestiones efectivas en el horizonte, por el canal o el
  medio de esas gestiones;
- CAMPO: las candidatas del plan de campo/v2 declarado;
- RUTEO: las paradas de la ejecucion de ruteo/v2 declarada.

Analytics es descriptivo: ninguna metrica es una prediccion, una probabilidad, una eficiencia ni un
efecto. Una asociacion de la atribucion es operacional, no causal.

Este modulo no sabe de la base. Su forma canonica (JSON con llaves ordenadas, sin espacios,
UTF-8) es lo que firma `hash_catalogo`: cambiar una formula, una fuente, una dimension permitida o
el texto que define una metrica cambia el hash, y con el la firma de cada ejecucion nueva. Una
ejecucion vieja conserva el catalogo y el hash con que se calculo.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum

VERSION_MOTOR = "analytics/v1"
"""El motor: como se arma la cohorte, se leen los dominios, se agrega, se suprime y se publica."""

VERSION_CATALOGO = "analytics-metrics/public-v1"
"""Cambia si cambia cualquier metrica, conjunto de dimensiones o universo de este modulo."""


class Familia(StrEnum):
    CARTERA = "CARTERA"
    RECUPERACION = "RECUPERACION"
    CALIDAD_PAGOS = "CALIDAD_PAGOS"
    GESTION = "GESTION"
    PROMESA = "PROMESA"
    ATRIBUCION = "ATRIBUCION"
    DECISION = "DECISION"
    GEOGRAFIA = "GEOGRAFIA"
    TERRITORIAL = "TERRITORIAL"
    CAMPO = "CAMPO"
    RUTEO = "RUTEO"


class Unidad(StrEnum):
    CUENTAS = "CUENTAS"
    MONEDA = "MONEDA"
    """La unidad monetaria de la fuente (CARTERA y PAGOS): v0.14 no representa monedas."""
    DIAS = "DIAS"
    RAZON = "RAZON"
    MOVIMIENTOS = "MOVIMIENTOS"
    GESTIONES = "GESTIONES"
    VISITAS = "VISITAS"
    PROMESAS = "PROMESAS"
    CUPOS = "CUPOS"
    """Cuentas al dia de capacidad declarada: la unidad de capacidad de campo/v2."""
    RUTAS = "RUTAS"
    PARADAS = "PARADAS"
    METROS = "METROS"
    SEGUNDOS = "SEGUNDOS"


class Tipo(StrEnum):
    VALOR = "VALOR"
    """Un conteo o una suma: `valor` es el agregado; sin numerador ni denominador."""
    RAZON = "RAZON"
    """Numerador entre denominador, los dos publicados; con denominador cero, valor NULL."""


class Fuente(StrEnum):
    """Las entradas que declara un manifiesto. Solo CORTE es obligatoria."""

    CORTE = "CORTE"
    MOTOR_PAGOS = "MOTOR_PAGOS"
    ATRIBUCION = "ATRIBUCION"
    EVALUACION_PROMESAS = "EVALUACION_PROMESAS"
    DECISION_V2 = "DECISION_V2"
    GEOGRAFIA = "GEOGRAFIA"
    TERRITORIAL_V2 = "TERRITORIAL_V2"
    CAMPO_V2 = "CAMPO_V2"
    RUTEO_V2 = "RUTEO_V2"


class Vista(StrEnum):
    ACUMULADA = "ACUMULADA"
    """Todo el horizonte, de fecha_corte a as_of: el resultado acumulado."""
    PERIODO = "PERIODO"
    """Cada periodo de la granularidad (dia, semana ISO o mes) dentro del horizonte."""


class Universo(StrEnum):
    COHORTE = "COHORTE"
    GESTION = "GESTION"
    CAMPO = "CAMPO"
    RUTEO = "RUTEO"


class Conjunto(StrEnum):
    """Los conjuntos de dimensiones: no hay GROUP BY arbitrario."""

    TOTAL = "TOTAL"
    PRODUCTO = "PRODUCTO"
    ATRASO_BUCKET = "ATRASO_BUCKET"
    PRIORIDAD_DECISION = "PRIORIDAD_DECISION"
    CANAL_DECISION = "CANAL_DECISION"
    ENTIDAD = "ENTIDAD"
    MUNICIPIO = "MUNICIPIO"
    BASE = "BASE"
    CANAL_GESTION = "CANAL_GESTION"
    MEDIO_GESTION = "MEDIO_GESTION"
    ESTADO_CAMPO = "ESTADO_CAMPO"
    ESTADO_RUTEO = "ESTADO_RUTEO"
    GESTOR_CAMPO = "GESTOR_CAMPO"
    PRODUCTO_X_ATRASO = "PRODUCTO_X_ATRASO"
    BASE_X_PRIORIDAD = "BASE_X_PRIORIDAD"
    CANAL_X_PRIORIDAD = "CANAL_X_PRIORIDAD"


SIN_BASE = "SIN_BASE"
"""La base de una cuenta que territorial/v2 no asigno, o de una candidata en revision territorial:
una categoria, no un error."""
SIN_GESTOR = "SIN_GESTOR"
"""El gestor de una candidata de campo que no quedo ASIGNADA."""
SIN_MEDIO = "SIN_MEDIO"
"""El medio de una gestion que no lo declaro: el medio es opcional en lifecycle/v1."""


@dataclass(frozen=True)
class DefinicionConjunto:
    codigo: Conjunto
    claves: tuple[str, ...]
    """Las llaves de `dimensiones`, en orden canonico."""
    descripcion: str
    universos: dict[Universo, tuple[Fuente, ...]]
    """En que universos existe, y que entradas necesita en cada uno para formar sus celdas."""
    padres: tuple[Conjunto, ...] = ()
    """Los conjuntos cuyas celdas son la suma de las suyas: los grupos de la supresion
    complementaria, ademas del TOTAL."""

    def definicion(self) -> dict:
        return {
            "codigo": self.codigo.value,
            "claves": list(self.claves),
            "descripcion": self.descripcion,
            "universos": {
                u.value: [f.value for f in fuentes] for u, fuentes in self.universos.items()
            },
            "padres": [p.value for p in self.padres],
        }


C, D, T = Fuente.CORTE, Fuente.DECISION_V2, Fuente.TERRITORIAL_V2
COH, GES, CAM, RUT = Universo.COHORTE, Universo.GESTION, Universo.CAMPO, Universo.RUTEO

CONJUNTOS: dict[Conjunto, DefinicionConjunto] = {
    d.codigo: d
    for d in (
        DefinicionConjunto(
            Conjunto.TOTAL,
            (),
            "Sin desglose: el universo entero. Se publica sin supresion.",
            {COH: (), CAM: (), RUT: ()},
        ),
        DefinicionConjunto(
            Conjunto.PRODUCTO,
            ("producto",),
            "El PRODUCTO del snapshot de la cuenta en el corte.",
            {COH: (C,)},
        ),
        DefinicionConjunto(
            Conjunto.ATRASO_BUCKET,
            ("atraso_bucket",),
            "El tramo de dias de atraso del snapshot en el corte, con las fronteras canonicas de "
            "atraso.TRAMOS_ATRASO: 0, 1-30, 31-60, 61-90 y 91+.",
            {COH: (C,)},
        ),
        DefinicionConjunto(
            Conjunto.PRIORIDAD_DECISION,
            ("prioridad_decision",),
            "La prioridad_final de decision/v2: en la cohorte, la de la decision declarada del "
            "corte; en campo y en ruteo, la que traen la asignacion y la parada, que es la misma.",
            {COH: (D,), CAM: (Fuente.CAMPO_V2,), RUT: (Fuente.RUTEO_V2,)},
        ),
        DefinicionConjunto(
            Conjunto.CANAL_DECISION,
            ("canal_decision",),
            "El canal_recomendado de la decision/v2 declarada del corte.",
            {COH: (D,)},
        ),
        DefinicionConjunto(
            Conjunto.ENTIDAD,
            ("entidad",),
            "La cve_entidad del snapshot en el corte (la que resolvio la fuente).",
            {COH: (C,)},
        ),
        DefinicionConjunto(
            Conjunto.MUNICIPIO,
            ("municipio",),
            "La clave municipal del snapshot en el corte, cve_entidad || cve_municipio (la que "
            "resolvio la fuente, no la espacial).",
            {COH: (C,)},
            (Conjunto.ENTIDAD,),
        ),
        DefinicionConjunto(
            Conjunto.BASE,
            ("base",),
            "El codigo de la base: en la cohorte, la que asigno la territorial/v2 declarada "
            f"({SIN_BASE} si no la asigno); en campo, la de la asignacion ({SIN_BASE} en revision "
            "territorial); en ruteo, la base de la ruta.",
            {COH: (T,), CAM: (Fuente.CAMPO_V2,), RUT: (Fuente.RUTEO_V2,)},
        ),
        DefinicionConjunto(
            Conjunto.CANAL_GESTION,
            ("canal_gestion",),
            "El canal de las gestiones efectivas en el horizonte: una cuenta esta en la celda de "
            "cada canal en que tuvo una gestion.",
            {GES: ()},
        ),
        DefinicionConjunto(
            Conjunto.MEDIO_GESTION,
            ("medio_gestion",),
            f"El medio de las gestiones efectivas en el horizonte; {SIN_MEDIO} si no se declaro.",
            {GES: ()},
        ),
        DefinicionConjunto(
            Conjunto.ESTADO_CAMPO,
            ("estado_campo",),
            "El estado de la candidata en el plan de campo/v2 declarado.",
            {CAM: (Fuente.CAMPO_V2,)},
        ),
        DefinicionConjunto(
            Conjunto.ESTADO_RUTEO,
            ("estado_ruteo",),
            "El estado de la parada en la ejecucion de ruteo/v2 declarada.",
            {RUT: (Fuente.RUTEO_V2,)},
        ),
        DefinicionConjunto(
            Conjunto.GESTOR_CAMPO,
            ("base", "gestor_campo"),
            "El codigo del gestor de campo y su base: en campo, el de la asignacion "
            f"({SIN_GESTOR} si no quedo ASIGNADA) y cada gestor de la configuracion; en ruteo, el "
            "de la ruta. Es un recurso planificado, no el actor_ref del lifecycle.",
            {CAM: (Fuente.CAMPO_V2,), RUT: (Fuente.RUTEO_V2,)},
            (Conjunto.BASE,),
        ),
        DefinicionConjunto(
            Conjunto.PRODUCTO_X_ATRASO,
            ("producto", "atraso_bucket"),
            "PRODUCTO y ATRASO_BUCKET juntos.",
            {COH: (C,)},
            (Conjunto.PRODUCTO, Conjunto.ATRASO_BUCKET),
        ),
        DefinicionConjunto(
            Conjunto.BASE_X_PRIORIDAD,
            ("base", "prioridad_decision"),
            "BASE y PRIORIDAD_DECISION juntos.",
            {COH: (T, D), CAM: (Fuente.CAMPO_V2,)},
            (Conjunto.BASE, Conjunto.PRIORIDAD_DECISION),
        ),
        DefinicionConjunto(
            Conjunto.CANAL_X_PRIORIDAD,
            ("canal_decision", "prioridad_decision"),
            "CANAL_DECISION y PRIORIDAD_DECISION juntos.",
            {COH: (D,)},
            (Conjunto.CANAL_DECISION, Conjunto.PRIORIDAD_DECISION),
        ),
    )
}


def universo_de_la_celda(universo_metrica: Universo, conjunto: Conjunto) -> Universo:
    """El universo de las celdas de una metrica en un conjunto: el de la metrica, salvo los
    conjuntos de las gestiones (canal y medio), cuyas celdas son de cuentas con gestiones."""
    if conjunto in (Conjunto.CANAL_GESTION, Conjunto.MEDIO_GESTION):
        return Universo.GESTION
    return universo_metrica


@dataclass(frozen=True)
class Metrica:
    codigo: str
    nombre: str
    descripcion: str
    """Que mide, en una o dos frases, sin formulas."""
    familia: Familia
    unidad: Unidad
    tipo: Tipo
    universo: Universo
    componentes: tuple[str, ...]
    """Los agregados base de los que sale, por su nombre: (agregado,) o (numerador, denominador)."""
    formula: str
    numerador: str | None
    denominador: str | None
    poblacion: str
    regla_temporal: str
    fuentes: tuple[Fuente, ...]
    nulo: str
    """Cuando no esta disponible, o cuando su valor es NULL."""
    agregacion: str
    aditiva: bool
    """Si suma exactamente entre celdas excluyentes y entre periodos."""
    conjuntos: tuple[Conjunto, ...]
    vistas: tuple[Vista, ...]
    monetaria: bool
    puede_ser_negativa: bool
    cobertura_de_pagos: bool
    """Si en cada periodo exige la cobertura de pagos conocida a known_at."""
    limitaciones: tuple[str, ...]

    def definicion(self) -> dict:
        return {
            "codigo": self.codigo,
            "nombre": self.nombre,
            "descripcion": self.descripcion,
            "familia": self.familia.value,
            "unidad": self.unidad.value,
            "tipo": self.tipo.value,
            "universo": self.universo.value,
            "componentes": list(self.componentes),
            "formula": self.formula,
            "numerador": self.numerador,
            "denominador": self.denominador,
            "poblacion": self.poblacion,
            "regla_temporal": self.regla_temporal,
            "fuentes": [f.value for f in self.fuentes],
            "nulo": self.nulo,
            "agregacion": self.agregacion,
            "aditiva": self.aditiva,
            "conjuntos": [c.value for c in self.conjuntos],
            "vistas": [v.value for v in self.vistas],
            "monetaria": self.monetaria,
            "puede_ser_negativa": self.puede_ser_negativa,
            "cobertura_de_pagos": self.cobertura_de_pagos,
            "limitaciones": list(self.limitaciones),
        }


# --- lo comun de cada familia ---------------------------------------------------------------------

COHORTE_CONJUNTOS = (
    Conjunto.TOTAL,
    Conjunto.PRODUCTO,
    Conjunto.ATRASO_BUCKET,
    Conjunto.PRIORIDAD_DECISION,
    Conjunto.CANAL_DECISION,
    Conjunto.ENTIDAD,
    Conjunto.MUNICIPIO,
    Conjunto.BASE,
    Conjunto.PRODUCTO_X_ATRASO,
    Conjunto.BASE_X_PRIORIDAD,
    Conjunto.CANAL_X_PRIORIDAD,
)
"""Los conjuntos de la cohorte: dimensiones fijas al corte (o a las ejecuciones del mismo corte)."""

AMBAS = (Vista.ACUMULADA, Vista.PERIODO)
SOLO_ACUMULADA = (Vista.ACUMULADA,)

POBLACION_COHORTE = (
    "Las cuentas de la cohorte: los snapshots del corte declarado, una vez cada una, con las "
    "dimensiones que tenian en el corte."
)
FOTO_DEL_CORTE = (
    "La foto del corte: el snapshot de cada cuenta en fecha_corte. No cambia con as_of ni con "
    "known_at: el corte se conocia a known_at (creado_en <= known_at), o la ejecucion no se abre."
)
NUNCA_NO_DISPONIBLE = "Nunca NO_DISPONIBLE: el corte es la entrada obligatoria."
SIN_DENOMINADOR = "Con denominador cero, valor NULL (estado DISPONIBLE, numerador y denominador 0)."

REGLA_PAGOS = (
    "Los movimientos de las corridas de motor-pagos/v1 declaradas, recibidos (fecha_recepcion, "
    "hora local de la fuente) desde el inicio de fecha_corte hasta el final de as_of en la zona de "
    "la ejecucion; en PERIODO, en el dia, la semana ISO o el mes de su recepcion. Un PAGO esta "
    "anulado si el REVERSO que lo anula esta en una corrida declarada y se recibio antes del final "
    "de as_of: un reverso posterior no existe para este as_of."
)
POBLACION_PAGOS = (
    "Los movimientos economicos canonicos de motor-pagos/v1 conciliados con una cuenta de la "
    "cohorte por su cuenta_canonica_id, nunca por el texto de CLIENTE_UNICO."
)
NULO_PAGOS = (
    "NO_DISPONIBLE si no se declaro ninguna interpretacion de pagos, o si algun dia del periodo no "
    "tiene cobertura de pagos conocida a known_at: ningun archivo de pagos aceptado lo cubre, o la "
    "interpretacion declarada de su mes no vio todos sus pagos observados. Nunca un cero inventado."
)
SEMANTICA_MOTOR = (
    "Reutiliza la interpretacion economica de motor-pagos/v1: un PAGO no anulado suma, un "
    "POSIBLE_REVERSO resta, y un REVERSO y el pago que anula suman cero juntos y no entran."
)

REGLA_LIFECYCLE = (
    "Gestiones efectivas: ocurridas desde el inicio de fecha_corte hasta el final de as_of, "
    "registradas hasta known_at y sin una anulacion que haya ocurrido antes del final de as_of y "
    "se haya registrado hasta known_at. En PERIODO, en el periodo de su ocurrido_en, en la zona de "
    "la ejecucion."
)
NULO_LIFECYCLE = (
    "Nunca NO_DISPONIBLE: el lifecycle es el registro de Motor Cartera, y 0 dice que no se conocia "
    "ninguna a known_at."
)

REGLA_PROMESA = (
    "Promesas creadas (PROMESA_CREADA) desde el inicio de fecha_corte hasta el final de as_of, "
    "registradas hasta known_at, de una gestion efectiva en el horizonte. En PERIODO, en el "
    "periodo de su creacion."
)
REGLA_EVALUACION = (
    REGLA_PROMESA + " Su estado es el de la EjecucionEvaluacionPromesas declarada, con el mismo "
    "as_of y la misma zona: analytics no reevalua ninguna promesa."
)
NULO_EVALUACION = "NO_DISPONIBLE si no se declaro una evaluacion de promesas."

REGLA_ATRIBUCION = (
    "Los PAGO de la cohorte recibidos en el horizonte, con su resultado en la ejecucion de "
    "atribucion/v1 declarada para su mes, que leyo exactamente la interpretacion de pagos "
    "declarada. Un monto cuenta solo si el pago no esta anulado al final de as_of."
)
NULO_ATRIBUCION = (
    "NO_DISPONIBLE si falta la atribucion declarada de un mes del periodo, o si el periodo no "
    "tiene cobertura de pagos."
)
NO_CAUSAL = "Asociacion operacional, no causalidad: no dice que una gestion produjo un pago."

POBLACION_CAMPO = "Las candidatas de campo del plan de campo/v2 declarado: una vez cada una."
REGLA_PLAN = (
    "El plan de una sola fecha de operacion, que cae entre fecha_corte y as_of y se conocia a "
    "known_at. Planificado, no ejecutado: no dice que una visita haya ocurrido."
)
POBLACION_RUTEO = "Las paradas de la ejecucion de ruteo/v2 declarada: una por cuenta ASIGNADA."
POBLACION_RUTAS = "Las rutas de la ejecucion de ruteo/v2 declarada: una por gestor con cuentas."
NULO_PLAN = "NO_DISPONIBLE si no se declaro la entrada de su familia."

DESCRIPCION: dict[str, str] = {
    # cartera
    "cartera.cuentas": "Cuantas cuentas tiene la cohorte, o la celda: las del corte declarado.",
    "cartera.saldo_total": (
        "El saldo total que las cuentas tenian en el corte, sumado: la foto inicial de la cohorte."
    ),
    "cartera.saldo_total_promedio": "El saldo total del corte por cuenta, en promedio.",
    "cartera.dias_atraso_promedio": (
        "Los dias de atraso que las cuentas tenian en el corte, en promedio."
    ),
    "cartera.saldo_atrasado_suma": (
        "El saldo atrasado del corte, sumado solo sobre las cuentas que lo traen: un vacio no se "
        "cuenta como cero."
    ),
    "cartera.saldo_atrasado_cuentas_con_dato": (
        "Cuantas cuentas traen el saldo atrasado en el corte, aunque sea 0."
    ),
    "cartera.saldo_atrasado_cobertura": (
        "Que parte de las cuentas trae el saldo atrasado en el corte: la cobertura del dato, no su "
        "valor."
    ),
    # recuperacion
    "recuperacion.neta": (
        "La recuperacion economica de la cohorte en el horizonte, con la semantica de "
        "motor-pagos/v1 al final de as_of: los pagos vigentes mas los posibles reversos. Puede ser "
        "negativa."
    ),
    "recuperacion.pagos_vigentes": (
        "Los PAGO de la cohorte en el horizonte que ningun reverso conocido al final de as_of "
        "anulo."
    ),
    "recuperacion.posibles_reversos": (
        "Los negativos que motor-pagos/v1 no pudo enlazar con un pago: restan en la neta, sin "
        "anular ningun pago."
    ),
    "recuperacion.movimientos_economicos": (
        "Cuantos movimientos economicos canonicos de la cohorte se recibieron en el horizonte, de "
        "cualquier tipo."
    ),
    "recuperacion.pagos_anulados": (
        "Cuantos PAGO de la cohorte en el horizonte anulo un reverso recibido antes del final de "
        "as_of."
    ),
    "recuperacion.cuentas_con_neto_positivo": (
        "Cuantas cuentas tienen una recuperacion neta mayor que cero en el periodo."
    ),
    "recuperacion.sobre_saldo_inicial": (
        "La recuperacion neta dividida entre el saldo total que esas mismas cuentas tenian en el "
        "corte. No es una tasa de recuperacion contable."
    ),
    # calidad de los pagos
    "calidad_pagos.movimientos_sin_cuenta": (
        "Cuantos movimientos del horizonte no se conciliaron con ninguna cuenta: no son "
        "recuperacion de la cohorte, y se informan aparte."
    ),
    "calidad_pagos.monto_sin_cuenta": "El monto neto de los movimientos del horizonte sin cuenta.",
    "calidad_pagos.movimientos_fuera_de_cohorte": (
        "Cuantos movimientos del horizonte son de una cuenta que no esta en el corte declarado."
    ),
    "calidad_pagos.monto_fuera_de_cohorte": (
        "El monto neto de los movimientos del horizonte de cuentas que no estan en el corte."
    ),
    # gestion
    "gestion.gestiones": (
        "Cuantas gestiones efectivas de la cohorte ocurrieron en el horizonte y se conocian a "
        "known_at."
    ),
    "gestion.cuentas_gestionadas": "Cuantas cuentas tuvieron al menos una gestion efectiva.",
    "gestion.cuentas_contactadas": (
        "Cuantas cuentas tuvieron al menos una gestion efectiva en que se hablo con el titular o "
        "con un tercero."
    ),
    "gestion.contactadas_sobre_gestionadas": (
        "De las cuentas gestionadas, que parte tuvo contacto: contactabilidad descriptiva, no una "
        "eficiencia."
    ),
    "gestion.visitas_campo_registradas": (
        "Cuantas gestiones efectivas registraron una visita de campo realizada. Es lo ejecutado, "
        "no lo planificado."
    ),
    # promesas
    "promesa.creadas": (
        "Cuantas promesas de pago se crearon en el horizonte, de gestiones efectivas."
    ),
    "promesa.monto_prometido": "Cuanto se prometio en esas promesas, sumado.",
    "promesa.cuentas_con_promesa": (
        "Cuantas cuentas tienen al menos una promesa creada en el horizonte."
    ),
    "promesa.evaluadas": "Cuantas de esas promesas tienen su resultado en la evaluacion declarada.",
    "promesa.cumplidas": "Cuantas promesas la evaluacion declarada da por CUMPLIDA.",
    "promesa.parciales": "Cuantas promesas la evaluacion declarada da por PARCIAL.",
    "promesa.incumplidas": "Cuantas promesas la evaluacion declarada da por INCUMPLIDA.",
    "promesa.pendientes": (
        "Cuantas promesas la evaluacion declarada da por PENDIENTE: su fecha limite no llegaba."
    ),
    "promesa.canceladas": "Cuantas promesas la evaluacion declarada da por CANCELADA.",
    "promesa.no_evaluables": (
        "Cuantas promesas la evaluacion declarada no pudo evaluar: los pagos no permitian afirmar "
        "nada."
    ),
    "promesa.sin_evaluacion": (
        "Cuantas promesas creadas no tienen resultado en la evaluacion declarada: llegaron "
        "despues de ella."
    ),
    "promesa.tasa_cumplimiento_total": (
        "De las promesas resueltas (cumplidas, parciales e incumplidas), que parte se cumplio por "
        "completo. Las pendientes, canceladas y no evaluables no entran."
    ),
    "promesa.tasa_cumplimiento_total_o_parcial": (
        "De las promesas resueltas, que parte se cumplio por completo o en parte."
    ),
    # atribucion
    "atribucion.movimientos_evaluados": (
        "Cuantos PAGO de la cohorte en el horizonte tienen resultado en la atribucion declarada."
    ),
    "atribucion.asociacion_unica": (
        "Cuantos de esos pagos tienen exactamente una gestion con contacto antes de ellos en la "
        "ventana: una asociacion operacional, no causal."
    ),
    "atribucion.ambigua": (
        "Cuantos pagos tienen dos o mas gestiones candidatas: atribucion/v1 no elige ninguna, y "
        "analytics tampoco."
    ),
    "atribucion.sin_gestion": "Cuantos pagos no tienen ninguna gestion candidata en la ventana.",
    "atribucion.monto_asociacion_unica": (
        "El monto de los pagos con asociacion unica que siguen vigentes al final de as_of."
    ),
    "atribucion.monto_ambiguo": (
        "El monto de los pagos con atribucion ambigua vigentes al final de as_of, sin repartirlo "
        "entre candidatas."
    ),
    "atribucion.monto_sin_gestion": (
        "El monto de los pagos sin gestion candidata vigentes al final de as_of."
    ),
    # decision
    "decision.cuentas": "Cuantas cuentas tienen su decision en la decision/v2 declarada.",
    "decision.pct_universo": (
        "Que parte de la cohorte cae en la celda: la mezcla de prioridades y canales de la "
        "decision declarada."
    ),
    # geografia
    "geografia.cuentas": "Cuantas cuentas tienen resultado en la geografia declarada.",
    "geografia.validas": "Cuantas cuentas tienen una geografia VALIDA.",
    "geografia.discrepancias": (
        "Cuantas cuentas tienen coordenadas en un municipio distinto del que dice la fuente."
    ),
    "geografia.sin_coordenadas": "Cuantas cuentas no traen coordenadas.",
    "geografia.incompletas": "Cuantas cuentas traen solo una de sus dos coordenadas.",
    "geografia.invalidas": "Cuantas cuentas traen coordenadas fuera de rango o sin sentido.",
    "geografia.fuera_referencia": (
        "Cuantas cuentas tienen coordenadas fuera de la referencia geografica declarada."
    ),
    "geografia.ambiguas": "Cuantas cuentas caen en mas de un municipio de la referencia.",
    "geografia.tasa_valida": "De las cuentas con resultado geografico, que parte es VALIDA.",
    # territorial
    "territorial.cuentas": "Cuantas cuentas tienen resultado en la territorial/v2 declarada.",
    "territorial.asignadas": "Cuantas cuentas quedaron asignadas a una base operativa.",
    "territorial.revision": (
        "Cuantas cuentas quedaron en revision geografica: su geografia no permite asignarlas."
    ),
    "territorial.sin_base": "Cuantas cuentas no tienen ninguna base elegible.",
    "territorial.ambiguas": "Cuantas cuentas empatan entre dos o mas bases.",
    "territorial.fuera_cobertura": ("Cuantas cuentas estan fuera de la cobertura de toda base."),
    "territorial.tasa_asignacion": (
        "De las cuentas con resultado territorial, que parte quedo asignada a una base."
    ),
    # campo
    "campo.candidatas": (
        "Cuantas cuentas son candidatas de campo en el plan declarado: las que decision/v2 manda a "
        "CAMPO."
    ),
    "campo.asignadas": "Cuantas candidatas quedaron asignadas a un gestor de campo en el plan.",
    "campo.revision_territorial": (
        "Cuantas candidatas no se asignaron porque su asignacion territorial no es ASIGNADA."
    ),
    "campo.sin_gestor": "Cuantas candidatas no tienen un gestor disponible en su base ese dia.",
    "campo.sin_capacidad": "Cuantas candidatas no cupieron en la capacidad declarada de su base.",
    "campo.rebalanceadas": (
        "Cuantas candidatas se asignaron a otro gestor que su propietario preferido."
    ),
    "campo.capacidad_total": "La capacidad declarada de los gestores con jornada ese dia.",
    "campo.capacidad_utilizada": "Cuanta de esa capacidad usaron las cuentas asignadas.",
    "campo.utilizacion": "Que parte de la capacidad declarada se uso: un cupo, no un tiempo.",
    "campo.tasa_asignacion": "De las candidatas de campo, que parte se asigno a un gestor.",
    # ruteo
    "ruteo.rutas": "Cuantas rutas planifico la ejecucion de ruteo/v2 declarada: una por gestor.",
    "ruteo.rutas_completas": "Cuantas rutas programaron todas sus paradas.",
    "ruteo.rutas_parciales": "Cuantas rutas dejaron alguna parada sin programar.",
    "ruteo.rutas_sin_paradas": "Cuantas rutas no tienen ninguna parada enrutable.",
    "ruteo.bases_no_enrutables": "Cuantas rutas no salen porque su base no es enrutable.",
    "ruteo.rutas_sin_jornada": "Cuantas rutas no salen porque su gestor no tiene jornada ese dia.",
    "ruteo.paradas_objetivo": "Cuantas paradas se intentaron rutear: una por cuenta asignada.",
    "ruteo.programadas": (
        "Cuantas paradas quedaron programadas en una ruta. Planificado: no es una visita realizada."
    ),
    "ruteo.sin_tiempo": "Cuantas paradas enrutables no cupieron en la jornada.",
    "ruteo.no_enrutables": "Cuantas paradas no tienen camino por la red vial o un snap valido.",
    "ruteo.no_enrutables_base": "Cuantas paradas son de una ruta cuya base no es enrutable.",
    "ruteo.tasa_programacion": "De las paradas objetivo, que parte quedo programada.",
    "ruteo.distancia_total_m": (
        "La distancia planificada por calle de todas las rutas, con el regreso a la base."
    ),
    "ruteo.duracion_vial_total_s": (
        "El tiempo de traslado planificado de todas las rutas, sin trafico."
    ),
    "ruteo.duracion_servicio_total_s": (
        "El tiempo de servicio planificado: una hipotesis fija por visita, no una medicion."
    ),
    "ruteo.duracion_espera_total_s": (
        "El tiempo planificado en espera de la siguiente ventana de la jornada."
    ),
}
"""Que mide cada metrica, en palabras: la descripcion que publica GET /analytics/catalogo."""


def _metrica(
    codigo: str,
    nombre: str,
    familia: Familia,
    unidad: Unidad,
    componentes: tuple[str, ...],
    formula: str,
    *,
    universo: Universo = Universo.COHORTE,
    numerador: str | None = None,
    denominador: str | None = None,
    poblacion: str,
    regla_temporal: str,
    fuentes: tuple[Fuente, ...],
    nulo: str,
    agregacion: str,
    aditiva: bool,
    conjuntos: tuple[Conjunto, ...],
    vistas: tuple[Vista, ...],
    monetaria: bool = False,
    puede_ser_negativa: bool = False,
    cobertura_de_pagos: bool = False,
    limitaciones: tuple[str, ...] = (),
) -> Metrica:
    tipo = Tipo.RAZON if len(componentes) == 2 else Tipo.VALOR
    if tipo == Tipo.RAZON and (numerador is None or denominador is None):
        raise ValueError(f"{codigo}: una razon declara su numerador y su denominador.")
    return Metrica(
        codigo=codigo,
        nombre=nombre,
        descripcion=DESCRIPCION[codigo],
        familia=familia,
        unidad=unidad,
        tipo=tipo,
        universo=universo,
        componentes=componentes,
        formula=formula,
        numerador=numerador,
        denominador=denominador,
        poblacion=poblacion,
        regla_temporal=regla_temporal,
        fuentes=fuentes,
        nulo=nulo,
        agregacion=agregacion,
        aditiva=aditiva,
        conjuntos=conjuntos,
        vistas=vistas,
        monetaria=monetaria,
        puede_ser_negativa=puede_ser_negativa,
        cobertura_de_pagos=cobertura_de_pagos,
        limitaciones=limitaciones,
    )


def _cartera() -> list[Metrica]:
    comun = {
        "poblacion": POBLACION_COHORTE,
        "regla_temporal": FOTO_DEL_CORTE,
        "fuentes": (Fuente.CORTE,),
        "conjuntos": COHORTE_CONJUNTOS,
        "vistas": SOLO_ACUMULADA,
    }
    return [
        _metrica(
            "cartera.cuentas",
            "Cuentas de la cohorte",
            Familia.CARTERA,
            Unidad.CUENTAS,
            ("cuentas",),
            "count(cuentas de la cohorte)",
            nulo=NUNCA_NO_DISPONIBLE,
            agregacion="CONTEO",
            aditiva=True,
            **comun,
        ),
        _metrica(
            "cartera.saldo_total",
            "Saldo total inicial",
            Familia.CARTERA,
            Unidad.MONEDA,
            ("saldo_total",),
            "sum(saldo_total del snapshot en el corte)",
            nulo=NUNCA_NO_DISPONIBLE,
            agregacion="SUMA",
            aditiva=True,
            monetaria=True,
            **comun,
        ),
        _metrica(
            "cartera.saldo_total_promedio",
            "Saldo total promedio por cuenta",
            Familia.CARTERA,
            Unidad.MONEDA,
            ("saldo_total", "cuentas"),
            "sum(saldo_total) / count(cuentas)",
            numerador="sum(saldo_total del snapshot en el corte)",
            denominador="count(cuentas de la cohorte)",
            nulo=f"{NUNCA_NO_DISPONIBLE} {SIN_DENOMINADOR}",
            agregacion="RAZON",
            aditiva=False,
            monetaria=True,
            **comun,
        ),
        _metrica(
            "cartera.dias_atraso_promedio",
            "Dias de atraso promedio",
            Familia.CARTERA,
            Unidad.DIAS,
            ("dias_atraso", "cuentas"),
            "sum(dias_atraso) / count(cuentas)",
            numerador="sum(dias_atraso del snapshot en el corte)",
            denominador="count(cuentas de la cohorte)",
            nulo=f"{NUNCA_NO_DISPONIBLE} {SIN_DENOMINADOR}",
            agregacion="RAZON",
            aditiva=False,
            **comun,
        ),
        _metrica(
            "cartera.saldo_atrasado_suma",
            "Saldo atrasado conocido",
            Familia.CARTERA,
            Unidad.MONEDA,
            ("saldo_atrasado",),
            "sum(saldo_atrasado) de las cuentas con dato",
            nulo=(
                f"{NUNCA_NO_DISPONIBLE} Un saldo_atrasado NULL no es cero: no entra en la suma, y "
                "cartera.saldo_atrasado_cobertura dice cuantas cuentas tenian dato."
            ),
            agregacion="SUMA",
            aditiva=True,
            monetaria=True,
            limitaciones=("Suma solo valores conocidos: no estima los que faltan.",),
            **comun,
        ),
        _metrica(
            "cartera.saldo_atrasado_cuentas_con_dato",
            "Cuentas con saldo atrasado conocido",
            Familia.CARTERA,
            Unidad.CUENTAS,
            ("saldo_atrasado_con_dato",),
            "count(cuentas con saldo_atrasado no NULL)",
            nulo=NUNCA_NO_DISPONIBLE,
            agregacion="CONTEO",
            aditiva=True,
            **comun,
        ),
        _metrica(
            "cartera.saldo_atrasado_cobertura",
            "Cobertura del saldo atrasado",
            Familia.CARTERA,
            Unidad.RAZON,
            ("saldo_atrasado_con_dato", "cuentas"),
            "count(cuentas con saldo_atrasado no NULL) / count(cuentas)",
            numerador="count(cuentas con saldo_atrasado no NULL)",
            denominador="count(cuentas de la cohorte)",
            nulo=f"{NUNCA_NO_DISPONIBLE} {SIN_DENOMINADOR}",
            agregacion="RAZON",
            aditiva=False,
            **comun,
        ),
    ]


def _recuperacion() -> list[Metrica]:
    comun = {
        "poblacion": POBLACION_PAGOS,
        "regla_temporal": REGLA_PAGOS,
        "fuentes": (Fuente.CORTE, Fuente.MOTOR_PAGOS),
        "nulo": NULO_PAGOS,
        "conjuntos": COHORTE_CONJUNTOS,
        "vistas": AMBAS,
        "cobertura_de_pagos": True,
    }
    return [
        _metrica(
            "recuperacion.neta",
            "Recuperacion economica neta",
            Familia.RECUPERACION,
            Unidad.MONEDA,
            ("rec_neta",),
            "sum(PAGO no anulado) + sum(POSIBLE_REVERSO)",
            agregacion="SUMA",
            aditiva=True,
            monetaria=True,
            puede_ser_negativa=True,
            limitaciones=(
                SEMANTICA_MOTOR,
                "No es contabilidad del acreedor: el saldo oficial sigue siendo el del snapshot.",
                "Puede ser negativa: un posible reverso en el horizonte resta, y no se acota a "
                "cero.",
            ),
            **comun,
        ),
        _metrica(
            "recuperacion.pagos_vigentes",
            "Pagos vigentes al final de as_of",
            Familia.RECUPERACION,
            Unidad.MONEDA,
            ("rec_pagos",),
            "sum(PAGO no anulado al final de as_of)",
            agregacion="SUMA",
            aditiva=True,
            monetaria=True,
            limitaciones=(SEMANTICA_MOTOR,),
            **comun,
        ),
        _metrica(
            "recuperacion.posibles_reversos",
            "Posibles reversos",
            Familia.RECUPERACION,
            Unidad.MONEDA,
            ("rec_posibles_reversos",),
            "sum(POSIBLE_REVERSO), siempre <= 0",
            agregacion="SUMA",
            aditiva=True,
            monetaria=True,
            puede_ser_negativa=True,
            limitaciones=(
                "pagos/v1 no dice el original de un reverso; motor-pagos/v1 no elige, y su "
                "importe resta.",
            ),
            **comun,
        ),
        _metrica(
            "recuperacion.movimientos_economicos",
            "Movimientos economicos",
            Familia.RECUPERACION,
            Unidad.MOVIMIENTOS,
            ("rec_movimientos",),
            "count(movimientos PAGO, REVERSO y POSIBLE_REVERSO)",
            agregacion="CONTEO",
            aditiva=True,
            limitaciones=("Cuenta hechos distintos: un duplicado exacto es un solo movimiento.",),
            **comun,
        ),
        _metrica(
            "recuperacion.pagos_anulados",
            "Pagos anulados al final de as_of",
            Familia.RECUPERACION,
            Unidad.MOVIMIENTOS,
            ("rec_pagos_anulados",),
            "count(PAGO anulado por un REVERSO recibido antes del final de as_of)",
            agregacion="CONTEO",
            aditiva=True,
            **comun,
        ),
        _metrica(
            "recuperacion.cuentas_con_neto_positivo",
            "Cuentas con recuperacion neta positiva",
            Familia.RECUPERACION,
            Unidad.CUENTAS,
            ("rec_cuentas_positivas",),
            "count(cuentas cuya recuperacion neta en el periodo es > 0)",
            agregacion="CONTEO_DISTINTO",
            aditiva=False,
            limitaciones=("Por periodo: una cuenta puede ser positiva en un mes y no en otro.",),
            **comun,
        ),
        _metrica(
            "recuperacion.sobre_saldo_inicial",
            "Recuperacion neta sobre saldo inicial",
            Familia.RECUPERACION,
            Unidad.RAZON,
            ("rec_neta", "saldo_total"),
            "recuperacion.neta / cartera.saldo_total de las mismas cuentas en el corte",
            numerador="la recuperacion neta de las cuentas de la celda en el periodo",
            denominador="el saldo_total de esas cuentas en el corte inicial",
            agregacion="RAZON",
            aditiva=False,
            puede_ser_negativa=True,
            limitaciones=(
                "Con saldo inicial cero, NULL. No es una tasa de exito ni una eficiencia: es una "
                "razon literal.",
            ),
            **{**comun, "nulo": f"{NULO_PAGOS} {SIN_DENOMINADOR}"},
        ),
    ]


def _calidad_pagos() -> list[Metrica]:
    comun = {
        "regla_temporal": REGLA_PAGOS,
        "fuentes": (Fuente.CORTE, Fuente.MOTOR_PAGOS),
        "nulo": NULO_PAGOS,
        "conjuntos": (Conjunto.TOTAL,),
        "vistas": AMBAS,
        "cobertura_de_pagos": True,
        "agregacion": "SUMA",
        "aditiva": True,
    }
    sin_cuenta = (
        "Los movimientos de las corridas declaradas sin cuenta_canonica_id "
        "(SIN_CUENTA_OBSERVADA): no son de la cohorte ni se asignan a una cuenta."
    )
    fuera = (
        "Los movimientos de las corridas declaradas conciliados con una cuenta que no esta en la "
        "cohorte."
    )
    return [
        _metrica(
            "calidad_pagos.movimientos_sin_cuenta",
            "Movimientos sin cuenta",
            Familia.CALIDAD_PAGOS,
            Unidad.MOVIMIENTOS,
            ("q_sin_cuenta_movimientos",),
            "count(movimientos sin cuenta_canonica_id)",
            poblacion=sin_cuenta,
            limitaciones=(
                "Un pago sin cuenta se concilia con otra interpretacion "
                "(backfill-motor-pagos --reconciliar), que se declara.",
            ),
            **comun,
        ),
        _metrica(
            "calidad_pagos.monto_sin_cuenta",
            "Monto neto sin cuenta",
            Familia.CALIDAD_PAGOS,
            Unidad.MONEDA,
            ("q_sin_cuenta_monto",),
            "sum(PAGO no anulado) + sum(POSIBLE_REVERSO) sin cuenta_canonica_id",
            poblacion=sin_cuenta,
            monetaria=True,
            puede_ser_negativa=True,
            limitaciones=(SEMANTICA_MOTOR,),
            **comun,
        ),
        _metrica(
            "calidad_pagos.movimientos_fuera_de_cohorte",
            "Movimientos de cuentas fuera de la cohorte",
            Familia.CALIDAD_PAGOS,
            Unidad.MOVIMIENTOS,
            ("q_fuera_movimientos",),
            "count(movimientos de una cuenta que no esta en el corte)",
            poblacion=fuera,
            **comun,
        ),
        _metrica(
            "calidad_pagos.monto_fuera_de_cohorte",
            "Monto neto de cuentas fuera de la cohorte",
            Familia.CALIDAD_PAGOS,
            Unidad.MONEDA,
            ("q_fuera_monto",),
            "sum(PAGO no anulado) + sum(POSIBLE_REVERSO) de cuentas que no estan en el corte",
            poblacion=fuera,
            monetaria=True,
            puede_ser_negativa=True,
            limitaciones=(SEMANTICA_MOTOR,),
            **comun,
        ),
    ]


def _gestion() -> list[Metrica]:
    comun = {
        "poblacion": (
            "Las gestiones efectivas de las cuentas de la cohorte. Por CANAL_GESTION y "
            "MEDIO_GESTION, las cuentas con gestiones de ese canal o ese medio."
        ),
        "regla_temporal": REGLA_LIFECYCLE,
        "fuentes": (Fuente.CORTE,),
        "nulo": NULO_LIFECYCLE,
        "conjuntos": (*COHORTE_CONJUNTOS, Conjunto.CANAL_GESTION, Conjunto.MEDIO_GESTION),
        "vistas": AMBAS,
    }
    contacto = (
        "Contactada: con al menos una gestion efectiva con nivel_contacto CONTACTO_TITULAR o "
        "CONTACTO_TERCERO (lifecycle.reglas.CONTACTOS)."
    )
    return [
        _metrica(
            "gestion.gestiones",
            "Gestiones",
            Familia.GESTION,
            Unidad.GESTIONES,
            ("g_gestiones",),
            "count(gestiones efectivas)",
            agregacion="CONTEO",
            aditiva=True,
            **comun,
        ),
        _metrica(
            "gestion.cuentas_gestionadas",
            "Cuentas gestionadas",
            Familia.GESTION,
            Unidad.CUENTAS,
            ("g_gestionadas",),
            "count(cuentas distintas con al menos una gestion efectiva)",
            agregacion="CONTEO_DISTINTO",
            aditiva=False,
            **comun,
        ),
        _metrica(
            "gestion.cuentas_contactadas",
            "Cuentas contactadas",
            Familia.GESTION,
            Unidad.CUENTAS,
            ("g_contactadas",),
            "count(cuentas distintas con al menos una gestion efectiva con contacto)",
            agregacion="CONTEO_DISTINTO",
            aditiva=False,
            limitaciones=(contacto,),
            **comun,
        ),
        _metrica(
            "gestion.contactadas_sobre_gestionadas",
            "Contactadas sobre gestionadas",
            Familia.GESTION,
            Unidad.RAZON,
            ("g_contactadas", "g_gestionadas"),
            "gestion.cuentas_contactadas / gestion.cuentas_gestionadas",
            numerador="cuentas distintas contactadas",
            denominador="cuentas distintas gestionadas",
            agregacion="RAZON",
            aditiva=False,
            limitaciones=(contacto, "Una razon literal, no una tasa de exito."),
            **{**comun, "nulo": f"{NULO_LIFECYCLE} {SIN_DENOMINADOR}"},
        ),
        _metrica(
            "gestion.visitas_campo_registradas",
            "Visitas de campo registradas",
            Familia.GESTION,
            Unidad.VISITAS,
            ("g_visitas",),
            "count(gestiones efectivas con su VisitaCampo)",
            agregacion="CONTEO",
            aditiva=True,
            limitaciones=(
                "Actividad de campo registrada, no planificada: no se liga a una parada de "
                "ruteo/v2, porque ninguna identidad lo prueba.",
            ),
            **comun,
        ),
    ]


def _promesa() -> list[Metrica]:
    comun = {
        "poblacion": "Las promesas de las cuentas de la cohorte.",
        "conjuntos": COHORTE_CONJUNTOS,
        "vistas": AMBAS,
    }
    creadas = {
        **comun,
        "regla_temporal": REGLA_PROMESA,
        "fuentes": (Fuente.CORTE,),
        "nulo": NULO_LIFECYCLE,
    }
    evaluadas = {
        **comun,
        "regla_temporal": REGLA_EVALUACION,
        "fuentes": (Fuente.CORTE, Fuente.EVALUACION_PROMESAS),
        "nulo": NULO_EVALUACION,
        "agregacion": "CONTEO",
        "aditiva": True,
    }
    resueltas = "CUMPLIDA + PARCIAL + INCUMPLIDA: PENDIENTE, CANCELADA y NO_EVALUABLE no entran"
    metricas = [
        _metrica(
            "promesa.creadas",
            "Promesas creadas",
            Familia.PROMESA,
            Unidad.PROMESAS,
            ("p_creadas",),
            "count(promesas creadas)",
            agregacion="CONTEO",
            aditiva=True,
            **creadas,
        ),
        _metrica(
            "promesa.monto_prometido",
            "Monto prometido",
            Familia.PROMESA,
            Unidad.MONEDA,
            ("p_monto",),
            "sum(monto_prometido)",
            agregacion="SUMA",
            aditiva=True,
            monetaria=True,
            limitaciones=("Lo acordado, no lo pagado.",),
            **creadas,
        ),
        _metrica(
            "promesa.cuentas_con_promesa",
            "Cuentas con promesa",
            Familia.PROMESA,
            Unidad.CUENTAS,
            ("p_cuentas",),
            "count(cuentas distintas con al menos una promesa creada)",
            agregacion="CONTEO_DISTINTO",
            aditiva=False,
            **creadas,
        ),
        _metrica(
            "promesa.evaluadas",
            "Promesas evaluadas",
            Familia.PROMESA,
            Unidad.PROMESAS,
            ("p_evaluadas",),
            "count(promesas creadas con su resultado en la evaluacion declarada)",
            **evaluadas,
        ),
    ]
    for estado, codigo, nombre in (
        ("CUMPLIDA", "cumplidas", "Promesas cumplidas"),
        ("PARCIAL", "parciales", "Promesas parciales"),
        ("INCUMPLIDA", "incumplidas", "Promesas incumplidas"),
        ("PENDIENTE", "pendientes", "Promesas pendientes"),
        ("CANCELADA", "canceladas", "Promesas canceladas"),
        ("NO_EVALUABLE", "no_evaluables", "Promesas no evaluables"),
    ):
        metricas.append(
            _metrica(
                f"promesa.{codigo}",
                nombre,
                Familia.PROMESA,
                Unidad.PROMESAS,
                (f"p_{codigo}",),
                f"count(promesas en estado {estado} en la evaluacion declarada)",
                limitaciones=(
                    "Recuperacion compatible no es causada: la evaluacion no dice que la promesa "
                    "produjo el pago.",
                ),
                **evaluadas,
            )
        )
    metricas += [
        _metrica(
            "promesa.sin_evaluacion",
            "Promesas sin resultado en la evaluacion",
            Familia.PROMESA,
            Unidad.PROMESAS,
            ("p_sin_evaluacion",),
            "count(promesas creadas sin resultado en la evaluacion declarada)",
            limitaciones=(
                "Una promesa registrada despues de que la evaluacion leyo sus datos (y hasta "
                "known_at) no tiene resultado: no se le inventa uno.",
            ),
            **evaluadas,
        ),
        _metrica(
            "promesa.tasa_cumplimiento_total",
            "Tasa de cumplimiento total",
            Familia.PROMESA,
            Unidad.RAZON,
            ("p_cumplidas", "p_resueltas"),
            "CUMPLIDA / (CUMPLIDA + PARCIAL + INCUMPLIDA)",
            numerador="promesas CUMPLIDA",
            denominador=resueltas,
            **{
                **evaluadas,
                "agregacion": "RAZON",
                "aditiva": False,
                "nulo": f"{NULO_EVALUACION} {SIN_DENOMINADOR}",
            },
        ),
        _metrica(
            "promesa.tasa_cumplimiento_total_o_parcial",
            "Tasa de cumplimiento total o parcial",
            Familia.PROMESA,
            Unidad.RAZON,
            ("p_cumplidas_o_parciales", "p_resueltas"),
            "(CUMPLIDA + PARCIAL) / (CUMPLIDA + PARCIAL + INCUMPLIDA)",
            numerador="promesas CUMPLIDA o PARCIAL",
            denominador=resueltas,
            **{
                **evaluadas,
                "agregacion": "RAZON",
                "aditiva": False,
                "nulo": f"{NULO_EVALUACION} {SIN_DENOMINADOR}",
            },
        ),
    ]
    return metricas


def _atribucion() -> list[Metrica]:
    comun = {
        "poblacion": POBLACION_PAGOS,
        "regla_temporal": REGLA_ATRIBUCION,
        "fuentes": (Fuente.CORTE, Fuente.MOTOR_PAGOS, Fuente.ATRIBUCION),
        "nulo": NULO_ATRIBUCION,
        "conjuntos": COHORTE_CONJUNTOS,
        "vistas": AMBAS,
        "cobertura_de_pagos": True,
        "aditiva": True,
        "limitaciones": (NO_CAUSAL,),
    }
    metricas = [
        _metrica(
            "atribucion.movimientos_evaluados",
            "Pagos con atribucion",
            Familia.ATRIBUCION,
            Unidad.MOVIMIENTOS,
            ("a_evaluados",),
            "count(PAGO con resultado en la atribucion declarada)",
            agregacion="CONTEO",
            **comun,
        )
    ]
    for clase, codigo, nombre in (
        ("ASOCIACION_UNICA", "asociacion_unica", "Pagos con asociacion unica"),
        ("AMBIGUA", "ambigua", "Pagos con atribucion ambigua"),
        ("SIN_GESTION_CANDIDATA", "sin_gestion", "Pagos sin gestion candidata"),
    ):
        metricas.append(
            _metrica(
                f"atribucion.{codigo}",
                nombre,
                Familia.ATRIBUCION,
                Unidad.MOVIMIENTOS,
                (f"a_{codigo}",),
                f"count(PAGO clasificados {clase})",
                agregacion="CONTEO",
                **comun,
            )
        )
    for clase, codigo, nombre in (
        ("ASOCIACION_UNICA", "monto_asociacion_unica", "Monto con asociacion unica"),
        ("AMBIGUA", "monto_ambiguo", "Monto con atribucion ambigua"),
        ("SIN_GESTION_CANDIDATA", "monto_sin_gestion", "Monto sin gestion candidata"),
    ):
        metricas.append(
            _metrica(
                f"atribucion.{codigo}",
                nombre,
                Familia.ATRIBUCION,
                Unidad.MONEDA,
                (f"a_{codigo}",),
                f"sum(monto de PAGO {clase} no anulado al final de as_of)",
                agregacion="SUMA",
                monetaria=True,
                **comun,
            )
        )
    return metricas


def _decision() -> list[Metrica]:
    comun = {
        "poblacion": POBLACION_COHORTE,
        "regla_temporal": (
            "La decision/v2 declarada del mismo corte, terminada hasta known_at: su "
            "prioridad_final y su canal_recomendado, tal como se decidieron. Analytics no vuelve a "
            "decidir."
        ),
        "fuentes": (Fuente.CORTE, Fuente.DECISION_V2),
        "conjuntos": COHORTE_CONJUNTOS,
        "vistas": SOLO_ACUMULADA,
        "limitaciones": ("score_nivel_final es un orden de prioridad, no una probabilidad.",),
    }
    return [
        _metrica(
            "decision.cuentas",
            "Cuentas decididas",
            Familia.DECISION,
            Unidad.CUENTAS,
            ("d_cuentas",),
            "count(cuentas de la cohorte con su decision en la decision/v2 declarada)",
            nulo="NO_DISPONIBLE si no se declaro una decision/v2.",
            agregacion="CONTEO",
            aditiva=True,
            **comun,
        ),
        _metrica(
            "decision.pct_universo",
            "Porcentaje del universo",
            Familia.DECISION,
            Unidad.RAZON,
            ("d_cuentas", "d_universo"),
            "decision.cuentas de la celda / cuentas de la cohorte",
            numerador="cuentas decididas de la celda",
            denominador="cuentas de la cohorte (el universo del corte)",
            nulo=f"NO_DISPONIBLE si no se declaro una decision/v2. {SIN_DENOMINADOR}",
            agregacion="RAZON",
            aditiva=False,
            **comun,
        ),
    ]


def _geografia() -> list[Metrica]:
    comun = {
        "poblacion": POBLACION_COHORTE,
        "regla_temporal": (
            "La geografia/v1 declarada del mismo corte, terminada hasta known_at: el estado de "
            "calidad de la coordenada de cada cuenta. Sin coordenadas en ninguna salida."
        ),
        "fuentes": (Fuente.CORTE, Fuente.GEOGRAFIA),
        "nulo": "NO_DISPONIBLE si no se declaro una geografia.",
        "conjuntos": COHORTE_CONJUNTOS,
        "vistas": SOLO_ACUMULADA,
        "agregacion": "CONTEO",
        "aditiva": True,
    }
    metricas = [
        _metrica(
            "geografia.cuentas",
            "Cuentas con geografia",
            Familia.GEOGRAFIA,
            Unidad.CUENTAS,
            ("geo_cuentas",),
            "count(cuentas con resultado en la geografia declarada)",
            **comun,
        )
    ]
    for estado, codigo, nombre in (
        ("VALIDA", "validas", "Geografia valida"),
        ("DISCREPANCIA_MUNICIPIO", "discrepancias", "Discrepancia de municipio"),
        ("SIN_COORDENADAS", "sin_coordenadas", "Sin coordenadas"),
        ("COORDENADAS_INCOMPLETAS", "incompletas", "Coordenadas incompletas"),
        ("COORDENADAS_INVALIDAS", "invalidas", "Coordenadas invalidas"),
        ("FUERA_DE_REFERENCIA", "fuera_referencia", "Fuera de la referencia"),
        ("MUNICIPIO_AMBIGUO", "ambiguas", "Municipio ambiguo"),
    ):
        metricas.append(
            _metrica(
                f"geografia.{codigo}",
                nombre,
                Familia.GEOGRAFIA,
                Unidad.CUENTAS,
                (f"geo_{codigo}",),
                f"count(cuentas con estado_geografia {estado})",
                **comun,
            )
        )
    metricas.append(
        _metrica(
            "geografia.tasa_valida",
            "Tasa de geografia valida",
            Familia.GEOGRAFIA,
            Unidad.RAZON,
            ("geo_validas", "geo_cuentas"),
            "geografia.validas / geografia.cuentas",
            numerador="cuentas VALIDA",
            denominador="cuentas con resultado geografico",
            **{
                **comun,
                "agregacion": "RAZON",
                "aditiva": False,
                "nulo": f"NO_DISPONIBLE si no se declaro una geografia. {SIN_DENOMINADOR}",
            },
        )
    )
    return metricas


def _territorial() -> list[Metrica]:
    comun = {
        "poblacion": POBLACION_COHORTE,
        "regla_temporal": (
            "La territorial/v2 declarada del mismo corte, sobre la geografia declarada, terminada "
            "hasta known_at."
        ),
        "fuentes": (Fuente.CORTE, Fuente.GEOGRAFIA, Fuente.TERRITORIAL_V2),
        "nulo": "NO_DISPONIBLE si no se declaro una territorial/v2.",
        "conjuntos": COHORTE_CONJUNTOS,
        "vistas": SOLO_ACUMULADA,
        "agregacion": "CONTEO",
        "aditiva": True,
    }
    metricas = [
        _metrica(
            "territorial.cuentas",
            "Cuentas con asignacion territorial",
            Familia.TERRITORIAL,
            Unidad.CUENTAS,
            ("t_cuentas",),
            "count(cuentas con resultado en la territorial/v2 declarada)",
            **comun,
        )
    ]
    for estado, codigo, nombre in (
        ("ASIGNADA", "asignadas", "Asignadas a una base"),
        ("REVISION_GEOGRAFICA", "revision", "En revision geografica"),
        ("SIN_BASE_ELEGIBLE", "sin_base", "Sin base elegible"),
        ("AMBIGUA_BASE", "ambiguas", "Empate entre bases"),
        ("FUERA_DE_COBERTURA", "fuera_cobertura", "Fuera de cobertura"),
    ):
        metricas.append(
            _metrica(
                f"territorial.{codigo}",
                nombre,
                Familia.TERRITORIAL,
                Unidad.CUENTAS,
                (f"t_{codigo}",),
                f"count(cuentas con estado_asignacion {estado})",
                limitaciones=(
                    ("Un resultado operacional, no un error.",)
                    if estado != "ASIGNADA"
                    else ("Pertenencia territorial, no capacidad.",)
                ),
                **comun,
            )
        )
    metricas.append(
        _metrica(
            "territorial.tasa_asignacion",
            "Tasa de asignacion territorial",
            Familia.TERRITORIAL,
            Unidad.RAZON,
            ("t_asignadas", "t_cuentas"),
            "territorial.asignadas / territorial.cuentas",
            numerador="cuentas ASIGNADA",
            denominador="cuentas con resultado territorial",
            **{
                **comun,
                "agregacion": "RAZON",
                "aditiva": False,
                "nulo": f"NO_DISPONIBLE si no se declaro una territorial/v2. {SIN_DENOMINADOR}",
            },
        )
    )
    return metricas


CAMPO_CONJUNTOS = (
    Conjunto.TOTAL,
    Conjunto.PRIORIDAD_DECISION,
    Conjunto.BASE,
    Conjunto.BASE_X_PRIORIDAD,
)
CAPACIDAD_CONJUNTOS = (Conjunto.TOTAL, Conjunto.BASE, Conjunto.GESTOR_CAMPO)


def _campo() -> list[Metrica]:
    comun = {
        "universo": Universo.CAMPO,
        "poblacion": POBLACION_CAMPO,
        "regla_temporal": REGLA_PLAN,
        "fuentes": (
            Fuente.CORTE,
            Fuente.DECISION_V2,
            Fuente.TERRITORIAL_V2,
            Fuente.CAMPO_V2,
        ),
        "nulo": NULO_PLAN,
        "vistas": SOLO_ACUMULADA,
        "agregacion": "CONTEO",
        "aditiva": True,
        "limitaciones": ("Capacidad declarada, no factibilidad vial ni visita realizada.",),
    }
    metricas = [
        _metrica(
            "campo.candidatas",
            "Candidatas de campo",
            Familia.CAMPO,
            Unidad.CUENTAS,
            ("c_candidatas",),
            "count(candidatas del plan)",
            conjuntos=(*CAMPO_CONJUNTOS, Conjunto.ESTADO_CAMPO, Conjunto.GESTOR_CAMPO),
            **comun,
        ),
        _metrica(
            "campo.asignadas",
            "Asignadas a un gestor",
            Familia.CAMPO,
            Unidad.CUENTAS,
            ("c_asignadas",),
            "count(candidatas ASIGNADA)",
            conjuntos=(*CAMPO_CONJUNTOS, Conjunto.GESTOR_CAMPO),
            **comun,
        ),
    ]
    for estado, codigo, nombre in (
        ("REVISION_TERRITORIAL", "revision_territorial", "En revision territorial"),
        ("SIN_GESTOR_DISPONIBLE", "sin_gestor", "Sin gestor disponible"),
        ("SIN_CAPACIDAD", "sin_capacidad", "Sin capacidad"),
    ):
        metricas.append(
            _metrica(
                f"campo.{codigo}",
                nombre,
                Familia.CAMPO,
                Unidad.CUENTAS,
                (f"c_{codigo}",),
                f"count(candidatas {estado})",
                conjuntos=CAMPO_CONJUNTOS,
                **comun,
            )
        )
    metricas += [
        _metrica(
            "campo.rebalanceadas",
            "Asignadas por spillover",
            Familia.CAMPO,
            Unidad.CUENTAS,
            ("c_rebalanceadas",),
            "count(candidatas ASIGNADA a otro gestor que el propietario preferido)",
            conjuntos=(*CAMPO_CONJUNTOS, Conjunto.GESTOR_CAMPO),
            **comun,
        ),
        _metrica(
            "campo.capacidad_total",
            "Capacidad declarada del dia",
            Familia.CAMPO,
            Unidad.CUPOS,
            ("c_capacidad_total",),
            "sum(capacidad efectiva de cada gestor de la configuracion en la fecha)",
            conjuntos=CAPACIDAD_CONJUNTOS,
            **{**comun, "agregacion": "SUMA"},
        ),
        _metrica(
            "campo.capacidad_utilizada",
            "Capacidad utilizada",
            Familia.CAMPO,
            Unidad.CUPOS,
            ("c_capacidad_utilizada",),
            "count(candidatas ASIGNADA): una cuenta es un cupo",
            conjuntos=CAPACIDAD_CONJUNTOS,
            **comun,
        ),
        _metrica(
            "campo.utilizacion",
            "Utilizacion de la capacidad",
            Familia.CAMPO,
            Unidad.RAZON,
            ("c_capacidad_utilizada", "c_capacidad_total"),
            "campo.capacidad_utilizada / campo.capacidad_total",
            numerador="cupos utilizados (cuentas ASIGNADA)",
            denominador="capacidad efectiva declarada",
            conjuntos=CAPACIDAD_CONJUNTOS,
            **{
                **comun,
                "agregacion": "RAZON",
                "aditiva": False,
                "nulo": f"{NULO_PLAN} Sin capacidad declarada, NULL, no 0 %.",
            },
        ),
        _metrica(
            "campo.tasa_asignacion",
            "Tasa de asignacion de campo",
            Familia.CAMPO,
            Unidad.RAZON,
            ("c_asignadas", "c_candidatas"),
            "campo.asignadas / campo.candidatas",
            numerador="candidatas ASIGNADA",
            denominador="candidatas del plan",
            conjuntos=CAMPO_CONJUNTOS,
            **{
                **comun,
                "agregacion": "RAZON",
                "aditiva": False,
                "nulo": f"{NULO_PLAN} {SIN_DENOMINADOR}",
            },
        ),
    ]
    return metricas


RUTA_CONJUNTOS = (Conjunto.TOTAL, Conjunto.BASE, Conjunto.GESTOR_CAMPO)
PARADA_CONJUNTOS = (*RUTA_CONJUNTOS, Conjunto.PRIORIDAD_DECISION)


def _ruteo() -> list[Metrica]:
    comun = {
        "universo": Universo.RUTEO,
        "regla_temporal": REGLA_PLAN,
        "fuentes": (
            Fuente.CORTE,
            Fuente.DECISION_V2,
            Fuente.TERRITORIAL_V2,
            Fuente.CAMPO_V2,
            Fuente.RUTEO_V2,
        ),
        "nulo": NULO_PLAN,
        "vistas": SOLO_ACUMULADA,
        "agregacion": "CONTEO",
        "aditiva": True,
        "limitaciones": (
            "Planificado, no ejecutado: una parada PROGRAMADA no es una visita realizada, y no se "
            "liga a una VisitaCampo.",
        ),
    }
    rutas = {**comun, "poblacion": POBLACION_RUTAS, "conjuntos": RUTA_CONJUNTOS}
    paradas = {**comun, "poblacion": POBLACION_RUTEO, "conjuntos": PARADA_CONJUNTOS}
    metricas = [
        _metrica(
            "ruteo.rutas",
            "Rutas",
            Familia.RUTEO,
            Unidad.RUTAS,
            ("r_rutas",),
            "count(rutas)",
            **rutas,
        )
    ]
    for estado, codigo, nombre in (
        ("COMPLETA", "rutas_completas", "Rutas completas"),
        ("PARCIAL", "rutas_parciales", "Rutas parciales"),
        ("SIN_PARADAS_ENRUTABLES", "rutas_sin_paradas", "Rutas sin paradas enrutables"),
        ("BASE_NO_ENRUTABLE", "bases_no_enrutables", "Rutas con la base no enrutable"),
        ("SIN_JORNADA", "rutas_sin_jornada", "Rutas sin jornada"),
    ):
        metricas.append(
            _metrica(
                f"ruteo.{codigo}",
                nombre,
                Familia.RUTEO,
                Unidad.RUTAS,
                (f"r_{codigo}",),
                f"count(rutas {estado})",
                **rutas,
            )
        )
    metricas.append(
        _metrica(
            "ruteo.paradas_objetivo",
            "Paradas objetivo",
            Familia.RUTEO,
            Unidad.PARADAS,
            ("r_paradas",),
            "count(paradas): una por cuenta ASIGNADA del plan",
            **{**paradas, "conjuntos": (*PARADA_CONJUNTOS, Conjunto.ESTADO_RUTEO)},
        )
    )
    for estado, codigo, nombre in (
        ("PROGRAMADA", "programadas", "Paradas programadas"),
        ("SIN_TIEMPO", "sin_tiempo", "Paradas sin tiempo"),
        ("NO_ENRUTABLE", "no_enrutables", "Paradas no enrutables"),
        ("NO_ENRUTABLE_BASE", "no_enrutables_base", "Paradas de una base no enrutable"),
    ):
        metricas.append(
            _metrica(
                f"ruteo.{codigo}",
                nombre,
                Familia.RUTEO,
                Unidad.PARADAS,
                (f"r_{codigo}",),
                f"count(paradas {estado})",
                **paradas,
            )
        )
    metricas.append(
        _metrica(
            "ruteo.tasa_programacion",
            "Programadas sobre objetivo",
            Familia.RUTEO,
            Unidad.RAZON,
            ("r_programadas", "r_paradas"),
            "ruteo.programadas / ruteo.paradas_objetivo",
            numerador="paradas PROGRAMADA",
            denominador="paradas objetivo",
            **{
                **paradas,
                "agregacion": "RAZON",
                "aditiva": False,
                "nulo": f"{NULO_PLAN} {SIN_DENOMINADOR}",
            },
        )
    )
    for codigo, nombre, unidad, componente, formula in (
        (
            "distancia_total_m",
            "Distancia total por calle",
            Unidad.METROS,
            "r_distancia_m",
            "sum(distancia_total_m de cada ruta, con el regreso a la base)",
        ),
        (
            "duracion_vial_total_s",
            "Duracion de traslado",
            Unidad.SEGUNDOS,
            "r_vial_s",
            "sum(duracion_traslado_s de cada ruta)",
        ),
        (
            "duracion_servicio_total_s",
            "Duracion de servicio",
            Unidad.SEGUNDOS,
            "r_servicio_s",
            "sum(duracion_servicio_s de cada ruta)",
        ),
        (
            "duracion_espera_total_s",
            "Duracion de espera",
            Unidad.SEGUNDOS,
            "r_espera_s",
            "sum(duracion_espera_s de cada ruta)",
        ),
    ):
        metricas.append(
            _metrica(
                f"ruteo.{codigo}",
                nombre,
                Familia.RUTEO,
                unidad,
                (componente,),
                formula,
                **{**rutas, "agregacion": "SUMA"},
            )
        )
    return metricas


METRICAS: tuple[Metrica, ...] = (
    *_cartera(),
    *_recuperacion(),
    *_calidad_pagos(),
    *_gestion(),
    *_promesa(),
    *_atribucion(),
    *_decision(),
    *_geografia(),
    *_territorial(),
    *_campo(),
    *_ruteo(),
)
"""El catalogo, en su orden canonico."""

POR_CODIGO: dict[str, Metrica] = {m.codigo: m for m in METRICAS}


def _verificar() -> None:
    """Lo que el catalogo exige de si mismo: codigos unicos, conjuntos que existen en el universo de
    su metrica, y vistas que tienen sentido."""
    if len(POR_CODIGO) != len(METRICAS):
        raise ValueError("Un codigo de metrica esta repetido en el catalogo.")
    if set(DESCRIPCION) != set(POR_CODIGO):
        raise ValueError("Cada metrica tiene exactamente una descripcion, y nada mas la tiene.")
    for m in METRICAS:
        if not m.codigo.startswith(m.familia.value.lower() + "."):
            raise ValueError(f"{m.codigo}: su prefijo no es el de su familia {m.familia}.")
        for conjunto in m.conjuntos:
            universo = universo_de_la_celda(m.universo, conjunto)
            if universo not in CONJUNTOS[conjunto].universos:
                raise ValueError(f"{m.codigo}: {conjunto} no existe en el universo {universo}.")
        if Conjunto.TOTAL not in m.conjuntos:
            raise ValueError(f"{m.codigo}: toda metrica se publica en TOTAL.")
        if Vista.ACUMULADA not in m.vistas:
            raise ValueError(f"{m.codigo}: toda metrica se publica en la vista ACUMULADA.")


_verificar()


def definicion() -> dict:
    """El catalogo como datos: lo que firma su hash y lo que publica GET /analytics/catalogo."""
    return {
        "version": VERSION_CATALOGO,
        "version_motor": VERSION_MOTOR,
        "universos": [u.value for u in Universo],
        "conjuntos": [CONJUNTOS[c].definicion() for c in Conjunto],
        "metricas": [m.definicion() for m in METRICAS],
    }


def canonico(datos) -> bytes:
    """JSON canonico: llaves en orden, sin espacios, UTF-8. Lo que se hashea."""
    return json.dumps(datos, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def hash_catalogo() -> str:
    """SHA-256 de la definicion canonica del catalogo."""
    return hashlib.sha256(canonico(definicion())).hexdigest()


def dimension_hash(dimensiones: dict[str, str]) -> str:
    """SHA-256 de las dimensiones de una celda en forma canonica (llaves en orden, sin espacios,
    UTF-8): el mismo texto que escribe el SQL del motor, sin depender del orden de un JSON, de una
    consulta ni de hash() de Python. TOTAL es {}."""
    return hashlib.sha256(canonico(dimensiones)).hexdigest()
