"""Contrato de datos de la cartera.

Un contrato declara, en un solo lugar, que forma debe tener un conjunto de datos para
que el resto del sistema pueda confiar en el. La validacion es *fail-closed*: si un
archivo no cumple, el proceso se detiene y no escribe nada. Es preferible no producir
salida a producir salida incorrecta, porque una salida incorrecta se usa para operar.
"""

from __future__ import annotations

import hashlib
import json
import numbers
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd
import pandera.pandas as pa
from pandera.engines.pandas_engine import DateTime
from pandera.typing import Series

VERSION_CONTRATO = "cartera/v1"
"""La version del contrato. Cada corrida guarda con cual se juzgo.

Cambia cuando cambia lo que decide si una cartera se publica o como se firma su contenido:
las reglas de cada registro, la de un solo corte por cartera (`fechas_de_corte`) o la forma
canonica de `firmar_contenido`. Asi, de cualquier corrida se sabe con que reglas se juzgo,
aunque el contrato cambie despues.
"""

PRODUCTOS = ("CONSUMO", "TARJETA", "NOMINA", "AUTOMOTRIZ")
CANALES = ("CAMPO", "TELEFONICA", "DIGITAL")

SALDO_MAXIMO = 10**12
"""Tope que cabe en NUMERIC(14, 2), la columna donde se guarda el saldo.

No es una regla de negocio: es que el contrato no puede aceptar lo que la base no puede
guardar. Sin este tope, una sola fila absurda haria fallar la corrida entera al insertar,
en lugar de rechazarse con su motivo.
"""

CENTAVO = Decimal("0.01")
"""El saldo se guarda con dos decimales, y con dos se escribe en la forma canonica."""


class ErrorDeContrato(RuntimeError):
    """Se levanta cuando un conjunto de datos no cumple su contrato.

    Lleva el reporte de pandera para que la bitacora registre que fallo y en que filas.
    """

    def __init__(self, mensaje: str, fallas: pd.DataFrame | None = None) -> None:
        super().__init__(mensaje)
        self.fallas = fallas


class CarteraCruda(pa.DataFrameModel):
    """Lo minimo que debe traer una cartera para entrar al sistema."""

    cliente_unico: Series[str] = pa.Field(unique=True, str_matches=r"^[A-Z0-9]{8,20}$")
    saldo_total: Series[float] = pa.Field(ge=0, lt=SALDO_MAXIMO, nullable=False)
    dias_atraso: Series[int] = pa.Field(ge=0, le=3650)
    producto: Series[str] = pa.Field(isin=PRODUCTOS)
    canal: Series[str] = pa.Field(isin=CANALES)
    cve_entidad: Series[str] = pa.Field(str_matches=r"^\d{2}$")
    cve_municipio: Series[str] = pa.Field(str_matches=r"^\d{3}$")
    # Solo ISO 8601. Con el formato por defecto, pandas infiere el formato de la primera
    # fila: "01/02/2026" puede entrar como 2 de enero o como 1 de febrero sin avisar, y
    # un archivo con formatos mezclados falla sin decir en que fila. Una fecha ambigua se
    # rechaza; no se adivina.
    fecha_corte: Series[DateTime] = pa.Field(
        nullable=False, dtype_kwargs={"to_datetime_kwargs": {"format": "ISO8601"}}
    )

    class Config:
        strict = "filter"  # columnas extra se descartan, no rompen
        coerce = True

    # TODO(israel): agrega aqui las validaciones cruzadas que el sistema necesite.
    # Ejemplos que valen la pena y que ya sabes justificar:
    #   - un saldo mayor a cero exige dias_atraso coherente con la fecha de corte
    #   - la combinacion cve_entidad + cve_municipio debe existir en el catalogo INEGI
    # Se declaran con @pa.dataframe_check.


def validar(df: pd.DataFrame, modelo: type[pa.DataFrameModel] = CarteraCruda) -> pd.DataFrame:
    """Valida `df` contra `modelo`. Devuelve el DataFrame validado o levanta ErrorDeContrato.

    `lazy=True` recoge TODAS las violaciones antes de fallar, en vez de detenerse en la
    primera. Eso importa: un archivo con 400 filas malas se corrige una vez, no 400 veces.

    Con un matiz: si una columna no se puede convertir a su tipo en alguna fila, pandera
    ya no evalua las demas reglas de esa columna fila por fila. `validar` sigue fallando
    como debe, pero su reporte puede no listar todo. El detalle completo por fila lo da
    `separar_rechazos`.
    """
    try:
        return modelo.validate(df, lazy=True)
    except pa.errors.SchemaErrors as exc:
        n = len(exc.failure_cases)
        raise ErrorDeContrato(
            f"La cartera no cumple el contrato {modelo.__name__}: {n} violaciones.",
            fallas=exc.failure_cases,
        ) from exc


@dataclass(frozen=True)
class Motivo:
    """Una regla del contrato que un registro no cumplio."""

    campo: str
    regla: str
    """La regla tal como la nombra pandera, p. ej. `greater_than_or_equal_to(0)`."""


@dataclass(frozen=True)
class Separacion:
    """Un archivo juzgado registro por registro."""

    validas: pd.DataFrame
    """Filas que cumplen el contrato completo, ya convertidas a sus tipos."""
    rechazos: dict[int, list[Motivo]]
    """Por cada fila rechazada (etiqueta del indice), las reglas que no cumplio."""


def separar_rechazos(
    df: pd.DataFrame, modelo: type[pa.DataFrameModel] = CarteraCruda
) -> Separacion:
    """Separa las filas que cumplen el contrato de las que no, con el motivo de cada rechazo.

    `validar` responde si un archivo completo cumple. Esto responde otra pregunta: que
    filas no cumplen y por que, para que la corrida pueda rechazarlas sin adivinar.

    Se valida columna por columna y, dentro de cada columna, se repite sobre lo que si se
    pudo convertir. Validar todo de una pasada no basta: con una sola fila con "N/D" en el
    saldo, pandera deja de evaluar `saldo >= 0` fila por fila y un saldo negativo pasaria
    como valido. Al final se valida el contrato completo sobre lo que queda, para las
    reglas que cruzan columnas y para devolver los datos ya convertidos.

    Un duplicado rechaza a todas sus copias: no hay forma de saber cual es la buena.

    Si una falla no se puede atribuir a filas concretas (falta una columna, por ejemplo),
    no hay rechazo por registro posible: se levanta ErrorDeContrato.
    """
    esquema = modelo.to_schema()
    faltantes = [nombre for nombre in esquema.columns if nombre not in df.columns]
    if faltantes:
        raise ErrorDeContrato(f"Faltan columnas del contrato: {', '.join(faltantes)}.")

    motivos: dict[int, list[Motivo]] = {}
    for nombre, columna in esquema.columns.items():
        parcial = pa.DataFrameSchema({nombre: columna}, coerce=esquema.coerce)
        convertibles = _descartar_fechas_inconvertibles(columna, df[[nombre]], motivos)
        _descartar_hasta_cumplir(parcial, convertibles, motivos)

    validas = _descartar_hasta_cumplir(esquema, df.drop(index=list(motivos)), motivos)
    rechazos = {fila: _sin_redundancias(motivos[fila]) for fila in sorted(motivos)}
    return Separacion(validas=validas, rechazos=rechazos)


def _descartar_fechas_inconvertibles(
    columna: pa.Column, datos: pd.DataFrame, motivos: dict[int, list[Motivo]]
) -> pd.DataFrame:
    """Rechaza de un golpe las fechas que no se pueden convertir, y devuelve el resto.

    Es solo por velocidad. Cuando una fecha no convierte, pandera busca las culpables
    celda por celda: 3 segundos por cada 10,000 filas. Aqui se convierte la columna
    entera con las mismas opciones que declara el contrato (no se repiten: se leen del
    esquema) y pandera recibe solo lo que si convirtio. El motivo es el mismo que daria el.
    """
    tipo = columna.dtype
    if not isinstance(tipo, DateTime):
        return datos
    crudo = datos[columna.name]
    convertido = pd.to_datetime(crudo, errors="coerce", **(tipo.to_datetime_kwargs or {}))
    inconvertibles = crudo.notna() & convertido.isna()
    motivo = Motivo(columna.name, f"coerce_dtype('{tipo}')")
    for fila in datos.index[inconvertibles]:
        motivos.setdefault(int(fila), []).append(motivo)
    return datos[~inconvertibles]


def _descartar_hasta_cumplir(
    esquema: pa.DataFrameSchema, datos: pd.DataFrame, motivos: dict[int, list[Motivo]]
) -> pd.DataFrame:
    """Valida `datos`; mientras falle, anota los motivos de cada fila y la descarta.

    Termina porque cada vuelta descarta al menos una fila o levanta ErrorDeContrato.
    """
    while True:
        try:
            return esquema.validate(datos, lazy=True)
        except pa.errors.SchemaErrors as exc:
            fallas = exc.failure_cases

        atribuibles = fallas[fallas["index"].notna()]
        if atribuibles.empty:
            raise ErrorDeContrato(
                "La cartera tiene fallas que no se pueden atribuir a filas concretas: "
                + "; ".join(sorted(set(fallas["check"].astype(str)))),
                fallas=fallas,
            )
        filas = atribuibles[["index", "column", "check"]].itertuples(index=False)
        for fila, campo, regla in filas:
            motivos.setdefault(int(fila), []).append(Motivo(str(campo), str(regla)))
        datos = datos.drop(index=atribuibles["index"].unique())


def _sin_redundancias(motivos: list[Motivo]) -> list[Motivo]:
    """Quita repetidos y el `dtype(...)` que pandera agrega junto a cada `coerce_dtype(...)`.

    Son la misma falla contada dos veces: el valor no se pudo convertir a su tipo.
    """
    convertidas = {m.campo for m in motivos if m.regla.startswith("coerce_dtype(")}
    unicos = dict.fromkeys(motivos)
    return [m for m in unicos if not (m.regla.startswith("dtype(") and m.campo in convertidas)]


def fechas_de_corte(validas: pd.DataFrame) -> dict[date, int]:
    """Cuantos registros validos trae cada fecha de corte, de la mas antigua a la mas reciente.

    Una cartera es la foto de un dia: para publicarse tiene que traer exactamente un corte.
    Con mas de uno no se elige ninguno (ni el mas reciente ni el mas comun) y la cartera se
    rechaza. Cuentan solo los registros validos, que son los que se publicarian.
    """
    if validas.empty:
        return {}
    dias = validas["fecha_corte"].dt.date
    return {dia: int(n) for dia, n in sorted(dias.value_counts().items())}


def firmar_contenido(validas: pd.DataFrame, modelo: type[pa.DataFrameModel] = CarteraCruda) -> str:
    """SHA-256 de la forma canonica de los registros validos: identifica la cartera, no el
    archivo que la trajo.

    La misma cartera da la misma firma venga en xlsx, csv o zip, y con sus filas en
    cualquier orden. La forma canonica:

      - una linea con los nombres de las columnas del contrato en orden alfabetico, y una
        por registro, con sus valores en ese orden;
      - cada linea es un arreglo JSON sin espacios, en UTF-8, terminado en salto de linea;
      - las lineas de registros van ordenadas: el orden de las filas del archivo no
        significa nada;
      - cada valor es texto. La fecha, AAAA-MM-DD; un entero, sin ceros a la izquierda; el
        saldo, con dos decimales, como se guarda; lo demas, tal como lo deja el contrato.
        Un vacio es null.

    Es parte del contrato, igual que sus reglas: un cambio incompatible en esta forma cambia
    todas las firmas, y exige una VERSION_CONTRATO nueva.
    """
    columnas = sorted(modelo.to_schema().columns)
    registros = sorted(
        _linea([_canonico(valor) for valor in fila])
        for fila in validas[columnas].itertuples(index=False, name=None)
    )
    firma = hashlib.sha256()
    for linea in (_linea(columnas), *registros):
        firma.update(linea.encode("utf-8") + b"\n")
    return firma.hexdigest()


def _linea(valores: list[str | None]) -> str:
    return json.dumps(valores, ensure_ascii=False, separators=(",", ":"))


def _canonico(valor: object) -> str | None:
    """Un valor, ya convertido por el contrato, en su texto canonico."""
    if valor is None or pd.isna(valor):
        return None
    if isinstance(valor, datetime):  # pd.Timestamp tambien lo es
        return valor.date().isoformat()
    if isinstance(valor, date):
        return valor.isoformat()
    if isinstance(valor, numbers.Integral):
        return str(int(valor))
    if isinstance(valor, float):
        if valor == 0:
            valor = 0.0  # -0.0 y 0.0 son el mismo saldo
        return str(Decimal(str(valor)).quantize(CENTAVO, rounding=ROUND_HALF_UP))
    return str(valor)
