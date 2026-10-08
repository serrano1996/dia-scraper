import httpx
import pytest
import respx

from app.core.config import Settings
from app.exceptions import CooldownActiveError, UpstreamBlockedError, UpstreamUnavailableError
from app.models.dia import DiaSearchResponse
from app.scrapers.dia_search import DEFAULT_POSTAL_CODE, SEARCH_PATH, DiaSearchScraper
from tests.fixture_data import load_fixture
from tests.scrapers.gate_doubles import FakeGate

BASE_URL = "https://dia.test"
SEARCH_URL = f"{BASE_URL}/api/v1/search-back/search/reduced"


def make_scraper(
    *, max_attempts: int = 3, gate: FakeGate | None = None
) -> tuple[DiaSearchScraper, httpx.AsyncClient]:
    settings = Settings(
        _env_file=None,
        dia_base_url=BASE_URL,
        redis_url="redis://localhost:6379/0",
        retry_max_attempts=max_attempts,
        retry_base_delay=0,
        retry_jitter_max_s=0,
    )
    return DiaSearchScraper(settings=settings, gate=gate), httpx.AsyncClient(base_url=BASE_URL)


async def search(term: str = "leche", *, page: int = 1, page_size: int = 50) -> DiaSearchResponse:
    scraper, client = make_scraper()
    try:
        return await scraper.search(term, page=page, page_size=page_size, client=client)
    finally:
        await client.aclose()


def test_search_path_and_default_postal_code_match_fase_0() -> None:
    assert SEARCH_PATH == "/api/v1/search-back/search/reduced"
    assert DEFAULT_POSTAL_CODE == "28041"


@respx.mock
async def test_search_calls_dia_exactly_once_with_term_page_and_page_size() -> None:
    route = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=load_fixture("dia_search_leche.json"))
    )

    response = await search("leche", page=2, page_size=50)

    assert route.call_count == 1
    query = httpx.QueryParams(route.calls.last.request.url.query)
    assert dict(query) == {"q": "leche", "page": "2", "page_size": "50"}
    assert isinstance(response, DiaSearchResponse)
    assert response.total_items == 417
    assert len(response.search_items) == 3


@respx.mock
async def test_search_sends_accented_terms_encoded() -> None:
    route = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=load_fixture("dia_search_no_results.json"))
    )

    await search("plátano maduro")

    request = route.calls.last.request
    assert b"pl%C3%A1tano" in request.url.raw_path
    assert httpx.QueryParams(request.url.query)["q"] == "plátano maduro"


@respx.mock
@pytest.mark.parametrize(
    "upstream",
    [
        httpx.Response(200, html="<html>not json</html>"),
        httpx.Response(200, json={"cart": {"postal_code": "28041"}}),
        httpx.Response(200, json=["not", "an", "object"]),
    ],
    ids=["html", "no-search-items", "json-array"],
)
async def test_search_raises_on_an_unexpected_body(upstream: httpx.Response) -> None:
    respx.get(SEARCH_URL).mock(return_value=upstream)

    with pytest.raises(UpstreamUnavailableError) as exc_info:
        await search()

    assert type(exc_info.value) is UpstreamUnavailableError


@respx.mock
async def test_akamai_block_is_raised_after_a_single_call() -> None:
    route = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            403,
            text="<HTML><TITLE>Access Denied</TITLE></HTML>",
            headers={"Content-Type": "text/html"},
        )
    )

    with pytest.raises(UpstreamBlockedError):
        await search()

    assert route.call_count == 1


@respx.mock
async def test_not_found_html_is_an_upstream_failure_after_a_single_call() -> None:
    # What Dia answers from page 51 on (Fase 0 §1).
    route = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(404, html="<h1>404 - Not Found</h1><p>Bloqueado</p>")
    )

    with pytest.raises(UpstreamUnavailableError) as exc_info:
        await search()

    assert exc_info.value.status_code == 404
    assert route.call_count == 1


@respx.mock
async def test_exhausted_transport_errors_never_leak_httpx_types() -> None:
    respx.get(SEARCH_URL).mock(side_effect=httpx.ConnectError("down"))

    with pytest.raises(UpstreamUnavailableError) as exc_info:
        await search()

    assert not isinstance(exc_info.value, httpx.HTTPError)


# --- The outbound gate (spec 003, T9) ---


@respx.mock
async def test_a_search_refused_by_the_gate_never_reaches_dia() -> None:
    route = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=load_fixture("dia_search_leche.json"))
    )
    scraper, client = make_scraper(gate=FakeGate(refuse=CooldownActiveError("cooldown")))

    try:
        with pytest.raises(CooldownActiveError):
            await scraper.search("leche", page=1, page_size=50, client=client)
    finally:
        await client.aclose()

    assert route.call_count == 0


@respx.mock
async def test_an_akamai_block_on_a_search_is_reported_to_the_gate() -> None:
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            403,
            text="<HTML><TITLE>Access Denied</TITLE></HTML>",
            headers={"Content-Type": "text/html"},
        )
    )
    gate = FakeGate()
    scraper, client = make_scraper(gate=gate)

    try:
        with pytest.raises(UpstreamBlockedError):
            await scraper.search("leche", page=1, page_size=50, client=client)
    finally:
        await client.aclose()

    assert (gate.admitted, gate.blocks) == (1, 1)


# --- Logs carry the path, never the term (spec 004 spec-D5, T5) ---


@respx.mock
async def test_retry_lines_name_the_path_without_the_term(caplog) -> None:
    respx.get(SEARCH_URL).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, json=load_fixture("dia_search_leche.json")),
        ]
    )

    with caplog.at_level("INFO"):
        await search("leche secreta")

    # Our own lines; httpx's are silenced by configure_logging (tests/core/test_logging.py).
    lines = [r.getMessage() for r in caplog.records if r.name.startswith("app.")]
    assert any(f"path={SEARCH_PATH}" in line for line in lines)
    assert not any("secreta" in line or "q=" in line for line in lines)
