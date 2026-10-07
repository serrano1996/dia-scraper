"""Application factory and lifespan: creates and closes the shared clients."""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

import redis.asyncio as redis
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.v1.products import router as products_router
from app.core.config import Settings, get_settings
from app.core.state import AppResources
from app.exceptions import (
    PageOutOfRangeError,
    PostalCodeNotServedError,
    UpstreamUnavailableError,
)
from app.scrapers.dia_session import DiaSession
from app.scrapers.http_client import create_http_client
from app.services.cooldown import AkamaiCooldown
from app.services.outbound import OutboundGate
from app.services.postal_code_sessions import PostalCodeSessions
from app.services.rate_limiter import RateLimiter


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
        # One gate per process: every request to Dia, from the scraper or from a
        # session's PUT, goes through it (spec 003 plan-D1, plan-D8).
        gate = OutboundGate(
            cooldown=AkamaiCooldown(redis_client, seconds=settings.akamai_cooldown_seconds),
            limiter=RateLimiter(
                redis_client,
                key="ratelimit:dia",
                name="dia",
                limit=settings.dia_rate_limit,
                window_seconds=settings.dia_rate_window_seconds,
            ),
        )
        # Each session builds its own HTTP client, with its own cookie jar (spec 002 plan-D1).
        sessions = PostalCodeSessions(
            new_session=lambda: DiaSession(
                client=create_http_client(settings), settings=settings, gate=gate
            ),
            max_age_seconds=settings.session_max_age_seconds,
            max_sessions=settings.max_sessions,
            session_limiter=RateLimiter(
                redis_client,
                key="ratelimit:dia:new_sessions",
                name="new_sessions",
                limit=settings.new_session_limit,
                window_seconds=settings.new_session_window_seconds,
            ),
        )
        stack.push_async_callback(sessions.aclose)
        app.state.resources = AppResources(
            settings=settings, redis=redis_client, sessions=sessions, gate=gate
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

    @app.exception_handler(PostalCodeNotServedError)
    async def postal_code_not_served(
        request: Request, exc: PostalCodeNotServedError
    ) -> JSONResponse:
        # An answer, not a failure: our own detail, never Dia's text (spec 002 RF-4).
        return JSONResponse(status_code=404, content={"detail": "Postal code not served by Dia"})

    @app.exception_handler(PageOutOfRangeError)
    async def page_out_of_range(request: Request, exc: PageOutOfRangeError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "Page out of range"})

    return app


app = create_app()
