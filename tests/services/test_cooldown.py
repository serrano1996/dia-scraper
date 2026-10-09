import logging

import pytest
from fakeredis import FakeAsyncRedis

from app.services.cooldown import COOLDOWN_KEY, AkamaiCooldown, LocalCooldown
from app.services.redis_circuit import RedisCircuitBreaker
from tests.redis_doubles import DOWN, HUNG, BrokenRedis


@pytest.fixture
def redis() -> FakeAsyncRedis:
    return FakeAsyncRedis()


def test_key() -> None:
    assert COOLDOWN_KEY == "akamai:cooldown"


async def test_inactive_at_first(redis: FakeAsyncRedis) -> None:
    assert await AkamaiCooldown(redis, seconds=300).is_active() is False


async def test_activate_starts_a_cooldown_with_its_duration(redis: FakeAsyncRedis) -> None:
    cooldown = AkamaiCooldown(redis, seconds=300)

    assert await cooldown.activate() is True

    assert await cooldown.is_active() is True
    assert 0 < await redis.ttl(COOLDOWN_KEY) <= 300


async def test_a_second_block_does_not_extend_it(redis: FakeAsyncRedis) -> None:
    # spec-D2: fixed duration.
    await redis.set(COOLDOWN_KEY, "1", ex=100)

    assert await AkamaiCooldown(redis, seconds=300).activate() is False

    assert 0 < await redis.ttl(COOLDOWN_KEY) <= 100


async def test_two_instances_share_it(redis: FakeAsyncRedis) -> None:
    await AkamaiCooldown(redis, seconds=300).activate()

    assert await AkamaiCooldown(redis, seconds=300).is_active() is True


# --- Without Redis (spec 008 RF-6, RF-8, plan-D5) ---


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class SwitchableRedis:
    """A fakeredis that can go down and come back."""

    def __init__(self) -> None:
        self.fake = FakeAsyncRedis()
        self.down = False

    def __getattr__(self, name: str) -> object:
        if self.down:
            raise DOWN
        return getattr(self.fake, name)


def test_local_cooldown_has_a_fixed_duration() -> None:
    clock = Clock()
    local = LocalCooldown(now=clock)

    assert local.is_active() is False
    assert local.activate(seconds=300) is True
    clock.now += 200
    assert local.activate(seconds=300) is False  # does not extend it (spec 003 spec-D2)
    clock.now += 99.9
    assert local.is_active() is True
    clock.now += 0.1
    assert local.is_active() is False


@pytest.mark.parametrize("error", [DOWN, HUNG], ids=["down", "hung"])
async def test_without_redis_a_block_starts_a_local_cooldown(
    error: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    clock = Clock()
    cooldown = AkamaiCooldown(
        BrokenRedis(error),
        seconds=300,
        circuit=RedisCircuitBreaker(open_seconds=10, now=clock),
        now=clock,
    )
    caplog.set_level(logging.DEBUG)

    assert await cooldown.is_active() is False
    assert await cooldown.activate() is True
    assert await cooldown.activate() is False
    assert await cooldown.is_active() is True
    clock.now += 300
    assert await cooldown.is_active() is False

    fallbacks = [r for r in caplog.records if r.name == "app.services.cooldown"]
    assert {(r.levelno, r.getMessage()) for r in fallbacks} == {
        (logging.DEBUG, "redis unavailable op=cooldown.is_active"),
        (logging.DEBUG, "redis unavailable op=cooldown.activate"),
    }


async def test_a_local_cooldown_survives_redis_coming_back_without_the_key() -> None:
    # RF-8: Redis is back but never saw the block; insisting would extend it.
    clock = Clock()
    redis = SwitchableRedis()
    cooldown = AkamaiCooldown(redis, seconds=300, now=clock)
    redis.down = True
    await cooldown.activate()

    redis.down = False
    clock.now += 100

    assert await redis.fake.exists(COOLDOWN_KEY) == 0
    assert await cooldown.is_active() is True
    clock.now += 200
    assert await cooldown.is_active() is False


async def test_another_instance_sees_only_the_shared_key(redis: FakeAsyncRedis) -> None:
    # The process that saw the block also keeps it locally (review T9); the
    # others rely on the shared key, which ends the cooldown for them.
    await AkamaiCooldown(redis, seconds=300).activate()
    other = AkamaiCooldown(redis, seconds=300)
    assert await other.is_active() is True

    await redis.delete(COOLDOWN_KEY)

    assert await other.is_active() is False


async def test_a_cooldown_started_in_redis_survives_redis_going_down() -> None:
    # Review T9: an Akamai block must not be forgotten because Redis died after it.
    clock = Clock()
    redis = SwitchableRedis()
    cooldown = AkamaiCooldown(redis, seconds=300, now=clock)
    assert await cooldown.activate() is True

    redis.down = True
    clock.now += 100

    assert await cooldown.is_active() is True
    clock.now += 200
    assert await cooldown.is_active() is False
