import httpx
import pytest
import respx
from fakeredis import FakeAsyncRedis
from fastapi.testclient import TestClient

import app.main as main
from app.core.config import get_settings
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
        response = client.get("/api/v1/products", params={"postal_code": "28041", "term": "pan"})

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
