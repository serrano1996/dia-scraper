import asyncio

import pytest

from app.services.in_flight import InFlight


class GatedFetch:
    """A fetch that blocks until `gate` is set, counting how often it starts."""

    def __init__(self, result: str = "result", error: Exception | None = None) -> None:
        self.gate = asyncio.Event()
        self.calls = 0
        self.result = result
        self.error = error

    async def __call__(self) -> str:
        self.calls += 1
        await self.gate.wait()
        if self.error is not None:
            raise self.error
        return self.result


async def settle() -> None:
    """Let every pending task reach its first real wait (no wall-clock sleep)."""
    for _ in range(10):
        await asyncio.sleep(0)


async def test_simultaneous_runs_of_one_key_share_a_single_fetch() -> None:
    in_flight: InFlight[str] = InFlight()
    fetch = GatedFetch()
    tasks = [asyncio.create_task(in_flight.run("08001", fetch)) for _ in range(5)]
    await settle()

    fetch.gate.set()
    results = await asyncio.gather(*tasks)

    assert fetch.calls == 1
    assert [result for result, _ in results] == ["result"] * 5
    assert [shared for _, shared in results] == [False, True, True, True, True]


async def test_an_error_reaches_every_waiter() -> None:
    in_flight: InFlight[str] = InFlight()
    fetch = GatedFetch(error=RuntimeError("boom"))
    tasks = [asyncio.create_task(in_flight.run("08001", fetch)) for _ in range(3)]
    await settle()

    fetch.gate.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)

    assert fetch.calls == 1
    assert all(isinstance(r, RuntimeError) and str(r) == "boom" for r in results)


async def test_different_keys_are_not_grouped() -> None:
    in_flight: InFlight[str] = InFlight()
    fetch = GatedFetch()
    tasks = [
        asyncio.create_task(in_flight.run("08001", fetch)),
        asyncio.create_task(in_flight.run("41001", fetch)),
    ]
    await settle()

    fetch.gate.set()
    await asyncio.gather(*tasks)

    assert fetch.calls == 2


async def test_a_finished_key_is_forgotten() -> None:
    in_flight: InFlight[str] = InFlight()
    fetch = GatedFetch()
    fetch.gate.set()

    await in_flight.run("08001", fetch)
    _, shared = await in_flight.run("08001", fetch)

    assert fetch.calls == 2
    assert shared is False


async def test_cancelling_one_waiter_does_not_cancel_the_others() -> None:
    # A client that disconnects must not abort the session others are waiting for.
    in_flight: InFlight[str] = InFlight()
    fetch = GatedFetch()
    first = asyncio.create_task(in_flight.run("08001", fetch))
    second = asyncio.create_task(in_flight.run("08001", fetch))
    await settle()

    first.cancel()
    await settle()
    fetch.gate.set()

    assert await second == ("result", True)
    with pytest.raises(asyncio.CancelledError):
        await first
    assert fetch.calls == 1
