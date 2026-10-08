"""Circuit breaker in front of Redis (spec 008 RF-2, RF-3, RF-9; plan-D2, plan-D3).

Taken from Alcampo (its spec 007 RF-18): with timeouts alone, a search with Redis
down waited one timeout per Redis operation, ~9 s in its manual check. After one
failure the circuit opens and, for `open_seconds`, every operation fails at once
with `RedisCircuitOpenError`, so each repository goes straight to its fallback.

`RedisCircuitOpenError` is a `RedisError`: fallbacks catch that one type.

After the period the next operation probes Redis: success closes the circuit,
failure opens it again. Concurrent operations may probe at the same time; they
are few and each is bounded by the Redis timeout (RF-1).

The circuit is the only one that warns (spec-D5): a WARNING when it opens, an
INFO when it closes. Logs carry the error type, never its message, which may
hold the Redis URL with its password.
"""

import logging
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

from redis.exceptions import RedisError

T = TypeVar("T")

logger = logging.getLogger(__name__)


class RedisCircuitOpenError(RedisError):
    """Redis was skipped because the circuit is open: no request was made."""


class RedisCircuitBreaker:
    """One per process, built in the `lifespan` and shared by everything using Redis.

    `open_seconds == 0` (the default, for objects built without one) never opens,
    but still warns on every failure: otherwise a dead Redis would leave no trace.
    """

    def __init__(self, *, open_seconds: int = 0, now: Callable[[], float] = time.monotonic) -> None:
        self._open_seconds = open_seconds
        self._now = now
        self._open_until: float | None = None

    async def call(self, operation: Callable[[], Awaitable[T]]) -> T:
        """Run `operation` unless the circuit is open; only `RedisError` opens it."""
        if self._open_until is not None and self._now() < self._open_until:
            raise RedisCircuitOpenError("redis circuit open")
        probing = self._open_until is not None
        try:
            result = await operation()
        except RedisError as exc:
            self._failed(exc)
            raise
        if probing:
            self._open_until = None
            logger.info("redis circuit closed")
        return result

    def _failed(self, exc: RedisError) -> None:
        error = type(exc).__name__
        if self._open_seconds == 0:
            logger.warning("redis operation failed circuit=disabled error=%s", error)
            return
        self._open_until = self._now() + self._open_seconds
        logger.warning("redis circuit open seconds=%d error=%s", self._open_seconds, error)
