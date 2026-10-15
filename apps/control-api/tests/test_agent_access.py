"""FEAT-05 agent onboarding tests: one-click access prerequisites.

- admin creates an agent identity and receives a ONE-TIME token;
- members/reviewers/agents cannot mint agent identities;
- the token authenticates as that agent principal (the exact auth path
  the MCP adapter and the python SDK use);
- listing shows the directory but never tokens;
- grant -> the new agent's capability listing shows the tool.
"""

from __future__ import annotations

from .conftest import AGENT_TOKEN, ALICE_TOKEN, BOB_TOKEN, CAROL_TOKEN, auth


def test_admin_creates_agent_and_token_works_once(demo):
    control = demo.harness.control

    created = control.post(
        "/api/v1/agent-principals",
        json={"display_name": "claude-desktop"},
        headers=auth(ALICE_TOKEN),
    )
    assert created.status_code == 201, created.text
    data = created.json()["data"]
    assert data["display_name"] == "claude-desktop"
    assert data["token"].startswith("dev-token-agent-")

    # the token authenticates as an agent principal (empty grants -> no tools)
    listing = control.get(
        "/api/v1/capabilities", headers={"Authorization": f"Bearer {data['token']}"}
    )
    assert listing.status_code == 200
    assert listing.json()["data"] == []

    # directory: present, but the token is never listed again
    directory = control.get("/api/v1/agent-principals", headers=auth(ALICE_TOKEN)).json()["data"]
    entry = next(a for a in directory if a["display_name"] == "claude-desktop")
    assert entry["token_hint"].startswith("dev-token-agent-")
    assert "token" not in entry


def test_only_admins_can_create_agent_principals(demo):
    control = demo.harness.control
    for token, who in (
        (CAROL_TOKEN, "reviewer"),
        (BOB_TOKEN, "member-other-workspace"),
        (AGENT_TOKEN, "agent"),
    ):
        response = control.post(
            "/api/v1/agent-principals",
            json={"display_name": "nope"},
            headers=auth(token),
        )
        assert response.status_code in (403, 404), (who, response.status_code)


def test_grant_then_agent_sees_only_the_granted_tool(demo):
    """The full one-click access chain, over the same auth path as MCP."""
    control = demo.harness.control
    capability_id = demo.ids["capability_id"]
    capability = control.get(
        f"/api/v1/capabilities/{capability_id}", headers=auth(ALICE_TOKEN)
    ).json()["data"]

    created = control.post(
        "/api/v1/agent-principals",
        json={"display_name": "cursor-local"},
        headers=auth(ALICE_TOKEN),
    ).json()["data"]
    granted = control.post(
        "/api/v1/agent-grants",
        json={
            "agent_principal_id": created["id"],
            "capability_id": capability_id,
            "scopes": [],
        },
        headers=auth(ALICE_TOKEN),
    )
    assert granted.status_code == 201, granted.text

    listing = control.get(
        "/api/v1/capabilities", headers={"Authorization": f"Bearer {created['token']}"}
    ).json()["data"]
    names = [c["name"] for c in listing]
    assert names == [capability["name"]]
