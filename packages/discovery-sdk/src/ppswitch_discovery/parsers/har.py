"""HAR parsing: extract request/response shapes from a sanitized HAR document.

The caller is expected to have pre-sanitized the file locally (docs/07
section 4). This parser sanitizes again - defense in depth - and reports any
position that still carries a raw credential value so the importer can
reject the upload.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from ppswitch_discovery.fingerprint import fingerprint_operation
from ppswitch_discovery.normalizer import normalize_path
from ppswitch_discovery.sanitizer import sanitize_observation
from ppswitch_discovery.schema_inference import infer_json_schema

MAX_BODY_BYTES = 512 * 1024


@dataclass
class CandidateOperation:
    """Deduplicated candidate API operation produced by discovery."""

    method: str
    template: str
    fingerprint: str
    query_keys: list[str] = field(default_factory=list)
    path_params: list[str] = field(default_factory=list)
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    sample_count: int = 0
    status_codes: list[int] = field(default_factory=list)
    needs_review: bool = False
    source: str = "har"


@dataclass
class DiscoveryImportResult:
    candidates: list[CandidateOperation] = field(default_factory=list)
    unsanitized_fields: list[str] = field(default_factory=list)
    entry_count: int = 0
    skipped_entries: int = 0


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
    result = DiscoveryImportResult()
    log = document.get("log")
    entries = log.get("entries") if isinstance(log, dict) else None
    if not isinstance(entries, list):
        return result

    groups: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        result.entry_count += 1
        raw = _extract_raw_shape(entry)
        if raw is None:
            result.skipped_entries += 1
            continue

        report = sanitize_observation(raw)
        if report.unsanitized_fields:
            result.unsanitized_fields.extend(report.unsanitized_fields)
            continue  # never keep unsanitized entries, not even partially

        request = report.sanitized["request"]
        response = report.sanitized["response"]
        normalized = normalize_path(
            request["method"], request["path"],
            graphql_operation=raw.get("_graphql_operation"),
        )
        query_keys = sorted(request.get("query", {}).keys())
        fp = fingerprint_operation(request["method"], normalized.template, query_keys)

        group = groups.setdefault(
            fp,
            {
                "method": request["method"],
                "template": normalized.template,
                "query_keys": query_keys,
                "path_params": normalized.param_names,
                "query_samples": [],
                "body_samples": [],
                "response_samples": [],
                "status_codes": set(),
                "count": 0,
                "conflict": False,
            },
        )
        group["count"] += 1
        group["status_codes"].add(int(response["status"]))
        if request.get("query"):
            group["query_samples"].append(request["query"])
        body = request.get("body")
        if isinstance(body, dict) and "__root__" not in body:
            group["body_samples"].append(body)
        if response.get("body") is not None:
            group["response_samples"].append(response["body"])
        if normalized.type_conflict:
            group["conflict"] = True

    for fp, group in groups.items():
        query_schema = infer_json_schema(group["query_samples"]) if group["query_samples"] else {}
        body_schema = infer_json_schema(group["body_samples"]) if group["body_samples"] else {}
        output_schema = (
            infer_json_schema(group["response_samples"]) if group["response_samples"] else {}
        )
        needs_review = group["conflict"] or bool(
            output_schema.get("x-type-conflict")
            or query_schema.get("x-type-conflict")
            or body_schema.get("x-type-conflict")
        )
        result.candidates.append(
            CandidateOperation(
                method=group["method"],
                template=group["template"],
                fingerprint=fp,
                query_keys=group["query_keys"],
                path_params=group["path_params"],
                input_schema={"query": query_schema, "body": body_schema},
                output_schema=output_schema,
                sample_count=group["count"],
                status_codes=sorted(group["status_codes"]),
                needs_review=needs_review,
                source="har",
            )
        )
    return result
