"""Domain exceptions. Must never expose types from httpx or any other transport library."""


class DiaScraperError(Exception):
    """Base class for every domain error raised by this service."""


class UpstreamUnavailableError(DiaScraperError):
    """Raised when Dia cannot be reached or its response cannot be trusted.

    Covers exhausted retries, non-retryable 4xx and malformed upstream bodies.
    `reason` is for internal logging only and must never be forwarded to the API
    response (spec 001 RF-20).
    """

    def __init__(self, reason: str, *, status_code: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        # Only for a non-retryable 4xx: lets a caller tell "Dia rejected this"
        # from a transient failure. Never sent to the client.
        self.status_code = status_code


class UpstreamBlockedError(UpstreamUnavailableError):
    """Raised when Akamai Bot Manager answers `403` with its HTML page.

    Akamai judged our fingerprint, so retrying only hurts the IP's reputation
    (spec 001 RF-19, spec-D7). Being a subclass, it still maps to the standard 502.
    """


class UpstreamThrottledError(UpstreamUnavailableError):
    """Parent of the foreseen, managed degradations: we chose not to call Dia.

    The 502 handler logs the whole family as a WARNING, not an ERROR: the
    actionable ERROR is the Akamai block that caused them (spec 004 RF-7, plan-D6).
    """


class CooldownActiveError(UpstreamThrottledError):
    """Raised when a request to Dia is refused because an Akamai cooldown is active.

    After a block the service stops calling Dia for a while (spec 003 RF-1, RF-2).
    We chose not to call Dia: never retried, the standard 502 (plan-D4).
    """


class OutboundRateLimitedError(UpstreamThrottledError):
    """Raised when a request to Dia would exceed an outbound limit (spec 003 RF-5, RF-7).

    No request is sent. The standard 502, never retried (plan-D4).
    """


class PageOutOfRangeError(DiaScraperError):
    """Raised when the requested page is past the last one Dia has for the term.

    Not an upstream failure: the search ended before that page. It maps to a 404
    with our own detail (spec 001 RF-7).
    """

    def __init__(self, page: int) -> None:
        super().__init__(f"page {page}")
        self.page = page


class PostalCodeNotServedError(DiaScraperError):
    """Raised when Dia does not serve a postal code: it has no service there or it does not exist.

    Dia answers both the same way (Fase 0 §4). Not an upstream failure: Dia
    answered, the answer is "no". It maps to a 404 with our own detail, never
    Dia's text (spec 002 RF-4, plan-D10).
    """

    def __init__(self, postal_code: str) -> None:
        super().__init__(postal_code)
        self.postal_code = postal_code
