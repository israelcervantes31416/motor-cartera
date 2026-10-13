"""El cliente de OSRM, con un transporte de pruebas: lo que envia (POST con JSON, las coordenadas
como sus decimales, nunca en la URL), como lee lo que recibe (decimales, nunca float), los lotes,
los bloques de la matriz, la huella de la red en cada respuesta, los reintentos acotados, los
errores que no se reintentan y la conexion que OSRM cerro. No necesita OSRM ni la base."""

from __future__ import annotations

import http.client
import json
from decimal import Decimal

import pytest

from motor_cartera.ruteo_v2.proveedor import (
    BackendIncompatible,
    ClienteOsrm,
    ConfiguracionOsrm,
    FallaProveedorRuteo,
    data_version,
    proveedor_configurado,
)

HUELLA = "a" * 64


class Transporte:
    """Lo que el cliente envio, y lo que responde a cada peticion: una funcion de (ruta, cuerpo)."""

    def __init__(self, responder):
        self.responder = responder
        self.enviadas: list[tuple[str, dict | None]] = []

    def __call__(self, ruta, cuerpo, _timeout):
        datos = None if cuerpo is None else json.loads(cuerpo)
        self.enviadas.append((ruta, datos))
        respuesta = self.responder(ruta, datos)
        if isinstance(respuesta, BaseException):
            raise respuesta
        estado, contenido = respuesta
        return estado, contenido if isinstance(contenido, bytes) else json.dumps(contenido).encode()


def cliente(monkeypatch, responder, **opciones) -> tuple[ClienteOsrm, Transporte]:
    c = ClienteOsrm(ConfiguracionOsrm("http://osrm:5000", backoff_segundos=0, **opciones))
    transporte = Transporte(responder)
    monkeypatch.setattr(c, "_enviar", transporte)
    return c, transporte


def ok(**campos) -> tuple[int, dict]:
    return 200, {"code": "Ok", "data_version": data_version(HUELLA), **campos}


def nearest(_ruta, datos):
    return ok(
        waypoints=[
            [{"distance": 10.5 + i, "location": [0, 0]}] for i in range(len(datos["coordinates"]))
        ]
    )


def test_envia_post_con_las_coordenadas_en_el_cuerpo_y_lee_decimales(monkeypatch):
    c, t = cliente(monkeypatch, nearest)

    distancias = c.sesion(HUELLA).snaps([("-98.123456", "19.000001"), ("-98.2", "19.5")])

    assert distancias == [Decimal("10.5"), Decimal("11.5")]
    assert all(isinstance(d, Decimal) for d in distancias)
    ((ruta, cuerpo),) = t.enviadas
    assert ruta == "/nearest/v1/driving"
    assert "98" not in ruta  # ninguna coordenada en la URL
    assert cuerpo == {
        "coordinates": [[-98.123456, 19.000001], [-98.2, 19.5]],
        "generate_hints": False,
        "number": 1,
    }


def test_el_cuerpo_lleva_los_decimales_tal_cual(monkeypatch):
    crudos: list[bytes] = []
    c = ClienteOsrm(ConfiguracionOsrm("http://osrm:5000"))

    def transporte(_ruta, cuerpo, _timeout):
        crudos.append(cuerpo)
        return 200, json.dumps(
            {
                "code": "Ok",
                "data_version": data_version(HUELLA),
                "waypoints": [[{"distance": 1}]] * 2,
            }
        ).encode()

    monkeypatch.setattr(c, "_enviar", transporte)
    c.sesion(HUELLA).snaps([("-98.100000", "19.000000"), ("0.000001", "-0.000001")])

    assert b"[-98.100000,19.000000],[0.000001,-0.000001]" in crudos[0]
    assert b"e-" not in crudos[0].lower()


def test_los_snaps_van_por_lotes_y_un_punto_solo_se_envia_dos_veces(monkeypatch):
    c, t = cliente(monkeypatch, nearest, lote_nearest=2)

    distancias = c.sesion(HUELLA).snaps([(str(i), "1") for i in range(5)])

    assert len(distancias) == 5
    assert [len(datos["coordinates"]) for _, datos in t.enviadas] == [2, 2, 2]
    # El ultimo lote tenia un punto: va dos veces, para que la respuesta sea la de varios puntos.
    assert t.enviadas[-1][1]["coordinates"] == [[4, 1], [4, 1]]


def test_un_punto_sin_segmento_es_sin_snap_y_no_tumba_el_lote(monkeypatch):
    def responder(_ruta, _datos):
        return ok(
            waypoints=[
                [{"distance": 3}],
                {"code": "NoSegment", "message": "Could not find a matching segment"},
            ]
        )

    c, _ = cliente(monkeypatch, responder)

    assert c.sesion(HUELLA).snaps([("1", "1"), ("2", "2")]) == [Decimal(3), None]


def test_cada_respuesta_trae_la_huella_de_la_red_o_falla_cerrado(monkeypatch):
    def otra_red(_ruta, datos):
        return 200, {"code": "Ok", "data_version": data_version("b" * 64), "waypoints": []}

    c, _ = cliente(monkeypatch, otra_red)
    with pytest.raises(BackendIncompatible):
        c.sesion(HUELLA).snaps([("1", "1"), ("2", "2")])

    sin_huella, _ = cliente(monkeypatch, lambda _r, _d: (200, {"code": "Ok", "waypoints": []}))
    with pytest.raises(BackendIncompatible) as error:
        sin_huella.sesion(HUELLA).snaps([("1", "1"), ("2", "2")])
    assert error.value.declarada is None


def test_la_sonda_verifica_la_red_sin_enviar_ningun_punto(monkeypatch):
    c, t = cliente(monkeypatch, lambda _r, _d: ok(waypoints=[{"distance": 1e7}]))

    c.sesion(HUELLA).verificar()
    with pytest.raises(BackendIncompatible):
        c.sesion("c" * 64).verificar()

    assert {ruta for ruta, _ in t.enviadas} == {
        "/nearest/v1/driving/0,0?number=1&generate_hints=false"
    }
    assert all(cuerpo is None for _, cuerpo in t.enviadas)
    # Si la sonda no trae la red (NoSegment), no decide: la primera respuesta la verifica.
    sin_segmento, _ = cliente(monkeypatch, lambda _r, _d: (400, {"code": "NoSegment"}))
    sin_segmento.sesion(HUELLA).verificar()


def test_la_salud_es_que_responda_algo_de_osrm(monkeypatch):
    arriba, _ = cliente(monkeypatch, lambda _r, _d: (400, {"code": "NoSegment"}))
    caido, _ = cliente(monkeypatch, lambda _r, _d: ConnectionRefusedError("sin conexion"))
    roto, _ = cliente(monkeypatch, lambda _r, _d: (200, b"<html>"))

    assert arriba.salud() is True
    assert caido.salud() is False
    assert roto.salud() is False


def tabla(_ruta, datos, sin_camino=frozenset({9})):
    """Una tabla de pruebas por la longitud de cada punto: la duracion entre dos puntos i y j es
    10 * |i - j| + 0.5 y la distancia, cien veces eso; un punto de `sin_camino` no tiene camino con
    nadie. Como OSRM, una tabla de un solo punto es InvalidOptions."""
    if len(datos["coordinates"]) < 2:
        return 400, {
            "code": "InvalidOptions",
            "message": "Number of coordinates needs to be at least two.",
        }
    puntos = [int(c[0]) for c in datos["coordinates"]]
    origenes = datos.get("sources", list(range(len(puntos))))
    destinos = datos.get("destinations", list(range(len(puntos))))

    def celda(a, b, factor):
        if puntos[a] == puntos[b]:
            return 0
        if sin_camino & {puntos[a], puntos[b]}:
            return None
        return (abs(puntos[a] - puntos[b]) * 10 + 0.5) * factor

    return ok(
        durations=[[celda(a, b, 1) for b in destinos] for a in origenes],
        distances=[[celda(a, b, 100) for b in destinos] for a in origenes],
    )


def _coordenadas(n: int) -> list[tuple[str, str]]:
    return [(str(i), "0") for i in range(n)]


def test_la_matriz_por_bloques_es_la_misma_que_en_un_solo_bloque(monkeypatch):
    c, t = cliente(monkeypatch, tabla)
    puntos = _coordenadas(7)

    entera = c.sesion(HUELLA).matriz(puntos, bloque=100)
    por_bloques = c.sesion(HUELLA).matriz(puntos, bloque=3)

    assert entera == por_bloques
    assert entera.duracion[0][6] == Decimal("60.5")
    assert entera.distancia[6][0] == Decimal("6050.0")
    # 7 puntos en bloques de 3: 3 x 3 peticiones, mas la del bloque unico.
    assert len(t.enviadas) == 1 + 9
    for _, cuerpo in t.enviadas[1:]:
        n_origenes = len(cuerpo.get("sources", cuerpo["coordinates"]))
        n_destinos = len(cuerpo.get("destinations", cuerpo["coordinates"]))
        assert n_origenes * n_destinos <= 9
        assert len(cuerpo["coordinates"]) >= 2
        assert cuerpo["annotations"] == ["duration", "distance"]
        assert cuerpo["skip_waypoints"] is True
    # El septimo punto queda solo en su bloque de la diagonal: va como origen y como destino.
    assert t.enviadas[-1][1]["coordinates"] == [[6, 0], [6, 0]]
    assert (t.enviadas[-1][1]["sources"], t.enviadas[-1][1]["destinations"]) == ([0], [1])
    assert entera.duracion[6][6] == 0 and entera.distancia[6][6] == 0


def test_un_punto_sin_camino_es_none_y_nunca_una_distancia_inventada(monkeypatch):
    c, _ = cliente(monkeypatch, tabla)

    m = c.sesion(HUELLA).matriz([("0", "0"), ("9", "0"), ("2", "0")], bloque=100)

    assert m.duracion[0][1] is None and m.duracion[1][0] is None
    assert m.distancia[0][1] is None
    assert m.duracion[0][2] == Decimal("20.5")


def test_una_respuesta_incompleta_o_incoherente_es_un_fallo_tecnico(monkeypatch):
    for respuesta, tipo in (
        (ok(durations=[[0]], distances=[[0]]), "RespuestaIncompleta"),
        (ok(durations=[[0, 1], [1, 0]], distances=[[0, None], [1, 0]]), "CeldaIncoherente"),
        (ok(durations=[[0, "x"], [1, 0]], distances=[[0, 1], [1, 0]]), "RespuestaIncompleta"),
        ((400, {"code": "NoSegment"}), "OSRM NoSegment"),
    ):
        c, _ = cliente(monkeypatch, lambda _r, _d, respuesta=respuesta: respuesta)
        with pytest.raises(FallaProveedorRuteo) as error:
            c.sesion(HUELLA).matriz(_coordenadas(2), bloque=100)
        assert error.value.tipo == tipo


def test_un_fallo_transitorio_se_reintenta_con_un_tope(monkeypatch):
    respuestas = iter(
        [TimeoutError("lento"), (503, b"ocupado"), ok(waypoints=[[{"distance": 1}]] * 2)]
    )
    c, t = cliente(monkeypatch, lambda _r, _d: next(respuestas), reintentos=2)
    sesion = c.sesion(HUELLA)

    assert sesion.snaps(_coordenadas(2)) == [Decimal(1), Decimal(1)]
    assert len(t.enviadas) == 3
    assert sesion.metricas.reintentos == 2

    siempre, t = cliente(monkeypatch, lambda _r, _d: ConnectionRefusedError("x"), reintentos=2)
    with pytest.raises(FallaProveedorRuteo) as error:
        siempre.sesion(HUELLA).snaps(_coordenadas(2))
    assert (error.value.tipo, error.value.intentos) == ("ConnectionRefusedError", 3)
    assert len(t.enviadas) == 3


@pytest.mark.parametrize("codigo", ["TooBig", "InvalidQuery", "InvalidOptions", "DisabledDataset"])
def test_lo_que_dice_que_la_peticion_o_el_servidor_no_son_los_esperados_no_se_reintenta(
    monkeypatch, codigo
):
    c, t = cliente(
        monkeypatch, lambda _r, _d: (400, {"code": codigo, "message": "..."}), reintentos=5
    )

    with pytest.raises(FallaProveedorRuteo) as error:
        c.sesion(HUELLA).matriz(_coordenadas(2), bloque=100)

    assert error.value.tipo == f"OSRM {codigo}"
    assert len(t.enviadas) == 1


def test_un_fallo_no_lleva_la_peticion_en_su_mensaje_ni_en_su_causa(monkeypatch):
    def con_coordenadas(_r, _d):
        return OSError("POST http://osrm:5000/table/v1/driving/-98.1234,19.4326 fallo")

    c, _ = cliente(monkeypatch, con_coordenadas, reintentos=0)

    with pytest.raises(FallaProveedorRuteo) as error:
        c.sesion(HUELLA).snaps(_coordenadas(2))

    assert "-98.1234" not in str(error.value)
    assert error.value.__cause__ is None and error.value.__suppress_context__


@pytest.mark.parametrize(
    "cerrada",
    [
        http.client.RemoteDisconnected("cerrada"),
        ConnectionResetError(10054, "reset"),
        # En Windows, segun el momento, la misma conexion cerrada se ve como un aborto.
        ConnectionAbortedError(10053, "abortada"),
        BrokenPipeError(32, "tubo roto"),
    ],
    ids=["desconectada", "reset", "abortada", "tubo-roto"],
)
def test_una_conexion_que_osrm_cerro_se_reabre_sin_gastar_un_intento(monkeypatch, cerrada):
    c = ClienteOsrm(ConfiguracionOsrm("http://osrm:5000", reintentos=0))
    llamadas = []

    def una_vez(_ruta, _cuerpo, _timeout):
        llamadas.append(1)
        if len(llamadas) == 1:
            raise cerrada
        return 200, json.dumps(
            {
                "code": "Ok",
                "data_version": data_version(HUELLA),
                "waypoints": [[{"distance": 2}]] * 2,
            }
        ).encode()

    monkeypatch.setattr(c, "_una_vez", una_vez)
    c._local.conexion = object()  # una conexion persistente que ya estaba abierta

    assert c.sesion(HUELLA).snaps(_coordenadas(2)) == [Decimal(2), Decimal(2)]
    assert len(llamadas) == 2


def test_la_configuracion_declara_el_proveedor_o_no_hay_ruteo():
    class Sin:
        osrm_url = None

    assert proveedor_configurado(Sin()) is None
    with pytest.raises(ValueError):
        ConfiguracionOsrm("osrm:5000")
    with pytest.raises(ValueError):
        ConfiguracionOsrm("http://osrm:5000", lote_nearest=0)
