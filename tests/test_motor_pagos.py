"""El motor de pagos contra PostgreSQL: lo que concluye de cada pago observado, los movimientos que
publica, la conciliacion con la cuenta y lo que nunca hace (borrar, fusionar a ciegas, elegir)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from historia_escenarios import (
    Cuenta,
    cliente,
    historia_de,
    ingerir_corte,
    ingerir_pagos_de,
    pago,
)
from motor_pagos_escenarios import (
    cuantos,
    ejecuciones,
    historiar_todo,
    interpretar_todo,
    movimientos,
    publicar,
    resultados,
)
from sqlmodel import select

from motor_cartera.db.modelos import (
    CuentaCanonica,
    EjecucionMotorPagos,
    MovimientoEconomicoCanonico,
    PagoObservado,
    TipoTrabajo,
    TrabajoOrquestacion,
)
from motor_cartera.db.sesion import sesion
from motor_cartera.motor_pagos.firmas import movimiento_id

pytestmark = pytest.mark.usefixtures("bd")

SEPTIEMBRE = date(2026, 9, 1)
OCTUBRE = date(2026, 10, 1)


# --- la interpretacion se abre con los pagos observados -------------------------------------------


def test_publicar_los_pagos_observados_abre_su_ventana_en_la_misma_transaccion(tmp_path):
    ingesta = ingerir_pagos_de(
        tmp_path,
        "pagos.csv",
        [pago(1, "2026-09-03 10:00:00", "500.00"), pago(2, "2026-10-02 11:00:00", "300.00")],
    )
    # Antes de la historia no hay nada que interpretar: los pagos observados todavia no existen.
    assert ejecuciones() == []

    from motor_cartera.historia.ejecuciones import materializar

    materializar(historia_de(ingesta=ingesta).id)

    abiertas = ejecuciones()
    assert [(e.periodo_desde, e.periodo_hasta, e.estado) for e in abiertas] == [
        (SEPTIEMBRE, OCTUBRE, "EN_PROCESO"),
        (OCTUBRE, date(2026, 11, 1), "EN_PROCESO"),
    ]
    assert {e.version_motor for e in abiertas} == {"motor-pagos/v1"}
    with sesion() as s:
        trabajos = s.exec(
            select(TrabajoOrquestacion).where(TrabajoOrquestacion.tipo == TipoTrabajo.MOTOR_PAGOS)
        ).all()
    # Un trabajo MOTOR_PAGOS por ventana, de ningun flujo, esperando a un worker.
    assert sorted(t.ejecucion_motor_pagos_id for t in trabajos) == [e.id for e in abiertas]
    assert {(t.estado, t.flujo_id) for t in trabajos} == {("PENDIENTE", None)}
    assert "2 ventana(s) del motor de pagos" in historia_de(ingesta=ingesta).detalle


def test_un_pago_normal_es_un_movimiento_primario_conciliado_con_su_cuenta(tmp_path):
    historiar_todo_con = ingerir_corte(tmp_path, date(2026, 9, 2), [Cuenta(1), Cuenta(2)])
    del historiar_todo_con
    historiar_todo()
    ingesta = ingerir_pagos_de(tmp_path, "pagos.csv", [pago(1, "2026-09-03 10:00:00", "500.00")])

    (ejecucion,) = publicar(ingesta)

    assert (ejecucion.estado, ejecucion.resultado) == ("EXITOSA", "INTERPRETACION_PUBLICADA")
    (visto,) = resultados(ejecucion)
    assert visto.clasificacion == "MOVIMIENTO_PRIMARIO"
    assert visto.resultado.estado_conciliacion == "CONCILIADO_CUENTA"
    assert visto.codigos == ["OBSERVACION_UNICA"]
    (movimiento,) = movimientos(ejecucion)
    assert visto.movimiento.id == movimiento.id
    assert (movimiento.tipo_movimiento, movimiento.signo_economico) == ("PAGO", "SUMA")
    assert movimiento.monto_reportado == Decimal("500.00")
    assert movimiento.observaciones == 1
    assert movimiento.estado_conciliacion == "CONCILIADO_CUENTA"
    assert movimiento.movimiento_id == movimiento_id("motor-pagos/v1", movimiento.firma_exacta)
    with sesion() as s:
        cuenta = s.exec(
            select(CuentaCanonica).where(CuentaCanonica.cliente_unico == cliente(1))
        ).one()
    assert movimiento.cuenta_canonica_id == cuenta.id
    assert (
        ejecucion.observaciones_leidas,
        ejecucion.observaciones_clasificadas,
        ejecucion.movimientos_canonicos,
        ejecucion.primarios,
    ) == (1, 1, 1, 1)
    assert ejecucion.recuperacion_bruta_interpretada == Decimal("500.00")
    assert ejecucion.recuperacion_neta_interpretada == Decimal("500.00")
    assert len(ejecucion.firma_entrada) == 64


def test_dos_filas_identicas_son_dos_observaciones_y_un_solo_movimiento(tmp_path):
    fila = pago(6, "2026-09-08 09:15:00", "300.00")
    ingesta = ingerir_pagos_de(tmp_path, "pagos.csv", [fila, dict(fila)])

    (ejecucion,) = publicar(ingesta)

    # Las dos observaciones siguen ahi, cada una con su resultado; el dinero cuenta una vez.
    assert cuantos(PagoObservado) == 2
    vistos = resultados(ejecucion)
    assert sorted(v.clasificacion for v in vistos) == ["DUPLICADO_EXACTO", "MOVIMIENTO_PRIMARIO"]
    (movimiento,) = movimientos(ejecucion)
    assert {v.movimiento.id for v in vistos} == {movimiento.id}
    assert movimiento.observaciones == 2
    primario, copia = sorted(vistos, key=lambda v: v.clasificacion != "MOVIMIENTO_PRIMARIO")
    assert primario.codigos == ["REPRESENTANTE_DE_COPIAS"]
    assert primario.resultado.motivos[0]["copias"] == 1
    assert copia.codigos == ["COPIA_EXACTA"]
    assert copia.resultado.motivos[0]["representante"] == str(primario.pago.pago_observado_id)
    # El representante es el de la fila menor del mismo archivo: no el primero que leyo el motor.
    assert primario.pago.source_row < copia.pago.source_row
    assert primario.resultado.firma_exacta == copia.resultado.firma_exacta
    assert ejecucion.recuperacion_bruta_interpretada == Decimal("300.00")
    assert (ejecucion.duplicados_exactos, ejecucion.grupos_exactos) == (1, 1)
    assert (ejecucion.grupos_legacy, ejecucion.grupos_ambiguos) == (1, 0)


def test_tres_copias_exactas_son_un_solo_hecho_economico(tmp_path):
    fila = pago(6, "2026-09-08 09:15:00", "300.00")
    ingesta = ingerir_pagos_de(tmp_path, "pagos.csv", [fila, dict(fila), dict(fila)])

    (ejecucion,) = publicar(ingesta)

    vistos = resultados(ejecucion)
    assert len(vistos) == cuantos(PagoObservado) == 3
    assert sorted(v.clasificacion for v in vistos) == [
        "DUPLICADO_EXACTO",
        "DUPLICADO_EXACTO",
        "MOVIMIENTO_PRIMARIO",
    ]
    (movimiento,) = movimientos(ejecucion)
    assert movimiento.observaciones == 3
    assert ejecucion.recuperacion_bruta_interpretada == Decimal("300.00")
    assert ejecucion.duplicados_exactos == 2


def test_la_misma_llave_historica_con_otro_gestor_es_ambigua_y_no_desaparece(tmp_path):
    # Mismo cliente, mismo segundo, mismo importe; otro gestor. La llave del sistema anterior los
    # juntaba: aqui no se fusionan ni se cuentan.
    ingesta = ingerir_pagos_de(
        tmp_path,
        "pagos.csv",
        [
            pago(1, "2026-09-14 16:45:00", "1000.00", Gestor="GESTOR 007"),
            pago(1, "2026-09-14 16:45:00", "1000.00", Gestor="GESTOR 012"),
        ],
    )

    (ejecucion,) = publicar(ingesta)

    vistos = resultados(ejecucion)
    assert [v.clasificacion for v in vistos] == ["COINCIDENCIA_AMBIGUA"] * 2
    assert all(v.movimiento is None for v in vistos)
    (motivo,) = vistos[0].resultado.motivos
    assert motivo == {
        "codigo": "LLAVE_HISTORICA_COMPARTIDA",
        "observaciones": 2,
        "firmas_exactas": 2,
        "campos_distintos": ["Gestor"],
    }
    assert movimientos(ejecucion) == []
    assert cuantos(PagoObservado) == 2
    assert ejecucion.recuperacion_bruta_interpretada == Decimal("0.00")
    assert ejecucion.importe_ambiguo_observado == Decimal("2000.00")
    assert (ejecucion.coincidencias_ambiguas, ejecucion.grupos_ambiguos) == (2, 1)
    # Las dos huellas: distintas firmas exactas, la misma legacy.
    assert vistos[0].resultado.firma_legacy == vistos[1].resultado.firma_legacy
    assert vistos[0].resultado.firma_exacta != vistos[1].resultado.firma_exacta


def test_un_pago_sin_cuenta_se_interpreta_igual_y_no_crea_ninguna(tmp_path):
    ingesta = ingerir_pagos_de(tmp_path, "pagos.csv", [pago(9, "2026-09-01 12:00:00", "320.00")])

    (ejecucion,) = publicar(ingesta)

    (visto,) = resultados(ejecucion)
    assert visto.clasificacion == "MOVIMIENTO_PRIMARIO"
    assert visto.resultado.estado_conciliacion == "SIN_CUENTA_OBSERVADA"
    (movimiento,) = movimientos(ejecucion)
    assert movimiento.estado_conciliacion == "SIN_CUENTA_OBSERVADA"
    assert movimiento.cuenta_canonica_id is None
    assert cuantos(CuentaCanonica) == 0
    assert ejecucion.sin_cuenta_observada == 1
    assert ejecucion.recuperacion_bruta_interpretada == Decimal("320.00")


def test_dos_pagos_legitimos_iguales_del_mismo_dia_no_se_fusionan(tmp_path):
    # El mismo cliente paga el mismo importe dos veces el mismo dia, a horas distintas: son dos
    # pagos, y la hora de recepcion es la evidencia que los distingue.
    ingesta = ingerir_pagos_de(
        tmp_path,
        "pagos.csv",
        [
            pago(5, "2026-09-13 10:00:00", "600.00"),
            pago(5, "2026-09-13 17:30:00", "600.00"),
            pago(5, "2026-09-13 17:30:00", "250.00"),
        ],
    )

    (ejecucion,) = publicar(ingesta)

    vistos = resultados(ejecucion)
    assert [v.clasificacion for v in vistos] == ["MOVIMIENTO_PRIMARIO"] * 3
    assert len(movimientos(ejecucion)) == 3
    assert ejecucion.recuperacion_bruta_interpretada == Decimal("1450.00")
    assert ejecucion.grupos_legacy == 0


def test_el_motor_no_toca_ningun_pago_observado(tmp_path):
    fila = pago(6, "2026-09-08 09:15:00", "300.00")
    ingesta = ingerir_pagos_de(
        tmp_path,
        "pagos.csv",
        [fila, dict(fila), pago(6, "2026-09-09 09:15:00", "-300.00"), pago(1, "2026-09-02", "1")],
    )
    historiar_todo()
    with sesion() as s:
        antes = [p.model_dump() for p in s.exec(select(PagoObservado)).all()]

    interpretar_todo()

    with sesion() as s:
        despues = [p.model_dump() for p in s.exec(select(PagoObservado)).all()]
    assert sorted(despues, key=str) == sorted(antes, key=str)
    assert ingesta.estado == "EXITOSA"
    with sesion() as s:
        assert s.exec(select(EjecucionMotorPagos.estado)).all() == ["EXITOSA"]
        assert len(s.exec(select(MovimientoEconomicoCanonico)).all()) == 3
