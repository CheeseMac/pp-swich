"""SECRET-06: keyring name claims + pairing proofs + connection pinning

Revision ID: c3f8d1e6a2b4
Revises: b7d2e4c9a1f3
Create Date: 2026-10-17 12:00:00.000000

- ``keyring_claims``: machine-level first-class ownership rows (never
  deleted; release/dispute bump generation - anti-ABA);
- ``keyring_claim_pairings``: one-time console pairings for shared-mode
  first claims (hashed code, brute-force cap, signed binding ticket);
- ``connections``: pin lifecycle columns (claim generation, pin version,
  full material tag, pin state).

Legacy backfill (docs/41 section 3.3): existing keyring connections are
marked un-pinned and CANNOT execute until their owner re-activates
(no NULL-tag fallback). Ownership candidates derived from existing
connections only create ``pending_claim``/``disputed`` rows - never an
automatically trusted active claim; multiple distinct owners of one name
are marked disputed for operator resolution.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3f8d1e6a2b4'
down_revision: Union[str, None] = 'b7d2e4c9a1f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'keyring_claims',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('namespace_id', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('owner_principal_id', sa.Uuid(), nullable=False),
        sa.Column('owner_workspace_id', sa.Uuid(), nullable=False),
        sa.Column('auth_scheme', sa.String(length=16), nullable=False),
        sa.Column('generation', sa.Integer(), nullable=False),
        sa.Column('pin_version', sa.Integer(), nullable=False),
        sa.Column('material_tag', sa.String(length=64), nullable=True),
        sa.Column('status', sa.String(length=24), nullable=False),
        sa.Column('proof_consumed_id', sa.String(length=32), nullable=True),
        sa.Column('claimed_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_keyring_claims')),
        sa.CheckConstraint(
            "status IN ('active','pending_rotation','released','disputed','pending_claim')",
            name='ck_keyring_claims_status',
        ),
        sa.CheckConstraint('generation > 0', name='ck_keyring_claims_generation_positive'),
        sa.CheckConstraint('pin_version > 0', name='ck_keyring_claims_pin_version_positive'),
        sa.CheckConstraint(
            'material_tag IS NULL OR length(material_tag) = 64',
            name='ck_keyring_claims_material_tag_len',
        ),
        sa.UniqueConstraint('namespace_id', 'name',
                            name='uq_keyring_claims_namespace_id_name'),
    )
    with op.batch_alter_table('keyring_claims', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_keyring_claims_namespace_id'), ['namespace_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_keyring_claims_name'), ['name'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_keyring_claims_owner_workspace_id'),
            ['owner_workspace_id'], unique=False,
        )

    op.create_table(
        'keyring_claim_pairings',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('workspace_id', sa.Uuid(), nullable=False),
        sa.Column('principal_id', sa.Uuid(), nullable=False),
        sa.Column('namespace_id', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('code_hash', sa.String(length=64), nullable=False),
        sa.Column('proof_id', sa.String(length=32), nullable=False),
        sa.Column('ticket', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_keyring_claim_pairings')),
        sa.CheckConstraint(
            "status IN ('pending','consumed','expired')",
            name='ck_keyring_claim_pairings_status',
        ),
        sa.ForeignKeyConstraint(
            ['workspace_id'], ['workspaces.id'],
            name=op.f('fk_keyring_claim_pairings_workspace_id_workspaces'),
        ),
    )
    with op.batch_alter_table('keyring_claim_pairings', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_keyring_claim_pairings_workspace_id'),
            ['workspace_id'], unique=False,
        )
        batch_op.create_index(
            batch_op.f('ix_keyring_claim_pairings_principal_id'),
            ['principal_id'], unique=False,
        )
        batch_op.create_index(
            batch_op.f('ix_keyring_claim_pairings_code_hash'),
            ['code_hash'], unique=False,
        )

    with op.batch_alter_table('connections', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('claim_generation', sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column('pin_version', sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column('secret_material_tag', sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column('pin_state', sa.String(length=24), nullable=False,
                      server_default='unpinned'))
        batch_op.create_check_constraint(
            'pin_state',
            "pin_state IN ('unpinned','pending_rotation','pinned')")

    _backfill_legacy_claims()


def _backfill_legacy_claims() -> None:
    """Candidate owners only (docs/41 3.3): single historical owner ->
    pending_claim (activates on the owner's next activation with proof or,
    in personal mode, the preserved first-activation flow); multiple
    owners -> disputed for operator resolution. Never an active claim."""
    import uuid as _uuid

    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT c.secret_ref, c.owner_principal_id, c.workspace_id, c.auth_scheme "
        "FROM connections c WHERE c.secret_ref LIKE 'keyring://%'"
    )).fetchall()
    by_name: dict[str, list] = {}
    for secret_ref, owner, workspace, scheme in rows:
        name = secret_ref[len('keyring://'):]
        by_name.setdefault(name, []).append((owner, workspace, scheme))
    for name, entries in by_name.items():
        owners = {(owner, ws) for owner, ws, _ in entries}
        first_owner, first_ws, scheme = entries[0]
        status = 'pending_claim' if len(owners) == 1 else 'disputed'
        bind.execute(sa.text(
            "INSERT INTO keyring_claims (id, namespace_id, name, owner_principal_id, "
            "owner_workspace_id, auth_scheme, generation, pin_version, material_tag, "
            "status, proof_consumed_id, claimed_at, updated_at) "
            "VALUES (:id, :ns, :name, :owner, :ws, :scheme, 1, 1, NULL, :status, "
            ":proof_consumed_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ), {
            "id": str(_uuid.uuid4()), "ns": "local", "name": name,
            "owner": str(first_owner), "ws": str(first_ws),
            "scheme": scheme or "api_key", "status": status,
            "proof_consumed_id": "legacy-migration-candidate",
        })


def _connections_table(meta: sa.MetaData, *, with_pins: bool) -> sa.Table:
    """Explicit connections definition for SQLite rebuilds.

    SQLite cannot drop the reflected pin_state CHECK in place (and column
    drops replay stale CHECK text), so both directions rebuild from an
    explicit definition. ``with_pins`` selects the SECRET-06 shape or the
    FEAT-04 shape.
    """
    columns = [
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('workspace_id', sa.Uuid(), nullable=False),
        sa.Column('service_id', sa.Uuid(), nullable=False),
        sa.Column('owner_principal_id', sa.Uuid(), nullable=False),
        sa.Column('auth_scheme', sa.String(length=16), nullable=False),
        sa.Column('secret_ref', sa.String(length=512), nullable=False),
        sa.Column('scopes', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
    ]
    if with_pins:
        columns.extend([
            sa.Column('claim_generation', sa.Integer(), nullable=True),
            sa.Column('pin_version', sa.Integer(), nullable=True),
            sa.Column('secret_material_tag', sa.String(length=64), nullable=True),
            sa.Column('pin_state', sa.String(length=24), nullable=False),
        ])
    columns.append(
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False))
    constraints = [
        sa.CheckConstraint(
            "auth_scheme IN ('none','api_key','bearer','oauth2','basic','postgres')",
            name='ck_connections_auth_scheme'),
        sa.CheckConstraint(
            "status IN ('pending','active','expired','revoked')",
            name='ck_connections_status'),
    ]
    if with_pins:
        constraints.append(sa.CheckConstraint(
            "pin_state IN ('unpinned','pending_rotation','pinned')",
            name='ck_connections_pin_state'))
    table = sa.Table(
        'connections', meta, *columns, *constraints,
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


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        # named CHECK replaceable in place; SQLite rebuilds via batch below
        op.execute('ALTER TABLE connections DROP CONSTRAINT ck_connections_pin_state')
        op.execute('ALTER TABLE connections DROP COLUMN claim_generation')
        op.execute('ALTER TABLE connections DROP COLUMN pin_version')
        op.execute('ALTER TABLE connections DROP COLUMN secret_material_tag')
        op.execute('ALTER TABLE connections DROP COLUMN pin_state')
    else:
        # rebuild from the explicit pre-SECRET-06 definition; no ops inside
        # the block - the definition itself drops the pin columns
        with op.batch_alter_table(
            'connections',
            copy_from=_connections_table(sa.MetaData(), with_pins=False),
            recreate='always', schema=None,
        ):
            pass
    with op.batch_alter_table('keyring_claim_pairings', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_keyring_claim_pairings_code_hash'))
        batch_op.drop_index(batch_op.f('ix_keyring_claim_pairings_principal_id'))
        batch_op.drop_index(batch_op.f('ix_keyring_claim_pairings_workspace_id'))
    op.drop_table('keyring_claim_pairings')
    with op.batch_alter_table('keyring_claims', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_keyring_claims_owner_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_keyring_claims_name'))
        batch_op.drop_index(batch_op.f('ix_keyring_claims_namespace_id'))
    op.drop_table('keyring_claims')
