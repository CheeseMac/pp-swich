"""Response guard: the last gate before anything returns to a caller.

Applies the capability's reviewed field allowlist, strips anything
secret-shaped out of allowed values, and enforces the response size budget
(docs/04 section 7, SEC-003 / SEC-020).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ppswich_core.errors import ApiError, ErrorCode

_SECRET_VALUE = re.compile(
    r"(?i)bearer\s+\S+|basic\s+[A-Za-z0-9+/=]{8,}|sk-[A-Za-z0-9]{16,}|CANARY_[A-Z0-9_]+"
)

DEFAULT_MAX_RESPONSE_BYTES = 65_536


@dataclass
class GuardedResponse:
    result: dict
    dropped_fields: list[str]


def _redact_string(value: str) -> str:
    return _SECRET_VALUE.sub("<redacted>", value)


def _redact(value):
    if isinstance(value, str):
        return _redact_string(value)
    if isinstance(value, dict):
        return {k: _redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def project_response(
    body: dict | list | None,
    allow_fields: list[str],
    *,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> GuardedResponse:
    if not isinstance(body, dict):
        raise ApiError(
            ErrorCode.UPSTREAM_FAILURE, "Upstream returned an unexpected payload shape"
        )
    allow = set(allow_fields)
    kept = {k: v for k, v in body.items() if k in allow}
    dropped = sorted(k for k in body if k not in allow)
    kept = {k: _redact(v) for k, v in kept.items()}

    serialized = json.dumps(kept, ensure_ascii=False, default=str)
    if len(serialized.encode("utf-8")) > max_response_bytes:
        raise ApiError(
            ErrorCode.UPSTREAM_FAILURE,
            "Upstream response exceeds the allowed size budget",
            {"reason": "response_too_large"},
        )
    return GuardedResponse(result=kept, dropped_fields=dropped)
