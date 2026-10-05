"""El flujo automatico de una corrida, y los trabajos que se piden a mano.

Una corrida que llega por la API nace con su flujo: el artefacto de su archivo, la corrida, el flujo
y el trabajo de la ingesta se registran en una sola transaccion, despues de que el archivo ya es
durable en el almacen de artefactos. Cuando el trabajo de una etapa termina, el worker encadena la
siguiente en la misma transaccion que lo cierra:

    INGESTA -> DECISION -> TERRITORIAL -> RUTEO -> COMPLETADA

Si la etapa termino EXITOSA, se abre la ejecucion de la siguiente y su trabajo, y el flujo apunta a
ella. Si termino de otra forma, el flujo se DETIENE en esa etapa. No hay un instante en que una
etapa haya terminado sin que el flujo lo sepa: o se confirman las dos cosas, o ninguna.

Un flujo detenido en DECISION, TERRITORIAL o RUTEO se reanuda: otra ejecucion de esa etapa, otro
trabajo, y la fallida queda en el historial. Uno detenido en INGESTA no: la corrida terminada es
evidencia y no se reabre; se vuelve a subir el archivo, y eso es otra corrida con otro flujo.

Las etapas tambien se pueden pedir a mano, sobre recursos que no son de un flujo que las vaya a
correr: el recurso EN_PROCESO y su trabajo, en una transaccion. No compiten con el flujo automatico.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement
from sqlmodel import Session, SQLModel, select

from motor_cartera.config import Config
from motor_cartera.contratos import VERSION_CONTRATO
from motor_cartera.db.modelos import (
    Corrida,
    EjecucionDecision,
    EjecucionRuteo,
    EjecucionTerritorial,
    EstadoFlujo,
    EtapaFlujo,
    FlujoOrquestacion,
    IngestaPagos,
    TipoTrabajo,
    TrabajoOrquestacion,
    ahora,
)
from motor_cartera.decision import ejecuciones as decision
from motor_cartera.fuentes.artefactos import ArtefactoGuardado
from motor_cartera.ingesta.corridas import abrir_corrida
from motor_cartera.ingesta.pagos import abrir_ingesta_pagos
from motor_cartera.orquestacion import cola, objetivos
from motor_cartera.ruteo import ejecuciones as ruteo
from motor_cartera.territorial import ejecuciones as territorial

log = logging.getLogger(__name__)

ETAPAS = (EtapaFlujo.INGESTA, EtapaFlujo.DECISION, EtapaFlujo.TERRITORIAL, EtapaFlujo.RUTEO)
"""Las etapas que ejecutan un motor, en orden. COMPLETADA no ejecuta nada: es haber llegado."""

TIPO_DE_LA_ETAPA = {etapa: TipoTrabajo(etapa.value) for etapa in ETAPAS}

PUNTERO = {
    EtapaFlujo.DECISION: "ejecucion_decision_id",
    EtapaFlujo.TERRITORIAL: "ejecucion_territorial_id",
    EtapaFlujo.RUTEO: "ejecucion_ruteo_id",
}
"""La columna del flujo que apunta a la ejecucion vigente de cada etapa. La de INGESTA es la
corrida, que no cambia."""

EN_COLA = {
    EtapaFlujo.INGESTA: "La ingesta esta en la cola.",
    EtapaFlujo.DECISION: "La corrida termino EXITOSA; la decision esta en la cola.",
    EtapaFlujo.TERRITORIAL: "La decision termino EXITOSA; la organizacion territorial esta en la "
    "cola.",
    EtapaFlujo.RUTEO: "La organizacion territorial termino EXITOSA; el ruteo esta en la cola.",
}
"""El detalle de un flujo que espera el trabajo de su etapa."""

COMPLETADO = "La ingesta, la decision, la organizacion territorial y el ruteo terminaron EXITOSA."

QUE_SE_DETUVO = {
    EtapaFlujo.INGESTA: "La corrida",
    EtapaFlujo.DECISION: "La decision",
    EtapaFlujo.TERRITORIAL: "La organizacion territorial",
    EtapaFlujo.RUTEO: "El ruteo",
}

NO_SE_ABRE = (
    decision.CorridaNoDecidible,
    decision.DecisionYaGenerada,
    decision.DecisionEnProceso,
    territorial.DecisionNoTerritorializable,
    territorial.TerritorialYaGenerado,
    territorial.TerritorialEnProceso,
    ruteo.TerritorialNoRuteable,
    ruteo.RuteoYaGenerado,
    ruteo.RuteoEnProceso,
)
"""Las razones conocidas por las que no se abre la ejecucion de una etapa."""


class FlujoNoEncontrado(Exception):
    """No existe un flujo con ese flujo_id."""

    def __init__(self, flujo_id: UUID) -> None:
        super().__init__(f"No existe un flujo con flujo_id {flujo_id}.")
        self.flujo_id = flujo_id


class _ErrorDeFlujo(Exception):
    """Un flujo que no admite lo que se le pidio. Guarda lo que hace falta para responder, sin
    depender de que la sesion que lo leyo siga abierta."""

    def __init__(self, flujo: FlujoOrquestacion, mensaje: str) -> None:
        super().__init__(mensaje)
        self.flujo_id = flujo.flujo_id
        self.estado = flujo.estado
        self.etapa = flujo.etapa


class FlujoEnProceso(_ErrorDeFlujo):
    """El flujo todavia esta EN_PROCESO: va a correr por su cuenta la etapa que se pidio."""


class FlujoDetenido(_ErrorDeFlujo):
    """El flujo se detuvo justo en la etapa que se pidio a mano: se reintenta reanudandolo."""


class FlujoYaCompletado(_ErrorDeFlujo):
    """El flujo ya llego a COMPLETADA: no hay nada que reanudar."""


class FlujoNoReanudable(_ErrorDeFlujo):
    """El flujo se detuvo en una etapa que no se reanuda, o no hay una ejecucion fallida que
    reintentar."""


def crear_flujo_ingesta(
    s: Session,
    *,
    origen: str,
    tolerancia: float,
    config: Config,
    contenido: bytes | None = None,
    guardado: ArtefactoGuardado | None = None,
    contrato: str = VERSION_CONTRATO,
    fecha_corte: date | None = None,
) -> tuple[Corrida, FlujoOrquestacion]:
    """El artefacto, la corrida EN_PROCESO, su flujo en INGESTA y el trabajo de la ingesta, en una
    sola transaccion: no hay un instante en que exista la corrida sin todo lo demas.

    El archivo ya esta en el almacen de artefactos (`guardado`), o se guarda ahi antes de la
    transaccion (`contenido`): no vive en la memoria de quien lo recibio, asi que sobrevive a la
    muerte de ese proceso, y cualquier worker puede hacer la ingesta. Propaga ArchivoDuplicado, y
    entonces no se registra nada en la base.
    """
    corrida = abrir_corrida(
        s,
        origen=origen,
        contenido=contenido,
        guardado=guardado,
        contrato=contrato,
        fecha_corte=fecha_corte,
        tolerancia=tolerancia,
        confirmar=False,
        config=config,
    )
    flujo = FlujoOrquestacion(corrida_id=corrida.id, detalle=EN_COLA[EtapaFlujo.INGESTA])
    s.add(flujo)
    s.flush()
    cola.crear(
        s,
        TipoTrabajo.INGESTA,
        corrida.id,
        max_intentos=config.worker_max_intentos,
        flujo_id=flujo.id,
    )
    s.commit()
    s.refresh(corrida)
    s.refresh(flujo)
    log.info("flujo %s creado para la corrida %s", flujo.flujo_id, corrida.run_id)
    return corrida, flujo


def encolar_ingesta(
    s: Session,
    *,
    origen: str,
    tolerancia: float | None,
    config: Config,
    contenido: bytes | None = None,
    guardado: ArtefactoGuardado | None = None,
    contrato: str = VERSION_CONTRATO,
    fecha_corte: date | None = None,
    tomado_por: str | None = None,
) -> tuple[Corrida, TrabajoOrquestacion]:
    """Una ingesta pedida a mano, sin flujo: el artefacto, la corrida EN_PROCESO y su trabajo, en
    una sola transaccion. Al terminar no encadena nada. Con `tomado_por`, el trabajo nace ya tomado
    por ese worker, que la va a ejecutar en primer plano. Propaga ArchivoDuplicado."""
    corrida = abrir_corrida(
        s,
        origen=origen,
        contenido=contenido,
        guardado=guardado,
        contrato=contrato,
        fecha_corte=fecha_corte,
        tolerancia=tolerancia,
        confirmar=False,
        config=config,
    )
    trabajo = cola.crear(
        s,
        TipoTrabajo.INGESTA,
        corrida.id,
        max_intentos=config.worker_max_intentos,
        tomado_por=tomado_por,
        lease_segundos=config.worker_lease_segundos,
    )
    s.commit()
    s.refresh(corrida)
    s.refresh(trabajo)
    return corrida, trabajo


def encolar_ingesta_pagos(
    s: Session,
    *,
    origen: str,
    tolerancia: float | None,
    config: Config,
    contenido: bytes | None = None,
    guardado: ArtefactoGuardado | None = None,
    tomado_por: str | None = None,
) -> tuple[IngestaPagos, TrabajoOrquestacion]:
    """Una ingesta de pagos: su artefacto, la ingesta EN_PROCESO y su trabajo INGESTA_PAGOS, en una
    sola transaccion. No es de ningun flujo y no encadena nada: la conciliacion es posterior. Con
    `tomado_por`, el trabajo nace ya tomado por ese worker. Propaga PagosDuplicados."""
    ingesta = abrir_ingesta_pagos(
        s,
        origen=origen,
        contenido=contenido,
        guardado=guardado,
        tolerancia=tolerancia,
        confirmar=False,
        config=config,
    )
    trabajo = cola.crear(
        s,
        TipoTrabajo.INGESTA_PAGOS,
        ingesta.id,
        max_intentos=config.worker_max_intentos,
        tomado_por=tomado_por,
        lease_segundos=config.worker_lease_segundos,
    )
    s.commit()
    s.refresh(ingesta)
    s.refresh(trabajo)
    log.info("ingesta de pagos %s en la cola", ingesta.pagos_run_id)
    return ingesta, trabajo


def encolar_decision(s: Session, corrida_id: int, *, config: Config) -> EjecucionDecision:
    """Una decision pedida a mano: la ejecucion EN_PROCESO y su trabajo, en una sola transaccion.

    Propaga lo que levanta la apertura (CorridaNoDecidible, DecisionYaGenerada, DecisionEnProceso),
    y FlujoEnProceso o FlujoDetenido si la corrida es de un flujo que corre o corrio esa etapa.
    """
    corrida = s.get_one(Corrida, corrida_id)
    _no_compite_con_su_flujo(s, FlujoOrquestacion.corrida_id == corrida_id, EtapaFlujo.DECISION)
    ejecucion = decision.abrir_ejecucion(s, corrida, confirmar=False)
    return _encolar(s, TipoTrabajo.DECISION, ejecucion, config)


def encolar_territorial(
    s: Session, ejecucion_decision_id: int, *, config: Config
) -> EjecucionTerritorial:
    """Una organizacion territorial pedida a mano, como encolar_decision. Propaga lo que levanta la
    apertura, y FlujoEnProceso o FlujoDetenido si las decisiones son de un flujo que corre o corrio
    esa etapa."""
    fuente = s.get_one(EjecucionDecision, ejecucion_decision_id)
    _no_compite_con_su_flujo(
        s,
        FlujoOrquestacion.ejecucion_decision_id == ejecucion_decision_id,
        EtapaFlujo.TERRITORIAL,
    )
    ejecucion = territorial.abrir_ejecucion(s, fuente, confirmar=False)
    return _encolar(s, TipoTrabajo.TERRITORIAL, ejecucion, config)


def encolar_ruteo(s: Session, ejecucion_territorial_id: int, *, config: Config) -> EjecucionRuteo:
    """Un ruteo pedido a mano, como encolar_decision. Propaga lo que levanta la apertura, y
    FlujoEnProceso o FlujoDetenido si la ejecucion territorial es de un flujo que corre o corrio esa
    etapa."""
    fuente = s.get_one(EjecucionTerritorial, ejecucion_territorial_id)
    _no_compite_con_su_flujo(
        s,
        FlujoOrquestacion.ejecucion_territorial_id == ejecucion_territorial_id,
        EtapaFlujo.RUTEO,
    )
    ejecucion = ruteo.abrir_ejecucion(s, fuente, confirmar=False)
    return _encolar(s, TipoTrabajo.RUTEO, ejecucion, config)


def avanzar(
    s: Session, flujo_id: int, tipo: TipoTrabajo, objetivo_id: int, *, max_intentos: int
) -> None:
    """Encadena el flujo despues de que el trabajo de su etapa termino, en la transaccion de quien
    lo cierra: no confirma.

    El flujo se toma con su fila bloqueada. Si la ejecucion de su etapa termino EXITOSA, abre la de
    la siguiente con su trabajo, apunta a ella y pasa a esa etapa; despues del ruteo, el flujo queda
    COMPLETADO. Si termino de otra forma, el flujo se DETIENE en la etapa. Un trabajo que no es el
    de la etapa vigente del flujo no lo mueve.
    """
    flujo = _bloqueado(s, FlujoOrquestacion.id == flujo_id)
    if flujo.estado != EstadoFlujo.EN_PROCESO or not _es_su_etapa(flujo, tipo, objetivo_id):
        log.warning(
            "flujo %s: el trabajo de %s %s no es el de su etapa vigente (%s %s); no se avanza",
            flujo.flujo_id,
            tipo,
            objetivo_id,
            flujo.estado,
            flujo.etapa,
        )
        return
    estado = objetivos.estado(s, tipo, objetivo_id)
    if estado != "EXITOSA":
        _detener(flujo, f"{QUE_SE_DETUVO[flujo.etapa]} termino {estado}. {_como_reintentar(flujo)}")
    elif flujo.etapa == EtapaFlujo.RUTEO:
        flujo.estado, flujo.etapa = EstadoFlujo.COMPLETADO, EtapaFlujo.COMPLETADA
        flujo.terminado_en = flujo.actualizado_en = ahora()
        flujo.detalle = COMPLETADO
    else:
        siguiente = ETAPAS[ETAPAS.index(flujo.etapa) + 1]
        try:
            ejecucion = _abrir(s, flujo, siguiente)
        except NO_SE_ABRE as exc:
            _detener(
                flujo,
                f"{QUE_SE_DETUVO[flujo.etapa]} termino EXITOSA, pero la etapa {siguiente} no se "
                f"pudo abrir: {exc}",
            )
        else:
            _apuntar(s, flujo, siguiente, ejecucion.id, max_intentos)
    s.add(flujo)
    s.flush()
    log.info("flujo %s: %s en %s", flujo.flujo_id, flujo.estado, flujo.etapa)


def detener(s: Session, flujo_id: int, detalle: str) -> None:
    """DETIENE el flujo en su etapa, en la transaccion de quien llama, si sigue EN_PROCESO."""
    flujo = _bloqueado(s, FlujoOrquestacion.id == flujo_id)
    if flujo.estado != EstadoFlujo.EN_PROCESO:
        return
    _detener(flujo, detalle)
    s.add(flujo)
    s.flush()
    log.info("flujo %s: DETENIDO en %s", flujo.flujo_id, flujo.etapa)


def reanudar_flujo(s: Session, flujo_id: UUID, *, config: Config) -> FlujoOrquestacion:
    """Reintenta la etapa en que se detuvo el flujo: otra ejecucion de esa etapa y su trabajo, y el
    flujo EN_PROCESO otra vez, apuntando a la nueva. La fallida no se reabre ni se borra: queda en
    el historial, con su trabajo. Confirma.

    Levanta FlujoNoEncontrado, FlujoEnProceso, FlujoYaCompletado, o FlujoNoReanudable si se
    detuvo en INGESTA (una corrida terminada no se reabre: se vuelve a subir el archivo) o si la
    ejecucion de su etapa no termino FALLIDA.
    """
    flujo = s.exec(
        select(FlujoOrquestacion).where(FlujoOrquestacion.flujo_id == flujo_id).with_for_update()
    ).one_or_none()
    if flujo is None:
        raise FlujoNoEncontrado(flujo_id)
    if flujo.estado == EstadoFlujo.COMPLETADO:
        raise FlujoYaCompletado(flujo, f"El flujo {flujo_id} ya esta COMPLETADO.")
    if flujo.estado == EstadoFlujo.EN_PROCESO:
        raise FlujoEnProceso(
            flujo, f"El flujo {flujo_id} sigue EN_PROCESO, en la etapa {flujo.etapa}."
        )
    tipo = TIPO_DE_LA_ETAPA[flujo.etapa]
    estado = objetivos.estado(s, tipo, _objetivo_de_la_etapa(flujo))
    if flujo.etapa == EtapaFlujo.INGESTA:
        raise FlujoNoReanudable(
            flujo,
            f"El flujo {flujo_id} se detuvo en la ingesta: la corrida termino {estado}, y una "
            "corrida terminada no se reabre. Vuelve a subir el archivo: eso crea otra corrida y "
            "otro flujo.",
        )
    if estado != "FALLIDA":
        raise FlujoNoReanudable(
            flujo,
            f"La etapa {flujo.etapa} del flujo {flujo_id} termino {estado}: no hay una ejecucion "
            "fallida que reintentar.",
        )
    etapa = flujo.etapa
    try:
        ejecucion = _abrir(s, flujo, etapa)
    except NO_SE_ABRE as exc:
        raise FlujoNoReanudable(flujo, str(exc)) from exc
    _apuntar(s, flujo, etapa, ejecucion.id, config.worker_max_intentos)
    flujo.estado, flujo.terminado_en = EstadoFlujo.EN_PROCESO, None
    flujo.detalle = (
        f"Se reanudo en la etapa {etapa} con otra ejecucion; la que fallo queda en el historial."
    )
    s.add(flujo)
    s.commit()
    s.refresh(flujo)
    log.info("flujo %s reanudado en %s", flujo.flujo_id, etapa)
    return flujo


def objetivo_de(trabajo: TrabajoOrquestacion) -> int:
    """El id del recurso que ejecuta un trabajo: la columna de su tipo."""
    return getattr(trabajo, objetivos.OBJETIVOS[trabajo.tipo].columna)


def _encolar(s: Session, tipo: TipoTrabajo, ejecucion: SQLModel, config: Config) -> Any:
    """El trabajo de una ejecucion pedida a mano, sin flujo, y el commit de las dos."""
    cola.crear(s, tipo, ejecucion.id, max_intentos=config.worker_max_intentos)
    s.commit()
    s.refresh(ejecucion)
    return ejecucion


def _no_compite_con_su_flujo(s: Session, de_la_fuente: ColumnElement, etapa: EtapaFlujo) -> None:
    """Una etapa pedida a mano no compite con el flujo automatico de su fuente.

    Si la fuente es de un flujo EN_PROCESO que todavia va a correr esa etapa (esta en la anterior o
    en esa misma), levanta FlujoEnProceso. Si el flujo se detuvo justo en esa etapa, FlujoDetenido:
    se reintenta reanudando el flujo, no con otra ejecucion a mano. En cualquier otro caso la fuente
    se juzga como cualquier otra. El flujo se lee con su fila bloqueada, y asi no cambia de etapa
    mientras se decide.
    """
    flujo = _bloqueado(s, de_la_fuente, opcional=True)
    if flujo is None:
        return
    anterior = ETAPAS[ETAPAS.index(etapa) - 1]
    if flujo.estado == EstadoFlujo.EN_PROCESO and flujo.etapa in (anterior, etapa):
        raise FlujoEnProceso(
            flujo,
            f"La fuente es del flujo {flujo.flujo_id}, que sigue EN_PROCESO en {flujo.etapa} y va "
            f"a correr la etapa {etapa} por su cuenta.",
        )
    if flujo.estado == EstadoFlujo.DETENIDO and flujo.etapa == etapa:
        raise FlujoDetenido(
            flujo,
            f"La fuente es del flujo {flujo.flujo_id}, que se detuvo en la etapa {etapa}: se "
            "reintenta reanudando el flujo.",
        )


def _bloqueado(s: Session, condicion: ColumnElement, *, opcional: bool = False) -> Any:
    """El flujo que cumple `condicion`, con su fila bloqueada hasta el commit o el rollback."""
    resultado = s.exec(select(FlujoOrquestacion).where(condicion).with_for_update())
    return resultado.one_or_none() if opcional else resultado.one()


def _es_su_etapa(flujo: FlujoOrquestacion, tipo: TipoTrabajo, objetivo_id: int) -> bool:
    return TIPO_DE_LA_ETAPA.get(flujo.etapa) == tipo and _objetivo_de_la_etapa(flujo) == objetivo_id


def _objetivo_de_la_etapa(flujo: FlujoOrquestacion) -> int:
    """La ejecucion vigente de la etapa del flujo; en INGESTA, su corrida."""
    if flujo.etapa == EtapaFlujo.INGESTA:
        return flujo.corrida_id
    return getattr(flujo, PUNTERO[flujo.etapa])


def _abrir(s: Session, flujo: FlujoOrquestacion, etapa: EtapaFlujo) -> SQLModel:
    """Abre, sin confirmar, la ejecucion de `etapa` sobre lo que el flujo ya publico."""
    if etapa == EtapaFlujo.DECISION:
        corrida = s.get_one(Corrida, flujo.corrida_id)
        return decision.abrir_ejecucion(s, corrida, confirmar=False)
    if etapa == EtapaFlujo.TERRITORIAL:
        fuente = s.get_one(EjecucionDecision, flujo.ejecucion_decision_id)
        return territorial.abrir_ejecucion(s, fuente, confirmar=False)
    fuente = s.get_one(EjecucionTerritorial, flujo.ejecucion_territorial_id)
    return ruteo.abrir_ejecucion(s, fuente, confirmar=False)


def _apuntar(
    s: Session, flujo: FlujoOrquestacion, etapa: EtapaFlujo, ejecucion_id: int, max_intentos: int
) -> None:
    """El flujo pasa a `etapa`, apuntando a su ejecucion, y el trabajo de esa ejecucion entra a la
    cola. Todo en la transaccion de quien llama."""
    setattr(flujo, PUNTERO[etapa], ejecucion_id)
    flujo.etapa = etapa
    flujo.actualizado_en = ahora()
    flujo.detalle = EN_COLA[etapa]
    s.add(flujo)
    s.flush()
    cola.crear(
        s,
        TIPO_DE_LA_ETAPA[etapa],
        ejecucion_id,
        max_intentos=max_intentos,
        flujo_id=flujo.id,
    )


def _detener(flujo: FlujoOrquestacion, detalle: str) -> None:
    flujo.estado = EstadoFlujo.DETENIDO
    flujo.terminado_en = flujo.actualizado_en = ahora()
    flujo.detalle = detalle


def _como_reintentar(flujo: FlujoOrquestacion) -> str:
    """Lo que se puede hacer con un flujo que se detiene en su etapa."""
    if flujo.etapa == EtapaFlujo.INGESTA:
        return "Para reintentar, vuelve a subir el archivo."
    return "Para reintentarla, reanuda el flujo."
