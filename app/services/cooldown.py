"""Redis marker that pauses every request to Dia after an Akamai block (spec 003 RF-1..RF-3).

Insisting while blocked can only make it last longer, so the marker is global,
shared by every instance behind the same IP. Its duration is fixed: a second
block does not extend it (spec-D2, plan-D3); while it lives no request leaves,
so only requests already in flight can be blocked again.
"""

from redis.asyncio import Redis

COOLDOWN_KEY = "akamai:cooldown"


class AkamaiCooldown:
    """Read and start the cooldown."""

    def __init__(self, redis: Redis, *, seconds: int) -> None:
        self._redis = redis
        self._seconds = seconds

    @property
    def seconds(self) -> int:
        return self._seconds

    async def is_active(self) -> bool:
        return bool(await self._redis.exists(COOLDOWN_KEY))

    async def activate(self) -> bool:
        """Start the cooldown; `False` if one was already running (it is left as is)."""
        return bool(await self._redis.set(COOLDOWN_KEY, "1", ex=self._seconds, nx=True))
