"""Los esquemas de la geografia, de las bases operativas y de territorial/v2 en la API.

Ninguna respuesta lleva la coordenada observada de una cuenta: es informacion sensible para la
operacion, y la API general da su estado, su municipio, su base, su distancia y sus motivos. Las
bases si llevan su punto: son configuracion operacional, no datos de una persona.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt

from motor_cartera.api.esquemas import Pagina, Paginacion
from motor_cartera.db.modelos import (
    PATRON_CODIGO_BASE,
    PATRON_CVEGEO,
    EstadoGeografia,
    EstadoTerritorialV2,
    EstadoVersionGeografia,
)
from motor_cartera.geografia.reglas import EstadoGeografia as EstadoDeLaCuenta
from motor_cartera.territorial_v2.politica import EstadoAsignacion

AVISO_GEOGRAFIA = (
    "geografia/v1 dice que coordenada se observo, si sirve y en que municipio de la referencia "
    "cae; no corrige ninguna coordenada ni la sustituye por un centroide. Las areas "
    "geoestadisticas del INEGI no son la fuente oficial de la division politico-administrativa. "
    "Las coordenadas de las cuentas no salen por esta API."
)
AVISO_TERRITORIAL_V2 = (
    "territorial/v2 dice a que base operativa pertenece cada cuenta segun territorial-public-v2: "
    "la base activa mas cercana entre las que cubren su municipio. Es pertenencia territorial, no "
    "capacidad: no considera carga, gestores, rutas ni prioridad, y una geografia dudosa no se "
    "asigna."
)


# --- las versiones de la referencia ---------------------------------------------------------------


class VersionGeografiaRespuesta(BaseModel):
    """Una version importada de la referencia geografica. No trae sus geometrias."""

    geografia_version_id: UUID
    proveedor: str
    producto: str
    edicion: str
    nivel: str
    capa: str
    archivo: str
    sha256: str = Field(description="El SHA-256 del archivo oficial, tal como se descargo.")
    tamano_bytes: int
    srid: int = Field(description="En que se guardaron sus geometrias: EPSG:4326.")
    srid_origen: int = Field(description="En que venia la capa: EPSG:6372 en el INEGI.")
    estado: EstadoVersionGeografia
    municipios: int
    reparadas: int = Field(description="Cuantas geometrias invalidas se repararon al importar.")
    metadatos: dict[str, Any] = Field(
        description="La URL oficial, la licencia, las fechas del producto, la envolvente, las "
        "versiones de PostGIS, GEOS y PROJ, y cada reparacion con su motivo."
    )
    importada_en: datetime
    terminada_en: datetime | None
    detalle: str | None


class PaginaVersionesGeografia(Pagina[VersionGeografiaRespuesta]):
    pass


# --- las ejecuciones de geografia -----------------------------------------------------------------


class GeografiaEntrada(BaseModel):
    """Lo que fija una geografia: el corte y la version de la referencia, declarada."""

    model_config = ConfigDict(extra="forbid")

    corte_id: UUID = Field(description="El corte canonico cuyas cuentas se ubican.")
    geografia_version_id: UUID = Field(
        description="La version PUBLICADA de la referencia. No hay una por omision: nunca se usa "
        "la mas nueva sin declararla."
    )


class CorteRespuesta(BaseModel):
    corte_id: UUID
    fecha_corte: date


class ReferenciaRespuesta(BaseModel):
    geografia_version_id: UUID
    producto: str
    edicion: str
    sha256: str


class ConteosGeografia(BaseModel):
    cuentas_objetivo: int = Field(description="Las cuentas del corte: sus snapshots.")
    validas: int
    sin_coordenadas: int
    incompletas: int
    invalidas: int
    fuera_cobertura: int = Field(description="FUERA_DE_REFERENCIA.")
    ambiguas: int = Field(description="MUNICIPIO_AMBIGUO.")
    discrepantes: int = Field(description="DISCREPANCIA_MUNICIPIO.")


class EjecucionGeografiaRespuesta(BaseModel):
    """Una ejecucion de geografia/v1: sus entradas, su firma y lo que publico."""

    geografia_run_id: UUID
    estado: EstadoGeografia
    resultado: str | None = Field(
        description="GEOGRAFIA_PUBLICADA, YA_GENERADA, DATOS_INCONSISTENTES, ERROR_INTERNO... "
        "null mientras esta EN_PROCESO."
    )
    corte: CorteRespuesta
    referencia: ReferenciaRespuesta
    version_motor: str
    firma_inputs: str = Field(
        description="SHA-256 de sus entradas: las mismas entradas son la misma geografia."
    )
    conteos: ConteosGeografia
    distribucion: dict[str, Any] | None = Field(
        description="Por estado, por comparacion con la fuente y por motivo, y las versiones de "
        "PostGIS, GEOS y PROJ con que se resolvio."
    )
    trabajo_id: UUID | None
    iniciada_en: datetime
    terminada_en: datetime | None
    detalle: str | None
    aviso: str = Field(default=AVISO_GEOGRAFIA)


class PaginaEjecucionesGeografia(Pagina[EjecucionGeografiaRespuesta]):
    pass


class MunicipioRespuesta(BaseModel):
    cvegeo: str
    nombre_oficial: str = Field(description="El de la referencia, tal como lo publica.")


class ResultadoGeografiaRespuesta(BaseModel):
    """La geografia de una cuenta en una ejecucion. Sin coordenadas."""

    cuenta_id: UUID
    cliente_unico: str
    source_row: int = Field(description="La fila del archivo original de la que salio.")
    source_sheet: str | None
    estado_geografia: EstadoDeLaCuenta
    municipio: MunicipioRespuesta | None = Field(
        description="El municipio espacial, si el punto cae en exactamente uno."
    )
    municipio_fuente: str | None = Field(
        description="El CVEGEO que resolvio la fuente con ESTADO_CTE y POBLACION_CTE."
    )
    comparacion_municipio: str = Field(description="COINCIDE, DISCREPA o NO_COMPARABLE.")
    motivos: list[dict[str, Any]]


class ParametrosEjecuciones(Paginacion):
    """La paginacion de un listado de ejecuciones, con su filtro por corte."""

    corte_id: UUID | None = Field(default=None, description="Solo las de este corte.")


class ParametrosResultadosGeografia(Paginacion):
    estado_geografia: EstadoDeLaCuenta | None = Field(
        default=None, description="Solo las de ese estado, por su indice: p. ej. las anomalias."
    )
    municipio: str | None = Field(
        default=None,
        pattern=PATRON_CVEGEO,
        description="Solo las que caen en ese municipio (su CVEGEO), por su indice.",
    )


class PaginaResultadosGeografia(Pagina[ResultadoGeografiaRespuesta]):
    geografia_run_id: UUID


class GeografiaDeCuentaRespuesta(BaseModel):
    """La geografia de una cuenta en una ejecucion EXITOSA."""

    geografia_run_id: UUID
    corte: CorteRespuesta
    referencia: ReferenciaRespuesta
    estado_geografia: EstadoDeLaCuenta
    municipio: MunicipioRespuesta | None
    municipio_fuente: str | None
    comparacion_municipio: str
    motivos: list[dict[str, Any]]


class PaginaGeografiaDeCuenta(Pagina[GeografiaDeCuentaRespuesta]):
    cuenta_id: UUID
    cliente_unico: str


# --- las bases operativas -------------------------------------------------------------------------


class BaseEntrada(BaseModel):
    model_config = ConfigDict(extra="forbid")

    codigo: str = Field(pattern=PATRON_CODIGO_BASE, examples=["BASE_CENTRO"])
    nombre: str = Field(min_length=1, max_length=120)
    lat: StrictFloat | StrictInt = Field(description="Latitud en grados, EPSG:4326.")
    lon: StrictFloat | StrictInt = Field(description="Longitud en grados, EPSG:4326.")
    activa: StrictBool
    municipios: list[str] = Field(
        min_length=1, description="Los CVEGEO que puede atender: su cobertura, declarada."
    )


class VersionBasesEntrada(BaseModel):
    """Una version completa de la configuracion de bases."""

    model_config = ConfigDict(extra="forbid")

    nombre: str = Field(min_length=1, max_length=120)
    bases: list[BaseEntrada] = Field(min_length=1)


class BaseRespuesta(BaseModel):
    base_id: UUID
    codigo: str
    nombre: str
    activa: bool
    lat: float
    lon: float
    municipios: list[str]


class VersionBasesRespuesta(BaseModel):
    bases_version_id: UUID
    nombre: str
    version_configuracion: str
    hash_configuracion: str = Field(
        description="SHA-256 de la configuracion en forma canonica: el mismo contenido es la "
        "misma version."
    )
    estado: str
    bases: int
    activas: int
    coberturas: int
    creada_en: datetime
    reutilizada: bool = Field(
        default=False,
        description="True si la configuracion ya existia y se devolvio su version, sin crear otra.",
    )


class VersionBasesDetalleRespuesta(VersionBasesRespuesta):
    hash_verificado: bool = Field(
        description="Si el hash, vuelto a calcular con lo que hay en sus tablas, es el que se "
        "guardo: que nada cambio desde que se publico."
    )
    detalle_bases: list[BaseRespuesta]


class PaginaVersionesBases(Pagina[VersionBasesRespuesta]):
    pass


# --- territorial/v2 -------------------------------------------------------------------------------


class TerritorialV2Entrada(BaseModel):
    """Lo que fija una asignacion: el corte, su geografia EXITOSA y una version de bases, todos
    declarados."""

    model_config = ConfigDict(extra="forbid")

    corte_id: UUID
    geografia_run_id: UUID = Field(description="Una ejecucion de geografia EXITOSA de ese corte.")
    bases_version_id: UUID
    version_politica: str | None = Field(
        default=None, description="Por omision, territorial-public-v2, la unica que se aplica."
    )


class BasesDeLaEjecucion(BaseModel):
    bases_version_id: UUID
    nombre: str
    hash_configuracion: str


class ConteosTerritorialV2(BaseModel):
    cuentas_objetivo: int
    asignadas: int
    revision_geografica: int
    sin_base: int
    fuera_cobertura: int
    ambiguas: int = Field(description="AMBIGUA_BASE: un empate entre bases.")


class EjecucionTerritorialV2Respuesta(BaseModel):
    territorial_run_id: UUID
    estado: EstadoTerritorialV2
    resultado: str | None
    corte: CorteRespuesta
    geografia_run_id: UUID
    bases: BasesDeLaEjecucion
    version_motor: str
    version_politica: str
    hash_politica: str
    tolerancia_empate_m: Decimal | None = Field(
        description="La de su politica: dos bases a no mas de esta diferencia son un empate."
    )
    firma_inputs: str
    conteos: ConteosTerritorialV2
    distribucion: dict[str, Any] | None = Field(
        description="Por estado, por base (cuantas cuentas y su distancia mediana: es un "
        "diagnostico, no un balanceo) y por motivo."
    )
    trabajo_id: UUID | None
    iniciada_en: datetime
    terminada_en: datetime | None
    detalle: str | None
    aviso: str = Field(default=AVISO_TERRITORIAL_V2)


class PaginaEjecucionesTerritorialV2(Pagina[EjecucionTerritorialV2Respuesta]):
    pass


class BaseAsignadaRespuesta(BaseModel):
    base_id: UUID
    codigo: str
    nombre: str


class AsignacionV2Respuesta(BaseModel):
    """La asignacion de una cuenta. Sin coordenadas."""

    cuenta_id: UUID
    cliente_unico: str
    estado_asignacion: EstadoAsignacion
    estado_geografia: EstadoDeLaCuenta
    municipio: MunicipioRespuesta | None
    base: BaseAsignadaRespuesta | None
    distancia_base_m: Decimal | None
    motivos: list[dict[str, Any]]


class ParametrosAsignacionesV2(Paginacion):
    base: str | None = Field(
        default=None, pattern=PATRON_CODIGO_BASE, description="El codigo de una base."
    )
    municipio: str | None = Field(default=None, pattern=PATRON_CVEGEO)
    estado: EstadoAsignacion | None = None


class PaginaAsignacionesV2(Pagina[AsignacionV2Respuesta]):
    territorial_run_id: UUID


class AsignacionDeCuentaRespuesta(AsignacionV2Respuesta):
    territorial_run_id: UUID
    corte: CorteRespuesta
    geografia_run_id: UUID
    bases_version_id: UUID
    version_politica: str


class PaginaTerritorialV2DeCuenta(Pagina[AsignacionDeCuentaRespuesta]):
    cuenta_id: UUID
    cliente_unico: str
