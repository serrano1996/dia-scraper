"""Redis marker that pauses every request to Dia after an Akamai block (spec 003 RF-1..RF-3).

Insisting while blocked can only make it last longer, so the marker is global,
shared by every instance behind the same IP. Its duration is fixed: a second
block does not extend it (spec-D2, plan-D3); while it lives no request leaves,
so only requests already in flight can be blocked again.

Without Redis each process keeps a local cooldown with the same rules (spec 008
RF-6, plan-D5). It is checked before Redis, so a cooldown started while Redis was
down still holds once Redis is back without the key (RF-8). Every block also
starts it, so one started in Redis still holds if Redis dies after (review T9).
"""

import logging
import time
from collections.abc import Callable

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.services.redis_circuit import RedisCircuitBreaker

COOLDOWN_KEY = "akamai:cooldown"

logger = logging.getLogger(__name__)


class LocalCooldown:
    """In-process cooldown used while Redis is unavailable."""

    def __init__(self, *, now: Callable[[], float] = time.monotonic) -> None:
        self._now = now
        self._until = 0.0

    def is_active(self) -> bool:
        return self._now() < self._until

    def activate(self, *, seconds: int) -> bool:
        """Start the cooldown; `False` if one was already running, like `SET NX`."""
        if self.is_active():
            return False
        self._until = self._now() + seconds
        return True


class AkamaiCooldown:
    """Read and start the cooldown. One per process, inside the outbound gate."""

    def __init__(
        self,
        redis: Redis,
        *,
        seconds: int,
        circuit: RedisCircuitBreaker | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._redis = redis
        self._seconds = seconds
        # Production passes the process's circuit; without one, Redis is always tried.
        self._circuit = circuit if circuit is not None else RedisCircuitBreaker()
        self._local = LocalCooldown(now=now)

    @property
    def seconds(self) -> int:
        return self._seconds

    async def is_active(self) -> bool:
        if self._local.is_active():
            return True
        try:
            return bool(await self._circuit.call(lambda: self._redis.exists(COOLDOWN_KEY)))
        except RedisError:
            # The circuit already warned (spec 008 spec-D5).
            logger.debug("redis unavailable op=cooldown.is_active")
            return False

    async def activate(self) -> bool:
        """Start the cooldown; `False` if one was already running (it is left as is)."""
        started_locally = self._local.activate(seconds=self._seconds)
        try:
            return bool(
                await self._circuit.call(
                    lambda: self._redis.set(COOLDOWN_KEY, "1", ex=self._seconds, nx=True)
                )
            )
        except RedisError:
            logger.debug("redis unavailable op=cooldown.activate")
            return started_locally
