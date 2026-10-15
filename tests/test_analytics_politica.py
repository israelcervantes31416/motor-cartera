"""El catalogo, la politica, los periodos y la supresion de analytics/v1, sin base: definiciones
formales, hashes que no dependen del orden ni del proceso, periodos con sus fronteras y la
privacidad de las celdas pequenas."""

from __future__ import annotations

import os
import random
import subprocess
import sys
from datetime import date

import pytest

from motor_cartera.analytics import catalogo, politica, supresion
from motor_cartera.analytics.catalogo import (
    CONJUNTOS,
    METRICAS,
    Conjunto,
    Familia,
    Fuente,
    Tipo,
    Universo,
    Vista,
)
from motor_cartera.analytics.politica import Granularidad, Periodo, periodos
from motor_cartera.analytics.supresion import COMPLEMENTARIA, PRIMARIA, Celda

# --- el catalogo ----------------------------------------------------------------------------------


def test_cada_metrica_tiene_una_definicion_formal_completa():
    assert len({m.codigo for m in METRICAS}) == len(METRICAS)
    for m in METRICAS:
        assert m.codigo.startswith(m.familia.value.lower() + "."), m.codigo
        assert m.nombre and m.formula and m.poblacion and m.regla_temporal and m.nulo
        assert Fuente.CORTE in m.fuentes, m.codigo
        assert Conjunto.TOTAL in m.conjuntos and Vista.ACUMULADA in m.vistas
        if m.tipo == Tipo.RAZON:
            assert len(m.componentes) == 2 and m.numerador and m.denominador
            assert not m.aditiva
        else:
            assert len(m.componentes) == 1 and m.numerador is None and m.denominador is None


def test_el_catalogo_cubre_lo_minimo_de_cada_familia():
    codigos = {m.codigo for m in METRICAS}
    minimos = {
        "cartera.cuentas",
        "cartera.saldo_total",
        "cartera.saldo_total_promedio",
        "cartera.dias_atraso_promedio",
        "cartera.saldo_atrasado_suma",
        "cartera.saldo_atrasado_cuentas_con_dato",
        "cartera.saldo_atrasado_cobertura",
        "recuperacion.neta",
        "recuperacion.movimientos_economicos",
        "recuperacion.cuentas_con_neto_positivo",
        "recuperacion.sobre_saldo_inicial",
        "calidad_pagos.movimientos_sin_cuenta",
        "calidad_pagos.monto_sin_cuenta",
        "gestion.gestiones",
        "gestion.cuentas_gestionadas",
        "gestion.cuentas_contactadas",
        "gestion.contactadas_sobre_gestionadas",
        "gestion.visitas_campo_registradas",
        "promesa.creadas",
        "promesa.monto_prometido",
        "promesa.evaluadas",
        "promesa.cumplidas",
        "promesa.parciales",
        "promesa.incumplidas",
        "promesa.pendientes",
        "promesa.canceladas",
        "promesa.no_evaluables",
        "promesa.tasa_cumplimiento_total",
        "promesa.tasa_cumplimiento_total_o_parcial",
        "atribucion.movimientos_evaluados",
        "atribucion.asociacion_unica",
        "atribucion.ambigua",
        "atribucion.sin_gestion",
        "atribucion.monto_asociacion_unica",
        "atribucion.monto_ambiguo",
        "atribucion.monto_sin_gestion",
        "decision.cuentas",
        "decision.pct_universo",
        "geografia.cuentas",
        "geografia.validas",
        "geografia.discrepancias",
        "geografia.sin_coordenadas",
        "geografia.incompletas",
        "geografia.invalidas",
        "geografia.fuera_referencia",
        "geografia.ambiguas",
        "geografia.tasa_valida",
        "territorial.cuentas",
        "territorial.asignadas",
        "territorial.revision",
        "territorial.sin_base",
        "territorial.ambiguas",
        "territorial.fuera_cobertura",
        "territorial.tasa_asignacion",
        "campo.candidatas",
        "campo.asignadas",
        "campo.revision_territorial",
        "campo.sin_gestor",
        "campo.sin_capacidad",
        "campo.rebalanceadas",
        "campo.capacidad_total",
        "campo.capacidad_utilizada",
        "campo.utilizacion",
        "campo.tasa_asignacion",
        "ruteo.rutas",
        "ruteo.rutas_completas",
        "ruteo.rutas_parciales",
        "ruteo.paradas_objetivo",
        "ruteo.programadas",
        "ruteo.sin_tiempo",
        "ruteo.no_enrutables",
        "ruteo.tasa_programacion",
        "ruteo.distancia_total_m",
        "ruteo.duracion_vial_total_s",
        "ruteo.duracion_servicio_total_s",
        "ruteo.duracion_espera_total_s",
    }
    assert minimos <= codigos


def test_ninguna_metrica_se_llama_eficiencia_ni_efecto():
    for m in METRICAS:
        texto = f"{m.codigo} {m.nombre} {m.formula}".lower()
        for palabra in ("eficiencia", "efecto", "roi", "causo", "probabilidad", "propension"):
            assert palabra not in texto, (m.codigo, palabra)


def test_la_tasa_de_cumplimiento_deja_fuera_pendientes_canceladas_y_no_evaluables():
    por_codigo = catalogo.POR_CODIGO
    total = por_codigo["promesa.tasa_cumplimiento_total"]
    parcial = por_codigo["promesa.tasa_cumplimiento_total_o_parcial"]
    assert total.componentes == ("p_cumplidas", "p_resueltas")
    assert parcial.componentes == ("p_cumplidas_o_parciales", "p_resueltas")
    for m in (total, parcial):
        for fuera in ("PENDIENTE", "CANCELADA", "NO_EVALUABLE"):
            assert fuera in m.denominador


def test_gestor_campo_no_es_una_dimension_de_lo_ocurrido():
    """El gestor de campo planificado no es el actor_ref de una gestion: no hay desglose de
    recuperacion, gestiones, promesas ni atribucion por GESTOR_CAMPO."""
    for m in METRICAS:
        if Conjunto.GESTOR_CAMPO in m.conjuntos:
            assert m.familia in (Familia.CAMPO, Familia.RUTEO), m.codigo
    assert "actor_ref" not in catalogo.canonico(catalogo.definicion()).decode().replace(
        "no el actor_ref del lifecycle", ""
    )


def test_los_conjuntos_existen_en_el_universo_de_su_metrica():
    for m in METRICAS:
        for c in m.conjuntos:
            universo = catalogo.universo_de_la_celda(m.universo, c)
            assert universo in CONJUNTOS[c].universos, (m.codigo, c)
    assert catalogo.universo_de_la_celda(Universo.COHORTE, Conjunto.CANAL_GESTION) == (
        Universo.GESTION
    )


def test_ninguna_dimension_es_una_coordenada_ni_un_microtramo():
    claves = {clave for d in CONJUNTOS.values() for clave in d.claves}
    for prohibida in ("latitud", "longitud", "punto", "tramo", "microtramo", "actor_ref"):
        assert prohibida not in claves


def _hash_en_otro_proceso(funcion: str) -> str:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            f"from motor_cartera.analytics import catalogo, politica; print({funcion}())",
        ],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "PYTHONHASHSEED": "12345"},
    ).stdout.strip()


def test_los_hashes_no_dependen_del_proceso():
    assert _hash_en_otro_proceso("catalogo.hash_catalogo") == catalogo.hash_catalogo()
    assert _hash_en_otro_proceso("politica.hash_politica") == politica.hash_politica()
    assert len(catalogo.hash_catalogo()) == len(politica.hash_politica()) == 64


def test_cambiar_una_formula_cambia_el_hash_del_catalogo(monkeypatch):
    antes = catalogo.hash_catalogo()
    primera = METRICAS[0]
    otra = catalogo.Metrica(**{**primera.__dict__, "formula": primera.formula + " "})
    monkeypatch.setattr(catalogo, "METRICAS", (otra, *METRICAS[1:]))
    assert catalogo.hash_catalogo() != antes


def test_cambiar_el_umbral_cambia_el_hash_de_la_politica():
    otra = politica.PoliticaAnalytics(**{**politica.PUBLIC_V1.__dict__, "min_group_size": 11})
    assert politica.hash_politica(otra) != politica.hash_politica()
    assert politica.PUBLIC_V1.min_group_size == 10


def test_el_hash_de_una_celda_no_depende_del_orden_de_sus_llaves():
    a = catalogo.dimension_hash({"producto": "CRÉDITO", "atraso_bucket": "91+"})
    b = catalogo.dimension_hash({"atraso_bucket": "91+", "producto": "CRÉDITO"})
    assert a == b
    assert catalogo.dimension_hash({}) != a
    assert (
        catalogo.canonico({"producto": "CRÉDITO", "atraso_bucket": "91+"}).decode()
        == '{"atraso_bucket":"91+","producto":"CRÉDITO"}'
    )


# --- los periodos ---------------------------------------------------------------------------------


def test_total_no_tiene_periodos():
    assert periodos(date(2026, 1, 1), date(2026, 1, 30), Granularidad.TOTAL) == []
    assert politica.horizonte_dias(date(2026, 1, 1), date(2026, 1, 30)) == 30


def test_los_dias_no_son_parciales():
    dias = periodos(date(2026, 2, 27), date(2026, 3, 1), Granularidad.DIA)
    assert dias == [
        Periodo(date(2026, 2, 27), date(2026, 2, 27), False),
        Periodo(date(2026, 2, 28), date(2026, 2, 28), False),
        Periodo(date(2026, 3, 1), date(2026, 3, 1), False),
    ]


def test_la_semana_iso_va_de_lunes_a_domingo_y_se_recorta_al_horizonte():
    # 2026-01-01 es jueves; 2026-01-11, domingo.
    semanas = periodos(date(2026, 1, 1), date(2026, 1, 11), Granularidad.SEMANA_ISO)
    assert semanas == [
        Periodo(date(2026, 1, 1), date(2026, 1, 4), True),
        Periodo(date(2026, 1, 5), date(2026, 1, 11), False),
    ]
    assert periodos(date(2026, 1, 5), date(2026, 1, 11), Granularidad.SEMANA_ISO) == [
        Periodo(date(2026, 1, 5), date(2026, 1, 11), False)
    ]


def test_el_mes_es_calendario_y_cruza_el_anio():
    meses = periodos(date(2025, 12, 15), date(2026, 2, 28), Granularidad.MES)
    assert meses == [
        Periodo(date(2025, 12, 15), date(2025, 12, 31), True),
        Periodo(date(2026, 1, 1), date(2026, 1, 31), False),
        Periodo(date(2026, 2, 1), date(2026, 2, 28), False),
    ]
    bisiesto = periodos(date(2028, 2, 1), date(2028, 2, 29), Granularidad.MES)
    assert bisiesto == [Periodo(date(2028, 2, 1), date(2028, 2, 29), False)]


@pytest.mark.parametrize("granularidad", list(Granularidad))
def test_los_periodos_cubren_el_horizonte_sin_huecos_ni_encimarse(granularidad):
    desde, hasta = date(2026, 1, 29), date(2026, 5, 3)
    lista = periodos(desde, hasta, granularidad)
    assert len(lista) == politica.cuantos_periodos(desde, hasta, granularidad)
    if granularidad == Granularidad.TOTAL:
        return
    assert lista[0].desde == desde and lista[-1].hasta == hasta
    for a, b in zip(lista, lista[1:], strict=False):
        assert (b.desde - a.hasta).days == 1


def test_un_as_of_anterior_al_corte_no_tiene_horizonte():
    with pytest.raises(ValueError):
        periodos(date(2026, 1, 2), date(2026, 1, 1), Granularidad.DIA)


# --- la supresion ---------------------------------------------------------------------------------


def _celda(conjunto: str, poblacion: int, universo: str = "COHORTE", **dimensiones) -> Celda:
    claves = CONJUNTOS[Conjunto(conjunto)].claves
    pares = tuple((clave, dimensiones[clave]) for clave in claves)
    return Celda(universo, conjunto, pares, catalogo.dimension_hash(dict(pares)), poblacion)


TOTAL = _celda("TOTAL", 20)


def test_nueve_se_suprime_y_diez_no():
    celdas = [
        TOTAL,
        _celda("PRODUCTO", 10, producto="X"),
        _celda("PRODUCTO", 9, producto="Y"),
        _celda("PRODUCTO", 1, producto="Z"),
    ]
    suprimidas = supresion.suprimir(celdas, 10)
    # Y y Z se suprimen por ser chicas; juntas suman 10, asi que X sigue visible.
    assert suprimidas == {celdas[2].llave: PRIMARIA, celdas[3].llave: PRIMARIA}
    assert supresion.violaciones(celdas, suprimidas, 10) == 0


def test_una_sola_celda_suprimida_arrastra_a_la_siguiente_mas_chica():
    celdas = [
        TOTAL,
        _celda("PRODUCTO", 25, producto="X"),
        _celda("PRODUCTO", 15, producto="W"),
        _celda("PRODUCTO", 5, producto="Y"),
    ]
    suprimidas = supresion.suprimir(celdas, 10)
    assert suprimidas == {celdas[3].llave: PRIMARIA, celdas[2].llave: COMPLEMENTARIA}
    # Sin la complementaria, TOTAL - X - W revelaria Y.
    assert supresion.violaciones(celdas, {celdas[3].llave: PRIMARIA}, 10) == 1


def test_dos_suprimidas_que_juntas_no_llegan_al_minimo_arrastran_otra():
    celdas = [
        TOTAL,
        _celda("ATRASO_BUCKET", 30, atraso_bucket="0"),
        _celda("ATRASO_BUCKET", 12, atraso_bucket="1-30"),
        _celda("ATRASO_BUCKET", 3, atraso_bucket="31-60"),
        _celda("ATRASO_BUCKET", 4, atraso_bucket="61-90"),
    ]
    suprimidas = supresion.suprimir(celdas, 10)
    assert suprimidas[celdas[3].llave] == suprimidas[celdas[4].llave] == PRIMARIA
    assert suprimidas[celdas[2].llave] == COMPLEMENTARIA
    assert celdas[1].llave not in suprimidas


def test_un_municipio_no_se_reconstruye_con_su_entidad():
    celdas = [
        TOTAL,
        _celda("ENTIDAD", 20, entidad="21"),
        _celda("MUNICIPIO", 15, municipio="21114"),
        _celda("MUNICIPIO", 5, municipio="21015"),
    ]
    suprimidas = supresion.suprimir(celdas, 10)
    assert suprimidas == {celdas[3].llave: PRIMARIA, celdas[2].llave: COMPLEMENTARIA}
    assert celdas[1].llave not in suprimidas


def test_un_cruce_se_protege_contra_sus_dos_marginales():
    def cruce(p: str, a: str, n: int) -> Celda:
        return _celda("PRODUCTO_X_ATRASO", n, producto=p, atraso_bucket=a)

    celdas = [
        _celda("TOTAL", 80),
        _celda("PRODUCTO", 40, producto="X"),
        _celda("PRODUCTO", 40, producto="Y"),
        _celda("ATRASO_BUCKET", 40, atraso_bucket="0"),
        _celda("ATRASO_BUCKET", 40, atraso_bucket="91+"),
        cruce("X", "0", 35),
        cruce("X", "91+", 5),
        cruce("Y", "0", 5),
        cruce("Y", "91+", 35),
    ]
    suprimidas = supresion.suprimir(celdas, 10)
    # Las dos chicas no bastan: cada fila y cada columna dejaria una sola. Se suprimen las cuatro.
    assert {k for k in suprimidas} == {c.llave for c in celdas[5:]}
    assert supresion.violaciones(celdas, suprimidas, 10) == 0


def test_un_gestor_no_se_reconstruye_con_su_base():
    celdas = [
        _celda("TOTAL", 40, "CAMPO"),
        _celda("BASE", 25, "CAMPO", base="BASE_A"),
        _celda("BASE", 15, "CAMPO", base="BASE_B"),
        _celda("GESTOR_CAMPO", 18, "CAMPO", base="BASE_A", gestor_campo="G1"),
        _celda("GESTOR_CAMPO", 7, "CAMPO", base="BASE_A", gestor_campo="G2"),
        _celda("GESTOR_CAMPO", 15, "CAMPO", base="BASE_B", gestor_campo="G3"),
    ]
    suprimidas = supresion.suprimir(celdas, 10)
    assert suprimidas[celdas[4].llave] == PRIMARIA
    assert suprimidas[celdas[3].llave] == COMPLEMENTARIA
    assert supresion.violaciones(celdas, suprimidas, 10) == 0


def test_total_no_se_suprime_nunca():
    celdas = [_celda("TOTAL", 3), _celda("PRODUCTO", 3, producto="X")]
    suprimidas = supresion.suprimir(celdas, 10)
    assert celdas[0].llave not in suprimidas and celdas[1].llave in suprimidas


def test_el_desempate_es_por_dimension_hash_y_no_por_el_orden():
    celdas = [
        TOTAL,
        _celda("PRODUCTO", 4, producto="Y"),
        _celda("PRODUCTO", 12, producto="A"),
        _celda("PRODUCTO", 12, producto="B"),
    ]
    una = supresion.suprimir(celdas, 10)
    otra = supresion.suprimir(list(reversed(celdas)), 10)
    assert una == otra
    elegida = min((celdas[2], celdas[3]), key=lambda c: c.dimension_hash)
    assert una[elegida.llave] == COMPLEMENTARIA


def _deducibles(celdas: list[Celda], suprimidas: dict) -> set:
    """Lo que un lector deduce de lo publicado sumando y restando: cada grupo es una ecuacion (su
    total es la suma de sus celdas, y el TOTAL del universo se publica siempre), y una ecuacion con
    una sola incognita la revela. Se repite hasta que ya no se deduce nada."""
    conocidas = {c.llave for c in celdas if c.llave not in suprimidas}
    ecuaciones = [
        ((g.total,) if g.total is not None else ()) + g.miembros for g in supresion.grupos(celdas)
    ]
    cambio = True
    while cambio:
        cambio = False
        for terminos in ecuaciones:
            incognitas = [t for t in terminos if t not in conocidas]
            if len(incognitas) == 1:
                conocidas.add(incognitas[0])
                cambio = True
    return conocidas


def _cruce(producto: str, tramo: str, poblacion: int) -> Celda:
    return _celda("PRODUCTO_X_ATRASO", poblacion, producto=producto, atraso_bucket=tramo)


def test_un_padre_visible_no_revela_a_su_unica_celda():
    """Y tiene un solo tramo. Si su unica celda del cruce se suprime para proteger a la chica de X,
    Y la revela, y con ella el tramo 0 revela la chica: el padre se suprime con ella."""
    celdas = [
        _celda("TOTAL", 55),
        _celda("PRODUCTO", 43, producto="X"),
        _celda("PRODUCTO", 12, producto="Y"),
        _celda("ATRASO_BUCKET", 15, atraso_bucket="0"),
        _celda("ATRASO_BUCKET", 40, atraso_bucket="91+"),
        _cruce("X", "0", 3),
        _cruce("X", "91+", 40),
        _cruce("Y", "0", 12),
    ]
    suprimidas = supresion.suprimir(celdas, 10)

    assert suprimidas[celdas[5].llave] == PRIMARIA
    assert suprimidas[celdas[2].llave] == COMPLEMENTARIA
    assert celdas[5].llave not in _deducibles(celdas, suprimidas)
    assert supresion.violaciones(celdas, suprimidas, 10) == 0
    # Con solo el cruce suprimido y los padres visibles, Y revela su unica celda, y el tramo 0
    # menos esa es la chica.
    solo_el_cruce = {c.llave: PRIMARIA for c in celdas[5:]}
    assert celdas[5].llave in _deducibles(celdas, solo_el_cruce)
    assert supresion.violaciones(celdas, solo_el_cruce, 10) > 0


def test_un_padre_suprimido_no_se_reconstruye_sumando_sus_celdas():
    """Y se suprime para proteger al producto chico X. Si sus dos celdas del cruce quedaran
    visibles, sumarlas daria Y, y TOTAL - Y - Z daria X: un grupo con el total suprimido tambien
    necesita una celda suprimida."""
    celdas = [
        _celda("TOTAL", 57),
        _celda("PRODUCTO", 3, producto="X"),
        _celda("PRODUCTO", 24, producto="Y"),
        _celda("PRODUCTO", 30, producto="Z"),
        _celda("ATRASO_BUCKET", 25, atraso_bucket="0"),
        _celda("ATRASO_BUCKET", 32, atraso_bucket="91+"),
        _cruce("X", "0", 2),
        _cruce("X", "91+", 1),
        _cruce("Y", "0", 12),
        _cruce("Y", "91+", 12),
        _cruce("Z", "0", 11),
        _cruce("Z", "91+", 19),
    ]
    suprimidas = supresion.suprimir(celdas, 10)
    chicas = {c.llave for c in celdas if c.conjunto != "TOTAL" and c.poblacion < 10}

    assert suprimidas[celdas[2].llave] == COMPLEMENTARIA
    assert {celdas[8].llave, celdas[9].llave} & set(suprimidas)
    assert not chicas & _deducibles(celdas, suprimidas)
    assert supresion.violaciones(celdas, suprimidas, 10) == 0
    # Con las dos celdas de Y visibles, X se deduce.
    sin_celdas_de_y = {
        k: v for k, v in suprimidas.items() if k not in {celdas[8].llave, celdas[9].llave}
    }
    assert celdas[1].llave in _deducibles(celdas, sin_celdas_de_y)
    assert supresion.violaciones(celdas, sin_celdas_de_y, 10) > 0


def test_una_celda_que_su_padre_ya_revela_no_protege_a_nadie():
    """Para proteger al gestor chico no se suprime G3, la unica de su base (su base la revela),
    sino G1: protege lo mismo y las bases siguen visibles."""
    celdas = [
        _celda("TOTAL", 40, "CAMPO"),
        _celda("BASE", 25, "CAMPO", base="BASE_A"),
        _celda("BASE", 15, "CAMPO", base="BASE_B"),
        _celda("GESTOR_CAMPO", 18, "CAMPO", base="BASE_A", gestor_campo="G1"),
        _celda("GESTOR_CAMPO", 7, "CAMPO", base="BASE_A", gestor_campo="G2"),
        _celda("GESTOR_CAMPO", 15, "CAMPO", base="BASE_B", gestor_campo="G3"),
    ]
    suprimidas = supresion.suprimir(celdas, 10)

    assert suprimidas == {celdas[4].llave: PRIMARIA, celdas[3].llave: COMPLEMENTARIA}


def test_ninguna_celda_chica_se_deduce_sumando_y_restando():
    """Tablas al azar (con semilla) de productos por tramo y de municipios por entidad: lo que se
    publica no deja deducir ninguna celda chica con sumas y restas de sus grupos."""
    for semilla in range(300):
        azar = random.Random(semilla)
        productos = ["A", "B", "C", "D"][: azar.randint(2, 4)]
        tramos = ["0", "1-30", "91+"][: azar.randint(2, 3)]
        cruces = {(p, t): azar.choice([0, 0, *range(1, 26)]) for p in productos for t in tramos}
        total = sum(cruces.values())
        otros = {m: azar.choice([0, *range(1, 26)]) for m in ("21001", "21002", "21003")}
        municipios = {**otros, "22001": total - sum(otros.values())}
        if not total or municipios["22001"] < 0:
            continue
        celdas = [_celda("TOTAL", total)]
        for p in productos:
            if n := sum(v for (q, _), v in cruces.items() if q == p):
                celdas.append(_celda("PRODUCTO", n, producto=p))
        for t in tramos:
            if n := sum(v for (_, u), v in cruces.items() if u == t):
                celdas.append(_celda("ATRASO_BUCKET", n, atraso_bucket=t))
        celdas += [_cruce(p, t, n) for (p, t), n in cruces.items() if n]
        for entidad in ("21", "22"):
            if n := sum(v for m, v in municipios.items() if m.startswith(entidad)):
                celdas.append(_celda("ENTIDAD", n, entidad=entidad))
        celdas += [_celda("MUNICIPIO", n, municipio=m) for m, n in municipios.items() if n]

        suprimidas = supresion.suprimir(celdas, 10)
        chicas = {c.llave for c in celdas if c.conjunto != "TOTAL" and c.poblacion < 10}

        assert chicas <= set(suprimidas), semilla
        assert not chicas & _deducibles(celdas, suprimidas), semilla
        assert supresion.violaciones(celdas, suprimidas, 10) == 0, semilla
        assert suprimidas == supresion.suprimir(list(reversed(celdas)), 10), semilla


def test_los_universos_no_se_mezclan():
    celdas = [
        TOTAL,
        _celda("PRODUCTO", 20, producto="X"),
        _celda("TOTAL", 12, "CAMPO"),
        _celda("PRIORIDAD_DECISION", 12, "CAMPO", prioridad_decision="MUY_ALTA"),
        _celda("PRIORIDAD_DECISION", 8, "COHORTE", prioridad_decision="MUY_ALTA"),
        _celda("PRIORIDAD_DECISION", 12, "COHORTE", prioridad_decision="ALTA"),
    ]
    suprimidas = supresion.suprimir(celdas, 10)
    assert celdas[3].llave not in suprimidas
    assert suprimidas[celdas[4].llave] == PRIMARIA
    assert suprimidas[celdas[5].llave] == COMPLEMENTARIA
