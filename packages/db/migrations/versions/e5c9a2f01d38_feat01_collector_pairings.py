"""FEAT-01: browser collector pairings

Revision ID: e5c9a2f01d38
Revises: f3a62c8e1b47
Create Date: 2026-10-14 12:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5c9a2f01d38'
down_revision: Union[str, None] = 'f3a62c8e1b47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'collector_pairings',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('workspace_id', sa.Uuid(), nullable=False),
        sa.Column('service_id', sa.Uuid(), nullable=False),
        sa.Column('created_by', sa.Uuid(), nullable=False),
        sa.Column('pairing_code_hash', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('collector_token_hash', sa.String(length=64), nullable=True),
        sa.Column('collector_token_hint', sa.String(length=32), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column('pairing_expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('token_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_submitted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('observation_count', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_collector_pairings')),
        sa.CheckConstraint(
            "status IN ('pending','paired','revoked','expired')",
            name='ck_collector_pairings_status',
        ),
        sa.ForeignKeyConstraint(
            ['workspace_id'], ['workspaces.id'],
            name=op.f('fk_collector_pairings_workspace_id_workspaces'),
        ),
        sa.ForeignKeyConstraint(
            ['service_id', 'workspace_id'],
            ['services.id', 'services.workspace_id'],
            name=op.f('fk_collector_pairings_service_id_services'),
        ),
    )
    with op.batch_alter_table('collector_pairings', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_collector_pairings_workspace_id'),
            ['workspace_id'], unique=False,
        )
        batch_op.create_index(
            batch_op.f('ix_collector_pairings_service_id'),
            ['service_id'], unique=False,
        )
        batch_op.create_index(
            batch_op.f('ix_collector_pairings_pairing_code_hash'),
            ['pairing_code_hash'], unique=False,
        )
        batch_op.create_index(
            batch_op.f('ix_collector_pairings_collector_token_hash'),
            ['collector_token_hash'], unique=True,
        )


def downgrade() -> None:
    with op.batch_alter_table('collector_pairings', schema=None) as batch_op:
        batch_op.drop_index(
            batch_op.f('ix_collector_pairings_collector_token_hash'),
        )
        batch_op.drop_index(batch_op.f('ix_collector_pairings_pairing_code_hash'))
        batch_op.drop_index(batch_op.f('ix_collector_pairings_service_id'))
        batch_op.drop_index(batch_op.f('ix_collector_pairings_workspace_id'))
    op.drop_table('collector_pairings')
