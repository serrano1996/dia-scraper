"""Loads the real Dia responses saved during Fase 0 (`tests/fixtures/`)."""

import json
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> Any:
    """A fresh copy of a fixture each call, so tests can edit it freely."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))
