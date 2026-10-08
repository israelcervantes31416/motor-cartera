"""Las huellas de un pago observado y los identificadores del Motor de Pagos, deterministas.

Dos huellas, las dos SHA-256 de una linea canonica (la de `contratos.fuente.linea_canonica`: cada
valor como `largo:texto`, separados por comas, y un vacio como `-`), con un prefijo que dice que
huella es y en que version:

- firma_exacta: `firma_exacta/v1`, el despacho, la cartera y los 23 campos de pagos/v1 en el orden
  del contrato. Dos observaciones con la misma firma exacta reportan exactamente lo mismo: el mismo
  cliente, el mismo instante, el mismo importe, el mismo gestor, todo.
- firma_legacy: `firma_legacy/v1`, el despacho, la cartera, el cliente, la fecha de recepcion
  truncada al segundo y el importe recuperado a centavos. Es la llave con que el sistema
  anterior juntaba pagos; aqui es solo una herramienta de comparacion.

Ninguna huella es un identificador de la fuente ni sustituye a los ids internos.

El texto canonico de cada valor es el de su tipo en PagoObservado, que es el del contrato: un
texto tal cual (el contrato ya lo dejo sin espacios alrededor y en NFC), un entero sin ceros a la
izquierda, un importe con dos decimales, un instante como AAAA-MM-DDTHH:MM:SS con sus microsegundos
solo si no son cero, y el porcentaje de comision, que es double precision, con el texto que
PostgreSQL le da a un float8 (la representacion mas corta que vuelve al mismo numero, en notacion
cientifica si su exponente es menor que -4 o de 15 o mas).

Cada huella existe dos veces: en SQL, que es la que calcula el motor sobre millones de filas dentro
de PostgreSQL, y en Python, que es la referencia legible y la que usan las pruebas para comprobar
que el SQL dice lo mismo.

Los identificadores publicos de los movimientos son UUID version 8 (RFC 9562, seccion 5.8) con
nombre y SHA-256, como el ejemplo del apendice B.2 del RFC: los primeros 128 bits del SHA-256 del
espacio de nombres y el nombre, con los bits de version y de variante. No son version 5 como los
del modelo historico porque PostgreSQL no trae SHA-1 sin una extension, y el motor genera millones
de identificadores dentro de la base; la propiedad que importa es la misma: deterministas, sin
revelar volumen ni orden.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from motor_cartera.contratos.fuente import Tipo, linea_canonica
from motor_cartera.contratos.pagos import CONTRATO_PAGOS
from motor_cartera.historia.carga import CAMPOS_PAGO

PREFIJO_EXACTA = "firma_exacta/v1"
PREFIJO_LEGACY = "firma_legacy/v1"

ESPACIO = UUID("6478ec69-cd8b-4d42-9760-53946192e428")
"""El espacio de nombres de los identificadores del Motor de Pagos. No cambia nunca: cambiarlo
cambiaria cada movimiento publicado."""


@dataclass(frozen=True)
class CampoFirmado:
    """Una columna de PagoObservado que entra en la firma exacta, con su tipo del contrato."""

    columna: str
    fuente: str
    tipo: Tipo


_COLUMNA_DE = {c.fuente: c.columna for c in CAMPOS_PAGO if c.fuente is not None}
CAMPOS_FIRMADOS: tuple[CampoFirmado, ...] = tuple(
    CampoFirmado(_COLUMNA_DE[c.nombre], c.nombre, c.tipo) for c in CONTRATO_PAGOS.columnas
)
"""Los 23 campos de pagos/v1, en el orden del contrato, con su columna en PagoObservado."""


# --- el texto canonico de cada valor --------------------------------------------------------------


def texto_canonico(valor: object, tipo: Tipo) -> str | None:
    """El texto con que un valor de PagoObservado entra en una huella. None si esta vacio."""
    if valor is None:
        return None
    if tipo in (Tipo.TEXTO, Tipo.CODIGO, Tipo.CATALOGO):
        return str(valor)
    if tipo == Tipo.ENTERO:
        return str(int(valor))
    if tipo == Tipo.IMPORTE:
        return _importe(Decimal(valor))
    if tipo == Tipo.FECHA_HORA:
        return _instante(valor)
    if tipo == Tipo.DECIMAL:
        return texto_flotante(float(valor))
    raise ValueError(f"pagos/v1 no tiene columnas de tipo {tipo}.")


def _importe(valor: Decimal) -> str:
    """Dos decimales; el cero, sin signo, como NUMERIC(14, 2) en PostgreSQL."""
    if valor == 0:
        return "0.00"
    return f"{valor.quantize(Decimal('0.01')):f}"


def _instante(valor: datetime) -> str:
    texto = valor.strftime("%Y-%m-%dT%H:%M:%S")
    return texto if valor.microsecond == 0 else f"{texto}.{valor.microsecond:06d}"


def texto_flotante(valor: float) -> str:
    """El texto que PostgreSQL le da a un float8 con extra_float_digits = 1: los digitos mas cortos
    que vuelven al mismo numero, en notacion cientifica si el exponente es menor que -4 o de 15 o
    mas, con el exponente con signo y al menos dos digitos."""
    if math.isnan(valor):
        return "NaN"
    if math.isinf(valor):
        return "Infinity" if valor > 0 else "-Infinity"
    if valor == 0:
        return "-0" if math.copysign(1.0, valor) < 0 else "0"
    signo, cifras, exponente = Decimal(repr(valor)).as_tuple()
    cifras = list(cifras)
    while len(cifras) > 1 and cifras[-1] == 0:
        cifras.pop()
        exponente += 1
    decimal10 = len(cifras) + exponente - 1
    menos = "-" if signo else ""
    if decimal10 < -4 or decimal10 >= 15:
        mantisa = str(cifras[0])
        if len(cifras) > 1:
            mantisa += "." + "".join(map(str, cifras[1:]))
        return f"{menos}{mantisa}e{'-' if decimal10 < 0 else '+'}{abs(decimal10):02d}"
    return menos + format(Decimal((0, tuple(cifras), exponente)), "f")


# --- las huellas, en Python -----------------------------------------------------------------------


def linea_exacta(despacho_id: str, cartera_id: str, valores: Mapping[str, object]) -> str:
    """La linea canonica de la firma exacta. `valores` trae cada columna de PagoObservado."""
    textos = [texto_canonico(valores[c.columna], c.tipo) for c in CAMPOS_FIRMADOS]
    return f"{PREFIJO_EXACTA}\n" + linea_canonica([despacho_id, cartera_id, *textos])


def firma_exacta(despacho_id: str, cartera_id: str, valores: Mapping[str, object]) -> bytes:
    return hashlib.sha256(linea_exacta(despacho_id, cartera_id, valores).encode("utf-8")).digest()


def linea_legacy(
    despacho_id: str,
    cartera_id: str,
    cliente_unico: str,
    fecha_recepcion: datetime,
    recuperacion_por_gestion: Decimal,
) -> str:
    segundo = fecha_recepcion.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")
    valores = [
        despacho_id,
        cartera_id,
        cliente_unico,
        segundo,
        _importe(Decimal(recuperacion_por_gestion)),
    ]
    return f"{PREFIJO_LEGACY}\n" + linea_canonica(valores)


def firma_legacy(
    despacho_id: str,
    cartera_id: str,
    cliente_unico: str,
    fecha_recepcion: datetime,
    recuperacion_por_gestion: Decimal,
) -> bytes:
    linea = linea_legacy(
        despacho_id, cartera_id, cliente_unico, fecha_recepcion, recuperacion_por_gestion
    )
    return hashlib.sha256(linea.encode("utf-8")).digest()


# --- las huellas, en SQL --------------------------------------------------------------------------


def _sql_texto(expresion: str, tipo: Tipo) -> str:
    """La expresion SQL del texto canonico de una columna. Un NULL sigue siendo NULL."""
    if tipo in (Tipo.TEXTO, Tipo.CODIGO, Tipo.CATALOGO):
        return expresion
    if tipo in (Tipo.ENTERO, Tipo.IMPORTE, Tipo.DECIMAL):
        return f"{expresion}::text"
    if tipo == Tipo.FECHA_HORA:
        return (
            f"(to_char({expresion}, 'YYYY-MM-DD\"T\"HH24:MI:SS') || CASE WHEN {expresion} = "
            f"date_trunc('second', {expresion}) THEN '' ELSE '.' || to_char({expresion}, 'US') "
            "END)"
        )
    raise ValueError(f"pagos/v1 no tiene columnas de tipo {tipo}.")


def _sql_linea(prefijo: str, textos: list[str]) -> str:
    partes = ", ".join(f"coalesce(length({t})::text || ':' || {t}, '-')" for t in textos)
    return f"('{prefijo}' || chr(10) || concat_ws(',', {partes}))"


def sql_firma_exacta(alias: str) -> str:
    """La firma exacta de cada fila de `alias` (una PagoObservado), como bytea de 32 bytes.
    Depende de extra_float_digits = 1, que el motor fija en su transaccion."""
    textos = [f"{alias}.despacho_id", f"{alias}.cartera_id"]
    textos += [_sql_texto(f"{alias}.{c.columna}", c.tipo) for c in CAMPOS_FIRMADOS]
    return f"sha256(convert_to({_sql_linea(PREFIJO_EXACTA, textos)}, 'UTF8'))"


def sql_firma_legacy(alias: str) -> str:
    segundo = f"to_char({alias}.fecha_recepcion, 'YYYY-MM-DD\"T\"HH24:MI:SS')"
    textos = [
        f"{alias}.despacho_id",
        f"{alias}.cartera_id",
        f"{alias}.cliente_unico",
        segundo,
        f"{alias}.recuperacion_por_gestion::text",
    ]
    return f"sha256(convert_to({_sql_linea(PREFIJO_LEGACY, textos)}, 'UTF8'))"


# --- los identificadores de los movimientos -------------------------------------------------------


def uuid_v8(espacio: UUID, nombre: str) -> UUID:
    """UUID version 8 con nombre y SHA-256 (RFC 9562, apendice B.2)."""
    digest = bytearray(hashlib.sha256(espacio.bytes + nombre.encode("utf-8")).digest()[:16])
    digest[6] = (digest[6] & 0x0F) | 0x80  # version 8
    digest[8] = (digest[8] & 0x3F) | 0x80  # variante RFC
    return UUID(bytes=bytes(digest))


def nombre_movimiento(version: str, firma: bytes) -> str:
    return f"movimiento:{version}:{firma.hex()}"


def movimiento_id(version: str, firma: bytes) -> UUID:
    """El identificador publico del movimiento que funda un grupo de observaciones identicas: sale
    de la version del motor y de su firma exacta, asi que reconstruir da el mismo, en cualquier
    orden, y otra version del motor da otro."""
    return uuid_v8(ESPACIO, nombre_movimiento(version, firma))


def sql_digest_movimiento(version: str, firma: str) -> str:
    """Los 16 bytes del SHA-256 de los que sale `movimiento_id`, en SQL, sobre la expresion bytea
    `firma`. Se calcula una vez por fila y `sql_uuid_de_digest` le pone version y variante."""
    if "'" in version:
        raise ValueError(f"Version invalida: {version!r}.")
    nombre = f"'movimiento:{version}:' || encode({firma}, 'hex')"
    return (
        f"substring(sha256('\\x{ESPACIO.hex}'::bytea || convert_to({nombre}, 'UTF8')) "
        "from 1 for 16)"
    )


def sql_uuid_de_digest(digest: str) -> str:
    """El UUID version 8 de una columna con su digest de 16 bytes, como en `uuid_v8`."""
    return (
        f"encode(set_byte(set_byte({digest}, 6, (get_byte({digest}, 6) & 15) | 128), 8, "
        f"(get_byte({digest}, 8) & 63) | 128), 'hex')::uuid"
    )
