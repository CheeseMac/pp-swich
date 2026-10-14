"""PR-QA-06 acceptance matrix (review8 §7).

Runs on SQLite by default and on PostgreSQL when PP_SWITCH_TEST_DB_URL
is set (the CI matrix job). Covers:

- QA-PG-01: upgrade from the pre-wipe revision wipes legacy plaintext
  results while metadata and foreign keys stay intact;
- QA-PG-03: concurrent one-shot approval consumption - exactly one
  concurrent consumer wins;
- QA-PG-03: idempotency unique constraint under concurrent inserts.

These complement the existing suites (QA-PG-02/04/05 are covered by
test_security_matrix5/6 which run under the same matrix).
"""

from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

DB_PACKAGE = Path(__file__).resolve().parents[1]
PRE_WIPE_REVISION = "d4e7b91c6a30"  # review5: last revision before the wipe


def _test_url(tmp_path: Path, name: str) -> str:
    pg_base = os.environ.get("PP_SWITCH_TEST_DB_URL")
    if pg_base:
        from ppswitch_db.testdb import provision_test_database

        return provision_test_database(pg_base, prefix="ppswitch_qa")
    return f"sqlite:///{tmp_path / name}"


def _alembic_config(url: str, revision: str):
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(DB_PACKAGE / "alembic.ini"))
    cfg.set_main_option("script_location", str(DB_PACKAGE / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)

    def run(target: str) -> None:
        command.upgrade(cfg, target)

    return run


class TestQAPG01LegacyWipeOnUpgrade:
    def test_upgrade_from_pre_wipe_wipes_plaintext_keeps_metadata(self, tmp_path):
        url = _test_url(tmp_path, "qa-pg-01.db")
        upgrade = _alembic_config(url, "head")

        upgrade(PRE_WIPE_REVISION)
        engine = create_engine(url)
        with engine.begin() as conn:
            # full parent chain via raw SQL: FKs are ENFORCED on PG
            # (SQLite tolerates dangling references) - QA-PG-01 checks
            # exactly this integrity across the wipe upgrade
            ws = "11111111-1111-1111-1111-111111111111"
            svc = "12121212-1212-1212-1212-121212121212"
            op = "13131313-1313-1313-1313-131313131313"
            opv = "44444444-4444-4444-4444-444444444444"
            cap = "22222222-2222-2222-2222-222222222222"
            capv = "33333333-3333-3333-3333-333333333333"
            conn.execute(
                text(
                    "INSERT INTO workspaces (id, slug, name, kind, created_at) "
                    "VALUES (:id, 'qa-pg-01', 'QA', 'personal', CURRENT_TIMESTAMP)"
                ),
                {"id": ws},
            )
            conn.execute(
                text(
                    "INSERT INTO services (id, workspace_id, slug, name, "
                    "visibility, owner_principal_id, created_at) "
                    "VALUES (:id, :w, 'qa-svc', 'QA', 'private', "
                    ":owner, CURRENT_TIMESTAMP)"
                ),
                {"id": svc, "w": ws, "owner": "16161616-1616-1616-1616-161616161616"},
            )
            conn.execute(
                text(
                    "INSERT INTO operations (id, workspace_id, service_id, "
                    "method, normalized_path, fingerprint, status, created_at) "
                    "VALUES (:id, :w, :s, 'GET', '/qa', 'qa-fp', 'observed', "
                    "CURRENT_TIMESTAMP)"
                ),
                {"id": op, "w": ws, "s": svc},
            )
            conn.execute(
                text(
                    "INSERT INTO operation_versions (id, operation_id, version, "
                    "input_schema, output_schema, contract_hash, created_at) "
                    "VALUES (:id, :op, 1, '{}', '{}', 'h', CURRENT_TIMESTAMP)"
                ),
                {"id": opv, "op": op},
            )
            conn.execute(
                text(
                    "INSERT INTO capabilities (id, workspace_id, name, "
                    "display_name, description, visibility, status, created_at) "
                    "VALUES (:id, :w, 'qa.cap', 'QA', '', 'private', 'draft', "
                    "CURRENT_TIMESTAMP)"
                ),
                {"id": cap, "w": ws},
            )
            conn.execute(
                text(
                    "INSERT INTO capability_versions (id, capability_id, "
                    "operation_version_id, version, risk_level, input_schema, "
                    "output_schema, allow_fields, execution, required_scopes, "
                    "policy_ref, status, content_hash, created_at) VALUES "
                    "(:id, :c, :o, '0.1.0', 'low', '{}', '{}', '[]', '{}', '[]', "
                    "'policy-v1', 'draft', 'h', CURRENT_TIMESTAMP)"
                ),
                {"id": capv, "c": cap, "o": opv},
            )
            conn.execute(
                text(
                    "INSERT INTO invocations (id, workspace_id, "
                    "capability_version_id, capability_name, "
                    "capability_version, principal_id, profile_id, "
                    "profile_snapshot, arguments_digest, status, result, "
                    "policy_version, created_at) VALUES "
                    "(:i, :w, :v, 'qa.cap', '0.1.0', :p, :p, '{}', 'd', "
                    "'succeeded', '{\"legacy\": \"plaintext\"}', 'p', "
                    "CURRENT_TIMESTAMP)"
                ),
                {"i": "55555555-5555-5555-5555-555555555555",
                 "w": ws, "v": capv,
                 "p": "66666666-6666-6666-6666-666666666666"},
            )

        upgrade("head")  # runs f3a62c8e1b47 (the wipe)

        with engine.begin() as conn:
            row = conn.execute(
                text("SELECT result, status, capability_name FROM invocations")
            ).first()
            assert row is not None            # metadata survives
            assert row.result is None        # legacy plaintext wiped (QA-PG-01)
            assert row.status == "succeeded"
            # foreign keys stay intact
            caps = conn.execute(text("SELECT count(*) FROM capabilities")).scalar()
            assert caps == 1
        engine.dispose()


class TestQAPG03Concurrency:
    def _bootstrap(self, url):
        from ppswitch_db.migrate import run_upgrade
        from ppswitch_db.models import (
            ApprovalRow,
            InvocationRow,
            utcnow,
        )
        from ppswitch_db.session import create_engine_for_url, create_session_factory

        run_upgrade(url)
        engine = create_engine_for_url(url)
        factory = create_session_factory(engine)
        workspace_id = uuid.uuid4()
        invocation_id = uuid.uuid4()
        approval_id = uuid.uuid4()
        with factory() as session:
            from ppswitch_db.models import (
                CapabilityRow,
                CapabilityVersionRow,
                OperationRow,
                OperationVersionRow,
                ServiceRow,
                WorkspaceRow,
            )

            session.add(WorkspaceRow(id=workspace_id, slug=f"qa-{uuid.uuid4().hex[:8]}",
                                     name="QA"))
            session.flush()
            service = ServiceRow(
                workspace_id=workspace_id, slug="qa-svc", name="QA",
                owner_principal_id=uuid.uuid4(),
            )
            session.add(service)
            session.flush()
            operation = OperationRow(
                workspace_id=workspace_id, service_id=service.id,
                method="GET", normalized_path="/qa", fingerprint="qa",
            )
            session.add(operation)
            session.flush()
            operation_version = OperationVersionRow(
                operation_id=operation.id, version=1,
                input_schema={}, output_schema={}, contract_hash="h",
            )
            session.add(operation_version)
            session.flush()
            capability = CapabilityRow(
                workspace_id=workspace_id, name="qa.conc", display_name="QA",
                status="published",
            )
            session.add(capability)
            session.flush()
            version = CapabilityVersionRow(
                capability_id=capability.id,
                operation_version_id=operation_version.id,
                version="1.0.0", content_hash="h",
            )
            session.add(version)
            session.flush()
            session.add(InvocationRow(
                workspace_id=workspace_id,
                capability_version_id=version.id,
                capability_name="qa.conc", capability_version="1.0.0",
                principal_id=uuid.uuid4(), profile_id=uuid.uuid4(),
                profile_snapshot={}, arguments_digest="d",
                status="approval_required",
            ))
            session.flush()
            session.add(ApprovalRow(
                id=approval_id, workspace_id=workspace_id,
                invocation_id=invocation_id, approver_id=uuid.uuid4(),
                arguments_digest="d", status="approved", created_at=utcnow(),
            ))
            session.commit()
        return factory, approval_id, workspace_id, version.id

    def test_approval_consumed_exactly_once_under_concurrency(self, tmp_path):
        url = _test_url(tmp_path, "qa-pg-03.db")
        factory, approval_id, _, _ = self._bootstrap(url)

        def consume() -> int:
            with factory() as session:
                result = session.execute(
                    text(
                        "UPDATE approvals SET status = 'consumed' "
                        "WHERE id = :id AND status = 'approved'"
                    ),
                    {"id": approval_id.hex},
                )
                session.commit()
                return result.rowcount or 0

        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(lambda _: consume(), range(8)))
        assert sum(outcomes) == 1  # exactly ONE winner, seven losers

    def test_idempotency_key_unique_under_concurrency(self, tmp_path):
        url = _test_url(tmp_path, "qa-pg-03b.db")
        from ppswitch_db.models import InvocationRow

        factory, _, workspace_id, version_id = self._bootstrap(url)
        key = f"conc-{uuid.uuid4().hex[:8]}"

        def insert(nth: int) -> bool:
            with factory() as session:
                session.add(InvocationRow(
                    workspace_id=workspace_id,
                    capability_version_id=version_id,
                    capability_name="qa.idem", capability_version="1.0.0",
                    principal_id=uuid.uuid4(), profile_id=uuid.uuid4(),
                    profile_snapshot={}, arguments_digest=f"d{nth}",
                    idempotency_key=key, status="succeeded",
                ))
                try:
                    session.commit()
                    return True
                except IntegrityError:
                    session.rollback()
                    return False

        with ThreadPoolExecutor(max_workers=6) as pool:
            outcomes = list(pool.map(insert, range(6)))
        assert sum(outcomes) == 1  # unique constraint admits exactly one
