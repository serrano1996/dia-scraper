"""One Dia session per postal code, in process memory (spec 002 RF-2, RF-3, RF-7, RF-13).

Dia takes the postal code from the session, so each postal code needs its own
session moved to it with one `PUT` (Fase 0 §3). The pool keeps them so a known
postal code costs no extra request. Session cookies stay in this process.
"""

import logging
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.exceptions import CooldownActiveError, OutboundRateLimitedError, UpstreamUnavailableError
from app.scrapers.dia_search import DEFAULT_POSTAL_CODE
from app.services.in_flight import InFlight
from app.services.rate_limiter import RateLimiter

# How long a retired session stays open: a search may still be using it. The
# worst search with the defaults is RETRY_MAX_ATTEMPTS x HTTP_TIMEOUT_SECONDS
# plus the waits, about 32 s; raise this if those settings grow (plan-D6, R5).
RETIRE_GRACE_SECONDS = 120

logger = logging.getLogger(__name__)


class PostalCodeSession(Protocol):
    """The part of `DiaSession` the pool and the service use."""

    @property
    def client(self) -> httpx.AsyncClient: ...

    @property
    def postal_code(self) -> str: ...

    async def set_postal_code(self, postal_code: str) -> None: ...
    async def aclose(self) -> None: ...


@dataclass
class _Entry:
    session: PostalCodeSession
    created_at: float


@dataclass
class _Retired:
    session: PostalCodeSession
    retired_at: float


class PostalCodeSessions:
    """Registry of sessions, one per postal code; one instance per process (`lifespan`)."""

    def __init__(
        self,
        *,
        new_session: Callable[[], PostalCodeSession],
        max_age_seconds: int,
        max_sessions: int,
        now: Callable[[], float] = time.monotonic,
        session_limiter: RateLimiter | None = None,
    ) -> None:
        self._new_session = new_session
        self._max_age = max_age_seconds
        self._max_sessions = max_sessions
        self._now = now
        # Least recently used first (plan-D5).
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._retired: list[_Retired] = []
        # Why each postal code last lost its session, to explain the next one (spec 004 RF-16).
        self._retire_reasons: dict[str, str] = {}
        self._closed = False
        # New sessions (each one a PUT) per window, renewals included (spec 003 RF-7, RF-8).
        self._session_limiter = session_limiter
        self._creations: InFlight[PostalCodeSession] = InFlight()

    @property
    def active_count(self) -> int:
        """Sessions currently handed out, never more than `max_sessions` (RF-13)."""
        return len(self._entries)

    async def get(self, postal_code: str) -> PostalCodeSession:
        """The session for `postal_code`, created (with its `PUT`) if missing or too old.

        Age counts from creation (plan-D4). Raises what `set_postal_code` raises:
        `PostalCodeNotServedError`, `UpstreamUnavailableError`. A failed session
        is closed and not kept.

        Tolerated race: for one loop turn after a creation ends, a caller can join
        the finished task and get a session another caller has just retired. It
        still works for the whole grace period (plan-D6).
        """
        await self._close_expired_retired()
        entry = self._entries.get(postal_code)
        if entry is not None:
            if self._now() - entry.created_at < self._max_age:
                self._entries.move_to_end(postal_code)
                return entry.session
            self._retire(postal_code, reason="age")
        session, _ = await self._creations.run(postal_code, lambda: self._create(postal_code))
        return session

    def discard(
        self, postal_code: str, session: PostalCodeSession, reason: str = "discarded"
    ) -> None:
        """Drop `session` if it is still the one kept for `postal_code` (spec 002 RF-9).

        A newer session of the same postal code is left alone: another search
        already replaced the stale one.
        """
        entry = self._entries.get(postal_code)
        if entry is not None and entry.session is session:
            self._retire(postal_code, reason=reason)

    async def aclose(self) -> None:
        """Close every session, active and retired (RF-14).

        Snapshots and empties the pool first: a creation still running (it is
        shielded, so it can outlive its request) must not change what is being
        closed, and finds the pool closed when it ends.
        """
        self._closed = True
        sessions = [entry.session for entry in self._entries.values()]
        sessions += [retired.session for retired in self._retired]
        self._entries.clear()
        self._retired.clear()
        for session in sessions:
            await _close(session)

    async def _create(self, postal_code: str) -> PostalCodeSession:
        slot = ""
        if postal_code != DEFAULT_POSTAL_CODE:
            # Before building anything: a refused postal code costs no client and no PUT.
            slot = await self._admit_new_session()
        session = self._new_session()
        try:
            # The anonymous session is born in Dia's default postal code (spec-D6).
            if postal_code != DEFAULT_POSTAL_CODE:
                await session.set_postal_code(postal_code)
        except (CooldownActiveError, OutboundRateLimitedError):
            # The gate stopped the PUT before it left: give the slot back, or a
            # cooldown would use up the quota of new postal codes (review T14).
            await self._release_new_session(slot)
            await _close(session)
            raise
        except BaseException:
            await _close(session)
            raise
        if self._closed:
            await _close(session)
            raise UpstreamUnavailableError("session pool closed")
        self._entries[postal_code] = _Entry(session=session, created_at=self._now())
        previous = self._retire_reasons.pop(postal_code, None)
        logger.info(
            "dia session created postal_code=%r reason=%s",
            postal_code,
            "new" if previous is None else f"renewal:{previous}",
        )
        while len(self._entries) > self._max_sessions:
            self._retire(next(iter(self._entries)), reason="lru")
        return session

    async def _release_new_session(self, slot: str) -> None:
        if self._session_limiter is not None:
            await self._session_limiter.release(slot)

    async def _admit_new_session(self) -> str:
        if self._session_limiter is None:
            return ""
        try:
            return await self._session_limiter.acquire()
        except OutboundRateLimitedError:
            logger.warning(
                "outbound limit reached limit=%s max=%d",
                self._session_limiter.name,
                self._session_limiter.limit,
            )
            raise

    def _retire(self, postal_code: str, *, reason: str) -> None:
        """Stop handing out the session; it is closed later, once no search can be using it."""
        entry = self._entries.pop(postal_code)
        self._retired.append(_Retired(session=entry.session, retired_at=self._now()))
        self._retire_reasons[postal_code] = reason
        logger.info("dia session retired postal_code=%r reason=%s", postal_code, reason)

    async def _close_expired_retired(self) -> None:
        now = self._now()
        expired = [r for r in self._retired if now - r.retired_at >= RETIRE_GRACE_SECONDS]
        self._retired = [r for r in self._retired if now - r.retired_at < RETIRE_GRACE_SECONDS]
        for retired in expired:
            await _close(retired.session)


async def _close(session: PostalCodeSession) -> None:
    """Close a session; a failure is logged, never raised: closing must not break a search."""
    try:
        await session.aclose()
    except Exception as error:  # any close failure is only worth a log line
        logger.warning("could not close a Dia session: %s", type(error).__name__)
