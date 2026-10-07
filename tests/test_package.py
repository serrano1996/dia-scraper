import importlib

import pytest


@pytest.mark.parametrize(
    "module",
    [
        "app",
        "app.api",
        "app.api.v1",
        "app.core",
        "app.models",
        "app.mappers",
        "app.scrapers",
        "app.services",
        "app.middleware",
    ],
)
def test_app_subpackages_are_importable(module: str) -> None:
    importlib.import_module(module)
