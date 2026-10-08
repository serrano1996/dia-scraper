import copy

import pytest

from app.mappers.product_mapper import map_search
from app.models.dia import DiaSearchResponse
from tests.fixture_data import load_fixture

BASE_URL = "https://www.dia.es"
LECHE_IDS = ["504P6", "608P6", "130063P6"]


def leche_with_items(items: list[dict]) -> DiaSearchResponse:
    body = load_fixture("dia_search_leche.json")
    body["search_items"] = items
    return DiaSearchResponse.model_validate(body)


def leche_items() -> list[dict]:
    return load_fixture("dia_search_leche.json")["search_items"]


def test_map_search_returns_products_in_order() -> None:
    raw = DiaSearchResponse.model_validate(load_fixture("dia_search_leche.json"))

    assert [p.id for p in map_search(raw, base_url=BASE_URL)] == LECHE_IDS


def test_map_search_with_no_results_returns_empty_list() -> None:
    raw = DiaSearchResponse.model_validate(load_fixture("dia_search_no_results.json"))

    assert map_search(raw, base_url=BASE_URL) == []


def test_map_search_deduplicates_by_object_id_keeping_first() -> None:
    items = leche_items()
    duplicate = copy.deepcopy(items[0])
    duplicate["display_name"] = "duplicate, should be discarded"

    products = map_search(leche_with_items([*items, duplicate]), base_url=BASE_URL)

    assert [p.id for p in products] == LECHE_IDS
    assert products[0].name == "Leche semidesnatada Dia Láctea pack 6 x 1 L"


@pytest.mark.parametrize(
    "break_product",
    [
        lambda item: item.pop("display_name"),
        lambda item: item.pop("object_id"),
        lambda item: item["prices"].update(price="abc"),
        lambda item: item.pop("image"),
    ],
    ids=["no-name", "no-id", "non-numeric-price", "no-image"],
)
def test_map_search_discards_broken_products_and_keeps_the_rest(break_product) -> None:
    items = leche_items()
    break_product(items[1])

    products = map_search(leche_with_items(items), base_url=BASE_URL)

    assert [p.id for p in products] == ["504P6", "130063P6"]


def test_map_search_discards_items_that_are_not_objects() -> None:
    items = [*leche_items(), "not a product", None, 42]

    assert [p.id for p in map_search(leche_with_items(items), base_url=BASE_URL)] == LECHE_IDS


# --- Logs of discarded products (spec 004 RF-13, RF-14, T8) ---

MAPPER = "app.mappers.product_mapper"


def mapper_records(caplog: pytest.LogCaptureFixture) -> list:
    return [r for r in caplog.records if r.name == MAPPER]


def test_some_discarded_products_are_one_warning_with_their_ids(caplog) -> None:
    items = leche_items()
    items[1]["prices"]["price"] = "abc"

    with caplog.at_level("INFO", logger=MAPPER):
        map_search(leche_with_items(items), base_url=BASE_URL)

    [record] = mapper_records(caplog)
    assert record.levelname == "WARNING"
    assert "discarded=1" in record.getMessage()
    assert "total=3" in record.getMessage()
    assert "'608P6'" in record.getMessage()


def test_every_product_discarded_is_an_error(caplog) -> None:
    items = leche_items()
    for item in items:
        del item["display_name"]

    with caplog.at_level("INFO", logger=MAPPER):
        products = map_search(leche_with_items(items), base_url=BASE_URL)

    assert products == []
    [record] = mapper_records(caplog)
    assert record.levelname == "ERROR"
    assert "discarded=3" in record.getMessage()


def test_no_results_log_nothing(caplog) -> None:
    raw = DiaSearchResponse.model_validate(load_fixture("dia_search_no_results.json"))

    with caplog.at_level("INFO", logger=MAPPER):
        map_search(raw, base_url=BASE_URL)

    assert mapper_records(caplog) == []


def test_duplicates_are_not_discards(caplog) -> None:
    items = leche_items()

    with caplog.at_level("INFO", logger=MAPPER):
        map_search(leche_with_items([*items, copy.deepcopy(items[0])]), base_url=BASE_URL)

    assert mapper_records(caplog) == []


def test_items_without_a_readable_id_are_counted(caplog) -> None:
    items = [*leche_items(), "not a product"]

    with caplog.at_level("INFO", logger=MAPPER):
        map_search(leche_with_items(items), base_url=BASE_URL)

    [record] = mapper_records(caplog)
    assert "discarded=1" in record.getMessage()
    assert "ids=[]" in record.getMessage()
