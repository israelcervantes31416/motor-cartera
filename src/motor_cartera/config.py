"""Configuracion del proyecto, leida del entorno."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MC_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://motor:motor@localhost:5434/cartera_dev"
    semilla: int = 31416
    """Semilla fija para que el generador sea reproducible."""
    tolerancia_rechazo: float = Field(default=0.05, ge=0, lt=1)
    """Fraccion maxima de registros rechazados con la que una corrida todavia publica.

    Es una regla de negocio y vive en el servidor: si el cliente de la API pudiera
    fijarla, la barrera seria opcional. 0 es todo o nada. 1 no se admite: publicaria
    aunque no pasara ningun registro.
    """


config = Config()
