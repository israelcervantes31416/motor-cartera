"""El SQL de predictive-features/public-v1 y de predictive-targets/public-v1: por conjuntos, en
tablas temporales de una transaccion, sin una fila por cuenta y dia.

Las features de un ancla (o de un scoring, o de la explicacion de una cuenta) se calculan con el
mismo SQL, con los relojes de cada uno, en tablas `pf_*`:

- pf_universo: las cuentas del corte (sus snapshots); con `:cuenta`, solo esa;
- pf_corte y pf_snap: los cortes de la cartera hasta T conocidos a K, y los snapshots de esas
  cuentas en ellos;
- pf_interpretacion y pf_mov: la interpretacion de pagos de cada mes VIGENTE a K (la EXITOSA mas
  reciente que ya habia terminado) y sus movimientos conciliados con esas cuentas, recibidos hasta
  el final de T, con la marca de si un REVERSO recibido hasta el final de T anula cada PAGO;
- pf_gestion y pf_promesa: el lifecycle ocurrido hasta el final de T, registrado hasta K, sin una
  anulacion registrada hasta K (decision/v2: una anulacion corrige el registro);
- pf_evaluacion: los estados de la evaluacion de promesas que se eligio (`:evaluacion`);
- pf_feature: una fila por cuenta, con cada feature con su tipo y su feature_hash.

Los labels de un ancla, en tablas `pl_*`, con las interpretaciones declaradas en el manifiesto y lo
registrado hasta label_known_at: la recuperacion neta en (T, T+30], el contacto en (T, T+14] y la
creacion de promesas en (T, T+30], con la semantica de analytics/v1.

Ningun join mezcla dominios fila contra fila: cada uno se agrega por cuenta y se une por la llave
de la cuenta. Este modulo solo genera texto: lo ejecuta `construccion`.
"""

from __future__ import annotations

from motor_cartera.atraso import TRAMOS_ATRASO
from motor_cartera.lifecycle.reglas import CONTACTOS
from motor_cartera.predictivo.features import FEATURES, NULO, VERSION_FEATURES, Tipo

_CONTACTOS = ", ".join(f"'{n}'" for n in sorted(CONTACTOS))

_TRAMO = (
    "CASE "
    + " ".join(
        f"WHEN u.dias_atraso BETWEEN {desde} AND {hasta} THEN '{etiqueta}'"
        if hasta is not None
        else f"WHEN u.dias_atraso >= {desde} THEN '{etiqueta}'"
        for etiqueta, desde, hasta in TRAMOS_ATRASO
    )
    + " END"
)


def _anulada(evento: str, *, conocida: str, ocurrida_antes_de: str | None = None) -> str:
    """Que la gestion del evento `evento` tenga una anulacion registrada hasta `conocida` (y, si se
    da, ocurrida antes de `ocurrida_antes_de`)."""
    ocurrida = "" if ocurrida_antes_de is None else f" AND a.ocurrido_en < {ocurrida_antes_de}"
    return (
        "EXISTS (SELECT 1 FROM evento_lifecycle a WHERE a.evento_relacionado_id = "
        f"{evento} AND a.tipo_evento = 'GESTION_ANULADA' AND a.registrado_en <= {conocida}"
        f"{ocurrida})"
    )


# --- el universo y la historia --------------------------------------------------------------------


def sql_universo(*, una_cuenta: bool) -> tuple[str, ...]:
    """Las cuentas del corte, o solo `:cuenta` (por su llave primaria, sin recorrer el corte)."""
    filtro = " AND s.cuenta_canonica_id = CAST(:cuenta AS integer)" if una_cuenta else ""
    return (
        "CREATE TEMP TABLE pf_universo ON COMMIT DROP AS "
        "SELECT s.cuenta_canonica_id AS cuenta, c.cuenta_id, s.saldo_total, s.saldo, "
        "s.saldo_atrasado, s.saldo_requerido, s.moratorios, s.pago_normal, s.dias_atraso, "
        "s.atraso_maximo, s.producto, s.cve_entidad FROM snapshot_cuenta s "
        "JOIN cuenta_canonica c ON c.id = s.cuenta_canonica_id "
        f"WHERE s.corte_canonico_id = :corte{filtro}",
        "CREATE UNIQUE INDEX ON pf_universo (cuenta)",
        "ANALYZE pf_universo",
    )


SQL_HISTORIA = (
    # Los cortes de la cartera hasta T que ya existian a K: un corte materializado despues no
    # existe para este ancla.
    "CREATE TEMP TABLE pf_corte ON COMMIT DROP AS SELECT id, fecha_corte FROM corte_canonico "
    "WHERE despacho_id = :despacho AND cartera_id = :cartera AND fecha_corte <= :as_of "
    "AND creado_en <= :known_at",
    "ANALYZE pf_corte",
    "CREATE TEMP TABLE pf_snap ON COMMIT DROP AS "
    "SELECT s.cuenta_canonica_id AS cuenta, s.fecha_corte, s.saldo_total, s.dias_atraso "
    "FROM pf_universo u JOIN snapshot_cuenta s ON s.cuenta_canonica_id = u.cuenta "
    "JOIN pf_corte k ON k.id = s.corte_canonico_id",
    "ANALYZE pf_snap",
    # El snapshot anterior observado y el mas reciente de 28 dias o mas antes.
    "CREATE TEMP TABLE pf_anterior ON COMMIT DROP AS SELECT DISTINCT ON (cuenta) cuenta, "
    "saldo_total, dias_atraso FROM pf_snap WHERE fecha_corte < :as_of "
    "ORDER BY cuenta, fecha_corte DESC",
    "CREATE TEMP TABLE pf_hace_28 ON COMMIT DROP AS SELECT DISTINCT ON (cuenta) cuenta, "
    "saldo_total, dias_atraso FROM pf_snap WHERE fecha_corte <= CAST(:as_of AS date) - 28 "
    "ORDER BY cuenta, fecha_corte DESC",
    # Cuantos cortes la observan, y la pendiente de minimos cuadrados en [T-28, T], en numeric
    # exacto (sin depender del orden en que la suma recorre las filas), con seis decimales.
    "CREATE TEMP TABLE pf_historia ON COMMIT DROP AS SELECT cuenta, total AS cortes, "
    "CASE WHEN n >= 2 AND n * sxx - sx * sx <> 0 "
    "THEN round((n * sxy - sx * sy) / (n * sxx - sx * sx), 6) END AS pendiente_saldo, "
    "CASE WHEN n >= 2 AND n * sxx - sx * sx <> 0 "
    "THEN round((n * sxd - sx * sd) / (n * sxx - sx * sx), 6) END AS pendiente_dias "
    "FROM (SELECT cuenta, count(*) AS total, "
    + ", ".join(
        f"CAST({agregado} FILTER (WHERE dx >= -28) AS numeric) AS {nombre}"
        for nombre, agregado in (
            ("n", "count(*)"),
            ("sx", "sum(dx)"),
            ("sxx", "sum(dx * dx)"),
            ("sy", "sum(saldo_total)"),
            ("sxy", "sum(dx * saldo_total)"),
            ("sd", "sum(dias_atraso)"),
            ("sxd", "sum(dx * dias_atraso)"),
        )
    )
    + " FROM (SELECT cuenta, saldo_total, dias_atraso, fecha_corte - CAST(:as_of AS date) AS dx "
    "FROM pf_snap) y GROUP BY cuenta) x",
)


# --- los pagos ------------------------------------------------------------------------------------

SQL_PAGOS = (
    # La interpretacion de cada mes VIGENTE a K: la EXITOSA mas reciente que ya habia terminado.
    "CREATE TEMP TABLE pf_interpretacion ON COMMIT DROP AS "
    "SELECT DISTINCT ON (periodo_desde) id, periodo_desde FROM ejecucion_motor_pagos "
    "WHERE despacho_id = :despacho AND cartera_id = :cartera "
    "AND version_motor = :version_motor_pagos AND estado = 'EXITOSA' "
    "AND terminada_en <= :known_at AND periodo_desde <= :as_of "
    "ORDER BY periodo_desde, id DESC",
    "ANALYZE pf_interpretacion",
    # Sus movimientos conciliados con las cuentas, recibidos hasta el final de T. Un PAGO esta
    # anulado solo si el REVERSO que lo anula esta en esas interpretaciones y llego hasta el final
    # de T: un reverso posterior no existe para este ancla.
    "CREATE TEMP TABLE pf_mov ON COMMIT DROP AS "
    "SELECT m.cuenta_canonica_id AS cuenta, m.tipo_movimiento AS tipo, m.fecha_recepcion, "
    "m.monto_reportado AS monto, rv.id IS NOT NULL AS anulado "
    "FROM pf_interpretacion i "
    "JOIN movimiento_economico_canonico m ON m.ejecucion_motor_pagos_id = i.id "
    "JOIN pf_universo u ON u.cuenta = m.cuenta_canonica_id "
    "LEFT JOIN movimiento_economico_canonico rv ON m.anulado_por_movimiento_id IS NOT NULL "
    "AND rv.movimiento_id = m.anulado_por_movimiento_id "
    "AND rv.ejecucion_motor_pagos_id IN (SELECT id FROM pf_interpretacion) "
    "AND rv.fecha_recepcion < :fin_local "
    "WHERE m.fecha_recepcion < :fin_local",
    "ANALYZE pf_mov",
    "CREATE TEMP TABLE pf_f_pago ON COMMIT DROP AS SELECT cuenta, "
    + ", ".join(
        f"coalesce(sum(CASE WHEN tipo = 'PAGO' AND NOT anulado THEN monto "
        f"WHEN tipo = 'POSIBLE_REVERSO' THEN monto ELSE 0 END) "
        f"FILTER (WHERE fecha_recepcion >= :inicio_{n}d_local), 0) AS neta_{n}d, "
        f"count(*) FILTER (WHERE fecha_recepcion >= :inicio_{n}d_local) AS movimientos_{n}d, "
        f"count(*) FILTER (WHERE tipo = 'PAGO' AND NOT anulado "
        f"AND fecha_recepcion >= :inicio_{n}d_local) AS pagos_{n}d"
        for n in (30, 90)
    )
    + ", count(*) FILTER (WHERE tipo IN ('REVERSO', 'POSIBLE_REVERSO') "
    "AND fecha_recepcion >= :inicio_90d_local) AS reversos_90d, "
    "max(fecha_recepcion) AS ultimo_movimiento, "
    "max(fecha_recepcion) FILTER (WHERE tipo = 'PAGO' AND NOT anulado) AS ultimo_pago "
    "FROM pf_mov GROUP BY cuenta",
)

SQL_DATASETS_DE_PAGOS = (
    # Los archivos de pagos de la cartera conocidos a K: los de una historia EXITOSA terminada
    # hasta entonces. Su alcance (primer y ultimo movimiento) es la evidencia de su cobertura.
    "CREATE TEMP TABLE pf_dataset_pagos ON COMMIT DROP AS "
    "SELECT DISTINCT h.dataset_conformado_id AS id FROM ejecucion_historia h "
    "JOIN dataset_conformado d ON d.id = h.dataset_conformado_id "
    "JOIN ingesta_pagos ip ON ip.id = d.ingesta_pagos_id "
    "WHERE h.tipo_fuente = 'PAGOS' AND h.estado = 'EXITOSA' AND h.terminada_en <= :known_at "
    "AND ip.despacho_id = :despacho AND ip.cartera_id = :cartera",
    "ANALYZE pf_dataset_pagos",
)
SQL_ALCANCES = (
    "SELECT min(p.fecha_recepcion), max(p.fecha_recepcion) FROM pago_observado p "
    "WHERE p.dataset_conformado_id IN (SELECT id FROM pf_dataset_pagos) "
    "AND p.fecha_recepcion < :fin_local AND p.fecha_recepcion >= :desde_alcance "
    "GROUP BY p.dataset_conformado_id ORDER BY 1"
)
SQL_MESES = (
    # Por mes de la ventana de 90 dias: los pagos observados conocidos a K y cuantos clasifico la
    # interpretacion de su mes vigente a K, uno por uno. El EXISTS sondea la llave primaria de
    # resultado_pago_observado con sus tres columnas: un LEFT JOIN, con la interpretacion del mes
    # mas reciente todavia fuera de las estadisticas, puede recorrer todos sus resultados por pago.
    "SELECT CAST(date_trunc('month', p.fecha_recepcion) AS date) AS mes, count(*), "
    "count(*) FILTER (WHERE EXISTS (SELECT 1 FROM resultado_pago_observado r "
    "WHERE r.ejecucion_motor_pagos_id = i.id AND r.dataset_conformado_id = p.dataset_conformado_id "
    "AND r.source_row = p.source_row)) FROM pago_observado p "
    "LEFT JOIN pf_interpretacion i "
    "ON i.periodo_desde = CAST(date_trunc('month', p.fecha_recepcion) AS date) "
    "WHERE p.despacho_id = :despacho AND p.cartera_id = :cartera "
    "AND p.fecha_recepcion >= :primer_mes AND p.fecha_recepcion < :fin_local "
    "AND p.dataset_conformado_id IN (SELECT id FROM pf_dataset_pagos) GROUP BY 1 ORDER BY 1"
)


# --- el lifecycle y las promesas ------------------------------------------------------------------

SQL_LIFECYCLE = (
    "CREATE TEMP TABLE pf_gestion ON COMMIT DROP AS "
    "SELECT g.cuenta_canonica_id AS cuenta, g.ocurrido_en, g.nivel_contacto "
    "FROM pf_universo u JOIN gestion_cobranza g ON g.cuenta_canonica_id = u.cuenta "
    "AND g.ocurrido_en < :fin "
    "JOIN evento_lifecycle e ON e.id = g.evento_lifecycle_id AND e.registrado_en <= :known_at "
    f"WHERE NOT {_anulada('g.evento_lifecycle_id', conocida=':known_at')}",
    "CREATE TEMP TABLE pf_f_gestion ON COMMIT DROP AS SELECT cuenta, "
    "count(*) FILTER (WHERE ocurrido_en >= :inicio_30d) AS gestiones_30d, "
    "count(*) FILTER (WHERE ocurrido_en >= :inicio_90d) AS gestiones_90d, "
    f"count(*) FILTER (WHERE ocurrido_en >= :inicio_30d AND nivel_contacto IN ({_CONTACTOS})) "
    "AS contactos_30d, "
    f"count(*) FILTER (WHERE ocurrido_en >= :inicio_90d AND nivel_contacto IN ({_CONTACTOS})) "
    "AS contactos_90d, "
    "count(*) FILTER (WHERE ocurrido_en >= :inicio_30d AND nivel_contacto = 'SIN_CONTACTO') "
    "AS sin_contacto_30d, max(ocurrido_en) AS ultima, "
    f"max(ocurrido_en) FILTER (WHERE nivel_contacto IN ({_CONTACTOS})) AS ultimo_contacto "
    "FROM pf_gestion GROUP BY cuenta",
    # Las promesas de una gestion que no se sabia anulada a K, acordadas hasta el final de T y
    # registradas hasta K.
    "CREATE TEMP TABLE pf_f_promesa ON COMMIT DROP AS SELECT p.cuenta_canonica_id AS cuenta, "
    "count(*) FILTER (WHERE e.ocurrido_en >= :inicio_90d) AS creadas_90d, "
    "max(e.ocurrido_en) AS ultima "
    "FROM pf_universo u JOIN promesa_pago p ON p.cuenta_canonica_id = u.cuenta "
    "JOIN evento_lifecycle e ON e.id = p.evento_lifecycle_id AND e.ocurrido_en < :fin "
    "AND e.registrado_en <= :known_at "
    "JOIN gestion_cobranza g ON g.id = p.gestion_cobranza_id "
    f"WHERE NOT {_anulada('g.evento_lifecycle_id', conocida=':known_at')} "
    "GROUP BY p.cuenta_canonica_id",
    # Los estados de la evaluacion elegida: EXITOSA, terminada hasta K, con as_of hasta T.
    "CREATE TEMP TABLE pf_f_evaluacion ON COMMIT DROP AS SELECT p.cuenta_canonica_id AS cuenta, "
    "count(*) FILTER (WHERE ev.estado = 'CUMPLIDA') AS cumplidas, "
    "count(*) FILTER (WHERE ev.estado = 'PARCIAL') AS parciales, "
    "count(*) FILTER (WHERE ev.estado = 'INCUMPLIDA') AS incumplidas, "
    "count(*) FILTER (WHERE ev.estado = 'PENDIENTE') AS pendientes "
    "FROM evaluacion_promesa ev JOIN promesa_pago p ON p.id = ev.promesa_pago_id "
    "JOIN pf_universo u ON u.cuenta = p.cuenta_canonica_id "
    "WHERE ev.ejecucion_evaluacion_promesas_id = CAST(:evaluacion AS integer) "
    "GROUP BY p.cuenta_canonica_id",
)


# --- las features ---------------------------------------------------------------------------------

TIPO_SQL: dict[Tipo, str] = {
    Tipo.IMPORTE: "numeric(18, 2)",
    Tipo.ENTERO: "bigint",
    Tipo.DECIMAL: "numeric(18, 6)",
    Tipo.TEXTO: "text",
}


def _dias(instante: str, *, local: bool = False) -> str:
    """Los dias de calendario de un instante (o de una hora local, en pagos/v1) hasta T."""
    dia = f"CAST({instante} AS date)" if local else f"CAST({instante} AT TIME ZONE :zona AS date)"
    return f"CAST(:as_of AS date) - {dia}"


EXPRESION: dict[str, str] = {
    "saldo_total": "u.saldo_total",
    "saldo": "u.saldo",
    "saldo_atrasado": "u.saldo_atrasado",
    "saldo_requerido": "u.saldo_requerido",
    "moratorios": "u.moratorios",
    "pago_normal": "u.pago_normal",
    "dias_atraso": "u.dias_atraso",
    "atraso_maximo": "u.atraso_maximo",
    "producto": "u.producto",
    "cve_entidad": "u.cve_entidad",
    "tramo_atraso": _TRAMO,
    "numero_cortes_observados": "h.cortes",
    "delta_saldo_1_corte": "u.saldo_total - a.saldo_total",
    "delta_saldo_4_semanas": "u.saldo_total - b.saldo_total",
    "delta_dias_atraso_1_corte": "u.dias_atraso - a.dias_atraso",
    "delta_dias_atraso_4_semanas": "u.dias_atraso - b.dias_atraso",
    "tendencia_saldo": "h.pendiente_saldo",
    "tendencia_atraso": "h.pendiente_dias",
    "recuperacion_neta_30d_pasados": "CASE WHEN :pagos_30d THEN coalesce(pg.neta_30d, 0) END",
    "recuperacion_neta_90d_pasados": "CASE WHEN :pagos_90d THEN coalesce(pg.neta_90d, 0) END",
    "movimientos_30d": "CASE WHEN :pagos_30d THEN coalesce(pg.movimientos_30d, 0) END",
    "movimientos_90d": "CASE WHEN :pagos_90d THEN coalesce(pg.movimientos_90d, 0) END",
    "pagos_positivos_30d": "CASE WHEN :pagos_30d THEN coalesce(pg.pagos_30d, 0) END",
    "pagos_positivos_90d": "CASE WHEN :pagos_90d THEN coalesce(pg.pagos_90d, 0) END",
    "reversos_90d": "CASE WHEN :pagos_90d THEN coalesce(pg.reversos_90d, 0) END",
    "dias_desde_ultimo_movimiento": (
        f"CASE WHEN :pagos_90d THEN {_dias('pg.ultimo_movimiento', local=True)} END"
    ),
    "dias_desde_ultima_recuperacion_positiva": (
        f"CASE WHEN :pagos_90d THEN {_dias('pg.ultimo_pago', local=True)} END"
    ),
    "gestiones_30d": "coalesce(g.gestiones_30d, 0)",
    "gestiones_90d": "coalesce(g.gestiones_90d, 0)",
    "contactos_30d": "coalesce(g.contactos_30d, 0)",
    "contactos_90d": "coalesce(g.contactos_90d, 0)",
    "gestiones_sin_contacto_30d": "coalesce(g.sin_contacto_30d, 0)",
    "dias_desde_ultima_gestion": _dias("g.ultima"),
    "dias_desde_ultimo_contacto": _dias("g.ultimo_contacto"),
    "promesas_creadas_90d": "coalesce(pr.creadas_90d, 0)",
    "promesas_cumplidas_historicas": "CASE WHEN :con_evaluacion THEN coalesce(ev.cumplidas, 0) END",
    "promesas_parciales_historicas": "CASE WHEN :con_evaluacion THEN coalesce(ev.parciales, 0) END",
    "promesas_incumplidas_historicas": (
        "CASE WHEN :con_evaluacion THEN coalesce(ev.incumplidas, 0) END"
    ),
    "promesas_pendientes_actuales": "CASE WHEN :con_evaluacion THEN coalesce(ev.pendientes, 0) END",
    "dias_desde_ultima_promesa": _dias("pr.ultima"),
}
"""De donde sale cada feature. Un conteo del lifecycle sin eventos es 0; una de pagos de una ventana
sin cobertura, o una de la evaluacion sin evaluacion conocida, es NULL; una distancia en dias sin
evento del cual medirla, tambien."""

assert set(EXPRESION) == {f.nombre for f in FEATURES}


def sql_canonico(nombre: str, tipo: Tipo) -> str:
    """La forma canonica de una feature en SQL: exactamente `features.texto`."""
    columna = f"x.{nombre}"
    if tipo == Tipo.TEXTO:
        return f"coalesce('s' || length({columna}) || ':' || {columna}, '{NULO}')"
    return f"coalesce(CAST({columna} AS text), '{NULO}')"


def _sql_features() -> tuple[str, ...]:
    valores = ",\n    ".join(
        f"CAST({EXPRESION[f.nombre]} AS {TIPO_SQL[f.tipo]}) AS {f.nombre}" for f in FEATURES
    )
    canonica = " || chr(10) || ".join(
        [f"'{VERSION_FEATURES}'"]
        + [f"'{f.nombre}=' || {sql_canonico(f.nombre, f.tipo)}" for f in FEATURES]
    )
    return (
        f"""
CREATE TEMP TABLE pf_feature ON COMMIT DROP AS
SELECT x.*, sha256(convert_to({canonica}, 'UTF8')) AS feature_hash
FROM (
  SELECT u.cuenta, u.cuenta_id,
    {valores}
  FROM pf_universo u
  LEFT JOIN pf_historia h ON h.cuenta = u.cuenta
  LEFT JOIN pf_anterior a ON a.cuenta = u.cuenta
  LEFT JOIN pf_hace_28 b ON b.cuenta = u.cuenta
  LEFT JOIN pf_f_pago pg ON pg.cuenta = u.cuenta
  LEFT JOIN pf_f_gestion g ON g.cuenta = u.cuenta
  LEFT JOIN pf_f_promesa pr ON pr.cuenta = u.cuenta
  LEFT JOIN pf_f_evaluacion ev ON ev.cuenta = u.cuenta
) x
""",
        "CREATE UNIQUE INDEX ON pf_feature (cuenta)",
    )


SQL_FEATURES = _sql_features()


# --- los labels -----------------------------------------------------------------------------------

SQL_LABELS = (
    # La recuperacion neta en (T, T+30] de las interpretaciones declaradas, conciliada con la
    # cuenta: un PAGO esta anulado si el REVERSO que lo anula esta en esas interpretaciones y llego
    # antes del final de la ventana.
    "CREATE TEMP TABLE pl_mov ON COMMIT DROP AS SELECT m.cuenta_canonica_id AS cuenta, "
    "sum(CASE WHEN m.tipo_movimiento = 'PAGO' AND rv.id IS NULL THEN m.monto_reportado "
    "WHEN m.tipo_movimiento = 'POSIBLE_REVERSO' THEN m.monto_reportado ELSE 0 END) AS neta "
    "FROM movimiento_economico_canonico m JOIN pf_universo u ON u.cuenta = m.cuenta_canonica_id "
    "LEFT JOIN movimiento_economico_canonico rv ON m.anulado_por_movimiento_id IS NOT NULL "
    "AND rv.movimiento_id = m.anulado_por_movimiento_id "
    "AND rv.ejecucion_motor_pagos_id = ANY(CAST(:interpretaciones AS integer[])) "
    "AND rv.fecha_recepcion < :fin_30_local "
    "WHERE m.ejecucion_motor_pagos_id = ANY(CAST(:interpretaciones AS integer[])) "
    "AND m.fecha_recepcion >= :fin_local AND m.fecha_recepcion < :fin_30_local "
    "GROUP BY m.cuenta_canonica_id",
    # La cuenta contactada de analytics/v1 en (T, T+14]: una gestion con contacto registrada hasta
    # label_known_at, sin una anulacion ocurrida antes del final de la ventana y registrada hasta
    # label_known_at.
    "CREATE TEMP TABLE pl_contacto ON COMMIT DROP AS SELECT DISTINCT g.cuenta_canonica_id "
    "AS cuenta FROM pf_universo u JOIN gestion_cobranza g ON g.cuenta_canonica_id = u.cuenta "
    "AND g.ocurrido_en >= :fin AND g.ocurrido_en < :fin_14 "
    f"AND g.nivel_contacto IN ({_CONTACTOS}) "
    "JOIN evento_lifecycle e ON e.id = g.evento_lifecycle_id "
    "AND e.registrado_en <= :label_known_at WHERE NOT "
    + _anulada("g.evento_lifecycle_id", conocida=":label_known_at", ocurrida_antes_de=":fin_14"),
    # La creacion de una promesa en (T, T+30], registrada hasta label_known_at, de una gestion
    # efectiva con la misma regla. Una cancelacion no la quita: el target es la creacion.
    "CREATE TEMP TABLE pl_promesa ON COMMIT DROP AS SELECT DISTINCT p.cuenta_canonica_id "
    "AS cuenta FROM pf_universo u JOIN promesa_pago p ON p.cuenta_canonica_id = u.cuenta "
    "JOIN evento_lifecycle e ON e.id = p.evento_lifecycle_id AND e.ocurrido_en >= :fin "
    "AND e.ocurrido_en < :fin_30 AND e.registrado_en <= :label_known_at "
    "JOIN gestion_cobranza g ON g.id = p.gestion_cobranza_id "
    "JOIN evento_lifecycle eg ON eg.id = g.evento_lifecycle_id "
    "AND eg.registrado_en <= :label_known_at WHERE NOT "
    + _anulada("g.evento_lifecycle_id", conocida=":label_known_at", ocurrida_antes_de=":fin_30"),
)

_COLUMNAS = ", ".join(f"f.{f.nombre}" for f in FEATURES)


def sql_dataset(*, pagos: bool, contacto: bool, promesa: bool) -> str:
    """Las features de un ancla con sus labels crudos, por cuenta, en el orden canonico del
    dataset: el de cuenta_id, un UUID (el mismo en cualquier base, sin depender de la collation).
    Un label CENSURADA o NO_DISPONIBLE sale NULL, nunca 0. Sin el id interno de la cuenta: el
    dataset y su hash solo nombran el cuenta_id publico."""
    return (
        f"SELECT f.cuenta_id, {_COLUMNAS}, encode(f.feature_hash, 'hex') AS feature_hash, "
        f"CASE WHEN {_literal(pagos)} THEN coalesce(lm.neta, 0) END AS neta_30d, "
        f"CASE WHEN {_literal(contacto)} THEN CAST(lc.cuenta IS NOT NULL AS integer) END "
        "AS contacto_14d, "
        f"CASE WHEN {_literal(promesa)} THEN CAST(lp.cuenta IS NOT NULL AS integer) END "
        "AS promesa_30d "
        "FROM pf_feature f "
        "LEFT JOIN pl_mov lm ON lm.cuenta = f.cuenta "
        "LEFT JOIN pl_contacto lc ON lc.cuenta = f.cuenta "
        "LEFT JOIN pl_promesa lp ON lp.cuenta = f.cuenta "
        "ORDER BY f.cuenta_id"
    )


def _literal(valor: bool) -> str:
    return "true" if valor else "false"


SQL_SCORING = (
    f"SELECT f.cuenta, f.cuenta_id, {_COLUMNAS}, encode(f.feature_hash, 'hex') AS feature_hash "
    "FROM pf_feature f ORDER BY f.cuenta_id"
)
"""Las features de cada cuenta de un corte, con su id interno (para publicar su prediccion), en el
orden canonico del scoring."""
