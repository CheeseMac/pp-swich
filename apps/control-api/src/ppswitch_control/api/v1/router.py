"""Control plane REST API v1 (docs/07-API-SPEC.md).

Every route is workspace-scoped through the verified principal; identity
never comes from payloads. Connections expose metadata only - secret_ref
and plaintext credentials are never serialized (SEC-003 / docs/06 section 6).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from ppswitch_core.errors import ApiError, ErrorCode
from ppswitch_db.models import (
    ActiveBindingRow,
    AgentGrantRow,
    ApprovalRow,
    AuditEventRow,
    CapabilityRow,
    CapabilityVersionRow,
    CollectorPairingRow,
    ConnectionRow,
    EndpointRow,
    InvocationRow,
    ObservationRow,
    OperationRow,
    OperationVersionRow,
    PrincipalRow,
    ProfileRow,
    ServiceRow,
    WorkspaceRow,
)
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ppswitch_control.auth import (
    AuthContext,
    _extract_bearer,
    get_session,
    require_permission,
)
from ppswitch_control.envelope import ok
from ppswitch_control.services.agent_principal_service import (
    create_agent_principal,
    list_agent_principals,
)
from ppswitch_control.services.capability_service import (
    create_capability,
    publish_capability,
    review_capability,
    verify_capability,
)
from ppswitch_control.services.collector_service import (
    create_pairing,
    exchange_pairing_code,
    list_pairings,
    submit_observations,
    verify_collector_token,
)
from ppswitch_control.services.connection_service import (
    activate_binding,
    create_connection,
    create_profile,
)
from ppswitch_control.services.delegation_service import _active_delegation_exists
from ppswitch_control.services.discovery_service import import_discovery_document
from ppswitch_control.services.invocation_service import invoke_capability
from ppswitch_control.services.operation_test_service import test_operation

router = APIRouter(prefix="/api/v1")


def _safe_yaml_load(content: bytes) -> dict:
    """R-14: YAML fallback for discovery imports (safe subset, dict only)."""
    import yaml

    document = yaml.safe_load(content)
    if not isinstance(document, dict):
        raise ValueError("YAML document is not a mapping")
    return document


async def read_upload_bounded(file, limit: int, chunk: int = 64 * 1024) -> bytes:
    """SEC-038 (R-08): stream an upload in bounded chunks.

    Returns the full content only when it fits ``limit``; raises
    PAYLOAD_TOO_LARGE as soon as the running total exceeds the limit, so
    the memory held never exceeds ``limit + chunk`` bytes regardless of
    how large the request body is.
    """
    parts: list[bytes] = []
    total = 0
    while True:
        block = await file.read(chunk)
        if not block:
            break
        total += len(block)
        if total > limit:
            raise ApiError(
                ErrorCode.PAYLOAD_TOO_LARGE, "Import file exceeds the size limit"
            )
        parts.append(block)
    return b"".join(parts)


def _is_admin(auth: AuthContext) -> bool:
    return auth.principal.kind == "user" and (auth.principal.role or "member") == "admin"


def _resource_visible(visibility: str, owner_id, auth: AuthContext) -> bool:
    """R-11 / SEC-042: private resources are owner-or-admin only.

    ``team`` and ``organization`` stay visible across the workspace (the
    workspace boundary itself is enforced by every query).
    """
    if visibility != "private":
        return True
    return owner_id == auth.principal_id or _is_admin(auth)


# ---------------------------------------------------------------------------
# request / response models
# ---------------------------------------------------------------------------


class WorkspaceCreate(BaseModel):
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=128)
    kind: str = "personal"


class ServiceCreate(BaseModel):
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=128)
    visibility: str = "private"


class EndpointCreate(BaseModel):
    environment: str = Field(min_length=1, max_length=32)
    base_url: str = Field(min_length=1, max_length=512)
    approved_hosts: list[str] = Field(default_factory=list)
    status: str = "pending"  # demo seeds may create approved endpoints directly


class CapabilityCreate(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
    display_name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2048)
    operation_id: UUID
    input_schema: dict = Field(default_factory=dict)
    output_schema: dict = Field(default_factory=dict)
    allow_fields: list[str] = Field(default_factory=list)
    risk_level: str = "low"
    execution: dict = Field(default_factory=dict)
    # review3 SEC-044: scopes this capability requires at execution time
    required_scopes: list[str] = Field(default_factory=list)


class ReviewRequest(BaseModel):
    evidence: str = Field(min_length=1, max_length=1024)


class PublishRequest(BaseModel):
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")


class ConnectionCreate(BaseModel):
    service_id: UUID
    auth_scheme: str = "api_key"
    scopes: list[str] = Field(default_factory=list)
    demo_dataset: str | None = None  # dev-only: which mock dataset this connection maps to


class ProfileCreate(BaseModel):
    service_id: UUID
    endpoint_id: UUID
    connection_id: UUID
    name: str = Field(min_length=1, max_length=128)


class BindingPut(BaseModel):
    profile_id: UUID
    expected_version: int = Field(ge=0)


class InvocationCreate(BaseModel):
    capability: str = Field(min_length=1, max_length=128)
    version: str | None = None
    context_key: str = Field(default="personal",
        pattern=r"^(personal|agent:[A-Za-z0-9._-]+|deployment:[A-Za-z0-9._-]+)$")
    arguments: dict = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=128)
    approval_request_id: UUID | None = None
    # SEC-046: signed, short-lived acting-identity ticket issued by the
    # delegating user; required to disambiguate a shared agent's delegations
    delegation_context: str | None = Field(default=None, max_length=2048)


class AgentGrantCreate(BaseModel):
    agent_principal_id: UUID
    capability_id: UUID
    scopes: list[str] = Field(default_factory=list)


class DelegationCreate(BaseModel):
    agent_principal_id: UUID
    scopes: list[str] = Field(default_factory=list)
    expires_at: str | None = None  # ISO-8601; None = no expiry


class OperationTestRequest(BaseModel):
    """FEAT-02 wizard: sample arguments for a live operation test."""

    arguments: dict = Field(default_factory=dict)


class AgentPrincipalCreate(BaseModel):
    """FEAT-05: onboard an external agent (Claude/Cursor/custom)."""

    display_name: str = Field(min_length=1, max_length=128)


class CollectorPairRequest(BaseModel):
    pairing_code: str = Field(min_length=6, max_length=16)


class CollectorObservations(BaseModel):
    """FEAT-01: zero-value observation skeletons from the extension."""

    observations: list[dict] = Field(min_length=1, max_length=1000)


# ---------------------------------------------------------------------------
# workspaces / services / endpoints
# ---------------------------------------------------------------------------


@router.post("/workspaces")
def create_workspace(
    payload: WorkspaceCreate,
    request: Request,
    auth: AuthContext = Depends(require_permission("workspace.create")),
    session: Session = Depends(get_session),
):
    if payload.kind not in ("personal", "organization"):
        raise ApiError(ErrorCode.BAD_REQUEST, "kind must be personal or organization")
    duplicate = session.scalars(
        select(WorkspaceRow).where(WorkspaceRow.slug == payload.slug)
    ).one_or_none()
    if duplicate is not None:
        raise ApiError(ErrorCode.CONFLICT, "Workspace slug already exists")
    workspace = WorkspaceRow(
        id=__import__("uuid").uuid4(), slug=payload.slug, name=payload.name, kind=payload.kind
    )
    session.add(workspace)
    session.flush()
    return ok(request, {"id": str(workspace.id), "slug": workspace.slug}, 201)


@router.get("/services")
def list_services(
    request: Request,
    auth: AuthContext = Depends(require_permission("services.read")),
    session: Session = Depends(get_session),
):
    rows = session.scalars(
        select(ServiceRow)
        .where(ServiceRow.workspace_id == auth.workspace_id)
        .order_by(ServiceRow.slug)
    ).all()
    # SEC-042: private services are invisible to members without ownership
    rows = [r for r in rows if _resource_visible(r.visibility, r.owner_principal_id, auth)]
    return ok(
        request,
        [
            {
                "id": str(r.id), "slug": r.slug, "name": r.name,
                "visibility": r.visibility, "status": "ok",
            }
            for r in rows
        ],
    )


@router.post("/services")
def create_service(
    payload: ServiceCreate,
    request: Request,
    auth: AuthContext = Depends(require_permission("services.write")),
    session: Session = Depends(get_session),
):
    if payload.visibility not in ("private", "team", "organization"):
        raise ApiError(ErrorCode.BAD_REQUEST, "invalid visibility")
    duplicate = session.scalars(
        select(ServiceRow).where(
            ServiceRow.workspace_id == auth.workspace_id, ServiceRow.slug == payload.slug
        )
    ).one_or_none()
    if duplicate is not None:
        raise ApiError(ErrorCode.CONFLICT, "Service slug already exists")
    service = ServiceRow(
        id=__import__("uuid").uuid4(),
        workspace_id=auth.workspace_id,
        slug=payload.slug,
        name=payload.name,
        visibility=payload.visibility,
        owner_principal_id=auth.principal_id,
    )
    session.add(service)
    session.flush()
    return ok(request, {"id": str(service.id), "slug": service.slug}, 201)


@router.post("/services/{service_id}/endpoints")
def create_endpoint(
    service_id: UUID,
    payload: EndpointCreate,
    request: Request,
    auth: AuthContext = Depends(require_permission("endpoints.write")),
    session: Session = Depends(get_session),
):
    service = session.get(ServiceRow, service_id)
    if service is None or service.workspace_id != auth.workspace_id:
        raise ApiError(ErrorCode.SERVICE_NOT_FOUND, "Service not found in this workspace")
    if payload.status not in ("pending", "approved"):
        raise ApiError(ErrorCode.BAD_REQUEST, "status must be pending or approved")
    # review PR-SEC-01: outside demo mode a creator cannot self-approve
    # an endpoint; use POST /endpoints/{id}/approve as another principal
    if payload.status == "approved" and not request.app.state.settings.demo_mode:
        raise ApiError(
            ErrorCode.ENDPOINT_NOT_APPROVABLE,
            "Endpoint approval requires the approve action by another principal",
        )
    settings_ = request.app.state.settings
    allowed_schemes: tuple[str, ...] = ("https://",)
    if settings_.demo_mode or settings_.allow_plain_http:
        # in-process demo transport / explicitly approved plaintext network
        allowed_schemes = ("http://", "https://")
        if settings_.demo_mode:
            allowed_schemes = (*allowed_schemes, "mock://")
    if not payload.base_url.startswith(allowed_schemes):
        raise ApiError(
            ErrorCode.BAD_REQUEST,
            "base_url must be https (plain http requires explicit operator opt-in)",
        )
    endpoint = EndpointRow(
        id=__import__("uuid").uuid4(),
        workspace_id=auth.workspace_id,
        service_id=service_id,
        environment=payload.environment,
        base_url=payload.base_url.rstrip("/"),
        approved_hosts=[h.lower() for h in payload.approved_hosts],
        created_by=auth.principal_id,
        status=payload.status,
    )
    session.add(endpoint)
    session.flush()
    session.add(
        AuditEventRow(
            workspace_id=auth.workspace_id, actor_id=auth.principal_id,
            action="endpoint.create", decision="allowed",
            redacted_metadata={"service_id": str(service_id), "status": endpoint.status},
        )
    )
    return ok(
        request,
        {
            "id": str(endpoint.id), "environment": endpoint.environment,
            "base_url": endpoint.base_url, "status": endpoint.status,
        },
        201,
    )


@router.post("/endpoints/{endpoint_id}/approve")
def approve_endpoint(
    endpoint_id: UUID,
    request: Request,
    auth: AuthContext = Depends(require_permission("endpoints.approve")),
    session: Session = Depends(get_session),
):
    """Approve a pending endpoint (PR-SEC-01).

    Human principals only. Outside demo mode the approver must differ from
    the endpoint creator - self-approval of arbitrary targets is rejected.
    """
    endpoint = session.get(EndpointRow, endpoint_id)
    if endpoint is None or endpoint.workspace_id != auth.workspace_id:
        raise ApiError(ErrorCode.ENDPOINT_NOT_FOUND, "Endpoint not found")
    if endpoint.status != "pending":
        raise ApiError(ErrorCode.CONFLICT, f"Endpoint already {endpoint.status}")
    demo = request.app.state.settings.demo_mode
    if not demo and endpoint.created_by is not None and endpoint.created_by == auth.principal_id:
        raise ApiError(
            ErrorCode.ENDPOINT_NOT_APPROVABLE,
            "Endpoint approval requires a principal other than the creator",
        )
    endpoint.status = "approved"
    session.add(
        AuditEventRow(
            workspace_id=auth.workspace_id, actor_id=auth.principal_id,
            action="endpoint.approve", decision="allowed",
            redacted_metadata={"endpoint_id": str(endpoint_id)},
        )
    )
    return ok(request, {"id": str(endpoint.id), "status": endpoint.status})


@router.get("/endpoints")
def list_endpoints(
    request: Request,
    service_id: UUID | None = None,
    auth: AuthContext = Depends(require_permission("endpoints.read")),
    session: Session = Depends(get_session),
):
    stmt = select(EndpointRow).where(EndpointRow.workspace_id == auth.workspace_id)
    if service_id:
        stmt = stmt.where(EndpointRow.service_id == service_id)
    rows = session.scalars(stmt.order_by(EndpointRow.environment)).all()
    return ok(
        request,
        [
            {
                "id": str(r.id), "service_id": str(r.service_id),
                "environment": r.environment, "base_url": r.base_url,
                "approved_hosts": r.approved_hosts, "status": r.status,
            }
            for r in rows
        ],
    )


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------


@router.post("/discoveries/imports")
async def import_discovery(
    request: Request,
    file: UploadFile = File(...),
    source_type: str = Form(...),
    service_id: UUID = Form(...),
    auth: AuthContext = Depends(require_permission("discovery.import")),
    session: Session = Depends(get_session),
):
    settings = request.app.state.settings
    # R-08 / SEC-038: stream the upload in bounded chunks; an oversized file
    # is aborted as soon as the limit is exceeded, never read fully first
    content = await read_upload_bounded(file, settings.max_import_bytes)
    try:
        document = json.loads(content)
    except (ValueError, TypeError):
        # R-14: OpenAPI documents are commonly YAML; safe_load refuses to
        # construct arbitrary python objects (no unsafe tags are honored)
        try:
            document = _safe_yaml_load(content)
        except ValueError as exc:
            raise ApiError(
                ErrorCode.BAD_REQUEST, "Import file is not valid JSON or YAML"
            ) from exc
    summary = import_discovery_document(
        session,
        workspace_id=auth.workspace_id,
        principal_id=auth.principal_id,
        service_id=service_id,
        source_type=source_type,
        document=document,
    )
    return ok(
        request,
        {
            "job_id": summary.import_id,
            "state": "completed",
            "candidates": summary.candidates,
            "operations_created": summary.operations_created,
            "operations_updated": summary.operations_updated,
            "observations_created": summary.observations_created,
        },
        201,
    )


# ---------------------------------------------------------------------------
# browser collector (FEAT-01): console issues one-time pairing codes, the
# extension exchanges them for a scoped short-lived token, and can then
# submit sanitized zero-value observations - drafts only, never published
# ---------------------------------------------------------------------------


def require_collector(
    request: Request, session: Session = Depends(get_session)
) -> CollectorPairingRow:
    """Authenticate the extension with its OWN token (never a user token)."""
    token = _extract_bearer(request)
    return verify_collector_token(session, token=token, now=datetime.now(UTC))


@router.post("/services/{service_id}/collector/pairings")
def create_collector_pairing(
    service_id: UUID,
    request: Request,
    auth: AuthContext = Depends(require_permission("discovery.import")),
    session: Session = Depends(get_session),
):
    settings = request.app.state.settings
    view, _code = create_pairing(
        session,
        workspace_id=auth.workspace_id,
        principal_id=auth.principal_id,
        service_id=service_id,
        now=datetime.now(UTC),
        pairing_ttl_seconds=settings.collector_pairing_ttl_seconds,
        token_ttl_seconds=settings.collector_token_ttl_seconds,
    )
    return ok(request, view, 201)


@router.get("/services/{service_id}/collector/pairings")
def list_collector_pairings(
    service_id: UUID,
    request: Request,
    auth: AuthContext = Depends(require_permission("operations.read")),
    session: Session = Depends(get_session),
):
    pairings = list_pairings(session, workspace_id=auth.workspace_id, service_id=service_id)
    return ok(request, pairings)


@router.post("/collector/pair")
def pair_collector(
    payload: CollectorPairRequest,
    request: Request,
    session: Session = Depends(get_session),
):
    """Exchange a one-time console pairing code for a collector token."""
    settings = request.app.state.settings
    result = exchange_pairing_code(
        session,
        pairing_code=payload.pairing_code,
        now=datetime.now(UTC),
        token_ttl_seconds=settings.collector_token_ttl_seconds,
    )
    return ok(request, result)


@router.post("/collector/observations")
def submit_collector_observations(
    payload: CollectorObservations,
    request: Request,
    pairing: CollectorPairingRow = Depends(require_collector),
    session: Session = Depends(get_session),
):
    settings = request.app.state.settings
    summary = submit_observations(
        session,
        pairing=pairing,
        document=payload.model_dump(),
        max_observations=settings.collector_max_observations,
        now=datetime.now(UTC),
    )
    return ok(
        request,
        {
            "job_id": summary.import_id,
            "state": "completed",
            "candidates": summary.candidates,
            "operations_created": summary.operations_created,
            "operations_updated": summary.operations_updated,
            "observations_created": summary.observations_created,
        },
        201,
    )


@router.get("/observations")
def list_observations(
    request: Request,
    service_id: UUID | None = None,
    auth: AuthContext = Depends(require_permission("operations.read")),
    session: Session = Depends(get_session),
):
    """Recent discovery observations (source breakdown for the console)."""
    stmt = select(ObservationRow).where(ObservationRow.workspace_id == auth.workspace_id)
    if service_id:
        stmt = stmt.where(ObservationRow.service_id == service_id)
    rows = session.scalars(
        stmt.order_by(ObservationRow.created_at.desc()).limit(50)
    ).all()
    return ok(
        request,
        [
            {
                "id": str(r.id),
                "service_id": str(r.service_id),
                "source": r.source,
                "normalized_shape": r.normalized_shape,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    )


@router.get("/operations")
def list_operations(    request: Request,
    service_id: UUID | None = None,
    status: str | None = None,
    auth: AuthContext = Depends(require_permission("operations.read")),
    session: Session = Depends(get_session),
):
    stmt = select(OperationRow).where(OperationRow.workspace_id == auth.workspace_id)
    if service_id:
        stmt = stmt.where(OperationRow.service_id == service_id)
    if status:
        stmt = stmt.where(OperationRow.status == status)
    rows = session.scalars(stmt.order_by(OperationRow.created_at.desc())).all()
    return ok(
        request,
        [
            {
                "id": str(r.id), "service_id": str(r.service_id),
                "method": r.method, "normalized_path": r.normalized_path,
                "status": r.status,
            }
            for r in rows
        ],
    )


@router.get("/operations/{operation_id}")
def get_operation(
    operation_id: UUID,
    request: Request,
    auth: AuthContext = Depends(require_permission("operations.read")),
    session: Session = Depends(get_session),
):
    operation = session.get(OperationRow, operation_id)
    if operation is None or operation.workspace_id != auth.workspace_id:
        raise ApiError(ErrorCode.OPERATION_NOT_FOUND, "Operation not found")
    versions = session.scalars(
        select(OperationVersionRow)
        .where(OperationVersionRow.operation_id == operation_id)
        .order_by(OperationVersionRow.version)
    ).all()
    return ok(
        request,
        {
            "id": str(operation.id),
            "method": operation.method,
            "normalized_path": operation.normalized_path,
            "status": operation.status,
            "versions": [
                {
                    "id": str(v.id), "version": v.version,
                    "input_schema": v.input_schema, "output_schema": v.output_schema,
                }
                for v in versions
            ],
        },
    )


@router.post("/operations/{operation_id}/test")
def test_operation_route(
    operation_id: UUID,
    payload: OperationTestRequest,
    request: Request,
    auth: AuthContext = Depends(require_permission("operations.test")),
    session: Session = Depends(get_session),
):
    """Live test call through the caller's active profile (FEAT-02).

    Human-only. Arguments are whitelisted against the operation's
    observed contract; the request runs through the same trusted
    executor path (approved endpoint, network policy, response guard)
    as a real invocation but creates no invocation record.
    """
    settings = request.app.state.settings
    executor_client: httpx.Client = request.app.state.executor_client
    result = test_operation(
        session,
        workspace_id=auth.workspace_id,
        principal_id=auth.principal_id,
        operation_id=operation_id,
        arguments=payload.arguments,
        settings=settings,
        executor_client=executor_client,
    )
    return ok(request, result)


@router.post("/operations/{operation_id}/verify")
def verify_operation(
    operation_id: UUID,
    payload: ReviewRequest,
    request: Request,
    auth: AuthContext = Depends(require_permission("operations.verify")),
    session: Session = Depends(get_session),
):
    operation = session.get(OperationRow, operation_id)
    if operation is None or operation.workspace_id != auth.workspace_id:
        raise ApiError(ErrorCode.OPERATION_NOT_FOUND, "Operation not found")
    if operation.status in ("rejected", "verified"):
        raise ApiError(ErrorCode.CONFLICT, f"Operation already {operation.status}")
    operation.status = "verified"
    session.add(
        AuditEventRow(
            workspace_id=auth.workspace_id, actor_id=auth.principal_id,
            action="operation.verify", decision="allowed",
            redacted_metadata={"operation_id": str(operation_id), "note": payload.evidence[:256]},
        )
    )
    return ok(request, {"id": str(operation.id), "status": operation.status})


# ---------------------------------------------------------------------------
# capabilities
# ---------------------------------------------------------------------------


def _capability_view(
    row: CapabilityRow, versions: list[CapabilityVersionRow] | Sequence[CapabilityVersionRow]
) -> dict:
    """Management view: every version incl. drafts and internal mappings."""
    return {
        "id": str(row.id),
        "name": row.name,
        "display_name": row.display_name,
        "description": row.description,
        "status": row.status,
        "versions": [
            {
                "id": str(v.id),
                "version": v.version,
                "risk_level": v.risk_level,
                "status": v.status,
                "input_schema": v.input_schema,
                "output_schema": v.output_schema,
                "allow_fields": v.allow_fields,
                "execution": v.execution,
            }
            for v in versions
        ],
    }


def _public_tool_view(row: CapabilityRow, versions) -> dict:
    """SEC-045: what an AGENT may see - published versions only, no draft
    data, no internal execution mapping, no allow-lists or output guard
    configuration. Name, description, public input schema, version and a
    risk hint; nothing else."""
    published = [v for v in versions if v.status == "published"]
    return {
        "id": str(row.id),
        "name": row.name,
        "display_name": row.display_name,
        "description": row.description,
        "status": row.status,
        "versions": [
            {
                "version": v.version,
                "risk_level": v.risk_level,
                "status": v.status,
                "input_schema": v.input_schema,
            }
            for v in published
        ],
    }


def _agent_granted_ids(session: Session, auth: AuthContext) -> set:
    """Capabilities granted to this agent via a NON-EXPIRED grant (SEC-045)."""
    from ppswitch_control.services.invocation_service import _epoch, epoch_now

    now = epoch_now()
    return {
        grant.capability_id
        for grant in session.scalars(
            select(AgentGrantRow).where(
                AgentGrantRow.workspace_id == auth.workspace_id,
                AgentGrantRow.agent_principal_id == auth.principal_id,
            )
        ).all()
        if grant.expires_at is None or _epoch(grant.expires_at) >= now
    }


@router.post("/capabilities")
def create_capability_route(
    payload: CapabilityCreate,
    request: Request,
    auth: AuthContext = Depends(require_permission("capabilities.create")),
    session: Session = Depends(get_session),
):
    if payload.risk_level not in ("low", "medium", "high", "critical"):
        raise ApiError(ErrorCode.BAD_REQUEST, "invalid risk_level")
    capability = create_capability(
        session,
        workspace_id=auth.workspace_id,
        principal_id=auth.principal_id,
        name=payload.name,
        display_name=payload.display_name,
        description=payload.description,
        operation_id=payload.operation_id,
        input_schema=payload.input_schema,
        output_schema=payload.output_schema,
        allow_fields=payload.allow_fields,
        risk_level=payload.risk_level,
        execution=payload.execution,
        required_scopes=payload.required_scopes,
    )
    return ok(request, {"id": str(capability.id), "status": capability.status}, 201)


@router.get("/capabilities")
def list_capabilities(
    request: Request,
    status: str | None = None,
    auth: AuthContext = Depends(require_permission("capabilities.read")),
    session: Session = Depends(get_session),
):
    stmt = select(CapabilityRow).where(CapabilityRow.workspace_id == auth.workspace_id)
    if status:
        stmt = stmt.where(CapabilityRow.status == status)
    rows = session.scalars(stmt.order_by(CapabilityRow.name)).all()
    is_agent = auth.principal.kind == "agent"
    if is_agent:
        # review Q5 + SEC-032: an agent's tool directory contains only
        # capabilities granted via a NON-EXPIRED grant, in their PUBLISHED
        # form; SEC-045: serialized through the public tool view
        granted_ids = _agent_granted_ids(session, auth)
        rows = [
            row for row in rows
            if row.id in granted_ids and row.status == "published"
        ]
    else:
        # SEC-042: private capabilities are invisible to members without
        # ownership (agents were already filtered above)
        rows = [
            row for row in rows
            if _resource_visible(row.visibility, row.created_by, auth)
        ]
    result = []
    for row in rows:
        versions = session.scalars(
            select(CapabilityVersionRow)
            .where(CapabilityVersionRow.capability_id == row.id)
            .order_by(CapabilityVersionRow.created_at)
        ).all()
        result.append(
            _public_tool_view(row, versions) if is_agent else _capability_view(row, versions)
        )
    return ok(request, result)


@router.get("/capabilities/{capability_id}")
def get_capability(
    capability_id: UUID,
    request: Request,
    auth: AuthContext = Depends(require_permission("capabilities.read")),
    session: Session = Depends(get_session),
):
    row = session.get(CapabilityRow, capability_id)
    if row is None or row.workspace_id != auth.workspace_id:
        raise ApiError(ErrorCode.CAPABILITY_NOT_FOUND, "Capability not found")
    # SEC-032 / SEC-042: agents see only granted+published; private
    # capabilities require ownership or the admin role (existence hidden)
    is_agent = auth.principal.kind == "agent"
    if is_agent:
        if capability_id not in _agent_granted_ids(session, auth) or row.status != "published":
            raise ApiError(ErrorCode.CAPABILITY_NOT_FOUND, "Capability not found")
    elif not _resource_visible(row.visibility, row.created_by, auth):
        raise ApiError(ErrorCode.CAPABILITY_NOT_FOUND, "Capability not found")
    stmt = (
        select(CapabilityVersionRow)
        .where(CapabilityVersionRow.capability_id == row.id)
        .order_by(CapabilityVersionRow.created_at)
    )
    if is_agent:
        stmt = stmt.where(CapabilityVersionRow.status == "published")
    versions = session.scalars(stmt).all()
    return ok(
        request,
        _public_tool_view(row, versions) if is_agent else _capability_view(row, versions),
    )


@router.post("/capabilities/{capability_id}/review")
def review_capability_route(
    capability_id: UUID,
    payload: ReviewRequest,
    request: Request,
    auth: AuthContext = Depends(require_permission("capabilities.review")),
    session: Session = Depends(get_session),
):
    row = review_capability(
        session, workspace_id=auth.workspace_id,
        principal_id=auth.principal_id, capability_id=capability_id,
        evidence=payload.evidence,
    )
    return ok(request, {"id": str(row.id), "status": row.status})


@router.post("/capabilities/{capability_id}/verify")
def verify_capability_route(
    capability_id: UUID,
    payload: ReviewRequest,
    request: Request,
    auth: AuthContext = Depends(require_permission("capabilities.review")),
    session: Session = Depends(get_session),
):
    row = verify_capability(
        session, workspace_id=auth.workspace_id,
        principal_id=auth.principal_id, capability_id=capability_id,
        evidence=payload.evidence,
    )
    return ok(request, {"id": str(row.id), "status": row.status})


@router.post("/capabilities/{capability_id}/publish")
def publish_capability_route(
    capability_id: UUID,
    payload: PublishRequest,
    request: Request,
    auth: AuthContext = Depends(require_permission("capabilities.publish")),
    session: Session = Depends(get_session),
):
    published = publish_capability(
        session, workspace_id=auth.workspace_id,
        principal_id=auth.principal_id, capability_id=capability_id,
        version=payload.version,
    )
    return ok(
        request,
        {"id": str(published.id), "version": published.version, "status": published.status},
    )


# ---------------------------------------------------------------------------
# connections / profiles / active bindings
# ---------------------------------------------------------------------------


@router.post("/connections")
def create_connection_route(
    payload: ConnectionCreate,
    request: Request,
    auth: AuthContext = Depends(require_permission("connections.write")),
    session: Session = Depends(get_session),
):
    settings = request.app.state.settings
    connection = create_connection(
        session,
        workspace_id=auth.workspace_id,
        principal_id=auth.principal_id,
        service_id=payload.service_id,
        auth_scheme=payload.auth_scheme,
        scopes=payload.scopes,
        demo_dataset=payload.demo_dataset,
        settings=settings,
    )
    # public projection only: never secret_ref
    return ok(
        request,
        {
            "id": str(connection.id),
            "service_id": str(connection.service_id),
            "auth_scheme": connection.auth_scheme,
            "status": connection.status,
            "secret_configured": bool(connection.secret_ref),
        },
        201,
    )


@router.get("/connections")
def list_connections(
    request: Request,
    auth: AuthContext = Depends(require_permission("connections.read")),
    session: Session = Depends(get_session),
):
    rows = session.scalars(
        select(ConnectionRow).where(ConnectionRow.workspace_id == auth.workspace_id)
    ).all()
    return ok(
        request,
        [
            {
                "id": str(r.id),
                "service_id": str(r.service_id),
                "auth_scheme": r.auth_scheme,
                "scopes": r.scopes,
                "status": r.status,
                "secret_configured": bool(r.secret_ref),
            }
            for r in rows
        ],
    )


@router.post("/profiles")
def create_profile_route(
    payload: ProfileCreate,
    request: Request,
    auth: AuthContext = Depends(require_permission("profiles.write")),
    session: Session = Depends(get_session),
):
    profile = create_profile(
        session,
        workspace_id=auth.workspace_id,
        principal_id=auth.principal_id,
        service_id=payload.service_id,
        endpoint_id=payload.endpoint_id,
        connection_id=payload.connection_id,
        name=payload.name,
    )
    return ok(request, {"id": str(profile.id), "name": profile.name}, 201)


@router.get("/profiles")
def list_profiles(
    request: Request,
    service_id: UUID | None = None,
    auth: AuthContext = Depends(require_permission("profiles.read")),
    session: Session = Depends(get_session),
):
    stmt = select(ProfileRow).where(
        ProfileRow.workspace_id == auth.workspace_id,
        ProfileRow.owner_principal_id == auth.principal_id,
    )
    if service_id:
        stmt = stmt.where(ProfileRow.service_id == service_id)
    rows = session.scalars(stmt.order_by(ProfileRow.name)).all()
    return ok(
        request,
        [
            {
                "id": str(r.id),
                "service_id": str(r.service_id),
                "endpoint_id": str(r.endpoint_id),
                "connection_id": str(r.connection_id),
                "name": r.name,
                "status": r.status,
            }
            for r in rows
        ],
    )


@router.get("/active-bindings")
def list_active_bindings(
    request: Request,
    auth: AuthContext = Depends(require_permission("bindings.read")),
    session: Session = Depends(get_session),
):
    rows = session.scalars(
        select(ActiveBindingRow).where(
            ActiveBindingRow.workspace_id == auth.workspace_id,
            ActiveBindingRow.principal_id == auth.principal_id,
        )
    ).all()
    result = []
    for binding in rows:
        profile = session.get(ProfileRow, binding.active_profile_id)
        service = session.get(ServiceRow, binding.service_id)
        result.append(
            {
                "service_id": str(binding.service_id),
                "context_key": binding.context_key,
                "active_profile_id": str(binding.active_profile_id),
                "display_label": (
                    f"{service.slug if service else '?'} / {profile.name if profile else '?'}"
                ),
                "version": binding.version,
            }
        )
    return ok(request, result)


@router.put("/active-bindings/{service_id}/{context_key}")
def put_active_binding(
    service_id: UUID,
    context_key: str,
    payload: BindingPut,
    request: Request,
    auth: AuthContext = Depends(require_permission("bindings.write")),
    session: Session = Depends(get_session),
):
    # an agent context is written by the DELEGATING user, into that user's
    # own scope (P0-2 / SEC-033): resolve the agent server-side and require
    # an active user->agent delegation; identity never comes from payloads
    agent_principal_id = None
    if context_key.startswith("agent:"):
        raw_id = context_key.split(":", 1)[1]
        try:
            candidate = UUID(raw_id)
        except ValueError as exc:
            raise ApiError(ErrorCode.BAD_REQUEST, "Invalid agent context") from exc
        agent = session.get(PrincipalRow, candidate)
        if agent is None or agent.workspace_id != auth.workspace_id or agent.kind != "agent":
            raise ApiError(ErrorCode.NOT_FOUND, "Agent principal not found in this workspace")
        if not _active_delegation_exists(session, auth.workspace_id, auth.principal_id, agent.id):
            raise ApiError(
                ErrorCode.FORBIDDEN,
                "Writing an agent context requires an active delegation to that agent",
            )
        agent_principal_id = agent.id

    binding = activate_binding(
        session,
        workspace_id=auth.workspace_id,
        caller_principal_id=auth.principal_id,
        service_id=service_id,
        context_key=context_key,
        profile_id=payload.profile_id,
        expected_version=payload.expected_version,
        agent_principal_id=agent_principal_id,
    )
    profile = session.get(ProfileRow, binding.active_profile_id)
    service = session.get(ServiceRow, service_id)
    return ok(
        request,
        {
            "service_id": str(service_id),
            "context_key": context_key,
            "active_profile_id": str(binding.active_profile_id),
            "display_label": (
                f"{service.slug if service else '?'} / {profile.name if profile else '?'}"
            ),
            "version": binding.version,
        },
    )


# ---------------------------------------------------------------------------
# invocations / approvals / audit
# ---------------------------------------------------------------------------


@router.post("/invocations")
def create_invocation(
    payload: InvocationCreate,
    request: Request,
    auth: AuthContext = Depends(require_permission("invocations.create")),
    session: Session = Depends(get_session),
):
    settings = request.app.state.settings
    executor_client: httpx.Client = request.app.state.executor_client
    result = invoke_capability(
        session,
        workspace_id=auth.workspace_id,
        principal_id=auth.principal_id,
        principal_kind=auth.principal.kind,
        capability_name=payload.capability,
        capability_version=payload.version,
        context_key=payload.context_key,
        arguments=payload.arguments,
        idempotency_key=payload.idempotency_key,
        approval_request_id=payload.approval_request_id,
        settings=settings,
        executor_client=executor_client,
        delegation_context=payload.delegation_context,
    )
    return ok(request, result, 200)


def _agent_invocation_scope(
    request: Request, auth: AuthContext, session: Session
):
    """SEC-050: agents read history ONLY under a delegation context.

    A shared agent's credential alone must not enumerate other users'
    results: the acting user comes from a signed ticket (X-Delegation-
    Context header) and history is filtered to that delegating user.
    A ticket proves issuance, NOT continued validity - the underlying
    delegation must still be active (revocation is checked live).
    Returns the live DelegationRow, or None for non-agent principals.
    """
    if auth.principal.kind != "agent":
        return None
    ticket = request.headers.get("x-delegation-context", "")
    if not ticket:
        raise ApiError(
            ErrorCode.FORBIDDEN,
            "Agents must present a delegation context to read invocation history",
        )
    from ppswitch_db.models import DelegationRow

    from ppswitch_control.services.delegation_service import (
        is_active,
        verify_delegation_context,
    )

    payload = verify_delegation_context(
        ticket,
        secret=request.app.state.settings.effective_delegation_key,
        workspace_id=auth.workspace_id,
        agent_principal_id=auth.principal_id,
    )
    delegation = session.get(DelegationRow, UUID(payload["delegation_id"]))
    if delegation is None or not is_active(delegation):
        raise ApiError(
            ErrorCode.FORBIDDEN,
            "Delegation context no longer corresponds to an active delegation",
        )
    # SEC-053: ticket generation must equal the CURRENT delegation
    # generation - re-delegation after revocation invalidates old tickets
    if (delegation.generation or 1) != payload.get("gen"):
        raise ApiError(
            ErrorCode.FORBIDDEN,
            "Delegation context belongs to a previous authorization generation",
        )
    return delegation


def _history_record_authorized(session: Session, agent_principal_id, delegation, row) -> bool:
    """SEC-052: every history record is re-authorized BEFORE it is served.

    The CURRENT grant (scopes + expiry), the CURRENT delegation scopes,
    the recorded connection's liveness and scopes, and the capability
    version's required_scopes must all still allow the data - a narrowed
    or revoked authorization can never be re-read through history.
    """
    from ppswitch_core.errors import ApiError as _ApiError
    from ppswitch_db.models import AgentGrantRow, CapabilityVersionRow, ConnectionRow

    from ppswitch_control.services.invocation_service import (
        _authorize_scope_chain,
        _epoch,
        epoch_now,
    )

    version = session.get(CapabilityVersionRow, row.capability_version_id)
    if version is None:
        return False
    recorded_connection_id = row.profile_snapshot.get("connection_id")
    connection = (
        session.get(ConnectionRow, UUID(recorded_connection_id))
        if recorded_connection_id else None
    )
    if connection is None or connection.status != "active":
        return False
    now = epoch_now()
    active_grants = [
        g for g in session.scalars(
            select(AgentGrantRow).where(
                AgentGrantRow.workspace_id == row.workspace_id,
                AgentGrantRow.agent_principal_id == agent_principal_id,
                AgentGrantRow.capability_id == version.capability_id,
            )
        ).all()
        if g.expires_at is None or _epoch(g.expires_at) >= now
    ]
    if not active_grants:
        return False
    grant_scopes = (
        None if any(not (g.scopes or []) for g in active_grants)
        else sorted({s for g in active_grants for s in (g.scopes or [])})
    )
    try:
        _authorize_scope_chain(
            grant_scopes=grant_scopes,
            connection_scopes=connection.scopes or None,
            delegation_scopes=delegation.scopes or None,
            required_scopes=list(version.required_scopes or []),
        )
    except _ApiError:
        return False
    return True


@router.get("/invocations")
def list_invocations(
    request: Request,
    limit: int = 50,
    auth: AuthContext = Depends(require_permission("invocations.read")),
    session: Session = Depends(get_session),
):
    scope_delegation = _agent_invocation_scope(request, auth, session)
    rows = session.scalars(
        select(InvocationRow)
        .where(
            InvocationRow.workspace_id == auth.workspace_id,
            # SEC-029: execution results are visible to their owner only
            InvocationRow.principal_id == auth.principal_id,
        )
        .order_by(InvocationRow.created_at.desc())
        .limit(min(limit, 200))
    ).all()
    if scope_delegation is not None:
        # SEC-050: a shared agent sees only the acting user's records;
        # SEC-052: each record is re-authorized against the CURRENT grant,
        # delegation scopes, connection and required_scopes
        rows = [
            r for r in rows
            if r.profile_snapshot.get("delegating_user_id")
            == str(scope_delegation.user_principal_id)
            and _history_record_authorized(
                session, auth.principal_id, scope_delegation, r
            )
        ]
    return ok(request, [
        _invocation_view(r, settings=request.app.state.settings) for r in rows
    ])


@router.get("/invocations/{invocation_id}")
def get_invocation(
    invocation_id: UUID,
    request: Request,
    auth: AuthContext = Depends(require_permission("invocations.read")),
    session: Session = Depends(get_session),
):
    scope_delegation = _agent_invocation_scope(request, auth, session)
    row = session.get(InvocationRow, invocation_id)
    if (
        row is None
        or row.workspace_id != auth.workspace_id
        or row.principal_id != auth.principal_id
    ):
        raise ApiError(ErrorCode.NOT_FOUND, "Invocation not found")
    if scope_delegation is not None:
        # SEC-050: another delegating user's record is indistinguishable
        # from a missing one; SEC-052: records no longer authorized by the
        # current chain are equally invisible
        if row.profile_snapshot.get("delegating_user_id") != str(
            scope_delegation.user_principal_id
        ) or not _history_record_authorized(
            session, auth.principal_id, scope_delegation, row
        ):
            raise ApiError(ErrorCode.NOT_FOUND, "Invocation not found")
    return ok(
        request,
        _invocation_view(row, include_result=True, settings=request.app.state.settings),
    )


def _invocation_view(
    row: InvocationRow, *, include_result: bool = False, settings=None
) -> dict:
    view = {
        "id": str(row.id),
        "capability": f"{row.capability_name}:{row.capability_version}",
        "status": row.status,
        "profile_label": row.profile_snapshot.get("profile_label"),
        "created_at": str(row.created_at),
        # PR-DATA-12: the classification decided at storage time
        "data_class": row.profile_snapshot.get("data_class", "normal"),
    }
    if include_result:
        from ppswitch_control.services.result_read import stored_result

        view["result"] = (
            stored_result(row, settings) if settings is not None else row.result
        )
        view["error_code"] = row.error_code
    return view


@router.post("/approvals/{approval_id}/approve")
def approve_approval(
    approval_id: UUID,
    request: Request,
    auth: AuthContext = Depends(require_permission("approvals.decide")),
    session: Session = Depends(get_session),
):
    approval = session.get(ApprovalRow, approval_id)
    if approval is None or approval.workspace_id != auth.workspace_id:
        raise ApiError(ErrorCode.NOT_FOUND, "Approval not found")
    invocation = session.get(InvocationRow, approval.invocation_id)
    if invocation is None:
        raise ApiError(ErrorCode.NOT_FOUND, "Approval not found")
    if auth.principal_id == invocation.principal_id and auth.principal.kind == "user":
        raise ApiError(ErrorCode.FORBIDDEN, "Approver must differ from the requester")
    if approval.status != "pending":
        raise ApiError(ErrorCode.CONFLICT, f"Approval already {approval.status}")
    approval.status = "approved"
    approval.approver_id = auth.principal_id
    from ppswitch_db.models import utcnow

    approval.decided_at = utcnow()
    session.add(
        AuditEventRow(
            workspace_id=auth.workspace_id, actor_id=auth.principal_id,
            action="approval.approve", decision="allowed",
            redacted_metadata={"approval_id": str(approval_id)},
        )
    )
    return ok(request, {"id": str(approval.id), "status": approval.status})


@router.get("/audit-events")
def list_audit_events(
    request: Request,
    limit: int = 100,
    auth: AuthContext = Depends(require_permission("audit.read")),
    session: Session = Depends(get_session),
):
    rows = session.scalars(
        select(AuditEventRow)
        .where(AuditEventRow.workspace_id == auth.workspace_id)
        .order_by(AuditEventRow.created_at.desc())
        .limit(min(limit, 500))
    ).all()
    return ok(
        request,
        [
            {
                "id": str(r.id),
                "action": r.action,
                "decision": r.decision,
                "invocation_id": str(r.invocation_id) if r.invocation_id else None,
                "metadata": r.redacted_metadata,
                "created_at": str(r.created_at),
            }
            for r in rows
        ],
    )


# ---------------------------------------------------------------------------
# user -> agent delegations (review2 P0-2)
# ---------------------------------------------------------------------------


@router.post("/delegations")
def create_delegation_route(
    payload: DelegationCreate,
    request: Request,
    auth: AuthContext = Depends(require_permission("delegations.write")),
    session: Session = Depends(get_session),
):
    expires_at = None
    if payload.expires_at is not None:
        from datetime import datetime

        try:
            parsed = datetime.fromisoformat(payload.expires_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ApiError(ErrorCode.BAD_REQUEST, "expires_at must be ISO-8601") from exc
        expires_at = parsed
    from ppswitch_control.services.delegation_service import create_delegation

    delegation = create_delegation(
        session,
        workspace_id=auth.workspace_id,
        principal_id=auth.principal_id,
        agent_principal_id=payload.agent_principal_id,
        scopes=payload.scopes,
        expires_at=expires_at,
    )
    return ok(
        request,
        {
            "id": str(delegation.id),
            "agent_principal_id": str(delegation.agent_principal_id),
            "status": delegation.status,
        },
        201,
    )


@router.get("/delegations")
def list_delegations(
    request: Request,
    auth: AuthContext = Depends(require_permission("delegations.read")),
    session: Session = Depends(get_session),
):
    from ppswitch_db.models import DelegationRow

    from ppswitch_control.services.delegation_service import is_active

    rows = session.scalars(
        select(DelegationRow).where(
            DelegationRow.workspace_id == auth.workspace_id,
            DelegationRow.user_principal_id == auth.principal_id,
        )
    ).all()
    return ok(
        request,
        [
            {
                "id": str(r.id),
                "agent_principal_id": str(r.agent_principal_id),
                "scopes": r.scopes,
                "status": r.status,
                "active": is_active(r),
                "expires_at": str(r.expires_at) if r.expires_at else None,
            }
            for r in rows
        ],
    )


@router.post("/delegations/{delegation_id}/context")
def issue_delegation_context_route(
    delegation_id: UUID,
    request: Request,
    auth: AuthContext = Depends(require_permission("delegations.write")),
    session: Session = Depends(get_session),
):
    """SEC-046 / ADR-005: mint a short-lived signed acting-identity ticket.

    Only the delegating USER can call this (authenticated route). The agent
    presents the ticket on invocations to select the acting identity when
    several delegations are active - a parameter-named user is never
    trusted, and the ticket cannot be forged without the server secret.
    """
    from ppswitch_control.services.delegation_service import issue_delegation_context

    token, expires = issue_delegation_context(
        session,
        workspace_id=auth.workspace_id,
        principal_id=auth.principal_id,
        delegation_id=delegation_id,
        secret=request.app.state.settings.effective_delegation_key,
    )
    from datetime import UTC, datetime

    return ok(
        request,
        {
            "context": token,
            "expires_at": datetime.fromtimestamp(expires, tz=UTC).isoformat(),
        },
        201,
    )


@router.post("/delegations/{delegation_id}/revoke")
def revoke_delegation_route(
    delegation_id: UUID,
    request: Request,
    auth: AuthContext = Depends(require_permission("delegations.write")),
    session: Session = Depends(get_session),
):
    from ppswitch_control.services.delegation_service import revoke_delegation

    delegation = revoke_delegation(
        session,
        workspace_id=auth.workspace_id,
        principal_id=auth.principal_id,
        delegation_id=delegation_id,
    )
    return ok(request, {"id": str(delegation.id), "status": delegation.status})


# ---------------------------------------------------------------------------
# result lifecycle admin (PR-DATA-12)
# ---------------------------------------------------------------------------


@router.post("/admin/purge-results")
def purge_results(
    request: Request,
    auth: AuthContext = Depends(require_permission("results.purge")),
    session: Session = Depends(get_session),
):
    """Destroy expired result payloads in THIS workspace (metadata
    survives). The unscoped global sweep is the internal startup job
    only (DATA-16: tenant-scoped admin, global = maintenance identity).
    """
    from ppswitch_control.services.result_read import purge_expired_results

    purged = purge_expired_results(session, workspace_id=auth.workspace_id)
    session.add(
        AuditEventRow(
            workspace_id=auth.workspace_id, actor_id=auth.principal_id,
            action="results.purge", decision="allowed",
            redacted_metadata={"rows": purged},
        )
    )
    return ok(request, {"purged": purged})


@router.post("/admin/re-encrypt-results")
def reencrypt_results_route(
    request: Request,
    auth: AuthContext = Depends(require_permission("results.purge")),
    session: Session = Depends(get_session),
):
    """DATA-17 key rotation: re-encrypt this workspace's stored results
    under the current primary key (previous keys stay as read window)."""
    from ppswitch_control.services.result_read import reencrypt_results

    stats = reencrypt_results(
        session, request.app.state.settings, workspace_id=auth.workspace_id
    )
    session.add(
        AuditEventRow(
            workspace_id=auth.workspace_id, actor_id=auth.principal_id,
            action="results.reencrypt", decision="allowed",
            redacted_metadata=stats,
        )
    )
    return ok(request, stats)


# ---------------------------------------------------------------------------
# agent grants + dev principal directory (demo only)
# ---------------------------------------------------------------------------


@router.post("/agent-principals")
def create_agent_principal_route(
    payload: AgentPrincipalCreate,
    request: Request,
    auth: AuthContext = Depends(require_permission("agent_principals.write")),
    session: Session = Depends(get_session),
):
    """Create an agent identity; the token is returned exactly once."""
    result = create_agent_principal(
        session,
        workspace_id=auth.workspace_id,
        principal_id=auth.principal_id,
        display_name=payload.display_name,
    )
    return ok(request, result, 201)


@router.get("/agent-principals")
def list_agent_principals_route(
    request: Request,
    auth: AuthContext = Depends(require_permission("agent_principals.read")),
    session: Session = Depends(get_session),
):
    return ok(request, list_agent_principals(session, workspace_id=auth.workspace_id))


@router.post("/agent-grants")
def create_agent_grant(
    payload: AgentGrantCreate,
    request: Request,
    auth: AuthContext = Depends(require_permission("agent_grants.write")),
    session: Session = Depends(get_session),
):
    agent = session.get(PrincipalRow, payload.agent_principal_id)
    capability = session.get(CapabilityRow, payload.capability_id)
    if agent is None or agent.workspace_id != auth.workspace_id or agent.kind != "agent":
        raise ApiError(ErrorCode.NOT_FOUND, "Agent principal not found in this workspace")
    if capability is None or capability.workspace_id != auth.workspace_id:
        raise ApiError(ErrorCode.CAPABILITY_NOT_FOUND, "Capability not found")
    grant = AgentGrantRow(
        id=__import__("uuid").uuid4(),
        workspace_id=auth.workspace_id,
        agent_principal_id=agent.id,
        capability_id=capability.id,
        scopes=payload.scopes,
    )
    session.add(grant)
    session.flush()
    session.add(
        AuditEventRow(
            workspace_id=auth.workspace_id, actor_id=auth.principal_id,
            action="agent_grant.create", decision="allowed",
            redacted_metadata={
                "agent_principal_id": str(agent.id),
                "capability_id": str(capability.id),
            },
        )
    )
    return ok(request, {"id": str(grant.id)}, 201)


@router.get("/dev/principals")
def list_dev_principals(
    request: Request,
    session: Session = Depends(get_session),
):
    """DEV-ONLY login directory: returns synthetic dev tokens.

    Unauthenticated on purpose: this is the demo login picker. Refused
    unless demo mode is on. The tokens are synthetic values that grant
    nothing outside a localhost demo deployment; production identity comes
    from OIDC and this endpoint stays disabled (404).
    """
    settings = request.app.state.settings
    if not settings.demo_mode:
        raise ApiError(ErrorCode.NOT_FOUND, "No such endpoint")
    tokens: dict[str, str] = {}
    tokens_path = Path(settings.dev_tokens_path)
    if tokens_path.exists():
        try:
            tokens = json.loads(tokens_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            tokens = {}
    rows = session.scalars(select(PrincipalRow)).all()
    return ok(
        request,
        [
            {
                "id": str(r.id),
                "kind": r.kind,
                "display_name": r.display_name,
                "workspace_id": str(r.workspace_id),
                "token_hint": r.token_hint,
                # DEV-ONLY: synthetic token from the local runtime file so the
                # demo login works; absent when seed-demo has not run
                "dev_token": tokens.get(str(r.id)),
            }
            for r in rows
        ],
    )
