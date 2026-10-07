"""Per-request id, start and end log lines, `X-Request-ID` and the 500 (spec 004 RF-3..RF-6).

The outermost middleware, so every request is traced, including `422`s from
validation. A pure ASGI middleware, as in alcampo-scraper (plan-D5):
`BaseHTTPMiddleware` logged the end line before a streamed body was sent.
"""

import logging
import time
import uuid

from starlette.datastructures import MutableHeaders, QueryParams
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import install_request_id_factory, request_id_var

REQUEST_ID_HEADER = "X-Request-ID"

logger = logging.getLogger(__name__)


class RequestContextMiddleware:
    """Gives each HTTP request its id and logs its start and end."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        # Records carry the id even before (or without) `configure_logging`.
        install_request_id_factory()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Always generated here: a client-supplied X-Request-ID is ignored, so
        # external input can never forge log lines (RF-5, spec-D1).
        request_id = uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started_at = time.perf_counter()
        status = 500
        response_started = False
        # Client-controlled values go through %r so control chars are escaped (RF-17).
        logger.info(
            "request started method=%s path=%r params=%r",
            scope["method"],
            scope["path"],
            dict(QueryParams(scope["query_string"])),
        )

        async def send_with_request_id(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            try:
                await self.app(scope, receive, send_with_request_id)
            except Exception:
                # Here, not with app.exception_handler(Exception): that one runs
                # outside this middleware, after the request id is gone and
                # without X-Request-ID on the response (RF-6, plan-D5).
                logger.exception("unhandled error")
                if response_started:
                    raise  # a response already on its way cannot become a 500
                error = JSONResponse({"detail": "Internal server error"}, status_code=500)
                await error(scope, receive, send_with_request_id)
        finally:
            logger.info(
                "request finished status=%d duration_ms=%.1f",
                status,
                (time.perf_counter() - started_at) * 1000,
            )
            request_id_var.reset(token)
