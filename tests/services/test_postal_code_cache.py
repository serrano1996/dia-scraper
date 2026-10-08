import logging

import pytest
from fakeredis import FakeAsyncRedis

from app.services.postal_code_cache import NotServedRepository, not_served_key
from app.services.redis_circuit import RedisCircuitBreaker
from tests.redis_doubles import DOWN, HUNG, BrokenRedis, CountingBrokenRedis


@pytest.fixture
def redis() -> FakeAsyncRedis:
    return FakeAsyncRedis()


@pytest.fixture
def repository(redis: FakeAsyncRedis) -> NotServedRepository:
    return NotServedRepository(redis)


def test_key_names_the_postal_code() -> None:
    assert not_served_key("35001") == "postal_code:not_served:35001"


async def test_an_unknown_postal_code_is_not_marked(repository: NotServedRepository) -> None:
    assert await repository.is_marked("35001") is False


async def test_mark_then_is_marked(repository: NotServedRepository, redis: FakeAsyncRedis) -> None:
    await repository.mark("35001", ttl_seconds=600)

    assert await repository.is_marked("35001") is True
    assert 0 < await redis.ttl("postal_code:not_served:35001") <= 600


async def test_marks_are_per_postal_code(repository: NotServedRepository) -> None:
    await repository.mark("35001", ttl_seconds=600)

    assert await repository.is_marked("08001") is False


# --- Without Redis (spec 008 RF-5, spec-D4, plan-D4) ---


@pytest.mark.parametrize("error", [DOWN, HUNG], ids=["down", "hung"])
async def test_without_redis_a_postal_code_is_not_marked_and_marking_is_skipped(
    error: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    # Dia is asked again: rare, and its PUT is limited locally (spec-D4).
    repository = NotServedRepository(
        BrokenRedis(error), circuit=RedisCircuitBreaker(open_seconds=10)
    )
    caplog.set_level(logging.DEBUG)

    assert await repository.is_marked("35001") is False
    await repository.mark("35001", ttl_seconds=600)

    fallbacks = [r for r in caplog.records if r.name == "app.services.postal_code_cache"]
    assert [(r.levelno, r.getMessage()) for r in fallbacks] == [
        (logging.DEBUG, "redis unavailable op=not_served.is_marked"),
        (logging.DEBUG, "redis unavailable op=not_served.mark"),
    ]


async def test_with_the_circuit_open_redis_is_not_touched() -> None:
    redis = CountingBrokenRedis(DOWN)
    repository = NotServedRepository(redis, circuit=RedisCircuitBreaker(open_seconds=10))

    await repository.is_marked("35001")
    await repository.mark("35001", ttl_seconds=600)

    assert redis.attempts == 1
