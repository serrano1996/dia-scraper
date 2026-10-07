"""One Dia session: its own HTTP client and cookie jar, set to one postal code.

Dia takes the postal code from the session (cookie `session_id`), not from the
search request, so each postal code needs a session of its own (spec 002 §1,
plan-D1). The cookies stay in this client: they never leave the process nor
reach the logs (constitution #12).
"""

import asyncio

import httpx
from pydantic import ValidationError

from app.core.config import Settings
from app.exceptions import PostalCodeNotServedError, UpstreamUnavailableError
from app.models.dia import DiaValidationError
from app.scrapers.dia_search import DEFAULT_POSTAL_CODE
from app.scrapers.retry import Sleep, send_with_retry

SAVE_SHIPPING_ADDRESS_PATH = "/api/v1/common-aggregator/save-shipping-address"


class DiaSession:
    """A Dia session. It starts with Dia's default postal code (Fase 0 §2)."""

    def __init__(
        self, *, client: httpx.AsyncClient, settings: Settings, sleep: Sleep = asyncio.sleep
    ) -> None:
        self._client = client
        self._max_attempts = settings.retry_max_attempts
        self._base_delay = settings.retry_base_delay
        self._sleep = sleep
        self._postal_code = DEFAULT_POSTAL_CODE

    @property
    def client(self) -> httpx.AsyncClient:
        return self._client

    @property
    def postal_code(self) -> str:
        """The postal code Dia has for this session, as far as we know."""
        return self._postal_code

    async def set_postal_code(self, postal_code: str) -> None:
        """Move the session to `postal_code` with one `PUT` (spec 002 RF-1, plan-D2).

        - 204: done.
        - 206 with Dia's no-service body: `PostalCodeNotServedError` (RF-4).
        - Any other answer: `UpstreamUnavailableError` (or `UpstreamBlockedError`),
          with the retry policy of every request to Dia (RF-6).
        On failure the session keeps its previous postal code.
        """
        response = await send_with_retry(
            lambda: self._client.put(
                SAVE_SHIPPING_ADDRESS_PATH, params={"new_postal_code": postal_code}
            ),
            max_attempts=self._max_attempts,
            base_delay=self._base_delay,
            sleep=self._sleep,
        )
        if response.status_code == 204:
            self._postal_code = postal_code
            return
        if response.status_code == 206 and _is_no_service(response):
            raise PostalCodeNotServedError(postal_code)
        raise UpstreamUnavailableError(
            f"unexpected answer to save-shipping-address: status {response.status_code}"
        )

    async def aclose(self) -> None:
        await self._client.aclose()


def _is_no_service(response: httpx.Response) -> bool:
    try:
        DiaValidationError.model_validate_json(response.content)
    except ValidationError:
        return False
    return True
