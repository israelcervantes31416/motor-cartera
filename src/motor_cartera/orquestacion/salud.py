"""La salud del worker, servida en su puerto interno junto a sus metricas.

- `GET /health/live`: el proceso responde y su ciclo da senales de vida. El ciclo late en cada
  vuelta (cada `worker_poll_segundos` con la cola vacia) y, mientras ejecuta un trabajo largo, late
  el hilo que renueva su lease. Si ninguno late en `umbral_segundos`, el proceso esta colgado: 503,
  y un orquestador lo puede reiniciar. Una base caida no lo vuelve no vivo: el ciclo sigue latiendo
  mientras espera.
- `GET /health/ready`: la cola se puede usar (la base contesta, la tabla de trabajos existe) y el
  esquema es el esperado. Si no, 503: vivo, pero sin poder trabajar.

La salud de OSRM se informa, pero no decide: sin OSRM el worker sigue con los trabajos que no son
RUTEO_V2.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from motor_cartera.db.esquema import CABEZA_ESPERADA, revision_actual
from motor_cartera.db.sesion import crear_motor
from motor_cartera.observabilidad import metricas

if TYPE_CHECKING:
    from motor_cartera.ruteo_v2.ejecuciones import SaludRuteo


@dataclass
class SaludDelWorker:
    umbral_segundos: float = 60.0
    ruteo: SaludRuteo | None = None
    """La salud del proveedor de ruteo del worker (SaludRuteo), si tiene uno."""
    _latido: float = field(default_factory=time.monotonic)
    trabajo_en_curso: bool = False

    def latir(self) -> None:
        self._latido = time.monotonic()
        metricas.WORKER_LATIDO.set(time.time())

    def vivo(self) -> tuple[int, dict]:
        hace = time.monotonic() - self._latido
        vivo = hace < self.umbral_segundos
        return (200 if vivo else 503), {
            "estado": "vivo" if vivo else "latido_vencido",
            "latido_hace_segundos": round(hace, 1),
            "trabajo_en_curso": self.trabajo_en_curso,
        }

    def listo(self) -> tuple[int, dict]:
        try:
            with crear_motor().connect() as conexion, conexion.begin():
                conexion.execute(text("SET LOCAL statement_timeout = 2000"))
                conexion.execute(text("SELECT 1 FROM trabajo_orquestacion LIMIT 0"))
                revision = revision_actual(conexion)
        except SQLAlchemyError:
            return 503, {"estado": "no_listo", "base_de_datos": "sin_conexion", "cola": "no_usable"}
        cuerpo = {
            "estado": "listo",
            "base_de_datos": "ok",
            "cola": "ok",
            "esquema": "ok" if revision == CABEZA_ESPERADA else "incompatible",
            "ruteo": self._ruteo(),
        }
        if revision != CABEZA_ESPERADA:
            cuerpo["estado"] = "no_listo"
            return 503, cuerpo
        return 200, cuerpo

    def _ruteo(self) -> str:
        if self.ruteo is None or not getattr(self.ruteo, "configurado", False):
            return "sin_configurar"
        return "ok" if self.ruteo.disponible() else "no_disponible"

    def registrar_rutas(self) -> None:
        metricas.RUTAS_DE_SALUD["/health/live"] = self.vivo
        metricas.RUTAS_DE_SALUD["/health/ready"] = self.listo
