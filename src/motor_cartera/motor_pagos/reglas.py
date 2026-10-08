"""motor-pagos/v1 como nucleo puro: que concluye el motor de cada observacion, y por que.

El motor no borra ni corrige observaciones: las interpreta. Cada PagoObservado de la ventana recibe
exactamente una clasificacion, y cada grupo de observaciones que v1 considera un mismo hecho
economico, distinto de los demas, funda un movimiento canonico. Prefiere no interpretar a
interpretar mal dinero: ante la duda, una observacion queda sin movimiento.

Las clasificaciones de una observacion:

- MOVIMIENTO_PRIMARIO: funda un movimiento de importe positivo (un PAGO). Si hay copias exactas
  suyas, es su representante.
- DUPLICADO_EXACTO: es identica, en sus 23 campos y en su despacho y cartera, al representante de su
  grupo: el mismo hecho reportado otra vez. Apunta al mismo movimiento y no suma nada.
- COINCIDENCIA_AMBIGUA: comparte la llave historica (cliente, segundo de recepcion e importe) con
  otra observacion que difiere en algun otro campo. v1 no sabe si son uno o dos pagos: no funda
  movimiento y no suma nada.
- REVERSO: funda un movimiento negativo que forma, con un PAGO del mismo cliente y del mismo importe
  recibido hasta 30 dias antes, una pareja aislada: ese pago es su unico original posible y el es el
  unico reverso posible de ese pago. El pago queda anulado.
- POSIBLE_REVERSO: funda un movimiento negativo que no forma esa pareja: no tiene ningun original
  posible, tiene varios, su unico original es ambiguo o lo disputa con otro reverso. v1 no elige: no
  anula ningun pago, y su importe resta solo en la recuperacion neta.
- NO_CONCILIADO: v1 no puede decidir que movimiento representa: su importe es cero, o su grupo de
  copias no paso la comprobacion campo por campo (dos observaciones distintas con la misma huella).

La conciliacion con la cuenta es otra cosa y va aparte: una observacion de cualquier clasificacion
es CONCILIADO_CUENTA si hay una CuentaCanonica con su despacho, cartera y Cliente_Unico, o
SIN_CUENTA_OBSERVADA si no. Un pago sin cuenta se interpreta igual: no se descarta ni crea una
cuenta.

La recuperacion interpretada, que no es contabilidad del acreedor:

- bruta: la suma de los PAGO que no anulo un REVERSO;
- neta: la bruta mas los POSIBLE_REVERSO, que son negativos. Un REVERSO y el pago que anula suman
  cero juntos y no entran en ninguna de las dos.

Este modulo es la referencia legible: el motor lo hace en PostgreSQL, por conjuntos, sobre millones
de observaciones (`motor_pagos.ejecuciones`), y una prueba comprueba que los dos dicen lo mismo. No
sabe de la base.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from motor_cartera.motor_pagos.firmas import movimiento_id

VERSION_MOTOR_PAGOS = "motor-pagos/v1"
"""Cambia si cambia cualquier regla de este modulo: las huellas, los grupos, los reversos, la
ventana o la recuperacion."""

VENTANA_REVERSO = timedelta(days=30)
"""Hasta cuanto despues de un pago puede llegar su reverso, en motor-pagos/v1. Ninguna columna de
pagos/v1 dice cual es el original de un reverso: la pareja se busca por cliente, importe y tiempo,
y la ventana es la regla de esta version."""


class Clasificacion(StrEnum):
    MOVIMIENTO_PRIMARIO = "MOVIMIENTO_PRIMARIO"
    DUPLICADO_EXACTO = "DUPLICADO_EXACTO"
    COINCIDENCIA_AMBIGUA = "COINCIDENCIA_AMBIGUA"
    REVERSO = "REVERSO"
    POSIBLE_REVERSO = "POSIBLE_REVERSO"
    NO_CONCILIADO = "NO_CONCILIADO"


class EstadoConciliacion(StrEnum):
    CONCILIADO_CUENTA = "CONCILIADO_CUENTA"
    SIN_CUENTA_OBSERVADA = "SIN_CUENTA_OBSERVADA"


class TipoMovimiento(StrEnum):
    PAGO = "PAGO"
    REVERSO = "REVERSO"
    POSIBLE_REVERSO = "POSIBLE_REVERSO"


class SignoEconomico(StrEnum):
    """Lo que hace el movimiento con la recuperacion. Un movimiento de importe cero no existe: su
    observacion queda NO_CONCILIADO."""

    SUMA = "SUMA"
    RESTA = "RESTA"


class Motivo(StrEnum):
    """Por que una observacion quedo como quedo. Una observacion tiene uno o dos."""

    OBSERVACION_UNICA = "OBSERVACION_UNICA"
    REPRESENTANTE_DE_COPIAS = "REPRESENTANTE_DE_COPIAS"
    COPIA_EXACTA = "COPIA_EXACTA"
    LLAVE_HISTORICA_COMPARTIDA = "LLAVE_HISTORICA_COMPARTIDA"
    PAREJA_UNICA = "PAREJA_UNICA"
    ANULADO_POR_REVERSO = "ANULADO_POR_REVERSO"
    SIN_CANDIDATOS = "SIN_CANDIDATOS"
    VARIOS_CANDIDATOS = "VARIOS_CANDIDATOS"
    CANDIDATO_AMBIGUO = "CANDIDATO_AMBIGUO"
    ORIGINAL_DISPUTADO = "ORIGINAL_DISPUTADO"
    IMPORTE_CERO = "IMPORTE_CERO"
    FIRMA_SIN_VALIDAR = "FIRMA_SIN_VALIDAR"


# --- las ventanas ---------------------------------------------------------------------------------


def periodo_de(instante: date | datetime) -> date:
    """El primer dia del mes calendario de un instante: la ventana a la que pertenece."""
    return date(instante.year, instante.month, 1)


def siguiente_periodo(periodo: date) -> date:
    if periodo.day != 1:
        raise ValueError(f"Un periodo empieza el dia 1: {periodo}.")
    return date(periodo.year + periodo.month // 12, periodo.month % 12 + 1, 1)


def periodos_entre(desde: date | datetime, hasta: date | datetime) -> list[date]:
    """Los periodos que tocan el intervalo cerrado de `desde` a `hasta`, en orden."""
    resultado, actual, ultimo = [], periodo_de(desde), periodo_de(hasta)
    while actual <= ultimo:
        resultado.append(actual)
        actual = siguiente_periodo(actual)
    return resultado


# --- el nucleo ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Observacion:
    """Lo que el nucleo necesita de un pago observado."""

    dataset_conformado_id: int
    source_row: int
    orden: tuple[str, int]
    """El SHA-256 de su archivo original y su fila: de donde sale el representante de sus copias,
    sin depender del orden de llegada ni de identificadores sorteados."""
    propia: bool
    """Si es de la ventana que se interpreta; las demas son su contexto, y no reciben resultado."""
    con_cuenta: bool
    cliente_unico: str
    fecha_recepcion: datetime
    recuperacion: Decimal
    firma_exacta: bytes
    firma_legacy: bytes
    valores: tuple = field(default=(), compare=False)
    """Sus 23 valores, para comprobar las copias campo por campo."""


@dataclass(frozen=True)
class Resultado:
    observacion: Observacion
    clasificacion: Clasificacion
    estado_conciliacion: EstadoConciliacion
    movimiento_id: UUID | None
    movimiento_relacionado_id: UUID | None
    motivos: tuple[Motivo, ...]


@dataclass(frozen=True)
class Movimiento:
    movimiento_id: UUID
    tipo: TipoMovimiento
    signo: SignoEconomico
    representante: Observacion
    observaciones: int
    estado_conciliacion: EstadoConciliacion
    original_id: UUID | None
    anulado_por_id: UUID | None


@dataclass(frozen=True)
class _Unidad:
    """Lo que cuenta como un candidato al buscar parejas: un grupo de copias limpio, o todo un grupo
    de la llave historica que es ambiguo, que v1 no sabe si es uno o varios pagos."""

    clave: tuple
    cliente_unico: str
    importe: Decimal
    signo: int
    fecha: datetime
    limpia: bool


def interpretar(
    observaciones: Sequence[Observacion],
    *,
    version: str = VERSION_MOTOR_PAGOS,
    ventana: timedelta = VENTANA_REVERSO,
) -> tuple[list[Resultado], list[Movimiento]]:
    """Un resultado por cada observacion propia y un movimiento por cada grupo propio que v1
    interpreta. Las observaciones de contexto solo cuentan como candidatos de una pareja."""
    exactos: dict[bytes, list[Observacion]] = defaultdict(list)
    for observacion in sorted(observaciones, key=lambda o: o.orden):
        exactos[observacion.firma_exacta].append(observacion)
    firmas_por_legacy: dict[bytes, set[bytes]] = defaultdict(set)
    for firma, grupo in exactos.items():
        firmas_por_legacy[grupo[0].firma_legacy].add(firma)

    def ambiguo(grupo: list[Observacion]) -> bool:
        return len(firmas_por_legacy[grupo[0].firma_legacy]) > 1

    def valido(grupo: list[Observacion]) -> bool:
        return all(o.valores == grupo[0].valores for o in grupo)

    unidad_de: dict[bytes, _Unidad] = {}
    for firma, grupo in exactos.items():
        rep = grupo[0]
        if ambiguo(grupo):
            miembros = [o for f in firmas_por_legacy[rep.firma_legacy] for o in exactos[f]]
            unidad = _Unidad(
                ("L", rep.firma_legacy),
                rep.cliente_unico,
                abs(rep.recuperacion),
                _signo(rep.recuperacion),
                min(o.fecha_recepcion for o in miembros),
                False,
            )
        else:
            unidad = _Unidad(
                ("E", firma),
                rep.cliente_unico,
                abs(rep.recuperacion),
                _signo(rep.recuperacion),
                rep.fecha_recepcion,
                valido(grupo),
            )
        unidad_de[firma] = unidad

    # Las parejas posibles: un pago y un negativo del mismo cliente y el mismo importe, con el
    # negativo recibido hasta `ventana` despues del pago.
    unidades = set(unidad_de.values())
    negativas = [u for u in unidades if u.signo < 0]
    positivas_por_llave: dict[tuple, list[_Unidad]] = defaultdict(list)
    for u in unidades:
        if u.signo > 0:
            positivas_por_llave[(u.cliente_unico, u.importe)].append(u)
    candidatos: dict[_Unidad, list[_Unidad]] = {}
    reclamantes: dict[_Unidad, list[_Unidad]] = defaultdict(list)
    for n in negativas:
        posibles = [
            p
            for p in positivas_por_llave[(n.cliente_unico, n.importe)]
            if p.fecha <= n.fecha <= p.fecha + ventana
        ]
        candidatos[n] = posibles
        for p in posibles:
            reclamantes[p].append(n)

    def pareja(n: _Unidad) -> _Unidad | None:
        posibles = candidatos[n]
        if len(posibles) != 1:
            return None
        (p,) = posibles
        if n.limpia and p.limpia and len(reclamantes[p]) == 1:
            return p
        return None

    anulado_por: dict[_Unidad, _Unidad] = {}
    for n in negativas:
        p = pareja(n)
        if p is not None:
            anulado_por[p] = n
    id_de: dict[_Unidad, UUID] = {
        u: movimiento_id(version, u.clave[1]) for u in unidades if u.clave[0] == "E"
    }

    resultados: list[Resultado] = []
    movimientos: list[Movimiento] = []
    for firma, grupo in exactos.items():
        rep = grupo[0]
        if not rep.propia:
            continue
        unidad = unidad_de[firma]
        if ambiguo(grupo):
            for o in grupo:
                resultados.append(
                    _resultado(
                        o, Clasificacion.COINCIDENCIA_AMBIGUA, Motivo.LLAVE_HISTORICA_COMPARTIDA
                    )
                )
            continue
        if not unidad.limpia or rep.recuperacion == 0:
            motivo = Motivo.FIRMA_SIN_VALIDAR if not unidad.limpia else Motivo.IMPORTE_CERO
            for o in grupo:
                resultados.append(_resultado(o, Clasificacion.NO_CONCILIADO, motivo))
            continue
        propio = id_de[unidad]
        original = anulador = None
        if unidad.signo > 0:
            tipo, clase = TipoMovimiento.PAGO, Clasificacion.MOVIMIENTO_PRIMARIO
            motivos = [
                Motivo.OBSERVACION_UNICA if len(grupo) == 1 else Motivo.REPRESENTANTE_DE_COPIAS
            ]
            if unidad in anulado_por:
                anulador = id_de[anulado_por[unidad]]
                motivos.append(Motivo.ANULADO_POR_REVERSO)
            relacionado = anulador
        else:
            p = pareja(unidad)
            if p is not None:
                tipo, clase = TipoMovimiento.REVERSO, Clasificacion.REVERSO
                original = id_de[p]
                motivos = [Motivo.PAREJA_UNICA]
            else:
                tipo, clase = TipoMovimiento.POSIBLE_REVERSO, Clasificacion.POSIBLE_REVERSO
                motivos = [_por_que_no_hay_pareja(unidad, candidatos, reclamantes)]
            relacionado = original
        conciliacion = _conciliacion(rep)
        movimientos.append(
            Movimiento(
                propio,
                tipo,
                SignoEconomico.SUMA if unidad.signo > 0 else SignoEconomico.RESTA,
                rep,
                len(grupo),
                conciliacion,
                original,
                anulador,
            )
        )
        resultados.append(Resultado(rep, clase, conciliacion, propio, relacionado, tuple(motivos)))
        for copia in grupo[1:]:
            resultados.append(
                Resultado(
                    copia,
                    Clasificacion.DUPLICADO_EXACTO,
                    _conciliacion(copia),
                    propio,
                    None,
                    (Motivo.COPIA_EXACTA,),
                )
            )
    return resultados, movimientos


def _signo(importe: Decimal) -> int:
    return (importe > 0) - (importe < 0)


def _conciliacion(observacion: Observacion) -> EstadoConciliacion:
    if observacion.con_cuenta:
        return EstadoConciliacion.CONCILIADO_CUENTA
    return EstadoConciliacion.SIN_CUENTA_OBSERVADA


def _resultado(observacion: Observacion, clase: Clasificacion, motivo: Motivo) -> Resultado:
    return Resultado(observacion, clase, _conciliacion(observacion), None, None, (motivo,))


def _por_que_no_hay_pareja(
    n: _Unidad, candidatos: dict[_Unidad, list[_Unidad]], reclamantes: dict[_Unidad, list[_Unidad]]
) -> Motivo:
    posibles = candidatos[n]
    if not posibles:
        return Motivo.SIN_CANDIDATOS
    if len(posibles) > 1:
        return Motivo.VARIOS_CANDIDATOS
    if not posibles[0].limpia:
        return Motivo.CANDIDATO_AMBIGUO
    return Motivo.ORIGINAL_DISPUTADO
