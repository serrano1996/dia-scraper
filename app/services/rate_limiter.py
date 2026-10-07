"""Sliding-window limit on requests to Dia, shared through Redis (spec 003 RF-4..RF-8).

Akamai was never seen blocking Dia by rate (Fase 0 §5), so these limits are
prudence: they keep a burst of distinct uncached searches, or of new postal
codes, from reaching Dia at once.

A sliding window over a sorted set, as in Alcampo (its spec 008 plan-D3): a
fixed window lets through twice the limit around its boundary, the very burst
to avoid. One member per request, scored with its timestamp (plan-D2).
"""

import time
import uuid
from collections.abc import Callable

from redis.asyncio import Redis

from app.exceptions import OutboundRateLimitedError

Clock = Callable[[], float]


class RateLimiter:
    """Admit at most `limit` requests per `window_seconds` across every instance."""

    def __init__(
        self,
        redis: Redis,
        *,
        key: str,
        name: str,
        limit: int,
        window_seconds: int,
        now: Clock = time.time,
    ) -> None:
        self._redis = redis
        self._key = key
        self._name = name
        self._limit = limit
        self._window = window_seconds
        self._now = now

    @property
    def name(self) -> str:
        return self._name

    @property
    def limit(self) -> int:
        return self._limit

    async def acquire(self) -> None:
        """Take one slot for a request about to be sent, or raise if none is left.

        `limit == 0` disables the limit and never touches Redis (RF-6).
        """
        if self._limit == 0:
            return
        now = self._now()
        member = uuid.uuid4().hex
        async with self._redis.pipeline(transaction=True) as pipe:
            # An entry exactly `window` seconds old has left the window.
            pipe.zremrangebyscore(self._key, "-inf", now - self._window)
            pipe.zadd(self._key, {member: now})
            pipe.zcard(self._key)
            pipe.expire(self._key, self._window)
            _, _, count, _ = await pipe.execute()
        if count > self._limit:
            # A refused request must not consume quota, or a burst of refusals
            # would keep the limit exhausted forever.
            await self._redis.zrem(self._key, member)
            raise OutboundRateLimitedError(f"outbound limit reached: {self._name}")
