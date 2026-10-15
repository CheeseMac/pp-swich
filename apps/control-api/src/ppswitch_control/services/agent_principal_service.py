"""FEAT-05: agent principal onboarding.

"One-click agent access" needs a principal + token the external agent
(Claude, Cursor, custom) can authenticate with. Production replaces the
synthetic token with OIDC audiences; the flow (admin creates the agent
identity, token returned exactly once, then grants) stays identical.

- admin-only (permission matrix ``agent_principals.write``);
- tokens are synthetic dev tokens (``dev-token-agent-`` prefix, covered
  by the repo secret-scanner allowlist) stored as SHA-256 hashes;
- the token is returned ONCE at creation and never listed again;
- creation is audited (``agent_principal.create``).
"""

from __future__ import annotations

import secrets

from ppswitch_core.errors import ApiError, ErrorCode
from ppswitch_db.models import AuditEventRow, PrincipalRow
from sqlalchemy import select
from sqlalchemy.orm import Session

from ppswitch_control.auth import hash_token

# synthetic prefix (repo secret-scanner allowlist), not a credential
TOKEN_PREFIX = "dev-token-agent-"  # noqa: S105


def _generate_token() -> str:
    return TOKEN_PREFIX + secrets.token_hex(16)


def create_agent_principal(
    session: Session,
    *,
    workspace_id,
    principal_id,
    display_name: str,
) -> dict:
    name = (display_name or "").strip()
    if not name or len(name) > 128:
        raise ApiError(ErrorCode.BAD_REQUEST, "display_name is required (1-128 chars)")

    token = _generate_token()
    agent = PrincipalRow(
        workspace_id=workspace_id,
        kind="agent",
        display_name=name,
        token_hash=hash_token(token),
        token_hint=token[:20] + "...",
    )
    session.add(agent)
    session.flush()
    session.add(
        AuditEventRow(
            workspace_id=workspace_id,
            actor_id=principal_id,
            action="agent_principal.create",
            decision="allowed",
            redacted_metadata={"agent_principal_id": str(agent.id), "name": name},
        )
    )
    return {
        "id": str(agent.id),
        "display_name": name,
        "kind": "agent",
        # one-time secret, shown exactly once (same contract as pairing
        # codes and collector tokens)
        "token": token,
    }


def list_agent_principals(session: Session, *, workspace_id) -> list[dict]:
    rows = session.scalars(
        select(PrincipalRow).where(
            PrincipalRow.workspace_id == workspace_id,
            PrincipalRow.kind == "agent",
        )
    ).all()
    return [
        {
            "id": str(r.id),
            "display_name": r.display_name,
            "kind": "agent",
            "token_hint": r.token_hint,
        }
        for r in rows
    ]
