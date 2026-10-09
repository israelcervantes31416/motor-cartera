"""La evaluacion de promesas en PostgreSQL: el escenario de la mision con pagos interpretados de
verdad, la fecha de corte explicita, la idempotencia, los datos que llegan despues, lo que no se
puede evaluar, la equivalencia con el nucleo puro, las fallas y su trabajo durable."""

from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from historia_escenarios import Cuenta, cuenta, ingerir_corte, ingerir_pagos_de, pago
from lifecycle_escenarios import (
    anular_gestion,
    cancelar_promesa,
    gestion_de,
    llave,
    promesa_de,
)
from motor_pagos_escenarios import historiar_todo, interpretar_todo
from sqlalchemy import func, text
from sqlmodel import select

from motor_cartera.config import Config
from motor_cartera.db.modelos import (
    EjecucionEvaluacionPromesas,
    EvaluacionPromesa,
    PromesaPago,
    TipoTrabajo,
    TrabajoOrquestacion,
)
from motor_cartera.db.sesion import sesion
from motor_cartera.evaluacion import ejecuciones as motor
from motor_cartera.evaluacion.ejecuciones import (
    EvaluacionEnProceso,
    EvaluacionYaPublicada,
    abrir,
    evaluar,
)
from motor_cartera.evaluacion.reglas import Corte, Movimiento, PromesaEvaluable
from motor_cartera.evaluacion.reglas import evaluar as evaluar_puro

pytestmark = pytest.mark.usefixtures("bd")

ZONA = "America/Mexico_City"
DESPACHO, CARTERA = "DSP_001", "CARTERA_PRINCIPAL"


def _escenario(tmp_path, *, pagos=None) -> dict[int, UUID]:
    """Seis cuentas, cada una con una promesa de $1,000 acordada el 5 de septiembre con fecha limite
    el 15; la 4 la cancela el 8. La 1 paga 1,000 el 10, la 2 paga 500 el 12, la 3 nada, la 5 paga
    1,000 el 20 (tarde, y es el horizonte de los datos) y la 6 pago 1,000 el 3, antes de
    prometer."""
    ingerir_corte(tmp_path, date(2026, 9, 1), [Cuenta(n) for n in range(1, 9)])
    historiar_todo()
    ingerir_pagos_de(
        tmp_path,
        "pagos.csv",
        pagos
        or [
            pago(1, "2026-09-10 10:00:00", "1000.00"),
            pago(2, "2026-09-12 10:00:00", "500.00"),
            pago(5, "2026-09-20 10:00:00", "1000.00"),
            pago(6, "2026-09-03 10:00:00", "1000.00"),
        ],
    )
    historiar_todo()
    interpretar_todo()
    promesas = {}
    for numero in range(1, 7):
        gestion = gestion_de(cuenta(numero).cuenta_id, "2026-09-05T10:00:00-06:00")
        promesas[numero] = promesa_de(gestion.recurso_id).recurso_id
    cancelar_promesa(promesas[4], "2026-09-08T09:00:00-06:00")
    return promesas


def _abrir(as_of: date) -> int:
    with sesion() as s:
        ejecucion, nueva = abrir(
            s, DESPACHO, CARTERA, as_of, zona=ZONA, max_intentos=5, reusar=False
        )
        s.commit()
        assert nueva
        return ejecucion.id


def _evaluar(as_of: date) -> EjecucionEvaluacionPromesas:
    ejecucion_id = _abrir(as_of)
    evaluar(ejecucion_id)
    with sesion() as s:
        return s.get_one(EjecucionEvaluacionPromesas, ejecucion_id)


def _estados(ejecucion: EjecucionEvaluacionPromesas) -> dict[UUID, tuple[str, Decimal]]:
    with sesion() as s:
        filas = s.exec(
            select(
                PromesaPago.promesa_id, EvaluacionPromesa.estado, EvaluacionPromesa.monto_observado
            )
            .join(PromesaPago, PromesaPago.id == EvaluacionPromesa.promesa_pago_id)
            .where(EvaluacionPromesa.ejecucion_evaluacion_promesas_id == ejecucion.id)
        ).all()
    return {promesa: (estado, monto) for promesa, estado, monto in filas}


def _cuantos(modelo) -> int:
    with sesion() as s:
        return s.exec(select(func.count()).select_from(modelo)).one()


# --- la evaluacion a una fecha explicita ----------------------------------------------------------


def test_al_dia_16_cada_promesa_queda_como_dicen_sus_pagos(tmp_path):
    promesas = _escenario(tmp_path)

    ejecucion = _evaluar(date(2026, 9, 16))

    assert (ejecucion.estado, ejecucion.resultado) == ("EXITOSA", "EVALUACION_PUBLICADA")
    estados = _estados(ejecucion)
    assert {n: estados[p] for n, p in promesas.items()} == {
        1: ("CUMPLIDA", Decimal("1000.00")),
        2: ("PARCIAL", Decimal("500.00")),
        3: ("INCUMPLIDA", Decimal("0.00")),
        4: ("CANCELADA", Decimal("0.00")),
        5: ("INCUMPLIDA", Decimal("0.00")),  # pago despues de su fecha limite
        6: ("INCUMPLIDA", Decimal("0.00")),  # pago antes de prometer
    }
    assert (
        ejecucion.promesas_evaluadas,
        ejecucion.cumplidas,
        ejecucion.parciales,
        ejecucion.incumplidas,
        ejecucion.canceladas,
        ejecucion.pendientes,
        ejecucion.no_evaluables,
    ) == (6, 1, 1, 3, 1, 0, 0)
    assert (ejecucion.monto_prometido, ejecucion.monto_observado) == (
        Decimal("6000.00"),
        Decimal("1500.00"),
    )
    assert ejecucion.horizonte_pagos == datetime(2026, 9, 20, 10, 0)
    assert "no causalidad" in ejecucion.detalle
    assert ejecucion.firma_entrada and len(ejecucion.firma_entrada) == 64


def test_otra_fecha_de_corte_ve_otra_cosa_con_los_mismos_datos(tmp_path):
    promesas = _escenario(tmp_path)

    al_14 = _estados(_evaluar(date(2026, 9, 14)))
    al_7 = _estados(_evaluar(date(2026, 9, 7)))

    assert {n: al_14[p][0] for n, p in promesas.items()} == {
        1: "CUMPLIDA",  # pago el 10: ya se cumplio, aunque no ha vencido
        2: "PENDIENTE",
        3: "PENDIENTE",
        4: "CANCELADA",  # se cancelo el 8
        5: "PENDIENTE",
        6: "PENDIENTE",
    }
    # El 7 la 4 todavia no se cancelaba, y la 1 todavia no pagaba.
    assert al_7[promesas[4]][0] == al_7[promesas[1]][0] == "PENDIENTE"


def test_la_misma_fecha_con_los_mismos_datos_no_se_publica_dos_veces(tmp_path):
    _escenario(tmp_path)
    primera = _evaluar(date(2026, 9, 16))

    with pytest.raises(EvaluacionYaPublicada) as error:
        evaluar(_abrir(date(2026, 9, 16)))

    assert error.value.previa.id == primera.id
    with sesion() as s:
        ejecuciones = s.exec(
            select(EjecucionEvaluacionPromesas).order_by(EjecucionEvaluacionPromesas.id)
        ).all()
    assert [(e.estado, e.resultado) for e in ejecuciones] == [
        ("EXITOSA", "EVALUACION_PUBLICADA"),
        ("FALLIDA", "YA_EVALUADA"),
    ]
    assert _cuantos(EvaluacionPromesa) == 6


def test_un_dato_que_llega_despues_publica_otra_evaluacion_y_la_anterior_no_cambia(tmp_path):
    promesas = _escenario(tmp_path)
    primera = _evaluar(date(2026, 9, 16))
    antes = _estados(primera)

    # Una cancelacion que ocurrio el 9 y se registra hoy: un evento tardio.
    cancelar_promesa(promesas[3], "2026-09-09T12:00:00-06:00")
    segunda = _evaluar(date(2026, 9, 16))

    assert segunda.estado == "EXITOSA" and segunda.firma_entrada != primera.firma_entrada
    assert _estados(segunda)[promesas[3]][0] == "CANCELADA"
    assert _estados(primera) == antes  # la evaluacion anterior no se toca
    with sesion() as s:
        from motor_cartera.lifecycle import consultas

        vista = consultas.obtener_promesa(s, promesas[3])
    assert vista.ultima_evaluacion.ejecucion.id == segunda.id


def test_una_gestion_anulada_deja_su_promesa_cancelada_en_la_evaluacion(tmp_path):
    promesas = _escenario(tmp_path)
    with sesion() as s:
        from motor_cartera.db.modelos import GestionCobranza

        gestion_id = s.exec(
            select(GestionCobranza.gestion_id)
            .join(PromesaPago, PromesaPago.gestion_cobranza_id == GestionCobranza.id)
            .where(PromesaPago.promesa_id == promesas[1])
        ).one()
    anular_gestion(gestion_id, "2026-09-30T09:00:00-06:00")

    estados = _estados(_evaluar(date(2026, 9, 16)))

    # Anulada despues del 16: se registro por error, y eso vale hacia atras.
    assert estados[promesas[1]] == ("CANCELADA", Decimal("1000.00"))


def test_sin_pagos_mas_alla_de_su_fecha_limite_no_se_afirma_un_incumplimiento(tmp_path):
    promesas = _escenario(
        tmp_path,
        pagos=[
            pago(1, "2026-09-10 10:00:00", "1000.00"),
            pago(2, "2026-09-12 10:00:00", "500.00"),
        ],
    )

    estados = _estados(_evaluar(date(2026, 9, 16)))

    # Los pagos de la cartera llegan hasta el 12: lo que no alcanzo el monto no se puede juzgar.
    assert {n: estados[p][0] for n, p in promesas.items()} == {
        1: "CUMPLIDA",
        2: "NO_EVALUABLE",
        3: "NO_EVALUABLE",
        4: "CANCELADA",
        5: "NO_EVALUABLE",
        6: "NO_EVALUABLE",
    }


def test_con_pagos_sin_interpretar_en_su_intervalo_no_se_evalua(tmp_path):
    promesas = _escenario(tmp_path)
    # Un archivo nuevo de septiembre: su historia abre la interpretacion, que nadie ejecuta todavia.
    ingerir_pagos_de(tmp_path, "tarde.csv", [pago(7, "2026-09-11 10:00:00", "50.00")])
    historiar_todo()

    estados = _estados(_evaluar(date(2026, 9, 16)))

    assert estados[promesas[1]][0] == "CUMPLIDA"
    assert estados[promesas[3]][0] == "NO_EVALUABLE"
    with sesion() as s:
        motivos = s.exec(
            select(EvaluacionPromesa.motivos)
            .join(PromesaPago, PromesaPago.id == EvaluacionPromesa.promesa_pago_id)
            .where(PromesaPago.promesa_id == promesas[3])
        ).one()
    assert motivos == [{"codigo": "PAGOS_SIN_INTERPRETAR"}]


# --- el SQL dice lo mismo que el nucleo puro ------------------------------------------------------


def test_el_sql_y_el_nucleo_puro_concluyen_lo_mismo_de_cada_promesa(tmp_path):
    azar = random.Random(31416)
    numeros = range(1, 31)
    ingerir_corte(tmp_path, date(2026, 9, 1), [Cuenta(n) for n in numeros])
    historiar_todo()
    filas = []
    for numero in numeros:
        for _ in range(azar.randint(0, 3)):
            dia, hora = azar.randint(2, 28), azar.randint(8, 20)
            monto = azar.choice(["150.00", "300.00", "500.00", "1000.00"])
            filas.append(pago(numero, f"2026-09-{dia:02d} {hora:02d}:00:00", monto))
    filas.append(pago(1, "2026-09-29 18:00:00", "10.00"))  # el horizonte
    ingerir_pagos_de(tmp_path, "pagos.csv", filas)
    historiar_todo()
    interpretar_todo()
    for numero in numeros:
        gestion = gestion_de(
            cuenta(numero).cuenta_id,
            f"2026-09-{azar.randint(2, 20):02d}T{azar.randint(8, 18):02d}:00:00-06:00",
        )
        limite = f"2026-09-{azar.randint(21, 28):02d}"
        promesa = promesa_de(gestion.recurso_id, azar.choice(["500.00", "1000.00"]), limite)
        if azar.random() < 0.15:
            cancelar_promesa(promesa.recurso_id, "2026-09-24T09:00:00-06:00")
        if azar.random() < 0.1:
            anular_gestion(gestion.recurso_id, "2026-09-25T09:00:00-06:00")
    as_of = date(2026, 9, 25)

    ejecucion = _evaluar(as_of)

    esperado = _evaluar_con_el_nucleo(as_of)
    with sesion() as s:
        publicadas = s.exec(
            select(PromesaPago.promesa_id, EvaluacionPromesa)
            .join(PromesaPago, PromesaPago.id == EvaluacionPromesa.promesa_pago_id)
            .where(EvaluacionPromesa.ejecucion_evaluacion_promesas_id == ejecucion.id)
        ).all()
    obtenido = {
        promesa_id: (
            e.estado,
            e.monto_observado,
            e.movimientos_compatibles,
            [m["codigo"] for m in e.motivos],
        )
        for promesa_id, e in publicadas
    }
    assert obtenido == esperado
    assert {estado for estado, *_ in obtenido.values()} >= {"PENDIENTE", "CUMPLIDA", "INCUMPLIDA"}


def _evaluar_con_el_nucleo(as_of: date) -> dict:
    """Lo mismo, leido de la base fila por fila y evaluado con evaluacion.reglas."""
    from zoneinfo import ZoneInfo

    zona = ZoneInfo(ZONA)

    def local(instante: datetime) -> datetime:
        return instante.replace(tzinfo=zona)

    fin_as_of = local(datetime.combine(as_of + timedelta(1), time()))
    with sesion() as s:
        horizonte = s.execute(text("SELECT max(fecha_recepcion) FROM pago_observado")).scalar()
        promesas = s.execute(
            text(
                "SELECT p.promesa_id, p.monto_prometido, e.ocurrido_en, p.fecha_limite, "
                "c.cliente_unico, "
                "EXISTS (SELECT 1 FROM evento_lifecycle a WHERE a.evento_relacionado_id = "
                "g.evento_lifecycle_id AND a.tipo_evento = 'GESTION_ANULADA'), "
                "(SELECT x.ocurrido_en FROM evento_lifecycle x WHERE x.evento_relacionado_id = "
                "p.evento_lifecycle_id AND x.tipo_evento = 'PROMESA_CANCELADA') "
                "FROM promesa_pago p JOIN evento_lifecycle e ON e.id = p.evento_lifecycle_id "
                "JOIN gestion_cobranza g ON g.id = p.gestion_cobranza_id "
                "JOIN cuenta_canonica c ON c.id = p.cuenta_canonica_id"
            )
        ).all()
        movimientos = s.execute(
            text(
                "SELECT m.cliente_unico, m.movimiento_id, m.tipo_movimiento, m.fecha_recepcion, "
                "m.monto_reportado, r.fecha_recepcion FROM movimiento_economico_canonico m "
                "LEFT JOIN movimiento_economico_canonico r ON r.movimiento_id = "
                "m.anulado_por_movimiento_id"
            )
        ).all()
    esperado = {}
    corte = Corte(fin_as_of=fin_as_of, horizonte=None if horizonte is None else local(horizonte))
    for promesa_id, monto, creada, limite, cliente, anulada, cancelada in promesas:
        if creada >= fin_as_of:
            continue
        evaluable = PromesaEvaluable(
            promesa_id=promesa_id,
            monto_prometido=monto,
            creada_en=creada,
            fin_limite=local(datetime.combine(limite + timedelta(1), time())),
            anulada=anulada,
            cancelada_en=cancelada,
        )
        suyos = [
            Movimiento(
                movimiento_id=m_id,
                tipo=tipo,
                instante=local(recepcion),
                monto=importe,
                anulado_en=None if reverso is None else local(reverso),
            )
            for cliente_m, m_id, tipo, recepcion, importe, reverso in movimientos
            if cliente_m == cliente and tipo in ("PAGO", "POSIBLE_REVERSO")
        ]
        e = evaluar_puro(evaluable, suyos, corte)
        esperado[promesa_id] = (
            e.estado.value,
            e.monto_observado,
            e.movimientos_compatibles,
            [m["codigo"] for m in e.motivos],
        )
    return esperado


# --- todo o nada ----------------------------------------------------------------------------------


class Falla(Exception):
    pass


def _lanzar(*_args, **_kwargs):
    raise Falla("inyectada")


@pytest.mark.parametrize("donde", ["_firma_de_entrada", "_cerrar"])
def test_una_falla_no_publica_nada_y_otra_ejecucion_la_termina(tmp_path, monkeypatch, donde):
    _escenario(tmp_path)
    ejecucion_id = _abrir(date(2026, 9, 16))
    monkeypatch.setattr(motor, donde, _lanzar)

    evaluar(ejecucion_id)

    with sesion() as s:
        fallida = s.get_one(EjecucionEvaluacionPromesas, ejecucion_id)
        for tabla in ("ev_promesa", "ev_movimiento", "ev_corte"):
            assert s.execute(text(f"SELECT to_regclass('pg_temp.{tabla}')")).scalar() is None
    assert (fallida.estado, fallida.resultado) == ("FALLIDA", "ERROR_INTERNO")
    assert fallida.promesas_evaluadas == 0 and _cuantos(EvaluacionPromesa) == 0
    monkeypatch.undo()
    assert _evaluar(date(2026, 9, 16)).promesas_evaluadas == 6


def test_una_fecha_de_corte_se_evalua_a_lo_mas_una_vez_a_la_vez(tmp_path):
    _escenario(tmp_path)
    _abrir(date(2026, 9, 16))

    with sesion() as s, pytest.raises(EvaluacionEnProceso):
        abrir(s, DESPACHO, CARTERA, date(2026, 9, 16), zona=ZONA, max_intentos=5, reusar=False)
    with sesion() as s:
        reusada, nueva = abrir(
            s, DESPACHO, CARTERA, date(2026, 9, 16), zona=ZONA, max_intentos=5, reusar=True
        )
    assert not nueva
    assert _cuantos(TrabajoOrquestacion) >= 1


# --- su trabajo durable ---------------------------------------------------------------------------


def test_el_worker_evalua_despues_del_motor_de_pagos_y_cierra_su_trabajo(tmp_path, trabajar):
    from motor_cartera.orquestacion import worker

    ingerir_corte(tmp_path, date(2026, 9, 1), [Cuenta(n) for n in range(1, 4)])
    trabajar()  # la historia del corte
    gestion = gestion_de(cuenta(1).cuenta_id, "2026-09-05T10:00:00-06:00")
    promesa_de(gestion.recurso_id)
    ejecucion_id = _abrir(date(2026, 9, 16))
    # Pagos que llegan despues: su historia y su interpretacion van antes que la evaluacion.
    ingerir_pagos_de(tmp_path, "pagos.csv", [pago(1, "2026-09-10 10:00:00", "1000.00")])

    procesados = worker.procesar_un_trabajo(worker.identificador_worker(), Config())
    resto = trabajar()

    tipos = [procesados.tipo, *(p.tipo for p in resto)]
    assert tipos.index(TipoTrabajo.MOTOR_PAGOS) < tipos.index(TipoTrabajo.EVALUACION_PROMESAS)
    with sesion() as s:
        ejecucion = s.get_one(EjecucionEvaluacionPromesas, ejecucion_id)
        trabajo = s.exec(
            select(TrabajoOrquestacion).where(
                TrabajoOrquestacion.ejecucion_evaluacion_promesas_id == ejecucion_id
            )
        ).one()
    assert (ejecucion.estado, ejecucion.cumplidas) == ("EXITOSA", 1)
    assert (trabajo.tipo, trabajo.estado) == ("EVALUACION_PROMESAS", "COMPLETADO")


def test_el_backfill_encola_una_evaluacion_por_cartera_y_no_la_repite(tmp_path):
    from motor_cartera.evaluacion.backfill import diagnosticar, encolar, pendientes

    _escenario(tmp_path)
    as_of = date(2026, 9, 16)
    with sesion() as s:
        (cartera,) = diagnosticar(s, as_of, zona=ZONA)
    assert (cartera.promesas, cartera.vencidas, cartera.vigente, cartera.en_cola) == (
        6,
        6,
        None,
        None,
    )

    (encolada,) = encolar(pendientes([cartera], reevaluar=False), as_of, config=Config())
    with sesion() as s:
        (en_cola,) = diagnosticar(s, as_of, zona=ZONA)
    assert encolada.nueva and en_cola.en_cola == encolada.evaluacion_run_id
    assert pendientes([en_cola], reevaluar=True) == []
    with sesion() as s:
        ejecucion_id = s.exec(
            select(EjecucionEvaluacionPromesas.id).where(
                EjecucionEvaluacionPromesas.evaluacion_run_id == encolada.evaluacion_run_id
            )
        ).one()
    evaluar(ejecucion_id)
    with sesion() as s:
        (al_dia,) = diagnosticar(s, as_of, zona=ZONA)
    assert al_dia.vigente == encolada.evaluacion_run_id
    assert pendientes([al_dia], reevaluar=False) == []
    assert pendientes([al_dia], reevaluar=True) == [al_dia]


# --- por la API -----------------------------------------------------------------------------------


def test_la_api_pide_una_evaluacion_y_la_muestra_con_cada_promesa(tmp_path, cliente, trabajar):
    promesas = _escenario(tmp_path)

    pedida = cliente.post("/evaluaciones-promesas", json={"as_of": "2026-09-16"})
    otra = cliente.post("/evaluaciones-promesas", json={"as_of": "2026-09-16"})
    futura = cliente.post("/evaluaciones-promesas", json={"as_of": "2099-01-01"})
    sin_fecha = cliente.post("/evaluaciones-promesas", json={})

    assert pedida.status_code == 201, pedida.text
    cuerpo = pedida.json()
    assert cuerpo["estado"] == "EN_PROCESO" and cuerpo["as_of"] == "2026-09-16"
    assert pedida.headers["location"] == f"/evaluaciones-promesas/{cuerpo['evaluacion_run_id']}"
    assert (otra.status_code, otra.json()["codigo"]) == (409, "EVALUACION_EN_PROCESO")
    assert (futura.status_code, futura.json()["codigo"]) == (422, "AS_OF_FUTURO")
    assert sin_fecha.status_code == 422

    trabajar()
    terminada = cliente.get(pedida.headers["location"]).json()
    parciales = cliente.get(f"{pedida.headers['location']}/promesas?estado=PARCIAL").json()
    lista = cliente.get("/evaluaciones-promesas?as_of=2026-09-16").json()
    promesa = cliente.get(f"/promesas/{promesas[1]}").json()

    assert (terminada["estado"], terminada["conteos"]["cumplidas"]) == ("EXITOSA", 1)
    assert terminada["duracion_segundos"] is not None and terminada["trabajo_id"]
    assert "no afirma que la promesa" in terminada["aviso"].lower()
    assert [p["promesa_id"] for p in parciales["elementos"]] == [str(promesas[2])]
    assert lista["total"] == 1
    evaluacion = promesa["ultima_evaluacion"]
    assert (evaluacion["estado"], evaluacion["as_of"], evaluacion["monto_observado"]) == (
        "CUMPLIDA",
        "2026-09-16",
        "1000.00",
    )
    assert evaluacion["motivos"][0]["codigo"] == "MONTO_ALCANZADO"
    assert cliente.get(f"/evaluaciones-promesas/{llave()[:8]}").status_code == 422
