"""review5: delegation generation + invocation result retention

Revision ID: d4e7b91c6a30
Revises: c8d31a90f4e2
Create Date: 2026-10-14 16:20:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e7b91c6a30'
down_revision: Union[str, None] = 'c8d31a90f4e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('delegations', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('generation', sa.Integer(), nullable=False,
                      server_default='1')
        )
    with op.batch_alter_table('invocations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('result_encrypted', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('result_expires_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index(
            batch_op.f('ix_invocations_result_expires_at'),
            ['result_expires_at'], unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table('invocations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_invocations_result_expires_at'))
        batch_op.drop_column('result_expires_at')
        batch_op.drop_column('result_encrypted')
    with op.batch_alter_table('delegations', schema=None) as batch_op:
        batch_op.drop_column('generation')
