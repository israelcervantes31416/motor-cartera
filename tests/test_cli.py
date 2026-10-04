from __future__ import annotations

import re
from datetime import timedelta

import pytest
from sqlalchemy import func, update
from sqlmodel import select
from typer.testing import CliRunner

from motor_cartera.cli import app
from motor_cartera.config import Config
from motor_cartera.db.modelos import (
    ArchivoCorrida,
    Corrida,
    EjecucionDecision,
    EstadoCorrida,
    EstadoTrabajo,
    FlujoOrquestacion,
    TipoTrabajo,
    TrabajoOrquestacion,
)
from motor_cartera.db.sesion import sesion
from motor_cartera.ingesta.lectores import leer
from motor_cartera.orquestacion import worker
from motor_cartera.orquestacion.flujo import crear_flujo_ingesta
from motor_cartera.orquestacion.worker import ingerir_en_primer_plano

cli = CliRunner()


def _generar(destino, *extra: str):
    argumentos = ["generar", "--n", "50", "--destino", str(destino), "--fecha-corte", "2026-09-30"]
    return cli.invoke(app, [*argumentos, *extra])


def _trabajos() -> list[TrabajoOrquestacion]:
    with sesion() as s:
        return list(s.exec(select(TrabajoOrquestacion).order_by(TrabajoOrquestacion.id)).all())


def _cuantos(modelo) -> int:
    with sesion() as s:
        return s.exec(select(func.count()).select_from(modelo)).one()


class Muerte(BaseException):  # noqa: N818
    """La muerte del proceso a media ingesta: ningun `except Exception` la atrapa."""


def test_generar_escribe_una_cartera_legible(tmp_path):
    destino = tmp_path / "cartera.csv"

    resultado = _generar(destino)

    assert resultado.exit_code == 0, resultado.output
    assert len(leer(destino).datos) == 50


@pytest.mark.usefixtures("bd")
def test_cargar_publica_y_dice_como_quedo(tmp_path):
    destino = tmp_path / "cartera.csv"
    _generar(destino, "--tasa-invalidas", "0")

    resultado = cli.invoke(app, ["cargar", str(destino)])

    assert resultado.exit_code == 0, resultado.output
    assert "EXITOSA" in resultado.output
    assert "leidas 50, validas 50, rechazadas 0" in resultado.output


@pytest.mark.usefixtures("bd")
def test_cargar_pasa_por_la_cola_y_no_encadena_nada(tmp_path):
    # La ingesta del CLI tiene su trabajo, como la de la API, pero sin flujo: no decide nada.
    destino = tmp_path / "cartera.csv"
    _generar(destino, "--tasa-invalidas", "0")

    resultado = cli.invoke(app, ["cargar", str(destino)])

    assert resultado.exit_code == 0, resultado.output
    (trabajo,) = _trabajos()
    assert (trabajo.tipo, trabajo.estado, trabajo.intentos, trabajo.flujo_id) == (
        TipoTrabajo.INGESTA,
        EstadoTrabajo.COMPLETADO,
        1,
        None,
    )
    assert (trabajo.worker_id, trabajo.lease_hasta) == (None, None)
    assert [_cuantos(m) for m in (ArchivoCorrida, FlujoOrquestacion, EjecucionDecision)] == [
        0,
        0,
        0,
    ]


@pytest.mark.usefixtures("bd")
def test_cargar_termina_con_error_si_no_publica(tmp_path):
    destino = tmp_path / "cartera.csv"
    destino.write_text("esto,no,es\nuna,cartera,valida\n", encoding="utf-8")

    resultado = cli.invoke(app, ["cargar", str(destino)])

    assert resultado.exit_code == 1
    assert "FALLIDA" in resultado.output


@pytest.mark.usefixtures("bd")
def test_cargar_dos_veces_el_mismo_archivo_se_niega(tmp_path):
    destino = tmp_path / "cartera.csv"
    _generar(destino, "--tasa-invalidas", "0")
    cli.invoke(app, ["cargar", str(destino)])

    resultado = cli.invoke(app, ["cargar", str(destino)])

    assert resultado.exit_code == 1
    assert "ya lo publico la corrida" in resultado.output


@pytest.mark.usefixtures("bd")
def test_cargar_un_archivo_vacio_se_niega_sin_registrar_nada(tmp_path):
    destino = tmp_path / "vacio.csv"
    destino.write_bytes(b"")

    resultado = cli.invoke(app, ["cargar", str(destino)])

    assert resultado.exit_code == 1
    assert "'vacio.csv' esta vacio: no hay nada que ingerir." in resultado.output
    assert _cuantos(Corrida) == _cuantos(TrabajoOrquestacion) == 0


@pytest.mark.usefixtures("bd")
def test_si_cargar_muere_a_la_mitad_un_worker_termina_la_ingesta(tmp_path, monkeypatch, trabajar):
    destino = tmp_path / "cartera.csv"
    _generar(destino, "--tasa-invalidas", "0")
    procesar = worker.procesar_corrida

    def muere(corrida_id, contenido):
        raise Muerte()

    monkeypatch.setattr(worker, "procesar_corrida", muere)
    with pytest.raises(Muerte):
        ingerir_en_primer_plano(destino)
    (trabajo,) = _trabajos()
    assert trabajo.estado == EstadoTrabajo.EJECUTANDO
    with sesion() as s:
        assert s.get_one(Corrida, trabajo.corrida_id).estado == EstadoCorrida.EN_PROCESO
    assert _cuantos(ArchivoCorrida) == 1  # el archivo sobrevive al proceso que lo leyo

    monkeypatch.setattr(worker, "procesar_corrida", procesar)
    with sesion() as s:
        s.execute(update(TrabajoOrquestacion).values(lease_hasta=func.now() - timedelta(seconds=1)))
        s.commit()
    (procesado,) = trabajar()

    assert (procesado.intentos, procesado.estado) == (2, EstadoTrabajo.COMPLETADO)
    with sesion() as s:
        assert s.get_one(Corrida, trabajo.corrida_id).estado == EstadoCorrida.EXITOSA
    assert _cuantos(ArchivoCorrida) == 0


@pytest.mark.usefixtures("bd")
def test_worker_una_vez_con_la_cola_vacia_sale_sin_error():
    resultado = cli.invoke(app, ["worker", "--una-vez"])

    assert resultado.exit_code == 0, resultado.output
    assert resultado.output == "No hay trabajos que tomar.\n"


@pytest.mark.usefixtures("bd")
def test_worker_una_vez_procesa_un_solo_trabajo(tmp_path):
    destino = tmp_path / "cartera.csv"
    _generar(destino, "--tasa-invalidas", "0")
    with sesion() as s:
        crear_flujo_ingesta(
            s,
            origen=destino.name,
            contenido=destino.read_bytes(),
            tolerancia=0.05,
            config=Config(),
        )

    resultado = cli.invoke(app, ["worker", "--una-vez"])

    assert resultado.exit_code == 0, resultado.output
    ingesta, decision = _trabajos()
    assert resultado.output == f"Trabajo {ingesta.trabajo_id} (INGESTA, intento 1): COMPLETADO\n"
    assert (ingesta.estado, decision.estado) == (EstadoTrabajo.COMPLETADO, EstadoTrabajo.PENDIENTE)


def test_el_worker_continuo_corre_hasta_que_lo_detienen(monkeypatch):
    # Sin --una-vez, el comando entrega el control al ciclo del worker y no imprime nada al salir.
    llamadas = []
    monkeypatch.setattr(
        worker, "ejecutar_worker", lambda **opciones: llamadas.append(opciones) or None
    )

    resultado = cli.invoke(app, ["worker"])

    assert (resultado.exit_code, resultado.output) == (0, "")
    assert llamadas == [{"una_vez": False}]


def test_el_worker_se_anuncia_en_la_ayuda():
    resultado = cli.invoke(app, ["worker", "--help"])

    # Con una terminal que acepta color, como la del CI, la ayuda trae secuencias ANSI entre las
    # letras de cada opcion: se quitan antes de buscarla.
    ayuda = re.sub(r"\x1b\[[0-9;]*m", "", resultado.output)
    assert resultado.exit_code == 0
    assert "--una-vez" in ayuda
