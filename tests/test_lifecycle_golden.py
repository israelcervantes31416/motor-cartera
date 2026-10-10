"""El escenario golden del lifecycle: los dieciseis casos de la mision, de punta a punta por la API,
con cortes, pagos interpretados de verdad, la evaluacion de las promesas a una fecha explicita y la
atribucion. Cada caso esta numerado como en la mision.

     1  gestion sin contacto                    9  visita con el titular
     2  contacto con el titular sin promesa    10  pago sin gestion candidata
     3  promesa y pago compatible              11  pago con una gestion candidata
     4  promesa parcial                        12  pago con dos gestiones candidatas
     5  promesa incumplida                     13  gestion tardia que cambia una atribucion
     6  promesa cancelada                      14  gestion anulada
     7  convenio                               15  la misma Idempotency-Key, la misma peticion
     8  visita sin contacto                    16  la misma Idempotency-Key, otra peticion
"""

from __future__ import annotations

from datetime import date

import pytest
from historia_escenarios import Cuenta, cuenta, ingerir_corte, ingerir_pagos_de, pago
from lifecycle_escenarios import cerrar, eventos, llave, prometer, registrar

pytestmark = pytest.mark.usefixtures("bd")

SIN_CONTACTO = {"nivel_contacto": "SIN_CONTACTO", "resultado": "SIN_RESPUESTA"}
CONTACTO = {"nivel_contacto": "CONTACTO_TITULAR", "resultado": "CONTACTO"}
CAMPO = {"canal": "CAMPO", "medio": None, "resultado": "VISITA_REALIZADA"}


def _gestion(cliente, numero: int, ocurrido_en: str, **campos) -> str:
    respuesta = registrar(
        cliente, cuenta(numero).cuenta_id, ocurrido_en=f"{ocurrido_en}-06:00", **campos
    )
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()["gestion_id"]


def _promesa(cliente, gestion_id: str) -> str:
    respuesta = prometer(cliente, gestion_id, monto_prometido="1000.00", fecha_limite="2026-09-15")
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()["promesa_id"]


def _pedir(cliente, trabajar, ruta: str, cuerpo: dict) -> dict:
    pedida = cliente.post(ruta, json=cuerpo)
    assert pedida.status_code == 201, pedida.text
    trabajar()
    terminada = cliente.get(pedida.headers["location"]).json()
    assert terminada["estado"] == "EXITOSA", terminada
    return terminada


def _movimiento(cliente, numero: int) -> str:
    pagos = cliente.get(f"/cuentas/{cuenta(numero).cuenta_id}/atribuciones").json()["elementos"]
    return pagos[0]["movimiento"]["movimiento_id"]


def test_el_escenario_golden_del_lifecycle(tmp_path, cliente, trabajar):
    ingerir_corte(tmp_path, date(2026, 9, 1), [Cuenta(n) for n in range(1, 13)])
    trabajar()  # la historia del corte: las cuentas canonicas

    # Lo que la cobranza hizo, por la API.
    g = {
        1: _gestion(cliente, 1, "2026-09-03T10:00:00", **SIN_CONTACTO),
        2: _gestion(cliente, 2, "2026-09-03T11:00:00", **CONTACTO),
        3: _gestion(cliente, 3, "2026-09-05T10:00:00"),
        4: _gestion(cliente, 4, "2026-09-05T10:00:00"),
        "4b": _gestion(
            cliente,
            4,
            "2026-09-08T10:00:00",
            nivel_contacto="CONTACTO_TERCERO",
            resultado="CONTACTO",
        ),
        5: _gestion(cliente, 5, "2026-09-05T10:00:00"),
        6: _gestion(cliente, 6, "2026-09-05T10:00:00"),
        7: _gestion(cliente, 7, "2026-09-06T10:00:00", resultado="CONVENIO"),
        8: _gestion(
            cliente,
            8,
            "2026-09-07T10:00:00",
            nivel_contacto="SIN_CONTACTO",
            visita={"resultado": "NO_LOCALIZADO"},
            **CAMPO,
        ),
        9: _gestion(
            cliente,
            9,
            "2026-09-07T12:00:00",
            nivel_contacto="CONTACTO_TITULAR",
            visita={"resultado": "CONTACTO_TITULAR", "observacion": "Atiende en su domicilio."},
            **CAMPO,
        ),
        11: _gestion(cliente, 11, "2026-09-08T10:00:00", **CONTACTO),
    }
    p = {n: _promesa(cliente, g[n]) for n in (3, 4, 5, 6)}
    cancelada = cerrar(
        cliente, f"/promesas/{p[6]}/cancelaciones", ocurrido_en="2026-09-08T09:00:00-06:00"
    )
    convenio = cliente.post(
        f"/gestiones/{g[7]}/convenios",
        json={
            "monto_total_acordado": "3000.00",
            "fecha_inicio": "2026-09-07",
            "fecha_fin": "2026-11-30",
            "cuotas": [
                {"fecha_vencimiento": "2026-09-30", "monto": "1500.00"},
                {"fecha_vencimiento": "2026-10-30", "monto": "1500.00"},
            ],
        },
        headers={"Idempotency-Key": llave()},
    )
    anulada = cerrar(
        cliente, f"/gestiones/{g[11]}/anulaciones", ocurrido_en="2026-09-11T09:00:00-06:00"
    )
    assert (cancelada.status_code, convenio.status_code, anulada.status_code) == (201, 201, 201)

    # Lo que se pago: los pagos se interpretan con el motor de pagos.
    ingerir_pagos_de(
        tmp_path,
        "pagos.csv",
        [
            pago(3, "2026-09-10 10:00:00", "1000.00"),
            pago(4, "2026-09-12 10:00:00", "500.00"),
            pago(10, "2026-09-20 10:00:00", "800.00"),
            pago(11, "2026-09-10 10:00:00", "300.00"),
        ],
    )
    trabajar()

    # La evaluacion de las promesas a una fecha explicita: el dia 16.
    evaluacion = _pedir(cliente, trabajar, "/evaluaciones-promesas", {"as_of": "2026-09-16"})
    estados = {
        n: cliente.get(f"/promesas/{p[n]}").json()["ultima_evaluacion"]["estado"]
        for n in (3, 4, 5, 6)
    }
    assert evaluacion["conteos"]["promesas_evaluadas"] == 4
    assert estados[3] == "CUMPLIDA"  # 3. promesa seguida de un pago compatible
    assert estados[4] == "PARCIAL"  # 4. promesa parcial
    assert estados[5] == "INCUMPLIDA"  # 5. promesa incumplida
    assert estados[6] == "CANCELADA"  # 6. promesa cancelada
    assert cliente.get(f"/promesas/{p[6]}").json()["estado_operativo"] == "CANCELADA"

    # La atribucion de septiembre.
    primera = _pedir(cliente, trabajar, "/atribuciones", {"periodo": "2026-09"})
    resultados = {
        r["cuenta_id"]: r
        for r in cliente.get(f"/atribuciones/{primera['atribucion_run_id']}/resultados").json()[
            "elementos"
        ]
    }

    def de(numero: int) -> dict:
        return resultados[str(cuenta(numero).cuenta_id)]

    # 10. pago sin gestion candidata
    assert (de(10)["clasificacion"], de(10)["candidatas"]) == ("SIN_GESTION_CANDIDATA", [])
    # 11. pago con una gestion candidata: la de su promesa
    assert (de(3)["clasificacion"], de(3)["gestion_id"]) == ("ASOCIACION_UNICA", g[3])
    # 12. pago con dos gestiones candidatas: no se elige ninguna
    assert (de(4)["clasificacion"], de(4)["gestion_id"]) == ("AMBIGUA", None)
    assert {c["gestion_id"] for c in de(4)["candidatas"]} == {g[4], g["4b"]}
    # 14. gestion anulada: deja de ser candidata y sigue visible, anulada
    assert de(11)["clasificacion"] == "SIN_GESTION_CANDIDATA"
    assert de(11)["motivos"][0]["gestiones_anuladas"] == 1
    gestion_anulada = cliente.get(f"/gestiones/{g[11]}").json()
    assert gestion_anulada["estado"] == "ANULADA" and gestion_anulada["anulacion"] is not None

    # 13. una gestion tardia (ocurrio el 18 y se registra hoy) cambia la atribucion siguiente,
    # y la primera se conserva.
    tardia = _gestion(cliente, 10, "2026-09-18T10:00:00", **CONTACTO)
    segunda = _pedir(cliente, trabajar, "/atribuciones", {"periodo": "2026-09"})
    historia = cliente.get(f"/movimientos/{_movimiento(cliente, 10)}/atribuciones").json()
    assert [
        (a["atribucion_run_id"], a["vigente"], a["clasificacion"]) for a in historia["elementos"]
    ] == [
        (segunda["atribucion_run_id"], True, "ASOCIACION_UNICA"),
        (primera["atribucion_run_id"], False, "SIN_GESTION_CANDIDATA"),
    ]
    assert historia["elementos"][0]["gestion_id"] == tardia
    tiempos = cliente.get(f"/gestiones/{tardia}").json()
    assert tiempos["ocurrido_en"] < tiempos["registrado_en"]

    # 1, 2, 8 y 9: las gestiones sin promesa, cada una con su contacto y su visita.
    vistas = {n: cliente.get(f"/gestiones/{g[n]}").json() for n in (1, 2, 8, 9)}
    assert (vistas[1]["nivel_contacto"], vistas[1]["resultado"]) == (
        "SIN_CONTACTO",
        "SIN_RESPUESTA",
    )  # 1. gestion sin contacto
    assert (vistas[2]["nivel_contacto"], vistas[2]["promesa_id"]) == (
        "CONTACTO_TITULAR",
        None,
    )  # 2. contacto con el titular sin promesa
    assert (vistas[8]["canal"], vistas[8]["visita"]["resultado"]) == (
        "CAMPO",
        "NO_LOCALIZADO",
    )  # 8. visita sin contacto
    assert (vistas[9]["nivel_contacto"], vistas[9]["visita"]["resultado"]) == (
        "CONTACTO_TITULAR",
        "CONTACTO_TITULAR",
    )  # 9. visita con el titular

    # 7. convenio: sus cuotas declaradas, sin ledger.
    convenio_visto = cliente.get(f"/convenios/{convenio.json()['convenio_id']}").json()
    assert convenio_visto["estado_operativo"] == "VIGENTE"
    assert [c["monto"] for c in convenio_visto["cuotas"]] == ["1500.00", "1500.00"]
    assert convenio_visto["evaluacion_de_cuotas"] == "NO_EVALUABLE"

    # 15 y 16: la misma llave, primero con la misma peticion y despues con otra.
    clave = llave()
    antes = eventos()
    nueva = registrar(cliente, cuenta(12).cuenta_id, con_llave=clave, **CONTACTO)
    repetida = registrar(cliente, cuenta(12).cuenta_id, con_llave=clave, **CONTACTO)
    otra = registrar(cliente, cuenta(12).cuenta_id, con_llave=clave, **SIN_CONTACTO)
    assert (nueva.status_code, repetida.status_code) == (201, 200)  # 15.
    assert repetida.headers["Idempotent-Replayed"] == "true"
    assert repetida.json()["gestion_id"] == nueva.json()["gestion_id"]
    assert (otra.status_code, otra.json()["codigo"]) == (409, "IDEMPOTENCY_KEY_REUTILIZADA")  # 16.
    assert eventos() == antes + 1

    # Cuenta 360 junta todo sin volverse un JSON gigante.
    resumen = cliente.get(f"/cuentas/{cuenta(3).cuenta_id}").json()["lifecycle_resumen"]
    assert (resumen["gestiones"], resumen["promesas"], resumen["promesas_vigentes"]) == (1, 1, 1)
    assert resumen["ultima_atribucion"]["clasificacion"] == "ASOCIACION_UNICA"
    linea = cliente.get(
        f"/cuentas/{cuenta(3).cuenta_id}/lifecycle", params={"orden": "asc"}
    ).json()["elementos"]
    # Sin estatus de promesa en el corte no hay OBSERVACION_EN_CORTE: nada se fabrica de un
    # snapshot.
    assert [(e["dominio"], e["tipo"]) for e in linea] == [
        ("OPERACIONAL", "GESTION_REGISTRADA"),
        ("OPERACIONAL", "PROMESA_CREADA"),
        ("ECONOMICO", "PAGO"),
    ]
