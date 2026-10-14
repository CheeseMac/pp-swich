"""FEAT-01 collector API tests: pairing, scoped tokens, intake, fail-closed.

The extension's security contract, pinned end-to-end through the public
HTTP surface:

- pairing codes are one-time, short-lived, shown once, hashed at rest;
- the collector token is NOT a principal: it only reaches
  ``POST /collector/observations`` and nothing else;
- submitted batches are re-sanitized server-side; one raw credential
  value rejects the whole batch (nothing partially imported);
- intake produces DRAFT operations only (observed/inferred), never
  verified/published ones.
"""

from __future__ import annotations

from ppswitch_control.services.collector_service import MAX_PAIRING_ATTEMPTS

from .conftest import AGENT_TOKEN, ALICE_TOKEN, BOB_TOKEN, auth


def observation(
    url: str,
    method: str = "GET",
    status: int = 200,
    query: dict | None = None,
    request_body: dict | None = None,
    response_body: dict | None = None,
) -> dict:
    return {
        "url": url,
        "method": method,
        "status": status,
        "query": query or {},
        "request_body": request_body,
        "response_body": response_body,
        "graphql_operation": None,
    }


def _create_pairing(control, service_id: str, token: str = ALICE_TOKEN) -> dict:
    response = control.post(
        f"/api/v1/services/{service_id}/collector/pairings", headers=auth(token)
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _pair(control, code: str):
    return control.post("/api/v1/collector/pair", json={"pairing_code": code})


def _submit(control, token: str, observations: list[dict]):
    return control.post(
        "/api/v1/collector/observations",
        json={"observations": observations},
        headers={"Authorization": f"Bearer {token}"},
    )


def test_pairing_flow_creates_draft_operations(demo):
    control = demo.harness.control
    service_id = demo.ids["service_id"]
    har_operations = {
        (o["method"], o["normalized_path"])
        for o in control.get(
            f"/api/v1/operations?service_id={service_id}", headers=auth(ALICE_TOKEN)
        ).json()["data"]
    }

    pairing = _create_pairing(control, service_id)
    assert pairing["status"] == "pending"
    code = pairing["pairing_code"]
    assert code  # shown once

    # listing shows the pairing but never the code again
    listing = control.get(
        f"/api/v1/services/{service_id}/collector/pairings", headers=auth(ALICE_TOKEN)
    ).json()["data"]
    assert listing[0]["status"] == "pending"
    assert "pairing_code" not in listing[0]

    paired = _pair(control, code)
    assert paired.status_code == 200, paired.text
    collector_token = paired.json()["data"]["collector_token"]
    assert collector_token.startswith("ppc-")

    response = _submit(
        control,
        collector_token,
        [
            observation(
                f"https://crm.example.test/api/customers/C-{n}",
                response_body={"id": "", "display_name": ""},
            )
            for n in (7001, 7002)
        ]
        + [
            observation(
                "https://crm.example.test/api/orders/search",
                method="POST",
                request_body={"q": "", "limit": 0},
                response_body={"orders": [{"id": "", "total": 0}]},
            )
        ],
    )
    assert response.status_code == 201, response.text
    summary = response.json()["data"]
    assert summary["candidates"] == 2

    operations = control.get(
        f"/api/v1/operations?service_id={service_id}", headers=auth(ALICE_TOKEN)
    ).json()["data"]
    by_template = {(o["method"], o["normalized_path"]): o for o in operations}
    # the collector GET merged into the existing HAR-imported operation
    assert ("GET", "/api/customers/{id}") in har_operations
    get_op = by_template[("GET", "/api/customers/{id}")]
    assert ("POST", "/api/orders/search") in by_template
    # drafts only: never auto-verified
    assert get_op["status"] in ("observed", "inferred", "needs_review")
    assert get_op["status"] != "verified"
    assert by_template[("POST", "/api/orders/search")]["status"] in (
        "observed",
        "inferred",
        "needs_review",
    )

    # observation source breakdown is visible to the console
    observations_view = control.get(
        f"/api/v1/observations?service_id={service_id}", headers=auth(ALICE_TOKEN)
    ).json()["data"]
    sources = {o["source"] for o in observations_view}
    assert "collector" in sources
    assert {"har", "collector"} <= sources

    # pairing statistics updated
    listing = control.get(
        f"/api/v1/services/{service_id}/collector/pairings", headers=auth(ALICE_TOKEN)
    ).json()["data"]
    assert listing[0]["status"] == "paired"
    assert listing[0]["observation_count"] == 3


def test_pairing_code_is_single_use(demo):
    control = demo.harness.control
    service_id = demo.ids["service_id"]
    pairing = _create_pairing(control, service_id)
    code = pairing["pairing_code"]

    assert _pair(control, code).status_code == 200
    replay = _pair(control, code)
    assert replay.status_code == 401


def test_replay_cap_revokes_pairing_and_token(demo):
    control = demo.harness.control
    service_id = demo.ids["service_id"]
    pairing = _create_pairing(control, service_id)
    code = pairing["pairing_code"]

    token = _pair(control, code).json()["data"]["collector_token"]
    for _ in range(MAX_PAIRING_ATTEMPTS):
        assert _pair(control, code).status_code == 401

    listing = control.get(
        f"/api/v1/services/{service_id}/collector/pairings", headers=auth(ALICE_TOKEN)
    ).json()["data"]
    assert listing[0]["status"] == "revoked"

    # the revoked pairing also killed its issued token
    response = _submit(control, token, [observation("https://x.test/api/a")])
    assert response.status_code == 401


def test_collector_token_is_not_a_principal(demo):
    """The extension token must grant NOTHING beyond observation intake."""
    control = demo.harness.control
    service_id = demo.ids["service_id"]
    pairing = _create_pairing(control, service_id)
    token = _pair(control, pairing["pairing_code"]).json()["data"]["collector_token"]
    headers = {"Authorization": f"Bearer {token}"}

    for path in (
        "/api/v1/services",
        "/api/v1/operations",
        "/api/v1/capabilities",
        "/api/v1/audit-events",
    ):
        response = control.get(path, headers=headers)
        assert response.status_code == 401, (path, response.status_code)

    # and it cannot mint another pairing or approve anything
    response = control.post(
        f"/api/v1/services/{service_id}/collector/pairings", headers=headers
    )
    assert response.status_code == 401


def test_unknown_pairing_code_rejected(demo):
    control = demo.harness.control
    assert _pair(control, "ZZZZZZZZ").status_code == 401
    assert _pair(control, "").status_code != 200  # validation: 422


def test_agent_cannot_create_pairing(demo):
    control = demo.harness.control
    service_id = demo.ids["service_id"]
    response = control.post(
        f"/api/v1/services/{service_id}/collector/pairings", headers=auth(AGENT_TOKEN)
    )
    assert response.status_code == 403


def test_cross_workspace_service_rejected(demo):
    control = demo.harness.control
    service_id = demo.ids["service_id"]
    response = control.post(
        f"/api/v1/services/{service_id}/collector/pairings", headers=auth(BOB_TOKEN)
    )
    assert response.status_code == 404


def test_unsanitized_batch_rejects_whole(demo):
    control = demo.harness.control
    service_id = demo.ids["service_id"]
    token = _pair(
        control, _create_pairing(control, service_id)["pairing_code"]
    ).json()["data"]["collector_token"]

    before = {
        (o["method"], o["normalized_path"]): o["status"]
        for o in control.get(
            f"/api/v1/operations?service_id={service_id}", headers=auth(ALICE_TOKEN)
        ).json()["data"]
    }

    response = _submit(
        control,
        token,
        [
            observation("https://crm.example.test/api/clean", response_body={"ok": ""}),
            # buggy-extension simulation: a real-looking key escapes the
            # local skeletonizer
            observation(
                "https://crm.example.test/api/dirty",
                request_body={"api_key": "sk-AZn8KqLmT4vXyP0aRs3Wu"},
            ),
        ],
    )
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "UNSANITIZED_INPUT"

    after = {
        (o["method"], o["normalized_path"]): o["status"]
        for o in control.get(
            f"/api/v1/operations?service_id={service_id}", headers=auth(ALICE_TOKEN)
        ).json()["data"]
    }
    assert after == before  # nothing imported, not even the clean entry


def test_batch_size_cap(demo):
    control = demo.harness.control
    service_id = demo.ids["service_id"]
    token = _pair(
        control, _create_pairing(control, service_id)["pairing_code"]
    ).json()["data"]["collector_token"]

    batch = [
        observation(f"https://crm.example.test/api/x/{i}") for i in range(201)
    ]
    response = _submit(control, token, batch)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


def test_expired_collector_token_rejected(demo, harness):
    from datetime import UTC, datetime, timedelta

    from ppswitch_db.models import CollectorPairingRow

    control = harness.control
    service_id = demo.ids["service_id"]
    token = _pair(
        control, _create_pairing(control, service_id)["pairing_code"]
    ).json()["data"]["collector_token"]

    with harness.session_factory() as session:
        row = (
            session.query(CollectorPairingRow)
            .filter_by(collector_token_hint=token[:10] + "...")
            .one()
        )
        row.token_expires_at = datetime.now(UTC) - timedelta(minutes=1)
        session.commit()

    response = _submit(control, token, [observation("https://x.test/api/a")])
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_submit_requires_collector_token(demo):
    control = demo.harness.control
    # a user management token is NOT valid for the collector surface and
    # vice versa - no token at all is rejected too
    response = control.post(
        "/api/v1/collector/observations",
        json={"observations": [observation("https://x.test/api/a")]},
    )
    assert response.status_code == 401
    response = control.post(
        "/api/v1/collector/observations",
        json={"observations": [observation("https://x.test/api/a")]},
        headers=auth(ALICE_TOKEN),
    )
    assert response.status_code == 401
