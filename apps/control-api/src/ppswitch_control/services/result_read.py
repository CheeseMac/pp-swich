"""Result read-out (PR-DATA-12): retention + decryption in one place.

Every read path (history detail, idempotent replay) resolves stored
results through :func:`stored_result` - a result past its retention
deadline reads as ``None`` and an encrypted payload decrypts only here.
Read-time authorization (SEC-052) happens BEFORE this layer.
"""

from __future__ import annotations

import time
from typing import Any

from ppswitch_db.models import InvocationRow, utcnow


def _expired(row: InvocationRow) -> bool:
    if row.result_expires_at is None:
        return False
    from ppswitch_control.services.invocation_service import _epoch

    return _epoch(row.result_expires_at) < time.time()


def stored_result(row: InvocationRow, settings) -> dict[str, Any] | None:
    """The readable result for ``row`` under the current settings."""
    if _expired(row):
        return None  # past retention: indistinguishable from never stored
    if row.result_encrypted:
        from ppswitch_control.result_vault import ResultVault

        return ResultVault(settings.result_encryption_key).decrypt(row.result_encrypted)
    return row.result


def purge_expired_results(session) -> int:
    """Wipe expired result payloads in place; returns affected rows.

    Metadata (status, capability, timestamps) survives - only the data
    payload is destroyed, matching the retention promise.
    """
    from sqlalchemy import text

    result = session.execute(
        text(
            "UPDATE invocations SET result = NULL, result_encrypted = NULL "
            "WHERE result_expires_at IS NOT NULL AND result_expires_at < :now"
        ),
        {"now": utcnow()},
    )
    session.commit()
    return getattr(result, "rowcount", 0) or 0
