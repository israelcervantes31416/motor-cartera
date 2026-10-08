"""Reversos, ceros y huellas: el motor solo enlaza un reverso con su original si forman una pareja
aislada, y ante cualquier duda no elige. pagos/v1 no trae ninguna columna que diga cual es el
original de un reverso: la pareja se busca por cliente, importe y tiempo."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from historia_escenarios import ingerir_pagos_de, pago
from motor_pagos_escenarios import ejecuciones, movimientos, publicar, resultados, vigente

pytestmark = pytest.mark.usefixtures("bd")

AGOSTO = date(2026, 8, 1)
SEPTIEMBRE = date(2026, 9, 1)
OCTUBRE = date(2026, 10, 1)


def test_un_reverso_inequivoco_anula_su_pago_y_apunta_a_el(tmp_path):
    ingesta = ingerir_pagos_de(
        tmp_path,
        "pagos.csv",
        [
            pago(8, "2026-09-10 10:00:00", "400.00"),
            pago(8, "2026-09-12 09:00:00", "-400.00", **{"Concepto_Cálculo": "AJUSTE"}),
            pago(8, "2026-09-20 09:00:00", "150.00"),
        ],
    )

    (ejecucion,) = publicar(ingesta)

    original, reverso, otro = resultados(ejecucion)
    assert (original.clasificacion, reverso.clasificacion, otro.clasificacion) == (
        "MOVIMIENTO_PRIMARIO",
        "REVERSO",
        "MOVIMIENTO_PRIMARIO",
    )
    assert reverso.movimiento.tipo_movimiento == "REVERSO"
    assert reverso.movimiento.signo_economico == "RESTA"
    assert reverso.movimiento.movimiento_original_id == original.movimiento.movimiento_id
    assert reverso.resultado.movimiento_relacionado_id == original.movimiento.movimiento_id
    assert original.movimiento.anulado_por_movimiento_id == reverso.movimiento.movimiento_id
    assert original.resultado.movimiento_relacionado_id == reverso.movimiento.movimiento_id
    assert original.codigos == ["OBSERVACION_UNICA", "ANULADO_POR_REVERSO"]
    assert reverso.codigos == ["PAREJA_UNICA"]
    assert otro.movimiento.anulado_por_movimiento_id is None
    # El pago anulado no suma, y el reverso no resta otra vez: el par suma cero.
    assert ejecucion.recuperacion_bruta_interpretada == Decimal("150.00")
    assert ejecucion.recuperacion_neta_interpretada == Decimal("150.00")
    assert (ejecucion.reversos, ejecucion.pagos_anulados, ejecucion.posibles_reversos) == (1, 1, 0)


def test_un_negativo_sin_original_es_un_posible_reverso_que_solo_resta_en_la_neta(tmp_path):
    ingesta = ingerir_pagos_de(
        tmp_path,
        "pagos.csv",
        [
            pago(8, "2026-09-10 10:00:00", "400.00"),
            # Ningun pago de 250.00 en los 30 dias anteriores: no hay original.
            pago(8, "2026-09-12 09:00:00", "-250.00"),
        ],
    )

    (ejecucion,) = publicar(ingesta)

    pago_, negativo = resultados(ejecucion)
    assert (pago_.clasificacion, negativo.clasificacion) == (
        "MOVIMIENTO_PRIMARIO",
        "POSIBLE_REVERSO",
    )
    assert negativo.resultado.motivos == [
        {"codigo": "SIN_CANDIDATOS", "candidatos": 0, "ventana_dias": 30}
    ]
    assert negativo.movimiento.tipo_movimiento == "POSIBLE_REVERSO"
    assert negativo.movimiento.movimiento_original_id is None
    assert negativo.resultado.movimiento_relacionado_id is None
    assert pago_.movimiento.anulado_por_movimiento_id is None
    assert ejecucion.recuperacion_bruta_interpretada == Decimal("400.00")
    assert ejecucion.recuperacion_neta_interpretada == Decimal("150.00")


def test_con_varios_originales_posibles_el_motor_no_elige(tmp_path):
    ingesta = ingerir_pagos_de(
        tmp_path,
        "pagos.csv",
        [
            pago(5, "2026-09-05 10:00:00", "250.00"),
            pago(5, "2026-09-08 10:00:00", "250.00"),
            pago(5, "2026-09-09 10:00:00", "-250.00"),
        ],
    )

    (ejecucion,) = publicar(ingesta)

    uno, dos, negativo = resultados(ejecucion)
    assert negativo.clasificacion == "POSIBLE_REVERSO"
    assert negativo.resultado.motivos[0] == {
        "codigo": "VARIOS_CANDIDATOS",
        "candidatos": 2,
        "ventana_dias": 30,
    }
    # Ningun pago se anula: los dos suman en la bruta, y el negativo resta en la neta.
    assert uno.movimiento.anulado_por_movimiento_id is None
    assert dos.movimiento.anulado_por_movimiento_id is None
    assert ejecucion.recuperacion_bruta_interpretada == Decimal("500.00")
    assert ejecucion.recuperacion_neta_interpretada == Decimal("250.00")


def test_un_original_que_reclaman_dos_negativos_no_se_le_da_a_ninguno(tmp_path):
    ingesta = ingerir_pagos_de(
        tmp_path,
        "pagos.csv",
        [
            pago(5, "2026-09-05 10:00:00", "250.00"),
            pago(5, "2026-09-07 10:00:00", "-250.00"),
            pago(5, "2026-09-09 10:00:00", "-250.00"),
        ],
    )

    (ejecucion,) = publicar(ingesta)

    original, uno, dos = resultados(ejecucion)
    for negativo in (uno, dos):
        assert negativo.clasificacion == "POSIBLE_REVERSO"
        assert negativo.resultado.motivos[0]["codigo"] == "ORIGINAL_DISPUTADO"
    assert original.movimiento.anulado_por_movimiento_id is None
    assert ejecucion.recuperacion_neta_interpretada == Decimal("-250.00")


def test_un_original_ambiguo_no_se_enlaza(tmp_path):
    ingesta = ingerir_pagos_de(
        tmp_path,
        "pagos.csv",
        [
            pago(5, "2026-09-05 10:00:00", "250.00", Gestor="GESTOR 001"),
            pago(5, "2026-09-05 10:00:00", "250.00", Gestor="GESTOR 002"),
            pago(5, "2026-09-07 10:00:00", "-250.00"),
        ],
    )

    (ejecucion,) = publicar(ingesta)

    *ambiguas, negativo = resultados(ejecucion)
    assert [v.clasificacion for v in ambiguas] == ["COINCIDENCIA_AMBIGUA"] * 2
    assert negativo.clasificacion == "POSIBLE_REVERSO"
    assert negativo.resultado.motivos[0]["codigo"] == "CANDIDATO_AMBIGUO"
    assert negativo.resultado.motivos[0]["candidatos"] == 1


def test_un_reverso_duplicado_es_un_solo_reverso(tmp_path):
    negativo = pago(8, "2026-09-12 09:00:00", "-400.00")
    ingesta = ingerir_pagos_de(
        tmp_path,
        "pagos.csv",
        [pago(8, "2026-09-10 10:00:00", "400.00"), negativo, dict(negativo)],
    )

    (ejecucion,) = publicar(ingesta)

    original, reverso, copia = resultados(ejecucion)
    assert (reverso.clasificacion, copia.clasificacion) == ("REVERSO", "DUPLICADO_EXACTO")
    assert copia.movimiento.id == reverso.movimiento.id
    assert original.movimiento.anulado_por_movimiento_id == reverso.movimiento.movimiento_id
    assert ejecucion.recuperacion_neta_interpretada == Decimal("0.00")


def test_un_reverso_mas_alla_de_30_dias_no_tiene_original(tmp_path):
    ingesta = ingerir_pagos_de(
        tmp_path,
        "pagos.csv",
        [pago(5, "2026-09-01 10:00:00", "250.00"), pago(5, "2026-10-01 10:00:01", "-250.00")],
    )

    publicar(ingesta)

    (negativo,) = resultados(vigente(OCTUBRE))
    assert negativo.clasificacion == "POSIBLE_REVERSO"
    assert negativo.resultado.motivos[0]["codigo"] == "SIN_CANDIDATOS"
    (pago_,) = resultados(vigente(SEPTIEMBRE))
    assert pago_.movimiento.anulado_por_movimiento_id is None


def test_reverso_y_original_en_archivos_y_ventanas_distintos(tmp_path):
    # El pago llega en el archivo de agosto, y su reverso en el de septiembre: otra ventana. Las dos
    # interpretaciones dicen lo mismo de la pareja, cada una desde su lado.
    agosto = ingerir_pagos_de(tmp_path, "agosto.csv", [pago(8, "2026-08-30 10:00:00", "400.00")])
    publicar(agosto)
    assert resultados(vigente(AGOSTO))[0].movimiento.anulado_por_movimiento_id is None

    septiembre = ingerir_pagos_de(
        tmp_path, "septiembre.csv", [pago(8, "2026-09-02 09:00:00", "-400.00")]
    )
    interpretadas = publicar(septiembre)

    # El reverso cambia el contexto de agosto: su ventana se interpreta otra vez, y la anterior
    # queda en el historial, intacta.
    assert [e.periodo_desde for e in interpretadas] == [AGOSTO, SEPTIEMBRE]
    (original,) = resultados(vigente(AGOSTO))
    (reverso,) = resultados(vigente(SEPTIEMBRE))
    assert reverso.clasificacion == "REVERSO"
    assert reverso.movimiento.movimiento_original_id == original.movimiento.movimiento_id
    assert original.movimiento.anulado_por_movimiento_id == reverso.movimiento.movimiento_id
    assert vigente(AGOSTO).recuperacion_bruta_interpretada == Decimal("0.00")
    assert vigente(SEPTIEMBRE).recuperacion_neta_interpretada == Decimal("0.00")
    anterior, actual = ejecuciones(AGOSTO)
    assert (anterior.estado, actual.estado) == ("EXITOSA", "EXITOSA")
    assert anterior.firma_entrada != actual.firma_entrada
    (antes,) = resultados(anterior)
    assert antes.movimiento.anulado_por_movimiento_id is None
    assert antes.movimiento.movimiento_id == original.movimiento.movimiento_id
    assert anterior.recuperacion_bruta_interpretada == Decimal("400.00")
    # Agosto leyo el reverso de septiembre como contexto, sin darle resultado.
    assert actual.observaciones_contexto == 1


def test_un_archivo_que_no_toca_el_contexto_de_una_vecina_no_la_reinterpreta(tmp_path):
    publicar(ingerir_pagos_de(tmp_path, "agosto.csv", [pago(8, "2026-08-30 10:00:00", "400.00")]))

    # Pagos de septiembre de otro importe y de otro cliente: nada cambia para agosto.
    interpretadas = publicar(
        ingerir_pagos_de(
            tmp_path,
            "septiembre.csv",
            [pago(8, "2026-09-02 09:00:00", "-399.00"), pago(9, "2026-09-02 09:00:00", "400.00")],
        )
    )

    assert [e.periodo_desde for e in interpretadas] == [SEPTIEMBRE]
    assert len(ejecuciones(AGOSTO)) == 1


def test_un_importe_cero_no_es_un_movimiento(tmp_path):
    fila = pago(3, "2026-09-21 12:00:00", "0.00")
    ingesta = ingerir_pagos_de(tmp_path, "pagos.csv", [fila, dict(fila)])

    (ejecucion,) = publicar(ingesta)

    vistos = resultados(ejecucion)
    assert [v.clasificacion for v in vistos] == ["NO_CONCILIADO"] * 2
    assert {v.codigos[0] for v in vistos} == {"IMPORTE_CERO"}
    assert movimientos(ejecucion) == []
    assert ejecucion.no_conciliados == 2


def test_una_huella_que_mintiera_no_fusiona_nada(tmp_path, monkeypatch):
    # SHA-256 no choca en la practica; para probar la comprobacion campo por campo, la firma exacta
    # se reemplaza por una que solo ve el cliente, el instante y el importe.
    from motor_cartera.motor_pagos import ejecuciones as motor
    from motor_cartera.motor_pagos.firmas import sql_firma_exacta, sql_firma_legacy

    pobre = sql_firma_legacy("l").replace("firma_legacy/v1", "firma_pobre/v1")
    monkeypatch.setattr(motor, "SQL_LEER", motor.SQL_LEER.replace(sql_firma_exacta("l"), pobre))
    ingesta = ingerir_pagos_de(
        tmp_path,
        "pagos.csv",
        [
            pago(4, "2026-09-03 13:00:00", "250.00", Gestor="GESTOR 001"),
            pago(4, "2026-09-03 13:00:00", "250.00", Gestor="GESTOR 002"),
        ],
    )

    (ejecucion,) = publicar(ingesta)

    vistos = resultados(ejecucion)
    assert vistos[0].resultado.firma_exacta == vistos[1].resultado.firma_exacta
    assert [v.clasificacion for v in vistos] == ["NO_CONCILIADO"] * 2
    assert {v.codigos[0] for v in vistos} == {"FIRMA_SIN_VALIDAR"}
    assert movimientos(ejecucion) == []
