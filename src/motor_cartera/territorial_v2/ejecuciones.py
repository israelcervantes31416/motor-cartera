"""Una ejecucion de territorial/v2: a que base operativa se asigna cada cuenta de un corte, en
PostgreSQL y todo o nada.

El orden:

1. `abrir` registra la ejecucion EN_PROCESO con su trabajo TERRITORIAL_V2, en la transaccion de
   quien llama, despues de fijar exactamente sus entradas: el corte, una ejecucion de geografia
   EXITOSA de ese corte, una version de bases operativas y la politica con su hash. Con ellas sale
   su `firma_inputs`. Nunca toma la geografia mas nueva ni las bases actuales: se declaran. La piden
   POST /territorial-v2 y `motor-cartera asignar-territorial-v2`.
2. Un worker toma el trabajo. `asignar` toma la ejecucion con su fila bloqueada y, en una
   transaccion y por conjuntos:
   - lee la geografia publicada de cada cuenta (ningun punto en poligono se repite: territorial/v2
     reutiliza el hecho geografico);
   - une las cuentas con geografia confiable con las bases activas de la version que cubren su
     municipio, y calcula la distancia geodesica a cada una (ST_Distance sobre geography);
   - ordena las elegibles de cada cuenta y decide, con territorial-public-v2: la unica, la
     inequivocamente mas cercana, o un empate dentro de la tolerancia;
   - publica con un INSERT ... SELECT la asignacion de cada cuenta, la cuenta y cierra EXITOSA.
   Si algo falla, se revierte todo y la ejecucion queda FALLIDA: nunca una asignacion a medias.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session, select

from motor_cartera.db.modelos import (
    CONTEOS_DE_TERRITORIAL_V2,
    CorteCanonico,
    EjecucionGeografia,
    EjecucionTerritorialV2,
    EstadoGeografia,
    EstadoTerritorialV2,
    ResultadoEjecucionTerritorialV2,
    TipoTrabajo,
    VersionBasesOperativas,
    ahora,
)
from motor_cartera.db.sesion import insertar_en_savepoint, restriccion, sesion
from motor_cartera.geografia.reglas import EstadoGeografia as Geografia
from motor_cartera.ingesta.fuente_oficial import Cronometro
from motor_cartera.orquestacion import cola
from motor_cartera.territorial_v2.politica import (
    CONTEO_DE_ESTADO,
    POLITICAS,
    PUBLIC_V2,
    VERSION_MOTOR,
    EstadoAsignacion,
    MotivoAsignacion,
    PoliticaTerritorial,
    firma_inputs,
    hash_politica,
)

log = logging.getLogger(__name__)

INDICE_EXITOSA = "ux_territorial_v2_exitosa"
INDICE_EN_PROCESO = "ux_territorial_v2_en_proceso"


# --- errores al abrir -----------------------------------------------------------------------------


class EntradaNoAsignable(Exception):
    """Una peticion de territorial/v2 que no se puede abrir: no registra nada. `codigo` es
    estable."""

    codigo = "ENTRADA_NO_ASIGNABLE"
    estado_http = 422


class CorteNoEncontrado(EntradaNoAsignable):
    codigo = "CORTE_NO_ENCONTRADO"
    estado_http = 404


class GeografiaNoEncontrada(EntradaNoAsignable):
    codigo = "GEOGRAFIA_NO_ENCONTRADA"
    estado_http = 404


class GeografiaNoExitosa(EntradaNoAsignable):
    codigo = "GEOGRAFIA_NO_EXITOSA"


class GeografiaDeOtroCorte(EntradaNoAsignable):
    codigo = "GEOGRAFIA_DE_OTRO_CORTE"


class BasesNoEncontradas(EntradaNoAsignable):
    codigo = "BASES_NO_ENCONTRADAS"
    estado_http = 404


class PoliticaNoSoportada(EntradaNoAsignable):
    codigo = "POLITICA_NO_SOPORTADA"


class TerritorialV2YaGenerado(Exception):
    """Otra ejecucion ya publico exactamente las mismas entradas (la misma firma)."""

    def __init__(self, previa: EjecucionTerritorialV2) -> None:
        super().__init__(
            f"Esas entradas ya se asignaron: la ejecucion {previa.territorial_run_id} (firma "
            f"{previa.firma_inputs[:12]}...)."
        )
        self.previa = previa


class TerritorialV2EnProceso(Exception):
    """Esas mismas entradas ya se estan asignando: hay una ejecucion EN_PROCESO."""

    def __init__(self, activa: EjecucionTerritorialV2) -> None:
        super().__init__(
            f"Esas entradas ya se estan asignando: la ejecucion {activa.territorial_run_id}."
        )
        self.activa = activa


class _NoSePublica(Exception):
    def __init__(self, resultado: ResultadoEjecucionTerritorialV2, mensaje: str) -> None:
        super().__init__(mensaje)
        self.resultado = resultado


# --- abrir ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Entradas:
    corte: CorteCanonico
    geografia: EjecucionGeografia
    bases: VersionBasesOperativas
    politica: PoliticaTerritorial
    firma: str


def entradas(
    s: Session,
    corte_id: UUID,
    geografia_run_id: UUID,
    bases_version_id: UUID,
    version_politica: str | None = None,
) -> Entradas:
    """Las entradas de una asignacion, comprobadas, con su firma. Levanta CorteNoEncontrado,
    GeografiaNoEncontrada, GeografiaNoExitosa, GeografiaDeOtroCorte, BasesNoEncontradas o
    PoliticaNoSoportada."""
    corte = s.exec(select(CorteCanonico).where(CorteCanonico.corte_id == corte_id)).one_or_none()
    if corte is None:
        raise CorteNoEncontrado(f"No existe un corte canonico con corte_id {corte_id}.")
    geografia = s.exec(
        select(EjecucionGeografia).where(EjecucionGeografia.geografia_run_id == geografia_run_id)
    ).one_or_none()
    if geografia is None:
        raise GeografiaNoEncontrada(
            f"No existe una ejecucion de geografia con geografia_run_id {geografia_run_id}."
        )
    if geografia.estado != EstadoGeografia.EXITOSA:
        raise GeografiaNoExitosa(
            f"La geografia {geografia_run_id} esta {geografia.estado}: territorial/v2 solo asigna "
            "sobre una geografia EXITOSA, nunca sobre una parcial."
        )
    if geografia.corte_canonico_id != corte.id:
        raise GeografiaDeOtroCorte(
            f"La geografia {geografia_run_id} es de otro corte, no del {corte.fecha_corte}."
        )
    bases = s.exec(
        select(VersionBasesOperativas).where(
            VersionBasesOperativas.bases_version_id == bases_version_id
        )
    ).one_or_none()
    if bases is None:
        raise BasesNoEncontradas(
            f"No existe una version de bases operativas con bases_version_id {bases_version_id}."
        )
    version_politica = version_politica or PUBLIC_V2.version
    politica = POLITICAS.get(version_politica)
    if politica is None:
        raise PoliticaNoSoportada(
            f"Este servicio asigna con {', '.join(sorted(POLITICAS))}, no con {version_politica}."
        )
    firma = firma_inputs(
        corte_id=corte.corte_id,
        geografia_run_id=geografia.geografia_run_id,
        firma_geografia=geografia.firma_inputs,
        bases_version_id=bases.bases_version_id,
        hash_configuracion=bases.hash_configuracion,
        politica=politica,
    )
    return Entradas(corte, geografia, bases, politica, firma)


def abrir(
    s: Session,
    corte_id: UUID,
    geografia_run_id: UUID,
    bases_version_id: UUID,
    *,
    version_politica: str | None = None,
    max_intentos: int,
    tomado_por: str | None = None,
    lease_segundos: float | None = None,
) -> EjecucionTerritorialV2:
    """La ejecucion EN_PROCESO de territorial/v2, con su trabajo TERRITORIAL_V2, en la transaccion
    de quien llama: envia los INSERT y no confirma. Levanta, sin registrar nada, lo que levanta
    `entradas`, TerritorialV2YaGenerado y TerritorialV2EnProceso."""
    e = entradas(s, corte_id, geografia_run_id, bases_version_id, version_politica)
    previa = _de_firma(s, e.firma, EstadoTerritorialV2.EXITOSA)
    if previa is not None:
        raise TerritorialV2YaGenerado(previa)
    activa = _de_firma(s, e.firma, EstadoTerritorialV2.EN_PROCESO)
    if activa is not None:
        raise TerritorialV2EnProceso(activa)
    ejecucion = EjecucionTerritorialV2(
        corte_canonico_id=e.corte.id,
        ejecucion_geografia_id=e.geografia.id,
        version_bases_operativas_id=e.bases.id,
        version_motor=VERSION_MOTOR,
        version_politica=e.politica.version,
        hash_politica=hash_politica(e.politica),
        firma_inputs=e.firma,
        cuentas_objetivo=e.corte.cuentas,
    )
    error = insertar_en_savepoint(s, ejecucion)
    if error is not None:
        if restriccion(error) != INDICE_EN_PROCESO:
            raise error
        activa = _de_firma(s, e.firma, EstadoTerritorialV2.EN_PROCESO)
        if (
            activa is None
        ):  # pragma: no cover - la que gano ya termino entre el rechazo y la revision
            raise error
        raise TerritorialV2EnProceso(activa) from error
    cola.crear(
        s,
        TipoTrabajo.TERRITORIAL_V2,
        ejecucion.id,
        max_intentos=max_intentos,
        tomado_por=tomado_por,
        lease_segundos=lease_segundos,
    )
    log.info(
        "territorial v2 %s abierta: corte %s, geografia %s, bases %s",
        ejecucion.territorial_run_id,
        e.corte.corte_id,
        e.geografia.geografia_run_id,
        e.bases.bases_version_id,
    )
    return ejecucion


def _de_firma(s: Session, firma: str, estado: EstadoTerritorialV2) -> EjecucionTerritorialV2 | None:
    consulta = select(EjecucionTerritorialV2).where(
        EjecucionTerritorialV2.firma_inputs == firma, EjecucionTerritorialV2.estado == estado
    )
    if estado == EstadoTerritorialV2.EN_PROCESO:
        consulta = consulta.with_for_update()
    return s.exec(consulta).first()


# --- asignar --------------------------------------------------------------------------------------

ANTES_DEL_STAGING = "ANTES_DEL_STAGING"
DESPUES_DE_LAS_ELEGIBLES = "DESPUES_DE_LAS_ELEGIBLES"
DESPUES_DE_LAS_DISTANCIAS = "DESPUES_DE_LAS_DISTANCIAS"
ANTES_DE_PUBLICAR = "ANTES_DE_PUBLICAR"
DESPUES_DE_LAS_ASIGNACIONES = "DESPUES_DE_LAS_ASIGNACIONES"
ANTES_DE_CERRAR = "ANTES_DE_CERRAR"
PUNTOS_DE_FALLA = (
    ANTES_DEL_STAGING,
    DESPUES_DE_LAS_ELEGIBLES,
    DESPUES_DE_LAS_DISTANCIAS,
    ANTES_DE_PUBLICAR,
    DESPUES_DE_LAS_ASIGNACIONES,
    ANTES_DE_CERRAR,
)


def _punto_de_falla(nombre: str) -> None:
    """Un punto donde una prueba puede inyectar una falla. En produccion no hace nada."""


def ejecutar_territorial_v2(ejecucion_id: int) -> None:
    """Lo que ejecuta un trabajo TERRITORIAL_V2. Si la ejecucion ya termino no hace nada."""
    with sesion() as s:
        ejecucion = s.get_one(EjecucionTerritorialV2, ejecucion_id)
        if ejecucion.estado != EstadoTerritorialV2.EN_PROCESO:
            log.info(
                "territorial v2 %s ya termino %s", ejecucion.territorial_run_id, ejecucion.estado
            )
            return
    asignar(ejecucion_id)


def asignar(ejecucion_id: int, *, cronometro: Cronometro | None = None) -> None:
    """Asigna las cuentas del corte de la ejecucion y la deja en un estado terminal: con su fila
    bloqueada hasta el commit, todo en una transaccion y en un savepoint; si falla, queda FALLIDA,
    y solo publica o falla el dueno vigente de su trabajo. Levanta TerritorialV2YaGenerado si otra
    ejecucion publico primero la misma firma, TrabajoAjeno, y los errores transitorios de la base,
    con la ejecucion EN_PROCESO para que la cola la reintente."""
    cronometro = cronometro or Cronometro()
    with sesion() as s:
        ejecucion = s.exec(
            select(EjecucionTerritorialV2)
            .where(EjecucionTerritorialV2.id == ejecucion_id)
            .with_for_update()
        ).one()
        if ejecucion.estado != EstadoTerritorialV2.EN_PROCESO:
            log.warning(
                "territorial v2 %s ya termino %s", ejecucion.territorial_run_id, ejecucion.estado
            )
            return
        etiqueta, firma = ejecucion.territorial_run_id, ejecucion.firma_inputs
        try:
            with s.begin_nested():
                _asignar(s, ejecucion, cronometro)
        except _NoSePublica as exc:
            resultado, motivo = exc.resultado, str(exc)
        except IntegrityError as exc:
            if restriccion(exc) == INDICE_EXITOSA:
                resultado, motivo = (
                    ResultadoEjecucionTerritorialV2.YA_GENERADO,
                    "Otra ejecucion publico la misma firma de entrada mientras esta se procesaba; "
                    "no se publica dos veces.",
                )
            else:
                log.exception("territorial v2 %s: violacion de integridad inesperada", etiqueta)
                resultado, motivo = (
                    ResultadoEjecucionTerritorialV2.ERROR_INTERNO,
                    "Error interno al publicar; ver la bitacora del servicio.",
                )
        except OperationalError:
            log.warning("territorial v2 %s: error transitorio; sigue EN_PROCESO", etiqueta)
            raise
        except Exception as exc:
            log.exception("territorial v2 %s: error inesperado", etiqueta)
            resultado, motivo = (
                ResultadoEjecucionTerritorialV2.ERROR_INTERNO,
                f"Error interno ({type(exc).__name__}); ver la bitacora.",
            )
        else:
            cola.confirmar_dueno(s)
            with cronometro.fase("commit"):
                s.commit()
            return
        _fallar(s, ejecucion_id, etiqueta, resultado, motivo)
        if resultado == ResultadoEjecucionTerritorialV2.YA_GENERADO:
            raise TerritorialV2YaGenerado(_de_firma(s, firma, EstadoTerritorialV2.EXITOSA))


def _asignar(s: Session, ejecucion: EjecucionTerritorialV2, cronometro: Cronometro) -> None:
    politica = POLITICAS.get(ejecucion.version_politica)
    if (
        ejecucion.version_motor != VERSION_MOTOR
        or politica is None
        or ejecucion.hash_politica != hash_politica(politica)
    ):
        raise _NoSePublica(
            ResultadoEjecucionTerritorialV2.VERSION_NO_SOPORTADA,
            f"La ejecucion pide {ejecucion.version_motor} con {ejecucion.version_politica} (hash "
            f"{ejecucion.hash_politica[:12]}...), y este servicio asigna con {VERSION_MOTOR} y "
            f"{', '.join(sorted(POLITICAS))}; no se publico nada.",
        )
    geografia = s.get_one(EjecucionGeografia, ejecucion.ejecucion_geografia_id)
    if geografia.estado != EstadoGeografia.EXITOSA:
        raise _NoSePublica(
            ResultadoEjecucionTerritorialV2.GEOGRAFIA_NO_EXITOSA,
            f"La geografia {geografia.geografia_run_id} esta {geografia.estado}; no se publico "
            "nada.",
        )
    corte = s.get_one(CorteCanonico, ejecucion.corte_canonico_id)
    _punto_de_falla(ANTES_DEL_STAGING)
    parametros: dict[str, Any] = {
        "ejecucion": ejecucion.id,
        "geografia": geografia.id,
        "bases": ejecucion.version_bases_operativas_id,
        "tolerancia": politica.tolerancia_empate_m,
    }
    with cronometro.fase("geografia"):
        _ejecutar(s, SQL_CUENTAS, parametros)
        cuentas = s.execute(text("SELECT count(*) FROM tv2_cuenta")).scalar_one()
    if not cuentas == geografia.cuentas_objetivo == corte.cuentas:
        raise _NoSePublica(
            ResultadoEjecucionTerritorialV2.DATOS_INCONSISTENTES,
            f"El corte tiene {corte.cuentas:,} cuentas y su geografia publico {cuentas:,}; no se "
            "publico nada.",
        )
    with cronometro.fase("elegibles"):
        _ejecutar(s, SQL_ELEGIBLES, parametros)
    _punto_de_falla(DESPUES_DE_LAS_ELEGIBLES)
    with cronometro.fase("distancias"):
        _ejecutar(s, SQL_DISTANCIAS, parametros)
    _punto_de_falla(DESPUES_DE_LAS_DISTANCIAS)
    _punto_de_falla(ANTES_DE_PUBLICAR)
    with cronometro.fase("publicacion"):
        publicadas = s.execute(text(SQL_ASIGNACIONES), parametros).rowcount
    _punto_de_falla(DESPUES_DE_LAS_ASIGNACIONES)
    with cronometro.fase("conteos"):
        conteos, distribucion = _contar(s, ejecucion)
    if publicadas != corte.cuentas or sum(conteos.values()) != corte.cuentas:
        raise _NoSePublica(
            ResultadoEjecucionTerritorialV2.DATOS_INCONSISTENTES,
            f"El corte tiene {corte.cuentas:,} cuentas y se publicaron {publicadas:,} "
            "asignaciones; no se publico ninguna.",
        )
    _punto_de_falla(ANTES_DE_CERRAR)
    _cerrar(s, ejecucion, corte, politica, conteos, distribucion)


def _ejecutar(s: Session, sentencias: tuple[str, ...], parametros: dict[str, Any]) -> None:
    for sentencia in sentencias:
        s.execute(text(sentencia), parametros)


# --- el SQL ---------------------------------------------------------------------------------------

SQL_CUENTAS = (
    # La geografia publicada de cada cuenta, con el CVEGEO de su municipio espacial. Nada se vuelve
    # a calcular: el hecho geografico es el de la ejecucion de geografia declarada.
    "CREATE TEMP TABLE tv2_cuenta ON COMMIT DROP AS SELECT r.cuenta_canonica_id AS cuenta, "
    "r.estado_geografia, r.municipio_geografico_id AS municipio, m.cvegeo, r.punto "
    "FROM resultado_geografia_cuenta r LEFT JOIN municipio_geografico m "
    "ON m.id = r.municipio_geografico_id WHERE r.ejecucion_geografia_id = :geografia",
    "CREATE UNIQUE INDEX ON tv2_cuenta (cuenta)",
    "ANALYZE tv2_cuenta",
)

SQL_ELEGIBLES = (
    # Las bases de la version que cubren cada municipio, activas o no: por municipio, no por cuenta.
    "CREATE TEMP TABLE tv2_cobertura ON COMMIT DROP AS SELECT k.cvegeo, b.id AS base, b.codigo, "
    "b.activa, b.punto FROM cobertura_base_municipio k JOIN base_operativa b "
    "ON b.id = k.base_operativa_id WHERE b.version_bases_operativas_id = :bases",
    "CREATE INDEX ON tv2_cobertura (cvegeo)",
    "ANALYZE tv2_cobertura",
    # Las elegibles de cada cuenta con geografia confiable: las activas que cubren su municipio,
    # con la distancia geodesica del punto de la cuenta a la base, en metros, al centimetro.
    "CREATE TEMP TABLE tv2_candidata ON COMMIT DROP AS SELECT c.cuenta, k.base, k.codigo, "
    "round(CAST(ST_Distance(CAST(c.punto AS geography), CAST(k.punto AS geography)) AS numeric), "
    "2) AS distancia FROM tv2_cuenta c JOIN tv2_cobertura k ON k.cvegeo = c.cvegeo AND k.activa "
    f"WHERE c.estado_geografia = '{Geografia.VALIDA}'",
    "CREATE INDEX ON tv2_candidata (cuenta)",
    "ANALYZE tv2_candidata",
)

SQL_DISTANCIAS = (
    # Por cuenta: cuantas elegibles, la menor distancia, las que quedan a no mas de la tolerancia
    # de la menor (si hay mas de una, es un empate), y la primera y la segunda en orden de
    # distancia. El codigo solo ordena lo que se muestra: nunca decide entre dos bases.
    "CREATE TEMP TABLE tv2_orden ON COMMIT DROP AS SELECT *, "
    "min(distancia) OVER (PARTITION BY cuenta) AS menor, "
    "row_number() OVER (PARTITION BY cuenta ORDER BY distancia, codigo) AS lugar "
    "FROM tv2_candidata",
    "CREATE TEMP TABLE tv2_rango ON COMMIT DROP AS SELECT cuenta, count(*) AS elegibles, "
    "min(menor) AS menor, array_agg(codigo ORDER BY codigo) FILTER (WHERE distancia - menor <= "
    "CAST(:tolerancia AS numeric)) AS empatadas, max(base) FILTER (WHERE lugar = 1) AS base, "
    "max(codigo) FILTER (WHERE lugar = 1) AS codigo, max(codigo) FILTER (WHERE lugar = 2) "
    "AS siguiente, max(distancia) FILTER (WHERE lugar = 2) AS distancia_siguiente "
    "FROM tv2_orden GROUP BY cuenta",
    "CREATE UNIQUE INDEX ON tv2_rango (cuenta)",
    # Las inactivas que cubren cada municipio: solo para explicar un SIN_BASE_ELEGIBLE.
    "CREATE TEMP TABLE tv2_inactiva ON COMMIT DROP AS SELECT cvegeo, array_agg(codigo ORDER BY "
    "codigo) AS bases FROM tv2_cobertura WHERE NOT activa GROUP BY cvegeo",
)


def _metros(columna: str) -> str:
    return f"to_jsonb(CAST({columna} AS text))"


SQL_ASIGNACIONES = (
    # La asignacion de cada cuenta del corte, con territorial-public-v2, en orden de cuenta.
    "WITH clasificada AS (SELECT c.*, r.elegibles, r.menor, r.empatadas, r.base, r.codigo, "
    "r.siguiente, r.distancia_siguiente, i.bases AS inactivas, CASE "
    f"WHEN c.estado_geografia = '{Geografia.FUERA_DE_REFERENCIA}' "
    f"THEN '{EstadoAsignacion.FUERA_DE_COBERTURA}' "
    f"WHEN c.estado_geografia <> '{Geografia.VALIDA}' "
    f"THEN '{EstadoAsignacion.REVISION_GEOGRAFICA}' "
    f"WHEN r.cuenta IS NULL THEN '{EstadoAsignacion.SIN_BASE_ELEGIBLE}' "
    f"WHEN cardinality(r.empatadas) > 1 THEN '{EstadoAsignacion.AMBIGUA_BASE}' "
    f"ELSE '{EstadoAsignacion.ASIGNADA}' END AS estado FROM tv2_cuenta c "
    "LEFT JOIN tv2_rango r ON r.cuenta = c.cuenta "
    "LEFT JOIN tv2_inactiva i ON i.cvegeo = c.cvegeo) "
    "INSERT INTO asignacion_territorial_v2 (distancia_base_m, ejecucion_territorial_v2_id, "
    "cuenta_canonica_id, ejecucion_geografia_id, version_bases_operativas_id, "
    "municipio_geografico_id, base_operativa_id, estado_asignacion, motivos) "
    f"SELECT CASE WHEN estado = '{EstadoAsignacion.ASIGNADA}' THEN menor END, :ejecucion, cuenta, "
    ":geografia, :bases, "
    f"CASE WHEN estado IN ('{EstadoAsignacion.ASIGNADA}', '{EstadoAsignacion.SIN_BASE_ELEGIBLE}', "
    f"'{EstadoAsignacion.AMBIGUA_BASE}') THEN municipio END, "
    f"CASE WHEN estado = '{EstadoAsignacion.ASIGNADA}' THEN base END, estado, "
    "to_jsonb(array_remove(ARRAY["
    f"CASE WHEN estado = '{EstadoAsignacion.FUERA_DE_COBERTURA}' THEN jsonb_build_object("
    f"'codigo', '{MotivoAsignacion.PUNTO_FUERA_DE_REFERENCIA}') END, "
    f"CASE WHEN estado = '{EstadoAsignacion.REVISION_GEOGRAFICA}' THEN jsonb_build_object("
    f"'codigo', '{MotivoAsignacion.GEOGRAFIA_NO_CONFIABLE}', 'estado_geografia', "
    "estado_geografia) END, "
    f"CASE WHEN estado = '{EstadoAsignacion.SIN_BASE_ELEGIBLE}' THEN jsonb_build_object("
    f"'codigo', '{MotivoAsignacion.SIN_BASE_QUE_CUBRA}', 'cvegeo', cvegeo) END, "
    f"CASE WHEN estado = '{EstadoAsignacion.SIN_BASE_ELEGIBLE}' AND inactivas IS NOT NULL THEN "
    f"jsonb_build_object('codigo', '{MotivoAsignacion.BASES_INACTIVAS}', 'bases', "
    "to_jsonb(inactivas)) END, "
    f"CASE WHEN estado = '{EstadoAsignacion.ASIGNADA}' AND elegibles = 1 THEN jsonb_build_object("
    f"'codigo', '{MotivoAsignacion.BASE_UNICA_ELEGIBLE}', 'base', codigo, 'distancia_m', "
    f"{_metros('menor')}) END, "
    f"CASE WHEN estado = '{EstadoAsignacion.ASIGNADA}' AND elegibles > 1 THEN jsonb_build_object("
    f"'codigo', '{MotivoAsignacion.BASE_MAS_CERCANA}', 'base', codigo, 'distancia_m', "
    f"{_metros('menor')}, 'elegibles', elegibles, 'siguiente', siguiente, "
    f"'distancia_siguiente_m', {_metros('distancia_siguiente')}) END, "
    f"CASE WHEN estado = '{EstadoAsignacion.AMBIGUA_BASE}' THEN jsonb_build_object("
    f"'codigo', '{MotivoAsignacion.EMPATE_ENTRE_BASES}', 'bases', to_jsonb(empatadas), "
    f"'distancia_m', {_metros('menor')}, 'tolerancia_m', "
    f"{_metros('CAST(:tolerancia AS numeric(12, 2))')}) END"
    "]::jsonb[], NULL)) FROM clasificada ORDER BY cuenta"
)


# --- contar y cerrar ------------------------------------------------------------------------------


def _contar(s: Session, ejecucion: EjecucionTerritorialV2) -> tuple[dict[str, int], dict[str, Any]]:
    """Cuantas asignaciones de cada estado, por base y por motivo, contadas en la transaccion que
    las publico. La distribucion por base es un diagnostico: v0.11 no balancea cargas."""
    parametros = {"ejecucion": ejecucion.id}
    por_estado = dict(
        s.execute(
            text(
                "SELECT estado_asignacion, count(*) FROM asignacion_territorial_v2 "
                "WHERE ejecucion_territorial_v2_id = :ejecucion GROUP BY 1"
            ),
            parametros,
        ).all()
    )
    por_base = {
        codigo: {"cuentas": cuentas, "distancia_mediana_m": str(mediana)}
        for codigo, cuentas, mediana in s.execute(
            text(
                "SELECT b.codigo, count(*), round(CAST(percentile_cont(0.5) WITHIN GROUP "
                "(ORDER BY a.distancia_base_m) AS numeric), 2) FROM asignacion_territorial_v2 a "
                "JOIN base_operativa b ON b.id = a.base_operativa_id "
                "WHERE a.ejecucion_territorial_v2_id = :ejecucion GROUP BY b.codigo ORDER BY "
                "b.codigo"
            ),
            parametros,
        ).all()
    }
    por_motivo = dict(
        s.execute(
            text(
                "SELECT m ->> 'codigo', count(*) FROM asignacion_territorial_v2 a, "
                "jsonb_array_elements(a.motivos) m WHERE a.ejecucion_territorial_v2_id = "
                ":ejecucion GROUP BY 1 ORDER BY 1"
            ),
            parametros,
        ).all()
    )
    conteos = {
        columna: por_estado.get(estado.value, 0) for estado, columna in CONTEO_DE_ESTADO.items()
    }
    distribucion = {
        "estado_asignacion": {e.value: por_estado.get(e.value, 0) for e in EstadoAsignacion},
        "por_base": por_base,
        "motivos": por_motivo,
    }
    return conteos, distribucion


assert set(CONTEO_DE_ESTADO.values()) == set(CONTEOS_DE_TERRITORIAL_V2)


def _cerrar(
    s: Session,
    ejecucion: EjecucionTerritorialV2,
    corte: CorteCanonico,
    politica: PoliticaTerritorial,
    conteos: dict[str, int],
    distribucion: dict[str, Any],
) -> None:
    for columna, valor in conteos.items():
        setattr(ejecucion, columna, valor)
    ejecucion.estado = EstadoTerritorialV2.EXITOSA
    ejecucion.resultado = ResultadoEjecucionTerritorialV2.TERRITORIAL_PUBLICADO.value
    ejecucion.distribucion = distribucion
    ejecucion.detalle = (
        f"Se asignaron las {corte.cuentas:,} cuentas del corte del {corte.fecha_corte.isoformat()} "
        f"con {politica.version}: {conteos['asignadas']:,} a una base, "
        f"{conteos['revision_geografica']:,} a revision geografica, {conteos['sin_base']:,} sin "
        f"base elegible, {conteos['fuera_cobertura']:,} fuera de cobertura y "
        f"{conteos['ambiguas']:,} con un empate entre bases (tolerancia "
        f"{politica.tolerancia_empate_m} m). Sin capacidad ni balanceo: es pertenencia "
        f"territorial. {VERSION_MOTOR}."
    )
    ejecucion.terminada_en = ahora()
    s.add(ejecucion)
    s.flush()


def _fallar(
    s: Session,
    ejecucion_id: int,
    etiqueta: UUID,
    resultado: ResultadoEjecucionTerritorialV2,
    motivo: str,
) -> None:
    try:
        cola.confirmar_dueno(s)
    except cola.TrabajoAjeno:
        s.rollback()
        log.warning("territorial v2 %s: su trabajo ya no es de este worker: %s", etiqueta, motivo)
        raise
    registrado = s.execute(
        update(EjecucionTerritorialV2)
        .where(
            EjecucionTerritorialV2.id == ejecucion_id,
            EjecucionTerritorialV2.estado == EstadoTerritorialV2.EN_PROCESO,
        )
        .values(
            estado=EstadoTerritorialV2.FALLIDA,
            resultado=resultado.value,
            detalle=motivo,
            terminada_en=ahora(),
            **dict.fromkeys(CONTEOS_DE_TERRITORIAL_V2, 0),
        )
        .execution_options(synchronize_session=False)
    ).rowcount
    s.commit()
    if registrado:
        log.warning("territorial v2 %s fallida (%s): %s", etiqueta, resultado.value, motivo)
