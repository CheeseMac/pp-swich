"""Review7 regression: NET-13-F1 (duplicate-property allOf), NET-13-F2
(resolution budgets) and PR-DEP-14 application-layer egress hardening
(IPv6 wrapper canonicalization).
"""

from __future__ import annotations

import pytest


class TestNET13F1DuplicateProperties:
    """A later allOf branch must never widen/replace an earlier branch."""

    def test_review_example_private_note_never_leaks(self):
        from ppswitch_core.errors import ApiError
        from ppswitch_executor.response_guard import project_response

        schema = {
            "allOf": [
                {
                    "type": "object",
                    "properties": {
                        "profile": {
                            "type": "object",
                            "properties": {"name": {"type": "string"}},
                        }
                    },
                },
                {
                    "type": "object",
                    "properties": {
                        "profile": {
                            "type": "object",
                            "properties": {"private_note": {"type": "string"}},
                        }
                    },
                },
            ]
        }
        # root-level duplicate: the whole response is refused
        with pytest.raises(ApiError):
            project_response(
                {"profile": {"name": "Alice", "private_note": "Confidential"}},
                ["profile"],
                output_schema=schema,
            )

    def test_nested_duplicate_property_denied(self):
        from ppswitch_executor.response_guard import project_response

        schema = {
            "type": "object",
            "properties": {
                "wrapper": {
                    "allOf": [
                        {"type": "object", "properties": {"a": {"type": "string"}}},
                        {"type": "object", "properties": {"a": {"type": "string"}}},
                    ]
                }
            },
        }
        guarded = project_response(
            {"wrapper": {"a": "x"}}, ["wrapper"], output_schema=schema
        )
        assert guarded.result == {"wrapper": None}
        assert "<uninterpretable_schema>" in guarded.dropped_fields

    def test_disjoint_branches_still_merge(self):
        from ppswitch_executor.response_guard import project_response

        schema = {
            "allOf": [
                {"type": "object", "properties": {"id": {"type": "string"}}},
                {"type": "object", "properties": {"tier": {"type": "string"}}},
            ]
        }
        guarded = project_response(
            {"id": "1", "tier": "gold", "extra": "x"},
            ["id", "tier"],
            output_schema=schema,
        )
        assert guarded.result == {"id": "1", "tier": "gold"}


class TestNET13F2ResolutionBudgets:
    def _document(self):
        return {
            "paths": {},
            "components": {"schemas": {"Big": {"type": "object"}}},
        }

    def test_deep_plain_nesting_fails_instead_of_recursing(self):
        from ppswitch_discovery.parsers.openapi import resolve_local_refs

        node = current = {}
        for _ in range(500):
            current["c"] = {}
            current = current["c"]
        _resolved, ok = resolve_local_refs(node, self._document(), frozenset())
        assert ok is False  # structure budget, NOT a RecursionError

    def test_long_arrays_fail(self):
        from ppswitch_discovery.parsers.openapi import resolve_local_refs

        _, ok = resolve_local_refs([{"a": 1}] * 10_000, self._document(), frozenset())
        assert ok is False

    def test_node_budget(self):
        from ppswitch_discovery.parsers.openapi import resolve_local_refs

        wide = {f"k{i}": {"v": i} for i in range(30_000)}
        _, ok = resolve_local_refs(wide, self._document(), frozenset())
        assert ok is False

    def test_ref_sibling_keywords_refused(self):
        from ppswitch_discovery.parsers.openapi import resolve_local_refs

        document = {
            "components": {"schemas": {"T": {"type": "string"}}},
            "node": {"$ref": "#/components/schemas/T", "description": "side"},
        }
        _, ok = resolve_local_refs(document["node"], document, frozenset())
        assert ok is False  # sibling semantics would be silently dropped

    def test_inflated_inline_size_routes_to_needs_review(self):
        from ppswitch_discovery.parsers.openapi import build_candidates_from_openapi

        big_properties = {f"f{i}": {"type": "string"} for i in range(40_000)}
        document = {
            "openapi": "3.1.0",
            "components": {"schemas": {"Big": {
                "type": "object", "properties": big_properties,
            }}},
            "paths": {
                "/v1/big": {
                    "get": {
                        "responses": {"200": {"content": {"application/json": {
                            "schema": {"$ref": "#/components/schemas/Big"},
                        }}}},
                    }
                }
            },
        }
        result = build_candidates_from_openapi(document)
        candidate = next(c for c in result.candidates if c.template == "/v1/big")
        assert candidate.needs_review is True  # over the inline size budget


class TestDEP14Ipv6WrapperCanonicalization:
    def test_nat64_wrapped_metadata_hard_denied(self):
        from ppswitch_executor.network_policy import (
            NetworkPolicyViolation,
            PinningNetworkBackend,
        )

        backend = PinningNetworkBackend(
            ["api.example.com"],
            checker=lambda url, hosts: __import__(
                "ppswitch_executor.network_policy", fromlist=["check_outbound_target"]
            ).check_outbound_target(
                url, hosts, resolver=lambda h: ["64:ff9b::a9fe:a9fe"]
            ),
        )
        with pytest.raises(NetworkPolicyViolation):
            backend.connect_tcp("api.example.com", 443, timeout=1.0)

    def test_v4_mapped_addresses_judged_as_their_ipv4(self):
        import ipaddress

        from ppswitch_executor.network_policy import canonical_ip

        cases = {
            "::ffff:169.254.169.254": "169.254.169.254",
            "::ffff:127.0.0.1": "127.0.0.1",
            "64:ff9b::a00:1": "10.0.0.1",
        }
        for raw, expected in cases.items():
            assert str(canonical_ip(ipaddress.ip_address(raw))) == expected
        # plain addresses pass through unchanged
        assert str(canonical_ip(ipaddress.ip_address("93.184.216.34"))) == "93.184.216.34"
        assert str(canonical_ip(ipaddress.ip_address("2001:db8::1"))) == "2001:db8::1"

    def test_unapproved_v4_mapped_loopback_rejected(self):
        from ppswitch_core.errors import ApiError
        from ppswitch_executor.network_policy import check_outbound_target

        with pytest.raises(ApiError):
            check_outbound_target(
                "https://[::ffff:127.0.0.1]/x", ["example.com"],
                resolver=lambda h: ["::ffff:127.0.0.1"],
            )
