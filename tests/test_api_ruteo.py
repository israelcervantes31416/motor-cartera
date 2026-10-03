"""La API del Motor de Ruteo: rutear una ejecucion territorial por HTTP y consultar lo que se
publico.

Las carteras se publican por POST /corridas, se deciden por POST /corridas/{run_id}/decisiones y
se organizan por POST /decisiones/{decision_run_id}/territoriales, como lo haria un cliente, y las
rutas se piden y se leen por la API. Las coordenadas, el recorrido y la transaccion ya se prueban en
el nucleo y en el servicio; aqui, que la capa HTTP traduzca bien: codigos, Location, errores, orden,
paginacion y consultas fijas por pagina. Las pruebas de la ultima seccion no tocan la base.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pandas as pd
import pytest
from sqlalchemy import event, update
from sqlmodel import func, select

from motor_cartera.api import ruteo as ruteo_api
from motor_cartera.api.esquemas import (
    EJEMPLO_EJECUCION_RUTEO,
    EJEMPLO_EJECUCION_RUTEO_FALLIDA,
    EJEMPLO_PARADA,
    EJEMPLO_RUTA,
    EjecucionRuteoRespuesta,
    ParadaRutaRespuesta,
    RutaTerritorialRespuesta,
)
from motor_cartera.db.modelos import (
    Corrida,
    Cuenta,
    DecisionCuenta,
    EjecucionDecision,
    EjecucionRuteo,
    EjecucionTerritorial,
    EstadoDecision,
    EstadoRuteo,
    EstadoTerritorial,
    ParadaRuta,
    ResultadoTerritorial,
    RutaTerritorial,
    ahora,
)
from motor_cartera.db.sesion import crear_motor, sesion
from motor_cartera.ruteo import ejecuciones as ruteo_ejecuciones
from motor_cartera.ruteo.reglas import VERSION_REGLAS_RUTEO, rutear_territorio
from motor_cartera.territorial import ejecuciones as territorial_ejecuciones

en_la_base = pytest.mark.usefixtures("bd")

CORTE = "2026-09-30"

CAMPOS_DE_ERROR = {"codigo", "mensaje", "detalles", "run_id"}
CAMPOS_DE_EJECUCION = {
    "ruteo_run_id",
    "territorial_run_id",
    "decision_run_id",
    "run_id",
    "version_reglas",
    "estado",
    "iniciada_en",
    "terminada_en",
    "duracion_segundos",
    "rutas_evaluadas",
    "rutas_publicadas",
    "paradas_evaluadas",
    "paradas_publicadas",
    "detalle",
}
CAMPOS_DE_RUTA = {
    "clave_territorio",
    "posicion_territorial",
    "cuentas_campo",
    "paradas",
    "distancia_inicial_m",
    "distancia_total_m",
    "distancia_regreso_deposito_m",
    "mejora_2opt_m",
}
CAMPOS_DE_PARADA = {"secuencia", "cliente_unico", "x_m", "y_m", "distancia_desde_anterior_m"}
CAMPOS_DEL_HISTORIAL = {
    "territorial_run_id",
    "decision_run_id",
    "run_id",
    "total",
    "pagina",
    "por_pagina",
    "elementos",
}
CAMPOS_DE_LAS_RUTAS = {
    "ruteo_run_id",
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
CAMPOS_DE_LAS_PARADAS = {
    "ruteo_run_id",
    "run_id",
    "version_reglas",
    "estado",
    "clave_territorio",
    "total",
    "pagina",
    "por_pagina",
    "elementos",
}

RUTAS = [
    ("POST", "/territoriales/{territorial_run_id}/ruteos"),
    ("GET", "/territoriales/{territorial_run_id}/ruteos"),
    ("GET", "/ruteos/{ruteo_run_id}"),
    ("GET", "/ruteos/{ruteo_run_id}/rutas"),
    ("GET", "/ruteos/{ruteo_run_id}/rutas/{clave_territorio}/paradas"),
]

Cuentas = list[tuple[str, str, int, str, str]]
"""Una cartera de prueba: por cuenta, (cliente_unico, clave del municipio, dias de atraso, saldo,
canal de la cartera). decision/v1 recomienda CAMPO con 91 dias o mas, o de 31 a 90 con saldo desde
50,000.00."""

# La misma cartera de las pruebas del servicio. A la derecha, el canal que decision/v1 recomienda.
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


def _ruta(clave, lugar, campo, paradas, inicial, total, regreso) -> dict:
    """Una ruta como la sirve la API."""
    return {
        "clave_territorio": clave,
        "posicion_territorial": lugar,
        "cuentas_campo": campo,
        "paradas": paradas,
        "distancia_inicial_m": inicial,
        "distancia_total_m": total,
        "distancia_regreso_deposito_m": regreso,
        "mejora_2opt_m": inicial - total,
    }


def _parada(cliente, secuencia, x, y, desde_anterior) -> dict:
    return {
        "secuencia": secuencia,
        "cliente_unico": cliente,
        "x_m": x,
        "y_m": y,
        "distancia_desde_anterior_m": desde_anterior,
    }


# Lo que ruteo/v1 publica de CARTERA, en el orden en que la API lo sirve. 21001 no tiene ruta.
RUTAS_GOLDEN = [
    _ruta("21114", 1, 5, 5, 37878, 28770, 7633),
    _ruta("09002", 2, 2, 2, 15416, 15416, 6211),
    _ruta("15033", 3, 1, 1, 11742, 11742, 5871),
]
PARADAS_GOLDEN = {
    "21114": [
        _parada("CU00000041", 1, 1016, -2070, 3086),
        _parada("CU00000044", 2, 1712, 1261, 4027),
        _parada("CU00000043", 3, 4679, 2833, 4539),
        _parada("CU00000042", 4, 966, 3811, 4691),
        _parada("CU00000045", 5, -3825, 3808, 4794),
    ],
    "09002": [
        _parada("CU00000004", 1, 3940, -1497, 5437),
        _parada("CU00000008", 2, 4268, 1943, 3768),
    ],
    "15033": [_parada("CU00000006", 1, 4268, 1603, 5871)],
}

# Tres cuentas en dos municipios, una de campo en cada uno.
PEQUENA: Cuentas = [
    ("CU00000041", "21114", 120, "3000.00", "DIGITAL"),  # CAMPO
    ("CU00000044", "21114", 0, "7000.00", "CAMPO"),  # DIGITAL
    ("CU00000004", "09002", 95, "1500.00", "CAMPO"),  # CAMPO
]


def _un_municipio_por_cuenta(n: int) -> Cuentas:
    """n cuentas, cada una en su propio municipio de 09 o de 21. Dos de cada tres son de campo, y
    cada una tiene otro saldo, para que ningun lugar se decida por la clave."""
    return [
        (
            f"CU{i + 1:08d}",
            ("09", "21")[i % 2] + f"{i // 2 + 1:03d}",
            120 if i % 3 else 0,
            f"{1000 + i}.00",
            "DIGITAL",
        )
        for i in range(n)
    ]


def _un_municipio(n: int) -> Cuentas:
    """n cuentas de campo, todas en 21114: una ruta de n paradas."""
    return [(f"CU{i + 1:08d}", "21114", 120, "1000.00", "DIGITAL") for i in range(n)]


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


def _publicar(cliente, cuentas: Cuentas) -> str:
    """Publica la cartera por POST /corridas y devuelve el run_id de la corrida EXITOSA."""
    respuesta = cliente.post(
        "/corridas", files={"archivo": ("cartera.csv", _csv(cuentas), "application/octet-stream")}
    )
    assert respuesta.status_code == 201, respuesta.text
    corrida = cliente.get(respuesta.headers["Location"]).json()
    assert corrida["estado"] == "EXITOSA", corrida["detalle"]
    return corrida["run_id"]


def _decidida(cliente, cuentas: Cuentas) -> tuple[str, str]:
    run_id = _publicar(cliente, cuentas)
    ejecucion = cliente.post(f"/corridas/{run_id}/decisiones").json()
    assert ejecucion["estado"] == "EXITOSA", ejecucion
    return run_id, ejecucion["decision_run_id"]


def _organizada(cliente, cuentas: Cuentas) -> tuple[str, str, str]:
    """La cartera publicada, decidida y organizada por la API: el run_id de su corrida, el
    decision_run_id y el territorial_run_id de sus ejecuciones EXITOSA."""
    run_id, decision_run_id = _decidida(cliente, cuentas)
    territorial = cliente.post(f"/decisiones/{decision_run_id}/territoriales").json()
    assert territorial["estado"] == "EXITOSA", territorial
    return run_id, decision_run_id, territorial["territorial_run_id"]


def _rutear(cliente, territorial_run_id: str):
    return cliente.post(f"/territoriales/{territorial_run_id}/ruteos")


def _ruteada(cliente, territorial_run_id: str) -> dict:
    """Rutea por la API y devuelve la ejecucion de ruteo EXITOSA."""
    ejecucion = _rutear(cliente, territorial_run_id).json()
    assert ejecucion["estado"] == "EXITOSA", ejecucion
    return ejecucion


def _id_de_territorial(territorial_run_id: str) -> int:
    with sesion() as s:
        return s.exec(
            select(EjecucionTerritorial.id).where(
                EjecucionTerritorial.territorial_run_id == UUID(territorial_run_id)
            )
        ).one()


def _id_de_decision(decision_run_id: str) -> int:
    with sesion() as s:
        return s.exec(
            select(EjecucionDecision.id).where(
                EjecucionDecision.decision_run_id == UUID(decision_run_id)
            )
        ).one()


def _registrar(territorial_id: int, **campos) -> EjecucionRuteo:
    """Una ejecucion de ruteo insertada a mano, como la dejaria el historial; por omision,
    FALLIDA."""
    instante = ahora()
    valores = {
        "version_reglas": VERSION_REGLAS_RUTEO,
        "estado": EstadoRuteo.FALLIDA,
        "iniciada_en": instante,
        "terminada_en": instante,
        "detalle": "Registrada a mano para la prueba.",
        **campos,
    }
    with sesion() as s:
        ejecucion = EjecucionRuteo(ejecucion_territorial_id=territorial_id, **valores)
        s.add(ejecucion)
        s.commit()
        s.refresh(ejecucion)
        return ejecucion


def _abierta(territorial_run_id: str) -> str:
    """Una ejecucion de ruteo abierta por el servicio y todavia sin trazar: como la ve otra
    peticion mientras el POST rutea, o como queda si el proceso muere a la mitad."""
    with sesion() as s:
        fuente = s.get_one(EjecucionTerritorial, _id_de_territorial(territorial_run_id))
        return str(ruteo_ejecuciones.abrir_ejecucion(s, fuente).ruteo_run_id)


def _ejecuciones_de_ruteo() -> int:
    with sesion() as s:
        return s.exec(select(func.count()).select_from(EjecucionRuteo)).one()


def _revienta(*_):
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


# --- POST /territoriales/{territorial_run_id}/ruteos -----------------------------------------


@en_la_base
def test_post_rutea_y_responde_201_con_location(cliente):
    run_id, decision_run_id, territorial_run_id = _organizada(cliente, CARTERA)

    respuesta = _rutear(cliente, territorial_run_id)

    assert respuesta.status_code == 201
    ejecucion = respuesta.json()
    assert set(ejecucion) == CAMPOS_DE_EJECUCION
    assert respuesta.headers["Location"] == f"/ruteos/{ejecucion['ruteo_run_id']}"
    assert (ejecucion["territorial_run_id"], ejecucion["decision_run_id"], ejecucion["run_id"]) == (
        territorial_run_id,
        decision_run_id,
        run_id,
    )
    assert (ejecucion["version_reglas"], ejecucion["estado"]) == ("ruteo/v1", "EXITOSA")
    assert ejecucion["rutas_evaluadas"] == ejecucion["rutas_publicadas"] == 3
    assert ejecucion["paradas_evaluadas"] == ejecucion["paradas_publicadas"] == 8
    assert ejecucion["detalle"] == "Se rutearon 8 cuentas de campo en 3 municipios con ruteo/v1."
    assert ejecucion["duracion_segundos"] >= 0
    assert ejecucion["iniciada_en"].endswith("Z") and ejecucion["terminada_en"].endswith("Z")
    # Location lleva al mismo recurso.
    assert cliente.get(respuesta.headers["Location"]).json() == ejecucion


@en_la_base
def test_post_con_el_motor_fallando_tambien_es_201_y_la_ejecucion_queda_fallida(
    cliente, monkeypatch
):
    # D1: la ejecucion se creo y su resultado es su estado; no es un error de la peticion.
    _, _, territorial_run_id = _organizada(cliente, PEQUENA)
    monkeypatch.setattr(ruteo_ejecuciones, "rutear_territorio", _revienta)

    respuesta = _rutear(cliente, territorial_run_id)

    assert respuesta.status_code == 201
    fallida = respuesta.json()
    assert respuesta.headers["Location"] == f"/ruteos/{fallida['ruteo_run_id']}"
    assert (fallida["estado"], fallida["version_reglas"]) == ("FALLIDA", "ruteo/v1")
    assert (fallida["rutas_evaluadas"], fallida["rutas_publicadas"]) == (0, 0)
    assert (fallida["paradas_evaluadas"], fallida["paradas_publicadas"]) == (0, 0)
    assert fallida["detalle"] == "Error interno (RuntimeError); ver la bitacora."
    assert "falla simulada" not in respuesta.text
    assert cliente.get(respuesta.headers["Location"]).json() == fallida


@en_la_base
def test_el_post_termina_la_transaccion_de_la_busqueda_antes_de_rutear(cliente, monkeypatch):
    # El POST es sincrono y el servicio abre sus propias sesiones. La transaccion de la sesion de
    # la peticion solo sirve para encontrar la ejecucion territorial: tiene que haber terminado al
    # entrar al servicio, y no volver a abrirse despues.
    _, _, territorial_run_id = _organizada(cliente, PEQUENA)
    buscar, rutear = ruteo_api._buscar_territorial, ruteo_api.rutear_territorial
    visto = {"transacciones": 0}

    def contar_transaccion(*_):
        visto["transacciones"] += 1

    def busca(s, territorial_run_id_pedido):
        event.listen(s, "after_begin", contar_transaccion)
        encontrada = buscar(s, territorial_run_id_pedido)
        visto["sesion"], visto["al_buscar"] = s, s.in_transaction()
        return encontrada

    def rutea(ejecucion_territorial_id):
        visto["al_rutear"] = visto["sesion"].in_transaction()
        visto["fuente_id"] = ejecucion_territorial_id
        return rutear(ejecucion_territorial_id)

    monkeypatch.setattr(ruteo_api, "_buscar_territorial", busca)
    monkeypatch.setattr(ruteo_api, "rutear_territorial", rutea)

    respuesta = _rutear(cliente, territorial_run_id)

    assert (respuesta.status_code, respuesta.json()["estado"]) == (201, "EXITOSA")
    # La busqueda leyo dentro de una transaccion, y esa ya no estaba al entrar al servicio.
    assert (visto["al_buscar"], visto["al_rutear"]) == (True, False)
    assert visto["fuente_id"] == _id_de_territorial(territorial_run_id)
    # Y la sesion de la peticion no abrio otra: ni el servicio la uso, ni nada volvio a leer con
    # ella despues del rollback.
    assert visto["transacciones"] == 1


@en_la_base
def test_post_otra_vez_409_ruteo_ya_generado_sin_location(cliente):
    run_id, _, territorial_run_id = _organizada(cliente, PEQUENA)
    primera = _ruteada(cliente, territorial_run_id)

    respuesta = _rutear(cliente, territorial_run_id)

    assert respuesta.status_code == 409
    error = respuesta.json()
    assert set(error) == CAMPOS_DE_ERROR
    assert (error["codigo"], error["run_id"]) == ("RUTEO_YA_GENERADO", run_id)
    assert error["mensaje"] == (
        "Los municipios de esta ejecucion territorial ya se rutearon con exito con ruteo/v1. "
        f"Consulta /territoriales/{territorial_run_id}/ruteos."
    )
    assert "Location" not in respuesta.headers
    # D2: el error no dice cual ejecucion fue, ni en un campo ni en el mensaje.
    assert primera["ruteo_run_id"] not in respuesta.text
    # La revision amable no deja ni el intento.
    assert _ejecuciones_de_ruteo() == 1


@en_la_base
def test_post_que_pierde_la_carrera_tambien_es_409_ruteo_ya_generado(cliente, monkeypatch):
    # La carrera que no ve la revision amable: mientras esta peticion rutea, otra ejecucion de la
    # misma ejecucion territorial publica primero. El indice de exito rechaza el cierre de esta, que
    # queda FALLIDA en el historial, y la peticion responde lo mismo que si la revision la hubiera
    # visto.
    run_id, _, territorial_run_id = _organizada(cliente, PEQUENA)
    rival = _registrar(
        _id_de_territorial(territorial_run_id),
        estado=EstadoRuteo.EN_PROCESO,
        terminada_en=None,
        detalle=None,
    )
    cerrar = ruteo_ejecuciones._cerrar

    def el_rival_publica_primero(s, ejecucion, *argumentos):
        monkeypatch.setattr(ruteo_ejecuciones, "_cerrar", cerrar)
        ruteo_ejecuciones.ejecutar_ruteo(rival.id)
        cerrar(s, ejecucion, *argumentos)

    monkeypatch.setattr(ruteo_ejecuciones, "_cerrar", el_rival_publica_primero)

    respuesta = _rutear(cliente, territorial_run_id)

    assert respuesta.status_code == 409
    assert (respuesta.json()["codigo"], respuesta.json()["run_id"]) == (
        "RUTEO_YA_GENERADO",
        run_id,
    )
    assert "Location" not in respuesta.headers
    assert str(rival.ruteo_run_id) not in respuesta.text
    historial = cliente.get(f"/territoriales/{territorial_run_id}/ruteos").json()["elementos"]
    estados = {e["ruteo_run_id"]: (e["estado"], e["rutas_publicadas"]) for e in historial}
    assert estados.pop(str(rival.ruteo_run_id)) == ("EXITOSA", 2)
    assert list(estados.values()) == [("FALLIDA", 0)]  # la de esta peticion


@en_la_base
@pytest.mark.parametrize("metodo", ["POST", "GET"])
def test_una_ejecucion_territorial_que_no_existe_404_sin_run_id(cliente, metodo):
    # No hay corrida que nombrar, y no se registra nada.
    territorial_run_id = uuid4()

    respuesta = cliente.request(metodo, f"/territoriales/{territorial_run_id}/ruteos")

    assert respuesta.status_code == 404
    error = respuesta.json()
    assert set(error) == CAMPOS_DE_ERROR
    assert (error["codigo"], error["run_id"]) == ("TERRITORIAL_NO_ENCONTRADO", None)
    assert error["mensaje"] == (
        f"No existe una ejecucion territorial con territorial_run_id {territorial_run_id}."
    )
    assert _ejecuciones_de_ruteo() == 0


def _territorial_en_proceso(cliente, decision_run_id: str, monkeypatch) -> str:
    # Abierta por el Motor Territorial y todavia sin organizar.
    with sesion() as s:
        fuente = s.get_one(EjecucionDecision, _id_de_decision(decision_run_id))
        return str(territorial_ejecuciones.abrir_ejecucion(s, fuente).territorial_run_id)


def _territorial_fallida(cliente, decision_run_id: str, monkeypatch) -> str:
    monkeypatch.setattr(territorial_ejecuciones, "priorizar_territorios", _revienta)
    fallida = cliente.post(f"/decisiones/{decision_run_id}/territoriales").json()
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
def test_post_sobre_una_territorial_que_no_publico_409_sin_ejecucion(
    cliente, monkeypatch, crear, estado
):
    run_id, decision_run_id = _decidida(cliente, PEQUENA)
    territorial_run_id = crear(cliente, decision_run_id, monkeypatch)

    respuesta = _rutear(cliente, territorial_run_id)

    assert respuesta.status_code == 409
    error = respuesta.json()
    assert set(error) == CAMPOS_DE_ERROR
    assert (error["codigo"], error["run_id"]) == ("TERRITORIAL_NO_RUTEABLE", run_id)
    assert error["mensaje"] == (
        f"La ejecucion territorial {territorial_run_id} esta {estado}; solo se rutean los "
        "municipios de una ejecucion territorial EXITOSA."
    )
    assert "Location" not in respuesta.headers
    assert _ejecuciones_de_ruteo() == 0


@en_la_base
def test_post_sobre_una_territorial_de_otra_version_409_sin_ejecucion(cliente):
    # Aunque este EXITOSA: ruteo/v1 solo rutea municipios de territorial/v1.
    run_id, decision_run_id = _decidida(cliente, PEQUENA)
    with sesion() as s:
        territorial = EjecucionTerritorial(
            ejecucion_decision_id=_id_de_decision(decision_run_id),
            version_reglas="territorial/v2",
            estado=EstadoTerritorial.EXITOSA,
        )
        s.add(territorial)
        s.commit()
        territorial_run_id = str(territorial.territorial_run_id)

    respuesta = _rutear(cliente, territorial_run_id)

    assert respuesta.status_code == 409
    error = respuesta.json()
    assert (error["codigo"], error["run_id"]) == ("TERRITORIAL_NO_RUTEABLE", run_id)
    assert error["mensaje"] == (
        f"La ejecucion territorial {territorial_run_id} se calculo con territorial/v2; ruteo/v1 "
        "solo rutea municipios de territorial/v1."
    )
    assert _ejecuciones_de_ruteo() == 0


@en_la_base
def test_post_sobre_decisiones_de_otra_version_409_sin_ejecucion(cliente):
    # Una territorial/v1 EXITOSA que sale de decisiones de decision/v2: la cadena no es la de
    # ruteo/v1.
    run_id = _publicar(cliente, PEQUENA)
    with sesion() as s:
        corrida_id = s.exec(select(Corrida.id).where(Corrida.run_id == UUID(run_id))).one()
        decision = EjecucionDecision(
            corrida_id=corrida_id, version_reglas="decision/v2", estado=EstadoDecision.EXITOSA
        )
        s.add(decision)
        s.commit()
        territorial = EjecucionTerritorial(
            ejecucion_decision_id=decision.id,
            version_reglas="territorial/v1",
            estado=EstadoTerritorial.EXITOSA,
        )
        s.add(territorial)
        s.commit()
        decision_run_id, territorial_run_id = (
            decision.decision_run_id,
            territorial.territorial_run_id,
        )

    respuesta = _rutear(cliente, str(territorial_run_id))

    assert respuesta.status_code == 409
    error = respuesta.json()
    assert (error["codigo"], error["run_id"]) == ("TERRITORIAL_NO_RUTEABLE", run_id)
    assert error["mensaje"] == (
        f"La ejecucion de decision {decision_run_id}, de la que sale la territorial, se decidio "
        "con decision/v2; ruteo/v1 solo rutea decisiones de decision/v1."
    )
    assert _ejecuciones_de_ruteo() == 0


@en_la_base
def test_d2_el_409_no_dice_cual_ejecucion_y_el_historial_si(cliente):
    # D2 de punta a punta: rutear, chocar con lo ya publicado y encontrarlo en el historial.
    _, _, territorial_run_id = _organizada(cliente, PEQUENA)
    primera = _rutear(cliente, territorial_run_id)
    assert (primera.status_code, primera.json()["estado"]) == (201, "EXITOSA")

    otra = _rutear(cliente, territorial_run_id)
    assert (otra.status_code, otra.json()["codigo"]) == (409, "RUTEO_YA_GENERADO")
    assert "Location" not in otra.headers
    assert primera.json()["ruteo_run_id"] not in otra.text

    historial = cliente.get(f"/territoriales/{territorial_run_id}/ruteos").json()["elementos"]
    (publicada,) = [ejecucion for ejecucion in historial if ejecucion["estado"] == "EXITOSA"]
    assert publicada["version_reglas"] == "ruteo/v1"
    assert publicada["ruteo_run_id"] == primera.json()["ruteo_run_id"]
    assert cliente.get(f"/ruteos/{publicada['ruteo_run_id']}").json() == primera.json()


# --- GET /territoriales/{territorial_run_id}/ruteos ------------------------------------------


@en_la_base
def test_el_historial_sin_ejecuciones_de_ruteo_es_una_lista_vacia(cliente, monkeypatch):
    # Tambien de una ejecucion territorial que no publico: el historial no exige que este EXITOSA.
    run_id, decision_run_id = _decidida(cliente, PEQUENA)
    with monkeypatch.context() as parche:
        fallida = _territorial_fallida(cliente, decision_run_id, parche)
    exitosa = cliente.post(f"/decisiones/{decision_run_id}/territoriales").json()
    assert exitosa["estado"] == "EXITOSA", exitosa

    for territorial_run_id in (exitosa["territorial_run_id"], fallida):
        respuesta = cliente.get(f"/territoriales/{territorial_run_id}/ruteos")
        assert respuesta.status_code == 200
        assert respuesta.json() == {
            "territorial_run_id": territorial_run_id,
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
    # Una FALLIDA y una EXITOSA de ruteo/v1, por la API, una FALLIDA de otra version y una que
    # sigue EN_PROCESO.
    run_id, decision_run_id, territorial_run_id = _organizada(cliente, PEQUENA)
    with monkeypatch.context() as parche:
        parche.setattr(ruteo_ejecuciones, "rutear_territorio", _revienta)
        fallida = _rutear(cliente, territorial_run_id).json()
    exitosa = _rutear(cliente, territorial_run_id).json()  # la FALLIDA se reintenta
    territorial_id = _id_de_territorial(territorial_run_id)
    otra_version = _registrar(territorial_id, version_reglas="ruteo/v2")
    en_proceso = _registrar(
        territorial_id, estado=EstadoRuteo.EN_PROCESO, terminada_en=None, detalle=None
    )

    respuesta = cliente.get(f"/territoriales/{territorial_run_id}/ruteos")

    assert respuesta.status_code == 200
    historial = respuesta.json()
    assert set(historial) == CAMPOS_DEL_HISTORIAL
    assert (
        historial["territorial_run_id"],
        historial["decision_run_id"],
        historial["run_id"],
        historial["total"],
    ) == (territorial_run_id, decision_run_id, run_id, 4)
    for ejecucion in historial["elementos"]:
        # Lo publico de cada ejecucion, sin su id ni los de su cadena.
        assert set(ejecucion) == CAMPOS_DE_EJECUCION
        assert (ejecucion["territorial_run_id"], ejecucion["run_id"]) == (
            territorial_run_id,
            run_id,
        )
    por_id = {ejecucion["ruteo_run_id"]: ejecucion for ejecucion in historial["elementos"]}
    assert (fallida["estado"], exitosa["estado"]) == ("FALLIDA", "EXITOSA")
    assert por_id[fallida["ruteo_run_id"]] == fallida
    assert por_id[exitosa["ruteo_run_id"]] == exitosa
    historica = por_id[str(otra_version.ruteo_run_id)]
    assert (historica["version_reglas"], historica["estado"]) == ("ruteo/v2", "FALLIDA")
    assert historica["detalle"] == "Registrada a mano para la prueba."
    abierta = por_id[str(en_proceso.ruteo_run_id)]
    assert (abierta["estado"], abierta["terminada_en"], abierta["duracion_segundos"]) == (
        "EN_PROCESO",
        None,
        None,
    )


@en_la_base
def test_el_historial_va_de_la_mas_reciente_a_la_mas_antigua(cliente):
    # Fuera del orden en que se insertan, y con un empate en el instante, que desempata el id.
    _, _, territorial_run_id = _organizada(cliente, PEQUENA)
    territorial_id = _id_de_territorial(territorial_run_id)
    mediodia = datetime(2026, 9, 30, 12, tzinfo=UTC)

    def iniciada(instante: datetime) -> EjecucionRuteo:
        return _registrar(
            territorial_id, iniciada_en=instante, terminada_en=instante + timedelta(minutes=1)
        )

    diez = iniciada(mediodia - timedelta(hours=2))
    primera_de_mediodia = iniciada(mediodia)
    segunda_de_mediodia = iniciada(mediodia)
    nueve = iniciada(mediodia - timedelta(hours=3))  # la mas antigua, insertada al final

    elementos = cliente.get(f"/territoriales/{territorial_run_id}/ruteos").json()["elementos"]

    assert [e["ruteo_run_id"] for e in elementos] == [
        str(e.ruteo_run_id) for e in (segunda_de_mediodia, primera_de_mediodia, diez, nueve)
    ]


@en_la_base
def test_el_historial_se_pagina_sin_huecos_ni_repetidos(cliente):
    _, _, territorial_run_id = _organizada(cliente, PEQUENA)
    territorial_id = _id_de_territorial(territorial_run_id)
    for _ in range(7):
        _registrar(territorial_id)
    ruta = f"/territoriales/{territorial_run_id}/ruteos"
    todas = [e["ruteo_run_id"] for e in cliente.get(ruta).json()["elementos"]]

    paginas = [
        cliente.get(ruta, params={"pagina": p, "por_pagina": 3}).json() for p in (1, 2, 3, 4)
    ]

    assert [len(p["elementos"]) for p in paginas] == [3, 3, 1, 0]
    assert {p["total"] for p in paginas} == {7}
    assert [e["ruteo_run_id"] for p in paginas for e in p["elementos"]] == todas
    assert len(set(todas)) == 7


# --- GET /ruteos/{ruteo_run_id} ----------------------------------------------------------------


@en_la_base
def test_una_ejecucion_de_ruteo_se_consulta_por_su_ruteo_run_id(cliente):
    run_id, decision_run_id, territorial_run_id = _organizada(cliente, PEQUENA)
    ruteo_run_id = _ruteada(cliente, territorial_run_id)["ruteo_run_id"]

    respuesta = cliente.get(f"/ruteos/{ruteo_run_id}")

    assert respuesta.status_code == 200
    with sesion() as s:
        guardada = s.exec(
            select(EjecucionRuteo).where(EjecucionRuteo.ruteo_run_id == UUID(ruteo_run_id))
        ).one()
    ejecucion = respuesta.json()
    assert set(ejecucion) == CAMPOS_DE_EJECUCION
    assert (
        ejecucion["ruteo_run_id"],
        ejecucion["territorial_run_id"],
        ejecucion["decision_run_id"],
        ejecucion["run_id"],
    ) == (ruteo_run_id, territorial_run_id, decision_run_id, run_id)
    assert (ejecucion["version_reglas"], ejecucion["estado"], ejecucion["detalle"]) == (
        guardada.version_reglas,
        guardada.estado,
        guardada.detalle,
    )
    assert (
        ejecucion["rutas_evaluadas"],
        ejecucion["rutas_publicadas"],
        ejecucion["paradas_evaluadas"],
        ejecucion["paradas_publicadas"],
    ) == (2, 2, 2, 2)
    assert datetime.fromisoformat(ejecucion["iniciada_en"]) == guardada.iniciada_en
    assert datetime.fromisoformat(ejecucion["terminada_en"]) == guardada.terminada_en
    assert ejecucion["duracion_segundos"] == round(
        (guardada.terminada_en - guardada.iniciada_en).total_seconds(), 3
    )
    # Cada identificador publico nombra su propio recurso: no se intercambian.
    no_es_ruteo = cliente.get(f"/ruteos/{territorial_run_id}")
    assert (no_es_ruteo.status_code, no_es_ruteo.json()["codigo"]) == (404, "RUTEO_NO_ENCONTRADO")
    no_es_territorial = cliente.get(f"/territoriales/{ruteo_run_id}/ruteos")
    assert (no_es_territorial.status_code, no_es_territorial.json()["codigo"]) == (
        404,
        "TERRITORIAL_NO_ENCONTRADO",
    )


@en_la_base
def test_una_ejecucion_de_ruteo_en_proceso_se_consulta_sin_fin_ni_duracion(cliente):
    run_id, _, territorial_run_id = _organizada(cliente, PEQUENA)
    ruteo_run_id = _abierta(territorial_run_id)

    respuesta = cliente.get(f"/ruteos/{ruteo_run_id}")

    assert respuesta.status_code == 200
    ejecucion = respuesta.json()
    assert set(ejecucion) == CAMPOS_DE_EJECUCION
    assert (ejecucion["territorial_run_id"], ejecucion["run_id"]) == (territorial_run_id, run_id)
    assert (ejecucion["version_reglas"], ejecucion["estado"]) == ("ruteo/v1", "EN_PROCESO")
    assert (ejecucion["terminada_en"], ejecucion["duracion_segundos"]) == (None, None)
    assert (ejecucion["rutas_publicadas"], ejecucion["paradas_publicadas"]) == (0, 0)


@en_la_base
@pytest.mark.parametrize(
    "ruta", ["/ruteos/{}", "/ruteos/{}/rutas", "/ruteos/{}/rutas/21114/paradas"]
)
def test_una_ejecucion_de_ruteo_que_no_existe_404_sin_run_id(cliente, ruta):
    ruteo_run_id = uuid4()

    respuesta = cliente.get(ruta.format(ruteo_run_id))

    assert respuesta.status_code == 404
    error = respuesta.json()
    assert set(error) == CAMPOS_DE_ERROR
    assert (error["codigo"], error["run_id"]) == ("RUTEO_NO_ENCONTRADO", None)
    assert error["mensaje"] == f"No existe una ejecucion de ruteo con ruteo_run_id {ruteo_run_id}."


# --- GET /ruteos/{ruteo_run_id}/rutas ----------------------------------------------------------


@en_la_base
def test_las_rutas_de_una_ejecucion_exitosa(cliente):
    # Exactamente lo que ruteo/v1 publico de cada municipio, en el orden de prioridad territorial.
    run_id, decision_run_id, territorial_run_id = _organizada(cliente, CARTERA)
    ejecucion = _ruteada(cliente, territorial_run_id)

    respuesta = cliente.get(f"/ruteos/{ejecucion['ruteo_run_id']}/rutas")

    assert respuesta.status_code == 200
    pagina = respuesta.json()
    assert set(pagina) == CAMPOS_DE_LAS_RUTAS
    assert (
        pagina["ruteo_run_id"],
        pagina["territorial_run_id"],
        pagina["decision_run_id"],
        pagina["run_id"],
    ) == (ejecucion["ruteo_run_id"], territorial_run_id, decision_run_id, run_id)
    assert (pagina["version_reglas"], pagina["estado"]) == ("ruteo/v1", "EXITOSA")
    assert (pagina["total"], pagina["pagina"], pagina["por_pagina"]) == (3, 1, 50)
    assert pagina["total"] == ejecucion["rutas_publicadas"]
    # Cada ruta con sus campos publicos, sin su id ni el de su ejecucion o su municipio.
    for ruta in pagina["elementos"]:
        assert set(ruta) == CAMPOS_DE_RUTA
    assert pagina["elementos"] == RUTAS_GOLDEN


@en_la_base
def test_el_total_de_rutas_sale_de_la_tabla_y_no_del_contador(cliente):
    # El contador se descompone a proposito: el total tiene que seguir siendo lo que hay guardado.
    _, _, territorial_run_id = _organizada(cliente, CARTERA)
    ruteo_run_id = _ruteada(cliente, territorial_run_id)["ruteo_run_id"]
    with sesion() as s:
        s.execute(
            update(EjecucionRuteo)
            .where(EjecucionRuteo.ruteo_run_id == UUID(ruteo_run_id))
            .values(rutas_publicadas=999)
        )
        s.commit()

    pagina = cliente.get(f"/ruteos/{ruteo_run_id}/rutas").json()

    assert pagina["total"] == len(pagina["elementos"]) == 3


def _ruteo_a_mano(territorial_run_id: str) -> tuple[EjecucionRuteo, dict[str, int]]:
    """Una ejecucion de ruteo EXITOSA insertada a mano, sin rutas, y los ResultadoTerritorial de
    su ejecucion territorial por clave."""
    ejecucion = _registrar(_id_de_territorial(territorial_run_id), estado=EstadoRuteo.EXITOSA)
    with sesion() as s:
        municipios = s.exec(
            select(
                ResultadoTerritorial.cve_entidad,
                ResultadoTerritorial.cve_municipio,
                ResultadoTerritorial.id,
            ).where(
                ResultadoTerritorial.ejecucion_territorial_id == ejecucion.ejecucion_territorial_id
            )
        ).all()
    return ejecucion, {entidad + municipio: id_ for entidad, municipio, id_ in municipios}


@en_la_base
def test_las_rutas_van_por_lugar_territorial_y_no_por_como_se_guardaron(cliente):
    # Guardadas a mano y en desorden: el orden lo pone la consulta, no el orden en que se
    # insertaron.
    _, _, territorial_run_id = _organizada(cliente, CARTERA)
    ejecucion, municipios = _ruteo_a_mano(territorial_run_id)
    with sesion() as s:
        for clave in ("15033", "21114", "09002"):
            s.add(
                RutaTerritorial(
                    ejecucion_ruteo_id=ejecucion.id,
                    resultado_territorial_id=municipios[clave],
                    paradas=1,
                    distancia_inicial_m=10,
                    distancia_total_m=10,
                    distancia_regreso_deposito_m=5,
                    mejora_2opt_m=0,
                )
            )
            s.flush()  # una por una, en este orden
        s.commit()

    elementos = cliente.get(f"/ruteos/{ejecucion.ruteo_run_id}/rutas").json()["elementos"]

    assert [(r["clave_territorio"], r["posicion_territorial"]) for r in elementos] == [
        ("21114", 1),
        ("09002", 2),
        ("15033", 3),
    ]


@en_la_base
def test_las_rutas_se_paginan_sin_huecos_ni_repetidos_en_el_orden_territorial(cliente):
    # 120 municipios: 80 con cuentas de campo, una ruta de una parada cada uno, y 40 sin ninguna.
    _, _, territorial_run_id = _organizada(cliente, _un_municipio_por_cuenta(120))
    ruteo_run_id = _ruteada(cliente, territorial_run_id)["ruteo_run_id"]
    ruta = f"/ruteos/{ruteo_run_id}/rutas"
    todas = cliente.get(ruta, params={"por_pagina": 500}).json()["elementos"]

    paginas = [cliente.get(ruta, params={"pagina": p, "por_pagina": 50}).json() for p in (1, 2, 3)]

    assert [len(p["elementos"]) for p in paginas] == [50, 30, 0]
    assert {p["total"] for p in paginas} == {80}
    assert [r for p in paginas for r in p["elementos"]] == todas
    assert len({r["clave_territorio"] for r in todas}) == 80  # cada una, una vez
    # El orden es el de la prioridad territorial: del lugar 1 al 80, sin huecos.
    assert [r["posicion_territorial"] for r in todas] == list(range(1, 81))
    assert all(r["paradas"] == r["cuentas_campo"] == 1 for r in todas)
    municipios = cliente.get(
        f"/territoriales/{territorial_run_id}/municipios", params={"por_pagina": 500}
    ).json()["elementos"]
    con_lugar = [m["clave_territorio"] for m in municipios if m["posicion_campo"] is not None]
    assert [r["clave_territorio"] for r in todas] == con_lugar


def _ruteo_en_proceso(cliente, territorial_run_id: str, monkeypatch) -> str:
    return _abierta(territorial_run_id)


def _ruteo_fallido(cliente, territorial_run_id: str, monkeypatch) -> str:
    monkeypatch.setattr(ruteo_ejecuciones, "rutear_territorio", _revienta)
    fallida = _rutear(cliente, territorial_run_id).json()
    assert fallida["estado"] == "FALLIDA", fallida
    return fallida["ruteo_run_id"]


@en_la_base
@pytest.mark.parametrize("ruta", ["/ruteos/{}/rutas", "/ruteos/{}/rutas/21114/paradas"])
@pytest.mark.parametrize(
    ("crear", "estado"),
    [
        pytest.param(_ruteo_en_proceso, "EN_PROCESO", id="EN_PROCESO"),
        pytest.param(_ruteo_fallido, "FALLIDA", id="FALLIDA"),
    ],
)
def test_las_rutas_y_paradas_de_una_ejecucion_que_no_publico_409(
    cliente, monkeypatch, crear, estado, ruta
):
    # No una lista vacia: diria que no habia municipios que rutear.
    run_id, _, territorial_run_id = _organizada(cliente, PEQUENA)
    ruteo_run_id = crear(cliente, territorial_run_id, monkeypatch)

    respuesta = cliente.get(ruta.format(ruteo_run_id))

    assert respuesta.status_code == 409
    error = respuesta.json()
    assert set(error) == CAMPOS_DE_ERROR
    assert (error["codigo"], error["run_id"]) == ("RUTEO_NO_PUBLICADO", run_id)
    assert error["mensaje"] == (
        f"La ejecucion de ruteo esta {estado} y no publico rutas; solo una ejecucion EXITOSA "
        "tiene rutas y paradas."
    )


@en_la_base
def test_una_pagina_de_rutas_son_tres_consultas_sean_10_o_100(cliente):
    # Sin N+1: la ejecucion con los identificadores de su cadena, el total y la pagina.
    _, _, territorial_run_id = _organizada(cliente, _un_municipio_por_cuenta(160))
    ruteo_run_id = _ruteada(cliente, territorial_run_id)["ruteo_run_id"]
    vistas = {}

    for por_pagina in (10, 100):
        with _sentencias() as sentencias:
            pagina = cliente.get(
                f"/ruteos/{ruteo_run_id}/rutas", params={"por_pagina": por_pagina}
            ).json()
        assert len(pagina["elementos"]) == por_pagina
        vistas[por_pagina] = [" ".join(sentencia.split()) for sentencia in sentencias]

    assert len(vistas[10]) == len(vistas[100]) == 3
    for buscar, contar, leer in vistas.values():
        # La ejecucion llega con un JOIN a toda su cadena.
        assert "JOIN ejecucion_territorial" in buscar and "JOIN corrida" in buscar
        assert contar.startswith("SELECT count(*)") and "FROM ruta_territorial" in contar
        # Y la pagina, en una sola consulta, con el municipio por JOIN y en orden territorial.
        assert "FROM ruta_territorial JOIN resultado_territorial" in leer
        assert "ORDER BY resultado_territorial.posicion_campo" in leer


# --- GET /ruteos/{ruteo_run_id}/rutas/{clave_territorio}/paradas -------------------------------


@en_la_base
def test_las_paradas_de_cada_ruta(cliente):
    # Exactamente lo que ruteo/v1 publico, en el orden de visita de cada municipio.
    run_id, _, territorial_run_id = _organizada(cliente, CARTERA)
    ruteo_run_id = _ruteada(cliente, territorial_run_id)["ruteo_run_id"]

    for clave, paradas in PARADAS_GOLDEN.items():
        respuesta = cliente.get(f"/ruteos/{ruteo_run_id}/rutas/{clave}/paradas")

        assert respuesta.status_code == 200
        pagina = respuesta.json()
        assert set(pagina) == CAMPOS_DE_LAS_PARADAS
        assert (pagina["ruteo_run_id"], pagina["run_id"], pagina["clave_territorio"]) == (
            ruteo_run_id,
            run_id,
            clave,
        )
        assert (pagina["version_reglas"], pagina["estado"]) == ("ruteo/v1", "EXITOSA")
        assert (pagina["total"], pagina["pagina"], pagina["por_pagina"]) == (len(paradas), 1, 50)
        # Cada parada con sus campos publicos: ni su id, ni el de su decision o su cuenta.
        for parada in pagina["elementos"]:
            assert set(parada) == CAMPOS_DE_PARADA
        assert pagina["elementos"] == paradas


@en_la_base
def test_las_paradas_van_en_el_orden_de_visita_y_no_como_se_guardaron(cliente):
    # Guardadas a mano y al reves: el orden lo pone la secuencia.
    _, decision_run_id, territorial_run_id = _organizada(cliente, CARTERA)
    ejecucion, municipios = _ruteo_a_mano(territorial_run_id)
    with sesion() as s:
        ruta = RutaTerritorial(
            ejecucion_ruteo_id=ejecucion.id,
            resultado_territorial_id=municipios["21114"],
            paradas=5,
            distancia_inicial_m=10,
            distancia_total_m=10,
            distancia_regreso_deposito_m=5,
            mejora_2opt_m=0,
        )
        s.add(ruta)
        s.flush()
        decisiones = dict(
            s.exec(
                select(Cuenta.cliente_unico, DecisionCuenta.id)
                .select_from(DecisionCuenta)
                .join(Cuenta, DecisionCuenta.cuenta_id == Cuenta.id)
                .where(DecisionCuenta.ejecucion_decision_id == _id_de_decision(decision_run_id))
            ).all()
        )
        for secuencia, parada in reversed(list(enumerate(PARADAS_GOLDEN["21114"], 1))):
            s.add(
                ParadaRuta(
                    ejecucion_ruteo_id=ejecucion.id,
                    ruta_territorial_id=ruta.id,
                    decision_cuenta_id=decisiones[parada["cliente_unico"]],
                    secuencia=secuencia,
                    x_m=-secuencia,
                    y_m=secuencia,
                    distancia_desde_anterior_m=secuencia * 10,
                )
            )
            s.flush()  # una por una, de la ultima a la primera
        s.commit()

    elementos = cliente.get(f"/ruteos/{ejecucion.ruteo_run_id}/rutas/21114/paradas").json()[
        "elementos"
    ]

    assert [(p["secuencia"], p["cliente_unico"], p["x_m"]) for p in elementos] == [
        (n, parada["cliente_unico"], -n) for n, parada in enumerate(PARADAS_GOLDEN["21114"], 1)
    ]


@en_la_base
def test_el_total_de_paradas_sale_de_la_tabla_y_no_del_conteo_de_la_ruta(cliente):
    _, _, territorial_run_id = _organizada(cliente, CARTERA)
    ruteo_run_id = _ruteada(cliente, territorial_run_id)["ruteo_run_id"]
    with sesion() as s:
        s.execute(update(RutaTerritorial).values(paradas=999))
        s.commit()

    pagina = cliente.get(f"/ruteos/{ruteo_run_id}/rutas/21114/paradas").json()

    assert pagina["total"] == len(pagina["elementos"]) == 5


@en_la_base
@pytest.mark.parametrize(
    "clave",
    [
        pytest.param("21001", id="municipio-sin-cuentas-de-campo"),
        pytest.param("99999", id="municipio-de-otra-territorial"),
    ],
)
def test_una_ruta_que_no_existe_404(cliente, clave):
    run_id, _, territorial_run_id = _organizada(cliente, CARTERA)
    ruteo_run_id = _ruteada(cliente, territorial_run_id)["ruteo_run_id"]

    respuesta = cliente.get(f"/ruteos/{ruteo_run_id}/rutas/{clave}/paradas")

    assert respuesta.status_code == 404
    error = respuesta.json()
    assert set(error) == CAMPOS_DE_ERROR
    assert (error["codigo"], error["run_id"]) == ("RUTA_NO_ENCONTRADA", run_id)
    assert error["mensaje"] == (
        f"La ejecucion de ruteo {ruteo_run_id} no tiene ruta para el municipio {clave}: no tiene "
        "cuentas de campo, o no es de su ejecucion territorial."
    )


@en_la_base
def test_las_paradas_se_paginan_sin_huecos_ni_repetidos_en_el_orden_de_visita(cliente):
    _, _, territorial_run_id = _organizada(cliente, _un_municipio(12))
    ruteo_run_id = _ruteada(cliente, territorial_run_id)["ruteo_run_id"]
    ruta = f"/ruteos/{ruteo_run_id}/rutas/21114/paradas"
    todas = cliente.get(ruta, params={"por_pagina": 500}).json()["elementos"]

    paginas = [
        cliente.get(ruta, params={"pagina": p, "por_pagina": 5}).json() for p in (1, 2, 3, 4)
    ]

    assert [len(p["elementos"]) for p in paginas] == [5, 5, 2, 0]
    assert {p["total"] for p in paginas} == {12}
    assert [x for p in paginas for x in p["elementos"]] == todas
    assert [x["secuencia"] for x in todas] == list(range(1, 13))
    assert len({x["cliente_unico"] for x in todas}) == 12
    # Y son exactamente la ruta que ruteo/v1 traza para esos doce clientes.
    esperada = rutear_territorio("21114", [cliente for cliente, *_ in _un_municipio(12)])
    assert [x["cliente_unico"] for x in todas] == [p.cliente_unico for p in esperada.paradas]


@en_la_base
def test_una_pagina_de_paradas_son_cuatro_consultas_sean_5_o_50(cliente):
    # Sin N+1: la ejecucion, la ruta del municipio, el total y la pagina con el cliente por JOIN.
    _, _, territorial_run_id = _organizada(cliente, _un_municipio(60))
    ruteo_run_id = _ruteada(cliente, territorial_run_id)["ruteo_run_id"]
    vistas = {}

    for por_pagina in (5, 50):
        with _sentencias() as sentencias:
            pagina = cliente.get(
                f"/ruteos/{ruteo_run_id}/rutas/21114/paradas", params={"por_pagina": por_pagina}
            ).json()
        assert len(pagina["elementos"]) == por_pagina
        vistas[por_pagina] = [" ".join(sentencia.split()) for sentencia in sentencias]

    assert len(vistas[5]) == len(vistas[50]) == 4
    for buscar, ruta, contar, leer in vistas.values():
        assert "JOIN ejecucion_territorial" in buscar and "JOIN corrida" in buscar
        assert "FROM ruta_territorial JOIN resultado_territorial" in ruta
        assert contar.startswith("SELECT count(*)") and "FROM parada_ruta" in contar
        assert "FROM parada_ruta JOIN decision_cuenta" in leer and "JOIN cuenta" in leer
        assert "ORDER BY parada_ruta.secuencia" in leer


# --- sin base de datos -------------------------------------------------------------------------


@pytest.mark.parametrize(("metodo", "ruta"), RUTAS)
@pytest.mark.parametrize(
    ("clave", "codigo"), [("", "API_KEY_AUSENTE"), ("otra", "API_KEY_INVALIDA")]
)
def test_las_rutas_de_ruteo_piden_api_key(cliente, metodo, ruta, clave, codigo):
    # La clave se revisa antes de buscar nada.
    url = ruta.format(territorial_run_id=uuid4(), ruteo_run_id=uuid4(), clave_territorio="21114")

    respuesta = cliente.request(metodo, url, headers={"X-API-Key": clave})

    assert respuesta.status_code == 401
    assert set(respuesta.json()) == CAMPOS_DE_ERROR
    assert respuesta.json()["codigo"] == codigo


@pytest.mark.parametrize(
    ("metodo", "ruta", "campo"),
    [
        ("POST", "/territoriales/123/ruteos", "path.territorial_run_id"),
        ("GET", "/territoriales/123/ruteos", "path.territorial_run_id"),
        ("GET", "/ruteos/123", "path.ruteo_run_id"),
        ("GET", "/ruteos/123/rutas", "path.ruteo_run_id"),
        ("GET", "/ruteos/123/rutas/21114/paradas", "path.ruteo_run_id"),
    ],
)
def test_un_identificador_que_no_es_uuid_422(cliente, metodo, ruta, campo):
    respuesta = cliente.request(metodo, ruta)

    assert respuesta.status_code == 422
    assert set(respuesta.json()) == CAMPOS_DE_ERROR
    assert respuesta.json()["codigo"] == "ENTRADA_INVALIDA"
    assert [detalle["campo"] for detalle in respuesta.json()["detalles"]] == [campo]


@pytest.mark.parametrize(
    "clave",
    [
        pytest.param("2111", id="cuatro-digitos"),
        pytest.param("211144", id="seis-digitos"),
        pytest.param("2111A", id="letra"),
        pytest.param("21%20114", id="espacio"),
        pytest.param("%D9%A2%D9%A1%D9%A1%D9%A1%D9%A4", id="arabigo-indica"),
        pytest.param("%EF%BC%92%EF%BC%91%EF%BC%91%EF%BC%91%EF%BC%94", id="ancho-completo"),
    ],
)
def test_una_clave_de_territorio_que_no_son_cinco_digitos_422(cliente, clave):
    respuesta = cliente.get(f"/ruteos/{uuid4()}/rutas/{clave}/paradas")

    assert respuesta.status_code == 422
    error = respuesta.json()
    assert set(error) == CAMPOS_DE_ERROR
    assert error["codigo"] == "ENTRADA_INVALIDA"
    assert error["detalles"] == [
        {"campo": "path.clave_territorio", "problema": "Debe cumplir el patron ^[0-9]{5}$."}
    ]


@pytest.mark.parametrize(
    "ruta",
    [
        "/territoriales/{}/ruteos",
        "/ruteos/{}/rutas",
        "/ruteos/{}/rutas/21114/paradas",
    ],
)
@pytest.mark.parametrize(
    ("parametros", "campo"),
    [({"pagina": 0}, "query.pagina"), ({"por_pagina": 501}, "query.por_pagina")],
)
def test_paginacion_fuera_de_rango_422(cliente, ruta, parametros, campo):
    respuesta = cliente.get(ruta.format(uuid4()), params=parametros)

    assert respuesta.status_code == 422
    assert respuesta.json()["codigo"] == "ENTRADA_INVALIDA"
    assert [detalle["campo"] for detalle in respuesta.json()["detalles"]] == [campo]


def test_openapi_documenta_las_cinco_operaciones_de_ruteo(app):
    api = app.openapi()
    operaciones = {
        (metodo.upper(), ruta): operacion
        for ruta, metodos in api["paths"].items()
        for metodo, operacion in metodos.items()
        if "ruteo" in operacion.get("tags", [])
    }

    assert {clave: set(op["responses"]) for clave, op in operaciones.items()} == {
        ("POST", "/territoriales/{territorial_run_id}/ruteos"): {"201", "401", "404", "409", "422"},
        ("GET", "/territoriales/{territorial_run_id}/ruteos"): {"200", "401", "404", "422"},
        ("GET", "/ruteos/{ruteo_run_id}"): {"200", "401", "404", "422"},
        ("GET", "/ruteos/{ruteo_run_id}/rutas"): {"200", "401", "404", "409", "422"},
        ("GET", "/ruteos/{ruteo_run_id}/rutas/{clave_territorio}/paradas"): {
            "200",
            "401",
            "404",
            "409",
            "422",
        },
    }
    modelos = {}
    for (metodo, ruta), operacion in operaciones.items():
        assert operacion["tags"] == ["ruteo"]
        assert operacion["summary"] and operacion["description"]
        exito = operacion["responses"]["201" if metodo == "POST" else "200"]
        modelos[(metodo, ruta)] = exito["content"]["application/json"]["schema"]["$ref"]
        # Todo error, con el mismo esquema que el resto de la API.
        for codigo, respuesta in operacion["responses"].items():
            if codigo[0] in "45":
                esquema = respuesta["content"]["application/json"]["schema"]
                assert esquema["$ref"].endswith("/ErrorRespuesta")
    assert {clave: ref.rsplit("/", 1)[1] for clave, ref in modelos.items()} == {
        ("POST", "/territoriales/{territorial_run_id}/ruteos"): "EjecucionRuteoRespuesta",
        ("GET", "/territoriales/{territorial_run_id}/ruteos"): "PaginaEjecucionesRuteo",
        ("GET", "/ruteos/{ruteo_run_id}"): "EjecucionRuteoRespuesta",
        ("GET", "/ruteos/{ruteo_run_id}/rutas"): "PaginaRutas",
        ("GET", "/ruteos/{ruteo_run_id}/rutas/{clave_territorio}/paradas"): "PaginaParadas",
    }

    # 201 dice que la ejecucion se creo, no que el motor tuviera exito: documenta las dos salidas.
    post = operaciones[("POST", "/territoriales/{territorial_run_id}/ruteos")]
    creada = post["responses"]["201"]
    assert "FALLIDA" in creada["description"] and "FALLIDA" in post["description"]
    assert set(creada["content"]["application/json"]["examples"]) == {"EXITOSA", "FALLIDA"}
    assert "`TERRITORIAL_NO_ENCONTRADO`" in post["responses"]["404"]["description"]
    assert "`TERRITORIAL_NO_RUTEABLE`" in post["responses"]["409"]["description"]
    assert "`RUTEO_YA_GENERADO`" in post["responses"]["409"]["description"]
    historial = operaciones[("GET", "/territoriales/{territorial_run_id}/ruteos")]
    assert "`TERRITORIAL_NO_ENCONTRADO`" in historial["responses"]["404"]["description"]
    for ruta in ("/ruteos/{ruteo_run_id}", "/ruteos/{ruteo_run_id}/rutas"):
        assert (
            "`RUTEO_NO_ENCONTRADO`" in operaciones[("GET", ruta)]["responses"]["404"]["description"]
        )
    rutas = operaciones[("GET", "/ruteos/{ruteo_run_id}/rutas")]
    assert "`RUTEO_NO_PUBLICADO`" in rutas["responses"]["409"]["description"]
    paradas = operaciones[("GET", "/ruteos/{ruteo_run_id}/rutas/{clave_territorio}/paradas")]
    assert "`RUTEO_NO_ENCONTRADO`" in paradas["responses"]["404"]["description"]
    assert "`RUTA_NO_ENCONTRADA`" in paradas["responses"]["404"]["description"]
    assert "`RUTEO_NO_PUBLICADO`" in paradas["responses"]["409"]["description"]
    (clave,) = [p for p in paradas["parameters"] if p["name"] == "clave_territorio"]
    assert clave["schema"]["pattern"] == "^[0-9]{5}$"
    # Lo sintetico se dice en la documentacion, no solo en el codigo.
    descripcion = " ".join(post["description"].split())
    assert "Las rutas son sinteticas." in descripcion
    assert "No son latitud ni longitud, domicilios, calles, trafico ni tiempos" in descripcion
    esquemas = api["components"]["schemas"]
    for campo in ("x_m", "y_m"):
        assert "sintetica" in esquemas["ParadaRutaRespuesta"]["properties"][campo]["description"]
    assert (
        "sinteticos"
        in esquemas["RutaTerritorialRespuesta"]["properties"]["distancia_total_m"]["description"]
    )
    assert {
        "name": "ruteo",
        "description": "Ejecuciones versionadas del Motor de Ruteo, su ruta por municipio y sus "
        "paradas en orden de visita. Coordenadas y distancias sinteticas: metros de un plano "
        "local por municipio, no geografia real.",
    } in api["tags"]
    assert "ruteo sintetico" in api["info"]["description"]


def test_el_error_sigue_siendo_el_de_toda_la_api(app):
    # Los errores nuevos usan ErrorRespuesta tal cual: sin ruteo_run_id ni territorial_run_id, y con
    # run_id, que sigue siendo el de la corrida.
    esquema = app.openapi()["components"]["schemas"]["ErrorRespuesta"]

    assert set(esquema["properties"]) == CAMPOS_DE_ERROR
    assert esquema["required"] == ["codigo", "mensaje"]
    run_id = esquema["properties"]["run_id"]
    assert run_id["description"] == "La corrida con la que tiene que ver el error, si hay una."


def test_los_esquemas_de_ruteo_no_exponen_ids_internos(app):
    esquemas = app.openapi()["components"]["schemas"]

    assert set(esquemas["EjecucionRuteoRespuesta"]["properties"]) == CAMPOS_DE_EJECUCION
    assert set(esquemas["RutaTerritorialRespuesta"]["properties"]) == CAMPOS_DE_RUTA
    assert set(esquemas["ParadaRutaRespuesta"]["properties"]) == CAMPOS_DE_PARADA
    assert set(esquemas["PaginaEjecucionesRuteo"]["properties"]) == CAMPOS_DEL_HISTORIAL
    assert set(esquemas["PaginaRutas"]["properties"]) == CAMPOS_DE_LAS_RUTAS
    assert set(esquemas["PaginaParadas"]["properties"]) == CAMPOS_DE_LAS_PARADAS
    internos = {
        "id",
        "cuenta_id",
        "decision_cuenta_id",
        "ruta_territorial_id",
        "ejecucion_ruteo_id",
    }
    for nombre in ("EjecucionRuteoRespuesta", "RutaTerritorialRespuesta", "ParadaRutaRespuesta"):
        assert not set(esquemas[nombre]["properties"]) & internos
    # Las distancias y las coordenadas son enteros; el estado, el catalogo del modelo.
    for nombre, campos in (
        ("RutaTerritorialRespuesta", CAMPOS_DE_RUTA - {"clave_territorio"}),
        ("ParadaRutaRespuesta", CAMPOS_DE_PARADA - {"cliente_unico"}),
    ):
        for campo in campos:
            assert esquemas[nombre]["properties"][campo]["type"] == "integer"
    estado = esquemas["EjecucionRuteoRespuesta"]["properties"]["estado"]
    assert estado["$ref"].endswith("/EstadoRuteo")


def test_los_ejemplos_de_ruteo_son_respuestas_posibles():
    # Traen cada campo de la respuesta; el OpenAPI omite los que valen null.
    campos = set(EjecucionRuteoRespuesta.model_json_schema(mode="serialization")["properties"])
    assert set(EJEMPLO_EJECUCION_RUTEO) == set(EJEMPLO_EJECUCION_RUTEO_FALLIDA) == campos
    fallida = EJEMPLO_EJECUCION_RUTEO_FALLIDA
    assert (fallida["estado"], fallida["rutas_publicadas"], fallida["paradas_publicadas"]) == (
        "FALLIDA",
        0,
        0,
    )
    # Y la ruta y la parada del ejemplo son lo que ruteo/v1 traza de verdad para esos clientes.
    assert RutaTerritorialRespuesta(**EJEMPLO_RUTA).model_dump() == EJEMPLO_RUTA
    assert ParadaRutaRespuesta(**EJEMPLO_PARADA).model_dump() == EJEMPLO_PARADA
    ruta = rutear_territorio("21114", [f"CU{n:08d}" for n in range(41, 46)])
    assert (
        EJEMPLO_RUTA["paradas"],
        EJEMPLO_RUTA["distancia_inicial_m"],
        EJEMPLO_RUTA["distancia_total_m"],
        EJEMPLO_RUTA["distancia_regreso_deposito_m"],
        EJEMPLO_RUTA["mejora_2opt_m"],
    ) == (
        len(ruta.paradas),
        ruta.distancia_inicial_m,
        ruta.distancia_total_m,
        ruta.distancia_regreso_deposito_m,
        ruta.mejora_2opt_m,
    )
    primera = ruta.paradas[0]
    assert EJEMPLO_PARADA == _parada(
        primera.cliente_unico,
        primera.secuencia,
        primera.x_m,
        primera.y_m,
        primera.distancia_desde_anterior_m,
    )
