"""FEAT-04: read-only PostgreSQL targets + endpoint-less DB profiles

Revision ID: b7d2e4c9a1f3
Revises: e5c9a2f01d38
Create Date: 2026-10-16 12:00:00.000000

- new ``db_targets`` table: the INDEPENDENT approval type for database
  access (host/port/database/allowed_schemas); HTTP endpoint approvals and
  database approvals never authorize each other's traffic;
- ``connections.auth_scheme`` gains the ``postgres`` variant (FEAT-03
  keyring credentials, material = {"user", "password"});
- ``profiles.endpoint_id`` becomes nullable: a database profile binds a
  postgres connection and draws network authorization from the service's
  approved db_target instead of an HTTP endpoint.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7d2e4c9a1f3'
down_revision: Union[str, None] = 'e5c9a2f01d38'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _connections_table(meta: sa.MetaData, auth_scheme_check: str) -> sa.Table:
    """Full ``connections`` definition (unchanged since the initial schema).

    SQLite cannot drop an unnamed CHECK in place, so there the table is
    rebuilt from an explicit old/new pair; PostgreSQL drops the named
    constraint directly. Incoming foreign keys (profiles) make a rebuild
    on PostgreSQL undesirable, hence the dialect branch in ``upgrade``.
    """
    table = sa.Table(
        'connections', meta,
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('workspace_id', sa.Uuid(), nullable=False),
        sa.Column('service_id', sa.Uuid(), nullable=False),
        sa.Column('owner_principal_id', sa.Uuid(), nullable=False),
        sa.Column('auth_scheme', sa.String(length=16), nullable=False),
        sa.Column('secret_ref', sa.String(length=512), nullable=False),
        sa.Column('scopes', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True),
            server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False,
        ),
        sa.CheckConstraint(
            auth_scheme_check, name='ck_connections_auth_scheme'),
        sa.CheckConstraint(
            "status IN ('pending','active','expired','revoked')",
            name='ck_connections_status'),
        sa.ForeignKeyConstraint(
            ['service_id', 'workspace_id'],
            ['services.id', 'services.workspace_id'],
            name='fk_connections_service_id_services'),
        sa.UniqueConstraint('id', 'service_id', name='uq_connections_id'),
    )
    sa.Index('ix_connections_owner_principal_id', table.c.owner_principal_id)
    sa.Index('ix_connections_service_id', table.c.service_id)
    sa.Index('ix_connections_workspace_id', table.c.workspace_id)
    return table


_OLD_AUTH_CHECK = "auth_scheme IN ('none','api_key','bearer','oauth2','basic')"
_NEW_AUTH_CHECK = (
    "auth_scheme IN ('none','api_key','bearer','oauth2','basic','postgres')"
)


def upgrade() -> None:
    op.create_table(
        'db_targets',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('workspace_id', sa.Uuid(), nullable=False),
        sa.Column('service_id', sa.Uuid(), nullable=False),
        sa.Column('engine', sa.String(length=16), nullable=False),
        sa.Column('host', sa.String(length=253), nullable=False),
        sa.Column('port', sa.Integer(), nullable=False),
        sa.Column('database', sa.String(length=63), nullable=False),
        sa.Column('allowed_schemas', sa.JSON(), nullable=False),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_db_targets')),
        sa.CheckConstraint(
            "status IN ('pending','approved','disabled')",
            name='ck_db_targets_status',
        ),
        sa.CheckConstraint("engine IN ('postgres')", name='ck_db_targets_engine'),
        sa.ForeignKeyConstraint(
            ['workspace_id'], ['workspaces.id'],
            name=op.f('fk_db_targets_workspace_id_workspaces'),
        ),
        sa.ForeignKeyConstraint(
            ['service_id', 'workspace_id'],
            ['services.id', 'services.workspace_id'],
            name=op.f('fk_db_targets_service_id_services'),
        ),
        sa.UniqueConstraint('id', 'service_id', name='uq_db_targets_id_service_id'),
        sa.UniqueConstraint(
            'service_id', 'engine', 'host', 'port', 'database',
            name='uq_db_targets_service_engine_host_port_database',
        ),
    )
    with op.batch_alter_table('db_targets', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_db_targets_workspace_id'), ['workspace_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_db_targets_service_id'), ['service_id'], unique=False
        )
    # connections: allow the postgres auth scheme (keyring-held ro account)
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        # named CHECK constraints are directly replaceable on PostgreSQL;
        # a table rebuild would fight the incoming profiles foreign key
        op.drop_constraint('ck_connections_auth_scheme', 'connections', type_='check')
        op.create_check_constraint(
            'ck_connections_auth_scheme', 'connections', _NEW_AUTH_CHECK)
    else:
        # SQLite cannot drop an unnamed CHECK in place: force a rebuild of
        # the table from an explicit definition that already carries the
        # NEW check. No ops inside the block - the definition drives it,
        # so no constraint-name lookups happen at all.
        rebuild_meta = sa.MetaData()
        copy_from = _connections_table(rebuild_meta, _NEW_AUTH_CHECK)
        with op.batch_alter_table(
            'connections', copy_from=copy_from, recreate='always', schema=None,
        ):
            pass
    # profiles: endpoint_id nullable for database profiles
    with op.batch_alter_table('profiles', schema=None) as batch_op:
        batch_op.alter_column('endpoint_id', existing_type=sa.Uuid(), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table('profiles', schema=None) as batch_op:
        batch_op.alter_column('endpoint_id', existing_type=sa.Uuid(), nullable=False)
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.drop_constraint('ck_connections_auth_scheme', 'connections', type_='check')
        op.create_check_constraint(
            'ck_connections_auth_scheme', 'connections', _OLD_AUTH_CHECK)
    else:
        rebuild_meta = sa.MetaData()
        copy_from = _connections_table(rebuild_meta, _OLD_AUTH_CHECK)
        with op.batch_alter_table(
            'connections', copy_from=copy_from, recreate='always', schema=None,
        ):
            pass
    with op.batch_alter_table('db_targets', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_db_targets_service_id'))
        batch_op.drop_index(batch_op.f('ix_db_targets_workspace_id'))
    op.drop_table('db_targets')
