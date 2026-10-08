import asyncio
import logging

import httpx
import pytest
from fakeredis import FakeAsyncRedis

from app.exceptions import (
    CooldownActiveError,
    OutboundRateLimitedError,
    PostalCodeNotServedError,
    UpstreamUnavailableError,
)
from app.scrapers.dia_search import DEFAULT_POSTAL_CODE
from app.services.postal_code_sessions import RETIRE_GRACE_SECONDS, PostalCodeSessions
from app.services.rate_limiter import RateLimiter


class FakeSession:
    """Stands in for `DiaSession`: records the PUTs and can fail or block."""

    def __init__(
        self,
        error: Exception | None = None,
        gate: asyncio.Event | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self.client = httpx.AsyncClient()
        self.postal_code = DEFAULT_POSTAL_CODE
        self.puts: list[str] = []
        self.closed = False
        self._error = error
        self._gate = gate
        self._close_error = close_error

    async def set_postal_code(self, postal_code: str) -> None:
        self.puts.append(postal_code)
        if self._gate is not None:
            await self._gate.wait()
        if self._error is not None:
            raise self._error
        self.postal_code = postal_code

    async def aclose(self) -> None:
        self.closed = True
        await self.client.aclose()
        if self._close_error is not None:
            raise self._close_error


class Factory:
    """`new_session` for the pool: hands out sessions and keeps them for the asserts."""

    def __init__(self, **session_kwargs: object) -> None:
        self.session_kwargs = session_kwargs
        self.created: list[FakeSession] = []

    def __call__(self) -> FakeSession:
        session = FakeSession(**self.session_kwargs)
        self.created.append(session)
        return session


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make_pool(
    factory: Factory,
    clock: Clock | None = None,
    *,
    session_limiter: RateLimiter | None = None,
    **kwargs: int,
) -> PostalCodeSessions:
    return PostalCodeSessions(
        new_session=factory,
        max_age_seconds=kwargs.get("max_age_seconds", 3000),
        max_sessions=kwargs.get("max_sessions", 100),
        now=clock or Clock(),
        session_limiter=session_limiter,
    )


def one_new_session(redis: FakeAsyncRedis) -> RateLimiter:
    return RateLimiter(
        redis, key="ratelimit:dia:new_sessions", name="new_sessions", limit=1, window_seconds=600
    )


async def settle() -> None:
    for _ in range(10):
        await asyncio.sleep(0)


async def test_a_new_postal_code_gets_a_session_moved_to_it() -> None:
    factory = Factory()
    pool = make_pool(factory)

    session = await pool.get("08001")

    assert session is factory.created[0]
    assert session.postal_code == "08001"
    assert session.puts == ["08001"]
    await pool.aclose()


async def test_a_known_postal_code_reuses_its_session_without_another_put() -> None:
    factory = Factory()
    pool = make_pool(factory)

    first = await pool.get("08001")
    second = await pool.get("08001")

    assert second is first
    assert len(factory.created) == 1
    assert first.puts == ["08001"]
    await pool.aclose()


async def test_the_default_postal_code_needs_no_put() -> None:
    # The anonymous session is born in 28041 (spec-D6).
    factory = Factory()
    pool = make_pool(factory)

    session = await pool.get(DEFAULT_POSTAL_CODE)

    assert session.puts == []
    assert session.postal_code == DEFAULT_POSTAL_CODE
    await pool.aclose()


async def test_simultaneous_gets_of_a_new_postal_code_share_one_session() -> None:
    gate = asyncio.Event()
    factory = Factory(gate=gate)
    pool = make_pool(factory)
    tasks = [asyncio.create_task(pool.get("08001")) for _ in range(5)]
    await settle()

    gate.set()
    sessions = await asyncio.gather(*tasks)

    assert len(factory.created) == 1
    assert all(session is factory.created[0] for session in sessions)
    await pool.aclose()


@pytest.mark.parametrize(
    "error",
    [PostalCodeNotServedError("35001"), UpstreamUnavailableError("down")],
    ids=["not-served", "unavailable"],
)
async def test_a_failed_put_closes_the_session_and_keeps_nothing(error: Exception) -> None:
    factory = Factory(error=error)
    pool = make_pool(factory)

    with pytest.raises(type(error)):
        await pool.get("35001")

    assert factory.created[0].closed
    factory.session_kwargs = {}
    session = await pool.get("35001")  # tries again with a new session
    assert session is factory.created[1]
    await pool.aclose()


# --- Age, LRU and retirement (T6) ---


async def test_an_old_session_is_replaced_and_the_old_one_retired_not_closed() -> None:
    factory, clock = Factory(), Clock()
    pool = make_pool(factory, clock, max_age_seconds=3000)
    old = await pool.get("08001")

    clock.now += 3000
    new = await pool.get("08001")

    assert new is not old
    assert new.puts == ["08001"]
    assert not old.closed  # a search may still be using it (plan-D6)
    await pool.aclose()


async def test_a_session_younger_than_the_max_age_is_kept() -> None:
    factory, clock = Factory(), Clock()
    pool = make_pool(factory, clock, max_age_seconds=3000)
    first = await pool.get("08001")

    clock.now += 2999
    assert await pool.get("08001") is first
    await pool.aclose()


async def test_past_the_maximum_the_least_recently_used_session_is_retired() -> None:
    factory = Factory()
    pool = make_pool(factory, max_sessions=2)
    a = await pool.get("08001")
    b = await pool.get("41001")
    await pool.get("08001")  # A is now the most recently used

    await pool.get("07001")

    assert await pool.get("08001") is a
    assert len(factory.created) == 3  # B was dropped, A kept
    b_again = await pool.get("41001")
    assert b_again is not b
    await pool.aclose()


async def test_discard_retires_that_session() -> None:
    factory = Factory()
    pool = make_pool(factory)
    stale = await pool.get("08001")

    pool.discard("08001", stale)
    fresh = await pool.get("08001")

    assert fresh is not stale
    assert not stale.closed
    await pool.aclose()


async def test_discard_leaves_a_newer_session_of_the_same_postal_code_alone() -> None:
    factory, clock = Factory(), Clock()
    pool = make_pool(factory, clock, max_age_seconds=3000)
    old = await pool.get("08001")
    clock.now += 3000
    new = await pool.get("08001")

    pool.discard("08001", old)

    assert await pool.get("08001") is new
    await pool.aclose()


async def test_retired_sessions_are_closed_once_the_grace_period_is_over() -> None:
    factory, clock = Factory(), Clock()
    pool = make_pool(factory, clock)
    stale = await pool.get("08001")
    pool.discard("08001", stale)

    clock.now += RETIRE_GRACE_SECONDS - 1
    await pool.get("41001")  # creating a session sweeps the retired ones
    assert not stale.closed

    clock.now += 1
    await pool.get("07001")
    assert stale.closed
    await pool.aclose()


async def test_aclose_closes_active_and_retired_sessions() -> None:
    factory = Factory()
    pool = make_pool(factory)
    stale = await pool.get("08001")
    pool.discard("08001", stale)
    active = await pool.get("41001")

    await pool.aclose()

    assert stale.closed
    assert active.closed


# --- Edges (T7) ---


async def test_with_room_for_one_session_two_alternating_postal_codes_keep_one_active() -> None:
    factory = Factory()
    pool = make_pool(factory, max_sessions=1)

    for postal_code in ["08001", "41001", "08001", "41001"]:
        session = await pool.get(postal_code)
        assert session.postal_code == postal_code
        assert pool.active_count == 1

    assert len(factory.created) == 4
    await pool.aclose()


async def test_discarding_a_session_the_pool_no_longer_has_is_harmless() -> None:
    factory = Factory()
    pool = make_pool(factory, max_sessions=1)
    dropped = await pool.get("08001")
    kept = await pool.get("41001")  # retires 08001's session

    pool.discard("08001", dropped)
    pool.discard("99999", dropped)

    assert await pool.get("41001") is kept
    await pool.aclose()


async def test_the_session_a_caller_holds_stays_open_while_its_search_runs() -> None:
    # A search that got 08001's session keeps a usable client even if the pool
    # drops it meanwhile (plan-D6).
    factory = Factory()
    pool = make_pool(factory, max_sessions=1)
    held = await pool.get("08001")

    await pool.get("41001")

    assert not held.closed
    assert not held.client.is_closed
    await pool.aclose()


# --- Fixes from the fresh review (T14) ---


async def test_retired_sessions_are_also_closed_when_no_new_session_is_created() -> None:
    factory, clock = Factory(), Clock()
    pool = make_pool(factory, clock)
    stale = await pool.get("08001")
    await pool.get("41001")
    pool.discard("08001", stale)

    clock.now += RETIRE_GRACE_SECONDS
    await pool.get("41001")  # a known postal code: no creation

    assert stale.closed
    await pool.aclose()


async def test_a_failing_close_of_a_retired_session_does_not_fail_the_get() -> None:
    factory, clock = Factory(close_error=RuntimeError("broken socket")), Clock()
    pool = make_pool(factory, clock)
    first = await pool.get("08001")
    second = await pool.get("41001")
    pool.discard("08001", first)
    pool.discard("41001", second)

    clock.now += RETIRE_GRACE_SECONDS
    session = await pool.get("07001")

    assert session.postal_code == "07001"
    assert first.closed and second.closed  # both tried, despite the first error
    factory.created[-1]._close_error = None
    await pool.aclose()


async def test_aclose_closes_every_session_even_if_one_close_fails() -> None:
    factory = Factory()
    pool = make_pool(factory)
    broken = await pool.get("08001")
    broken._close_error = RuntimeError("broken socket")
    healthy = await pool.get("41001")

    await pool.aclose()

    assert broken.closed
    assert healthy.closed


async def test_a_creation_that_ends_after_aclose_is_closed_not_kept() -> None:
    # Shielded creations can outlive the request that started them (shutdown).
    gate = asyncio.Event()
    factory = Factory(gate=gate)
    pool = make_pool(factory)
    pending = asyncio.create_task(pool.get("08001"))
    await settle()

    await pool.aclose()
    gate.set()

    with pytest.raises(UpstreamUnavailableError):
        await pending
    assert factory.created[0].closed
    assert pool.active_count == 0


# --- Limit of new sessions (spec 003 RF-7, RF-8, T10) ---


async def test_past_the_limit_a_new_postal_code_is_refused_without_a_put(
    caplog: pytest.LogCaptureFixture,
) -> None:
    factory = Factory()
    pool = make_pool(factory, session_limiter=one_new_session(FakeAsyncRedis()))
    await pool.get("08001")

    with (
        caplog.at_level(logging.WARNING, logger="app.services.postal_code_sessions"),
        pytest.raises(OutboundRateLimitedError),
    ):
        await pool.get("41001")

    assert len(factory.created) == 1  # no session, so no PUT, for 41001
    assert "limit=new_sessions" in caplog.text
    await pool.aclose()


async def test_a_postal_code_with_a_live_session_is_not_limited() -> None:
    factory = Factory()
    pool = make_pool(factory, session_limiter=one_new_session(FakeAsyncRedis()))
    first = await pool.get("08001")

    assert await pool.get("08001") is first
    await pool.aclose()


async def test_dias_default_postal_code_does_not_count() -> None:
    factory = Factory()
    pool = make_pool(factory, session_limiter=one_new_session(FakeAsyncRedis()))

    await pool.get(DEFAULT_POSTAL_CODE)
    await pool.get("08001")  # the only new session of the window

    assert len(factory.created) == 2
    await pool.aclose()


async def test_renewing_an_old_session_counts_as_a_new_one() -> None:
    factory, clock = Factory(), Clock()
    pool = make_pool(
        factory, clock, session_limiter=one_new_session(FakeAsyncRedis()), max_age_seconds=3000
    )
    await pool.get("08001")

    clock.now += 3000
    with pytest.raises(OutboundRateLimitedError):
        await pool.get("08001")
    await pool.aclose()


# --- Fixes from the fresh review (T14) ---


@pytest.mark.parametrize(
    "refusal",
    [CooldownActiveError("cooldown"), OutboundRateLimitedError("outbound limit reached: dia")],
    ids=["cooldown", "global-limit"],
)
async def test_a_put_the_gate_refuses_gives_its_new_session_slot_back(refusal: Exception) -> None:
    # The PUT never left: it must not eat the quota of new postal codes (review W2).
    redis = FakeAsyncRedis()
    factory = Factory(error=refusal)
    pool = make_pool(factory, session_limiter=one_new_session(redis))

    with pytest.raises(type(refusal)):
        await pool.get("08001")

    assert await redis.zcard("ratelimit:dia:new_sessions") == 0
    factory.session_kwargs = {}
    assert (await pool.get("41001")).postal_code == "41001"
    await pool.aclose()


@pytest.mark.parametrize(
    "error",
    [PostalCodeNotServedError("35001"), UpstreamUnavailableError("down")],
    ids=["not-served", "upstream"],
)
async def test_a_put_that_reached_dia_keeps_its_slot(error: Exception) -> None:
    redis = FakeAsyncRedis()
    pool = make_pool(Factory(error=error), session_limiter=one_new_session(redis))

    with pytest.raises(type(error)):
        await pool.get("35001")

    assert await redis.zcard("ratelimit:dia:new_sessions") == 1
    await pool.aclose()


# --- Session events (spec 004 RF-16, T10) ---

POOL_LOGGER = "app.services.postal_code_sessions"


def events(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == POOL_LOGGER and r.levelno == logging.INFO
    ]


async def test_a_new_postal_code_logs_a_new_session(caplog: pytest.LogCaptureFixture) -> None:
    pool = make_pool(Factory())

    with caplog.at_level(logging.INFO, logger=POOL_LOGGER):
        await pool.get("08001")

    assert events(caplog) == ["dia session created postal_code='08001' reason=new"]
    await pool.aclose()


async def test_an_old_session_logs_its_retirement_and_the_renewal(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = Clock()
    pool = make_pool(Factory(), clock, max_age_seconds=3000)
    await pool.get("08001")
    clock.now += 3000

    with caplog.at_level(logging.INFO, logger=POOL_LOGGER):
        await pool.get("08001")

    assert events(caplog) == [
        "dia session retired postal_code='08001' reason=age",
        "dia session created postal_code='08001' reason=renewal:age",
    ]
    await pool.aclose()


async def test_the_least_used_session_logs_lru(caplog: pytest.LogCaptureFixture) -> None:
    pool = make_pool(Factory(), max_sessions=1)
    await pool.get("08001")

    with caplog.at_level(logging.INFO, logger=POOL_LOGGER):
        await pool.get("41001")

    assert "dia session retired postal_code='08001' reason=lru" in events(caplog)
    await pool.aclose()


async def test_a_discard_logs_its_reason_and_the_next_creation_says_why(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pool = make_pool(Factory())
    stale = await pool.get("08001")

    with caplog.at_level(logging.INFO, logger=POOL_LOGGER):
        pool.discard("08001", stale, reason="mismatch")
        await pool.get("08001")

    assert events(caplog) == [
        "dia session retired postal_code='08001' reason=mismatch",
        "dia session created postal_code='08001' reason=renewal:mismatch",
    ]
    await pool.aclose()
