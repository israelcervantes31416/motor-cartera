"""El explorador de cuentas: las cuentas de un corte, por pagina, con lo que la decision/v2, el
scoring y la optimizacion del contexto publicaron de cada una.

Todo se filtra, se ordena y se pagina en la base, en dos pasos: primero las llaves de la pagina, en
la tabla mas chica que basta para filtrarlas y ordenarlas (el corte, el scoring o la ejecucion que
se filtra, por sus indices), y despues, solo para esas llaves, lo que se muestra de cada una. Asi
una pagina lejana o un orden por saldo no unen las 500,000 cuentas del corte con cada ejecucion
antes de quedarse con 50. Ningun filtro es un dato personal. El tramo de atraso sale de
TRAMOS_ATRASO, la unica fuente de sus fronteras.

Cada ejecucion del contexto es del corte (si no, el contexto no es compatible y no se llega aqui):
sus filas son de cuentas del corte, una por cuenta. Por eso una ejecucion filtrada puede ser la
tabla de las llaves sin unirse al corte, y un scoring con una prediccion por cada cuenta del corte
puede ordenar las llaves por su propio indice.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlmodel import Session

from motor_cartera.atraso import TRAMOS_ATRASO, tramo_de_atraso

ORDENES = {
    "CUENTA": (None, None),
    "SALDO_TOTAL": ("sn.saldo_total", None),
    "DIAS_ATRASO": ("sn.dias_atraso", None),
    "P_RECUPERACION_POSITIVA_30D": ("p.p_recuperacion_positiva_30d", "scoring"),
    "RECUPERACION_POSITIVA_ESPERADA_30D": ("p.recuperacion_positiva_esperada_30d", "scoring"),
}
"""Cada orden de la lista cerrada: su columna (CUENTA es la llave de la cuenta) y la ejecucion que
necesita. Ninguna de esas columnas admite null en su tabla."""

TABLAS = {
    "decision": ("d", "resultado_decision_v2", "ejecucion_decision_v2_id"),
    "scoring": ("p", "prediccion_cuenta", "ejecucion_scoring_predictivo_id"),
    "optimizacion": ("o", "resultado_optimizacion_cuenta", "ejecucion_optimizacion_id"),
}
"""Cada ejecucion que el explorador une: su alias, su tabla y su columna de ejecucion. La llave de
cada tabla es (ejecucion, cuenta)."""


class FiltroSinEjecucion(ValueError):
    """Un filtro o un orden de una ejecucion que el contexto no declara: no es una lista vacia."""


@dataclass(frozen=True)
class Filtros:
    cuenta: str | None = None
    producto: str | None = None
    atraso_bucket: str | None = None
    prioridad: str | None = None
    canal: str | None = None
    estado_prediccion: str | None = None
    estado_optimizacion: str | None = None


@dataclass(frozen=True)
class CuentaListada:
    cuenta_id: UUID
    producto: str
    saldo_total: Decimal
    dias_atraso: int
    atraso_bucket: str
    prioridad: str | None
    canal_recomendado: str | None
    p_recuperacion_positiva_30d: Decimal | None
    recuperacion_positiva_esperada_30d: Decimal | None
    estado_prediccion: str | None
    estado_optimizacion: str | None
    canal_asignado: str | None
    valor_oportunidad_centavos: int | None


def rango_de_cuenta(prefijo: str) -> tuple[UUID, UUID]:
    """El intervalo de cuenta_id que empieza por esos digitos hexadecimales: entra por el indice
    unico de cuenta_id, sin recorrer la tabla."""
    digitos = prefijo.replace("-", "").lower()
    if not digitos or len(digitos) > 32 or any(c not in "0123456789abcdef" for c in digitos):
        raise ValueError(f"No es el principio de un cuenta_id: {prefijo}")
    return UUID(digitos.ljust(32, "0")), UUID(digitos.ljust(32, "f"))


@dataclass
class _Condiciones:
    """Lo que filtra, por tabla: el corte (sn), la cuenta (c) y cada ejecucion."""

    del_corte: list[str]
    de_la_cuenta: list[str]
    de_ejecucion: dict[str, list[str]]
    parametros: dict

    def hay(self) -> bool:
        return bool(self.del_corte or self.de_la_cuenta or any(self.de_ejecucion.values()))

    def ejecucion_que_filtra(self) -> str | None:
        """La primera ejecucion con un filtro, si nada del corte filtra: conduce las llaves sola."""
        if self.del_corte:
            return None
        return next((e for e, c in self.de_ejecucion.items() if c), None)


def _condiciones(filtros: Filtros, declaradas: dict[str, int | None]) -> _Condiciones:
    c = _Condiciones([], [], {e: [] for e in TABLAS}, {})

    def de(ejecucion: str, que: str, condicion: str, parametro: str, valor: str) -> None:
        if declaradas[ejecucion] is None:
            raise FiltroSinEjecucion(f"{que} necesita que el contexto declare su {ejecucion}.")
        c.de_ejecucion[ejecucion].append(condicion)
        c.parametros[parametro] = valor

    if filtros.cuenta is not None:
        desde, hasta = rango_de_cuenta(filtros.cuenta)
        c.de_la_cuenta.append("c.cuenta_id BETWEEN :desde AND :hasta")
        c.parametros.update(desde=desde, hasta=hasta)
    if filtros.producto is not None:
        c.del_corte.append("sn.producto = :producto")
        c.parametros["producto"] = filtros.producto
    if filtros.atraso_bucket is not None:
        _, minimo, maximo = next(t for t in TRAMOS_ATRASO if t[0] == filtros.atraso_bucket)
        c.del_corte.append("sn.dias_atraso >= :atraso_min")
        c.parametros["atraso_min"] = minimo
        if maximo is not None:
            c.del_corte.append("sn.dias_atraso <= :atraso_max")
            c.parametros["atraso_max"] = maximo
    if filtros.prioridad is not None:
        de(
            "decision",
            "El filtro de prioridad",
            "d.prioridad_final = :prioridad",
            "prioridad",
            filtros.prioridad,
        )
    if filtros.canal is not None:
        de(
            "decision",
            "El filtro de canal",
            "d.canal_recomendado = :canal",
            "canal",
            filtros.canal,
        )
    if filtros.estado_prediccion is not None:
        de(
            "scoring",
            "El filtro de estado predictivo",
            "p.estado = :estado_prediccion",
            "estado_prediccion",
            filtros.estado_prediccion,
        )
    if filtros.estado_optimizacion is not None:
        de(
            "optimizacion",
            "El filtro de estado de optimizacion",
            "o.estado = :estado_optimizacion",
            "estado_optimizacion",
            filtros.estado_optimizacion,
        )
    return c


def _desde(
    conductor: str, c: _Condiciones, *, con_corte: bool, scoring_opcional: bool
) -> tuple[str, str, str]:
    """El FROM y el WHERE conducidos por una tabla: el corte (sn) o una ejecucion, y la llave de la
    cuenta en ella. Las demas tablas se unen solo si filtran, o si hacen falta para ordenar."""
    if conductor == "sn":
        partes = ["snapshot_cuenta sn"]
        llave = "sn.cuenta_canonica_id"
        donde = ["sn.corte_canonico_id = :corte"]
    else:
        alias, tabla, columna = TABLAS[conductor]
        partes = [f"{tabla} {alias}"]
        llave = f"{alias}.cuenta_canonica_id"
        donde = [f"{alias}.{columna} = :{conductor}"]
        if con_corte:
            partes.append(
                "JOIN snapshot_cuenta sn ON sn.corte_canonico_id = :corte "
                f"AND sn.cuenta_canonica_id = {llave}"
            )
    if c.de_la_cuenta:
        partes.append(f"JOIN cuenta_canonica c ON c.id = {llave}")
    for ejecucion, condiciones in c.de_ejecucion.items():
        if ejecucion == conductor or not (
            condiciones or (scoring_opcional and ejecucion == "scoring")
        ):
            continue
        alias, tabla, columna = TABLAS[ejecucion]
        union = "JOIN" if condiciones else "LEFT JOIN"
        partes.append(
            f"{union} {tabla} {alias} ON {alias}.{columna} = :{ejecucion} "
            f"AND {alias}.cuenta_canonica_id = {llave}"
        )
    donde += c.del_corte + c.de_la_cuenta
    donde += [condicion for condiciones in c.de_ejecucion.values() for condicion in condiciones]
    return " ".join(partes), " AND ".join(donde), llave


def listar(
    s: Session,
    *,
    corte_id: int,
    cuentas_del_corte: int,
    decision_id: int | None,
    scoring_id: int | None,
    optimizacion_id: int | None,
    filtros: Filtros,
    orden: str,
    descendente: bool,
    desplazamiento: int,
    limite: int,
) -> tuple[int, list[CuentaListada]]:
    """Una pagina de las cuentas del corte. Sin filtros, el total es el del corte publicado: no se
    cuenta otra vez. Una ejecucion que el contexto no declara no se une, y sus columnas son null."""
    declaradas = {"decision": decision_id, "scoring": scoring_id, "optimizacion": optimizacion_id}
    c = _condiciones(filtros, declaradas)
    columna, necesita = ORDENES[orden]
    if necesita is not None and declaradas[necesita] is None:
        raise FiltroSinEjecucion(
            f"El orden {orden} necesita que el contexto declare su {necesita}."
        )
    parametros = {"corte": corte_id, **c.parametros}
    parametros.update({e: v for e, v in declaradas.items() if v is not None})
    direccion = "DESC" if descendente else "ASC"

    # Las llaves de la pagina, por la tabla mas chica que basta para filtrar y ordenar.
    scoring_opcional = False
    if necesita == "scoring" and _cubre_el_corte(s, scoring_id, cuentas_del_corte):
        conductor = "scoring"
    elif necesita == "scoring":
        # Sin una prediccion por cada cuenta del corte, las que no tienen van al final.
        conductor, scoring_opcional = "sn", True
    elif columna is not None:
        conductor = "sn"
    else:
        conductor = c.ejecucion_que_filtra() or "sn"
    desde, donde, llave = _desde(
        conductor, c, con_corte=bool(c.del_corte), scoring_opcional=scoring_opcional
    )
    if columna is None:
        orden_sql = f"{llave} {direccion}"
    elif scoring_opcional:
        orden_sql = f"{columna} {direccion} NULLS LAST, {llave} {direccion}"
    else:
        orden_sql = f"{columna} {direccion}, {llave} {direccion}"
    llaves = list(
        s.execute(
            text(
                f"SELECT {llave} FROM {desde} WHERE {donde} "
                f"ORDER BY {orden_sql} OFFSET :desplazamiento LIMIT :limite"
            ),
            {**parametros, "desplazamiento": desplazamiento, "limite": limite},
        ).scalars()
    )

    # El total: el del corte sin filtros; con filtros, contado por la tabla que filtra.
    if not c.hay():
        total = cuentas_del_corte
    else:
        desde, donde, _ = _desde(
            c.ejecucion_que_filtra() or "sn",
            c,
            con_corte=bool(c.del_corte),
            scoring_opcional=False,
        )
        total = s.execute(
            text(f"SELECT count(*) FROM {desde} WHERE {donde}"), parametros
        ).scalar_one()
    return total, _detalle(s, llaves, corte_id, declaradas)


def _cubre_el_corte(s: Session, scoring_id: int | None, cuentas_del_corte: int) -> bool:
    """Si el scoring publico una prediccion por cada cuenta del corte: sus predicciones son de
    cuentas del corte, una por cuenta, asi que basta contarlas."""
    publicadas = s.execute(
        text("SELECT predicciones_publicadas FROM ejecucion_scoring_predictivo WHERE id = :id"),
        {"id": scoring_id},
    ).scalar_one()
    return publicadas == cuentas_del_corte


def _detalle(
    s: Session, llaves: list[int], corte_id: int, declaradas: dict[str, int | None]
) -> list[CuentaListada]:
    """Lo que se muestra de cada llave, en el orden de la pagina: una union por la llave
    (ejecucion, cuenta) de cada tabla, para cada una de las llaves y nada mas."""
    if not llaves:
        return []

    def columnas(ejecucion: str, nombres: str, nulos: int) -> str:
        return nombres if declaradas[ejecucion] is not None else ", ".join(["NULL"] * nulos)

    uniones = "".join(
        f" LEFT JOIN {tabla} {alias} ON {alias}.{columna} = :{ejecucion}"
        f" AND {alias}.cuenta_canonica_id = k.id"
        for ejecucion, (alias, tabla, columna) in TABLAS.items()
        if declaradas[ejecucion] is not None
    )
    filas = s.execute(
        text(
            "SELECT c.cuenta_id, sn.producto, sn.saldo_total, sn.dias_atraso, "
            + columnas("decision", "d.prioridad_final, d.canal_recomendado", 2)
            + ", "
            + columnas(
                "scoring",
                "p.p_recuperacion_positiva_30d, p.recuperacion_positiva_esperada_30d, p.estado",
                3,
            )
            + ", "
            + columnas(
                "optimizacion", "o.estado, o.canal_asignado, o.valor_oportunidad_centavos", 3
            )
            + " FROM unnest(CAST(:llaves AS bigint[])) WITH ORDINALITY AS k(id, n)"
            " JOIN snapshot_cuenta sn ON sn.corte_canonico_id = :corte"
            " AND sn.cuenta_canonica_id = k.id"
            " JOIN cuenta_canonica c ON c.id = k.id" + uniones + " ORDER BY k.n"
        ),
        {
            "llaves": llaves,
            "corte": corte_id,
            **{e: v for e, v in declaradas.items() if v is not None},
        },
    ).all()
    return [
        CuentaListada(
            cuenta_id=f[0],
            producto=f[1],
            saldo_total=f[2],
            dias_atraso=f[3],
            atraso_bucket=tramo_de_atraso(f[3]),
            prioridad=f[4],
            canal_recomendado=f[5],
            p_recuperacion_positiva_30d=f[6],
            recuperacion_positiva_esperada_30d=f[7],
            estado_prediccion=f[8],
            estado_optimizacion=f[9],
            canal_asignado=f[10],
            valor_oportunidad_centavos=f[11],
        )
        for f in filas
    ]
