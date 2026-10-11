"""Lo que comparten las pruebas de la geografia y de territorial/v2: una referencia sintetica de
tres municipios inventados, cortes con coordenadas elegidas una por una, y como abrir y resolver una
ejecucion de geografia/v1 y leer lo que publico.

Todo es sintetico. Los municipios son rectangulos dibujados a proposito, con claves reales del
catalogo del INEGI para que el municipio que resuelve la fuente (ESTADO_CTE y POBLACION_CTE) se
pueda comparar con el espacial; ninguna geometria ni ninguna coordenada es real, ni de una persona:

- A (21114, el de PUEBLA, PUEBLA): de (-98.30, 18.95) a (-98.10, 19.15);
- B (21015, el de AMOZOC, PUEBLA): de (-98.10, 18.95) a (-97.90, 19.15), al este de A, con el que
  comparte el borde x = -98.10;
- C (31050, el de MERIDA, YUCATAN): de (-89.75, 20.85) a (-89.45, 21.15), al este del meridiano 90:
  una latitud de -89.6 todavia es una latitud valida, asi que unas coordenadas intercambiadas de
  ahi pasan el contrato de cartera/v2.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd
from historia_escenarios import SEMILLA, Cuenta, estado
from motor_pagos_escenarios import historiar_todo
from sqlalchemy import text
from sqlmodel import select

from motor_cartera.config import config
from motor_cartera.db.modelos import (
    Corrida,
    CorteCanonico,
    CuentaCanonica,
    EjecucionGeografia,
    MunicipioGeografico,
    ResultadoGeografiaCuenta,
    VersionGeografia,
)
from motor_cartera.db.sesion import sesion
from motor_cartera.generador.oficial import escribir_cartera, tabla_cartera
from motor_cartera.geografia import ejecuciones as motor
from motor_cartera.geografia import referencia, reglas
from motor_cartera.geografia.referencia import Metadatos
from motor_cartera.geografia.sintetica import MunicipioSintetico, escribir_capa, rectangulo
from motor_cartera.ingesta.corridas import ingerir_archivo

PUEBLA, AMOZOC, MERIDA, ATLIXCO = "21114", "21015", "31050", "21019"
A = rectangulo(PUEBLA, "Municipio A (sintetico)", -98.30, 18.95, -98.10, 19.15)
B = rectangulo(AMOZOC, "Municipio B (sintetico)", -98.10, 18.95, -97.90, 19.15)
C = rectangulo(MERIDA, "Municipio C (sintetico)", -89.75, 20.85, -89.45, 21.15)
MUNICIPIOS = (A, B, C)

FUENTE = {
    PUEBLA: ("PUEBLA", "PUEBLA"),
    AMOZOC: ("PUEBLA", "AMOZOC"),
    MERIDA: ("YUCATAN", "MERIDA"),
    ATLIXCO: ("PUEBLA", "ATLIXCO"),
}
"""ESTADO_CTE y POBLACION_CTE con que la fuente resuelve cada clave. ATLIXCO no esta en la
referencia sintetica."""


@dataclass(frozen=True)
class CuentaGeo:
    """Una cuenta de un corte: su numero de cliente, el municipio que dice la fuente y sus
    coordenadas observadas, como texto (None si la celda va vacia)."""

    numero: int
    fuente: str = PUEBLA
    latitud: str | None = None
    longitud: str | None = None


def cliente(numero: int) -> str:
    return f"CU{numero:010d}"


def importar_referencia(
    destino: Path,
    municipios: tuple[MunicipioSintetico, ...] = MUNICIPIOS,
    *,
    edicion: str = "golden-v1",
    **opciones,
) -> VersionGeografia:
    """La referencia sintetica importada como cualquier otra, con sus metadatos de prueba."""
    ruta = escribir_capa(destino / f"referencia_{edicion}.zip", list(municipios), **opciones)
    return referencia.importar(
        ruta,
        metadatos=Metadatos(
            "SINTETICO",
            "Referencia sintetica de pruebas",
            edicion,
            adicionales={"aviso": "Geometrias inventadas: no son limites reales."},
        ),
    )


def tabla(cuentas: list[CuentaGeo], corte: date) -> pd.DataFrame:
    """Las 93 columnas de cartera/v2 de esas cuentas, con su municipio de fuente y sus
    coordenadas."""
    base = tabla_cartera(
        estado([Cuenta(c.numero) for c in cuentas]), semilla=SEMILLA, fecha_corte=corte
    ).to_pandas()
    base = base.astype(object)
    for i, c in enumerate(cuentas):
        entidad, poblacion = FUENTE[c.fuente]
        base.at[i, "ESTADO_CTE"] = entidad
        base.at[i, "POBLACION_CTE"] = poblacion
        base.at[i, "LATITUD"] = c.latitud
        base.at[i, "LONGITUD"] = c.longitud
    return base


def ingerir_corte_geo(
    destino: Path,
    corte: date,
    cuentas: list[CuentaGeo],
    *,
    tolerancia: float | None = None,
    nombre: str = "cartera",
) -> Corrida:
    """Un corte de cartera/v2 con esas coordenadas, ingerido y materializado en la historia."""
    ruta = destino / f"{nombre}_{corte.isoformat()}.csv"
    escribir_cartera(
        estado([]), ruta, semilla=SEMILLA, fecha_corte=corte, tabla=tabla(cuentas, corte)
    )
    corrida = ingerir_archivo(ruta, contrato="cartera/v2", fecha_corte=corte, tolerancia=tolerancia)
    assert corrida.estado == "EXITOSA", corrida.detalle
    historiar_todo()
    return corrida


def corte_de(fecha: date) -> CorteCanonico:
    with sesion() as s:
        return s.exec(
            select(CorteCanonico).where(
                CorteCanonico.despacho_id == config.despacho_id,
                CorteCanonico.cartera_id == config.cartera_id,
                CorteCanonico.fecha_corte == fecha,
            )
        ).one()


def abrir_geo(fecha: date, version: VersionGeografia, **opciones) -> int:
    """Abre la geografia del corte de esa fecha con esa version, y devuelve el id interno."""
    with sesion() as s:
        ejecucion = motor.abrir(
            s,
            corte_de(fecha).corte_id,
            version.geografia_version_id,
            max_intentos=config.worker_max_intentos,
            **opciones,
        )
        s.commit()
        return ejecucion.id


def ejecucion(ejecucion_id: int) -> EjecucionGeografia:
    with sesion() as s:
        return s.get_one(EjecucionGeografia, ejecucion_id)


def geolocalizar(fecha: date, version: VersionGeografia) -> EjecucionGeografia:
    """Abre y resuelve la geografia del corte, como lo haria su trabajo, y la devuelve terminada."""
    ejecucion_id = abrir_geo(fecha, version)
    motor.ejecutar_geografia(ejecucion_id)
    return ejecucion(ejecucion_id)


def resultados(ejecucion_id: int) -> dict[int, dict]:
    """Lo que publico la ejecucion, por numero de cliente: estado, municipio, comparacion y
    motivos."""
    with sesion() as s:
        filas = s.exec(
            select(
                ResultadoGeografiaCuenta, CuentaCanonica.cliente_unico, MunicipioGeografico.cvegeo
            )
            .join(CuentaCanonica, CuentaCanonica.id == ResultadoGeografiaCuenta.cuenta_canonica_id)
            .outerjoin(
                MunicipioGeografico,
                MunicipioGeografico.id == ResultadoGeografiaCuenta.municipio_geografico_id,
            )
            .where(ResultadoGeografiaCuenta.ejecucion_geografia_id == ejecucion_id)
        ).all()
    return {
        int(cliente_unico[2:]): {
            "estado": r.estado_geografia,
            "cvegeo": cvegeo,
            "fuente": r.municipio_fuente_resuelto,
            "coincide": r.coincide_municipio,
            "latitud": r.latitud_observada,
            "longitud": r.longitud_observada,
            "con_punto": r.punto is not None,
            "motivos": r.motivos,
            "source_row": r.source_row,
        }
        for r, cliente_unico, cvegeo in filas
    }


def espejo(ejecucion_id: int) -> dict[int, tuple]:
    """Lo que `reglas.clasificar` concluye de cada cuenta que publico la ejecucion, con lo que
    PostGIS dice de su punto consultado aparte, punto por punto: lo que el SQL por conjuntos tenia
    que publicar. Por numero de cliente: estado, municipio, comparacion y motivos."""
    with sesion() as s:
        version_id = s.get_one(EjecucionGeografia, ejecucion_id).version_geografia_id
        en_referencia = set(
            s.exec(
                select(MunicipioGeografico.cvegeo).where(
                    MunicipioGeografico.version_geografia_id == version_id
                )
            ).all()
        )

        def cubren(x: float, y: float) -> list[str]:
            return list(
                s.execute(
                    text(
                        "SELECT cvegeo FROM municipio_geografico WHERE version_geografia_id = :v "
                        "AND ST_Covers(geometria, ST_SetSRID(ST_MakePoint(:x, :y), 4326)) "
                        "ORDER BY cvegeo"
                    ),
                    {"v": version_id, "x": x, "y": y},
                ).scalars()
            )

        esperado = {}
        for numero, r in resultados(ejecucion_id).items():
            latitud, longitud = r["latitud"], r["longitud"]
            clase = reglas.clasificar(
                latitud,
                longitud,
                municipios=[]
                if reglas.coordenadas(latitud, longitud)
                else cubren(longitud, latitud),
                cvegeo_fuente=r["fuente"],
                fuente_en_referencia=r["fuente"] in en_referencia,
                invertido=(
                    cubren(latitud, longitud)
                    if reglas.se_puede_invertir(latitud, longitud)
                    else None
                ),
            )
            esperado[numero] = (clase.estado.value, clase.cvegeo, clase.coincide, clase.motivos)
    return esperado


def publicado(ejecucion_id: int) -> dict[int, tuple]:
    """Lo que publico la ejecucion, en la forma de `espejo`."""
    return {
        numero: (r["estado"], r["cvegeo"], r["coincide"], r["motivos"])
        for numero, r in resultados(ejecucion_id).items()
    }


def inyectar(monkeypatch, valores: dict[int, tuple[str, str]]) -> Callable[..., Iterator[bytes]]:
    """Sustituye, en lo que geografia/v1 copia del Parquet, la latitud y la longitud de unas cuentas
    por texto que cartera/v2 nunca dejaria pasar (95, -200, NaN, infinito): simula un dataset que
    las trajera, para probar que geografia/v1 no depende de que el contrato ya las haya
    rechazado."""
    original = motor._bloques

    def bloques(*argumentos, **opciones) -> Iterator[bytes]:
        for bloque in original(*argumentos, **opciones):
            texto = bloque.decode("utf-8")
            for numero, (latitud, longitud) in valores.items():
                texto = re.sub(
                    rf'^(\d+,(?:"[^"]*")?,"{cliente(numero)}"),[^,\n]*,[^,\n]*$',
                    rf"\g<1>,{latitud},{longitud}",
                    texto,
                    flags=re.MULTILINE,
                )
            yield texto.encode("utf-8")

    monkeypatch.setattr(motor, "_bloques", bloques)
    return bloques
