"""Domain entities (Pydantic v2).

Rules enforced here (docs/19-FIRST-CODING-TASKS.md Task 002):
- IDs are UUIDs.
- Status enums are explicit.
- A Profile must reference an Endpoint and a Connection of the *same* Service.
- ``secret_ref`` never appears in public serializations; use the ``*Public``
  projections instead.
- Published capability versions are immutable.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ppswitch_core.enums import (
    AuthScheme,
    CapabilityStatus,
    CapabilityVersionStatus,
    ConnectionStatus,
    DatabaseTargetStatus,
    EndpointStatus,
    InvocationStatus,
    OperationStatus,
    PrincipalKind,
    ProfileStatus,
    RiskLevel,
    Visibility,
    WorkspaceKind,
)

CAPABILITY_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
CONTEXT_KEY_PATTERN = re.compile(r"^(personal|agent:[A-Za-z0-9._-]+|deployment:[A-Za-z0-9._-]+)$")
# FEAT-04: PostgreSQL identifier (schema / view / column). Deliberately
# narrower than PG allows: unquoted-safe names only, so identifiers can be
# composed into a parameterized SELECT without any quoting ambiguity.
PG_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def _now() -> datetime:
    return datetime.now(UTC)


def new_id() -> uuid.UUID:
    return uuid.uuid4()


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


# ---------------------------------------------------------------------------
# Workspaces, principals, services
# ---------------------------------------------------------------------------


class Workspace(DomainModel):
    id: uuid.UUID = Field(default_factory=new_id)
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=128)
    kind: WorkspaceKind = WorkspaceKind.PERSONAL
    created_at: datetime = Field(default_factory=_now)


class Principal(DomainModel):
    """Local dev identity mapping (docs/06-DATABASE.md: minimal principal table).

    Production deployments resolve principals from an OIDC provider instead.
    ``token_hash`` stores a SHA-256 of a synthetic dev token; the raw token is
    never persisted or logged.
    """

    id: uuid.UUID = Field(default_factory=new_id)
    workspace_id: uuid.UUID
    kind: PrincipalKind = PrincipalKind.USER
    display_name: str = Field(min_length=1, max_length=128)
    token_hash: str = Field(min_length=64, max_length=64)
    token_hint: str = Field(default="", max_length=32)
    created_at: datetime = Field(default_factory=_now)


class PrincipalPublic(DomainModel):
    id: uuid.UUID
    kind: PrincipalKind
    display_name: str


class Service(DomainModel):
    id: uuid.UUID = Field(default_factory=new_id)
    workspace_id: uuid.UUID
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=128)
    visibility: Visibility = Visibility.PRIVATE
    owner_principal_id: uuid.UUID
    created_at: datetime = Field(default_factory=_now)


class Endpoint(DomainModel):
    id: uuid.UUID = Field(default_factory=new_id)
    workspace_id: uuid.UUID
    service_id: uuid.UUID
    environment: str = Field(min_length=1, max_length=32)
    base_url: str = Field(min_length=1, max_length=512)
    approved_hosts: list[str] = Field(default_factory=list)
    status: EndpointStatus = EndpointStatus.PENDING
    created_at: datetime = Field(default_factory=_now)


class DatabaseTarget(DomainModel):
    """FEAT-04: one approved read-only PostgreSQL target.

    An INDEPENDENT approval type from HTTP endpoints (docs/38 section 1):
    approving this row authorizes exactly (host, port, database, allowed_
    schemas) for discovery and fixed parameterized SELECT tools - it never
    authorizes HTTP egress, and an HTTP endpoint approval never authorizes
    a database connection. Credentials are NOT stored here: they follow the
    FEAT-03 keyring path on the Connection (auth_scheme=postgres).
    """

    id: uuid.UUID = Field(default_factory=new_id)
    workspace_id: uuid.UUID
    service_id: uuid.UUID
    engine: str = Field(default="postgres", pattern=r"^postgres$")
    host: str = Field(min_length=1, max_length=253, pattern=r"^[A-Za-z0-9._-]+$")
    port: int = Field(ge=1, le=65535)
    database: str = Field(min_length=1, max_length=63)
    allowed_schemas: list[str] = Field(min_length=1, max_length=16)
    status: DatabaseTargetStatus = DatabaseTargetStatus.PENDING
    created_by: uuid.UUID | None = None
    created_at: datetime = Field(default_factory=_now)

    @field_validator("database")
    @classmethod
    def _validate_database(cls, value: str) -> str:
        if not PG_IDENTIFIER_PATTERN.match(value):
            raise ValueError("database must be an unquoted PostgreSQL identifier")
        return value

    @field_validator("allowed_schemas")
    @classmethod
    def _validate_allowed_schemas(cls, value: list[str]) -> list[str]:
        if not value or len(set(value)) != len(value):
            raise ValueError("allowed_schemas must be a non-empty unique list")
        for schema in value:
            if not PG_IDENTIFIER_PATTERN.match(schema):
                raise ValueError(f"schema {schema!r} is not an unquoted PostgreSQL identifier")
        return value


# ---------------------------------------------------------------------------
# Discovery: observations, operations, operation versions
# ---------------------------------------------------------------------------


class Observation(DomainModel):
    id: uuid.UUID = Field(default_factory=new_id)
    workspace_id: uuid.UUID
    service_id: uuid.UUID
    source: str = Field(min_length=1, max_length=64)
    normalized_shape: dict[str, Any] = Field(default_factory=dict)
    redaction_version: str = "har-sanitizer/1"
    created_at: datetime = Field(default_factory=_now)
    expires_at: datetime | None = None


class Operation(DomainModel):
    id: uuid.UUID = Field(default_factory=new_id)
    workspace_id: uuid.UUID
    service_id: uuid.UUID
    method: str = Field(pattern=r"^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)$")
    normalized_path: str = Field(min_length=1, max_length=512)
    fingerprint: str = Field(min_length=16, max_length=64)
    status: OperationStatus = OperationStatus.OBSERVED
    created_at: datetime = Field(default_factory=_now)


class OperationVersion(DomainModel):
    id: uuid.UUID = Field(default_factory=new_id)
    operation_id: uuid.UUID
    version: int = Field(ge=1)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    contract_hash: str = Field(min_length=16, max_length=64)
    created_at: datetime = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


class Capability(DomainModel):
    id: uuid.UUID = Field(default_factory=new_id)
    workspace_id: uuid.UUID
    name: str = Field(pattern=CAPABILITY_NAME_PATTERN.pattern)
    display_name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2048)
    visibility: Visibility = Visibility.PRIVATE
    status: CapabilityStatus = CapabilityStatus.DRAFT
    created_at: datetime = Field(default_factory=_now)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not CAPABILITY_NAME_PATTERN.match(value):
            raise ValueError("capability name must look like 'namespace.operation'")
        return value


class CapabilityVersion(DomainModel):
    id: uuid.UUID = Field(default_factory=new_id)
    capability_id: uuid.UUID
    operation_version_id: uuid.UUID
    version: str = Field(pattern=SEMVER_PATTERN.pattern)
    risk_level: RiskLevel = RiskLevel.LOW
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    allow_fields: list[str] = Field(default_factory=list)
    policy_ref: str = Field(default="policy-v1", max_length=128)
    # request mapping: how validated arguments fill the reviewed operation
    execution: dict[str, Any] = Field(default_factory=dict)
    status: CapabilityVersionStatus = CapabilityVersionStatus.DRAFT
    published_at: datetime | None = None
    content_hash: str = Field(min_length=16, max_length=64)
    created_at: datetime = Field(default_factory=_now)

    @field_validator("version")
    @classmethod
    def _validate_semver(cls, value: str) -> str:
        if not SEMVER_PATTERN.match(value):
            raise ValueError("version must be semver like '1.0.0'")
        return value

    @model_validator(mode="after")
    def _published_is_immutable(self) -> CapabilityVersion:
        if self.status == CapabilityVersionStatus.PUBLISHED and self.published_at is None:
            raise ValueError("published versions must carry a published_at timestamp")
        return self


# ---------------------------------------------------------------------------
# Connections, profiles, active bindings
# ---------------------------------------------------------------------------


class Connection(DomainModel):
    """An authorization binding between a principal and a Service.

    Holds only an opaque ``secret_ref``; plaintext credentials live in the
    SecretProvider and are accessible solely to the trusted Executor.
    """

    id: uuid.UUID = Field(default_factory=new_id)
    workspace_id: uuid.UUID
    service_id: uuid.UUID
    owner_principal_id: uuid.UUID
    auth_scheme: AuthScheme = AuthScheme.API_KEY
    secret_ref: str = Field(min_length=1, max_length=512)
    scopes: list[str] = Field(default_factory=list)
    status: ConnectionStatus = ConnectionStatus.PENDING
    created_at: datetime = Field(default_factory=_now)


class ConnectionPublic(DomainModel):
    """Public projection of a Connection: no secret_ref, no auth material."""

    id: uuid.UUID
    service_id: uuid.UUID
    owner_principal_id: uuid.UUID
    auth_scheme: AuthScheme
    scopes: list[str]
    status: ConnectionStatus
    secret_configured: bool = True
    created_at: datetime

    @classmethod
    def from_connection(cls, connection: Connection) -> ConnectionPublic:
        return cls(
            id=connection.id,
            service_id=connection.service_id,
            owner_principal_id=connection.owner_principal_id,
            auth_scheme=connection.auth_scheme,
            scopes=connection.scopes,
            status=connection.status,
            secret_configured=bool(connection.secret_ref),
            created_at=connection.created_at,
        )


class Profile(DomainModel):
    """endpoint_id is optional for FEAT-04 database services: a DB profile
    binds a postgres Connection and draws its network authorization from the
    service's approved DatabaseTarget instead of an HTTP endpoint."""

    id: uuid.UUID = Field(default_factory=new_id)
    workspace_id: uuid.UUID
    service_id: uuid.UUID
    endpoint_id: uuid.UUID | None = None
    connection_id: uuid.UUID
    owner_principal_id: uuid.UUID
    name: str = Field(min_length=1, max_length=128)
    status: ProfileStatus = ProfileStatus.ENABLED
    created_at: datetime = Field(default_factory=_now)


class ProfilePublic(DomainModel):
    id: uuid.UUID
    service_id: uuid.UUID
    endpoint_id: uuid.UUID | None
    connection_id: uuid.UUID
    owner_principal_id: uuid.UUID
    name: str
    status: ProfileStatus
    created_at: datetime

    @classmethod
    def from_profile(cls, profile: Profile) -> ProfilePublic:
        return ProfilePublic(
            id=profile.id,
            service_id=profile.service_id,
            endpoint_id=profile.endpoint_id,
            connection_id=profile.connection_id,
            owner_principal_id=profile.owner_principal_id,
            name=profile.name,
            status=profile.status,
            created_at=profile.created_at,
        )


def validate_profile_alignment(
    profile: Profile,
    endpoint: Endpoint | None,
    connection: Connection,
    service: Service,
) -> None:
    """A Profile must combine an Endpoint (HTTP profiles) and a Connection
    of the same Service, all inside the same workspace (docs/04 section 4).

    FEAT-04: DB profiles carry no endpoint; the connection must then be a
    postgres connection (its network authorization comes from the service's
    approved DatabaseTarget, checked at plan-build time).
    """

    if profile.service_id != service.id:
        raise ValueError("profile references a different service")
    if endpoint is not None and endpoint.service_id != service.id:
        raise ValueError("endpoint belongs to a different service")
    if connection.service_id != service.id:
        raise ValueError("connection belongs to a different service")
    if profile.endpoint_id is not None:
        if endpoint is None:
            raise ValueError("profile references a missing endpoint")
        if profile.endpoint_id != endpoint.id:
            raise ValueError("profile references a different endpoint")
    elif connection.auth_scheme != AuthScheme.POSTGRES:
        raise ValueError("an endpoint-less profile requires a postgres connection")
    if profile.connection_id != connection.id:
        raise ValueError("profile references a different connection")
    if endpoint is not None and endpoint.workspace_id != profile.workspace_id:
        raise ValueError("endpoint workspace mismatch")
    if connection.workspace_id != profile.workspace_id:
        raise ValueError("connection workspace mismatch")


class ActiveBinding(DomainModel):
    """Current profile selection for (workspace, principal, service, context)."""

    workspace_id: uuid.UUID
    principal_id: uuid.UUID
    service_id: uuid.UUID
    context_key: str = Field(pattern=CONTEXT_KEY_PATTERN.pattern)
    active_profile_id: uuid.UUID
    version: int = Field(ge=1)
    updated_at: datetime = Field(default_factory=_now)
    updated_by: uuid.UUID

    @field_validator("context_key")
    @classmethod
    def _validate_context_key(cls, value: str) -> str:
        if not CONTEXT_KEY_PATTERN.match(value):
            raise ValueError(
                "context_key must be 'personal', 'agent:<id>' or 'deployment:<id>'"
            )
        return value


class AgentGrant(DomainModel):
    id: uuid.UUID = Field(default_factory=new_id)
    workspace_id: uuid.UUID
    agent_principal_id: uuid.UUID
    capability_id: uuid.UUID
    scopes: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None
    created_at: datetime = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Invocations and audit
# ---------------------------------------------------------------------------


class InvocationPlanOperation(DomainModel):
    method: str
    path_template: str
    # placeholder -> argument name, e.g. {"id": "customer_id"}; unmapped
    # placeholders use their own name
    path_map: dict[str, str] = Field(default_factory=dict)
    query_template: dict[str, str] = Field(default_factory=dict)
    body_template: dict[str, str] = Field(default_factory=dict)


class InvocationPlanDbQuery(DomainModel):
    """FEAT-04: the frozen, structured description of one read-only DB tool.

    Deliberately carries NO SQL text: the executor composes the statement
    itself from validated identifiers (psycopg ``sql.Identifier``), so no
    caller - and no compromise of the control plane's signature of a
    hand-written string - can ever smuggle arbitrary SQL, a write, or a
    multi-statement payload. The plan only says: these columns, from this
    schema-qualified view, filtered by these named parameters, capped at
    ``fetch_limit`` rows and ``statement_timeout_ms``.
    """

    host: str = Field(min_length=1, max_length=253)
    port: int = Field(ge=1, le=65535)
    database: str = Field(min_length=1, max_length=63)
    allowed_schemas: list[str] = Field(min_length=1, max_length=16)
    # named schema_name: "schema" collides with BaseModel's deprecated
    # .schema() method and breaks static typing
    schema_name: str = Field(min_length=1, max_length=63)
    view: str = Field(min_length=1, max_length=63)
    columns: list[str] = Field(min_length=1, max_length=64)
    # placeholder name -> argument name, mirroring query_template
    param_map: dict[str, str] = Field(default_factory=dict)
    fetch_limit: int = Field(default=2, ge=1, le=2)
    statement_timeout_ms: int = Field(default=5000, ge=100, le=30_000)

    @field_validator("database", "schema_name", "view")
    @classmethod
    def _validate_identifiers(cls, value: str) -> str:
        if not PG_IDENTIFIER_PATTERN.match(value):
            raise ValueError("database/schema/view must be unquoted PostgreSQL identifiers")
        return value

    @field_validator("columns")
    @classmethod
    def _validate_columns(cls, value: list[str]) -> list[str]:
        if not value or len(set(value)) != len(value):
            raise ValueError("columns must be a non-empty unique list")
        for column in value:
            if not PG_IDENTIFIER_PATTERN.match(column):
                raise ValueError(f"column {column!r} is not an unquoted PostgreSQL identifier")
        return value

    @field_validator("param_map")
    @classmethod
    def _validate_param_map(cls, value: dict[str, str]) -> dict[str, str]:
        for placeholder, argument in value.items():
            if not PG_IDENTIFIER_PATTERN.match(placeholder):
                raise ValueError(f"parameter {placeholder!r} is not a valid placeholder")
            if not argument or len(argument) > 128:
                raise ValueError("argument names must be 1-128 characters")
        return value


class InvocationPlan(DomainModel):
    """Structured, pre-approved call description handed to the Executor.

    Server-generated only: callers can never construct this directly. The
    control plane signs it (HMAC in the dev deployment, workload identity in
    production) and the executor re-verifies before executing.

    FEAT-04: when ``db_query`` is set the plan executes against the approved
    DatabaseTarget it froze (host/port/database/allowed_schemas) through the
    dedicated DB adapter - ``endpoint_base_url`` is then empty and the HTTP
    adapter/network path is skipped entirely.
    """

    invocation_id: uuid.UUID
    capability_name: str
    capability_version: str
    capability_version_id: uuid.UUID
    operation: InvocationPlanOperation
    endpoint_base_url: str
    approved_hosts: list[str]
    auth_scheme: AuthScheme
    secret_ref: str
    allow_fields: list[str]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    arguments: dict[str, Any]
    workspace_id: uuid.UUID
    principal_id: uuid.UUID
    agent_id: uuid.UUID | None = None
    risk_level: RiskLevel = RiskLevel.LOW
    policy_version: str = "policy-v1"
    arguments_digest: str
    idempotency_key: str | None = None
    deadline_epoch_s: float
    nonce: str = Field(min_length=16, max_length=64)
    db_query: InvocationPlanDbQuery | None = None
    # display-only metadata carried in the frozen snapshot; ignored by execution
    profile_label: str | None = None


class Invocation(DomainModel):
    id: uuid.UUID = Field(default_factory=new_id)
    workspace_id: uuid.UUID
    capability_version_id: uuid.UUID
    capability_name: str
    capability_version: str
    principal_id: uuid.UUID
    agent_id: uuid.UUID | None = None
    profile_id: uuid.UUID
    profile_snapshot: dict[str, Any]
    arguments_digest: str
    idempotency_key: str | None = None
    policy_version: str = "policy-v1"
    status: InvocationStatus = InvocationStatus.PENDING
    result: dict[str, Any] | None = None
    error_code: str | None = None
    created_at: datetime = Field(default_factory=_now)
    finished_at: datetime | None = None


class AuditEvent(DomainModel):
    id: uuid.UUID = Field(default_factory=new_id)
    workspace_id: uuid.UUID
    invocation_id: uuid.UUID | None = None
    actor_id: uuid.UUID
    action: str = Field(min_length=1, max_length=128)
    decision: str = Field(min_length=1, max_length=64)
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Approval (high-risk operations)
# ---------------------------------------------------------------------------


class Approval(DomainModel):
    id: uuid.UUID = Field(default_factory=new_id)
    workspace_id: uuid.UUID
    invocation_id: uuid.UUID
    approver_id: uuid.UUID
    arguments_digest: str
    status: str = Field(default="pending", pattern=r"^(pending|approved|rejected|consumed)$")
    created_at: datetime = Field(default_factory=_now)
    decided_at: datetime | None = None
