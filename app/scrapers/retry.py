"""Retry helper for outbound calls to Dia.

`send_with_retry` is a pure function: it takes a `send` callable and an
injectable `sleep`, so tests never wait for real time (plan-D6).
"""

import asyncio
import random
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from app.exceptions import UpstreamBlockedError, UpstreamUnavailableError

Sleep = Callable[[float], Awaitable[None]]
Uniform = Callable[[float, float], float]
Now = Callable[[], datetime]

# Longest wait a 429's Retry-After may ask for; above it, no retry (spec 003 RF-10, plan-D6).
MAX_RETRY_AFTER_SECONDS = 60.0

_SECONDS = re.compile(r"^[0-9]+$")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def parse_retry_after(value: str | None, *, now: datetime) -> float | None:
    """`Retry-After` in seconds: integer seconds or an HTTP date (a past date is 0).

    Anything else is `None`, and the caller falls back to the backoff.
    """
    if value is None:
        return None
    value = value.strip()
    if _SECONDS.match(value):
        return float(value)
    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)
    return max((retry_at - now).total_seconds(), 0.0)


def _retry_after(response: httpx.Response, now: datetime) -> float | None:
    """The wait a 429 asks for, or `None` (other statuses, no usable header).

    Raises `UpstreamUnavailableError` when it is longer than we are willing to
    hold a request (spec 003 RF-10).
    """
    if response.status_code != 429:
        return None
    seconds = parse_retry_after(response.headers.get("Retry-After"), now=now)
    if seconds is not None and seconds > MAX_RETRY_AFTER_SECONDS:
        raise UpstreamUnavailableError(f"429 with Retry-After of {seconds:.0f} s")
    return seconds


def _is_akamai_block(response: httpx.Response) -> bool:
    """Akamai Bot Manager rejects a request with a 403 HTML page (Fase 0 §5)."""
    content_type = response.headers.get("content-type", "").lower()
    return response.status_code == 403 and content_type.startswith("text/html")


def _is_retryable(response: httpx.Response) -> bool:
    return response.status_code >= 500 or response.status_code == 429


def _raise_if_rejected(response: httpx.Response) -> None:
    """Raise at once for the answers that are never retried (RF-18, RF-19)."""
    status = response.status_code
    if _is_akamai_block(response):
        raise UpstreamBlockedError("blocked by Akamai (403 HTML)", status_code=status)
    # Besides 4xx, any 1xx or 3xx: httpx does not follow redirects, and a
    # redirect (to a challenge page, say) is not the JSON we asked for.
    if not response.is_success and not _is_retryable(response):
        raise UpstreamUnavailableError(f"status {status}", status_code=status)


async def send_with_retry(
    send: Callable[[], Awaitable[httpx.Response]],
    *,
    max_attempts: int,
    base_delay: float,
    sleep: Sleep = asyncio.sleep,
    jitter_max: float = 0.0,
    uniform: Uniform = random.uniform,
    now: Now = _utc_now,
) -> httpx.Response:
    """Call `send`, retrying transient failures with exponential backoff.

    - 2xx: returned at once.
    - Akamai's 403 HTML page: `UpstreamBlockedError` at once, never retried: it
      is a verdict on our fingerprint, not a transient failure (RF-19, spec-D7).
    - 5xx, 429 and transport errors (timeouts included) are retried up to
      `max_attempts` attempts in total (spec 001 RF-17). The wait between attempt
      n and n+1 is `base_delay * 2 ** (n - 1)` plus a random jitter in
      `[0, jitter_max]`, so the waits do not follow a regular pattern (spec 003 RF-9).
    - A 429 with `Retry-After` waits what it says (plus jitter) instead of the
      backoff; above 60 s, `UpstreamUnavailableError` at once (spec 003 RF-10).
    - Any other 4xx, and any 1xx or 3xx: `UpstreamUnavailableError` at once, with
      its status (RF-18). Other request errors (decoding, redirects) too, unretried.
    - Exhausting the attempts raises `UpstreamUnavailableError` (RF-20), chained
      to the last transport error, if any: no httpx type leaves the scrapers (RF-22).
    """
    reason = "no attempt made"
    last_error: httpx.TransportError | None = None
    asked_wait: float | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = await send()
        except httpx.TransportError as error:
            reason = f"transport error: {type(error).__name__}"
            last_error = error
            asked_wait = None
        except httpx.RequestError as error:
            # Not transient (a body that cannot be decoded, a redirect loop): no retry.
            raise UpstreamUnavailableError(f"request error: {type(error).__name__}") from error
        else:
            _raise_if_rejected(response)
            if not _is_retryable(response):
                return response
            reason = f"status {response.status_code}"
            last_error = None
            asked_wait = _retry_after(response, now())
        if attempt < max_attempts:
            wait = base_delay * 2 ** (attempt - 1) if asked_wait is None else asked_wait
            await sleep(wait + uniform(0, jitter_max))
    raise UpstreamUnavailableError(
        f"retries exhausted after {max_attempts} attempts ({reason})"
    ) from last_error
