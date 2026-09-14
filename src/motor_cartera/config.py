"""Configuracion del proyecto, leida del entorno."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MC_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://motor:motor@localhost:5434/cartera_dev"
    semilla: int = 31416
    """Semilla fija para que el generador sea reproducible."""


config = Config()
