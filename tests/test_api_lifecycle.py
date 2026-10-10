"""El lifecycle de cobranza por la API: gestiones, anulaciones, promesas, convenios, la linea de
tiempo y el resumen de la Cuenta 360, contra PostgreSQL. La idempotencia se prueba por su contrato
(201, 200 y 409) y por la base: una llave, un evento."""

from __future__ import annotations

import threading
from datetime import date, datetime
from uuid import UUID

import pytest
from historia_escenarios import Cuenta, ingerir_corte, ingerir_pagos_de, pago
from lifecycle_escenarios import (
    GESTION,
    cerrar,
    cuenta_canonica,
    eventos,
    llave,
    prometer,
    registrar,
)
from motor_pagos_escenarios import historiar_todo, interpretar_todo
from sqlalchemy import func
from sqlmodel import select

from motor_cartera.db.modelos import (
    ConvenioCobranza,
    CuentaCanonica,
    EventoLifecycle,
    GestionCobranza,
    PromesaPago,
    SnapshotCuenta,
)
from motor_cartera.db.sesion import sesion
from motor_cartera.lifecycle import registro
from motor_cartera.lifecycle.reglas import Canal, NivelContacto, ResultadoGestion

pytestmark = pytest.mark.usefixtures("bd")


def _cuantos(modelo) -> int:
    with sesion() as s:
        return s.exec(select(func.count()).select_from(modelo)).one()


# --- registrar una gestion ------------------------------------------------------------------------


def test_registrar_una_gestion_responde_201_con_su_location_y_la_base_pone_el_registro(cliente):
    cuenta = cuenta_canonica()

    respuesta = registrar(cliente, cuenta.cuenta_id, con_llave="llave-de-la-prueba-1")

    assert respuesta.status_code == 201, respuesta.text
    gestion = respuesta.json()
    assert respuesta.headers["location"] == f"/gestiones/{gestion['gestion_id']}"
    assert "idempotent-replayed" not in respuesta.headers
    assert (
        gestion["canal"],
        gestion["medio"],
        gestion["nivel_contacto"],
        gestion["resultado"],
    ) == (
        "TELEFONICA",
        "LLAMADA",
        "CONTACTO_TITULAR",
        "PROMESA",
    )
    assert (gestion["estado"], gestion["cuenta_id"]) == ("VIGENTE", str(cuenta.cuenta_id))
    assert gestion["ocurrido_en"].startswith("2026-09-20T16:15:00")
    # registrado_en lo pone el reloj de la base: no es ocurrido_en, ni nada que mande el cliente.
    assert gestion["registrado_en"] != gestion["ocurrido_en"]
    assert gestion["registrado_en"][:4] >= "2026"
    evento = gestion["evento"]
    assert evento["tipo_evento"] == "GESTION_REGISTRADA"
    assert evento["idempotency_key"] == "llave-de-la-prueba-1"
    assert (evento["origen_registro"], evento["version_evento"]) == ("API", "lifecycle/v1")
    assert evento["evento_relacionado_id"] is None
    assert gestion["anulacion"] is gestion["visita"] is gestion["promesa_id"] is None

    leida = cliente.get(respuesta.headers["location"])
    assert leida.status_code == 200
    assert leida.json() == gestion


def test_la_misma_llave_con_la_misma_peticion_responde_200_sin_registrar_otra(cliente):
    cuenta = cuenta_canonica()
    primera = registrar(cliente, cuenta.cuenta_id, con_llave="llave-repetida-0001")

    # El mismo instante con otra zona, y el mismo cuerpo: la misma peticion.
    otra_forma = registrar(
        cliente,
        cuenta.cuenta_id,
        con_llave="llave-repetida-0001",
        ocurrido_en="2026-09-20T16:15:00Z",
    )

    assert (primera.status_code, otra_forma.status_code) == (201, 200)
    assert otra_forma.headers["idempotent-replayed"] == "true"
    assert otra_forma.headers["location"] == primera.headers["location"]
    assert otra_forma.json() == primera.json()
    assert eventos() == 1 and _cuantos(GestionCobranza) == 1


@pytest.mark.parametrize(
    "cambio",
    [
        {"resultado": "CONTACTO"},
        {"observacion": "Otra nota."},
        {"ocurrido_en": "2026-09-20T10:16:00-06:00"},
    ],
    ids=["otro-resultado", "otra-observacion", "otro-instante"],
)
def test_la_misma_llave_con_otra_peticion_es_un_409_y_no_registra_nada(cliente, cambio):
    cuenta = cuenta_canonica()
    registrar(cliente, cuenta.cuenta_id, con_llave="llave-reusada-00001")

    respuesta = registrar(cliente, cuenta.cuenta_id, con_llave="llave-reusada-00001", **cambio)

    assert respuesta.status_code == 409
    assert respuesta.json()["codigo"] == "IDEMPOTENCY_KEY_REUTILIZADA"
    assert eventos() == 1


def test_la_misma_llave_sobre_otra_cuenta_de_la_cartera_es_otra_peticion(cliente):
    una, otra = cuenta_canonica(1), cuenta_canonica(2)
    registrar(cliente, una.cuenta_id, con_llave="llave-de-la-cartera")

    respuesta = registrar(cliente, otra.cuenta_id, con_llave="llave-de-la-cartera")

    assert respuesta.status_code == 409
    assert respuesta.json()["codigo"] == "IDEMPOTENCY_KEY_REUTILIZADA"
    # En otra cartera, la llave es de otro cliente: no choca.
    de_otra_cartera = cuenta_canonica(3, cartera="CARTERA_DOS")
    assert registrar(
        cliente, de_otra_cartera.cuenta_id, con_llave="llave-de-la-cartera"
    ).status_code == (201)


def test_dos_peticiones_a_la_vez_con_la_misma_llave_registran_un_solo_evento(monkeypatch):
    cuenta = cuenta_canonica()
    datos = registro.DatosGestion(
        ocurrido_en=datetime.fromisoformat(GESTION["ocurrido_en"]),
        canal=Canal.TELEFONICA,
        nivel_contacto=NivelContacto.SIN_CONTACTO,
        resultado=ResultadoGestion.SIN_RESPUESTA,
    )
    juntas = threading.Barrier(2, timeout=30)
    buscar = registro._previo
    salidas: list = []

    def despues_de_buscar(*args, **kwargs):
        # Las dos buscan la llave y no la encuentran; solo entonces siguen, y las dos insertan. La
        # que pierde la carrera choca con el indice unico de la llave, la vuelve a buscar y la
        # encuentra.
        previo = buscar(*args, **kwargs)
        if previo is None:
            try:
                juntas.wait()
            except threading.BrokenBarrierError:
                pass
        return previo

    def correr():
        try:
            salidas.append(
                registro.registrar_gestion(cuenta.cuenta_id, "llave-simultanea-01", datos)
            )
        except BaseException as exc:  # pragma: no cover - la prueba lo reporta
            salidas.append(exc)

    monkeypatch.setattr(registro, "_previo", despues_de_buscar)
    hilos = [threading.Thread(target=correr) for _ in range(2)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(60)

    assert all(isinstance(salida, registro.Registro) for salida in salidas), salidas
    assert sorted(salida.nuevo for salida in salidas) == [False, True]
    assert len({salida.recurso_id for salida in salidas}) == 1
    assert eventos() == 1


@pytest.mark.parametrize(
    ("encabezados", "cuerpo", "campo"),
    [
        ({}, {}, "header.Idempotency-Key"),
        ({"Idempotency-Key": "corta"}, {}, "header.Idempotency-Key"),
        ({"Idempotency-Key": "con espacios no"}, {}, "header.Idempotency-Key"),
        (None, {"registrado_en": "2026-09-20T10:15:00-06:00"}, "body.registrado_en"),
        (None, {"ocurrido_en": "2026-09-20T10:15:00"}, "body.ocurrido_en"),
        (None, {"canal": "WHATSAPP"}, "body.canal"),
        (None, {"actor_ref": "Juan Perez"}, "body.actor_ref"),
    ],
    ids=[
        "sin-llave",
        "llave-corta",
        "llave-con-espacios",
        "registrado-en",
        "sin-zona",
        "canal",
        "actor",
    ],
)
def test_lo_que_no_cumple_el_contrato_es_un_422_sin_registrar_nada(
    cliente, encabezados, cuerpo, campo
):
    cuenta = cuenta_canonica()
    respuesta = cliente.post(
        f"/cuentas/{cuenta.cuenta_id}/gestiones",
        json={**GESTION, **cuerpo},
        headers={"Idempotency-Key": llave()} if encabezados is None else encabezados,
    )

    assert respuesta.status_code == 422, respuesta.text
    error = respuesta.json()
    assert error["codigo"] == "ENTRADA_INVALIDA"
    assert campo in {d["campo"] for d in error["detalles"]}
    assert eventos() == 0


@pytest.mark.parametrize(
    ("cambio", "codigo", "frase"),
    [
        ({"nivel_contacto": "SIN_CONTACTO"}, "GESTION_INCOHERENTE", "PROMESA exige contacto"),
        (
            {"nivel_contacto": "CONTACTO_TITULAR", "resultado": "SIN_RESPUESTA"},
            "GESTION_INCOHERENTE",
            "SIN_RESPUESTA no puede tener",
        ),
        ({"medio": "SMS"}, "GESTION_INCOHERENTE", "no es del canal TELEFONICA"),
        ({"canal": "CAMPO", "medio": None}, "GESTION_INCOHERENTE", "trae su visita"),
        (
            {"observacion": "Llamar al 55 1234 5678 despues de las 6"},
            "DATOS_PERSONALES",
            "un numero de telefono",
        ),
        ({"ocurrido_en": "2099-01-01T10:00:00-06:00"}, "OCURRIDO_EN_FUTURO", "posterior"),
        (
            {
                "canal": "CAMPO",
                "medio": None,
                "resultado": "VISITA_REALIZADA",
                "visita": {
                    "resultado": "CONTACTO_TITULAR",
                    "inicio": "2026-09-20T11:00:00-06:00",
                },
            },
            "VISITA_INCOHERENTE",
            "antes de que empiece",
        ),
    ],
    ids=[
        "promesa-sin-contacto",
        "sin-respuesta-con-contacto",
        "medio",
        "campo-sin-visita",
        "telefono",
        "futuro",
        "visita-fuera-de-tiempo",
    ],
)
def test_una_gestion_que_no_tiene_sentido_es_un_422_que_dice_por_que(
    cliente, cambio, codigo, frase
):
    cuenta = cuenta_canonica()

    respuesta = registrar(cliente, cuenta.cuenta_id, **cambio)

    assert respuesta.status_code == 422, respuesta.text
    error = respuesta.json()
    assert error["codigo"] == codigo
    assert any(frase in d["problema"] for d in error["detalles"]), error
    assert eventos() == 0


def test_una_visita_es_una_gestion_de_campo_con_su_detalle(cliente):
    cuenta = cuenta_canonica()

    respuesta = registrar(
        cliente,
        cuenta.cuenta_id,
        canal="CAMPO",
        medio=None,
        nivel_contacto="SIN_CONTACTO",
        resultado="VISITA_REALIZADA",
        visita={
            "resultado": "NO_LOCALIZADO",
            "inicio": "2026-09-20T10:00:00-06:00",
            "fin": "2026-09-20T10:30:00-06:00",
            "observacion": "Nadie atendio; el vecino no conoce al titular.",
        },
    )

    assert respuesta.status_code == 201, respuesta.text
    visita = respuesta.json()["visita"]
    assert visita["resultado"] == "NO_LOCALIZADO"
    assert visita["visita_id"] and visita["inicio"].startswith("2026-09-20T16:00:00")


def test_una_gestion_tardia_es_valida_y_conserva_sus_dos_tiempos(cliente):
    cuenta = cuenta_canonica()

    tardia = registrar(cliente, cuenta.cuenta_id, ocurrido_en="2026-08-10T09:00:00-06:00")

    assert tardia.status_code == 201
    cuerpo = tardia.json()
    assert cuerpo["ocurrido_en"].startswith("2026-08-10T15:00:00")
    assert cuerpo["registrado_en"] > cuerpo["ocurrido_en"]


def test_una_cuenta_que_no_existe_es_un_404(cliente):
    respuesta = registrar(cliente, "00000000-0000-4000-8000-000000000000")

    assert respuesta.status_code == 404
    assert respuesta.json()["codigo"] == "CUENTA_NO_ENCONTRADA"
    assert eventos() == 0


# --- anular ---------------------------------------------------------------------------------------


def test_anular_una_gestion_la_deja_visible_y_anulada(cliente):
    cuenta = cuenta_canonica()
    gestion = registrar(cliente, cuenta.cuenta_id).json()
    ruta = f"/gestiones/{gestion['gestion_id']}/anulaciones"

    anulada = cerrar(cliente, ruta, con_llave="llave-anulacion-001", actor_ref="SUP-01")
    repetida = cerrar(cliente, ruta, con_llave="llave-anulacion-001", actor_ref="SUP-01")
    otra = cerrar(cliente, ruta)
    antes = cerrar(cliente, ruta, ocurrido_en="2026-09-19T09:00:00-06:00")

    assert anulada.status_code == 201, anulada.text
    cuerpo = anulada.json()
    assert cuerpo["estado"] == "ANULADA"
    anulacion = cuerpo["anulacion"]
    assert (anulacion["tipo_evento"], anulacion["motivo"], anulacion["actor_ref"]) == (
        "GESTION_ANULADA",
        "Registrada por error.",
        "SUP-01",
    )
    assert anulacion["evento_relacionado_id"] == gestion["evento"]["evento_id"]
    # La gestion original sigue igual, con su evento: nada se borro ni se sobrescribio.
    assert cuerpo["evento"] == gestion["evento"]
    assert (repetida.status_code, repetida.headers["idempotent-replayed"]) == (200, "true")
    assert (otra.status_code, otra.json()["codigo"]) == (409, "GESTION_YA_ANULADA")
    # Una anulacion no ocurre antes que lo que anula; se revisa despues de buscar la llave.
    assert antes.status_code == 409
    assert eventos() == 2

    vigentes = cliente.get(f"/cuentas/{cuenta.cuenta_id}/gestiones?estado=VIGENTE").json()
    anuladas = cliente.get(f"/cuentas/{cuenta.cuenta_id}/gestiones?estado=ANULADA").json()
    assert (vigentes["total"], anuladas["total"]) == (0, 1)


def test_una_anulacion_no_ocurre_antes_que_su_gestion(cliente):
    cuenta = cuenta_canonica()
    gestion = registrar(cliente, cuenta.cuenta_id).json()

    respuesta = cerrar(
        cliente,
        f"/gestiones/{gestion['gestion_id']}/anulaciones",
        ocurrido_en="2026-09-19T09:00:00-06:00",
    )

    assert respuesta.status_code == 422
    assert respuesta.json()["codigo"] == "OCURRIDO_ANTES_DEL_EVENTO"


# --- promesas y convenios -------------------------------------------------------------------------


def test_una_promesa_nace_de_una_gestion_con_resultado_promesa(cliente):
    cuenta = cuenta_canonica()
    gestion = registrar(cliente, cuenta.cuenta_id).json()
    sin_promesa = registrar(
        cliente, cuenta.cuenta_id, resultado="CONTACTO", ocurrido_en="2026-09-21T10:00:00-06:00"
    ).json()

    creada = prometer(cliente, gestion["gestion_id"], con_llave="llave-promesa-0001")
    repetida = prometer(cliente, gestion["gestion_id"], con_llave="llave-promesa-0001")
    otra = prometer(cliente, gestion["gestion_id"])
    de_un_contacto = prometer(cliente, sin_promesa["gestion_id"])
    anterior = prometer(cliente, sin_promesa["gestion_id"], fecha_limite="2026-09-19")

    assert creada.status_code == 201, creada.text
    promesa = creada.json()
    assert creada.headers["location"] == f"/promesas/{promesa['promesa_id']}"
    assert (promesa["monto_prometido"], promesa["fecha_limite"]) == ("1000.00", "2026-09-30")
    assert promesa["gestion_id"] == gestion["gestion_id"]
    assert promesa["estado_operativo"] == "VIGENTE"
    assert promesa["creada_en"] == gestion["ocurrido_en"]  # por omision, la de su gestion
    assert promesa["ultima_evaluacion"] is None
    assert promesa["evento"]["evento_relacionado_id"] == gestion["evento"]["evento_id"]
    assert repetida.status_code == 200
    assert (otra.status_code, otra.json()["codigo"]) == (409, "PROMESA_YA_REGISTRADA")
    assert (de_un_contacto.status_code, de_un_contacto.json()["codigo"]) == (
        409,
        "GESTION_SIN_PROMESA",
    )
    assert anterior.status_code == 409  # la gestion no termino en PROMESA: eso se revisa primero
    assert (
        cliente.get(f"/gestiones/{gestion['gestion_id']}").json()["promesa_id"]
        == (promesa["promesa_id"])
    )
    assert _cuantos(PromesaPago) == 1


def test_la_fecha_limite_no_es_anterior_al_dia_en_que_se_acordo_en_la_zona_de_la_fuente(cliente):
    cuenta = cuenta_canonica()
    # 23:30 del 19 en Mexico es el 20 en UTC: el dia que cuenta es el 19, el de la fuente.
    gestion = registrar(cliente, cuenta.cuenta_id, ocurrido_en="2026-09-19T23:30:00-06:00").json()

    el_mismo_dia = prometer(cliente, gestion["gestion_id"], fecha_limite="2026-09-19")
    otra = registrar(cliente, cuenta.cuenta_id, ocurrido_en="2026-09-19T23:30:00-06:00").json()
    antes = prometer(cliente, otra["gestion_id"], fecha_limite="2026-09-18")

    assert el_mismo_dia.status_code == 201, el_mismo_dia.text
    assert (antes.status_code, antes.json()["codigo"]) == (422, "FECHA_LIMITE_ANTERIOR")


def test_cancelar_una_promesa_y_anular_su_gestion(cliente):
    cuenta = cuenta_canonica()
    gestion = registrar(cliente, cuenta.cuenta_id).json()
    promesa = prometer(cliente, gestion["gestion_id"]).json()
    ruta = f"/promesas/{promesa['promesa_id']}/cancelaciones"

    cancelada = cerrar(cliente, ruta, motivo="El titular pidio otra fecha.")
    otra_vez = cerrar(cliente, ruta)
    anulacion = cerrar(cliente, f"/gestiones/{gestion['gestion_id']}/anulaciones")
    leida = cliente.get(f"/promesas/{promesa['promesa_id']}").json()

    assert cancelada.status_code == 201, cancelada.text
    assert cancelada.json()["estado_operativo"] == "CANCELADA"
    assert cancelada.json()["cancelacion"]["motivo"] == "El titular pidio otra fecha."
    assert (otra_vez.status_code, otra_vez.json()["codigo"]) == (409, "PROMESA_YA_CANCELADA")
    assert anulacion.status_code == 201
    # Anulada con su gestion: se registro por error, y eso pesa mas que la cancelacion.
    assert leida["estado_operativo"] == "ANULADA"
    assert leida["anulacion"]["evento_relacionado_id"] == gestion["evento"]["evento_id"]
    assert cerrar(cliente, ruta).json()["codigo"] == "PROMESA_ANULADA"
    nueva = prometer(cliente, gestion["gestion_id"])
    assert (nueva.status_code, nueva.json()["codigo"]) == (409, "GESTION_ANULADA")


def test_un_convenio_se_registra_con_sus_cuotas_declaradas_y_nada_mas(cliente):
    cuenta = cuenta_canonica()
    gestion = registrar(cliente, cuenta.cuenta_id, resultado="CONVENIO").json()
    cuerpo = {
        "monto_total_acordado": "3000.00",
        "fecha_inicio": "2026-10-01",
        "fecha_fin": "2026-12-31",
        "cuotas": [
            {"fecha_vencimiento": "2026-10-15", "monto": "1000.00"},
            {"fecha_vencimiento": "2026-11-15", "monto": "1000.00"},
            {"fecha_vencimiento": "2026-12-15", "monto": "1000.00"},
        ],
    }
    ruta = f"/gestiones/{gestion['gestion_id']}/convenios"

    no_suman = cliente.post(
        ruta,
        json={**cuerpo, "cuotas": cuerpo["cuotas"][:2]},
        headers={"Idempotency-Key": llave()},
    )
    con_plazo = cliente.post(
        ruta, json={**cuerpo, "plazo": 3}, headers={"Idempotency-Key": llave()}
    )
    creado = cliente.post(ruta, json=cuerpo, headers={"Idempotency-Key": llave()})

    assert (no_suman.status_code, no_suman.json()["codigo"]) == (422, "CUOTAS_INCOHERENTES")
    assert con_plazo.status_code == 422  # un plazo no genera cuotas: no existe en el contrato
    assert creado.status_code == 201, creado.text
    convenio = creado.json()
    assert [c["numero"] for c in convenio["cuotas"]] == [1, 2, 3]
    assert convenio["evaluacion_de_cuotas"] == "NO_EVALUABLE"
    assert convenio["estado_operativo"] == "VIGENTE"
    recuperacion = convenio["recuperacion_observada_durante_convenio"]
    assert (recuperacion["monto"], recuperacion["movimientos"]) == ("0.00", 0)
    assert "no un ledger" in convenio["aviso"]
    sin_calendario = registrar(
        cliente, cuenta.cuenta_id, resultado="CONVENIO", ocurrido_en="2026-09-22T10:00:00-06:00"
    ).json()
    simple = cliente.post(
        f"/gestiones/{sin_calendario['gestion_id']}/convenios",
        json={"monto_total_acordado": "500.00", "fecha_inicio": "2026-10-01"},
        headers={"Idempotency-Key": llave()},
    )
    assert simple.status_code == 201 and simple.json()["cuotas"] == []
    listados = cliente.get(f"/cuentas/{cuenta.cuenta_id}/convenios").json()
    assert listados["total"] == 2 and _cuantos(ConvenioCobranza) == 2
    cancelado = cerrar(cliente, f"/convenios/{convenio['convenio_id']}/cancelaciones")
    assert cancelado.json()["estado_operativo"] == "CANCELADO"


# --- leer -----------------------------------------------------------------------------------------


def test_las_gestiones_de_una_cuenta_se_filtran_en_la_base_y_se_paginan(cliente):
    cuenta = cuenta_canonica()
    otra = cuenta_canonica(2)
    for dia, canal, nivel, resultado in (
        (1, "DIGITAL", "NO_APLICA", "SIN_RESPUESTA"),
        (3, "TELEFONICA", "SIN_CONTACTO", "SIN_RESPUESTA"),
        (5, "TELEFONICA", "CONTACTO_TERCERO", "CONTACTO"),
        (8, "TELEFONICA", "CONTACTO_TITULAR", "RECHAZO"),
    ):
        registrar(
            cliente,
            cuenta.cuenta_id,
            ocurrido_en=f"2026-09-{dia:02d}T10:00:00-06:00",
            canal=canal,
            medio=None,
            nivel_contacto=nivel,
            resultado=resultado,
        )
    registrar(cliente, otra.cuenta_id)
    base = f"/cuentas/{cuenta.cuenta_id}/gestiones"

    todas = cliente.get(base).json()
    telefonicas = cliente.get(f"{base}?canal=TELEFONICA&por_pagina=2&pagina=2").json()
    del_rango = cliente.get(f"{base}?desde=2026-09-03&hasta=2026-09-05").json()
    titulares = cliente.get(f"{base}?nivel_contacto=CONTACTO_TITULAR").json()

    assert todas["total"] == 4
    assert [g["ocurrido_en"][:10] for g in todas["elementos"]] == [
        "2026-09-08",
        "2026-09-05",
        "2026-09-03",
        "2026-09-01",
    ]
    assert telefonicas["total"] == 3
    assert [g["ocurrido_en"][:10] for g in telefonicas["elementos"]] == ["2026-09-03"]
    assert [g["ocurrido_en"][:10] for g in del_rango["elementos"]] == ["2026-09-05", "2026-09-03"]
    assert [g["resultado"] for g in titulares["elementos"]] == ["RECHAZO"]
    assert "no son una fuente oficial" in todas["aviso"].lower()


def test_la_cuenta_360_resume_su_lifecycle_tambien_como_se_veia_en_una_fecha(cliente):
    cuenta = cuenta_canonica()
    contacto = registrar(
        cliente,
        cuenta.cuenta_id,
        ocurrido_en="2026-09-05T10:00:00-06:00",
        resultado="CONTACTO",
    ).json()
    con_promesa = registrar(cliente, cuenta.cuenta_id, ocurrido_en="2026-09-10T10:00:00-06:00")
    promesa = prometer(cliente, con_promesa.json()["gestion_id"], fecha_limite="2026-09-20").json()
    errada = registrar(
        cliente,
        cuenta.cuenta_id,
        ocurrido_en="2026-09-12T10:00:00-06:00",
        canal="CAMPO",
        medio=None,
        nivel_contacto="SIN_CONTACTO",
        resultado="VISITA_REALIZADA",
        visita={"resultado": "NO_LOCALIZADO"},
    ).json()
    cerrar(
        cliente,
        f"/gestiones/{errada['gestion_id']}/anulaciones",
        ocurrido_en="2026-09-13T09:00:00-06:00",
    )
    cerrar(
        cliente,
        f"/promesas/{promesa['promesa_id']}/cancelaciones",
        ocurrido_en="2026-09-15T09:00:00-06:00",
    )

    hoy = cliente.get(f"/cuentas/{cuenta.cuenta_id}").json()["lifecycle_resumen"]
    al_11 = cliente.get(f"/cuentas/{cuenta.cuenta_id}?al=2026-09-11").json()["lifecycle_resumen"]
    al_6 = cliente.get(f"/cuentas/{cuenta.cuenta_id}?al=2026-09-06").json()["lifecycle_resumen"]

    assert hoy["version_lifecycle"] == "lifecycle/v1"
    assert (hoy["gestiones"], hoy["gestiones_anuladas"], hoy["visitas"]) == (2, 1, 0)
    assert hoy["ultima_gestion"]["resultado"] == "PROMESA"
    assert hoy["ultimo_contacto_titular"]["gestion_id"] == con_promesa.json()["gestion_id"]
    assert (hoy["promesas"], hoy["promesas_vigentes"]) == (1, 0)
    # El 11 la promesa todavia no se cancelaba; el 6 ni siquiera existia.
    assert (al_11["promesas"], al_11["promesas_vigentes"], al_11["gestiones"]) == (1, 1, 2)
    assert (al_6["gestiones"], al_6["promesas"]) == (1, 0)
    assert al_6["ultima_gestion"]["gestion_id"] == contacto["gestion_id"]


def test_la_linea_de_tiempo_junta_las_tres_verdades_sin_mezclarlas(cliente, tmp_path):
    corte_1, corte_2 = date(2026, 9, 2), date(2026, 9, 9)
    cuentas = [Cuenta(n) for n in range(1, 41)]
    ingerir_corte(tmp_path, corte_1, cuentas)
    ingerir_corte(tmp_path, corte_2, cuentas)
    historiar_todo()
    with sesion() as s:
        # Una cuenta cuyo corte dice algo de su promesa en los dos cortes.
        con_promesa = s.exec(
            select(SnapshotCuenta.cuenta_canonica_id)
            .where(SnapshotCuenta.estatus_promesa_pago.is_not(None))
            .group_by(SnapshotCuenta.cuenta_canonica_id)
            .having(func.count() == 2)
            .order_by(SnapshotCuenta.cuenta_canonica_id)
        ).first()
        cuenta = s.get_one(CuentaCanonica, con_promesa)
    cliente_unico = cuenta.cliente_unico
    ingerir_pagos_de(
        tmp_path, "pagos.csv", [pago(int(cliente_unico[2:]), "2026-09-05 10:00:00", "300.00")]
    )
    historiar_todo()
    interpretar_todo()
    gestion = registrar(cliente, cuenta.cuenta_id, ocurrido_en="2026-09-04T18:00:00-06:00").json()
    # Una gestion tardia: ocurrio antes del primer corte, se registra al final.
    tardia = registrar(
        cliente,
        cuenta.cuenta_id,
        ocurrido_en="2026-09-01T09:00:00-06:00",
        resultado="CONTACTO",
    ).json()

    linea = cliente.get(f"/cuentas/{cuenta.cuenta_id}/lifecycle?orden=asc").json()
    solo_corte = cliente.get(f"/cuentas/{cuenta.cuenta_id}/lifecycle?dominio=FUENTE_CORTE").json()

    assert linea["total"] == 5
    secuencia = [(e["dominio"], e["tipo"], e["instante"][:10]) for e in linea["elementos"]]
    assert secuencia == [
        ("OPERACIONAL", "GESTION_REGISTRADA", "2026-09-01"),
        ("FUENTE_CORTE", "OBSERVACION_EN_CORTE", "2026-09-02"),
        ("OPERACIONAL", "GESTION_REGISTRADA", "2026-09-05"),  # 18:00 en Mexico es el 5 en UTC
        ("ECONOMICO", "PAGO", "2026-09-05"),
        ("FUENTE_CORTE", "OBSERVACION_EN_CORTE", "2026-09-09"),
    ]
    primero, corte, de_gestion, movimiento, _ = linea["elementos"]
    assert primero["operacional"]["gestion_id"] == tardia["gestion_id"]
    assert primero["fuente_corte"] is primero["economico"] is None
    assert de_gestion["operacional"]["gestion_id"] == gestion["gestion_id"]
    observacion = corte["fuente_corte"]
    assert observacion["estatus_promesa_pago"] is not None
    assert observacion["source_row"] >= 2 and observacion["artefacto_original"]["sha256"]
    assert "no un evento" in observacion["aviso"]
    assert movimiento["economico"]["monto_reportado"] == "300.00"
    assert solo_corte["total"] == 2
    # Lo que el corte dice de una promesa no es una promesa: no se fabrico ninguna.
    assert _cuantos(PromesaPago) == 0
    with sesion() as s:
        assert s.exec(select(func.count()).select_from(EventoLifecycle)).one() == 2
    assert UUID(linea["cuenta_id"]) == cuenta.cuenta_id
