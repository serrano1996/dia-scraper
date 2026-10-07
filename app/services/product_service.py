"""Product search: cache, then Dia, then cache again (spec 001 §4)."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

import httpx

from app.core.config import Settings
from app.mappers.product_mapper import map_search
from app.models.dia import DiaSearchResponse
from app.models.product import MAX_PAGE, ProductQuery, ProductSearchResponse, SearchMetadata
from app.scrapers.dia_search import DEFAULT_POSTAL_CODE
from app.services.search_cache import SearchCacheRepository

STRATEGY = "api"


class SearchScraper(Protocol):
    """What the service needs from `DiaSearchScraper`."""

    async def search(
        self, term: str, *, page: int, page_size: int, client: httpx.AsyncClient
    ) -> DiaSearchResponse: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ProductService:
    """Orchestrates cache, scraper and mapper for `GET /api/v1/products`."""

    def __init__(
        self,
        *,
        scraper: SearchScraper,
        cache: SearchCacheRepository,
        http_client: httpx.AsyncClient,
        settings: Settings,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._scraper = scraper
        self._cache = cache
        self._http_client = http_client
        self._base_url = settings.dia_base_url
        self._ttl_seconds = settings.cache_ttl_seconds
        self._clock = clock

    async def search(self, query: ProductQuery) -> ProductSearchResponse:
        """One page of results for `query.term` (RF-1, RF-12, RF-14)."""
        raw = await self._scraper.search(
            query.term, page=query.page, page_size=query.page_size, client=self._http_client
        )
        response = ProductSearchResponse(
            search=SearchMetadata(
                postal_code=query.postal_code,
                term=query.term,
                warehouse=raw.cart.postal_code,
                strategy_used=STRATEGY,
                scraped_at=self._clock(),
                total_results=raw.total_items,
                page=query.page,
                page_size=query.page_size,
                total_pages=min(raw.pagination.total_pages, MAX_PAGE),
            ),
            products=map_search(raw, base_url=self._base_url),
        )
        await self._cache.set(
            # Every search uses Dia's default postal code in spec 001 (spec-D2, plan-D4).
            postal_code=DEFAULT_POSTAL_CODE,
            term=query.term,
            page=query.page,
            page_size=query.page_size,
            response=response,
            ttl_seconds=self._ttl_seconds,
        )
        return response
