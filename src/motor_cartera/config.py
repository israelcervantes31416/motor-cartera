"""Configuracion del proyecto, leida del entorno."""

from __future__ import annotations

from pathlib import Path
from typing import Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

IDENTIFICADOR_INTERNO = r"^[A-Z0-9_]{1,32}$"
"""Forma de despacho_id y cartera_id: mayusculas, digitos y guion bajo, como DSP_001."""


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MC_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://motor:motor@localhost:5434/cartera_dev"
    semilla: int = 31416
    """Semilla fija para que el generador sea reproducible."""

    source_store_root: Path = Path("datos/fuentes")
    """MC_SOURCE_STORE_ROOT: la raiz del almacen de artefactos fuente, donde cada archivo recibido
    se guarda por su SHA-256 y no se borra. La API y el worker tienen que ver la misma: en el
    compose es un volumen propio, aparte del de PostgreSQL, que guarda solo su registro."""

    # Un despacho y una cartera: metadata del sistema, no columnas de las fuentes. Cada corrida y
    # cada ingesta de pagos guarda con cual se registro. v0.6 no administra varios despachos; que
    # el valor viaje con cada registro es lo que permite cambiarlo despues sin reescribir nada.
    despacho_id: str = Field(default="DSP_001", pattern=IDENTIFICADOR_INTERNO)
    """El despacho que opera este sistema."""
    cartera_id: str = Field(default="CARTERA_PRINCIPAL", pattern=IDENTIFICADOR_INTERNO)
    """La cartera del acreedor que ese despacho gestiona."""
    tolerancia_rechazo: float = Field(default=0.05, ge=0, lt=1)
    """Fraccion maxima de registros rechazados con la que una corrida todavia publica.

    Es una regla de negocio y vive en el servidor: si el cliente de la API pudiera
    fijarla, la barrera seria opcional. 0 es todo o nada. 1 no se admite: publicaria
    aunque no pasara ningun registro.
    """
    api_key: SecretStr | None = None
    """Clave que la API exige en la cabecera X-API-Key. Sin ella la API no arranca.

    Es opcional aqui solo porque el CLI, Alembic y las pruebas no la necesitan.
    """
    tamano_maximo_mb: int = Field(default=512, gt=0)
    """Tope del archivo que aceptan POST /corridas y POST /pagos, en MiB. El archivo se copia al
    almacen por bloques mientras llega, sin cargarlo en memoria: el tope cuida el disco, y alcanza
    para la cartera objetivo de 500,000 cuentas."""

    # El worker. Son parametros operativos: deciden cuando, cada cuanto y cuantas veces se ejecuta
    # un trabajo, nunca que calcula un motor. Ninguno cambia una decision, un municipio ni una ruta.
    worker_poll_segundos: float = Field(default=0.5, gt=0)
    """Cuanto espera el worker antes de volver a buscar trabajo cuando la cola esta vacia."""
    worker_lease_segundos: float = Field(default=60, gt=0)
    """Por cuanto tiempo un trabajo es de su worker sin que este lata. Si deja de latir, otro worker
    lo puede tomar cuando vence."""
    worker_heartbeat_segundos: float = Field(default=20, gt=0)
    """Cada cuanto late el worker para renovar el lease del trabajo que ejecuta. Menor que el lease:
    si no, el lease venceria entre un latido y otro."""
    worker_max_intentos: int = Field(default=5, ge=1)
    """Cuantas veces se puede tomar un trabajo antes de darlo por FALLIDO. Cada trabajo copia el
    valor al nacer, y conserva la politica con que nacio."""
    worker_backoff_segundos: float = Field(default=1, gt=0)
    """La espera tras el primer error de un trabajo; se duplica en cada intento: 1, 2, 4, 8..."""

    @model_validator(mode="after")
    def _el_latido_cabe_en_el_lease(self) -> Self:
        if self.worker_heartbeat_segundos >= self.worker_lease_segundos:
            raise ValueError(
                "MC_WORKER_HEARTBEAT_SEGUNDOS debe ser menor que MC_WORKER_LEASE_SEGUNDOS: "
                f"{self.worker_heartbeat_segundos} no es menor que {self.worker_lease_segundos}."
            )
        return self


config = Config()
