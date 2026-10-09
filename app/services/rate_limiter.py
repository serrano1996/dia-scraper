"""Sliding-window limit on requests to Dia, shared through Redis (spec 003 RF-4..RF-8).

Akamai was never seen blocking Dia by rate (Fase 0 §5), so these limits are
prudence: they keep a burst of distinct uncached searches, or of new postal
codes, from reaching Dia at once.

A sliding window over a sorted set, as in Alcampo (its spec 008 plan-D3): a
fixed window lets through twice the limit around its boundary, the very burst
to avoid. One member per request, scored with its timestamp (plan-D2).

Without Redis each limiter falls back to a local window with the same limit and
window (spec 008 RF-7, plan-D6): coordination between instances is lost, but no
process can burst. Each limiter is built once per process (`lifespan`), so it
owns its local window.
"""

import logging
import time
import uuid
from collections import deque
from collections.abc import Callable

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.exceptions import OutboundRateLimitedError
from app.services.redis_circuit import RedisCircuitBreaker

Clock = Callable[[], float]

logger = logging.getLogger(__name__)


class LocalRateLimiter:
    """In-process sliding window used while Redis is unavailable."""

    def __init__(self, *, name: str, limit: int, window_seconds: int) -> None:
        self._name = name
        self._limit = limit
        self._window = window_seconds
        self._sent: deque[tuple[float, str]] = deque()

    def acquire(self, *, now: float, slot: str) -> None:
        # Same boundary as ZREMRANGEBYSCORE -inf now-window: an entry exactly
        # `window` seconds old has left the window.
        while self._sent and self._sent[0][0] <= now - self._window:
            self._sent.popleft()
        if len(self._sent) >= self._limit:
            raise OutboundRateLimitedError(f"outbound limit reached: {self._name}")
        self._sent.append((now, slot))

    def release(self, slot: str) -> bool:
        """Give `slot` back; `False` if this window does not hold it."""
        for entry in self._sent:
            if entry[1] == slot:
                self._sent.remove(entry)
                return True
        return False


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
        circuit: RedisCircuitBreaker | None = None,
    ) -> None:
        self._redis = redis
        self._key = key
        self._name = name
        self._limit = limit
        self._window = window_seconds
        self._now = now
        # Production passes the process's circuit; without one, Redis is always tried.
        self._circuit = circuit if circuit is not None else RedisCircuitBreaker()
        self._local = LocalRateLimiter(name=name, limit=limit, window_seconds=window_seconds)

    @property
    def name(self) -> str:
        return self._name

    @property
    def limit(self) -> int:
        return self._limit

    async def acquire(self) -> str:
        """Take one slot for a request about to be sent, or raise if none is left.

        Returns the slot, to give it back with `release` if the request never
        leaves. `limit == 0` disables the limit, never touches Redis and
        returns `""` (RF-6).

        Accepted limits (spec 003 review): scores use each process's clock, so
        instances with skewed clocks count slightly off (NTP keeps it to
        milliseconds against windows of 60-600 s); and a refusal is undone with
        a separate ZREM, so a concurrent acquirer can be refused transiently.
        Doing it atomically with Redis' own clock needs a Lua script, which
        fakeredis only runs with an extra dependency (RNF-1).
        """
        if self._limit == 0:
            return ""
        now = self._now()
        member = uuid.uuid4().hex
        try:
            await self._circuit.call(lambda: self._acquire_in_redis(now, member))
        except RedisError:
            # The circuit already warned (spec 008 spec-D5).
            logger.debug("redis unavailable op=rate_limit.acquire limit=%s", self._name)
            self._local.acquire(now=now, slot=member)
        return member

    async def release(self, slot: str) -> None:
        """Give back a slot taken by `acquire` (no-op for `""`).

        A slot taken locally goes back locally; one taken in Redis is left to
        expire with the window if Redis is gone meanwhile (spec 008 plan-D6).
        """
        if not slot or self._local.release(slot):
            return
        try:
            await self._circuit.call(lambda: self._redis.zrem(self._key, slot))
        except RedisError:
            logger.debug("redis unavailable op=rate_limit.release limit=%s", self._name)

    async def _acquire_in_redis(self, now: float, member: str) -> None:
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
