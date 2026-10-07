"""`GET /api/v1/products` — product search (spec 001 RF-1)."""

from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import get_product_service
from app.models.product import ProductQuery, ProductSearchResponse

router = APIRouter(prefix="/api/v1", tags=["products"])


class ProductServiceLike(Protocol):
    async def search(self, query: ProductQuery) -> ProductSearchResponse: ...


@router.get("/products", response_model=ProductSearchResponse)
async def search_products(
    query: Annotated[ProductQuery, Query()],
    service: Annotated[ProductServiceLike, Depends(get_product_service)],
) -> ProductSearchResponse:
    """Search Dia for `query.term` (validated in the route signature, RF-2)."""
    return await service.search(query)
