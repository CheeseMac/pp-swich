"""Golden tests for the discovery pipeline (docs/11-TEST-PLAN.md section 4)."""

from __future__ import annotations

import json

from ppswich_discovery import (
    CANARY_API_KEY,
    fingerprint_operation,
    infer_json_schema,
    normalize_path,
    sanitize_observation,
)
from ppswich_discovery.parsers.har import build_candidates_from_har
from ppswich_discovery.parsers.openapi import build_candidates_from_openapi


def make_har(entries: list[dict]) -> dict:
    return {"log": {"version": "1.2", "creator": {"name": "synthetic"}, "entries": entries}}


def make_entry(
    method: str,
    url: str,
    response_body: dict | None = None,
    response_headers: list[dict] | None = None,
    request_headers: list[dict] | None = None,
    status: int = 200,
) -> dict:
    return {
        "request": {
            "method": method,
            "url": url,
            "headers": request_headers or [],
            "queryString": [],
            "cookies": [],
        },
        "response": {
            "status": status,
            "headers": response_headers or [],
            "content": {
                "mimeType": "application/json",
                "text": json.dumps(response_body or {}),
            },
        },
    }


class TestNormalizePath:
    def test_numeric_ids_merge(self) -> None:
        a = normalize_path("GET", "/api/customers/100")
        b = normalize_path("GET", "/api/customers/200")
        assert a.template == b.template == "/api/customers/{id}"

    def test_uuid_segment_templated(self) -> None:
        result = normalize_path(
            "GET", "/orgs/550e8400-e29b-41d4-a716-446655440000/members"
        )
        assert result.template == "/orgs/{uuid}/members"

    def test_api_versions_stay_separate(self) -> None:
        assert normalize_path("GET", "/api/v1/customers").template == "/api/v1/customers"
        assert normalize_path("GET", "/api/v2/customers").template == "/api/v2/customers"

    def test_literal_word_is_not_an_id(self) -> None:
        assert normalize_path("GET", "/api/users/search").template == "/api/users/search"
        assert normalize_path("GET", "/api/users/123").template == "/api/users/{id}"

    def test_graphql_separated_by_operation(self) -> None:
        a = normalize_path("POST", "/graphql", graphql_operation="getCustomer")
        b = normalize_path("POST", "/graphql", graphql_operation="searchCustomers")
        assert a.template == "/graphql#getCustomer"
        assert b.template == "/graphql#searchCustomers"
        assert a.template != b.template


class TestSanitizer:
    def test_canary_authorization_is_flagged_and_never_kept(self) -> None:
        raw = {
            "request": {
                "method": "GET",
                "headers": {"Authorization": f"Bearer {CANARY_API_KEY}"},
                "query": {},
            },
            "response": {"status": 200, "headers": {}},
        }
        report = sanitize_observation(raw)
        assert report.unsanitized_fields == ["request.headers.Authorization"]
        assert CANARY_API_KEY not in json.dumps(report.sanitized)

    def test_cookie_and_set_cookie_redacted(self) -> None:
        raw = {
            "request": {"method": "GET", "headers": {"Cookie": "SESSION=CANARY_SESSION_NOT_REAL"}},
            "response": {"status": 200, "headers": {"Set-Cookie": "refresh=x"}},
        }
        report = sanitize_observation(raw)
        assert "request.headers.Cookie" in report.unsanitized_fields
        assert "response.headers.Set-Cookie" in report.unsanitized_fields
        assert "CANARY_SESSION_NOT_REAL" not in json.dumps(report.sanitized)

    def test_already_redacted_headers_are_not_flagged(self) -> None:
        raw = {
            "request": {"method": "GET", "headers": {"Authorization": "<REDACTED:authorization>"}},
            "response": {"status": 200, "headers": {}},
        }
        report = sanitize_observation(raw)
        assert report.unsanitized_fields == []

    def test_response_body_secret_fields_redacted(self) -> None:
        raw = {
            "request": {"method": "GET", "headers": {}},
            "response": {
                "status": 200,
                "headers": {},
                "body": {"id": "C-1", "access_token": "leaky", "display_name": "Acme"},
            },
        }
        report = sanitize_observation(raw)
        assert "response.body.access_token" in report.unsanitized_fields
        assert "leaky" not in json.dumps(report.sanitized)
        assert report.sanitized["response"]["body"]["display_name"] == "Acme"


class TestSchemaInference:
    def test_required_is_intersection_of_keys(self) -> None:
        schema = infer_json_schema(
            [{"id": "C-1", "display_name": "A", "tier": "gold"},
             {"id": "C-2", "display_name": "B"}]
        )
        assert schema["required"] == ["display_name", "id"]
        assert set(schema["properties"]) == {"id", "display_name", "tier"}

    def test_type_conflict_is_flagged(self) -> None:
        schema = infer_json_schema([{"id": 1}, {"id": "abc"}])
        assert schema["properties"]["id"]["x-type-conflict"] is True


class TestHarImport:
    def test_three_customer_ids_merge_into_one_candidate(self) -> None:
        har = make_har(
            [
                make_entry("GET", "https://crm.example.test/api/customers/C-1024",
                           {"id": "C-1024", "display_name": "Acme"}),
                make_entry("GET", "https://crm.example.test/api/customers/C-2048",
                           {"id": "C-2048", "display_name": "Globex"}),
                make_entry("GET", "https://crm.example.test/api/customers/C-4096",
                           {"id": "C-4096", "display_name": "Initech"}),
            ]
        )
        result = build_candidates_from_har(har)
        assert len(result.candidates) == 1
        candidate = result.candidates[0]
        assert candidate.template == "/api/customers/{id}"
        assert candidate.sample_count == 3
        assert candidate.output_schema["required"] == ["display_name", "id"]
        assert result.unsanitized_fields == []

    def test_import_with_raw_credentials_is_rejected_not_stored(self) -> None:
        har = make_har(
            [
                make_entry(
                    "GET", "https://crm.example.test/api/customers/C-1024",
                    {"id": "C-1024"},
                    request_headers=[
                        {"name": "Authorization", "value": f"Bearer {CANARY_API_KEY}"}
                    ],
                )
            ]
        )
        result = build_candidates_from_har(har)
        assert result.candidates == []
        assert result.unsanitized_fields == ["request.headers.Authorization"]
        # the canary value itself must never appear anywhere in the result
        assert CANARY_API_KEY not in json.dumps(
            {"unsanitized": result.unsanitized_fields, "skipped": result.skipped_entries}
        )

    def test_different_methods_are_distinct(self) -> None:
        har = make_har(
            [
                make_entry("GET", "https://x.test/api/customers/1"),
                make_entry("POST", "https://x.test/api/customers/1"),
            ]
        )
        result = build_candidates_from_har(har)
        assert len(result.candidates) == 2

    def test_graphql_operations_dedupe_by_name(self) -> None:
        def gql_entry(op: str) -> dict:
            entry = make_entry("POST", "https://x.test/graphql", {"data": {}})
            entry["request"]["postData"] = {
                "mimeType": "application/json",
                "text": json.dumps({"operationName": op, "query": "query {}"}),
            }
            return entry

        result = build_candidates_from_har(make_har([gql_entry("getCustomer"), gql_entry("getCustomer"),  # noqa: E501
            gql_entry("search")]))
        templates = sorted(c.template for c in result.candidates)
        assert templates == ["/graphql#getCustomer", "/graphql#search"]

    def test_invalid_urls_are_skipped(self) -> None:
        har = make_har([make_entry("GET", "file:///etc/hostname")])
        result = build_candidates_from_har(har)
        assert result.candidates == []
        assert result.skipped_entries == 1


class TestOpenapiImport:
    def test_spec_becomes_candidates_without_servers_or_security(self) -> None:
        spec = {
            "openapi": "3.1.0",
            "servers": [{"url": "https://crm.example.test"}],
            "paths": {
                "/api/customers/{customer_id}": {
                    "get": {
                        "operationId": "getCustomer",
                        "parameters": [
                            {"name": "customer_id", "in": "path", "required": True,
                             "schema": {"type": "string"}},
                            {"name": "verbose", "in": "query", "schema": {"type": "boolean"}},
                        ],
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "string"},
                                                "display_name": {"type": "string"},
                                            },
                                        }
                                    }
                                }
                            }
                        },
                    }
                }
            },
        }
        result = build_candidates_from_openapi(spec)
        assert len(result.candidates) == 1
        candidate = result.candidates[0]
        assert candidate.path_params == ["customer_id"]
        assert candidate.query_keys == ["verbose"]
        assert candidate.source == "openapi"


class TestFingerprint:
    def test_same_shape_same_fingerprint(self) -> None:
        a = fingerprint_operation("GET", "/api/customers/{id}", [])
        b = fingerprint_operation("GET", "/api/customers/{id}", [])
        assert a == b

    def test_different_query_keys_differ(self) -> None:
        a = fingerprint_operation("GET", "/api/customers/{id}", ["verbose"])
        b = fingerprint_operation("GET", "/api/customers/{id}", ["fields"])
        assert a != b
