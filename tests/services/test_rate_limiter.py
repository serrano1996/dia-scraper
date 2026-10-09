import logging

import pytest
from fakeredis import FakeAsyncRedis
from redis.exceptions import RedisError

from app.exceptions import OutboundRateLimitedError
from app.services.rate_limiter import LocalRateLimiter, RateLimiter
from app.services.redis_circuit import RedisCircuitBreaker
from tests.redis_doubles import DOWN, HUNG, BrokenRedis, CountingBrokenRedis


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


# --- Without Redis (spec 008 RF-7, plan-D6) ---


def broken_limiter(redis: object, clock: Clock, *, limit: int = 2) -> RateLimiter:
    return RateLimiter(
        redis,
        key="ratelimit:t",
        name="test",
        limit=limit,
        window_seconds=60,
        now=clock,
        circuit=RedisCircuitBreaker(open_seconds=10, now=clock),
    )


def test_the_local_window_has_the_same_boundary_as_redis() -> None:
    # Same as ZREMRANGEBYSCORE -inf now-window: exactly `window` old has left.
    local = LocalRateLimiter(name="test", limit=1, window_seconds=60)
    local.acquire(now=1000.0, slot="a")

    with pytest.raises(OutboundRateLimitedError, match="outbound limit reached: test"):
        local.acquire(now=1059.9, slot="b")
    local.acquire(now=1060.0, slot="c")


@pytest.mark.parametrize("error", [DOWN, HUNG], ids=["down", "hung"])
async def test_without_redis_the_limit_still_holds_locally(
    error: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    clock = Clock()
    rate = broken_limiter(BrokenRedis(error), clock)
    caplog.set_level(logging.DEBUG)

    await rate.acquire()
    await rate.acquire()
    with pytest.raises(OutboundRateLimitedError, match="outbound limit reached: test"):
        await rate.acquire()

    clock.now += 60
    await rate.acquire()  # the window slides locally too
    fallbacks = {r.getMessage() for r in caplog.records if r.name == "app.services.rate_limiter"}
    assert fallbacks == {"redis unavailable op=rate_limit.acquire limit=test"}
    assert all(
        r.levelno == logging.DEBUG for r in caplog.records if r.name == "app.services.rate_limiter"
    )


async def test_a_local_refusal_does_not_consume_the_quota() -> None:
    clock = Clock()
    rate = broken_limiter(BrokenRedis(DOWN), clock, limit=1)
    await rate.acquire()
    for _ in range(3):
        with pytest.raises(OutboundRateLimitedError):
            await rate.acquire()

    clock.now += 60
    await rate.acquire()


async def test_a_local_slot_is_given_back_locally() -> None:
    redis = CountingBrokenRedis(DOWN)
    rate = broken_limiter(redis, Clock(), limit=1)
    slot = await rate.acquire()

    await rate.release(slot)

    await rate.acquire()  # the slot was free again
    assert redis.attempts == 1  # only the first acquire tried Redis: the circuit opened


async def test_releasing_a_redis_slot_with_redis_down_does_not_fail() -> None:
    # The slot expires with the window (plan-D6).
    clock = Clock()
    redis = SwitchableBroken()
    rate = broken_limiter(redis, clock)
    slot = await rate.acquire()
    redis.down = True

    await rate.release(slot)


async def test_zero_disables_the_limit_without_redis_too() -> None:
    redis = CountingBrokenRedis(DOWN)
    rate = broken_limiter(redis, Clock(), limit=0)

    for _ in range(5):
        await rate.release(await rate.acquire())

    assert redis.attempts == 0


class SwitchableBroken:
    """A fakeredis that can go down."""

    def __init__(self) -> None:
        self.fake = FakeAsyncRedis()
        self.down = False

    def __getattr__(self, name: str) -> object:
        if self.down:
            raise DOWN
        return getattr(self.fake, name)


# --- Review T9 ---


async def test_a_refusal_holds_even_if_giving_the_slot_back_fails(
    redis: FakeAsyncRedis,
) -> None:
    # Redis already answered "over the limit": the local window must not admit it.
    clock = Clock()
    rate = broken_limiter(redis, clock, limit=1)
    await rate.acquire()

    async def failing_zrem(*args: object) -> int:
        raise DOWN

    redis.zrem = failing_zrem  # type: ignore[method-assign]

    with pytest.raises(OutboundRateLimitedError):
        await rate.acquire()


async def test_a_refusal_from_redis_closes_a_probing_circuit(redis: FakeAsyncRedis) -> None:
    # Redis answered the probe: the circuit closes, even if the answer is "no".
    clock = Clock()
    circuit = RedisCircuitBreaker(open_seconds=10, now=clock)
    rate = RateLimiter(
        redis,
        key="ratelimit:t",
        name="test",
        limit=1,
        window_seconds=60,
        now=clock,
        circuit=circuit,
    )
    await rate.acquire()
    with pytest.raises(RedisError):
        await circuit.call(BrokenRedis(DOWN).ping)
    clock.now += 10

    with pytest.raises(OutboundRateLimitedError):
        await rate.acquire()

    assert await circuit.call(redis.ping) is True  # not open
