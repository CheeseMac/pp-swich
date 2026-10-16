"""FEAT-04 database metadata parsing: approved schemas/views -> candidates.

The trusted executor discovered the metadata through the read-only account
(see ``ppswitch_executor.adapters.db_adapter.discover_objects``): column
names and types only, restricted to the APPROVED schemas of the db target.
This parser turns that document into the same ``CandidateOperation`` shape
every other source produces, so the importer, the wizard and the review
pipeline work unchanged:

    one view/table  ->  one candidate "GET /db/{schema}/{view}"
                       columns -> query parameters AND output fields
                       PG data types -> JSON schema leaf types

No HTTP exchange is involved, so the sanitize/normalize stages of the
traffic pipeline do not apply; the input is trusted-side metadata that
never carried values. Defense in depth: identifiers are restricted to
unquoted-safe names and objects beyond the caps are reported as skipped
(the caller decides to re-run with narrower allowed_schemas).

Candidates are flagged ``needs_review`` - metadata-derived contracts are
inferences, not observations, and a human must confirm the tool shape in
the FEAT-02 wizard before anything publishes.
"""

from __future__ import annotations

import re
from typing import Any

from ppswitch_discovery.fingerprint import fingerprint_operation
from ppswitch_discovery.parsers.merge import CandidateOperation, DiscoveryImportResult

MAX_OBJECTS = 200
MAX_COLUMNS = 64

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

# PostgreSQL data types -> JSON schema leaf types. Everything not numeric
# or boolean degrades to string: the read-only tool compares and returns,
# it never needs PG-native type fidelity for its first slice.
_PG_TYPE_MAP: dict[str, str] = {
    "smallint": "integer",
    "integer": "integer",
    "bigint": "integer",
    "numeric": "number",
    "real": "number",
    "double precision": "number",
    "boolean": "boolean",
}


def _json_type(pg_type: Any) -> str:
    return _PG_TYPE_MAP.get(str(pg_type or "").lower(), "string")


def _object_columns(entry: dict[str, Any]) -> list[dict[str, Any]] | None:
    columns = entry.get("columns")
    if not isinstance(columns, list) or not columns:
        return None
    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for column in columns:
        if not isinstance(column, dict):
            return None
        name = str(column.get("name", ""))
        if not _IDENTIFIER.match(name) or name in seen:
            return None
        seen.add(name)
        cleaned.append({"name": name, "data_type": str(column.get("data_type", ""))})
    return cleaned


def build_candidates_from_db_metadata(document: dict[str, Any]) -> DiscoveryImportResult:
    """Turn an executor db-discovery document into candidate operations."""
    result = DiscoveryImportResult()
    objects = document.get("objects")
    if not isinstance(objects, list):
        return result

    for entry in objects:
        result.entry_count += 1
        if not isinstance(entry, dict):
            result.skipped_entries += 1
            continue
        schema = str(entry.get("schema", ""))
        name = str(entry.get("name", ""))
        kind = str(entry.get("kind", "view"))
        if kind not in ("view", "table"):
            result.skipped_entries += 1
            continue
        if not _IDENTIFIER.match(schema) or not _IDENTIFIER.match(name):
            # quoted/exotic names cannot flow into identifier-composed SQL
            result.skipped_entries += 1
            continue
        columns = _object_columns(entry)
        if columns is None:
            result.skipped_entries += 1
            continue
        if len(result.candidates) >= MAX_OBJECTS:
            result.skipped_entries += 1
            continue
        columns = columns[:MAX_COLUMNS]

        template = f"/db/{schema}/{name}"
        query_keys = sorted(column["name"] for column in columns)
        fingerprint = fingerprint_operation("GET", template, query_keys)
        properties = {
            column["name"]: {"type": _json_type(column["data_type"])}
            for column in columns
        }
        result.candidates.append(
            CandidateOperation(
                method="GET",
                template=template,
                fingerprint=fingerprint,
                query_keys=query_keys,
                path_params=[],
                input_schema={
                    "query": {
                        "type": "object",
                        "properties": properties,
                        "additionalProperties": False,
                    }
                },
                output_schema={
                    "type": "object",
                    "properties": properties,
                    "additionalProperties": False,
                },
                sample_count=1,
                status_codes=[200],
                needs_review=True,  # metadata inference: human confirms in the wizard
                source="db",
            )
        )
    return result
