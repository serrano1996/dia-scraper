"""One Dia session per postal code, in process memory (spec 002 RF-2, RF-3, RF-7, RF-13).

Dia takes the postal code from the session, so each postal code needs its own
session moved to it with one `PUT` (Fase 0 §3). The pool keeps them so a known
postal code costs no extra request. Session cookies stay in this process.
"""

import time
from collections.abc import Callable
from typing import Protocol

import httpx

from app.scrapers.dia_search import DEFAULT_POSTAL_CODE
from app.services.in_flight import InFlight


class PostalCodeSession(Protocol):
    """The part of `DiaSession` the pool and the service use."""

    @property
    def client(self) -> httpx.AsyncClient: ...

    @property
    def postal_code(self) -> str: ...

    async def set_postal_code(self, postal_code: str) -> None: ...
    async def aclose(self) -> None: ...


class PostalCodeSessions:
    """Registry of sessions, one per postal code; one instance per process (`lifespan`)."""

    def __init__(
        self,
        *,
        new_session: Callable[[], PostalCodeSession],
        max_age_seconds: int,
        max_sessions: int,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._new_session = new_session
        self._max_age = max_age_seconds
        self._max_sessions = max_sessions
        self._now = now
        self._sessions: dict[str, PostalCodeSession] = {}
        self._creations: InFlight[PostalCodeSession] = InFlight()

    async def get(self, postal_code: str) -> PostalCodeSession:
        """The session for `postal_code`, created (with its `PUT`) if missing.

        Raises what `set_postal_code` raises: `PostalCodeNotServedError`,
        `UpstreamUnavailableError`. A failed session is closed and not kept.
        """
        session = self._sessions.get(postal_code)
        if session is not None:
            return session
        session, _ = await self._creations.run(postal_code, lambda: self._create(postal_code))
        return session

    async def aclose(self) -> None:
        for session in self._sessions.values():
            await session.aclose()
        self._sessions.clear()

    async def _create(self, postal_code: str) -> PostalCodeSession:
        session = self._new_session()
        try:
            # The anonymous session is born in Dia's default postal code (spec-D6).
            if postal_code != DEFAULT_POSTAL_CODE:
                await session.set_postal_code(postal_code)
        except BaseException:
            await session.aclose()
            raise
        self._sessions[postal_code] = session
        return session
