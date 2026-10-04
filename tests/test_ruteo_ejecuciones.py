"""El servicio de ruteo contra PostgreSQL: trazar la ruta de cada municipio de una ejecucion
territorial publicada, sin publicar nada a medias.

Las carteras se publican, se deciden y se organizan por los flujos reales de la fase 1, del Decision
Engine y del Motor Territorial, y se rutean con el servicio completo: la cadena revisada otra vez
dentro de la transaccion, una sola lectura de las cuentas de campo, ruteo/v1 municipio por
municipio, rutas y paradas en una sola transaccion, FALLIDA con rastro ante cualquier fallo, un solo
worker por ejecucion y la idempotencia que garantiza el indice unico parcial. Las pruebas de la
ultima seccion no tocan la base.

Las rutas esperadas se calcularon aparte con la implementacion independiente de las pruebas del
nucleo, y se escriben aqui como numeros.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
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
    EjecucionRuteo,
    EjecucionTerritorial,
    EstadoCorrida,
    EstadoDecision,
    EstadoRuteo,
    EstadoTerritorial,
    ParadaRuta,
    ResultadoTerritorial,
    RutaTerritorial,
)
from motor_cartera.db.sesion import crear_motor, sesion
from motor_cartera.decision.ejecuciones import decidir_corrida
from motor_cartera.decision.reglas import CANAL_POR_PRIORIDAD
from motor_cartera.ingesta.corridas import ingerir_archivo
from motor_cartera.ruteo import ejecuciones
from motor_cartera.ruteo.ejecuciones import (
    CANAL_CAMPO,
    VERSION_DECISION_COMPATIBLE,
    VERSION_TERRITORIAL_COMPATIBLE,
    RuteoYaGenerado,
    TerritorialNoRuteable,
    abrir_ejecucion,
    ejecutar_ruteo,
    rutear_territorial,
)
from motor_cartera.ruteo.reglas import VERSION_REGLAS_RUTEO, rutear_territorio
from motor_cartera.territorial.ejecuciones import territorializar_decision

en_la_base = pytest.mark.usefixtures("bd")

CORTE = "2026-09-30"

Cuentas = list[tuple[str, str, int, str, str]]
"""Una cartera de prueba: por cuenta, (cliente_unico, clave del municipio, dias de atraso, saldo,
canal de la cartera). El canal recomendado lo decide decision/v1 con los dias y el saldo: CAMPO con
91 dias o mas, o de 31 a 90 con saldo desde 50,000.00; si no, TELEFONICA o DIGITAL."""

# Cuatro municipios. A la derecha, el canal que decision/v1 recomienda; la ultima columna, el canal
# de la cartera, lo contradice a menudo y no debe contar.
CARTERA: Cuentas = [
    ("CU00000041", "21114", 120, "1000.00", "DIGITAL"),  # CAMPO
    ("CU00000042", "21114", 95, "2000.00", "TELEFONICA"),  # CAMPO
    ("CU00000043", "21114", 91, "1500.00", "CAMPO"),  # CAMPO
    ("CU00000044", "21114", 200, "900.00", "DIGITAL"),  # CAMPO
    ("CU00000045", "21114", 45, "75000.00", "TELEFONICA"),  # CAMPO, por el saldo
    ("CU00000003", "21114", 0, "500.00", "CAMPO"),  # DIGITAL
    ("CU00000004", "09002", 150, "3000.00", "DIGITAL"),  # CAMPO
    ("CU00000008", "09002", 300, "800.00", "TELEFONICA"),  # CAMPO
    ("CU00000010", "09002", 5, "100.00", "CAMPO"),  # DIGITAL
    ("CU00000006", "15033", 100, "60000.00", "TELEFONICA"),  # CAMPO
    ("CU00000007", "21001", 10, "4000.00", "CAMPO"),  # DIGITAL
    ("CU00000011", "21001", 60, "2000.00", "CAMPO"),  # TELEFONICA
]

# Lo que ruteo/v1 publica de CARTERA, en el orden de posicion_campo. 21001 no tiene cuentas de campo
# y no tiene ruta, aunque la cartera diga CAMPO en sus dos cuentas.
RUTAS = [
    # (clave, posicion_campo, paradas, inicial, total, regreso al deposito, mejora del 2-opt)
    ("21114", 1, 5, 37878, 28770, 7633, 9108),
    ("09002", 2, 2, 15416, 15416, 6211, 0),
    ("15033", 3, 1, 11742, 11742, 5871, 0),
]
PARADAS = {
    # clave: [(cliente_unico, secuencia, x_m, y_m, distancia_desde_anterior_m)]
    "21114": [
        ("CU00000041", 1, 1016, -2070, 3086),
        ("CU00000044", 2, 1712, 1261, 4027),
        ("CU00000043", 3, 4679, 2833, 4539),
        ("CU00000042", 4, 966, 3811, 4691),
        ("CU00000045", 5, -3825, 3808, 4794),
    ],
    "09002": [("CU00000004", 1, 3940, -1497, 5437), ("CU00000008", 2, 4268, 1943, 3768)],
    "15033": [("CU00000006", 1, 4268, 1603, 5871)],
}
DE_CAMPO = {cliente for paradas in PARADAS.values() for cliente, *_ in paradas}

# Tres cuentas en dos municipios, una de campo en cada uno: 21114 va primero, con mas saldo de
# campo que 09002.
PEQUENA: Cuentas = [
    ("CU00000041", "21114", 120, "3000.00", "DIGITAL"),  # CAMPO
    ("CU00000044", "21114", 0, "7000.00", "CAMPO"),  # DIGITAL
    ("CU00000004", "09002", 95, "1500.00", "CAMPO"),  # CAMPO
]

# Ninguna cuenta va a campo, aunque la cartera diga CAMPO.
SIN_CAMPO: Cuentas = [
    ("CU00000001", "21114", 0, "100.00", "CAMPO"),  # DIGITAL
    ("CU00000002", "09002", 10, "200.00", "CAMPO"),  # DIGITAL
]


def _cartera(cuentas: Cuentas, corte: str = CORTE) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "cliente_unico": [cliente for cliente, *_ in cuentas],
            "saldo_total": [saldo for *_, saldo, _ in cuentas],
            "dias_atraso": [dias for _, _, dias, _, _ in cuentas],
            "producto": ["CONSUMO"] * len(cuentas),
            "canal": [canal for *_, canal in cuentas],
            "cve_entidad": [clave[:2] for _, clave, *_ in cuentas],
            "cve_municipio": [clave[2:] for _, clave, *_ in cuentas],
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
    fuente = decidir_corrida(_publicar(tmp_path, cuentas, nombre, corte).id)
    assert fuente.estado == EstadoDecision.EXITOSA, fuente.detalle
    return fuente


def _organizada(
    tmp_path: Path, cuentas: Cuentas, nombre: str = "cartera.csv", corte: str = CORTE
) -> EjecucionTerritorial:
    """La cartera publicada, decidida con decision/v1 y organizada con territorial/v1, por los
    servicios reales."""
    territorial = territorializar_decision(_decidida(tmp_path, cuentas, nombre, corte).id)
    assert territorial.estado == EstadoTerritorial.EXITOSA, territorial.detalle
    return territorial


def _decision_a_mano(corrida_id: int, estado: EstadoDecision, version: str) -> EjecucionDecision:
    with sesion() as s:
        fuente = EjecucionDecision(corrida_id=corrida_id, version_reglas=version, estado=estado)
        s.add(fuente)
        s.commit()
        s.refresh(fuente)
        return fuente


def _territorial_a_mano(
    decision_id: int, estado: EstadoTerritorial, version: str = VERSION_TERRITORIAL_COMPATIBLE
) -> EjecucionTerritorial:
    """Una ejecucion territorial insertada a mano y sin municipios."""
    with sesion() as s:
        territorial = EjecucionTerritorial(
            ejecucion_decision_id=decision_id, version_reglas=version, estado=estado
        )
        s.add(territorial)
        s.commit()
        s.refresh(territorial)
        return territorial


def _registrar(territorial_id: int, version: str = VERSION_REGLAS_RUTEO) -> int:
    """Una ejecucion de ruteo EN_PROCESO insertada a mano, sin pasar por abrir_ejecucion."""
    with sesion() as s:
        ejecucion = EjecucionRuteo(ejecucion_territorial_id=territorial_id, version_reglas=version)
        s.add(ejecucion)
        s.commit()
        return ejecucion.id


def _ejecucion(ejecucion_id: int) -> EjecucionRuteo:
    with sesion() as s:
        return s.get_one(EjecucionRuteo, ejecucion_id)


def _ruteos(territorial_id: int | None = None) -> list[EjecucionRuteo]:
    with sesion() as s:
        consulta = select(EjecucionRuteo).order_by(EjecucionRuteo.id)
        if territorial_id is not None:
            consulta = consulta.where(EjecucionRuteo.ejecucion_territorial_id == territorial_id)
        return list(s.exec(consulta).all())


def _cuantas(modelo, ejecucion_id: int | None = None) -> int:
    """Cuantas rutas o paradas hay guardadas, de una ejecucion o de todas."""
    with sesion() as s:
        consulta = select(func.count()).select_from(modelo)
        if ejecucion_id is not None:
            consulta = consulta.where(modelo.ejecucion_ruteo_id == ejecucion_id)
        return s.exec(consulta).one()


def _contadores(ejecucion: EjecucionRuteo) -> tuple[int, int, int, int]:
    return (
        ejecucion.rutas_evaluadas,
        ejecucion.rutas_publicadas,
        ejecucion.paradas_evaluadas,
        ejecucion.paradas_publicadas,
    )


def _publicadas(ejecucion_id: int) -> list[tuple]:
    """Las rutas de una ejecucion, con la clave y el lugar de su municipio por JOIN, en el orden de
    posicion_campo."""
    with sesion() as s:
        filas = s.exec(
            select(
                ResultadoTerritorial.cve_entidad,
                ResultadoTerritorial.cve_municipio,
                ResultadoTerritorial.posicion_campo,
                RutaTerritorial.paradas,
                RutaTerritorial.distancia_inicial_m,
                RutaTerritorial.distancia_total_m,
                RutaTerritorial.distancia_regreso_deposito_m,
                RutaTerritorial.mejora_2opt_m,
            )
            .select_from(RutaTerritorial)
            .join(
                ResultadoTerritorial,
                RutaTerritorial.resultado_territorial_id == ResultadoTerritorial.id,
            )
            .where(RutaTerritorial.ejecucion_ruteo_id == ejecucion_id)
            .order_by(ResultadoTerritorial.posicion_campo)
        ).all()
    return [(entidad + municipio, *resto) for entidad, municipio, *resto in filas]


def _paradas(ejecucion_id: int) -> dict[str, list[tuple]]:
    """Las paradas de una ejecucion por municipio, con el cliente de su decision por JOIN, en el
    orden de visita."""
    with sesion() as s:
        filas = s.exec(
            select(
                ResultadoTerritorial.cve_entidad,
                ResultadoTerritorial.cve_municipio,
                Cuenta.cliente_unico,
                ParadaRuta.secuencia,
                ParadaRuta.x_m,
                ParadaRuta.y_m,
                ParadaRuta.distancia_desde_anterior_m,
            )
            .select_from(ParadaRuta)
            .join(RutaTerritorial, ParadaRuta.ruta_territorial_id == RutaTerritorial.id)
            .join(
                ResultadoTerritorial,
                RutaTerritorial.resultado_territorial_id == ResultadoTerritorial.id,
            )
            .join(DecisionCuenta, ParadaRuta.decision_cuenta_id == DecisionCuenta.id)
            .join(Cuenta, DecisionCuenta.cuenta_id == Cuenta.id)
            .where(ParadaRuta.ejecucion_ruteo_id == ejecucion_id)
            .order_by(ResultadoTerritorial.posicion_campo, ParadaRuta.secuencia)
        ).all()
    paradas: dict[str, list[tuple]] = {}
    for entidad, municipio, *parada in filas:
        paradas.setdefault(entidad + municipio, []).append(tuple(parada))
    return paradas


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
    ejecucion_ruteo: la consulta del servicio, detenida por el FOR UPDATE de otra."""
    hasta = time.monotonic() + limite
    while time.monotonic() < hasta:
        with crear_motor().connect() as conexion:
            consulta = {"consulta": "%FROM ejecucion_ruteo%FOR UPDATE%"}
            if conexion.execute(ESPERANDO_LA_FILA, consulta).scalar_one():
                return True
        time.sleep(0.05)
    return False


def _revienta(*_):
    raise RuntimeError("falla simulada del nucleo")


def _fallida_por_el_motor(monkeypatch: pytest.MonkeyPatch, territorial_id: int) -> EjecucionRuteo:
    """Una ejecucion de ruteo FALLIDA porque el nucleo fallo a la mitad."""
    with monkeypatch.context() as parche:
        parche.setattr(ejecuciones, "rutear_territorio", _revienta)
        fallida = rutear_territorial(territorial_id)
    assert fallida.estado == EstadoRuteo.FALLIDA
    return fallida


# --- de punta a punta ----------------------------------------------------------------------------


@en_la_base
def test_una_organizacion_territorial_publicada_se_rutea_de_punta_a_punta(tmp_path):
    # La cartera se publica, se decide y se organiza de verdad, y lo publicado se lee de
    # PostgreSQL: cada ruta con su municipio, su lugar y sus distancias, y cada parada con su
    # cliente, su punto y su distancia, exactos.
    territorial = _organizada(tmp_path, CARTERA)

    ejecucion = rutear_territorial(territorial.id)

    assert ejecucion.estado == EstadoRuteo.EXITOSA
    assert ejecucion.ejecucion_territorial_id == territorial.id
    assert ejecucion.version_reglas == VERSION_REGLAS_RUTEO
    assert _contadores(ejecucion) == (3, 3, 8, 8)
    assert ejecucion.detalle == "Se rutearon 8 cuentas de campo en 3 municipios con ruteo/v1."
    assert ejecucion.terminada_en >= ejecucion.iniciada_en
    assert _publicadas(ejecucion.id) == RUTAS
    assert _paradas(ejecucion.id) == PARADAS


@en_la_base
def test_solo_son_paradas_las_cuentas_que_decision_v1_mando_a_campo(tmp_path):
    # Cuatro cuentas dicen CAMPO en la cartera y decision/v1 no las manda a campo; cuatro de las
    # que si van a campo dicen otra cosa en la cartera. Cuenta la decision.
    territorial = _organizada(tmp_path, CARTERA)
    with sesion() as s:
        canales = s.exec(
            select(Cuenta.cliente_unico, Cuenta.canal, DecisionCuenta.canal_recomendado)
            .select_from(DecisionCuenta)
            .join(Cuenta, DecisionCuenta.cuenta_id == Cuenta.id)
            .where(DecisionCuenta.ejecucion_decision_id == territorial.ejecucion_decision_id)
        ).all()
    por_cartera = {cliente for cliente, canal, _ in canales if canal == "CAMPO"}
    por_decision = {cliente for cliente, _, recomendado in canales if recomendado == "CAMPO"}
    assert por_decision == DE_CAMPO
    assert por_cartera - por_decision == {"CU00000003", "CU00000010", "CU00000007", "CU00000011"}

    ejecucion = rutear_territorial(territorial.id)

    visitados = {parada[0] for paradas in _paradas(ejecucion.id).values() for parada in paradas}
    assert visitados == por_decision


@en_la_base
def test_cada_decision_de_campo_es_exactamente_una_parada(tmp_path):
    territorial = _organizada(tmp_path, CARTERA)

    ejecucion = rutear_territorial(territorial.id)

    with sesion() as s:
        de_campo = s.exec(
            select(DecisionCuenta.id).where(
                DecisionCuenta.ejecucion_decision_id == territorial.ejecucion_decision_id,
                DecisionCuenta.canal_recomendado == CANAL_CAMPO,
            )
        ).all()
        paradas = s.exec(
            select(ParadaRuta.decision_cuenta_id, ParadaRuta.ruta_territorial_id).where(
                ParadaRuta.ejecucion_ruteo_id == ejecucion.id
            )
        ).all()
        rutas = s.exec(
            select(RutaTerritorial.id).where(RutaTerritorial.ejecucion_ruteo_id == ejecucion.id)
        ).all()
    visitadas = [decision for decision, _ in paradas]
    assert sorted(visitadas) == sorted(de_campo)
    assert len(set(visitadas)) == len(visitadas) == 8
    # Y cada parada es de una ruta de su misma ejecucion.
    assert {ruta for _, ruta in paradas} == set(rutas)


@en_la_base
def test_los_municipios_sin_cuentas_de_campo_no_tienen_ruta(tmp_path):
    # La ejecucion territorial publico cuatro municipios; 21001 no tiene cuentas de campo.
    territorial = _organizada(tmp_path, CARTERA)
    assert territorial.territorios_publicados == 4

    ejecucion = rutear_territorial(territorial.id)

    assert [clave for clave, *_ in _publicadas(ejecucion.id)] == ["21114", "09002", "15033"]
    assert "21001" not in _paradas(ejecucion.id)


@en_la_base
def test_un_solo_municipio_con_campo_da_una_sola_ruta(tmp_path):
    cuentas: Cuentas = [
        ("CU00000041", "21114", 120, "1000.00", "DIGITAL"),  # CAMPO
        ("CU00000045", "21114", 95, "2000.00", "DIGITAL"),  # CAMPO
        ("CU00000042", "21114", 5, "300.00", "CAMPO"),  # DIGITAL
        ("CU00000004", "09002", 0, "100.00", "CAMPO"),  # DIGITAL
    ]

    ejecucion = rutear_territorial(_organizada(tmp_path, cuentas).id)

    assert _contadores(ejecucion) == (1, 1, 2, 2)
    assert _publicadas(ejecucion.id) == [("21114", 1, 2, 21438, 21438, 7633, 0)]
    assert _paradas(ejecucion.id) == {
        "21114": [("CU00000041", 1, 1016, -2070, 3086), ("CU00000045", 2, -3825, 3808, 10719)]
    }


@en_la_base
def test_el_nucleo_se_llama_una_vez_por_municipio_con_campo_y_en_su_orden(tmp_path, monkeypatch):
    # Una llamada por municipio con ruta, en el orden de posicion_campo y con sus clientes de campo;
    # ninguna para 21001. El servicio no calcula ni coordenadas ni distancias: las pide al nucleo.
    territorial = _organizada(tmp_path, CARTERA)
    llamadas = []

    def anota(clave, clientes):
        llamadas.append((clave, list(clientes)))
        return rutear_territorio(clave, clientes)

    monkeypatch.setattr(ejecuciones, "rutear_territorio", anota)

    rutear_territorial(territorial.id)

    assert llamadas == [
        ("21114", ["CU00000041", "CU00000042", "CU00000043", "CU00000044", "CU00000045"]),
        ("09002", ["CU00000004", "CU00000008"]),
        ("15033", ["CU00000006"]),
    ]


@en_la_base
def test_rutear_no_cambia_ninguna_decision_cuenta_ni_municipio(tmp_path):
    territorial = _organizada(tmp_path, CARTERA)

    def fuente_completa() -> tuple:
        with sesion() as s:
            filas = []
            for modelo in (Cuenta, DecisionCuenta, EjecucionDecision, ResultadoTerritorial):
                filas.append([f.model_dump() for f in s.exec(select(modelo).order_by(modelo.id))])
            filas.append(s.get_one(EjecucionTerritorial, territorial.id).model_dump())
        return tuple(map(str, filas))

    antes = fuente_completa()

    assert rutear_territorial(territorial.id).estado == EstadoRuteo.EXITOSA
    assert fuente_completa() == antes


# --- que se rutea y que no -----------------------------------------------------------------------


@en_la_base
@pytest.mark.parametrize("estado", [EstadoTerritorial.EN_PROCESO, EstadoTerritorial.FALLIDA])
def test_solo_se_rutea_una_ejecucion_territorial_exitosa(tmp_path, estado):
    fuente = _decidida(tmp_path, PEQUENA)
    territorial = _territorial_a_mano(fuente.id, estado)

    with pytest.raises(TerritorialNoRuteable) as exc:
        rutear_territorial(territorial.id)

    assert str(exc.value) == (
        f"La ejecucion territorial {territorial.territorial_run_id} esta {estado}; solo se rutean "
        "los municipios de una ejecucion territorial EXITOSA."
    )
    assert exc.value.ejecucion_territorial.id == territorial.id
    assert _ruteos() == []  # ni el intento queda registrado


@en_la_base
def test_ruteo_v1_solo_rutea_municipios_de_territorial_v1(tmp_path):
    # Aunque este EXITOSA: territorial/v2 no es la version con la que ruteo/v1 es compatible.
    fuente = _decidida(tmp_path, PEQUENA)
    territorial = _territorial_a_mano(fuente.id, EstadoTerritorial.EXITOSA, "territorial/v2")

    with pytest.raises(TerritorialNoRuteable) as exc:
        rutear_territorial(territorial.id)

    assert str(exc.value) == (
        f"La ejecucion territorial {territorial.territorial_run_id} se calculo con "
        "territorial/v2; ruteo/v1 solo rutea municipios de territorial/v1."
    )
    assert _ruteos() == []


@en_la_base
@pytest.mark.parametrize(
    ("estado", "version", "razon"),
    [
        pytest.param(
            EstadoDecision.EXITOSA,
            "decision/v2",
            "se decidio con decision/v2; ruteo/v1 solo rutea decisiones de decision/v1.",
            id="decision-v2",
        ),
        pytest.param(
            EstadoDecision.FALLIDA,
            "decision/v1",
            "esta FALLIDA; solo se rutean decisiones de una ejecucion EXITOSA.",
            id="decision-fallida",
        ),
    ],
)
def test_la_cadena_se_revisa_hasta_las_decisiones(tmp_path, estado, version, razon):
    # Una territorial EXITOSA de territorial/v1 no basta: las decisiones de las que sale tambien
    # tienen que ser una ejecucion EXITOSA de decision/v1.
    decision = _decision_a_mano(_publicar(tmp_path, PEQUENA).id, estado, version)
    territorial = _territorial_a_mano(decision.id, EstadoTerritorial.EXITOSA)

    with pytest.raises(TerritorialNoRuteable) as exc:
        rutear_territorial(territorial.id)

    assert str(exc.value) == (
        f"La ejecucion de decision {decision.decision_run_id}, de la que sale la territorial, "
        f"{razon}"
    )
    assert _ruteos() == []


@en_la_base
def test_la_ejecucion_queda_registrada_antes_de_calcular_nada(tmp_path):
    territorial = _organizada(tmp_path, PEQUENA)

    with sesion() as s:
        ejecucion = abrir_ejecucion(s, s.get_one(EjecucionTerritorial, territorial.id))

    registrada = _ejecucion(ejecucion.id)  # desde otra sesion: ya esta confirmada
    assert registrada.estado == EstadoRuteo.EN_PROCESO
    assert registrada.ejecucion_territorial_id == territorial.id
    assert registrada.version_reglas == VERSION_REGLAS_RUTEO
    assert isinstance(registrada.ruteo_run_id, UUID)
    assert _contadores(registrada) == (0, 0, 0, 0)
    assert (registrada.terminada_en, registrada.detalle) == (None, None)
    assert _cuantas(RutaTerritorial, ejecucion.id) == _cuantas(ParadaRuta, ejecucion.id) == 0


@en_la_base
def test_una_territorial_ya_ruteada_no_se_rutea_otra_vez(tmp_path):
    territorial = _organizada(tmp_path, PEQUENA)
    primera = rutear_territorial(territorial.id)

    with pytest.raises(RuteoYaGenerado) as exc:
        rutear_territorial(territorial.id)

    assert str(exc.value) == (
        "Esta ejecucion territorial ya se ruteo con ruteo/v1: la ejecucion de ruteo "
        f"{primera.ruteo_run_id}."
    )
    assert (exc.value.previa.id, exc.value.previa.estado) == (primera.id, EstadoRuteo.EXITOSA)
    # La revision amable no deja ni el intento: sigue habiendo una sola ejecucion, con sus rutas.
    assert [e.id for e in _ruteos(territorial.id)] == [primera.id]
    assert (_cuantas(RutaTerritorial), _cuantas(ParadaRuta)) == (2, 2)


@en_la_base
def test_una_ejecucion_de_otra_version_de_ruteo_no_se_calcula_con_estas_reglas(tmp_path):
    territorial = _organizada(tmp_path, PEQUENA)
    ejecucion_id = _registrar(territorial.id, version="ruteo/v2")

    ejecutar_ruteo(ejecucion_id)

    ejecucion = _ejecucion(ejecucion_id)
    assert ejecucion.estado == EstadoRuteo.FALLIDA
    assert ejecucion.version_reglas == "ruteo/v2"  # la que pidio, no la del codigo
    assert _contadores(ejecucion) == (0, 0, 0, 0)
    assert ejecucion.detalle == (
        "La ejecucion pide las reglas ruteo/v2 y este servicio solo rutea con ruteo/v1; no se "
        "publico ninguna ruta."
    )
    assert _cuantas(RutaTerritorial) == 0


@en_la_base
@pytest.mark.parametrize(
    ("modelo", "cambio", "razon"),
    [
        pytest.param(
            EjecucionTerritorial,
            {"estado": EstadoTerritorial.FALLIDA},
            "La ejecucion territorial {territorial} esta FALLIDA; solo se rutean los municipios de "
            "una ejecucion territorial EXITOSA.",
            id="territorial-fallida",
        ),
        pytest.param(
            EjecucionTerritorial,
            {"version_reglas": "territorial/v2"},
            "La ejecucion territorial {territorial} se calculo con territorial/v2; ruteo/v1 solo "
            "rutea municipios de territorial/v1.",
            id="territorial-v2",
        ),
        pytest.param(
            EjecucionDecision,
            {"estado": EstadoDecision.FALLIDA},
            "La ejecucion de decision {decision}, de la que sale la territorial, esta FALLIDA; "
            "solo se rutean decisiones de una ejecucion EXITOSA.",
            id="decision-fallida",
        ),
        pytest.param(
            EjecucionDecision,
            {"version_reglas": "decision/v2"},
            "La ejecucion de decision {decision}, de la que sale la territorial, se decidio con "
            "decision/v2; ruteo/v1 solo rutea decisiones de decision/v1.",
            id="decision-v2",
        ),
    ],
)
def test_una_fuente_que_cambia_entre_abrir_y_ejecutar_no_se_rutea(tmp_path, modelo, cambio, razon):
    territorial = _organizada(tmp_path, PEQUENA)
    with sesion() as s:
        ejecucion = abrir_ejecucion(s, s.get_one(EjecucionTerritorial, territorial.id))
        decision = s.get_one(EjecucionDecision, territorial.ejecucion_decision_id)
        decision_run_id = decision.decision_run_id
    # Entre la transaccion que abre y la que publica, alguien cambia la cadena en la base.
    fila = territorial.id if modelo is EjecucionTerritorial else territorial.ejecucion_decision_id
    with sesion() as s:
        s.execute(update(modelo).where(modelo.id == fila).values(**cambio))
        s.commit()

    ejecutar_ruteo(ejecucion.id)

    fallida = _ejecucion(ejecucion.id)
    assert fallida.estado == EstadoRuteo.FALLIDA
    assert _contadores(fallida) == (0, 0, 0, 0)
    esperado = razon.format(territorial=territorial.territorial_run_id, decision=decision_run_id)
    assert fallida.detalle == f"{esperado} No se publico ninguna ruta."
    assert _cuantas(RutaTerritorial, ejecucion.id) == 0


# --- una fuente incompleta o incoherente no se rutea ---------------------------------------------


def _ruteo_fallido(territorial_id: int) -> EjecucionRuteo:
    ejecucion = rutear_territorial(territorial_id)
    assert ejecucion.estado == EstadoRuteo.FALLIDA
    assert (_cuantas(RutaTerritorial), _cuantas(ParadaRuta)) == (0, 0)
    return ejecucion


def _cambiar(modelo, donde, **valores) -> None:
    with sesion() as s:
        s.execute(update(modelo).where(donde).values(**valores))
        s.commit()


def _resultado(territorial: EjecucionTerritorial, clave: str):
    return (
        (ResultadoTerritorial.ejecucion_territorial_id == territorial.id)
        & (ResultadoTerritorial.cve_entidad == clave[:2])
        & (ResultadoTerritorial.cve_municipio == clave[2:])
    )


@en_la_base
def test_una_territorial_con_contadores_que_no_cuadran_no_se_rutea(tmp_path):
    territorial = _organizada(tmp_path, CARTERA)
    _cambiar(
        EjecucionTerritorial,
        EjecucionTerritorial.id == territorial.id,
        territorios_publicados=EjecucionTerritorial.territorios_publicados + 1,
    )

    ejecucion = _ruteo_fallido(territorial.id)

    assert ejecucion.detalle == (
        "La ejecucion territorial esta incompleta: dice que evaluo 4 municipios y publico 5, y "
        "tiene 4; no se publico ninguna ruta."
    )
    assert _contadores(ejecucion) == (0, 0, 0, 0)


@en_la_base
def test_una_territorial_a_la_que_le_falta_un_municipio_no_se_rutea(tmp_path):
    territorial = _organizada(tmp_path, CARTERA)
    with sesion() as s:
        s.execute(delete(ResultadoTerritorial).where(_resultado(territorial, "21001")))
        s.commit()

    ejecucion = _ruteo_fallido(territorial.id)

    assert ejecucion.detalle == (
        "La ejecucion territorial esta incompleta: dice que evaluo 4 municipios y publico 4, y "
        "tiene 3; no se publico ninguna ruta."
    )


@en_la_base
@pytest.mark.parametrize(
    ("clave", "lugar", "como"),
    [
        pytest.param("21001", 4, "0 cuentas de campo y el lugar 4", id="sin-campo-con-lugar"),
        pytest.param("15033", None, "1 cuentas de campo y ningun lugar", id="con-campo-sin-lugar"),
    ],
)
def test_un_municipio_incoherente_no_se_rutea(tmp_path, clave, lugar, como):
    territorial = _organizada(tmp_path, CARTERA)
    _cambiar(ResultadoTerritorial, _resultado(territorial, clave), posicion_campo=lugar)

    ejecucion = _ruteo_fallido(territorial.id)

    assert ejecucion.detalle == (
        f"El municipio {clave} de la ejecucion territorial no es coherente: tiene {como}; no se "
        "publico ninguna ruta."
    )


@en_la_base
def test_una_territorial_sin_municipios_con_campo_no_se_rutea_vacia(tmp_path):
    # Ninguna cuenta va a campo: la ejecucion territorial es valida, pero no hay nada que rutear, y
    # no se publica un ruteo vacio.
    territorial = _organizada(tmp_path, SIN_CAMPO)

    ejecucion = _ruteo_fallido(territorial.id)

    assert ejecucion.detalle == (
        "La ejecucion territorial no tiene municipios con cuentas de campo; no se publica un "
        "ruteo vacio."
    )
    assert _contadores(ejecucion) == (0, 0, 0, 0)


@en_la_base
def test_si_falta_una_decision_de_campo_no_se_rutea(tmp_path):
    # Despues de organizarse, una decision de campo deja de serlo: los municipios suman ocho
    # cuentas de campo y la ejecucion de decision ya solo tiene siete.
    territorial = _organizada(tmp_path, CARTERA)
    with sesion() as s:
        cuenta = s.exec(select(Cuenta.id).where(Cuenta.cliente_unico == "CU00000006")).one()
    _cambiar(DecisionCuenta, DecisionCuenta.cuenta_id == cuenta, canal_recomendado="DIGITAL")

    ejecucion = _ruteo_fallido(territorial.id)

    assert ejecucion.detalle == (
        "Los municipios suman 8 cuentas de campo y la ejecucion de decision tiene 7 decisiones de "
        "campo, 7 de cuentas de su corrida; no se publico ninguna ruta."
    )


@en_la_base
def test_una_decision_de_campo_sobre_una_cuenta_de_otra_corrida_no_se_rutea(tmp_path):
    # La base deja que una decision apunte a cualquier cuenta que exista, aunque sea de otra
    # corrida. Aqui una decision de campo de enero pasa a apuntar a una cuenta de febrero.
    territorial = _organizada(tmp_path, CARTERA, "enero.csv")
    febrero = _publicar(tmp_path, PEQUENA, "febrero.csv", corte="2026-10-31")
    with sesion() as s:
        ajena = s.exec(
            select(Cuenta.id).where(Cuenta.corrida_id == febrero.id).order_by(Cuenta.id)
        ).first()
        propia = s.exec(select(Cuenta.id).where(Cuenta.cliente_unico == "CU00000006")).one()
    _cambiar(DecisionCuenta, DecisionCuenta.cuenta_id == propia, cuenta_id=ajena)

    ejecucion = _ruteo_fallido(territorial.id)

    assert ejecucion.detalle == (
        "Los municipios suman 8 cuentas de campo y la ejecucion de decision tiene 8 decisiones de "
        "campo, 7 de cuentas de su corrida; no se publico ninguna ruta."
    )


@en_la_base
def test_una_cuenta_de_campo_en_un_municipio_sin_ruta_no_se_rutea(tmp_path):
    # Despues de organizarse, la cuenta de campo de 15033 aparece en 21001, que no tiene ruta.
    territorial = _organizada(tmp_path, CARTERA)
    _cambiar(Cuenta, Cuenta.cliente_unico == "CU00000006", cve_entidad="21", cve_municipio="001")

    ejecucion = _ruteo_fallido(territorial.id)

    assert ejecucion.detalle == (
        "Hay cuentas de campo en el municipio 21001, que no tiene ruta en la ejecucion "
        "territorial; no se publico ninguna ruta."
    )


@en_la_base
def test_una_cuenta_de_campo_que_cambia_de_municipio_con_ruta_no_se_rutea(tmp_path):
    # Ahora pasa de 15033 a 09002, que si tiene ruta: 09002 tendria tres cuentas de campo, y la
    # ejecucion territorial dice dos.
    territorial = _organizada(tmp_path, CARTERA)
    _cambiar(Cuenta, Cuenta.cliente_unico == "CU00000006", cve_entidad="09", cve_municipio="002")

    ejecucion = _ruteo_fallido(territorial.id)

    assert ejecucion.detalle == (
        "El municipio 09002 tiene 2 cuentas de campo en la ejecucion territorial y 3 en la de "
        "decision; no se publico ninguna ruta."
    )


@en_la_base
def test_un_cliente_que_no_cumple_ruteo_v1_no_se_rutea(tmp_path, caplog):
    # La base guarda lo que sea; ruteo/v1 exige mayusculas y digitos. Si un cliente no lo cumple,
    # no se publica ninguna ruta, y el nucleo no llego a terminar.
    territorial = _organizada(tmp_path, CARTERA)
    _cambiar(Cuenta, Cuenta.cliente_unico == "CU00000043", cliente_unico="cu00000043")

    ejecucion = _ruteo_fallido(territorial.id)

    assert ejecucion.detalle == (
        "Las cuentas de campo de un municipio no cumplen ruteo/v1; ver la bitacora. No se publico "
        "ninguna ruta."
    )
    assert _contadores(ejecucion) == (0, 0, 0, 0)
    assert any("no cumplen ruteo/v1" in registro.getMessage() for registro in caplog.records)


# --- un fallo no publica nada --------------------------------------------------------------------


@en_la_base
def test_un_fallo_antes_del_nucleo_no_lo_llama_ni_cuenta_nada(tmp_path, monkeypatch):
    # Una lectura con un error, que pierde la primera cuenta de campo (CU00000004, de 09002).
    territorial = _organizada(tmp_path, CARTERA)
    repartir = ejecuciones._repartir
    llamadas = []
    monkeypatch.setattr(
        ejecuciones, "_repartir", lambda municipios, cuentas: repartir(municipios, cuentas[1:])
    )
    monkeypatch.setattr(ejecuciones, "rutear_territorio", lambda *a: llamadas.append(a))

    ejecucion = _ruteo_fallido(territorial.id)

    assert llamadas == []  # el nucleo ni se llamo
    assert _contadores(ejecucion) == (0, 0, 0, 0)
    assert ejecucion.detalle == (
        "El municipio 09002 tiene 2 cuentas de campo en la ejecucion territorial y 1 en la de "
        "decision; no se publico ninguna ruta."
    )


@en_la_base
def test_un_fallo_despues_del_nucleo_registra_lo_evaluado_y_no_publica(
    tmp_path, monkeypatch, caplog
):
    # El nucleo ya trazo las tres rutas; falla al preparar las filas que se van a guardar.
    territorial = _organizada(tmp_path, CARTERA)
    monkeypatch.setattr(ejecuciones, "_fila_ruta", _revienta)

    ejecucion = _ruteo_fallido(territorial.id)

    assert _contadores(ejecucion) == (3, 0, 8, 0)
    # El detalle no lleva la traza ni el mensaje del error; la bitacora si.
    assert ejecucion.detalle == "Error interno (RuntimeError); ver la bitacora."
    assert ejecucion.terminada_en is not None
    (registro,) = [r for r in caplog.records if r.name == ejecuciones.__name__ and r.exc_info]
    assert "falla simulada" in str(registro.exc_info[1])


@en_la_base
def test_un_fallo_despues_de_insertar_las_rutas_revierte_las_rutas(tmp_path, monkeypatch):
    territorial = _organizada(tmp_path, CARTERA)
    insertar = ejecuciones._insertar_rutas
    vistas = []

    def inserta_y_cuenta(s, ejecucion_id, *argumentos):
        ids = insertar(s, ejecucion_id, *argumentos)
        de_la_ejecucion = RutaTerritorial.ejecucion_ruteo_id == ejecucion_id
        vistas.append(
            s.exec(select(func.count()).select_from(RutaTerritorial).where(de_la_ejecucion)).one()
        )
        return ids

    monkeypatch.setattr(ejecuciones, "_insertar_rutas", inserta_y_cuenta)
    monkeypatch.setattr(ejecuciones, "_fila_parada", _revienta)

    ejecucion = _ruteo_fallido(territorial.id)

    assert vistas == [3]  # dentro de la transaccion ya estaban las tres rutas
    assert _contadores(ejecucion) == (3, 0, 8, 0)
    assert ejecucion.detalle == "Error interno (RuntimeError); ver la bitacora."


@en_la_base
def test_un_fallo_despues_de_insertar_las_paradas_y_antes_del_commit_revierte_todo(
    tmp_path, monkeypatch
):
    # El cierre ya conto las rutas y las paradas y envio el EXITOSA; falla justo antes del commit.
    territorial = _organizada(tmp_path, CARTERA)
    cerrar = ejecuciones._cerrar
    vistas = []

    def cierra_y_falla(s, ejecucion, *argumentos):
        cerrar(s, ejecucion, *argumentos)
        vistas.append(
            (
                *s.exec(ejecuciones._conteo_publicado(ejecucion.id)).one(),
                s.exec(
                    select(EjecucionRuteo.estado).where(EjecucionRuteo.id == ejecucion.id)
                ).one(),
            )
        )
        raise RuntimeError("falla simulada antes del commit")

    monkeypatch.setattr(ejecuciones, "_cerrar", cierra_y_falla)

    ejecucion = _ruteo_fallido(territorial.id)

    assert vistas == [(3, 8, EstadoRuteo.EXITOSA)]
    assert ejecucion.estado == EstadoRuteo.FALLIDA
    assert _contadores(ejecucion) == (3, 0, 8, 0)
    assert ejecucion.detalle == "Error interno (RuntimeError); ver la bitacora."


@en_la_base
@pytest.mark.parametrize(
    ("alterar", "problema"),
    [
        pytest.param(
            lambda r: replace(r, distancia_total_m=r.distancia_inicial_m + 1),
            "la distancia final pasa de la inicial",
            id="final-mayor-que-inicial",
        ),
        pytest.param(
            lambda r: replace(r, mejora_2opt_m=r.mejora_2opt_m + 1),
            "la mejora no es la distancia inicial menos la final",
            id="mejora",
        ),
        pytest.param(
            lambda r: replace(r, distancia_regreso_deposito_m=r.distancia_regreso_deposito_m + 1),
            "sus tramos y el regreso no suman la distancia total",
            id="regreso",
        ),
        pytest.param(
            lambda r: replace(r, paradas=r.paradas[:-1]),
            "sus paradas no son sus cuentas de campo, una vez cada una",
            id="falta-una-parada",
        ),
        pytest.param(
            lambda r: replace(r, paradas=(*r.paradas[:-1], r.paradas[0])),
            "sus paradas no son sus cuentas de campo, una vez cada una",
            id="parada-repetida",
        ),
        pytest.param(
            lambda r: replace(
                r, paradas=tuple(replace(p, secuencia=p.secuencia - 1) for p in r.paradas)
            ),
            "su secuencia no va de 1 en 1 desde 1",
            id="secuencia-desde-0",
        ),
        pytest.param(
            lambda r: replace(r, clave_territorio="09002"),
            "es de otro municipio",
            id="otro-municipio",
        ),
    ],
)
def test_una_ruta_que_no_cuadra_no_se_publica(tmp_path, monkeypatch, alterar, problema):
    # Un nucleo con un error en la ruta del primer municipio: el servicio no la vuelve a calcular,
    # pero si revisa que cuadre.
    territorial = _organizada(tmp_path, CARTERA)

    def con_un_error(clave, clientes):
        ruta = rutear_territorio(clave, clientes)
        return alterar(ruta) if clave == "21114" else ruta

    monkeypatch.setattr(ejecuciones, "rutear_territorio", con_un_error)

    ejecucion = _ruteo_fallido(territorial.id)

    assert ejecucion.detalle == (
        f"La ruta del municipio 21114 no cuadra: {problema}; no se publico ninguna ruta."
    )
    assert (ejecucion.rutas_evaluadas, ejecucion.rutas_publicadas) == (3, 0)
    assert ejecucion.paradas_publicadas == 0


@en_la_base
def test_si_una_parada_se_repite_la_base_lo_impide_y_no_es_una_carrera(tmp_path, monkeypatch):
    # Un error que guarda todas las paradas en el primer lugar de su ruta: lo rechaza la restriccion
    # unica de parada_ruta, que no es el indice de exito. Es un error interno y no una carrera
    # perdida, asi que no levanta RuteoYaGenerado.
    territorial = _organizada(tmp_path, CARTERA)
    fila_parada = ejecuciones._fila_parada
    monkeypatch.setattr(ejecuciones, "_fila_parada", lambda *a: {**fila_parada(*a), "secuencia": 1})

    ejecucion = _ruteo_fallido(territorial.id)

    assert ejecucion.detalle == "Error interno al publicar; ver la bitacora del servicio."
    assert _contadores(ejecucion) == (3, 0, 8, 0)


@en_la_base
def test_el_cierre_no_publica_rutas_ni_paradas_que_faltan(tmp_path):
    # Las dos guardas del cierre, directo y dentro de una transaccion que despues se revierte: con
    # una ruta menos que municipios con campo, o con las rutas guardadas pero sin sus paradas, la
    # ejecucion no se cierra EXITOSA.
    territorial = _organizada(tmp_path, PEQUENA)
    ejecucion_id = _registrar(territorial.id)
    with sesion() as s:
        ejecucion = s.get_one(EjecucionRuteo, ejecucion_id)
        fuente, cuentas = ejecuciones._comprobar_fuente(s, ejecucion)
        rutas = ejecuciones._trazar(fuente, cuentas)

        with pytest.raises(ejecuciones._NoSePublica) as falta_una_ruta:
            ejecuciones._cerrar(s, ejecucion, fuente, rutas[:1])
        ejecuciones._insertar_rutas(s, ejecucion_id, fuente, rutas)
        with pytest.raises(ejecuciones._NoSePublica) as faltan_las_paradas:
            ejecuciones._cerrar(s, ejecucion, fuente, rutas)
        s.rollback()

    assert str(falta_una_ruta.value) == (
        "Las rutas estan incompletas: hay 2 municipios con campo, se calcularon 1 rutas y se "
        "guardaron 0; no se publico ninguna."
    )
    assert str(faltan_las_paradas.value) == (
        "Las paradas estan incompletas: hay 2 decisiones de campo, se calcularon 2 paradas y se "
        "guardaron 0; no se publico ninguna."
    )
    assert _ejecucion(ejecucion_id).estado == EstadoRuteo.EN_PROCESO
    assert (_cuantas(RutaTerritorial), _cuantas(ParadaRuta)) == (0, 0)


# --- un solo worker por ejecucion, y la carrera la decide la base --------------------------------


@en_la_base
def test_el_mismo_id_lo_procesa_un_solo_worker_y_el_otro_lo_encuentra_terminado(
    tmp_path, monkeypatch, caplog
):
    # Dos workers con la misma ejecucion, cada uno con su conexion. A la toma primero; B la pide
    # mientras A traza y se queda esperando el bloqueo de la fila. Cuando A publica y la suelta, B
    # la encuentra terminada: ni calcula, ni inserta, ni la degrada.
    territorial = _organizada(tmp_path, PEQUENA)
    ejecucion_id = _registrar(territorial.id)
    trazar = ejecuciones._trazar
    b: dict = {}

    def a_traza_mientras_b_espera(fuente, cuentas):
        b["hilo"], b["salida"] = _en_otro_hilo(ejecutar_ruteo, ejecucion_id)
        b["esperaba"] = _alguien_espera_la_fila()
        return trazar(fuente, cuentas)

    monkeypatch.setattr(ejecuciones, "_trazar", a_traza_mientras_b_espera)

    try:
        ejecutar_ruteo(ejecucion_id)  # el worker A
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
    assert ejecucion.estado == EstadoRuteo.EXITOSA
    assert _contadores(ejecucion) == (2, 2, 2, 2)
    assert (_cuantas(RutaTerritorial), _cuantas(ParadaRuta)) == (2, 2)  # las de A, una sola vez


@en_la_base
def test_la_base_no_admite_dos_ejecuciones_en_proceso_de_la_misma_fuente_y_version(tmp_path):
    # Dos peticiones pueden pasar la revision amable en el mismo instante: se registran aqui a
    # mano, sin revision. Desde la 0006 la segunda ya no entra: a lo mas un intento activo por
    # fuente y version. Otra version si, y una que ya termino no cuenta.
    territorial = _organizada(tmp_path, PEQUENA)
    activa = _registrar(territorial.id)

    with pytest.raises(IntegrityError, match="ux_ejecucion_ruteo_en_proceso"):
        _registrar(territorial.id)

    otra_version = _registrar(territorial.id, version="ruteo/v2")
    ejecutar_ruteo(otra_version)  # ruteo/v2 no se calcula aqui: queda FALLIDA
    assert [(e.id, e.version_reglas, e.estado) for e in _ruteos(territorial.id)] == [
        (activa, "ruteo/v1", EstadoRuteo.EN_PROCESO),
        (otra_version, "ruteo/v2", EstadoRuteo.FALLIDA),
    ]
    _registrar(territorial.id, version="ruteo/v2")  # la FALLIDA ya no la bloquea


@en_la_base
def test_si_otra_ejecucion_publica_mientras_esta_rutea_solo_una_publica(tmp_path, monkeypatch):
    # La garantia de no publicar dos veces sigue siendo el indice de las EXITOSA. Ya no puede haber
    # dos EN_PROCESO de la misma version que lleguen a cerrar a la vez: aqui la otra aparece
    # EXITOSA justo antes de que esta cierre, con sus rutas y paradas ya insertadas, como la dejaria
    # alguien que se salta la revision.
    territorial = _organizada(tmp_path, CARTERA)
    ejecucion_id = _registrar(territorial.id)
    cerrar = ejecuciones._cerrar
    rival = []

    def otra_publica_primero(s, ejecucion, *argumentos):
        with sesion() as otra:
            ganadora = EjecucionRuteo(
                ejecucion_territorial_id=territorial.id,
                version_reglas=VERSION_REGLAS_RUTEO,
                estado=EstadoRuteo.EXITOSA,
            )
            otra.add(ganadora)
            otra.commit()
            rival.append(ganadora.id)
        cerrar(s, ejecucion, *argumentos)

    monkeypatch.setattr(ejecuciones, "_cerrar", otra_publica_primero)

    with pytest.raises(RuteoYaGenerado) as exc:
        ejecutar_ruteo(ejecucion_id)

    # La perdedora hizo el trabajo, pero no publico nada, y a quien la ejecuto le dice cual gano.
    assert exc.value.previa.id == rival[0]
    perdio = _ejecucion(ejecucion_id)
    assert perdio.estado == EstadoRuteo.FALLIDA
    assert _contadores(perdio) == (3, 0, 8, 0)
    assert perdio.detalle == (
        "Otra ejecucion publico las rutas de esta ejecucion territorial con ruteo/v1 mientras esta "
        "se procesaba; no se publican dos veces."
    )
    assert (_cuantas(RutaTerritorial), _cuantas(ParadaRuta)) == (0, 0)


@en_la_base
def test_un_fallo_que_llega_tarde_no_degrada_la_ejecucion_que_otro_worker_dejo_exitosa(
    tmp_path, monkeypatch, caplog
):
    # El worker A falla y revierte, lo que suelta la fila; el B la toma, la publica y la deja
    # EXITOSA; y solo despues A registra su fallo. Se reproduce en ese orden y sin hilos: B corre
    # completo justo antes de que A llame a _fallar.
    territorial = _organizada(tmp_path, PEQUENA)
    ejecucion_id = _registrar(territorial.id)
    fallar = ejecuciones._fallar
    trazar = ejecuciones._trazar
    llamadas = []

    def falla_solo_en_a(fuente, cuentas):
        llamadas.append(fuente)
        if len(llamadas) == 1:
            raise RuntimeError("falla simulada en el worker A")
        return trazar(fuente, cuentas)

    def b_termina_antes_de_que_a_registre_su_fallo(*argumentos):
        monkeypatch.setattr(ejecuciones, "_fallar", fallar)
        # El rollback de A ya solto la fila; si no, B esperaria a A para siempre.
        assert _otro_worker_la_toma(ejecucion_id)
        ejecutar_ruteo(ejecucion_id)  # el worker B
        fallar(*argumentos)  # y ahora si, el fallo de A

    monkeypatch.setattr(ejecuciones, "_trazar", falla_solo_en_a)
    monkeypatch.setattr(ejecuciones, "_fallar", b_termina_antes_de_que_a_registre_su_fallo)

    ejecutar_ruteo(ejecucion_id)  # el worker A

    ejecucion = _ejecucion(ejecucion_id)
    assert ejecucion.estado == EstadoRuteo.EXITOSA
    assert _contadores(ejecucion) == (2, 2, 2, 2)
    assert ejecucion.detalle == "Se rutearon 2 cuentas de campo en 2 municipios con ruteo/v1."
    assert (_cuantas(RutaTerritorial, ejecucion_id), _cuantas(ParadaRuta, ejecucion_id)) == (2, 2)
    # El fallo de A queda en la bitacora, no en la ejecucion.
    assert any(
        "ya termino EXITOSA; este fallo no la cambia" in registro.getMessage()
        for registro in caplog.records
    )


@en_la_base
def test_registrar_un_fallo_no_cambia_una_ejecucion_que_ya_termino(tmp_path, monkeypatch):
    # El mecanismo de fallo, directo sobre ejecuciones terminales: la EXITOSA no se degrada, la
    # FALLIDA conserva el registro de su fallo, y no se borra ninguna ruta.
    territorial = _organizada(tmp_path, PEQUENA)
    fallida = _fallida_por_el_motor(monkeypatch, territorial.id)
    exitosa = rutear_territorial(territorial.id)
    terminadas = [exitosa, fallida]
    antes = [_ejecucion(ejecucion.id).model_dump() for ejecucion in terminadas]

    for ejecucion in terminadas:
        with sesion() as s:
            ejecuciones._fallar(s, ejecucion.id, ejecucion.ruteo_run_id, 99, 99, "Fallo tardio.")

    assert [_ejecucion(ejecucion.id).model_dump() for ejecucion in terminadas] == antes
    assert [fila["estado"] for fila in antes] == [EstadoRuteo.EXITOSA, EstadoRuteo.FALLIDA]
    assert (_cuantas(RutaTerritorial, exitosa.id), _cuantas(ParadaRuta, exitosa.id)) == (2, 2)


@en_la_base
def test_una_ejecucion_que_ya_termino_no_se_vuelve_a_ejecutar(tmp_path, monkeypatch):
    # Un reintento del mismo trabajo, por ejemplo: ni la EXITOSA ni la FALLIDA cambian en nada.
    territorial = _organizada(tmp_path, PEQUENA)
    fallida = _fallida_por_el_motor(monkeypatch, territorial.id)
    exitosa = rutear_territorial(territorial.id)
    antes = {e.id: _ejecucion(e.id).model_dump() for e in (exitosa, fallida)}
    paradas = _paradas(exitosa.id)

    for ejecucion_id in antes:
        with _sentencias() as sentencias:
            ejecutar_ruteo(ejecucion_id)
        # Solo tomo la fila, con FOR UPDATE, y la encontro terminada: ni leyo la fuente ni
        # escribio nada.
        (tomar,) = sentencias
        assert tomar.rstrip().endswith("FOR UPDATE")

    assert {i: _ejecucion(i).model_dump() for i in antes} == antes
    assert _paradas(exitosa.id) == paradas
    assert _cuantas(RutaTerritorial, fallida.id) == 0


@en_la_base
def test_una_fallida_se_reintenta_con_otra_ejecucion_y_queda_en_la_historia(tmp_path, monkeypatch):
    territorial = _organizada(tmp_path, PEQUENA)
    fallida = _fallida_por_el_motor(monkeypatch, territorial.id)
    assert _contadores(fallida) == (0, 0, 0, 0)
    assert fallida.detalle == "Error interno (RuntimeError); ver la bitacora."

    # Con el nucleo de vuelta, la misma fuente se reintenta: una FALLIDA no cuenta, y no se reabre.
    reintento = rutear_territorial(territorial.id)

    assert reintento.estado == EstadoRuteo.EXITOSA
    assert reintento.id != fallida.id
    historia = [(e.id, e.estado, e.version_reglas) for e in _ruteos(territorial.id)]
    assert historia == [
        (fallida.id, EstadoRuteo.FALLIDA, VERSION_REGLAS_RUTEO),
        (reintento.id, EstadoRuteo.EXITOSA, VERSION_REGLAS_RUTEO),
    ]
    assert (_cuantas(RutaTerritorial, fallida.id), _cuantas(RutaTerritorial, reintento.id)) == (
        0,
        2,
    )


@en_la_base
def test_las_sentencias_no_crecen_con_las_cuentas(tmp_path):
    claves = ("21114", "21156", "09005", "15033")

    def cartera(n: int) -> Cuentas:
        # n cuentas en los mismos cuatro municipios: las impares, de campo; las pares, digitales.
        return [
            (f"CU{1000 + i:08d}", claves[i % 4], 120 if i % 2 else 10, "1000.00", "DIGITAL")
            for i in range(n)
        ]

    vistas = {}
    for n, corte in ((10, "2026-09-30"), (100, "2026-10-31")):
        territorial = _organizada(tmp_path, cartera(n), f"cartera_{n}.csv", corte)
        ejecucion_id = _registrar(territorial.id)
        with _sentencias() as sentencias:
            ejecutar_ruteo(ejecucion_id)
        ejecucion = _ejecucion(ejecucion_id)
        assert ejecucion.estado == EstadoRuteo.EXITOSA, ejecucion.detalle
        assert ejecucion.paradas_publicadas == n // 2
        vistas[n] = [" ".join(sentencia.split()) for sentencia in sentencias]

    for sentencias in vistas.values():
        # El conteo y la lectura de las cuentas de campo, una vez cada uno; un INSERT para todas
        # las rutas y otro para todas las paradas.
        assert len([x for x in sentencias if "FROM decision_cuenta JOIN cuenta" in x]) == 2
        assert len([x for x in sentencias if x.startswith("INSERT INTO ruta_territorial")]) == 1
        assert len([x for x in sentencias if x.startswith("INSERT INTO parada_ruta")]) == 1
    # Diez veces mas cuentas, las mismas sentencias: nada se consulta ni se inserta cuenta por
    # cuenta.
    assert len(vistas[10]) == len(vistas[100])


# --- sin base de datos ---------------------------------------------------------------------------


def _municipio(id_: int, clave: str, cuentas_campo: int, lugar: int | None) -> SimpleNamespace:
    """Una fila de la consulta de municipios, sin base."""
    return SimpleNamespace(
        id=id_,
        cve_entidad=clave[:2],
        cve_municipio=clave[2:],
        cuentas_campo=cuentas_campo,
        posicion_campo=lugar,
    )


def _cuenta(decision_id: int, cliente: str, clave: str) -> SimpleNamespace:
    """Una fila de la lectura de cuentas de campo, sin base."""
    return SimpleNamespace(
        decision_cuenta_id=decision_id,
        cliente_unico=cliente,
        cve_entidad=clave[:2],
        cve_municipio=clave[2:],
    )


MUNICIPIOS = [
    _municipio(11, "21114", 5, 1),
    _municipio(12, "09002", 2, 2),
    _municipio(13, "15033", 1, 3),
    _municipio(14, "21001", 0, None),
]
CUENTAS_DE_CAMPO = [
    _cuenta(104, "CU00000004", "09002"),
    _cuenta(108, "CU00000008", "09002"),
    _cuenta(106, "CU00000006", "15033"),
    *(_cuenta(140 + n, f"CU000000{40 + n}", "21114") for n in range(1, 6)),
]


def _fuente() -> tuple[ejecuciones._Fuente, dict[str, dict[str, int]]]:
    municipios = ejecuciones._con_ruta(MUNICIPIOS)
    return ejecuciones._Fuente(municipios, 8), ejecuciones._repartir(municipios, CUENTAS_DE_CAMPO)


def test_solo_los_municipios_con_campo_tienen_ruta_y_en_su_orden():
    municipios = ejecuciones._con_ruta(MUNICIPIOS)

    assert municipios == (
        ejecuciones._Territorio(11, "21114", 5, 1),
        ejecuciones._Territorio(12, "09002", 2, 2),
        ejecuciones._Territorio(13, "15033", 1, 3),
    )


@pytest.mark.parametrize(
    ("municipio", "como"),
    [
        pytest.param(_municipio(14, "21001", 0, 4), "0 cuentas de campo y el lugar 4", id="sin"),
        pytest.param(
            _municipio(13, "15033", 1, None), "1 cuentas de campo y ningun lugar", id="con"
        ),
        pytest.param(
            _municipio(13, "15033", -1, None), "-1 cuentas de campo y ningun lugar", id="neg"
        ),
    ],
)
def test_un_municipio_incoherente_detiene_todo(municipio, como):
    with pytest.raises(ejecuciones._NoSePublica) as exc:
        ejecuciones._con_ruta([*MUNICIPIOS[:2], municipio])

    clave = municipio.cve_entidad + municipio.cve_municipio
    assert str(exc.value) == (
        f"El municipio {clave} de la ejecucion territorial no es coherente: tiene {como}; no se "
        "publico ninguna ruta."
    )


def test_cada_cliente_de_campo_vuelve_a_su_decision():
    _, cuentas = _fuente()

    assert cuentas == {
        "21114": {f"CU000000{40 + n}": 140 + n for n in range(1, 6)},
        "09002": {"CU00000004": 104, "CU00000008": 108},
        "15033": {"CU00000006": 106},
    }


@pytest.mark.parametrize(
    ("cuentas", "mensaje"),
    [
        pytest.param(
            [*CUENTAS_DE_CAMPO, _cuenta(199, "CU00000099", "21001")],
            "Hay cuentas de campo en el municipio 21001, que no tiene ruta en la ejecucion "
            "territorial; no se publico ninguna ruta.",
            id="municipio-sin-ruta",
        ),
        pytest.param(
            [*CUENTAS_DE_CAMPO, _cuenta(199, "CU00000004", "09002")],
            "Un cliente se repite entre las cuentas de campo del municipio 09002; no se publico "
            "ninguna ruta.",
            id="cliente-repetido",
        ),
        pytest.param(
            CUENTAS_DE_CAMPO[:-1],
            "El municipio 21114 tiene 5 cuentas de campo en la ejecucion territorial y 4 en la de "
            "decision; no se publico ninguna ruta.",
            id="falta-una",
        ),
    ],
)
def test_el_mapa_de_paradas_a_decisiones_es_uno_a_uno(cuentas, mensaje):
    with pytest.raises(ejecuciones._NoSePublica) as exc:
        ejecuciones._repartir(ejecuciones._con_ruta(MUNICIPIOS), cuentas)

    assert str(exc.value) == mensaje


def test_el_servicio_traza_con_el_nucleo_y_las_rutas_cuadran():
    fuente, cuentas = _fuente()

    rutas = ejecuciones._trazar(fuente, cuentas)

    assert [ruta.clave_territorio for ruta in rutas] == ["21114", "09002", "15033"]
    assert [
        (
            r.distancia_inicial_m,
            r.distancia_total_m,
            r.distancia_regreso_deposito_m,
            r.mejora_2opt_m,
        )
        for r in rutas
    ] == [(inicial, total, regreso, mejora) for _, _, _, inicial, total, regreso, mejora in RUTAS]
    assert {
        clave: [
            (p.cliente_unico, p.secuencia, p.x_m, p.y_m, p.distancia_desde_anterior_m)
            for p in ruta.paradas
        ]
        for clave, ruta in zip(cuentas, rutas, strict=True)
    } == PARADAS
    ejecuciones._comprobar_rutas(fuente, cuentas, rutas)  # no levanta nada


def test_un_cliente_que_el_nucleo_rechaza_detiene_todo(caplog):
    fuente, cuentas = _fuente()
    cuentas["09002"] = {"cu00000004": 104, "CU00000008": 108}

    with pytest.raises(ejecuciones._NoSePublica) as exc:
        ejecuciones._trazar(fuente, cuentas)

    assert str(exc.value) == (
        "Las cuentas de campo de un municipio no cumplen ruteo/v1; ver la bitacora. No se publico "
        "ninguna ruta."
    )
    assert any("cu00000004" in registro.getMessage() for registro in caplog.records)


@pytest.mark.parametrize(
    ("alterar", "problema"),
    [
        pytest.param(lambda r: replace(r, paradas=()), "no tiene paradas", id="sin-paradas"),
        pytest.param(
            lambda r: replace(r, clave_territorio="15033"), "es de otro municipio", id="otra-clave"
        ),
        pytest.param(
            lambda r: replace(r, distancia_inicial_m=r.distancia_total_m - 1),
            "la distancia final pasa de la inicial",
            id="final-mayor",
        ),
    ],
)
def test_una_ruta_que_no_cuadra_detiene_todo(alterar, problema):
    fuente, cuentas = _fuente()
    rutas = ejecuciones._trazar(fuente, cuentas)
    rutas[1] = alterar(rutas[1])

    with pytest.raises(ejecuciones._NoSePublica) as exc:
        ejecuciones._comprobar_rutas(fuente, cuentas, rutas)

    assert str(exc.value) == (
        f"La ruta del municipio 09002 no cuadra: {problema}; no se publico ninguna ruta."
    )


def test_las_rutas_y_las_paradas_se_guardan_como_enteros_planos():
    fuente, cuentas = _fuente()
    ruta = ejecuciones._trazar(fuente, cuentas)[0]

    fila_ruta = ejecuciones._fila_ruta(7, fuente.municipios[0], ruta)
    fila_parada = ejecuciones._fila_parada(7, 70, 141, ruta.paradas[0])

    assert fila_ruta == {
        "ejecucion_ruteo_id": 7,
        "resultado_territorial_id": 11,
        "paradas": 5,
        "distancia_inicial_m": 37878,
        "distancia_total_m": 28770,
        "distancia_regreso_deposito_m": 7633,
        "mejora_2opt_m": 9108,
    }
    assert fila_parada == {
        "ejecucion_ruteo_id": 7,
        "ruta_territorial_id": 70,
        "decision_cuenta_id": 141,
        "secuencia": 1,
        "x_m": 1016,
        "y_m": -2070,
        "distancia_desde_anterior_m": 3086,
    }
    # Ni el cliente ni la clave del municipio: se leen por JOIN.
    assert all(type(valor) is int for valor in (*fila_ruta.values(), *fila_parada.values()))


def _sql(consulta) -> str:
    return " ".join(str(consulta.compile(dialect=postgresql.psycopg.dialect())).split())


def test_la_ejecucion_se_toma_con_su_fila_bloqueada():
    sql = _sql(ejecuciones._bloqueada(7))

    # Solo la fila de esa ejecucion, bloqueada hasta que termine la transaccion que la toma.
    assert "FROM ejecucion_ruteo WHERE ejecucion_ruteo.id = " in sql
    assert sql.endswith("FOR UPDATE")


def test_las_cuentas_de_campo_se_leen_en_una_consulta_por_la_decision():
    sql = _sql(ejecuciones._cuentas_de_campo(7, 3))

    # Una sola consulta: las decisiones de la ejecucion, con su cuenta, solo si es de su corrida y
    # solo si la decision es CAMPO, con el id de la decision, el cliente y el municipio.
    assert sql.startswith(
        "SELECT decision_cuenta.id AS decision_cuenta_id, cuenta.cliente_unico, "
        "cuenta.cve_entidad, cuenta.cve_municipio "
        "FROM decision_cuenta JOIN cuenta ON decision_cuenta.cuenta_id = cuenta.id"
    )
    assert "decision_cuenta.ejecucion_decision_id = " in sql
    assert "cuenta.corrida_id = " in sql
    assert "decision_cuenta.canal_recomendado = " in sql
    assert sql.endswith("ORDER BY cuenta.cve_entidad, cuenta.cve_municipio, cuenta.cliente_unico")
    # Lo que hace de una cuenta una parada es el canal recomendado, nunca el de la cartera.
    for consulta in (ejecuciones._cuentas_de_campo(7, 3), ejecuciones._conteo_de_campo(7, 3)):
        assert re.search("(?<![a-z_])cuenta[.]canal(?![a-z_])", _sql(consulta)) is None


def test_los_municipios_se_leen_en_el_orden_de_territorial_v1():
    sql = _sql(ejecuciones._municipios(7))

    assert "FROM resultado_territorial WHERE resultado_territorial.ejecucion_territorial_id" in sql
    assert sql.endswith(
        "ORDER BY resultado_territorial.posicion_campo ASC NULLS LAST, "
        "resultado_territorial.cve_entidad, resultado_territorial.cve_municipio"
    )


def test_ruteo_v1_consume_territorial_v1_sobre_decision_v1_y_nada_mas():
    # Literales, y no las versiones del codigo de las otras capas: si el Motor Territorial pasa a
    # territorial/v2 o el Decision Engine a decision/v2, ruteo/v1 no los sigue solo.
    assert VERSION_TERRITORIAL_COMPATIBLE == "territorial/v1"
    assert VERSION_DECISION_COMPATIBLE == "decision/v1"
    assert not hasattr(ejecuciones, "VERSION_REGLAS_TERRITORIAL")
    assert not hasattr(ejecuciones, "VERSION_REGLAS_DECISION")
    # Y lo que hace de una cuenta una parada es un canal del vocabulario de decision/v1.
    assert CANAL_CAMPO == "CAMPO"
    assert CANAL_CAMPO in CANAL_POR_PRIORIDAD.values()


def test_el_paquete_de_ruteo_no_reexporta_el_servicio():
    # motor_cartera.ruteo se sigue importando sin base: el servicio se importa por su modulo.
    import motor_cartera.ruteo as ruteo

    servicio = {
        "abrir_ejecucion",
        "ejecutar_ruteo",
        "rutear_territorial",
        "TerritorialNoRuteable",
        "RuteoYaGenerado",
    }
    assert not servicio & set(ruteo.__all__)
    assert not servicio & set(dir(ruteo))
