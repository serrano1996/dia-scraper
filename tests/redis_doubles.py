"""Redis doubles for the degradation tests (spec 008 RNF-3, plan-D8), as in Alcampo.

Every command fails when it is attempted, pipelines included, the way the real
client fails when Redis is down (`ConnectionError`) or hung past its timeout
(`TimeoutError`).
"""

from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

DOWN = RedisConnectionError("Error 111 connecting to redis:6379. Connection refused.")
HUNG = RedisTimeoutError("Timeout reading from redis:6379")


class BrokenRedis:
    """A Redis where every command fails with `error`."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def aclose(self) -> None:
        pass

    def __getattr__(self, name: str) -> object:
        raise self.error


class CountingBrokenRedis(BrokenRedis):
    """A broken Redis that counts every command attempted (RF-2: open means untouched)."""

    def __init__(self, error: Exception) -> None:
        super().__init__(error)
        self.attempts = 0

    def __getattr__(self, name: str) -> object:
        self.attempts += 1
        raise self.error
