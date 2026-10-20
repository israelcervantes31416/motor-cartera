"""Lo que la API y el CLI leen de optimization/v1: las configuraciones, las ejecuciones, sus
resultados por cuenta (filtrados y paginados en la base), el uso de cada pool, cada restriccion,
la explicacion de una cuenta y los planes de una cuenta.

Todo se busca por su identificador publico; el id interno no sale de la base. Un resultado sale
con el cuenta_id de su cuenta y con codigos (de pool, de base, de gestor), nunca con un dato
personal ni una coordenada.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, text
from sqlmodel import Session, col, select

from motor_cartera.db.modelos import (
    CorteCanonico,
    EjecucionCampoV2,
    EjecucionDecisionV2,
    EjecucionOptimizacion,
    EjecucionRuteoV2,
    EjecucionScoringPredictivo,
    PoolCapacidadOptimizacion,
    ResultadoRestriccionOptimizacion,
    TrabajoOrquestacion,
    VersionConfiguracionOptimizacion,
    VersionModeloPredictivo,
    id_de,
)
from motor_cartera.db.sesion import seleccionar
from motor_cartera.invariantes import presente
from motor_cartera.optimizacion import configuracion as configuracion_
from motor_cartera.optimizacion.politica import (
    AVISO,
    EXPLICACION_DEL_MOTIVO,
    Dimension,
    EstadoCuenta,
    Motivo,
)


class NoEncontrado(Exception):
    """No existe lo que se pidio con ese identificador publico."""


# --- las configuraciones --------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfiguracionVista:
    version: VersionConfiguracionOptimizacion
    contenido: configuracion_.ConfiguracionOptimizacion


def obtener_configuracion(s: Session, configuracion_id: UUID) -> ConfiguracionVista:
    v = s.exec(
        select(VersionConfiguracionOptimizacion).where(
            VersionConfiguracionOptimizacion.configuracion_id == configuracion_id
        )
    ).one_or_none()
    if v is None:
        raise NoEncontrado(configuracion_id)
    return ConfiguracionVista(v, configuracion_.configuracion_de(s, v))


def listar_configuraciones(
    s: Session, *, estado: str | None, desplazamiento: int, limite: int
) -> tuple[int, list[VersionConfiguracionOptimizacion]]:
    filtros = [] if estado is None else [VersionConfiguracionOptimizacion.estado == estado]
    total = s.exec(
        select(func.count()).select_from(VersionConfiguracionOptimizacion).where(*filtros)
    ).one()
    filas = s.exec(
        select(VersionConfiguracionOptimizacion)
        .where(*filtros)
        .order_by(col(VersionConfiguracionOptimizacion.id).desc())
        .offset(desplazamiento)
        .limit(limite)
    ).all()
    return total, list(filas)


# --- las ejecuciones ------------------------------------------------------------------------------


@dataclass(frozen=True)
class EjecucionVista:
    ejecucion: EjecucionOptimizacion
    scoring_run_id: UUID
    modelo_version_id: UUID
    decision_run_id: UUID
    ruteo_run_id: UUID | None
    campo_run_id: UUID | None
    configuracion_id: UUID
    corte_id: UUID
    fecha_corte: date
    trabajo_id: UUID | None


def _vistas(s: Session, ejecuciones: list[EjecucionOptimizacion]) -> list[EjecucionVista]:
    """Las vistas de varias ejecuciones, en su orden, con una sola consulta de los identificadores
    publicos de lo que declararon: no una por ejecucion."""
    if not ejecuciones:
        return []
    filas = s.exec(
        seleccionar(
            col(EjecucionOptimizacion.id),
            col(EjecucionScoringPredictivo.scoring_run_id),
            col(VersionModeloPredictivo.modelo_version_id),
            col(EjecucionDecisionV2.decision_run_id),
            col(EjecucionRuteoV2.ruteo_run_id),
            col(EjecucionCampoV2.campo_run_id),
            col(VersionConfiguracionOptimizacion.configuracion_id),
            col(CorteCanonico.corte_id),
            col(CorteCanonico.fecha_corte),
            col(TrabajoOrquestacion.trabajo_id),
        )
        .join(
            EjecucionScoringPredictivo,
            col(EjecucionScoringPredictivo.id)
            == EjecucionOptimizacion.ejecucion_scoring_predictivo_id,
        )
        .join(
            VersionModeloPredictivo,
            col(VersionModeloPredictivo.id)
            == EjecucionScoringPredictivo.version_modelo_predictivo_id,
        )
        .join(
            EjecucionDecisionV2,
            col(EjecucionDecisionV2.id) == EjecucionOptimizacion.ejecucion_decision_v2_id,
        )
        .outerjoin(
            EjecucionRuteoV2,
            col(EjecucionRuteoV2.id) == EjecucionOptimizacion.ejecucion_ruteo_v2_id,
        )
        .outerjoin(
            EjecucionCampoV2,
            col(EjecucionCampoV2.id) == EjecucionOptimizacion.ejecucion_campo_v2_id,
        )
        .join(
            VersionConfiguracionOptimizacion,
            col(VersionConfiguracionOptimizacion.id)
            == EjecucionOptimizacion.version_configuracion_optimizacion_id,
        )
        .join(CorteCanonico, col(CorteCanonico.id) == EjecucionOptimizacion.corte_canonico_id)
        .outerjoin(
            TrabajoOrquestacion,
            col(TrabajoOrquestacion.ejecucion_optimizacion_id) == EjecucionOptimizacion.id,
        )
        .where(col(EjecucionOptimizacion.id).in_([e.id for e in ejecuciones]))
    ).all()
    por_id = {f[0]: f[1:] for f in filas}
    return [EjecucionVista(e, *por_id[e.id]) for e in ejecuciones]


def obtener_ejecucion(s: Session, optimizacion_run_id: UUID) -> EjecucionVista:
    e = s.exec(
        select(EjecucionOptimizacion).where(
            EjecucionOptimizacion.optimizacion_run_id == optimizacion_run_id
        )
    ).one_or_none()
    if e is None:
        raise NoEncontrado(optimizacion_run_id)
    return _vistas(s, [e])[0]


def listar_ejecuciones(
    s: Session,
    *,
    estado: str | None,
    configuracion: VersionConfiguracionOptimizacion | None,
    scoring: EjecucionScoringPredictivo | None,
    desplazamiento: int,
    limite: int,
) -> tuple[int, list[EjecucionVista]]:
    filtros = []
    if estado is not None:
        filtros.append(EjecucionOptimizacion.estado == estado)
    if configuracion is not None:
        filtros.append(
            EjecucionOptimizacion.version_configuracion_optimizacion_id == configuracion.id
        )
    if scoring is not None:
        filtros.append(EjecucionOptimizacion.ejecucion_scoring_predictivo_id == scoring.id)
    total = s.exec(select(func.count()).select_from(EjecucionOptimizacion).where(*filtros)).one()
    filas = s.exec(
        select(EjecucionOptimizacion)
        .where(*filtros)
        .order_by(col(EjecucionOptimizacion.id).desc())
        .offset(desplazamiento)
        .limit(limite)
    ).all()
    return total, _vistas(s, list(filas))


# --- los resultados por cuenta --------------------------------------------------------------------


@dataclass(frozen=True)
class FiltrosResultados:
    """Una lista blanca: ningun otro filtro se acepta."""

    estado: str | None = None
    canal: str | None = None
    """El canal asignado."""
    pool: str | None = None
    """El codigo de un pool de la configuracion de la ejecucion."""
    producto: str | None = None
    prioridad: str | None = None
    base: str | None = None
    """El codigo de una base."""


@dataclass(frozen=True)
class ResultadoVista:
    cuenta_id: UUID
    estado: str
    motivo: str
    canal_decision: str
    canal_asignado: str | None
    pool: str | None
    prioridad: str
    producto: str
    atraso_bucket: str
    base: str | None
    gestor: str | None
    orden_ruteo_original: int | None
    valor_oportunidad_centavos: int
    p_contacto_14d: Decimal
    unidades_recurso: int | None
    retenida: bool


COLUMNAS_DEL_RESULTADO = (
    "SELECT c.cuenta_id, r.estado, r.motivo, r.canal_decision, r.canal_asignado, k.codigo, "
    "r.prioridad, r.producto, r.atraso_bucket, b.codigo, g.codigo, r.orden_ruteo_original, "
    "r.valor_oportunidad_centavos, r.p_contacto_14d, r.unidades_recurso, r.retenida "
)
UNIONES_DEL_RESULTADO = (
    "JOIN cuenta_canonica c ON c.id = r.cuenta_canonica_id "
    "LEFT JOIN pool_capacidad_optimizacion k ON k.id = r.pool_capacidad_optimizacion_id "
    "LEFT JOIN base_operativa b ON b.id = r.base_operativa_id "
    "LEFT JOIN gestor_campo g ON g.id = r.gestor_campo_id "
)


class FiltroDesconocido(ValueError):
    """Un pool o una base que no existen en la ejecucion: no es una lista vacia."""


def resultados(
    s: Session,
    ejecucion: EjecucionOptimizacion,
    filtros: FiltrosResultados,
    *,
    desplazamiento: int,
    limite: int,
) -> tuple[int, list[ResultadoVista]]:
    """Una pagina de los resultados, filtrados en la base, en el orden de la cuenta. Sin filtros,
    el total es lo que publico la ejecucion, todo o nada: no se cuenta otra vez."""
    condiciones = ["r.ejecucion_optimizacion_id = :e"]
    parametros: dict = {"e": ejecucion.id}
    if filtros.estado is not None:
        condiciones.append("r.estado = :estado")
        parametros["estado"] = filtros.estado
    if filtros.canal is not None:
        condiciones.append("r.canal_asignado = :canal")
        parametros["canal"] = filtros.canal
    if filtros.pool is not None:
        pool = s.exec(
            select(PoolCapacidadOptimizacion.id).where(
                PoolCapacidadOptimizacion.version_configuracion_optimizacion_id
                == ejecucion.version_configuracion_optimizacion_id,
                PoolCapacidadOptimizacion.codigo == filtros.pool,
            )
        ).one_or_none()
        if pool is None:
            raise FiltroDesconocido(
                f"La configuracion de la ejecucion no tiene el pool {filtros.pool}."
            )
        condiciones.append("r.pool_capacidad_optimizacion_id = :pool")
        parametros["pool"] = pool
    if filtros.producto is not None:
        condiciones.append("r.producto = :producto")
        parametros["producto"] = filtros.producto
    if filtros.prioridad is not None:
        condiciones.append("r.prioridad = :prioridad")
        parametros["prioridad"] = filtros.prioridad
    if filtros.base is not None:
        if ejecucion.version_bases_operativas_id is None:
            raise FiltroDesconocido("La ejecucion no declaro un ruteo: sus cuentas no tienen base.")
        base = s.execute(
            text(
                "SELECT id FROM base_operativa WHERE version_bases_operativas_id = :v "
                "AND codigo = :codigo"
            ),
            {"v": ejecucion.version_bases_operativas_id, "codigo": filtros.base},
        ).scalar_one_or_none()
        if base is None:
            raise FiltroDesconocido(
                f"La version de bases de la ejecucion no tiene la base {filtros.base}."
            )
        condiciones.append("r.base_operativa_id = :base")
        parametros["base"] = base
    donde = " AND ".join(condiciones)
    total = (
        s.execute(
            text(f"SELECT count(*) FROM resultado_optimizacion_cuenta r WHERE {donde}"),
            parametros,
        ).scalar_one()
        if len(condiciones) > 1
        else ejecucion.cuentas_publicadas
    )
    filas = s.execute(
        text(
            COLUMNAS_DEL_RESULTADO
            + "FROM (SELECT * FROM resultado_optimizacion_cuenta r WHERE "
            + donde
            + " ORDER BY r.cuenta_canonica_id OFFSET :desplazamiento LIMIT :limite) r "
            + UNIONES_DEL_RESULTADO
            + "ORDER BY r.cuenta_canonica_id"
        ),
        {**parametros, "desplazamiento": desplazamiento, "limite": limite},
    ).all()
    return total, [ResultadoVista(*f) for f in filas]


def resultado_de_cuenta(
    s: Session, ejecucion: EjecucionOptimizacion, cuenta_canonica_id: int
) -> ResultadoVista | None:
    fila = s.execute(
        text(
            COLUMNAS_DEL_RESULTADO
            + "FROM resultado_optimizacion_cuenta r "
            + UNIONES_DEL_RESULTADO
            + "WHERE r.ejecucion_optimizacion_id = :e AND r.cuenta_canonica_id = :c"
        ),
        {"e": ejecucion.id, "c": cuenta_canonica_id},
    ).one_or_none()
    return None if fila is None else ResultadoVista(*fila)


# --- la capacidad y las restricciones -------------------------------------------------------------


@dataclass(frozen=True)
class UsoVista:
    pool: str
    canal: str
    tipo_alcance: str
    referencia_alcance: str | None
    capacidad_declarada: int | None
    capacidad_efectiva: int
    elegibles: int
    retenidas: int
    utilizada: int
    holgura: int
    unidades_por_asignacion: int
    unidades_consumidas: int

    @property
    def utilizacion(self) -> Decimal | None:
        if not self.capacidad_efectiva:
            return None
        return (Decimal(self.utilizada) / Decimal(self.capacidad_efectiva)).quantize(
            Decimal("0.000001")
        )


def capacidad(
    s: Session,
    ejecucion: EjecucionOptimizacion,
    *,
    canal: str | None,
    desplazamiento: int,
    limite: int,
) -> tuple[int, list[UsoVista]]:
    """El uso de cada pool, por codigo."""
    condiciones = "u.ejecucion_optimizacion_id = :e" + (
        " AND k.canal = :canal" if canal is not None else ""
    )
    parametros = {"e": ejecucion.id, "canal": canal}
    total = s.execute(
        text(
            "SELECT count(*) FROM uso_pool_optimizacion u JOIN pool_capacidad_optimizacion k "
            f"ON k.id = u.pool_capacidad_optimizacion_id WHERE {condiciones}"
        ),
        parametros,
    ).scalar_one()
    filas = s.execute(
        text(
            "SELECT k.codigo, k.canal, k.tipo_alcance, k.referencia_alcance, "
            "u.capacidad_declarada, u.capacidad_efectiva, u.elegibles, u.retenidas, u.utilizada, "
            "u.holgura, k.unidades_recurso_por_asignacion, u.unidades_consumidas "
            "FROM uso_pool_optimizacion u JOIN pool_capacidad_optimizacion k "
            f"ON k.id = u.pool_capacidad_optimizacion_id WHERE {condiciones} "
            'ORDER BY k.codigo COLLATE "C" OFFSET :desplazamiento LIMIT :limite'
        ),
        {**parametros, "desplazamiento": desplazamiento, "limite": limite},
    ).all()
    return total, [UsoVista(*f) for f in filas]


def restricciones(
    s: Session,
    ejecucion: EjecucionOptimizacion,
    *,
    binding: bool | None,
    tipo: str | None,
    desplazamiento: int,
    limite: int,
) -> tuple[int, list[ResultadoRestriccionOptimizacion]]:
    filtros = [ResultadoRestriccionOptimizacion.ejecucion_optimizacion_id == ejecucion.id]
    if binding is not None:
        filtros.append(ResultadoRestriccionOptimizacion.binding == binding)
    if tipo is not None:
        filtros.append(ResultadoRestriccionOptimizacion.tipo == tipo)
    total = s.exec(
        select(func.count()).select_from(ResultadoRestriccionOptimizacion).where(*filtros)
    ).one()
    filas = s.exec(
        select(ResultadoRestriccionOptimizacion)
        .where(*filtros)
        .order_by(ResultadoRestriccionOptimizacion.codigo)
        .offset(desplazamiento)
        .limit(limite)
    ).all()
    return total, list(filas)


# --- los planes de una cuenta ---------------------------------------------------------------------


@dataclass(frozen=True)
class PlanDeCuentaVista:
    optimizacion_run_id: UUID
    configuracion_id: UUID
    fecha_operacion: date
    corte_id: UUID
    estado: str
    motivo: str
    canal_decision: str
    canal_asignado: str | None
    pool: str | None
    valor_oportunidad_centavos: int
    p_contacto_14d: Decimal


def planes_de_cuenta(
    s: Session, cuenta: int, *, desplazamiento: int, limite: int
) -> tuple[int, list[PlanDeCuentaVista]]:
    """Lo que cada plan publicado concluyo de una cuenta, del mas reciente al mas antiguo."""
    total = s.execute(
        text("SELECT count(*) FROM resultado_optimizacion_cuenta WHERE cuenta_canonica_id = :c"),
        {"c": cuenta},
    ).scalar_one()
    filas = s.execute(
        text(
            "SELECT e.optimizacion_run_id, v.configuracion_id, e.fecha_operacion, k.corte_id, "
            "r.estado, r.motivo, r.canal_decision, r.canal_asignado, p.codigo, "
            "r.valor_oportunidad_centavos, r.p_contacto_14d "
            "FROM resultado_optimizacion_cuenta r "
            "JOIN ejecucion_optimizacion e ON e.id = r.ejecucion_optimizacion_id "
            "JOIN version_configuracion_optimizacion v "
            "ON v.id = e.version_configuracion_optimizacion_id "
            "JOIN corte_canonico k ON k.id = r.corte_canonico_id "
            "LEFT JOIN pool_capacidad_optimizacion p ON p.id = r.pool_capacidad_optimizacion_id "
            "WHERE r.cuenta_canonica_id = :c "
            "ORDER BY r.ejecucion_optimizacion_id DESC OFFSET :desplazamiento LIMIT :limite"
        ),
        {"c": cuenta, "desplazamiento": desplazamiento, "limite": limite},
    ).all()
    return total, [PlanDeCuentaVista(*f) for f in filas]


# --- exportar y comparar planes -------------------------------------------------------------------

COLUMNAS_EXPORTADAS = (
    "cuenta_id",
    "estado",
    "motivo",
    "canal_decision",
    "canal_asignado",
    "pool",
    "prioridad",
    "producto",
    "atraso_bucket",
    "base",
    "gestor",
    "orden_ruteo_original",
    "valor_oportunidad_centavos",
    "p_contacto_14d",
    "unidades_recurso",
    "retenida",
)


def copiar_resultados(s: Session, ejecucion: EjecucionOptimizacion, salida) -> None:
    """Escribe en `salida` (binaria) los resultados por cuenta de un plan, en CSV con encabezado,
    con COPY y en el orden de la cuenta: sin un objeto por fila. Solo codigos y el cuenta_id
    publico; ningun dato personal ni coordenada. Un booleano sale como true o false."""
    conexion = presente(s.connection().connection.driver_connection, "la conexion de la sesion")
    consulta = (
        "COPY (SELECT c.cuenta_id, r.estado, r.motivo, r.canal_decision, r.canal_asignado, "
        "k.codigo AS pool, r.prioridad, r.producto, r.atraso_bucket, b.codigo AS base, "
        "g.codigo AS gestor, r.orden_ruteo_original, r.valor_oportunidad_centavos, "
        "r.p_contacto_14d, r.unidades_recurso, r.retenida::text AS retenida "
        "FROM resultado_optimizacion_cuenta r "
        + UNIONES_DEL_RESULTADO
        + f"WHERE r.ejecucion_optimizacion_id = {int(id_de(ejecucion))} "
        "ORDER BY r.cuenta_canonica_id) TO STDOUT WITH (FORMAT csv, HEADER true)"
    )
    with conexion.cursor() as cursor, cursor.copy(consulta) as copia:
        for bloque in copia:
            salida.write(bloque)


@dataclass(frozen=True)
class Comparacion:
    """Dos planes publicados, cuenta por cuenta, por conjuntos. Describe diferencias: no dice
    cual plan es mejor fuera de su propio modelo."""

    a: EjecucionVista
    b: EjecucionVista
    entradas_distintas: list[str]
    objetivos: dict[str, tuple[int | None, int | None]]
    en_ambos: int
    solo_en_a: int
    solo_en_b: int
    misma_asignacion: int
    """En los dos planes: asignada al mismo pool (por codigo), o no asignada en ninguno."""
    otro_pool_mismo_canal: int
    otro_canal: int
    asignada_en_ambos: int
    asignada_solo_en_a: int
    """Asignada en A y no en B, este o no la cuenta en B."""
    asignada_solo_en_b: int
    estados: dict[str, dict[str, int]]
    """De estado en A a estado en B; "-" si la cuenta no esta en ese plan."""
    canales: dict[str, dict[str, int]]
    """De canal asignado en A a canal asignado en B, de las cuentas de los dos planes; "-" si no
    quedo asignada."""

    @property
    def jaccard_asignadas(self) -> float | None:
        """Las asignadas en los dos entre las asignadas en alguno."""
        union = self.asignada_en_ambos + self.asignada_solo_en_a + self.asignada_solo_en_b
        return None if union == 0 else round(self.asignada_en_ambos / union, 6)


def comparar(s: Session, a: EjecucionVista, b: EjecucionVista) -> Comparacion:
    """Compara dos planes EXITOSA: que entradas declaradas difieren, sus objetivos y como cambio
    cada cuenta, en una sola consulta agregada (un FULL JOIN por cuenta, agrupado). Un pool se
    compara por su codigo: dos configuraciones distintas no comparten filas de pool."""
    filas = s.execute(
        text(
            "WITH a AS (SELECT r.cuenta_canonica_id AS c, r.estado, r.canal_asignado AS canal, "
            "p.codigo AS pool FROM resultado_optimizacion_cuenta r "
            "LEFT JOIN pool_capacidad_optimizacion p ON p.id = r.pool_capacidad_optimizacion_id "
            "WHERE r.ejecucion_optimizacion_id = :a), "
            "b AS (SELECT r.cuenta_canonica_id AS c, r.estado, r.canal_asignado AS canal, "
            "p.codigo AS pool FROM resultado_optimizacion_cuenta r "
            "LEFT JOIN pool_capacidad_optimizacion p ON p.id = r.pool_capacidad_optimizacion_id "
            "WHERE r.ejecucion_optimizacion_id = :b) "
            "SELECT a.estado, b.estado, a.canal, b.canal, a.pool IS NOT DISTINCT FROM b.pool, "
            "count(*) FROM a FULL JOIN b ON a.c = b.c GROUP BY 1, 2, 3, 4, 5"
        ),
        {"a": a.ejecucion.id, "b": b.ejecucion.id},
    ).all()
    estados: dict[str, dict[str, int]] = {}
    canales: dict[str, dict[str, int]] = {}
    conteo = dict.fromkeys(
        (
            "en_ambos",
            "solo_en_a",
            "solo_en_b",
            "misma",
            "otro_pool",
            "otro_canal",
            "ambos",
            "solo_a",
            "solo_b",
        ),
        0,
    )
    asignada = EstadoCuenta.ASIGNADA.value
    for estado_a, estado_b, canal_a, canal_b, mismo_pool, n in filas:
        fila = estados.setdefault(estado_a or "-", {})
        fila[estado_b or "-"] = fila.get(estado_b or "-", 0) + n
        en_a, en_b = estado_a == asignada, estado_b == asignada
        if en_a and not en_b:
            conteo["solo_a"] += n
        elif en_b and not en_a:
            conteo["solo_b"] += n
        if estado_a is None:
            conteo["solo_en_b"] += n
            continue
        if estado_b is None:
            conteo["solo_en_a"] += n
            continue
        conteo["en_ambos"] += n
        fila = canales.setdefault(canal_a or "-", {})
        fila[canal_b or "-"] = fila.get(canal_b or "-", 0) + n
        if en_a and en_b:
            conteo["ambos"] += n
            if mismo_pool:
                conteo["misma"] += n
            elif canal_a == canal_b:
                conteo["otro_pool"] += n
            else:
                conteo["otro_canal"] += n
        elif not en_a and not en_b:
            conteo["misma"] += n
    ea, eb = a.ejecucion, b.ejecucion
    declaradas = {
        "scoring": (a.scoring_run_id, b.scoring_run_id),
        "modelo": (a.modelo_version_id, b.modelo_version_id),
        "decision_v2": (a.decision_run_id, b.decision_run_id),
        "ruteo_v2": (a.ruteo_run_id, b.ruteo_run_id),
        "configuracion": (ea.hash_configuracion, eb.hash_configuracion),
        "fecha_operacion": (ea.fecha_operacion, eb.fecha_operacion),
        "politica": (ea.hash_politica, eb.hash_politica),
        "solver": (ea.solver_fingerprint, eb.solver_fingerprint),
    }
    return Comparacion(
        a=a,
        b=b,
        entradas_distintas=[k for k, (x, y) in declaradas.items() if x != y],
        objetivos={
            "valor_oportunidad_centavos": (
                ea.objetivo_primario_centavos,
                eb.objetivo_primario_centavos,
            ),
            "contacto_1e8": (ea.objetivo_secundario, eb.objetivo_secundario),
            "unidades_recurso": (ea.unidades_recurso, eb.unidades_recurso),
            "cuentas_asignadas": (ea.cuentas_asignadas, eb.cuentas_asignadas),
        },
        en_ambos=conteo["en_ambos"],
        solo_en_a=conteo["solo_en_a"],
        solo_en_b=conteo["solo_en_b"],
        misma_asignacion=conteo["misma"],
        otro_pool_mismo_canal=conteo["otro_pool"],
        otro_canal=conteo["otro_canal"],
        asignada_en_ambos=conteo["ambos"],
        asignada_solo_en_a=conteo["solo_a"],
        asignada_solo_en_b=conteo["solo_b"],
        estados={k: dict(sorted(v.items())) for k, v in sorted(estados.items())},
        canales={k: dict(sorted(v.items())) for k, v in sorted(canales.items())},
    )


# --- la explicacion de una cuenta -----------------------------------------------------------------


@dataclass
class Explicacion:
    """Por que una cuenta quedo como quedo en un plan: lo publicado y los hechos de su
    elegibilidad, leidos de las entradas declaradas. Ningun motivo se inventa."""

    resultado: ResultadoVista
    canales_admisibles: list[str]
    hechos: dict
    pools_elegibles: list[str]
    grupos: list[dict]
    explicacion: str
    aviso: str = AVISO
    detalle: dict = field(default_factory=dict)


def explicar(s: Session, vista: EjecucionVista, cuenta_canonica_id: int) -> Explicacion | None:
    """La explicacion de una cuenta del plan, o None si la ejecucion no publico un resultado de
    ella. Sus hechos salen de las entradas declaradas por sus llaves (la prediccion, la parada y la
    asignacion territorial de esa cuenta), con las mismas reglas que armaron los pares."""
    e = vista.ejecucion
    resultado = resultado_de_cuenta(s, e, cuenta_canonica_id)
    if resultado is None:
        return None
    version = s.get_one(VersionConfiguracionOptimizacion, e.version_configuracion_optimizacion_id)
    contenido = configuracion_.configuracion_de(s, version)
    prediccion = s.execute(
        text(
            "SELECT estado FROM prediccion_cuenta WHERE ejecucion_scoring_predictivo_id = :s "
            "AND cuenta_canonica_id = :c"
        ),
        {"s": e.ejecucion_scoring_predictivo_id, "c": cuenta_canonica_id},
    ).scalar_one()
    parada = None
    if e.ejecucion_ruteo_v2_id is not None:
        parada = s.execute(
            text(
                "SELECT x.estado, x.motivo, g.codigo FROM parada_ruta_v2 x "
                "JOIN gestor_campo g ON g.id = x.gestor_campo_id "
                "WHERE x.ejecucion_ruteo_v2_id = :r AND x.cuenta_canonica_id = :c"
            ),
            {"r": e.ejecucion_ruteo_v2_id, "c": cuenta_canonica_id},
        ).one_or_none()
    admisibles = list(contenido.admisibles(resultado.canal_decision))
    imputada = prediccion == "CON_IMPUTACION"
    hechos = {
        "canal_recomendado_decision_v2": resultado.canal_decision,
        "prioridad_decision_v2": resultado.prioridad,
        "prediccion": prediccion,
        "configuracion_admite_imputacion": contenido.permitir_predicciones_con_imputacion,
        "base_territorial": resultado.base,
        "parada_ruteo_v2": None
        if e.ejecucion_ruteo_v2_id is None
        else (
            {"estado": "SIN_PARADA"}
            if parada is None
            else {"estado": parada[0], "motivo": parada[1], "gestor": parada[2]}
        ),
    }
    elegibles = []
    if not imputada or contenido.permitir_predicciones_con_imputacion:
        for pool in contenido.pools:
            if not pool.activo or pool.canal not in admisibles:
                continue
            if pool.tipo_alcance == "GLOBAL":
                elegibles.append(pool.codigo)
            elif pool.tipo_alcance == "BASE" and pool.referencia_alcance == resultado.base:
                elegibles.append(pool.codigo)
            elif (
                pool.tipo_alcance == "GESTOR_CAMPO"
                and parada is not None
                and parada[0] == "PROGRAMADA"
                and parada[2] == pool.referencia_alcance
            ):
                elegibles.append(pool.codigo)
    dimensiones = {
        Dimension.PRODUCTO.value: resultado.producto,
        Dimension.ATRASO_BUCKET.value: resultado.atraso_bucket,
        Dimension.PRIORIDAD_DECISION.value: resultado.prioridad,
        Dimension.BASE.value: resultado.base,
    }
    grupos = [
        {
            "restriccion": r.codigo,
            "tipo": r.tipo,
            "dimension": r.dimension,
            "valor": r.valor_dimension,
            "pertenece": dimensiones[r.dimension] == r.valor_dimension,
        }
        for r in contenido.restricciones
        if r.activa
    ]
    textos = {
        EstadoCuenta.ASIGNADA.value: f"El plan optimo publicado la asigna al pool {resultado.pool} "
        f"({resultado.canal_asignado}). Es una asignacion de capacidad compatible con las "
        "restricciones, no la evidencia de que ese canal cause la recuperacion predicha.",
        EstadoCuenta.NO_SELECCIONADA.value: "Era elegible y el plan optimo publicado no la asigna. "
        "No se atribuye a una restriccion individual: el optimo es de todo el plan."
        + (
            ""
            if resultado.retenida
            else " No entro al modelo: en su clase de restricciones (los mismos pools elegibles y "
            "las mismas pertenencias) habia al menos tantas cuentas con mejor orden canonico como "
            "cualquier plan factible puede usar de esa clase; cualquiera que la use puede usar una "
            "de ellas sin cambiar ninguna restriccion ni empeorar el objetivo."
        ),
    }
    explicacion = textos.get(resultado.estado) or (
        f"No era elegible ({resultado.motivo}): " + EXPLICACION_DEL_MOTIVO[Motivo(resultado.motivo)]
    )
    return Explicacion(resultado, admisibles, hechos, elegibles, grupos, explicacion)
