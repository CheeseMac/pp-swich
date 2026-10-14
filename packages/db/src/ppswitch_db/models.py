"""SQLAlchemy 2.0 ORM models.

The schema mirrors docs/06-DATABASE.md section 4 (PostgreSQL DDL reference),
adapted so the same migrations run on SQLite for single-machine development.
Workspace tenancy is enforced on every query in the service layer; the
composite foreign keys below give the database a second line of defense.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = metadata
    type_annotation_map = {
        dict: JSON,
    }


class WorkspaceRow(Base):
    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(
        String(16), CheckConstraint("kind IN ('personal','organization')", "kind"),
        default="personal",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PrincipalRow(Base):
    """Local dev identity mapping. Production resolves principals via OIDC."""

    __tablename__ = "principals"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(
        String(16), CheckConstraint("kind IN ('user','agent','service')", "kind"),
        default="user",
    )
    # workspace role for human principals (review R-13): 'member' | 'reviewer'
    # | 'admin'. NULL for agent/service principals. Legacy rows are NULL and
    # are treated as 'member' by the permission matrix.
    role: Mapped[str | None] = mapped_column(
        String(16),
        CheckConstraint("role IS NULL OR role IN ('member','reviewer','admin')", "role"),
        default=None,
    )
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    token_hint: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ServiceRow(Base):
    __tablename__ = "services"
    __table_args__ = (
        UniqueConstraint("workspace_id", "slug"),
        UniqueConstraint("id", "workspace_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id"), nullable=False, index=True
    )
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    visibility: Mapped[str] = mapped_column(
        String(16),
        CheckConstraint("visibility IN ('private','team','organization')", "visibility"),
        default="private",
    )
    owner_principal_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class EndpointRow(Base):
    __tablename__ = "endpoints"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','approved','disabled')", "status"
        ),
        ForeignKeyConstraint(
            ["service_id", "workspace_id"], ["services.id", "services.workspace_id"]
        ),
        UniqueConstraint("id", "service_id"),
        UniqueConstraint("service_id", "environment", "base_url"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    service_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    environment: Mapped[str] = mapped_column(String(32), nullable=False)
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    approved_hosts: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ObservationRow(Base):
    __tablename__ = "observations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["service_id", "workspace_id"], ["services.id", "services.workspace_id"]
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    service_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_shape: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    redaction_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OperationRow(Base):
    __tablename__ = "operations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('observed','inferred','needs_review','verified','rejected')",
            "status",
        ),
        ForeignKeyConstraint(
            ["service_id", "workspace_id"], ["services.id", "services.workspace_id"]
        ),
        UniqueConstraint("service_id", "fingerprint"),
        UniqueConstraint("id", "service_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    service_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    normalized_path: Mapped[str] = mapped_column(String(512), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="observed")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


Index("ix_operations_service_status", OperationRow.service_id, OperationRow.status)


class OperationVersionRow(Base):
    __tablename__ = "operation_versions"
    __table_args__ = (
        UniqueConstraint("operation_id", "version"),
        CheckConstraint("version > 0", "version_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    operation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("operations.id"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    input_schema: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    output_schema: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    contract_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CapabilityRow(Base):
    __tablename__ = "capabilities"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','reviewed','verified','published','deprecated','disabled')",
            "status",
        ),
        CheckConstraint(
            "visibility IN ('private','team','organization')", "visibility"
        ),
        UniqueConstraint("workspace_id", "name"),
        UniqueConstraint("id", "workspace_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # creator accountability: review/verify/publish must be performed by a
    # DIFFERENT human principal than the creator (review PR-SEC-01)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    status: Mapped[str] = mapped_column(String(16), default="draft")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CapabilityVersionRow(Base):
    __tablename__ = "capability_versions"
    __table_args__ = (
        CheckConstraint("risk_level IN ('low','medium','high','critical')", "risk_level"),
        CheckConstraint("status IN ('draft','published','deprecated')", "status"),
        UniqueConstraint("capability_id", "version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    capability_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("capabilities.id"), nullable=False, index=True
    )
    operation_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("operation_versions.id"), nullable=False
    )
    version: Mapped[str] = mapped_column(String(16), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(16), default="low")
    input_schema: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    output_schema: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    allow_fields: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    policy_ref: Mapped[str] = mapped_column(String(128), nullable=False, default="policy-v1")
    # request mapping: how validated arguments fill the reviewed operation:
    # {"query": {"verbose": "verbose"}, "body": {"amount": "amount"}}
    execution: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # review3 SEC-044: scopes this capability REQUIRES; empty = unscoped.
    # Non-empty means every invocation must carry an explicit authorization
    # chain (grant/connection/delegation) covering ALL of them.
    required_scopes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ConnectionRow(Base):
    __tablename__ = "connections"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','active','expired','revoked')", "status"
        ),
        CheckConstraint(
            "auth_scheme IN ('none','api_key','bearer','oauth2','basic')", "auth_scheme"
        ),
        ForeignKeyConstraint(
            ["service_id", "workspace_id"], ["services.id", "services.workspace_id"]
        ),
        UniqueConstraint("id", "service_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    service_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    owner_principal_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    auth_scheme: Mapped[str] = mapped_column(String(16), default="api_key")
    secret_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    scopes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ProfileRow(Base):
    __tablename__ = "profiles"
    __table_args__ = (
        CheckConstraint("status IN ('enabled','disabled')", "status"),
        ForeignKeyConstraint(
            ["service_id", "workspace_id"], ["services.id", "services.workspace_id"]
        ),
        ForeignKeyConstraint(
            ["endpoint_id", "service_id"], ["endpoints.id", "endpoints.service_id"]
        ),
        ForeignKeyConstraint(
            ["connection_id", "service_id"], ["connections.id", "connections.service_id"]
        ),
        UniqueConstraint("workspace_id", "owner_principal_id", "service_id", "name"),
        UniqueConstraint("id", "workspace_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    service_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    endpoint_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    connection_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    owner_principal_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="enabled")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ActiveBindingRow(Base):
    """Current profile per (workspace, principal, service, context_key).

    Switching uses optimistic concurrency on ``version`` (docs/06 section 9):
    an UPDATE guarded by ``version = :expected``; 0 rows updated means 409.
    """

    __tablename__ = "active_bindings"
    __table_args__ = (
        CheckConstraint("version > 0", "version_positive"),
        # composite FK: the bound profile must live in the same workspace
        ForeignKeyConstraint(
            ["active_profile_id", "workspace_id"], ["profiles.id", "profiles.workspace_id"]
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    principal_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    service_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    context_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    active_profile_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    updated_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)


class AgentGrantRow(Base):
    __tablename__ = "agent_grants"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id"), nullable=False, index=True
    )
    agent_principal_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    capability_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    scopes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DelegationRow(Base):
    """A user delegating their authority to an agent (review P0-2 / SEC-033).

    An agent may only act for a user with an ACTIVE, unexpired delegation;
    invocation-time profile binding resolution is keyed to the delegating
    user, never to a shared agent scope. One row per (user, agent): the
    status column toggles active/revoked.
    """

    __tablename__ = "delegations"
    __table_args__ = (
        CheckConstraint("status IN ('active','revoked')", "status"),
        UniqueConstraint("workspace_id", "user_principal_id", "agent_principal_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id"), nullable=False, index=True
    )
    user_principal_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    agent_principal_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    # optional narrowing: delegation scopes intersect grant + connection scopes
    scopes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InvocationRow(Base):
    __tablename__ = "invocations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','approval_required','running','succeeded','failed',"
            "'rejected','upstream_timeout')",
            "status",
        ),
        UniqueConstraint("workspace_id", "idempotency_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id"), nullable=False, index=True
    )
    capability_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("capability_versions.id"), nullable=False
    )
    capability_name: Mapped[str] = mapped_column(String(128), nullable=False)
    capability_version: Mapped[str] = mapped_column(String(16), nullable=False)
    principal_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    profile_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    profile_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    arguments_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    policy_version: Mapped[str] = mapped_column(String(128), nullable=False, default="policy-v1")
    status: Mapped[str] = mapped_column(String(24), default="pending")
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApprovalRow(Base):
    __tablename__ = "approvals"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id"), nullable=False, index=True
    )
    invocation_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    requester_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    capability_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    profile_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    approver_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    arguments_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    # approvals expire; a stale approval can never authorize a replay
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), CheckConstraint("status IN ('pending','approved','rejected','consumed')", "status"),  # noqa: E501
        default="pending",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEventRow(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id"), nullable=False, index=True
    )
    invocation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    actor_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    decision: Mapped[str] = mapped_column(String(64), nullable=False)
    redacted_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
