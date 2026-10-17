"""FEAT-02: live test of a discovered operation through the active profile.

The publish wizard needs a "try it before you publish" step that does not
require creating a capability first. This service routes one request
through the SAME trusted path as a real invocation - active profile
resolution, approved endpoint, signed plan, executor with network policy
and response guard - but creates no InvocationRow and consumes no grant:

- human principals only (the permission matrix maps the route to
  ``operations.test``, which agents never receive);
- arguments are whitelisted against the operation's OWN inferred
  contract: path placeholders plus observed query keys plus observed
  body fields. Unknown or non-scalar argument names are rejected, so a
  test call cannot smuggle arbitrary request material;
- identity execution maps are generated from the inferred contract
  (empty maps would send no query/body at all - the executor only sends
  parameters that appear in its templates);
- every attempt is audited (``operation.test``).

This is deliberately NOT a capability invocation: it exercises observed
evidence before review, so it can never be granted to an agent and the
response is shown only to the human in the console.
"""

from __future__ import annotations

import json
import re
import time
import uuid as uuid_module
from typing import Any
from uuid import UUID

import httpx
from ppswitch_core.entities import (
    AuthScheme,
    InvocationPlan,
    InvocationPlanOperation,
)
from ppswitch_core.enums import RiskLevel
from ppswitch_core.errors import ApiError, ErrorCode
from ppswitch_db.models import (
    ActiveBindingRow,
    AuditEventRow,
    ConnectionRow,
    EndpointRow,
    OperationRow,
    OperationVersionRow,
    ProfileRow,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

PLAN_DEADLINE_S = 30.0
MAX_TEST_BODY_CHARS = 8 * 1024  # console shows an excerpt, not a firehose

_PLACEHOLDER = re.compile(r"\{([^}]+)\}")


def _arguments_digest(arguments: dict) -> str:
    import hashlib

    return hashlib.sha256(
        json.dumps(arguments, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _contract_parameters(operation: OperationRow, input_schema: dict) -> dict:
    """Allowed argument names, derived from the observed contract."""
    path_params = _PLACEHOLDER.findall(operation.normalized_path or "")
    query_keys = sorted((input_schema.get("query") or {}).get("properties", {}))
    body_fields = sorted((input_schema.get("body") or {}).get("properties", {}))
    return {
        "path": list(dict.fromkeys(path_params)),
        "query": query_keys,
        "body": body_fields,
    }


def _validate_arguments(arguments: dict, params: dict) -> None:
    allowed = set(params["path"]) | set(params["query"]) | set(params["body"])
    unknown = sorted(set(arguments) - allowed)
    if unknown:
        raise ApiError(
            ErrorCode.SCHEMA_VIOLATION,
            "Argument names must come from the observed contract",
            {"unknown_arguments": unknown[:10], "allowed": sorted(allowed)[:50]},
        )
    for name, value in arguments.items():
        if value is not None and not isinstance(value, (str, int, float, bool)):
            raise ApiError(
                ErrorCode.SCHEMA_VIOLATION,
                "Test arguments must be scalar values",
                {"argument": name},
            )
    missing = [p for p in params["path"] if p not in arguments or arguments[p] in (None, "")]
    if missing:
        raise ApiError(
            ErrorCode.SCHEMA_VIOLATION,
            "Path parameters are required for a test call",
            {"missing": missing},
        )


def test_operation(
    session: Session,
    *,
    workspace_id: UUID,
    principal_id: UUID,
    operation_id: UUID,
    arguments: dict,
    settings,
    executor_client: httpx.Client,
    db_execution: dict | None = None,
) -> dict:
    operation = session.get(OperationRow, operation_id)
    if operation is None or operation.workspace_id != workspace_id:
        raise ApiError(ErrorCode.OPERATION_NOT_FOUND, "Operation not found")

    version_row = session.scalars(
        select(OperationVersionRow)
        .where(OperationVersionRow.operation_id == operation_id)
        .order_by(OperationVersionRow.version.desc())
    ).first()
    if version_row is None:
        raise ApiError(ErrorCode.OPERATION_NOT_FOUND, "Operation has no contract version yet")

    is_db_operation = (operation.normalized_path or "").startswith("/db/")

    # active profile for THIS user on the operation's service (personal
    # context - the same resolution a real invocation would use)
    binding = session.get(
        ActiveBindingRow,
        {
            "workspace_id": workspace_id,
            "principal_id": principal_id,
            "service_id": operation.service_id,
            "context_key": "personal",
        },
    )
    if binding is None:
        raise ApiError(
            ErrorCode.NO_ACTIVE_PROFILE,
            "No active profile for this service; activate a profile first",
        )
    profile = session.get(ProfileRow, binding.active_profile_id)
    connection = (
        session.get(ConnectionRow, profile.connection_id) if profile else None
    )
    endpoint = (
        session.get(EndpointRow, profile.endpoint_id) if profile and profile.endpoint_id else None
    )
    if profile is None or connection is None:
        raise ApiError(ErrorCode.NO_ACTIVE_PROFILE, "Bound profile is inconsistent")
    if connection.status != "active":
        raise ApiError(
            ErrorCode.FORBIDDEN,
            "Test calls require an approved endpoint and an active connection",
        )

    # SECRET-06: the test path freezes the same claim context a real
    # invocation would (un-pinned keyring credentials never test-execute)
    from ppswitch_control.services.keyring_claim_service import (
        resolve_claim_context,
    )

    claim_context = resolve_claim_context(
        session, settings=settings, connection=connection
    )

    if is_db_operation:
        return _test_db_operation(
            session,
            workspace_id=workspace_id,
            principal_id=principal_id,
            operation=operation,
            version_row=version_row,
            connection=connection,
            arguments=arguments or {},
            db_execution=db_execution,
            settings=settings,
            executor_client=executor_client,
            claim_context=claim_context,
        )
    if endpoint is None or endpoint.status != "approved":
        raise ApiError(
            ErrorCode.FORBIDDEN,
            "Test calls require an approved endpoint and an active connection",
        )

    params = _contract_parameters(operation, version_row.input_schema or {})
    try:
        _validate_arguments(arguments or {}, params)
    except ApiError:
        session.add(
            AuditEventRow(
                workspace_id=workspace_id, actor_id=principal_id,
                action="operation.test", decision="denied",
                redacted_metadata={
                    "operation_id": str(operation_id),
                    "reason": "schema_violation",
                },
            )
        )
        session.commit()  # keep the deny audit even though we 422
        raise

    # identity execution maps: the executor only sends parameters that
    # appear in its templates, so observed query keys and body fields map
    # to arguments of the same name
    execution_query = {name: name for name in params["query"]}
    execution_body = {name: name for name in params["body"]}

    plan = InvocationPlan(
        invocation_id=uuid_module.uuid4(),
        capability_name=f"__test__{operation.method}_{operation.normalized_path}",
        capability_version="test",
        capability_version_id=uuid_module.UUID(int=0),
        operation=InvocationPlanOperation(
            method=operation.method,
            path_template=operation.normalized_path,
            path_map={name: name for name in params["path"]},
            query_template=execution_query,
            body_template=execution_body,
        ),
        endpoint_base_url=endpoint.base_url,
        approved_hosts=endpoint.approved_hosts or [],
        auth_scheme=AuthScheme(connection.auth_scheme),
        secret_ref=connection.secret_ref,
        allow_fields=[],
        input_schema=version_row.input_schema or {},
        output_schema=version_row.output_schema or {},
        arguments=arguments or {},
        workspace_id=workspace_id,
        principal_id=principal_id,
        agent_id=None,
        risk_level=RiskLevel.LOW,
        policy_version="operation-test/1",
        arguments_digest=_arguments_digest(arguments or {}),
        idempotency_key=None,
        deadline_epoch_s=time.time() + PLAN_DEADLINE_S,
        nonce=uuid_module.uuid4().hex + uuid_module.uuid4().hex[:8],
        claim=claim_context,
    )
    plan_payload = json.loads(plan.model_dump_json())

    from ppswitch_executor.plan_signing import sign_plan

    signature = sign_plan(plan_payload, settings.effective_plan_signing_key)
    try:
        response = executor_client.post(
            f"{settings.executor_url}/internal/v1/execute",
            json={"plan": plan_payload, "signature": signature},
            headers={"X-Internal-Token": settings.internal_token},
        )
    except httpx.HTTPError as exc:
        session.add(
            AuditEventRow(
                workspace_id=workspace_id, actor_id=principal_id,
                action="operation.test", decision="denied",
                redacted_metadata={
                    "operation_id": str(operation_id),
                    "reason": "executor_unreachable",
                },
            )
        )
        session.commit()  # keep the deny audit even though we 502
        raise ApiError(ErrorCode.UPSTREAM_FAILURE, "Executor unreachable") from exc
    body: dict[str, Any] = {}
    try:
        body = response.json()
    except ValueError:
        body = {}

    ok = response.status_code == 200
    result = body.get("result")
    excerpt = result if result is not None else body.get("error")
    if isinstance(excerpt, str):
        excerpt = excerpt[:MAX_TEST_BODY_CHARS]
    elif excerpt is not None:
        encoded = json.dumps(excerpt, ensure_ascii=False, default=str)
        if len(encoded) > MAX_TEST_BODY_CHARS:
            excerpt = {"_truncated": encoded[:MAX_TEST_BODY_CHARS]}
    else:
        excerpt = {"raw_status": response.status_code}

    session.add(
        AuditEventRow(
            workspace_id=workspace_id, actor_id=principal_id,
            action="operation.test", decision="allowed" if ok else "denied",
            redacted_metadata={
                "operation_id": str(operation_id),
                "executor_status": response.status_code,
            },
        )
    )
    return {
        "ok": ok,
        "executor_status": response.status_code,
        "error_code": None if ok else body.get("error", {}).get("code"),
        "result": excerpt,
    }


def _test_db_operation(
    session: Session,
    *,
    workspace_id: UUID,
    principal_id: UUID,
    operation: OperationRow,
    version_row: OperationVersionRow,
    connection: ConnectionRow,
    arguments: dict,
    db_execution: dict | None,
    settings,
    executor_client: httpx.Client,
    claim_context=None,
) -> dict:
    """FEAT-04: live test of a discovered db operation.

    The wizard supplies the STRUCTURED spec it configured (columns +
    filters); the server validates it exactly like a published capability
    (approved target, schema allowlist, active postgres connection) and
    composes no SQL of its own - the executor does. All filter arguments
    are required: an equality filter without a value has no meaning.
    """
    from ppswitch_control.services.db_target_service import (
        resolve_db_query,
        validate_db_execution_spec,
    )

    if not isinstance(db_execution, dict):
        raise ApiError(
            ErrorCode.BAD_REQUEST,
            "Testing a database operation requires the wizard's db_execution spec",
        )
    try:
        spec = validate_db_execution_spec(db_execution)
        db_query = resolve_db_query(
            session,
            workspace_id=workspace_id,
            spec=spec,
            service_id=operation.service_id,
            connection=connection,
        )
    except ApiError:
        session.add(
            AuditEventRow(
                workspace_id=workspace_id, actor_id=principal_id,
                action="operation.test", decision="denied",
                redacted_metadata={
                    "operation_id": str(operation.id),
                    "reason": "db_spec_rejected",
                },
            )
        )
        session.commit()  # keep the deny audit even though we refuse
        raise

    allowed = sorted(set(spec["filters"].values()))
    unknown = sorted(set(arguments) - set(allowed))
    if unknown:
        raise ApiError(
            ErrorCode.SCHEMA_VIOLATION,
            "Argument names must come from the configured db filters",
            {"unknown_arguments": unknown[:10], "allowed": allowed},
        )
    for name, value in arguments.items():
        if value is not None and not isinstance(value, (str, int, float, bool)):
            raise ApiError(
                ErrorCode.SCHEMA_VIOLATION,
                "Test arguments must be scalar values",
                {"argument": name},
            )
    missing = [name for name in allowed if arguments.get(name) in (None, "")]
    if missing:
        raise ApiError(
            ErrorCode.SCHEMA_VIOLATION,
            "Database filter parameters are required for a test call",
            {"missing": missing},
        )

    # flat schema for the executor's argument re-validation, typed from the
    # operation's discovered contract where the name is known
    query_props = (version_row.input_schema or {}).get("query", {}).get("properties", {})
    properties = {
        name: dict(query_props.get(name) or {"type": "string"}) for name in allowed
    }
    flat_input_schema = {
        "type": "object",
        "properties": properties,
        "required": allowed,
        "additionalProperties": False,
    }

    plan = InvocationPlan(
        invocation_id=uuid_module.uuid4(),
        capability_name=f"__test__{operation.method}_{operation.normalized_path}",
        capability_version="test",
        capability_version_id=uuid_module.UUID(int=0),
        operation=InvocationPlanOperation(
            method=operation.method,
            path_template=operation.normalized_path,
        ),
        endpoint_base_url="",
        approved_hosts=[],
        auth_scheme=AuthScheme(connection.auth_scheme),
        secret_ref=connection.secret_ref,
        allow_fields=[],
        input_schema=flat_input_schema,
        output_schema=version_row.output_schema or {},
        arguments=arguments,
        workspace_id=workspace_id,
        principal_id=principal_id,
        agent_id=None,
        risk_level=RiskLevel.LOW,
        policy_version="operation-test/1",
        arguments_digest=_arguments_digest(arguments),
        idempotency_key=None,
        deadline_epoch_s=time.time() + PLAN_DEADLINE_S,
        nonce=uuid_module.uuid4().hex + uuid_module.uuid4().hex[:8],
        db_query=db_query,
        claim=claim_context,
    )
    plan_payload = json.loads(plan.model_dump_json())

    from ppswitch_executor.plan_signing import sign_plan

    signature = sign_plan(plan_payload, settings.effective_plan_signing_key)
    try:
        response = executor_client.post(
            f"{settings.executor_url}/internal/v1/execute",
            json={"plan": plan_payload, "signature": signature},
            headers={"X-Internal-Token": settings.internal_token},
        )
    except httpx.HTTPError as exc:
        session.add(
            AuditEventRow(
                workspace_id=workspace_id, actor_id=principal_id,
                action="operation.test", decision="denied",
                redacted_metadata={
                    "operation_id": str(operation.id),
                    "reason": "executor_unreachable",
                },
            )
        )
        session.commit()  # keep the deny audit even though we 502
        raise ApiError(ErrorCode.UPSTREAM_FAILURE, "Executor unreachable") from exc
    body: dict = {}
    try:
        body = response.json()
    except ValueError:
        body = {}

    ok = response.status_code == 200
    result = body.get("result")
    excerpt = result if result is not None else body.get("error")
    if isinstance(excerpt, str):
        excerpt = excerpt[:MAX_TEST_BODY_CHARS]
    elif excerpt is not None:
        encoded = json.dumps(excerpt, ensure_ascii=False, default=str)
        if len(encoded) > MAX_TEST_BODY_CHARS:
            excerpt = {"_truncated": encoded[:MAX_TEST_BODY_CHARS]}
    else:
        excerpt = {"raw_status": response.status_code}

    session.add(
        AuditEventRow(
            workspace_id=workspace_id, actor_id=principal_id,
            action="operation.test", decision="allowed" if ok else "denied",
            redacted_metadata={
                "operation_id": str(operation.id),
                "executor_status": response.status_code,
                "db": True,
            },
        )
    )
    return {
        "ok": ok,
        "executor_status": response.status_code,
        "error_code": None if ok else body.get("error", {}).get("code"),
        "result": excerpt,
    }
