"""El SQL de analytics/v1: por conjuntos, en tablas temporales, sin una fila por cuenta publicada.

Cada dominio se lee aparte y se agrega primero a nivel (cuenta, periodo): los movimientos de pagos,
las gestiones, las promesas y la atribucion nunca se unen entre si fila contra fila, asi que una
cuenta con tres gestiones, dos pagos y dos promesas no multiplica nada. Despues se suman por
(cuenta, periodo) con UNION ALL, y de ahi a las celdas de cada conjunto de dimensiones con GROUPING
SETS: unos pocos recorridos para todos los conjuntos. Lo que se publica es una fila por metrica,
periodo y celda (`publicacion`), con su estado, su poblacion, su valor y, si es una razon, su
numerador y su denominador.

Las tablas temporales, todas ON COMMIT DROP:

- an_cohorte: una fila por cuenta del corte, con sus dimensiones fijas al corte;
- an_mov: los movimientos de las interpretaciones declaradas recibidos en el horizonte, con su
  periodo y si su PAGO esta anulado al final de as_of;
- an_gestion y an_promesa: las gestiones efectivas y sus promesas, point-in-time;
- an_atrib: la atribucion declarada de los PAGO de la cohorte;
- an_campo, an_carga, an_parada y an_ruta: el plan y el ruteo declarados;
- an_pc y an_ac: los hechos por (cuenta, periodo) y por cuenta en el horizonte;
- an_valor, an_calidad, an_celda, an_periodo, an_aplica y an_suprimida: los agregados, las celdas,
  los periodos con su cobertura, lo que se publica y lo que se suprime.
"""

from __future__ import annotations

from dataclasses import dataclass

from motor_cartera.analytics.catalogo import (
    CONJUNTOS,
    METRICAS,
    SIN_BASE,
    SIN_GESTOR,
    SIN_MEDIO,
    Conjunto,
    Universo,
)
from motor_cartera.analytics.politica import Granularidad
from motor_cartera.atraso import TRAMOS_ATRASO
from motor_cartera.lifecycle.reglas import CONTACTOS

# --- los componentes ------------------------------------------------------------------------------

HECHOS = (
    "rec_neta",
    "rec_pagos",
    "rec_posibles_reversos",
    "rec_movimientos",
    "rec_pagos_anulados",
    "g_gestiones",
    "g_contactada",
    "g_visitas",
    "p_creadas",
    "p_monto",
    "p_evaluadas",
    "p_cumplidas",
    "p_parciales",
    "p_incumplidas",
    "p_pendientes",
    "p_canceladas",
    "p_no_evaluables",
    "p_sin_evaluacion",
    "a_evaluados",
    "a_asociacion_unica",
    "a_ambigua",
    "a_sin_gestion",
    "a_monto_asociacion_unica",
    "a_monto_ambiguo",
    "a_monto_sin_gestion",
)
"""Los hechos de una cuenta en un periodo (an_pc), sumados de cada dominio por separado."""

COMPONENTES = (
    "cuentas",
    "saldo_total",
    "dias_atraso",
    "saldo_atrasado",
    "saldo_atrasado_con_dato",
    "rec_neta",
    "rec_pagos",
    "rec_posibles_reversos",
    "rec_movimientos",
    "rec_pagos_anulados",
    "rec_cuentas_positivas",
    "g_gestiones",
    "g_gestionadas",
    "g_contactadas",
    "g_visitas",
    "p_creadas",
    "p_monto",
    "p_cuentas",
    "p_evaluadas",
    "p_cumplidas",
    "p_parciales",
    "p_incumplidas",
    "p_pendientes",
    "p_canceladas",
    "p_no_evaluables",
    "p_sin_evaluacion",
    "p_resueltas",
    "p_cumplidas_o_parciales",
    "a_evaluados",
    "a_asociacion_unica",
    "a_ambigua",
    "a_sin_gestion",
    "a_monto_asociacion_unica",
    "a_monto_ambiguo",
    "a_monto_sin_gestion",
    "d_cuentas",
    "d_universo",
    "geo_cuentas",
    "geo_validas",
    "geo_discrepancias",
    "geo_sin_coordenadas",
    "geo_incompletas",
    "geo_invalidas",
    "geo_fuera_referencia",
    "geo_ambiguas",
    "t_cuentas",
    "t_asignadas",
    "t_revision",
    "t_sin_base",
    "t_ambiguas",
    "t_fuera_cobertura",
    "c_candidatas",
    "c_asignadas",
    "c_revision_territorial",
    "c_sin_gestor",
    "c_sin_capacidad",
    "c_rebalanceadas",
    "c_capacidad_total",
    "c_capacidad_utilizada",
    "r_rutas",
    "r_rutas_completas",
    "r_rutas_parciales",
    "r_rutas_sin_paradas",
    "r_bases_no_enrutables",
    "r_rutas_sin_jornada",
    "r_paradas",
    "r_programadas",
    "r_sin_tiempo",
    "r_no_enrutables",
    "r_no_enrutables_base",
    "r_distancia_m",
    "r_vial_s",
    "r_servicio_s",
    "r_espera_s",
)
"""Los agregados base de una celda (an_valor): de ellos sale cada metrica del catalogo."""

CALIDAD = (
    "q_sin_cuenta_movimientos",
    "q_sin_cuenta_monto",
    "q_fuera_movimientos",
    "q_fuera_monto",
)
"""Los de la calidad de los pagos, que no son de ninguna cuenta de la cohorte (an_calidad)."""

assert {c for m in METRICAS for c in m.componentes} <= {*COMPONENTES, *CALIDAD}

_CONTACTOS = ", ".join(f"'{n}'" for n in sorted(CONTACTOS))


def tramo(columna: str) -> str:
    """El tramo de atraso con las fronteras canonicas de atraso.TRAMOS_ATRASO; NULL fuera de
    todos, y entonces la ejecucion no publica."""
    casos = " ".join(
        f"WHEN {columna} BETWEEN {desde} AND {hasta} THEN '{etiqueta}'"
        if hasta is not None
        else f"WHEN {columna} >= {desde} THEN '{etiqueta}'"
        for etiqueta, desde, hasta in TRAMOS_ATRASO
    )
    return f"CASE {casos} END"


_UNIDAD = {Granularidad.DIA: "day", Granularidad.SEMANA_ISO: "week", Granularidad.MES: "month"}


def periodo(local: str, granularidad: Granularidad) -> str:
    """El primer dia del periodo de un instante local (timestamp sin zona), recortado al inicio del
    horizonte. Con TOTAL, el horizonte entero es un solo periodo: fecha_corte."""
    if granularidad == Granularidad.TOTAL:
        return "CAST(:fecha_corte AS date)"
    return (
        f"greatest(CAST(date_trunc('{_UNIDAD[granularidad]}', {local}) AS date), "
        "CAST(:fecha_corte AS date))"
    )


# --- la cohorte y los dominios --------------------------------------------------------------------


def sql_cohorte() -> tuple[str, ...]:
    """Las cuentas del corte, una vez, con sus dimensiones fijas al corte: las del snapshot y, si
    se declararon, su decision/v2, su base de territorial/v2 y su estado geografico. Cada union es
    por la llave primaria (ejecucion, cuenta): ninguna multiplica."""
    return (
        "CREATE TEMP TABLE an_cohorte ON COMMIT DROP AS "
        "SELECT s.cuenta_canonica_id AS cuenta, s.saldo_total, s.saldo_atrasado, s.dias_atraso, "
        f"s.producto, {tramo('s.dias_atraso')} AS atraso_bucket, s.cve_entidad AS entidad, "
        "s.cve_entidad || s.cve_municipio AS municipio, d.prioridad_final AS prioridad, "
        "d.canal_recomendado AS canal_decision, CASE WHEN CAST(:territorial AS integer) IS NULL "
        f"THEN NULL ELSE coalesce(b.codigo, '{SIN_BASE}') END AS base, "
        "t.estado_asignacion AS estado_territorial, g.estado_geografia "
        "FROM snapshot_cuenta s "
        "LEFT JOIN resultado_decision_v2 d ON d.ejecucion_decision_v2_id = :decision "
        "AND d.cuenta_canonica_id = s.cuenta_canonica_id "
        "LEFT JOIN asignacion_territorial_v2 t ON t.ejecucion_territorial_v2_id = :territorial "
        "AND t.cuenta_canonica_id = s.cuenta_canonica_id "
        "LEFT JOIN base_operativa b ON b.id = t.base_operativa_id "
        "LEFT JOIN resultado_geografia_cuenta g ON g.ejecucion_geografia_id = :geografia "
        "AND g.cuenta_canonica_id = s.cuenta_canonica_id "
        "WHERE s.corte_canonico_id = :corte",
        "CREATE UNIQUE INDEX ON an_cohorte (cuenta)",
        "ANALYZE an_cohorte",
    )


SQL_REVISAR_COHORTE = (
    "SELECT count(*), count(*) FILTER (WHERE atraso_bucket IS NULL), "
    "count(*) FILTER (WHERE CAST(:decision AS integer) IS NOT NULL AND prioridad IS NULL), "
    "count(*) FILTER (WHERE CAST(:territorial AS integer) IS NOT NULL "
    "AND estado_territorial IS NULL), "
    "count(*) FILTER (WHERE CAST(:geografia AS integer) IS NOT NULL AND estado_geografia IS NULL) "
    "FROM an_cohorte"
)
"""Lo que hace a la cohorte publicable: cada cuenta en un tramo y, por cada ejecucion declarada del
corte, con su resultado en ella."""


def sql_pagos(granularidad: Granularidad) -> tuple[str, ...]:
    """Los movimientos de las interpretaciones declaradas recibidos en el horizonte. Un PAGO esta
    anulado solo si el REVERSO que lo anula esta en una interpretacion declarada y se recibio antes
    del final de as_of: un reverso posterior no existe para este as_of. Su contribucion a la neta
    es la de motor-pagos/v1: el PAGO no anulado suma, el POSIBLE_REVERSO resta, y el REVERSO y su
    pago anulado no entran."""
    return (
        "CREATE TEMP TABLE an_interpretacion ON COMMIT DROP AS "
        "SELECT ejecucion_motor_pagos_id AS id, periodo_desde FROM entrada_analytics "
        "WHERE ejecucion_analytics_id = :ejecucion AND tipo_entrada = 'MOTOR_PAGOS'",
        # Una fila por mes: sin sus estadisticas, el planificador supone miles y ordena todos los
        # movimientos de la cartera para unirlos.
        "ANALYZE an_interpretacion",
        "CREATE TEMP TABLE an_mov ON COMMIT DROP AS "
        "SELECT m.id, m.cuenta_canonica_id AS cuenta, m.tipo_movimiento AS tipo, "
        "m.monto_reportado AS monto, rv.id IS NOT NULL AS anulado, "
        f"{periodo('m.fecha_recepcion', granularidad)} AS periodo, "
        "CAST(date_trunc('month', m.fecha_recepcion) AS date) AS mes, "
        "c.cuenta IS NOT NULL AS en_cohorte, "
        "CASE WHEN m.tipo_movimiento = 'PAGO' AND rv.id IS NULL THEN m.monto_reportado "
        "WHEN m.tipo_movimiento = 'POSIBLE_REVERSO' THEN m.monto_reportado "
        "ELSE CAST(0 AS numeric) END AS neta "
        "FROM an_interpretacion i "
        "JOIN movimiento_economico_canonico m ON m.ejecucion_motor_pagos_id = i.id "
        "LEFT JOIN an_cohorte c ON c.cuenta = m.cuenta_canonica_id "
        "LEFT JOIN movimiento_economico_canonico rv ON m.anulado_por_movimiento_id IS NOT NULL "
        "AND rv.movimiento_id = m.anulado_por_movimiento_id "
        "AND rv.ejecucion_motor_pagos_id IN (SELECT id FROM an_interpretacion) "
        "AND rv.fecha_recepcion < :fin_local "
        "WHERE m.fecha_recepcion >= :inicio_local AND m.fecha_recepcion < :fin_local",
        "CREATE INDEX ON an_mov (id)",
        "ANALYZE an_mov",
    )


SQL_REVERSOS_INCOHERENTES = (
    # Los REVERSO de las interpretaciones declaradas recibidos antes del final de as_of cuyo pago
    # original esta en una interpretacion declarada que no dice que ese reverso lo anula: dos
    # interpretaciones de ventanas vecinas que no se corresponden. Cero, o no se publica.
    "SELECT count(*) FROM an_interpretacion i "
    "JOIN movimiento_economico_canonico r ON r.ejecucion_motor_pagos_id = i.id "
    "AND r.tipo_movimiento = 'REVERSO' AND r.fecha_recepcion < :fin_local "
    "JOIN movimiento_economico_canonico p ON p.movimiento_id = r.movimiento_original_id "
    "AND p.ejecucion_motor_pagos_id IN (SELECT id FROM an_interpretacion) "
    "WHERE p.anulado_por_movimiento_id IS DISTINCT FROM r.movimiento_id"
)

SQL_DATASETS_DE_PAGOS = (
    # Los archivos de pagos de la cartera conocidos a known_at: los de una historia EXITOSA
    # terminada hasta entonces. Y el primer y el ultimo movimiento de cada uno.
    "CREATE TEMP TABLE an_dataset_pagos ON COMMIT DROP AS "
    "SELECT DISTINCT h.dataset_conformado_id AS id FROM ejecucion_historia h "
    "JOIN dataset_conformado d ON d.id = h.dataset_conformado_id "
    "JOIN ingesta_pagos ip ON ip.id = d.ingesta_pagos_id "
    "WHERE h.tipo_fuente = 'PAGOS' AND h.estado = 'EXITOSA' AND h.terminada_en <= :known_at "
    "AND ip.despacho_id = :despacho AND ip.cartera_id = :cartera",
    "ANALYZE an_dataset_pagos",
)
SQL_ALCANCE_DE_LOS_ARCHIVOS = (
    "SELECT p.dataset_conformado_id, min(p.fecha_recepcion), max(p.fecha_recepcion) "
    "FROM pago_observado p WHERE p.dataset_conformado_id IN (SELECT id FROM an_dataset_pagos) "
    "GROUP BY 1 ORDER BY 1"
)
SQL_MESES_INTERPRETADOS = (
    # Por mes del horizonte: los pagos observados conocidos y cuantos de ellos clasifico la
    # interpretacion declarada de su mes, uno por uno.
    "SELECT CAST(date_trunc('month', p.fecha_recepcion) AS date) AS mes, count(*), "
    "count(r.source_row) FROM pago_observado p "
    "LEFT JOIN an_interpretacion i "
    "ON i.periodo_desde = CAST(date_trunc('month', p.fecha_recepcion) AS date) "
    "LEFT JOIN resultado_pago_observado r ON r.ejecucion_motor_pagos_id = i.id "
    "AND r.dataset_conformado_id = p.dataset_conformado_id AND r.source_row = p.source_row "
    "WHERE p.despacho_id = :despacho AND p.cartera_id = :cartera "
    "AND p.fecha_recepcion >= :primer_mes AND p.fecha_recepcion < :fin_de_los_meses "
    "AND p.dataset_conformado_id IN (SELECT id FROM an_dataset_pagos) GROUP BY 1 ORDER BY 1"
)


def sql_gestiones(granularidad: Granularidad) -> tuple[str, ...]:
    """Las gestiones efectivas de la cohorte, point-in-time: una gestion cuenta si ocurrio en el
    horizonte, se registro hasta known_at y no tiene una anulacion que haya ocurrido antes del
    final de as_of y se haya registrado hasta known_at."""
    local = "(g.ocurrido_en AT TIME ZONE :zona)"
    return (
        "CREATE TEMP TABLE an_gestion ON COMMIT DROP AS "
        "SELECT g.id, g.cuenta_canonica_id AS cuenta, g.canal, "
        f"coalesce(g.medio, '{SIN_MEDIO}') AS medio, g.nivel_contacto IN ({_CONTACTOS}) "
        "AS contactada, EXISTS (SELECT 1 FROM visita_campo v WHERE v.gestion_cobranza_id = g.id) "
        f"AS con_visita, {periodo(local, granularidad)} AS periodo "
        "FROM an_cohorte c JOIN gestion_cobranza g ON g.cuenta_canonica_id = c.cuenta "
        "AND g.ocurrido_en >= :inicio AND g.ocurrido_en < :fin "
        "JOIN evento_lifecycle e ON e.id = g.evento_lifecycle_id AND e.registrado_en <= :known_at "
        "WHERE NOT EXISTS (SELECT 1 FROM evento_lifecycle a "
        "WHERE a.evento_relacionado_id = g.evento_lifecycle_id "
        "AND a.tipo_evento = 'GESTION_ANULADA' AND a.ocurrido_en < :fin "
        "AND a.registrado_en <= :known_at)",
        "CREATE UNIQUE INDEX ON an_gestion (id)",
        "ANALYZE an_gestion",
    )


def sql_promesas(granularidad: Granularidad) -> tuple[str, ...]:
    """Las promesas de esas gestiones, point-in-time: una promesa cuenta si la creo una gestion
    efectiva en el horizonte y se registro hasta known_at; su estado es el de la evaluacion
    declarada, si hay una."""
    return (
        "CREATE TEMP TABLE an_promesa ON COMMIT DROP AS "
        "SELECT p.id, g.cuenta, p.monto_prometido AS monto, "
        f"{periodo('(e.ocurrido_en AT TIME ZONE :zona)', granularidad)} AS periodo, "
        "ev.estado FROM an_gestion g JOIN promesa_pago p ON p.gestion_cobranza_id = g.id "
        "JOIN evento_lifecycle e ON e.id = p.evento_lifecycle_id AND e.registrado_en <= :known_at "
        "AND e.ocurrido_en >= :inicio AND e.ocurrido_en < :fin "
        "LEFT JOIN evaluacion_promesa ev ON ev.ejecucion_evaluacion_promesas_id = :evaluacion "
        "AND ev.promesa_pago_id = p.id",
        "ANALYZE an_promesa",
    )


SQL_ATRIBUCION = (
    # La atribucion declarada de cada ventana, de los PAGO de la cohorte en el horizonte. Su monto
    # cuenta solo si el pago no esta anulado al final de as_of. Ninguna candidata se elige.
    "CREATE TEMP TABLE an_atribucion ON COMMIT DROP AS "
    "SELECT ejecucion_atribucion_id AS id, periodo_desde FROM entrada_analytics "
    "WHERE ejecucion_analytics_id = :ejecucion AND tipo_entrada = 'ATRIBUCION'",
    "ANALYZE an_atribucion",
    "CREATE TEMP TABLE an_atrib ON COMMIT DROP AS "
    "SELECT m.id, m.cuenta, m.periodo, am.clasificacion, "
    "CASE WHEN m.anulado THEN CAST(0 AS numeric) ELSE m.monto END AS monto "
    "FROM an_atribucion a JOIN atribucion_movimiento am ON am.ejecucion_atribucion_id = a.id "
    "JOIN an_mov m ON m.id = am.movimiento_economico_canonico_id "
    "WHERE m.en_cohorte AND m.tipo = 'PAGO'",
    "ANALYZE an_atrib",
)

SQL_CAMPO = (
    "CREATE TEMP TABLE an_campo ON COMMIT DROP AS "
    "SELECT a.cuenta_canonica_id AS cuenta, a.estado, a.prioridad, "
    f"coalesce(b.codigo, '{SIN_BASE}') AS base, "
    f"CASE WHEN a.estado = 'ASIGNADA' THEN gc.codigo ELSE '{SIN_GESTOR}' END AS gestor, "
    "a.es_rebalanceada FROM asignacion_campo_cuenta a "
    "LEFT JOIN base_operativa b ON b.id = a.base_operativa_id "
    "LEFT JOIN gestor_campo gc ON gc.id = a.gestor_campo_id "
    "WHERE a.ejecucion_campo_v2_id = :campo",
    "CREATE TEMP TABLE an_carga ON COMMIT DROP AS "
    "SELECT gc.codigo AS gestor, b.codigo AS base, c.capacidad, c.asignadas "
    "FROM carga_gestor_campo c JOIN gestor_campo gc ON gc.id = c.gestor_campo_id "
    "JOIN base_operativa b ON b.id = c.base_operativa_id WHERE c.ejecucion_campo_v2_id = :campo",
)

SQL_RUTEO = (
    "CREATE TEMP TABLE an_parada ON COMMIT DROP AS "
    "SELECT p.cuenta_canonica_id AS cuenta, p.estado, p.prioridad, b.codigo AS base, "
    "gc.codigo AS gestor FROM parada_ruta_v2 p "
    "JOIN ruta_gestor_v2 r ON r.ejecucion_ruteo_v2_id = p.ejecucion_ruteo_v2_id "
    "AND r.gestor_campo_id = p.gestor_campo_id "
    "JOIN base_operativa b ON b.id = r.base_operativa_id "
    "JOIN gestor_campo gc ON gc.id = p.gestor_campo_id WHERE p.ejecucion_ruteo_v2_id = :ruteo",
    "CREATE TEMP TABLE an_ruta ON COMMIT DROP AS "
    "SELECT gc.codigo AS gestor, b.codigo AS base, r.estado_ruta, r.distancia_total_m, "
    "r.duracion_traslado_s, r.duracion_servicio_s, r.duracion_espera_s FROM ruta_gestor_v2 r "
    "JOIN base_operativa b ON b.id = r.base_operativa_id "
    "JOIN gestor_campo gc ON gc.id = r.gestor_campo_id WHERE r.ejecucion_ruteo_v2_id = :ruteo",
)


# --- los hechos por cuenta ------------------------------------------------------------------------


def _rama(fuente: str, expresiones: dict[str, str]) -> str:
    """Los hechos de un dominio por (cuenta, periodo). Los de los otros dominios van NULL, con su
    tipo (el UNION ALL los resuelve de dos en dos): un NULL no ocupa lugar en la fila, y la fila de
    millones que se agrupa despues es la de sus hechos y no la de los 25."""
    columnas = ", ".join(f"{expresiones.get(h, _nulo(h))} AS {h}" for h in HECHOS)
    return f"SELECT cuenta, periodo, {columnas} FROM {fuente} GROUP BY cuenta, periodo"


def _nulo(hecho: str) -> str:
    return "CAST(NULL AS integer)" if hecho == "g_contactada" else "CAST(NULL AS numeric)"


def _total(hecho: str) -> str:
    """La suma exterior de un hecho: 0 si ningun dominio lo trae para esa cuenta y periodo."""
    if hecho == "g_contactada":
        return f"coalesce(max({hecho}), 0) AS {hecho}"
    return f"coalesce(sum({hecho}), 0) AS {hecho}"


SQL_HECHOS = (
    # Cada dominio agregado aparte a (cuenta, periodo), y despues sumados: un UNION ALL, sin unir un
    # dominio con otro. Asi ningun hecho se multiplica por los de otro dominio. Se escribe en el
    # orden de su llave: su indice y la suma por cuenta la recorren en orden, no al azar.
    "CREATE TEMP TABLE an_pc ON COMMIT DROP AS SELECT cuenta, periodo, "
    + ", ".join(_total(h) for h in HECHOS)
    + " FROM ("
    + " UNION ALL ".join(
        (
            _rama(
                "an_mov WHERE en_cohorte",
                {
                    "rec_neta": "sum(neta)",
                    "rec_pagos": "coalesce(sum(monto) FILTER (WHERE tipo = 'PAGO' AND NOT "
                    "anulado), 0)",
                    "rec_posibles_reversos": "coalesce(sum(monto) FILTER (WHERE tipo = "
                    "'POSIBLE_REVERSO'), 0)",
                    "rec_movimientos": "count(*)",
                    "rec_pagos_anulados": "count(*) FILTER (WHERE tipo = 'PAGO' AND anulado)",
                },
            ),
            _rama(
                "an_gestion",
                {
                    "g_gestiones": "count(*)",
                    "g_contactada": "max(CAST(contactada AS integer))",
                    "g_visitas": "count(*) FILTER (WHERE con_visita)",
                },
            ),
            _rama(
                "an_promesa",
                {
                    "p_creadas": "count(*)",
                    "p_monto": "sum(monto)",
                    "p_evaluadas": "count(estado)",
                    "p_cumplidas": "count(*) FILTER (WHERE estado = 'CUMPLIDA')",
                    "p_parciales": "count(*) FILTER (WHERE estado = 'PARCIAL')",
                    "p_incumplidas": "count(*) FILTER (WHERE estado = 'INCUMPLIDA')",
                    "p_pendientes": "count(*) FILTER (WHERE estado = 'PENDIENTE')",
                    "p_canceladas": "count(*) FILTER (WHERE estado = 'CANCELADA')",
                    "p_no_evaluables": "count(*) FILTER (WHERE estado = 'NO_EVALUABLE')",
                    "p_sin_evaluacion": "count(*) FILTER (WHERE estado IS NULL)",
                },
            ),
            _rama(
                "an_atrib",
                {
                    "a_evaluados": "count(*)",
                    "a_asociacion_unica": "count(*) FILTER (WHERE clasificacion = "
                    "'ASOCIACION_UNICA')",
                    "a_ambigua": "count(*) FILTER (WHERE clasificacion = 'AMBIGUA')",
                    "a_sin_gestion": "count(*) FILTER (WHERE clasificacion = "
                    "'SIN_GESTION_CANDIDATA')",
                    "a_monto_asociacion_unica": "coalesce(sum(monto) FILTER (WHERE clasificacion "
                    "= 'ASOCIACION_UNICA'), 0)",
                    "a_monto_ambiguo": "coalesce(sum(monto) FILTER (WHERE clasificacion = "
                    "'AMBIGUA'), 0)",
                    "a_monto_sin_gestion": "coalesce(sum(monto) FILTER (WHERE clasificacion = "
                    "'SIN_GESTION_CANDIDATA'), 0)",
                },
            ),
        )
    )
    + ") x GROUP BY cuenta, periodo ORDER BY cuenta, periodo",
    "CREATE UNIQUE INDEX ON an_pc (cuenta, periodo)",
    "ANALYZE an_pc",
    # Por cuenta en todo el horizonte: la suma de sus periodos.
    "CREATE TEMP TABLE an_ac ON COMMIT DROP AS SELECT cuenta, "
    + ", ".join(f"max({h}) AS {h}" if h == "g_contactada" else f"sum({h}) AS {h}" for h in HECHOS)
    + " FROM an_pc GROUP BY cuenta",
    "CREATE UNIQUE INDEX ON an_ac (cuenta)",
    "ANALYZE an_ac",
)


# --- las celdas -----------------------------------------------------------------------------------


def sql_tablas_de_celdas() -> tuple[str, ...]:
    columnas = ", ".join(f"{c} numeric" for c in COMPONENTES)
    return (
        "CREATE TEMP TABLE an_valor (universo text NOT NULL, conjunto text NOT NULL, "
        "vista text NOT NULL, periodo_desde date NOT NULL, dimension_hash bytea NOT NULL, "
        f"dimensiones jsonb NOT NULL, poblacion bigint, {columnas}) ON COMMIT DROP",
        "CREATE TEMP TABLE an_calidad (vista text NOT NULL, periodo_desde date NOT NULL, "
        + ", ".join(f"{c} numeric NOT NULL" for c in CALIDAD)
        + ", PRIMARY KEY (vista, periodo_desde)) ON COMMIT DROP",
    )


@dataclass(frozen=True)
class Agrupacion:
    """Los conjuntos que se agrupan de una fuente: cada conjunto con sus columnas, por llave."""

    universo: Universo
    conjuntos: tuple[Conjunto, ...]
    columnas: dict[str, str]
    """La expresion SQL de cada llave de dimension en la fuente."""


def _canonica(conjunto: Conjunto, columnas: dict[str, str]) -> str:
    """El JSON canonico de las dimensiones de una celda, como `catalogo.dimension_hash`: llaves en
    orden, sin espacios. to_json escapa cada texto como JSON."""
    claves = sorted(CONJUNTOS[conjunto].claves)
    if not claves:
        return "'{}'"
    partes = []
    for i, clave in enumerate(claves):
        prefijo = "{" if i == 0 else ","
        partes.append(f"'{prefijo}\"{clave}\":' || CAST(to_json({columnas[clave]}) AS text)")
    return " || ".join(partes) + " || '}'"


def _dimensiones(conjunto: Conjunto, columnas: dict[str, str]) -> str:
    claves = CONJUNTOS[conjunto].claves
    if not claves:
        return "CAST('{}' AS jsonb)"
    return "jsonb_build_object(" + ", ".join(f"'{c}', {columnas[c]}" for c in claves) + ")"


def _por_conjunto(agrupacion: Agrupacion, expresion) -> str:
    """CASE sobre el bitmask de GROUPING: la expresion de cada conjunto agrupado."""
    usadas = _usadas(agrupacion)
    if not usadas:
        (conjunto,) = agrupacion.conjuntos
        return expresion(conjunto)
    grouping = "GROUPING(" + ", ".join(agrupacion.columnas[c] for c in usadas) + ")"
    casos = []
    for conjunto in agrupacion.conjuntos:
        claves = set(CONJUNTOS[conjunto].claves)
        mascara = sum(1 << (len(usadas) - 1 - i) for i, c in enumerate(usadas) if c not in claves)
        casos.append(f"WHEN {mascara} THEN {expresion(conjunto)}")
    return f"CASE {grouping} " + " ".join(casos) + " END"


def _usadas(agrupacion: Agrupacion) -> list[str]:
    return sorted({c for conjunto in agrupacion.conjuntos for c in CONJUNTOS[conjunto].claves})


def _grupos(agrupacion: Agrupacion, *fijas: str) -> str:
    conjuntos = []
    for conjunto in agrupacion.conjuntos:
        columnas = [*fijas, *(agrupacion.columnas[c] for c in CONJUNTOS[conjunto].claves)]
        conjuntos.append("(" + ", ".join(columnas) + ")")
    return "GROUPING SETS (" + ", ".join(conjuntos) + ")"


def sql_agrupar(
    agrupacion: Agrupacion,
    fuente: str,
    componentes: dict[str, str],
    *,
    vista: str,
    periodo_col: str | None,
    poblacion: str,
    tabla: str = "an_valor",
) -> str:
    """Un INSERT de una fila por celda de cada conjunto (y por periodo, en la vista PERIODO), con
    sus componentes, en un solo recorrido de la fuente con GROUPING SETS."""
    fijas = (periodo_col,) if periodo_col else ()
    canonica = _por_conjunto(agrupacion, lambda c: _canonica(c, agrupacion.columnas))
    dimensiones = _por_conjunto(agrupacion, lambda c: _dimensiones(c, agrupacion.columnas))
    nombre = _por_conjunto(agrupacion, lambda c: f"'{c.value}'")
    columnas = ", ".join(componentes)
    valores = ", ".join(componentes.values())
    return (
        f"INSERT INTO {tabla} (universo, conjunto, vista, periodo_desde, dimension_hash, "
        f"dimensiones, poblacion, {columnas}) SELECT '{agrupacion.universo.value}', {nombre}, "
        f"'{vista}', {periodo_col or 'CAST(:fecha_corte AS date)'}, "
        f"sha256(convert_to({canonica}, 'UTF8')), {dimensiones}, {poblacion}, {valores} "
        f"FROM {fuente} GROUP BY {_grupos(agrupacion, *fijas)}"
    )


def _suma(columna: str) -> str:
    return f"coalesce(sum({columna}), 0)"


def componentes_cohorte(prefijo: str) -> dict[str, str]:
    """Los componentes de la cohorte en una celda: los del corte y, de `prefijo` (an_ac o an_pc),
    los de los hechos."""
    p = prefijo
    componentes = {
        "rec_neta": _suma(f"{p}.rec_neta"),
        "rec_pagos": _suma(f"{p}.rec_pagos"),
        "rec_posibles_reversos": _suma(f"{p}.rec_posibles_reversos"),
        "rec_movimientos": _suma(f"{p}.rec_movimientos"),
        "rec_pagos_anulados": _suma(f"{p}.rec_pagos_anulados"),
        "rec_cuentas_positivas": f"count(*) FILTER (WHERE {p}.rec_neta > 0)",
        "g_gestiones": _suma(f"{p}.g_gestiones"),
        "g_gestionadas": f"count(*) FILTER (WHERE {p}.g_gestiones > 0)",
        "g_contactadas": f"count(*) FILTER (WHERE {p}.g_contactada = 1)",
        "g_visitas": _suma(f"{p}.g_visitas"),
        "p_creadas": _suma(f"{p}.p_creadas"),
        "p_monto": _suma(f"{p}.p_monto"),
        "p_cuentas": f"count(*) FILTER (WHERE {p}.p_creadas > 0)",
        "p_evaluadas": _suma(f"{p}.p_evaluadas"),
        "p_cumplidas": _suma(f"{p}.p_cumplidas"),
        "p_parciales": _suma(f"{p}.p_parciales"),
        "p_incumplidas": _suma(f"{p}.p_incumplidas"),
        "p_pendientes": _suma(f"{p}.p_pendientes"),
        "p_canceladas": _suma(f"{p}.p_canceladas"),
        "p_no_evaluables": _suma(f"{p}.p_no_evaluables"),
        "p_sin_evaluacion": _suma(f"{p}.p_sin_evaluacion"),
        "p_resueltas": _suma(f"{p}.p_cumplidas + {p}.p_parciales + {p}.p_incumplidas"),
        "p_cumplidas_o_parciales": _suma(f"{p}.p_cumplidas + {p}.p_parciales"),
        "a_evaluados": _suma(f"{p}.a_evaluados"),
        "a_asociacion_unica": _suma(f"{p}.a_asociacion_unica"),
        "a_ambigua": _suma(f"{p}.a_ambigua"),
        "a_sin_gestion": _suma(f"{p}.a_sin_gestion"),
        "a_monto_asociacion_unica": _suma(f"{p}.a_monto_asociacion_unica"),
        "a_monto_ambiguo": _suma(f"{p}.a_monto_ambiguo"),
        "a_monto_sin_gestion": _suma(f"{p}.a_monto_sin_gestion"),
    }
    if prefijo == "a":
        componentes = {
            "cuentas": "count(*)",
            "saldo_total": "sum(c.saldo_total)",
            "dias_atraso": "sum(c.dias_atraso)",
            "saldo_atrasado": "coalesce(sum(c.saldo_atrasado), 0)",
            "saldo_atrasado_con_dato": "count(c.saldo_atrasado)",
            **componentes,
            "d_cuentas": "count(c.prioridad)",
            "d_universo": "CAST(:cuentas_cohorte AS numeric)",
            "geo_cuentas": "count(c.estado_geografia)",
            **{
                f"geo_{codigo}": f"count(*) FILTER (WHERE c.estado_geografia = '{estado}')"
                for codigo, estado in (
                    ("validas", "VALIDA"),
                    ("discrepancias", "DISCREPANCIA_MUNICIPIO"),
                    ("sin_coordenadas", "SIN_COORDENADAS"),
                    ("incompletas", "COORDENADAS_INCOMPLETAS"),
                    ("invalidas", "COORDENADAS_INVALIDAS"),
                    ("fuera_referencia", "FUERA_DE_REFERENCIA"),
                    ("ambiguas", "MUNICIPIO_AMBIGUO"),
                )
            },
            "t_cuentas": "count(c.estado_territorial)",
            **{
                f"t_{codigo}": f"count(*) FILTER (WHERE c.estado_territorial = '{estado}')"
                for codigo, estado in (
                    ("asignadas", "ASIGNADA"),
                    ("revision", "REVISION_GEOGRAFICA"),
                    ("sin_base", "SIN_BASE_ELEGIBLE"),
                    ("ambiguas", "AMBIGUA_BASE"),
                    ("fuera_cobertura", "FUERA_DE_COBERTURA"),
                )
            },
        }
    return componentes


COLUMNAS_COHORTE = {
    "producto": "c.producto",
    "atraso_bucket": "c.atraso_bucket",
    "entidad": "c.entidad",
    "municipio": "c.municipio",
    "prioridad_decision": "c.prioridad",
    "canal_decision": "c.canal_decision",
    "base": "c.base",
}
COLUMNAS_GESTION = {"canal_gestion": "g.canal", "medio_gestion": "g.medio"}
COLUMNAS_CAMPO = {
    "prioridad_decision": "a.prioridad",
    "base": "a.base",
    "estado_campo": "a.estado",
    "gestor_campo": "a.gestor",
}
COLUMNAS_CARGA = {"base": "k.base", "gestor_campo": "k.gestor"}
COLUMNAS_PARADA = {
    "prioridad_decision": "p.prioridad",
    "base": "p.base",
    "estado_ruteo": "p.estado",
    "gestor_campo": "p.gestor",
}
COLUMNAS_RUTA = {"base": "r.base", "gestor_campo": "r.gestor"}

COMPONENTES_GESTION = {
    "g_gestiones": "count(*)",
    "g_gestionadas": "count(DISTINCT g.cuenta)",
    "g_contactadas": "count(DISTINCT g.cuenta) FILTER (WHERE g.contactada)",
    "g_visitas": "count(*) FILTER (WHERE g.con_visita)",
}
"""En las celdas de canal y de medio, las cuentas distintas son la definicion: una cuenta con dos
gestiones del mismo canal es una cuenta gestionada de ese canal. an_gestion tiene una fila por
gestion."""

COMPONENTES_CAMPO = {
    "c_candidatas": "count(*)",
    "c_asignadas": "count(*) FILTER (WHERE a.estado = 'ASIGNADA')",
    "c_revision_territorial": "count(*) FILTER (WHERE a.estado = 'REVISION_TERRITORIAL')",
    "c_sin_gestor": "count(*) FILTER (WHERE a.estado = 'SIN_GESTOR_DISPONIBLE')",
    "c_sin_capacidad": "count(*) FILTER (WHERE a.estado = 'SIN_CAPACIDAD')",
    "c_rebalanceadas": "count(*) FILTER (WHERE a.es_rebalanceada)",
}
COMPONENTES_CARGA = {
    "c_capacidad_total": "coalesce(sum(k.capacidad), 0)",
    "c_capacidad_utilizada": "coalesce(sum(k.asignadas), 0)",
}
COMPONENTES_PARADA = {
    "r_paradas": "count(*)",
    **{
        f"r_{codigo}": f"count(*) FILTER (WHERE p.estado = '{estado}')"
        for codigo, estado in (
            ("programadas", "PROGRAMADA"),
            ("sin_tiempo", "SIN_TIEMPO"),
            ("no_enrutables", "NO_ENRUTABLE"),
            ("no_enrutables_base", "NO_ENRUTABLE_BASE"),
        )
    },
}
COMPONENTES_RUTA = {
    "r_rutas": "count(*)",
    **{
        f"r_{codigo}": f"count(*) FILTER (WHERE r.estado_ruta = '{estado}')"
        for codigo, estado in (
            ("rutas_completas", "COMPLETA"),
            ("rutas_parciales", "PARCIAL"),
            ("rutas_sin_paradas", "SIN_PARADAS_ENRUTABLES"),
            ("bases_no_enrutables", "BASE_NO_ENRUTABLE"),
            ("rutas_sin_jornada", "SIN_JORNADA"),
        )
    },
    "r_distancia_m": "coalesce(sum(r.distancia_total_m), 0)",
    "r_vial_s": "coalesce(sum(r.duracion_traslado_s), 0)",
    "r_servicio_s": "coalesce(sum(r.duracion_servicio_s), 0)",
    "r_espera_s": "coalesce(sum(r.duracion_espera_s), 0)",
}


def sql_unir(
    universo: Universo,
    uno: str,
    componentes_uno: dict[str, str],
    otro: str,
    componentes_otro: dict[str, str],
    poblacion: str,
) -> str:
    """Las celdas de dos agregaciones del mismo universo unidas por su conjunto y su
    dimension_hash (FULL OUTER JOIN): una celda que solo esta en una tiene cero en lo de la otra.
    Asi una base con gestores y sin candidatas tiene su capacidad, y una ruta su paradas."""
    columnas = [*componentes_uno, *componentes_otro]
    return (
        "INSERT INTO an_valor (universo, conjunto, vista, periodo_desde, dimension_hash, "
        f"dimensiones, poblacion, {', '.join(columnas)}) SELECT '{universo.value}', "
        "coalesce(x.conjunto, y.conjunto), 'ACUMULADA', CAST(:fecha_corte AS date), "
        "coalesce(x.dimension_hash, y.dimension_hash), coalesce(x.dimensiones, y.dimensiones), "
        f"{poblacion}, "
        + ", ".join(
            [*(f"coalesce(x.{c}, 0)" for c in componentes_uno)]
            + [*(f"y.{c}" for c in componentes_otro)]
        )
        + f" FROM {uno} x FULL OUTER JOIN {otro} y ON y.conjunto = x.conjunto "
        "AND y.dimension_hash = x.dimension_hash"
    )


def sql_tabla_parcial(nombre: str, componentes: dict[str, str]) -> str:
    return (
        f"CREATE TEMP TABLE {nombre} (universo text, conjunto text, vista text, "
        "periodo_desde date, dimension_hash bytea, dimensiones jsonb, poblacion bigint, "
        + ", ".join(f"{c} numeric" for c in componentes)
        + ") ON COMMIT DROP"
    )


def sql_calidad(granularidad: Granularidad) -> tuple[str, ...]:
    """La calidad de los pagos del horizonte: los movimientos sin cuenta y los de cuentas que no son
    de la cohorte, en la vista ACUMULADA y, por periodo, en la vista PERIODO."""
    columnas = (
        "count(*) FILTER (WHERE cuenta IS NULL), "
        "coalesce(sum(neta) FILTER (WHERE cuenta IS NULL), 0), "
        "count(*) FILTER (WHERE cuenta IS NOT NULL AND NOT en_cohorte), "
        "coalesce(sum(neta) FILTER (WHERE cuenta IS NOT NULL AND NOT en_cohorte), 0)"
    )
    sentencias = [
        f"INSERT INTO an_calidad (vista, periodo_desde, {', '.join(CALIDAD)}) "
        f"SELECT 'ACUMULADA', CAST(:fecha_corte AS date), {columnas} FROM an_mov"
    ]
    if granularidad != Granularidad.TOTAL:
        sentencias.append(
            f"INSERT INTO an_calidad (vista, periodo_desde, {', '.join(CALIDAD)}) "
            f"SELECT 'PERIODO', periodo, {columnas} FROM an_mov GROUP BY periodo"
        )
    return tuple(sentencias)


SQL_CELDAS = (
    # Las celdas del horizonte: las de la vista ACUMULADA, con su poblacion y su saldo inicial (el
    # denominador de recuperacion.sobre_saldo_inicial en cualquier periodo).
    "CREATE TEMP TABLE an_celda ON COMMIT DROP AS SELECT universo, conjunto, dimension_hash, "
    "dimensiones, poblacion, saldo_total FROM an_valor WHERE vista = 'ACUMULADA'",
    "CREATE UNIQUE INDEX ON an_celda (universo, conjunto, dimension_hash)",
    "CREATE INDEX ON an_valor (universo, conjunto, dimension_hash, vista, periodo_desde)",
    "ANALYZE an_celda",
    "ANALYZE an_valor",
)

SQL_TABLAS_DE_PUBLICACION = (
    "CREATE TEMP TABLE an_periodo (vista text NOT NULL, periodo_desde date NOT NULL, "
    "periodo_hasta date NOT NULL, parcial boolean NOT NULL, pagos_ok boolean NOT NULL, "
    "atribucion_ok boolean NOT NULL, motivo_pagos jsonb NOT NULL, "
    "motivo_atribucion jsonb NOT NULL, PRIMARY KEY (vista, periodo_desde)) ON COMMIT DROP",
    "CREATE TEMP TABLE an_aplica (metrica text NOT NULL, universo text NOT NULL, "
    "conjunto text NOT NULL, vista text NOT NULL, disponible boolean NOT NULL, "
    "cobertura_pagos boolean NOT NULL, atribucion boolean NOT NULL, "
    "con_poblacion boolean NOT NULL, motivo jsonb NOT NULL, "
    "PRIMARY KEY (metrica, universo, conjunto, vista)) ON COMMIT DROP",
    "CREATE TEMP TABLE an_suprimida (universo text NOT NULL, conjunto text NOT NULL, "
    "dimension_hash bytea NOT NULL, motivo text NOT NULL, "
    "PRIMARY KEY (universo, conjunto, dimension_hash)) ON COMMIT DROP",
)


def _expresion(componente: str) -> str:
    if componente in CALIDAD:
        return f"coalesce(q.{componente}, 0)"
    if componente == "saldo_total":
        return "c.saldo_total"
    return f"coalesce(v.{componente}, 0)"


def sql_publicacion(escala: int, minimo: int, conjuntos_en_periodo: tuple[Conjunto, ...]) -> str:
    """Una fila por metrica, periodo y celda aplicable: su estado, su poblacion, su valor y, en una
    razon, su numerador y su denominador. NO_DISPONIBLE si faltan sus entradas o la cobertura de su
    periodo; SUPRIMIDO si su celda esta suprimida; nunca un valor fuera de un DISPONIBLE."""
    numerador = (
        "CASE ap.metrica "
        + " ".join(f"WHEN '{m.codigo}' THEN {_expresion(m.componentes[0])}" for m in METRICAS)
        + " END"
    )
    denominador = (
        "CASE ap.metrica "
        + " ".join(
            f"WHEN '{m.codigo}' THEN {_expresion(m.componentes[1])}"
            for m in METRICAS
            if len(m.componentes) == 2
        )
        + " END"
    )
    en_periodo = ", ".join(f"'{c.value}'" for c in conjuntos_en_periodo)
    return f"""
INSERT INTO agregado_analytics (ejecucion_analytics_id, metrica_codigo, vista, dimension_set,
    periodo_desde, dimension_hash, periodo_hasta, periodo_parcial, dimensiones, estado_valor,
    poblacion_cuentas, valor, numerador, denominador, motivos)
SELECT :ejecucion, z.metrica, z.vista, z.conjunto, z.periodo_desde, z.dimension_hash,
    z.periodo_hasta, z.parcial, z.dimensiones, z.estado,
    CASE WHEN z.suprimida OR NOT z.con_poblacion THEN NULL ELSE z.poblacion END,
    CASE WHEN z.estado <> 'DISPONIBLE' THEN NULL WHEN z.denominador IS NULL THEN z.numerador
        WHEN z.denominador = 0 THEN NULL ELSE round(z.numerador / z.denominador, {escala}) END,
    CASE WHEN z.estado = 'DISPONIBLE' AND z.denominador IS NOT NULL THEN z.numerador END,
    CASE WHEN z.estado = 'DISPONIBLE' THEN z.denominador END,
    CASE z.estado
        WHEN 'NO_DISPONIBLE' THEN z.motivo_no_disponible
        WHEN 'SUPRIMIDO' THEN jsonb_build_array(jsonb_build_object('codigo', z.motivo_supresion,
            'min_group_size', {minimo}))
        WHEN 'DISPONIBLE' THEN CASE WHEN z.denominador = 0
            THEN CAST('[{{"codigo": "DENOMINADOR_CERO"}}]' AS jsonb) ELSE CAST('[]' AS jsonb) END
    END
FROM (
    SELECT ap.metrica, p.vista, c.conjunto, p.periodo_desde, c.dimension_hash, p.periodo_hasta,
        p.parcial, c.dimensiones, c.poblacion, ap.con_poblacion,
        s.dimension_hash IS NOT NULL AS suprimida, s.motivo AS motivo_supresion,
        CASE WHEN NOT ap.disponible THEN 'NO_DISPONIBLE'
            WHEN ap.cobertura_pagos AND NOT p.pagos_ok THEN 'NO_DISPONIBLE'
            WHEN ap.atribucion AND NOT p.atribucion_ok THEN 'NO_DISPONIBLE'
            WHEN s.dimension_hash IS NOT NULL THEN 'SUPRIMIDO'
            ELSE 'DISPONIBLE' END AS estado,
        CASE WHEN NOT ap.disponible THEN ap.motivo
            WHEN ap.cobertura_pagos AND NOT p.pagos_ok THEN p.motivo_pagos
            ELSE p.motivo_atribucion END AS motivo_no_disponible,
        CAST({numerador} AS numeric) AS numerador,
        CAST({denominador} AS numeric) AS denominador
    FROM an_celda c
    JOIN an_periodo p ON p.vista = 'ACUMULADA'
        OR (p.vista = 'PERIODO' AND c.conjunto IN ({en_periodo}))
    JOIN an_aplica ap ON ap.universo = c.universo AND ap.conjunto = c.conjunto
        AND ap.vista = p.vista
    LEFT JOIN an_valor v ON v.universo = c.universo AND v.conjunto = c.conjunto
        AND v.dimension_hash = c.dimension_hash AND v.vista = p.vista
        AND v.periodo_desde = p.periodo_desde
    LEFT JOIN an_calidad q ON c.universo = 'COHORTE' AND c.conjunto = 'TOTAL'
        AND q.vista = p.vista AND q.periodo_desde = p.periodo_desde
    LEFT JOIN an_suprimida s ON s.universo = c.universo AND s.conjunto = c.conjunto
        AND s.dimension_hash = c.dimension_hash
) z
"""


SQL_NO_APLICA = """
INSERT INTO agregado_analytics (ejecucion_analytics_id, metrica_codigo, vista, dimension_set,
    periodo_desde, dimension_hash, periodo_hasta, periodo_parcial, dimensiones, estado_valor,
    poblacion_cuentas, valor, numerador, denominador, motivos)
SELECT :ejecucion, m.metrica, 'PERIODO', 'TOTAL', p.periodo_desde,
    sha256(convert_to('{}', 'UTF8')), p.periodo_hasta, p.parcial, CAST('{}' AS jsonb),
    'NO_APLICA', NULL, NULL, NULL, NULL,
    CAST('[{"codigo": "METRICA_SIN_PERIODO"}]' AS jsonb)
FROM an_periodo p CROSS JOIN unnest(CAST(:sin_periodo AS text[])) AS m(metrica)
WHERE p.vista = 'PERIODO'
"""
"""Una metrica de estado al corte o de un plan de un dia no tiene sentido por periodo: en la vista
PERIODO se publica una fila NO_APLICA por periodo, en TOTAL."""

SQL_HASH_AGREGADOS = (
    # Todo lo publicado, en orden canonico: lo que una reconstruccion tiene que repetir.
    "SELECT encode(sha256(convert_to(coalesce(string_agg(concat_ws('|', metrica_codigo, vista, "
    "dimension_set, periodo_desde, periodo_hasta, periodo_parcial, encode(dimension_hash, 'hex'), "
    "estado_valor, coalesce(CAST(poblacion_cuentas AS text), '~'), "
    "coalesce(CAST(valor AS text), '~'), coalesce(CAST(numerador AS text), '~'), "
    "coalesce(CAST(denominador AS text), '~'), CAST(motivos AS text)), chr(10) "
    "ORDER BY metrica_codigo, vista, dimension_set, periodo_desde, dimension_hash), ''), "
    "'UTF8')), 'hex') FROM agregado_analytics WHERE ejecucion_analytics_id = :ejecucion"
)
