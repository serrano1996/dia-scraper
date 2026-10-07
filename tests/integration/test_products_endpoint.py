"""End to end: real app, real lifespan, fakeredis, and Dia mocked with respx."""

import httpx
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
