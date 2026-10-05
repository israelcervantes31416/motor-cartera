"""Contratos de fuentes oficiales: columnas exactas, tipos explicitos y juicio por lotes.

cartera/v1 es una forma minima, de 8 columnas, que se juzga con pandera sobre el archivo entero. Las
fuentes oficiales, cartera/v2 (93 columnas) y pagos/v1 (23), son el contrato tal como lo entrega el
acreedor, y pueden traer cientos de miles de filas. Se juzgan de otra forma:

- La estructura es exacta: las columnas del contrato, todas, y ninguna mas, con su nombre exacto.
  Solo se quitan los espacios alrededor de cada encabezado y se normaliza su Unicode (NFC), que no
  cambia lo que dice. Una columna que falta, una que sobra o un encabezado repetido es un error de
  estructura: el archivo no se juzga, y nada se descarta en silencio. El orden fisico de las
  columnas no significa nada: se leen por nombre y se ponen en el orden oficial.
- Cada registro se juzga contra las reglas de cada columna, por lotes y con operaciones
  vectorizadas, sin tener el archivo entero en memoria. Un registro que no cumple se rechaza con el
  motivo de cada regla que no cumplio, como en cartera/v1.
- De cada registro valido sale su forma canonica: cada valor en un texto unico. Un importe con dos
  decimales, un entero sin ceros a la izquierda, una fecha AAAA-MM-DD, un texto en NFC. Es la forma
  que se firma, la que se escribe en el dataset conformado y la que lee la proyeccion: asi la misma
  fuente da lo mismo venga en xlsx o en csv.

Las reglas son las que el contrato declara para cada columna; aqui no se inventa ninguna.
"""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import pandas as pd

from motor_cartera.contratos.cartera import Motivo


class Tipo(StrEnum):
    """Como se juzga y como se escribe en canonico el valor de una columna."""

    TEXTO = "texto"
    """Cualquier texto. Canonico: sin espacios alrededor, en NFC."""
    CODIGO = "codigo"
    """Texto con una forma exacta (una expresion regular), como un codigo postal."""
    CATALOGO = "catalogo"
    """Uno de una lista cerrada de valores."""
    ENTERO = "entero"
    """Digitos, con signo opcional. Canonico: sin ceros a la izquierda."""
    IMPORTE = "importe"
    """Pesos con punto decimal, hasta dos decimales que no sean cero y menos de 10^12. Sin
    separador de miles ni notacion cientifica. Canonico: con dos decimales."""
    DECIMAL = "decimal"
    """Un numero con punto decimal, de cualquier precision. Canonico: sin ceros sobrantes."""
    FECHA = "fecha"
    """AAAA-MM-DD, una fecha que exista en el calendario."""
    FECHA_HORA = "fecha_hora"
    """AAAA-MM-DD, o AAAA-MM-DD HH:MM:SS con microsegundos opcionales (la T tambien separa). Sin
    zona horaria: es la hora local de la fuente. Canonico: AAAA-MM-DDTHH:MM:SS[.ffffff]."""


IMPORTE_MAXIMO_DIGITOS = 12
"""Un importe tiene a lo mas 12 digitos enteros: menos de 10^12, lo que cabe en NUMERIC(14, 2)."""


@dataclass(frozen=True)
class Columna:
    """Una columna de un contrato fuente, con sus reglas."""

    nombre: str
    tipo: Tipo = Tipo.TEXTO
    requerida: bool = False
    patron: str | None = None
    """La forma exacta de un CODIGO."""
    catalogo: tuple[str, ...] = ()
    """Los valores de un CATALOGO."""
    minimo: int | None = None
    maximo: int | None = None
    """El rango de un ENTERO, un IMPORTE o un DECIMAL, inclusive."""
    descripcion: str = ""
    """Que es, para el diccionario de datos."""

    @property
    def reglas(self) -> str:
        """Las reglas de la columna, en palabras, para el diccionario de datos."""
        partes = ["requerida" if self.requerida else "opcional", self.tipo.value]
        if self.patron:
            partes.append(f"forma `{self.patron}`")
        if self.catalogo:
            partes.append("uno de " + ", ".join(f"`{v}`" for v in self.catalogo))
        if self.minimo is not None or self.maximo is not None:
            partes.append(f"entre {self.minimo} y {self.maximo}")
        return "; ".join(partes)


@dataclass(frozen=True)
class ContratoFuente:
    """El contrato de una fuente oficial: su version y sus columnas, en el orden oficial."""

    version: str
    columnas: tuple[Columna, ...]
    llave_unica: str | None = None
    """La columna que no se puede repetir dentro de un mismo archivo, si hay una."""

    def __post_init__(self) -> None:
        nombres = [c.nombre for c in self.columnas]
        if len(set(nombres)) != len(nombres):
            raise ValueError(f"{self.version}: el contrato repite columnas.")
        if self.llave_unica is not None and self.llave_unica not in nombres:
            raise ValueError(f"{self.version}: la llave {self.llave_unica} no es una columna.")

    @property
    def nombres(self) -> tuple[str, ...]:
        return tuple(c.nombre for c in self.columnas)


class ErrorDeEstructura(ValueError):
    """El archivo no tiene la forma del contrato: no se juzga ningun registro."""


def normalizar_encabezado(valor: object) -> str:
    """Un encabezado, sin espacios alrededor y en NFC. Nada mas: no se adivinan sinonimos."""
    texto = "" if valor is None else str(valor)
    return unicodedata.normalize("NFC", texto).strip()


def verificar_encabezado(encabezado: list[str], contrato: ContratoFuente, donde: str) -> list[str]:
    """Los encabezados normalizados, si son exactamente las columnas del contrato en cualquier
    orden; si no, ErrorDeEstructura con todo lo que no cuadra: columnas sin nombre, repetidas, las
    que faltan y las que sobran."""
    nombres = [normalizar_encabezado(h) for h in encabezado]
    oficiales = set(contrato.nombres)
    sin_nombre = [str(i) for i, nombre in enumerate(nombres, start=1) if not nombre]
    vistos: dict[str, int] = {}
    for nombre in nombres:
        vistos[nombre] = vistos.get(nombre, 0) + 1
    repetidos = [nombre for nombre, veces in vistos.items() if nombre and veces > 1]
    faltantes = [nombre for nombre in contrato.nombres if nombre not in vistos]
    sobrantes = [nombre for nombre in vistos if nombre and nombre not in oficiales]

    problemas = []
    if sin_nombre:
        problemas.append(f"columnas sin encabezado en las posiciones {', '.join(sin_nombre)}")
    if repetidos:
        problemas.append(f"encabezados repetidos: {', '.join(repetidos)}")
    if faltantes:
        problemas.append(f"faltan {len(faltantes)} columnas del contrato: {', '.join(faltantes)}")
    if sobrantes:
        problemas.append(
            f"sobran {len(sobrantes)} columnas que el contrato no tiene: {', '.join(sobrantes)}"
        )
    if problemas:
        raise ErrorDeEstructura(
            f"{donde} no tiene la estructura de {contrato.version}, que son "
            f"{len(contrato.nombres)} columnas exactas: " + "; ".join(problemas) + "."
        )
    return nombres


@dataclass(frozen=True)
class JuicioDeLote:
    """Un lote juzgado registro por registro."""

    canonicos: pd.DataFrame
    """Las filas que cumplen cada regla, con cada valor en su texto canonico y las columnas en el
    orden oficial. El indice es la fila del archivo."""
    rechazos: dict[int, list[Motivo]]
    """Por cada fila rechazada, las reglas que no cumplio, en el orden de las columnas."""


def juzgar_lote(datos: pd.DataFrame, contrato: ContratoFuente) -> JuicioDeLote:
    """Juzga cada registro de `datos` contra las reglas de cada columna.

    `datos` trae las columnas del contrato, como texto sin espacios alrededor, con un vacio como
    NA, y la fila del archivo como indice: es lo que entrega el lector por lotes. La unicidad de
    la llave no se juzga aqui, porque un lote no ve los demas: la juzga quien lee el archivo entero.
    """
    canon: dict[str, pd.Series] = {}
    motivos: dict[int, list[Motivo]] = {}
    invalida = pd.Series(False, index=datos.index)
    for columna in contrato.columnas:
        valores, fallas = _convertir(columna, datos[columna.nombre])
        for mascara, regla in fallas:
            if not mascara.any():
                continue
            invalida |= mascara
            motivo = Motivo(columna.nombre, regla)
            for fila in datos.index[mascara.to_numpy()]:
                motivos.setdefault(int(fila), []).append(motivo)
        canon[columna.nombre] = valores
    canonicos = pd.DataFrame(canon, index=datos.index).loc[~invalida.to_numpy()]
    return JuicioDeLote(canonicos, {fila: motivos[fila] for fila in sorted(motivos)})


def _convertir(columna: Columna, crudo: pd.Series) -> tuple[pd.Series, list[tuple[pd.Series, str]]]:
    """El valor canonico de cada fila (NA si esta vacio o no cumple) y cada regla incumplida, como
    una mascara de las filas que no la cumplen."""
    crudo = crudo.astype("string")
    presente = crudo.notna()
    fallas: list[tuple[pd.Series, str]] = []
    if columna.requerida:
        fallas.append((~presente, "requerido"))
    convertidor = _CONVERTIDORES[columna.tipo]
    canonico, propias = convertidor(columna, crudo, presente)
    fallas.extend(propias)
    return canonico, fallas


def _cumple(resultado: pd.Series) -> pd.Series:
    """Un resultado booleano con NA, como False."""
    return resultado.fillna(False).astype(bool)


def _texto(columna: Columna, crudo: pd.Series, presente: pd.Series):
    return crudo.str.normalize("NFC"), []


def _codigo(columna: Columna, crudo: pd.Series, presente: pd.Series):
    cumple = _cumple(crudo.str.fullmatch(columna.patron))
    return crudo.where(cumple), [(presente & ~cumple, f"forma({columna.patron})")]


def _catalogo(columna: Columna, crudo: pd.Series, presente: pd.Series):
    cumple = _cumple(crudo.isin(columna.catalogo))
    return crudo.where(cumple), [(presente & ~cumple, f"catalogo({'|'.join(columna.catalogo)})")]


def _entero(columna: Columna, crudo: pd.Series, presente: pd.Series):
    forma = _cumple(crudo.str.fullmatch(r"[+-]?\d{1,18}"))
    numeros = pd.to_numeric(
        crudo.where(forma), errors="coerce", dtype_backend="numpy_nullable"
    ).astype("Int64")
    fallas = [(presente & ~forma, "entero")]
    fuera = _fuera_de_rango(numeros, columna)
    if fuera is not None:
        fallas.append((forma & fuera, _rango(columna)))
    return numeros.astype("string"), fallas


def _importe(columna: Columna, crudo: pd.Series, presente: pd.Series):
    partes = crudo.str.extract(r"^([+-]?)(\d+)(?:\.(\d*))?$")
    signo, entera, fraccion = partes[0], partes[1], partes[2].fillna("")
    forma = entera.notna()
    entera = entera.str.lstrip("0").replace("", "0")
    fraccion = fraccion.str.rstrip("0")
    decimales = _cumple(fraccion.str.len() <= 2)
    cabe = _cumple(entera.str.len() <= IMPORTE_MAXIMO_DIGITOS)
    cero = _cumple((entera == "0") & (fraccion == ""))
    signo = signo.where(~cero & (signo == "-"), "")
    canonico = signo + entera + "." + fraccion.str.pad(2, side="right", fillchar="0")
    fallas = [
        (presente & ~forma, "importe"),
        (forma & ~decimales, "importe(2 decimales)"),
        (forma & decimales & ~cabe, f"importe(menos de 10^{IMPORTE_MAXIMO_DIGITOS})"),
    ]
    valido = forma & decimales & cabe
    fuera = _fuera_de_rango(pd.to_numeric(canonico.where(valido), errors="coerce"), columna)
    if fuera is not None:
        fallas.append((valido & fuera, _rango(columna)))
    return canonico.where(valido), fallas


def _decimal(columna: Columna, crudo: pd.Series, presente: pd.Series):
    partes = crudo.str.extract(r"^([+-]?)(\d+)(?:\.(\d+))?$")
    signo, entera, fraccion = partes[0], partes[1], partes[2].fillna("")
    forma = entera.notna()
    entera = entera.str.lstrip("0").replace("", "0")
    fraccion = fraccion.str.rstrip("0")
    cero = _cumple((entera == "0") & (fraccion == ""))
    signo = signo.where(~cero & (signo == "-"), "")
    punto = fraccion.where(fraccion == "", "." + fraccion)
    canonico = (signo + entera + punto).where(forma)
    fallas = [(presente & ~forma, "decimal")]
    fuera = _fuera_de_rango(pd.to_numeric(canonico, errors="coerce"), columna)
    if fuera is not None:
        fallas.append((forma & fuera, _rango(columna)))
    return canonico, fallas


def _fecha(columna: Columna, crudo: pd.Series, presente: pd.Series):
    forma = _cumple(crudo.str.fullmatch(r"\d{4}-\d{2}-\d{2}"))
    fechas = pd.to_datetime(crudo.where(forma), format="%Y-%m-%d", errors="coerce")
    cumple = forma & fechas.notna()
    return crudo.where(cumple), [(presente & ~cumple, "fecha(AAAA-MM-DD)")]


def _fecha_hora(columna: Columna, crudo: pd.Series, presente: pd.Series):
    partes = crudo.str.extract(
        r"^(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?)?$"
    )
    forma = partes[0].notna()
    fraccion = partes[4].fillna("").str.pad(6, side="right", fillchar="0")
    fraccion = fraccion.where((fraccion != "") & (fraccion != "000000"), "")
    canonico = (
        partes[0]
        + "T"
        + partes[1].fillna("00")
        + ":"
        + partes[2].fillna("00")
        + ":"
        + partes[3].fillna("00")
        + fraccion.where(fraccion == "", "." + fraccion)
    )
    instantes = pd.to_datetime(canonico.where(forma), format="ISO8601", errors="coerce")
    cumple = forma & instantes.notna()
    regla = "fecha_hora(AAAA-MM-DD[THH:MM:SS])"
    return canonico.where(cumple), [(presente & ~cumple, regla)]


_CONVERTIDORES = {
    Tipo.TEXTO: _texto,
    Tipo.CODIGO: _codigo,
    Tipo.CATALOGO: _catalogo,
    Tipo.ENTERO: _entero,
    Tipo.IMPORTE: _importe,
    Tipo.DECIMAL: _decimal,
    Tipo.FECHA: _fecha,
    Tipo.FECHA_HORA: _fecha_hora,
}


def _fuera_de_rango(numeros: pd.Series, columna: Columna) -> pd.Series | None:
    if columna.minimo is None and columna.maximo is None:
        return None
    fuera = pd.Series(False, index=numeros.index)
    if columna.minimo is not None:
        fuera |= _cumple(numeros < columna.minimo)
    if columna.maximo is not None:
        fuera |= _cumple(numeros > columna.maximo)
    return fuera


def _rango(columna: Columna) -> str:
    return f"rango({columna.minimo}..{columna.maximo})"


# --- la firma del contenido ----------------------------------------------------------------------


def linea_canonica(valores: list[str | None]) -> str:
    """Un registro en su linea canonica: cada valor como `largo:texto`, separados por comas, y un
    vacio como `-`. El largo (en caracteres) hace la linea inequivoca sin escapar nada, aunque un
    texto traiga comas, dos puntos o guiones."""
    return ",".join("-" if v is None else f"{len(v)}:{v}" for v in valores)


def lineas_canonicas(canonicos: pd.DataFrame, nombres: tuple[str, ...]) -> pd.Series:
    """`linea_canonica` de cada fila, vectorizada, con las columnas en el orden de `nombres`."""
    linea: pd.Series | None = None
    for nombre in nombres:
        valores = canonicos[nombre].astype("string")
        parte = (valores.str.len().astype("string") + ":" + valores).fillna("-")
        linea = parte if linea is None else linea + "," + parte
    if linea is None:
        return pd.Series([], dtype="string")
    return linea


class FirmaDeContenido:
    """El SHA-256 de un dataset en su forma canonica, que identifica el contenido y no el archivo.

    No depende del formato (xlsx, csv o zip), ni del orden fisico de las filas, ni del de las
    columnas, y cambia si cambia cualquier valor. La forma canonica, que es parte de cada contrato:

      SHA-256( version + "\\n"
             + linea_canonica(columnas en el orden oficial) + "\\n"
             + los SHA-256 de cada linea_canonica de registro, en binario y ordenados )

    Ordenar los SHA-256 de los registros, y no los registros, hace que la firma no dependa del
    orden de las filas sin tener el dataset entero en memoria: 500,000 registros son 16 MB de
    digests. Dos registros identicos aportan dos digests iguales: la firma cuenta repetidos.
    """

    def __init__(self, contrato: ContratoFuente) -> None:
        self._contrato = contrato
        self._digests: list[np.ndarray] = []
        self.registros = 0

    def agregar(self, canonicos: pd.DataFrame) -> None:
        lineas = lineas_canonicas(canonicos, self._contrato.nombres)
        digests = [hashlib.sha256(linea.encode("utf-8")).digest() for linea in lineas]
        if digests:
            self._digests.append(np.array(digests, dtype="S32"))
            self.registros += len(digests)

    def hexdigest(self) -> str:
        firma = hashlib.sha256()
        firma.update(f"{self._contrato.version}\n".encode())
        firma.update(linea_canonica(list(self._contrato.nombres)).encode("utf-8") + b"\n")
        if self._digests:
            firma.update(np.sort(np.concatenate(self._digests)).tobytes())
        return firma.hexdigest()
