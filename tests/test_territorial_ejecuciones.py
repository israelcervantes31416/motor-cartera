"""El servicio territorial contra PostgreSQL: organizar por municipio las decisiones publicadas, sin
publicar nada a medias.

Las carteras se publican y se deciden por los flujos reales de la fase 1 y del Decision Engine, y se
organizan con el servicio completo: la agregacion en PostgreSQL, territorial/v1 sobre los agregados,
todos los resultados en una sola transaccion, FALLIDA con rastro ante cualquier fallo, un solo
worker por ejecucion y la idempotencia que garantiza el indice unico parcial. Las pruebas de la
ultima seccion no tocan la base.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pandas as pd
import pytest
from psycopg.errors import LockNotAvailable
from sqlalchemy import delete, event, text, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import func, select

from motor_cartera.db.modelos import (
    Corrida,
    Cuenta,
    DecisionCuenta,
    EjecucionDecision,
    EjecucionTerritorial,
    EstadoCorrida,
    EstadoDecision,
    EstadoTerritorial,
    ResultadoTerritorial,
)
from motor_cartera.db.sesion import crear_motor, sesion
from motor_cartera.decision.ejecuciones import decidir_corrida
from motor_cartera.decision.reglas import CANAL_POR_PRIORIDAD
from motor_cartera.ingesta.corridas import ingerir_archivo
from motor_cartera.territorial import ejecuciones
from motor_cartera.territorial.ejecuciones import (
    CANAL_CAMPO,
    VERSION_DECISION_COMPATIBLE,
    DecisionNoTerritorializable,
    TerritorialYaGenerado,
    abrir_ejecucion,
    ejecutar_territorial,
    territorializar_decision,
)
from motor_cartera.territorial.reglas import (
    VERSION_REGLAS_TERRITORIAL,
    EntradaTerritorio,
    priorizar_territorios,
)

en_la_base = pytest.mark.usefixtures("bd")

CORTE = "2026-09-30"

Cuentas = list[tuple[str, int, str, str]]
"""Una cartera de prueba: por cuenta, (clave del municipio, dias de atraso, saldo, canal de la
cartera). El canal recomendado lo decide decision/v1 con los dias y el saldo: CAMPO con 91 dias o
mas, o de 31 a 90 con saldo desde 50,000.00; si no, TELEFONICA o DIGITAL."""

# Ocho municipios de tres entidades. A la derecha, el canal que decision/v1 recomienda; la cuarta
# columna, el canal de la cartera, lo contradice a menudo y no debe contar.
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

AGREGADOS = [
    # (clave, cuentas_total, saldo_total, cuentas_campo, saldo_campo), en el orden de la consulta
    ("09002", 1, Decimal("100000.00"), 0, Decimal("0")),
    ("09005", 4, Decimal("12999.00"), 3, Decimal("3000.00")),
    ("15033", 2, Decimal("5100.00"), 1, Decimal("5000.00")),
    ("21001", 3, Decimal("20000.00"), 1, Decimal("5000.00")),
    ("21099", 2, Decimal("7000.00"), 0, Decimal("0")),
    ("21114", 7, Decimal("14000.00"), 5, Decimal("10000.00")),
    ("21156", 21, Decimal("20500.00"), 20, Decimal("20000.00")),
    ("21208", 4, Decimal("102000.00"), 3, Decimal("100000.00")),
]


def _motivo(codigo: str, valor: str) -> list[dict[str, str]]:
    return [{"codigo": codigo, "campo": "cuentas_campo", "valor": valor}]


PUBLICADOS = [
    # (clave, cuentas_total, saldo_total, cuentas_campo, saldo_campo, carga, lugar, motivos), con
    # los saldos en texto, para fijar tambien sus dos decimales
    ("21156", 21, "20500.00", 20, "20000.00", "ALTA", 1, _motivo("CARGA_CAMPO_20_MAS", "20")),
    ("21114", 7, "14000.00", 5, "10000.00", "MEDIA", 2, _motivo("CARGA_CAMPO_5_19", "5")),
    # Empatan en cuentas de campo: va primero el de mas saldo de campo, aunque su clave sea mayor.
    ("21208", 4, "102000.00", 3, "100000.00", "BAJA", 3, _motivo("CARGA_CAMPO_1_4", "3")),
    ("09005", 4, "12999.00", 3, "3000.00", "BAJA", 4, _motivo("CARGA_CAMPO_1_4", "3")),
    # Empatan en cuentas y saldo de campo: va primero la clave menor.
    ("15033", 2, "5100.00", 1, "5000.00", "BAJA", 5, _motivo("CARGA_CAMPO_1_4", "1")),
    ("21001", 3, "20000.00", 1, "5000.00", "BAJA", 6, _motivo("CARGA_CAMPO_1_4", "1")),
    # Los SIN_CARGA al final, por clave y sin lugar.
    ("09002", 1, "100000.00", 0, "0.00", "SIN_CARGA", None, _motivo("SIN_CARGA_CAMPO", "0")),
    ("21099", 2, "7000.00", 0, "0.00", "SIN_CARGA", None, _motivo("SIN_CARGA_CAMPO", "0")),
]

# Tres cuentas en dos municipios: 21001 va primero, con mas saldo de campo que 09002.
PEQUENA: Cuentas = [
    ("21001", 120, "3000.00", "DIGITAL"),  # CAMPO
    ("21001", 0, "7000.00", "CAMPO"),  # DIGITAL
    ("09002", 95, "1500.00", "CAMPO"),  # CAMPO
]


def _cartera(cuentas: Cuentas, corte: str = CORTE) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "cliente_unico": [f"CU{numero:08d}" for numero in range(1, len(cuentas) + 1)],
            "saldo_total": [saldo for _, _, saldo, _ in cuentas],
            "dias_atraso": [dias for _, dias, _, _ in cuentas],
            "producto": ["CONSUMO"] * len(cuentas),
            "canal": [canal for _, _, _, canal in cuentas],
            "cve_entidad": [clave[:2] for clave, _, _, _ in cuentas],
            "cve_municipio": [clave[2:] for clave, _, _, _ in cuentas],
            "fecha_corte": [corte] * len(cuentas),
        }
    )


def _publicar(
    tmp_path: Path, cuentas: Cuentas, nombre: str = "cartera.csv", corte: str = CORTE
) -> Corrida:
    """Publica la cartera como CSV, por el flujo real de la fase 1."""
    ruta = tmp_path / nombre
    _cartera(cuentas, corte).to_csv(ruta, index=False)
    corrida = ingerir_archivo(ruta)
    assert corrida.estado == EstadoCorrida.EXITOSA, corrida.detalle
    return corrida


def _decidida(
    tmp_path: Path, cuentas: Cuentas, nombre: str = "cartera.csv", corte: str = CORTE
) -> EjecucionDecision:
    """La cartera publicada y decidida con decision/v1, por el Decision Engine real."""
    fuente = decidir_corrida(_publicar(tmp_path, cuentas, nombre, corte).id)
    assert fuente.estado == EstadoDecision.EXITOSA, fuente.detalle
    return fuente


def _fuente_a_mano(
    corrida_id: int, estado: EstadoDecision, version: str = VERSION_DECISION_COMPATIBLE
) -> EjecucionDecision:
    """Una ejecucion de decision insertada a mano y sin decisiones: lo que el Decision Engine nunca
    deja como fuente valida."""
    with sesion() as s:
        fuente = EjecucionDecision(corrida_id=corrida_id, version_reglas=version, estado=estado)
        s.add(fuente)
        s.commit()
        s.refresh(fuente)
        return fuente


def _registrar(fuente_id: int, version: str = VERSION_REGLAS_TERRITORIAL) -> int:
    """Una ejecucion territorial EN_PROCESO insertada a mano, sin pasar por abrir_ejecucion."""
    with sesion() as s:
        ejecucion = EjecucionTerritorial(ejecucion_decision_id=fuente_id, version_reglas=version)
        s.add(ejecucion)
        s.commit()
        return ejecucion.id


def _ejecucion(ejecucion_id: int) -> EjecucionTerritorial:
    with sesion() as s:
        return s.get_one(EjecucionTerritorial, ejecucion_id)


def _territoriales(fuente_id: int | None = None) -> list[EjecucionTerritorial]:
    with sesion() as s:
        consulta = select(EjecucionTerritorial).order_by(EjecucionTerritorial.id)
        if fuente_id is not None:
            consulta = consulta.where(EjecucionTerritorial.ejecucion_decision_id == fuente_id)
        return list(s.exec(consulta).all())


def _cuantos_resultados(ejecucion_id: int | None = None) -> int:
    with sesion() as s:
        consulta = select(func.count()).select_from(ResultadoTerritorial)
        if ejecucion_id is not None:
            consulta = consulta.where(ResultadoTerritorial.ejecucion_territorial_id == ejecucion_id)
        return s.exec(consulta).one()


def _publicados(ejecucion_id: int) -> list[tuple]:
    """Lo que publico una ejecucion, en el orden de territorial/v1: primero por lugar de campo, y
    los que no tienen lugar, al final y por clave."""
    with sesion() as s:
        filas = s.exec(
            select(ResultadoTerritorial)
            .where(ResultadoTerritorial.ejecucion_territorial_id == ejecucion_id)
            .order_by(
                ResultadoTerritorial.posicion_campo.asc().nulls_last(),
                ResultadoTerritorial.cve_entidad,
                ResultadoTerritorial.cve_municipio,
            )
        ).all()
    return [
        (
            fila.cve_entidad + fila.cve_municipio,
            fila.cuentas_total,
            str(fila.saldo_total),
            fila.cuentas_campo,
            str(fila.saldo_campo),
            fila.carga,
            fila.posicion_campo,
            fila.motivos,
        )
        for fila in filas
    ]


def _json_plano(valor: object) -> bool:
    """Solo listas, diccionarios con llaves de texto y texto: ni enums, ni numeros, ni nulos."""
    if type(valor) is list:
        return all(_json_plano(v) for v in valor)
    if type(valor) is dict:
        return all(type(llave) is str and _json_plano(v) for llave, v in valor.items())
    return type(valor) is str


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


def _otro_worker_la_toma(ejecucion_id: int) -> bool:
    """Si otra sesion puede tomar la ejecucion en este momento: la misma consulta del servicio, con
    NOWAIT para no esperar. Si la toma, la suelta al salir."""
    with sesion() as otra:
        try:
            otra.exec(ejecuciones._bloqueada(ejecucion_id).with_for_update(nowait=True)).one()
        except OperationalError as exc:
            if not isinstance(exc.orig, LockNotAvailable):
                raise
            return False
        return True


def _en_otro_hilo(
    funcion: Callable[..., object], *argumentos: object
) -> tuple[threading.Thread, dict]:
    """Corre `funcion` en otro hilo, como otro worker con su propia conexion, y guarda lo que
    devuelve o lo que levanta."""
    salida: dict = {}

    def correr() -> None:
        try:
            salida["resultado"] = funcion(*argumentos)
        except Exception as exc:
            salida["error"] = exc

    hilo = threading.Thread(target=correr, daemon=True)
    hilo.start()
    return hilo, salida


ESPERANDO_LA_FILA = text(
    "SELECT count(*) FROM pg_stat_activity "
    "WHERE datname = current_database() AND wait_event_type = 'Lock' AND query LIKE :consulta"
)


def _alguien_espera_la_fila(limite: float = 10.0) -> bool:
    """Si dentro de `limite` segundos alguna sesion queda esperando el bloqueo de una fila de
    ejecucion_territorial: la consulta del servicio, detenida por el FOR UPDATE de otra. Pregunta
    cada 50 ms y no espera de mas."""
    hasta = time.monotonic() + limite
    while time.monotonic() < hasta:
        with crear_motor().connect() as conexion:
            consulta = {"consulta": "%FROM ejecucion_territorial%FOR UPDATE%"}
            if conexion.execute(ESPERANDO_LA_FILA, consulta).scalar_one():
                return True
        time.sleep(0.05)
    return False


def _fallida_por_el_motor(monkeypatch: pytest.MonkeyPatch, fuente_id: int) -> EjecucionTerritorial:
    """Una ejecucion territorial FALLIDA porque el nucleo fallo a la mitad."""

    def revienta(entradas):
        raise RuntimeError("falla simulada del nucleo")

    with monkeypatch.context() as parche:
        parche.setattr(ejecuciones, "priorizar_territorios", revienta)
        fallida = territorializar_decision(fuente_id)
    assert fallida.estado == EstadoTerritorial.FALLIDA
    return fallida


# --- de punta a punta ------------------------------------------------------------------------


@en_la_base
def test_una_decision_publicada_se_organiza_por_municipio_de_punta_a_punta(tmp_path):
    # La cartera se publica y se decide de verdad, y lo publicado se lee de PostgreSQL: cada
    # municipio con sus agregados, su carga, su lugar y su motivo, exactos.
    fuente = _decidida(tmp_path, CARTERA)

    ejecucion = territorializar_decision(fuente.id)

    assert ejecucion.estado == EstadoTerritorial.EXITOSA
    assert ejecucion.ejecucion_decision_id == fuente.id
    assert ejecucion.version_reglas == VERSION_REGLAS_TERRITORIAL
    assert (ejecucion.territorios_evaluados, ejecucion.territorios_publicados) == (8, 8)
    assert ejecucion.detalle == "Se organizaron 44 decisiones en 8 municipios con territorial/v1."
    assert ejecucion.terminada_en >= ejecucion.iniciada_en
    publicados = _publicados(ejecucion.id)
    assert publicados == PUBLICADOS
    # El vocabulario vuelve como texto plano, no como enums que se hacen pasar por texto.
    assert all(type(carga) is str and _json_plano(motivos) for *_, carga, _, motivos in publicados)


@en_la_base
def test_la_agregacion_cuenta_cada_municipio_en_postgresql(tmp_path):
    # Antes del nucleo: la consulta agregada, con las cuentas, los saldos y lo que es de campo de
    # cada municipio, exactos.
    fuente = _decidida(tmp_path, CARTERA)

    with sesion() as s:
        entradas = ejecuciones._agregar(s, s.get_one(EjecucionDecision, fuente.id))

    assert [
        (e.clave_territorio, e.cuentas_total, e.saldo_total, e.cuentas_campo, e.saldo_campo)
        for e in entradas
    ] == AGREGADOS
    # Los saldos llegan como Decimal, sin pasar por float, y sin campo la suma es 0 y no NULL.
    assert all(type(e.saldo_total) is Decimal and type(e.saldo_campo) is Decimal for e in entradas)
    assert all(type(e.cuentas_total) is int and type(e.cuentas_campo) is int for e in entradas)


@en_la_base
def test_lo_que_cuenta_como_campo_es_la_decision_y_no_el_canal_de_la_cartera(tmp_path):
    # Dos cuentas en las que el canal de la cartera contradice al recomendado. Si contara el de la
    # cartera, 09017 tendria carga y 21001 no.
    contradicciones: Cuentas = [
        ("09017", 0, "7000.00", "CAMPO"),  # la cartera dice CAMPO; decision/v1 recomienda DIGITAL
        ("21001", 120, "3000.00", "DIGITAL"),  # la cartera dice DIGITAL; decision/v1, CAMPO
    ]
    fuente = _decidida(tmp_path, contradicciones)
    with sesion() as s:
        canales = s.exec(
            select(Cuenta.cve_municipio, Cuenta.canal, DecisionCuenta.canal_recomendado)
            .select_from(DecisionCuenta)
            .join(Cuenta, DecisionCuenta.cuenta_id == Cuenta.id)
            .where(DecisionCuenta.ejecucion_decision_id == fuente.id)
            .order_by(Cuenta.cliente_unico)
        ).all()
    assert [tuple(fila) for fila in canales] == [
        ("017", "CAMPO", "DIGITAL"),
        ("001", "DIGITAL", "CAMPO"),
    ]

    ejecucion = territorializar_decision(fuente.id)

    assert _publicados(ejecucion.id) == [
        ("21001", 1, "3000.00", 1, "3000.00", "BAJA", 1, _motivo("CARGA_CAMPO_1_4", "1")),
        ("09017", 1, "7000.00", 0, "0.00", "SIN_CARGA", None, _motivo("SIN_CARGA_CAMPO", "0")),
    ]


@en_la_base
def test_organizar_no_cambia_ninguna_decision_ni_ninguna_cuenta(tmp_path):
    # El motor territorial no cambia ninguna decision individual: solo las reorganiza.
    fuente = _decidida(tmp_path, CARTERA)

    def fuente_completa() -> tuple:
        with sesion() as s:
            ejecucion = s.get_one(EjecucionDecision, fuente.id).model_dump()
            consulta = select(DecisionCuenta).where(
                DecisionCuenta.ejecucion_decision_id == fuente.id
            )
            decisiones = [
                d.model_dump() for d in s.exec(consulta.order_by(DecisionCuenta.id)).all()
            ]
            cuentas = [c.model_dump() for c in s.exec(select(Cuenta).order_by(Cuenta.id)).all()]
        return ejecucion, decisiones, cuentas

    antes = fuente_completa()

    ejecucion = territorializar_decision(fuente.id)

    assert ejecucion.estado == EstadoTerritorial.EXITOSA
    assert fuente_completa() == antes


# --- que se organiza y que no ----------------------------------------------------------------


@en_la_base
@pytest.mark.parametrize("estado", [EstadoDecision.EN_PROCESO, EstadoDecision.FALLIDA])
def test_solo_se_organiza_una_ejecucion_de_decision_exitosa(tmp_path, estado):
    corrida = _publicar(tmp_path, PEQUENA)
    fuente = _fuente_a_mano(corrida.id, estado)

    with pytest.raises(
        DecisionNoTerritorializable,
        match="solo se organizan por territorio las decisiones de una ejecucion EXITOSA",
    ) as exc:
        territorializar_decision(fuente.id)

    assert (exc.value.ejecucion_decision.id, exc.value.ejecucion_decision.estado) == (
        fuente.id,
        estado,
    )
    assert _territoriales() == []  # ni el intento queda registrado


@en_la_base
def test_territorial_v1_solo_organiza_decisiones_de_decision_v1(tmp_path):
    # Aunque este EXITOSA: decision/v2 no es la version con la que territorial/v1 es compatible.
    corrida = _publicar(tmp_path, PEQUENA)
    fuente = _fuente_a_mano(corrida.id, EstadoDecision.EXITOSA, version="decision/v2")

    with pytest.raises(
        DecisionNoTerritorializable,
        match="se decidio con decision/v2; territorial/v1 solo organiza decisiones de decision/v1",
    ):
        territorializar_decision(fuente.id)

    assert _territoriales() == []


@en_la_base
def test_la_ejecucion_queda_registrada_antes_de_calcular_nada(tmp_path):
    fuente = _decidida(tmp_path, PEQUENA)

    with sesion() as s:
        ejecucion = abrir_ejecucion(s, s.get_one(EjecucionDecision, fuente.id))

    registrada = _ejecucion(ejecucion.id)  # desde otra sesion: ya esta confirmada
    assert registrada.estado == EstadoTerritorial.EN_PROCESO
    assert registrada.ejecucion_decision_id == fuente.id
    assert registrada.version_reglas == VERSION_REGLAS_TERRITORIAL
    assert isinstance(registrada.territorial_run_id, UUID)
    assert (registrada.territorios_evaluados, registrada.territorios_publicados) == (0, 0)
    assert registrada.terminada_en is None
    assert registrada.detalle is None
    assert _cuantos_resultados(ejecucion.id) == 0


@en_la_base
def test_una_decision_ya_organizada_no_se_organiza_otra_vez(tmp_path):
    fuente = _decidida(tmp_path, PEQUENA)
    primera = territorializar_decision(fuente.id)

    with pytest.raises(TerritorialYaGenerado, match="ya se organizo con territorial/v1") as exc:
        territorializar_decision(fuente.id)

    assert exc.value.previa.id == primera.id
    assert exc.value.previa.estado == EstadoTerritorial.EXITOSA
    # La revision amable no deja ni el intento: sigue habiendo una sola ejecucion, con sus
    # resultados.
    assert [e.id for e in _territoriales(fuente.id)] == [primera.id]
    assert _cuantos_resultados(primera.id) == 2


@en_la_base
def test_una_ejecucion_de_otra_version_territorial_no_se_calcula_con_estas_reglas(tmp_path):
    fuente = _decidida(tmp_path, PEQUENA)
    ejecucion_id = _registrar(fuente.id, version="territorial/v2")

    ejecutar_territorial(ejecucion_id)

    ejecucion = _ejecucion(ejecucion_id)
    assert ejecucion.estado == EstadoTerritorial.FALLIDA
    assert ejecucion.version_reglas == "territorial/v2"  # la que pidio, no la del codigo
    assert (ejecucion.territorios_evaluados, ejecucion.territorios_publicados) == (0, 0)
    assert ejecucion.detalle == (
        "La ejecucion pide las reglas territorial/v2 y este servicio solo organiza con "
        "territorial/v1; no se publico ningun territorio."
    )
    assert _cuantos_resultados(ejecucion_id) == 0


@en_la_base
@pytest.mark.parametrize(
    ("cambio", "razon"),
    [
        pytest.param(
            {"estado": EstadoDecision.FALLIDA},
            "esta FALLIDA; solo se organizan por territorio las decisiones de una ejecucion "
            "EXITOSA.",
            id="ya-no-esta-exitosa",
        ),
        pytest.param(
            {"version_reglas": "decision/v2"},
            "se decidio con decision/v2; territorial/v1 solo organiza decisiones de decision/v1.",
            id="otra-version",
        ),
    ],
)
def test_una_fuente_que_cambia_entre_abrir_y_ejecutar_no_se_organiza(tmp_path, cambio, razon):
    fuente = _decidida(tmp_path, PEQUENA)
    with sesion() as s:
        ejecucion = abrir_ejecucion(s, s.get_one(EjecucionDecision, fuente.id))
    # Entre la transaccion que abre y la que publica, alguien cambia la fuente en la base.
    with sesion() as s:
        s.execute(
            update(EjecucionDecision).where(EjecucionDecision.id == fuente.id).values(**cambio)
        )
        s.commit()

    ejecutar_territorial(ejecucion.id)

    fallida = _ejecucion(ejecucion.id)
    assert fallida.estado == EstadoTerritorial.FALLIDA
    assert (fallida.territorios_evaluados, fallida.territorios_publicados) == (0, 0)
    assert fallida.detalle == (
        f"La ejecucion de decision {fuente.decision_run_id} {razon} No se publico ningun "
        "territorio."
    )
    assert _cuantos_resultados(ejecucion.id) == 0


# --- un fallo no publica nada ----------------------------------------------------------------


@en_la_base
def test_un_fallo_despues_de_insertar_y_antes_del_commit_revierte_todo(tmp_path, monkeypatch):
    # El cierre ya conto los resultados y envio el EXITOSA; falla justo antes del commit.
    fuente = _decidida(tmp_path, CARTERA)
    cerrar = ejecuciones._cerrar
    vistas = []

    def cierra_y_falla(s, ejecucion, *argumentos):
        cerrar(s, ejecucion, *argumentos)
        # Dentro de la transaccion ya estan los ocho municipios y la ejecucion ya dice EXITOSA.
        de_la_ejecucion = ResultadoTerritorial.ejecucion_territorial_id == ejecucion.id
        resultados = select(func.count()).select_from(ResultadoTerritorial).where(de_la_ejecucion)
        estado = select(EjecucionTerritorial.estado).where(EjecucionTerritorial.id == ejecucion.id)
        vistas.append((s.exec(resultados).one(), s.exec(estado).one()))
        raise RuntimeError("falla simulada antes del commit")

    monkeypatch.setattr(ejecuciones, "_cerrar", cierra_y_falla)

    ejecucion = territorializar_decision(fuente.id)

    assert vistas == [(8, EstadoTerritorial.EXITOSA)]
    assert ejecucion.estado == EstadoTerritorial.FALLIDA
    assert (ejecucion.territorios_evaluados, ejecucion.territorios_publicados) == (8, 0)
    assert ejecucion.detalle == "Error interno (RuntimeError); ver la bitacora."
    assert ejecucion.terminada_en is not None
    assert _cuantos_resultados(ejecucion.id) == 0


@en_la_base
def test_un_fallo_despues_del_nucleo_registra_lo_evaluado_y_no_publica(
    tmp_path, monkeypatch, caplog
):
    # El nucleo ya evaluo los ocho municipios; falla al preparar las filas que se van a guardar.
    fuente = _decidida(tmp_path, CARTERA)

    def revienta(ejecucion_id, resultado):
        raise RuntimeError("falla simulada al preparar las filas")

    monkeypatch.setattr(ejecuciones, "_fila_resultado", revienta)

    ejecucion = territorializar_decision(fuente.id)

    assert ejecucion.estado == EstadoTerritorial.FALLIDA
    assert (ejecucion.territorios_evaluados, ejecucion.territorios_publicados) == (8, 0)
    # El detalle no lleva la traza ni el mensaje del error; la bitacora si.
    assert ejecucion.detalle == "Error interno (RuntimeError); ver la bitacora."
    (registro,) = [r for r in caplog.records if r.name == ejecuciones.__name__ and r.exc_info]
    assert "falla simulada al preparar las filas" in str(registro.exc_info[1])
    assert _cuantos_resultados(ejecucion.id) == 0


@en_la_base
def test_una_agregacion_que_pierde_un_municipio_falla_antes_del_nucleo(tmp_path, monkeypatch):
    # Una agregacion con un error: pierde el primer municipio, 09002, con su unica decision.
    fuente = _decidida(tmp_path, CARTERA)
    agregar = ejecuciones._agregar
    priorizar = ejecuciones.priorizar_territorios
    evaluaciones = []

    def pierde_el_primero(s, decision):
        return agregar(s, decision)[1:]

    def anota(entradas):
        evaluaciones.append(entradas)
        return priorizar(entradas)

    monkeypatch.setattr(ejecuciones, "_agregar", pierde_el_primero)
    monkeypatch.setattr(ejecuciones, "priorizar_territorios", anota)

    ejecucion = territorializar_decision(fuente.id)

    assert evaluaciones == []  # el nucleo ni se llamo
    assert ejecucion.estado == EstadoTerritorial.FALLIDA
    assert (ejecucion.territorios_evaluados, ejecucion.territorios_publicados) == (0, 0)
    assert ejecucion.detalle == (
        "Los municipios agregados suman 43 decisiones y la ejecucion de decision tiene 44; no se "
        "publico ningun territorio."
    )
    assert _cuantos_resultados(ejecucion.id) == 0


@en_la_base
def test_si_el_nucleo_entrega_menos_municipios_que_los_agregados_no_se_publica_nada(
    tmp_path, monkeypatch
):
    # Un nucleo con un error, que pierde el ultimo municipio: lo descubre el conteo del cierre.
    fuente = _decidida(tmp_path, CARTERA)
    priorizar = ejecuciones.priorizar_territorios
    monkeypatch.setattr(
        ejecuciones, "priorizar_territorios", lambda entradas: priorizar(entradas)[:-1]
    )

    ejecucion = territorializar_decision(fuente.id)

    assert ejecucion.estado == EstadoTerritorial.FALLIDA
    assert (ejecucion.territorios_evaluados, ejecucion.territorios_publicados) == (7, 0)
    assert ejecucion.detalle == (
        "Los resultados estan incompletos: se agregaron 8 municipios, se evaluaron 7 y se "
        "guardaron 7; no se publico ninguno."
    )
    assert _cuantos_resultados(ejecucion.id) == 0


@en_la_base
def test_si_los_resultados_no_suman_las_decisiones_no_se_publica_nada(tmp_path, monkeypatch):
    # Otro nucleo con un error: le cuenta una decision de mas al primer municipio.
    fuente = _decidida(tmp_path, CARTERA)
    priorizar = ejecuciones.priorizar_territorios

    def cuenta_una_de_mas(entradas):
        primero, *resto = priorizar(entradas)
        return (replace(primero, cuentas_total=primero.cuentas_total + 1), *resto)

    monkeypatch.setattr(ejecuciones, "priorizar_territorios", cuenta_una_de_mas)

    ejecucion = territorializar_decision(fuente.id)

    assert ejecucion.estado == EstadoTerritorial.FALLIDA
    assert (ejecucion.territorios_evaluados, ejecucion.territorios_publicados) == (8, 0)
    assert ejecucion.detalle == (
        "Los resultados suman 45 decisiones y la ejecucion de decision tiene 44; no se publico "
        "ninguno."
    )
    assert _cuantos_resultados(ejecucion.id) == 0


@en_la_base
def test_si_un_municipio_se_repite_la_base_lo_impide_y_no_es_una_carrera(tmp_path, monkeypatch):
    # Un error que entrega dos veces el ultimo municipio, sin carga y sin lugar: lo rechaza la
    # restriccion unica de resultado_territorial, que no es el indice de exito. Es un error
    # interno y no una carrera perdida, asi que no levanta TerritorialYaGenerado.
    fuente = _decidida(tmp_path, CARTERA)
    priorizar = ejecuciones.priorizar_territorios

    def repite_el_ultimo(entradas):
        resultados = priorizar(entradas)
        return (*resultados, resultados[-1])

    monkeypatch.setattr(ejecuciones, "priorizar_territorios", repite_el_ultimo)

    ejecucion = territorializar_decision(fuente.id)

    assert ejecucion.estado == EstadoTerritorial.FALLIDA
    assert (ejecucion.territorios_evaluados, ejecucion.territorios_publicados) == (9, 0)
    assert ejecucion.detalle == "Error interno al publicar; ver la bitacora del servicio."
    assert _cuantos_resultados(ejecucion.id) == 0


def _decididas_de_mas(fuente_id: int) -> None:
    with sesion() as s:
        s.execute(
            update(EjecucionDecision)
            .where(EjecucionDecision.id == fuente_id)
            .values(cuentas_decididas=EjecucionDecision.cuentas_decididas + 1)
        )
        s.commit()


def _sin_una_decision(fuente_id: int) -> None:
    with sesion() as s:
        de_la_fuente = DecisionCuenta.ejecucion_decision_id == fuente_id
        una = s.exec(select(func.min(DecisionCuenta.id)).where(de_la_fuente)).one()
        s.execute(delete(DecisionCuenta).where(DecisionCuenta.id == una))
        s.commit()


@en_la_base
@pytest.mark.parametrize(
    ("alterar", "conteos"),
    [
        pytest.param(
            _decididas_de_mas,
            "dice que evaluo 3 cuentas y decidio 4, tiene 3 decisiones y 3",
            id="decididas-de-mas",
        ),
        pytest.param(
            _sin_una_decision,
            "dice que evaluo 3 cuentas y decidio 3, tiene 2 decisiones y 2",
            id="falta-una-decision",
        ),
    ],
)
def test_una_fuente_incompleta_no_se_organiza(tmp_path, alterar, conteos):
    fuente = _decidida(tmp_path, PEQUENA)
    alterar(fuente.id)

    ejecucion = territorializar_decision(fuente.id)

    assert ejecucion.estado == EstadoTerritorial.FALLIDA
    assert (ejecucion.territorios_evaluados, ejecucion.territorios_publicados) == (0, 0)
    assert ejecucion.detalle == (
        f"La ejecucion de decision esta incompleta: {conteos} son de cuentas de su corrida; no se "
        "publico ningun territorio."
    )
    assert _cuantos_resultados(ejecucion.id) == 0


@en_la_base
def test_una_decision_sobre_una_cuenta_de_otra_corrida_no_se_organiza(tmp_path):
    # La base deja que una decision apunte a cualquier cuenta que exista, aunque sea de otra
    # corrida. Aqui una decision de enero pasa a apuntar a una cuenta de febrero.
    fuente = _decidida(tmp_path, PEQUENA, "enero.csv")
    febrero = _publicar(tmp_path, PEQUENA, "febrero.csv", corte="2026-10-31")
    with sesion() as s:
        ajena = s.exec(
            select(Cuenta.id).where(Cuenta.corrida_id == febrero.id).order_by(Cuenta.id)
        ).first()
        de_la_fuente = DecisionCuenta.ejecucion_decision_id == fuente.id
        una = s.exec(select(func.min(DecisionCuenta.id)).where(de_la_fuente)).one()
        s.execute(update(DecisionCuenta).where(DecisionCuenta.id == una).values(cuenta_id=ajena))
        s.commit()

    ejecucion = territorializar_decision(fuente.id)

    assert ejecucion.estado == EstadoTerritorial.FALLIDA
    assert (ejecucion.territorios_evaluados, ejecucion.territorios_publicados) == (0, 0)
    assert ejecucion.detalle == (
        "La ejecucion de decision esta incompleta: dice que evaluo 3 cuentas y decidio 3, tiene 3 "
        "decisiones y 2 son de cuentas de su corrida; no se publico ningun territorio."
    )
    assert _cuantos_resultados(ejecucion.id) == 0


@en_la_base
def test_una_fuente_sin_decisiones_no_se_publica_vacia(tmp_path):
    # El Decision Engine nunca deja una ejecucion EXITOSA sin decisiones. Si aparece una, por
    # corrupcion o a mano, no se le publica un territorial vacio.
    corrida = _publicar(tmp_path, PEQUENA)
    fuente = _fuente_a_mano(corrida.id, EstadoDecision.EXITOSA)

    ejecucion = territorializar_decision(fuente.id)

    assert ejecucion.estado == EstadoTerritorial.FALLIDA
    assert (ejecucion.territorios_evaluados, ejecucion.territorios_publicados) == (0, 0)
    assert ejecucion.detalle == (
        "La ejecucion de decision no tiene decisiones; no se publica un territorial vacio."
    )
    assert _cuantos_resultados() == 0


@en_la_base
def test_una_agregacion_sin_municipios_no_se_publica_vacia(tmp_path, monkeypatch):
    # La coleccion vacia es valida para el nucleo, pero no se publica: es una regla del servicio.
    fuente = _decidida(tmp_path, PEQUENA)
    monkeypatch.setattr(ejecuciones, "_agregar", lambda s, decision: [])

    ejecucion = territorializar_decision(fuente.id)

    assert ejecucion.estado == EstadoTerritorial.FALLIDA
    assert (ejecucion.territorios_evaluados, ejecucion.territorios_publicados) == (0, 0)
    assert ejecucion.detalle == (
        "La agregacion no produjo ningun municipio; no se publica un territorial vacio."
    )
    assert _cuantos_resultados(ejecucion.id) == 0


@en_la_base
def test_un_municipio_que_no_cumple_territorial_v1_no_se_publica(tmp_path, caplog):
    # cartera/v1 acepta claves con digitos de otros alfabetos, como estos arabigo-indicos, y
    # territorial/v1 exige digitos ASCII. EntradaTerritorio es la barrera, y no se publica nada.
    fuente = _decidida(tmp_path, PEQUENA)
    with sesion() as s:
        s.execute(
            update(Cuenta)
            .where(Cuenta.cliente_unico == "CU00000003")
            .values(cve_entidad=chr(0x0660) + chr(0x0669))
        )
        s.commit()

    ejecucion = territorializar_decision(fuente.id)

    assert ejecucion.estado == EstadoTerritorial.FALLIDA
    assert (ejecucion.territorios_evaluados, ejecucion.territorios_publicados) == (0, 0)
    assert ejecucion.detalle == (
        "Los agregados de un municipio no cumplen territorial/v1; ver la bitacora. No se publico "
        "ningun territorio."
    )
    assert any("un agregado no cumple territorial/v1" in r.getMessage() for r in caplog.records)
    assert _cuantos_resultados(ejecucion.id) == 0


# --- un solo worker por ejecucion, y la carrera la decide la base ----------------------------


@en_la_base
def test_el_mismo_id_lo_procesa_un_solo_worker_y_el_otro_lo_encuentra_terminado(
    tmp_path, monkeypatch, caplog
):
    # Dos workers con la misma ejecucion, cada uno con su conexion. A la toma primero; B la pide
    # mientras A la procesa y se queda esperando el bloqueo de la fila. Cuando A publica y la
    # suelta, B la encuentra terminada: ni calcula, ni inserta, ni la degrada.
    fuente = _decidida(tmp_path, PEQUENA)
    ejecucion_id = _registrar(fuente.id)
    agregar = ejecuciones._agregar
    b: dict = {}

    def a_agrega_mientras_b_espera(s, decision):
        b["hilo"], b["salida"] = _en_otro_hilo(ejecutar_territorial, ejecucion_id)
        b["esperaba"] = _alguien_espera_la_fila()
        return agregar(s, decision)

    monkeypatch.setattr(ejecuciones, "_agregar", a_agrega_mientras_b_espera)

    try:
        ejecutar_territorial(ejecucion_id)  # el worker A
    finally:
        if "hilo" in b:
            b["hilo"].join(timeout=10)

    assert b["esperaba"] is True  # B espero el bloqueo de A, en la base
    assert not b["hilo"].is_alive()
    assert b["salida"] == {"resultado": None}  # y al soltarse, B termino sin error
    assert any(
        "ya termino EXITOSA; no se vuelve a ejecutar" in registro.getMessage()
        for registro in caplog.records
    )
    ejecucion = _ejecucion(ejecucion_id)
    assert ejecucion.estado == EstadoTerritorial.EXITOSA
    assert (ejecucion.territorios_evaluados, ejecucion.territorios_publicados) == (2, 2)
    assert _cuantos_resultados() == 2  # los de A, una sola vez


@en_la_base
def test_la_base_no_admite_dos_ejecuciones_en_proceso_de_la_misma_fuente_y_version(tmp_path):
    # Dos peticiones pueden pasar la revision amable en el mismo instante: se registran aqui a
    # mano, sin revision. Desde la 0006 la segunda ya no entra: a lo mas un intento activo por
    # fuente y version. Otra version si, y una que ya termino no cuenta.
    fuente = _decidida(tmp_path, PEQUENA)
    activa = _registrar(fuente.id)

    with pytest.raises(IntegrityError, match="ux_ejecucion_territorial_en_proceso"):
        _registrar(fuente.id)

    otra_version = _registrar(fuente.id, version="territorial/v2")
    ejecutar_territorial(otra_version)  # territorial/v2 no se calcula aqui: queda FALLIDA
    assert [(e.id, e.version_reglas, e.estado) for e in _territoriales(fuente.id)] == [
        (activa, "territorial/v1", EstadoTerritorial.EN_PROCESO),
        (otra_version, "territorial/v2", EstadoTerritorial.FALLIDA),
    ]
    _registrar(fuente.id, version="territorial/v2")  # la FALLIDA ya no la bloquea


@en_la_base
def test_si_otra_ejecucion_publica_mientras_esta_organiza_solo_una_publica(tmp_path, monkeypatch):
    # La garantia de no publicar dos veces sigue siendo el indice de las EXITOSA. Ya no puede haber
    # dos EN_PROCESO de la misma version que lleguen a cerrar a la vez: aqui la otra aparece
    # EXITOSA justo antes de que esta cierre, con su transaccion abierta y sus municipios ya
    # evaluados, como la dejaria alguien que se salta la revision.
    fuente = _decidida(tmp_path, CARTERA)
    ejecucion_id = _registrar(fuente.id)
    cerrar = ejecuciones._cerrar
    rival = []

    def otra_publica_primero(s, ejecucion, *argumentos):
        with sesion() as otra:
            ganadora = EjecucionTerritorial(
                ejecucion_decision_id=fuente.id,
                version_reglas=VERSION_REGLAS_TERRITORIAL,
                estado=EstadoTerritorial.EXITOSA,
            )
            otra.add(ganadora)
            otra.commit()
            rival.append(ganadora.id)
        cerrar(s, ejecucion, *argumentos)

    monkeypatch.setattr(ejecuciones, "_cerrar", otra_publica_primero)

    with pytest.raises(TerritorialYaGenerado) as exc:
        ejecutar_territorial(ejecucion_id)

    # La perdedora hizo el trabajo, pero no publico nada, y a quien la ejecuto le dice cual gano.
    assert exc.value.previa.id == rival[0]
    perdio = _ejecucion(ejecucion_id)
    assert perdio.estado == EstadoTerritorial.FALLIDA
    assert (perdio.territorios_evaluados, perdio.territorios_publicados) == (8, 0)
    assert perdio.detalle == (
        "Otra ejecucion publico los territorios de esta ejecucion de decision con territorial/v1 "
        "mientras esta se procesaba; no se publican dos veces."
    )
    assert _cuantos_resultados() == 0


@en_la_base
def test_un_fallo_que_llega_tarde_no_degrada_la_ejecucion_que_otro_worker_dejo_exitosa(
    tmp_path, monkeypatch, caplog
):
    # La carrera que el bloqueo solo no cierra: el worker A falla y revierte, lo que suelta la fila;
    # el B la toma, la publica y la deja EXITOSA; y solo despues A registra su fallo. Se reproduce
    # en ese orden y sin hilos: B corre completo justo antes de que A llame a _fallar.
    fuente = _decidida(tmp_path, PEQUENA)
    ejecucion_id = _registrar(fuente.id)
    fallar = ejecuciones._fallar
    priorizar = ejecuciones.priorizar_territorios
    llamadas = []

    def falla_solo_en_a(entradas):
        llamadas.append(entradas)
        if len(llamadas) == 1:
            raise RuntimeError("falla simulada en el worker A")
        return priorizar(entradas)

    def b_termina_antes_de_que_a_registre_su_fallo(*argumentos):
        monkeypatch.setattr(ejecuciones, "_fallar", fallar)
        # El rollback de A ya solto la fila; si no, B esperaria a A para siempre.
        assert _otro_worker_la_toma(ejecucion_id)
        ejecutar_territorial(ejecucion_id)  # el worker B
        fallar(*argumentos)  # y ahora si, el fallo de A

    monkeypatch.setattr(ejecuciones, "priorizar_territorios", falla_solo_en_a)
    monkeypatch.setattr(ejecuciones, "_fallar", b_termina_antes_de_que_a_registre_su_fallo)

    ejecutar_territorial(ejecucion_id)  # el worker A

    ejecucion = _ejecucion(ejecucion_id)
    assert ejecucion.estado == EstadoTerritorial.EXITOSA
    assert (ejecucion.territorios_evaluados, ejecucion.territorios_publicados) == (2, 2)
    assert ejecucion.detalle == "Se organizaron 3 decisiones en 2 municipios con territorial/v1."
    assert _cuantos_resultados(ejecucion_id) == 2  # los de B siguen ahi
    # El fallo de A queda en la bitacora, no en la ejecucion.
    assert any(
        "ya termino EXITOSA; este fallo no la cambia" in registro.getMessage()
        for registro in caplog.records
    )


@en_la_base
def test_registrar_un_fallo_no_cambia_una_ejecucion_que_ya_termino(tmp_path, monkeypatch):
    # El mecanismo de fallo, directo sobre ejecuciones terminales: la EXITOSA no se degrada, la
    # FALLIDA conserva el registro de su fallo, y no se borra ningun resultado.
    fuente = _decidida(tmp_path, PEQUENA)
    fallida = _fallida_por_el_motor(monkeypatch, fuente.id)
    exitosa = territorializar_decision(fuente.id)
    terminadas = [exitosa, fallida]
    antes = [_ejecucion(ejecucion.id).model_dump() for ejecucion in terminadas]

    for ejecucion in terminadas:
        with sesion() as s:
            ejecuciones._fallar(s, ejecucion.id, ejecucion.territorial_run_id, 99, "Fallo tardio.")

    assert [_ejecucion(ejecucion.id).model_dump() for ejecucion in terminadas] == antes
    assert [fila["estado"] for fila in antes] == [
        EstadoTerritorial.EXITOSA,
        EstadoTerritorial.FALLIDA,
    ]
    assert _cuantos_resultados(exitosa.id) == 2


@en_la_base
def test_una_ejecucion_que_ya_termino_no_se_vuelve_a_ejecutar(tmp_path, monkeypatch):
    # Un reintento del mismo trabajo, por ejemplo: ni la EXITOSA ni la FALLIDA cambian en nada.
    fuente = _decidida(tmp_path, PEQUENA)
    fallida = _fallida_por_el_motor(monkeypatch, fuente.id)
    exitosa = territorializar_decision(fuente.id)
    antes = {
        ejecucion.id: _ejecucion(ejecucion.id).model_dump() for ejecucion in (exitosa, fallida)
    }
    publicados = _publicados(exitosa.id)

    for ejecucion_id in antes:
        with _sentencias() as sentencias:
            ejecutar_territorial(ejecucion_id)
        # Solo tomo la fila, con FOR UPDATE, y la encontro terminada: ni leyo la fuente ni
        # escribio nada.
        (tomar,) = sentencias
        assert tomar.rstrip().endswith("FOR UPDATE")

    assert {i: _ejecucion(i).model_dump() for i in antes} == antes
    assert _publicados(exitosa.id) == publicados
    assert _cuantos_resultados(fallida.id) == 0


@en_la_base
def test_una_fallida_se_reintenta_con_otra_ejecucion_y_queda_en_la_historia(tmp_path, monkeypatch):
    fuente = _decidida(tmp_path, PEQUENA)
    fallida = _fallida_por_el_motor(monkeypatch, fuente.id)
    assert (fallida.territorios_evaluados, fallida.territorios_publicados) == (0, 0)
    assert fallida.detalle == "Error interno (RuntimeError); ver la bitacora."

    # Con el nucleo de vuelta, la misma fuente se reintenta: una FALLIDA no cuenta, y no se reabre.
    reintento = territorializar_decision(fuente.id)

    assert reintento.estado == EstadoTerritorial.EXITOSA
    assert reintento.id != fallida.id
    historia = [(e.id, e.estado, e.version_reglas) for e in _territoriales(fuente.id)]
    assert historia == [
        (fallida.id, EstadoTerritorial.FALLIDA, VERSION_REGLAS_TERRITORIAL),
        (reintento.id, EstadoTerritorial.EXITOSA, VERSION_REGLAS_TERRITORIAL),
    ]
    assert (_cuantos_resultados(fallida.id), _cuantos_resultados(reintento.id)) == (0, 2)


@en_la_base
def test_la_agregacion_es_una_sola_consulta_sin_importar_cuantas_cuentas(tmp_path):
    claves = ("21114", "21156", "09005", "15033")

    def cartera(n: int) -> Cuentas:
        # n cuentas en los mismos cuatro municipios: las impares, de campo; las pares, digitales.
        return [(claves[i % 4], 120 if i % 2 else 10, "1000.00", "DIGITAL") for i in range(n)]

    vistas = {}
    for n, corte in ((10, "2026-09-30"), (100, "2026-10-31")):
        fuente = _decidida(tmp_path, cartera(n), f"cartera_{n}.csv", corte)
        ejecucion_id = _registrar(fuente.id)
        with _sentencias() as sentencias:
            ejecutar_territorial(ejecucion_id)
        assert _ejecucion(ejecucion_id).estado == EstadoTerritorial.EXITOSA
        vistas[n] = sentencias

    for sentencias in vistas.values():
        agrupadas = [x for x in sentencias if "GROUP BY" in x]
        inserciones = [x for x in sentencias if x.lstrip().startswith("INSERT INTO resultado")]
        # Una sola agregacion, en PostgreSQL, y un solo INSERT para todos los municipios.
        assert len(agrupadas) == 1
        assert "FROM decision_cuenta JOIN cuenta" in agrupadas[0]
        assert len(inserciones) == 1
    # Diez veces mas cuentas, las mismas sentencias: nada se consulta cuenta por cuenta.
    assert len(vistas[10]) == len(vistas[100])


# --- sin base de datos -----------------------------------------------------------------------


def test_los_resultados_se_guardan_como_texto_plano():
    (resultado,) = priorizar_territorios(
        [EntradaTerritorio("21", "001", 12, Decimal("84000.50"), 8, Decimal("61000.25"))]
    )

    fila = ejecuciones._fila_resultado(7, resultado)

    assert fila == {
        "ejecucion_territorial_id": 7,
        "cve_entidad": "21",
        "cve_municipio": "001",
        "cuentas_total": 12,
        "saldo_total": Decimal("84000.50"),
        "cuentas_campo": 8,
        "saldo_campo": Decimal("61000.25"),
        "carga": "MEDIA",
        "posicion_campo": 1,
        "motivos": [{"codigo": "CARGA_CAMPO_5_19", "campo": "cuentas_campo", "valor": "8"}],
    }
    # El == de arriba no basta: un StrEnum es igual a su texto. El tipo exacto si los distingue.
    assert type(fila["carga"]) is str
    assert _json_plano(fila["motivos"])


def test_la_ejecucion_se_toma_con_su_fila_bloqueada():
    sql = " ".join(
        str(ejecuciones._bloqueada(7).compile(dialect=postgresql.psycopg.dialect())).split()
    )

    # Solo la fila de esa ejecucion, bloqueada hasta que termine la transaccion que la toma: ni la
    # tabla entera ni un advisory lock.
    assert "FROM ejecucion_territorial WHERE ejecucion_territorial.id = " in sql
    assert sql.endswith("FOR UPDATE")


def test_la_agregacion_agrupa_por_municipio_y_cuenta_como_campo_la_decision():
    sql = " ".join(
        str(ejecuciones._agregados(7, 3).compile(dialect=postgresql.psycopg.dialect())).split()
    )

    # Una sola consulta: las decisiones de la ejecucion, con su cuenta, solo si es de su corrida,
    # agrupadas por municipio.
    assert "FROM decision_cuenta JOIN cuenta ON decision_cuenta.cuenta_id = cuenta.id" in sql
    assert "decision_cuenta.ejecucion_decision_id = " in sql
    assert "cuenta.corrida_id = " in sql
    assert sql.endswith(
        "GROUP BY cuenta.cve_entidad, cuenta.cve_municipio "
        "ORDER BY cuenta.cve_entidad, cuenta.cve_municipio"
    )
    # Lo que cuenta como campo es el canal recomendado, en el conteo y en la suma, y nunca el
    # canal de la cartera.
    assert sql.count("FILTER (WHERE decision_cuenta.canal_recomendado = ") == 2
    assert re.search("(?<![a-z_])cuenta[.]canal(?![a-z_])", sql) is None


def test_territorial_v1_es_compatible_con_decision_v1_y_con_ninguna_otra():
    # Un literal, y no la version del codigo de decision: si el Decision Engine pasa a decision/v2,
    # territorial/v1 no lo sigue solo.
    assert VERSION_DECISION_COMPATIBLE == "decision/v1"
    assert not hasattr(ejecuciones, "VERSION_REGLAS_DECISION")
    # Y lo que cuenta como campo es un canal del vocabulario de decision/v1.
    assert CANAL_CAMPO == "CAMPO"
    assert CANAL_CAMPO in CANAL_POR_PRIORIDAD.values()
