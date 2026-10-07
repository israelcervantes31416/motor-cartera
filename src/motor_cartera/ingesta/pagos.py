"""La ingesta de pagos/v1: los movimientos economicos de un archivo, validados y conformados.

No es una corrida de cartera: una corrida publica cuentas, que es un corte, y una ingesta de pagos
acepta movimientos, que son hechos economicos de un periodo. Por eso es su propia entidad
(IngestaPagos), con su propio identificador publico (pagos_run_id), sus rechazos y su trabajo en la
cola durable, y no encadena nada: la conciliacion y la atribucion son de un motor posterior.

En la transaccion que tiene la ingesta bloqueada: se vuelve a firmar el artefacto, se revisa la
estructura (las 23 columnas exactas), se juzga cada movimiento por lotes, se decide con la barrera
de pagos y, si se acepta, se escribe el dataset conformado con TODOS los movimientos validos, tal
como llegaron: no se deduplica ninguno. Con el dataset se abren, en la misma transaccion, su
ejecucion historica y su trabajo HISTORIA, que materializa cada movimiento como un pago observado.

La barrera de pagos es conservadora: por omision la tolerancia es 0
(MC_TOLERANCIA_RECHAZO_PAGOS), asi que un solo movimiento invalido rechaza el archivo entero. Un
archivo de dinero aceptado a medias subestimaria la recuperacion sin que nadie lo notara; sus
rechazos quedan a la vista, con su fila, sus valores y sus motivos.
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import case, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select
from sqlmodel.sql.expression import SelectOfScalar

from motor_cartera.config import Config, config
from motor_cartera.contratos.fuente import ErrorDeEstructura
from motor_cartera.contratos.pagos import CONTRATO_PAGOS, VERSION_CONTRATO_PAGOS
from motor_cartera.db.copia import Jsonb, copiar
from motor_cartera.db.modelos import (
    ArtefactoFuente,
    DatasetConformado,
    EstadoIngestaPagos,
    IngestaPagos,
    ahora,
)
from motor_cartera.db.sesion import insertar_en_savepoint, restriccion, sesion
from motor_cartera.fuentes.almacen import ArtefactoCorrupto
from motor_cartera.fuentes.artefactos import (
    ArtefactoGuardado,
    almacen_de,
    guardar_artefacto,
    guardar_contenido,
    registrar_artefacto,
)
from motor_cartera.fuentes.conformado import EscritorConformado
from motor_cartera.fuentes.formatos import Formato
from motor_cartera.fuentes.lotes import abrir_fuente
from motor_cartera.historia.ejecuciones import abrir_historia
from motor_cartera.ingesta.fuente_oficial import Cronometro, JuicioDeFuente, Rechazado
from motor_cartera.ingesta.lectores import ErrorDeLectura

log = logging.getLogger(__name__)

INDICE_PAGOS_PUBLICADOS = "ux_ingesta_pagos_firma_publicada"
INDICE_PAGOS_EN_PROCESO = "ux_ingesta_pagos_firma_en_proceso"
INTENTOS_DE_APERTURA = 3


class PagosDuplicados(Exception):
    """Ese mismo archivo de pagos ya se acepto, o se esta procesando en otra ingesta."""

    def __init__(self, previa: IngestaPagos) -> None:
        if previa.estado == EstadoIngestaPagos.EXITOSA:
            mensaje = f"Este archivo de pagos ya lo acepto la ingesta {previa.pagos_run_id}."
        else:
            mensaje = (
                f"Este archivo de pagos ya se esta procesando en la ingesta {previa.pagos_run_id}."
            )
        super().__init__(mensaje)
        self.previa = previa


def abrir_ingesta_pagos(
    s: Session,
    *,
    origen: str,
    contenido: bytes | None = None,
    guardado: ArtefactoGuardado | None = None,
    tolerancia: float | None = None,
    confirmar: bool = True,
    config: Config = config,
) -> IngestaPagos:
    """Registra la ingesta EN_PROCESO, apuntando a su artefacto, antes de leer nada.

    Como una corrida: el archivo ya esta guardado (`guardado`) o se guarda aqui (`contenido`) antes
    de tocar la base, y el mismo archivo no se acepta dos veces ni se procesa dos veces a la vez
    (PagosDuplicados). Lo garantizan dos indices unicos parciales sobre la firma; la revision de
    aqui es la via amable, que sabe decir cual ingesta lo tiene.
    """
    if (contenido is None) == (guardado is None):
        raise ValueError("Una ingesta se abre con su contenido o con su artefacto ya guardado.")
    if guardado is None:
        guardado = guardar_contenido(almacen_de(config), contenido, origen)
    firma = guardado.objeto.sha256
    artefacto = registrar_artefacto(s, guardado)
    for _ in range(INTENTOS_DE_APERTURA):
        previa = s.exec(_previa(firma)).first()
        if previa is not None:
            raise PagosDuplicados(previa)
        ingesta = IngestaPagos(
            artefacto_fuente_id=artefacto.id,
            origen=origen,
            firma=firma,
            version_contrato=VERSION_CONTRATO_PAGOS,
            tolerancia_rechazo=(
                config.tolerancia_rechazo_pagos if tolerancia is None else tolerancia
            ),
            despacho_id=config.despacho_id,
            cartera_id=config.cartera_id,
        )
        error = insertar_en_savepoint(s, ingesta)
        if error is None:
            break
        if restriccion(error) != INDICE_PAGOS_EN_PROCESO:
            raise error
    else:
        raise error
    if confirmar:
        s.commit()
        s.refresh(ingesta)
    log.info("ingesta de pagos %s abierta para %s", ingesta.pagos_run_id, origen)
    return ingesta


def procesar_ingesta_pagos(ingesta_id: int, *, config: Config = config) -> None:
    """Juzga y, si pasa la barrera, acepta los movimientos de la ingesta. La deja terminal.

    Se toma con su fila bloqueada y no se vuelve a procesar si ya termino: una segunda entrega del
    mismo trabajo no hace nada. Todo va en una transaccion: si algo falla, se revierte y la ingesta
    queda FALLIDA con el motivo, si sigue EN_PROCESO. No levanta: corre en un worker.
    """
    with sesion() as s:
        ingesta = s.exec(
            select(IngestaPagos).where(IngestaPagos.id == ingesta_id).with_for_update()
        ).one()
        if ingesta.estado != EstadoIngestaPagos.EN_PROCESO:
            log.warning(
                "ingesta de pagos %s ya termino %s; no se procesa otra vez",
                ingesta.pagos_run_id,
                ingesta.estado,
            )
            return
        etiqueta = ingesta.pagos_run_id
        try:
            juzgar_y_aceptar(s, ingesta, config=config)
            s.commit()
        except (ErrorDeLectura, ErrorDeEstructura, ArtefactoCorrupto) as exc:
            s.rollback()
            _fallar(s, ingesta_id, etiqueta, str(exc))
        except IntegrityError as exc:
            s.rollback()
            if restriccion(exc) == INDICE_PAGOS_PUBLICADOS:
                motivo = (
                    "Otra ingesta acepto este mismo archivo mientras esta se procesaba; no se "
                    "acepta dos veces."
                )
            else:
                log.exception("ingesta de pagos %s: violacion de integridad inesperada", etiqueta)
                motivo = "Error interno al aceptar; ver la bitacora del servicio."
            _fallar(s, ingesta_id, etiqueta, motivo)
        except Exception as exc:
            log.exception("ingesta de pagos %s: error inesperado", etiqueta)
            s.rollback()
            _fallar(
                s, ingesta_id, etiqueta, f"Error interno ({type(exc).__name__}); ver la bitacora."
            )


def juzgar_y_aceptar(
    s: Session, ingesta: IngestaPagos, *, config: Config, cronometro: Cronometro | None = None
) -> str:
    """Juzga los movimientos y, si pasan la barrera, escribe su dataset conformado; deja la ingesta
    en su estado terminal y devuelve el veredicto. No confirma."""
    cronometro = cronometro or Cronometro()
    artefacto = s.get_one(ArtefactoFuente, ingesta.artefacto_fuente_id)
    almacen = almacen_de(config)
    with cronometro.fase("verificacion"):
        almacen.verificar(artefacto.sha256, artefacto.tamano_bytes)
    with (
        tempfile.TemporaryDirectory(prefix="motor-cartera-") as temporal,
        almacen.como_archivo(artefacto.sha256) as ruta,
    ):
        directorio = Path(temporal)
        with abrir_fuente(
            ruta,
            artefacto.formato,
            ingesta.origen,
            CONTRATO_PAGOS,
            filas_por_lote=config.filas_por_lote,
        ) as fuente:
            juicio = JuicioDeFuente(
                CONTRATO_PAGOS,
                directorio,
                rechazar=lambda rechazados: _copiar_rechazos(s, ingesta.id, rechazados),
                filas_por_lote=config.filas_por_lote,
                cronometro=cronometro,
            )
            juicio.primera_pasada(fuente.lotes())
            if juicio.leidas == 0:
                raise ErrorDeLectura(f"{fuente.origen}: no trae ningun movimiento.")
            origen = fuente.origen

        estado, veredicto = decidir_pagos(
            juicio.leidas, juicio.rechazadas, ingesta.tolerancia_rechazo
        )
        acepta = estado == EstadoIngestaPagos.EXITOSA
        escritor = None
        if acepta:
            escritor = EscritorConformado(
                directorio / "conformado.parquet",
                CONTRATO_PAGOS,
                {"contrato": CONTRATO_PAGOS.version, "artefacto_original": artefacto.sha256},
            )

        def conformar(canonicos, hojas) -> None:
            with cronometro.fase("conformado"):
                escritor.escribir(canonicos, hojas)

        firma = juicio.segunda_pasada(conformar if acepta else None)
        firma_contenido = firma.hexdigest()
        if escritor is not None:
            escritor.cerrar()
            with cronometro.fase("almacen_conformado"):
                with escritor.ruta.open("rb") as archivo:
                    objeto = almacen.guardar(archivo)
                nombre = f"pagos_v1_{ingesta.pagos_run_id}.parquet"
                parquet = registrar_artefacto(s, ArtefactoGuardado(objeto, Formato.PARQUET, nombre))
                dataset = DatasetConformado(
                    contrato=CONTRATO_PAGOS.version,
                    ingesta_pagos_id=ingesta.id,
                    artefacto_original_id=artefacto.id,
                    artefacto_conformado_id=parquet.id,
                    firma_contenido=firma_contenido,
                    filas=escritor.filas,
                    columnas=len(CONTRATO_PAGOS.columnas),
                )
                s.add(dataset)
                s.flush()
                abrir_historia(s, dataset, max_intentos=config.worker_max_intentos)

    ingesta.estado = estado
    ingesta.filas_leidas = juicio.leidas
    ingesta.filas_validas = juicio.validas
    ingesta.filas_rechazadas = juicio.rechazadas
    ingesta.firma_contenido = firma_contenido
    ingesta.detalle = f"{veredicto} Origen: {origen.rstrip('.')}."
    ingesta.terminada_en = ahora()
    s.add(ingesta)
    s.flush()
    log.info("ingesta de pagos %s: %s", ingesta.pagos_run_id, veredicto)
    return veredicto


def decidir_pagos(
    leidas: int, rechazadas: int, tolerancia: float
) -> tuple[EstadoIngestaPagos, str]:
    """La barrera de pagos: se aceptan los movimientos solo si los rechazados caben en la
    tolerancia, que por omision es 0. RECHAZADA si se juzgo y no paso; nunca a medias."""
    if leidas <= 0:
        raise ValueError("Una ingesta sin movimientos no se juzga: es un error de lectura.")
    validas = leidas - rechazadas
    tasa = rechazadas / leidas
    if validas == 0:
        return EstadoIngestaPagos.RECHAZADA, (
            "Ningun movimiento cumple el contrato; no se acepto nada."
        )
    if tasa > tolerancia:
        return EstadoIngestaPagos.RECHAZADA, (
            f"{rechazadas:,} de {leidas:,} movimientos ({tasa:.1%}) no cumplen el contrato y la "
            f"tolerancia es {tolerancia:.1%}; no se acepto nada."
        )
    if rechazadas == 0:
        return EstadoIngestaPagos.EXITOSA, f"Se aceptaron {validas:,} movimientos."
    return EstadoIngestaPagos.EXITOSA, (
        f"Se aceptaron {validas:,} movimientos; {rechazadas:,} ({tasa:.1%}) se rechazaron, dentro "
        f"de la tolerancia de {tolerancia:.1%}."
    )


def ingerir_pagos(ruta: str | Path, *, tolerancia: float | None = None) -> IngestaPagos:
    """Una ingesta de pagos completa en primer plano, en modo directo: sin cola. Para las pruebas;
    el CLI usa la cola, como la API."""
    ruta = Path(ruta)
    with ruta.open("rb") as archivo:
        guardado = guardar_artefacto(almacen_de(config), archivo, ruta.name)
    with sesion() as s:
        ingesta = abrir_ingesta_pagos(s, origen=ruta.name, guardado=guardado, tolerancia=tolerancia)
    procesar_ingesta_pagos(ingesta.id)
    with sesion() as s:
        return s.get_one(IngestaPagos, ingesta.id)


def _previa(firma: str) -> SelectOfScalar[IngestaPagos]:
    return (
        select(IngestaPagos)
        .where(
            IngestaPagos.firma == firma,
            IngestaPagos.estado.in_([EstadoIngestaPagos.EXITOSA, EstadoIngestaPagos.EN_PROCESO]),
        )
        .order_by(case((IngestaPagos.estado == EstadoIngestaPagos.EXITOSA, 0), else_=1))
    )


def _copiar_rechazos(s: Session, ingesta_id: int, rechazados: list[Rechazado]) -> None:
    copiar(
        s,
        "rechazo_pago",
        ("ingesta_pagos_id", "fila", "valores", "motivos"),
        (
            (ingesta_id, r.fila, Jsonb(r.valores), Jsonb([asdict(m) for m in r.motivos]))
            for r in rechazados
        ),
    )


def _fallar(s: Session, ingesta_id: int, etiqueta: object, motivo: str) -> None:
    """FALLIDA, solo si sigue EN_PROCESO: un fallo que llega tarde no cambia un estado terminal."""
    registrado = s.execute(
        update(IngestaPagos)
        .where(IngestaPagos.id == ingesta_id, IngestaPagos.estado == EstadoIngestaPagos.EN_PROCESO)
        .values(estado=EstadoIngestaPagos.FALLIDA, detalle=motivo, terminada_en=ahora())
        .execution_options(synchronize_session=False)
    ).rowcount
    s.commit()
    if registrado:
        log.warning("ingesta de pagos %s fallida: %s", etiqueta, motivo)
    else:
        log.warning("ingesta de pagos %s ya habia terminado; este fallo no la cambia", etiqueta)
