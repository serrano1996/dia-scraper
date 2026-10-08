from datetime import UTC, datetime

import pytest
from fakeredis import FakeAsyncRedis

from app.models.product import Product, ProductSearchResponse, SearchMetadata
from app.services.search_cache import SearchCacheRepository, cache_key


def make_response(term: str = "leche") -> ProductSearchResponse:
    return ProductSearchResponse(
        search=SearchMetadata(
            postal_code="28001",
            term=term,
            warehouse="28041",
            strategy_used="api",
            scraped_at=datetime(2026, 10, 7, 9, 0, tzinfo=UTC),
            total_results=417,
            page=1,
            page_size=50,
            total_pages=9,
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


@pytest.fixture
def redis() -> FakeAsyncRedis:
    return FakeAsyncRedis()


@pytest.fixture
def cache(redis: FakeAsyncRedis) -> SearchCacheRepository:
    return SearchCacheRepository(redis)


KEY = {"postal_code": "28041", "term": "leche", "page": 1, "page_size": 50}


def test_cache_key_has_postal_code_term_page_and_page_size() -> None:
    assert cache_key(**KEY) == "search:28041:leche:1:50"


@pytest.mark.parametrize("term", ["LECHE", " leche ", "Leche"])
def test_cache_key_ignores_case_and_surrounding_spaces(term: str) -> None:
    # Dia does not tell case apart (Fase 0 §1, plan-D5).
    assert cache_key(**(KEY | {"term": term})) == "search:28041:leche:1:50"


async def test_get_without_an_entry_is_a_miss(cache: SearchCacheRepository) -> None:
    assert await cache.get(**KEY) is None


async def test_set_then_get_returns_the_same_response(cache: SearchCacheRepository) -> None:
    response = make_response()

    await cache.set(**KEY, response=response, ttl_seconds=3600)

    assert await cache.get(**KEY) == response


async def test_pages_and_page_sizes_are_cached_apart(cache: SearchCacheRepository) -> None:
    await cache.set(**KEY, response=make_response(), ttl_seconds=3600)

    assert await cache.get(**(KEY | {"page": 2})) is None
    assert await cache.get(**(KEY | {"page_size": 30})) is None


async def test_set_applies_the_ttl(cache: SearchCacheRepository, redis: FakeAsyncRedis) -> None:
    await cache.set(**KEY, response=make_response(), ttl_seconds=120)

    assert 0 < await redis.ttl("search:28041:leche:1:50") <= 120


@pytest.mark.parametrize("stored", [b"{no json", b'{"search": {}, "products": []}'])
async def test_corrupted_entry_is_a_miss(
    cache: SearchCacheRepository, redis: FakeAsyncRedis, stored: bytes
) -> None:
    await redis.set("search:28041:leche:1:50", stored)

    assert await cache.get(**KEY) is None


async def test_a_corrupted_entry_is_a_warning_with_its_key(
    cache: SearchCacheRepository, redis: FakeAsyncRedis, caplog: pytest.LogCaptureFixture
) -> None:
    # spec 004 RF-15, T9.
    await redis.set("search:28041:leche:1:50", b"{no json")

    with caplog.at_level("INFO", logger="app.services.search_cache"):
        assert await cache.get(**KEY) is None

    [record] = [r for r in caplog.records if r.name == "app.services.search_cache"]
    assert record.levelname == "WARNING"
    assert "key='search:28041:leche:1:50'" in record.getMessage()


async def test_a_plain_miss_logs_nothing(
    cache: SearchCacheRepository, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("INFO", logger="app.services.search_cache"):
        assert await cache.get(**KEY) is None

    assert not [r for r in caplog.records if r.name == "app.services.search_cache"]
