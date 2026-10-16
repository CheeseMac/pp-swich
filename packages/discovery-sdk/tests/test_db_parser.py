"""FEAT-04: database metadata parser tests (pure, no PG involved)."""

from __future__ import annotations

from ppswitch_discovery.parsers.db import build_candidates_from_db_metadata


def _document(objects: list) -> dict:
    return {
        "target": {"host": "127.0.0.1", "port": 5432, "database": "crm"},
        "schemas": ["crm"],
        "objects": objects,
        "truncated": False,
    }


def test_builds_one_candidate_per_view_with_typed_columns():
    result = build_candidates_from_db_metadata(
        _document(
            [
                {
                    "schema": "crm",
                    "name": "customers_v",
                    "kind": "view",
                    "columns": [
                        {"name": "id", "data_type": "integer"},
                        {"name": "balance", "data_type": "numeric"},
                        {"name": "active", "data_type": "boolean"},
                        {"name": "display_name", "data_type": "text"},
                    ],
                }
            ]
        )
    )
    assert result.entry_count == 1
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.method == "GET"
    assert candidate.template == "/db/crm/customers_v"
    assert candidate.source == "db"
    assert candidate.needs_review is True  # metadata inference, human confirms
    assert candidate.query_keys == ["active", "balance", "display_name", "id"]
    properties = candidate.output_schema["properties"]
    assert properties["id"] == {"type": "integer"}
    assert properties["balance"] == {"type": "number"}
    assert properties["active"] == {"type": "boolean"}
    assert properties["display_name"] == {"type": "string"}
    # the SAME typed properties are offered as query filters (wizard picks)
    assert candidate.input_schema["query"]["properties"] == properties
    # identical metadata twice -> identical fingerprint (importer dedupes)
    again = build_candidates_from_db_metadata(_document([
        {
            "schema": "crm", "name": "customers_v", "kind": "view",
            "columns": [{"name": "id", "data_type": "integer"}],
        }
    ]))
    assert again.candidates[0].fingerprint != candidate.fingerprint  # columns differ


def test_fingerprint_stable_for_identical_contract():
    one = build_candidates_from_db_metadata(_document([
        {"schema": "crm", "name": "v", "kind": "view",
         "columns": [{"name": "a", "data_type": "text"}]}
    ]))
    two = build_candidates_from_db_metadata(_document([
        {"schema": "crm", "name": "v", "kind": "table",
         "columns": [{"name": "a", "data_type": "text"}]}
    ]))
    assert one.candidates[0].fingerprint == two.candidates[0].fingerprint


def test_skips_exotic_identifiers_and_malformed_entries():
    result = build_candidates_from_db_metadata(
        _document(
            [
                {"schema": "crm", "name": "weird name", "kind": "view",
                 "columns": [{"name": "id", "data_type": "integer"}]},
                {"schema": "crm", "name": "ok", "kind": "view",
                 "columns": [{"name": "quoted col", "data_type": "integer"}]},
                {"schema": "crm", "name": "nocolumns", "kind": "view", "columns": []},
                {"schema": "crm", "name": "badkind", "kind": "materialized",
                 "columns": [{"name": "id", "data_type": "integer"}]},
                "not-a-dict",
                {"schema": "crm", "name": "good", "kind": "table",
                 "columns": [{"name": "id", "data_type": "integer"}]},
            ]
        )
    )
    templates = [c.template for c in result.candidates]
    assert templates == ["/db/crm/good"]
    assert result.skipped_entries == 5


def test_unknown_pg_types_degrade_to_string():
    result = build_candidates_from_db_metadata(_document([
        {"schema": "crm", "name": "v", "kind": "view",
         "columns": [
             {"name": "ts", "data_type": "timestamp with time zone"},
             {"name": "payload", "data_type": "jsonb"},
             {"name": "tags", "data_type": "ARRAY"},
         ]}
    ]))
    properties = result.candidates[0].output_schema["properties"]
    assert all(prop == {"type": "string"} for prop in properties.values())


def test_empty_and_malformed_documents_fail_soft():
    assert build_candidates_from_db_metadata({}).candidates == []
    assert build_candidates_from_db_metadata({"objects": "nope"}).candidates == []
