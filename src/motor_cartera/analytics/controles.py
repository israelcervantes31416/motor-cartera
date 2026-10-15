"""Los controles de reconciliacion de una ejecucion de analytics/v1.

Cada control compara lo que se esperaba con lo que se observo, exacto: en conteos y en centavos, sin
tolerancia y sin floats. Se calculan sobre los agregados antes de suprimir nada, en la transaccion
que los va a publicar, y si uno falla la ejecucion queda FALLIDA (CONTROL_FALLIDO) sin publicar un
solo agregado; sus controles se guardan, para auditarla. El detalle de un control dice que comparo,
con codigos y conteos: nunca una cuenta.

Las familias:

- integridad de la lectura: la cohorte es el corte, y ningun hecho se duplico al leerlo (ni un
  movimiento, ni una gestion, ni una promesa, ni una atribucion);
- reconciliacion: cada conjunto de dimensiones excluyente suma su total (cuentas, saldo,
  recuperacion en centavos, decisiones, gestiones, candidatas, paradas, rutas y distancia), cada
  periodo suma el acumulado, y cada familia cuadra con la ejecucion que leyo (geografia,
  territorial/v2, campo/v2, ruteo/v2, decision/v2);
- semantica: la neta es pagos mas posibles reversos, las clases de la atribucion suman lo evaluado y
  sus montos los pagos vigentes, los estados de las promesas suman lo evaluado, y los embudos son
  conjuntos anidados.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import text
from sqlmodel import Session

from motor_cartera.analytics.catalogo import Conjunto, Fuente, Universo
from motor_cartera.analytics.sql import SQL_REVERSOS_INCOHERENTES


@dataclass(frozen=True)
class Control:
    codigo: str
    esperado: Decimal
    observado: Decimal
    detalle: str

    @property
    def ok(self) -> bool:
        return self.esperado == self.observado


def _d(valor) -> Decimal:
    return Decimal(0) if valor is None else Decimal(valor)


@dataclass(frozen=True)
class Contexto:
    """Lo que los controles necesitan saber de la ejecucion, sin leerlo otra vez."""

    cuentas_cohorte: int
    declaradas: frozenset[Fuente]
    ids: dict[Fuente, int]
    """El id interno de cada entrada declarada que no es por ventana."""
    periodos: bool
    """Si hay vista PERIODO."""
    parametros: dict
    """Los de las sentencias del motor: el final de as_of, entre otros."""


def _uno(s: Session, sql: str, parametros: dict | None = None):
    return s.execute(text(sql), parametros or {}).one()


def _por_conjunto(s: Session, universo: Universo, componente: str) -> dict[str, Decimal]:
    filas = s.execute(
        text(
            f"SELECT conjunto, sum({componente}) FROM an_valor WHERE universo = :u "
            "AND vista = 'ACUMULADA' GROUP BY conjunto"
        ),
        {"u": universo.value},
    ).all()
    return {conjunto: _d(suma) for conjunto, suma in filas}


def _sumas(
    s: Session, universo: Universo, componente: str, conjuntos: tuple[Conjunto, ...], prefijo: str
) -> list[Control]:
    """Cada conjunto excluyente del universo suma exactamente su TOTAL."""
    sumas = _por_conjunto(s, universo, componente)
    total = sumas.get(Conjunto.TOTAL.value, Decimal(0))
    return [
        Control(
            f"{prefijo}.{componente}.por_{conjunto.value.lower()}",
            total,
            sumas[conjunto.value],
            f"sum({componente}) de las celdas de {conjunto.value} contra TOTAL, en "
            f"{universo.value}",
        )
        for conjunto in conjuntos
        if conjunto.value in sumas
    ]


def _periodos(s: Session, universo: Universo, componente: str, prefijo: str) -> Control:
    acumulado, periodos = _uno(
        s,
        f"SELECT (SELECT sum({componente}) FROM an_valor WHERE universo = :u AND conjunto = "
        f"'TOTAL' AND vista = 'ACUMULADA'), (SELECT sum({componente}) FROM an_valor WHERE "
        "universo = :u AND conjunto = 'TOTAL' AND vista = 'PERIODO')",
        {"u": universo.value},
    )
    return Control(
        f"{prefijo}.{componente}.por_periodo",
        _d(acumulado),
        _d(periodos),
        f"sum({componente}) de los periodos contra la vista ACUMULADA, en TOTAL",
    )


def _total(s: Session, universo: Universo, *componentes: str) -> list[Decimal]:
    fila = _uno(
        s,
        "SELECT "
        + ", ".join(componentes)
        + " FROM an_valor WHERE universo = :u AND conjunto = 'TOTAL' AND vista = 'ACUMULADA'",
        {"u": universo.value},
    )
    return [_d(v) for v in fila]


COHORTE_EXCLUYENTES = (
    Conjunto.PRODUCTO,
    Conjunto.ATRASO_BUCKET,
    Conjunto.ENTIDAD,
    Conjunto.MUNICIPIO,
    Conjunto.PRODUCTO_X_ATRASO,
    Conjunto.PRIORIDAD_DECISION,
    Conjunto.CANAL_DECISION,
    Conjunto.CANAL_X_PRIORIDAD,
    Conjunto.BASE,
    Conjunto.BASE_X_PRIORIDAD,
)
"""Los conjuntos de la cohorte: cada cuenta cae en exactamente una celda de cada uno."""


def calcular(s: Session, ctx: Contexto) -> list[Control]:
    """Los controles de una ejecucion, sobre las tablas temporales que ya armo el motor."""
    controles: list[Control] = []
    declaradas = ctx.declaradas
    cohorte, unicas = _uno(s, "SELECT count(*), count(DISTINCT cuenta) FROM an_cohorte")
    controles += [
        Control(
            "cohorte.cuentas",
            Decimal(ctx.cuentas_cohorte),
            Decimal(cohorte),
            "las cuentas leidas de los snapshots del corte contra las que el corte dice tener",
        ),
        Control(
            "cohorte.cuentas_sin_duplicar",
            Decimal(cohorte),
            Decimal(unicas),
            "una cuenta, una vez en la cohorte",
        ),
    ]
    controles += _sumas(s, Universo.COHORTE, "cuentas", COHORTE_EXCLUYENTES, "cartera")
    controles += _sumas(s, Universo.COHORTE, "saldo_total", COHORTE_EXCLUYENTES, "cartera")
    controles += _lectura(s)
    if Fuente.MOTOR_PAGOS in declaradas:
        controles += _pagos(s, ctx)
    controles += _gestiones(s, ctx)
    if Fuente.EVALUACION_PROMESAS in declaradas:
        controles += _promesas(s)
    if Fuente.ATRIBUCION in declaradas:
        controles += _atribucion(s, ctx)
    if Fuente.DECISION_V2 in declaradas:
        controles += _decision(s, ctx)
    if Fuente.GEOGRAFIA in declaradas:
        controles += _geografia(s, ctx)
    if Fuente.TERRITORIAL_V2 in declaradas:
        controles += _territorial(s, ctx)
    if Fuente.CAMPO_V2 in declaradas:
        controles += _campo(s, ctx)
    if Fuente.RUTEO_V2 in declaradas:
        controles += _ruteo(s, ctx)
    controles += _embudos(s, ctx)
    return controles


def _lectura(s: Session) -> list[Control]:
    """Ningun hecho se multiplico: lo que suman los hechos por (cuenta, periodo) es lo que se leyo,
    fila por fila, de cada dominio."""
    movimientos, distintos = _uno(s, "SELECT count(*), count(DISTINCT id) FROM an_mov")
    atrib, atrib_distintas = _uno(s, "SELECT count(*), count(DISTINCT id) FROM an_atrib")
    hechos = _uno(
        s,
        "SELECT coalesce(sum(rec_movimientos), 0), coalesce(sum(g_gestiones), 0), "
        "coalesce(sum(p_creadas), 0), coalesce(sum(a_evaluados), 0) FROM an_pc",
    )
    leidos = _uno(
        s,
        "SELECT (SELECT count(*) FROM an_mov WHERE en_cohorte), (SELECT count(*) FROM an_gestion), "
        "(SELECT count(*) FROM an_promesa), (SELECT count(*) FROM an_atrib)",
    )
    return [
        Control(
            "lectura.movimientos_sin_duplicar",
            Decimal(movimientos),
            Decimal(distintos),
            "un movimiento de las interpretaciones declaradas, una vez",
        ),
        Control(
            "lectura.atribuciones_sin_duplicar",
            Decimal(atrib),
            Decimal(atrib_distintas),
            "un pago, un resultado de atribucion",
        ),
        *(
            Control(
                f"lectura.{nombre}_sin_multiplicar",
                _d(leido),
                _d(sumado),
                f"lo que suman los hechos por cuenta y periodo contra los {nombre} leidos",
            )
            for nombre, leido, sumado in zip(
                ("movimientos", "gestiones", "promesas", "atribuciones"),
                leidos,
                hechos,
                strict=True,
            )
        ),
    ]


def _pagos(s: Session, ctx: Contexto) -> list[Control]:
    controles = _sumas(s, Universo.COHORTE, "rec_neta", COHORTE_EXCLUYENTES, "recuperacion")
    if ctx.periodos:
        controles.append(_periodos(s, Universo.COHORTE, "rec_neta", "recuperacion"))
        controles.append(_periodos(s, Universo.COHORTE, "rec_movimientos", "recuperacion"))
    neta, pagos, posibles, movimientos = _total(
        s, Universo.COHORTE, "rec_neta", "rec_pagos", "rec_posibles_reversos", "rec_movimientos"
    )
    universo, sin_cuenta, fuera = _uno(
        s,
        "SELECT count(*), count(*) FILTER (WHERE cuenta IS NULL), "
        "count(*) FILTER (WHERE cuenta IS NOT NULL AND NOT en_cohorte) FROM an_mov",
    )
    (incoherentes,) = _uno(s, SQL_REVERSOS_INCOHERENTES, ctx.parametros)
    return [
        *controles,
        Control(
            "recuperacion.neta_es_pagos_mas_posibles_reversos",
            pagos + posibles,
            neta,
            "recuperacion.neta contra pagos vigentes mas posibles reversos, en TOTAL",
        ),
        Control(
            "pagos.universo_de_movimientos",
            Decimal(universo),
            movimientos + Decimal(sin_cuenta) + Decimal(fuera),
            "los movimientos del horizonte: de la cohorte, sin cuenta y de otras cuentas",
        ),
        Control(
            "pagos.reversos_coherentes",
            Decimal(0),
            Decimal(incoherentes),
            "reversos cuya interpretacion y la de su pago no se corresponden",
        ),
    ]


def _gestiones(s: Session, ctx: Contexto) -> list[Control]:
    total = _total(s, Universo.COHORTE, "g_gestiones")[0]
    controles = []
    for conjunto in (Conjunto.CANAL_GESTION, Conjunto.MEDIO_GESTION):
        sumas = _por_conjunto(s, Universo.GESTION, "g_gestiones")
        controles.append(
            Control(
                f"gestion.g_gestiones.por_{conjunto.value.lower()}",
                total,
                sumas.get(conjunto.value, Decimal(0)),
                f"las gestiones de las celdas de {conjunto.value} contra TOTAL",
            )
        )
    if ctx.periodos:
        controles.append(_periodos(s, Universo.COHORTE, "g_gestiones", "gestion"))
        controles.append(_periodos(s, Universo.COHORTE, "p_creadas", "promesa"))
    return controles


def _promesas(s: Session) -> list[Control]:
    (
        creadas,
        evaluadas,
        sin_evaluacion,
        cumplidas,
        parciales,
        incumplidas,
        pendientes,
        canceladas,
        no_evaluables,
    ) = _total(
        s,
        Universo.COHORTE,
        "p_creadas",
        "p_evaluadas",
        "p_sin_evaluacion",
        "p_cumplidas",
        "p_parciales",
        "p_incumplidas",
        "p_pendientes",
        "p_canceladas",
        "p_no_evaluables",
    )
    return [
        Control(
            "promesa.estados_suman_evaluadas",
            evaluadas,
            cumplidas + parciales + incumplidas + pendientes + canceladas + no_evaluables,
            "los seis estados de evaluacion-promesa/v1 contra las evaluadas",
        ),
        Control(
            "promesa.evaluadas_y_sin_evaluacion_suman_creadas",
            creadas,
            evaluadas + sin_evaluacion,
            "las creadas contra las evaluadas mas las que no tienen resultado",
        ),
    ]


def _atribucion(s: Session, ctx: Contexto) -> list[Control]:
    evaluados, unica, ambigua, sin, monto_unica, monto_ambiguo, monto_sin = _total(
        s,
        Universo.COHORTE,
        "a_evaluados",
        "a_asociacion_unica",
        "a_ambigua",
        "a_sin_gestion",
        "a_monto_asociacion_unica",
        "a_monto_ambiguo",
        "a_monto_sin_gestion",
    )
    pagos, monto = _uno(
        s,
        "SELECT count(*), coalesce(sum(CASE WHEN anulado THEN 0 ELSE monto END), 0) FROM an_mov "
        "WHERE en_cohorte AND tipo = 'PAGO' AND mes IN (SELECT periodo_desde FROM an_atribucion)",
    )
    return [
        Control(
            "atribucion.clases_suman_evaluados",
            evaluados,
            unica + ambigua + sin,
            "ASOCIACION_UNICA + AMBIGUA + SIN_GESTION_CANDIDATA contra los evaluados",
        ),
        Control(
            "atribucion.cubre_los_pagos",
            Decimal(pagos),
            evaluados,
            "los PAGO de la cohorte de los meses con atribucion declarada contra los evaluados",
        ),
        Control(
            "atribucion.montos_suman_pagos_vigentes",
            _d(monto),
            monto_unica + monto_ambiguo + monto_sin,
            "los montos de las tres clases contra los PAGO vigentes de esos meses, en centavos",
        ),
    ]


def _contra_la_ejecucion(
    s: Session, universo: Universo, columnas: dict[str, str], tabla: str, ejecucion: int, prefijo
) -> list[Control]:
    """Lo que analytics conto de cada estado contra lo que publico la ejecucion que lo leyo."""
    propios = _total(s, universo, *columnas)
    suyos = _uno(
        s,
        f"SELECT {', '.join(columnas.values())} FROM {tabla} WHERE id = :e",
        {"e": ejecucion},
    )
    return [
        Control(
            f"{prefijo}.{componente}.contra_la_ejecucion",
            _d(suyo),
            propio,
            f"{componente} contra {tabla}.{columna}",
        )
        for (componente, columna), propio, suyo in zip(
            columnas.items(), propios, suyos, strict=True
        )
    ]


def _decision(s: Session, ctx: Contexto) -> list[Control]:
    (cuentas,) = _total(s, Universo.COHORTE, "d_cuentas")
    return [
        Control(
            "decision.cuentas_son_la_cohorte",
            Decimal(ctx.cuentas_cohorte),
            cuentas,
            "las cuentas decididas contra las de la cohorte",
        ),
        *_sumas(
            s,
            Universo.COHORTE,
            "d_cuentas",
            (Conjunto.PRIORIDAD_DECISION, Conjunto.CANAL_DECISION),
            "decision",
        ),
        *_contra_la_ejecucion(
            s,
            Universo.COHORTE,
            {"d_cuentas": "cuentas_decididas"},
            "ejecucion_decision_v2",
            ctx.ids[Fuente.DECISION_V2],
            "decision",
        ),
    ]


_ESTADOS_GEOGRAFIA = {
    "geo_validas": "validas",
    "geo_discrepancias": "discrepantes",
    "geo_sin_coordenadas": "sin_coordenadas",
    "geo_incompletas": "incompletas",
    "geo_invalidas": "invalidas",
    "geo_fuera_referencia": "fuera_cobertura",
    "geo_ambiguas": "ambiguas",
}
_ESTADOS_TERRITORIAL = {
    "t_asignadas": "asignadas",
    "t_revision": "revision_geografica",
    "t_sin_base": "sin_base",
    "t_ambiguas": "ambiguas",
    "t_fuera_cobertura": "fuera_cobertura",
}


def _estados(
    s: Session,
    ctx: Contexto,
    prefijo: str,
    total: str,
    estados: dict[str, str],
    tabla: str,
    fuente: Fuente,
) -> list[Control]:
    valores = _total(s, Universo.COHORTE, total, *estados)
    return [
        Control(
            f"{prefijo}.estados_suman_la_cohorte",
            Decimal(ctx.cuentas_cohorte),
            sum(valores[1:], Decimal(0)),
            f"los estados de {tabla} contra las cuentas de la cohorte",
        ),
        Control(
            f"{prefijo}.cuentas_son_la_cohorte",
            Decimal(ctx.cuentas_cohorte),
            valores[0],
            f"las cuentas con resultado en {tabla} contra las de la cohorte",
        ),
        *_contra_la_ejecucion(s, Universo.COHORTE, estados, tabla, ctx.ids[fuente], prefijo),
    ]


def _geografia(s: Session, ctx: Contexto) -> list[Control]:
    return _estados(
        s,
        ctx,
        "geografia",
        "geo_cuentas",
        _ESTADOS_GEOGRAFIA,
        "ejecucion_geografia",
        Fuente.GEOGRAFIA,
    )


def _territorial(s: Session, ctx: Contexto) -> list[Control]:
    return _estados(
        s,
        ctx,
        "territorial",
        "t_cuentas",
        _ESTADOS_TERRITORIAL,
        "ejecucion_territorial_v2",
        Fuente.TERRITORIAL_V2,
    )


def _campo(s: Session, ctx: Contexto) -> list[Control]:
    candidatas, asignadas, revision, sin_gestor, sin_capacidad = _total(
        s,
        Universo.CAMPO,
        "c_candidatas",
        "c_asignadas",
        "c_revision_territorial",
        "c_sin_gestor",
        "c_sin_capacidad",
    )
    conjuntos = (
        Conjunto.PRIORIDAD_DECISION,
        Conjunto.BASE,
        Conjunto.BASE_X_PRIORIDAD,
        Conjunto.ESTADO_CAMPO,
        Conjunto.GESTOR_CAMPO,
    )
    return [
        Control(
            "campo.estados_suman_candidatas",
            candidatas,
            asignadas + revision + sin_gestor + sin_capacidad,
            "ASIGNADA + REVISION_TERRITORIAL + SIN_GESTOR_DISPONIBLE + SIN_CAPACIDAD contra las "
            "candidatas",
        ),
        *_sumas(s, Universo.CAMPO, "c_candidatas", conjuntos, "campo"),
        *_sumas(
            s,
            Universo.CAMPO,
            "c_capacidad_total",
            (Conjunto.BASE, Conjunto.GESTOR_CAMPO),
            "campo",
        ),
        *_contra_la_ejecucion(
            s,
            Universo.CAMPO,
            {
                "c_candidatas": "candidatas_campo",
                "c_asignadas": "asignadas",
                "c_revision_territorial": "revision_territorial",
                "c_sin_gestor": "sin_gestor",
                "c_sin_capacidad": "sin_capacidad",
                "c_rebalanceadas": "rebalanceadas",
                "c_capacidad_total": "capacidad_total",
                "c_capacidad_utilizada": "capacidad_utilizada",
            },
            "ejecucion_campo_v2",
            ctx.ids[Fuente.CAMPO_V2],
            "campo",
        ),
    ]


def _ruteo(s: Session, ctx: Contexto) -> list[Control]:
    paradas, programadas, sin_tiempo, no_enrutables, no_base, rutas, *estados = _total(
        s,
        Universo.RUTEO,
        "r_paradas",
        "r_programadas",
        "r_sin_tiempo",
        "r_no_enrutables",
        "r_no_enrutables_base",
        "r_rutas",
        "r_rutas_completas",
        "r_rutas_parciales",
        "r_rutas_sin_paradas",
        "r_bases_no_enrutables",
        "r_rutas_sin_jornada",
    )
    return [
        Control(
            "ruteo.estados_suman_paradas",
            paradas,
            programadas + sin_tiempo + no_enrutables + no_base,
            "PROGRAMADA + SIN_TIEMPO + NO_ENRUTABLE + NO_ENRUTABLE_BASE contra las paradas",
        ),
        Control(
            "ruteo.estados_suman_rutas",
            rutas,
            sum(estados, Decimal(0)),
            "los cinco estados de una ruta contra las rutas",
        ),
        *_sumas(
            s,
            Universo.RUTEO,
            "r_paradas",
            (
                Conjunto.BASE,
                Conjunto.GESTOR_CAMPO,
                Conjunto.PRIORIDAD_DECISION,
                Conjunto.ESTADO_RUTEO,
            ),
            "ruteo",
        ),
        *_sumas(s, Universo.RUTEO, "r_rutas", (Conjunto.BASE, Conjunto.GESTOR_CAMPO), "ruteo"),
        *_sumas(
            s, Universo.RUTEO, "r_distancia_m", (Conjunto.BASE, Conjunto.GESTOR_CAMPO), "ruteo"
        ),
        *_contra_la_ejecucion(
            s,
            Universo.RUTEO,
            {
                "r_paradas": "paradas_objetivo",
                "r_programadas": "programadas",
                "r_sin_tiempo": "sin_tiempo",
                "r_no_enrutables": "no_enrutables",
                "r_no_enrutables_base": "no_enrutables_base",
                "r_rutas": "gestores_objetivo",
                "r_rutas_completas": "rutas_completas",
                "r_rutas_parciales": "rutas_parciales",
                "r_distancia_m": "distancia_total_m",
                "r_vial_s": "duracion_vial_total_s",
                "r_servicio_s": "duracion_servicio_total_s",
                "r_espera_s": "duracion_espera_total_s",
            },
            "ejecucion_ruteo_v2",
            ctx.ids[Fuente.RUTEO_V2],
            "ruteo",
        ),
    ]


def _embudos(s: Session, ctx: Contexto) -> list[Control]:
    """Los embudos son conjuntos de cuentas anidados, no sumas de eventos: cada etapa esta dentro
    de la anterior, contado cuenta por cuenta."""
    promesa_sin_contacto, contacto_sin_gestion = _uno(
        s,
        "SELECT count(*) FILTER (WHERE p_creadas > 0 AND g_contactada = 0), "
        "count(*) FILTER (WHERE g_contactada = 1 AND g_gestiones = 0) FROM an_ac",
    )
    controles = [
        Control(
            "embudo.contacto.con_promesa_dentro_de_contactadas",
            Decimal(0),
            Decimal(promesa_sin_contacto),
            "cuentas con promesa en el horizonte sin una gestion efectiva con contacto",
        ),
        Control(
            "embudo.contacto.contactadas_dentro_de_gestionadas",
            Decimal(0),
            Decimal(contacto_sin_gestion),
            "cuentas contactadas sin gestion efectiva",
        ),
    ]
    if Fuente.RUTEO_V2 in ctx.declaradas:
        (fuera,) = _uno(
            s,
            "SELECT count(*) FROM an_parada p LEFT JOIN an_campo a ON a.cuenta = p.cuenta "
            "WHERE a.cuenta IS NULL OR a.estado <> 'ASIGNADA'",
        )
        controles.append(
            Control(
                "embudo.campo.paradas_dentro_de_asignadas",
                Decimal(0),
                Decimal(fuera),
                "paradas de ruteo/v2 cuya cuenta no esta ASIGNADA en el plan declarado",
            )
        )
    return controles
