"""El contexto operativo de la app: el corte y las ejecuciones que una pantalla lee, cada una por su
identificador publico, y si son compatibles entre si.

Resolver es leer lo que cada ejecucion declarada dice de si misma: su corte, sus relojes, sus
versiones y lo que ella misma declaro que leyo (una optimizacion, su scoring, su decision/v2, su
plan de campo y su ruteo; un plan de campo, su decision/v2 y su territorial/v2; un ruteo, su plan;
un analytics, las entradas de su manifiesto; un scoring, su version del modelo). Validar es
comparar eso entre ellas y con lo que declara el contexto.

Nada se completa ni se corrige: un contexto incompatible se reporta con cada problema, y ninguna
pantalla lo combina. Lo que el contexto no declara no se busca: no hay "la ejecucion mas reciente".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from itertools import combinations
from uuid import UUID

from sqlalchemy import text
from sqlmodel import Session


class Tipo(StrEnum):
    """Las ejecuciones que un contexto puede declarar."""

    DECISION_V2 = "DECISION_V2"
    ANALYTICS = "ANALYTICS"
    SCORING = "SCORING"
    OPTIMIZACION = "OPTIMIZACION"
    CAMPO_V2 = "CAMPO_V2"
    RUTEO_V2 = "RUTEO_V2"


NOMBRES = {
    "DECISION_V2": "la decision/v2",
    "ANALYTICS": "la ejecucion de analytics/v1",
    "SCORING": "el scoring",
    "OPTIMIZACION": "la optimizacion",
    "CAMPO_V2": "el plan de campo/v2",
    "RUTEO_V2": "el ruteo/v2",
    "TERRITORIAL_V2": "la territorial/v2",
    "GEOGRAFIA": "la geografia",
    "EVALUACION_PROMESAS": "la evaluacion de promesas",
    "MODELO": "la version del modelo",
    "CONFIGURACION_CAMPO": "la configuracion de campo",
    "CONFIGURACION_OPTIMIZACION": "la configuracion de optimizacion",
    "RED_VIAL": "la red vial",
}
"""Como se nombra cada cosa en un problema, para una persona."""


@dataclass(frozen=True)
class Contexto:
    """Lo que una pantalla declara leer. Solo el corte es obligatorio."""

    corte_id: UUID
    decision_run_id: UUID | None = None
    analytics_run_id: UUID | None = None
    scoring_run_id: UUID | None = None
    optimizacion_run_id: UUID | None = None
    campo_run_id: UUID | None = None
    ruteo_run_id: UUID | None = None

    def declarada(self, tipo: Tipo) -> UUID | None:
        return getattr(self, CAMPOS[tipo])

    def declaradas(self) -> dict[Tipo, UUID]:
        return {tipo: run for tipo in Tipo if (run := self.declarada(tipo)) is not None}


CAMPOS = {
    Tipo.DECISION_V2: "decision_run_id",
    Tipo.ANALYTICS: "analytics_run_id",
    Tipo.SCORING: "scoring_run_id",
    Tipo.OPTIMIZACION: "optimizacion_run_id",
    Tipo.CAMPO_V2: "campo_run_id",
    Tipo.RUTEO_V2: "ruteo_run_id",
}
"""El campo del contexto que declara cada tipo."""


@dataclass(frozen=True)
class Corte:
    id: int
    corte_id: UUID
    despacho_id: str
    cartera_id: str
    fecha_corte: date
    cuentas: int
    firma_contenido: str
    version_modelo: str
    creado_en: datetime
    dataset_id: UUID
    run_id: UUID | None


@dataclass(frozen=True)
class Ejecucion:
    """Lo que el contexto lee de una ejecucion: su identidad, su estado, su corte, sus relojes, sus
    versiones y lo que ella misma declaro que leyo (`lee`, por tipo)."""

    tipo: Tipo
    id: int
    """Interno: nunca sale de la base."""
    run_id: UUID
    estado: str
    resultado: str | None
    corte_id: UUID
    fecha_corte: date
    as_of: date | None
    known_at: datetime | None
    zona_horaria: str | None
    """La zona de sus relojes: la de la decision, el analytics o el scoring, o la de la
    configuracion de campo de un plan o de un ruteo; null en una optimizacion."""
    fecha_operacion: date | None
    version_motor: str | None
    version_politica: str | None
    hash_politica: str | None
    firma_inputs: str | None
    iniciada_en: datetime
    terminada_en: datetime | None
    lee: dict[str, UUID]


@dataclass(frozen=True)
class Problema:
    codigo: str
    mensaje: str
    ejecuciones: tuple[str, ...]
    """Los tipos que lo producen, para que la pantalla los senale."""


@dataclass(frozen=True)
class ContextoResuelto:
    contexto: Contexto
    corte: Corte
    ejecuciones: dict[Tipo, Ejecucion]
    problemas: tuple[Problema, ...]

    @property
    def compatible(self) -> bool:
        return not self.problemas

    def ejecucion(self, tipo: Tipo) -> Ejecucion | None:
        return self.ejecuciones.get(tipo)

    def derivada(self, clave: str) -> UUID | None:
        """Lo que una ejecucion declarada leyo y el contexto no declara (la territorial/v2 y la
        geografia de un plan de campo, la version del modelo de un scoring...). En un contexto
        compatible, todas las que lo leen coinciden: se toma la de cualquiera."""
        for ejecucion in self.ejecuciones.values():
            if clave in ejecucion.lee:
                return ejecucion.lee[clave]
        return None


class CorteNoEncontrado(Exception):
    pass


# --- leer las ejecuciones -------------------------------------------------------------------------

_COMUNES = (
    "{x}.estado, {x}.resultado, k.corte_id, k.fecha_corte, {as_of} AS as_of, "
    "{known_at} AS known_at, {zona} AS zona_horaria, {fecha} AS fecha_operacion, "
    "{version} AS version_motor, "
    "{politica} AS version_politica, {hash} AS hash_politica, {x}.firma_inputs, "
    "{x}.iniciada_en, {x}.terminada_en"
)


def _columnas(x: str, **cambios: str) -> str:
    valores = {
        "x": x,
        "as_of": "CAST(NULL AS date)",
        "known_at": "CAST(NULL AS timestamptz)",
        "zona": "CAST(NULL AS varchar)",
        "fecha": "CAST(NULL AS date)",
        "version": f"{x}.version_motor",
        "politica": f"{x}.version_politica",
        "hash": f"{x}.hash_politica",
    }
    valores.update(cambios)
    return _COMUNES.format(**valores)


_LECTURAS: dict[Tipo, tuple[str, str, str]] = {
    # (columnas y uniones, la columna de su identificador publico, la del id de su corte)
    Tipo.DECISION_V2: (
        "SELECT d.id, d.decision_run_id AS run_id, "
        + _columnas("d", as_of="d.as_of", known_at="d.known_at", zona="d.zona_horaria")
        + " FROM ejecucion_decision_v2 d JOIN corte_canonico k ON k.id = d.corte_canonico_id",
        "d.decision_run_id",
        "d.corte_canonico_id",
    ),
    Tipo.ANALYTICS: (
        "SELECT a.id, a.analytics_run_id AS run_id, "
        + _columnas("a", as_of="a.as_of", known_at="a.known_at", zona="a.zona_horaria")
        + " FROM ejecucion_analytics a JOIN corte_canonico k ON k.id = a.corte_canonico_id",
        "a.analytics_run_id",
        "a.corte_canonico_id",
    ),
    Tipo.SCORING: (
        "SELECT e.id, e.scoring_run_id AS run_id, "
        + _columnas(
            "e",
            as_of="e.as_of",
            known_at="e.known_at",
            zona="e.zona_horaria",
            version="v.version_modelo",
            politica="v.version_politica",
            hash="v.hash_politica",
        )
        + ", v.modelo_version_id AS lee_modelo"
        " FROM ejecucion_scoring_predictivo e"
        " JOIN corte_canonico k ON k.id = e.corte_canonico_id"
        " JOIN version_modelo_predictivo v ON v.id = e.version_modelo_predictivo_id",
        "e.scoring_run_id",
        "e.corte_canonico_id",
    ),
    Tipo.OPTIMIZACION: (
        "SELECT o.id, o.optimizacion_run_id AS run_id, "
        + _columnas("o", fecha="o.fecha_operacion")
        + ", sc.scoring_run_id AS lee_scoring, d.decision_run_id AS lee_decision_v2,"
        " ca.campo_run_id AS lee_campo_v2, ru.ruteo_run_id AS lee_ruteo_v2,"
        " cf.configuracion_id AS lee_configuracion_optimizacion"
        " FROM ejecucion_optimizacion o"
        " JOIN corte_canonico k ON k.id = o.corte_canonico_id"
        " JOIN ejecucion_scoring_predictivo sc ON sc.id = o.ejecucion_scoring_predictivo_id"
        " JOIN ejecucion_decision_v2 d ON d.id = o.ejecucion_decision_v2_id"
        " LEFT JOIN ejecucion_campo_v2 ca ON ca.id = o.ejecucion_campo_v2_id"
        " LEFT JOIN ejecucion_ruteo_v2 ru ON ru.id = o.ejecucion_ruteo_v2_id"
        " JOIN version_configuracion_optimizacion cf"
        " ON cf.id = o.version_configuracion_optimizacion_id",
        "o.optimizacion_run_id",
        "o.corte_canonico_id",
    ),
    Tipo.CAMPO_V2: (
        "SELECT c.id, c.campo_run_id AS run_id, "
        + _columnas("c", fecha="c.fecha_operacion", zona="cf.zona_horaria")
        + ", d.decision_run_id AS lee_decision_v2, t.territorial_run_id AS lee_territorial_v2,"
        " g.geografia_run_id AS lee_geografia,"
        " cf.configuracion_campo_version_id AS lee_configuracion_campo"
        " FROM ejecucion_campo_v2 c"
        " JOIN corte_canonico k ON k.id = c.corte_canonico_id"
        " JOIN ejecucion_decision_v2 d ON d.id = c.ejecucion_decision_v2_id"
        " JOIN ejecucion_territorial_v2 t ON t.id = c.ejecucion_territorial_v2_id"
        " JOIN ejecucion_geografia g ON g.id = t.ejecucion_geografia_id"
        " JOIN version_configuracion_campo cf ON cf.id = c.version_configuracion_campo_id",
        "c.campo_run_id",
        "c.corte_canonico_id",
    ),
    Tipo.RUTEO_V2: (
        "SELECT r.id, r.ruteo_run_id AS run_id, "
        + _columnas("r", fecha="r.fecha_operacion", zona="cf.zona_horaria")
        + ", c.campo_run_id AS lee_campo_v2, v.red_vial_version_id AS lee_red_vial"
        " FROM ejecucion_ruteo_v2 r"
        " JOIN ejecucion_campo_v2 c ON c.id = r.ejecucion_campo_v2_id"
        " JOIN version_configuracion_campo cf ON cf.id = c.version_configuracion_campo_id"
        " JOIN corte_canonico k ON k.id = c.corte_canonico_id"
        " JOIN version_red_vial v ON v.id = r.version_red_vial_id",
        "r.ruteo_run_id",
        "c.corte_canonico_id",
    ),
}

ENTRADAS_DE_ANALYTICS = (
    "DECISION_V2",
    "GEOGRAFIA",
    "TERRITORIAL_V2",
    "CAMPO_V2",
    "RUTEO_V2",
    "EVALUACION_PROMESAS",
)
"""Las entradas de un manifiesto de analytics que son una sola ejecucion: las que se comparan con
el contexto. Las interpretaciones de pagos y sus atribuciones son una por mes, y no se declaran en
el contexto."""


def leer(
    s: Session,
    tipo: Tipo,
    *,
    run_ids: list[UUID] | None = None,
    corte_id: int | None = None,
    limite: int | None = None,
) -> list[Ejecucion]:
    """Las ejecuciones de un tipo, por sus identificadores publicos o de un corte (la mas reciente
    primero, hasta `limite`), con lo que cada una declaro que leyo. Una consulta por tipo, mas una
    para las entradas de analytics: nunca una por ejecucion."""
    consulta, columna_run, columna_corte = _LECTURAS[tipo]
    parametros: dict = {}
    if run_ids is not None:
        consulta += f" WHERE {columna_run} = ANY(CAST(:runs AS uuid[]))"
        parametros["runs"] = [str(r) for r in run_ids]
    else:
        consulta += f" WHERE {columna_corte} = :corte"
        parametros["corte"] = corte_id
    consulta += " ORDER BY iniciada_en DESC, id DESC"
    if limite is not None:
        consulta += " LIMIT :limite"
        parametros["limite"] = limite
    filas = s.execute(text(consulta), parametros).mappings().all()
    entradas: dict[int, dict[str, UUID]] = {}
    if tipo == Tipo.ANALYTICS and filas:
        for fila in s.execute(
            text(
                "SELECT ejecucion_analytics_id, tipo_entrada, referencia_publica "
                "FROM entrada_analytics WHERE ejecucion_analytics_id = ANY(:ids) "
                "AND tipo_entrada = ANY(:tipos)"
            ),
            {"ids": [f["id"] for f in filas], "tipos": list(ENTRADAS_DE_ANALYTICS)},
        ).all():
            entradas.setdefault(fila[0], {})[fila[1]] = fila[2]
    resultado = []
    for f in filas:
        lee = {
            columna.removeprefix("lee_").upper(): valor
            for columna, valor in f.items()
            if columna.startswith("lee_") and valor is not None
        }
        lee.update(entradas.get(f["id"], {}))
        resultado.append(
            Ejecucion(
                tipo=tipo,
                id=f["id"],
                run_id=f["run_id"],
                estado=f["estado"],
                resultado=f["resultado"],
                corte_id=f["corte_id"],
                fecha_corte=f["fecha_corte"],
                as_of=f["as_of"],
                known_at=f["known_at"],
                zona_horaria=f["zona_horaria"],
                fecha_operacion=f["fecha_operacion"],
                version_motor=f["version_motor"],
                version_politica=f["version_politica"],
                hash_politica=f["hash_politica"],
                firma_inputs=f["firma_inputs"],
                iniciada_en=f["iniciada_en"],
                terminada_en=f["terminada_en"],
                lee=lee,
            )
        )
    return resultado


def leer_corte(s: Session, corte_id: UUID) -> Corte:
    fila = s.execute(
        text(
            "SELECT k.id, k.corte_id, k.despacho_id, k.cartera_id, k.fecha_corte, k.cuentas, "
            "k.firma_contenido, k.version_modelo, k.creado_en, d.dataset_id, c.run_id "
            "FROM corte_canonico k JOIN dataset_conformado d ON d.id = k.dataset_conformado_id "
            "LEFT JOIN corrida c ON c.id = d.corrida_id WHERE k.corte_id = :corte"
        ),
        {"corte": corte_id},
    ).one_or_none()
    if fila is None:
        raise CorteNoEncontrado(f"No existe un corte canonico con corte_id {corte_id}.")
    return Corte(*fila)


# --- validar --------------------------------------------------------------------------------------


def resolver(s: Session, contexto: Contexto) -> ContextoResuelto:
    """Lee el corte y cada ejecucion declarada, y compara lo que cada una leyo con lo que declara
    el contexto y con lo que leyeron las demas. A lo mas siete consultas, cualquiera que sea el
    contexto."""
    corte = leer_corte(s, contexto.corte_id)
    ejecuciones: dict[Tipo, Ejecucion] = {}
    for tipo, run_id in contexto.declaradas().items():
        encontradas = leer(s, tipo, run_ids=[run_id])
        if encontradas:
            ejecuciones[tipo] = encontradas[0]
    return ContextoResuelto(
        contexto=contexto,
        corte=corte,
        ejecuciones=ejecuciones,
        problemas=tuple(problemas(contexto, corte, ejecuciones)),
    )


def problemas(contexto: Contexto, corte: Corte, ejecuciones: dict[Tipo, Ejecucion]) -> list:
    """Cada razon por la que el contexto no se puede combinar, en un orden estable: una ejecucion
    que no existe, una de otro corte, una que leyo otra cosa que lo que el contexto declara, y dos
    que leyeron cosas distintas de lo mismo."""
    encontrados: list[Problema] = []
    for tipo, run_id in contexto.declaradas().items():
        ejecucion = ejecuciones.get(tipo)
        if ejecucion is None:
            encontrados.append(
                Problema(
                    "EJECUCION_NO_ENCONTRADA",
                    f"No existe {NOMBRES[tipo]} {run_id}.",
                    (tipo.value,),
                )
            )
        elif ejecucion.corte_id != corte.corte_id:
            encontrados.append(
                Problema(
                    "CORTE_DISTINTO",
                    f"{_capital(NOMBRES[tipo])} {run_id} es del corte {ejecucion.corte_id} "
                    f"({ejecucion.fecha_corte}), no del corte del contexto {corte.corte_id} "
                    f"({corte.fecha_corte}).",
                    (tipo.value,),
                )
            )
    for tipo, ejecucion in ejecuciones.items():
        for leida, valor in sorted(ejecucion.lee.items()):
            if leida not in Tipo.__members__:
                continue
            declarada = contexto.declarada(Tipo(leida))
            if declarada is not None and declarada != valor:
                encontrados.append(
                    Problema(
                        "ENCADENAMIENTO_DISTINTO",
                        f"{_capital(NOMBRES[tipo])} {ejecucion.run_id} lee {NOMBRES[leida]} "
                        f"{valor}; el contexto declara {NOMBRES[leida]} {declarada}.",
                        (tipo.value, leida),
                    )
                )
    for a, b in combinations(sorted(ejecuciones.values(), key=lambda e: e.tipo.value), 2):
        for leida in sorted(set(a.lee) & set(b.lee)):
            if a.lee[leida] != b.lee[leida]:
                encontrados.append(
                    Problema(
                        "ENCADENAMIENTO_DISTINTO",
                        f"{_capital(NOMBRES[a.tipo])} {a.run_id} lee {NOMBRES[leida]} "
                        f"{a.lee[leida]}, y {NOMBRES[b.tipo]} {b.run_id} lee {b.lee[leida]}.",
                        (a.tipo.value, b.tipo.value),
                    )
                )
    return encontrados


def _capital(texto: str) -> str:
    return texto[:1].upper() + texto[1:]
