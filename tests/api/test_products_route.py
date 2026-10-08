import logging
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.core.dependencies import get_product_service
from app.exceptions import (
    CooldownActiveError,
    OutboundRateLimitedError,
    PageOutOfRangeError,
    PostalCodeNotServedError,
    UpstreamBlockedError,
    UpstreamUnavailableError,
)
from app.main import create_app
from app.models.product import Product, ProductQuery, ProductSearchResponse, SearchMetadata

URL = "/api/v1/products"
PARAMS = {"postal_code": "28001", "term": "leche"}


def canned_response(query: ProductQuery) -> ProductSearchResponse:
    return ProductSearchResponse(
        search=SearchMetadata(
            postal_code=query.postal_code,
            term=query.term,
            warehouse="28041",
            strategy_used="api",
            scraped_at=datetime(2026, 10, 7, 9, 0, tzinfo=UTC),
            total_results=1,
            page=query.page,
            page_size=query.page_size,
            total_pages=1,
        ),
        products=[
            Product(
                id="504P6",
                name="Leche semidesnatada Dia Láctea pack 6 x 1 L",
                price=4.98,
                price_format="0.83 €/L",
                image_url="https://www.dia.es/product_images/504P6/504P6_ISO_0_ES.jpg",
                category="Leche",
            )
        ],
    )


class FakeService:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.queries: list[ProductQuery] = []

    async def search(self, query: ProductQuery) -> ProductSearchResponse:
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        return canned_response(query)


def client_with(service: FakeService) -> TestClient:
    # Without `with`, TestClient does not run the lifespan: no Redis, no Dia.
    app = create_app()
    app.dependency_overrides[get_product_service] = lambda: service
    return TestClient(app)


def test_route_returns_the_service_response() -> None:
    service = FakeService()

    response = client_with(service).get(URL, params=PARAMS | {"page": 2, "page_size": 30})

    assert response.status_code == 200
    body = response.json()
    assert body["search"]["scraped_at"] == "2026-10-07T09:00:00Z"
    assert body["products"][0]["id"] == "504P6"
    assert service.queries == [
        ProductQuery(postal_code="28001", term="leche", page=2, page_size=30)
    ]


def test_invalid_query_is_rejected_without_calling_the_service() -> None:
    service = FakeService()

    response = client_with(service).get(URL, params={"postal_code": "2800", "term": "leche"})

    assert response.status_code == 422
    assert service.queries == []


@pytest.mark.parametrize(
    "error",
    [
        UpstreamUnavailableError("secret internal reason"),
        UpstreamBlockedError("blocked by Akamai (403 HTML)", status_code=403),
    ],
    ids=["unavailable", "blocked"],
)
def test_upstream_errors_answer_502_without_the_internal_reason(error: Exception) -> None:
    response = client_with(FakeService(error)).get(URL, params=PARAMS)

    assert response.status_code == 502
    assert response.json() == {"detail": "Upstream service unavailable"}


def test_page_out_of_range_answers_404() -> None:
    response = client_with(FakeService(PageOutOfRangeError(3))).get(URL, params=PARAMS)

    assert response.status_code == 404
    assert response.json() == {"detail": "Page out of range"}


def test_a_postal_code_dia_does_not_serve_answers_404() -> None:
    error = PostalCodeNotServedError("35001")

    response = client_with(FakeService(error)).get(URL, params=PARAMS | {"postal_code": "35001"})

    assert response.status_code == 404
    assert response.json() == {"detail": "Postal code not served by Dia"}


# --- Logs of the domain handlers (spec 004 RF-5, RF-7, RF-8, T4) ---


def records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "app.main"]


def test_an_upstream_failure_is_an_error_with_reason_and_search(
    caplog: pytest.LogCaptureFixture,
) -> None:
    error = UpstreamUnavailableError("status 404")

    with caplog.at_level(logging.INFO):
        response = client_with(FakeService(error)).get(URL, params=PARAMS)

    assert "X-Request-ID" in response.headers
    [record] = records(caplog)
    assert record.levelno == logging.ERROR
    assert "reason='status 404'" in record.getMessage()
    assert "postal_code='28001'" in record.getMessage()
    assert "term='leche'" in record.getMessage()


@pytest.mark.parametrize(
    "error",
    [CooldownActiveError("akamai cooldown active"), OutboundRateLimitedError("limit: dia")],
    ids=["cooldown", "limit"],
)
def test_foreseen_degradations_are_warnings(caplog: pytest.LogCaptureFixture, error) -> None:
    with caplog.at_level(logging.INFO):
        response = client_with(FakeService(error)).get(URL, params=PARAMS)

    assert response.status_code == 502
    [record] = records(caplog)
    assert record.levelno == logging.WARNING


@pytest.mark.parametrize(
    "error",
    [PostalCodeNotServedError("35001"), PageOutOfRangeError(3)],
    ids=["not-served", "out-of-range"],
)
def test_404_answers_are_info(caplog: pytest.LogCaptureFixture, error) -> None:
    with caplog.at_level(logging.INFO):
        response = client_with(FakeService(error)).get(URL, params=PARAMS)

    assert response.status_code == 404
    assert "X-Request-ID" in response.headers
    [record] = records(caplog)
    assert record.levelno == logging.INFO


def test_an_akamai_block_is_a_warning_here_the_error_is_the_gates(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # spec-D3: the gate's block is the only ERROR of the episode (review T13).
    error = UpstreamBlockedError("blocked by Akamai (403 HTML)", status_code=403)

    with caplog.at_level(logging.INFO):
        client_with(FakeService(error)).get(URL, params=PARAMS)

    [record] = records(caplog)
    assert record.levelno == logging.WARNING
