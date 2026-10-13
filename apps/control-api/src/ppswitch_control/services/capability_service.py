"""Capability registry: review workflow and immutable publishing."""

from __future__ import annotations

import hashlib
import json
import uuid as uuid_module
from datetime import UTC, datetime
from uuid import UUID

from ppswitch_core.entities import Capability, CapabilityVersion
from ppswitch_core.enums import CapabilityStatus, CapabilityVersionStatus, RiskLevel
from ppswitch_core.errors import ApiError, ErrorCode
from ppswitch_core.state_machine import transition_capability
from ppswitch_db.models import (
    AuditEventRow,
    CapabilityRow,
    CapabilityVersionRow,
    OperationRow,
    OperationVersionRow,
)
from sqlalchemy import select
from sqlalchemy.orm import Session


def content_hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:64]


def create_capability(
    session: Session,
    *,
    workspace_id: UUID,
    principal_id: UUID,
    name: str,
    display_name: str,
    description: str,
    operation_id: UUID,
    input_schema: dict,
    output_schema: dict,
    allow_fields: list[str],
    risk_level: str,
    execution: dict | None,
) -> CapabilityRow:
    Capability.model_validate(
        Capability(id=uuid_module.uuid4(), workspace_id=workspace_id, name=name,
                   display_name=display_name, description=description)
    )
    operation = session.get(OperationRow, operation_id)
    if operation is None or operation.workspace_id != workspace_id:
        raise ApiError(ErrorCode.OPERATION_NOT_FOUND, "Operation not found in this workspace")

    existing = session.scalars(
        select(CapabilityRow).where(
            CapabilityRow.workspace_id == workspace_id, CapabilityRow.name == name
        )
    ).one_or_none()
    if existing is not None:
        raise ApiError(ErrorCode.CONFLICT, "Capability name already exists in this workspace")

    latest_op_version = session.scalars(
        select(OperationVersionRow)
        .where(OperationVersionRow.operation_id == operation_id)
        .order_by(OperationVersionRow.version.desc())
    ).first()
    if latest_op_version is None:
        raise ApiError(ErrorCode.OPERATION_NOT_FOUND, "Operation has no contract version yet")

    capability = CapabilityRow(
        workspace_id=workspace_id,
        name=name,
        display_name=display_name,
        description=description,
        created_by=principal_id,
        status=CapabilityStatus.DRAFT.value,
    )
    session.add(capability)
    session.flush()

    draft_payload = {
        "operation_version_id": str(latest_op_version.id),
        "input_schema": input_schema,
        "output_schema": output_schema,
        "allow_fields": sorted(allow_fields),
        "execution": execution or {},
    }
    session.add(
        CapabilityVersionRow(
            capability_id=capability.id,
            operation_version_id=latest_op_version.id,
            version="0.1.0",
            risk_level=risk_level,
            input_schema=input_schema,
            output_schema=output_schema,
            allow_fields=sorted(allow_fields),
            execution=execution or {},
            status="draft",
            content_hash=content_hash(draft_payload),
        )
    )
    session.add(
        AuditEventRow(
            workspace_id=workspace_id,
            actor_id=principal_id,
            action="capability.create",
            decision="allowed",
            redacted_metadata={"capability": name, "operation_id": str(operation_id)},
        )
    )
    return capability


def _get_capability(session: Session, workspace_id: UUID, capability_id: UUID) -> CapabilityRow:
    capability = session.get(CapabilityRow, capability_id)
    if capability is None or capability.workspace_id != workspace_id:
        # do not reveal existence across workspaces (SEC-006)
        raise ApiError(ErrorCode.CAPABILITY_NOT_FOUND, "Capability not found")
    return capability


def _transition(
    session: Session,
    *,
    workspace_id: UUID,
    principal_id: UUID,
    capability_id: UUID,
    target: CapabilityStatus,
    action: str,
    note: str,
) -> CapabilityRow:
    capability = _get_capability(session, workspace_id, capability_id)
    # review PR-SEC-01: the creator cannot review/verify/publish their
    # own capability - a different human must sign each step
    if capability.created_by is not None and capability.created_by == principal_id:
        raise ApiError(
            ErrorCode.FORBIDDEN,
            "Capability review steps require a principal other than the creator",
        )
    capability.status = transition_capability(
        CapabilityStatus(capability.status), target
    ).value
    session.add(
        AuditEventRow(
            workspace_id=workspace_id,
            actor_id=principal_id,
            action=action,
            decision="allowed",
            redacted_metadata={"capability": capability.name, "note": note[:256]},
        )
    )
    return capability


def review_capability(
    session: Session, *, workspace_id: UUID, principal_id: UUID,
    capability_id: UUID, evidence: str,
) -> CapabilityRow:
    return _transition(
        session, workspace_id=workspace_id, principal_id=principal_id,
        capability_id=capability_id, target=CapabilityStatus.REVIEWED,
        action="capability.review", note=evidence,
    )


def verify_capability(
    session: Session, *, workspace_id: UUID, principal_id: UUID,
    capability_id: UUID, evidence: str,
) -> CapabilityRow:
    return _transition(
        session, workspace_id=workspace_id, principal_id=principal_id,
        capability_id=capability_id, target=CapabilityStatus.VERIFIED,
        action="capability.verify", note=evidence,
    )


def publish_capability(
    session: Session,
    *,
    workspace_id: UUID,
    principal_id: UUID,
    capability_id: UUID,
    version: str,
) -> CapabilityVersionRow:
    """Publish an immutable capability version.

    Requires the capability to have passed review AND verification
    (acceptance scenario 3: publishing an unreviewed candidate -> 409).
    """
    capability = _get_capability(session, workspace_id, capability_id)
    if (
        capability.created_by is not None
        and capability.created_by == principal_id
    ):
        raise ApiError(
            ErrorCode.FORBIDDEN,
            "Publishing requires a principal other than the capability creator",
        )
    current = CapabilityStatus(capability.status)
    if current != CapabilityStatus.VERIFIED:
        raise ApiError(
            ErrorCode.CAPABILITY_NOT_REVIEWED,
            "Capability must be reviewed and verified before publishing",
            {"current_status": current.value},
        )

    draft = session.scalars(
        select(CapabilityVersionRow)
        .where(CapabilityVersionRow.capability_id == capability_id)
        .order_by(CapabilityVersionRow.created_at.desc())
    ).first()
    if draft is None:
        raise ApiError(ErrorCode.CAPABILITY_NOT_FOUND, "No draft version to publish")

    existing = session.scalars(
        select(CapabilityVersionRow).where(
            CapabilityVersionRow.capability_id == capability_id,
            CapabilityVersionRow.version == version,
        )
    ).one_or_none()
    if existing is not None:
        raise ApiError(
            ErrorCode.IMMUTABLE_VERSION,
            "Version already published; content changes require a new version",
            {"version": version},
        )

    CapabilityVersion.model_validate(
        CapabilityVersion(
            capability_id=capability_id,
            operation_version_id=draft.operation_version_id,
            version=version,
            risk_level=RiskLevel(draft.risk_level),
            input_schema=draft.input_schema,
            output_schema=draft.output_schema,
            allow_fields=draft.allow_fields,
            execution=draft.execution,
            status=CapabilityVersionStatus.PUBLISHED,
            published_at=datetime.now(UTC),
            content_hash=draft.content_hash,
        )
    )

    published = CapabilityVersionRow(
        capability_id=capability_id,
        operation_version_id=draft.operation_version_id,
        version=version,
        risk_level=draft.risk_level,
        input_schema=draft.input_schema,
        output_schema=draft.output_schema,
        allow_fields=draft.allow_fields,
        execution=draft.execution,
        status="published",
        published_at=None,  # set below; entity validation wants a timestamp
        content_hash=draft.content_hash,
    )
    from ppswitch_db.models import utcnow

    published.published_at = utcnow()
    session.add(published)
    session.flush()

    capability.status = CapabilityStatus.PUBLISHED.value
    session.add(
        AuditEventRow(
            workspace_id=workspace_id,
            actor_id=principal_id,
            action="capability.publish",
            decision="allowed",
            redacted_metadata={"capability": capability.name, "version": version},
        )
    )
    return published


def latest_published_version(
    session: Session, capability: CapabilityRow, requested_version: str | None
) -> CapabilityVersionRow | None:
    stmt = select(CapabilityVersionRow).where(
        CapabilityVersionRow.capability_id == capability.id,
        CapabilityVersionRow.status == "published",
    )
    if requested_version:
        stmt = stmt.where(CapabilityVersionRow.version == requested_version)
    else:
        stmt = stmt.order_by(CapabilityVersionRow.created_at.desc())
    return session.scalars(stmt).first()
