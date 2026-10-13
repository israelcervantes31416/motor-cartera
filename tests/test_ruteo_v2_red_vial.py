"""Las versiones de la red vial, sin OSRM: el perfil fijado, la red sintetica byte por byte, la
huella que sale de la identidad y no de un nombre, el guion de Docker con la imagen fijada, el
manifiesto que detecta una fuente, un perfil o un archivo cambiado, y el registro una vez por
huella.

Los archivos "preparados" de estas pruebas son de mentira (bytes cualesquiera con el nombre de cada
archivo de OSRM): lo que se prueba es la verificacion, no OSRM, que se prueba en
test_ruteo_v2_osrm.py.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
from ruteo_v2_escenarios import descripcion, fuente_sintetica, preparada

from motor_cartera.db.sesion import sesion
from motor_cartera.ruteo_v2 import red_vial
from motor_cartera.ruteo_v2.proveedor import data_version
from motor_cartera.ruteo_v2.sintetica import (
    BASE_DE_HUMO,
    ORIGEN_PRUEBAS,
    PUNTOS_DE_HUMO,
    Origen,
    nodos,
    osm_xml,
    vias,
)

RAIZ = Path(__file__).resolve().parents[1]
SHA_RED_SINTETICA = "a75f8b8b28a8d0753cc00aeea92c2bedf40fcb65e6f202b7e63cec390037ea0e"
"""El SHA-256 de la red sintetica en el origen de las pruebas: la misma entrada, los mismos
bytes."""


@pytest.fixture
def fuente(tmp_path) -> Path:
    return fuente_sintetica(tmp_path / "entrada")


# --- el perfil y la red sintetica -----------------------------------------------------------------


def test_el_perfil_es_el_car_lua_de_osrm_v26_9_0_tal_cual():
    archivos = sorted(
        p.relative_to(red_vial.DIRECTORIO_PERFIL).as_posix()
        for p in red_vial.DIRECTORIO_PERFIL.rglob("*")
        if p.is_file()
    )

    assert red_vial.hash_perfil() == red_vial.HASH_PERFIL_CAR
    assert "car.lua" in archivos and "LICENSE.TXT" in archivos
    assert len([a for a in archivos if a.startswith("lib/")]) == 15
    licencia = (red_vial.DIRECTORIO_PERFIL / "LICENSE.TXT").read_text(encoding="utf-8")
    assert "Project OSRM contributors" in licencia
    assert red_vial.VERSION_OSRM == "v26.9.0"
    assert re.fullmatch(r".+:v26\.9\.0-debian@sha256:[0-9a-f]{64}", red_vial.IMAGEN_OSRM)


def test_la_red_sintetica_es_la_misma_byte_por_byte_y_no_tiene_nada_real():
    contenido = osm_xml(Origen(*ORIGEN_PRUEBAS))

    assert hashlib.sha256(contenido).hexdigest() == SHA_RED_SINTETICA
    assert contenido == osm_xml(Origen(*ORIGEN_PRUEBAS))
    assert (len(nodos()), len(vias())) == (19, 9)
    texto = contenido.decode("utf-8")
    assert texto.count("<node ") == 19 and texto.count("<way ") == 9
    assert texto.count('k="oneway"') == 1
    assert "addr:" not in texto and "Sintetic" in texto
    assert len(PUNTOS_DE_HUMO) == 18 and BASE_DE_HUMO == (1_000, 1_000)
    # Otro origen, otros bytes.
    assert osm_xml(Origen(*(v + 1 for v in ORIGEN_PRUEBAS))) != contenido


# --- la huella ------------------------------------------------------------------------------------


def test_la_huella_sale_de_la_identidad_y_no_del_nombre_ni_de_la_etiqueta(fuente, tmp_path):
    a = red_vial.preparar_directorio(fuente, descripcion(), "docker:x", tmp_path / "a")
    otro_nombre = tmp_path / "otra" / "otro_nombre.osm"
    otro_nombre.parent.mkdir()
    otro_nombre.write_bytes(fuente.read_bytes())
    b = red_vial.preparar_directorio(
        otro_nombre, descripcion(region="Otra etiqueta"), "docker:x", tmp_path / "b"
    )
    otra_distribucion = red_vial.preparar_directorio(
        fuente, descripcion(), "docker:y", tmp_path / "c"
    )
    otros_bytes = tmp_path / "otros" / "red_sintetica.osm"
    otros_bytes.parent.mkdir()
    otros_bytes.write_bytes(fuente.read_bytes() + b"\n")
    otra_fuente = red_vial.preparar_directorio(
        otros_bytes, descripcion(), "docker:x", tmp_path / "d"
    )

    assert a.huella == b.huella == red_vial.huella(a.identidad)
    assert len({a.huella, otra_distribucion.huella, otra_fuente.huella}) == 3
    assert a.identidad["sha256_fuente"] == SHA_RED_SINTETICA
    assert a.identidad["hash_perfil"] == red_vial.HASH_PERFIL_CAR
    assert a.identidad["parametros"] == {"small_component_size": 1}
    assert a.directorio.name == a.huella
    assert (a.directorio / "perfil" / "car.lua").is_file()


def test_una_fuente_que_no_es_una_red_no_se_prepara(tmp_path):
    vacia = tmp_path / "vacia.osm"
    vacia.write_bytes(b"")
    hoja = tmp_path / "cartera.xlsx"
    hoja.write_bytes(b"PK\x03\x04")
    for ruta in (vacia, hoja, tmp_path / "no_existe.osm.pbf"):
        with pytest.raises(red_vial.RedVialInvalida):
            red_vial.preparar_directorio(ruta, descripcion(), "docker:x", tmp_path / "r")
    with pytest.raises(red_vial.RedVialInvalida):
        descripcion(region=" ")


def test_el_guion_de_docker_usa_la_imagen_fijada_y_los_comandos_de_la_huella(fuente, tmp_path):
    p = red_vial.preparar_directorio(
        fuente, descripcion(), red_vial.distribucion_docker(), tmp_path
    )
    guion = (p.directorio / "preparar.sh").read_text(encoding="utf-8")

    assert f"IMAGEN='{red_vial.IMAGEN_OSRM}'" in guion
    assert f"--data_version {data_version(p.huella)} " in guion
    assert "--small-component-size 1 " in guion
    # Cada bitacora la escribe el contenedor, no el anfitrion.
    for comando, bitacora in (
        ("osrm-extract -p perfil/car.lua", "extract"),
        ("osrm-partition red.osrm", "partition"),
        ("osrm-customize red.osrm", "customize"),
    ):
        assert f"sh -c '{comando}" in guion
        assert f" > {bitacora}.log 2>&1'\n" in guion
    assert guion.count('docker run --rm -v "$PWD:/datos" -w /datos "$IMAGEN" sh -c') == 3
    assert "latest" not in guion
    assert red_vial.comandos("red_sintetica.osm", p.huella)[0][-1] == "fuente/red_sintetica.osm"


def test_el_compose_arranca_osrm_con_la_misma_imagen_que_prepara_la_red():
    compose = (RAIZ / "docker-compose.yml").read_text(encoding="utf-8")

    assert f"image: {red_vial.IMAGEN_OSRM}" in compose
    assert "--verbosity" in compose and "WARNING" in compose


def test_el_ci_sirve_la_red_de_las_pruebas_con_la_misma_imagen_y_sin_bitacora_de_accesos():
    ci = (RAIZ / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert red_vial.IMAGEN_OSRM in ci
    imagenes = re.findall(r"ghcr\.io/project-osrm/osrm-backend\S*", ci)
    assert imagenes and set(imagenes) == {red_vial.IMAGEN_OSRM}
    assert "osrm-routed --algorithm mld --verbosity WARNING" in ci
    assert ":latest" not in ci  # ubuntu-latest es la etiqueta del runner, no una imagen


def test_la_distribucion_nativa_es_el_sha_de_sus_binarios(tmp_path):
    for nombre in red_vial.BINARIOS_OSRM:
        (tmp_path / f"{nombre}.exe").write_bytes(nombre.encode())
    distribucion = red_vial.distribucion_nativa(tmp_path)

    assert distribucion.startswith("nativa:") and len(distribucion) == len("nativa:") + 64
    (tmp_path / "osrm-routed.exe").write_bytes(b"otro binario")
    assert red_vial.distribucion_nativa(tmp_path) != distribucion
    (tmp_path / "osrm-extract.exe").unlink()
    with pytest.raises(red_vial.RedVialInvalida):
        red_vial.distribucion_nativa(tmp_path)


# --- el manifiesto --------------------------------------------------------------------------------


def test_el_manifiesto_lleva_la_identidad_los_comandos_cada_archivo_y_la_cobertura(
    fuente, tmp_path
):
    p = preparada(fuente, tmp_path)

    datos = red_vial.manifiesto(p.directorio)

    assert datos["huella"] == p.huella
    assert datos["data_version"] == data_version(p.huella)
    assert datos["comandos"] == red_vial.comandos("red_sintetica.osm", p.huella)
    assert {a["archivo"] for a in datos["artefactos"]} >= {
        f"red.osrm.{a}" for a in red_vial.ARTEFACTOS_MLD
    }
    assert all(re.fullmatch(r"[0-9a-f]{64}", a["sha256"]) for a in datos["artefactos"])
    assert datos["cobertura"] == {
        "nodos_osm": 19,
        "vias_osm": 9,
        "relaciones_osm": 0,
        "restricciones_osm": 0,
        "aristas": 26,
    }
    assert red_vial.hash_manifiesto(datos) == red_vial.hash_manifiesto(
        red_vial.manifiesto(p.directorio)
    )
    texto = json.dumps(datos)
    for llave in ("lat", "lon", "bbox", "location"):
        assert f'"{llave}"' not in texto


@pytest.mark.parametrize("cambio", ["fuente", "perfil", "artefacto", "huella"])
def test_el_manifiesto_detecta_lo_que_cambio(fuente, tmp_path, cambio):
    p = preparada(fuente, tmp_path)
    if cambio == "fuente":
        destino = p.directorio / "fuente" / "red_sintetica.osm"
        destino.write_bytes(destino.read_bytes().replace(b"Avenida", b"Calzada"))
    elif cambio == "perfil":
        perfil = p.directorio / "perfil" / "car.lua"
        perfil.write_bytes(perfil.read_bytes() + b"\n-- un cambio silencioso\n")
    elif cambio == "artefacto":
        (p.directorio / "red.osrm.mldgr").unlink()
    else:
        declaracion = json.loads((p.directorio / "identidad.json").read_bytes())
        declaracion["identidad"]["parametros"] = {"small_component_size": 1000}
        (p.directorio / "identidad.json").write_text(json.dumps(declaracion), encoding="utf-8")

    with pytest.raises(red_vial.RedVialInvalida):
        red_vial.manifiesto(p.directorio)


# --- el registro ----------------------------------------------------------------------------------


@pytest.mark.usefixtures("bd")
def test_una_red_se_publica_una_vez_por_huella(fuente, tmp_path):
    p = preparada(fuente, tmp_path)
    with sesion() as s:
        abierta = red_vial.abrir_preparacion(s, p)
        s.commit()
        assert (abierta.version.estado, abierta.reutilizada) == ("EN_PROCESO", False)
    with sesion() as s:
        registro = red_vial.registrar(s, p.directorio)
        s.commit()
        version = registro.version
        assert (version.estado, registro.reutilizada) == ("PUBLICADA", False)
        assert version.huella == p.huella
        assert version.hash_manifest == red_vial.hash_manifiesto(version.manifiesto)
        assert version.metadatos["cobertura"]["aristas"] == 26
        assert version.publicada_en is not None
        assert (p.directorio / "manifiesto.json").is_file()
    with sesion() as s:
        otra_vez = red_vial.registrar(s, p.directorio)
        assert otra_vez.reutilizada and otra_vez.misma_preparacion is True
        assert otra_vez.version.red_vial_version_id == version.red_vial_version_id
        assert red_vial.abrir_preparacion(s, p).reutilizada
    # Otra preparacion de la misma red (otros bytes en sus archivos, como los de OSRM): la misma
    # huella, la misma version, y se dice que no es la preparacion publicada.
    otra = preparada(fuente, tmp_path / "otra")
    (otra.directorio / "red.osrm.partition").write_bytes(b"otra particion")
    assert otra.huella == p.huella
    with sesion() as s:
        segunda = red_vial.registrar(s, otra.directorio)
        assert segunda.reutilizada and segunda.misma_preparacion is False
        assert segunda.version.hash_manifest == version.hash_manifest


@pytest.mark.usefixtures("bd")
def test_una_preparacion_que_falla_queda_fallida_y_no_estorba_a_la_siguiente(fuente, tmp_path):
    p = preparada(fuente, tmp_path)
    with sesion() as s:
        abierta = red_vial.abrir_preparacion(s, p)
        red_vial.fallar(s, abierta.version, "osrm-extract termino con codigo 1")
        s.commit()
        s.refresh(abierta.version)
        assert (abierta.version.estado, abierta.version.detalle) == (
            "FALLIDA",
            "osrm-extract termino con codigo 1",
        )
    with sesion() as s:
        assert red_vial.registrar(s, p.directorio).version.estado == "PUBLICADA"
        s.commit()


@pytest.mark.usefixtures("bd")
def test_una_red_sintetica_se_registra_con_su_propia_huella():
    with sesion() as s:
        a = red_vial.registrar_sintetica(s, "Modelo A", {"version": "pruebas/v1", "x": 1})
        b = red_vial.registrar_sintetica(s, "Modelo A otra vez", {"version": "pruebas/v1", "x": 1})
        c = red_vial.registrar_sintetica(s, "Modelo C", {"version": "pruebas/v1", "x": 2})
        s.commit()
        assert b.reutilizada and b.version.huella == a.version.huella
        assert c.version.huella != a.version.huella
        assert a.version.motor_ruteo == "SINTETICO" and a.version.estado == "PUBLICADA"
