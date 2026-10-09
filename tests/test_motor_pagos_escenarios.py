"""El motor de pagos de punta a punta: el golden de seis cortes con sus pagos, la reconstruccion de
la interpretacion, su independencia del orden de llegada de los archivos, y el escenario del
generador, cuyo SALDO evoluciona exactamente con los movimientos que el motor interpreta."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

import pandas as pd
import pytest
from historia_escenarios import (
    GOLDEN_CORTES,
    GOLDEN_PAGOS,
    GOLDEN_PRESENCIA,
    GOLDEN_RELLENO,
    GOLDEN_SALDOS,
    Cuenta,
    cliente,
    cuenta,
    escribir_corte,
    golden,
)
from motor_pagos_escenarios import (
    cuantos,
    del_cliente,
    ejecuciones,
    foto_motor,
    resultados,
    vigente,
)
from sqlalchemy import text
from sqlmodel import select

from motor_cartera.config import Config
from motor_cartera.contratos.pagos import CONTRATO_PAGOS
from motor_cartera.db.modelos import (
    CuentaCanonica,
    EjecucionMotorPagos,
    EstadoMotorPagos,
    MovimientoEconomicoCanonico,
    PagoObservado,
    ResultadoPagoObservado,
    SnapshotCuenta,
)
from motor_cartera.db.sesion import crear_motor, sesion
from motor_cartera.generador.oficial import escribir_pagos, generar_escenario
from motor_cartera.ingesta.corridas import ingerir_archivo
from motor_cartera.ingesta.pagos import ingerir_pagos
from motor_cartera.motor_pagos import backfill, consultas
from motor_cartera.motor_pagos.reglas import VERSION_MOTOR_PAGOS

pytestmark = pytest.mark.usefixtures("bd")

AGOSTO, SEPTIEMBRE = date(2026, 8, 1), date(2026, 9, 1)


# --- el golden de seis cortes ---------------------------------------------------------------------


def test_el_escenario_golden_de_pagos(tmp_path, trabajar):
    golden(tmp_path)

    procesados = trabajar()

    # Agosto y septiembre, interpretadas una vez cada una, al final de la cola.
    assert [p.tipo for p in procesados][-2:] == ["MOTOR_PAGOS", "MOTOR_PAGOS"]
    assert cuantos(PagoObservado) == sum(len(filas) for filas in GOLDEN_PAGOS.values()) == 17
    agosto, septiembre = vigente(AGOSTO), vigente(SEPTIEMBRE)
    assert [len(ejecuciones(AGOSTO)), len(ejecuciones(SEPTIEMBRE))] == [1, 1]
    vistos = resultados(agosto) + resultados(septiembre)
    assert len(vistos) == 17

    def de(numero: int) -> list[tuple[str, str, str]]:
        return [
            (v.clasificacion, v.resultado.estado_conciliacion, str(v.pago.recuperacion_por_gestion))
            for v in del_cliente(vistos, cliente(numero))
        ]

    primario, conciliado, sin_cuenta = (
        "MOVIMIENTO_PRIMARIO",
        "CONCILIADO_CUENTA",
        "SIN_CUENTA_OBSERVADA",
    )
    # La 1: un pago normal y dos observaciones con la misma llave historica y otro gestor.
    assert de(1) == [
        (primario, conciliado, "1000.00"),
        ("COINCIDENCIA_AMBIGUA", conciliado, "150.00"),
        ("COINCIDENCIA_AMBIGUA", conciliado, "150.00"),
    ]
    # La 5: dos pagos legitimos del mismo importe el mismo dia, y un negativo que no elige entre
    # ellos.
    assert de(5) == [
        (primario, conciliado, "500.00"),
        (primario, conciliado, "600.00"),
        (primario, conciliado, "600.00"),
        (primario, conciliado, "700.00"),
        ("POSIBLE_REVERSO", conciliado, "-600.00"),
    ]
    negativo_5 = del_cliente(vistos, cliente(5))[-1]
    assert negativo_5.codigos == ["VARIOS_CANDIDATOS"]
    # La 6: dos filas identicas, un solo movimiento.
    assert de(6) == [(primario, conciliado, "300.00"), ("DUPLICADO_EXACTO", conciliado, "300.00")]
    # La 7: ningun corte la trae.
    assert de(7) == [(primario, sin_cuenta, "200.00")]
    # La 8: su pago de agosto y su reverso de septiembre, en otro archivo: una pareja aislada.
    assert de(8) == [(primario, conciliado, "400.00"), ("REVERSO", conciliado, "-400.00")]
    pago_8, reverso_8 = del_cliente(vistos, cliente(8))
    assert reverso_8.movimiento.movimiento_original_id == pago_8.movimiento.movimiento_id
    assert pago_8.movimiento.anulado_por_movimiento_id == reverso_8.movimiento.movimiento_id

    # Los conteos de cada ventana.
    assert (
        agosto.observaciones_leidas,
        agosto.movimientos_canonicos,
        agosto.primarios,
        agosto.duplicados_exactos,
        agosto.coincidencias_ambiguas,
        agosto.posibles_reversos,
        agosto.reversos,
        agosto.sin_cuenta_observada,
        agosto.pagos_anulados,
        agosto.grupos_ambiguos,
    ) == (15, 12, 11, 1, 2, 1, 0, 1, 1, 1)
    assert (septiembre.observaciones_leidas, septiembre.reversos, septiembre.primarios) == (2, 1, 1)
    # Agosto vio el reverso de septiembre como contexto, y septiembre el pago de agosto.
    assert agosto.observaciones_contexto == septiembre.observaciones_contexto == 1
    # La recuperacion: sin la copia, sin los ambiguos, sin el pago anulado; la neta resta el
    # posible reverso y no vuelve a restar el reverso.
    assert agosto.recuperacion_bruta_interpretada == Decimal("4130.00")
    assert agosto.recuperacion_neta_interpretada == Decimal("3530.00")
    assert agosto.importe_ambiguo_observado == Decimal("300.00")
    assert septiembre.recuperacion_bruta_interpretada == Decimal("250.00")
    assert septiembre.recuperacion_neta_interpretada == Decimal("250.00")

    # El contexto temporal de cada movimiento, entre los snapshots de su cuenta.
    with sesion() as s:

        def contextos(numero: int):
            c = cuenta(numero)
            _, pagina = consultas.movimientos_de_cuenta(
                s,
                c,
                version=VERSION_MOTOR_PAGOS,
                desde=None,
                hasta=None,
                desplazamiento=0,
                limite=50,
            )
            return [m.contexto for m in reversed(pagina)]

        # La 4 aparece en el corte 3 (19 de agosto): su pago del 11 es de antes.
        antes, despues = contextos(4)
        assert antes.antes_de_primera_observacion and antes.snapshot_anterior is None
        assert antes.snapshot_siguiente == GOLDEN_CORTES[2]
        assert not despues.antes_de_primera_observacion
        # La 3 falta en los cortes 3 y 4: su pago del 21 llego durante su ausencia.
        (ausente,) = contextos(3)
        assert ausente.durante_ausencia_observada
        assert (ausente.snapshot_anterior, ausente.snapshot_siguiente) == (
            GOLDEN_CORTES[1],
            GOLDEN_CORTES[4],
        )
        # La 2 sale en el corte 4 y no vuelve: su pago del 28, despues de su salida.
        (salida,) = contextos(2)
        assert salida.durante_ausencia_observada and salida.despues_de_ultima_observacion
        # La 5, siempre presente: sus pagos caen entre cortes continuos.
        assert not any(
            c.durante_ausencia_observada or c.antes_de_primera_observacion for c in contextos(5)
        )
    assert GOLDEN_SALDOS and GOLDEN_RELLENO and GOLDEN_PRESENCIA


# --- reconstruir y cambiar el orden ---------------------------------------------------------------


def _sin_motor() -> None:
    with sesion() as s:
        for tabla in (
            "trabajo_orquestacion WHERE tipo = 'MOTOR_PAGOS'",
            "resultado_pago_observado",
            "movimiento_economico_canonico",
            "ejecucion_motor_pagos",
        ):
            s.execute(text(f"DELETE FROM {tabla}"))
        s.commit()


def test_reconstruir_la_interpretacion_da_exactamente_la_misma(tmp_path, trabajar):
    golden(tmp_path)
    trabajar()
    antes = foto_motor()
    assert len(antes["resultados"]) == 17 and len(antes["movimientos"]) == 14

    # Se borra solo la capa del motor de pagos y se vuelve a interpretar con el backfill.
    _sin_motor()
    with sesion() as s:
        diagnostico = backfill.diagnosticar(s)
    assert [v.desde for v in diagnostico.sin_interpretacion] == [AGOSTO, SEPTIEMBRE]
    backfill.encolar(diagnostico.sin_interpretacion, config=Config())
    trabajar()

    # Las mismas clasificaciones, los mismos grupos, los mismos movimientos con los mismos
    # identificadores, la misma recuperacion, el mismo linaje y la misma firma de entrada.
    assert foto_motor() == antes


def _vaciar() -> None:
    with crear_motor().begin() as conexion:
        conexion.execute(
            text(
                "TRUNCATE corrida, cuenta, rechazo, artefacto_fuente, cuenta_canonica, "
                "ejecucion_motor_pagos RESTART IDENTITY CASCADE"
            )
        )


def test_el_orden_de_llegada_de_los_archivos_no_cambia_la_interpretacion(tmp_path, trabajar):
    cortes = []
    for numero, corte in enumerate(GOLDEN_CORTES, start=1):
        cuentas = [Cuenta(n) for n in GOLDEN_RELLENO] + [
            Cuenta(c, *GOLDEN_SALDOS.get(c, {}).get(numero, (10_000, 15)))
            for c, presentes in GOLDEN_PRESENCIA.items()
            if numero in presentes
        ]
        cortes.append((corte, escribir_corte(tmp_path, corte, cuentas)))
    archivos = {
        periodo: escribir_pagos(
            pd.DataFrame(filas, columns=list(CONTRATO_PAGOS.nombres)),
            tmp_path / f"pagos_golden_{periodo}.csv",
        ).ruta
        for periodo, filas in GOLDEN_PAGOS.items()
    }

    fotos = []
    for orden in ([1, 2, 3, 4, 5], [5, 3, 1, 4, 2], [4, 5, 2, 1, 3]):
        _vaciar()
        for corte, ruta in cortes:
            ingerir_archivo(ruta, contrato="cartera/v2", fecha_corte=corte)
        trabajar()
        # Cada archivo de pagos llega y se interpreta antes de que llegue el siguiente: el motor
        # trabaja por incrementos, y una ventana se vuelve a interpretar cuando cambia.
        for periodo in orden:
            ingerir_pagos(archivos[periodo])
            trabajar()
        fotos.append(foto_motor())

    assert fotos[0] == fotos[1] == fotos[2]
    assert len(fotos[0]["movimientos"]) == 14


# --- el escenario del generador -------------------------------------------------------------------


def test_el_saldo_del_generador_evoluciona_con_los_movimientos_que_el_motor_interpreta(
    tmp_path, trabajar
):
    escenario = generar_escenario(
        tmp_path,
        cuentas=300,
        cortes=5,
        primer_corte=date(2026, 9, 2),
        semilla=11,
        formato="csv",
        tasa_altas=0.04,
        tasa_retiros=0.03,
    )
    manifiesto = escenario.manifiesto
    for corte in manifiesto["cortes"]:
        fecha = date.fromisoformat(corte["fecha_corte"])
        ingerir_archivo(tmp_path / corte["archivo"], contrato="cartera/v2", fecha_corte=fecha)
    for periodo in manifiesto["periodos"]:
        ingerir_pagos(tmp_path / periodo["archivo"])

    trabajar()

    with sesion() as s:
        assert set(s.exec(select(EjecucionMotorPagos.estado)).all()) == {EstadoMotorPagos.EXITOSA}
        vigentes = consultas.vigentes(s, VERSION_MOTOR_PAGOS)
        ventanas = s.exec(
            select(EjecucionMotorPagos).where(EjecucionMotorPagos.id.in_(vigentes))
        ).all()
        publicados = s.exec(
            select(MovimientoEconomicoCanonico).where(
                MovimientoEconomicoCanonico.ejecucion_motor_pagos_id.in_(vigentes)
            )
        ).all()
        saldos = {
            (cliente_, fecha): saldo
            for cliente_, fecha, saldo in s.exec(
                select(
                    CuentaCanonica.cliente_unico, SnapshotCuenta.fecha_corte, SnapshotCuenta.saldo
                ).join(SnapshotCuenta, SnapshotCuenta.cuenta_canonica_id == CuentaCanonica.id)
            ).all()
        }
    observaciones = sum(p["movimientos"] for p in manifiesto["periodos"])
    repetidos = sum(p["repetidos_exactos"] for p in manifiesto["periodos"])
    ajustes = sum(p["ajustes"] for p in manifiesto["periodos"])
    # Cada movimiento del manifiesto, y cada repetido exacto, donde dice el manifiesto.
    assert sum(v.observaciones_clasificadas for v in ventanas) == observaciones
    assert cuantos(ResultadoPagoObservado) == observaciones
    assert sum(v.duplicados_exactos for v in ventanas) == repetidos
    assert sum(v.coincidencias_ambiguas + v.no_conciliados for v in ventanas) == 0
    assert len(publicados) == observaciones - repetidos
    assert sum(1 for m in publicados if m.signo_economico == "RESTA") == ajustes
    assert {m.estado_conciliacion for m in publicados} == {"CONCILIADO_CUENTA"}

    # La invariante del generador: el SALDO de una cuenta que continua es el anterior menos lo que
    # recupero en el periodo, contando una vez cada movimiento. Con los movimientos del motor se
    # cumple exacta, centavo por centavo; con los pagos observados, no: los repetidos se contarian
    # dos veces. En datos reales no se cumpliria asi: el acreedor carga intereses y ajustes que
    # ninguna fuente trae.
    fechas = [date.fromisoformat(c["fecha_corte"]) for c in manifiesto["cortes"]]
    flujo: dict[tuple[str, date], Decimal] = defaultdict(Decimal)
    for m in publicados:
        dia = m.fecha_recepcion.date()
        cierre = next(f for f in fechas if f >= dia)
        flujo[(m.cliente_unico, cierre)] += m.monto_reportado
    comprobadas = 0
    for anterior, siguiente in zip(fechas, fechas[1:], strict=False):
        for (cliente_, fecha), saldo in saldos.items():
            if fecha != anterior or (cliente_, siguiente) not in saldos:
                continue
            assert saldos[(cliente_, siguiente)] == saldo - flujo[(cliente_, siguiente)], cliente_
            comprobadas += 1
    assert comprobadas > 900
