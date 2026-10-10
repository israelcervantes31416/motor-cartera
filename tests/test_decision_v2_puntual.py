"""Point-in-time: lo que llega despues no cambia una decision que ya se tomo, y lo que ocurre
despues de as_of no entra en ninguna. Cada prueba es un caso de la mision: el evento futuro, el pago
futuro, el conocimiento futuro, el corte tardio, el evento tardio que una frontera posterior si ve,
y la reconstruccion completa de la capa v0.10."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from decision_v2_escenarios import (
    AS_OF,
    C1,
    C2,
    C3,
    C4,
    abrir_v2,
    decidir_v2,
    ejecucion,
    features_de,
    foto,
    pequeno,
    reloj,
    resultados_de,
)
from historia_escenarios import Cuenta, cuenta, ingerir_corte, ingerir_pagos_de, pago
from lifecycle_escenarios import gestion_de
from motor_pagos_escenarios import historiar_todo, interpretar_todo
from sqlalchemy import text
from sqlmodel import select

from motor_cartera.db.modelos import EventoLifecycle, GestionCobranza
from motor_cartera.db.sesion import sesion
from motor_cartera.decision_v2 import ejecuciones as motor
from motor_cartera.lifecycle.reglas import NivelContacto, ResultadoGestion

pytestmark = pytest.mark.usefixtures("bd")


def _sin_contacto(numero: int, ocurrido_en: str):
    return gestion_de(
        cuenta(numero).cuenta_id,
        ocurrido_en,
        nivel_contacto=NivelContacto.SIN_CONTACTO,
        resultado=ResultadoGestion.SIN_RESPUESTA,
    )


def _borrar_la_capa_v010() -> None:
    """Quita exactamente lo que v0.10 agrego, y nada mas: resultados, features, ejecuciones y sus
    trabajos DECISION_V2. Lo publicado no se borra desde la aplicacion (un trigger lo impide): la
    prueba lo hace como un administrador, con los triggers apagados solo en su transaccion."""
    with sesion() as s:
        for tabla in ("resultado_decision_v2", "feature_decision_cuenta"):
            s.execute(text(f"ALTER TABLE {tabla} DISABLE TRIGGER tr_decision_v2_inmutable"))
        s.execute(text("DELETE FROM resultado_decision_v2"))
        s.execute(text("DELETE FROM feature_decision_cuenta"))
        s.execute(text("DELETE FROM trabajo_orquestacion WHERE tipo = 'DECISION_V2'"))
        s.execute(text("DELETE FROM ejecucion_decision_v2"))
        for tabla in ("resultado_decision_v2", "feature_decision_cuenta"):
            s.execute(text(f"ALTER TABLE {tabla} ENABLE TRIGGER tr_decision_v2_inmutable"))
        s.commit()


def test_un_evento_ocurrido_despues_de_as_of_no_cambia_ninguna_decision(tmp_path):
    pequeno(tmp_path)
    k = reloj()
    original = decidir_v2(known_at=k)
    antes = foto(original.id)

    # Una gestion ocurrida el 17, despues de as_of, se registra despues de decidir.
    futura = _sin_contacto(1, "2026-09-17T10:00:00-06:00")
    # Con el mismo known_at, y tambien con uno que ya la conoce: ninguna la ve.
    _borrar_la_capa_v010()
    reconstruida = decidir_v2(known_at=k)
    posterior = decidir_v2(known_at=reloj())

    assert foto(reconstruida.id) == foto(posterior.id) == antes
    with sesion() as s:
        evento = s.exec(
            select(EventoLifecycle)
            .join(GestionCobranza, GestionCobranza.evento_lifecycle_id == EventoLifecycle.id)
            .where(GestionCobranza.gestion_id == futura.recurso_id)
        ).one()
    # Lo unico que la deja fuera de la posterior es su momento de negocio: se registro antes de
    # su known_at.
    assert evento.registrado_en <= posterior.known_at
    assert evento.ocurrido_en >= datetime.fromisoformat("2026-09-17T00:00:00-06:00")


def test_un_pago_recibido_despues_de_as_of_no_cambia_features_ni_decision(tmp_path):
    pequeno(tmp_path)
    original = decidir_v2(known_at=reloj())
    antes = foto(original.id)

    # Un pago del 18 llega, se materializa y se interpreta: se conoce, pero es posterior a as_of.
    ingerir_pagos_de(tmp_path, "futuro.csv", [pago(3, "2026-09-18 10:00:00", "900.00")])
    historiar_todo()
    interpretar_todo()
    posterior = decidir_v2(known_at=reloj())

    assert foto(posterior.id) == antes
    # Tampoco certifica nada: el horizonte de los pagos no pasa del final de as_of.
    assert posterior.disponibilidad["horizonte_pagos"] == "2026-09-16T09:00:00"


def test_un_evento_conocido_despues_de_known_at_queda_fuera(tmp_path):
    pequeno(tmp_path)
    # La 3 no tiene pagos: su base es BAJA, y con tres gestiones sin contacto en 7 dias sube.
    for dia in (12, 13):
        _sin_contacto(3, f"2026-09-{dia:02d}T10:00:00-06:00")
    k1 = reloj()
    # Ocurrio el 14, antes de as_of, pero se registro despues de k1: a k1 no existia.
    tardia = _sin_contacto(3, "2026-09-14T10:00:00-06:00")
    k2 = reloj()

    d1 = decidir_v2(known_at=k1)
    d2 = decidir_v2(known_at=k2)

    f1, f2 = features_de(d1.id)[3], features_de(d2.id)[3]
    assert (f1["gestiones_sin_contacto_7d"], f2["gestiones_sin_contacto_7d"]) == (2, 3)
    r1, r2 = resultados_de(d1.id)[3], resultados_de(d2.id)[3]
    assert (r1.prioridad_final, r2.prioridad_final) == ("BAJA", "MEDIA")
    # Lo unico que la deja fuera de d1 es su registrado_en: si se quitara ese filtro, d1 la veria.
    with sesion() as s:
        evento = s.exec(
            select(EventoLifecycle)
            .join(GestionCobranza, GestionCobranza.evento_lifecycle_id == EventoLifecycle.id)
            .where(GestionCobranza.gestion_id == tardia.recurso_id)
        ).one()
    assert evento.ocurrido_en < d1.known_at < evento.registrado_en <= d2.known_at


def test_un_evento_tardio_no_cambia_la_decision_que_ya_se_tomo(tmp_path):
    """El caso en tiempo real: se decide a K1 antes de que llegue la gestion tardia; despues llega,
    y una decision a K2 la ve. La de K1 sigue intacta, y reconstruirla con su known_at da lo
    mismo."""
    pequeno(tmp_path)
    for dia in (12, 13):
        _sin_contacto(3, f"2026-09-{dia:02d}T10:00:00-06:00")
    d1 = decidir_v2(known_at=reloj())
    antes = foto(d1.id)

    _sin_contacto(3, "2026-09-14T10:00:00-06:00")
    d2 = decidir_v2(known_at=reloj())

    assert foto(d1.id) == antes
    assert resultados_de(d1.id)[3].prioridad_final == "BAJA"
    assert resultados_de(d2.id)[3].prioridad_final == "MEDIA"
    _borrar_la_capa_v010()
    assert foto(decidir_v2(known_at=d1.known_at).id) == antes


def test_un_corte_viejo_materializado_despues_de_known_at_no_altera_la_decision(tmp_path):
    cuentas = [Cuenta(1), Cuenta(2)]
    for fecha in (C2, C3, C4):
        ingerir_corte(tmp_path, fecha, cuentas)
    historiar_todo()
    k = reloj()
    original = decidir_v2(known_at=k)
    antes = foto(original.id)

    # El corte del 26 de agosto llega tarde: es historia anterior, pero se conoce despues de k.
    ingerir_corte(tmp_path, C1, [Cuenta(1)])
    historiar_todo()
    _borrar_la_capa_v010()
    reconstruida = decidir_v2(known_at=k)
    posterior = decidir_v2(known_at=reloj())

    assert foto(reconstruida.id) == antes
    assert (antes[1][0]["cortes_observados"], antes[2][0]["cortes_observados"]) == (3, 3)
    tarde = features_de(posterior.id)
    assert (tarde[1]["cortes_observados"], tarde[2]["cortes_observados"]) == (4, 3)


def test_reconstruir_la_capa_v010_da_exactamente_lo_mismo(tmp_path):
    pequeno(tmp_path)
    for dia in (10, 11, 12):
        _sin_contacto(2, f"2026-09-{dia:02d}T10:00:00-06:00")
    sin_known_at = ejecucion(abrir_v2())
    motor.ejecutar_decision_v2(sin_known_at.id)
    primera = ejecucion(sin_known_at.id)
    antes = foto(primera.id)
    firma, known_at = primera.firma_inputs, primera.known_at

    _borrar_la_capa_v010()
    # Se vuelve a pedir con su known_at (el que se fijo al abrir) y el mismo corte y as_of.
    reconstruida = decidir_v2(known_at=known_at)

    assert reconstruida.firma_inputs == firma
    assert foto(reconstruida.id) == antes
    assert (reconstruida.distribucion, reconstruida.disponibilidad) == (
        primera.distribucion,
        primera.disponibilidad,
    )
    assert reconstruida.as_of == AS_OF == date(2026, 9, 16)
