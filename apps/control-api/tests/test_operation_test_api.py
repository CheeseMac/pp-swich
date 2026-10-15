"""FEAT-02 wizard test-endpoint tests.

`POST /operations/{id}/test` runs a discovered operation once through the
caller's ACTIVE profile and the trusted executor path. Pinned here:

- happy path returns the guarded result from the mock CRM;
- arguments are whitelisted to the observed contract (unknown names,
  non-scalars and missing path parameters are rejected);
- no active profile -> 409, agent principals -> 403 (permission matrix);
- every attempt lands in the audit trail.
"""

from __future__ import annotations

from .conftest import AGENT_TOKEN, ALICE_TOKEN, auth


def _post_test(control, operation_id: str, arguments: dict, token: str = ALICE_TOKEN):
    return control.post(
        f"/api/v1/operations/{operation_id}/test",
        json={"arguments": arguments},
        headers=auth(token),
    )


def test_live_operation_test_returns_guarded_result(demo):
    control = demo.harness.control
    operation_id = demo.ids["get_operation_id"]

    response = _post_test(control, operation_id, {"id": "C-1024"})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["ok"] is True
    assert data["executor_status"] == 200
    # in-process demo harness: the MockAdapter answers. The important
    # assertion is the GUARD: the observed fields survive projection and
    # the adapter's deliberately leaky canaries never reach the console
    result = data["result"]
    assert result["display_name"] == "Mock Adapter Customer"
    assert "access_token" not in result
    assert "internal_note" not in result


def test_unknown_argument_names_rejected(demo):
    control = demo.harness.control
    operation_id = demo.ids["get_operation_id"]

    response = _post_test(control, operation_id, {"id": "C-1024", "hack": "x"})
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "SCHEMA_VIOLATION"
    assert "hack" in body["details"]["unknown_arguments"]


def test_non_scalar_arguments_rejected(demo):
    control = demo.harness.control
    operation_id = demo.ids["get_operation_id"]

    response = _post_test(control, operation_id, {"id": {"nested": "object"}})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "SCHEMA_VIOLATION"


def test_missing_path_parameter_rejected(demo):
    control = demo.harness.control
    operation_id = demo.ids["get_operation_id"]

    response = _post_test(control, operation_id, {})
    assert response.status_code == 422
    assert "id" in response.json()["error"]["details"]["missing"]


def test_requires_active_profile(demo, harness):
    """A fresh service has no profile: the wizard surfaces a clear 409."""
    control = harness.control
    created = control.post(
        "/api/v1/services",
        json={"slug": "no-profile", "name": "No profile"},
        headers=auth(ALICE_TOKEN),
    )
    assert created.status_code == 201
    service_id = created.json()["data"]["id"]

    observation = {
        "url": "https://orphan.example.test/api/ping",
        "method": "GET",
        "status": 200,
        "query": {},
        "request_body": None,
        "response_body": {"pong": ""},
        "graphql_operation": None,
    }
    imported = control.post(
        "/api/v1/discoveries/imports",
        files={"file": ("o.json", __import__("json").dumps(
            {"observations": [observation]}).encode(), "application/json")},
        data={"source_type": "collector", "service_id": service_id},
        headers=auth(ALICE_TOKEN),
    )
    assert imported.status_code == 201, imported.text
    ops = control.get(
        f"/api/v1/operations?service_id={service_id}", headers=auth(ALICE_TOKEN)
    ).json()["data"]
    response = _post_test(control, ops[0]["id"], {})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "NO_ACTIVE_PROFILE"


def test_agents_cannot_use_the_test_endpoint(demo):
    control = demo.harness.control
    operation_id = demo.ids["get_operation_id"]
    response = _post_test(control, operation_id, {"id": "C-1024"}, token=AGENT_TOKEN)
    assert response.status_code == 403


def test_attempts_are_audited(demo):
    control = demo.harness.control
    operation_id = demo.ids["get_operation_id"]
    _post_test(control, operation_id, {"id": "C-1024"})
    _post_test(control, operation_id, {"bogus": "x"})

    events = control.get(
        "/api/v1/audit-events", headers=auth(ALICE_TOKEN)
    ).json()["data"]
    tests = [e for e in events if e["action"] == "operation.test"]
    assert len(tests) >= 2
    decisions = {e["decision"] for e in tests}
    assert decisions == {"allowed", "denied"}
