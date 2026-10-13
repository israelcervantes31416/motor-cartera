"""ruteo/v2 por la linea de comandos: generar la red sintetica (en el origen de las pruebas o dentro
de un municipio, con los puntos de la prueba de humo), preparar una red (sin decir como, con un
nombre que no es de una red, o con Docker), registrarla (y que una invalida quede FALLIDA), rutear
un plan en primer plano, resumirlo, y el backfill, que pide solo lo que falta sobre una red
declarada. Ninguna salida lleva una coordenada de una cuenta."""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal

import pytest
from ruteo_v2_escenarios import (
    ORIGEN,
    CuentaRuta,
    ProveedorDePruebas,
    base,
    coordenada,
    escenario,
    fuente_sintetica,
    gestor,
    preparada,
    red_de_pruebas,
)
from sqlalchemy import text
from territorial_v2_escenarios import referencia_tv2
from typer.testing import CliRunner

from motor_cartera.cli import app
from motor_cartera.config import config
from motor_cartera.db.sesion import sesion
from motor_cartera.ruteo_v2 import ejecuciones as motor
from motor_cartera.ruteo_v2 import red_vial
from motor_cartera.ruteo_v2.sintetica import BASE_DE_HUMO, PUNTOS_DE_HUMO, Origen, osm_xml

pytestmark = pytest.mark.usefixtures("bd")

cli = CliRunner()
NO_EXISTE = "00000000-0000-4000-8000-000000000000"
JUEVES = "2026-10-01"
CUENTAS = [CuentaRuta(1, 1_000, 1_500), CuentaRuta(2, 1_600, 1_000), CuentaRuta(3, 2_000, 2_000)]
DE_LAS_CUENTAS = re.compile(r"-98\.\d{4,}|19\.0\d{4,}")
"""La longitud o la latitud de una cuenta de estas pruebas, con sus decimales."""


def _id(salida: str, llave: str) -> str:
    encontrado = re.search(rf"^{llave} ([0-9a-f-]{{36}})$", salida, re.MULTILINE)
    assert encontrado, salida
    return encontrado.group(1)


def _redes() -> list[tuple[str, str]]:
    with sesion() as s:
        return [
            tuple(f)
            for f in s.execute(
                text("SELECT estado, coalesce(detalle, '') FROM version_red_vial ORDER BY id")
            ).all()
        ]


def _ejecuciones() -> list[tuple[str, str | None]]:
    with sesion() as s:
        return [
            tuple(f)
            for f in s.execute(
                text("SELECT estado, resultado FROM ejecucion_ruteo_v2 ORDER BY id")
            ).all()
        ]


# --- la red sintetica -----------------------------------------------------------------------------


def test_la_red_sintetica_por_la_linea_de_comandos_es_la_misma_byte_por_byte(tmp_path):
    destino = tmp_path / "red.osm"

    escrita = cli.invoke(app, ["generar-red-sintetica", "--destino", str(destino)])
    otra = cli.invoke(
        app,
        ["generar-red-sintetica", "--destino", str(tmp_path / "otra.osm"), "--origen", "-99,20"],
    )

    assert escrita.exit_code == 0, escrita.output
    assert destino.read_bytes() == osm_xml(ORIGEN)
    assert hashlib.sha256(destino.read_bytes()).hexdigest() == (
        "a75f8b8b28a8d0753cc00aeea92c2bedf40fcb65e6f202b7e63cec390037ea0e"
    )
    assert "origen -98.2000000,19.0400000" in escrita.output
    assert f"base {' '.join(coordenada(*BASE_DE_HUMO))}" in escrita.output
    assert otra.exit_code == 0, otra.output
    assert (tmp_path / "otra.osm").read_bytes() == osm_xml(Origen(Decimal(-99), Decimal(20)))
    no_escrita = tmp_path / "no_escrita.osm"
    sin_municipio = cli.invoke(
        app, ["generar-red-sintetica", "--destino", str(no_escrita), "--puntos", "muestra.json"]
    )
    assert sin_municipio.exit_code == 2
    assert "--puntos necesita --municipio" in sin_municipio.output
    assert not no_escrita.exists()


def test_la_red_sintetica_dentro_de_un_municipio_con_los_puntos_de_humo(tmp_path):
    referencia = referencia_tv2(tmp_path)
    muestra = tmp_path / "muestra.json"
    otro = [[-98.0, 19.5]]
    muestra.write_text(
        json.dumps({"version": "muestra-puntos/v1", "municipios": {"21114": [], "21015": otro}}),
        encoding="ascii",
    )
    generar = [
        "generar-red-sintetica",
        "--destino",
        str(tmp_path / "red.osm"),
        "--geografia-version",
        str(referencia.geografia_version_id),
    ]

    dentro = cli.invoke(app, [*generar, "--municipio", "21114", "--puntos", str(muestra)])
    no_esta = cli.invoke(app, [*generar, "--municipio", "99999"])

    assert dentro.exit_code == 0, dentro.output
    # 1.5 km al este y 3 km al norte de la esquina suroeste del municipio (-98.30, 18.95).
    origen = Origen(*Origen(Decimal("-98.3"), Decimal("18.95")).grados(1_500, 3_000))
    assert f"origen {origen.lon},{origen.lat}" in dentro.output
    assert (tmp_path / "red.osm").read_bytes() == osm_xml(origen)
    puntos = json.loads(muestra.read_text(encoding="ascii"))["municipios"]
    assert puntos["21114"] == [[float(c) for c in origen.coordenada(*xy)] for xy in PUNTOS_DE_HUMO]
    assert puntos["21015"] == otro
    assert f"Reemplazados en {muestra}: 18 puntos de 21114" in dentro.output
    assert no_esta.exit_code == 1
    assert "No esta el municipio 99999" in no_esta.output


# --- preparar y registrar una red -----------------------------------------------------------------


def test_preparar_una_red_dice_como_y_con_docker_deja_su_guion(tmp_path):
    fuente = fuente_sintetica(tmp_path / "entrada")
    raiz = tmp_path / "redes"
    preparar = ["preparar-red-vial", str(fuente), "--region", "Red sintetica de pruebas"]
    otro_nombre = tmp_path / "entrada" / "red.txt"
    otro_nombre.write_bytes(fuente.read_bytes())

    sin_como = cli.invoke(app, preparar)
    con_los_dos = cli.invoke(app, [*preparar, "--docker", "--osrm-bin", str(tmp_path)])
    no_es_red = cli.invoke(
        app, ["preparar-red-vial", str(otro_nombre), "--region", "X", "--docker"]
    )
    con_docker = cli.invoke(
        app, [*preparar, "--docker", "--destino", str(raiz), "--fecha-snapshot", "2026-10-11"]
    )

    assert sin_como.exit_code == 2 and "--osrm-bin o --docker" in sin_como.output
    assert con_los_dos.exit_code == 2
    assert no_es_red.exit_code == 1 and "RED_VIAL_INVALIDA" in no_es_red.output
    assert con_docker.exit_code == 0, con_docker.output
    huella = re.search(r"^huella ([0-9a-f]{64})$", con_docker.output, re.MULTILINE).group(1)
    directorio = raiz / huella
    assert (directorio / "preparar.sh").exists()
    assert red_vial.IMAGEN_OSRM in (directorio / "preparar.sh").read_text(encoding="utf-8")
    assert "registrar-red-vial" in con_docker.output
    assert _redes() == [("EN_PROCESO", "")]
    _id(con_docker.output, "red_vial_version_id")


def test_registrar_publica_una_red_valida_y_deja_fallida_una_invalida(tmp_path, monkeypatch):
    valida = preparada(fuente_sintetica(tmp_path / "entrada"), tmp_path / "redes")
    # Otra fuente (la misma red en otro origen): otra huella, preparada a medias.
    otra_fuente = tmp_path / "otra" / "red_sintetica.osm"
    otra_fuente.parent.mkdir()
    otra_fuente.write_bytes(osm_xml(Origen(Decimal(-99), Decimal(20))))
    invalida = preparada(otra_fuente, tmp_path / "otras")
    assert invalida.huella != valida.huella
    (invalida.directorio / "red.osrm.cells").unlink()
    monkeypatch.setattr(config, "osrm_url", None)
    with sesion() as s:
        red_vial.abrir_preparacion(s, invalida)
        s.commit()

    publicada = cli.invoke(app, ["registrar-red-vial", str(valida.directorio)])
    otra_vez = cli.invoke(app, ["registrar-red-vial", str(valida.directorio)])
    rechazada = cli.invoke(app, ["registrar-red-vial", str(invalida.directorio)])

    assert publicada.exit_code == 0, publicada.output
    assert f"huella {valida.huella}" in publicada.output and "nueva" in publicada.output
    assert "reutilizada" in otra_vez.output
    assert _id(otra_vez.output, "red_vial_version_id") == _id(
        publicada.output, "red_vial_version_id"
    )
    assert rechazada.exit_code == 1 and "RED_VIAL_INVALIDA" in rechazada.output
    estados = dict((estado, detalle) for estado, detalle in _redes())
    assert set(estados) == {"PUBLICADA", "FALLIDA"}
    assert "faltan cells" in estados["FALLIDA"]
    sin_backend = cli.invoke(
        app, ["registrar-red-vial", str(valida.directorio), "--verificar-backend"]
    )
    assert sin_backend.exit_code == 1 and "Sin MC_OSRM_URL" in sin_backend.output


# --- rutear, resumir y el backfill ----------------------------------------------------------------


@pytest.fixture
def plan(tmp_path):
    return escenario(tmp_path, CUENTAS, [base("BASE_A", 1_000, 1_000)], [gestor("GESTOR_A")]).campo


@pytest.fixture
def con_proveedor(monkeypatch):
    """Un proceso con proveedor de ruteo: el de pruebas, atado a la red que se le da."""

    def configurar(red) -> ProveedorDePruebas:
        proveedor = ProveedorDePruebas(red.huella)
        monkeypatch.setattr(config, "osrm_url", "http://osrm-de-pruebas:5000")
        monkeypatch.setattr(motor, "proveedor_configurado", lambda _config: proveedor)
        return proveedor

    return configurar


def test_rutear_en_primer_plano_y_resumir_sin_coordenadas(plan, con_proveedor, monkeypatch):
    red = red_de_pruebas()
    rutear = [
        "rutear-v2",
        "--plan",
        str(plan.campo_run_id),
        "--red-vial-version",
        str(red.red_vial_version_id),
    ]
    monkeypatch.setattr(config, "osrm_url", None)
    sin_proveedor = cli.invoke(app, rutear)
    con_proveedor(red)

    ruteado = cli.invoke(app, rutear)
    otra_vez = cli.invoke(app, rutear)
    otra_politica = cli.invoke(app, [*rutear, "--politica", "ruteo-public-v9"])
    ruteo_run_id = _id(ruteado.output, "ruteo_run_id")
    resumen = cli.invoke(app, ["resumen-ruteo-v2", "--ejecucion", ruteo_run_id])
    no_existe = cli.invoke(app, ["resumen-ruteo-v2", "--ejecucion", NO_EXISTE])

    assert sin_proveedor.exit_code == 2 and "define MC_OSRM_URL" in sin_proveedor.output
    assert ruteado.exit_code == 0, ruteado.output
    assert f"ruteo/v2 {ruteo_run_id}: EXITOSA" in ruteado.output
    assert otra_vez.exit_code == 1 and "RUTEO_V2_YA_GENERADO" in otra_vez.output
    assert otra_politica.exit_code == 1 and "POLITICA_NO_SOPORTADA" in otra_politica.output
    assert _ejecuciones() == [("EXITOSA", "RUTAS_PUBLICADAS")]
    assert resumen.exit_code == 0, resumen.output
    assert f"plan de campo {plan.campo_run_id}, fecha de operacion {JUEVES}" in resumen.output
    assert "1 rutas: 1 completas" in resumen.output
    assert "3 paradas: 3 programadas, 0 sin tiempo" in resumen.output
    assert "BASE_A: 1 rutas (1 completas), 3 de 3 paradas programadas" in resumen.output
    assert "no una ruta optima" in resumen.output and "sin trafico" in resumen.output
    assert no_existe.exit_code == 1
    for salida in (ruteado.output, resumen.output):
        assert not DE_LAS_CUENTAS.search(salida), salida


def test_el_backfill_pide_solo_lo_que_falta_sobre_la_red_declarada(
    plan, con_proveedor, trabajar, tmp_path
):
    red = red_de_pruebas()
    backfill = ["backfill-ruteo-v2", "--red-vial-version", str(red.red_vial_version_id)]
    with sesion() as s:
        en_preparacion = red_vial.abrir_preparacion(
            s, preparada(fuente_sintetica(tmp_path / "entrada"), tmp_path / "redes")
        ).version.red_vial_version_id
        s.commit()

    sin_red = cli.invoke(app, ["backfill-ruteo-v2"])
    red_ajena = cli.invoke(app, ["backfill-ruteo-v2", "--red-vial-version", NO_EXISTE])
    sin_publicar = cli.invoke(app, ["backfill-ruteo-v2", "--red-vial-version", str(en_preparacion)])
    revisado = cli.invoke(app, [*backfill, "--dry-run"])
    tras_revisar = _ejecuciones()
    otro_dia = cli.invoke(app, [*backfill, "--fecha-operacion", "2026-10-02", "--dry-run"])
    encolado = cli.invoke(app, backfill)
    con_proveedor(red)
    procesados = trabajar()
    otra_vez = cli.invoke(app, backfill)

    assert sin_red.exit_code == 2
    assert red_ajena.exit_code == 1 and f"No existe la red vial {NO_EXISTE}" in red_ajena.output
    assert sin_publicar.exit_code == 1 and "esta EN_PROCESO" in sin_publicar.output
    assert revisado.exit_code == 0, revisado.output
    assert "Ejecuciones pendientes: 1" in revisado.output
    assert f"{JUEVES}: plan {plan.campo_run_id}" in revisado.output
    assert "Con --dry-run no se encolo nada." in revisado.output
    assert tras_revisar == []
    assert "Ejecuciones pendientes: 0" in otro_dia.output
    assert encolado.exit_code == 0, encolado.output
    assert "Se encolaron 1 trabajos RUTEO_V2" in encolado.output
    # El worker toma tambien lo que el escenario dejo en la cola; de ruteo/v2, solo ese trabajo.
    ruteo = [(p.estado, p.intentos) for p in procesados if p.tipo == "RUTEO_V2"]
    assert ruteo == [("COMPLETADO", 1)]
    assert _ejecuciones() == [("EXITOSA", "RUTAS_PUBLICADAS")]
    assert "ya ruteados: 1" in otra_vez.output and "Ejecuciones pendientes: 0" in otra_vez.output
    assert "Se encolaron 0 trabajos" in otra_vez.output
