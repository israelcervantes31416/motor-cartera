"""Lo que la API recibe y devuelve.

Las reglas no se repiten aqui: los estados salen del modelo y los catalogos del contrato.
Si el contrato cambia, la API cambia con el, sin que nadie tenga que acordarse.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from motor_cartera.contratos.cartera import CANALES, PRODUCTOS, VERSION_CONTRATO
from motor_cartera.contratos.cartera_v2 import VERSION_CONTRATO_V2
from motor_cartera.db.modelos import (
    EstadoCorrida,
    EstadoDecision,
    EstadoFlujo,
    EstadoHistoria,
    EstadoIngestaPagos,
    EstadoMotorPagos,
    EstadoRuteo,
    EstadoTerritorial,
    EstadoTrabajo,
    EtapaFlujo,
    TipoFuenteHistoria,
    TipoTrabajo,
)
from motor_cartera.decision.reglas import VERSION_REGLAS_DECISION
from motor_cartera.fuentes.proyeccion import VERSION_PROYECCION
from motor_cartera.historia.presencia import Presencia, TipoEvento
from motor_cartera.motor_pagos.reglas import (
    VERSION_MOTOR_PAGOS,
    Clasificacion,
    EstadoConciliacion,
    SignoEconomico,
    TipoMovimiento,
)
from motor_cartera.ruteo.reglas import VERSION_REGLAS_RUTEO
from motor_cartera.segmentacion import Dimension
from motor_cartera.territorial.reglas import VERSION_REGLAS_TERRITORIAL

# Los catalogos del contrato, como tipos de la API: un filtro con un producto que el
# contrato no conoce es un 422, sin que la lista se haya escrito dos veces.
Producto = StrEnum("Producto", {p: p for p in PRODUCTOS})
Canal = StrEnum("Canal", {c: c for c in CANALES})


class Paginacion(BaseModel):
    """Paginacion por numero de pagina, igual en todas las listas de la API."""

    pagina: int = Field(default=1, ge=1, description="Pagina a devolver, desde 1.")
    por_pagina: int = Field(
        default=50, ge=1, le=500, description="Elementos por pagina; hasta 500."
    )

    @property
    def desplazamiento(self) -> int:
        return (self.pagina - 1) * self.por_pagina


class Pagina[T](BaseModel):
    total: int = Field(description="Cuantos elementos hay en total, sumando todas las paginas.")
    pagina: int
    por_pagina: int
    elementos: list[T] = Field(description="Vacia si la pagina queda despues de la ultima.")


EJEMPLO_CORRIDA = {
    "run_id": "0b8e6f1c-3d5a-4f7e-9c2b-1a4d6e8f0a2c",
    "estado": "EXITOSA",
    "origen": "cartera_sintetica.xlsx",
    "firma": "9f2c4e6a8b0d1f3e5a7c9e1b3d5f7a9c1e3b5d7f9a1c3e5b7d9f1a3c5e7b9d1f",
    "firma_contenido": "4b8d2f6a0c4e8a2d6f0b4d8e2a6c0f4b8d2e6a0c4f8b2d6e0a4c8f2b6d0e4a8c",
    "fecha_corte": "2026-09-30",
    "filas_leidas": 10000,
    "filas_validas": 9800,
    "filas_rechazadas": 200,
    "tolerancia_rechazo": 0.05,
    "version_contrato": VERSION_CONTRATO,
    "iniciada_en": "2026-09-30T15:04:05.123456Z",
    "terminada_en": "2026-09-30T15:04:09.654321Z",
    "duracion_segundos": 4.531,
    "detalle": "Se publicaron 9,800 cuentas; 200 registros (2.0%) se rechazaron, dentro de la "
    "tolerancia de 5.0%. Origen: hoja 'cartera' de 'cartera_sintetica.xlsx'. Descartado: "
    "hoja 'LEEME': ...",
    "despacho_id": "DSP_001",
    "cartera_id": "CARTERA_PRINCIPAL",
    "version_proyeccion": None,
}

# Lo que responde POST /corridas: la corrida recien registrada, antes de leer nada.
EJEMPLO_CORRIDA_EN_PROCESO = {
    **EJEMPLO_CORRIDA,
    "estado": "EN_PROCESO",
    "firma_contenido": None,
    "fecha_corte": None,
    "filas_leidas": 0,
    "filas_validas": 0,
    "filas_rechazadas": 0,
    "terminada_en": None,
    "duracion_segundos": None,
    "detalle": None,
}


class CorridaRespuesta(BaseModel):
    """Una corrida: que archivo, como va o como termino, y cuanto tardo."""

    model_config = ConfigDict(
        from_attributes=True, json_schema_extra={"examples": [EJEMPLO_CORRIDA]}
    )

    run_id: UUID
    estado: EstadoCorrida = Field(
        description="EN_PROCESO desde que se sube hasta que el worker la termina. Al terminar: "
        "EXITOSA (publico sus cuentas), RECHAZADA (demasiados registros no cumplen el contrato, o "
        "la cartera trae mas de una fecha de corte; no publico nada) o FALLIDA (no se pudo "
        "juzgar: archivo ilegible o error; no publico nada)."
    )
    origen: str = Field(description="Nombre del archivo recibido.")
    firma: str = Field(description="SHA-256 del archivo: misma firma, mismo archivo.")
    firma_contenido: str | None = Field(
        description="SHA-256 de la cartera ya normalizada (sus registros validos): la misma "
        "cartera en xlsx, csv o zip tiene la misma, aunque cada archivo tenga su firma. Vacia "
        "mientras esta en proceso, y si no se pudo juzgar."
    )
    fecha_corte: date | None = Field(
        description="El corte de la cartera. Vacio si no se leyo, o si trae mas de uno."
    )
    filas_leidas: int
    filas_validas: int = Field(
        description="Cumplen el contrato. Se publican solo si la corrida termina EXITOSA."
    )
    filas_rechazadas: int = Field(description="No cumplen el contrato; ver /rechazos.")
    tolerancia_rechazo: float = Field(description="Fraccion maxima de rechazos que se admitio.")
    version_contrato: str = Field(description="La version del contrato con que se juzga.")
    iniciada_en: datetime
    terminada_en: datetime | None
    detalle: str | None = Field(description="Que paso, en palabras, y de donde se leyo.")
    despacho_id: str = Field(
        description="El despacho que opera el sistema, cuando se registro la corrida. Es metadata "
        "del sistema, no un dato del archivo."
    )
    cartera_id: str = Field(
        description="La cartera del acreedor que gestiona ese despacho. Metadata del sistema."
    )
    version_proyeccion: str | None = Field(
        description=f"Con que version de la proyeccion operacional ({VERSION_PROYECCION}) se llevo "
        f"una cartera {VERSION_CONTRATO_V2} a las cuentas que leen los motores. Null en "
        f"{VERSION_CONTRATO}, que ya tiene esa forma."
    )

    @computed_field(description="Segundos de inicio a fin; vacio mientras esta en proceso.")
    @property
    def duracion_segundos(self) -> float | None:
        if self.terminada_en is None:
            return None
        return round((self.terminada_en - self.iniciada_en).total_seconds(), 3)


class MotivoRespuesta(BaseModel):
    campo: str
    regla: str = Field(
        description="La regla del contrato que no se cumplio, como la nombra pandera.",
        examples=["greater_than_or_equal_to(0)"],
    )


class RechazoRespuesta(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    fila: int = Field(description="Fila del archivo; el encabezado es la fila 1.")
    valores: dict[str, str | None] = Field(
        description="El registro tal como llego, en texto, con las columnas en el orden de su "
        "contrato."
    )
    motivos: list[MotivoRespuesta]


def ordenar_valores(
    valores: dict[str, str | None], orden: tuple[str, ...]
) -> dict[str, str | None]:
    """JSONB no conserva el orden de las llaves; quien corrige el archivo lo lee en el de su
    contrato. Una llave que el contrato no tiene va al final, en orden alfabetico: no se pierde."""
    conocidas = {columna: valores[columna] for columna in orden if columna in valores}
    otras = {columna: valores[columna] for columna in sorted(valores) if columna not in conocidas}
    return {**conocidas, **otras}


class PaginaRechazos(Pagina[RechazoRespuesta]):
    run_id: UUID
    estado: EstadoCorrida


# --- la evidencia de una fuente -------------------------------------------------------------------

EJEMPLO_ARTEFACTO = {
    "artifact_id": "5f1c2e3d-4b5a-4c6d-8e7f-9a0b1c2d3e4f",
    "sha256": EJEMPLO_CORRIDA["firma"],
    "tamano_bytes": 48211337,
    "nombre_original": "cartera_oficial_2026-09-30.zip",
    "formato": "zip",
    "media_type": "application/zip",
    "creado_en": "2026-09-30T15:04:04.981000Z",
}


class ArtefactoRespuesta(BaseModel):
    """Un archivo guardado en el almacen de artefactos, tal como llego. Nunca se dice donde vive."""

    model_config = ConfigDict(from_attributes=True)

    artifact_id: UUID = Field(description="Identificador publico del artefacto.")
    sha256: str = Field(description="SHA-256 de sus bytes: su identidad.")
    tamano_bytes: int
    nombre_original: str = Field(
        description="El nombre con que llego la primera vez. Es metadata: no identifica nada."
    )
    formato: str = Field(description="xlsx, csv o zip; parquet, si es un dataset conformado.")
    media_type: str | None
    creado_en: datetime


class ConformadoRespuesta(BaseModel):
    """El dataset conformado que publico la ingesta: sus registros validos, en Parquet."""

    dataset_id: UUID
    contrato: str = Field(description="Con que contrato se juzgo.")
    filas: int
    columnas: int = Field(description="Las del contrato, sin las dos tecnicas.")
    firma_contenido: str
    artefacto: ArtefactoRespuesta = Field(description="El Parquet, en el mismo almacen.")
    creado_en: datetime


class CompaneraRespuesta(BaseModel):
    """Una hoja companera del archivo, como CARRIER: se reconoce y se audita, no se publica."""

    model_config = ConfigDict(from_attributes=True)

    nombre: str
    filas: int
    columnas: int
    estructura_reconocida: bool = Field(description="Si trae exactamente las columnas esperadas.")
    advertencias: list[str] = Field(description="Lo incoherente. No bloquea la publicacion.")


class FuenteCorridaRespuesta(BaseModel):
    """La evidencia de una corrida y su linaje: el archivo original, el dataset conformado que
    publico y las hojas companeras que traia."""

    run_id: UUID
    version_contrato: str
    version_proyeccion: str | None
    fecha_corte: date | None
    despacho_id: str
    cartera_id: str
    artefacto: ArtefactoRespuesta | None = Field(
        description="El archivo tal como llego. Null solo en las corridas anteriores a v0.6.0, "
        "cuyo archivo no se conservo."
    )
    conformado: ConformadoRespuesta | None = Field(
        description=f"El dataset conformado, si la corrida publico una cartera "
        f"{VERSION_CONTRATO_V2}. Null en {VERSION_CONTRATO}, y en una corrida que no publico."
    )
    hojas_companeras: list[CompaneraRespuesta]


# --- las ingestas de pagos ------------------------------------------------------------------------

EJEMPLO_PAGOS = {
    "pagos_run_id": "9c1d3e5f-7a9b-4c2d-8e4f-6a8b0c2d4e6f",
    "estado": "EXITOSA",
    "origen": "pagos_oficial_2026-09-24_2026-09-30.zip",
    "firma": "1f3e5a7c9e1b3d5f7a9c1e3b5d7f9a1c3e5b7d9f1a3c5e7b9d1f3a5c7e9b1d3f",
    "firma_contenido": "7a9c1e3b5d7f9a1c3e5b7d9f1a3c5e7b9d1f3a5c7e9b1d3f5a7c9e1b3d5f7a9c",
    "version_contrato": "pagos/v1",
    "tolerancia_rechazo": 0.0,
    "filas_leidas": 4210,
    "filas_validas": 4210,
    "filas_rechazadas": 0,
    "despacho_id": "DSP_001",
    "cartera_id": "CARTERA_PRINCIPAL",
    "trabajo_id": "4e6a8c0e-2b4d-4f6a-8c0e-2b4d6f8a0c2e",
    "iniciada_en": "2026-09-30T18:00:00.120000Z",
    "terminada_en": "2026-09-30T18:00:01.940000Z",
    "duracion_segundos": 1.82,
    "detalle": "Se aceptaron 4,210 movimientos. Origen: 'pagos.csv', dentro de 'pagos.zip'.",
}

EJEMPLO_PAGOS_EN_PROCESO = {
    **EJEMPLO_PAGOS,
    "estado": "EN_PROCESO",
    "firma_contenido": None,
    "filas_leidas": 0,
    "filas_validas": 0,
    "filas_rechazadas": 0,
    "terminada_en": None,
    "duracion_segundos": None,
    "detalle": None,
}


class IngestaPagosRespuesta(BaseModel):
    """Una ingesta de pagos: que archivo, como va o como termino, y cuantos movimientos acepto."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={"examples": [EJEMPLO_PAGOS, EJEMPLO_PAGOS_EN_PROCESO]},
    )

    pagos_run_id: UUID = Field(description="Identificador publico de la ingesta de pagos.")
    estado: EstadoIngestaPagos = Field(
        description="EN_PROCESO hasta que el worker la termina. Al terminar: EXITOSA (acepto sus "
        "movimientos), RECHAZADA (mas rechazos que la tolerancia; no acepto nada) o FALLIDA (no "
        "se pudo juzgar: archivo ilegible o estructura distinta de pagos/v1)."
    )
    origen: str = Field(description="Nombre del archivo recibido.")
    firma: str = Field(description="SHA-256 del archivo: misma firma, mismo archivo.")
    firma_contenido: str | None = Field(
        description="SHA-256 de sus movimientos validos en forma canonica. Cuenta los repetidos."
    )
    version_contrato: str = Field(description="pagos/v1.")
    tolerancia_rechazo: float = Field(
        description="Fraccion maxima de movimientos rechazados con que se juzgo; por omision 0."
    )
    filas_leidas: int
    filas_validas: int = Field(description="Movimientos que cumplen el contrato.")
    filas_rechazadas: int = Field(description="No cumplen el contrato; ver /rechazos.")
    despacho_id: str
    cartera_id: str
    trabajo_id: UUID | None = Field(
        default=None, description="Su trabajo en la cola durable: GET /trabajos/{trabajo_id}."
    )
    iniciada_en: datetime
    terminada_en: datetime | None
    detalle: str | None = Field(description="Que paso, en palabras, y de donde se leyo.")

    @computed_field(description="Segundos de inicio a fin; vacio mientras esta en proceso.")
    @property
    def duracion_segundos(self) -> float | None:
        if self.terminada_en is None:
            return None
        return round((self.terminada_en - self.iniciada_en).total_seconds(), 3)


class PaginaRechazosPagos(Pagina[RechazoRespuesta]):
    pagos_run_id: UUID
    estado: EstadoIngestaPagos


class FuentePagosRespuesta(BaseModel):
    """La evidencia de una ingesta de pagos: su archivo original y su dataset conformado."""

    pagos_run_id: UUID
    version_contrato: str
    despacho_id: str
    cartera_id: str
    artefacto: ArtefactoRespuesta = Field(description="El archivo tal como llego.")
    conformado: ConformadoRespuesta | None = Field(
        description="Todos sus movimientos validos, sin deduplicar, en Parquet. Null si la "
        "ingesta no se acepto."
    )


class ParametrosResumen(Paginacion):
    run_id: UUID | None = Field(
        default=None,
        description="Resume esa corrida y no la vigente. Para paginar, fija el run_id de la "
        "primera respuesta: si entre pagina y pagina se publica otra cartera, no se mezclan.",
    )
    por: list[Dimension] = Field(
        default_factory=lambda: [Dimension.CANAL, Dimension.TRAMO_ATRASO],
        min_length=1,
        description="Dimensiones del segmento, en orden. Se repite el parametro: "
        "`?por=canal&por=tramo_atraso`.",
    )
    producto: Producto | None = Field(default=None, description="Solo cuentas de ese producto.")
    canal: Canal | None = Field(default=None, description="Solo cuentas de ese canal.")

    @field_validator("por")
    @classmethod
    def _sin_repetir(cls, por: list[Dimension]) -> list[Dimension]:
        if len(set(por)) != len(por):
            raise ValueError("Una dimension no puede repetirse.")
        return por


class SegmentoRespuesta(BaseModel):
    segmento: dict[str, str] = Field(
        description="El valor de cada dimension.",
        examples=[{"canal": "CAMPO", "tramo_atraso": "91+"}],
    )
    cuentas: int
    saldo_total: Decimal = Field(description="En pesos, como texto para no perder centavos.")
    saldo_promedio: Decimal


class ResumenCartera(Pagina[SegmentoRespuesta]):
    run_id: UUID = Field(description="La corrida que se resume: de ahi sale cada numero.")
    fecha_corte: date
    dimensiones: list[Dimension]
    total_cuentas: int = Field(description="Cuentas que pasan los filtros, en todos los segmentos.")
    saldo_total: Decimal = Field(description="Su saldo sumado, en todos los segmentos.")


# --- el Decision Engine --------------------------------------------------------------------------
#
# El estado de una ejecucion sale del modelo, como el de una corrida. El vocabulario de una decision
# (segmento, prioridad, canal recomendado, codigo de cada motivo) no: es el de la version de las
# reglas que decidio, y una API que lee el historial no puede dejar de servir una decision vieja o
# futura porque este proceso conozca otro. Por eso viaja como texto.

EJEMPLO_EJECUCION = {
    "decision_run_id": "7d3f5b1e-9a2c-4e6f-8b0d-3c5e7a9b1d2f",
    "run_id": EJEMPLO_CORRIDA["run_id"],
    "version_reglas": VERSION_REGLAS_DECISION,
    "estado": "EXITOSA",
    "iniciada_en": "2026-09-30T15:10:00.000000Z",
    "terminada_en": "2026-09-30T15:10:01.840000Z",
    "duracion_segundos": 1.84,
    "cuentas_evaluadas": 9800,
    "cuentas_decididas": 9800,
    "detalle": f"Se decidieron 9,800 cuentas con {VERSION_REGLAS_DECISION}.",
}

# Como queda si el motor falla: la ejecucion existe, pero no publico ninguna decision.
EJEMPLO_EJECUCION_FALLIDA = {
    **EJEMPLO_EJECUCION,
    "estado": "FALLIDA",
    "terminada_en": "2026-09-30T15:10:00.910000Z",
    "duracion_segundos": 0.91,
    "cuentas_evaluadas": 4000,
    "cuentas_decididas": 0,
    "detalle": "Error interno (RuntimeError); ver la bitacora.",
}

# Lo que responde el POST: la ejecucion recien creada, con su trabajo en la cola y sin decidir nada.
EJEMPLO_EJECUCION_EN_PROCESO = {
    **EJEMPLO_EJECUCION,
    "estado": "EN_PROCESO",
    "terminada_en": None,
    "duracion_segundos": None,
    "cuentas_evaluadas": 0,
    "cuentas_decididas": 0,
    "detalle": None,
}

# Una cuenta con 65 dias de atraso y 62,000.00 de saldo, decidida con decision/v1.
EJEMPLO_DECISION_CUENTA = {
    "cliente_unico": "CU0000004521",
    "segmento": "MORA_MEDIA",
    "prioridad": "MUY_ALTA",
    "canal_recomendado": "CAMPO",
    "motivos": [
        {"codigo": "MORA_31_90", "campo": "dias_atraso", "valor": "65"},
        {"codigo": "SALDO_ALTO", "campo": "saldo_total", "valor": "62000.00"},
        {"codigo": "PRIORIDAD_MUY_ALTA", "campo": "prioridad", "valor": "MUY_ALTA"},
        {"codigo": "CANAL_CAMPO", "campo": "canal_recomendado", "valor": "CAMPO"},
    ],
}


class EjecucionDecisionRespuesta(BaseModel):
    """Una ejecucion del Decision Engine sobre una corrida: con que reglas, como va o como termino,
    y cuantas cuentas decidio."""

    model_config = ConfigDict(
        json_schema_extra={"examples": [EJEMPLO_EJECUCION, EJEMPLO_EJECUCION_EN_PROCESO]}
    )

    decision_run_id: UUID = Field(description="Identificador publico de la ejecucion.")
    run_id: UUID = Field(description="La corrida que se decidio.")
    version_reglas: str = Field(description="Con que reglas se decidio, p. ej. `decision/v1`.")
    estado: EstadoDecision = Field(
        description="EN_PROCESO desde que se pide hasta que el worker la termina. Al terminar: "
        "EXITOSA (publico una decision por cada cuenta de la corrida) o FALLIDA (no publico "
        "ninguna; `detalle` dice por que)."
    )
    iniciada_en: datetime
    terminada_en: datetime | None
    cuentas_evaluadas: int = Field(
        description="Cuantas cuentas alcanzo a evaluar el motor. En una FALLIDA puede ser mayor "
        "que cero aunque no se haya publicado ninguna decision."
    )
    cuentas_decididas: int = Field(
        description="Cuantas decisiones publico: todas las cuentas de la corrida, o ninguna."
    )
    detalle: str | None = Field(description="Que paso, en palabras.")

    @computed_field(description="Segundos de inicio a fin; vacio mientras esta en proceso.")
    @property
    def duracion_segundos(self) -> float | None:
        if self.terminada_en is None:
            return None
        return round((self.terminada_en - self.iniciada_en).total_seconds(), 3)


class MotivoDecisionRespuesta(BaseModel):
    """Un paso de una decision: que regla aplico, sobre que campo y con que valor. No es el motivo
    de un rechazo, que es una regla del contrato que un registro no cumplio."""

    codigo: str = Field(
        description="Del catalogo de la version de las reglas con que se decidio.",
        examples=["SALDO_ALTO"],
    )
    campo: str = Field(
        description="El campo que la regla leyo o el que produjo.", examples=["saldo_total"]
    )
    valor: str = Field(
        description="En texto canonico: los dias como entero y el saldo con dos decimales.",
        examples=["62000.00"],
    )


class DecisionCuentaRespuesta(BaseModel):
    """Lo que el motor decidio de una cuenta, y por que."""

    model_config = ConfigDict(json_schema_extra={"examples": [EJEMPLO_DECISION_CUENTA]})

    cliente_unico: str = Field(description="La cuenta, como la identifica la cartera.")
    segmento: str = Field(description="En que situacion de mora esta la cuenta.")
    prioridad: str = Field(description="Que tan pronto hay que gestionarla.")
    canal_recomendado: str = Field(
        description="La decision del motor. No es el canal de la cartera, que es un dato de "
        "entrada."
    )
    motivos: list[MotivoDecisionRespuesta] = Field(
        description="Por que salio asi, en el orden en que se aplicaron las reglas."
    )


class PaginaEjecucionesDecision(Pagina[EjecucionDecisionRespuesta]):
    run_id: UUID = Field(description="La corrida de la que son las ejecuciones.")


class PaginaDecisionCuentas(Pagina[DecisionCuentaRespuesta]):
    decision_run_id: UUID
    run_id: UUID = Field(description="La corrida de la que son las cuentas.")
    version_reglas: str = Field(
        description="Las reglas que decidieron: el vocabulario de las decisiones es el suyo."
    )
    estado: EstadoDecision


# --- el Motor Territorial ------------------------------------------------------------------------
#
# Como en el Decision Engine: el estado de una ejecucion sale del modelo, y el vocabulario de un
# municipio (la carga y el codigo de cada motivo) viaja como texto, para que la API sirva el
# historial de cualquier version de las reglas territoriales.

EJEMPLO_EJECUCION_TERRITORIAL = {
    "territorial_run_id": "5c1e3a7b-2d4f-4b6a-8e0c-9f1b3d5a7c9e",
    "decision_run_id": EJEMPLO_EJECUCION["decision_run_id"],
    "run_id": EJEMPLO_CORRIDA["run_id"],
    "version_reglas": VERSION_REGLAS_TERRITORIAL,
    "estado": "EXITOSA",
    "iniciada_en": "2026-09-30T15:12:00.000000Z",
    "terminada_en": "2026-09-30T15:12:00.420000Z",
    "duracion_segundos": 0.42,
    "territorios_evaluados": 312,
    "territorios_publicados": 312,
    "detalle": "Se organizaron 9,800 decisiones en 312 municipios con "
    f"{VERSION_REGLAS_TERRITORIAL}.",
}

# Como queda si el motor falla: la ejecucion existe, pero no publico ningun municipio.
EJEMPLO_EJECUCION_TERRITORIAL_FALLIDA = {
    **EJEMPLO_EJECUCION_TERRITORIAL,
    "estado": "FALLIDA",
    "terminada_en": "2026-09-30T15:12:00.310000Z",
    "duracion_segundos": 0.31,
    "territorios_publicados": 0,
    "detalle": "Error interno (RuntimeError); ver la bitacora.",
}

# Lo que responde el POST: la ejecucion recien creada, con su trabajo en la cola.
EJEMPLO_EJECUCION_TERRITORIAL_EN_PROCESO = {
    **EJEMPLO_EJECUCION_TERRITORIAL,
    "estado": "EN_PROCESO",
    "terminada_en": None,
    "duracion_segundos": None,
    "territorios_evaluados": 0,
    "territorios_publicados": 0,
    "detalle": None,
}

# Un municipio con 37 cuentas de campo, organizado con territorial/v1.
EJEMPLO_MUNICIPIO = {
    "clave_territorio": "21114",
    "cve_entidad": "21",
    "cve_municipio": "114",
    "cuentas_total": 412,
    "saldo_total": "6150000.00",
    "cuentas_campo": 37,
    "saldo_campo": "1520000.00",
    "carga": "ALTA",
    "posicion_campo": 1,
    "motivos": [{"codigo": "CARGA_CAMPO_20_MAS", "campo": "cuentas_campo", "valor": "37"}],
}


class EjecucionTerritorialRespuesta(BaseModel):
    """Una ejecucion del Motor Territorial sobre una ejecucion de decision: con que reglas, como va
    o como termino, y cuantos municipios evaluo y publico."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [EJEMPLO_EJECUCION_TERRITORIAL, EJEMPLO_EJECUCION_TERRITORIAL_EN_PROCESO]
        }
    )

    territorial_run_id: UUID = Field(description="Identificador publico de la ejecucion.")
    decision_run_id: UUID = Field(
        description="La ejecucion de decision cuyas decisiones se organizaron."
    )
    run_id: UUID = Field(description="La corrida de esas decisiones.")
    version_reglas: str = Field(description="Con que reglas se organizo, p. ej. `territorial/v1`.")
    estado: EstadoTerritorial = Field(
        description="EN_PROCESO desde que se pide hasta que el worker la termina. Al terminar: "
        "EXITOSA (publico un resultado por cada municipio con decisiones) o FALLIDA (no publico "
        "ninguno; `detalle` dice por que)."
    )
    iniciada_en: datetime
    terminada_en: datetime | None
    territorios_evaluados: int = Field(
        description="Cuantos municipios evaluaron las reglas. En una FALLIDA puede ser mayor que "
        "cero aunque no se haya publicado ninguno."
    )
    territorios_publicados: int = Field(
        description="Cuantos municipios publico: todos los evaluados, o ninguno."
    )
    detalle: str | None = Field(description="Que paso, en palabras.")

    @computed_field(description="Segundos de inicio a fin; vacio mientras esta en proceso.")
    @property
    def duracion_segundos(self) -> float | None:
        if self.terminada_en is None:
            return None
        return round((self.terminada_en - self.iniciada_en).total_seconds(), 3)


class MotivoTerritorialRespuesta(BaseModel):
    """Por que un municipio tiene su carga: que regla aplico, sobre que campo y con que valor."""

    codigo: str = Field(
        description="Del catalogo de la version de las reglas territoriales con que se calculo.",
        examples=["CARGA_CAMPO_5_19"],
    )
    campo: str = Field(description="El campo que la regla leyo.", examples=["cuentas_campo"])
    valor: str = Field(description="En texto canonico: el conteo como entero.", examples=["8"])


class ResultadoTerritorialRespuesta(BaseModel):
    """Lo que una ejecucion territorial publico de un municipio: sus agregados, su carga, su lugar
    entre los municipios con trabajo de campo, y por que."""

    model_config = ConfigDict(json_schema_extra={"examples": [EJEMPLO_MUNICIPIO]})

    cve_entidad: str = Field(description="Dos digitos, como texto.")
    cve_municipio: str = Field(description="Tres digitos, como texto.")
    cuentas_total: int = Field(description="Cuantas decisiones tiene el municipio.")
    saldo_total: Decimal = Field(description="El saldo de esas cuentas, en pesos, como texto.")
    cuentas_campo: int = Field(
        description="Cuantas tienen CAMPO como canal recomendado. No es el canal de la cartera."
    )
    saldo_campo: Decimal = Field(description="El saldo de esas cuentas de campo, como texto.")
    carga: str = Field(
        description="Cuanto trabajo de campo concentra, en el vocabulario de la version que lo "
        "calculo; con territorial/v1, SIN_CARGA, BAJA, MEDIA o ALTA."
    )
    posicion_campo: int | None = Field(
        description="Su lugar entre los municipios con cuentas de campo, desde 1. Null si no "
        "tiene ninguna: es una prioridad territorial, no una ruta."
    )
    motivos: list[MotivoTerritorialRespuesta] = Field(description="Por que tiene esa carga.")

    @computed_field(description="La clave del municipio: cve_entidad + cve_municipio.")
    @property
    def clave_territorio(self) -> str:
        return self.cve_entidad + self.cve_municipio


class PaginaEjecucionesTerritoriales(Pagina[EjecucionTerritorialRespuesta]):
    decision_run_id: UUID = Field(description="La ejecucion de decision de la que son.")
    run_id: UUID = Field(description="La corrida de esas decisiones.")


class PaginaMunicipios(Pagina[ResultadoTerritorialRespuesta]):
    territorial_run_id: UUID
    decision_run_id: UUID
    run_id: UUID = Field(description="La corrida de las decisiones organizadas.")
    version_reglas: str = Field(
        description="Las reglas que organizaron: el vocabulario de los municipios es el suyo."
    )
    estado: EstadoTerritorial


# --- el Motor de Ruteo ---------------------------------------------------------------------------
#
# Como en las otras ejecuciones, el estado sale del modelo. Las distancias y las coordenadas son
# enteros en metros sinteticos: los de un plano operativo local, propio de cada municipio, con el
# deposito en (0, 0). No son latitud ni longitud, domicilios, calles ni tiempos.

EJEMPLO_EJECUCION_RUTEO = {
    "ruteo_run_id": "3a9c5e7f-1b2d-4c6e-8f0a-2b4d6f8a0c1e",
    "territorial_run_id": EJEMPLO_EJECUCION_TERRITORIAL["territorial_run_id"],
    "decision_run_id": EJEMPLO_EJECUCION["decision_run_id"],
    "run_id": EJEMPLO_CORRIDA["run_id"],
    "version_reglas": VERSION_REGLAS_RUTEO,
    "estado": "EXITOSA",
    "iniciada_en": "2026-09-30T15:14:00.000000Z",
    "terminada_en": "2026-09-30T15:14:01.120000Z",
    "duracion_segundos": 1.12,
    "rutas_evaluadas": 403,
    "rutas_publicadas": 403,
    "paradas_evaluadas": 2968,
    "paradas_publicadas": 2968,
    "detalle": f"Se rutearon 2,968 cuentas de campo en 403 municipios con {VERSION_REGLAS_RUTEO}.",
}

# Como queda si el motor falla: la ejecucion existe, pero no publico ninguna ruta.
EJEMPLO_EJECUCION_RUTEO_FALLIDA = {
    **EJEMPLO_EJECUCION_RUTEO,
    "estado": "FALLIDA",
    "terminada_en": "2026-09-30T15:14:00.830000Z",
    "duracion_segundos": 0.83,
    "rutas_publicadas": 0,
    "paradas_publicadas": 0,
    "detalle": "Error interno (RuntimeError); ver la bitacora.",
}

# Lo que responde el POST: la ejecucion recien creada, con su trabajo en la cola.
EJEMPLO_EJECUCION_RUTEO_EN_PROCESO = {
    **EJEMPLO_EJECUCION_RUTEO,
    "estado": "EN_PROCESO",
    "terminada_en": None,
    "duracion_segundos": None,
    "rutas_evaluadas": 0,
    "rutas_publicadas": 0,
    "paradas_evaluadas": 0,
    "paradas_publicadas": 0,
    "detalle": None,
}

# Un municipio con cinco cuentas de campo, ruteado con ruteo/v1: el 2-opt le quito 9,108 metros
# sinteticos a la ruta del vecino mas cercano.
EJEMPLO_RUTA = {
    "clave_territorio": "21114",
    "posicion_territorial": 1,
    "cuentas_campo": 5,
    "paradas": 5,
    "distancia_inicial_m": 37878,
    "distancia_total_m": 28770,
    "distancia_regreso_deposito_m": 7633,
    "mejora_2opt_m": 9108,
}

# La primera parada de esa ruta.
EJEMPLO_PARADA = {
    "secuencia": 1,
    "cliente_unico": "CU00000041",
    "x_m": 1016,
    "y_m": -2070,
    "distancia_desde_anterior_m": 3086,
}


class EjecucionRuteoRespuesta(BaseModel):
    """Una ejecucion del Motor de Ruteo sobre una ejecucion territorial: con que reglas, como va o
    como termino, y cuantas rutas y paradas calculo y publico."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [EJEMPLO_EJECUCION_RUTEO, EJEMPLO_EJECUCION_RUTEO_EN_PROCESO]
        }
    )

    ruteo_run_id: UUID = Field(description="Identificador publico de la ejecucion.")
    territorial_run_id: UUID = Field(
        description="La ejecucion territorial cuyos municipios se rutearon."
    )
    decision_run_id: UUID = Field(description="La ejecucion de decision debajo de ella.")
    run_id: UUID = Field(description="La corrida de esas decisiones.")
    version_reglas: str = Field(description="Con que reglas se ruteo, p. ej. `ruteo/v1`.")
    estado: EstadoRuteo = Field(
        description="EN_PROCESO desde que se pide hasta que el worker la termina. Al terminar: "
        "EXITOSA (publico una ruta por cada municipio con cuentas de campo) o FALLIDA (no publico "
        "ninguna; `detalle` dice por que)."
    )
    iniciada_en: datetime
    terminada_en: datetime | None
    rutas_evaluadas: int = Field(
        description="Cuantas rutas calculo el nucleo. Cero si no termino de calcularlas; en una "
        "FALLIDA puede ser mayor que cero aunque no se haya publicado ninguna."
    )
    rutas_publicadas: int = Field(description="Cuantas rutas publico: todas, o ninguna.")
    paradas_evaluadas: int = Field(description="Cuantas paradas tienen las rutas calculadas.")
    paradas_publicadas: int = Field(
        description="Cuantas paradas publico: una por cuenta con CAMPO como canal recomendado, o "
        "ninguna."
    )
    detalle: str | None = Field(description="Que paso, en palabras.")

    @computed_field(description="Segundos de inicio a fin; vacio mientras esta en proceso.")
    @property
    def duracion_segundos(self) -> float | None:
        if self.terminada_en is None:
            return None
        return round((self.terminada_en - self.iniciada_en).total_seconds(), 3)


class RutaTerritorialRespuesta(BaseModel):
    """La ruta de un municipio: cuantas paradas tiene y cuanto mide, en metros sinteticos. Sale del
    deposito del municipio, en (0, 0), visita cada cuenta de campo una vez y regresa."""

    model_config = ConfigDict(json_schema_extra={"examples": [EJEMPLO_RUTA]})

    clave_territorio: str = Field(description="El municipio: cve_entidad + cve_municipio.")
    posicion_territorial: int = Field(
        description="El lugar del municipio entre los que tienen cuentas de campo, como lo publico "
        "la ejecucion territorial (`posicion_campo`). Es una prioridad entre municipios, no el "
        "orden de visita, que es la `secuencia` de cada parada."
    )
    cuentas_campo: int = Field(
        description="Cuantas cuentas del municipio tienen CAMPO como canal recomendado."
    )
    paradas: int = Field(description="Cuantas paradas tiene la ruta: una por cuenta de campo.")
    distancia_inicial_m: int = Field(
        description="La ruta del vecino mas cercano, antes del 2-opt, con el regreso al deposito. "
        "Metros sinteticos."
    )
    distancia_total_m: int = Field(
        description="La ruta publicada, con el regreso al deposito. Metros sinteticos, no la "
        "distancia de ninguna calle real."
    )
    distancia_regreso_deposito_m: int = Field(
        description="De la ultima parada al deposito. Metros sinteticos."
    )
    mejora_2opt_m: int = Field(
        description="distancia_inicial_m - distancia_total_m: lo que el 2-opt le quito a la ruta."
    )


class ParadaRutaRespuesta(BaseModel):
    """Una cuenta en la ruta: en que lugar se visita, en que punto del plano sintetico de su
    municipio y a que distancia de la parada anterior."""

    model_config = ConfigDict(json_schema_extra={"examples": [EJEMPLO_PARADA]})

    secuencia: int = Field(description="El orden de visita dentro de la ruta, desde 1.")
    cliente_unico: str = Field(description="La cuenta, como la identifica la cartera.")
    x_m: int = Field(
        description="Coordenada sintetica en metros, de -5000 a 5000, en el plano del municipio "
        "con el deposito en (0, 0). No es una longitud geografica ni un domicilio."
    )
    y_m: int = Field(
        description="Coordenada sintetica en metros, de -5000 a 5000. No es una latitud."
    )
    distancia_desde_anterior_m: int = Field(
        description="Distancia Manhattan desde la parada anterior; la primera, desde el deposito. "
        "Metros sinteticos."
    )


class PaginaEjecucionesRuteo(Pagina[EjecucionRuteoRespuesta]):
    territorial_run_id: UUID = Field(description="La ejecucion territorial de la que son.")
    decision_run_id: UUID = Field(description="La ejecucion de decision debajo de ella.")
    run_id: UUID = Field(description="La corrida de esas decisiones.")


class PaginaRutas(Pagina[RutaTerritorialRespuesta]):
    ruteo_run_id: UUID
    territorial_run_id: UUID
    decision_run_id: UUID
    run_id: UUID = Field(description="La corrida de las decisiones ruteadas.")
    version_reglas: str = Field(description="Las reglas que trazaron las rutas.")
    estado: EstadoRuteo


class PaginaParadas(Pagina[ParadaRutaRespuesta]):
    ruteo_run_id: UUID
    run_id: UUID = Field(description="La corrida de las decisiones ruteadas.")
    version_reglas: str = Field(description="Las reglas que trazaron la ruta.")
    estado: EstadoRuteo
    clave_territorio: str = Field(description="El municipio de la ruta.")


# --- la orquestacion ------------------------------------------------------------------------------
#
# Lo que la API deja ver de la cola durable: el flujo de una corrida y sus trabajos, con los
# identificadores publicos de lo que tocan. Nunca el worker que tiene un trabajo, ni un id interno.

EJEMPLO_FLUJO = {
    "flujo_id": "9b2d4f6a-8c0e-4a1b-9d3f-5e7a9c1b3d5f",
    "run_id": EJEMPLO_CORRIDA["run_id"],
    "estado": "COMPLETADO",
    "etapa": "COMPLETADA",
    "decision_run_id": EJEMPLO_EJECUCION["decision_run_id"],
    "territorial_run_id": EJEMPLO_EJECUCION_TERRITORIAL["territorial_run_id"],
    "ruteo_run_id": EJEMPLO_EJECUCION_RUTEO["ruteo_run_id"],
    "creado_en": "2026-09-30T15:04:05.123456Z",
    "actualizado_en": "2026-09-30T15:14:01.250000Z",
    "terminado_en": "2026-09-30T15:14:01.250000Z",
    "duracion_segundos": 596.127,
    "detalle": (
        "La ingesta, la decision, la organizacion territorial y el ruteo terminaron EXITOSA."
    ),
}

# Como lo ve el cliente mientras el worker trabaja: ya tiene su decision y espera la territorial.
EJEMPLO_FLUJO_EN_PROCESO = {
    **EJEMPLO_FLUJO,
    "estado": "EN_PROCESO",
    "etapa": "TERRITORIAL",
    "ruteo_run_id": None,
    "actualizado_en": "2026-09-30T15:10:01.851000Z",
    "terminado_en": None,
    "duracion_segundos": None,
    "detalle": "La decision termino EXITOSA; la organizacion territorial esta en la cola.",
}

# Detenido en la decision: se reanuda con POST /flujos/{flujo_id}/reanudar.
EJEMPLO_FLUJO_DETENIDO = {
    **EJEMPLO_FLUJO,
    "estado": "DETENIDO",
    "etapa": "DECISION",
    "territorial_run_id": None,
    "ruteo_run_id": None,
    "actualizado_en": "2026-09-30T15:10:00.921000Z",
    "terminado_en": "2026-09-30T15:10:00.921000Z",
    "duracion_segundos": 355.798,
    "detalle": "La decision termino FALLIDA. Para reintentarla, reanuda el flujo.",
}

EJEMPLO_TRABAJO = {
    "trabajo_id": "2c4e6a8b-0d1f-4a3c-8e5b-7d9f1a3c5e7b",
    "flujo_id": EJEMPLO_FLUJO["flujo_id"],
    "tipo": "DECISION",
    "estado": "COMPLETADO",
    "objetivo_run_id": EJEMPLO_EJECUCION["decision_run_id"],
    "intentos": 1,
    "max_intentos": 5,
    "creado_en": "2026-09-30T15:09:59.912000Z",
    "disponible_desde": "2026-09-30T15:09:59.912000Z",
    "tomado_en": "2026-09-30T15:10:00.004000Z",
    "latido_en": "2026-09-30T15:10:00.004000Z",
    "lease_hasta": None,
    "terminado_en": "2026-09-30T15:10:01.846000Z",
    "ultimo_error": None,
}

# Uno que fallo una vez por un error del worker y espera su segundo intento.
EJEMPLO_TRABAJO_PENDIENTE = {
    **EJEMPLO_TRABAJO,
    "estado": "PENDIENTE",
    "disponible_desde": "2026-09-30T15:10:01.231000Z",
    "terminado_en": None,
    "ultimo_error": "Error de worker (OperationalError); ver la bitacora.",
}


class FlujoRespuesta(BaseModel):
    """El flujo automatico de una corrida: en que etapa va, como va, y que ejecucion publico o esta
    ejecutando cada etapa a la que ya llego."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [EJEMPLO_FLUJO, EJEMPLO_FLUJO_EN_PROCESO, EJEMPLO_FLUJO_DETENIDO]
        }
    )

    flujo_id: UUID = Field(description="Identificador publico del flujo.")
    run_id: UUID = Field(description="La corrida que el flujo lleva de la ingesta al ruteo.")
    estado: EstadoFlujo = Field(
        description="EN_PROCESO mientras puede avanzar. COMPLETADO si llego hasta un ruteo "
        "EXITOSA. DETENIDO si una etapa termino sin poder continuar: `etapa` dice cual, y "
        "`detalle` por que."
    )
    etapa: EtapaFlujo = Field(
        description="Hasta donde llego: INGESTA, DECISION, TERRITORIAL o RUTEO, o COMPLETADA."
    )
    decision_run_id: UUID | None = Field(
        description="La ejecucion de decision del flujo; null hasta que llega a esa etapa."
    )
    territorial_run_id: UUID | None = Field(
        description="La ejecucion territorial del flujo; null hasta que llega a esa etapa."
    )
    ruteo_run_id: UUID | None = Field(
        description="La ejecucion de ruteo del flujo; null hasta que llega a esa etapa."
    )
    creado_en: datetime
    actualizado_en: datetime = Field(description="El ultimo cambio de etapa o de estado.")
    terminado_en: datetime | None = Field(description="Null mientras esta EN_PROCESO.")
    detalle: str | None = Field(description="En que va, o por que se detuvo, en palabras.")

    @computed_field(description="Segundos de inicio a fin; vacio mientras esta en proceso.")
    @property
    def duracion_segundos(self) -> float | None:
        if self.terminado_en is None:
            return None
        return round((self.terminado_en - self.creado_en).total_seconds(), 3)


class TrabajoRespuesta(BaseModel):
    """Un trabajo de la cola durable: ejecutar el motor de su tipo sobre un recurso. Es el estado de
    la entrega, no el del motor: un trabajo COMPLETADO puede tener su ejecucion FALLIDA."""

    model_config = ConfigDict(
        json_schema_extra={"examples": [EJEMPLO_TRABAJO, EJEMPLO_TRABAJO_PENDIENTE]}
    )

    trabajo_id: UUID = Field(description="Identificador publico del trabajo.")
    flujo_id: UUID | None = Field(
        description="El flujo del que es; null si su etapa se pidio a mano."
    )
    tipo: TipoTrabajo = Field(
        description="Que motor ejecuta: INGESTA, DECISION, TERRITORIAL o RUTEO; o INGESTA_PAGOS o "
        "HISTORIA, que no son de ningun flujo. Un HISTORIA materializa un dataset conformado en el "
        "modelo historico, en paralelo al flujo operacional."
    )
    estado: EstadoTrabajo = Field(
        description="PENDIENTE (en la cola), EJECUTANDO (lo tiene un worker), COMPLETADO (su "
        "recurso termino, EXITOSA o no) o FALLIDO (agoto sus intentos sin que su recurso "
        "terminara)."
    )
    objetivo_run_id: UUID = Field(
        description="El identificador publico de su recurso, segun el tipo: run_id, "
        "decision_run_id, territorial_run_id, ruteo_run_id, pagos_run_id o historia_run_id."
    )
    intentos: int = Field(description="Cuantas veces lo ha tomado un worker para ejecutarlo.")
    max_intentos: int = Field(description="Cuantas veces se puede tomar, desde que nacio.")
    creado_en: datetime
    disponible_desde: datetime = Field(
        description="Desde cuando se puede tomar: tras un error, despues de su espera."
    )
    tomado_en: datetime | None = Field(description="La ultima vez que un worker lo tomo.")
    latido_en: datetime | None = Field(description="El ultimo latido de su worker.")
    lease_hasta: datetime | None = Field(
        description="Hasta cuando es de su worker, solo mientras esta EJECUTANDO. Si vence sin un "
        "latido, otro worker lo toma."
    )
    terminado_en: datetime | None
    ultimo_error: str | None = Field(
        description="El ultimo error del worker, sin traza: el detalle esta en su bitacora."
    )


class PaginaTrabajos(Pagina[TrabajoRespuesta]):
    flujo_id: UUID
    run_id: UUID = Field(description="La corrida del flujo.")


# --- el modelo historico y la Cuenta 360 ---------------------------------------------------------
#
# Lo que la API deja ver de la historia: siempre por identificadores publicos (cuenta_id, corte_id,
# historia_run_id, dataset_id), nunca por un id interno. Los importes viajan como texto, como en el
# resto de la API, para no perder centavos.

AVISO_PAGOS_OBSERVADOS = (
    "Estos son movimientos observados de la fuente pagos/v1. No estan deduplicados, conciliados, "
    "interpretados como reversos ni atribuidos. Ese procesamiento corresponde al Motor de Pagos: "
    "su interpretacion esta en /movimientos."
)
"""Lo que dice cada respuesta de pagos observados, y su documentacion, de forma visible."""

EJEMPLO_SNAPSHOT = {
    "fecha_corte": "2026-09-30",
    "corte_id": "0b87ab2f-e6eb-54a6-b1b4-2826722801db",
    "saldo_total": "62450.00",
    "saldo": "60000.00",
    "moratorios": "2450.00",
    "saldo_atrasado": "9200.00",
    "saldo_requerido": "9200.00",
    "pago_normal": "1153.00",
    "dias_atraso": 65,
    "atraso_maximo": 90,
    "semanas_atraso": 10,
    "producto": "CONSUMO",
    "estrategia": "TARDIA",
    "canal": "CAMPO",
    "fecha_ultimo_pago": "2026-07-14",
    "imp_ultimo_pago": "1500.00",
    "cve_entidad": "21",
    "cve_municipio": "114",
    "estatus_plan": None,
    "monto_plan": None,
    "pagos_recibidos": None,
    "estatus_promesa_pago": "VIGENTE",
    "monto_promesa_pago": "4600.00",
    "dataset_id": "5f1c2e3d-4b5a-4c6d-8e7f-9a0b1c2d3e4f",
    "source_row": 4523,
    "source_sheet": "CARTERA.csv",
}


class SnapshotRespuesta(BaseModel):
    """Una cuenta en un corte canonico: sus variables historicas y de que fila de que dataset
    salieron. Las demas columnas de la fuente (nombre, domicilio, telefonos...) siguen en el dataset
    conformado: `dataset_id` y `source_row` llevan a la fila exacta."""

    model_config = ConfigDict(json_schema_extra={"examples": [EJEMPLO_SNAPSHOT]})

    fecha_corte: date
    corte_id: UUID = Field(description="El corte canonico: GET /cartera/cortes/{corte_id}.")
    saldo_total: Decimal
    saldo: Decimal | None
    moratorios: Decimal | None
    saldo_atrasado: Decimal | None
    saldo_requerido: Decimal | None
    pago_normal: Decimal | None
    dias_atraso: int
    atraso_maximo: int | None
    semanas_atraso: int | None
    producto: str
    estrategia: str | None
    canal: str = Field(description="El canal de la cartera: un dato de la fuente, no una decision.")
    fecha_ultimo_pago: date | None
    imp_ultimo_pago: Decimal | None
    cve_entidad: str = Field(
        description="Resuelta con el catalogo del INEGI, como en la proyeccion."
    )
    cve_municipio: str
    estatus_plan: str | None
    monto_plan: Decimal | None
    pagos_recibidos: int | None
    estatus_promesa_pago: str | None
    monto_promesa_pago: Decimal | None
    dataset_id: UUID = Field(
        description="El dataset conformado del que salio; GET /corridas/{run_id}/fuente lo "
        "describe."
    )
    source_row: int = Field(description="La fila del archivo original; el encabezado es la fila 1.")
    source_sheet: str | None = Field(
        description="La hoja del xlsx o el miembro del zip; null en un csv suelto."
    )


class SnapshotEnHistoriaRespuesta(SnapshotRespuesta):
    """Un snapshot en la historia de su cuenta, unido al anterior."""

    continuo_desde_anterior: bool | None = Field(
        description="true si el snapshot anterior de la cuenta es del corte anterior de su "
        "cartera. false si la cuenta falto en medio: la diferencia no es la de un periodo "
        "continuo. null en la primera observacion."
    )
    cortes_ausentes_desde_anterior: int | None = Field(
        description="Cuantos cortes de la cartera falto la cuenta desde su snapshot anterior. null "
        "en la primera observacion."
    )
    delta_saldo_total: Decimal | None = Field(
        description="saldo_total menos el del snapshot anterior de la cuenta, continuo o no. null "
        "en la primera observacion."
    )
    delta_dias_atraso: int | None = Field(
        description="dias_atraso menos los del snapshot anterior, continuo o no."
    )


EJEMPLO_CUENTA_360 = {
    "cuenta_id": "7b1d2c3e-4f5a-5b6c-8d7e-9f0a1b2c3d4e",
    "cliente_unico": "CU0000004521",
    "despacho_id": "DSP_001",
    "cartera_id": "CARTERA_PRINCIPAL",
    "al": None,
    "primera_observacion": "2026-09-02",
    "ultima_observacion": "2026-09-30",
    "ultimo_corte_cartera": "2026-09-30",
    "estado_presencia": "EN_CARTERA",
    "cortes_observados": 4,
    "cortes_ausentes_desde_primera_observacion": 1,
    "salidas_observadas": 1,
    "reingresos_observados": 1,
    "pagos_observados": 3,
    "snapshot_actual": EJEMPLO_SNAPSHOT,
    "ultimo_snapshot_observado": EJEMPLO_SNAPSHOT,
}


class CuentaEncontradaRespuesta(BaseModel):
    """La cuenta canonica de un CLIENTE_UNICO en la cartera del sistema."""

    cuenta_id: UUID = Field(description="Su identificador publico: GET /cuentas/{cuenta_id}.")
    cliente_unico: str
    despacho_id: str
    cartera_id: str


class UltimaGestionRespuesta(BaseModel):
    gestion_id: UUID
    ocurrido_en: datetime
    canal: str
    nivel_contacto: str
    resultado: str


class UltimaAtribucionRespuesta(BaseModel):
    """Lo que la atribucion vigente dice del pago mas reciente de la cuenta que tiene una."""

    atribucion_run_id: UUID
    version_atribucion: str
    ventana_dias: int
    movimiento_id: UUID
    fecha_recepcion: datetime
    monto: Decimal
    anulado_por_reverso: bool
    clasificacion: str = Field(
        description="SIN_GESTION_CANDIDATA, ASOCIACION_UNICA o AMBIGUA. Asociacion operacional, no "
        "causalidad."
    )
    gestion_id: UUID | None = Field(description="La asociada, solo en ASOCIACION_UNICA.")
    candidatas: int


class LifecycleResumenRespuesta(BaseModel):
    """El lifecycle de la cuenta en numeros. Las gestiones, las promesas, los convenios y la linea
    de tiempo son subrecursos paginados: aqui no estan."""

    version_lifecycle: str
    gestiones: int = Field(description="Las vigentes: sin las anuladas.")
    gestiones_anuladas: int
    ultima_gestion: UltimaGestionRespuesta | None = Field(
        description="La vigente de ocurrido_en mas reciente."
    )
    ultimo_contacto_titular: UltimaGestionRespuesta | None = Field(
        description="La ultima gestion vigente con CONTACTO_TITULAR. Hablar con el titular no es "
        "un resultado favorable."
    )
    promesas: int = Field(description="Las acordadas, sin las de una gestion anulada.")
    promesas_vigentes: int = Field(description="De esas, las que nadie cancelo.")
    convenios: int
    convenios_vigentes: int
    visitas: int = Field(description="Las gestiones de CAMPO vigentes.")
    ultima_atribucion: UltimaAtribucionRespuesta | None = Field(
        description="La ultima atribucion disponible: la de la atribucion vigente de su ventana "
        "sobre el PAGO vigente mas reciente de la cuenta que tiene una (con `al`, recibido hasta "
        "ese dia). El detalle esta en /cuentas/{cuenta_id}/atribuciones."
    )


class Cuenta360Respuesta(BaseModel):
    """El resumen de una cuenta a traves de sus cortes. La historia, los eventos y los pagos son
    subrecursos paginados: esta respuesta no los trae."""

    model_config = ConfigDict(json_schema_extra={"examples": [EJEMPLO_CUENTA_360]})

    cuenta_id: UUID
    cliente_unico: str = Field(
        description="El identificador fuente de la cuenta. No es una persona."
    )
    despacho_id: str
    cartera_id: str
    al: date | None = Field(
        description="Si se pidio la cuenta como se veia en una fecha: solo cuentan los cortes de "
        "fecha hasta ese dia, y los pagos recibidos hasta el final de ese dia."
    )
    primera_observacion: date | None = Field(
        description="El primer corte en que aparece. No es su originacion ni un alta: la cartera "
        "pudo tenerla desde antes del primer corte que se observo."
    )
    ultima_observacion: date | None = Field(description="El ultimo corte en que aparece.")
    ultimo_corte_cartera: date | None = Field(
        description="El ultimo corte canonico de su cartera, este o no la cuenta en el."
    )
    estado_presencia: Presencia = Field(
        description="EN_CARTERA si aparece en el ultimo corte de su cartera; si no, "
        "NO_OBSERVADA_EN_ULTIMO_CORTE. No es un estado crediticio: que no aparezca no dice si se "
        "liquido, se cancelo o se castigo."
    )
    cortes_observados: int
    cortes_ausentes_desde_primera_observacion: int
    salidas_observadas: int
    reingresos_observados: int
    pagos_observados: int = Field(
        description="Cuantos movimientos de pagos/v1 hay con su CLIENTE_UNICO. Es un conteo de "
        "observaciones, no una recuperacion: ver /pagos-observados."
    )
    snapshot_actual: SnapshotRespuesta | None = Field(
        description="El snapshot del ultimo corte de la cartera, si la cuenta esta en el; si no, "
        "null."
    )
    ultimo_snapshot_observado: SnapshotRespuesta | None = Field(
        description="El snapshot del ultimo corte en que se observo, aunque ya no este en el "
        "vigente."
    )
    resumen_pagos: ResumenPagosRespuesta = Field(
        description="Sus pagos en numeros, segun la interpretacion vigente del motor de pagos: "
        "cuantas observaciones, cuantos movimientos, duplicados, ambiguos y reversos, y la "
        "recuperacion interpretada. Los movimientos estan en /movimientos y las observaciones tal "
        "como llegaron en /pagos-observados."
    )
    lifecycle_resumen: LifecycleResumenRespuesta = Field(
        description="Lo que la cobranza hizo con la cuenta, en numeros: gestiones, la ultima, el "
        "ultimo contacto con el titular, promesas, convenios y visitas. El detalle esta en "
        "/gestiones, /promesas, /convenios y /lifecycle."
    )


class ParametrosHistoria(Paginacion):
    orden: Literal["desc", "asc"] = Field(
        default="desc", description="Por fecha de corte: desc (por omision) o asc."
    )


class PaginaHistoria(Pagina[SnapshotEnHistoriaRespuesta]):
    cuenta_id: UUID
    cliente_unico: str
    orden: Literal["desc", "asc"]


class EventoRespuesta(BaseModel):
    """Un cambio de presencia de la cuenta, en el corte en que se observo."""

    tipo: TipoEvento = Field(
        description="PRIMERA_OBSERVACION, SALIDA_OBSERVADA (aparecia y en este corte ya no) o "
        "REINGRESO_OBSERVADO (vuelve despues de faltar al menos un corte). Una salida no dice por "
        "que salio."
    )
    fecha_corte: date
    corte_id: UUID
    ultima_observacion: date | None = Field(
        description="En una salida o un reingreso, el ultimo corte en que se habia observado."
    )
    cortes_ausente: int = Field(description="En un reingreso, cuantos cortes falto; si no, 0.")


class PaginaEventos(Pagina[EventoRespuesta]):
    cuenta_id: UUID
    cliente_unico: str


class PagoObservadoRespuesta(BaseModel):
    """Un movimiento de pagos/v1 tal como llego: una fila, una observacion."""

    pago_observado_id: UUID
    fecha_recepcion: datetime = Field(
        description="Hora local de la fuente, sin zona horaria, como la trae pagos/v1."
    )
    recuperacion_por_gestion: Decimal = Field(
        description="El importe del movimiento, como llego. Puede ser negativo, como un ajuste: "
        "interpretarlo es del Motor de Pagos."
    )
    concepto_calculo: str | None
    anio: int | None
    semana: int | None
    territorio: str | None
    zona: str | None
    segmento: str | None
    gerencia: str | None
    tipo_cartera: str | None
    producto: str | None
    campania: str | None
    gestor: str | None
    dias_de_atraso: int | None
    semanas_de_atraso: int | None
    plan_de_pago: str | None
    fecha_de_gestion: datetime | None
    cargos_automaticos: Decimal | None
    captacion: Decimal | None
    cobranza_total: Decimal | None
    porcentaje_comision: float | None
    monto_comision: Decimal | None
    cliente_unico: str
    pagos_run_id: UUID = Field(description="La ingesta de pagos que lo acepto.")
    dataset_id: UUID = Field(description="El dataset conformado del que salio.")
    source_row: int
    source_sheet: str | None


class PaginaPagosObservados(Pagina[PagoObservadoRespuesta]):
    cuenta_id: UUID
    cliente_unico: str
    aviso: str = Field(default=AVISO_PAGOS_OBSERVADOS, description="Que son y que no son.")


class CorteRespuesta(BaseModel):
    """Un corte canonico: la fotografia de la cartera en una fecha."""

    corte_id: UUID
    fecha_corte: date
    cuentas: int = Field(description="Cuantos snapshots tiene: uno por cuenta del corte.")
    firma_contenido: str = Field(
        description="La firma de la cartera, no del archivo: la misma en xlsx, csv o zip."
    )
    version_modelo: str
    dataset_id: UUID = Field(description="El dataset conformado que lo produjo.")
    run_id: UUID = Field(description="La corrida que publico ese dataset.")
    creado_en: datetime


class PaginaCortes(Pagina[CorteRespuesta]):
    despacho_id: str
    cartera_id: str
    ultimo_corte: CorteRespuesta | None = Field(
        description="El de fecha mas reciente, este o no en esta pagina. null si no hay ninguno."
    )


class FuenteDelCorteRespuesta(BaseModel):
    """Una ejecucion historica que publico el corte (CORTE_PUBLICADO) o lo reconocio como fuente
    equivalente (FUENTE_EQUIVALENTE): la misma cartera, llegada en otro archivo."""

    historia_run_id: UUID
    resultado: str
    dataset_id: UUID
    run_id: UUID
    artefacto_original: ArtefactoRespuesta


class CorteDetalleRespuesta(CorteRespuesta):
    """Un corte con su evidencia, de punta a punta."""

    artefacto_conformado: ArtefactoRespuesta = Field(
        description="El Parquet de su dataset: de ahi salieron sus snapshots."
    )
    artefacto_original: ArtefactoRespuesta = Field(
        description="El archivo tal como llego, con su SHA-256."
    )
    fuentes: list[FuenteDelCorteRespuesta]


EJEMPLO_HISTORIA = {
    "historia_run_id": "1d3f5a7c-9e1b-4d3f-8a7c-9e1b3d5f7a9c",
    "tipo_fuente": "CARTERA",
    "version_modelo": "historia/v1",
    "estado": "EXITOSA",
    "resultado": "CORTE_PUBLICADO",
    "dataset_id": EJEMPLO_SNAPSHOT["dataset_id"],
    "run_id": EJEMPLO_CORRIDA["run_id"],
    "pagos_run_id": None,
    "corte_id": EJEMPLO_SNAPSHOT["corte_id"],
    "fecha_corte": "2026-09-30",
    "registros_leidos": 500000,
    "registros_publicados": 500000,
    "trabajo_id": "6a8c0e2b-4d6f-4a8c-8e2b-4d6f8a0c2e4b",
    "iniciada_en": "2026-09-30T15:05:00.000000Z",
    "terminada_en": "2026-09-30T15:05:41.200000Z",
    "duracion_segundos": 41.2,
    "detalle": "Se publico el corte canonico 0b87ab2f-e6eb-54a6-b1b4-2826722801db del 2026-09-30 "
    "con 500,000 snapshots; 10,212 cuentas canonicas nuevas. historia/v1.",
}


class EjecucionHistoriaRespuesta(BaseModel):
    """Una materializacion de un dataset conformado en el modelo historico."""

    model_config = ConfigDict(json_schema_extra={"examples": [EJEMPLO_HISTORIA]})

    historia_run_id: UUID
    tipo_fuente: TipoFuenteHistoria = Field(description="CARTERA (un corte) o PAGOS.")
    version_modelo: str
    estado: EstadoHistoria = Field(
        description="EN_PROCESO hasta que un worker la termina. EXITOSA: publico o reconocio algo, "
        "segun su resultado. FALLIDA: no publico nada."
    )
    resultado: str | None = Field(
        description="Como termino: CORTE_PUBLICADO, FUENTE_EQUIVALENTE, PAGOS_PUBLICADOS, "
        "CORTE_CANONICO_CONFLICTIVO, DATOS_INCONSISTENTES... null mientras esta EN_PROCESO."
    )
    dataset_id: UUID
    run_id: UUID | None = Field(description="La corrida del dataset, si es de cartera.")
    pagos_run_id: UUID | None = Field(description="La ingesta del dataset, si es de pagos.")
    corte_id: UUID | None = Field(
        description="El corte que publico o al que es equivalente; null en las demas."
    )
    fecha_corte: date | None = Field(description="La del dataset, si es de cartera.")
    registros_leidos: int
    registros_publicados: int = Field(
        description="Snapshots o pagos observados que publico. 0 en una fuente equivalente: no "
        "duplica nada."
    )
    trabajo_id: UUID | None = Field(description="Su trabajo HISTORIA: GET /trabajos/{trabajo_id}.")
    iniciada_en: datetime
    terminada_en: datetime | None
    detalle: str | None

    @computed_field(description="Segundos de inicio a fin; vacio mientras esta en proceso.")
    @property
    def duracion_segundos(self) -> float | None:
        if self.terminada_en is None:
            return None
        return round((self.terminada_en - self.iniciada_en).total_seconds(), 3)


class RelacionPagosRespuesta(BaseModel):
    """Como se relacionan hoy los pagos observados de una ingesta con las cuentas canonicas. Se
    calcula al consultar: un corte que llega despues relaciona sus pagos sin tocarlos."""

    pagos_con_cuenta_observada: int
    pagos_sin_cuenta_observada: int = Field(
        description="SIN_CUENTA_OBSERVADA: pagos de un CLIENTE_UNICO que ningun corte de la "
        "cartera ha traido. Se conservan igual; ningun pago crea una cuenta."
    )
    clientes_sin_cuenta_observada: int


class PaginaHistoriasDeCorrida(Pagina[EjecucionHistoriaRespuesta]):
    run_id: UUID
    dataset_id: UUID


class PaginaHistoriasDePagos(Pagina[EjecucionHistoriaRespuesta]):
    pagos_run_id: UUID
    dataset_id: UUID
    relacion: RelacionPagosRespuesta | None = Field(
        description="Solo si sus pagos ya se publicaron (una ejecucion EXITOSA)."
    )


# --- el motor de pagos ----------------------------------------------------------------------------
#
# Lo que la API deja ver de la interpretacion de los pagos: siempre por identificadores publicos
# (motor_pagos_run_id, movimiento_id, pago_observado_id, cuenta_id), nunca por un id interno. Las
# huellas viajan en hexadecimal. Los importes, como texto, como en el resto de la API.

AVISO_MOVIMIENTOS = (
    "Estos son movimientos economicos interpretados por motor-pagos/v1 a partir de los pagos "
    "observados de pagos/v1. No son el libro contable del acreedor: no hay intereses, cargos, "
    "condonaciones ni ajustes que ninguna fuente trae, y la recuperacion interpretada no sustituye "
    "a la contabilidad oficial. Las observaciones tal como llegaron estan en /pagos-observados."
)
"""Lo que dice cada respuesta de movimientos, y su documentacion, de forma visible."""

AVISO_RECUPERACION = (
    "Recuperacion interpretada por motor-pagos/v1 sobre las fuentes disponibles, no un saldo "
    "contable. Bruta: los pagos que ningun reverso anulo. Neta: la bruta menos los negativos que "
    "no se pudieron enlazar con su original. No cuenta duplicados exactos ni coincidencias "
    "ambiguas."
)

EJEMPLO_MOTOR_PAGOS = {
    "motor_pagos_run_id": "2b4d6f8a-0c2e-4a6c-8e0a-2c4e6a8c0e2a",
    "version_motor": "motor-pagos/v1",
    "estado": "EXITOSA",
    "resultado": "INTERPRETACION_PUBLICADA",
    "despacho_id": "DSP_001",
    "cartera_id": "CARTERA_PRINCIPAL",
    "periodo": "2026-09",
    "periodo_desde": "2026-09-01",
    "periodo_hasta": "2026-10-01",
    "vigente": True,
    "firma_entrada": "9c1e7b5d3f2a4c6e8a0b2d4f6a8c0e2b4d6f8a0c2e4a6c8e0a2c4e6a8c0e2b4d",
    "calidad": {
        "observaciones_leidas": 1103715,
        "observaciones_contexto": 4127,
        "observaciones_clasificadas": 1103715,
        "movimientos_canonicos": 1098214,
        "movimientos_primarios": 1094879,
        "duplicados_exactos": 5501,
        "coincidencias_ambiguas": 0,
        "reversos": 12,
        "posibles_reversos": 3323,
        "no_conciliados": 0,
        "sin_cuenta_observada": 0,
        "grupos_exactos": 5501,
        "grupos_legacy": 5501,
        "grupos_ambiguos": 0,
        "observaciones_en_grupos_legacy": 11002,
        "pagos_anulados": 12,
    },
    "recuperacion": {
        "bruta_interpretada": "2031874550.12",
        "neta_interpretada": "2030512331.40",
        "importe_ambiguo_observado": "0.00",
        "aviso": AVISO_RECUPERACION,
    },
    "trabajo_id": "6a8c0e2b-4d6f-4a8c-8e2b-4d6f8a0c2e4b",
    "iniciada_en": "2026-10-01T03:00:00.000000Z",
    "terminada_en": "2026-10-01T03:01:12.500000Z",
    "duracion_segundos": 72.5,
    "detalle": "Se interpretaron 1,103,715 pagos observados recibidos desde el 2026-09-01 y antes "
    "del 2026-10-01, con 4,127 de contexto: ... motor-pagos/v1.",
}


class CalidadMotorPagosRespuesta(BaseModel):
    """Que encontro la ejecucion en su ventana: cuantas observaciones de cada clase, cuantos grupos
    de copias y de la llave historica, y cuantos pagos sin cuenta."""

    observaciones_leidas: int = Field(description="Los pagos observados de la ventana.")
    observaciones_contexto: int = Field(
        description="Los de fuera de la ventana que leyo para decidir sus reversos (hasta 30 dias "
        "antes y despues); no reciben resultado aqui."
    )
    observaciones_clasificadas: int = Field(description="Una por observacion, en una EXITOSA.")
    movimientos_canonicos: int
    movimientos_primarios: int = Field(description="Observaciones MOVIMIENTO_PRIMARIO: los PAGO.")
    duplicados_exactos: int
    coincidencias_ambiguas: int
    reversos: int
    posibles_reversos: int
    no_conciliados: int
    sin_cuenta_observada: int = Field(
        description="Observaciones de un CLIENTE_UNICO sin cuenta canonica cuando se interpretaron."
    )
    grupos_exactos: int = Field(description="Grupos de dos o mas observaciones identicas.")
    grupos_legacy: int = Field(
        description="Grupos de dos o mas observaciones con la misma llave historica (cliente, "
        "segundo de recepcion e importe)."
    )
    grupos_ambiguos: int = Field(description="De esos, los que no son copias identicas.")
    observaciones_en_grupos_legacy: int
    pagos_anulados: int = Field(description="Pagos de la ventana que anulo un reverso.")


class RecuperacionInterpretadaRespuesta(BaseModel):
    bruta_interpretada: Decimal
    neta_interpretada: Decimal
    importe_ambiguo_observado: Decimal = Field(
        description="Lo que reportan las observaciones ambiguas, sumado tal como llego: puede "
        "contar dos veces el mismo pago, y por eso no entra en ninguna recuperacion."
    )
    aviso: str = Field(default=AVISO_RECUPERACION)


class EjecucionMotorPagosRespuesta(BaseModel):
    """Una interpretacion de una ventana de pagos observados: un despacho, una cartera y un mes de
    recepcion."""

    model_config = ConfigDict(json_schema_extra={"examples": [EJEMPLO_MOTOR_PAGOS]})

    motor_pagos_run_id: UUID
    version_motor: str
    estado: EstadoMotorPagos = Field(
        description="EN_PROCESO hasta que un worker la termina. EXITOSA: publico un resultado por "
        "observacion y sus movimientos. FALLIDA: no publico nada."
    )
    resultado: str | None = Field(
        description="Como termino: INTERPRETACION_PUBLICADA, YA_INTERPRETADA (otra ejecucion ya "
        "interpreto exactamente las mismas entradas), ERROR_INTERNO... null mientras esta "
        "EN_PROCESO."
    )
    despacho_id: str
    cartera_id: str
    periodo: str = Field(description="El mes de la ventana, AAAA-MM.")
    periodo_desde: date = Field(description="Recibidos desde este dia, inclusive.")
    periodo_hasta: date = Field(description="Hasta antes de este dia.")
    vigente: bool = Field(
        description="Si es la interpretacion vigente de su ventana: su EXITOSA mas reciente. Las "
        "demas son historia y no cambian."
    )
    firma_entrada: str | None = Field(
        description="SHA-256 de lo que leyo: dos ejecuciones con la misma firma leyeron lo mismo, "
        "y la base no deja publicar la segunda."
    )
    calidad: CalidadMotorPagosRespuesta
    recuperacion: RecuperacionInterpretadaRespuesta
    trabajo_id: UUID | None = Field(description="Su trabajo MOTOR_PAGOS: GET /trabajos/{id}.")
    iniciada_en: datetime
    terminada_en: datetime | None
    detalle: str | None

    @computed_field(description="Segundos de inicio a fin; vacio mientras esta en proceso.")
    @property
    def duracion_segundos(self) -> float | None:
        if self.terminada_en is None:
            return None
        return round((self.terminada_en - self.iniciada_en).total_seconds(), 3)


class PaginaEjecucionesMotorPagos(Pagina[EjecucionMotorPagosRespuesta]):
    despacho_id: str
    cartera_id: str
    version_motor: str


class ParametrosEjecucionesMotorPagos(Paginacion):
    periodo: str | None = Field(
        default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="Solo las de un mes, AAAA-MM."
    )
    estado: EstadoMotorPagos | None = None
    version: str = Field(
        default=VERSION_MOTOR_PAGOS, max_length=32, description="Por omision, la del servicio."
    )


class MotivoMotorPagosRespuesta(BaseModel):
    """Por que una observacion quedo como quedo, con los datos que lo explican."""

    model_config = ConfigDict(extra="allow")

    codigo: str = Field(
        description="OBSERVACION_UNICA, REPRESENTANTE_DE_COPIAS, COPIA_EXACTA, "
        "LLAVE_HISTORICA_COMPARTIDA, PAREJA_UNICA, ANULADO_POR_REVERSO, SIN_CANDIDATOS, "
        "VARIOS_CANDIDATOS, CANDIDATO_AMBIGUO, ORIGINAL_DISPUTADO, IMPORTE_CERO o "
        "FIRMA_SIN_VALIDAR. Los demas campos dependen del codigo."
    )


class ResultadoPagoRespuesta(BaseModel):
    """Lo que una ejecucion concluyo de un pago observado."""

    pago_observado_id: UUID
    cliente_unico: str
    fecha_recepcion: datetime
    recuperacion_por_gestion: Decimal = Field(description="Como llego, con su signo.")
    clasificacion: Clasificacion
    estado_conciliacion: EstadoConciliacion
    movimiento_id: UUID | None = Field(
        description="El movimiento que funda o del que es copia; null si no funda ninguno."
    )
    movimiento_relacionado_id: UUID | None = Field(
        description="En un REVERSO, su original; en un pago anulado, su reverso."
    )
    firma_exacta: str = Field(description="Su firma exacta, en hexadecimal.")
    firma_legacy: str = Field(description="Su llave historica, en hexadecimal.")
    motivos: list[MotivoMotorPagosRespuesta]
    pagos_run_id: UUID
    dataset_id: UUID
    source_row: int
    source_sheet: str | None


class ParametrosResultados(Paginacion):
    clasificacion: Clasificacion | None = None
    cliente_unico: str | None = Field(default=None, pattern=r"^[A-Z0-9]{8,20}$")
    firma_exacta: str | None = Field(
        default=None,
        pattern=r"^[0-9a-fA-F]{64}$",
        description="Las observaciones con esta firma exacta, en hexadecimal: un grupo de copias.",
    )
    firma_legacy: str | None = Field(
        default=None,
        pattern=r"^[0-9a-fA-F]{64}$",
        description="Las observaciones con esta llave historica, en hexadecimal: el grupo de una "
        "coincidencia. Con cliente_unico se busca por el indice de los pagos de su cuenta.",
    )


class PaginaResultadosPago(Pagina[ResultadoPagoRespuesta]):
    motor_pagos_run_id: UUID


class SnapshotDelContextoRespuesta(BaseModel):
    """El snapshot de la cuenta en un corte, en breve: para mirar saldo antes y despues de un pago,
    sin afirmar que la diferencia la causo el pago."""

    fecha_corte: date
    corte_id: UUID
    saldo_total: Decimal
    dias_atraso: int


class ContextoTemporalRespuesta(BaseModel):
    """Donde cae el movimiento entre los snapshots de su cuenta. Se calcula al consultar: es
    informacion, no un error."""

    snapshot_anterior: SnapshotDelContextoRespuesta | None = Field(
        description="El del ultimo corte en que se observo la cuenta, del dia del pago o de antes."
    )
    snapshot_siguiente: SnapshotDelContextoRespuesta | None = Field(
        description="El del primer corte posterior al dia del pago en que se observo la cuenta."
    )
    antes_de_primera_observacion: bool
    despues_de_ultima_observacion: bool
    durante_ausencia_observada: bool = Field(
        description="La cuenta ya se habia observado y el corte mas reciente de su cartera al dia "
        "del pago no la traia."
    )


class MovimientoRespuesta(BaseModel):
    """Un movimiento economico canonico: lo que motor-pagos/v1 considera un hecho distinto."""

    movimiento_id: UUID = Field(
        description="Identificador interno de Motor Cartera, determinista; no es del acreedor."
    )
    version_motor: str
    motor_pagos_run_id: UUID = Field(description="La ejecucion que lo publico.")
    periodo: str
    vigente: bool = Field(
        description="Si es de la interpretacion vigente de su ventana. Uno que dejo de estar en "
        "ella se puede leer, como historia, con vigente=false."
    )
    tipo_movimiento: TipoMovimiento
    signo_economico: SignoEconomico
    monto_reportado: Decimal = Field(description="El importe recuperado, con su signo.")
    fecha_recepcion: datetime
    cliente_unico: str
    cuenta_id: UUID | None = Field(description="Su cuenta canonica, si se concilio con una.")
    estado_conciliacion: EstadoConciliacion
    observaciones: int = Field(description="Cuantas observaciones identicas lo sustentan.")
    movimiento_original_id: UUID | None = Field(description="En un REVERSO, el que revierte.")
    anulado_por_movimiento_id: UUID | None = Field(description="En un PAGO, el que lo anula.")
    firma_exacta: str


class ParametrosMovimientos(Paginacion):
    cliente_unico: str | None = Field(default=None, pattern=r"^[A-Z0-9]{8,20}$")
    cuenta_id: UUID | None = None
    desde: date | None = Field(default=None, description="Recibidos desde este dia, inclusive.")
    hasta: date | None = Field(default=None, description="Recibidos hasta este dia, inclusive.")
    tipo: TipoMovimiento | None = None
    estado_conciliacion: EstadoConciliacion | None = None
    version: str = Field(default=VERSION_MOTOR_PAGOS, max_length=32)


class PaginaMovimientos(Pagina[MovimientoRespuesta]):
    version_motor: str
    aviso: str = Field(default=AVISO_MOVIMIENTOS)


class ObservacionDeMovimientoRespuesta(BaseModel):
    """Un pago observado que sustenta un movimiento, con su evidencia de punta a punta."""

    clasificacion: Clasificacion = Field(
        description="MOVIMIENTO_PRIMARIO, REVERSO o POSIBLE_REVERSO si lo funda; DUPLICADO_EXACTO "
        "si es una copia."
    )
    motivos: list[MotivoMotorPagosRespuesta]
    pago_observado: PagoObservadoRespuesta
    artefacto_original: ArtefactoRespuesta = Field(
        description="El archivo tal como llego, con su SHA-256: la fuente de esta fila."
    )


class MovimientoDetalleRespuesta(MovimientoRespuesta):
    """Un movimiento con su por que y su evidencia."""

    clasificacion: Clasificacion = Field(description="La de la observacion que lo funda.")
    motivos: list[MotivoMotorPagosRespuesta]
    representante: ObservacionDeMovimientoRespuesta = Field(
        description="La observacion que lo funda: de menor (SHA-256 del archivo original, fila)."
    )
    contexto_temporal: ContextoTemporalRespuesta | None = Field(
        description="Si se concilio con una cuenta, donde cae entre sus snapshots."
    )
    aviso: str = Field(default=AVISO_MOVIMIENTOS)


class ParametrosObservacionesDeMovimiento(Paginacion):
    version: str = Field(
        default=VERSION_MOTOR_PAGOS, max_length=32, description="Por omision, la del servicio."
    )


class PaginaObservacionesDeMovimiento(Pagina[ObservacionDeMovimientoRespuesta]):
    movimiento_id: UUID
    motor_pagos_run_id: UUID


class MovimientoDeCuentaRespuesta(MovimientoRespuesta):
    contexto_temporal: ContextoTemporalRespuesta


class ParametrosMovimientosDeCuenta(Paginacion):
    desde: date | None = Field(default=None, description="Recibidos desde este dia, inclusive.")
    hasta: date | None = Field(default=None, description="Recibidos hasta este dia, inclusive.")


class PaginaMovimientosDeCuenta(Pagina[MovimientoDeCuentaRespuesta]):
    cuenta_id: UUID
    cliente_unico: str
    version_motor: str
    aviso: str = Field(default=AVISO_MOVIMIENTOS)


class ResumenPagosRespuesta(BaseModel):
    """Los pagos de la cuenta en numeros: las observaciones y lo que la interpretacion vigente dice
    de ellas. Los movimientos y las observaciones son subrecursos: aqui no estan."""

    version_motor: str
    observaciones: int = Field(description="Pagos observados con su CLIENTE_UNICO.")
    observaciones_interpretadas: int = Field(
        description="Las que tienen un resultado en la interpretacion vigente de su ventana."
    )
    movimientos_canonicos: int
    duplicados_exactos: int
    coincidencias_ambiguas: int
    reversos: int
    posibles_reversos: int
    no_conciliados: int
    pagos_anulados: int
    recuperacion_bruta_interpretada: Decimal
    recuperacion_neta_interpretada: Decimal
    aviso: str = Field(default=AVISO_RECUPERACION)


# La Cuenta 360 trae el resumen de sus pagos, que se declara aqui abajo.
Cuenta360Respuesta.model_rebuild()
