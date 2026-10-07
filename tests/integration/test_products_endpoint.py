"""End to end: real app, real lifespan, fakeredis, and Dia mocked with respx."""

import httpx
import pytest
import respx

from app.models.product import ProductSearchResponse
from tests.fixture_data import load_fixture
from tests.integration.conftest import Harness, mock_dia_search

URL = "/api/v1/products"
PARAMS = {"postal_code": "28001", "term": "leche"}


def test_miss_answers_200_from_a_single_call_to_dia(
    harness: Harness, respx_mock: respx.MockRouter
) -> None:
    route = mock_dia_search(respx_mock, json_body=load_fixture("dia_search_leche.json"))

    response = harness.client.get(URL, params=PARAMS | {"page": 2, "page_size": 30})

    assert response.status_code == 200
    body = ProductSearchResponse.model_validate(response.json())
    assert [p.id for p in body.products] == ["504P6", "608P6", "130063P6"]
    assert body.search.warehouse == "28041"
    assert body.search.total_results == 417
    assert route.call_count == 1
    request = route.calls.last.request
    assert dict(httpx.QueryParams(request.url.query)) == {
        "q": "leche",
        "page": "2",
        "page_size": "30",
    }
    # Also proves `cache_keys` sees the app's Redis, so the "== []" checks below bite.
    assert harness.cache_keys() == [b"search:28041:leche:2:30"]


def test_requests_to_dia_carry_the_chrome_headers(
    harness: Harness, respx_mock: respx.MockRouter
) -> None:
    route = mock_dia_search(respx_mock, json_body=load_fixture("dia_search_leche.json"))

    harness.client.get(URL, params=PARAMS)

    headers = route.calls.last.request.headers
    assert "Chrome/155." in headers["User-Agent"]
    assert '"Google Chrome";v="155"' in headers["sec-ch-ua"]
    assert headers["Sec-Fetch-Mode"] == "cors"
    assert headers["Referer"] == "https://www.dia.es/"


def test_a_repeated_search_is_served_from_cache(
    harness: Harness, respx_mock: respx.MockRouter
) -> None:
    route = mock_dia_search(respx_mock, json_body=load_fixture("dia_search_leche.json"))

    first = harness.client.get(URL, params=PARAMS)
    second = harness.client.get(URL, params=PARAMS)

    assert second.status_code == 200
    assert second.json() == first.json()
    assert route.call_count == 1


def test_another_casing_of_the_term_is_a_hit(
    harness: Harness, respx_mock: respx.MockRouter
) -> None:
    route = mock_dia_search(respx_mock, json_body=load_fixture("dia_search_leche.json"))

    harness.client.get(URL, params=PARAMS)
    response = harness.client.get(URL, params=PARAMS | {"term": "LECHE"})

    assert response.json()["search"]["term"] == "LECHE"
    assert route.call_count == 1


def test_a_search_without_results_answers_an_empty_list(
    harness: Harness, respx_mock: respx.MockRouter
) -> None:
    mock_dia_search(respx_mock, json_body=load_fixture("dia_search_no_results.json"))

    response = harness.client.get(URL, params=PARAMS | {"term": "xqzwvkjhgf"})

    assert response.status_code == 200
    assert response.json()["products"] == []
    assert response.json()["search"]["total_pages"] == 0


# --- Validation and errors (T18) ---

AKAMAI_403 = "<HTML><HEAD>\n<TITLE>Access Denied</TITLE>\n</HEAD><BODY>\n</BODY>\n</HTML>\n"


@pytest.mark.parametrize(
    "params",
    [
        {"term": "leche"},
        {"postal_code": "28001"},
        PARAMS | {"term": "   "},
        PARAMS | {"term": "x" * 101},
        PARAMS | {"postal_code": "2800"},
        PARAMS | {"postal_code": "28001a"},
        PARAMS | {"page": 0},
        PARAMS | {"page": 21},
        PARAMS | {"page_size": 0},
        PARAMS | {"page_size": 101},
    ],
    ids=[
        "no-postal-code",
        "no-term",
        "blank-term",
        "long-term",
        "short-postal-code",
        "letter-in-postal-code",
        "page-0",
        "page-21",
        "page-size-0",
        "page-size-101",
    ],
)
def test_invalid_queries_answer_422_without_touching_dia_or_redis(
    harness: Harness, respx_mock: respx.MockRouter, params: dict
) -> None:
    route = mock_dia_search(respx_mock, json_body=load_fixture("dia_search_leche.json"))

    response = harness.client.get(URL, params=params)

    assert response.status_code == 422
    assert route.call_count == 0
    assert harness.cache_keys() == []


def test_an_empty_page_past_the_first_answers_404_and_is_not_cached(
    harness: Harness, respx_mock: respx.MockRouter
) -> None:
    body = load_fixture("dia_search_leche.json")
    body["search_items"] = []
    mock_dia_search(respx_mock, json_body=body)

    response = harness.client.get(URL, params=PARAMS | {"page": 15})

    assert response.status_code == 404
    assert response.json() == {"detail": "Page out of range"}
    assert harness.cache_keys() == []


def test_an_akamai_block_answers_502_after_a_single_call(
    harness: Harness, respx_mock: respx.MockRouter
) -> None:
    route = mock_dia_search(
        respx_mock, status_code=403, text=AKAMAI_403, headers={"Content-Type": "text/html"}
    )

    response = harness.client.get(URL, params=PARAMS)

    assert response.status_code == 502
    assert response.json() == {"detail": "Upstream service unavailable"}
    assert route.call_count == 1
    assert harness.cache_keys() == []


def test_a_dia_404_answers_502_after_a_single_call(
    harness: Harness, respx_mock: respx.MockRouter
) -> None:
    route = mock_dia_search(
        respx_mock, status_code=404, text="<h1>404 - Not Found</h1><p>Bloqueado</p>"
    )

    response = harness.client.get(URL, params=PARAMS)

    assert response.status_code == 502
    assert "Bloqueado" not in response.text
    assert route.call_count == 1
    assert harness.cache_keys() == []


def test_persistent_server_errors_answer_502_after_every_attempt(
    harness: Harness, respx_mock: respx.MockRouter
) -> None:
    route = mock_dia_search(respx_mock, status_code=503, json_body={"error": "down"})

    response = harness.client.get(URL, params=PARAMS)

    assert response.status_code == 502
    assert response.json() == {"detail": "Upstream service unavailable"}
    assert route.call_count == 3  # RETRY_MAX_ATTEMPTS default
    assert harness.cache_keys() == []


# --- Page sizes below Dia's minimum of 30 (T21) ---


def test_a_small_page_is_cut_from_the_dia_page_that_holds_it(
    harness: Harness, respx_mock: respx.MockRouter
) -> None:
    body = load_fixture("dia_search_leche.json")
    template = body["search_items"][0]
    body["search_items"] = [template | {"object_id": f"P{n:02d}"} for n in range(1, 31)]
    body["total_items"] = 418
    route = mock_dia_search(respx_mock, json_body=body)

    response = harness.client.get(URL, params=PARAMS | {"page": 2, "page_size": 5})

    assert response.status_code == 200
    query = dict(httpx.QueryParams(route.calls.last.request.url.query))
    assert (query["page"], query["page_size"]) == ("1", "30")
    assert [p["id"] for p in response.json()["products"]] == ["P06", "P07", "P08", "P09", "P10"]
    assert response.json()["search"]["total_pages"] == 20
