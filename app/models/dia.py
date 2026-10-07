"""Pydantic schemas for Dia's raw search response (`/api/v1/search-back/search/reduced`).

Only the fields the API needs are declared; the rest are ignored (Fase 0 §6).
"""

from typing import Annotated, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    FiniteFloat,
    JsonValue,
    model_validator,
)


def _reject_bool(value: object) -> object:
    """Pydantic turns `true` into `1.0` in lax mode; a boolean is never a price."""
    if isinstance(value, bool):
        raise ValueError("a price must be a number, not a boolean")
    return value


# Dia sends prices as JSON numbers (Fase 0 §6). NaN and infinity are rejected.
Price = Annotated[FiniteFloat, BeforeValidator(_reject_bool)]


class DiaPrices(BaseModel):
    """`prices` of a product."""

    model_config = ConfigDict(extra="ignore")

    price: Price
    price_per_unit: Price
    measure_unit: str = Field(min_length=1)
    strikethrough_price: Price | None = None
    is_club_price: bool = False

    @model_validator(mode="after")
    def _club_price_needs_the_regular_one(self) -> Self:
        """With a Club Dia price, `strikethrough_price` is what everyone else pays (RF-11)."""
        if self.is_club_price and self.strikethrough_price is None:
            raise ValueError("a Club Dia price needs strikethrough_price")
        return self


class DiaProduct(BaseModel):
    """One item of `search_items`."""

    model_config = ConfigDict(extra="ignore")

    object_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    prices: DiaPrices
    # Relative path, such as `/product_images/504P6/504P6_ISO_0_ES.jpg`.
    image: str = Field(min_length=1)
    l2_category_description: str = Field(min_length=1)


class DiaCart(BaseModel):
    """`cart` of the anonymous session: carries the postal code Dia searched with."""

    model_config = ConfigDict(extra="ignore")

    postal_code: str = Field(min_length=1)


class DiaPagination(BaseModel):
    """`pagination` of the search response."""

    model_config = ConfigDict(extra="ignore")

    page_number: int
    page_size: int
    total_pages: int


class DiaSearchResponse(BaseModel):
    """The search response envelope.

    Products stay raw (`JsonValue`) and are validated one by one by the mapper,
    so a single broken product does not fail the whole page (plan-D1).
    """

    model_config = ConfigDict(extra="ignore")

    cart: DiaCart
    pagination: DiaPagination
    total_items: int
    search_items: list[JsonValue]
