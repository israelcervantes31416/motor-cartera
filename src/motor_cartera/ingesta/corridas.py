"""La corrida: leer, juzgar, decidir y publicar, con trazabilidad de punta a punta.

El orden importa:
  1. abre una Corrida y registra el origen y la firma del archivo
  2. lee el archivo
  3. juzga cada registro contra el contrato
  4. decide: publica solo si los rechazos caben en la tolerancia y la cartera trae un solo
     corte
  5. cierra la corrida con sus conteos, en la misma transaccion que publica

La API y el CLI usan este mismo modulo. La validacion vive en el contrato y la decision
aqui, no en un endpoint: asi ninguna puerta de entrada puede saltarse la barrera.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import and_, case, insert, or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from motor_cartera.config import config
from motor_cartera.contratos import (
    ErrorDeContrato,
    Motivo,
    Separacion,
    fechas_de_corte,
    separar_rechazos,
)
from motor_cartera.db.modelos import Corrida, Cuenta, EstadoCorrida, Rechazo, ahora
from motor_cartera.db.sesion import sesion
from motor_cartera.ingesta.lectores import ErrorDeLectura, Lectura, leer_contenido

log = logging.getLogger(__name__)

INDICE_FIRMA_PUBLICADA = "ux_corrida_firma_publicada"

ABANDONO = timedelta(minutes=15)
"""Una corrida EN_PROCESO por mas de esto se da por abandonada: la API se reinicio a media
corrida y nadie la va a terminar. Deja de impedir que se reintente el mismo archivo."""


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
    """SHA-256 del archivo tal como llego. Identifica el archivo, no su contenido."""
    return hashlib.sha256(contenido).hexdigest()


def abrir_corrida(
    s: Session, *, origen: str, contenido: bytes, tolerancia: float | None = None
) -> Corrida:
    """Registra la corrida EN_PROCESO, antes de leer nada: si algo falla, queda rastro.

    Si ese mismo archivo ya se publico, o se esta procesando en otra corrida, levanta
    ArchivoDuplicado: ingerirlo otra vez duplicaria la cartera o repetiria el trabajo.
    Esta revision es la via amable, porque sabe decir cual corrida fue; la garantia es el
    indice unico sobre la firma, que atrapa las carreras al publicar.
    """
    firma = firmar(contenido)
    en_curso = and_(
        Corrida.estado == EstadoCorrida.EN_PROCESO, Corrida.iniciada_en > ahora() - ABANDONO
    )
    previa = s.exec(
        select(Corrida)
        .where(Corrida.firma == firma, or_(Corrida.estado == EstadoCorrida.EXITOSA, en_curso))
        .order_by(case((Corrida.estado == EstadoCorrida.EXITOSA, 0), else_=1))
    ).first()
    if previa is not None:
        raise ArchivoDuplicado(previa)

    corrida = Corrida(
        origen=origen,
        firma=firma,
        tolerancia_rechazo=config.tolerancia_rechazo if tolerancia is None else tolerancia,
    )
    s.add(corrida)
    s.commit()
    s.refresh(corrida)
    log.info("corrida %s abierta para %s", corrida.run_id, origen)
    return corrida


def procesar_corrida(corrida_id: int, contenido: bytes) -> None:
    """Lee, juzga, decide y publica. Deja la corrida en un estado terminal.

    Nada se publica a medias: cuentas, rechazos y cierre van en una sola transaccion. Si
    algo falla a la mitad se revierte todo, y la corrida queda FALLIDA con el motivo.

    No levanta excepciones: corre en segundo plano, y su resultado es el estado de la
    corrida, no un error que nadie va a ver.
    """
    with sesion() as s:
        corrida = s.get_one(Corrida, corrida_id)
        try:
            lectura = leer_contenido(contenido, corrida.origen)
            _cerrar(s, corrida, lectura, separar_rechazos(lectura.datos))
        except (ErrorDeLectura, ErrorDeContrato) as exc:
            s.rollback()
            _fallar(s, corrida, str(exc))
        except IntegrityError as exc:
            s.rollback()
            if _restriccion(exc) != INDICE_FIRMA_PUBLICADA:
                log.exception("corrida %s: violacion de integridad inesperada", corrida.run_id)
                _fallar(s, corrida, "Error interno al publicar; ver la bitacora del servicio.")
            else:
                _fallar(
                    s,
                    corrida,
                    "Otra corrida publico este mismo archivo mientras esta se procesaba; "
                    "no se publica dos veces.",
                )
        except Exception as exc:
            log.exception("corrida %s: error inesperado", corrida.run_id)
            s.rollback()
            _fallar(s, corrida, f"Error interno ({type(exc).__name__}); ver la bitacora.")


def ingerir_archivo(ruta: str | Path, *, tolerancia: float | None = None) -> Corrida:
    """Una corrida completa en primer plano, para el CLI. La API hace lo mismo en dos tiempos."""
    ruta = Path(ruta)
    contenido = ruta.read_bytes()
    with sesion() as s:
        corrida = abrir_corrida(s, origen=ruta.name, contenido=contenido, tolerancia=tolerancia)
    procesar_corrida(corrida.id, contenido)
    with sesion() as s:
        return s.get_one(Corrida, corrida.id)


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


def _fallar(s: Session, corrida: Corrida, motivo: str) -> None:
    corrida.estado = EstadoCorrida.FALLIDA
    corrida.detalle = motivo
    corrida.terminada_en = ahora()
    s.add(corrida)
    s.commit()
    log.warning("corrida %s fallida: %s", corrida.run_id, motivo)


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


def _restriccion(exc: IntegrityError) -> str | None:
    diagnostico = getattr(exc.orig, "diag", None)
    return getattr(diagnostico, "constraint_name", None)
