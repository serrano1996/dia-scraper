"""Redis-backed cache for search responses (spec 001 RF-13, RF-14).

One entry per requested postal code, term, page and page size (spec 002
RF-11): Dia exposes no store to group postal codes by.

Without Redis a read is a miss and a write is skipped: the search is served from
Dia, uncached (spec 008 RF-4, plan-D4).
"""

import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.models.product import ProductSearchResponse
from app.services.redis_circuit import RedisCircuitBreaker

logger = logging.getLogger(__name__)


def cache_key(*, postal_code: str, term: str, page: int, page_size: int) -> str:
    """`search:28041:leche:1:50`. The term is trimmed and case-folded: Dia does not
    tell case apart (Fase 0 §1, plan-D5)."""
    return f"search:{postal_code}:{term.strip().casefold()}:{page}:{page_size}"


class SearchCacheRepository:
    """Get/set `ProductSearchResponse` entries in Redis."""

    def __init__(self, redis: Redis, *, circuit: RedisCircuitBreaker | None = None) -> None:
        self._redis = redis
        # Production passes the process's circuit; without one, Redis is always tried.
        self._circuit = circuit if circuit is not None else RedisCircuitBreaker()

    async def get(
        self, *, postal_code: str, term: str, page: int, page_size: int
    ) -> ProductSearchResponse | None:
        """Return the cached response, or `None` on a miss, a corrupted value (plan-D10)
        or without Redis."""
        key = cache_key(postal_code=postal_code, term=term, page=page, page_size=page_size)
        try:
            raw = await self._circuit.call(lambda: self._redis.get(key))
        except RedisError:
            # The circuit already warned (spec 008 spec-D5).
            logger.debug("redis unavailable op=search_cache.get")
            return None
        if raw is None:
            return None
        try:
            return ProductSearchResponse.model_validate_json(raw)
        except ValueError:
            # A miss, but worth knowing: a schema change or a foreign writer (spec 004 RF-15).
            # The key holds the client's term: %r keeps it on one line (RF-17).
            logger.warning("corrupted cache entry treated as a miss key=%r", key)
            return None

    async def set(
        self,
        *,
        postal_code: str,
        term: str,
        page: int,
        page_size: int,
        response: ProductSearchResponse,
        ttl_seconds: int,
    ) -> None:
        key = cache_key(postal_code=postal_code, term=term, page=page, page_size=page_size)
        value = response.model_dump_json()
        try:
            await self._circuit.call(lambda: self._redis.set(key, value, ex=ttl_seconds))
        except RedisError:
            logger.debug("redis unavailable op=search_cache.set")
