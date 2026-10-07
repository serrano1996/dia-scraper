import pytest
from fakeredis import FakeAsyncRedis

from app.services.cooldown import COOLDOWN_KEY, AkamaiCooldown


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
