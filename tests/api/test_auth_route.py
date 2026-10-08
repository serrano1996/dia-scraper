"""spec 005 RF-1..RF-3, RF-7: X-API-Key on /api/v1, with the real app and its lifespan."""

from collections.abc import Iterator

import pytest
from fakeredis import FakeAsyncRedis
from fastapi.testclient import TestClient

import app.main as main
from app.core.config import get_settings
from app.core.dependencies import get_product_service
from app.main import create_app
from tests.api.test_products_route import PARAMS, URL, FakeService

# Synthetic, same shape as a real token (constitution #12).
TOKEN = "synthetic-token-0123456789abcdef"


@pytest.fixture
def service() -> FakeService:
    return FakeService()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, service: FakeService) -> Iterator[TestClient]:
    monkeypatch.setenv("DIA_BASE_URL", "https://dia.test")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("API_KEYS", f"{TOKEN},another-synthetic-token")
    get_settings.cache_clear()
    monkeypatch.setattr(main, "create_redis", lambda _settings: FakeAsyncRedis())
    app = create_app()
    app.dependency_overrides[get_product_service] = lambda: service
    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


@pytest.mark.parametrize(
    "headers",
    [{}, {"X-API-Key": ""}, {"X-API-Key": "wrong"}, {"X-API-Key": f" {TOKEN}"}],
    ids=["missing", "empty", "invalid", "with-space"],
)
def test_without_a_valid_token_the_answer_is_the_same_401(
    client: TestClient, service: FakeService, headers: dict[str, str]
) -> None:
    response = client.get(URL, params=PARAMS, headers=headers)

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or missing API key"}
    assert response.headers["WWW-Authenticate"] == "ApiKey"
    assert service.queries == []


def test_authentication_comes_before_validating_the_query(client: TestClient) -> None:
    response = client.get(
        URL, params={"postal_code": "bad", "term": ""}, headers={"X-API-Key": "x"}
    )

    assert response.status_code == 401


@pytest.mark.parametrize("token", [TOKEN, "another-synthetic-token"])
def test_a_valid_token_reaches_the_service(
    client: TestClient, service: FakeService, token: str
) -> None:
    response = client.get(URL, params=PARAMS, headers={"X-API-Key": token})

    assert response.status_code == 200
    assert len(service.queries) == 1


def test_a_valid_token_with_an_invalid_query_is_a_422(client: TestClient) -> None:
    response = client.get(URL, params={"postal_code": "bad"}, headers={"X-API-Key": TOKEN})

    assert response.status_code == 422
