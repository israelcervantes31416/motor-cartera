from __future__ import annotations

import hashlib
from datetime import date

import pandas as pd
import pytest

from motor_cartera.contratos import (
    ErrorDeContrato,
    Motivo,
    fechas_de_corte,
    firmar_contenido,
    separar_rechazos,
    validar,
)
from motor_cartera.contratos.cartera import SALDO_MAXIMO
from motor_cartera.generador.sintetico import generar_archivo
from motor_cartera.ingesta.lectores import leer

CORTE = date(2026, 9, 30)


def test_cartera_valida_pasa(cartera_valida):
    resultado = validar(cartera_valida)
    assert len(resultado) == 3


def test_saldo_negativo_detiene_el_proceso(cartera_valida):
    cartera_valida.loc[0, "saldo_total"] = -1.0
    with pytest.raises(ErrorDeContrato):
        validar(cartera_valida)


def test_cliente_duplicado_detiene_el_proceso(cartera_valida):
    cartera_valida.loc[1, "cliente_unico"] = cartera_valida.loc[0, "cliente_unico"]
    with pytest.raises(ErrorDeContrato):
        validar(cartera_valida)


def test_el_error_reporta_todas_las_fallas_no_solo_la_primera(cartera_valida):
    cartera_valida.loc[0, "saldo_total"] = -1.0
    cartera_valida.loc[1, "dias_atraso"] = -5
    cartera_valida.loc[2, "producto"] = "INEXISTENTE"
    with pytest.raises(ErrorDeContrato) as exc:
        validar(cartera_valida)
    assert exc.value.fallas is not None
    assert len(exc.value.fallas) >= 3


def test_columnas_extra_se_descartan_sin_romper(cartera_valida):
    cartera_valida["columna_que_nadie_pidio"] = "x"
    resultado = validar(cartera_valida)
    assert "columna_que_nadie_pidio" not in resultado.columns


def test_fecha_ambigua_se_rechaza_en_vez_de_adivinarse(cartera_valida):
    # "01/02/2026" podria ser 2 de enero o 1 de febrero. El contrato no elige.
    cartera_valida["fecha_corte"] = ["01/02/2026", "2026-01-31", "2026-01-31"]
    with pytest.raises(ErrorDeContrato):
        validar(cartera_valida)


def test_fecha_con_hora_como_la_entrega_excel_se_acepta(cartera_valida):
    cartera_valida["fecha_corte"] = ["2026-01-31 00:00:00"] * 3
    resultado = validar(cartera_valida)
    assert (resultado["fecha_corte"] == pd.Timestamp("2026-01-31")).all()


def test_saldo_que_no_cabe_en_la_base_se_rechaza(cartera_valida):
    cartera_valida.loc[0, "saldo_total"] = float(SALDO_MAXIMO)
    with pytest.raises(ErrorDeContrato):
        validar(cartera_valida)


# --- separar_rechazos: el juicio registro por registro ---------------------------------
#
# Los lectores entregan todo como texto; el contrato convierte. Por eso estas pruebas
# parten de `cartera_valida.astype(str)`.


def test_separar_sin_rechazos_devuelve_todo_convertido(cartera_valida):
    separacion = separar_rechazos(cartera_valida.astype(str))

    assert separacion.rechazos == {}
    assert len(separacion.validas) == 3
    assert separacion.validas["saldo_total"].dtype == "float64"
    assert separacion.validas["dias_atraso"].dtype == "int64"
    assert separacion.validas["fecha_corte"].dtype.kind == "M"


def test_un_saldo_no_numerico_no_esconde_un_saldo_negativo(cartera_valida):
    # Validando todo de una pasada, el "N/D" hace que pandera deje de evaluar
    # saldo >= 0 fila por fila, y el -1 se colaria como valido.
    texto = cartera_valida.astype(str)
    texto.loc[0, "saldo_total"] = "N/D"
    texto.loc[1, "saldo_total"] = "-1"

    separacion = separar_rechazos(texto)

    assert set(separacion.rechazos) == {0, 1}
    assert separacion.rechazos[1] == [Motivo("saldo_total", "greater_than_or_equal_to(0)")]
    assert separacion.validas.index.tolist() == [2]


def test_cada_rechazo_lleva_todos_sus_motivos(cartera_valida):
    texto = cartera_valida.astype(str)
    texto.loc[2, ["producto", "canal", "cve_municipio"]] = ["HIPOTECA", "CARTA", "5"]

    separacion = separar_rechazos(texto)

    campos = [m.campo for m in separacion.rechazos[2]]
    assert campos == ["producto", "canal", "cve_municipio"]


def test_un_duplicado_rechaza_a_todas_sus_copias(cartera_valida):
    # No hay forma de saber cual de las dos filas es la buena: se rechazan ambas.
    texto = cartera_valida.astype(str)
    texto.loc[1, "cliente_unico"] = texto.loc[0, "cliente_unico"]

    separacion = separar_rechazos(texto)

    assert set(separacion.rechazos) == {0, 1}
    assert separacion.rechazos[0] == [Motivo("cliente_unico", "field_uniqueness")]
    assert separacion.validas.index.tolist() == [2]


def test_un_valor_que_no_se_convierte_cuenta_como_un_solo_motivo(cartera_valida):
    texto = cartera_valida.astype(str)
    texto.loc[0, "dias_atraso"] = "abc"

    separacion = separar_rechazos(texto)

    assert separacion.rechazos[0] == [Motivo("dias_atraso", "coerce_dtype('int64')")]


def test_una_fecha_que_no_es_iso_se_rechaza_con_el_motivo_de_conversion(cartera_valida):
    texto = cartera_valida.astype(str)
    texto.loc[1, "fecha_corte"] = "31/01/2026"

    separacion = separar_rechazos(texto)

    assert separacion.rechazos == {1: [Motivo("fecha_corte", "coerce_dtype('datetime64[ns]')")]}
    assert separacion.validas.index.tolist() == [0, 2]


def test_un_vacio_se_rechaza(cartera_valida):
    texto = cartera_valida.astype(str)
    texto.loc[1, "saldo_total"] = None

    separacion = separar_rechazos(texto)

    assert separacion.rechazos[1] == [Motivo("saldo_total", "not_nullable")]


def test_los_rechazos_y_las_validas_conservan_la_etiqueta_de_fila(cartera_valida):
    # La etiqueta es lo que despues se reporta como numero de fila del archivo.
    texto = cartera_valida.astype(str).set_axis([10, 11, 12])
    texto.loc[11, "producto"] = "HIPOTECA"

    separacion = separar_rechazos(texto)

    assert list(separacion.rechazos) == [11]
    assert separacion.validas.index.tolist() == [10, 12]


def test_una_columna_faltante_no_es_un_rechazo_por_fila(cartera_valida):
    with pytest.raises(ErrorDeContrato, match="canal"):
        separar_rechazos(cartera_valida.astype(str).drop(columns=["canal"]))


# --- una cartera, un corte ------------------------------------------------------------------


def test_fechas_de_corte_cuenta_los_registros_validos_de_cada_corte(cartera_valida):
    texto = cartera_valida.astype(str)
    texto.loc[2, "fecha_corte"] = "2026-01-30"

    cortes = fechas_de_corte(separar_rechazos(texto).validas)

    assert cortes == {date(2026, 1, 30): 1, date(2026, 1, 31): 2}


def test_un_registro_rechazado_no_cuenta_como_otro_corte(cartera_valida):
    # Lo rechazado no se publica: su fecha no parte la cartera en dos.
    texto = cartera_valida.astype(str)
    texto.loc[2, ["fecha_corte", "saldo_total"]] = ["2026-01-30", "-1"]

    assert fechas_de_corte(separar_rechazos(texto).validas) == {date(2026, 1, 31): 2}


def test_la_hora_no_parte_un_corte(cartera_valida):
    # Un corte es un dia del calendario, traiga o no una hora.
    texto = cartera_valida.astype(str)
    texto.loc[0, "fecha_corte"] = "2026-01-31 10:30:00"

    assert fechas_de_corte(separar_rechazos(texto).validas) == {date(2026, 1, 31): 3}


# --- la firma del contenido ---------------------------------------------------------------


@pytest.mark.parametrize("tasa", [0.0, 0.05])
def test_la_misma_cartera_en_csv_xlsx_y_zip_tiene_la_misma_firma_de_contenido(tmp_path, tasa):
    # Tres archivos distintos, cada uno con su firma, y una sola cartera. Con rechazos
    # tambien: se firman los registros validos, y son los mismos en los tres formatos.
    firmas = set()
    for formato in (".csv", ".xlsx", ".zip"):
        ruta = generar_archivo(
            tmp_path / f"cartera{formato}", n=300, tasa_invalidas=tasa, semilla=7, fecha_corte=CORTE
        )
        firmas.add(firmar_contenido(separar_rechazos(leer(ruta).datos).validas))

    assert len(firmas) == 1
    assert len(firmas.pop()) == 64


def test_la_firma_de_contenido_no_depende_del_orden_de_filas_ni_de_columnas(cartera_valida):
    validas = separar_rechazos(cartera_valida.astype(str)).validas
    revuelta = validas.iloc[::-1][list(reversed(validas.columns))]

    assert firmar_contenido(revuelta) == firmar_contenido(validas)


def test_un_valor_distinto_es_otra_cartera(cartera_valida):
    validas = separar_rechazos(cartera_valida.astype(str)).validas
    otra = validas.copy()
    otra.loc[otra.index[0], "saldo_total"] += 0.01

    assert firmar_contenido(otra) != firmar_contenido(validas)


def test_el_mismo_valor_escrito_de_otra_forma_es_la_misma_cartera(cartera_valida):
    # Se firma lo que el contrato convirtio: 1500.5 y 1500.5000 son el mismo saldo, y la
    # fecha con hora que entrega Excel es el mismo dia.
    texto = cartera_valida.astype(str)
    otra = texto.copy()
    otra["saldo_total"] = ["1500.5000", "23000", "780.25"]
    otra["fecha_corte"] = ["2026-01-31 00:00:00"] * 3

    assert firmar_contenido(separar_rechazos(otra).validas) == firmar_contenido(
        separar_rechazos(texto).validas
    )


def test_un_saldo_en_cero_es_el_mismo_con_signo_o_sin_el(cartera_valida):
    texto = cartera_valida.astype(str)
    texto.loc[0, "saldo_total"] = "0"
    con_signo = texto.copy()
    con_signo.loc[0, "saldo_total"] = "-0.00"

    assert firmar_contenido(separar_rechazos(con_signo).validas) == firmar_contenido(
        separar_rechazos(texto).validas
    )


def test_la_forma_canonica_es_la_que_documenta_el_contrato(cartera_valida):
    # Fija el formato exacto: si cambia, cambian todas las firmas, y eso es otra version.
    esperado = (
        '["canal","cliente_unico","cve_entidad","cve_municipio","dias_atraso",'
        '"fecha_corte","producto","saldo_total"]\n'
        '["CAMPO","CU00000002","21","156","45","2026-01-31","TARJETA","23000.00"]\n'
        '["DIGITAL","CU00000003","09","005","190","2026-01-31","NOMINA","780.25"]\n'
        '["TELEFONICA","CU00000001","21","114","0","2026-01-31","CONSUMO","1500.50"]\n'
    )
    validas = separar_rechazos(cartera_valida.astype(str)).validas

    assert firmar_contenido(validas) == hashlib.sha256(esperado.encode("utf-8")).hexdigest()


# La prueba basada en propiedades (10,000 filas sinteticas, el contrato las acepta siempre)
# vive en test_generador.py, junto al generador que la hace posible.
