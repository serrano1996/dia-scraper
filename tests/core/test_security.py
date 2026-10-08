import pytest

from app.core.security import is_valid_api_key

# Synthetic tokens, same shape as real ones (constitution #12).
KEYS = frozenset({"token-one-abcdef", "token-two-ghijkl"})


@pytest.mark.parametrize("candidate", ["token-one-abcdef", "token-two-ghijkl"])
def test_any_configured_token_is_valid(candidate: str) -> None:
    assert is_valid_api_key(candidate, KEYS) is True


@pytest.mark.parametrize(
    "candidate",
    [
        None,
        "",
        " token-one-abcdef",
        "token-one-abcdef ",
        "TOKEN-ONE-ABCDEF",
        "token-one-abcde",
        "token-one-abcdefg",
    ],
    ids=["none", "empty", "leading-space", "trailing-space", "case", "shorter", "longer"],
)
def test_anything_but_the_exact_token_is_invalid(candidate: str | None) -> None:
    # A token is an opaque secret: no stripping, no case folding (RF-5).
    assert is_valid_api_key(candidate, KEYS) is False


def test_a_non_ascii_candidate_is_invalid_not_an_error() -> None:
    # secrets.compare_digest raises TypeError on non-ASCII str (plan-D2).
    candidate = "token-one-abcde" + chr(241)

    assert is_valid_api_key(candidate, KEYS) is False


def test_a_non_ascii_configured_token_works() -> None:
    token = "clave-" + chr(241) + "-segura"

    assert is_valid_api_key(token, frozenset({token})) is True


def test_without_configured_tokens_nothing_is_valid() -> None:
    assert is_valid_api_key("token-one-abcdef", frozenset()) is False
