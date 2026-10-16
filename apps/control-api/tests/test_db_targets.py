"""FEAT-04 control-plane tests: db target lifecycle, structured db
execution specs, and the fail-closed lifecycle checks at plan-build time.

Runs on SQLite (and the PG matrix via the shared harness). The full
executor-backed chain (real discovery + invocation) lives in
tests/e2e/test_db_discovery.py, which the CI postgres job executes.

Acceptance mapping (docs/38 section 1): item 2 (no SQL surface, no
out-of-scope schemas/fields), item 4 (revocation takes effect at
plan-build), item 6 (independent approval type).
"""

from __future__ import annotations

import uuid

import pytest
from ppswitch_core.errors import ApiError, ErrorCode

from .conftest import AGENT_TOKEN, ALICE_TOKEN, CAROL_TOKEN

pytestmark = pytest.mark.usefixtures("harness")


def _alice() -> dict:
    return {"Authorization": f"Bearer {ALICE_TOKEN}"}


def _carol() -> dict:
    return {"Authorization": f"Bearer {CAROL_TOKEN}"}


def _agent() -> dict:
    return {"Authorization": f"Bearer {AGENT_TOKEN}"}


def _create_service(control) -> str:
    response = control.post(
        "/api/v1/services",
        json={"slug": f"crmdb-{uuid.uuid4().hex[:6]}", "name": "CRM DB"},
        headers=_alice(),
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


def _create_target(control, service_id: str, *, schemas=("crm",), **overrides) -> dict:
    payload = {
        "engine": "postgres",
        "host": "127.0.0.1",
        "port": 5432,
        "database": "crmdb",
        "allowed_schemas": list(schemas),
    }
    payload.update(overrides)
    response = control.post(
        f"/api/v1/services/{service_id}/db-targets", json=payload, headers=_alice()
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _create_postgres_connection(control, service_id: str) -> str:
    response = control.post(
        "/api/v1/connections",
        json={
            "service_id": service_id,
            "auth_scheme": "postgres",
            "secret_name": f"crm-db-ro-{uuid.uuid4().hex[:6]}",
        },
        headers=_alice(),
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


class TestDbTargetLifecycle:
    def test_create_validates_coordinates_and_schemas(self, harness):
        control = harness.control
        service_id = _create_service(control)
        bad = [
            {"engine": "mysql"},
            {"port": 0},
            {"database": "crm db"},
            {"allowed_schemas": []},
            {"allowed_schemas": ["crm", "crm"]},
            {"allowed_schemas": ["crm-v2"]},
            {"host": "bad host with spaces"},
        ]
        for overrides in bad:
            payload = {
                "engine": "postgres", "host": "127.0.0.1", "port": 5432,
                "database": "crmdb", "allowed_schemas": ["crm"],
            }
            payload.update(overrides)
            response = control.post(
                f"/api/v1/services/{service_id}/db-targets", json=payload, headers=_alice()
            )
            assert response.status_code == 400, (overrides, response.text)

    def test_duplicate_target_rejected(self, harness):
        control = harness.control
        service_id = _create_service(control)
        _create_target(control, service_id)
        response = control.post(
            f"/api/v1/services/{service_id}/db-targets",
            json={
                "engine": "postgres", "host": "127.0.0.1", "port": 5432,
                "database": "crmdb", "allowed_schemas": ["crm"],
            },
            headers=_alice(),
        )
        assert response.status_code == 409

    def test_agent_principals_cannot_manage_targets(self, harness):
        control = harness.control
        service_id = _create_service(control)
        response = control.post(
            f"/api/v1/services/{service_id}/db-targets",
            json={
                "engine": "postgres", "host": "127.0.0.1", "port": 5432,
                "database": "crmdb", "allowed_schemas": ["crm"],
            },
            headers=_agent(),
        )
        assert response.status_code == 403

    def test_creator_self_approval_blocked_outside_demo(self, harness):
        """Acceptance 6: the DB approval is reviewer-grade with the same
        no-self-approval posture as HTTP endpoints (PR-SEC-01)."""
        from ppswitch_control.services.db_target_service import approve_db_target
        from ppswitch_db.models import DatabaseTargetRow

        control = harness.control
        service_id = _create_service(control)
        target = _create_target(control, service_id)
        session = harness.session_factory()
        try:
            row = session.get(DatabaseTargetRow, uuid.UUID(target["id"]))
            assert row is not None
            with pytest.raises(ApiError) as exc:
                approve_db_target(
                    session,
                    workspace_id=row.workspace_id,
                    principal_id=row.created_by,  # the creator themself
                    target_id=row.id,
                    demo_mode=False,
                )
            assert exc.value.code == ErrorCode.DATABASE_TARGET_NOT_APPROVABLE
        finally:
            session.close()

    def test_reviewer_approves_and_disable_closes_the_target(self, harness):
        control = harness.control
        service_id = _create_service(control)
        target = _create_target(control, service_id)
        assert target["status"] == "pending"
        approved = control.post(f"/api/v1/db-targets/{target['id']}/approve", headers=_carol())
        assert approved.status_code == 200, approved.text
        assert approved.json()["data"]["status"] == "approved"
        disabled = control.post(f"/api/v1/db-targets/{target['id']}/disable", headers=_carol())
        assert disabled.status_code == 200
        assert disabled.json()["data"]["status"] == "disabled"
        # double-disable conflicts
        assert control.post(
            f"/api/v1/db-targets/{target['id']}/disable", headers=_carol()
        ).status_code == 409


class TestDbExecutionSpec:
    """Acceptance 2: the execution mapping is STRUCTURED - SQL text,
    extra keys, or filters outside the selected columns are all rejected
    before anything can execute."""

    def _spec(self, **overrides) -> dict:
        spec = {
            "target_id": str(uuid.uuid4()),
            "schema": "crm",
            "view": "customers_v",
            "columns": ["id", "display_name"],
            "filters": {"id": "customer_id"},
            "limit": 2,
        }
        spec.update(overrides)
        return spec

    def test_valid_spec_normalizes(self, harness):
        from ppswitch_control.services.db_target_service import validate_db_execution_spec

        clean = validate_db_execution_spec(self._spec())
        assert clean["columns"] == ["id", "display_name"]
        assert clean["limit"] == 2

    @pytest.mark.parametrize(
        "overrides",
        [
            {"sql": "SELECT * FROM crm.customers"},  # SQL text has no surface
            {"columns": []},
            {"columns": ["id", "id"]},
            {"columns": ["id", "note; --"]},
            {"filters": {"note": "customer_id"}},  # filter on unselected column
            {"limit": 10},
            {"schema": 'crm"; DROP SCHEMA public'},
            {"target_id": "not-a-uuid"},
        ],
    )
    def test_malformed_specs_rejected(self, harness, overrides):
        from ppswitch_control.services.db_target_service import validate_db_execution_spec

        with pytest.raises(ApiError) as exc:
            validate_db_execution_spec(self._spec(**overrides))
        assert exc.value.code == ErrorCode.BAD_REQUEST

    def test_capability_create_rejects_db_spec_with_http_keys(self, harness):
        control = harness.control
        service_id = _create_service(control)
        target = _create_target(control, service_id)
        # import one synthetic db candidate so a capability can reference it
        imported = control.post(
            "/api/v1/discoveries/imports",
            files={"file": ("db.json", __import__("json").dumps({
                "target": {"host": "127.0.0.1", "port": 5432, "database": "crmdb"},
                "objects": [{
                    "schema": "crm", "name": "customers_v", "kind": "view",
                    "columns": [
                        {"name": "id", "data_type": "text"},
                        {"name": "display_name", "data_type": "text"},
                    ],
                }],
            }).encode(), "application/json")},
            data={"source_type": "db", "service_id": service_id},
            headers=_alice(),
        )
        assert imported.status_code == 201, imported.text
        operation_id = imported.json()["data"]["candidates"] and control.get(
            f"/api/v1/operations?service_id={service_id}", headers=_alice()
        ).json()["data"][0]["id"]

        response = control.post(
            "/api/v1/capabilities",
            json={
                "name": f"crmdb.get_customer_{uuid.uuid4().hex[:4]}",
                "display_name": "Get customer",
                "operation_id": operation_id,
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "execution": {"db": self._spec(target_id=target["id"]), "path": {"x": "y"}},
            },
            headers=_alice(),
        )
        assert response.status_code == 400, response.text


class TestResolveDbQueryLifecycle:
    """Acceptance 4 (immediate revocation) + 6 (independent approval):
    the plan-build resolver refuses disabled targets, foreign services,
    out-of-allowlist schemas and non-postgres connections."""

    def _fixture(self, harness):
        control = harness.control
        service_id = _create_service(control)
        target = _create_target(control, service_id)
        approved = control.post(f"/api/v1/db-targets/{target['id']}/approve", headers=_carol())
        assert approved.status_code == 200
        connection_id = _create_postgres_connection(control, service_id)
        return control, service_id, target, connection_id

    def _resolve(self, harness, service_id, target_id, connection_id, status="approved"):
        from ppswitch_control.services.db_target_service import resolve_db_query
        from ppswitch_db.models import ConnectionRow, DatabaseTargetRow

        session = harness.session_factory()
        try:
            target = session.get(DatabaseTargetRow, uuid.UUID(target_id))
            assert target is not None
            target.status = status
            connection = session.get(ConnectionRow, uuid.UUID(connection_id))
            assert connection is not None
            query = resolve_db_query(
                session,
                workspace_id=target.workspace_id,
                spec={
                    "target_id": target_id,
                    "schema": "crm",
                    "view": "customers_v",
                    "columns": ["id", "display_name"],
                    "filters": {"id": "customer_id"},
                    "limit": 2,
                },
                service_id=uuid.UUID(service_id),
                connection=connection,
            )
            return query
        finally:
            session.close()

    def test_approved_target_freezes_the_coordinates(self, harness):
        _control, service_id, target, connection_id = self._fixture(harness)
        query = self._resolve(harness, service_id, target["id"], connection_id)
        assert query.host == "127.0.0.1"
        assert query.database == "crmdb"
        assert query.allowed_schemas == ["crm"]
        assert query.param_map == {"id": "customer_id"}

    def test_disabled_target_rejects_immediately(self, harness):
        """Acceptance 4: revoking the approval must fail the very next plan."""
        _control, service_id, target, connection_id = self._fixture(harness)
        with pytest.raises(ApiError) as exc:
            self._resolve(harness, service_id, target["id"], connection_id, status="disabled")
        assert exc.value.code == ErrorCode.FORBIDDEN

    def test_schema_outside_allowlist_rejected(self, harness):
        from ppswitch_control.services.db_target_service import resolve_db_query
        from ppswitch_db.models import ConnectionRow, DatabaseTargetRow

        _control, service_id, target, connection_id = self._fixture(harness)
        session = harness.session_factory()
        try:
            row = session.get(DatabaseTargetRow, uuid.UUID(target["id"]))
            connection = session.get(ConnectionRow, uuid.UUID(connection_id))
            with pytest.raises(ApiError) as exc:
                resolve_db_query(
                    session,
                    workspace_id=row.workspace_id,
                    spec={
                        "target_id": target["id"],
                        "schema": "hr",  # exists in the DB world, NOT approved
                        "view": "salaries",
                        "columns": ["emp"],
                        "filters": {"emp": "emp"},
                        "limit": 2,
                    },
                    service_id=uuid.UUID(service_id),
                    connection=connection,
                )
            assert exc.value.code == ErrorCode.DATABASE_QUERY_REJECTED
        finally:
            session.close()

    def test_non_postgres_connection_rejected(self, harness):
        from ppswitch_control.services.db_target_service import resolve_db_query
        from ppswitch_db.models import ConnectionRow, DatabaseTargetRow

        _control, service_id, target, connection_id = self._fixture(harness)
        session = harness.session_factory()
        try:
            row = session.get(DatabaseTargetRow, uuid.UUID(target["id"]))
            connection = session.get(ConnectionRow, uuid.UUID(connection_id))
            connection.auth_scheme = "api_key"  # wrong scheme for a db tool
            with pytest.raises(ApiError) as exc:
                resolve_db_query(
                    session,
                    workspace_id=row.workspace_id,
                    spec={
                        "target_id": target["id"], "schema": "crm",
                        "view": "customers_v", "columns": ["id"],
                        "filters": {"id": "customer_id"}, "limit": 2,
                    },
                    service_id=uuid.UUID(service_id),
                    connection=connection,
                )
            assert exc.value.code == ErrorCode.FORBIDDEN
        finally:
            session.close()


class TestDiscoveryPreconditions:
    def test_discovery_requires_an_approved_target(self, harness):
        control = harness.control
        service_id = _create_service(control)
        target = _create_target(control, service_id)
        response = control.post(f"/api/v1/db-targets/{target['id']}/discover", headers=_alice())
        assert response.status_code == 403

    def test_discovery_requires_an_active_postgres_connection(self, harness):
        control = harness.control
        service_id = _create_service(control)
        target = _create_target(control, service_id)
        assert control.post(
            f"/api/v1/db-targets/{target['id']}/approve", headers=_carol()
        ).status_code == 200
        response = control.post(f"/api/v1/db-targets/{target['id']}/discover", headers=_alice())
        assert response.status_code == 404  # no postgres connection at all

    def test_postgres_activation_requires_approved_target(self, harness):
        """Control-side gate before the executor is even asked: a postgres
        connection cannot activate while the service has no approved
        database target (acceptance 6: DB access is separately approved)."""
        control = harness.control
        service_id = _create_service(control)
        connection_id = _create_postgres_connection(control, service_id)
        response = control.post(
            f"/api/v1/connections/{connection_id}/activate", headers=_alice()
        )
        assert response.status_code == 409


class TestEndpointlessProfiles:
    def test_db_profile_binds_connection_without_endpoint(self, harness):
        control = harness.control
        service_id = _create_service(control)
        connection_id = _create_postgres_connection(control, service_id)
        response = control.post(
            "/api/v1/profiles",
            json={
                "service_id": service_id,
                "endpoint_id": None,
                "connection_id": connection_id,
                "name": "RO db profile",
            },
            headers=_alice(),
        )
        assert response.status_code == 201, response.text
        assert response.json()["data"]["name"] == "RO db profile"

    def test_endpointless_profile_requires_postgres_connection(self, harness):
        control = harness.control
        service_id = _create_service(control)
        connection = control.post(
            "/api/v1/connections",
            json={"service_id": service_id, "auth_scheme": "api_key",
                  "demo_dataset": "A"},
            headers=_alice(),
        )
        assert connection.status_code == 201, connection.text
        response = control.post(
            "/api/v1/profiles",
            json={
                "service_id": service_id,
                "endpoint_id": None,
                "connection_id": connection.json()["data"]["id"],
                "name": "not a db profile",
            },
            headers=_alice(),
        )
        assert response.status_code == 400
