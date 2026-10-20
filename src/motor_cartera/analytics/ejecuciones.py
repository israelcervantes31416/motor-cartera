"""Una ejecucion de analytics/v1: describir una cohorte point-in-time, en PostgreSQL y todo o nada.

El orden:

1. `abrir` resuelve el manifiesto (`analytics.manifiesto`), fija known_at (en OPERATIVO sin
   declararlo, now() de la base, una sola vez), calcula la firma y registra la ejecucion
   EN_PROCESO con sus entradas y su trabajo ANALYTICS, en la transaccion de quien llama. La piden
   POST /analytics/ejecuciones y `motor-cartera analizar-cobranza`.
2. Un worker toma el trabajo. Antes de leer nada espera a que la frontera de known_at se asiente
   (`decision_v2.ejecuciones.esperar_frontera`): nada registrado antes de known_at queda por
   confirmar.
3. `analizar` toma la ejecucion con su fila bloqueada y, en una transaccion y en un savepoint, arma
   la cohorte, lee cada dominio point-in-time, calcula la cobertura de pagos de cada dia, agrega por
   (cuenta, periodo) y por celdas con GROUPING SETS, calcula los controles antes de suprimir nada,
   decide la supresion de las celdas pequenas, y publica con un INSERT ... SELECT cada agregado y
   cada control. Cuenta, firma lo publicado y cierra EXITOSA. Si algo falla, se revierte todo: un
   control que no cuadra deja la ejecucion FALLIDA con sus controles y sin un solo agregado.

Lo que hace segura la entrega repetida es lo de los demas motores: el bloqueo de la ejecucion, sus
estados terminales, la transaccion todo o nada, los indices unicos (una EXITOSA y una EN_PROCESO por
firma) y que solo publica o falla el dueno vigente de su trabajo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session, col, select

from motor_cartera.analytics import cobertura, controles, manifiesto, sql
from motor_cartera.analytics.catalogo import (
    CONJUNTOS,
    METRICAS,
    VERSION_CATALOGO,
    VERSION_MOTOR,
    Conjunto,
    Familia,
    Fuente,
    Universo,
    Vista,
    hash_catalogo,
    universo_de_la_celda,
)
from motor_cartera.analytics.politica import (
    POLITICAS,
    Granularidad,
    Periodo,
    PoliticaAnalytics,
    hash_politica,
    horizonte_dias,
    periodos,
)
from motor_cartera.analytics.supresion import Celda, suprimir, violaciones
from motor_cartera.db.copia import Jsonb, copiar
from motor_cartera.db.modelos import (
    COLUMNA_DE_LA_ENTRADA,
    ControlAnalytics,
    EjecucionAnalytics,
    EntradaAnalytics,
    EstadoAnalytics,
    ResultadoEjecucionAnalytics,
    TipoTrabajo,
    ahora,
    id_de,
)
from motor_cartera.db.sesion import filas_afectadas, insertar_en_savepoint, restriccion, sesion
from motor_cartera.decision_v2.ejecuciones import esperar_frontera
from motor_cartera.geografia.ejecuciones import _traza
from motor_cartera.ingesta.fuente_oficial import Cronometro
from motor_cartera.invariantes import presente
from motor_cartera.orquestacion import cola

log = logging.getLogger(__name__)

INDICE_EXITOSA = "ux_analytics_exitosa"
INDICE_EN_PROCESO = "ux_analytics_en_proceso"

AVISO = (
    "Analytics describe lo observado y lo planificado, agregado: no prueba causalidad, no predice "
    "y no recomienda. Una asociacion de la atribucion es operacional, no causal; una parada "
    "programada no es una visita realizada; un gestor de campo no es el actor_ref de una gestion."
)


class AnalyticsYaGenerada(Exception):
    """Otra ejecucion ya publico exactamente las mismas entradas (la misma firma)."""

    def __init__(self, previa: EjecucionAnalytics) -> None:
        super().__init__(
            f"Esas entradas ya se analizaron: la ejecucion {previa.analytics_run_id} (firma "
            f"{previa.firma_inputs[:12]}...)."
        )
        self.previa = previa


class AnalyticsEnProceso(Exception):
    """Esas mismas entradas ya se estan analizando."""

    def __init__(self, activa: EjecucionAnalytics) -> None:
        super().__init__(
            f"Esas entradas ya se estan analizando: la ejecucion {activa.analytics_run_id}."
        )
        self.activa = activa


class _NoSePublica(Exception):
    def __init__(
        self,
        resultado: ResultadoEjecucionAnalytics,
        mensaje: str,
        controles_: list[controles.Control] | None = None,
    ) -> None:
        super().__init__(mensaje)
        self.resultado = resultado
        self.controles = controles_ or []


# --- abrir ----------------------------------------------------------------------------------------


def abrir(
    s: Session,
    peticion: manifiesto.Peticion,
    *,
    max_intentos: int,
    tomado_por: str | None = None,
    lease_segundos: float | None = None,
) -> EjecucionAnalytics:
    """La ejecucion EN_PROCESO, con sus entradas y su trabajo ANALYTICS, en la transaccion de quien
    llama: envia los INSERT y no confirma. Levanta, sin registrar nada, una
    manifiesto.EntradaNoAnalizable, AnalyticsYaGenerada o AnalyticsEnProceso."""
    r = manifiesto.resolver(s, peticion)
    previa = _de_firma(s, r.firma, EstadoAnalytics.EXITOSA)
    if previa is not None:
        raise AnalyticsYaGenerada(previa)
    activa = _de_firma(s, r.firma, EstadoAnalytics.EN_PROCESO)
    if activa is not None:
        raise AnalyticsEnProceso(activa)
    ejecucion = EjecucionAnalytics(
        corte_canonico_id=id_de(r.corte),
        despacho_id=r.corte.despacho_id,
        cartera_id=r.corte.cartera_id,
        fecha_corte=r.corte.fecha_corte,
        as_of=r.as_of,
        known_at=r.known_at,
        modo=r.modo,
        zona_horaria=r.zona_horaria,
        granularidad=r.granularidad,
        horizonte_dias=horizonte_dias(r.corte.fecha_corte, r.as_of),
        version_motor=VERSION_MOTOR,
        version_catalogo=VERSION_CATALOGO,
        hash_catalogo=hash_catalogo(),
        version_politica=r.politica.version,
        hash_politica=hash_politica(r.politica),
        hash_manifiesto=r.hash_manifiesto,
        firma_inputs=r.firma,
        cuentas_cohorte=r.corte.cuentas,
        detalle=" ".join(r.advertencias) or None,
    )
    error = insertar_en_savepoint(s, ejecucion)
    if error is not None:
        if restriccion(error) != INDICE_EN_PROCESO:
            raise error
        activa = _de_firma(s, r.firma, EstadoAnalytics.EN_PROCESO)
        if (
            activa is None
        ):  # pragma: no cover - la que gano ya termino entre el rechazo y la revision
            raise error
        raise AnalyticsEnProceso(activa) from error
    for e in r.entradas:
        s.add(
            EntradaAnalytics(
                ejecucion_analytics_id=id_de(ejecucion),
                tipo_entrada=e.tipo.value,
                referencia_publica=e.referencia_publica,
                version=e.version,
                firma=e.firma,
                periodo_desde=e.periodo_desde,
                periodo_hasta=e.periodo_hasta,
                disponible_en=e.disponible_en,
                metadatos=e.metadatos,
                **{COLUMNA_DE_LA_ENTRADA[e.tipo.value]: e.id_interno},
            )
        )
    s.flush()
    cola.crear(
        s,
        TipoTrabajo.ANALYTICS,
        id_de(ejecucion),
        max_intentos=max_intentos,
        tomado_por=tomado_por,
        lease_segundos=lease_segundos,
    )
    log.info(
        "analytics %s abierta: corte %s, as_of %s, known_at %s, %s, %d entradas",
        ejecucion.analytics_run_id,
        r.corte.corte_id,
        r.as_of.isoformat(),
        r.known_at.isoformat(),
        r.granularidad,
        len(r.entradas),
    )
    return ejecucion


def _de_firma(s: Session, firma: str, estado: EstadoAnalytics) -> EjecucionAnalytics | None:
    consulta = select(EjecucionAnalytics).where(
        EjecucionAnalytics.firma_inputs == firma, EjecucionAnalytics.estado == estado
    )
    if estado == EstadoAnalytics.EN_PROCESO:
        consulta = consulta.with_for_update()
    return s.exec(consulta).one_or_none()


# --- analizar -------------------------------------------------------------------------------------

ANTES_DE_COHORTE = "ANTES_DE_COHORTE"
DESPUES_DE_COHORTE = "DESPUES_DE_COHORTE"
DESPUES_DE_PAGOS = "DESPUES_DE_PAGOS"
DESPUES_DE_LIFECYCLE = "DESPUES_DE_LIFECYCLE"
DESPUES_DE_AGREGADOS = "DESPUES_DE_AGREGADOS"
DESPUES_DE_CONTROLES = "DESPUES_DE_CONTROLES"
ANTES_DE_PUBLICAR = "ANTES_DE_PUBLICAR"
ANTES_DE_CERRAR = "ANTES_DE_CERRAR"
PUNTOS_DE_FALLA = (
    ANTES_DE_COHORTE,
    DESPUES_DE_COHORTE,
    DESPUES_DE_PAGOS,
    DESPUES_DE_LIFECYCLE,
    DESPUES_DE_AGREGADOS,
    DESPUES_DE_CONTROLES,
    ANTES_DE_PUBLICAR,
    ANTES_DE_CERRAR,
)


def _punto_de_falla(nombre: str) -> None:
    """Un punto donde una prueba puede inyectar una falla. En produccion no hace nada."""


def ejecutar_analytics(ejecucion_id: int) -> None:
    """Lo que ejecuta un trabajo ANALYTICS. Si la ejecucion ya termino no hace nada; si no, espera
    a que su frontera de conocimiento se asiente y analiza."""
    with sesion() as s:
        ejecucion = s.get_one(EjecucionAnalytics, ejecucion_id)
        if ejecucion.estado != EstadoAnalytics.EN_PROCESO:
            log.info("analytics %s ya termino %s", ejecucion.analytics_run_id, ejecucion.estado)
            return
        known_at = ejecucion.known_at
    esperar_frontera(known_at)
    analizar(ejecucion_id)


def analizar(ejecucion_id: int, *, cronometro: Cronometro | None = None) -> None:
    """Analiza la ejecucion y la deja en un estado terminal: con su fila bloqueada hasta el commit,
    todo en una transaccion y en un savepoint; si falla, queda FALLIDA sin soltar su fila, y solo
    publica o falla el dueno vigente de su trabajo. Levanta AnalyticsYaGenerada si otra publico
    primero la misma firma, TrabajoAjeno, y los errores transitorios de la base, con la ejecucion
    EN_PROCESO para que la cola la reintente.

    No espera la frontera: eso lo hace `ejecutar_analytics`. Quien llame directamente tiene que
    saber que known_at ya paso y que nada quedo abierto antes de el."""
    cronometro = cronometro or Cronometro()
    with sesion() as s:
        ejecucion = s.exec(
            select(EjecucionAnalytics)
            .where(EjecucionAnalytics.id == ejecucion_id)
            .with_for_update()
        ).one()
        if ejecucion.estado != EstadoAnalytics.EN_PROCESO:
            log.warning("analytics %s ya termino %s", ejecucion.analytics_run_id, ejecucion.estado)
            return
        etiqueta, firma = ejecucion.analytics_run_id, ejecucion.firma_inputs
        fallidos: list[controles.Control] = []
        try:
            with s.begin_nested():
                _analizar(s, ejecucion, cronometro)
        except _NoSePublica as exc:
            resultado, motivo, fallidos = exc.resultado, str(exc), exc.controles
        except IntegrityError as exc:
            if restriccion(exc) == INDICE_EXITOSA:
                resultado, motivo = (
                    ResultadoEjecucionAnalytics.YA_GENERADA,
                    "Otra ejecucion publico la misma firma de entrada mientras esta se procesaba; "
                    "no se publica dos veces.",
                )
            else:
                log.error(
                    "analytics %s: violacion de integridad inesperada%s", etiqueta, _traza(exc)
                )
                resultado, motivo = (
                    ResultadoEjecucionAnalytics.ERROR_INTERNO,
                    "Error interno al publicar; ver la bitacora del servicio.",
                )
        except OperationalError:
            log.warning("analytics %s: error transitorio; sigue EN_PROCESO", etiqueta)
            raise
        except Exception as exc:
            # Sin el mensaje: el de un error de la base puede citar una fila de las tablas de paso.
            log.error("analytics %s: error inesperado%s", etiqueta, _traza(exc))
            resultado, motivo = (
                ResultadoEjecucionAnalytics.ERROR_INTERNO,
                f"Error interno ({type(exc).__name__}); ver la bitacora.",
            )
        else:
            cola.confirmar_dueno(s)
            with cronometro.fase("commit"):
                s.commit()
            return
        _fallar(s, ejecucion_id, etiqueta, resultado, motivo, fallidos)
        if resultado == ResultadoEjecucionAnalytics.YA_GENERADA:
            previa = _de_firma(s, firma, EstadoAnalytics.EXITOSA)
            raise AnalyticsYaGenerada(presente(previa, "la ejecucion EXITOSA con esa firma"))


@dataclass
class _Corrida:
    """Lo que el motor fija al empezar y usa en cada paso."""

    ejecucion: EjecucionAnalytics
    politica: PoliticaAnalytics
    declaradas: frozenset[Fuente]
    ids: dict[Fuente, int]
    meses_interpretados: set[date]
    meses_atribuidos: set[date]
    periodos: list[Periodo]
    parametros: dict[str, Any] = field(default_factory=dict)
    meses: list[cobertura.Mes] = field(default_factory=list)
    archivos: int = 0

    @property
    def granularidad(self) -> Granularidad:
        return Granularidad(self.ejecucion.granularidad)


def _analizar(s: Session, ejecucion: EjecucionAnalytics, cronometro: Cronometro) -> None:
    politica = POLITICAS.get(ejecucion.version_politica)
    if (
        ejecucion.version_motor != VERSION_MOTOR
        or ejecucion.version_catalogo != VERSION_CATALOGO
        or ejecucion.hash_catalogo != hash_catalogo()
        or politica is None
        or ejecucion.hash_politica != hash_politica(politica)
    ):
        raise _NoSePublica(
            ResultadoEjecucionAnalytics.VERSION_NO_SOPORTADA,
            f"La ejecucion pide {ejecucion.version_motor} con {ejecucion.version_catalogo} (hash "
            f"{ejecucion.hash_catalogo[:12]}...) y {ejecucion.version_politica} (hash "
            f"{ejecucion.hash_politica[:12]}...), y este servicio analiza con {VERSION_MOTOR}, "
            f"{VERSION_CATALOGO} y {', '.join(sorted(POLITICAS))}; no se publico nada.",
        )
    corrida = _entradas(s, ejecucion, politica)
    _punto_de_falla(ANTES_DE_COHORTE)
    p = corrida.parametros
    with cronometro.fase("cohorte"):
        _ejecutar(s, sql.sql_cohorte(), p)
        cuentas, sin_tramo, sin_decision, sin_territorial, sin_geografia = s.execute(
            text(sql.SQL_REVISAR_COHORTE), p
        ).one()
    if sin_tramo or sin_decision or sin_territorial or sin_geografia:
        raise _NoSePublica(
            ResultadoEjecucionAnalytics.DATOS_INCONSISTENTES,
            f"De {cuentas:,} cuentas de la cohorte, {sin_tramo:,} no caen en ningun tramo de "
            f"atraso, {sin_decision:,} no tienen decision, {sin_territorial:,} no tienen "
            f"asignacion territorial y {sin_geografia:,} no tienen geografia en las ejecuciones "
            "declaradas; no se publico nada.",
        )
    _punto_de_falla(DESPUES_DE_COHORTE)
    with cronometro.fase("pagos"):
        _ejecutar(s, sql.sql_pagos(corrida.granularidad), p)
        dias = _cobertura(s, corrida)
    _punto_de_falla(DESPUES_DE_PAGOS)
    with cronometro.fase("lifecycle"):
        _ejecutar(s, sql.sql_gestiones(corrida.granularidad), p)
    with cronometro.fase("promesas"):
        _ejecutar(s, sql.sql_promesas(corrida.granularidad), p)
    with cronometro.fase("atribucion"):
        _ejecutar(s, sql.SQL_ATRIBUCION, p)
    _punto_de_falla(DESPUES_DE_LIFECYCLE)
    with cronometro.fase("operacion"):
        if Fuente.CAMPO_V2 in corrida.declaradas:
            _ejecutar(s, sql.SQL_CAMPO, p)
        if Fuente.RUTEO_V2 in corrida.declaradas:
            _ejecutar(s, sql.SQL_RUTEO, p)
    with cronometro.fase("hechos"):
        _ejecutar(s, sql.SQL_HECHOS, p)
    with cronometro.fase("agrupacion"):
        _agrupar(s, corrida)
    _punto_de_falla(DESPUES_DE_AGREGADOS)
    with cronometro.fase("controles"):
        calculados = controles.calcular(
            s,
            controles.Contexto(
                cuentas_cohorte=ejecucion.cuentas_cohorte,
                declaradas=corrida.declaradas,
                ids=corrida.ids,
                periodos=bool(corrida.periodos),
                parametros=p,
            ),
        )
    _punto_de_falla(DESPUES_DE_CONTROLES)
    _si_fallo(calculados)
    with cronometro.fase("supresion"):
        celdas, suprimidas = _suprimir(s, corrida)
        aplica = _aplica(s, corrida, dias)
    _punto_de_falla(ANTES_DE_PUBLICAR)
    with cronometro.fase("publicacion"):
        publicados = filas_afectadas(
            s.execute(
                text(
                    sql.sql_publicacion(
                        politica.escala_razon,
                        politica.min_group_size,
                        politica.conjuntos_en_periodo,
                    )
                ),
                p,
            )
        )
        sin_periodo = [m.codigo for m in METRICAS if Vista.PERIODO not in m.vistas]
        if corrida.periodos:
            publicados += filas_afectadas(
                s.execute(text(sql.SQL_NO_APLICA), {**p, "sin_periodo": sin_periodo})
            )
        esperados = _esperados(s, aplica, len(corrida.periodos), len(sin_periodo))
        calculados += [
            controles.Control(
                "publicacion.filas",
                Decimal(esperados),
                Decimal(publicados),
                "las filas publicadas contra las metricas aplicables de cada celda y periodo",
            ),
            controles.Control(
                "privacidad.supresion",
                Decimal(0),
                Decimal(violaciones(celdas, suprimidas, politica.min_group_size)),
                "celdas visibles con menos cuentas que el minimo, o grupos que dejan reconstruir "
                "una celda suprimida",
            ),
        ]
        _si_fallo(calculados)
        copiar(
            s,
            "control_analytics",
            (
                "ejecucion_analytics_id",
                "codigo_control",
                "estado",
                "esperado",
                "observado",
                "diferencia",
                "detalle",
            ),
            _filas_de_control(id_de(ejecucion), calculados),
        )
        firma = s.execute(text(sql.SQL_HASH_AGREGADOS), p).scalar_one()
    _punto_de_falla(ANTES_DE_CERRAR)
    _cerrar(s, ejecucion, corrida, dias, publicados, calculados, firma, len(suprimidas))


def _ejecutar(s: Session, sentencias: tuple[str, ...], parametros: dict[str, Any]) -> None:
    for sentencia in sentencias:
        s.execute(text(sentencia), parametros)


def _si_fallo(calculados: list[controles.Control]) -> None:
    fallidos = [c for c in calculados if not c.ok]
    if fallidos:
        raise _NoSePublica(
            ResultadoEjecucionAnalytics.CONTROL_FALLIDO,
            f"{len(fallidos)} de {len(calculados)} controles no cuadran: "
            + ", ".join(
                f"{c.codigo} (esperado {c.esperado}, observado {c.observado})" for c in fallidos[:5]
            )
            + ("..." if len(fallidos) > 5 else "")
            + "; no se publico ningun agregado.",
            calculados,
        )


def _entradas(s: Session, ejecucion: EjecucionAnalytics, politica: PoliticaAnalytics) -> _Corrida:
    """Las entradas declaradas, comprobadas otra vez: siguen EXITOSA (o PUBLICADA, el corte no
    cambia). Y los parametros de cada sentencia, con los limites del horizonte en la zona."""
    filas = s.exec(
        select(EntradaAnalytics).where(EntradaAnalytics.ejecucion_analytics_id == ejecucion.id)
    ).all()
    declaradas = frozenset(Fuente(f.tipo_entrada) for f in filas)
    ids: dict[Fuente, int] = {}
    interpretados, atribuidos = set(), set()
    for f in filas:
        tipo = Fuente(f.tipo_entrada)
        interno = getattr(f, COLUMNA_DE_LA_ENTRADA[f.tipo_entrada])
        if tipo == Fuente.MOTOR_PAGOS:
            interpretados.add(f.periodo_desde)
        elif tipo == Fuente.ATRIBUCION:
            atribuidos.add(f.periodo_desde)
        else:
            ids[tipo] = interno
    no_exitosas = s.execute(
        text(
            "SELECT count(*) FROM entrada_analytics e WHERE e.ejecucion_analytics_id = :e AND ("
            "(e.tipo_entrada = 'MOTOR_PAGOS' AND (SELECT estado FROM ejecucion_motor_pagos "
            "WHERE id = e.ejecucion_motor_pagos_id) <> 'EXITOSA') OR "
            "(e.tipo_entrada = 'ATRIBUCION' AND (SELECT estado FROM ejecucion_atribucion "
            "WHERE id = e.ejecucion_atribucion_id) <> 'EXITOSA') OR "
            "(e.tipo_entrada = 'EVALUACION_PROMESAS' AND (SELECT estado FROM "
            "ejecucion_evaluacion_promesas WHERE id = e.ejecucion_evaluacion_promesas_id) "
            "<> 'EXITOSA') OR "
            "(e.tipo_entrada = 'DECISION_V2' AND (SELECT estado FROM ejecucion_decision_v2 "
            "WHERE id = e.ejecucion_decision_v2_id) <> 'EXITOSA') OR "
            "(e.tipo_entrada = 'GEOGRAFIA' AND (SELECT estado FROM ejecucion_geografia "
            "WHERE id = e.ejecucion_geografia_id) <> 'EXITOSA') OR "
            "(e.tipo_entrada = 'TERRITORIAL_V2' AND (SELECT estado FROM ejecucion_territorial_v2 "
            "WHERE id = e.ejecucion_territorial_v2_id) <> 'EXITOSA') OR "
            "(e.tipo_entrada = 'CAMPO_V2' AND (SELECT estado FROM ejecucion_campo_v2 "
            "WHERE id = e.ejecucion_campo_v2_id) <> 'EXITOSA') OR "
            "(e.tipo_entrada = 'RUTEO_V2' AND (SELECT estado FROM ejecucion_ruteo_v2 "
            "WHERE id = e.ejecucion_ruteo_v2_id) <> 'EXITOSA'))"
        ),
        {"e": ejecucion.id},
    ).scalar_one()
    if no_exitosas:
        raise _NoSePublica(
            ResultadoEjecucionAnalytics.ENTRADAS_NO_EXITOSAS,
            f"{no_exitosas} entradas declaradas ya no estan EXITOSA; no se publico nada.",
        )
    fin_local = datetime.combine(ejecucion.as_of, time()) + timedelta(days=1)
    inicio_local = datetime.combine(ejecucion.fecha_corte, time())
    inicio, fin = s.execute(
        text(
            "SELECT timezone(:zona, CAST(:i AS timestamp)), timezone(:zona, CAST(:f AS timestamp))"
        ),
        {"zona": ejecucion.zona_horaria, "i": inicio_local, "f": fin_local},
    ).one()
    parametros = {
        "ejecucion": ejecucion.id,
        "corte": ejecucion.corte_canonico_id,
        "despacho": ejecucion.despacho_id,
        "cartera": ejecucion.cartera_id,
        "fecha_corte": ejecucion.fecha_corte,
        "as_of": ejecucion.as_of,
        "known_at": ejecucion.known_at,
        "zona": ejecucion.zona_horaria,
        "inicio": inicio,
        "fin": fin,
        "inicio_local": inicio_local,
        "fin_local": fin_local,
        "cuentas_cohorte": ejecucion.cuentas_cohorte,
        **{
            nombre: ids.get(fuente)
            for nombre, fuente in (
                ("decision", Fuente.DECISION_V2),
                ("geografia", Fuente.GEOGRAFIA),
                ("territorial", Fuente.TERRITORIAL_V2),
                ("evaluacion", Fuente.EVALUACION_PROMESAS),
                ("campo", Fuente.CAMPO_V2),
                ("ruteo", Fuente.RUTEO_V2),
            )
        },
    }
    return _Corrida(
        ejecucion=ejecucion,
        politica=politica,
        declaradas=declaradas,
        ids=ids,
        meses_interpretados=interpretados,
        meses_atribuidos=atribuidos,
        periodos=periodos(
            ejecucion.fecha_corte, ejecucion.as_of, Granularidad(ejecucion.granularidad)
        ),
        parametros=parametros,
    )


def _cobertura(s: Session, corrida: _Corrida) -> dict[date, cobertura.Dia]:
    """La cobertura de pagos de cada dia del horizonte: los archivos conocidos a known_at y si la
    interpretacion declarada de cada mes clasifico sus pagos observados."""
    e = corrida.ejecucion
    p = corrida.parametros
    declarados = Fuente.MOTOR_PAGOS in corrida.declaradas
    alcances, meses = [], []
    if declarados:
        _ejecutar(s, sql.SQL_DATASETS_DE_PAGOS, p)
        alcances = [(a, b) for _, a, b in s.execute(text(sql.SQL_ALCANCE_DE_LOS_ARCHIVOS)).all()]
        primer = date(e.fecha_corte.year, e.fecha_corte.month, 1)
        ultimo = date(e.as_of.year, e.as_of.month, 1)
        fin = date(ultimo.year + ultimo.month // 12, ultimo.month % 12 + 1, 1)
        meses = [
            cobertura.Mes(mes, observados, clasificados)
            for mes, observados, clasificados in s.execute(
                text(sql.SQL_MESES_INTERPRETADOS),
                {
                    **p,
                    "primer_mes": datetime.combine(primer, time()),
                    "fin_de_los_meses": datetime.combine(fin, time()),
                },
            ).all()
        ]
    corrida.meses = meses
    corrida.archivos = len(alcances)
    return cobertura.por_dia(
        e.fecha_corte,
        e.as_of,
        pagos_declarados=declarados,
        alcances=alcances,
        meses=meses,
        interpretados=corrida.meses_interpretados,
        atribuidos=corrida.meses_atribuidos,
    )


# --- agrupar --------------------------------------------------------------------------------------


def _disponible(conjunto: Conjunto, universo: Universo, declaradas: frozenset[Fuente]) -> bool:
    fuentes = CONJUNTOS[conjunto].universos.get(universo)
    return fuentes is not None and set(fuentes) <= declaradas


def _conjuntos(universo: Universo, declaradas: frozenset[Fuente]) -> tuple[Conjunto, ...]:
    """Los conjuntos del universo que usa alguna metrica y se pueden formar con lo declarado."""
    usados = {
        c for m in METRICAS for c in m.conjuntos if universo_de_la_celda(m.universo, c) == universo
    }
    return tuple(c for c in Conjunto if c in usados and _disponible(c, universo, declaradas))


def _agrupar(s: Session, corrida: _Corrida) -> None:
    p = corrida.parametros
    declaradas = corrida.declaradas
    en_periodo = set(corrida.politica.conjuntos_en_periodo)
    _ejecutar(s, sql.sql_tablas_de_celdas(), p)
    cohorte = sql.Agrupacion(
        Universo.COHORTE, _conjuntos(Universo.COHORTE, declaradas), sql.COLUMNAS_COHORTE
    )
    s.execute(
        text(
            sql.sql_agrupar(
                cohorte,
                "an_cohorte c LEFT JOIN an_ac a ON a.cuenta = c.cuenta",
                sql.componentes_cohorte("a"),
                vista=Vista.ACUMULADA.value,
                periodo_col=None,
                poblacion="count(*)",
            )
        ),
        p,
    )
    gestion = sql.Agrupacion(
        Universo.GESTION, _conjuntos(Universo.GESTION, declaradas), sql.COLUMNAS_GESTION
    )
    s.execute(
        text(
            sql.sql_agrupar(
                gestion,
                "an_gestion g",
                sql.COMPONENTES_GESTION,
                vista=Vista.ACUMULADA.value,
                periodo_col=None,
                poblacion="count(DISTINCT g.cuenta)",
            )
        ),
        p,
    )
    if corrida.periodos:
        cohorte_periodo = sql.Agrupacion(
            Universo.COHORTE,
            tuple(c for c in cohorte.conjuntos if c in en_periodo),
            sql.COLUMNAS_COHORTE,
        )
        s.execute(
            text(
                sql.sql_agrupar(
                    cohorte_periodo,
                    "an_pc pc JOIN an_cohorte c ON c.cuenta = pc.cuenta",
                    sql.componentes_cohorte("pc"),
                    vista=Vista.PERIODO.value,
                    periodo_col="pc.periodo",
                    poblacion="NULL",
                )
            ),
            p,
        )
        gestion_periodo = sql.Agrupacion(
            Universo.GESTION,
            tuple(c for c in gestion.conjuntos if c in en_periodo),
            sql.COLUMNAS_GESTION,
        )
        if gestion_periodo.conjuntos:
            s.execute(
                text(
                    sql.sql_agrupar(
                        gestion_periodo,
                        "an_gestion g",
                        sql.COMPONENTES_GESTION,
                        vista=Vista.PERIODO.value,
                        periodo_col="g.periodo",
                        poblacion="NULL",
                    )
                ),
                p,
            )
    _ejecutar(s, sql.sql_calidad(corrida.granularidad), p)
    _operacion(
        s,
        p,
        Universo.CAMPO,
        Fuente.CAMPO_V2 in declaradas,
        ("an_campo a", sql.COLUMNAS_CAMPO, sql.COMPONENTES_CAMPO),
        ("an_carga k", sql.COLUMNAS_CARGA, sql.COMPONENTES_CARGA),
        (Conjunto.TOTAL, Conjunto.BASE, Conjunto.GESTOR_CAMPO),
    )
    _operacion(
        s,
        p,
        Universo.RUTEO,
        Fuente.RUTEO_V2 in declaradas,
        ("an_parada p", sql.COLUMNAS_PARADA, sql.COMPONENTES_PARADA),
        ("an_ruta r", sql.COLUMNAS_RUTA, sql.COMPONENTES_RUTA),
        (Conjunto.TOTAL, Conjunto.BASE, Conjunto.GESTOR_CAMPO),
    )
    _ejecutar(s, sql.SQL_CELDAS, p)


def _operacion(
    s: Session,
    p: dict[str, Any],
    universo: Universo,
    declarada: bool,
    principal: tuple[str, dict[str, str], dict[str, str]],
    secundaria: tuple[str, dict[str, str], dict[str, str]],
    conjuntos_secundarios: tuple[Conjunto, ...],
) -> None:
    """Las celdas de campo o de ruteo: las de las cuentas (candidatas o paradas) unidas con las de
    los gestores (su capacidad) o las de las rutas. Sin la entrada declarada, solo el TOTAL, para
    publicar sus metricas NO_DISPONIBLE."""
    if not declarada:
        s.execute(
            text(
                "INSERT INTO an_valor (universo, conjunto, vista, periodo_desde, dimension_hash, "
                "dimensiones, poblacion) VALUES (:u, 'TOTAL', 'ACUMULADA', "
                "CAST(:fecha_corte AS date), sha256(convert_to('{}', 'UTF8')), "
                "CAST('{}' AS jsonb), NULL)"
            ),
            {**p, "u": universo.value},
        )
        return
    nombre = universo.value.lower()
    fuente, columnas, componentes = principal
    fuente_2, columnas_2, componentes_2 = secundaria
    _ejecutar(
        s,
        (
            sql.sql_tabla_parcial(f"an_x_{nombre}", componentes),
            sql.sql_tabla_parcial(f"an_y_{nombre}", componentes_2),
        ),
        p,
    )
    todos = _conjuntos(universo, frozenset(Fuente))
    s.execute(
        text(
            sql.sql_agrupar(
                sql.Agrupacion(universo, todos, columnas),
                fuente,
                componentes,
                vista=Vista.ACUMULADA.value,
                periodo_col=None,
                poblacion="count(*)",
                tabla=f"an_x_{nombre}",
            )
        ),
        p,
    )
    s.execute(
        text(
            sql.sql_agrupar(
                sql.Agrupacion(universo, conjuntos_secundarios, columnas_2),
                fuente_2,
                componentes_2,
                vista=Vista.ACUMULADA.value,
                periodo_col=None,
                poblacion="NULL",
                tabla=f"an_y_{nombre}",
            )
        ),
        p,
    )
    s.execute(
        text(
            sql.sql_unir(
                universo,
                f"an_x_{nombre}",
                componentes,
                f"an_y_{nombre}",
                componentes_2,
                "coalesce(x.poblacion, 0)",
            )
        ),
        p,
    )


# --- la supresion y lo que se publica -------------------------------------------------------------


def _suprimir(s: Session, corrida: _Corrida) -> tuple[list[Celda], dict]:
    """Las celdas de los desgloses, con su poblacion, y la supresion de analytics-public-v1. Son
    pocas (las celdas, no las cuentas): se deciden en Python y vuelven por COPY."""
    celdas = [
        Celda(
            universo,
            conjunto,
            tuple((clave, dimensiones[clave]) for clave in CONJUNTOS[Conjunto(conjunto)].claves),
            bytes(dimension_hash).hex(),
            int(poblacion or 0),
        )
        for universo, conjunto, dimensiones, dimension_hash, poblacion in s.execute(
            text(
                "SELECT universo, conjunto, dimensiones, dimension_hash, poblacion FROM an_celda "
                "ORDER BY universo, conjunto, dimension_hash"
            )
        ).all()
    ]
    suprimidas = suprimir(celdas, corrida.politica.min_group_size)
    _ejecutar(s, sql.SQL_TABLAS_DE_PUBLICACION, corrida.parametros)
    copiar(
        s,
        "an_suprimida",
        ("universo", "conjunto", "dimension_hash", "motivo"),
        (
            (universo, conjunto, bytes.fromhex(dimension_hash), motivo)
            for (universo, conjunto, dimension_hash), motivo in sorted(suprimidas.items())
        ),
    )
    return celdas, suprimidas


def _aplica(s: Session, corrida: _Corrida, dias: dict[date, cobertura.Dia]) -> list[tuple]:
    """Lo que se publica: los periodos de cada vista con su cobertura, y cada metrica en cada
    conjunto y vista en que aplica, con si esta disponible por sus entradas."""
    e = corrida.ejecucion
    horizonte = Periodo(e.fecha_corte, e.as_of, False)
    filas_periodo = []
    for vista, lista in ((Vista.ACUMULADA, [horizonte]), (Vista.PERIODO, corrida.periodos)):
        for periodo in lista:
            pagos = cobertura.motivos(dias, periodo, "pagos")
            atribucion = cobertura.motivos(dias, periodo, "atribucion")
            filas_periodo.append(
                (
                    vista.value,
                    periodo.desde,
                    periodo.hasta,
                    periodo.parcial,
                    not pagos,
                    not atribucion,
                    Jsonb(pagos),
                    Jsonb(atribucion),
                )
            )
    copiar(
        s,
        "an_periodo",
        (
            "vista",
            "periodo_desde",
            "periodo_hasta",
            "parcial",
            "pagos_ok",
            "atribucion_ok",
            "motivo_pagos",
            "motivo_atribucion",
        ),
        filas_periodo,
    )
    en_periodo = set(corrida.politica.conjuntos_en_periodo)
    aplica = []
    for m in METRICAS:
        faltan = sorted(f.value for f in m.fuentes if f not in corrida.declaradas)
        for conjunto in m.conjuntos:
            universo = universo_de_la_celda(m.universo, conjunto)
            if not _disponible(conjunto, universo, corrida.declaradas):
                continue
            for vista in m.vistas:
                if vista == Vista.PERIODO and (not corrida.periodos or conjunto not in en_periodo):
                    continue
                aplica.append(
                    (
                        m.codigo,
                        universo.value,
                        conjunto.value,
                        vista.value,
                        not faltan,
                        m.cobertura_de_pagos,
                        Fuente.ATRIBUCION in m.fuentes,
                        m.familia != Familia.CALIDAD_PAGOS,
                        Jsonb(
                            [{"codigo": cobertura.ENTRADA_NO_DECLARADA, "entradas": faltan}]
                            if faltan
                            else []
                        ),
                    )
                )
    copiar(
        s,
        "an_aplica",
        (
            "metrica",
            "universo",
            "conjunto",
            "vista",
            "disponible",
            "cobertura_pagos",
            "atribucion",
            "con_poblacion",
            "motivo",
        ),
        aplica,
    )
    _ejecutar(s, ("ANALYZE an_periodo", "ANALYZE an_aplica", "ANALYZE an_suprimida"), {})
    return aplica


def _esperados(s: Session, aplica: list[tuple], periodos_: int, sin_periodo: int) -> int:
    """Cuantas filas tiene que publicar: cada metrica aplicable por cada celda de su conjunto y
    cada periodo de su vista, mas las NO_APLICA."""
    celdas = {
        (universo, conjunto): cuantas
        for universo, conjunto, cuantas in s.execute(
            text("SELECT universo, conjunto, count(*) FROM an_celda GROUP BY 1, 2")
        ).all()
    }
    total = 0
    for _metrica, universo, conjunto, vista, *_ in aplica:
        total += celdas.get((universo, conjunto), 0) * (
            1 if vista == Vista.ACUMULADA.value else periodos_
        )
    return total + sin_periodo * periodos_


def _filas_de_control(ejecucion_id: int, calculados: list[controles.Control]):
    for c in calculados:
        yield (
            ejecucion_id,
            c.codigo,
            "OK" if c.ok else "FALLO",
            c.esperado,
            c.observado,
            c.observado - c.esperado,
            c.detalle[:500],
        )


# --- cerrar ---------------------------------------------------------------------------------------


def _disponibilidad(
    corrida: _Corrida, dias: dict[date, cobertura.Dia], suprimidas: int
) -> dict[str, Any]:
    declaradas = corrida.declaradas
    familias = {}
    for familia in Familia:
        metricas = [m for m in METRICAS if m.familia == familia]
        faltan = sorted({f.value for m in metricas for f in m.fuentes if f not in declaradas})
        if faltan:
            familias[familia.value] = {"estado": "NO_DISPONIBLE", "faltan": faltan}
        elif any(m.cobertura_de_pagos for m in metricas):
            clave = "atribucion" if familia == Familia.ATRIBUCION else "pagos"
            sin = sum(1 for d in dias.values() if getattr(d, clave) is not None)
            estado = "DISPONIBLE" if sin == 0 else "PARCIAL"
            if sin == len(dias):
                estado = "NO_DISPONIBLE"
            familias[familia.value] = {"estado": estado, "dias_sin_cobertura": sin}
        else:
            familias[familia.value] = {"estado": "DISPONIBLE"}
    conjuntos = {
        u.value: sorted(
            c.value
            for c in Conjunto
            if u in CONJUNTOS[c].universos and not _disponible(c, u, declaradas)
        )
        for u in Universo
    }
    return {
        "entradas": sorted(f.value for f in declaradas),
        "familias": familias,
        "conjuntos_no_disponibles": conjuntos,
        "cobertura_pagos": {
            "dias": len(dias),
            "dias_con_cobertura": sum(1 for d in dias.values() if d.pagos is None),
            "archivos": corrida.archivos,
            "meses": [
                {
                    "mes": m.mes.isoformat(),
                    "pagos_observados": m.observados,
                    "clasificados": m.clasificados,
                    "interpretado": m.mes in corrida.meses_interpretados,
                    "atribuido": m.mes in corrida.meses_atribuidos,
                }
                for m in corrida.meses
            ],
        },
        "celdas_suprimidas": suprimidas,
        "periodos": len(corrida.periodos),
    }


def _cerrar(
    s: Session,
    ejecucion: EjecucionAnalytics,
    corrida: _Corrida,
    dias: dict[date, cobertura.Dia],
    publicados: int,
    calculados: list[controles.Control],
    firma: str,
    suprimidas: int,
) -> None:
    """EXITOSA, con sus conteos, su disponibilidad y el hash de lo publicado, en la transaccion que
    publico. Envia el cambio pero no confirma."""
    advertencias = ejecucion.detalle
    ejecucion.estado = EstadoAnalytics.EXITOSA
    ejecucion.resultado = ResultadoEjecucionAnalytics.ANALYTICS_PUBLICADA.value
    ejecucion.agregados_publicados = publicados
    ejecucion.controles_totales = len(calculados)
    ejecucion.controles_fallidos = 0
    ejecucion.hash_agregados = firma
    ejecucion.disponibilidad = _disponibilidad(corrida, dias, suprimidas)
    ejecucion.detalle = (
        f"Se analizo la cohorte de {ejecucion.cuentas_cohorte:,} cuentas del corte del "
        f"{ejecucion.fecha_corte.isoformat()} hasta el {ejecucion.as_of.isoformat()} "
        f"({ejecucion.horizonte_dias} dias, {ejecucion.granularidad}), con lo que se conocia hasta "
        f"{ejecucion.known_at.isoformat()}: {publicados:,} agregados ({suprimidas:,} celdas "
        f"suprimidas) y {len(calculados)} controles, todos OK. {AVISO} {VERSION_MOTOR}."
        + (f" {advertencias}" if advertencias else "")
    )
    ejecucion.terminada_en = ahora()
    s.add(ejecucion)
    s.flush()


def _fallar(
    s: Session,
    ejecucion_id: int,
    etiqueta: UUID,
    resultado: ResultadoEjecucionAnalytics,
    motivo: str,
    calculados: list[controles.Control],
) -> None:
    """FALLIDA, con su fila todavia bloqueada, solo si quien la ejecuta sigue siendo el dueno de su
    trabajo; si no, TrabajoAjeno sin cambiar nada. No publica ningun agregado: lo de este intento ya
    lo quito el rollback del savepoint. Si fallo un control, se guardan los controles."""
    try:
        cola.confirmar_dueno(s)
    except cola.TrabajoAjeno:
        s.rollback()
        log.warning("analytics %s: su trabajo ya no es de este worker: %s", etiqueta, motivo)
        raise
    registrado = filas_afectadas(
        s.execute(
            update(EjecucionAnalytics)
            .where(
                col(EjecucionAnalytics.id) == ejecucion_id,
                col(EjecucionAnalytics.estado) == EstadoAnalytics.EN_PROCESO,
            )
            .values(
                estado=EstadoAnalytics.FALLIDA,
                resultado=resultado.value,
                agregados_publicados=0,
                controles_totales=len(calculados),
                controles_fallidos=sum(1 for c in calculados if not c.ok),
                detalle=motivo,
                terminada_en=ahora(),
            )
            .execution_options(synchronize_session=False)
        )
    )
    if registrado and calculados:
        s.add_all(
            ControlAnalytics(
                ejecucion_analytics_id=ejecucion_id,
                codigo_control=c.codigo,
                estado="OK" if c.ok else "FALLO",
                esperado=c.esperado,
                observado=c.observado,
                diferencia=c.observado - c.esperado,
                detalle=c.detalle[:500],
            )
            for c in calculados
        )
    s.commit()
    if registrado:
        log.warning("analytics %s fallida (%s): %s", etiqueta, resultado.value, motivo)
