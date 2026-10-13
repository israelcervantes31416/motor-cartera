"""Los esquemas de ruteo/v2 en la API: las versiones de la red vial, las ejecuciones, las rutas
y las paradas.

Ninguna respuesta lleva una coordenada (ni la de un punto, ni la de su snap), una polilinea, una
geometria, un recuadro ni una matriz: el orden de visita, los instantes planificados (ETA estaticas,
sin trafico), las distancias y duraciones por calle, el estado y su motivo. Los instantes van en la
zona horaria de la configuracion de campo del plan, con su desfase.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from motor_cartera.api.esquemas import Pagina, Paginacion
from motor_cartera.db.modelos import PATRON_CODIGO_BASE, EstadoRuteoV2, EstadoVersionRedVial
from motor_cartera.ruteo_v2.politica import EstadoParada, EstadoRuta

AVISO_RUTEO_V2 = (
    "ruteo/v2 dice en que orden puede recorrer cada gestor, por la red vial, las cuentas que "
    "campo/v2 le asigno, con ETA planificadas sobre tiempos estaticos de la red (sin trafico) y un "
    "servicio fijo de 900 s por visita, que es una hipotesis publica. Es una heuristica "
    "determinista, no una ruta optima. No reasigna cuentas entre gestores ni entre bases. Sin "
    "coordenadas ni geometrias."
)
ATRIBUCION_OSM = "Datos de la red vial: (c) colaboradores de OpenStreetMap, licencia ODbL 1.0."


# --- las redes ------------------------------------------------------------------------------------


class RedVialRespuesta(BaseModel):
    """Una version de la red vial. Sus archivos no se sirven: solo lo que dice de ellos."""

    red_vial_version_id: UUID
    estado: EstadoVersionRedVial
    huella: str = Field(
        description="SHA-256 de su identidad (fuente, motor, distribucion, algoritmo, perfil y "
        "comandos). El backend la declara en cada respuesta."
    )
    proveedor_datos: str
    proveedor_extracto: str | None
    licencia: str
    fecha_snapshot: date | None
    region: str
    archivo_fuente: str
    url_fuente: str | None
    sha256_fuente: str
    motor_ruteo: str
    version_motor_ruteo: str
    distribucion_motor: str
    algoritmo_motor: str
    perfil: str
    hash_perfil: str
    hash_manifest: str | None
    cobertura: dict[str, int] = Field(
        description="Lo que la preparacion leyo y escribio: nodos, vias y aristas. Sin recuadro."
    )
    creada_en: datetime
    publicada_en: datetime | None
    detalle: str | None
    atribucion: str | None = Field(
        description="La atribucion que exige la licencia de sus datos, si es de OpenStreetMap."
    )


class ArtefactoRedVialRespuesta(BaseModel):
    archivo: str
    bytes: int
    sha256: str


class RedVialDetalleRespuesta(RedVialRespuesta):
    comandos: list[list[str]] = Field(description="Los comandos exactos de la preparacion.")
    artefactos: list[ArtefactoRedVialRespuesta] = Field(
        description="Cada archivo preparado con su SHA-256: la evidencia de su preparacion."
    )


class PaginaRedesViales(Pagina[RedVialRespuesta]):
    pass


# --- las ejecuciones ------------------------------------------------------------------------------


class EjecucionRuteoEntrada(BaseModel):
    """Lo que fija una ejecucion: el plan de campo/v2 y la version de la red, declarados."""

    model_config = ConfigDict(extra="forbid")

    campo_run_id: UUID = Field(description="Un plan de campo/v2 EXITOSA.")
    red_vial_version_id: UUID = Field(description="Una version de la red vial PUBLICADA.")
    version_politica: str | None = Field(
        default=None, description="Por omision, ruteo-public-v2, la unica que se aplica."
    )


class PoliticaDeRuteo(BaseModel):
    algoritmo: str
    max_snap_distancia_m: int
    duracion_servicio_s: int = Field(description="Una hipotesis publica, no una medicion.")
    inicio_fin_base: bool
    bloque_matriz: int
    max_mejoras_2opt: int
    perfil: str


class RedDeLaEjecucion(BaseModel):
    red_vial_version_id: UUID
    huella: str
    region: str
    motor_ruteo: str
    version_motor_ruteo: str


class ConteosRuteoV2(BaseModel):
    gestores_objetivo: int = Field(description="Los gestores con al menos una cuenta ASIGNADA.")
    rutas_completas: int
    rutas_parciales: int
    rutas_sin_paradas: int = Field(description="SIN_PARADAS_ENRUTABLES.")
    bases_no_enrutables: int
    rutas_sin_jornada: int
    paradas_objetivo: int = Field(description="Las cuentas ASIGNADA del plan.")
    programadas: int
    sin_tiempo: int
    no_enrutables: int
    no_enrutables_base: int


class TotalesRuteoV2(BaseModel):
    distancia_total_m: int = Field(description="Por calle, de cada base a su base.")
    duracion_vial_total_s: int = Field(description="El traslado, con cada regreso.")
    duracion_servicio_total_s: int
    duracion_espera_total_s: int


class EjecucionRuteoV2Respuesta(BaseModel):
    ruteo_run_id: UUID
    estado: EstadoRuteoV2
    resultado: str | None = Field(
        description="RUTAS_PUBLICADAS, RED_VIAL_BACKEND_INCOMPATIBLE, INTENTOS_AGOTADOS... null "
        "mientras esta EN_PROCESO."
    )
    campo_run_id: UUID
    fecha_operacion: date
    zona_horaria: str
    red: RedDeLaEjecucion
    version_motor: str
    version_politica: str
    hash_politica: str
    politica: PoliticaDeRuteo | None = Field(
        description="Los parametros de su politica, si este servicio la conoce."
    )
    firma_inputs: str
    conteos: ConteosRuteoV2
    totales: TotalesRuteoV2
    distribucion: dict[str, Any] | None = Field(
        description="Por estado, por motivo, por prioridad y por base, y lo que costo la red "
        "(matrices, bloques, snaps). Sin coordenadas ni celdas."
    )
    trabajo_id: UUID | None
    iniciada_en: datetime
    terminada_en: datetime | None
    duracion_segundos: float | None
    detalle: str | None
    aviso: str = Field(default=AVISO_RUTEO_V2)


class PaginaEjecucionesRuteoV2(Pagina[EjecucionRuteoV2Respuesta]):
    pass


class ParametrosEjecucionesRuteo(Paginacion):
    campo_run_id: UUID | None = None
    red_vial_version_id: UUID | None = None
    estado: EstadoRuteoV2 | None = None


# --- las rutas ------------------------------------------------------------------------------------


class GestorDeLaRuta(BaseModel):
    gestor_id: UUID
    codigo: str


class BaseDeLaRuta(BaseModel):
    base_id: UUID
    codigo: str


class ConteosDeLaRuta(BaseModel):
    paradas_campo: int
    programadas: int
    sin_tiempo: int
    no_enrutables: int
    no_enrutables_base: int


class RegresoDeLaRuta(BaseModel):
    """El ultimo tramo: de la ultima parada programada a la base."""

    salida: datetime
    llegada: datetime
    distancia_m: int
    duracion_s: int
    espera_s: int


class RutaRespuesta(BaseModel):
    ruta_id: UUID
    ruteo_run_id: UUID
    fecha_operacion: date
    zona_horaria: str
    gestor: GestorDeLaRuta
    base: BaseDeLaRuta
    estado_ruta: EstadoRuta
    motivo_base: str | None = Field(description="Solo en BASE_NO_ENRUTABLE.")
    conteos: ConteosDeLaRuta
    base_snap_distancia_m: Decimal | None
    jornada_inicio: datetime | None
    jornada_fin: datetime | None
    salida_base: datetime | None
    regreso: RegresoDeLaRuta | None
    distancia_total_m: int
    duracion_traslado_s: int
    duracion_servicio_s: int
    duracion_espera_s: int
    duracion_total_s: int = Field(description="De la salida de la base al regreso.")
    mejoras_2opt: int
    hash_matriz: str | None = Field(description="La matriz no se guarda ni se publica: su hash.")
    hash_secuencia: str


class PaginaRutasV2(Pagina[RutaRespuesta]):
    ruteo_run_id: UUID


class ParametrosRutas(Paginacion):
    gestor: str | None = Field(default=None, pattern=PATRON_CODIGO_BASE)
    base: str | None = Field(default=None, pattern=PATRON_CODIGO_BASE)
    estado: EstadoRuta | None = None


# --- las paradas ----------------------------------------------------------------------------------


class ParadaRespuesta(BaseModel):
    """Una parada: su cuenta, su estado, su motivo y, si quedo PROGRAMADA, su orden y sus instantes
    planificados. Sin coordenadas."""

    cuenta_id: UUID
    cliente_unico: str
    ruta_id: UUID
    gestor: str
    prioridad: str
    estado: EstadoParada
    motivo: str | None
    orden_visita: int | None
    snap_distancia_m: Decimal | None = Field(
        description="Del punto a su segmento de la red, al centimetro. Null sin snap."
    )
    salida_anterior: datetime | None
    llegada_estimada: datetime | None
    inicio_servicio: datetime | None
    fin_servicio: datetime | None
    distancia_desde_anterior_m: int | None
    duracion_desde_anterior_s: int | None
    espera_s: int | None


class PaginaParadasV2(Pagina[ParadaRespuesta]):
    pass


class ParametrosParadas(Paginacion):
    estado: EstadoParada | None = None
    gestor: str | None = Field(default=None, pattern=PATRON_CODIGO_BASE)
    base: str | None = Field(default=None, pattern=PATRON_CODIGO_BASE)
    prioridad: str | None = Field(default=None, pattern="^(BAJA|MEDIA|ALTA|MUY_ALTA)$")


class ParadaDeCuentaRespuesta(ParadaRespuesta):
    ruteo_run_id: UUID
    fecha_operacion: date
    zona_horaria: str
    base: str
    estado_ruta: str
    version_politica: str


class PaginaRuteoDeCuenta(Pagina[ParadaDeCuentaRespuesta]):
    cuenta_id: UUID
    cliente_unico: str
