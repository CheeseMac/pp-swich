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
from ppswitch_core.entities import InvocationPlan, InvocationPlanOperation
from ppswitch_core.enums import (
    AuthScheme,
    CapabilityStatus,
    ConnectionStatus,
    EndpointStatus,
    InvocationStatus,
    PrincipalKind,
    ProfileStatus,
    RiskLevel,
)
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
    ProfileRow,
)
from ppswitch_policy import evaluate_invocation, validate_arguments
from sqlalchemy import select
from sqlalchemy.orm import Session

PLAN_DEADLINE_S = 30.0


def epoch_now() -> float:
    return time.time()


def _epoch(value) -> float:
    """Epoch seconds, treating naive datetimes as UTC.

    SQLite returns DateTime values without tzinfo; ``.timestamp()`` would
    reinterpret them in the host's local zone and misjudge expiries.
    """
    import calendar

    if value.tzinfo is None:
        return float(calendar.timegm(value.timetuple()))
    return value.timestamp()
APPROVAL_TTL_S = 15 * 60  # approvals expire after 15 minutes


def _arguments_digest(arguments: dict) -> str:
    return hashlib.sha256(
        json.dumps(arguments, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _authorize_scope_chain(
    *,
    grant_scopes: list[str] | None,
    connection_scopes: list[str] | None,
    delegation_scopes: list[str] | None,
    required_scopes: list[str],
) -> set[str] | None:
    """R-06/SEC-041 + SEC-044/SEC-049: the single scope authority.

    Returns the effective scope set (None = unrestricted), or raises
    FORBIDDEN when the chain no longer authorizes the call. Shared by the
    live invocation path AND the idempotent replay path so a narrowed or
    revoked authorization can never be bypassed through the cache.
    """
    effective: set[str] | None = None  # None = unrestricted
    for scopes in (grant_scopes, connection_scopes, delegation_scopes):
        if scopes:
            effective = set(scopes) if effective is None else (effective & set(scopes))
    if effective is not None and not effective:
        raise ApiError(
            ErrorCode.FORBIDDEN, "No scope is permitted by grant, connection and delegation"
        )
    if required_scopes:
        missing = set(required_scopes) - (effective or set())
        if effective is None or missing:
            raise ApiError(
                ErrorCode.FORBIDDEN,
                "Capability requires scopes not covered by the authorization chain",
                {"missing_scopes": sorted(missing) or sorted(required_scopes)},
            )
    return effective


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
    delegation_context: str | None = None,
) -> dict:
    # --- capability resolution first (workspace-scoped, no cross-tenant leak)
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

    # --- context normalization (P0-2): agent contexts belong to agents and
    # are validated against the authenticated principal, never trusted.
    delegating_user_id: UUID | None = None
    if principal_kind == PrincipalKind.AGENT.value:
        if context_key.startswith("agent:"):
            parts = context_key.split(":", 2)
            claimed_agent = parts[1]
            # the agent id embedded in the context MUST be the caller itself
            if claimed_agent != str(principal_id):
                _audit(
                    session, workspace_id, principal_id, "invocation.deny", "denied",
                    {"capability": capability_name, "reason": "agent_context_mismatch"},
                )
                session.commit()
                raise ApiError(
                    ErrorCode.FORBIDDEN, "Agent context does not match the caller"
                )
        elif context_key.startswith("deployment:"):
            _audit(
                session, workspace_id, principal_id, "invocation.deny", "denied",
                {"capability": capability_name, "reason": "agent_deployment_context"},
            )
            session.commit()
            raise ApiError(
                ErrorCode.FORBIDDEN, "Deployment contexts are user-scoped"
            )
        # "personal" from an agent maps server-side to its own agent context
    elif context_key.startswith("agent:"):
        _audit(
            session, workspace_id, principal_id, "invocation.deny", "denied",
            {"capability": capability_name, "reason": "user_agent_context"},
        )
        session.commit()
        raise ApiError(
            ErrorCode.FORBIDDEN, "Agent contexts may only be used by that agent"
        )

    # --- agent grants: agents act only on explicitly granted capabilities
    grant_scopes: list[str] | None = None  # None = unrestricted
    if principal_kind == PrincipalKind.AGENT.value:
        grants = session.scalars(
            select(AgentGrantRow).where(
                AgentGrantRow.workspace_id == workspace_id,
                AgentGrantRow.agent_principal_id == principal_id,
                AgentGrantRow.capability_id == capability.id,
            )
        ).all()
        now = epoch_now()
        active_grants = [
            g for g in grants
            if g.expires_at is None or _epoch(g.expires_at) >= now
        ]
        if not active_grants:
            _audit(
                session, workspace_id, principal_id, "invocation.deny", "denied",
                {"capability": capability_name, "reason": "agent_missing_grant"},
            )
            session.commit()  # keep the deny audit even though we 403
            raise ApiError(ErrorCode.FORBIDDEN, "Agent is not granted this capability")
        # any grant without scopes is unrestricted for this capability
        if any(not (g.scopes or []) for g in active_grants):
            grant_scopes = None
        else:
            grant_scopes = sorted({s for g in active_grants for s in (g.scopes or [])})

    # --- delegation (P0-2 / SEC-033): an agent acts for a user only under
    # an ACTIVE delegation; the delegating user's bindings are the ones used.
    # SEC-046 / ADR-005: with several active delegations the acting user is
    # selected ONLY by a signed, short-lived delegation context ticket that
    # the delegating USER issued over an authenticated API call - never by
    # a request parameter the model could fill in.
    delegation_scopes: list[str] | None = None
    if principal_kind == PrincipalKind.AGENT.value:
        from ppswitch_control.services.delegation_service import (
            active_delegations_for_agent,
            verify_delegation_context,
        )

        delegations = active_delegations_for_agent(session, workspace_id, principal_id)
        if delegation_context is not None:
            payload = verify_delegation_context(
                delegation_context,
                secret=settings.effective_delegation_key,
                workspace_id=workspace_id,
                agent_principal_id=principal_id,
            )
            wanted = UUID(payload["user_id"])
            matched = [d for d in delegations if d.user_principal_id == wanted]
            if len(matched) != 1:
                _audit(
                    session, workspace_id, principal_id, "invocation.deny", "denied",
                    {"capability": capability_name, "reason": "context_delegation_inactive"},
                )
                session.commit()
                raise ApiError(
                    ErrorCode.FORBIDDEN,
                    "Delegation context does not match an active delegation",
                )
            delegating_user_id = matched[0].user_principal_id
            delegation_scopes = matched[0].scopes or None
        elif len(delegations) == 1:
            delegating_user_id = delegations[0].user_principal_id
            delegation_scopes = delegations[0].scopes or None
        elif not delegations:
            _audit(
                session, workspace_id, principal_id, "invocation.deny", "denied",
                {"capability": capability_name, "reason": "agent_no_delegation"},
            )
            session.commit()
            raise ApiError(
                ErrorCode.FORBIDDEN,
                "Agent has no active user delegation; a user must delegate first",
            )
        else:
            raise ApiError(
                ErrorCode.CONFLICT,
                "Multiple active delegations; present a delegation context ticket "
                "issued by the acting user",
            )

    # --- idempotency: a key replays ONLY for the same principal invoking the
    # same capability version with the same arguments (review PR-SEC-02 /
    # SEC-025). A key reused by anyone else is a conflict, never their data.
    # R-07 / SEC-037: the replay happens AFTER grant + delegation checks -
    # an expired grant or a revoked delegation can no longer read cached
    # results through an old idempotency key.
    # SEC-043: the DELEGATING USER is part of the replay identity - the
    # same shared agent with the same key under another user's delegation
    # is a conflict, never a cross-user cache hit.
    if idempotency_key:
        existing = session.scalars(
            select(InvocationRow).where(
                InvocationRow.workspace_id == workspace_id,
                InvocationRow.idempotency_key == idempotency_key,
            )
        ).one_or_none()
        if existing is not None:
            recorded_user = existing.profile_snapshot.get("delegating_user_id")
            replay_ok = (
                existing.principal_id == principal_id
                and existing.capability_name == capability.name
                and existing.capability_version == version_row.version
                and existing.arguments_digest == _arguments_digest(arguments)
                and recorded_user == (
                    str(delegating_user_id) if delegating_user_id is not None else None
                )
            )
            if not replay_ok:
                raise ApiError(
                    ErrorCode.IDEMPOTENCY_KEY_CONFLICT,
                    "Idempotency key already used by a different request",
                )
            # SEC-049: a narrowed or revoked authorization must not be
            # bypassed through the cache. The CURRENT grant/delegation
            # scopes were resolved above; the recorded connection is
            # re-loaded and must still be active, and the whole scope
            # chain is re-validated against this version's requirements.
            recorded_connection_id = existing.profile_snapshot.get("connection_id")
            replay_connection = (
                session.get(ConnectionRow, UUID(recorded_connection_id))
                if recorded_connection_id else None
            )
            if replay_connection is None or replay_connection.status != "active":
                _audit(
                    session, workspace_id, principal_id, "invocation.deny", "denied",
                    {"capability": capability_name, "reason": "replay_connection_revoked"},
                )
                session.commit()
                raise ApiError(
                    ErrorCode.FORBIDDEN,
                    "Cached result is no longer authorized (connection revoked)",
                )
            try:
                _authorize_scope_chain(
                    grant_scopes=grant_scopes,
                    connection_scopes=replay_connection.scopes or None,
                    delegation_scopes=delegation_scopes,
                    required_scopes=list(version_row.required_scopes or []),
                )
            except ApiError as exc:
                _audit(
                    session, workspace_id, principal_id, "invocation.deny", "denied",
                    {"capability": capability_name, "reason": "replay_scope_revoked"},
                )
                session.commit()
                raise ApiError(
                    ErrorCode.FORBIDDEN,
                    "Cached result is no longer authorized by the current scope chain",
                ) from exc
            return {
                "invocation_id": str(existing.id),
                "status": existing.status,
                "result": existing.result,
                "profile_label": existing.profile_snapshot.get("profile_label"),
                "idempotent_replay": True,
            }

    # --- argument validation: reserved names + strict schema
    validate_arguments(arguments, version_row.input_schema or {})

    # --- active binding resolution (capability -> operation -> service).
    # P0-2 / SEC-033: an agent resolves the DELEGATING user's binding under
    # that agent's context - never a binding shared across users.
    operation_version = session.get(OperationVersionRow, version_row.operation_version_id)
    assert operation_version is not None  # FK-guaranteed by the registry
    operation = session.get(OperationRow, operation_version.operation_id)
    assert operation is not None
    if principal_kind == PrincipalKind.AGENT.value:
        binding_principal_id = delegating_user_id
        binding_context = f"agent:{principal_id}"
    else:
        binding_principal_id = principal_id
        binding_context = context_key
    assert binding_principal_id is not None
    binding = session.get(
        ActiveBindingRow,
        {
            "workspace_id": workspace_id,
            "principal_id": binding_principal_id,
            "service_id": operation.service_id,
            "context_key": binding_context,
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

    # --- scope intersection (R-06/SEC-041) + required scopes (SEC-044):
    # delegated to the shared guard so replay and live paths can never
    # disagree on what the chain authorizes
    required_scopes = list(version_row.required_scopes or [])
    try:
        effective_scopes = _authorize_scope_chain(
            grant_scopes=grant_scopes,
            connection_scopes=connection.scopes or None,
            delegation_scopes=delegation_scopes,
            required_scopes=required_scopes,
        )
    except ApiError as exc:
        _audit(
            session, workspace_id, principal_id, "invocation.deny", "denied",
            {"capability": capability_name, "reason": "scope_chain_denied"},
        )
        session.commit()  # keep the deny audit even though we 403
        raise exc

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
    plan_payload = json.loads(plan.model_dump_json())
    plan_payload["profile_label"] = profile.name

    # PR-SEC-04: the database keeps a REDACTED audit snapshot only - no
    # arguments, no secret_ref, no target address. The full plan exists
    # solely in memory for this dispatch.
    audit_snapshot = {
        "profile_id": str(profile.id),
        "profile_label": profile.name,
        "connection_id": str(connection.id),
        "endpoint_id": str(endpoint.id),
        "capability_name": capability.name,
        "capability_version": version_row.version,
        "policy_version": version_row.policy_ref,
        "arguments_digest": plan.arguments_digest,
        "risk_level": version_row.risk_level,
        # P0-2: which user the agent acted for (None for direct user calls)
        "delegating_user_id": (
            str(delegating_user_id) if delegating_user_id is not None else None
        ),
        # R-06: the scope set that actually authorized this call
        "effective_scopes": sorted(effective_scopes) if effective_scopes is not None else [],
    }

    invocation = InvocationRow(
        workspace_id=workspace_id,
        capability_version_id=version_row.id,
        capability_name=capability.name,
        capability_version=version_row.version,
        principal_id=principal_id,
        agent_id=plan.agent_id,
        profile_id=profile.id,
        profile_snapshot=audit_snapshot,
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
            from datetime import timedelta

            from ppswitch_db.models import utcnow

            approval = ApprovalRow(
                workspace_id=workspace_id,
                invocation_id=invocation.id,
                requester_id=principal_id,
                capability_version_id=version_row.id,
                profile_id=profile.id,
                approver_id=principal_id,  # placeholder; replaced on approval
                arguments_digest=plan.arguments_digest,
                expires_at=utcnow() + timedelta(minutes=APPROVAL_TTL_S // 60),
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
        # PR-SEC-02 / SEC-024: an approval authorizes exactly ONE proposal -
        # requester + capability version + profile + arguments - and expires.
        approval_row = session.get(ApprovalRow, approval_request_id)
        binding_ok = approval_row is not None and (
            approval_row.workspace_id == workspace_id
            and approval_row.status == "approved"
            and approval_row.requester_id == principal_id
            and approval_row.capability_version_id == version_row.id
            and approval_row.profile_id == profile.id
            and approval_row.arguments_digest == plan.arguments_digest
            and (
                approval_row.expires_at is None
                or _epoch(approval_row.expires_at) >= time.time()
            )
        )
        if not binding_ok:
            raise ApiError(
                ErrorCode.APPROVAL_REQUIRED,
                "Approval missing, expired, or bound to a different request",
            )
        from sqlalchemy import text as sql_text

        assert approval_row is not None  # narrowed by binding_ok above
        consume = session.execute(
            sql_text(
                "UPDATE approvals SET status = 'consumed' "
                "WHERE id = :id AND status = 'approved'"
            ),
            {"id": approval_row.id.hex},
        )
        if getattr(consume, "rowcount", 0) == 0:
            raise ApiError(
                ErrorCode.APPROVAL_REQUIRED,
                "Approval was consumed concurrently",
            )

    # --- dispatch to the trusted executor with a signed, frozen snapshot
    from ppswitch_executor.plan_signing import sign_plan

    signature = sign_plan(plan_payload, settings.internal_token)
    invocation.status = InvocationStatus.RUNNING.value
    session.flush()  # the in-memory plan is frozen before dispatch (SEC-017)

    try:
        response = executor_client.post(
            f"{settings.executor_url}/internal/v1/execute",
            json={"plan": plan_payload, "signature": signature},
            headers={"X-Internal-Token": settings.internal_token},
        )
    except httpx.HTTPError as exc:
        invocation.status = InvocationStatus.FAILED.value
        invocation.error_code = ErrorCode.UPSTREAM_FAILURE.name
        _audit(session, workspace_id, principal_id, "invocation.dispatch", "failed",
               {"capability": capability.name}, invocation_id=invocation.id)
        session.commit()  # persist the failed invocation record
        raise ApiError(ErrorCode.UPSTREAM_FAILURE, "Executor unreachable") from exc

    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code == 200 and body:
        invocation.status = InvocationStatus.SUCCEEDED.value
        invocation.result = body.get("result")
    else:
        error = body.get("error", {})
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

    from ppswitch_db.models import utcnow

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
