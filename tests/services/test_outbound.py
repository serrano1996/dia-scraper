import logging

import pytest
from fakeredis import FakeAsyncRedis

from app.exceptions import CooldownActiveError, OutboundRateLimitedError
from app.services.cooldown import COOLDOWN_KEY, AkamaiCooldown
from app.services.outbound import OutboundGate
from app.services.rate_limiter import RateLimiter

LOGGER = "app.services.outbound"


@pytest.fixture
def redis() -> FakeAsyncRedis:
    return FakeAsyncRedis()


def make_gate(redis: FakeAsyncRedis, *, limit: int = 2) -> OutboundGate:
    return OutboundGate(
        cooldown=AkamaiCooldown(redis, seconds=300),
        limiter=RateLimiter(redis, key="ratelimit:dia", name="dia", limit=limit, window_seconds=60),
    )


async def test_admit_takes_a_slot(redis: FakeAsyncRedis) -> None:
    gate = make_gate(redis)

    await gate.admit()

    assert await redis.zcard("ratelimit:dia") == 1


async def test_admit_during_a_cooldown_refuses_without_taking_a_slot(redis: FakeAsyncRedis) -> None:
    gate = make_gate(redis)
    await redis.set(COOLDOWN_KEY, "1", ex=300)

    with pytest.raises(CooldownActiveError):
        await gate.admit()

    assert await redis.zcard("ratelimit:dia") == 0


async def test_admit_past_the_limit_refuses_and_warns(
    redis: FakeAsyncRedis, caplog: pytest.LogCaptureFixture
) -> None:
    gate = make_gate(redis, limit=1)
    await gate.admit()

    with caplog.at_level(logging.WARNING, logger=LOGGER), pytest.raises(OutboundRateLimitedError):
        await gate.admit()

    assert "outbound limit reached" in caplog.text
    assert "limit=dia" in caplog.text


async def test_a_block_is_one_error_naming_the_path_and_the_cooldown(
    redis: FakeAsyncRedis, caplog: pytest.LogCaptureFixture
) -> None:
    # spec 004 RF-11, spec-D3: the actionable event of the whole episode.
    gate = make_gate(redis)

    with caplog.at_level(logging.INFO, logger=LOGGER):
        await gate.blocked("/api/v1/search-back/search/reduced")
        await gate.blocked("/api/v1/common-aggregator/save-shipping-address")

    assert await redis.exists(COOLDOWN_KEY)
    first, second = [r for r in caplog.records if r.name == LOGGER]
    assert first.levelno == second.levelno == logging.ERROR
    assert "path=/api/v1/search-back/search/reduced" in first.getMessage()
    assert "cooldown=started seconds=300" in first.getMessage()
    assert "cooldown=already_active" in second.getMessage()
