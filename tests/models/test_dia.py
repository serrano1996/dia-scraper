import math

import pytest
from pydantic import ValidationError

from app.models.dia import DiaProduct, DiaSearchResponse, DiaValidationError
from tests.fixture_data import load_fixture

CLUB_PRODUCT_ID = "274051"  # Jamón serrano, Club Dia offer (Fase 0 §6)


def leche_product() -> dict:
    return load_fixture("dia_search_leche.json")["search_items"][0]


def club_product() -> dict:
    items = load_fixture("dia_search_jamon_promos.json")["search_items"]
    return next(item for item in items if item["object_id"] == CLUB_PRODUCT_ID)


# --- Search envelope ---


def test_real_search_response_validates() -> None:
    response = DiaSearchResponse.model_validate(load_fixture("dia_search_leche.json"))

    assert response.cart.postal_code == "28041"
    assert response.total_items == 417
    assert response.pagination.page_number == 1
    assert response.pagination.page_size == 30
    assert response.pagination.total_pages == 14
    assert len(response.search_items) == 3


def test_real_no_results_response_validates() -> None:
    # The real empty answer has no `facets` at all (Fase 0 §1).
    response = DiaSearchResponse.model_validate(load_fixture("dia_search_no_results.json"))

    assert response.search_items == []
    assert response.total_items == 0
    assert response.pagination.total_pages == 0


@pytest.mark.parametrize("missing", ["search_items", "cart", "pagination", "total_items"])
def test_search_response_without_a_required_key_is_invalid(missing: str) -> None:
    body = load_fixture("dia_search_leche.json")
    del body[missing]

    with pytest.raises(ValidationError):
        DiaSearchResponse.model_validate(body)


def test_search_items_are_kept_raw_so_one_broken_product_does_not_fail_the_page() -> None:
    body = load_fixture("dia_search_leche.json")
    body["search_items"][1] = {"broken": True}

    response = DiaSearchResponse.model_validate(body)

    assert response.search_items[1] == {"broken": True}


# --- Product ---


def test_real_product_validates() -> None:
    product = DiaProduct.model_validate(leche_product())

    assert product.object_id == "504P6"
    assert product.display_name == "Leche semidesnatada Dia Láctea pack 6 x 1 L"
    assert product.prices.price == 4.98
    assert product.prices.price_per_unit == 0.83
    assert product.prices.measure_unit == "LITRO"
    assert product.prices.is_club_price is False
    assert product.image == "/product_images/504P6/504P6_ISO_0_ES.jpg"
    assert product.l2_category_description == "Leche"


@pytest.mark.parametrize("field", ["price", "price_per_unit", "strikethrough_price"])
@pytest.mark.parametrize("value", ["abc", True, math.nan, math.inf])
def test_non_numeric_prices_are_invalid(field: str, value: object) -> None:
    raw = leche_product()
    raw["prices"][field] = value

    with pytest.raises(ValidationError):
        DiaProduct.model_validate(raw)


def test_integer_prices_are_valid() -> None:
    raw = leche_product()
    raw["prices"]["price"] = 5

    assert DiaProduct.model_validate(raw).prices.price == 5.0


@pytest.mark.parametrize(
    "missing",
    ["object_id", "display_name", "prices", "image", "l2_category_description"],
)
def test_product_without_a_required_field_is_invalid(missing: str) -> None:
    raw = leche_product()
    del raw[missing]

    with pytest.raises(ValidationError):
        DiaProduct.model_validate(raw)


@pytest.mark.parametrize("field", ["object_id", "display_name", "image", "l2_category_description"])
def test_product_with_an_empty_required_text_is_invalid(field: str) -> None:
    raw = leche_product()
    raw[field] = ""

    with pytest.raises(ValidationError):
        DiaProduct.model_validate(raw)


@pytest.mark.parametrize("missing", ["price", "price_per_unit", "measure_unit"])
def test_product_without_a_required_price_field_is_invalid(missing: str) -> None:
    raw = leche_product()
    del raw["prices"][missing]

    with pytest.raises(ValidationError):
        DiaProduct.model_validate(raw)


def test_real_club_product_validates() -> None:
    product = DiaProduct.model_validate(club_product())

    assert product.prices.is_club_price is True
    assert product.prices.price == 2.79
    assert product.prices.strikethrough_price == 3.49


def test_club_product_without_strikethrough_price_is_invalid() -> None:
    # Without it we cannot know what a customer without the card pays (spec 001 RF-11).
    raw = club_product()
    del raw["prices"]["strikethrough_price"]

    with pytest.raises(ValidationError):
        DiaProduct.model_validate(raw)


def test_regular_product_without_strikethrough_price_is_valid() -> None:
    raw = leche_product()
    del raw["prices"]["strikethrough_price"]

    assert DiaProduct.model_validate(raw).prices.strikethrough_price is None


# --- Body of the 206 to save-shipping-address (spec 002) ---


def test_real_no_service_body_validates() -> None:
    body = DiaValidationError.model_validate(
        load_fixture("dia_save_shipping_address_no_service.json")
    )

    assert body.type == "VALIDATION_ERROR"
    assert body.message.no_service == "No service for supplied postal code"


@pytest.mark.parametrize(
    "change",
    [
        lambda body: body["message"].pop("no_service"),
        lambda body: body["message"].update(no_service=""),
        lambda body: body.update(type="OTHER_ERROR"),
        lambda body: body.pop("message"),
    ],
    ids=["no-no-service", "empty-no-service", "other-type", "no-message"],
)
def test_other_206_bodies_are_not_a_no_service_answer(change) -> None:
    body = load_fixture("dia_save_shipping_address_no_service.json")
    change(body)

    with pytest.raises(ValidationError):
        DiaValidationError.model_validate(body)
