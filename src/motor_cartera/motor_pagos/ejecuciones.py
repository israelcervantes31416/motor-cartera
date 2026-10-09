"""La interpretacion de una ventana de pagos observados, en PostgreSQL y todo o nada.

Una ventana es un despacho, una cartera y un mes calendario de recepcion, [desde, hasta). El
orden:

1. Cuando la historia publica los pagos observados de un archivo, en la misma transaccion abre (o
   reusa) la ejecucion EN_PROCESO de cada ventana que esos pagos tocan, con su trabajo MOTOR_PAGOS
   (`abrir_por_dataset`); tambien la de una ventana vecina si esos pagos cambian su contexto. No
   hay un instante en que existan pagos observados sin su interpretacion pendiente.
2. Un worker toma el trabajo, e `interpretar` toma la ejecucion con su fila bloqueada: una ventana
   la interpreta un solo worker a la vez, y una ejecucion terminada no se vuelve a interpretar.
3. Una sola sentencia lee a una tabla temporal los pagos de la ventana y los de su contexto (los de
   hasta 30 dias antes y despues con el cliente y el importe de algun negativo), con sus dos
   huellas, el SHA-256 de su archivo original y su cuenta canonica. Una sola sentencia: todo lo que
   se interpreta sale de la misma foto de la base.
4. De ahi en adelante todo es SQL por conjuntos sobre las tablas temporales: los grupos de copias y
   de la llave historica, la comprobacion campo por campo de las copias, las parejas de reverso, y
   los movimientos y los resultados con INSERT ... SELECT. Ninguna observacion pasa por Python.
5. Se cuenta, se comprueba que cada observacion tenga un resultado y se cierra EXITOSA, en la
   misma transaccion. Si algo falla, se revierte todo y queda FALLIDA: ningun resultado ni
   movimiento a medias.

Lo que hace segura la entrega repetida no es la cola: es el bloqueo de la ejecucion, sus estados
terminales, la transaccion todo o nada, los indices unicos (una EXITOSA por ventana, version y
firma de entrada; una EN_PROCESO por ventana y version) y que solo publica el dueno vigente de su
trabajo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, fields
from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session, select
from sqlmodel.sql.expression import SelectOfScalar

from motor_cartera.db.modelos import (
    DatasetConformado,
    EjecucionMotorPagos,
    EstadoMotorPagos,
    IngestaPagos,
    ResultadoMotorPagos,
    TipoTrabajo,
    ahora,
)
from motor_cartera.db.sesion import insertar_en_savepoint, restriccion, sesion
from motor_cartera.ingesta.fuente_oficial import Cronometro
from motor_cartera.motor_pagos.firmas import (
    CAMPOS_FIRMADOS,
    sql_digest_movimiento,
    sql_firma_exacta,
    sql_firma_legacy,
    sql_uuid_de_digest,
)
from motor_cartera.motor_pagos.reglas import (
    VENTANA_REVERSO,
    VERSION_MOTOR_PAGOS,
    Clasificacion,
    periodos_entre,
    siguiente_periodo,
)
from motor_cartera.orquestacion import cola

log = logging.getLogger(__name__)

INDICE_EXITOSA = "ux_ejecucion_motor_pagos_exitosa"
INDICE_EN_PROCESO = "ux_ejecucion_motor_pagos_en_proceso"
INTENTOS_DE_APERTURA = 3
"""Cuantas veces se revisa y se inserta una ejecucion que pierde la carrera contra otra apertura de
la misma ventana, como en los demas motores."""

DIAS_DE_VENTANA = VENTANA_REVERSO.days


class MotorPagosYaInterpretado(Exception):
    """Otra ejecucion de la ventana publico primero exactamente las mismas entradas."""

    def __init__(self, previa: EjecucionMotorPagos) -> None:
        super().__init__(
            f"La ventana {previa.periodo_desde.isoformat()} ya se interpreto con estas mismas "
            f"entradas y {previa.version_motor}: la ejecucion {previa.motor_pagos_run_id}."
        )
        self.previa = previa


class _NoSePublica(Exception):
    """Una razon conocida para no publicar nada, con su resultado. El mensaje va tal cual al detalle
    de la ejecucion: es para una persona y no lleva datos de los pagos."""

    def __init__(self, resultado: ResultadoMotorPagos, mensaje: str) -> None:
        super().__init__(mensaje)
        self.resultado = resultado


@dataclass(frozen=True)
class Ventana:
    """Lo que interpreta una ejecucion: un despacho, una cartera y un mes de recepcion."""

    despacho_id: str
    cartera_id: str
    desde: date
    hasta: date

    @classmethod
    def del_periodo(cls, despacho_id: str, cartera_id: str, periodo: date) -> Ventana:
        return cls(despacho_id, cartera_id, periodo, siguiente_periodo(periodo))

    @classmethod
    def de(cls, ejecucion: EjecucionMotorPagos) -> Ventana:
        return cls(
            ejecucion.despacho_id,
            ejecucion.cartera_id,
            ejecucion.periodo_desde,
            ejecucion.periodo_hasta,
        )

    @property
    def inicio(self) -> datetime:
        return datetime.combine(self.desde, time())

    @property
    def fin(self) -> datetime:
        return datetime.combine(self.hasta, time())

    @property
    def contexto_desde(self) -> datetime:
        """Hasta donde mira atras: un reverso del primer dia busca su original 30 dias antes."""
        return self.inicio - VENTANA_REVERSO

    @property
    def contexto_hasta(self) -> datetime:
        """Hasta donde mira adelante: otro reverso del pago del ultimo dia puede llegar 30 dias
        despues, y entonces la pareja ya no es unica."""
        return self.fin + VENTANA_REVERSO

    def parametros(self) -> dict:
        return {
            "despacho": self.despacho_id,
            "cartera": self.cartera_id,
            "desde": self.inicio,
            "hasta": self.fin,
            "contexto_desde": self.contexto_desde,
            "contexto_hasta": self.contexto_hasta,
        }


# --- abrir una ventana ----------------------------------------------------------------------------


def abrir_ventana(
    s: Session, ventana: Ventana, *, max_intentos: int
) -> tuple[EjecucionMotorPagos, bool]:
    """La ejecucion EN_PROCESO de la ventana con motor-pagos/v1 y su trabajo MOTOR_PAGOS, en la
    transaccion de quien llama: envia los INSERT y no confirma. Devuelve la ejecucion y si es
    nueva.

    Si la ventana ya tiene una EN_PROCESO, la reusa: todavia no lee nada, o la esta leyendo un
    worker que la tiene bloqueada, y entonces se espera a que termine. En el primer caso la leera
    despues del commit de quien llama; en el segundo, al terminar ya no esta EN_PROCESO y se abre
    otra. Asi ningun pago que se publique queda sin una interpretacion pendiente que lo vea.
    """
    for _ in range(INTENTOS_DE_APERTURA):
        activa = s.exec(
            _de_la_ventana(ventana, EstadoMotorPagos.EN_PROCESO).with_for_update()
        ).first()
        if activa is not None:
            return activa, False
        ejecucion = EjecucionMotorPagos(
            version_motor=VERSION_MOTOR_PAGOS,
            despacho_id=ventana.despacho_id,
            cartera_id=ventana.cartera_id,
            periodo_desde=ventana.desde,
            periodo_hasta=ventana.hasta,
        )
        error = insertar_en_savepoint(s, ejecucion)
        if error is None:
            break
        if restriccion(error) != INDICE_EN_PROCESO:
            raise error
    else:
        raise error
    cola.crear(s, TipoTrabajo.MOTOR_PAGOS, ejecucion.id, max_intentos=max_intentos)
    log.info(
        "motor de pagos %s abierto para %s/%s %s",
        ejecucion.motor_pagos_run_id,
        ventana.despacho_id,
        ventana.cartera_id,
        ventana.desde.isoformat(),
    )
    return ejecucion, True


def abrir_por_dataset(
    s: Session, dataset: DatasetConformado, *, max_intentos: int
) -> list[EjecucionMotorPagos]:
    """Abre o reusa la interpretacion de cada ventana que cambia porque se publicaron los pagos
    observados de `dataset`, en la transaccion de quien llama (la de la historia que los publica).

    Cambian las ventanas que tienen pagos del dataset, y las vecinas cuyo contexto de reversos
    recibe alguno: un pago o un negativo del mismo cliente e importe que un negativo de la vecina,
    a menos de 30 dias de ella. Las aperturas de una misma cartera se hacen una a la vez (un bloqueo
    consultivo hasta el commit): asi la que va despues ve los pagos que publico la anterior, y dos
    archivos que llegan juntos no pueden dejar sin interpretar un contexto que solo cambian los dos.
    """
    ingesta = s.get_one(IngestaPagos, dataset.ingesta_pagos_id)
    despacho, cartera = ingesta.despacho_id, ingesta.cartera_id
    s.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:llave, 0))"),
        {"llave": f"motor-pagos:{despacho}:{cartera}"},
    )
    for sentencia in SQL_APERTURA:
        s.execute(text(sentencia), {"dataset": dataset.id})
    extremos = s.execute(
        text("SELECT min(fecha_recepcion), max(fecha_recepcion) FROM mp_apertura")
    ).one()
    if extremos[0] is None:
        return []
    propias = {
        fila[0]
        for fila in s.execute(
            text("SELECT DISTINCT date_trunc('month', fecha_recepcion)::date FROM mp_apertura")
        )
    }
    vecinas = [
        Ventana.del_periodo(despacho, cartera, periodo)
        for periodo in periodos_entre(extremos[0] - VENTANA_REVERSO, extremos[1] + VENTANA_REVERSO)
        if periodo not in propias
    ]
    relevantes = {v.desde for v in vecinas if _cambia_su_contexto(s, v)}
    abiertas = []
    for periodo in sorted(propias | relevantes):
        ejecucion, _ = abrir_ventana(
            s, Ventana.del_periodo(despacho, cartera, periodo), max_intentos=max_intentos
        )
        abiertas.append(ejecucion)
    return abiertas


SQL_APERTURA = (
    "DROP TABLE IF EXISTS pg_temp.mp_apertura",
    "CREATE TEMP TABLE mp_apertura ON COMMIT DROP AS SELECT cliente_unico, "
    "abs(recuperacion_por_gestion) AS importe, fecha_recepcion FROM pago_observado "
    "WHERE dataset_conformado_id = :dataset",
    "CREATE INDEX ON mp_apertura (cliente_unico, importe)",
    "ANALYZE mp_apertura",
)
"""Los pagos del dataset que se acaba de publicar, en una tabla temporal con su indice y sus
estadisticas. Se publicaron en esta misma transaccion, asi que las estadisticas de pago_observado
todavia no los conocen: el planificador estima una fila, y con esa estimacion puede unir el contexto
de una vecina recorriendo todos los pagos del dataset por cada negativo (medido en el XL: 84 s para
un archivo de 262,997 pagos). Con la tabla temporal, cualquier plan que elija es lineal."""


def _cambia_su_contexto(s: Session, ventana: Ventana) -> bool:
    """Si los pagos del dataset (en mp_apertura) entran al contexto de `ventana`: es exactamente la
    condicion con que la interpretacion los leeria (ver SQL_LEER)."""
    return bool(
        s.execute(
            text(
                "WITH llaves AS (SELECT DISTINCT n.cliente_unico, -n.recuperacion_por_gestion AS "
                "importe FROM pago_observado n WHERE n.despacho_id = :despacho AND n.cartera_id = "
                ":cartera AND n.recuperacion_por_gestion < 0 AND n.fecha_recepcion >= "
                ":contexto_desde AND n.fecha_recepcion < :contexto_hasta) "
                "SELECT EXISTS (SELECT 1 FROM mp_apertura o JOIN llaves k ON k.cliente_unico = "
                "o.cliente_unico AND k.importe = o.importe "
                "WHERE ((o.fecha_recepcion >= :contexto_desde AND o.fecha_recepcion < :desde) "
                "OR (o.fecha_recepcion >= :hasta AND o.fecha_recepcion < :contexto_hasta)) "
                "AND EXISTS (SELECT 1 FROM pago_observado a WHERE a.despacho_id = :despacho "
                "AND a.cartera_id = :cartera AND a.cliente_unico = o.cliente_unico "
                "AND a.fecha_recepcion >= :desde AND a.fecha_recepcion < :hasta "
                "AND abs(a.recuperacion_por_gestion) = k.importe))"
            ),
            ventana.parametros(),
        ).scalar_one()
    )


# --- interpretar ----------------------------------------------------------------------------------


@dataclass
class _Avance:
    """Cuanto alcanzo a leer, y su firma: es lo que una FALLIDA dice que leyo."""

    leidas: int = 0
    contexto: int = 0
    firma: str | None = None


@dataclass(frozen=True)
class _Conteos:
    """Lo que la ejecucion publica de si misma, contado de lo que publico."""

    observaciones_clasificadas: int
    primarios: int
    duplicados_exactos: int
    coincidencias_ambiguas: int
    reversos: int
    posibles_reversos: int
    no_conciliados: int
    sin_cuenta_observada: int
    movimientos_canonicos: int
    pagos_anulados: int
    grupos_exactos: int
    grupos_legacy: int
    grupos_ambiguos: int
    observaciones_en_grupos_legacy: int
    recuperacion_bruta_interpretada: Decimal
    recuperacion_neta_interpretada: Decimal
    importe_ambiguo_observado: Decimal


def ejecutar_motor_pagos(ejecucion_id: int) -> None:
    """Lo que ejecuta un trabajo MOTOR_PAGOS. Si la ejecucion ya termino no hace nada."""
    with sesion() as s:
        ejecucion = s.get_one(EjecucionMotorPagos, ejecucion_id)
        if ejecucion.estado != EstadoMotorPagos.EN_PROCESO:
            log.info(
                "motor de pagos %s ya termino %s", ejecucion.motor_pagos_run_id, ejecucion.estado
            )
            return
    interpretar(ejecucion_id)


def interpretar(ejecucion_id: int, *, cronometro: Cronometro | None = None) -> None:
    """Interpreta la ventana de la ejecucion y la deja en un estado terminal.

    La ejecucion se toma con su fila bloqueada hasta el commit, y todo va en una sola transaccion:
    los movimientos, los resultados y el cierre. La interpretacion corre en un savepoint: si falla,
    se revierte solo lo de este intento y la ejecucion queda FALLIDA en la misma transaccion, sin
    soltar su fila. Asi nadie la ve EN_PROCESO entre el fallo y su cierre: una historia que abre la
    ventana en ese momento espera, la encuentra FALLIDA y abre otra, en lugar de dejarle sus pagos a
    una ejecucion que ya no los va a leer. Ningun camino de error degrada un estado terminal. Si la
    ejecuta un worker que perdio su trabajo, no publica ni la falla: la termina el dueno vigente.

    No levanta excepciones, salvo tres: MotorPagosYaInterpretado, si otra ejecucion de la ventana
    publico primero las mismas entradas (esta ya quedo FALLIDA); TrabajoAjeno; y los errores
    transitorios de la base (OperationalError: la conexion, un interbloqueo, una cancelacion), que
    no son una conclusion del motor: se revierte todo, la ejecucion sigue EN_PROCESO y la cola
    reintenta su trabajo con su espera, como cualquier error del worker.
    """
    cronometro = cronometro or Cronometro()
    with sesion() as s:
        ejecucion = s.exec(
            select(EjecucionMotorPagos)
            .where(EjecucionMotorPagos.id == ejecucion_id)
            .with_for_update()
        ).one()
        if ejecucion.estado != EstadoMotorPagos.EN_PROCESO:
            log.warning(
                "motor de pagos %s ya termino %s; no se interpreta otra vez",
                ejecucion.motor_pagos_run_id,
                ejecucion.estado,
            )
            return
        # Se leen ahora: despues de revertir el savepoint la ejecucion en memoria caduca.
        etiqueta, ventana = ejecucion.motor_pagos_run_id, Ventana.de(ejecucion)
        avance = _Avance()
        try:
            with s.begin_nested():
                _interpretar(s, ejecucion, avance, cronometro)
        except _NoSePublica as exc:
            resultado, motivo = exc.resultado, str(exc)
        except IntegrityError as exc:
            if restriccion(exc) == INDICE_EXITOSA:
                resultado, motivo = (
                    ResultadoMotorPagos.YA_INTERPRETADA,
                    f"Otra ejecucion interpreto la ventana {ventana.desde.isoformat()} con las "
                    f"mismas entradas y {VERSION_MOTOR_PAGOS} mientras esta se procesaba; no se "
                    "publica dos veces.",
                )
            else:
                log.exception("motor de pagos %s: violacion de integridad inesperada", etiqueta)
                resultado, motivo = (
                    ResultadoMotorPagos.ERROR_INTERNO,
                    "Error interno al publicar; ver la bitacora del servicio.",
                )
        except OperationalError:
            log.warning("motor de pagos %s: error transitorio; sigue EN_PROCESO", etiqueta)
            raise
        except Exception as exc:
            log.exception("motor de pagos %s: error inesperado", etiqueta)
            resultado, motivo = (
                ResultadoMotorPagos.ERROR_INTERNO,
                f"Error interno ({type(exc).__name__}); ver la bitacora.",
            )
        else:
            cola.confirmar_dueno(s)
            with cronometro.fase("commit"):
                s.commit()  # el unico commit que publica una interpretacion
            return
        _fallar(s, ejecucion_id, etiqueta, resultado, motivo, avance)
        if resultado == ResultadoMotorPagos.YA_INTERPRETADA:
            raise MotorPagosYaInterpretado(_exitosa_con_la_firma(s, ejecucion_id))


def _interpretar(
    s: Session, ejecucion: EjecucionMotorPagos, avance: _Avance, cronometro: Cronometro
) -> None:
    if ejecucion.version_motor != VERSION_MOTOR_PAGOS:
        raise _NoSePublica(
            ResultadoMotorPagos.VERSION_NO_SOPORTADA,
            f"La ejecucion pide {ejecucion.version_motor} y este servicio solo interpreta con "
            f"{VERSION_MOTOR_PAGOS}; no se publico nada.",
        )
    ventana = Ventana.de(ejecucion)
    # El texto de un float8 depende de esta variable: la firma exacta no puede depender de como
    # este configurado el servidor.
    s.execute(text("SET LOCAL extra_float_digits = 1"))
    _preparar_staging(s)
    with cronometro.fase("staging"):
        avance.leidas, avance.contexto = _leer_ventana(s, ventana)
    with cronometro.fase("firma_de_entrada"):
        firma = avance.firma = _firma_de_entrada(s, ventana)
    previa = _ya_interpretada(s, ventana, firma)
    if previa is not None:
        raise _NoSePublica(
            ResultadoMotorPagos.YA_INTERPRETADA,
            f"La ejecucion {previa.motor_pagos_run_id} ya interpreto exactamente estas entradas "
            f"(la misma firma de entrada) con {VERSION_MOTOR_PAGOS}; no se publica dos veces.",
        )
    with cronometro.fase("clasificacion"):
        _clasificar(s)
    with cronometro.fase("movimientos"):
        _crear_movimientos(s, ejecucion.id, ventana)
    with cronometro.fase("resultados"):
        _insertar_resultados(s, ejecucion.id)
    with cronometro.fase("conteos"):
        conteos = _contar(s, ejecucion.id)
    _cerrar(s, ejecucion, ventana, firma, avance, conteos)


# --- las tablas temporales ------------------------------------------------------------------------


def _preparar_staging(s: Session) -> None:
    """La tabla temporal de lo que se lee. Es de la sesion y se borra al terminar la transaccion
    (ON COMMIT DROP), se confirme o se revierta, como las demas de la interpretacion."""
    s.execute(
        text(
            "CREATE TEMP TABLE mp_observacion (dataset_conformado_id integer NOT NULL, "
            "source_row integer NOT NULL, propia boolean NOT NULL, "
            "pago_observado_id uuid NOT NULL, cliente_unico varchar(20) NOT NULL, "
            "fecha_recepcion timestamp NOT NULL, recuperacion numeric(14, 2) NOT NULL, "
            "firma_exacta bytea NOT NULL, firma_legacy bytea NOT NULL, "
            "archivo varchar(64) NOT NULL, cuenta_canonica_id integer) ON COMMIT DROP"
        )
    )


SQL_LEER = f"""
INSERT INTO mp_observacion (dataset_conformado_id, source_row, propia, pago_observado_id,
    cliente_unico, fecha_recepcion, recuperacion, firma_exacta, firma_legacy, archivo,
    cuenta_canonica_id)
WITH llaves AS (
    SELECT DISTINCT n.cliente_unico, -n.recuperacion_por_gestion AS importe
    FROM pago_observado n
    WHERE n.despacho_id = :despacho AND n.cartera_id = :cartera
      AND n.recuperacion_por_gestion < 0
      AND n.fecha_recepcion >= :contexto_desde AND n.fecha_recepcion < :contexto_hasta
),
llaves_de_la_ventana AS (
    SELECT k.cliente_unico, k.importe FROM llaves k
    WHERE EXISTS (
        SELECT 1 FROM pago_observado a
        WHERE a.despacho_id = :despacho AND a.cartera_id = :cartera
          AND a.cliente_unico = k.cliente_unico
          AND a.fecha_recepcion >= :desde AND a.fecha_recepcion < :hasta
          AND abs(a.recuperacion_por_gestion) = k.importe)
),
leidas AS (
    SELECT p.*, true AS propia FROM pago_observado p
    WHERE p.despacho_id = :despacho AND p.cartera_id = :cartera
      AND p.fecha_recepcion >= :desde AND p.fecha_recepcion < :hasta
    UNION ALL
    SELECT c.*, false FROM llaves_de_la_ventana k
    JOIN pago_observado c ON c.despacho_id = :despacho AND c.cartera_id = :cartera
     AND c.cliente_unico = k.cliente_unico AND abs(c.recuperacion_por_gestion) = k.importe
    WHERE (c.fecha_recepcion >= :contexto_desde AND c.fecha_recepcion < :desde)
       OR (c.fecha_recepcion >= :hasta AND c.fecha_recepcion < :contexto_hasta)
)
SELECT l.dataset_conformado_id, l.source_row, l.propia, l.pago_observado_id, l.cliente_unico,
    l.fecha_recepcion, l.recuperacion_por_gestion, {sql_firma_exacta("l")},
    {sql_firma_legacy("l")}, a.sha256, cc.id
FROM leidas l
JOIN dataset_conformado d ON d.id = l.dataset_conformado_id
JOIN artefacto_fuente a ON a.id = d.artefacto_original_id
LEFT JOIN cuenta_canonica cc ON cc.despacho_id = l.despacho_id AND cc.cartera_id = l.cartera_id
    AND cc.cliente_unico = l.cliente_unico
"""
"""Lo que se interpreta, en una sola sentencia y por lo tanto en una sola foto de la base: los pagos
de la ventana, y de su contexto solo los de una llave (cliente, importe) que tiene algun negativo a
menos de 30 dias de la ventana y algun pago dentro de ella. Los demas no pueden cambiar ninguna
pareja de reverso de la ventana, y no se leen."""


def _leer_ventana(s: Session, ventana: Ventana) -> tuple[int, int]:
    s.execute(text(SQL_LEER), ventana.parametros())
    s.execute(text("ANALYZE mp_observacion"))
    propias, contexto = s.execute(
        text(
            "SELECT count(*) FILTER (WHERE propia), count(*) FILTER (WHERE NOT propia) "
            "FROM mp_observacion"
        )
    ).one()
    return propias, contexto


def _firma_de_entrada(s: Session, ventana: Ventana) -> str:
    """El SHA-256 de lo que se leyo, en forma canonica: cada archivo original con pagos en la
    ventana (sus pagos de la ventana son siempre los mismos: un pago observado no cambia ni se
    borra, y los de un archivo se publican juntos), cada pago del contexto y cada cliente de la
    ventana que tenia cuenta canonica. Con la version y la ventana delante."""
    cabecera = (
        f"{VERSION_MOTOR_PAGOS}\n{ventana.despacho_id}\n{ventana.cartera_id}\n"
        f"{ventana.desde.isoformat()}\n{ventana.hasta.isoformat()}\n"
    )
    return s.execute(
        text(
            "SELECT encode(sha256(convert_to(:cabecera || coalesce(string_agg(atomo, chr(10) "
            "ORDER BY atomo), ''), 'UTF8')), 'hex') FROM ("
            "SELECT DISTINCT 'd:' || archivo AS atomo FROM mp_observacion WHERE propia "
            "UNION ALL SELECT 'c:' || archivo || ':' || source_row FROM mp_observacion "
            "WHERE NOT propia "
            "UNION ALL SELECT DISTINCT 'k:' || cliente_unico FROM mp_observacion "
            "WHERE propia AND cuenta_canonica_id IS NOT NULL) atomos"
        ),
        {"cabecera": cabecera},
    ).scalar_one()


# --- clasificar: grupos, copias y parejas ---------------------------------------------------------

_COLUMNAS = ["despacho_id", "cartera_id", *(c.columna for c in CAMPOS_FIRMADOS)]
"""Lo que la firma exacta resume: si dos copias difieren en algo de esto, la firma mintio."""


def _distintos(alias: str) -> str:
    """Una expresion por cada campo del contrato, con su nombre si toma mas de un valor en el grupo
    (un vacio cuenta como un valor)."""
    casos = []
    for campo in CAMPOS_FIRMADOS:
        columna = f"{alias}.{campo.columna}"
        casos.append(
            f"CASE WHEN count(DISTINCT {columna}) + max(({columna} IS NULL)::int) > 1 "
            f"THEN '{campo.fuente}' END"
        )
    return f"to_jsonb(array_remove(ARRAY[{', '.join(casos)}], NULL))"


SQL_CLASIFICAR = (
    # Los grupos de la llave historica: cuantas observaciones y cuantas firmas exactas distintas.
    "CREATE TEMP TABLE mp_legado ON COMMIT DROP AS "
    "SELECT firma_legacy, count(*) AS observaciones, count(DISTINCT firma_exacta) AS firmas, "
    "bool_and(propia) AS propia FROM mp_observacion GROUP BY firma_legacy",
    # Un grupo por firma exacta, con su representante: el de menor (SHA-256 del archivo original,
    # fila). No depende del orden de llegada ni de un identificador sorteado.
    "CREATE TEMP TABLE mp_grupo ON COMMIT DROP AS "
    f"SELECT y.*, {sql_uuid_de_digest('y.digest')} AS movimiento_id FROM ("
    f"SELECT x.*, {sql_digest_movimiento(VERSION_MOTOR_PAGOS, 'x.firma_exacta')} AS digest FROM ("
    "SELECT DISTINCT ON (o.firma_exacta) o.firma_exacta, o.firma_legacy, "
    "o.dataset_conformado_id AS rep_dataset, o.source_row AS rep_fila, "
    "o.pago_observado_id AS rep_id, o.cliente_unico, o.fecha_recepcion, o.recuperacion, o.propia, "
    "o.cuenta_canonica_id, count(*) OVER (PARTITION BY o.firma_exacta) AS copias, "
    "l.firmas > 1 AS ambiguo, true AS valido "
    "FROM mp_observacion o JOIN mp_legado l ON l.firma_legacy = o.firma_legacy "
    "ORDER BY o.firma_exacta, o.archivo, o.source_row) x) y",
    "CREATE UNIQUE INDEX ON mp_grupo (firma_exacta)",
    "ANALYZE mp_grupo",
    # Nunca se confia ciegamente en una huella: cada copia se compara campo por campo con su
    # representante. Un grupo con una diferencia no se interpreta.
    "UPDATE mp_grupo g SET valido = false FROM ("
    "SELECT DISTINCT r.firma_exacta FROM mp_grupo r "
    "JOIN mp_observacion o ON o.firma_exacta = r.firma_exacta "
    "JOIN pago_observado m ON m.dataset_conformado_id = o.dataset_conformado_id "
    "AND m.source_row = o.source_row "
    "JOIN pago_observado p ON p.dataset_conformado_id = r.rep_dataset "
    "AND p.source_row = r.rep_fila "
    "WHERE r.copias > 1 AND "
    f"({', '.join('m.' + c for c in _COLUMNAS)}) IS DISTINCT FROM "
    f"({', '.join('p.' + c for c in _COLUMNAS)})) distintas "
    "WHERE g.firma_exacta = distintas.firma_exacta",
    # Que campos difieren en cada grupo ambiguo de la llave historica: el por que de su ambiguedad.
    "CREATE TEMP TABLE mp_diferencia ON COMMIT DROP AS "
    f"SELECT o.firma_legacy, {_distintos('p')} AS campos "
    "FROM mp_observacion o JOIN mp_legado l ON l.firma_legacy = o.firma_legacy AND l.firmas > 1 "
    "JOIN pago_observado p ON p.dataset_conformado_id = o.dataset_conformado_id "
    "AND p.source_row = o.source_row GROUP BY o.firma_legacy",
    # Los candidatos de una pareja de reverso, solo de las llaves (cliente, importe) con algun
    # negativo: un grupo limpio de copias, o todo un grupo ambiguo de la llave historica, que cuenta
    # como un solo candidato que no es limpio.
    "CREATE TEMP TABLE mp_unidad ON COMMIT DROP AS "
    "SELECT CASE WHEN g.ambiguo THEN g.firma_legacy ELSE g.firma_exacta END AS unidad, "
    "g.cliente_unico, abs(g.recuperacion) AS importe, sign(g.recuperacion)::integer AS signo, "
    "min(g.fecha_recepcion) AS fecha, bool_and(g.valido AND NOT g.ambiguo) AS limpia "
    "FROM mp_grupo g WHERE g.recuperacion <> 0 AND (g.cliente_unico, abs(g.recuperacion)) IN ("
    "SELECT n.cliente_unico, -n.recuperacion FROM mp_grupo n WHERE n.recuperacion < 0) "
    "GROUP BY 1, 2, 3, 4",
    # Cada pareja posible: un pago y un negativo del mismo cliente e importe, el negativo hasta 30
    # dias despues.
    "CREATE TEMP TABLE mp_pareja ON COMMIT DROP AS "
    "SELECT p.unidad AS pago, n.unidad AS negativo FROM mp_unidad p "
    "JOIN mp_unidad n ON n.cliente_unico = p.cliente_unico AND n.importe = p.importe "
    "WHERE p.signo > 0 AND n.signo < 0 AND n.fecha >= p.fecha "
    f"AND n.fecha <= p.fecha + interval '{DIAS_DE_VENTANA} days'",
    # Por cada negativo: cuantos originales posibles tiene, si son limpios y cuantos negativos
    # reclaman a su original, cuando es uno.
    "CREATE TEMP TABLE mp_negativo ON COMMIT DROP AS "
    "SELECT n.unidad, n.limpia, count(x.pago) AS candidatos, bool_and(p.limpia) AS limpios, "
    "max(gp.grado) AS grado, (array_agg(x.pago))[1] AS candidato FROM mp_unidad n "
    "LEFT JOIN mp_pareja x ON x.negativo = n.unidad "
    "LEFT JOIN mp_unidad p ON p.unidad = x.pago "
    "LEFT JOIN (SELECT pago, count(*) AS grado FROM mp_pareja GROUP BY pago) gp "
    "ON gp.pago = x.pago "
    "WHERE n.signo < 0 GROUP BY n.unidad, n.limpia",
    # Una pareja aislada, y solo esa, es un reverso con su original.
    "CREATE TEMP TABLE mp_enlace ON COMMIT DROP AS "
    "SELECT n.candidato AS pago, n.unidad AS negativo, op.movimiento_id AS pago_id, "
    "rn.movimiento_id AS negativo_id FROM mp_negativo n "
    "JOIN mp_grupo op ON op.firma_exacta = n.candidato "
    "JOIN mp_grupo rn ON rn.firma_exacta = n.unidad "
    "WHERE n.candidatos = 1 AND n.limpia AND n.limpios AND n.grado = 1",
)


def _clasificar(s: Session) -> None:
    for sentencia in SQL_CLASIFICAR:
        s.execute(text(sentencia))


# --- publicar -------------------------------------------------------------------------------------

SQL_MOVIMIENTOS = """
INSERT INTO movimiento_economico_canonico (observaciones, fecha_recepcion, creado_en,
    ejecucion_motor_pagos_id, cuenta_canonica_id, movimiento_id, movimiento_original_id,
    anulado_por_movimiento_id, monto_reportado, version_motor, despacho_id, cartera_id,
    cliente_unico, signo_economico, tipo_movimiento, estado_conciliacion, firma_exacta)
SELECT g.copias, g.fecha_recepcion, now(), :ejecucion, g.cuenta_canonica_id, g.movimiento_id,
    como_reverso.pago_id, como_pago.negativo_id, g.recuperacion, :version, :despacho, :cartera,
    g.cliente_unico,
    CASE WHEN g.recuperacion > 0 THEN 'SUMA' ELSE 'RESTA' END,
    CASE WHEN g.recuperacion > 0 THEN 'PAGO'
         WHEN como_reverso.pago_id IS NOT NULL THEN 'REVERSO'
         ELSE 'POSIBLE_REVERSO' END,
    CASE WHEN g.cuenta_canonica_id IS NULL THEN 'SIN_CUENTA_OBSERVADA'
         ELSE 'CONCILIADO_CUENTA' END,
    g.firma_exacta
FROM mp_grupo g
LEFT JOIN mp_enlace como_reverso ON como_reverso.negativo = g.firma_exacta
LEFT JOIN mp_enlace como_pago ON como_pago.pago = g.firma_exacta
WHERE g.propia AND NOT g.ambiguo AND g.valido AND g.recuperacion <> 0
ORDER BY g.cliente_unico, g.fecha_recepcion, g.firma_exacta
"""
"""Un movimiento por cada grupo de la ventana que v1 interpreta: limpio, comprobado y con importe.
En orden de cuenta y de recepcion, para que el indice de los movimientos de una cuenta se llene en
orden."""


def _crear_movimientos(s: Session, ejecucion_id: int, ventana: Ventana) -> None:
    s.execute(
        text(SQL_MOVIMIENTOS),
        {
            "ejecucion": ejecucion_id,
            "version": VERSION_MOTOR_PAGOS,
            "despacho": ventana.despacho_id,
            "cartera": ventana.cartera_id,
        },
    )


SQL_RESULTADOS = f"""
INSERT INTO resultado_pago_observado (ejecucion_motor_pagos_id, dataset_conformado_id, source_row,
    movimiento_economico_canonico_id, movimiento_relacionado_id, clasificacion,
    estado_conciliacion, firma_exacta, firma_legacy, motivos)
SELECT :ejecucion, c.dataset_conformado_id, c.source_row, m.id,
    CASE WHEN c.representante THEN coalesce(m.movimiento_original_id, m.anulado_por_movimiento_id)
    END,
    CASE WHEN c.ambiguo THEN '{Clasificacion.COINCIDENCIA_AMBIGUA}'
         WHEN m.id IS NULL THEN '{Clasificacion.NO_CONCILIADO}'
         WHEN NOT c.representante THEN '{Clasificacion.DUPLICADO_EXACTO}'
         WHEN m.tipo_movimiento = 'PAGO' THEN '{Clasificacion.MOVIMIENTO_PRIMARIO}'
         ELSE m.tipo_movimiento END,
    CASE WHEN c.cuenta_canonica_id IS NULL THEN 'SIN_CUENTA_OBSERVADA'
         ELSE 'CONCILIADO_CUENTA' END,
    c.firma_exacta, c.firma_legacy,
    CASE
      WHEN c.ambiguo THEN jsonb_build_array(jsonb_build_object(
          'codigo', 'LLAVE_HISTORICA_COMPARTIDA', 'observaciones', l.observaciones,
          'firmas_exactas', l.firmas, 'campos_distintos', d.campos))
      WHEN NOT c.valido THEN jsonb_build_array(jsonb_build_object(
          'codigo', 'FIRMA_SIN_VALIDAR', 'observaciones', c.copias))
      WHEN c.recuperacion = 0 THEN jsonb_build_array(jsonb_build_object(
          'codigo', 'IMPORTE_CERO', 'observaciones', c.copias))
      WHEN NOT c.representante THEN jsonb_build_array(jsonb_build_object(
          'codigo', 'COPIA_EXACTA', 'representante', c.rep_id, 'observaciones', c.copias))
      WHEN m.tipo_movimiento = 'PAGO' THEN jsonb_build_array(
          CASE WHEN c.copias = 1 THEN jsonb_build_object('codigo', 'OBSERVACION_UNICA')
               ELSE jsonb_build_object('codigo', 'REPRESENTANTE_DE_COPIAS',
                   'copias', c.copias - 1) END)
        || CASE WHEN m.anulado_por_movimiento_id IS NULL THEN '[]'::jsonb
                ELSE jsonb_build_array(jsonb_build_object('codigo', 'ANULADO_POR_REVERSO',
                    'reverso', m.anulado_por_movimiento_id, 'ventana_dias', {DIAS_DE_VENTANA}))
           END
      WHEN m.tipo_movimiento = 'REVERSO' THEN jsonb_build_array(jsonb_build_object(
          'codigo', 'PAREJA_UNICA', 'original', m.movimiento_original_id,
          'ventana_dias', {DIAS_DE_VENTANA}))
      ELSE jsonb_build_array(jsonb_build_object(
          'codigo', CASE WHEN n.candidatos = 0 THEN 'SIN_CANDIDATOS'
                         WHEN n.candidatos > 1 THEN 'VARIOS_CANDIDATOS'
                         WHEN NOT n.limpios THEN 'CANDIDATO_AMBIGUO'
                         ELSE 'ORIGINAL_DISPUTADO' END,
          'candidatos', n.candidatos, 'ventana_dias', {DIAS_DE_VENTANA}))
    END
FROM (
    SELECT o.dataset_conformado_id, o.source_row, o.firma_exacta, o.firma_legacy,
        o.cuenta_canonica_id, g.copias, g.ambiguo, g.valido, g.recuperacion, g.movimiento_id,
        g.rep_id, (o.dataset_conformado_id = g.rep_dataset AND o.source_row = g.rep_fila)
            AS representante
    FROM mp_observacion o JOIN mp_grupo g ON g.firma_exacta = o.firma_exacta
    WHERE o.propia
) c
LEFT JOIN movimiento_economico_canonico m ON m.ejecucion_motor_pagos_id = :ejecucion
    AND m.movimiento_id = c.movimiento_id AND NOT c.ambiguo
LEFT JOIN mp_legado l ON c.ambiguo AND l.firma_legacy = c.firma_legacy
LEFT JOIN mp_diferencia d ON c.ambiguo AND d.firma_legacy = c.firma_legacy
LEFT JOIN mp_negativo n ON n.unidad = c.firma_exacta
ORDER BY c.dataset_conformado_id, c.source_row
"""
"""Un resultado por cada observacion de la ventana, con su clasificacion, su conciliacion, sus dos
huellas, su movimiento y sus motivos. En orden de su llave, que es la de la tabla."""


def _insertar_resultados(s: Session, ejecucion_id: int) -> None:
    s.execute(text(SQL_RESULTADOS), {"ejecucion": ejecucion_id})


def _contar(s: Session, ejecucion_id: int) -> _Conteos:
    clases = s.execute(
        text(
            "SELECT count(*), "
            + ", ".join(
                f"count(*) FILTER (WHERE clasificacion = '{clase}')"
                for clase in (
                    Clasificacion.MOVIMIENTO_PRIMARIO,
                    Clasificacion.DUPLICADO_EXACTO,
                    Clasificacion.COINCIDENCIA_AMBIGUA,
                    Clasificacion.REVERSO,
                    Clasificacion.POSIBLE_REVERSO,
                    Clasificacion.NO_CONCILIADO,
                )
            )
            + ", count(*) FILTER (WHERE estado_conciliacion = 'SIN_CUENTA_OBSERVADA') "
            "FROM resultado_pago_observado WHERE ejecucion_motor_pagos_id = :ejecucion"
        ),
        {"ejecucion": ejecucion_id},
    ).one()
    movimientos = s.execute(
        text(
            "SELECT count(*), count(*) FILTER (WHERE anulado_por_movimiento_id IS NOT NULL), "
            "coalesce(sum(monto_reportado) FILTER (WHERE tipo_movimiento = 'PAGO' "
            "AND anulado_por_movimiento_id IS NULL), 0), "
            "coalesce(sum(monto_reportado) FILTER (WHERE tipo_movimiento = 'POSIBLE_REVERSO'), 0) "
            "FROM movimiento_economico_canonico WHERE ejecucion_motor_pagos_id = :ejecucion"
        ),
        {"ejecucion": ejecucion_id},
    ).one()
    grupos = s.execute(
        text(
            "SELECT (SELECT count(*) FROM mp_grupo WHERE propia AND copias > 1), "
            "count(*) FILTER (WHERE observaciones > 1), count(*) FILTER (WHERE firmas > 1), "
            "coalesce(sum(observaciones) FILTER (WHERE observaciones > 1), 0), "
            "(SELECT coalesce(sum(o.recuperacion), 0) FROM mp_observacion o "
            "JOIN mp_grupo g ON g.firma_exacta = o.firma_exacta WHERE o.propia AND g.ambiguo) "
            "FROM mp_legado WHERE propia"
        )
    ).one()
    (
        clasificadas,
        primarios,
        duplicados,
        ambiguas,
        reversos,
        posibles,
        no_conciliados,
        sin_cuenta,
    ) = clases
    total_movimientos, anulados, bruta, posibles_importe = movimientos
    exactos, legacy, ambiguos, en_legacy, importe_ambiguo = grupos
    return _Conteos(
        observaciones_clasificadas=clasificadas,
        primarios=primarios,
        duplicados_exactos=duplicados,
        coincidencias_ambiguas=ambiguas,
        reversos=reversos,
        posibles_reversos=posibles,
        no_conciliados=no_conciliados,
        sin_cuenta_observada=sin_cuenta,
        movimientos_canonicos=total_movimientos,
        pagos_anulados=anulados,
        grupos_exactos=exactos,
        grupos_legacy=legacy,
        grupos_ambiguos=ambiguos,
        observaciones_en_grupos_legacy=en_legacy,
        recuperacion_bruta_interpretada=bruta,
        recuperacion_neta_interpretada=bruta + posibles_importe,
        importe_ambiguo_observado=importe_ambiguo,
    )


def _cerrar(
    s: Session,
    ejecucion: EjecucionMotorPagos,
    ventana: Ventana,
    firma: str,
    avance: _Avance,
    conteos: _Conteos,
) -> None:
    """EXITOSA, con sus conteos y su firma de entrada, en la transaccion que publico. Antes
    comprueba que lo publicado cuadre con lo leido. Envia el cambio pero no confirma: una carrera
    con otra EXITOSA de las mismas entradas se descubre aqui, todavia dentro de la transaccion."""
    if conteos.observaciones_clasificadas != avance.leidas:
        raise _NoSePublica(
            ResultadoMotorPagos.DATOS_INCONSISTENTES,
            f"Se leyeron {avance.leidas:,} pagos observados de la ventana y se clasificaron "
            f"{conteos.observaciones_clasificadas:,}; no se publico nada.",
        )
    fundados = conteos.primarios + conteos.reversos + conteos.posibles_reversos
    if conteos.movimientos_canonicos != fundados:
        raise _NoSePublica(
            ResultadoMotorPagos.DATOS_INCONSISTENTES,
            f"Se publicaron {conteos.movimientos_canonicos:,} movimientos y los fundan "
            f"{fundados:,} observaciones; no se publico nada.",
        )
    for campo in fields(_Conteos):
        setattr(ejecucion, campo.name, getattr(conteos, campo.name))
    ejecucion.estado = EstadoMotorPagos.EXITOSA
    ejecucion.resultado = ResultadoMotorPagos.INTERPRETACION_PUBLICADA.value
    ejecucion.firma_entrada = firma
    ejecucion.observaciones_leidas = avance.leidas
    ejecucion.observaciones_contexto = avance.contexto
    ejecucion.detalle = _detalle(ventana, avance, conteos)
    ejecucion.terminada_en = ahora()
    s.add(ejecucion)
    s.flush()


def _detalle(ventana: Ventana, avance: _Avance, c: _Conteos) -> str:
    return (
        f"Se interpretaron {avance.leidas:,} pagos observados recibidos desde el "
        f"{ventana.desde.isoformat()} y antes del {ventana.hasta.isoformat()}, con "
        f"{avance.contexto:,} de contexto: {c.movimientos_canonicos:,} movimientos canonicos "
        f"({c.primarios:,} pagos, {c.reversos:,} reversos y {c.posibles_reversos:,} posibles "
        f"reversos), {c.duplicados_exactos:,} duplicados exactos, {c.coincidencias_ambiguas:,} "
        f"coincidencias ambiguas y {c.no_conciliados:,} no conciliados; {c.sin_cuenta_observada:,} "
        f"sin cuenta observada. {VERSION_MOTOR_PAGOS}."
    )


def _fallar(
    s: Session,
    ejecucion_id: int,
    etiqueta: UUID,
    resultado: ResultadoMotorPagos,
    motivo: str,
    avance: _Avance,
) -> None:
    """FALLIDA, en la transaccion que tomo la ejecucion y con su fila todavia bloqueada, solo si
    quien la ejecuta sigue siendo el dueno de su trabajo: si no, levanta TrabajoAjeno sin cambiar
    nada, y la termina el dueno vigente. No borra nada: lo de este intento ya lo revirtio su
    savepoint."""
    try:
        cola.confirmar_dueno(s)
    except cola.TrabajoAjeno:
        s.rollback()
        log.warning("motor de pagos %s: su trabajo ya no es de este worker: %s", etiqueta, motivo)
        raise
    registrado = s.execute(
        update(EjecucionMotorPagos)
        .where(
            EjecucionMotorPagos.id == ejecucion_id,
            EjecucionMotorPagos.estado == EstadoMotorPagos.EN_PROCESO,
        )
        .values(
            estado=EstadoMotorPagos.FALLIDA,
            resultado=resultado.value,
            observaciones_leidas=avance.leidas,
            observaciones_contexto=avance.contexto,
            firma_entrada=avance.firma,
            detalle=motivo,
            terminada_en=ahora(),
        )
        .execution_options(synchronize_session=False)
    ).rowcount
    s.commit()
    if registrado:
        log.warning("motor de pagos %s fallido (%s): %s", etiqueta, resultado.value, motivo)
    else:
        log.warning("motor de pagos %s ya habia terminado; este fallo no la cambia", etiqueta)


def _ya_interpretada(s: Session, ventana: Ventana, firma: str) -> EjecucionMotorPagos | None:
    """La EXITOSA de la ventana que ya publico exactamente estas entradas, si hay una. Es la via
    amable; la garantia es el indice unico de las EXITOSA, que una carrera encuentra al cerrar."""
    return s.exec(
        _de_la_ventana(ventana, EstadoMotorPagos.EXITOSA).where(
            EjecucionMotorPagos.firma_entrada == firma
        )
    ).first()


def _exitosa_con_la_firma(s: Session, ejecucion_id: int) -> EjecucionMotorPagos:
    """La EXITOSA de la misma ventana que gano: la que publico la firma de entrada que esta leyo."""
    perdedora = s.get_one(EjecucionMotorPagos, ejecucion_id)
    return s.exec(
        _de_la_ventana(Ventana.de(perdedora), EstadoMotorPagos.EXITOSA).where(
            EjecucionMotorPagos.firma_entrada == perdedora.firma_entrada
        )
    ).one()


def _de_la_ventana(
    ventana: Ventana, estado: EstadoMotorPagos
) -> SelectOfScalar[EjecucionMotorPagos]:
    """Las ejecuciones de la ventana con motor-pagos/v1 en ese estado. De EN_PROCESO hay a lo mas
    una: lo garantiza un indice unico parcial."""
    return select(EjecucionMotorPagos).where(
        EjecucionMotorPagos.despacho_id == ventana.despacho_id,
        EjecucionMotorPagos.cartera_id == ventana.cartera_id,
        EjecucionMotorPagos.version_motor == VERSION_MOTOR_PAGOS,
        EjecucionMotorPagos.periodo_desde == ventana.desde,
        EjecucionMotorPagos.estado == estado,
    )
