"""User -> agent delegation (review2 P0-2 / SEC-033 / SEC-041).

An agent acts for a user only while an ACTIVE, unexpired delegation row
exists. Profile bindings for an agent context are stored in the DELEGATING
user's scope, so two users sharing one agent can never overwrite each
other's effective profile.
"""

from __future__ import annotations

from uuid import UUID

from ppswitch_core.errors import ApiError, ErrorCode
from ppswitch_db.models import AuditEventRow, DelegationRow, PrincipalRow
from sqlalchemy import select
from sqlalchemy.orm import Session

from ppswitch_control.services.invocation_service import epoch_now


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
