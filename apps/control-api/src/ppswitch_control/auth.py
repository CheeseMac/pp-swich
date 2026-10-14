"""Authentication for the control plane.

Dev deployment: synthetic bearer tokens whose SHA-256 hash is stored in the
``principals`` table (a controlled local mock - docs/02-ARCHITECTURE.md
section 6 "本地开发账号/受控 mock"). Production replaces this dependency
with OIDC session / JWT audience validation; the AuthContext shape stays.

Identity NEVER comes from request payloads: only from the validated token
(docs/00-START-HERE.md red line: "不得以模型输出的 tenant_id、user_id、
agent_id 作为身份依据").
"""

from __future__ import annotations

import hashlib
from collections.abc import Generator
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, Request
from ppswitch_core.errors import ApiError, ErrorCode
from ppswitch_db.models import PrincipalRow
from sqlalchemy.orm import Session


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AuthContext:
    principal: PrincipalRow
    workspace_id: UUID

    @property
    def principal_id(self) -> UUID:
        return self.principal.id


def _extract_bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise ApiError(ErrorCode.UNAUTHENTICATED, "Missing bearer token")
    token = header[7:].strip()
    if not token:
        raise ApiError(ErrorCode.UNAUTHENTICATED, "Missing bearer token")
    return token


def get_session(request: Request) -> Generator[Session, None, None]:
    session: Session = request.app.state.session_factory()
    request.state.db_session = session
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def require_principal(
    request: Request, session: Session = Depends(get_session)
) -> AuthContext:
    token = _extract_bearer(request)
    principal = (
        session.query(PrincipalRow).filter_by(token_hash=hash_token(token)).one_or_none()
    )
    if principal is None:
        raise ApiError(ErrorCode.UNAUTHENTICATED, "Unknown or revoked credential")
    request.state.principal_id = str(principal.id)
    return AuthContext(principal=principal, workspace_id=principal.workspace_id)


def require_user(
    auth: AuthContext = Depends(require_principal),
) -> AuthContext:
    """Management APIs are human-only: agent principals are denied.

    Review decision PR-SEC-01: an agent must never be able to grant
    itself capabilities, review/publish tools, approve endpoints or
    approve high-risk invocations.
    """
    if auth.principal.kind != "user":
        raise ApiError(
            ErrorCode.FORBIDDEN,
            "Management operations require a human principal",
        )
    return auth


# ---------------------------------------------------------------------------
# Permission matrix (review2 P0-1 / R-13): default-deny per principal kind.
#
# Instead of scattering require_user across individual routes, every route
# declares the action it performs; the matrix below decides who may perform
# it. Agents are limited to the discovery of granted capabilities, tool
# invocation and reading their own invocation records - nothing else.
# ---------------------------------------------------------------------------

ROLE_RANK: dict[str, int] = {"member": 0, "reviewer": 1, "admin": 2}

# minimum role required for user principals ("member" = any user)
ACTION_MIN_ROLE: dict[str, int] = {
    "workspace.create": 0,
    "services.read": 0,
    "services.write": 0,
    "endpoints.read": 0,
    "endpoints.write": 0,
    "endpoints.approve": 1,   # review3 SEC-047: approving network targets
                              # is a reviewer-grade action, not member-grade
    "discovery.import": 0,
    "operations.read": 0,
    "operations.verify": 0,
    "capabilities.read": 0,
    "capabilities.create": 0,
    "capabilities.review": 1,   # R-13: formal reviewer role, not just "any human"
    "capabilities.publish": 1,
    "connections.write": 0,
    "connections.read": 0,
    "profiles.write": 0,
    "profiles.read": 0,
    "bindings.write": 0,
    "bindings.read": 0,
    "delegations.write": 0,
    "delegations.read": 0,
    "invocations.create": 0,
    "invocations.read": 0,
    "approvals.decide": 1,   # review3 SEC-047: high-risk approvals require
                             # the reviewer role (business-resource
                             # approvers arrive with RBAC deepening)
    "agent_grants.write": 2,    # granting is an admin action
    "audit.read": 2,            # workspace-wide audit trail: admins only
}

# what an agent principal may do at all (review2 P0-1: agents discover
# granted capabilities, invoke tools, and read their own invocations)
AGENT_ACTIONS: frozenset[str] = frozenset(
    {"capabilities.read", "invocations.create", "invocations.read"}
)

# machine service principals: no actions granted yet (phase in with the
# Edge Connector work); denied everything by default
SERVICE_ACTIONS: frozenset[str] = frozenset()


def require_permission(action: str):
    """Route dependency enforcing the permission matrix for ``action``."""

    def dependency(
        auth: AuthContext = Depends(require_principal),
    ) -> AuthContext:
        principal = auth.principal
        if principal.kind == "agent":
            if action not in AGENT_ACTIONS:
                raise ApiError(
                    ErrorCode.FORBIDDEN,
                    "Agents may only discover granted capabilities and invoke tools",
                )
            return auth
        if principal.kind == "service":
            if action not in SERVICE_ACTIONS:
                raise ApiError(ErrorCode.FORBIDDEN, "Service principals have no API access")
            return auth
        # human principal: role gate (legacy NULL roles behave as member)
        role = principal.role or "member"
        min_role = ACTION_MIN_ROLE.get(action, ROLE_RANK["admin"])
        if ROLE_RANK.get(role, -1) < min_role:
            raise ApiError(
                ErrorCode.FORBIDDEN,
                f"This operation requires a higher workspace role than '{role}'",
            )
        return auth

    return dependency
