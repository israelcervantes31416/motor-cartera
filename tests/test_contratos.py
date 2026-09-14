from __future__ import annotations

import pytest

from motor_cartera.contratos import ErrorDeContrato, validar


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


# TODO(israel): cuando el generador exista, agrega una prueba basada en propiedades:
# generar 10,000 filas sinteticas y afirmar que el contrato las acepta siempre.
# Esa prueba encuentra los casos que a mano no se te ocurren.
