import logging

import pytest

from app.mappers.product_mapper import UNIT_SUFFIXES, format_unit_price

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
