import pytest
from fakeredis import FakeAsyncRedis

from app.exceptions import OutboundRateLimitedError
from app.services.rate_limiter import RateLimiter


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def redis() -> FakeAsyncRedis:
    return FakeAsyncRedis()


def limiter(
    redis: FakeAsyncRedis, clock: Clock, *, limit: int = 2, key: str = "ratelimit:t"
) -> RateLimiter:
    return RateLimiter(redis, key=key, name="test", limit=limit, window_seconds=60, now=clock)


async def test_admits_up_to_the_limit_then_refuses(redis: FakeAsyncRedis) -> None:
    clock = Clock()
    rate = limiter(redis, clock)

    await rate.acquire()
    await rate.acquire()
    with pytest.raises(OutboundRateLimitedError):
        await rate.acquire()


async def test_a_refusal_does_not_consume_the_quota(redis: FakeAsyncRedis) -> None:
    clock = Clock()
    rate = limiter(redis, clock)
    await rate.acquire()
    await rate.acquire()
    for _ in range(5):
        with pytest.raises(OutboundRateLimitedError):
            await rate.acquire()

    assert await redis.zcard("ratelimit:t") == 2


async def test_the_window_slides(redis: FakeAsyncRedis) -> None:
    clock = Clock()
    rate = limiter(redis, clock)
    await rate.acquire()
    clock.now += 30
    await rate.acquire()

    clock.now += 30  # the first one is exactly 60 s old: out of the window
    await rate.acquire()
    with pytest.raises(OutboundRateLimitedError):
        await rate.acquire()  # the second one is still in


async def test_keys_do_not_mix(redis: FakeAsyncRedis) -> None:
    clock = Clock()
    a = limiter(redis, clock, limit=1, key="ratelimit:a")
    b = limiter(redis, clock, limit=1, key="ratelimit:b")

    await a.acquire()
    await b.acquire()


async def test_two_instances_share_the_count(redis: FakeAsyncRedis) -> None:
    clock = Clock()
    await limiter(redis, clock).acquire()
    await limiter(redis, clock).acquire()

    with pytest.raises(OutboundRateLimitedError):
        await limiter(redis, clock).acquire()


async def test_zero_disables_the_limit_without_touching_redis(redis: FakeAsyncRedis) -> None:
    clock = Clock()
    rate = limiter(redis, clock, limit=0)

    for _ in range(100):
        await rate.acquire()

    assert await redis.keys("*") == []


async def test_the_key_expires_with_the_window(redis: FakeAsyncRedis) -> None:
    await limiter(redis, Clock()).acquire()

    assert 0 < await redis.ttl("ratelimit:t") <= 60


async def test_the_refusal_names_the_limit(redis: FakeAsyncRedis) -> None:
    rate = limiter(redis, Clock(), limit=1)
    await rate.acquire()

    with pytest.raises(OutboundRateLimitedError, match="test"):
        await rate.acquire()
