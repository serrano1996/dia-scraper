"""API key authentication for `/api/v1` (spec 005)."""

import secrets
from typing import Annotated

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

from app.core.state import resources

API_KEY_HEADER = "X-API-Key"
UNAUTHORIZED_DETAIL = "Invalid or missing API key"

# auto_error=False: we build the 401 ourselves, so the body is the same for a
# missing and an invalid key and carries WWW-Authenticate (plan-D3, RF-3, RF-7).
api_key_header = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)


def is_valid_api_key(candidate: str | None, valid_keys: frozenset[str]) -> bool:
    """Whether `candidate` is exactly one of `valid_keys` (spec 005 RF-4, RF-5, plan-D2).

    - Exact match: no stripping or case folding, a key is an opaque secret.
    - Compared as UTF-8 bytes: `secrets.compare_digest` raises `TypeError` on
      non-ASCII `str`, which would turn an odd header into a 500.
    - Checked against every key without stopping at the first match, so the
      response time does not reveal which configured key matched.
    """
    if not candidate:
        return False
    candidate_bytes = candidate.encode("utf-8")
    matched = False
    for key in valid_keys:
        matched |= secrets.compare_digest(candidate_bytes, key.encode("utf-8"))
    return matched


def require_api_key(
    request: Request, api_key: Annotated[str | None, Security(api_key_header)]
) -> None:
    """Router-level dependency guarding every `/api/v1` endpoint (spec 005 RF-1..RF-3).

    Resolved before the route's query parameters, so a rejected request gets a
    401 and not a 422, and never reaches Redis or Dia (plan-D4). The keys are
    the ones the lifespan loaded (plan-D5).
    """
    if not is_valid_api_key(api_key, resources(request.app).settings.api_keys):
        raise HTTPException(
            status_code=401,
            detail=UNAUTHORIZED_DETAIL,
            headers={"WWW-Authenticate": "ApiKey"},
        )
