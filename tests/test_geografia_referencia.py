"""Importar una referencia geografica: el sistema de coordenadas validado y normalizado a EPSG:4326,
las claves y las geometrias comprobadas, solo reparaciones seguras y registradas, el SHA-256 del
archivo, la publicacion todo o nada, una importacion a la vez, y lo publicado inmutable.

Todas las capas son sinteticas y diminutas; la referencia oficial del INEGI solo se prueba si el
archivo esta en la maquina (MC_REFERENCIA_INEGI), nunca se descarga aqui."""

from __future__ import annotations

import hashlib
import os
import zipfile
from pathlib import Path

import pytest
import shapefile
from geografia_escenarios import MUNICIPIOS, A, B, C
from sqlalchemy import func, text
from sqlalchemy.exc import DBAPIError
from sqlmodel import select

from motor_cartera.db.modelos import MunicipioGeografico, VersionGeografia
from motor_cartera.db.sesion import crear_motor, sesion
from motor_cartera.geografia import referencia
from motor_cartera.geografia.referencia import (
    GeografiaEnImportacion,
    GeografiaYaImportada,
    ImportacionFallida,
    ImportacionRechazada,
    Metadatos,
    importar,
    referencias,
)
from motor_cartera.geografia.sintetica import (
    LCC_MEXICO_PRJ,
    WGS84_PRJ,
    MunicipioSintetico,
    escribir_capa,
    rectangulo,
)

pytestmark = pytest.mark.usefixtures("bd")

SINTETICA = Metadatos("SINTETICO", "Referencia sintetica de pruebas", "v1")
INEGI = Path(
    os.environ.get(
        "MC_REFERENCIA_INEGI",
        Path(__file__).resolve().parents[1]
        / "datos/referencias/inegi_mg_2025/mg_2025_integrado.zip",
    )
)


def _cuantos(modelo, *condiciones) -> int:
    with sesion() as s:
        return s.exec(select(func.count()).select_from(modelo).where(*condiciones)).one()


def _versiones() -> list[VersionGeografia]:
    with sesion() as s:
        return list(s.exec(select(VersionGeografia).order_by(VersionGeografia.id)).all())


def _sha(ruta: Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


# --- lo que publica -------------------------------------------------------------------------------


def test_una_capa_en_wgs84_se_publica_con_su_sha256_sus_claves_y_sus_metadatos(tmp_path):
    ruta = escribir_capa(tmp_path / "capa.zip", list(MUNICIPIOS))
    antes = _sha(ruta)

    v = importar(ruta, metadatos=SINTETICA)

    assert (v.estado, v.municipios, v.reparadas, v.srid, v.srid_origen) == (
        "PUBLICADA",
        3,
        0,
        4326,
        4326,
    )
    assert (v.proveedor, v.producto, v.edicion, v.nivel, v.capa, v.archivo) == (
        "SINTETICO",
        "Referencia sintetica de pruebas",
        "v1",
        "MUNICIPAL",
        "municipios",
        "capa.zip",
    )
    assert (v.sha256, v.tamano_bytes) == (antes, ruta.stat().st_size)
    assert v.terminada_en is not None and "ya en EPSG:4326" in v.detalle
    assert v.metadatos["importador"] == "importador-geografia/v1"
    assert v.metadatos["codificacion"] == "utf-8" and v.metadatos["cpg"] == "UTF-8"
    assert v.metadatos["envolvente"] == [-98.3, 18.95, -89.45, 21.15]
    assert set(v.metadatos["versiones"]) == {"postgis", "geos", "proj"}
    assert v.metadatos["reparaciones"] == []
    # El archivo no se modifica: se lee.
    assert _sha(ruta) == antes
    with sesion() as s:
        filas = s.execute(
            text(
                "SELECT cvegeo, cve_ent, cve_mun, nombre_oficial, registro_origen, reparada, "
                "GeometryType(geometria), ST_SRID(geometria), ST_IsValid(geometria) "
                "FROM municipio_geografico ORDER BY cvegeo"
            )
        ).all()
    assert [tuple(f) for f in filas] == [
        ("21015", "21", "015", B.nombre, 1, False, "MULTIPOLYGON", 4326, True),
        ("21114", "21", "114", A.nombre, 2, False, "MULTIPOLYGON", 4326, True),
        ("31050", "31", "050", C.nombre, 3, False, "MULTIPOLYGON", 4326, True),
    ]


def test_una_capa_del_marco_geoestadistico_se_transforma_de_epsg_6372_a_4326(tmp_path):
    """Los mismos rectangulos, escritos en la proyeccion del INEGI (Lambert, ITRF2008), vuelven a
    sus grados con PostGIS: X es la longitud y Y la latitud."""
    proyectados = []
    with sesion() as s:
        for m in MUNICIPIOS:
            (exterior,) = m.poligonos[0]
            puntos = [
                s.execute(
                    text(
                        "SELECT ST_X(p), ST_Y(p) FROM (SELECT ST_Transform(ST_SetSRID("
                        "ST_MakePoint(:x, :y), 4326), 6372) AS p) q"
                    ),
                    {"x": x, "y": y},
                ).one()
                for x, y in exterior
            ]
            proyectados.append(MunicipioSintetico(m.cvegeo, m.nombre, ((puntos,),)))
    ruta = escribir_capa(tmp_path / "lcc.zip", proyectados, prj=LCC_MEXICO_PRJ)

    v = importar(ruta, metadatos=SINTETICA)

    assert (v.srid_origen, v.municipios) == (6372, 3)
    assert "EPSG:6372" in v.metadatos["crs_origen"]
    assert "transformados de EPSG:6372 a EPSG:4326" in v.detalle
    xmin, ymin, xmax, ymax = v.metadatos["envolvente"]
    assert (xmin, ymin, xmax, ymax) == pytest.approx((-98.3, 18.95, -89.45, 21.15), abs=1e-6)
    with sesion() as s:
        cubre = (
            s.execute(
                text(
                    "SELECT cvegeo FROM municipio_geografico WHERE ST_Covers(geometria, "
                    "ST_SetSRID(ST_MakePoint(-98.2, 19.05), 4326))"
                )
            )
            .scalars()
            .all()
        )
    assert cubre == ["21114"]


def test_los_nombres_se_leen_con_la_codificacion_que_declara_su_cpg(tmp_path):
    con_acentos = rectangulo("21114", "Tehuacán de Peñas (sintético)", -98.3, 18.95, -98.1, 19.15)
    ruta = escribir_capa(tmp_path / "latin.zip", [con_acentos], cpg="88591", codificacion="latin-1")

    v = importar(ruta, metadatos=SINTETICA)

    assert v.metadatos["codificacion"] == "latin-1"
    with sesion() as s:
        nombre = s.exec(select(MunicipioGeografico.nombre_oficial)).one()
    assert nombre == "Tehuacán de Peñas (sintético)"


def test_una_codificacion_que_no_se_reconoce_se_rechaza(tmp_path):
    ruta = escribir_capa(tmp_path / "rara.zip", [A], cpg="KLINGON")
    with pytest.raises(ImportacionRechazada, match="KLINGON"):
        importar(ruta, metadatos=SINTETICA)
    assert _versiones() == []


def test_un_shp_suelto_se_lee_con_sus_companeros(tmp_path):
    ruta = escribir_capa(tmp_path / "capa.zip", list(MUNICIPIOS))
    with zipfile.ZipFile(ruta) as paquete:
        paquete.extractall(tmp_path / "suelto")

    v = importar(tmp_path / "suelto" / "municipios.shp", metadatos=SINTETICA)

    assert (v.estado, v.municipios, v.capa) == ("PUBLICADA", 3, "municipios")
    assert v.sha256 == _sha(tmp_path / "suelto" / "municipios.shp")


def test_un_zip_con_varias_capas_pide_cual(tmp_path):
    primera = escribir_capa(tmp_path / "a.zip", [A], capa="datos/uno")
    segunda = escribir_capa(tmp_path / "b.zip", [B], capa="datos/dos")
    juntas = tmp_path / "juntas.zip"
    with zipfile.ZipFile(juntas, "w") as destino:
        for origen in (primera, segunda):
            with zipfile.ZipFile(origen) as paquete:
                for miembro in paquete.infolist():
                    destino.writestr(miembro, paquete.read(miembro))

    with pytest.raises(ImportacionRechazada, match="datos/dos, datos/uno"):
        importar(juntas, metadatos=SINTETICA)
    v = importar(juntas, capa="datos/dos", metadatos=SINTETICA)

    assert (v.capa, v.municipios) == ("datos/dos", 1)


# --- lo que se rechaza sin registrar nada ---------------------------------------------------------


def test_un_archivo_que_no_es_el_del_manifiesto_se_rechaza_por_su_sha256(tmp_path):
    ruta = escribir_capa(tmp_path / "capa.zip", list(MUNICIPIOS))
    with pytest.raises(ImportacionRechazada, match="SHA-256"):
        importar(ruta, referencia="inegi-mg-2025-municipal")
    with pytest.raises(ImportacionRechazada, match="no tiene la referencia"):
        importar(ruta, referencia="inegi-mg-1999")
    assert _versiones() == []


def test_el_manifiesto_dice_de_donde_sale_la_referencia_oficial():
    oficial = referencias()["inegi-mg-2025-municipal"]
    assert oficial.datos["proveedor"] == "INEGI"
    assert oficial.datos["upc"] == "794551163061"
    assert oficial.datos["url"].startswith("https://www.inegi.org.mx/")
    assert oficial.sha256 == "f1335bab72d5582adab06e9e3b5d49b7c42da8f8d82e588f484ad6a7c7871d1b"
    assert (oficial.capa, oficial.datos["municipios"]) == ("conjunto_de_datos/00mun", 2478)
    assert oficial.datos["licencia_url"] == "https://www.inegi.org.mx/inegi/terminos.html"


@pytest.mark.parametrize(
    ("opciones", "dice"),
    [
        ({"prj": LCC_MEXICO_PRJ.replace("-102.0", "-99.0")}, "EPSG:6372"),
        ({"prj": 'GEOGCS["x",DATUM["y",SPHEROID["z",6378206.4,294.9786982]]]'}, "WGS 84"),
        ({"campos": ("CVEGEO", "NOMGEO")}, "CVE_ENT, CVE_MUN"),
    ],
    ids=["otra-proyeccion", "otro-datum", "sin-campos"],
)
def test_una_capa_con_otro_sistema_o_sin_sus_campos_se_rechaza(tmp_path, opciones, dice):
    ruta = escribir_capa(tmp_path / "capa.zip", [A], **opciones)
    with pytest.raises(ImportacionRechazada, match=dice):
        importar(ruta, metadatos=SINTETICA)
    assert _versiones() == []


@pytest.mark.parametrize("falta", ["prj", "shx", "dbf"])
def test_una_capa_incompleta_se_rechaza(tmp_path, falta):
    completa = escribir_capa(tmp_path / "capa.zip", [A])
    incompleta = tmp_path / "incompleta.zip"
    with zipfile.ZipFile(completa) as origen, zipfile.ZipFile(incompleta, "w") as destino:
        for miembro in origen.infolist():
            if not miembro.filename.endswith(f".{falta}"):
                destino.writestr(miembro, origen.read(miembro))
    with pytest.raises(ImportacionRechazada, match=f".{falta}"):
        importar(incompleta, metadatos=SINTETICA)
    assert _versiones() == []


def test_una_capa_de_puntos_no_es_una_capa_de_municipios(tmp_path):
    directorio = tmp_path / "puntos"
    directorio.mkdir()
    with shapefile.Writer(str(directorio / "capa"), shapeType=shapefile.POINT) as escritor:
        for campo in ("CVEGEO", "CVE_ENT", "CVE_MUN", "NOMGEO"):
            escritor.field(campo, "C", size=80)
        escritor.point(-98.2, 19.05)
        escritor.record("21114", "21", "114", "Punto")
    (directorio / "capa.prj").write_text(WGS84_PRJ)
    with pytest.raises(ImportacionRechazada, match="POLYGON"):
        importar(directorio / "capa.shp", metadatos=SINTETICA)


def test_un_archivo_que_no_es_un_shapefile_se_rechaza(tmp_path):
    ruta = tmp_path / "capa.geojson"
    ruta.write_text("{}")
    with pytest.raises(ImportacionRechazada, match="Shapefile"):
        importar(ruta, metadatos=SINTETICA)


# --- lo que falla despues de registrarse: FALLIDA, sin ningun municipio ---------------------------


@pytest.mark.parametrize(
    ("municipios", "dice"),
    [
        (
            [A, MunicipioSintetico("21115", "Clave incoherente", A.poligonos, entidad="22")],
            "claves que no son de un municipio",
        ),
        ([A, rectangulo("21114", "Otra vez A", -97.0, 18.0, -96.9, 18.1)], "repite CVEGEO"),
        ([A, MunicipioSintetico("21015", "Sin geometria", ())], "no trae geometria"),
        ([rectangulo("21114", "", -98.3, 18.95, -98.1, 19.15)], "sin nombre"),
    ],
    ids=["claves", "repetida", "sin-geometria", "sin-nombre"],
)
def test_una_capa_corrupta_queda_fallida_sin_publicar_nada(tmp_path, municipios, dice):
    ruta = escribir_capa(tmp_path / "capa.zip", municipios)

    with pytest.raises(ImportacionFallida, match=dice) as error:
        importar(ruta, metadatos=SINTETICA)

    (v,) = _versiones()
    assert (v.estado, v.municipios, v.geografia_version_id) == (
        "FALLIDA",
        0,
        error.value.geografia_version_id,
    )
    assert dice in v.detalle
    assert _cuantos(MunicipioGeografico) == 0


def test_una_capa_en_el_sistema_equivocado_queda_fuera_del_mundo_y_se_rechaza(tmp_path):
    """Metros declarados como grados: la envolvente sale de los grados validos."""
    en_metros = rectangulo("21114", "En metros", 2_500_000, 800_000, 2_510_000, 810_000)
    ruta = escribir_capa(tmp_path / "capa.zip", [en_metros])

    with pytest.raises(ImportacionFallida, match="no es el que declara"):
        importar(ruta, metadatos=SINTETICA)
    assert _cuantos(MunicipioGeografico) == 0


AUTOTANGENTE = MunicipioSintetico(
    "21114",
    "Anillo que se toca a si mismo",
    (
        (
            [
                (-98.30, 18.95),
                (-98.10, 18.95),
                (-98.10, 19.15),
                (-98.20, 19.15),
                (-98.15, 19.05),
                (-98.25, 19.05),
                (-98.20, 19.15),
                (-98.30, 19.15),
                (-98.30, 18.95),
            ],
        ),
    ),
)
"""Un exterior que pasa dos veces por (-98.20, 19.15): una muesca triangular que el formato de ESRI
acepta y la especificacion OGC no. Repararlo da un hueco que toca el borde, con la misma area."""

MONO = MunicipioSintetico(
    "21015",
    "Mono",
    (([(-98.10, 18.95), (-97.90, 19.15), (-97.90, 18.95), (-98.10, 19.15), (-98.10, 18.95)],),),
)
"""Un anillo que se cruza a si mismo: repararlo cambia su area por completo."""


def test_una_geometria_invalida_se_repara_solo_si_es_seguro_y_queda_registrada(tmp_path):
    ruta = escribir_capa(tmp_path / "capa.zip", [AUTOTANGENTE, C])

    v = importar(ruta, metadatos=SINTETICA)

    assert (v.estado, v.municipios, v.reparadas) == ("PUBLICADA", 2, 1)
    (reparacion,) = v.metadatos["reparaciones"]
    assert reparacion["cvegeo"] == "21114"
    assert "Self-intersection" in reparacion["motivo"]
    assert reparacion["area_m2_despues"] == pytest.approx(reparacion["area_m2_antes"], rel=1e-6)
    with sesion() as s:
        filas = s.execute(
            text(
                "SELECT cvegeo, reparada, ST_IsValid(geometria), ST_NumInteriorRings("
                "ST_GeometryN(geometria, 1)) FROM municipio_geografico ORDER BY cvegeo"
            )
        ).all()
    assert [tuple(f) for f in filas] == [("21114", True, True, 1), ("31050", False, True, 0)]


def test_una_reparacion_que_cambiaria_el_area_rechaza_la_importacion(tmp_path):
    ruta = escribir_capa(tmp_path / "capa.zip", [MONO, A], orientar=False)

    with pytest.raises(ImportacionFallida, match="repararla no es seguro"):
        importar(ruta, metadatos=SINTETICA)
    assert _cuantos(MunicipioGeografico) == 0


@pytest.mark.parametrize("donde", referencia.PUNTOS_DE_FALLA)
def test_una_falla_en_cualquier_punto_no_publica_ningun_municipio(tmp_path, monkeypatch, donde):
    ruta = escribir_capa(tmp_path / "capa.zip", list(MUNICIPIOS))

    def fallar_en(nombre: str) -> None:
        if nombre == donde:
            raise RuntimeError(nombre)

    monkeypatch.setattr(referencia, "_punto_de_falla", fallar_en)
    with pytest.raises(ImportacionFallida, match="RuntimeError"):
        importar(ruta, metadatos=SINTETICA)
    assert [(v.estado, v.municipios) for v in _versiones()] == [("FALLIDA", 0)]
    assert _cuantos(MunicipioGeografico) == 0

    # Una FALLIDA no estorba: el mismo archivo se vuelve a importar, completo.
    monkeypatch.undo()
    v = importar(ruta, metadatos=SINTETICA)
    assert (v.estado, v.municipios) == ("PUBLICADA", 3)


# --- una vez, una a la vez, y lo publicado no cambia ----------------------------------------------


def test_el_mismo_archivo_no_se_importa_dos_veces_y_otra_edicion_es_otra_version(tmp_path):
    ruta = escribir_capa(tmp_path / "capa.zip", list(MUNICIPIOS))
    primera = importar(ruta, metadatos=SINTETICA)

    with pytest.raises(GeografiaYaImportada) as error:
        importar(ruta, metadatos=SINTETICA)
    assert error.value.previa.geografia_version_id == primera.geografia_version_id

    otra = escribir_capa(tmp_path / "otra.zip", [A, B])
    segunda = importar(otra, metadatos=Metadatos("SINTETICO", "Referencia sintetica", "v2"))
    assert segunda.geografia_version_id != primera.geografia_version_id
    assert [v.estado for v in _versiones()] == ["PUBLICADA", "PUBLICADA"]
    assert _cuantos(MunicipioGeografico) == 5


def test_mientras_otro_proceso_importa_el_mismo_archivo_no_se_empieza_otra(tmp_path):
    ruta = escribir_capa(tmp_path / "capa.zip", list(MUNICIPIOS))
    llave = referencia._candado(_sha(ruta), "municipios")
    with crear_motor().connect() as otro:
        assert otro.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": llave}).scalar_one()
        try:
            with pytest.raises(GeografiaEnImportacion):
                importar(ruta, metadatos=SINTETICA)
        finally:
            otro.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": llave})
            otro.commit()
    assert _versiones() == []
    assert importar(ruta, metadatos=SINTETICA).estado == "PUBLICADA"


def test_una_importacion_interrumpida_se_cierra_fallida_y_la_siguiente_publica(tmp_path):
    """Un proceso que murio despues de registrar su version la dejo EN_PROCESO y sin candado: la
    siguiente importacion del mismo archivo la cierra antes de empezar."""
    ruta = escribir_capa(tmp_path / "capa.zip", list(MUNICIPIOS))
    with sesion() as s:
        s.add(
            VersionGeografia(
                proveedor="SINTETICO",
                producto="Referencia sintetica de pruebas",
                edicion="v1",
                nivel="MUNICIPAL",
                capa="municipios",
                archivo="capa.zip",
                sha256=_sha(ruta),
                tamano_bytes=ruta.stat().st_size,
                srid_origen=4326,
                metadatos={},
            )
        )
        s.commit()

    v = importar(ruta, metadatos=SINTETICA)

    interrumpida, publicada = _versiones()
    assert (interrumpida.estado, publicada.estado) == ("FALLIDA", "PUBLICADA")
    assert "se interrumpio" in interrumpida.detalle
    assert publicada.geografia_version_id == v.geografia_version_id


@pytest.mark.parametrize(
    "sentencia",
    [
        "UPDATE municipio_geografico SET nombre_oficial = 'otro'",
        "DELETE FROM municipio_geografico",
        "UPDATE version_geografia SET edicion = 'otra'",
        "DELETE FROM version_geografia",
    ],
)
def test_una_referencia_publicada_no_se_reescribe(tmp_path, sentencia):
    importar(escribir_capa(tmp_path / "capa.zip", list(MUNICIPIOS)), metadatos=SINTETICA)
    with sesion() as s, pytest.raises(DBAPIError) as error:
        s.execute(text(sentencia))
    assert "no se reescribe" in str(error.value) or "no cambia" in str(error.value)
    assert _cuantos(MunicipioGeografico) == 3


# --- la referencia oficial, si esta en la maquina -------------------------------------------------


@pytest.mark.skipif(not INEGI.exists(), reason="sin el Marco Geoestadistico 2025 en esta maquina")
def test_el_marco_geoestadistico_2025_del_inegi_se_importa_completo():
    v = importar(INEGI, referencia="inegi-mg-2025-municipal")

    assert (v.estado, v.municipios, v.reparadas, v.srid_origen) == ("PUBLICADA", 2478, 0, 6372)
    assert v.sha256 == referencias()["inegi-mg-2025-municipal"].sha256
    assert v.producto == "Marco Geoestadístico Integrado, 2025"
    with sesion() as s:
        # El zocalo de la ciudad de Puebla cae en el municipio de Puebla, y en ningun otro.
        cubre = s.execute(
            text(
                "SELECT cvegeo, nombre_oficial FROM municipio_geografico WHERE ST_Covers("
                "geometria, ST_SetSRID(ST_MakePoint(-98.1981, 19.0433), 4326))"
            )
        ).all()
    assert [tuple(f) for f in cubre] == [("21114", "Puebla")]
