"""La ejecucion territorial: organizar por municipio las decisiones de una ejecucion, sin publicar
nada a medias.

El orden importa:
  1. abre una EjecucionTerritorial EN_PROCESO y la confirma antes de calcular nada: si algo falla,
     queda rastro de que se intento
  2. toma la ejecucion con su fila bloqueada, para que la procese un solo worker a la vez, y vuelve
     a revisar la ejecucion de decision de la que sale y que sus decisiones esten completas
  3. agrega las decisiones por municipio en PostgreSQL, con un solo GROUP BY, y convierte cada fila
     en una EntradaTerritorio
  4. ordena los municipios con las reglas puras de `reglas.py`, inserta los resultados en bloque, y
     cierra la ejecucion EXITOSA en la misma transaccion que los inserto

Si algo falla entre el paso 2 y el 4, se revierte todo y la ejecucion queda FALLIDA, con cuantos
municipios alcanzo a evaluar. Un estado terminal, EXITOSA o FALLIDA, ya no cambia. Las reglas no
saben nada de esto: siguen puras, y quien las aplica sobre la base es este modulo. Por eso el
paquete no lo reexporta: `motor_cartera.territorial` sigue importandose sin base de datos.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import Row, insert, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, func, select
from sqlmodel.sql.expression import Select, SelectOfScalar

from motor_cartera.db.modelos import (
    Cuenta,
    DecisionCuenta,
    EjecucionDecision,
    EjecucionTerritorial,
    EstadoDecision,
    EstadoTerritorial,
    ResultadoTerritorial,
    ahora,
)
from motor_cartera.db.sesion import sesion
from motor_cartera.territorial.reglas import (
    VERSION_REGLAS_TERRITORIAL,
    EntradaTerritorio,
    ResultadoTerritorio,
    priorizar_territorios,
)

log = logging.getLogger(__name__)

VERSION_DECISION_COMPATIBLE = "decision/v1"
"""Las unicas decisiones que territorial/v1 sabe organizar. Es un literal, y no se toma de
VERSION_REGLAS_DECISION: cuando el Decision Engine decida con decision/v2, territorial/v1 no se
vuelve compatible solo. Que version de las reglas consume a cual es parte del contrato historico."""

CANAL_CAMPO = "CAMPO"
"""El canal recomendado que cuenta como trabajo de campo, en el vocabulario de decision/v1. Se
compara con DecisionCuenta.canal_recomendado, la decision, y nunca con Cuenta.canal, que es un dato
de la cartera."""

INDICE_TERRITORIAL_EXITOSA = "ux_ejecucion_territorial_exitosa"


class DecisionNoTerritorializable(Exception):
    """Las decisiones de esa ejecucion no se organizan con territorial/v1: la ejecucion no termino
    EXITOSA, o se decidio con otras reglas que las de VERSION_DECISION_COMPATIBLE.

    Es una precondicion: se levanta antes de registrar nada, y no es el fallo de una ejecucion
    territorial ya abierta, que queda FALLIDA."""

    def __init__(self, ejecucion_decision: EjecucionDecision, razon: str) -> None:
        super().__init__(razon)
        self.ejecucion_decision = ejecucion_decision


class TerritorialYaGenerado(Exception):
    """Las decisiones de esa ejecucion ya se organizaron con exito con esta version de las reglas
    territoriales."""

    def __init__(self, previa: EjecucionTerritorial) -> None:
        super().__init__(
            f"Esta ejecucion de decision ya se organizo con {previa.version_reglas}: la ejecucion "
            f"territorial {previa.territorial_run_id}."
        )
        self.previa = previa


class _NoSePublica(Exception):
    """Una razon conocida para no publicar ningun municipio. Su mensaje es para una persona y va tal
    cual al detalle de la ejecucion: no lleva datos de las cuentas."""


def abrir_ejecucion(s: Session, ejecucion_decision: EjecucionDecision) -> EjecucionTerritorial:
    """Registra la ejecucion EN_PROCESO antes de calcular nada: si algo falla, queda rastro.

    Desde aqui queda fija la version de las reglas territoriales con que se va a calcular. Solo se
    organizan las decisiones de una ejecucion EXITOSA de decision/v1; con cualquier otra levanta
    DecisionNoTerritorializable y no registra nada.

    Si esas decisiones ya se organizaron con exito con esta version, levanta TerritorialYaGenerado:
    hacerlo otra vez duplicaria sus resultados. Esta revision es la via amable, porque sabe decir
    cual ejecucion fue; la garantia es el indice unico parcial, que atrapa las carreras al cerrar.
    Por eso otra ejecucion EN_PROCESO de la misma fuente no impide abrir esta: las dos pueden
    trabajar, pero solo una puede cerrar EXITOSA.
    """
    razon = _por_que_no_se_organiza(ejecucion_decision)
    if razon is not None:
        raise DecisionNoTerritorializable(ejecucion_decision, razon)
    previa = s.exec(_exitosa(ejecucion_decision.id, VERSION_REGLAS_TERRITORIAL)).one_or_none()
    if previa is not None:
        raise TerritorialYaGenerado(previa)

    ejecucion = EjecucionTerritorial(
        ejecucion_decision_id=ejecucion_decision.id, version_reglas=VERSION_REGLAS_TERRITORIAL
    )
    s.add(ejecucion)
    s.commit()
    s.refresh(ejecucion)
    log.info(
        "ejecucion territorial %s abierta para la ejecucion de decision %s",
        ejecucion.territorial_run_id,
        ejecucion_decision.decision_run_id,
    )
    return ejecucion


def ejecutar_territorial(ejecucion_territorial_id: int) -> None:
    """Organiza por municipio las decisiones de la fuente y publica todos los resultados, o ninguno.

    Una ejecucion la procesa un solo worker a la vez: su fila se bloquea al empezar y se suelta con
    el commit o el rollback. Otro worker con la misma ejecucion espera, y si la encuentra terminada,
    EXITOSA o FALLIDA, no la vuelve a ejecutar. La ejecucion de decision de la que sale no se
    bloquea: una EXITOSA ya no cambia, y aqui solo se vuelve a leer y a revisar.

    La agregacion, la evaluacion, los resultados y el cierre EXITOSA van en una sola transaccion. Si
    algo falla se revierte todo, y en otra transaccion la ejecucion queda FALLIDA, con el motivo y
    con territorios_evaluados: cero si el nucleo no llego a terminar, y si termino, cuantos
    municipios evaluo. Ningun camino de error degrada un estado terminal.

    Un fallo del motor no levanta excepciones: su resultado es el estado de la ejecucion. La
    excepcion es perder la carrera: si otra ejecucion de la misma fuente y version cerro EXITOSA
    primero, esta queda FALLIDA y levanta TerritorialYaGenerado con la que gano.
    """
    with sesion() as s:
        # El estado se revisa ya con la fila bloqueada: es el que dejo el ultimo worker que la tuvo.
        ejecucion = s.exec(_bloqueada(ejecucion_territorial_id)).one()
        if ejecucion.estado != EstadoTerritorial.EN_PROCESO:
            log.warning(
                "ejecucion territorial %s ya termino %s; no se vuelve a ejecutar",
                ejecucion.territorial_run_id,
                ejecucion.estado,
            )
            return  # al cerrarse, la sesion revierte y suelta la fila
        # Se leen ahora: despues de un rollback la ejecucion en memoria caduca, y leerla otra vez
        # seria volver a la base justo cuando algo fallo.
        etiqueta, version = ejecucion.territorial_run_id, ejecucion.version_reglas
        decision_id = ejecucion.ejecucion_decision_id
        evaluados = 0
        try:
            fuente, decisiones = _comprobar_fuente(s, ejecucion)
            entradas = _agregar(s, fuente)
            _comprobar_agregados(entradas, decisiones)
            resultados = priorizar_territorios(entradas)
            evaluados = len(resultados)
            # render_nulls: el lugar de un SIN_CARGA es NULL a proposito, y se escribe como NULL.
            # Sin esto, el INSERT en bloque del ORM omite las columnas en None y parte los
            # municipios en dos sentencias: los que tienen lugar y los que no.
            s.execute(
                insert(ResultadoTerritorial).execution_options(render_nulls=True),
                [_fila_resultado(ejecucion_territorial_id, r) for r in resultados],
            )
            _cerrar(s, ejecucion, len(entradas), resultados, decisiones)
            s.commit()  # el unico commit que publica resultados
        except _NoSePublica as exc:
            s.rollback()
            _fallar(s, ejecucion_territorial_id, etiqueta, evaluados, str(exc))
        except IntegrityError as exc:
            s.rollback()
            if _restriccion(exc) != INDICE_TERRITORIAL_EXITOSA:
                log.exception(
                    "ejecucion territorial %s: violacion de integridad inesperada", etiqueta
                )
                _fallar(
                    s,
                    ejecucion_territorial_id,
                    etiqueta,
                    evaluados,
                    "Error interno al publicar; ver la bitacora del servicio.",
                )
                return
            _fallar(
                s,
                ejecucion_territorial_id,
                etiqueta,
                evaluados,
                "Otra ejecucion publico los territorios de esta ejecucion de decision con "
                f"{version} mientras esta se procesaba; no se publican dos veces.",
            )
            # El indice la vio al rechazar este cierre: es la que gano.
            raise TerritorialYaGenerado(s.exec(_exitosa(decision_id, version)).one()) from exc
        except Exception as exc:
            s.rollback()
            log.exception("ejecucion territorial %s: error inesperado", etiqueta)
            motivo = f"Error interno ({type(exc).__name__}); ver la bitacora."
            _fallar(s, ejecucion_territorial_id, etiqueta, evaluados, motivo)
        else:
            log.info(
                "ejecucion territorial %s: se publicaron %s municipios con %s",
                etiqueta,
                evaluados,
                version,
            )


def territorializar_decision(ejecucion_decision_id: int) -> EjecucionTerritorial:
    """Una ejecucion territorial completa en primer plano, y como termino.

    Propaga DecisionNoTerritorializable y TerritorialYaGenerado. Un fallo del motor no se propaga:
    la ejecucion ya existe, y se devuelve FALLIDA. Cada paso usa su propia sesion, como
    decidir_corrida: ninguna queda abierta durante todo el proceso.
    """
    with sesion() as s:
        ejecucion = abrir_ejecucion(s, s.get_one(EjecucionDecision, ejecucion_decision_id))
    ejecutar_territorial(ejecucion.id)
    with sesion() as s:
        return s.get_one(EjecucionTerritorial, ejecucion.id)


def _por_que_no_se_organiza(fuente: EjecucionDecision) -> str | None:
    """Por que las decisiones de `fuente` no se pueden organizar con territorial/v1, o None si se
    pueden. El mensaje es para una persona."""
    if fuente.estado != EstadoDecision.EXITOSA:
        return (
            f"La ejecucion de decision {fuente.decision_run_id} esta {fuente.estado}; solo se "
            "organizan por territorio las decisiones de una ejecucion EXITOSA."
        )
    if fuente.version_reglas != VERSION_DECISION_COMPATIBLE:
        return (
            f"La ejecucion de decision {fuente.decision_run_id} se decidio con "
            f"{fuente.version_reglas}; {VERSION_REGLAS_TERRITORIAL} solo organiza decisiones de "
            f"{VERSION_DECISION_COMPATIBLE}."
        )
    return None


def _comprobar_fuente(s: Session, ejecucion: EjecucionTerritorial) -> tuple[EjecucionDecision, int]:
    """Lo que tiene que seguir siendo cierto para organizar, ya dentro de la transaccion que
    publica. Devuelve la ejecucion de decision y cuantas decisiones tiene.

    Una ejecucion de otra version de las reglas territoriales no se calcula con estas. Aunque
    abrir_ejecucion ya reviso la fuente, entre las dos transacciones la base pudo cambiar: se
    vuelve a leer, y no se toca. Y sus decisiones tienen que estar completas: lo que la ejecucion
    de decision dice que evaluo y que decidio, las decisiones que de verdad tiene y las que son de
    cuentas de su propia corrida tienen que ser el mismo numero. La base garantiza que cada decision
    apunta a una cuenta que existe, pero no que sea de esa corrida.
    """
    if ejecucion.version_reglas != VERSION_REGLAS_TERRITORIAL:
        raise _NoSePublica(
            f"La ejecucion pide las reglas {ejecucion.version_reglas} y este servicio solo "
            f"organiza con {VERSION_REGLAS_TERRITORIAL}; no se publico ningun territorio."
        )
    fuente = s.get_one(EjecucionDecision, ejecucion.ejecucion_decision_id)
    razon = _por_que_no_se_organiza(fuente)
    if razon is not None:
        raise _NoSePublica(f"{razon} No se publico ningun territorio.")
    decisiones, de_la_corrida = s.exec(_conteo_de_decisiones(fuente.id, fuente.corrida_id)).one()
    # El Decision Engine no publica una ejecucion sin decisiones. Si aparece una, no se le inventa
    # un territorial vacio.
    if decisiones == 0:
        raise _NoSePublica(
            "La ejecucion de decision no tiene decisiones; no se publica un territorial vacio."
        )
    if not fuente.cuentas_evaluadas == fuente.cuentas_decididas == decisiones == de_la_corrida:
        raise _NoSePublica(
            "La ejecucion de decision esta incompleta: dice que evaluo "
            f"{fuente.cuentas_evaluadas:,} cuentas y decidio {fuente.cuentas_decididas:,}, tiene "
            f"{decisiones:,} decisiones y {de_la_corrida:,} son de cuentas de su corrida; no se "
            "publico ningun territorio."
        )
    return fuente, decisiones


def _agregar(s: Session, fuente: EjecucionDecision) -> list[EntradaTerritorio]:
    """Los agregados de cada municipio, ya como entradas de territorial/v1.

    Agrega PostgreSQL, con un solo GROUP BY: a Python llega una fila por municipio, nunca una por
    cuenta. Cada fila pasa por EntradaTerritorio, que es la barrera del dominio: aqui no se repite
    ninguna de sus reglas. Si un agregado no la cumple, no se publica nada.
    """
    filas = s.exec(_agregados(fuente.id, fuente.corrida_id)).all()
    try:
        return [_entrada(fila) for fila in filas]
    except (TypeError, ValueError) as exc:
        log.warning(
            "ejecucion de decision %s: un agregado no cumple %s: %s",
            fuente.decision_run_id,
            VERSION_REGLAS_TERRITORIAL,
            exc,
        )
        raise _NoSePublica(
            f"Los agregados de un municipio no cumplen {VERSION_REGLAS_TERRITORIAL}; ver la "
            "bitacora. No se publico ningun territorio."
        ) from exc


def _comprobar_agregados(entradas: Sequence[EntradaTerritorio], decisiones: int) -> None:
    """Que la agregacion no haya perdido ni inventado decisiones. Una ejecucion de decision completa
    no puede dar cero municipios, y los municipios tienen que sumar exactamente sus decisiones: un
    JOIN o un filtro que se salte filas lo descubre esta suma, no un territorial incompleto."""
    if not entradas:
        raise _NoSePublica(
            "La agregacion no produjo ningun municipio; no se publica un territorial vacio."
        )
    agregadas = sum(entrada.cuentas_total for entrada in entradas)
    if agregadas != decisiones:
        raise _NoSePublica(
            f"Los municipios agregados suman {agregadas:,} decisiones y la ejecucion de decision "
            f"tiene {decisiones:,}; no se publico ningun territorio."
        )


def _cerrar(
    s: Session,
    ejecucion: EjecucionTerritorial,
    agregados: int,
    resultados: Sequence[ResultadoTerritorio],
    decisiones: int,
) -> None:
    """Deja la ejecucion EXITOSA si cada municipio agregado se evaluo y quedo guardado una vez.

    Cuenta en la misma transaccion que inserto los resultados: los municipios agregados, los
    evaluados y las filas guardadas tienen que ser el mismo numero, mayor que cero, y los resultados
    tienen que sumar exactamente las decisiones de la fuente. Envia el cambio de estado pero no
    confirma, para que una carrera con otra ejecucion EXITOSA se descubra todavia dentro de la
    transaccion; el commit es de quien llama.
    """
    publicados = s.exec(
        select(func.count())
        .select_from(ResultadoTerritorial)
        .where(ResultadoTerritorial.ejecucion_territorial_id == ejecucion.id)
    ).one()
    evaluados = len(resultados)
    if not (agregados == evaluados == publicados and publicados > 0):
        raise _NoSePublica(
            f"Los resultados estan incompletos: se agregaron {agregados:,} municipios, se "
            f"evaluaron {evaluados:,} y se guardaron {publicados:,}; no se publico ninguno."
        )
    organizadas = sum(resultado.cuentas_total for resultado in resultados)
    if organizadas != decisiones:
        raise _NoSePublica(
            f"Los resultados suman {organizadas:,} decisiones y la ejecucion de decision tiene "
            f"{decisiones:,}; no se publico ninguno."
        )

    ejecucion.estado = EstadoTerritorial.EXITOSA
    ejecucion.territorios_evaluados = evaluados
    ejecucion.territorios_publicados = publicados
    ejecucion.detalle = (
        f"Se organizaron {decisiones:,} decisiones en {publicados:,} municipios con "
        f"{ejecucion.version_reglas}."
    )
    ejecucion.terminada_en = ahora()
    s.add(ejecucion)
    s.flush()


def _fallar(s: Session, ejecucion_id: int, etiqueta: UUID, evaluados: int, motivo: str) -> None:
    """Deja la ejecucion FALLIDA, solo si sigue EN_PROCESO.

    Es un UPDATE condicionado al estado que tiene la base, y no la ejecucion que se tenia en
    memoria: entre el rollback y este registro, otro worker pudo tomar la misma ejecucion y dejarla
    EXITOSA, y un fallo que llega tarde no la degrada. Si ya termino, se deja como esta y el fallo
    queda solo en la bitacora. No borra resultados: los de este intento ya los quito el rollback, y
    los de una ejecucion EXITOSA son suyos.
    """
    registrado = s.execute(
        update(EjecucionTerritorial)
        .where(
            EjecucionTerritorial.id == ejecucion_id,
            EjecucionTerritorial.estado == EstadoTerritorial.EN_PROCESO,
        )
        .values(
            estado=EstadoTerritorial.FALLIDA,
            territorios_evaluados=evaluados,
            territorios_publicados=0,
            detalle=motivo,
            terminada_en=ahora(),
        )
        .execution_options(synchronize_session=False)
    ).rowcount
    s.commit()
    if registrado:
        log.warning("ejecucion territorial %s fallida: %s", etiqueta, motivo)
        return
    estado = s.exec(
        select(EjecucionTerritorial.estado).where(EjecucionTerritorial.id == ejecucion_id)
    ).one()
    log.warning(
        "ejecucion territorial %s ya termino %s; este fallo no la cambia: %s",
        etiqueta,
        estado,
        motivo,
    )


def _conteo_de_decisiones(ejecucion_decision_id: int, corrida_id: int) -> Select[Any]:
    """Cuantas decisiones tiene la ejecucion, y cuantas de ellas son de cuentas de su corrida. El
    JOIN no pierde decisiones: la llave foranea garantiza que cada una apunta a una cuenta."""
    return (
        select(func.count(), func.count().filter(Cuenta.corrida_id == corrida_id))
        .select_from(DecisionCuenta)
        .join(Cuenta, DecisionCuenta.cuenta_id == Cuenta.id)
        .where(DecisionCuenta.ejecucion_decision_id == ejecucion_decision_id)
    )


def _agregados(ejecucion_decision_id: int, corrida_id: int) -> Select[Any]:
    """Una fila por municipio con sus cuatro agregados, sobre las decisiones de la ejecucion que son
    de cuentas de su corrida.

    Lo que cuenta como campo es la decision, DecisionCuenta.canal_recomendado, y no Cuenta.canal.
    Sin cuentas de campo, el conteo da 0 y la suma se vuelve 0 en lugar de NULL. Las sumas llegan
    como Decimal, sin pasar por float. El orden por clave solo hace la consulta repetible: el orden
    publicado lo decide territorial/v1.
    """
    es_campo = DecisionCuenta.canal_recomendado == CANAL_CAMPO
    return (
        select(
            Cuenta.cve_entidad,
            Cuenta.cve_municipio,
            func.count().label("cuentas_total"),
            func.sum(Cuenta.saldo_total).label("saldo_total"),
            func.count().filter(es_campo).label("cuentas_campo"),
            func.coalesce(func.sum(Cuenta.saldo_total).filter(es_campo), 0).label("saldo_campo"),
        )
        .select_from(DecisionCuenta)
        .join(Cuenta, DecisionCuenta.cuenta_id == Cuenta.id)
        .where(
            DecisionCuenta.ejecucion_decision_id == ejecucion_decision_id,
            Cuenta.corrida_id == corrida_id,
        )
        .group_by(Cuenta.cve_entidad, Cuenta.cve_municipio)
        .order_by(Cuenta.cve_entidad, Cuenta.cve_municipio)
    )


def _entrada(fila: Row[Any]) -> EntradaTerritorio:
    return EntradaTerritorio(
        cve_entidad=fila.cve_entidad,
        cve_municipio=fila.cve_municipio,
        cuentas_total=fila.cuentas_total,
        saldo_total=fila.saldo_total,
        cuentas_campo=fila.cuentas_campo,
        saldo_campo=fila.saldo_campo,
    )


def _fila_resultado(ejecucion_id: int, resultado: ResultadoTerritorio) -> dict[str, Any]:
    # Texto plano, campo por campo: no se confia en que un StrEnum se guarde como su valor.
    return {
        "ejecucion_territorial_id": ejecucion_id,
        "cve_entidad": resultado.cve_entidad,
        "cve_municipio": resultado.cve_municipio,
        "cuentas_total": resultado.cuentas_total,
        "saldo_total": resultado.saldo_total,
        "cuentas_campo": resultado.cuentas_campo,
        "saldo_campo": resultado.saldo_campo,
        "carga": resultado.carga.value,
        "posicion_campo": resultado.posicion_campo,
        "motivos": [
            {"codigo": motivo.codigo.value, "campo": motivo.campo, "valor": motivo.valor}
            for motivo in resultado.motivos
        ],
    }


def _bloqueada(ejecucion_id: int) -> SelectOfScalar[EjecucionTerritorial]:
    """La ejecucion, con FOR UPDATE: bloquea solo su fila, hasta el commit o el rollback de la
    transaccion que la toma. Otra transaccion que la pida igual espera a que se suelte, y entonces
    la lee como quedo."""
    return (
        select(EjecucionTerritorial)
        .where(EjecucionTerritorial.id == ejecucion_id)
        .with_for_update()
    )


def _exitosa(ejecucion_decision_id: int, version: str) -> SelectOfScalar[EjecucionTerritorial]:
    """La ejecucion EXITOSA de esas decisiones con esa version de las reglas. Hay a lo mas una: lo
    garantiza el indice unico parcial."""
    return select(EjecucionTerritorial).where(
        EjecucionTerritorial.ejecucion_decision_id == ejecucion_decision_id,
        EjecucionTerritorial.version_reglas == version,
        EjecucionTerritorial.estado == EstadoTerritorial.EXITOSA,
    )


def _restriccion(exc: IntegrityError) -> str | None:
    diagnostico = getattr(exc.orig, "diag", None)
    return getattr(diagnostico, "constraint_name", None)
