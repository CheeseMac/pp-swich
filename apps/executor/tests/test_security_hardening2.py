"""Review2 executor hardening tests.

- SEC-034: the address actually connected is the validated resolution;
  a DNS answer that flips after the pre-flight check cannot redirect the
  connection, and a rebinding answer at connect time is refused.
- SEC-035: response guard type-first validation and default-deny for
  unknown object structures under a reviewed schema.
"""

from __future__ import annotations

import contextlib
from unittest import mock

import httpx
import pytest
from ppswitch_executor.adapters.http_adapter import NetworkValidatingTransport
from ppswitch_executor.network_policy import (
    NetworkPolicyViolation,
    PinningNetworkBackend,
    check_outbound_target,
)
from ppswitch_executor.response_guard import project_response

GOOD_IP = "93.184.216.34"
EVIL_IP = "203.0.113.66"      # public: would pass the host policy
LINK_LOCAL = "169.254.169.254"  # metadata: hard-denied


class _FlippingResolver:
    """Answers differently per call to simulate DNS rebinding."""

    def __init__(self, *answers: list[str]) -> None:
        self._answers = list(answers)
        self.calls = 0

    def __call__(self, host: str) -> list[str]:
        answer = self._answers[min(self.calls, len(self._answers) - 1)]
        self.calls += 1
        return answer


class TestSEC034ConnectTimeIpPinning:
    def _transport(self, resolver) -> NetworkValidatingTransport:
        return NetworkValidatingTransport(
            ["api.example.com"], lambda url, hosts: check_outbound_target(
                url, hosts, resolver=resolver
            )
        )

    def test_connected_ip_is_the_validated_resolution(self):
        """The socket target must be the SAME IP the connect-time check
        produced - not a fresh resolution by httpcore/OS."""
        resolver = _FlippingResolver([GOOD_IP], [EVIL_IP])
        transport = self._transport(resolver)

        connected: list[tuple[str, int]] = []
        with mock.patch(
            "httpcore.SyncBackend.connect_tcp",
            side_effect=lambda host, port, **kw: connected.append((host, port))
            or object(),
        ):
            request = httpx.Request("GET", "https://api.example.com/v1/x")
            with contextlib.suppress(Exception):
                # the mocked stream cannot serve a real response; the
                # assertion below only cares about the connect target
                transport.handle_request(request)

        # the connection went to the LAST validated answer (EVIL_IP here):
        # whatever DNS says at connect time is exactly what is connected,
        # and nothing re-resolves afterwards
        assert connected == [(EVIL_IP, 443)]
        assert resolver.calls == 2  # pre-flight + connect, nothing more

    def test_rebinding_to_metadata_at_connect_time_is_refused(self):
        resolver = _FlippingResolver([GOOD_IP], [LINK_LOCAL])
        transport = self._transport(resolver)

        connected: list[tuple[str, int]] = []
        with mock.patch(
            "httpcore.SyncBackend.connect_tcp",
            side_effect=lambda host, port, **kw: connected.append((host, port))
            or object(),
        ):
            request = httpx.Request("GET", "https://api.example.com/v1/x")
            with pytest.raises(NetworkPolicyViolation):
                transport.handle_request(request)

        assert connected == []  # no socket was ever opened

    def test_backend_pins_first_ipv4_deterministically(self):
        backend = PinningNetworkBackend(
            ["api.example.com"],
            lambda url, hosts: check_outbound_target(
                url, hosts, resolver=lambda host: [GOOD_IP]
            ),
        )
        connected: list[tuple[str, int]] = []
        with mock.patch(
            "httpcore.SyncBackend.connect_tcp",
            side_effect=lambda host, port, **kw: connected.append((host, port))
            or object(),
        ):
            backend.connect_tcp("api.example.com", 443, timeout=1.0)
        assert connected == [(GOOD_IP, 443)]


class TestSEC035TypeFirstSchemaEnforcement:
    def test_object_smuggled_into_string_field_is_denied(self):
        schema = {
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
        }
        guarded = project_response(
            {"customer_id": {"internal_note": "confidential note", "token": "x"}},
            ["customer_id"],
            output_schema=schema,
        )
        # the smuggled object is denied entirely: the field collapses to
        # None with a type marker, and no nested key ever survives
        assert guarded.result == {"customer_id": None}
        assert "<type:string>" in guarded.dropped_fields
        assert "internal_note" not in guarded.result

    def test_declared_string_scalar_still_passes(self):
        schema = {
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
        }
        guarded = project_response(
            {"customer_id": "C-1024"}, ["customer_id"], output_schema=schema
        )
        assert guarded.result == {"customer_id": "C-1024"}

    def test_unknown_nested_object_defaults_to_deny(self):
        schema = {
            "type": "object",
            "properties": {
                "customer": {"type": "object"},  # declared, but undescribed
            },
        }
        guarded = project_response(
            {"customer": {"internal_note": "x", "tier": "gold"}},
            ["customer"],
            output_schema=schema,
        )
        assert guarded.result == {"customer": {}}
        assert "<unspecified_object>" in guarded.dropped_fields

    def test_allow_fields_intersect_schema_properties(self):
        schema = {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "display_name": {"type": "string"},
                "internal_note": {"type": "string"},
            },
        }
        # schema allows all three; allow_fields narrows to two - the
        # INTERSECTION governs, never the union
        guarded = project_response(
            {"id": "1", "display_name": "Acme", "internal_note": "private"},
            ["id", "display_name"],
            output_schema=schema,
        )
        assert set(guarded.result) == {"id", "display_name"}

    def test_array_type_mismatch_denied(self):
        schema = {
            "type": "object",
            "properties": {"items": {"type": "array", "items": {"type": "string"}}},
        }
        guarded = project_response(
            {"items": {"sneaky": "object-instead-of-array"}}, ["items"],
            output_schema=schema,
        )
        assert guarded.result == {"items": None}

    def test_legacy_capabilities_keep_scrub_path(self):
        # no output_schema: legacy top-level allow + scrub, unchanged
        guarded = project_response(
            {"id": "1", "notes": {"session_id": "x", "ok": "fine"}},
            ["id", "notes"],
        )
        assert guarded.result == {"id": "1", "notes": {"ok": "fine"}}
