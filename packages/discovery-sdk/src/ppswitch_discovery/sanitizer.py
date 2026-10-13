"""Observation sanitizer.

Values that look like credentials must never enter the central store.
Two layers:

1. ``sanitize_observation`` rewrites sensitive positions to redaction markers
   and reports which positions still carried raw values.
2. The importer treats any reported position as *unsanitized input* and
   rejects the upload (fail-closed, docs/07-API-SPEC.md section 4).

Canary constants follow docs/11-TEST-PLAN.md section 3: synthetic values
that can never collide with real credentials and must never survive into
storage, logs or tool output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

CANARY_API_KEY = "CANARY_NOT_REAL_ABC123"
CANARY_COOKIE = "CANARY_SESSION_NOT_REAL"
CANARY_PASSWORD = "CANARY_PASSWORD_NOT_REAL"  # noqa: S105 - synthetic canary

REDACTED_PREFIX = "<REDACTED"

SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "api-key",
        "apikey",
        "x-auth-token",
        "x-session-token",
        "x-csrf-token",
        "x-amz-security-token",
        "authentication",
    }
)

SENSITIVE_PARAM_NAMES = frozenset(
    {
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "api_key",
        "apikey",
        "secret",
        "client_secret",
        "password",
        "passwd",
        "session_id",
        "sessionid",
        "session",
        "csrf",
        "auth",
        "signature",
        "private_key",
    }
)

# value shapes that look like real bearer keys even under innocent names
_SECRET_VALUE_PATTERN = re.compile(
    r"(?i)bearer\s+\S+|basic\s+[A-Za-z0-9+/=]{8,}|^sk-[A-Za-z0-9]{16,}$"
)


def _is_redacted(value: str) -> bool:
    return value.startswith(REDACTED_PREFIX)


def _looks_like_secret_value(value: str) -> bool:
    return bool(_SECRET_VALUE_PATTERN.search(value))


def _is_sensitive_name(name: str, sensitive_set: frozenset[str]) -> bool:
    return name.strip().lower() in sensitive_set


@dataclass
class SanitizationReport:
    """Outcome of sanitizing one observed request/response pair.

    ``unsanitized_fields`` contains field NAMES and positions only - never
    values - so it is safe to persist for auditing the rejection.
    """

    sanitized: dict[str, Any]
    unsanitized_fields: list[str] = field(default_factory=list)


def _sanitize_mapping(
    mapping: dict[str, Any], sensitive_set: frozenset[str], position: str
) -> tuple[dict[str, Any], list[str]]:
    result: dict[str, Any] = {}
    unsanitized: list[str] = []
    for key, value in mapping.items():
        if _is_sensitive_name(key, sensitive_set):
            str_value = value if isinstance(value, str) else str(value)
            if str_value == "" or _is_redacted(str_value):
                result[key] = f"<REDACTED:{key.lower()}>"
            else:
                unsanitized.append(f"{position}.{key}")
                result[key] = f"<REDACTED:{key.lower()}>"
        elif isinstance(value, str) and _looks_like_secret_value(value):
            unsanitized.append(f"{position}.{key}")
            result[key] = f"<REDACTED:{key.lower()}>"
        elif isinstance(value, dict):
            inner, inner_unsanitized = _sanitize_mapping(value, sensitive_set, f"{position}.{key}")
            result[key] = inner
            unsanitized.extend(inner_unsanitized)
        else:
            result[key] = value
    return result, unsanitized


def sanitize_observation(raw: dict[str, Any]) -> SanitizationReport:
    """Sanitize one observed request/response shape.

    ``raw`` is a structural description of a single HTTP exchange:

    .. code-block:: python

        {
          "request": {
            "method": "GET",
            "path": "/api/customers/1024",
            "query": {"verbose": "true"},
            "headers": {"Authorization": "..."},
            "body": {"customer_id": "C-1024"},
          },
          "response": {
            "status": 200,
            "headers": {"Set-Cookie": "..."},
            "body": {"id": "C-1024", "display_name": "Acme"},
          },
        }

    Returns a sanitized copy plus the names of positions that still held
    raw sensitive values (reject-worthy input).
    """
    sanitized: dict[str, Any] = {}
    unsanitized: list[str] = []

    request = raw.get("request") or {}
    response = raw.get("response") or {}

    safe_request: dict[str, Any] = dict(request)
    for position, sensitive_set in (
        ("headers", SENSITIVE_HEADERS),
        ("query", SENSITIVE_PARAM_NAMES),
        ("cookies", SENSITIVE_PARAM_NAMES),
    ):
        section = request.get(position) or {}
        if isinstance(section, dict):
            clean, found = _sanitize_mapping(section, sensitive_set, f"request.{position}")
            safe_request[position] = clean
            unsanitized.extend(found)
    body = request.get("body")
    if isinstance(body, dict):
        clean, found = _sanitize_mapping(body, SENSITIVE_PARAM_NAMES, "request.body")
        safe_request["body"] = clean
        unsanitized.extend(found)

    safe_response: dict[str, Any] = dict(response)
    resp_headers = response.get("headers") or {}
    if isinstance(resp_headers, dict):
        clean, found = _sanitize_mapping(resp_headers, SENSITIVE_HEADERS, "response.headers")
        safe_response["headers"] = clean
        unsanitized.extend(found)
    resp_body = response.get("body")
    if isinstance(resp_body, dict):
        clean, found = _sanitize_mapping(resp_body, SENSITIVE_PARAM_NAMES, "response.body")
        safe_response["body"] = clean
        unsanitized.extend(found)

    sanitized["request"] = safe_request
    sanitized["response"] = safe_response
    return SanitizationReport(sanitized=sanitized, unsanitized_fields=unsanitized)
