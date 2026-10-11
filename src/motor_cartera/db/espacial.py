"""El tipo `geometry` de PostGIS, para los modelos y las migraciones.

PostGIS guarda cada geometria con su tipo y su SRID en la columna: `geometry(MULTIPOLYGON,4326)`
para un municipio y `geometry(POINT,4326)` para una cuenta o una base. SQLAlchemy no conoce el tipo,
asi que aqui se declara, sin traer otra dependencia: solo hace falta escribir la columna en el
esquema y reconocerla al reflejarlo, para que `alembic check` compare la base con los modelos.

Los valores no pasan por el ORM. Las geometrias se escriben y se leen con SQL de PostGIS, por
conjuntos (`ST_SetSRID(ST_MakePoint(longitud, latitud), 4326)`, `ST_Covers`, `ST_Distance` sobre
geography): un objeto de Python nunca calcula un punto en un poligono ni una distancia.
"""

from __future__ import annotations

from sqlalchemy.dialects.postgresql.base import ischema_names
from sqlalchemy.types import UserDefinedType

SRID_WGS84 = 4326
"""EPSG:4326, WGS 84 en grados: X es la longitud y Y la latitud. Es el sistema en que se guarda
todo: municipios, puntos de cuentas y bases."""


class Geometria(UserDefinedType):
    """Una columna `geometry(<tipo>,<srid>)` de PostGIS.

    Acepta el tipo y el SRID como los escribe PostgreSQL al reflejar la columna (`Point`, `"4326"`)
    y los normaliza, para que una columna reflejada sea igual a la del modelo."""

    cache_ok = True

    def __init__(self, tipo: str = "GEOMETRY", srid: int | str = SRID_WGS84) -> None:
        self.tipo = str(tipo).upper()
        self.srid = int(srid)

    def get_col_spec(self, **_: object) -> str:
        return f"geometry({self.tipo},{self.srid})"


# Al reflejar el esquema, `geometry(Point,4326)` se reconoce como Geometria("Point", "4326"), y no
# como un tipo desconocido que alembic dejaria de comparar.
ischema_names["geometry"] = Geometria
