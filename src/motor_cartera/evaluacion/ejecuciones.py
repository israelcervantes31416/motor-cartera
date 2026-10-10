"""La evaluacion de las promesas de una cartera a una fecha de corte, en PostgreSQL y todo o nada.

Una ejecucion evalua, en un solo trabajo EVALUACION_PROMESAS, cada promesa de una cartera acordada
hasta el final de su `as_of`: nunca un trabajo por promesa. El orden:

1. Se abre la ejecucion EN_PROCESO, con su trabajo, en la misma transaccion (`abrir`): la piden
   POST /evaluaciones-promesas o `motor-cartera backfill-lifecycle`, con su as_of explicito.
2. Un worker toma el trabajo, y `evaluar` toma la ejecucion con su fila bloqueada.
3. Tablas temporales, por conjuntos: las promesas con su estado operativo a la fecha de corte, los
   movimientos de la interpretacion vigente de los pagos en el intervalo de cada una (por el indice
   de los movimientos de una cuenta), los meses con pagos observados que su interpretacion vigente
   no ve, y el horizonte de los pagos de la cartera. Ninguna promesa pasa por Python.
4. Un INSERT ... SELECT publica una evaluacion por promesa, con las reglas de
   `evaluacion.reglas` (una prueba compara las dos); se cuenta y se cierra EXITOSA en la misma
   transaccion. Si algo falla, se revierte todo y queda FALLIDA.

La fecha de corte nunca sale del reloj. La misma cartera, el mismo as_of, la misma version y la
misma firma de entrada no se publican dos veces: lo garantiza un indice unico.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session, select

from motor_cartera.db.modelos import (
    EjecucionEvaluacionPromesas,
    EstadoEvaluacionPromesas,
    ResultadoEvaluacionPromesas,
    TipoTrabajo,
    ahora,
)
from motor_cartera.db.sesion import insertar_en_savepoint, restriccion, sesion
from motor_cartera.evaluacion.reglas import VERSION_EVALUACION, EstadoEvaluacion, Motivo
from motor_cartera.ingesta.fuente_oficial import Cronometro
from motor_cartera.motor_pagos.reglas import VERSION_MOTOR_PAGOS
from motor_cartera.orquestacion import cola

log = logging.getLogger(__name__)

INDICE_EXITOSA = "ux_evaluacion_promesas_exitosa"
INDICE_EN_PROCESO = "ux_evaluacion_promesas_en_proceso"


class EvaluacionYaPublicada(Exception):
    """Otra ejecucion de la misma cartera y fecha de corte publico primero las mismas entradas."""

    def __init__(self, previa: EjecucionEvaluacionPromesas) -> None:
        super().__init__(
            f"Las promesas al {previa.as_of.isoformat()} ya se evaluaron con estas mismas entradas "
            f"y {previa.version_evaluacion}: la ejecucion {previa.evaluacion_run_id}."
        )
        self.previa = previa


class EvaluacionEnProceso(Exception):
    """La cartera ya tiene una evaluacion EN_PROCESO a esa fecha de corte."""

    def __init__(self, activa: EjecucionEvaluacionPromesas) -> None:
        super().__init__(
            f"Las promesas al {activa.as_of.isoformat()} ya se estan evaluando: la ejecucion "
            f"{activa.evaluacion_run_id}."
        )
        self.activa = activa


class _NoSePublica(Exception):
    def __init__(self, resultado: ResultadoEvaluacionPromesas, mensaje: str) -> None:
        super().__init__(mensaje)
        self.resultado = resultado


# --- abrir ----------------------------------------------------------------------------------------


def abrir(
    s: Session,
    despacho_id: str,
    cartera_id: str,
    as_of: date,
    *,
    zona: str,
    max_intentos: int,
    reusar: bool,
) -> tuple[EjecucionEvaluacionPromesas, bool]:
    """La ejecucion EN_PROCESO de la cartera a esa fecha de corte, con su trabajo, en la
    transaccion de quien llama: envia los INSERT y no confirma. Si ya hay una EN_PROCESO, la reusa
    (`reusar`, el backfill) o levanta EvaluacionEnProceso (la API). Devuelve la ejecucion y si es
    nueva."""
    activa = _activa(s, despacho_id, cartera_id, as_of)
    if activa is not None:
        if reusar:
            return activa, False
        raise EvaluacionEnProceso(activa)
    ejecucion = EjecucionEvaluacionPromesas(
        version_evaluacion=VERSION_EVALUACION,
        despacho_id=despacho_id,
        cartera_id=cartera_id,
        as_of=as_of,
        zona_horaria=zona,
    )
    error = insertar_en_savepoint(s, ejecucion)
    if error is not None:
        if restriccion(error) != INDICE_EN_PROCESO:
            raise error
        activa = _activa(s, despacho_id, cartera_id, as_of)
        if reusar and activa is not None:
            return activa, False
        raise EvaluacionEnProceso(activa) from error
    cola.crear(s, TipoTrabajo.EVALUACION_PROMESAS, ejecucion.id, max_intentos=max_intentos)
    log.info(
        "evaluacion de promesas %s abierta para %s/%s al %s",
        ejecucion.evaluacion_run_id,
        despacho_id,
        cartera_id,
        as_of.isoformat(),
    )
    return ejecucion, True


def _activa(
    s: Session, despacho_id: str, cartera_id: str, as_of: date
) -> EjecucionEvaluacionPromesas | None:
    return s.exec(
        select(EjecucionEvaluacionPromesas)
        .where(
            EjecucionEvaluacionPromesas.despacho_id == despacho_id,
            EjecucionEvaluacionPromesas.cartera_id == cartera_id,
            EjecucionEvaluacionPromesas.version_evaluacion == VERSION_EVALUACION,
            EjecucionEvaluacionPromesas.as_of == as_of,
            EjecucionEvaluacionPromesas.estado == EstadoEvaluacionPromesas.EN_PROCESO,
        )
        .with_for_update()
    ).first()


# --- evaluar --------------------------------------------------------------------------------------


@dataclass
class _Avance:
    firma: str | None = None


def ejecutar_evaluacion(ejecucion_id: int) -> None:
    """Lo que ejecuta un trabajo EVALUACION_PROMESAS. Si la ejecucion ya termino no hace nada."""
    with sesion() as s:
        ejecucion = s.get_one(EjecucionEvaluacionPromesas, ejecucion_id)
        if ejecucion.estado != EstadoEvaluacionPromesas.EN_PROCESO:
            log.info("evaluacion %s ya termino %s", ejecucion.evaluacion_run_id, ejecucion.estado)
            return
    evaluar(ejecucion_id)


def evaluar(ejecucion_id: int, *, cronometro: Cronometro | None = None) -> None:
    """Evalua las promesas de la ejecucion y la deja en un estado terminal, como el motor de pagos:
    con su fila bloqueada hasta el commit, todo en una transaccion y en un savepoint; si falla,
    queda FALLIDA sin soltar su fila, y solo publica o falla el dueno vigente de su trabajo. Levanta
    EvaluacionYaPublicada si otra ejecucion publico primero las mismas entradas, TrabajoAjeno, y los
    errores transitorios de la base, con la ejecucion EN_PROCESO para que la cola la reintente."""
    cronometro = cronometro or Cronometro()
    with sesion() as s:
        ejecucion = s.exec(
            select(EjecucionEvaluacionPromesas)
            .where(EjecucionEvaluacionPromesas.id == ejecucion_id)
            .with_for_update()
        ).one()
        if ejecucion.estado != EstadoEvaluacionPromesas.EN_PROCESO:
            log.warning(
                "evaluacion %s ya termino %s", ejecucion.evaluacion_run_id, ejecucion.estado
            )
            return
        etiqueta, as_of = ejecucion.evaluacion_run_id, ejecucion.as_of
        avance = _Avance()
        try:
            with s.begin_nested():
                _evaluar(s, ejecucion, avance, cronometro)
        except _NoSePublica as exc:
            resultado, motivo = exc.resultado, str(exc)
        except IntegrityError as exc:
            if restriccion(exc) == INDICE_EXITOSA:
                resultado, motivo = (
                    ResultadoEvaluacionPromesas.YA_EVALUADA,
                    f"Otra ejecucion evaluo las promesas al {as_of.isoformat()} con las mismas "
                    "entradas mientras esta se procesaba; no se publica dos veces.",
                )
            else:
                log.exception("evaluacion %s: violacion de integridad inesperada", etiqueta)
                resultado, motivo = (
                    ResultadoEvaluacionPromesas.ERROR_INTERNO,
                    "Error interno al publicar; ver la bitacora del servicio.",
                )
        except OperationalError:
            log.warning("evaluacion %s: error transitorio; sigue EN_PROCESO", etiqueta)
            raise
        except Exception as exc:
            log.exception("evaluacion %s: error inesperado", etiqueta)
            resultado, motivo = (
                ResultadoEvaluacionPromesas.ERROR_INTERNO,
                f"Error interno ({type(exc).__name__}); ver la bitacora.",
            )
        else:
            cola.confirmar_dueno(s)
            with cronometro.fase("commit"):
                s.commit()
            return
        _fallar(s, ejecucion_id, etiqueta, resultado, motivo, avance)
        if resultado == ResultadoEvaluacionPromesas.YA_EVALUADA:
            raise EvaluacionYaPublicada(_exitosa_con_la_firma(s, ejecucion_id))


def _evaluar(
    s: Session,
    ejecucion: EjecucionEvaluacionPromesas,
    avance: _Avance,
    cronometro: Cronometro,
) -> None:
    if ejecucion.version_evaluacion != VERSION_EVALUACION:
        raise _NoSePublica(
            ResultadoEvaluacionPromesas.VERSION_NO_SOPORTADA,
            f"La ejecucion pide {ejecucion.version_evaluacion} y este servicio solo evalua con "
            f"{VERSION_EVALUACION}; no se publico nada.",
        )
    parametros = {
        "ejecucion": ejecucion.id,
        "despacho": ejecucion.despacho_id,
        "cartera": ejecucion.cartera_id,
        "zona": ejecucion.zona_horaria,
        "as_of": ejecucion.as_of,
        "version_motor": VERSION_MOTOR_PAGOS,
    }
    with cronometro.fase("staging"):
        for sentencia in SQL_LEER:
            s.execute(text(sentencia), parametros)
    with cronometro.fase("firma_de_entrada"):
        firma = avance.firma = _firma_de_entrada(s, ejecucion)
    previa = _ya_evaluada(s, ejecucion, firma)
    if previa is not None:
        raise _NoSePublica(
            ResultadoEvaluacionPromesas.YA_EVALUADA,
            f"La ejecucion {previa.evaluacion_run_id} ya evaluo exactamente estas entradas (la "
            f"misma firma de entrada) con {VERSION_EVALUACION}; no se publica dos veces.",
        )
    with cronometro.fase("evaluaciones"):
        s.execute(text(SQL_EVALUACIONES), parametros)
    with cronometro.fase("conteos"):
        _cerrar(s, ejecucion, firma)


# --- lo que se lee --------------------------------------------------------------------------------

SQL_LEER = (
    # El final del dia as_of, como instante, y el horizonte de los pagos de la cartera.
    "CREATE TEMP TABLE ev_corte ON COMMIT DROP AS SELECT "
    "timezone(:zona, (CAST(:as_of AS date) + 1)::timestamp) AS fin_as_of, "
    "(SELECT timezone(:zona, max(fecha_recepcion)) FROM pago_observado "
    "WHERE despacho_id = :despacho AND cartera_id = :cartera) AS horizonte",
    # Las promesas de la cartera acordadas hasta el final de as_of, con su estado operativo.
    "CREATE TEMP TABLE ev_promesa ON COMMIT DROP AS "
    "SELECT p.id, p.promesa_id, p.cuenta_canonica_id AS cuenta, c.cliente_unico, "
    "p.monto_prometido AS monto, e.ocurrido_en AS creada_en, "
    "timezone(:zona, (p.fecha_limite + 1)::timestamp) AS fin_limite, "
    "least(timezone(:zona, (p.fecha_limite + 1)::timestamp), k.fin_as_of) AS fin, "
    "EXISTS (SELECT 1 FROM evento_lifecycle a WHERE a.evento_relacionado_id = "
    "g.evento_lifecycle_id AND a.tipo_evento = 'GESTION_ANULADA') AS anulada, "
    "(SELECT x.ocurrido_en FROM evento_lifecycle x WHERE x.evento_relacionado_id = "
    "p.evento_lifecycle_id AND x.tipo_evento = 'PROMESA_CANCELADA') AS cancelada_en "
    "FROM promesa_pago p "
    "JOIN evento_lifecycle e ON e.id = p.evento_lifecycle_id "
    "JOIN gestion_cobranza g ON g.id = p.gestion_cobranza_id "
    "JOIN cuenta_canonica c ON c.id = p.cuenta_canonica_id "
    "CROSS JOIN ev_corte k "
    "WHERE e.despacho_id = :despacho AND e.cartera_id = :cartera AND e.ocurrido_en < k.fin_as_of",
    "ANALYZE ev_promesa",
    # Los movimientos de la interpretacion vigente en el intervalo de cada promesa, por el indice de
    # los movimientos de una cuenta, conciliados con su cuenta; con el reverso que anulo un pago.
    "CREATE TEMP TABLE ev_movimiento ON COMMIT DROP AS "
    "WITH vigentes AS (SELECT max(id) AS id FROM ejecucion_motor_pagos "
    "WHERE despacho_id = :despacho AND cartera_id = :cartera AND version_motor = :version_motor "
    "AND estado = 'EXITOSA' GROUP BY periodo_desde) "
    "SELECT pr.id AS promesa, m.movimiento_id, m.tipo_movimiento AS tipo, m.fecha_recepcion, "
    "timezone(:zona, m.fecha_recepcion) AS instante, m.monto_reportado AS monto, "
    "timezone(:zona, rv.fecha_recepcion) AS anulado_en "
    "FROM ev_promesa pr "
    "JOIN movimiento_economico_canonico m ON m.despacho_id = :despacho "
    "AND m.cartera_id = :cartera AND m.cliente_unico = pr.cliente_unico "
    "AND m.fecha_recepcion >= (pr.creada_en AT TIME ZONE :zona) "
    "AND m.fecha_recepcion < (pr.fin AT TIME ZONE :zona) "
    "AND m.ejecucion_motor_pagos_id IN (SELECT id FROM vigentes) "
    "AND m.cuenta_canonica_id = pr.cuenta AND m.tipo_movimiento IN ('PAGO', 'POSIBLE_REVERSO') "
    "LEFT JOIN movimiento_economico_canonico rv ON m.anulado_por_movimiento_id IS NOT NULL "
    "AND rv.movimiento_id = m.anulado_por_movimiento_id "
    "AND rv.ejecucion_motor_pagos_id IN (SELECT id FROM vigentes)",
    # Los meses con pagos observados que su interpretacion vigente no ve, o que no tienen ninguna.
    "CREATE TEMP TABLE ev_mes_pendiente ON COMMIT DROP AS "
    "WITH observados AS (SELECT date_trunc('month', fecha_recepcion)::date AS mes, count(*) AS n "
    "FROM pago_observado WHERE despacho_id = :despacho AND cartera_id = :cartera GROUP BY 1), "
    "interpretados AS (SELECT DISTINCT ON (periodo_desde) periodo_desde AS mes, "
    "observaciones_leidas AS n FROM ejecucion_motor_pagos WHERE despacho_id = :despacho "
    "AND cartera_id = :cartera AND version_motor = :version_motor AND estado = 'EXITOSA' "
    "ORDER BY periodo_desde, id DESC) "
    "SELECT o.mes FROM observados o LEFT JOIN interpretados i ON i.mes = o.mes "
    "WHERE i.mes IS NULL OR i.n < o.n",
    # Que promesas tienen en su intervalo un mes de esos.
    "CREATE TEMP TABLE ev_sin_interpretar ON COMMIT DROP AS "
    "SELECT DISTINCT pr.id AS promesa FROM ev_promesa pr JOIN ev_mes_pendiente x "
    "ON x.mes::timestamp < (pr.fin AT TIME ZONE :zona) "
    "AND (x.mes + interval '1 month') > (pr.creada_en AT TIME ZONE :zona)",
)
"""Lo que se evalua, en tablas temporales que se borran al terminar la transaccion."""


def _firma_de_entrada(s: Session, ejecucion: EjecucionEvaluacionPromesas) -> str:
    """El SHA-256 de lo que se leyo, en forma canonica: cada promesa con su estado operativo, cada
    movimiento de su intervalo con su anulacion, las promesas con pagos sin interpretar y el
    horizonte de los pagos, con la version, la cartera, la fecha de corte y la zona delante. Los
    instantes, en UTC: la sesion de la base no tiene otra zona."""
    cabecera = (
        f"{VERSION_EVALUACION}\n{ejecucion.despacho_id}\n{ejecucion.cartera_id}\n"
        f"{ejecucion.as_of.isoformat()}\n{ejecucion.zona_horaria}\n"
    )
    return s.execute(
        text(
            "SELECT encode(sha256(convert_to(:cabecera || coalesce(string_agg(atomo, chr(10) "
            "ORDER BY atomo), ''), 'UTF8')), 'hex') FROM ("
            "SELECT 'p:' || promesa_id || ':' || monto || ':' || creada_en || ':' || fin_limite "
            "|| ':' || anulada || ':' || coalesce(cancelada_en::text, '') AS atomo "
            "FROM ev_promesa "
            "UNION ALL SELECT 'm:' || pr.promesa_id || ':' || m.movimiento_id || ':' || m.tipo "
            "|| ':' || m.monto || ':' || m.instante || ':' || coalesce(m.anulado_en::text, '') "
            "FROM ev_movimiento m JOIN ev_promesa pr ON pr.id = m.promesa "
            "UNION ALL SELECT 'x:' || pr.promesa_id FROM ev_sin_interpretar x "
            "JOIN ev_promesa pr ON pr.id = x.promesa "
            "UNION ALL SELECT 'h:' || coalesce(horizonte::text, '') FROM ev_corte) atomos"
        ),
        {"cabecera": cabecera},
    ).scalar_one()


# --- lo que se publica ----------------------------------------------------------------------------

E = EstadoEvaluacion
SQL_EVALUACIONES = f"""
INSERT INTO evaluacion_promesa (ejecucion_evaluacion_promesas_id, promesa_pago_id,
    primer_movimiento_en, ultimo_movimiento_en, movimientos_compatibles, monto_observado, estado,
    motivos)
SELECT :ejecucion, pr.id, a.primero, a.ultimo, coalesce(a.compatibles, 0),
    coalesce(a.observado, 0), v.estado,
    v.motivo || CASE WHEN coalesce(a.posibles, 0) > 0 AND v.estado <> '{E.CANCELADA}'
        THEN jsonb_build_array(jsonb_build_object('codigo', '{Motivo.POSIBLES_REVERSOS}',
            'cuantos', a.posibles, 'monto', a.monto_posibles::numeric(14, 2)::text))
        ELSE '[]'::jsonb END
FROM ev_promesa pr
CROSS JOIN ev_corte k
LEFT JOIN (
    SELECT promesa,
        count(*) FILTER (WHERE compatible) AS compatibles,
        sum(monto) FILTER (WHERE compatible) AS observado,
        min(fecha_recepcion) FILTER (WHERE compatible) AS primero,
        max(fecha_recepcion) FILTER (WHERE compatible) AS ultimo,
        count(*) FILTER (WHERE tipo = 'POSIBLE_REVERSO') AS posibles,
        sum(monto) FILTER (WHERE tipo = 'POSIBLE_REVERSO') AS monto_posibles
    FROM (
        SELECT m.*, m.tipo = 'PAGO' AND (m.anulado_en IS NULL OR m.anulado_en >= k.fin_as_of)
            AS compatible
        FROM ev_movimiento m CROSS JOIN ev_corte k
    ) x GROUP BY promesa
) a ON a.promesa = pr.id
LEFT JOIN ev_sin_interpretar si ON si.promesa = pr.id
CROSS JOIN LATERAL (
    SELECT coalesce(a.observado, 0)::numeric(14, 2)::text AS observado,
        pr.monto::numeric(14, 2)::text AS prometido
) t
CROSS JOIN LATERAL (
    SELECT CASE
        WHEN pr.anulada THEN '{E.CANCELADA}'
        WHEN pr.cancelada_en IS NOT NULL AND pr.cancelada_en < k.fin_as_of THEN '{E.CANCELADA}'
        WHEN coalesce(a.observado, 0) >= pr.monto THEN '{E.CUMPLIDA}'
        WHEN k.fin_as_of < pr.fin_limite THEN '{E.PENDIENTE}'
        WHEN si.promesa IS NOT NULL THEN '{E.NO_EVALUABLE}'
        WHEN k.horizonte IS NULL OR k.horizonte < pr.fin THEN '{E.NO_EVALUABLE}'
        WHEN coalesce(a.observado, 0) > 0 THEN '{E.PARCIAL}'
        ELSE '{E.INCUMPLIDA}' END AS estado,
    jsonb_build_array(CASE
        WHEN pr.anulada THEN jsonb_build_object('codigo', '{Motivo.GESTION_ANULADA}')
        WHEN pr.cancelada_en IS NOT NULL AND pr.cancelada_en < k.fin_as_of
            THEN jsonb_build_object('codigo', '{Motivo.PROMESA_CANCELADA}',
                'cancelada_en', pr.cancelada_en)
        WHEN coalesce(a.observado, 0) >= pr.monto
            THEN jsonb_build_object('codigo', '{Motivo.MONTO_ALCANZADO}',
                'observado', t.observado, 'prometido', t.prometido)
        WHEN k.fin_as_of < pr.fin_limite
            THEN jsonb_build_object('codigo', '{Motivo.ANTES_DE_LA_FECHA_LIMITE}',
                'observado', t.observado, 'prometido', t.prometido)
        WHEN si.promesa IS NOT NULL
            THEN jsonb_build_object('codigo', '{Motivo.PAGOS_SIN_INTERPRETAR}')
        WHEN k.horizonte IS NULL OR k.horizonte < pr.fin
            THEN jsonb_build_object('codigo', '{Motivo.DATOS_DE_PAGOS_INSUFICIENTES}',
                'horizonte', k.horizonte)
        WHEN coalesce(a.observado, 0) > 0
            THEN jsonb_build_object('codigo', '{Motivo.MONTO_PARCIAL}',
                'observado', t.observado, 'prometido', t.prometido)
        ELSE jsonb_build_object('codigo', '{Motivo.SIN_RECUPERACION}') END) AS motivo
) v
ORDER BY pr.id
"""
"""Una evaluacion por promesa, con las reglas de `evaluacion.reglas.evaluar`, en el mismo orden."""


def _cerrar(s: Session, ejecucion: EjecucionEvaluacionPromesas, firma: str) -> None:
    conteos = s.execute(
        text(
            "SELECT count(*), "
            + ", ".join(f"count(*) FILTER (WHERE r.estado = '{e}')" for e in EstadoEvaluacion)
            + ", coalesce(sum(p.monto_prometido), 0), coalesce(sum(r.monto_observado), 0) "
            "FROM evaluacion_promesa r JOIN promesa_pago p ON p.id = r.promesa_pago_id "
            "WHERE r.ejecucion_evaluacion_promesas_id = :ejecucion"
        ),
        {"ejecucion": ejecucion.id},
    ).one()
    total, *por_estado, prometido, observado = conteos
    leidas = s.execute(text("SELECT count(*) FROM ev_promesa")).scalar_one()
    if total != leidas:
        raise _NoSePublica(
            ResultadoEvaluacionPromesas.DATOS_INCONSISTENTES,
            f"Se leyeron {leidas:,} promesas y se publicaron {total:,} evaluaciones; no se publico "
            "nada.",
        )
    cuantas = dict(zip(EstadoEvaluacion, por_estado, strict=True))
    horizonte = s.execute(
        text("SELECT horizonte AT TIME ZONE :zona FROM ev_corte"), {"zona": ejecucion.zona_horaria}
    ).scalar_one()
    ejecucion.estado = EstadoEvaluacionPromesas.EXITOSA
    ejecucion.resultado = ResultadoEvaluacionPromesas.EVALUACION_PUBLICADA.value
    ejecucion.firma_entrada = firma
    ejecucion.horizonte_pagos = horizonte
    ejecucion.promesas_evaluadas = total
    ejecucion.pendientes = cuantas[EstadoEvaluacion.PENDIENTE]
    ejecucion.cumplidas = cuantas[EstadoEvaluacion.CUMPLIDA]
    ejecucion.parciales = cuantas[EstadoEvaluacion.PARCIAL]
    ejecucion.incumplidas = cuantas[EstadoEvaluacion.INCUMPLIDA]
    ejecucion.canceladas = cuantas[EstadoEvaluacion.CANCELADA]
    ejecucion.no_evaluables = cuantas[EstadoEvaluacion.NO_EVALUABLE]
    ejecucion.monto_prometido = Decimal(prometido)
    ejecucion.monto_observado = Decimal(observado)
    ejecucion.detalle = (
        f"Se evaluaron {total:,} promesas al {ejecucion.as_of.isoformat()} (fin del dia en "
        f"{ejecucion.zona_horaria}): {ejecucion.cumplidas:,} cumplidas, {ejecucion.parciales:,} "
        f"parciales, {ejecucion.incumplidas:,} incumplidas, {ejecucion.pendientes:,} pendientes, "
        f"{ejecucion.canceladas:,} canceladas y {ejecucion.no_evaluables:,} no evaluables. "
        "Recuperacion compatible observada, no causalidad. "
        f"{VERSION_EVALUACION}."
    )
    ejecucion.terminada_en = ahora()
    s.add(ejecucion)
    s.flush()


def _fallar(
    s: Session,
    ejecucion_id: int,
    etiqueta: UUID,
    resultado: ResultadoEvaluacionPromesas,
    motivo: str,
    avance: _Avance,
) -> None:
    """FALLIDA, con su fila todavia bloqueada, solo si quien la ejecuta sigue siendo el dueno de su
    trabajo; si no, TrabajoAjeno sin cambiar nada."""
    try:
        cola.confirmar_dueno(s)
    except cola.TrabajoAjeno:
        s.rollback()
        log.warning("evaluacion %s: su trabajo ya no es de este worker: %s", etiqueta, motivo)
        raise
    registrado = s.execute(
        update(EjecucionEvaluacionPromesas)
        .where(
            EjecucionEvaluacionPromesas.id == ejecucion_id,
            EjecucionEvaluacionPromesas.estado == EstadoEvaluacionPromesas.EN_PROCESO,
        )
        .values(
            estado=EstadoEvaluacionPromesas.FALLIDA,
            resultado=resultado.value,
            firma_entrada=avance.firma,
            detalle=motivo,
            terminada_en=ahora(),
        )
        .execution_options(synchronize_session=False)
    ).rowcount
    s.commit()
    if registrado:
        log.warning("evaluacion %s fallida (%s): %s", etiqueta, resultado.value, motivo)


def _ya_evaluada(
    s: Session, ejecucion: EjecucionEvaluacionPromesas, firma: str
) -> EjecucionEvaluacionPromesas | None:
    return s.exec(
        _de_la_fecha(ejecucion).where(EjecucionEvaluacionPromesas.firma_entrada == firma)
    ).first()


def _exitosa_con_la_firma(s: Session, ejecucion_id: int) -> EjecucionEvaluacionPromesas:
    perdedora = s.get_one(EjecucionEvaluacionPromesas, ejecucion_id)
    return s.exec(
        _de_la_fecha(perdedora).where(
            EjecucionEvaluacionPromesas.firma_entrada == perdedora.firma_entrada
        )
    ).one()


def _de_la_fecha(ejecucion: EjecucionEvaluacionPromesas):
    return select(EjecucionEvaluacionPromesas).where(
        EjecucionEvaluacionPromesas.despacho_id == ejecucion.despacho_id,
        EjecucionEvaluacionPromesas.cartera_id == ejecucion.cartera_id,
        EjecucionEvaluacionPromesas.version_evaluacion == VERSION_EVALUACION,
        EjecucionEvaluacionPromesas.as_of == ejecucion.as_of,
        EjecucionEvaluacionPromesas.estado == EstadoEvaluacionPromesas.EXITOSA,
    )
