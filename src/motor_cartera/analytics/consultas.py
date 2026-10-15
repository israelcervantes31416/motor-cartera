"""Lo que la API y la linea de comandos leen de analytics/v1: el catalogo, las ejecuciones con su
manifiesto, sus agregados filtrados en la base, su resumen, sus embudos y sus controles.

Ninguna consulta lee una cuenta: solo agregados publicados. Los filtros de los agregados son
columnas y llaves de dimension permitidas, con parametros: nunca SQL, expresiones ni columnas
arbitrarias. Lo SUPRIMIDO se lee como se publico, sin ningun valor; un filtro por una llave de
dimension no devuelve lo que la celda suprimida ocultaba, porque la celda no lo tiene.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, text
from sqlmodel import Session, select

from motor_cartera.analytics.catalogo import (
    CONJUNTOS,
    METRICAS,
    VERSION_CATALOGO,
    VERSION_MOTOR,
    Conjunto,
    hash_catalogo,
)
from motor_cartera.analytics.politica import POLITICAS, hash_politica
from motor_cartera.db.modelos import (
    CorteCanonico,
    EjecucionAnalytics,
    EntradaAnalytics,
    TrabajoOrquestacion,
)

LLAVES_DE_DIMENSION = tuple(sorted({c for d in CONJUNTOS.values() for c in d.claves}))
"""Las unicas llaves de dimension por las que se filtra: las de algun conjunto del catalogo."""

ESCALA = Decimal("0.0000000001")


class EjecucionNoEncontrada(Exception):
    """No hay una ejecucion de analytics/v1 con ese analytics_run_id."""


# --- el catalogo ----------------------------------------------------------------------------------


def catalogo() -> dict[str, Any]:
    """El catalogo vigente con su version y su hash, la politica con el suyo y cada conjunto con
    sus llaves: lo que firma cada ejecucion."""
    politicas = [
        {
            "version": p.version,
            "hash": hash_politica(p),
            "min_group_size": p.min_group_size,
            "conjuntos_en_periodo": [c.value for c in p.conjuntos_en_periodo],
            "max_periodos": p.max_periodos,
            "escala_razon": p.escala_razon,
            "reglas": list(p.reglas),
        }
        for p in POLITICAS.values()
    ]
    return {
        "version": VERSION_CATALOGO,
        "hash": hash_catalogo(),
        "version_motor": VERSION_MOTOR,
        "politicas": politicas,
        "conjuntos": [
            {
                "conjunto": c.value,
                "llaves": list(CONJUNTOS[c].claves),
                "universos": sorted(u.value for u in CONJUNTOS[c].universos),
            }
            for c in Conjunto
        ],
        "metricas": [
            {
                **m.definicion(),
                "dimensiones_permitidas": [
                    {"conjunto": c.value, "llaves": list(CONJUNTOS[c].claves)} for c in m.conjuntos
                ],
                "version": VERSION_CATALOGO,
            }
            for m in METRICAS
        ],
    }


# --- las ejecuciones ------------------------------------------------------------------------------


@dataclass(frozen=True)
class EjecucionVista:
    ejecucion: EjecucionAnalytics
    corte_id: UUID
    trabajo_id: UUID | None
    entradas: list[EntradaAnalytics]


def _vistas(s: Session, ejecuciones: list[EjecucionAnalytics]) -> list[EjecucionVista]:
    """Cada ejecucion con su corte, su trabajo y sus entradas: tres consultas para toda la pagina,
    no tres por ejecucion."""
    if not ejecuciones:
        return []
    ids = [e.id for e in ejecuciones]
    cortes = dict(
        s.exec(
            select(CorteCanonico.id, CorteCanonico.corte_id).where(
                CorteCanonico.id.in_({e.corte_canonico_id for e in ejecuciones})
            )
        ).all()
    )
    trabajo = TrabajoOrquestacion
    trabajos = dict(
        s.exec(
            select(trabajo.ejecucion_analytics_id, trabajo.trabajo_id).where(
                trabajo.ejecucion_analytics_id.in_(ids)
            )
        ).all()
    )
    entradas: dict[int, list[EntradaAnalytics]] = {i: [] for i in ids}
    for entrada in s.exec(
        select(EntradaAnalytics)
        .where(EntradaAnalytics.ejecucion_analytics_id.in_(ids))
        .order_by(
            EntradaAnalytics.ejecucion_analytics_id,
            EntradaAnalytics.tipo_entrada,
            EntradaAnalytics.periodo_desde,
        )
    ).all():
        entradas[entrada.ejecucion_analytics_id].append(entrada)
    return [
        EjecucionVista(e, cortes[e.corte_canonico_id], trabajos.get(e.id), entradas[e.id])
        for e in ejecuciones
    ]


def obtener(s: Session, analytics_run_id: UUID) -> EjecucionVista:
    ejecucion = s.exec(
        select(EjecucionAnalytics).where(EjecucionAnalytics.analytics_run_id == analytics_run_id)
    ).one_or_none()
    if ejecucion is None:
        raise EjecucionNoEncontrada(analytics_run_id)
    return _vistas(s, [ejecucion])[0]


def listar(
    s: Session,
    *,
    corte: CorteCanonico | None,
    estado: str | None,
    as_of: date | None,
    desplazamiento: int,
    limite: int,
) -> tuple[int, list[EjecucionVista]]:
    """Las ejecuciones, la mas reciente primero: las de un corte por (corte, as_of, id)."""
    condiciones = []
    if corte is not None:
        condiciones.append(EjecucionAnalytics.corte_canonico_id == corte.id)
    if estado is not None:
        condiciones.append(EjecucionAnalytics.estado == estado)
    if as_of is not None:
        condiciones.append(EjecucionAnalytics.as_of == as_of)
    total = s.exec(select(func.count()).select_from(EjecucionAnalytics).where(*condiciones)).one()
    filas = s.exec(
        select(EjecucionAnalytics)
        .where(*condiciones)
        .order_by(EjecucionAnalytics.id.desc())
        .offset(desplazamiento)
        .limit(limite)
    ).all()
    return total, _vistas(s, list(filas))


# --- los agregados --------------------------------------------------------------------------------


@dataclass(frozen=True)
class FiltrosAgregados:
    """Lo unico por lo que se filtra: columnas y llaves de dimension del catalogo, con valores."""

    metrica: str | None = None
    vista: str | None = None
    dimension_set: str | None = None
    periodo_desde: date | None = None
    periodo_hasta: date | None = None
    estado_valor: str | None = None
    dimensiones: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class AgregadoVista:
    metrica_codigo: str
    vista: str
    dimension_set: str
    periodo_desde: date
    periodo_hasta: date
    periodo_parcial: bool
    dimensiones: dict[str, str]
    dimension_hash: str
    estado_valor: str
    poblacion_cuentas: int | None
    valor: Decimal | None
    numerador: Decimal | None
    denominador: Decimal | None
    motivos: list[dict]


def _donde(ejecucion_id: int, filtros: FiltrosAgregados) -> tuple[str, dict[str, Any]]:
    condiciones = ["ejecucion_analytics_id = :ejecucion"]
    parametros: dict[str, Any] = {"ejecucion": ejecucion_id}
    for columna, valor in (
        ("metrica_codigo", filtros.metrica),
        ("vista", filtros.vista),
        ("dimension_set", filtros.dimension_set),
        ("estado_valor", filtros.estado_valor),
    ):
        if valor is not None:
            condiciones.append(f"{columna} = :{columna}")
            parametros[columna] = valor
    if filtros.periodo_desde is not None:
        condiciones.append("periodo_desde >= :desde")
        parametros["desde"] = filtros.periodo_desde
    if filtros.periodo_hasta is not None:
        condiciones.append("periodo_hasta <= :hasta")
        parametros["hasta"] = filtros.periodo_hasta
    for i, (llave, valor) in enumerate(filtros.dimensiones):
        if llave not in LLAVES_DE_DIMENSION:  # pragma: no cover - la API ya lo valido
            raise ValueError(f"{llave} no es una llave de dimension del catalogo.")
        # La llave sale de una lista cerrada; el valor va como parametro.
        condiciones.append(f"dimensiones ->> '{llave}' = :dim_{i}")
        parametros[f"dim_{i}"] = valor
    return " AND ".join(condiciones), parametros


def agregados(
    s: Session,
    ejecucion: EjecucionAnalytics,
    filtros: FiltrosAgregados,
    *,
    desplazamiento: int,
    limite: int,
) -> tuple[int, list[AgregadoVista]]:
    """Una pagina de lo publicado, filtrada en la base y en el orden de su llave."""
    donde, parametros = _donde(ejecucion.id, filtros)
    total = s.execute(
        text(f"SELECT count(*) FROM agregado_analytics WHERE {donde}"), parametros
    ).scalar_one()
    filas = s.execute(
        text(
            "SELECT metrica_codigo, vista, dimension_set, periodo_desde, periodo_hasta, "
            "periodo_parcial, dimensiones, encode(dimension_hash, 'hex'), estado_valor, "
            "poblacion_cuentas, valor, numerador, denominador, motivos FROM agregado_analytics "
            f"WHERE {donde} ORDER BY metrica_codigo, vista, dimension_set, periodo_desde, "
            "dimension_hash OFFSET :desplazamiento LIMIT :limite"
        ),
        {**parametros, "desplazamiento": desplazamiento, "limite": limite},
    ).all()
    return total, [AgregadoVista(*f) for f in filas]


def todos_los_agregados(
    s: Session, ejecucion: EjecucionAnalytics, filtros: FiltrosAgregados
) -> list[AgregadoVista]:
    """Todo lo publicado que cumple los filtros, en el orden de su llave: lo que se exporta."""
    donde, parametros = _donde(ejecucion.id, filtros)
    filas = s.execute(
        text(
            "SELECT metrica_codigo, vista, dimension_set, periodo_desde, periodo_hasta, "
            "periodo_parcial, dimensiones, encode(dimension_hash, 'hex'), estado_valor, "
            "poblacion_cuentas, valor, numerador, denominador, motivos FROM agregado_analytics "
            f"WHERE {donde} ORDER BY metrica_codigo, vista, dimension_set, periodo_desde, "
            "dimension_hash"
        ),
        parametros,
    ).all()
    return [AgregadoVista(*f) for f in filas]


# --- el resumen y los embudos ---------------------------------------------------------------------

RESUMEN = (
    ("cuentas", "cartera.cuentas"),
    ("saldo_total", "cartera.saldo_total"),
    ("recuperacion_neta", "recuperacion.neta"),
    ("recuperacion_sobre_saldo_inicial", "recuperacion.sobre_saldo_inicial"),
    ("cuentas_gestionadas", "gestion.cuentas_gestionadas"),
    ("contactabilidad", "gestion.contactadas_sobre_gestionadas"),
    ("promesas_creadas", "promesa.creadas"),
    ("tasa_cumplimiento_promesas", "promesa.tasa_cumplimiento_total"),
    ("asociacion_unica", "atribucion.asociacion_unica"),
    ("campo_candidatas", "campo.candidatas"),
    ("campo_asignadas", "campo.asignadas"),
    ("ruteo_paradas_objetivo", "ruteo.paradas_objetivo"),
    ("ruteo_programadas", "ruteo.programadas"),
)
"""Los indicadores del resumen: un conjunto pequeno de metricas del TOTAL acumulado."""

EMBUDOS = {
    "CONTACTO": (
        ("COHORTE", "cartera.cuentas"),
        ("GESTIONADAS", "gestion.cuentas_gestionadas"),
        ("CONTACTADAS", "gestion.cuentas_contactadas"),
        ("CON_PROMESA", "promesa.cuentas_con_promesa"),
    ),
    "CAMPO": (
        ("CANDIDATAS", "campo.candidatas"),
        ("ASIGNADAS", "campo.asignadas"),
        ("PARADAS_PROGRAMADAS", "ruteo.programadas"),
    ),
}
"""Cada embudo es una cadena de conjuntos de cuentas anidados, del TOTAL acumulado: cada etapa esta
dentro de la anterior (los controles lo comprueban al publicar). Un pago no es una etapa: el
embudo no dice que una gestion produjo un pago."""


@dataclass(frozen=True)
class Indicador:
    nombre: str
    metrica: str
    estado_valor: str
    valor: Decimal | None
    numerador: Decimal | None
    denominador: Decimal | None
    motivos: list[dict]


def _totales(s: Session, ejecucion: EjecucionAnalytics, metricas: list[str]) -> dict[str, tuple]:
    filas = s.execute(
        text(
            "SELECT metrica_codigo, estado_valor, valor, numerador, denominador, motivos "
            "FROM agregado_analytics WHERE ejecucion_analytics_id = :e "
            "AND metrica_codigo = ANY(CAST(:metricas AS text[])) AND vista = 'ACUMULADA' "
            "AND dimension_set = 'TOTAL'"
        ),
        {"e": ejecucion.id, "metricas": metricas},
    ).all()
    return {f[0]: tuple(f[1:]) for f in filas}


def resumen(s: Session, ejecucion: EjecucionAnalytics) -> list[Indicador]:
    """Los indicadores del TOTAL acumulado, cada uno con su estado: un NO_DISPONIBLE no tiene
    valor, y no se vuelve cero."""
    totales = _totales(s, ejecucion, [m for _, m in RESUMEN])
    return [Indicador(nombre, metrica, *totales[metrica]) for nombre, metrica in RESUMEN]


@dataclass(frozen=True)
class Etapa:
    etapa: str
    metrica: str
    estado_valor: str
    cuentas: int | None
    pct_etapa_anterior: Decimal | None
    pct_cohorte: Decimal | None


def _razon(numerador: Decimal | None, denominador: Decimal | None) -> Decimal | None:
    if numerador is None or denominador is None or denominador == 0:
        return None
    return (numerador / denominador).quantize(ESCALA)


def embudos(s: Session, ejecucion: EjecucionAnalytics) -> dict[str, list[Etapa]]:
    """Cada embudo, etapa por etapa: sus cuentas, su porcentaje de la anterior y de la cohorte.
    Null cuando la etapa (o la anterior) no esta disponible, o cuando el denominador es cero."""
    metricas = sorted({m for etapas in EMBUDOS.values() for _, m in etapas})
    totales = _totales(s, ejecucion, metricas)
    _, cohorte, *_ = totales["cartera.cuentas"]
    resultado: dict[str, list[Etapa]] = {}
    for embudo, etapas in EMBUDOS.items():
        anterior: Decimal | None = None
        lista = []
        for i, (etapa, metrica) in enumerate(etapas):
            estado, valor, *_ = totales[metrica]
            cuentas = valor if estado == "DISPONIBLE" else None
            lista.append(
                Etapa(
                    etapa,
                    metrica,
                    estado,
                    None if cuentas is None else int(cuentas),
                    None if i == 0 else _razon(cuentas, anterior),
                    _razon(cuentas, cohorte),
                )
            )
            anterior = cuentas
        resultado[embudo] = lista
    return resultado


# --- los controles --------------------------------------------------------------------------------


@dataclass(frozen=True)
class ControlVista:
    codigo_control: str
    estado: str
    esperado: Decimal | None
    observado: Decimal | None
    diferencia: Decimal | None
    detalle: str


def controles(s: Session, ejecucion: EjecucionAnalytics) -> list[ControlVista]:
    """Todos sus controles, en el orden de su codigo: los de una EXITOSA y los de una FALLIDA
    porque un control no cuadro."""
    filas = s.execute(
        text(
            "SELECT codigo_control, estado, esperado, observado, diferencia, detalle "
            "FROM control_analytics WHERE ejecucion_analytics_id = :e ORDER BY codigo_control"
        ),
        {"e": ejecucion.id},
    ).all()
    return [ControlVista(*f) for f in filas]
