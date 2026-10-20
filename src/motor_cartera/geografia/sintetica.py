"""Referencias geograficas sinteticas: Shapefiles pequenos, deterministas y totalmente inventados,
para las pruebas, la prueba de humo y el CI, que no descargan nada del INEGI.

Sus geometrias son ficticias (rectangulos y poligonos dibujados a proposito) y su proveedor es
SINTETICO: no representan ningun limite real. Llevan claves del catalogo del INEGI (CVEGEO) para que
el municipio que resuelve la fuente con ESTADO_CTE y POBLACION_CTE se pueda comparar con el
espacial, como con la referencia oficial. No hay coordenadas de ninguna persona.

El mismo contenido produce los mismos bytes: los anillos van orientados como pide el formato
(exterior en el sentido del reloj, huecos al reves), la fecha del .dbf y la de cada miembro del zip
son fijas, y los municipios van en orden de CVEGEO.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import shapefile

from motor_cartera.fuentes.geografia import catalogo

WGS84_PRJ = (
    'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],'
    'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]'
)
"""El .prj de una capa en EPSG:4326, como lo escribe ESRI."""
LCC_MEXICO_PRJ = (
    'PROJCS["MEXICO_ITRF_2008_LCC",GEOGCS["GCS_ITRF_2008",DATUM["D_ITRF_2008",'
    'SPHEROID["GRS_1980",6378137.0,298.257222101]],PRIMEM["Greenwich",0.0],'
    'UNIT["Degree",0.0174532925199433]],PROJECTION["Lambert_Conformal_Conic"],'
    'PARAMETER["False_Easting",2500000.0],PARAMETER["False_Northing",0.0],'
    'PARAMETER["Central_Meridian",-102.0],PARAMETER["Standard_Parallel_1",17.5],'
    'PARAMETER["Standard_Parallel_2",29.5],PARAMETER["Latitude_Of_Origin",12.0],'
    'UNIT["Meter",1.0]]'
)
"""El .prj de EPSG:6372, el del Marco Geoestadistico."""

FECHA_EN_ZIP = (1980, 1, 1, 0, 0, 0)
FECHA_DBF = bytes((80, 1, 1))
"""1980-01-01 en la cabecera del .dbf (anio desde 1900, mes y dia): fija, para que el archivo no
dependa del dia en que se escribe."""

Anillo = list[tuple[float, float]]


@dataclass(frozen=True)
class MunicipioSintetico:
    """Un municipio inventado: su clave, su nombre y sus poligonos, cada uno con su exterior y sus
    huecos. Sin poligonos, su registro va sin geometria. `entidad` y `municipio` reemplazan las
    claves que salen de CVEGEO, para probar una capa con claves incoherentes."""

    cvegeo: str
    nombre: str
    poligonos: tuple[tuple[Anillo, ...], ...]
    entidad: str | None = None
    municipio: str | None = None

    @property
    def cve_ent(self) -> str:
        return self.cvegeo[:2] if self.entidad is None else self.entidad

    @property
    def cve_mun(self) -> str:
        return self.cvegeo[2:] if self.municipio is None else self.municipio


def rectangulo(
    cvegeo: str, nombre: str, xmin: float, ymin: float, xmax: float, ymax: float
) -> MunicipioSintetico:
    """Un municipio rectangular, en grados: de (xmin, ymin) a (xmax, ymax)."""
    anillo = [(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax), (xmin, ymin)]
    return MunicipioSintetico(cvegeo, nombre, ((anillo,),))


def _area_con_signo(anillo: Anillo) -> float:
    return (
        sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(anillo, anillo[1:], strict=False)) / 2
    )


def _orientar(anillo: Anillo, *, exterior: bool) -> Anillo:
    """El exterior en el sentido del reloj (area con signo negativa) y los huecos al reves, como
    pide el formato Shapefile."""
    horario = _area_con_signo(anillo) < 0
    return list(anillo) if horario == exterior else list(reversed(anillo))


def escribir_capa(
    destino: str | Path,
    municipios: list[MunicipioSintetico],
    *,
    capa: str = "municipios",
    prj: str = WGS84_PRJ,
    cpg: str | None = "UTF-8",
    codificacion: str = "utf-8",
    campos: tuple[str, ...] = ("CVEGEO", "CVE_ENT", "CVE_MUN", "NOMGEO"),
    orientar: bool = True,
) -> Path:
    """Escribe los municipios como una capa de poligonos en un zip: `<capa>.shp`, `.shx`, `.dbf`,
    `.prj` y, si se da, `.cpg`. Devuelve la ruta del zip. Con `orientar=False`, los anillos van tal
    como se dieron, para probar capas mal formadas."""
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    # Sin tipo estatico: las firmas de pyshp 3 no aceptan un BytesIO (su protocolo de escritura no
    # es posicional) ni los anillos como listas de pares.
    escritura: Any = shapefile.Writer
    escritor = escritura(
        shp=shp, shx=shx, dbf=dbf, shapeType=shapefile.POLYGON, encoding=codificacion
    )
    anchos = {"CVEGEO": 5, "CVE_ENT": 2, "CVE_MUN": 3, "NOMGEO": 80}
    for campo in campos:
        escritor.field(campo, "C", size=anchos.get(campo, 80))
    for municipio in sorted(municipios, key=lambda m: m.cvegeo):
        anillos: list[Anillo] = []
        for exterior, *huecos in municipio.poligonos:
            anillos.append(_orientar(exterior, exterior=True) if orientar else list(exterior))
            anillos.extend(_orientar(h, exterior=False) if orientar else list(h) for h in huecos)
        if anillos:
            escritor.poly(anillos)
        else:
            escritor.null()
        valores = {
            "CVEGEO": municipio.cvegeo,
            "CVE_ENT": municipio.cve_ent,
            "CVE_MUN": municipio.cve_mun,
            "NOMGEO": municipio.nombre,
        }
        escritor.record(*(valores.get(campo, "") for campo in campos))
    escritor.close()
    datos_dbf = bytearray(dbf.getvalue())
    datos_dbf[1:4] = FECHA_DBF
    ruta = Path(destino)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    miembros = {
        f"{capa}.shp": shp.getvalue(),
        f"{capa}.shx": shx.getvalue(),
        f"{capa}.dbf": bytes(datos_dbf),
        f"{capa}.prj": prj.encode("ascii"),
    }
    if cpg is not None:
        miembros[f"{capa}.cpg"] = cpg.encode("ascii")
    with zipfile.ZipFile(ruta, "w", compression=zipfile.ZIP_DEFLATED) as paquete:
        for nombre, contenido in miembros.items():
            info = zipfile.ZipInfo(nombre, date_time=FECHA_EN_ZIP)
            info.compress_type = zipfile.ZIP_DEFLATED
            paquete.writestr(info, contenido)
    return ruta


# --- la referencia sintetica de la prueba de humo y el benchmark ----------------------------------

ORIGEN = (-100.0, 18.0)
"""Donde empieza la cuadricula sintetica, en grados. No coincide con ningun limite real."""
CELDA = 0.1
"""El lado de cada municipio sintetico, en grados: unos 11 km."""


def cuadricula(cvegeos: list[str]) -> list[MunicipioSintetico]:
    """Un rectangulo por clave, en una cuadricula cuadrada desde ORIGEN, en orden de clave: los
    vecinos comparten su borde. El nombre es el oficial del catalogo, para que se lea, pero la
    geometria es inventada."""
    lado = max(1, int(len(cvegeos) ** 0.5 + 0.999999))
    nombres = {
        e.cve_entidad + m.cve_municipio: m.nombre
        for e in catalogo().entidades
        for m in e.municipios
    }
    municipios = []
    for i, cvegeo in enumerate(sorted(cvegeos)):
        fila, columna = divmod(i, lado)
        x = round(ORIGEN[0] + columna * CELDA, 6)
        y = round(ORIGEN[1] + fila * CELDA, 6)
        municipios.append(
            rectangulo(
                cvegeo,
                f"{nombres.get(cvegeo, cvegeo)} (sintetico)",
                x,
                y,
                round(x + CELDA, 6),
                round(y + CELDA, 6),
            )
        )
    return municipios


def referencia_de_humo(destino: str | Path, cvegeos: list[str]) -> Path:
    """La referencia sintetica de la prueba de humo: la cuadricula de esas claves, en EPSG:4326, en
    `destino` (un zip con la capa `municipios`)."""
    return escribir_capa(destino, cuadricula(cvegeos))
