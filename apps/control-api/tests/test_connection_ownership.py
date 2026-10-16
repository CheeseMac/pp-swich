"""Review 2026-10-16: connection ownership boundary regression.

Finding (confirmed real): activate/revoke only checked the workspace, and
the connection list exposed every connection in it - so a member could
activate or revoke ANOTHER member's connection. Pinned here:

- a same-workspace non-owner cannot activate or revoke (404, existence
  not revealed);
- non-admins list only their own connections; the admin keeps the
  workspace-wide view;
- the owner's own flow still works.
"""

from __future__ import annotations

import pytest

from .conftest import ALICE_TOKEN, CAROL_TOKEN, DAVE_TOKEN, Harness, auth


@pytest.fixture()
def setup(harness: Harness):
    """Alice (admin) owns a pending keyring connection; Dave is a plain
    member in the SAME workspace."""
    from ppswitch_control.auth import hash_token
    from ppswitch_db.models import PrincipalRow

    with harness.session_factory() as session:
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
            import uuid as uuid_module

            session.add(
                PrincipalRow(
                    id=uuid_module.uuid4(), workspace_id=alice.workspace_id,
                    kind="user", display_name="Dave (member)",
                    token_hash=hash_token(DAVE_TOKEN),
                    token_hint=DAVE_TOKEN[:14] + "...", role="member",
                )
            )
            session.commit()

    control = harness.control
    created = control.post(
        "/api/v1/services",
        json={"slug": "ownship", "name": "Ownership"},
        headers=auth(ALICE_TOKEN),
    )
    service_id = created.json()["data"]["id"]
    return control, service_id


def _make_pending_keyring_connection(control, service_id, token):
    return control.post(
        "/api/v1/connections",
        json={"service_id": service_id, "auth_scheme": "api_key",
              "secret_name": "ownship-key"},
        headers=auth(token),
    ).json()["data"]


def test_non_owner_cannot_activate_or_revoke(setup):
    control, service_id = setup
    alice_conn = _make_pending_keyring_connection(control, service_id, ALICE_TOKEN)

    activate = control.post(
        f"/api/v1/connections/{alice_conn['id']}/activate", headers=auth(DAVE_TOKEN)
    )
    revoke = control.post(
        f"/api/v1/connections/{alice_conn['id']}/revoke", headers=auth(DAVE_TOKEN)
    )
    # same-workspace non-owner: refused AND existence not revealed
    assert activate.status_code == 404
    assert activate.json()["error"]["code"] == "CONNECTION_NOT_FOUND"
    assert revoke.status_code == 404
    assert revoke.json()["error"]["code"] == "CONNECTION_NOT_FOUND"

    # the reviewer principal is equally bound by ownership (both actions)
    carol_activate = control.post(
        f"/api/v1/connections/{alice_conn['id']}/activate", headers=auth(CAROL_TOKEN)
    )
    assert carol_activate.status_code == 404
    carol_revoke = control.post(
        f"/api/v1/connections/{alice_conn['id']}/revoke", headers=auth(CAROL_TOKEN)
    )
    assert carol_revoke.status_code == 404


def test_non_admin_listing_shows_only_own_connections(setup):
    control, service_id = setup
    _make_pending_keyring_connection(control, service_id, ALICE_TOKEN)
    dave_conn = _make_pending_keyring_connection(control, service_id, DAVE_TOKEN)

    dave_view = control.get("/api/v1/connections", headers=auth(DAVE_TOKEN)).json()["data"]
    assert [c["id"] for c in dave_view] == [dave_conn["id"]]

    # admin keeps the workspace-wide operational view
    admin_view = control.get("/api/v1/connections", headers=auth(ALICE_TOKEN)).json()["data"]
    assert len({c["id"] for c in admin_view}) == 2


def test_owner_flow_still_works(setup, harness):
    control, service_id = setup
    conn = _make_pending_keyring_connection(control, service_id, ALICE_TOKEN)

    # activation fails closed here (no secret stored) but the OWNER gets
    # the provider error, not a 404 - proving the ownership gate passes
    activate = control.post(
        f"/api/v1/connections/{conn['id']}/activate", headers=auth(ALICE_TOKEN)
    )
    assert activate.status_code == 503
    assert activate.json()["error"]["code"] == "SECRET_PROVIDER_UNAVAILABLE"

    revoked = control.post(
        f"/api/v1/connections/{conn['id']}/revoke", headers=auth(ALICE_TOKEN)
    )
    assert revoked.status_code == 200
    assert revoked.json()["data"]["status"] == "revoked"
