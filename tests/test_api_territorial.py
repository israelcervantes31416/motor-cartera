"""La API del Motor Territorial: pedir que se organice por municipio una ejecucion de decision por
HTTP y consultar lo que se publico.

El POST no organiza: deja la ejecucion EN_PROCESO con su trabajo en la cola durable y responde 201.
Las pruebas hacen lo que haria el worker y despues leen el resultado por la API. Las carteras se
publican en modo directo, sin flujo, y se deciden por POST /corridas/{run_id}/decisiones: asi la
organizacion se pide a mano. Las de un flujo se prueban aparte. La agregacion, las reglas y la
transaccion ya se prueban en el servicio; aqui, que la capa HTTP traduzca bien: codigos, Location,
errores, orden, paginacion y vocabulario historico. Las pruebas de la ultima seccion no tocan la
base.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pandas as pd
import pytest
from sqlalchemy import event, update
from sqlmodel import func, select

from motor_cartera.api.esquemas import (
    EJEMPLO_EJECUCION_TERRITORIAL,
    EJEMPLO_EJECUCION_TERRITORIAL_EN_PROCESO,
    EJEMPLO_EJECUCION_TERRITORIAL_FALLIDA,
    EJEMPLO_MUNICIPIO,
    EjecucionTerritorialRespuesta,
    ResultadoTerritorialRespuesta,
)
from motor_cartera.config import Config
from motor_cartera.db.modelos import (
    Corrida,
    EjecucionDecision,
    EjecucionTerritorial,
    EstadoDecision,
    EstadoTerritorial,
    EstadoTrabajo,
    ResultadoTerritorial,
    TipoTrabajo,
    TrabajoOrquestacion,
    ahora,
)
from motor_cartera.db.sesion import crear_motor, sesion
from motor_cartera.decision import ejecuciones as ejecuciones_de_decision
from motor_cartera.ingesta.corridas import abrir_corrida, procesar_corrida
from motor_cartera.orquestacion.worker import identificador_worker, procesar_un_trabajo
from motor_cartera.territorial import ejecuciones
from motor_cartera.territorial.reglas import (
    VERSION_REGLAS_TERRITORIAL,
    EntradaTerritorio,
    ResultadoTerritorio,
    priorizar_territorios,
)

en_la_base = pytest.mark.usefixtures("bd")

CORTE = "2026-09-30"

CAMPOS_DE_ERROR = {"codigo", "mensaje", "detalles", "run_id"}
CAMPOS_DE_EJECUCION = {
    "territorial_run_id",
    "decision_run_id",
    "run_id",
    "version_reglas",
    "estado",
    "iniciada_en",
    "terminada_en",
    "duracion_segundos",
    "territorios_evaluados",
    "territorios_publicados",
    "detalle",
}
CAMPOS_DE_MUNICIPIO = {
    "clave_territorio",
    "cve_entidad",
    "cve_municipio",
    "cuentas_total",
    "saldo_total",
    "cuentas_campo",
    "saldo_campo",
    "carga",
    "posicion_campo",
    "motivos",
}
CAMPOS_DEL_HISTORIAL = {"decision_run_id", "run_id", "total", "pagina", "por_pagina", "elementos"}
CAMPOS_DE_LOS_MUNICIPIOS = {
    "territorial_run_id",
    "decision_run_id",
    "run_id",
    "version_reglas",
    "estado",
    "total",
    "pagina",
    "por_pagina",
    "elementos",
}

RUTAS = [
    ("POST", "/decisiones/{decision_run_id}/territoriales"),
    ("GET", "/decisiones/{decision_run_id}/territoriales"),
    ("GET", "/territoriales/{territorial_run_id}"),
    ("GET", "/territoriales/{territorial_run_id}/municipios"),
]

Cuentas = list[tuple[str, int, str, str]]
"""Una cartera de prueba: por cuenta, (clave del municipio, dias de atraso, saldo, canal de la
cartera). El canal recomendado lo decide decision/v1 con los dias y el saldo: CAMPO con 91 dias o
mas, o de 31 a 90 con saldo desde 50,000.00; si no, TELEFONICA o DIGITAL."""

# Ocho municipios de tres entidades, la misma cartera de las pruebas del servicio. A la derecha, el
# canal que decision/v1 recomienda; la cuarta columna, el canal de la cartera, no cuenta.
CARTERA: Cuentas = [
    *[("21156", 100, "1000.00", "CAMPO")] * 20,  # CAMPO, 20 veces
    ("21156", 0, "500.00", "TELEFONICA"),  # DIGITAL
    *[("21114", 150, "2000.00", "DIGITAL")] * 5,  # CAMPO, 5 veces
    ("21114", 10, "1000.00", "CAMPO"),  # DIGITAL
    ("21114", 40, "3000.00", "CAMPO"),  # TELEFONICA
    ("21208", 95, "30000.00", "TELEFONICA"),  # CAMPO
    ("21208", 45, "60000.00", "TELEFONICA"),  # CAMPO, por el saldo
    ("21208", 300, "10000.00", "DIGITAL"),  # CAMPO
    ("21208", 60, "2000.00", "CAMPO"),  # TELEFONICA
    ("09005", 91, "1000.00", "DIGITAL"),  # CAMPO
    ("09005", 92, "1000.00", "DIGITAL"),  # CAMPO
    ("09005", 93, "1000.00", "DIGITAL"),  # CAMPO
    ("09005", 0, "9999.00", "CAMPO"),  # DIGITAL
    ("15033", 120, "5000.00", "TELEFONICA"),  # CAMPO
    ("15033", 5, "100.00", "CAMPO"),  # DIGITAL
    ("21001", 120, "5000.00", "DIGITAL"),  # CAMPO
    ("21001", 0, "7000.00", "CAMPO"),  # DIGITAL
    ("21001", 0, "8000.00", "CAMPO"),  # DIGITAL
    ("21099", 20, "4000.00", "CAMPO"),  # DIGITAL
    ("21099", 70, "3000.00", "CAMPO"),  # TELEFONICA
    ("09002", 0, "100000.00", "CAMPO"),  # DIGITAL
]


def _municipio(
    clave: str,
    cuentas: tuple[int, str],
    campo: tuple[int, str],
    carga: str,
    lugar: int | None,
    codigo: str,
) -> dict:
    """Un municipio como lo sirve la API: los saldos en texto, con sus dos decimales."""
    (cuentas_total, saldo_total), (cuentas_campo, saldo_campo) = cuentas, campo
    return {
        "clave_territorio": clave,
        "cve_entidad": clave[:2],
        "cve_municipio": clave[2:],
        "cuentas_total": cuentas_total,
        "saldo_total": saldo_total,
        "cuentas_campo": cuentas_campo,
        "saldo_campo": saldo_campo,
        "carga": carga,
        "posicion_campo": lugar,
        "motivos": [{"codigo": codigo, "campo": "cuentas_campo", "valor": str(cuentas_campo)}],
    }


# Lo que territorial/v1 publica de CARTERA, en el orden en que la API lo sirve.
MUNICIPIOS = [
    _municipio("21156", (21, "20500.00"), (20, "20000.00"), "ALTA", 1, "CARGA_CAMPO_20_MAS"),
    _municipio("21114", (7, "14000.00"), (5, "10000.00"), "MEDIA", 2, "CARGA_CAMPO_5_19"),
    # Empatan en cuentas de campo: va primero el de mas saldo de campo, aunque su clave sea mayor.
    _municipio("21208", (4, "102000.00"), (3, "100000.00"), "BAJA", 3, "CARGA_CAMPO_1_4"),
    _municipio("09005", (4, "12999.00"), (3, "3000.00"), "BAJA", 4, "CARGA_CAMPO_1_4"),
    # Empatan en cuentas y saldo de campo: va primero la clave menor.
    _municipio("15033", (2, "5100.00"), (1, "5000.00"), "BAJA", 5, "CARGA_CAMPO_1_4"),
    _municipio("21001", (3, "20000.00"), (1, "5000.00"), "BAJA", 6, "CARGA_CAMPO_1_4"),
    # Los SIN_CARGA al final, por clave y sin lugar.
    _municipio("09002", (1, "100000.00"), (0, "0.00"), "SIN_CARGA", None, "SIN_CARGA_CAMPO"),
    _municipio("21099", (2, "7000.00"), (0, "0.00"), "SIN_CARGA", None, "SIN_CARGA_CAMPO"),
]

# Tres cuentas en dos municipios, los dos con carga: 21001 va primero, con mas saldo de campo.
PEQUENA: Cuentas = [
    ("21001", 120, "3000.00", "DIGITAL"),  # CAMPO
    ("21001", 0, "7000.00", "CAMPO"),  # DIGITAL
    ("09002", 95, "1500.00", "CAMPO"),  # CAMPO
]


def _un_municipio_por_cuenta(n: int) -> Cuentas:
    """n cuentas, cada una en su propio municipio de 09 o de 21. Dos de cada tres son de campo, y
    cada una tiene otro saldo, para que ningun lugar se decida por la clave."""
    cuentas: Cuentas = []
    for i in range(n):
        clave = ("09", "21")[i % 2] + f"{i // 2 + 1:03d}"
        cuentas.append((clave, 120 if i % 3 else 0, f"{1000 + i}.00", "DIGITAL"))
    return cuentas


def _csv(cuentas: Cuentas) -> bytes:
    cartera = pd.DataFrame(
        {
            "cliente_unico": [f"CU{numero:08d}" for numero in range(1, len(cuentas) + 1)],
            "saldo_total": [saldo for _, _, saldo, _ in cuentas],
            "dias_atraso": [dias for _, dias, _, _ in cuentas],
            "producto": ["CONSUMO"] * len(cuentas),
            "canal": [canal for _, _, _, canal in cuentas],
            "cve_entidad": [clave[:2] for clave, _, _, _ in cuentas],
            "cve_municipio": [clave[2:] for clave, _, _, _ in cuentas],
            "fecha_corte": [CORTE] * len(cuentas),
        }
    )
    return cartera.to_csv(index=False).encode("utf-8")


def _trabajar() -> None:
    """Lo que haria el worker: procesa la cola hasta que no quede ningun trabajo que tomar."""
    worker_id = identificador_worker()
    while procesar_un_trabajo(worker_id, Config()) is not None:
        pass


def _publicar(cliente, cuentas: Cuentas) -> str:
    """Publica la cartera en modo directo, sin flujo, y devuelve el run_id de la corrida EXITOSA."""
    contenido = _csv(cuentas)
    with sesion() as s:
        corrida = abrir_corrida(s, origen="cartera.csv", contenido=contenido)
    procesar_corrida(corrida.id, contenido)
    publicada = cliente.get(f"/corridas/{corrida.run_id}").json()
    assert publicada["estado"] == "EXITOSA", publicada["detalle"]
    return publicada["run_id"]


def _decidir(cliente, run_id: str) -> dict:
    """Pide la decision por la API, deja trabajar al worker y devuelve la ejecucion como quedo."""
    respuesta = cliente.post(f"/corridas/{run_id}/decisiones")
    assert respuesta.status_code == 201, respuesta.text
    _trabajar()
    return cliente.get(respuesta.headers["Location"]).json()


def _decidida(cliente, cuentas: Cuentas) -> tuple[str, str]:
    """La cartera publicada y decidida con decision/v1 por la API: el run_id de su corrida y el
    decision_run_id de su ejecucion EXITOSA."""
    run_id = _publicar(cliente, cuentas)
    ejecucion = _decidir(cliente, run_id)
    assert ejecucion["estado"] == "EXITOSA", ejecucion
    return run_id, ejecucion["decision_run_id"]


def _organizar(cliente, decision_run_id: str):
    return cliente.post(f"/decisiones/{decision_run_id}/territoriales")


def _organizar_y_esperar(cliente, decision_run_id: str) -> tuple:
    """Pide la organizacion, deja trabajar al worker y devuelve la respuesta del POST y la ejecucion
    como quedo."""
    respuesta = _organizar(cliente, decision_run_id)
    assert respuesta.status_code == 201, respuesta.text
    _trabajar()
    return respuesta, cliente.get(respuesta.headers["Location"]).json()


def _organizada(cliente, decision_run_id: str) -> dict:
    """Organiza las decisiones por la API y devuelve su ejecucion territorial EXITOSA."""
    _, ejecucion = _organizar_y_esperar(cliente, decision_run_id)
    assert ejecucion["estado"] == "EXITOSA", ejecucion
    return ejecucion


def _id_de_corrida(run_id: str) -> int:
    with sesion() as s:
        return s.exec(select(Corrida.id).where(Corrida.run_id == UUID(run_id))).one()


def _id_de_decision(decision_run_id: str) -> int:
    with sesion() as s:
        return s.exec(
            select(EjecucionDecision.id).where(
                EjecucionDecision.decision_run_id == UUID(decision_run_id)
            )
        ).one()


def _fuente_a_mano(run_id: str, estado: EstadoDecision, version: str) -> str:
    """Una ejecucion de decision insertada a mano y sin decisiones; devuelve su decision_run_id."""
    fuente = EjecucionDecision(
        corrida_id=_id_de_corrida(run_id), version_reglas=version, estado=estado
    )
    with sesion() as s:
        s.add(fuente)
        s.commit()
        return str(fuente.decision_run_id)


def _registrar(fuente_id: int, **campos) -> EjecucionTerritorial:
    """Una ejecucion territorial insertada a mano, como la dejaria el historial; por omision,
    FALLIDA."""
    instante = ahora()
    valores = {
        "version_reglas": VERSION_REGLAS_TERRITORIAL,
        "estado": EstadoTerritorial.FALLIDA,
        "iniciada_en": instante,
        "terminada_en": instante,
        "detalle": "Registrada a mano para la prueba.",
        **campos,
    }
    with sesion() as s:
        ejecucion = EjecucionTerritorial(ejecucion_decision_id=fuente_id, **valores)
        s.add(ejecucion)
        s.commit()
        s.refresh(ejecucion)
        return ejecucion


def _abierta(cliente, decision_run_id: str) -> str:
    """Una ejecucion territorial pedida por la API y todavia sin organizar: como la ve el cliente
    mientras el worker no la toma, o si el worker murio a la mitad."""
    respuesta = _organizar(cliente, decision_run_id)
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()["territorial_run_id"]


def _ejecuciones_territoriales() -> int:
    with sesion() as s:
        return s.exec(select(func.count()).select_from(EjecucionTerritorial)).one()


def _trabajos_territoriales() -> list[TrabajoOrquestacion]:
    with sesion() as s:
        return list(
            s.exec(
                select(TrabajoOrquestacion)
                .where(TrabajoOrquestacion.tipo == TipoTrabajo.TERRITORIAL)
                .order_by(TrabajoOrquestacion.id)
            ).all()
        )


def _como_se_sirve(resultado: ResultadoTerritorio) -> dict:
    """Un municipio del nucleo, en la forma en que la API lo devuelve."""
    return {
        "clave_territorio": resultado.clave_territorio,
        "cve_entidad": resultado.cve_entidad,
        "cve_municipio": resultado.cve_municipio,
        "cuentas_total": resultado.cuentas_total,
        "saldo_total": str(resultado.saldo_total),
        "cuentas_campo": resultado.cuentas_campo,
        "saldo_campo": str(resultado.saldo_campo),
        "carga": resultado.carga.value,
        "posicion_campo": resultado.posicion_campo,
        "motivos": [
            {"codigo": motivo.codigo.value, "campo": motivo.campo, "valor": motivo.valor}
            for motivo in resultado.motivos
        ],
    }


def _entradas(municipios: list[dict]) -> list[EntradaTerritorio]:
    """Los agregados que sirve la API, de vuelta como entradas de territorial/v1."""
    return [
        EntradaTerritorio(
            m["cve_entidad"],
            m["cve_municipio"],
            m["cuentas_total"],
            Decimal(m["saldo_total"]),
            m["cuentas_campo"],
            Decimal(m["saldo_campo"]),
        )
        for m in municipios
    ]


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


# --- POST /decisiones/{decision_run_id}/territoriales ---------------------------------------------


@en_la_base
def test_post_deja_la_ejecucion_en_proceso_y_el_worker_la_organiza(cliente):
    # A1. 201 con la ejecucion recien creada, y su direccion; el worker la organiza despues.
    run_id, decision_run_id = _decidida(cliente, CARTERA)

    respuesta = _organizar(cliente, decision_run_id)

    assert respuesta.status_code == 201
    creada = respuesta.json()
    assert set(creada) == CAMPOS_DE_EJECUCION
    assert respuesta.headers["Location"] == f"/territoriales/{creada['territorial_run_id']}"
    assert (creada["decision_run_id"], creada["run_id"]) == (decision_run_id, run_id)
    assert (creada["version_reglas"], creada["estado"]) == ("territorial/v1", "EN_PROCESO")
    assert (creada["territorios_evaluados"], creada["territorios_publicados"]) == (0, 0)
    assert (creada["terminada_en"], creada["duracion_segundos"]) == (None, None)
    assert cliente.get(respuesta.headers["Location"]).json() == creada
    (trabajo,) = _trabajos_territoriales()
    assert (trabajo.estado, trabajo.flujo_id) == (EstadoTrabajo.PENDIENTE, None)

    _trabajar()

    ejecucion = cliente.get(respuesta.headers["Location"]).json()
    assert (ejecucion["territorial_run_id"], ejecucion["estado"]) == (
        creada["territorial_run_id"],
        "EXITOSA",
    )
    assert ejecucion["territorios_evaluados"] == ejecucion["territorios_publicados"] == 8
    assert (
        ejecucion["detalle"] == "Se organizaron 44 decisiones en 8 municipios con territorial/v1."
    )
    assert ejecucion["duracion_segundos"] >= 0
    assert ejecucion["iniciada_en"].endswith("Z") and ejecucion["terminada_en"].endswith("Z")


@en_la_base
def test_un_motor_que_falla_deja_la_ejecucion_fallida_despues_del_201(cliente, monkeypatch):
    # A2. D1: la ejecucion se creo y su resultado es su estado; no es un error de la peticion.
    _, decision_run_id = _decidida(cliente, PEQUENA)
    monkeypatch.setattr(ejecuciones, "priorizar_territorios", _revienta)

    respuesta, fallida = _organizar_y_esperar(cliente, decision_run_id)

    assert (respuesta.status_code, respuesta.json()["estado"]) == (201, "EN_PROCESO")
    assert respuesta.headers["Location"] == f"/territoriales/{fallida['territorial_run_id']}"
    assert (fallida["estado"], fallida["version_reglas"]) == ("FALLIDA", "territorial/v1")
    assert (fallida["territorios_evaluados"], fallida["territorios_publicados"]) == (0, 0)
    assert fallida["detalle"] == "Error interno (RuntimeError); ver la bitacora."
    assert "falla simulada" not in str(fallida)
    assert [t.estado for t in _trabajos_territoriales()] == [EstadoTrabajo.COMPLETADO]


@en_la_base
def test_el_post_no_organiza_nada_antes_de_responder(cliente, monkeypatch):
    # A25. El motor no corre en la peticion: ni se priorizan municipios, ni se agrega una decision.
    _, decision_run_id = _decidida(cliente, PEQUENA)
    priorizadas = []
    monkeypatch.setattr(ejecuciones, "priorizar_territorios", priorizadas.append)

    with _sentencias() as sentencias:
        respuesta = _organizar(cliente, decision_run_id)

    assert (respuesta.status_code, respuesta.json()["estado"]) == (201, "EN_PROCESO")
    assert priorizadas == []
    assert not any("resultado_territorial" in sentencia for sentencia in sentencias)
    assert not any("GROUP BY" in sentencia for sentencia in sentencias)


@en_la_base
def test_post_otra_vez_409_territorial_ya_generado_sin_location(cliente):
    # A3.
    run_id, decision_run_id = _decidida(cliente, PEQUENA)
    primera = _organizada(cliente, decision_run_id)

    respuesta = _organizar(cliente, decision_run_id)

    assert respuesta.status_code == 409
    error = respuesta.json()
    assert set(error) == CAMPOS_DE_ERROR
    assert (error["codigo"], error["run_id"]) == ("TERRITORIAL_YA_GENERADO", run_id)
    assert error["mensaje"] == (
        "Las decisiones de esta ejecucion ya se organizaron con exito con territorial/v1. "
        f"Consulta /decisiones/{decision_run_id}/territoriales."
    )
    assert "Location" not in respuesta.headers
    # D2: el error no dice cual ejecucion fue, ni en un campo ni en el mensaje.
    assert primera["territorial_run_id"] not in respuesta.text
    # La revision amable no deja ni el intento.
    assert _ejecuciones_territoriales() == 1


@en_la_base
def test_post_mientras_otra_sigue_en_proceso_409_territorial_en_proceso(cliente):
    # A lo mas una ejecucion activa por fuente y version: un doble clic no crea dos.
    run_id, decision_run_id = _decidida(cliente, PEQUENA)
    primera = _organizar(cliente, decision_run_id).json()

    respuesta = _organizar(cliente, decision_run_id)

    assert respuesta.status_code == 409
    error = respuesta.json()
    assert (error["codigo"], error["run_id"]) == ("TERRITORIAL_EN_PROCESO", run_id)
    assert error["mensaje"] == (
        "Las decisiones de esta ejecucion ya se estan organizando con territorial/v1. Consulta "
        f"/decisiones/{decision_run_id}/territoriales."
    )
    assert "Location" not in respuesta.headers
    assert primera["territorial_run_id"] not in respuesta.text
    assert _ejecuciones_territoriales() == len(_trabajos_territoriales()) == 1


@en_la_base
def test_una_organizacion_que_pierde_la_carrera_en_el_worker_queda_fallida_en_el_historial(
    cliente, monkeypatch
):
    # La carrera que no ve la revision amable: mientras el worker organiza, otra ejecucion de las
    # mismas decisiones aparece EXITOSA, a mano, justo antes del cierre. El indice de exito rechaza
    # el cierre de esta, que queda FALLIDA en el historial; el siguiente POST ya ve la que gano.
    run_id, decision_run_id = _decidida(cliente, PEQUENA)
    cerrar = ejecuciones._cerrar
    rivales = []

    def el_rival_publica_primero(s, ejecucion, *argumentos):
        rivales.append(
            _registrar(
                _id_de_decision(decision_run_id),
                estado=EstadoTerritorial.EXITOSA,
                territorios_evaluados=2,
                territorios_publicados=2,
            )
        )
        cerrar(s, ejecucion, *argumentos)

    monkeypatch.setattr(ejecuciones, "_cerrar", el_rival_publica_primero)

    respuesta, perdedora = _organizar_y_esperar(cliente, decision_run_id)

    (rival,) = rivales
    assert respuesta.status_code == 201
    assert (perdedora["estado"], perdedora["territorios_publicados"]) == ("FALLIDA", 0)
    assert perdedora["detalle"] == (
        "Otra ejecucion publico los territorios de esta ejecucion de decision con territorial/v1 "
        "mientras esta se procesaba; no se publican dos veces."
    )
    otra = _organizar(cliente, decision_run_id)
    assert (otra.status_code, otra.json()["codigo"], otra.json()["run_id"]) == (
        409,
        "TERRITORIAL_YA_GENERADO",
        run_id,
    )
    assert "Location" not in otra.headers
    assert str(rival.territorial_run_id) not in otra.text
    historial = cliente.get(f"/decisiones/{decision_run_id}/territoriales").json()["elementos"]
    estados = {
        e["territorial_run_id"]: (e["estado"], e["territorios_publicados"]) for e in historial
    }
    assert estados.pop(str(rival.territorial_run_id)) == ("EXITOSA", 2)
    assert list(estados.values()) == [("FALLIDA", 0)]  # la de esta peticion


@en_la_base
@pytest.mark.parametrize("metodo", ["POST", "GET"])
def test_una_ejecucion_de_decision_que_no_existe_404_sin_run_id(cliente, metodo):
    # A4. No hay corrida que nombrar, y no se registra nada.
    decision_run_id = uuid4()

    respuesta = cliente.request(metodo, f"/decisiones/{decision_run_id}/territoriales")

    assert respuesta.status_code == 404
    error = respuesta.json()
    assert set(error) == CAMPOS_DE_ERROR
    assert (error["codigo"], error["run_id"]) == ("DECISION_NO_ENCONTRADA", None)
    assert error["mensaje"] == (
        f"No existe una ejecucion de decision con decision_run_id {decision_run_id}."
    )
    assert _ejecuciones_territoriales() == 0


def _decision_en_proceso(cliente, run_id: str, monkeypatch) -> str:
    # Abierta por el Decision Engine y todavia sin decidir.
    with sesion() as s:
        corrida = s.exec(select(Corrida).where(Corrida.run_id == UUID(run_id))).one()
        return str(ejecuciones_de_decision.abrir_ejecucion(s, corrida).decision_run_id)


def _decision_fallida(cliente, run_id: str, monkeypatch) -> str:
    monkeypatch.setattr(ejecuciones_de_decision, "decidir_cuenta", _revienta)
    fallida = _decidir(cliente, run_id)
    assert fallida["estado"] == "FALLIDA", fallida
    return fallida["decision_run_id"]


@en_la_base
@pytest.mark.parametrize(
    ("crear", "estado"),
    [
        pytest.param(_decision_en_proceso, "EN_PROCESO", id="EN_PROCESO"),
        pytest.param(_decision_fallida, "FALLIDA", id="FALLIDA"),
    ],
)
def test_post_sobre_una_decision_que_no_publico_409_sin_ejecucion(
    cliente, monkeypatch, crear, estado
):
    # A5.
    run_id = _publicar(cliente, PEQUENA)
    decision_run_id = crear(cliente, run_id, monkeypatch)

    respuesta = _organizar(cliente, decision_run_id)

    assert respuesta.status_code == 409
    error = respuesta.json()
    assert set(error) == CAMPOS_DE_ERROR
    assert (error["codigo"], error["run_id"]) == ("DECISION_NO_TERRITORIALIZABLE", run_id)
    assert error["mensaje"] == (
        f"La ejecucion de decision {decision_run_id} esta {estado}; solo se organizan por "
        "territorio las decisiones de una ejecucion EXITOSA."
    )
    assert "Location" not in respuesta.headers
    assert _ejecuciones_territoriales() == 0


@en_la_base
def test_post_sobre_decisiones_de_otra_version_409_sin_ejecucion(cliente):
    # A6. Aunque este EXITOSA: territorial/v1 solo organiza decisiones de decision/v1.
    run_id = _publicar(cliente, PEQUENA)
    decision_run_id = _fuente_a_mano(run_id, EstadoDecision.EXITOSA, "decision/v2")

    respuesta = _organizar(cliente, decision_run_id)

    assert respuesta.status_code == 409
    error = respuesta.json()
    assert set(error) == CAMPOS_DE_ERROR
    assert (error["codigo"], error["run_id"]) == ("DECISION_NO_TERRITORIALIZABLE", run_id)
    assert error["mensaje"] == (
        f"La ejecucion de decision {decision_run_id} se decidio con decision/v2; territorial/v1 "
        "solo organiza decisiones de decision/v1."
    )
    assert _ejecuciones_territoriales() == 0


@en_la_base
def test_d2_el_409_no_dice_cual_ejecucion_y_el_historial_si(cliente):
    # A26. D2 de punta a punta: organizar, chocar con lo ya publicado y encontrarlo en el historial.
    _, decision_run_id = _decidida(cliente, PEQUENA)
    primera = _organizada(cliente, decision_run_id)

    otra = _organizar(cliente, decision_run_id)
    assert (otra.status_code, otra.json()["codigo"]) == (409, "TERRITORIAL_YA_GENERADO")
    assert "Location" not in otra.headers
    assert primera["territorial_run_id"] not in otra.text

    historial = cliente.get(f"/decisiones/{decision_run_id}/territoriales").json()["elementos"]
    (publicada,) = [ejecucion for ejecucion in historial if ejecucion["estado"] == "EXITOSA"]
    assert publicada["version_reglas"] == "territorial/v1"
    assert publicada["territorial_run_id"] == primera["territorial_run_id"]
    assert cliente.get(f"/territoriales/{publicada['territorial_run_id']}").json() == primera


# --- las decisiones de un flujo automatico -------------------------------------------------------


def _subir(cliente, cuentas: Cuentas) -> str:
    """Sube la cartera por POST /corridas: nace con su flujo. Devuelve el run_id."""
    respuesta = cliente.post(
        "/corridas", files={"archivo": ("cartera.csv", _csv(cuentas), "application/octet-stream")}
    )
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()["run_id"]


def _una_vez() -> None:
    """Un solo trabajo, como un worker con --una-vez."""
    procesar_un_trabajo(identificador_worker(), Config())


@en_la_base
def test_unas_decisiones_cuyo_flujo_las_va_a_organizar_no_se_organizan_a_mano(cliente):
    run_id = _subir(cliente, PEQUENA)
    _una_vez()  # la ingesta; el flujo ya pidio la decision
    flujo = cliente.get(f"/corridas/{run_id}/flujo").json()
    assert (flujo["estado"], flujo["etapa"]) == ("EN_PROCESO", "DECISION")

    respuesta = _organizar(cliente, flujo["decision_run_id"])

    assert respuesta.status_code == 409
    error = respuesta.json()
    assert (error["codigo"], error["run_id"]) == ("FLUJO_EN_PROCESO", run_id)
    assert error["mensaje"] == (
        f"Las decisiones son del flujo {flujo['flujo_id']}, que sigue EN_PROCESO en DECISION y "
        f"las va a organizar por su cuenta. Consulta /flujos/{flujo['flujo_id']}."
    )
    assert _ejecuciones_territoriales() == 0


@en_la_base
def test_unas_decisiones_cuyo_flujo_se_detuvo_al_organizarlas_se_reanudan(cliente, monkeypatch):
    run_id = _subir(cliente, PEQUENA)
    monkeypatch.setattr(ejecuciones, "priorizar_territorios", _revienta)
    _trabajar()
    flujo = cliente.get(f"/corridas/{run_id}/flujo").json()
    assert (flujo["estado"], flujo["etapa"]) == ("DETENIDO", "TERRITORIAL")

    respuesta = _organizar(cliente, flujo["decision_run_id"])

    assert respuesta.status_code == 409
    error = respuesta.json()
    assert (error["codigo"], error["run_id"]) == ("FLUJO_DETENIDO", run_id)
    assert error["mensaje"] == (
        f"Las decisiones son del flujo {flujo['flujo_id']}, que se detuvo en la organizacion "
        f"territorial. Para reintentarla: POST /flujos/{flujo['flujo_id']}/reanudar."
    )
    assert _ejecuciones_territoriales() == 1


@en_la_base
def test_unas_decisiones_que_su_flujo_ya_organizo_dan_su_409_de_siempre(cliente):
    run_id = _subir(cliente, PEQUENA)
    _trabajar()
    flujo = cliente.get(f"/corridas/{run_id}/flujo").json()

    respuesta = _organizar(cliente, flujo["decision_run_id"])

    assert (respuesta.status_code, respuesta.json()["codigo"]) == (409, "TERRITORIAL_YA_GENERADO")
    assert "Location" not in respuesta.headers


# --- GET /decisiones/{decision_run_id}/territoriales ----------------------------------------------


@en_la_base
def test_el_historial_de_decisiones_sin_ejecuciones_territoriales_es_una_lista_vacia(
    cliente, monkeypatch
):
    # A7. Tambien de una ejecucion de decision que no publico: el historial no exige que este
    # EXITOSA.
    run_id = _publicar(cliente, PEQUENA)
    with monkeypatch.context() as parche:
        fallida = _decision_fallida(cliente, run_id, parche)
    exitosa = _decidir(cliente, run_id)
    assert exitosa["estado"] == "EXITOSA", exitosa

    for decision_run_id in (exitosa["decision_run_id"], fallida):
        respuesta = cliente.get(f"/decisiones/{decision_run_id}/territoriales")
        assert respuesta.status_code == 200
        assert respuesta.json() == {
            "decision_run_id": decision_run_id,
            "run_id": run_id,
            "total": 0,
            "pagina": 1,
            "por_pagina": 50,
            "elementos": [],
        }


@en_la_base
def test_el_historial_trae_todas_las_ejecuciones_en_cualquier_estado_y_version(
    cliente, monkeypatch
):
    # A8. Una FALLIDA y una EXITOSA de territorial/v1, por la API, una FALLIDA de otra version y
    # una que sigue EN_PROCESO.
    run_id, decision_run_id = _decidida(cliente, PEQUENA)
    with monkeypatch.context() as parche:
        parche.setattr(ejecuciones, "priorizar_territorios", _revienta)
        _, fallida = _organizar_y_esperar(cliente, decision_run_id)
    _, exitosa = _organizar_y_esperar(cliente, decision_run_id)  # la FALLIDA se reintenta
    fuente_id = _id_de_decision(decision_run_id)
    otra_version = _registrar(fuente_id, version_reglas="territorial/v2")
    en_proceso = _registrar(
        fuente_id, estado=EstadoTerritorial.EN_PROCESO, terminada_en=None, detalle=None
    )

    respuesta = cliente.get(f"/decisiones/{decision_run_id}/territoriales")

    assert respuesta.status_code == 200
    historial = respuesta.json()
    assert set(historial) == CAMPOS_DEL_HISTORIAL
    assert (historial["decision_run_id"], historial["run_id"], historial["total"]) == (
        decision_run_id,
        run_id,
        4,
    )
    for ejecucion in historial["elementos"]:
        # Lo publico de cada ejecucion, sin su id ni el de su ejecucion de decision o su corrida.
        assert set(ejecucion) == CAMPOS_DE_EJECUCION
        assert (ejecucion["decision_run_id"], ejecucion["run_id"]) == (decision_run_id, run_id)
    por_id = {ejecucion["territorial_run_id"]: ejecucion for ejecucion in historial["elementos"]}
    assert (fallida["estado"], exitosa["estado"]) == ("FALLIDA", "EXITOSA")
    assert por_id[fallida["territorial_run_id"]] == fallida
    assert por_id[exitosa["territorial_run_id"]] == exitosa
    historica = por_id[str(otra_version.territorial_run_id)]
    assert (historica["version_reglas"], historica["estado"]) == ("territorial/v2", "FALLIDA")
    assert historica["detalle"] == "Registrada a mano para la prueba."
    abierta = por_id[str(en_proceso.territorial_run_id)]
    assert (abierta["estado"], abierta["terminada_en"], abierta["duracion_segundos"]) == (
        "EN_PROCESO",
        None,
        None,
    )


@en_la_base
def test_el_historial_va_de_la_mas_reciente_a_la_mas_antigua(cliente):
    # A9. Fuera del orden en que se insertan, y con un empate en el instante, que desempata el id.
    _, decision_run_id = _decidida(cliente, PEQUENA)
    fuente_id = _id_de_decision(decision_run_id)
    mediodia = datetime(2026, 9, 30, 12, tzinfo=UTC)

    def iniciada(instante: datetime) -> EjecucionTerritorial:
        return _registrar(
            fuente_id, iniciada_en=instante, terminada_en=instante + timedelta(minutes=1)
        )

    diez = iniciada(mediodia - timedelta(hours=2))
    primera_de_mediodia = iniciada(mediodia)
    segunda_de_mediodia = iniciada(mediodia)
    nueve = iniciada(mediodia - timedelta(hours=3))  # la mas antigua, insertada al final

    elementos = cliente.get(f"/decisiones/{decision_run_id}/territoriales").json()["elementos"]

    assert [e["territorial_run_id"] for e in elementos] == [
        str(e.territorial_run_id) for e in (segunda_de_mediodia, primera_de_mediodia, diez, nueve)
    ]


@en_la_base
def test_el_historial_se_pagina_sin_huecos_ni_repetidos(cliente):
    # A10.
    _, decision_run_id = _decidida(cliente, PEQUENA)
    fuente_id = _id_de_decision(decision_run_id)
    for _ in range(7):
        _registrar(fuente_id)
    ruta = f"/decisiones/{decision_run_id}/territoriales"
    todas = [e["territorial_run_id"] for e in cliente.get(ruta).json()["elementos"]]

    paginas = [
        cliente.get(ruta, params={"pagina": p, "por_pagina": 3}).json() for p in (1, 2, 3, 4)
    ]

    assert [len(p["elementos"]) for p in paginas] == [3, 3, 1, 0]
    assert {p["total"] for p in paginas} == {7}
    assert [e["territorial_run_id"] for p in paginas for e in p["elementos"]] == todas
    assert len(set(todas)) == 7


# --- GET /territoriales/{territorial_run_id} ------------------------------------------------------


@en_la_base
def test_una_ejecucion_territorial_se_consulta_por_su_territorial_run_id(cliente):
    # A11.
    run_id, decision_run_id = _decidida(cliente, PEQUENA)
    territorial_run_id = _organizada(cliente, decision_run_id)["territorial_run_id"]

    respuesta = cliente.get(f"/territoriales/{territorial_run_id}")

    assert respuesta.status_code == 200
    with sesion() as s:
        guardada = s.exec(
            select(EjecucionTerritorial).where(
                EjecucionTerritorial.territorial_run_id == UUID(territorial_run_id)
            )
        ).one()
    ejecucion = respuesta.json()
    assert set(ejecucion) == CAMPOS_DE_EJECUCION
    assert (ejecucion["territorial_run_id"], ejecucion["decision_run_id"], ejecucion["run_id"]) == (
        territorial_run_id,
        decision_run_id,
        run_id,
    )
    assert (ejecucion["version_reglas"], ejecucion["estado"], ejecucion["detalle"]) == (
        guardada.version_reglas,
        guardada.estado,
        guardada.detalle,
    )
    assert (ejecucion["territorios_evaluados"], ejecucion["territorios_publicados"]) == (
        guardada.territorios_evaluados,
        guardada.territorios_publicados,
    )
    assert datetime.fromisoformat(ejecucion["iniciada_en"]) == guardada.iniciada_en
    assert datetime.fromisoformat(ejecucion["terminada_en"]) == guardada.terminada_en
    assert ejecucion["duracion_segundos"] == round(
        (guardada.terminada_en - guardada.iniciada_en).total_seconds(), 3
    )
    # Cada identificador publico nombra su propio recurso: no se intercambian.
    no_es_territorial = cliente.get(f"/territoriales/{decision_run_id}")
    assert (no_es_territorial.status_code, no_es_territorial.json()["codigo"]) == (
        404,
        "TERRITORIAL_NO_ENCONTRADO",
    )
    no_es_decision = cliente.get(f"/decisiones/{territorial_run_id}/territoriales")
    assert (no_es_decision.status_code, no_es_decision.json()["codigo"]) == (
        404,
        "DECISION_NO_ENCONTRADA",
    )


@en_la_base
def test_una_ejecucion_territorial_en_proceso_se_consulta_sin_fin_ni_duracion(cliente):
    # Existe y se consulta: 200, con el fin y la duracion en null, no en cero.
    run_id, decision_run_id = _decidida(cliente, PEQUENA)
    territorial_run_id = _abierta(cliente, decision_run_id)

    respuesta = cliente.get(f"/territoriales/{territorial_run_id}")

    assert respuesta.status_code == 200
    ejecucion = respuesta.json()
    assert set(ejecucion) == CAMPOS_DE_EJECUCION
    assert (ejecucion["decision_run_id"], ejecucion["run_id"]) == (decision_run_id, run_id)
    assert (ejecucion["version_reglas"], ejecucion["estado"]) == ("territorial/v1", "EN_PROCESO")
    assert (ejecucion["terminada_en"], ejecucion["duracion_segundos"]) == (None, None)
    assert (ejecucion["territorios_evaluados"], ejecucion["territorios_publicados"]) == (0, 0)


@en_la_base
@pytest.mark.parametrize("ruta", ["/territoriales/{}", "/territoriales/{}/municipios"])
def test_una_ejecucion_territorial_que_no_existe_404_sin_run_id(cliente, ruta):
    # A12. No hay corrida que nombrar.
    territorial_run_id = uuid4()

    respuesta = cliente.get(ruta.format(territorial_run_id))

    assert respuesta.status_code == 404
    error = respuesta.json()
    assert set(error) == CAMPOS_DE_ERROR
    assert (error["codigo"], error["run_id"]) == ("TERRITORIAL_NO_ENCONTRADO", None)
    assert error["mensaje"] == (
        f"No existe una ejecucion territorial con territorial_run_id {territorial_run_id}."
    )


# --- GET /territoriales/{territorial_run_id}/municipios -------------------------------------------


@en_la_base
def test_los_municipios_de_una_ejecucion_exitosa(cliente):
    # A13. Exactamente lo que territorial/v1 publico de cada municipio, en su orden.
    run_id, decision_run_id = _decidida(cliente, CARTERA)
    ejecucion = _organizada(cliente, decision_run_id)

    respuesta = cliente.get(f"/territoriales/{ejecucion['territorial_run_id']}/municipios")

    assert respuesta.status_code == 200
    pagina = respuesta.json()
    assert set(pagina) == CAMPOS_DE_LOS_MUNICIPIOS
    assert (pagina["territorial_run_id"], pagina["decision_run_id"], pagina["run_id"]) == (
        ejecucion["territorial_run_id"],
        decision_run_id,
        run_id,
    )
    assert (pagina["version_reglas"], pagina["estado"]) == ("territorial/v1", "EXITOSA")
    assert (pagina["total"], pagina["pagina"], pagina["por_pagina"]) == (8, 1, 50)
    assert pagina["total"] == ejecucion["territorios_publicados"]
    # Cada municipio con sus campos publicos, sin el id del resultado ni el de su ejecucion.
    for municipio in pagina["elementos"]:
        assert set(municipio) == CAMPOS_DE_MUNICIPIO
    assert pagina["elementos"] == MUNICIPIOS
    # Los saldos en texto, para no perder centavos, y el vocabulario como texto plano.
    assert all(type(m["saldo_total"]) is str for m in pagina["elementos"])
    assert not re.search(r"CargaTerritorial|CodigoMotivoTerritorial", respuesta.text)


@en_la_base
def test_el_total_de_municipios_sale_de_la_tabla_y_no_del_contador(cliente):
    # A14. El contador se descompone a proposito: el total tiene que seguir siendo lo que hay
    # guardado.
    _, decision_run_id = _decidida(cliente, CARTERA)
    territorial_run_id = _organizada(cliente, decision_run_id)["territorial_run_id"]
    with sesion() as s:
        s.execute(
            update(EjecucionTerritorial)
            .where(EjecucionTerritorial.territorial_run_id == UUID(territorial_run_id))
            .values(territorios_publicados=999)
        )
        s.commit()

    pagina = cliente.get(f"/territoriales/{territorial_run_id}/municipios").json()

    assert pagina["total"] == len(pagina["elementos"]) == 8


def _resultado(ejecucion_id: int, clave: str, lugar: int | None) -> ResultadoTerritorial:
    """Un municipio con una cuenta: de campo y BAJA si tiene lugar, y SIN_CARGA si no."""
    campo = 0 if lugar is None else 1
    return ResultadoTerritorial(
        ejecucion_territorial_id=ejecucion_id,
        cve_entidad=clave[:2],
        cve_municipio=clave[2:],
        cuentas_total=1,
        saldo_total=Decimal("100.00"),
        cuentas_campo=campo,
        saldo_campo=Decimal("100.00") * campo,
        carga="BAJA" if campo else "SIN_CARGA",
        posicion_campo=lugar,
        motivos=[
            {
                "codigo": "CARGA_CAMPO_1_4" if campo else "SIN_CARGA_CAMPO",
                "campo": "cuentas_campo",
                "valor": str(campo),
            }
        ],
    )


@en_la_base
def test_los_municipios_van_por_lugar_y_los_que_no_tienen_al_final_por_clave(cliente):
    # A15. Guardados a mano y en desorden: el orden lo pone la consulta, no el orden en que se
    # insertaron. Primero por lugar; los que no tienen (NULL) al final, por entidad y municipio.
    _, decision_run_id = _decidida(cliente, PEQUENA)
    ejecucion = _registrar(
        _id_de_decision(decision_run_id),
        estado=EstadoTerritorial.EXITOSA,
        territorios_evaluados=6,
        territorios_publicados=6,
    )
    desordenados = [
        ("21099", None),
        ("15033", 3),
        ("21001", None),
        ("21114", 1),
        ("09002", None),
        ("09005", 2),
    ]
    with sesion() as s:
        for clave, lugar in desordenados:
            s.add(_resultado(ejecucion.id, clave, lugar))
            s.flush()  # uno por uno, en este orden
        s.commit()

    elementos = cliente.get(f"/territoriales/{ejecucion.territorial_run_id}/municipios").json()[
        "elementos"
    ]

    assert [(m["clave_territorio"], m["posicion_campo"]) for m in elementos] == [
        ("21114", 1),
        ("09005", 2),
        ("15033", 3),
        ("09002", None),
        ("21001", None),
        ("21099", None),
    ]


@en_la_base
def test_los_municipios_se_paginan_sin_huecos_ni_repetidos_en_el_orden_de_territorial_v1(cliente):
    # A16. 120 municipios: 80 con cuentas de campo y 40 sin ninguna.
    _, decision_run_id = _decidida(cliente, _un_municipio_por_cuenta(120))
    territorial_run_id = _organizada(cliente, decision_run_id)["territorial_run_id"]
    ruta = f"/territoriales/{territorial_run_id}/municipios"
    todos = cliente.get(ruta, params={"por_pagina": 500}).json()["elementos"]

    paginas = [
        cliente.get(ruta, params={"pagina": p, "por_pagina": 50}).json() for p in (1, 2, 3, 4)
    ]

    assert [len(p["elementos"]) for p in paginas] == [50, 50, 20, 0]
    assert {p["total"] for p in paginas} == {120}
    assert [m for p in paginas for m in p["elementos"]] == todos
    assert len({m["clave_territorio"] for m in todos}) == 120  # cada uno, una vez
    assert sum(m["cuentas_campo"] for m in todos) == 80
    # Los lugares del 1 al 80, sin huecos, y despues los 40 sin lugar. Cada municipio y su orden
    # son los que territorial/v1 da a esos mismos agregados.
    assert [m["posicion_campo"] for m in todos] == [*range(1, 81), *[None] * 40]
    assert todos == [_como_se_sirve(r) for r in priorizar_territorios(_entradas(todos))]


def _territorial_en_proceso(cliente, decision_run_id: str, monkeypatch) -> str:
    return _abierta(cliente, decision_run_id)


def _territorial_fallida(cliente, decision_run_id: str, monkeypatch) -> str:
    monkeypatch.setattr(ejecuciones, "priorizar_territorios", _revienta)
    _, fallida = _organizar_y_esperar(cliente, decision_run_id)
    assert fallida["estado"] == "FALLIDA", fallida
    return fallida["territorial_run_id"]


@en_la_base
@pytest.mark.parametrize(
    ("crear", "estado"),
    [
        pytest.param(_territorial_en_proceso, "EN_PROCESO", id="EN_PROCESO"),
        pytest.param(_territorial_fallida, "FALLIDA", id="FALLIDA"),
    ],
)
def test_los_municipios_de_una_ejecucion_que_no_publico_409(cliente, monkeypatch, crear, estado):
    # A17 y A18. No una lista vacia: diria que no habia municipios.
    run_id, decision_run_id = _decidida(cliente, PEQUENA)
    territorial_run_id = crear(cliente, decision_run_id, monkeypatch)

    respuesta = cliente.get(f"/territoriales/{territorial_run_id}/municipios")

    assert respuesta.status_code == 409
    error = respuesta.json()
    assert set(error) == CAMPOS_DE_ERROR
    assert (error["codigo"], error["run_id"]) == ("TERRITORIAL_NO_PUBLICADO", run_id)
    assert error["mensaje"] == (
        f"La ejecucion territorial esta {estado} y no publico municipios; solo una ejecucion "
        "EXITOSA tiene resultados por municipio."
    )


@en_la_base
def test_un_municipio_historico_con_otro_vocabulario_se_sigue_sirviendo(cliente):
    # A19. Lo que una version futura podria haber guardado, con valores que territorial/v1 no
    # conoce. No se ejecuta ninguna regla: solo se lee el historial.
    _, decision_run_id = _decidida(cliente, PEQUENA)
    historica = _registrar(
        _id_de_decision(decision_run_id),
        version_reglas="territorial/v2",
        estado=EstadoTerritorial.EXITOSA,
        territorios_evaluados=1,
        territorios_publicados=1,
        detalle="Se organizaron 3 decisiones en 1 municipios con territorial/v2.",
    )
    futuro = {
        "cve_entidad": "21",
        "cve_municipio": "001",
        "cuentas_total": 3,
        "cuentas_campo": 2,
        "carga": "CRITICA",
        "posicion_campo": 1,
        "motivos": [
            {"codigo": "REGLA_TERRITORIAL_FUTURA", "campo": "saldo_campo", "valor": "4500.00"}
        ],
    }
    with sesion() as s:
        s.add(
            ResultadoTerritorial(
                ejecucion_territorial_id=historica.id,
                saldo_total=Decimal("11500.00"),
                saldo_campo=Decimal("4500.00"),
                **futuro,
            )
        )
        s.commit()

    respuesta = cliente.get(f"/territoriales/{historica.territorial_run_id}/municipios")

    assert respuesta.status_code == 200
    pagina = respuesta.json()
    assert (pagina["version_reglas"], pagina["estado"], pagina["total"]) == (
        "territorial/v2",
        "EXITOSA",
        1,
    )
    assert pagina["elementos"] == [
        {
            "clave_territorio": "21001",
            "saldo_total": "11500.00",
            "saldo_campo": "4500.00",
            **futuro,
        }
    ]
    # La ejecucion y el historial tambien la sirven.
    ejecucion = cliente.get(f"/territoriales/{historica.territorial_run_id}").json()
    assert (ejecucion["version_reglas"], ejecucion["estado"]) == ("territorial/v2", "EXITOSA")
    historial = cliente.get(f"/decisiones/{decision_run_id}/territoriales").json()
    assert historial["elementos"] == [ejecucion]


@en_la_base
def test_una_pagina_de_municipios_son_tres_consultas_sean_10_o_100(cliente):
    # A24. Sin N+1: la ejecucion con sus identificadores publicos, el total y la pagina.
    _, decision_run_id = _decidida(cliente, _un_municipio_por_cuenta(120))
    territorial_run_id = _organizada(cliente, decision_run_id)["territorial_run_id"]
    vistas = {}

    for por_pagina in (10, 100):
        with _sentencias() as sentencias:
            pagina = cliente.get(
                f"/territoriales/{territorial_run_id}/municipios",
                params={"por_pagina": por_pagina},
            ).json()
        assert len(pagina["elementos"]) == por_pagina
        vistas[por_pagina] = [" ".join(sentencia.split()) for sentencia in sentencias]

    # Tres consultas, sean 10 o 100 municipios.
    assert len(vistas[10]) == len(vistas[100]) == 3
    for buscar, contar, leer in vistas.values():
        # La ejecucion llega con un JOIN a su ejecucion de decision y a su corrida.
        assert "JOIN ejecucion_decision" in buscar and "JOIN corrida" in buscar
        assert contar.startswith("SELECT count(*)") and "FROM resultado_territorial" in contar
        # Y la pagina, en una sola consulta, en el orden de territorial/v1.
        assert "FROM resultado_territorial" in leer
        assert (
            "ORDER BY resultado_territorial.posicion_campo ASC NULLS LAST, "
            "resultado_territorial.cve_entidad, resultado_territorial.cve_municipio"
        ) in leer


# --- sin base de datos -------------------------------------------------------------------------


@pytest.mark.parametrize(("metodo", "ruta"), RUTAS)
@pytest.mark.parametrize(
    ("clave", "codigo"), [("", "API_KEY_AUSENTE"), ("otra", "API_KEY_INVALIDA")]
)
def test_las_rutas_territoriales_piden_api_key(cliente, metodo, ruta, clave, codigo):
    # A20. La clave se revisa antes de buscar nada.
    url = ruta.format(decision_run_id=uuid4(), territorial_run_id=uuid4())

    respuesta = cliente.request(metodo, url, headers={"X-API-Key": clave})

    assert respuesta.status_code == 401
    assert set(respuesta.json()) == CAMPOS_DE_ERROR
    assert respuesta.json()["codigo"] == codigo


@pytest.mark.parametrize(
    ("metodo", "ruta", "campo"),
    [
        ("POST", "/decisiones/123/territoriales", "path.decision_run_id"),
        ("GET", "/decisiones/123/territoriales", "path.decision_run_id"),
        ("GET", "/territoriales/123", "path.territorial_run_id"),
        ("GET", "/territoriales/123/municipios", "path.territorial_run_id"),
    ],
)
def test_un_identificador_que_no_es_uuid_422(cliente, metodo, ruta, campo):
    respuesta = cliente.request(metodo, ruta)

    assert respuesta.status_code == 422
    assert set(respuesta.json()) == CAMPOS_DE_ERROR
    assert respuesta.json()["codigo"] == "ENTRADA_INVALIDA"
    assert [detalle["campo"] for detalle in respuesta.json()["detalles"]] == [campo]


@pytest.mark.parametrize("ruta", ["/decisiones/{}/territoriales", "/territoriales/{}/municipios"])
@pytest.mark.parametrize(
    ("parametros", "campo"),
    [({"pagina": 0}, "query.pagina"), ({"por_pagina": 501}, "query.por_pagina")],
)
def test_paginacion_fuera_de_rango_422(cliente, ruta, parametros, campo):
    # A21.
    respuesta = cliente.get(ruta.format(uuid4()), params=parametros)

    assert respuesta.status_code == 422
    assert respuesta.json()["codigo"] == "ENTRADA_INVALIDA"
    assert [detalle["campo"] for detalle in respuesta.json()["detalles"]] == [campo]


def test_openapi_documenta_las_cuatro_operaciones_territoriales(app):
    # A22.
    api = app.openapi()
    operaciones = {
        (metodo.upper(), ruta): operacion
        for ruta, metodos in api["paths"].items()
        for metodo, operacion in metodos.items()
        if "territorial" in operacion.get("tags", [])
    }

    assert {clave: set(op["responses"]) for clave, op in operaciones.items()} == {
        ("POST", "/decisiones/{decision_run_id}/territoriales"): {
            "201",
            "401",
            "404",
            "409",
            "422",
        },
        ("GET", "/decisiones/{decision_run_id}/territoriales"): {"200", "401", "404", "422"},
        ("GET", "/territoriales/{territorial_run_id}"): {"200", "401", "404", "422"},
        ("GET", "/territoriales/{territorial_run_id}/municipios"): {
            "200",
            "401",
            "404",
            "409",
            "422",
        },
    }
    modelos = {}
    for (metodo, ruta), operacion in operaciones.items():
        assert operacion["tags"] == ["territorial"]
        assert operacion["summary"] and operacion["description"]
        exito = operacion["responses"]["201" if metodo == "POST" else "200"]
        modelos[(metodo, ruta)] = exito["content"]["application/json"]["schema"]["$ref"]
        # Todo error, con el mismo esquema que el resto de la API.
        for codigo, respuesta in operacion["responses"].items():
            if codigo[0] in "45":
                esquema = respuesta["content"]["application/json"]["schema"]
                assert esquema["$ref"].endswith("/ErrorRespuesta")
    assert {clave: ref.rsplit("/", 1)[1] for clave, ref in modelos.items()} == {
        ("POST", "/decisiones/{decision_run_id}/territoriales"): "EjecucionTerritorialRespuesta",
        ("GET", "/decisiones/{decision_run_id}/territoriales"): "PaginaEjecucionesTerritoriales",
        ("GET", "/territoriales/{territorial_run_id}"): "EjecucionTerritorialRespuesta",
        ("GET", "/territoriales/{territorial_run_id}/municipios"): "PaginaMunicipios",
    }

    # 201 dice que la ejecucion se creo EN_PROCESO, no que el motor terminara: su ejemplo es lo
    # que de verdad responde el POST, y el GET documenta los tres estados.
    post = operaciones[("POST", "/decisiones/{decision_run_id}/territoriales")]
    creada = post["responses"]["201"]
    assert "EN_PROCESO" in creada["description"] and "worker" in post["description"]
    assert creada["content"]["application/json"]["example"]["estado"] == "EN_PROCESO"
    assert "`DECISION_NO_ENCONTRADA`" in post["responses"]["404"]["description"]
    for codigo in (
        "DECISION_NO_TERRITORIALIZABLE",
        "TERRITORIAL_YA_GENERADO",
        "TERRITORIAL_EN_PROCESO",
        "FLUJO_EN_PROCESO",
        "FLUJO_DETENIDO",
    ):
        assert f"`{codigo}`" in post["responses"]["409"]["description"]
    historial = operaciones[("GET", "/decisiones/{decision_run_id}/territoriales")]
    assert "`DECISION_NO_ENCONTRADA`" in historial["responses"]["404"]["description"]
    ejecucion = operaciones[("GET", "/territoriales/{territorial_run_id}")]
    assert "`TERRITORIAL_NO_ENCONTRADO`" in ejecucion["responses"]["404"]["description"]
    ejemplos = ejecucion["responses"]["200"]["content"]["application/json"]["examples"]
    assert set(ejemplos) == {"EN_PROCESO", "EXITOSA", "FALLIDA"}
    municipios = operaciones[("GET", "/territoriales/{territorial_run_id}/municipios")]
    assert "`TERRITORIAL_NO_ENCONTRADO`" in municipios["responses"]["404"]["description"]
    assert "`TERRITORIAL_NO_PUBLICADO`" in municipios["responses"]["409"]["description"]
    assert {
        "name": "territorial",
        "description": "Ejecuciones versionadas del Motor Territorial y sus resultados por "
        "municipio: carga de campo y prioridad, no rutas.",
    } in api["tags"]


def test_el_error_sigue_siendo_el_de_toda_la_api(app):
    # A23. Los errores nuevos usan ErrorRespuesta tal cual: sin territorial_run_id ni
    # decision_run_id, y con run_id, que sigue siendo el de la corrida.
    esquema = app.openapi()["components"]["schemas"]["ErrorRespuesta"]

    assert set(esquema["properties"]) == CAMPOS_DE_ERROR
    assert esquema["required"] == ["codigo", "mensaje"]
    run_id = esquema["properties"]["run_id"]
    assert run_id["description"] == "La corrida con la que tiene que ver el error, si hay una."


def test_los_esquemas_territoriales_no_atan_el_vocabulario_a_territorial_v1(app):
    esquemas = app.openapi()["components"]["schemas"]

    # Los campos exactos de cada respuesta, sin ids internos.
    assert set(esquemas["EjecucionTerritorialRespuesta"]["properties"]) == CAMPOS_DE_EJECUCION
    assert set(esquemas["ResultadoTerritorialRespuesta"]["properties"]) == CAMPOS_DE_MUNICIPIO
    assert set(esquemas["MotivoTerritorialRespuesta"]["properties"]) == {"codigo", "campo", "valor"}
    assert set(esquemas["PaginaEjecucionesTerritoriales"]["properties"]) == CAMPOS_DEL_HISTORIAL
    assert set(esquemas["PaginaMunicipios"]["properties"]) == CAMPOS_DE_LOS_MUNICIPIOS
    # El vocabulario de un municipio es texto, sin el catalogo de territorial/v1: la API sirve el
    # historial de cualquier version.
    for esquema, campo in [
        ("ResultadoTerritorialRespuesta", "carga"),
        ("MotivoTerritorialRespuesta", "codigo"),
    ]:
        propiedad = esquemas[esquema]["properties"][campo]
        assert propiedad["type"] == "string"
        assert "enum" not in propiedad and "$ref" not in propiedad
    # El estado de la ejecucion si es un catalogo, el del modelo.
    estado = esquemas["EjecucionTerritorialRespuesta"]["properties"]["estado"]
    assert estado["$ref"].endswith("/EstadoTerritorial")
    # La clave del territorio se deriva al responder: la base no la guarda.
    assert "clave_territorio" not in ResultadoTerritorial.__table__.columns


def test_los_ejemplos_territoriales_son_respuestas_posibles():
    # Traen cada campo de la respuesta; el OpenAPI omite los que valen null.
    campos = set(
        EjecucionTerritorialRespuesta.model_json_schema(mode="serialization")["properties"]
    )
    ejemplos = (
        EJEMPLO_EJECUCION_TERRITORIAL,
        EJEMPLO_EJECUCION_TERRITORIAL_FALLIDA,
        EJEMPLO_EJECUCION_TERRITORIAL_EN_PROCESO,
    )
    assert all(set(ejemplo) == campos for ejemplo in ejemplos)
    fallida = EJEMPLO_EJECUCION_TERRITORIAL_FALLIDA
    assert (fallida["estado"], fallida["territorios_publicados"]) == ("FALLIDA", 0)
    en_proceso = EJEMPLO_EJECUCION_TERRITORIAL_EN_PROCESO
    assert (en_proceso["estado"], en_proceso["terminada_en"]) == ("EN_PROCESO", None)
    # Y el municipio del ejemplo es lo que territorial/v1 publica de verdad con esos agregados.
    ejemplo = ResultadoTerritorialRespuesta(**EJEMPLO_MUNICIPIO).model_dump(mode="json")
    assert ejemplo == EJEMPLO_MUNICIPIO
    assert [ejemplo] == [_como_se_sirve(r) for r in priorizar_territorios(_entradas([ejemplo]))]
