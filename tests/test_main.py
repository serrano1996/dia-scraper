import httpx
import pytest
from fakeredis import FakeAsyncRedis
from fastapi.testclient import TestClient

import app.main as main
from app.core.config import get_settings
from app.core.state import AppResources, resources
from app.main import create_app


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

    with TestClient(app):
        res = resources(app)
        assert isinstance(res, AppResources)
        assert res.redis is redis
        assert isinstance(res.http_client, httpx.AsyncClient)
        assert res.http_client.base_url == httpx.URL("https://dia.test")
        http_client = res.http_client

    assert http_client.is_closed
    assert redis.closed


def test_resources_outside_the_lifespan_fail_clearly() -> None:
    with pytest.raises(RuntimeError, match="lifespan"):
        resources(create_app())


def test_redis_is_closed_when_the_http_client_cannot_be_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = SpyRedis()
    monkeypatch.setattr(main, "create_redis", lambda settings: redis)

    def broken_http_client(settings):
        raise RuntimeError("cannot build the HTTP client")

    monkeypatch.setattr(main, "create_http_client", broken_http_client)

    with pytest.raises(RuntimeError, match="HTTP client"), TestClient(create_app()):
        pass

    assert redis.closed
