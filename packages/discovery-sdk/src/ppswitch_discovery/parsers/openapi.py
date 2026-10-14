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

# PR-NET-13: bounded LOCAL $ref resolution. Rules:
# - only "#/..." document-local pointers resolve; external/URL refs never
#   dereference (no network, ever);
# - resolution depth is capped and cycles abort (the operation falls to
#   needs_review instead of looping);
# - anything unresolvable marks the candidate needs_review rather than
#   silently storing a broken contract.
_MAX_REF_DEPTH = 8


def _json_pointer(root: Any, pointer: str) -> Any:
    node = root
    for part in pointer.split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return None
    return node


def resolve_local_refs(node: Any, root: Any, stack: frozenset, depth: int = 0):
    """Resolve local ``$ref`` nodes in-place-safe. Returns (resolved, ok)."""
    if depth > _MAX_REF_DEPTH:
        return None, False
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            if not ref.startswith("#/"):
                return None, False  # external reference: never dereferenced
            if ref in stack:
                return None, False  # cycle
            target = _json_pointer(root, ref[2:])
            if target is None:
                return None, False
            return resolve_local_refs(target, root, stack | {ref}, depth + 1)
        out: dict[str, Any] = {}
        for key, value in node.items():
            resolved, ok = resolve_local_refs(value, root, stack, depth)
            if not ok:
                return None, False
            out[key] = resolved
        return out, True
    if isinstance(node, list):
        resolved_items: list[Any] = []
        for item in node:
            resolved, ok = resolve_local_refs(item, root, stack, depth)
            if not ok:
                return None, False
            resolved_items.append(resolved)
        return resolved_items, True
    return node, True

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
            # PR-NET-13: resolve local $ref BEFORE extraction; an
            # unresolvable operation keeps default (empty) contracts and
            # goes to needs_review - never a silently broken schema
            resolved, refs_ok = resolve_local_refs(operation, document, frozenset())
            resolved = resolved if isinstance(resolved, dict) else {}
            params = resolved.get("parameters") or []
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
                    output_schema=_response_schema(resolved) if refs_ok else {},
                    sample_count=1,
                    status_codes=[200],
                    needs_review=not refs_ok,
                    source="openapi",
                )
            )
    return result
