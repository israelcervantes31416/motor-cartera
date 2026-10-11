"""decision/v2 por la API, contra PostgreSQL: pedirla, leer su ejecucion, sus resultados por pagina
y con filtros, la decision explicada de una cuenta, las de una cuenta y las de un corte, y la Cuenta
360 con su ultima decision."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from decision_v2_escenarios import C3, C4, corte_id, pequeno, reloj
from historia_escenarios import cuenta
from lifecycle_escenarios import cuenta_canonica, gestion_de
from sqlalchemy import func, text
from sqlalchemy.exc import DBAPIError
from sqlmodel import select

from motor_cartera.db.modelos import EjecucionDecisionV2
from motor_cartera.db.sesion import sesion
from motor_cartera.decision_v2.features import FEATURES
from motor_cartera.lifecycle.reglas import NivelContacto, ResultadoGestion

pytestmark = pytest.mark.usefixtures("bd")


def _pedir(cliente, **cuerpo):
    return cliente.post("/decisiones-v2", json={"corte_id": str(corte_id(C4)), **cuerpo})


@pytest.fixture
def decidida(cliente, tmp_path, trabajar):
    """El escenario pequeno, con tres gestiones sin contacto de la 3, decidido por la API y el
    worker."""
    pequeno(tmp_path)
    for dia in (12, 13, 14):
        gestion_de(
            cuenta(3).cuenta_id,
            f"2026-09-{dia:02d}T10:00:00-06:00",
            nivel_contacto=NivelContacto.SIN_CONTACTO,
            resultado=ResultadoGestion.SIN_RESPUESTA,
        )
    respuesta = _pedir(cliente)
    assert respuesta.status_code == 201, respuesta.text
    trabajar()
    return respuesta.json()


# --- pedirla --------------------------------------------------------------------------------------


def test_pedirla_responde_201_en_proceso_con_sus_entradas_fijas(cliente, tmp_path, trabajar):
    pequeno(tmp_path)
    antes = reloj()

    respuesta = _pedir(cliente)

    assert respuesta.status_code == 201, respuesta.text
    cuerpo = respuesta.json()
    assert respuesta.headers["location"] == f"/decisiones-v2/{cuerpo['decision_run_id']}"
    assert (cuerpo["estado"], cuerpo["resultado"], cuerpo["modo"]) == (
        "EN_PROCESO",
        None,
        "OPERATIVO",
    )
    assert cuerpo["corte"] == {"corte_id": str(corte_id(C4)), "fecha_corte": "2026-09-16"}
    assert cuerpo["as_of"] == "2026-09-16"
    assert datetime.fromisoformat(cuerpo["known_at"]) > antes
    assert cuerpo["versiones"] == {
        "motor": "decision/v2",
        "features": "features-decision/v1",
        "politica": "decision-policy/public-v1",
    }
    assert len(cuerpo["firma_inputs"]) == len(cuerpo["hash_politica"]) == 64
    assert cuerpo["conteos"] == {
        "cuentas_objetivo": 3,
        "cuentas_decididas": 0,
        "cuentas_fallidas": 0,
    }
    assert cuerpo["trabajo_id"] is not None
    assert "no es un modelo predictivo" in cuerpo["aviso"]
    assert "no representa una politica real" in cuerpo["aviso"]
    # El fin del dia logico: medianoche del 17 en la zona de la fuente.
    assert cuerpo["fin_del_dia"].startswith("2026-09-17T06:00:00")

    trabajar()
    terminada = cliente.get(respuesta.headers["location"]).json()
    assert (terminada["estado"], terminada["resultado"]) == ("EXITOSA", "DECISION_PUBLICADA")
    assert terminada["conteos"]["cuentas_decididas"] == 3
    assert terminada["known_at"] == cuerpo["known_at"]  # el que se fijo al abrir
    assert terminada["distribucion"]["cuentas"] == 3
    # Los pagos empiezan el 14: agosto, en las ventanas de 30 y 90 dias, no tiene datos, y no hay
    # ninguna atribucion. Esas features son NULL, y la decision lo dice.
    assert terminada["disponibilidad"]["pagos"] == {"7d": True, "30d": False, "90d": False}
    assert terminada["disponibilidad"]["atribucion"] == {"30d": False, "90d": False}
    assert [a["codigo"] for a in terminada["advertencias"]] == [
        "CONOCIMIENTO_POSTERIOR_AL_DIA",  # decide un dia pasado con lo que se sabe hoy
        "PAGOS_NO_DISPONIBLES",
        "ATRIBUCION_NO_DISPONIBLES",
    ]


def test_un_backtest_declara_su_known_at(cliente, tmp_path):
    pequeno(tmp_path)
    k = reloj()

    sin_known_at = cliente.post(
        "/decisiones-v2", json={"corte_id": str(corte_id(C3)), "modo": "BACKTEST"}
    )
    backtest = cliente.post(
        "/decisiones-v2",
        json={"corte_id": str(corte_id(C3)), "modo": "BACKTEST", "known_at": k.isoformat()},
    )

    assert (sin_known_at.status_code, sin_known_at.json()["codigo"]) == (422, "KNOWN_AT_REQUERIDO")
    assert backtest.status_code == 201, backtest.text
    cuerpo = backtest.json()
    assert (cuerpo["modo"], cuerpo["as_of"]) == ("BACKTEST", "2026-09-09")
    assert datetime.fromisoformat(cuerpo["known_at"]) == k
    assert "BACKTEST" in {a["codigo"] for a in cuerpo["advertencias"]}


@pytest.mark.parametrize(
    ("cuerpo", "estado", "codigo"),
    [
        ({"corte_id": "00000000-0000-4000-8000-000000000000"}, 404, "CORTE_NO_ENCONTRADO"),
        ({"as_of": "2026-09-15"}, 422, "AS_OF_DISTINTO_DEL_CORTE"),
        ({"known_at": "2099-01-01T00:00:00Z"}, 422, "KNOWN_AT_FUTURO"),
        ({"known_at": "2026-09-17T08:00:00"}, 422, "ENTRADA_INVALIDA"),  # sin zona
        ({"version_politica": "decision-policy/privada-v9"}, 422, "POLITICA_NO_SOPORTADA"),
        ({"modo": "PRODUCCION"}, 422, "ENTRADA_INVALIDA"),
        ({"probabilidad": 0.9}, 422, "ENTRADA_INVALIDA"),
        ({"corte_id": "no-es-un-uuid"}, 422, "ENTRADA_INVALIDA"),
    ],
    ids=[
        "corte",
        "as-of",
        "futuro",
        "sin-zona",
        "politica",
        "modo",
        "campo-desconocido",
        "uuid",
    ],
)
def test_lo_que_no_se_puede_decidir_responde_su_error_sin_registrar_nada(
    cliente, tmp_path, cuerpo, estado, codigo
):
    pequeno(tmp_path)

    respuesta = _pedir(cliente, **cuerpo)

    assert (respuesta.status_code, respuesta.json()["codigo"]) == (estado, codigo), respuesta.text
    with sesion() as s:
        assert s.exec(select(func.count()).select_from(EjecucionDecisionV2)).one() == 0


def test_un_operativo_de_un_corte_que_ya_no_es_el_ultimo_se_niega(cliente, tmp_path):
    pequeno(tmp_path)

    respuesta = cliente.post("/decisiones-v2", json={"corte_id": str(corte_id(C3))})

    assert (respuesta.status_code, respuesta.json()["codigo"]) == (422, "CORTE_NO_VIGENTE")


def test_en_proceso_y_ya_generada_son_409(cliente, tmp_path, trabajar):
    pequeno(tmp_path)
    k = reloj().isoformat()
    primera = _pedir(cliente, known_at=k)

    en_proceso = _pedir(cliente)
    trabajar()
    repetida = _pedir(cliente, known_at=k)

    assert primera.status_code == 201
    assert (en_proceso.status_code, en_proceso.json()["codigo"]) == (409, "DECISION_V2_EN_PROCESO")
    assert (repetida.status_code, repetida.json()["codigo"]) == (409, "DECISION_V2_YA_GENERADA")
    assert primera.json()["decision_run_id"] in repetida.json()["mensaje"]


def test_sin_la_clave_es_401(cliente, tmp_path):
    pequeno(tmp_path)

    respuesta = cliente.post(
        "/decisiones-v2", json={"corte_id": str(corte_id(C4))}, headers={"X-API-Key": ""}
    )

    assert respuesta.status_code == 401


# --- leerla ---------------------------------------------------------------------------------------


def test_una_decision_que_no_existe_es_404_y_un_id_que_no_es_uuid_422(cliente):
    no_existe = cliente.get("/decisiones-v2/00000000-0000-4000-8000-000000000000")
    invalido = cliente.get("/decisiones-v2/no-es-un-uuid")
    resultados = cliente.get("/decisiones-v2/00000000-0000-4000-8000-000000000000/resultados")

    assert (no_existe.status_code, no_existe.json()["codigo"]) == (404, "DECISION_V2_NO_ENCONTRADA")
    assert (invalido.status_code, invalido.json()["codigo"]) == (422, "ENTRADA_INVALIDA")
    assert resultados.status_code == 404


def test_los_resultados_se_paginan_en_el_orden_de_la_cuenta_y_se_filtran(cliente, decidida):
    base = f"/decisiones-v2/{decidida['decision_run_id']}/resultados"

    todos = cliente.get(base).json()
    paginas = [cliente.get(f"{base}?por_pagina=2&pagina={n}").json() for n in (1, 2, 3)]
    medias = cliente.get(f"{base}?prioridad_final=MEDIA").json()
    digitales = cliente.get(f"{base}?canal_recomendado=DIGITAL").json()
    las_dos = cliente.get(f"{base}?prioridad_final=MEDIA&canal_recomendado=DIGITAL").json()
    ninguna = cliente.get(f"{base}?prioridad_final=MUY_ALTA").json()
    invalida = cliente.get(f"{base}?prioridad_final=URGENTE")

    assert todos["total"] == 3
    clientes = [r["cliente_unico"] for r in todos["elementos"]]
    assert clientes == ["CU0000000001", "CU0000000002", "CU0000000003"]
    assert [[r["cliente_unico"] for r in p["elementos"]] for p in paginas] == [
        clientes[:2],
        clientes[2:],
        [],
    ]
    # 1: MEDIA -1 (pago del 14) = BAJA; 2: ALTA -1 (pago del 16) = MEDIA; 3: BAJA +1 = MEDIA.
    assert {r["cliente_unico"]: r["prioridad_final"] for r in todos["elementos"]} == {
        "CU0000000001": "BAJA",
        "CU0000000002": "MEDIA",
        "CU0000000003": "MEDIA",
    }
    assert (medias["total"], [r["cliente_unico"] for r in medias["elementos"]]) == (
        2,
        ["CU0000000002", "CU0000000003"],
    )
    assert digitales["total"] == 3
    assert las_dos["total"] == 2
    assert (ninguna["total"], ninguna["elementos"]) == (0, [])
    assert invalida.status_code == 422
    primero = todos["elementos"][0]
    assert (primero["score_nivel_base"], primero["ajuste_total"], primero["score_nivel_final"]) == (
        2,
        -1,
        1,
    )
    assert len(primero["decision_hash"]) == 64
    assert [m["codigo"] for m in primero["motivos"]] == [
        "PRIORIDAD_BASE_MORA",
        "RECUPERACION_RECIENTE",
        "PRIORIDAD_FINAL",
        "CANAL_POR_PRIORIDAD",
    ]


def test_la_decision_de_una_cuenta_se_explica_y_se_verifica(cliente, decidida):
    tres = cuenta(3)
    respuesta = cliente.get(
        f"/decisiones-v2/{decidida['decision_run_id']}/resultados/{tres.cuenta_id}"
    )

    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert list(cuerpo["features"]) == [f.nombre for f in FEATURES]
    assert cuerpo["features"]["gestiones_sin_contacto_7d"] == 3
    assert cuerpo["features"]["dias_desde_ultimo_pago"] is None
    assert cuerpo["features"]["recuperacion_interpretada_7d"] == "0.00"
    assert (cuerpo["prioridad_base"], cuerpo["prioridad_final"], cuerpo["canal_recomendado"]) == (
        "BAJA",
        "MEDIA",
        "DIGITAL",
    )
    reglas = {r["codigo"]: r for r in cuerpo["reglas"]}
    assert list(reglas) == [
        "RECUPERACION_RECIENTE",
        "PROMESA_VIGENTE",
        "SATURACION_SIN_CONTACTO",
        "PROMESA_INCUMPLIDA_RECIENTE",
    ]
    assert {codigo: (r["activa"], r["efecto"]) for codigo, r in reglas.items()} == {
        "RECUPERACION_RECIENTE": (False, 0),
        "PROMESA_VIGENTE": (False, 0),
        "SATURACION_SIN_CONTACTO": (True, 1),
        "PROMESA_INCUMPLIDA_RECIENTE": (False, 0),
    }
    assert reglas["SATURACION_SIN_CONTACTO"]["condiciones"] == [
        {
            "feature": "gestiones_sin_contacto_7d",
            "valor": "3",
            "operador": ">=",
            "umbral": "3",
            "cumple": True,
        },
        {
            "feature": "contactos_titular_30d",
            "valor": "0",
            "operador": "=",
            "umbral": "0",
            "cumple": True,
        },
    ]
    assert cuerpo["verificacion"] == {"feature_hash": True, "decision_hash": True, "motivos": True}
    assert len(cuerpo["feature_hash"]) == len(cuerpo["decision_hash"]) == 64


def test_la_verificacion_descubre_una_decision_alterada(cliente, decidida):
    # Lo publicado no se reescribe desde la aplicacion: el trigger lo impide.
    alterar = text(
        "UPDATE resultado_decision_v2 SET decision_hash = :hash WHERE cuenta_canonica_id = :cuenta"
    )
    parametros = {"hash": bytes(32), "cuenta": cuenta(1).id}
    with sesion() as s, pytest.raises(DBAPIError, match="no se reescribe"):
        s.execute(alterar, parametros)
    # Alguien que apaga el trigger si puede alterarla; la verificacion lo descubre.
    with sesion() as s:
        s.execute(
            text("ALTER TABLE resultado_decision_v2 DISABLE TRIGGER tr_decision_v2_inmutable")
        )
        s.execute(alterar, parametros)
        s.execute(text("ALTER TABLE resultado_decision_v2 ENABLE TRIGGER tr_decision_v2_inmutable"))
        s.commit()

    cuerpo = cliente.get(
        f"/decisiones-v2/{decidida['decision_run_id']}/resultados/{cuenta(1).cuenta_id}"
    ).json()

    assert cuerpo["verificacion"] == {"feature_hash": True, "decision_hash": False, "motivos": True}


def test_una_cuenta_que_no_es_del_corte_no_tiene_decision(cliente, decidida):
    ajena = cuenta_canonica(99)
    base = f"/decisiones-v2/{decidida['decision_run_id']}/resultados"

    sin_decision = cliente.get(f"{base}/{ajena.cuenta_id}")
    no_existe = cliente.get(f"{base}/{uuid4()}")

    assert (sin_decision.status_code, sin_decision.json()["codigo"]) == (404, "CUENTA_SIN_DECISION")
    assert (no_existe.status_code, no_existe.json()["codigo"]) == (404, "CUENTA_NO_ENCONTRADA")


def test_las_decisiones_de_una_cuenta_y_las_de_un_corte(cliente, tmp_path, trabajar):
    pequeno(tmp_path)
    k = reloj()
    del_9 = cliente.post(
        "/decisiones-v2",
        json={"corte_id": str(corte_id(C3)), "modo": "BACKTEST", "known_at": k.isoformat()},
    ).json()
    trabajar()
    primera = _pedir(cliente, known_at=k.isoformat()).json()
    trabajar()
    segunda = _pedir(cliente).json()
    trabajar()
    # Una FALLIDA del corte: aparece en la lista del corte, no en la historia de la cuenta.
    with sesion() as s:
        fallida = EjecucionDecisionV2(
            corte_canonico_id=s.exec(
                EjecucionDecisionV2.__table__.select()  # type: ignore[attr-defined]
            )
            .first()
            .corte_canonico_id,
            despacho_id="DSP_001",
            cartera_id="CARTERA_PRINCIPAL",
            as_of=C3,
            known_at=datetime.now(UTC) - timedelta(days=1),
            modo="BACKTEST",
            zona_horaria="America/Mexico_City",
            version_motor="decision/v2",
            version_features="features-decision/v1",
            version_politica="decision-policy/public-v1",
            hash_politica="a" * 64,
            firma_inputs="b" * 64,
            estado="FALLIDA",
            resultado="ERROR_INTERNO",
            cuentas_objetivo=3,
        )
        s.add(fallida)
        s.commit()

    historia = cliente.get(f"/cuentas/{cuenta(1).cuenta_id}/decisiones-v2").json()
    pagina = cliente.get(f"/cuentas/{cuenta(1).cuenta_id}/decisiones-v2?por_pagina=1&pagina=3")
    del_corte = cliente.get(f"/cartera/cortes/{corte_id(C4)}/decisiones-v2").json()
    del_corte_9 = cliente.get(f"/cartera/cortes/{corte_id(C3)}/decisiones-v2").json()
    sin_corte = cliente.get(f"/cartera/cortes/{uuid4()}/decisiones-v2")

    # De la de as_of mas reciente a la mas antigua; en el mismo as_of, la de known_at mas reciente.
    assert historia["total"] == 3
    assert [d["decision_run_id"] for d in historia["elementos"]] == [
        segunda["decision_run_id"],
        primera["decision_run_id"],
        del_9["decision_run_id"],
    ]
    assert [d["as_of"] for d in historia["elementos"]] == ["2026-09-16", "2026-09-16", "2026-09-09"]
    assert historia["elementos"][0]["motivos"][0] == "PRIORIDAD_BASE_MORA"
    assert pagina.json()["elementos"][0]["decision_run_id"] == del_9["decision_run_id"]
    assert [e["decision_run_id"] for e in del_corte["elementos"]] == [
        segunda["decision_run_id"],
        primera["decision_run_id"],
    ]
    assert del_corte_9["total"] == 2
    assert {e["estado"] for e in del_corte_9["elementos"]} == {"EXITOSA", "FALLIDA"}
    assert (sin_corte.status_code, sin_corte.json()["codigo"]) == (404, "CORTE_NO_ENCONTRADO")


def test_la_cuenta_360_trae_su_ultima_decision_v2_tambien_como_se_veia(cliente, decidida):
    tres = cuenta(3)

    hoy = cliente.get(f"/cuentas/{tres.cuenta_id}").json()["ultima_decision_v2"]
    antes = cliente.get(f"/cuentas/{tres.cuenta_id}?al=2026-09-15").json()["ultima_decision_v2"]
    ese_dia = cliente.get(f"/cuentas/{tres.cuenta_id}?al=2026-09-16").json()["ultima_decision_v2"]

    assert hoy == {
        "decision_run_id": decidida["decision_run_id"],
        "as_of": "2026-09-16",
        "prioridad": "MEDIA",
        "canal": "DIGITAL",
    }
    assert antes is None
    assert ese_dia == hoy
