"""La API con cartera/v2: el contrato y la fecha de corte se declaran, y la evidencia de cada
corrida se consulta en GET /corridas/{run_id}/fuente."""

from __future__ import annotations

import hashlib
from datetime import date
from uuid import uuid4

import pytest

from motor_cartera.config import Config
from motor_cartera.contratos.cartera_v2 import CONTRATO_V2
from motor_cartera.generador.oficial import (
    contaminar,
    escribir_cartera,
    estado_inicial,
    generar_cartera_oficial,
)
from motor_cartera.generador.sintetico import generar_archivo
from motor_cartera.ingesta.lectores import REQUERIDAS
from motor_cartera.orquestacion.worker import identificador_worker, procesar_un_trabajo

pytestmark = pytest.mark.usefixtures("bd")

CORTE = date(2026, 9, 30)


def _subir(cliente, contenido: bytes, nombre: str, **campos):
    archivo = {"archivo": (nombre, contenido, "application/octet-stream")}
    return cliente.post("/corridas", files=archivo, data=campos)


def _ingerir() -> None:
    procesado = procesar_un_trabajo(identificador_worker(), Config())
    assert procesado is not None and procesado.tipo == "INGESTA", procesado


def _oficial(tmp_path, nombre="c.xlsx", n=120, semilla=1) -> bytes:
    estado = estado_inicial(n, semilla=semilla, fecha_corte=CORTE)
    ruta = escribir_cartera(estado, tmp_path / nombre, semilla=semilla, fecha_corte=CORTE).ruta
    return ruta.read_bytes()


def test_post_con_cartera_v2_registra_su_contrato_y_su_corte(cliente, tmp_path):
    respuesta = _subir(
        cliente, _oficial(tmp_path), "c.xlsx", contrato="cartera/v2", fecha_corte="2026-09-30"
    )

    assert respuesta.status_code == 201, respuesta.text
    corrida = respuesta.json()
    assert corrida["estado"] == "EN_PROCESO"
    assert (corrida["version_contrato"], corrida["fecha_corte"]) == ("cartera/v2", "2026-09-30")
    assert corrida["version_proyeccion"] == "operacional/v1"


def test_una_cartera_oficial_publicada_y_su_evidencia(cliente, tmp_path):
    contenido = _oficial(tmp_path, n=150, semilla=2)
    ubicacion = _subir(
        cliente, contenido, "c.xlsx", contrato="cartera/v2", fecha_corte="2026-09-30"
    ).headers["Location"]
    _ingerir()

    corrida = cliente.get(ubicacion).json()
    fuente = cliente.get(f"{ubicacion}/fuente")

    assert corrida["estado"] == "EXITOSA" and corrida["filas_validas"] == 150
    assert fuente.status_code == 200
    evidencia = fuente.json()
    assert (evidencia["version_contrato"], evidencia["fecha_corte"]) == ("cartera/v2", "2026-09-30")
    assert evidencia["artefacto"]["sha256"] == hashlib.sha256(contenido).hexdigest()
    assert evidencia["artefacto"]["sha256"] == corrida["firma"]
    assert (evidencia["artefacto"]["formato"], evidencia["artefacto"]["tamano_bytes"]) == (
        "xlsx",
        len(contenido),
    )
    assert evidencia["artefacto"]["nombre_original"] == "c.xlsx"
    conformado = evidencia["conformado"]
    assert (conformado["contrato"], conformado["filas"], conformado["columnas"]) == (
        "cartera/v2",
        150,
        93,
    )
    assert conformado["firma_contenido"] == corrida["firma_contenido"]
    assert conformado["artefacto"]["formato"] == "parquet"
    (hoja,) = evidencia["hojas_companeras"]
    assert (hoja["nombre"], hoja["columnas"], hoja["estructura_reconocida"]) == (
        "CARRIER",
        85,
        True,
    )
    # Nunca se dice donde vive un objeto en el almacen.
    assert "storage_key" not in str(evidencia) and "sha256/" not in str(evidencia)


def test_los_rechazos_de_cartera_v2_traen_sus_93_columnas_en_el_orden_del_contrato(
    cliente, tmp_path
):
    cartera = generar_cartera_oficial(100, semilla=3, fecha_corte=CORTE)
    sucia, _ = contaminar(cartera, 0.04, semilla=3)
    ubicacion = _subir(
        cliente,
        sucia.to_csv(index=False).encode(),
        "c.csv",
        contrato="cartera/v2",
        fecha_corte="2026-09-30",
    ).headers["Location"]
    _ingerir()

    pagina = cliente.get(f"{ubicacion}/rechazos").json()

    assert pagina["estado"] == "EXITOSA" and pagina["total"] == 4
    for rechazo in pagina["elementos"]:
        assert list(rechazo["valores"]) == list(CONTRATO_V2.nombres)


@pytest.mark.parametrize(
    ("campos", "codigo"),
    [
        ({"contrato": "cartera/v2"}, "FECHA_CORTE_REQUERIDA"),
        ({"contrato": "cartera/v1", "fecha_corte": "2026-09-30"}, "FECHA_CORTE_NO_APLICA"),
        ({"fecha_corte": "2026-09-30"}, "FECHA_CORTE_NO_APLICA"),
    ],
    ids=["v2-sin-corte", "v1-con-corte", "por-omision-con-corte"],
)
def test_una_declaracion_invalida_422_sin_guardar_nada(cliente, tmp_path, objetos, campos, codigo):
    contenido = _oficial(tmp_path, "c.csv", n=10, semilla=uuid4().int % 2**31)
    antes = objetos()

    respuesta = _subir(cliente, contenido, "c.csv", **campos)

    assert respuesta.status_code == 422
    assert respuesta.json()["codigo"] == codigo
    assert objetos() == antes


@pytest.mark.parametrize(
    ("campos", "campo", "problema"),
    [
        (
            {"contrato": "cartera/v9", "fecha_corte": "2026-09-30"},
            "body.contrato",
            "Debe ser uno de: 'cartera/v1' o 'cartera/v2'.",
        ),
        (
            {"contrato": "cartera/v2", "fecha_corte": "30/09/2026"},
            "body.fecha_corte",
            "Debe ser una fecha AAAA-MM-DD.",
        ),
    ],
    ids=["contrato", "fecha"],
)
def test_un_contrato_o_una_fecha_que_no_existen_422(cliente, campos, campo, problema):
    respuesta = _subir(cliente, b"CLIENTE_UNICO\nCU00000001\n", "c.csv", **campos)

    assert respuesta.status_code == 422
    assert respuesta.json()["codigo"] == "ENTRADA_INVALIDA"
    assert respuesta.json()["detalles"] == [{"campo": campo, "problema": problema}]


def test_sin_contrato_es_cartera_v1_como_siempre(cliente, tmp_path):
    ruta = generar_archivo(tmp_path / "c.csv", n=30, semilla=4, fecha_corte=CORTE)
    ubicacion = _subir(cliente, ruta.read_bytes(), "c.csv").headers["Location"]
    _ingerir()

    corrida = cliente.get(ubicacion).json()
    evidencia = cliente.get(f"{ubicacion}/fuente").json()
    rechazos = cliente.get(f"{ubicacion}/rechazos").json()

    assert (corrida["estado"], corrida["version_contrato"]) == ("EXITOSA", "cartera/v1")
    assert corrida["version_proyeccion"] is None
    assert evidencia["artefacto"]["formato"] == "csv"
    assert (evidencia["conformado"], evidencia["hojas_companeras"]) == (None, [])
    assert rechazos["total"] == 0
    assert list(REQUERIDAS)  # cartera/v1 sigue con sus 8 columnas


def test_la_fuente_de_una_corrida_que_no_existe_404(cliente):
    respuesta = cliente.get(f"/corridas/{uuid4()}/fuente")

    assert respuesta.status_code == 404
    assert respuesta.json()["codigo"] == "CORRIDA_NO_ENCONTRADA"


def test_openapi_documenta_el_contrato_declarado_y_la_evidencia(app):
    rutas = app.openapi()["paths"]

    post = rutas["/corridas"]["post"]
    descripcion_422 = post["responses"]["422"]["description"]
    assert "`FECHA_CORTE_REQUERIDA`" in descripcion_422
    assert "`FECHA_CORTE_NO_APLICA`" in descripcion_422
    esquema = post["requestBody"]["content"]["multipart/form-data"]["schema"]["$ref"]
    cuerpo = app.openapi()["components"]["schemas"][esquema.rsplit("/", 1)[1]]
    assert set(cuerpo["properties"]) == {"archivo", "contrato", "fecha_corte"}
    assert cuerpo["properties"]["contrato"]["default"] == "cartera/v1"
    assert set(rutas["/corridas/{run_id}/fuente"]["get"]["responses"]) == {
        "200",
        "401",
        "404",
        "422",
    }
