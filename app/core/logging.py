"""Logging configuration: stderr output, a fixed line format and a per-request id (spec 004).

Same approach as alcampo-scraper (its spec 003), verified there:

- The request id reaches every record through a `LogRecord` factory, not a
  `logging.Filter` (plan-D1): a filter would only tag records flowing through
  our own handler, so pytest's `caplog` records would lack it.
- We manage our own handler instead of `logging.basicConfig(force=True)`
  (plan-D2), which would also remove the handler `caplog` relies on.
"""

import logging
import sys
from collections.abc import Callable
from contextvars import ContextVar
from typing import TYPE_CHECKING, TextIO

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"

# "-" outside an HTTP request (startup, shutdown).
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_installed_factory: Callable[..., logging.LogRecord] | None = None


if TYPE_CHECKING:
    _StreamHandler = logging.StreamHandler[TextIO]
else:  # not subscriptable at runtime on every supported Python
    _StreamHandler = logging.StreamHandler


class _AppHandler(_StreamHandler):
    """Marks the handler installed by `configure_logging`, so it can be replaced alone."""


def install_request_id_factory() -> None:
    """Give every `LogRecord` a `request_id` attribute (plan-D1). Idempotent."""
    global _installed_factory
    current = logging.getLogRecordFactory()
    if current is _installed_factory:
        return

    def factory(*args: object, **kwargs: object) -> logging.LogRecord:
        record = current(*args, **kwargs)
        record.request_id = request_id_var.get()
        return record

    logging.setLogRecordFactory(factory)
    _installed_factory = factory


def configure_logging(level: str, *, stream: TextIO | None = None) -> None:
    """Configure the root logger (spec 004 RF-1). Safe to call more than once."""
    install_request_id_factory()

    root = logging.getLogger()
    for handler in [h for h in root.handlers if isinstance(h, _AppHandler)]:
        root.removeHandler(handler)

    handler = _AppHandler(sys.stderr if stream is None else stream)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(handler)
    root.setLevel(level)

    # httpx logs every request at INFO with its full URL, which carries the
    # client's term and postal code: our own lines give the path (plan-D4).
    logging.getLogger("httpx").setLevel(logging.WARNING)
