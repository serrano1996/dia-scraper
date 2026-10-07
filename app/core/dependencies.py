"""FastAPI dependency providers, centralized as required by the constitution.

Everything stateful is built once in the `lifespan` (`app.main`) and read
through the typed `resources(app)`; a provider only assembles the per-request
objects around it.
"""

from fastapi import Request

from app.core.state import resources
from app.scrapers.dia_search import DiaSearchScraper
from app.services.product_service import ProductService
from app.services.search_cache import SearchCacheRepository


def get_product_service(request: Request) -> ProductService:
    """Build a `ProductService` from the resources created in the `lifespan`."""
    res = resources(request.app)
    return ProductService(
        scraper=DiaSearchScraper(settings=res.settings),
        cache=SearchCacheRepository(res.redis),
        http_client=res.http_client,
        settings=res.settings,
    )
