"""Spec 001 RNF-5: the search response has exactly Mercadona's shape (constitution #13).

`tests/fixtures/mercadona_search_response_schema.json` is Mercadona's
`ProductSearchResponse.model_json_schema()`, generated from its app (commit
3126851, 2026-10-05). Model names differ between projects, so shapes are
compared after resolving references: field names, types and which are required.
If Mercadona changes its contract, regenerate the fixture on purpose.
"""

import json
from pathlib import Path

from app.models.product import ProductSearchResponse

MERCADONA = Path(__file__).parents[1] / "fixtures" / "mercadona_search_response_schema.json"

Shape = dict[str, object]


def shape(schema: dict, defs: dict) -> object:
    """A model-name-free description of a JSON schema node."""
    if "$ref" in schema:
        return shape(defs[schema["$ref"].rsplit("/", 1)[1]], defs)
    if "anyOf" in schema:
        return sorted((shape(option, defs) for option in schema["anyOf"]), key=str)
    if schema.get("type") == "object" and "properties" in schema:
        required = set(schema.get("required", []))
        return {
            name: {"type": shape(prop, defs), "required": name in required}
            for name, prop in sorted(schema["properties"].items())
        }
    if schema.get("type") == "array":
        return {"array_of": shape(schema["items"], defs)}
    return {key: schema[key] for key in ("type", "format") if key in schema}


def response_shape(schema: dict) -> object:
    return shape(schema, schema.get("$defs", {}))


def test_the_search_response_has_mercadonas_shape() -> None:
    mercadona = json.loads(MERCADONA.read_text(encoding="utf-8"))

    assert response_shape(ProductSearchResponse.model_json_schema()) == response_shape(mercadona)
