"""Browser collector parsing (FEAT-01): sanitized observations -> candidates.

The Chrome extension observes Fetch/XHR traffic on tabs the user explicitly
attached the debugger to, converts every request/response pair into a
*zero-value sample* (structure + leaf types only - never data values, never
headers) and uploads batches of these observations with a short-lived
collector token (pairing flow, see control-api collector_service).

Payload contract (``document``)::

    {
      "observations": [
        {
          "url": "https://crm.example.com/api/customers/1024?verbose=true",
          "method": "GET",
          "status": 200,
          "query": {"verbose": ""},          # names only, zero values
          "request_body": {"q": ""},         # zero-value skeleton or null
          "response_body": {"id": ""},       # zero-value skeleton or null
          "graphql_operation": "getCustomer" # present only for graphql
        }
      ]
    }

Defense in depth: ``merge_raw_shapes`` sanitizes every observation again.
A batch where any position still carries a real-looking credential value
is rejected whole by the importer (fail-closed) - a buggy or malicious
extension cannot smuggle secrets past this layer.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from ppswitch_discovery.parsers.merge import (
    DiscoveryImportResult,
    merge_raw_shapes,
)

MAX_OBSERVATIONS = 500


def _extract_observation_shape(observation: dict[str, Any]) -> dict[str, Any] | None:
    url = str(observation.get("url", ""))
    method = str(observation.get("method", "GET")).upper()
    try:
        split = urlsplit(url)
    except ValueError:
        return None
    if split.scheme not in ("http", "https"):
        return None

    query = observation.get("query")
    if not isinstance(query, dict):
        parsed: dict[str, str] = {}
        for name in (split.query or "").split("&"):
            if name:
                parsed.setdefault(name.split("=", 1)[0], "")
        query = parsed
    query = {str(k): v if isinstance(v, str) else str(v) for k, v in query.items() if k}

    def _body(key: str) -> Any:
        # passed through as-is: dict/list/scalar skeletons all infer
        # natively; the sanitizer redacts dict keys and the merge layer
        # only collects dict bodies as request-body samples (HAR parity)
        return observation.get(key)

    body = _body("request_body")
    graphql_operation = observation.get("graphql_operation")
    graphql_operation = (
        str(graphql_operation) if graphql_operation else None
    )
    if body is not None and split.path.rstrip("/").endswith("graphql") and not graphql_operation:
        operation_name = body.get("operationName")
        if isinstance(operation_name, str):
            graphql_operation = operation_name or None

    try:
        status = int(observation.get("status", 0) or 0)
    except (TypeError, ValueError):
        return None

    return {
        "request": {
            "method": method,
            "path": split.path,
            "host": split.netloc,
            "query": query,
            "headers": {},  # headers are never uploaded by the collector
            "cookies": {},
            "body": body,
        },
        "response": {
            "status": status,
            "headers": {},
            "body": _body("response_body"),
        },
        "_graphql_operation": graphql_operation,
    }


def build_candidates_from_collector(document: dict[str, Any]) -> DiscoveryImportResult:
    """Turn a collector observation batch into deduplicated candidates."""
    observations = document.get("observations")
    if not isinstance(observations, list):
        return DiscoveryImportResult()

    shapes = []
    for observation in observations[:MAX_OBSERVATIONS]:
        if isinstance(observation, dict):
            shapes.append(_extract_observation_shape(observation))
        else:
            shapes.append(None)
    result = merge_raw_shapes(shapes, source="collector")
    # observations beyond the cap are counted as skipped so the caller can
    # tell the client the batch was truncated (it should batch smaller)
    overflow = max(0, len(observations) - MAX_OBSERVATIONS)
    result.skipped_entries += overflow
    return result
