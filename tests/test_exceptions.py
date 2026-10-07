import app.exceptions
from app.exceptions import (
    CooldownActiveError,
    DiaScraperError,
    OutboundRateLimitedError,
    PageOutOfRangeError,
    PostalCodeNotServedError,
    UpstreamBlockedError,
    UpstreamUnavailableError,
)


def test_upstream_unavailable_error_is_a_domain_error() -> None:
    error = UpstreamUnavailableError("boom", status_code=404)

    assert isinstance(error, DiaScraperError)
    assert error.reason == "boom"
    assert error.status_code == 404


def test_upstream_unavailable_error_has_no_status_code_by_default() -> None:
    assert UpstreamUnavailableError("timeout").status_code is None


def test_upstream_blocked_error_is_an_upstream_unavailable_error() -> None:
    error = UpstreamBlockedError("akamai")

    assert isinstance(error, UpstreamUnavailableError)
    assert error.reason == "akamai"


def test_page_out_of_range_error_is_not_an_upstream_failure() -> None:
    error = PageOutOfRangeError(3)

    assert isinstance(error, DiaScraperError)
    assert not isinstance(error, UpstreamUnavailableError)
    assert error.page == 3


def test_exceptions_module_does_not_import_httpx() -> None:
    assert "httpx" not in vars(app.exceptions)


def test_postal_code_not_served_is_an_answer_not_an_upstream_failure() -> None:
    error = PostalCodeNotServedError("35001")

    assert isinstance(error, DiaScraperError)
    assert not isinstance(error, UpstreamUnavailableError)
    assert error.postal_code == "35001"


def test_anti_ban_refusals_are_upstream_failures_but_not_blocks() -> None:
    # Both mean "we chose not to call Dia": the standard 502, never retried (spec 003).
    for error in (CooldownActiveError("cooldown"), OutboundRateLimitedError("limit")):
        assert isinstance(error, UpstreamUnavailableError)
        assert not isinstance(error, UpstreamBlockedError)
