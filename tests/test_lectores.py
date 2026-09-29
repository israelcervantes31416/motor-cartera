from __future__ import annotations

import io
import zipfile

import pandas as pd
import pytest

from motor_cartera.ingesta.lectores import (
    ALIAS,
    REQUERIDAS,
    ErrorDeLectura,
    leer,
    leer_contenido,
    mapear_columnas,
    normalizar,
)

ENCABEZADO = (
    "cliente_unico,saldo_total,dias_atraso,producto,canal,cve_entidad,cve_municipio,fecha_corte"
)
FILA = "CU00000001,1500.50,45,CONSUMO,CAMPO,21,114,2026-01-31"


def _csv(*lineas: str) -> bytes:
    return "\n".join(lineas).encode("utf-8")


def _xlsx(**hojas: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as escritor:
        for nombre, df in hojas.items():
            df.to_excel(escritor, sheet_name=nombre, index=False)
    return buf.getvalue()


def _zip(**miembros: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as paquete:
        for nombre, contenido in miembros.items():
            paquete.writestr(nombre, contenido)
    return buf.getvalue()


@pytest.fixture
def cartera_texto(cartera_valida) -> pd.DataFrame:
    return cartera_valida.astype(str)


# --- encabezados ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("crudo", "esperado"),
    [
        ("  Días de   Atraso ", "dias de atraso"),
        ("SALDO_TOTAL", "saldo total"),
        ("Cliente Único", "cliente unico"),
        (2026, "2026"),
    ],
)
def test_normalizar(crudo, esperado):
    assert normalizar(crudo) == esperado


def test_hay_alias_para_cada_columna_del_contrato():
    assert set(ALIAS) == set(REQUERIDAS)


def test_mapea_encabezados_escritos_a_mano():
    df = pd.DataFrame(
        columns=[
            "Cliente Único",
            "Saldo",
            "Días de Atraso",
            "Tipo Producto",
            "Canal Gestión",
            "Estado",
            "Municipio",
            " Fecha  Corte ",
            "Comentarios",
        ]
    )

    mapeo = mapear_columnas(df)

    assert sorted(mapeo.values()) == sorted(REQUERIDAS)
    assert "Comentarios" not in mapeo


def test_columna_faltante_dice_cual_falta_y_que_si_venia():
    df = pd.DataFrame(columns=[c for c in REQUERIDAS if c != "canal"])
    with pytest.raises(ErrorDeLectura) as exc:
        mapear_columnas(df)
    assert "Faltan columnas requeridas: canal." in str(exc.value)
    assert "Encabezados recibidos: cliente_unico" in str(exc.value)


def test_dos_columnas_que_dicen_ser_la_misma_no_se_resuelven_a_ciegas():
    df = pd.DataFrame(columns=[*REQUERIDAS, "Saldo"])
    with pytest.raises(ErrorDeLectura, match="saldo_total: saldo_total, Saldo"):
        mapear_columnas(df)


# --- csv --------------------------------------------------------------------------------


def test_csv_con_nombres_canonicos_y_numeros_de_fila_del_archivo():
    otra = "CU00000002,80.00,0,TARJETA,DIGITAL,09,005,2026-01-31"
    # La linea en blanco cuenta como fila: el segundo registro esta en la fila 4.
    lectura = leer_contenido(_csv(ENCABEZADO, FILA, "", otra), "c.csv")

    assert list(lectura.datos.columns) == list(REQUERIDAS)
    assert lectura.datos.index.tolist() == [2, 4]
    assert lectura.datos.loc[4, "cve_municipio"] == "005"  # texto: los ceros no se pierden
    assert lectura.origen == "'c.csv'"


def test_csv_quita_espacios_y_una_celda_solo_con_espacios_es_vacia():
    fila = "CU00000001,1500.50,45,   ,  CAMPO  ,21,114,2026-01-31"

    lectura = leer_contenido(_csv(ENCABEZADO, fila), "c.csv")

    assert lectura.datos.loc[2, "canal"] == "CAMPO"
    assert pd.isna(lectura.datos.loc[2, "producto"])


def test_csv_en_cp1252_se_lee_y_queda_registrado():
    contenido = (ENCABEZADO + ",comentario\n" + FILA + ",Pagó en ventanilla\n").encode("cp1252")

    lectura = leer_contenido(contenido, "c.csv")

    assert "cp1252" in lectura.origen
    assert len(lectura.datos) == 1


def test_csv_sin_registros_es_un_error_de_lectura():
    with pytest.raises(ErrorDeLectura, match="no trae ningun registro"):
        leer_contenido(_csv(ENCABEZADO, "", ","), "c.csv")


# --- xlsx -------------------------------------------------------------------------------


def test_xlsx_elige_la_hoja_util_y_dice_por_que(cartera_texto):
    contenido = _xlsx(LEEME=pd.DataFrame({"Nota": ["Datos sinteticos"]}), cartera=cartera_texto)

    lectura = leer_contenido(contenido, "cartera.xlsx")

    assert len(lectura.datos) == 3
    assert lectura.origen.startswith("hoja 'cartera' de 'cartera.xlsx'. Descartado: hoja 'LEEME'")
    assert lectura.datos.index.tolist() == [2, 3, 4]


def test_xlsx_sin_hoja_util_explica_cada_hoja():
    contenido = _xlsx(LEEME=pd.DataFrame({"Nota": ["x"]}), Otra=pd.DataFrame({"a": [1]}))
    with pytest.raises(ErrorDeLectura, match="No hay hojas con forma de cartera") as exc:
        leer_contenido(contenido, "c.xlsx")
    assert "hoja 'LEEME'" in str(exc.value) and "hoja 'Otra'" in str(exc.value)


def test_xlsx_con_dos_hojas_utiles_no_elige_a_ciegas(cartera_texto):
    contenido = _xlsx(enero=cartera_texto, febrero=cartera_texto)
    with pytest.raises(ErrorDeLectura, match="no se elige una a ciegas"):
        leer_contenido(contenido, "c.xlsx")


def test_xlsx_corrupto_es_un_error_de_lectura():
    with pytest.raises(ErrorDeLectura, match="no es un Excel legible"):
        leer_contenido(b"esto no es un excel", "c.xlsx")


# --- zip --------------------------------------------------------------------------------


def test_zip_elige_el_archivo_util_y_registra_lo_descartado():
    contenido = _zip(
        **{
            "LEEME.txt": b"Cartera sintetica",
            "catalogo_productos.csv": b"producto,descripcion\nCONSUMO,Credito personal\n",
            "__MACOSX/._cartera.csv": b"ruido",
            "cartera.csv": _csv(ENCABEZADO, FILA),
        }
    )

    lectura = leer_contenido(contenido, "paquete.zip")

    assert len(lectura.datos) == 1
    assert lectura.origen.startswith("'cartera.csv', dentro de 'paquete.zip'. Descartado:")
    assert "'LEEME.txt': formato no soportado" in lectura.origen
    assert "'catalogo_productos.csv': Faltan columnas requeridas" in lectura.origen
    assert "__MACOSX" not in lectura.origen


def test_zip_con_un_excel_adentro(cartera_texto):
    contenido = _zip(**{"cartera.xlsx": _xlsx(cartera=cartera_texto)})

    lectura = leer_contenido(contenido, "paquete.zip")

    assert lectura.origen == "hoja 'cartera' de 'cartera.xlsx', dentro de 'paquete.zip'"


def test_zip_sin_archivo_util():
    with pytest.raises(ErrorDeLectura, match="No hay archivos con forma de cartera"):
        leer_contenido(_zip(**{"LEEME.txt": b"x"}), "p.zip")


def test_zip_con_dos_archivos_utiles_no_elige_a_ciegas():
    carteras = {"a.csv": _csv(ENCABEZADO, FILA), "b.csv": _csv(ENCABEZADO, FILA)}
    with pytest.raises(ErrorDeLectura, match="Hay 2 archivos"):
        leer_contenido(_zip(**carteras), "p.zip")


def test_zip_corrupto_es_un_error_de_lectura():
    with pytest.raises(ErrorDeLectura, match="no es un zip legible"):
        leer_contenido(b"PK no tanto", "p.zip")


# --- entrada ----------------------------------------------------------------------------


def test_formato_no_soportado():
    with pytest.raises(ErrorDeLectura, match="Formato no soportado: 'cartera.pdf'"):
        leer_contenido(b"%PDF", "cartera.pdf")


def test_archivo_vacio():
    with pytest.raises(ErrorDeLectura, match="esta vacio"):
        leer_contenido(b"", "c.csv")


def test_leer_desde_disco(tmp_path):
    ruta = tmp_path / "cartera.csv"
    ruta.write_bytes(_csv(ENCABEZADO, FILA))

    assert len(leer(ruta).datos) == 1
