import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings

REQUIRED = {
    "DIA_BASE_URL": "https://dia.test",
    "REDIS_URL": "redis://localhost:6379/0",
}
OPTIONAL = [
    "CACHE_TTL_SECONDS",
    "RETRY_MAX_ATTEMPTS",
    "RETRY_BASE_DELAY",
    "HTTP_TIMEOUT_SECONDS",
    "LOG_LEVEL",
    "SESSION_MAX_AGE_SECONDS",
    "MAX_SESSIONS",
    "POSTAL_CODE_NEGATIVE_CACHE_TTL_SECONDS",
    "AKAMAI_COOLDOWN_SECONDS",
    "DIA_RATE_LIMIT",
    "DIA_RATE_WINDOW_SECONDS",
    "NEW_SESSION_LIMIT",
    "NEW_SESSION_WINDOW_SECONDS",
    "RETRY_JITTER_MAX_S",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [*REQUIRED, *OPTIONAL]:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()


def set_required(monkeypatch: pytest.MonkeyPatch, *, skip: str | None = None) -> None:
    for name, value in REQUIRED.items():
        if name != skip:
            monkeypatch.setenv(name, value)


@pytest.mark.parametrize("missing", list(REQUIRED))
def test_missing_required_variable_fails(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    set_required(monkeypatch, skip=missing)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    assert missing.lower() in str(exc_info.value)


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required(monkeypatch)

    settings = Settings(_env_file=None)

    assert settings.dia_base_url == "https://dia.test"
    assert settings.redis_url == "redis://localhost:6379/0"
    assert settings.cache_ttl_seconds == 3600
    assert settings.retry_max_attempts == 3
    assert settings.retry_base_delay == 0.5
    assert settings.http_timeout_seconds == 10
    assert settings.log_level == "INFO"
    # Spec 002: postal code sessions and the negative cache (spec-D2, spec-D5).
    assert settings.session_max_age_seconds == 3000
    assert settings.max_sessions == 100
    assert settings.postal_code_negative_cache_ttl_seconds == 86400
    # Spec 003: anti-ban (spec-D1, D3, D4, D5).
    assert settings.akamai_cooldown_seconds == 300
    assert settings.dia_rate_limit == 30
    assert settings.dia_rate_window_seconds == 60
    assert settings.new_session_limit == 10
    assert settings.new_session_window_seconds == 600
    assert settings.retry_jitter_max_s == 0.3


def test_environment_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required(monkeypatch)
    monkeypatch.setenv("CACHE_TTL_SECONDS", "60")
    monkeypatch.setenv("RETRY_MAX_ATTEMPTS", "5")
    monkeypatch.setenv("RETRY_BASE_DELAY", "0.1")
    monkeypatch.setenv("HTTP_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("SESSION_MAX_AGE_SECONDS", "600")
    monkeypatch.setenv("MAX_SESSIONS", "5")
    monkeypatch.setenv("POSTAL_CODE_NEGATIVE_CACHE_TTL_SECONDS", "3600")

    settings = Settings(_env_file=None)

    assert settings.cache_ttl_seconds == 60
    assert settings.retry_max_attempts == 5
    assert settings.retry_base_delay == 0.1
    assert settings.http_timeout_seconds == 2.5
    assert settings.log_level == "DEBUG"
    assert settings.session_max_age_seconds == 600
    assert settings.max_sessions == 5
    assert settings.postal_code_negative_cache_ttl_seconds == 3600


@pytest.mark.parametrize(
    "name",
    [
        "CACHE_TTL_SECONDS",
        "RETRY_MAX_ATTEMPTS",
        "HTTP_TIMEOUT_SECONDS",
        "SESSION_MAX_AGE_SECONDS",
        "MAX_SESSIONS",
        "POSTAL_CODE_NEGATIVE_CACHE_TTL_SECONDS",
        "AKAMAI_COOLDOWN_SECONDS",
        "DIA_RATE_WINDOW_SECONDS",
        "NEW_SESSION_WINDOW_SECONDS",
    ],
)
def test_non_positive_values_fail(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    set_required(monkeypatch)
    monkeypatch.setenv(name, "0")

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    assert name.lower() in str(exc_info.value)


def test_retry_base_delay_accepts_zero_but_not_negative(monkeypatch: pytest.MonkeyPatch) -> None:
    # Zero is how the integration tests skip real waits (tasks T18).
    set_required(monkeypatch)
    monkeypatch.setenv("RETRY_BASE_DELAY", "0")
    assert Settings(_env_file=None).retry_base_delay == 0

    monkeypatch.setenv("RETRY_BASE_DELAY", "-0.1")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_get_settings_returns_the_same_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required(monkeypatch)

    assert get_settings() is get_settings()


@pytest.mark.parametrize(
    ("name", "attribute"),
    [
        ("DIA_RATE_LIMIT", "dia_rate_limit"),
        ("NEW_SESSION_LIMIT", "new_session_limit"),
        ("RETRY_JITTER_MAX_S", "retry_jitter_max_s"),
    ],
)
def test_zero_disables_but_negative_fails(
    monkeypatch: pytest.MonkeyPatch, name: str, attribute: str
) -> None:
    set_required(monkeypatch)
    monkeypatch.setenv(name, "0")
    assert getattr(Settings(_env_file=None), attribute) == 0

    monkeypatch.setenv(name, "-1")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_the_environment_overrides_the_anti_ban_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required(monkeypatch)
    for name, value in {
        "AKAMAI_COOLDOWN_SECONDS": "120",
        "DIA_RATE_LIMIT": "5",
        "DIA_RATE_WINDOW_SECONDS": "10",
        "NEW_SESSION_LIMIT": "2",
        "NEW_SESSION_WINDOW_SECONDS": "30",
        "RETRY_JITTER_MAX_S": "0.1",
    }.items():
        monkeypatch.setenv(name, value)

    settings = Settings(_env_file=None)

    assert (settings.akamai_cooldown_seconds, settings.dia_rate_limit) == (120, 5)
    assert (settings.dia_rate_window_seconds, settings.new_session_limit) == (10, 2)
    assert (settings.new_session_window_seconds, settings.retry_jitter_max_s) == (30, 0.1)
