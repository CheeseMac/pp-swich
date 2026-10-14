"""FEAT-01 browser collector service: pairing, token exchange, intake.

Threat model and design (docs/32-FEAT-01-COLLECTOR.md):

- the Chrome extension NEVER holds a user/management token. The console
  shows a one-time pairing code bound to one service; the extension
  exchanges it for a short-lived collector token whose only power is
  "submit sanitized observations for that service";
- codes and tokens are stored as SHA-256 hashes, never plaintext;
- the pairing code is single-use with a short TTL. Presenting a code that
  is no longer exchangable counts as an attempt; at the cap the pairing
  (and any token it issued) is revoked - replay/brute-force guard. Code
  entropy is 40 bits over a 10-minute window, so blind guessing cannot
  reach a specific pairing;
- submitted observations are parsed by ``build_candidates_from_collector``
  which re-sanitizes everything (fail-closed) and produces DRAFT
  operations only - nothing here publishes, verifies or executes.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from ppswitch_core.errors import ApiError, ErrorCode
from ppswitch_db.models import AuditEventRow, CollectorPairingRow, ServiceRow
from ppswitch_discovery.parsers.collector import build_candidates_from_collector
from sqlalchemy import select
from sqlalchemy.orm import Session

from ppswitch_control.services.discovery_service import ImportSummary
from ppswitch_control.services.discovery_service import (
    import_discovery_document as _import_discovery_document,
)

# unambiguous alphabet (no 0/O/1/I) - 8 chars ~= 40 bits
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_LENGTH = 8

MAX_PAIRING_ATTEMPTS = 5


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _generate_pairing_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))


def _generate_collector_token() -> str:
    return "ppc-" + secrets.token_urlsafe(24)


@dataclass
class PairingView:
    pairing_id: UUID
    service_id: UUID
    status: str
    created_at: datetime
    pairing_expires_at: datetime
    token_expires_at: datetime | None
    last_submitted_at: datetime | None
    observation_count: int
    attempts: int


def _view(row: CollectorPairingRow) -> dict:
    return {
        "id": str(row.id),
        "service_id": str(row.service_id),
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "pairing_expires_at": row.pairing_expires_at.isoformat(),
        "token_expires_at": (
            row.token_expires_at.isoformat() if row.token_expires_at else None
        ),
        "last_submitted_at": (
            row.last_submitted_at.isoformat() if row.last_submitted_at else None
        ),
        "observation_count": row.observation_count,
        "attempts": row.attempts,
    }


def _get_service(session: Session, workspace_id: UUID, service_id: UUID) -> ServiceRow:
    service = session.get(ServiceRow, service_id)
    if service is None or service.workspace_id != workspace_id:
        raise ApiError(ErrorCode.SERVICE_NOT_FOUND, "Service not found in this workspace")
    return service


def create_pairing(
    session: Session,
    *,
    workspace_id: UUID,
    principal_id: UUID,
    service_id: UUID,
    now: datetime,
    pairing_ttl_seconds: int,
    token_ttl_seconds: int,
) -> tuple[dict, str]:
    """Create a pending pairing; returns (view, one-time code).

    The code is returned exactly once and never persisted in plaintext.
    """
    _get_service(session, workspace_id, service_id)
    code = _generate_pairing_code()
    row = CollectorPairingRow(
        workspace_id=workspace_id,
        service_id=service_id,
        created_by=principal_id,
        pairing_code_hash=_hash(code),
        status="pending",
        attempts=0,
        pairing_expires_at=now + timedelta(seconds=pairing_ttl_seconds),
        observation_count=0,
    )
    session.add(row)
    session.flush()
    session.add(
        AuditEventRow(
            workspace_id=workspace_id,
            actor_id=principal_id,
            action="collector.pairing.create",
            decision="allowed",
            redacted_metadata={"service_id": str(service_id), "pairing_id": str(row.id)},
        )
    )
    view = _view(row)
    view["pairing_code"] = code  # one-time secret, shown in the console once
    return view, code


def list_pairings(
    session: Session, *, workspace_id: UUID, service_id: UUID
) -> list[dict]:
    _get_service(session, workspace_id, service_id)
    rows = session.scalars(
        select(CollectorPairingRow)
        .where(
            CollectorPairingRow.workspace_id == workspace_id,
            CollectorPairingRow.service_id == service_id,
        )
        .order_by(CollectorPairingRow.created_at.desc())
    ).all()
    return [_view(row) for row in rows]


def exchange_pairing_code(
    session: Session,
    *,
    pairing_code: str,
    now: datetime,
    token_ttl_seconds: int,
) -> dict:
    """Exchange a one-time pairing code for a scoped collector token."""
    code = (pairing_code or "").strip().upper()
    row = (
        session.query(CollectorPairingRow)
        .filter_by(pairing_code_hash=_hash(code))
        .one_or_none()
    )
    if row is None:
        # unknown code: no oracle about which pairings exist
        raise ApiError(ErrorCode.UNAUTHENTICATED, "Unknown or expired pairing code")

    if row.status == "pending":
        from ppswitch_control.services.invocation_service import _epoch

        if _epoch(row.pairing_expires_at) < now.timestamp():
            row.status = "expired"
            session.commit()  # persist lifecycle even though we 401
            raise ApiError(ErrorCode.UNAUTHENTICATED, "Unknown or expired pairing code")
        token = _generate_collector_token()
        row.status = "paired"
        row.collector_token_hash = _hash(token)
        row.collector_token_hint = token[:10] + "..."
        row.token_expires_at = now + timedelta(seconds=token_ttl_seconds)
        session.flush()
        session.add(
            AuditEventRow(
                workspace_id=row.workspace_id,
                actor_id=row.created_by,
                action="collector.pairing.exchange",
                decision="allowed",
                redacted_metadata={
                    "service_id": str(row.service_id),
                    "pairing_id": str(row.id),
                },
            )
        )
        return {
            "collector_token": token,  # one-time secret, returned exactly once
            "service_id": str(row.service_id),
            "token_expires_at": row.token_expires_at.isoformat(),
        }

    # replay / guess on a non-pending pairing: count it, revoke at the cap
    row.attempts += 1
    revoked = False
    if row.attempts >= MAX_PAIRING_ATTEMPTS and row.status != "revoked":
        row.status = "revoked"
        # a revoked pairing must also kill its issued token
        row.collector_token_hash = None
        row.token_expires_at = None
        revoked = True
    session.add(
        AuditEventRow(
            workspace_id=row.workspace_id,
            actor_id=row.created_by,
            action="collector.pairing.replay",
            decision="denied",
            redacted_metadata={
                "service_id": str(row.service_id),
                "pairing_id": str(row.id),
                "attempts": row.attempts,
                "revoked": revoked,
            },
        )
    )
    # persist the counter/revoke and the deny audit even though we 401
    session.commit()
    raise ApiError(ErrorCode.UNAUTHENTICATED, "Pairing code already used")


def verify_collector_token(session: Session, token: str, *, now: datetime) -> CollectorPairingRow:
    if not token:
        raise ApiError(ErrorCode.UNAUTHENTICATED, "Missing collector token")
    row = (
        session.query(CollectorPairingRow)
        .filter_by(collector_token_hash=_hash(token))
        .one_or_none()
    )
    if row is None or row.status != "paired":
        raise ApiError(ErrorCode.UNAUTHENTICATED, "Unknown or revoked collector token")
    from ppswitch_control.services.invocation_service import _epoch

    if row.token_expires_at is None or _epoch(row.token_expires_at) < now.timestamp():
        raise ApiError(ErrorCode.UNAUTHENTICATED, "Collector token expired")
    return row


def submit_observations(
    session: Session,
    *,
    pairing: CollectorPairingRow,
    document: dict,
    max_observations: int,
    now: datetime,
) -> ImportSummary:
    """Import one observation batch as DRAFT candidates (nothing published)."""
    observations = document.get("observations")
    if not isinstance(observations, list) or not observations:
        raise ApiError(ErrorCode.BAD_REQUEST, "observations[] is required")
    if len(observations) > max_observations:
        raise ApiError(
            ErrorCode.PAYLOAD_TOO_LARGE,
            f"Batch exceeds {max_observations} observations; submit smaller batches",
        )

    result = build_candidates_from_collector(document)
    if result.unsanitized_fields:
        # fail closed: one raw credential value rejects the whole batch
        raise ApiError(
            ErrorCode.UNSANITIZED_INPUT,
            "Observation batch contains unsanitized credential fields",
            {"fields": sorted(set(result.unsanitized_fields))[:20]},
        )

    summary = _import_discovery_document(
        session,
        workspace_id=pairing.workspace_id,
        principal_id=pairing.created_by,
        service_id=pairing.service_id,
        source_type="collector",
        document=document,
    )
    pairing.last_submitted_at = now
    pairing.observation_count += len(observations)
    session.add(
        AuditEventRow(
            workspace_id=pairing.workspace_id,
            actor_id=pairing.created_by,
            action="collector.submit",
            decision="allowed",
            redacted_metadata={
                "service_id": str(pairing.service_id),
                "pairing_id": str(pairing.id),
                "observations": len(observations),
                "candidates": summary.candidates,
            },
        )
    )
    return summary
