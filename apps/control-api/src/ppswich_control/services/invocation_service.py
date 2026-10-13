"""Invocation service: the authorization chain and dispatch to the executor.

Chain (docs/05-SECURITY.md section 2):
verified principal -> published capability -> agent grant -> argument
validation -> active binding -> profile/connection/endpoint checks ->
policy decision -> immutable snapshot -> signed dispatch -> executor
-> sanitized result -> audit.

The plan snapshot is frozen BEFORE dispatch: switching the active profile
mid-flight never changes an invocation already in progress (SEC-017).
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid as uuid_module
from uuid import UUID

import httpx
from ppswich_core.entities import InvocationPlan, InvocationPlanOperation
from ppswich_core.enums import (
    AuthScheme,
    CapabilityStatus,
    ConnectionStatus,
    EndpointStatus,
    InvocationStatus,
    PrincipalKind,
    ProfileStatus,
    RiskLevel,
)
from ppswich_core.errors import ApiError, ErrorCode
from ppswich_db.models import (
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
    ProfileRow,
)
from ppswich_policy import evaluate_invocation, validate_arguments
from sqlalchemy import select
from sqlalchemy.orm import Session

PLAN_DEADLINE_S = 30.0


def _arguments_digest(arguments: dict) -> str:
    return hashlib.sha256(
        json.dumps(arguments, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _audit(
    session: Session, workspace_id: UUID, principal_id: UUID, action: str,
    decision: str, metadata: dict, invocation_id: UUID | None = None,
) -> None:
    session.add(
        AuditEventRow(
            workspace_id=workspace_id,
            invocation_id=invocation_id,
            actor_id=principal_id,
            action=action,
            decision=decision,
            redacted_metadata=metadata,
        )
    )


def _load_published_version(
    session: Session, capability: CapabilityRow, requested_version: str | None
) -> CapabilityVersionRow:
    stmt = select(CapabilityVersionRow).where(
        CapabilityVersionRow.capability_id == capability.id,
        CapabilityVersionRow.status == "published",
    )
    if requested_version:
        stmt = stmt.where(CapabilityVersionRow.version == requested_version)
    else:
        stmt = stmt.order_by(CapabilityVersionRow.created_at.desc())
    version = session.scalars(stmt).first()
    if version is None:
        raise ApiError(ErrorCode.CAPABILITY_NOT_FOUND, "Capability not found")
    return version


def invoke_capability(
    session: Session,
    *,
    workspace_id: UUID,
    principal_id: UUID,
    principal_kind: str,
    capability_name: str,
    capability_version: str | None,
    context_key: str,
    arguments: dict,
    idempotency_key: str | None,
    approval_request_id: UUID | None,
    settings,
    executor_client: httpx.Client,
) -> dict:
    # --- idempotency: same key returns the recorded outcome, no re-execution
    if idempotency_key:
        existing = session.scalars(
            select(InvocationRow).where(
                InvocationRow.workspace_id == workspace_id,
                InvocationRow.idempotency_key == idempotency_key,
            )
        ).one_or_none()
        if existing is not None:
            return {
                "invocation_id": str(existing.id),
                "status": existing.status,
                "result": existing.result,
                "profile_label": existing.profile_snapshot.get("profile_label"),
                "idempotent_replay": True,
            }

    # --- capability resolution (workspace-scoped, no cross-tenant leakage)
    capability = session.scalars(
        select(CapabilityRow).where(
            CapabilityRow.workspace_id == workspace_id,
            CapabilityRow.name == capability_name,
        )
    ).one_or_none()
    if capability is None or capability.status != CapabilityStatus.PUBLISHED.value:
        # 404 for missing AND for unpublished: existence is not revealed
        raise ApiError(ErrorCode.CAPABILITY_NOT_FOUND, "Capability not found")
    version_row = _load_published_version(session, capability, capability_version)

    # --- agent grants: agents act only on explicitly granted capabilities
    if principal_kind == PrincipalKind.AGENT.value:
        grants = session.scalars(
            select(AgentGrantRow).where(
                AgentGrantRow.workspace_id == workspace_id,
                AgentGrantRow.agent_principal_id == principal_id,
                AgentGrantRow.capability_id == capability.id,
            )
        ).all()
        now = time.time()
        grant_expired = all(
            g.expires_at is not None and g.expires_at.timestamp() < now for g in grants
        )
        if not grants or grant_expired:
            _audit(
                session, workspace_id, principal_id, "invocation.deny", "denied",
                {"capability": capability_name, "reason": "agent_missing_grant"},
            )
            session.commit()  # keep the deny audit even though we 403
            raise ApiError(ErrorCode.FORBIDDEN, "Agent is not granted this capability")

    # --- argument validation: reserved names + strict schema
    validate_arguments(arguments, version_row.input_schema or {})

    # --- active binding resolution (capability -> operation -> service)
    operation_version = session.get(OperationVersionRow, version_row.operation_version_id)
    assert operation_version is not None  # FK-guaranteed by the registry
    operation = session.get(OperationRow, operation_version.operation_id)
    assert operation is not None
    binding = session.get(
        ActiveBindingRow,
        {
            "workspace_id": workspace_id,
            "principal_id": principal_id,
            "service_id": operation.service_id,
            "context_key": context_key,
        },
    )
    if binding is None:
        raise ApiError(
            ErrorCode.NO_ACTIVE_PROFILE,
            "No active profile for this service/context; activate a profile first",
        )

    profile = session.get(ProfileRow, binding.active_profile_id)
    if profile is None:
        raise ApiError(ErrorCode.NO_ACTIVE_PROFILE, "Bound profile is inconsistent")
    connection = session.get(ConnectionRow, profile.connection_id)
    endpoint = session.get(EndpointRow, profile.endpoint_id)
    if connection is None or endpoint is None:
        raise ApiError(ErrorCode.NO_ACTIVE_PROFILE, "Bound profile is inconsistent")

    # --- policy decision (deny by default)
    decision = evaluate_invocation(
        principal_kind=PrincipalKind(principal_kind),
        capability_status=CapabilityStatus(capability.status),
        agent_has_grant=True,
        connection_status=ConnectionStatus(connection.status),
        profile_status=ProfileStatus(profile.status),
        endpoint_status=EndpointStatus(endpoint.status),
        risk_level=RiskLevel(version_row.risk_level),
    )
    if not decision.allowed:
        _audit(
            session, workspace_id, principal_id, "invocation.deny", "denied",
            {"capability": capability_name, "reason": decision.reason},
        )
        session.commit()  # keep the deny audit even though we 403
        raise ApiError(ErrorCode.FORBIDDEN, "Invocation denied by policy")

    execution = version_row.execution or {}
    plan = InvocationPlan(
        invocation_id=uuid_module.uuid4(),
        capability_name=capability.name,
        capability_version=version_row.version,
        capability_version_id=version_row.id,
        operation=InvocationPlanOperation(
            method=operation.method,
            path_template=operation.normalized_path,
            path_map=execution.get("path", {}),
            query_template=execution.get("query", {}),
            body_template=execution.get("body", {}),
        ),
        endpoint_base_url=endpoint.base_url,
        approved_hosts=endpoint.approved_hosts or [],
        auth_scheme=AuthScheme(connection.auth_scheme),
        secret_ref=connection.secret_ref,
        allow_fields=version_row.allow_fields or [],
        input_schema=version_row.input_schema or {},
        output_schema=version_row.output_schema or {},
        arguments=arguments,
        workspace_id=workspace_id,
        principal_id=principal_id,
        agent_id=principal_id if principal_kind == PrincipalKind.AGENT.value else None,
        risk_level=RiskLevel(version_row.risk_level),
        policy_version=version_row.policy_ref,
        arguments_digest=_arguments_digest(arguments),
        idempotency_key=idempotency_key,
        deadline_epoch_s=time.time() + PLAN_DEADLINE_S,
        nonce=uuid_module.uuid4().hex + uuid_module.uuid4().hex[:8],
    )
    snapshot = json.loads(plan.model_dump_json())
    snapshot["profile_label"] = profile.name

    invocation = InvocationRow(
        workspace_id=workspace_id,
        capability_version_id=version_row.id,
        capability_name=capability.name,
        capability_version=version_row.version,
        principal_id=principal_id,
        agent_id=plan.agent_id,
        profile_id=profile.id,
        profile_snapshot=snapshot,
        arguments_digest=plan.arguments_digest,
        idempotency_key=idempotency_key,
        policy_version=version_row.policy_ref,
        status=InvocationStatus.PENDING.value,
    )
    session.add(invocation)
    session.flush()

    # --- high-risk operations require an approved, digested approval
    if decision.approval_required:
        if approval_request_id is None:
            approval = ApprovalRow(
                workspace_id=workspace_id,
                invocation_id=invocation.id,
                approver_id=principal_id,  # placeholder; replaced on approval
                arguments_digest=plan.arguments_digest,
                status="pending",
            )
            session.add(approval)
            session.flush()
            invocation.status = InvocationStatus.APPROVAL_REQUIRED.value
            _audit(
                session, workspace_id, principal_id, "invocation.approval_required",
                "pending", {"capability": capability.name},
                invocation_id=invocation.id,
            )
            # persist the approval request despite the 409 response
            session.commit()
            raise ApiError(
                ErrorCode.APPROVAL_REQUIRED,
                "High-risk capability requires approval before execution",
                {"approval_request_id": str(approval.id)},
            )
        approval_row = session.get(ApprovalRow, approval_request_id)
        if approval_row is None or (
            approval_row.workspace_id != workspace_id
            or approval_row.status != "approved"
            or approval_row.arguments_digest != plan.arguments_digest
        ):
            raise ApiError(
                ErrorCode.APPROVAL_REQUIRED,
                "Approval missing, not approved, or arguments changed",
            )
        approval_row.status = "consumed"

    # --- dispatch to the trusted executor with a signed, frozen snapshot
    from ppswich_executor.plan_signing import sign_plan

    signature = sign_plan(snapshot, settings.internal_token)
    invocation.status = InvocationStatus.RUNNING.value
    session.flush()  # snapshot is durably frozen before dispatch (SEC-017)

    try:
        response = executor_client.post(
            f"{settings.executor_url}/internal/v1/execute",
            json={"plan": snapshot, "signature": signature},
            headers={"X-Internal-Token": settings.internal_token},
        )
    except httpx.HTTPError as exc:
        invocation.status = InvocationStatus.FAILED.value
        invocation.error_code = ErrorCode.UPSTREAM_FAILURE.name
        _audit(session, workspace_id, principal_id, "invocation.dispatch", "failed",
               {"capability": capability.name}, invocation_id=invocation.id)
        session.commit()  # persist the failed invocation record
        raise ApiError(ErrorCode.UPSTREAM_FAILURE, "Executor unreachable") from exc

    if response.status_code == 200:
        body = response.json()
        invocation.status = InvocationStatus.SUCCEEDED.value
        invocation.result = body.get("result")
    else:
        error = response.json().get("error", {})
        invocation.status = InvocationStatus.FAILED.value
        invocation.error_code = error.get("code", "UPSTREAM_FAILURE")
        _audit(
            session, workspace_id, principal_id, "invocation.execute", "failed",
            {"capability": capability.name, "error_code": invocation.error_code},
            invocation_id=invocation.id,
        )
        try:
            code = ErrorCode(error.get("code", "UPSTREAM_FAILURE"))
        except ValueError:
            code = ErrorCode.UPSTREAM_FAILURE
        session.commit()  # persist the failed invocation record
        raise ApiError(code, error.get("message", "Execution failed"))

    from ppswich_db.models import utcnow

    invocation.finished_at = utcnow()
    _audit(
        session, workspace_id, principal_id, "invocation.execute", "allowed",
        {
            "capability": capability.name,
            "capability_version": version_row.version,
            "profile_id": str(profile.id),
            "dropped_fields": response.json().get("dropped_fields", []),
        },
        invocation_id=invocation.id,
    )
    return {
        "invocation_id": str(invocation.id),
        "status": invocation.status,
        "result": invocation.result,
        "profile_label": profile.name,
        "idempotent_replay": False,
    }
