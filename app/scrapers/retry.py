"""Retry helper for outbound calls to Dia.

`send_with_retry` is a pure function: it takes a `send` callable and an
injectable `sleep`, so tests never wait for real time (plan-D6).
"""

import asyncio
from collections.abc import Awaitable, Callable

import httpx

from app.exceptions import UpstreamUnavailableError

Sleep = Callable[[float], Awaitable[None]]


def _is_retryable(response: httpx.Response) -> bool:
    return response.status_code >= 500 or response.status_code == 429


async def send_with_retry(
    send: Callable[[], Awaitable[httpx.Response]],
    *,
    max_attempts: int,
    base_delay: float,
    sleep: Sleep = asyncio.sleep,
) -> httpx.Response:
    """Call `send`, retrying transient failures with exponential backoff.

    - 5xx, 429 and transport errors (timeouts included) are retried up to
      `max_attempts` attempts in total (spec 001 RF-17).
    - The wait between attempt n and n+1 is `base_delay * 2 ** (n - 1)`.
    - Exhausting the attempts raises `UpstreamUnavailableError` (RF-20).
    """
    reason = "no attempt made"
    for attempt in range(1, max_attempts + 1):
        try:
            response = await send()
        except httpx.TransportError as error:
            reason = f"transport error: {type(error).__name__}"
        else:
            if not _is_retryable(response):
                return response
            reason = f"status {response.status_code}"
        if attempt < max_attempts:
            await sleep(base_delay * 2 ** (attempt - 1))
    raise UpstreamUnavailableError(f"retries exhausted after {max_attempts} attempts ({reason})")
