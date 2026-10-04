"""Abrir una corrida o una ejecucion: un solo intento activo por fuente y version, y la apertura
dentro de la transaccion de quien llama.

Lo que garantiza que no haya dos intentos activos es el indice unico parcial de las EN_PROCESO; las
revisiones previas son la via amable, porque saben decir cual fue. Aqui se prueban las dos, y lo que
pasa cuando una apertura pierde la carrera entre la revision y el INSERT: choca con el indice dentro
de un SAVEPOINT, la transaccion de quien llama sigue usable, y la apertura vuelve a revisar. Para
perder la carrera sin hilos, la revision amable se ciega una vez; una prueba la pierde de verdad,
con otra transaccion que confirma mientras esta espera el indice.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest
from sqlalchemy import false, text, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import SQLModel, func, select

from motor_cartera.contratos import VERSION_CONTRATO
from motor_cartera.db.modelos import (
    Corrida,
    EjecucionDecision,
    EjecucionRuteo,
    EjecucionTerritorial,
    EstadoCorrida,
    ahora,
)
from motor_cartera.db.sesion import crear_motor, sesion
from motor_cartera.decision import ejecuciones as decision
from motor_cartera.generador.sintetico import generar_archivo
from motor_cartera.ingesta import corridas
from motor_cartera.ingesta.corridas import ArchivoDuplicado, abrir_corrida, firmar, ingerir_archivo
from motor_cartera.ruteo import ejecuciones as ruteo
from motor_cartera.territorial import ejecuciones as territorial

en_la_base = pytest.mark.usefixtures("bd")

CORTE = date(2026, 9, 30)


def _corrida(tmp_path: Path) -> int:
    ruta = generar_archivo(tmp_path / "c.csv", n=60, semilla=1, fecha_corte=CORTE)
    corrida = ingerir_archivo(ruta)
    assert corrida.estado == EstadoCorrida.EXITOSA
    return corrida.id


def _decision(tmp_path: Path) -> int:
    return decision.decidir_corrida(_corrida(tmp_path)).id


def _territorial(tmp_path: Path) -> int:
    return territorial.territorializar_decision(_decision(tmp_path)).id


@dataclass(frozen=True)
class Caso:
    """Un motor, como lo abre quien le pide una ejecucion."""

    modulo: ModuleType
    modelo: type[SQLModel]
    fuente: type[SQLModel]
    columna: str
    version: str
    publico: str
    publicar: Callable[[Path], int]
    en_proceso: type[Exception]
    ya_generada: type[Exception]
    indice: str

    def abrir(self, s, fuente_id: int, **opciones) -> SQLModel:
        return self.modulo.abrir_ejecucion(s, s.get_one(self.fuente, fuente_id), **opciones)

    def registrar(self, fuente_id: int, estado: str = "EN_PROCESO") -> int:
        """Una ejecucion insertada a mano, sin revision."""
        with sesion() as s:
            ejecucion = self.modelo(
                **{self.columna: fuente_id}, version_reglas=self.version, estado=estado
            )
            s.add(ejecucion)
            s.commit()
            return ejecucion.id

    def terminar(self, ejecucion_id: int, estado: str) -> None:
        with sesion() as s:
            s.execute(
                update(self.modelo)
                .where(self.modelo.id == ejecucion_id)
                .values(estado=estado, terminada_en=ahora())
            )
            s.commit()

    def estados(self) -> list[tuple[int, str]]:
        with sesion() as s:
            filas = s.exec(select(self.modelo).order_by(self.modelo.id)).all()
            return [(fila.id, fila.estado) for fila in filas]


CASOS = [
    Caso(
        decision,
        EjecucionDecision,
        Corrida,
        "corrida_id",
        decision.VERSION_REGLAS_DECISION,
        "decision_run_id",
        _corrida,
        decision.DecisionEnProceso,
        decision.DecisionYaGenerada,
        "ux_ejecucion_decision_en_proceso",
    ),
    Caso(
        territorial,
        EjecucionTerritorial,
        EjecucionDecision,
        "ejecucion_decision_id",
        territorial.VERSION_REGLAS_TERRITORIAL,
        "territorial_run_id",
        _decision,
        territorial.TerritorialEnProceso,
        territorial.TerritorialYaGenerado,
        "ux_ejecucion_territorial_en_proceso",
    ),
    Caso(
        ruteo,
        EjecucionRuteo,
        EjecucionTerritorial,
        "ejecucion_territorial_id",
        ruteo.VERSION_REGLAS_RUTEO,
        "ruteo_run_id",
        _territorial,
        ruteo.RuteoEnProceso,
        ruteo.RuteoYaGenerado,
        "ux_ejecucion_ruteo_en_proceso",
    ),
]
POR_MOTOR = pytest.mark.parametrize("caso", CASOS, ids=["decision", "territorial", "ruteo"])


def _ciega(monkeypatch, modulo: ModuleType, revision: str, veces: int = 1) -> None:
    """La revision amable no ve nada las primeras `veces`: como si la otra apertura se hubiera
    confirmado justo despues de revisar."""
    original = getattr(modulo, revision)
    llamadas = []

    def revisa(*argumentos):
        llamadas.append(argumentos)
        consulta = original(*argumentos)
        return consulta.where(false()) if len(llamadas) <= veces else consulta

    monkeypatch.setattr(modulo, revision, revisa)


def _otra_corrida() -> Corrida:
    """Algo mas que quien llama escribe en la misma transaccion."""
    return Corrida(
        origen="otra.csv",
        firma=uuid4().hex * 2,
        tolerancia_rechazo=0.05,
        version_contrato=VERSION_CONTRATO,
    )


def _corridas_que_se_llaman(origen: str) -> int:
    with sesion() as s:
        return s.exec(
            select(func.count()).select_from(Corrida).where(Corrida.origen == origen)
        ).one()


# --- los tres motores ----------------------------------------------------------------------------


@en_la_base
@POR_MOTOR
def test_otra_ejecucion_en_proceso_impide_abrir_y_se_dice_cual(tmp_path, caso):
    fuente_id = caso.publicar(tmp_path)
    with sesion() as s:
        activa = caso.abrir(s, fuente_id)

    with sesion() as s, pytest.raises(caso.en_proceso) as exc:
        caso.abrir(s, fuente_id)

    assert exc.value.activa.id == activa.id
    assert str(getattr(activa, caso.publico)) in str(exc.value)
    assert caso.estados() == [(activa.id, "EN_PROCESO")]
    # Cuando la activa termina sin publicar, ya no estorba: una FALLIDA es historia.
    caso.terminar(activa.id, "FALLIDA")
    with sesion() as s:
        otra = caso.abrir(s, fuente_id)
    assert caso.estados() == [(activa.id, "FALLIDA"), (otra.id, "EN_PROCESO")]


@en_la_base
@POR_MOTOR
def test_una_ejecucion_exitosa_se_dice_antes_que_una_activa(tmp_path, caso):
    fuente_id = caso.publicar(tmp_path)
    exitosa = caso.registrar(fuente_id, "EXITOSA")
    caso.registrar(fuente_id)

    with sesion() as s, pytest.raises(caso.ya_generada) as exc:
        caso.abrir(s, fuente_id)

    assert exc.value.previa.id == exitosa


@en_la_base
@POR_MOTOR
def test_sin_confirmar_la_ejecucion_queda_en_la_transaccion_de_quien_llama(tmp_path, caso):
    fuente_id = caso.publicar(tmp_path)

    with sesion() as s:
        abierta = caso.abrir(s, fuente_id, confirmar=False)
        assert abierta.id is not None and abierta.estado == "EN_PROCESO"
        assert caso.estados() == []  # desde otra sesion: todavia no se confirma
        s.rollback()
    assert caso.estados() == []

    with sesion() as s:
        abierta = caso.abrir(s, fuente_id, confirmar=False)
        s.commit()
        abierta_id = abierta.id
    assert caso.estados() == [(abierta_id, "EN_PROCESO")]


@en_la_base
@POR_MOTOR
def test_la_apertura_que_pierde_la_carrera_deja_usable_la_transaccion(tmp_path, caso, monkeypatch):
    # La otra apertura se confirma justo despues de la revision amable: el INSERT choca con el
    # indice dentro de un SAVEPOINT, la apertura vuelve a revisar y ahora si la ve. Lo que quien
    # llama ya habia escrito en la transaccion sigue ahi, y se confirma.
    fuente_id = caso.publicar(tmp_path)
    rival = caso.registrar(fuente_id)
    _ciega(monkeypatch, caso.modulo, "_en_proceso")

    with sesion() as s:
        s.add(_otra_corrida())
        s.flush()
        with pytest.raises(caso.en_proceso) as exc:
            caso.abrir(s, fuente_id, confirmar=False)
        ganadora = exc.value.activa.id  # antes del commit, que la caduca
        s.commit()

    assert ganadora == rival
    assert _corridas_que_se_llaman("otra.csv") == 1
    assert caso.estados() == [(rival, "EN_PROCESO")]


@en_la_base
@POR_MOTOR
def test_si_la_que_gano_ya_termino_al_volver_a_revisar_se_abre(tmp_path, caso, monkeypatch):
    fuente_id = caso.publicar(tmp_path)
    rival = caso.registrar(fuente_id)
    _ciega(monkeypatch, caso.modulo, "_en_proceso")
    insertar = caso.modulo.insertar_en_savepoint

    def y_la_rival_falla(s, objeto):
        error = insertar(s, objeto)
        if error is not None:
            caso.terminar(rival, "FALLIDA")  # entre el choque y la nueva revision
        return error

    monkeypatch.setattr(caso.modulo, "insertar_en_savepoint", y_la_rival_falla)

    with sesion() as s:
        abierta = caso.abrir(s, fuente_id)

    assert caso.estados() == [(rival, "FALLIDA"), (abierta.id, "EN_PROCESO")]


@en_la_base
@POR_MOTOR
def test_una_violacion_que_no_es_la_carrera_se_propaga(tmp_path, caso, monkeypatch):
    fuente_id = caso.publicar(tmp_path)
    caso.registrar(fuente_id)
    _ciega(monkeypatch, caso.modulo, "_en_proceso")
    monkeypatch.setattr(caso.modulo, "restriccion", lambda _error: "otra_restriccion")

    with sesion() as s, pytest.raises(IntegrityError, match=caso.indice):
        caso.abrir(s, fuente_id)


@en_la_base
@POR_MOTOR
def test_si_pierde_la_carrera_cada_vez_se_rinde(tmp_path, caso, monkeypatch):
    fuente_id = caso.publicar(tmp_path)
    caso.registrar(fuente_id)
    _ciega(monkeypatch, caso.modulo, "_en_proceso", veces=99)
    insertar = caso.modulo.insertar_en_savepoint
    intentos = []

    def cuenta(s, objeto):
        intentos.append(objeto)
        return insertar(s, objeto)

    monkeypatch.setattr(caso.modulo, "insertar_en_savepoint", cuenta)

    with sesion() as s, pytest.raises(IntegrityError, match=caso.indice):
        caso.abrir(s, fuente_id)

    assert len(intentos) == caso.modulo.INTENTOS_DE_APERTURA == 3


# --- la corrida ----------------------------------------------------------------------------------


def _contenido(tmp_path: Path) -> bytes:
    return generar_archivo(tmp_path / "a.csv", n=20, semilla=2, fecha_corte=CORTE).read_bytes()


def _registrar_corrida(contenido: bytes) -> int:
    """Una corrida EN_PROCESO del archivo, insertada a mano, sin revision."""
    with sesion() as s:
        corrida = Corrida(
            origen="rival.csv",
            firma=firmar(contenido),
            tolerancia_rechazo=0.05,
            version_contrato=VERSION_CONTRATO,
        )
        s.add(corrida)
        s.commit()
        return corrida.id


def _corridas() -> list[tuple[int, str]]:
    with sesion() as s:
        return [(c.id, c.estado) for c in s.exec(select(Corrida).order_by(Corrida.id)).all()]


@en_la_base
def test_sin_confirmar_la_corrida_queda_en_la_transaccion_de_quien_llama(tmp_path):
    contenido = _contenido(tmp_path)

    with sesion() as s:
        abierta = abrir_corrida(s, origen="a.csv", contenido=contenido, confirmar=False)
        assert abierta.id is not None and abierta.estado == EstadoCorrida.EN_PROCESO
        assert _corridas() == []
        s.rollback()
    assert _corridas() == []

    with sesion() as s:
        abrir_corrida(s, origen="a.csv", contenido=contenido, confirmar=False)
        s.commit()
    assert [estado for _, estado in _corridas()] == ["EN_PROCESO"]


@en_la_base
def test_la_corrida_que_pierde_la_carrera_dice_cual_gano_y_deja_usable_la_transaccion(
    tmp_path, monkeypatch
):
    contenido = _contenido(tmp_path)
    rival = _registrar_corrida(contenido)
    _ciega(monkeypatch, corridas, "_previa")

    with sesion() as s:
        s.add(_otra_corrida())
        s.flush()
        with pytest.raises(ArchivoDuplicado, match="se esta procesando") as exc:
            abrir_corrida(s, origen="a.csv", contenido=contenido, confirmar=False)
        ganadora = exc.value.previa.id  # antes del commit, que la caduca
        s.commit()

    assert ganadora == rival
    assert _corridas_que_se_llaman("otra.csv") == 1
    assert _corridas_que_se_llaman("a.csv") == 0


@en_la_base
def test_la_corrida_se_abre_si_la_que_gano_ya_termino_al_volver_a_revisar(tmp_path, monkeypatch):
    contenido = _contenido(tmp_path)
    rival = _registrar_corrida(contenido)
    _ciega(monkeypatch, corridas, "_previa")
    insertar = corridas.insertar_en_savepoint

    def y_la_rival_falla(s, objeto):
        error = insertar(s, objeto)
        if error is not None:
            with sesion() as otra:
                otra.execute(update(Corrida).where(Corrida.id == rival).values(estado="FALLIDA"))
                otra.commit()
        return error

    monkeypatch.setattr(corridas, "insertar_en_savepoint", y_la_rival_falla)

    with sesion() as s:
        abierta = abrir_corrida(s, origen="a.csv", contenido=contenido)

    assert _corridas() == [(rival, "FALLIDA"), (abierta.id, "EN_PROCESO")]


@en_la_base
@pytest.mark.parametrize("como", ["otra_restriccion", "siempre"])
def test_la_corrida_propaga_lo_que_no_es_la_carrera_y_se_rinde_si_la_pierde_siempre(
    tmp_path, monkeypatch, como
):
    contenido = _contenido(tmp_path)
    _registrar_corrida(contenido)
    if como == "otra_restriccion":
        _ciega(monkeypatch, corridas, "_previa")
        monkeypatch.setattr(corridas, "restriccion", lambda _error: "otra_restriccion")
    else:
        _ciega(monkeypatch, corridas, "_previa", veces=99)

    with sesion() as s, pytest.raises(IntegrityError, match="ux_corrida_firma_en_proceso"):
        abrir_corrida(s, origen="a.csv", contenido=contenido)


@en_la_base
def test_dos_aperturas_del_mismo_archivo_a_la_vez_dejan_una_sola_activa(tmp_path):
    # La carrera de verdad, sin cegar nada: otra transaccion inserto la corrida y todavia no la
    # confirma, asi que la revision amable no la ve. El INSERT de esta se queda esperando el indice;
    # cuando la otra confirma, choca, vuelve a revisar y levanta ArchivoDuplicado con la otra.
    contenido = _contenido(tmp_path)
    errores: list[BaseException] = []
    with sesion() as otra:
        competidora = Corrida(
            origen="competidora.csv",
            firma=firmar(contenido),
            tolerancia_rechazo=0.05,
            version_contrato=VERSION_CONTRATO,
        )
        otra.add(competidora)
        otra.flush()
        competidora_id = competidora.id

        def confirmar_cuando_espere() -> None:
            try:
                assert _alguien_espera_para("INSERT INTO corrida")
                otra.commit()
            except BaseException as exc:  # pragma: no cover - solo si la prueba falla
                errores.append(exc)

        hilo = threading.Thread(target=confirmar_cuando_espere, daemon=True)
        hilo.start()
        with sesion() as s, pytest.raises(ArchivoDuplicado, match="se esta procesando") as exc:
            abrir_corrida(s, origen="a.csv", contenido=contenido)
        hilo.join(timeout=30)

    assert errores == []
    assert exc.value.previa.id == competidora_id
    assert _corridas() == [(competidora_id, "EN_PROCESO")]


def _alguien_espera_para(sentencia: str, limite: float = 10.0) -> bool:
    """Si dentro de `limite` segundos alguna sesion queda esperando un bloqueo con esa sentencia."""
    consulta = text(
        "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
        "AND wait_event_type = 'Lock' AND query LIKE :sentencia"
    )
    hasta = time.monotonic() + limite
    while time.monotonic() < hasta:
        with crear_motor().connect() as conexion:
            if conexion.execute(consulta, {"sentencia": f"{sentencia}%"}).scalar_one():
                return True
        time.sleep(0.05)
    return False
