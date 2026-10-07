"""End to end, spec 003: cooldown after an Akamai block and outbound limits (fakeredis, respx)."""

from collections.abc import Iterator
from contextlib import ExitStack

import httpx
import pytest
import respx
from fakeredis import FakeAsyncRedis
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.config import get_settings
from app.main import create_app
from app.services.cooldown import COOLDOWN_KEY
from tests.fixture_data import load_fixture
from tests.integration.conftest import (
    DIA_BASE_URL,
    SEARCH_URL,
    mock_dia_put,
    mock_dia_search,
)

URL = "/api/v1/products"
AKAMAI_403 = httpx.Response(
    403, text="<HTML><TITLE>Access Denied</TITLE></HTML>", headers={"Content-Type": "text/html"}
)


def body(postal_code: str = "28041") -> dict:
    data = load_fixture("dia_search_leche.json")
    data["cart"]["postal_code"] = postal_code
    return data


@pytest.fixture
def redis() -> FakeAsyncRedis:
    return FakeAsyncRedis()


@pytest.fixture
def start(monkeypatch: pytest.MonkeyPatch, redis: FakeAsyncRedis) -> Iterator:
    """Start app instances sharing one fakeredis, as instances share Redis in production."""
    monkeypatch.setenv("DIA_BASE_URL", DIA_BASE_URL)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("RETRY_BASE_DELAY", "0")
    monkeypatch.setenv("RETRY_JITTER_MAX_S", "0")
    monkeypatch.setattr(main_module, "create_redis", lambda _settings: redis)
    stack = ExitStack()

    def _start(**env: str) -> TestClient:
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        get_settings.cache_clear()
        return stack.enter_context(TestClient(create_app()))

    with stack:
        yield _start
    get_settings.cache_clear()


def search(client: TestClient, term: str, postal_code: str = "28041") -> httpx.Response:
    return client.get(URL, params={"postal_code": postal_code, "term": term})


def test_after_an_akamai_block_only_the_cache_is_served(
    start, redis: FakeAsyncRedis, respx_mock: respx.MockRouter
) -> None:
    client = start()
    route = respx_mock.get(SEARCH_URL).mock(
        side_effect=[httpx.Response(200, json=body()), AKAMAI_403]
    )
    put = mock_dia_put(
        respx_mock,
        httpx.Response(206, json=load_fixture("dia_save_shipping_address_no_service.json")),
    )
    assert search(client, "leche").status_code == 200  # cached
    assert search(client, "agua", postal_code="35001").status_code == 404  # remembered

    blocked = search(client, "pan")
    later = search(client, "arroz")
    cached = search(client, "leche")
    not_served = search(client, "cafe", postal_code="35001")
    new_postal_code = search(client, "pan", postal_code="08001")

    assert blocked.status_code == later.status_code == new_postal_code.status_code == 502
    assert cached.status_code == 200
    assert not_served.status_code == 404
    assert route.call_count == 2  # leche and pan: nothing after the block
    assert put.call_count == 1  # 35001 only: no PUT for 08001 during the cooldown
    ttl = client.portal.call(redis.ttl, COOLDOWN_KEY)
    assert 0 < ttl <= 300


def test_an_akamai_block_on_a_put_starts_the_cooldown_too(
    start, redis: FakeAsyncRedis, respx_mock: respx.MockRouter
) -> None:
    client = start()
    mock_dia_put(respx_mock, AKAMAI_403)
    route = mock_dia_search(respx_mock, json_body=body())

    assert search(client, "pan", postal_code="08001").status_code == 502
    assert search(client, "pan").status_code == 502

    assert route.call_count == 0
    assert client.portal.call(redis.exists, COOLDOWN_KEY)


def test_the_global_limit_stops_requests_before_they_leave(
    start, respx_mock: respx.MockRouter
) -> None:
    client = start(DIA_RATE_LIMIT="2")
    route = mock_dia_search(respx_mock, json_body=body())

    statuses = [search(client, term).status_code for term in ("leche", "agua", "pan")]

    assert statuses == [200, 200, 502]
    assert route.call_count == 2


def test_the_new_session_limit_refuses_the_next_postal_code_only(
    start, respx_mock: respx.MockRouter
) -> None:
    client = start(NEW_SESSION_LIMIT="1")
    put = mock_dia_put(respx_mock, httpx.Response(204), httpx.Response(204))
    respx_mock.get(SEARCH_URL).mock(
        side_effect=[
            httpx.Response(200, json=body("08001")),
            httpx.Response(200, json=body("08001")),
        ]
    )

    first = search(client, "leche", postal_code="08001")
    refused = search(client, "leche", postal_code="41001")
    same_postal_code = search(client, "agua", postal_code="08001")

    assert (first.status_code, refused.status_code, same_postal_code.status_code) == (200, 502, 200)
    assert put.call_count == 1


def test_two_instances_share_the_cooldown_and_the_limit(
    start, respx_mock: respx.MockRouter
) -> None:
    one = start(DIA_RATE_LIMIT="2")
    two = start(DIA_RATE_LIMIT="2")
    route = respx_mock.get(SEARCH_URL).mock(
        side_effect=[httpx.Response(200, json=body()), httpx.Response(200, json=body()), AKAMAI_403]
    )

    assert search(one, "leche").status_code == 200
    assert search(two, "agua").status_code == 200
    assert search(one, "pan").status_code == 502  # limit shared: 2 already sent
    assert route.call_count == 2


def test_a_block_seen_by_one_instance_stops_the_other(start, respx_mock: respx.MockRouter) -> None:
    one = start()
    two = start()
    route = respx_mock.get(SEARCH_URL).mock(
        side_effect=[AKAMAI_403, httpx.Response(200, json=body())]
    )

    assert search(one, "pan").status_code == 502
    assert search(two, "leche").status_code == 502

    assert route.call_count == 1
