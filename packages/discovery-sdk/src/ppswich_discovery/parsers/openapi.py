"""OpenAPI import: turn a spec into candidate operations.

Only method/path/parameters/response schemas are extracted. Server URLs,
security schemes and example credential values are ignored: importing an
OpenAPI document never yields credentials or executable endpoints
(docs/03-API-DISCOVERY.md).
"""

from __future__ import annotations

import re
from typing import Any

from ppswich_discovery.fingerprint import fingerprint_operation
from ppswich_discovery.parsers.har import CandidateOperation, DiscoveryImportResult

_PATH_PARAM = re.compile(r"\{([^}]+)\}")
_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


def _parameter_schema(parameters: list[dict[str, Any]], location: str) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for param in parameters:
        if str(param.get("in", "")) != location:
            continue
        name = str(param.get("name", ""))
        if not name:
            continue
        prop: dict[str, Any] = dict(param.get("schema") or {"type": "string"})
        if param.get("description"):
            prop["description"] = str(param["description"])[:256]
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
            return json_content["schema"]
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
