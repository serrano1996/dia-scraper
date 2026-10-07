"""End to end, spec 002: one Dia session per postal code (real app, fakeredis, respx)."""

import httpx
import respx

from tests.fixture_data import load_fixture
from tests.integration.conftest import Harness, mock_dia_put, mock_dia_search

URL = "/api/v1/products"
AKAMAI_403 = "<HTML><HEAD>\n<TITLE>Access Denied</TITLE>\n</HEAD><BODY>\n</BODY>\n</HTML>\n"


def search_body(postal_code: str) -> dict:
    body = load_fixture("dia_search_leche.json")
    body["cart"]["postal_code"] = postal_code
    return body


def no_service() -> httpx.Response:
    return httpx.Response(206, json=load_fixture("dia_save_shipping_address_no_service.json"))


def test_a_new_postal_code_costs_a_put_then_one_request_per_search(
    harness: Harness, respx_mock: respx.MockRouter
) -> None:
    put = mock_dia_put(respx_mock, httpx.Response(204))
    search = mock_dia_search(respx_mock, json_body=search_body("08001"))

    first = harness.client.get(URL, params={"postal_code": "08001", "term": "leche"})
    other_term = harness.client.get(URL, params={"postal_code": "08001", "term": "agua"})
    repeated = harness.client.get(URL, params={"postal_code": "08001", "term": "leche"})

    assert [r.status_code for r in (first, other_term, repeated)] == [200, 200, 200]
    assert first.json()["search"]["warehouse"] == "08001"
    assert put.call_count == 1
    assert dict(httpx.QueryParams(put.calls.last.request.url.query)) == {"new_postal_code": "08001"}
    assert search.call_count == 2  # the repeated search came from the cache


def test_dias_default_postal_code_needs_no_put(
    harness: Harness, respx_mock: respx.MockRouter
) -> None:
    put = mock_dia_put(respx_mock)
    mock_dia_search(respx_mock, json_body=search_body("28041"))

    response = harness.client.get(URL, params={"postal_code": "28041", "term": "leche"})

    assert response.status_code == 200
    assert put.call_count == 0


def test_a_postal_code_dia_does_not_serve_answers_404_and_is_remembered(
    harness: Harness, respx_mock: respx.MockRouter
) -> None:
    put = mock_dia_put(respx_mock, no_service())
    search = mock_dia_search(respx_mock, json_body=search_body("35001"))

    first = harness.client.get(URL, params={"postal_code": "35001", "term": "leche"})
    second = harness.client.get(URL, params={"postal_code": "35001", "term": "agua"})

    assert first.status_code == second.status_code == 404
    assert second.json() == {"detail": "Postal code not served by Dia"}
    assert put.call_count == 1
    assert search.call_count == 0
    assert harness.cache_keys() == [b"postal_code:not_served:35001"]


def test_a_stale_session_is_replaced_and_the_search_repeated_once(
    harness: Harness, respx_mock: respx.MockRouter
) -> None:
    put = mock_dia_put(respx_mock, httpx.Response(204), httpx.Response(204))
    search = respx_mock.get("https://dia.test/api/v1/search-back/search/reduced").mock(
        side_effect=[
            httpx.Response(200, json=search_body("28041")),  # session lost: Dia's default
            httpx.Response(200, json=search_body("08001")),
        ]
    )

    response = harness.client.get(URL, params={"postal_code": "08001", "term": "leche"})

    assert response.status_code == 200
    assert response.json()["search"]["warehouse"] == "08001"
    assert put.call_count == 2
    assert search.call_count == 2


def test_another_postal_codes_data_never_reaches_the_client(
    harness: Harness, respx_mock: respx.MockRouter
) -> None:
    mock_dia_put(respx_mock, httpx.Response(204), httpx.Response(204))
    mock_dia_search(respx_mock, json_body=search_body("28041"))

    response = harness.client.get(URL, params={"postal_code": "08001", "term": "leche"})

    assert response.status_code == 502
    assert harness.cache_keys() == []


def test_a_put_blocked_by_akamai_answers_502_after_a_single_call(
    harness: Harness, respx_mock: respx.MockRouter
) -> None:
    put = mock_dia_put(
        respx_mock,
        httpx.Response(403, text=AKAMAI_403, headers={"Content-Type": "text/html"}),
    )
    search = mock_dia_search(respx_mock, json_body=search_body("08001"))

    response = harness.client.get(URL, params={"postal_code": "08001", "term": "leche"})

    assert response.status_code == 502
    assert response.json() == {"detail": "Upstream service unavailable"}
    assert put.call_count == 1
    assert search.call_count == 0
    assert harness.cache_keys() == []
