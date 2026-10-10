"""features-decision/v1: las variables con que decision/v2 decide una cuenta, y su forma canonica.

Una feature es un valor de una cuenta calculado a un `as_of` con lo que se conocia en un
`known_at`, y guardado tal cual en FeatureDecisionCuenta: no se vuelve a calcular al consultar una
decision. Este modulo dice cuales son, de que tipo y familia, si pueden ser NULL, y como se escribe
cada valor en forma canonica, que es lo que firma `feature_hash`. El calculo esta en
`decision_v2.ejecuciones`, en PostgreSQL. El diccionario completo (la ventana, la fuente, la
semantica, que significa NULL y el filtro point-in-time de cada una) esta en docs/decision_v2.md, y
una prueba exige que coincida con esta lista, en este orden.

NULL no es cero. Un conteo de eventos del lifecycle es 0 cuando no se conoce ninguno: el lifecycle
es el registro de Motor Cartera. Una feature de pagos o de atribucion es NULL cuando su familia no
estaba disponible a known_at en su ventana, y una distancia o una tasa, cuando no hay de que
calcularla (sin gestiones, sin pagos, sin denominador).

Este modulo no sabe de la base.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

VERSION_FEATURES = "features-decision/v1"
"""Cambia si cambia cualquier feature: su lista, su tipo, su ventana, su fuente, su filtro
point-in-time o lo que significa su NULL. Cambiar la politica no la cambia."""

NULO = "~"
"""Como se escribe un NULL en la forma canonica: no es el texto de ningun valor."""


class Familia(StrEnum):
    """De donde sale una feature."""

    SNAPSHOT = "SNAPSHOT"  # el snapshot de la cuenta en el corte objetivo
    HISTORIA = "HISTORIA"  # sus snapshots en los cortes conocidos hasta as_of
    GESTION = "GESTION"  # las gestiones del lifecycle
    PROMESA = "PROMESA"  # las promesas, evaluadas con evaluacion-promesa/v1
    CONVENIO = "CONVENIO"  # los convenios
    PAGOS = "PAGOS"  # los movimientos economicos de motor-pagos/v1
    ATRIBUCION = "ATRIBUCION"  # las asociaciones unicas de atribucion/v1


class Tipo(StrEnum):
    """Como se guarda y como se escribe en forma canonica."""

    ENTERO = "ENTERO"
    IMPORTE = "IMPORTE"  # pesos, con dos decimales
    TASA = "TASA"  # de 0 a 1, con cuatro decimales
    DIAS = "DIAS"  # dias con dos decimales: una mediana
    TEXTO = "TEXTO"
    BOOLEANO = "BOOLEANO"


ESCALA: dict[Tipo, Decimal] = {
    Tipo.IMPORTE: Decimal("0.01"),
    Tipo.TASA: Decimal("0.0001"),
    Tipo.DIAS: Decimal("0.01"),
}
"""Los decimales de cada tipo numerico no entero: los de su columna en la base."""


@dataclass(frozen=True)
class Feature:
    nombre: str
    tipo: Tipo
    familia: Familia
    nula: bool
    """Si puede ser NULL. Las que no, tienen siempre un valor conocido."""


def _f(nombre: str, tipo: Tipo, familia: Familia, *, nula: bool = False) -> Feature:
    return Feature(nombre, tipo, familia, nula)


E, IMP, T, D, X, B = Tipo.ENTERO, Tipo.IMPORTE, Tipo.TASA, Tipo.DIAS, Tipo.TEXTO, Tipo.BOOLEANO
S, H, G = Familia.SNAPSHOT, Familia.HISTORIA, Familia.GESTION
P, C, M, A = Familia.PROMESA, Familia.CONVENIO, Familia.PAGOS, Familia.ATRIBUCION

FEATURES: tuple[Feature, ...] = (
    _f("saldo_total", IMP, S),
    _f("saldo_atrasado", IMP, S, nula=True),
    _f("saldo_requerido", IMP, S, nula=True),
    _f("moratorios", IMP, S, nula=True),
    _f("dias_atraso", E, S),
    _f("semanas_atraso", E, S, nula=True),
    _f("producto", X, S),
    _f("estrategia", X, S, nula=True),
    _f("estatus_plan", X, S, nula=True),
    _f("cortes_observados", E, H),
    _f("reingresos_observados", E, H),
    _f("delta_saldo_total_desde_corte_anterior", IMP, H, nula=True),
    _f("delta_dias_atraso_desde_corte_anterior", E, H, nula=True),
    _f("continuo_desde_corte_anterior", B, H, nula=True),
    _f("dias_desde_ultima_gestion", E, G, nula=True),
    _f("gestiones_7d", E, G),
    _f("gestiones_30d", E, G),
    _f("gestiones_sin_contacto_7d", E, G),
    _f("contactos_titular_30d", E, G),
    _f("contactos_tercero_30d", E, G),
    _f("visitas_30d", E, G),
    _f("gestiones_contactables_30d", E, G),
    _f("tasa_contacto_titular_30d", T, G, nula=True),
    _f("ultima_gestion_canal", X, G, nula=True),
    _f("ultima_gestion_nivel_contacto", X, G, nula=True),
    _f("promesa_vigente", B, P),
    _f("promesas_30d", E, P),
    _f("promesas_90d", E, P),
    _f("promesas_cumplidas_90d", E, P),
    _f("promesas_parciales_90d", E, P),
    _f("promesas_incumplidas_30d", E, P),
    _f("promesas_incumplidas_90d", E, P),
    _f("convenio_vigente", B, C),
    _f("convenios_90d", E, C),
    _f("dias_desde_ultimo_pago", E, M, nula=True),
    _f("movimientos_pago_7d", E, M, nula=True),
    _f("movimientos_pago_30d", E, M, nula=True),
    _f("recuperacion_interpretada_7d", IMP, M, nula=True),
    _f("recuperacion_interpretada_30d", IMP, M, nula=True),
    _f("recuperacion_interpretada_90d", IMP, M, nula=True),
    _f("pagos_asociados_unicos_30d", E, A, nula=True),
    _f("recuperacion_asociada_unica_30d", IMP, A, nula=True),
    _f("dias_gestion_a_pago_mediana_90d", D, A, nula=True),
)
"""Las features de features-decision/v1, en su orden canonico: el de su hash, de la API y del
diccionario."""

POR_NOMBRE: dict[str, Feature] = {f.nombre: f for f in FEATURES}


class ValorInvalido(ValueError):
    """Un valor que no es de su tipo, o un NULL donde la feature no admite uno."""


def texto(feature: Feature, valor: object) -> str:
    """El valor de una feature en forma canonica: los enteros en decimal, los numeros con los
    decimales de su columna, los booleanos como true o false, el texto con su largo delante
    (`s5:CAMPO`) y NULL como `~`. Es exactamente lo que PostgreSQL escribe de la columna con
    `::text`, para que el hash de la base y el de Python coincidan."""
    if valor is None:
        if not feature.nula:
            raise ValorInvalido(f"{feature.nombre} no admite NULL.")
        return NULO
    if feature.tipo == Tipo.BOOLEANO:
        if not isinstance(valor, bool):
            raise ValorInvalido(f"{feature.nombre} es booleana: {valor!r}.")
        return "true" if valor else "false"
    if feature.tipo == Tipo.ENTERO:
        if isinstance(valor, bool) or not isinstance(valor, int):
            raise ValorInvalido(f"{feature.nombre} es entera: {valor!r}.")
        return str(valor)
    if feature.tipo == Tipo.TEXTO:
        if not isinstance(valor, str):
            raise ValorInvalido(f"{feature.nombre} es texto: {valor!r}.")
        return f"s{len(valor)}:{valor}"
    if not isinstance(valor, Decimal) or not valor.is_finite():
        raise ValorInvalido(f"{feature.nombre} es un Decimal finito: {valor!r}.")
    return str(valor.quantize(ESCALA[feature.tipo]))


def forma_canonica(valores: Mapping[str, object]) -> str:
    """Todas las features de una cuenta en forma canonica, una por linea y en el orden de FEATURES,
    con la version delante. Exige exactamente las de features-decision/v1."""
    faltan = [f.nombre for f in FEATURES if f.nombre not in valores]
    if faltan:
        raise ValorInvalido(f"Faltan features: {', '.join(faltan)}.")
    lineas = [VERSION_FEATURES, *(f"{f.nombre}={texto(f, valores[f.nombre])}" for f in FEATURES)]
    return "\n".join(lineas)


def feature_hash(valores: Mapping[str, object]) -> bytes:
    """El SHA-256 de la forma canonica de las features de una cuenta."""
    return hashlib.sha256(forma_canonica(valores).encode("utf-8")).digest()
