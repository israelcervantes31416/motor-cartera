"""decision/v1 sin base de datos: la decision exacta en cada frontera, y lo que no cambia.

Los casos golden afirman la decision completa, motivos incluidos, en la forma en que se va a
guardar. Si una regla cambia, cambian estos resultados: eso es una version nueva de las reglas,
no una prueba que corregir.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict, fields
from decimal import Decimal

import pytest

from motor_cartera import atraso, segmentacion
from motor_cartera.contratos import VERSION_CONTRATO
from motor_cartera.contratos.cartera import CANALES
from motor_cartera.decision import reglas
from motor_cartera.decision.reglas import (
    CANAL_POR_PRIORIDAD,
    MOTIVO_POR_SEGMENTO,
    PRIORIDAD_BASE,
    SEGMENTO_POR_TRAMO,
    UMBRAL_SALDO_ALTO,
    VERSION_REGLAS_DECISION,
    CodigoMotivo,
    EntradaDecision,
    MotivoDecision,
    Prioridad,
    ResultadoDecision,
    SegmentoMora,
    decidir_cuenta,
)
from motor_cartera.generador import sintetico

# --- golden: la decision exacta en cada frontera ----------------------------------------------
#
# Dias: cada frontera de los tramos (0, 1, 30, 31, 60, 61, 90, 91) y el tope del contrato (3650).
# Saldo: cero, justo abajo del umbral, justo en el umbral y el del ejemplo de la especificacion.

DECISIONES = {
    # caso: (dias, saldo, segmento, prioridad, canal)
    "G01": (0, "49999.99", "AL_CORRIENTE", "BAJA", "DIGITAL"),
    "G02": (0, "50000.00", "AL_CORRIENTE", "BAJA", "DIGITAL"),  # sin atraso, el saldo no sube
    "G03": (1, "49999.99", "MORA_TEMPRANA", "MEDIA", "DIGITAL"),
    "G04": (1, "50000.00", "MORA_TEMPRANA", "ALTA", "TELEFONICA"),  # el umbral es inclusivo
    "G05": (30, "49999.99", "MORA_TEMPRANA", "MEDIA", "DIGITAL"),
    "G06": (30, "50000.00", "MORA_TEMPRANA", "ALTA", "TELEFONICA"),
    "G07": (31, "49999.99", "MORA_MEDIA", "ALTA", "TELEFONICA"),
    "G08": (31, "50000.00", "MORA_MEDIA", "MUY_ALTA", "CAMPO"),
    "G09": (60, "49999.99", "MORA_MEDIA", "ALTA", "TELEFONICA"),
    "G10": (60, "50000.00", "MORA_MEDIA", "MUY_ALTA", "CAMPO"),
    "G11": (61, "49999.99", "MORA_MEDIA", "ALTA", "TELEFONICA"),  # 60/61 no separa segmentos
    "G12": (61, "50000.00", "MORA_MEDIA", "MUY_ALTA", "CAMPO"),
    "G13": (90, "49999.99", "MORA_MEDIA", "ALTA", "TELEFONICA"),
    "G14": (90, "50000.00", "MORA_MEDIA", "MUY_ALTA", "CAMPO"),
    "G15": (91, "49999.99", "MORA_ALTA", "MUY_ALTA", "CAMPO"),
    "G16": (91, "50000.00", "MORA_ALTA", "MUY_ALTA", "CAMPO"),  # tope: no sube, pero se anota
    "G17": (65, "62000.00", "MORA_MEDIA", "MUY_ALTA", "CAMPO"),  # el ejemplo de la especificacion
    "G18": (0, "0.00", "AL_CORRIENTE", "BAJA", "DIGITAL"),
    "G19": (3650, "0.00", "MORA_ALTA", "MUY_ALTA", "CAMPO"),
    "G20": (1, "50000", "MORA_TEMPRANA", "ALTA", "TELEFONICA"),  # el motivo lleva dos decimales
}

MOTIVOS = {
    # caso: los motivos, en su orden, como (codigo, campo, valor)
    "G01": [
        ("SIN_MORA", "dias_atraso", "0"),
        ("PRIORIDAD_BAJA", "prioridad", "BAJA"),
        ("CANAL_DIGITAL", "canal_recomendado", "DIGITAL"),
    ],
    "G02": [
        ("SIN_MORA", "dias_atraso", "0"),
        ("PRIORIDAD_BAJA", "prioridad", "BAJA"),
        ("CANAL_DIGITAL", "canal_recomendado", "DIGITAL"),
    ],
    "G03": [
        ("MORA_1_30", "dias_atraso", "1"),
        ("PRIORIDAD_MEDIA", "prioridad", "MEDIA"),
        ("CANAL_DIGITAL", "canal_recomendado", "DIGITAL"),
    ],
    "G04": [
        ("MORA_1_30", "dias_atraso", "1"),
        ("SALDO_ALTO", "saldo_total", "50000.00"),
        ("PRIORIDAD_ALTA", "prioridad", "ALTA"),
        ("CANAL_TELEFONICA", "canal_recomendado", "TELEFONICA"),
    ],
    "G05": [
        ("MORA_1_30", "dias_atraso", "30"),
        ("PRIORIDAD_MEDIA", "prioridad", "MEDIA"),
        ("CANAL_DIGITAL", "canal_recomendado", "DIGITAL"),
    ],
    "G06": [
        ("MORA_1_30", "dias_atraso", "30"),
        ("SALDO_ALTO", "saldo_total", "50000.00"),
        ("PRIORIDAD_ALTA", "prioridad", "ALTA"),
        ("CANAL_TELEFONICA", "canal_recomendado", "TELEFONICA"),
    ],
    "G07": [
        ("MORA_31_90", "dias_atraso", "31"),
        ("PRIORIDAD_ALTA", "prioridad", "ALTA"),
        ("CANAL_TELEFONICA", "canal_recomendado", "TELEFONICA"),
    ],
    "G08": [
        ("MORA_31_90", "dias_atraso", "31"),
        ("SALDO_ALTO", "saldo_total", "50000.00"),
        ("PRIORIDAD_MUY_ALTA", "prioridad", "MUY_ALTA"),
        ("CANAL_CAMPO", "canal_recomendado", "CAMPO"),
    ],
    "G09": [
        ("MORA_31_90", "dias_atraso", "60"),
        ("PRIORIDAD_ALTA", "prioridad", "ALTA"),
        ("CANAL_TELEFONICA", "canal_recomendado", "TELEFONICA"),
    ],
    "G10": [
        ("MORA_31_90", "dias_atraso", "60"),
        ("SALDO_ALTO", "saldo_total", "50000.00"),
        ("PRIORIDAD_MUY_ALTA", "prioridad", "MUY_ALTA"),
        ("CANAL_CAMPO", "canal_recomendado", "CAMPO"),
    ],
    "G11": [
        ("MORA_31_90", "dias_atraso", "61"),
        ("PRIORIDAD_ALTA", "prioridad", "ALTA"),
        ("CANAL_TELEFONICA", "canal_recomendado", "TELEFONICA"),
    ],
    "G12": [
        ("MORA_31_90", "dias_atraso", "61"),
        ("SALDO_ALTO", "saldo_total", "50000.00"),
        ("PRIORIDAD_MUY_ALTA", "prioridad", "MUY_ALTA"),
        ("CANAL_CAMPO", "canal_recomendado", "CAMPO"),
    ],
    "G13": [
        ("MORA_31_90", "dias_atraso", "90"),
        ("PRIORIDAD_ALTA", "prioridad", "ALTA"),
        ("CANAL_TELEFONICA", "canal_recomendado", "TELEFONICA"),
    ],
    "G14": [
        ("MORA_31_90", "dias_atraso", "90"),
        ("SALDO_ALTO", "saldo_total", "50000.00"),
        ("PRIORIDAD_MUY_ALTA", "prioridad", "MUY_ALTA"),
        ("CANAL_CAMPO", "canal_recomendado", "CAMPO"),
    ],
    "G15": [
        ("MORA_91_MAS", "dias_atraso", "91"),
        ("PRIORIDAD_MUY_ALTA", "prioridad", "MUY_ALTA"),
        ("CANAL_CAMPO", "canal_recomendado", "CAMPO"),
    ],
    "G16": [
        ("MORA_91_MAS", "dias_atraso", "91"),
        ("SALDO_ALTO", "saldo_total", "50000.00"),
        ("PRIORIDAD_MUY_ALTA", "prioridad", "MUY_ALTA"),
        ("CANAL_CAMPO", "canal_recomendado", "CAMPO"),
    ],
    "G17": [
        ("MORA_31_90", "dias_atraso", "65"),
        ("SALDO_ALTO", "saldo_total", "62000.00"),
        ("PRIORIDAD_MUY_ALTA", "prioridad", "MUY_ALTA"),
        ("CANAL_CAMPO", "canal_recomendado", "CAMPO"),
    ],
    "G18": [
        ("SIN_MORA", "dias_atraso", "0"),
        ("PRIORIDAD_BAJA", "prioridad", "BAJA"),
        ("CANAL_DIGITAL", "canal_recomendado", "DIGITAL"),
    ],
    "G19": [
        ("MORA_91_MAS", "dias_atraso", "3650"),
        ("PRIORIDAD_MUY_ALTA", "prioridad", "MUY_ALTA"),
        ("CANAL_CAMPO", "canal_recomendado", "CAMPO"),
    ],
    "G20": [
        ("MORA_1_30", "dias_atraso", "1"),
        ("SALDO_ALTO", "saldo_total", "50000.00"),
        ("PRIORIDAD_ALTA", "prioridad", "ALTA"),
        ("CANAL_TELEFONICA", "canal_recomendado", "TELEFONICA"),
    ],
}

ENTRADAS = [pytest.param(dias, saldo, id=caso) for caso, (dias, saldo, *_) in DECISIONES.items()]


def _decidir(dias: int, saldo: str) -> ResultadoDecision:
    return decidir_cuenta(EntradaDecision(dias, Decimal(saldo)))


def _como_se_guarda(resultado: ResultadoDecision) -> list:
    return [
        resultado.segmento,
        resultado.prioridad,
        resultado.canal_recomendado,
        [asdict(motivo) for motivo in resultado.motivos],
    ]


def test_cada_caso_golden_tiene_sus_motivos():
    assert list(MOTIVOS) == list(DECISIONES)


@pytest.mark.parametrize(
    ("dias", "saldo", "segmento", "prioridad", "canal", "motivos"),
    [pytest.param(*DECISIONES[caso], MOTIVOS[caso], id=caso) for caso in DECISIONES],
)
def test_cada_frontera_da_su_decision_exacta(dias, saldo, segmento, prioridad, canal, motivos):
    esperados = (MotivoDecision(CodigoMotivo(c), campo, valor) for c, campo, valor in motivos)

    assert _decidir(dias, saldo) == ResultadoDecision(
        segmento=SegmentoMora(segmento),
        prioridad=Prioridad(prioridad),
        canal_recomendado=canal,
        motivos=tuple(esperados),
    )


def test_el_ejemplo_de_la_especificacion_sale_exacto():
    resultado = _decidir(65, "62000.00")

    assert (resultado.segmento, resultado.prioridad, resultado.canal_recomendado) == (
        "MORA_MEDIA",
        "MUY_ALTA",
        "CAMPO",
    )
    # Tal como se va a guardar: una lista de objetos con codigo, campo y valor.
    assert [asdict(motivo) for motivo in resultado.motivos] == [
        {"codigo": "MORA_31_90", "campo": "dias_atraso", "valor": "65"},
        {"codigo": "SALDO_ALTO", "campo": "saldo_total", "valor": "62000.00"},
        {"codigo": "PRIORIDAD_MUY_ALTA", "campo": "prioridad", "valor": "MUY_ALTA"},
        {"codigo": "CANAL_CAMPO", "campo": "canal_recomendado", "valor": "CAMPO"},
    ]


@pytest.mark.parametrize(
    ("dias", "saldo", "error"),
    [
        pytest.param(-1, Decimal("100.00"), ValueError, id="G21"),  # dias negativos
        pytest.param(10, Decimal("-0.01"), ValueError, id="G22"),  # saldo negativo
        pytest.param(10, 50000.0, TypeError, id="G23"),  # saldo float
        pytest.param(10, 50000, TypeError, id="G24"),  # saldo int
        pytest.param(10, "50000.00", TypeError, id="G25"),  # saldo texto
        pytest.param(10, Decimal("NaN"), ValueError, id="G26"),  # saldo que no es un numero
        pytest.param(10, Decimal("Infinity"), ValueError, id="G27"),  # saldo infinito
        pytest.param(30.0, Decimal("100.00"), TypeError, id="G28"),  # dias float
        pytest.param(True, Decimal("100.00"), TypeError, id="G29"),  # dias bool
    ],
)
def test_una_entrada_invalida_no_se_decide(dias, saldo, error):
    with pytest.raises(error):
        EntradaDecision(dias, saldo)


# --- invariantes ----------------------------------------------------------------------------


def test_los_catalogos_son_cerrados_y_ningun_motivo_sobra():
    # V1. Cada codigo sale en algun caso golden: ninguno es letra muerta.
    assert [codigo.value for codigo in CodigoMotivo] == [
        "SIN_MORA",
        "MORA_1_30",
        "MORA_31_90",
        "MORA_91_MAS",
        "SALDO_ALTO",
        "PRIORIDAD_BAJA",
        "PRIORIDAD_MEDIA",
        "PRIORIDAD_ALTA",
        "PRIORIDAD_MUY_ALTA",
        "CANAL_DIGITAL",
        "CANAL_TELEFONICA",
        "CANAL_CAMPO",
    ]
    usados = {codigo for motivos in MOTIVOS.values() for codigo, _, _ in motivos}
    assert usados == {codigo.value for codigo in CodigoMotivo}
    assert [s.value for s in SegmentoMora] == [
        "AL_CORRIENTE",
        "MORA_TEMPRANA",
        "MORA_MEDIA",
        "MORA_ALTA",
    ]
    assert [p.value for p in Prioridad] == ["BAJA", "MEDIA", "ALTA", "MUY_ALTA"]


def test_todo_tramo_tiene_segmento_y_cada_tabla_esta_completa():
    # V2. El segmento sale del tramo, y ningun tramo, segmento ni prioridad queda sin regla.
    assert list(SEGMENTO_POR_TRAMO) == [etiqueta for etiqueta, _, _ in atraso.TRAMOS_ATRASO]
    assert set(SEGMENTO_POR_TRAMO.values()) == set(SegmentoMora)
    assert set(MOTIVO_POR_SEGMENTO) == set(PRIORIDAD_BASE) == set(SegmentoMora)
    assert set(CANAL_POR_PRIORIDAD) == set(Prioridad)


def test_el_canal_recomendado_es_del_vocabulario_del_contrato():
    # V3. El mismo vocabulario que el canal de la cartera: no hay un catalogo de canales nuevo.
    assert set(CANAL_POR_PRIORIDAD.values()) <= set(CANALES)


def test_la_version_de_las_reglas_es_propia_y_el_umbral_esta_fijo():
    # V4. Los golden de este archivo son los de decision/v1.
    assert VERSION_REGLAS_DECISION == "decision/v1"
    assert VERSION_REGLAS_DECISION != VERSION_CONTRATO
    assert isinstance(UMBRAL_SALDO_ALTO, Decimal)
    assert str(UMBRAL_SALDO_ALTO) == "50000.00"


# El mismo calculo en un proceso aparte, con otra semilla de hash: un resultado que dependiera
# del orden de un set o de algun estado del proceso saldria distinto.
DECIDIR_EN_OTRO_PROCESO = """
import json, sys
from dataclasses import asdict
from decimal import Decimal
from motor_cartera.decision.reglas import EntradaDecision, decidir_cuenta

salida = []
for dias, saldo in json.load(sys.stdin):
    r = decidir_cuenta(EntradaDecision(dias, Decimal(saldo)))
    salida.append([r.segmento, r.prioridad, r.canal_recomendado, [asdict(m) for m in r.motivos]])
print(json.dumps(salida))
"""


def test_la_misma_entrada_da_siempre_el_mismo_resultado():
    # V5. Dos veces en este proceso y una en otro: el mismo resultado, byte por byte.
    entradas = [[dias, saldo] for dias, saldo, *_ in DECISIONES.values()]
    una = json.dumps([_como_se_guarda(_decidir(dias, saldo)) for dias, saldo in entradas])
    otra = json.dumps([_como_se_guarda(_decidir(dias, saldo)) for dias, saldo in entradas])

    proceso = subprocess.run(
        [sys.executable, "-I", "-c", DECIDIR_EN_OTRO_PROCESO],
        input=json.dumps(entradas),
        capture_output=True,
        text=True,
        check=True,
    )

    assert una == otra == proceso.stdout.strip()


@pytest.mark.parametrize(("dias", "saldo"), ENTRADAS)
def test_los_motivos_siguen_el_orden_de_las_reglas(dias, saldo):
    # V6. El segmento primero; SALDO_ALTO solo si aplica; al final la prioridad y el canal, que
    # repiten las columnas de la decision.
    resultado = _decidir(dias, saldo)
    primero, *enmedio, prioridad, canal = resultado.motivos
    aplica_saldo = dias > 0 and Decimal(saldo) >= UMBRAL_SALDO_ALTO

    assert (primero.codigo, primero.campo, primero.valor) == (
        MOTIVO_POR_SEGMENTO[resultado.segmento],
        "dias_atraso",
        str(dias),
    )
    assert [motivo.codigo for motivo in enmedio] == (
        [CodigoMotivo.SALDO_ALTO] if aplica_saldo else []
    )
    assert (prioridad.codigo, prioridad.campo, prioridad.valor) == (
        f"PRIORIDAD_{resultado.prioridad}",
        "prioridad",
        resultado.prioridad,
    )
    assert (canal.codigo, canal.campo, canal.valor) == (
        f"CANAL_{resultado.canal_recomendado}",
        "canal_recomendado",
        resultado.canal_recomendado,
    )
    # Lo que se va a guardar es texto plano, no enums que se hacen pasar por texto.
    for motivo in resultado.motivos:
        assert type(motivo.codigo) is CodigoMotivo
        assert type(motivo.campo) is str and type(motivo.valor) is str
    assert type(resultado.canal_recomendado) is str


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

MODULOS_PROPIOS = {
    "motor_cartera.atraso": {"motor_cartera", "motor_cartera.atraso"},
    "motor_cartera.decision.reglas": {
        "motor_cartera",
        "motor_cartera.atraso",
        "motor_cartera.decision",
        "motor_cartera.decision.reglas",
    },
}

IMPORTAR_EN_LIMPIO = """
import importlib, json, sys

antes = set(sys.modules)
importlib.import_module(sys.argv[1])
print(json.dumps(sorted(set(sys.modules) - antes)))
"""


@pytest.mark.parametrize("modulo", sorted(MODULOS_PROPIOS))
def test_el_nucleo_solo_carga_la_biblioteca_estandar(modulo):
    # V7. En un proceso aparte, aislado: el de pytest ya tiene cargado todo lo demas.
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
    assert {nombre for nombre in cargados if nombre.startswith("motor_cartera")} == (
        MODULOS_PROPIOS[modulo]
    )


def test_los_tramos_tienen_una_sola_fuente():
    # V8. El resumen y el generador leen los mismos objetos que las reglas, no una copia.
    assert segmentacion.TRAMOS_ATRASO is atraso.TRAMOS_ATRASO
    assert segmentacion.tramo_de_atraso is atraso.tramo_de_atraso
    assert sintetico.TRAMOS_ATRASO is atraso.TRAMOS_ATRASO


def test_la_entrada_solo_trae_lo_que_decide():
    # Sin canal, producto ni claves geograficas: no pueden influir, y el canal recomendado no
    # puede ser una copia del original.
    assert [campo.name for campo in fields(EntradaDecision)] == ["dias_atraso", "saldo_total"]


def test_un_tramo_sin_segmento_hace_fallar_la_decision(monkeypatch):
    # Si el resumen gana un tramo nuevo, la decision no le adivina un segmento.
    monkeypatch.setattr(reglas, "tramo_de_atraso", lambda dias: "181+")

    with pytest.raises(KeyError, match="181"):
        _decidir(200, "0.00")
