"""Lo que comparten las pruebas de decision/v2: el escenario golden, como abrir y decidir una
ejecucion, y como leer lo que publico. Todo es sintetico.

El escenario golden tiene una cuenta por cada caso de la mision, con cortes, pagos interpretados,
atribuciones y lifecycle de verdad (ingeridos y registrados como cualquier fuente), y una frontera
de conocimiento K1 despues de la cual llegan, tarde, un corte viejo, una gestion, una promesa y un
evento posterior a as_of. Dos decisiones del mismo corte: D1 con known_at K1 y D2 con known_at K2,
despues de todo.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from historia_escenarios import Cuenta, cliente, cuenta, ingerir_corte, ingerir_pagos_de, pago
from lifecycle_escenarios import anular_gestion, cancelar_promesa, gestion_de, instante, llave
from motor_pagos_escenarios import historiar_todo, interpretar_todo
from sqlalchemy import text
from sqlmodel import select

from motor_cartera.atribucion.ejecuciones import abrir as abrir_atribucion
from motor_cartera.atribucion.ejecuciones import atribuir
from motor_cartera.db.modelos import (
    CorteCanonico,
    CuentaCanonica,
    EjecucionDecisionV2,
    FeatureDecisionCuenta,
    ModoDecisionV2,
    ResultadoDecisionV2,
)
from motor_cartera.db.sesion import sesion
from motor_cartera.decision_v2 import ejecuciones as motor
from motor_cartera.decision_v2.features import FEATURES
from motor_cartera.lifecycle import registro
from motor_cartera.lifecycle.reglas import Canal, Medio, NivelContacto, ResultadoGestion
from motor_cartera.motor_pagos.ejecuciones import Ventana

ZONA = "America/Mexico_City"
DESPACHO, CARTERA = "DSP_001", "CARTERA_PRINCIPAL"
AS_OF = date(2026, 9, 16)
C0, C1, C2, C3, C4 = (
    date(2026, 8, 19),
    date(2026, 8, 26),
    date(2026, 9, 2),
    date(2026, 9, 9),
    AS_OF,
)

DIAS = {3: 45, 5: 45, 7: 45, 9: 45, 12: 45, 13: 45, 22: 120, 23: 0, 24: 45, 2: 45}
"""Los dias de atraso de cada cuenta en el corte objetivo; las demas, 15."""

SIN_HISTORIA = 1
REINGRESO, AUSENTE_ANTES = 10, 11
CORTE_TARDIO = 17
SALIDA = 30
"""La 30 esta en C3 y no en C4: no es del universo, aunque tenga gestiones."""


def _cuentas_de(fecha: date) -> list[Cuenta]:
    """Las cuentas de cada corte: todas en C1..C4, salvo las que faltan a proposito."""
    numeros = [n for n in range(1, 32) if n != SALIDA]
    if fecha == C0:
        return [Cuenta(CORTE_TARDIO)]
    cuentas = []
    for n in [*numeros, SALIDA]:
        if n == SIN_HISTORIA and fecha != C4:
            continue
        if n == REINGRESO and fecha == C2:
            continue
        if n == AUSENTE_ANTES and fecha == C3:
            continue
        if n == CORTE_TARDIO and fecha == C1:
            continue
        if n == SALIDA and fecha == C4:
            continue
        dias = DIAS.get(n, 15)
        saldo = 10_000
        if n == 2 and fecha == C3:
            dias, saldo = 38, 9_000
        cuentas.append(Cuenta(n, saldo=saldo, dias=dias))
    return cuentas


PAGOS = [
    pago(20, "2026-06-10 10:00:00", "100.00"),  # cubre junio: la ventana de 90 dias
    pago(31, "2026-08-15 10:00:00", "400.00"),  # una promesa PARCIAL
    pago(29, "2026-08-20 10:00:00", "1000.00"),  # una promesa CUMPLIDA
    pago(14, "2026-09-10 10:00:00", "400.00"),  # una asociacion unica
    pago(15, "2026-09-10 10:00:00", "250.00"),  # una atribucion ambigua
    pago(12, "2026-09-12 10:00:00", "800.00"),  # revertido el 13
    pago(12, "2026-09-13 10:00:00", "-800.00", **{"Concepto_Cálculo": "AJUSTE"}),
    pago(7, "2026-09-13 10:00:00", "300.00"),
    pago(3, "2026-09-14 10:00:00", "500.00"),
    pago(9, "2026-09-15 10:00:00", "200.00"),
    pago(13, "2026-09-15 10:00:00", "300.00"),  # revertido el 18, despues de as_of
    pago(23, "2026-09-16 10:00:00", "150.00"),
    pago(13, "2026-09-18 10:00:00", "-300.00", **{"Concepto_Cálculo": "AJUSTE"}),
    pago(16, "2026-09-20 10:00:00", "1000.00"),  # futuro: despues de as_of
]


def gestion(numero: int, ocurrido_en: str, **campos):
    """Una gestion de la cuenta `numero` el dia y la hora dados (en la zona de la fuente)."""
    return gestion_de(cuenta(numero).cuenta_id, f"{ocurrido_en}:00-06:00", **campos)


def sin_contacto(numero: int, ocurrido_en: str):
    return gestion(
        numero,
        ocurrido_en,
        nivel_contacto=NivelContacto.SIN_CONTACTO,
        resultado=ResultadoGestion.SIN_RESPUESTA,
    )


def promesa(numero: int, ocurrido_en: str, fecha_limite: str, *, monto: str = "1000.00", **campos):
    """Una gestion con PROMESA, con un tercero por omision (no cuenta como contacto con el titular),
    y su promesa."""
    campos = {"nivel_contacto": NivelContacto.CONTACTO_TERCERO, **campos}
    hecho = gestion(numero, ocurrido_en, **campos)
    creada = registro.crear_promesa(
        hecho.recurso_id,
        llave(),
        registro.DatosPromesa(Decimal(monto), date.fromisoformat(fecha_limite)),
        zona=ZONA,
    )
    return hecho, creada


def convenio(numero: int, ocurrido_en: str, inicio: str, fin: str | None = None):
    hecho = gestion(numero, ocurrido_en, resultado=ResultadoGestion.CONVENIO)
    return registro.crear_convenio(
        hecho.recurso_id,
        llave(),
        registro.DatosConvenio(
            Decimal("3000.00"),
            date.fromisoformat(inicio),
            None if fin is None else date.fromisoformat(fin),
        ),
    )


def reloj() -> datetime:
    """El reloj de la base, en una transaccion propia. Despues se espera un poco: lo que se registre
    luego lleva una marca estrictamente posterior."""
    with sesion() as s:
        ahora = s.execute(text("SELECT now()")).scalar_one()
    time.sleep(0.02)
    return ahora


def atribuir_mes(periodo: date):
    with sesion() as s:
        ejecucion, _ = abrir_atribucion(
            s,
            Ventana.del_periodo(DESPACHO, CARTERA, periodo),
            ventana_dias=30,
            zona=ZONA,
            max_intentos=5,
            reusar=False,
        )
        s.commit()
        ejecucion_id = ejecucion.id
    atribuir(ejecucion_id)


def corte_id(fecha: date) -> UUID:
    with sesion() as s:
        consulta = select(CorteCanonico.corte_id).where(CorteCanonico.fecha_corte == fecha)
        return s.exec(consulta).one()


def abrir_v2(
    fecha: date = AS_OF,
    *,
    known_at: datetime | None = None,
    modo: ModoDecisionV2 = ModoDecisionV2.OPERATIVO,
    **opciones,
) -> int:
    with sesion() as s:
        ejecucion = motor.abrir(
            s,
            corte_id(fecha),
            as_of=opciones.pop("as_of", None),
            known_at=known_at,
            modo=modo,
            version_politica=opciones.pop("version_politica", None),
            zona=opciones.pop("zona", ZONA),
            max_intentos=5,
            **opciones,
        )
        s.commit()
        return ejecucion.id


def decidir_v2(fecha: date = AS_OF, **opciones) -> EjecucionDecisionV2:
    """Abre la decision v2 del corte, la ejecuta como su trabajo (esperando la frontera) y la
    devuelve terminada."""
    ejecucion_id = abrir_v2(fecha, **opciones)
    motor.ejecutar_decision_v2(ejecucion_id)
    return ejecucion(ejecucion_id)


def ejecucion(ejecucion_id: int) -> EjecucionDecisionV2:
    with sesion() as s:
        return s.get_one(EjecucionDecisionV2, ejecucion_id)


def features_de(ejecucion_id: int) -> dict[int, dict]:
    """Las features que publico, por numero de cliente."""
    with sesion() as s:
        filas = s.exec(
            select(FeatureDecisionCuenta, CuentaCanonica.cliente_unico)
            .join(CuentaCanonica, CuentaCanonica.id == FeatureDecisionCuenta.cuenta_canonica_id)
            .where(FeatureDecisionCuenta.ejecucion_decision_v2_id == ejecucion_id)
        ).all()
    return {
        int(cliente_unico[2:]): {
            **{f.nombre: getattr(fila, f.nombre) for f in FEATURES},
            "feature_hash": fila.feature_hash,
        }
        for fila, cliente_unico in filas
    }


def resultados_de(ejecucion_id: int) -> dict[int, ResultadoDecisionV2]:
    """Lo que decidio, por numero de cliente."""
    with sesion() as s:
        filas = s.exec(
            select(ResultadoDecisionV2, CuentaCanonica.cliente_unico)
            .join(CuentaCanonica, CuentaCanonica.id == ResultadoDecisionV2.cuenta_canonica_id)
            .where(ResultadoDecisionV2.ejecucion_decision_v2_id == ejecucion_id)
        ).all()
    return {int(cliente_unico[2:]): resultado for resultado, cliente_unico in filas}


def codigos(resultado: ResultadoDecisionV2) -> list[str]:
    return [m["codigo"] for m in resultado.motivos]


def foto(ejecucion_id: int) -> dict[int, tuple]:
    """Lo que una ejecucion publico de cada cuenta, completo: sus features con su hash, su
    prioridad base, su ajuste, su prioridad final, su canal, sus motivos y su decision_hash."""
    features = features_de(ejecucion_id)
    return {
        numero: (
            features[numero],
            r.prioridad_base,
            r.score_nivel_base,
            r.ajuste_total,
            r.score_nivel_final,
            r.prioridad_final,
            r.canal_recomendado,
            r.motivos,
            r.decision_hash,
        )
        for numero, r in resultados_de(ejecucion_id).items()
    }


@dataclass(frozen=True)
class Golden:
    k1: datetime
    k2: datetime
    d1: EjecucionDecisionV2
    d2: EjecucionDecisionV2
    foto_d1: dict[int, tuple]
    """D1 tal como quedo antes de que existiera D2."""
    gestion_tardia: UUID
    gestion_futura: UUID


def golden(destino) -> Golden:
    """El escenario golden con sus dos decisiones. Ver el docstring del modulo y docs/decision_v2.md
    para lo que prueba cada cuenta."""
    for fecha in (C1, C2, C3, C4):
        ingerir_corte(destino, fecha, _cuentas_de(fecha))
    historiar_todo()
    ingerir_pagos_de(destino, "pagos.csv", PAGOS)
    historiar_todo()
    interpretar_todo()

    # Promesas: vigente (4), incumplidas recientes (6, 7, 8), vigente con pago (9), cancelada (18),
    # anulada (19), cumplida (29) y parcial (31).
    promesa(4, "2026-09-10T10:00", "2026-09-25")
    promesa(6, "2026-08-25T10:00", "2026-09-05")
    promesa(7, "2026-08-28T10:00", "2026-09-08")
    promesa(8, "2026-08-26T10:00", "2026-09-03")
    promesa(9, "2026-09-14T10:00", "2026-09-30", nivel_contacto=NivelContacto.CONTACTO_TITULAR)
    _, cancelada = promesa(18, "2026-09-10T10:00", "2026-09-25")
    cancelar_promesa(cancelada.recurso_id, "2026-09-12T09:00:00-06:00")
    anulada, _ = promesa(19, "2026-09-10T10:00", "2026-09-25")
    anular_gestion(anulada.recurso_id, "2026-09-11T09:00:00-06:00")
    promesa(29, "2026-08-15T10:00", "2026-08-22")
    promesa(31, "2026-08-10T10:00", "2026-08-20")
    # Saturacion sin contacto: 5, 8 y 22 (tres o mas en 7 dias, sin el titular en 30); la 24 tiene
    # dos, y una tercera llega tarde.
    for numero, dias in ((5, (10, 11, 12, 13)), (8, (10, 11, 12, 13)), (22, (10, 11, 12))):
        for dia in dias:
            sin_contacto(numero, f"2026-09-{dia:02d}T09:00")
    sin_contacto(24, "2026-09-10T09:00")
    sin_contacto(24, "2026-09-11T09:00")
    # Atribucion: una sola candidata con contacto (14) y dos (15).
    gestion(14, "2026-09-08T10:00", resultado=ResultadoGestion.CONTACTO)
    gestion(
        15,
        "2026-09-07T10:00",
        nivel_contacto=NivelContacto.CONTACTO_TERCERO,
        resultado=ResultadoGestion.CONTACTO,
    )
    gestion(15, "2026-09-09T10:00", resultado=ResultadoGestion.CONTACTO)
    # Mensajes de una via, sin denominador para la tasa de contacto (26).
    for dia in (5, 12):
        gestion(
            26,
            f"2026-09-{dia:02d}T10:00",
            canal=Canal.DIGITAL,
            medio=Medio.SMS,
            nivel_contacto=NivelContacto.NO_APLICA,
            resultado=ResultadoGestion.SIN_RESPUESTA,
        )
    # Convenios: vigente (27) y cancelado (28). La 30 no es del universo.
    convenio(27, "2026-09-05T10:00", "2026-09-01", "2026-12-31")
    cancelado = convenio(28, "2026-09-05T10:00", "2026-09-01", "2026-12-31")
    registro.cancelar_convenio(
        cancelado.recurso_id,
        llave(),
        registro.DatosCierre(instante("2026-09-08T09:00:00-06:00"), "Ya no aplica."),
    )
    gestion(SALIDA, "2026-09-14T10:00")
    for periodo in (date(2026, 6, 1), date(2026, 8, 1), date(2026, 9, 1)):
        atribuir_mes(periodo)

    k1 = reloj()
    # Lo que llega despues de K1: una gestion del 12 (tardia), una promesa del 14 (tardia), una
    # gestion del 20 (posterior a as_of) y el corte viejo del 19 de agosto.
    tardia = sin_contacto(24, "2026-09-12T09:00")
    promesa(21, "2026-09-14T10:00", "2026-09-30")
    futura = sin_contacto(25, "2026-09-20T09:00")
    ingerir_corte(destino, C0, _cuentas_de(C0))
    historiar_todo()
    k2 = reloj()

    d1 = decidir_v2(known_at=k1)
    foto_d1 = foto(d1.id)
    d2 = decidir_v2(known_at=k2)
    return Golden(k1, k2, d1, d2, foto_d1, tardia.recurso_id, futura.recurso_id)


def numero_de(cliente_unico: str) -> int:
    assert cliente_unico == cliente(int(cliente_unico[2:]))
    return int(cliente_unico[2:])


def pequeno(destino) -> None:
    """Tres cuentas en dos cortes (el 9 y el 16 de septiembre) y sus pagos de septiembre
    interpretados: lo minimo para decidir el corte del 16 con historia y con pagos."""
    cuentas = [Cuenta(1), Cuenta(2, dias=45), Cuenta(3, dias=0)]
    for fecha in (C3, C4):
        ingerir_corte(destino, fecha, cuentas)
    historiar_todo()
    ingerir_pagos_de(
        destino,
        "pagos.csv",
        [pago(1, "2026-09-14 10:00:00", "300.00"), pago(2, "2026-09-16 09:00:00", "50.00")],
    )
    historiar_todo()
    interpretar_todo()
