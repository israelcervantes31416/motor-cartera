"""El artefacto fuente en la base, contra PostgreSQL: una fila por contenido, la corrida que apunta
a el, y la evidencia que sobrevive a la ingesta y a que se reinicien la API y el worker."""

from __future__ import annotations

import hashlib
import io
from datetime import date

import pytest
from sqlmodel import func, select
from typer.testing import CliRunner

from motor_cartera.cli import app as cli_app
from motor_cartera.config import Config
from motor_cartera.db.modelos import ArtefactoFuente, Corrida, EstadoCorrida
from motor_cartera.db.sesion import sesion
from motor_cartera.fuentes.artefactos import (
    almacen_de,
    auditar_artefactos,
    guardar_contenido,
    registrar_artefacto,
)
from motor_cartera.generador.sintetico import generar_archivo
from motor_cartera.ingesta.corridas import ingerir_archivo

pytestmark = pytest.mark.usefixtures("bd")

CORTE = date(2026, 9, 30)
cli = CliRunner()


def _artefactos() -> list[ArtefactoFuente]:
    with sesion() as s:
        return list(s.exec(select(ArtefactoFuente).order_by(ArtefactoFuente.id)).all())


def test_el_mismo_contenido_se_registra_una_sola_vez_con_el_nombre_de_la_primera(almacen):
    contenido = b"cliente,saldo\nCU0000000001,10.00\n"
    primero = guardar_contenido(almacen, contenido, "enero.csv")
    segundo = guardar_contenido(almacen, contenido, "copia de enero.csv")

    with sesion() as s:
        uno = registrar_artefacto(s, primero).id
        otro = registrar_artefacto(s, segundo).id
        s.commit()

    assert uno == otro
    (artefacto,) = _artefactos()
    assert (artefacto.nombre_original, artefacto.formato, artefacto.media_type) == (
        "enero.csv",
        "csv",
        "text/csv",
    )
    assert artefacto.sha256 == hashlib.sha256(contenido).hexdigest()
    assert artefacto.storage_key == f"sha256/{artefacto.sha256[:2]}/{artefacto.sha256}"


def test_la_evidencia_sobrevive_a_la_ingesta_y_a_reiniciar_la_api_y_el_worker(tmp_path, almacen):
    ruta = generar_archivo(tmp_path / "cartera.xlsx", n=60, semilla=4, fecha_corte=CORTE)
    contenido = ruta.read_bytes()

    corrida = ingerir_archivo(ruta)

    assert corrida.estado == EstadoCorrida.EXITOSA
    # Otro proceso, con otra configuracion y otro almacen sobre la misma raiz: lo que haria una API
    # o un worker recien reiniciados. El archivo sigue ahi, byte por byte.
    otra_config = Config()
    with sesion() as s:
        guardada = s.get_one(Corrida, corrida.id)
        artefacto = s.get_one(ArtefactoFuente, guardada.artefacto_fuente_id)
    with almacen_de(otra_config).abrir(artefacto.sha256) as objeto:
        recuperado = objeto.read()
    assert recuperado == contenido
    assert hashlib.sha256(recuperado).hexdigest() == guardada.firma == artefacto.sha256
    assert (artefacto.formato, artefacto.tamano_bytes) == ("xlsx", len(contenido))


def test_un_reintento_despues_de_un_rechazo_reusa_el_mismo_artefacto(tmp_path):
    ruta = generar_archivo(
        tmp_path / "c.csv", n=100, tasa_invalidas=0.1, semilla=5, fecha_corte=CORTE
    )

    rechazada = ingerir_archivo(ruta, tolerancia=0.05)
    reintento = ingerir_archivo(ruta, tolerancia=0.2)

    assert (rechazada.estado, reintento.estado) == (EstadoCorrida.RECHAZADA, EstadoCorrida.EXITOSA)
    assert rechazada.artefacto_fuente_id == reintento.artefacto_fuente_id
    assert len(_artefactos()) == 1
    # Cada corrida conserva su propia historia: la rechazada no cambio.
    with sesion() as s:
        assert s.get_one(Corrida, rechazada.id).estado == EstadoCorrida.RECHAZADA


def test_auditar_vuelve_a_firmar_cada_artefacto(tmp_path, almacen):
    buena = ingerir_archivo(generar_archivo(tmp_path / "a.csv", n=30, semilla=6, fecha_corte=CORTE))
    danada = ingerir_archivo(
        generar_archivo(tmp_path / "b.csv", n=31, semilla=7, fecha_corte=CORTE)
    )
    with sesion() as s:
        sha_danada = s.get_one(ArtefactoFuente, danada.artefacto_fuente_id).sha256
    ruta = almacen.ruta(sha_danada)
    ruta.chmod(0o600)
    ruta.write_bytes(b"otra cosa")

    with sesion() as s:
        revisados = {r.artefacto.id: r.problema for r in auditar_artefactos(s, almacen)}

    assert revisados[buena.artefacto_fuente_id] is None
    assert "esta danado" in revisados[danada.artefacto_fuente_id]


def test_verificar_fuentes_dice_si_el_almacen_tiene_lo_que_la_base_registra(tmp_path, almacen):
    vacio = cli.invoke(cli_app, ["verificar-fuentes", "--minimo", "1"])
    assert vacio.exit_code == 1  # un almacen sin nada no pasa un smoke que espera algo
    assert "0 de 0 artefactos intactos" in vacio.output

    corrida = ingerir_archivo(
        generar_archivo(tmp_path / "c.csv", n=40, semilla=8, fecha_corte=CORTE)
    )
    intacto = cli.invoke(cli_app, ["verificar-fuentes", "--minimo", "1"])
    assert (intacto.exit_code, intacto.output.strip()) == (0, "1 de 1 artefactos intactos.")

    with sesion() as s:
        sha256 = s.get_one(ArtefactoFuente, corrida.artefacto_fuente_id).sha256
    ruta = almacen.ruta(sha256)
    contenido = ruta.read_bytes()
    ruta.chmod(0o600)
    ruta.unlink()

    faltante = cli.invoke(cli_app, ["verificar-fuentes"])
    assert faltante.exit_code == 1
    assert "no tiene el artefacto" in faltante.output
    almacen.guardar(io.BytesIO(contenido))  # se repone, para no dejarlo roto a otras pruebas


def test_cada_corrida_registra_el_despacho_y_la_cartera_de_su_configuracion(tmp_path, monkeypatch):
    from motor_cartera.config import config

    monkeypatch.setattr(config, "despacho_id", "DSP_002")
    monkeypatch.setattr(config, "cartera_id", "OTRA_CARTERA")

    corrida = ingerir_archivo(
        generar_archivo(tmp_path / "c.csv", n=20, semilla=9, fecha_corte=CORTE)
    )

    with sesion() as s:
        guardada = s.get_one(Corrida, corrida.id)
        assert (guardada.despacho_id, guardada.cartera_id) == ("DSP_002", "OTRA_CARTERA")
        assert s.exec(select(func.count()).select_from(Corrida)).one() == 1


@pytest.mark.parametrize("valor", ["dsp-001", "", "D" * 33, "DSP 001"])
def test_el_despacho_tiene_la_forma_de_un_identificador_interno(valor):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Config(despacho_id=valor)
