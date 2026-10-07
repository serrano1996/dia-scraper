"""Product search: cache, then Dia, then cache again (spec 001 §4)."""

import math
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

import httpx

from app.core.config import Settings
from app.exceptions import PageOutOfRangeError
from app.mappers.product_mapper import map_search
from app.models.dia import DiaSearchResponse
from app.models.product import MAX_PAGE, ProductQuery, ProductSearchResponse, SearchMetadata
from app.services.pagination import dia_window
from app.services.postal_code_sessions import PostalCodeSession
from app.services.search_cache import SearchCacheRepository

STRATEGY = "api"


class SearchScraper(Protocol):
    """What the service needs from `DiaSearchScraper`."""

    async def search(
        self, term: str, *, page: int, page_size: int, client: httpx.AsyncClient
    ) -> DiaSearchResponse: ...


class SessionPool(Protocol):
    """What the service needs from `PostalCodeSessions`."""

    async def get(self, postal_code: str) -> PostalCodeSession: ...
    def discard(self, postal_code: str, session: PostalCodeSession) -> None: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ProductService:
    """Orchestrates cache, scraper and mapper for `GET /api/v1/products`."""

    def __init__(
        self,
        *,
        scraper: SearchScraper,
        cache: SearchCacheRepository,
        sessions: SessionPool,
        settings: Settings,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._scraper = scraper
        self._cache = cache
        self._sessions = sessions
        self._base_url = settings.dia_base_url
        self._ttl_seconds = settings.cache_ttl_seconds
        self._clock = clock

    async def search(self, query: ProductQuery) -> ProductSearchResponse:
        """One page of results for `query.term` (RF-1, RF-7, RF-12..RF-16).

        Raises `PageOutOfRangeError` past the last page, and lets upstream errors
        through; neither is cached (RF-15).
        """
        cached = await self._cache.get(
            # One entry per requested postal code (spec 002 RF-11, spec-D8).
            postal_code=query.postal_code,
            term=query.term,
            page=query.page,
            page_size=query.page_size,
        )
        if cached is not None:
            # The entry is shared by every casing of the term: answer with the
            # current request's, keep the original scraped_at (spec 001 RF-16).
            search = cached.search.model_copy(update={"term": query.term})
            return cached.model_copy(update={"search": search})

        # Dia never serves fewer than 30 products: ask for the Dia page that
        # holds the requested one and keep only that slice (spec-D9, plan-D17).
        window = dia_window(query.page, query.page_size)
        # Dia takes the postal code from the session (spec 002 RF-1, RF-2).
        session = await self._sessions.get(query.postal_code)
        raw = await self._scraper.search(
            query.term, page=window.page, page_size=window.page_size, client=session.client
        )
        items = raw.search_items[window.offset : window.offset + query.page_size]
        # Raw items, not mapped products: a page whose products are all broken
        # still exists (plan-D8).
        if query.page > 1 and not items:
            raise PageOutOfRangeError(query.page)
        page_raw = raw.model_copy(update={"search_items": items})
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
                # Ours: Dia counts its own pages of at least 30 (plan-D9).
                total_pages=min(math.ceil(raw.total_items / query.page_size), MAX_PAGE),
            ),
            products=map_search(page_raw, base_url=self._base_url),
        )
        await self._cache.set(
            postal_code=query.postal_code,
            term=query.term,
            page=query.page,
            page_size=query.page_size,
            response=response,
            ttl_seconds=self._ttl_seconds,
        )
        return response
