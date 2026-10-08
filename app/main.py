"""Application factory and lifespan: creates and closes the shared clients."""

import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

import redis.asyncio as redis
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.v1.products import router as products_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.state import AppResources
from app.exceptions import (
    PageOutOfRangeError,
    PostalCodeNotServedError,
    UpstreamThrottledError,
    UpstreamUnavailableError,
)
from app.middleware.request_context import RequestContextMiddleware
from app.scrapers.dia_session import DiaSession
from app.scrapers.http_client import create_http_client
from app.services.cooldown import AkamaiCooldown
from app.services.outbound import OutboundGate
from app.services.postal_code_sessions import PostalCodeSessions
from app.services.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


def create_redis(settings: Settings) -> redis.Redis:
    """The process-wide Redis client. Building it does not connect yet."""
    return redis.from_url(settings.redis_url)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """One HTTP client and one Redis client per process, closed on shutdown (RNF-1)."""
    settings = get_settings()
    # First, so everything below logs with the format and level (spec 004 RF-1).
    configure_logging(settings.log_level)
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
    # The outermost middleware: every request gets its id and its start and
    # end lines, 422s and 500s included (spec 004 plan-D5).
    app.add_middleware(RequestContextMiddleware)

    @app.exception_handler(UpstreamUnavailableError)
    async def upstream_unavailable(request: Request, exc: UpstreamUnavailableError) -> JSONResponse:
        postal_code = request.query_params.get("postal_code")
        term = request.query_params.get("term")
        # Client values through %r: they cannot forge lines (spec 004 RF-17).
        if isinstance(exc, UpstreamThrottledError):
            # Foreseen and managed: the actionable ERROR was the block (RF-7, spec-D2).
            logger.warning(
                "search throttled reason=%r postal_code=%r term=%r", exc.reason, postal_code, term
            )
        else:
            logger.error(
                "upstream unavailable reason=%r postal_code=%r term=%r",
                exc.reason,
                postal_code,
                term,
            )
        # Our own detail, never Dia's body nor `exc.reason` (spec 001 RF-20). Covers
        # `UpstreamBlockedError` too (RF-19).
        return JSONResponse(status_code=502, content={"detail": "Upstream service unavailable"})

    @app.exception_handler(PostalCodeNotServedError)
    async def postal_code_not_served(
        request: Request, exc: PostalCodeNotServedError
    ) -> JSONResponse:
        # An answer, not a failure: INFO, and our own detail, never Dia's text
        # (spec 002 RF-4, spec 004 RF-8).
        logger.info("postal code not served postal_code=%r", exc.postal_code)
        return JSONResponse(status_code=404, content={"detail": "Postal code not served by Dia"})

    @app.exception_handler(PageOutOfRangeError)
    async def page_out_of_range(request: Request, exc: PageOutOfRangeError) -> JSONResponse:
        # The search ended before that page: an answer, not a failure (spec 004 RF-8).
        logger.info("page out of range page=%d term=%r", exc.page, request.query_params.get("term"))
        return JSONResponse(status_code=404, content={"detail": "Page out of range"})

    return app


app = create_app()
