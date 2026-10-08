"""Application settings loaded from environment variables (and an optional `.env` file)."""

from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


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
    # One Dia session per postal code (spec 002 spec-D2): renewed this long after
    # its creation, below the hour `session_id` lasts (Fase 0 §2), and at most
    # this many at once, dropping the least recently used.
    session_max_age_seconds: int = Field(default=3000, gt=0)
    max_sessions: int = Field(default=100, gt=0)
    # How long a postal code Dia does not serve is answered 404 without asking
    # Dia again (spec 002 spec-D5).
    postal_code_negative_cache_ttl_seconds: int = Field(default=86400, gt=0)
    # Anti-ban (spec 003). None of these limits is a measured threshold: Dia was
    # never seen blocking by rate (Fase 0 §5). They are prudence, tuned with the
    # warnings logged when they trigger.
    # No request to Dia for this long after an Akamai block, across instances.
    akamai_cooldown_seconds: int = Field(default=300, gt=0)
    # At most this many requests to Dia (retries and PUTs included) per window,
    # across instances. 0 disables the limit.
    dia_rate_limit: int = Field(default=30, ge=0)
    dia_rate_window_seconds: int = Field(default=60, gt=0)
    # At most this many new Dia sessions (PUTs) per window. 0 disables the limit.
    new_session_limit: int = Field(default=10, ge=0)
    new_session_window_seconds: int = Field(default=600, gt=0)
    # Random extra wait, up to this many seconds, added to every retry. 0 = none.
    retry_jitter_max_s: float = Field(default=0.3, ge=0)
    # Tokens accepted in X-API-Key (spec 005), comma-separated in the environment.
    # `NoDecode` stops pydantic-settings from reading it as JSON ("a,b" would fail)
    # and `repr=False` keeps the tokens out of any printed or logged Settings
    # (RF-8, RF-11, plan-D1). Empty = nobody authenticates (RF-9).
    api_keys: Annotated[frozenset[str], NoDecode] = Field(default=frozenset(), repr=False)

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        """Any case; an unknown level fails at startup (spec 004 RF-2)."""
        level = value.strip().upper()
        if level not in LOG_LEVELS:
            raise ValueError(f"LOG_LEVEL must be one of {sorted(LOG_LEVELS)}, got {value!r}")
        return level

    @field_validator("api_keys", mode="before")
    @classmethod
    def _split_api_keys(cls, value: object) -> object:
        """`" a , ,b "` -> `{"a", "b"}`: trim each entry and drop empty ones (RF-8)."""
        if isinstance(value, str):
            return frozenset(key.strip() for key in value.split(",") if key.strip())
        return value


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
