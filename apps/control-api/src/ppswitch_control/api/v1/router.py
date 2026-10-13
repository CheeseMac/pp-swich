"""Control plane REST API v1 (docs/07-API-SPEC.md).

Every route is workspace-scoped through the verified principal; identity
never comes from payloads. Connections expose metadata only - secret_ref
and plaintext credentials are never serialized (SEC-003 / docs/06 section 6).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
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
    ConnectionRow,
    EndpointRow,
    InvocationRow,
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
    get_session,
    require_principal,
    require_user,
)
from ppswitch_control.envelope import ok
from ppswitch_control.services.capability_service import (
    create_capability,
    publish_capability,
    review_capability,
    verify_capability,
)
from ppswitch_control.services.connection_service import (
    activate_binding,
    create_connection,
    create_profile,
)
from ppswitch_control.services.discovery_service import import_discovery_document
from ppswitch_control.services.invocation_service import invoke_capability

router = APIRouter(prefix="/api/v1")


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


class AgentGrantCreate(BaseModel):
    agent_principal_id: UUID
    capability_id: UUID
    scopes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# workspaces / services / endpoints
# ---------------------------------------------------------------------------


@router.post("/workspaces")
def create_workspace(
    payload: WorkspaceCreate,
    request: Request,
    auth: AuthContext = Depends(require_principal),
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
    auth: AuthContext = Depends(require_principal),
    session: Session = Depends(get_session),
):
    rows = session.scalars(
        select(ServiceRow)
        .where(ServiceRow.workspace_id == auth.workspace_id)
        .order_by(ServiceRow.slug)
    ).all()
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
    auth: AuthContext = Depends(require_principal),
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
    auth: AuthContext = Depends(require_principal),
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
    allowed_schemes: tuple[str, ...] = ("http://", "https://")
    if request.app.state.settings.demo_mode:
        allowed_schemes = (*allowed_schemes, "mock://")  # in-process demo transport
    if not payload.base_url.startswith(allowed_schemes):
        raise ApiError(ErrorCode.BAD_REQUEST, "base_url must be http(s)")
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
    auth: AuthContext = Depends(require_user),
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
    auth: AuthContext = Depends(require_principal),
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
    auth: AuthContext = Depends(require_principal),
    session: Session = Depends(get_session),
):
    settings = request.app.state.settings
    content = await file.read()
    if len(content) > settings.max_import_bytes:
        raise ApiError(
            ErrorCode.PAYLOAD_TOO_LARGE, "Import file exceeds the size limit"
        )
    try:
        document = json.loads(content)
    except (ValueError, TypeError) as exc:
        raise ApiError(ErrorCode.BAD_REQUEST, "Import file is not valid JSON") from exc
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


@router.get("/operations")
def list_operations(
    request: Request,
    service_id: UUID | None = None,
    status: str | None = None,
    auth: AuthContext = Depends(require_principal),
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
    auth: AuthContext = Depends(require_principal),
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


@router.post("/operations/{operation_id}/verify")
def verify_operation(
    operation_id: UUID,
    payload: ReviewRequest,
    request: Request,
    auth: AuthContext = Depends(require_principal),
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


@router.post("/capabilities")
def create_capability_route(
    payload: CapabilityCreate,
    request: Request,
    auth: AuthContext = Depends(require_principal),
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
    )
    return ok(request, {"id": str(capability.id), "status": capability.status}, 201)


@router.get("/capabilities")
def list_capabilities(
    request: Request,
    status: str | None = None,
    auth: AuthContext = Depends(require_principal),
    session: Session = Depends(get_session),
):
    stmt = select(CapabilityRow).where(CapabilityRow.workspace_id == auth.workspace_id)
    if status:
        stmt = stmt.where(CapabilityRow.status == status)
    rows = session.scalars(stmt.order_by(CapabilityRow.name)).all()
    if auth.principal.kind == "agent":
        # review Q5: an agent's tool directory (HTTP and MCP alike) must
        # contain only capabilities explicitly granted to it
        granted_ids = {
            grant.capability_id
            for grant in session.scalars(
                select(AgentGrantRow).where(
                    AgentGrantRow.workspace_id == auth.workspace_id,
                    AgentGrantRow.agent_principal_id == auth.principal_id,
                )
            ).all()
        }
        rows = [row for row in rows if row.id in granted_ids]
    result = []
    for row in rows:
        versions = session.scalars(
            select(CapabilityVersionRow)
            .where(CapabilityVersionRow.capability_id == row.id)
            .order_by(CapabilityVersionRow.created_at)
        ).all()
        result.append(_capability_view(row, versions))
    return ok(request, result)


@router.get("/capabilities/{capability_id}")
def get_capability(
    capability_id: UUID,
    request: Request,
    auth: AuthContext = Depends(require_principal),
    session: Session = Depends(get_session),
):
    row = session.get(CapabilityRow, capability_id)
    if row is None or row.workspace_id != auth.workspace_id:
        raise ApiError(ErrorCode.CAPABILITY_NOT_FOUND, "Capability not found")
    versions = session.scalars(
        select(CapabilityVersionRow)
        .where(CapabilityVersionRow.capability_id == row.id)
        .order_by(CapabilityVersionRow.created_at)
    ).all()
    return ok(request, _capability_view(row, versions))


@router.post("/capabilities/{capability_id}/review")
def review_capability_route(
    capability_id: UUID,
    payload: ReviewRequest,
    request: Request,
    auth: AuthContext = Depends(require_user),
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
    auth: AuthContext = Depends(require_user),
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
    auth: AuthContext = Depends(require_user),
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
    auth: AuthContext = Depends(require_principal),
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
    auth: AuthContext = Depends(require_principal),
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
    auth: AuthContext = Depends(require_principal),
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
    auth: AuthContext = Depends(require_principal),
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
    auth: AuthContext = Depends(require_principal),
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
    auth: AuthContext = Depends(require_principal),
    session: Session = Depends(get_session),
):
    # an agent context maps to that agent's own scope; resolve the agent
    # server-side (existence in this workspace) and never trust payload ids
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
    auth: AuthContext = Depends(require_principal),
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
    )
    return ok(request, result, 200)


@router.get("/invocations")
def list_invocations(
    request: Request,
    limit: int = 50,
    auth: AuthContext = Depends(require_principal),
    session: Session = Depends(get_session),
):
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
    return ok(request, [_invocation_view(r) for r in rows])


@router.get("/invocations/{invocation_id}")
def get_invocation(
    invocation_id: UUID,
    request: Request,
    auth: AuthContext = Depends(require_principal),
    session: Session = Depends(get_session),
):
    row = session.get(InvocationRow, invocation_id)
    if (
        row is None
        or row.workspace_id != auth.workspace_id
        or row.principal_id != auth.principal_id
    ):
        raise ApiError(ErrorCode.NOT_FOUND, "Invocation not found")
    return ok(request, _invocation_view(row, include_result=True))


def _invocation_view(row: InvocationRow, *, include_result: bool = False) -> dict:
    view = {
        "id": str(row.id),
        "capability": f"{row.capability_name}:{row.capability_version}",
        "status": row.status,
        "profile_label": row.profile_snapshot.get("profile_label"),
        "created_at": str(row.created_at),
    }
    if include_result:
        view["result"] = row.result
        view["error_code"] = row.error_code
    return view


@router.post("/approvals/{approval_id}/approve")
def approve_approval(
    approval_id: UUID,
    request: Request,
    auth: AuthContext = Depends(require_user),
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
    auth: AuthContext = Depends(require_principal),
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
# agent grants + dev principal directory (demo only)
# ---------------------------------------------------------------------------


@router.post("/agent-grants")
def create_agent_grant(
    payload: AgentGrantCreate,
    request: Request,
    auth: AuthContext = Depends(require_user),
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
