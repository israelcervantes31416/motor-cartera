"""features-decision/v1: la forma canonica de cada tipo, NULL distinto de cero, el hash, y que la
lista, la tabla y el ensamble del motor dicen lo mismo."""

from __future__ import annotations

from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlmodel.sql.sqltypes import AutoString

from motor_cartera.db.modelos import FeatureDecisionCuenta
from motor_cartera.decision_v2 import ejecuciones
from motor_cartera.decision_v2.features import (
    FEATURES,
    NULO,
    POR_NOMBRE,
    VERSION_FEATURES,
    Familia,
    Tipo,
    ValorInvalido,
    feature_hash,
    forma_canonica,
    texto,
)


def _valores(**cambios) -> dict:
    valores = {
        f.nombre: None
        if f.nula
        else {
            Tipo.ENTERO: 0,
            Tipo.IMPORTE: Decimal("0"),
            Tipo.TEXTO: "CONSUMO",
            Tipo.BOOLEANO: False,
        }[f.tipo]
        for f in FEATURES
    }
    valores.update(cambios)
    return valores


@pytest.mark.parametrize(
    ("nombre", "valor", "esperado"),
    [
        ("dias_atraso", 45, "45"),
        ("delta_dias_atraso_desde_corte_anterior", -7, "-7"),
        ("saldo_total", Decimal("10045"), "10045.00"),
        ("delta_saldo_total_desde_corte_anterior", Decimal("-1007.5"), "-1007.50"),
        ("tasa_contacto_titular_30d", Decimal("0.5"), "0.5000"),
        ("tasa_contacto_titular_30d", Decimal("1"), "1.0000"),
        ("dias_gestion_a_pago_mediana_90d", Decimal("2"), "2.00"),
        ("promesa_vigente", True, "true"),
        ("continuo_desde_corte_anterior", False, "false"),
        ("producto", "NOMINA", "s6:NOMINA"),
        ("estrategia", "AÑO 1: ñ", "s8:AÑO 1: ñ"),  # el largo es en caracteres, como length()
        ("estrategia", "", "s0:"),
        ("semanas_atraso", None, NULO),
    ],
)
def test_la_forma_canonica_de_cada_tipo(nombre, valor, esperado):
    assert texto(POR_NOMBRE[nombre], valor) == esperado


@pytest.mark.parametrize(
    ("nombre", "valor"),
    [
        ("dias_atraso", None),  # no admite NULL
        ("dias_atraso", True),  # un bool no es un entero
        ("dias_atraso", 4.0),
        ("saldo_total", 10.5),  # un float no es un importe
        ("saldo_total", Decimal("NaN")),
        ("promesa_vigente", 1),
        ("producto", 7),
    ],
)
def test_un_valor_que_no_es_de_su_tipo_no_tiene_forma_canonica(nombre, valor):
    with pytest.raises(ValorInvalido):
        texto(POR_NOMBRE[nombre], valor)


def test_null_y_cero_no_se_confunden():
    con_cero = _valores(dias_desde_ultimo_pago=0, recuperacion_interpretada_7d=Decimal("0"))
    sin_dato = _valores(dias_desde_ultimo_pago=None, recuperacion_interpretada_7d=None)

    assert feature_hash(con_cero) != feature_hash(sin_dato)
    assert "recuperacion_interpretada_7d=0.00" in forma_canonica(con_cero)
    assert f"recuperacion_interpretada_7d={NULO}" in forma_canonica(sin_dato)


def test_el_texto_con_su_largo_no_se_confunde_con_otro_campo():
    # Sin el largo, "A" + "\nB" y "A\n" + "B" se escribirian igual.
    uno = _valores(estrategia="A", estatus_plan="B=C")
    otro = _valores(estrategia="A\nestatus_plan=B", estatus_plan="C")

    assert feature_hash(uno) != feature_hash(otro)


def test_la_forma_canonica_lleva_la_version_y_cada_feature_en_su_orden():
    lineas = forma_canonica(_valores()).split("\n")

    assert lineas[0] == VERSION_FEATURES == "features-decision/v1"
    assert [linea.split("=", 1)[0] for linea in lineas[1:]] == [f.nombre for f in FEATURES]


def test_faltar_una_feature_es_un_error():
    valores = _valores()
    del valores["visitas_30d"]

    with pytest.raises(ValorInvalido, match="visitas_30d"):
        forma_canonica(valores)


def test_cada_cambio_cambia_el_hash_y_el_orden_del_diccionario_no():
    base = _valores()
    assert feature_hash(base) == feature_hash(dict(reversed(list(base.items()))))
    for f in FEATURES:
        cambio = {
            Tipo.ENTERO: 1,
            Tipo.IMPORTE: Decimal("1"),
            Tipo.TASA: Decimal("0.5"),
            Tipo.DIAS: Decimal("1"),
            Tipo.TEXTO: "OTRO",
            Tipo.BOOLEANO: True,
        }[f.tipo]
        assert feature_hash({**base, f.nombre: cambio}) != feature_hash(base), f.nombre


def test_la_lista_tiene_cada_familia_y_ningun_nombre_repetido():
    nombres = [f.nombre for f in FEATURES]
    assert len(nombres) == len(set(nombres)) == 43
    assert {f.familia for f in FEATURES} == set(Familia)
    # Lo que la mision pide como minimo, y nada de la PII del corte.
    for minimo in (
        "saldo_total",
        "saldo_atrasado",
        "saldo_requerido",
        "moratorios",
        "dias_atraso",
        "semanas_atraso",
        "producto",
        "estrategia",
        "estatus_plan",
        "cortes_observados",
        "reingresos_observados",
        "continuo_desde_corte_anterior",
        "tasa_contacto_titular_30d",
        "promesa_vigente",
        "convenio_vigente",
        "recuperacion_interpretada_90d",
        "pagos_asociados_unicos_30d",
    ):
        assert minimo in POR_NOMBRE


TIPO_DE_COLUMNA = {
    Tipo.ENTERO: (sa.Integer, sa.BigInteger),
    Tipo.IMPORTE: (sa.Numeric,),
    Tipo.TASA: (sa.Numeric,),
    Tipo.DIAS: (sa.Numeric,),
    Tipo.TEXTO: (sa.String, AutoString),
    Tipo.BOOLEANO: (sa.Boolean,),
}
ESCALA = {Tipo.IMPORTE: 2, Tipo.TASA: 4, Tipo.DIAS: 2}


def test_la_tabla_guarda_cada_feature_con_su_tipo_y_su_nulabilidad():
    columnas = FeatureDecisionCuenta.__table__.c
    tecnicas = {
        "ejecucion_decision_v2_id",
        "cuenta_canonica_id",
        "corte_canonico_id",
        "feature_hash",
    }

    assert {c.name for c in columnas} - tecnicas == set(POR_NOMBRE)
    for f in FEATURES:
        columna = columnas[f.nombre]
        assert isinstance(columna.type, TIPO_DE_COLUMNA[f.tipo]), f.nombre
        assert columna.nullable is f.nula, f.nombre
        if f.tipo in ESCALA:
            assert columna.type.scale == ESCALA[f.tipo], f.nombre


def test_el_ensamble_del_motor_calcula_cada_feature_y_ninguna_mas():
    assert set(ejecuciones.EXPRESION) == set(POR_NOMBRE)
    assert set(ejecuciones.TIPO_SQL) <= set(POR_NOMBRE)
    for f in FEATURES:
        tipo = ejecuciones.TIPO_SQL.get(f.nombre, "integer")
        if f.tipo in ESCALA:
            assert tipo.replace(" ", "").endswith(f",{ESCALA[f.tipo]})"), f.nombre
        if f.tipo == Tipo.BOOLEANO:
            assert tipo == "boolean", f.nombre
