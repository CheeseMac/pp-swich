"""FEAT-04 database targets: lifecycle, discovery import and tool specs.

A ``db_target`` is the SEPARATE approval type for database access
(docs/38 section 1): creating one is member-grade, approving it is
reviewer-grade (creator cannot self-approve outside demo mode), and it
authorizes exactly (host, port, database, allowed_schemas) - never HTTP
egress. Credentials never appear here: the human stores the read-only
account in the OS keyring (``ppswitch-secret put --scheme postgres``) and
references it from a Connection, exactly like FEAT-03.

The execution mapping for database capabilities is a STRUCTURED spec, not
SQL: ``{"db": {"target_id", "schema", "view", "columns", "filters",
"limit"}}``. The control plane validates it against the approved target and
freezes the coordinates into the signed plan; the executor composes the
parameterized SELECT itself. No client - console, wizard, agent - can ever
submit SQL text (red line: no ``execute_any_sql``-equivalent surface).
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

import httpx
from ppswitch_core.entities import DatabaseTarget, InvocationPlanDbQuery
from ppswitch_core.enums import AuthScheme, DatabaseTargetStatus
from ppswitch_core.errors import ApiError, ErrorCode
from ppswitch_db.models import (
    AuditEventRow,
    ConnectionRow,
    DatabaseTargetRow,
    ServiceRow,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

_DB_SPEC_KEYS = {"target_id", "schema", "view", "columns", "filters", "limit"}


def _validate_target_entity(
    *, engine: str, host: str, port: int, database: str, allowed_schemas: list[str]
) -> None:
    try:
        # shape validation only - the row below carries the real ids
        DatabaseTarget(
            workspace_id=UUID(int=0),
            service_id=UUID(int=0),
            engine=engine,
            host=host,
            port=port,
            database=database,
            allowed_schemas=allowed_schemas,
        )
    except ValueError as exc:
        raise ApiError(ErrorCode.BAD_REQUEST, str(exc)) from exc


def create_db_target(
    session: Session,
    *,
    workspace_id: UUID,
    principal_id: UUID,
    service_id: UUID,
    engine: str,
    host: str,
    port: int,
    database: str,
    allowed_schemas: list[str],
) -> DatabaseTargetRow:
    service = session.get(ServiceRow, service_id)
    if service is None or service.workspace_id != workspace_id:
        raise ApiError(ErrorCode.SERVICE_NOT_FOUND, "Service not found in this workspace")
    if engine != "postgres":
        raise ApiError(ErrorCode.BAD_REQUEST, "Only the postgres engine is supported")
    host = host.strip().lower()
    _validate_target_entity(
        engine=engine, host=host, port=port, database=database,
        allowed_schemas=allowed_schemas,
    )
    duplicate = session.scalars(
        select(DatabaseTargetRow).where(
            DatabaseTargetRow.workspace_id == workspace_id,
            DatabaseTargetRow.service_id == service_id,
            DatabaseTargetRow.engine == engine,
            DatabaseTargetRow.host == host,
            DatabaseTargetRow.port == port,
            DatabaseTargetRow.database == database,
        )
    ).one_or_none()
    if duplicate is not None:
        raise ApiError(ErrorCode.CONFLICT, "This database target already exists")
    target = DatabaseTargetRow(
        workspace_id=workspace_id,
        service_id=service_id,
        engine=engine,
        host=host,
        port=port,
        database=database,
        allowed_schemas=allowed_schemas,
        created_by=principal_id,
        status="pending",
    )
    session.add(target)
    session.flush()
    session.add(
        AuditEventRow(
            workspace_id=workspace_id,
            actor_id=principal_id,
            action="dbtarget.create",
            decision="allowed",
            redacted_metadata={
                "service_id": str(service_id),
                "engine": engine,
                "host": host,
                "port": port,
                "database": database,
                "allowed_schemas": allowed_schemas,
                "db_target_id": str(target.id),
            },
        )
    )
    return target


def _get_target(session: Session, workspace_id: UUID, target_id: UUID) -> DatabaseTargetRow:
    target = session.get(DatabaseTargetRow, target_id)
    if target is None or target.workspace_id != workspace_id:
        raise ApiError(ErrorCode.DATABASE_TARGET_NOT_FOUND, "Database target not found")
    return target


def approve_db_target(
    session: Session,
    *,
    workspace_id: UUID,
    principal_id: UUID,
    target_id: UUID,
    demo_mode: bool,
) -> DatabaseTargetRow:
    """Reviewer-grade approval; the creator cannot self-approve outside
    demo mode (same posture as HTTP endpoints, PR-SEC-01)."""
    target = _get_target(session, workspace_id, target_id)
    if target.status != "pending":
        raise ApiError(ErrorCode.CONFLICT, f"Database target already {target.status}")
    if (
        not demo_mode
        and target.created_by is not None
        and target.created_by == principal_id
    ):
        raise ApiError(
            ErrorCode.DATABASE_TARGET_NOT_APPROVABLE,
            "Database target approval requires a principal other than the creator",
        )
    target.status = "approved"
    session.add(
        AuditEventRow(
            workspace_id=workspace_id,
            actor_id=principal_id,
            action="dbtarget.approve",
            decision="allowed",
            redacted_metadata={"db_target_id": str(target_id)},
        )
    )
    return target


def disable_db_target(
    session: Session,
    *,
    workspace_id: UUID,
    principal_id: UUID,
    target_id: UUID,
) -> DatabaseTargetRow:
    """Revocation: every plan built after this is rejected at plan-build
    time (acceptance item 4 - immediate effect, fail closed)."""
    target = _get_target(session, workspace_id, target_id)
    if target.status == "disabled":
        raise ApiError(ErrorCode.CONFLICT, "Database target already disabled")
    target.status = "disabled"
    session.add(
        AuditEventRow(
            workspace_id=workspace_id,
            actor_id=principal_id,
            action="dbtarget.disable",
            decision="allowed",
            redacted_metadata={"db_target_id": str(target_id)},
        )
    )
    return target


def caller_postgres_connection(
    session: Session, *, workspace_id: UUID, principal_id: UUID, service_id: UUID
) -> ConnectionRow:
    """The caller's ACTIVE postgres connection on this service (the
    FEAT-03 keyring path; activation already proved the read-only role)."""
    connection = session.scalars(
        select(ConnectionRow).where(
            ConnectionRow.workspace_id == workspace_id,
            ConnectionRow.service_id == service_id,
            ConnectionRow.owner_principal_id == principal_id,
            ConnectionRow.auth_scheme == AuthScheme.POSTGRES.value,
            ConnectionRow.status == "active",
        )
    ).first()
    if connection is None:
        raise ApiError(
            ErrorCode.CONNECTION_NOT_FOUND,
            "No active postgres connection for this service; store the read-only "
            "account with ppswitch-secret and activate the connection first",
        )
    return connection


def discover_db_metadata(
    session: Session,
    *,
    workspace_id: UUID,
    principal_id: UUID,
    target_id: UUID,
    settings,
    executor_client: httpx.Client,
) -> dict[str, Any]:
    """Ask the trusted executor for the approved schemas' metadata.

    The executor both restricts the query to ``allowed_schemas`` AND relies
    on the read-only role's visibility, so unapproved schemas cannot be
    discovered (acceptance item 1). Only column names/types return.
    """
    target = _get_target(session, workspace_id, target_id)
    if target.status != DatabaseTargetStatus.APPROVED.value:
        raise ApiError(ErrorCode.FORBIDDEN, "Database target is not approved")
    connection = caller_postgres_connection(
        session,
        workspace_id=workspace_id,
        principal_id=principal_id,
        service_id=target.service_id,
    )
    # SECRET-06: discovery resolves keyring material too - it freezes the
    # same claim context and presents a purpose=discover ticket
    from ppswitch_control.services.keyring_claim_service import (
        resolve_claim_context,
        sign_discover_ticket,
    )

    claim_context = resolve_claim_context(
        session, settings=settings, connection=connection
    )
    assert claim_context is not None  # caller_postgres_connection is keyring-backed
    discover_ticket = sign_discover_ticket(settings=settings, claim=claim_context)
    try:
        response = executor_client.post(
            f"{settings.executor_url}/internal/v1/db/discover",
            json={
                "secret_ref": connection.secret_ref,
                "target": {
                    "host": target.host,
                    "port": target.port,
                    "database": target.database,
                    "allowed_schemas": target.allowed_schemas or [],
                },
                "claim_ticket": discover_ticket,
            },
            headers={"X-Internal-Token": settings.internal_token},
        )
    except httpx.HTTPError as exc:
        raise ApiError(ErrorCode.UPSTREAM_FAILURE, "Executor unreachable") from exc
    if response.status_code != 200:
        try:
            message = response.json().get("error", {}).get("message", "")
        except ValueError:
            message = ""
        raise ApiError(
            ErrorCode.UPSTREAM_FAILURE,
            message or "Database discovery failed on the trusted side",
        )
    document = response.json()
    session.add(
        AuditEventRow(
            workspace_id=workspace_id,
            actor_id=principal_id,
            action="dbtarget.discover",
            decision="allowed",
            redacted_metadata={
                "db_target_id": str(target_id),
                "objects": len(document.get("objects", [])),
                "truncated": bool(document.get("truncated")),
            },
        )
    )
    return document


# ---------------------------------------------------------------------------
# structured db execution specs (capability.execution["db"])
# ---------------------------------------------------------------------------


def validate_db_execution_spec(spec: Any) -> dict[str, Any]:
    """Validate the STRUCTURED db execution spec; reject anything beyond
    the fixed keys - in particular any SQL-like content (red line)."""
    if not isinstance(spec, dict):
        raise ApiError(ErrorCode.BAD_REQUEST, "db execution must be an object")
    unknown = sorted(set(spec) - _DB_SPEC_KEYS)
    if unknown:
        raise ApiError(
            ErrorCode.BAD_REQUEST,
            "db execution carries unsupported keys; only structured fields are allowed",
            {"unknown_keys": unknown},
        )
    try:
        target_id = UUID(str(spec.get("target_id", "")))
    except ValueError as exc:
        raise ApiError(ErrorCode.BAD_REQUEST, "db execution target_id must be a UUID") from exc
    schema = str(spec.get("schema", ""))
    view = str(spec.get("view", ""))
    columns = spec.get("columns")
    filters = spec.get("filters", {})
    limit = spec.get("limit", 2)
    if not _IDENTIFIER.match(schema) or not _IDENTIFIER.match(view):
        raise ApiError(
            ErrorCode.BAD_REQUEST, "db execution schema/view must be plain identifiers"
        )
    if (
        not isinstance(columns, list)
        or not 1 <= len(columns) <= 64
        or len(set(columns)) != len(columns)
        or any(not _IDENTIFIER.match(str(c)) for c in columns)
    ):
        raise ApiError(ErrorCode.BAD_REQUEST, "db execution columns are invalid")
    if not isinstance(filters, dict) or any(
        not _IDENTIFIER.match(str(k)) or not str(v) or len(str(v)) > 128
        for k, v in filters.items()
    ):
        raise ApiError(ErrorCode.BAD_REQUEST, "db execution filters are invalid")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 2:
        raise ApiError(ErrorCode.BAD_REQUEST, "db execution limit must be 1 or 2")
    if sorted(filters) and sorted(set(filters) - set(columns)):
        raise ApiError(
            ErrorCode.BAD_REQUEST, "db execution filters must reference selected columns"
        )
    return {
        "target_id": str(target_id),
        "schema": schema,
        "view": view,
        "columns": [str(c) for c in columns],
        "filters": {str(k): str(v) for k, v in filters.items()},
        "limit": limit,
    }


def resolve_db_query(
    session: Session,
    *,
    workspace_id: UUID,
    spec: Any,
    service_id: UUID,
    connection: ConnectionRow,
) -> InvocationPlanDbQuery:
    """Freeze a validated db spec into the signed plan's query block.

    Fail-closed on every lifecycle gap: the target must exist in this
    workspace, belong to the capability's service, be APPROVED (a disabled
    or revoked target rejects the call here - acceptance item 4), and the
    bound connection must be an active postgres keyring connection.
    """
    clean = validate_db_execution_spec(spec)
    target = session.get(DatabaseTargetRow, UUID(clean["target_id"]))
    if (
        target is None
        or target.workspace_id != workspace_id
        or target.service_id != service_id
    ):
        raise ApiError(
            ErrorCode.DATABASE_TARGET_NOT_FOUND,
            "Database target not found for this capability's service",
        )
    if target.status != DatabaseTargetStatus.APPROVED.value:
        raise ApiError(ErrorCode.FORBIDDEN, "Database target is not approved")
    if connection.auth_scheme != AuthScheme.POSTGRES.value:
        raise ApiError(
            ErrorCode.FORBIDDEN,
            "Database tools require an active postgres connection",
        )
    if clean["schema"] not in (target.allowed_schemas or []):
        raise ApiError(
            ErrorCode.DATABASE_QUERY_REJECTED,
            "Database schema is not in the approved target allowlist",
        )
    return InvocationPlanDbQuery(
        host=target.host,
        port=target.port,
        database=target.database,
        allowed_schemas=list(target.allowed_schemas or []),
        schema_name=clean["schema"],
        view=clean["view"],
        columns=clean["columns"],
        param_map=clean["filters"],
        fetch_limit=clean["limit"],
    )
