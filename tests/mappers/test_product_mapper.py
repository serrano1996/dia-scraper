import logging

import pytest

from app.mappers.product_mapper import UNIT_SUFFIXES, format_unit_price, map_product
from app.models.dia import DiaProduct
from app.models.product import Product
from tests.fixture_data import load_fixture

BASE_URL = "https://www.dia.es"


def raw_product(fixture: str, object_id: str | None = None) -> DiaProduct:
    items = load_fixture(fixture)["search_items"]
    item = items[0] if object_id is None else next(i for i in items if i["object_id"] == object_id)
    return DiaProduct.model_validate(item)


# --- format_unit_price ---


def test_format_unit_price_litre() -> None:
    assert format_unit_price(0.83, "LITRO") == "0.83 €/L"


def test_format_unit_price_always_shows_two_decimals() -> None:
    assert format_unit_price(0.8, "LITRO") == "0.80 €/L"
    assert format_unit_price(16.99, "KILO") == "16.99 €/kg"
    assert format_unit_price(3, "DOCENA") == "3.00 €/docena"


@pytest.mark.parametrize(
    ("measure_unit", "suffix"),
    [
        ("LITRO", "L"),
        ("KILO", "kg"),
        ("UNIDAD", "ud"),
        ("DOCENA", "docena"),
        ("LAVADO", "lavado"),
        ("100 ML.", "100 ml"),
        ("100 GR.", "100 g"),
    ],
)
def test_format_unit_price_every_unit_seen_live(measure_unit: str, suffix: str) -> None:
    # The 7 units of spec-D5, all seen in Fase 0 §6.
    assert format_unit_price(0.07, measure_unit) == f"0.07 €/{suffix}"


def test_unit_table_has_only_the_units_seen_live() -> None:
    assert len(UNIT_SUFFIXES) == 7


def test_format_unit_price_unknown_unit_is_none_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="app.mappers.product_mapper"):
        assert format_unit_price(1.5, "BOTELLA") is None

    assert "BOTELLA" in caplog.text


# --- map_product ---


def test_map_product_real_leche() -> None:
    product = map_product(raw_product("dia_search_leche.json"), base_url=BASE_URL)

    assert product == Product(
        id="504P6",
        name="Leche semidesnatada Dia Láctea pack 6 x 1 L",
        price=4.98,
        price_format="0.83 €/L",
        image_url="https://www.dia.es/product_images/504P6/504P6_ISO_0_ES.jpg",
        category="Leche",
    )


def test_map_product_club_offer_uses_the_price_without_card() -> None:
    # Jamón serrano: 2.79 with the Club Dia card, 3.49 without (spec-D3, spec-D8).
    product = map_product(raw_product("dia_search_jamon_promos.json", "274051"), base_url=BASE_URL)

    assert product.price == 3.49
    assert product.price_format is None


def test_map_product_offer_for_everyone_uses_the_reduced_price() -> None:
    # Jamón cocido: 1.49 for everyone, 1.99 before the offer.
    product = map_product(raw_product("dia_search_jamon_promos.json", "273737"), base_url=BASE_URL)

    assert product.price == 1.49
    assert product.price_format == "9.93 €/kg"


def test_map_product_regular_price() -> None:
    product = map_product(raw_product("dia_search_jamon_promos.json", "274059"), base_url=BASE_URL)

    assert product.price == 2.21
    assert product.price_format == "14.73 €/kg"


@pytest.mark.parametrize(
    ("fixture", "object_id", "price_format"),
    [
        ("dia_search_huevos_units.json", "306230", "3.05 €/docena"),
        ("dia_search_detergente_lavado.json", "273998", "0.07 €/lavado"),
        ("dia_search_platano_100ml.json", "290122", "0.96 €/100 ml"),
        ("dia_search_jamon_promos.json", "308293", "1.18 €/100 g"),
    ],
)
def test_map_product_real_units(fixture: str, object_id: str, price_format: str) -> None:
    product = map_product(raw_product(fixture, object_id), base_url=BASE_URL)

    assert product.price_format == price_format


@pytest.mark.parametrize("base_url", ["https://www.dia.es", "https://www.dia.es/"])
def test_map_product_builds_the_image_url_without_double_slash(base_url: str) -> None:
    product = map_product(raw_product("dia_search_leche.json"), base_url=base_url)

    assert product.image_url == "https://www.dia.es/product_images/504P6/504P6_ISO_0_ES.jpg"
