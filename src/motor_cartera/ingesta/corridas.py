"""La corrida: leer, juzgar, decidir y publicar, con trazabilidad de punta a punta.

El orden importa:
  1. guarda el archivo en el almacen de artefactos, abre una Corrida que apunta a el y registra el
     origen, la firma del archivo y la version del contrato
  2. lee el archivo, del almacen
  3. juzga cada registro contra el contrato
  4. decide: publica solo si los rechazos caben en la tolerancia y la cartera trae un solo
     corte
  5. cierra la corrida con sus conteos y la firma de su contenido, en la misma transaccion
     que publica

La API, el worker y el CLI usan este mismo modulo. La validacion vive en el contrato y la
decision aqui, no en un endpoint: asi ninguna puerta de entrada puede saltarse la barrera.

Procesar es idempotente: la corrida se toma con su fila bloqueada, y si ya termino no se vuelve a
procesar. La cola la entrega al menos una vez, y una segunda entrega no publica dos veces.

El archivo no se borra cuando la corrida termina: es la evidencia de lo que se recibio, y con el se
puede reproducir exactamente la entrada de cualquier corrida.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

import pandas as pd
from sqlalchemy import case, insert, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select
from sqlmodel.sql.expression import SelectOfScalar

from motor_cartera.config import Config, config
from motor_cartera.contratos import (
    VERSION_CONTRATO,
    ErrorDeContrato,
    Motivo,
    Separacion,
    fechas_de_corte,
    firmar_contenido,
    separar_rechazos,
)
from motor_cartera.contratos.cartera_v2 import VERSION_CONTRATO_V2
from motor_cartera.contratos.fuente import ErrorDeEstructura
from motor_cartera.db.modelos import ArtefactoFuente, Corrida, Cuenta, EstadoCorrida, Rechazo, ahora
from motor_cartera.db.sesion import insertar_en_savepoint, restriccion, sesion
from motor_cartera.fuentes.almacen import ArtefactoCorrupto
from motor_cartera.fuentes.artefactos import (
    ArtefactoGuardado,
    almacen_de,
    guardar_artefacto,
    guardar_contenido,
    leer_verificado,
    registrar_artefacto,
)
from motor_cartera.fuentes.proyeccion import VERSION_PROYECCION
from motor_cartera.ingesta.lectores import ErrorDeLectura, Lectura, leer_contenido

log = logging.getLogger(__name__)

INDICE_FIRMA_PUBLICADA = "ux_corrida_firma_publicada"
INDICE_FIRMA_EN_PROCESO = "ux_corrida_firma_en_proceso"

CONTRATOS_DE_CARTERA = (VERSION_CONTRATO, VERSION_CONTRATO_V2)
"""Los contratos con que se puede juzgar una cartera. cartera/v1 sigue congelado y disponible."""

INTENTOS_DE_APERTURA = 3
"""Cuantas veces se revisa y se inserta una corrida que pierde la carrera contra otra apertura del
mismo archivo. La segunda revision ya ve a la que gano; solo se vuelve a insertar si esa termino,
sin publicar, entre el rechazo y la revision."""


class CorridaMalDeclarada(ValueError):
    """El contrato o la fecha de corte que se declararon no son una combinacion valida."""


class ArchivoDuplicado(Exception):
    """Ese mismo archivo ya lo publico otra corrida, o se esta procesando en otra."""

    def __init__(self, previa: Corrida) -> None:
        if previa.estado == EstadoCorrida.EXITOSA:
            mensaje = f"Este archivo ya lo publico la corrida {previa.run_id}."
        else:
            mensaje = f"Este archivo ya se esta procesando en la corrida {previa.run_id}."
        super().__init__(mensaje)
        self.previa = previa


def firmar(contenido: bytes) -> str:
    """SHA-256 del archivo tal como llego. Identifica el archivo, no su contenido: eso lo
    hace `firmar_contenido`, del contrato."""
    return hashlib.sha256(contenido).hexdigest()


def abrir_corrida(
    s: Session,
    *,
    origen: str,
    contenido: bytes | None = None,
    guardado: ArtefactoGuardado | None = None,
    contrato: str = VERSION_CONTRATO,
    fecha_corte: date | None = None,
    tolerancia: float | None = None,
    confirmar: bool = True,
    config: Config = config,
) -> Corrida:
    """Registra la corrida EN_PROCESO, antes de leer nada: si algo falla, queda rastro.

    El contrato se declara, nunca se adivina por la forma del archivo. Con cartera/v1, la fecha de
    corte viene dentro del archivo y no se declara; con cartera/v2, que no la trae, es metadata del
    lote y es obligatoria. Una combinacion distinta levanta CorridaMalDeclarada.

    El archivo llega ya guardado en el almacen de artefactos (`guardado`, como lo deja la API), o
    en `contenido`, y entonces se guarda aqui antes de tocar la base: primero el objeto durable,
    despues la fila que apunta a el. La corrida nace apuntando a su ArtefactoFuente, con el
    despacho y la cartera de la configuracion.

    Desde aqui quedan fijas las reglas con que se va a juzgar: la tolerancia y la version
    del contrato.

    Si ese mismo archivo ya se publico, o se esta procesando en otra corrida, levanta
    ArchivoDuplicado: ingerirlo otra vez duplicaria la cartera o repetiria el trabajo. Una
    corrida EN_PROCESO cuenta hasta que termina, lleve el tiempo que lleve: el tiempo no
    demuestra que se abandono. La cierra el worker que tiene su trabajo, o el que lo toma
    cuando vence su lease.

    Esta revision es la via amable, porque sabe decir cual corrida fue; la garantia son los
    indices unicos sobre la firma: el de las EN_PROCESO atrapa las carreras al abrir, y el de
    las EXITOSA, al publicar. Una apertura que pierde la carrera choca con el indice dentro de
    un SAVEPOINT y vuelve a revisar, y entonces levanta ArchivoDuplicado con la que gano.

    Con confirmar=False no confirma: deja la corrida en la transaccion de quien llama, que la
    confirma junto con lo que tenga que escribir con ella (su artefacto, su flujo y su trabajo).
    """
    if (contenido is None) == (guardado is None):
        raise ValueError("Una corrida se abre con su contenido o con su artefacto ya guardado.")
    verificar_declaracion(contrato, fecha_corte)
    if guardado is None:
        guardado = guardar_contenido(almacen_de(config), contenido, origen)
    firma = guardado.objeto.sha256
    artefacto = registrar_artefacto(s, guardado)
    for _ in range(INTENTOS_DE_APERTURA):
        previa = s.exec(_previa(firma)).first()
        if previa is not None:
            raise ArchivoDuplicado(previa)

        corrida = Corrida(
            origen=origen,
            firma=firma,
            tolerancia_rechazo=config.tolerancia_rechazo if tolerancia is None else tolerancia,
            version_contrato=contrato,
            fecha_corte=fecha_corte,
            version_proyeccion=VERSION_PROYECCION if contrato == VERSION_CONTRATO_V2 else None,
            artefacto_fuente_id=artefacto.id,
            despacho_id=config.despacho_id,
            cartera_id=config.cartera_id,
        )
        error = insertar_en_savepoint(s, corrida)
        if error is None:
            break
        if restriccion(error) != INDICE_FIRMA_EN_PROCESO:
            raise error
    else:
        raise error

    if confirmar:
        s.commit()
        s.refresh(corrida)
    log.info("corrida %s abierta para %s", corrida.run_id, origen)
    return corrida


def procesar_corrida(corrida_id: int, contenido: bytes | None = None) -> None:
    """Lee, juzga, decide y publica. Deja la corrida en un estado terminal.

    Lee el archivo de su artefacto, comprobado contra su SHA-256: si el almacen ya no tiene los
    mismos bytes, la corrida queda FALLIDA y lo dice. `contenido` es solo para las corridas de v0.5
    que seguian en la cola al migrar, con su archivo en BYTEA y sin artefacto.

    La corrida se toma con su fila bloqueada, hasta el commit o el rollback: la procesa un solo
    worker a la vez. Si ya termino (EXITOSA, RECHAZADA o FALLIDA) no se vuelve a procesar: una
    segunda entrega del mismo trabajo no hace nada.

    Nada se publica a medias: cuentas, rechazos y cierre van en una sola transaccion. Si
    algo falla a la mitad se revierte todo, y la corrida queda FALLIDA con el motivo, si para
    entonces sigue EN_PROCESO: ningun camino de error degrada un estado terminal.

    No levanta excepciones: corre en un worker, y su resultado es el estado de la corrida,
    no un error que nadie va a ver.
    """
    with sesion() as s:
        # El estado se revisa ya con la fila bloqueada: es el que dejo el ultimo que la tuvo.
        corrida = s.exec(select(Corrida).where(Corrida.id == corrida_id).with_for_update()).one()
        if corrida.estado != EstadoCorrida.EN_PROCESO:
            log.warning(
                "corrida %s ya termino %s; no se vuelve a procesar", corrida.run_id, corrida.estado
            )
            return  # al cerrarse, la sesion revierte y suelta la fila
        # Se lee ahora: despues de un rollback la corrida en memoria caduca.
        etiqueta = corrida.run_id
        try:
            if corrida.version_contrato == VERSION_CONTRATO_V2:
                from motor_cartera.ingesta.cartera_v2 import juzgar_y_publicar

                juzgar_y_publicar(s, corrida, config=config)
                s.commit()
            else:
                datos = contenido if contenido is not None else _contenido_de(s, corrida)
                lectura = leer_contenido(datos, corrida.origen)
                _cerrar(s, corrida, lectura, separar_rechazos(lectura.datos))
        except (ErrorDeLectura, ErrorDeContrato, ErrorDeEstructura, ArtefactoCorrupto) as exc:
            s.rollback()
            _fallar(s, corrida_id, etiqueta, str(exc))
        except IntegrityError as exc:
            s.rollback()
            if restriccion(exc) != INDICE_FIRMA_PUBLICADA:
                log.exception("corrida %s: violacion de integridad inesperada", etiqueta)
                _fallar(
                    s,
                    corrida_id,
                    etiqueta,
                    "Error interno al publicar; ver la bitacora del servicio.",
                )
            else:
                _fallar(
                    s,
                    corrida_id,
                    etiqueta,
                    "Otra corrida publico este mismo archivo mientras esta se procesaba; "
                    "no se publica dos veces.",
                )
        except Exception as exc:
            log.exception("corrida %s: error inesperado", etiqueta)
            s.rollback()
            motivo = f"Error interno ({type(exc).__name__}); ver la bitacora."
            _fallar(s, corrida_id, etiqueta, motivo)


def ingerir_archivo(
    ruta: str | Path,
    *,
    tolerancia: float | None = None,
    contrato: str = VERSION_CONTRATO,
    fecha_corte: date | None = None,
) -> Corrida:
    """Una corrida completa en primer plano, en modo directo: sin cola, sin trabajo y sin lease.
    Si el proceso muere a la mitad, la corrida queda EN_PROCESO y nadie la cierra; un trabajo de
    la cola durable, en cambio, se recupera cuando vence su lease. El archivo se guarda en el
    almacen igual que en cualquier otra corrida."""
    ruta = Path(ruta)
    verificar_declaracion(contrato, fecha_corte)
    with ruta.open("rb") as archivo:
        guardado = guardar_artefacto(almacen_de(config), archivo, ruta.name)
    with sesion() as s:
        corrida = abrir_corrida(
            s,
            origen=ruta.name,
            guardado=guardado,
            tolerancia=tolerancia,
            contrato=contrato,
            fecha_corte=fecha_corte,
        )
    procesar_corrida(corrida.id)
    with sesion() as s:
        return s.get_one(Corrida, corrida.id)


def verificar_declaracion(contrato: str, fecha_corte: date | None) -> None:
    """Que el contrato exista y que la fecha de corte se declare solo donde el archivo no la trae.
    Se revisa antes de guardar el archivo: una declaracion invalida no deja nada."""
    if contrato not in CONTRATOS_DE_CARTERA:
        raise CorridaMalDeclarada(
            f"Contrato desconocido: {contrato!r}. Se aceptan {', '.join(CONTRATOS_DE_CARTERA)}."
        )
    if contrato == VERSION_CONTRATO_V2 and fecha_corte is None:
        raise CorridaMalDeclarada(
            "cartera/v2 no trae la fecha de corte en el archivo: se declara con la corrida."
        )
    if contrato == VERSION_CONTRATO and fecha_corte is not None:
        raise CorridaMalDeclarada(
            "En cartera/v1 la fecha de corte viene en el archivo: no se declara aparte."
        )


def decidir(
    leidas: int, rechazadas: int, tolerancia: float, cortes: dict[date, int]
) -> tuple[EstadoCorrida, str]:
    """La barrera: publica solo si los rechazos caben en la tolerancia y la cartera trae un
    solo corte.

    Rechazar por registro sin un tope seria fail-open: un archivo con media cartera rota
    publicaria la otra media, y la operacion trabajaria sobre ella sin saberlo. Pasado el
    tope, el problema ya no son filas sueltas sino el archivo, y no se publica nada.

    Con mas de un corte tampoco hay filas culpables: no se sabe cual de los cortes es el de
    la cartera, asi que se rechaza entera. Es RECHAZADA y no FALLIDA, porque el contenido se
    pudo juzgar y no cumple; FALLIDA es para lo que no se pudo juzgar.
    """
    if leidas <= 0:
        raise ValueError("Una corrida sin registros no se juzga: es un error de lectura.")
    tasa = rechazadas / leidas
    publicadas = leidas - rechazadas
    if publicadas == 0:
        return EstadoCorrida.RECHAZADA, "Ningun registro cumple el contrato; no se publico nada."

    problemas = []
    if tasa > tolerancia:
        problemas.append(
            f"el {tasa:.1%} de los registros no cumple el contrato y la tolerancia es "
            f"{tolerancia:.1%}"
        )
    if len(cortes) > 1:
        problemas.append(
            f"la cartera trae {len(cortes)} fechas de corte ({_listar_cortes(cortes)}) y debe "
            "traer una sola"
        )
    if problemas:
        veredicto = "; ".join(problemas) + "; no se publico nada."
        return EstadoCorrida.RECHAZADA, veredicto[0].upper() + veredicto[1:]
    if rechazadas == 0:
        return EstadoCorrida.EXITOSA, f"Se publicaron {publicadas:,} cuentas."
    return (
        EstadoCorrida.EXITOSA,
        f"Se publicaron {publicadas:,} cuentas; {rechazadas:,} registros ({tasa:.1%}) se "
        f"rechazaron, dentro de la tolerancia de {tolerancia:.1%}.",
    )


def corrida_vigente(s: Session) -> Corrida | None:
    """La cartera vigente: la corrida EXITOSA con el corte mas reciente.

    Por corte y no por hora de publicacion: si alguien sube hoy la cartera de la semana
    pasada, no debe reemplazar a la de hoy, porque operar con cartera vieja es el error
    mas caro de cobranza. Si dos corridas publicaron el mismo corte, vale la ultima: es
    una correccion. Solo cuenta lo publicado; lo rechazado o fallido nunca es vigente.
    """
    return s.exec(
        select(Corrida)
        .where(Corrida.estado == EstadoCorrida.EXITOSA)
        .order_by(Corrida.fecha_corte.desc(), Corrida.terminada_en.desc(), Corrida.id.desc())
    ).first()


def _contenido_de(s: Session, corrida: Corrida) -> bytes:
    """El archivo de la corrida, leido de su artefacto y comprobado contra su SHA-256."""
    if corrida.artefacto_fuente_id is None:
        raise ErrorDeLectura("La corrida no tiene un artefacto del que leer su archivo.")
    artefacto = s.get_one(ArtefactoFuente, corrida.artefacto_fuente_id)
    return leer_verificado(almacen_de(config), artefacto)


def _previa(firma: str) -> SelectOfScalar[Corrida]:
    """La corrida que ya publico ese archivo, o la que lo esta procesando; la EXITOSA primero. De
    cada una hay a lo mas una: lo garantizan los indices unicos parciales sobre la firma."""
    return (
        select(Corrida)
        .where(
            Corrida.firma == firma,
            Corrida.estado.in_([EstadoCorrida.EXITOSA, EstadoCorrida.EN_PROCESO]),
        )
        .order_by(case((Corrida.estado == EstadoCorrida.EXITOSA, 0), else_=1))
    )


def _cerrar(s: Session, corrida: Corrida, lectura: Lectura, separacion: Separacion) -> None:
    leidas = len(lectura.datos)
    rechazadas = len(separacion.rechazos)
    cortes = fechas_de_corte(separacion.validas)
    estado, veredicto = decidir(leidas, rechazadas, corrida.tolerancia_rechazo, cortes)

    if separacion.rechazos:
        s.execute(insert(Rechazo), _filas_rechazo(corrida.id, lectura.datos, separacion.rechazos))
    if estado is EstadoCorrida.EXITOSA:
        s.execute(insert(Cuenta), _filas_cuenta(corrida.id, separacion.validas))

    corrida.estado = estado
    corrida.filas_leidas = leidas
    corrida.filas_validas = len(separacion.validas)
    corrida.filas_rechazadas = rechazadas
    # El corte de la cartera, si trae uno solo. Con varios no hay uno que elegir.
    corrida.fecha_corte = next(iter(cortes)) if len(cortes) == 1 else None
    corrida.firma_contenido = firmar_contenido(separacion.validas)
    corrida.detalle = f"{veredicto} Origen: {lectura.origen.rstrip('.')}."
    corrida.terminada_en = ahora()
    s.add(corrida)
    s.commit()
    log.info("corrida %s: %s", corrida.run_id, veredicto)


def _listar_cortes(cortes: dict[date, int], hasta: int = 5) -> str:
    partes = [
        f"{dia.isoformat()} en {n:,} registro{'' if n == 1 else 's'}" for dia, n in cortes.items()
    ]
    if len(partes) > hasta:
        partes = [*partes[:hasta], f"{len(partes) - hasta} fechas mas"]
    return ", ".join(partes[:-1]) + " y " + partes[-1]


def _fallar(s: Session, corrida_id: int, etiqueta: UUID, motivo: str) -> None:
    """Deja la corrida FALLIDA, solo si sigue EN_PROCESO.

    Es un UPDATE condicionado al estado que tiene la base, y no la corrida que se tenia en
    memoria: entre el rollback y este registro, otro worker pudo tomar la misma corrida y
    terminarla, y un fallo que llega tarde no cambia una EXITOSA, una RECHAZADA ni otra FALLIDA.
    Si ya termino, se deja como esta y el fallo queda solo en la bitacora.
    """
    registrado = s.execute(
        update(Corrida)
        .where(Corrida.id == corrida_id, Corrida.estado == EstadoCorrida.EN_PROCESO)
        .values(estado=EstadoCorrida.FALLIDA, detalle=motivo, terminada_en=ahora())
        .execution_options(synchronize_session=False)
    ).rowcount
    s.commit()
    if registrado:
        log.warning("corrida %s fallida: %s", etiqueta, motivo)
        return
    estado = s.exec(select(Corrida.estado).where(Corrida.id == corrida_id)).one()
    log.warning("corrida %s ya termino %s; este fallo no la cambia: %s", etiqueta, estado, motivo)


def _filas_cuenta(corrida_id: int, validas: pd.DataFrame) -> list[dict[str, Any]]:
    registros = validas.assign(corrida_id=corrida_id, fecha_corte=validas["fecha_corte"].dt.date)
    return registros.to_dict("records")


def _filas_rechazo(
    corrida_id: int, datos: pd.DataFrame, rechazos: dict[int, list[Motivo]]
) -> list[dict[str, Any]]:
    crudos = datos.loc[list(rechazos)]
    crudos = crudos.astype(object).where(crudos.notna(), None)  # NaN no existe en JSON
    return [
        {
            "corrida_id": corrida_id,
            "fila": fila,
            "valores": crudos.loc[fila].to_dict(),
            "motivos": [asdict(m) for m in motivos],
        }
        for fila, motivos in rechazos.items()
    ]
