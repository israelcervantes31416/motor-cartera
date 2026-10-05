"""La orquestacion por HTTP: el flujo automatico de una corrida, sus trabajos y reanudar un flujo
detenido.

Una cartera se sube por POST /corridas y las pruebas hacen lo que haria el worker: tomar los
trabajos de la cola, uno por uno o hasta vaciarla. Lo que se prueba es lo que un cliente ve por la
API mientras tanto. La cola, el worker y el flujo ya se prueban por su lado; aqui, que la capa HTTP
los cuente bien: estados, etapas, identificadores publicos, errores y paginacion. Las pruebas de la
ultima seccion no tocan la base.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from motor_cartera import __version__
from motor_cartera.api import crear_app
from motor_cartera.api.esquemas import (
    EJEMPLO_FLUJO,
    EJEMPLO_FLUJO_DETENIDO,
    EJEMPLO_FLUJO_EN_PROCESO,
    EJEMPLO_TRABAJO,
    EJEMPLO_TRABAJO_PENDIENTE,
    FlujoRespuesta,
    TrabajoRespuesta,
)
from motor_cartera.config import Config
from motor_cartera.db.modelos import EjecucionDecision, TipoTrabajo, TrabajoOrquestacion
from motor_cartera.db.sesion import sesion
from motor_cartera.decision import ejecuciones as decision_ejecuciones
from motor_cartera.ingesta.corridas import abrir_corrida, procesar_corrida
from motor_cartera.orquestacion import cola, worker
from motor_cartera.orquestacion.worker import identificador_worker, procesar_un_trabajo
from motor_cartera.ruteo import ejecuciones as ruteo_ejecuciones
from motor_cartera.territorial import ejecuciones as territorial_ejecuciones

en_la_base = pytest.mark.usefixtures("bd")

CORTE = "2026-09-30"

CAMPOS_DE_ERROR = {"codigo", "mensaje", "detalles", "run_id", "pagos_run_id"}
CAMPOS_DEL_FLUJO = {
    "flujo_id",
    "run_id",
    "estado",
    "etapa",
    "decision_run_id",
    "territorial_run_id",
    "ruteo_run_id",
    "creado_en",
    "actualizado_en",
    "terminado_en",
    "duracion_segundos",
    "detalle",
}
CAMPOS_DEL_TRABAJO = {
    "trabajo_id",
    "flujo_id",
    "tipo",
    "estado",
    "objetivo_run_id",
    "intentos",
    "max_intentos",
    "creado_en",
    "disponible_desde",
    "tomado_en",
    "latido_en",
    "lease_hasta",
    "terminado_en",
    "ultimo_error",
}
CAMPOS_DE_LOS_TRABAJOS = {"flujo_id", "run_id", "total", "pagina", "por_pagina", "elementos"}

RUTAS = [
    ("GET", "/corridas/{run_id}/flujo"),
    ("GET", "/flujos/{flujo_id}"),
    ("GET", "/flujos/{flujo_id}/trabajos"),
    ("GET", "/trabajos/{trabajo_id}"),
    ("POST", "/flujos/{flujo_id}/reanudar"),
]

Cuentas = list[tuple[str, str, int, str, str]]
"""Una cartera de prueba: por cuenta, (cliente_unico, clave del municipio, dias de atraso, saldo,
canal de la cartera)."""

# Tres cuentas en dos municipios, una de campo en cada uno: el flujo llega hasta dos rutas.
PEQUENA: Cuentas = [
    ("CU00000041", "21114", 120, "3000.00", "DIGITAL"),  # CAMPO
    ("CU00000044", "21114", 0, "7000.00", "CAMPO"),  # DIGITAL
    ("CU00000004", "09002", 95, "1500.00", "CAMPO"),  # CAMPO
]

COMPLETADO = "La ingesta, la decision, la organizacion territorial y el ruteo terminaron EXITOSA."


def _csv(cuentas: Cuentas) -> bytes:
    cartera = pd.DataFrame(
        {
            "cliente_unico": [cliente for cliente, *_ in cuentas],
            "saldo_total": [saldo for *_, saldo, _ in cuentas],
            "dias_atraso": [dias for _, _, dias, _, _ in cuentas],
            "producto": ["CONSUMO"] * len(cuentas),
            "canal": [canal for *_, canal in cuentas],
            "cve_entidad": [clave[:2] for _, clave, *_ in cuentas],
            "cve_municipio": [clave[2:] for _, clave, *_ in cuentas],
            "fecha_corte": [CORTE] * len(cuentas),
        }
    )
    return cartera.to_csv(index=False).encode("utf-8")


def _subir(cliente, contenido: bytes):
    respuesta = cliente.post(
        "/corridas", files={"archivo": ("cartera.csv", contenido, "application/octet-stream")}
    )
    assert respuesta.status_code == 201, respuesta.text
    return respuesta


def _trabajar() -> None:
    """Lo que haria el worker: procesa la cola hasta que no quede ningun trabajo que tomar."""
    worker_id = identificador_worker()
    while procesar_un_trabajo(worker_id, Config()) is not None:
        pass


def _una_vez() -> None:
    """Un solo trabajo, como un worker con --una-vez."""
    assert procesar_un_trabajo(identificador_worker(), Config()) is not None


def _flujo(cliente, run_id: str) -> dict:
    respuesta = cliente.get(f"/corridas/{run_id}/flujo")
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


def _trabajos(cliente, flujo_id: str) -> list[dict]:
    respuesta = cliente.get(f"/flujos/{flujo_id}/trabajos")
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()["elementos"]


def _instante(texto: str) -> datetime:
    return datetime.fromisoformat(texto)


# --- el flujo automatico, de la subida al ruteo ----------------------------------------------


@en_la_base
def test_una_subida_llega_hasta_el_ruteo_sin_pedir_cada_etapa(cliente):
    # El pipeline completo por HTTP: un POST, el worker, y el cliente sigue el flujo.
    subida = _subir(cliente, _csv(PEQUENA))
    run_id = subida.json()["run_id"]
    assert subida.json()["estado"] == "EN_PROCESO"

    recien_subido = _flujo(cliente, run_id)
    assert set(recien_subido) == CAMPOS_DEL_FLUJO
    assert (recien_subido["run_id"], recien_subido["estado"], recien_subido["etapa"]) == (
        run_id,
        "EN_PROCESO",
        "INGESTA",
    )
    assert (
        recien_subido["decision_run_id"],
        recien_subido["territorial_run_id"],
        recien_subido["ruteo_run_id"],
    ) == (None, None, None)
    assert (recien_subido["terminado_en"], recien_subido["duracion_segundos"]) == (None, None)
    assert recien_subido["detalle"] == "La ingesta esta en la cola."
    flujo_id = recien_subido["flujo_id"]
    (en_cola,) = _trabajos(cliente, flujo_id)
    assert (en_cola["tipo"], en_cola["estado"], en_cola["intentos"]) == ("INGESTA", "PENDIENTE", 0)
    assert (en_cola["objetivo_run_id"], en_cola["flujo_id"]) == (run_id, flujo_id)
    assert (en_cola["tomado_en"], en_cola["lease_hasta"], en_cola["terminado_en"]) == (
        None,
        None,
        None,
    )

    _trabajar()

    flujo = _flujo(cliente, run_id)
    assert (flujo["flujo_id"], flujo["estado"], flujo["etapa"]) == (
        flujo_id,
        "COMPLETADO",
        "COMPLETADA",
    )
    assert flujo["detalle"] == COMPLETADO
    assert flujo["terminado_en"].endswith("Z") and flujo["duracion_segundos"] >= 0
    assert cliente.get(f"/flujos/{flujo_id}").json() == flujo
    # Cada etapa EXITOSA, y encadenada con la anterior.
    corrida = cliente.get(f"/corridas/{run_id}").json()
    decision = cliente.get(f"/decisiones/{flujo['decision_run_id']}").json()
    territorial = cliente.get(f"/territoriales/{flujo['territorial_run_id']}").json()
    ruteo = cliente.get(f"/ruteos/{flujo['ruteo_run_id']}").json()
    assert [r["estado"] for r in (corrida, decision, territorial, ruteo)] == ["EXITOSA"] * 4
    assert decision["run_id"] == territorial["run_id"] == ruteo["run_id"] == run_id
    assert territorial["decision_run_id"] == ruteo["decision_run_id"] == flujo["decision_run_id"]
    assert ruteo["territorial_run_id"] == flujo["territorial_run_id"]
    assert (ruteo["rutas_publicadas"], ruteo["paradas_publicadas"]) == (2, 2)
    # Un trabajo por etapa, en el orden en que entraron a la cola, cada uno con el identificador
    # publico de su recurso. Ninguno dice que worker lo tuvo.
    trabajos = _trabajos(cliente, flujo_id)
    assert [t["tipo"] for t in trabajos] == ["INGESTA", "DECISION", "TERRITORIAL", "RUTEO"]
    assert [t["objetivo_run_id"] for t in trabajos] == [
        run_id,
        flujo["decision_run_id"],
        flujo["territorial_run_id"],
        flujo["ruteo_run_id"],
    ]
    for trabajo in trabajos:
        assert set(trabajo) == CAMPOS_DEL_TRABAJO
        assert (trabajo["flujo_id"], trabajo["estado"], trabajo["intentos"]) == (
            flujo_id,
            "COMPLETADO",
            1,
        )
        assert (trabajo["lease_hasta"], trabajo["ultimo_error"]) == (None, None)
        assert trabajo["tomado_en"] and trabajo["latido_en"] and trabajo["terminado_en"]
        assert cliente.get(f"/trabajos/{trabajo['trabajo_id']}").json() == trabajo


@en_la_base
def test_el_flujo_avanza_una_etapa_por_trabajo_y_cada_una_nace_en_la_cola(cliente):
    # Al terminar una etapa, la siguiente ya tiene su ejecucion EN_PROCESO y su trabajo: no queda
    # un hueco entre etapas en el que el flujo no apunte a nada.
    run_id = _subir(cliente, _csv(PEQUENA)).json()["run_id"]
    esperado = [
        ("DECISION", "decision_run_id", "/decisiones/{}"),
        ("TERRITORIAL", "territorial_run_id", "/territoriales/{}"),
        ("RUTEO", "ruteo_run_id", "/ruteos/{}"),
    ]
    detalles = {
        "DECISION": "La corrida termino EXITOSA; la decision esta en la cola.",
        "TERRITORIAL": "La decision termino EXITOSA; la organizacion territorial esta en la cola.",
        "RUTEO": "La organizacion territorial termino EXITOSA; el ruteo esta en la cola.",
    }
    antes = _flujo(cliente, run_id)

    for numero, (etapa, campo, ruta) in enumerate(esperado, start=1):
        _una_vez()
        flujo = _flujo(cliente, run_id)
        assert (flujo["estado"], flujo["etapa"], flujo["detalle"]) == (
            "EN_PROCESO",
            etapa,
            detalles[etapa],
        )
        assert cliente.get(ruta.format(flujo[campo])).json()["estado"] == "EN_PROCESO"
        siguientes = [c for _, c, _ in esperado[numero:]]
        assert all(flujo[c] is None for c in siguientes)
        trabajos = _trabajos(cliente, flujo["flujo_id"])
        assert len(trabajos) == numero + 1
        assert (trabajos[-1]["tipo"], trabajos[-1]["estado"]) == (etapa, "PENDIENTE")
        assert trabajos[-1]["objetivo_run_id"] == flujo[campo]
        assert _instante(flujo["actualizado_en"]) >= _instante(antes["actualizado_en"])
        antes = flujo

    _una_vez()

    flujo = _flujo(cliente, run_id)
    assert (flujo["estado"], flujo["etapa"], flujo["detalle"]) == (
        "COMPLETADO",
        "COMPLETADA",
        COMPLETADO,
    )


@en_la_base
def test_lo_que_la_api_registro_sobrevive_a_que_la_api_se_apague(app, clave_api):
    # La API no guarda trabajo en memoria: lo que registra queda en PostgreSQL. Se apaga antes de
    # que el worker tome nada, el worker lo ejecuta igual, y otra API, recien arrancada, lo ve
    # terminado.
    encabezados = {"X-API-Key": clave_api}
    with TestClient(app, headers=encabezados) as primera:
        run_id = _subir(primera, _csv(PEQUENA)).json()["run_id"]

    _trabajar()

    with TestClient(crear_app(Config(api_key=clave_api)), headers=encabezados) as otra:
        flujo = _flujo(otra, run_id)
        trabajos = _trabajos(otra, flujo["flujo_id"])
    assert (flujo["estado"], flujo["etapa"]) == ("COMPLETADO", "COMPLETADA")
    assert [(t["tipo"], t["estado"]) for t in trabajos] == [
        ("INGESTA", "COMPLETADO"),
        ("DECISION", "COMPLETADO"),
        ("TERRITORIAL", "COMPLETADO"),
        ("RUTEO", "COMPLETADO"),
    ]


# --- un flujo que se detiene -------------------------------------------------------------------


@en_la_base
@pytest.mark.parametrize(
    ("contenido", "estado"),
    [
        pytest.param(b"cliente,saldo\nCU00000001,10\n", "FALLIDA", id="FALLIDA"),
        pytest.param(
            _csv([*PEQUENA[:2], ("CU00000004", "09002", 95, "1500.00", "OTRO")]),
            "RECHAZADA",
            id="RECHAZADA",
        ),
    ],
)
def test_un_flujo_detenido_en_la_ingesta_no_se_reanuda_se_vuelve_a_subir(
    cliente, contenido, estado
):
    run_id = _subir(cliente, contenido).json()["run_id"]
    _trabajar()
    flujo = _flujo(cliente, run_id)
    assert (flujo["estado"], flujo["etapa"], flujo["decision_run_id"]) == (
        "DETENIDO",
        "INGESTA",
        None,
    )
    assert flujo["detalle"] == (
        f"La corrida termino {estado}. Para reintentar, vuelve a subir el archivo."
    )
    assert flujo["terminado_en"] is not None

    respuesta = cliente.post(f"/flujos/{flujo['flujo_id']}/reanudar")

    assert respuesta.status_code == 409
    error = respuesta.json()
    assert set(error) == CAMPOS_DE_ERROR
    assert (error["codigo"], error["run_id"]) == ("FLUJO_NO_REANUDABLE", run_id)
    assert error["mensaje"] == (
        f"El flujo {flujo['flujo_id']} se detuvo en la ingesta: la corrida termino {estado}, y "
        "una corrida terminada no se reabre. Vuelve a subir el archivo: eso crea otra corrida y "
        "otro flujo."
    )
    assert _flujo(cliente, run_id) == flujo
    assert len(_trabajos(cliente, flujo["flujo_id"])) == 1


@en_la_base
@pytest.mark.parametrize(
    ("etapa", "motor", "funcion", "campo", "ruta", "que"),
    [
        pytest.param(
            "DECISION",
            decision_ejecuciones,
            "decidir_cuenta",
            "decision_run_id",
            "/decisiones/{}",
            "La decision",
            id="DECISION",
        ),
        pytest.param(
            "TERRITORIAL",
            territorial_ejecuciones,
            "priorizar_territorios",
            "territorial_run_id",
            "/territoriales/{}",
            "La organizacion territorial",
            id="TERRITORIAL",
        ),
        pytest.param(
            "RUTEO",
            ruteo_ejecuciones,
            "rutear_territorio",
            "ruteo_run_id",
            "/ruteos/{}",
            "El ruteo",
            id="RUTEO",
        ),
    ],
)
def test_un_flujo_detenido_en_una_etapa_se_reanuda_y_llega_al_ruteo(
    cliente, monkeypatch, etapa, motor, funcion, campo, ruta, que
):
    # El motor termina FALLIDA: la entrega se cumplio, su trabajo queda COMPLETADO, y el flujo se
    # detiene. Reanudar crea otra ejecucion de esa etapa; la que fallo queda en el historial.
    run_id = _subir(cliente, _csv(PEQUENA)).json()["run_id"]
    with monkeypatch.context() as parche:
        parche.setattr(motor, funcion, _revienta)
        _trabajar()
    detenido = _flujo(cliente, run_id)
    flujo_id, fallida = detenido["flujo_id"], detenido[campo]
    assert (detenido["estado"], detenido["etapa"]) == ("DETENIDO", etapa)
    assert detenido["detalle"] == f"{que} termino FALLIDA. Para reintentarla, reanuda el flujo."
    assert cliente.get(ruta.format(fallida)).json()["estado"] == "FALLIDA"

    respuesta = cliente.post(f"/flujos/{flujo_id}/reanudar")

    assert respuesta.status_code == 200
    reanudado = respuesta.json()
    assert set(reanudado) == CAMPOS_DEL_FLUJO
    assert (reanudado["flujo_id"], reanudado["estado"], reanudado["etapa"]) == (
        flujo_id,
        "EN_PROCESO",
        etapa,
    )
    assert reanudado[campo] not in (None, fallida)
    assert (reanudado["terminado_en"], reanudado["duracion_segundos"]) == (None, None)
    assert reanudado["detalle"] == (
        f"Se reanudo en la etapa {etapa} con otra ejecucion; la que fallo queda en el historial."
    )
    assert "Location" not in respuesta.headers
    assert _flujo(cliente, run_id) == reanudado
    nuevo = _trabajos(cliente, flujo_id)[-1]
    assert (nuevo["tipo"], nuevo["estado"], nuevo["objetivo_run_id"]) == (
        etapa,
        "PENDIENTE",
        reanudado[campo],
    )

    _trabajar()

    flujo = _flujo(cliente, run_id)
    assert (flujo["estado"], flujo["etapa"], flujo[campo]) == (
        "COMPLETADO",
        "COMPLETADA",
        reanudado[campo],
    )
    assert cliente.get(ruta.format(fallida)).json()["estado"] == "FALLIDA"
    # Los dos trabajos de la etapa: el de la fallida tambien se entrego, una sola vez.
    trabajos = _trabajos(cliente, flujo_id)
    tipos = ["INGESTA", "DECISION", "TERRITORIAL", "RUTEO"]
    tipos.insert(tipos.index(etapa), etapa)
    assert [t["tipo"] for t in trabajos] == tipos
    assert {(t["estado"], t["intentos"]) for t in trabajos} == {("COMPLETADO", 1)}
    de_la_etapa = [t["objetivo_run_id"] for t in trabajos if t["tipo"] == etapa]
    assert de_la_etapa == [fallida, reanudado[campo]]


def _revienta(*_):
    raise RuntimeError("falla simulada del motor")


@en_la_base
def test_un_flujo_detenido_sin_una_ejecucion_fallida_no_se_reanuda(cliente, monkeypatch):
    # La decision termino EXITOSA, pero la organizacion territorial no se pudo abrir: alguien la
    # abrio a mano, en modo directo, justo en ese momento. No hay nada fallido que reintentar.
    run_id = _subir(cliente, _csv(PEQUENA)).json()["run_id"]
    decidir = worker.MANEJADORES[TipoTrabajo.DECISION]

    def decide_y_alguien_organiza_a_mano(ejecucion_decision_id):
        decidir(ejecucion_decision_id)
        with sesion() as s:
            fuente = s.get_one(EjecucionDecision, ejecucion_decision_id)
            territorial_ejecuciones.abrir_ejecucion(s, fuente)

    monkeypatch.setitem(worker.MANEJADORES, TipoTrabajo.DECISION, decide_y_alguien_organiza_a_mano)
    _trabajar()
    flujo = _flujo(cliente, run_id)
    assert (flujo["estado"], flujo["etapa"], flujo["territorial_run_id"]) == (
        "DETENIDO",
        "DECISION",
        None,
    )
    assert flujo["detalle"].startswith(
        "La decision termino EXITOSA, pero la etapa TERRITORIAL no se pudo abrir: "
    )

    respuesta = cliente.post(f"/flujos/{flujo['flujo_id']}/reanudar")

    assert respuesta.status_code == 409
    error = respuesta.json()
    assert (error["codigo"], error["run_id"]) == ("FLUJO_NO_REANUDABLE", run_id)
    assert error["mensaje"] == (
        f"La etapa DECISION del flujo {flujo['flujo_id']} termino EXITOSA: no hay una ejecucion "
        "fallida que reintentar."
    )
    assert _flujo(cliente, run_id) == flujo


@en_la_base
def test_reanudar_un_flujo_que_sigue_en_proceso_409(cliente):
    run_id = _subir(cliente, _csv(PEQUENA)).json()["run_id"]
    flujo_id = _flujo(cliente, run_id)["flujo_id"]

    respuesta = cliente.post(f"/flujos/{flujo_id}/reanudar")

    assert respuesta.status_code == 409
    error = respuesta.json()
    assert (error["codigo"], error["run_id"]) == ("FLUJO_EN_PROCESO", run_id)
    assert error["mensaje"] == (
        "El flujo sigue EN_PROCESO, en la etapa INGESTA: no hay nada que reanudar. Consulta "
        f"/flujos/{flujo_id}."
    )
    assert len(_trabajos(cliente, flujo_id)) == 1


@en_la_base
def test_reanudar_un_flujo_completado_409(cliente):
    run_id = _subir(cliente, _csv(PEQUENA)).json()["run_id"]
    _trabajar()
    flujo = _flujo(cliente, run_id)

    respuesta = cliente.post(f"/flujos/{flujo['flujo_id']}/reanudar")

    assert respuesta.status_code == 409
    error = respuesta.json()
    assert (error["codigo"], error["run_id"]) == ("FLUJO_YA_COMPLETADO", run_id)
    assert error["mensaje"] == (
        "El flujo ya esta COMPLETADO: llego hasta el ruteo y no hay nada que reanudar."
    )
    assert _flujo(cliente, run_id) == flujo


# --- los trabajos ------------------------------------------------------------------------------


@en_la_base
def test_un_error_del_worker_devuelve_el_trabajo_a_la_cola_y_el_flujo_sigue(cliente, monkeypatch):
    # La entrega se reintenta: el trabajo vuelve PENDIENTE, con su espera y sin la traza, la
    # corrida sigue EN_PROCESO y el flujo no se detiene. No es lo mismo que un motor que termina
    # FALLIDA.
    run_id = _subir(cliente, _csv(PEQUENA)).json()["run_id"]

    def se_cae(_):
        raise RuntimeError("se cayo la conexion a postgresql://motor:secreto@base")

    monkeypatch.setitem(worker.MANEJADORES, TipoTrabajo.INGESTA, se_cae)

    _una_vez()

    flujo = _flujo(cliente, run_id)
    assert (flujo["estado"], flujo["etapa"]) == ("EN_PROCESO", "INGESTA")
    respuesta = cliente.get(f"/flujos/{flujo['flujo_id']}/trabajos")
    (trabajo,) = respuesta.json()["elementos"]
    assert (trabajo["estado"], trabajo["intentos"], trabajo["lease_hasta"]) == (
        "PENDIENTE",
        1,
        None,
    )
    assert trabajo["ultimo_error"] == "Error de worker (RuntimeError); ver la bitacora."
    assert "secreto" not in respuesta.text
    assert _instante(trabajo["disponible_desde"]) > _instante(trabajo["tomado_en"])
    assert cliente.get(f"/corridas/{run_id}").json()["estado"] == "EN_PROCESO"


@en_la_base
def test_un_trabajo_ejecutando_dice_hasta_cuando_es_de_su_worker_pero_no_cual(cliente):
    run_id = _subir(cliente, _csv(PEQUENA)).json()["run_id"]
    worker_id = identificador_worker()
    reclamo = cola.reclamar(worker_id, 60)
    assert reclamo is not None

    respuesta = cliente.get(f"/trabajos/{reclamo.trabajo_id}")

    assert respuesta.status_code == 200
    trabajo = respuesta.json()
    assert set(trabajo) == CAMPOS_DEL_TRABAJO
    assert (trabajo["tipo"], trabajo["estado"], trabajo["intentos"]) == ("INGESTA", "EJECUTANDO", 1)
    assert trabajo["objetivo_run_id"] == run_id
    assert trabajo["latido_en"] == trabajo["tomado_en"]
    assert _instante(trabajo["lease_hasta"]) - _instante(trabajo["tomado_en"]) == timedelta(
        seconds=60
    )
    assert worker_id not in respuesta.text
    assert worker_id.split(":")[0] not in respuesta.text  # ni la maquina


@en_la_base
def test_el_trabajo_de_una_etapa_pedida_a_mano_no_es_de_ningun_flujo(cliente):
    # Una corrida publicada en modo directo, como con el CLI, no tiene flujo; su decision se pide a
    # mano y su trabajo se consulta igual, con flujo_id null.
    contenido = _csv(PEQUENA)
    with sesion() as s:
        corrida = abrir_corrida(s, origen="cartera.csv", contenido=contenido)
    procesar_corrida(corrida.id, contenido)
    run_id = str(corrida.run_id)
    sin_flujo = cliente.get(f"/corridas/{run_id}/flujo")
    assert sin_flujo.status_code == 404
    assert (sin_flujo.json()["codigo"], sin_flujo.json()["run_id"]) == (
        "FLUJO_NO_ENCONTRADO",
        run_id,
    )
    assert sin_flujo.json()["mensaje"] == (
        f"La corrida {run_id} no tiene un flujo automatico: se publico antes de v0.5.0, o con el "
        "CLI."
    )
    decision_run_id = cliente.post(f"/corridas/{run_id}/decisiones").json()["decision_run_id"]
    with sesion() as s:
        trabajo_id = s.exec(
            select(TrabajoOrquestacion.trabajo_id).where(
                TrabajoOrquestacion.tipo == TipoTrabajo.DECISION
            )
        ).one()

    pendiente = cliente.get(f"/trabajos/{trabajo_id}").json()
    _trabajar()
    completado = cliente.get(f"/trabajos/{trabajo_id}").json()

    assert (pendiente["flujo_id"], pendiente["tipo"], pendiente["objetivo_run_id"]) == (
        None,
        "DECISION",
        decision_run_id,
    )
    assert (pendiente["estado"], pendiente["intentos"], pendiente["tomado_en"]) == (
        "PENDIENTE",
        0,
        None,
    )
    assert (completado["estado"], completado["intentos"], completado["lease_hasta"]) == (
        "COMPLETADO",
        1,
        None,
    )
    assert completado["terminado_en"] is not None
    assert cliente.get(f"/decisiones/{decision_run_id}").json()["estado"] == "EXITOSA"


@en_la_base
def test_los_trabajos_de_un_flujo_se_paginan_sin_huecos_ni_repetidos(cliente):
    run_id = _subir(cliente, _csv(PEQUENA)).json()["run_id"]
    _trabajar()
    flujo_id = _flujo(cliente, run_id)["flujo_id"]
    ruta = f"/flujos/{flujo_id}/trabajos"
    todos = [t["trabajo_id"] for t in cliente.get(ruta).json()["elementos"]]

    paginas = [cliente.get(ruta, params={"pagina": p, "por_pagina": 3}).json() for p in (1, 2, 3)]

    for pagina in paginas:
        assert set(pagina) == CAMPOS_DE_LOS_TRABAJOS
        assert (pagina["flujo_id"], pagina["run_id"], pagina["total"]) == (flujo_id, run_id, 4)
    assert [len(p["elementos"]) for p in paginas] == [3, 1, 0]
    assert [t["trabajo_id"] for p in paginas for t in p["elementos"]] == todos
    assert len(set(todos)) == 4


# --- lo que no existe --------------------------------------------------------------------------


@en_la_base
def test_el_flujo_de_una_corrida_que_no_existe_404_corrida_no_encontrada(cliente):
    run_id = uuid4()

    respuesta = cliente.get(f"/corridas/{run_id}/flujo")

    assert respuesta.status_code == 404
    error = respuesta.json()
    assert set(error) == CAMPOS_DE_ERROR
    assert (error["codigo"], error["run_id"]) == ("CORRIDA_NO_ENCONTRADA", str(run_id))


@en_la_base
@pytest.mark.parametrize(
    ("metodo", "ruta", "codigo", "mensaje"),
    [
        ("GET", "/flujos/{}", "FLUJO_NO_ENCONTRADO", "No existe un flujo con flujo_id {}."),
        (
            "GET",
            "/flujos/{}/trabajos",
            "FLUJO_NO_ENCONTRADO",
            "No existe un flujo con flujo_id {}.",
        ),
        (
            "POST",
            "/flujos/{}/reanudar",
            "FLUJO_NO_ENCONTRADO",
            "No existe un flujo con flujo_id {}.",
        ),
        ("GET", "/trabajos/{}", "TRABAJO_NO_ENCONTRADO", "No existe un trabajo con trabajo_id {}."),
    ],
)
def test_un_flujo_o_un_trabajo_que_no_existe_404_sin_run_id(cliente, metodo, ruta, codigo, mensaje):
    identificador = uuid4()

    respuesta = cliente.request(metodo, ruta.format(identificador))

    assert respuesta.status_code == 404
    error = respuesta.json()
    assert set(error) == CAMPOS_DE_ERROR
    assert (error["codigo"], error["run_id"]) == (codigo, None)
    assert error["mensaje"] == mensaje.format(identificador)


# --- sin base de datos -------------------------------------------------------------------------


@pytest.mark.parametrize(("metodo", "ruta"), RUTAS)
@pytest.mark.parametrize(
    ("clave", "codigo"), [("", "API_KEY_AUSENTE"), ("otra", "API_KEY_INVALIDA")]
)
def test_las_rutas_de_orquestacion_piden_api_key(cliente, metodo, ruta, clave, codigo):
    # La clave se revisa antes de buscar nada.
    url = ruta.format(run_id=uuid4(), flujo_id=uuid4(), trabajo_id=uuid4())

    respuesta = cliente.request(metodo, url, headers={"X-API-Key": clave})

    assert respuesta.status_code == 401
    assert set(respuesta.json()) == CAMPOS_DE_ERROR
    assert respuesta.json()["codigo"] == codigo


@pytest.mark.parametrize(
    ("metodo", "ruta", "campo"),
    [
        ("GET", "/corridas/123/flujo", "path.run_id"),
        ("GET", "/flujos/123", "path.flujo_id"),
        ("GET", "/flujos/123/trabajos", "path.flujo_id"),
        ("GET", "/trabajos/123", "path.trabajo_id"),
        ("POST", "/flujos/123/reanudar", "path.flujo_id"),
    ],
)
def test_un_identificador_que_no_es_uuid_422(cliente, metodo, ruta, campo):
    respuesta = cliente.request(metodo, ruta)

    assert respuesta.status_code == 422
    assert set(respuesta.json()) == CAMPOS_DE_ERROR
    assert respuesta.json()["codigo"] == "ENTRADA_INVALIDA"
    assert [detalle["campo"] for detalle in respuesta.json()["detalles"]] == [campo]


@pytest.mark.parametrize(
    ("parametros", "campo"),
    [({"pagina": 0}, "query.pagina"), ({"por_pagina": 501}, "query.por_pagina")],
)
def test_paginacion_de_trabajos_fuera_de_rango_422(cliente, parametros, campo):
    respuesta = cliente.get(f"/flujos/{uuid4()}/trabajos", params=parametros)

    assert respuesta.status_code == 422
    assert respuesta.json()["codigo"] == "ENTRADA_INVALIDA"
    assert [detalle["campo"] for detalle in respuesta.json()["detalles"]] == [campo]


def test_openapi_documenta_las_cinco_operaciones_de_orquestacion(app):
    api = app.openapi()
    operaciones = {
        (metodo.upper(), ruta): operacion
        for ruta, metodos in api["paths"].items()
        for metodo, operacion in metodos.items()
        if "orquestacion" in operacion.get("tags", [])
    }

    assert {clave: set(op["responses"]) for clave, op in operaciones.items()} == {
        ("GET", "/corridas/{run_id}/flujo"): {"200", "401", "404", "422"},
        ("GET", "/flujos/{flujo_id}"): {"200", "401", "404", "422"},
        ("GET", "/flujos/{flujo_id}/trabajos"): {"200", "401", "404", "422"},
        ("GET", "/trabajos/{trabajo_id}"): {"200", "401", "404", "422"},
        ("POST", "/flujos/{flujo_id}/reanudar"): {"200", "401", "404", "409", "422"},
    }
    modelos = {}
    for clave, operacion in operaciones.items():
        assert operacion["tags"] == ["orquestacion"]
        assert operacion["summary"] and operacion["description"]
        exito = operacion["responses"]["200"]
        modelos[clave] = exito["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[1]
        for codigo, respuesta in operacion["responses"].items():
            if codigo[0] in "45":
                esquema = respuesta["content"]["application/json"]["schema"]
                assert esquema["$ref"].endswith("/ErrorRespuesta")
    assert modelos == {
        ("GET", "/corridas/{run_id}/flujo"): "FlujoRespuesta",
        ("GET", "/flujos/{flujo_id}"): "FlujoRespuesta",
        ("GET", "/flujos/{flujo_id}/trabajos"): "PaginaTrabajos",
        ("GET", "/trabajos/{trabajo_id}"): "TrabajoRespuesta",
        ("POST", "/flujos/{flujo_id}/reanudar"): "FlujoRespuesta",
    }
    for clave in (("GET", "/corridas/{run_id}/flujo"), ("GET", "/flujos/{flujo_id}")):
        ejemplos = operaciones[clave]["responses"]["200"]["content"]["application/json"]
        assert set(ejemplos["examples"]) == {"EN_PROCESO", "COMPLETADO", "DETENIDO"}
    del_flujo = operaciones[("GET", "/corridas/{run_id}/flujo")]["responses"]["404"]
    assert "`CORRIDA_NO_ENCONTRADA`" in del_flujo["description"]
    assert "`FLUJO_NO_ENCONTRADO`" in del_flujo["description"]
    trabajo = operaciones[("GET", "/trabajos/{trabajo_id}")]["responses"]["404"]
    assert "`TRABAJO_NO_ENCONTRADO`" in trabajo["description"]
    reanudar = operaciones[("POST", "/flujos/{flujo_id}/reanudar")]
    for codigo in ("FLUJO_EN_PROCESO", "FLUJO_YA_COMPLETADO", "FLUJO_NO_REANUDABLE"):
        assert f"`{codigo}`" in reanudar["responses"]["409"]["description"]
    assert "`FLUJO_NO_ENCONTRADO`" in reanudar["responses"]["404"]["description"]
    assert {
        "name": "orquestacion",
        "description": "El flujo automatico de cada corrida, de la ingesta al ruteo, y los "
        "trabajos de la cola durable que lo ejecutan. La API los registra; un worker los ejecuta.",
    } in api["tags"]
    assert api["info"]["version"] == __version__
    descripcion = " ".join(api["info"]["description"].split())
    assert "La API no ejecuta ningun motor." in descripcion
    assert "La entrega es al menos una vez" in descripcion
    assert "POST /flujos/{flujo_id}/reanudar" in descripcion


def test_los_esquemas_de_orquestacion_no_exponen_ids_internos_ni_el_worker(app):
    esquemas = app.openapi()["components"]["schemas"]

    assert set(esquemas["FlujoRespuesta"]["properties"]) == CAMPOS_DEL_FLUJO
    assert set(esquemas["TrabajoRespuesta"]["properties"]) == CAMPOS_DEL_TRABAJO
    assert set(esquemas["PaginaTrabajos"]["properties"]) == CAMPOS_DE_LOS_TRABAJOS
    internos = {
        "id",
        "worker_id",
        "corrida_id",
        "ejecucion_decision_id",
        "ejecucion_territorial_id",
        "ejecucion_ruteo_id",
    }
    for nombre in ("FlujoRespuesta", "TrabajoRespuesta", "PaginaTrabajos"):
        assert not set(esquemas[nombre]["properties"]) & internos
    assert esquemas["FlujoRespuesta"]["properties"]["estado"]["$ref"].endswith("/EstadoFlujo")
    assert esquemas["TrabajoRespuesta"]["properties"]["estado"]["$ref"].endswith("/EstadoTrabajo")


@pytest.mark.parametrize(
    ("modelo", "ejemplo"),
    [
        pytest.param(FlujoRespuesta, EJEMPLO_FLUJO, id="flujo-COMPLETADO"),
        pytest.param(FlujoRespuesta, EJEMPLO_FLUJO_EN_PROCESO, id="flujo-EN_PROCESO"),
        pytest.param(FlujoRespuesta, EJEMPLO_FLUJO_DETENIDO, id="flujo-DETENIDO"),
        pytest.param(TrabajoRespuesta, EJEMPLO_TRABAJO, id="trabajo-COMPLETADO"),
        pytest.param(TrabajoRespuesta, EJEMPLO_TRABAJO_PENDIENTE, id="trabajo-PENDIENTE"),
    ],
)
def test_los_ejemplos_de_orquestacion_son_respuestas_posibles(modelo, ejemplo):
    # Traen cada campo, y la API los serializaria tal cual: la duracion tambien cuadra.
    assert modelo.model_validate(ejemplo).model_dump(mode="json") == ejemplo
