"""La ejecucion del motor de pagos: todo o nada, idempotente, versionada, y con la interpretacion
anterior intacta cuando llegan pagos o cortes nuevos."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from historia_escenarios import Cuenta, ingerir_corte, ingerir_pagos_de, pago
from motor_pagos_escenarios import (
    cuantos,
    ejecuciones,
    historiar_todo,
    interpretar_todo,
    movimientos,
    publicar,
    resultados,
    vigente,
)
from sqlalchemy import text
from sqlmodel import select

from motor_cartera.config import Config
from motor_cartera.db.modelos import (
    EjecucionMotorPagos,
    EstadoMotorPagos,
    MovimientoEconomicoCanonico,
    ResultadoPagoObservado,
    TipoTrabajo,
    TrabajoOrquestacion,
)
from motor_cartera.db.sesion import sesion
from motor_cartera.motor_pagos import ejecuciones as motor
from motor_cartera.motor_pagos.ejecuciones import (
    MotorPagosYaInterpretado,
    Ventana,
    abrir_ventana,
    interpretar,
)

pytestmark = pytest.mark.usefixtures("bd")

SEPTIEMBRE = date(2026, 9, 1)
VENTANA = Ventana.del_periodo("DSP_001", "CARTERA_PRINCIPAL", SEPTIEMBRE)


def _pagos_de_septiembre(tmp_path, nombre="pagos.csv", n=30):
    filas = [pago(i, f"2026-09-{1 + i % 28:02d} 10:00:00", f"{100 + i}.00") for i in range(1, n)]
    filas.append(pago(1, "2026-09-03 11:00:00", "-101.00"))
    return ingerir_pagos_de(tmp_path, nombre, filas)


def _abrir() -> EjecucionMotorPagos:
    with sesion() as s:
        ejecucion, nueva = abrir_ventana(s, VENTANA, max_intentos=5)
        s.commit()
        s.refresh(ejecucion)
    assert nueva
    return ejecucion


# --- todo o nada ----------------------------------------------------------------------------------


class Falla(Exception):
    pass


def _lanzar(*_args, **_kwargs):
    raise Falla("inyectada")


def _falla_despues(original):
    def envoltura(*args, **kwargs):
        original(*args, **kwargs)
        raise Falla("inyectada")

    return envoltura


FALLAS = {
    "antes del staging": ("_preparar_staging", lambda original: _lanzar),
    "despues de clasificar duplicados": ("_clasificar", _falla_despues),
    "despues de crear movimientos": ("_crear_movimientos", _falla_despues),
    "antes de insertar resultados": ("_insertar_resultados", lambda original: _lanzar),
    "antes de cerrar la ejecucion": ("_cerrar", lambda original: _lanzar),
}


@pytest.mark.parametrize("donde", list(FALLAS))
def test_una_falla_en_cualquier_punto_no_publica_nada(tmp_path, monkeypatch, donde):
    _pagos_de_septiembre(tmp_path)
    historiar_todo()
    (abierta,) = ejecuciones()
    funcion, falla = FALLAS[donde]
    monkeypatch.setattr(motor, funcion, falla(getattr(motor, funcion)))

    interpretar(abierta.id)

    (fallida,) = ejecuciones()
    assert (fallida.estado, fallida.resultado) == ("FALLIDA", "ERROR_INTERNO")
    assert fallida.detalle == "Error interno (Falla); ver la bitacora."
    assert (fallida.observaciones_clasificadas, fallida.movimientos_canonicos) == (0, 0)
    assert cuantos(MovimientoEconomicoCanonico) == cuantos(ResultadoPagoObservado) == 0
    with sesion() as s:
        for tabla in ("mp_observacion", "mp_grupo", "mp_enlace"):
            assert s.execute(text(f"SELECT to_regclass('pg_temp.{tabla}')")).scalar() is None

    # Sin la falla, otra ejecucion de la misma ventana la interpreta entera.
    monkeypatch.undo()
    otra = _abrir()
    interpretar(otra.id)
    terminada = vigente(SEPTIEMBRE)
    assert terminada.id == otra.id
    assert terminada.observaciones_clasificadas == cuantos(ResultadoPagoObservado) == 30


class Muerte(BaseException):  # noqa: N818
    """La muerte del proceso: ningun `except Exception` la atrapa."""


def test_si_el_proceso_muere_a_media_transaccion_la_ventana_se_vuelve_a_interpretar(
    tmp_path, monkeypatch
):
    _pagos_de_septiembre(tmp_path)
    historiar_todo()
    (abierta,) = ejecuciones()

    def muere(*_args, **_kwargs):
        raise Muerte()

    monkeypatch.setattr(motor, "_insertar_resultados", muere)
    with pytest.raises(Muerte):
        interpretar(abierta.id)

    # La transaccion se revirtio sola: nada publicado, y la ejecucion sigue EN_PROCESO.
    assert ejecuciones()[0].estado == EstadoMotorPagos.EN_PROCESO
    assert cuantos(MovimientoEconomicoCanonico) == 0
    monkeypatch.undo()
    interpretar(abierta.id)
    assert ejecuciones()[0].estado == EstadoMotorPagos.EXITOSA
    assert cuantos(ResultadoPagoObservado) == 30


# --- idempotencia y versiones ---------------------------------------------------------------------


def test_una_ejecucion_terminada_no_se_vuelve_a_interpretar(tmp_path):
    (terminada,) = publicar(_pagos_de_septiembre(tmp_path))

    interpretar(terminada.id)
    motor.ejecutar_motor_pagos(terminada.id)

    (otra_vez,) = ejecuciones()
    assert otra_vez.terminada_en == terminada.terminada_en
    assert cuantos(ResultadoPagoObservado) == 30


def test_las_mismas_entradas_no_se_publican_dos_veces(tmp_path):
    (primera,) = publicar(_pagos_de_septiembre(tmp_path))
    segunda = _abrir()

    with pytest.raises(MotorPagosYaInterpretado) as ganadora:
        interpretar(segunda.id)

    assert ganadora.value.previa.motor_pagos_run_id == primera.motor_pagos_run_id
    perdedora = ejecuciones()[-1]
    assert (perdedora.estado, perdedora.resultado) == ("FALLIDA", "YA_INTERPRETADA")
    assert perdedora.firma_entrada == primera.firma_entrada
    assert str(primera.motor_pagos_run_id) in perdedora.detalle
    assert cuantos(ResultadoPagoObservado) == 30


def test_la_base_no_deja_dos_exitosas_con_la_misma_firma(tmp_path, monkeypatch):
    # Una segunda ejecucion que se salta la revision de la firma llega hasta el cierre: el indice de
    # las EXITOSA la detiene y queda FALLIDA, sin publicar nada.
    (primera,) = publicar(_pagos_de_septiembre(tmp_path))
    segunda = _abrir()
    monkeypatch.setattr(motor, "_ya_interpretada", lambda *_args: None)

    with pytest.raises(MotorPagosYaInterpretado) as ganadora:
        interpretar(segunda.id)

    assert ganadora.value.previa.id == primera.id
    perdedora = ejecuciones()[-1]
    assert (perdedora.estado, perdedora.resultado) == ("FALLIDA", "YA_INTERPRETADA")
    assert "mientras esta se procesaba" in perdedora.detalle
    assert cuantos(ResultadoPagoObservado) == 30
    assert vigente(SEPTIEMBRE).id == primera.id


def test_una_ejecucion_de_otra_version_no_se_interpreta_con_esta(tmp_path):
    _pagos_de_septiembre(tmp_path)
    historiar_todo()
    (abierta,) = ejecuciones()
    with sesion() as s:
        s.execute(
            text(
                "UPDATE ejecucion_motor_pagos SET version_motor = 'motor-pagos/v9' WHERE id = :id"
            ),
            {"id": abierta.id},
        )
        s.commit()

    interpretar(abierta.id)

    (terminada,) = ejecuciones()
    assert (terminada.estado, terminada.resultado) == ("FALLIDA", "VERSION_NO_SOPORTADA")
    assert cuantos(MovimientoEconomicoCanonico) == 0


def test_una_ventana_tiene_a_lo_mas_una_interpretacion_en_proceso(tmp_path):
    _pagos_de_septiembre(tmp_path)
    historiar_todo()
    (abierta,) = ejecuciones()

    with sesion() as s:
        reusada, nueva = abrir_ventana(s, VENTANA, max_intentos=5)
        s.commit()
        reusada_id = reusada.id

    assert (reusada_id, nueva) == (abierta.id, False)
    assert cuantos(EjecucionMotorPagos) == 1
    assert cuantos(TrabajoOrquestacion, TrabajoOrquestacion.tipo == TipoTrabajo.MOTOR_PAGOS) == 1


# --- pagos y cortes que llegan despues ------------------------------------------------------------


def test_un_archivo_nuevo_en_la_ventana_publica_otra_interpretacion_sin_tocar_la_anterior(
    tmp_path,
):
    (primera,) = publicar(_pagos_de_septiembre(tmp_path))
    antes = [(v.clasificacion, v.movimiento.movimiento_id) for v in resultados(primera)]

    (segunda,) = publicar(
        ingerir_pagos_de(tmp_path, "mas.csv", [pago(77, "2026-09-25 10:00:00", "900.00")])
    )

    assert segunda.id != primera.id and vigente(SEPTIEMBRE).id == segunda.id
    assert (primera.observaciones_leidas, segunda.observaciones_leidas) == (30, 31)
    assert segunda.firma_entrada != primera.firma_entrada
    # La anterior sigue como estaba, con sus resultados y sus movimientos.
    assert [(v.clasificacion, v.movimiento.movimiento_id) for v in resultados(primera)] == antes
    # Los movimientos que ya existian conservan su identificador en la interpretacion nueva.
    nuevos = {m.movimiento_id for m in movimientos(segunda)}
    assert {m for _, m in antes} < nuevos
    assert len(nuevos - {m for _, m in antes}) == 1
    assert segunda.recuperacion_bruta_interpretada == primera.recuperacion_bruta_interpretada + (
        Decimal("900.00")
    )


def test_un_pago_sin_cuenta_se_concilia_con_otra_interpretacion_cuando_llega_su_corte(
    tmp_path,
):
    from motor_cartera.motor_pagos import backfill

    ingesta = ingerir_pagos_de(tmp_path, "pagos.csv", [pago(9, "2026-09-01 12:00:00", "320.00")])
    (sin_cuenta,) = publicar(ingesta)
    assert resultados(sin_cuenta)[0].resultado.estado_conciliacion == "SIN_CUENTA_OBSERVADA"

    # Llega un corte, anterior al pago, que trae a la cuenta; su historia se materializa.
    ingerir_corte(tmp_path, date(2026, 8, 26), [Cuenta(9), Cuenta(10)])
    historiar_todo()
    with sesion() as s:
        diagnostico = backfill.diagnosticar(s)
    assert [v.desde for v in diagnostico.por_conciliar] == [SEPTIEMBRE]
    backfill.encolar(diagnostico.por_conciliar, config=Config())
    (conciliada,) = interpretar_todo()

    (visto,) = resultados(conciliada)
    assert visto.resultado.estado_conciliacion == "CONCILIADO_CUENTA"
    assert visto.movimiento.cuenta_canonica_id is not None
    # La interpretacion anterior queda intacta: decia lo que se sabia entonces.
    (antes,) = resultados(sin_cuenta)
    assert antes.resultado.estado_conciliacion == "SIN_CUENTA_OBSERVADA"
    assert antes.movimiento.cuenta_canonica_id is None
    assert antes.movimiento.movimiento_id == visto.movimiento.movimiento_id
    assert vigente(SEPTIEMBRE).id == conciliada.id


# --- el worker ------------------------------------------------------------------------------------


def test_el_worker_interpreta_despues_de_la_historia(tmp_path, trabajar):
    ingerir_corte(tmp_path, date(2026, 9, 2), [Cuenta(n) for n in range(1, 6)])
    _pagos_de_septiembre(tmp_path)

    procesados = trabajar()

    assert [p.tipo for p in procesados] == ["HISTORIA", "HISTORIA", "MOTOR_PAGOS"]
    assert {p.estado for p in procesados} == {"COMPLETADO"}
    assert vigente(SEPTIEMBRE).observaciones_clasificadas == 30


def test_la_interpretacion_que_agota_sus_intentos_queda_fallida_con_su_motivo(
    tmp_path, monkeypatch
):
    from motor_cartera.orquestacion import worker

    _pagos_de_septiembre(tmp_path)
    worker_id = worker.identificador_worker()
    # Primero la historia, que abre la ventana.
    assert worker.procesar_un_trabajo(worker_id, Config()).tipo == TipoTrabajo.HISTORIA

    def siempre_falla(_ejecucion_id: int) -> None:
        raise ConnectionError("la base se fue")

    monkeypatch.setitem(worker.MANEJADORES, TipoTrabajo.MOTOR_PAGOS, siempre_falla)
    with sesion() as s:
        s.execute(
            text("UPDATE trabajo_orquestacion SET max_intentos = 1 WHERE tipo = 'MOTOR_PAGOS'")
        )
        s.commit()

    procesado = worker.procesar_un_trabajo(worker_id, Config())

    assert (procesado.tipo, procesado.estado) == (TipoTrabajo.MOTOR_PAGOS, "FALLIDO")
    (ejecucion,) = ejecuciones()
    assert (ejecucion.estado, ejecucion.resultado) == ("FALLIDA", "INTENTOS_AGOTADOS")
    assert ejecucion.movimientos_canonicos == 0


def test_la_cola_toma_la_historia_antes_que_el_motor_de_pagos(tmp_path):
    from motor_cartera.orquestacion import cola, worker

    _pagos_de_septiembre(tmp_path, "uno.csv")
    hecho = worker.procesar_un_trabajo("worker-0", Config())
    assert (hecho.tipo, hecho.estado) == (TipoTrabajo.HISTORIA, "COMPLETADO")
    # La interpretacion de septiembre ya esta en la cola; despues llega otro archivo, con su
    # historia.
    ingerir_pagos_de(tmp_path, "dos.csv", [pago(50, "2026-09-25 10:00:00", "500.00")])

    primero = cola.reclamar("worker-a", 60)
    segundo = cola.reclamar("worker-b", 60)

    assert (primero.tipo, segundo.tipo) == (TipoTrabajo.HISTORIA, TipoTrabajo.MOTOR_PAGOS)
    assert segundo.id < primero.id
    with sesion() as s:
        assert s.exec(select(EjecucionMotorPagos.estado)).all() == ["EN_PROCESO"]
