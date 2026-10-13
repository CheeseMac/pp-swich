"""JSON Schema inference from observed samples.

Merges multiple sample values into a single draft-style JSON Schema:

- object keys union; ``required`` = keys present in every sample;
- scalar type conflicts (int vs string) set ``x-type-conflict`` so the
  operation is routed to ``needs_review`` instead of silently guessed
  (docs/11-TEST-PLAN.md section 4);
- lists describe their element schema;
- ``null`` observed anywhere makes the property nullable.

Inferred schemas are *observational evidence*, not published contracts.
"""

from __future__ import annotations

from typing import Any

_TYPE_ORDER = ["boolean", "integer", "number", "string", "object", "array", "null"]


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):  # bool is a subclass of int: check first
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return "string"


def _merge_types(types: list[str]) -> dict[str, Any]:
    unique = sorted(set(types), key=_TYPE_ORDER.index)
    schema: dict[str, Any] = {}
    if len(unique) == 1:
        schema["type"] = unique[0]
    else:
        # int + float collapse to number
        if "integer" in unique and "number" in unique:
            unique = [t for t in unique if t != "integer"]
        if "null" in unique and len(unique) > 1:
            schema["type"] = [t for t in unique if t != "null"]
            schema["nullable"] = True
        elif len(unique) == 1:
            schema["type"] = unique[0]
        else:
            schema["type"] = unique
            schema["x-type-conflict"] = True
    return schema


def _infer_from_samples(samples: list[Any]) -> dict[str, Any]:
    if not samples:
        return {"type": "null"}
    merged = _merge_types([_json_type(s) for s in samples])
    if merged.get("type") == "object":
        return _infer_object([s for s in samples if isinstance(s, dict)])
    if merged.get("type") == "array":
        elements: list[Any] = []
        for sample in samples:
            if isinstance(sample, list):
                elements.extend(sample)
        schema: dict[str, Any] = {"type": "array"}
        if elements:
            schema["items"] = _infer_from_samples(elements)
        return schema
    return merged


def _infer_object(samples: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(samples)
    key_counts: dict[str, int] = {}
    for sample in samples:
        for key in sample:
            key_counts[key] = key_counts.get(key, 0) + 1
    properties: dict[str, Any] = {}
    conflicts = False
    for key in sorted(key_counts):
        values = [s[key] for s in samples if key in s]
        prop_schema = _infer_from_samples(values)
        if prop_schema.get("x-type-conflict"):
            conflicts = True
        properties[key] = prop_schema
    required = sorted(k for k, c in key_counts.items() if c == total)
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": True,  # observational, not a strict contract
    }
    if conflicts:
        schema["x-type-conflict"] = True
    return schema


def infer_json_schema(samples: list[Any]) -> dict[str, Any]:
    """Infer a JSON Schema that describes all observed samples."""
    return _infer_from_samples(samples)
