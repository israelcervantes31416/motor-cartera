"""El nucleo puro del motor de pagos: las huellas, los identificadores, las ventanas, el contexto
temporal y las reglas de motor-pagos/v1, sin base de datos."""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from motor_cartera.contratos.fuente import Tipo, linea_canonica
from motor_cartera.contratos.pagos import CONTRATO_PAGOS
from motor_cartera.motor_pagos import firmas
from motor_cartera.motor_pagos.contexto import contexto
from motor_cartera.motor_pagos.reglas import (
    VENTANA_REVERSO,
    VERSION_MOTOR_PAGOS,
    Clasificacion,
    EstadoConciliacion,
    Motivo,
    Observacion,
    SignoEconomico,
    TipoMovimiento,
    interpretar,
    periodo_de,
    periodos_entre,
    siguiente_periodo,
)

# --- las huellas ----------------------------------------------------------------------------------


def _valores(**cambios) -> dict:
    valores = {c.columna: None for c in firmas.CAMPOS_FIRMADOS}
    valores.update(
        anio=2026,
        semana=37,
        cliente_unico="CU0000000001",
        fecha_recepcion=datetime(2026, 9, 3, 10, 15),
        recuperacion_por_gestion=Decimal("-150.00"),
        gestor="GESTOR 007",
        porcentaje_comision=0.05,
    )
    valores.update(cambios)
    return valores


def test_los_23_campos_entran_en_la_firma_exacta_en_el_orden_del_contrato():
    assert [c.fuente for c in firmas.CAMPOS_FIRMADOS] == list(CONTRATO_PAGOS.nombres)
    assert len(firmas.CAMPOS_FIRMADOS) == 23
    assert firmas.CAMPOS_FIRMADOS[4].columna == "cliente_unico"


def test_la_linea_exacta_es_la_linea_canonica_del_contrato_con_su_prefijo():
    linea = firmas.linea_exacta("DSP_001", "CARTERA_PRINCIPAL", _valores())

    cabecera, cuerpo = linea.split("\n")
    assert cabecera == "firma_exacta/v1"
    textos = [
        "DSP_001",
        "CARTERA_PRINCIPAL",
        "2026",
        "37",
        None,
        None,
        "CU0000000001",
        "2026-09-03T10:15:00",
        *[None] * 5,
        "GESTOR 007",
        None,
        None,
        None,
        None,
        "-150.00",
        None,
        None,
        None,
        None,
        "0.05",
        None,
    ]
    assert cuerpo == linea_canonica(textos)
    assert (
        firmas.firma_exacta("DSP_001", "CARTERA_PRINCIPAL", _valores())
        == hashlib.sha256(linea.encode()).digest()
    )


def test_cualquier_campo_distinto_cambia_la_firma_exacta_y_no_la_legacy():
    base = _valores()
    exacta = firmas.firma_exacta("DSP_001", "CARTERA_PRINCIPAL", base)
    for cambio in (
        {"gestor": "GESTOR 008"},
        {"gestor": None},
        {"anio": 2025},
        {"fecha_recepcion": datetime(2026, 9, 3, 10, 15, 0, 500)},
        {"porcentaje_comision": 0.08},
        {"cargos_automaticos": Decimal("0.00")},
    ):
        otra = _valores(**cambio)
        assert firmas.firma_exacta("DSP_001", "CARTERA_PRINCIPAL", otra) != exacta, cambio
    # La de otra cartera, tambien.
    assert firmas.firma_exacta("DSP_001", "OTRA_CARTERA", base) != exacta
    legacy = firmas.firma_legacy(
        "DSP_001",
        "CARTERA_PRINCIPAL",
        "CU0000000001",
        datetime(2026, 9, 3, 10, 15),
        Decimal("-150"),
    )
    # La legacy trunca al segundo y no ve el gestor.
    assert legacy == firmas.firma_legacy(
        "DSP_001",
        "CARTERA_PRINCIPAL",
        "CU0000000001",
        datetime(2026, 9, 3, 10, 15, 0, 999_999),
        Decimal("-150.00"),
    )
    assert legacy != firmas.firma_legacy(
        "DSP_001",
        "CARTERA_PRINCIPAL",
        "CU0000000001",
        datetime(2026, 9, 3, 10, 15, 1),
        Decimal("-150.00"),
    )


@pytest.mark.parametrize(
    ("valor", "tipo", "texto"),
    [
        (None, Tipo.TEXTO, None),
        ("Campaña Ñ", Tipo.TEXTO, "Campaña Ñ"),
        (7, Tipo.ENTERO, "7"),
        (Decimal("1500.5"), Tipo.IMPORTE, "1500.50"),
        (Decimal("-0.00"), Tipo.IMPORTE, "0.00"),
        (datetime(2026, 9, 3), Tipo.FECHA_HORA, "2026-09-03T00:00:00"),
        (datetime(2026, 9, 3, 1, 2, 3, 40), Tipo.FECHA_HORA, "2026-09-03T01:02:03.000040"),
        (0.05, Tipo.DECIMAL, "0.05"),
        (2.0, Tipo.DECIMAL, "2"),
        (1e-05, Tipo.DECIMAL, "1e-05"),
        (1e15, Tipo.DECIMAL, "1e+15"),
        (123456789012345.0, Tipo.DECIMAL, "123456789012345"),
        (1234567890123456.0, Tipo.DECIMAL, "1.234567890123456e+15"),
        (-0.0, Tipo.DECIMAL, "-0"),
        (0.30000000000000004, Tipo.DECIMAL, "0.30000000000000004"),
    ],
)
def test_el_texto_canonico_de_cada_tipo(valor, tipo, texto):
    assert firmas.texto_canonico(valor, tipo) == texto


def test_un_tipo_que_pagos_v1_no_tiene_no_tiene_texto():
    with pytest.raises(ValueError, match="no tiene columnas"):
        firmas.texto_canonico("2026-09-03", Tipo.FECHA)


def test_el_uuid_v8_es_el_del_ejemplo_del_rfc_9562():
    # RFC 9562, apendice B.2: SHA-256 del espacio DNS y "www.example.com".
    assert firmas.uuid_v8(uuid.NAMESPACE_DNS, "www.example.com") == uuid.UUID(
        "5c146b14-3c52-8afd-938a-375d0df1fbf6"
    )


def test_el_movimiento_id_sale_de_la_version_y_la_firma():
    firma = hashlib.sha256(b"x").digest()

    uno = firmas.movimiento_id(VERSION_MOTOR_PAGOS, firma)

    assert uno.version == 8
    assert uno == firmas.movimiento_id(VERSION_MOTOR_PAGOS, firma)
    assert uno != firmas.movimiento_id("motor-pagos/v2", firma)
    assert uno != firmas.movimiento_id(VERSION_MOTOR_PAGOS, hashlib.sha256(b"y").digest())
    with pytest.raises(ValueError, match="Version invalida"):
        firmas.sql_digest_movimiento("v1'; DROP TABLE x; --", "f")


# --- las ventanas ---------------------------------------------------------------------------------


def test_una_ventana_es_un_mes_calendario():
    assert periodo_de(datetime(2026, 2, 28, 23, 59)) == date(2026, 2, 1)
    assert siguiente_periodo(date(2026, 12, 1)) == date(2027, 1, 1)
    assert periodos_entre(datetime(2026, 11, 30), date(2027, 2, 1)) == [
        date(2026, 11, 1),
        date(2026, 12, 1),
        date(2027, 1, 1),
        date(2027, 2, 1),
    ]
    with pytest.raises(ValueError, match="empieza el dia 1"):
        siguiente_periodo(date(2026, 1, 2))
    assert VENTANA_REVERSO == timedelta(days=30)


# --- el contexto temporal -------------------------------------------------------------------------

CORTES = [date(2026, 8, 5) + timedelta(days=7 * i) for i in range(6)]
# La cuenta aparece en los cortes 1, 2, 5 y 6: falta en el 3 y el 4.
PRESENTES = [CORTES[0], CORTES[1], CORTES[4], CORTES[5]]


@pytest.mark.parametrize(
    ("recepcion", "esperado"),
    [
        # Antes de su primera observacion (y de cualquier corte).
        (datetime(2026, 8, 1, 9), (None, CORTES[0], True, False, False)),
        # El mismo dia de un corte cuenta como despues de el.
        (datetime(2026, 8, 5, 23), (CORTES[0], CORTES[1], False, False, False)),
        # Entre dos cortes continuos.
        (datetime(2026, 8, 14), (CORTES[1], CORTES[4], False, False, False)),
        # Despues del corte 3, que ya no la traia: durante su ausencia.
        (datetime(2026, 8, 21, 12), (CORTES[1], CORTES[4], False, False, True)),
        (datetime(2026, 8, 30), (CORTES[1], CORTES[4], False, False, True)),
        # De vuelta en la cartera.
        (datetime(2026, 9, 3), (CORTES[4], CORTES[5], False, False, False)),
        # Despues de su ultima observacion, que es el ultimo corte.
        (datetime(2026, 9, 20), (CORTES[5], None, False, True, False)),
    ],
)
def test_el_contexto_de_un_pago_entre_los_snapshots_de_su_cuenta(recepcion, esperado):
    visto = contexto(CORTES, PRESENTES, recepcion)

    assert (
        visto.snapshot_anterior,
        visto.snapshot_siguiente,
        visto.antes_de_primera_observacion,
        visto.despues_de_ultima_observacion,
        visto.durante_ausencia_observada,
    ) == esperado


def test_un_pago_despues_de_la_salida_observada_es_durante_la_ausencia():
    # Sale en el corte 4 y no vuelve.
    visto = contexto(CORTES, CORTES[:3], datetime(2026, 9, 1))

    assert visto.snapshot_anterior == CORTES[2]
    assert visto.snapshot_siguiente is None
    assert visto.durante_ausencia_observada and visto.despues_de_ultima_observacion


# --- las reglas -----------------------------------------------------------------------------------


def _obs(
    fila: int,
    recepcion: str,
    monto: str,
    *,
    cliente: str = "CU0000000005",
    gestor: str = "G1",
    propia: bool = True,
    con_cuenta: bool = True,
    archivo: str = "a" * 64,
) -> Observacion:
    instante = datetime.fromisoformat(recepcion)
    valores = _valores(
        cliente_unico=cliente,
        fecha_recepcion=instante,
        recuperacion_por_gestion=Decimal(monto),
        gestor=gestor,
    )
    return Observacion(
        dataset_conformado_id=1,
        source_row=fila,
        orden=(archivo, fila),
        propia=propia,
        con_cuenta=con_cuenta,
        cliente_unico=cliente,
        fecha_recepcion=instante,
        recuperacion=Decimal(monto),
        firma_exacta=firmas.firma_exacta("DSP_001", "CARTERA_PRINCIPAL", valores),
        firma_legacy=firmas.firma_legacy(
            "DSP_001", "CARTERA_PRINCIPAL", cliente, instante, Decimal(monto)
        ),
        valores=tuple(valores.values()),
    )


def _clases(resultados) -> list[tuple[int, str, tuple[str, ...]]]:
    return sorted((r.observacion.source_row, r.clasificacion, tuple(r.motivos)) for r in resultados)


def test_las_reglas_de_v1_en_un_solo_cliente():
    observaciones = [
        _obs(2, "2026-09-05 10:00:00", "250.00"),
        _obs(3, "2026-09-05 10:00:00", "250.00"),  # copia exacta de la fila 2
        _obs(4, "2026-09-06 10:00:00", "-250.00"),  # su reverso
        _obs(5, "2026-09-07 10:00:00", "100.00", gestor="G1"),
        _obs(6, "2026-09-07 10:00:00", "100.00", gestor="G2"),  # ambigua con la 5
        _obs(7, "2026-09-08 10:00:00", "0.00"),
        _obs(8, "2026-09-09 10:00:00", "-75.00"),  # sin original
        _obs(9, "2026-09-10 10:00:00", "40.00", con_cuenta=False, cliente="CU0000000009"),
    ]

    resultados, movimientos = interpretar(observaciones)

    assert _clases(resultados) == [
        (
            2,
            Clasificacion.MOVIMIENTO_PRIMARIO,
            (Motivo.REPRESENTANTE_DE_COPIAS, Motivo.ANULADO_POR_REVERSO),
        ),
        (3, Clasificacion.DUPLICADO_EXACTO, (Motivo.COPIA_EXACTA,)),
        (4, Clasificacion.REVERSO, (Motivo.PAREJA_UNICA,)),
        (5, Clasificacion.COINCIDENCIA_AMBIGUA, (Motivo.LLAVE_HISTORICA_COMPARTIDA,)),
        (6, Clasificacion.COINCIDENCIA_AMBIGUA, (Motivo.LLAVE_HISTORICA_COMPARTIDA,)),
        (7, Clasificacion.NO_CONCILIADO, (Motivo.IMPORTE_CERO,)),
        (8, Clasificacion.POSIBLE_REVERSO, (Motivo.SIN_CANDIDATOS,)),
        (9, Clasificacion.MOVIMIENTO_PRIMARIO, (Motivo.OBSERVACION_UNICA,)),
    ]
    por_tipo = {m.representante.source_row: m for m in movimientos}
    assert set(por_tipo) == {2, 4, 8, 9}
    assert (por_tipo[2].tipo, por_tipo[2].signo, por_tipo[2].observaciones) == (
        TipoMovimiento.PAGO,
        SignoEconomico.SUMA,
        2,
    )
    assert por_tipo[2].anulado_por_id == por_tipo[4].movimiento_id
    assert por_tipo[4].original_id == por_tipo[2].movimiento_id
    assert por_tipo[8].tipo == TipoMovimiento.POSIBLE_REVERSO
    assert por_tipo[9].estado_conciliacion == EstadoConciliacion.SIN_CUENTA_OBSERVADA
    # El representante es el de menor (archivo, fila), no el primero que llega a la funcion.
    otra_vez, _ = interpretar(list(reversed(observaciones)))
    assert _clases(otra_vez) == _clases(resultados)


def test_el_contexto_no_recibe_resultado_pero_cuenta_como_candidato():
    observaciones = [
        _obs(2, "2026-08-30 10:00:00", "400.00", propia=False),
        _obs(3, "2026-09-02 10:00:00", "-400.00"),
        _obs(4, "2026-10-01 09:00:00", "-400.00", propia=False),  # otro reclamante, a 32 dias
    ]

    resultados, movimientos = interpretar(observaciones)

    assert _clases(resultados) == [(3, Clasificacion.REVERSO, (Motivo.PAREJA_UNICA,))]
    (reverso,) = movimientos
    assert reverso.original_id == firmas.movimiento_id(
        VERSION_MOTOR_PAGOS, observaciones[0].firma_exacta
    )


def test_una_copia_que_no_es_igual_campo_por_campo_no_se_interpreta():
    uno = _obs(2, "2026-09-05 10:00:00", "250.00")
    distinto = _obs(3, "2026-09-05 10:00:00", "250.00", gestor="OTRO")
    # La misma huella, valores distintos: lo que pasaria si la huella mintiera.
    impostor = Observacion(**{**distinto.__dict__, "firma_exacta": uno.firma_exacta})

    resultados, movimientos = interpretar([uno, impostor])

    assert _clases(resultados) == [
        (2, Clasificacion.NO_CONCILIADO, (Motivo.FIRMA_SIN_VALIDAR,)),
        (3, Clasificacion.NO_CONCILIADO, (Motivo.FIRMA_SIN_VALIDAR,)),
    ]
    assert movimientos == []
