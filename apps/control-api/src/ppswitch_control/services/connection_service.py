"""Connection and profile management with one-click active binding switch."""

from __future__ import annotations

import re
from uuid import UUID

import httpx
from ppswitch_core.entities import Profile, validate_profile_alignment
from ppswitch_core.enums import AuthScheme, ConnectionStatus, EndpointStatus
from ppswitch_core.errors import ApiError, ErrorCode
from ppswitch_db.models import (
    ActiveBindingRow,
    AuditEventRow,
    ConnectionRow,
    EndpointRow,
    ProfileRow,
    ServiceRow,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

_KEYRING_NAME = re.compile(r"^[a-z0-9][a-z0-9/_-]{2,63}$")


def create_connection(
    session: Session,
    *,
    workspace_id: UUID,
    principal_id: UUID,
    service_id: UUID,
    auth_scheme: str,
    scopes: list[str],
    demo_dataset: str | None,
    secret_name: str | None,
    settings,
) -> ConnectionRow:
    service = session.get(ServiceRow, service_id)
    if service is None or service.workspace_id != workspace_id:
        raise ApiError(ErrorCode.SERVICE_NOT_FOUND, "Service not found in this workspace")

    connection = ConnectionRow(
        workspace_id=workspace_id,
        service_id=service_id,
        owner_principal_id=principal_id,
        auth_scheme=auth_scheme,
        secret_ref="",  # set below after the id exists
        scopes=scopes,
        status="pending",
    )
    session.add(connection)
    session.flush()
    if secret_name:
        # FEAT-03: a REAL local credential stored by the human in the OS
        # keyring via `ppswitch-secret put <name>`; the value never touches
        # the control plane. The connection starts PENDING and must be
        # activated, which asks the executor to prove the ref resolves.
        if not _KEYRING_NAME.match(secret_name):
            raise ApiError(
                ErrorCode.BAD_REQUEST,
                "secret_name must match [a-z0-9][a-z0-9/_-]{2,63}",
            )
        connection.secret_ref = f"keyring://{secret_name}"
        connection.status = "pending"
    elif settings.demo_mode:
        # DEV-ONLY deterministic provisioning: the dataset scope is encoded
        # in the opaque ref and the executor derives a synthetic MOCKKEY from
        # it. No secret value ever transits the control plane; production
        # provisions real credentials out-of-band (OpenBao / OS keyring).
        dataset = demo_dataset or "A"
        if dataset not in ("A", "B"):
            raise ApiError(ErrorCode.BAD_REQUEST, "demo_dataset must be 'A' or 'B'")
        connection.secret_ref = f"mock://connections/{connection.id}?dataset={dataset}"
        connection.status = "active"
    else:
        connection.secret_ref = f"mock://connections/{connection.id}"
        connection.status = "pending"

    session.add(
        AuditEventRow(
            workspace_id=workspace_id,
            actor_id=principal_id,
            action="connection.create",
            decision="allowed",
            redacted_metadata={"service_id": str(service_id), "auth_scheme": auth_scheme},
        )
    )
    return connection


def create_profile(
    session: Session,
    *,
    workspace_id: UUID,
    principal_id: UUID,
    service_id: UUID,
    endpoint_id: UUID,
    connection_id: UUID,
    name: str,
) -> ProfileRow:
    service = session.get(ServiceRow, service_id)
    endpoint = session.get(EndpointRow, endpoint_id)
    connection = session.get(ConnectionRow, connection_id)
    if any(x is None for x in (service, endpoint, connection)):
        raise ApiError(ErrorCode.PROFILE_NOT_FOUND, "Referenced objects not found")
    assert service and endpoint and connection
    # review PR-SEC-01 (SEC-021): a profile may only bind a Connection
    # owned by the creating principal - no cross-user assembly
    if connection.owner_principal_id != principal_id:
        raise ApiError(
            ErrorCode.PROFILE_NOT_ALLOWED,
            "Connection belongs to another principal",
        )

    domain_profile = Profile(
        workspace_id=workspace_id, service_id=service_id, endpoint_id=endpoint_id,
        connection_id=connection_id, owner_principal_id=principal_id, name=name,
    )
    try:
        validate_profile_alignment(
            domain_profile,
            EndpointRow_to_entity(endpoint),
            ConnectionRow_to_entity(connection),
            ServiceRow_to_entity(service),
        )
    except ValueError as exc:
        raise ApiError(ErrorCode.BAD_REQUEST, str(exc)) from exc

    duplicate = session.scalars(
        select(ProfileRow).where(
            ProfileRow.workspace_id == workspace_id,
            ProfileRow.owner_principal_id == principal_id,
            ProfileRow.service_id == service_id,
            ProfileRow.name == name,
        )
    ).one_or_none()
    if duplicate is not None:
        raise ApiError(ErrorCode.CONFLICT, "Profile name already exists for this service")

    profile = ProfileRow(
        workspace_id=workspace_id,
        service_id=service_id,
        endpoint_id=endpoint_id,
        connection_id=connection_id,
        owner_principal_id=principal_id,
        name=name,
        status="enabled",
    )
    session.add(profile)
    session.flush()
    session.add(
        AuditEventRow(
            workspace_id=workspace_id,
            actor_id=principal_id,
            action="profile.create",
            decision="allowed",
            redacted_metadata={"service_id": str(service_id), "name": name},
        )
    )
    return profile


def EndpointRow_to_entity(row: EndpointRow):
    from ppswitch_core.entities import Endpoint

    return Endpoint(
        id=row.id, workspace_id=row.workspace_id, service_id=row.service_id,
        environment=row.environment, base_url=row.base_url,
        approved_hosts=row.approved_hosts or [],
        status=EndpointStatus(row.status),
    )


def ConnectionRow_to_entity(row: ConnectionRow):
    from ppswitch_core.entities import Connection as ConnectionEntity

    return ConnectionEntity(
        id=row.id, workspace_id=row.workspace_id, service_id=row.service_id,
        owner_principal_id=row.owner_principal_id,
        auth_scheme=AuthScheme(row.auth_scheme),
        secret_ref=row.secret_ref, scopes=row.scopes or [],
        status=ConnectionStatus(row.status),
    )


def ServiceRow_to_entity(row: ServiceRow):
    from ppswitch_core.entities import Service

    return Service(
        id=row.id, workspace_id=row.workspace_id, slug=row.slug, name=row.name,
        owner_principal_id=row.owner_principal_id,
    )


def activate_binding(
    session: Session,
    *,
    workspace_id: UUID,
    caller_principal_id: UUID,
    service_id: UUID,
    context_key: str,
    profile_id: UUID,
    expected_version: int,
    agent_principal_id: UUID | None = None,
) -> ActiveBindingRow:
    """One-click switch with optimistic concurrency.

    Permission rules (SEC-005):
    - profile in another workspace -> 404 (existence not revealed);
    - profile owned by someone else in this workspace -> 403;
    - disabled profile / inactive connection / unapproved endpoint -> 403.

    Agent contexts (P0-2 / SEC-033): the binding row lives in the
    CALLING user's own scope under the full ``agent:<id>`` context key.
    Two users delegating to the same agent therefore maintain separate
    bindings and can never overwrite each other. The route layer already
    verified an active delegation to ``agent_principal_id``.
    """
    binding_principal_id = caller_principal_id
    stored_context = context_key
    if context_key.startswith("agent:"):
        if agent_principal_id is None:
            raise ApiError(ErrorCode.BAD_REQUEST, "Unknown agent principal for context")

    profile = session.get(ProfileRow, profile_id)
    if profile is None or profile.workspace_id != workspace_id:
        raise ApiError(ErrorCode.PROFILE_NOT_FOUND, "Profile not found")
    if profile.owner_principal_id != caller_principal_id:
        raise ApiError(ErrorCode.PROFILE_NOT_ALLOWED, "Profile belongs to another principal")
    if profile.service_id != service_id:
        raise ApiError(ErrorCode.BAD_REQUEST, "Profile belongs to a different service")
    if profile.status != "enabled":
        raise ApiError(ErrorCode.PROFILE_NOT_ALLOWED, "Profile is disabled")

    connection = session.get(ConnectionRow, profile.connection_id)
    endpoint = session.get(EndpointRow, profile.endpoint_id)
    if connection is None or connection.status != "active":
        raise ApiError(ErrorCode.PROFILE_NOT_ALLOWED, "Connection is not active")
    # execution-time re-check (PR-SEC-01): the bound profile's
    # connection must still belong to the profile owner
    if connection.owner_principal_id != profile.owner_principal_id:
        raise ApiError(ErrorCode.PROFILE_NOT_ALLOWED, "Connection ownership mismatch")
    if endpoint is None or endpoint.status != "approved":
        raise ApiError(ErrorCode.PROFILE_NOT_ALLOWED, "Endpoint is not approved")
    if endpoint.service_id != profile.service_id or connection.service_id != profile.service_id:
        raise ApiError(ErrorCode.PROFILE_NOT_ALLOWED, "Profile references misaligned objects")

    existing = session.get(
        ActiveBindingRow,
        {
            "workspace_id": workspace_id,
            "principal_id": binding_principal_id,
            "service_id": service_id,
            "context_key": stored_context,
        },
    )
    if existing is None:
        if expected_version != 0:
            raise ApiError(
                ErrorCode.PROFILE_VERSION_CONFLICT,
                "No active binding yet; send expected_version=0",
            )
        binding = ActiveBindingRow(
            workspace_id=workspace_id,
            principal_id=binding_principal_id,
            service_id=service_id,
            context_key=stored_context,
            active_profile_id=profile_id,
            version=1,
            updated_by=caller_principal_id,
        )
        session.add(binding)
        session.flush()
    else:
        from sqlalchemy import text

        result = session.execute(
            text(
                "UPDATE active_bindings SET active_profile_id = :p, "
                "version = version + 1, updated_by = :u "
                "WHERE workspace_id = :w AND principal_id = :pr AND service_id = :s "
                "AND context_key = :c AND version = :v"
            ),
            {
                "p": profile_id.hex, "u": caller_principal_id.hex,
                "w": workspace_id.hex, "pr": binding_principal_id.hex,
                "s": service_id.hex, "c": stored_context, "v": expected_version,
            },
        )
        if getattr(result, "rowcount", 0) == 0:
            raise ApiError(
                ErrorCode.PROFILE_VERSION_CONFLICT,
                "Active binding was modified concurrently; reload and retry",
            )
        refreshed = session.get(
            ActiveBindingRow,
            {
                "workspace_id": workspace_id,
                "principal_id": binding_principal_id,
                "service_id": service_id,
                "context_key": stored_context,
            },
        )
        assert refreshed is not None
        session.refresh(refreshed)  # raw UPDATE bypasses the identity map
        binding = refreshed

    session.add(
        AuditEventRow(
            workspace_id=workspace_id,
            actor_id=caller_principal_id,
            action="profile.activate",
            decision="allowed",
            redacted_metadata={
                "service_id": str(service_id),
                "context_key": context_key,
                "binding_principal_id": str(binding_principal_id),
                "profile_id": str(profile_id),
                "version": binding.version,
            },
        )
    )
    return binding


def activate_connection(
    session: Session,
    *,
    workspace_id: UUID,
    principal_id: UUID,
    connection_id: UUID,
    settings,
    executor_client,
) -> ConnectionRow:
    """FEAT-03: activate a pending keyring connection.

    Asks the trusted executor to RESOLVE the secret_ref (it returns only
    a fingerprint, never material). Fail-closed: nothing activates while
    the store is unreachable or the secret is missing.
    """
    connection = session.get(ConnectionRow, connection_id)
    if (
        connection is None
        or connection.workspace_id != workspace_id
        # review 2026-10-16: connections are per-owner; a non-owner in
        # the same workspace must not learn the resource exists
        or connection.owner_principal_id != principal_id
    ):
        raise ApiError(ErrorCode.CONNECTION_NOT_FOUND, "Connection not found")
    if connection.status != "pending":
        raise ApiError(ErrorCode.CONFLICT, f"Connection already {connection.status}")
    try:
        response = executor_client.post(
            f"{settings.executor_url}/internal/v1/secrets/verify",
            json={"secret_ref": connection.secret_ref, "auth_scheme": connection.auth_scheme},
            headers={"X-Internal-Token": settings.internal_token},
        )
    except httpx.HTTPError as exc:
        raise ApiError(ErrorCode.SECRET_PROVIDER_UNAVAILABLE, "Executor unreachable") from exc
    body = response.json() if response.status_code == 200 else {}
    if not body.get("ok"):
        raise ApiError(
            ErrorCode.SECRET_PROVIDER_UNAVAILABLE,
            "Secret store did not resolve this connection's secret_ref",
            {"detail": body.get("detail", "unavailable")},
        )
    connection.status = "active"
    session.add(
        AuditEventRow(
            workspace_id=workspace_id,
            actor_id=principal_id,
            action="connection.activate",
            decision="allowed",
            redacted_metadata={
                "connection_id": str(connection_id),
                "fingerprint": body.get("fingerprint"),
            },
        )
    )
    return connection


def revoke_connection(
    session: Session,
    *,
    workspace_id: UUID,
    principal_id: UUID,
    connection_id: UUID,
) -> ConnectionRow:
    """FEAT-03: revoke a connection; the policy engine denies its calls."""
    connection = session.get(ConnectionRow, connection_id)
    if (
        connection is None
        or connection.workspace_id != workspace_id
        or connection.owner_principal_id != principal_id
    ):
        raise ApiError(ErrorCode.CONNECTION_NOT_FOUND, "Connection not found")
    if connection.status == "revoked":
        raise ApiError(ErrorCode.CONFLICT, "Connection already revoked")
    connection.status = "revoked"
    session.add(
        AuditEventRow(
            workspace_id=workspace_id,
            actor_id=principal_id,
            action="connection.revoke",
            decision="allowed",
            redacted_metadata={"connection_id": str(connection_id)},
        )
    )
    return connection
