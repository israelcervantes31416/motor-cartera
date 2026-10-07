"""Lo que comparten las pruebas del modelo historico: cortes y pagos sinteticos con cuentas
elegidas, ingeridos como cualquier fuente oficial, y la foto logica de toda la capa historica.

Las carteras salen del generador oficial (`tabla_cartera` sobre un estado armado a mano): las 93
columnas, con nombres, domicilios y telefonos derivados del numero de cliente. Los pagos se arman
fila por fila con las 23 columnas de pagos/v1. Todo es sintetico.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sqlmodel import select

from motor_cartera.contratos.pagos import CONTRATO_PAGOS
from motor_cartera.db.modelos import (
    Corrida,
    CorteCanonico,
    CuentaCanonica,
    DatasetConformado,
    EjecucionHistoria,
    IngestaPagos,
    PagoObservado,
    SnapshotCuenta,
)
from motor_cartera.db.sesion import sesion
from motor_cartera.generador.oficial import EstadoCartera, escribir_cartera, escribir_pagos
from motor_cartera.historia import cuenta360
from motor_cartera.ingesta.corridas import ingerir_archivo
from motor_cartera.ingesta.pagos import ingerir_pagos

SEMILLA = 7


def cliente(numero: int) -> str:
    """El CLIENTE_UNICO que el generador le da a un numero de cliente."""
    return f"CU{numero:010d}"


@dataclass(frozen=True)
class Cuenta:
    """Una cuenta de un corte: su numero de cliente, su saldo en pesos y sus dias de atraso."""

    numero: int
    saldo: int = 10_000
    dias: int = 15


def estado(cuentas: list[Cuenta]) -> EstadoCartera:
    """El estado de un corte con exactamente esas cuentas, en ese orden."""
    n = len(cuentas)
    dias = np.array([c.dias for c in cuentas], dtype=np.int64)
    return EstadoCartera(
        cliente=np.array([c.numero for c in cuentas], dtype=np.int64),
        saldo_centavos=np.array([c.saldo * 100 for c in cuentas], dtype=np.int64),
        moratorios_centavos=np.array([c.dias * 100 for c in cuentas], dtype=np.int64),
        dias_atraso=dias,
        atraso_maximo=dias + 30,
        asignacion=np.full(n, np.datetime64("2026-01-15", "D")),
        pagos=np.zeros(n, dtype=np.int64),
        monto_pagos_centavos=np.zeros(n, dtype=np.int64),
        ultimo_pago=np.full(n, np.datetime64("NaT", "D")),
        ultimo_pago_centavos=np.zeros(n, dtype=np.int64),
    )


def escribir_corte(
    destino: Path, corte: date, cuentas: list[Cuenta], *, formato: str = "csv", nombre: str = ""
) -> Path:
    """El archivo de cartera/v2 de un corte con esas cuentas."""
    ruta = destino / f"{nombre or 'cartera'}_{corte.isoformat()}.{formato}"
    return escribir_cartera(
        estado(cuentas), ruta, semilla=SEMILLA, fecha_corte=corte, con_carrier=formato != "csv"
    ).ruta


def ingerir_corte(destino: Path, corte: date, cuentas: list[Cuenta], **opciones) -> Corrida:
    """Un corte ingerido como cualquier cartera oficial: su dataset conformado y su historia en la
    cola. No materializa nada."""
    ruta = escribir_corte(destino, corte, cuentas, **opciones)
    corrida = ingerir_archivo(ruta, contrato="cartera/v2", fecha_corte=corte)
    assert corrida.estado == "EXITOSA", corrida.detalle
    return corrida


def pago(numero: int, recepcion: str, monto: str, **otros: str | None) -> dict[str, str | None]:
    """Un movimiento de pagos/v1, con lo minimo y lo que se le pase."""
    fila: dict[str, str | None] = dict.fromkeys(CONTRATO_PAGOS.nombres)
    fila.update(
        {
            "Año": recepcion[:4],
            "Semana": "37",
            "Cliente_Unico": cliente(numero),
            "Fecha_Recepción": recepcion,
            "Recuperación_por_Gestión": monto,
            "Concepto_Cálculo": "PAGO NORMAL",
            "Territorio": "TERRITORIO 21",
            "Producto": "CONSUMO",
            "Gestor": "GESTOR 007",
            "Días_de_Atraso": "12",
            "Cobranza_Total": monto,
            "Porcentaje_Comision": "0.08",
        }
    )
    fila.update(otros)
    return fila


def ingerir_pagos_de(destino: Path, nombre: str, filas: list[dict]) -> IngestaPagos:
    ruta = escribir_pagos(
        pd.DataFrame(filas, columns=list(CONTRATO_PAGOS.nombres)), destino / nombre
    )
    ingesta = ingerir_pagos(ruta.ruta)
    assert ingesta.estado == "EXITOSA", ingesta.detalle
    return ingesta


def historia_de(*, corrida: Corrida | None = None, ingesta: IngestaPagos | None = None):
    """La ultima ejecucion historica del dataset de una corrida o de una ingesta de pagos."""
    with sesion() as s:
        condicion = (
            DatasetConformado.corrida_id == corrida.id
            if corrida is not None
            else DatasetConformado.ingesta_pagos_id == ingesta.id
        )
        return s.exec(
            select(EjecucionHistoria)
            .join(
                DatasetConformado, DatasetConformado.id == EjecucionHistoria.dataset_conformado_id
            )
            .where(condicion)
            .order_by(EjecucionHistoria.id.desc())
        ).first()


def cuenta(numero: int, cartera_id: str = "CARTERA_PRINCIPAL") -> CuentaCanonica:
    with sesion() as s:
        return cuenta360.buscar(s, "DSP_001", cartera_id, cliente(numero))


def foto() -> dict:
    """La capa historica entera, como se ve desde fuera: sin ids internos ni instantes de
    creacion, que dependen de cuando y en que orden se materializo. Lo demas, incluidos los
    identificadores publicos, tiene que ser identico si la historia se reconstruye."""
    with sesion() as s:
        cuentas = {c.id: c for c in s.exec(select(CuentaCanonica)).all()}
        datasets = {d.id: d.dataset_id for d in s.exec(select(DatasetConformado)).all()}
        cortes = {c.id: c for c in s.exec(select(CorteCanonico)).all()}
        snapshots = s.exec(select(SnapshotCuenta)).all()
        pagos = s.exec(select(PagoObservado)).all()
        vista = {
            "cuentas": sorted(
                (c.cuenta_id, c.despacho_id, c.cartera_id, c.cliente_unico)
                for c in cuentas.values()
            ),
            "cortes": sorted(
                (
                    c.corte_id,
                    c.despacho_id,
                    c.cartera_id,
                    c.fecha_corte,
                    c.firma_contenido,
                    c.version_modelo,
                    c.cuentas,
                    datasets[c.dataset_conformado_id],
                )
                for c in cortes.values()
            ),
            "snapshots": sorted(
                (
                    cortes[x.corte_canonico_id].corte_id,
                    cuentas[x.cuenta_canonica_id].cuenta_id,
                    *_sin(x, ("corte_canonico_id", "cuenta_canonica_id")),
                )
                for x in snapshots
            ),
            "pagos": sorted(
                (datasets[p.dataset_conformado_id], *_sin(p, ("dataset_conformado_id",)))
                for p in pagos
            ),
        }
        vista["cuenta360"] = {
            c.cuenta_id: _cuenta_360(s, c) for c in sorted(cuentas.values(), key=lambda c: c.id)
        }
    return vista


def _sin(fila, ocultas: tuple[str, ...]) -> tuple:
    return tuple(
        (columna, getattr(fila, columna))
        for columna in sorted(type(fila).model_fields)
        if columna not in ocultas
    )


def _cuenta_360(s, c: CuentaCanonica) -> tuple:
    resumen = cuenta360.resumen(s, c)
    _, historia = cuenta360.historia(s, c, desplazamiento=0, limite=500)
    _, pagos = cuenta360.pagos_observados(s, c, desplazamiento=0, limite=500)
    return (
        resumen.presencia,
        resumen.pagos_observados,
        None if resumen.snapshot_actual is None else resumen.snapshot_actual.corte_id,
        resumen.ultimo_snapshot_observado.corte_id,
        tuple(cuenta360.eventos(s, c)),
        tuple(
            (h.visto.corte_id, h.enlace, h.delta_saldo_total, h.delta_dias_atraso) for h in historia
        ),
        tuple(p.pago.pago_observado_id for p in pagos),
    )


# --- el escenario golden longitudinal ------------------------------------------------------------

GOLDEN_CORTES = [date(2026, 8, 5) + timedelta(days=7 * i) for i in range(6)]
"""Seis cortes semanales."""

GOLDEN_PRESENCIA = {
    1: (1, 2, 3, 4, 5, 6),  # siempre presente
    2: (1, 2, 3),  # sale en el 4 y no vuelve
    3: (1, 2, 5, 6),  # sale en el 3 y reingresa en el 5
    4: (3, 4, 5, 6),  # aparece por primera vez en el 3
    5: (1, 2, 3, 4, 5, 6),  # paga varias veces
    6: (1, 2, 3, 4, 5, 6),  # tiene dos pagos identicos
    8: (1, 3, 5, 6),  # sale y reingresa dos veces
}
"""En que cortes (desde 1) aparece cada cuenta. El 7 no aparece en ninguno: solo tiene un pago."""

GOLDEN_RELLENO = range(100, 120)
"""Veinte cuentas mas, en todos los cortes, para que la cartera no sea de juguete."""

GOLDEN_SALDOS = {3: {1: (9_000, 30), 2: (8_800, 37), 5: (8_000, 0), 6: (7_900, 7)}}
"""El saldo y los dias de atraso de la cuenta 3 en cada corte en que aparece."""

GOLDEN_PAGOS = {
    1: [
        pago(5, "2026-08-06 10:00:00", "500.00"),
        pago(7, "2026-08-07 11:30:00", "200.00"),
        pago(6, "2026-08-08 09:15:00", "300.00"),
        pago(6, "2026-08-08 09:15:00", "300.00"),
    ],
    2: [pago(5, "2026-08-13 10:00:00", "600.00"), pago(1, "2026-08-14 16:45:00", "1000.00")],
    3: [pago(5, "2026-08-20 10:00:00", "700.00"), pago(3, "2026-08-21 12:00:00", "50.00")],
    4: [pago(2, "2026-08-28 08:00:00", "100.00")],
    5: [pago(4, "2026-09-03 13:00:00", "250.00")],
}
"""Los pagos de cada periodo: del dia siguiente a un corte hasta el siguiente corte."""


@dataclass(frozen=True)
class Golden:
    corridas: list[Corrida]
    ingestas: list[IngestaPagos]


def golden(destino: Path) -> Golden:
    """Ingiere el escenario golden, como cualquier fuente oficial. No materializa nada."""
    corridas = []
    for numero, corte in enumerate(GOLDEN_CORTES, start=1):
        cuentas = [Cuenta(n) for n in GOLDEN_RELLENO]
        for cliente_, cortes in GOLDEN_PRESENCIA.items():
            if numero in cortes:
                saldo, dias = GOLDEN_SALDOS.get(cliente_, {}).get(numero, (10_000, 15))
                cuentas.append(Cuenta(cliente_, saldo=saldo, dias=dias))
        corridas.append(ingerir_corte(destino, corte, cuentas))
    ingestas = [
        ingerir_pagos_de(destino, f"pagos_golden_{periodo}.csv", filas)
        for periodo, filas in GOLDEN_PAGOS.items()
    ]
    return Golden(corridas, ingestas)
