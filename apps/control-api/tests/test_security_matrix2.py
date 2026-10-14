"""Review2 security regression matrix: SEC-031 .. SEC-042 (control plane).

Complements test_security_matrix.py (SEC-021..030). Each test names the
review item it locks in; assertions target attack combinations, not
single protections.
"""

from __future__ import annotations

import asyncio
import io
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from ppswitch_control.api.v1.router import read_upload_bounded
from ppswitch_core.errors import ApiError

from .conftest import AGENT_TOKEN, ALICE_TOKEN, CAROL_TOKEN, Demo, auth

DAVE_TOKEN = "dev-token-dave-5c1a"


def _client(demo: Demo) -> TestClient:
    return demo.harness.control


def _agent_id(demo: Demo) -> str:
    listing = _client(demo).get("/api/v1/dev/principals").json()["data"]
    return next(p["id"] for p in listing if p["kind"] == "agent")


@pytest.fixture()
def demo_with_dave(harness, demo):
    """The demo bootstrap plus a plain member (dave) in workspace A."""
    from ppswitch_control.auth import hash_token
    from ppswitch_db.models import PrincipalRow

    session_factory = harness.session_factory
    with session_factory() as session:
        alice = (
            session.query(PrincipalRow)
            .filter_by(token_hash=hash_token(ALICE_TOKEN))
            .one()
        )
        if (
            session.query(PrincipalRow)
            .filter_by(token_hash=hash_token(DAVE_TOKEN))
            .one_or_none()
            is None
        ):
            session.add(
                PrincipalRow(
                    id=uuid.uuid4(), workspace_id=alice.workspace_id, kind="user",
                    display_name="Dave (member)", token_hash=hash_token(DAVE_TOKEN),
                    token_hint=DAVE_TOKEN[:14] + "...", role="member",
                )
            )
            session.commit()
    return demo


class _FakeUpload:
    """UploadFile stand-in that counts bytes actually consumed."""

    def __init__(self, payload: bytes, chunk: int) -> None:
        self._stream = io.BytesIO(payload)
        self._chunk = chunk
        self.consumed = 0

    async def read(self, size: int = -1) -> bytes:
        data = self._stream.read(self._chunk if size < 0 else size)
        self.consumed += len(data)
        return data


class TestSEC031AgentManagementSurface:
    """SEC-031: agents are denied read AND write on management APIs."""

    @pytest.mark.parametrize(
        ("method", "path", "payload"),
        [
            ("POST", "/api/v1/services", {"slug": "x", "name": "X"}),
            ("POST", "/api/v1/workspaces", {"slug": "x2", "name": "X"}),
            ("POST", "/api/v1/connections", None),
            ("POST", "/api/v1/profiles", None),
            ("POST", "/api/v1/operations/{op}/verify", {"evidence": "x"}),
            ("POST", "/api/v1/agent-grants", None),
            ("POST", "/api/v1/delegations", None),
        ],
    )
    def test_agent_cannot_write_management(self, demo, method, path, payload):
        control = _client(demo)
        path = path.replace("{op}", demo.ids["get_operation_id"])
        response = control.request(
            method, path, json=payload, headers=auth(AGENT_TOKEN)
        )
        assert response.status_code == 403, response.text

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/audit-events",
            "/api/v1/endpoints",
            "/api/v1/operations",
            "/api/v1/profiles",
            "/api/v1/connections",
        ],
    )
    def test_agent_cannot_read_management(self, demo, path):
        response = _client(demo).get(path, headers=auth(AGENT_TOKEN))
        assert response.status_code == 403, response.text

    def test_member_cannot_read_workspace_audit(self, demo_with_dave):
        response = _client(demo_with_dave).get(
            "/api/v1/audit-events", headers=auth(DAVE_TOKEN)
        )
        assert response.status_code == 403, response.text

    def test_admin_reads_audit_member_cannot_publish(self, demo_with_dave):
        control = _client(demo_with_dave)
        # dave (member) cannot review/publish even as second human (R-13)
        cap_id = demo_with_dave.ids["capability_id"]
        for action, payload in (
            ("review", {"evidence": "dave was here"}),
            ("publish", {"version": "9.9.9"}),
        ):
            response = control.post(
                f"/api/v1/capabilities/{cap_id}/{action}",
                json=payload, headers=auth(DAVE_TOKEN),
            )
            assert response.status_code == 403, response.text
        # carol (reviewer) reads are fine and audit stays admin-readable
        assert control.get(
            "/api/v1/audit-events", headers=auth(ALICE_TOKEN)
        ).status_code == 200


class TestSEC032AgentDiscoveryBoundaries:
    """SEC-032: agents cannot read drafts, internal endpoints or audit."""

    def _grant(self, demo, capability_id: str) -> None:
        control = _client(demo)
        response = control.post(
            "/api/v1/agent-grants",
            json={
                "agent_principal_id": _agent_id(demo),
                "capability_id": capability_id,
            },
            headers=auth(ALICE_TOKEN),
        )
        assert response.status_code == 201, response.text

    def test_granted_agent_sees_only_published(self, demo):
        control = _client(demo)
        # a second capability that stays in draft
        response = control.post(
            "/api/v1/capabilities",
            json={
                "name": "crm.refund",
                "display_name": "Refund",
                "operation_id": demo.ids["refund_operation_id"],
                "risk_level": "medium",
            },
            headers=auth(ALICE_TOKEN),
        )
        assert response.status_code == 201, response.text
        draft_id = response.json()["data"]["id"]
        self._grant(demo, draft_id)  # even WITH a grant...

        listed = control.get("/api/v1/capabilities", headers=auth(AGENT_TOKEN))
        assert listed.status_code == 200
        names = {c["name"] for c in listed.json()["data"]}
        assert "crm.refund" not in names  # draft invisible
        detail = control.get(
            f"/api/v1/capabilities/{draft_id}", headers=auth(AGENT_TOKEN)
        )
        assert detail.status_code == 404  # existence not revealed


class TestSEC033SharedAgentBindingIsolation:
    """SEC-033: two users sharing an agent keep separate bindings."""

    def test_users_do_not_overwrite_each_other(self, demo_with_dave):
        demo = demo_with_dave
        control = _client(demo)
        ids = demo.ids
        agent_id = _agent_id(demo)

        # dave needs his own connection + profile (demo dataset B is alice's)
        created = control.post(
            "/api/v1/connections",
            json={"service_id": ids["service_id"], "auth_scheme": "api_key",
                  "demo_dataset": "B"},
            headers=auth(DAVE_TOKEN),
        )
        assert created.status_code == 201, created.text
        dave_connection = created.json()["data"]["id"]
        created = control.post(
            "/api/v1/profiles",
            json={"service_id": ids["service_id"], "endpoint_id": ids["endpoint_id"],
                  "connection_id": dave_connection, "name": "Dave B"},
            headers=auth(DAVE_TOKEN),
        )
        assert created.status_code == 201, created.text
        dave_profile = created.json()["data"]["id"]

        for token in (ALICE_TOKEN, DAVE_TOKEN):
            created = control.post(
                "/api/v1/delegations",
                json={"agent_principal_id": agent_id, "scopes": []},
                headers=auth(token),
            )
            assert created.status_code == 201, created.text

        # both users bind the SAME agent context with their own profiles
        for token, profile in ((ALICE_TOKEN, ids["profile_A"]),
                               (DAVE_TOKEN, dave_profile)):
            response = control.put(
                f"/api/v1/active-bindings/{ids['service_id']}/agent:{agent_id}",
                json={"profile_id": profile, "expected_version": 0},
                headers=auth(token),
            )
            assert response.status_code == 200, response.text

        # alice rebinding must NOT bump or touch dave's binding
        response = control.put(
            f"/api/v1/active-bindings/{ids['service_id']}/agent:{agent_id}",
            json={"profile_id": ids["profile_B"], "expected_version": 1},
            headers=auth(ALICE_TOKEN),
        )
        assert response.status_code == 200, response.text
        dave_bindings = control.get(
            "/api/v1/active-bindings", headers=auth(DAVE_TOKEN)
        ).json()["data"]
        agent_binding = next(
            b for b in dave_bindings if b["context_key"] == f"agent:{agent_id}"
        )
        assert agent_binding["version"] == 1  # untouched by alice's rebind
        assert agent_binding["active_profile_id"] == dave_profile

        # grant + both delegations let the agent run under EITHER user -
        # selected ONLY via a signed context ticket the user issued (SEC-046)
        grant = control.post(
            "/api/v1/agent-grants",
            json={"agent_principal_id": agent_id, "capability_id": ids["capability_id"]},
            headers=auth(ALICE_TOKEN),
        )
        assert grant.status_code == 201, grant.text
        for token, expected_profile in (
            (ALICE_TOKEN, "CRM Demo B"),   # alice rebound to B above
            (DAVE_TOKEN, "Dave B"),
        ):
            delegation_id = next(
                d["id"] for d in control.get(
                    "/api/v1/delegations", headers=auth(token)
                ).json()["data"]
                if d["agent_principal_id"] == agent_id and d["active"]
            )
            ticket = control.post(
                f"/api/v1/delegations/{delegation_id}/context",
                headers=auth(token),
            )
            assert ticket.status_code == 201, ticket.text
            invocation = control.post(
                "/api/v1/invocations",
                json={
                    "capability": "crm.get_customer",
                    "arguments": {"customer_id": "C-1024"},
                    "context_key": f"agent:{agent_id}",
                    "delegation_context": ticket.json()["data"]["context"],
                },
                headers=auth(AGENT_TOKEN),
            )
            assert invocation.status_code == 200, invocation.text
            assert invocation.json()["data"]["profile_label"] == expected_profile
        # the legacy parameter-form user selection is gone for good
        rejected = control.post(
            "/api/v1/invocations",
            json={
                "capability": "crm.get_customer",
                "arguments": {"customer_id": "C-1024"},
                "context_key": f"agent:{agent_id}:{_principal_id(control, ALICE_TOKEN)}",
            },
            headers=auth(AGENT_TOKEN),
        )
        assert rejected.status_code == 400, rejected.text  # pattern-rejected

    def test_agent_binding_requires_delegation(self, demo):
        control = _client(demo)
        agent_id = _agent_id(demo)
        response = control.put(
            f"/api/v1/active-bindings/{demo.ids['service_id']}/agent:{agent_id}",
            json={"profile_id": demo.ids["profile_A"], "expected_version": 0},
            headers=auth(CAROL_TOKEN),
        )
        assert response.status_code == 403, response.text
        assert "delegation" in response.json()["error"]["message"]


def _principal_id(control: TestClient, token: str) -> str:
    listing = control.get("/api/v1/dev/principals").json()["data"]
    return next(p["id"] for p in listing if p["token_hint"].startswith(token[:14]))


class TestSEC037ExpiredGrantCannotReplayIdempotency:
    """SEC-037: idempotent replay re-authorizes grants first (R-07)."""

    def test_expired_grant_gets_403_not_cached_result(self, demo, harness):
        control = _client(demo)
        ids = demo.ids
        agent_id = _agent_id(demo)
        assert control.post(
            "/api/v1/agent-grants",
            json={"agent_principal_id": agent_id, "capability_id": ids["capability_id"]},
            headers=auth(ALICE_TOKEN),
        ).status_code == 201
        assert control.post(
            "/api/v1/delegations",
            json={"agent_principal_id": agent_id},
            headers=auth(ALICE_TOKEN),
        ).status_code == 201
        assert control.put(
            f"/api/v1/active-bindings/{ids['service_id']}/agent:{agent_id}",
            json={"profile_id": ids["profile_A"], "expected_version": 0},
            headers=auth(ALICE_TOKEN),
        ).status_code == 200

        key = f"idem-agent-{uuid.uuid4().hex[:8]}"
        first = control.post(
            "/api/v1/invocations",
            json={"capability": "crm.get_customer", "arguments": {"customer_id": "C-1024"},
                  "idempotency_key": key},
            headers=auth(AGENT_TOKEN),
        )
        assert first.status_code == 200, first.text

        # expire the grant out-of-band (revocation path)
        from datetime import timedelta

        from ppswitch_db.models import AgentGrantRow, utcnow

        with harness.session_factory() as session:
            rows = (
                session.query(AgentGrantRow)
                .filter_by(agent_principal_id=uuid.UUID(agent_id))
                .all()
            )
            assert rows
            for row in rows:
                row.expires_at = utcnow() - timedelta(minutes=1)
            session.commit()

        replay = control.post(
            "/api/v1/invocations",
            json={"capability": "crm.get_customer", "arguments": {"customer_id": "C-1024"},
                  "idempotency_key": key},
            headers=auth(AGENT_TOKEN),
        )
        assert replay.status_code == 403, replay.text  # NOT the cached result


class TestSEC038BoundedUpload:
    """SEC-038 (R-08): oversized uploads abort mid-read with bounded memory."""

    def test_reader_stops_at_limit_plus_one_chunk(self):
        payload = b"x" * (1024 * 1024)  # 1MB payload
        upload = _FakeUpload(payload, chunk=64 * 1024)
        with pytest.raises(ApiError):
            asyncio.run(read_upload_bounded(upload, limit=128 * 1024))
        # memory bound: consumed <= limit + one chunk
        assert upload.consumed <= 128 * 1024 + 64 * 1024

    def test_api_rejects_oversized_import(self, demo):
        control = _client(demo)
        big = json.dumps({"log": {"entries": []}, "padding": "y" * (6 * 1024 * 1024)})
        response = control.post(
            "/api/v1/discoveries/imports",
            files={"file": ("big.har.json", big.encode(), "application/json")},
            data={"source_type": "har", "service_id": demo.ids["service_id"]},
            headers=auth(ALICE_TOKEN),
        )
        assert response.status_code == 413, response.text


class TestSEC040PublishToolNameCollision:
    """SEC-040 (R-10): colliding public tool names cannot publish."""

    def test_colliding_capability_name_is_refused(self, demo):
        control = _client(demo)
        ids = demo.ids
        # svc.foo_bar and svc_foo.bar both map to the MCP name svc_foo_bar
        for name, op_key in (
            ("svc_foo.bar", "get_operation_id"),
            ("svc.foo_bar", "refund_operation_id"),
        ):
            created = control.post(
                "/api/v1/capabilities",
                json={
                    "name": name,
                    "display_name": name,
                    "operation_id": ids[op_key],
                    "risk_level": "low",
                },
                headers=auth(ALICE_TOKEN),
            )
            assert created.status_code == 201, created.text
            cap_id = created.json()["data"]["id"]
            for action in ("review", "verify"):
                assert control.post(
                    f"/api/v1/capabilities/{cap_id}/{action}",
                    json={"evidence": "collision check"},
                    headers=auth(CAROL_TOKEN),
                ).status_code == 200

        # publish the first: fine
        listing = control.get("/api/v1/capabilities", headers=auth(ALICE_TOKEN)).json()["data"]
        by_name = {c["name"]: c["id"] for c in listing}
        assert control.post(
            f"/api/v1/capabilities/{by_name['svc_foo.bar']}/publish",
            json={"version": "1.0.0"}, headers=auth(CAROL_TOKEN),
        ).status_code == 200

        # publish the colliding second one: 409, never a silent shadow
        collision = control.post(
            f"/api/v1/capabilities/{by_name['svc.foo_bar']}/publish",
            json={"version": "1.0.0"}, headers=auth(CAROL_TOKEN),
        )
        assert collision.status_code == 409, collision.text
        assert collision.json()["error"]["details"]["tool_name"] == "svc_foo_bar"


class TestSEC041ScopeIntersection:
    """SEC-041 (R-06): grant / connection / delegation scopes intersect."""

    def _setup(self, demo, connection_scopes, delegation_scopes):
        control = _client(demo)
        ids = demo.ids
        agent_id = _agent_id(demo)
        assert control.post(
            "/api/v1/agent-grants",
            json={"agent_principal_id": agent_id,
                  "capability_id": ids["capability_id"],
                  "scopes": ["read"]},
            headers=auth(ALICE_TOKEN),
        ).status_code == 201
        assert control.post(
            "/api/v1/delegations",
            json={"agent_principal_id": agent_id, "scopes": delegation_scopes},
            headers=auth(ALICE_TOKEN),
        ).status_code == 201
        created = control.post(
            "/api/v1/connections",
            json={"service_id": ids["service_id"], "auth_scheme": "api_key",
                  "demo_dataset": "A", "scopes": connection_scopes},
            headers=auth(ALICE_TOKEN),
        )
        assert created.status_code == 201, created.text
        connection_id = created.json()["data"]["id"]
        created = control.post(
            "/api/v1/profiles",
            json={"service_id": ids["service_id"], "endpoint_id": ids["endpoint_id"],
                  "connection_id": connection_id, "name": f"scoped-{uuid.uuid4().hex[:6]}"},
            headers=auth(ALICE_TOKEN),
        )
        assert created.status_code == 201, created.text
        assert control.put(
            f"/api/v1/active-bindings/{ids['service_id']}/agent:{agent_id}",
            json={"profile_id": created.json()["data"]["id"], "expected_version": 0},
            headers=auth(ALICE_TOKEN),
        ).status_code == 200
        return control

    def test_disjoint_connection_scopes_denied(self, demo):
        control = self._setup(demo, connection_scopes=["write"], delegation_scopes=[])
        response = control.post(
            "/api/v1/invocations",
            json={"capability": "crm.get_customer", "arguments": {"customer_id": "C-1024"}},
            headers=auth(AGENT_TOKEN),
        )
        assert response.status_code == 403, response.text
        assert "scope" in response.json()["error"]["message"].lower()

    def test_overlapping_scopes_allowed(self, demo):
        control = self._setup(demo, connection_scopes=["read", "write"],
                              delegation_scopes=["read"])
        response = control.post(
            "/api/v1/invocations",
            json={"capability": "crm.get_customer", "arguments": {"customer_id": "C-1024"}},
            headers=auth(AGENT_TOKEN),
        )
        assert response.status_code == 200, response.text

    def test_narrow_delegation_blocks(self, demo):
        control = self._setup(demo, connection_scopes=["read"],
                              delegation_scopes=["admin"])
        response = control.post(
            "/api/v1/invocations",
            json={"capability": "crm.get_customer", "arguments": {"customer_id": "C-1024"}},
            headers=auth(AGENT_TOKEN),
        )
        assert response.status_code == 403, response.text


class TestSEC042PrivateResourceVisibility:
    """SEC-042 (R-11): private resources are invisible to non-owners."""

    def test_private_service_hidden_from_member(self, demo_with_dave):
        control = _client(demo_with_dave)
        created = control.post(
            "/api/v1/services",
            json={"slug": f"secret-{uuid.uuid4().hex[:6]}", "name": "Secret",
                  "visibility": "private"},
            headers=auth(ALICE_TOKEN),
        )
        assert created.status_code == 201, created.text

        alice_view = control.get("/api/v1/services", headers=auth(ALICE_TOKEN)).json()["data"]
        dave_view = control.get("/api/v1/services", headers=auth(DAVE_TOKEN)).json()["data"]
        alice_slugs = {s["slug"] for s in alice_view}
        dave_slugs = {s["slug"] for s in dave_view}
        # the bootstrap "crm" service is private by default: owner-only too
        assert "crm" in alice_slugs and "crm" not in dave_slugs
        secret = next(s for s in alice_view if s["name"] == "Secret")
        assert secret["slug"] not in dave_slugs  # private: owner only

    def test_private_capability_hidden_from_member(self, demo_with_dave):
        control = _client(demo_with_dave)
        ids = demo_with_dave.ids
        created = control.post(
            "/api/v1/capabilities",
            json={
                "name": "crm.private_probe",
                "display_name": "private probe",
                "operation_id": ids["refund_operation_id"],
                "risk_level": "low",
            },
            headers=auth(ALICE_TOKEN),
        )
        assert created.status_code == 201
        cap_id = created.json()["data"]["id"]

        dave_list = control.get("/api/v1/capabilities", headers=auth(DAVE_TOKEN)).json()["data"]
        assert all(c["id"] != cap_id for c in dave_list)
        assert control.get(
            f"/api/v1/capabilities/{cap_id}", headers=auth(DAVE_TOKEN)
        ).status_code == 404
        assert control.get(
            f"/api/v1/capabilities/{cap_id}", headers=auth(ALICE_TOKEN)
        ).status_code == 200


class TestSEC036OpenApiImportSanitization:
    """SEC-036 (P0-5): synthetic secrets in examples never persist."""

    CANARY = "CANARY_NOT_REAL_ABC123"
    SK = "sk-AZ123456789012345678"

    def _openapi_doc(self) -> dict:
        return {
            "openapi": "3.1.0",
            "info": {"title": "Canary API", "version": "1.0.0"},
            "paths": {
                "/v1/orders/{order_id}": {
                    "get": {
                        "operationId": "getOrder",
                        "parameters": [
                            {
                                "name": "order_id", "in": "path", "required": True,
                                "schema": {"type": "string", "example": self.CANARY},
                            },
                            {
                                "name": "token", "in": "query",
                                "schema": {
                                    "type": "string",
                                    "default": self.SK,
                                    "examples": [self.CANARY],
                                    "x-example": self.SK,
                                },
                            },
                        ],
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "receipt_url": {
                                                    "type": "string",
                                                    "example": f"https://x/{self.SK}",
                                                },
                                                "status": {
                                                    "type": "string",
                                                    "enum": ["ok", self.CANARY],
                                                },
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

    def test_canaries_neither_stored_nor_served(self, demo, harness):
        control = _client(demo)
        response = control.post(
            "/api/v1/discoveries/imports",
            files={"file": ("canary.json", json.dumps(self._openapi_doc()).encode(),
                           "application/json")},
            data={"source_type": "openapi", "service_id": demo.ids["service_id"]},
            headers=auth(ALICE_TOKEN),
        )
        assert response.status_code == 201, response.text

        # not in the served operation contract (the agent-visible surface)
        operations = control.get(
            f"/api/v1/operations?service_id={demo.ids['service_id']}",
            headers=auth(ALICE_TOKEN),
        ).json()["data"]
        order_op = next(o for o in operations if "orders" in o["normalized_path"])
        detail = control.get(
            f"/api/v1/operations/{order_op['id']}", headers=auth(ALICE_TOKEN)
        )
        assert self.CANARY not in detail.text
        assert self.SK not in detail.text

        # not in the database either (direct storage check)
        from ppswitch_db.models import OperationVersionRow

        with harness.session_factory() as session:
            rows = (
                session.query(OperationVersionRow)
                .filter_by(operation_id=uuid.UUID(order_op["id"]))
                .all()
            )
            assert rows
            for row in rows:
                blob = json.dumps(row.input_schema) + json.dumps(row.output_schema)
                assert self.CANARY not in blob
                assert self.SK not in blob

    def test_yaml_openapi_import_supported(self, demo):
        import yaml

        control = _client(demo)
        doc = yaml.safe_dump(self._openapi_doc())
        response = control.post(
            "/api/v1/discoveries/imports",
            files={"file": ("canary.yaml", doc.encode(), "application/yaml")},
            data={"source_type": "openapi", "service_id": demo.ids["service_id"]},
            headers=auth(ALICE_TOKEN),
        )
        assert response.status_code == 201, response.text
