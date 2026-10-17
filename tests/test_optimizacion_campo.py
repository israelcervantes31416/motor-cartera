"""optimization/v1 con CAMPO, contra PostgreSQL: solo una parada PROGRAMADA del ruteo/v2 declarado
puede ir a CAMPO, y solo con su propio gestor; la capacidad efectiva de un gestor; los pools y las
restricciones por BASE, de la cadena del ruteo; lo que no es de la misma cadena no se abre.

El escenario se construye una vez por modulo (optimizacion_escenarios.escenario_campo): doce
cuentas, tres bases y cuatro gestores. campo/v2 y ruteo/v2 dejan:

- GESTOR_A1 (BASE_A): la 2 y la 3, PROGRAMADA; GESTOR_A2 (BASE_A): la 1 y la 4, PROGRAMADA;
- GESTOR_C (BASE_B, sin snap valido): la 5 y la 6, NO_ENRUTABLE_BASE;
- GESTOR_B (BASE_C, media hora): la 7 PROGRAMADA, la 8 SIN_TIEMPO y la 10 NO_ENRUTABLE;
- y no son de CAMPO la 9 (DIGITAL, de BASE_A), la 11 (TELEFONICA, BASE_A) y la 12 (TELEFONICA,
  BASE_C).

Las que no se pueden programar (5, 6, 8 y 10) son las que mas valen.
"""

from __future__ import annotations

import pytest
from optimizacion_escenarios import (
    CAMPO,
    JUEVES,
    CuentaOpt,
    abrir,
    admisible,
    decision_de,
    escenario_campo,
    optimizar,
    plan,
    pool,
    registrar,
    restriccion,
    restricciones,
    resultados,
    scoring_sintetico,
    usos,
)
from sqlalchemy import text

from motor_cartera.db.sesion import sesion
from motor_cartera.optimizacion import ejecuciones as motor

A1, A2, B, C = "CAMPO_A1", "CAMPO_A2", "CAMPO_B", "CAMPO_C"
D, TA = "DIGITAL_GLOBAL", "TELEFONICA_BASE_A"
PROGRAMADAS = {1: "GESTOR_A2", 2: "GESTOR_A1", 3: "GESTOR_A1", 4: "GESTOR_A2", 7: "GESTOR_B"}


@pytest.fixture(scope="module")
def c(bd_del_modulo, tmp_path_factory):
    e = escenario_campo(tmp_path_factory.mktemp("optimizacion_campo"))
    # Lo que campo/v2 (rendezvous hashing, determinista) y ruteo/v2 dejaron: las goldens de abajo
    # se calcularon a mano con esto.
    estados = {n: (p["estado"], p["gestor"]) for n, p in e.paradas.items()}
    assert estados == {
        **{n: ("PROGRAMADA", g) for n, g in PROGRAMADAS.items()},
        5: ("NO_ENRUTABLE_BASE", "GESTOR_C"),
        6: ("NO_ENRUTABLE_BASE", "GESTOR_C"),
        8: ("SIN_TIEMPO", "GESTOR_B"),
        10: ("NO_ENRUTABLE", "GESTOR_B"),
    }
    return e


def documento_campo(**cambios) -> dict:
    """Un pool por gestor (A1 sin capacidad declarada: sus PROGRAMADA; A2 con 1; C con 5 y ninguna
    PROGRAMADA), DIGITAL global y TELEFONICA de BASE_A. Cada canal de decision admite solo el suyo.

    El optimo, a mano: A1 lleva la 2 y la 3; A2, solo una de la 1 y la 4: la 1 (100.00); B, la 7;
    DIGITAL, la 9; TELEFONICA de BASE_A, la 11. La 12 es TELEFONICA de BASE_C: ningun pool la
    alcanza. 420.00, contacto 3.3, 4 x 10 + 1 + 4 = 45 unidades."""
    return {
        "nombre": "Golden con CAMPO",
        "permitir_predicciones_con_imputacion": False,
        "admisibilidad": [
            admisible("CAMPO", "CAMPO"),
            admisible("DIGITAL", "DIGITAL"),
            admisible("TELEFONICA", "TELEFONICA"),
        ],
        "pools": [
            pool(A1, "CAMPO", None, 10, alcance="GESTOR_CAMPO", referencia="GESTOR_A1"),
            pool(A2, "CAMPO", 1, 10, alcance="GESTOR_CAMPO", referencia="GESTOR_A2"),
            pool(B, "CAMPO", None, 10, alcance="GESTOR_CAMPO", referencia="GESTOR_B"),
            pool(C, "CAMPO", 5, 10, alcance="GESTOR_CAMPO", referencia="GESTOR_C"),
            pool(D, "DIGITAL", 5, 1),
            pool(TA, "TELEFONICA", 5, 4, alcance="BASE", referencia="BASE_A"),
        ],
        "restricciones": [],
        **cambios,
    }


def test_solo_una_parada_programada_va_a_campo(c):
    """Golden 115: PROGRAMADA puede ir a CAMPO; SIN_TIEMPO, NO_ENRUTABLE y NO_ENRUTABLE_BASE no,
    aunque valgan mas que todas."""
    e = optimizar(c.scoring, c.decision, registrar(documento_campo()), ruteo=c.ruteo)
    assert (e.estado, e.resultado) == ("EXITOSA", "PLAN_PUBLICADO"), e.detalle
    assert plan(e.id) == {
        1: A2,
        2: A1,
        3: A1,
        4: None,
        5: None,
        6: None,
        7: B,
        8: None,
        9: D,
        10: None,
        11: TA,
        12: None,
    }
    assert (e.objetivo_primario_centavos, e.objetivo_secundario, e.unidades_recurso) == (
        42_000,
        330_000_000,
        45,
    )
    publicados = resultados(e.id)
    for n in (5, 6, 8, 10):
        assert (publicados[n]["estado"], publicados[n]["motivo"]) == (
            "NO_ELEGIBLE",
            "CAMPO_NO_PROGRAMADA",
        ), n
    assert (publicados[4]["estado"], publicados[4]["motivo"]) == (
        "NO_SELECCIONADA",
        "FUERA_DE_SOLUCION_OPTIMA",
    )
    assert (publicados[12]["estado"], publicados[12]["motivo"]) == (
        "NO_ELEGIBLE",
        "SIN_POOL_APLICABLE",
    )
    for n, gestor in PROGRAMADAS.items():
        if publicados[n]["canal_asignado"] == "CAMPO":
            # Su propio gestor, y el orden de su ruta, que el plan no cambia.
            assert publicados[n]["gestor"] == gestor
            assert publicados[n]["orden_ruteo_original"] == c.paradas[n]["orden_visita"]
    assert publicados[9]["orden_ruteo_original"] is None and publicados[9]["gestor"] is None
    assert {n: publicados[n]["base"] for n in (1, 9, 11, 12)} == {
        1: "BASE_A",
        9: "BASE_A",
        11: "BASE_A",
        12: "BASE_C",
    }
    u = usos(e.id)
    # La capacidad efectiva de CAMPO es la menor de la declarada y las PROGRAMADA del gestor.
    assert {
        k: (u[k]["capacidad_declarada"], u[k]["capacidad_efectiva"]) for k in (A1, A2, B, C)
    } == {
        A1: (None, 2),
        A2: (1, 1),
        B: (None, 1),
        C: (5, 0),
    }
    assert {k: u[k]["utilizada"] for k in u} == {A1: 2, A2: 1, B: 1, C: 0, D: 1, TA: 1}
    assert u[A2]["elegibles"] == 2 and u[C]["elegibles"] == 0
    assert e.resumen["por_canal"]["CAMPO"]["utilizada"] == 4


def test_una_cuenta_de_campo_no_va_con_otro_gestor(c):
    """Golden 116: sin el pool de GESTOR_A1, la 2 y la 3 (PROGRAMADA de A1) no van con A2 aunque A2
    tenga lugar: sus propias cuentas (1 y 4) no son elegibles en este scoring (CON_IMPUTACION)."""
    imputadas = tuple(
        CuentaOpt(x.numero, x.canal, x.prioridad, valor=x.valor, contacto=x.contacto, imputada=True)
        if x.numero in (1, 4)
        else x
        for x in CAMPO
    )
    scoring = scoring_sintetico(c.modelo, imputadas, etiqueta="campo con imputadas")
    documento = documento_campo(
        pools=[
            pool(
                A1, "CAMPO", None, 10, alcance="GESTOR_CAMPO", referencia="GESTOR_A1", activo=False
            ),
            pool(A2, "CAMPO", None, 10, alcance="GESTOR_CAMPO", referencia="GESTOR_A2"),
            pool(B, "CAMPO", None, 10, alcance="GESTOR_CAMPO", referencia="GESTOR_B"),
        ],
    )
    e = optimizar(scoring, c.decision, registrar(documento), ruteo=c.ruteo)
    assert e.estado == "EXITOSA", e.detalle
    publicados = resultados(e.id)
    for n in (2, 3):
        assert (publicados[n]["estado"], publicados[n]["motivo"]) == (
            "NO_ELEGIBLE",
            "SIN_POOL_APLICABLE",
        )
    for n in (1, 4):
        assert publicados[n]["motivo"] == "PREDICCION_CON_IMPUTACION"
    u = usos(e.id)
    assert (u[A2]["capacidad_efectiva"], u[A2]["utilizada"], u[A2]["elegibles"]) == (2, 0, 0)
    assert A1 not in u  # un pool inactivo no entra a la ejecucion
    assert plan(e.id)[7] == B


PASO = (
    "CREATE TEMP TABLE po_resultado ON COMMIT DROP AS SELECT * FROM resultado_optimizacion_cuenta "
    "WHERE ejecucion_optimizacion_id = :e"
)


def test_la_ultima_revision_no_publica_campo_fuera_de_su_gestor(c):
    """Antes de publicar, la tabla de paso se revisa contra el ruteo, por conjuntos: el plan optimo
    pasa; una cuenta de CAMPO en el pool de otro gestor (aunque su gestor y su orden sean los de su
    parada), con otro gestor, con otro orden o en una parada que no es PROGRAMADA, no."""
    # El plan de la golden 115 (el hash no lleva el nombre: otra version necesita otro contenido).
    documento = documento_campo(
        restricciones=[restriccion("MAX_BASE_A", "MAX_CUENTAS", "BASE", "BASE_A", 99)]
    )
    e = optimizar(c.scoring, c.decision, registrar(documento), ruteo=c.ruteo)
    assert e.estado == "EXITOSA", e.detalle
    publicados = resultados(e.id)
    uno, dos, siete, ocho = (publicados[n] for n in (1, 2, 7, 8))
    assert (uno["pool"], dos["pool"], siete["pool"], ocho["pool"]) == (A2, A1, B, None)
    cambios = (
        {"pool_capacidad_optimizacion_id": uno["pool_capacidad_optimizacion_id"]},
        {"gestor_campo_id": uno["gestor_campo_id"]},
        {"orden_ruteo_original": dos["orden_ruteo_original"] + 100},
    )
    with sesion() as s:
        s.execute(text(PASO), {"e": e.id})
        motor._verificar_campo(s, c.ruteo.id)
        for cambio in cambios:
            punto = s.begin_nested()
            ((columna, valor),) = cambio.items()
            s.execute(
                text(f"UPDATE po_resultado SET {columna} = :v WHERE cuenta_canonica_id = :c"),
                {"v": valor, "c": dos["cuenta_canonica_id"]},
            )
            with pytest.raises(motor._NoSePublica, match="1 cuentas en CAMPO"):
                motor._verificar_campo(s, c.ruteo.id)
            punto.rollback()
        # La 8 es SIN_TIEMPO de GESTOR_B: en el pool de GESTOR_B, con su gestor, tampoco.
        s.execute(
            text(
                "UPDATE po_resultado SET estado = 'ASIGNADA', canal_asignado = 'CAMPO', "
                "pool_capacidad_optimizacion_id = :pool, gestor_campo_id = :gestor "
                "WHERE cuenta_canonica_id = :c"
            ),
            {
                "pool": siete["pool_capacidad_optimizacion_id"],
                "gestor": siete["gestor_campo_id"],
                "c": ocho["cuenta_canonica_id"],
            },
        )
        with pytest.raises(motor._NoSePublica):
            motor._verificar_campo(s, c.ruteo.id)
        s.rollback()


def test_una_restriccion_por_base_sale_de_la_cadena_del_ruteo(c):
    """A lo mas 3 cuentas de BASE_A: entran la 1, la 2 y la 3 (las de mayor valor de BASE_A) y la
    7, de BASE_C. 330.00."""
    documento = documento_campo(
        restricciones=[restriccion("MAX_BASE_A", "MAX_CUENTAS", "BASE", "BASE_A", 3)]
    )
    e = optimizar(c.scoring, c.decision, registrar(documento), ruteo=c.ruteo)
    assert e.estado == "EXITOSA", e.detalle
    asignadas = {n: p for n, p in plan(e.id).items() if p is not None}
    assert asignadas == {1: A2, 2: A1, 3: A1, 7: B}
    assert e.objetivo_primario_centavos == 33_000
    maximo = restricciones(e.id)["MAX_BASE_A"]
    assert (maximo["lhs"], maximo["rhs"], maximo["holgura"], maximo["binding"]) == (3, 3, 0, True)
    assert (maximo["dimension"], maximo["valor_dimension"], maximo["operador"]) == (
        "BASE",
        "BASE_A",
        "<=",
    )


@pytest.mark.parametrize(
    "nuevo, hallazgo",
    [
        (pool("CAMPO_X", "CAMPO", 3, 10, alcance="GESTOR_CAMPO", referencia="GESTOR_X"), "GESTOR"),
        (pool("TELEFONICA_X", "TELEFONICA", 3, 4, alcance="BASE", referencia="BASE_X"), "BASE"),
    ],
)
def test_un_gestor_o_una_base_que_la_cadena_no_tiene_no_construye_ningun_modelo(c, nuevo, hallazgo):
    documento = documento_campo()
    documento["pools"] = [*documento["pools"], nuevo]
    e = optimizar(c.scoring, c.decision, registrar(documento), ruteo=c.ruteo)
    assert (e.estado, e.resultado, e.solver_status) == (
        "FALLIDA",
        "CONFIGURACION_INCOMPATIBLE",
        "NO_INVOCADO",
    )
    codigos = {h["codigo"] for h in e.diagnostico["hallazgos"]}
    assert f"{hallazgo}_DESCONOCIDO" in codigos or f"{hallazgo}_DESCONOCIDA" in codigos
    assert e.cuentas_publicadas == 0 and e.hash_plan is None


def test_un_ruteo_de_otra_decision_o_de_otro_dia_no_se_abre(c):
    """Golden 128: el ruteo/v2 declarado rutea un plan de campo/v2 de otra decision/v2 del mismo
    corte; o la fecha de operacion no es la del ruteo."""
    version = registrar(documento_campo(nombre="Golden con CAMPO, otra vez"))
    otra_decision = decision_de(CAMPO)
    with pytest.raises(motor.OptimizacionInvalida) as error:
        abrir(c.scoring, otra_decision, version, ruteo=c.ruteo)
    assert error.value.codigo == "RUTEO_DE_OTRA_DECISION"
    with pytest.raises(motor.OptimizacionInvalida) as error:
        abrir(c.scoring, c.decision, version, ruteo=c.ruteo, fecha=JUEVES.replace(day=2))
    assert error.value.codigo == "FECHA_OPERACION_DISTINTA"


def test_sin_campo_un_ruteo_declarado_da_la_base(c):
    """Una configuracion sin CAMPO puede declarar el ruteo: de su cadena sale la base de cada
    cuenta, para un pool por BASE. Las cuentas de CAMPO quedan sin canal admisible con pool."""
    documento = documento_campo(
        admisibilidad=[admisible("DIGITAL", "DIGITAL"), admisible("TELEFONICA", "TELEFONICA")],
        pools=[
            pool(D, "DIGITAL", 5, 1),
            pool(TA, "TELEFONICA", 5, 4, alcance="BASE", referencia="BASE_A"),
        ],
    )
    e = optimizar(c.scoring, c.decision, registrar(documento), ruteo=c.ruteo)
    assert e.estado == "EXITOSA", e.detalle
    asignadas = {n: p for n, p in plan(e.id).items() if p is not None}
    assert asignadas == {9: D, 11: TA}
    assert resultados(e.id)[1]["motivo"] == "SIN_CANAL_ADMISIBLE"
