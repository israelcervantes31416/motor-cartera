"""La API del Decision Engine: decidir una corrida por HTTP y consultar lo que se decidio.

Las corridas se publican por POST /corridas, como lo haria un cliente, y las decisiones se piden y
se leen por la API. Las reglas y la transaccion ya se prueban en el servicio; aqui, que la capa HTTP
traduzca bien: codigos, Location, errores, orden, paginacion y vocabulario historico. Las pruebas de
la ultima seccion no tocan la base.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pandas as pd
import pytest
from sqlalchemy import event
from sqlmodel import func, select

from motor_cartera.api import decisiones
from motor_cartera.api.esquemas import (
    EJEMPLO_DECISION_CUENTA,
    EJEMPLO_EJECUCION,
    EJEMPLO_EJECUCION_FALLIDA,
    DecisionCuentaRespuesta,
    EjecucionDecisionRespuesta,
)
from motor_cartera.db.modelos import (
    Corrida,
    Cuenta,
    DecisionCuenta,
    EjecucionDecision,
    EstadoDecision,
    ahora,
)
from motor_cartera.db.sesion import crear_motor, sesion
from motor_cartera.decision import ejecuciones
from motor_cartera.decision.ejecuciones import abrir_ejecucion, ejecutar_decision
from motor_cartera.decision.reglas import (
    VERSION_REGLAS_DECISION,
    EntradaDecision,
    ResultadoDecision,
    decidir_cuenta,
)
from motor_cartera.generador.sintetico import generar_archivo
from motor_cartera.ingesta.corridas import abrir_corrida

en_la_base = pytest.mark.usefixtures("bd")

CORTE = date(2026, 9, 30)

CAMPOS_DE_ERROR = {"codigo", "mensaje", "detalles", "run_id"}
CAMPOS_DE_EJECUCION = {
    "decision_run_id",
    "run_id",
    "version_reglas",
    "estado",
    "iniciada_en",
    "terminada_en",
    "duracion_segundos",
    "cuentas_evaluadas",
    "cuentas_decididas",
    "detalle",
}
CAMPOS_DE_DECISION = {"cliente_unico", "segmento", "prioridad", "canal_recomendado", "motivos"}

ENTRADAS = {
    # cliente: (dias de atraso, saldo), como en la cartera de conftest mas una cuenta de saldo alto
    "CU00000001": (0, "1500.50"),
    "CU00000002": (45, "23000.00"),
    "CU00000003": (190, "780.25"),
    "CU00000004": (12, "62000.00"),
}

RUTAS = [
    ("POST", "/corridas/{run_id}/decisiones"),
    ("GET", "/corridas/{run_id}/decisiones"),
    ("GET", "/decisiones/{decision_run_id}"),
    ("GET", "/decisiones/{decision_run_id}/cuentas"),
]


def _con_saldo_alto(cartera: pd.DataFrame) -> pd.DataFrame:
    """La cartera de conftest y una cuenta mas, que pasa el umbral de saldo."""
    extra = pd.DataFrame(
        {
            "cliente_unico": ["CU00000004"],
            "saldo_total": [62000.00],
            "dias_atraso": [12],
            "producto": ["AUTOMOTRIZ"],
            "canal": ["CAMPO"],
            "cve_entidad": ["15"],
            "cve_municipio": ["033"],
            "fecha_corte": pd.to_datetime(["2026-01-31"]),
        }
    )
    return pd.concat([cartera, extra], ignore_index=True)


def _csv(cartera: pd.DataFrame) -> bytes:
    return cartera.to_csv(index=False).encode("utf-8")


def _sintetica(tmp_path: Path, n: int, *, tasa: float = 0.0) -> bytes:
    ruta = generar_archivo(
        tmp_path / "cartera.csv", n=n, tasa_invalidas=tasa, semilla=1, fecha_corte=CORTE
    )
    return ruta.read_bytes()


def _subir(cliente, contenido: bytes) -> dict:
    """Sube un archivo por POST /corridas y devuelve la corrida ya procesada."""
    respuesta = cliente.post(
        "/corridas", files={"archivo": ("cartera.csv", contenido, "application/octet-stream")}
    )
    assert respuesta.status_code == 201, respuesta.text
    return cliente.get(respuesta.headers["Location"]).json()


def _publicar(cliente, contenido: bytes) -> str:
    """Publica una cartera por la API y devuelve su run_id."""
    corrida = _subir(cliente, contenido)
    assert corrida["estado"] == "EXITOSA", corrida["detalle"]
    return corrida["run_id"]


def _decidir(cliente, run_id: str):
    return cliente.post(f"/corridas/{run_id}/decisiones")


def _decidida(cliente, run_id: str) -> str:
    """Decide la corrida por la API y devuelve el decision_run_id de su ejecucion EXITOSA."""
    ejecucion = _decidir(cliente, run_id).json()
    assert ejecucion["estado"] == "EXITOSA", ejecucion
    return ejecucion["decision_run_id"]


def _id_de_corrida(run_id: str) -> int:
    with sesion() as s:
        return s.exec(select(Corrida.id).where(Corrida.run_id == UUID(run_id))).one()


def _registrar(corrida_id: int, **campos) -> EjecucionDecision:
    """Una ejecucion insertada a mano, como la dejaria el historial; por omision, FALLIDA."""
    instante = ahora()
    valores = {
        "version_reglas": VERSION_REGLAS_DECISION,
        "estado": EstadoDecision.FALLIDA,
        "iniciada_en": instante,
        "terminada_en": instante,
        "detalle": "Registrada a mano para la prueba.",
        **campos,
    }
    with sesion() as s:
        ejecucion = EjecucionDecision(corrida_id=corrida_id, **valores)
        s.add(ejecucion)
        s.commit()
        s.refresh(ejecucion)
        return ejecucion


def _ejecuciones() -> int:
    with sesion() as s:
        return s.exec(select(func.count()).select_from(EjecucionDecision)).one()


def _decisiones_en_la_tabla(decision_run_id: str) -> int:
    with sesion() as s:
        return s.exec(
            select(func.count())
            .select_from(DecisionCuenta)
            .join(EjecucionDecision, DecisionCuenta.ejecucion_decision_id == EjecucionDecision.id)
            .where(EjecucionDecision.decision_run_id == UUID(decision_run_id))
        ).one()


def _como_se_sirve(cliente_unico: str, resultado: ResultadoDecision) -> dict:
    """Una decision del nucleo, en la forma en que la API la devuelve."""
    return {
        "cliente_unico": cliente_unico,
        "segmento": resultado.segmento.value,
        "prioridad": resultado.prioridad.value,
        "canal_recomendado": resultado.canal_recomendado,
        "motivos": [
            {"codigo": motivo.codigo.value, "campo": motivo.campo, "valor": motivo.valor}
            for motivo in resultado.motivos
        ],
    }


def _revienta(_):
    raise RuntimeError("falla simulada del motor")


@contextmanager
def _sentencias() -> Iterator[list[str]]:
    """El SQL que le llega a la base mientras dura el bloque, una sentencia por ejecucion."""
    motor = crear_motor()
    vistas: list[str] = []

    def anotar(conexion, cursor, sentencia, parametros, contexto, varias):
        vistas.append(sentencia)

    event.listen(motor, "before_cursor_execute", anotar)
    try:
        yield vistas
    finally:
        event.remove(motor, "before_cursor_execute", anotar)


# --- POST /corridas/{run_id}/decisiones ---------------------------------------------------------


@en_la_base
def test_post_decide_la_corrida_y_responde_201_con_location(cliente, cartera_valida):
    # A1.
    corrida = _subir(cliente, _csv(_con_saldo_alto(cartera_valida)))
    assert corrida["estado"] == "EXITOSA"

    respuesta = _decidir(cliente, corrida["run_id"])

    assert respuesta.status_code == 201
    ejecucion = respuesta.json()
    assert set(ejecucion) == CAMPOS_DE_EJECUCION
    assert respuesta.headers["Location"] == f"/decisiones/{ejecucion['decision_run_id']}"
    assert (ejecucion["run_id"], ejecucion["version_reglas"], ejecucion["estado"]) == (
        corrida["run_id"],
        "decision/v1",
        "EXITOSA",
    )
    publicadas = corrida["filas_validas"]
    assert ejecucion["cuentas_evaluadas"] == ejecucion["cuentas_decididas"] == publicadas == 4
    assert ejecucion["detalle"] == "Se decidieron 4 cuentas con decision/v1."
    assert ejecucion["duracion_segundos"] >= 0
    assert ejecucion["iniciada_en"].endswith("Z") and ejecucion["terminada_en"].endswith("Z")
    # Location lleva al mismo recurso.
    assert cliente.get(respuesta.headers["Location"]).json() == ejecucion


@en_la_base
def test_post_con_el_motor_fallando_tambien_es_201_y_la_ejecucion_queda_fallida(
    cliente, cartera_valida, monkeypatch
):
    # A2. D1: la ejecucion se creo y su resultado es su estado; no es un error de la peticion.
    run_id = _publicar(cliente, _csv(cartera_valida))
    monkeypatch.setattr(ejecuciones, "decidir_cuenta", _revienta)

    respuesta = _decidir(cliente, run_id)

    assert respuesta.status_code == 201
    fallida = respuesta.json()
    assert respuesta.headers["Location"] == f"/decisiones/{fallida['decision_run_id']}"
    assert (fallida["estado"], fallida["cuentas_decididas"]) == ("FALLIDA", 0)
    assert fallida["detalle"] == "Error interno (RuntimeError); ver la bitacora."
    assert "falla simulada" not in respuesta.text
    assert cliente.get(respuesta.headers["Location"]).json() == fallida


@en_la_base
def test_el_post_termina_la_transaccion_de_la_busqueda_antes_de_decidir(
    cliente, cartera_valida, monkeypatch
):
    # El POST es sincrono y el motor abre sus propias transacciones. La de la sesion de la peticion
    # solo sirve para encontrar la corrida: tiene que haber terminado al entrar al motor, y no
    # volver a abrirse despues.
    run_id = _publicar(cliente, _csv(cartera_valida))
    buscar, decidir = decisiones.buscar_corrida, decisiones.decidir_corrida
    visto = {"transacciones": 0}

    def contar_transaccion(*_):
        visto["transacciones"] += 1

    def busca(s, run_id_pedido):
        event.listen(s, "after_begin", contar_transaccion)
        corrida = buscar(s, run_id_pedido)
        visto["sesion"], visto["al_buscar"] = s, s.in_transaction()
        return corrida

    def decide(corrida_id):
        visto["al_decidir"], visto["corrida_id"] = visto["sesion"].in_transaction(), corrida_id
        return decidir(corrida_id)

    monkeypatch.setattr(decisiones, "buscar_corrida", busca)
    monkeypatch.setattr(decisiones, "decidir_corrida", decide)

    respuesta = _decidir(cliente, run_id)

    assert (respuesta.status_code, respuesta.json()["estado"]) == (201, "EXITOSA")
    # La busqueda leyo dentro de una transaccion, y esa ya no estaba al entrar al motor.
    assert (visto["al_buscar"], visto["al_decidir"]) == (True, False)
    assert visto["corrida_id"] == _id_de_corrida(run_id)
    # Y la sesion de la peticion no abrio otra: nada volvio a leer la corrida despues del rollback.
    assert visto["transacciones"] == 1


@en_la_base
def test_post_otra_vez_409_decision_ya_generada_sin_location(cliente, cartera_valida):
    # A3.
    run_id = _publicar(cliente, _csv(cartera_valida))
    primera = _decidida(cliente, run_id)

    respuesta = _decidir(cliente, run_id)

    assert respuesta.status_code == 409
    error = respuesta.json()
    assert set(error) == CAMPOS_DE_ERROR
    assert (error["codigo"], error["run_id"]) == ("DECISION_YA_GENERADA", run_id)
    assert error["mensaje"] == (
        "La corrida ya tiene una ejecucion EXITOSA con decision/v1. Consulta "
        f"/corridas/{run_id}/decisiones."
    )
    assert "Location" not in respuesta.headers
    # D2: el error no dice cual ejecucion fue, ni en un campo ni en el mensaje.
    assert primera not in respuesta.text
    # La revision amable no deja ni el intento.
    assert _ejecuciones() == 1


@en_la_base
def test_post_que_pierde_la_carrera_tambien_es_409_decision_ya_generada(
    cliente, cartera_valida, monkeypatch
):
    # La carrera que no ve la revision amable: mientras esta peticion decide, otra ejecucion de la
    # misma corrida publica primero. El indice de exito rechaza el cierre de esta, que queda FALLIDA
    # en el historial, y la peticion responde lo mismo que si la revision la hubiera visto.
    run_id = _publicar(cliente, _csv(cartera_valida))
    rival = _registrar(
        _id_de_corrida(run_id), estado=EstadoDecision.EN_PROCESO, terminada_en=None, detalle=None
    )
    cerrar = ejecuciones._cerrar

    def el_rival_publica_primero(s, ejecucion, evaluadas):
        monkeypatch.setattr(ejecuciones, "_cerrar", cerrar)
        ejecutar_decision(rival.id)
        cerrar(s, ejecucion, evaluadas)

    monkeypatch.setattr(ejecuciones, "_cerrar", el_rival_publica_primero)

    respuesta = _decidir(cliente, run_id)

    assert respuesta.status_code == 409
    assert (respuesta.json()["codigo"], respuesta.json()["run_id"]) == (
        "DECISION_YA_GENERADA",
        run_id,
    )
    assert "Location" not in respuesta.headers
    assert str(rival.decision_run_id) not in respuesta.text
    historial = cliente.get(f"/corridas/{run_id}/decisiones").json()["elementos"]
    estados = {e["decision_run_id"]: (e["estado"], e["cuentas_decididas"]) for e in historial}
    assert estados.pop(str(rival.decision_run_id)) == ("EXITOSA", 3)
    assert list(estados.values()) == [("FALLIDA", 0)]  # la de esta peticion


def _corrida_rechazada(cliente, tmp_path: Path) -> str:
    return _subir(cliente, _sintetica(tmp_path, n=100, tasa=0.5))["run_id"]


def _corrida_fallida(cliente, tmp_path: Path) -> str:
    return _subir(cliente, b"cliente,saldo\nCU00000001,10\n")["run_id"]


def _corrida_en_proceso(cliente, tmp_path: Path) -> str:
    with sesion() as s:
        corrida = abrir_corrida(s, origen="cartera.csv", contenido=_sintetica(tmp_path, n=10))
    return str(corrida.run_id)


@en_la_base
@pytest.mark.parametrize(
    ("crear", "estado"),
    [
        pytest.param(_corrida_rechazada, "RECHAZADA", id="RECHAZADA"),
        pytest.param(_corrida_fallida, "FALLIDA", id="FALLIDA"),
        pytest.param(_corrida_en_proceso, "EN_PROCESO", id="EN_PROCESO"),
    ],
)
def test_post_sobre_una_corrida_que_no_publico_409_sin_ejecucion(cliente, tmp_path, crear, estado):
    # A4.
    run_id = crear(cliente, tmp_path)

    respuesta = _decidir(cliente, run_id)

    assert respuesta.status_code == 409
    error = respuesta.json()
    assert set(error) == CAMPOS_DE_ERROR
    assert (error["codigo"], error["run_id"]) == ("CORRIDA_NO_PUBLICADA", run_id)
    assert error["mensaje"] == (
        f"La corrida esta {estado} y no publico una cartera; solo una corrida EXITOSA puede "
        "decidirse."
    )
    assert _ejecuciones() == 0


@en_la_base
def test_post_sobre_una_corrida_que_no_existe_404(cliente):
    # A5.
    run_id = str(uuid4())

    respuesta = _decidir(cliente, run_id)

    assert respuesta.status_code == 404
    assert set(respuesta.json()) == CAMPOS_DE_ERROR
    assert (respuesta.json()["codigo"], respuesta.json()["run_id"]) == (
        "CORRIDA_NO_ENCONTRADA",
        run_id,
    )
    assert _ejecuciones() == 0


@en_la_base
def test_d2_el_409_no_dice_cual_ejecucion_y_el_historial_si(cliente, cartera_valida):
    # D2 de punta a punta: decidir, chocar con lo ya publicado y encontrarlo en el historial.
    run_id = _publicar(cliente, _csv(cartera_valida))
    primera = _decidir(cliente, run_id)
    assert (primera.status_code, primera.json()["estado"]) == (201, "EXITOSA")

    otra = _decidir(cliente, run_id)
    assert (otra.status_code, otra.json()["codigo"]) == (409, "DECISION_YA_GENERADA")
    assert "Location" not in otra.headers

    historial = cliente.get(f"/corridas/{run_id}/decisiones").json()["elementos"]
    (publicada,) = [ejecucion for ejecucion in historial if ejecucion["estado"] == "EXITOSA"]
    assert publicada["version_reglas"] == "decision/v1"
    assert publicada["decision_run_id"] == primera.json()["decision_run_id"]
    assert cliente.get(f"/decisiones/{publicada['decision_run_id']}").json() == primera.json()


# --- GET /corridas/{run_id}/decisiones ----------------------------------------------------------


@en_la_base
def test_el_historial_de_una_corrida_sin_ejecuciones_es_una_lista_vacia(
    cliente, cartera_valida, tmp_path
):
    # A6. Tambien de una corrida que no publico: el historial no exige que este EXITOSA.
    publicada = _publicar(cliente, _csv(cartera_valida))
    rechazada = _corrida_rechazada(cliente, tmp_path)

    for run_id in (publicada, rechazada):
        respuesta = cliente.get(f"/corridas/{run_id}/decisiones")
        assert respuesta.status_code == 200
        assert respuesta.json() == {
            "run_id": run_id,
            "total": 0,
            "pagina": 1,
            "por_pagina": 50,
            "elementos": [],
        }

    no_existe = cliente.get(f"/corridas/{uuid4()}/decisiones")
    assert (no_existe.status_code, no_existe.json()["codigo"]) == (404, "CORRIDA_NO_ENCONTRADA")


@en_la_base
def test_el_historial_trae_todas_las_ejecuciones_en_cualquier_estado_y_version(
    cliente, cartera_valida, monkeypatch
):
    # A7. Una FALLIDA y una EXITOSA de decision/v1, por la API, y una FALLIDA de otra version.
    run_id = _publicar(cliente, _csv(cartera_valida))
    monkeypatch.setattr(ejecuciones, "decidir_cuenta", _revienta)
    fallida = _decidir(cliente, run_id).json()
    monkeypatch.setattr(ejecuciones, "decidir_cuenta", decidir_cuenta)
    exitosa = _decidir(cliente, run_id).json()
    otra_version = _registrar(_id_de_corrida(run_id), version_reglas="decision/v2")

    respuesta = cliente.get(f"/corridas/{run_id}/decisiones")

    assert respuesta.status_code == 200
    historial = respuesta.json()
    assert (historial["run_id"], historial["total"]) == (run_id, 3)
    for ejecucion in historial["elementos"]:
        # Lo publico de cada ejecucion, sin su id ni el de su corrida.
        assert set(ejecucion) == CAMPOS_DE_EJECUCION
        assert ejecucion["run_id"] == run_id
    por_id = {ejecucion["decision_run_id"]: ejecucion for ejecucion in historial["elementos"]}
    assert por_id[fallida["decision_run_id"]] == fallida
    assert por_id[exitosa["decision_run_id"]] == exitosa
    historica = por_id[str(otra_version.decision_run_id)]
    assert (historica["version_reglas"], historica["estado"]) == ("decision/v2", "FALLIDA")
    assert historica["detalle"] == "Registrada a mano para la prueba."


@en_la_base
def test_el_historial_va_de_la_mas_reciente_a_la_mas_antigua(cliente, cartera_valida):
    # A8. Fuera del orden en que se insertan, y con un empate en el instante, que desempata el id.
    run_id = _publicar(cliente, _csv(cartera_valida))
    corrida_id = _id_de_corrida(run_id)
    mediodia = datetime(2026, 9, 30, 12, tzinfo=UTC)

    def iniciada(instante: datetime) -> EjecucionDecision:
        return _registrar(
            corrida_id, iniciada_en=instante, terminada_en=instante + timedelta(minutes=1)
        )

    diez = iniciada(mediodia - timedelta(hours=2))
    primera_de_mediodia = iniciada(mediodia)
    segunda_de_mediodia = iniciada(mediodia)
    nueve = iniciada(mediodia - timedelta(hours=3))  # la mas antigua, insertada al final

    elementos = cliente.get(f"/corridas/{run_id}/decisiones").json()["elementos"]

    assert [e["decision_run_id"] for e in elementos] == [
        str(e.decision_run_id) for e in (segunda_de_mediodia, primera_de_mediodia, diez, nueve)
    ]


@en_la_base
def test_el_historial_se_pagina_sin_huecos_ni_repetidos(cliente, cartera_valida):
    # A9.
    run_id = _publicar(cliente, _csv(cartera_valida))
    corrida_id = _id_de_corrida(run_id)
    for _ in range(7):
        _registrar(corrida_id)
    ruta = f"/corridas/{run_id}/decisiones"
    todas = [e["decision_run_id"] for e in cliente.get(ruta).json()["elementos"]]

    paginas = [
        cliente.get(ruta, params={"pagina": p, "por_pagina": 3}).json() for p in (1, 2, 3, 4)
    ]

    assert [len(p["elementos"]) for p in paginas] == [3, 3, 1, 0]
    assert {p["total"] for p in paginas} == {7}
    assert [e["decision_run_id"] for p in paginas for e in p["elementos"]] == todas
    assert len(set(todas)) == 7


# --- GET /decisiones/{decision_run_id} ----------------------------------------------------------


@en_la_base
def test_una_ejecucion_se_consulta_por_su_decision_run_id(cliente, cartera_valida):
    # A10.
    run_id = _publicar(cliente, _csv(cartera_valida))
    decision_run_id = _decidida(cliente, run_id)

    respuesta = cliente.get(f"/decisiones/{decision_run_id}")

    assert respuesta.status_code == 200
    with sesion() as s:
        guardada = s.exec(
            select(EjecucionDecision).where(
                EjecucionDecision.decision_run_id == UUID(decision_run_id)
            )
        ).one()
    ejecucion = respuesta.json()
    assert set(ejecucion) == CAMPOS_DE_EJECUCION
    assert (ejecucion["decision_run_id"], ejecucion["run_id"]) == (decision_run_id, run_id)
    assert (ejecucion["version_reglas"], ejecucion["estado"], ejecucion["detalle"]) == (
        guardada.version_reglas,
        guardada.estado,
        guardada.detalle,
    )
    assert (ejecucion["cuentas_evaluadas"], ejecucion["cuentas_decididas"]) == (
        guardada.cuentas_evaluadas,
        guardada.cuentas_decididas,
    )
    assert datetime.fromisoformat(ejecucion["iniciada_en"]) == guardada.iniciada_en
    assert datetime.fromisoformat(ejecucion["terminada_en"]) == guardada.terminada_en


@en_la_base
@pytest.mark.parametrize("ruta", ["/decisiones/{}", "/decisiones/{}/cuentas"])
def test_una_ejecucion_que_no_existe_404_sin_run_id(cliente, ruta):
    # A10. No hay corrida que nombrar.
    respuesta = cliente.get(ruta.format(uuid4()))

    assert respuesta.status_code == 404
    assert set(respuesta.json()) == CAMPOS_DE_ERROR
    assert (respuesta.json()["codigo"], respuesta.json()["run_id"]) == (
        "DECISION_NO_ENCONTRADA",
        None,
    )


# --- GET /decisiones/{decision_run_id}/cuentas -------------------------------------------------


@en_la_base
def test_las_cuentas_de_una_ejecucion_exitosa(cliente, tmp_path):
    # A11.
    run_id = _publicar(cliente, _sintetica(tmp_path, n=60))
    ejecucion = _decidir(cliente, run_id).json()

    respuesta = cliente.get(f"/decisiones/{ejecucion['decision_run_id']}/cuentas")

    assert respuesta.status_code == 200
    pagina = respuesta.json()
    assert (pagina["decision_run_id"], pagina["run_id"]) == (ejecucion["decision_run_id"], run_id)
    assert (pagina["version_reglas"], pagina["estado"]) == ("decision/v1", "EXITOSA")
    en_la_tabla = _decisiones_en_la_tabla(ejecucion["decision_run_id"])
    assert pagina["total"] == ejecucion["cuentas_decididas"] == en_la_tabla == 60
    assert len(pagina["elementos"]) == 50  # la primera pagina, de 50 por omision
    # Sin el id de la decision, el de su ejecucion ni el de su cuenta.
    for decision in pagina["elementos"]:
        assert set(decision) == CAMPOS_DE_DECISION


@en_la_base
def test_el_total_de_cuentas_sale_de_la_tabla_y_no_del_contador(cliente, cartera_valida):
    # El contador se descompone a proposito: el total tiene que seguir siendo lo que hay guardado.
    run_id = _publicar(cliente, _csv(cartera_valida))
    decision_run_id = _decidida(cliente, run_id)
    with sesion() as s:
        ejecucion = s.exec(
            select(EjecucionDecision).where(
                EjecucionDecision.decision_run_id == UUID(decision_run_id)
            )
        ).one()
        ejecucion.cuentas_decididas = 999
        s.add(ejecucion)
        s.commit()

    pagina = cliente.get(f"/decisiones/{decision_run_id}/cuentas").json()

    assert pagina["total"] == len(pagina["elementos"]) == 3


@en_la_base
def test_las_cuentas_traen_exactamente_lo_que_decide_el_nucleo(cliente, cartera_valida):
    # A12. La API no vuelve a decidir: esto valida lo que se guardo y como se sirve.
    run_id = _publicar(cliente, _csv(_con_saldo_alto(cartera_valida)))
    decision_run_id = _decidida(cliente, run_id)

    elementos = cliente.get(f"/decisiones/{decision_run_id}/cuentas").json()["elementos"]

    assert elementos == [
        _como_se_sirve(
            cliente_unico,
            decidir_cuenta(EntradaDecision(dias_atraso=dias, saldo_total=Decimal(saldo))),
        )
        for cliente_unico, (dias, saldo) in sorted(ENTRADAS.items())
    ]


@en_la_base
def test_la_api_da_el_canal_recomendado_y_no_toca_el_de_la_cartera(cliente, cartera_valida):
    # A13. CU00000001 llega a la cartera por TELEFONICA; sin atraso, el motor recomienda DIGITAL.
    run_id = _publicar(cliente, _csv(cartera_valida))
    decision_run_id = _decidida(cliente, run_id)

    primera = cliente.get(f"/decisiones/{decision_run_id}/cuentas").json()["elementos"][0]

    assert (primera["cliente_unico"], primera["canal_recomendado"]) == ("CU00000001", "DIGITAL")
    assert "canal" not in primera  # el de la cartera no se copia a la decision
    with sesion() as s:
        canal = s.exec(select(Cuenta.canal).where(Cuenta.cliente_unico == "CU00000001")).one()
    assert canal == "TELEFONICA"


@en_la_base
def test_los_motivos_viajan_como_codigo_campo_y_valor_en_texto(cliente, cartera_valida):
    # A14.
    run_id = _publicar(cliente, _csv(_con_saldo_alto(cartera_valida)))
    decision_run_id = _decidida(cliente, run_id)

    respuesta = cliente.get(f"/decisiones/{decision_run_id}/cuentas")

    motivos = [motivo for d in respuesta.json()["elementos"] for motivo in d["motivos"]]
    assert motivos
    assert all(set(motivo) == {"codigo", "campo", "valor"} for motivo in motivos)
    assert all(type(valor) is str for motivo in motivos for valor in motivo.values())
    assert {"codigo": "SALDO_ALTO", "campo": "saldo_total", "valor": "62000.00"} in motivos
    # Ni la representacion de un enum de Python.
    assert not re.search(r"CodigoMotivo|SegmentoMora|Prioridad\.", respuesta.text)


@en_la_base
def test_las_cuentas_se_ordenan_por_cliente_y_se_paginan_sin_huecos_ni_repetidos(cliente, tmp_path):
    # A15.
    run_id = _publicar(cliente, _sintetica(tmp_path, n=120))
    ruta = f"/decisiones/{_decidida(cliente, run_id)}/cuentas"

    paginas = [
        cliente.get(ruta, params={"pagina": p, "por_pagina": 50}).json() for p in (1, 2, 3, 4)
    ]

    assert [len(p["elementos"]) for p in paginas] == [50, 50, 20, 0]
    assert {p["total"] for p in paginas} == {120}
    clientes = [decision["cliente_unico"] for p in paginas for decision in p["elementos"]]
    assert clientes == sorted(clientes)
    with sesion() as s:
        publicados = s.exec(select(Cuenta.cliente_unico)).all()
    assert sorted(clientes) == sorted(publicados)  # todas, una vez cada una


def _ejecucion_en_proceso(cliente, run_id: str, monkeypatch) -> str:
    # Abierta por el servicio y todavia sin decidir: como la veria otra peticion mientras decide.
    with sesion() as s:
        corrida = s.exec(select(Corrida).where(Corrida.run_id == UUID(run_id))).one()
        return str(abrir_ejecucion(s, corrida).decision_run_id)


def _ejecucion_fallida(cliente, run_id: str, monkeypatch) -> str:
    monkeypatch.setattr(ejecuciones, "decidir_cuenta", _revienta)
    return _decidir(cliente, run_id).json()["decision_run_id"]


@en_la_base
@pytest.mark.parametrize(
    ("crear", "estado"),
    [
        pytest.param(_ejecucion_en_proceso, "EN_PROCESO", id="EN_PROCESO"),
        pytest.param(_ejecucion_fallida, "FALLIDA", id="FALLIDA"),
    ],
)
def test_las_cuentas_de_una_ejecucion_que_no_publico_409(
    cliente, cartera_valida, monkeypatch, crear, estado
):
    # A16. No una lista vacia: diria que no habia cuentas.
    run_id = _publicar(cliente, _csv(cartera_valida))
    decision_run_id = crear(cliente, run_id, monkeypatch)

    respuesta = cliente.get(f"/decisiones/{decision_run_id}/cuentas")

    assert respuesta.status_code == 409
    error = respuesta.json()
    assert set(error) == CAMPOS_DE_ERROR
    assert (error["codigo"], error["run_id"]) == ("DECISION_NO_PUBLICADA", run_id)
    assert error["mensaje"] == (
        f"La ejecucion esta {estado} y no publico decisiones; solo una ejecucion EXITOSA tiene "
        "decisiones por cuenta."
    )


@en_la_base
def test_una_decision_historica_con_otro_vocabulario_se_sigue_sirviendo(cliente, cartera_valida):
    # A17. Lo que una version futura podria haber guardado, con valores que decision/v1 no conoce.
    # No se ejecuta ninguna regla: solo se lee el historial.
    run_id = _publicar(cliente, _csv(cartera_valida))
    corrida_id = _id_de_corrida(run_id)
    historica = _registrar(
        corrida_id,
        version_reglas="decision/v2",
        estado=EstadoDecision.EXITOSA,
        cuentas_evaluadas=1,
        cuentas_decididas=1,
        detalle="Se decidieron 1 cuentas con decision/v2.",
    )
    futura = {
        "segmento": "SEGMENTO_FUTURO",
        "prioridad": "CRITICA",
        "canal_recomendado": "ASISTIDO",
        "motivos": [{"codigo": "REGLA_FUTURA", "campo": "saldo_total", "valor": "23000.00"}],
    }
    with sesion() as s:
        cuenta_id = s.exec(
            select(Cuenta.id).where(
                Cuenta.corrida_id == corrida_id, Cuenta.cliente_unico == "CU00000002"
            )
        ).one()
        s.add(DecisionCuenta(ejecucion_decision_id=historica.id, cuenta_id=cuenta_id, **futura))
        s.commit()

    respuesta = cliente.get(f"/decisiones/{historica.decision_run_id}/cuentas")

    assert respuesta.status_code == 200
    assert (respuesta.json()["version_reglas"], respuesta.json()["total"]) == ("decision/v2", 1)
    assert respuesta.json()["elementos"] == [{"cliente_unico": "CU00000002", **futura}]
    # La ejecucion y el historial tambien la sirven.
    ejecucion = cliente.get(f"/decisiones/{historica.decision_run_id}").json()
    assert (ejecucion["version_reglas"], ejecucion["estado"]) == ("decision/v2", "EXITOSA")
    assert cliente.get(f"/corridas/{run_id}/decisiones").json()["elementos"] == [ejecucion]


@en_la_base
@pytest.mark.parametrize("por_pagina", [10, 100])
def test_una_pagina_de_cuentas_sale_de_un_join_y_no_de_una_consulta_por_cuenta(
    cliente, tmp_path, por_pagina
):
    # A22.
    run_id = _publicar(cliente, _sintetica(tmp_path, n=120))
    decision_run_id = _decidida(cliente, run_id)

    with _sentencias() as sentencias:
        pagina = cliente.get(
            f"/decisiones/{decision_run_id}/cuentas", params={"por_pagina": por_pagina}
        ).json()

    assert len(pagina["elementos"]) == por_pagina
    # La ejecucion, el total y la pagina: tres consultas, sean 10 o 100 cuentas. Y solo la pagina
    # lee la tabla de cuentas, con un JOIN.
    assert len(sentencias) == 3
    (de_cuentas,) = [x for x in sentencias if re.search(r"\b(FROM|JOIN) cuenta\b", x)]
    assert re.search(r"\bJOIN cuenta\b", de_cuentas)


# --- sin base de datos -------------------------------------------------------------------------


@pytest.mark.parametrize(("metodo", "ruta"), RUTAS)
def test_las_rutas_de_decisiones_piden_api_key(cliente, metodo, ruta):
    # A18. La clave se revisa antes de buscar nada.
    url = ruta.format(run_id=uuid4(), decision_run_id=uuid4())

    respuesta = cliente.request(metodo, url, headers={"X-API-Key": ""})

    assert respuesta.status_code == 401
    assert set(respuesta.json()) == CAMPOS_DE_ERROR
    assert respuesta.json()["codigo"] == "API_KEY_AUSENTE"


@pytest.mark.parametrize(
    ("metodo", "ruta", "campo"),
    [
        ("POST", "/corridas/123/decisiones", "path.run_id"),
        ("GET", "/corridas/123/decisiones", "path.run_id"),
        ("GET", "/decisiones/123", "path.decision_run_id"),
        ("GET", "/decisiones/123/cuentas", "path.decision_run_id"),
    ],
)
def test_un_identificador_que_no_es_uuid_422(cliente, metodo, ruta, campo):
    # A10.
    respuesta = cliente.request(metodo, ruta)

    assert respuesta.status_code == 422
    assert respuesta.json()["codigo"] == "ENTRADA_INVALIDA"
    assert [detalle["campo"] for detalle in respuesta.json()["detalles"]] == [campo]


@pytest.mark.parametrize("ruta", ["/corridas/{}/decisiones", "/decisiones/{}/cuentas"])
@pytest.mark.parametrize(
    ("parametros", "campo"),
    [({"pagina": 0}, "query.pagina"), ({"por_pagina": 501}, "query.por_pagina")],
)
def test_paginacion_fuera_de_rango_422(cliente, ruta, parametros, campo):
    # A19.
    respuesta = cliente.get(ruta.format(uuid4()), params=parametros)

    assert respuesta.status_code == 422
    assert respuesta.json()["codigo"] == "ENTRADA_INVALIDA"
    assert [detalle["campo"] for detalle in respuesta.json()["detalles"]] == [campo]


def test_openapi_documenta_las_cuatro_operaciones_de_decisiones(app):
    # A20 y A21.
    api = app.openapi()
    operaciones = {
        (metodo.upper(), ruta): operacion
        for ruta, metodos in api["paths"].items()
        for metodo, operacion in metodos.items()
        if "decisiones" in operacion.get("tags", [])
    }

    assert {clave: set(op["responses"]) for clave, op in operaciones.items()} == {
        ("POST", "/corridas/{run_id}/decisiones"): {"201", "401", "404", "409", "422"},
        ("GET", "/corridas/{run_id}/decisiones"): {"200", "401", "404", "422"},
        ("GET", "/decisiones/{decision_run_id}"): {"200", "401", "404", "422"},
        ("GET", "/decisiones/{decision_run_id}/cuentas"): {"200", "401", "404", "409", "422"},
    }
    for operacion in operaciones.values():
        assert operacion["tags"] == ["decisiones"]
        assert operacion["summary"] and operacion["description"]
        # Todo error, con el mismo esquema que el resto de la API.
        for codigo, respuesta in operacion["responses"].items():
            if codigo[0] in "45":
                esquema = respuesta["content"]["application/json"]["schema"]
                assert esquema["$ref"].endswith("/ErrorRespuesta")

    # 201 dice que la ejecucion se creo, no que el motor tuviera exito: documenta las dos salidas.
    post = operaciones[("POST", "/corridas/{run_id}/decisiones")]
    creada = post["responses"]["201"]
    assert "FALLIDA" in creada["description"] and "FALLIDA" in post["description"]
    contenido = creada["content"]["application/json"]
    assert contenido["schema"]["$ref"].endswith("/EjecucionDecisionRespuesta")
    assert set(contenido["examples"]) == {"EXITOSA", "FALLIDA"}
    assert "`CORRIDA_NO_ENCONTRADA`" in post["responses"]["404"]["description"]
    assert "`CORRIDA_NO_PUBLICADA`" in post["responses"]["409"]["description"]
    assert "`DECISION_YA_GENERADA`" in post["responses"]["409"]["description"]
    cuentas = operaciones[("GET", "/decisiones/{decision_run_id}/cuentas")]
    assert "`DECISION_NO_ENCONTRADA`" in cuentas["responses"]["404"]["description"]
    assert "`DECISION_NO_PUBLICADA`" in cuentas["responses"]["409"]["description"]
    assert {
        "name": "decisiones",
        "description": "Ejecuciones versionadas del Decision Engine y sus decisiones por cuenta.",
    } in api["tags"]


def test_los_esquemas_de_decisiones_no_atan_el_vocabulario_a_decision_v1(app):
    esquemas = app.openapi()["components"]["schemas"]

    # Los campos exactos de cada respuesta, sin ids internos.
    assert set(esquemas["EjecucionDecisionRespuesta"]["properties"]) == CAMPOS_DE_EJECUCION
    assert set(esquemas["DecisionCuentaRespuesta"]["properties"]) == CAMPOS_DE_DECISION
    assert set(esquemas["MotivoDecisionRespuesta"]["properties"]) == {"codigo", "campo", "valor"}
    assert set(esquemas["PaginaEjecucionesDecision"]["properties"]) == {
        "run_id",
        "total",
        "pagina",
        "por_pagina",
        "elementos",
    }
    assert set(esquemas["PaginaDecisionCuentas"]["properties"]) == {
        "decision_run_id",
        "run_id",
        "version_reglas",
        "estado",
        "total",
        "pagina",
        "por_pagina",
        "elementos",
    }
    # El vocabulario de una decision es texto, sin el catalogo de decision/v1: la API sirve el
    # historial de cualquier version.
    for esquema, campo in [
        ("DecisionCuentaRespuesta", "segmento"),
        ("DecisionCuentaRespuesta", "prioridad"),
        ("DecisionCuentaRespuesta", "canal_recomendado"),
        ("MotivoDecisionRespuesta", "codigo"),
    ]:
        propiedad = esquemas[esquema]["properties"][campo]
        assert propiedad["type"] == "string"
        assert "enum" not in propiedad and "$ref" not in propiedad
    # El estado de la ejecucion si es un catalogo, el del modelo. Y el error es el de siempre.
    estado = esquemas["EjecucionDecisionRespuesta"]["properties"]["estado"]
    assert estado["$ref"].endswith("/EstadoDecision")
    assert set(esquemas["ErrorRespuesta"]["properties"]) == CAMPOS_DE_ERROR


def test_los_ejemplos_de_la_documentacion_son_respuestas_posibles():
    # Traen cada campo de la respuesta; el OpenAPI omite los que valen null.
    campos = set(EjecucionDecisionRespuesta.model_json_schema(mode="serialization")["properties"])
    assert set(EJEMPLO_EJECUCION) == set(EJEMPLO_EJECUCION_FALLIDA) == campos
    fallida = EJEMPLO_EJECUCION_FALLIDA
    assert (fallida["estado"], fallida["cuentas_decididas"]) == ("FALLIDA", 0)
    # Y la cuenta del ejemplo es lo que decision/v1 decide de verdad para esa entrada.
    resultado = decidir_cuenta(EntradaDecision(dias_atraso=65, saldo_total=Decimal("62000.00")))
    ejemplo = DecisionCuentaRespuesta(**EJEMPLO_DECISION_CUENTA).model_dump()
    assert ejemplo == _como_se_sirve(EJEMPLO_DECISION_CUENTA["cliente_unico"], resultado)
