"""FEAT-02 acceptance: the full no-code wizard journey over the real API.

Collector observation (FEAT-01) -> configure + draft capability -> live
test call -> review + verify + publish by a SECOND principal -> grant to
an agent -> the agent's public tool listing shows it -> a direct user
invocation succeeds through the trusted executor path.

This is the product promise end to end: a discovered HTTP API becomes a
testable, reviewable, authorizable, agent-callable tool without writing
code or OpenAPI YAML.
"""

from __future__ import annotations

import json

from .conftest import AGENT_TOKEN, ALICE_TOKEN, CAROL_TOKEN, auth


def _collector_import(control, service_id: str, token: str = ALICE_TOKEN) -> str:
    observation = {
        "url": "https://crm.example.test/api/insights/summary",
        "method": "POST",
        "status": 200,
        "query": {"depth": ""},
        "request_body": {"period": "", "include churn": ""},
        "response_body": {"summary": "", "rows": 0},
        "graphql_operation": None,
    }
    imported = control.post(
        "/api/v1/discoveries/imports",
        files={"file": ("o.json", json.dumps({"observations": [observation]}).encode(),
                        "application/json")},
        data={"source_type": "collector", "service_id": service_id},
        headers=auth(token),
    )
    assert imported.status_code == 201, imported.text
    ops = control.get(
        f"/api/v1/operations?service_id={service_id}", headers=auth(token)
    ).json()["data"]
    return next(o["id"] for o in ops if o["normalized_path"] == "/api/insights/summary")


def test_full_wizard_journey_to_agent_tool(demo):
    control = demo.harness.control
    service_id = demo.ids["service_id"]

    # 1) discovery evidence from the collector
    operation_id = _collector_import(control, service_id)

    # 2) configure + create the draft capability (wizard step "配置工具")
    #    identity execution maps: every exposed parameter flows to the
    #    same-named request position
    created = control.post(
        "/api/v1/capabilities",
        json={
            "name": "crm.post_api_insights_summary",
            "display_name": "Insights summary",
            "description": "Discovered from traffic by the browser collector",
            "operation_id": operation_id,
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "depth": {"type": "string"},
                    "period": {"type": "string"},
                },
                "required": [],
            },
            "output_schema": {"type": "object", "properties": {"summary": {"type": "string"}}},
            "allow_fields": ["summary"],
            "risk_level": "low",
            "execution": {"path": {}, "query": {"depth": "depth"}, "body": {"period": "period"}},
        },
        headers=auth(ALICE_TOKEN),
    )
    assert created.status_code == 201, created.text
    capability_id = created.json()["data"]["id"]

    # 3) live test call through the active profile (wizard step "试调")
    tested = control.post(
        f"/api/v1/operations/{operation_id}/test",
        json={"arguments": {"depth": "full", "period": "30d"}},
        headers=auth(ALICE_TOKEN),
    )
    assert tested.status_code == 200, tested.text
    assert tested.json()["data"]["ok"] is True

    # 4) review -> verify -> publish by the SECOND principal (Carol)
    for action in ("review", "verify"):
        step = control.post(
            f"/api/v1/capabilities/{capability_id}/{action}",
            json={"evidence": "wizard flow check"},
            headers=auth(CAROL_TOKEN),
        )
        assert step.status_code == 200, step.text
    published = control.post(
        f"/api/v1/capabilities/{capability_id}/publish",
        json={"version": "1.0.0"},
        headers=auth(CAROL_TOKEN),
    )
    assert published.status_code == 200, published.text

    # the creator cannot self-review (guards the wizard's hint)
    other = control.post(
        "/api/v1/capabilities",
        json={
            "name": "crm.post_api_insights_self",
            "display_name": "x",
            "operation_id": operation_id,
            "input_schema": {},
            "output_schema": {},
            "allow_fields": [],
            "risk_level": "low",
        },
        headers=auth(ALICE_TOKEN),
    )
    assert other.status_code == 201
    self_review = control.post(
        f"/api/v1/capabilities/{other.json()['data']['id']}/review",
        json={"evidence": "self"},
        headers=auth(ALICE_TOKEN),
    )
    assert self_review.status_code == 403

    # 5) grant to the agent (admin action)
    listing = control.get("/api/v1/dev/principals").json()["data"]
    agent_id = next(p["id"] for p in listing if p["kind"] == "agent")
    granted = control.post(
        "/api/v1/agent-grants",
        json={"agent_principal_id": agent_id, "capability_id": capability_id, "scopes": []},
        headers=auth(ALICE_TOKEN),
    )
    assert granted.status_code == 201, granted.text

    # 6) the agent's public tool listing now contains the new tool
    agent_view = control.get("/api/v1/capabilities", headers=auth(AGENT_TOKEN)).json()["data"]
    names = {c["name"] for c in agent_view}
    assert "crm.post_api_insights_summary" in names
    # agents never see draft metadata
    published_tool = next(c for c in agent_view if c["name"] == "crm.post_api_insights_summary")
    assert all(v["status"] == "published" for v in published_tool["versions"])

    # 7) and a direct human invocation succeeds through the executor
    invoked = control.post(
        "/api/v1/invocations",
        json={
            "capability": "crm.post_api_insights_summary",
            "context_key": "personal",
            "arguments": {"depth": "full", "period": "30d"},
        },
        headers=auth(ALICE_TOKEN),
    )
    assert invoked.status_code == 200, invoked.text
    body = invoked.json()["data"]
    assert body["status"] == "succeeded", body
