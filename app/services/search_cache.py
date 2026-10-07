"""Redis-backed cache for search responses (spec 001 RF-13, RF-14).

One entry per postal code, term, page and page size. In spec 001 the postal
code is always Dia's default (plan-D4); spec 002 will pass the resolved one.
"""

from redis.asyncio import Redis

from app.models.product import ProductSearchResponse


def cache_key(*, postal_code: str, term: str, page: int, page_size: int) -> str:
    """`search:28041:leche:1:50`. The term is trimmed and case-folded: Dia does not
    tell case apart (Fase 0 §1, plan-D5)."""
    return f"search:{postal_code}:{term.strip().casefold()}:{page}:{page_size}"


class SearchCacheRepository:
    """Get/set `ProductSearchResponse` entries in Redis."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def get(
        self, *, postal_code: str, term: str, page: int, page_size: int
    ) -> ProductSearchResponse | None:
        """Return the cached response, or `None` on a miss or a corrupted value (plan-D10)."""
        key = cache_key(postal_code=postal_code, term=term, page=page, page_size=page_size)
        raw = await self._redis.get(key)
        if raw is None:
            return None
        try:
            return ProductSearchResponse.model_validate_json(raw)
        except ValueError:
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
        await self._redis.set(key, response.model_dump_json(), ex=ttl_seconds)
