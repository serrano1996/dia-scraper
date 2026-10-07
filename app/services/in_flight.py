"""Groups simultaneous identical operations into one (copied from Alcampo, spec 008 RF-1).

Used to create one Dia session per postal code even when several searches for a
new postal code arrive at once (spec 002 RF-7, plan-D3). The first caller for a
key starts the work; later callers wait for the same task.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Generic, TypeVar

T = TypeVar("T")


class InFlight(Generic[T]):
    """Registry of the operations currently running, by key."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[T]] = {}

    async def run(self, key: str, fetch: Callable[[], Awaitable[T]]) -> tuple[T, bool]:
        """Return `(result, shared)`: `shared` is `False` only for the caller that ran it.

        The result, or the exception, reaches every caller. Each one waits
        through `asyncio.shield`, so a client that disconnects cancels its own
        wait, never the work the others depend on.
        """
        task = self._tasks.get(key)
        shared = task is not None
        if task is None:
            task = asyncio.create_task(self._call(fetch))
            self._tasks[key] = task
            task.add_done_callback(lambda done: self._forget(key, done))
        return await asyncio.shield(task), shared

    @staticmethod
    async def _call(fetch: Callable[[], Awaitable[T]]) -> T:
        return await fetch()

    def _forget(self, key: str, done: asyncio.Task[T]) -> None:
        if self._tasks.get(key) is done:
            del self._tasks[key]
        if not done.cancelled():
            # Marks the exception as retrieved: if every waiter was cancelled,
            # asyncio would otherwise log "Task exception was never retrieved".
            done.exception()
