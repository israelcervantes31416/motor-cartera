"""POST /corridas, GET /corridas/{run_id} y GET /corridas/{run_id}/rechazos."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from sqlmodel import func, select

from motor_cartera.db.modelos import Corrida
from motor_cartera.db.sesion import sesion
from motor_cartera.generador.sintetico import generar_archivo
from motor_cartera.ingesta.corridas import abrir_corrida
from motor_cartera.ingesta.lectores import REQUERIDAS

pytestmark = pytest.mark.usefixtures("bd")

CORTE = date(2026, 9, 30)


def _subir(cliente, contenido: bytes, nombre: str = "cartera.csv", **kwargs):
    archivo = {"archivo": (nombre, contenido, "application/octet-stream")}
    return cliente.post("/corridas", files=archivo, **kwargs)


def _cartera(tmp_path, n=100, tasa=0.03, semilla=1, nombre="cartera.csv") -> bytes:
    ruta = generar_archivo(
        tmp_path / nombre, n=n, tasa_invalidas=tasa, semilla=semilla, fecha_corte=CORTE
    )
    return ruta.read_bytes()


def _corridas_registradas() -> int:
    with sesion() as s:
        return s.exec(select(func.count()).select_from(Corrida)).one()


# --- POST /corridas: el camino feliz --------------------------------------------------------


def test_post_crea_la_corrida_201_con_location_y_estado_inicial(cliente, tmp_path):
    respuesta = _subir(cliente, _cartera(tmp_path))

    assert respuesta.status_code == 201
    corrida = respuesta.json()
    assert respuesta.headers["Location"] == f"/corridas/{corrida['run_id']}"
    assert corrida["estado"] == "EN_PROCESO"
    assert corrida["terminada_en"] is None
    assert corrida["duracion_segundos"] is None
    assert corrida["tolerancia_rechazo"] == 0.05


def test_get_da_conteos_tiempos_y_resultado(cliente, tmp_path):
    ubicacion = _subir(cliente, _cartera(tmp_path)).headers["Location"]

    respuesta = cliente.get(ubicacion)

    assert respuesta.status_code == 200
    corrida = respuesta.json()
    assert corrida["estado"] == "EXITOSA"
    assert (corrida["filas_leidas"], corrida["filas_validas"], corrida["filas_rechazadas"]) == (
        100,
        97,
        3,
    )
    assert corrida["fecha_corte"] == "2026-09-30"
    assert corrida["duracion_segundos"] >= 0
    assert corrida["detalle"].startswith("Se publicaron 97 cuentas")
    assert not corrida["detalle"].endswith("..")
    # En UTC, se configure como se configure el servidor de PostgreSQL.
    assert corrida["iniciada_en"].endswith("Z") and corrida["terminada_en"].endswith("Z")


def test_un_archivo_malformado_si_crea_la_corrida_y_esta_termina_fallida(cliente):
    # La peticion es valida: lo que no sirve es el contenido, y eso lo juzga la corrida.
    respuesta = _subir(cliente, b"cliente,saldo\nCU00000001,10\n")

    assert respuesta.status_code == 201
    corrida = cliente.get(respuesta.headers["Location"]).json()
    assert corrida["estado"] == "FALLIDA"
    assert "Faltan columnas requeridas" in corrida["detalle"]


def test_el_nombre_del_archivo_se_queda_sin_ruta(cliente, tmp_path):
    respuesta = _subir(cliente, _cartera(tmp_path), nombre="C:\\fakepath\\cartera.csv")

    assert respuesta.json()["origen"] == "cartera.csv"


# --- POST /corridas: cada error, a proposito ------------------------------------------------


def test_sin_api_key_401_y_no_registra_nada(cliente, tmp_path):
    respuesta = _subir(cliente, _cartera(tmp_path), headers={"X-API-Key": ""})

    assert respuesta.status_code == 401
    assert _corridas_registradas() == 0


def test_el_mismo_archivo_otra_vez_409_con_la_corrida_que_ya_lo_publico(cliente, tmp_path):
    contenido = _cartera(tmp_path)
    primera = _subir(cliente, contenido).json()["run_id"]

    respuesta = _subir(cliente, contenido)

    assert respuesta.status_code == 409
    assert respuesta.json()["codigo"] == "ARCHIVO_YA_PUBLICADO"
    assert respuesta.json()["run_id"] == primera
    assert _corridas_registradas() == 1


def test_formato_no_soportado_415(cliente):
    respuesta = _subir(cliente, b"%PDF-1.7", nombre="cartera.pdf")

    assert respuesta.status_code == 415
    assert respuesta.json()["codigo"] == "FORMATO_NO_SOPORTADO"


def test_archivo_demasiado_grande_413(cliente):
    # La app de prueba tiene un tope de 1 MiB.
    respuesta = _subir(cliente, b"x" * (1024 * 1024 + 1))

    assert respuesta.status_code == 413
    assert respuesta.json()["codigo"] == "ARCHIVO_DEMASIADO_GRANDE"


def test_archivo_vacio_422(cliente):
    respuesta = _subir(cliente, b"")

    assert respuesta.status_code == 422
    assert respuesta.json()["codigo"] == "ARCHIVO_VACIO"


def test_sin_archivo_422_dice_que_falta(cliente):
    respuesta = cliente.post("/corridas")

    assert respuesta.status_code == 422
    assert respuesta.json()["detalles"] == [
        {"campo": "body.archivo", "problema": "Falta este campo."}
    ]


# --- GET /corridas/{run_id} -----------------------------------------------------------------


def test_una_corrida_que_no_existe_404(cliente):
    run_id = uuid4()

    respuesta = cliente.get(f"/corridas/{run_id}")

    assert respuesta.status_code == 404
    assert respuesta.json()["codigo"] == "CORRIDA_NO_ENCONTRADA"
    assert respuesta.json()["run_id"] == str(run_id)


def test_un_run_id_que_no_es_uuid_422(cliente):
    respuesta = cliente.get("/corridas/123")

    assert respuesta.status_code == 422
    assert respuesta.json()["detalles"][0]["campo"] == "path.run_id"


# --- GET /corridas/{run_id}/rechazos --------------------------------------------------------


def test_cada_rechazo_trae_su_fila_lo_que_traia_y_el_motivo(cliente, tmp_path):
    run_id = _subir(cliente, _cartera(tmp_path)).json()["run_id"]

    respuesta = cliente.get(f"/corridas/{run_id}/rechazos")

    assert respuesta.status_code == 200
    pagina = respuesta.json()
    assert (pagina["run_id"], pagina["estado"], pagina["total"]) == (run_id, "EXITOSA", 3)
    filas = [r["fila"] for r in pagina["elementos"]]
    assert filas == sorted(filas)
    for rechazo in pagina["elementos"]:
        assert list(rechazo["valores"]) == list(REQUERIDAS)  # en el orden del contrato
        assert rechazo["motivos"] and {"campo", "regla"} == set(rechazo["motivos"][0])


def test_los_rechazos_se_paginan_sin_huecos_ni_repetidos(cliente, tmp_path):
    run_id = _subir(cliente, _cartera(tmp_path, n=200, tasa=0.1)).json()["run_id"]

    paginas = [
        cliente.get(f"/corridas/{run_id}/rechazos", params={"pagina": p, "por_pagina": 8}).json()
        for p in (1, 2, 3, 4)
    ]

    assert [len(p["elementos"]) for p in paginas] == [8, 8, 4, 0]
    assert {p["total"] for p in paginas} == {20}
    filas = [r["fila"] for p in paginas for r in p["elementos"]]
    assert filas == sorted(set(filas)) and len(filas) == 20


def test_una_corrida_rechazada_muestra_por_que_no_publico(cliente, tmp_path):
    run_id = _subir(cliente, _cartera(tmp_path, n=100, tasa=0.2)).json()["run_id"]

    pagina = cliente.get(f"/corridas/{run_id}/rechazos").json()

    assert pagina["estado"] == "RECHAZADA"
    assert pagina["total"] == 20


def test_los_rechazos_de_una_corrida_en_proceso_409(cliente, tmp_path):
    with sesion() as s:
        corrida = abrir_corrida(s, origen="cartera.csv", contenido=_cartera(tmp_path))

    respuesta = cliente.get(f"/corridas/{corrida.run_id}/rechazos")

    assert respuesta.status_code == 409
    assert respuesta.json()["codigo"] == "CORRIDA_EN_PROCESO"


def test_los_rechazos_de_una_corrida_que_no_existe_404(cliente):
    assert cliente.get(f"/corridas/{uuid4()}/rechazos").status_code == 404


@pytest.mark.parametrize(
    ("parametros", "campo"),
    [({"por_pagina": 501}, "query.por_pagina"), ({"pagina": 0}, "query.pagina")],
)
def test_paginacion_fuera_de_rango_422(cliente, tmp_path, parametros, campo):
    run_id = _subir(cliente, _cartera(tmp_path)).json()["run_id"]

    respuesta = cliente.get(f"/corridas/{run_id}/rechazos", params=parametros)

    assert respuesta.status_code == 422
    assert respuesta.json()["detalles"][0]["campo"] == campo


# --- la documentacion dice lo que la ruta hace ----------------------------------------------


def test_openapi_documenta_cada_respuesta_con_el_esquema_real(app):
    rutas = app.openapi()["paths"]

    post = rutas["/corridas"]["post"]
    assert set(post["responses"]) == {"201", "401", "409", "413", "415", "422"}
    for codigo in ("401", "409", "413", "415", "422"):
        esquema = post["responses"][codigo]["content"]["application/json"]["schema"]
        assert esquema["$ref"].endswith("/ErrorRespuesta")
    assert set(rutas["/corridas/{run_id}/rechazos"]["get"]["responses"]) == {
        "200",
        "401",
        "404",
        "409",
        "422",
    }
    for ruta in rutas.values():
        for operacion in ruta.values():
            assert operacion["summary"] and operacion["description"]
