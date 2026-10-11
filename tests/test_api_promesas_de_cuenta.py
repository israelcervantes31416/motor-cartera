"""GET /cuentas/{cuenta_id}/promesas contra PostgreSQL: el orden, la paginacion, los tres estados
operativos, los errores y que la respuesta sea exactamente lo que lee `lifecycle.consultas`, con la
ultima evaluacion de cada promesa."""

from __future__ import annotations

from datetime import date

import pytest
from lifecycle_escenarios import cerrar, cuenta_canonica, prometer, registrar

from motor_cartera.api.operacional import promesa_respuesta
from motor_cartera.db.modelos import EjecucionEvaluacionPromesas
from motor_cartera.db.sesion import sesion, sesion_de_lectura
from motor_cartera.evaluacion.ejecuciones import abrir, evaluar
from motor_cartera.lifecycle import consultas
from motor_cartera.lifecycle.reglas import EstadoPromesa

pytestmark = pytest.mark.usefixtures("bd")

ZONA = "America/Mexico_City"


def _promesa(cliente, cuenta_id, dia: int, fecha_limite: str) -> dict:
    """Una gestion con resultado PROMESA el dia `dia` de septiembre, y su promesa."""
    gestion = registrar(cliente, cuenta_id, ocurrido_en=f"2026-09-{dia:02d}T10:00:00-06:00").json()
    promesa = prometer(cliente, gestion["gestion_id"], fecha_limite=fecha_limite)
    assert promesa.status_code == 201, promesa.text
    return {"gestion": gestion, "promesa": promesa.json()}


def _cancelar(cliente, registro: dict, ocurrido_en: str) -> None:
    respuesta = cerrar(
        cliente,
        f"/promesas/{registro['promesa']['promesa_id']}/cancelaciones",
        ocurrido_en=ocurrido_en,
        motivo="El titular pidio otra fecha.",
    )
    assert respuesta.status_code == 201, respuesta.text


def _anular(cliente, registro: dict, ocurrido_en: str) -> None:
    respuesta = cerrar(
        cliente,
        f"/gestiones/{registro['gestion']['gestion_id']}/anulaciones",
        ocurrido_en=ocurrido_en,
    )
    assert respuesta.status_code == 201, respuesta.text


@pytest.fixture
def escenario(cliente) -> dict:
    """Cinco promesas de una cuenta y una de otra.

    - p1: limite el 30 de septiembre, vigente.
    - p2: limite el 5 de octubre, cancelada el 26 de septiembre.
    - p3: limite el 10 de octubre; su gestion se anulo.
    - p4: limite el 10 de octubre; se cancelo y despues se anulo su gestion: la anulacion pesa mas.
    - p5: limite el 25 de septiembre, vigente.

    El orden de la lista es fecha limite descendente y, en un empate, la registrada despues
    primero: p4, p3, p2, p1, p5.
    """
    cuenta, otra = cuenta_canonica(1), cuenta_canonica(2)
    p1 = _promesa(cliente, cuenta.cuenta_id, 20, "2026-09-30")
    p2 = _promesa(cliente, cuenta.cuenta_id, 21, "2026-10-05")
    p3 = _promesa(cliente, cuenta.cuenta_id, 22, "2026-10-10")
    p4 = _promesa(cliente, cuenta.cuenta_id, 23, "2026-10-10")
    p5 = _promesa(cliente, cuenta.cuenta_id, 24, "2026-09-25")
    ajena = _promesa(cliente, otra.cuenta_id, 20, "2026-09-30")
    _cancelar(cliente, p2, "2026-09-26T09:00:00-06:00")
    _anular(cliente, p3, "2026-09-26T09:00:00-06:00")
    _cancelar(cliente, p4, "2026-09-26T09:00:00-06:00")
    _anular(cliente, p4, "2026-09-27T09:00:00-06:00")
    ids = {
        nombre: registro["promesa"]["promesa_id"]
        for nombre, registro in (("p1", p1), ("p2", p2), ("p3", p3), ("p4", p4), ("p5", p5))
    }
    return {"cuenta": cuenta, "ids": ids, "ajena": ajena["promesa"]["promesa_id"]}


def _ruta(escenario) -> str:
    return f"/cuentas/{escenario['cuenta'].cuenta_id}/promesas"


def _nombres(escenario, cuerpo: dict) -> list[str]:
    por_id = {promesa_id: nombre for nombre, promesa_id in escenario["ids"].items()}
    return [por_id[p["promesa_id"]] for p in cuerpo["elementos"]]


def _evaluar(as_of: date) -> EjecucionEvaluacionPromesas:
    with sesion() as s:
        ejecucion, nueva = abrir(
            s, "DSP_001", "CARTERA_PRINCIPAL", as_of, zona=ZONA, max_intentos=5, reusar=False
        )
        s.commit()
        assert nueva
        ejecucion_id = ejecucion.id
    evaluar(ejecucion_id)
    with sesion() as s:
        return s.get_one(EjecucionEvaluacionPromesas, ejecucion_id)


def test_lista_las_promesas_de_la_cuenta_de_la_fecha_limite_mas_reciente_a_la_mas_antigua(
    cliente, escenario
):
    respuesta = cliente.get(_ruta(escenario))

    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["cuenta_id"] == str(escenario["cuenta"].cuenta_id)
    assert cuerpo["cliente_unico"] == escenario["cuenta"].cliente_unico
    assert (cuerpo["total"], cuerpo["pagina"], cuerpo["por_pagina"]) == (5, 1, 50)
    assert _nombres(escenario, cuerpo) == ["p4", "p3", "p2", "p1", "p5"]
    # La promesa de la otra cuenta no aparece.
    assert escenario["ajena"] not in {p["promesa_id"] for p in cuerpo["elementos"]}
    assert [p["estado_operativo"] for p in cuerpo["elementos"]] == [
        "ANULADA",
        "ANULADA",
        "CANCELADA",
        "VIGENTE",
        "VIGENTE",
    ]
    assert [p["fecha_limite"] for p in cuerpo["elementos"]] == [
        "2026-10-10",
        "2026-10-10",
        "2026-10-05",
        "2026-09-30",
        "2026-09-25",
    ]
    assert "no son una fuente oficial" in cuerpo["aviso"].lower()
    # Sin ninguna evaluacion, ninguna promesa trae una.
    assert all(p["ultima_evaluacion"] is None for p in cuerpo["elementos"])


def test_cada_promesa_trae_su_linaje_y_sus_cierres_segun_su_estado(cliente, escenario):
    elementos = {p["promesa_id"]: p for p in cliente.get(_ruta(escenario)).json()["elementos"]}
    p1, p2, p3, p4 = (elementos[escenario["ids"][n]] for n in ("p1", "p2", "p3", "p4"))

    # Vigente: sin cancelacion ni anulacion.
    assert p1["cancelacion"] is None and p1["anulacion"] is None
    # Cancelada: su cancelacion se refiere al evento de la promesa.
    assert p2["anulacion"] is None
    assert p2["cancelacion"]["tipo_evento"] == "PROMESA_CANCELADA"
    assert p2["cancelacion"]["evento_relacionado_id"] == p2["evento"]["evento_id"]
    # Anulada: la anulacion es de su gestion, no de la promesa.
    assert p3["cancelacion"] is None
    assert p3["anulacion"]["tipo_evento"] == "GESTION_ANULADA"
    assert p3["anulacion"]["evento_relacionado_id"] == p3["evento"]["evento_relacionado_id"]
    # Cancelada y despues anulada: trae los dos cierres y su estado es ANULADA.
    assert p4["cancelacion"]["tipo_evento"] == "PROMESA_CANCELADA"
    assert p4["anulacion"]["tipo_evento"] == "GESTION_ANULADA"
    assert p4["estado_operativo"] == "ANULADA"


@pytest.mark.parametrize(
    ("estado", "esperadas"),
    [
        ("VIGENTE", ["p1", "p5"]),
        ("CANCELADA", ["p2"]),
        ("ANULADA", ["p4", "p3"]),
    ],
)
def test_filtra_por_cada_estado_operativo(cliente, escenario, estado, esperadas):
    cuerpo = cliente.get(f"{_ruta(escenario)}?estado={estado}").json()

    assert cuerpo["total"] == len(esperadas)
    assert _nombres(escenario, cuerpo) == esperadas
    assert {p["estado_operativo"] for p in cuerpo["elementos"]} == {estado}


def test_los_tres_estados_reparten_todas_las_promesas_sin_repetir_ninguna(cliente, escenario):
    por_estado = [
        cliente.get(f"{_ruta(escenario)}?estado={estado}").json() for estado in EstadoPromesa
    ]

    assert sum(cuerpo["total"] for cuerpo in por_estado) == 5
    vistas = [p["promesa_id"] for cuerpo in por_estado for p in cuerpo["elementos"]]
    assert sorted(vistas) == sorted(escenario["ids"].values())


def test_pagina_en_el_mismo_orden_con_el_total_en_cada_pagina(cliente, escenario):
    paginas = [
        cliente.get(f"{_ruta(escenario)}?por_pagina=2&pagina={n}").json() for n in (1, 2, 3, 4)
    ]

    assert [_nombres(escenario, p) for p in paginas] == [["p4", "p3"], ["p2", "p1"], ["p5"], []]
    assert {p["total"] for p in paginas} == {5}
    assert [(p["pagina"], p["por_pagina"]) for p in paginas] == [(n, 2) for n in (1, 2, 3, 4)]
    filtrada = cliente.get(f"{_ruta(escenario)}?estado=ANULADA&por_pagina=1&pagina=2").json()
    assert (filtrada["total"], _nombres(escenario, filtrada)) == (2, ["p3"])


def test_trae_la_ultima_evaluacion_de_cada_promesa_la_de_fecha_de_corte_mas_reciente(
    cliente, escenario
):
    # Sin pagos observados: lo que vence queda NO_EVALUABLE y lo que no, PENDIENTE.
    al_26 = _evaluar(date(2026, 9, 26))
    al_6 = _evaluar(date(2026, 10, 6))
    assert al_26.estado == al_6.estado == "EXITOSA"

    elementos = {p["promesa_id"]: p for p in cliente.get(_ruta(escenario)).json()["elementos"]}

    ultimas = {
        nombre: elementos[promesa_id]["ultima_evaluacion"]
        for nombre, promesa_id in escenario["ids"].items()
    }
    assert {u["evaluacion_run_id"] for u in ultimas.values()} == {str(al_6.evaluacion_run_id)}
    assert {u["as_of"] for u in ultimas.values()} == {"2026-10-06"}
    assert {nombre: u["estado"] for nombre, u in ultimas.items()} == {
        "p1": "NO_EVALUABLE",  # vencio el 30 sin pagos que la evaluen
        "p2": "CANCELADA",
        "p3": "CANCELADA",  # su gestion se anulo
        "p4": "CANCELADA",
        "p5": "NO_EVALUABLE",
    }
    assert ultimas["p3"]["motivos"] == [{"codigo": "GESTION_ANULADA"}]
    assert ultimas["p2"]["motivos"][0]["codigo"] == "PROMESA_CANCELADA"


@pytest.mark.parametrize(
    "consulta",
    ["", "?por_pagina=2&pagina=2", "?estado=VIGENTE", "?estado=CANCELADA", "?estado=ANULADA"],
)
def test_responde_exactamente_lo_que_lee_lifecycle_consultas(cliente, escenario, consulta):
    _evaluar(date(2026, 9, 26))
    parametros = dict(p.split("=") for p in consulta.lstrip("?").split("&") if p)
    estado = EstadoPromesa(parametros["estado"]) if "estado" in parametros else None
    por_pagina = int(parametros.get("por_pagina", 50))
    pagina = int(parametros.get("pagina", 1))

    cuerpo = cliente.get(f"{_ruta(escenario)}{consulta}").json()

    with sesion_de_lectura() as s:
        total, vistas = consultas.promesas_de_cuenta(
            s,
            escenario["cuenta"],
            estado=estado,
            desplazamiento=(pagina - 1) * por_pagina,
            limite=por_pagina,
        )
        esperadas = [promesa_respuesta(v).model_dump(mode="json") for v in vistas]
    assert cuerpo["total"] == total
    assert cuerpo["elementos"] == esperadas


def test_una_cuenta_sin_promesas_responde_una_lista_vacia(cliente, escenario):
    sin_promesas = cuenta_canonica(3)

    cuerpo = cliente.get(f"/cuentas/{sin_promesas.cuenta_id}/promesas").json()

    assert (cuerpo["total"], cuerpo["elementos"]) == (0, [])
    assert cuerpo["cliente_unico"] == sin_promesas.cliente_unico


def test_una_cuenta_que_no_existe_es_un_404(cliente, escenario):
    respuesta = cliente.get("/cuentas/00000000-0000-4000-8000-000000000000/promesas")

    assert respuesta.status_code == 404
    assert respuesta.json()["codigo"] == "CUENTA_NO_ENCONTRADA"


@pytest.mark.parametrize(
    ("ruta", "campo"),
    [
        ("/cuentas/no-es-un-uuid/promesas", "path.cuenta_id"),
        ("{cuenta}?estado=CUMPLIDA", "query.estado"),
        ("{cuenta}?pagina=0", "query.pagina"),
        ("{cuenta}?por_pagina=501", "query.por_pagina"),
    ],
    ids=["uuid-invalido", "estado-de-evaluacion", "pagina-cero", "pagina-demasiado-grande"],
)
def test_lo_que_no_es_valido_es_un_422(cliente, escenario, ruta, campo):
    respuesta = cliente.get(ruta.format(cuenta=_ruta(escenario)))

    assert respuesta.status_code == 422, respuesta.text
    error = respuesta.json()
    assert error["codigo"] == "ENTRADA_INVALIDA"
    assert campo in {d["campo"] for d in error["detalles"]}


def test_sin_la_clave_de_la_api_es_un_401(cliente, escenario):
    respuesta = cliente.get(_ruta(escenario), headers={"X-API-Key": ""})

    assert respuesta.status_code == 401


def test_una_promesa_que_no_existe_es_un_404_y_un_id_que_no_es_uuid_un_422(cliente, escenario):
    no_existe = cliente.get("/promesas/00000000-0000-4000-8000-000000000000")
    invalido = cliente.get("/promesas/no-es-un-uuid")
    existe = cliente.get(f"/promesas/{escenario['ids']['p1']}")

    assert (no_existe.status_code, no_existe.json()["codigo"]) == (404, "PROMESA_NO_ENCONTRADA")
    assert (invalido.status_code, invalido.json()["codigo"]) == (422, "ENTRADA_INVALIDA")
    # La misma promesa que trae la lista de su cuenta.
    en_la_lista = next(
        p
        for p in cliente.get(_ruta(escenario)).json()["elementos"]
        if p["promesa_id"] == escenario["ids"]["p1"]
    )
    assert existe.json() == en_la_lista
