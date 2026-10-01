"""La ejecucion de decision: decidir toda una corrida publicada, sin publicar nada a medias.

El orden importa:
  1. abre una EjecucionDecision EN_PROCESO y la confirma antes de decidir nada: si algo falla,
     queda rastro de que se intento
  2. toma la ejecucion con su fila bloqueada, para que la decida un solo worker a la vez, y lee las
     cuentas de la corrida por lotes, en el orden de su llave unica
  3. decide cada cuenta con las reglas puras de `reglas.py` e inserta las decisiones del lote
  4. revisa que haya una decision por cada cuenta y cierra la ejecucion EXITOSA, en la misma
     transaccion que inserto las decisiones de todos los lotes

Si algo falla entre el paso 2 y el 4, se revierte todo y la ejecucion queda FALLIDA, con cuantas
cuentas alcanzo a evaluar. Un estado terminal, EXITOSA o FALLIDA, ya no cambia. Las reglas no saben
nada de esto: siguen puras, y quien las aplica sobre la base es este modulo.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import Row, insert, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, func, select
from sqlmodel.sql.expression import SelectOfScalar

from motor_cartera.db.modelos import (
    Corrida,
    Cuenta,
    DecisionCuenta,
    EjecucionDecision,
    EstadoCorrida,
    EstadoDecision,
    ahora,
)
from motor_cartera.db.sesion import sesion
from motor_cartera.decision.reglas import (
    VERSION_REGLAS_DECISION,
    EntradaDecision,
    ResultadoDecision,
    decidir_cuenta,
)

log = logging.getLogger(__name__)

INDICE_DECISION_EXITOSA = "ux_ejecucion_decision_exitosa"

TAMANO_LOTE = 1000
"""Cuantas cuentas se leen e insertan a la vez. Es un ajuste tecnico y no una regla de negocio: no
cambia ninguna decision, solo cuantas cuentas hay en memoria y cuantas idas a la base hacen falta.
Por eso vive aqui y no en la configuracion."""


class CorridaNoDecidible(Exception):
    """La corrida no esta EXITOSA: no publico una cartera, asi que no hay cuentas que decidir."""

    def __init__(self, corrida: Corrida) -> None:
        super().__init__(
            f"La corrida {corrida.run_id} esta {corrida.estado}; solo se decide una corrida "
            "EXITOSA."
        )
        self.corrida = corrida


class DecisionYaGenerada(Exception):
    """La corrida ya se decidio con exito con esta version de las reglas."""

    def __init__(self, previa: EjecucionDecision) -> None:
        super().__init__(
            f"Esta corrida ya se decidio con {previa.version_reglas}: la ejecucion "
            f"{previa.decision_run_id}."
        )
        self.previa = previa


class _NoSePublica(Exception):
    """Una razon conocida para no publicar ninguna decision. Su mensaje es para una persona y va tal
    cual al detalle de la ejecucion: no lleva datos de las cuentas."""


def abrir_ejecucion(s: Session, corrida: Corrida) -> EjecucionDecision:
    """Registra la ejecucion EN_PROCESO antes de decidir nada: si algo falla, queda rastro.

    Desde aqui queda fija la version de las reglas con que se va a decidir. Solo se decide una
    corrida EXITOSA; con cualquier otra levanta CorridaNoDecidible y no registra nada.

    Si la corrida ya se decidio con exito con esta version, levanta DecisionYaGenerada: decidirla
    otra vez duplicaria sus decisiones. Esta revision es la via amable, porque sabe decir cual
    ejecucion fue; la garantia es el indice unico parcial, que atrapa las carreras al cerrar. Por
    eso otra ejecucion EN_PROCESO de la misma corrida no impide abrir esta: las dos pueden trabajar,
    pero solo una puede cerrar EXITOSA.
    """
    if corrida.estado != EstadoCorrida.EXITOSA:
        raise CorridaNoDecidible(corrida)
    previa = s.exec(_exitosa(corrida.id, VERSION_REGLAS_DECISION)).one_or_none()
    if previa is not None:
        raise DecisionYaGenerada(previa)

    ejecucion = EjecucionDecision(corrida_id=corrida.id, version_reglas=VERSION_REGLAS_DECISION)
    s.add(ejecucion)
    s.commit()
    s.refresh(ejecucion)
    log.info("ejecucion %s abierta para la corrida %s", ejecucion.decision_run_id, corrida.run_id)
    return ejecucion


def ejecutar_decision(ejecucion_id: int, *, tamano_lote: int = TAMANO_LOTE) -> None:
    """Decide cada cuenta de la corrida y publica todas las decisiones, o ninguna.

    Una ejecucion la decide un solo worker a la vez: su fila se bloquea al empezar y se suelta con
    el commit o el rollback. Otro worker con la misma ejecucion espera, y si la encuentra terminada,
    EXITOSA o FALLIDA, no la vuelve a ejecutar.

    No hay commit entre lotes: las decisiones de todos y el cierre EXITOSA van en una sola
    transaccion. Si algo falla a la mitad se revierte todo, y en otra transaccion la ejecucion
    queda FALLIDA, con cuantas cuentas alcanzo a evaluar y el motivo, si para entonces sigue
    EN_PROCESO: ningun camino de error degrada un estado terminal.

    Un fallo del motor no levanta excepciones: su resultado es el estado de la ejecucion. La
    excepcion es perder la carrera: si otra ejecucion de la misma version cerro EXITOSA primero,
    esta queda FALLIDA y levanta DecisionYaGenerada con la que gano. No se revisa antes de empezar
    si ya hay una EXITOSA: eso lo garantiza el indice al cerrar.

    Un tamano de lote que no sea positivo es un error de quien llama: levanta ValueError antes de
    tocar la base.
    """
    _validar_tamano_lote(tamano_lote)
    with sesion() as s:
        # El estado se revisa ya con la fila bloqueada: es el que dejo el ultimo worker que la tuvo.
        ejecucion = s.exec(_bloqueada(ejecucion_id)).one()
        if ejecucion.estado != EstadoDecision.EN_PROCESO:
            log.warning(
                "ejecucion %s ya termino %s; no se vuelve a ejecutar",
                ejecucion.decision_run_id,
                ejecucion.estado,
            )
            return  # al cerrarse, la sesion revierte y suelta la fila
        # Se leen ahora: despues de un rollback la ejecucion en memoria caduca, y leerla otra vez
        # seria volver a la base justo cuando algo fallo.
        etiqueta, corrida_id = ejecucion.decision_run_id, ejecucion.corrida_id
        version = ejecucion.version_reglas
        evaluadas = 0
        try:
            _comprobar_decidible(s, ejecucion)
            for lote in _lotes_cuentas(s, corrida_id, tamano_lote):
                filas = []
                for cuenta in lote:
                    entrada = EntradaDecision(
                        dias_atraso=cuenta.dias_atraso, saldo_total=cuenta.saldo_total
                    )
                    resultado = decidir_cuenta(entrada)
                    evaluadas += 1
                    filas.append(_fila_decision(ejecucion_id, cuenta.id, resultado))
                s.execute(insert(DecisionCuenta), filas)
            _cerrar(s, ejecucion, evaluadas)
            s.commit()  # el unico commit que publica decisiones
        except _NoSePublica as exc:
            s.rollback()
            _fallar(s, ejecucion_id, etiqueta, evaluadas, str(exc))
        except IntegrityError as exc:
            s.rollback()
            if _restriccion(exc) != INDICE_DECISION_EXITOSA:
                log.exception("ejecucion %s: violacion de integridad inesperada", etiqueta)
                _fallar(
                    s,
                    ejecucion_id,
                    etiqueta,
                    evaluadas,
                    "Error interno al publicar; ver la bitacora del servicio.",
                )
                return
            _fallar(
                s,
                ejecucion_id,
                etiqueta,
                evaluadas,
                f"Otra ejecucion publico las decisiones de esta corrida con {version} mientras "
                "esta se procesaba; no se publican dos veces.",
            )
            # El indice la vio al rechazar este cierre: es la que gano.
            raise DecisionYaGenerada(s.exec(_exitosa(corrida_id, version)).one()) from exc
        except Exception as exc:
            s.rollback()
            log.exception("ejecucion %s: error inesperado", etiqueta)
            motivo = f"Error interno ({type(exc).__name__}); ver la bitacora."
            _fallar(s, ejecucion_id, etiqueta, evaluadas, motivo)
        else:
            log.info("ejecucion %s: se decidieron %s cuentas con %s", etiqueta, evaluadas, version)


def decidir_corrida(corrida_id: int, *, tamano_lote: int = TAMANO_LOTE) -> EjecucionDecision:
    """Una ejecucion completa en primer plano, y como termino. La API hara lo mismo en dos tiempos:
    abrir la ejecucion al recibir la peticion y ejecutarla en segundo plano.

    Propaga CorridaNoDecidible y DecisionYaGenerada. Un fallo del motor no se propaga: la ejecucion
    ya existe, y se devuelve FALLIDA.
    """
    _validar_tamano_lote(tamano_lote)
    with sesion() as s:
        ejecucion = abrir_ejecucion(s, s.get_one(Corrida, corrida_id))
    ejecutar_decision(ejecucion.id, tamano_lote=tamano_lote)
    with sesion() as s:
        return s.get_one(EjecucionDecision, ejecucion.id)


def _comprobar_decidible(s: Session, ejecucion: EjecucionDecision) -> None:
    """Lo que tiene que seguir siendo cierto para decidir, ya dentro de la transaccion que decide.

    Una ejecucion de otra version de las reglas no se decide con estas: este codigo solo sabe
    aplicar las suyas, y aplicarlas con otro nombre falsearia la trazabilidad. Y aunque
    abrir_ejecucion ya reviso la corrida, entre las dos transacciones la base pudo cambiar: se
    vuelve a leer, y no se toca.
    """
    if ejecucion.version_reglas != VERSION_REGLAS_DECISION:
        raise _NoSePublica(
            f"La ejecucion pide las reglas {ejecucion.version_reglas} y este servicio solo decide "
            f"con {VERSION_REGLAS_DECISION}; no se decidio ninguna cuenta."
        )
    corrida = s.get_one(Corrida, ejecucion.corrida_id)
    if corrida.estado != EstadoCorrida.EXITOSA:
        raise _NoSePublica(
            f"La corrida {corrida.run_id} ya no esta EXITOSA, esta {corrida.estado}; no se decide "
            "una cartera que no esta publicada."
        )


def _lotes_cuentas(s: Session, corrida_id: int, tamano_lote: int) -> Iterator[Sequence[Row]]:
    """Las cuentas de la corrida en lotes de `tamano_lote`, en el orden de cliente_unico.

    Por llave y no con OFFSET: cada lote empieza despues del ultimo cliente del anterior, y el
    indice unico (corrida_id, cliente_unico) llega ahi sin recorrer lo ya leido. Trae solo lo que
    las reglas leen de cada cuenta, y su id para guardar la decision.
    """
    _validar_tamano_lote(tamano_lote)
    consulta = (
        select(Cuenta.id, Cuenta.cliente_unico, Cuenta.dias_atraso, Cuenta.saldo_total)
        .where(Cuenta.corrida_id == corrida_id)
        .order_by(Cuenta.cliente_unico)
        .limit(tamano_lote)
    )
    ultimo = None
    while True:
        lote = s.exec(
            consulta if ultimo is None else consulta.where(Cuenta.cliente_unico > ultimo)
        ).all()
        if lote:
            yield lote
        if len(lote) < tamano_lote:
            return
        ultimo = lote[-1].cliente_unico


def _cerrar(s: Session, ejecucion: EjecucionDecision, evaluadas: int) -> None:
    """Deja la ejecucion EXITOSA si hay exactamente una decision por cada cuenta de la corrida.

    Cuenta en la misma transaccion que inserto las decisiones. Que el motor haya evaluado todo lo
    que leyo no basta: si el lector se salto una cuenta, el conteo lo descubre. Envia el cambio de
    estado pero no confirma, para que una carrera con otra ejecucion EXITOSA se descubra todavia
    dentro de la transaccion; el commit es de quien llama.
    """
    cuentas = s.exec(
        select(func.count()).select_from(Cuenta).where(Cuenta.corrida_id == ejecucion.corrida_id)
    ).one()
    decisiones = s.exec(
        select(func.count())
        .select_from(DecisionCuenta)
        .where(DecisionCuenta.ejecucion_decision_id == ejecucion.id)
    ).one()
    # La fase 1 no publica una corrida sin cuentas. Si aparece una, no se le inventa un exito vacio.
    if cuentas == 0:
        raise _NoSePublica(
            "La corrida no tiene cuentas; una ejecucion sin decisiones no se publica."
        )
    if not evaluadas == cuentas == decisiones:
        raise _NoSePublica(
            f"Las decisiones estan incompletas: la corrida tiene {cuentas:,} cuentas, se evaluaron "
            f"{evaluadas:,} y se guardaron {decisiones:,} decisiones; no se publico ninguna."
        )

    ejecucion.estado = EstadoDecision.EXITOSA
    ejecucion.cuentas_evaluadas = evaluadas
    ejecucion.cuentas_decididas = decisiones
    ejecucion.detalle = f"Se decidieron {decisiones:,} cuentas con {ejecucion.version_reglas}."
    ejecucion.terminada_en = ahora()
    s.add(ejecucion)
    s.flush()


def _fallar(s: Session, ejecucion_id: int, etiqueta: UUID, evaluadas: int, motivo: str) -> None:
    """Deja la ejecucion FALLIDA, solo si sigue EN_PROCESO.

    Es un UPDATE condicionado al estado que tiene la base, y no la ejecucion que se tenia en
    memoria: entre el rollback y este registro, otro worker pudo tomar la misma ejecucion y dejarla
    EXITOSA, y un fallo que llega tarde no la degrada. Si ya termino, se deja como esta y el fallo
    queda solo en la bitacora. No borra decisiones: las de este intento ya las quito el rollback, y
    las de una ejecucion EXITOSA son suyas.
    """
    registrado = s.execute(
        update(EjecucionDecision)
        .where(
            EjecucionDecision.id == ejecucion_id,
            EjecucionDecision.estado == EstadoDecision.EN_PROCESO,
        )
        .values(
            estado=EstadoDecision.FALLIDA,
            cuentas_evaluadas=evaluadas,
            cuentas_decididas=0,
            detalle=motivo,
            terminada_en=ahora(),
        )
        .execution_options(synchronize_session=False)
    ).rowcount
    s.commit()
    if registrado:
        log.warning("ejecucion %s fallida: %s", etiqueta, motivo)
        return
    estado = s.exec(
        select(EjecucionDecision.estado).where(EjecucionDecision.id == ejecucion_id)
    ).one()
    log.warning("ejecucion %s ya termino %s; este fallo no la cambia: %s", etiqueta, estado, motivo)


def _fila_decision(
    ejecucion_id: int, cuenta_id: int, resultado: ResultadoDecision
) -> dict[str, Any]:
    # Texto plano, campo por campo: no se confia en que un StrEnum se guarde como su valor.
    return {
        "ejecucion_decision_id": ejecucion_id,
        "cuenta_id": cuenta_id,
        "segmento": resultado.segmento.value,
        "prioridad": resultado.prioridad.value,
        "canal_recomendado": resultado.canal_recomendado,
        "motivos": [
            {"codigo": motivo.codigo.value, "campo": motivo.campo, "valor": motivo.valor}
            for motivo in resultado.motivos
        ],
    }


def _bloqueada(ejecucion_id: int) -> SelectOfScalar[EjecucionDecision]:
    """La ejecucion, con FOR UPDATE: bloquea solo su fila, hasta el commit o el rollback de la
    transaccion que la toma. Otra transaccion que la pida igual espera a que se suelte, y entonces
    la lee como quedo."""
    return select(EjecucionDecision).where(EjecucionDecision.id == ejecucion_id).with_for_update()


def _exitosa(corrida_id: int, version: str) -> SelectOfScalar[EjecucionDecision]:
    """La ejecucion EXITOSA de la corrida con esa version de las reglas. Hay a lo mas una: lo
    garantiza el indice unico parcial."""
    return select(EjecucionDecision).where(
        EjecucionDecision.corrida_id == corrida_id,
        EjecucionDecision.version_reglas == version,
        EjecucionDecision.estado == EstadoDecision.EXITOSA,
    )


def _validar_tamano_lote(tamano_lote: int) -> None:
    if tamano_lote <= 0:
        raise ValueError(f"El tamano del lote debe ser mayor que cero: {tamano_lote}.")


def _restriccion(exc: IntegrityError) -> str | None:
    diagnostico = getattr(exc.orig, "diag", None)
    return getattr(diagnostico, "constraint_name", None)
