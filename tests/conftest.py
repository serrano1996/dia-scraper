"""Shared fixtures for the whole suite."""

import logging
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def restore_logging_state() -> Iterator[None]:
    """Undo what `configure_logging` (run by every app lifespan) does to the process.

    Without it, the root level and handler of one test leak into the next, and
    `caplog` starts capturing records that a test emitted before its own
    `caplog.at_level` block (spec 004 T11).
    """
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    factory = logging.getLogRecordFactory()
    httpx_level = logging.getLogger("httpx").level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
    logging.setLogRecordFactory(factory)
    logging.getLogger("httpx").setLevel(httpx_level)
