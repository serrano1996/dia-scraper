"""Per-request id, start and end log lines, `X-Request-ID` and the 500 (spec 004 RF-3..RF-6).

The outermost middleware, so every request is traced, including `422`s from
validation. A pure ASGI middleware, as in alcampo-scraper (plan-D5):
`BaseHTTPMiddleware` logged the end line before a streamed body was sent.
"""

import logging
import time
import traceback
import uuid

from starlette.datastructures import MutableHeaders, QueryParams
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import install_request_id_factory, request_id_var

REQUEST_ID_HEADER = "X-Request-ID"

# Longest query logged on the start line: a huge one must not bloat the logs (review T13).
MAX_PARAMS_LOGGED = 500

# A query param whose name contains any of these is hidden on the start line:
# a client sending its key in the URL by mistake must not leak it into our logs
# (spec 005 RF-15, plan-D6). Judged by the normalised name (trimmed, lower case,
# "-" as "_"), never by the value; broader than the five names of RF-15 after the
# review (T9) found `api-key`, `access_token`, `password` and the like leaking.
# None of the API's own params (postal_code, term, page, page_size) matches.
SECRET_NAME_MARKERS = ("key", "token", "secret", "auth", "pass")
REDACTED = "***"

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
            "request started method=%s path=%r params=%s",
            scope["method"],
            scope["path"],
            _params_for_log(scope["query_string"]),
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
            except Exception as error:
                # Here, not with app.exception_handler(Exception): that one runs
                # outside this middleware, after the request id is gone and
                # without X-Request-ID on the response (RF-6, plan-D5).
                # Type and frames, never the message: it can carry a Dia body
                # fragment or a URL with the client's term (RF-18, review T13).
                logger.error(
                    "unhandled error type=%s frames=%r",
                    type(error).__name__,
                    "".join(traceback.format_tb(error.__traceback__)).rstrip(),
                )
                if response_started:
                    raise  # a response already on its way cannot become a 500
                response = JSONResponse({"detail": "Internal server error"}, status_code=500)
                await response(scope, receive, send_with_request_id)
        finally:
            logger.info(
                "request finished status=%d duration_ms=%.1f",
                status,
                (time.perf_counter() - started_at) * 1000,
            )
            request_id_var.reset(token)


def _params_for_log(query_string: bytes) -> str:
    """Every parameter, repeated ones included, through repr and capped (RF-17)."""
    text = repr(redact_params(QueryParams(query_string).multi_items()))
    if len(text) > MAX_PARAMS_LOGGED:
        return f"{text[:MAX_PARAMS_LOGGED]}...(truncated, {len(text)} chars)"
    return text


def redact_params(params: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """`params` with the values of secret-named params replaced by `***`, order kept."""
    return [(name, REDACTED if _is_secret_name(name) else value) for name, value in params]


def _is_secret_name(name: str) -> bool:
    normalised = name.strip().lower().replace("-", "_")
    return any(marker in normalised for marker in SECRET_NAME_MARKERS)
