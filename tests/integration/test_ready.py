"""Spec 008 RF-10: `GET /ready` says whether this instance reaches Redis (plan-D7)."""

import pytest
import respx
from fakeredis import FakeAsyncRedis
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.config import get_settings
from app.main import create_app
from tests.integration.conftest import DIA_BASE_URL
from tests.redis_doubles import DOWN, HUNG, BrokenRedis, CountingBrokenRedis


@pytest.fixture
def environment(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setenv("DIA_BASE_URL", DIA_BASE_URL)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("API_KEYS", "synthetic-ready-token")
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


def ready(monkeypatch: pytest.MonkeyPatch, redis: object, *, times: int = 1) -> list:
    monkeypatch.setattr(main_module, "create_redis", lambda _settings: redis)
    # No X-API-Key: public, like /health.
    with respx.mock(assert_all_called=False) as dia, TestClient(create_app()) as client:
        responses = [client.get("/ready") for _ in range(times)]
        assert dia.calls.call_count == 0  # never asks Dia
    return responses


def test_ready_with_redis(environment: pytest.MonkeyPatch) -> None:
    [response] = ready(environment, FakeAsyncRedis())

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "redis": "ok"}


@pytest.mark.parametrize("error", [DOWN, HUNG], ids=["down", "hung"])
def test_not_ready_without_redis(environment: pytest.MonkeyPatch, error: Exception) -> None:
    [response] = ready(environment, BrokenRedis(error))

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "redis": "unreachable"}


def test_with_the_circuit_open_ready_does_not_ping(environment: pytest.MonkeyPatch) -> None:
    redis = CountingBrokenRedis(DOWN)

    responses = ready(environment, redis, times=3)

    assert [r.status_code for r in responses] == [503, 503, 503]
    assert redis.attempts == 1
