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
import time
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
            claim_ticket_key="ci-ticket-key-synthetic-06",
            plan_signing_key="ci-plan-key-synthetic-06",
            local_attest_dir=str(tmp_path / "attest"),
            control_url="http://127.0.0.1:9",  # execute-path only; unused here
        )
        control_settings = ControlSettings(
            db_url=harness.db_url, executor_url="http://executor.test.internal",
            auto_migrate=False, demo_mode=True,
            shared_keyring=True, namespace_id="ci-machine",
            claim_ticket_key="ci-ticket-key-synthetic-06",
            plan_signing_key="ci-plan-key-synthetic-06",
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
        """FIX-03: exactly what the CLI does - dial the OS-protected local
        channel (never HTTP) with the nonce from the protected directory."""
        from ppswitch_secrets.local_channel import local_attest_request

        nonce = (shared["attest_dir"] / "local.nonce").read_text(encoding="utf-8").strip()
        reply = local_attest_request(str(shared["attest_dir"]), {
            "name": name, "pairing_code": code,
            "ticket": pairing["ticket"], "local_nonce": nonce,
        })
        assert "attestation" in reply, reply
        return reply["attestation"]

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
        from ppswitch_secrets.local_channel import local_attest_request

        bad_nonce = local_attest_request(str(shared_stack["attest_dir"]), {
            "name": "shared-2", "pairing_code": pairing["pairing_code"],
            "ticket": pairing["ticket"], "local_nonce": "0" * 64,
        })
        assert "error" in bad_nonce  # no valid protected-file nonce, no proof
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

    def test_r07_http_attest_endpoint_is_gone_everywhere(self, personal, shared_stack):
        """R07: even a valid internal token + a real nonce can never mint
        an attestation over HTTP - the signing surface is the OS-protected
        local channel only (FIX-03)."""
        nonce = (shared_stack["attest_dir"] / "local.nonce").read_text(encoding="utf-8").strip()
        for executor in (personal["executor"], shared_stack["executor"]):
            response = executor.post(
                "/internal/v1/keyring/attest",
                json={"name": "x", "pairing_code": "claim-1234",
                      "ticket": "t" * 40, "local_nonce": nonce},
                headers={"X-Internal-Token": "dev-internal-token-synthetic"},
            )
            assert response.status_code == 404

    def test_r09_old_nonce_reuse_refused(self, shared_stack, fake_keyring):
        """R09: a successful attestation CONSUMES the nonce - replaying the
        same (leaked) value against a fresh pairing is refused."""
        fake_keyring.put("r09-key", {"auth_scheme": "api_key",
                                     "material": {"api_key": "MOCKKEY-r09"}})
        from ppswitch_secrets.local_channel import local_attest_request

        pairing1 = self._pair(shared_stack, "r09-key")
        nonce = (shared_stack["attest_dir"] / "local.nonce").read_text(encoding="utf-8").strip()
        first = local_attest_request(str(shared_stack["attest_dir"]), {
            "name": "r09-key", "pairing_code": pairing1["pairing_code"],
            "ticket": pairing1["ticket"], "local_nonce": nonce,
        })
        assert "attestation" in first
        pairing2 = self._pair(shared_stack, "r09-key")
        replay = local_attest_request(str(shared_stack["attest_dir"]), {
            "name": "r09-key", "pairing_code": pairing2["pairing_code"],
            "ticket": pairing2["ticket"], "local_nonce": nonce,
        })
        assert "error" in replay  # consumed by the first success


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


class TestFix01LegacyRecovery:
    """FIX-01 (A7/T1): a migration-generated pending_claim confers NO
    ownership in shared mode - recovery demands the two-part proof;
    personal mode keeps the candidate owner's plain re-verification."""

    def _seed_pending_claim(self, harness, name: str) -> None:
        from ppswitch_db.models import KeyringClaimRow

        session = harness.session_factory()
        try:
            session.add(KeyringClaimRow(
                id=uuid.uuid4(), namespace_id="ci-machine", name=name,
                owner_principal_id=_principal_id(harness, "Alice (demo)"),
                owner_workspace_id=_workspace_id(harness, "demo-a-"),
                auth_scheme="api_key", generation=1, pin_version=1,
                material_tag=None, status="pending_claim",
                proof_consumed_id="legacy-migration-candidate",
            ))
            session.commit()
        finally:
            session.close()

    def test_r01_shared_recovery_without_proof_refused(self, harness, shared_stack, fake_keyring):
        fake_keyring.put("legacy-key", {"auth_scheme": "api_key",
                                        "material": {"api_key": "MOCKKEY-legacy"}})
        self._seed_pending_claim(harness, "legacy-key")
        service_id = _service(shared_stack)
        connection_id = _conn(shared_stack, service_id, "legacy-key")
        refused = _activate(shared_stack, connection_id)
        assert refused.status_code == 403, refused.text
        assert refused.json()["error"]["code"] == "KEYRING_CLAIM_PROOF_INVALID"

    def test_r02_shared_recovery_with_proof_succeeds(self, harness, shared_stack, fake_keyring):
        fake_keyring.put("legacy-ok", {"auth_scheme": "api_key",
                                       "material": {"api_key": "MOCKKEY-legacy-ok"}})
        self._seed_pending_claim(harness, "legacy-ok")
        service_id = _service(shared_stack)
        connection_id = _conn(shared_stack, service_id, "legacy-ok")
        proofs = TestSharedModeProofs()
        pairing = proofs._pair(shared_stack, "legacy-ok")
        attestation = proofs._attest(shared_stack, "legacy-ok", pairing,
                                     pairing["pairing_code"])
        activated = shared_stack["control"].post(
            f"/api/v1/connections/{connection_id}/activate",
            json={"pairing_code": pairing["pairing_code"],
                  "attestation": attestation},
            headers=_alice(),
        )
        assert activated.status_code == 200, activated.text
        from ppswitch_db.models import KeyringClaimRow

        session = harness.session_factory()
        try:
            claim = session.scalars(select(KeyringClaimRow).where(
                KeyringClaimRow.name == "legacy-ok")).one()
            assert claim.status == "active" and claim.material_tag
        finally:
            session.close()

    def test_r03_personal_recovery_stays_passwordless_for_owner(
        self, harness, personal, fake_keyring
    ):
        from ppswitch_db.models import KeyringClaimRow

        fake_keyring.put("legacy-p", {"auth_scheme": "api_key",
                                      "material": {"api_key": "MOCKKEY-legacy-p"}})
        session = harness.session_factory()
        try:
            session.add(KeyringClaimRow(
                id=uuid.uuid4(), namespace_id="local", name="legacy-p",
                owner_principal_id=_principal_id(harness, "Alice (demo)"),
                owner_workspace_id=_workspace_id(harness, "demo-a-"),
                auth_scheme="api_key", generation=1, pin_version=1,
                material_tag=None, status="pending_claim",
                proof_consumed_id="legacy-migration-candidate",
            ))
            session.commit()
        finally:
            session.close()
        service_id = _service(personal)
        connection_id = _conn(personal, service_id, "legacy-p")
        activated = _activate(personal, connection_id)
        assert activated.status_code == 200, activated.text


class TestFix02KeySeparation:
    """FIX-02: the transport token is NOT an authorization signing key -
    forged tickets/plans signed with the internal token are refused when
    dedicated keys are configured."""

    def test_r04_forged_claim_ticket_with_internal_token_refused(self, shared_stack):
        from ppswitch_executor.claim_security import sign_claim_ticket

        forged = sign_claim_ticket(
            key="dev-internal-token-synthetic",  # the TRANSPORT token
            purpose="verify", namespace_id="ci-machine", name="whatever",
            generation=1, pin_version=1, ttl_s=60,
        )
        response = shared_stack["executor"].post(
            "/internal/v1/secrets/verify",
            json={"secret_ref": "keyring://whatever", "auth_scheme": "api_key",
                  "claim_ticket": forged},
            headers={"X-Internal-Token": "dev-internal-token-synthetic"},
        )
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "KEYRING_CLAIM_PROOF_INVALID"

    def test_r05_plan_signed_with_transport_token_refused(self, tmp_path):
        """An explicit plan-signing key makes internal-token signatures
        invalid at the executor (the two trust channels are separate)."""
        from fastapi.testclient import TestClient
        from ppswitch_executor.main import ExecutorSettings
        from ppswitch_executor.main import create_app as create_executor_app
        from ppswitch_executor.plan_signing import sign_plan

        executor = TestClient(create_executor_app(ExecutorSettings(
            demo_mode=True, plan_signing_key="ci-plan-key-synthetic-06",
        )))
        plan = {"invocation_id": str(uuid.uuid4()), "nonce": "n" * 32,
                "deadline_epoch_s": time.time() + 60}
        response = executor.post(
            "/internal/v1/execute",
            json={"plan": plan,
                  "signature": sign_plan(plan, "dev-internal-token-synthetic")},
            headers={"X-Internal-Token": "dev-internal-token-synthetic"},
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "INVALID_SIGNATURE"

    def test_r11_ticket_audience_mismatch_refused(self):
        from ppswitch_executor.claim_security import sign_claim_ticket, verify_claim_ticket

        key = "ci-ticket-key-synthetic-06"
        ticket = sign_claim_ticket(
            key=key, purpose="verify", namespace_id="ns", name="n",
            generation=1, pin_version=1, ttl_s=60, extra={"aud": "someone-else"},
        )
        with pytest.raises(ApiError):
            verify_claim_ticket(ticket, key=key, purpose="verify")


class TestFix02Startup:
    """R06: shared mode refuses to start without the dedicated keys."""

    def test_r06_shared_without_ticket_key_refused(self, tmp_path):
        from ppswitch_executor.main import ExecutorSettings
        from ppswitch_executor.main import create_app as create_executor_app

        with pytest.raises(RuntimeError):
            create_executor_app(ExecutorSettings(
                demo_mode=True, shared_keyring=True,
                pin_key="k" * 8, attest_key="a" * 8,
                plan_signing_key="p" * 8,
                local_attest_dir=str(tmp_path), control_url="http://127.0.0.1:9",
            ))

    def test_r06b_shared_without_plan_key_refused(self, tmp_path):
        from ppswitch_executor.main import ExecutorSettings
        from ppswitch_executor.main import create_app as create_executor_app

        with pytest.raises(RuntimeError):
            create_executor_app(ExecutorSettings(
                demo_mode=True, shared_keyring=True,
                pin_key="k" * 8, attest_key="a" * 8,
                claim_ticket_key="t" * 8,
                local_attest_dir=str(tmp_path), control_url="http://127.0.0.1:9",
            ))


def _principal_id(harness, display_name: str):
    from ppswitch_db.models import PrincipalRow

    session = harness.session_factory()
    try:
        row = session.scalars(
            select(PrincipalRow).where(PrincipalRow.display_name == display_name)
        ).first()
        assert row is not None
        return row.id
    finally:
        session.close()


def _workspace_id(harness, slug_prefix: str):
    from ppswitch_db.models import WorkspaceRow

    session = harness.session_factory()
    try:
        row = session.scalars(
            select(WorkspaceRow).where(WorkspaceRow.slug.startswith(slug_prefix))
        ).first()
        assert row is not None
        return row.id
    finally:
        session.close()
