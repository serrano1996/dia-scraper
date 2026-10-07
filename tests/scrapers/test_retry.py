from collections.abc import Awaitable, Callable

import httpx
import pytest

from app.exceptions import UpstreamBlockedError, UpstreamUnavailableError
from app.scrapers.retry import send_with_retry

REQUEST = httpx.Request("GET", "https://dia.test/api/v1/search-back/search/reduced")

Outcome = httpx.Response | Exception

# Akamai's real 403 page (Fase 0 §5), with a synthetic reference number.
AKAMAI_ACCESS_DENIED = (
    "<HTML><HEAD>\n<TITLE>Access Denied</TITLE>\n</HEAD><BODY>\n<H1>Access Denied</H1>\n \n"
    'You don\'t have permission to access "http&#58;&#47;&#47;www&#46;dia&#46;es&#47;" '
    "on this server.<P>\nReference&#32;&#35;18&#46;00000000&#46;0000000000&#46;00000000\n"
    "</BODY>\n</HTML>\n"
)


class FakeSend:
    """Returns (or raises) each outcome in order and counts the calls."""

    def __init__(self, *outcomes: Outcome) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    async def __call__(self) -> httpx.Response:
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeSleep:
    """Records the requested waits instead of waiting."""

    def __init__(self) -> None:
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


def response(status_code: int, **kwargs: object) -> httpx.Response:
    return httpx.Response(status_code, request=REQUEST, **kwargs)


async def run(send: Callable[[], Awaitable[httpx.Response]], sleep: FakeSleep) -> httpx.Response:
    return await send_with_retry(send, max_attempts=3, base_delay=0.5, sleep=sleep)


async def test_success_is_returned_at_once() -> None:
    send, sleep = FakeSend(response(200, json={"ok": True})), FakeSleep()

    result = await run(send, sleep)

    assert result.status_code == 200
    assert send.calls == 1
    assert sleep.waits == []


async def test_server_errors_are_retried_with_exponential_backoff() -> None:
    send, sleep = FakeSend(response(503), response(503), response(200)), FakeSleep()

    result = await run(send, sleep)

    assert result.status_code == 200
    assert send.calls == 3
    assert sleep.waits == [0.5, 1.0]


async def test_too_many_requests_is_retried() -> None:
    send, sleep = FakeSend(response(429), response(200)), FakeSleep()

    assert (await run(send, sleep)).status_code == 200
    assert send.calls == 2


@pytest.mark.parametrize(
    "error",
    [httpx.ConnectTimeout("slow", request=REQUEST), httpx.ConnectError("down", request=REQUEST)],
)
async def test_transport_errors_are_retried(error: Exception) -> None:
    send, sleep = FakeSend(error, response(200)), FakeSleep()

    assert (await run(send, sleep)).status_code == 200
    assert send.calls == 2


async def test_exhausted_retries_raise_without_waiting_after_the_last_attempt() -> None:
    send, sleep = FakeSend(response(503), response(503), response(503)), FakeSleep()

    with pytest.raises(UpstreamUnavailableError):
        await run(send, sleep)

    assert send.calls == 3
    assert sleep.waits == [0.5, 1.0]


# --- Non-retryable answers (T11) ---


async def test_not_found_is_not_retried() -> None:
    send, sleep = FakeSend(response(404, text="<html>Bloqueado</html>")), FakeSleep()

    with pytest.raises(UpstreamUnavailableError) as exc_info:
        await run(send, sleep)

    assert type(exc_info.value) is UpstreamUnavailableError
    assert exc_info.value.status_code == 404
    assert send.calls == 1
    assert sleep.waits == []


async def test_a_json_forbidden_is_a_plain_rejection_not_an_akamai_block() -> None:
    send, sleep = FakeSend(response(403, json={"error": "forbidden"})), FakeSleep()

    with pytest.raises(UpstreamUnavailableError) as exc_info:
        await run(send, sleep)

    assert type(exc_info.value) is UpstreamUnavailableError
    assert exc_info.value.status_code == 403
    assert send.calls == 1


async def test_akamai_access_denied_is_a_block_and_is_not_retried() -> None:
    blocked = response(403, text=AKAMAI_ACCESS_DENIED, headers={"Content-Type": "text/html"})
    send, sleep = FakeSend(blocked, response(200)), FakeSleep()

    with pytest.raises(UpstreamBlockedError) as exc_info:
        await run(send, sleep)

    assert exc_info.value.status_code == 403
    assert send.calls == 1
    assert sleep.waits == []


async def test_exhausted_transport_errors_raise_a_domain_error_chained_to_the_cause() -> None:
    timeout = httpx.ReadTimeout("slow", request=REQUEST)
    send, sleep = FakeSend(timeout, timeout, timeout), FakeSleep()

    with pytest.raises(UpstreamUnavailableError) as exc_info:
        await run(send, sleep)

    assert not isinstance(exc_info.value, httpx.HTTPError)
    assert exc_info.value.__cause__ is timeout
