"""HAR parsing: extract request/response shapes from a sanitized HAR document.

The caller is expected to have pre-sanitized the file locally (docs/07
section 4). This parser sanitizes again - defense in depth - and reports any
position that still carries a raw credential value so the importer can
reject the upload.

The sanitize -> normalize -> dedupe -> infer pipeline itself lives in
``parsers.merge`` and is shared with the browser collector (FEAT-01).
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

from ppswitch_discovery.parsers.merge import (
    CandidateOperation,  # re-export: openapi.py and tests import it from here
    DiscoveryImportResult,
    merge_raw_shapes,
)

MAX_BODY_BYTES = 512 * 1024

__all__ = [
    "MAX_BODY_BYTES",
    "CandidateOperation",
    "DiscoveryImportResult",
    "build_candidates_from_har",
]


def _headers_to_dict(headers: list[dict[str, Any]] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for header in headers or []:
        name = str(header.get("name", ""))
        if name:
            result[name] = str(header.get("value", ""))
    return result


def _params_to_dict(params: list[dict[str, Any]] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for param in params or []:
        name = str(param.get("name", ""))
        if name:
            result[name] = str(param.get("value", ""))
    return result


def _parse_body(post_data: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(post_data, dict):
        return None
    mime = str(post_data.get("mimeType", ""))
    text = post_data.get("text")
    if isinstance(text, str) and "json" in mime and len(text) <= MAX_BODY_BYTES:
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else {"__root__": parsed}
    params = post_data.get("params")
    if isinstance(params, list) and params:
        return _params_to_dict(params)
    return None


def _parse_response_body(content: dict[str, Any] | None) -> Any | None:
    if not isinstance(content, dict):
        return None
    mime = str(content.get("mimeType", ""))
    text = content.get("text")
    if isinstance(text, str) and "json" in mime and len(text) <= MAX_BODY_BYTES:
        try:
            return json.loads(text)
        except (ValueError, TypeError):
            return None
    return None


def _extract_raw_shape(entry: dict[str, Any]) -> dict[str, Any] | None:
    request = entry.get("request")
    response = entry.get("response")
    if not isinstance(request, dict) or not isinstance(response, dict):
        return None
    url = str(request.get("url", ""))
    method = str(request.get("method", "GET")).upper()
    try:
        split = urlsplit(url)
    except ValueError:
        return None
    if split.scheme not in ("http", "https"):
        return None

    body = _parse_body(request.get("postData"))
    graphql_operation = None
    if body and split.path.rstrip("/").endswith("graphql"):
        graphql_operation = str(body.get("operationName") or "") or None

    return {
        "request": {
            "method": method,
            "path": split.path,
            "host": split.netloc,
            "query": _params_to_dict(request.get("queryString")),
            "headers": _headers_to_dict(request.get("headers")),
            "cookies": _params_to_dict(request.get("cookies")),
            "body": body,
        },
        "response": {
            "status": int(response.get("status", 0)),
            "headers": _headers_to_dict(response.get("headers")),
            "body": _parse_response_body(response.get("content")),
        },
        "_graphql_operation": graphql_operation,
    }


def build_candidates_from_har(document: dict[str, Any]) -> DiscoveryImportResult:
    """Turn a HAR document into deduplicated candidate operations."""
    log = document.get("log")
    entries = log.get("entries") if isinstance(log, dict) else None
    if not isinstance(entries, list):
        return DiscoveryImportResult()

    shapes: list[dict[str, Any] | None] = []
    for entry in entries:
        if not isinstance(entry, dict):
            shapes.append(None)
            continue
        shapes.append(_extract_raw_shape(entry))
    return merge_raw_shapes(shapes, source="har")
