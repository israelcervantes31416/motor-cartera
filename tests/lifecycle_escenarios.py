"""Lo que comparten las pruebas del lifecycle: cuentas canonicas, llaves de idempotencia y
gestiones, promesas y convenios registrados por la API. Todo es sintetico."""

from __future__ import annotations

from uuid import uuid4

from historia_escenarios import cliente
from sqlalchemy import func
from sqlmodel import select

from motor_cartera.db.modelos import CuentaCanonica, EventoLifecycle
from motor_cartera.db.sesion import sesion
from motor_cartera.historia import identidad

DESPACHO, CARTERA = "DSP_001", "CARTERA_PRINCIPAL"

GESTION = {
    "ocurrido_en": "2026-09-20T10:15:00-06:00",
    "canal": "TELEFONICA",
    "medio": "LLAMADA",
    "nivel_contacto": "CONTACTO_TITULAR",
    "resultado": "PROMESA",
    "actor_ref": "AGT-0007",
}
"""Una llamada en que el titular promete pagar: lo minimo de una gestion valida."""


def llave() -> str:
    """Una Idempotency-Key nueva."""
    return f"prueba-{uuid4().hex}"


def cuenta_canonica(numero: int = 1, cartera: str = CARTERA) -> CuentaCanonica:
    """Una cuenta canonica sin cortes, con el cuenta_id determinista de su llave: basta para
    registrar gestiones."""
    with sesion() as s:
        cuenta = CuentaCanonica(
            cuenta_id=identidad.cuenta_id(DESPACHO, cartera, cliente(numero)),
            despacho_id=DESPACHO,
            cartera_id=cartera,
            cliente_unico=cliente(numero),
        )
        s.add(cuenta)
        s.commit()
        s.refresh(cuenta)
        return cuenta


def registrar(cliente_api, cuenta_id, *, con_llave: str | None = None, **campos):
    """POST de una gestion de la cuenta, con su llave (una nueva si no se da)."""
    return cliente_api.post(
        f"/cuentas/{cuenta_id}/gestiones",
        json={**GESTION, **campos},
        headers={"Idempotency-Key": con_llave or llave()},
    )


def prometer(cliente_api, gestion_id, *, con_llave: str | None = None, **campos):
    cuerpo = {"monto_prometido": "1000.00", "fecha_limite": "2026-09-30", **campos}
    return cliente_api.post(
        f"/gestiones/{gestion_id}/promesas",
        json=cuerpo,
        headers={"Idempotency-Key": con_llave or llave()},
    )


def cerrar(cliente_api, ruta: str, *, con_llave: str | None = None, **campos):
    """POST de una anulacion o una cancelacion."""
    cuerpo = {
        "ocurrido_en": "2026-09-21T09:00:00-06:00",
        "motivo": "Registrada por error.",
        **campos,
    }
    return cliente_api.post(ruta, json=cuerpo, headers={"Idempotency-Key": con_llave or llave()})


def eventos() -> int:
    with sesion() as s:
        return s.exec(select(func.count()).select_from(EventoLifecycle)).one()
