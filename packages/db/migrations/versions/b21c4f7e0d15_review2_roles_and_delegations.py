"""review2: workspace roles and user->agent delegations

Revision ID: b21c4f7e0d15
Revises: efec0ab1653a
Create Date: 2026-10-13 19:40:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b21c4f7e0d15'
down_revision: Union[str, None] = 'efec0ab1653a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('principals', schema=None) as batch_op:
        batch_op.add_column(sa.Column('role', sa.String(16), nullable=True))

    op.create_table(
        'delegations',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('workspace_id', sa.Uuid(), nullable=False),
        sa.Column('user_principal_id', sa.Uuid(), nullable=False),
        sa.Column('agent_principal_id', sa.Uuid(), nullable=False),
        sa.Column('scopes', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_delegations')),
        sa.CheckConstraint("status IN ('active','revoked')", name='ck_delegations_status'),
        sa.UniqueConstraint(
            'workspace_id', 'user_principal_id', 'agent_principal_id',
            name=op.f('uq_delegations_workspace_id'),
        ),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_delegations_workspace_id_workspaces')),
    )
    with op.batch_alter_table('delegations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_delegations_workspace_id'), ['workspace_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_delegations_user_principal_id'), ['user_principal_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_delegations_agent_principal_id'), ['agent_principal_id'], unique=False)


def downgrade() -> None:
    op.drop_table('delegations')
    with op.batch_alter_table('principals', schema=None) as batch_op:
        batch_op.drop_column('role')
