"""El lifecycle sintetico de un escenario: determinista por semilla, sin tocar los cortes ni los
pagos, coherente con lifecycle/v1, con cada tipo de evento que pide la mision y con eventos
tardios; y se carga entero, dos veces sin duplicar nada."""

from __future__ import annotations

import gzip
import hashlib
import json
from collections import Counter
from datetime import date, timedelta

import pandas as pd
import pytest
from typer.testing import CliRunner

from motor_cartera.cli import app
from motor_cartera.generador.lifecycle import escribir_lifecycle_del_escenario
from motor_cartera.generador.oficial import generar_escenario
from motor_cartera.lifecycle.importacion import LECTOR, REFERENCIA, _revisar

PRIMER_CORTE = date(2026, 6, 3)
PARAMETROS = {
    "cuentas": 600,
    "cortes": 4,
    "primer_corte": PRIMER_CORTE,
    "semilla": 11,
    "formato": "csv",
}


def _sha256(ruta) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def _lineas(ruta) -> list[dict]:
    with gzip.open(ruta, "rt", encoding="utf-8") as archivo:
        return [json.loads(linea) for linea in archivo]


@pytest.fixture(scope="module")
def con_lifecycle(tmp_path_factory):
    return generar_escenario(tmp_path_factory.mktemp("con"), lifecycle=True, **PARAMETROS)


def test_agregar_el_lifecycle_no_cambia_un_byte_de_los_cortes_ni_de_los_pagos(
    con_lifecycle, tmp_path
):
    sin = generar_escenario(tmp_path / "sin", **PARAMETROS)
    otra_vez = generar_escenario(tmp_path / "otra", lifecycle=True, **PARAMETROS)

    fuentes = [c["archivo"] for c in sin.manifiesto["cortes"]] + [
        p["archivo"] for p in sin.manifiesto["periodos"]
    ]
    for archivo in fuentes:
        assert _sha256(sin.destino / archivo) == _sha256(con_lifecycle.destino / archivo)
    assert "lifecycle" not in sin.manifiesto
    assert "lifecycle" not in sin.manifiesto["contratos"]
    # Determinista: la misma semilla, los mismos archivos del lifecycle.
    assert otra_vez.manifiesto["lifecycle"] == con_lifecycle.manifiesto["lifecycle"]
    for archivo in con_lifecycle.manifiesto["lifecycle"]:
        assert _sha256(con_lifecycle.destino / archivo["archivo"]) == archivo["sha256"]


def test_el_lifecycle_sin_las_fuentes_es_el_mismo(con_lifecycle, tmp_path):
    archivos = escribir_lifecycle_del_escenario(
        tmp_path,
        cuentas=PARAMETROS["cuentas"],
        cortes=PARAMETROS["cortes"],
        primer_corte=PRIMER_CORTE,
        semilla=PARAMETROS["semilla"],
    )

    assert [a.manifiesto() for a in archivos] == con_lifecycle.manifiesto["lifecycle"]
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(a.archivo for a in archivos)


def test_cada_linea_cumple_lifecycle_v1_y_sus_referencias_estan_en_su_archivo(con_lifecycle):
    manifiesto = con_lifecycle.manifiesto
    cortes = manifiesto["cortes"]
    llaves: set[str] = set()
    for numero, archivo in enumerate(manifiesto["lifecycle"]):
        clientes_del_corte = set(
            pd.read_csv(
                con_lifecycle.destino / cortes[numero]["archivo"],
                dtype="string",
                usecols=["CLIENTE_UNICO"],
            )["CLIENTE_UNICO"]
        )
        lineas = _lineas(con_lifecycle.destino / archivo["archivo"])
        assert len(lineas) == archivo["eventos"]
        assert Counter(linea["tipo"] for linea in lineas) == archivo["por_tipo"]
        del_archivo = set()
        fin = date.fromisoformat(archivo["hasta"]) + timedelta(1)
        for linea in lineas:
            _revisar(LECTOR.validate_python(linea))  # el contrato y la coherencia de la API
            assert linea["idempotency_key"] not in llaves
            llaves.add(linea["idempotency_key"])
            del_archivo.add(linea["idempotency_key"])
            campo = REFERENCIA[linea["tipo"]]
            if campo == "cliente_unico":
                # De una cuenta del corte que abre su periodo, o de uno anterior si llego tarde.
                assert linea[campo] in clientes_del_corte or archivo["tardios"]
            else:
                assert linea[campo] in del_archivo  # se refiere a algo antes en su archivo
            if "ocurrido_en" in linea:
                assert linea["ocurrido_en"].endswith("-06:00")
                assert linea["ocurrido_en"][:10] < fin.isoformat()


def test_trae_cada_clase_de_evento_que_pide_la_mision(con_lifecycle):
    lineas = [
        linea
        for archivo in con_lifecycle.manifiesto["lifecycle"]
        for linea in _lineas(con_lifecycle.destino / archivo["archivo"])
    ]
    gestiones = [g for g in lineas if g["tipo"] == "GESTION_REGISTRADA"]
    vistos = {
        "gestiones digitales": any(g["canal"] == "DIGITAL" for g in gestiones),
        "llamadas": any(g.get("medio") == "LLAMADA" for g in gestiones),
        "contactos fallidos": any(g["nivel_contacto"] == "SIN_CONTACTO" for g in gestiones),
        "contacto con titular": any(g["nivel_contacto"] == "CONTACTO_TITULAR" for g in gestiones),
        "contacto con tercero": any(g["nivel_contacto"] == "CONTACTO_TERCERO" for g in gestiones),
        "visitas": any("visita" in g for g in gestiones),
        "promesas": any(e["tipo"] == "PROMESA_CREADA" for e in lineas),
        "convenios con cuotas": any(e.get("cuotas") for e in lineas),
        "convenios sin calendario": any(
            e["tipo"] == "CONVENIO_CREADO" and "cuotas" not in e for e in lineas
        ),
        "cancelaciones": any(e["tipo"].endswith("_CANCELADA") for e in lineas),
        "anulaciones": any(e["tipo"] == "GESTION_ANULADA" for e in lineas),
        "tardios": sum(a["tardios"] for a in con_lifecycle.manifiesto["lifecycle"]) > 0,
    }
    assert vistos == dict.fromkeys(vistos, True)
    assert all(a["tardios"] == 0 for a in con_lifecycle.manifiesto["lifecycle"][:1])
    assert len(con_lifecycle.manifiesto["lifecycle"]) == PARAMETROS["cortes"] - 1


def test_un_escenario_que_no_termina_antes_de_hoy_no_trae_lifecycle(tmp_path):
    ultimo = PRIMER_CORTE + timedelta(days=7 * 3)

    with pytest.raises(ValueError, match="terminar antes de hoy"):
        generar_escenario(tmp_path, lifecycle=True, hoy=ultimo, **PARAMETROS)
    resultado = CliRunner().invoke(
        app, ["generar-escenario", "--destino", str(tmp_path / "hoy"), "--lifecycle"]
    )

    assert resultado.exit_code == 2
    assert "terminar antes de hoy" in resultado.output


def test_generar_escenario_con_lifecycle_desde_la_linea_de_comandos(tmp_path):
    resultado = CliRunner().invoke(
        app,
        [
            "generar-escenario",
            "--destino",
            str(tmp_path),
            "--cuentas",
            "300",
            "--cortes",
            "3",
            "--primer-corte",
            "2026-06-03",
            "--formato",
            "csv",
            "--lifecycle",
            "--intensidad-lifecycle",
            "0.5",
        ],
    )

    assert resultado.exit_code == 0, resultado.output
    assert "lifecycle del 2026-06-04 al 2026-06-10" in resultado.output
    manifiesto = json.loads((tmp_path / "escenario.json").read_text(encoding="utf-8"))
    assert manifiesto["contratos"]["lifecycle"] == "lifecycle/v1"
    assert manifiesto["intensidad_lifecycle"] == 0.5
    assert len(manifiesto["lifecycle"]) == 2


@pytest.mark.usefixtures("bd")
def test_el_escenario_con_lifecycle_se_carga_entero_y_dos_veces_no_duplica(tmp_path, trabajar):
    from motor_cartera.ingesta.corridas import ingerir_archivo
    from motor_cartera.ingesta.pagos import ingerir_pagos
    from motor_cartera.lifecycle.importacion import importar

    escenario = generar_escenario(
        tmp_path, lifecycle=True, **{**PARAMETROS, "cuentas": 200, "cortes": 3}
    )
    for corte in escenario.manifiesto["cortes"]:
        ingerir_archivo(
            escenario.destino / corte["archivo"],
            contrato="cartera/v2",
            fecha_corte=date.fromisoformat(corte["fecha_corte"]),
        )
    for periodo in escenario.manifiesto["periodos"]:
        ingerir_pagos(escenario.destino / periodo["archivo"])
    trabajar()  # las historias y las interpretaciones de los pagos

    def cargar():
        return [
            importar(
                escenario.destino / archivo["archivo"],
                despacho_id="DSP_001",
                cartera_id="CARTERA_PRINCIPAL",
                zona="America/Mexico_City",
            )
            for archivo in escenario.manifiesto["lifecycle"]
        ]

    primera, segunda = cargar(), cargar()

    for reporte, archivo in zip(primera, escenario.manifiesto["lifecycle"], strict=True):
        assert reporte.problemas == [] and reporte.confirmada
        assert reporte.registrados == archivo["por_tipo"]
    assert all(r.nuevos == 0 and r.ya_registradas == r.lineas for r in segunda)


def test_un_periodo_que_termina_antes_de_empezar_no_tiene_eventos():
    from motor_cartera.generador.lifecycle import grupos_del_periodo

    with pytest.raises(ValueError, match="termina antes de empezar"):
        grupos_del_periodo(None, None, semilla=1, desde=date(2026, 6, 10), hasta=date(2026, 6, 3))
