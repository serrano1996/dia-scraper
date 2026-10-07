"""Pydantic schemas for the public API (`app/api/v1`). Same contract as Mercadona and Alcampo."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_serializer

# Up to 100 characters, as in Mercadona (spec 001 RF-2); Dia's own web client
# also caps the term at 100 (Fase 0 §1).
SearchTerm = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
]

# Same limits as Mercadona and Alcampo (spec 001 RF-2). Also keeps us well below
# page 51, from which Dia answers a 404 HTML page (Fase 0 §1).
MAX_PAGE = 20
MAX_PAGE_SIZE = 100

# Exactly 5 ASCII digits. `[0-9]`, not `\d`: `\d` also matches other Unicode
# digits, such as fullwidth ones (U+FF10..U+FF19).
PostalCode = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[0-9]{5}$")]


class ProductQuery(BaseModel):
    """Query parameters for `GET /api/v1/products`."""

    postal_code: PostalCode
    term: SearchTerm
    page: int = Field(default=1, ge=1, le=MAX_PAGE)
    page_size: int = Field(default=50, ge=1, le=MAX_PAGE_SIZE)


class Product(BaseModel):
    """A single product as returned to the API consumer."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    price: float
    price_format: str | None
    # Never null, as in Mercadona.
    image_url: str
    category: str


class SearchMetadata(BaseModel):
    """Metadata describing how a search was performed."""

    model_config = ConfigDict(frozen=True)

    postal_code: str
    term: str
    warehouse: str
    strategy_used: str
    scraped_at: datetime
    total_results: int
    page: int
    page_size: int
    total_pages: int

    @field_serializer("scraped_at")
    def serialize_scraped_at(self, value: datetime) -> str:
        return value.isoformat().replace("+00:00", "Z")


class ProductSearchResponse(BaseModel):
    """Response body of `GET /api/v1/products`."""

    model_config = ConfigDict(frozen=True)

    search: SearchMetadata
    products: list[Product]
