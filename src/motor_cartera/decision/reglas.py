"""Las reglas de decision por cuenta: `decision/v1`.

A cada cuenta de una cartera publicada le asignan un segmento de mora, una prioridad y el canal
por el que conviene gestionarla, con el motivo de cada paso. Son reglas explicitas sobre datos
sinteticos, no un modelo: `cartera/v1` no trae pagos, promesas ni gestiones, y sin eso no hay
recuperabilidad que estimar.

Son puras: no leen la base, archivos, el reloj ni el azar. La misma entrada con la misma version
da siempre el mismo resultado, y por eso cualquier decision guardada se puede volver a explicar.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from motor_cartera.atraso import tramo_de_atraso

VERSION_REGLAS_DECISION = "decision/v1"
"""La version de estas reglas. Cada ejecucion va a guardar con cual se decidio.

Es independiente de `VERSION_CONTRATO`: el contrato dice que datos se publican; estas reglas,
como se convierten en decisiones. Cambia cuando cambia cualquier resultado posible (un segmento,
una prioridad, un canal, el umbral o un codigo de motivo), para que la misma version signifique
siempre exactamente las mismas reglas.
"""

UMBRAL_SALDO_ALTO = Decimal("50000.00")
"""Desde este saldo, una cuenta con atraso sube un nivel de prioridad.

Es un valor sintetico, propio de este proyecto publico: no proviene de ninguna operacion real.
Vive aqui y no en la configuracion: si se pudiera cambiar por entorno, la misma version daria
resultados distintos.
"""

CENTAVO = Decimal("0.01")
"""El saldo de un motivo se escribe con dos decimales, como se guarda y con el mismo redondeo que
la forma canonica del contrato."""


class SegmentoMora(StrEnum):
    """En que situacion de mora esta una cuenta.

    Es de cada cuenta, y no es el segmento del resumen, que es un grupo de cuentas con los mismos
    valores en las dimensiones pedidas.
    """

    AL_CORRIENTE = "AL_CORRIENTE"
    MORA_TEMPRANA = "MORA_TEMPRANA"
    MORA_MEDIA = "MORA_MEDIA"
    MORA_ALTA = "MORA_ALTA"


class Prioridad(StrEnum):
    """Que tan pronto hay que gestionar una cuenta, de menor a mayor."""

    BAJA = "BAJA"
    MEDIA = "MEDIA"
    ALTA = "ALTA"
    MUY_ALTA = "MUY_ALTA"


class CodigoMotivo(StrEnum):
    """Por que una decision salio como salio, en un catalogo cerrado.

    Es lo que un cliente compara, como el `codigo` de un error: cambiar el nombre o el significado
    de un codigo exige una version nueva de las reglas.
    """

    SIN_MORA = "SIN_MORA"
    MORA_1_30 = "MORA_1_30"
    MORA_31_90 = "MORA_31_90"
    MORA_91_MAS = "MORA_91_MAS"
    SALDO_ALTO = "SALDO_ALTO"
    PRIORIDAD_BAJA = "PRIORIDAD_BAJA"
    PRIORIDAD_MEDIA = "PRIORIDAD_MEDIA"
    PRIORIDAD_ALTA = "PRIORIDAD_ALTA"
    PRIORIDAD_MUY_ALTA = "PRIORIDAD_MUY_ALTA"
    CANAL_DIGITAL = "CANAL_DIGITAL"
    CANAL_TELEFONICA = "CANAL_TELEFONICA"
    CANAL_CAMPO = "CANAL_CAMPO"


SEGMENTO_POR_TRAMO: dict[str, SegmentoMora] = {
    "0": SegmentoMora.AL_CORRIENTE,
    "1-30": SegmentoMora.MORA_TEMPRANA,
    "31-60": SegmentoMora.MORA_MEDIA,
    "61-90": SegmentoMora.MORA_MEDIA,
    "91+": SegmentoMora.MORA_ALTA,
}
"""El segmento sale del tramo de atraso: las fronteras en dias tienen una sola fuente,
`atraso.TRAMOS_ATRASO`. Los cinco tramos del resumen se juntan en cuatro segmentos; la frontera
60/61 sigue en el resumen y aqui no cambia nada. Un tramo que no este aqui hace fallar la decision
con KeyError, en lugar de adivinarle un segmento."""

MOTIVO_POR_SEGMENTO: dict[SegmentoMora, CodigoMotivo] = {
    SegmentoMora.AL_CORRIENTE: CodigoMotivo.SIN_MORA,
    SegmentoMora.MORA_TEMPRANA: CodigoMotivo.MORA_1_30,
    SegmentoMora.MORA_MEDIA: CodigoMotivo.MORA_31_90,
    SegmentoMora.MORA_ALTA: CodigoMotivo.MORA_91_MAS,
}

PRIORIDAD_BASE: dict[SegmentoMora, Prioridad] = {
    SegmentoMora.AL_CORRIENTE: Prioridad.BAJA,
    SegmentoMora.MORA_TEMPRANA: Prioridad.MEDIA,
    SegmentoMora.MORA_MEDIA: Prioridad.ALTA,
    SegmentoMora.MORA_ALTA: Prioridad.MUY_ALTA,
}

SUBIR_UN_NIVEL: dict[Prioridad, Prioridad] = {
    Prioridad.MEDIA: Prioridad.ALTA,
    Prioridad.ALTA: Prioridad.MUY_ALTA,
    Prioridad.MUY_ALTA: Prioridad.MUY_ALTA,
}
"""El ajuste por saldo sube a lo mas un nivel, con tope en MUY_ALTA. BAJA no esta: es la
prioridad de una cuenta al corriente, y esa nunca sube por saldo."""

CANAL_POR_PRIORIDAD: dict[Prioridad, str] = {
    Prioridad.BAJA: "DIGITAL",
    Prioridad.MEDIA: "DIGITAL",
    Prioridad.ALTA: "TELEFONICA",
    Prioridad.MUY_ALTA: "CAMPO",
}
"""El canal recomendado sale solo de la prioridad final, nunca del canal de la cartera. Sus
valores son los del catalogo de canales del contrato (`CANALES`); no se importan de ahi porque
eso arrastraria pandas y pandera, y una prueba exige que coincidan."""


@dataclass(frozen=True)
class EntradaDecision:
    """Lo que `decision/v1` necesita de una cuenta: sus dias de atraso y su saldo, nada mas.

    Producto, canal y claves geograficas no entran porque estas reglas no los usan. Asi no pueden
    influir por descuido, y el canal recomendado no puede ser una copia del original.

    Se valida al construirse, fail-closed: lo que el contrato no dejaria publicar no se decide.
    """

    dias_atraso: int
    saldo_total: Decimal
    """En pesos, como sale de la base: Decimal, nunca float."""

    def __post_init__(self) -> None:
        # bool es un int para Python, pero True no es un dia de atraso.
        if isinstance(self.dias_atraso, bool) or not isinstance(self.dias_atraso, int):
            raise TypeError(
                f"dias_atraso debe ser un entero, no {type(self.dias_atraso).__name__}."
            )
        if self.dias_atraso < 0:
            raise ValueError(f"dias_atraso no puede ser negativo: {self.dias_atraso}.")
        if not isinstance(self.saldo_total, Decimal):
            raise TypeError(
                f"saldo_total debe ser Decimal, no {type(self.saldo_total).__name__}: los saldos "
                "se comparan y se escriben en centavos exactos."
            )
        if not self.saldo_total.is_finite() or self.saldo_total < 0:
            raise ValueError(
                f"saldo_total debe ser un numero finito y no negativo: {self.saldo_total}."
            )


@dataclass(frozen=True)
class MotivoDecision:
    """Un paso de la decision: que regla aplico, sobre que campo y con que valor."""

    codigo: CodigoMotivo
    campo: str
    """El campo que la regla leyo (`dias_atraso`, `saldo_total`) o el que produjo (`prioridad`,
    `canal_recomendado`)."""
    valor: str
    """En texto canonico: los dias como entero y el saldo con dos decimales."""


@dataclass(frozen=True)
class ResultadoDecision:
    """La decision de una cuenta, con sus motivos en el orden en que se aplicaron las reglas."""

    segmento: SegmentoMora
    prioridad: Prioridad
    canal_recomendado: str
    """Uno de los canales del contrato. No es el canal de la cartera: ese es un dato de entrada y
    este es una decision, y no tienen por que coincidir."""
    motivos: tuple[MotivoDecision, ...]


def decidir_cuenta(entrada: EntradaDecision) -> ResultadoDecision:
    """Decide una cuenta con `decision/v1`.

    El orden importa, y es el de los motivos: los dias de atraso dan el segmento; el segmento, la
    prioridad base; el saldo, si hay atraso, la sube a lo mas un nivel; la prioridad final da el
    canal. SALDO_ALTO se anota siempre que la regla aplica, aunque la prioridad ya este en el tope
    y no cambie: asi se ve que se evaluo.
    """
    segmento = SEGMENTO_POR_TRAMO[tramo_de_atraso(entrada.dias_atraso)]
    motivos = [
        MotivoDecision(MOTIVO_POR_SEGMENTO[segmento], "dias_atraso", str(entrada.dias_atraso))
    ]

    prioridad = PRIORIDAD_BASE[segmento]
    if entrada.dias_atraso > 0 and entrada.saldo_total >= UMBRAL_SALDO_ALTO:
        prioridad = SUBIR_UN_NIVEL[prioridad]
        saldo = str(entrada.saldo_total.quantize(CENTAVO, rounding=ROUND_HALF_UP))
        motivos.append(MotivoDecision(CodigoMotivo.SALDO_ALTO, "saldo_total", saldo))

    canal = CANAL_POR_PRIORIDAD[prioridad]
    # El codigo de la prioridad y el del canal son su valor con prefijo. Si alguno no existe en el
    # catalogo, CodigoMotivo falla: no hay motivos fuera de el.
    motivos += [
        MotivoDecision(CodigoMotivo(f"PRIORIDAD_{prioridad}"), "prioridad", prioridad.value),
        MotivoDecision(CodigoMotivo(f"CANAL_{canal}"), "canal_recomendado", canal),
    ]
    return ResultadoDecision(segmento, prioridad, canal, tuple(motivos))
