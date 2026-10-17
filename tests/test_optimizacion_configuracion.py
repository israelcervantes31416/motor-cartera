"""La configuracion de optimization/v1 (optimization-config/v1) y su politica
(optimization-public-v1): el esquema cerrado, que reporta todos sus problemas juntos; la forma
canonica y su hash (goldens 134 y 135); y, contra PostgreSQL, registrar, reutilizar, publicar y leer
una version, que ya publicada no cambia.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import random
import threading

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from motor_cartera.analytics.catalogo import canonico
from motor_cartera.contratos.cartera import PRODUCTOS as PRODUCTOS_DE_CARTERA
from motor_cartera.db.sesion import restriccion as restriccion_violada
from motor_cartera.db.sesion import sesion
from motor_cartera.optimizacion import configuracion as cfg
from motor_cartera.optimizacion.politica import (
    PRODUCTOS,
    PUBLIC_V1,
    hash_politica,
)

HASH_DE_PUBLIC_V1 = "540bc21fadd8297c2a8d427e8af540610642016e7dea5e9be87f997c53d23fc5"
"""optimization-public-v1, fijada antes de medir: cambiarla es otra version de la politica."""


def documento() -> dict:
    return {
        "nombre": "Escenario de las pruebas",
        "permitir_predicciones_con_imputacion": False,
        "max_cuentas_atendidas": 900,
        "max_unidades_recurso": 1_500,
        "admisibilidad": [
            {"canal_decision": "DIGITAL", "canal_operativo": "DIGITAL", "permitido": True},
            {"canal_decision": "TELEFONICA", "canal_operativo": "TELEFONICA", "permitido": True},
            {"canal_decision": "TELEFONICA", "canal_operativo": "DIGITAL", "permitido": True},
            {"canal_decision": "CAMPO", "canal_operativo": "CAMPO", "permitido": True},
            {"canal_decision": "OTRO", "canal_operativo": "DIGITAL", "permitido": False},
        ],
        "pools": [
            {
                "codigo": "DIGITAL_GLOBAL",
                "canal": "DIGITAL",
                "tipo_alcance": "GLOBAL",
                "referencia_alcance": None,
                "capacidad": 600,
                "unidades_recurso_por_asignacion": 1,
                "activo": True,
            },
            {
                "codigo": "TELEFONICA_BASE_A",
                "canal": "TELEFONICA",
                "tipo_alcance": "BASE",
                "referencia_alcance": "BASE_A",
                "capacidad": 200,
                "unidades_recurso_por_asignacion": 4,
                "activo": True,
            },
            {
                "codigo": "CAMPO_G001",
                "canal": "CAMPO",
                "tipo_alcance": "GESTOR_CAMPO",
                "referencia_alcance": "G001",
                "capacidad": None,
                "unidades_recurso_por_asignacion": 20,
                "activo": True,
            },
        ],
        "restricciones": [
            {
                "codigo": "MIN_MUY_ALTA",
                "tipo": "MIN_CUENTAS",
                "dimension": "PRIORIDAD_DECISION",
                "valor_dimension": "MUY_ALTA",
                "limite": 50,
                "activa": True,
            },
            {
                "codigo": "MAX_CONSUMO",
                "tipo": "MAX_PARTICIPACION_BPS",
                "dimension": "PRODUCTO",
                "valor_dimension": "CONSUMO",
                "limite": 4_500,
                "activa": True,
            },
        ],
        "supuestos": "SUPUESTOS SINTETICOS: solo para medir el optimizador.",
    }


# --- leer -----------------------------------------------------------------------------------------


def test_un_documento_valido_se_lee_ordenado():
    c = cfg.leer(documento())
    assert [p.codigo for p in c.pools] == ["CAMPO_G001", "DIGITAL_GLOBAL", "TELEFONICA_BASE_A"]
    assert c.admisibles("TELEFONICA") == ("DIGITAL", "TELEFONICA")
    assert c.admisibles("OTRO") == ()
    assert c.usa_campo and c.usa_base
    assert c.pools[0].capacidad is None and c.supuestos.startswith("SUPUESTOS SINTETICOS")
    assert c.canonica()["forma"] == cfg.VERSION_CONFIGURACION


@pytest.mark.parametrize(
    "cambio, problema",
    [
        (lambda d: d.pop("permitir_predicciones_con_imputacion"), "falta permitir_predicciones"),
        (lambda d: d.update(permitir_predicciones_con_imputacion=None), "sin valor por omision"),
        (lambda d: d.update(expresion="valor * 2"), "no se conoce expresion"),
        (lambda d: d["pools"][0].update(capacidad="SELECT 1"), "pools[0].capacidad"),
        (lambda d: d["pools"][0].update(capacidad=0), "pools[0].capacidad"),
        (lambda d: d["pools"][0].update(capacidad=None), "pools[0].capacidad"),
        (lambda d: d["pools"][0].update(capacidad=True), "pools[0].capacidad"),
        (lambda d: d["pools"][0].update(codigo="DIGITAL; DROP"), "pools[0].codigo"),
        (lambda d: d["pools"][0].update(canal="SMS"), "pools[0].canal"),
        (lambda d: d["pools"][0].update(referencia_alcance="X"), "un pool GLOBAL no tiene"),
        (lambda d: d["pools"][2].update(tipo_alcance="GLOBAL"), "un pool CAMPO es de alcance"),
        (lambda d: d["pools"][1].update(tipo_alcance="GESTOR_CAMPO"), "un pool TELEFONICA"),
        (lambda d: d["pools"][2].update(referencia_alcance=None), "pools[2].referencia_alcance"),
        (lambda d: d["pools"][0].update(unidades_recurso_por_asignacion=0), "unidades_recurso"),
        (lambda d: d["pools"].append(dict(d["pools"][0])), "esta dos veces"),
        (
            lambda d: d["pools"].append({**d["pools"][2], "codigo": "CAMPO_OTRO"}),
            "uno por gestor",
        ),
        (lambda d: [p.update(activo=False) for p in d["pools"]], "al menos uno activo"),
        (lambda d: d.update(pools=[]), "pools: una lista con al menos uno"),
        (lambda d: d["admisibilidad"].append(dict(d["admisibilidad"][0])), "esta dos veces"),
        (lambda d: d["admisibilidad"][0].update(canal_operativo="OTRO"), "canal_operativo"),
        (lambda d: d["restricciones"][1].update(limite=10_001), "puntos base"),
        (lambda d: d["restricciones"][0].update(limite=-1), "(cuentas)"),
        (lambda d: d["restricciones"][0].update(tipo="MIN_UPLIFT"), ".tipo"),
        (lambda d: d["restricciones"][0].update(dimension="CUENTA_ID"), ".dimension"),
        (lambda d: d["restricciones"][0].update(dimension="CLIENTE_UNICO"), ".dimension"),
        (lambda d: d["restricciones"][1].update(valor_dimension="HIPOTECA"), "valor_dimension"),
        (
            lambda d: d["restricciones"][1].update(dimension="BASE", valor_dimension="base a"),
            "el codigo de una base",
        ),
        (lambda d: d.update(max_cuentas_atendidas=0), "max_cuentas_atendidas"),
        (lambda d: d.update(max_unidades_recurso=2**53), "max_unidades_recurso"),
        (lambda d: d.update(nombre=" "), "nombre"),
    ],
)
def test_lo_que_no_es_valido_se_rechaza_entero(cambio, problema):
    d = documento()
    cambio(d)
    with pytest.raises(cfg.ConfiguracionInvalida) as error:
        cfg.leer(d)
    assert any(problema in p for p in error.value.problemas), error.value.problemas


def test_todos_los_problemas_se_reportan_juntos():
    d = documento()
    d.pop("nombre")
    d["pools"][0]["canal"] = "SMS"
    d["pools"][1]["capacidad"] = 0
    d["restricciones"][0]["dimension"] = "CUENTA_ID"
    with pytest.raises(cfg.ConfiguracionInvalida) as error:
        cfg.leer(d)
    assert len(error.value.problemas) >= 4
    assert "falta nombre" in str(error.value) and "pools[0].canal" in str(error.value)


def test_un_documento_que_no_es_un_objeto_se_rechaza():
    with pytest.raises(cfg.ConfiguracionInvalida):
        cfg.leer([documento()])


# --- el hash --------------------------------------------------------------------------------------


def test_el_mismo_contenido_en_otro_orden_es_el_mismo_hash():
    """Golden 134: el orden de las listas, el nombre y los supuestos no cambian el hash."""
    a = cfg.leer(documento())
    d = documento()
    rng = random.Random(7)
    for llave in ("admisibilidad", "pools", "restricciones"):
        rng.shuffle(d[llave])
    d["nombre"], d["supuestos"] = "Otro nombre", None
    assert cfg.leer(d).hash() == a.hash()


@pytest.mark.parametrize(
    "cambio",
    [
        lambda d: d["pools"][0].update(capacidad=601),
        lambda d: d["pools"][2].update(capacidad=8),
        lambda d: d["pools"][1].update(unidades_recurso_por_asignacion=5),
        lambda d: d["pools"][1].update(activo=False),
        lambda d: d["admisibilidad"][4].update(permitido=True),
        lambda d: d["restricciones"][1].update(limite=4_501),
        lambda d: d["restricciones"][0].update(activa=False),
        lambda d: d.update(permitir_predicciones_con_imputacion=True),
        lambda d: d.update(max_cuentas_atendidas=None),
        lambda d: d.update(max_unidades_recurso=1_501),
    ],
)
def test_cada_dato_que_cambia_un_plan_cambia_el_hash(cambio):
    """Golden 134: una capacidad distinta (o cualquier otro dato del plan) es otro hash."""
    d = documento()
    cambio(d)
    assert cfg.leer(d).hash() != cfg.leer(documento()).hash()


def test_la_politica_tiene_su_hash_fijo():
    assert hash_politica() == HASH_DE_PUBLIC_V1
    assert hash_politica(PUBLIC_V1) == hashlib.sha256(canonico(PUBLIC_V1.definicion())).hexdigest()


@pytest.mark.parametrize(
    "cambio",
    [
        {"escala_valor": 1},
        {"decimales_contacto": 6},
        {"segundos_por_nivel": 60.0},
        {"mip_rel_gap": 1e-4},
        {"presolve": True},
        {"reglas": {**PUBLIC_V1.reglas, "no_causal": "otra cosa"}},
    ],
)
def test_cambiar_la_politica_cambia_su_hash(cambio):
    """Golden 135."""
    assert hash_politica(dataclasses.replace(PUBLIC_V1, **cambio)) != HASH_DE_PUBLIC_V1


def test_cambiar_la_formula_o_un_objetivo_cambia_el_hash():
    """Golden 135: la definicion canonica lleva la formula del valor y cada objetivo; otra formula
    (multiplicar por p_recuperacion) u otro orden de los objetivos es otro hash."""
    definicion = PUBLIC_V1.definicion()
    otra_formula = copy.deepcopy(definicion)
    otra_formula["valor"]["formula"] += " * p_recuperacion_positiva_30d"
    otro_orden = copy.deepcopy(definicion)
    otro_orden["objetivos"][0], otro_orden["objetivos"][1] = (
        otro_orden["objetivos"][1],
        otro_orden["objetivos"][0],
    )
    for otra in (otra_formula, otro_orden):
        assert hashlib.sha256(canonico(otra)).hexdigest() != HASH_DE_PUBLIC_V1
    assert "p_recuperacion_positiva_30d" in definicion["valor"]["no_se_multiplica_por"]


def test_los_productos_son_los_del_contrato_de_cartera():
    assert set(PRODUCTOS) == set(PRODUCTOS_DE_CARTERA)


def test_las_advertencias():
    d = documento()
    d["admisibilidad"].append(
        {"canal_decision": "CAMPO", "canal_operativo": "TELEFONICA", "permitido": True}
    )
    d["pools"] = [p for p in d["pools"] if p["canal"] != "TELEFONICA"]
    d["pools"].append({**documento()["pools"][1], "codigo": "TELEFONICA_X", "activo": False})
    d["restricciones"][0]["activa"] = False
    avisos = cfg.advertencias(cfg.leer(d))
    assert "el canal TELEFONICA es admisible y no tiene ningun pool activo" in avisos
    assert "la restriccion MIN_MUY_ALTA esta inactiva" in avisos
    d = documento()
    d["admisibilidad"] = [a for a in d["admisibilidad"] if a["canal_operativo"] != "CAMPO"]
    avisos = cfg.advertencias(cfg.leer(d))
    assert "el pool CAMPO_G001 es de CAMPO, que ningun canal de decision admite" in avisos


# --- registrar y publicar -------------------------------------------------------------------------


def _registrar(d: dict, *, publicar: bool) -> cfg.Registrada:
    with sesion() as s:
        registrada = cfg.registrar(s, cfg.leer(d), publicar=publicar)
        s.commit()
        s.refresh(registrada.version)
        return registrada


def _cuantas(tabla: str) -> int:
    with sesion() as s:
        return s.execute(text(f"SELECT count(*) FROM {tabla}")).scalar_one()


def test_registrar_reutiliza_el_mismo_contenido_y_publica(bd):
    borrador = _registrar(documento(), publicar=False)
    v = borrador.version
    assert (borrador.creada, v.estado, v.publicada_en) == (True, "BORRADOR", None)
    assert (v.pools, v.pools_activos, v.restricciones, v.restricciones_activas) == (3, 3, 2, 2)
    assert v.hash_contenido == cfg.leer(documento()).hash()
    assert v.metadatos["pools_por_canal"] == {"DIGITAL": 1, "TELEFONICA": 1, "CAMPO": 1}
    assert v.metadatos["supuestos"].startswith("SUPUESTOS SINTETICOS")
    otra = documento()
    otra["nombre"] = "El mismo contenido con otro nombre"
    reutilizada = _registrar(otra, publicar=False)
    assert (reutilizada.creada, reutilizada.version.id) == (False, v.id)
    publicada = _registrar(documento(), publicar=True)
    assert (publicada.creada, publicada.publicada_ahora, publicada.version.estado) == (
        False,
        True,
        "PUBLICADA",
    )
    with sesion() as s:
        otra_vez = cfg.publicar(s, v.configuracion_id)
        s.commit()
        assert (otra_vez.publicada_ahora, otra_vez.version.estado) == (False, "PUBLICADA")
        leida = cfg.configuracion_de(s, otra_vez.version)
    assert leida.hash() == v.hash_contenido and leida == cfg.leer(documento())
    assert (_cuantas("pool_capacidad_optimizacion"), _cuantas("restriccion_optimizacion")) == (3, 2)
    assert _cuantas("admisibilidad_canal_optimizacion") == 5


def test_publicar_una_que_no_existe(bd):
    with sesion() as s, pytest.raises(cfg.ConfiguracionNoEncontrada):
        cfg.publicar(s, "00000000-0000-4000-8000-000000000000")


def test_dos_registros_simultaneos_del_mismo_contenido_dan_una_version(bd):
    barrera = threading.Barrier(2)
    salidas: list = []

    def registrar_a_la_vez():
        with sesion() as s:
            barrera.wait(10)
            salidas.append(cfg.registrar(s, cfg.leer(documento()), publicar=True))
            s.commit()

    hilos = [threading.Thread(target=registrar_a_la_vez) for _ in range(2)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(30)
    assert len(salidas) == 2
    assert sorted(r.creada for r in salidas) == [False, True]
    assert _cuantas("version_configuracion_optimizacion") == 1
    assert _cuantas("pool_capacidad_optimizacion") == 3


@pytest.mark.parametrize(
    "sentencia, disparador",
    [
        (
            "UPDATE version_configuracion_optimizacion SET max_cuentas_atendidas = 1",
            "tr_configuracion_optimizacion_ciclo",
        ),
        ("UPDATE version_configuracion_optimizacion SET estado = 'BORRADOR'", None),
        ("DELETE FROM version_configuracion_optimizacion", "tr_configuracion_optimizacion_ciclo"),
        ("UPDATE pool_capacidad_optimizacion SET capacidad = 1", "tr_optimizacion_inmutable"),
        ("DELETE FROM restriccion_optimizacion", "tr_optimizacion_inmutable"),
        ("UPDATE admisibilidad_canal_optimizacion SET permitido = true", None),
    ],
)
def test_una_configuracion_registrada_no_se_reescribe(bd, sentencia, disparador):
    _registrar(documento(), publicar=True)
    with pytest.raises(DBAPIError) as error, sesion() as s:
        s.execute(text(sentencia))
    esperado = disparador or (
        "tr_optimizacion_inmutable"
        if "admisibilidad" in sentencia
        else "tr_configuracion_optimizacion_ciclo"
    )
    assert restriccion_violada(error.value) == esperado


def test_un_borrador_solo_pasa_a_publicada(bd):
    v = _registrar(documento(), publicar=False).version
    with sesion() as s:
        s.execute(
            text(
                "UPDATE version_configuracion_optimizacion SET estado = 'PUBLICADA', "
                "publicada_en = now() WHERE id = :v"
            ),
            {"v": v.id},
        )
        s.commit()
    with pytest.raises(DBAPIError) as error, sesion() as s:
        s.execute(
            text("UPDATE version_configuracion_optimizacion SET estado = 'BORRADOR' WHERE id = :v"),
            {"v": v.id},
        )
    assert restriccion_violada(error.value) == "tr_configuracion_optimizacion_ciclo"
