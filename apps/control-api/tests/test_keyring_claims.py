"""SECRET-06 control-plane tests (docs/41 A1..A8, T1, T2, T4, T6, T7).

In-process topology: the shared harness provides the DATABASE (with
Alice/Carol/Bob principals); each test family builds its own control +
executor app pair on that database - the executor uses the default
DispatchingSecretProvider so keyring:// refs resolve through the patched
in-process fake OS keyring. The pre-execution freshness reverse query
needs a real server pair and is covered by
tests/e2e/test_keyring_isolation.py instead.
"""

from __future__ import annotations

import json
import threading
import uuid

import pytest
from ppswitch_core.errors import ApiError
from sqlalchemy import select

from .conftest import AGENT_TOKEN, ALICE_TOKEN, BOB_TOKEN, CAROL_TOKEN

pytestmark = pytest.mark.usefixtures("harness")


def _alice() -> dict:
    return {"Authorization": f"Bearer {ALICE_TOKEN}"}


def _carol() -> dict:
    return {"Authorization": f"Bearer {CAROL_TOKEN}"}


def _bob() -> dict:
    return {"Authorization": f"Bearer {BOB_TOKEN}"}


def _agent() -> dict:
    return {"Authorization": f"Bearer {AGENT_TOKEN}"}


class _FakeKeyringModule:
    class errors:
        class KeyringError(Exception):
            pass

        class PasswordDeleteError(KeyringError):
            pass

    def __init__(self) -> None:
        self.store: dict = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        self.store.pop((service, username), None)

    def put(self, name, payload):
        self.store[(f"ppswitch/{name}", "pp-switch-executor")] = json.dumps(payload)


@pytest.fixture()
def fake_keyring(monkeypatch):
    backend = _FakeKeyringModule()
    import ppswitch_secrets.keyring as kr_module

    monkeypatch.setattr(kr_module, "_keyring_module", lambda: backend)
    return backend


def _build_stack(harness, tmp_path, *, shared: bool):
    from fastapi.testclient import TestClient
    from ppswitch_control.app import create_app as create_control_app
    from ppswitch_control.settings import ControlSettings
    from ppswitch_executor.main import ExecutorSettings
    from ppswitch_executor.main import create_app as create_executor_app

    if shared:
        executor_settings = ExecutorSettings(
            demo_mode=True,
            shared_keyring=True,
            pin_key="ci-pin-key-synthetic-06",
            attest_key="ci-attest-key-synthetic-06",
            local_attest_dir=str(tmp_path / "attest"),
            control_url="http://127.0.0.1:9",  # execute-path only; unused here
        )
        control_settings = ControlSettings(
            db_url=harness.db_url, executor_url="http://executor.test.internal",
            auto_migrate=False, demo_mode=True,
            shared_keyring=True, namespace_id="ci-machine",
        )
    else:
        executor_settings = ExecutorSettings(demo_mode=True)
        control_settings = ControlSettings(
            db_url=harness.db_url, executor_url="http://executor.test.internal",
            auto_migrate=False, demo_mode=True,
        )
    executor = TestClient(create_executor_app(executor_settings))
    control = TestClient(create_control_app(
        control_settings, executor_client=executor, run_migrations=False
    ))
    stack = {"control": control, "executor": executor}
    if shared:
        stack["attest_dir"] = tmp_path / "attest"
    return stack


@pytest.fixture()
def personal(harness, fake_keyring, tmp_path):
    return _build_stack(harness, tmp_path, shared=False)


@pytest.fixture()
def shared_stack(harness, fake_keyring, tmp_path):
    return _build_stack(harness, tmp_path, shared=True)


def _service(stack) -> str:
    created = stack["control"].post(
        "/api/v1/services",
        json={"slug": f"svc-{uuid.uuid4().hex[:6]}", "name": "svc"},
        headers=_alice(),
    )
    assert created.status_code == 201, created.text
    return created.json()["data"]["id"]


def _conn(stack, service_id: str, name: str, token: dict | None = None) -> str:
    created = stack["control"].post(
        "/api/v1/connections",
        json={"service_id": service_id, "auth_scheme": "api_key",
              "secret_name": name},
        headers=token or _alice(),
    )
    assert created.status_code == 201, created.text
    return created.json()["data"]["id"]


def _activate(stack, connection_id: str, token: dict | None = None, **body):
    return stack["control"].post(
        f"/api/v1/connections/{connection_id}/activate",
        json=body or None,
        headers=token or _alice(),
    )


class TestPersonalModeClaims:
    """A1/A2 + T1: personal mode keeps the FEAT-03 first-activation
    experience; ownership blocks every other principal."""

    def test_first_activation_claims_and_others_are_refused(self, personal, fake_keyring):
        fake_keyring.put("team-key", {"auth_scheme": "api_key",
                                      "material": {"api_key": "MOCKKEY-team-a"}})
        service_id = _service(personal)
        alice_conn = _conn(personal, service_id, "team-key")
        assert _activate(personal, alice_conn).status_code == 200

        # same-workspace borrower (Carol): 403 without owner disclosure
        carol_create = personal["control"].post(
            "/api/v1/connections",
            json={"service_id": service_id, "auth_scheme": "api_key",
                  "secret_name": "team-key"},
            headers=_carol(),
        )
        assert carol_create.status_code == 403
        assert carol_create.json()["error"]["code"] == "KEYRING_NAME_CLAIMED"
        assert "Alice" not in carol_create.text

        # cross-workspace member (Bob): machine-global claim blocks him too
        bob_create = personal["control"].post(
            "/api/v1/connections",
            json={"service_id": service_id, "auth_scheme": "api_key",
                  "secret_name": "team-key"},
            headers=_bob(),
        )
        assert bob_create.status_code in (403, 404)

    def test_owner_re_references_and_lists(self, personal, fake_keyring):
        fake_keyring.put("own-key", {"auth_scheme": "api_key",
                                     "material": {"api_key": "MOCKKEY-own"}})
        service_id = _service(personal)
        first = _conn(personal, service_id, "own-key")
        assert _activate(personal, first).status_code == 200
        second = _conn(personal, service_id, "own-key")
        assert _activate(personal, second).status_code == 200
        claims = personal["control"].get("/api/v1/keyring-claims", headers=_alice())
        assert claims.status_code == 200
        assert "own-key" in {row["name"] for row in claims.json()["data"]}

    def test_agents_cannot_read_claims(self, personal):
        response = personal["control"].get("/api/v1/keyring-claims", headers=_agent())
        assert response.status_code == 403


class TestRotationStateMachine:
    """A3 (control half) + T6: plain re-activation never re-pins changed
    material; the explicit confirm CAS-pins pin_version forward."""

    def _activated(self, stack, fake_keyring, name="rot-key"):
        fake_keyring.put(name, {"auth_scheme": "api_key",
                                "material": {"api_key": "MOCKKEY-rot-v1"}})
        service_id = _service(stack)
        connection_id = _conn(stack, service_id, name)
        assert _activate(stack, connection_id).status_code == 200
        return service_id, connection_id

    def test_material_change_requires_explicit_confirmation(self, personal, fake_keyring):
        _service_id, connection_id = self._activated(personal, fake_keyring)
        fake_keyring.put("rot-key", {"auth_scheme": "api_key",
                                     "material": {"api_key": "MOCKKEY-rot-v2"}})
        refused = _activate(personal, connection_id)
        assert refused.status_code == 409, refused.text
        assert refused.json()["error"]["code"] == "SECRET_ROTATION_CONFIRM_REQUIRED"
        confirmed = personal["control"].post(
            f"/api/v1/connections/{connection_id}/confirm-rotation", headers=_alice()
        )
        assert confirmed.status_code == 200, confirmed.text
        claims = personal["control"].get("/api/v1/keyring-claims", headers=_alice())
        row = next(c for c in claims.json()["data"] if c["name"] == "rot-key")
        assert row["status"] == "active" and row["pin_version"] == 2

    def test_confirm_without_pending_rotation_conflicts(self, personal, fake_keyring):
        _service_id, connection_id = self._activated(personal, fake_keyring)
        response = personal["control"].post(
            f"/api/v1/connections/{connection_id}/confirm-rotation", headers=_alice()
        )
        assert response.status_code == 409


class TestReleaseAndReclaim:
    """A5/A6 + errata 2: released rows re-claim through the conditional
    UPDATE on the old generation; foreign-workspace admins cannot release."""

    def test_release_invalidates_and_reclaim_restores(self, harness, personal, fake_keyring):
        fake_keyring.put("rel-key", {"auth_scheme": "api_key",
                                     "material": {"api_key": "MOCKKEY-rel"}})
        service_id = _service(personal)
        connection_id = _conn(personal, service_id, "rel-key")
        assert _activate(personal, connection_id).status_code == 200

        released = personal["control"].post(
            "/api/v1/keyring-claims/rel-key/release", headers=_alice()
        )
        assert released.status_code == 200, released.text
        body = released.json()["data"]
        assert body["status"] == "released" and body["generation"] == 2

        from ppswitch_db.models import ConnectionRow, KeyringClaimRow

        session = harness.session_factory()
        try:
            row = session.get(ConnectionRow, uuid.UUID(connection_id))
            assert row.pin_state == "unpinned" and row.secret_material_tag is None
        finally:
            session.close()

        # personal mode: the owner re-claims via plain re-activation on the
        # released row (conditional UPDATE path - never a second INSERT)
        again = _conn(personal, service_id, "rel-key")
        assert _activate(personal, again).status_code == 200
        session = harness.session_factory()
        try:
            claim = session.scalars(
                select(KeyringClaimRow).where(KeyringClaimRow.name == "rel-key")
            ).one()
            assert claim.status == "active"
            assert claim.generation == 3  # 1 -> released(2) -> reclaimed(3)
            rows = session.scalars(
                select(KeyringClaimRow).where(KeyringClaimRow.name == "rel-key")
            ).all()
            assert len(rows) == 1  # still ONE row: no INSERT resurrection
        finally:
            session.close()

    def test_foreign_workspace_admin_cannot_release(self, harness, personal, fake_keyring):
        fake_keyring.put("x-key", {"auth_scheme": "api_key",
                                   "material": {"api_key": "MOCKKEY-x"}})
        service_id = _service(personal)
        connection_id = _conn(personal, service_id, "x-key")
        assert _activate(personal, connection_id).status_code == 200

        from ppswitch_db.models import PrincipalRow

        session = harness.session_factory()
        try:
            bob = session.scalars(select(PrincipalRow).where(
                PrincipalRow.display_name == "Bob (other org)")).one()
            bob.role = "admin"  # admin of HIS workspace, not the claim's
            session.commit()
        finally:
            session.close()
        foreign = personal["control"].post("/api/v1/keyring-claims/x-key/release",
                                           headers=_bob())
        assert foreign.status_code == 403
        own = personal["control"].post("/api/v1/keyring-claims/x-key/release",
                                       headers=_alice())
        assert own.status_code == 200


class TestSharedModeProofs:
    """T1/T2 control halves: no proof -> refused; full pairing +
    executor-local attestation works; code and attestation are one-shot."""

    def _pair(self, shared, name: str) -> dict:
        created = shared["control"].post(
            "/api/v1/keyring-claims/pairings", json={"name": name}, headers=_alice()
        )
        assert created.status_code == 201, created.text
        return created.json()["data"]

    def _attest(self, shared, name: str, pairing: dict, code: str) -> str:
        nonce = (shared["attest_dir"] / "local.nonce").read_text(encoding="utf-8").strip()
        response = shared["executor"].post(
            "/internal/v1/keyring/attest",
            json={"name": name, "pairing_code": code,
                  "ticket": pairing["ticket"], "local_nonce": nonce},
            headers={"X-Internal-Token": "dev-internal-token-synthetic"},
        )
        assert response.status_code == 200, response.text
        return response.json()["attestation"]

    def _shared_conn(self, shared, name: str):
        service_id = _service(shared)
        return service_id, _conn(shared, service_id, name)

    def test_first_claim_requires_proof_and_succeeds_with_it(self, shared_stack, fake_keyring):
        fake_keyring.put("shared-key", {"auth_scheme": "api_key",
                                        "material": {"api_key": "MOCKKEY-shared"}})
        _service_id, connection_id = self._shared_conn(shared_stack, "shared-key")
        no_proof = shared_stack["control"].post(
            f"/api/v1/connections/{connection_id}/activate", headers=_alice()
        )
        assert no_proof.status_code == 403, no_proof.text
        assert no_proof.json()["error"]["code"] == "KEYRING_CLAIM_PROOF_INVALID"

        pairing = self._pair(shared_stack, "shared-key")
        attestation = self._attest(shared_stack, "shared-key", pairing,
                                   pairing["pairing_code"])
        activated = shared_stack["control"].post(
            f"/api/v1/connections/{connection_id}/activate",
            json={"pairing_code": pairing["pairing_code"],
                  "attestation": attestation},
            headers=_alice(),
        )
        assert activated.status_code == 200, activated.text

        # single-use (T2): after a release, re-claiming with the SAME
        # consumed code + attestation is refused - a fresh pairing proof
        # is mandatory (a same-owner second connection needs no proof at
        # all, which is a different, legitimate path)
        released = shared_stack["control"].post(
            "/api/v1/keyring-claims/shared-key/release", headers=_alice()
        )
        assert released.status_code == 200, released.text
        _replay_service, replay_conn = self._shared_conn(shared_stack, "shared-key")
        replay = shared_stack["control"].post(
            f"/api/v1/connections/{replay_conn}/activate",
            json={"pairing_code": pairing["pairing_code"],
                  "attestation": attestation},
            headers=_alice(),
        )
        assert replay.status_code == 403
        # and a FRESH pairing round works again (proof renewal path)
        pairing2 = self._pair(shared_stack, "shared-key")
        attestation2 = self._attest(shared_stack, "shared-key", pairing2,
                                    pairing2["pairing_code"])
        reactivated = shared_stack["control"].post(
            f"/api/v1/connections/{replay_conn}/activate",
            json={"pairing_code": pairing2["pairing_code"],
                  "attestation": attestation2},
            headers=_alice(),
        )
        assert reactivated.status_code == 200, reactivated.text

    def test_wrong_code_or_nonce_never_attests_or_redeems(self, shared_stack, fake_keyring):
        fake_keyring.put("shared-2", {"auth_scheme": "api_key",
                                      "material": {"api_key": "MOCKKEY-s2"}})
        pairing = self._pair(shared_stack, "shared-2")
        bad_nonce = shared_stack["executor"].post(
            "/internal/v1/keyring/attest",
            json={"name": "shared-2", "pairing_code": pairing["pairing_code"],
                  "ticket": pairing["ticket"], "local_nonce": "0" * 64},
            headers={"X-Internal-Token": "dev-internal-token-synthetic"},
        )
        assert bad_nonce.status_code == 403  # no local file access, no proof
        # attestation over the WRONG code: redeeming with the real code fails
        wrong_att = self._attest(shared_stack, "shared-2", pairing, "wrong-code-1")
        _service_id, connection_id = self._shared_conn(shared_stack, "shared-2")
        activated = shared_stack["control"].post(
            f"/api/v1/connections/{connection_id}/activate",
            json={"pairing_code": pairing["pairing_code"],
                  "attestation": wrong_att},
            headers=_alice(),
        )
        assert activated.status_code == 403

    def test_personal_executor_never_attests(self, personal):
        response = personal["executor"].post(
            "/internal/v1/keyring/attest",
            json={"name": "x", "pairing_code": "claim-1234",
                  "ticket": "t" * 40, "local_nonce": "n" * 64},
            headers={"X-Internal-Token": "dev-internal-token-synthetic"},
        )
        assert response.status_code == 404


class TestFullMacNotTruncated:
    """T4: security decisions use the FULL HMAC - colliding truncated
    display fingerprints do not fool the tag; tickets enforce purpose and
    expiry."""

    def test_truncated_collision_does_not_fool_the_tag(self):
        import hashlib as _hashlib

        from ppswitch_executor.claim_security import material_tag, tags_match

        seen: dict[str, str] = {}
        a = b = None
        for index in range(400_000):
            candidate = f"MOCKKEY-collision-{index}"
            prefix = _hashlib.sha256(candidate.encode()).hexdigest()[:8]
            if prefix in seen and seen[prefix] != candidate:
                a, b = seen[prefix], candidate
                break
            seen.setdefault(prefix, candidate)
        assert a is not None, "collision search failed (raise the budget)"
        assert (
            _hashlib.sha256(a.encode()).hexdigest()[:8]
            == _hashlib.sha256(b.encode()).hexdigest()[:8]
        )
        key = "ci-pin-key-synthetic-06"
        tag_a = material_tag(key, namespace_id="ns", name="n",
                             auth_scheme="api_key", material={"api_key": a})
        tag_b = material_tag(key, namespace_id="ns", name="n",
                             auth_scheme="api_key", material={"api_key": b})
        assert tag_a != tag_b and len(tag_a) == 64
        assert not tags_match(tag_a, tag_b)
        # the HMAC covers every authenticated field, not just the password
        tag_u1 = material_tag(key, namespace_id="ns", name="n",
                              auth_scheme="postgres",
                              material={"user": "ro", "password": "x"})
        tag_u2 = material_tag(key, namespace_id="ns", name="n",
                              auth_scheme="postgres",
                              material={"user": "ro2", "password": "x"})
        assert tag_u1 != tag_u2

    def test_ticket_purpose_and_expiry_enforced(self):
        from ppswitch_executor.claim_security import sign_claim_ticket, verify_claim_ticket

        key = "tok"
        ticket = sign_claim_ticket(key=key, purpose="verify", namespace_id="ns",
                                   name="n", generation=1, pin_version=1, ttl_s=60)
        with pytest.raises(ApiError):
            verify_claim_ticket(ticket, key=key, purpose="discover")
        expired = sign_claim_ticket(key=key, purpose="verify", namespace_id="ns",
                                    name="n", generation=1, pin_version=1, ttl_s=-1)
        with pytest.raises(ApiError):
            verify_claim_ticket(expired, key=key, purpose="verify")
        with pytest.raises(ApiError):
            verify_claim_ticket(ticket, key="other", purpose="verify")


class TestStartupFailClosed:
    """T7: shared mode without the mandatory keys or control wiring
    refuses to start."""

    def test_executor_refuses_shared_without_keys(self, tmp_path):
        from ppswitch_executor.main import ExecutorSettings
        from ppswitch_executor.main import create_app as create_executor_app

        with pytest.raises(RuntimeError):
            create_executor_app(ExecutorSettings(
                demo_mode=True, shared_keyring=True,
                local_attest_dir=str(tmp_path), control_url="http://127.0.0.1:9",
            ))

    def test_executor_refuses_shared_without_control_url(self, tmp_path):
        from ppswitch_executor.main import ExecutorSettings
        from ppswitch_executor.main import create_app as create_executor_app

        with pytest.raises(RuntimeError):
            create_executor_app(ExecutorSettings(
                demo_mode=True, shared_keyring=True,
                pin_key="k" * 8, attest_key="a" * 8,
                local_attest_dir=str(tmp_path),
            ))


class TestConcurrentFirstClaim:
    """T2: exactly one claim row when activations race the INSERT."""

    def test_parallel_activations_yield_one_row(self, harness, personal, fake_keyring):
        fake_keyring.put("race-key", {"auth_scheme": "api_key",
                                      "material": {"api_key": "MOCKKEY-race"}})
        service_id = _service(personal)
        ids = [_conn(personal, service_id, "race-key") for _ in range(2)]
        results: list[int] = []
        lock = threading.Lock()

        def activate(connection_id: str) -> None:
            response = _activate(personal, connection_id)
            with lock:
                results.append(response.status_code)

        threads = [threading.Thread(target=activate, args=(cid,)) for cid in ids]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert results == [200, 200]  # same owner: both fine, one INSERT

        from ppswitch_db.models import KeyringClaimRow

        session = harness.session_factory()
        try:
            rows = session.scalars(
                select(KeyringClaimRow).where(KeyringClaimRow.name == "race-key")
            ).all()
            assert len(rows) == 1
        finally:
            session.close()


class TestAuditHygiene:
    """A8: audits carry no material value, no full 64-hex tag, no proof."""

    def test_audit_rows_are_free_of_secrets(self, harness, personal, fake_keyring):
        fake_keyring.put("audit-key", {"auth_scheme": "api_key",
                                       "material": {"api_key": "MOCKKEY-audit-xyz"}})
        service_id = _service(personal)
        connection_id = _conn(personal, service_id, "audit-key")
        assert _activate(personal, connection_id).status_code == 200
        from ppswitch_db.models import AuditEventRow

        session = harness.session_factory()
        try:
            blob = " ".join(
                str(row.redacted_metadata) for row in session.scalars(
                    select(AuditEventRow)).all()
            )
        finally:
            session.close()
        assert "MOCKKEY-audit-xyz" not in blob
        for token in blob.replace(",", " ").replace(":", " ").split():
            cleaned = token.strip("'\"{}[]")
            assert not (
                len(cleaned) == 64
                and all(char in "0123456789abcdef" for char in cleaned)
            ), f"full material tag leaked into audit: {cleaned}"
