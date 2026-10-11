"""Una ejecucion de geografia/v1: la geografia de cada cuenta de un corte, en PostgreSQL y todo o
nada.

El orden:

1. `abrir` registra la ejecucion EN_PROCESO con su trabajo GEOGRAFIA, en la transaccion de quien
   llama, despues de fijar sus entradas: el corte (el universo: sus snapshots), su dataset
   conformado de cartera/v2 (de donde salen las coordenadas observadas), una version PUBLICADA de
   la referencia y la version del motor. Con ellas sale su `firma_inputs`. La piden
   POST /geografia/ejecuciones y `motor-cartera geolocalizar`. Nunca usa la referencia mas nueva:
   la declara quien la pide.
2. Un worker toma el trabajo. `geolocalizar` toma la ejecucion con su fila bloqueada y, en una
   transaccion y por conjuntos:
   - comprueba el Parquet conformado contra su SHA-256 y contra su registro (nunca lee el archivo
     original);
   - copia de el, por lotes, con el escritor CSV de Arrow y COPY, solo cinco columnas: la fila, la
     hoja, CLIENTE_UNICO, LATITUD y LONGITUD;
   - une cada fila con su cuenta canonica y su snapshot del corte, de donde sale el municipio que
     resolvio la fuente (ESTADO_CTE y POBLACION_CTE con el catalogo del INEGI);
   - clasifica las coordenadas con las reglas de `reglas` y construye ST_MakePoint(longitud,
     latitud) de las utilizables, en una tabla temporal con su indice GiST;
   - busca los puntos en los municipios de la version con ST_Covers, recorriendo los municipios y
     entrando por el indice de los puntos: PostGIS prepara cada poligono una vez y lo prueba contra
     todos sus puntos. Ningun punto en poligono pasa por Python;
   - prueba el punto con las coordenadas intercambiadas de las que no dieron un municipio;
   - publica con un INSERT ... SELECT el resultado de cada cuenta, lo cuenta y cierra EXITOSA.
   Si algo falla, se revierte todo y la ejecucion queda FALLIDA: nunca una geografia a medias.

Lo que hace segura la entrega repetida es lo mismo que en los demas motores: el bloqueo de la
ejecucion, sus estados terminales, la transaccion todo o nada, los indices unicos (una EXITOSA y una
EN_PROCESO por firma) y que solo publica el dueno vigente de su trabajo. Las coordenadas no salen a
la bitacora ni al detalle: solo conteos.
"""

from __future__ import annotations

import io
import logging
import traceback
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import pyarrow.csv as pcsv
import pyarrow.parquet as pq
from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session, select

from motor_cartera.config import Config
from motor_cartera.config import config as config_del_entorno
from motor_cartera.contratos.cartera_v2 import CONTRATO_V2
from motor_cartera.db.copia import copiar_csv
from motor_cartera.db.modelos import (
    CONTEOS_DE_GEOGRAFIA,
    ArtefactoFuente,
    CorteCanonico,
    DatasetConformado,
    EjecucionGeografia,
    EstadoGeografia,
    EstadoVersionGeografia,
    ResultadoEjecucionGeografia,
    TipoTrabajo,
    VersionGeografia,
    ahora,
)
from motor_cartera.db.sesion import insertar_en_savepoint, restriccion, sesion
from motor_cartera.fuentes.almacen import ArtefactoCorrupto, ArtefactoFaltante
from motor_cartera.fuentes.artefactos import almacen_de
from motor_cartera.fuentes.conformado import COLUMNA_FILA, COLUMNA_HOJA
from motor_cartera.geografia import reglas
from motor_cartera.geografia.referencia import versiones_espaciales
from motor_cartera.geografia.reglas import (
    LATITUD_MAXIMA,
    LATITUD_MINIMA,
    LONGITUD_MAXIMA,
    LONGITUD_MINIMA,
    VERSION_MOTOR,
    Motivo,
)
from motor_cartera.geografia.reglas import (
    EstadoGeografia as Estado,
)
from motor_cartera.historia import carga
from motor_cartera.ingesta.fuente_oficial import Cronometro
from motor_cartera.orquestacion import cola

log = logging.getLogger(__name__)

INDICE_EXITOSA = "ux_geografia_exitosa"
INDICE_EN_PROCESO = "ux_geografia_en_proceso"

COLUMNAS_LEIDAS = (COLUMNA_FILA, COLUMNA_HOJA, "CLIENTE_UNICO", "LATITUD", "LONGITUD")
"""Lo unico que geografia/v1 lee del dataset conformado: ni nombres, ni domicilios, ni telefonos."""


# --- errores al abrir -----------------------------------------------------------------------------


class EntradaNoGeolocalizable(Exception):
    """Una peticion de geografia que no se puede abrir: no registra nada. `codigo` es estable."""

    codigo = "ENTRADA_NO_GEOLOCALIZABLE"
    estado_http = 422


class CorteNoEncontrado(EntradaNoGeolocalizable):
    codigo = "CORTE_NO_ENCONTRADO"
    estado_http = 404


class VersionGeografiaNoEncontrada(EntradaNoGeolocalizable):
    codigo = "GEOGRAFIA_VERSION_NO_ENCONTRADA"
    estado_http = 404


class VersionGeografiaNoPublicada(EntradaNoGeolocalizable):
    codigo = "GEOGRAFIA_VERSION_NO_PUBLICADA"


class GeografiaYaGenerada(Exception):
    """Otra ejecucion ya publico exactamente las mismas entradas (la misma firma)."""

    def __init__(self, previa: EjecucionGeografia) -> None:
        super().__init__(
            f"Esas entradas ya tienen su geografia: la ejecucion {previa.geografia_run_id} "
            f"(firma {previa.firma_inputs[:12]}...)."
        )
        self.previa = previa


class GeografiaEnProceso(Exception):
    """Esas mismas entradas ya se estan resolviendo: hay una ejecucion EN_PROCESO."""

    def __init__(self, activa: EjecucionGeografia) -> None:
        super().__init__(
            f"Esas entradas ya se estan resolviendo: la ejecucion {activa.geografia_run_id}."
        )
        self.activa = activa


class _NoSePublica(Exception):
    def __init__(self, resultado: ResultadoEjecucionGeografia, mensaje: str) -> None:
        super().__init__(mensaje)
        self.resultado = resultado


# --- abrir ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Entradas:
    """Lo que fija una ejecucion: el corte, su dataset y la version de la referencia."""

    corte: CorteCanonico
    dataset: DatasetConformado
    version: VersionGeografia
    firma: str


def entradas(s: Session, corte_id: UUID, geografia_version_id: UUID) -> Entradas:
    """Las entradas de una geografia, comprobadas, con su firma. Levanta CorteNoEncontrado,
    VersionGeografiaNoEncontrada o VersionGeografiaNoPublicada."""
    corte = s.exec(select(CorteCanonico).where(CorteCanonico.corte_id == corte_id)).one_or_none()
    if corte is None:
        raise CorteNoEncontrado(f"No existe un corte canonico con corte_id {corte_id}.")
    version = s.exec(
        select(VersionGeografia).where(
            VersionGeografia.geografia_version_id == geografia_version_id
        )
    ).one_or_none()
    if version is None:
        raise VersionGeografiaNoEncontrada(
            f"No existe una version de la geografia con geografia_version_id "
            f"{geografia_version_id}."
        )
    if version.estado != EstadoVersionGeografia.PUBLICADA:
        raise VersionGeografiaNoPublicada(
            f"La version {geografia_version_id} esta {version.estado}: solo una referencia "
            "PUBLICADA resuelve geografia."
        )
    dataset = s.get_one(DatasetConformado, corte.dataset_conformado_id)
    firma = reglas.firma_inputs(
        geografia_version_id=version.geografia_version_id,
        sha256_referencia=version.sha256,
        capa=version.capa,
        corte_id=corte.corte_id,
        firma_corte=corte.firma_contenido,
        dataset_id=dataset.dataset_id,
        firma_dataset=dataset.firma_contenido,
    )
    return Entradas(corte, dataset, version, firma)


def abrir(
    s: Session,
    corte_id: UUID,
    geografia_version_id: UUID,
    *,
    max_intentos: int,
    tomado_por: str | None = None,
    lease_segundos: float | None = None,
) -> EjecucionGeografia:
    """La ejecucion EN_PROCESO de geografia/v1 sobre un corte, con su trabajo GEOGRAFIA, en la
    transaccion de quien llama: envia los INSERT y no confirma. Con `tomado_por`, el trabajo nace
    tomado por ese proceso, que la resuelve en primer plano. Levanta, sin registrar nada, lo que
    levanta `entradas`, GeografiaYaGenerada y GeografiaEnProceso."""
    e = entradas(s, corte_id, geografia_version_id)
    previa = _de_firma(s, e.firma, EstadoGeografia.EXITOSA)
    if previa is not None:
        raise GeografiaYaGenerada(previa)
    activa = _de_firma(s, e.firma, EstadoGeografia.EN_PROCESO)
    if activa is not None:
        raise GeografiaEnProceso(activa)
    ejecucion = EjecucionGeografia(
        corte_canonico_id=e.corte.id,
        dataset_conformado_id=e.dataset.id,
        version_geografia_id=e.version.id,
        version_motor=VERSION_MOTOR,
        firma_inputs=e.firma,
        cuentas_objetivo=e.corte.cuentas,
    )
    error = insertar_en_savepoint(s, ejecucion)
    if error is not None:
        if restriccion(error) != INDICE_EN_PROCESO:
            raise error
        activa = _de_firma(s, e.firma, EstadoGeografia.EN_PROCESO)
        if (
            activa is None
        ):  # pragma: no cover - la que gano ya termino entre el rechazo y la revision
            raise error
        raise GeografiaEnProceso(activa) from error
    cola.crear(
        s,
        TipoTrabajo.GEOGRAFIA,
        ejecucion.id,
        max_intentos=max_intentos,
        tomado_por=tomado_por,
        lease_segundos=lease_segundos,
    )
    log.info(
        "geografia %s abierta: corte %s con la referencia %s",
        ejecucion.geografia_run_id,
        e.corte.corte_id,
        e.version.geografia_version_id,
    )
    return ejecucion


def _de_firma(s: Session, firma: str, estado: EstadoGeografia) -> EjecucionGeografia | None:
    consulta = select(EjecucionGeografia).where(
        EjecucionGeografia.firma_inputs == firma, EjecucionGeografia.estado == estado
    )
    if estado == EstadoGeografia.EN_PROCESO:
        consulta = consulta.with_for_update()
    return s.exec(consulta).first()


# --- geolocalizar ---------------------------------------------------------------------------------

ANTES_DEL_STAGING = "ANTES_DEL_STAGING"
DESPUES_DEL_COPY = "DESPUES_DEL_COPY"
DESPUES_DEL_PUNTO_EN_POLIGONO = "DESPUES_DEL_PUNTO_EN_POLIGONO"
ANTES_DE_PUBLICAR = "ANTES_DE_PUBLICAR"
DESPUES_DE_LOS_RESULTADOS = "DESPUES_DE_LOS_RESULTADOS"
ANTES_DE_CERRAR = "ANTES_DE_CERRAR"
PUNTOS_DE_FALLA = (
    ANTES_DEL_STAGING,
    DESPUES_DEL_COPY,
    DESPUES_DEL_PUNTO_EN_POLIGONO,
    ANTES_DE_PUBLICAR,
    DESPUES_DE_LOS_RESULTADOS,
    ANTES_DE_CERRAR,
)


def _punto_de_falla(nombre: str) -> None:
    """Un punto donde una prueba puede inyectar una falla. En produccion no hace nada."""


@dataclass
class _Avance:
    leidos: int = 0


def ejecutar_geografia(ejecucion_id: int, *, config: Config | None = None) -> None:
    """Lo que ejecuta un trabajo GEOGRAFIA. Si la ejecucion ya termino no hace nada. Si el almacen
    no tiene el Parquet, es un error del worker y no de la geografia (el volumen puede no estar
    montado): levanta ArtefactoFaltante sin tocar la ejecucion, y el trabajo se reintenta."""
    config = config or config_del_entorno
    with sesion() as s:
        ejecucion = s.get_one(EjecucionGeografia, ejecucion_id)
        if ejecucion.estado != EstadoGeografia.EN_PROCESO:
            log.info("geografia %s ya termino %s", ejecucion.geografia_run_id, ejecucion.estado)
            return
        sha256 = _artefacto(s, ejecucion.dataset_conformado_id).sha256
    if not almacen_de(config).existe(sha256):
        raise ArtefactoFaltante(sha256)
    geolocalizar(ejecucion_id, config=config)


def _artefacto(s: Session, dataset_id: int) -> ArtefactoFuente:
    return s.exec(
        select(ArtefactoFuente)
        .join(DatasetConformado, DatasetConformado.artefacto_conformado_id == ArtefactoFuente.id)
        .where(DatasetConformado.id == dataset_id)
    ).one()


def geolocalizar(
    ejecucion_id: int, *, config: Config | None = None, cronometro: Cronometro | None = None
) -> None:
    """Resuelve la geografia del corte de la ejecucion y la deja en un estado terminal: con su fila
    bloqueada hasta el commit, todo en una transaccion y en un savepoint; si falla, queda FALLIDA
    sin soltar su fila, y solo publica o falla el dueno vigente de su trabajo. Levanta
    GeografiaYaGenerada si otra ejecucion publico primero la misma firma, ArtefactoFaltante,
    TrabajoAjeno, y los errores transitorios de la base, con la ejecucion EN_PROCESO para que la
    cola la reintente."""
    config = config or config_del_entorno
    cronometro = cronometro or Cronometro()
    with sesion() as s:
        ejecucion = s.exec(
            select(EjecucionGeografia)
            .where(EjecucionGeografia.id == ejecucion_id)
            .with_for_update()
        ).one()
        if ejecucion.estado != EstadoGeografia.EN_PROCESO:
            log.warning("geografia %s ya termino %s", ejecucion.geografia_run_id, ejecucion.estado)
            return
        etiqueta, firma = ejecucion.geografia_run_id, ejecucion.firma_inputs
        try:
            with s.begin_nested():
                _geolocalizar(s, ejecucion, config, cronometro)
        except _NoSePublica as exc:
            resultado, motivo = exc.resultado, str(exc)
        except ArtefactoCorrupto as exc:
            resultado, motivo = ResultadoEjecucionGeografia.ARTEFACTO_CORRUPTO, str(exc)
        except carga.DatosInconsistentes as exc:
            resultado, motivo = ResultadoEjecucionGeografia.DATOS_INCONSISTENTES, str(exc)
        except ArtefactoFaltante:
            s.rollback()
            raise
        except IntegrityError as exc:
            if restriccion(exc) == INDICE_EXITOSA:
                resultado, motivo = (
                    ResultadoEjecucionGeografia.YA_GENERADA,
                    "Otra ejecucion publico la misma firma de entrada mientras esta se procesaba; "
                    "no se publica dos veces.",
                )
            else:
                log.error(
                    "geografia %s: violacion de integridad inesperada%s", etiqueta, _traza(exc)
                )
                resultado, motivo = (
                    ResultadoEjecucionGeografia.ERROR_INTERNO,
                    "Error interno al publicar; ver la bitacora del servicio.",
                )
        except OperationalError:
            log.warning("geografia %s: error transitorio; sigue EN_PROCESO", etiqueta)
            raise
        except Exception as exc:
            log.error("geografia %s: error inesperado%s", etiqueta, _traza(exc))
            resultado, motivo = (
                ResultadoEjecucionGeografia.ERROR_INTERNO,
                f"Error interno ({type(exc).__name__}); ver la bitacora.",
            )
        else:
            cola.confirmar_dueno(s)
            with cronometro.fase("commit"):
                s.commit()
            return
        _fallar(s, ejecucion_id, etiqueta, resultado, motivo)
        if resultado == ResultadoEjecucionGeografia.YA_GENERADA:
            raise GeografiaYaGenerada(_de_firma(s, firma, EstadoGeografia.EXITOSA))


def _traza(exc: BaseException) -> str:
    """El tipo de un error, su SQLSTATE si es de la base y sus marcos, sin su mensaje: el de un
    error de PostgreSQL puede citar los valores de la fila que lo provoco (una violacion de un CHECK
    dice "Failing row contains (...)"), y esos valores pueden ser la coordenada de una cuenta, que
    no sale a la bitacora."""
    sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
    tipo = type(exc).__name__ + (f", SQLSTATE {sqlstate}" if sqlstate else "")
    return f" ({tipo})\n" + "".join(traceback.format_tb(exc.__traceback__)).rstrip()


def _geolocalizar(
    s: Session, ejecucion: EjecucionGeografia, config: Config, cronometro: Cronometro
) -> None:
    if ejecucion.version_motor != VERSION_MOTOR:
        raise _NoSePublica(
            ResultadoEjecucionGeografia.VERSION_NO_SOPORTADA,
            f"La ejecucion pide {ejecucion.version_motor} y este servicio resuelve "
            f"{VERSION_MOTOR}; no se publico nada.",
        )
    version = s.get_one(VersionGeografia, ejecucion.version_geografia_id)
    if version.estado != EstadoVersionGeografia.PUBLICADA:
        raise _NoSePublica(
            ResultadoEjecucionGeografia.REFERENCIA_NO_PUBLICADA,
            f"La version {version.geografia_version_id} de la referencia esta {version.estado}; "
            "no se publico nada.",
        )
    corte = s.get_one(CorteCanonico, ejecucion.corte_canonico_id)
    dataset = s.get_one(DatasetConformado, ejecucion.dataset_conformado_id)
    if corte.dataset_conformado_id != dataset.id or dataset.contrato != CONTRATO_V2.version:
        raise _NoSePublica(
            ResultadoEjecucionGeografia.DATOS_INCONSISTENTES,
            "El dataset de la ejecucion no es el dataset de cartera/v2 que produjo su corte; no se "
            "publico nada.",
        )
    _punto_de_falla(ANTES_DEL_STAGING)
    avance = _Avance()
    parametros = {
        "ejecucion": ejecucion.id,
        "corte": corte.id,
        "version": version.id,
        "despacho": corte.despacho_id,
        "cartera": corte.cartera_id,
    }
    s.execute(text(SQL_STAGING))
    artefacto = s.get_one(ArtefactoFuente, dataset.artefacto_conformado_id)
    almacen = almacen_de(config)
    with cronometro.fase("verificacion"):
        almacen.verificar(artefacto.sha256, artefacto.tamano_bytes)
    with almacen.como_archivo(artefacto.sha256) as ruta:
        archivo = carga.abrir_parquet(ruta, CONTRATO_V2, dataset.filas)
        try:
            with cronometro.fase("staging"):
                copiar_csv(
                    s,
                    STAGING,
                    ("source_row", "source_sheet", "cliente_unico", "latitud", "longitud"),
                    _bloques(archivo, config.filas_por_lote, avance),
                )
        finally:
            archivo.close()
    _punto_de_falla(DESPUES_DEL_COPY)
    if avance.leidos != dataset.filas:
        raise _NoSePublica(
            ResultadoEjecucionGeografia.DATOS_INCONSISTENTES,
            f"El dataset dice {dataset.filas:,} registros y se leyeron {avance.leidos:,}; no se "
            "publico nada.",
        )
    with cronometro.fase("universo"):
        _ejecutar(s, SQL_UNIVERSO, parametros)
        unidas, distintas, ajenas = s.execute(
            text(
                "SELECT count(*), count(DISTINCT cuenta), "
                "count(*) FILTER (WHERE source_row <> fila_snapshot) FROM geo_cuenta"
            )
        ).one()
    if not unidas == distintas == corte.cuentas == avance.leidos or ajenas:
        raise _NoSePublica(
            ResultadoEjecucionGeografia.DATOS_INCONSISTENTES,
            f"El corte tiene {corte.cuentas:,} cuentas, su dataset {avance.leidos:,} registros y "
            f"se unieron {unidas:,} ({distintas:,} cuentas distintas, {ajenas:,} de otra fila); no "
            "se publico nada.",
        )
    with cronometro.fase("clasificacion"):
        _ejecutar(s, SQL_CLASIFICACION, parametros)
    with cronometro.fase("punto_en_poligono"):
        _ejecutar(s, SQL_PUNTO_EN_POLIGONO, parametros)
    _punto_de_falla(DESPUES_DEL_PUNTO_EN_POLIGONO)
    with cronometro.fase("invertidos"):
        _ejecutar(s, SQL_INVERTIDOS, parametros)
    _punto_de_falla(ANTES_DE_PUBLICAR)
    with cronometro.fase("publicacion"):
        publicados = s.execute(text(SQL_RESULTADOS), parametros).rowcount
    _punto_de_falla(DESPUES_DE_LOS_RESULTADOS)
    with cronometro.fase("conteos"):
        conteos, distribucion = _contar(s, ejecucion)
    if publicados != corte.cuentas or sum(conteos.values()) != corte.cuentas:
        raise _NoSePublica(
            ResultadoEjecucionGeografia.DATOS_INCONSISTENTES,
            f"El corte tiene {corte.cuentas:,} cuentas y se publicaron {publicados:,} resultados; "
            "no se publico ninguno.",
        )
    distribucion["versiones"] = versiones_espaciales(s)
    _punto_de_falla(ANTES_DE_CERRAR)
    _cerrar(s, ejecucion, version, corte, conteos, distribucion)


def _bloques(archivo: pq.ParquetFile, filas_por_lote: int, avance: _Avance) -> Iterator[bytes]:
    """Cada lote del Parquet, solo con las cinco columnas que se leen, como CSV para el COPY."""
    opciones = pcsv.WriteOptions(include_header=False)
    for lote in archivo.iter_batches(batch_size=filas_por_lote, columns=list(COLUMNAS_LEIDAS)):
        salida = io.BytesIO()
        pcsv.write_csv(lote.select(list(COLUMNAS_LEIDAS)), salida, write_options=opciones)
        avance.leidos += lote.num_rows
        yield salida.getvalue()


def _ejecutar(s: Session, sentencias: tuple[str, ...], parametros: dict[str, Any]) -> None:
    for sentencia in sentencias:
        s.execute(text(sentencia), parametros)


# --- el SQL ---------------------------------------------------------------------------------------

STAGING = "geo_staging"

SQL_STAGING = (
    f"CREATE TEMP TABLE {STAGING} (source_row bigint NOT NULL, source_sheet varchar(255), "
    "cliente_unico varchar(20) NOT NULL, latitud double precision, longitud double precision) "
    "ON COMMIT DROP"
)

SQL_UNIVERSO = (
    # Cada fila del dataset con su cuenta canonica y su snapshot del corte: el municipio que
    # resolvio la fuente es el del snapshot, con el que se publico el corte.
    "CREATE TEMP TABLE geo_cuenta ON COMMIT DROP AS "
    "SELECT s.cuenta_canonica_id AS cuenta, st.source_row, st.source_sheet, st.latitud, "
    "st.longitud, s.cve_entidad || s.cve_municipio AS cvegeo_fuente, "
    "s.source_row AS fila_snapshot "
    f"FROM {STAGING} st JOIN cuenta_canonica c ON c.despacho_id = :despacho "
    "AND c.cartera_id = :cartera AND c.cliente_unico = st.cliente_unico "
    "JOIN snapshot_cuenta s ON s.corte_canonico_id = :corte AND s.cuenta_canonica_id = c.id",
    "CREATE INDEX ON geo_cuenta (cuenta)",
    "ANALYZE geo_cuenta",
)


def _sql_valida(columna: str, minimo: float, maximo: float) -> str:
    # En PostgreSQL, NaN es mayor que cualquier numero y los infinitos quedan fuera de cualquier
    # rango finito: BETWEEN tambien descarta lo que no es finito.
    return f"({columna} BETWEEN {minimo} AND {maximo})"


def _sql_no_finita(columna: str) -> str:
    return f"({columna} = 'NaN'::float8 OR {columna} IN ('Infinity'::float8, '-Infinity'::float8))"


_LATITUD_VALIDA = _sql_valida("latitud", LATITUD_MINIMA, LATITUD_MAXIMA)
_LONGITUD_VALIDA = _sql_valida("longitud", LONGITUD_MINIMA, LONGITUD_MAXIMA)


def _motivo(codigo: Motivo, **detalle: str) -> str:
    pares = [f"'codigo', '{codigo.value}'", *(f"'{k}', {v}" for k, v in detalle.items())]
    return f"jsonb_build_object({', '.join(pares)})"


MOTIVOS_DE_COORDENADAS = (
    f"CASE WHEN latitud IS NULL THEN {_motivo(Motivo.SIN_LATITUD)} END",
    f"CASE WHEN longitud IS NULL THEN {_motivo(Motivo.SIN_LONGITUD)} END",
    "CASE WHEN latitud IS NOT NULL AND longitud IS NOT NULL THEN CASE "
    f"WHEN {_sql_no_finita('latitud')} THEN {_motivo(Motivo.LATITUD_NO_FINITA)} "
    f"WHEN NOT {_LATITUD_VALIDA} THEN {_motivo(Motivo.LATITUD_FUERA_DE_RANGO)} END END",
    "CASE WHEN latitud IS NOT NULL AND longitud IS NOT NULL THEN CASE "
    f"WHEN {_sql_no_finita('longitud')} THEN {_motivo(Motivo.LONGITUD_NO_FINITA)} "
    f"WHEN NOT {_LONGITUD_VALIDA} THEN {_motivo(Motivo.LONGITUD_FUERA_DE_RANGO)} END END",
    f"CASE WHEN {_LATITUD_VALIDA} AND {_LONGITUD_VALIDA} AND latitud = 0 AND longitud = 0 "
    f"THEN {_motivo(Motivo.PUNTO_0_0)} END",
)
"""Los motivos que descartan una coordenada, en el orden de `reglas.coordenadas`."""

SQL_CLASIFICACION = (
    # Que coordenadas no sirven, y por que; las que sirven, con su punto. X es la longitud y Y la
    # latitud: ST_MakePoint(longitud, latitud).
    "CREATE TEMP TABLE geo_clase ON COMMIT DROP AS SELECT *, CASE "
    f"WHEN latitud IS NULL AND longitud IS NULL THEN '{Estado.SIN_COORDENADAS}' "
    f"WHEN latitud IS NULL OR longitud IS NULL THEN '{Estado.COORDENADAS_INCOMPLETAS}' "
    f"WHEN NOT {_LATITUD_VALIDA} OR NOT {_LONGITUD_VALIDA} OR (latitud = 0 AND longitud = 0) "
    f"THEN '{Estado.COORDENADAS_INVALIDAS}' END AS descarte FROM geo_cuenta",
    "CREATE UNIQUE INDEX ON geo_clase (cuenta)",
    "CREATE TEMP TABLE geo_punto ON COMMIT DROP AS SELECT cuenta, "
    "ST_SetSRID(ST_MakePoint(longitud, latitud), 4326) AS punto FROM geo_clase "
    "WHERE descarte IS NULL",
    "CREATE INDEX ON geo_punto USING gist (punto)",
    "CREATE UNIQUE INDEX ON geo_punto (cuenta)",
    "ANALYZE geo_punto",
)

SQL_PUNTO_EN_POLIGONO = (
    # Los municipios de la version que cubren cada punto (ST_Covers: el borde cuenta). Se recorren
    # los municipios y se entra por el indice GiST de los puntos: cada poligono se prepara una vez.
    "CREATE TEMP TABLE geo_cubre ON COMMIT DROP AS SELECT p.cuenta, m.id AS municipio, m.cvegeo "
    "FROM municipio_geografico m JOIN geo_punto p ON ST_Covers(m.geometria, p.punto) "
    "WHERE m.version_geografia_id = :version",
    "CREATE TEMP TABLE geo_municipio ON COMMIT DROP AS SELECT cuenta, count(*) AS cuantos, "
    "min(municipio) AS municipio, min(cvegeo) AS cvegeo, array_agg(cvegeo ORDER BY cvegeo) "
    "AS cvegeos FROM geo_cubre GROUP BY cuenta",
    "CREATE UNIQUE INDEX ON geo_municipio (cuenta)",
    "ANALYZE geo_municipio",
)

SQL_INVERTIDOS = (
    # Las coordenadas que no dieron un municipio (invalidas o fuera de la referencia), con la
    # latitud y la longitud intercambiadas, si ese punto es valido: ST_MakePoint(latitud, longitud).
    # Solo para el motivo POSIBLE_ORDEN_INVERTIDO: nada se corrige.
    "CREATE TEMP TABLE geo_invertido ON COMMIT DROP AS SELECT c.cuenta, "
    "ST_SetSRID(ST_MakePoint(c.latitud, c.longitud), 4326) AS punto FROM geo_clase c "
    "LEFT JOIN geo_municipio g ON g.cuenta = c.cuenta "
    f"WHERE (c.descarte = '{Estado.COORDENADAS_INVALIDAS}' OR (c.descarte IS NULL "
    "AND g.cuenta IS NULL)) "
    f"AND c.latitud BETWEEN {LONGITUD_MINIMA} AND {LONGITUD_MAXIMA} "
    f"AND c.longitud BETWEEN {LATITUD_MINIMA} AND {LATITUD_MAXIMA} "
    "AND NOT (c.latitud = 0 AND c.longitud = 0)",
    "CREATE INDEX ON geo_invertido USING gist (punto)",
    "ANALYZE geo_invertido",
    "CREATE TEMP TABLE geo_invertido_cubre ON COMMIT DROP AS SELECT i.cuenta, "
    "array_agg(m.cvegeo ORDER BY m.cvegeo) AS cvegeos FROM municipio_geografico m "
    "JOIN geo_invertido i ON ST_Covers(m.geometria, i.punto) "
    "WHERE m.version_geografia_id = :version GROUP BY i.cuenta",
    "CREATE UNIQUE INDEX ON geo_invertido_cubre (cuenta)",
)

SQL_RESULTADOS = (
    # La geografia de cada cuenta del corte, con su estado, su comparacion con la fuente y sus
    # motivos, en el orden de `reglas.clasificar`; en orden de cuenta, para llenar los indices en
    # orden.
    "WITH base AS (SELECT c.*, g.cuantos, g.municipio, g.cvegeo AS cvegeo_espacial, g.cvegeos, "
    "i.cvegeos AS cvegeos_invertido, mf.id IS NOT NULL AS fuente_en_referencia, p.punto "
    "FROM geo_clase c LEFT JOIN geo_municipio g ON g.cuenta = c.cuenta "
    "LEFT JOIN geo_invertido_cubre i ON i.cuenta = c.cuenta "
    "LEFT JOIN geo_punto p ON p.cuenta = c.cuenta "
    "LEFT JOIN municipio_geografico mf ON mf.version_geografia_id = :version "
    "AND mf.cvegeo = c.cvegeo_fuente), "
    "clasificada AS (SELECT base.*, CASE WHEN descarte IS NOT NULL THEN descarte "
    f"WHEN cuantos IS NULL THEN '{Estado.FUERA_DE_REFERENCIA}' "
    f"WHEN cuantos > 1 THEN '{Estado.MUNICIPIO_AMBIGUO}' "
    "WHEN cvegeo_fuente IS NOT NULL AND cvegeo_fuente <> cvegeo_espacial "
    f"THEN '{Estado.DISCREPANCIA_MUNICIPIO}' ELSE '{Estado.VALIDA}' END AS estado FROM base) "
    "INSERT INTO resultado_geografia_cuenta (latitud_observada, longitud_observada, source_row, "
    "ejecucion_geografia_id, cuenta_canonica_id, corte_canonico_id, municipio_geografico_id, "
    "estado_geografia, municipio_fuente_resuelto, coincide_municipio, punto, source_sheet, "
    "motivos) "
    "SELECT latitud, longitud, source_row, :ejecucion, cuenta, :corte, "
    f"CASE WHEN estado IN ('{Estado.VALIDA}', '{Estado.DISCREPANCIA_MUNICIPIO}') "
    "THEN municipio END, estado, cvegeo_fuente, "
    "CASE WHEN cuantos = 1 AND cvegeo_fuente IS NOT NULL THEN cvegeo_fuente = cvegeo_espacial "
    "END, CASE WHEN descarte IS NULL THEN punto END, source_sheet, "
    "to_jsonb(array_remove(ARRAY["
    + ", ".join(MOTIVOS_DE_COORDENADAS)
    + f", CASE WHEN estado = '{Estado.FUERA_DE_REFERENCIA}' "
    f"THEN {_motivo(Motivo.SIN_MUNICIPIO_EN_REFERENCIA)} END, "
    f"CASE WHEN estado = '{Estado.MUNICIPIO_AMBIGUO}' "
    f"THEN {_motivo(Motivo.VARIOS_MUNICIPIOS, cvegeo='to_jsonb(cvegeos)')} END, "
    "CASE WHEN descarte IS NULL AND cuantos = 1 "
    f"THEN {_motivo(Motivo.MUNICIPIO_UNICO, cvegeo='cvegeo_espacial')} END, "
    "CASE WHEN descarte IS NULL AND cuantos = 1 AND cvegeo_fuente IS NULL "
    f"THEN {_motivo(Motivo.FUENTE_SIN_MUNICIPIO)} END, "
    "CASE WHEN descarte IS NULL AND cuantos = 1 AND cvegeo_fuente = cvegeo_espacial "
    f"THEN {_motivo(Motivo.COINCIDE_CON_FUENTE, cvegeo='cvegeo_espacial')} END, "
    f"CASE WHEN estado = '{Estado.DISCREPANCIA_MUNICIPIO}' THEN "
    + _motivo(
        Motivo.DISCREPA_DE_FUENTE,
        cvegeo_fuente="cvegeo_fuente",
        cvegeo_espacial="cvegeo_espacial",
        fuente_en_referencia="fuente_en_referencia",
    )
    + " END, "
    f"CASE WHEN estado IN ('{Estado.COORDENADAS_INVALIDAS}', '{Estado.FUERA_DE_REFERENCIA}') "
    "AND cvegeos_invertido IS NOT NULL "
    f"THEN {_motivo(Motivo.POSIBLE_ORDEN_INVERTIDO, cvegeo='to_jsonb(cvegeos_invertido)')} END"
    "]::jsonb[], NULL)) FROM clasificada ORDER BY cuenta"
)


# --- contar y cerrar ------------------------------------------------------------------------------


def _contar(s: Session, ejecucion: EjecucionGeografia) -> tuple[dict[str, int], dict[str, Any]]:
    """Cuantas cuentas quedaron en cada estado, cuantas con cada motivo y cada comparacion, contado
    en la transaccion que lo publico."""
    parametros = {"ejecucion": ejecucion.id}
    por_estado = dict(
        s.execute(
            text(
                "SELECT estado_geografia, count(*) FROM resultado_geografia_cuenta "
                "WHERE ejecucion_geografia_id = :ejecucion GROUP BY 1"
            ),
            parametros,
        ).all()
    )
    por_motivo = dict(
        s.execute(
            text(
                "SELECT m ->> 'codigo', count(*) FROM resultado_geografia_cuenta r, "
                "jsonb_array_elements(r.motivos) m WHERE r.ejecucion_geografia_id = :ejecucion "
                "GROUP BY 1 ORDER BY 1"
            ),
            parametros,
        ).all()
    )
    por_comparacion = dict(
        s.execute(
            text(
                "SELECT CASE coincide_municipio WHEN true THEN 'COINCIDE' WHEN false THEN "
                "'DISCREPA' ELSE 'NO_COMPARABLE' END, count(*) FROM resultado_geografia_cuenta "
                "WHERE ejecucion_geografia_id = :ejecucion GROUP BY 1"
            ),
            parametros,
        ).all()
    )
    conteos = {columna: por_estado.get(estado.value, 0) for estado, columna in _COLUMNAS.items()}
    distribucion = {
        "estado_geografia": {e.value: por_estado.get(e.value, 0) for e in Estado},
        "comparacion_municipio": {
            c.value: por_comparacion.get(c.value, 0) for c in reglas.Comparacion
        },
        "motivos": por_motivo,
    }
    return conteos, distribucion


_COLUMNAS = reglas.CONTEO_DE_ESTADO
assert set(_COLUMNAS.values()) == set(CONTEOS_DE_GEOGRAFIA)


def _cerrar(
    s: Session,
    ejecucion: EjecucionGeografia,
    version: VersionGeografia,
    corte: CorteCanonico,
    conteos: dict[str, int],
    distribucion: dict[str, Any],
) -> None:
    """EXITOSA, con sus conteos y su distribucion, en la transaccion que publico. Envia el cambio
    pero no confirma: una carrera con otra EXITOSA de la misma firma se descubre aqui."""
    for columna, valor in conteos.items():
        setattr(ejecucion, columna, valor)
    ejecucion.estado = EstadoGeografia.EXITOSA
    ejecucion.resultado = ResultadoEjecucionGeografia.GEOGRAFIA_PUBLICADA.value
    ejecucion.distribucion = distribucion
    ejecucion.detalle = (
        f"Se resolvio la geografia de {corte.cuentas:,} cuentas del corte del "
        f"{corte.fecha_corte.isoformat()} con {version.producto} ({version.edicion}): "
        f"{conteos['validas']:,} validas, {conteos['sin_coordenadas']:,} sin coordenadas, "
        f"{conteos['incompletas']:,} incompletas, {conteos['invalidas']:,} invalidas, "
        f"{conteos['fuera_cobertura']:,} fuera de la referencia, {conteos['ambiguas']:,} en mas "
        f"de un municipio y {conteos['discrepantes']:,} que discrepan de la fuente. Ninguna "
        f"coordenada se corrigio. {VERSION_MOTOR}."
    )
    ejecucion.terminada_en = ahora()
    s.add(ejecucion)
    s.flush()


def _fallar(
    s: Session,
    ejecucion_id: int,
    etiqueta: UUID,
    resultado: ResultadoEjecucionGeografia,
    motivo: str,
) -> None:
    """FALLIDA, con su fila todavia bloqueada, solo si quien la ejecuta sigue siendo el dueno de su
    trabajo; si no, TrabajoAjeno sin cambiar nada. No publica nada: lo de este intento ya lo quito
    el rollback del savepoint."""
    try:
        cola.confirmar_dueno(s)
    except cola.TrabajoAjeno:
        s.rollback()
        log.warning("geografia %s: su trabajo ya no es de este worker: %s", etiqueta, motivo)
        raise
    registrado = s.execute(
        update(EjecucionGeografia)
        .where(
            EjecucionGeografia.id == ejecucion_id,
            EjecucionGeografia.estado == EstadoGeografia.EN_PROCESO,
        )
        .values(
            estado=EstadoGeografia.FALLIDA,
            resultado=resultado.value,
            detalle=motivo,
            terminada_en=ahora(),
            **dict.fromkeys(CONTEOS_DE_GEOGRAFIA, 0),
        )
        .execution_options(synchronize_session=False)
    ).rowcount
    s.commit()
    if registrado:
        log.warning("geografia %s fallida (%s): %s", etiqueta, resultado.value, motivo)
