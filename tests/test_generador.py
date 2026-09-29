from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from motor_cartera.contratos import separar_rechazos, validar
from motor_cartera.contratos.cartera import CANALES, PRODUCTOS
from motor_cartera.generador.sintetico import (
    CANAL_POR_TRAMO,
    ENTIDADES,
    MEDIANA_SALDO,
    PESO_PRODUCTO,
    PESO_TRAMO,
    contaminar,
    generar_archivo,
    generar_cartera,
)
from motor_cartera.ingesta.lectores import leer
from motor_cartera.segmentacion import TRAMOS_ATRASO, tramo_de_atraso

CORTE = date(2026, 9, 30)


@pytest.mark.parametrize("semilla", [1, 31416, 2026])
def test_diez_mil_cuentas_generadas_cumplen_siempre_el_contrato(semilla):
    # Prueba basada en propiedades: con cualquier semilla, el contrato acepta todo lo que
    # sale del generador, tanto con sus tipos como escrito en texto (como lo leeria la
    # ingesta). Encuentra los casos que a mano no se le ocurren a nadie.
    cartera = generar_cartera(10_000, semilla=semilla, fecha_corte=CORTE)

    assert len(validar(cartera)) == 10_000
    assert separar_rechazos(cartera.astype(str)).rechazos == {}


def test_es_reproducible_con_la_misma_semilla():
    una = generar_cartera(500, semilla=7, fecha_corte=CORTE)
    otra = generar_cartera(500, semilla=7, fecha_corte=CORTE)
    distinta = generar_cartera(500, semilla=8, fecha_corte=CORTE)

    pd.testing.assert_frame_equal(una, otra)
    assert not una.equals(distinta)


def test_las_categorias_con_peso_son_las_del_contrato():
    etiquetas = {etiqueta for etiqueta, _, _ in TRAMOS_ATRASO}
    assert set(PESO_PRODUCTO) == set(MEDIANA_SALDO) == set(PRODUCTOS)
    assert set(PESO_TRAMO) == set(CANAL_POR_TRAMO) == etiquetas
    assert all(set(pesos) == set(CANALES) for pesos in CANAL_POR_TRAMO.values())


@pytest.fixture(scope="module")
def cartera() -> pd.DataFrame:
    return generar_cartera(10_000, semilla=31416, fecha_corte=CORTE)


def test_saldos_con_cola_larga(cartera):
    saldo = cartera["saldo_total"]
    assert (saldo > 0).all()
    assert saldo.max() > 10 * saldo.median()


def test_el_atraso_cae_en_todos_los_tramos_con_su_peso(cartera):
    proporcion = cartera["dias_atraso"].map(tramo_de_atraso).value_counts(normalize=True)
    for etiqueta, peso in PESO_TRAMO.items():
        assert proporcion[etiqueta] == pytest.approx(peso, abs=0.02)


def test_el_canal_escala_con_el_atraso(cartera):
    tramo = cartera["dias_atraso"].map(tramo_de_atraso)
    campo = (cartera["canal"] == "CAMPO").groupby(tramo).mean()
    assert campo["0"] < campo["31-60"] < campo["91+"]


def test_la_cartera_se_concentra_como_una_real(cartera):
    assert (cartera["cve_entidad"] == "21").mean() == pytest.approx(0.70, abs=0.02)

    puebla = cartera.loc[cartera["cve_entidad"] == "21", "cve_municipio"].value_counts()
    assert puebla.iloc[0] > 20 * puebla.median()


def test_las_claves_geograficas_existen_en_el_catalogo(cartera):
    for entidad, municipio in cartera[["cve_entidad", "cve_municipio"]].itertuples(index=False):
        assert int(municipio) in ENTIDADES[entidad][0]


def test_contaminar_marca_exactamente_lo_que_el_contrato_rechaza():
    cartera = generar_cartera(2_000, semilla=5, fecha_corte=CORTE)

    sucia, filas = contaminar(cartera, 0.05, semilla=5)

    assert len(filas) == 100
    assert sorted(separar_rechazos(sucia.astype(str)).rechazos) == filas


def test_contaminar_no_toca_la_cartera_original():
    cartera = generar_cartera(200, semilla=5, fecha_corte=CORTE)
    copia = cartera.copy()

    contaminar(cartera, 0.5, semilla=5)

    pd.testing.assert_frame_equal(cartera, copia)


def test_contaminar_rechaza_una_tasa_imposible():
    with pytest.raises(ValueError, match="entre 0 y 1"):
        contaminar(generar_cartera(10, fecha_corte=CORTE), 1.5)


@pytest.mark.parametrize("extension", [".csv", ".xlsx", ".zip"])
def test_cada_formato_se_lee_y_sus_rechazos_caen_en_su_fila(tmp_path, extension):
    ruta = generar_archivo(
        tmp_path / f"cartera{extension}", n=300, tasa_invalidas=0.1, semilla=3, fecha_corte=CORTE
    )
    _, filas = contaminar(generar_cartera(300, semilla=3, fecha_corte=CORTE), 0.1, semilla=3)

    separacion = separar_rechazos(leer(ruta).datos)

    # El encabezado es la fila 1: la posicion p del generador es la fila p + 2 del archivo.
    assert sorted(separacion.rechazos) == [p + 2 for p in filas]
    assert len(separacion.validas) == 270


def test_el_xlsx_y_el_zip_obligan_a_elegir(tmp_path):
    xlsx = leer(generar_archivo(tmp_path / "c.xlsx", n=10, fecha_corte=CORTE))
    paquete = leer(generar_archivo(tmp_path / "c.zip", n=10, fecha_corte=CORTE))

    assert xlsx.origen.startswith("hoja 'cartera' de 'c.xlsx'. Descartado: hoja 'LEEME'")
    assert paquete.origen.startswith("'cartera.csv', dentro de 'c.zip'. Descartado:")
    assert "catalogo_productos.csv" in paquete.origen


def test_formato_de_archivo_no_soportado(tmp_path):
    with pytest.raises(ValueError, match="Formato no soportado"):
        generar_archivo(tmp_path / "cartera.json", n=10)
