"""Cuenta 360: lo que se sabe de una cuenta canonica, su historia, sus eventos y sus pagos.

Compone las cuatro tablas del modelo historico, y nada mas: CuentaCanonica, CorteCanonico,
SnapshotCuenta y PagoObservado. Ninguna consulta vuelve a leer un Parquet ni un archivo original, y
ninguna depende del tamano de la cartera: todas son de una sola cuenta y entran por un indice.

- La cuenta se busca por su llave (despacho, cartera y CLIENTE_UNICO) o por su cuenta_id.
- Sus cortes salen del indice (cuenta_canonica_id, fecha_corte): a lo mas uno por corte de su
  cartera.
- Los cortes de la cartera salen de corte_canonico, que tiene uno por fecha.
- Sus pagos observados salen del indice (despacho, cartera, CLIENTE_UNICO, fecha_recepcion): no hay
  una llave que los ate a la cuenta, asi que un pago que llego antes que su cuenta tambien se ve.

Los eventos, la continuidad y los deltas se calculan aqui, al consultar, con `presencia`.

Cada funcion compone varias consultas. La API las llama con una sesion de lectura
(`db.sesion.sesion_de_lectura`), en la que todas ven la misma foto de la base: un corte que se
publica mientras se responde no aparece en una consulta y falta en la siguiente.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, func, text
from sqlmodel import Session, select

from motor_cartera.db.modelos import (
    ArtefactoFuente,
    Corrida,
    CorteCanonico,
    CuentaCanonica,
    DatasetConformado,
    EjecucionHistoria,
    IngestaPagos,
    PagoObservado,
    SnapshotCuenta,
)
from motor_cartera.historia import presencia
from motor_cartera.historia.presencia import Corte, Enlace, Evento, ResumenPresencia


class CuentaNoEncontrada(Exception):
    """No hay una cuenta canonica con esa llave o ese cuenta_id."""


class SinCuentaObservada(CuentaNoEncontrada):
    """No hay cuenta canonica con ese CLIENTE_UNICO, pero si pagos observados: llegaron pagos de un
    cliente que ningun corte de la cartera trae (todavia)."""

    def __init__(self, pagos: int) -> None:
        super().__init__(pagos)
        self.pagos = pagos


class CorteNoEncontrado(Exception):
    """No hay un corte canonico con ese corte_id."""


@dataclass(frozen=True)
class SnapshotVisto:
    """Un snapshot con lo que lo ata a su evidencia: su corte y el dataset del que salio."""

    snapshot: SnapshotCuenta
    corte_id: UUID
    dataset_id: UUID


@dataclass(frozen=True)
class SnapshotEnHistoria:
    """Un snapshot en la historia de su cuenta: como se une con el anterior y cuanto cambio."""

    visto: SnapshotVisto
    enlace: Enlace
    delta_saldo_total: Decimal | None
    """saldo_total menos el del snapshot anterior de la cuenta, continuo o no."""
    delta_dias_atraso: int | None


@dataclass(frozen=True)
class Resumen360:
    """La cuenta como se ve en el ultimo corte de su cartera, o en el ultimo hasta una fecha."""

    cuenta: CuentaCanonica
    al: date | None
    presencia: ResumenPresencia
    snapshot_actual: SnapshotVisto | None
    """El de el ultimo corte de la cartera, si la cuenta esta en el."""
    ultimo_snapshot_observado: SnapshotVisto | None
    """El del ultimo corte en que se observo, este o no en el ultimo de la cartera."""
    pagos_observados: int


@dataclass(frozen=True)
class PagoVisto:
    pago: PagoObservado
    pagos_run_id: UUID
    dataset_id: UUID


@dataclass(frozen=True)
class CorteVisto:
    corte: CorteCanonico
    dataset_id: UUID
    run_id: UUID


@dataclass(frozen=True)
class FuenteDelCorte:
    """Una ejecucion historica que publico el corte o que lo reconocio como equivalente."""

    ejecucion: EjecucionHistoria
    dataset: DatasetConformado
    corrida: Corrida
    original: ArtefactoFuente


@dataclass(frozen=True)
class DetalleDelCorte:
    visto: CorteVisto
    conformado: ArtefactoFuente
    original: ArtefactoFuente
    fuentes: list[FuenteDelCorte]


# --- la cuenta ------------------------------------------------------------------------------------


def buscar(s: Session, despacho_id: str, cartera_id: str, cliente_unico: str) -> CuentaCanonica:
    """La cuenta canonica de ese CLIENTE_UNICO en esa cartera. Si no hay, SinCuentaObservada si hay
    pagos observados de ese cliente, o CuentaNoEncontrada si no hay nada."""
    cuenta = s.exec(
        select(CuentaCanonica).where(
            CuentaCanonica.despacho_id == despacho_id,
            CuentaCanonica.cartera_id == cartera_id,
            CuentaCanonica.cliente_unico == cliente_unico,
        )
    ).one_or_none()
    if cuenta is not None:
        return cuenta
    pagos = _contar_pagos(s, despacho_id, cartera_id, cliente_unico, None)
    if pagos:
        raise SinCuentaObservada(pagos)
    raise CuentaNoEncontrada(cliente_unico)


def obtener(s: Session, cuenta_id: UUID) -> CuentaCanonica:
    cuenta = s.exec(select(CuentaCanonica).where(CuentaCanonica.cuenta_id == cuenta_id)).first()
    if cuenta is None:
        raise CuentaNoEncontrada(cuenta_id)
    return cuenta


def resumen(s: Session, cuenta: CuentaCanonica, al: date | None = None) -> Resumen360:
    """La Cuenta 360: su presencia en los cortes de su cartera y su ultimo snapshot. Con `al`, como
    se veia con los cortes de fecha hasta ese dia: el ultimo corte es el ultimo hasta esa fecha."""
    # Primero sus cortes y despues los de la cartera. Con una sola foto de la base (la sesion de
    # lectura) el orden da igual; con READ COMMITTED, al menos la cuenta nunca trae un corte que la
    # cartera no.
    fechas = fechas_observadas(s, cuenta.id, al)
    cortes = cortes_de_la_cartera(s, cuenta.despacho_id, cuenta.cartera_id, al)
    vista = presencia.resumir(cortes, fechas)
    actual = None
    if vista.estado == presencia.Presencia.EN_CARTERA:
        actual = _snapshot_en(s, cuenta.id, vista.ultimo_corte.fecha_corte)
    ultimo = None
    if vista.ultima_observacion is not None:
        ultimo = actual or _snapshot_en(s, cuenta.id, vista.ultima_observacion)
    pagos = _contar_pagos(s, cuenta.despacho_id, cuenta.cartera_id, cuenta.cliente_unico, al)
    return Resumen360(cuenta, al, vista, actual, ultimo, pagos)


def historia(
    s: Session,
    cuenta: CuentaCanonica,
    *,
    desplazamiento: int,
    limite: int,
    descendente: bool = True,
) -> tuple[int, list[SnapshotEnHistoria]]:
    """Una pagina de los snapshots de la cuenta, en orden de corte, con su continuidad y sus deltas.

    Cada snapshot se compara con el anterior de la cuenta, que puede caer fuera de la pagina: se lee
    un snapshot mas, del lado de los anteriores. Devuelve el total de snapshots y la pagina."""
    total = s.exec(
        select(func.count())
        .select_from(SnapshotCuenta)
        .where(SnapshotCuenta.cuenta_canonica_id == cuenta.id)
    ).one()
    orden = SnapshotCuenta.fecha_corte.desc() if descendente else SnapshotCuenta.fecha_corte
    if descendente:
        inicio, cuantos = desplazamiento, limite + 1
    else:
        inicio = max(desplazamiento - 1, 0)
        cuantos = limite + (desplazamiento - inicio)
    filas = s.exec(
        _snapshots_vistos()
        .where(SnapshotCuenta.cuenta_canonica_id == cuenta.id)
        .order_by(orden)
        .offset(inicio)
        .limit(cuantos)
    ).all()
    vistos = [SnapshotVisto(*fila) for fila in filas]
    cronologicos = vistos[::-1] if descendente else vistos
    # Los cortes de la cartera, despues de los snapshots: aun con READ COMMITTED, cada snapshot
    # encuentra su lugar entre los cortes.
    lugares = presencia.posiciones(cortes_de_la_cartera(s, cuenta.despacho_id, cuenta.cartera_id))
    enlazados = []
    anteriores = [None, *cronologicos][: len(cronologicos)]
    for anterior, actual in zip(anteriores, cronologicos, strict=True):
        enlazados.append(_enlazar(lugares, actual, anterior))
    if descendente:
        pagina = enlazados[::-1][:limite]
    else:
        pagina = enlazados[desplazamiento - inicio :][:limite]
    return total, pagina


def eventos(s: Session, cuenta: CuentaCanonica) -> list[Evento]:
    """Los eventos de presencia de la cuenta, en orden de corte, calculados al consultar."""
    fechas = fechas_observadas(s, cuenta.id)  # antes que los cortes, como en `resumen`
    cortes = cortes_de_la_cartera(s, cuenta.despacho_id, cuenta.cartera_id)
    return presencia.eventos(cortes, fechas)


def pagos_observados(
    s: Session, cuenta: CuentaCanonica, *, desplazamiento: int, limite: int
) -> tuple[int, list[PagoVisto]]:
    """Una pagina de los pagos observados de la cuenta, del mas reciente al mas antiguo, tal como
    llegaron. Entre dos con la misma fecha de recepcion, el del dataset mas reciente y despues la
    fila mas alta: el orden es total y la paginacion, estable."""
    de_la_cuenta = _de_la_cuenta(cuenta.despacho_id, cuenta.cartera_id, cuenta.cliente_unico)
    total = s.exec(select(func.count()).select_from(PagoObservado).where(de_la_cuenta)).one()
    filas = s.exec(
        select(PagoObservado, IngestaPagos.pagos_run_id, DatasetConformado.dataset_id)
        .join(IngestaPagos, IngestaPagos.id == PagoObservado.ingesta_pagos_id)
        .join(DatasetConformado, DatasetConformado.id == PagoObservado.dataset_conformado_id)
        .where(de_la_cuenta)
        .order_by(
            PagoObservado.fecha_recepcion.desc(),
            PagoObservado.dataset_conformado_id.desc(),
            PagoObservado.source_row.desc(),
        )
        .offset(desplazamiento)
        .limit(limite)
    ).all()
    return total, [PagoVisto(*fila) for fila in filas]


# --- los cortes -----------------------------------------------------------------------------------


def cortes_de_la_cartera(
    s: Session, despacho_id: str, cartera_id: str, al: date | None = None
) -> list[Corte]:
    """Los cortes canonicos de la cartera, en orden de fecha; con `al`, los de fecha hasta ese dia.
    Hay uno por fecha: unos cientos en anos de cortes semanales."""
    consulta = select(CorteCanonico.fecha_corte, CorteCanonico.corte_id).where(
        CorteCanonico.despacho_id == despacho_id, CorteCanonico.cartera_id == cartera_id
    )
    if al is not None:
        consulta = consulta.where(CorteCanonico.fecha_corte <= al)
    return [Corte(*fila) for fila in s.exec(consulta.order_by(CorteCanonico.fecha_corte)).all()]


def fechas_observadas(s: Session, cuenta_canonica_id: int, al: date | None = None) -> list[date]:
    """Las fechas de los cortes en que la cuenta tiene snapshot: solo lee el indice de la
    historia."""
    consulta = select(SnapshotCuenta.fecha_corte).where(
        SnapshotCuenta.cuenta_canonica_id == cuenta_canonica_id
    )
    if al is not None:
        consulta = consulta.where(SnapshotCuenta.fecha_corte <= al)
    return list(s.exec(consulta.order_by(SnapshotCuenta.fecha_corte)).all())


def listar_cortes(
    s: Session,
    despacho_id: str,
    cartera_id: str,
    *,
    desplazamiento: int,
    limite: int,
    descendente: bool = True,
) -> tuple[int, list[CorteVisto], CorteVisto | None]:
    """Una pagina de los cortes de la cartera, el total y el ultimo, este o no en la pagina."""
    de_la_cartera = and_(
        CorteCanonico.despacho_id == despacho_id, CorteCanonico.cartera_id == cartera_id
    )
    total = s.exec(select(func.count()).select_from(CorteCanonico).where(de_la_cartera)).one()
    orden = CorteCanonico.fecha_corte.desc() if descendente else CorteCanonico.fecha_corte
    filas = s.exec(
        _cortes_vistos().where(de_la_cartera).order_by(orden).offset(desplazamiento).limit(limite)
    ).all()
    ultimo = s.exec(
        _cortes_vistos().where(de_la_cartera).order_by(CorteCanonico.fecha_corte.desc()).limit(1)
    ).first()
    return (
        total,
        [CorteVisto(*fila) for fila in filas],
        None if ultimo is None else CorteVisto(*ultimo),
    )


def detalle_del_corte(s: Session, corte_id: UUID) -> DetalleDelCorte:
    """Un corte con su evidencia: el Parquet y el archivo original del dataset que lo produjo, y
    cada ejecucion que lo publico o lo reconocio como fuente equivalente, con su propio original."""
    fila = s.exec(_cortes_vistos().where(CorteCanonico.corte_id == corte_id)).first()
    if fila is None:
        raise CorteNoEncontrado(corte_id)
    visto = CorteVisto(*fila)
    dataset = s.get_one(DatasetConformado, visto.corte.dataset_conformado_id)
    fuentes = [
        FuenteDelCorte(*f)
        for f in s.exec(
            select(EjecucionHistoria, DatasetConformado, Corrida, ArtefactoFuente)
            .join(
                DatasetConformado, DatasetConformado.id == EjecucionHistoria.dataset_conformado_id
            )
            .join(Corrida, Corrida.id == DatasetConformado.corrida_id)
            .join(ArtefactoFuente, ArtefactoFuente.id == DatasetConformado.artefacto_original_id)
            .where(EjecucionHistoria.corte_canonico_id == visto.corte.id)
            .order_by(EjecucionHistoria.terminada_en, EjecucionHistoria.id)
        ).all()
    ]
    return DetalleDelCorte(
        visto,
        conformado=s.get_one(ArtefactoFuente, dataset.artefacto_conformado_id),
        original=s.get_one(ArtefactoFuente, dataset.artefacto_original_id),
        fuentes=fuentes,
    )


# --- los pagos sin cuenta -------------------------------------------------------------------------


@dataclass(frozen=True)
class RelacionDePagos:
    """Como se relacionan los pagos observados de un dataset con las cuentas canonicas, hoy."""

    con_cuenta_observada: int
    sin_cuenta_observada: int
    """Pagos de un CLIENTE_UNICO que ningun corte de la cartera ha traido: SIN_CUENTA_OBSERVADA."""
    clientes_sin_cuenta_observada: int


def relacion_de_pagos(s: Session, dataset_conformado_id: int) -> RelacionDePagos:
    """Cuantos pagos observados del dataset tienen hoy una cuenta canonica con su llave, y cuantos
    no. Se calcula al consultar: si despues llega un corte con ese cliente, el pago se relaciona sin
    que se toque."""
    fila = s.execute(
        text(
            "SELECT count(c.id), count(*) - count(c.id), "
            "count(DISTINCT p.cliente_unico) FILTER (WHERE c.id IS NULL) "
            "FROM pago_observado p LEFT JOIN cuenta_canonica c ON c.despacho_id = p.despacho_id "
            "AND c.cartera_id = p.cartera_id AND c.cliente_unico = p.cliente_unico "
            "WHERE p.dataset_conformado_id = :dataset"
        ),
        {"dataset": dataset_conformado_id},
    ).one()
    return RelacionDePagos(*fila)


# --- lo comun -------------------------------------------------------------------------------------


def _snapshots_vistos():
    return (
        select(SnapshotCuenta, CorteCanonico.corte_id, DatasetConformado.dataset_id)
        .join(CorteCanonico, CorteCanonico.id == SnapshotCuenta.corte_canonico_id)
        .join(DatasetConformado, DatasetConformado.id == CorteCanonico.dataset_conformado_id)
    )


def _cortes_vistos():
    return (
        select(CorteCanonico, DatasetConformado.dataset_id, Corrida.run_id)
        .join(DatasetConformado, DatasetConformado.id == CorteCanonico.dataset_conformado_id)
        .join(Corrida, Corrida.id == DatasetConformado.corrida_id)
    )


def _snapshot_en(s: Session, cuenta_canonica_id: int, fecha: date) -> SnapshotVisto | None:
    fila = s.exec(
        _snapshots_vistos().where(
            SnapshotCuenta.cuenta_canonica_id == cuenta_canonica_id,
            SnapshotCuenta.fecha_corte == fecha,
        )
    ).first()
    return None if fila is None else SnapshotVisto(*fila)


def _enlazar(
    lugares: dict[date, int], actual: SnapshotVisto, anterior: SnapshotVisto | None
) -> SnapshotEnHistoria:
    fecha = actual.snapshot.fecha_corte
    if anterior is None:
        return SnapshotEnHistoria(actual, presencia.enlazar(lugares, fecha, None), None, None)
    previo = anterior.snapshot
    return SnapshotEnHistoria(
        actual,
        presencia.enlazar(lugares, fecha, previo.fecha_corte),
        actual.snapshot.saldo_total - previo.saldo_total,
        actual.snapshot.dias_atraso - previo.dias_atraso,
    )


def _de_la_cuenta(despacho_id: str, cartera_id: str, cliente_unico: str):
    return and_(
        PagoObservado.despacho_id == despacho_id,
        PagoObservado.cartera_id == cartera_id,
        PagoObservado.cliente_unico == cliente_unico,
    )


def _contar_pagos(
    s: Session, despacho_id: str, cartera_id: str, cliente_unico: str, al: date | None
) -> int:
    condicion = _de_la_cuenta(despacho_id, cartera_id, cliente_unico)
    if al is not None:
        # Hasta el final de ese dia, en la hora local de la fuente, que no tiene zona.
        condicion = and_(
            condicion, PagoObservado.fecha_recepcion < datetime.combine(al + timedelta(1), time())
        )
    return s.exec(select(func.count()).select_from(PagoObservado).where(condicion)).one()
