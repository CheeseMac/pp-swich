"""Shared observation merge pipeline (HAR and Collector both use it).

Every discovery source produces the same *raw shape* description of one
HTTP exchange (see ``sanitize_observation`` for the shape), then this
module runs the common pipeline:

    raw shape -> sanitize (fail-closed) -> normalize_path
              -> fingerprint_operation -> group + infer_json_schema
              -> CandidateOperation

FEAT-01 note: the browser collector uploads *zero-value samples* - JSON
structures whose leaves carry type information only ("" / 0 / false /
null), never data. The pipeline treats them like any other sample, so
schema inference, dedup and type-conflict detection work unchanged while
no observed value ever leaves the browser.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from ppswitch_discovery.fingerprint import fingerprint_operation
from ppswitch_discovery.normalizer import normalize_path
from ppswitch_discovery.sanitizer import sanitize_observation
from ppswitch_discovery.schema_inference import infer_json_schema


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


def merge_raw_shapes(
    shapes: Iterable[dict[str, Any] | None], *, source: str
) -> DiscoveryImportResult:
    """Sanitize, normalize, dedupe and infer over raw exchange shapes.

    Entries that still carry raw credential values are counted as skipped
    and their field names reported - the importer rejects the whole upload
    (fail-closed, docs/07-API-SPEC.md section 4).
    """
    result = DiscoveryImportResult()
    groups: dict[str, dict[str, Any]] = {}
    for raw in shapes:
        result.entry_count += 1
        if not isinstance(raw, dict):
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
                source=source,
            )
        )
    return result
