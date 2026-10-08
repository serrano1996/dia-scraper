"""Product search: cache, then Dia, then cache again (spec 001 §4)."""

import logging
import math
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

import httpx

from app.core.config import Settings
from app.exceptions import PageOutOfRangeError, PostalCodeNotServedError, UpstreamUnavailableError
from app.mappers.product_mapper import map_search
from app.models.dia import DiaSearchResponse
from app.models.product import MAX_PAGE, ProductQuery, ProductSearchResponse, SearchMetadata
from app.services.pagination import dia_window
from app.services.postal_code_cache import NotServedRepository
from app.services.postal_code_sessions import PostalCodeSession
from app.services.search_cache import SearchCacheRepository

STRATEGY = "api"

logger = logging.getLogger(__name__)

# A session that answers for another postal code is replaced once (spec-D7).
SESSION_TRIES = 2


class SearchScraper(Protocol):
    """What the service needs from `DiaSearchScraper`."""

    async def search(
        self, term: str, *, page: int, page_size: int, client: httpx.AsyncClient
    ) -> DiaSearchResponse: ...


class SessionPool(Protocol):
    """What the service needs from `PostalCodeSessions`."""

    async def get(self, postal_code: str) -> PostalCodeSession: ...
    def discard(self, postal_code: str, session: PostalCodeSession, reason: str = ...) -> None: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ProductService:
    """Orchestrates cache, scraper and mapper for `GET /api/v1/products`."""

    def __init__(
        self,
        *,
        scraper: SearchScraper,
        cache: SearchCacheRepository,
        not_served: NotServedRepository,
        sessions: SessionPool,
        settings: Settings,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._scraper = scraper
        self._cache = cache
        self._not_served = not_served
        self._sessions = sessions
        self._base_url = settings.dia_base_url
        self._ttl_seconds = settings.cache_ttl_seconds
        self._not_served_ttl_seconds = settings.postal_code_negative_cache_ttl_seconds
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
        raw = await self._search_dia(query, window.page, window.page_size)
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

    async def _search_dia(
        self, query: ProductQuery, page: int, page_size: int
    ) -> DiaSearchResponse:
        """Search Dia with the session of `query.postal_code`, and check Dia used it.

        Dia takes the postal code from the session (spec 002 RF-1, RF-2), and
        every answer says which one it used (`cart.postal_code`). A session that
        expired answers from Dia's default instead: it is replaced and the search
        repeated once. Never one postal code labelled as another (RF-8, RF-9).
        """
        answered = ""
        for _ in range(SESSION_TRIES):
            session = await self._session_for(query.postal_code)
            raw = await self._scraper.search(
                query.term, page=page, page_size=page_size, client=session.client
            )
            answered = raw.cart.postal_code
            if answered == query.postal_code:
                return raw
            self._sessions.discard(query.postal_code, session, reason="mismatch")
        # %r: `answered` comes from Dia and could carry control characters (review T13).
        logger.warning("postal code mismatch expected=%r got=%r", query.postal_code, answered)
        raise UpstreamUnavailableError("Dia answered for another postal code")

    async def _session_for(self, postal_code: str) -> PostalCodeSession:
        """The pool's session, unless Dia is known not to serve `postal_code` (RF-4, RF-5).

        Only Dia's "no service" answer is remembered; an upstream failure is not (RF-6).
        """
        if await self._not_served.is_marked(postal_code):
            raise PostalCodeNotServedError(postal_code)
        try:
            return await self._sessions.get(postal_code)
        except PostalCodeNotServedError:
            await self._not_served.mark(postal_code, ttl_seconds=self._not_served_ttl_seconds)
            raise
