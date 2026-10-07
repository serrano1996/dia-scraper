"""Application settings loaded from environment variables (and an optional `.env` file)."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Missing required variables fail at startup (spec 001 RF-23)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    dia_base_url: str
    redis_url: str
    cache_ttl_seconds: int = Field(default=3600, gt=0)
    retry_max_attempts: int = Field(default=3, gt=0)
    # Zero disables the waits between attempts.
    retry_base_delay: float = Field(default=0.5, ge=0)
    # Each request to Dia: connect, read, write and pool (spec 001 RNF-3).
    http_timeout_seconds: float = Field(default=10, gt=0)
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
