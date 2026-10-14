"""User -> agent delegation (review2 P0-2 / SEC-033 / SEC-041).

An agent acts for a user only while an ACTIVE, unexpired delegation row
exists. Profile bindings for an agent context are stored in the DELEGATING
user's scope, so two users sharing one agent can never overwrite each
other's effective profile.

Review3 SEC-046 / ADR-005 adds *delegation contexts*: short-lived
HMAC-signed tickets issued over an authenticated USER API call. A shared
agent with several active delegations may only pick the acting user by
presenting a valid ticket - never by a request parameter. Tickets are
unforgeable without the server secret and expire quickly.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from uuid import UUID

from ppswitch_core.errors import ApiError, ErrorCode
from ppswitch_db.models import AuditEventRow, DelegationRow, PrincipalRow
from sqlalchemy import select
from sqlalchemy.orm import Session

from ppswitch_control.services.invocation_service import epoch_now

DELEGATION_CONTEXT_TTL_S = 10 * 60  # review3: short-lived by design
DELEGATION_CONTEXT_MAX_LEN = 2048


def _epoch(value) -> float:
    # local import avoids a cycle; same naive-UTC semantics as invocation_service
    if value.tzinfo is None:
        import calendar

        return float(calendar.timegm(value.timetuple()))
    return value.timestamp()


def is_active(delegation: DelegationRow, *, now: float | None = None) -> bool:
    if delegation.status != "active":
        return False
    if delegation.expires_at is None:
        return True
    return _epoch(delegation.expires_at) >= (now if now is not None else epoch_now())


def _active_delegation_exists(
    session: Session, workspace_id: UUID, user_principal_id: UUID, agent_principal_id: UUID
) -> bool:
    row = session.scalars(
        select(DelegationRow).where(
            DelegationRow.workspace_id == workspace_id,
            DelegationRow.user_principal_id == user_principal_id,
            DelegationRow.agent_principal_id == agent_principal_id,
        )
    ).one_or_none()
    return row is not None and is_active(row)


def active_delegations_for_agent(
    session: Session, workspace_id: UUID, agent_principal_id: UUID
) -> list[DelegationRow]:
    """All active user->agent delegations (unexpired)."""
    now = epoch_now()
    rows = session.scalars(
        select(DelegationRow).where(
            DelegationRow.workspace_id == workspace_id,
            DelegationRow.agent_principal_id == agent_principal_id,
        )
    ).all()
    return [r for r in rows if is_active(r, now=now)]


def create_delegation(
    session: Session,
    *,
    workspace_id: UUID,
    principal_id: UUID,
    agent_principal_id: UUID,
    scopes: list[str],
    expires_at=None,
) -> DelegationRow:
    user = session.get(PrincipalRow, principal_id)
    agent = session.get(PrincipalRow, agent_principal_id)
    if user is None or user.workspace_id != workspace_id or user.kind != "user":
        raise ApiError(ErrorCode.FORBIDDEN, "Only user principals delegate")
    if agent is None or agent.workspace_id != workspace_id or agent.kind != "agent":
        raise ApiError(ErrorCode.NOT_FOUND, "Agent principal not found in this workspace")

    existing = session.scalars(
        select(DelegationRow).where(
            DelegationRow.workspace_id == workspace_id,
            DelegationRow.user_principal_id == principal_id,
            DelegationRow.agent_principal_id == agent_principal_id,
        )
    ).one_or_none()
    if existing is not None:
        # reactivate in place: the (user, agent) pair keeps exactly one row
        existing.status = "active"
        existing.scopes = scopes
        existing.expires_at = expires_at
        delegation = existing
    else:
        delegation = DelegationRow(
            workspace_id=workspace_id,
            user_principal_id=principal_id,
            agent_principal_id=agent_principal_id,
            scopes=scopes,
            status="active",
            expires_at=expires_at,
        )
        session.add(delegation)
    session.flush()
    session.add(
        AuditEventRow(
            workspace_id=workspace_id,
            actor_id=principal_id,
            action="delegation.create",
            decision="allowed",
            redacted_metadata={"agent_principal_id": str(agent_principal_id)},
        )
    )
    return delegation


def revoke_delegation(
    session: Session, *, workspace_id: UUID, principal_id: UUID, delegation_id: UUID
) -> DelegationRow:
    delegation = session.get(DelegationRow, delegation_id)
    if delegation is None or delegation.workspace_id != workspace_id:
        raise ApiError(ErrorCode.NOT_FOUND, "Delegation not found")
    # only the delegating user (or an admin, checked at the route layer for
    # simplicity here we keep strictly the delegating user) may revoke
    if delegation.user_principal_id != principal_id:
        raise ApiError(ErrorCode.FORBIDDEN, "Only the delegating user may revoke")
    delegation.status = "revoked"
    session.flush()
    session.add(
        AuditEventRow(
            workspace_id=workspace_id,
            actor_id=principal_id,
            action="delegation.revoke",
            decision="allowed",
            redacted_metadata={"delegation_id": str(delegation_id)},
        )
    )
    return delegation


# ---------------------------------------------------------------------------
# delegation context tickets (review3 SEC-046 / ADR-005)
# ---------------------------------------------------------------------------


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def issue_delegation_context(
    session: Session,
    *,
    workspace_id: UUID,
    principal_id: UUID,
    delegation_id: UUID,
    secret: str,
    ttl_s: int = DELEGATION_CONTEXT_TTL_S,
) -> tuple[str, float]:
    """Issue a short-lived, signed acting-identity ticket (user-auth only).

    The ticket proves that the delegating USER initiated this context right
    now; an agent cannot mint one for another user because it lacks the
    server secret and the user credential.
    """
    delegation = session.get(DelegationRow, delegation_id)
    if delegation is None or delegation.workspace_id != workspace_id:
        raise ApiError(ErrorCode.NOT_FOUND, "Delegation not found")
    if delegation.user_principal_id != principal_id:
        raise ApiError(ErrorCode.FORBIDDEN, "Only the delegating user may issue contexts")
    if not is_active(delegation):
        raise ApiError(ErrorCode.CONFLICT, "Delegation is not active")

    expires = epoch_now() + ttl_s
    payload = {
        "v": 1,
        "delegation_id": str(delegation.id),
        "workspace_id": str(workspace_id),
        "user_id": str(delegation.user_principal_id),
        "agent_id": str(delegation.agent_principal_id),
        "scopes": delegation.scopes or [],
        "exp": expires,
        "nonce": uuid.uuid4().hex,
    }
    body = _b64encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    signature = _b64encode(
        hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    )
    session.add(
        AuditEventRow(
            workspace_id=workspace_id,
            actor_id=principal_id,
            action="delegation.context.issue",
            decision="allowed",
            redacted_metadata={
                "delegation_id": str(delegation_id),
                "agent_principal_id": str(delegation.agent_principal_id),
            },
        )
    )
    return f"{body}.{signature}", expires


def verify_delegation_context(
    token: str,
    *,
    secret: str,
    workspace_id: UUID,
    agent_principal_id: UUID,
) -> dict:
    """Verify a delegation context ticket; raise FORBIDDEN on any mismatch.

    Returns the payload (with ``user_id`` naming the acting user). The
    caller still re-checks that the underlying delegation is ACTIVE - the
    ticket proves issuance, revocation is checked against live state.
    """
    if not token or len(token) > DELEGATION_CONTEXT_MAX_LEN or token.count(".") != 1:
        raise ApiError(ErrorCode.FORBIDDEN, "Malformed delegation context")
    body, signature = token.split(".", 1)
    expected = _b64encode(
        hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(signature, expected):
        raise ApiError(ErrorCode.FORBIDDEN, "Delegation context signature is invalid")
    try:
        payload = json.loads(_b64decode(body))
    except (ValueError, TypeError) as exc:
        raise ApiError(ErrorCode.FORBIDDEN, "Malformed delegation context") from exc
    if payload.get("v") != 1:
        raise ApiError(ErrorCode.FORBIDDEN, "Unsupported delegation context version")
    if float(payload.get("exp", 0)) < epoch_now():
        raise ApiError(ErrorCode.FORBIDDEN, "Delegation context has expired")
    if payload.get("workspace_id") != str(workspace_id):
        raise ApiError(ErrorCode.FORBIDDEN, "Delegation context belongs to another workspace")
    if payload.get("agent_id") != str(agent_principal_id):
        raise ApiError(
            ErrorCode.FORBIDDEN, "Delegation context was issued for a different agent"
        )
    return payload
