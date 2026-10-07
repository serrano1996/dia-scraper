from datetime import UTC, datetime

import httpx
import pytest
from fakeredis import FakeAsyncRedis

from app.core.config import Settings
from app.models.dia import DiaSearchResponse
from app.models.product import ProductQuery
from app.services.product_service import ProductService
from app.services.search_cache import SearchCacheRepository
from tests.fixture_data import load_fixture

NOW = datetime(2026, 10, 7, 9, 30, tzinfo=UTC)


class FakeScraper:
    """Stands in for `DiaSearchScraper`: records calls and returns or raises."""

    def __init__(self, body: dict | None = None, *, error: Exception | None = None) -> None:
        self.body = body
        self.error = error
        self.calls: list[dict] = []

    async def search(
        self, term: str, *, page: int, page_size: int, client: httpx.AsyncClient
    ) -> DiaSearchResponse:
        self.calls.append({"term": term, "page": page, "page_size": page_size, "client": client})
        if self.error is not None:
            raise self.error
        return DiaSearchResponse.model_validate(self.body)


def fixture_body(name: str = "dia_search_leche.json", **overrides: object) -> dict:
    return load_fixture(name) | overrides


@pytest.fixture
def redis() -> FakeAsyncRedis:
    return FakeAsyncRedis()


@pytest.fixture
async def http_client() -> httpx.AsyncClient:
    client = httpx.AsyncClient(base_url="https://dia.test")
    yield client
    await client.aclose()


def make_service(
    scraper: FakeScraper, redis: FakeAsyncRedis, http_client: httpx.AsyncClient
) -> ProductService:
    settings = Settings(
        _env_file=None,
        dia_base_url="https://www.dia.es",
        redis_url="redis://localhost:6379/0",
        cache_ttl_seconds=600,
    )
    return ProductService(
        scraper=scraper,
        cache=SearchCacheRepository(redis),
        http_client=http_client,
        settings=settings,
        clock=lambda: NOW,
    )


def query(**overrides: object) -> ProductQuery:
    fields: dict[str, object] = {"postal_code": "28001", "term": "  Leche "}
    return ProductQuery.model_validate(fields | overrides)


# --- Miss ---


async def test_miss_calls_dia_with_the_trimmed_term_as_typed(redis, http_client) -> None:
    scraper = FakeScraper(fixture_body())

    await make_service(scraper, redis, http_client).search(query(page=2, page_size=30))

    assert len(scraper.calls) == 1
    call = scraper.calls[0]
    assert (call["term"], call["page"], call["page_size"]) == ("Leche", 2, 30)
    assert call["client"] is http_client


async def test_miss_returns_the_mapped_products(redis, http_client) -> None:
    scraper = FakeScraper(fixture_body())

    response = await make_service(scraper, redis, http_client).search(query())

    assert [p.id for p in response.products] == ["504P6", "608P6", "130063P6"]
    assert response.products[0].image_url == (
        "https://www.dia.es/product_images/504P6/504P6_ISO_0_ES.jpg"
    )


async def test_metadata_describes_the_search(redis, http_client) -> None:
    body = fixture_body()
    body["cart"]["postal_code"] = "08001"  # warehouse comes from Dia's answer (spec-D6)
    scraper = FakeScraper(body)

    search = (await make_service(scraper, redis, http_client).search(query())).search

    assert search.postal_code == "28001"
    assert search.term == "Leche"
    assert search.warehouse == "08001"
    assert search.strategy_used == "api"
    assert search.scraped_at == NOW
    assert search.total_results == 417
    assert (search.page, search.page_size) == (1, 50)
    assert search.total_pages == 14


async def test_total_pages_is_capped_at_max_page(redis, http_client) -> None:
    body = fixture_body()
    body["pagination"]["total_pages"] = 84
    scraper = FakeScraper(body)

    search = (await make_service(scraper, redis, http_client).search(query())).search

    assert search.total_pages == 20


async def test_first_page_without_results_is_an_empty_answer(redis, http_client) -> None:
    scraper = FakeScraper(fixture_body("dia_search_no_results.json"))

    response = await make_service(scraper, redis, http_client).search(query(term="xqzwvkjhgf"))

    assert response.products == []
    assert response.search.total_results == 0
    assert response.search.total_pages == 0


@pytest.mark.parametrize("fixture", ["dia_search_leche.json", "dia_search_no_results.json"])
async def test_a_miss_is_cached_with_the_configured_ttl(redis, http_client, fixture) -> None:
    scraper = FakeScraper(fixture_body(fixture))

    response = await make_service(scraper, redis, http_client).search(query())

    cache = SearchCacheRepository(redis)
    cached = await cache.get(postal_code="28041", term="leche", page=1, page_size=50)
    assert cached == response
    assert 0 < await redis.ttl("search:28041:leche:1:50") <= 600
