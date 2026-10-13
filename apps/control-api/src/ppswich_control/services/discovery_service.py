"""Discovery import service: sanitized HAR / OpenAPI -> candidate operations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from ppswich_core.errors import ApiError, ErrorCode
from ppswich_db.models import (
    AuditEventRow,
    ObservationRow,
    OperationRow,
    OperationVersionRow,
    ServiceRow,
)
from ppswich_discovery.parsers.har import build_candidates_from_har
from ppswich_discovery.parsers.openapi import build_candidates_from_openapi
from sqlalchemy import select
from sqlalchemy.orm import Session


@dataclass
class ImportSummary:
    import_id: str
    candidates: int
    operations_created: int
    operations_updated: int
    observations_created: int
    unsanitized_fields: list[str]


def _contract_hash(candidate) -> str:
    material = json.dumps(
        {"input": candidate.input_schema, "output": candidate.output_schema},
        sort_keys=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:64]


def import_discovery_document(
    session: Session,
    *,
    workspace_id: UUID,
    principal_id: UUID,
    service_id: UUID,
    source_type: str,
    document: dict,
) -> ImportSummary:
    service = session.get(ServiceRow, service_id)
    if service is None or service.workspace_id != workspace_id:
        raise ApiError(ErrorCode.SERVICE_NOT_FOUND, "Service not found in this workspace")

    if source_type == "har":
        result = build_candidates_from_har(document)
    elif source_type == "openapi":
        result = build_candidates_from_openapi(document)
    else:
        raise ApiError(ErrorCode.BAD_REQUEST, "Unsupported source_type")

    if result.unsanitized_fields:
        # fail closed: raw credentials in the upload reject the whole import
        raise ApiError(
            ErrorCode.UNSANITIZED_INPUT,
            "Upload still contains unsanitized credential fields; sanitize locally first",
            {"fields": sorted(set(result.unsanitized_fields))[:20]},
        )

    created = updated = observations = 0
    for candidate in result.candidates:
        operation = session.scalars(
            select(OperationRow).where(
                OperationRow.service_id == service_id,
                OperationRow.fingerprint == candidate.fingerprint,
            )
        ).one_or_none()

        contract_hash = _contract_hash(candidate)
        if operation is None:
            operation = OperationRow(
                workspace_id=workspace_id,
                service_id=service_id,
                method=candidate.method,
                normalized_path=candidate.template,
                fingerprint=candidate.fingerprint,
                status="inferred" if candidate.needs_review else "observed",
            )
            session.add(operation)
            session.flush()
            session.add(
                OperationVersionRow(
                    operation_id=operation.id,
                    version=1,
                    input_schema=candidate.input_schema,
                    output_schema=candidate.output_schema,
                    contract_hash=contract_hash,
                )
            )
            created += 1
        else:
            latest = session.scalars(
                select(OperationVersionRow)
                .where(OperationVersionRow.operation_id == operation.id)
                .order_by(OperationVersionRow.version.desc())
            ).first()
            if latest is None or latest.contract_hash != contract_hash:
                next_version = (latest.version if latest else 0) + 1
                session.add(
                    OperationVersionRow(
                        operation_id=operation.id,
                        version=next_version,
                        input_schema=candidate.input_schema,
                        output_schema=candidate.output_schema,
                        contract_hash=contract_hash,
                    )
                )
                operation.status = "needs_review"
                updated += 1

        session.add(
            ObservationRow(
                workspace_id=workspace_id,
                service_id=service_id,
                source=source_type,
                normalized_shape={
                    "method": candidate.method,
                    "template": candidate.template,
                    "query_keys": candidate.query_keys,
                    "sample_count": candidate.sample_count,
                    "status_codes": candidate.status_codes,
                },
                redaction_version="har-sanitizer/1",
            )
        )
        observations += 1

    session.add(
        AuditEventRow(
            workspace_id=workspace_id,
            actor_id=principal_id,
            action="discovery.import",
            decision="allowed",
            redacted_metadata={
                "source_type": source_type,
                "service_id": str(service_id),
                "entries": result.entry_count,
                "candidates": len(result.candidates),
            },
        )
    )
    import_id = hashlib.sha256(
        f"{workspace_id}{service_id}{result.entry_count}{created}{updated}".encode()
    ).hexdigest()[:16]
    return ImportSummary(
        import_id=import_id,
        candidates=len(result.candidates),
        operations_created=created,
        operations_updated=updated,
        observations_created=observations,
        unsanitized_fields=[],
    )
