"""El servicio de decision contra PostgreSQL: todas las decisiones de una corrida, o ninguna.

Las corridas se publican por el flujo real de la fase 1 y se deciden con el servicio completo: por
lotes, con todas las decisiones en una sola transaccion, FALLIDA con rastro ante cualquier fallo, un
solo worker por ejecucion, y con la idempotencia que garantiza el indice unico parcial y no solo la
revision previa. Las pruebas de la ultima seccion no tocan la base.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest
from psycopg.errors import LockNotAvailable
from sqlalchemy import event
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import func, select

from motor_cartera.contratos import VERSION_CONTRATO
from motor_cartera.db.modelos import (
    Corrida,
    Cuenta,
    DecisionCuenta,
    EjecucionDecision,
    EstadoCorrida,
    EstadoDecision,
)
from motor_cartera.db.sesion import crear_motor, sesion
from motor_cartera.decision import ejecuciones
from motor_cartera.decision.ejecuciones import (
    CorridaNoDecidible,
    DecisionYaGenerada,
    abrir_ejecucion,
    decidir_corrida,
    ejecutar_decision,
)
from motor_cartera.decision.reglas import (
    VERSION_REGLAS_DECISION,
    EntradaDecision,
    SegmentoMora,
    decidir_cuenta,
)
from motor_cartera.generador.sintetico import generar_archivo
from motor_cartera.ingesta.corridas import abrir_corrida, ingerir_archivo

en_la_base = pytest.mark.usefixtures("bd")

CORTE = date(2026, 9, 30)


def _publicar(tmp_path: Path, cartera: pd.DataFrame, nombre: str = "cartera.csv") -> Corrida:
    """Publica `cartera` como CSV, por el flujo real de la fase 1."""
    ruta = tmp_path / nombre
    cartera.to_csv(ruta, index=False)
    corrida = ingerir_archivo(ruta)
    assert corrida.estado == EstadoCorrida.EXITOSA, corrida.detalle
    return corrida


def _sintetica(tmp_path: Path, nombre: str = "cartera.csv", n: int = 200) -> Corrida:
    """Una cartera del generador, publicada. El formato sale de la extension de `nombre`."""
    corrida = ingerir_archivo(generar_archivo(tmp_path / nombre, n=n, semilla=1, fecha_corte=CORTE))
    assert corrida.estado == EstadoCorrida.EXITOSA, corrida.detalle
    return corrida


def _con_saldo_alto(cartera: pd.DataFrame) -> pd.DataFrame:
    """La cartera de conftest y una cuenta mas, que pasa el umbral de saldo: asi tambien sale
    SALDO_ALTO, con el saldo tal como lo devuelve la base."""
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


def _registrar_ejecucion(corrida_id: int, version: str = VERSION_REGLAS_DECISION) -> int:
    """Una ejecucion EN_PROCESO insertada a mano, sin pasar por abrir_ejecucion."""
    with sesion() as s:
        ejecucion = EjecucionDecision(corrida_id=corrida_id, version_reglas=version)
        s.add(ejecucion)
        s.commit()
        return ejecucion.id


def _ejecucion(ejecucion_id: int) -> EjecucionDecision:
    with sesion() as s:
        return s.get_one(EjecucionDecision, ejecucion_id)


def _ejecuciones(corrida_id: int) -> list[EjecucionDecision]:
    with sesion() as s:
        return list(
            s.exec(
                select(EjecucionDecision)
                .where(EjecucionDecision.corrida_id == corrida_id)
                .order_by(EjecucionDecision.id)
            ).all()
        )


def _corrida(corrida_id: int) -> dict:
    """La corrida completa, para compararla antes y despues."""
    with sesion() as s:
        return s.get_one(Corrida, corrida_id).model_dump()


def _cuantas_decisiones(ejecucion_id: int) -> int:
    with sesion() as s:
        return s.exec(
            select(func.count())
            .select_from(DecisionCuenta)
            .where(DecisionCuenta.ejecucion_decision_id == ejecucion_id)
        ).one()


def _decisiones(ejecucion_id: int) -> dict[str, tuple]:
    """Lo que publico una ejecucion, por cliente: segmento, prioridad, canal y motivos."""
    with sesion() as s:
        filas = s.exec(
            select(
                Cuenta.cliente_unico,
                DecisionCuenta.segmento,
                DecisionCuenta.prioridad,
                DecisionCuenta.canal_recomendado,
                DecisionCuenta.motivos,
            )
            .select_from(DecisionCuenta)
            .join(Cuenta, DecisionCuenta.cuenta_id == Cuenta.id)
            .where(DecisionCuenta.ejecucion_decision_id == ejecucion_id)
        ).all()
    return {cliente: tuple(resto) for cliente, *resto in filas}


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


# --- la decision de punta a punta --------------------------------------------------------------


@en_la_base
def test_una_corrida_publicada_se_decide_de_punta_a_punta(tmp_path, cartera_valida):
    # I1. La decision exacta de cada cuenta, motivos incluidos, tal como quedo guardada.
    corrida = _publicar(tmp_path, _con_saldo_alto(cartera_valida))

    ejecucion = decidir_corrida(corrida.id)

    assert ejecucion.estado == EstadoDecision.EXITOSA
    assert ejecucion.corrida_id == corrida.id
    assert ejecucion.version_reglas == VERSION_REGLAS_DECISION
    assert (ejecucion.cuentas_evaluadas, ejecucion.cuentas_decididas) == (4, 4)
    assert ejecucion.detalle == "Se decidieron 4 cuentas con decision/v1."
    assert ejecucion.terminada_en >= ejecucion.iniciada_en
    decisiones = _decisiones(ejecucion.id)
    # El canal recomendado sale de la prioridad y no es una copia del canal de la cartera: en las
    # cuatro cuentas es otro.
    assert decisiones == {
        # 0 dias, saldo 1500.50, canal de la cartera TELEFONICA
        "CU00000001": (
            "AL_CORRIENTE",
            "BAJA",
            "DIGITAL",
            [
                {"codigo": "SIN_MORA", "campo": "dias_atraso", "valor": "0"},
                {"codigo": "PRIORIDAD_BAJA", "campo": "prioridad", "valor": "BAJA"},
                {"codigo": "CANAL_DIGITAL", "campo": "canal_recomendado", "valor": "DIGITAL"},
            ],
        ),
        # 45 dias, saldo 23000.00, canal de la cartera CAMPO
        "CU00000002": (
            "MORA_MEDIA",
            "ALTA",
            "TELEFONICA",
            [
                {"codigo": "MORA_31_90", "campo": "dias_atraso", "valor": "45"},
                {"codigo": "PRIORIDAD_ALTA", "campo": "prioridad", "valor": "ALTA"},
                {"codigo": "CANAL_TELEFONICA", "campo": "canal_recomendado", "valor": "TELEFONICA"},
            ],
        ),
        # 190 dias, saldo 780.25, canal de la cartera DIGITAL
        "CU00000003": (
            "MORA_ALTA",
            "MUY_ALTA",
            "CAMPO",
            [
                {"codigo": "MORA_91_MAS", "campo": "dias_atraso", "valor": "190"},
                {"codigo": "PRIORIDAD_MUY_ALTA", "campo": "prioridad", "valor": "MUY_ALTA"},
                {"codigo": "CANAL_CAMPO", "campo": "canal_recomendado", "valor": "CAMPO"},
            ],
        ),
        # 12 dias, saldo 62000.00, canal de la cartera CAMPO: el saldo sube la prioridad un nivel
        "CU00000004": (
            "MORA_TEMPRANA",
            "ALTA",
            "TELEFONICA",
            [
                {"codigo": "MORA_1_30", "campo": "dias_atraso", "valor": "12"},
                {"codigo": "SALDO_ALTO", "campo": "saldo_total", "valor": "62000.00"},
                {"codigo": "PRIORIDAD_ALTA", "campo": "prioridad", "valor": "ALTA"},
                {"codigo": "CANAL_TELEFONICA", "campo": "canal_recomendado", "valor": "TELEFONICA"},
            ],
        ),
    }
    # Los motivos vuelven de la base como JSON de texto: el valor de los dias tambien es texto.
    assert all(_json_plano(motivos) for *_, motivos in decisiones.values())


@en_la_base
def test_cada_cuenta_de_la_corrida_tiene_exactamente_una_decision(tmp_path):
    # I2. Con lotes que no dividen la cartera: 12 lotes llenos y uno de 8.
    corrida = _sintetica(tmp_path, n=200)

    ejecucion = decidir_corrida(corrida.id, tamano_lote=16)

    with sesion() as s:
        cuentas = s.exec(select(Cuenta.id).where(Cuenta.corrida_id == corrida.id)).all()
        decididas = s.exec(
            select(DecisionCuenta.cuenta_id).where(
                DecisionCuenta.ejecucion_decision_id == ejecucion.id
            )
        ).all()
    assert ejecucion.estado == EstadoDecision.EXITOSA
    assert len(decididas) == len(cuentas) == 200
    assert ejecucion.cuentas_evaluadas == ejecucion.cuentas_decididas == 200
    # Y son las mismas: ninguna cuenta sin decision, ninguna decidida dos veces.
    assert sorted(decididas) == sorted(cuentas)


@en_la_base
def test_lo_guardado_es_exactamente_lo_que_decide_el_nucleo(tmp_path):
    # I3. Cada decision guardada, contra las reglas puras aplicadas otra vez a su cuenta.
    corrida = _sintetica(tmp_path, n=300)

    ejecucion = decidir_corrida(corrida.id)

    with sesion() as s:
        filas = s.exec(
            select(
                Cuenta.dias_atraso,
                Cuenta.saldo_total,
                DecisionCuenta.segmento,
                DecisionCuenta.prioridad,
                DecisionCuenta.canal_recomendado,
                DecisionCuenta.motivos,
            )
            .select_from(DecisionCuenta)
            .join(Cuenta, DecisionCuenta.cuenta_id == Cuenta.id)
            .where(DecisionCuenta.ejecucion_decision_id == ejecucion.id)
        ).all()
    assert len(filas) == 300
    for dias, saldo, segmento, prioridad, canal, motivos in filas:
        esperado = decidir_cuenta(EntradaDecision(dias_atraso=dias, saldo_total=saldo))
        assert (segmento, prioridad, canal) == (
            esperado.segmento,
            esperado.prioridad,
            esperado.canal_recomendado,
        )
        assert motivos == [asdict(motivo) for motivo in esperado.motivos]
    # La cartera pasa por los cuatro segmentos y por el umbral de saldo: la comparacion cubre todas
    # las reglas, no solo las faciles.
    assert {fila.segmento for fila in filas} == set(SegmentoMora)
    assert any(motivo["codigo"] == "SALDO_ALTO" for fila in filas for motivo in fila.motivos)


# --- una corrida se decide con exito una sola vez por version ----------------------------------


@en_la_base
def test_una_corrida_decidida_no_se_decide_otra_vez_con_la_misma_version(tmp_path, cartera_valida):
    # I4.
    corrida = _publicar(tmp_path, cartera_valida)
    primera = decidir_corrida(corrida.id)

    with pytest.raises(DecisionYaGenerada, match="ya se decidio con decision/v1") as exc:
        decidir_corrida(corrida.id)

    assert exc.value.previa.id == primera.id
    assert exc.value.previa.estado == EstadoDecision.EXITOSA
    # La revision amable no deja ni el intento: sigue habiendo una sola ejecucion, con sus
    # decisiones.
    assert [e.id for e in _ejecuciones(corrida.id)] == [primera.id]
    assert _cuantas_decisiones(primera.id) == 3


@en_la_base
def test_una_ejecucion_de_otra_version_no_se_decide_con_estas_reglas(tmp_path, cartera_valida):
    # I5. Que el esquema guarde varias versiones ya lo prueba la migracion. Aqui, que una ejecucion
    # registrada para otras reglas no se decide con las de decision/v1.
    corrida = _publicar(tmp_path, cartera_valida)
    ejecucion_id = _registrar_ejecucion(corrida.id, version="decision/v2")

    ejecutar_decision(ejecucion_id)

    ejecucion = _ejecucion(ejecucion_id)
    assert ejecucion.estado == EstadoDecision.FALLIDA
    assert ejecucion.version_reglas == "decision/v2"  # la que pidio, no la del codigo
    assert (ejecucion.cuentas_evaluadas, ejecucion.cuentas_decididas) == (0, 0)
    assert ejecucion.detalle == (
        "La ejecucion pide las reglas decision/v2 y este servicio solo decide con decision/v1; "
        "no se decidio ninguna cuenta."
    )
    assert _cuantas_decisiones(ejecucion_id) == 0
    assert _corrida(corrida.id)["estado"] == EstadoCorrida.EXITOSA


# --- un fallo no publica nada ----------------------------------------------------------------


@en_la_base
def test_un_fallo_en_la_tercera_cuenta_no_deja_ninguna_decision(
    tmp_path, cartera_valida, monkeypatch, caplog
):
    # I6. Con lotes de dos, las dos primeras decisiones ya se insertaron cuando falla la tercera.
    corrida = _publicar(tmp_path, cartera_valida)
    antes = _corrida(corrida.id)
    llamadas = []

    def falla_en_la_tercera(entrada):
        llamadas.append(entrada)
        if len(llamadas) == 3:
            raise RuntimeError("falla simulada en la tercera cuenta")
        return decidir_cuenta(entrada)

    monkeypatch.setattr(ejecuciones, "decidir_cuenta", falla_en_la_tercera)

    fallida = decidir_corrida(corrida.id, tamano_lote=2)

    assert fallida.estado == EstadoDecision.FALLIDA
    assert (fallida.cuentas_evaluadas, fallida.cuentas_decididas) == (2, 0)
    assert fallida.terminada_en is not None
    assert _cuantas_decisiones(fallida.id) == 0
    assert _corrida(corrida.id) == antes  # sigue EXITOSA, sin un solo cambio
    # El detalle no lleva la traza ni el mensaje del error; la bitacora si.
    assert fallida.detalle == "Error interno (RuntimeError); ver la bitacora."
    (registro,) = [r for r in caplog.records if r.name == ejecuciones.__name__ and r.exc_info]
    assert registro.exc_info[0] is RuntimeError
    assert "falla simulada en la tercera cuenta" in str(registro.exc_info[1])

    # Con el motor de vuelta, la misma corrida se reintenta: una FALLIDA no cuenta.
    monkeypatch.setattr(ejecuciones, "decidir_cuenta", decidir_cuenta)

    reintento = decidir_corrida(corrida.id, tamano_lote=2)

    assert reintento.estado == EstadoDecision.EXITOSA
    assert reintento.id != fallida.id
    assert _cuantas_decisiones(reintento.id) == 3
    assert _ejecucion(fallida.id).estado == EstadoDecision.FALLIDA


@en_la_base
def test_un_fallo_despues_de_insertar_todo_y_antes_del_commit_revierte_todo(
    tmp_path, cartera_valida, monkeypatch
):
    # I7. El cierre ya conto las decisiones y envio el EXITOSA; falla justo antes del commit.
    corrida = _publicar(tmp_path, cartera_valida)
    cerrar = ejecuciones._cerrar
    vistas = []

    def cierra_y_falla(s, ejecucion, evaluadas):
        cerrar(s, ejecucion, evaluadas)
        # Dentro de la transaccion ya estan las tres decisiones y la ejecucion ya dice EXITOSA.
        de_la_ejecucion = DecisionCuenta.ejecucion_decision_id == ejecucion.id
        decisiones = select(func.count()).select_from(DecisionCuenta).where(de_la_ejecucion)
        estado = select(EjecucionDecision.estado).where(EjecucionDecision.id == ejecucion.id)
        vistas.append((s.exec(decisiones).one(), s.exec(estado).one()))
        raise RuntimeError("falla simulada antes del commit")

    monkeypatch.setattr(ejecuciones, "_cerrar", cierra_y_falla)

    ejecucion = decidir_corrida(corrida.id)

    assert vistas == [(3, EstadoDecision.EXITOSA)]
    assert ejecucion.estado == EstadoDecision.FALLIDA
    assert (ejecucion.cuentas_evaluadas, ejecucion.cuentas_decididas) == (3, 0)
    assert ejecucion.detalle == "Error interno (RuntimeError); ver la bitacora."
    assert _cuantas_decisiones(ejecucion.id) == 0
    assert _corrida(corrida.id)["estado"] == EstadoCorrida.EXITOSA


@en_la_base
def test_si_el_lector_se_salta_una_cuenta_no_se_publica_nada(tmp_path, cartera_valida, monkeypatch):
    # I8. Un lector con un error: del primer lote se pierde la primera cuenta.
    corrida = _publicar(tmp_path, cartera_valida)
    leer = ejecuciones._lotes_cuentas

    def se_salta_una(s, corrida_id, tamano_lote):
        for numero, lote in enumerate(leer(s, corrida_id, tamano_lote)):
            yield lote[1:] if numero == 0 else lote

    monkeypatch.setattr(ejecuciones, "_lotes_cuentas", se_salta_una)

    ejecucion = decidir_corrida(corrida.id, tamano_lote=2)

    assert ejecucion.estado == EstadoDecision.FALLIDA
    # El motor si decidio todo lo que le llego: lo que falta lo descubre el conteo del cierre.
    assert (ejecucion.cuentas_evaluadas, ejecucion.cuentas_decididas) == (2, 0)
    assert ejecucion.detalle == (
        "Las decisiones estan incompletas: la corrida tiene 3 cuentas, se evaluaron 2 y se "
        "guardaron 2 decisiones; no se publico ninguna."
    )
    assert _cuantas_decisiones(ejecucion.id) == 0


@en_la_base
def test_si_el_lector_repite_una_cuenta_la_base_lo_impide_y_no_es_una_carrera(
    tmp_path, cartera_valida, monkeypatch
):
    # Otro lector con un error: entrega dos veces el primer lote. La segunda decision de la misma
    # cuenta la rechaza la restriccion unica de decision_cuenta, que no es el indice de exito: es un
    # error interno y no una carrera perdida, asi que no levanta DecisionYaGenerada.
    corrida = _publicar(tmp_path, cartera_valida)
    leer = ejecuciones._lotes_cuentas

    def repite_el_primero(s, corrida_id, tamano_lote):
        lotes = leer(s, corrida_id, tamano_lote)
        primero = next(lotes)
        yield primero
        yield primero
        yield from lotes

    monkeypatch.setattr(ejecuciones, "_lotes_cuentas", repite_el_primero)

    ejecucion = decidir_corrida(corrida.id, tamano_lote=2)

    assert ejecucion.estado == EstadoDecision.FALLIDA
    assert (ejecucion.cuentas_evaluadas, ejecucion.cuentas_decididas) == (4, 0)
    assert ejecucion.detalle == "Error interno al publicar; ver la bitacora del servicio."
    assert _cuantas_decisiones(ejecucion.id) == 0


# --- la carrera la decide la base --------------------------------------------------------------


@en_la_base
def test_la_base_no_admite_dos_ejecuciones_en_proceso_de_la_misma_corrida_y_version(
    tmp_path, cartera_valida
):
    # I9. Dos peticiones pueden pasar la revision amable en el mismo instante, antes de que
    # cualquiera termine. Se registran aqui a mano, sin revision, como si eso hubiera pasado: desde
    # la 0006 la segunda ya no entra. A lo mas un intento activo por corrida y version; otra
    # version si, y una FALLIDA no cuenta.
    corrida = _publicar(tmp_path, cartera_valida)
    activa = _registrar_ejecucion(corrida.id)

    with pytest.raises(IntegrityError, match="ux_ejecucion_decision_en_proceso"):
        _registrar_ejecucion(corrida.id)

    otra_version = _registrar_ejecucion(corrida.id, version="decision/v2")
    ejecutar_decision(otra_version)  # decision/v2 no se decide aqui: queda FALLIDA
    assert [(e.id, e.version_reglas, e.estado) for e in _ejecuciones(corrida.id)] == [
        (activa, "decision/v1", EstadoDecision.EN_PROCESO),
        (otra_version, "decision/v2", EstadoDecision.FALLIDA),
    ]
    _registrar_ejecucion(corrida.id, version="decision/v2")  # la FALLIDA ya no la bloquea


@en_la_base
def test_si_otra_ejecucion_publica_mientras_esta_decide_solo_una_publica(
    tmp_path, cartera_valida, monkeypatch
):
    # La garantia de no publicar dos veces sigue siendo el indice de las EXITOSA. Ya no puede haber
    # dos EN_PROCESO de la misma version que lleguen a cerrar a la vez: aqui la otra aparece
    # EXITOSA justo antes de que esta cierre, como la dejaria alguien que se salta la revision.
    corrida = _publicar(tmp_path, cartera_valida)
    ejecucion_id = _registrar_ejecucion(corrida.id)
    cerrar = ejecuciones._cerrar
    rival = []

    def otra_publica_primero(s, ejecucion, evaluadas):
        with sesion() as otra:
            ganadora = EjecucionDecision(
                corrida_id=corrida.id,
                version_reglas=VERSION_REGLAS_DECISION,
                estado=EstadoDecision.EXITOSA,
            )
            otra.add(ganadora)
            otra.commit()
            rival.append(ganadora.id)
        cerrar(s, ejecucion, evaluadas)

    monkeypatch.setattr(ejecuciones, "_cerrar", otra_publica_primero)

    with pytest.raises(DecisionYaGenerada) as exc:
        ejecutar_decision(ejecucion_id)

    assert exc.value.previa.id == rival[0]
    perdedora = _ejecucion(ejecucion_id)
    assert perdedora.estado == EstadoDecision.FALLIDA
    # Hizo el trabajo, evaluo todas sus cuentas, pero no publico ninguna.
    assert (perdedora.cuentas_evaluadas, perdedora.cuentas_decididas) == (3, 0)
    assert perdedora.detalle == (
        "Otra ejecucion publico las decisiones de esta corrida con decision/v1 mientras esta se "
        "procesaba; no se publican dos veces."
    )
    assert _cuantas_decisiones(ejecucion_id) == 0
    exitosas = [e.id for e in _ejecuciones(corrida.id) if e.estado == EstadoDecision.EXITOSA]
    assert exitosas == rival


# --- una ejecucion la decide un solo worker, y un estado terminal no cambia --------------------


@en_la_base
def test_otro_worker_no_puede_tomar_la_ejecucion_mientras_se_decide(
    tmp_path, cartera_valida, monkeypatch
):
    # Determinista, sin hilos ni esperas: desde dentro de la transaccion que decide, otra sesion
    # intenta tomar la misma ejecucion sin esperar. Recien tomada la fila y antes de insertar nada,
    # lo unico que la puede tener bloqueada es el FOR UPDATE con que se cargo.
    corrida = _publicar(tmp_path, cartera_valida)
    ejecucion_id = _registrar_ejecucion(corrida.id)
    comprobar = ejecuciones._comprobar_decidible
    durante = []

    def comprueba_mientras_otro_intenta(s, ejecucion):
        durante.append(_otro_worker_la_toma(ejecucion_id))
        comprobar(s, ejecucion)

    monkeypatch.setattr(ejecuciones, "_comprobar_decidible", comprueba_mientras_otro_intenta)

    antes = _otro_worker_la_toma(ejecucion_id)
    ejecutar_decision(ejecucion_id)
    despues = _otro_worker_la_toma(ejecucion_id)

    # Libre antes, tomada mientras se decide, y libre otra vez cuando el commit la suelta.
    assert (antes, durante, despues) == (True, [False], True)
    assert _ejecucion(ejecucion_id).estado == EstadoDecision.EXITOSA


@en_la_base
def test_un_fallo_que_llega_tarde_no_degrada_la_ejecucion_que_otro_worker_dejo_exitosa(
    tmp_path, cartera_valida, monkeypatch, caplog
):
    # La carrera que el bloqueo solo no cierra: el worker A falla y revierte, lo que suelta la fila;
    # el B la toma, la decide y la deja EXITOSA; y solo despues A registra su fallo. Se reproduce en
    # ese orden y sin hilos: B corre completo justo antes de que A llame a _fallar.
    corrida = _publicar(tmp_path, cartera_valida)
    ejecucion_id = _registrar_ejecucion(corrida.id)
    fallar = ejecuciones._fallar
    llamadas = []

    def falla_solo_en_a(entrada):
        llamadas.append(entrada)
        if len(llamadas) == 1:
            raise RuntimeError("falla simulada en el worker A")
        return decidir_cuenta(entrada)

    def b_termina_antes_de_que_a_registre_su_fallo(*argumentos):
        monkeypatch.setattr(ejecuciones, "_fallar", fallar)
        # El rollback de A ya solto la fila; si no, B esperaria a A para siempre.
        assert _otro_worker_la_toma(ejecucion_id)
        ejecutar_decision(ejecucion_id)  # el worker B
        fallar(*argumentos)  # y ahora si, el fallo de A

    monkeypatch.setattr(ejecuciones, "decidir_cuenta", falla_solo_en_a)
    monkeypatch.setattr(ejecuciones, "_fallar", b_termina_antes_de_que_a_registre_su_fallo)

    ejecutar_decision(ejecucion_id)  # el worker A

    ejecucion = _ejecucion(ejecucion_id)
    assert ejecucion.estado == EstadoDecision.EXITOSA
    assert (ejecucion.cuentas_evaluadas, ejecucion.cuentas_decididas) == (3, 3)
    assert ejecucion.detalle == "Se decidieron 3 cuentas con decision/v1."
    assert _cuantas_decisiones(ejecucion_id) == 3  # las de B siguen ahi
    # El fallo de A queda en la bitacora, no en la ejecucion.
    assert any(
        "ya termino EXITOSA; este fallo no la cambia" in registro.getMessage()
        for registro in caplog.records
    )


@en_la_base
def test_registrar_un_fallo_no_cambia_una_ejecucion_que_ya_termino(tmp_path, cartera_valida):
    # El mecanismo de fallo, directo sobre ejecuciones terminales: la EXITOSA no se degrada, la
    # FALLIDA conserva el registro de su fallo, y no se borra ninguna decision.
    corrida = _publicar(tmp_path, cartera_valida)
    exitosa = decidir_corrida(corrida.id)
    fallida = _registrar_ejecucion(corrida.id, version="decision/v2")
    ejecutar_decision(fallida)
    terminadas = [exitosa, _ejecucion(fallida)]
    antes = [ejecucion.model_dump() for ejecucion in terminadas]

    for ejecucion in terminadas:
        with sesion() as s:
            ejecuciones._fallar(s, ejecucion.id, ejecucion.decision_run_id, 99, "Un fallo tardio.")

    assert [_ejecucion(ejecucion.id).model_dump() for ejecucion in terminadas] == antes
    assert [fila["estado"] for fila in antes] == [EstadoDecision.EXITOSA, EstadoDecision.FALLIDA]
    assert _cuantas_decisiones(exitosa.id) == 3


@en_la_base
def test_una_fallida_no_se_reabre_aunque_el_motor_ya_funcione(
    tmp_path, cartera_valida, monkeypatch
):
    # Una FALLIDA de decision/v1 por un fallo del motor. Con el motor de vuelta, ejecutarla otra vez
    # la decidiria y la publicaria EXITOSA; la guarda lo impide sin leer una sola cuenta. Reintentar
    # es abrir una ejecucion nueva.
    corrida = _publicar(tmp_path, cartera_valida)
    ejecucion_id = _registrar_ejecucion(corrida.id)

    def revienta(_):
        raise RuntimeError("falla simulada")

    monkeypatch.setattr(ejecuciones, "decidir_cuenta", revienta)
    ejecutar_decision(ejecucion_id)
    monkeypatch.setattr(ejecuciones, "decidir_cuenta", decidir_cuenta)
    antes = _ejecucion(ejecucion_id).model_dump()

    with _sentencias() as sentencias:
        ejecutar_decision(ejecucion_id)

    assert antes["estado"] == EstadoDecision.FALLIDA
    assert _ejecucion(ejecucion_id).model_dump() == antes
    assert _cuantas_decisiones(ejecucion_id) == 0
    # Solo tomo la fila, con FOR UPDATE, y la encontro terminada: ni leyo cuentas ni escribio nada.
    (tomar,) = sentencias
    assert tomar.rstrip().endswith("FOR UPDATE")


# --- que se decide y que no se toca ----------------------------------------------------------


def _en_proceso(tmp_path: Path, cartera: pd.DataFrame) -> Corrida:
    ruta = tmp_path / "en_proceso.csv"
    cartera.to_csv(ruta, index=False)
    with sesion() as s:
        return abrir_corrida(s, origen=ruta.name, contenido=ruta.read_bytes())


def _rechazada(tmp_path: Path, cartera: pd.DataFrame) -> Corrida:
    cartera.loc[0, "saldo_total"] = -1.0  # con tolerancia cero, un rechazo detiene todo
    ruta = tmp_path / "rechazada.csv"
    cartera.to_csv(ruta, index=False)
    return ingerir_archivo(ruta, tolerancia=0.0)


def _fallida(tmp_path: Path, cartera: pd.DataFrame) -> Corrida:
    ruta = tmp_path / "malformado.csv"
    ruta.write_text("cliente,saldo\nCU00000001,10.00\n", encoding="utf-8")
    return ingerir_archivo(ruta)


@en_la_base
@pytest.mark.parametrize(
    ("crear", "estado"),
    [
        pytest.param(_en_proceso, EstadoCorrida.EN_PROCESO, id="EN_PROCESO"),
        pytest.param(_rechazada, EstadoCorrida.RECHAZADA, id="RECHAZADA"),
        pytest.param(_fallida, EstadoCorrida.FALLIDA, id="FALLIDA"),
    ],
)
def test_solo_se_decide_una_corrida_exitosa(tmp_path, cartera_valida, crear, estado):
    # I10.
    corrida = crear(tmp_path, cartera_valida)
    assert corrida.estado == estado

    with pytest.raises(CorridaNoDecidible, match="solo se decide una corrida EXITOSA") as exc:
        decidir_corrida(corrida.id)

    assert (exc.value.corrida.id, exc.value.corrida.estado) == (corrida.id, estado)
    with sesion() as s:
        assert s.exec(select(func.count()).select_from(EjecucionDecision)).one() == 0


@en_la_base
def test_una_ejecucion_solo_decide_las_cuentas_de_su_corrida(tmp_path, cartera_valida):
    # I11. Los mismos clientes en dos cortes. El lector avanza por cliente, de uno en uno, y no
    # debe pasarse a la otra corrida.
    enero = _publicar(tmp_path, cartera_valida, "enero.csv")
    febrero = _publicar(
        tmp_path,
        cartera_valida.assign(
            dias_atraso=[5, 75, 200], fecha_corte=pd.to_datetime(["2026-02-28"] * 3)
        ),
        "febrero.csv",
    )

    ejecucion = decidir_corrida(enero.id, tamano_lote=1)

    with sesion() as s:
        corridas = s.exec(
            select(Cuenta.corrida_id)
            .select_from(DecisionCuenta)
            .join(Cuenta, DecisionCuenta.cuenta_id == Cuenta.id)
            .where(DecisionCuenta.ejecucion_decision_id == ejecucion.id)
        ).all()
        de_febrero = s.exec(
            select(func.count())
            .select_from(DecisionCuenta)
            .join(Cuenta, DecisionCuenta.cuenta_id == Cuenta.id)
            .where(Cuenta.corrida_id == febrero.id)
        ).one()
    assert corridas == [enero.id] * 3
    assert de_febrero == 0
    assert _ejecuciones(febrero.id) == []


@en_la_base
def test_decidir_no_cambia_ninguna_cuenta(tmp_path):
    # I12. Las cuentas son datos de la cartera; la decision se guarda aparte.
    corrida = _sintetica(tmp_path, n=100)

    def cuentas() -> list[dict]:
        with sesion() as s:
            consulta = select(Cuenta).where(Cuenta.corrida_id == corrida.id).order_by(Cuenta.id)
            return [cuenta.model_dump() for cuenta in s.exec(consulta).all()]

    antes = cuentas()

    ejecucion = decidir_corrida(corrida.id)

    assert ejecucion.estado == EstadoDecision.EXITOSA
    assert cuentas() == antes
    # Y el canal de la cartera sigue siendo el suyo aunque el recomendado sea otro.
    decisiones = _decisiones(ejecucion.id)
    assert any(cuenta["canal"] != decisiones[cuenta["cliente_unico"]][2] for cuenta in antes)


@en_la_base
def test_el_tamano_del_lote_no_cambia_ninguna_decision(tmp_path):
    # I13. La misma cartera en tres archivos son tres corridas equivalentes; cada una se decide con
    # otro tamano de lote. Se compara por cliente, no por ids internos.
    resultados, firmas = {}, set()
    for nombre, tamano in (("c.csv", 1), ("c.xlsx", 7), ("c.zip", 1000)):
        corrida = _sintetica(tmp_path, nombre, n=50)
        firmas.add(corrida.firma_contenido)
        ejecucion = decidir_corrida(corrida.id, tamano_lote=tamano)
        assert ejecucion.estado == EstadoDecision.EXITOSA
        resultados[tamano] = _decisiones(ejecucion.id)

    assert len(firmas) == 1  # el mismo contenido
    assert len(resultados[1]) == 50
    assert resultados[1] == resultados[7] == resultados[1000]


# --- lo demas que tiene que cumplir ------------------------------------------------------------


@en_la_base
def test_la_ejecucion_queda_registrada_antes_de_decidir_nada(tmp_path, cartera_valida):
    corrida = _publicar(tmp_path, cartera_valida)

    with sesion() as s:
        ejecucion = abrir_ejecucion(s, s.get_one(Corrida, corrida.id))

    registrada = _ejecucion(ejecucion.id)  # desde otra sesion: ya esta confirmada
    assert registrada.estado == EstadoDecision.EN_PROCESO
    assert registrada.corrida_id == corrida.id
    assert registrada.version_reglas == VERSION_REGLAS_DECISION
    assert (registrada.cuentas_evaluadas, registrada.cuentas_decididas) == (0, 0)
    assert registrada.terminada_en is None
    assert registrada.detalle is None
    assert _cuantas_decisiones(ejecucion.id) == 0


@en_la_base
def test_una_corrida_exitosa_sin_cuentas_no_se_decide_con_exito():
    # La fase 1 nunca deja una corrida EXITOSA sin cuentas. Si aparece una, por corrupcion o a mano,
    # no se le publica una ejecucion vacia, y la corrida no se corrige.
    with sesion() as s:
        vacia = Corrida(
            origen="vacia.csv",
            firma="0" * 64,
            tolerancia_rechazo=0.05,
            version_contrato=VERSION_CONTRATO,
            estado=EstadoCorrida.EXITOSA,
        )
        s.add(vacia)
        s.commit()
        s.refresh(vacia)
    antes = _corrida(vacia.id)

    ejecucion = decidir_corrida(vacia.id)

    assert ejecucion.estado == EstadoDecision.FALLIDA
    assert (ejecucion.cuentas_evaluadas, ejecucion.cuentas_decididas) == (0, 0)
    assert ejecucion.detalle == (
        "La corrida no tiene cuentas; una ejecucion sin decisiones no se publica."
    )
    assert _cuantas_decisiones(ejecucion.id) == 0
    assert _corrida(vacia.id) == antes


@en_la_base
def test_una_corrida_que_deja_de_estar_publicada_antes_de_ejecutar_no_se_decide(
    tmp_path, cartera_valida
):
    corrida = _publicar(tmp_path, cartera_valida)
    with sesion() as s:
        ejecucion = abrir_ejecucion(s, s.get_one(Corrida, corrida.id))
    # Entre la transaccion que abre y la que decide, alguien cambia la corrida en la base.
    with sesion() as s:
        alterada = s.get_one(Corrida, corrida.id)
        alterada.estado = EstadoCorrida.FALLIDA
        s.add(alterada)
        s.commit()
    antes = _corrida(corrida.id)

    ejecutar_decision(ejecucion.id)

    fallida = _ejecucion(ejecucion.id)
    assert fallida.estado == EstadoDecision.FALLIDA
    assert (fallida.cuentas_evaluadas, fallida.cuentas_decididas) == (0, 0)
    assert fallida.detalle == (
        f"La corrida {corrida.run_id} ya no esta EXITOSA, esta FALLIDA; no se decide una cartera "
        "que no esta publicada."
    )
    assert _cuantas_decisiones(ejecucion.id) == 0
    assert _corrida(corrida.id) == antes  # ni se corrige ni se toca


@en_la_base
def test_una_ejecucion_que_ya_termino_no_se_vuelve_a_ejecutar(tmp_path, cartera_valida):
    # Un reintento del mismo trabajo, por ejemplo: ni la EXITOSA ni la FALLIDA cambian en nada.
    corrida = _publicar(tmp_path, cartera_valida)
    exitosa = decidir_corrida(corrida.id).id
    fallida = _registrar_ejecucion(corrida.id, version="decision/v2")
    ejecutar_decision(fallida)
    antes = {i: _ejecucion(i).model_dump() for i in (exitosa, fallida)}

    for ejecucion_id in antes:
        ejecutar_decision(ejecucion_id)

    assert {i: _ejecucion(i).model_dump() for i in antes} == antes
    assert (antes[exitosa]["estado"], antes[fallida]["estado"]) == (
        EstadoDecision.EXITOSA,
        EstadoDecision.FALLIDA,
    )
    assert _cuantas_decisiones(exitosa) == 3


@en_la_base
@pytest.mark.parametrize("tamano_lote", [10, 25])
def test_las_cuentas_se_leen_por_lote_y_no_una_por_una(tmp_path, tamano_lote):
    corrida = _sintetica(tmp_path, n=100)

    with _sentencias() as sentencias:
        ejecucion = decidir_corrida(corrida.id, tamano_lote=tamano_lote)

    assert ejecucion.cuentas_decididas == 100
    lotes = math.ceil(100 / tamano_lote)
    lecturas = [x for x in sentencias if re.match(r"\s*SELECT\b.*\bFROM cuenta\b", x, re.S)]
    inserciones = [x for x in sentencias if re.match(r"\s*INSERT INTO decision_cuenta\b", x)]
    # Una lectura por lote, mas la que confirma que ya no quedan y el conteo del cierre: crecen con
    # los lotes, no con las cuentas. Y un INSERT por lote, no uno por decision.
    assert len(lecturas) <= lotes + 2
    assert len(inserciones) == lotes


# --- sin base de datos -------------------------------------------------------------------------


def test_los_motivos_se_guardan_como_json_de_texto_plano():
    resultado = decidir_cuenta(EntradaDecision(dias_atraso=65, saldo_total=Decimal("62000.00")))

    fila = ejecuciones._fila_decision(7, 11, resultado)

    assert fila == {
        "ejecucion_decision_id": 7,
        "cuenta_id": 11,
        "segmento": "MORA_MEDIA",
        "prioridad": "MUY_ALTA",
        "canal_recomendado": "CAMPO",
        "motivos": [
            {"codigo": "MORA_31_90", "campo": "dias_atraso", "valor": "65"},
            {"codigo": "SALDO_ALTO", "campo": "saldo_total", "valor": "62000.00"},
            {"codigo": "PRIORIDAD_MUY_ALTA", "campo": "prioridad", "valor": "MUY_ALTA"},
            {"codigo": "CANAL_CAMPO", "campo": "canal_recomendado", "valor": "CAMPO"},
        ],
    }
    # El == de arriba no basta: un StrEnum es igual a su texto. El tipo exacto si los distingue.
    assert _json_plano(fila["motivos"])
    assert all(type(fila[campo]) is str for campo in ("segmento", "prioridad", "canal_recomendado"))


def test_la_ejecucion_se_toma_con_su_fila_bloqueada():
    sql = str(ejecuciones._bloqueada(7).compile(dialect=postgresql.psycopg.dialect()))

    # Solo la fila de esa ejecucion, bloqueada hasta que termine la transaccion que la toma: ni la
    # tabla entera ni un advisory lock.
    assert re.search(r"FROM ejecucion_decision\s+WHERE ejecucion_decision\.id = ", sql)
    assert sql.rstrip().endswith("FOR UPDATE")


@pytest.mark.parametrize("tamano_lote", [0, -1])
def test_un_tamano_de_lote_que_no_es_positivo_se_rechaza_sin_tocar_la_base(tamano_lote):
    with _sentencias() as sentencias:
        with pytest.raises(ValueError, match="tamano del lote"):
            decidir_corrida(1, tamano_lote=tamano_lote)
        with pytest.raises(ValueError, match="tamano del lote"):
            ejecutar_decision(1, tamano_lote=tamano_lote)
        with sesion() as s, pytest.raises(ValueError, match="tamano del lote"):
            next(ejecuciones._lotes_cuentas(s, 1, tamano_lote))

    # Ni una sentencia: no se abrio ninguna ejecucion, ni se leyo nada para descubrir el error.
    assert sentencias == []
