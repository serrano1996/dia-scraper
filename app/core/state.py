"""The resources the `lifespan` builds, as one typed object (plan-D14).

`app.state` is an untyped namespace: a misspelt attribute would only fail at
run time. The lifespan stores a single `AppResources`, and `resources(app)` is
the only place that reads `app.state`, so any other typo is a `mypy` error.
"""

from dataclasses import dataclass

import httpx
from redis.asyncio import Redis
from starlette.applications import Starlette

from app.core.config import Settings


@dataclass(frozen=True)
class AppResources:
    """Everything stateful, built once per process (spec 001 RNF-1)."""

    settings: Settings
    redis: Redis
    http_client: httpx.AsyncClient


def resources(app: Starlette) -> AppResources:
    """The `AppResources` of a running app; fails clearly outside its `lifespan`."""
    found = getattr(app.state, "resources", None)
    if not isinstance(found, AppResources):
        raise RuntimeError("app resources are only available while its lifespan runs")
    return found
