"""Redis doubles for the degradation tests (spec 008 RNF-3, plan-D8).

Like the real client, building a command or a pipeline succeeds and awaiting it
fails: with `ConnectionError` when Redis is down, `TimeoutError` when it is hung
past its timeout (review T9).
"""

from collections.abc import Awaitable, Callable
from typing import Any

from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

DOWN = RedisConnectionError("Error 111 connecting to redis:6379. Connection refused.")
HUNG = RedisTimeoutError("Timeout reading from redis:6379")


class BrokenPipeline:
    """Queues commands without failing; `execute` fails, as on the real client."""

    def __init__(self, redis: "BrokenRedis") -> None:
        self._redis = redis

    async def __aenter__(self) -> "BrokenPipeline":
        return self

    async def __aexit__(self, *exc: object) -> None:
        pass

    def __getattr__(self, name: str) -> Callable[..., "BrokenPipeline"]:
        return lambda *args, **kwargs: self

    async def execute(self) -> list[Any]:
        self._redis.attempts += 1
        raise self._redis.error


class BrokenRedis:
    """A Redis where every command fails with `error` when awaited.

    `attempts` counts the commands (and pipeline executions) that reached it.
    """

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.attempts = 0

    async def aclose(self) -> None:
        pass

    def pipeline(self, *args: object, **kwargs: object) -> BrokenPipeline:
        return BrokenPipeline(self)

    def __getattr__(self, name: str) -> Callable[..., Awaitable[Any]]:
        async def command(*args: object, **kwargs: object) -> Any:
            self.attempts += 1
            raise self.error

        return command


# Kept as a name for the tests that count attempts.
CountingBrokenRedis = BrokenRedis
