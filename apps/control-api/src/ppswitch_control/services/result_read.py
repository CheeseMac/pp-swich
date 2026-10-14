"""Result read-out (PR-DATA-12): retention + decryption in one place.

Every read path (history detail, idempotent replay) resolves stored
results through :func:`stored_result` - a result past its retention
deadline reads as ``None`` and an encrypted payload decrypts only here.
Read-time authorization (SEC-052) happens BEFORE this layer.

review6 additions:

- DATA-15: purging also wipes any plaintext result WITHOUT a retention
  deadline - such rows can only be legacy pre-lifecycle data (every new
  write sets ``result_expires_at``), so the runtime purge is a safety
  net on top of migration ``f3a62c8e1b47``;
- DATA-16: purge accepts a ``workspace_id`` scope - workspace admins
  only ever touch their own workspace; the unscoped form is reserved
  for the internal startup job.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ppswitch_db.models import InvocationRow, utcnow

logger = logging.getLogger("ppswitch.control.results")


def _expired(row: InvocationRow) -> bool:
    if row.result_expires_at is None:
        return False
    from ppswitch_control.services.invocation_service import _epoch

    return _epoch(row.result_expires_at) < time.time()


def stored_result(row: InvocationRow, settings) -> dict[str, Any] | None:
    """The readable result for ``row`` under the current settings.

    Failure modes are distinguishable in logs (DATA-17): past retention,
    never stored, and decrypt failure (wrong/rotated key or corrupted
    ciphertext) each record their own event; the API response is ``None``
    in all three - callers learn nothing about the reason.
    """
    if _expired(row):
        return None  # past retention: indistinguishable from never stored
    if row.result_encrypted:
        from ppswitch_control.result_vault import ResultVault

        result = ResultVault(
            settings.result_encryption_key,
            previous_keys=[
                k for k in settings.result_previous_keys.split(",") if k
            ],
        ).decrypt(row.result_encrypted)
        if result is None:
            logger.warning(
                "result_decrypt_failed invocation_id=%s classification=decrypt_error",
                row.id,
            )
        return result
    if row.result is not None and row.result_expires_at is None:
        logger.warning(
            "legacy_plaintext_result_read invocation_id=%s classification=legacy",
            row.id,
        )
    return row.result


def purge_expired_results(session, workspace_id=None) -> int:
    """Wipe expired (and legacy deadline-less) result payloads.

    Metadata (status, capability, timestamps) survives - only the data
    payload is destroyed, matching the retention promise. With
    ``workspace_id`` the statement is tenant-scoped (admin API);
    ``None`` means the internal maintenance job (startup).
    """
    from sqlalchemy import and_, or_, update

    statement = update(InvocationRow).where(
        or_(
            InvocationRow.result.is_not(None),
            InvocationRow.result_encrypted.is_not(None),
        ),
        or_(
            and_(
                InvocationRow.result_expires_at.is_not(None),
                InvocationRow.result_expires_at < utcnow(),
            ),
            InvocationRow.result_expires_at.is_(None),  # DATA-15: legacy
        ),
    )
    if workspace_id is not None:
        statement = statement.where(InvocationRow.workspace_id == workspace_id)
    statement = statement.values(result=None, result_encrypted=None)
    result = session.execute(statement)
    session.commit()
    return getattr(result, "rowcount", 0) or 0


def reencrypt_results(session, settings, workspace_id=None) -> dict[str, int]:
    """DATA-17 key rotation: re-encrypt stored payloads under the current
    primary key. Rows that fail to decrypt under ANY configured key are
    left untouched, counted as ``failed`` and logged."""
    from sqlalchemy import select

    from ppswitch_control.result_vault import ResultVault

    vault = ResultVault(
        settings.result_encryption_key,
        previous_keys=[k for k in settings.result_previous_keys.split(",") if k],
    )
    statement = select(InvocationRow).where(
        InvocationRow.result_encrypted.is_not(None)
    )
    if workspace_id is not None:
        statement = statement.where(InvocationRow.workspace_id == workspace_id)
    rekeyed = failed = 0
    for row in session.scalars(statement).all():
        payload = vault.decrypt(row.result_encrypted or "")
        if payload is None:
            failed += 1
            logger.warning(
                "result_reencrypt_failed invocation_id=%s classification=decrypt_error",
                row.id,
            )
            continue
        row.result_encrypted = vault.encrypt(payload)
        rekeyed += 1
    session.commit()
    return {"rekeyed": rekeyed, "failed": failed}
