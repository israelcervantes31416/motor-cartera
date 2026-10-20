"""decision-policy/public-v1 como nucleo puro: de las features de una cuenta a su decision, con sus
motivos.

**Esta politica demuestra la capacidad del motor; no representa una politica real de un
acreedor.** Es sintetica, publica y de referencia: sus reglas y umbrales se fijaron antes de ver
cualquier resultado, y no se ajustan contra ningun benchmark.

1. La prioridad base es la de decision/v1 por dias de atraso: el segmento de su tramo y su
   `PRIORIDAD_BASE`, importados de `decision.reglas`, no reescritos. El ajuste por saldo de v1
   (SALDO_ALTO) no es parte de la base: no esta en public-v1.
2. Cuatro modificadores, cada uno evaluado aparte, con efecto -1 o +1:
   - RECUPERACION_RECIENTE: recuperacion_interpretada_7d > 0, -1;
   - PROMESA_VIGENTE: hay una promesa PENDIENTE (evaluacion-promesa/v1), -1;
   - SATURACION_SIN_CONTACTO: gestiones_sin_contacto_7d >= 3 y contactos_titular_30d = 0, +1;
   - PROMESA_INCUMPLIDA_RECIENTE: promesas_incumplidas_30d >= 1, +1.
3. Los efectos de todas las que aplican se suman (`ajuste_total`), sin que una oculte a otra, y el
   nivel final es el base mas el ajuste, acotado a la escala de BAJA (1) a MUY_ALTA (4).
4. El canal sale de la prioridad final con la correspondencia de decision/v1, sin un modelo propio
   de canal.

Un nivel es el lugar de la prioridad en su escala, no una probabilidad: esta politica no predice
nada. Una condicion sobre una feature NULL no se cumple: la informacion que no estaba disponible a
known_at no activa ninguna regla, ni a favor ni en contra.

Es pura: no lee la base, el reloj ni el azar. `decision_v2.ejecuciones` aplica exactamente estas
reglas en PostgreSQL, generadas de esta misma definicion, y una prueba compara las dos.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from motor_cartera.atraso import TRAMOS_ATRASO, tramo_de_atraso
from motor_cartera.decision.reglas import (
    CANAL_POR_PRIORIDAD,
    PRIORIDAD_BASE,
    SEGMENTO_POR_TRAMO,
    VERSION_REGLAS_DECISION,
    Prioridad,
    SegmentoMora,
)
from motor_cartera.decision_v2.features import POR_NOMBRE, VERSION_FEATURES, texto

VERSION_MOTOR = "decision/v2"
"""La version del motor: como se calcula, se publica y se explica una ejecucion, incluido el
esquema de sus motivos."""

VERSION_POLITICA = "decision-policy/public-v1"
"""La version de las reglas. Cambia si cambia cualquier regla, umbral, efecto o correspondencia; su
hash (`hash_politica`) lo comprueba."""

NIVEL: dict[Prioridad, int] = {
    Prioridad.BAJA: 1,
    Prioridad.MEDIA: 2,
    Prioridad.ALTA: 3,
    Prioridad.MUY_ALTA: 4,
}
"""El orden de cada prioridad en su escala. El enum publico de decision/v1 no cambia: esto es su
orden, no otro vocabulario."""

PRIORIDAD_DEL_NIVEL: dict[int, Prioridad] = {nivel: p for p, nivel in NIVEL.items()}
NIVEL_MINIMO, NIVEL_MAXIMO = min(NIVEL.values()), max(NIVEL.values())


class Operador(StrEnum):
    MAYOR = ">"
    MAYOR_O_IGUAL = ">="
    IGUAL = "="


class CodigoMotivo(StrEnum):
    """Por que una decision salio como salio, en un catalogo cerrado: es lo que un cliente
    compara. Cambiar el nombre o el significado de un codigo exige otra version."""

    PRIORIDAD_BASE_MORA = "PRIORIDAD_BASE_MORA"
    RECUPERACION_RECIENTE = "RECUPERACION_RECIENTE"
    PROMESA_VIGENTE = "PROMESA_VIGENTE"
    SATURACION_SIN_CONTACTO = "SATURACION_SIN_CONTACTO"
    PROMESA_INCUMPLIDA_RECIENTE = "PROMESA_INCUMPLIDA_RECIENTE"
    PRIORIDAD_FINAL = "PRIORIDAD_FINAL"
    CANAL_POR_PRIORIDAD = "CANAL_POR_PRIORIDAD"


EN_TRAMO = "EN_TRAMO"
"""El operador de la condicion de la prioridad base: los dias de atraso caen en el tramo."""


@dataclass(frozen=True)
class Condicion:
    feature: str
    operador: Operador
    umbral: int | Decimal | bool

    def __post_init__(self) -> None:
        # El umbral se escribe como su feature: un umbral que no es de su tipo falla aqui.
        texto(POR_NOMBRE[self.feature], self.umbral)

    @property
    def umbral_canonico(self) -> str:
        return texto(POR_NOMBRE[self.feature], self.umbral)


@dataclass(frozen=True)
class Modificador:
    """Una regla que, si se cumplen todas sus condiciones, suma su efecto al nivel base."""

    codigo: CodigoMotivo
    condiciones: tuple[Condicion, ...]
    efecto: int

    def __post_init__(self) -> None:
        if self.efecto not in (-1, 1) or not self.condiciones:
            raise ValueError(f"{self.codigo}: un modificador tiene condiciones y efecto -1 o +1.")


@dataclass(frozen=True)
class Politica:
    version: str
    modificadores: tuple[Modificador, ...]


PUBLIC_V1 = Politica(
    VERSION_POLITICA,
    (
        Modificador(
            CodigoMotivo.RECUPERACION_RECIENTE,
            (Condicion("recuperacion_interpretada_7d", Operador.MAYOR, Decimal("0")),),
            -1,
        ),
        Modificador(
            CodigoMotivo.PROMESA_VIGENTE,
            (Condicion("promesa_vigente", Operador.IGUAL, True),),
            -1,
        ),
        Modificador(
            CodigoMotivo.SATURACION_SIN_CONTACTO,
            (
                Condicion("gestiones_sin_contacto_7d", Operador.MAYOR_O_IGUAL, 3),
                Condicion("contactos_titular_30d", Operador.IGUAL, 0),
            ),
            1,
        ),
        Modificador(
            CodigoMotivo.PROMESA_INCUMPLIDA_RECIENTE,
            (Condicion("promesas_incumplidas_30d", Operador.MAYOR_O_IGUAL, 1),),
            1,
        ),
    ),
)
"""decision-policy/public-v1. Ni un modificador mas: los demas, si hacen falta, son otra version."""

POLITICAS: dict[str, Politica] = {PUBLIC_V1.version: PUBLIC_V1}
"""Las politicas que este servicio sabe aplicar."""


# --- la definicion y su hash ----------------------------------------------------------------------


def definicion(politica: Politica = PUBLIC_V1) -> dict:
    """La politica completa como datos: la base (tramo por tramo, con su segmento y su prioridad),
    cada modificador con sus condiciones y su efecto, la escala, el acotamiento, el canal de cada
    prioridad y que hace un NULL. Es lo que firma `hash_politica`."""
    return {
        "version": politica.version,
        "motor": VERSION_MOTOR,
        "features": VERSION_FEATURES,
        "base": {
            "de": VERSION_REGLAS_DECISION,
            "feature": "dias_atraso",
            "tramos": [
                {
                    "tramo": etiqueta,
                    "desde": desde,
                    "hasta": hasta,
                    "segmento": SEGMENTO_POR_TRAMO[etiqueta].value,
                    "prioridad": PRIORIDAD_BASE[SEGMENTO_POR_TRAMO[etiqueta]].value,
                }
                for etiqueta, desde, hasta in TRAMOS_ATRASO
            ],
        },
        "modificadores": [
            {
                "codigo": m.codigo.value,
                "condiciones": [
                    {
                        "feature": c.feature,
                        "operador": c.operador.value,
                        "umbral": c.umbral_canonico,
                    }
                    for c in m.condiciones
                ],
                "efecto": m.efecto,
            }
            for m in politica.modificadores
        ],
        "niveles": {p.value: nivel for p, nivel in NIVEL.items()},
        "acotado": [NIVEL_MINIMO, NIVEL_MAXIMO],
        "canal": {p.value: CANAL_POR_PRIORIDAD[p] for p in NIVEL},
        "nulo": "una condicion sobre una feature NULL no se cumple",
    }


def hash_politica(politica: Politica = PUBLIC_V1) -> str:
    """El SHA-256 de la definicion de la politica, en JSON canonico."""
    texto_json = json.dumps(
        definicion(politica), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(texto_json.encode("utf-8")).hexdigest()


# --- decidir --------------------------------------------------------------------------------------


class CuentaNoDecidible(ValueError):
    """Las features de la cuenta no tienen lo que la politica necesita: sus dias de atraso no son un
    entero no negativo."""


@dataclass(frozen=True)
class CondicionEvaluada:
    feature: str
    operador: str
    umbral: str
    valor: str | None
    """El de la feature, en forma canonica; None si era NULL."""
    cumple: bool

    def motivo(self) -> dict:
        return {
            "feature": self.feature,
            "valor": self.valor,
            "operador": self.operador,
            "umbral": self.umbral,
        }


@dataclass(frozen=True)
class ReglaEvaluada:
    codigo: CodigoMotivo
    condiciones: tuple[CondicionEvaluada, ...]
    efecto_declarado: int

    @property
    def activa(self) -> bool:
        return all(c.cumple for c in self.condiciones)

    @property
    def efecto(self) -> int:
        """El que suma: el declarado si aplica, 0 si no."""
        return self.efecto_declarado if self.activa else 0


@dataclass(frozen=True)
class DecisionV2:
    """La decision de una cuenta con sus pasos: la base, cada regla (aplique o no), el ajuste, el
    nivel antes y despues de acotar, la prioridad final y el canal."""

    dias_atraso: int
    tramo: str
    segmento: SegmentoMora
    prioridad_base: Prioridad
    reglas: tuple[ReglaEvaluada, ...]
    prioridad_final: Prioridad
    canal_recomendado: str

    @property
    def nivel_base(self) -> int:
        return NIVEL[self.prioridad_base]

    @property
    def ajuste_total(self) -> int:
        return sum(r.efecto for r in self.reglas)

    @property
    def nivel_sin_acotar(self) -> int:
        return self.nivel_base + self.ajuste_total

    @property
    def nivel_final(self) -> int:
        return NIVEL[self.prioridad_final]

    @property
    def acotada(self) -> bool:
        return self.nivel_sin_acotar != self.nivel_final

    def motivos(self) -> list[dict]:
        """Lo que se guarda como explicacion: la prioridad base, cada modificador que aplico con sus
        condiciones y su efecto, y la prioridad final con su canal. Los que no aplicaron no se
        guardan: se ven en las features y en la politica, que tambien se guardan."""
        base = {
            "codigo": CodigoMotivo.PRIORIDAD_BASE_MORA.value,
            "condiciones": [
                {
                    "feature": "dias_atraso",
                    "valor": str(self.dias_atraso),
                    "operador": EN_TRAMO,
                    "umbral": self.tramo,
                }
            ],
            "segmento": self.segmento.value,
            "nivel": self.nivel_base,
            "prioridad": self.prioridad_base.value,
        }
        aplicadas = [
            {
                "codigo": r.codigo.value,
                "condiciones": [c.motivo() for c in r.condiciones],
                "efecto": r.efecto,
            }
            for r in self.reglas
            if r.activa
        ]
        final = {
            "codigo": CodigoMotivo.PRIORIDAD_FINAL.value,
            "nivel_base": self.nivel_base,
            "ajuste_total": self.ajuste_total,
            "nivel_sin_acotar": self.nivel_sin_acotar,
            "nivel": self.nivel_final,
            "prioridad": self.prioridad_final.value,
            "acotada": self.acotada,
        }
        canal = {
            "codigo": CodigoMotivo.CANAL_POR_PRIORIDAD.value,
            "prioridad": self.prioridad_final.value,
            "canal": self.canal_recomendado,
        }
        return [base, *aplicadas, final, canal]


def cumple(condicion: Condicion, valor: object) -> bool:
    """Si el valor de la feature cumple la condicion. Un NULL no la cumple."""
    if valor is None:
        return False
    if condicion.operador == Operador.MAYOR:
        return _numero(condicion, valor) > condicion.umbral
    if condicion.operador == Operador.MAYOR_O_IGUAL:
        return _numero(condicion, valor) >= condicion.umbral
    return valor == condicion.umbral


def _numero(condicion: Condicion, valor: object) -> int | Decimal:
    """El valor de una feature que se compara por orden: un numero (un entero o un decimal)."""
    if not isinstance(valor, int | Decimal):
        raise TypeError(f"{condicion.feature} se compara con {condicion.operador}: {valor!r}.")
    return valor


def prioridad_base(dias_atraso: int) -> tuple[str, SegmentoMora, Prioridad]:
    """El tramo, el segmento y la prioridad base de decision/v1 por dias de atraso."""
    if isinstance(dias_atraso, bool) or not isinstance(dias_atraso, int) or dias_atraso < 0:
        raise CuentaNoDecidible(f"dias_atraso debe ser un entero no negativo: {dias_atraso!r}.")
    tramo = tramo_de_atraso(dias_atraso)
    segmento = SEGMENTO_POR_TRAMO[tramo]
    return tramo, segmento, PRIORIDAD_BASE[segmento]


def decidir(features: Mapping[str, object], politica: Politica = PUBLIC_V1) -> DecisionV2:
    """La decision de una cuenta con decision-policy/public-v1. Cada regla se evalua aparte; la suma
    de sus efectos se acota a la escala; el canal sale de la prioridad final."""
    dias = features["dias_atraso"]
    if isinstance(dias, bool) or not isinstance(dias, int):
        raise CuentaNoDecidible(f"dias_atraso debe ser un entero no negativo: {dias!r}.")
    tramo, segmento, base = prioridad_base(dias)
    reglas = tuple(
        ReglaEvaluada(
            m.codigo,
            tuple(
                CondicionEvaluada(
                    c.feature,
                    c.operador.value,
                    c.umbral_canonico,
                    None
                    if features[c.feature] is None
                    else texto(POR_NOMBRE[c.feature], features[c.feature]),
                    cumple(c, features[c.feature]),
                )
                for c in m.condiciones
            ),
            m.efecto,
        )
        for m in politica.modificadores
    )
    ajuste = sum(r.efecto for r in reglas)
    nivel = min(NIVEL_MAXIMO, max(NIVEL_MINIMO, NIVEL[base] + ajuste))
    final = PRIORIDAD_DEL_NIVEL[nivel]
    return DecisionV2(dias, tramo, segmento, base, reglas, final, CANAL_POR_PRIORIDAD[final])


def forma_canonica_de_la_decision(
    feature_hash: bytes,
    decision: DecisionV2,
    *,
    version_features: str = VERSION_FEATURES,
    politica: Politica = PUBLIC_V1,
) -> str:
    """Lo que firma `decision_hash`: las versiones, el hash de las features y el de la politica, la
    prioridad base, el efecto de cada regla en su orden, el ajuste, la prioridad final y el canal.
    Los motivos no entran como texto: son funcion exacta de las features y de la politica, que ya
    estan aqui por su hash."""
    lineas = [
        VERSION_MOTOR,
        version_features,
        feature_hash.hex(),
        politica.version,
        hash_politica(politica),
        decision.prioridad_base.value,
        *(f"{r.codigo.value}={r.efecto}" for r in decision.reglas),
        str(decision.ajuste_total),
        decision.prioridad_final.value,
        decision.canal_recomendado,
    ]
    return "\n".join(lineas)


def decision_hash(
    feature_hash: bytes,
    decision: DecisionV2,
    *,
    version_features: str = VERSION_FEATURES,
    politica: Politica = PUBLIC_V1,
) -> bytes:
    """El SHA-256 de la forma canonica de la decision."""
    canonica = forma_canonica_de_la_decision(
        feature_hash, decision, version_features=version_features, politica=politica
    )
    return hashlib.sha256(canonica.encode("utf-8")).digest()
