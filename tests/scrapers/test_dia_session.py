import httpx
import pytest
import respx

from app.core.config import Settings
from app.exceptions import (
    CooldownActiveError,
    PostalCodeNotServedError,
    UpstreamBlockedError,
    UpstreamUnavailableError,
)
from app.scrapers.dia_search import DEFAULT_POSTAL_CODE
from app.scrapers.dia_session import SAVE_SHIPPING_ADDRESS_PATH, DiaSession
from app.scrapers.http_client import create_http_client
from tests.fixture_data import load_fixture
from tests.scrapers.gate_doubles import FakeGate

BASE_URL = "https://dia.test"
PUT_URL = f"{BASE_URL}/api/v1/common-aggregator/save-shipping-address"
SEARCH_URL = f"{BASE_URL}/api/v1/search-back/search/reduced"
# Synthetic, same shape as the real one (constitution #12).
SESSION_ID = "00000000-0000-4000-8000-000000000000"


def make_session(gate: FakeGate | None = None) -> DiaSession:
    settings = Settings(
        _env_file=None,
        dia_base_url=BASE_URL,
        redis_url="redis://x",
        retry_base_delay=0,
        retry_jitter_max_s=0,
    )
    return DiaSession(client=create_http_client(settings), settings=settings, gate=gate)


async def set_postal_code(postal_code: str = "08001") -> DiaSession:
    session = make_session()
    try:
        await session.set_postal_code(postal_code)
    except BaseException:
        await session.aclose()
        raise
    return session


def test_path_matches_fase_0() -> None:
    assert SAVE_SHIPPING_ADDRESS_PATH == "/api/v1/common-aggregator/save-shipping-address"


async def test_a_new_session_starts_with_dias_default_postal_code() -> None:
    session = make_session()
    try:
        assert session.postal_code == DEFAULT_POSTAL_CODE
    finally:
        await session.aclose()


@respx.mock
async def test_204_sets_the_postal_code_with_a_single_bodyless_put() -> None:
    route = respx.put(PUT_URL).mock(return_value=httpx.Response(204))

    session = await set_postal_code("08001")
    try:
        assert session.postal_code == "08001"
    finally:
        await session.aclose()

    assert route.call_count == 1
    request = route.calls.last.request
    assert dict(httpx.QueryParams(request.url.query)) == {"new_postal_code": "08001"}
    assert request.content == b""
    assert "Chrome/155." in request.headers["User-Agent"]


@respx.mock
async def test_the_put_carries_the_cookies_of_the_session() -> None:
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json=load_fixture("dia_search_no_results.json"),
            headers={"Set-Cookie": f"session_id={SESSION_ID}; Path=/; Domain=dia.test"},
        )
    )
    route = respx.put(PUT_URL).mock(return_value=httpx.Response(204))
    session = make_session()
    try:
        await session.client.get("/api/v1/search-back/search/reduced", params={"q": "pan"})
        await session.set_postal_code("08001")
    finally:
        await session.aclose()

    assert f"session_id={SESSION_ID}" in route.calls.last.request.headers["Cookie"]


@respx.mock
async def test_the_real_206_means_the_postal_code_is_not_served() -> None:
    respx.put(PUT_URL).mock(
        return_value=httpx.Response(
            206, json=load_fixture("dia_save_shipping_address_no_service.json")
        )
    )

    with pytest.raises(PostalCodeNotServedError) as exc_info:
        await set_postal_code("35001")

    assert exc_info.value.postal_code == "35001"


@respx.mock
@pytest.mark.parametrize(
    "upstream",
    [
        httpx.Response(206, json={"code": 206, "type": "OTHER"}),
        httpx.Response(206, text="not json"),
        httpx.Response(200, json={}),
    ],
    ids=["206-other-body", "206-not-json", "200"],
)
async def test_any_other_success_answer_is_an_upstream_failure(upstream: httpx.Response) -> None:
    respx.put(PUT_URL).mock(return_value=upstream)

    with pytest.raises(UpstreamUnavailableError) as exc_info:
        await set_postal_code()

    assert type(exc_info.value) is UpstreamUnavailableError


@respx.mock
async def test_server_errors_are_retried_like_any_request_to_dia() -> None:
    route = respx.put(PUT_URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(503), httpx.Response(204)]
    )

    session = await set_postal_code()
    await session.aclose()

    assert route.call_count == 3


@respx.mock
async def test_an_akamai_block_is_not_retried() -> None:
    route = respx.put(PUT_URL).mock(
        return_value=httpx.Response(
            403,
            text="<HTML><TITLE>Access Denied</TITLE></HTML>",
            headers={"Content-Type": "text/html"},
        )
    )

    with pytest.raises(UpstreamBlockedError):
        await set_postal_code()

    assert route.call_count == 1


@respx.mock
async def test_a_failed_put_keeps_the_previous_postal_code() -> None:
    respx.put(PUT_URL).mock(return_value=httpx.Response(404))
    session = make_session()
    try:
        with pytest.raises(UpstreamUnavailableError):
            await session.set_postal_code("08001")
        assert session.postal_code == DEFAULT_POSTAL_CODE
    finally:
        await session.aclose()


async def test_aclose_closes_its_client() -> None:
    session = make_session()

    await session.aclose()

    assert session.client.is_closed


# --- The outbound gate (spec 003, T9) ---


@respx.mock
async def test_a_put_refused_by_the_gate_never_reaches_dia() -> None:
    route = respx.put(PUT_URL).mock(return_value=httpx.Response(204))
    session = make_session(FakeGate(refuse=CooldownActiveError("cooldown")))
    try:
        with pytest.raises(CooldownActiveError):
            await session.set_postal_code("08001")
    finally:
        await session.aclose()

    assert route.call_count == 0


@respx.mock
async def test_an_akamai_block_on_a_put_is_reported_to_the_gate() -> None:
    respx.put(PUT_URL).mock(
        return_value=httpx.Response(
            403,
            text="<HTML><TITLE>Access Denied</TITLE></HTML>",
            headers={"Content-Type": "text/html"},
        )
    )
    gate = FakeGate()
    session = make_session(gate)
    try:
        with pytest.raises(UpstreamBlockedError):
            await session.set_postal_code("08001")
    finally:
        await session.aclose()

    assert (gate.admitted, gate.blocks) == (1, 1)


# --- Logs carry the path (spec 004, T5) ---


@respx.mock
async def test_retry_lines_name_the_put_path(caplog) -> None:
    respx.put(PUT_URL).mock(side_effect=[httpx.Response(503), httpx.Response(204)])

    with caplog.at_level("INFO"):
        session = await set_postal_code("08001")
        await session.aclose()

    assert any(f"path={SAVE_SHIPPING_ADDRESS_PATH}" in r.getMessage() for r in caplog.records)
