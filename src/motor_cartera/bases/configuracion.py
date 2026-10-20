"""Una version de bases operativas: leerla, validarla, darle su hash y publicarla.

Las bases son CONFIGURACION OPERACIONAL VERSIONADA, no datos de CARTERA ni una fuente del acreedor.
Una version dice que bases hay, donde esta cada una (un punto en EPSG:4326), si esta activa y que
municipios puede atender (su cobertura, por CVEGEO). Nada mas: ni capacidad, ni gestores, ni
jornadas, ni rutas.

El documento, en JSON (POST /bases/versiones, `motor-cartera cargar-bases`):

    {"nombre": "Bases sinteticas",
     "bases": [{"codigo": "BASE_A", "nombre": "Base A", "lat": 19.05, "lon": -98.2,
                "activa": true, "municipios": ["21114", "21015"]}]}

o un JSONL con una base por linea y el nombre aparte. Se valida entero, fail-closed, y todos los
problemas se reportan juntos: una llave desconocida, un codigo repetido o mal formado, una
coordenada fuera de rango o (0, 0), una cobertura vacia, repetida o que no es un CVEGEO.

La forma canonica: las bases en orden de codigo, cada coordenada redondeada a 7 decimales (un
centimetro, la misma precision con que se guarda el punto) y sus municipios en orden. El nombre de
la version no entra: es una etiqueta, no cambia lo que la version dice. El mismo contenido es la
misma version: se reutiliza, sin duplicarla. Una version publicada no cambia nunca.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from sqlalchemy import text
from sqlmodel import Session, select

from motor_cartera.db.modelos import (
    PATRON_CODIGO_BASE,
    PATRON_CVEGEO,
    VersionBasesOperativas,
)
from motor_cartera.db.sesion import insertar_en_savepoint, restriccion
from motor_cartera.invariantes import presente

VERSION_CONFIGURACION = "bases-operativas/v1"
"""Cambia si cambia la forma canonica con que se calcula el hash."""
CUANTO = Decimal("0.0000001")
"""7 decimales de grado: un centimetro. Asi se redondea, se firma y se guarda cada coordenada."""
LLAVES_DEL_DOCUMENTO = frozenset({"nombre", "bases"})
LLAVES_DE_UNA_BASE = frozenset({"codigo", "nombre", "lat", "lon", "activa", "municipios"})
LARGO_DE_NOMBRE = 120
RESTRICCION_HASH = "uq_version_bases_hash"


class ConfiguracionInvalida(ValueError):
    """La configuracion no se puede publicar: no se registro nada. `problemas` dice cada uno."""

    def __init__(self, problemas: list[str]) -> None:
        super().__init__("La configuracion de bases no es valida: " + "; ".join(problemas) + ".")
        self.problemas = problemas


@dataclass(frozen=True)
class BaseConfigurada:
    codigo: str
    nombre: str
    latitud: Decimal
    longitud: Decimal
    activa: bool
    municipios: tuple[str, ...]

    def canonica(self) -> dict[str, Any]:
        return {
            "codigo": self.codigo,
            "nombre": self.nombre,
            "lat": str(self.latitud),
            "lon": str(self.longitud),
            "activa": self.activa,
            "municipios": list(self.municipios),
        }


@dataclass(frozen=True)
class ConfiguracionBases:
    nombre: str
    bases: tuple[BaseConfigurada, ...]

    def canonica(self) -> str:
        """El texto que se firma: la version de la forma y las bases, sin el nombre de la version,
        en JSON con las llaves en orden y sin espacios."""
        documento = {
            "version": VERSION_CONFIGURACION,
            "bases": [b.canonica() for b in self.bases],
        }
        return json.dumps(documento, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @property
    def hash(self) -> str:
        return hashlib.sha256(self.canonica().encode("utf-8")).hexdigest()

    @property
    def coberturas(self) -> int:
        return sum(len(b.municipios) for b in self.bases)


def _nombre(valor: Any, donde: str, problemas: list[str]) -> str:
    if not isinstance(valor, str) or not valor.strip():
        problemas.append(f"{donde}: el nombre es un texto no vacio")
        return ""
    nombre = unicodedata.normalize("NFC", valor.strip())
    if len(nombre) > LARGO_DE_NOMBRE:
        problemas.append(f"{donde}: el nombre tiene mas de {LARGO_DE_NOMBRE} caracteres")
    return nombre


def _coordenada(
    valor: Any, campo: str, minimo: int, maximo: int, donde: str, problemas: list[str]
) -> Decimal | None:
    # Un numero de JSON, no un texto ni un booleano: una coordenada "19,05" no se adivina.
    if isinstance(valor, bool) or not isinstance(valor, int | float):
        problemas.append(f"{donde}: {campo} es un numero")
        return None
    if not math.isfinite(valor) or not minimo <= valor <= maximo:
        problemas.append(f"{donde}: {campo} esta entre {minimo} y {maximo}")
        return None
    return Decimal(repr(valor)).quantize(CUANTO, rounding=ROUND_HALF_EVEN)


def _base(valor: Any, i: int, problemas: list[str]) -> BaseConfigurada | None:
    donde = f"bases[{i}]"
    if not isinstance(valor, dict):
        problemas.append(f"{donde} es un objeto")
        return None
    desconocidas = sorted(set(valor) - LLAVES_DE_UNA_BASE)
    if desconocidas:
        problemas.append(f"{donde} trae llaves que no se conocen: {', '.join(desconocidas)}")
    faltan = sorted(LLAVES_DE_UNA_BASE - set(valor))
    if faltan:
        problemas.append(f"{donde} no trae {', '.join(faltan)}")
        return None
    codigo = valor["codigo"]
    if not isinstance(codigo, str) or not re.fullmatch(PATRON_CODIGO_BASE, codigo):
        problemas.append(f"{donde}: el codigo cumple {PATRON_CODIGO_BASE}")
    else:
        donde = f"la base {codigo}"
    nombre = _nombre(valor["nombre"], donde, problemas)
    latitud = _coordenada(valor["lat"], "lat", -90, 90, donde, problemas)
    longitud = _coordenada(valor["lon"], "lon", -180, 180, donde, problemas)
    if latitud == 0 and longitud == 0:
        problemas.append(f"{donde}: (0, 0) no es la ubicacion de una base")
    activa = valor["activa"]
    if not isinstance(activa, bool):
        problemas.append(f"{donde}: activa es true o false")
    municipios = valor["municipios"]
    if not isinstance(municipios, list) or not municipios:
        problemas.append(f"{donde}: municipios es una lista no vacia de CVEGEO")
        municipios = []
    malos = [m for m in municipios if not isinstance(m, str) or not re.fullmatch(PATRON_CVEGEO, m)]
    if malos:
        problemas.append(f"{donde}: no son CVEGEO de 5 digitos: {', '.join(map(str, malos))}")
    repetidos = sorted({m for m in municipios if isinstance(m, str) and municipios.count(m) > 1})
    if repetidos:
        problemas.append(f"{donde}: repite municipios: {', '.join(repetidos)}")
    if latitud is None or longitud is None or not isinstance(activa, bool) or malos:
        return None
    return BaseConfigurada(
        codigo=codigo,
        nombre=nombre,
        latitud=latitud,
        longitud=longitud,
        activa=activa,
        municipios=tuple(sorted(set(municipios))),
    )


def leer(documento: Any) -> ConfiguracionBases:
    """La configuracion de un documento JSON ya decodificado, validada. Levanta
    ConfiguracionInvalida con todos sus problemas."""
    problemas: list[str] = []
    if not isinstance(documento, dict):
        raise ConfiguracionInvalida(["el documento es un objeto con nombre y bases"])
    desconocidas = sorted(set(documento) - LLAVES_DEL_DOCUMENTO)
    if desconocidas:
        problemas.append(f"el documento trae llaves que no se conocen: {', '.join(desconocidas)}")
    nombre = _nombre(documento.get("nombre"), "la version", problemas)
    bases = documento.get("bases")
    if not isinstance(bases, list) or not bases:
        problemas.append("bases es una lista no vacia")
        bases = []
    leidas = [_base(valor, i, problemas) for i, valor in enumerate(bases)]
    # Los codigos de todas las bases que traen uno, aunque tengan otros problemas: un codigo
    # repetido se reporta junto con todo lo demas.
    codigos = [
        b["codigo"] for b in bases if isinstance(b, dict) and isinstance(b.get("codigo"), str)
    ]
    repetidos = sorted({c for c in codigos if codigos.count(c) > 1})
    if repetidos:
        problemas.append(f"codigos repetidos: {', '.join(repetidos)}")
    if problemas:
        raise ConfiguracionInvalida(problemas)
    validas = [presente(b, "una base sin problemas") for b in leidas]
    return ConfiguracionBases(nombre, tuple(sorted(validas, key=lambda b: b.codigo)))


def leer_jsonl(texto: str, nombre: str) -> ConfiguracionBases:
    """Una configuracion en JSONL: una base por linea, con el nombre de la version aparte."""
    bases = []
    for numero, linea in enumerate(texto.splitlines(), start=1):
        if not linea.strip():
            continue
        try:
            bases.append(json.loads(linea))
        except json.JSONDecodeError as exc:
            raise ConfiguracionInvalida([f"la linea {numero} no es JSON: {exc.msg}"]) from exc
    return leer({"nombre": nombre, "bases": bases})


@dataclass(frozen=True)
class Publicada:
    """Una version publicada: la nueva, o la que ya existia con el mismo contenido."""

    version: VersionBasesOperativas
    creada: bool


def publicar(s: Session, configuracion: ConfiguracionBases) -> Publicada:
    """La version de esa configuracion, en la transaccion de quien llama: si ya hay una con el mismo
    hash, esa, sin escribir nada; si no, la version, sus bases y su cobertura, enviadas y sin
    confirmar. Dos publicaciones simultaneas del mismo contenido dan una sola version."""
    huella = configuracion.hash
    existente = _por_hash(s, huella)
    if existente is not None:
        return Publicada(existente, creada=False)
    version = VersionBasesOperativas(
        nombre=configuracion.nombre,
        version_configuracion=VERSION_CONFIGURACION,
        hash_configuracion=huella,
        bases=len(configuracion.bases),
        activas=sum(b.activa for b in configuracion.bases),
        coberturas=configuracion.coberturas,
    )
    error = insertar_en_savepoint(s, version)
    if error is not None:
        if restriccion(error) != RESTRICCION_HASH:
            raise error
        return Publicada(presente(_por_hash(s, huella), "la version con ese hash"), creada=False)
    for base in configuracion.bases:
        base_id = s.execute(
            text(
                "INSERT INTO base_operativa (base_id, version_bases_operativas_id, codigo, nombre, "
                "activa, punto) VALUES (gen_random_uuid(), :version, :codigo, :nombre, :activa, "
                "ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)) RETURNING id"
            ),
            {
                "version": version.id,
                "codigo": base.codigo,
                "nombre": base.nombre,
                "activa": base.activa,
                "lon": base.longitud,
                "lat": base.latitud,
            },
        ).scalar_one()
        s.execute(
            text(
                "INSERT INTO cobertura_base_municipio (base_operativa_id, cvegeo) "
                "SELECT :base, unnest(CAST(:municipios AS varchar[]))"
            ),
            {"base": base_id, "municipios": list(base.municipios)},
        )
    return Publicada(version, creada=True)


def _por_hash(s: Session, huella: str) -> VersionBasesOperativas | None:
    return s.exec(
        select(VersionBasesOperativas).where(VersionBasesOperativas.hash_configuracion == huella)
    ).first()


def configuracion_de(s: Session, version: VersionBasesOperativas) -> ConfiguracionBases:
    """La configuracion de una version publicada, leida de sus tablas: con ella se vuelve a
    calcular su hash y se comprueba que nada cambio."""
    filas = s.execute(
        text(
            "SELECT b.codigo, b.nombre, ST_Y(b.punto), ST_X(b.punto), b.activa, "
            "array_agg(k.cvegeo ORDER BY k.cvegeo) FROM base_operativa b "
            "JOIN cobertura_base_municipio k ON k.base_operativa_id = b.id "
            "WHERE b.version_bases_operativas_id = :version "
            "GROUP BY b.id ORDER BY b.codigo"
        ),
        {"version": version.id},
    ).all()
    return ConfiguracionBases(
        version.nombre,
        tuple(
            BaseConfigurada(
                codigo=codigo,
                nombre=nombre,
                latitud=Decimal(repr(latitud)).quantize(CUANTO, rounding=ROUND_HALF_EVEN),
                longitud=Decimal(repr(longitud)).quantize(CUANTO, rounding=ROUND_HALF_EVEN),
                activa=activa,
                municipios=tuple(municipios),
            )
            for codigo, nombre, latitud, longitud, activa, municipios in filas
        ),
    )
