"""Application factory and lifespan: creates and closes the shared clients."""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

import redis.asyncio as redis
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.v1.products import router as products_router
from app.core.config import Settings, get_settings
from app.core.state import AppResources
from app.exceptions import PageOutOfRangeError, UpstreamUnavailableError
from app.scrapers.http_client import create_http_client


def create_redis(settings: Settings) -> redis.Redis:
    """The process-wide Redis client. Building it does not connect yet."""
    return redis.from_url(settings.redis_url)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """One HTTP client and one Redis client per process, closed on shutdown (RNF-1)."""
    settings = get_settings()
    async with AsyncExitStack() as stack:
        # Each client is closed even if building the next one fails.
        redis_client = create_redis(settings)
        stack.push_async_callback(redis_client.aclose)
        http_client = create_http_client(settings)
        stack.push_async_callback(http_client.aclose)
        app.state.resources = AppResources(
            settings=settings, redis=redis_client, http_client=http_client
        )
        yield


def create_app() -> FastAPI:
    app = FastAPI(title="dia-scraper", lifespan=lifespan)
    app.include_router(products_router)

    @app.exception_handler(UpstreamUnavailableError)
    async def upstream_unavailable(request: Request, exc: UpstreamUnavailableError) -> JSONResponse:
        # Our own detail, never Dia's body nor `exc.reason` (RF-20). Covers
        # `UpstreamBlockedError` too (RF-19).
        return JSONResponse(status_code=502, content={"detail": "Upstream service unavailable"})

    @app.exception_handler(PageOutOfRangeError)
    async def page_out_of_range(request: Request, exc: PageOutOfRangeError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "Page out of range"})

    return app


app = create_app()
