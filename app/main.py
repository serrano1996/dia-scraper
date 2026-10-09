"""Application factory and lifespan: creates and closes the shared clients."""

import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

import redis.asyncio as redis
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError

from app.api.v1.products import router as products_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.state import AppResources, resources
from app.exceptions import (
    PageOutOfRangeError,
    PostalCodeNotServedError,
    UpstreamBlockedError,
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
from app.services.redis_circuit import RedisCircuitBreaker

logger = logging.getLogger(__name__)


def create_redis(settings: Settings) -> redis.Redis:
    """The process-wide Redis client. Building it does not connect yet.

    Connecting and every operation are bounded by `REDIS_TIMEOUT_SECONDS`, and the
    client retries nothing on its own, so one operation costs at most one timeout
    (spec 008 RF-1, plan-D1).
    """
    return redis.from_url(
        settings.redis_url,
        socket_connect_timeout=settings.redis_timeout_seconds,
        socket_timeout=settings.redis_timeout_seconds,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """One HTTP client and one Redis client per process, closed on shutdown (RNF-1)."""
    settings = get_settings()
    # First, so everything below logs with the format and level (spec 004 RF-1).
    configure_logging(settings.log_level)
    if not settings.api_keys:
        # Fail closed, but loudly: otherwise "everything answers 401" is a mystery
        # after a deploy (spec 005 RF-9, RF-10, spec-D2).
        logger.warning("no API_KEYS configured: every request to /api/v1 will be rejected")
    async with AsyncExitStack() as stack:
        # Each client is closed even if building the next one fails.
        redis_client = create_redis(settings)
        stack.push_async_callback(redis_client.aclose)
        # One circuit for every use of Redis: after a failure, all of them skip it
        # at once and use their fallbacks (spec 008 RF-3, plan-D2).
        circuit = RedisCircuitBreaker(open_seconds=settings.redis_circuit_open_seconds)
        # One gate per process: every request to Dia, from the scraper or from a
        # session's PUT, goes through it (spec 003 plan-D1, plan-D8).
        gate = OutboundGate(
            cooldown=AkamaiCooldown(
                redis_client, seconds=settings.akamai_cooldown_seconds, circuit=circuit
            ),
            limiter=RateLimiter(
                redis_client,
                key="ratelimit:dia",
                name="dia",
                limit=settings.dia_rate_limit,
                window_seconds=settings.dia_rate_window_seconds,
                circuit=circuit,
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
                circuit=circuit,
            ),
        )
        stack.push_async_callback(sessions.aclose)
        app.state.resources = AppResources(
            settings=settings, redis=redis_client, circuit=circuit, sessions=sessions, gate=gate
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
        if isinstance(exc, UpstreamBlockedError):
            # The gate already logged the block as the episode's only ERROR (spec-D3).
            logger.warning("search blocked by akamai postal_code=%r term=%r", postal_code, term)
        elif isinstance(exc, UpstreamThrottledError):
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

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness: public and touching neither Redis nor Dia (spec 005 RF-6, spec-D5)."""
        return {"status": "ok"}

    @app.get("/ready")
    async def ready(request: Request) -> JSONResponse:
        """Readiness: does this instance reach Redis? Public, like /health (spec 008 RF-10).

        The PING goes through the circuit (plan-D7): while it is open the answer is
        503 without touching Redis, and a hung Redis costs at most one timeout.
        Never calls Dia: a third party must not mark every instance as not ready.
        """
        res = resources(request.app)
        try:
            await res.circuit.call(lambda: res.redis.ping())
        except RedisError:
            # The circuit already warned (spec-D5).
            logger.debug("redis unavailable op=ready.ping")
            return JSONResponse(
                status_code=503, content={"status": "unavailable", "redis": "unreachable"}
            )
        return JSONResponse(content={"status": "ready", "redis": "ok"})

    return app


app = create_app()
