"""FEAT-01 collector parser tests: zero-value samples, dedup, fail-closed.

The browser extension uploads structure-only observations ("zero-value
samples": every leaf replaced by a type tag, no headers, no values). These
tests pin the privacy and merge contract on the server side.
"""

from __future__ import annotations

from ppswitch_discovery.parsers.collector import (
    MAX_OBSERVATIONS,
    build_candidates_from_collector,
)


def observation(
    url: str,
    method: str = "GET",
    status: int = 200,
    query: dict | None = None,
    request_body: dict | None = None,
    response_body: dict | None = None,
    graphql_operation: str | None = None,
) -> dict:
    return {
        "url": url,
        "method": method,
        "status": status,
        "query": query or {},
        "request_body": request_body,
        "response_body": response_body,
        "graphql_operation": graphql_operation,
    }


def test_repeated_requests_merge_into_one_candidate():
    doc = {
        "observations": [
            observation(
                f"https://crm.example.com/api/customers/{i}",
                response_body={"id": "", "display_name": ""},
            )
            for i in (1024, 1025, 2049)
        ]
    }
    result = build_candidates_from_collector(doc)
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.method == "GET"
    assert candidate.template == "/api/customers/{id}"
    assert candidate.sample_count == 3
    assert candidate.source == "collector"
    # zero-value samples still infer real types
    assert (
        candidate.output_schema["properties"]["id"]["type"] == "string"
    )
    assert candidate.output_schema["required"] == ["display_name", "id"]
    assert candidate.needs_review is False


def test_query_names_split_fingerprints():
    doc = {
        "observations": [
            observation("https://crm.example.com/api/orders", query={"page": ""}),
            observation("https://crm.example.com/api/orders", query={"page": "", "q": ""}),
        ]
    }
    result = build_candidates_from_collector(doc)
    assert len(result.candidates) == 2
    assert {c.sample_count for c in result.candidates} == {1}


def test_sensitive_query_name_zero_value_is_redacted_not_rejected():
    doc = {
        "observations": [
            observation(
                "https://crm.example.com/api/profile",
                query={"session_id": ""},
                response_body={"email": ""},
            )
        ]
    }
    result = build_candidates_from_collector(doc)
    assert result.unsanitized_fields == []
    candidate = result.candidates[0]
    # the parameter NAME survives as structural knowledge; value never existed
    assert candidate.query_keys == ["session_id"]


def test_realistic_secret_value_fails_closed_whole_batch():
    doc = {
        "observations": [
            observation("https://crm.example.com/api/orders", query={"page": ""}),
            observation(
                "https://crm.example.com/api/profile",
                query={"token": "Bearer eyJhbGciOi.example.sig"},
            ),
        ]
    }
    result = build_candidates_from_collector(doc)
    # parser layer: the raw credential position is REPORTED (never kept);
    # the import layer treats any report as grounds to reject the whole
    # upload - pinned end-to-end in control-api collector tests
    assert "request.query.token" in result.unsanitized_fields
    flagged = [c for c in result.candidates if "profile" in c.template]
    assert flagged == []


def test_precoarsened_path_template_is_idempotent():
    doc = {
        "observations": [
            observation("https://crm.example.com/api/customers/{id}"),
        ]
    }
    result = build_candidates_from_collector(doc)
    assert result.candidates[0].template == "/api/customers/{id}"


def test_graphql_operations_split_by_name():
    doc = {
        "observations": [
            observation(
                "https://crm.example.com/graphql",
                method="POST",
                request_body={"operationName": "", "query": ""},
                response_body={"data": {"customer": {"id": ""}}},
                graphql_operation="getCustomer",
            ),
            observation(
                "https://crm.example.com/graphql",
                method="POST",
                request_body={"operationName": "", "query": ""},
                graphql_operation="getCustomer",
            ),
            observation(
                "https://crm.example.com/graphql",
                method="POST",
                request_body={"operationName": "", "query": ""},
                graphql_operation="searchOrders",
            ),
        ]
    }
    result = build_candidates_from_collector(doc)
    templates = {c.template for c in result.candidates}
    assert templates == {"/graphql#getCustomer", "/graphql#searchOrders"}
    counts = {c.template: c.sample_count for c in result.candidates}
    assert counts["/graphql#getCustomer"] == 2


def test_type_conflict_routes_to_needs_review():
    doc = {
        "observations": [
            observation("https://crm.example.com/api/items/1", response_body={"qty": 0}),
            observation("https://crm.example.com/api/items/2", response_body={"qty": ""}),
        ]
    }
    result = build_candidates_from_collector(doc)
    assert result.candidates[0].needs_review is True


def test_non_object_roots_wrap_like_har():
    doc = {
        "observations": [
            observation(
                "https://crm.example.com/api/tags",
                response_body=[{"name": ""}],
            )
        ]
    }
    result = build_candidates_from_collector(doc)
    schema = result.candidates[0].output_schema
    assert schema["type"] == "array"
    assert schema["items"]["properties"]["name"]["type"] == "string"


def test_non_http_schemes_and_bad_rows_are_skipped():
    doc = {
        "observations": [
            observation("chrome-extension://abc/popup.html"),
            observation("ftp://crm.example.com/api"),
            "not-a-dict",
            observation("https://crm.example.com/api/ok", status="NaN-ish"),
            observation("https://crm.example.com/api/fine"),
        ]
    }
    result = build_candidates_from_collector(doc)
    assert result.entry_count == 5
    assert result.skipped_entries == 4
    assert [c.template for c in result.candidates] == ["/api/fine"]


def test_observation_cap_reports_overflow():
    doc = {
        "observations": [
            observation(f"https://crm.example.com/api/x/{i}") for i in range(MAX_OBSERVATIONS + 3)
        ]
    }
    result = build_candidates_from_collector(doc)
    assert result.skipped_entries == 3


def test_query_missing_falls_back_to_url_names():
    doc = {
        "observations": [
            {
                "url": "https://crm.example.com/api/orders?page=1&limit=50&limit=50",
                "method": "GET",
                "status": 200,
            },
        ]
    }
    result = build_candidates_from_collector(doc)
    candidate = result.candidates[0]
    assert candidate.query_keys == ["limit", "page"]
