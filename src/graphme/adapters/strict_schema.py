"""Render a Pydantic model's JSON schema in OpenAI structured-output strict form.

Strict mode requires every property of every object to appear in `required` and
every object to set `additionalProperties: false`. Pydantic omits fields that have
defaults from `required`, so the schema it generates is rejected outright:

    Invalid schema for response_format: 'required' is required to be supplied and
    to be an array including every key in properties.

Optional fields stay optional in meaning — their schema already admits null — so
listing them as required only forces the model to send the key explicitly.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


def _strictify(node: Any) -> Any:
    if isinstance(node, list):
        return [_strictify(item) for item in node]
    if not isinstance(node, dict):
        return node
    result = {key: _strictify(value) for key, value in node.items() if key != "default"}
    properties = result.get("properties")
    if isinstance(properties, dict):
        result["required"] = list(properties)
        result["additionalProperties"] = False
    return result


def strict_response_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The model's JSON schema, adjusted for OpenAI strict structured output."""
    schema = _strictify(model.model_json_schema())
    if not isinstance(schema, dict):  # pragma: no cover - model_json_schema returns a dict
        raise TypeError("model schema must be an object")
    return schema


_UNPORTABLE = frozenset({
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minLength", "maxLength", "maxItems",
})
_NAMES = frozenset({"properties", "$defs"})
"""Keys whose children are names, not keywords: a property called `maximum` stays."""


def _portable(node: Any, *, names: bool = False) -> Any:
    if isinstance(node, list):
        return [_portable(item) for item in node]
    if not isinstance(node, dict):
        return node
    if names:
        return {key: _portable(value) for key, value in node.items()}
    return {
        key: _portable(value, names=key in _NAMES)
        for key, value in node.items()
        if key not in _UNPORTABLE
        and not (key == "minItems" and isinstance(value, int) and value > 1)
    }


def portable(schema: dict[str, Any]) -> dict[str, Any]:
    """The schema without the constraints Anthropic's structured outputs refuse: numeric and
    length bounds, and array bounds beyond `minItems` of 0 or 1. The answer is still checked
    against them, by the Pydantic model that reads it."""
    result = _portable(schema)
    if not isinstance(result, dict):  # pragma: no cover - a dict in is a dict out
        raise TypeError("schema must be an object")
    return result
