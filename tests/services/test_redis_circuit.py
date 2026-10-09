"""Spec 008 RF-2, RF-3, RF-9: the circuit breaker in front of Redis (plan-D2, plan-D3)."""

import asyncio
import logging

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.services.redis_circuit import RedisCircuitBreaker, RedisCircuitOpenError

SECRET_URL = "redis://:hunter2@redis:6379/0"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Operation:
    """Counts calls; fails with `error` while it is set."""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls = 0
        self.error = error

    async def __call__(self) -> str:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return "pong"


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def circuit(clock: Clock) -> RedisCircuitBreaker:
    return RedisCircuitBreaker(open_seconds=10, now=clock)


def test_the_open_error_is_a_redis_error() -> None:
    # Fallbacks catch `RedisError` only; skipping Redis must land there too (plan-D2).
    assert issubclass(RedisCircuitOpenError, RedisError)


async def test_closed_it_runs_the_operation(circuit: RedisCircuitBreaker) -> None:
    operation = Operation()

    assert await circuit.call(operation) == "pong"
    assert operation.calls == 1


@pytest.mark.parametrize(
    "error", [RedisConnectionError("down"), RedisTimeoutError("hung")], ids=["down", "hung"]
)
async def test_a_redis_failure_opens_it_and_is_raised(
    circuit: RedisCircuitBreaker, error: Exception
) -> None:
    with pytest.raises(type(error)):
        await circuit.call(Operation(error))

    skipped = Operation()
    with pytest.raises(RedisCircuitOpenError):
        await circuit.call(skipped)
    assert skipped.calls == 0


async def test_it_stays_open_until_open_seconds_have_passed(
    circuit: RedisCircuitBreaker, clock: Clock
) -> None:
    with pytest.raises(RedisError):
        await circuit.call(Operation(RedisConnectionError("down")))

    clock.now += 9.9
    skipped = Operation()
    with pytest.raises(RedisCircuitOpenError):
        await circuit.call(skipped)
    assert skipped.calls == 0


async def test_after_open_seconds_a_successful_probe_closes_it(
    circuit: RedisCircuitBreaker, clock: Clock, caplog: pytest.LogCaptureFixture
) -> None:
    with pytest.raises(RedisError):
        await circuit.call(Operation(RedisConnectionError("down")))
    clock.now += 10
    caplog.set_level(logging.INFO, logger="app.services.redis_circuit")

    probe = Operation()
    assert await circuit.call(probe) == "pong"
    assert await circuit.call(probe) == "pong"

    assert probe.calls == 2
    closed = [r for r in caplog.records if r.levelno == logging.INFO]
    assert [r.getMessage() for r in closed] == ["redis circuit closed"]


async def test_a_failed_probe_opens_it_again(circuit: RedisCircuitBreaker, clock: Clock) -> None:
    with pytest.raises(RedisError):
        await circuit.call(Operation(RedisConnectionError("down")))
    clock.now += 10

    with pytest.raises(RedisTimeoutError):
        await circuit.call(Operation(RedisTimeoutError("hung")))

    clock.now += 9.9
    with pytest.raises(RedisCircuitOpenError):
        await circuit.call(Operation())


async def test_an_error_that_is_not_redis_passes_through_without_opening_it(
    circuit: RedisCircuitBreaker,
) -> None:
    # A bug in the operation, not an unavailable Redis: no fallback should hide it.
    with pytest.raises(ValueError, match="bug"):
        await circuit.call(Operation(ValueError("bug")))

    assert await circuit.call(Operation()) == "pong"


async def test_opening_logs_one_warning_with_the_error_type_only(
    circuit: RedisCircuitBreaker, caplog: pytest.LogCaptureFixture
) -> None:
    # The message may carry the Redis URL and its password (plan-D3).
    error = RedisConnectionError(f"Error connecting to {SECRET_URL}")
    caplog.set_level(logging.DEBUG, logger="app.services.redis_circuit")

    with pytest.raises(RedisError):
        await circuit.call(Operation(error))
    for _ in range(3):
        with pytest.raises(RedisCircuitOpenError):
            await circuit.call(Operation())

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert [r.getMessage() for r in warnings] == [
        "redis circuit open seconds=10 error=ConnectionError"
    ]
    assert "hunter2" not in caplog.text
    assert not [r for r in caplog.records if r.exc_info]


async def test_zero_open_seconds_never_opens_but_warns_each_failure(
    clock: Clock, caplog: pytest.LogCaptureFixture
) -> None:
    # RF-3: disabled. Without its WARNING a dead Redis would leave no trace (plan-D3).
    circuit = RedisCircuitBreaker(open_seconds=0, now=clock)
    failing = Operation(RedisTimeoutError("hung"))
    caplog.set_level(logging.WARNING, logger="app.services.redis_circuit")

    for _ in range(2):
        with pytest.raises(RedisTimeoutError):
            await circuit.call(failing)

    assert failing.calls == 2
    assert [r.getMessage() for r in caplog.records] == [
        "redis operation failed circuit=disabled error=TimeoutError"
    ] * 2
    assert await circuit.call(Operation()) == "pong"


async def test_disabled_by_default() -> None:
    # Repositories built without one (the current tests) behave as before (plan-D4).
    circuit = RedisCircuitBreaker()
    failing = Operation(RedisConnectionError("down"))

    for _ in range(2):
        with pytest.raises(RedisConnectionError):
            await circuit.call(failing)

    assert failing.calls == 2


# --- Review T9: one probe at a time ---


async def test_after_open_seconds_only_one_call_probes_redis(
    circuit: RedisCircuitBreaker, clock: Clock, caplog: pytest.LogCaptureFixture
) -> None:
    # Against a hung Redis every probe pays the timeout: only one may pay it (RNF-2).
    with pytest.raises(RedisError):
        await circuit.call(Operation(RedisConnectionError("down")))
    clock.now += 10
    caplog.set_level(logging.WARNING, logger="app.services.redis_circuit")
    caplog.clear()
    hung = Operation(RedisTimeoutError("hung"))

    async def slow_probe() -> str:
        await asyncio.sleep(0.01)
        return await hung()

    results = await asyncio.gather(
        *[circuit.call(slow_probe) for _ in range(20)], return_exceptions=True
    )

    assert hung.calls == 1
    assert sum(isinstance(r, RedisCircuitOpenError) for r in results) == 19
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


async def test_while_a_probe_runs_the_circuit_stays_open_for_others(
    circuit: RedisCircuitBreaker, clock: Clock
) -> None:
    with pytest.raises(RedisError):
        await circuit.call(Operation(RedisConnectionError("down")))
    clock.now += 10
    started = asyncio.Event()
    release = asyncio.Event()

    async def probe() -> str:
        started.set()
        await release.wait()
        return "pong"

    task = asyncio.create_task(circuit.call(probe))
    await started.wait()  # the probe is in flight, whatever the scheduler (spec 009 RF-3)
    with pytest.raises(RedisCircuitOpenError):
        await circuit.call(Operation())
    release.set()

    assert await task == "pong"
    assert await circuit.call(Operation()) == "pong"  # closed by the probe


async def test_a_probe_that_ends_without_a_redis_answer_lets_the_next_call_probe(
    circuit: RedisCircuitBreaker, clock: Clock
) -> None:
    # A bug or a cancellation in the probe says nothing about Redis: try again next time.
    with pytest.raises(RedisError):
        await circuit.call(Operation(RedisConnectionError("down")))
    clock.now += 10

    with pytest.raises(ValueError):
        await circuit.call(Operation(ValueError("bug")))

    assert await circuit.call(Operation()) == "pong"
