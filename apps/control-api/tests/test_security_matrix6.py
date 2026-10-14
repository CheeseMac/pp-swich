"""Review6 regression: DATA-15/16/17 (result lifecycle production gaps)
+ PR-NET-13 (bounded local $ref resolution, allOf merge) + an
end-to-end encrypted-lifecycle test through real HTTP calls.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from .conftest import (
    ALICE_TOKEN,
    CAROL_TOKEN,
    Demo,
    auth,
)

BOB_TOKEN = "dev-token-bob-77e0"


def _client(demo: Demo) -> TestClient:
    return demo.harness.control


def _seed_invocation(control) -> str:
    """One real invocation through the API (FK-valid template row)."""
    response = control.post(
        "/api/v1/invocations",
        json={"capability": "crm.get_customer", "arguments": {"customer_id": "C-1024"}},
        headers=auth(ALICE_TOKEN),
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["invocation_id"]


class TestDATA15LegacyPlaintext:
    def test_legacy_deadlineless_plaintext_is_purged(self, demo, harness):
        """A pre-lifecycle row (result set, no deadline) is wiped by the
        FIRST purge of any kind - migration f3a62c8e1b47 does it at
        upgrade; this runtime sweep is the safety net."""
        from ppswitch_db.models import InvocationRow

        _seed_invocation(_client(demo))
        with harness.session_factory() as session:
            template = session.query(InvocationRow).order_by(
                InvocationRow.created_at
            ).first()
            assert template is not None
            legacy = InvocationRow(
                id=uuid.uuid4(), workspace_id=template.workspace_id,
                capability_version_id=template.capability_version_id,
                capability_name=template.capability_name,
                capability_version=template.capability_version,
                principal_id=template.principal_id,
                profile_id=template.profile_id, profile_snapshot={},
                arguments_digest="d", status="succeeded",
                result={"legacy": "plaintext"},
                result_expires_at=None,
            )
            session.add(legacy)
            session.commit()
            legacy_id = legacy.id

        control = _client(demo)
        purged = control.post(
            "/api/v1/admin/purge-results", headers=auth(ALICE_TOKEN)
        ).json()["data"]["purged"]
        assert purged >= 1
        with harness.session_factory() as session:
            row = session.get(InvocationRow, legacy_id)
            assert row.result is None and row.result_encrypted is None


class TestDATA16TenantScopedPurge:
    def test_workspace_admin_cannot_purge_other_workspaces(self, demo, harness):
        from ppswitch_db.models import InvocationRow, WorkspaceRow

        # an expired row reusing real foreign keys from a seeded call
        _seed_invocation(_client(demo))
        with harness.session_factory() as session:
            demo_row = session.query(InvocationRow).order_by(
                InvocationRow.created_at
            ).first()
            assert demo_row is not None
            bob_ws = (
                session.query(WorkspaceRow)
                .filter(WorkspaceRow.slug.like("demo-b-%"))
                .one()
            )
            from datetime import timedelta

            from ppswitch_db.models import utcnow

            foreign = InvocationRow(
                id=uuid.uuid4(), workspace_id=bob_ws.id,
                capability_version_id=demo_row.capability_version_id,
                capability_name=demo_row.capability_name,
                capability_version=demo_row.capability_version,
                principal_id=demo_row.principal_id,
                profile_id=demo_row.profile_id, profile_snapshot={},
                arguments_digest="d", status="succeeded",
                result={"foreign": "data"},
                result_expires_at=utcnow() - timedelta(minutes=5),
            )
            session.add(foreign)
            session.commit()
            foreign_id = foreign.id

        # alice purges HER workspace only
        control = _client(demo)
        response = control.post(
            "/api/v1/admin/purge-results", headers=auth(ALICE_TOKEN)
        )
        assert response.status_code == 200
        with harness.session_factory() as session:
            row = session.get(InvocationRow, foreign_id)
            assert row.result == {"foreign": "data"}  # untouched: other tenant

        # the global startup sweep (unscoped) does clear it
        from ppswitch_control.services.result_read import purge_expired_results

        with harness.session_factory() as session:
            purge_expired_results(session)  # internal maintenance form
            row = session.get(InvocationRow, foreign_id)
            assert row.result is None

    def test_member_cannot_purge(self, demo):
        # bob exists in the plain demo fixture (member of another org)
        from .conftest import BOB_TOKEN

        assert _client(demo).post(
            "/api/v1/admin/purge-results", headers=auth(BOB_TOKEN)
        ).status_code == 403


class TestDATA17KeyRotation:
    def test_multi_key_read_window_and_reencrypt(self, demo, harness):
        from cryptography.fernet import Fernet
        from ppswitch_control.result_vault import ResultVault
        from ppswitch_control.services.result_read import reencrypt_results
        from ppswitch_control.settings import ControlSettings

        key_a = Fernet.generate_key().decode()
        key_b = Fernet.generate_key().decode()

        # data written under KEY_A (previous generation)
        old_vault = ResultVault(key_a)
        ciphertext_a = old_vault.encrypt({"id": "C-7"})

        # rotation: KEY_B is primary, KEY_A stays in the read window
        rotated = ControlSettings(
            db_url=demo.harness.db_url, demo_mode=True,
            result_encryption_key=key_b, result_previous_keys=key_a,
        )
        read_vault = ResultVault(
            rotated.result_encryption_key,
            previous_keys=[rotated.result_previous_keys],
        )
        assert read_vault.decrypt(ciphertext_a) == {"id": "C-7"}

        # re-encrypt storage under the primary; then the old key can be
        # retired completely
        from ppswitch_db.models import InvocationRow

        _seed_invocation(_client(demo))
        with harness.session_factory() as session:
            template = session.query(InvocationRow).order_by(
                InvocationRow.created_at
            ).first()
            assert template is not None
            row = InvocationRow(
                id=uuid.uuid4(), workspace_id=template.workspace_id,
                capability_version_id=template.capability_version_id,
                capability_name=template.capability_name,
                capability_version=template.capability_version,
                principal_id=template.principal_id,
                profile_id=template.profile_id, profile_snapshot={},
                arguments_digest="d", status="succeeded",
                result_encrypted=ciphertext_a,
            )
            session.add(row)
            session.commit()
            row_id = row.id

        with harness.session_factory() as session:
            stats = reencrypt_results(session, rotated)
        assert stats == {"rekeyed": 1, "failed": 0}
        with harness.session_factory() as session:
            row = session.get(InvocationRow, row_id)
        final = ResultVault(key_b)  # ONLY the new key
        assert final.decrypt(row.result_encrypted) == {"id": "C-7"}
        assert ResultVault(key_a).decrypt(row.result_encrypted) is None


class TestEncryptedLifecycleEndToEnd:
    """The review's requested full-chain test: real invocation -> stored
    ciphertext -> HTTP history read -> retention expiry -> purge."""

    def test_full_chain(self, harness, tmp_path):
        from cryptography.fernet import Fernet
        from ppswitch_control.app import create_app
        from ppswitch_control.settings import ControlSettings
        from ppswitch_db.migrate import run_upgrade

        db_url = f"sqlite:///{tmp_path / 'enc-e2e.db'}"
        run_upgrade(db_url)

        # principals in the fresh database
        from ppswitch_control.auth import hash_token
        from ppswitch_db.models import PrincipalRow, WorkspaceRow
        from ppswitch_db.session import create_engine_for_url, create_session_factory

        engine = create_engine_for_url(db_url)
        factory = create_session_factory(engine)
        with factory() as session:
            ws = WorkspaceRow(id=uuid.uuid4(), slug="enc-e2e", name="enc")
            session.add(ws)
            session.flush()
            session.add_all([
                PrincipalRow(
                    id=uuid.uuid4(), workspace_id=ws.id, kind="user",
                    display_name="Alice", token_hash=hash_token(ALICE_TOKEN),
                    token_hint="enc...", role="admin",
                ),
                PrincipalRow(
                    id=uuid.uuid4(), workspace_id=ws.id, kind="user",
                    display_name="Carol", token_hash=hash_token(CAROL_TOKEN),
                    token_hint="enc...", role="reviewer",
                ),
            ])
            session.commit()

        key = Fernet.generate_key().decode()
        from ppswitch_executor.main import ExecutorSettings
        from ppswitch_executor.main import create_app as create_executor_app
        from ppswitch_secrets import MockSecretProvider

        executor = create_executor_app(
            ExecutorSettings(demo_mode=True), provider=MockSecretProvider()
        )
        from fastapi.testclient import TestClient as TC

        executor_client = TC(executor)
        control = TC(create_app(
            ControlSettings(
                db_url=db_url, demo_mode=True,
                executor_url="http://executor.test.internal",
                result_encryption_key=key,
            ),
            executor_client=executor_client, run_migrations=False,
        ))

        # bootstrap the chain via the API
        control.post("/api/v1/services", json={"slug": "crm", "name": "CRM"},
                     headers=auth(ALICE_TOKEN))
        services = control.get("/api/v1/services", headers=auth(ALICE_TOKEN)).json()["data"]
        service_id = services[0]["id"]
        control.post(
            f"/api/v1/services/{service_id}/endpoints",
            json={"environment": "demo", "base_url": "mock://demo", "status": "approved"},
            headers=auth(ALICE_TOKEN),
        )
        endpoints = control.get(
            f"/api/v1/endpoints?service_id={service_id}", headers=auth(ALICE_TOKEN)
        ).json()["data"]
        endpoint_id = endpoints[0]["id"]

        from .conftest import HAR_FIXTURE

        with open(HAR_FIXTURE, "rb") as f:
            control.post(
                "/api/v1/discoveries/imports",
                files={"file": ("crm.har.json", f, "application/json")},
                data={"source_type": "har", "service_id": service_id},
                headers=auth(ALICE_TOKEN),
            )
        operations = control.get(
            f"/api/v1/operations?service_id={service_id}", headers=auth(ALICE_TOKEN)
        ).json()["data"]
        get_op = next(o for o in operations if o["method"] == "GET")
        control.post(
            f"/api/v1/operations/{get_op['id']}/verify",
            json={"evidence": "e2e"}, headers=auth(ALICE_TOKEN),
        )
        created = control.post(
            "/api/v1/capabilities",
            json={
                "name": "crm.get_customer", "display_name": "Get",
                "operation_id": get_op["id"], "risk_level": "low",
                "allow_fields": ["id", "display_name"],
            },
            headers=auth(ALICE_TOKEN),
        )
        cap_id = created.json()["data"]["id"]
        for action in ("review", "verify"):
            control.post(
                f"/api/v1/capabilities/{cap_id}/{action}",
                json={"evidence": "e2e"}, headers=auth(CAROL_TOKEN),
            )
        control.post(
            f"/api/v1/capabilities/{cap_id}/publish",
            json={"version": "1.0.0"}, headers=auth(CAROL_TOKEN),
        )
        control.post(
            "/api/v1/connections",
            json={"service_id": service_id, "auth_scheme": "api_key",
                  "demo_dataset": "A"},
            headers=auth(ALICE_TOKEN),
        )
        connections = control.get(
            "/api/v1/connections", headers=auth(ALICE_TOKEN)
        ).json()["data"]
        control.post(
            "/api/v1/profiles",
            json={"service_id": service_id, "endpoint_id": endpoint_id,
                  "connection_id": connections[0]["id"], "name": "P"},
            headers=auth(ALICE_TOKEN),
        )
        profiles = control.get(
            f"/api/v1/profiles?service_id={service_id}", headers=auth(ALICE_TOKEN)
        ).json()["data"]
        control.put(
            f"/api/v1/active-bindings/{service_id}/personal",
            json={"profile_id": profiles[0]["id"], "expected_version": 0},
            headers=auth(ALICE_TOKEN),
        )

        # REAL invocation -> immediate response carries the result...
        executed = control.post(
            "/api/v1/invocations",
            json={"capability": "crm.get_customer", "arguments": {"customer_id": "C-1024"}},
            headers=auth(ALICE_TOKEN),
        )
        assert executed.status_code == 200, executed.text
        assert executed.json()["data"]["result"] is not None
        invocation_id = executed.json()["data"]["invocation_id"]

        # ...while storage is ciphertext-only
        from ppswitch_db.models import InvocationRow

        with factory() as session:
            row = session.get(InvocationRow, uuid.UUID(invocation_id))
            assert row.result is None
            assert row.result_encrypted is not None
            assert "C-1024" not in row.result_encrypted

        # HTTP history read decrypts transparently
        detail = control.get(
            f"/api/v1/invocations/{invocation_id}", headers=auth(ALICE_TOKEN)
        ).json()["data"]
        assert detail["result"] is not None

        # retention deadline passes: read becomes empty, purge wipes
        from datetime import timedelta

        from ppswitch_db.models import utcnow

        with factory() as session:
            row = session.get(InvocationRow, uuid.UUID(invocation_id))
            row.result_expires_at = utcnow() - timedelta(minutes=1)
            session.commit()
        detail = control.get(
            f"/api/v1/invocations/{invocation_id}", headers=auth(ALICE_TOKEN)
        ).json()["data"]
        assert detail["result"] is None
        control.post("/api/v1/admin/purge-results", headers=auth(ALICE_TOKEN))
        with factory() as session:
            row = session.get(InvocationRow, uuid.UUID(invocation_id))
            assert row.result_encrypted is None

        engine.dispose()


class TestPRNET13BoundedRefResolution:
    """Discovery-side local $ref resolution with cycle/depth guards."""

    def _document(self):
        return {
            "openapi": "3.1.0",
            "components": {
                "schemas": {
                    "Customer": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "tier": {"$ref": "#/components/schemas/Tier"},
                        },
                    },
                    "Tier": {"type": "string", "enum": ["gold", "silver"]},
                    "LoopA": {
                        "type": "object",
                        "properties": {"b": {"$ref": "#/components/schemas/LoopB"}},
                    },
                    "LoopB": {
                        "type": "object",
                        "properties": {"a": {"$ref": "#/components/schemas/LoopA"}},
                    },
                }
            },
            "paths": {
                "/v1/customers/{id}": {
                    "get": {
                        "parameters": [
                            {"name": "id", "in": "path", "required": True,
                             "schema": {"$ref": "#/components/schemas/Tier"}},
                        ],
                        "responses": {"200": {"content": {"application/json": {
                            "schema": {"$ref": "#/components/schemas/Customer"},
                        }}}},
                    }
                },
                "/v1/loop": {
                    "get": {
                        "responses": {"200": {"content": {"application/json": {
                            "schema": {"$ref": "#/components/schemas/LoopA"},
                        }}}},
                    }
                },
                "/v1/external": {
                    "get": {
                        "responses": {"200": {"content": {"application/json": {
                            "schema": {"$ref": "https://evil.example.com/x.json"},
                        }}}},
                    }
                },
            },
        }

    def test_local_refs_resolve_inline(self):
        from ppswitch_discovery.parsers.openapi import build_candidates_from_openapi

        result = build_candidates_from_openapi(self._document())
        by_path = {c.template: c for c in result.candidates}
        customers = by_path["/v1/customers/{id}"]
        assert customers.needs_review is False
        # nested $ref (Customer -> Tier) is fully inlined
        blob = str(customers.output_schema)
        assert "$ref" not in blob
        assert "gold" in blob  # Tier's enum survived inline

    def test_cycles_and_external_refs_fall_to_needs_review(self):
        from ppswitch_discovery.parsers.openapi import build_candidates_from_openapi

        result = build_candidates_from_openapi(self._document())
        by_path = {c.template: c for c in result.candidates}
        assert by_path["/v1/loop"].needs_review is True
        assert by_path["/v1/external"].needs_review is True
        assert by_path["/v1/external"].output_schema == {}


class TestPRNET13GuardAllOfMerge:
    def test_supported_allof_merges_and_projects(self):
        from ppswitch_executor.response_guard import project_response

        schema = {
            "allOf": [
                {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "secret": {"type": "string"},
                    },
                },
                {
                    "type": "object",
                    "properties": {"tier": {"type": "string"}},
                    "required": ["tier"],
                },
            ]
        }
        guarded = project_response(
            {"id": "C-1", "tier": "gold", "internal": "x", "secret": "s"},
            ["id", "tier", "secret"],
            output_schema=schema,
        )
        # merged whitelist projects; sensitive NAME still denied at any depth
        assert guarded.result == {"id": "C-1", "tier": "gold"}
        assert "secret" in guarded.dropped_fields

    def test_unsupported_allof_shapes_stay_denied(self):
        from ppswitch_executor.response_guard import project_response

        # nested composition inside a branch
        with_nested = {
            "allOf": [
                {"type": "object", "properties": {"a": {"type": "string"}}},
                {"type": "object", "properties": {"b": {
                    "anyOf": [{"type": "string"}]
                }}},
            ]
        }
        guarded = project_response(
            {"a": "x", "b": "y"}, ["a", "b"], output_schema=with_nested
        )
        assert guarded.result == {"a": "x", "b": None}
