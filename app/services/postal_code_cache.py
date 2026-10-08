"""Redis-backed negative cache: postal codes Dia does not serve (spec 002 RF-5).

Dia answers "no service" and "does not exist" the same way (Fase 0 §4), so one
mark covers both. While it lives, the API answers 404 without asking Dia.

Without Redis a postal code counts as unmarked, so Dia is asked again, and a mark
is skipped (spec 008 RF-5, spec-D4): rare, and the session PUT that asks is
still limited, locally.
"""

import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.services.redis_circuit import RedisCircuitBreaker

logger = logging.getLogger(__name__)


def not_served_key(postal_code: str) -> str:
    return f"postal_code:not_served:{postal_code}"


class NotServedRepository:
    """Marks and checks the postal codes Dia does not serve."""

    def __init__(self, redis: Redis, *, circuit: RedisCircuitBreaker | None = None) -> None:
        self._redis = redis
        # Production passes the process's circuit; without one, Redis is always tried.
        self._circuit = circuit if circuit is not None else RedisCircuitBreaker()

    async def is_marked(self, postal_code: str) -> bool:
        key = not_served_key(postal_code)
        try:
            return bool(await self._circuit.call(lambda: self._redis.exists(key)))
        except RedisError:
            # The circuit already warned (spec 008 spec-D5).
            logger.debug("redis unavailable op=not_served.is_marked")
            return False

    async def mark(self, postal_code: str, *, ttl_seconds: int) -> None:
        key = not_served_key(postal_code)
        try:
            await self._circuit.call(lambda: self._redis.set(key, "1", ex=ttl_seconds))
        except RedisError:
            logger.debug("redis unavailable op=not_served.mark")
