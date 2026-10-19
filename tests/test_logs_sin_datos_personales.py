"""Ningun log lleva un dato personal, un token ni un secreto.

Se recorre lo que mas datos personales toca: una cartera oficial (cartera/v2, con nombres,
domicilios, telefonos, avales, claves SPEI y coordenadas sinteticas) ingerida y materializada, su
geografia, una gestion del lifecycle con su actor_ref y una observacion que trae un nombre y un
telefono, y peticiones con un CLIENTE_UNICO en la query, un token, una cookie y una clave. Todo lo
que se registra en el camino, en cualquier logger y a cualquier nivel (con SQLAlchemy en
advertencias, como en produccion), se formatea como en produccion y se busca cada valor: no aparece
ninguno.
"""

from __future__ import annotations

import logging
from datetime import date

import pytest
from fastapi.testclient import TestClient
from geografia_escenarios import PUEBLA, CuentaGeo, geolocalizar, importar_referencia, tabla
from geografia_escenarios import ingerir_corte_geo as ingerir
from lifecycle_escenarios import llave
from sqlmodel import select

from motor_cartera.db.modelos import CuentaCanonica
from motor_cartera.db.sesion import sesion
from motor_cartera.observabilidad.logs import RUIDOSOS, FormatoJson

CORTE = date(2026, 9, 30)
CUENTAS = [
    CuentaGeo(1, PUEBLA, "19.0498765", "-98.2001234"),
    CuentaGeo(2, PUEBLA, "19.0512345", "-98.1987654"),
    CuentaGeo(3, PUEBLA, None, None),
]
COLUMNAS_PERSONALES = (
    "CLIENTE_UNICO",
    "NOMBRE_CTE",
    "DIRECCION_CTE",
    "COLONIA_CTE",
    "REFERENCIAS_DOMICILIO",
    "CALLE_EMPLEO",
    "NOMBRE_AVAL",
    "TEL_AVAL",
    "CALLE_AVAL",
    "TELEFONO1",
    "TELEFONO2",
    "TELEFONO3",
    "TELEFONO4",
    "CLAVE_SPEI",
    "LATITUD",
    "LONGITUD",
)
SECRETO_EN_LA_CABECERA = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJwZXJzb25hIn0.firmaDeLaPrueba"
OBSERVACION = "Contesto Rosaura Villanueva Quintero; pidio que se le marque el lunes."
ACTOR = "AGT-PRIVADO-4471"


def _personales() -> set[str]:
    """Cada valor personal que la cartera trae, tal como viene en el archivo."""
    datos = tabla(CUENTAS, CORTE)
    valores = set()
    for columna in COLUMNAS_PERSONALES:
        if columna in datos.columns:
            valores |= {str(v) for v in datos[columna] if v not in (None, "") and len(str(v)) >= 5}
    return valores


@pytest.mark.usefixtures("bd")
def test_ningun_log_lleva_un_dato_personal_un_token_ni_un_secreto(
    app, clave_api, trabajar, tmp_path, caplog
):
    caplog.set_level(logging.DEBUG)
    # Como en produccion (observabilidad.logs.RUIDOSOS): el SQL de SQLAlchemy y la URL de cada
    # peticion del cliente HTTP de las pruebas (el que hace de navegador) no llegan a la bitacora.
    for nombre in (*RUIDOSOS, "sqlalchemy.engine", "sqlalchemy.pool"):
        logging.getLogger(nombre).setLevel(logging.WARNING)

    version = importar_referencia(tmp_path)
    ingerir(tmp_path, CORTE, CUENTAS)
    geolocalizar(CORTE, version)
    trabajar()
    with sesion() as s:
        cuenta_id = s.exec(
            select(CuentaCanonica.cuenta_id).where(CuentaCanonica.cliente_unico == "CU0000000001")
        ).one()

    with TestClient(app, headers={"X-API-Key": clave_api}) as cliente:
        gestion = cliente.post(
            f"/cuentas/{cuenta_id}/gestiones",
            json={
                "ocurrido_en": "2026-09-20T10:15:00-06:00",
                "canal": "TELEFONICA",
                "medio": "LLAMADA",
                "nivel_contacto": "CONTACTO_TITULAR",
                "resultado": "PROMESA",
                "actor_ref": ACTOR,
                "observacion": OBSERVACION,
            },
            headers={"Idempotency-Key": llave()},
        )
        assert gestion.status_code == 201, gestion.text
        cliente.get("/cuentas", params={"cliente_unico": "CU0000000001"})
        cliente.get(
            f"/cuentas/{cuenta_id}/gestiones",
            headers={"Cookie": f"__Host-mc_sesion={SECRETO_EN_LA_CABECERA}"},
        )
        cliente.get("/app/v1/sesion", headers={"X-API-Key": "", "Authorization": "Bearer x"})
        cliente.get(
            "/app/v1/sesion",
            headers={"X-API-Key": "", "Authorization": f"Bearer {SECRETO_EN_LA_CABECERA}"},
        )

    formato = FormatoJson("api", "production")
    lineas = "\n".join(formato.format(r) for r in caplog.records)
    assert caplog.records, "el recorrido no dejo ningun log"
    prohibidos = _personales() | {
        "CU0000000001",
        "CU0000000002",
        ACTOR,
        "Rosaura",
        "Villanueva",
        SECRETO_EN_LA_CABECERA,
        "eyJzdWIiOiJwZXJzb25hIn0",
        clave_api,
    }
    for prohibido in sorted(prohibidos):
        assert prohibido not in lineas, f"un log lleva {prohibido!r}"
    for palabra in ("Authorization:", "Cookie:", "X-API-Key:"):
        assert palabra.lower() not in lineas.lower(), palabra
