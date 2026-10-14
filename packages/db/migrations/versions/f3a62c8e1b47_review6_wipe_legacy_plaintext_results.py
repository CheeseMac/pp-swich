"""review6 DATA-15: wipe legacy plaintext results on upgrade

Revision ID: f3a62c8e1b47
Revises: d4e7b91c6a30
Create Date: 2026-10-14 22:30:00.000000

Security-first upgrade policy (review6): rows written before the
PR-DATA-12 lifecycle existed keep their plaintext result forever, never
expire and are never purged. The migration clears those payloads,
keeping execution metadata only.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = 'f3a62c8e1b47'
down_revision: Union[str, None] = 'd4e7b91c6a30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE invocations SET result = NULL WHERE result IS NOT NULL")


def downgrade() -> None:
    # intentionally irreversible: cleared legacy plaintext cannot be restored
    pass
