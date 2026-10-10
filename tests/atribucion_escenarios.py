"""Lo que comparten las pruebas de la atribucion: un mes de pagos interpretados de verdad, las
gestiones que los anteceden, y como leer lo que concluyo una ejecucion. Todo es sintetico."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from historia_escenarios import Cuenta, cliente, cuenta, ingerir_corte, ingerir_pagos_de, pago
from lifecycle_escenarios import anular_gestion, gestion_de
from motor_pagos_escenarios import historiar_todo, interpretar_todo
from sqlalchemy import text

from motor_cartera.atribucion.ejecuciones import abrir, atribuir
from motor_cartera.db.modelos import EjecucionAtribucion
from motor_cartera.db.sesion import sesion
from motor_cartera.lifecycle.reglas import NivelContacto, ResultadoGestion
from motor_cartera.motor_pagos.ejecuciones import Ventana

ZONA = "America/Mexico_City"
DESPACHO, CARTERA = "DSP_001", "CARTERA_PRINCIPAL"
SEPTIEMBRE = Ventana.del_periodo(DESPACHO, CARTERA, date(2026, 9, 1))

PAGOS = [
    pago(1, "2026-09-10 10:00:00", "1000.00"),
    pago(2, "2026-09-10 10:00:00", "500.00"),
    pago(3, "2026-09-10 10:00:00", "300.00"),
    pago(4, "2026-09-10 10:00:00", "200.00"),
    pago(5, "2026-09-10 10:00:00", "100.00"),
    pago(6, "2026-09-10 10:00:00", "400.00"),
    pago(6, "2026-09-12 09:00:00", "-400.00", **{"Concepto_Cálculo": "AJUSTE"}),
    pago(7, "2026-09-10 10:00:00", "70.00"),
    pago(9, "2026-09-10 10:00:00", "50.00"),
]
"""Un pago por cliente el 10 de septiembre a las 10:00, hora de la fuente. El de la 6 lo anula un
reverso el 12, y el cliente 9 no esta en ningun corte: su pago no tiene cuenta."""


def escenario(tmp_path, *, pagos=None) -> dict[str, UUID]:
    """Siete cuentas en el corte del 1 de septiembre, sus pagos interpretados y sus gestiones:

    - la 1, una llamada con el titular el 8: una sola candidata;
    - la 2, un contacto con un tercero el 7 y una llamada con el titular el 9: dos candidatas;
    - la 3, una llamada el 8 que se anula el 9;
    - la 4, una llamada sin respuesta el 8;
    - la 5, nada;
    - la 6, una llamada el 9; su pago lo anula un reverso;
    - la 7, una llamada el 11, despues de su pago, y otra el 10 de agosto, mas de 30 dias antes.

    Devuelve los gestion_id por nombre."""
    ingerir_corte(tmp_path, date(2026, 9, 1), [Cuenta(n) for n in range(1, 8)])
    historiar_todo()
    ingerir_pagos_de(tmp_path, "pagos.csv", pagos or PAGOS)
    historiar_todo()
    interpretar_todo()
    gestiones = {
        "1": gestion_de(cuenta(1).cuenta_id, "2026-09-08T10:00:00-06:00"),
        "2_tercero": gestion_de(
            cuenta(2).cuenta_id,
            "2026-09-07T10:00:00-06:00",
            nivel_contacto=NivelContacto.CONTACTO_TERCERO,
            resultado=ResultadoGestion.CONTACTO,
        ),
        "2_titular": gestion_de(cuenta(2).cuenta_id, "2026-09-09T10:00:00-06:00"),
        "3": gestion_de(cuenta(3).cuenta_id, "2026-09-08T10:00:00-06:00"),
        "4": gestion_de(
            cuenta(4).cuenta_id,
            "2026-09-08T10:00:00-06:00",
            nivel_contacto=NivelContacto.SIN_CONTACTO,
            resultado=ResultadoGestion.SIN_RESPUESTA,
        ),
        "6": gestion_de(cuenta(6).cuenta_id, "2026-09-09T10:00:00-06:00"),
        "7_despues": gestion_de(cuenta(7).cuenta_id, "2026-09-11T10:00:00-06:00"),
        "7_antes": gestion_de(cuenta(7).cuenta_id, "2026-08-10T10:00:00-06:00"),
    }
    anular_gestion(gestiones["3"].recurso_id, "2026-09-09T09:00:00-06:00")
    return {nombre: registro.recurso_id for nombre, registro in gestiones.items()}


def abrir_ventana(ventana: Ventana = SEPTIEMBRE, *, ventana_dias: int = 30) -> int:
    with sesion() as s:
        ejecucion, nueva = abrir(
            s, ventana, ventana_dias=ventana_dias, zona=ZONA, max_intentos=5, reusar=False
        )
        s.commit()
        assert nueva
        return ejecucion.id


def atribuir_ventana(ventana: Ventana = SEPTIEMBRE, *, ventana_dias: int = 30):
    """Abre la atribucion de la ventana, la ejecuta y la devuelve terminada."""
    ejecucion_id = abrir_ventana(ventana, ventana_dias=ventana_dias)
    atribuir(ejecucion_id)
    with sesion() as s:
        return s.get_one(EjecucionAtribucion, ejecucion_id)


@dataclass(frozen=True)
class Concluido:
    """Lo que una ejecucion concluyo de un pago, leido de la base."""

    clasificacion: str
    gestion_id: UUID | None
    candidatas: dict[UUID, int]
    """Cada candidata con su antelacion en segundos."""
    anulado_por_reverso: bool
    codigos: list[str]


def concluido(ejecucion: EjecucionAtribucion) -> dict[int, Concluido]:
    """Lo que la ejecucion concluyo del pago de cada cliente, por su numero."""
    numeros = {cliente(n): n for n in range(1, 50)}
    with sesion() as s:
        filas = s.execute(
            text(
                "SELECT m.cliente_unico, a.clasificacion, g.gestion_id, a.anulado_por_reverso, "
                "a.motivos, (SELECT coalesce(jsonb_object_agg(cg.gestion_id::text, "
                "c.antelacion_segundos), '{}'::jsonb) FROM candidato_atribucion c "
                "JOIN gestion_cobranza cg ON cg.id = c.gestion_cobranza_id "
                "WHERE c.ejecucion_atribucion_id = a.ejecucion_atribucion_id "
                "AND c.movimiento_economico_canonico_id = a.movimiento_economico_canonico_id) "
                "FROM atribucion_movimiento a "
                "JOIN movimiento_economico_canonico m ON m.id = a.movimiento_economico_canonico_id "
                "LEFT JOIN gestion_cobranza g ON g.id = a.gestion_cobranza_id "
                "WHERE a.ejecucion_atribucion_id = :ejecucion"
            ),
            {"ejecucion": ejecucion.id},
        ).all()
    return {
        numeros[cliente_unico]: Concluido(
            clasificacion,
            gestion_id,
            {UUID(g): segundos for g, segundos in candidatas.items()},
            anulado,
            [m["codigo"] for m in motivos],
        )
        for cliente_unico, clasificacion, gestion_id, anulado, motivos, candidatas in filas
    }
