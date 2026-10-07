"""Maps Dia's raw products to the public API schema."""

import logging
from types import MappingProxyType

logger = logging.getLogger(__name__)

# `measure_unit` -> suffix of `price_format`. Only units seen live (spec-D5,
# Fase 0 §6); any other gives `price_format: null` and a warning (RF-10).
UNIT_SUFFIXES = MappingProxyType(
    {
        "LITRO": "L",
        "KILO": "kg",
        "UNIDAD": "ud",
        "DOCENA": "docena",
        "LAVADO": "lavado",
        "100 ML.": "100 ml",
        "100 GR.": "100 g",
    }
)


def format_unit_price(price_per_unit: float, measure_unit: str) -> str | None:
    """`0.83, "LITRO"` -> `"0.83 €/L"`, always with two decimals (RF-9, plan-D3)."""
    suffix = UNIT_SUFFIXES.get(measure_unit)
    if suffix is None:
        logger.warning("unknown measure unit %r: price_format set to null", measure_unit)
        return None
    return f"{price_per_unit:.2f} €/{suffix}"
