"""geografia/v1 como nucleo puro: su vocabulario, la regla de una coordenada observada y la firma de
una ejecucion.

Separa el HECHO GEOGRAFICO de la POLITICA TERRITORIAL: aqui solo se responde que ubicacion se
observo, si sirve, en que municipio de la referencia cae y si coincide con el municipio que resolvio
la fuente. Que base atiende esa cuenta es de territorial/v2, que reutiliza esta misma geografia.

La regla, cuenta por cuenta, sin corregir nada:

1. Sin latitud ni longitud: SIN_COORDENADAS. Con una sola: COORDENADAS_INCOMPLETAS; la otra no se
   inventa.
2. Con las dos, son COORDENADAS_INVALIDAS si alguna no es finita (NaN o infinito), si la latitud
   sale de [-90, 90] o la longitud de [-180, 180], o si son exactamente (0, 0). cartera/v2 ya
   rechaza una latitud o una longitud fuera de rango o que no sea un numero; geografia/v1 lo vuelve
   a revisar, porque no depende de que un contrato lo haya hecho. (0, 0) si pasa el contrato: es un
   punto del Golfo de Guinea, donde no hay ninguna cuenta de una cartera mexicana, y es lo que deja
   un sistema que no capturo la coordenada.
3. Con coordenadas utilizables, el punto es ST_MakePoint(longitud, latitud) en EPSG:4326: X es la
   longitud y Y la latitud. Se busca en los municipios de la referencia con ST_Covers, que incluye
   el borde: un punto sobre el limite comun de dos municipios cae en los dos.
   - en ninguno: FUERA_DE_REFERENCIA;
   - en mas de uno: MUNICIPIO_AMBIGUO, sin elegir ninguno (ni el primero, ni el de menor CVEGEO, ni
     el mas grande);
   - en exactamente uno, se compara con el municipio de la fuente: si es otro,
     DISCREPANCIA_MUNICIPIO; si es el mismo, o la fuente no resolvio ninguno, VALIDA.
4. Si las coordenadas no dieron un punto en la referencia (invalidas o fuera), se prueba el punto
   con la latitud y la longitud intercambiadas. Si ese si cae en un municipio, se agrega el motivo
   POSIBLE_ORDEN_INVERTIDO, con ese municipio. Nunca se corrige: la fuente se queda como vino.

Solo una VALIDA es geografia confiable para territorial/v2: coordenadas utilizables, un solo
municipio y ninguna discrepancia conocida con la fuente. Lo demas no se envia a campo con una
ubicacion dudosa.

El SQL de `geografia.ejecuciones` aplica esta misma regla por conjuntos; `clasificar` es su espejo
en Python, con el que las pruebas comparan cada resultado.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

VERSION_MOTOR = "geografia/v1"
"""Cambia si cambia la regla: el vocabulario, los rangos, el predicado espacial o la comparacion."""

LATITUD_MINIMA, LATITUD_MAXIMA = -90.0, 90.0
LONGITUD_MINIMA, LONGITUD_MAXIMA = -180.0, 180.0

PREDICADO_ESPACIAL = "ST_Covers"
"""El punto cae en un municipio si el municipio lo cubre: su interior y su borde. Con ST_Contains un
punto sobre un limite no caeria en ninguno y pareceria fuera de la referencia."""


class EstadoGeografia(StrEnum):
    """El vocabulario de geografia/v1: uno por cuenta, y solo uno."""

    VALIDA = "VALIDA"
    """Coordenadas utilizables, un solo municipio, sin discrepancia conocida con la fuente."""
    SIN_COORDENADAS = "SIN_COORDENADAS"
    COORDENADAS_INCOMPLETAS = "COORDENADAS_INCOMPLETAS"
    COORDENADAS_INVALIDAS = "COORDENADAS_INVALIDAS"
    FUERA_DE_REFERENCIA = "FUERA_DE_REFERENCIA"
    MUNICIPIO_AMBIGUO = "MUNICIPIO_AMBIGUO"
    DISCREPANCIA_MUNICIPIO = "DISCREPANCIA_MUNICIPIO"


CONFIABLES = frozenset({EstadoGeografia.VALIDA})
"""Lo unico que territorial/v2 puede asignar automaticamente."""

CONTEO_DE_ESTADO = {
    EstadoGeografia.VALIDA: "validas",
    EstadoGeografia.SIN_COORDENADAS: "sin_coordenadas",
    EstadoGeografia.COORDENADAS_INCOMPLETAS: "incompletas",
    EstadoGeografia.COORDENADAS_INVALIDAS: "invalidas",
    EstadoGeografia.FUERA_DE_REFERENCIA: "fuera_cobertura",
    EstadoGeografia.MUNICIPIO_AMBIGUO: "ambiguas",
    EstadoGeografia.DISCREPANCIA_MUNICIPIO: "discrepantes",
}
"""La columna de EjecucionGeografia que cuenta cada estado."""


class Motivo(StrEnum):
    """Por que una cuenta quedo en su estado. Cada motivo es un objeto {"codigo": ...} con lo que lo
    explica; ninguno lleva coordenadas."""

    SIN_LATITUD = "SIN_LATITUD"
    SIN_LONGITUD = "SIN_LONGITUD"
    LATITUD_NO_FINITA = "LATITUD_NO_FINITA"
    LONGITUD_NO_FINITA = "LONGITUD_NO_FINITA"
    LATITUD_FUERA_DE_RANGO = "LATITUD_FUERA_DE_RANGO"
    LONGITUD_FUERA_DE_RANGO = "LONGITUD_FUERA_DE_RANGO"
    PUNTO_0_0 = "PUNTO_0_0"
    SIN_MUNICIPIO_EN_REFERENCIA = "SIN_MUNICIPIO_EN_REFERENCIA"
    VARIOS_MUNICIPIOS = "VARIOS_MUNICIPIOS"
    """Con `cvegeo`: los municipios que cubren el punto, en orden."""
    MUNICIPIO_UNICO = "MUNICIPIO_UNICO"
    """Con `cvegeo`: el unico municipio que cubre el punto."""
    COINCIDE_CON_FUENTE = "COINCIDE_CON_FUENTE"
    DISCREPA_DE_FUENTE = "DISCREPA_DE_FUENTE"
    """Con `cvegeo_fuente`, `cvegeo_espacial` y `fuente_en_referencia` (si la clave de la fuente
    existe en esa version de la referencia)."""
    FUENTE_SIN_MUNICIPIO = "FUENTE_SIN_MUNICIPIO"
    """La fuente no resolvio un municipio: no hay con que comparar."""
    POSIBLE_ORDEN_INVERTIDO = "POSIBLE_ORDEN_INVERTIDO"
    """Con `cvegeo`: los municipios donde caeria el punto con latitud y longitud intercambiadas, en
    orden. No se corrige."""


class Comparacion(StrEnum):
    """El municipio espacial contra el que resolvio la fuente con ESTADO_CTE y POBLACION_CTE."""

    COINCIDE = "COINCIDE"
    DISCREPA = "DISCREPA"
    NO_COMPARABLE = "NO_COMPARABLE"


def comparacion(coincide: bool | None) -> Comparacion:
    """La comparacion que guarda `coincide_municipio`: True, False o NULL."""
    if coincide is None:
        return Comparacion.NO_COMPARABLE
    return Comparacion.COINCIDE if coincide else Comparacion.DISCREPA


@dataclass(frozen=True)
class Clasificacion:
    """Lo que geografia/v1 concluye de una cuenta."""

    estado: EstadoGeografia
    cvegeo: str | None
    """El municipio espacial, si el punto cae en exactamente uno."""
    coincide: bool | None
    motivos: list[dict]
    utilizable: bool
    """Si las coordenadas dieron un punto (aunque caiga fuera de la referencia)."""


def coordenadas(latitud: float | None, longitud: float | None) -> list[dict]:
    """Los motivos que descartan una coordenada antes de buscarla en la referencia: vacio si es
    utilizable."""
    if latitud is None and longitud is None:
        return [{"codigo": Motivo.SIN_LATITUD.value}, {"codigo": Motivo.SIN_LONGITUD.value}]
    if latitud is None:
        return [{"codigo": Motivo.SIN_LATITUD.value}]
    if longitud is None:
        return [{"codigo": Motivo.SIN_LONGITUD.value}]
    motivos = []
    if not math.isfinite(latitud):
        motivos.append({"codigo": Motivo.LATITUD_NO_FINITA.value})
    elif not LATITUD_MINIMA <= latitud <= LATITUD_MAXIMA:
        motivos.append({"codigo": Motivo.LATITUD_FUERA_DE_RANGO.value})
    if not math.isfinite(longitud):
        motivos.append({"codigo": Motivo.LONGITUD_NO_FINITA.value})
    elif not LONGITUD_MINIMA <= longitud <= LONGITUD_MAXIMA:
        motivos.append({"codigo": Motivo.LONGITUD_FUERA_DE_RANGO.value})
    if not motivos and latitud == 0 and longitud == 0:
        motivos.append({"codigo": Motivo.PUNTO_0_0.value})
    return motivos


def clasificar(
    latitud: float | None,
    longitud: float | None,
    *,
    municipios: list[str],
    cvegeo_fuente: str | None,
    fuente_en_referencia: bool,
    invertido: list[str] | None = None,
) -> Clasificacion:
    """La regla completa de una cuenta, con lo que el SQL obtiene de PostGIS: `municipios`, los
    CVEGEO que cubren el punto, e `invertido`, los que cubririan el punto con las coordenadas
    intercambiadas, si alguno lo cubre."""
    descartes = coordenadas(latitud, longitud)
    motivos = list(descartes)
    cvegeo: str | None = None
    coincide: bool | None = None
    if latitud is None and longitud is None:
        estado = EstadoGeografia.SIN_COORDENADAS
    elif latitud is None or longitud is None:
        estado = EstadoGeografia.COORDENADAS_INCOMPLETAS
    elif descartes:
        estado = EstadoGeografia.COORDENADAS_INVALIDAS
    elif not municipios:
        estado = EstadoGeografia.FUERA_DE_REFERENCIA
        motivos.append({"codigo": Motivo.SIN_MUNICIPIO_EN_REFERENCIA.value})
    elif len(municipios) > 1:
        estado = EstadoGeografia.MUNICIPIO_AMBIGUO
        motivos.append({"codigo": Motivo.VARIOS_MUNICIPIOS.value, "cvegeo": sorted(municipios)})
    else:
        cvegeo = municipios[0]
        motivos.append({"codigo": Motivo.MUNICIPIO_UNICO.value, "cvegeo": cvegeo})
        if cvegeo_fuente is None:
            estado = EstadoGeografia.VALIDA
            motivos.append({"codigo": Motivo.FUENTE_SIN_MUNICIPIO.value})
        elif cvegeo_fuente == cvegeo:
            estado, coincide = EstadoGeografia.VALIDA, True
            motivos.append({"codigo": Motivo.COINCIDE_CON_FUENTE.value, "cvegeo": cvegeo})
        else:
            estado, coincide = EstadoGeografia.DISCREPANCIA_MUNICIPIO, False
            motivos.append(
                {
                    "codigo": Motivo.DISCREPA_DE_FUENTE.value,
                    "cvegeo_fuente": cvegeo_fuente,
                    "cvegeo_espacial": cvegeo,
                    "fuente_en_referencia": fuente_en_referencia,
                }
            )
    if invertido and estado in (
        EstadoGeografia.COORDENADAS_INVALIDAS,
        EstadoGeografia.FUERA_DE_REFERENCIA,
    ):
        motivos.append(
            {"codigo": Motivo.POSIBLE_ORDEN_INVERTIDO.value, "cvegeo": sorted(invertido)}
        )
    utilizable = latitud is not None and longitud is not None and not descartes
    return Clasificacion(estado, cvegeo, coincide, motivos, utilizable)


def se_puede_invertir(latitud: float | None, longitud: float | None) -> bool:
    """Si el punto con las coordenadas intercambiadas (X = latitud, Y = longitud) es un punto
    valido, y por tanto vale la pena buscarlo en la referencia."""
    if latitud is None or longitud is None:
        return False
    if not (math.isfinite(latitud) and math.isfinite(longitud)):
        return False
    return (
        LONGITUD_MINIMA <= latitud <= LONGITUD_MAXIMA
        and LATITUD_MINIMA <= longitud <= LATITUD_MAXIMA
        and not (latitud == 0 and longitud == 0)
    )


def firma_inputs(
    *,
    geografia_version_id: UUID,
    sha256_referencia: str,
    capa: str,
    corte_id: UUID,
    firma_corte: str,
    dataset_id: UUID,
    firma_dataset: str,
    version_motor: str = VERSION_MOTOR,
) -> str:
    """El SHA-256 de las entradas logicas de una ejecucion: el motor, la version de la referencia
    con el SHA-256 de su archivo y su capa, el corte con su firma de contenido y su dataset
    conformado con la suya. Las mismas entradas son la misma geografia."""
    lineas = [
        version_motor,
        str(geografia_version_id),
        sha256_referencia,
        capa,
        str(corte_id),
        firma_corte,
        str(dataset_id),
        firma_dataset,
    ]
    return hashlib.sha256("\n".join(lineas).encode("utf-8")).hexdigest()
