"""review3: capability required_scopes

Revision ID: c8d31a90f4e2
Revises: b21c4f7e0d15
Create Date: 2026-10-14 10:05:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c8d31a90f4e2'
down_revision: Union[str, None] = 'b21c4f7e0d15'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('capability_versions', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('required_scopes', sa.JSON(), nullable=False,
                      server_default='[]')
        )


def downgrade() -> None:
    with op.batch_alter_table('capability_versions', schema=None) as batch_op:
        batch_op.drop_column('required_scopes')
