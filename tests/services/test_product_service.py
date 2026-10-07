from datetime import UTC, datetime

import httpx
import pytest
from fakeredis import FakeAsyncRedis

from app.core.config import Settings
from app.exceptions import PageOutOfRangeError, UpstreamBlockedError, UpstreamUnavailableError
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
    scraper: FakeScraper,
    redis: FakeAsyncRedis,
    http_client: httpx.AsyncClient,
    *,
    now: datetime = NOW,
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
        clock=lambda: now,
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
    # Ours, from total_items and our page size, not Dia's pagination (plan-D9).
    assert search.total_pages == 9  # ceil(417 / 50)


async def test_total_pages_is_capped_at_max_page(redis, http_client) -> None:
    scraper = FakeScraper(fixture_body())

    search = (await make_service(scraper, redis, http_client).search(query(page_size=5))).search

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


# --- Hit (T15) ---


async def test_hit_does_not_call_dia_and_keeps_the_original_scraped_at(redis, http_client) -> None:
    first = await make_service(FakeScraper(fixture_body()), redis, http_client).search(query())
    later = datetime(2026, 10, 7, 10, 0, tzinfo=UTC)
    scraper = FakeScraper(fixture_body())

    response = await make_service(scraper, redis, http_client, now=later).search(query())

    assert scraper.calls == []
    assert response.search.scraped_at == first.search.scraped_at == NOW
    assert response.products == first.products


async def test_hit_answers_with_the_postal_code_and_term_of_the_current_request(
    redis, http_client
) -> None:
    await make_service(FakeScraper(fixture_body()), redis, http_client).search(query())
    scraper = FakeScraper(fixture_body())

    response = await make_service(scraper, redis, http_client).search(
        query(postal_code="08001", term="LECHE")
    )

    assert scraper.calls == []
    assert response.search.postal_code == "08001"
    assert response.search.term == "LECHE"


# --- Page out of range (T15) ---


def empty_page(page: int) -> dict:
    body = fixture_body()
    body["search_items"] = []
    body["pagination"]["page_number"] = page
    return body


async def test_an_empty_page_past_the_first_is_out_of_range_and_not_cached(
    redis, http_client
) -> None:
    scraper = FakeScraper(empty_page(2))

    with pytest.raises(PageOutOfRangeError) as exc_info:
        await make_service(scraper, redis, http_client).search(query(page=2))

    assert exc_info.value.page == 2
    assert await redis.keys("*") == []


async def test_a_page_whose_products_are_all_broken_is_not_out_of_range(redis, http_client) -> None:
    # Dia did send products: the page exists (plan-D8).
    body = fixture_body()
    body["search_items"] = [{"broken": True}, {"also": "broken"}]

    response = await make_service(FakeScraper(body), redis, http_client).search(query(page=2))

    assert response.products == []


# --- Errors (T15) ---


@pytest.mark.parametrize(
    "error",
    [UpstreamUnavailableError("down"), UpstreamBlockedError("akamai", status_code=403)],
    ids=["unavailable", "blocked"],
)
async def test_upstream_errors_propagate_and_nothing_is_cached(
    redis, http_client, error: Exception
) -> None:
    with pytest.raises(type(error)):
        await make_service(FakeScraper(error=error), redis, http_client).search(query())

    assert await redis.keys("*") == []


# --- Page sizes below Dia's minimum of 30 (T21) ---


def thirty_items_body() -> dict:
    """The real leche page with 30 distinct products, as Dia sends for any size below 30."""
    body = fixture_body()
    template = body["search_items"][0]
    body["search_items"] = [
        template | {"object_id": f"P{n:02d}", "display_name": f"Product {n:02d}"}
        for n in range(1, 31)
    ]
    body["pagination"] = {"page_number": 1, "page_size": 30, "total_pages": 14}
    body["total_items"] = 418
    return body


async def test_a_small_page_asks_dia_for_the_page_that_contains_it(redis, http_client) -> None:
    scraper = FakeScraper(thirty_items_body())

    response = await make_service(scraper, redis, http_client).search(query(page=2, page_size=5))

    call = scraper.calls[0]
    assert (call["page"], call["page_size"]) == (1, 30)
    assert [p.id for p in response.products] == ["P06", "P07", "P08", "P09", "P10"]
    assert (response.search.page, response.search.page_size) == (2, 5)
    assert response.search.total_pages == 20  # ceil(418 / 5) = 84, capped


async def test_a_small_page_past_the_last_product_is_out_of_range(redis, http_client) -> None:
    body = thirty_items_body()
    body["search_items"] = body["search_items"][:12]  # the last Dia page holds 12 products

    with pytest.raises(PageOutOfRangeError):
        await make_service(FakeScraper(body), redis, http_client).search(query(page=4, page_size=5))
