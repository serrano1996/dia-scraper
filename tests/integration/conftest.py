"""Integration harness: real app + real `lifespan`, fakeredis and respx (plan-D15).

`create_redis` is patched to return a brand-new `FakeAsyncRedis()` per test, so
no state leaks between tests even though the app's `lifespan` calls it as if it
were talking to a real Redis instance. Dia is never called: respx answers.
"""

from collections.abc import Iterator
from dataclasses import dataclass

import httpx
import pytest
import respx
from fakeredis import FakeAsyncRedis
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.config import get_settings
from app.main import create_app

DIA_BASE_URL = "https://dia.test"
SEARCH_URL = f"{DIA_BASE_URL}/api/v1/search-back/search/reduced"
PUT_URL = f"{DIA_BASE_URL}/api/v1/common-aggregator/save-shipping-address"


@dataclass
class Harness:
    client: TestClient
    redis: FakeAsyncRedis

    def cache_keys(self) -> list[bytes]:
        """Redis keys, read on the app's own event loop (the TestClient portal)."""
        assert self.client.portal is not None
        return self.client.portal.call(self.redis.keys, "*")


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> Iterator[Harness]:
    monkeypatch.setenv("DIA_BASE_URL", DIA_BASE_URL)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("RETRY_BASE_DELAY", "0")
    monkeypatch.setenv("RETRY_JITTER_MAX_S", "0")
    get_settings.cache_clear()
    redis = FakeAsyncRedis()
    monkeypatch.setattr(main_module, "create_redis", lambda _settings: redis)

    with TestClient(create_app()) as client:
        yield Harness(client=client, redis=redis)
    get_settings.cache_clear()


def mock_dia_search(
    respx_mock: respx.MockRouter,
    *,
    json_body: object = None,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
    text: str | None = None,
) -> respx.Route:
    """Register the single mocked route a search hits, returning its `respx.Route`."""
    if text is not None:
        response = httpx.Response(status_code, text=text, headers=headers or {})
    else:
        response = httpx.Response(status_code, json=json_body, headers=headers or {})
    return respx_mock.get(SEARCH_URL).mock(return_value=response)


def mock_dia_put(respx_mock: respx.MockRouter, *responses: httpx.Response) -> respx.Route:
    """Register `save-shipping-address`, answering each call with the next response."""
    return respx_mock.put(PUT_URL).mock(side_effect=list(responses))
