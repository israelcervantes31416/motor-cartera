"""Un plan de campo/v2: que candidata de campo queda con que gestor en una fecha, en PostgreSQL y
todo o nada.

El orden:

1. `abrir` registra el plan EN_PROCESO con su trabajo CAMPO_V2, en la transaccion de quien llama,
   despues de fijar exactamente sus entradas y comprobar que son coherentes: el corte, una
   decision/v2 EXITOSA de ese corte, una territorial/v2 EXITOSA de ese corte, una configuracion de
   campo sobre la misma version de bases que esa territorial/v2, la fecha de operacion y la
   politica con su hash. Con ellas sale su `firma_inputs`. Nunca toma la decision, la asignacion ni
   los gestores mas recientes: se declaran.
2. Un worker toma el trabajo. `planificar` toma el plan con su fila bloqueada y, en una transaccion
   y por conjuntos:
   - selecciona las decisiones de canal CAMPO por su indice, sin recorrer las demas;
   - les une su asignacion territorial y, a las ASIGNADA, su punto (el de su geografia publicada);
   - lleva cada punto a EPSG:6372 y calcula su celda de microtramo (PostGIS);
   - agrupa las candidatas en bloques (base, prioridad, microtramo) y, en Python, da a cada unidad
     su identificador y reparte los bloques con campo-public-v2: son ordenes de magnitud menos que
     las cuentas, y vuelven a la base por COPY;
   - publica con INSERT ... SELECT la asignacion de cada candidata, las unidades que ocupo y la
     carga de cada gestor, las cuenta y cierra EXITOSA.
   Si algo falla, se revierte todo y el plan queda FALLIDA: nunca un plan a medias.

No usa distancias, tiempos ni rutas: la geografia metrica solo sirve para la particion.
"""

from __future__ import annotations

import logging
import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session, select

from motor_cartera.campo_v2.politica import (
    CANAL_CAMPO,
    CONTEO_DE_ESTADO,
    DISPONIBLES,
    POLITICAS,
    PUBLIC_V2,
    VERSION_MOTOR,
    Bloque,
    EstadoCampo,
    EstadoJornada,
    NivelUnidad,
    PoliticaCampo,
    firma_inputs,
    hash_politica,
    particion,
    repartir,
)
from motor_cartera.db.copia import Jsonb, copiar
from motor_cartera.db.modelos import (
    CONTEOS_DE_CAMPO_V2,
    CorteCanonico,
    EjecucionCampoV2,
    EjecucionDecisionV2,
    EjecucionTerritorialV2,
    EstadoCampoV2,
    EstadoDecisionV2,
    EstadoTerritorialV2,
    ResultadoEjecucionCampoV2,
    TipoTrabajo,
    VersionConfiguracionCampo,
    ahora,
)
from motor_cartera.db.sesion import insertar_en_savepoint, restriccion, sesion
from motor_cartera.geografia.ejecuciones import _traza
from motor_cartera.ingesta.fuente_oficial import Cronometro
from motor_cartera.orquestacion import cola

log = logging.getLogger(__name__)

INDICE_EXITOSA = "ux_campo_v2_exitosa"
INDICE_EN_PROCESO = "ux_campo_v2_en_proceso"


# --- errores al abrir -----------------------------------------------------------------------------


class EntradaNoPlanificable(Exception):
    """Una peticion de campo/v2 que no se puede abrir: no registra nada. `codigo` es estable."""

    codigo = "ENTRADA_NO_PLANIFICABLE"
    estado_http = 422


class CorteNoEncontrado(EntradaNoPlanificable):
    codigo = "CORTE_NO_ENCONTRADO"
    estado_http = 404


class DecisionNoEncontrada(EntradaNoPlanificable):
    codigo = "DECISION_NO_ENCONTRADA"
    estado_http = 404


class DecisionNoExitosa(EntradaNoPlanificable):
    codigo = "DECISION_NO_EXITOSA"


class TerritorialNoEncontrado(EntradaNoPlanificable):
    codigo = "TERRITORIAL_NO_ENCONTRADO"
    estado_http = 404


class TerritorialNoExitosa(EntradaNoPlanificable):
    codigo = "TERRITORIAL_NO_EXITOSA"


class CorteInconsistente(EntradaNoPlanificable):
    codigo = "CORTE_INCONSISTENTE"


class ConfiguracionNoEncontrada(EntradaNoPlanificable):
    codigo = "CONFIGURACION_NO_ENCONTRADA"
    estado_http = 404


class BasesInconsistentes(EntradaNoPlanificable):
    codigo = "BASES_INCONSISTENTES"


class FechaAnteriorAlCorte(EntradaNoPlanificable):
    codigo = "FECHA_ANTERIOR_AL_CORTE"


class PoliticaNoSoportada(EntradaNoPlanificable):
    codigo = "POLITICA_NO_SOPORTADA"


class CampoV2YaGenerado(Exception):
    """Otro plan ya publico exactamente las mismas entradas (la misma firma)."""

    def __init__(self, previo: EjecucionCampoV2) -> None:
        super().__init__(
            f"Esas entradas ya se planearon: el plan {previo.campo_run_id} (firma "
            f"{previo.firma_inputs[:12]}...)."
        )
        self.previo = previo


class CampoV2EnProceso(Exception):
    """Esas mismas entradas ya se estan planeando: hay un plan EN_PROCESO."""

    def __init__(self, activo: EjecucionCampoV2) -> None:
        super().__init__(f"Esas entradas ya se estan planeando: el plan {activo.campo_run_id}.")
        self.activo = activo


class _NoSePublica(Exception):
    def __init__(self, resultado: ResultadoEjecucionCampoV2, mensaje: str) -> None:
        super().__init__(mensaje)
        self.resultado = resultado


# --- abrir ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Entradas:
    corte: CorteCanonico
    decision: EjecucionDecisionV2
    territorial: EjecucionTerritorialV2
    configuracion: VersionConfiguracionCampo
    fecha_operacion: date
    politica: PoliticaCampo
    firma: str


def _una(s: Session, modelo, columna, valor):
    return s.exec(select(modelo).where(columna == valor)).one_or_none()


def entradas(
    s: Session,
    corte_id: UUID,
    decision_run_id: UUID,
    territorial_run_id: UUID,
    configuracion_campo_version_id: UUID,
    fecha_operacion: date,
    version_politica: str | None = None,
) -> Entradas:
    """Las entradas de un plan, comprobadas y coherentes, con su firma. Levanta, sin registrar nada,
    CorteNoEncontrado, DecisionNoEncontrada, DecisionNoExitosa, TerritorialNoEncontrado,
    TerritorialNoExitosa, CorteInconsistente, ConfiguracionNoEncontrada, BasesInconsistentes,
    FechaAnteriorAlCorte o PoliticaNoSoportada."""
    corte = _una(s, CorteCanonico, CorteCanonico.corte_id, corte_id)
    if corte is None:
        raise CorteNoEncontrado(f"No existe un corte canonico con corte_id {corte_id}.")
    decision = _una(s, EjecucionDecisionV2, EjecucionDecisionV2.decision_run_id, decision_run_id)
    if decision is None:
        raise DecisionNoEncontrada(
            f"No existe una ejecucion de decision/v2 con decision_run_id {decision_run_id}."
        )
    if decision.estado != EstadoDecisionV2.EXITOSA:
        raise DecisionNoExitosa(
            f"La decision {decision_run_id} esta {decision.estado}: campo/v2 solo planea sobre "
            "una decision EXITOSA, nunca sobre una parcial."
        )
    territorial = _una(
        s, EjecucionTerritorialV2, EjecucionTerritorialV2.territorial_run_id, territorial_run_id
    )
    if territorial is None:
        raise TerritorialNoEncontrado(
            f"No existe una ejecucion de territorial/v2 con territorial_run_id "
            f"{territorial_run_id}."
        )
    if territorial.estado != EstadoTerritorialV2.EXITOSA:
        raise TerritorialNoExitosa(
            f"La asignacion territorial {territorial_run_id} esta {territorial.estado}: campo/v2 "
            "solo planea sobre una territorial/v2 EXITOSA."
        )
    for nombre, ejecucion in (("decision", decision), ("territorial/v2", territorial)):
        if ejecucion.corte_canonico_id != corte.id:
            raise CorteInconsistente(
                f"La {nombre} declarada es de otro corte, no del {corte.fecha_corte}: la decision, "
                "territorial/v2 y el plan son del mismo corte."
            )
    configuracion = _una(
        s,
        VersionConfiguracionCampo,
        VersionConfiguracionCampo.configuracion_campo_version_id,
        configuracion_campo_version_id,
    )
    if configuracion is None:
        raise ConfiguracionNoEncontrada(
            f"No existe una configuracion de campo con configuracion_campo_version_id "
            f"{configuracion_campo_version_id}."
        )
    if configuracion.version_bases_operativas_id != territorial.version_bases_operativas_id:
        raise BasesInconsistentes(
            "La configuracion de campo esta construida sobre otra version de bases que la de "
            "territorial/v2: sus gestores no son de las bases a las que se asignaron las cuentas."
        )
    if fecha_operacion < corte.fecha_corte:
        raise FechaAnteriorAlCorte(
            f"La fecha de operacion {fecha_operacion} es anterior al corte del "
            f"{corte.fecha_corte}: un plan no usa lo que todavia no se sabia ese dia."
        )
    version_politica = version_politica or PUBLIC_V2.version
    politica = POLITICAS.get(version_politica)
    if politica is None:
        raise PoliticaNoSoportada(
            f"Este servicio planea con {', '.join(sorted(POLITICAS))}, no con {version_politica}."
        )
    firma = firma_inputs(
        corte_id=corte.corte_id,
        decision_run_id=decision.decision_run_id,
        firma_decision=decision.firma_inputs,
        territorial_run_id=territorial.territorial_run_id,
        firma_territorial=territorial.firma_inputs,
        configuracion_campo_version_id=configuracion.configuracion_campo_version_id,
        hash_configuracion=configuracion.hash_configuracion,
        fecha_operacion=fecha_operacion,
        politica=politica,
    )
    return Entradas(corte, decision, territorial, configuracion, fecha_operacion, politica, firma)


def abrir(
    s: Session,
    corte_id: UUID,
    decision_run_id: UUID,
    territorial_run_id: UUID,
    configuracion_campo_version_id: UUID,
    fecha_operacion: date,
    *,
    version_politica: str | None = None,
    max_intentos: int,
    tomado_por: str | None = None,
    lease_segundos: float | None = None,
) -> EjecucionCampoV2:
    """El plan EN_PROCESO de campo/v2, con su trabajo CAMPO_V2, en la transaccion de quien llama:
    envia los INSERT y no confirma. Levanta, sin registrar nada, lo que levanta `entradas`,
    CampoV2YaGenerado y CampoV2EnProceso."""
    e = entradas(
        s,
        corte_id,
        decision_run_id,
        territorial_run_id,
        configuracion_campo_version_id,
        fecha_operacion,
        version_politica,
    )
    previo = _de_firma(s, e.firma, EstadoCampoV2.EXITOSA)
    if previo is not None:
        raise CampoV2YaGenerado(previo)
    activo = _de_firma(s, e.firma, EstadoCampoV2.EN_PROCESO)
    if activo is not None:
        raise CampoV2EnProceso(activo)
    ejecucion = EjecucionCampoV2(
        corte_canonico_id=e.corte.id,
        ejecucion_decision_v2_id=e.decision.id,
        ejecucion_territorial_v2_id=e.territorial.id,
        version_bases_operativas_id=e.territorial.version_bases_operativas_id,
        version_configuracion_campo_id=e.configuracion.id,
        fecha_operacion=e.fecha_operacion,
        version_motor=VERSION_MOTOR,
        version_politica=e.politica.version,
        hash_politica=hash_politica(e.politica),
        firma_inputs=e.firma,
        cuentas_evaluadas_decision=e.decision.cuentas_decididas,
    )
    error = insertar_en_savepoint(s, ejecucion)
    if error is not None:
        if restriccion(error) != INDICE_EN_PROCESO:
            raise error
        activo = _de_firma(s, e.firma, EstadoCampoV2.EN_PROCESO)
        if (
            activo is None
        ):  # pragma: no cover - el que gano ya termino entre el rechazo y la revision
            raise error
        raise CampoV2EnProceso(activo) from error
    cola.crear(
        s,
        TipoTrabajo.CAMPO_V2,
        ejecucion.id,
        max_intentos=max_intentos,
        tomado_por=tomado_por,
        lease_segundos=lease_segundos,
    )
    log.info(
        "campo v2 %s abierto: corte %s, decision %s, territorial %s, configuracion %s, fecha %s",
        ejecucion.campo_run_id,
        e.corte.corte_id,
        e.decision.decision_run_id,
        e.territorial.territorial_run_id,
        e.configuracion.configuracion_campo_version_id,
        e.fecha_operacion,
    )
    return ejecucion


def _de_firma(s: Session, firma: str, estado: EstadoCampoV2) -> EjecucionCampoV2 | None:
    consulta = select(EjecucionCampoV2).where(
        EjecucionCampoV2.firma_inputs == firma, EjecucionCampoV2.estado == estado
    )
    if estado == EstadoCampoV2.EN_PROCESO:
        consulta = consulta.with_for_update()
    return s.exec(consulta).one_or_none()


# --- planificar -----------------------------------------------------------------------------------

ANTES_DEL_STAGING = "ANTES_DEL_STAGING"
DESPUES_DE_CANDIDATAS = "DESPUES_DE_CANDIDATAS"
DESPUES_DE_PARTICION = "DESPUES_DE_PARTICION"
DESPUES_DE_CAPACIDAD = "DESPUES_DE_CAPACIDAD"
DESPUES_DE_ASIGNACIONES = "DESPUES_DE_ASIGNACIONES"
ANTES_DE_CERRAR = "ANTES_DE_CERRAR"
PUNTOS_DE_FALLA = (
    ANTES_DEL_STAGING,
    DESPUES_DE_CANDIDATAS,
    DESPUES_DE_PARTICION,
    DESPUES_DE_CAPACIDAD,
    DESPUES_DE_ASIGNACIONES,
    ANTES_DE_CERRAR,
)


def _punto_de_falla(nombre: str) -> None:
    """Un punto donde una prueba puede inyectar una falla. En produccion no hace nada."""


def ejecutar_campo_v2(ejecucion_id: int) -> None:
    """Lo que ejecuta un trabajo CAMPO_V2. Si el plan ya termino no hace nada."""
    with sesion() as s:
        ejecucion = s.get_one(EjecucionCampoV2, ejecucion_id)
        if ejecucion.estado != EstadoCampoV2.EN_PROCESO:
            log.info("campo v2 %s ya termino %s", ejecucion.campo_run_id, ejecucion.estado)
            return
    planificar(ejecucion_id)


def planificar(ejecucion_id: int, *, cronometro: Cronometro | None = None) -> None:
    """Planea las candidatas de campo del plan y lo deja en un estado terminal: con su fila
    bloqueada hasta el commit, todo en una transaccion y en un savepoint; si falla, queda FALLIDA, y
    solo publica o falla el dueno vigente de su trabajo. Levanta CampoV2YaGenerado si otro plan
    publico primero la misma firma, TrabajoAjeno, y los errores transitorios de la base, con el plan
    EN_PROCESO para que la cola lo reintente."""
    cronometro = cronometro or Cronometro()
    with sesion() as s:
        ejecucion = s.exec(
            select(EjecucionCampoV2).where(EjecucionCampoV2.id == ejecucion_id).with_for_update()
        ).one()
        if ejecucion.estado != EstadoCampoV2.EN_PROCESO:
            log.warning("campo v2 %s ya termino %s", ejecucion.campo_run_id, ejecucion.estado)
            return
        etiqueta, firma = ejecucion.campo_run_id, ejecucion.firma_inputs
        try:
            with s.begin_nested():
                _planificar(s, ejecucion, cronometro)
        except _NoSePublica as exc:
            resultado, motivo = exc.resultado, str(exc)
        except IntegrityError as exc:
            if restriccion(exc) == INDICE_EXITOSA:
                resultado, motivo = (
                    ResultadoEjecucionCampoV2.YA_GENERADO,
                    "Otro plan publico la misma firma de entrada mientras este se procesaba; no se "
                    "publica dos veces.",
                )
            else:
                log.error(
                    "campo v2 %s: violacion de integridad inesperada%s", etiqueta, _traza(exc)
                )
                resultado, motivo = (
                    ResultadoEjecucionCampoV2.ERROR_INTERNO,
                    "Error interno al publicar; ver la bitacora del servicio.",
                )
        except OperationalError:
            log.warning("campo v2 %s: error transitorio; sigue EN_PROCESO", etiqueta)
            raise
        except Exception as exc:
            # Sin el mensaje: el de un error de la base puede citar una fila, y las tablas de paso
            # tienen el punto de cada cuenta. El tipo, el SQLSTATE y los marcos bastan.
            log.error("campo v2 %s: error inesperado%s", etiqueta, _traza(exc))
            resultado, motivo = (
                ResultadoEjecucionCampoV2.ERROR_INTERNO,
                f"Error interno ({type(exc).__name__}); ver la bitacora.",
            )
        else:
            cola.confirmar_dueno(s)
            with cronometro.fase("commit"):
                s.commit()
            return
        _fallar(s, ejecucion_id, etiqueta, resultado, motivo)
        if resultado == ResultadoEjecucionCampoV2.YA_GENERADO:
            raise CampoV2YaGenerado(_de_firma(s, firma, EstadoCampoV2.EXITOSA))


def _planificar(s: Session, ejecucion: EjecucionCampoV2, cronometro: Cronometro) -> None:
    politica = POLITICAS.get(ejecucion.version_politica)
    if (
        ejecucion.version_motor != VERSION_MOTOR
        or politica is None
        or ejecucion.hash_politica != hash_politica(politica)
    ):
        raise _NoSePublica(
            ResultadoEjecucionCampoV2.VERSION_NO_SOPORTADA,
            f"El plan pide {ejecucion.version_motor} con {ejecucion.version_politica} (hash "
            f"{ejecucion.hash_politica[:12]}...), y este servicio planea con {VERSION_MOTOR} y "
            f"{', '.join(sorted(POLITICAS))}; no se publico nada.",
        )
    decision = s.get_one(EjecucionDecisionV2, ejecucion.ejecucion_decision_v2_id)
    territorial = s.get_one(EjecucionTerritorialV2, ejecucion.ejecucion_territorial_v2_id)
    if (
        decision.estado != EstadoDecisionV2.EXITOSA
        or territorial.estado != EstadoTerritorialV2.EXITOSA
    ):
        raise _NoSePublica(
            ResultadoEjecucionCampoV2.ENTRADAS_NO_EXITOSAS,
            f"La decision esta {decision.estado} y territorial/v2 {territorial.estado}; no se "
            "publico nada.",
        )
    configuracion = s.get_one(VersionConfiguracionCampo, ejecucion.version_configuracion_campo_id)
    _punto_de_falla(ANTES_DEL_STAGING)
    parametros: dict[str, Any] = {
        "ejecucion": ejecucion.id,
        "decision": ejecucion.ejecucion_decision_v2_id,
        "territorial": ejecucion.ejecucion_territorial_v2_id,
        "configuracion": ejecucion.version_configuracion_campo_id,
        "bases": ejecucion.version_bases_operativas_id,
        "fecha": ejecucion.fecha_operacion,
        "dia": ejecucion.fecha_operacion.isoweekday(),
        "zona": configuracion.zona_horaria,
        "canal": CANAL_CAMPO,
        "srid": politica.srid_metrico,
        "microtramo": politica.tamano_microtramo_m,
    }
    with cronometro.fase("seleccion"):
        _ejecutar(s, SQL_DECISION, parametros)
        candidatas = _contar_tabla(s, "cv2_decision")
    with cronometro.fase("territorial"):
        _ejecutar(s, SQL_CANDIDATAS, parametros)
        sin_territorial, sin_ubicacion = s.execute(text(SQL_REVISAR_CANDIDATAS)).one()
    if sin_territorial or sin_ubicacion:
        raise _NoSePublica(
            ResultadoEjecucionCampoV2.DATOS_INCONSISTENTES,
            f"{sin_territorial:,} candidatas no tienen asignacion en territorial/v2 y "
            f"{sin_ubicacion:,} asignadas no tienen base, municipio o punto; no se publico nada.",
        )
    _punto_de_falla(DESPUES_DE_CANDIDATAS)
    with cronometro.fase("particion"):
        _ejecutar(s, SQL_CELDAS, parametros)
    with cronometro.fase("bloques"):
        bloques = s.execute(text(SQL_BLOQUES)).all()
        unidades = _unidades(bloques, politica)
        copiar(
            s,
            "cv2_unidad",
            ("base", "cvegeo", "ix", "iy", "zona_id", "subzona_id", "tramo_id", "microtramo_id"),
            (
                (
                    base,
                    cvegeo,
                    ix,
                    iy,
                    *map(str, (p.zona_id, p.subzona_id, p.tramo_id, p.microtramo_id)),
                )
                for (base, cvegeo, ix, iy), p in unidades.items()
            ),
        )
        s.execute(text("ANALYZE cv2_unidad"))
    _punto_de_falla(DESPUES_DE_PARTICION)
    with cronometro.fase("capacidad"):
        _ejecutar(s, SQL_GESTORES, parametros)
        gestores = s.execute(
            text("SELECT gestor, codigo, base, capacidad, estado_jornada FROM cv2_gestor")
        ).all()
        partes, propietarios = _repartir(bloques, unidades, gestores)
        copiar(
            s,
            "cv2_parte",
            (
                "base",
                "prioridad",
                "microtramo_id",
                "desde",
                "hasta",
                "inicio",
                "estado",
                "gestor",
                "rebalanceada",
                "motivo",
            ),
            partes,
        )
        copiar(s, "cv2_propietario", ("base", "microtramo_id", "gestor"), propietarios)
        s.execute(text("ANALYZE cv2_parte"))
    _punto_de_falla(DESPUES_DE_CAPACIDAD)
    with cronometro.fase("publicacion"):
        _ejecutar(s, SQL_RESULTADO, parametros)
        publicadas = s.execute(text(SQL_ASIGNACIONES), parametros).rowcount
        _ejecutar(s, SQL_UNIDADES, parametros)
        s.execute(text(SQL_CARGAS), parametros)
    _punto_de_falla(DESPUES_DE_ASIGNACIONES)
    with cronometro.fase("conteos"):
        conteos, distribucion = _contar(s, ejecucion)
    if publicadas != candidatas or sum(conteos[c] for c in CONTEOS_DE_CAMPO_V2) != candidatas:
        raise _NoSePublica(
            ResultadoEjecucionCampoV2.DATOS_INCONSISTENTES,
            f"La decision tiene {candidatas:,} candidatas de campo y se publicaron "
            f"{publicadas:,} asignaciones; no se publico ninguna.",
        )
    _punto_de_falla(ANTES_DE_CERRAR)
    _cerrar(s, ejecucion, politica, candidatas, conteos, distribucion)


def _ejecutar(s: Session, sentencias: tuple[str, ...], parametros: dict[str, Any]) -> None:
    for sentencia in sentencias:
        s.execute(text(sentencia), parametros)


def _contar_tabla(s: Session, tabla: str) -> int:
    return s.execute(text(f"SELECT count(*) FROM {tabla}")).scalar_one()


# --- lo que se resuelve en Python, sobre los bloques ----------------------------------------------


def _unidades(bloques, politica: PoliticaCampo) -> dict[tuple, Any]:
    """El identificador de cada unidad de cada celda ocupada: uno por (base, municipio, celda), no
    por cuenta."""
    return {
        (base, cvegeo, ix, iy): particion(codigo, cvegeo, ix, iy, politica)
        for base, codigo, cvegeo, ix, iy, _prioridad, _cuentas in bloques
    }


def _repartir(bloques, unidades, gestores) -> tuple[list[tuple], list[tuple]]:
    """El reparto de cada base con campo-public-v2, en las filas que vuelven a la base: una por
    parte de cada bloque, con el gestor por su id interno, y el propietario preferido de cada
    microtramo."""
    por_base: dict[int, list[Bloque]] = defaultdict(list)
    codigos: dict[int, str] = {}
    for base, codigo, cvegeo, ix, iy, prioridad, cuentas in bloques:
        microtramo = unidades[(base, cvegeo, ix, iy)].microtramo_id
        por_base[base].append(Bloque(prioridad, microtramo, cuentas))
        codigos[base] = codigo
    capacidades: dict[int, dict[str, int]] = defaultdict(dict)
    jornadas: dict[int, Counter] = defaultdict(Counter)
    ids: dict[tuple[int, str], int] = {}
    for gestor, codigo, base, capacidad, estado in gestores:
        jornadas[base][EstadoJornada(estado)] += 1
        if EstadoJornada(estado) in DISPONIBLES:
            capacidades[base][codigo] = capacidad
        ids[(base, codigo)] = gestor
    partes: list[tuple] = []
    propietarios: list[tuple] = []
    for base in sorted(por_base):
        reparto = repartir(
            por_base[base], capacidades.get(base, {}), base=codigos[base], jornadas=jornadas[base]
        )
        for p in reparto.partes:
            partes.append(
                (
                    base,
                    p.bloque.prioridad,
                    str(p.bloque.microtramo_id),
                    p.desde,
                    p.hasta,
                    reparto.inicio[p.bloque],
                    p.estado.value,
                    None if p.gestor is None else ids[(base, p.gestor)],
                    p.rebalanceada,
                    Jsonb(p.motivo),
                )
            )
        propietarios.extend(
            (base, str(microtramo), ids[(base, codigo)])
            for microtramo, codigo in reparto.propietario.items()
        )
    return partes, propietarios


# --- el SQL ---------------------------------------------------------------------------------------

SQL_DECISION = (
    # Las decisiones de canal CAMPO, por (ejecucion, canal, cuenta): sin recorrer las demas.
    "CREATE TEMP TABLE cv2_decision ON COMMIT DROP AS SELECT cuenta_canonica_id AS cuenta, "
    "prioridad_final AS prioridad FROM resultado_decision_v2 WHERE ejecucion_decision_v2_id = "
    ":decision AND canal_recomendado = :canal",
    "CREATE UNIQUE INDEX ON cv2_decision (cuenta)",
    "ANALYZE cv2_decision",
)

SQL_CANDIDATAS = (
    # Cada candidata con su cuenta_id publico, su asignacion territorial y, si es ASIGNADA, su base,
    # su municipio espacial y el punto de su geografia publicada: el hecho geografico no se vuelve a
    # calcular.
    "CREATE TEMP TABLE cv2_candidata ON COMMIT DROP AS SELECT d.cuenta, c.cuenta_id, d.prioridad, "
    "a.estado_asignacion AS estado_territorial, a.motivos AS motivos_territoriales, "
    "a.base_operativa_id AS base, m.cvegeo, g.punto FROM cv2_decision d "
    "JOIN cuenta_canonica c ON c.id = d.cuenta "
    "LEFT JOIN asignacion_territorial_v2 a ON a.ejecucion_territorial_v2_id = :territorial "
    "AND a.cuenta_canonica_id = d.cuenta "
    "LEFT JOIN municipio_geografico m ON m.id = a.municipio_geografico_id "
    "LEFT JOIN resultado_geografia_cuenta g ON a.estado_asignacion = 'ASIGNADA' "
    "AND g.ejecucion_geografia_id = a.ejecucion_geografia_id AND g.cuenta_canonica_id = d.cuenta",
    "CREATE UNIQUE INDEX ON cv2_candidata (cuenta)",
    "ANALYZE cv2_candidata",
)

SQL_REVISAR_CANDIDATAS = (
    "SELECT count(*) FILTER (WHERE estado_territorial IS NULL), count(*) FILTER (WHERE "
    "estado_territorial = 'ASIGNADA' AND (base IS NULL OR cvegeo IS NULL OR punto IS NULL)) "
    "FROM cv2_candidata"
)

SQL_CELDAS = (
    # La celda de microtramo de cada ASIGNADA: su punto en EPSG:6372, cada coordenada al milimetro
    # (numeric con 15 digitos y round a 3 decimales) y floor(x / 1,000). MATERIALIZED: una sola
    # transformacion por punto, no una por coordenada.
    "CREATE TEMP TABLE cv2_celda ON COMMIT DROP AS WITH metrico AS MATERIALIZED (SELECT cuenta, "
    "ST_Transform(punto, CAST(:srid AS integer)) AS p FROM cv2_candidata "
    "WHERE estado_territorial = 'ASIGNADA') SELECT cuenta, "
    "CAST(floor(round(CAST(ST_X(p) AS numeric), 3) / :microtramo) AS bigint) AS ix, "
    "CAST(floor(round(CAST(ST_Y(p) AS numeric), 3) / :microtramo) AS bigint) AS iy FROM metrico",
    "CREATE UNIQUE INDEX ON cv2_celda (cuenta)",
    "ANALYZE cv2_celda",
    # Lo que vuelve de Python: las unidades de cada celda ocupada, y el reparto de cada bloque.
    "CREATE TEMP TABLE cv2_unidad (base integer NOT NULL, cvegeo varchar(5) NOT NULL, "
    "ix bigint NOT NULL, iy bigint NOT NULL, zona_id uuid NOT NULL, subzona_id uuid NOT NULL, "
    "tramo_id uuid NOT NULL, microtramo_id uuid NOT NULL, PRIMARY KEY (base, cvegeo, ix, iy)) "
    "ON COMMIT DROP",
    "CREATE TEMP TABLE cv2_parte (base integer NOT NULL, prioridad varchar(12) NOT NULL, "
    "microtramo_id uuid NOT NULL, desde integer NOT NULL, hasta integer NOT NULL, "
    "inicio integer NOT NULL, estado varchar(24) NOT NULL, gestor integer, "
    "rebalanceada boolean NOT NULL, motivo jsonb NOT NULL) ON COMMIT DROP",
    "CREATE INDEX ON cv2_parte (base, prioridad, microtramo_id)",
    "CREATE TEMP TABLE cv2_propietario (base integer NOT NULL, microtramo_id uuid NOT NULL, "
    "gestor integer NOT NULL, PRIMARY KEY (base, microtramo_id)) ON COMMIT DROP",
)

SQL_BLOQUES = (
    # Los bloques: base, municipio, celda y prioridad, con cuantas candidatas tiene cada uno. Son lo
    # que reparte Python.
    "SELECT c.base, b.codigo, c.cvegeo, l.ix, l.iy, c.prioridad, count(*) FROM cv2_candidata c "
    "JOIN cv2_celda l ON l.cuenta = c.cuenta JOIN base_operativa b ON b.id = c.base "
    "GROUP BY c.base, b.codigo, c.cvegeo, l.ix, l.iy, c.prioridad"
)

_DISPONIBLES = "('JORNADA_SEMANAL', 'VENTANAS_ESPECIALES')"

SQL_GESTORES = (
    # La jornada de cada gestor de la configuracion en la fecha: inactivo; con una excepcion de la
    # fecha, cerrado o con sus ventanas especiales; o con las ventanas de ese dia de la semana. Su
    # capacidad efectiva es su cupo si esta disponible, 0 si no. Por gestor, no por cuenta.
    "CREATE TEMP TABLE cv2_gestor ON COMMIT DROP AS SELECT *, CASE WHEN estado_jornada IN "
    f"{_DISPONIBLES} THEN cupo ELSE 0 END AS capacidad FROM (SELECT g.id AS gestor, g.codigo, "
    "g.base_operativa_id AS base, g.calendario_campo_id AS calendario, x.id AS excepcion, "
    "CASE WHEN NOT g.activo THEN 'INACTIVO' WHEN x.tipo = 'CERRADO' THEN 'CERRADO_POR_EXCEPCION' "
    "WHEN x.tipo = 'VENTANAS_ESPECIALES' THEN 'VENTANAS_ESPECIALES' "
    "WHEN EXISTS (SELECT 1 FROM ventana_jornada_campo w WHERE w.calendario_campo_id = "
    "g.calendario_campo_id AND w.dia_semana = :dia) THEN 'JORNADA_SEMANAL' ELSE 'SIN_JORNADA' END "
    "AS estado_jornada, g.capacidad_cuentas_dia AS cupo FROM gestor_campo g "
    "LEFT JOIN excepcion_calendario_campo x ON x.calendario_campo_id = g.calendario_campo_id "
    "AND x.fecha = :fecha WHERE g.version_configuracion_campo_id = :configuracion) jornada",
)

SQL_RESULTADO = (
    # Lo que queda de cada candidata. Las ASIGNADA en territorial/v2: sus unidades, y su parte de su
    # bloque por su posicion en el orden de su cuenta_id (desde 0). Las demas, en revision
    # territorial, con su estado y los codigos de sus motivos territoriales.
    "CREATE TEMP TABLE cv2_resultado ON COMMIT DROP AS WITH ubicada AS (SELECT c.cuenta, "
    "c.prioridad, c.base, c.cvegeo, u.zona_id, u.subzona_id, u.tramo_id, u.microtramo_id, "
    "row_number() OVER (PARTITION BY c.base, c.prioridad, u.microtramo_id ORDER BY c.cuenta_id) "
    "- 1 AS posicion FROM cv2_candidata c JOIN cv2_celda l ON l.cuenta = c.cuenta "
    "JOIN cv2_unidad u ON u.base = c.base AND u.cvegeo = c.cvegeo AND u.ix = l.ix AND u.iy = l.iy) "
    "SELECT u.cuenta, u.prioridad, u.base, u.cvegeo, u.zona_id, u.subzona_id, u.tramo_id, "
    "u.microtramo_id, p.estado, p.gestor, p.rebalanceada, p.inicio + u.posicion + 1 AS orden, "
    "jsonb_build_array(p.motivo) AS motivos FROM ubicada u JOIN cv2_parte p ON p.base = u.base "
    "AND p.prioridad = u.prioridad AND p.microtramo_id = u.microtramo_id "
    "AND u.posicion >= p.desde AND u.posicion < p.hasta "
    "UNION ALL SELECT c.cuenta, c.prioridad, NULL, NULL, NULL, NULL, NULL, NULL, "
    f"'{EstadoCampo.REVISION_TERRITORIAL}', NULL, false, NULL, jsonb_build_array("
    "jsonb_build_object('codigo', 'TERRITORIAL_NO_ASIGNADA', 'estado_territorial', "
    "c.estado_territorial, 'motivos_territoriales', coalesce((SELECT jsonb_agg(m -> 'codigo') "
    "FROM jsonb_array_elements(c.motivos_territoriales) m), '[]'::jsonb))) "
    "FROM cv2_candidata c WHERE c.estado_territorial <> 'ASIGNADA'",
    "CREATE UNIQUE INDEX ON cv2_resultado (cuenta)",
)

SQL_ASIGNACIONES = (
    # La asignacion de cada candidata, en orden de cuenta.
    "INSERT INTO asignacion_campo_cuenta (zona_id, subzona_id, tramo_id, microtramo_id, "
    "ejecucion_campo_v2_id, cuenta_canonica_id, ejecucion_decision_v2_id, "
    "ejecucion_territorial_v2_id, version_configuracion_campo_id, version_bases_operativas_id, "
    "base_operativa_id, gestor_campo_id, orden_prioridad, estado, prioridad, es_rebalanceada, "
    "motivos) SELECT zona_id, subzona_id, tramo_id, microtramo_id, :ejecucion, cuenta, :decision, "
    ":territorial, :configuracion, :bases, base, gestor, orden, estado, prioridad, rebalanceada, "
    "motivos FROM cv2_resultado ORDER BY cuenta"
)


def _sql_unidad(nivel: NivelUnidad, unidad: str, padre: str, municipio: str) -> str:
    """Las unidades de un nivel que ocupo el plan, con su padre, su base, su municipio y cuantas
    candidatas y asignadas tienen. Una unidad determina su padre, su base y su municipio: agrupar
    tambien por ellos no cambia los grupos (y si alguna vez no fuera asi, la llave primaria lo
    rechazaria). Un nivel a la vez, del mas grande al mas chico: el padre ya esta publicado cuando
    llega su hija. Solo el microtramo lleva su propietario preferido."""
    propietario, propietarios = "NULL", ""
    if nivel == NivelUnidad.MICROTRAMO:
        propietario = "o.gestor"
        propietarios = " LEFT JOIN cv2_propietario o ON o.microtramo_id = r.microtramo_id"
    grupo = ", ".join(c for c in (unidad, padre, "r.base", municipio, propietario) if c != "NULL")
    return (
        "INSERT INTO unidad_territorial_campo (ejecucion_campo_v2_id, unidad_id, nivel, padre_id, "
        "base_operativa_id, cve_ent, cvegeo, candidatas, asignadas, propietario_gestor_campo_id) "
        f"SELECT :ejecucion, {unidad}, '{nivel}', {padre}, r.base, left({municipio}, 2), "
        f"{municipio if nivel != NivelUnidad.ZONA else 'NULL'}, count(*), count(*) FILTER "
        f"(WHERE r.estado = '{EstadoCampo.ASIGNADA}'), {propietario} FROM cv2_resultado r"
        f"{propietarios} WHERE r.base IS NOT NULL GROUP BY {grupo} ORDER BY {unidad}"
    )


SQL_UNIDADES = (
    _sql_unidad(NivelUnidad.ZONA, "r.zona_id", "NULL", "left(r.cvegeo, 2)"),
    _sql_unidad(NivelUnidad.SUBZONA, "r.subzona_id", "r.zona_id", "r.cvegeo"),
    _sql_unidad(NivelUnidad.TRAMO, "r.tramo_id", "r.subzona_id", "r.cvegeo"),
    _sql_unidad(NivelUnidad.MICROTRAMO, "r.microtramo_id", "r.tramo_id", "r.cvegeo"),
)


def _hhmm(columna: str) -> str:
    return (
        f"lpad(CAST({columna} / 60 AS text), 2, '0') || ':' || "
        f"lpad(CAST({columna} % 60 AS text), 2, '0')"
    )


def _instante(columna: str) -> str:
    return f"timezone(:zona, CAST(:fecha AS timestamp) + make_interval(mins => {columna}))"


SQL_CARGAS = (
    # La carga de cada gestor de la configuracion: su jornada y su capacidad en la fecha, lo que
    # recibio, y las ventanas de su dia con sus instantes en la zona de la configuracion.
    "INSERT INTO carga_gestor_campo (utilizacion, ejecucion_campo_v2_id, gestor_campo_id, "
    "base_operativa_id, estado_jornada, capacidad, asignadas, rebalanceadas, microtramos, tramos, "
    "ventanas) SELECT CASE WHEN g.capacidad > 0 THEN round(CAST(coalesce(r.asignadas, 0) AS "
    "numeric) / g.capacidad, 4) END, :ejecucion, g.gestor, g.base, g.estado_jornada, g.capacidad, "
    "coalesce(r.asignadas, 0), coalesce(r.rebalanceadas, 0), coalesce(r.microtramos, 0), "
    "coalesce(r.tramos, 0), coalesce((SELECT jsonb_agg(jsonb_build_object('inicio', "
    f"{_hhmm('w.minuto_inicio')}, 'fin', {_hhmm('w.minuto_fin')}, 'inicio_en', "
    f"{_instante('w.minuto_inicio')}, 'fin_en', {_instante('w.minuto_fin')}) "
    "ORDER BY w.minuto_inicio) FROM ventana_jornada_campo w WHERE w.calendario_campo_id = "
    "g.calendario AND ((g.estado_jornada = 'VENTANAS_ESPECIALES' AND "
    "w.excepcion_calendario_campo_id = g.excepcion) OR (g.estado_jornada = 'JORNADA_SEMANAL' "
    "AND w.dia_semana = :dia))), '[]'::jsonb) FROM cv2_gestor g LEFT JOIN (SELECT gestor, "
    "count(*) AS asignadas, count(*) FILTER (WHERE rebalanceada) AS rebalanceadas, "
    "count(DISTINCT microtramo_id) AS microtramos, count(DISTINCT tramo_id) AS tramos "
    "FROM cv2_resultado WHERE gestor IS NOT NULL GROUP BY gestor) r ON r.gestor = g.gestor "
    "ORDER BY g.gestor"
)


# --- contar y cerrar ------------------------------------------------------------------------------


def _contar(s: Session, ejecucion: EjecucionCampoV2) -> tuple[dict[str, int], dict[str, Any]]:
    """Lo que publico el plan, contado en la transaccion que lo publico: por estado, por base, por
    motivo y por nivel de unidad, la jornada de los gestores y el balance entre los disponibles. La
    distribucion es un diagnostico: no se usa para ajustar nada."""
    parametros = {"ejecucion": ejecucion.id}
    por_estado = dict(
        s.execute(
            text(
                "SELECT estado, count(*) FROM asignacion_campo_cuenta "
                "WHERE ejecucion_campo_v2_id = :ejecucion GROUP BY 1"
            ),
            parametros,
        ).all()
    )
    rebalanceadas = s.execute(
        text(
            "SELECT count(*) FROM asignacion_campo_cuenta WHERE ejecucion_campo_v2_id = :ejecucion "
            "AND es_rebalanceada"
        ),
        parametros,
    ).scalar_one()
    por_motivo = dict(
        s.execute(
            text(
                "SELECT m ->> 'codigo', count(*) FROM asignacion_campo_cuenta a, "
                "jsonb_array_elements(a.motivos) m WHERE a.ejecucion_campo_v2_id = :ejecucion "
                "GROUP BY 1 ORDER BY 1"
            ),
            parametros,
        ).all()
    )
    por_nivel = dict(
        s.execute(
            text(
                "SELECT nivel, count(*) FROM unidad_territorial_campo "
                "WHERE ejecucion_campo_v2_id = :ejecucion GROUP BY 1"
            ),
            parametros,
        ).all()
    )
    cargas = s.execute(
        text(
            "SELECT b.codigo, c.estado_jornada, c.capacidad, c.asignadas, c.microtramos, "
            "c.rebalanceadas FROM carga_gestor_campo c JOIN base_operativa b "
            "ON b.id = c.base_operativa_id WHERE c.ejecucion_campo_v2_id = :ejecucion"
        ),
        parametros,
    ).all()
    candidatas_por_base = {
        codigo: dict(
            zip(
                ("candidatas", "asignadas", "sin_gestor", "sin_capacidad", "rebalanceadas"),
                resto,
                strict=True,
            )
        )
        for codigo, *resto in s.execute(
            text(
                "SELECT b.codigo, count(*), count(*) FILTER (WHERE a.estado = 'ASIGNADA'), "
                "count(*) FILTER (WHERE a.estado = 'SIN_GESTOR_DISPONIBLE'), count(*) FILTER "
                "(WHERE a.estado = 'SIN_CAPACIDAD'), count(*) FILTER (WHERE a.es_rebalanceada) "
                "FROM asignacion_campo_cuenta a JOIN base_operativa b "
                "ON b.id = a.base_operativa_id WHERE a.ejecucion_campo_v2_id = :ejecucion "
                "GROUP BY b.codigo"
            ),
            parametros,
        ).all()
    }
    capacidad_total = sum(c[2] for c in cargas)
    por_base: dict[str, dict[str, Any]] = {}
    for codigo in sorted({c[0] for c in cargas} | set(candidatas_por_base)):
        propias = [c for c in cargas if c[0] == codigo]
        capacidad = sum(c[2] for c in propias)
        fila = candidatas_por_base.get(
            codigo,
            dict.fromkeys(
                ("candidatas", "asignadas", "sin_gestor", "sin_capacidad", "rebalanceadas"), 0
            ),
        )
        por_base[codigo] = {
            **fila,
            "gestores": len(propias),
            "gestores_disponibles": sum(1 for c in propias if c[2] > 0),
            "capacidad": capacidad,
            "utilizacion": _razon(fila["asignadas"], capacidad),
        }
    disponibles = [c for c in cargas if c[2] > 0]
    conteos = {
        columna: por_estado.get(estado.value, 0) for estado, columna in CONTEO_DE_ESTADO.items()
    }
    conteos["rebalanceadas"] = rebalanceadas
    conteos["capacidad_total"] = capacidad_total
    distribucion = {
        "estado": {e.value: por_estado.get(e.value, 0) for e in EstadoCampo},
        "motivos": por_motivo,
        "por_base": por_base,
        "unidades": {n.value: por_nivel.get(n.value, 0) for n in NivelUnidad},
        "jornada": dict(sorted(Counter(c[1] for c in cargas).items())),
        "gestores": len(cargas),
        "gestores_disponibles": len(disponibles),
        "balance": {
            "utilizacion": _resumen([Decimal(c[3]) / Decimal(c[2]) for c in disponibles]),
            "cuentas_por_gestor": _resumen([Decimal(c[3]) for c in disponibles]),
            "microtramos_por_gestor": _resumen([Decimal(c[4]) for c in disponibles]),
            "rebalanceadas_por_gestor": _resumen([Decimal(c[5]) for c in disponibles]),
            "coeficiente_variacion_cuentas": _coeficiente([c[3] for c in disponibles]),
        },
    }
    return conteos, distribucion


assert set(CONTEO_DE_ESTADO.values()) == set(CONTEOS_DE_CAMPO_V2)

_CUATRO = Decimal("0.0001")


def _razon(parte: int, total: int) -> str | None:
    if total == 0:
        return None
    return str((Decimal(parte) / Decimal(total)).quantize(_CUATRO, rounding=ROUND_HALF_UP))


def _resumen(valores: list[Decimal]) -> dict[str, str | None]:
    """El minimo, la mediana, el p95 (por rango mas cercano: el valor en la posicion
    ceil(0.95 * n)) y el maximo, como texto con cuatro decimales."""
    if not valores:
        return dict.fromkeys(("minimo", "mediana", "p95", "maximo"))
    orden = sorted(valores)
    p95 = orden[max(1, math.ceil(0.95 * len(orden))) - 1]

    def texto(valor: Decimal) -> str:
        return str(Decimal(valor).quantize(_CUATRO, rounding=ROUND_HALF_UP))

    return {
        "minimo": texto(orden[0]),
        "mediana": texto(statistics.median(orden)),
        "p95": texto(p95),
        "maximo": texto(orden[-1]),
    }


def _coeficiente(valores: list[int]) -> str | None:
    """La desviacion estandar poblacional entre la media: cuanto se separan las cargas."""
    if not valores or sum(valores) == 0:
        return None
    media = Decimal(sum(valores)) / len(valores)
    varianza = sum((Decimal(v) - media) ** 2 for v in valores) / len(valores)
    return str((varianza.sqrt() / media).quantize(_CUATRO, rounding=ROUND_HALF_UP))


def _cerrar(
    s: Session,
    ejecucion: EjecucionCampoV2,
    politica: PoliticaCampo,
    candidatas: int,
    conteos: dict[str, int],
    distribucion: dict[str, Any],
) -> None:
    for columna in (*CONTEOS_DE_CAMPO_V2, "rebalanceadas", "capacidad_total"):
        setattr(ejecucion, columna, conteos[columna])
    ejecucion.candidatas_campo = candidatas
    ejecucion.capacidad_utilizada = conteos["asignadas"]
    ejecucion.estado = EstadoCampoV2.EXITOSA
    ejecucion.resultado = ResultadoEjecucionCampoV2.PLAN_PUBLICADO.value
    ejecucion.distribucion = distribucion
    ejecucion.detalle = (
        f"Se planearon las {candidatas:,} candidatas de campo de "
        f"{ejecucion.cuentas_evaluadas_decision:,} cuentas decididas, para el "
        f"{ejecucion.fecha_operacion.isoformat()}, con {politica.version}: "
        f"{conteos['asignadas']:,} asignadas a un gestor de su base "
        f"({conteos['rebalanceadas']:,} por spillover), {conteos['revision_territorial']:,} en "
        f"revision territorial, {conteos['sin_gestor']:,} sin gestor disponible y "
        f"{conteos['sin_capacidad']:,} sin capacidad; {conteos['asignadas']:,} de "
        f"{conteos['capacidad_total']:,} de capacidad declarada. Capacidad declarada, no "
        f"factibilidad vial. {VERSION_MOTOR}."
    )
    ejecucion.terminada_en = ahora()
    s.add(ejecucion)
    s.flush()


def _fallar(
    s: Session,
    ejecucion_id: int,
    etiqueta: UUID,
    resultado: ResultadoEjecucionCampoV2,
    motivo: str,
) -> None:
    try:
        cola.confirmar_dueno(s)
    except cola.TrabajoAjeno:
        s.rollback()
        log.warning("campo v2 %s: su trabajo ya no es de este worker: %s", etiqueta, motivo)
        raise
    registrado = s.execute(
        update(EjecucionCampoV2)
        .where(
            EjecucionCampoV2.id == ejecucion_id, EjecucionCampoV2.estado == EstadoCampoV2.EN_PROCESO
        )
        .values(
            estado=EstadoCampoV2.FALLIDA,
            resultado=resultado.value,
            detalle=motivo,
            terminada_en=ahora(),
            candidatas_campo=0,
            rebalanceadas=0,
            capacidad_total=0,
            capacidad_utilizada=0,
            **dict.fromkeys(CONTEOS_DE_CAMPO_V2, 0),
        )
        .execution_options(synchronize_session=False)
    ).rowcount
    s.commit()
    if registrado:
        log.warning("campo v2 %s fallido (%s): %s", etiqueta, resultado.value, motivo)
