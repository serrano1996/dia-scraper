import asyncio

import httpx
import pytest

from app.exceptions import PostalCodeNotServedError, UpstreamUnavailableError
from app.scrapers.dia_search import DEFAULT_POSTAL_CODE
from app.services.postal_code_sessions import RETIRE_GRACE_SECONDS, PostalCodeSessions


class FakeSession:
    """Stands in for `DiaSession`: records the PUTs and can fail or block."""

    def __init__(self, error: Exception | None = None, gate: asyncio.Event | None = None) -> None:
        self.client = httpx.AsyncClient()
        self.postal_code = DEFAULT_POSTAL_CODE
        self.puts: list[str] = []
        self.closed = False
        self._error = error
        self._gate = gate

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


def make_pool(factory: Factory, clock: Clock | None = None, **kwargs: int) -> PostalCodeSessions:
    return PostalCodeSessions(
        new_session=factory,
        max_age_seconds=kwargs.get("max_age_seconds", 3000),
        max_sessions=kwargs.get("max_sessions", 100),
        now=clock or Clock(),
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

    await pool.discard("08001", stale)
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

    await pool.discard("08001", old)

    assert await pool.get("08001") is new
    await pool.aclose()


async def test_retired_sessions_are_closed_once_the_grace_period_is_over() -> None:
    factory, clock = Factory(), Clock()
    pool = make_pool(factory, clock)
    stale = await pool.get("08001")
    await pool.discard("08001", stale)

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
    await pool.discard("08001", stale)
    active = await pool.get("41001")

    await pool.aclose()

    assert stale.closed
    assert active.closed
