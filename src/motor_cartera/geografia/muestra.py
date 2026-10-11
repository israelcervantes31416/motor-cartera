"""Una muestra de puntos de una referencia importada: con ellos el generador sintetico da
coordenadas a sus cuentas, sin hacer en Python ningun punto en poligono.

PostGIS genera los puntos dentro de cada municipio (ST_GeneratePoints, con semilla: la misma
version, la misma semilla y la misma version de PostGIS dan los mismos puntos), los redondea a 6
decimales (unos 11 cm) y descarta los que, redondeados, ya no cubre su municipio. Agrega puntos
FUERA de la referencia: en una caja al oeste de su extension, que PostGIS comprueba que no toca
ningun municipio.

Son puntos de la cartografia publica, no ubicaciones de nadie, y la muestra no se versiona en el
repositorio: es un insumo del generador, en datos/ o en un directorio temporal.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlmodel import Session

from motor_cartera.db.modelos import EstadoVersionGeografia, VersionGeografia
from motor_cartera.geografia.referencia import versiones_espaciales

VERSION_MUESTRA = "muestra-puntos/v1"
DECIMALES = 6
MARGEN_FUERA = 1.0
"""Grados entre la extension de la referencia y la caja de los puntos de fuera."""
LADO_FUERA = 2.0


class MuestraImposible(ValueError):
    """La referencia no permite la muestra: no esta PUBLICADA, o la caja de fuera toca un
    municipio."""


def muestrear(
    s: Session,
    version: VersionGeografia,
    *,
    por_municipio: int,
    fuera: int,
    semilla: int,
) -> dict[str, Any]:
    """La muestra de la version: hasta `por_municipio` puntos dentro de cada municipio y `fuera`
    puntos fuera de la referencia, con lo que la identifica."""
    if version.estado != EstadoVersionGeografia.PUBLICADA:
        raise MuestraImposible(
            f"La version {version.geografia_version_id} esta {version.estado}: solo se muestrea "
            "una referencia PUBLICADA."
        )
    if por_municipio < 1 or fuera < 1 or semilla < 1:
        raise ValueError("La muestra tiene al menos un punto por municipio y uno fuera, y semilla.")
    parametros = {
        "version": version.id,
        "n": por_municipio,
        "semilla": semilla,
        "fuera": fuera,
        "decimales": DECIMALES,
    }
    filas = s.execute(
        text(
            "SELECT cvegeo, x, y FROM (SELECT m.cvegeo, m.geometria, d.path[1] AS orden, "
            "round(CAST(ST_X(d.geom) AS numeric), :decimales) AS x, "
            "round(CAST(ST_Y(d.geom) AS numeric), :decimales) AS y FROM municipio_geografico m, "
            "ST_Dump(ST_GeneratePoints(m.geometria, :n, :semilla)) d "
            "WHERE m.version_geografia_id = :version) p "
            "WHERE ST_Covers(geometria, ST_SetSRID(ST_MakePoint(x, y), 4326)) "
            "ORDER BY cvegeo, orden"
        ),
        parametros,
    ).all()
    municipios: dict[str, list[list[float]]] = {}
    for cvegeo, x, y in filas:
        municipios.setdefault(cvegeo, []).append([float(x), float(y)])
    xmin, ymin = s.execute(
        text(
            "SELECT ST_XMin(e), ST_YMin(e) FROM (SELECT ST_Extent(geometria) AS e FROM "
            "municipio_geografico WHERE version_geografia_id = :version) x"
        ),
        parametros,
    ).one()
    caja = (
        max(-180.0, xmin - MARGEN_FUERA - LADO_FUERA),
        max(-90.0, ymin),
        max(-180.0 + LADO_FUERA, xmin - MARGEN_FUERA),
        min(90.0, ymin + LADO_FUERA),
    )
    toca = s.execute(
        text(
            "SELECT count(*) FROM municipio_geografico WHERE version_geografia_id = :version "
            "AND ST_Intersects(geometria, ST_MakeEnvelope(:x0, :y0, :x1, :y1, 4326))"
        ),
        {**parametros, "x0": caja[0], "y0": caja[1], "x1": caja[2], "y1": caja[3]},
    ).scalar_one()
    if toca:
        raise MuestraImposible(
            f"La caja de los puntos de fuera, {caja}, toca {toca} municipios de la referencia."
        )
    afuera = s.execute(
        text(
            "SELECT round(CAST(ST_X(d.geom) AS numeric), :decimales), "
            "round(CAST(ST_Y(d.geom) AS numeric), :decimales) FROM ST_Dump(ST_GeneratePoints("
            "ST_MakeEnvelope(:x0, :y0, :x1, :y1, 4326), :fuera, :semilla)) d ORDER BY d.path[1]"
        ),
        {**parametros, "x0": caja[0], "y0": caja[1], "x1": caja[2], "y1": caja[3]},
    ).all()
    return {
        "version": VERSION_MUESTRA,
        "geografia_version_id": str(version.geografia_version_id),
        "producto": version.producto,
        "edicion": version.edicion,
        "sha256_referencia": version.sha256,
        "capa": version.capa,
        "semilla": semilla,
        "por_municipio": por_municipio,
        "decimales": DECIMALES,
        "caja_fuera": list(caja),
        "versiones": versiones_espaciales(s),
        "municipios": municipios,
        "fuera": [[float(x), float(y)] for x, y in afuera],
    }


def escribir(muestra: dict[str, Any], destino: str | Path) -> Path:
    """La muestra en JSON, con las llaves en orden: el mismo contenido, los mismos bytes."""
    ruta = Path(destino)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(
        json.dumps(muestra, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n",
        encoding="ascii",
        newline="\n",
    )
    return ruta
