"""Response guard: the last gate before anything returns to a caller.

PR-SEC-04 (review) + review2 P0-4 (SEC-035): projection is driven by the
capability's reviewed ``output_schema`` recursively, and the TYPE gate
runs FIRST - a value whose actual JSON kind disagrees with the declared
schema type is denied outright, never structurally walked. Only fields
the schema declares survive, at every nesting level; sensitive *names*
(token, api_key, ...) are dropped at any depth even when the schema
allows them; depth/size budgets are enforced; the regex redaction of
secret-shaped strings remains the final supplementary layer.

Default-deny rules when a reviewed schema is in charge (``output_schema``
has ``properties``):

- a nested object the schema does not describe via ``properties`` is
  DENIED (empty object), not scrubbed-through;
- the top-level allow set is the INTERSECTION of the reviewed schema
  properties and ``allow_fields`` when both are present - exactly one
  authoritative allow set, never the union;
- legacy capabilities without any ``output_schema`` keep the old behavior
  (top-level allow_fields + recursive scrub) and never gain exposure.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ppswitch_core.errors import ApiError, ErrorCode

_SECRET_VALUE = re.compile(
    r"(?i)bearer\s+\S+|basic\s+[A-Za-z0-9+/=]{8,}|sk-[A-Za-z0-9]{16,}|CANARY_[A-Z0-9_]+"
)

# sensitive names are denied at ANY depth, schema or not
SENSITIVE_NAMES = frozenset(
    {
        "authorization", "proxy-authorization", "cookie", "set-cookie",
        "token", "access_token", "refresh_token", "id_token", "api_key",
        "apikey", "secret", "client_secret", "password", "passwd",
        "private_key", "session_id", "sessionid",
    }
)

@dataclass
class GuardedResponse:
    result: dict
    dropped_fields: list[str] = field(default_factory=list)


DEFAULT_MAX_RESPONSE_BYTES = 65_536
MAX_DEPTH = 12
MAX_ARRAY_ITEMS = 1_000

_TYPE_MAP: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
}


class _Dropped:
    """Sentinel tracking for audit metadata (names only)."""

    def __init__(self) -> None:
        self.names: set[str] = set()

    def add(self, name: str) -> None:
        if len(self.names) < 64:
            self.names.add(name)


def _redact_string(value: str) -> str:
    return _SECRET_VALUE.sub("<redacted>", value)


def _type_ok(value: Any, expected: str) -> bool:
    python_type = _TYPE_MAP.get(expected)
    if python_type is None:
        return True  # unknown/unsupported schema type: don't guess, deny below
    if expected == "number" and isinstance(value, bool):
        return False
    return isinstance(value, python_type)


def _scrub(value: Any, dropped: _Dropped, depth: int = 0) -> Any:
    """Schema-less fallback: keep structure, drop sensitive names, redact."""
    if depth > MAX_DEPTH:
        raise ApiError(ErrorCode.UPSTREAM_FAILURE, "Upstream payload nesting too deep")
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in SENSITIVE_NAMES:
                dropped.add(str(key))
                continue
            out[str(key)] = _scrub(item, dropped, depth + 1)
        return out
    if isinstance(value, list):
        return [_scrub(item, dropped, depth + 1) for item in value[:MAX_ARRAY_ITEMS]]
    if isinstance(value, str):
        return _redact_string(value)
    return value


# composition keywords without a value-driven branch selection stay
# uninterpretable: the guard is schema-driven and must not guess
_UNSUPPORTED_COMPOSITION = ("$ref", "oneOf", "anyOf", "not")


def _normalized_schema(schema: Any) -> dict[str, Any] | None:
    """PR-NET-13 (bounded): resolve interpretable schema constructs.

    Returns a plain interpretable schema dict, or None when the schema is
    uninterpretable (unresolved references, value-driven compositions,
    unsupported allOf shapes) - the caller then denies the value.
    """
    if not isinstance(schema, dict):
        return {}
    if any(keyword in schema for keyword in _UNSUPPORTED_COMPOSITION):
        return None
    if "allOf" not in schema:
        return schema
    # allOf is supported ONLY as a full merge of plain object branches
    # ({type: object, properties, required}); anything richer (nested
    # composition, extra sibling keys, non-object branches) stays denied
    branches = schema.get("allOf")
    if (
        isinstance(branches, list)
        and branches
        and "properties" not in schema
        and "type" not in schema
        and all(
            isinstance(b, dict)
            and not any(keyword in b for keyword in _UNSUPPORTED_COMPOSITION)
            and "allOf" not in b
            and b.get("type", "object") == "object"
            and isinstance(b.get("properties"), dict)
            and set(b) <= {"type", "properties", "required"}
            for b in branches
        )
    ):
        merged: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
        for branch in branches:
            merged["properties"].update(branch["properties"])
            merged["required"].extend(branch.get("required") or [])
        merged["required"] = sorted(set(merged["required"]))
        return merged
    return None


def _project(value: Any, schema: dict[str, Any], dropped: _Dropped, depth: int = 0) -> Any:
    """Recursive schema-driven whitelist projection (default-deny)."""
    if depth > MAX_DEPTH:
        raise ApiError(ErrorCode.UPSTREAM_FAILURE, "Upstream payload nesting too deep")
    normalized = _normalized_schema(schema)
    if normalized is None:
        # SEC-048 + PR-NET-13: unresolved references and value-driven
        # compositions never gain a lenient path; unsupported allOf too
        dropped.add("<uninterpretable_schema>")
        return None
    schema = normalized

    expected = schema.get("type")

    # --- type gate FIRST (SEC-035): the declared type decides before any
    # structural walk, so an object smuggled into a string field is denied
    # instead of falling into a lenient structural branch
    if expected == "object" and not isinstance(value, dict):
        dropped.add("<type:object>")
        return None
    if expected == "array" and not isinstance(value, list):
        dropped.add("<type:array>")
        return None
    if isinstance(expected, str) and expected not in ("object", "array"):
        if not _type_ok(value, expected):
            dropped.add(f"<type:{expected}>")
            return None

    # --- structural projection
    if isinstance(value, dict):
        properties = schema.get("properties")
        if not isinstance(properties, dict) or not properties:
            # reviewed-schema mode: an object level the schema does not
            # describe is DENIED, never passed through a lenient scrub
            # (review2 P0-4: unknown object structures default to reject)
            dropped.add("<unspecified_object>")
            return {}
        out: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.lower() in SENSITIVE_NAMES:
                dropped.add(key_text)  # denied even when schema allows
                continue
            if key_text not in properties:
                dropped.add(key_text)
                continue
            out[key_text] = _project(item, properties[key_text], dropped, depth + 1)
        return out
    if isinstance(value, list):
        # SEC-048 (review3): an array without a usable ``items`` schema is
        # DENIED outright - regex-redacted pass-through is not an allowlist.
        # An items schema that is an unresolved reference (``$ref``) or that
        # declares neither ``type`` nor ``properties`` is equally unusable.
        raw_items = schema.get("items")
        items_schema = _normalized_schema(raw_items)
        if items_schema is None or not (
            items_schema.get("type") is not None
            or items_schema.get("properties")
        ):
            dropped.add("<items_unspecified>")
            return None
        projected = [
            _project(item, items_schema, dropped, depth + 1)
            for item in value[:MAX_ARRAY_ITEMS]
        ]
        return projected
    # scalar: type gate already ran; redact string values
    if isinstance(value, str):
        return _redact_string(value)
    return value


def project_response(
    body: dict | list | None,
    allow_fields: list[str],
    *,
    output_schema: dict[str, Any] | None = None,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> GuardedResponse:
    if not isinstance(body, dict):
        raise ApiError(
            ErrorCode.UPSTREAM_FAILURE, "Upstream returned an unexpected payload shape"
        )
    dropped = _Dropped()
    schema: dict[str, Any] = dict(output_schema or {})

    # SEC-051 + PR-NET-13: the ROOT schema passes through the same
    # bounded normalizer - a supported allOf merge proceeds (and REPLACES
    # the original from here on, so merges never fall into the legacy
    # branch), while unresolved references and value-driven compositions
    # are refused BEFORE any fallback path could run
    normalized_root = _normalized_schema(schema)
    if schema and normalized_root is None:
        raise ApiError(
            ErrorCode.UPSTREAM_FAILURE,
            "Capability output schema is not interpretable "
            "(unresolved reference or composition); refusing to project",
            {"reason": "root_schema_not_interpretable"},
        )
    if normalized_root:
        schema = normalized_root

    schema_properties = schema.get("properties")
    if isinstance(schema_properties, dict) and schema_properties:
        # P0-4: one authoritative allow set - when the reviewed schema and
        # allow_fields are both present the INTERSECTION governs the top
        # level; the schema alone governs when allow_fields is empty
        if allow_fields:
            effective_properties = {
                k: v for k, v in schema_properties.items() if k in set(allow_fields)
            }
            schema = {**schema, "properties": effective_properties}
        projected = _project(body, schema, dropped)
        kept = projected if isinstance(projected, dict) else {}
    else:
        # legacy path: top-level allow_fields, then recursive scrub
        allow = set(allow_fields)
        kept = {k: v for k, v in body.items() if k in allow}
        dropped.names.update(k for k in body if k not in allow)
        kept = {k: _scrub(v, dropped) for k, v in kept.items()}

    serialized = json.dumps(kept, ensure_ascii=False, default=str)
    if len(serialized.encode("utf-8")) > max_response_bytes:
        raise ApiError(
            ErrorCode.UPSTREAM_FAILURE,
            "Upstream response exceeds the allowed size budget",
            {"reason": "response_too_large"},
        )
    return GuardedResponse(result=kept, dropped_fields=sorted(dropped.names))
