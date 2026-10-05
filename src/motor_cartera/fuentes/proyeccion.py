"""La proyeccion operacional: de cartera/v2 a Cuenta, que es lo que leen los motores.

decision/v1, territorial/v1 y ruteo/v1 no conocen las 93 columnas de cartera/v2: leen Cuenta, que
tiene ocho. La proyeccion es explicita, esta versionada y no hace nada mas que esto:

    CLIENTE_UNICO               -> cliente_unico
    SALDO_TOTAL                 -> saldo_total
    DIAS_ATRASO                 -> dias_atraso
    PRODUCTO                    -> producto
    CANAL                       -> canal
    ESTADO_CTE + POBLACION_CTE  -> cve_entidad + cve_municipio, con el catalogo del INEGI
    la fecha de corte del lote  -> fecha_corte

Cada valor sale de la forma canonica del registro, que ya cumplio el contrato: el saldo con dos
decimales, los dias como entero. Un registro cuya geografia no se resuelve se rechaza con su motivo
antes de publicar nada (ver `rechazos_geograficos`), asi que una Cuenta nunca nace sin sus claves.
No se calcula ningun score ni ninguna variable de negocio: eso no es proyectar.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pandas as pd

from motor_cartera.contratos.cartera import Motivo
from motor_cartera.fuentes.geografia import catalogo, resolver

VERSION_PROYECCION = "operacional/v1"
"""Cambia si cambia el mapeo o el catalogo geografico: una cuenta proyectada con otro catalogo
podria tener otras claves, y cada corrida tiene que decir con cual se proyecto."""

COLUMNAS_CUENTA = (
    "corrida_id",
    "cliente_unico",
    "saldo_total",
    "dias_atraso",
    "producto",
    "canal",
    "cve_entidad",
    "cve_municipio",
    "fecha_corte",
)
"""Las columnas de Cuenta que llena la proyeccion, en el orden en que se copian."""


def catalogo_consultado() -> str:
    """Cuando se consulto el catalogo del INEGI que usa esta version de la proyeccion."""
    return catalogo().consultado


def rechazos_geograficos(canonicos: pd.DataFrame) -> dict[int, list[Motivo]]:
    """Los registros validos cuya geografia no se resuelve, con el motivo de cada uno."""
    resolucion = resolver(canonicos["ESTADO_CTE"], canonicos["POBLACION_CTE"])
    sin_resolver = resolucion.motivo.notna().to_numpy()
    return {
        int(fila): [Motivo(str(campo), str(regla))]
        for fila, campo, regla in zip(
            canonicos.index[sin_resolver],
            resolucion.campo[sin_resolver],
            resolucion.motivo[sin_resolver],
            strict=True,
        )
    }


def filas_cuenta(
    canonicos: pd.DataFrame, corrida_id: int, fecha_corte: date
) -> Iterator[tuple[object, ...]]:
    """Una fila de Cuenta por registro, en el orden de COLUMNAS_CUENTA.

    Los registros ya pasaron por `rechazos_geograficos`; si alguno no se resuelve, es un error de
    programacion y no una cuenta sin claves: se levanta, y la corrida no publica nada.
    """
    resolucion = resolver(canonicos["ESTADO_CTE"], canonicos["POBLACION_CTE"])
    if resolucion.motivo.notna().any():
        raise ValueError("Hay registros sin geografia resuelta: debieron rechazarse antes.")
    yield from (
        (corrida_id, cliente, saldo, dias, producto, canal, entidad, municipio, fecha_corte)
        for cliente, saldo, dias, producto, canal, entidad, municipio in zip(
            canonicos["CLIENTE_UNICO"],
            canonicos["SALDO_TOTAL"],
            canonicos["DIAS_ATRASO"],
            canonicos["PRODUCTO"],
            canonicos["CANAL"],
            resolucion.cve_entidad,
            resolucion.cve_municipio,
            strict=True,
        )
    )
