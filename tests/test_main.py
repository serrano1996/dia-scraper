import socket
import time
from collections.abc import Iterator

import httpx
import pytest
import respx
from fakeredis import FakeAsyncRedis
from fastapi.testclient import TestClient
from redis.exceptions import TimeoutError as RedisTimeoutError

import app.main as main
from app.core.config import Settings, get_settings
from app.core.state import AppResources, resources
from app.exceptions import CooldownActiveError, OutboundRateLimitedError
from app.main import create_app
from app.services.cooldown import COOLDOWN_KEY
from app.services.outbound import OutboundGate
from app.services.postal_code_sessions import PostalCodeSessions


@pytest.fixture(autouse=True)
def environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DIA_BASE_URL", "https://dia.test")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("API_KEYS", "synthetic-main-token")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class SpyRedis(FakeAsyncRedis):
    closed = False

    async def aclose(self, close_connection_pool: bool | None = None) -> None:
        self.closed = True
        await super().aclose(close_connection_pool)


def test_the_products_route_is_registered() -> None:
    paths = create_app().openapi()["paths"]

    assert "get" in paths["/api/v1/products"]


def test_lifespan_builds_and_closes_the_shared_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = SpyRedis()
    monkeypatch.setattr(main, "create_redis", lambda settings: redis)
    app = create_app()

    with TestClient(app) as client:
        res = resources(app)
        assert isinstance(res, AppResources)
        assert res.redis is redis
        assert isinstance(res.sessions, PostalCodeSessions)
        # Dia's default postal code needs no request to build its session.
        session = client.portal.call(res.sessions.get, "28041")
        assert session.client.base_url == httpx.URL("https://dia.test")

    assert session.client.is_closed
    assert redis.closed


def test_resources_outside_the_lifespan_fail_clearly() -> None:
    with pytest.raises(RuntimeError, match="lifespan"):
        resources(create_app())


def test_redis_is_closed_when_the_session_pool_cannot_be_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = SpyRedis()
    monkeypatch.setattr(main, "create_redis", lambda settings: redis)

    def broken_pool(**kwargs):
        raise RuntimeError("cannot build the session pool")

    monkeypatch.setattr(main, "PostalCodeSessions", broken_pool)

    with pytest.raises(RuntimeError, match="session pool"), TestClient(create_app()):
        pass

    assert redis.closed


# --- Anti-ban wiring (spec 003, T11) ---


def app_with(monkeypatch: pytest.MonkeyPatch, redis: FakeAsyncRedis):
    monkeypatch.setenv("RETRY_JITTER_MAX_S", "0")
    get_settings.cache_clear()
    monkeypatch.setattr(main, "create_redis", lambda settings: redis)
    return create_app()


def test_the_lifespan_builds_one_outbound_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    app = app_with(monkeypatch, FakeAsyncRedis())

    with TestClient(app):
        assert isinstance(resources(app).gate, OutboundGate)


def test_during_a_cooldown_neither_puts_nor_searches_reach_dia(
    monkeypatch: pytest.MonkeyPatch, respx_mock: respx.MockRouter
) -> None:
    put = respx_mock.put("https://dia.test/api/v1/common-aggregator/save-shipping-address")
    search = respx_mock.get("https://dia.test/api/v1/search-back/search/reduced")
    redis = FakeAsyncRedis()
    app = app_with(monkeypatch, redis)

    with TestClient(app) as client:
        client.portal.call(lambda: redis.set(COOLDOWN_KEY, "1", ex=300))
        with pytest.raises(CooldownActiveError):
            client.portal.call(resources(app).sessions.get, "08001")
        response = client.get(
            "/api/v1/products",
            params={"postal_code": "28041", "term": "pan"},
            headers={"X-API-Key": "synthetic-main-token"},
        )

    assert response.status_code == 502
    assert put.call_count == search.call_count == 0


def test_the_pool_limits_new_sessions(
    monkeypatch: pytest.MonkeyPatch, respx_mock: respx.MockRouter
) -> None:
    monkeypatch.setenv("NEW_SESSION_LIMIT", "1")
    put = respx_mock.put("https://dia.test/api/v1/common-aggregator/save-shipping-address").mock(
        return_value=httpx.Response(204)
    )
    app = app_with(monkeypatch, FakeAsyncRedis())

    with TestClient(app) as client:
        client.portal.call(resources(app).sessions.get, "08001")
        with pytest.raises(OutboundRateLimitedError):
            client.portal.call(resources(app).sessions.get, "41001")

    assert put.call_count == 1


# --- Logging (spec 004 RF-1, T11) ---


def test_the_lifespan_configures_logging_with_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "debug")
    calls: list[str] = []
    monkeypatch.setattr(main, "configure_logging", lambda level: calls.append(level))
    app = app_with(monkeypatch, FakeAsyncRedis())

    with TestClient(app):
        pass

    assert calls == ["DEBUG"]


# --- Redis timeouts (spec 008 RF-1, plan-D1) ---


def test_create_redis_bounds_connecting_and_every_operation() -> None:
    settings = Settings(_env_file=None, redis_timeout_seconds=1.5)

    client = main.create_redis(settings)

    kwargs = client.connection_pool.connection_kwargs
    assert (kwargs["socket_connect_timeout"], kwargs["socket_timeout"]) == (1.5, 1.5)


@pytest.fixture
def silent_redis_port() -> Iterator[int]:
    """A port that completes the TCP handshake and never answers: a hung Redis.

    The kernel accepts connections into the backlog without `accept()`, so the
    client connects and then waits for a reply that never comes.
    """
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(8)
        yield server.getsockname()[1]


async def test_a_hung_redis_fails_after_the_timeout_not_later(silent_redis_port: int) -> None:
    # No retries hidden in the client: one operation costs one timeout (plan-D1).
    settings = Settings(
        _env_file=None,
        redis_url=f"redis://127.0.0.1:{silent_redis_port}/0",
        redis_timeout_seconds=0.3,
    )
    client = main.create_redis(settings)
    started = time.perf_counter()

    try:
        with pytest.raises(RedisTimeoutError):
            await client.ping()
    finally:
        await client.aclose()

    assert time.perf_counter() - started < 0.3 * 3
