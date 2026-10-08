import logging
from datetime import UTC, datetime

import httpx
import pytest
from fakeredis import FakeAsyncRedis

from app.core.config import Settings
from app.exceptions import (
    PageOutOfRangeError,
    PostalCodeNotServedError,
    UpstreamBlockedError,
    UpstreamUnavailableError,
)
from app.models.dia import DiaSearchResponse
from app.models.product import ProductQuery
from app.services.postal_code_cache import NotServedRepository
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


class FakeSession:
    def __init__(self, postal_code: str) -> None:
        self.postal_code = postal_code
        self.client = httpx.AsyncClient(base_url="https://dia.test")


class FakeSessions:
    """Stands in for `PostalCodeSessions`: one fake session per postal code."""

    def __init__(self) -> None:
        self.handed_out: dict[str, FakeSession] = {}
        self.requested: list[str] = []
        self.discarded: list[tuple[str, FakeSession]] = []
        self.discard_reasons: list[str] = []
        self.errors: dict[str, Exception] = {}

    async def get(self, postal_code: str) -> FakeSession:
        self.requested.append(postal_code)
        if postal_code in self.errors:
            raise self.errors[postal_code]
        return self.handed_out.setdefault(postal_code, FakeSession(postal_code))

    def discard(self, postal_code: str, session: FakeSession, reason: str = "discarded") -> None:
        self.discarded.append((postal_code, session))
        self.discard_reasons.append(reason)
        if self.handed_out.get(postal_code) is session:
            del self.handed_out[postal_code]

    async def aclose(self) -> None:
        for session in self.handed_out.values():
            await session.client.aclose()


@pytest.fixture
async def sessions() -> FakeSessions:
    pool = FakeSessions()
    yield pool
    await pool.aclose()


def make_service(
    scraper: FakeScraper,
    redis: FakeAsyncRedis,
    sessions: FakeSessions,
    *,
    now: datetime = NOW,
) -> ProductService:
    settings = Settings(
        _env_file=None,
        dia_base_url="https://www.dia.es",
        redis_url="redis://localhost:6379/0",
        cache_ttl_seconds=600,
        postal_code_negative_cache_ttl_seconds=900,
    )
    return ProductService(
        scraper=scraper,
        cache=SearchCacheRepository(redis),
        not_served=NotServedRepository(redis),
        sessions=sessions,
        settings=settings,
        clock=lambda: now,
    )


def query(**overrides: object) -> ProductQuery:
    # Dia's default postal code: the one the real fixtures carry in `cart`.
    fields: dict[str, object] = {"postal_code": "28041", "term": "  Leche "}
    return ProductQuery.model_validate(fields | overrides)


# --- Miss ---


async def test_miss_calls_dia_with_the_trimmed_term_as_typed(redis, sessions) -> None:
    scraper = FakeScraper(fixture_body())

    await make_service(scraper, redis, sessions).search(query(page=2, page_size=30))

    assert len(scraper.calls) == 1
    call = scraper.calls[0]
    assert (call["term"], call["page"], call["page_size"]) == ("Leche", 2, 30)
    assert call["client"] is sessions.handed_out["28041"].client


async def test_miss_returns_the_mapped_products(redis, sessions) -> None:
    scraper = FakeScraper(fixture_body())

    response = await make_service(scraper, redis, sessions).search(query())

    assert [p.id for p in response.products] == ["504P6", "608P6", "130063P6"]
    assert response.products[0].image_url == (
        "https://www.dia.es/product_images/504P6/504P6_ISO_0_ES.jpg"
    )


async def test_metadata_describes_the_search(redis, sessions) -> None:
    scraper = FakeScraper(fixture_body())

    search = (await make_service(scraper, redis, sessions).search(query())).search

    assert search.postal_code == "28041"
    assert search.term == "Leche"
    assert search.warehouse == "28041"  # from Dia's answer (spec 001 spec-D6)
    assert search.strategy_used == "api"
    assert search.scraped_at == NOW
    assert search.total_results == 417
    assert (search.page, search.page_size) == (1, 50)
    # Ours, from total_items and our page size, not Dia's pagination (plan-D9).
    assert search.total_pages == 9  # ceil(417 / 50)


async def test_total_pages_is_capped_at_max_page(redis, sessions) -> None:
    scraper = FakeScraper(fixture_body())

    search = (await make_service(scraper, redis, sessions).search(query(page_size=5))).search

    assert search.total_pages == 20


async def test_first_page_without_results_is_an_empty_answer(redis, sessions) -> None:
    scraper = FakeScraper(fixture_body("dia_search_no_results.json"))

    response = await make_service(scraper, redis, sessions).search(query(term="xqzwvkjhgf"))

    assert response.products == []
    assert response.search.total_results == 0
    assert response.search.total_pages == 0


@pytest.mark.parametrize("fixture", ["dia_search_leche.json", "dia_search_no_results.json"])
async def test_a_miss_is_cached_with_the_configured_ttl(redis, sessions, fixture) -> None:
    scraper = FakeScraper(fixture_body(fixture))

    response = await make_service(scraper, redis, sessions).search(query())

    cache = SearchCacheRepository(redis)
    cached = await cache.get(postal_code="28041", term="leche", page=1, page_size=50)
    assert cached == response
    assert 0 < await redis.ttl("search:28041:leche:1:50") <= 600


# --- Hit (T15) ---


async def test_hit_does_not_call_dia_and_keeps_the_original_scraped_at(redis, sessions) -> None:
    first = await make_service(FakeScraper(fixture_body()), redis, sessions).search(query())
    later = datetime(2026, 10, 7, 10, 0, tzinfo=UTC)
    scraper = FakeScraper(fixture_body())

    response = await make_service(scraper, redis, sessions, now=later).search(query())

    assert scraper.calls == []
    assert response.search.scraped_at == first.search.scraped_at == NOW
    assert response.products == first.products


async def test_hit_answers_with_the_term_of_the_current_request(redis, sessions) -> None:
    await make_service(FakeScraper(fixture_body()), redis, sessions).search(query())
    scraper = FakeScraper(fixture_body())

    response = await make_service(scraper, redis, sessions).search(query(term="LECHE"))

    assert scraper.calls == []
    assert response.search.postal_code == "28041"
    assert response.search.term == "LECHE"


# --- Page out of range (T15) ---


def empty_page(page: int) -> dict:
    body = fixture_body()
    body["search_items"] = []
    body["pagination"]["page_number"] = page
    return body


async def test_an_empty_page_past_the_first_is_out_of_range_and_not_cached(redis, sessions) -> None:
    scraper = FakeScraper(empty_page(2))

    with pytest.raises(PageOutOfRangeError) as exc_info:
        await make_service(scraper, redis, sessions).search(query(page=2))

    assert exc_info.value.page == 2
    assert await redis.keys("*") == []


async def test_a_page_whose_products_are_all_broken_is_not_out_of_range(redis, sessions) -> None:
    # Dia did send products: the page exists (plan-D8).
    body = fixture_body()
    body["search_items"] = [{"broken": True}, {"also": "broken"}]

    response = await make_service(FakeScraper(body), redis, sessions).search(query(page=2))

    assert response.products == []


# --- Errors (T15) ---


@pytest.mark.parametrize(
    "error",
    [UpstreamUnavailableError("down"), UpstreamBlockedError("akamai", status_code=403)],
    ids=["unavailable", "blocked"],
)
async def test_upstream_errors_propagate_and_nothing_is_cached(
    redis, sessions, error: Exception
) -> None:
    with pytest.raises(type(error)):
        await make_service(FakeScraper(error=error), redis, sessions).search(query())

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


async def test_a_small_page_asks_dia_for_the_page_that_contains_it(redis, sessions) -> None:
    scraper = FakeScraper(thirty_items_body())

    response = await make_service(scraper, redis, sessions).search(query(page=2, page_size=5))

    call = scraper.calls[0]
    assert (call["page"], call["page_size"]) == (1, 30)
    assert [p.id for p in response.products] == ["P06", "P07", "P08", "P09", "P10"]
    assert (response.search.page, response.search.page_size) == (2, 5)
    assert response.search.total_pages == 20  # ceil(418 / 5) = 84, capped


async def test_a_small_page_past_the_last_product_is_out_of_range(redis, sessions) -> None:
    body = thirty_items_body()
    body["search_items"] = body["search_items"][:12]  # the last Dia page holds 12 products

    with pytest.raises(PageOutOfRangeError):
        await make_service(FakeScraper(body), redis, sessions).search(query(page=4, page_size=5))


# --- The session of the requested postal code (spec 002, T8) ---


def body_for(postal_code: str) -> dict:
    body = fixture_body()
    body["cart"]["postal_code"] = postal_code
    return body


async def test_the_search_uses_the_session_of_the_requested_postal_code(redis, sessions) -> None:
    scraper = FakeScraper(body_for("08001"))

    response = await make_service(scraper, redis, sessions).search(query(postal_code="08001"))

    assert sessions.requested == ["08001"]
    assert scraper.calls[0]["client"] is sessions.handed_out["08001"].client
    assert response.search.postal_code == "08001"
    assert await redis.keys("*") == [b"search:08001:leche:1:50"]


async def test_postal_codes_do_not_share_cache_entries(redis, sessions) -> None:
    await make_service(FakeScraper(body_for("28041")), redis, sessions).search(query())
    scraper = FakeScraper(body_for("08001"))

    await make_service(scraper, redis, sessions).search(query(postal_code="08001"))

    assert len(scraper.calls) == 1
    assert sorted(await redis.keys("*")) == [
        b"search:08001:leche:1:50",
        b"search:28041:leche:1:50",
    ]


async def test_a_hit_asks_the_pool_for_no_session(redis, sessions) -> None:
    await make_service(FakeScraper(body_for("08001")), redis, sessions).search(
        query(postal_code="08001")
    )

    await make_service(FakeScraper(body_for("08001")), redis, sessions).search(
        query(postal_code="08001")
    )

    assert sessions.requested == ["08001"]


# --- Never one postal code for another (spec 002 RF-8..RF-10, T9) ---


class SequenceScraper(FakeScraper):
    """Answers each call with the next body."""

    def __init__(self, *bodies: dict) -> None:
        super().__init__()
        self.bodies = list(bodies)

    async def search(self, term, *, page, page_size, client):
        self.body = self.bodies[len(self.calls)]
        return await super().search(term, page=page, page_size=page_size, client=client)


async def test_a_session_answering_for_another_postal_code_is_replaced_once(
    redis, sessions
) -> None:
    # The session expired: Dia answered from a fresh one in its default 28041.
    scraper = SequenceScraper(body_for("28041"), body_for("08001"))

    response = await make_service(scraper, redis, sessions).search(query(postal_code="08001"))

    assert len(scraper.calls) == 2
    assert [cp for cp, _ in sessions.discarded] == ["08001"]
    assert sessions.discard_reasons == ["mismatch"]  # spec 004 RF-16
    stale = sessions.discarded[0][1]
    assert scraper.calls[0]["client"] is stale.client
    assert scraper.calls[1]["client"] is not stale.client
    assert response.search.warehouse == "08001"
    assert await redis.keys("*") == [b"search:08001:leche:1:50"]


async def test_two_mismatches_answer_502_warn_and_cache_nothing(
    redis, sessions, caplog: pytest.LogCaptureFixture
) -> None:
    scraper = SequenceScraper(body_for("28041"), body_for("28041"))

    with (
        caplog.at_level(logging.WARNING, logger="app.services.product_service"),
        pytest.raises(UpstreamUnavailableError),
    ):
        await make_service(scraper, redis, sessions).search(query(postal_code="08001"))

    assert len(scraper.calls) == 2
    assert "expected=08001" in caplog.text
    assert "got=28041" in caplog.text
    assert await redis.keys("*") == []


async def test_warehouse_is_always_the_requested_postal_code(redis, sessions) -> None:
    scraper = FakeScraper(body_for("41001"))

    response = await make_service(scraper, redis, sessions).search(query(postal_code="41001"))

    assert response.search.warehouse == response.search.postal_code == "41001"
    assert sessions.discarded == []


# --- Postal codes Dia does not serve (spec 002 RF-4..RF-6, T10) ---


async def test_a_postal_code_dia_does_not_serve_is_remembered(redis, sessions) -> None:
    sessions.errors["35001"] = PostalCodeNotServedError("35001")
    scraper = FakeScraper(fixture_body())

    with pytest.raises(PostalCodeNotServedError):
        await make_service(scraper, redis, sessions).search(query(postal_code="35001"))

    assert scraper.calls == []
    assert await NotServedRepository(redis).is_marked("35001")
    assert 0 < await redis.ttl("postal_code:not_served:35001") <= 900


async def test_a_remembered_postal_code_is_answered_without_asking_dia(redis, sessions) -> None:
    await NotServedRepository(redis).mark("35001", ttl_seconds=900)

    with pytest.raises(PostalCodeNotServedError) as exc_info:
        await make_service(FakeScraper(fixture_body()), redis, sessions).search(
            query(postal_code="35001")
        )

    assert exc_info.value.postal_code == "35001"
    assert sessions.requested == []


async def test_a_failed_put_does_not_mark_the_postal_code(redis, sessions) -> None:
    sessions.errors["08001"] = UpstreamUnavailableError("down")

    with pytest.raises(UpstreamUnavailableError):
        await make_service(FakeScraper(fixture_body()), redis, sessions).search(
            query(postal_code="08001")
        )

    assert await redis.keys("*") == []
