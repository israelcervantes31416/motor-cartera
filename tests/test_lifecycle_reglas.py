"""El vocabulario de lifecycle/v1 y sus reglas, sin base: la coherencia de una gestion, la barrera
de datos personales en un texto libre y la huella de una peticion operacional."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from motor_cartera.lifecycle.reglas import (
    CIERRES,
    MEDIOS_POR_CANAL,
    NIVEL_DE_LA_VISITA,
    RELACIONADO,
    Canal,
    Medio,
    NivelContacto,
    ResultadoGestion,
    ResultadoVisita,
    TipoEvento,
    datos_personales,
    huella,
    incoherencias_de_gestion,
)

T, D, C, OTRO = Canal.TELEFONICA, Canal.DIGITAL, Canal.CAMPO, Canal.OTRO
TITULAR, TERCERO = NivelContacto.CONTACTO_TITULAR, NivelContacto.CONTACTO_TERCERO
SIN, NA = NivelContacto.SIN_CONTACTO, NivelContacto.NO_APLICA
R = ResultadoGestion


@pytest.mark.parametrize(
    ("canal", "medio", "nivel", "resultado", "visita"),
    [
        (T, Medio.LLAMADA, SIN, R.SIN_RESPUESTA, None),
        (T, None, TITULAR, R.CONTACTO, None),  # hablar con el titular no es una promesa
        (T, Medio.LLAMADA, TERCERO, R.RECHAZO, None),
        (T, Medio.LLAMADA, TITULAR, R.PROMESA, None),
        (D, Medio.SMS, NA, R.SIN_RESPUESTA, None),  # un mensaje de una via
        (D, Medio.WHATSAPP, TITULAR, R.CONVENIO, None),
        (C, None, SIN, R.VISITA_REALIZADA, ResultadoVisita.NO_LOCALIZADO),
        (C, None, SIN, R.VISITA_REALIZADA, ResultadoVisita.DOMICILIO_NO_VALIDO),
        (C, None, TITULAR, R.PROMESA, ResultadoVisita.CONTACTO_TITULAR),
        (C, None, TERCERO, R.VISITA_REALIZADA, ResultadoVisita.CONTACTO_TERCERO),
        (C, None, NA, R.OTRO, ResultadoVisita.OTRO),  # NO_APLICA no es de campo
        (OTRO, None, NA, R.OTRO, None),
    ],
)
def test_una_gestion_coherente_no_tiene_incoherencias(canal, medio, nivel, resultado, visita):
    problemas = incoherencias_de_gestion(canal, medio, nivel, resultado, visita)

    if nivel == NA and canal == C:
        assert problemas == ["NO_APLICA es de un mensaje DIGITAL o de OTRO canal, no de CAMPO."]
    else:
        assert problemas == []


@pytest.mark.parametrize(
    ("canal", "medio", "nivel", "resultado", "visita", "problema"),
    [
        (D, Medio.LLAMADA, NA, R.SIN_RESPUESTA, None, "no es del canal DIGITAL"),
        (C, Medio.SMS, SIN, R.VISITA_REALIZADA, ResultadoVisita.SIN_CONTACTO, "admite: ninguno"),
        (T, None, TITULAR, R.SIN_RESPUESTA, None, "SIN_RESPUESTA no puede tener"),
        (T, None, SIN, R.PROMESA, None, "PROMESA exige contacto"),
        (D, None, NA, R.CONVENIO, None, "CONVENIO exige contacto"),
        (T, None, TITULAR, R.VISITA_REALIZADA, None, "solo es de una gestion de CAMPO"),
        (C, None, SIN, R.SIN_RESPUESTA, ResultadoVisita.SIN_CONTACTO, "no termina en"),
        (C, None, TITULAR, R.CONTACTO, ResultadoVisita.CONTACTO_TITULAR, "no termina en"),
        (T, None, NA, R.OTRO, None, "NO_APLICA es de un mensaje"),
        (C, None, TITULAR, R.PROMESA, None, "trae su visita"),
        (T, None, TITULAR, R.PROMESA, ResultadoVisita.CONTACTO_TITULAR, "Solo una gestion"),
        (
            C,
            None,
            TITULAR,
            R.VISITA_REALIZADA,
            ResultadoVisita.NO_LOCALIZADO,
            "corresponde a una gestion SIN_CONTACTO",
        ),
        (
            C,
            None,
            TERCERO,
            R.PROMESA,
            ResultadoVisita.CONTACTO_TITULAR,
            "corresponde a una gestion CONTACTO_TITULAR",
        ),
    ],
)
def test_cada_regla_rota_se_dice_con_una_frase(canal, medio, nivel, resultado, visita, problema):
    problemas = incoherencias_de_gestion(canal, medio, nivel, resultado, visita)

    assert any(problema in p for p in problemas), problemas


def test_el_vocabulario_cubre_cada_canal_y_cada_visita():
    assert set(MEDIOS_POR_CANAL) == set(Canal)
    assert set(NIVEL_DE_LA_VISITA) == set(ResultadoVisita)
    # Cada evento, salvo registrar una gestion, se refiere a uno anterior; los cierres, tambien.
    assert set(RELACIONADO) == set(TipoEvento) - {TipoEvento.GESTION_REGISTRADA}
    assert CIERRES <= set(RELACIONADO)
    assert {RELACIONADO[c] for c in CIERRES} == {
        TipoEvento.GESTION_REGISTRADA,
        TipoEvento.PROMESA_CREADA,
        TipoEvento.CONVENIO_CREADO,
    }


# --- datos personales en un texto libre -----------------------------------------------------------


@pytest.mark.parametrize(
    ("texto", "encontrado"),
    [
        ("Pidio que le marquen al 55 1234 5678", "un numero de telefono o de tarjeta"),
        ("Su tel es (222)-555-0101.", "un numero de telefono o de tarjeta"),
        ("Pago con la tarjeta 4152313412345678", "un numero de telefono o de tarjeta"),
        ("Mandar estado de cuenta a cliente.prueba@correo.example", "un correo electronico"),
        ("CURP ABCD800101HPLRRN09 en el expediente", "una CURP"),
        ("RFC ABCD800101XY1", "un RFC"),
    ],
)
def test_un_texto_libre_con_datos_personales_se_reconoce(texto, encontrado):
    assert encontrado in datos_personales(texto)


@pytest.mark.parametrize(
    "texto",
    [
        "Promete pagar 1,500.00 el viernes; se le recordara por SMS.",
        "Visita a las 10:30, nadie atendio. Volver el 2026-09-20.",
        "Se acordo un convenio de 6 cuotas de 2500.00.",
        "Folio interno 123456789 de la llamada",  # nueve digitos: no es un telefono
    ],
)
def test_un_texto_operativo_no_se_confunde_con_datos_personales(texto):
    assert datos_personales(texto) == []


# --- la huella de una peticion --------------------------------------------------------------------

CUENTA = "4d7b8c2e-0f1a-4b3c-9d5e-6f7a8b9c0d1e"


def test_la_huella_es_la_misma_para_la_misma_peticion_aunque_cambie_su_forma():
    a = huella(
        TipoEvento.GESTION_REGISTRADA,
        CUENTA,
        {
            "ocurrido_en": datetime(2026, 9, 20, 10, 15, tzinfo=timezone(timedelta(hours=-6))),
            "canal": "TELEFONICA",
            "monto": Decimal("1000"),
            "fecha_limite": date(2026, 9, 30),
            "visita": None,
        },
    )
    b = huella(
        TipoEvento.GESTION_REGISTRADA,
        CUENTA,
        {
            # El mismo instante en UTC, otro orden de las llaves y el mismo importe con centavos.
            "visita": None,
            "fecha_limite": date(2026, 9, 30),
            "monto": Decimal("1000.00"),
            "canal": "TELEFONICA",
            "ocurrido_en": datetime(2026, 9, 20, 16, 15, tzinfo=UTC),
        },
    )

    assert a == b
    assert len(a) == 32


def test_otra_operacion_otro_recurso_u_otro_dato_es_otra_huella():
    datos = {"ocurrido_en": datetime(2026, 9, 20, 16, 15, tzinfo=UTC), "canal": "TELEFONICA"}
    base = huella(TipoEvento.GESTION_REGISTRADA, CUENTA, datos)

    assert huella(TipoEvento.PROMESA_CREADA, CUENTA, datos) != base
    assert huella(TipoEvento.GESTION_REGISTRADA, str(UUID(int=1)), datos) != base
    assert huella(TipoEvento.GESTION_REGISTRADA, CUENTA, {**datos, "canal": "CAMPO"}) != base


def test_un_instante_sin_zona_no_tiene_huella():
    with pytest.raises(ValueError, match="zona horaria"):
        huella(TipoEvento.GESTION_REGISTRADA, CUENTA, {"ocurrido_en": datetime(2026, 9, 20)})


def test_lo_que_no_se_sabe_poner_en_forma_canonica_no_tiene_huella():
    with pytest.raises(TypeError, match="set"):
        huella(TipoEvento.GESTION_REGISTRADA, CUENTA, {"canales": {"TELEFONICA"}})


IMPORTAR_EN_LIMPIO = """
import importlib, json, sys

antes = set(sys.modules)
importlib.import_module(sys.argv[1])
print(json.dumps(sorted(set(sys.modules) - antes)))
"""


def test_el_vocabulario_solo_carga_la_biblioteca_estandar():
    # En un proceso aparte: el de pytest ya tiene cargado todo lo demas. Lo usan los modelos, la
    # API y la importacion, y no puede arrastrar la base ni el framework.
    proceso = subprocess.run(
        [sys.executable, "-I", "-c", IMPORTAR_EN_LIMPIO, "motor_cartera.lifecycle.reglas"],
        capture_output=True,
        text=True,
        check=True,
    )
    cargados = set(json.loads(proceso.stdout))
    raices = {nombre.partition(".")[0] for nombre in cargados}

    assert raices - sys.stdlib_module_names == {"motor_cartera"}
    assert {n for n in cargados if n.startswith("motor_cartera")} == {
        "motor_cartera",
        "motor_cartera.lifecycle",
        "motor_cartera.lifecycle.reglas",
    }
