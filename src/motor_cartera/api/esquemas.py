"""Lo que la API recibe y devuelve.

Las reglas no se repiten aqui: los estados salen del modelo y los catalogos del contrato.
Si el contrato cambia, la API cambia con el, sin que nadie tenga que acordarse.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from motor_cartera.contratos.cartera import CANALES, PRODUCTOS, VERSION_CONTRATO
from motor_cartera.contratos.cartera_v2 import VERSION_CONTRATO_V2
from motor_cartera.db.modelos import (
    EstadoCorrida,
    EstadoDecision,
    EstadoFlujo,
    EstadoIngestaPagos,
    EstadoRuteo,
    EstadoTerritorial,
    EstadoTrabajo,
    EtapaFlujo,
    TipoTrabajo,
)
from motor_cartera.decision.reglas import VERSION_REGLAS_DECISION
from motor_cartera.fuentes.proyeccion import VERSION_PROYECCION
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
        description="Que motor ejecuta: INGESTA, DECISION, TERRITORIAL o RUTEO, o INGESTA_PAGOS, "
        "que no es de ningun flujo."
    )
    estado: EstadoTrabajo = Field(
        description="PENDIENTE (en la cola), EJECUTANDO (lo tiene un worker), COMPLETADO (su "
        "recurso termino, EXITOSA o no) o FALLIDO (agoto sus intentos sin que su recurso "
        "terminara)."
    )
    objetivo_run_id: UUID = Field(
        description="El identificador publico de su recurso, segun el tipo: run_id, "
        "decision_run_id, territorial_run_id, ruteo_run_id o pagos_run_id."
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
