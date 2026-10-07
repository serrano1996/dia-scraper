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


async def test_blocked_starts_the_cooldown_and_warns_once(
    redis: FakeAsyncRedis, caplog: pytest.LogCaptureFixture
) -> None:
    gate = make_gate(redis)

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        await gate.blocked()
        await gate.blocked()

    assert await redis.exists(COOLDOWN_KEY)
    warnings = [r for r in caplog.records if "akamai cooldown activated" in r.getMessage()]
    assert len(warnings) == 1
    assert "seconds=300" in warnings[0].getMessage()
