from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models.product import (
    MAX_PAGE,
    MAX_PAGE_SIZE,
    Product,
    ProductQuery,
    ProductSearchResponse,
    SearchMetadata,
)

# 28001 in fullwidth digits (U+FF10..U+FF19), which `\d` would accept.
FULLWIDTH_28001 = "".join(chr(0xFF10 + int(digit)) for digit in "28001")


def make_product(**overrides: object) -> Product:
    fields: dict[str, object] = {
        "id": "504P6",
        "name": "Leche semidesnatada Dia Láctea pack 6 x 1 L",
        "price": 4.98,
        "price_format": "0.83 €/L",
        "image_url": "https://www.dia.es/product_images/504P6/504P6_ISO_0_ES.jpg",
        "category": "Leche",
    }
    return Product.model_validate(fields | overrides)


def make_metadata() -> SearchMetadata:
    return SearchMetadata(
        postal_code="28001",
        term="leche",
        warehouse="28041",
        strategy_used="api",
        scraped_at=datetime(2026, 10, 7, 9, 0, tzinfo=UTC),
        total_results=417,
        page=1,
        page_size=50,
        total_pages=9,
    )


# --- ProductQuery ---


def test_query_strips_whitespace_and_has_mercadona_defaults() -> None:
    query = ProductQuery(postal_code=" 28001 ", term="  leche ")

    assert query.postal_code == "28001"
    assert query.term == "leche"
    assert query.page == 1
    assert query.page_size == 50


@pytest.mark.parametrize("term", ["", "   ", "x" * 101])
def test_query_rejects_invalid_terms(term: str) -> None:
    with pytest.raises(ValidationError):
        ProductQuery(postal_code="28001", term=term)


def test_query_accepts_a_term_of_exactly_100_characters() -> None:
    assert len(ProductQuery(postal_code="28001", term="x" * 100).term) == 100


@pytest.mark.parametrize("postal_code", ["", "2800", "280011", "28a01", FULLWIDTH_28001])
def test_query_rejects_postal_codes_that_are_not_five_ascii_digits(postal_code: str) -> None:
    with pytest.raises(ValidationError):
        ProductQuery(postal_code=postal_code, term="leche")


@pytest.mark.parametrize(
    ("page", "page_size"),
    [(0, 50), (MAX_PAGE + 1, 50), (1, 0), (1, MAX_PAGE_SIZE + 1)],
)
def test_query_rejects_out_of_range_pagination(page: int, page_size: int) -> None:
    with pytest.raises(ValidationError):
        ProductQuery(postal_code="28001", term="leche", page=page, page_size=page_size)


def test_query_accepts_the_pagination_limits() -> None:
    query = ProductQuery(postal_code="28001", term="leche", page=20, page_size=100)

    assert (query.page, query.page_size) == (20, 100)
    assert (MAX_PAGE, MAX_PAGE_SIZE) == (20, 100)


# --- Product ---


def test_product_accepts_a_null_price_format() -> None:
    assert make_product(price_format=None).price_format is None


@pytest.mark.parametrize("field", ["image_url", "category"])
def test_product_rejects_null_image_url_and_category(field: str) -> None:
    # Never null, as in Mercadona (spec 001 §6).
    with pytest.raises(ValidationError):
        make_product(**{field: None})


# --- ProductSearchResponse ---


def test_response_serializes_scraped_at_with_z_suffix() -> None:
    response = ProductSearchResponse(search=make_metadata(), products=[make_product()])

    body = response.model_dump(mode="json")

    assert body["search"]["scraped_at"] == "2026-10-07T09:00:00Z"
    assert body["products"][0]["id"] == "504P6"


def test_response_round_trips_through_json() -> None:
    response = ProductSearchResponse(search=make_metadata(), products=[make_product()])

    assert ProductSearchResponse.model_validate_json(response.model_dump_json()) == response
