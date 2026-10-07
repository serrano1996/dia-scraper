"""Retry helper for outbound calls to Dia.

`send_with_retry` is a pure function: it takes a `send` callable and an
injectable `sleep`, so tests never wait for real time (plan-D6).
"""

import asyncio
from collections.abc import Awaitable, Callable

import httpx

from app.exceptions import UpstreamBlockedError, UpstreamUnavailableError

Sleep = Callable[[float], Awaitable[None]]


def _is_akamai_block(response: httpx.Response) -> bool:
    """Akamai Bot Manager rejects a request with a 403 HTML page (Fase 0 §5)."""
    content_type = response.headers.get("content-type", "")
    return response.status_code == 403 and content_type.startswith("text/html")


def _is_retryable(response: httpx.Response) -> bool:
    return response.status_code >= 500 or response.status_code == 429


def _raise_if_rejected(response: httpx.Response) -> None:
    """Raise at once for the answers that are never retried (RF-18, RF-19)."""
    status = response.status_code
    if _is_akamai_block(response):
        raise UpstreamBlockedError("blocked by Akamai (403 HTML)", status_code=status)
    if 400 <= status < 500 and not _is_retryable(response):
        raise UpstreamUnavailableError(f"status {status}", status_code=status)


async def send_with_retry(
    send: Callable[[], Awaitable[httpx.Response]],
    *,
    max_attempts: int,
    base_delay: float,
    sleep: Sleep = asyncio.sleep,
) -> httpx.Response:
    """Call `send`, retrying transient failures with exponential backoff.

    - 2xx: returned at once.
    - Akamai's 403 HTML page: `UpstreamBlockedError` at once, never retried: it
      is a verdict on our fingerprint, not a transient failure (RF-19, spec-D7).
    - 5xx, 429 and transport errors (timeouts included) are retried up to
      `max_attempts` attempts in total (spec 001 RF-17). The wait between attempt
      n and n+1 is `base_delay * 2 ** (n - 1)`.
    - Any other 4xx: `UpstreamUnavailableError` at once, with its status (RF-18).
    - Exhausting the attempts raises `UpstreamUnavailableError` (RF-20), chained
      to the last transport error, if any: no httpx type leaves the scrapers (RF-22).
    """
    reason = "no attempt made"
    last_error: httpx.TransportError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = await send()
        except httpx.TransportError as error:
            reason = f"transport error: {type(error).__name__}"
            last_error = error
        else:
            _raise_if_rejected(response)
            if not _is_retryable(response):
                return response
            reason = f"status {response.status_code}"
            last_error = None
        if attempt < max_attempts:
            await sleep(base_delay * 2 ** (attempt - 1))
    raise UpstreamUnavailableError(
        f"retries exhausted after {max_attempts} attempts ({reason})"
    ) from last_error
