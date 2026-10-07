"""La materializacion historica: de un dataset conformado al modelo historico, todo o nada.

El orden importa:
  1. cuando una fuente oficial publica su dataset conformado, en la misma transaccion se abren su
     EjecucionHistoria EN_PROCESO y su trabajo HISTORIA (`abrir_historia`). No hay un instante en
     que el dataset exista sin su historia pendiente, y si la API o el worker mueren despues, otro
     worker la termina;
  2. un worker toma el trabajo, y `materializar` toma la ejecucion con su fila bloqueada: la
     materializa un solo worker a la vez, y si ya termino no la vuelve a materializar;
  3. el Parquet se comprueba contra su SHA-256 y contra lo que la base dice de el, y se materializa:
     - cartera/v2: si ya hay un corte canonico de esa fecha, el dataset es una fuente equivalente
       (misma firma de contenido) o un conflicto (otra firma), y no se lee nada. Si no, cada lote se
       copia con COPY a una tabla temporal, se insertan las cuentas canonicas nuevas y los snapshots
       con INSERT ... SELECT, se cuentan y se publica el corte;
     - pagos/v1: cada lote se copia con COPY a pago_observado, y se cuenta;
  4. la ejecucion se cierra EXITOSA en la misma transaccion. Si algo falla, se revierte todo y
     queda FALLIDA con su resultado y su detalle: no queda ningun corte, cuenta, snapshot ni pago a
     medias.

La historia se ordena por fecha de corte, no por orden de llegada: un corte atrasado se materializa
igual que uno a tiempo, y nada de lo que ya estaba cambia. Ningun snapshot se actualiza nunca.

Lo que hace segura la entrega repetida no es la cola: es el bloqueo de la ejecucion, sus estados
terminales, la transaccion todo o nada y los indices unicos (una EXITOSA por dataset y version, un
corte por fecha, un snapshot por cuenta y corte, un pago por fila).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import func, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select
from sqlmodel.sql.expression import SelectOfScalar

from motor_cartera.config import Config
from motor_cartera.config import config as config_del_entorno
from motor_cartera.contratos.cartera_v2 import CONTRATO_V2
from motor_cartera.contratos.fuente import ContratoFuente
from motor_cartera.contratos.pagos import CONTRATO_PAGOS
from motor_cartera.db.copia import copiar_csv
from motor_cartera.db.modelos import (
    ArtefactoFuente,
    Corrida,
    CorteCanonico,
    DatasetConformado,
    EjecucionHistoria,
    EstadoHistoria,
    IngestaPagos,
    PagoObservado,
    ResultadoHistoria,
    TipoFuenteHistoria,
    TipoTrabajo,
    ahora,
)
from motor_cartera.db.sesion import insertar_en_savepoint, restriccion, sesion
from motor_cartera.fuentes.almacen import ArtefactoCorrupto, ArtefactoFaltante
from motor_cartera.fuentes.artefactos import almacen_de
from motor_cartera.historia import carga, identidad
from motor_cartera.ingesta.fuente_oficial import Cronometro
from motor_cartera.orquestacion import cola

log = logging.getLogger(__name__)

VERSION_MODELO_HISTORIA = "historia/v1"
"""Cambia si cambia que se guarda de cada corte o de cada pago, o como se resuelve su identidad."""

INDICE_HISTORIA_EXITOSA = "ux_ejecucion_historia_exitosa"
INDICE_HISTORIA_EN_PROCESO = "ux_ejecucion_historia_en_proceso"
CORTE_POR_FECHA = "uq_corte_canonico_fecha"
CUENTA_POR_CLAVE = "uq_cuenta_canonica_clave"

INTENTOS_DE_APERTURA = 3
"""Cuantas veces se revisa y se inserta una ejecucion que pierde la carrera contra otra apertura del
mismo dataset, como en los motores."""

TIPO_DEL_CONTRATO = {
    CONTRATO_V2.version: TipoFuenteHistoria.CARTERA,
    CONTRATO_PAGOS.version: TipoFuenteHistoria.PAGOS,
}
"""Que datasets conformados tienen historia: los de las dos fuentes oficiales."""


class DatasetSinHistoria(Exception):
    """El dataset conformado es de un contrato que no tiene modelo historico."""

    def __init__(self, dataset: DatasetConformado) -> None:
        super().__init__(
            f"El dataset {dataset.dataset_id} es de {dataset.contrato}, que no tiene historia: "
            f"solo la tienen {', '.join(TIPO_DEL_CONTRATO)}."
        )
        self.dataset = dataset


class HistoriaYaMaterializada(Exception):
    """El dataset ya se materializo con exito con esta version del modelo."""

    def __init__(self, previa: EjecucionHistoria) -> None:
        super().__init__(
            f"El dataset ya se materializo con {previa.version_modelo}: la ejecucion "
            f"{previa.historia_run_id}."
        )
        self.previa = previa


class HistoriaEnProceso(Exception):
    """El dataset ya se esta materializando con esta version del modelo."""

    def __init__(self, activa: EjecucionHistoria) -> None:
        super().__init__(
            f"El dataset ya se esta materializando con {activa.version_modelo}: la ejecucion "
            f"{activa.historia_run_id}."
        )
        self.activa = activa


class _NoSePublica(Exception):
    """Una razon conocida para no publicar nada, con su resultado. El mensaje va tal cual al detalle
    de la ejecucion: es para una persona y no lleva datos de las cuentas."""

    def __init__(self, resultado: ResultadoHistoria, mensaje: str) -> None:
        super().__init__(mensaje)
        self.resultado = resultado


@dataclass
class _Avance:
    """Cuantos registros del dataset se leyeron: es lo que una FALLIDA dice que alcanzo a leer."""

    leidos: int = 0


@dataclass(frozen=True)
class _Clave:
    """La cartera y la fecha de un corte."""

    despacho_id: str
    cartera_id: str
    fecha_corte: date


def abrir_historia(
    s: Session, dataset: DatasetConformado, *, max_intentos: int
) -> EjecucionHistoria:
    """La ejecucion historica EN_PROCESO del dataset y su trabajo HISTORIA, en la transaccion de
    quien llama: envia los INSERT y no confirma. La ingesta que publica el dataset los confirma con
    el, y el backfill, solos.

    Levanta DatasetSinHistoria si el contrato no tiene historia, HistoriaYaMaterializada si ya hay
    una EXITOSA con esta version del modelo, y HistoriaEnProceso si hay un intento activo. Como en
    los motores, estas revisiones son la via amable; la garantia son los dos indices unicos
    parciales, y una apertura que pierde la carrera vuelve a revisar dentro de un SAVEPOINT.
    """
    tipo = TIPO_DEL_CONTRATO.get(dataset.contrato)
    if tipo is None:
        raise DatasetSinHistoria(dataset)
    for _ in range(INTENTOS_DE_APERTURA):
        previa = s.exec(_del_dataset(dataset.id, EstadoHistoria.EXITOSA)).first()
        if previa is not None:
            raise HistoriaYaMaterializada(previa)
        activa = s.exec(_del_dataset(dataset.id, EstadoHistoria.EN_PROCESO)).first()
        if activa is not None:
            raise HistoriaEnProceso(activa)
        ejecucion = EjecucionHistoria(
            dataset_conformado_id=dataset.id,
            tipo_fuente=tipo,
            version_modelo=VERSION_MODELO_HISTORIA,
        )
        error = insertar_en_savepoint(s, ejecucion)
        if error is None:
            break
        if restriccion(error) != INDICE_HISTORIA_EN_PROCESO:
            raise error
    else:
        raise error
    cola.crear(s, TipoTrabajo.HISTORIA, ejecucion.id, max_intentos=max_intentos)
    log.info(
        "historia %s abierta para el dataset %s", ejecucion.historia_run_id, dataset.dataset_id
    )
    return ejecucion


def ejecutar_historia(ejecucion_id: int, *, config: Config | None = None) -> None:
    """Lo que ejecuta un trabajo HISTORIA. Si la ejecucion ya termino no hace nada. Si el almacen no
    tiene el Parquet, es un error del worker y no de la historia (el volumen puede no estar
    montado): levanta ArtefactoFaltante sin tocar la ejecucion, y el trabajo se reintenta."""
    config = config or config_del_entorno
    with sesion() as s:
        ejecucion = s.get_one(EjecucionHistoria, ejecucion_id)
        if ejecucion.estado != EstadoHistoria.EN_PROCESO:
            log.info("historia %s ya termino %s", ejecucion.historia_run_id, ejecucion.estado)
            return
        sha256 = s.exec(
            select(ArtefactoFuente.sha256)
            .join(
                DatasetConformado, DatasetConformado.artefacto_conformado_id == ArtefactoFuente.id
            )
            .where(DatasetConformado.id == ejecucion.dataset_conformado_id)
        ).one()
    if not almacen_de(config).existe(sha256):
        raise ArtefactoFaltante(sha256)
    materializar(ejecucion_id, config=config)


def materializar(
    ejecucion_id: int, *, config: Config | None = None, cronometro: Cronometro | None = None
) -> None:
    """Materializa el dataset de la ejecucion y la deja en un estado terminal.

    La ejecucion se toma con su fila bloqueada hasta el commit o el rollback. Todo va en una sola
    transaccion: el corte, las cuentas nuevas y los snapshots, o los pagos observados, y el cierre.
    Si algo falla, se revierte todo y, en otra transaccion, la ejecucion queda FALLIDA si sigue
    EN_PROCESO: ningun camino de error degrada un estado terminal.

    No levanta excepciones, salvo dos: ArtefactoFaltante, que es un error del worker y deja la
    ejecucion EN_PROCESO para que el trabajo se reintente, y HistoriaYaMaterializada, si otra
    ejecucion del mismo dataset publico primero (esta ya quedo FALLIDA).
    """
    config = config or config_del_entorno
    cronometro = cronometro or Cronometro()
    with sesion() as s:
        ejecucion = s.exec(
            select(EjecucionHistoria).where(EjecucionHistoria.id == ejecucion_id).with_for_update()
        ).one()
        if ejecucion.estado != EstadoHistoria.EN_PROCESO:
            log.warning(
                "historia %s ya termino %s; no se materializa otra vez",
                ejecucion.historia_run_id,
                ejecucion.estado,
            )
            return
        # Se leen ahora: despues de un rollback la ejecucion en memoria caduca.
        etiqueta, dataset_id = ejecucion.historia_run_id, ejecucion.dataset_conformado_id
        avance = _Avance()
        try:
            _materializar(s, ejecucion, avance, config, cronometro)
            with cronometro.fase("commit"):
                s.commit()  # el unico commit que publica historia
        except _NoSePublica as exc:
            s.rollback()
            _fallar(s, ejecucion_id, etiqueta, exc.resultado, str(exc), avance.leidos)
        except carga.DatosInconsistentes as exc:
            s.rollback()
            _fallar(
                s,
                ejecucion_id,
                etiqueta,
                ResultadoHistoria.DATOS_INCONSISTENTES,
                str(exc),
                avance.leidos,
            )
        except ArtefactoCorrupto as exc:
            s.rollback()
            _fallar(s, ejecucion_id, etiqueta, ResultadoHistoria.ARTEFACTO_CORRUPTO, str(exc), 0)
        except ArtefactoFaltante:
            s.rollback()
            raise
        except IntegrityError as exc:
            s.rollback()
            if restriccion(exc) == INDICE_HISTORIA_EXITOSA:
                _fallar(
                    s,
                    ejecucion_id,
                    etiqueta,
                    ResultadoHistoria.YA_MATERIALIZADA,
                    f"Otra ejecucion materializo este dataset con {VERSION_MODELO_HISTORIA} "
                    "mientras esta se procesaba; no se publica dos veces.",
                    avance.leidos,
                )
                previa = s.exec(_del_dataset(dataset_id, EstadoHistoria.EXITOSA)).one()
                raise HistoriaYaMaterializada(previa) from exc
            log.exception("historia %s: violacion de integridad inesperada", etiqueta)
            _fallar(
                s,
                ejecucion_id,
                etiqueta,
                ResultadoHistoria.ERROR_INTERNO,
                "Error interno al publicar; ver la bitacora del servicio.",
                avance.leidos,
            )
        except Exception as exc:
            s.rollback()
            log.exception("historia %s: error inesperado", etiqueta)
            _fallar(
                s,
                ejecucion_id,
                etiqueta,
                ResultadoHistoria.ERROR_INTERNO,
                f"Error interno ({type(exc).__name__}); ver la bitacora.",
                avance.leidos,
            )


def _materializar(
    s: Session,
    ejecucion: EjecucionHistoria,
    avance: _Avance,
    config: Config,
    cronometro: Cronometro,
) -> None:
    if ejecucion.version_modelo != VERSION_MODELO_HISTORIA:
        raise _NoSePublica(
            ResultadoHistoria.VERSION_NO_SOPORTADA,
            f"La ejecucion pide {ejecucion.version_modelo} y este servicio solo materializa "
            f"{VERSION_MODELO_HISTORIA}; no se publico nada.",
        )
    dataset = s.get_one(DatasetConformado, ejecucion.dataset_conformado_id)
    if ejecucion.tipo_fuente == TipoFuenteHistoria.CARTERA:
        _cartera(s, ejecucion, dataset, avance, config, cronometro)
    else:
        _pagos(s, ejecucion, dataset, avance, config, cronometro)


# --- cartera/v2: un corte canonico --------------------------------------------------------------


def _cartera(
    s: Session,
    ejecucion: EjecucionHistoria,
    dataset: DatasetConformado,
    avance: _Avance,
    config: Config,
    cronometro: Cronometro,
) -> None:
    corrida = s.get_one(Corrida, dataset.corrida_id)
    clave = _Clave(corrida.despacho_id, corrida.cartera_id, corrida.fecha_corte)
    existente = _corte(s, clave)
    if existente is not None:
        _equivalente_o_conflicto(s, ejecucion, dataset, existente)
        return

    with _parquet(s, dataset, CONTRATO_V2, config, cronometro) as archivo:
        _preparar_staging(s)
        bloques = carga.bloques_snapshot(
            archivo,
            despacho_id=clave.despacho_id,
            cartera_id=clave.cartera_id,
            filas_por_lote=config.filas_por_lote,
        )
        with cronometro.fase("staging"):
            _copiar(s, carga.STAGING, carga.CAMPOS_SNAPSHOT, bloques, avance)
    _comprobar_lectura(avance.leidos, dataset)
    s.execute(text(f"ANALYZE {carga.STAGING}"))

    corte = _insertar_corte(s, clave, dataset, avance.leidos)
    if corte is None:
        # Otra ejecucion publico ese corte mientras esta leia su dataset: el INSERT espero a que
        # confirmara, y ahora se juzga contra el.
        _equivalente_o_conflicto(s, ejecucion, dataset, _corte(s, clave))
        return
    with cronometro.fase("cuentas"):
        nuevas = _crear_cuentas(s, clave)
    with cronometro.fase("snapshots"):
        publicados = _insertar_snapshots(s, corte.id, clave)
    if publicados != avance.leidos:
        raise _NoSePublica(
            ResultadoHistoria.DATOS_INCONSISTENTES,
            f"Se leyeron {avance.leidos:,} registros del corte y se resolvieron {publicados:,} "
            "snapshots; no se publico nada.",
        )
    _cerrar(
        s,
        ejecucion,
        ResultadoHistoria.CORTE_PUBLICADO,
        corte=corte,
        leidos=avance.leidos,
        publicados=publicados,
        detalle=(
            f"Se publico el corte canonico {corte.corte_id} del {clave.fecha_corte.isoformat()} "
            f"con {publicados:,} snapshots; {nuevas:,} cuentas canonicas nuevas. "
            f"{VERSION_MODELO_HISTORIA}."
        ),
    )


def _equivalente_o_conflicto(
    s: Session, ejecucion: EjecucionHistoria, dataset: DatasetConformado, existente: CorteCanonico
) -> None:
    """El corte de esa fecha ya existe. Con la misma firma de contenido, el dataset es una fuente
    equivalente: la ejecucion termina EXITOSA apuntando a el, sin leer ni duplicar nada. Con otra,
    es un conflicto: nadie elige un archivo, el corte publicado no cambia y la ejecucion falla."""
    productor = s.get_one(DatasetConformado, existente.dataset_conformado_id)
    fecha = existente.fecha_corte.isoformat()
    if existente.firma_contenido != dataset.firma_contenido:
        raise _NoSePublica(
            ResultadoHistoria.CORTE_CANONICO_CONFLICTIVO,
            f"Ya existe el corte canonico {existente.corte_id} del {fecha} de "
            f"{existente.despacho_id}/{existente.cartera_id}, publicado desde el dataset "
            f"{productor.dataset_id} con la firma de contenido {existente.firma_contenido}, y "
            f"este dataset trae otra, {dataset.firma_contenido}. La historia no se sobrescribe: "
            "el corte publicado no cambia, y una correccion explicita no es de "
            f"{VERSION_MODELO_HISTORIA}.",
        )
    _cerrar(
        s,
        ejecucion,
        ResultadoHistoria.FUENTE_EQUIVALENTE,
        corte=existente,
        leidos=0,
        publicados=0,
        detalle=(
            f"Fuente equivalente: el corte canonico {existente.corte_id} del {fecha} ya existe "
            f"con la misma firma de contenido, publicado desde el dataset {productor.dataset_id}. "
            "No se duplico ningun snapshot; esta ejecucion queda como su procedencia."
        ),
    )


def _preparar_staging(s: Session) -> None:
    s.execute(text(carga.crear_staging()))


def _insertar_corte(
    s: Session, clave: _Clave, dataset: DatasetConformado, cuentas: int
) -> CorteCanonico | None:
    """El corte, si nadie publico ya uno de esa fecha; si no, None. Si otra transaccion lo esta
    publicando, PostgreSQL espera a que termine antes de decidir."""
    fila = s.execute(
        insert(CorteCanonico)
        .values(
            corte_id=identidad.corte_id(clave.despacho_id, clave.cartera_id, clave.fecha_corte),
            despacho_id=clave.despacho_id,
            cartera_id=clave.cartera_id,
            fecha_corte=clave.fecha_corte,
            firma_contenido=dataset.firma_contenido,
            version_modelo=VERSION_MODELO_HISTORIA,
            cuentas=cuentas,
            dataset_conformado_id=dataset.id,
            creado_en=ahora(),
        )
        .on_conflict_do_nothing(constraint=CORTE_POR_FECHA)
        .returning(CorteCanonico.id)
    ).first()
    return None if fila is None else s.get_one(CorteCanonico, fila[0])


def _crear_cuentas(s: Session, clave: _Clave) -> int:
    """Las cuentas canonicas que el corte trae por primera vez, en una sola operacion. En orden de
    CLIENTE_UNICO: dos cortes que se materializan a la vez y comparten cuentas nuevas las bloquean
    en el mismo orden, asi que uno espera al otro y nunca se bloquean entre si. Si otra transaccion
    ya creo una, no se crea dos veces. Devuelve cuantas creo."""
    return s.execute(
        text(
            "INSERT INTO cuenta_canonica (cuenta_id, despacho_id, cartera_id, cliente_unico, "
            "creada_en) "
            "SELECT st.cuenta_id, CAST(:despacho AS VARCHAR), CAST(:cartera AS VARCHAR), "
            "st.cliente_unico, now() "
            f"FROM {carga.STAGING} st "
            "WHERE NOT EXISTS (SELECT 1 FROM cuenta_canonica c WHERE c.despacho_id = :despacho "
            "AND c.cartera_id = :cartera AND c.cliente_unico = st.cliente_unico) "
            f"ORDER BY st.cliente_unico ON CONFLICT ON CONSTRAINT {CUENTA_POR_CLAVE} DO NOTHING"
        ),
        {"despacho": clave.despacho_id, "cartera": clave.cartera_id},
    ).rowcount


def _insertar_snapshots(s: Session, corte_id: int, clave: _Clave) -> int:
    """Un snapshot por cada registro del corte, con su cuenta canonica resuelta por JOIN, en orden
    de cuenta: asi el indice de la historia se llena en orden. Devuelve cuantos inserto."""
    propias = [
        c.columna for c in carga.CAMPOS_SNAPSHOT if c.columna not in ("cliente_unico", "cuenta_id")
    ]
    return s.execute(
        text(
            "INSERT INTO snapshot_cuenta (corte_canonico_id, cuenta_canonica_id, fecha_corte, "
            f"{', '.join(propias)}) "
            f"SELECT :corte, c.id, :fecha, {', '.join('st.' + p for p in propias)} "
            f"FROM {carga.STAGING} st JOIN cuenta_canonica c ON c.despacho_id = :despacho "
            "AND c.cartera_id = :cartera AND c.cliente_unico = st.cliente_unico ORDER BY c.id"
        ),
        {
            "corte": corte_id,
            "fecha": clave.fecha_corte,
            "despacho": clave.despacho_id,
            "cartera": clave.cartera_id,
        },
    ).rowcount


def _corte(s: Session, clave: _Clave) -> CorteCanonico | None:
    return s.exec(
        select(CorteCanonico).where(
            CorteCanonico.despacho_id == clave.despacho_id,
            CorteCanonico.cartera_id == clave.cartera_id,
            CorteCanonico.fecha_corte == clave.fecha_corte,
        )
    ).one_or_none()


# --- pagos/v1: los pagos observados ---------------------------------------------------------------


def _pagos(
    s: Session,
    ejecucion: EjecucionHistoria,
    dataset: DatasetConformado,
    avance: _Avance,
    config: Config,
    cronometro: Cronometro,
) -> None:
    ingesta = s.get_one(IngestaPagos, dataset.ingesta_pagos_id)
    with _parquet(s, dataset, CONTRATO_PAGOS, config, cronometro) as archivo:
        bloques = carga.bloques_pagos(
            archivo,
            dataset_conformado_id=dataset.id,
            dataset_id=dataset.dataset_id,
            ingesta_pagos_id=ingesta.id,
            despacho_id=ingesta.despacho_id,
            cartera_id=ingesta.cartera_id,
            filas_por_lote=config.filas_por_lote,
        )
        with cronometro.fase("pagos"):
            _copiar(s, PagoObservado.__tablename__, carga.CAMPOS_PAGO, bloques, avance)
    _comprobar_lectura(avance.leidos, dataset)
    publicados = s.exec(
        select(func.count())
        .select_from(PagoObservado)
        .where(PagoObservado.dataset_conformado_id == dataset.id)
    ).one()
    if publicados != avance.leidos:
        raise _NoSePublica(
            ResultadoHistoria.DATOS_INCONSISTENTES,
            f"Se leyeron {avance.leidos:,} movimientos y quedaron {publicados:,} pagos observados; "
            "no se publico nada.",
        )
    _cerrar(
        s,
        ejecucion,
        ResultadoHistoria.PAGOS_PUBLICADOS,
        corte=None,
        leidos=avance.leidos,
        publicados=publicados,
        detalle=(
            f"Se publicaron {publicados:,} pagos observados de la ingesta {ingesta.pagos_run_id}, "
            "uno por fila, tal como llegaron: sin deduplicar, conciliar ni atribuir. "
            f"{VERSION_MODELO_HISTORIA}."
        ),
    )


# --- lo comun -------------------------------------------------------------------------------------


@contextmanager
def _parquet(
    s: Session,
    dataset: DatasetConformado,
    contrato: ContratoFuente,
    config: Config,
    cronometro: Cronometro,
) -> Iterator:
    """El Parquet conformado del dataset, comprobado contra su SHA-256 y contra su registro. Nunca
    el archivo original: la historia no vuelve a interpretar un xlsx, un csv ni un zip."""
    artefacto = s.get_one(ArtefactoFuente, dataset.artefacto_conformado_id)
    almacen = almacen_de(config)
    with cronometro.fase("verificacion"):
        almacen.verificar(artefacto.sha256, artefacto.tamano_bytes)
    with almacen.como_archivo(artefacto.sha256) as ruta:
        archivo = carga.abrir_parquet(ruta, contrato, dataset.filas)
        try:
            yield archivo
        finally:
            archivo.close()


def _copiar(
    s: Session, tabla: str, campos, bloques: Iterable[carga.Bloque], avance: _Avance
) -> None:
    def contados() -> Iterator[bytes]:
        for bloque in bloques:
            avance.leidos += bloque.filas
            yield bloque.csv

    copiar_csv(s, tabla, carga.columnas(campos), contados())


def _comprobar_lectura(leidos: int, dataset: DatasetConformado) -> None:
    if leidos != dataset.filas:
        raise _NoSePublica(
            ResultadoHistoria.DATOS_INCONSISTENTES,
            f"El dataset dice {dataset.filas:,} registros y se leyeron {leidos:,}; no se publico "
            "nada.",
        )


def _cerrar(
    s: Session,
    ejecucion: EjecucionHistoria,
    resultado: ResultadoHistoria,
    *,
    corte: CorteCanonico | None,
    leidos: int,
    publicados: int,
    detalle: str,
) -> None:
    """EXITOSA, con su resultado y su corte, en la transaccion que publico. Envia el cambio pero no
    confirma: una carrera con otra ejecucion EXITOSA del mismo dataset se descubre aqui, todavia
    dentro de la transaccion."""
    ejecucion.estado = EstadoHistoria.EXITOSA
    ejecucion.resultado = resultado.value
    ejecucion.corte_canonico_id = None if corte is None else corte.id
    ejecucion.registros_leidos = leidos
    ejecucion.registros_publicados = publicados
    ejecucion.detalle = detalle
    ejecucion.terminada_en = ahora()
    s.add(ejecucion)
    s.flush()


def _fallar(
    s: Session,
    ejecucion_id: int,
    etiqueta: UUID,
    resultado: ResultadoHistoria,
    motivo: str,
    leidos: int,
) -> None:
    """FALLIDA, solo si sigue EN_PROCESO: un fallo que llega tarde no cambia un estado terminal. No
    borra nada: lo de este intento ya lo quito el rollback."""
    registrado = s.execute(
        update(EjecucionHistoria)
        .where(
            EjecucionHistoria.id == ejecucion_id,
            EjecucionHistoria.estado == EstadoHistoria.EN_PROCESO,
        )
        .values(
            estado=EstadoHistoria.FALLIDA,
            resultado=resultado.value,
            registros_leidos=leidos,
            registros_publicados=0,
            detalle=motivo,
            terminada_en=ahora(),
        )
        .execution_options(synchronize_session=False)
    ).rowcount
    s.commit()
    if registrado:
        log.warning("historia %s fallida (%s): %s", etiqueta, resultado.value, motivo)
    else:
        log.warning("historia %s ya habia terminado; este fallo no la cambia: %s", etiqueta, motivo)


def _del_dataset(dataset_id: int, estado: EstadoHistoria) -> SelectOfScalar[EjecucionHistoria]:
    """La ejecucion del dataset con historia/v1 en ese estado. De EXITOSA y de EN_PROCESO hay a lo
    mas una: lo garantizan los indices unicos parciales."""
    return select(EjecucionHistoria).where(
        EjecucionHistoria.dataset_conformado_id == dataset_id,
        EjecucionHistoria.version_modelo == VERSION_MODELO_HISTORIA,
        EjecucionHistoria.estado == estado,
    )
