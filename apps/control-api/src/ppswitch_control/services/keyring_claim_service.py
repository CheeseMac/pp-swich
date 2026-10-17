"""SECRET-06 control-side claim service (docs/41 + 2026-10-16 errata).

Ownership layer for ``keyring://`` names on a shared executor:

- personal mode (default): first successful activation claims the name -
  the single-user Alpha UX is unchanged;
- shared mode: a first claim (or a re-claim after release) additionally
  requires the two-part ownership proof - a one-time console pairing
  code AND an executor-local attestation obtained on the machine that
  holds the keyring (docs/41 section 8.1 / errata 1);
- re-claims after release are atomic conditional UPDATEs on the old
  generation (errata 2) - the INSERT path is first-claim only;
- rotation is an explicit 409-confirm state machine; the material tag is
  only ever re-pinned through the owner's confirmed re-verification;
- release bumps the generation (tombstone rows are never deleted), so
  plans frozen at an older generation cannot be replayed (anti-ABA).

The full material tag is credential-derived data: it lives in the DB
column, the signed plan and the internal channel - never in audits,
agent payloads or logs.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import uuid as uuid_module
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
from ppswitch_core.entities import InvocationPlanClaim
from ppswitch_core.errors import ApiError, ErrorCode
from ppswitch_db.models import (
    AuditEventRow,
    ConnectionRow,
    KeyringClaimPairingRow,
    KeyringClaimRow,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

KEYRING_NAME = re.compile(r"^[a-z0-9][a-z0-9/_-]{2,63}$")
PAIRING_TTL_SECONDS = 5 * 60
PAIRING_MAX_ATTEMPTS = 5

_BORROW_DENIED = ApiError(
    ErrorCode.KEYRING_NAME_CLAIMED,
    "keyring name already claimed by another principal",
)


def keyring_name_of(secret_ref: str) -> str | None:
    if not secret_ref.startswith("keyring://"):
        return None
    return secret_ref[len("keyring://"):]


def _now() -> datetime:
    return datetime.now(UTC)


def _epoch(value: datetime | None) -> float:
    if value is None:
        return 0.0
    if value.tzinfo is None:  # SQLite naive datetimes (pitfall #4)
        return calendar_timegm(value.utctimetuple())
    return value.timestamp()


def calendar_timegm(t) -> float:
    import calendar

    return calendar.timegm(t)


def get_claim(session: Session, namespace_id: str, name: str) -> KeyringClaimRow | None:
    return session.scalars(
        select(KeyringClaimRow).where(
            KeyringClaimRow.namespace_id == namespace_id,
            KeyringClaimRow.name == name,
        )
    ).one_or_none()


# ---------------------------------------------------------------------------
# connection-create pre-check (UX early failure; activation re-checks)
# ---------------------------------------------------------------------------


def assert_name_referenceable_by(
    session: Session, *, namespace_id: str, name: str, principal_id: UUID
) -> None:
    claim = get_claim(session, namespace_id, name)
    if claim is None:
        return
    if claim.status in ("active", "pending_rotation", "pending_claim"):
        if claim.owner_principal_id != principal_id:
            _audit(
                session, claim.owner_workspace_id, principal_id,
                "claim.rejected", "denied", {"name": name, "reason": "borrow"},
            )
            session.commit()
            raise _BORROW_DENIED
    elif claim.status == "disputed":
        raise ApiError(
            ErrorCode.CONFLICT,
            "keyring name is disputed; an operator must resolve it before use",
        )


# ---------------------------------------------------------------------------
# shared-mode pairing (one-time console code + executor-local attestation)
# ---------------------------------------------------------------------------


def create_pairing(
    session: Session,
    *,
    workspace_id: UUID,
    principal_id: UUID,
    namespace_id: str,
    name: str,
    internal_token: str,
    shared_mode: bool,
    ticket_key: str | None = None,
) -> tuple[KeyringClaimPairingRow, str]:
    if not shared_mode:
        raise ApiError(
            ErrorCode.BAD_REQUEST,
            "Claim pairing is only used in shared-keyring mode; personal mode "
            "activates directly",
        )
    if not KEYRING_NAME.match(name):
        raise ApiError(ErrorCode.BAD_REQUEST, "invalid keyring name")
    # an existing active claim by someone else cannot be stolen via pairing
    assert_name_referenceable_by(
        session, namespace_id=namespace_id, name=name, principal_id=principal_id
    )
    from ppswitch_executor.claim_security import sign_pairing_ticket

    code = f"claim-{secrets.token_hex(8)}"
    proof_id = secrets.token_hex(8)
    ticket = sign_pairing_ticket(
        key=ticket_key or internal_token,
        namespace_id=namespace_id,
        name=name,
        principal_id=str(principal_id),
        workspace_id=str(workspace_id),
        proof_id=proof_id,
        ttl_s=PAIRING_TTL_SECONDS,
    )
    pairing = KeyringClaimPairingRow(
        id=uuid_module.uuid4(),
        workspace_id=workspace_id,
        principal_id=principal_id,
        namespace_id=namespace_id,
        name=name,
        code_hash=hashlib.sha256(code.encode("utf-8")).hexdigest(),
        proof_id=proof_id,
        ticket=ticket,
        status="pending",
        attempts=0,
        expires_at=_now() + timedelta(seconds=PAIRING_TTL_SECONDS),
    )
    session.add(pairing)
    session.flush()
    _audit(
        session, workspace_id, principal_id, "claim.pairing_created", "allowed",
        {"name": name, "proof_id": proof_id},
    )
    # the code is returned ONCE to the caller; never persisted raw
    return pairing, code


def _redeem_pairing(
    session: Session,
    *,
    namespace_id: str,
    name: str,
    principal_id: UUID,
    pairing_code: str,
    attestation: str,
    settings,
    executor_client: httpx.Client,
) -> KeyringClaimPairingRow:
    """Verify code + executor-local attestation; consume the pairing.

    Single-use and race-safe: the consumed transition is a conditional
    UPDATE - only one concurrent redemption wins (T2).
    """
    candidates = session.scalars(
        select(KeyringClaimPairingRow).where(
            KeyringClaimPairingRow.namespace_id == namespace_id,
            KeyringClaimPairingRow.name == name,
            KeyringClaimPairingRow.principal_id == principal_id,
            KeyringClaimPairingRow.status == "pending",
        )
    ).all()
    code_hash = hashlib.sha256(pairing_code.encode("utf-8")).hexdigest()
    now = _now()
    match: KeyringClaimPairingRow | None = None
    for candidate in candidates:
        if _epoch(candidate.expires_at) >= _epoch(now):
            if candidate.code_hash == code_hash:
                match = candidate
                break
            candidate.attempts += 1
            if candidate.attempts >= PAIRING_MAX_ATTEMPTS:
                candidate.status = "expired"
    if match is None:
        session.commit()
        raise ApiError(
            ErrorCode.KEYRING_CLAIM_PROOF_INVALID,
            "Pairing code invalid, expired or brute-force capped",
        )
    # executor-side constant-time verification of the FULL binding
    try:
        response = executor_client.post(
            f"{settings.executor_url}/internal/v1/keyring/attest/verify",
            json={
                "attestation": attestation,
                "namespace_id": namespace_id,
                "name": name,
                "principal_id": str(principal_id),
                "workspace_id": str(match.workspace_id),
                "proof_id": match.proof_id,
                "pairing_code_hash": code_hash,
            },
            headers={"X-Internal-Token": settings.internal_token},
        )
    except httpx.HTTPError as exc:
        raise ApiError(ErrorCode.UPSTREAM_FAILURE, "Executor unreachable") from exc
    if response.status_code != 200:
        raise ApiError(
            ErrorCode.KEYRING_CLAIM_PROOF_INVALID,
            "Local ownership attestation rejected",
        )
    from sqlalchemy import update

    result = session.execute(
        update(KeyringClaimPairingRow)
        .where(
            KeyringClaimPairingRow.id == match.id,
            KeyringClaimPairingRow.status == "pending",
        )
        .values(status="consumed", consumed_at=now)
    )
    if getattr(result, "rowcount", 0) == 0:
        session.commit()
        raise ApiError(
            ErrorCode.KEYRING_CLAIM_PROOF_INVALID, "Pairing already consumed"
        )
    return match


# ---------------------------------------------------------------------------
# activation-time claim acquisition (personal + shared) and pinning
# ---------------------------------------------------------------------------


def acquire_or_check_claim(
    session: Session,
    *,
    settings,
    executor_client: httpx.Client,
    workspace_id: UUID,
    principal_id: UUID,
    connection: ConnectionRow,
    pairing_code: str | None,
    attestation: str | None,
) -> tuple[KeyringClaimRow | None, TicketBundle]:
    """Run the authorization decision + build the verify reservation.

    Nothing is mutated here: the executor proves the credential under a
    purpose=verify ticket BEFORE any claim/pin write, so a failed verify
    leaves no active claim behind (atomicity, docs/41 section 3.1).
    Returns (current claim row or None, reservation bundle).
    """
    name = keyring_name_of(connection.secret_ref)
    assert name is not None
    namespace_id = settings.namespace_id
    shared_mode = settings.shared_keyring
    claim = get_claim(session, namespace_id, name)

    target_generation = 1
    target_pin = 1
    fresh_claim = False  # INSERT path (first claim ever)
    reclaim = False  # conditional UPDATE path (released)
    recovery = False  # legacy pending_claim recovery by its candidate owner

    if claim is None:
        if shared_mode:
            if not (pairing_code and attestation):
                raise ApiError(
                    ErrorCode.KEYRING_CLAIM_PROOF_INVALID,
                    "Shared mode requires a pairing code + local attestation "
                    "for the first claim (see docs/41 section 8.1)",
                )
            pairing = _redeem_pairing(
                session, namespace_id=namespace_id, name=name,
                principal_id=principal_id, pairing_code=pairing_code,
                attestation=attestation, settings=settings,
                executor_client=executor_client,
            )
            proof_id = pairing.proof_id
        else:
            proof_id = None
        fresh_claim = True
    elif claim.status == "released":
        if shared_mode:
            if not (pairing_code and attestation):
                raise ApiError(
                    ErrorCode.KEYRING_CLAIM_PROOF_INVALID,
                    "Re-claiming a released name requires a new pairing proof",
                )
            pairing = _redeem_pairing(
                session, namespace_id=namespace_id, name=name,
                principal_id=principal_id, pairing_code=pairing_code,
                attestation=attestation, settings=settings,
                executor_client=executor_client,
            )
            proof_id = pairing.proof_id
        else:
            if claim.owner_principal_id != principal_id:
                _audit(session, claim.owner_workspace_id, principal_id,
                       "claim.rejected", "denied", {"name": name, "reason": "borrow"})
                session.commit()
                raise _BORROW_DENIED
            proof_id = None
        # errata 2: released rows are NEVER re-INSERTed - a conditional
        # UPDATE on the observed generation is the only re-claim path
        target_generation = claim.generation + 1
        target_pin = 1
        reclaim = True
    elif claim.status == "disputed":
        raise ApiError(
            ErrorCode.CONFLICT,
            "keyring name is disputed; an operator must resolve it before use",
        )
    elif claim.owner_principal_id != principal_id:
        _audit(session, claim.owner_workspace_id, principal_id,
               "claim.rejected", "denied", {"name": name, "reason": "borrow"})
        session.commit()
        raise _BORROW_DENIED
    elif claim.status == "pending_claim":
        # FIX-01: a legacy migration candidate confers NO ownership in
        # shared mode - recovery requires the full two-part proof (one-time
        # pairing code + executor-local attestation), exactly like a first
        # claim. Personal mode (single-user trust domain) lets the
        # candidate owner recover by plain re-verification.
        if shared_mode:
            if not (pairing_code and attestation):
                raise ApiError(
                    ErrorCode.KEYRING_CLAIM_PROOF_INVALID,
                    "Recovering a legacy claim requires a pairing code + "
                    "local attestation (docs/41 A7)",
                )
            pairing = _redeem_pairing(
                session, namespace_id=namespace_id, name=name,
                principal_id=principal_id, pairing_code=pairing_code,
                attestation=attestation, settings=settings,
                executor_client=executor_client,
            )
            proof_id = pairing.proof_id
        else:
            if claim.owner_principal_id != principal_id:
                _audit(session, claim.owner_workspace_id, principal_id,
                       "claim.rejected", "denied", {"name": name, "reason": "borrow"})
                session.commit()
                raise _BORROW_DENIED
            proof_id = "recovery-reverify"
        recovery = True
        target_generation = claim.generation
        target_pin = 1
    else:
        # active / pending_rotation by the owner
        target_generation = claim.generation
        target_pin = claim.pin_version
        proof_id = claim.proof_consumed_id

    # the purpose=verify reservation: frozen at the TARGET generation/pin,
    # short-lived, demanded by the executor before it resolves the material
    from ppswitch_executor.claim_security import sign_claim_ticket

    ticket = sign_claim_ticket(
        key=settings.effective_claim_ticket_key,
        purpose="verify",
        namespace_id=namespace_id,
        name=name,
        generation=target_generation,
        pin_version=target_pin,
        ttl_s=60.0,
    )
    return (
        claim,
        TicketBundle(
            namespace_id=namespace_id,
            name=name,
            target_generation=target_generation,
            target_pin=target_pin,
            ticket=ticket,
            fresh_claim=fresh_claim,
            reclaim=reclaim,
            recovery=recovery,
            proof_id=proof_id,
        ),
    )


@dataclass(frozen=True)
class TicketBundle:
    """The activation reservation: target claim coordinates + the
    purpose=verify ticket the executor demands before resolving."""

    namespace_id: str
    name: str
    target_generation: int
    target_pin: int
    ticket: str
    fresh_claim: bool
    reclaim: bool
    recovery: bool
    proof_id: str | None


def executor_verify_with_ticket(
    *,
    settings,
    executor_client: httpx.Client,
    connection: ConnectionRow,
    bundle: TicketBundle,
    db_target: dict | None,
) -> dict:
    """Ask the executor to resolve + tag the material under the ticket."""
    payload: dict[str, Any] = {
        "secret_ref": connection.secret_ref,
        "auth_scheme": connection.auth_scheme,
        "claim_ticket": bundle.ticket,
    }
    if db_target is not None:
        payload["db_target"] = db_target
    try:
        response = executor_client.post(
            f"{settings.executor_url}/internal/v1/secrets/verify",
            json=payload,
            headers={"X-Internal-Token": settings.internal_token},
        )
    except httpx.HTTPError as exc:
        raise ApiError(ErrorCode.SECRET_PROVIDER_UNAVAILABLE, "Executor unreachable") from exc
    body = response.json() if response.status_code == 200 else {}
    if not body.get("ok"):
        raise ApiError(
            ErrorCode.SECRET_PROVIDER_UNAVAILABLE,
            "Secret store did not resolve this connection's secret_ref",
            {"detail": body.get("detail") or body.get("reason") or "unavailable"},
        )
    if not body.get("material_tag"):
        raise ApiError(
            ErrorCode.SECRET_PROVIDER_UNAVAILABLE,
            "Executor did not return a material tag for a keyring ref",
        )
    return body


def commit_claim(
    session: Session,
    *,
    workspace_id: UUID,
    principal_id: UUID,
    connection: ConnectionRow,
    claim: KeyringClaimRow | None,
    bundle: TicketBundle,
    material_tag: str,
    auth_scheme: str,
) -> KeyringClaimRow:
    """Atomically persist the claim + pin (INSERT / conditional UPDATE)."""
    from sqlalchemy import insert, update
    from sqlalchemy.exc import IntegrityError

    name = bundle.name
    if bundle.fresh_claim:
        try:
            # SAVEPOINT keeps the race decision portable across SQLite and
            # PostgreSQL: the unique (namespace, name) index picks exactly
            # one winner, the loser sees IntegrityError (T2)
            with session.begin_nested():
                session.execute(
                    insert(KeyringClaimRow).values(
                        id=uuid_module.uuid4(),
                        namespace_id=bundle.namespace_id,
                        name=name,
                        owner_principal_id=principal_id,
                        owner_workspace_id=workspace_id,
                        auth_scheme=auth_scheme,
                        generation=1,
                        pin_version=1,
                        material_tag=material_tag,
                        status="active",
                        proof_consumed_id=bundle.proof_id,
                    )
                )
        except IntegrityError:
            session.commit()
            loser = get_claim(session, bundle.namespace_id, name)
            if loser is not None and loser.owner_principal_id != principal_id:
                raise _BORROW_DENIED from None
            claim = loser
        else:
            claim = get_claim(session, bundle.namespace_id, name)
            assert claim is not None
            _audit(session, workspace_id, principal_id, "claim.created", "allowed",
                   {"name": name, "generation": 1})
    elif bundle.reclaim:
        assert claim is not None  # reclaim/recovery only happen on rows
        result = session.execute(
            update(KeyringClaimRow)
            .where(
                KeyringClaimRow.id == claim.id,
                KeyringClaimRow.status == "released",
                KeyringClaimRow.generation == claim.generation,
            )
            .values(
                owner_principal_id=principal_id,
                owner_workspace_id=workspace_id,
                auth_scheme=auth_scheme,
                generation=claim.generation + 1,
                pin_version=1,
                material_tag=material_tag,
                status="active",
                proof_consumed_id=bundle.proof_id,
            )
        )
        if getattr(result, "rowcount", 0) == 0:
            session.commit()
            raise ApiError(
                ErrorCode.CONFLICT, "Claim changed concurrently; retry activation"
            )
        _audit(session, workspace_id, principal_id, "claim.recovered", "allowed",
               {"name": name, "generation": claim.generation + 1})
    elif bundle.recovery:
        assert claim is not None
        result = session.execute(
            update(KeyringClaimRow)
            .where(
                KeyringClaimRow.id == claim.id,
                KeyringClaimRow.status == "pending_claim",
            )
            .values(
                status="active",
                pin_version=1,
                material_tag=material_tag,
                proof_consumed_id=bundle.proof_id,
            )
        )
        if getattr(result, "rowcount", 0) == 0:
            session.commit()
            raise ApiError(ErrorCode.CONFLICT, "Claim changed concurrently")
        _audit(session, workspace_id, principal_id, "claim.recovered", "allowed",
               {"name": name, "generation": claim.generation})
    assert claim is not None
    session.flush()
    _pin_connection(session, connection, claim, material_tag)
    return claim


def _pin_connection(
    session: Session, connection: ConnectionRow, claim: KeyringClaimRow,
    material_tag: str,
) -> None:
    connection.claim_generation = claim.generation
    connection.pin_version = claim.pin_version
    connection.secret_material_tag = material_tag
    connection.pin_state = "pinned"


# ---------------------------------------------------------------------------
# rotation state machine
# ---------------------------------------------------------------------------


def detect_rotation(
    claim: KeyringClaimRow, material_tag: str
) -> bool:
    """True when the resolved material no longer matches the pinned tag."""
    return (
        claim.status in ("active", "pending_rotation")
        and claim.material_tag is not None
        and claim.material_tag != material_tag
    )


def mark_rotation_pending(session: Session, claim: KeyringClaimRow, name: str,
                          workspace_id: UUID, principal_id: UUID) -> None:
    from sqlalchemy import update

    result = session.execute(
        update(KeyringClaimRow)
        .where(
            KeyringClaimRow.id == claim.id,
            KeyringClaimRow.status == "active",
            KeyringClaimRow.pin_version == claim.pin_version,
        )
        .values(status="pending_rotation")
    )
    if getattr(result, "rowcount", 0) == 1:
        _audit(session, workspace_id, principal_id, "claim.rotation_pending",
               "pending", {"name": name})


def confirm_rotation(
    session: Session,
    *,
    settings,
    executor_client: httpx.Client,
    workspace_id: UUID,
    principal_id: UUID,
    connection: ConnectionRow,
) -> KeyringClaimRow:
    """Owner's explicit 409-resolution: re-verify under the reservation,
    then CAS the claim to active(pin+1). Old-pin connections stay refused
    until each re-verifies (docs/41 section 3.2)."""
    name = keyring_name_of(connection.secret_ref)
    assert name is not None
    claim = get_claim(session, settings.namespace_id, name)
    if claim is None:
        raise ApiError(ErrorCode.CONNECTION_NOT_FOUND, "No claim for this connection")
    if claim.owner_principal_id != principal_id:
        raise _BORROW_DENIED
    if claim.status != "pending_rotation":
        raise ApiError(
            ErrorCode.CONFLICT, f"Claim is {claim.status}, not pending rotation"
        )
    from ppswitch_executor.claim_security import sign_claim_ticket

    ticket = sign_claim_ticket(
        key=settings.effective_claim_ticket_key,
        purpose="verify",
        namespace_id=settings.namespace_id,
        name=name,
        generation=claim.generation,
        pin_version=claim.pin_version + 1,
        ttl_s=60.0,
    )
    bundle = TicketBundle(
        namespace_id=settings.namespace_id, name=name,
        target_generation=claim.generation,
        target_pin=claim.pin_version + 1, ticket=ticket,
        fresh_claim=False, reclaim=False, recovery=False,
        proof_id=claim.proof_consumed_id,
    )
    body = executor_verify_with_ticket(
        settings=settings, executor_client=executor_client,
        connection=connection, bundle=bundle, db_target=None,
    )
    material_tag = body["material_tag"]
    from sqlalchemy import update

    result = session.execute(
        update(KeyringClaimRow)
        .where(
            KeyringClaimRow.id == claim.id,
            KeyringClaimRow.status == "pending_rotation",
            KeyringClaimRow.generation == claim.generation,
            KeyringClaimRow.pin_version == claim.pin_version,
        )
        .values(
            status="active", pin_version=claim.pin_version + 1,
            material_tag=material_tag,
        )
    )
    if getattr(result, "rowcount", 0) == 0:
        session.commit()
        raise ApiError(
            ErrorCode.CONFLICT, "Rotation changed concurrently; retry"
        )
    session.flush()
    refreshed = get_claim(session, settings.namespace_id, name)
    assert refreshed is not None
    _pin_connection(session, connection, refreshed, material_tag)
    connection.status = "active"
    _audit(session, workspace_id, principal_id, "claim.rotated", "allowed",
           {"name": name, "pin_version": refreshed.pin_version})
    return refreshed


# ---------------------------------------------------------------------------
# release (owner or the claim's own workspace admin - never a foreign admin)
# ---------------------------------------------------------------------------


def release_claim(
    session: Session,
    *,
    namespace_id: str,
    name: str,
    workspace_id: UUID,
    principal_id: UUID,
    is_workspace_admin: bool,
) -> KeyringClaimRow:
    claim = get_claim(session, namespace_id, name)
    if claim is None:
        raise ApiError(ErrorCode.NOT_FOUND, "No claim under that name")
    authorized = claim.owner_principal_id == principal_id or (
        is_workspace_admin and claim.owner_workspace_id == workspace_id
    )
    if not authorized:
        # cross-workspace admins must not learn or release foreign claims
        raise ApiError(ErrorCode.FORBIDDEN, "Not authorized to release this claim")
    from sqlalchemy import update

    result = session.execute(
        update(KeyringClaimRow)
        .where(
            KeyringClaimRow.id == claim.id,
            KeyringClaimRow.status != "released",
            KeyringClaimRow.generation == claim.generation,
        )
        .values(status="released", generation=claim.generation + 1)
    )
    if getattr(result, "rowcount", 0) == 0:
        session.commit()
        raise ApiError(ErrorCode.CONFLICT, "Claim changed concurrently")
    # every pinned connection on this name stops executing immediately
    rows = session.scalars(
        select(ConnectionRow).where(
            ConnectionRow.secret_ref == f"keyring://{name}",
            ConnectionRow.pin_state != "unpinned",
        )
    ).all()
    for row in rows:
        row.pin_state = "unpinned"
        row.claim_generation = None
        row.pin_version = None
        row.secret_material_tag = None
    _audit(session, workspace_id, principal_id, "claim.released", "allowed",
           {"name": name, "generation": claim.generation + 1})
    return get_claim(session, namespace_id, name)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# plan-build freeze (called from invocation/test/discovery paths)
# ---------------------------------------------------------------------------


def resolve_claim_context(
    session: Session, *, settings, connection: ConnectionRow
) -> InvocationPlanClaim | None:
    """Freeze the live claim state into the signed plan, or reject.

    Universal fail-closed: un-pinned keyring connections never execute
    (no NULL-tag fallback, docs/41 section 3.3), status must be active,
    and the connection must sit at the claim's current generation/pin.
    """
    name = keyring_name_of(connection.secret_ref)
    if name is None:
        return None
    claim = get_claim(session, settings.namespace_id, name)
    if (
        claim is None
        or claim.status != "active"
        or claim.owner_principal_id != connection.owner_principal_id
        or claim.material_tag is None
        or connection.pin_state != "pinned"
        or connection.claim_generation != claim.generation
        or connection.pin_version != claim.pin_version
        or connection.secret_material_tag != claim.material_tag
    ):
        raise ApiError(
            ErrorCode.FORBIDDEN,
            "Connection credential claim is not active or not pinned; "
            "re-activate the connection first",
        )
    return InvocationPlanClaim(
        namespace_id=claim.namespace_id,
        name=claim.name,
        generation=claim.generation,
        pin_version=claim.pin_version,
        material_tag=claim.material_tag,
    )


def sign_discover_ticket(*, settings, claim: InvocationPlanClaim) -> str:
    from ppswitch_executor.claim_security import sign_claim_ticket

    return sign_claim_ticket(
        key=settings.effective_claim_ticket_key,
        purpose="discover",
        namespace_id=claim.namespace_id,
        name=claim.name,
        generation=claim.generation,
        pin_version=claim.pin_version,
        ttl_s=60.0,
    )


def _audit(
    session: Session,
    workspace_id: UUID,
    actor_id: UUID,
    action: str,
    decision: str,
    metadata: dict,
) -> None:
    session.add(
        AuditEventRow(
            workspace_id=workspace_id,
            actor_id=actor_id,
            action=action,
            decision=decision,
            redacted_metadata=metadata,
        )
    )
