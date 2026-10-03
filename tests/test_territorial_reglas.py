"""territorial/v1 sin base de datos: la carga exacta en cada frontera, el orden de campo y lo que
no cambia.

Los casos golden afirman el resultado completo, motivo incluido, en la forma en que se va a
guardar. Si una regla cambia, cambian estos resultados: eso es una version nueva de las reglas, no
una prueba que corregir.

El motor territorial no cambia ninguna decision individual: recibe un agregado por territorio, ya
calculado, y solo lo clasifica y lo ordena. Por eso aqui no hay cuentas, decisiones ni PostgreSQL.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import FrozenInstanceError, asdict, fields, replace
from decimal import ROUND_DOWN, Decimal, Inexact, Rounded, localcontext
from itertools import permutations
from pathlib import Path

import pytest

from motor_cartera.contratos import VERSION_CONTRATO
from motor_cartera.decision.reglas import VERSION_REGLAS_DECISION, CodigoMotivo
from motor_cartera.territorial import reglas
from motor_cartera.territorial.reglas import (
    MOTIVO_POR_CARGA,
    UMBRAL_CARGA_ALTA,
    UMBRAL_CARGA_MEDIA,
    VERSION_REGLAS_TERRITORIAL,
    CargaTerritorial,
    CodigoMotivoTerritorial,
    EntradaTerritorio,
    MotivoTerritorial,
    ResultadoTerritorio,
    evaluar_territorio,
    priorizar_territorios,
)


def _entrada(
    clave: str, cuentas_total: int, saldo_total: str, cuentas_campo: int, saldo_campo: str
) -> EntradaTerritorio:
    """Una entrada a partir de la clave de territorio completa, con los saldos en texto."""
    return EntradaTerritorio(
        cve_entidad=clave[:2],
        cve_municipio=clave[2:],
        cuentas_total=cuentas_total,
        saldo_total=Decimal(saldo_total),
        cuentas_campo=cuentas_campo,
        saldo_campo=Decimal(saldo_campo),
    )


def _claves(resultados: Iterable[ResultadoTerritorio]) -> list[str]:
    return [resultado.clave_territorio for resultado in resultados]


# --- golden: la carga exacta en cada frontera -------------------------------------------------
#
# Cada frontera de los umbrales (0, 1, 4, 5, 19, 20), los ejemplos de la especificacion (3, 8, 35)
# y una cantidad muy superior a 20: ALTA no tiene tope.

CARGAS = {
    # caso: (cuentas_campo, carga, motivo como (codigo, campo, valor))
    "T01": (0, "SIN_CARGA", ("SIN_CARGA_CAMPO", "cuentas_campo", "0")),
    "T02": (1, "BAJA", ("CARGA_CAMPO_1_4", "cuentas_campo", "1")),
    "T03": (3, "BAJA", ("CARGA_CAMPO_1_4", "cuentas_campo", "3")),
    "T04": (4, "BAJA", ("CARGA_CAMPO_1_4", "cuentas_campo", "4")),
    "T05": (5, "MEDIA", ("CARGA_CAMPO_5_19", "cuentas_campo", "5")),  # el umbral es inclusivo
    "T06": (8, "MEDIA", ("CARGA_CAMPO_5_19", "cuentas_campo", "8")),
    "T07": (19, "MEDIA", ("CARGA_CAMPO_5_19", "cuentas_campo", "19")),
    "T08": (20, "ALTA", ("CARGA_CAMPO_20_MAS", "cuentas_campo", "20")),  # el umbral es inclusivo
    "T09": (35, "ALTA", ("CARGA_CAMPO_20_MAS", "cuentas_campo", "35")),
    "T10": (250000, "ALTA", ("CARGA_CAMPO_20_MAS", "cuentas_campo", "250000")),
}


def _con_campo(cuentas_campo: int) -> EntradaTerritorio:
    """El mismo territorio con `cuentas_campo` cuentas de campo y todo lo demas fijo."""
    saldo_campo = "0.00" if cuentas_campo == 0 else "1500000.00"
    return _entrada("21114", 300000, "9000000000.00", cuentas_campo, saldo_campo)


@pytest.mark.parametrize(
    ("cuentas_campo", "carga", "motivo"),
    [pytest.param(*valores, id=caso) for caso, valores in CARGAS.items()],
)
def test_cada_frontera_da_su_carga_exacta(cuentas_campo, carga, motivo):
    entrada = _con_campo(cuentas_campo)
    codigo, campo, valor = motivo

    assert evaluar_territorio(entrada) == ResultadoTerritorio(
        clave_territorio="21114",
        cve_entidad="21",
        cve_municipio="114",
        cuentas_total=entrada.cuentas_total,
        saldo_total=entrada.saldo_total,
        cuentas_campo=cuentas_campo,
        saldo_campo=entrada.saldo_campo,
        carga=CargaTerritorial(carga),
        posicion_campo=None,
        motivos=(MotivoTerritorial(CodigoMotivoTerritorial(codigo), campo, valor),),
    )


def test_el_ejemplo_de_la_especificacion_sale_exacto():
    resultado = evaluar_territorio(_entrada("09002", 12, "84000.50", 8, "61000.25"))

    assert (resultado.clave_territorio, resultado.carga, resultado.posicion_campo) == (
        "09002",
        "MEDIA",
        None,
    )
    # Tal como se va a guardar: una lista de objetos con codigo, campo y valor.
    assert [asdict(motivo) for motivo in resultado.motivos] == [
        {"codigo": "CARGA_CAMPO_5_19", "campo": "cuentas_campo", "valor": "8"}
    ]


@pytest.mark.parametrize("cuentas_campo", [1, 4, 5, 19, 20])
def test_ni_el_saldo_ni_el_total_cambian_la_carga(cuentas_campo):
    # Del saldo de campo mas chico al mas grande, y con o sin mas cuentas que las de campo: la
    # misma carga y el mismo motivo. El saldo no sube ni baja el nivel, y la carga no es una
    # proporcion del total.
    esperado = evaluar_territorio(_con_campo(cuentas_campo))
    variantes = [
        _entrada("21114", cuentas_campo, "0.00", cuentas_campo, "0.00"),
        _entrada("21114", cuentas_campo, "999999999999.99", cuentas_campo, "999999999999.99"),
        _entrada("21114", 250000, "999999999999.99", cuentas_campo, "0.01"),
        _entrada("21114", 250000, "4999999999999.99", cuentas_campo, "4999999999999.99"),
    ]

    for variante in variantes:
        resultado = evaluar_territorio(variante)
        assert (resultado.carga, resultado.motivos) == (esperado.carga, esperado.motivos)


# --- golden: el orden de campo ----------------------------------------------------------------
#
# Una coleccion con las cuatro cargas y un empate en cada criterio. Los totales van a contrapelo
# del orden esperado: si alguno contara, el orden cambiaria.

TERRITORIOS = {
    # clave: (cuentas_total, saldo_total, cuentas_campo, saldo_campo)
    "21114": (300, "4100000.00", 25, "900000.00"),
    "21156": (26, "1250000.00", 25, "1200000.00"),
    "09005": (12, "310000.00", 8, "300000.00"),
    "15033": (90, "2000000.00", 8, "300000.00"),
    "30087": (500, "5000000000.00", 3, "999999999.99"),
    "21001": (40, "2000000.00", 0, "0.00"),
    "09002": (1, "10.00", 0, "0.00"),
}

ENTRADAS = tuple(_entrada(clave, *agregados) for clave, agregados in TERRITORIOS.items())

ORDEN_DE_CAMPO = [
    # (clave, carga, posicion_campo)
    ("21156", "ALTA", 1),  # empata en cuentas de campo con 21114, y tiene mas saldo de campo
    ("21114", "ALTA", 2),  # mas cuentas en total, que no cuentan para el orden
    ("09005", "MEDIA", 3),  # empata en cuentas y saldo de campo con 15033: va la clave menor
    ("15033", "MEDIA", 4),
    ("30087", "BAJA", 5),  # el mayor saldo de campo, pero menos cuentas de campo que los demas
    ("09002", "SIN_CARGA", None),  # al final y por clave, aunque 21001 tenga mas cuentas
    ("21001", "SIN_CARGA", None),
]


def test_el_orden_de_una_coleccion_sale_exacto():
    resultado = priorizar_territorios(ENTRADAS)

    assert [(r.clave_territorio, r.carga, r.posicion_campo) for r in resultado] == ORDEN_DE_CAMPO


def test_mas_cuentas_de_campo_van_antes_que_mas_saldo():
    # El ejemplo de la especificacion: el saldo nunca le gana a las cuentas como primer criterio.
    a = _entrada("21001", 10, "100.00", 10, "100.00")
    b = _entrada("09002", 9, "999999999.00", 9, "999999999.00")

    assert _claves(priorizar_territorios([b, a])) == ["21001", "09002"]


def test_a_iguales_cuentas_de_campo_va_antes_el_mayor_saldo():
    # Por un centavo, y aunque su clave sea la mayor.
    menor = _entrada("09002", 12, "5000.00", 7, "5000.00")
    mayor = _entrada("21001", 7, "5000.01", 7, "5000.01")

    assert _claves(priorizar_territorios([menor, mayor])) == ["21001", "09002"]


@pytest.mark.parametrize(
    ("saldo_09002", "saldo_21001"),
    [
        pytest.param("5000.00", "5000.00", id="mismo-saldo"),
        # El saldo se compara por su valor, no por como se escribe.
        pytest.param("5000.0", "5000.00", id="mismo-valor-con-otra-escala"),
    ],
)
def test_a_iguales_cuentas_y_saldo_de_campo_va_antes_la_clave_menor(saldo_09002, saldo_21001):
    a = _entrada("21001", 7, saldo_21001, 7, saldo_21001)
    b = _entrada("09002", 7, saldo_09002, 7, saldo_09002)

    assert _claves(priorizar_territorios([a, b])) == ["09002", "21001"]


def test_las_posiciones_van_desde_1_sin_huecos_y_solo_con_campo():
    resultado = priorizar_territorios(ENTRADAS)
    con_campo = [r for r in resultado if r.cuentas_campo > 0]

    assert [r.posicion_campo for r in con_campo] == list(range(1, len(con_campo) + 1))
    assert all(type(r.posicion_campo) is int for r in con_campo)
    # None, nunca 0: un territorio SIN_CARGA no tiene lugar en la gestion de campo.
    assert [r.posicion_campo for r in resultado if r.cuentas_campo == 0] == [None, None]


def test_sin_cuentas_de_campo_ningun_territorio_tiene_posicion():
    resultado = priorizar_territorios(
        [_entrada("21001", 40, "2000000.00", 0, "0.00"), _entrada("09002", 1, "10.00", 0, "0.00")]
    )

    assert [(r.clave_territorio, r.carga, r.posicion_campo) for r in resultado] == [
        ("09002", "SIN_CARGA", None),
        ("21001", "SIN_CARGA", None),
    ]


def test_los_sin_carga_van_al_final_y_por_clave():
    resultado = priorizar_territorios(ENTRADAS)
    activos = [r for r in resultado if r.carga != CargaTerritorial.SIN_CARGA]
    sin_carga = [r for r in resultado if r.carga == CargaTerritorial.SIN_CARGA]

    assert {r.carga for r in resultado} == set(CargaTerritorial)
    assert list(resultado) == activos + sin_carga
    assert _claves(sin_carga) == sorted(_claves(sin_carga))


# --- lo que no cambia -------------------------------------------------------------------------


def test_el_orden_de_llegada_no_cambia_el_resultado():
    # Las 5040 formas de ordenar la coleccion, incluidas la original y la inversa: siempre la
    # misma tupla, completa y en el mismo orden.
    esperado = priorizar_territorios(ENTRADAS)

    for orden in permutations(ENTRADAS):
        assert priorizar_territorios(orden) == esperado


@pytest.mark.parametrize(
    "como_llegan",
    [
        pytest.param(list, id="list"),
        pytest.param(tuple, id="tuple"),
        pytest.param(lambda entradas: (e for e in entradas), id="generador"),
        pytest.param(reversed, id="reversed"),
        pytest.param(
            lambda entradas: {e.clave_territorio: e for e in entradas}.values(), id="dict"
        ),
        pytest.param(frozenset, id="frozenset"),  # su orden depende del hash
    ],
)
def test_cualquier_iterable_da_el_mismo_resultado(como_llegan):
    assert priorizar_territorios(como_llegan(ENTRADAS)) == priorizar_territorios(list(ENTRADAS))


def test_priorizar_no_toca_la_coleccion_que_recibe():
    entradas = list(reversed(ENTRADAS))
    antes = list(entradas)

    priorizar_territorios(entradas)

    assert all(actual is original for actual, original in zip(entradas, antes, strict=True))


@pytest.mark.parametrize(
    ("objeto", "campo", "valor"),
    [
        pytest.param(ENTRADAS[0], "cuentas_campo", 0, id="EntradaTerritorio"),
        pytest.param(
            evaluar_territorio(ENTRADAS[0]), "posicion_campo", 1, id="ResultadoTerritorio"
        ),
        pytest.param(
            evaluar_territorio(ENTRADAS[0]).motivos[0], "valor", "0", id="MotivoTerritorial"
        ),
    ],
)
def test_entradas_resultados_y_motivos_no_se_pueden_modificar(objeto, campo, valor):
    with pytest.raises(FrozenInstanceError):
        setattr(objeto, campo, valor)


def _como_se_guarda(resultados: tuple[ResultadoTerritorio, ...]) -> str:
    return json.dumps(
        [
            {**asdict(r), "saldo_total": str(r.saldo_total), "saldo_campo": str(r.saldo_campo)}
            for r in resultados
        ]
    )


def test_la_misma_coleccion_da_siempre_el_mismo_resultado():
    # Cargas, motivos, posiciones y orden: igual cada vez, y tambien byte por byte.
    resultados = [priorizar_territorios(ENTRADAS) for _ in range(5)]

    assert all(resultado == resultados[0] for resultado in resultados)
    assert len({_como_se_guarda(resultado) for resultado in resultados}) == 1


# La misma coleccion en un proceso aparte, con otra semilla de hash y como frozenset, cuyo orden
# depende de esa semilla: un resultado que dependiera del orden de llegada o de algun estado del
# proceso saldria distinto.
PRIORIZAR_EN_OTRO_PROCESO = """
import json, sys
from dataclasses import asdict
from decimal import Decimal
from motor_cartera.territorial.reglas import EntradaTerritorio, priorizar_territorios

entradas = frozenset(
    EntradaTerritorio(clave[:2], clave[2:], cuentas_total, Decimal(saldo_total), cuentas_campo,
                      Decimal(saldo_campo))
    for clave, (cuentas_total, saldo_total, cuentas_campo, saldo_campo)
    in json.load(sys.stdin).items()
)
print(json.dumps([
    {**asdict(r), "saldo_total": str(r.saldo_total), "saldo_campo": str(r.saldo_campo)}
    for r in priorizar_territorios(entradas)
]))
"""


def test_la_misma_coleccion_da_lo_mismo_en_otro_proceso():
    proceso = subprocess.run(
        [sys.executable, "-I", "-c", PRIORIZAR_EN_OTRO_PROCESO],
        input=json.dumps(TERRITORIOS),
        capture_output=True,
        text=True,
        check=True,
    )

    assert proceso.stdout.strip() == _como_se_guarda(priorizar_territorios(ENTRADAS))


def test_el_orden_no_depende_del_contexto_decimal():
    # Los saldos solo se comparan, nunca se operan: negar o sumar un Decimal lo redondea al contexto
    # vigente. Con 3 digitos de precision, 1001.00 y 1004.00 serian el mismo saldo y desempataria
    # la clave; con Inexact atrapado, cualquier redondeo fallaria.
    entradas = [
        _entrada("09002", 7, "1001.00", 7, "1001.00"),
        _entrada("21001", 7, "1004.00", 7, "1004.00"),
    ]
    esperado = priorizar_territorios(entradas)

    with localcontext() as contexto:
        contexto.prec = 3
        contexto.rounding = ROUND_DOWN
        contexto.traps[Inexact] = True
        contexto.traps[Rounded] = True
        en_otro_contexto = priorizar_territorios(entradas)

    assert _claves(esperado) == ["21001", "09002"]
    assert en_otro_contexto == esperado


def test_priorizar_solo_agrega_la_posicion():
    # Cada territorio sale con la misma carga y el mismo motivo que al evaluarlo solo.
    entradas = {entrada.clave_territorio: entrada for entrada in ENTRADAS}

    for resultado in priorizar_territorios(ENTRADAS):
        sin_posicion = replace(resultado, posicion_campo=None)
        assert sin_posicion == evaluar_territorio(entradas[resultado.clave_territorio])


# --- la coleccion: vacia, su tipo y territorios repetidos -------------------------------------


@pytest.mark.parametrize(
    "vacia",
    [pytest.param([], id="list"), pytest.param((), id="tuple"), pytest.param(iter(()), id="iter")],
)
def test_sin_entradas_devuelve_una_tupla_vacia(vacia):
    # No es un error: si una ejecucion sin territorios se puede publicar lo decide quien la guarde.
    resultado = priorizar_territorios(vacia)

    assert resultado == ()
    assert type(resultado) is tuple


def test_devuelve_una_tupla_y_no_una_lista():
    assert type(priorizar_territorios(list(ENTRADAS))) is tuple


@pytest.mark.parametrize(
    "entradas",
    [
        pytest.param([ENTRADAS[0], ENTRADAS[0]], id="la-misma-entrada"),
        pytest.param(
            [
                _entrada("21001", 3, "300.00", 1, "100.00"),
                _entrada("21001", 3, "300.00", 1, "100.00"),
            ],
            id="mismas-metricas",
        ),
        pytest.param(
            [
                _entrada("21001", 3, "300.00", 1, "100.00"),
                _entrada("21001", 9, "900.00", 0, "0.00"),
            ],
            id="otras-metricas",
        ),
        pytest.param(
            [*ENTRADAS, _entrada("21001", 3, "300.00", 1, "100.00")], id="lejos-una-de-otra"
        ),
    ],
)
def test_un_territorio_repetido_no_se_suma_ni_se_elige(entradas):
    # Cada entrada ya es el agregado completo de su municipio: dos del mismo son un error de quien
    # agrego, aunque traigan las mismas metricas.
    with pytest.raises(ValueError, match="viene mas de una vez"):
        priorizar_territorios(entradas)


# --- validacion: lo que no entra --------------------------------------------------------------

VALIDA = _entrada("21001", 3, "300.00", 1, "100.00")


def test_la_clave_conserva_los_ceros_a_la_izquierda():
    entrada = EntradaTerritorio("09", "002", 1, Decimal("0.00"), 0, Decimal("0.00"))
    resultado = evaluar_territorio(entrada)

    assert entrada.clave_territorio == resultado.clave_territorio == "09002"  # ni "92" ni "9002"
    assert (resultado.cve_entidad, resultado.cve_municipio) == ("09", "002")


@pytest.mark.parametrize(
    ("campo", "valor", "error"),
    [
        pytest.param("cve_entidad", 9, TypeError, id="entidad-int-9"),
        pytest.param("cve_entidad", 21, TypeError, id="entidad-int-21"),
        pytest.param("cve_entidad", "9", ValueError, id="entidad-un-digito-9"),
        pytest.param("cve_entidad", "2", ValueError, id="entidad-un-digito-2"),
        pytest.param("cve_entidad", "009", ValueError, id="entidad-tres-digitos-009"),
        pytest.param("cve_entidad", "021", ValueError, id="entidad-tres-digitos-021"),
        pytest.param("cve_entidad", "A9", ValueError, id="entidad-letra-A9"),
        pytest.param("cve_entidad", "2A", ValueError, id="entidad-letra-2A"),
        pytest.param("cve_entidad", " 09", ValueError, id="entidad-espacio-antes"),
        pytest.param("cve_entidad", "09 ", ValueError, id="entidad-espacio-despues"),
        pytest.param("cve_entidad", "09\n", ValueError, id="entidad-salto-de-linea"),
        pytest.param("cve_entidad", "", ValueError, id="entidad-vacia"),
        pytest.param("cve_entidad", "\u0660\u0669", ValueError, id="entidad-arabigo-indica"),
        pytest.param("cve_entidad", "\uff10\uff19", ValueError, id="entidad-ancho-completo"),
        pytest.param("cve_entidad", None, TypeError, id="entidad-None"),
        pytest.param("cve_municipio", 2, TypeError, id="municipio-int"),
        pytest.param("cve_municipio", "2", ValueError, id="municipio-un-digito"),
        pytest.param("cve_municipio", "02", ValueError, id="municipio-dos-digitos"),
        pytest.param("cve_municipio", "0002", ValueError, id="municipio-cuatro-digitos"),
        pytest.param("cve_municipio", "0A2", ValueError, id="municipio-letra"),
        pytest.param("cve_municipio", "00\u00b2", ValueError, id="municipio-superindice"),
        pytest.param("cve_municipio", b"002", TypeError, id="municipio-bytes"),
    ],
)
def test_una_clave_mal_formada_no_entra(campo, valor, error):
    # Solo la forma: dos y tres digitos ASCII, como texto. Que la clave exista en el catalogo del
    # INEGI no se valida aqui.
    with pytest.raises(error, match=campo):
        replace(VALIDA, **{campo: valor})


@pytest.mark.parametrize("campo", ["cuentas_total", "cuentas_campo"])
@pytest.mark.parametrize(
    "valor",
    [
        pytest.param(True, id="True"),
        pytest.param(False, id="False"),
        pytest.param(1.0, id="float"),
        pytest.param(Decimal("1"), id="Decimal"),
        pytest.param("1", id="texto"),
        pytest.param(None, id="None"),
    ],
)
def test_un_conteo_que_no_es_int_no_entra(campo, valor):
    # bool es un int para Python, pero True no es una cuenta; y un conteo no se convierte.
    with pytest.raises(TypeError, match=campo):
        replace(VALIDA, **{campo: valor})


@pytest.mark.parametrize(
    ("cuentas_total", "cuentas_campo", "regla"),
    [
        pytest.param(0, 0, "cuentas_total debe ser al menos 1", id="sin-cuentas"),
        pytest.param(-1, 0, "cuentas_total no puede ser negativo", id="total-negativo"),
        pytest.param(3, -1, "cuentas_campo no puede ser negativo", id="campo-negativo"),
        pytest.param(3, 4, "no puede ser mayor que cuentas_total", id="mas-campo-que-total"),
    ],
)
def test_conteos_inconsistentes_no_entran(cuentas_total, cuentas_campo, regla):
    # Un territorio aparece porque tiene al menos una cuenta, y las de campo son parte del total.
    with pytest.raises(ValueError, match=regla):
        _entrada("21001", cuentas_total, "300.00", cuentas_campo, "0.00")


@pytest.mark.parametrize("campo", ["saldo_total", "saldo_campo"])
@pytest.mark.parametrize(
    ("valor", "error"),
    [
        pytest.param(1, TypeError, id="int"),
        pytest.param(1.0, TypeError, id="float"),
        pytest.param(True, TypeError, id="bool"),
        pytest.param("1.00", TypeError, id="texto"),
        pytest.param(None, TypeError, id="None"),
        pytest.param(Decimal("NaN"), ValueError, id="NaN"),
        pytest.param(Decimal("sNaN"), ValueError, id="sNaN"),
        pytest.param(Decimal("Infinity"), ValueError, id="Infinity"),
        pytest.param(Decimal("-Infinity"), ValueError, id="-Infinity"),
        pytest.param(Decimal("-0.01"), ValueError, id="negativo"),
    ],
)
def test_un_saldo_invalido_no_entra(campo, valor, error):
    # Un saldo que no es Decimal no se convierte en uno, aunque se pudiera.
    with pytest.raises(error, match=campo):
        replace(VALIDA, **{campo: valor})


@pytest.mark.parametrize("saldo", ["0", "1.00"])
def test_un_saldo_decimal_finito_y_no_negativo_entra(saldo):
    resultado = evaluar_territorio(_entrada("21001", 1, saldo, 1, saldo))

    assert (resultado.saldo_total, resultado.saldo_campo) == (Decimal(saldo), Decimal(saldo))


def test_el_saldo_de_campo_no_supera_el_total():
    with pytest.raises(ValueError, match="no puede ser mayor que saldo_total"):
        _entrada("21001", 3, "300.00", 2, "300.01")


def test_sin_cuentas_de_campo_no_hay_saldo_de_campo():
    with pytest.raises(ValueError, match="saldo_campo debe ser 0"):
        _entrada("21001", 3, "300.00", 0, "0.01")


def test_si_todas_las_cuentas_son_de_campo_el_saldo_de_campo_es_el_total():
    # Las dos sumas son de las mismas cuentas: tienen que ser iguales, sin redondear. Un centavo de
    # diferencia es un error de quien agrego.
    with pytest.raises(ValueError, match="si todas las cuentas son de campo"):
        _entrada("21001", 3, "100.00", 3, "99.99")

    entrada = _entrada("21001", 3, "100.00", 3, "100.00")
    assert evaluar_territorio(entrada).saldo_campo == entrada.saldo_total


@pytest.mark.parametrize(
    "agregados",
    [
        pytest.param((1, "0.00", 1, "0.00"), id="una-cuenta-de-campo-con-saldo-cero"),
        pytest.param((3, "300.00", 2, "0.00"), id="cuentas-de-campo-con-saldo-cero"),
        pytest.param((3, "300.00", 3, "300.00"), id="todo-en-campo"),
        pytest.param((3, "300.00", 0, "0"), id="sin-campo-y-saldo-cero"),
        pytest.param((3, "100.00", 2, "100.00"), id="saldo-de-campo-igual-al-total-sin-todo-campo"),
    ],
)
def test_las_fronteras_de_la_consistencia_si_entran(agregados):
    # Un saldo de cero es posible en el contrato: una cuenta de campo con saldo cero no es un
    # error, y no hay restriccion que inventar. Por lo mismo, que el saldo de campo sea todo el
    # saldo no exige que todas las cuentas sean de campo: las demas pueden tener saldo cero.
    entrada = _entrada("21001", *agregados)

    assert evaluar_territorio(entrada).saldo_campo == entrada.saldo_campo


def test_los_saldos_pasan_sin_redondear_ni_cuantizar():
    resultado = evaluar_territorio(_entrada("21001", 2, "1234.5", 1, "0.001"))

    assert (str(resultado.saldo_total), str(resultado.saldo_campo)) == ("1234.5", "0.001")


def test_solo_se_evalua_una_entrada_validada():
    # Un resultado tiene los mismos campos que una entrada, pero no paso por su validacion.
    with pytest.raises(TypeError, match="EntradaTerritorio"):
        evaluar_territorio(evaluar_territorio(VALIDA))
    with pytest.raises(TypeError, match="EntradaTerritorio"):
        priorizar_territorios([VALIDA, asdict(ENTRADAS[0])])


# --- invariantes ------------------------------------------------------------------------------


def test_los_catalogos_son_cerrados_y_ningun_codigo_sobra():
    # Cada carga y cada codigo salen en algun caso golden: ninguno es letra muerta. Agregar o
    # quitar uno rompe esta prueba, y es una version nueva de las reglas.
    assert [carga.value for carga in CargaTerritorial] == ["SIN_CARGA", "BAJA", "MEDIA", "ALTA"]
    assert [codigo.value for codigo in CodigoMotivoTerritorial] == [
        "SIN_CARGA_CAMPO",
        "CARGA_CAMPO_1_4",
        "CARGA_CAMPO_5_19",
        "CARGA_CAMPO_20_MAS",
    ]
    assert {carga for _, carga, _ in CARGAS.values()} == {c.value for c in CargaTerritorial}
    usados = {codigo for _, _, (codigo, _, _) in CARGAS.values()}
    assert usados == {codigo.value for codigo in CodigoMotivoTerritorial}
    # Una carga, un codigo, y ninguno repetido.
    assert list(MOTIVO_POR_CARGA) == list(CargaTerritorial)
    assert list(MOTIVO_POR_CARGA.values()) == list(CodigoMotivoTerritorial)


def test_los_codigos_territoriales_no_son_los_de_decision():
    # Dominios distintos: un codigo territorial no se confunde con el motivo de una cuenta.
    assert not {c.value for c in CodigoMotivoTerritorial} & {c.value for c in CodigoMotivo}


def test_cada_territorio_tiene_un_solo_motivo_y_explica_su_carga():
    # Ni por saldo, ni por clave, ni por posicion: el motivo explica la carga, y el lugar lo
    # explica la regla publica del orden.
    for resultado in priorizar_territorios(ENTRADAS):
        (motivo,) = resultado.motivos
        assert (motivo.codigo, motivo.campo, motivo.valor) == (
            MOTIVO_POR_CARGA[resultado.carga],
            "cuentas_campo",
            str(resultado.cuentas_campo),
        )


def test_lo_que_se_va_a_guardar_es_texto_plano():
    # El codigo es un StrEnum del dominio; campo, valor y claves son str, no enums, numeros ni
    # Decimal que se hacen pasar por texto.
    for resultado in priorizar_territorios(ENTRADAS):
        assert type(resultado.carga) is CargaTerritorial
        assert type(resultado.motivos) is tuple
        for motivo in resultado.motivos:
            assert type(motivo.codigo) is CodigoMotivoTerritorial
            assert type(motivo.campo) is str and type(motivo.valor) is str
        claves = (resultado.clave_territorio, resultado.cve_entidad, resultado.cve_municipio)
        assert all(type(clave) is str for clave in claves)


def test_la_version_es_propia_y_los_umbrales_estan_fijos():
    # Los golden de este archivo son los de territorial/v1.
    assert VERSION_REGLAS_TERRITORIAL == "territorial/v1"
    assert VERSION_REGLAS_TERRITORIAL not in {VERSION_CONTRATO, VERSION_REGLAS_DECISION}
    assert (UMBRAL_CARGA_MEDIA, UMBRAL_CARGA_ALTA) == (5, 20)
    assert type(UMBRAL_CARGA_MEDIA) is int and type(UMBRAL_CARGA_ALTA) is int


def test_la_entrada_solo_trae_los_agregados_del_territorio():
    # Sin cuentas, dias de atraso, saldos por cuenta, producto, canal, segmento, prioridad ni
    # coordenadas: el motor territorial no puede volver a decidir ni cambiar una decision
    # individual, solo reorganizar por territorio las que ya se publicaron.
    assert [campo.name for campo in fields(EntradaTerritorio)] == [
        "cve_entidad",
        "cve_municipio",
        "cuentas_total",
        "saldo_total",
        "cuentas_campo",
        "saldo_campo",
    ]


def test_el_resultado_es_del_territorio_y_trae_sus_agregados_tal_cual():
    # Nada de una cuenta ni de su decision: los agregados que llegaron, su carga, su lugar y el
    # motivo. Con eso se guarda sin volver a calcular.
    assert [campo.name for campo in fields(ResultadoTerritorio)] == [
        "clave_territorio",
        "cve_entidad",
        "cve_municipio",
        "cuentas_total",
        "saldo_total",
        "cuentas_campo",
        "saldo_campo",
        "carga",
        "posicion_campo",
        "motivos",
    ]
    entradas = {entrada.clave_territorio: entrada for entrada in ENTRADAS}
    for resultado in priorizar_territorios(ENTRADAS):
        entrada = entradas[resultado.clave_territorio]
        agregados = {campo.name: getattr(resultado, campo.name) for campo in fields(entrada)}
        assert agregados == asdict(entrada)


INFRAESTRUCTURA = {
    "sqlmodel",
    "sqlalchemy",
    "fastapi",
    "starlette",
    "psycopg",
    "pydantic",
    "pandas",
    "pandera",
    "numpy",
}

NUCLEO = {"motor_cartera", "motor_cartera.territorial", "motor_cartera.territorial.reglas"}

IMPORTAR_EN_LIMPIO = """
import importlib, json, sys

antes = set(sys.modules)
importlib.import_module(sys.argv[1])
print(json.dumps(sorted(set(sys.modules) - antes)))
"""


@pytest.mark.parametrize("modulo", sorted(NUCLEO - {"motor_cartera"}))
def test_el_nucleo_solo_carga_la_biblioteca_estandar(modulo):
    # En un proceso aparte, aislado: el de pytest ya tiene cargado todo lo demas. Importar las
    # reglas corre antes el __init__ del paquete, asi que tampoco ahi puede entrar infraestructura.
    proceso = subprocess.run(
        [sys.executable, "-I", "-c", IMPORTAR_EN_LIMPIO, modulo],
        capture_output=True,
        text=True,
        check=True,
    )
    cargados = set(json.loads(proceso.stdout))
    raices = {nombre.partition(".")[0] for nombre in cargados}

    assert not raices & INFRAESTRUCTURA
    assert raices - sys.stdlib_module_names == {"motor_cartera"}
    # Ni decision, ni db, ni la API, ni la configuracion: el paquete y sus reglas, nada mas.
    assert {nombre for nombre in cargados if nombre.startswith("motor_cartera")} == NUCLEO


BIBLIOTECA_PERMITIDA = {"__future__", "collections.abc", "dataclasses", "decimal", "enum", "typing"}


def _importa(ruta: Path) -> set[str]:
    """Todo lo que importa un archivo, en cualquier parte: tambien dentro de una funcion."""
    importados: set[str] = set()
    for nodo in ast.walk(ast.parse(ruta.read_text(encoding="utf-8"))):
        if isinstance(nodo, ast.Import):
            importados |= {alias.name for alias in nodo.names}
        elif isinstance(nodo, ast.ImportFrom):
            importados.add("." * nodo.level + (nodo.module or ""))
    return importados


def test_el_nucleo_no_importa_el_motor_de_decision_ni_dentro_de_una_funcion():
    # Importar en limpio solo ve lo que se carga al importar; un import dentro de una funcion solo
    # se ve leyendo el codigo. Acoplarse a decision/v1 le toca al servicio que arme las entradas.
    ruta = Path(reglas.__file__)

    assert _importa(ruta) <= BIBLIOTECA_PERMITIDA
    assert _importa(ruta.with_name("__init__.py")) == {"motor_cartera.territorial.reglas"}
