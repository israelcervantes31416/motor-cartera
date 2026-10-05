"""La API de pagos: POST /pagos registra la ingesta y su trabajo, el worker la juzga, y la ingesta,
sus rechazos y su evidencia se consultan con su pagos_run_id."""

from __future__ import annotations

import hashlib
from datetime import date, timedelta
from uuid import uuid4

import pandas as pd
import pyarrow as pa
import pytest
from sqlmodel import func, select

from motor_cartera.config import Config
from motor_cartera.contratos.pagos import CONTRATO_PAGOS
from motor_cartera.db.modelos import ArtefactoFuente, IngestaPagos, TrabajoOrquestacion
from motor_cartera.db.sesion import sesion
from motor_cartera.generador.oficial import escribir_pagos, estado_inicial, tabla_pagos
from motor_cartera.orquestacion.worker import identificador_worker, procesar_un_trabajo

pytestmark = pytest.mark.usefixtures("bd")

CORTE = date(2026, 9, 30)
RECUPERADO = "Recuperación_por_Gestión"


def _movimientos(n: int, semilla: int) -> pd.DataFrame:
    estado = estado_inicial(n, semilla=semilla, fecha_corte=CORTE)
    tabla = tabla_pagos(estado, semilla=semilla, desde=CORTE - timedelta(days=6), hasta=CORTE)
    return tabla.to_pandas(types_mapper={pa.string(): pd.StringDtype()}.get)


def _archivo(tmp_path, datos: pd.DataFrame, nombre: str = "pagos.csv") -> bytes:
    return escribir_pagos(datos, tmp_path / nombre).ruta.read_bytes()


def _subir(cliente, contenido: bytes, nombre: str = "pagos.csv"):
    return cliente.post(
        "/pagos", files={"archivo": (nombre, contenido, "application/octet-stream")}
    )


def _ingerir() -> None:
    procesado = procesar_un_trabajo(identificador_worker(), Config())
    assert procesado is not None and procesado.tipo == "INGESTA_PAGOS", procesado


def _filas(modelo) -> int:
    with sesion() as s:
        return s.exec(select(func.count()).select_from(modelo)).one()


def test_post_pagos_registra_la_ingesta_en_proceso_con_su_trabajo(cliente, tmp_path):
    contenido = _archivo(tmp_path, _movimientos(60, semilla=1))

    respuesta = _subir(cliente, contenido)

    assert respuesta.status_code == 201, respuesta.text
    ingesta = respuesta.json()
    assert respuesta.headers["Location"] == f"/pagos/{ingesta['pagos_run_id']}"
    assert ingesta["estado"] == "EN_PROCESO"
    assert ingesta["firma"] == hashlib.sha256(contenido).hexdigest()
    assert (ingesta["version_contrato"], ingesta["tolerancia_rechazo"]) == ("pagos/v1", 0.0)
    assert (ingesta["despacho_id"], ingesta["cartera_id"]) == ("DSP_001", "CARTERA_PRINCIPAL")
    assert (ingesta["filas_leidas"], ingesta["firma_contenido"]) == (0, None)
    assert ingesta["duracion_segundos"] is None and ingesta["trabajo_id"] is not None
    # Su trabajo, en la cola, apunta a esta ingesta por su pagos_run_id; no es de ningun flujo.
    trabajo = cliente.get(f"/trabajos/{ingesta['trabajo_id']}").json()
    assert (trabajo["tipo"], trabajo["estado"], trabajo["flujo_id"]) == (
        "INGESTA_PAGOS",
        "PENDIENTE",
        None,
    )
    assert trabajo["objetivo_run_id"] == ingesta["pagos_run_id"]


def test_unos_pagos_aceptados_y_su_evidencia(cliente, tmp_path):
    movimientos = _movimientos(120, semilla=2)
    contenido = _archivo(tmp_path, movimientos, "pagos.zip")
    ubicacion = _subir(cliente, contenido, "pagos.zip").headers["Location"]
    _ingerir()

    ingesta = cliente.get(ubicacion).json()
    fuente = cliente.get(f"{ubicacion}/fuente")
    rechazos = cliente.get(f"{ubicacion}/rechazos").json()

    n = len(movimientos)
    assert ingesta["estado"] == "EXITOSA", ingesta["detalle"]
    assert (ingesta["filas_leidas"], ingesta["filas_validas"], ingesta["filas_rechazadas"]) == (
        n,
        n,
        0,
    )
    assert ingesta["duracion_segundos"] >= 0 and len(ingesta["firma_contenido"]) == 64
    assert "'PAGOS.csv', dentro de 'pagos.zip'" in ingesta["detalle"]
    assert (rechazos["estado"], rechazos["total"], rechazos["elementos"]) == ("EXITOSA", 0, [])
    assert fuente.status_code == 200
    evidencia = fuente.json()
    assert evidencia["version_contrato"] == "pagos/v1"
    assert evidencia["artefacto"]["sha256"] == ingesta["firma"]
    assert (evidencia["artefacto"]["formato"], evidencia["artefacto"]["tamano_bytes"]) == (
        "zip",
        len(contenido),
    )
    assert evidencia["artefacto"]["nombre_original"] == "pagos.zip"
    conformado = evidencia["conformado"]
    assert (conformado["contrato"], conformado["filas"], conformado["columnas"]) == (
        "pagos/v1",
        n,
        23,
    )
    assert conformado["firma_contenido"] == ingesta["firma_contenido"]
    assert conformado["artefacto"]["formato"] == "parquet"
    # Nunca se dice donde vive un objeto en el almacen.
    assert "storage_key" not in str(evidencia) and "sha256/" not in str(evidencia)


def test_el_mismo_archivo_409_mientras_se_procesa_y_despues_de_aceptarse(cliente, tmp_path):
    contenido = _archivo(tmp_path, _movimientos(50, semilla=3))
    primera = _subir(cliente, contenido).json()

    en_proceso = _subir(cliente, contenido)
    _ingerir()
    aceptado = _subir(cliente, contenido)

    for respuesta, codigo in ((en_proceso, "PAGOS_EN_PROCESO"), (aceptado, "PAGOS_YA_ACEPTADOS")):
        assert respuesta.status_code == 409
        assert respuesta.json()["codigo"] == codigo
        assert respuesta.json()["pagos_run_id"] == primera["pagos_run_id"]
        assert respuesta.json()["run_id"] is None
    assert _filas(IngestaPagos) == 1 and _filas(ArtefactoFuente) == 2  # el original y su Parquet


def test_los_rechazos_traen_su_fila_sus_23_valores_y_su_motivo(cliente, tmp_path):
    movimientos = _movimientos(90, semilla=4).reset_index(drop=True)
    movimientos.loc[[4, 2], RECUPERADO] = ["12,50", "doce"]
    ubicacion = _subir(cliente, _archivo(tmp_path, movimientos)).headers["Location"]
    _ingerir()

    ingesta = cliente.get(ubicacion).json()
    pagina = cliente.get(f"{ubicacion}/rechazos", params={"por_pagina": 1, "pagina": 2}).json()
    todos = cliente.get(f"{ubicacion}/rechazos").json()

    # Con la tolerancia por omision (0), dos movimientos invalidos rechazan el archivo entero.
    assert ingesta["estado"] == "RECHAZADA" and ingesta["filas_rechazadas"] == 2
    assert [r["fila"] for r in todos["elementos"]] == [4, 6]
    assert (pagina["total"], [r["fila"] for r in pagina["elementos"]]) == (2, [6])
    for rechazo in todos["elementos"]:
        assert list(rechazo["valores"]) == list(CONTRATO_PAGOS.nombres)
        assert rechazo["motivos"] == [{"campo": RECUPERADO, "regla": "importe"}]
    assert todos["elementos"][0]["valores"][RECUPERADO] == "doce"
    assert cliente.get(f"{ubicacion}/fuente").json()["conformado"] is None


def test_los_rechazos_de_una_ingesta_en_proceso_409(cliente, tmp_path):
    ubicacion = _subir(cliente, _archivo(tmp_path, _movimientos(40, semilla=5))).headers["Location"]

    respuesta = cliente.get(f"{ubicacion}/rechazos")

    assert respuesta.status_code == 409
    assert respuesta.json()["codigo"] == "PAGOS_EN_PROCESO"


@pytest.mark.parametrize("sufijo", ["", "/rechazos", "/fuente"])
def test_una_ingesta_de_pagos_que_no_existe_404(cliente, sufijo):
    pagos_run_id = uuid4()

    respuesta = cliente.get(f"/pagos/{pagos_run_id}{sufijo}")

    assert respuesta.status_code == 404
    assert respuesta.json()["codigo"] == "PAGOS_NO_ENCONTRADOS"
    assert respuesta.json()["pagos_run_id"] == str(pagos_run_id)


def test_un_pagos_run_id_que_no_es_uuid_422(cliente):
    respuesta = cliente.get("/pagos/no-soy-un-uuid")

    assert respuesta.status_code == 422
    assert respuesta.json()["codigo"] == "ENTRADA_INVALIDA"


@pytest.mark.parametrize(
    ("contenido", "nombre", "estado", "codigo"),
    [
        (b"%PDF-1.7", "pagos.pdf", 415, "FORMATO_NO_SOPORTADO"),
        (b"%PDF-1.7 renombrado", "pagos.csv", 415, "FORMATO_NO_CORRESPONDE"),
        (b"no soy un excel", "pagos.xlsx", 415, "FORMATO_NO_CORRESPONDE"),
        (b"x" * (1024 * 1024 + 1), "pagos.csv", 413, "ARCHIVO_DEMASIADO_GRANDE"),
        (b"", "pagos.csv", 422, "ARCHIVO_VACIO"),
    ],
    ids=["pdf", "pdf-como-csv", "xlsx-falso", "demasiado-grande", "vacio"],
)
def test_lo_que_no_es_un_archivo_de_pagos_aceptable_no_se_guarda(
    cliente, objetos, contenido, nombre, estado, codigo
):
    antes = objetos()

    respuesta = _subir(cliente, contenido, nombre)

    assert respuesta.status_code == estado
    assert respuesta.json()["codigo"] == codigo
    assert objetos() == antes
    assert _filas(IngestaPagos) == _filas(ArtefactoFuente) == _filas(TrabajoOrquestacion) == 0


def test_sin_archivo_422(cliente):
    respuesta = cliente.post("/pagos", data={"otro": "campo"})

    assert respuesta.status_code == 422
    assert respuesta.json()["codigo"] == "ENTRADA_INVALIDA"


def test_sin_api_key_401(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as anonimo:
        respuesta = anonimo.post("/pagos", files={"archivo": ("p.csv", b"a\n1\n", "text/csv")})

    assert respuesta.status_code == 401
    assert respuesta.json()["codigo"] == "API_KEY_AUSENTE"


def test_openapi_documenta_la_ingesta_de_pagos(app):
    esquema = app.openapi()
    rutas = esquema["paths"]

    post = rutas["/pagos"]["post"]
    assert set(post["responses"]) == {"201", "401", "409", "413", "415", "422"}
    assert "`PAGOS_YA_ACEPTADOS`" in post["responses"]["409"]["description"]
    assert "`PAGOS_EN_PROCESO`" in post["responses"]["409"]["description"]
    assert set(rutas["/pagos/{pagos_run_id}"]["get"]["responses"]) == {"200", "401", "404", "422"}
    assert set(rutas["/pagos/{pagos_run_id}/rechazos"]["get"]["responses"]) == {
        "200",
        "401",
        "404",
        "409",
        "422",
    }
    assert "/pagos/{pagos_run_id}/fuente" in rutas
    assert "pagos" in {etiqueta["name"] for etiqueta in esquema["tags"]}
    propiedades = esquema["components"]["schemas"]["IngestaPagosRespuesta"]["properties"]
    assert {"pagos_run_id", "trabajo_id", "firma_contenido", "duracion_segundos"} <= set(
        propiedades
    )
