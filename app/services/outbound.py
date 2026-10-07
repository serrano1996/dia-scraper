"""The gate every request to Dia goes through, before each attempt (spec 003, plan-D1).

`admit` refuses while an Akamai cooldown is active (RF-2) and takes a slot of the
global limit (RF-4, RF-5); `blocked` starts the cooldown after an Akamai block
(RF-1). Both refusals are the standard 502. Logs carry numbers and limit names
only: never URLs, headers or cookies (RF-11, plan-D7).
"""

import logging

from app.exceptions import CooldownActiveError, OutboundRateLimitedError
from app.services.cooldown import AkamaiCooldown
from app.services.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


class OutboundGate:
    """One per process, shared by the search scraper and every Dia session (plan-D8)."""

    def __init__(self, *, cooldown: AkamaiCooldown, limiter: RateLimiter) -> None:
        self._cooldown = cooldown
        self._limiter = limiter

    async def admit(self) -> None:
        """Let one request to Dia leave, or raise without sending it."""
        if await self._cooldown.is_active():
            # Checked first: a request that will not leave must not take a slot.
            raise CooldownActiveError("akamai cooldown active")
        try:
            await self._limiter.acquire()
        except OutboundRateLimitedError:
            logger.warning(
                "outbound limit reached limit=%s max=%d", self._limiter.name, self._limiter.limit
            )
            raise

    async def blocked(self) -> None:
        """Akamai blocked a request: stop calling Dia for a while (RF-1)."""
        if await self._cooldown.activate():
            logger.warning("akamai cooldown activated seconds=%d", self._cooldown.seconds)
