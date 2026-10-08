"""Client for Dia's product search endpoint (Fase 0 §1)."""

import asyncio
import logging

import httpx
from pydantic import ValidationError

from app.core.config import Settings
from app.exceptions import UpstreamUnavailableError
from app.models.dia import DiaSearchResponse
from app.scrapers.retry import Gate, Sleep, send_with_retry

# The lighter variant of the search Dia's own web client uses when paging: the
# same data without header, footer and customer (Fase 0 §1).
SEARCH_PATH = "/api/v1/search-back/search/reduced"

# Postal code of every anonymous session (Fase 0 §2). Spec 001 always searches
# with it (spec-D2); it keys the cache until the real resolution of spec 002 (plan-D4).
DEFAULT_POSTAL_CODE = "28041"

logger = logging.getLogger(__name__)


class DiaSearchScraper:
    """Searches Dia's catalogue: one request per page, plus retries (spec 001 RF-3)."""

    def __init__(
        self, *, settings: Settings, sleep: Sleep = asyncio.sleep, gate: Gate | None = None
    ) -> None:
        self._max_attempts = settings.retry_max_attempts
        self._base_delay = settings.retry_base_delay
        self._sleep = sleep
        self._jitter_max = settings.retry_jitter_max_s
        # Every request goes through the outbound gate (spec 003 plan-D1).
        self._gate = gate

    async def search(
        self, term: str, *, page: int, page_size: int, client: httpx.AsyncClient
    ) -> DiaSearchResponse:
        """Fetch one page of results, validated against the raw envelope (RF-21).

        Raises `UpstreamUnavailableError` (or `UpstreamBlockedError`) for any
        failure; no httpx type leaves this method (RF-22).
        """
        params: dict[str, str | int] = {"q": term, "page": page, "page_size": page_size}
        response = await send_with_retry(
            lambda: client.get(SEARCH_PATH, params=params),
            max_attempts=self._max_attempts,
            base_delay=self._base_delay,
            sleep=self._sleep,
            jitter_max=self._jitter_max,
            gate=self._gate,
            path=SEARCH_PATH,
        )
        try:
            return DiaSearchResponse.model_validate_json(response.content)
        except ValidationError as error:
            # Never the body: only where and what kind of failure (spec 004 RF-12, RF-18).
            invalid_json = any(e["type"] == "json_invalid" for e in error.errors())
            kind = "invalid_json" if invalid_json else "unexpected_schema"
            logger.error("unexpected upstream body path=%s kind=%s", SEARCH_PATH, kind)
            raise UpstreamUnavailableError("unexpected search response body") from error
