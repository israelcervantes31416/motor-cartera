"""Lo que comparten las pruebas de territorial/v2: la referencia sintetica de la geografia con un
cuarto municipio, bases operativas inventadas, como abrir y resolver una ejecucion y leer lo que
publico, y dos espejos independientes del SQL: la politica en Python y la distancia geodesica con la
formula de Vincenty.

Todo es sintetico: los municipios son los rectangulos de `geografia_escenarios`, y las bases, puntos
elegidos a mano dentro de ellos. Ninguna ubicacion es real.

- D (21019, el de ATLIXCO, PUEBLA): de (-98.70, 18.75) a (-98.50, 18.95), sin bordes comunes.
"""

from __future__ import annotations

import math
from datetime import date
from decimal import Decimal
from pathlib import Path

from geografia_escenarios import ATLIXCO, MUNICIPIOS, corte_de, importar_referencia, resultados
from sqlalchemy import text
from sqlmodel import select

from motor_cartera.bases import configuracion
from motor_cartera.config import config
from motor_cartera.db.modelos import (
    AsignacionTerritorialV2,
    BaseOperativa,
    CuentaCanonica,
    EjecucionGeografia,
    EjecucionTerritorialV2,
    MunicipioGeografico,
    VersionBasesOperativas,
    VersionGeografia,
)
from motor_cartera.db.sesion import sesion
from motor_cartera.geografia.sintetica import rectangulo
from motor_cartera.territorial_v2 import ejecuciones as motor
from motor_cartera.territorial_v2 import politica

D = rectangulo(ATLIXCO, "Municipio D (sintetico)", -98.70, 18.75, -98.50, 18.95)
MUNICIPIOS_TV2 = (*MUNICIPIOS, D)


def referencia_tv2(destino: Path) -> VersionGeografia:
    """La referencia de la geografia con los cuatro municipios."""
    return importar_referencia(destino, MUNICIPIOS_TV2, edicion="territorial")


def base(
    codigo: str, latitud: float, longitud: float, municipios: list[str], *, activa: bool = True
) -> dict:
    return {
        "codigo": codigo,
        "nombre": f"Base {codigo} (sintetica)",
        "lat": latitud,
        "lon": longitud,
        "activa": activa,
        "municipios": municipios,
    }


def publicar_bases(*bases: dict, nombre: str = "Bases sinteticas") -> VersionBasesOperativas:
    """Una version de bases operativas publicada con esas bases."""
    with sesion() as s:
        publicada = configuracion.publicar(
            s, configuracion.leer({"nombre": nombre, "bases": list(bases)})
        )
        s.commit()
        s.refresh(publicada.version)
        return publicada.version


def abrir_tv2(
    fecha: date, geografia: EjecucionGeografia, bases: VersionBasesOperativas, **opciones
) -> int:
    """Abre territorial/v2 del corte de esa fecha, y devuelve el id interno."""
    with sesion() as s:
        ejecucion = motor.abrir(
            s,
            corte_de(fecha).corte_id,
            geografia.geografia_run_id,
            bases.bases_version_id,
            max_intentos=config.worker_max_intentos,
            **opciones,
        )
        s.commit()
        return ejecucion.id


def ejecucion_tv2(ejecucion_id: int) -> EjecucionTerritorialV2:
    with sesion() as s:
        return s.get_one(EjecucionTerritorialV2, ejecucion_id)


def asignar_tv2(
    fecha: date, geografia: EjecucionGeografia, bases: VersionBasesOperativas
) -> EjecucionTerritorialV2:
    """Abre y resuelve territorial/v2, como lo haria su trabajo, y la devuelve terminada."""
    ejecucion_id = abrir_tv2(fecha, geografia, bases)
    motor.ejecutar_territorial_v2(ejecucion_id)
    return ejecucion_tv2(ejecucion_id)


def asignaciones(ejecucion_id: int) -> dict[int, dict]:
    """Lo que publico la ejecucion, por numero de cliente."""
    with sesion() as s:
        filas = s.exec(
            select(
                AsignacionTerritorialV2,
                CuentaCanonica.cliente_unico,
                BaseOperativa.codigo,
                MunicipioGeografico.cvegeo,
            )
            .join(CuentaCanonica, CuentaCanonica.id == AsignacionTerritorialV2.cuenta_canonica_id)
            .outerjoin(BaseOperativa, BaseOperativa.id == AsignacionTerritorialV2.base_operativa_id)
            .outerjoin(
                MunicipioGeografico,
                MunicipioGeografico.id == AsignacionTerritorialV2.municipio_geografico_id,
            )
            .where(AsignacionTerritorialV2.ejecucion_territorial_v2_id == ejecucion_id)
        ).all()
    return {
        int(cliente_unico[2:]): {
            "estado": a.estado_asignacion,
            "base": codigo,
            "distancia": a.distancia_base_m,
            "cvegeo": cvegeo,
            "motivos": a.motivos,
        }
        for a, cliente_unico, codigo, cvegeo in filas
    }


def publicadas(ejecucion_id: int) -> dict[int, tuple]:
    """Lo que publico la ejecucion, en la forma de `espejo`: estado, base, distancia y motivos."""
    return {
        numero: (a["estado"], a["base"], a["distancia"], a["motivos"])
        for numero, a in asignaciones(ejecucion_id).items()
    }


def espejo(ejecucion_id: int) -> dict[int, tuple]:
    """Lo que `politica.asignar` concluye de cada cuenta, con su geografia publicada y, aparte y
    cuenta por cuenta, las bases de la version que cubren su municipio y la distancia geodesica de
    su punto a cada una: lo que el SQL por conjuntos tenia que publicar."""
    with sesion() as s:
        e = s.get_one(EjecucionTerritorialV2, ejecucion_id)
        geografia = resultados(e.ejecucion_geografia_id)
        cliente_de = dict(
            s.execute(
                text(
                    "SELECT c.cliente_unico, r.punto FROM resultado_geografia_cuenta r "
                    "JOIN cuenta_canonica c ON c.id = r.cuenta_canonica_id "
                    "WHERE r.ejecucion_geografia_id = :g"
                ),
                {"g": e.ejecucion_geografia_id},
            ).all()
        )
        esperado = {}
        for numero, g in geografia.items():
            punto = cliente_de[f"CU{numero:010d}"]
            bases = s.execute(
                text(
                    "SELECT b.codigo, b.activa, round(CAST(ST_Distance(CAST(:punto AS geography), "
                    "CAST(b.punto AS geography)) AS numeric), 2) FROM base_operativa b "
                    "JOIN cobertura_base_municipio k ON k.base_operativa_id = b.id "
                    "WHERE b.version_bases_operativas_id = :v AND k.cvegeo = :cvegeo"
                ),
                {"punto": punto, "v": e.version_bases_operativas_id, "cvegeo": g["cvegeo"]},
            ).all()
            a = politica.asignar(
                g["estado"],
                g["cvegeo"],
                [politica.Candidata(codigo, metros) for codigo, activa, metros in bases if activa],
                inactivas=[codigo for codigo, activa, _ in bases if not activa],
            )
            esperado[numero] = (a.estado.value, a.base, a.distancia_m, a.motivos)
    return esperado


# --- la distancia geodesica, aparte -------------------------------------------------------------

_A = 6378137.0
_F = 1 / 298.257223563
_B = _A * (1 - _F)


def vincenty(latitud_1: float, longitud_1: float, latitud_2: float, longitud_2: float) -> float:
    """La distancia geodesica en metros entre dos puntos del elipsoide WGS 84, con la formula
    inversa de Vincenty (1975): un calculo independiente del de PostGIS, exacto a menos de un
    milimetro a estas distancias."""
    if (latitud_1, longitud_1) == (latitud_2, longitud_2):
        return 0.0
    u1 = math.atan((1 - _F) * math.tan(math.radians(latitud_1)))
    u2 = math.atan((1 - _F) * math.tan(math.radians(latitud_2)))
    diferencia = math.radians(longitud_2 - longitud_1)
    lam = diferencia
    for _ in range(200):
        seno_lam, coseno_lam = math.sin(lam), math.cos(lam)
        seno_sigma = math.hypot(
            math.cos(u2) * seno_lam,
            math.cos(u1) * math.sin(u2) - math.sin(u1) * math.cos(u2) * coseno_lam,
        )
        coseno_sigma = math.sin(u1) * math.sin(u2) + math.cos(u1) * math.cos(u2) * coseno_lam
        sigma = math.atan2(seno_sigma, coseno_sigma)
        seno_alfa = math.cos(u1) * math.cos(u2) * seno_lam / seno_sigma
        coseno2_alfa = 1 - seno_alfa**2
        coseno_2sm = coseno_sigma - 2 * math.sin(u1) * math.sin(u2) / coseno2_alfa
        c = _F / 16 * coseno2_alfa * (4 + _F * (4 - 3 * coseno2_alfa))
        anterior = lam
        lam = diferencia + (1 - c) * _F * seno_alfa * (
            sigma + c * seno_sigma * (coseno_2sm + c * coseno_sigma * (2 * coseno_2sm**2 - 1))
        )
        if abs(lam - anterior) < 1e-13:
            break
    u2_ = coseno2_alfa * (_A**2 - _B**2) / _B**2
    a = 1 + u2_ / 16384 * (4096 + u2_ * (-768 + u2_ * (320 - 175 * u2_)))
    b = u2_ / 1024 * (256 + u2_ * (-128 + u2_ * (74 - 47 * u2_)))
    delta_sigma = (
        b
        * seno_sigma
        * (
            coseno_2sm
            + b
            / 4
            * (
                coseno_sigma * (2 * coseno_2sm**2 - 1)
                - b / 6 * coseno_2sm * (4 * seno_sigma**2 - 3) * (4 * coseno_2sm**2 - 3)
            )
        )
    )
    return _B * a * (sigma - delta_sigma)


def metros(valor: float) -> Decimal:
    return Decimal(str(valor)).quantize(Decimal("0.01"))
