"""End to end, spec 008: a Redis that is down or hung degrades the service, never a 500.

The app runs its real `lifespan` against a broken Redis double (plan-D8); Dia is
respx. Postal code 28041 is Dia's default: its session needs no PUT, so each
search is one request to Dia.
"""

import logging
from collections.abc import Iterator
from contextlib import ExitStack

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.config import get_settings
from app.main import create_app
from tests.fixture_data import load_fixture
from tests.integration.conftest import API_TOKEN, AUTH_HEADERS, DIA_BASE_URL, SEARCH_URL
from tests.redis_doubles import DOWN, HUNG, BrokenRedis, CountingBrokenRedis

URL = "/api/v1/products"
AKAMAI_403 = httpx.Response(
    403, text="<HTML><TITLE>Access Denied</TITLE></HTML>", headers={"Content-Type": "text/html"}
)


@pytest.fixture
def start(monkeypatch: pytest.MonkeyPatch) -> Iterator:
    """Start the app against `redis`, with extra environment variables."""
    monkeypatch.setenv("DIA_BASE_URL", DIA_BASE_URL)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("API_KEYS", API_TOKEN)
    monkeypatch.setenv("RETRY_BASE_DELAY", "0")
    monkeypatch.setenv("RETRY_JITTER_MAX_S", "0")
    stack = ExitStack()

    def _start(redis: object, **env: str) -> TestClient:
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setattr(main_module, "create_redis", lambda _settings: redis)
        get_settings.cache_clear()
        return stack.enter_context(TestClient(create_app(), headers=AUTH_HEADERS))

    with stack:
        yield _start
    get_settings.cache_clear()


def search(client: TestClient, term: str) -> httpx.Response:
    return client.get(URL, params={"postal_code": "28041", "term": term})


@pytest.mark.parametrize("error", [DOWN, HUNG], ids=["down", "hung"])
def test_without_redis_searches_are_served_from_dia(
    start, respx_mock: respx.MockRouter, caplog: pytest.LogCaptureFixture, error: Exception
) -> None:
    # H1, RF-4: uncached, but served.
    client = start(BrokenRedis(error))
    route = respx_mock.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=load_fixture("dia_search_leche.json"))
    )

    first = search(client, "leche")
    second = search(client, "leche")

    assert (first.status_code, second.status_code) == (200, 200)
    assert first.json()["products"]
    assert route.call_count == 2  # nothing was cached
    assert client.get("/health").status_code == 200
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_after_the_first_failure_redis_is_not_touched_again(
    start, respx_mock: respx.MockRouter, caplog: pytest.LogCaptureFixture
) -> None:
    # RF-2, RF-3, RF-9: one circuit for every use of Redis, one WARNING.
    redis = CountingBrokenRedis(DOWN)
    client = start(redis)
    respx_mock.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=load_fixture("dia_search_leche.json"))
    )

    assert search(client, "leche").status_code == 200
    assert search(client, "agua").status_code == 200

    assert redis.attempts == 1
    circuit = [r for r in caplog.records if r.name == "app.services.redis_circuit"]
    assert [(r.levelno, r.getMessage()) for r in circuit] == [
        (logging.WARNING, "redis circuit open seconds=10 error=ConnectionError")
    ]


def test_without_redis_the_global_limit_still_holds(start, respx_mock: respx.MockRouter) -> None:
    # H2, RF-7.
    client = start(BrokenRedis(DOWN), DIA_RATE_LIMIT="1")
    route = respx_mock.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=load_fixture("dia_search_leche.json"))
    )

    assert search(client, "leche").status_code == 200
    assert search(client, "agua").status_code == 502
    assert route.call_count == 1


def test_without_redis_an_akamai_block_still_stops_the_next_request(
    start, respx_mock: respx.MockRouter
) -> None:
    # H2, RF-6: the cooldown lives in the process.
    client = start(BrokenRedis(DOWN))
    route = respx_mock.get(SEARCH_URL).mock(return_value=AKAMAI_403)

    assert search(client, "leche").status_code == 502
    assert search(client, "agua").status_code == 502
    assert route.call_count == 1
