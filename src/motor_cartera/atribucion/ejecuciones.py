"""La atribucion de una ventana de movimientos economicos, en PostgreSQL y todo o nada.

Una ventana es un despacho, una cartera y un mes de recepcion, como en el motor de pagos. El orden:

1. Se abre la ejecucion EN_PROCESO con su trabajo ATRIBUCION, en una transaccion (`abrir`). No se
abre sola: la piden POST /atribuciones o `motor-cartera backfill-atribucion`, con la ventana hacia
atras que se quiera (`ventana_dias`, la configurada por omision) y la zona de la fuente. 2. Un
worker toma el trabajo, y `atribuir` toma la ejecucion con su fila bloqueada. 3. Lee, en tablas
temporales y por conjuntos, los PAGO de la interpretacion vigente del motor de pagos de la ventana y
las gestiones de sus cuentas que pudieron antecederlos (por el indice de las gestiones de una
cuenta). Las parejas (movimiento, gestion) salen de una sola union temporal: ninguna consulta por
movimiento, ningun movimiento por Python. 4. Publica, con INSERT ... SELECT, una clasificacion por
movimiento y una fila por candidata; cuenta, comprueba y cierra EXITOSA en la misma transaccion. Si
algo falla, se revierte todo y queda FALLIDA.

Como en los demas motores, lo que hace segura la entrega repetida es el bloqueo de la ejecucion, sus
estados terminales, la transaccion todo o nada, los indices unicos (una EXITOSA por ventana, version
y firma de entrada; una EN_PROCESO por ventana y version) y que solo publica el dueno vigente de su
trabajo. Una ejecucion nueva no toca ninguna anterior: con una gestion tardia, otra atribucion de la
misma ventana publica la interpretacion nueva y la anterior se conserva.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session, select

from motor_cartera.atribucion.reglas import VERSION_ATRIBUCION, Clasificacion, Motivo
from motor_cartera.db.modelos import (
    EjecucionAtribucion,
    EjecucionMotorPagos,
    EstadoAtribucion,
    EstadoMotorPagos,
    ResultadoAtribucion,
    TipoTrabajo,
    ahora,
)
from motor_cartera.db.sesion import insertar_en_savepoint, restriccion, sesion
from motor_cartera.ingesta.fuente_oficial import Cronometro
from motor_cartera.lifecycle.reglas import CONTACTOS
from motor_cartera.motor_pagos.ejecuciones import Ventana
from motor_cartera.motor_pagos.reglas import VERSION_MOTOR_PAGOS
from motor_cartera.orquestacion import cola

log = logging.getLogger(__name__)

INDICE_EXITOSA = "ux_ejecucion_atribucion_exitosa"
INDICE_EN_PROCESO = "ux_ejecucion_atribucion_en_proceso"


class AtribucionYaPublicada(Exception):
    """Otra ejecucion de la ventana publico primero exactamente las mismas entradas."""

    def __init__(self, previa: EjecucionAtribucion) -> None:
        super().__init__(
            f"La ventana {previa.periodo_desde.isoformat()} ya se atribuyo con estas mismas "
            f"entradas y {previa.version_atribucion}: la ejecucion {previa.atribucion_run_id}."
        )
        self.previa = previa


class AtribucionEnProceso(Exception):
    def __init__(self, activa: EjecucionAtribucion) -> None:
        super().__init__(
            f"La ventana {activa.periodo_desde.isoformat()} ya se esta atribuyendo: la ejecucion "
            f"{activa.atribucion_run_id}."
        )
        self.activa = activa


class _NoSePublica(Exception):
    def __init__(self, resultado: ResultadoAtribucion, mensaje: str) -> None:
        super().__init__(mensaje)
        self.resultado = resultado


# --- abrir ----------------------------------------------------------------------------------------


def interpretacion_vigente(s: Session, ventana: Ventana) -> EjecucionMotorPagos | None:
    """La interpretacion vigente del motor de pagos de la ventana: su EXITOSA mas reciente."""
    return s.exec(
        select(EjecucionMotorPagos)
        .where(
            EjecucionMotorPagos.despacho_id == ventana.despacho_id,
            EjecucionMotorPagos.cartera_id == ventana.cartera_id,
            EjecucionMotorPagos.version_motor == VERSION_MOTOR_PAGOS,
            EjecucionMotorPagos.periodo_desde == ventana.desde,
            EjecucionMotorPagos.estado == EstadoMotorPagos.EXITOSA,
        )
        .order_by(EjecucionMotorPagos.id.desc())
    ).first()


def abrir(
    s: Session,
    ventana: Ventana,
    *,
    ventana_dias: int,
    zona: str,
    max_intentos: int,
    reusar: bool,
) -> tuple[EjecucionAtribucion, bool]:
    """La ejecucion EN_PROCESO de la ventana con atribucion/v1 y su trabajo ATRIBUCION, en la
    transaccion de quien llama: envia los INSERT y no confirma. Si la ventana ya tiene una
    EN_PROCESO, la reusa (`reusar`, el backfill) o levanta AtribucionEnProceso (la API)."""
    activa = _activa(s, ventana)
    if activa is not None:
        if reusar:
            return activa, False
        raise AtribucionEnProceso(activa)
    ejecucion = EjecucionAtribucion(
        version_atribucion=VERSION_ATRIBUCION,
        despacho_id=ventana.despacho_id,
        cartera_id=ventana.cartera_id,
        periodo_desde=ventana.desde,
        periodo_hasta=ventana.hasta,
        ventana_dias=ventana_dias,
        zona_horaria=zona,
    )
    error = insertar_en_savepoint(s, ejecucion)
    if error is not None:
        if restriccion(error) != INDICE_EN_PROCESO:
            raise error
        activa = _activa(s, ventana)
        if reusar and activa is not None:
            return activa, False
        raise AtribucionEnProceso(activa) from error
    cola.crear(s, TipoTrabajo.ATRIBUCION, ejecucion.id, max_intentos=max_intentos)
    log.info(
        "atribucion %s abierta para %s/%s %s, ventana de %s dias",
        ejecucion.atribucion_run_id,
        ventana.despacho_id,
        ventana.cartera_id,
        ventana.desde.isoformat(),
        ventana_dias,
    )
    return ejecucion, True


def _activa(s: Session, ventana: Ventana) -> EjecucionAtribucion | None:
    return s.exec(_de_la_ventana(ventana, EstadoAtribucion.EN_PROCESO).with_for_update()).first()


# --- atribuir -------------------------------------------------------------------------------------


@dataclass
class _Avance:
    """Lo que alcanzo a leer: es lo que una FALLIDA dice que leyo."""

    gestiones: int = 0
    anuladas: int = 0
    firma: str | None = None
    motor: int | None = None


@dataclass(frozen=True)
class _Conteos:
    movimientos_evaluados: int
    asociados: int
    ambiguos: int
    sin_candidato: int
    candidatos: int
    movimientos_anulados: int
    monto_asociado: Decimal
    monto_ambiguo: Decimal
    monto_sin_candidato: Decimal
    monto_anulado: Decimal
    fuera_de_alcance: int = field(default=0)


def ejecutar_atribucion(ejecucion_id: int) -> None:
    """Lo que ejecuta un trabajo ATRIBUCION. Si la ejecucion ya termino no hace nada."""
    with sesion() as s:
        ejecucion = s.get_one(EjecucionAtribucion, ejecucion_id)
        if ejecucion.estado != EstadoAtribucion.EN_PROCESO:
            log.info("atribucion %s ya termino %s", ejecucion.atribucion_run_id, ejecucion.estado)
            return
    atribuir(ejecucion_id)


def atribuir(ejecucion_id: int, *, cronometro: Cronometro | None = None) -> None:
    """Atribuye la ventana de la ejecucion y la deja en un estado terminal: con su fila bloqueada
    hasta el commit, todo en una transaccion y en un savepoint; si falla, queda FALLIDA sin soltar
    su fila, y solo publica o falla el dueno vigente de su trabajo. Levanta AtribucionYaPublicada si
    otra ejecucion publico primero las mismas entradas, TrabajoAjeno, y los errores transitorios de
    la base, con la ejecucion EN_PROCESO para que la cola la reintente."""
    cronometro = cronometro or Cronometro()
    with sesion() as s:
        ejecucion = s.exec(
            select(EjecucionAtribucion)
            .where(EjecucionAtribucion.id == ejecucion_id)
            .with_for_update()
        ).one()
        if ejecucion.estado != EstadoAtribucion.EN_PROCESO:
            log.warning(
                "atribucion %s ya termino %s", ejecucion.atribucion_run_id, ejecucion.estado
            )
            return
        etiqueta, desde = ejecucion.atribucion_run_id, ejecucion.periodo_desde
        avance = _Avance()
        try:
            with s.begin_nested():
                _atribuir(s, ejecucion, avance, cronometro)
        except _NoSePublica as exc:
            resultado, motivo = exc.resultado, str(exc)
        except IntegrityError as exc:
            if restriccion(exc) == INDICE_EXITOSA:
                resultado, motivo = (
                    ResultadoAtribucion.YA_ATRIBUIDA,
                    f"Otra ejecucion atribuyo la ventana {desde.isoformat()} con las mismas "
                    f"entradas y {VERSION_ATRIBUCION} mientras esta se procesaba; no se publica "
                    "dos veces.",
                )
            else:
                log.exception("atribucion %s: violacion de integridad inesperada", etiqueta)
                resultado, motivo = (
                    ResultadoAtribucion.ERROR_INTERNO,
                    "Error interno al publicar; ver la bitacora del servicio.",
                )
        except OperationalError:
            log.warning("atribucion %s: error transitorio; sigue EN_PROCESO", etiqueta)
            raise
        except Exception as exc:
            log.exception("atribucion %s: error inesperado", etiqueta)
            resultado, motivo = (
                ResultadoAtribucion.ERROR_INTERNO,
                f"Error interno ({type(exc).__name__}); ver la bitacora.",
            )
        else:
            cola.confirmar_dueno(s)
            with cronometro.fase("commit"):
                s.commit()
            return
        _fallar(s, ejecucion_id, etiqueta, resultado, motivo, avance)
        if resultado == ResultadoAtribucion.YA_ATRIBUIDA:
            raise AtribucionYaPublicada(_exitosa_con_la_firma(s, ejecucion_id))


def _atribuir(
    s: Session, ejecucion: EjecucionAtribucion, avance: _Avance, cronometro: Cronometro
) -> None:
    if ejecucion.version_atribucion != VERSION_ATRIBUCION:
        raise _NoSePublica(
            ResultadoAtribucion.VERSION_NO_SOPORTADA,
            f"La ejecucion pide {ejecucion.version_atribucion} y este servicio solo atribuye con "
            f"{VERSION_ATRIBUCION}; no se publico nada.",
        )
    ventana = Ventana(
        ejecucion.despacho_id,
        ejecucion.cartera_id,
        ejecucion.periodo_desde,
        ejecucion.periodo_hasta,
    )
    motor = interpretacion_vigente(s, ventana)
    if motor is None:
        raise _NoSePublica(
            ResultadoAtribucion.SIN_INTERPRETACION_DE_PAGOS,
            f"La ventana {ventana.desde.isoformat()} no tiene una interpretacion vigente de "
            f"{VERSION_MOTOR_PAGOS}: no hay movimientos que atribuir.",
        )
    avance.motor = motor.id
    parametros = {
        "ejecucion": ejecucion.id,
        "motor": motor.id,
        "zona": ejecucion.zona_horaria,
        "ventana": ejecucion.ventana_dias,
    }
    with cronometro.fase("staging"):
        for sentencia in SQL_LEER:
            s.execute(text(sentencia), parametros)
        avance.gestiones, avance.anuladas = s.execute(
            text("SELECT count(*), count(*) FILTER (WHERE anulada) FROM at_gestion")
        ).one()
    with cronometro.fase("firma_de_entrada"):
        firma = avance.firma = _firma_de_entrada(s, ejecucion, motor)
    previa = _ya_atribuida(s, ventana, firma)
    if previa is not None:
        raise _NoSePublica(
            ResultadoAtribucion.YA_ATRIBUIDA,
            f"La ejecucion {previa.atribucion_run_id} ya atribuyo exactamente estas entradas (la "
            f"misma firma de entrada) con {VERSION_ATRIBUCION}; no se publica dos veces.",
        )
    with cronometro.fase("candidatos"):
        for sentencia in SQL_CANDIDATOS:
            s.execute(text(sentencia), parametros)
    with cronometro.fase("resultados"):
        s.execute(text(SQL_RESULTADOS), parametros)
        s.execute(text(SQL_CANDIDATAS), parametros)
    with cronometro.fase("conteos"):
        conteos = _contar(s, ejecucion.id, motor.id)
    _cerrar(s, ejecucion, motor, firma, avance, conteos)


# --- lo que se lee --------------------------------------------------------------------------------

SQL_LEER = (
    # Los PAGO de la interpretacion vigente de la ventana, por el indice de sus movimientos, con
    # su recepcion como instante en la zona de la fuente.
    "CREATE TEMP TABLE at_movimiento ON COMMIT DROP AS "
    "SELECT m.id, m.movimiento_id, m.cuenta_canonica_id AS cuenta, m.fecha_recepcion, "
    "timezone(:zona, m.fecha_recepcion) AS instante, m.monto_reportado AS monto, "
    "m.anulado_por_movimiento_id AS anulado_por "
    "FROM movimiento_economico_canonico m "
    "WHERE m.ejecucion_motor_pagos_id = :motor AND m.tipo_movimiento = 'PAGO'",
    "CREATE INDEX ON at_movimiento (cuenta, instante)",
    "ANALYZE at_movimiento",
    # Las gestiones de esas cuentas que pudieron anteceder a alguno de sus pagos: de la ventana
    # hacia atras desde su primer pago hasta su ultimo. Todas, con contacto o sin el, anuladas o no:
    # son lo que se leyo, y lo que explica por que un movimiento no tuvo candidata.
    "CREATE TEMP TABLE at_gestion ON COMMIT DROP AS "
    "SELECT g.id, g.gestion_id, g.cuenta_canonica_id AS cuenta, g.ocurrido_en, g.nivel_contacto, "
    "EXISTS (SELECT 1 FROM evento_lifecycle a WHERE a.evento_relacionado_id = "
    "g.evento_lifecycle_id AND a.tipo_evento = 'GESTION_ANULADA') AS anulada "
    "FROM (SELECT cuenta, min(instante) AS primero, max(instante) AS ultimo "
    "FROM at_movimiento WHERE cuenta IS NOT NULL GROUP BY cuenta) c "
    "JOIN gestion_cobranza g ON g.cuenta_canonica_id = c.cuenta "
    "AND g.ocurrido_en >= c.primero - make_interval(days => :ventana) "
    "AND g.ocurrido_en <= c.ultimo",
    "CREATE INDEX ON at_gestion (cuenta, ocurrido_en)",
    "ANALYZE at_gestion",
)
"""Lo que se atribuye, en tablas temporales que se borran al terminar la transaccion."""

_CON_CONTACTO = ", ".join(f"'{n}'" for n in sorted(CONTACTOS))

SQL_CANDIDATOS = (
    # Cada gestion de la cuenta de cada movimiento, en su ventana: antes del movimiento o en su
    # instante, a lo mas ventana_dias antes. Una sola union temporal, por el indice de at_gestion.
    "CREATE TEMP TABLE at_par ON COMMIT DROP AS "
    "SELECT m.id AS movimiento, g.id AS gestion, g.anulada, "
    f"g.nivel_contacto IN ({_CON_CONTACTO}) AS con_contacto, "
    "floor(extract(epoch FROM m.instante - g.ocurrido_en))::bigint AS antelacion "
    "FROM at_movimiento m JOIN at_gestion g ON g.cuenta = m.cuenta "
    "AND g.ocurrido_en <= m.instante "
    "AND g.ocurrido_en >= m.instante - make_interval(days => :ventana)",
    # Por movimiento: cuantas candidatas (vigentes y con contacto), cual si es una, y cuantas
    # gestiones de su ventana no lo fueron, y por que.
    "CREATE TEMP TABLE at_resumen ON COMMIT DROP AS "
    "SELECT movimiento, "
    "count(*) FILTER (WHERE NOT anulada AND con_contacto) AS candidatas, "
    "min(gestion) FILTER (WHERE NOT anulada AND con_contacto) AS unica, "
    "count(*) FILTER (WHERE NOT anulada AND NOT con_contacto) AS sin_contacto, "
    "count(*) FILTER (WHERE anulada) AS anuladas "
    "FROM at_par GROUP BY movimiento",
    "CREATE UNIQUE INDEX ON at_resumen (movimiento)",
)

C = Clasificacion
SQL_RESULTADOS = f"""
INSERT INTO atribucion_movimiento (ejecucion_atribucion_id, movimiento_economico_canonico_id,
    fecha_recepcion, gestion_cobranza_id, movimiento_id, cuenta_canonica_id, candidatos, monto,
    anulado_por_reverso, clasificacion, motivos)
SELECT :ejecucion, m.id, m.fecha_recepcion,
    CASE WHEN r.candidatas = 1 THEN r.unica END,
    m.movimiento_id, m.cuenta, coalesce(r.candidatas, 0), m.monto, m.anulado_por IS NOT NULL,
    CASE WHEN coalesce(r.candidatas, 0) = 0 THEN '{C.SIN_GESTION_CANDIDATA}'
         WHEN r.candidatas = 1 THEN '{C.ASOCIACION_UNICA}'
         ELSE '{C.AMBIGUA}' END,
    jsonb_build_array(CASE
        WHEN m.cuenta IS NULL THEN jsonb_build_object('codigo', '{Motivo.MOVIMIENTO_SIN_CUENTA}')
        WHEN coalesce(r.candidatas, 0) = 0 THEN jsonb_build_object(
            'codigo', '{Motivo.SIN_GESTION_EN_LA_VENTANA}', 'ventana_dias', :ventana,
            'gestiones_sin_contacto', coalesce(r.sin_contacto, 0),
            'gestiones_anuladas', coalesce(r.anuladas, 0))
        WHEN r.candidatas = 1 THEN jsonb_build_object(
            'codigo', '{Motivo.UNA_GESTION_CANDIDATA}', 'ventana_dias', :ventana)
        ELSE jsonb_build_object('codigo', '{Motivo.VARIAS_GESTIONES_CANDIDATAS}',
            'candidatas', r.candidatas, 'ventana_dias', :ventana) END)
    || CASE WHEN m.anulado_por IS NULL THEN '[]'::jsonb
        ELSE jsonb_build_array(jsonb_build_object('codigo', '{Motivo.ANULADO_POR_REVERSO}',
            'reverso', m.anulado_por)) END
FROM at_movimiento m
LEFT JOIN at_resumen r ON r.movimiento = m.id
ORDER BY m.id
"""
"""Una clasificacion por cada PAGO de la ventana, en el orden de su llave."""

SQL_CANDIDATAS = """
INSERT INTO candidato_atribucion (ejecucion_atribucion_id, movimiento_economico_canonico_id,
    gestion_cobranza_id, antelacion_segundos)
SELECT :ejecucion, movimiento, gestion, antelacion FROM at_par
WHERE NOT anulada AND con_contacto
ORDER BY movimiento, gestion
"""
"""Cada candidata de cada movimiento, en relaciones: es lo que explica una asociacion unica y una
ambigua, sin elegir ninguna."""


def _firma_de_entrada(
    s: Session, ejecucion: EjecucionAtribucion, motor: EjecucionMotorPagos
) -> str:
    """El SHA-256 de lo que se leyo, en forma canonica: la interpretacion de pagos (su
    identificador y su propia firma de entrada, que resume sus pagos observados) y cada gestion
    leida con su nivel de contacto y si esta anulada, con la version, la ventana, la ventana hacia
    atras y la zona delante. La misma ventana con las mismas entradas da la misma firma."""
    cabecera = (
        f"{VERSION_ATRIBUCION}\n{ejecucion.despacho_id}\n{ejecucion.cartera_id}\n"
        f"{ejecucion.periodo_desde.isoformat()}\n{ejecucion.periodo_hasta.isoformat()}\n"
        f"{ejecucion.ventana_dias}\n{ejecucion.zona_horaria}\n"
        f"{motor.motor_pagos_run_id}\n{motor.firma_entrada}\n"
    )
    return s.execute(
        text(
            "SELECT encode(sha256(convert_to(:cabecera || coalesce(string_agg(atomo, chr(10) "
            "ORDER BY atomo), ''), 'UTF8')), 'hex') FROM ("
            "SELECT 'g:' || gestion_id || ':' || nivel_contacto || ':' || anulada AS atomo "
            "FROM at_gestion) atomos"
        ),
        {"cabecera": cabecera},
    ).scalar_one()


# --- lo que se publica ----------------------------------------------------------------------------


def _contar(s: Session, ejecucion_id: int, motor_id: int) -> _Conteos:
    fila = s.execute(
        text(
            "SELECT count(*), "
            + ", ".join(
                f"count(*) FILTER (WHERE clasificacion = '{c}')"
                for c in (C.ASOCIACION_UNICA, C.AMBIGUA, C.SIN_GESTION_CANDIDATA)
            )
            + ", coalesce(sum(candidatos), 0), count(*) FILTER (WHERE anulado_por_reverso), "
            + ", ".join(
                f"coalesce(sum(monto) FILTER (WHERE clasificacion = '{c}' "
                "AND NOT anulado_por_reverso), 0)"
                for c in (C.ASOCIACION_UNICA, C.AMBIGUA, C.SIN_GESTION_CANDIDATA)
            )
            + ", coalesce(sum(monto) FILTER (WHERE anulado_por_reverso), 0) "
            "FROM atribucion_movimiento WHERE ejecucion_atribucion_id = :ejecucion"
        ),
        {"ejecucion": ejecucion_id},
    ).one()
    candidatas = s.execute(
        text(
            "SELECT count(*) FROM candidato_atribucion WHERE ejecucion_atribucion_id = :ejecucion"
        ),
        {"ejecucion": ejecucion_id},
    ).scalar_one()
    fuera = s.execute(
        text(
            "SELECT count(*) FROM movimiento_economico_canonico "
            "WHERE ejecucion_motor_pagos_id = :motor AND tipo_movimiento <> 'PAGO'"
        ),
        {"motor": motor_id},
    ).scalar_one()
    (
        evaluados,
        asociados,
        ambiguos,
        sin_candidato,
        candidatos,
        anulados,
        monto_asociado,
        monto_ambiguo,
        monto_sin_candidato,
        monto_anulado,
    ) = fila
    if candidatos != candidatas:
        raise _NoSePublica(
            ResultadoAtribucion.DATOS_INCONSISTENTES,
            f"Las clasificaciones dicen {candidatos:,} candidatas y se publicaron {candidatas:,}; "
            "no se publico nada.",
        )
    return _Conteos(
        movimientos_evaluados=evaluados,
        asociados=asociados,
        ambiguos=ambiguos,
        sin_candidato=sin_candidato,
        candidatos=candidatas,
        movimientos_anulados=anulados,
        monto_asociado=monto_asociado,
        monto_ambiguo=monto_ambiguo,
        monto_sin_candidato=monto_sin_candidato,
        monto_anulado=monto_anulado,
        fuera_de_alcance=fuera,
    )


def _cerrar(
    s: Session,
    ejecucion: EjecucionAtribucion,
    motor: EjecucionMotorPagos,
    firma: str,
    avance: _Avance,
    conteos: _Conteos,
) -> None:
    """EXITOSA, con sus conteos, en la transaccion que publico. Antes comprueba que se haya
    clasificado cada PAGO leido."""
    leidos = s.execute(text("SELECT count(*) FROM at_movimiento")).scalar_one()
    if conteos.movimientos_evaluados != leidos:
        raise _NoSePublica(
            ResultadoAtribucion.DATOS_INCONSISTENTES,
            f"Se leyeron {leidos:,} pagos y se clasificaron {conteos.movimientos_evaluados:,}; no "
            "se publico nada.",
        )
    ejecucion.estado = EstadoAtribucion.EXITOSA
    ejecucion.resultado = ResultadoAtribucion.ATRIBUCION_PUBLICADA.value
    ejecucion.firma_entrada = firma
    ejecucion.ejecucion_motor_pagos_id = motor.id
    ejecucion.movimientos_evaluados = conteos.movimientos_evaluados
    ejecucion.asociados = conteos.asociados
    ejecucion.ambiguos = conteos.ambiguos
    ejecucion.sin_candidato = conteos.sin_candidato
    ejecucion.candidatos = conteos.candidatos
    ejecucion.movimientos_anulados = conteos.movimientos_anulados
    ejecucion.gestiones_leidas = avance.gestiones
    ejecucion.gestiones_anuladas = avance.anuladas
    ejecucion.monto_asociado = conteos.monto_asociado
    ejecucion.monto_ambiguo = conteos.monto_ambiguo
    ejecucion.monto_sin_candidato = conteos.monto_sin_candidato
    ejecucion.monto_anulado = conteos.monto_anulado
    ejecucion.detalle = (
        f"Se atribuyeron {conteos.movimientos_evaluados:,} pagos recibidos desde el "
        f"{ejecucion.periodo_desde.isoformat()} y antes del {ejecucion.periodo_hasta.isoformat()}, "
        f"de la interpretacion {motor.motor_pagos_run_id}, con una ventana de "
        f"{ejecucion.ventana_dias} dias en {ejecucion.zona_horaria}: {conteos.asociados:,} con "
        f"asociacion unica, {conteos.ambiguos:,} ambiguos y {conteos.sin_candidato:,} sin gestion "
        f"candidata; {conteos.movimientos_anulados:,} anulados por un reverso. "
        f"{conteos.fuera_de_alcance:,} reversos y posibles reversos no se atribuyen. Se leyeron "
        f"{avance.gestiones:,} gestiones de sus cuentas ({avance.anuladas:,} anuladas). "
        f"Asociacion operacional, no causalidad. {VERSION_ATRIBUCION}."
    )
    ejecucion.terminada_en = ahora()
    s.add(ejecucion)
    s.flush()


def _fallar(
    s: Session,
    ejecucion_id: int,
    etiqueta: UUID,
    resultado: ResultadoAtribucion,
    motivo: str,
    avance: _Avance,
) -> None:
    """FALLIDA, con su fila todavia bloqueada, solo si quien la ejecuta sigue siendo el dueno de su
    trabajo; si no, TrabajoAjeno sin cambiar nada. Dice cuantas gestiones alcanzo a leer."""
    try:
        cola.confirmar_dueno(s)
    except cola.TrabajoAjeno:
        s.rollback()
        log.warning("atribucion %s: su trabajo ya no es de este worker: %s", etiqueta, motivo)
        raise
    registrado = s.execute(
        update(EjecucionAtribucion)
        .where(
            EjecucionAtribucion.id == ejecucion_id,
            EjecucionAtribucion.estado == EstadoAtribucion.EN_PROCESO,
        )
        .values(
            estado=EstadoAtribucion.FALLIDA,
            resultado=resultado.value,
            firma_entrada=avance.firma,
            gestiones_leidas=avance.gestiones,
            gestiones_anuladas=avance.anuladas,
            detalle=motivo,
            terminada_en=ahora(),
        )
        .execution_options(synchronize_session=False)
    ).rowcount
    s.commit()
    if registrado:
        log.warning("atribucion %s fallida (%s): %s", etiqueta, resultado.value, motivo)


def _ya_atribuida(s: Session, ventana: Ventana, firma: str) -> EjecucionAtribucion | None:
    return s.exec(
        _de_la_ventana(ventana, EstadoAtribucion.EXITOSA).where(
            EjecucionAtribucion.firma_entrada == firma
        )
    ).first()


def _exitosa_con_la_firma(s: Session, ejecucion_id: int) -> EjecucionAtribucion:
    perdedora = s.get_one(EjecucionAtribucion, ejecucion_id)
    ventana = Ventana(
        perdedora.despacho_id,
        perdedora.cartera_id,
        perdedora.periodo_desde,
        perdedora.periodo_hasta,
    )
    return s.exec(
        _de_la_ventana(ventana, EstadoAtribucion.EXITOSA).where(
            EjecucionAtribucion.firma_entrada == perdedora.firma_entrada
        )
    ).one()


def _de_la_ventana(ventana: Ventana, estado: EstadoAtribucion):
    """Las ejecuciones de la ventana con atribucion/v1 en ese estado. De EN_PROCESO hay a lo mas
    una: lo garantiza un indice unico parcial."""
    return select(EjecucionAtribucion).where(
        EjecucionAtribucion.despacho_id == ventana.despacho_id,
        EjecucionAtribucion.cartera_id == ventana.cartera_id,
        EjecucionAtribucion.version_atribucion == VERSION_ATRIBUCION,
        EjecucionAtribucion.periodo_desde == ventana.desde,
        EjecucionAtribucion.estado == estado,
    )
