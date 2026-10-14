"""Database layer tests: empty migration, double migration, optimistic
profile switching, and composite-FK workspace integrity."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from ppswitch_db.models import (
    ActiveBindingRow,
    ConnectionRow,
    EndpointRow,
    ProfileRow,
    ServiceRow,
    WorkspaceRow,
)
from ppswitch_db.session import create_engine_for_url, create_session_factory
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

DB_PACKAGE = Path(__file__).resolve().parents[1]


def _tmp_sqlite_url(tmp_path: Path, name: str) -> str:
    return f"sqlite:///{tmp_path / name}"


def _run_migrations(url: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(DB_PACKAGE / "alembic.ini"))
    cfg.set_main_option("script_location", str(DB_PACKAGE / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")


def test_empty_database_migration_and_rerun(tmp_path: Path) -> None:
    url = _tmp_sqlite_url(tmp_path, "empty.db")
    _run_migrations(url)
    # running again must be a no-op (acceptance: repeated migrations pass)
    _run_migrations(url)
    engine = create_engine(url)
    with engine.connect() as conn:
        tables = {
            r[0]
            for r in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
        }
    engine.dispose()
    for expected in (
        "workspaces", "services", "endpoints", "observations", "operations",
        "operation_versions", "capabilities", "capability_versions",
        "connections", "profiles", "active_bindings", "invocations",
        "audit_events", "principals", "agent_grants", "approvals",
        "delegations", "collector_pairings",
    ):
        assert expected in tables


def _seed_profile_fixture(factory) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """workspace + service + endpoint + connection + two profiles."""
    with factory() as session:
        ws = WorkspaceRow(id=uuid.uuid4(), slug=f"ws-{uuid.uuid4().hex[:8]}", name="WS")
        session.add(ws)
        session.flush()
        principal = uuid.uuid4()
        svc = ServiceRow(
            id=uuid.uuid4(), workspace_id=ws.id, slug="crm", name="CRM",
            owner_principal_id=principal,
        )
        session.add(svc)
        session.flush()
        ep = EndpointRow(
            id=uuid.uuid4(), workspace_id=ws.id, service_id=svc.id,
            environment="demo", base_url="http://127.0.0.1:8913",
            approved_hosts=["127.0.0.1:8913"], status="approved",
        )
        conns = [
            ConnectionRow(
                id=uuid.uuid4(), workspace_id=ws.id, service_id=svc.id,
                owner_principal_id=principal, auth_scheme="api_key",
                secret_ref=f"mock://conn/{i}", status="active",
            )
            for i in range(2)
        ]
        session.add_all([ep, *conns])
        session.flush()
        profiles = [
            ProfileRow(
                id=uuid.uuid4(), workspace_id=ws.id, service_id=svc.id,
                endpoint_id=ep.id, connection_id=c.id, owner_principal_id=principal,
                name=f"Demo {chr(65 + i)}", status="enabled",
            )
            for i, c in enumerate(conns)
        ]
        session.add_all(profiles)
        session.flush()
        session.commit()
        return ws.id, principal, svc.id, profiles[1].id


def test_optimistic_switch_one_winner(tmp_path: Path) -> None:
    url = _tmp_sqlite_url(tmp_path, "switch.db")
    _run_migrations(url)
    engine = create_engine_for_url(url)
    factory = create_session_factory(engine)
    ws, principal, svc, new_profile_id = _seed_profile_fixture(factory)

    with factory() as session:
        session.add(
            ActiveBindingRow(
                workspace_id=ws, principal_id=principal, service_id=svc,
                context_key="personal", active_profile_id=new_profile_id,
                version=1, updated_by=principal,
            )
        )
        session.commit()

    # two concurrent switches from the same expected_version: exactly one wins
    results = []
    for _ in range(2):
        with factory() as session:
            updated = session.execute(
                text(
                    "UPDATE active_bindings SET active_profile_id = :p, "
                    "version = version + 1, updated_by = :u "
                    "WHERE workspace_id = :w AND principal_id = :pr "
                    "AND service_id = :s AND context_key = 'personal' "
                    "AND version = :v"
                ),
                {"p": new_profile_id.hex, "u": principal.hex, "w": ws.hex,
                 "pr": principal.hex, "s": svc.hex, "v": 1},
            )
            session.commit()
            results.append(updated.rowcount)
    assert sorted(results) == [0, 1]
    engine.dispose()


def test_active_binding_fk_blocks_cross_workspace_profile(tmp_path: Path) -> None:
    """Composite FK: binding a profile from another workspace must fail."""
    url = _tmp_sqlite_url(tmp_path, "fk.db")
    _run_migrations(url)
    engine = create_engine_for_url(url)
    factory = create_session_factory(engine)
    _ws, principal, svc, profile_id = _seed_profile_fixture(factory)

    other_ws = uuid.uuid4()
    with factory() as session:
        session.add(
            ActiveBindingRow(
                workspace_id=other_ws, principal_id=principal, service_id=svc,
                context_key="personal", active_profile_id=profile_id,
                version=1, updated_by=principal,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
    engine.dispose()
