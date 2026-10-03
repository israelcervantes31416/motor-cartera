"""La ejecucion de ruteo: trazar la ruta de cada municipio de una ejecucion territorial publicada,
sin publicar nada a medias.

El orden importa:
  1. abre una EjecucionRuteo EN_PROCESO y la confirma antes de calcular nada: si algo falla, queda
     rastro de que se intento
  2. toma la ejecucion con su fila bloqueada, para que la procese un solo worker a la vez, y vuelve
     a revisar toda la cadena de la que sale: la ejecucion territorial, la de decision debajo de
     ella, y que los municipios publicados y las decisiones de campo esten completos y cuadren
  3. lee en una sola consulta las cuentas que decision/v1 mando a CAMPO y las reparte por municipio
  4. traza la ruta de cada municipio con las reglas puras de `reglas.py`, en el orden de prioridad
     territorial, inserta las rutas y despues las paradas en bloque, y cierra la ejecucion EXITOSA
     en la misma transaccion que las inserto

Si algo falla entre el paso 2 y el 4, se revierte todo y la ejecucion queda FALLIDA, con cuantas
rutas y paradas alcanzo a calcular el nucleo. Un estado terminal, EXITOSA o FALLIDA, ya no cambia.
Las reglas no saben nada de esto: siguen puras, y quien las aplica sobre la base es este modulo. Por
eso el paquete no lo reexporta: `motor_cartera.ruteo` sigue importandose sin base de datos.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
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
    EjecucionRuteo,
    EjecucionTerritorial,
    EstadoDecision,
    EstadoRuteo,
    EstadoTerritorial,
    ParadaRuta,
    ResultadoTerritorial,
    RutaTerritorial,
    ahora,
)
from motor_cartera.db.sesion import sesion
from motor_cartera.ruteo.reglas import (
    VERSION_REGLAS_RUTEO,
    ParadaCalculada,
    ResultadoRuta,
    rutear_territorio,
)

log = logging.getLogger(__name__)

VERSION_TERRITORIAL_COMPATIBLE = "territorial/v1"
"""Las unicas organizaciones territoriales que ruteo/v1 sabe rutear. Es un literal, y no se toma de
VERSION_REGLAS_TERRITORIAL: cuando el Motor Territorial calcule con territorial/v2, ruteo/v1 no se
vuelve compatible solo."""

VERSION_DECISION_COMPATIBLE = "decision/v1"
"""Las unicas decisiones, debajo de esa organizacion territorial, cuyas cuentas de campo ruteo/v1
sabe visitar. Tambien es un literal: que version consume a cual es parte del contrato historico."""

CANAL_CAMPO = "CAMPO"
"""El canal recomendado que hace de una cuenta una parada, en el vocabulario de decision/v1. Se
compara con DecisionCuenta.canal_recomendado, la decision, y nunca con Cuenta.canal, que es un dato
de la cartera."""

INDICE_RUTEO_EXITOSA = "ux_ejecucion_ruteo_exitosa"


class TerritorialNoRuteable(Exception):
    """Los municipios de esa ejecucion territorial no se rutean con ruteo/v1: la ejecucion no
    termino EXITOSA o no es de territorial/v1, o las decisiones de las que sale no terminaron
    EXITOSA o no son de decision/v1.

    Es una precondicion: se levanta antes de registrar nada, y no es el fallo de una ejecucion de
    ruteo ya abierta, que queda FALLIDA."""

    def __init__(self, ejecucion_territorial: EjecucionTerritorial, razon: str) -> None:
        super().__init__(razon)
        self.ejecucion_territorial = ejecucion_territorial


class RuteoYaGenerado(Exception):
    """Los municipios de esa ejecucion territorial ya se rutearon con exito con esta version de las
    reglas de ruteo."""

    def __init__(self, previa: EjecucionRuteo) -> None:
        super().__init__(
            f"Esta ejecucion territorial ya se ruteo con {previa.version_reglas}: la ejecucion de "
            f"ruteo {previa.ruteo_run_id}."
        )
        self.previa = previa


class _NoSePublica(Exception):
    """Una razon conocida para no publicar ninguna ruta. Su mensaje es para una persona y va tal
    cual al detalle de la ejecucion: no lleva datos de las cuentas."""


@dataclass(frozen=True)
class _Territorio:
    """Un municipio con trabajo de campo, como lo publico la ejecucion territorial: lo que hace
    falta para trazar su ruta y para guardarla."""

    resultado_territorial_id: int
    clave: str
    cuentas_campo: int
    posicion_campo: int


@dataclass(frozen=True)
class _Fuente:
    """Lo que la ejecucion de ruteo va a rutear, revisado dentro de la transaccion que publica."""

    municipios: tuple[_Territorio, ...]
    """Los municipios con cuentas de campo, en el orden de posicion_campo: uno por ruta."""
    campo: int
    """Las decisiones de campo de la fuente, lo que suman sus municipios: una parada por cada una.
    """


def abrir_ejecucion(s: Session, ejecucion_territorial: EjecucionTerritorial) -> EjecucionRuteo:
    """Registra la ejecucion EN_PROCESO antes de calcular nada: si algo falla, queda rastro.

    Desde aqui queda fija la version de las reglas de ruteo con que se va a calcular. Solo se rutea
    una ejecucion territorial EXITOSA de territorial/v1 cuyas decisiones sean una ejecucion EXITOSA
    de decision/v1; con cualquier otra levanta TerritorialNoRuteable y no registra nada.

    Si esa ejecucion territorial ya se ruteo con exito con esta version, levanta RuteoYaGenerado:
    hacerlo otra vez duplicaria sus rutas. Esta revision es la via amable, porque sabe decir cual
    ejecucion fue; la garantia es el indice unico parcial, que atrapa las carreras al cerrar. Por
    eso otra ejecucion EN_PROCESO de la misma fuente no impide abrir esta: las dos pueden trabajar,
    pero solo una puede cerrar EXITOSA.
    """
    decision = s.get_one(EjecucionDecision, ejecucion_territorial.ejecucion_decision_id)
    razon = _por_que_no_se_rutea(ejecucion_territorial, decision)
    if razon is not None:
        raise TerritorialNoRuteable(ejecucion_territorial, razon)
    previa = s.exec(_exitosa(ejecucion_territorial.id, VERSION_REGLAS_RUTEO)).one_or_none()
    if previa is not None:
        raise RuteoYaGenerado(previa)

    ejecucion = EjecucionRuteo(
        ejecucion_territorial_id=ejecucion_territorial.id, version_reglas=VERSION_REGLAS_RUTEO
    )
    s.add(ejecucion)
    s.commit()
    s.refresh(ejecucion)
    log.info(
        "ejecucion de ruteo %s abierta para la ejecucion territorial %s",
        ejecucion.ruteo_run_id,
        ejecucion_territorial.territorial_run_id,
    )
    return ejecucion


def ejecutar_ruteo(ejecucion_ruteo_id: int) -> None:
    """Traza la ruta de cada municipio con trabajo de campo y publica todas las rutas, o ninguna.

    Una ejecucion la procesa un solo worker a la vez: su fila se bloquea al empezar y se suelta con
    el commit o el rollback. Otro worker con la misma ejecucion espera, y si la encuentra terminada,
    EXITOSA o FALLIDA, no la vuelve a ejecutar. La ejecucion territorial y la de decision de las que
    sale no se bloquean: una EXITOSA ya no cambia, y aqui solo se vuelven a leer y a revisar.

    Las rutas, las paradas y el cierre EXITOSA van en una sola transaccion. Si algo falla se
    revierte todo, y en otra transaccion la ejecucion queda FALLIDA, con el motivo y con lo que
    evaluo el nucleo: cero si no llego a terminar, y si termino, cuantas rutas y paradas calculo.
    Ningun camino de error degrada un estado terminal.

    Un fallo del motor no levanta excepciones: su resultado es el estado de la ejecucion. La
    excepcion es perder la carrera: si otra ejecucion de la misma fuente y version cerro EXITOSA
    primero, esta queda FALLIDA y levanta RuteoYaGenerado con la que gano.
    """
    with sesion() as s:
        # El estado se revisa ya con la fila bloqueada: es el que dejo el ultimo worker que la tuvo.
        ejecucion = s.exec(_bloqueada(ejecucion_ruteo_id)).one()
        if ejecucion.estado != EstadoRuteo.EN_PROCESO:
            log.warning(
                "ejecucion de ruteo %s ya termino %s; no se vuelve a ejecutar",
                ejecucion.ruteo_run_id,
                ejecucion.estado,
            )
            return  # al cerrarse, la sesion revierte y suelta la fila
        # Se leen ahora: despues de un rollback la ejecucion en memoria caduca, y leerla otra vez
        # seria volver a la base justo cuando algo fallo.
        etiqueta, version = ejecucion.ruteo_run_id, ejecucion.version_reglas
        territorial_id = ejecucion.ejecucion_territorial_id
        rutas_evaluadas = paradas_evaluadas = 0
        try:
            fuente, cuentas = _comprobar_fuente(s, ejecucion)
            rutas = _trazar(fuente, cuentas)
            # Solo con todas las rutas calculadas: el nucleo no reporta avances a medias.
            rutas_evaluadas = len(rutas)
            paradas_evaluadas = sum(len(ruta.paradas) for ruta in rutas)
            _comprobar_rutas(fuente, cuentas, rutas)
            ids = _insertar_rutas(s, ejecucion_ruteo_id, fuente, rutas)
            s.execute(
                insert(ParadaRuta),
                [
                    _fila_parada(
                        ejecucion_ruteo_id,
                        ids[territorio.resultado_territorial_id],
                        cuentas[territorio.clave][parada.cliente_unico],
                        parada,
                    )
                    for territorio, ruta in zip(fuente.municipios, rutas, strict=True)
                    for parada in ruta.paradas
                ],
            )
            _cerrar(s, ejecucion, fuente, rutas)
            s.commit()  # el unico commit que publica rutas
        except _NoSePublica as exc:
            s.rollback()
            _fallar(s, ejecucion_ruteo_id, etiqueta, rutas_evaluadas, paradas_evaluadas, str(exc))
        except IntegrityError as exc:
            s.rollback()
            if _restriccion(exc) != INDICE_RUTEO_EXITOSA:
                log.exception("ejecucion de ruteo %s: violacion de integridad inesperada", etiqueta)
                _fallar(
                    s,
                    ejecucion_ruteo_id,
                    etiqueta,
                    rutas_evaluadas,
                    paradas_evaluadas,
                    "Error interno al publicar; ver la bitacora del servicio.",
                )
                return
            _fallar(
                s,
                ejecucion_ruteo_id,
                etiqueta,
                rutas_evaluadas,
                paradas_evaluadas,
                "Otra ejecucion publico las rutas de esta ejecucion territorial con "
                f"{version} mientras esta se procesaba; no se publican dos veces.",
            )
            # El indice la vio al rechazar este cierre: es la que gano.
            raise RuteoYaGenerado(s.exec(_exitosa(territorial_id, version)).one()) from exc
        except Exception as exc:
            s.rollback()
            log.exception("ejecucion de ruteo %s: error inesperado", etiqueta)
            motivo = f"Error interno ({type(exc).__name__}); ver la bitacora."
            _fallar(s, ejecucion_ruteo_id, etiqueta, rutas_evaluadas, paradas_evaluadas, motivo)
        else:
            log.info(
                "ejecucion de ruteo %s: se publicaron %s rutas y %s paradas con %s",
                etiqueta,
                rutas_evaluadas,
                paradas_evaluadas,
                version,
            )


def rutear_territorial(ejecucion_territorial_id: int) -> EjecucionRuteo:
    """Una ejecucion de ruteo completa en primer plano, y como termino.

    Propaga TerritorialNoRuteable y RuteoYaGenerado. Un fallo del motor no se propaga: la ejecucion
    ya existe, y se devuelve FALLIDA. Cada paso usa su propia sesion, como territorializar_decision:
    ninguna queda abierta durante todo el proceso.
    """
    with sesion() as s:
        ejecucion = abrir_ejecucion(s, s.get_one(EjecucionTerritorial, ejecucion_territorial_id))
    ejecutar_ruteo(ejecucion.id)
    with sesion() as s:
        return s.get_one(EjecucionRuteo, ejecucion.id)


def _por_que_no_se_rutea(
    territorial: EjecucionTerritorial, decision: EjecucionDecision
) -> str | None:
    """Por que los municipios de `territorial` no se pueden rutear con ruteo/v1, o None si se
    pueden. Se revisa la cadena completa: la organizacion territorial y las decisiones de las que
    sale. El mensaje es para una persona."""
    if territorial.estado != EstadoTerritorial.EXITOSA:
        return (
            f"La ejecucion territorial {territorial.territorial_run_id} esta {territorial.estado}; "
            "solo se rutean los municipios de una ejecucion territorial EXITOSA."
        )
    if territorial.version_reglas != VERSION_TERRITORIAL_COMPATIBLE:
        return (
            f"La ejecucion territorial {territorial.territorial_run_id} se calculo con "
            f"{territorial.version_reglas}; {VERSION_REGLAS_RUTEO} solo rutea municipios de "
            f"{VERSION_TERRITORIAL_COMPATIBLE}."
        )
    if decision.estado != EstadoDecision.EXITOSA:
        return (
            f"La ejecucion de decision {decision.decision_run_id}, de la que sale la territorial, "
            f"esta {decision.estado}; solo se rutean decisiones de una ejecucion EXITOSA."
        )
    if decision.version_reglas != VERSION_DECISION_COMPATIBLE:
        return (
            f"La ejecucion de decision {decision.decision_run_id}, de la que sale la territorial, "
            f"se decidio con {decision.version_reglas}; {VERSION_REGLAS_RUTEO} solo rutea "
            f"decisiones de {VERSION_DECISION_COMPATIBLE}."
        )
    return None


def _comprobar_fuente(
    s: Session, ejecucion: EjecucionRuteo
) -> tuple[_Fuente, dict[str, dict[str, int]]]:
    """Lo que tiene que seguir siendo cierto para rutear, ya dentro de la transaccion que publica.
    Devuelve los municipios con ruta y, por municipio, cada cliente de campo con su decision.

    Una ejecucion de otra version de las reglas de ruteo no se calcula con estas. Aunque
    abrir_ejecucion ya reviso la fuente, entre las dos transacciones la base pudo cambiar: se vuelve
    a leer toda la cadena, y no se toca. Despues, la fuente tiene que estar completa y cuadrar:

      - la ejecucion territorial tiene publicados los municipios que dice, y al menos uno;
      - cada municipio es coherente: con cuentas de campo y con lugar, o sin ninguna y sin lugar;
      - hay al menos un municipio con cuentas de campo;
      - los municipios suman exactamente las decisiones de campo de la ejecucion de decision, y
        todas son de cuentas de su corrida;
      - cada cuenta de campo es de un municipio con ruta, y cada municipio tiene exactamente las
        cuentas de campo que dice.
    """
    if ejecucion.version_reglas != VERSION_REGLAS_RUTEO:
        raise _NoSePublica(
            f"La ejecucion pide las reglas {ejecucion.version_reglas} y este servicio solo rutea "
            f"con {VERSION_REGLAS_RUTEO}; no se publico ninguna ruta."
        )
    territorial = s.get_one(EjecucionTerritorial, ejecucion.ejecucion_territorial_id)
    decision = s.get_one(EjecucionDecision, territorial.ejecucion_decision_id)
    razon = _por_que_no_se_rutea(territorial, decision)
    if razon is not None:
        raise _NoSePublica(f"{razon} No se publico ninguna ruta.")

    municipios = s.exec(_municipios(territorial.id)).all()
    if not (
        territorial.territorios_evaluados
        == territorial.territorios_publicados
        == len(municipios)
        > 0
    ):
        raise _NoSePublica(
            "La ejecucion territorial esta incompleta: dice que evaluo "
            f"{territorial.territorios_evaluados:,} municipios y publico "
            f"{territorial.territorios_publicados:,}, y tiene {len(municipios):,}; no se publico "
            "ninguna ruta."
        )
    con_ruta = _con_ruta(municipios)
    if not con_ruta:
        raise _NoSePublica(
            "La ejecucion territorial no tiene municipios con cuentas de campo; no se publica un "
            "ruteo vacio."
        )
    suma = sum(territorio.cuentas_campo for territorio in con_ruta)
    campo, de_la_corrida = s.exec(_conteo_de_campo(decision.id, decision.corrida_id)).one()
    if not suma == campo == de_la_corrida:
        raise _NoSePublica(
            f"Los municipios suman {suma:,} cuentas de campo y la ejecucion de decision tiene "
            f"{campo:,} decisiones de campo, {de_la_corrida:,} de cuentas de su corrida; no se "
            "publico ninguna ruta."
        )
    cuentas = _repartir(con_ruta, s.exec(_cuentas_de_campo(decision.id, decision.corrida_id)).all())
    return _Fuente(con_ruta, campo), cuentas


def _con_ruta(municipios: Sequence[Row[Any]]) -> tuple[_Territorio, ...]:
    """Los municipios publicados que tienen ruta, en el orden en que llegan, despues de revisar que
    cada uno sea coherente: con cuentas de campo y con lugar, o sin ninguna y sin lugar. Uno sin
    cuentas de campo no tiene ruta, y no debe tener lugar; uno con cuentas de campo tiene ruta, y
    debe tenerlo."""
    con_ruta = []
    for municipio in municipios:
        clave = municipio.cve_entidad + municipio.cve_municipio
        con_campo, con_lugar = municipio.cuentas_campo > 0, municipio.posicion_campo is not None
        if municipio.cuentas_campo < 0 or con_campo != con_lugar:
            lugar = f"el lugar {municipio.posicion_campo}" if con_lugar else "ningun lugar"
            raise _NoSePublica(
                f"El municipio {clave} de la ejecucion territorial no es coherente: tiene "
                f"{municipio.cuentas_campo:,} cuentas de campo y {lugar}; no se publico ninguna "
                "ruta."
            )
        if con_campo:
            con_ruta.append(
                _Territorio(municipio.id, clave, municipio.cuentas_campo, municipio.posicion_campo)
            )
    return tuple(con_ruta)


def _repartir(
    municipios: Sequence[_Territorio], cuentas: Sequence[Row[Any]]
) -> dict[str, dict[str, int]]:
    """Las cuentas de campo de cada municipio con ruta: por clave, cada cliente_unico con el id de
    su DecisionCuenta. Es el mapa con que cada parada del nucleo vuelve a su decision, y tiene que
    ser uno a uno: un cliente repetido, una cuenta de un municipio sin ruta o un municipio con otro
    numero de cuentas que el publicado detienen todo."""
    por_clave: dict[str, dict[str, int]] = {territorio.clave: {} for territorio in municipios}
    for cuenta in cuentas:
        clave = cuenta.cve_entidad + cuenta.cve_municipio
        if clave not in por_clave:
            raise _NoSePublica(
                f"Hay cuentas de campo en el municipio {clave}, que no tiene ruta en la ejecucion "
                "territorial; no se publico ninguna ruta."
            )
        clientes = por_clave[clave]
        if cuenta.cliente_unico in clientes:
            raise _NoSePublica(
                f"Un cliente se repite entre las cuentas de campo del municipio {clave}; no se "
                "publico ninguna ruta."
            )
        clientes[cuenta.cliente_unico] = cuenta.decision_cuenta_id
    for territorio in municipios:
        if len(por_clave[territorio.clave]) != territorio.cuentas_campo:
            raise _NoSePublica(
                f"El municipio {territorio.clave} tiene {territorio.cuentas_campo:,} cuentas de "
                "campo en la ejecucion territorial y "
                f"{len(por_clave[territorio.clave]):,} en la de decision; no se publico ninguna "
                "ruta."
            )
    return por_clave


def _trazar(fuente: _Fuente, cuentas: dict[str, dict[str, int]]) -> list[ResultadoRuta]:
    """La ruta de cada municipio con trabajo de campo, en el orden de posicion_campo: una llamada al
    nucleo por municipio, con sus clientes de campo. El servicio no calcula coordenadas, distancias
    ni recorridos: eso es de `reglas.py`. Si un municipio no cumple ruteo/v1, no se publica nada."""
    try:
        return [
            rutear_territorio(territorio.clave, list(cuentas[territorio.clave]))
            for territorio in fuente.municipios
        ]
    except (TypeError, ValueError) as exc:
        log.warning(
            "las cuentas de campo de un municipio no cumplen %s: %s", VERSION_REGLAS_RUTEO, exc
        )
        raise _NoSePublica(
            f"Las cuentas de campo de un municipio no cumplen {VERSION_REGLAS_RUTEO}; ver la "
            "bitacora. No se publico ninguna ruta."
        ) from exc


def _comprobar_rutas(
    fuente: _Fuente, cuentas: dict[str, dict[str, int]], rutas: Sequence[ResultadoRuta]
) -> None:
    """Que cada ruta cuadre antes de publicarla, sin volver a calcularla: es de su municipio, tiene
    exactamente sus cuentas de campo, una vez cada una y en secuencia desde 1, la distancia final no
    pasa de la inicial, la mejora es la diferencia y los tramos mas el regreso suman la distancia
    total."""
    for territorio, ruta in zip(fuente.municipios, rutas, strict=True):
        clientes = [parada.cliente_unico for parada in ruta.paradas]
        tramos = sum(parada.distancia_desde_anterior_m for parada in ruta.paradas)
        problemas = [
            (ruta.clave_territorio != territorio.clave, "es de otro municipio"),
            (not ruta.paradas, "no tiene paradas"),
            (
                len(clientes) != len(set(clientes))
                or set(clientes) != set(cuentas[territorio.clave]),
                "sus paradas no son sus cuentas de campo, una vez cada una",
            ),
            (
                [parada.secuencia for parada in ruta.paradas] != list(range(1, len(clientes) + 1)),
                "su secuencia no va de 1 en 1 desde 1",
            ),
            (
                ruta.distancia_total_m > ruta.distancia_inicial_m,
                "la distancia final pasa de la inicial",
            ),
            (
                ruta.mejora_2opt_m != ruta.distancia_inicial_m - ruta.distancia_total_m,
                "la mejora no es la distancia inicial menos la final",
            ),
            (
                tramos + ruta.distancia_regreso_deposito_m != ruta.distancia_total_m,
                "sus tramos y el regreso no suman la distancia total",
            ),
        ]
        for hay_problema, problema in problemas:
            if hay_problema:
                raise _NoSePublica(
                    f"La ruta del municipio {territorio.clave} no cuadra: {problema}; no se "
                    "publico ninguna ruta."
                )


def _insertar_rutas(
    s: Session, ejecucion_id: int, fuente: _Fuente, rutas: Sequence[ResultadoRuta]
) -> dict[int, int]:
    """Inserta todas las rutas en bloque y devuelve, por ResultadoTerritorial, el id de su ruta. El
    RETURNING trae los dos ids juntos: asi el mapa no depende del orden en que la base los devuelve.
    """
    filas = s.execute(
        insert(RutaTerritorial).returning(
            RutaTerritorial.resultado_territorial_id, RutaTerritorial.id
        ),
        [
            _fila_ruta(ejecucion_id, territorio, ruta)
            for territorio, ruta in zip(fuente.municipios, rutas, strict=True)
        ],
    ).all()
    return {resultado_territorial_id: ruta_id for resultado_territorial_id, ruta_id in filas}


def _cerrar(
    s: Session, ejecucion: EjecucionRuteo, fuente: _Fuente, rutas: Sequence[ResultadoRuta]
) -> None:
    """Deja la ejecucion EXITOSA si cada ruta y cada parada se calculo y quedo guardada una vez.

    Cuenta en la misma transaccion que las inserto: los municipios con campo de la fuente, las rutas
    calculadas y las guardadas tienen que ser el mismo numero, mayor que cero; y las decisiones de
    campo, las paradas calculadas y las guardadas, otro, tambien mayor que cero. Envia el cambio de
    estado pero no confirma, para que una carrera con otra ejecucion EXITOSA se descubra todavia
    dentro de la transaccion; el commit es de quien llama.
    """
    rutas_guardadas, paradas_guardadas = s.exec(_conteo_publicado(ejecucion.id)).one()
    calculadas = sum(len(ruta.paradas) for ruta in rutas)
    if not (len(fuente.municipios) == len(rutas) == rutas_guardadas > 0):
        raise _NoSePublica(
            f"Las rutas estan incompletas: hay {len(fuente.municipios):,} municipios con campo, "
            f"se calcularon {len(rutas):,} rutas y se guardaron {rutas_guardadas:,}; no se publico "
            "ninguna."
        )
    if not (fuente.campo == calculadas == paradas_guardadas > 0):
        raise _NoSePublica(
            f"Las paradas estan incompletas: hay {fuente.campo:,} decisiones de campo, se "
            f"calcularon {calculadas:,} paradas y se guardaron {paradas_guardadas:,}; no se "
            "publico ninguna."
        )

    ejecucion.estado = EstadoRuteo.EXITOSA
    ejecucion.rutas_evaluadas, ejecucion.rutas_publicadas = len(rutas), rutas_guardadas
    ejecucion.paradas_evaluadas, ejecucion.paradas_publicadas = calculadas, paradas_guardadas
    ejecucion.detalle = (
        f"Se rutearon {paradas_guardadas:,} cuentas de campo en {rutas_guardadas:,} municipios con "
        f"{ejecucion.version_reglas}."
    )
    ejecucion.terminada_en = ahora()
    s.add(ejecucion)
    s.flush()


def _fallar(
    s: Session,
    ejecucion_id: int,
    etiqueta: UUID,
    rutas_evaluadas: int,
    paradas_evaluadas: int,
    motivo: str,
) -> None:
    """Deja la ejecucion FALLIDA, solo si sigue EN_PROCESO.

    Es un UPDATE condicionado al estado que tiene la base, y no la ejecucion que se tenia en
    memoria: entre el rollback y este registro, otro worker pudo tomar la misma ejecucion y dejarla
    EXITOSA, y un fallo que llega tarde no la degrada. Si ya termino, se deja como esta y el fallo
    queda solo en la bitacora. No borra rutas ni paradas: las de este intento ya las quito el
    rollback, y las de una ejecucion EXITOSA son suyas.
    """
    registrado = s.execute(
        update(EjecucionRuteo)
        .where(
            EjecucionRuteo.id == ejecucion_id,
            EjecucionRuteo.estado == EstadoRuteo.EN_PROCESO,
        )
        .values(
            estado=EstadoRuteo.FALLIDA,
            rutas_evaluadas=rutas_evaluadas,
            rutas_publicadas=0,
            paradas_evaluadas=paradas_evaluadas,
            paradas_publicadas=0,
            detalle=motivo,
            terminada_en=ahora(),
        )
        .execution_options(synchronize_session=False)
    ).rowcount
    s.commit()
    if registrado:
        log.warning("ejecucion de ruteo %s fallida: %s", etiqueta, motivo)
        return
    estado = s.exec(select(EjecucionRuteo.estado).where(EjecucionRuteo.id == ejecucion_id)).one()
    log.warning(
        "ejecucion de ruteo %s ya termino %s; este fallo no la cambia: %s", etiqueta, estado, motivo
    )


def _municipios(ejecucion_territorial_id: int) -> Select[Any]:
    """Los municipios que publico la ejecucion territorial, con lo que el ruteo necesita de cada
    uno, en el orden de territorial/v1: primero por lugar de campo, y los que no tienen, al
    final."""
    return (
        select(
            ResultadoTerritorial.id,
            ResultadoTerritorial.cve_entidad,
            ResultadoTerritorial.cve_municipio,
            ResultadoTerritorial.cuentas_campo,
            ResultadoTerritorial.posicion_campo,
        )
        .where(ResultadoTerritorial.ejecucion_territorial_id == ejecucion_territorial_id)
        .order_by(
            ResultadoTerritorial.posicion_campo.asc().nulls_last(),
            ResultadoTerritorial.cve_entidad,
            ResultadoTerritorial.cve_municipio,
        )
    )


def _conteo_de_campo(ejecucion_decision_id: int, corrida_id: int) -> Select[Any]:
    """Cuantas decisiones de campo tiene la ejecucion de decision, y cuantas de ellas son de cuentas
    de su corrida. Lo que cuenta como campo es la decision, y nunca Cuenta.canal."""
    return (
        select(func.count(), func.count().filter(Cuenta.corrida_id == corrida_id))
        .select_from(DecisionCuenta)
        .join(Cuenta, DecisionCuenta.cuenta_id == Cuenta.id)
        .where(
            DecisionCuenta.ejecucion_decision_id == ejecucion_decision_id,
            DecisionCuenta.canal_recomendado == CANAL_CAMPO,
        )
    )


def _cuentas_de_campo(ejecucion_decision_id: int, corrida_id: int) -> Select[Any]:
    """Las cuentas que seran paradas, en una sola consulta: cada decision de campo de la ejecucion,
    sobre una cuenta de su corrida, con su cliente y su municipio. Nunca una consulta por cuenta. El
    orden solo hace la lectura repetible: el de visita lo decide ruteo/v1."""
    return (
        select(
            DecisionCuenta.id.label("decision_cuenta_id"),
            Cuenta.cliente_unico,
            Cuenta.cve_entidad,
            Cuenta.cve_municipio,
        )
        .select_from(DecisionCuenta)
        .join(Cuenta, DecisionCuenta.cuenta_id == Cuenta.id)
        .where(
            DecisionCuenta.ejecucion_decision_id == ejecucion_decision_id,
            Cuenta.corrida_id == corrida_id,
            DecisionCuenta.canal_recomendado == CANAL_CAMPO,
        )
        .order_by(Cuenta.cve_entidad, Cuenta.cve_municipio, Cuenta.cliente_unico)
    )


def _conteo_publicado(ejecucion_ruteo_id: int) -> Select[Any]:
    """Cuantas rutas y cuantas paradas tiene guardadas la ejecucion, en una sola consulta."""
    rutas = (
        select(func.count())
        .select_from(RutaTerritorial)
        .where(RutaTerritorial.ejecucion_ruteo_id == ejecucion_ruteo_id)
        .scalar_subquery()
    )
    paradas = (
        select(func.count())
        .select_from(ParadaRuta)
        .where(ParadaRuta.ejecucion_ruteo_id == ejecucion_ruteo_id)
        .scalar_subquery()
    )
    return select(rutas, paradas)


def _fila_ruta(ejecucion_id: int, territorio: _Territorio, ruta: ResultadoRuta) -> dict[str, Any]:
    return {
        "ejecucion_ruteo_id": ejecucion_id,
        "resultado_territorial_id": territorio.resultado_territorial_id,
        "paradas": len(ruta.paradas),
        "distancia_inicial_m": ruta.distancia_inicial_m,
        "distancia_total_m": ruta.distancia_total_m,
        "distancia_regreso_deposito_m": ruta.distancia_regreso_deposito_m,
        "mejora_2opt_m": ruta.mejora_2opt_m,
    }


def _fila_parada(
    ejecucion_id: int, ruta_id: int, decision_cuenta_id: int, parada: ParadaCalculada
) -> dict[str, Any]:
    return {
        "ejecucion_ruteo_id": ejecucion_id,
        "ruta_territorial_id": ruta_id,
        "decision_cuenta_id": decision_cuenta_id,
        "secuencia": parada.secuencia,
        "x_m": parada.x_m,
        "y_m": parada.y_m,
        "distancia_desde_anterior_m": parada.distancia_desde_anterior_m,
    }


def _bloqueada(ejecucion_id: int) -> SelectOfScalar[EjecucionRuteo]:
    """La ejecucion, con FOR UPDATE: bloquea solo su fila, hasta el commit o el rollback de la
    transaccion que la toma. Otra transaccion que la pida igual espera a que se suelte, y entonces
    la lee como quedo."""
    return select(EjecucionRuteo).where(EjecucionRuteo.id == ejecucion_id).with_for_update()


def _exitosa(ejecucion_territorial_id: int, version: str) -> SelectOfScalar[EjecucionRuteo]:
    """La ejecucion EXITOSA de esa ejecucion territorial con esa version de las reglas. Hay a lo mas
    una: lo garantiza el indice unico parcial."""
    return select(EjecucionRuteo).where(
        EjecucionRuteo.ejecucion_territorial_id == ejecucion_territorial_id,
        EjecucionRuteo.version_reglas == version,
        EjecucionRuteo.estado == EstadoRuteo.EXITOSA,
    )


def _restriccion(exc: IntegrityError) -> str | None:
    diagnostico = getattr(exc.orig, "diag", None)
    return getattr(diagnostico, "constraint_name", None)
