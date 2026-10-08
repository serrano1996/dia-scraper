"""Maps Dia's raw products to the public API schema."""

import logging
from types import MappingProxyType

from pydantic import JsonValue, ValidationError

from app.models.dia import DiaProduct, DiaSearchResponse
from app.models.product import Product

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


def map_product(raw: DiaProduct, *, base_url: str) -> Product:
    """Map one validated Dia product to the API schema (RF-8, RF-10)."""
    prices = raw.prices
    # DiaPrices guarantees `strikethrough_price` whenever `is_club_price` is true.
    if prices.is_club_price and prices.strikethrough_price is not None:
        # `price` is the Club Dia card price; everyone else pays `strikethrough_price`,
        # and Dia's `price_per_unit` follows the card price, so it is dropped (spec-D3, D8).
        price = prices.strikethrough_price
        price_format = None
    else:
        price = prices.price
        price_format = format_unit_price(prices.price_per_unit, prices.measure_unit)
    return Product(
        id=raw.object_id,
        name=raw.display_name,
        price=price,
        price_format=price_format,
        image_url=f"{base_url.rstrip('/')}/{raw.image.lstrip('/')}",
        category=raw.l2_category_description,
    )


def map_search(raw: DiaSearchResponse, *, base_url: str) -> list[Product]:
    """Map every valid product, in Dia's order (RF-5, RF-11).

    Each item is validated on its own: a broken one is discarded instead of
    failing the whole page (plan-D1). A repeated `object_id` keeps its first
    appearance.
    """
    products: list[Product] = []
    seen: set[str] = set()
    discarded: list[str | None] = []
    for item in raw.search_items:
        try:
            product = DiaProduct.model_validate(item)
        except ValidationError:
            discarded.append(_raw_object_id(item))
            continue
        if product.object_id in seen:
            continue
        seen.add(product.object_id)
        products.append(map_product(product, base_url=base_url))
    _log_discarded(discarded, total=len(raw.search_items))
    return products


def _raw_object_id(item: JsonValue) -> str | None:
    """The `object_id` of a broken item, when it can be read at all."""
    if isinstance(item, dict):
        object_id = item.get("object_id")
        if isinstance(object_id, str):
            return object_id
    return None


def _log_discarded(discarded: list[str | None], *, total: int) -> None:
    """One line per response, never one per product (spec 004 RF-13, RF-14)."""
    if not discarded:
        return
    ids = [object_id for object_id in discarded if object_id is not None]
    if len(discarded) == total:
        # Not "no results": Dia sent products and none fits. A format change (spec-D4).
        logger.error(
            "every product discarded discarded=%d total=%d ids=%r", len(discarded), total, ids
        )
    else:
        logger.warning(
            "products discarded discarded=%d total=%d ids=%r", len(discarded), total, ids
        )
