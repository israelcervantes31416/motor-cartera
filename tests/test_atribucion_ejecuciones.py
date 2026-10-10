"""La atribucion en PostgreSQL: pagos interpretados de verdad, cada clasificacion con sus
candidatas, los eventos tardios, las anulaciones, los reversos, la ventana como parametro, la
idempotencia, la equivalencia con el nucleo puro, las fallas y su trabajo durable."""

from __future__ import annotations

import random
import threading
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from atribucion_escenarios import (
    SEPTIEMBRE,
    ZONA,
    abrir_ventana,
    atribuir_ventana,
    concluido,
    escenario,
)
from historia_escenarios import Cuenta, cuenta, ingerir_corte, ingerir_pagos_de, pago
from lifecycle_escenarios import anular_gestion, gestion_de
from motor_pagos_escenarios import historiar_todo, interpretar_todo, vigente
from sqlalchemy import event, func, text, update
from sqlmodel import select

from motor_cartera.atribucion import consultas
from motor_cartera.atribucion import ejecuciones as motor
from motor_cartera.atribucion.ejecuciones import (
    AtribucionEnProceso,
    AtribucionYaPublicada,
    abrir,
    atribuir,
)
from motor_cartera.atribucion.reglas import GestionLeida, MovimientoAtribuible
from motor_cartera.atribucion.reglas import atribuir as atribuir_puro
from motor_cartera.config import Config
from motor_cartera.db.modelos import (
    AtribucionMovimiento,
    CandidatoAtribucion,
    EjecucionAtribucion,
    EstadoTrabajo,
    TipoTrabajo,
    TrabajoOrquestacion,
)
from motor_cartera.db.sesion import crear_motor, sesion
from motor_cartera.lifecycle import consultas as lifecycle
from motor_cartera.lifecycle.reglas import NivelContacto, ResultadoGestion
from motor_cartera.motor_pagos.ejecuciones import Ventana
from motor_cartera.orquestacion import cola, objetivos, worker

pytestmark = pytest.mark.usefixtures("bd")

ESPERA = 60
DIA = 86_400


def _cuantos(modelo) -> int:
    with sesion() as s:
        return s.exec(select(func.count()).select_from(modelo)).one()


# --- lo que concluye de cada pago -----------------------------------------------------------------


def test_cada_pago_queda_con_sus_candidatas_y_nadie_elige_entre_varias(tmp_path):
    g = escenario(tmp_path)

    ejecucion = atribuir_ventana()

    assert (ejecucion.estado, ejecucion.resultado) == ("EXITOSA", "ATRIBUCION_PUBLICADA")
    pagos = concluido(ejecucion)
    assert pagos[1].clasificacion == "ASOCIACION_UNICA"
    assert (pagos[1].gestion_id, pagos[1].candidatas) == (g["1"], {g["1"]: 2 * DIA})
    # Dos candidatas igual de elegibles: ninguna se elige, ni la mas cercana.
    assert (pagos[2].clasificacion, pagos[2].gestion_id) == ("AMBIGUA", None)
    assert pagos[2].candidatas == {g["2_tercero"]: 3 * DIA, g["2_titular"]: DIA}
    assert [pagos[n].clasificacion for n in (3, 4, 5, 7, 9)] == ["SIN_GESTION_CANDIDATA"] * 5
    assert pagos[9].codigos == ["MOVIMIENTO_SIN_CUENTA"]
    # Anulado por un reverso: se clasifica igual, porque la gestion si lo antecedio.
    assert (pagos[6].clasificacion, pagos[6].gestion_id, pagos[6].anulado_por_reverso) == (
        "ASOCIACION_UNICA",
        g["6"],
        True,
    )
    assert pagos[6].codigos == ["UNA_GESTION_CANDIDATA", "ANULADO_POR_REVERSO"]
    assert (
        ejecucion.movimientos_evaluados,
        ejecucion.asociados,
        ejecucion.ambiguos,
        ejecucion.sin_candidato,
        ejecucion.candidatos,
        ejecucion.movimientos_anulados,
        ejecucion.gestiones_leidas,
        ejecucion.gestiones_anuladas,
    ) == (8, 2, 1, 5, 4, 1, 6, 1)
    # El monto del pago anulado no es recuperacion asociada: va aparte.
    assert (
        ejecucion.monto_asociado,
        ejecucion.monto_ambiguo,
        ejecucion.monto_sin_candidato,
        ejecucion.monto_anulado,
    ) == (Decimal("1000.00"), Decimal("500.00"), Decimal("720.00"), Decimal("400.00"))
    assert ejecucion.ejecucion_motor_pagos_id == vigente(SEPTIEMBRE.desde).id
    assert (ejecucion.ventana_dias, ejecucion.zona_horaria) == (30, ZONA)
    assert len(ejecucion.firma_entrada) == 64
    assert "1 reversos y posibles reversos no se atribuyen" in ejecucion.detalle
    assert "no causalidad" in ejecucion.detalle


def test_los_motivos_dicen_por_que_un_pago_no_tuvo_candidata(tmp_path):
    escenario(tmp_path)

    ejecucion = atribuir_ventana()

    with sesion() as s:
        motivos = dict(
            s.execute(
                text(
                    "SELECT m.cliente_unico, a.motivos FROM atribucion_movimiento a "
                    "JOIN movimiento_economico_canonico m "
                    "ON m.id = a.movimiento_economico_canonico_id "
                    "WHERE a.ejecucion_atribucion_id = :e"
                ),
                {"e": ejecucion.id},
            ).all()
        )
    sin = {"codigo": "SIN_GESTION_EN_LA_VENTANA", "ventana_dias": 30}
    assert motivos[cuenta(3).cliente_unico] == [
        {**sin, "gestiones_sin_contacto": 0, "gestiones_anuladas": 1}
    ]
    assert motivos[cuenta(4).cliente_unico] == [
        {**sin, "gestiones_sin_contacto": 1, "gestiones_anuladas": 0}
    ]
    assert motivos[cuenta(7).cliente_unico] == [
        {**sin, "gestiones_sin_contacto": 0, "gestiones_anuladas": 0}
    ]


def test_una_gestion_tardia_publica_otra_atribucion_y_la_primera_no_cambia(tmp_path):
    # La mision: pago el 10, atribucion, una gestion que ocurrio el 8 pero se registra despues, y
    # otra atribucion.
    escenario(tmp_path)
    primera = atribuir_ventana()
    antes = concluido(primera)
    assert antes[5].clasificacion == "SIN_GESTION_CANDIDATA"

    tardia = gestion_de(cuenta(5).cuenta_id, "2026-09-08T12:00:00-06:00").recurso_id
    segunda = atribuir_ventana()

    assert segunda.estado == "EXITOSA" and segunda.firma_entrada != primera.firma_entrada
    despues = concluido(segunda)
    assert (despues[5].clasificacion, despues[5].gestion_id) == ("ASOCIACION_UNICA", tardia)
    assert concluido(primera) == antes  # la primera se conserva intacta
    with sesion() as s:
        assert s.get_one(EjecucionAtribucion, primera.id).estado == "EXITOSA"
        assert consultas.vigentes(s) == [segunda.id]
        historia = consultas.de_movimiento(s, _movimiento_de(s, 5), desplazamiento=0, limite=10)[1]
    assert [(r.ejecucion.id, r.vigente, r.resultado.clasificacion) for r in historia] == [
        (segunda.id, True, "ASOCIACION_UNICA"),
        (primera.id, False, "SIN_GESTION_CANDIDATA"),
    ]


def test_una_gestion_anulada_deja_de_ser_candidata_y_sigue_en_la_auditoria(tmp_path):
    g = escenario(tmp_path)
    primera = atribuir_ventana()
    assert concluido(primera)[1].gestion_id == g["1"]

    anular_gestion(g["1"], "2026-09-12T09:00:00-06:00")
    segunda = atribuir_ventana()

    assert concluido(segunda)[1].clasificacion == "SIN_GESTION_CANDIDATA"
    assert concluido(segunda)[1].codigos == ["SIN_GESTION_EN_LA_VENTANA"]
    assert segunda.gestiones_anuladas == primera.gestiones_anuladas + 1
    assert concluido(primera)[1].gestion_id == g["1"]  # la anterior no se toca
    with sesion() as s:
        gestion = lifecycle.obtener_gestion(s, g["1"])
    assert gestion.estado == "ANULADA" and gestion.anulacion is not None


def test_una_gestion_en_el_instante_del_pago_es_candidata_y_una_despues_no(tmp_path):
    escenario(tmp_path)
    en_el_instante = gestion_de(cuenta(5).cuenta_id, "2026-09-10T10:00:00-06:00").recurso_id
    gestion_de(cuenta(4).cuenta_id, "2026-09-10T10:00:01-06:00")

    pagos = concluido(atribuir_ventana())

    assert pagos[5].candidatas == {en_el_instante: 0}
    assert pagos[4].clasificacion == "SIN_GESTION_CANDIDATA"


def test_la_ventana_es_un_parametro_de_la_ejecucion(tmp_path):
    escenario(tmp_path)
    # Exactamente 30 dias antes del pago de la 5 (que es a las 10:00 del 10 de septiembre).
    limite = gestion_de(cuenta(5).cuenta_id, "2026-08-11T10:00:00-06:00").recurso_id
    gestion_de(cuenta(5).cuenta_id, "2026-08-11T09:59:59-06:00")  # un segundo de mas

    con_30 = atribuir_ventana(ventana_dias=30)
    con_29 = atribuir_ventana(ventana_dias=29)

    assert concluido(con_30)[5].candidatas == {limite: 30 * DIA}
    assert concluido(con_29)[5].clasificacion == "SIN_GESTION_CANDIDATA"
    # Otra ventana es otra entrada: se publica, no es YA_ATRIBUIDA.
    assert (con_29.estado, con_29.ventana_dias) == ("EXITOSA", 29)
    assert con_29.firma_entrada != con_30.firma_entrada
    assert concluido(con_29)[2].clasificacion == "AMBIGUA"  # del 7 y del 9: dentro de 29


def test_las_mismas_entradas_no_se_publican_dos_veces(tmp_path):
    escenario(tmp_path)
    primera = atribuir_ventana()

    with pytest.raises(AtribucionYaPublicada) as error:
        atribuir(abrir_ventana())

    assert error.value.previa.id == primera.id
    with sesion() as s:
        ejecuciones = s.exec(select(EjecucionAtribucion).order_by(EjecucionAtribucion.id)).all()
    assert [(e.estado, e.resultado) for e in ejecuciones] == [
        ("EXITOSA", "ATRIBUCION_PUBLICADA"),
        ("FALLIDA", "YA_ATRIBUIDA"),
    ]
    assert ejecuciones[1].firma_entrada == primera.firma_entrada
    assert _cuantos(AtribucionMovimiento) == 8


def test_una_ventana_sin_interpretacion_de_pagos_falla_sin_publicar(tmp_path):
    escenario(tmp_path)
    octubre = Ventana.del_periodo(SEPTIEMBRE.despacho_id, SEPTIEMBRE.cartera_id, date(2026, 10, 1))

    ejecucion = atribuir_ventana(octubre)

    assert (ejecucion.estado, ejecucion.resultado) == ("FALLIDA", "SIN_INTERPRETACION_DE_PAGOS")
    assert ejecucion.ejecucion_motor_pagos_id is None and ejecucion.movimientos_evaluados == 0
    assert _cuantos(AtribucionMovimiento) == 0


def test_una_interpretacion_nueva_de_los_pagos_se_atribuye_aparte(tmp_path):
    escenario(tmp_path)
    primera = atribuir_ventana()
    # Llega otro archivo de septiembre: el motor publica otra interpretacion de la ventana.
    ingerir_pagos_de(tmp_path, "tarde.csv", [pago(5, "2026-09-15 10:00:00", "10.00")])
    historiar_todo()
    interpretar_todo()

    with sesion() as s:
        vista = consultas.obtener(s, primera.atribucion_run_id)
    assert vista.vigente and vista.interpretacion_vigente is False

    segunda = atribuir_ventana()

    assert segunda.ejecucion_motor_pagos_id == vigente(SEPTIEMBRE.desde).id
    assert segunda.ejecucion_motor_pagos_id != primera.ejecucion_motor_pagos_id
    assert segunda.movimientos_evaluados == primera.movimientos_evaluados + 1
    with sesion() as s:
        assert consultas.obtener(s, segunda.atribucion_run_id).interpretacion_vigente is True


# --- el SQL dice lo mismo que el nucleo puro ------------------------------------------------------


def test_el_sql_y_el_nucleo_puro_concluyen_lo_mismo_de_cada_pago(tmp_path):
    azar = random.Random(271828)
    numeros = range(1, 41)
    ingerir_corte(tmp_path, date(2026, 9, 1), [Cuenta(n) for n in numeros])
    historiar_todo()
    filas = []
    for numero in [*numeros, 90, 91]:  # 90 y 91 no estan en el corte: sus pagos no tienen cuenta
        for _ in range(azar.randint(0, 3)):
            dia, hora, minuto = azar.randint(1, 30), azar.randint(7, 21), azar.choice([0, 30])
            monto = azar.choice(["150.00", "300.00", "500.00", "1000.00"])
            filas.append(pago(numero, f"2026-09-{dia:02d} {hora:02d}:{minuto:02d}:00", monto))
    filas.append(pago(1, "2026-09-03 10:00:00", "-150.00", **{"Concepto_Cálculo": "AJUSTE"}))
    ingerir_pagos_de(tmp_path, "pagos.csv", filas)
    historiar_todo()
    interpretar_todo()
    niveles = [
        (NivelContacto.CONTACTO_TITULAR, ResultadoGestion.PROMESA),
        (NivelContacto.CONTACTO_TERCERO, ResultadoGestion.CONTACTO),
        (NivelContacto.SIN_CONTACTO, ResultadoGestion.SIN_RESPUESTA),
    ]
    for numero in numeros:
        for _ in range(azar.randint(0, 4)):
            nivel, resultado = azar.choice(niveles)
            cuando = datetime(2026, 8, 1, 8) + timedelta(minutes=azar.randint(0, 60 * 24 * 60))
            registro = gestion_de(
                cuenta(numero).cuenta_id,
                f"{cuando:%Y-%m-%dT%H:%M:00}-06:00",
                nivel_contacto=nivel,
                resultado=resultado,
            )
            if azar.random() < 0.15:
                anular_gestion(registro.recurso_id, "2026-10-05T09:00:00-06:00")

    ejecucion = atribuir_ventana(ventana_dias=15)

    esperado = _atribuir_con_el_nucleo(ejecucion, ventana_dias=15)
    obtenido = {
        movimiento_id: (r.clasificacion, r.candidatos, r.anulado_por_reverso, r.motivos)
        for movimiento_id, r in _publicados(ejecucion).items()
    }
    assert obtenido == {
        m: (a, len(c), anulado, motivos) for m, (a, c, anulado, motivos) in esperado.items()
    }
    with sesion() as s:
        candidatas = {
            (m, g): segundos
            for m, g, segundos in s.execute(
                text(
                    "SELECT a.movimiento_id, g.gestion_id, c.antelacion_segundos "
                    "FROM candidato_atribucion c JOIN atribucion_movimiento a "
                    "ON a.ejecucion_atribucion_id = c.ejecucion_atribucion_id "
                    "AND a.movimiento_economico_canonico_id = c.movimiento_economico_canonico_id "
                    "JOIN gestion_cobranza g ON g.id = c.gestion_cobranza_id "
                    "WHERE c.ejecucion_atribucion_id = :e"
                ),
                {"e": ejecucion.id},
            ).all()
        }
    assert candidatas == {
        (m, c.gestion_id): c.antelacion_segundos
        for m, (_, todas, _, _) in esperado.items()
        for c in todas
    }
    clases = {clase for clase, *_ in obtenido.values()}
    assert clases == {"SIN_GESTION_CANDIDATA", "ASOCIACION_UNICA", "AMBIGUA"}


def _publicados(ejecucion) -> dict[UUID, AtribucionMovimiento]:
    with sesion() as s:
        return {
            r.movimiento_id: r
            for r in s.exec(
                select(AtribucionMovimiento).where(
                    AtribucionMovimiento.ejecucion_atribucion_id == ejecucion.id
                )
            ).all()
        }


def _atribuir_con_el_nucleo(ejecucion, *, ventana_dias: int) -> dict:
    """Lo mismo, leido de la base fila por fila y atribuido con atribucion.reglas."""
    from zoneinfo import ZoneInfo

    zona = ZoneInfo(ZONA)
    with sesion() as s:
        movimientos = [
            MovimientoAtribuible(
                movimiento_id=m_id,
                cuenta=cuenta_id,
                instante=recepcion.replace(tzinfo=zona),
                monto=monto,
                anulado_por=anulado_por,
            )
            for m_id, cuenta_id, recepcion, monto, anulado_por in s.execute(
                text(
                    "SELECT movimiento_id, cuenta_canonica_id, fecha_recepcion, monto_reportado, "
                    "anulado_por_movimiento_id FROM movimiento_economico_canonico "
                    "WHERE ejecucion_motor_pagos_id = :motor AND tipo_movimiento = 'PAGO'"
                ),
                {"motor": ejecucion.ejecucion_motor_pagos_id},
            ).all()
        ]
        gestiones = [
            GestionLeida(gestion_id, cuenta_id, ocurrido_en, NivelContacto(nivel), anulada)
            for gestion_id, cuenta_id, ocurrido_en, nivel, anulada in s.execute(
                text(
                    "SELECT g.gestion_id, g.cuenta_canonica_id, g.ocurrido_en, g.nivel_contacto, "
                    "EXISTS (SELECT 1 FROM evento_lifecycle a WHERE a.evento_relacionado_id = "
                    "g.evento_lifecycle_id AND a.tipo_evento = 'GESTION_ANULADA') "
                    "FROM gestion_cobranza g"
                )
            ).all()
        ]
    return {
        a.movimiento_id: (
            a.clasificacion.value,
            a.candidatas,
            next(
                m.anulado_por is not None for m in movimientos if m.movimiento_id == a.movimiento_id
            ),
            list(a.motivos),
        )
        for a in atribuir_puro(movimientos, gestiones, ventana_dias)
    }


# --- por conjuntos --------------------------------------------------------------------------------


def _sentencias_al_atribuir(ejecucion_id: int) -> int:
    """Cuantas sentencias manda la atribucion a la base."""
    contadas = []

    def contar(*_args):
        contadas.append(1)

    motor_bd = crear_motor()
    event.listen(motor_bd, "before_cursor_execute", contar)
    try:
        atribuir(ejecucion_id)
    finally:
        event.remove(motor_bd, "before_cursor_execute", contar)
    return len(contadas)


def test_la_atribucion_no_hace_una_consulta_por_pago(tmp_path):
    ingerir_corte(tmp_path, date(2026, 9, 1), [Cuenta(n) for n in range(1, 61)])
    historiar_todo()
    ingerir_pagos_de(
        tmp_path, "pocos.csv", [pago(n, "2026-08-20 10:00:00", "100.00") for n in range(1, 3)]
    )
    ingerir_pagos_de(
        tmp_path, "muchos.csv", [pago(n, "2026-09-20 10:00:00", "100.00") for n in range(1, 61)]
    )
    historiar_todo()
    interpretar_todo()
    for n in range(1, 61):
        gestion_de(cuenta(n).cuenta_id, "2026-08-19T10:00:00-06:00")
        gestion_de(cuenta(n).cuenta_id, "2026-09-19T10:00:00-06:00")
    agosto = Ventana.del_periodo(SEPTIEMBRE.despacho_id, SEPTIEMBRE.cartera_id, date(2026, 8, 1))

    con_2 = _sentencias_al_atribuir(abrir_ventana(agosto))
    con_60 = _sentencias_al_atribuir(abrir_ventana())

    with sesion() as s:
        conteos = s.exec(
            select(EjecucionAtribucion.movimientos_evaluados).order_by(EjecucionAtribucion.id)
        ).all()
    assert conteos == [2, 60]
    assert con_60 == con_2  # las mismas sentencias para 2 pagos que para 60


# --- todo o nada ----------------------------------------------------------------------------------


class Falla(Exception):
    pass


def _lanzar(*_args, **_kwargs):
    raise Falla("inyectada")


@pytest.mark.parametrize("donde", ["_firma_de_entrada", "_contar", "_cerrar"])
def test_una_falla_no_publica_nada_y_otra_ejecucion_la_termina(tmp_path, monkeypatch, donde):
    escenario(tmp_path)
    ejecucion_id = abrir_ventana()
    monkeypatch.setattr(motor, donde, _lanzar)

    atribuir(ejecucion_id)

    with sesion() as s:
        fallida = s.get_one(EjecucionAtribucion, ejecucion_id)
        for tabla in ("at_movimiento", "at_gestion", "at_par", "at_resumen"):
            assert s.execute(text(f"SELECT to_regclass('pg_temp.{tabla}')")).scalar() is None
    assert (fallida.estado, fallida.resultado) == ("FALLIDA", "ERROR_INTERNO")
    assert fallida.movimientos_evaluados == 0 and fallida.candidatos == 0
    assert _cuantos(AtribucionMovimiento) == _cuantos(CandidatoAtribucion) == 0
    if donde != "_firma_de_entrada":
        # Alcanzo a leer: lo dice, sin haber publicado.
        assert (fallida.gestiones_leidas, fallida.gestiones_anuladas) == (6, 1)
    monkeypatch.undo()
    assert atribuir_ventana().movimientos_evaluados == 8


def test_una_ventana_se_atribuye_a_lo_mas_una_vez_a_la_vez(tmp_path):
    escenario(tmp_path)
    abierta = abrir_ventana()

    with sesion() as s, pytest.raises(AtribucionEnProceso) as error:
        abrir(s, SEPTIEMBRE, ventana_dias=30, zona=ZONA, max_intentos=5, reusar=False)
    assert error.value.activa.id == abierta
    with sesion() as s:
        reusada, nueva = abrir(
            s, SEPTIEMBRE, ventana_dias=7, zona=ZONA, max_intentos=5, reusar=True
        )
    assert (reusada.id, reusada.ventana_dias, nueva) == (abierta, 30, False)
    assert _cuantos(TrabajoOrquestacion) >= 1


def test_la_cola_que_agota_sus_intentos_la_deja_fallida_sin_publicar(tmp_path):
    escenario(tmp_path)
    ejecucion_id = abrir_ventana()

    with sesion() as s:
        assert objetivos.fallar(s, TipoTrabajo.ATRIBUCION, ejecucion_id, "Se agotaron.")
        s.commit()
        fallida = s.get_one(EjecucionAtribucion, ejecucion_id)
        assert not objetivos.fallar(s, TipoTrabajo.ATRIBUCION, ejecucion_id, "Otra vez.")

    assert (fallida.estado, fallida.resultado, fallida.detalle) == (
        "FALLIDA",
        "INTENTOS_AGOTADOS",
        "Se agotaron.",
    )


# --- varios workers -------------------------------------------------------------------------------


def _en_otro_hilo(funcion, *argumentos) -> tuple[threading.Thread, dict]:
    salida: dict = {}

    def correr() -> None:
        try:
            salida["resultado"] = funcion(*argumentos)
        except BaseException as exc:
            salida["error"] = exc

    hilo = threading.Thread(target=correr, daemon=True)
    hilo.start()
    return hilo, salida


def _detener_en(monkeypatch, funcion: str) -> tuple[threading.Event, threading.Event]:
    """El primer hilo que llega a `funcion` la ejecuta y se detiene despues, hasta que la prueba lo
    suelte. Los demas pasan de largo."""
    llego, soltar = threading.Event(), threading.Event()
    original = getattr(motor, funcion)

    def envoltura(*args, **kwargs):
        hecho = original(*args, **kwargs)
        if not llego.is_set():
            llego.set()
            assert soltar.wait(ESPERA)
        return hecho

    monkeypatch.setattr(motor, funcion, envoltura)
    return llego, soltar


def test_dos_workers_con_la_misma_atribucion_publican_una_sola_vez(tmp_path, monkeypatch):
    escenario(tmp_path)
    ejecucion_id = abrir_ventana()
    llego, soltar = _detener_en(monkeypatch, "_contar")

    primero, salida_1 = _en_otro_hilo(atribuir, ejecucion_id)
    assert llego.wait(ESPERA)
    # El segundo llega mientras el primero tiene la ejecucion bloqueada: espera.
    segundo, salida_2 = _en_otro_hilo(atribuir, ejecucion_id)
    time.sleep(0.5)
    assert segundo.is_alive()

    soltar.set()
    for hilo in (primero, segundo):
        hilo.join(ESPERA)

    assert "error" not in salida_1 and "error" not in salida_2
    with sesion() as s:
        (terminada,) = s.exec(select(EjecucionAtribucion)).all()
    assert (terminada.estado, terminada.resultado) == ("EXITOSA", "ATRIBUCION_PUBLICADA")
    assert _cuantos(AtribucionMovimiento) == 8 and _cuantos(CandidatoAtribucion) == 4


def test_un_worker_que_pierde_el_lease_a_media_atribucion_no_publica(tmp_path, monkeypatch):
    escenario(tmp_path)
    with sesion() as s:
        # La historia y el motor se ejecutaron a mano: sus trabajos se dan por terminados, para que
        # la cola solo tenga el de la atribucion.
        s.execute(
            update(TrabajoOrquestacion).values(
                estado=EstadoTrabajo.COMPLETADO, terminado_en=func.now()
            )
        )
        s.commit()
    ejecucion_id = abrir_ventana()
    # Sin latidos durante la prueba: el lease vence porque la prueba lo vence.
    config = Config(worker_lease_segundos=600, worker_heartbeat_segundos=300)
    calculo, soltar = _detener_en(monkeypatch, "_contar")
    reclamo_a = cola.reclamar("worker-a", config.worker_lease_segundos)
    assert reclamo_a.tipo == TipoTrabajo.ATRIBUCION
    hilo_a, salida_a = _en_otro_hilo(worker.procesar_reclamo, reclamo_a, "worker-a", config)
    assert calculo.wait(ESPERA)

    # A ya publico en su transaccion, pero su lease vence antes de que confirme: B toma el trabajo.
    with sesion() as s:
        s.execute(
            update(TrabajoOrquestacion)
            .where(TrabajoOrquestacion.id == reclamo_a.id)
            .values(lease_hasta=func.now() - timedelta(seconds=1))
        )
        s.commit()
    hilo_b, salida_b = _en_otro_hilo(worker.procesar_un_trabajo, "worker-b", config)
    time.sleep(0.5)
    assert hilo_b.is_alive()  # espera la ejecucion, que A todavia tiene bloqueada

    soltar.set()
    for hilo in (hilo_a, hilo_b):
        hilo.join(ESPERA)

    assert "error" not in salida_a and "error" not in salida_b
    # A no publico: al confirmar, su trabajo ya era de B, y se revirtio entero.
    assert salida_a["resultado"].estado is None
    assert salida_b["resultado"].estado == EstadoTrabajo.COMPLETADO
    with sesion() as s:
        trabajo = s.get_one(TrabajoOrquestacion, reclamo_a.id)
        terminada = s.get_one(EjecucionAtribucion, ejecucion_id)
    assert (trabajo.estado, trabajo.intentos, trabajo.worker_id) == ("COMPLETADO", 2, None)
    assert (terminada.estado, terminada.resultado) == ("EXITOSA", "ATRIBUCION_PUBLICADA")
    assert _cuantos(AtribucionMovimiento) == 8 and _cuantos(EjecucionAtribucion) == 1


# --- su trabajo durable ---------------------------------------------------------------------------


def test_el_worker_atribuye_despues_del_motor_de_pagos_y_cierra_su_trabajo(tmp_path, trabajar):
    ingerir_corte(tmp_path, date(2026, 9, 1), [Cuenta(n) for n in range(1, 4)])
    trabajar()  # la historia del corte
    gestion = gestion_de(cuenta(1).cuenta_id, "2026-09-08T10:00:00-06:00").recurso_id
    # Una primera interpretacion de septiembre, para poder pedir la atribucion.
    ingerir_pagos_de(tmp_path, "uno.csv", [pago(2, "2026-09-05 10:00:00", "300.00")])
    trabajar()
    # Otro archivo llega despues de pedirla: su historia y su interpretacion van antes.
    ejecucion_id = abrir_ventana()
    ingerir_pagos_de(tmp_path, "dos.csv", [pago(1, "2026-09-10 10:00:00", "1000.00")])

    procesados = trabajar()

    tipos = [p.tipo for p in procesados]
    assert tipos.index(TipoTrabajo.MOTOR_PAGOS) < tipos.index(TipoTrabajo.ATRIBUCION)
    with sesion() as s:
        ejecucion = s.get_one(EjecucionAtribucion, ejecucion_id)
        trabajo = s.exec(
            select(TrabajoOrquestacion).where(
                TrabajoOrquestacion.ejecucion_atribucion_id == ejecucion_id
            )
        ).one()
    assert (ejecucion.estado, ejecucion.movimientos_evaluados) == ("EXITOSA", 2)
    assert concluido(ejecucion)[1].gestion_id == gestion
    assert (trabajo.tipo, trabajo.estado) == ("ATRIBUCION", "COMPLETADO")


# --- lo que lee la API ----------------------------------------------------------------------------


def _movimiento_de(s, numero: int) -> UUID:
    return s.execute(
        text(
            "SELECT movimiento_id FROM movimiento_economico_canonico "
            "WHERE cliente_unico = :cliente AND tipo_movimiento = 'PAGO' ORDER BY id DESC LIMIT 1"
        ),
        {"cliente": cuenta(numero).cliente_unico},
    ).scalar_one()


def test_la_ultima_atribucion_de_una_cuenta_es_la_de_su_pago_mas_reciente(tmp_path):
    g = escenario(
        tmp_path,
        pagos=[
            pago(1, "2026-09-03 10:00:00", "100.00"),
            pago(1, "2026-09-10 10:00:00", "1000.00"),
        ],
    )
    gestion_de(cuenta(1).cuenta_id, "2026-09-02T10:00:00-06:00")
    ejecucion = atribuir_ventana()

    with sesion() as s:
        cuenta_1 = cuenta(1)
        hoy = consultas.ultima_de_cuenta(s, cuenta_1, version_motor="motor-pagos/v1")
        al_5 = consultas.ultima_de_cuenta(
            s, cuenta_1, version_motor="motor-pagos/v1", al=date(2026, 9, 5)
        )
        al_1 = consultas.ultima_de_cuenta(
            s, cuenta_1, version_motor="motor-pagos/v1", al=date(2026, 9, 1)
        )
        sin_pagos = consultas.ultima_de_cuenta(s, cuenta(5), version_motor="motor-pagos/v1")
        total, pagina = consultas.de_cuenta(
            s, cuenta_1, version_motor="motor-pagos/v1", desplazamiento=0, limite=10
        )

    # El del 10 tiene dos candidatas (la del 2 y la del 8); el del 3, solo la del 2.
    assert (hoy.ejecucion.id, hoy.resultado.clasificacion) == (ejecucion.id, "AMBIGUA")
    assert hoy.resultado.fecha_recepcion == datetime(2026, 9, 10, 10)
    assert (al_5.resultado.clasificacion, al_5.resultado.fecha_recepcion) == (
        "ASOCIACION_UNICA",
        datetime(2026, 9, 3, 10),
    )
    assert al_1 is None and sin_pagos is None
    assert total == 2
    assert [p.atribucion.resultado.clasificacion for p in pagina] == ["AMBIGUA", "ASOCIACION_UNICA"]
    assert [c.gestion_id for c in pagina[0].atribucion.candidatas][0] == g["1"]  # la mas proxima
