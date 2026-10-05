"""El catalogo geografico con que la proyeccion resuelve ESTADO_CTE y POBLACION_CTE.

Los motores agrupan por cve_entidad y cve_municipio, las claves del Marco Geoestadistico del
INEGI. cartera/v2 no trae claves: trae el nombre del estado y el de la poblacion. La proyeccion los
resuelve con este catalogo, que es publico y esta versionado con el codigo (catalogo_inegi.json; su
procedencia es scripts/actualizar_catalogo_geografico.py). Ninguna clave se inventa.

Como se resuelve, sin adivinar:

- Los nombres se comparan normalizados: sin acentos, en mayusculas, sin puntos y con los espacios
  colapsados. `Tehuacán`, `TEHUACAN` y ` tehuacan ` son el mismo nombre.
- Una entidad se reconoce por su nombre oficial, por su abreviatura del INEGI o por uno de los
  pocos alias de ALIAS_DE_ENTIDAD, que se controlan aqui, a mano.
- POBLACION_CTE se interpreta como el nombre de un municipio de esa entidad. Una localidad que no es
  un municipio no se resuelve: la geografia por localidad, colonia o codigo postal es posterior.
- Un nombre que, normalizado, corresponde a dos municipios de la misma entidad es ambiguo (Oaxaca
  tiene dos San Juan Mixtepec): no se elige ninguno, y el registro se rechaza con ese motivo.
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import pandas as pd

CATALOGO = Path(__file__).with_name("catalogo_inegi.json")

ALIAS_DE_ENTIDAD = {
    "05": ("COAHUILA",),
    "09": ("CDMX", "DISTRITO FEDERAL", "DF", "D F"),
    "15": ("ESTADO DE MEXICO", "EDO MEX", "EDO DE MEXICO", "EDOMEX"),
    "16": ("MICHOACAN",),
    "30": ("VERACRUZ",),
}
"""Nombres comunes de una entidad que no son el oficial ni su abreviatura. Solo estos se aceptan."""

DESCONOCIDO = ""
AMBIGUO = "?"
"""Marcas internas de la resolucion, que nunca son una clave de tres digitos."""

ENTIDAD_DESCONOCIDA = "catalogo_inegi(entidad)"
MUNICIPIO_DESCONOCIDO = "catalogo_inegi(municipio)"
MUNICIPIO_AMBIGUO = "catalogo_inegi(municipio ambiguo)"
"""Las reglas de un rechazo geografico, como las ve quien corrige el archivo."""


def normalizar_nombre(texto: str) -> str:
    """Sin acentos, en mayusculas, sin puntos ni comas, con los espacios colapsados."""
    descompuesto = unicodedata.normalize("NFKD", str(texto))
    sin_acentos = "".join(c for c in descompuesto if not unicodedata.combining(c))
    return " ".join(sin_acentos.upper().replace(".", " ").replace(",", " ").split())


@dataclass(frozen=True)
class Municipio:
    cve_entidad: str
    cve_municipio: str
    nombre: str
    """El nombre oficial, con sus acentos."""
    poblacion: int | None
    """La poblacion del censo; los municipios creados despues del censo no la tienen."""


@dataclass(frozen=True)
class Entidad:
    cve_entidad: str
    nombre: str
    abreviatura: str
    municipios: tuple[Municipio, ...]


@dataclass(frozen=True)
class Catalogo:
    """El catalogo cargado, con sus indices por nombre normalizado."""

    consultado: str
    """Cuando se consulto la fuente."""
    entidades: tuple[Entidad, ...]
    por_entidad: dict[str, str]
    """Nombre normalizado de una entidad (oficial, abreviatura o alias) -> cve_entidad."""
    por_municipio: dict[tuple[str, str], str | None]
    """(cve_entidad, nombre normalizado) -> cve_municipio, o None si el nombre es ambiguo."""

    def entidad(self, cve_entidad: str) -> Entidad:
        return next(e for e in self.entidades if e.cve_entidad == cve_entidad)


@cache
def catalogo() -> Catalogo:
    """El catalogo del paquete. Se carga una vez por proceso."""
    documento = json.loads(CATALOGO.read_text(encoding="ascii"))
    entidades = tuple(
        Entidad(
            cve_entidad=e["cve_ent"],
            nombre=e["nombre"],
            abreviatura=e["abreviatura"],
            municipios=tuple(
                Municipio(e["cve_ent"], m["cve_mun"], m["nombre"], m["poblacion"])
                for m in e["municipios"]
            ),
        )
        for e in documento["entidades"]
    )
    por_entidad: dict[str, str] = {}
    for entidad in entidades:
        alias = ALIAS_DE_ENTIDAD.get(entidad.cve_entidad, ())
        nombres = (entidad.nombre, entidad.abreviatura, *alias)
        for nombre in nombres:
            clave = normalizar_nombre(nombre)
            if por_entidad.get(clave, entidad.cve_entidad) != entidad.cve_entidad:
                raise ValueError(f"El nombre de entidad {clave!r} es de dos entidades.")
            por_entidad[clave] = entidad.cve_entidad
    por_municipio: dict[tuple[str, str], str | None] = {}
    for entidad in entidades:
        for municipio in entidad.municipios:
            clave = (entidad.cve_entidad, normalizar_nombre(municipio.nombre))
            # Un nombre que ya estaba en la entidad es ambiguo: se marca, y no se elige ninguno.
            por_municipio[clave] = None if clave in por_municipio else municipio.cve_municipio
    return Catalogo(documento["consultado"], entidades, por_entidad, por_municipio)


@dataclass(frozen=True)
class Resolucion:
    """Lo que el catalogo dijo de cada fila: sus claves, o por que no las tiene."""

    cve_entidad: pd.Series
    cve_municipio: pd.Series
    motivo: pd.Series
    """La regla que no se cumplio (ENTIDAD_DESCONOCIDA...), o NA si la fila se resolvio."""
    campo: pd.Series
    """La columna del motivo: ESTADO_CTE o POBLACION_CTE."""


def resolver(estados: pd.Series, poblaciones: pd.Series) -> Resolucion:
    """Resuelve cada par (estado, poblacion) a sus claves del INEGI.

    Se resuelve cada nombre distinto una sola vez: 500,000 cuentas traen unas decenas de estados y
    unos cientos de poblaciones.
    """
    cat = catalogo()
    estados = estados.astype("string")
    poblaciones = poblaciones.astype("string")
    entidad_de = {e: cat.por_entidad.get(normalizar_nombre(e)) for e in estados.dropna().unique()}
    cve_entidad = estados.map(entidad_de).astype("string")

    pares = pd.DataFrame({"e": cve_entidad, "p": poblaciones}).dropna().drop_duplicates()
    municipio_de = {}
    for e, p in pares.itertuples(index=False, name=None):
        encontrado = cat.por_municipio.get((e, normalizar_nombre(p)), DESCONOCIDO)
        municipio_de[(e, p)] = AMBIGUO if encontrado is None else encontrado
    claves = pd.Series(list(zip(cve_entidad, poblaciones, strict=True)), index=estados.index)
    resultado = claves.map(lambda par: municipio_de.get(par, DESCONOCIDO)).astype("string")
    desconocido = resultado == DESCONOCIDO
    ambiguo = resultado == AMBIGUO
    cve_municipio = resultado.where(~(desconocido | ambiguo))

    sin_entidad = estados.notna() & cve_entidad.isna()
    motivo = pd.Series(pd.NA, index=estados.index, dtype="string")
    campo = pd.Series(pd.NA, index=estados.index, dtype="string")
    con_entidad = cve_entidad.notna() & poblaciones.notna()
    sin_municipio = con_entidad & desconocido
    con_ambiguedad = con_entidad & ambiguo
    motivo = motivo.mask(sin_entidad, ENTIDAD_DESCONOCIDA)
    campo = campo.mask(sin_entidad, "ESTADO_CTE")
    motivo = motivo.mask(sin_municipio, MUNICIPIO_DESCONOCIDO)
    campo = campo.mask(sin_municipio, "POBLACION_CTE")
    motivo = motivo.mask(con_ambiguedad, MUNICIPIO_AMBIGUO)
    campo = campo.mask(con_ambiguedad, "POBLACION_CTE")
    return Resolucion(cve_entidad, cve_municipio, motivo, campo)
