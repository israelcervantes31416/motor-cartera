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
from motor_cartera.db.modelos import EstadoCorrida, EstadoDecision, EstadoTerritorial
from motor_cartera.decision.reglas import VERSION_REGLAS_DECISION
from motor_cartera.ingesta.lectores import REQUERIDAS
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
        description="EN_PROCESO mientras trabaja. Al terminar: EXITOSA (publico sus cuentas), "
        "RECHAZADA (demasiados registros no cumplen el contrato, o la cartera trae mas de una "
        "fecha de corte; no publico nada) o FALLIDA (no se pudo juzgar: archivo ilegible o "
        "error; no publico nada)."
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
    valores: dict[str, str | None] = Field(description="El registro tal como llego, en texto.")
    motivos: list[MotivoRespuesta]

    @field_validator("valores")
    @classmethod
    def _en_el_orden_del_contrato(cls, valores: dict[str, str | None]) -> dict[str, str | None]:
        # JSONB no conserva el orden de las llaves; quien corrige el archivo lo lee en este.
        return {columna: valores[columna] for columna in REQUERIDAS if columna in valores}


class PaginaRechazos(Pagina[RechazoRespuesta]):
    run_id: UUID
    estado: EstadoCorrida


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

# Lo que responde el POST si el motor falla: la ejecucion se creo, pero no publico ninguna decision.
EJEMPLO_EJECUCION_FALLIDA = {
    **EJEMPLO_EJECUCION,
    "estado": "FALLIDA",
    "terminada_en": "2026-09-30T15:10:00.910000Z",
    "duracion_segundos": 0.91,
    "cuentas_evaluadas": 4000,
    "cuentas_decididas": 0,
    "detalle": "Error interno (RuntimeError); ver la bitacora.",
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

    model_config = ConfigDict(json_schema_extra={"examples": [EJEMPLO_EJECUCION]})

    decision_run_id: UUID = Field(description="Identificador publico de la ejecucion.")
    run_id: UUID = Field(description="La corrida que se decidio.")
    version_reglas: str = Field(description="Con que reglas se decidio, p. ej. `decision/v1`.")
    estado: EstadoDecision = Field(
        description="EN_PROCESO mientras decide. Al terminar: EXITOSA (publico una decision por "
        "cada cuenta de la corrida) o FALLIDA (no publico ninguna; `detalle` dice por que)."
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

# Lo que responde el POST si el motor falla: la ejecucion se creo, pero no publico ningun municipio.
EJEMPLO_EJECUCION_TERRITORIAL_FALLIDA = {
    **EJEMPLO_EJECUCION_TERRITORIAL,
    "estado": "FALLIDA",
    "terminada_en": "2026-09-30T15:12:00.310000Z",
    "duracion_segundos": 0.31,
    "territorios_publicados": 0,
    "detalle": "Error interno (RuntimeError); ver la bitacora.",
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

    model_config = ConfigDict(json_schema_extra={"examples": [EJEMPLO_EJECUCION_TERRITORIAL]})

    territorial_run_id: UUID = Field(description="Identificador publico de la ejecucion.")
    decision_run_id: UUID = Field(
        description="La ejecucion de decision cuyas decisiones se organizaron."
    )
    run_id: UUID = Field(description="La corrida de esas decisiones.")
    version_reglas: str = Field(description="Con que reglas se organizo, p. ej. `territorial/v1`.")
    estado: EstadoTerritorial = Field(
        description="EN_PROCESO mientras organiza. Al terminar: EXITOSA (publico un resultado por "
        "cada municipio con decisiones) o FALLIDA (no publico ninguno; `detalle` dice por que)."
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
