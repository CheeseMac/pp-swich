"""Response guard: the last gate before anything returns to a caller.

PR-SEC-04 (review): projection is driven by the capability's reviewed
``output_schema`` recursively - only fields the schema declares survive, at
every nesting level. Sensitive *names* (token, api_key, ...) are dropped at
any depth even when the schema allows them, values are type-checked against
the schema, and depth/size budgets are enforced. The regex redaction of
secret-shaped strings remains only as the final supplementary layer.

If the schema has no ``properties`` at a level, that level falls back to
the capability ``allow_fields`` (top level) or is scrubbed-only (nested) -
legacy capabilities without detailed schemas never get MORE exposure than
before.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ppswich_core.errors import ApiError, ErrorCode

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
        return True  # unknown/unsupported schema type: don't guess, scrub layer handles
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


def _project(value: Any, schema: dict[str, Any], dropped: _Dropped, depth: int = 0) -> Any:
    """Recursive schema-driven whitelist projection."""
    if depth > MAX_DEPTH:
        raise ApiError(ErrorCode.UPSTREAM_FAILURE, "Upstream payload nesting too deep")
    if not isinstance(schema, dict):
        schema = {}

    expected = schema.get("type")
    if isinstance(value, dict):
        properties = schema.get("properties")
        if not isinstance(properties, dict) or not properties:
            # schema doesn't describe this object level: scrub-only, never wider
            return _scrub(value, dropped, depth)
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
        raw_items = schema.get("items")
        items_schema: dict[str, Any] = raw_items if isinstance(raw_items, dict) else {}
        projected = [
            _project(item, items_schema, dropped, depth + 1)
            for item in value[:MAX_ARRAY_ITEMS]
        ]
        return projected
    # scalar: type-check against the schema, then redact string values
    if expected and not _type_ok(value, str(expected)):
        dropped.add(f"<type:{expected}>")
        return None
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
    schema_properties = schema.get("properties")

    if isinstance(schema_properties, dict) and schema_properties:
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
