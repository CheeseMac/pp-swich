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
from ppswich_core.errors import ApiError, ErrorCode
from ppswich_db.models import PrincipalRow
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
