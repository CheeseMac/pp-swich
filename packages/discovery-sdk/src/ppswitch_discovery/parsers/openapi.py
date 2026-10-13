"""OpenAPI import: turn a spec into candidate operations.

Only method/path/parameters/response schemas are extracted. Server URLs,
security schemes and example credential values are ignored: importing an
OpenAPI document never yields credentials or executable endpoints
(docs/03-API-DISCOVERY.md).

Review2 P0-5 (SEC-036): schemas copied from the document are SANITIZED
first. Annotation keys that carry concrete business values (``example``,
``examples``, ``default``, ``const``, vendor ``x-example`` variants) are
stripped recursively - an OpenAPI file that embeds a secret in an example
must not leak it into operation_versions, capability schemas or the MCP
tool catalog. ``enum`` values are contract data, so they survive, but any
enum containing a secret-shaped or oversized value drops the whole key.
"""

from __future__ import annotations

import re
from typing import Any

from ppswitch_discovery.fingerprint import fingerprint_operation
from ppswitch_discovery.parsers.har import CandidateOperation, DiscoveryImportResult

_PATH_PARAM = re.compile(r"\{([^}]+)\}")
_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}

# annotation keys that may carry real credential-shaped values; never stored
_STRIPPED_ANNOTATIONS = frozenset(
    {"example", "examples", "default", "const", "x-example", "x-default", "x-examples"}
)
_MAX_DESCRIPTION = 256
_MAX_ENUM_ITEMS = 32
_MAX_ENUM_VALUE = 64

# secret-shaped value patterns (mirrors the sanitizer's canary/sk- rules;
# kept local so the SDK has no executor dependency)
_SECRET_SHAPED = re.compile(
    r"(?i)bearer\s+\S+|sk-[A-Za-z0-9]{16,}|CANARY_[A-Z0-9_]+|"
    r"(?:api[_-]?key|token|secret|password)[=:]\s*\S+"
)


def _value_is_sensitive(value: Any) -> bool:
    return isinstance(value, str) and bool(_SECRET_SHAPED.search(value))


def _sanitize_text(text: str) -> str:
    return _SECRET_SHAPED.sub("<REDACTED>", text)[:_MAX_DESCRIPTION]


def sanitize_schema(node: Any, depth: int = 0) -> Any:
    """Return a persisted-safe copy of an OpenAPI schema fragment."""
    if depth > 16:
        return {}
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, value in node.items():
            key_text = str(key)
            if key_text.lower() in _STRIPPED_ANNOTATIONS:
                continue  # concrete example/default values never persist
            if key_text == "enum" and isinstance(value, list):
                trimmed = value[:_MAX_ENUM_ITEMS]
                if (
                    len(value) > _MAX_ENUM_ITEMS
                    or any(
                        not isinstance(v, (str, int, float, bool))
                        or (isinstance(v, str) and len(v) > _MAX_ENUM_VALUE)
                        or _value_is_sensitive(v)
                        for v in trimmed
                    )
                ):
                    continue  # drop the whole enum rather than risk a leak
                out[key_text] = trimmed
                continue
            if key_text == "description" and isinstance(value, str):
                out[key_text] = _sanitize_text(value)
                continue
            sanitized = sanitize_schema(value, depth + 1)
            out[key_text] = sanitized
        return out
    if isinstance(node, list):
        return [sanitize_schema(item, depth + 1) for item in node[:100]]
    if isinstance(node, str):
        return _sanitize_text(node)
    return node


def _parameter_schema(parameters: list[dict[str, Any]], location: str) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for param in parameters:
        if str(param.get("in", "")) != location:
            continue
        name = str(param.get("name", ""))
        if not name:
            continue
        prop: dict[str, Any] = sanitize_schema(dict(param.get("schema") or {"type": "string"}))
        if param.get("description"):
            prop["description"] = _sanitize_text(str(param["description"]))
        properties[name] = prop
        if param.get("required"):
            required.append(name)
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = sorted(required)
    return schema


def _response_schema(operation: dict[str, Any]) -> dict[str, Any]:
    responses = operation.get("responses") or {}
    for status in ("200", "201", "default"):
        response = responses.get(status)
        if not isinstance(response, dict):
            continue
        content = response.get("content") or {}
        json_content = content.get("application/json")
        if isinstance(json_content, dict) and isinstance(json_content.get("schema"), dict):
            return sanitize_schema(json_content["schema"])
    return {}


def build_candidates_from_openapi(document: dict[str, Any]) -> DiscoveryImportResult:
    result = DiscoveryImportResult()
    paths = document.get("paths")
    if not isinstance(paths, dict):
        return result

    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in _METHODS or not isinstance(operation, dict):
                continue
            result.entry_count += 1
            template = "/" + path.strip("/")
            params = operation.get("parameters") or []
            path_params = _PATH_PARAM.findall(template)
            query_keys = sorted(
                str(p.get("name", ""))
                for p in params
                if isinstance(p, dict) and p.get("in") == "query"
            )
            fp = fingerprint_operation(method.upper(), template, query_keys)
            result.candidates.append(
                CandidateOperation(
                    method=method.upper(),
                    template=template,
                    fingerprint=fp,
                    query_keys=query_keys,
                    path_params=path_params,
                    input_schema={
                        "path": _parameter_schema(params, "path"),
                        "query": _parameter_schema(params, "query"),
                    },
                    output_schema=_response_schema(operation),
                    sample_count=1,
                    status_codes=[200],
                    needs_review=False,
                    source="openapi",
                )
            )
    return result
