"""Redis-backed negative cache: postal codes Dia does not serve (spec 002 RF-5).

Dia answers "no service" and "does not exist" the same way (Fase 0 §4), so one
mark covers both. While it lives, the API answers 404 without asking Dia.
"""

from redis.asyncio import Redis


def not_served_key(postal_code: str) -> str:
    return f"postal_code:not_served:{postal_code}"


class NotServedRepository:
    """Marks and checks the postal codes Dia does not serve."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def is_marked(self, postal_code: str) -> bool:
        return bool(await self._redis.exists(not_served_key(postal_code)))

    async def mark(self, postal_code: str, *, ttl_seconds: int) -> None:
        await self._redis.set(not_served_key(postal_code), "1", ex=ttl_seconds)
