"""End to end, spec 004: request id on every line, no secrets, no forged lines."""

import logging

import httpx
import pytest
import respx

from tests.fixture_data import load_fixture
from tests.integration.conftest import Harness, mock_dia_search

URL = "/api/v1/products"
PARAMS = {"postal_code": "28041", "term": "leche"}
# Synthetic, same shape as Dia's real one (constitution #12).
SESSION_ID = "11111111-2222-4333-8444-555555555555"


def app_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name.startswith("app.")]


def test_every_line_of_a_failing_search_carries_its_request_id(
    harness: Harness, respx_mock: respx.MockRouter, caplog: pytest.LogCaptureFixture
) -> None:
    mock_dia_search(respx_mock, status_code=503, json_body={"error": "down"})

    with caplog.at_level(logging.INFO):
        response = harness.client.get(URL, params=PARAMS)

    assert response.status_code == 502
    request_id = response.headers["X-Request-ID"]
    records = app_records(caplog)
    messages = [r.getMessage() for r in records]
    assert any(m.startswith("retrying ") for m in messages)
    assert any(m.startswith("retries exhausted ") for m in messages)
    assert any(m.startswith("upstream unavailable ") for m in messages)
    assert {r.request_id for r in records} == {request_id}


def test_no_line_ever_carries_dias_cookies(
    harness: Harness, respx_mock: respx.MockRouter, caplog: pytest.LogCaptureFixture
) -> None:
    respx_mock.get("https://dia.test/api/v1/search-back/search/reduced").mock(
        return_value=httpx.Response(
            200,
            json=load_fixture("dia_search_leche.json"),
            headers={"Set-Cookie": f"session_id={SESSION_ID}; Path=/"},
        )
    )

    with caplog.at_level(logging.DEBUG):
        harness.client.get(URL, params=PARAMS)
        harness.client.get(URL, params=PARAMS | {"term": "agua"})

    assert all(SESSION_ID not in r.getMessage() for r in caplog.records)


def test_a_term_with_a_newline_stays_on_one_line(
    harness: Harness, respx_mock: respx.MockRouter, caplog: pytest.LogCaptureFixture
) -> None:
    mock_dia_search(respx_mock, json_body=load_fixture("dia_search_no_results.json"))

    with caplog.at_level(logging.INFO):
        harness.client.get(URL, params=PARAMS | {"term": "leche\nERROR forged line"})

    assert all("\n" not in r.getMessage() for r in app_records(caplog))


def test_httpx_does_not_log_dia_urls_once_the_app_runs(
    harness: Harness, respx_mock: respx.MockRouter, caplog: pytest.LogCaptureFixture
) -> None:
    mock_dia_search(respx_mock, json_body=load_fixture("dia_search_leche.json"))

    with caplog.at_level(logging.INFO):
        harness.client.get(URL, params=PARAMS | {"term": "termino secreto"})

    assert not [r for r in caplog.records if r.name == "httpx"]
    # No line carries a URL to Dia, whose query has the client's term. (The test
    # client's own line, logged by `httpx2` for the request to our API, is not one.)
    assert all("dia.test" not in r.getMessage() for r in caplog.records)
