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


class PageOutOfRangeError(DiaScraperError):
    """Raised when the requested page is past the last one Dia has for the term.

    Not an upstream failure: the search ended before that page. It maps to a 404
    with our own detail (spec 001 RF-7).
    """

    def __init__(self, page: int) -> None:
        super().__init__(f"page {page}")
        self.page = page
