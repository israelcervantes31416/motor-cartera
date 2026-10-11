"""La configuracion de bases operativas: su validacion fail-closed, su forma canonica, su hash, y su
publicacion versionada e inmutable."""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest
from sqlalchemy import func, text
from sqlalchemy.exc import DBAPIError
from sqlmodel import select

from motor_cartera.bases.configuracion import (
    VERSION_CONFIGURACION,
    ConfiguracionInvalida,
    configuracion_de,
    leer,
    leer_jsonl,
    publicar,
)
from motor_cartera.db.modelos import BaseOperativa, CoberturaBaseMunicipio, VersionBasesOperativas
from motor_cartera.db.sesion import sesion


def base(codigo="BASE_A", **cambios) -> dict:
    return {
        "codigo": codigo,
        "nombre": f"Base {codigo} (sintetica)",
        "lat": 19.05,
        "lon": -98.2,
        "activa": True,
        "municipios": ["21114", "21015"],
        **cambios,
    }


def documento(*bases, nombre="Bases sinteticas") -> dict:
    return {"nombre": nombre, "bases": list(bases) or [base()]}


# --- leer: fail-closed, con todos los problemas ---------------------------------------------------


def test_una_configuracion_valida_queda_en_su_forma_canonica():
    c = leer(documento(base("BASE_Z", municipios=["21015", "21114"]), base("BASE_A")))
    assert [b.codigo for b in c.bases] == ["BASE_A", "BASE_Z"]
    assert c.bases[0].latitud == Decimal("19.0500000")
    assert c.bases[1].municipios == ("21015", "21114")
    assert c.coberturas == 4
    assert len(c.hash) == 64


@pytest.mark.parametrize(
    ("cambios", "dice"),
    [
        ({"codigo": "base a"}, "codigo"),
        ({"nombre": "  "}, "nombre"),
        ({"lat": 95.0}, "lat esta entre -90 y 90"),
        ({"lon": -181}, "lon esta entre -180 y 180"),
        ({"lat": "19.05"}, "lat es un numero"),
        ({"lon": True}, "lon es un numero"),
        ({"lat": float("nan")}, "lat esta entre"),
        ({"lat": 0, "lon": 0}, "(0, 0)"),
        ({"activa": "si"}, "activa es true o false"),
        ({"municipios": []}, "lista no vacia"),
        ({"municipios": ["2111"]}, "no son CVEGEO"),
        ({"municipios": ["21114", "21114"]}, "repite municipios"),
        ({"capacidad": 10}, "llaves que no se conocen: capacidad"),
    ],
)
def test_cada_problema_de_una_base_rechaza_la_configuracion(cambios, dice):
    with pytest.raises(ConfiguracionInvalida) as error:
        leer(documento(base(**cambios)))
    assert any(dice in p for p in error.value.problemas), error.value.problemas


def test_se_reportan_todos_los_problemas_juntos():
    sin_codigo = base()
    del sin_codigo["codigo"]
    with pytest.raises(ConfiguracionInvalida) as error:
        leer(
            {
                "nombre": "",
                "bases": [base("BASE_A"), base("BASE_A"), sin_codigo, "no", base(lat=200)],
                "extra": 1,
            }
        )
    problemas = " | ".join(error.value.problemas)
    for dice in (
        "llaves que no se conocen: extra",
        "nombre",
        "codigos repetidos: BASE_A",
        "no trae codigo",
        "bases[3] es un objeto",
        "lat esta entre",
    ):
        assert dice in problemas


@pytest.mark.parametrize("malo", [None, [], "x", {"nombre": "x"}, {"nombre": "x", "bases": []}])
def test_un_documento_sin_bases_se_rechaza(malo):
    with pytest.raises(ConfiguracionInvalida):
        leer(malo)


def test_el_jsonl_es_una_base_por_linea_con_el_nombre_aparte():
    import json

    texto = "\n".join(json.dumps(b) for b in (base("BASE_B"), base("BASE_A"))) + "\n\n"
    c = leer_jsonl(texto, "Bases en JSONL")
    assert (c.nombre, [b.codigo for b in c.bases]) == ("Bases en JSONL", ["BASE_A", "BASE_B"])
    with pytest.raises(ConfiguracionInvalida):
        leer_jsonl('{"codigo": ', "x")


# --- la forma canonica y el hash ------------------------------------------------------------------


def test_el_hash_no_depende_del_orden_ni_del_nombre_de_la_version():
    uno = leer(documento(base("BASE_A"), base("BASE_B", municipios=["21015", "21114"])))
    otro = leer(
        documento(
            base("BASE_B", municipios=["21114", "21015"]),
            base("BASE_A"),
            nombre="Otro nombre",
        )
    )
    assert uno.hash == otro.hash
    assert uno.canonica() == otro.canonica()
    assert f'"version":"{VERSION_CONFIGURACION}"' in uno.canonica()


@pytest.mark.parametrize(
    "cambios",
    [
        {"nombre": "Otra base"},
        {"lat": 19.0500001},
        {"lon": -98.21},
        {"activa": False},
        {"municipios": ["21114"]},
    ],
)
def test_el_hash_cambia_con_cualquier_dato_de_una_base(cambios):
    assert leer(documento(base(**cambios))).hash != leer(documento(base())).hash


def test_las_coordenadas_se_redondean_al_centimetro_antes_de_firmar():
    assert leer(documento(base(lat=19.05))).hash == leer(documento(base(lat=19.05000000001))).hash


# --- publicar: versionada, reutilizada, todo o nada e inmutable ----------------------------------

pytestmark_bd = pytest.mark.usefixtures("bd")


def _cuantas(modelo) -> int:
    with sesion() as s:
        return s.exec(select(func.count()).select_from(modelo)).one()


@pytestmark_bd
def test_publicar_crea_la_version_sus_bases_y_su_cobertura():
    c = leer(documento(base("BASE_A"), base("BASE_B", activa=False, municipios=["21015"])))
    with sesion() as s:
        publicada = publicar(s, c)
        s.commit()
        v = publicada.version
        s.refresh(v)
        assert publicada.creada
        assert (v.bases, v.activas, v.coberturas, v.estado) == (2, 1, 3, "PUBLICADA")
        assert v.hash_configuracion == c.hash
        assert v.version_configuracion == VERSION_CONFIGURACION
        # Lo que esta en las tablas vuelve a dar el mismo hash.
        assert configuracion_de(s, v).hash == c.hash
        punto = s.execute(
            text(
                "SELECT ST_X(punto), ST_Y(punto), ST_SRID(punto) FROM base_operativa "
                "WHERE codigo = 'BASE_A'"
            )
        ).one()
    assert tuple(punto) == (-98.2, 19.05, 4326)
    assert (_cuantas(BaseOperativa), _cuantas(CoberturaBaseMunicipio)) == (2, 3)


@pytestmark_bd
def test_el_mismo_contenido_reutiliza_su_version_aunque_cambie_el_nombre():
    with sesion() as s:
        primera = publicar(s, leer(documento(base())))
        s.commit()
        segunda = publicar(s, leer(documento(base(), nombre="Otra etiqueta")))
        s.commit()
        assert (primera.creada, segunda.creada) == (True, False)
        assert primera.version.bases_version_id == segunda.version.bases_version_id
    assert _cuantas(VersionBasesOperativas) == 1


@pytestmark_bd
def test_dos_publicaciones_simultaneas_del_mismo_contenido_dan_una_version():
    c = leer(documento(base()))
    barrera = threading.Barrier(2)
    resultados = []

    def publicar_a_la_vez() -> None:
        with sesion() as s:
            barrera.wait(timeout=30)
            resultados.append(publicar(s, c))
            s.commit()

    hilos = [threading.Thread(target=publicar_a_la_vez) for _ in range(2)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(60)
    assert sorted(r.creada for r in resultados) == [False, True]
    assert _cuantas(VersionBasesOperativas) == 1
    assert _cuantas(BaseOperativa) == 1


@pytestmark_bd
@pytest.mark.parametrize(
    "sentencia",
    [
        "UPDATE version_bases_operativas SET nombre = 'otro'",
        "DELETE FROM version_bases_operativas",
        "UPDATE base_operativa SET activa = false",
        "DELETE FROM base_operativa",
        "UPDATE cobertura_base_municipio SET cvegeo = '99999'",
        "DELETE FROM cobertura_base_municipio",
    ],
)
def test_una_version_publicada_no_se_reescribe(sentencia):
    with sesion() as s:
        publicar(s, leer(documento(base())))
        s.commit()
        with pytest.raises(DBAPIError) as error:
            s.execute(text(sentencia))
        s.rollback()
    assert "no se reescribe" in str(error.value)
    assert _cuantas(BaseOperativa) == 1
