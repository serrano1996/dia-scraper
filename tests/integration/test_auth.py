"""End to end, spec 005: nothing reaches Redis or Dia without a valid X-API-Key."""

import logging

import httpx
import pytest
import respx

from app.services.cooldown import COOLDOWN_KEY
from tests.fixture_data import load_fixture
from tests.integration.conftest import API_TOKEN, PUT_URL, SEARCH_URL, Harness

URL = "/api/v1/products"
PARAMS = {"postal_code": "28041", "term": "leche"}
NO_TOKEN = {"X-API-Key": ""}
WRONG_TOKEN = {"X-API-Key": "synthetic-wrong-token"}


def all_keys(harness: Harness) -> list[bytes]:
    assert harness.client.portal is not None
    return harness.client.portal.call(harness.redis.keys, "*")


@pytest.mark.parametrize("headers", [NO_TOKEN, WRONG_TOKEN], ids=["missing", "invalid"])
@pytest.mark.parametrize("postal_code", ["28041", "08001"], ids=["default", "new-postal-code"])
def test_without_a_valid_token_neither_dia_nor_redis_is_touched(
    harness: Harness, respx_mock: respx.MockRouter, headers: dict, postal_code: str
) -> None:
    search = respx_mock.get(SEARCH_URL)
    put = respx_mock.put(PUT_URL)

    response = harness.client.get(
        URL, params=PARAMS | {"postal_code": postal_code}, headers=headers
    )

    assert response.status_code == 401
    assert search.call_count == put.call_count == 0
    assert all_keys(harness) == []


def test_during_a_cooldown_a_bad_token_is_a_401_not_a_502(
    harness: Harness, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(SEARCH_URL)
    harness.client.portal.call(lambda: harness.redis.set(COOLDOWN_KEY, "1", ex=300))

    assert harness.client.get(URL, params=PARAMS, headers=WRONG_TOKEN).status_code == 401
    assert harness.client.get(URL, params=PARAMS).status_code == 502  # the harness token


def test_a_401_carries_its_request_id(harness: Harness) -> None:
    response = harness.client.get(URL, params=PARAMS, headers=NO_TOKEN)

    assert response.headers["WWW-Authenticate"] == "ApiKey"
    assert len(response.headers["X-Request-ID"]) == 32


@pytest.mark.parametrize("path", ["/health", "/docs", "/openapi.json"])
def test_outside_api_v1_everything_is_public(harness: Harness, path: str) -> None:
    response = harness.client.get(path, headers=NO_TOKEN)

    assert response.status_code == 200


def test_health_is_ok_and_touches_neither_redis_nor_dia(
    harness: Harness, respx_mock: respx.MockRouter
) -> None:
    search = respx_mock.get(SEARCH_URL)

    response = harness.client.get("/health", headers=NO_TOKEN)

    assert response.json() == {"status": "ok"}
    assert search.call_count == 0
    assert all_keys(harness) == []


def test_no_line_ever_carries_a_token(
    harness: Harness, respx_mock: respx.MockRouter, caplog: pytest.LogCaptureFixture
) -> None:
    respx_mock.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=load_fixture("dia_search_leche.json"))
    )

    with caplog.at_level(logging.DEBUG):
        harness.client.get(URL, params=PARAMS)  # valid token
        harness.client.get(URL, params=PARAMS, headers=WRONG_TOKEN)
        harness.client.get(URL, params=PARAMS | {"api_key": API_TOKEN}, headers=NO_TOKEN)

    ours = " ".join(r.getMessage() for r in caplog.records if r.name.startswith("app."))
    assert API_TOKEN not in ours
    assert "synthetic-wrong-token" not in ours


def test_openapi_declares_the_api_key_for_api_v1(harness: Harness) -> None:
    schema = harness.client.get("/openapi.json").json()

    schemes = schema["components"]["securitySchemes"]
    [(name, scheme)] = schemes.items()
    assert scheme == {"type": "apiKey", "in": "header", "name": "X-API-Key"}
    assert schema["paths"]["/api/v1/products"]["get"]["security"] == [{name: []}]
    assert "security" not in schema["paths"].get("/health", {}).get("get", {})
